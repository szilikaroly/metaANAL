# -*- coding: utf-8 -*-
"""Sodródás-őr (terv 4.20, 5.4) és fixture-megfelelés az értékelő eszközökhöz.

1. **Motor ↔ validator** (ha a motor natív definíciói — ``metaelemzes.api`` instruments — elérhetők ÉS a validator plugin
   jelen van): eszközönként a tétel-azonosítók, PROBAST+AI-nál a menetenkénti 16 + 18 jelző-kérdés, TRIPOD+AI-nál az 52
   altétel D/E címkéje, AMSTAR 2-nél a 7 kritikus tétel egyezik-e a validator saját vázával (``appraise.py --skeleton``,
   ``checklist.py --skeleton``; a plugint csak futtatjuk, nem importáljuk és nem módosítjuk). Hiányzó motor-függvény
   vagy plugin: a teszt tisztán kimarad.
2. **Fixture ↔ motor** (FID-7): a fejlesztői fixture-ök a MOTOR kimenetei (``gen_appraisal_fixtures.py`` alapból a
   ``metaelemzes.api``-val fut; ``--check`` ezzel vet össze), az eszköz-definíciók azonosak az ``api.instrument_get``-tel,
   és minden ``schema``-mezős fixture-objektum, amelyhez a motornak szerződése van (metaelemzes/contracts), annak
   megfelel (jsonschema, 2020-12). A validator vázával való összevetés verziófüggő: az 1.0.x régi ROBINS-I- és
   QUIPS-számozásával a ``validator_ids``-en át, a javított (≥ 2.0.0) validatoréval a motor saját — publikált —
   azonosítóival.
3. **Fixture-alak** (mindig): a ``web/fixtures/appraisal_*.json`` a 4.2 borítékot és a 4.11 szerződés kötelező mezőit
   követi; a mintaértékelések útja a 2.4 elrendezés."""
import glob
import json
import os
import re
import subprocess
import sys
import unittest


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from ma_gui.adapters import validator as V  # noqa: E402
from ma_gui.routes import appraisal_common as C  # noqa: E402

FIX = os.path.join(ROOT, "ma_gui", "web", "fixtures")
DEFAULT_PLUGINS = "/home/user/szilikaroly/szk-plugins/plugins"
APPRAISE_TOOLS = ("rob2", "robins-i", "robins-e", "quadas2", "nos", "quips", "jbi", "amstar2")
ROW_RE = re.compile(r"^\|\s*([0-9A-Za-z][0-9A-Za-z.\-]*)\s*\|")
ALGORITHMS = ("published", "count", "conservative", "none", "validator-compatible")


def plugin_dir():
    for d in (os.environ.get("MA_GUI_PLUGIN_DIRS") or DEFAULT_PLUGINS).split(os.pathsep):
        v = os.path.join(d, "validator")
        if os.path.isfile(os.path.join(v, "scripts", "appraise.py")):
            return v
    return None


def _run(vdir, script, *args):
    out = subprocess.run([sys.executable, os.path.join(vdir, "scripts", script)] + list(args), cwd=vdir,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False,
                         env=dict(os.environ, PYTHONUTF8="1"))
    return out.stdout.decode("utf-8", "replace")


def validator_version(vdir):
    """A plugin.json verziója, vagy None."""
    try:
        v = _load_json(os.path.join(vdir, ".claude-plugin", "plugin.json")).get("version")
    except (OSError, ValueError):
        return None
    return v if isinstance(v, str) else None


def validator_items(vdir):
    """{eszköz: [tétel-id …]} + {'probast-ai': {'development': [...], 'evaluation': [...]}, 'tripod-ai': [(id, D/E)]},
    és a '_version' kulcson a plugin verziója."""
    out = {"_version": validator_version(vdir)}
    for tool in APPRAISE_TOOLS:
        ids = []
        for line in _run(vdir, "appraise.py", "--skeleton", tool, "--scope", "all").splitlines():
            m = ROW_RE.match(line)
            if m and m.group(1) not in ("#",):
                ids.append(m.group(1))
        out[tool] = ids
    pb, cur = {"development": [], "evaluation": []}, None
    for line in _run(vdir, "checklist.py", "--skeleton", "probast", "--scope", "both").splitlines():
        if line.startswith("### Quality (development)"):
            cur = "development"
        elif line.startswith("### Risk of bias (evaluation)"):
            cur = "evaluation"
        m = ROW_RE.match(line)
        if m and cur and m.group(1) != "SQ":
            pb[cur].append(m.group(1))
    out["probast-ai"] = pb
    tr = []
    for line in _run(vdir, "checklist.py", "--skeleton", "tripod", "--scope", "both").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and re.match(r"^\d{1,2}[a-z]?$", cells[0]):
            tr.append((cells[0], cells[1]))
    out["tripod-ai"] = tr
    return out


