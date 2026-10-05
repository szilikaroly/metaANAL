# -*- coding: utf-8 -*-
"""MVP: a motor szk.* szerződései (metaelemzes/contracts/*.schema.json; terv 4.0–4.20, 8.3).

- minden séma betölthető; fájlnév ↔ $id ↔ "schema"-const egyezik; 2020-12 $schema; kanonikus formázás (stabil
  sha256 — a capabilities-kézfogás sodródás-őre ezt hasonlítja); a mappában nincs más *.json;
- minden $ref feloldható (helyi '#/$defs/…' és fájlok közti 'urn:szk:contract:…#/…'); a minták lefordulnak;
  csak a ma_gui.schema_lite által is ismert kulcsszavak szerepelnek; a marketplace CI slash-lintje nem akad meg rajtuk;
- a tervben JSON-ként megadott sémák (4.0, 4.1, 4.3, 4.4, 4.6, 4.8) szó szerint egyeznek a terv blokkjaival (csak a
  $schema és a title annotáció új; az analysis-spec options generált részét a spec.py-val vetjük össze); a prózából
  vagy példából írt sémák "$comment"-je jelzi, hogy pontosítandók;
- a terv 4. fejezetének példadokumentumai (tests/reference/contract_examples/*.terv.json) a terv szövegével egyeznek
  (a „…” hash-helykitöltők 64 jegyűre egészítve, a „…” időbélyeg rögzítve) és érvényesek;
- szerződésenként legalább 3 pozitív és 3 negatív példa (fájlok + a kötelező mezők elhagyása, NaN, rossz főverzió);
- a motor meglévő termelői (validálás, spec, futás-leíró, plot v2, napló, projektleíró) a sémáknak megfelelő
  dokumentumot adnak, és a kézi ellenőrzők mintái azonosak a szerződésekéivel;
- a metaelemzes.contracts segédfüggvényei (load, sha256, parse, index, registry, sync, parancssor).

A validátor egy kis, önálló részhalmaz-implementáció (a motor tesztjei nem függnek a ma_gui-tól); ha a
ma_gui.schema_lite importálható, a két validátor ítéletét is összevetjük.
"""
import copy
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from _helpers import ROOT
from metaelemzes import contracts as K

EXAMPLES = os.path.join(ROOT, "tests", "reference", "contract_examples")
TERV = os.path.join(ROOT, "TERV_validalo_grafikus_felulet.md")
PELDAK = os.path.join(ROOT, "peldak")

EXACT = {("common", 1), ("capabilities", 1), ("ma.validation", 1), ("ma.analysis-spec", 1), ("ma.plot", 2),
         ("ma.provenance", 1)}
PROSE = {("ma.validate-request", 1), ("ma.run", 1), ("ma.convert-request", 1), ("ma.convert-result", 1),
         ("ma.compare-result", 1), ("ma.consensus", 1), ("ma.studies", 1), ("ma.project-audit", 1),
         ("ma.activity", 1), ("ma.project", 1),
         # v1: értékelés (instruments/appraisal), GRADE/SoF (E10), PRISMA-folyamatábra (E9)
         ("instrument", 1), ("appraisal", 1), ("appraisal-result", 1), ("rob-summary", 1),
         ("ma.appraisal-agreement", 1), ("ma.rob-sync-proposal", 1), ("ma.grade", 1), ("ma.sof", 1),
         ("ff.flowchart", 1)}
EXPECTED = EXACT | PROSE

SUPPORTED = frozenset(["type", "required", "enum", "const", "pattern", "properties", "additionalProperties", "items",
                       "oneOf", "$ref", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength",
                       "maxLength", "minItems", "maxItems"])
ANNOTATIONS = frozenset(["$schema", "$id", "$comment", "$defs", "title", "description", "default", "examples"])
TYPES = ("null", "boolean", "object", "array", "number", "integer", "string")
# a szk-plugins selftest.yml „Slash commands” lépése (a közös sémák bájtra azonos másolatai oda is kerülnek)
SLASH_RE = re.compile(r"/[a-z][a-z0-9-]*:[a-z][a-z0-9-]*")
SLASH_OK = re.compile(r"https?:|/(api|Users|usr|tmp|etc|bin|svg|xml|xlink|w|a|r):")
EXAMPLE_RE = re.compile(r"^(?P<name>[a-z0-9][a-z0-9.\-]*?)\.v(?P<ver>\d+)\.(?P<label>[a-z0-9_\-]+)\.json$")
_PLACEHOLDER = re.compile(r"^([0-9a-f]*)…$")


# ------------------------------------------------------------------ kis JSON-Schema-részhalmaz validátor
def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_type(v, t):
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "string":
        return isinstance(v, str)
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "number":
        return _is_num(v) and (not isinstance(v, float) or math.isfinite(v))
    if t == "integer":
        return _is_num(v) and (isinstance(v, int) or (math.isfinite(v) and float(v).is_integer()))
    raise ValueError("ismeretlen típus: %s" % t)


