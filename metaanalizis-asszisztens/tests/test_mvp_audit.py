# -*- coding: utf-8 -*-
"""E8 (alap): projekt-audit — X001, X003, X005, X006, X010, X013, X014, X016, X022 (terv 6.4, 4.15).

Valósághű ideiglenes projektmappák (terv 2.4: ma-projekt.json, projekt.sqlite, 03_adatok/<kimenet>.csv +
.prov.json, studies.json, appraisals, specs, 05_elemzes/<kimenet>/<run_id>/run.json + results.json): minden
szabályt kiváltunk, majd a javítással eltüntetünk; a hiányzó / hibás opcionális fájl nem hiba, hanem
'not_checked' tétel. A szerződés: szk.ma.project-audit/v1, szigorú JSON (NaN/Infinity nélkül).
"""
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest

from _helpers import ROOT
from metaelemzes import audit as A
from metaelemzes import projekt, prisma, validate

NOW = "2026-10-05T12:00:00Z"
BCG = [("Aronson 1948", 4, 123, 11, 139), ("Ferguson & Simes 1949", 6, 306, 29, 303),
       ("Rosenthal et al 1960", 3, 231, 11, 220), ("Hart & Sutherland 1977", 62, 13598, 248, 12867),
       ("Frimodt-Moller et al 1973", 33, 5069, 47, 5808), ("Stein & Aronson 1953", 180, 1541, 372, 1451),
       ("Vandiviere et al 1973", 8, 2545, 10, 629), ("TPT Madras 1980", 505, 88391, 499, 88391),
       ("Coetzee & Berjak 1968", 29, 7499, 45, 7277), ("Rosenthal et al 1961", 17, 1716, 65, 1665),
       ("Comstock et al 1974", 186, 50634, 141, 27338), ("Comstock & Webster 1969", 5, 2498, 3, 2341),
       ("Comstock et al 1976", 27, 16913, 29, 17854)]
HEADER = ["row_uid", "study_id", "study", "e1", "n1", "e2", "n2", "rob", "estimated", "forras_oldal"]
FIELDS = ("e1", "n1", "e2", "n2")


def uid(i):
    return "rbcg%02d" % (i + 1)


def sid(i):
    return "S%02d" % (i + 1)


def bcg_rows():
    return [{"row_uid": uid(i), "study_id": sid(i), "study": lab, "e1": e1, "n1": n1, "e2": e2, "n2": n2,
             "rob": "low", "estimated": "nem", "forras_oldal": "p. %d" % (i + 3)}
            for i, (lab, e1, n1, e2, n2) in enumerate(BCG)]


def codes(rep, code=None, outcome=None):
    return [f for f in rep["findings"] if (code is None or f["code"] == code)
            and (outcome is None or f["outcome"] == outcome)]


def skipped(rep, code):
    return [n for n in rep["not_checked"] if n["code"] == code]


class Proj(object):
    """Valósághű projektmappa-építő (egy kimenet: o1, RR, BCG-adat)."""

    def __init__(self, base, name="glp1", journal=True):
        self.root = os.path.join(base, name)
        if journal:
            projekt.init(self.root, "GLP-1 teszt", "PICO?")
        else:
            os.makedirs(self.root)
        self._t = 0

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
        m = {"title": "GLP-1 teszt", "data_class": "A", "review_type": "intervention",
             "outcomes": outcomes if outcomes is not None else [
                 {"id": "o1", "name": "Halálozás", "data": "03_adatok/o1.csv", "measure": "RR",
                  "primary_spec": "05_elemzes/specs/o1_primary.json"}]}
        m.update(kw)
        projekt.save_project_meta(self.root, m)

    def csv(self, rows=None, rel="03_adatok/o1.csv", header=None):
        header = header or HEADER
        rows = rows if rows is not None else bcg_rows()
        lines = [";".join(header)] + [";".join("" if r.get(h) is None else str(r.get(h)) for h in header)
                                      for r in rows]
        self.write(rel, "\n".join(lines) + "\n")
        return rel

    def prov(self, rows=None, rel="03_adatok/o1.prov.json", table="03_adatok/o1.csv", table_sha=None, cells=None,
             **cell_kw):
        rows = rows if rows is not None else bcg_rows()
        if cells is None:
            cells = []
            for i, r in enumerate(rows):
                for f in FIELDS:
                    c = {"row_uid": r["row_uid"], "field": f, "value_as_entered": str(r[f]), "method": "reported",
                         "estimated": False, "source": {"doc": "pmid:%d" % (100 + i), "page": i + 3,
                                                        "locator": "Table 2"}}
                    c.update(cell_kw)
                    cells.append(c)
        self.write(rel, {"schema": "szk.ma.provenance/v1", "table": table,
                         "table_sha256": table_sha or self.sha(table), "cells": cells})
        return cells

    def studies(self, n=13, labels=True):
        self.write("03_adatok/studies.json", {"schema": "szk.ma.studies/v1", "studies": [
            dict({"study_id": sid(i), "design": "rct_parallel", "outcomes": ["o1"],
                  "reports": [{"rec_id": "pmid:%d" % (100 + i), "role": "primary"}]},
                 **({"label": BCG[i][0]} if labels else {})) for i in range(n)]})

    def spec(self, name, purpose="primary", prespecified=True, options=None, filters=None, parent=None,
             outcome="o1", data="03_adatok/o1.csv"):
        doc = {"schema": "szk.ma.analysis-spec/v1", "name": name, "outcome": outcome, "purpose": purpose,
               "prespecified": prespecified, "protocol_ref": "00_protokoll/protokoll.md#elemzes", "parent": parent,
               "data": {"path": data}, "options": options or {"measure": "RR"},
               "filters": filters or {"exclude": [], "include": []}}
        if prespecified is None:
            del doc["prespecified"]
        rel = "05_elemzes/specs/%s.json" % name
        self.write(rel, doc)
        return rel, doc

    def run(self, spec_name, k=13, exclude=None, include=None, data="03_adatok/o1.csv", outcome="o1",
            spec_path="auto", data_sha=None, with_results=True):
        """Kézi run.json + results.json a szerződés szerint (motor nélkül)."""
        self._t += 1
        started = datetime.datetime(2026, 10, 4, 21, 12, 0) + datetime.timedelta(minutes=self._t)
        sha = data_sha or self.sha(data)
        rid = started.strftime("%Y%m%dT%H%M%SZ") + "-" + sha[:6]
        d = "05_elemzes/%s/%s" % (outcome, rid)
        sp = "05_elemzes/specs/%s.json" % spec_name if spec_path == "auto" else spec_path
        self.write(d + "/run.json", {
            "schema": "szk.ma.run/v1", "run_id": rid, "mode": "commit",
            "spec": {"path": sp, "sha256": None, "name": spec_name, "parent": None},
            "equivalent_argv": (["ma.py", "analyze", "--spec", sp] if sp else
                                ["ma.py", "analyze", "--data", data, "--measure", "RR"]) +
                               ["--out", d, "--project", "."],
            "engine_version": "0.1.0", "data": {"path": data, "sha256": sha, "rows": 13},
            "k": k, "primary": None, "validation_summary": {"error": 0, "warning": 0, "info": 0},
            "started": started.strftime("%Y-%m-%dT%H:%M:%SZ")})
        if with_results:
            self.write(d + "/results.json", {"options": {"measure": "RR"},
                                             "input": {"filters": {"exclude": exclude, "include": include}}})
        return rid, d

    def appraisal(self, study, judgement, assessor="SzK", status="complete", tool="rob2", outcome="o1", fname=None):
        rel = "04_torzitas_kockazat/appraisals/%s" % (fname or "%s.%s.%s.json" % (study, tool, assessor))
        self.write(rel, {"schema": "szk.appraisal/v1", "tool": tool, "scope": "assignment",
                         "target": {"study_id": study, "model": None, "outcome": outcome, "result": None,
                                    "index_test": None},
                         "assessor": assessor, "second_assessor": None, "status": status, "origin": "human",
                         "answers": {}, "domain_judgements": [],
                         "overall": {"judgement": judgement, "rationale": "…", "implied": None,
                                     "override_reason": None},
                         "created": "2026-10-01T10:00:00Z", "updated": "2026-10-02T10:00:00Z"})
        return rel

    def clean(self):
        """Tiszta projekt: minden X-szabály teljesül."""
        rows = bcg_rows()
        rows[0]["rob"] = "high"
        rows[1]["estimated"] = "igen"
        self.meta()
        self.csv(rows)
        cells = self.prov(rows)
        for c in cells:
            if c["row_uid"] == uid(1) and c["field"] == "e1":
                c.update(method="estimated", estimated=True)
        self.prov(rows, cells=cells)
        self.studies()
        self.spec("o1_primary")
        self.spec("o1_becsult_nelkul", purpose="sensitivity", prespecified=True, parent="o1_primary",
                  filters={"exclude": ["estimated=igen"], "include": []})
        self.spec("o1_magas_rob_nelkul", purpose="sensitivity", prespecified=True, parent="o1_primary",
                  filters={"exclude": ["rob=high"], "include": []})
        self.run("o1_primary", k=13)
        self.run("o1_becsult_nelkul", k=12, exclude=["estimated=igen"])
        self.run("o1_magas_rob_nelkul", k=12, exclude=["rob=high"])
        self.appraisal(sid(0), "High risk of bias", assessor="consensus")
        self.appraisal(sid(2), "low")
        return rows

    def audit(self, **kw):
        return A.project_audit(self.root, now=NOW, **kw)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pj = Proj(self.tmp)