def compare(testcase, insts, vitems):
    """Az eszköz-definíciók (motor vagy fixture) összevetése a validator vázával."""
    fixed = V.fixed_release({"version": vitems.get("_version")})
    for tool in APPRAISE_TOOLS:
        if tool not in insts:
            continue
        # ahol a motor a publikált eszközt követi a validator 1.0.x helyett (ROBINS-I 2016, QUIPS 1a–6d; v1 javítás
        # A), az 1.0.x azonosítói a 'validator_ids' mezőben vannak — a régi pluginnal a sodródás-őr azzal vet össze
        # (mint a motor saját test_v1_instruments.test_generic_ids_match-e). A javított validator (≥ 2.0.0) a
        # publikált számozást használja: ott a motor SAJÁT azonosítóinak kell egyezniük (H13 nélkül).
        own = [it["id"] for it in insts[tool]["items"]]
        mine = own if fixed else (insts[tool].get("validator_ids") or own)
        testcase.assertEqual(sorted(mine), sorted(vitems[tool]), "%s: a tétel-azonosítók eltérnek (validator %s)"
                             % (tool, vitems.get("_version")))
    if "probast-ai" in insts:
        inst = insts["probast-ai"]
        for p in ("development", "evaluation"):
            mine = [it["id"] for it in inst["items"] if it.get("pass") == p]
            testcase.assertEqual(mine, vitems["probast-ai"][p], "PROBAST+AI %s: eltér" % p)
            for it in inst["items"]:
                if it.get("pass") == p:
                    testcase.assertEqual(it.get("key"), "%s/%s" % (p, it["id"]), "menettel minősített kulcs (H2)")
        testcase.assertEqual((len(vitems["probast-ai"]["development"]), len(vitems["probast-ai"]["evaluation"])), (16, 18))
    if "tripod-ai" in insts:
        mine = [(it["id"], it.get("applies_to")) for it in insts["tripod-ai"]["items"]]
        testcase.assertEqual(mine, vitems["tripod-ai"], "TRIPOD+AI: az 52 altétel vagy a D/E címke eltér")
        testcase.assertEqual(len(mine), 52)
    if "amstar2" in insts:
        crit = sorted((it["id"] for it in insts["amstar2"]["items"] if it.get("critical")), key=int)
        testcase.assertEqual(crit, ["2", "4", "7", "9", "11", "13", "15"], "AMSTAR 2: a kritikus tételek")


def fixture_instruments():
    data = _load_json(os.path.join(FIX, "appraisal_instruments.json"))
    return {r["envelope"]["data"]["key"]: r["envelope"]["data"] for r in data["routes"]
            if r["envelope"]["data"].get("schema") == "szk.instrument/v1"}


class EngineDriftTests(unittest.TestCase):
    def test_engine_definitions_match_validator(self):
        vdir = plugin_dir()
        if vdir is None:
            self.skipTest("nincs validator plugin (MA_GUI_PLUGIN_DIRS)")
        lst, get = C.engine_fn("instruments"), C.engine_fn("instrument")
        if lst is None or get is None:
            self.skipTest("a motor natív eszköz-definíciói (metaelemzes.api.%s / %s) még nem érhetők el"
                          % (C.ENGINE["instruments"][0], C.ENGINE["instrument"][0]))
        insts = {}
        for x in lst():
            key = x.get("key") if isinstance(x, dict) else None
            if key:
                insts[key] = get(key)
        self.assertTrue({"rob2", "probast-ai", "tripod-ai", "amstar2"} <= set(insts), sorted(insts))
        compare(self, insts, validator_items(vdir))