def _json_eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_num(a) and _is_num(b):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_eq(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def non_finite(inst, path="$"):
    """A 4.0 szabálya: JSON-szám soha nem NaN/Infinity — bárhol a dokumentumban."""
    if isinstance(inst, float) and not math.isfinite(inst):
        return [path]
    if isinstance(inst, dict):
        return [p for k, v in inst.items() for p in non_finite(v, "%s.%s" % (path, k))]
    if isinstance(inst, list):
        return [p for i, v in enumerate(inst) for p in non_finite(v, "%s[%d]" % (path, i))]
    return []


class MiniValidator(object):
    def __init__(self, registry):
        self.registry = registry

    def resolve(self, ref, root):
        base, _, frag = ref.partition("#")
        doc = self.registry[base] if base else root
        node = doc
        if frag:
            if not frag.startswith("/"):
                raise LookupError("nem JSON-pointer: %s" % ref)
            for part in frag[1:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                node = node[int(part)] if isinstance(node, list) else node[part]
        return node, doc

    def errors(self, inst, schema, root=None):
        out = ["%s: nem véges szám (NaN/Infinity)" % p for p in non_finite(inst)]
        self._v(inst, schema, schema if root is None else root, "$", out)
        return out

    def _ok(self, inst, schema, root, path):
        out = []
        self._v(inst, schema, root, path, out)
        return not out

    def _v(self, inst, s, root, path, out):
        if s is True:
            return
        if s is False:
            out.append("%s: nem megengedett (false séma)" % path)
            return
        if "$ref" in s:
            target, troot = self.resolve(s["$ref"], root)
            self._v(inst, target, troot, path, out)
        if "type" in s:
            types = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(_is_type(inst, t) for t in types):
                out.append("%s: típus %s kell" % (path, "|".join(types)))
                return
        if "const" in s and not _json_eq(inst, s["const"]):
            out.append("%s: const %r kell" % (path, s["const"]))
        if "enum" in s and not any(_json_eq(inst, e) for e in s["enum"]):
            out.append("%s: %r egyike kell" % (path, s["enum"]))
        if "oneOf" in s:
            hits = sum(1 for sub in s["oneOf"] if self._ok(inst, sub, root, path))
            if hits != 1:
                out.append("%s: oneOf — %d ág illeszkedik (pontosan 1 kell)" % (path, hits))
        if isinstance(inst, dict):
            for k in s.get("required", ()):
                if k not in inst:
                    out.append("%s: hiányzó kötelező mező: %s" % (path, k))
            props = s.get("properties", {})
            for k, v in inst.items():
                if k in props:
                    self._v(v, props[k], root, "%s.%s" % (path, k), out)
                elif "additionalProperties" in s:
                    self._v(v, s["additionalProperties"], root, "%s.%s" % (path, k), out)
        if isinstance(inst, list):
            if "items" in s:
                for i, v in enumerate(inst):
                    self._v(v, s["items"], root, "%s[%d]" % (path, i), out)
            if len(inst) < s.get("minItems", 0) or len(inst) > s.get("maxItems", float("inf")):
                out.append("%s: elemszám %d (minItems/maxItems)" % (path, len(inst)))
        if isinstance(inst, str):
            if "pattern" in s and not re.search(s["pattern"], inst):
                out.append("%s: nem illeszkedik a mintára %s" % (path, s["pattern"]))
            if len(inst) < s.get("minLength", 0) or len(inst) > s.get("maxLength", float("inf")):
                out.append("%s: hossz %d (minLength/maxLength)" % (path, len(inst)))
        if _is_num(inst):
            if "minimum" in s and inst < s["minimum"]:
                out.append("%s: < minimum %r" % (path, s["minimum"]))
            if "maximum" in s and inst > s["maximum"]:
                out.append("%s: > maximum %r" % (path, s["maximum"]))
            if "exclusiveMinimum" in s and inst <= s["exclusiveMinimum"]:
                out.append("%s: <= exclusiveMinimum %r" % (path, s["exclusiveMinimum"]))
            if "exclusiveMaximum" in s and inst >= s["exclusiveMaximum"]:
                out.append("%s: >= exclusiveMaximum %r" % (path, s["exclusiveMaximum"]))


REGISTRY = K.registry()
VALIDATOR = MiniValidator(REGISTRY)


def errors_for(doc, name, version):
    return VALIDATOR.errors(doc, REGISTRY[K.urn(name, version)])


def walk_schemas(node, ptr="#"):
    """(json-pointer, séma-csomópont) párok — csak a séma-pozíciókon (nem az enum/const/examples értékein)."""
    if not isinstance(node, dict):
        return
    yield ptr, node
    for key in ("properties", "$defs"):
        for k, sub in (node.get(key) or {}).items():
            for x in walk_schemas(sub, "%s/%s/%s" % (ptr, key, k.replace("~", "~0").replace("/", "~1"))):
                yield x
    for key in ("additionalProperties", "items"):
        if isinstance(node.get(key), dict):
            for x in walk_schemas(node[key], "%s/%s" % (ptr, key)):
                yield x
    for i, sub in enumerate(node.get("oneOf") or ()):
        for x in walk_schemas(sub, "%s/oneOf/%d" % (ptr, i)):
            yield x


def design_blocks():
    """[(szakasz, objektum)] a terv 4. fejezetének ```json blokkjaiból (blokkonként több objektum is lehet)."""
    with open(TERV, encoding="utf-8") as fh:
        text = fh.read()
    sec = text[text.index("## 4. Adatszerződések"):text.index("## 5. Plugin-integráció")]
    dec, cur, out = json.JSONDecoder(), None, []
    for m in re.finditer(r"^### (4\.\d+)[^\n]*$|^```json\n(.*?)^```", sec, re.M | re.S):
        if m.group(1):
            cur = m.group(1)
            continue
        body, i = m.group(2), 0
        while True:
            while i < len(body) and body[i].isspace():
                i += 1
            if i >= len(body):
                break
            obj, i = dec.raw_decode(body, i)
            out.append((cur, obj))
    return out


def normalize_design_example(obj, key=None, parent=None):
    """A terv példáinak helykitöltői: '9f3a…' / '…' hash → 64 jegyű hex (nullákkal kiegészítve);
    'generated': '…' → rögzített időbélyeg. Minden más változatlan."""
    if isinstance(obj, dict):
        return {k: normalize_design_example(v, k, key) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_design_example(v, None, key) for v in obj]
    if isinstance(obj, str):
        m = _PLACEHOLDER.match(obj)
        if m and ((key and ("sha256" in key or key == "prev")) or parent in ("inputs", "outputs")):
            return m.group(1) + "0" * (64 - len(m.group(1)))
        if key == "generated" and obj == "…":
            return "2026-10-04T21:12:00Z"
    return obj


def _reject_constant(token):
    raise ValueError("NaN/Infinity a példafájlban: %s" % token)


def example_files():
    """[(fájlnév, név, verzió, címke, dokumentum)]"""
    out = []
    for fn in sorted(os.listdir(EXAMPLES)):
        m = EXAMPLE_RE.match(fn)
        if not m:
            continue
        with open(os.path.join(EXAMPLES, fn), encoding="utf-8") as fh:
            doc = json.load(fh, parse_constant=_reject_constant)
        out.append((fn, m.group("name"), int(m.group("ver")), m.group("label"), doc))
    return out


def strip_annotations(node):
    if isinstance(node, dict):
        return {k: strip_annotations(v) for k, v in node.items() if k not in ANNOTATIONS}
    if isinstance(node, list):
        return [strip_annotations(v) for v in node]
    return node


# ------------------------------------------------------------------ sémafájlok
class TestSchemaFiles(unittest.TestCase):
    def test_expected_contracts_present_and_no_stray_json(self):
        self.assertEqual(set(K.available()), EXPECTED)
        self.assertEqual(set(K.CONTRACTS), EXPECTED, "a CONTRACTS jegyzék és a fájlok eltérnek")
        stray = [f for f in os.listdir(K.DIR) if f.endswith(".json") and not f.endswith(K.SUFFIX)]
        # a ma_gui caps.py a mappa minden *.json-ját szerződésként hirdeti
        self.assertEqual(stray, [])

    def test_ids_filenames_and_schema_consts(self):
        for name, ver in K.available():
            s = K.load(name, ver)
            with self.subTest(contract=name):
                self.assertEqual(s.get("$schema"), K.DRAFT)
                self.assertEqual(s.get("$id"), "urn:szk:contract:%s:%d" % (name, ver))
                self.assertTrue(s.get("title", "").startswith("szk.%s/v%d" % (name, ver)), s.get("title"))
                const = ((s.get("properties") or {}).get("schema") or {}).get("const")
                if name == "common":
                    self.assertIsNone(const)
                    self.assertIn("$defs", s)
                else:
                    self.assertEqual(const, "szk.%s/v%d" % (name, ver))
                    self.assertEqual(s.get("type"), "object")
                    self.assertIn("schema", s.get("required", []))

    def test_canonical_bytes_without_nan(self):
        for name, ver in K.available():
            data = K.raw(name, ver)
            with self.subTest(contract=name):
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "BOM")
                self.assertNotIn(b"\r", data)
                self.assertEqual(data.decode("utf-8"), K.canonical_text(K.load(name, ver)),
                                 "nem kanonikus formázás: a fájlt K.canonical_text()-tel kell írni")
                self.assertEqual(K.sha256(name, ver), hashlib.sha256(data).hexdigest())

    def test_keywords_types_patterns(self):
        for name, ver in K.available():
            s = K.load(name, ver)
            for ptr, node in walk_schemas(s):
                with self.subTest(contract=name, at=ptr):
                    unknown = set(node) - SUPPORTED - ANNOTATIONS
                    self.assertEqual(unknown, set(), "a schema_lite nem ismeri")
                    if "type" in node:
                        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
                        self.assertTrue(types and all(t in TYPES for t in types), types)
                        self.assertEqual(len(types), len(set(types)))
                    if "required" in node:
                        self.assertTrue(all(isinstance(k, str) for k in node["required"]))
                        self.assertEqual(len(node["required"]), len(set(node["required"])))
                    if "pattern" in node:
                        re.compile(node["pattern"])
                    if "enum" in node:
                        self.assertIsInstance(node["enum"], list)
                        self.assertTrue(node["enum"])

    def test_every_ref_resolves(self):
        seen_cross = 0
        for name, ver in K.available():
            s = K.load(name, ver)
            for ptr, node in walk_schemas(s):
                if "$ref" not in node:
                    continue
                ref = node["$ref"]
                with self.subTest(contract=name, at=ptr, ref=ref):
                    base = ref.partition("#")[0]
                    if base:
                        seen_cross += 1
                        self.assertRegex(base, r"^urn:szk:contract:[a-z0-9.\-]+:\d+$")
                        self.assertIn(base, REGISTRY, "a hivatkozott szerződés nincs a mappában")
                    target, _ = VALIDATOR.resolve(ref, s)
                    self.assertIsInstance(target, (dict, bool))
        self.assertGreater(seen_cross, 10)

    def test_marketplace_slash_lint(self):
        for name, ver in K.available():
            text = K.raw(name, ver).decode("utf-8")
            bad = [m.group(0) for m in SLASH_RE.finditer(text) if not SLASH_OK.search(m.group(0))]
            self.assertEqual(bad, [], name)

    def test_prose_marker(self):
        for key in EXPECTED:
            s = K.load(*key)
            with self.subTest(contract=key[0]):
                if key in PROSE:
                    self.assertEqual(s.get("$comment"), K.PROSE_COMMENT)
                else:
                    self.assertNotIn("$comment", s)

    @unittest.skipUnless(os.path.isfile(TERV), "nincs meg a terv (TERV_validalo_grafikus_felulet.md)")
    def test_design_json_schemas_transcribed_exactly(self):
        found = set()
        for sec, obj in design_blocks():
            if not str(obj.get("$id", "")).startswith(K.URN_PREFIX):
                continue
            key = K.parse(obj["$id"])
            found.add(key)
            mine = K.load(*key)
            del mine["$schema"], mine["title"]
            if key == ("ma.analysis-spec", 1):
                opts = mine["properties"]["options"]
                for k in ("$comment", "required", "properties"):      # a generált rész
                    del opts[k]
            self.assertEqual(mine, obj, "%s (terv %s)" % (key[0], sec))
        self.assertEqual(found, EXACT)

    def test_analysis_spec_options_are_generated_from_defaults(self):
        from metaelemzes import spec as SPEC
        from metaelemzes.pipeline import DEFAULTS
        opts = K.load("ma.analysis-spec", 1)["properties"]["options"]
        self.assertIs(opts["additionalProperties"], False)
        self.assertEqual(list(opts["properties"]), list(DEFAULTS))
        gen = SPEC.analysis_spec_schema()["properties"]["options"]
        self.assertEqual(strip_annotations(opts["properties"]), strip_annotations(gen["properties"]),
                         "a spec.py opciói megváltoztak — futtasd: python3 -m metaelemzes.contracts sync")
        self.assertEqual(opts["required"], gen["required"])

    def test_patterns_match_engine_checkers(self):
        """A kézi ellenőrzők (spec, projekt, activity, validate) mintái azonosak a szerződésekéivel."""
        from metaelemzes import activity as A, projekt as P, spec as SPEC, validate as V
        common = K.load("common", 1)["$defs"]
        spec = K.load("ma.analysis-spec", 1)["properties"]
        run = K.load("ma.run", 1)["properties"]
        act = K.load("ma.activity", 1)["properties"]
        proj = K.load("ma.project", 1)["properties"]
        pairs = [
            (common["relpath"]["pattern"], getattr(SPEC, "RELPATH_PATTERN", None)),
            (common["relpath"]["pattern"], getattr(getattr(P, "_RELPATH", None), "pattern", None)),
            (common["sha256"]["pattern"], getattr(SPEC, "SHA256_PATTERN", None)),
            (common["sha256"]["pattern"], getattr(getattr(V, "_SHA256", None), "pattern", None)),
            (spec["name"]["pattern"], getattr(SPEC, "NAME_PATTERN", None)),
            (spec["filters"]["properties"]["exclude"]["items"]["pattern"], getattr(SPEC, "FILTER_PATTERN", None)),
            (run["run_id"]["pattern"], getattr(SPEC, "RUN_ID_PATTERN", None)),
            (act["ts"]["pattern"], getattr(getattr(A, "_TS_RE", None), "pattern", None)),
            (act["action"]["pattern"], getattr(getattr(A, "_ACTION_RE", None), "pattern", None)),
            (proj["outcomes"]["items"]["properties"]["id"]["pattern"],
             getattr(getattr(P, "_OUTCOME_ID", None), "pattern", None)),
        ]
        for i, (mine, engine) in enumerate(pairs):
            if engine is not None:
                self.assertEqual(mine, engine, "minta #%d" % i)
        self.assertEqual(proj["review_type"]["enum"], list(P.REVIEW_TYPES))
        self.assertEqual(proj["data_class"]["enum"], list(P.DATA_CLASSES))
        self.assertEqual(proj["locale"]["enum"], list(P.LOCALES))
        conv = proj["conventions"]["properties"]
        self.assertEqual({k: tuple(v["enum"]) for k, v in conv.items()}, dict(P.CONVENTIONS))
        self.assertEqual(run["mode"]["enum"], list(SPEC.MODES))
        self.assertEqual(spec["purpose"]["enum"], list(SPEC.PURPOSES))


# ------------------------------------------------------------------ példák
class TestExamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = example_files()

    def test_example_file_names(self):
        names = [f for f in os.listdir(EXAMPLES) if f.endswith(".json")]
        self.assertEqual(len(names), len(self.files), "érvénytelen példafájlnév (<név>.v<N>.<címke>.json)")
        for fn, name, ver, label, doc in self.files:
            with self.subTest(example=fn):
                self.assertIn((name, ver), EXPECTED)
                self.assertEqual(doc.get("schema"), "szk.%s/v%d" % (name, ver))

    @unittest.skipUnless(os.path.isfile(TERV), "nincs meg a terv (TERV_validalo_grafikus_felulet.md)")
    def test_design_examples_copied_faithfully(self):
        expected = {}
        for sec, obj in design_blocks():
            if sec == "4.2" or not isinstance(obj.get("schema"), str):      # 4.2: munkapad-boríték
                continue
            try:
                key = K.parse(obj["schema"])
            except K.ContractError:
                continue
            if key in EXPECTED:
                expected["%s.v%d.terv.json" % key] = normalize_design_example(obj)
        self.assertGreaterEqual(len(expected), 8)
        files = {fn: doc for fn, _n, _v, label, doc in self.files if label == "terv"}
        self.assertEqual(sorted(files), sorted(expected))
        for fn, doc in expected.items():
            self.assertEqual(files[fn], doc, fn)

    def test_examples_validate_as_labelled(self):
        for fn, name, ver, label, doc in self.files:
            errs = errors_for(doc, name, ver)
            with self.subTest(example=fn):
                if label.startswith("invalid"):
                    self.assertTrue(errs, "a negatív példát a séma elfogadta")
                else:
                    self.assertEqual(errs, [])

    def _positives(self):
        return [(fn, n, v, doc) for fn, n, v, label, doc in self.files if not label.startswith("invalid")]

    def test_mutations_of_positive_examples(self):
        for fn, name, ver, doc in self._positives():
            schema = REGISTRY[K.urn(name, ver)]
            with self.subTest(example=fn):
                minimal = {k: doc[k] for k in schema["required"]}
                self.assertEqual(errors_for(minimal, name, ver), [], "csak a kötelező mezőkkel")
                for k in schema["required"]:
                    bad = dict(doc)
                    del bad[k]
                    self.assertTrue(errors_for(bad, name, ver), "hiányzó %s elfogadva" % k)
                bumped = dict(doc, schema="szk.%s/v%d" % (name, ver + 1))
                self.assertTrue(errors_for(bumped, name, ver), "más főverzió elfogadva")
                self.assertTrue(errors_for(dict(doc, x_nan=float("nan")), name, ver), "NaN elfogadva")
                self.assertTrue(errors_for(dict(doc, x_inf=float("inf")), name, ver), "Infinity elfogadva")
                self.assertTrue(errors_for([doc], name, ver), "tömb gyökér elfogadva")
                extra = dict(doc, x_ismeretlen={"a": 1})
                self.assertEqual(errors_for(extra, name, ver), [], "4.0: az ismeretlen mezőt el kell fogadni")

    def test_at_least_three_positive_and_negative_per_contract(self):
        pos, neg = {}, {}
        for fn, name, ver, label, doc in self.files:
            bucket = neg if label.startswith("invalid") else pos
            bucket.setdefault((name, ver), []).append(fn)
        for key in EXPECTED - {("common", 1)}:
            with self.subTest(contract=key[0]):
                # fájlpéldák + a csak-kötelező változat ≥ 3; negatívak: fájlok + mutációk (≥ 4 / pozitív példa)
                self.assertGreaterEqual(len(pos.get(key, [])), 2)
                self.assertGreaterEqual(len(neg.get(key, [])), 2)

    def test_common_definitions(self):
        cases = {
            "i18n": ([{"hu": "0,49", "en": "0.49"}, {"hu": "", "en": "", "de": "x"}], [{"hu": "a"}, "a", {"hu": 1, "en": "b"}]),
            "num": ([0, -1.5, None, 1e308], ["1", True, float("nan"), float("inf")]),
            "relpath": (["03_adatok/o1.csv", "a", "05_elemzes/specs/o1_primary.json", "ékezetes mappa/x.csv"],
                        ["/abs/x.csv", "C:/x.csv", "a/../b", "a\\b", "", "x:y"]),
            "sha256": (["0" * 64, "9f3a" + "0" * 60], ["0" * 63, "A" * 64, "g" * 64, None]),
            "severity": (["error", "warning", "info"], ["fatal", None, "Error"]),
            "kbid": (["V008", "X022", "P010", "D-S13-003", "AMSTAR2-00", "K-ABC1"],
                     ["v008", "V08", "D-", "d-s13-003", "X0222"]),
            "row_uid": (["r7f3a2", "r11aa", "rpp35os", "r" + "a" * 12], ["7f3a2", "r7F3A2", "r12", "r" + "a" * 13]),
            "display": ([{"est": 0.49, "lo": 0.33, "hi": 0.73}, {"est": None, "lo": None, "hi": None}],
                        [{"est": 0.49, "lo": 0.33}, {"est": "0.49", "lo": 0.33, "hi": 0.73}, None]),
        }
        defs = K.load("common", 1)["$defs"]
        self.assertEqual(set(cases), set(defs))
        for name, (good, bad) in cases.items():
            schema = {"$ref": "urn:szk:contract:common:1#/$defs/" + name}
            for v in good:
                self.assertEqual(VALIDATOR.errors(v, schema), [], (name, v))
            for v in bad:
                self.assertTrue(VALIDATOR.errors(v, schema), (name, v))

    def test_cross_file_refs_are_enforced(self):
        # validate-request options → analysis-spec options; convert-result kind → convert-request kind
        req = {"schema": "szk.ma.validate-request/v1", "measure": "RR", "table": {"header": [], "rows": []},
               "options": {"cc_to": "only0", "smd_vtype": "LS"}}
        self.assertEqual(errors_for(req, "ma.validate-request", 1), [])
        self.assertTrue(errors_for(dict(req, options={"smd_vtype": "XX"}), "ma.validate-request", 1))
        self.assertEqual(errors_for(dict(req, options={"model": "nem-opció-itt"}), "ma.validate-request", 1), [])
        res = {"schema": "szk.ma.convert-result/v1", "kind": "se_to_sd", "engine_version": "0.2.0",
               "outputs": {"sd": 2.1}, "estimated": False, "method": {"id": "se_to_sd"}}
        self.assertEqual(errors_for(res, "ma.convert-result", 1), [])
        self.assertTrue(errors_for(dict(res, kind="se"), "ma.convert-result", 1))

    def test_schema_lite_agrees_when_available(self):
        try:
            from ma_gui import schema_lite as sl
        except Exception as exc:                                     # noqa: BLE001 — opcionális összevetés
            self.skipTest("ma_gui.schema_lite nem importálható: %s" % exc)
        registry = sl.load_schema_dir(K.DIR)
        for schema in registry.values():
            sl.check_schema(schema, registry)
        for fn, name, ver, label, doc in self.files:
            mine = not errors_for(doc, name, ver)
            theirs = not sl.validate(doc, registry[K.urn(name, ver)], registry)
            self.assertEqual(mine, theirs, fn)


# ------------------------------------------------------------------ a motor termelői
CASES = [("bcg_oltas_RR.csv", {"measure": "RR"}),
         ("bcg_oltas_RR.csv", {"measure": "OR", "subgroup": "allokáció", "cumulative": "év"}),
         ("bcg_oltas_RR.csv", {"measure": "RR", "moderators": ["szélesség"], "outliers": True}),
         ("normand1999_folytonos.csv", {"measure": "SMD"}),
         ("molloy2014_korrelacio.csv", {"measure": "ZCOR"}),
         ("pritz1997_arany.csv", {"measure": "PFT"})]


class TestEngineConformance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="szk_contracts_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertConforms(self, doc, name, ver, what):
        text = json.dumps(doc, ensure_ascii=False, allow_nan=False)        # 4.0: nincs NaN/Infinity
        self.assertEqual(errors_for(json.loads(text), name, ver), [], what)

    def test_validation_documents(self):
        from metaelemzes import validate as V
        if not hasattr(V, "validation_document_from_file"):
            self.skipTest("nincs validate.validation_document_from_file (E2)")
        for fn, opts in CASES:
            doc = V.validation_document_from_file(os.path.join(PELDAK, fn), opts["measure"])
            self.assertConforms(doc, "ma.validation", 1, fn)
        with open(os.path.join(EXAMPLES, "ma.validate-request.v1.terv.json"), encoding="utf-8") as fh:
            req = json.load(fh)
        self.assertEqual(errors_for(req, "ma.validate-request", 1), [])
        doc = V.validation_from_request(req)
        self.assertConforms(doc, "ma.validation", 1, "terv 4.3 kérés")
        with open(os.path.join(EXAMPLES, "ma.validate-request.v1.magyar.json"), encoding="utf-8") as fh:
            doc = V.validation_from_request(json.load(fh))
        self.assertConforms(doc, "ma.validation", 1, "magyar kérés")
        self.assertTrue(doc["findings"])

    def test_spec_and_run_descriptor(self):
        from metaelemzes import pipeline, tableio
        from metaelemzes import spec as SPEC
        for fn, opts in CASES[:4]:
            path = os.path.join(PELDAK, fn)
            argv = ["analyze", "--data", path, "--measure", opts["measure"]]
            if "subgroup" in opts:
                argv += ["--subgroup", opts["subgroup"]]
            spec_doc = SPEC.spec_from_argv(argv, project_root=ROOT, pin_data=True)
            self.assertConforms(spec_doc, "ma.analysis-spec", 1, fn)
            rows, meta = tableio.read_table(path)
            out, es = pipeline.run(rows, opts, meta)
            explore = SPEC.run_descriptor(out, mode="explore", spec=spec_doc, data_file=path)
            self.assertConforms(explore, "ma.run", 1, "explore " + fn)
            self.assertNotIn("files", explore)
            outdir = os.path.join(self.tmp, os.path.splitext(fn)[0] + "_" + opts["measure"])
            paths = pipeline.write_outputs(out, es, outdir)
            rid = SPEC.run_id(datetime.datetime(2026, 10, 4, 21, 12), SPEC.sha256_file(path))
            commit = SPEC.run_descriptor(out, mode="commit", run_id=rid, spec=spec_doc, data_file=path, files=paths,
                                         equivalent_argv=["ma.py"] + argv, elapsed_ms=5)
            self.assertConforms(commit, "ma.run", 1, "commit " + fn)
            self.assertIn("results", commit["files"])
            with open(paths["plot_data.json"], encoding="utf-8") as fh:
                written = json.load(fh)
            if written.get("schema") == "szk.ma.plot/v2":
                self.assertConforms(written, "ma.plot", 2, "plot_data.json " + fn)

    def test_plot_documents(self):
        from metaelemzes import pipeline, tableio
        if not hasattr(pipeline, "plot_document"):
            self.skipTest("nincs pipeline.plot_document (E4)")
        for fn, opts in CASES:
            rows, meta = tableio.read_table(os.path.join(PELDAK, fn))
            out, es = pipeline.run(rows, opts, meta)
            doc = pipeline.to_jsonable(pipeline.plot_document(out, es))
            self.assertConforms(doc, "ma.plot", 2, "%s %s" % (fn, opts))

    def test_activity_records(self):
        from metaelemzes import activity as A
        os.makedirs(os.path.join(self.tmp, "03_adatok"))
        with open(os.path.join(self.tmp, "03_adatok", "o1.csv"), "w", encoding="utf-8") as fh:
            fh.write("study;e1;n1;e2;n2\nA;1;10;2;10\n")
        A.append(self.tmp, {"action": "analyze.commit", "actor": "user:SzK",
                            "argv": ["ma.py", "analyze", "--spec", "05_elemzes/specs/o1_primary.json"],
                            "inputs": {"03_adatok/o1.csv": None}, "outputs": {"05_elemzes/o1/x/results.json": None},
                            "result": {"exit_code": 0, "summary": "k=1"}}, fsync=False)
        A.append(self.tmp, {"action": "table.external_edit", "actor": "external",
                            "outputs": ["03_adatok/o1.csv"], "details": {"kind": "watch"}}, fsync=False)
        recs = A.read(self.tmp)
        self.assertEqual(len(recs), 2)
        for rec in recs:
            self.assertConforms(rec, "ma.activity", 1, rec["action"])
        self.assertIsNone(recs[0]["prev"])
        self.assertRegex(recs[1]["prev"], r"^[0-9a-f]{64}$")

    def test_project_audit_report(self):
        try:
            from metaelemzes import audit
        except ImportError:
            self.skipTest("nincs metaelemzes.audit (E8)")
        from metaelemzes import projekt as P
        root = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(root, "03_adatok"))
        os.makedirs(os.path.join(root, "05_elemzes", "specs"))
        P.save_project_meta(root, {"title": "Szerződésteszt", "data_class": "A", "outcomes": [
            {"id": "o1", "name": "Halálozás", "data": "03_adatok/o1.csv", "measure": "RR",
             "primary_spec": "05_elemzes/specs/o1_primary.json"}]})

        def put(rel, obj):
            with open(os.path.join(root, *rel.split("/")), "w", encoding="utf-8") as fh:
                fh.write(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False))

        put("03_adatok/o1.csv", "row_uid;study_id;study;e1;n1;e2;n2;rob;estimated;forras_oldal\n" + "".join(
            "r%04d;S%d;Vizsgálat %d;%d;100;%d;100;%s;%s;p. %d\n" % (
                i, i, i, 5 + i, 9 + i, "high" if i == 1 else "low", "igen" if i == 2 else "nem", i + 2)
            for i in range(4)))
        with open(os.path.join(EXAMPLES, "ma.analysis-spec.v1.o1_primary.json"), encoding="utf-8") as fh:
            spec_doc = json.load(fh)
        spec_doc["data"] = {"path": "03_adatok/o1.csv"}
        put("05_elemzes/specs/o1_primary.json", spec_doc)
        put("03_adatok/studies.json", {"schema": "szk.ma.studies/v1", "studies": [{"study_id": "S0"}, {"study_id": "S1"}]})
        put("03_adatok/o1.prov.json", {"schema": "szk.ma.provenance/v1", "table": "03_adatok/o1.csv",
                                       "table_sha256": "0" * 64, "cells": [
                                           {"row_uid": "r0002", "field": "e1", "method": "reported", "estimated": False}]})
        for stage in (None, "S13", "FINAL"):
            rep = json.loads(audit.to_json(audit.project_audit(root, stage=stage)))
            self.assertConforms(rep, "ma.project-audit", 1, "stage=%s" % stage)
            self.assertTrue(rep["findings"])

    def test_project_meta_agrees_with_engine_checker(self):
        from metaelemzes import projekt as P
        for fn, name, ver, label, doc in example_files():
            if (name, ver) != ("ma.project", 1):
                continue
            norm, errs = P.validate_project_meta(copy.deepcopy(doc))
            with self.subTest(example=fn):
                if label.startswith("invalid"):
                    self.assertTrue(errs)
                else:
                    self.assertEqual(errs, [])
                    self.assertConforms(norm, "ma.project", 1, fn)
        norm, errs = P.validate_project_meta({"title": "Minimális", "data_class": "b",
                                              "outcomes": [{"id": "o1", "name": "Halálozás"}]})
        self.assertEqual(errs, [])
        self.assertConforms(norm, "ma.project", 1, "normalizált")


