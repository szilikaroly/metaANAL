# -*- coding: utf-8 -*-
"""Integrációs tesztek: CLI, pipeline, tudásbázis, projektnapló (ideiglenes mappákban)."""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr

from _helpers import ROOT
from metaelemzes import cli, kb, projekt

EXAMPLES = os.path.join(ROOT, "peldak")


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = cli.main(list(args))
    return code, buf.getvalue(), err.getvalue()


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_analyze_all_examples(self):
        cases = [("bcg_oltas_RR.csv", "RR", ["--mh", "--subgroup", "allokáció", "--moderators", "szélesség", "--cumulative", "év"]),
                 ("bcg_oltas_RR.csv", "OR", ["--mh", "--peto", "--model", "fixed"]),
                 ("normand1999_folytonos.csv", "SMD", []),
                 ("normand1999_folytonos.csv", "MD", ["--tau2", "DL", "--ci", "z"]),
                 ("molloy2014_korrelacio.csv", "ZCOR", []),
                 ("pritz1997_arany.csv", "PFT", []),
                 ("pritz1997_arany.csv", "PLO", [])]
        for fname, measure, extra in cases:
            out = os.path.join(self.tmp, "%s_%s" % (measure, fname))
            code, so, se = run_cli("analyze", "--data", os.path.join(EXAMPLES, fname), "--measure", measure,
                                   "--out", out, "--date", "2026-01-01", *extra)
            self.assertEqual(code, 0, "%s %s: %s %s" % (fname, measure, so, se))
            for f in ("results.json", "report.md", "forest.svg", "funnel.svg", "plot_data.json", "effect_sizes.csv"):
                self.assertTrue(os.path.exists(os.path.join(out, f)), f)
            with open(os.path.join(out, "results.json"), encoding="utf-8") as fh:
                res = json.load(fh)
            self.assertGreater(res["effect_sizes"]["k"], 0)
            with open(os.path.join(out, "report.md"), encoding="utf-8") as fh:
                md = fh.read()
            self.assertIn("## Methods", md)

    def test_validate_exit_code(self):
        bad = os.path.join(self.tmp, "bad.csv")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("study,e1,n1,e2,n2\nA,12,10,3,10\nB,1,10,2,10\n")
        code, so, _ = run_cli("validate", "--data", bad, "--measure", "OR")
        self.assertEqual(code, 1)
        self.assertIn("V006", so)

    def test_convert(self):
        code, so, _ = run_cli("convert", "median", "--n", "40", "--median", "12", "--q1", "9", "--q3", "16")
        self.assertEqual(code, 0)
        self.assertIn("mean", json.loads(so))


class TestKnowledgeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "kb.sqlite")
        kb.build(self.db)

    def test_build_and_validation_rules_loaded(self):
        st = kb.stats(self.db)
        self.assertGreaterEqual(st["decision_rule"], 20)
        rows = kb.rules(stage="S05", db=self.db)
        self.assertTrue(any(r["rule_id"] == "V011" for r in rows))

    def test_search_and_readonly_sql(self):
        res = kb.search("SE SD", db=self.db, scopes=("rule",))
        self.assertTrue(any(r["id"] == "V011" for r in res["rule"]))
        cols, rows = kb.query("SELECT rule_id FROM decision_rule WHERE rule_id = ?", ("V001",), db=self.db)
        self.assertEqual(rows[0][0], "V001")
        for bad in ("DELETE FROM decision_rule", "DROP TABLE source", "UPDATE tool SET name='x'"):
            with self.assertRaises(kb.KBError):
                kb.query(bad, db=self.db)
        # read-only kapcsolat: a WITH-be csempészett írás is elbukik
        with self.assertRaises(sqlite3.Error):
            kb.query("WITH x AS (SELECT 1) DELETE FROM decision_rule", db=self.db)

    def test_ingest_txt_and_docx(self):
        txt = os.path.join(self.tmp, "sajat_jegyzet.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write("Heterogenitás\n\nA predikciós intervallum a várható valódi hatások tartománya.\n\n" * 3)
        docx = os.path.join(self.tmp, "minta.docx")
        body = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                '<w:p><w:r><w:t>Chapter 9 Weighted mean difference</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>The Hartung-Knapp adjustment widens confidence intervals.</w:t></w:r></w:p>'
                '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Study</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>MD</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                '</w:body></w:document>')
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("word/document.xml", body)
        res = kb.ingest(self.tmp, db=self.db)
        self.assertEqual(len([r for r in res if r[0]]), 2)
        hits = kb.search("predikciós intervallum", db=self.db, scopes=("chunk",))
        self.assertTrue(hits["chunk"])
        hits = kb.search("Hartung", db=self.db, scopes=("chunk",))
        self.assertTrue(any("Chapter 9" in (h["locator"] or "") for h in hits["chunk"]))
        # újrabetöltés nem duplikál
        kb.ingest(self.tmp, db=self.db)
        self.assertEqual(len(kb.search("Hartung", db=self.db, scopes=("chunk",))["chunk"]), 1)


class TestProject(unittest.TestCase):
    def test_lifecycle(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        d = os.path.join(tmp, "proj")
        projekt.init(d, "Teszt SR", "PICO?")
        did = projekt.log_decision(d, "planner", "REML + HKSJ", "k várhatóan < 10", "S08", "D-SYN-001")
        fid = projekt.add_finding(d, "reviewer", "blocker", "SE/SD csere gyanú", stage="S05", kb_refs="V011")
        with self.assertRaises(ValueError):
            projekt.checkpoint(d, "S05", "reviewer", "PASS")
        projekt.resolve_finding(d, fid, "fixed", "forrás ellenőrizve, SD javítva")
        projekt.checkpoint(d, "S05", "reviewer", "PASS", "rendben")
        projekt.add_grade(d, "HbA1c", "moderate", k=8, imprecision="-1")
        st = projekt.status(d)
        self.assertEqual(st["open_findings"], [])
        self.assertEqual(st["decisions"], 1)
        md = projekt.export_markdown(d)
        self.assertIn("REML + HKSJ", md)
        self.assertGreater(did, 0)


if __name__ == "__main__":
    unittest.main()