class FixtureDriftTests(unittest.TestCase):
    def test_fixtures_match_validator(self):
        vdir = plugin_dir()
        if vdir is None:
            self.skipTest("nincs validator plugin (MA_GUI_PLUGIN_DIRS)")
        compare(self, fixture_instruments(), validator_items(vdir))

    def test_generator_is_up_to_date(self):
        if plugin_dir() is None:
            self.skipTest("nincs validator plugin")
        r = subprocess.run([sys.executable, os.path.join(HERE, "ui", "gen_appraisal_fixtures.py"), "--check"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace"))


def _contract_validator():
    """(séma-azonosító → validáló) a motor szerződés-jegyzékével (metaelemzes.contracts.registry; $ref-ek feloldva)."""
    from metaelemzes import contracts as K
    reg = K.registry()
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        refs = Registry().with_resources([(uri, Resource.from_contents(sch)) for uri, sch in reg.items()])

        def errors(doc, schema):
            return ["%s: %s" % ("/".join(str(x) for x in e.absolute_path), e.message)
                    for e in Draft202012Validator(schema, registry=refs).iter_errors(doc)]
    except ImportError:                                 # tartalék: a repó saját mini-validátora
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        from test_mvp_contracts import MiniValidator
        mv = MiniValidator(reg)

        def errors(doc, schema):
            return mv.errors(doc, schema)

    def for_doc(schema_id):
        try:
            name, ver = K.parse(schema_id)
            return reg.get(K.urn(name, ver))
        except Exception:                               # noqa: BLE001 — nincs ilyen szerződés (felületi nézet)
            return None
    return for_doc, errors


def _schema_objects(node, path="$"):
    if isinstance(node, dict):
        if isinstance(node.get("schema"), str):
            yield path, node
        for k, v in node.items():
            for x in _schema_objects(v, "%s.%s" % (path, k)):
                yield x
    elif isinstance(node, list):
        for i, v in enumerate(node):
            for x in _schema_objects(v, "%s[%d]" % (path, i)):
                yield x


class FixtureEngineTests(unittest.TestCase):
    """FID-7: a fixture-ök a motor kimenetei, és a motor szerződéseinek megfelelnek."""

    def test_instruments_are_engine_definitions(self):
        from metaelemzes import api
        fx = fixture_instruments()
        self.assertEqual(sorted(fx), sorted(x["key"] for x in api.instruments_list()))
        for key, inst in sorted(fx.items()):
            self.assertEqual(inst, api.instrument_get(key), "%s: a fixture eszköz-definíciója nem a motoré" % key)
        q = fx["quadas2"]
        self.assertEqual([it.get("polarity") for it in q["items"] if it["id"] in ("1.2", "1.3")], ["normal", "normal"])
        self.assertEqual(len(fx["robins-i"]["items"]), 34)
        self.assertEqual(fx["quips"]["items"][0]["id"], "1a")

    def test_fixture_objects_validate_against_engine_contracts(self):
        for_doc, errors = _contract_validator()
        n = 0
        for f in sorted(glob.glob(os.path.join(FIX, "appraisal_*.json"))):
            for r in _load_json(f)["routes"]:
                for where, obj in _schema_objects(r["envelope"]["data"]):
                    schema = for_doc(obj["schema"])
                    if schema is None:
                        continue
                    n += 1
                    errs = errors(obj, schema)
                    self.assertEqual(errs[:5], [], "%s %s %s (%s)" % (os.path.basename(f), r["path"], where, obj["schema"]))
        self.assertGreater(n, 20)

    def test_robsummary_texts_are_strings(self):
        data = _load_json(os.path.join(FIX, "appraisal_robsummary.json"))["routes"][0]["envelope"]["data"]
        for st in data["studies"]:
            self.assertTrue(st.get("weight_text") is None or isinstance(st["weight_text"], str), st.get("weight_text"))
        for w in data.get("weighted") or []:
            self.assertTrue(w.get("text") is None or isinstance(w["text"], str), w.get("text"))

    def test_contract_check_is_not_blind(self):
        for_doc, errors = _contract_validator()
        data = _load_json(os.path.join(FIX, "appraisal_robsummary.json"))["routes"][0]["envelope"]["data"]
        bad = json.loads(json.dumps(data))
        bad["studies"][0]["weight_text"] = {"hu": "1,0%", "en": "1.0%"}
        self.assertTrue(errors(bad, for_doc(bad["schema"])), "a régi (csonk) alak elbukik")


class FixtureShapeTests(unittest.TestCase):
    def test_envelopes_and_contract_fields(self):
        files = sorted(glob.glob(os.path.join(FIX, "appraisal_*.json")))
        self.assertGreaterEqual(len(files), 6)
        for f in files:
            data = _load_json(f)
            for r in data["routes"]:
                env = r["envelope"]
                self.assertTrue(r["path"].startswith("/api/"), f)
                self.assertEqual(sorted(k for k in env if k in ("ok", "schema", "data", "warnings", "meta")),
                                 ["data", "meta", "ok", "schema", "warnings"], f)
        for key, inst in fixture_instruments().items():
            for k in ("schema", "key", "name", "unit", "answers", "verdicts", "domains", "items", "rollup", "reference_sha256"):
                self.assertIn(k, inst, "%s.%s" % (key, k))
            self.assertIn(inst["rollup"]["algorithm"], ALGORITHMS, key)
            vals = {a["value"] for a in inst["answers"]}
            self.assertTrue(vals, key)
            for v in inst["verdicts"]:
                # a szint-szótár a motor instrument.v1 szerződéséé (AMSTAR 2: „critical” = kritikusan alacsony)
                self.assertIn(v["level"], ("low", "some", "high", "critical", "ni"), key)
            doms = {d["id"] for d in inst["domains"]}
            for it in inst["items"]:
                self.assertIn(str(it["domain"]), doms, "%s %s" % (key, it["id"]))
                self.assertTrue(C.ANSWER_KEY_RE.match(it["key"]), it["key"])

    def test_seed_appraisals_follow_layout(self):
        insts = fixture_instruments()
        data = _load_json(os.path.join(FIX, "appraisal_docs.json"))
        seen = set()
        for r in data["routes"]:
            v = r["envelope"]["data"]
            doc = v["doc"]
            unit = C.unit_of(doc)
            self.assertEqual(v["path"], C.relpath(unit, doc["tool"], (doc["target"] or {}).get("key"), v["rater"]))
            self.assertNotIn(v["path"], seen)
            seen.add(v["path"])
            probs = C.doc_problems(doc, insts[doc["tool"]], unit, doc["tool"], (doc["target"] or {}).get("key"), v["rater"])
            self.assertEqual(probs, [], v["path"])
            self.assertEqual(r["query"]["rater"], v["rater"])
        origins = {r["envelope"]["data"]["doc"]["origin"] for r in data["routes"]}
        self.assertEqual(origins, {"human", "ai_draft"})


if __name__ == "__main__":
    unittest.main()