# ------------------------------------------------------------------ szerződés és szabály-metaadat
class TestContract(_Base):
    def test_rules_metadata_complete(self):
        mvp = {"X001", "X003", "X005", "X006", "X010", "X013", "X014", "X016", "X022"}
        self.assertEqual(set(A.RULES), mvp)
        self.assertEqual(set(A.RULE_STAGES), mvp)
        self.assertEqual(set(A.KB_REFS), mvp)
        for code, (sev, title, advice, src) in A.RULES.items():
            self.assertRegex(code, r"^X\d{3}$")
            self.assertIn(sev, ("error", "warning", "info"))
            self.assertTrue(title and advice and src, code)
            self.assertRegex(A.RULE_STAGES[code], r"^S(0\d|1[0-4])$")
        # a 6.4 táblázat súlyosságai
        self.assertEqual({c: A.RULES[c][0] for c in mvp},
                         {"X001": "error", "X003": "error", "X005": "warning", "X006": "warning",
                          "X010": "warning", "X013": "error", "X014": "error", "X016": "warning", "X022": "error"})
        self.assertFalse(set(A.RULES) & set(validate.RULES) | set(A.RULES) & set(prisma.RULES))
        table = A.rules_table()
        self.assertEqual([r["code"] for r in table], sorted(mvp))
        self.assertEqual(next(r for r in table if r["code"] == "X001")["escalation"],
                         {"from": "S08", "before": "warning"})

    def test_kb_refs_exist_in_knowledge_base(self):
        db = os.path.join(ROOT, "tudasbazis", "tudasbazis.sqlite")
        if not os.path.isfile(db):
            self.skipTest("nincs tudásbázis")
        con = sqlite3.connect(db)
        try:
            known = {r[0] for r in con.execute("SELECT rule_id FROM decision_rule")}
        finally:
            con.close()
        known |= set(prisma.RULES) | set(validate.RULES)
        for code, refs in A.KB_REFS.items():
            for r in refs:
                self.assertIn(r, known, "%s: ismeretlen KB-azonosító %s" % (code, r))

    def test_empty_project_reports_not_checked(self):
        rep = self.pj.audit()
        self.assertEqual(rep["schema"], "szk.ma.project-audit/v1")
        self.assertEqual(rep["project"], "glp1")
        self.assertEqual(rep["generated"], NOW)
        self.assertEqual(rep["findings"], [])
        self.assertEqual(rep["summary"], {"error": 0, "warning": 0, "info": 0})
        self.assertTrue(rep["not_checked"])
        self.assertIn("projekt.sqlite", rep["inputs"])
        validate_schema(self, rep, A.audit_schema())

    def test_missing_project_dir(self):
        with self.assertRaises(FileNotFoundError):
            A.project_audit(os.path.join(self.tmp, "nincs"))

    def test_clean_project_has_no_findings(self):
        self.pj.clean()
        rep = self.pj.audit()
        self.assertEqual(rep["findings"], [], json.dumps(rep["findings"], ensure_ascii=False, indent=1))
        self.assertEqual(rep["outcomes"], ["o1"])
        self.assertEqual(rep["title"], "GLP-1 teszt")
        self.assertFalse([n for n in rep["not_checked"] if n["outcome"] == "o1"], rep["not_checked"])
        for rel in ("03_adatok/o1.csv", "03_adatok/o1.prov.json", "03_adatok/studies.json", "ma-projekt.json",
                    "05_elemzes/specs/o1_primary.json"):
            self.assertEqual(rep["inputs"][rel], self.pj.sha(rel))
        validate_schema(self, rep, A.audit_schema())

    def test_report_is_strict_json_and_deterministic(self):
        self.pj.clean()
        rows = bcg_rows()
        rows[0]["rob"] = "high"
        rows[1]["estimated"] = "nem"        # X013
        rows[4]["n1"] = "5070"              # X001, X022
        self.pj.csv(rows)
        a, b = self.pj.audit(), self.pj.audit()
        self.assertEqual(A.to_json(a), A.to_json(b))
        json.loads(A.to_json(a))
        json.dumps(a, allow_nan=False)
        self.assertTrue(a["findings"])
        validate_schema(self, a, A.audit_schema())
        for f in a["findings"]:
            self.assertEqual(f["title"], A.RULES[f["code"]][1])
            self.assertEqual(f["stage"], A.RULE_STAGES[f["code"]])
            self.assertEqual(f["kb_refs"], list(A.KB_REFS[f["code"]]))
            for p in f["artifacts"]:
                self.assertFalse(os.path.isabs(p) or "\\" in p, p)
        sev = [("error", "warning", "info").index(f["severity"]) for f in a["findings"]]
        self.assertEqual(sev, sorted(sev))

    def test_schema_agrees_with_contract_file(self):
        try:
            from metaelemzes import contracts as K
            theirs = K.load("ma.project-audit", 1)
        except Exception:
            self.skipTest("nincs metaelemzes/contracts/ma.project-audit.v1.schema.json")
        mine = A.audit_schema()
        self.assertEqual(theirs["$id"], mine["$id"])
        self.assertEqual(set(theirs["required"]), set(mine["required"]))
        self.assertEqual(set(theirs["properties"]), set(mine["properties"]))
        ti, mi = theirs["properties"]["findings"]["items"], mine["properties"]["findings"]["items"]
        self.assertEqual(set(ti["required"]), set(mi["required"]))
        self.assertEqual(set(ti["properties"]), set(mi["properties"]))

    def test_paths_outside_project_are_not_artifacts(self):
        outside = os.path.join(self.tmp, "kulso.csv")
        self.pj.meta()
        self.pj.csv()
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write(read_text(self.pj.p("03_adatok/o1.csv")))
        sha = hashlib.sha256(read_text(outside).encode("utf-8")).hexdigest()
        rid, d = self.pj.run("o1_primary", data=outside, data_sha=sha)
        with open(outside, "a", encoding="utf-8") as fh:
            fh.write("Új 2020;S99;Új 2020;1;10;2;10;low;nem;p. 1\n")
        rep = self.pj.audit()
        x = codes(rep, "X001")
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]["artifacts"], [d + "/run.json"])
        self.assertIn(outside, x[0]["detail"])
        self.assertFalse([k for k in rep["inputs"] if os.path.isabs(k)])
        validate_schema(self, rep, A.audit_schema())

    def test_audit_is_read_only(self):
        self.pj.clean()
        self.pj.csv(bcg_rows())
        before = snapshot(self.pj.root)
        self.pj.audit()
        A.audit_gate_errors(self.pj.root)
        self.assertEqual(snapshot(self.pj.root), before)


