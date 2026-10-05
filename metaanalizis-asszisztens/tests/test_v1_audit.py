# -*- coding: utf-8 -*-
"""v1 (E8 teljes + E9): projekt-audit X002, X004, X007, X008, X009, X011, X012, X015, X017–X021, a FINAL és az S08
kapu, valamint a PRISMA vizsgálat-térkép és a teljes PRISMA 2020 folyamatábra-specifikáció (szk.ff.flowchart/v1).

Valósághű ideiglenes projektmappák (terv 2.4): a commit-futások a valódi motorral készülnek (BCG-adat, RR, k = 13),
az értékelések, a GRADE, a SoF, az ábra-eredmények, a kettős kinyerés és a PRISMA-fájlok a terv szerinti alakban.
Minden szabályt kiváltunk, majd a javítással eltüntetünk; a hiányzó opcionális fájl és a hiányzó (párhuzamosan
fejlődő) v1-modul nem hiba, hanem 'not_checked' tétel, indokkal.
"""
import copy
import datetime
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import types
import unittest
from unittest import mock

from _helpers import ROOT
from metaelemzes import audit as A
from metaelemzes import contracts as K
from metaelemzes import pipeline, projekt, prisma as PR, tableio
from metaelemzes import spec as S

NOW = "2026-10-05T12:00:00Z"
T0 = datetime.datetime(2026, 10, 4, 21, 12, 0)
EXAMPLES = os.path.join(ROOT, "tests", "reference", "contract_examples")
BCG = [("Aronson 1948", 4, 123, 11, 139), ("Ferguson & Simes 1949", 6, 306, 29, 303),
       ("Rosenthal et al 1960", 3, 231, 11, 220), ("Hart & Sutherland 1977", 62, 13598, 248, 12867),
       ("Frimodt-Moller et al 1973", 33, 5069, 47, 5808), ("Stein & Aronson 1953", 180, 1541, 372, 1451),
       ("Vandiviere et al 1973", 8, 2545, 10, 629), ("TPT Madras 1980", 505, 88391, 499, 88391),
       ("Coetzee & Berjak 1968", 29, 7499, 45, 7277), ("Rosenthal et al 1961", 17, 1716, 65, 1665),
       ("Comstock et al 1974", 186, 50634, 141, 27338), ("Comstock & Webster 1969", 5, 2498, 3, 2341),
       ("Comstock et al 1976", 27, 16913, 29, 17854)]
HEADER = ["row_uid", "study_id", "study", "e1", "n1", "e2", "n2", "rob", "estimated", "forras_oldal"]
V1_RULES = {"X002", "X004", "X007", "X008", "X009", "X011", "X012", "X015", "X017", "X018", "X019", "X020", "X021"}
MVP_RULES = {"X001", "X003", "X005", "X006", "X010", "X013", "X014", "X016", "X022"}


def uid(i):
    return "rbcg%02d" % (i + 1)


def sid(i):
    return "S%02d" % (i + 1)


def bcg_rows():
    return [{"row_uid": uid(i), "study_id": sid(i), "study": lab, "e1": e1, "n1": n1, "e2": e2, "n2": n2,
             "rob": "low", "estimated": "nem", "forras_oldal": "p. %d" % (i + 3)}
            for i, (lab, e1, n1, e2, n2) in enumerate(BCG)]


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def codes(rep, code=None, outcome=None):
    return [f for f in rep["findings"] if (code is None or f["code"] == code)
            and (outcome is None or f["outcome"] == outcome)]


def skipped(rep, code, outcome="any"):
    return [n for n in rep["not_checked"] if n["code"] == code and (outcome == "any" or n["outcome"] == outcome)]


# ------------------------------------------------------------------ a szerződések részhalmaz-validátora
def _is_type(v, t):
    num = isinstance(v, (int, float)) and not isinstance(v, bool)
    return {"null": v is None, "boolean": isinstance(v, bool), "object": isinstance(v, dict),
            "array": isinstance(v, list), "string": isinstance(v, str),
            "number": num and (not isinstance(v, float) or math.isfinite(v)),
            "integer": num and (isinstance(v, int) or (math.isfinite(v) and float(v).is_integer()))}[t]


def contract_errors(doc, name, version=1):
    """A dokumentum eltérései a metaelemzes/contracts sémájától (a sémák kulcsszó-részhalmaza)."""
    reg = K.registry()
    root = reg[K.urn(name, version)]
    out = []

    def run(inst, schema, root, where):
        if "$ref" in schema:
            base, _, frag = schema["$ref"].partition("#")
            node = reg[base] if base else root
            rroot = node
            for part in [p for p in frag.split("/") if p]:
                node = node[part]
            run(inst, node, rroot, where)
        if isinstance(inst, float) and not math.isfinite(inst):
            out.append("%s: nem véges" % where)
            return
        if "type" in schema:
            ts = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_is_type(inst, t) for t in ts):
                out.append("%s: típus %s" % (where, ts))
                return
        if "const" in schema and inst != schema["const"]:
            out.append("%s: const" % where)
        if "enum" in schema and inst not in schema["enum"]:
            out.append("%s: enum" % where)
        if "oneOf" in schema:
            hits = 0
            for sub in schema["oneOf"]:
                before = len(out)
                run(inst, sub, root, where)
                hits += 1 if len(out) == before else 0
                del out[before:]
            if hits != 1:
                out.append("%s: oneOf %d" % (where, hits))
        if isinstance(inst, dict):
            for k in schema.get("required", ()):
                if k not in inst:
                    out.append("%s: hiányzó %s" % (where, k))
            props = schema.get("properties", {})
            for k, v in inst.items():
                if k in props:
                    run(v, props[k], root, "%s.%s" % (where, k))
                elif isinstance(schema.get("additionalProperties"), dict):
                    run(v, schema["additionalProperties"], root, "%s.%s" % (where, k))
        if isinstance(inst, list):
            if "items" in schema:
                for i, v in enumerate(inst):
                    run(v, schema["items"], root, "%s[%d]" % (where, i))
            if len(inst) < schema.get("minItems", 0):
                out.append("%s: minItems" % where)
        if isinstance(inst, str):
            if "pattern" in schema and not re.search(schema["pattern"], inst):
                out.append("%s: pattern" % where)
            if len(inst) < schema.get("minLength", 0):
                out.append("%s: minLength" % where)
        if isinstance(inst, (int, float)) and not isinstance(inst, bool):
            if "minimum" in schema and inst < schema["minimum"]:
                out.append("%s: minimum" % where)
            if "maximum" in schema and inst > schema["maximum"]:
                out.append("%s: maximum" % where)
            if "exclusiveMinimum" in schema and inst <= schema["exclusiveMinimum"]:
                out.append("%s: exclusiveMinimum" % where)
    run(doc, root, root, "$")
    return out