# ------------------------------------------------------------------ a contracts modul
class TestContractsModule(unittest.TestCase):
    def test_parse_forms(self):
        for raw, want in [("ma.plot", ("ma.plot", None)), ("szk.ma.plot/v2", ("ma.plot", 2)),
                          ("urn:szk:contract:ma.plot:2", ("ma.plot", 2)), ("urn:szk:contract:ma.plot:2#", ("ma.plot", 2)),
                          ("ma.plot.v2.schema.json", ("ma.plot", 2)), ("ma.plot.v2", ("ma.plot", 2)),
                          ("SZK.Capabilities/V1", ("capabilities", 1)), ("common.v1.schema.json", ("common", 1))]:
            self.assertEqual(K.parse(raw), want, raw)
        self.assertEqual(K.parse("ma.run", "v1"), ("ma.run", 1))
        self.assertEqual(K.parse("ma.run", "1"), ("ma.run", 1))
        for bad in [("ma.plot.v2", 3), ("ma.run", 0), ("ma.run", True), ("ma.run", "x"), ("../etc/passwd", None),
                    ("ma plot", None), ("", None)]:
            with self.assertRaises(K.ContractError, msg=repr(bad)):
                K.parse(*bad)

    def test_load_path_sha256(self):
        self.assertEqual(K.path("ma.plot"), os.path.join(K.DIR, "ma.plot.v2.schema.json"))
        a = K.load("szk.ma.plot/v2")
        a["properties"].clear()
        self.assertTrue(K.load("ma.plot", 2)["properties"], "a load friss példányt ad")
        with open(K.path("common", 1), "rb") as fh:
            self.assertEqual(K.sha256("common", 1), hashlib.sha256(fh.read()).hexdigest())
        for args in [("ma.plot", 1), ("nincs-ilyen",), ("ma.studies", 9)]:
            with self.assertRaises(K.ContractError):
                K.load(*args)
        with self.assertRaises(LookupError):
            K.sha256("nincs-ilyen", 1)

    def test_index_registry_direction(self):
        rows = K.index()
        self.assertEqual([(r["name"], r["version"]) for r in rows], K.available())
        for r in rows:
            with self.subTest(contract=r["name"]):
                self.assertEqual(r["file"], "%s.v%d.schema.json" % (r["name"], r["version"]))
                self.assertEqual(r["schema"], "szk.%s/v%d" % (r["name"], r["version"]))
                self.assertEqual(r["id"], K.load(r["name"], r["version"])["$id"])
                self.assertRegex(r["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(r["producer"] and r["consumer"])
                self.assertTrue(set(r["dir"]) <= {"in", "out"})
                self.assertEqual(r["dir"], K.direction(r["name"], r["version"]))
                if r["name"] != "common":
                    self.assertTrue(r["dir"])
        self.assertEqual(set(K.registry()), {r["id"] for r in rows})
        self.assertEqual(K.direction("szk.ma.validate-request/v1"), ["in"])
        self.assertEqual(K.direction("ma.plot"), ["out"])

    def test_sync_rewrites_generated_options_only(self):
        self.assertTrue(K.sync())
        tmp = tempfile.mkdtemp(prefix="szk_contracts_sync_")
        old = K.DIR
        try:
            for fn in os.listdir(old):
                if fn.endswith(K.SUFFIX):
                    shutil.copy2(os.path.join(old, fn), os.path.join(tmp, fn))
            K.DIR = tmp
            original = K.raw("ma.analysis-spec", 1)
            s = K.load("ma.analysis-spec", 1)
            del s["properties"]["options"]["properties"]["outliers"]
            with open(K.path("ma.analysis-spec", 1), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(K.canonical_text(s))
            self.assertFalse(K.sync())
            self.assertTrue(K.sync(write=True))
            self.assertEqual(K.raw("ma.analysis-spec", 1), original)
            self.assertEqual([f for f in os.listdir(tmp) if ".tmp" in f], [])
        finally:
            K.DIR = old
            shutil.rmtree(tmp, ignore_errors=True)

    def test_command_line(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        run = lambda *a: subprocess.run([sys.executable, "-m", "metaelemzes.contracts"] + list(a), cwd=ROOT,  # noqa: E731
                                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        p = run("--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = json.loads(p.stdout.decode("utf-8"))
        self.assertEqual(rows, K.index())
        p = run("check")
        self.assertEqual(p.returncode, 0, p.stderr)
        p = run("ismeretlen")
        self.assertEqual(p.returncode, 2)
        self.assertIn("HIBA", p.stderr.decode("utf-8"))

    def test_readme_lists_every_contract(self):
        with open(os.path.join(K.DIR, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        for name, ver in K.available():
            self.assertIn("`%s`" % K.filename(name, ver), text)
            self.assertIn("szk.%s/v%d" % (name, ver), text)


if __name__ == "__main__":
    unittest.main()