# ------------------------------------------------------------------ X001
class TestX001(_Base):
    def test_stale_primary_run_and_rerun_clears(self):
        self.pj.clean()
        rows = bcg_rows()
        rows[0]["rob"] = "high"
        rows[1]["estimated"] = "igen"
        rows[3]["e1"] = 63
        self.pj.csv(rows)
        self.pj.prov(rows, cells=None)    # az eredet is frissült (X022 nem jelez)
        cells = read_json(self.pj.p("03_adatok/o1.prov.json"))["cells"]
        for c in cells:
            if c["row_uid"] == uid(1) and c["field"] == "e1":
                c.update(method="estimated", estimated=True)
        self.pj.prov(rows, cells=cells)
        rep = self.pj.audit()
        x = codes(rep, "X001")
        self.assertEqual(len(x), 3)                       # a három spec legutóbbi futása mind elavult
        prim = next(f for f in x if "o1_primary" in f["detail"])
        self.assertEqual(prim["severity"], "error")
        self.assertEqual(prim["stage"], "S08")
        self.assertIn("elsődleges", prim["detail"])
        self.assertEqual(prim["suggested_command"],
                         ["ma.py", "analyze", "--spec", "05_elemzes/specs/o1_primary.json", "--project", "."])
        self.assertIn("03_adatok/o1.csv", prim["artifacts"])
        self.assertTrue(prim["artifacts"][0].endswith("/run.json"))
        self.assertRegex(prim["run_id"], r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
        self.assertEqual(rep["summary"]["error"], 3)
        # újrafuttatás: csak a legutóbbi futás számít
        for name, ex in (("o1_primary", None), ("o1_becsult_nelkul", ["estimated=igen"]),
                         ("o1_magas_rob_nelkul", ["rob=high"])):
            self.pj.run(name, k=13 if ex is None else 12, exclude=ex)
        self.assertEqual(codes(self.pj.audit(), "X001"), [])

    def test_stage_escalation(self):
        self.pj.clean()
        rows = bcg_rows()
        rows[0]["rob"] = "high"
        rows[1]["estimated"] = "igen"
        rows[3]["e1"] = 63
        self.pj.csv(rows)
        self.assertEqual({f["severity"] for f in codes(self.pj.audit(), "X001")}, {"error"})   # ismeretlen szakasz
        projekt.checkpoint(self.pj.root, "S05", "reviewer", "PASS")                            # → S06
        rep = self.pj.audit()
        self.assertEqual(rep["stage"], "S06")
        self.assertEqual({f["severity"] for f in codes(rep, "X001")}, {"warning"})
        self.assertEqual({f["severity"] for f in codes(self.pj.audit(stage="S08"), "X001")}, {"error"})
        self.assertEqual({f["severity"] for f in codes(self.pj.audit(stage="s7"), "X001")}, {"warning"})
        self.assertEqual(self.pj.audit(stage="S07-S09")["stage"], "S09")
        projekt.checkpoint(self.pj.root, "S08", "reviewer", "FAIL")
        self.assertEqual({f["severity"] for f in codes(self.pj.audit(), "X001")}, {"error"})
        self.assertEqual(A.severity_for("X001", "FINAL"), "error")
        self.assertEqual(A.severity_for("X005", "S01"), "warning")
        with self.assertRaises(ValueError):
            self.pj.audit(stage="S99")

    def test_missing_data_file_and_plain_cli_run(self):
        self.pj.meta()
        self.pj.csv()
        self.pj.studies()
        rid, d = self.pj.run("o1", spec_path=None)
        os.remove(self.pj.p("03_adatok/o1.csv"))
        rep = self.pj.audit()
        x = codes(rep, "X001")
        self.assertEqual(len(x), 1)
        self.assertIn("nem létezik", x[0]["detail"])
        # spec nélküli futás: az egyenértékű parancssor, --out nélkül (az új futás-mappa a hívó dolga)
        self.assertEqual(x[0]["suggested_command"], ["ma.py", "analyze", "--data", "03_adatok/o1.csv", "--measure",
                                                     "RR", "--project", "."])
        self.assertTrue(skipped(rep, "X005"))      # a tábla hiányzik: nem ellenőrizhető

    def test_no_runs_not_checked(self):
        self.pj.meta()
        self.pj.csv()
        rep = self.pj.audit()
        self.assertEqual(codes(rep, "X001"), [])
        self.assertTrue(skipped(rep, "X001"))

    def test_with_real_engine_run(self):
        """A motor valódi results.json / run.json kimenetével (spec.run_descriptor)."""
        from metaelemzes import pipeline, tableio
        from metaelemzes import spec as S
        pj = self.pj
        pj.meta()
        pj.csv()
        pj.studies()
        spec_rel, doc = pj.spec("o1_primary")

        def engine(spec_rel, doc, t):
            data = pj.p(doc["data"]["path"])
            rows, meta = tableio.read_table(data)
            excl, incl = S.filters_from_spec(doc)
            rep = []
            rows_f = tableio.apply_filters(rows, excl, incl, meta=meta, report=rep)
            meta["filters"] = {"exclude": excl, "include": incl}
            opts = S.options_from_spec(doc)
            opts["filter_report"] = rep
            out, es = pipeline.run(rows_f, opts, meta)
            sha = S.sha256_file(data)
            rid = S.run_id(t, sha)
            outdir = pj.p("05_elemzes/o1/" + rid)
            paths = pipeline.write_outputs(out, es, outdir, None, plots=False)
            desc = S.run_descriptor(out, mode="commit", run_id=rid, spec=doc, spec_path=pj.p(spec_rel),
                                    data_file=data, files=paths, project_root=pj.root, started=t, finished=t)
            S.write_run_json(outdir, desc)
            return desc
        t0 = datetime.datetime(2026, 10, 4, 21, 12, 0)
        desc = engine(spec_rel, doc, t0)
        self.assertEqual(desc["data"]["path"], "03_adatok/o1.csv")
        self.assertEqual(codes(pj.audit()), [])
        rows = bcg_rows()
        rows[2]["e2"] = 12
        pj.csv(rows)
        x = codes(pj.audit(), "X001")
        self.assertEqual([f["run_id"] for f in x], [desc["run_id"]])
        # érzékenységi gyermek-futás valódi szűrővel → X005 a results.json input.filters-éből teljesül
        rows[5]["estimated"] = "igen"
        pj.csv(rows)
        self.assertEqual(len(codes(pj.audit(), "X005")), 1)
        sens_rel, sens = pj.spec("o1_becsult_nelkul", purpose="sensitivity", parent="o1_primary",
                                 filters={"exclude": ["becsült=igen"], "include": []})
        engine(sens_rel, sens, t0 + datetime.timedelta(minutes=5))
        engine(spec_rel, doc, t0 + datetime.timedelta(minutes=6))
        self.assertEqual(codes(pj.audit()), [])


# ------------------------------------------------------------------ X003
class TestX003(_Base):
    def test_rob_mismatch_with_consensus_and_fix(self):
        self.pj.clean()
        self.pj.appraisal(sid(2), "Some concerns", assessor="consensus")    # a CSV-ben: low
        rep = self.pj.audit()
        x = codes(rep, "X003")
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]["severity"], "error")
        self.assertEqual(x[0]["stage"], "S06")
        self.assertEqual(x[0]["row_uids"], [uid(2)])
        self.assertIn("Rosenthal et al 1960", x[0]["detail"])
        self.assertIn("04_torzitas_kockazat/appraisals/S03.rob2.consensus.json", x[0]["artifacts"])
        rows = bcg_rows()
        rows[0]["rob"] = "high"
        rows[1]["estimated"] = "igen"
        rows[2]["rob"] = "some concerns"
        self.pj.csv(rows)
        self.pj.prov(rows, cells=read_json(self.pj.p("03_adatok/o1.prov.json"))["cells"])
        self.assertEqual(codes(self.pj.audit(), "X003"), [])

    def test_synonyms_drafts_and_unresolved_disagreement(self):
        self.pj.clean()
        self.pj.appraisal(sid(4), "Low risk of bias", assessor="KP")              # egyezik (low)
        self.pj.appraisal(sid(5), "high", status="draft")                          # vázlat: nem végső
        self.pj.appraisal(sid(6), "high", assessor="SzK")
        self.pj.appraisal(sid(6), "low", assessor="KP")                            # eltérés, nincs konszenzus
        rep = self.pj.audit()
        self.assertEqual(codes(rep, "X003"), [])
        self.assertTrue(any(sid(6) in n["reason"] for n in skipped(rep, "X003")))
        self.pj.appraisal(sid(6), "high", assessor="consensus")
        x = codes(self.pj.audit(), "X003")
        self.assertEqual([f["row_uids"] for f in x], [[uid(6)]])

    def test_label_matching_via_studies_json_and_other_outcome(self):
        rows = bcg_rows()
        for r in rows:
            del r["study_id"]
        header = [h for h in HEADER if h != "study_id"]
        self.pj.meta()
        self.pj.csv(rows, header=header)
        self.pj.studies()
        self.pj.appraisal(sid(3), "high")                                  # Hart & Sutherland 1977 → studies.json
        self.pj.appraisal(sid(4), "high", outcome="o2")                    # másik kimenet: nem érinti
        x = codes(self.pj.audit(), "X003")
        self.assertEqual([f["row_uids"] for f in x], [[uid(3)]])

    def test_non_rob_tools_ignored(self):
        self.pj.clean()
        self.pj.appraisal(sid(4), "High", tool="amstar2", assessor="consensus")     # AMSTAR 2: magas BIZALOM
        self.pj.appraisal(sid(5), "high", tool="grade", assessor="consensus")
        self.assertEqual(codes(self.pj.audit(), "X003"), [])
        self.pj.appraisal(sid(5), "high", tool="robins-i", assessor="consensus")
        self.assertEqual([f["row_uids"] for f in codes(self.pj.audit(), "X003")], [[uid(5)]])

    def test_no_appraisals_not_checked(self):
        self.pj.meta()
        self.pj.csv()
        rep = self.pj.audit()
        self.assertEqual(codes(rep, "X003"), [])
        self.assertTrue(skipped(rep, "X003"))