# ------------------------------------------------------------------ projektépítő
class Proj(object):
    """Valósághű projektmappa (o1: RR, BCG) a v1-fájlokkal; a commit-futás a valódi motorral."""

    def __init__(self, base, name="v1proj"):
        self.root = os.path.join(base, name)
        projekt.init(self.root, "v1 audit", "PICO?")
        self.spec_rel = self.spec_doc = None

    def p(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def write(self, rel, data):
        path = self.p(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        with open(path, "wb") as fh:
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def sha(self, rel):
        with open(self.p(rel), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def meta(self, outcomes=None, **kw):
        m = {"title": "v1 audit", "data_class": "A", "review_type": "intervention",
             "outcomes": outcomes if outcomes is not None else [
                 {"id": "o1", "name": {"hu": "Tbc", "en": "TB"}, "data": "03_adatok/o1.csv", "measure": "RR",
                  "primary_spec": "05_elemzes/specs/o1_primary.json"}]}
        m.update(kw)
        projekt.save_project_meta(self.root, m)

    def csv(self, rows=None, rel="03_adatok/o1.csv"):
        rows = rows if rows is not None else bcg_rows()
        lines = [";".join(HEADER)] + [";".join("" if r.get(h) is None else str(r.get(h)) for h in HEADER)
                                      for r in rows]
        self.write(rel, "\n".join(lines) + "\n")
        return rel

    def studies(self, n=13, outcomes=("o1",), designs=None, extra=()):
        st = []
        for i in range(n):
            s = {"study_id": sid(i), "label": BCG[i][0], "design": (designs or {}).get(sid(i), "rct_parallel"),
                 "reports": [{"rec_id": "pmid:%d" % (100 + i), "role": "primary"}]}
            if outcomes is not None:
                s["outcomes"] = list(outcomes)
            st.append(s)
        self.write("03_adatok/studies.json", {"schema": "szk.ma.studies/v1", "studies": st + list(extra)})

    def spec(self, name="o1_primary", purpose="primary", filters=None, outcome="o1", data="03_adatok/o1.csv"):
        doc = {"schema": "szk.ma.analysis-spec/v1", "name": name, "outcome": outcome, "purpose": purpose,
               "prespecified": True, "protocol_ref": "00_protokoll/protokoll.md#elemzes", "parent": None,
               "data": {"path": data}, "options": {"measure": "RR"},
               "filters": filters or {"exclude": [], "include": []}}
        rel = "05_elemzes/specs/%s.json" % name
        self.write(rel, doc)
        if purpose == "primary":
            self.spec_rel, self.spec_doc = rel, doc
        return rel, doc

    def engine(self, spec_rel=None, doc=None, minutes=0, plots=True):
        """Valódi commit-futás (pipeline + spec.run_descriptor) → a run.json tartalma + '_dir'."""
        spec_rel, doc = spec_rel or self.spec_rel, doc or self.spec_doc
        data = self.p(doc["data"]["path"])
        rows, meta = tableio.read_table(data)
        excl, incl = S.filters_from_spec(doc)
        rep = []
        rows_f = tableio.apply_filters(rows, excl, incl, meta=meta, report=rep)
        meta["filters"] = {"exclude": excl, "include": incl}
        opts = S.options_from_spec(doc)
        opts["filter_report"] = rep
        out, es = pipeline.run(rows_f, opts, meta)
        sha = S.sha256_file(data)
        t = T0 + datetime.timedelta(minutes=minutes)
        rid = S.run_id(t, sha)
        rel = "05_elemzes/%s/%s" % (doc.get("outcome") or "o1", rid)
        paths = pipeline.write_outputs(out, es, self.p(rel), None, plots=plots)
        desc = S.run_descriptor(out, mode="commit", run_id=rid, spec=doc, spec_path=self.p(spec_rel),
                                data_file=data, files=paths, project_root=self.root, started=t, finished=t)
        S.write_run_json(self.p(rel), desc)
        desc = dict(desc)
        desc["_dir"] = rel
        with open(self.p(rel + "/results.json"), encoding="utf-8") as fh:
            desc["_results"] = json.load(fh)
        return desc

    def ready(self, **meta_kw):
        """meta + tábla + studies.json + elsődleges spec + valódi commit-futás (tiszta kiindulás)."""
        self.meta(**meta_kw)
        self.csv()
        self.studies()
        self.spec()
        return self.engine()

    def appraisal(self, unit, tool="rob2", status="complete", judgement="low", outcome="o1", assessor="SzK",
                  origin="human", approved_by=None, domain_judgements=None, answers=None, overall=None):
        doc = {"schema": "szk.appraisal/v1", "tool": tool, "scope": None,
               "target": {"unit": unit, "study_id": unit if unit != "review" else None, "outcome": outcome},
               "assessor": assessor, "second_assessor": None, "status": status, "origin": origin,
               "approved_by": approved_by, "answers": answers or {}, "domain_judgements": domain_judgements or [],
               "overall": overall if overall is not None else {"judgement": judgement, "rationale": "indok",
                                                                "implied": None, "override_reason": None},
               "created": "2026-10-01T10:00:00Z", "updated": "2026-10-02T10:00:00Z"}
        rater = "consensus" if status == "consensus" else ("ai" if origin == "ai_draft" else assessor)
        rel = "04_torzitas_kockazat/appraisals/%s.%s.%s.json" % (unit, tool, rater)
        self.write(rel, doc)
        return rel

    def appraise_all(self, n=13, skip=(), **kw):
        for i in range(n):
            if sid(i) not in skip:
                self.appraisal(sid(i), **kw)

    def audit(self, **kw):
        return A.project_audit(self.root, now=NOW, **kw)


def run_numbers(desc):
    res = desc["_results"]
    return {"k": desc["k"], "participants": res["totals"]["participants"],
            "display_text": desc["primary"]["display_text"], "bt": res["back_transformed"]["estimate_ci"]}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pj = Proj(self.tmp)

    def assertConforms(self, rep):
        self.assertEqual(contract_errors(json.loads(A.to_json(rep)), "ma.project-audit"), [])


# ================================================================== szabály-metaadat és szerződés
class TestMetadata(_Base):
    def test_all_x_rules_present(self):
        want = {"X%03d" % i for i in range(1, 23)}
        self.assertEqual(set(A.RULES), want)
        self.assertEqual(set(A.RULE_STAGES), want)
        self.assertEqual(set(A.KB_REFS), want)
        self.assertEqual(set(A.TITLES_EN), want)
        self.assertEqual(MVP_RULES | V1_RULES, want)
        for code, (sev, title, advice, src) in A.RULES.items():
            self.assertIn(sev, ("error", "warning", "info"))
            self.assertTrue(title and advice and src, code)
            self.assertRegex(A.RULE_STAGES[code], r"^S(0\d|1[0-4])$")
            self.assertNotEqual(A.TITLES_EN[code], title)
        # a 6.4 táblázat súlyosságai
        self.assertEqual({c: A.RULES[c][0] for c in V1_RULES},
                         {"X002": "warning", "X004": "error", "X007": "error", "X008": "error", "X009": "error",
                          "X011": "error", "X012": "warning", "X015": "warning", "X017": "error", "X018": "warning",
                          "X019": "error", "X020": "warning", "X021": "warning"})
        self.assertEqual(A.ESCALATION, {"X001": "S08", "X004": "S13", "X019": "S13"})
        self.assertEqual(A.GATE_STAGES, {"X009": "S08"})
        # az S12-es KB-szabályok köre változatlan (a KB / facade ezt rögzíti)
        self.assertEqual({c for c, s in A.RULE_STAGES.items() if s == "S12"}, {"X005", "X006", "X016"})
        table = {r["code"]: r for r in A.rules_table()}
        self.assertEqual(table["X004"]["escalation"], {"from": "S13", "before": "warning"})
        self.assertEqual(table["X019"]["escalation"], {"from": "S13", "before": "warning"})

    def test_kb_refs_exist_in_knowledge_base(self):
        db = os.path.join(ROOT, "tudasbazis", "tudasbazis.sqlite")
        if not os.path.isfile(db):
            self.skipTest("nincs tudásbázis")
        con = sqlite3.connect(db)
        try:
            known = {r[0] for r in con.execute("SELECT rule_id FROM decision_rule")}
        finally:
            con.close()
        from metaelemzes import validate
        known |= set(PR.RULES) | set(validate.RULES)
        for code in V1_RULES:
            self.assertTrue(A.KB_REFS[code], code)
            for r in A.KB_REFS[code]:
                self.assertIn(r, known, "%s: ismeretlen KB-azonosító %s" % (code, r))

    def test_severity_escalation(self):
        self.assertEqual(A.severity_for("X004", "S12"), "warning")
        self.assertEqual(A.severity_for("X004", "S13"), "error")
        self.assertEqual(A.severity_for("X019", "S06"), "warning")
        self.assertEqual(A.severity_for("X019", "FINAL"), "error")
        self.assertEqual(A.severity_for("X009", "S02"), "error")
        self.assertEqual(A.severity_for("X004", None), "error")

    def test_clean_v1_project(self):
        d = self.pj.ready(appraisal_tools=["rob2"])
        self.pj.appraise_all()
        rep = self.pj.audit()
        self.assertEqual(rep["findings"], [], json.dumps(rep["findings"], ensure_ascii=False, indent=1))
        self.assertEqual(rep["rules_checked"], sorted(A.RULES))
        self.assertConforms(rep)
        # minden hiányzó opcionális fájl projektszintű not_checked (outcome: null), okkal
        for code in ("X007", "X008", "X009", "X002", "X018", "X019", "X020", "X021", "X012"):
            self.assertTrue(skipped(rep, code, None), code)
        self.assertFalse([n for n in rep["not_checked"] if n["outcome"] == "o1" and n["code"] in V1_RULES],
                         rep["not_checked"])
        self.assertEqual(rep["amstar2_hints"]["9"]["suggested"], "yes")
        self.assertIn("13/13", rep["amstar2_hints"]["9"]["evidence"][0])
        self.assertTrue(d["run_id"])

    def test_report_is_deterministic_strict_json(self):
        self.pj.ready(appraisal_tools=["rob2"])
        self.pj.appraise_all(skip=("S02",))
        a, b = self.pj.audit(), self.pj.audit()
        self.assertEqual(A.to_json(a), A.to_json(b))
        json.dumps(a, allow_nan=False)
        for f in a["findings"]:
            self.assertEqual(f["title"], A.RULES[f["code"]][1])
            self.assertEqual(f["stage"], A.RULE_STAGES[f["code"]])
            self.assertEqual(f["kb_refs"], list(A.KB_REFS[f["code"]]))
            for p in f["artifacts"]:
                self.assertFalse(os.path.isabs(p) or "\\" in p, p)
        self.assertConforms(a)


# ================================================================== X002 / X018 (ábrák)
class TestFigures(_Base):
    def figure(self, stem, plot_rel=None, run_id=None, kind="forest", plot_sha=None, **kw):
        doc = {"schema": "szk.figure-result/v1", "kind": kind, "stem": stem, "ff_version": "0.3.0",
               "formats": {"svg": "06_kezirat/abrak/%s.svg" % stem}, "dpi": 600, "clean": True,
               "labels_checked": 64, "residual_violations": [], "glyphs": {"ok": True, "missing": [], "font": "Arial"},
               "numbers": {"ok": True, "checked": 31, "mismatches": []},
               "source": {"plot_sha256": plot_sha or (self.pj.sha(plot_rel) if plot_rel else None), "run_id": run_id}}
        doc.update(kw)
        self.pj.write("06_kezirat/abrak/%s.svg" % stem, "<svg/>")
        self.pj.write("06_kezirat/abrak/%s.result.json" % stem, doc)

    def test_x002_stale_figure_and_redraw(self):
        pj = self.pj
        d1 = pj.ready()
        self.figure("fig2_forest", d1["_dir"] + "/plot_data.json", d1["run_id"])
        rep = pj.audit()
        self.assertEqual(codes(rep, "X002") + codes(rep, "X018"), [])
        self.assertFalse(skipped(rep, "X002"))
        # a tábla változik, ugyanarra a specre új commit-futás → az ábra elavult
        rows = bcg_rows()
        rows[2]["e2"] = 12
        pj.csv(rows)
        d2 = pj.engine(minutes=5)
        x = codes(pj.audit(), "X002")
        self.assertEqual(len(x), 1, x)
        self.assertEqual((x[0]["outcome"], x[0]["run_id"], x[0]["severity"]), ("o1", d2["run_id"], "warning"))
        self.assertIn("06_kezirat/abrak/fig2_forest.result.json", x[0]["artifacts"])
        self.assertIn(d1["run_id"], x[0]["detail"])
        self.assertConforms(pj.audit())
        # újrarajzolás a legutóbbi futásból
        self.figure("fig2_forest", d2["_dir"] + "/plot_data.json", d2["run_id"])
        self.assertEqual(codes(pj.audit(), "X002"), [])

    def test_x002_unknown_plot_and_non_plot_kinds(self):
        pj = self.pj
        pj.ready()
        self.figure("fig9", plot_sha="ab" * 32, run_id=None)
        x = codes(pj.audit(), "X002")
        self.assertEqual(len(x), 1)
        self.assertIn("egyik commit-futás", x[0]["detail"])
        os.remove(pj.p("06_kezirat/abrak/fig9.result.json"))
        # folyamatábra: nem plot_data.json-ból készül → sem találat, sem not_checked
        self.figure("fig1_prisma", kind="flowchart", plot_sha=None)
        os.remove(pj.p("06_kezirat/abrak/fig9.svg"))
        rep = pj.audit()
        self.assertEqual(codes(rep, "X002"), [])
        self.assertFalse(skipped(rep, "X002"))

    def test_figure_without_result_json_not_checked(self):
        pj = self.pj
        pj.ready()
        pj.write("06_kezirat/abrak/fig3_funnel.svg", "<svg/>")
        pj.write("06_kezirat/abrak/fig3_funnel.overlay.png", b"\x89PNG")
        rep = pj.audit()
        for code in ("X002", "X018"):
            n = skipped(rep, code, None)
            self.assertEqual(len(n), 1, rep["not_checked"])
            self.assertIn("fig3_funnel", n[0]["reason"])

    def test_x018_qc_and_number_fidelity(self):
        pj = self.pj
        d = pj.ready()
        self.figure("fig2_forest", d["_dir"] + "/plot_data.json", d["run_id"], clean=False,
                    residual_violations=[{"label": "Aronson 1948", "kind": "overlap"}])
        x = codes(pj.audit(), "X018")
        self.assertEqual(len(x), 1)
        self.assertEqual((x[0]["severity"], x[0]["outcome"]), ("warning", "o1"))
        self.assertIn("QC nem tiszta", x[0]["detail"])
        self.figure("fig2_forest", d["_dir"] + "/plot_data.json", d["run_id"],
                    numbers={"ok": False, "checked": 31, "mismatches": [{"expected": "0.49", "found": "0.48"}]},
                    glyphs={"ok": False, "missing": ["−"], "font": "X"})
        x = codes(pj.audit(), "X018")
        self.assertIn("számhűség: 1 eltérés (31 ellenőrzött szám)", x[0]["detail"])
        self.assertIn("hiányzó karakter", x[0]["detail"])
        self.figure("fig2_forest", d["_dir"] + "/plot_data.json", d["run_id"], server_check={"ok": False})
        self.assertIn("szerver", codes(pj.audit(), "X018")[0]["detail"])
        self.figure("fig2_forest", d["_dir"] + "/plot_data.json", d["run_id"])
        self.assertEqual(codes(pj.audit(), "X018"), [])


# ================================================================== X004
class TestX004(_Base):
    def test_missing_appraisals_escalate_and_clear(self):
        pj = self.pj
        pj.ready(appraisal_tools=["rob2"])
        pj.appraise_all(skip=("S02", "S05"))
        x = codes(pj.audit(), "X004")
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]["studies"], ["S02", "S05"])
        self.assertEqual(x[0]["row_uids"], [uid(1), uid(4)])
        self.assertIn("2/13", x[0]["detail"])
        self.assertEqual(x[0]["severity"], "error")          # ismeretlen szakasz: a szigorúbb
        self.assertEqual(codes(pj.audit(stage="S12"), "X004")[0]["severity"], "warning")
        self.assertEqual(codes(pj.audit(stage="S13"), "X004")[0]["severity"], "error")
        self.assertConforms(pj.audit())
        pj.appraisal("S02")
        pj.appraisal("S05", status="consensus", assessor="consensus")
        self.assertEqual(codes(pj.audit(), "X004"), [])

    def test_drafts_and_ai_drafts_do_not_count(self):
        pj = self.pj
        pj.ready(appraisal_tools=["rob2"])
        pj.appraise_all(skip=("S02", "S03"))
        pj.appraisal("S02", status="draft")
        pj.appraisal("S03", status="complete", origin="ai_draft", assessor="ai")
        x = codes(pj.audit(), "X004")
        self.assertEqual(x[0]["studies"], ["S02", "S03"])
        self.assertIn("csak vázlat", x[0]["detail"])
        pj.appraisal("S03", status="complete", origin="ai_draft", assessor="ai", approved_by="SzK")
        self.assertEqual(codes(pj.audit(), "X004")[0]["studies"], ["S02"])

    def test_other_outcome_or_tool_does_not_count(self):
        pj = self.pj
        pj.ready(appraisal_tools=["rob2", "amstar2"])
        pj.appraise_all(skip=("S04",))
        pj.appraisal("S04", outcome="o2")
        pj.appraisal("S04", tool="nos", outcome="o1")
        self.assertEqual(codes(pj.audit(), "X004")[0]["studies"], ["S04"])
        pj.appraisal("S04", outcome=None)          # kimenet nélküli értékelés minden kimenetre érvényes
        self.assertEqual(codes(pj.audit(), "X004"), [])

    def test_rows_excluded_by_primary_run_need_no_appraisal(self):
        pj = self.pj
        pj.meta(appraisal_tools=["rob2"])
        pj.csv()
        pj.studies()
        pj.spec(filters={"exclude": ["study_id=S13"], "include": []})
        pj.engine()
        pj.appraise_all(n=12)
        self.assertEqual(codes(pj.audit(), "X004"), [])

    def test_design_routes_the_tool(self):
        from metaelemzes import appraisal as AP
        if not callable(getattr(AP, "instrument_route", None)):
            self.skipTest("nincs appraisal.instrument_route")
        pj = self.pj
        pj.meta(appraisal_tools=["rob2", "robins-i"])
        pj.csv()
        pj.studies(designs={"S01": "nrsi"})
        pj.spec()
        pj.engine()
        pj.appraise_all()
        x = codes(pj.audit(), "X004")
        self.assertEqual(x[0]["studies"], ["S01"], "a nem randomizált vizsgálathoz ROBINS-I kell")
        pj.appraisal("S01", tool="robins-i")
        self.assertEqual(codes(pj.audit(), "X004"), [])

    def test_no_tool_declared_not_checked(self):
        pj = self.pj
        pj.ready()
        rep = pj.audit()
        self.assertEqual(codes(rep, "X004"), [])
        self.assertIn("appraisal_tools", skipped(rep, "X004", None)[0]["reason"])

    def test_tools_declared_without_any_appraisal(self):
        pj = self.pj
        pj.ready(appraisal_tools=["rob2"])
        self.assertEqual(len(codes(pj.audit(), "X004")[0]["studies"]), 13)


# ================================================================== X007 / X008 (GRADE, SoF)
class TestGradeSof(_Base):
    def grade_doc(self, d, run_summary=None, status="recorded", outcome="o1", pb=None, run_id="auto"):
        doc = {"schema": "szk.ma.grade/v1", "outcome_id": outcome, "importance": "critical",
               "run_id": d["run_id"] if run_id == "auto" else run_id, "start": "high", "start_reason": "RCT",
               "domains": {k: {"rating": "not serious", "step": 0, "rationale": "ok"} for k in
                           ("risk_of_bias", "inconsistency", "indirectness", "imprecision")},
               "upgrades": {"large_effect": False, "dose_response": False, "opposing_confounding": False},
               "certainty": "high", "status": status}
        doc["domains"]["publication_bias"] = pb or {"rating": "undetected", "step": 0, "status": "resolved",
                                                    "rationale": "a tesztek nem jeleznek"}
        if run_summary is not None:
            doc["run_summary"] = run_summary
        return self.pj.write("06_kezirat/grade/%s.grade.json" % outcome, doc)

    def test_x007_journal_row(self):
        pj = self.pj
        d = pj.ready()
        n = run_numbers(d)
        self.assertEqual(n["display_text"]["hu"], "0.49 [0.33; 0.73]")
        projekt.add_grade(pj.root, "o1", "moderate", k=13, participants=n["participants"],
                          effect="RR 0.49 (95% CI 0.33–0.73)", check_kb=False)
        rep = pj.audit()
        self.assertEqual(codes(rep, "X007"), [])
        self.assertFalse(skipped(rep, "X007"))
        projekt.add_grade(pj.root, "o1", "moderate", k=12, participants=n["participants"] - 1,
                          effect="RR 0,51 [0,34; 0,76]", check_kb=False)
        x = codes(pj.audit(), "X007")
        self.assertEqual(len(x), 1)
        self.assertEqual((x[0]["severity"], x[0]["run_id"]), ("error", d["run_id"]))
        for part in ("k: 12 ≠ 13", "résztvevők", "hatás-szöveg"):
            self.assertIn(part, x[0]["detail"])
        self.assertIn("projekt.sqlite", x[0]["artifacts"])
        # az új, helyes sor (a napló legutóbbi GRADE-je) — tizedesvesszővel is egyezik
        projekt.add_grade(pj.root, "Tbc", "moderate", k=13, participants=n["participants"],
                          effect="RR 0,49 [0,33; 0,73]", check_kb=False)
        self.assertEqual(codes(pj.audit(), "X007"), [])

    def test_x007_grade_document(self):
        pj = self.pj
        d = pj.ready()
        n = run_numbers(d)
        rs = {"k": 13, "participants": n["participants"], "measure": "RR", "display_text": n["display_text"]}
        self.grade_doc(d, rs)
        self.assertEqual(codes(pj.audit(), "X007"), [])
        self.grade_doc(d, dict(rs, k=11, display_text={"hu": "0.52 [0.35; 0.77]", "en": "0.52 [0.35; 0.77]"}))
        x = codes(pj.audit(), "X007")
        self.assertIn("06_kezirat/grade/o1.grade.json", x[0]["artifacts"])
        self.assertIn("k: 11 ≠ 13", x[0]["detail"])
        # a piszkozatot a GRADE-lap frissíti: az X007 csak a rögzítettet nézi
        self.grade_doc(d, dict(rs, k=11), status="draft")
        self.assertEqual(codes(pj.audit(), "X007"), [])
        # run_summary nélkül a hivatkozott futás számai
        self.grade_doc(d, None)
        self.assertEqual(codes(pj.audit(), "X007"), [])
        rows = bcg_rows()
        rows[0]["e1"] = 40
        pj.csv(rows)
        d2 = pj.engine(minutes=3)
        x = codes(pj.audit(), "X007")
        self.assertEqual(x[0]["run_id"], d2["run_id"])
        self.assertIn("a GRADE a(z) %s futásra hivatkozik" % d["run_id"], x[0]["detail"])

    def test_x007_not_checked(self):
        pj = self.pj
        d = pj.ready()
        self.assertIn("nincs GRADE", skipped(pj.audit(), "X007", None)[0]["reason"])
        self.grade_doc(d, {"k": 1}, status="draft")
        rep = pj.audit()
        self.assertEqual(codes(rep, "X007"), [])
        self.assertIn("piszkozat", skipped(rep, "X007", "o1")[0]["reason"])

    def test_amstar2_hints_from_grade(self):
        pj = self.pj
        d = pj.ready()
        self.grade_doc(d, None)
        hints = pj.audit()["amstar2_hints"]
        self.assertEqual(hints["13"]["suggested"], "yes")
        self.assertEqual(hints["15"]["suggested"], "yes")
        self.grade_doc(d, None, status="draft", pb={"rating": "suspected", "step": None, "status": "unresolved"})
        self.assertNotIn("15", pj.audit()["amstar2_hints"])

    def sof(self, d, absolute=True, **row_kw):
        n = run_numbers(d)
        est, lo, hi = n["bt"]
        acr = 0.1
        risk = [1000.0 * acr * x for x in (est, lo, hi)]
        row = {"outcome_id": "o1", "label": {"hu": "Tbc", "en": "TB"}, "k": 13, "participants": n["participants"],
               "studies_text": {"hu": "x", "en": "x"},
               "relative": {"measure": "RR", "estimate": est, "ci_lower": lo, "ci_upper": hi, "level": 0.95,
                            "display_text": dict(n["display_text"]), "text": {"hu": "RR", "en": "RR"}},
               "effect": None,
               "absolute": [{"label": {"hu": "magas kockázat", "en": "high risk"}, "kind": "external",
                             "assumed_risk_per_1000": 100.0, "risk_per_1000": risk,
                             "difference_per_1000": [r - 100.0 for r in risk], "text": {"hu": "x", "en": "x"}}]
               if absolute else [],
               "certainty": "moderate", "footnotes": [], "sources": {}}
        row.update(row_kw)
        self.pj.write("06_kezirat/sof/o1.sof.json", {"schema": "szk.ma.sof/v1", "outcome_id": "o1",
                                                     "run_id": d["run_id"], "measure": "RR", "rows": [row]})
        return row

    def test_x008_sof_cells(self):
        pj = self.pj
        d = pj.ready()
        row = self.sof(d)
        rep = pj.audit()
        self.assertEqual(codes(rep, "X008"), [])
        self.assertFalse(skipped(rep, "X008"))
        bad = copy.deepcopy(row["relative"])
        bad["display_text"]["hu"] = "0.48 [0.33; 0.73]"
        self.sof(d, relative=bad)
        x = codes(pj.audit(), "X008")
        self.assertEqual((len(x), x[0]["severity"]), (1, "error"))
        self.assertIn("hatás (hu)", x[0]["detail"])
        self.assertIn("06_kezirat/sof/o1.sof.json", x[0]["artifacts"])
        absolute = copy.deepcopy(row["absolute"])
        absolute[0]["difference_per_1000"][0] = -40.0
        self.sof(d, absolute=True)
        doc = read_json(pj.p("06_kezirat/sof/o1.sof.json"))
        doc["rows"][0]["absolute"] = absolute
        doc["rows"][0]["k"] = 12
        pj.write("06_kezirat/sof/o1.sof.json", doc)
        detail = codes(pj.audit(), "X008")[0]["detail"]
        self.assertIn("abszolút hatás (magas kockázat)", detail)
        self.assertIn("k: 12 ≠ 13", detail)
        self.sof(d)
        self.assertEqual(codes(pj.audit(), "X008"), [])

    def test_x008_engine_sof_is_consistent(self):
        """A motor saját SoF-ja (grade_help.sof) nem ad X008-at — a két oldal ugyanazt számolja."""
        try:
            from metaelemzes import grade_help as G
            pj = self.pj
            d = pj.ready()
            doc = G.sof(pj.p(d["_dir"]), assumed_risks=[{"label": "magas", "per_1000": 40, "source": "kohorsz"}],
                        project_dir=pj.root, outcome_id="o1")
        except Exception as exc:        # noqa: BLE001 — a párhuzamosan fejlődő modul még alakul
            self.skipTest("grade_help.sof nem használható: %s" % exc)
        pj.write("06_kezirat/sof/o1.sof.json", doc)
        self.assertEqual(codes(pj.audit(), "X008"), [])

    def test_x008_stale_after_rerun(self):
        pj = self.pj
        d = pj.ready()
        self.sof(d)
        rows = bcg_rows()
        rows[3]["e1"] = 90
        pj.csv(rows)
        d2 = pj.engine(minutes=2)
        x = codes(pj.audit(), "X008")
        self.assertEqual(x[0]["run_id"], d2["run_id"])
        self.assertIn("a SoF a(z) %s futásra hivatkozik" % d["run_id"], x[0]["detail"])


# ================================================================== X009 (kettős kinyerés)
class TestX009(_Base):
    def pair(self, b_change=None):
        pj = self.pj
        pj.ready()
        rows = bcg_rows()
        pj.csv(rows, "03_adatok/kettos/o1.A.csv")
        if b_change:
            b_change(rows)
        pj.csv(rows, "03_adatok/kettos/o1.B.csv")

    def consensus(self, decisions, key=("study_id",)):
        self.pj.write("03_adatok/kettos/o1.consensus.json", {"schema": "szk.ma.consensus/v1", "key": list(key),
                                                             "decisions": decisions})

    @staticmethod
    def decision(key, field, a, b, chosen="a"):
        return {"key": key, "field": field, "chosen": chosen, "value": a if chosen == "a" else b,
                "reason": "Table 2", "actor": "SzK", "ts": "2026-10-05T10:00:00Z", "a": a, "b": b}

    def test_with_engine_kettos(self):
        from metaelemzes import kettos
        if not callable(getattr(kettos, "outcome_status", None)):
            self.skipTest("nincs kettos.outcome_status")
        pj = self.pj

        def change(rows):
            rows[2]["e2"] = 12
            rows[4]["n1"] = "5069.0"          # csak írásmód: nem kell dönteni
        self.pair(change)
        x = codes(pj.audit(), "X009")
        self.assertEqual(len(x), 1)
        self.assertEqual((x[0]["severity"], x[0]["outcome"]), ("error", "o1"))
        self.assertIn("1 feloldatlan eltérés", x[0]["detail"])
        self.assertEqual(x[0]["suggested_command"][:3], ["ma.py", "kettos", "compare"])
        self.assertIn("03_adatok/kettos/o1.A.csv", x[0]["artifacts"])
        self.assertConforms(pj.audit())
        self.consensus([self.decision("S03", "e2", "11", "12")])
        self.assertEqual(codes(pj.audit(), "X009"), [])
        # a döntés óta az A tábla megváltozott: a döntés elavult
        rows = bcg_rows()
        rows[2]["e2"] = 10
        pj.csv(rows, "03_adatok/kettos/o1.A.csv")
        x = codes(pj.audit(), "X009")
        self.assertIn("elavult", x[0]["detail"])

    def test_fallback_compare_path(self):
        """Ha a kettos-modulnak nincs outcome_status-a, az audit a compare() eredményéből maga számol (E6 4.9)."""
        pj = self.pj
        self.pair()
        calls = []

        def compare(path_a, path_b, key=None, measure=None):
            calls.append((path_a, path_b, key, measure))
            return {"schema": "szk.ma.compare-result/v1", "key": ["study_id"], "summary": {},
                    "disagreements": [
                        {"key": "S03", "field": "e2", "a": "11", "b": "12", "kind": "value", "row_uid_a": uid(2)},
                        {"key": "S05", "field": "n1", "a": "5069", "b": "5069.0", "kind": "format_only"}],
                    "only_a": ["S99"], "only_b": []}
        fake = types.SimpleNamespace(compare=compare)
        with mock.patch.object(A, "_module", lambda name: fake if name == "kettos" else None):
            x = codes(pj.audit(), "X009")
            self.assertEqual(len(x), 1)
            self.assertIn("2 feloldatlan eltérés", x[0]["detail"])
            self.assertIn("1 sor csak az egyik táblában", x[0]["detail"])
            self.assertEqual(x[0]["cells"], [{"row_uid": uid(2), "field": "e2"}])
            self.assertTrue(os.path.isabs(calls[-1][0]) and calls[-1][0].endswith("o1.A.csv"))
            self.assertEqual(calls[-1][3], "RR")
            self.consensus([self.decision("S03", "e2", "11", "12"),
                            {"key": "S99", "field": "*", "chosen": "a", "value": None, "reason": "valódi vizsgálat",
                             "actor": "SzK", "ts": "2026-10-05T10:00:00Z"}])
            self.assertEqual(codes(pj.audit(), "X009"), [])
            self.assertEqual(calls[-1][2], ["study_id"], "a konszenzus kulcsa")
            self.consensus([self.decision("S03", "e2", "10", "12")])
            self.assertIn("elavult", codes(pj.audit(), "X009")[0]["detail"])

    def test_fallback_with_table_arguments(self):
        pj = self.pj
        self.pair()
        seen = {}

        def compare(a, b, **kw):
            seen.update(a=a, kw=kw)
            return {"key": ["study_id"], "disagreements": [], "only_a": [], "only_b": []}
        fake = types.SimpleNamespace(compare=compare)
        with mock.patch.object(A, "_module", lambda name: fake if name == "kettos" else None):
            self.assertEqual(codes(pj.audit(), "X009"), [])
        self.assertEqual(seen["a"]["header"], HEADER)
        self.assertEqual(seen["a"]["rows"][0][:3], [uid(0), sid(0), BCG[0][0]])
        self.assertEqual(seen["a"]["row_uids"][:2], [uid(0), uid(1)])
        self.assertEqual(seen["kw"], {"measure": "RR"})

    def test_missing_module_and_files_not_checked(self):
        pj = self.pj
        self.pair()
        with mock.patch.object(A, "_module", lambda name: None):
            rep = pj.audit()
        self.assertEqual(codes(rep, "X009"), [])
        self.assertIn("nem érhető el", skipped(rep, "X009", "o1")[0]["reason"])
        os.remove(pj.p("03_adatok/kettos/o1.B.csv"))
        self.assertIn("csak az egyik", skipped(pj.audit(), "X009", "o1")[0]["reason"])
        shutil.rmtree(pj.p("03_adatok/kettos"))
        self.assertIn("nincs kettős kinyerés", skipped(pj.audit(), "X009", None)[0]["reason"])


# ================================================================== X011
class TestX011(_Base):
    def test_prediction_model_review_needs_probast(self):
        pj = self.pj
        pj.ready(review_type="prediction_model")
        for i in range(12):
            pj.appraisal(sid(i), tool="probast-ai", judgement="low")
        pj.appraisal("S13", tool="probast-ai", status="complete", origin="ai_draft", assessor="ai")
        x = codes(pj.audit(), "X011")
        self.assertEqual(len(x), 1)
        self.assertEqual((x[0]["severity"], x[0]["outcome"], x[0]["studies"]), ("error", None, ["S13"]))
        self.assertIn("csak vázlat", x[0]["detail"])
        pj.appraisal("S13", tool="probast-ai", status="complete", origin="ai_draft", assessor="ai",
                     approved_by="SzK")
        self.assertEqual(codes(pj.audit(), "X011"), [])

    def test_other_review_types_unaffected(self):
        pj = self.pj
        pj.ready(review_type="intervention")
        rep = pj.audit()
        self.assertEqual(codes(rep, "X011") + skipped(rep, "X011"), [])

    def test_studies_from_tables_without_map(self):
        pj = self.pj
        pj.ready(review_type="prediction_model")
        os.remove(pj.p("03_adatok/studies.json"))
        self.assertEqual(len(codes(pj.audit(), "X011")[0]["studies"]), 13)


# ================================================================== X012 (AMSTAR 2)
class TestX012(_Base):
    def amstar(self, answers, judgement=None, status="complete"):
        return self.pj.appraisal("review", tool="amstar2", status=status, outcome=None,
                                 answers={str(k): {"value": v} for k, v in answers.items()},
                                 overall={"judgement": judgement, "rationale": "önellenőrzés"} if judgement else {})

    def test_declared_but_missing(self):
        pj = self.pj
        pj.ready(appraisal_tools=["rob2", "amstar2"])
        pj.appraise_all()
        self.assertEqual(len(codes(pj.audit(), "X012")), 1)
        rep = pj.audit(stage="S05")
        self.assertEqual(codes(rep, "X012"), [])
        self.assertIn("S14", skipped(rep, "X012", None)[0]["reason"])

    def test_incomplete_only_late(self):
        pj = self.pj
        pj.ready()
        rel = self.amstar({i: "yes" for i in range(1, 11)})
        x = codes(pj.audit(stage="FINAL"), "X012")
        self.assertIn("10/16", x[0]["detail"])
        self.assertEqual((x[0]["severity"], x[0]["artifacts"]), ("warning", [rel]))
        self.assertEqual(codes(pj.audit(stage="S06"), "X012"), [])
        self.assertTrue(skipped(pj.audit(stage="S06"), "X012"))

    def test_rating_consistency_both_conventions(self):
        from metaelemzes import audit
        if audit._amstar2_ratings({str(i): "yes" for i in range(1, 17)}, "meets") is None:
            self.skipTest("nincs AMSTAR 2-algoritmus")
        pj = self.pj
        pj.ready()
        all_yes = {i: "yes" for i in range(1, 17)}
        self.amstar(all_yes, "High")
        self.assertEqual(codes(pj.audit(stage="FINAL"), "X012"), [])
        self.amstar(all_yes, "critically low")
        x = codes(pj.audit(stage="FINAL"), "X012")
        self.assertIn("nem egyezik", x[0]["detail"])
        # „részben igen” kritikus tételen + egy nem kritikus „nem”: meets → magas, weakness → mérsékelt
        mixed = dict(all_yes)
        mixed.update({2: "partial_yes", 1: "no"})
        self.amstar(mixed, "moderate")
        self.assertEqual(codes(pj.audit(stage="FINAL"), "X012"), [], "a másik konvencióval egyeztethető")
        self.amstar(mixed, "low")
        self.assertIn("a másik konvencióval: moderate", codes(pj.audit(stage="FINAL"), "X012")[0]["detail"])

    def test_algorithm_missing_not_checked(self):
        pj = self.pj
        pj.ready()
        self.amstar({i: "yes" for i in range(1, 17)}, "low")
        with mock.patch.object(A, "_module", lambda name: None):
            rep = pj.audit(stage="FINAL")
        self.assertEqual(codes(rep, "X012"), [])
        self.assertIn("nem számolható", skipped(rep, "X012", None)[0]["reason"])


# ================================================================== X015
class TestX015(_Base):
    def test_studies_map_per_outcome(self):
        pj = self.pj
        d = pj.ready()
        rep = pj.audit()
        self.assertEqual(codes(rep, "X015"), [])
        self.assertFalse(skipped(rep, "X015"))
        pj.studies(outcomes=None)
        st = read_json(pj.p("03_adatok/studies.json"))
        for s in st["studies"][:12]:
            s["outcomes"] = ["o1"]
        st["studies"][12]["outcomes"] = ["o2"]
        pj.write("03_adatok/studies.json", st)
        x = codes(pj.audit(), "X015")
        self.assertEqual((len(x), x[0]["severity"], x[0]["run_id"]), (1, "warning", d["run_id"]))
        self.assertIn("13 vizsgálat szerepel (plot_data.json)", x[0]["detail"])
        self.assertIn("a 03_adatok/studies.json szerint 12", x[0]["detail"])

    def test_prisma_included_meta(self):
        pj = self.pj
        pj.ready()
        pj.studies(outcomes=None)
        pj.write("02_szures/prisma_flow.json", {"included_studies": 13, "included_meta": 13})
        self.assertEqual(codes(pj.audit(), "X015"), [])
        pj.write("02_szures/prisma_flow.json", {"included_studies": 13, "included_meta": 11})
        self.assertIn("a 02_szures/prisma_flow.json szerint 11", codes(pj.audit(), "X015")[0]["detail"])
        pj.write("02_szures/prisma_flow.json", {"included_studies": 13, "included_meta": {"o1": 13}})
        self.assertEqual(codes(pj.audit(), "X015"), [])

    def test_no_per_outcome_count_not_checked(self):
        pj = self.pj
        pj.ready()
        pj.studies(outcomes=None)
        self.assertTrue(skipped(pj.audit(), "X015", None))

    def test_table_fallback_without_plot(self):
        pj = self.pj
        pj.meta()
        pj.csv()
        pj.studies(n=13)
        pj.spec()
        pj.engine(plots=False)
        st = read_json(pj.p("03_adatok/studies.json"))
        st["studies"][0]["outcomes"] = []
        pj.write("03_adatok/studies.json", st)
        self.assertIn("az adattábla study_id-i", codes(pj.audit(), "X015")[0]["detail"])


# ================================================================== X017
class TestX017(_Base):
    def rob2_doc(self, unit="S01", judgement2="low", reason=None):
        from metaelemzes import instruments as I
        inst = I.load("rob2")
        ans = {}
        for it in inst.slots("assignment"):
            allowed = inst.allowed(it)
            v = "no" if it.get("polarity") == "reverse" else ("yes" if "yes" in allowed else allowed[0])
            ans[it["key"]] = {"value": v}
        ans["2.6"] = {"value": "no"}                  # a 2. domént a válaszok „high”-ra kényszerítik
        dj = [{"domain": str(i), "pass": None, "judgement": "low"} for i in range(1, 6)]
        dj[1].update(judgement=judgement2, override_reason=reason)
        overall = {"judgement": "high", "rationale": "x"}
        rel = self.pj.appraisal(unit, answers={}, domain_judgements=dj, overall=overall)
        doc = read_json(self.pj.p(rel))
        doc["answers"] = ans
        doc["scope"] = "assignment"
        self.pj.write(rel, doc)
        return rel

    def test_engine_implied_override_needs_reason(self):
        try:
            from metaelemzes import appraisal as AP, instruments as I     # noqa: F401
            I.load("rob2")
        except Exception as exc:        # noqa: BLE001
            self.skipTest("nincs értékelő modul: %s" % exc)
        pj = self.pj
        pj.ready()
        rel = self.rob2_doc()
        x = codes(pj.audit(), "X017")
        self.assertEqual(len(x), 1)
        self.assertEqual((x[0]["severity"], x[0]["outcome"], x[0]["studies"]), ("error", "o1", ["S01"]))
        self.assertIn("D2: low ≠ implikált high", x[0]["detail"])
        self.assertEqual(x[0]["artifacts"], [rel])
        self.rob2_doc(reason="a vakítás hiánya ennél a kimenetnél nem számít")
        self.assertEqual(codes(pj.audit(), "X017"), [])
        self.rob2_doc(judgement2="high")
        self.assertEqual(codes(pj.audit(), "X017"), [])

    def test_stored_implied_when_module_missing(self):
        pj = self.pj
        pj.ready()
        pj.appraisal("S02", domain_judgements=[{"domain": "3", "pass": None, "judgement": "low", "implied": "high",
                                                "override_reason": None}],
                     overall={"judgement": "low", "implied": "high", "override_reason": "egyeztetve"})
        pj.appraisal("S03", status="draft", domain_judgements=[{"domain": "3", "judgement": "low", "implied": "high"}])
        with mock.patch.object(A, "_module", lambda name: None):
            x = codes(pj.audit(), "X017")
        self.assertEqual(len(x), 1, "a vázlat nem számít, az indokolt összítélet sem")
        self.assertIn("D3: low ≠ implikált high", x[0]["detail"])
        self.assertNotIn("összítélet", x[0]["detail"])

    def test_no_final_appraisal_not_checked(self):
        pj = self.pj
        pj.ready()
        self.assertTrue(skipped(pj.audit(), "X017", None))


# ================================================================== X019
class TestX019(TestGradeSof):
    def test_unresolved_suspected_in_grade_doc(self):
        pj = self.pj
        d = pj.ready()
        self.grade_doc(d, status="draft", pb={"rating": "suspected", "step": None, "status": "unresolved",
                                              "rationale": None})
        x = codes(pj.audit(), "X019")
        self.assertEqual((len(x), x[0]["outcome"], x[0]["severity"]), (1, "o1", "error"))
        self.assertEqual(codes(pj.audit(stage="S12"), "X019")[0]["severity"], "warning")
        self.assertEqual(codes(pj.audit(stage="S13"), "X019")[0]["severity"], "error")
        self.assertEqual([f["code"] for f in A.audit_gate_errors(pj.root) if f["code"] == "X019"], ["X019"])
        self.grade_doc(d, status="draft", pb={"rating": "suspected", "step": 0, "status": "resolved",
                                              "rationale": "a regiszterkeresés teljes, a tesztek nem jeleznek"})
        self.assertEqual(codes(pj.audit(), "X019"), [])
        self.grade_doc(d, pb={"rating": "strongly suspected", "step": -1, "status": None, "rationale": None})
        self.assertEqual(codes(pj.audit(), "X019"), [])

    def test_legacy_journal_row(self):
        pj = self.pj
        pj.ready()
        con = sqlite3.connect(projekt.db_path(pj.root))
        con.execute("INSERT INTO grade (ts, outcome, certainty, publication_bias) VALUES (?,?,?,?)",
                    ("2026-10-01T00:00:00Z", "o1", "low", "gyanított: kevés vizsgálat"))
        con.commit()
        con.close()
        x = codes(pj.audit(), "X019")
        self.assertIn("projektnapló grade", x[0]["detail"])
        projekt.add_grade(pj.root, "o1", "low", publication_bias="0 gyanított (feloldva): a keresés teljes",
                          check_kb=False)
        self.assertEqual(codes(pj.audit(), "X019"), [])


# ================================================================== X020 / X021 (PRISMA)
class TestPrismaRules(_Base):
    FLOW = {"identified_databases": 120, "dedup_removed": 20, "screened": 100, "excluded_screening": 80,
            "sought_for_retrieval": 20, "not_retrieved": 2, "assessed_eligibility": 18, "excluded_eligibility": 5,
            "excluded_eligibility_reasons": {"wrong population": 3, "Wrong design": 2}, "included": 13,
            "included_studies": 13, "undecided": 0}

    def log(self, rows, name="dontesek.csv"):
        self.pj.write("02_szures/" + name, "rec_id;decision;reason;phase\n" + "".join(
            ";".join(r) + "\n" for r in rows))

    def test_x020_undecided_records(self):
        pj = self.pj
        pj.ready()
        pj.write("02_szures/prisma_flow.json", dict(self.FLOW, undecided=3))
        x = codes(pj.audit(), "X020")
        self.assertEqual((len(x), x[0]["severity"], x[0]["outcome"]), (1, "warning", None))
        self.assertIn("3 rekord", x[0]["detail"])
        self.assertEqual(len(codes(pj.audit(stage="FINAL"), "X020")), 1)
        self.assertEqual(len(codes(pj.audit(stage="S14"), "X020")), 1)
        rep = pj.audit(stage="S10")
        self.assertEqual(codes(rep, "X020"), [])
        self.assertTrue(skipped(rep, "X020", None))
        pj.write("02_szures/prisma_flow.json", self.FLOW)
        self.assertEqual(codes(pj.audit(), "X020"), [])

    def test_x021_reasons_vs_decision_log(self):
        pj = self.pj
        pj.ready()
        pj.write("02_szures/prisma_flow.json", self.FLOW)
        rows = [["r%d" % i, "exclude", "wrong population", "eligibility"] for i in range(3)] + \
               [["r%d" % i, "exclude", "wrong design", "eligibility"] for i in range(3, 5)] + \
               [["r%d" % i, "exclude", "off topic", "screen"] for i in range(5, 9)] + \
               [["r%d" % i, "include", "", "eligibility"] for i in range(9, 22)]
        self.log(rows)
        rep = pj.audit()
        self.assertEqual(codes(rep, "X021"), [])
        self.assertFalse(skipped(rep, "X021"))
        # egy rekord döntése egy későbbi naplóban: kizárás helyett bevonás → okonként eltér
        self.log([["r0", "include", "", "eligibility"]], name="dontesek_2.csv")
        x = codes(pj.audit(), "X021")
        self.assertEqual((len(x), x[0]["severity"]), (1, "warning"))
        self.assertIn("„wrong population”: folyamat 3, napló 2", x[0]["detail"])
        self.assertIn("02_szures/dontesek_2.csv", x[0]["artifacts"])
        os.remove(pj.p("02_szures/dontesek_2.csv"))
        self.assertEqual(codes(pj.audit(), "X021"), [])

    def test_amstar2_hints_from_prisma(self):
        pj = self.pj
        pj.ready()
        pj.write("02_szures/prisma_flow.json", dict(self.FLOW, databases=[{"source": "PubMed", "count_total": 80},
                                                                          {"source": "Embase", "count_total": 40}]))
        hints = pj.audit()["amstar2_hints"]
        self.assertEqual(hints["4"]["suggested"], "partial_yes")
        self.assertIn("2 adatbázis", hints["4"]["evidence"][0])
        self.assertEqual(hints["7"]["suggested"], "partial_yes")
        pj.write("02_szures/prisma_flow.json", dict(self.FLOW, excluded_eligibility_reasons={"x": 4, "": 1},
                                                    databases=[{"source": "PubMed", "count_total": 120}]))
        hints = pj.audit()["amstar2_hints"]
        self.assertEqual((hints["4"]["suggested"], hints["7"]["suggested"]), ("no", "no"))
        self.assertEqual(contract_errors(json.loads(A.to_json(pj.audit())), "ma.project-audit"), [])

    def test_x021_total_without_reasons_and_not_checked(self):
        pj = self.pj
        pj.ready()
        flow = dict(self.FLOW)
        del flow["excluded_eligibility_reasons"]
        pj.write("02_szures/prisma_flow.json", flow)
        self.assertIn("nincs szűrési döntési napló", skipped(pj.audit(), "X021", None)[0]["reason"])
        self.log([["r1", "kizárt", "rossz populáció", "teljes szöveg"]])
        self.assertIn("H = 5, a naplóban 1", codes(pj.audit(), "X021")[0]["detail"])


# ================================================================== kapuk
class TestGates(TestX009):
    def test_s08_and_final_gates(self):
        from metaelemzes import kettos
        if not callable(getattr(kettos, "outcome_status", None)):
            self.skipTest("nincs kettos.outcome_status")
        pj = self.pj

        def change(rows):
            rows[2]["e2"] = 12
        self.pair(change)
        pj.meta(appraisal_tools=["rob2"])         # + X004 (error a FINAL-ban)
        self.assertEqual(A.checkpoint_gate_errors(pj.root, "S05"), [])
        self.assertEqual([f["code"] for f in A.checkpoint_gate_errors(pj.root, "S08")], ["X009"])
        self.assertEqual([f["code"] for f in A.checkpoint_gate_errors(pj.root, "S09")], ["X009"])
        final = sorted({f["code"] for f in A.checkpoint_gate_errors(pj.root, "FINAL")})
        self.assertEqual(final, ["X004", "X009"])
        self.assertEqual(final, sorted({f["code"] for f in A.audit_gate_errors(pj.root)}))
        with self.assertRaises(ValueError) as cm:
            A.require_gate(pj.root)
        self.assertIn("X009", str(cm.exception))
        self.assertIn("X004", str(cm.exception))
        with self.assertRaises(ValueError):
            A.checkpoint_gate_errors(pj.root, "S99")
        self.consensus([self.decision("S03", "e2", "11", "12")])
        pj.appraise_all()
        self.assertEqual(A.checkpoint_gate_errors(pj.root, "FINAL"), [])
        A.require_gate(pj.root)

    def test_audit_is_read_only(self):
        """A v1-modulokon (kettos, appraisal, grade_help) át sem ír semmit a projektmappába."""
        pj = self.pj

        def change(rows):
            rows[2]["e2"] = 12
        self.pair(change)
        pj.meta(appraisal_tools=["rob2", "amstar2"])
        pj.appraise_all(skip=("S01",))
        pj.write("02_szures/prisma_flow.json", dict(TestPrismaRules.FLOW, undecided=2))
        pj.write("02_szures/dontesek.csv", "rec_id;decision;reason;phase\nr1;exclude;x;eligibility\n")

        def snapshot():
            out = {}
            for d, _dirs, files in os.walk(pj.root):
                for f in files:
                    p = os.path.join(d, f)
                    with open(p, "rb") as fh:
                        out[os.path.relpath(p, pj.root)] = hashlib.sha256(fh.read()).hexdigest()
            return out
        before = snapshot()
        rep = pj.audit(stage="FINAL")
        A.checkpoint_gate_errors(pj.root, "S08")
        self.assertTrue(codes(rep, "X004") and codes(rep, "X020") and codes(rep, "X021"))
        self.assertEqual(snapshot(), before)

    def test_warnings_do_not_block(self):
        pj = self.pj
        pj.ready()
        pj.write("02_szures/prisma_flow.json", dict(TestPrismaRules.FLOW, undecided=4))
        self.assertTrue(codes(pj.audit(stage="FINAL"), "X020"))
        self.assertEqual(A.audit_gate_errors(pj.root), [])

    def test_cli_main_json(self):
        pj = self.pj
        pj.ready(appraisal_tools=["rob2"])
        out = io.StringIO()
        rc = A.cli_main(pj.root, as_json=True, out=out)
        rep = json.loads(out.getvalue())
        self.assertEqual(rc, 1)
        self.assertEqual([f["code"] for f in rep["findings"]], ["X004"])
        out = io.StringIO()
        A.cli_main(pj.root, out=out)
        self.assertIn("[X004]", out.getvalue())


# ================================================================== E9: vizsgálat-térkép és PRISMA-ellenőrzés
STUDIES = {"schema": "szk.ma.studies/v1", "studies": [
    {"study_id": "NCT0456", "label": "Smith 2010", "outcomes": ["o1", "o2"],
     "reports": [{"rec_id": "pmid:1", "role": "primary"}, {"rec_id": "pmid:2", "role": "secondary"}]},
    {"study_id": "NCT0789", "outcomes": ["o1"], "reports": [{"rec_id": "pmid:3"}, {"rec_id": "pmid:2"}]},
    {"study_id": "NCT0999", "outcomes": [], "reports": []},
    {"study_id": "NCT0456", "reports": [{"rec_id": "pmid:9"}]}]}
FLOW_4_13 = {"schema": "szk.prisma-flow/v1", "project": "glp1", "generated": "2026-10-04T20:12:00Z",
             "composer_version": "1.5.0", "identified_databases": 1234, "identified_registers": 12,
             "identified_other": 0, "dedup_removed": 300, "automation_removed": 0, "removed_before_screening_n": 0,
             "screened": 946, "excluded_screening": 860, "sought_for_retrieval": 86, "not_retrieved": 4,
             "assessed_eligibility": 82, "excluded_eligibility": 57,
             "excluded_eligibility_reasons": {"wrong population": 30, "wrong comparator": 15, "wrong outcome": 12},
             "included": 25, "included_reports": 25, "included_studies": 18, "undecided": 0, "other_methods": None,
             "databases": [{"source": "PubMed", "count_total": 800}, {"source": "Embase", "count_total": 434}]}


class TestStudiesMap(unittest.TestCase):
    def test_counts(self):
        c = PR.studies_counts(STUDIES)
        self.assertEqual((c["I"], c["J"]), (3, 4))
        self.assertEqual(c["study_ids"], ["NCT0456", "NCT0789", "NCT0999"])
        self.assertEqual(c["report_ids"], ["pmid:1", "pmid:2", "pmid:3", "pmid:9"])
        self.assertEqual(c["per_outcome"], {"o1": 2, "o2": 1})
        self.assertEqual((c["duplicates"], c["without_reports"]), (["NCT0456"], ["NCT0999"]))
        self.assertIsNone(PR.studies_counts({"studies": [{"study_id": "a"}]})["J"])
        self.assertIsNone(PR.studies_counts({"studies": [{"study_id": "a"}]})["per_outcome"])
        with self.assertRaises(PR.PrismaError):
            PR.studies_counts({"x": 1})

    def test_apply_studies_fills_and_reports_p017(self):
        flow, extra, counts = PR.apply_studies({"included_reports": 4}, STUDIES)
        self.assertEqual((flow["included_studies"], extra), (3, []))
        flow, extra, _ = PR.apply_studies(FLOW_4_13, STUDIES)
        self.assertEqual(sorted(f["fields"][0] for f in extra), ["included_reports", "included_studies"])
        self.assertEqual(extra[0]["detail"], "Bevont vizsgálatok (I): flow = 18, studies.json = 3")
        res = PR.check_with_studies(FLOW_4_13, STUDIES)
        self.assertEqual([f["code"] for f in res.findings[:2]], ["P017", "P017"])
        self.assertFalse(res.ok)
        self.assertEqual(res.studies["I"], 3)
        self.assertIsNone(PR.check_with_studies(FLOW_4_13).studies)

    def test_parity_with_facade(self):
        try:
            from metaelemzes import api
        except Exception as exc:        # noqa: BLE001
            self.skipTest("nincs api: %s" % exc)
        for flow in (FLOW_4_13, {"dedup_removed": "1", "included_reports": "15", "included_studies": "13"},
                     {"included_reports": "x", "included_studies": ""}):
            want = api.prisma_check(flow, studies=STUDIES)
            got = json.loads(json.dumps(PR.check_with_studies(flow, STUDIES).to_dict()))
            self.assertEqual(got["findings"], want["findings"])
            self.assertEqual(got["counts"], want["counts"])

    def test_undecided_and_reason_helpers(self):
        self.assertEqual(PR.undecided_count({"undecided": 3}), 3)
        self.assertEqual(PR.undecided_count({"awaiting": "2"}), 2)
        self.assertIsNone(PR.undecided_count({"undecided": "x"}))
        self.assertIsNone(PR.undecided_count({}))
        self.assertEqual(PR.reason_key(" Wrong   Population. "), "wrong population")
        self.assertEqual(PR.reason_key("Rossz populáció"), "rossz populacio")
        self.assertEqual(PR.reason_key(""), PR.NO_REASON)
        self.assertEqual(PR.reason_key("no reason"), PR.NO_REASON)
        rc = PR.reason_counts([{"reason": "A", "count": 2}, {"reason": "a", "count": 1}, {"reason": "", "count": 1}])
        self.assertEqual(dict(rc), {"a": ["A", 3], PR.NO_REASON: [PR.NO_REASON, 1]})

    def test_decision_log(self):
        self.assertIsNone(PR.decision_log_reasons(["pmid", "cim"], [["1", "x"]]))
        out = PR.decision_log_reasons(["PMID", "Döntés", "Kizárási ok", "Fázis"], [
            ["1", "Kizárt", "Rossz populáció", "Teljes szöveg"], ["2", "exclude", "", "eligibility"],
            ["3", "exclude", "x", ""], ["4", "include", "", "eligibility"], ["1", "Excluded", "wrong design",
                                                                          "full-text"]])
        self.assertEqual(out["rows"], 2)
        self.assertEqual(out["records"], 4)
        self.assertEqual(dict(out["reasons"]), {"wrong design": ["wrong design", 1], PR.NO_REASON: [PR.NO_REASON, 1]})


# ================================================================== E9: PRISMA 2020 folyamatábra
def _boxes_overlap(a, b):
    return (abs(a["x"] - b["x"]) * 2 < a["w"] + b["w"]) and (abs(a["y"] - b["y"]) * 2 < a["h"] + b["h"])


class TestFlowchart(unittest.TestCase):
    def check_geometry(self, spec):
        ids = [n["id"] for n in spec["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))
        for e in spec["edges"]:
            self.assertIn(e["from"], ids)
            self.assertIn(e["to"], ids)
        for n in spec["nodes"]:
            self.assertGreaterEqual(n["x"] - n["w"] / 2, -1e-9, n["id"])
            self.assertLessEqual(n["x"] + n["w"] / 2, 100 + 1e-9, n["id"])
            self.assertGreaterEqual(n["y"] - n["h"] / 2, -1e-9, n["id"])
            self.assertLessEqual(n["y"] + n["h"] / 2, 100 + 1e-9, n["id"])
        nodes = spec["nodes"]
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                self.assertFalse(_boxes_overlap(a, b), "%s ↔ %s" % (a["id"], b["id"]))
        self.assertEqual(contract_errors(spec, "ff.flowchart"), [])

    def text(self, spec, nid):
        return next(n["text"] for n in spec["nodes"] if n["id"] == nid)

    def flat(self, spec, nid):
        """A felirat sortörés nélkül (a keskeny dobozokban a motor tördel)."""
        return self.text(spec, nid).replace("\n", " ")

    def test_full_prisma_2020_from_composer_flow(self):
        spec = PR.flowchart(FLOW_4_13)
        self.check_geometry(spec)
        self.assertEqual((spec["schema"], spec["kind"], spec["direction"]), ("szk.ff.flowchart/v1", "prisma2020",
                                                                              "TB"))
        inc = self.text(spec, "included")
        self.assertIn("Studies included in review (n = 18)", inc)
        self.assertIn("Reports of included studies (n = 25)", inc)
        ident = self.text(spec, "identified")
        for part in ("Databases (n = 1234)", "Registers (n = 12)", "PubMed (n = 800)", "Embase (n = 434)"):
            self.assertIn(part, ident)
        self.assertIn("Duplicate records removed (n = 300)", self.text(spec, "removed"))
        self.assertIn("Records screened (n = 946)", self.text(spec, "screened"))
        self.assertIn("Reports sought for retrieval (n = 86)", self.text(spec, "sought"))
        self.assertIn("Reports not retrieved (n = 4)", self.text(spec, "not_retrieved"))
        self.assertIn("Reports assessed for eligibility (n = 82)", self.text(spec, "assessed"))
        excl = self.text(spec, "excluded_eligibility")
        self.assertTrue(excl.startswith("Reports excluded (n = 57):"))
        self.assertIn("wrong population (n = 30)", excl)
        self.assertEqual(spec["counts"]["I"], 18)
        self.assertEqual(spec["counts"]["J"], 25)
        self.assertTrue(spec["check"]["ok"])
        self.assertEqual(spec["layout"]["other_methods"], False)
        self.assertEqual({(e["from"], e["to"]) for e in spec["edges"]} >= {
            ("identified", "removed"), ("identified", "screened"), ("assessed", "included")}, True)
        # a dobozok fentről lefelé
        ys = [next(n["y"] for n in spec["nodes"] if n["id"] == k) for k in
              ("hdr_db", "identified", "screened", "sought", "assessed", "included")]
        self.assertEqual(ys, sorted(ys, reverse=True))

    def test_studies_map_sets_i(self):
        spec = PR.flowchart({k: v for k, v in FLOW_4_13.items() if k != "included_studies"}, studies=STUDIES)
        self.assertIn("Studies included in review (n = 3)", self.text(spec, "included"))
        self.assertTrue(spec["source"]["studies_map"])
        conflict = PR.flowchart(FLOW_4_13, studies=STUDIES)
        self.assertIn("P017", conflict["check"]["codes"])
        self.assertFalse(conflict["check"]["ok"])
        self.assertIn("(n = 3)", self.text(conflict, "included"), "az I forrása a vizsgálat-térkép")

    def test_other_methods_and_updated_review(self):
        flow = dict(FLOW_4_13, other_methods_identified=20, other_methods_sought=10, other_methods_not_retrieved=1,
                    other_methods_assessed=9, other_methods_excluded=2,
                    other_methods_excluded_reasons={"no outcome data": 2}, included=32, included_reports=32,
                    previous_studies=5, previous_reports=6, total_studies=23, total_reports=38)
        spec = PR.flowchart(flow)
        self.check_geometry(spec)
        ids = {n["id"] for n in spec["nodes"]}
        self.assertTrue({"hdr_om", "om_identified", "om_sought", "om_not_retrieved", "om_assessed", "om_excluded",
                         "previous", "total"} <= ids)
        self.assertIn({"from": "om_assessed", "to": "included"}, spec["edges"])
        self.assertIn({"from": "previous", "to": "total"}, spec["edges"])
        self.assertIn("no outcome data (n = 2)", self.flat(spec, "om_excluded"))
        self.assertIn("Total studies included in review (n = 23)", self.flat(spec, "total"))
        self.assertFalse(spec["notes"], "a tipikus frissített, egyéb ágas ábra elfér")
        self.assertEqual((spec["layout"]["width"], spec["layout"]["arrangement"]), ("double", "full"))
        self.assertTrue(spec["check"]["ok"], spec["check"])

    def test_missing_values_language_and_many_reasons(self):
        reasons = {"ok %02d" % i: 1 for i in range(14)}
        spec = PR.flowchart({"identified_databases": 50, "excluded_eligibility": 14,
                             "excluded_eligibility_reasons": reasons}, lang="hu")
        self.check_geometry(spec)
        self.assertIn("Bevont vizsgálatok (n = ?)", self.text(spec, "included"))
        self.assertIn("A bevont vizsgálatok jelentései (n = ?)", self.text(spec, "included"))
        self.assertTrue(any("„n = ?”" in n["hu"] for n in spec["notes"]))
        excl = self.text(spec, "excluded_eligibility")
        self.assertIn("Egyéb okok (n = 5)", excl)
        self.assertEqual(excl.count("\n"), 10)
        self.assertTrue(any("leggyakoribb" in n["hu"] for n in spec["notes"]))
        # sok hosszú ok a keskeny (egyéb ágas) elrendezésben: az okok listája rövidül, a geometria érvényes marad
        long_reasons = {"reason %02d: a long description of why the report was excluded" % i: 2 for i in range(30)}
        crowded = PR.flowchart(dict(FLOW_4_13, excluded_eligibility=60, excluded_eligibility_reasons=long_reasons,
                                    other_methods_identified=5, other_methods_sought=5, other_methods_assessed=5,
                                    other_methods_excluded=0, included=30))
        self.check_geometry(crowded)
        self.assertTrue(any("leggyakoribb" in n["en"] or "most frequent" in n["en"] for n in crowded["notes"]))
        no_reason = PR.flowchart(dict(FLOW_4_13, excluded_eligibility_reasons={"x": 55, "ok nélkül": 2}))
        self.assertIn("No reason recorded (n = 2)", self.text(no_reason, "excluded_eligibility"))
        with self.assertRaises(PR.PrismaError):
            PR.flowchart(FLOW_4_13, lang="de")

    def test_write_and_examples(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        spec = PR.flowchart(FLOW_4_13)
        path = PR.write_flowchart(spec, os.path.join(tmp, "sub", "prisma.flowchart.json"))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), json.loads(json.dumps(spec)))
        self.assertEqual([f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")], [])
        for fn in sorted(os.listdir(EXAMPLES)):
            if not fn.startswith("ff.flowchart.v1."):
                continue
            with open(os.path.join(EXAMPLES, fn), encoding="utf-8") as fh:
                doc = json.load(fh)
            with self.subTest(example=fn):
                errs = contract_errors(doc, "ff.flowchart")
                self.assertEqual(bool(errs), ".invalid-" in fn, errs)
        # a motor mai kimenete egyezik a szerződés-példával (a verziószám nélkül)
        with open(os.path.join(EXAMPLES, "ff.flowchart.v1.prisma.json"), encoding="utf-8") as fh:
            ex = json.load(fh)
        now = json.loads(json.dumps(PR.flowchart({k: v for k, v in FLOW_4_13.items() if k != "databases"})))
        for d in (ex, now):
            d["source"].pop("engine_version", None)
        self.assertEqual(now, ex)


if __name__ == "__main__":
    unittest.main()