# ------------------------------------------------------------------ X005 / X006
class TestX005X006(_Base):
    def setUp(self):
        super(TestX005X006, self).setUp()
        rows = bcg_rows()
        rows[1]["estimated"] = "igen"
        rows[7]["estimated"] = "i"
        rows[0]["rob"] = "magas"
        rows[9]["rob"] = "Serious risk of bias"
        self.rows = rows
        self.pj.meta()
        self.pj.csv(rows)
        self.pj.studies()
        self.pj.spec("o1_primary")
        self.pj.run("o1_primary")

    def test_missing_child_runs(self):
        rep = self.pj.audit()
        x5, x6 = codes(rep, "X005"), codes(rep, "X006")
        self.assertEqual(len(x5), 1)
        self.assertEqual(len(x6), 1)
        self.assertEqual(x5[0]["severity"], "warning")
        self.assertEqual(x5[0]["row_uids"], [uid(1), uid(7)])
        self.assertEqual(x6[0]["row_uids"], [uid(0), uid(9)])
        cmd = x5[0]["suggested_command"]
        self.assertEqual(cmd[:2], ["ma.py", "analyze"])
        self.assertIn("estimated=igen", cmd)
        self.assertEqual(cmd[cmd.index("--out") + 1], "05_elemzes/o1/o1_primary_becsult_nelkul")
        self.assertEqual(cmd[-2:], ["--project", "."])
        self.assertIn("rob=high", x6[0]["suggested_command"])

    def test_child_runs_clear_with_synonym_filters(self):
        self.pj.run("o1_sens_est", k=11, exclude=["becsült=yes"])
        self.pj.run("o1_sens_rob", k=11, include=["rob=low"])
        self.assertEqual(codes(self.pj.audit(), "X005") + codes(self.pj.audit(), "X006"), [])

    def test_unrelated_or_incomplete_filters_do_not_count(self):
        self.pj.run("o1_alloc", k=6, exclude=["study=Aronson 1948"])     # nem az estimated/rob oszlop
        self.pj.run("o1_rob_low", k=12, exclude=["rob=low"])             # nem a magas RoB-t zárja ki
        rep = self.pj.audit()
        self.assertEqual(len(codes(rep, "X005")), 1)
        self.assertEqual(len(codes(rep, "X006")), 1)

    def test_new_flagged_row_after_child_run(self):
        self.pj.run("o1_sens_est", k=11, exclude=["estimated=igen"])
        self.assertEqual(codes(self.pj.audit(), "X005"), [])
        self.rows[11]["estimated"] = "igen"                  # a szűrő a mostani táblán is kizárja → teljesül
        self.pj.csv(self.rows)
        self.assertEqual(codes(self.pj.audit(), "X005"), [])
        self.assertTrue(codes(self.pj.audit(), "X001"))      # az elavultságot az X001 jelzi

    def test_spec_without_run_is_mentioned(self):
        rel, _ = self.pj.spec("o1_becsult_nelkul", purpose="sensitivity", parent="o1_primary",
                              filters={"exclude": ["estimated=igen"], "include": []})
        x = codes(self.pj.audit(), "X005")
        self.assertEqual(len(x), 1)
        self.assertIn("o1_becsult_nelkul", x[0]["detail"])
        self.assertIn(rel, x[0]["artifacts"])

    def test_results_json_missing_falls_back_to_spec_filters(self):
        self.pj.spec("o1_sens_rob", purpose="sensitivity", parent="o1_primary",
                     filters={"exclude": ["rob=high"], "include": []})
        self.pj.run("o1_sens_rob", k=11, with_results=False)
        self.assertEqual(codes(self.pj.audit(), "X006"), [])

    def test_results_json_without_filters_key_uses_spec(self):
        self.pj.spec("o1_sens_rob", purpose="sensitivity", parent="o1_primary",
                     filters={"exclude": ["rob=high"], "include": []})
        rid, d = self.pj.run("o1_sens_rob", k=11)
        self.pj.write(d + "/results.json", {"options": {"measure": "RR"}, "input": {"n_rows": 13}})
        self.assertEqual(codes(self.pj.audit(), "X006"), [])

    def test_only_latest_run_of_a_spec_counts(self):
        self.pj.run("o1_sens_est", k=11, exclude=["estimated=igen"])
        self.pj.run("o1_sens_est", k=13)                     # a spec szűrőjét később kivették, újrafuttatták
        self.assertEqual(len(codes(self.pj.audit(), "X005")), 1)

    def test_no_flagged_rows_no_finding(self):
        self.pj.csv(bcg_rows())
        rep = self.pj.audit()
        self.assertEqual(codes(rep, "X005") + codes(rep, "X006"), [])


# ------------------------------------------------------------------ X010 / X013
class TestX010X013(_Base):
    def setUp(self):
        super(TestX010X013, self).setUp()
        self.pj.meta()
        self.pj.csv()
        self.pj.studies()

    def test_cells_without_page(self):
        cells = self.pj.prov()
        cells[0]["source"] = {"doc": "pmid:100", "page": None}
        cells[5]["source"] = {}
        del cells[9]                       # nincs eredet-bejegyzés
        cells[12]["source"] = {"doc": "pmid:103", "page": None, "locator": "Suppl. S4"}     # lokátor elég
        cells[13].update(method="calculated", source={}, conversion={"from": "se", "engine": "0.1.0"})
        self.pj.prov(cells=cells)
        x = codes(self.pj.audit(), "X010")
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]["severity"], "warning")
        self.assertEqual(x[0]["cells"], [{"row_uid": uid(0), "field": "e1"}, {"row_uid": uid(1), "field": "n1"},
                                         {"row_uid": uid(2), "field": "n1"}])
        self.assertIn("3 elemzett cellának", x[0]["detail"])
        self.pj.prov()
        self.assertEqual(codes(self.pj.audit(), "X010"), [])

    def test_fallback_page_column(self):
        rows = bcg_rows()
        rows[4]["forras_oldal"] = ""
        self.pj.csv(rows)
        x = codes(self.pj.audit(), "X010")
        self.assertEqual([f["row_uids"] for f in x], [[uid(4)]])
        self.pj.csv(bcg_rows(), header=[h for h in HEADER if h != "forras_oldal"])
        rep = self.pj.audit()
        self.assertEqual(codes(rep, "X010"), [])
        self.assertTrue(skipped(rep, "X010"))

    def test_estimated_flag_out_of_sync(self):
        cells = self.pj.prov()
        cells[4].update(method="digitized", estimated=True)          # S02 e1
        self.pj.prov(cells=cells)
        x = codes(self.pj.audit(), "X013")
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]["severity"], "error")
        self.assertEqual(x[0]["row_uids"], [uid(1)])
        self.assertIn("e1", x[0]["detail"])
        rows = bcg_rows()
        rows[1]["estimated"] = "igen"
        self.pj.csv(rows)
        self.pj.prov(rows, cells=cells)
        self.assertEqual(codes(self.pj.audit(), "X013"), [])
        # fordítva: igen, de minden dokumentált cella közölt
        rows[3]["estimated"] = "igen"
        self.pj.csv(rows)
        self.pj.prov(rows, cells=cells)
        x = codes(self.pj.audit(), "X013")
        self.assertEqual(x[0]["row_uids"], [uid(3)])
        # hiányos eredetnél a fordított irány nem ítélhető meg
        self.pj.prov(rows, cells=[c for c in cells if not (c["row_uid"] == uid(3) and c["field"] == "n2")])
        self.assertEqual(codes(self.pj.audit(), "X013"), [])

    def test_estimated_column_missing(self):
        rows = bcg_rows()
        self.pj.csv(rows, header=[h for h in HEADER if h != "estimated"])
        cells = self.pj.prov(rows)
        cells[0].update(method="imputed")
        self.pj.prov(rows, cells=cells)
        x = codes(self.pj.audit(), "X013")
        self.assertEqual(x[0]["row_uids"], [uid(0)])
        self.assertIn("nincs estimated oszlop", x[0]["detail"])

    def test_no_prov_not_checked(self):
        rep = self.pj.audit()
        self.assertEqual(codes(rep, "X013") + codes(rep, "X022"), [])
        self.assertTrue(skipped(rep, "X013"))
        self.assertTrue(skipped(rep, "X022"))


# ------------------------------------------------------------------ X014
class TestX014(_Base):
    def setUp(self):
        super(TestX014, self).setUp()
        self.pj.meta()
        self.pj.spec("o1_primary")

    def test_k_exceeds_included_studies(self):
        rows = bcg_rows()
        for r in rows:
            del r["study_id"]
        self.pj.csv(rows, header=[h for h in HEADER if h != "study_id"])
        self.pj.studies(n=10)
        rid, _ = self.pj.run("o1_primary", k=13)
        x = codes(self.pj.audit(), "X014")
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]["severity"], "error")
        self.assertEqual(x[0]["stage"], "S04")
        self.assertIn("k = 13 > I = 10", x[0]["detail"])
        self.assertIn("03_adatok/studies.json", x[0]["artifacts"])
        self.pj.studies(n=13)
        self.assertEqual(codes(self.pj.audit(), "X014"), [])

    def test_multi_arm_rows_use_unique_study_id(self):
        rows = bcg_rows()
        for i, r in enumerate(rows):
            r["study_id"] = sid(min(i, 9))             # 13 sor, 10 vizsgálat (több karú)
        self.pj.csv(rows)
        self.pj.studies(n=10)
        self.pj.run("o1_primary", k=13)
        self.assertEqual(codes(self.pj.audit(), "X014"), [])
        self.pj.studies(n=9)
        x = codes(self.pj.audit(), "X014")
        self.assertIn("egyedi study_id", x[0]["detail"])
        self.assertIn("k = 13 > I = 9", x[0]["detail"])

    def test_i_from_prisma_files(self):
        self.pj.csv()
        self.pj.run("o1_primary", k=13)
        self.pj.write("02_szures/prisma_flow.json", {"schema": "szk.prisma-flow/v1", "included": 12,
                                                     "included_studies": 12})
        x = codes(self.pj.audit(), "X014")
        self.assertIn("02_szures/prisma_flow.json", x[0]["artifacts"])
        os.remove(self.pj.p("02_szures/prisma_flow.json"))
        md = read_text(self.pj.p("02_szures/prisma_folyamat.md"))
        md = md.replace("| Bevont közlemények (n = J) és vizsgálatok (n = I) | |",
                        "| Bevont közlemények (n = J) és vizsgálatok (n = I) | 15 / 13 |")
        self.pj.write("02_szures/prisma_folyamat.md", md)
        self.assertEqual(codes(self.pj.audit(), "X014"), [])
        self.pj.write("02_szures/prisma_folyamat.md", md.replace("15 / 13", "15 / 11"))
        x = codes(self.pj.audit(), "X014")
        self.assertIn("02_szures/prisma_folyamat.md", x[0]["artifacts"])

    def test_unknown_i_not_checked(self):
        self.pj.csv()
        self.pj.run("o1_primary", k=13)
        rep = self.pj.audit()            # a project init sablonja üres: I ismeretlen
        self.assertEqual(codes(rep, "X014"), [])
        self.assertTrue(skipped(rep, "X014"))


# ------------------------------------------------------------------ X016
class TestX016(_Base):
    def setUp(self):
        super(TestX016, self).setUp()
        self.pj.meta()
        self.pj.csv()
        self.pj.studies()

    def test_not_prespecified_until_decision(self):
        self.pj.spec("o1_primary", prespecified=False, options={"measure": "RR", "tau2": "REML"})
        x = codes(self.pj.audit(), "X016")
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]["severity"], "warning")
        self.assertEqual(x[0]["spec"], "o1_primary")
        self.assertIn("prespecified: false", x[0]["detail"])
        projekt.log_decision(self.pj.root, "planner", "Modellválasztás rögzítve", stage="S08", kb_refs="D-S08-001",
                             check_kb=False)          # nem eltérés-döntés
        self.assertEqual(len(codes(self.pj.audit(), "X016")), 1)
        projekt.log_decision(self.pj.root, "planner", "Protokoll-eltérés (o1): REML a DL helyett",
                             rationale="kevés vizsgálat, a DL alulbecsüli a τ²-t", stage="S12",
                             kb_refs="X016,D-S12-006", check_kb=False)
        self.assertEqual(codes(self.pj.audit(), "X016"), [])

    def test_differs_from_prespecified_protocol_spec(self):
        self.pj.spec("o1_protocol", prespecified=True, options={"measure": "RR", "tau2": "DL"})
        self.pj.spec("o1_primary", prespecified=None, options={"measure": "RR", "tau2": "REML"})
        x = codes(self.pj.audit(), "X016")
        self.assertEqual(len(x), 1)
        self.assertIn('tau2: "DL" → "REML"', x[0]["detail"])
        self.assertIn("05_elemzes/specs/o1_protocol.json", x[0]["artifacts"])
        # azonos tartalom, más név (és kozmetikai eltérés): nem eltérés
        self.pj.spec("o1_primary", prespecified=False, options={"measure": "rr", "tau2": "DL", "title": "BCG"})
        self.assertEqual(codes(self.pj.audit(), "X016"), [])

    def test_decision_must_name_outcome_in_multi_outcome_project(self):
        self.pj.meta(outcomes=[{"id": "o1", "data": "03_adatok/o1.csv", "measure": "RR",
                                "primary_spec": "05_elemzes/specs/o1_primary.json"},
                               {"id": "o2", "data": "03_adatok/o2.csv", "measure": "RR"}])
        self.pj.spec("o1_primary", prespecified=False)
        projekt.log_decision(self.pj.root, "planner", "Protokoll-eltérés: más modell", stage="S12",
                             check_kb=False)
        self.assertEqual(len(codes(self.pj.audit(), "X016", "o1")), 1)
        projekt.log_decision(self.pj.root, "planner", "Az o1_primary elsődleges, eltérve a protokolltól",
                             stage="S12", kb_refs="D-S12-006", check_kb=False)
        self.assertEqual(codes(self.pj.audit(), "X016"), [])

    def test_prespecified_primary_and_missing_info(self):
        self.pj.spec("o1_primary", prespecified=True)
        self.assertEqual(codes(self.pj.audit(), "X016"), [])
        self.pj.spec("o1_primary", prespecified=None)
        rep = self.pj.audit()
        self.assertEqual(codes(rep, "X016"), [])
        self.assertTrue(skipped(rep, "X016"))
        os.remove(self.pj.p("05_elemzes/specs/o1_primary.json"))
        self.pj.spec("o1_other", purpose="exploratory")
        self.assertTrue(any("primary_spec" in n["reason"] for n in skipped(self.pj.audit(), "X016")))

    def test_without_journal(self):
        pj = Proj(self.tmp, "nojournal", journal=False)
        pj.meta()
        pj.csv()
        pj.spec("o1_primary", prespecified=False)
        x = codes(pj.audit(), "X016")
        self.assertEqual(len(x), 1)
        self.assertIn("projekt.sqlite", x[0]["detail"])

    def test_primary_by_purpose_without_meta(self):
        os.remove(self.pj.p("ma-projekt.json"))
        self.pj.spec("o1_a", prespecified=False)
        self.pj.spec("o1_b", prespecified=True, options={"measure": "OR"})
        rep = self.pj.audit()
        self.assertTrue(any("több elsődleges" in n["reason"] for n in skipped(rep, "X016")))
        self.pj.run("o1_a")
        x = codes(self.pj.audit(), "X016")
        self.assertEqual([f["spec"] for f in x], ["o1_a"])


# ------------------------------------------------------------------ X022
class TestX022(_Base):
    def setUp(self):
        super(TestX022, self).setUp()
        self.pj.meta()
        self.pj.csv()
        self.pj.studies()
        self.cells = self.pj.prov()

    def test_stale_sidecar_but_reconcilable(self):
        rows = bcg_rows()
        rows[0]["rob"] = "some concerns"           # nem eredet-mező változott
        self.pj.csv(rows)
        rep = self.pj.audit()
        self.assertEqual(codes(rep, "X022"), [])

    def test_stale_sidecar_unreconcilable(self):
        rows = bcg_rows()
        rows[0]["e1"] = 5                          # eltérő érték
        del rows[12]                               # eltűnt sor
        self.pj.csv(rows)
        x = codes(self.pj.audit(), "X022")
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]["severity"], "error")
        self.assertEqual(x[0]["cells"][0], {"row_uid": uid(0), "field": "e1"})
        self.assertEqual(len(x[0]["cells"]), 5)
        self.assertIn("03_adatok/o1.prov.json", x[0]["artifacts"])
        self.pj.prov(rows)                         # újramentett eredet: friss hash
        self.assertEqual(codes(self.pj.audit(), "X022"), [])

    def test_decimal_comma_and_na_reconcile(self):
        rows = bcg_rows()
        rows[0]["e1"] = "4,0"
        self.pj.csv(rows)
        self.cells[0]["value_as_entered"] = "4,0"
        self.cells[1]["value_as_entered"] = None
        self.pj.prov(cells=self.cells, table_sha="0" * 64)
        self.assertEqual(codes(self.pj.audit(), "X022"), [])

    def test_missing_table_and_hash(self):
        doc = read_json(self.pj.p("03_adatok/o1.prov.json"))
        del doc["table_sha256"]
        self.pj.write("03_adatok/o1.prov.json", doc)
        rep = self.pj.audit()
        self.assertTrue(any("table_sha256" in n["reason"] for n in skipped(rep, "X022")))
        self.pj.prov(cells=self.cells)
        os.remove(self.pj.p("03_adatok/o1.csv"))
        x = codes(self.pj.audit(), "X022")
        self.assertEqual(len(x), 1)
        self.assertIn("nem található", x[0]["detail"])


# ------------------------------------------------------------------ kapu, CLI, tűrőképesség
class TestGateAndCli(_Base):
    def test_gate(self):
        self.pj.clean()
        self.assertEqual(A.audit_gate_errors(self.pj.root), [])
        A.require_gate(self.pj.root)
        self.assertIsNone(A.gate_message([]))
        projekt.checkpoint(self.pj.root, "S05", "reviewer", "PASS")
        rows = bcg_rows()
        rows[0]["rob"] = "high"
        rows[1]["estimated"] = "igen"
        rows[3]["e1"] = 63
        self.pj.csv(rows)
        # a napló szerint S06: az X001 figyelmeztetés — a FINAL kapu viszont hibaként kezeli
        self.assertEqual({f["severity"] for f in codes(self.pj.audit(), "X001")}, {"warning"})
        errs = A.audit_gate_errors(self.pj.root)
        self.assertTrue(errs)
        self.assertEqual({f["code"] for f in errs}, {"X001", "X022"})
        with self.assertRaises(ValueError) as cm:
            A.require_gate(self.pj.root)
        self.assertIn("FINAL", str(cm.exception))
        self.assertIn("X001 [o1] (3×)", str(cm.exception))
        self.assertEqual(str(cm.exception).count("X001"), 1)

    def test_warnings_do_not_block_gate(self):
        self.pj.meta()
        self.pj.csv()
        self.pj.studies()
        self.pj.spec("o1_primary", prespecified=False)
        self.pj.run("o1_primary")
        rep = self.pj.audit()
        self.assertTrue(codes(rep, "X016"))
        self.assertEqual(A.audit_gate_errors(self.pj.root), [])

    def test_cli_main(self):
        self.pj.clean()
        buf = io.StringIO()
        self.assertEqual(A.cli_main(self.pj.root, as_json=True, out=buf), 0)
        self.assertEqual(json.loads(buf.getvalue())["schema"], "szk.ma.project-audit/v1")
        rows = bcg_rows()
        rows[3]["e1"] = 63
        self.pj.csv(rows)
        buf = io.StringIO()
        self.assertEqual(A.cli_main(self.pj.root, out=buf), 1)
        text = buf.getvalue()
        self.assertTrue(text.startswith("Projekt-audit (glp1"))
        self.assertIn("[X001] HIBA", text)
        self.assertIn("javasolt parancs: ma.py analyze --spec 05_elemzes/specs/o1_primary.json", text)
        buf = io.StringIO()
        self.assertEqual(A.cli_main(self.pj.root, stage="S06", out=buf), 1)    # X022/X013 hiba marad

    def test_tolerates_malformed_files(self):
        self.pj.clean()
        self.pj.write("05_elemzes/specs/rossz.json", "{nem json")
        self.pj.write("05_elemzes/o1/20261004T000000Z-abcdef/run.json", "[1, 2")
        self.pj.write("04_torzitas_kockazat/appraisals/S05.rob2.SzK.json", '{"schema": "szk.appraisal/v1", "status": '
                                                                           '"complete", "overall": {"judgement": 7}}')
        self.pj.write("03_adatok/studies.json", '{"studies": NaN}')
        self.pj.write("05_elemzes/o1/üres/run.json", "")
        rep = self.pj.audit()
        reasons = " | ".join(n["reason"] for n in rep["not_checked"])
        self.assertIn("rossz.json", reasons)
        self.assertIn("20261004T000000Z-abcdef/run.json", reasons)
        json.dumps(rep, allow_nan=False)
        # érvénytelen ma-projekt.json: a kimenetek a többi fájlból
        self.pj.write("ma-projekt.json", '{"title": "", "data_class": "Z"}')
        rep = self.pj.audit()
        self.assertEqual(rep["outcomes"], ["o1"])
        self.assertTrue(any("ma-projekt.json" in n["reason"] for n in rep["not_checked"]))
        # olvashatatlan tábla
        self.pj.write("03_adatok/o1.csv", b"")
        rep = self.pj.audit()
        self.assertTrue(any("adattábla nem olvasható" in n["reason"] for n in skipped(rep, "X005")))

    def test_outcomes_discovered_without_meta(self):
        os.remove(self.pj.p("ma-projekt.json")) if os.path.exists(self.pj.p("ma-projekt.json")) else None
        self.pj.csv(rel="03_adatok/o7.csv")
        self.pj.prov(rel="03_adatok/o7.prov.json", table="03_adatok/o7.csv")
        rep = self.pj.audit()
        self.assertEqual(rep["outcomes"], ["o7"])
        self.assertIn("03_adatok/o7.csv", rep["inputs"])
        self.assertNotIn("03_adatok/adatkinyeres.csv", rep["inputs"])     # a project init sablonja nem kimenet

    def test_sidecar_of_declared_outcome_is_not_a_new_outcome(self):
        self.pj.meta(outcomes=[{"id": "mortalitas", "data": "03_adatok/o1.csv", "measure": "RR"}])
        self.pj.csv()
        self.pj.prov()
        rows = bcg_rows()
        rows[0]["e1"] = 5
        self.pj.csv(rows)
        rep = self.pj.audit()
        self.assertEqual(rep["outcomes"], ["mortalitas"])
        self.assertEqual([f["outcome"] for f in codes(rep, "X022")], ["mortalitas"])


class TestCliWiring(_Base):
    """A 'project audit' alparancs és a 'checkpoint --audit-gate' (cli.py / projekt.py bekötése után fut)."""

    def _cli(self, *args):
        from contextlib import redirect_stderr, redirect_stdout
        from metaelemzes import cli
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_project_audit_subcommand(self):
        self.pj.clean()
        code, out, err = self._cli("project", "audit", self.pj.root, "--json")
        if code == 2 and "invalid choice" in err:
            self.skipTest("a 'project audit' alparancs még nincs bekötve (cli.py)")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["schema"], A.SCHEMA)
        rows = bcg_rows()
        rows[3]["e1"] = 63
        self.pj.csv(rows)
        code, out, _ = self._cli("project", "audit", self.pj.root, "--stage", "S06")
        self.assertEqual(code, 1)
        self.assertIn("[X001] FIGYELEM", out)

    def test_checkpoint_audit_gate(self):
        import inspect
        if "audit_gate" not in inspect.signature(projekt.checkpoint).parameters:
            self.skipTest("a projekt.checkpoint audit_gate paramétere még nincs bekötve (projekt.py)")
        self.pj.clean()
        rows = bcg_rows()
        rows[3]["e1"] = 63
        self.pj.csv(rows)
        with self.assertRaises(ValueError):
            projekt.checkpoint(self.pj.root, "FINAL", "reviewer", "PASS", audit_gate=True)
        with self.assertRaises(ValueError):
            projekt.checkpoint(self.pj.root, "S08", "reviewer", "PASS", audit_gate=True)
        projekt.checkpoint(self.pj.root, "FINAL", "reviewer", "FAIL", audit_gate=True)
        projekt.checkpoint(self.pj.root, "FINAL", "reviewer", "PASS")             # kapu nélkül: régi viselkedés
        code, _, err = self._cli("project", "checkpoint", self.pj.root, "--stage", "FINAL", "--agent", "reviewer",
                                 "--verdict", "PASS", "--audit-gate")
        self.assertEqual(code, 1)
        self.assertIn("X-szabály", err)


# ------------------------------------------------------------------ segédek
def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_json(path):
    return json.loads(read_text(path))


def snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def validate_schema(tc, obj, schema, path="$"):
    """A JSON Schema általunk használt részhalmaza (type, const, enum, required, properties, items, pattern,
    additionalProperties, minimum)."""
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        ok = {"object": isinstance(obj, dict), "array": isinstance(obj, list), "string": isinstance(obj, str),
              "integer": isinstance(obj, int) and not isinstance(obj, bool),
              "number": isinstance(obj, (int, float)) and not isinstance(obj, bool), "null": obj is None,
              "boolean": isinstance(obj, bool)}
        tc.assertTrue(any(ok[x] for x in types), "%s: típus %s, kapott %r" % (path, types, obj))
    if "const" in schema:
        tc.assertEqual(obj, schema["const"], path)
    if "enum" in schema:
        tc.assertIn(obj, schema["enum"], path)
    if isinstance(obj, str) and "pattern" in schema:
        tc.assertTrue(re.search(schema["pattern"], obj), "%s: %r !~ %s" % (path, obj, schema["pattern"]))
    if isinstance(obj, int) and "minimum" in schema:
        tc.assertGreaterEqual(obj, schema["minimum"], path)
    if isinstance(obj, dict):
        for k in schema.get("required", ()):
            tc.assertIn(k, obj, "%s: hiányzó kötelező mező %s" % (path, k))
        props = schema.get("properties", {})
        for k, v in obj.items():
            if k in props:
                validate_schema(tc, v, props[k], "%s.%s" % (path, k))
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(tc, v, schema["additionalProperties"], "%s.%s" % (path, k))
    if isinstance(obj, list) and "items" in schema:
        for i, v in enumerate(obj):
            validate_schema(tc, v, schema["items"], "%s[%d]" % (path, i))


if __name__ == "__main__":
    unittest.main()
