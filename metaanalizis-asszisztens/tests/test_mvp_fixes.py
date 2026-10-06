# -*- coding: utf-8 -*-
"""MVP-javítások regressziós tesztjei (a bírálat megerősített megállapításai; azonosító a tesztek leírásában).

- compat-1 / contracts C1 / correctness C3: a project audit a dokumentált elrendezések commit-futásait is látja
  (05_elemzes/<kimenet>/run.json, a --project melletti alapértelmezett <adatmappa>/eredmeny a projektnaplóból, az
  'analyze --spec --project' alapértelmezett 05_elemzes/<kimenet>/<run_id>/ mappája), a javasolt parancs az X001-et
  megszünteti, és a FINAL audit-kapu figyelmeztet, ha semmit sem tudott ellenőrizni;
- compat-2 / contracts C3 / correctness C1: a run_id egyedi (két commit ugyanabban a másodpercben sem írja felül
  egymást);
- compat-3: a régi, egyértelmű rövidítések (--a, --j) továbbra is működnek;
- contracts C2: plot_data.json funnel.tests_text sosem null (k < 3, egyforma vizsgálatok);
- contracts C4 / correctness C8: egyetlen sorindex-tér (read_table, validate_file, validate_table, analyze, plot);
- contracts C5: a read_raw / write_raw az üres és ';;;;' sorokat is bájtra azonosan írja vissza;
- contracts C6: egy számszöveg-konvenció (outputs_text = display_text), a cellába írt szöveg a cell_text;
- contracts C7: a spec-ellenőrző nem fogad el olyat, amit a szerződés tilt;
- contracts C8: explore-futás run.json-jában nincs files;
- contracts C9: széles ZCOR-tengelyen is több tick;
- correctness C2: a futás-csoportok (különböző CLI-elemzések nem takarják el egymást; időrend másodpercen belül);
- correctness C5: X022 a tábla saját számformátumával olvassa a bevitt értéket;
- correctness C6: a slugolt kimenetnév nem osztja ketté a kimenetet;
- correctness C7: az api commit projekt-relatív adatutat ír a results.json-ba és a report.md-be;
- correctness C3 / C9: az ágens-dokumentáció és a TELEPITES.md.
"""
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from _helpers import ROOT
from metaelemzes import api, audit as A, cli, contracts as K, plots, projekt, spec as S, tableio

PELDAK = os.path.join(ROOT, "peldak")
BCG = os.path.join(PELDAK, "bcg_oltas_RR.csv")
DATE = "2026-10-05"
FIXED = datetime.datetime(2026, 10, 5, 3, 45, 7, tzinfo=datetime.timezone.utc)
REPO = os.path.dirname(ROOT)


def contract_errors(doc, name, version):
    import test_mvp_contracts as TC
    return TC.errors_for(doc, name, version)


def run_cli(*args, cwd=None):
    out, err = io.StringIO(), io.StringIO()
    here = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([str(a) for a in args])
    finally:
        os.chdir(here)
    return code, out.getvalue(), err.getvalue()


def frozen(when=FIXED):
    """A datetime modul helyettese: a now() mindig ugyanazt az időpontot adja (ugyanabban a másodpercben futó
    commitok)."""
    class DT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return when if tz is not None else when.replace(tzinfo=None)
    return types.SimpleNamespace(datetime=DT, timezone=datetime.timezone, date=datetime.date,
                                 timedelta=datetime.timedelta)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def edit_first_cell(path, old="Aronson 1948;4;", new="Aronson 1948;5;"):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert old in text
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(old, new, 1))


def codes(rep, code):
    return [f for f in rep["findings"] if f["code"] == code]


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def project(self, name="p", data="adatkinyeres.csv", text=None):
        p = os.path.join(self.tmp, name)
        projekt.init(p, "Teszt")
        dst = os.path.join(p, "03_adatok", data)
        if text is None:
            shutil.copy(BCG, dst)
        else:
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(text)
        return p, dst


# ================================================================ compat-1 / C1 / C3
class TestAuditSeesDocumentedLayouts(_Tmp):
    def test_flat_out_layout_from_agent_docs_is_audited_and_blocks_final_gate(self):
        """compat-1 (a), contracts C1, correctness C3: --out <mappa>/05_elemzes/<kimenet> (egy szint)."""
        p, data = self.project()
        code, _, err = run_cli("analyze", "--data", data, "--measure", "RR", "--project", p, "--out",
                               os.path.join(p, "05_elemzes", "o1"), "--date", DATE)
        self.assertEqual(code, 0, err)
        self.assertEqual(read_json(os.path.join(p, "05_elemzes", "o1", "run.json"))["mode"], "commit")
        self.assertEqual(codes(A.project_audit(p, stage="S08"), "X001"), [])
        edit_first_cell(data)
        rep = A.project_audit(p, stage="S08")
        x = codes(rep, "X001")
        self.assertEqual(len(x), 1, rep["not_checked"])
        self.assertEqual(x[0]["severity"], "error")
        self.assertIn("05_elemzes/o1/run.json", x[0]["artifacts"])
        with self.assertRaises(ValueError) as cm:
            projekt.checkpoint(p, "FINAL", "reviewer", "PASS", audit_gate=True)
        self.assertIn("X001", str(cm.exception))
        code, _, err = run_cli("project", "checkpoint", p, "--stage", "FINAL", "--agent", "reviewer", "--verdict",
                               "PASS", "--audit-gate")
        self.assertEqual(code, 1)
        self.assertIn("X001", err)

    def test_default_outdir_found_via_journal_and_suggested_command_clears_x001(self):
        """contracts C1 (3), compat-1 (c): a --project melletti alapértelmezett <adatmappa>/eredmeny futás a
        projektnaplóból; a javasolt (--out nélküli) parancs után az X001 eltűnik."""
        p, data = self.project()
        code, _, err = run_cli("analyze", "--data", data, "--measure", "RR", "--project", p, "--date", DATE)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(p, "03_adatok", "eredmeny", "run.json")))
        edit_first_cell(data)
        x = codes(A.project_audit(p, stage="S08"), "X001")
        self.assertEqual(len(x), 1)
        cmd = x[0]["suggested_command"]
        self.assertEqual(cmd[:2], ["ma.py", "analyze"])
        code, _, err = run_cli(*cmd[1:], cwd=p)
        self.assertEqual(code, 0, err)
        self.assertEqual(codes(A.project_audit(p, stage="S08"), "X001"), [])

    def test_nested_run_then_suggested_command_clears_x001(self):
        """compat-1 (c): futás a 05_elemzes/o2/r1-ben, elavult adat → a javasolt parancs futtatása után nincs X001."""
        p, data = self.project()
        code, _, err = run_cli("analyze", "--data", "03_adatok/adatkinyeres.csv", "--measure", "RR", "--project", ".",
                               "--out", "05_elemzes/o2/r1", cwd=p)
        self.assertEqual(code, 0, err)
        edit_first_cell(data)
        x = codes(A.project_audit(p, stage="S08"), "X001")
        self.assertEqual([f["outcome"] for f in x], ["o2"])
        code, _, err = run_cli(*x[0]["suggested_command"][1:], cwd=p)
        self.assertEqual(code, 0, err)
        rep = A.project_audit(p, stage="S08")
        self.assertEqual(codes(rep, "X001"), [])
        self.assertEqual(rep["outcomes"], ["o2"])

    def test_spec_run_without_out_lands_in_outcome_run_dir(self):
        """compat-1 (b): 'analyze --spec … --project .' → 05_elemzes/<kimenet>/<run_id>/ (mint az api.analyze)."""
        p, data = self.project(data="o1.csv")
        sp = S.spec_from_argv(["analyze", "--data", data, "--measure", "RR"], p, name="o1_primary", outcome="bcg",
                              purpose="primary", pin_data=True)
        S.save_spec(os.path.join(p, S.spec_relpath("o1_primary")), sp)
        code, out, err = run_cli("analyze", "--spec", "05_elemzes/specs/o1_primary.json", "--project", ".",
                                 "--json-summary", cwd=p)
        self.assertEqual(code, 0, err)
        run = json.loads(out)
        d = os.path.join(p, "05_elemzes", "bcg", run["run_id"])
        self.assertEqual(read_json(os.path.join(d, "run.json")), run)
        self.assertRegex(run["run_id"], S.RUN_ID_PATTERN)
        self.assertFalse(os.path.exists(os.path.join(p, "03_adatok", "eredmeny")))
        self.assertEqual(read_json(os.path.join(d, "results.json"))["input"]["path"], "03_adatok/o1.csv")
        edit_first_cell(data)
        x = codes(A.project_audit(p, stage="S08"), "X001")
        self.assertEqual([(f["outcome"], f["run_id"]) for f in x], [("bcg", run["run_id"])])

    def test_moved_project_relocates_journal_outdirs(self):
        p, data = self.project()
        code, _, err = run_cli("analyze", "--data", data, "--measure", "RR", "--project", p, "--date", DATE)
        self.assertEqual(code, 0, err)
        moved = os.path.join(self.tmp, "athelyezve")
        shutil.move(p, moved)
        edit_first_cell(os.path.join(moved, "03_adatok", "adatkinyeres.csv"))
        x = codes(A.project_audit(moved, stage="S08"), "X001")
        self.assertEqual(len(x), 1)
        self.assertIn("03_adatok/eredmeny/run.json", x[0]["artifacts"])

    def test_outside_dir_overwritten_by_another_project_is_ignored(self):
        p1, d1 = self.project("p1")
        p2, d2 = self.project("p2")
        edit_first_cell(d2)
        shared = os.path.join(self.tmp, "kozos")
        for p in (p1, p2):
            code, _, err = run_cli("analyze", "--data", "03_adatok/adatkinyeres.csv", "--measure", "RR", "--project",
                                   ".", "--out", shared, cwd=p)
            self.assertEqual(code, 0, err)
        rep = A.project_audit(p1, stage="S08")
        self.assertEqual(codes(rep, "X001"), [])
        self.assertEqual(rep["outcomes"], [])               # p1 futásának helyén már a p2-é van
        edit_first_cell(d1)
        self.assertEqual(len(codes(A.project_audit(p2, stage="S08"), "X001")), 0)
        edit_first_cell(d2, "Aronson 1948;5;", "Aronson 1948;6;")
        self.assertEqual(len(codes(A.project_audit(p2, stage="S08"), "X001")), 1)

    def test_explore_runs_are_not_commit_runs(self):
        p, data = self.project()
        code, _, err = run_cli("analyze", "--data", data, "--measure", "RR", "--out",
                               os.path.join(p, "05_elemzes", "o1", "proba"))
        self.assertEqual(code, 0, err)
        edit_first_cell(data)
        self.assertEqual(codes(A.project_audit(p, stage="S08"), "X001"), [])

    def test_final_gate_warns_when_audit_checked_nothing(self):
        """correctness C3: kimenet nélkül (de adattáblával) a kapu nem némán megy át."""
        p, _ = self.project()
        rep = A.project_audit(p)
        self.assertEqual(rep["outcomes"], [])
        self.assertIn("semmit sem ellenőriztek", A.format_text(rep))
        warns = []
        projekt.checkpoint(p, "FINAL", "reviewer", "PASS", warnings=warns, audit_gate=True)
        self.assertTrue(any("egyetlen kimenetet sem talált" in w for w in warns), warns)
        q, _ = self.project("q")
        code, _, err = run_cli("project", "checkpoint", q, "--stage", "FINAL", "--agent", "reviewer", "--verdict",
                               "PASS", "--audit-gate")
        self.assertEqual(code, 0, err)
        self.assertIn("egyetlen kimenetet sem talált", err)


# ================================================================ compat-2 / C3 / correctness C1
class TestRunIdUnique(_Tmp):
    def specs(self, p):
        data = os.path.join(p, "03_adatok", "bcg.csv")
        s1 = api.spec_from_argv(["--data", data, "--measure", "RR"], project_root=p)
        s2 = api.spec_from_argv(["--data", data, "--measure", "RR", "--exclude", "allokáció=random"], project_root=p)
        return s1, s2

    def test_two_api_commits_in_the_same_second(self):
        p, _ = self.project(data="bcg.csv")
        s1, s2 = self.specs(p)
        with mock.patch.object(api, "datetime", frozen()):
            v1 = api.analyze(s1, mode="commit", project_root=p, date=DATE)
            first = os.path.join(p, v1["run"]["files"]["results"]["path"])
            with open(first, "rb") as fh:
                before = fh.read()
            v2 = api.analyze(s2, mode="commit", project_root=p, date=DATE)
        r1, r2 = v1["run"]["run_id"], v2["run"]["run_id"]
        self.assertNotEqual(r1, r2)
        for r in (r1, r2):
            self.assertRegex(r, S.RUN_ID_PATTERN)
        self.assertEqual(r1, "20261005T034507Z-" + S.sha256_file(os.path.join(p, "03_adatok", "bcg.csv"))[:6])
        self.assertEqual(sorted(os.listdir(os.path.join(p, "05_elemzes", "bcg"))), sorted([r1, r2]))
        with open(first, "rb") as fh:
            self.assertEqual(fh.read(), before)            # az első futás kimenete érintetlen
        self.assertEqual((v1["run"]["k"], v2["run"]["k"]), (13, 6))
        self.assertEqual(read_json(os.path.join(p, "05_elemzes", "bcg", r1, "run.json"))["k"], 13)
        outdirs = [r["outdir"] for r in projekt.list_items(p, "runs")]
        self.assertEqual(len(set(outdirs)), 2)

    def test_explicit_run_id_never_overwrites_a_committed_run_dir(self):
        p, _ = self.project(data="bcg.csv")
        s1, s2 = self.specs(p)
        rid = "20261005T120000Z-abcdef"
        api.analyze(s1, mode="commit", project_root=p, run_id=rid, date=DATE)
        with self.assertRaises(S.SpecError):
            api.analyze(s2, mode="commit", project_root=p, run_id=rid, date=DATE)
        self.assertEqual(read_json(os.path.join(p, "05_elemzes", "bcg", rid, "run.json"))["k"], 13)

    def test_cli_commit_runs_in_the_same_second(self):
        p, data = self.project(data="bcg.csv")
        with mock.patch.object(cli, "datetime", frozen()):
            for m, o in (("RR", "c1"), ("OR", "c2")):
                code, _, err = run_cli("analyze", "--data", data, "--measure", m, "--project", p, "--out",
                                       os.path.join(self.tmp, o), "--date", DATE)
                self.assertEqual(code, 0, err)
        ids = [read_json(os.path.join(self.tmp, o, "run.json"))["run_id"] for o in ("c1", "c2")]
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(S.project_run_ids(p), set(ids))     # a projekten kívüli futás is a naplóból

    def test_cli_spec_runs_in_the_same_second_get_own_dirs(self):
        p, data = self.project(data="o1.csv")
        sp = S.spec_from_argv(["analyze", "--data", data, "--measure", "RR"], p, name="o1_primary", outcome="o1")
        S.save_spec(os.path.join(p, S.spec_relpath("o1_primary")), sp)
        with mock.patch.object(cli, "datetime", frozen()):
            for _ in range(2):
                code, _, err = run_cli("analyze", "--spec", "05_elemzes/specs/o1_primary.json", "--project", ".",
                                       cwd=p)
                self.assertEqual(code, 0, err)
        dirs = sorted(os.listdir(os.path.join(p, "05_elemzes", "o1")))
        self.assertEqual(len(dirs), 2)
        self.assertEqual([read_json(os.path.join(p, "05_elemzes", "o1", d, "run.json"))["run_id"] for d in dirs],
                         dirs)
        # kifejezett --run-id: a meglévő futásmappát nem írja felül
        before = read_json(os.path.join(p, "05_elemzes", "o1", dirs[0], "run.json"))
        code, _, err = run_cli("analyze", "--spec", "05_elemzes/specs/o1_primary.json", "--project", ".", "--run-id",
                               dirs[0], cwd=p)
        self.assertEqual(code, 1)
        self.assertIn("nem írja felül", err)
        self.assertEqual(read_json(os.path.join(p, "05_elemzes", "o1", dirs[0], "run.json")), before)

    def test_unique_run_id_helper(self):
        sha = "ab" * 32
        self.assertEqual(S.unique_run_id(FIXED, sha)[0], "20261005T034507Z-ababab")
        self.assertEqual(S.unique_run_id(FIXED, sha, taken={"20261005T034507Z-ababab"})[0],
                         "20261005T034508Z-ababab")
        base = os.path.join(self.tmp, "runs")
        a, da = S.unique_run_id(FIXED, sha, base_dir=base)
        b, db = S.unique_run_id(FIXED, sha, base_dir=base)
        self.assertNotEqual((a, da), (b, db))
        self.assertTrue(os.path.isdir(da) and os.path.isdir(db))


# ================================================================ compat-3
class TestLegacyAbbreviations(_Tmp):
    def test_old_unique_prefixes_still_parse(self):
        p = cli.build_parser()
        self.assertEqual(p.parse_args(["project", "finding", "P", "--a", "reviewer", "--severity", "minor",
                                       "--title", "X"]).agent, "reviewer")
        self.assertEqual(p.parse_args(["projekt", "checkpoint", "P", "--stage", "S01", "--a=reviewer", "--verdict",
                                       "PASS"]).agent, "reviewer")
        for cmd in ("analyze", "elemez"):
            ns = p.parse_args([cmd, "--data", "x.csv", "--measure", "SMD", "--j", "approx"])
            self.assertEqual((ns.j_method, ns.json_summary), ("approx", False))
        # az új kapcsolók teljes (és új, egyértelmű) alakja változatlan
        ns = p.parse_args(["project", "checkpoint", "P", "--stage", "FINAL", "--agent", "reviewer", "--verdict",
                           "PASS", "--actor", "user:SzK", "--audit-gate"])
        self.assertEqual((ns.agent, ns.actor, ns.audit_gate), ("reviewer", "user:SzK", True))
        self.assertTrue(p.parse_args(["analyze", "--data", "x", "--measure", "RR", "--json-s"]).json_summary)
        # a '--' utáni elemeket nem bántja
        self.assertEqual(cli._expand_legacy(["--a", "x", "--", "--a"], {"--a": "--agent"}),
                         ["--agent", "x", "--", "--a"])

    def test_old_command_lines_run(self):
        p, data = self.project()
        code, out, err = run_cli("project", "finding", p, "--a", "reviewer", "--severity", "minor", "--title", "X")
        self.assertEqual(code, 0, err)
        self.assertIn("megállapítás #1", out)
        code, out, err = run_cli("project", "checkpoint", p, "--stage", "S01", "--a", "reviewer", "--verdict", "PASS")
        self.assertEqual(code, 0, err)
        code, _, err = run_cli("analyze", "--data", os.path.join(PELDAK, "normand1999_folytonos.csv"), "--measure",
                               "SMD", "--j", "approx", "--out", os.path.join(self.tmp, "smd"))
        self.assertEqual(code, 0, err)
        self.assertEqual(read_json(os.path.join(self.tmp, "smd", "results.json"))["options"]["j_method"], "approx")


# ================================================================ contracts C2
class TestPlotTestsText(_Tmp):
    TABLES = {
        "k1": ("OR", "study;e1;n1;e2;n2\nA;4;120;11;130\n"),
        "k2": ("OR", "study;e1;n1;e2;n2\nA;4;120;11;130\nB;10;100;12;100\n"),
        "ident": ("MD", "study;m1;sd1;n1;m2;sd2;n2\nA;10;2;30;8;2;30\nB;10;2;30;8;2;30\nC;10;2;30;8;2;30\n"),
    }

    def test_small_and_degenerate_tables_conform(self):
        for name, (measure, text) in self.TABLES.items():
            with self.subTest(name):
                path = os.path.join(self.tmp, name + ".csv")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                out = os.path.join(self.tmp, "o_" + name)
                code, _, err = run_cli("analyze", "--data", path, "--measure", measure, "--out", out)
                self.assertEqual(code, 0, err)
                doc = read_json(os.path.join(out, "plot_data.json"))
                fu = doc["funnel"]
                if "tests_text" in fu:
                    self.assertTrue(all(isinstance(fu["tests_text"][lg], str) for lg in ("hu", "en")))
                self.assertEqual(contract_errors(doc, "ma.plot", 2), [])
                sp = api.spec_from_argv(["--data", path, "--measure", measure], project_root=self.tmp)
                self.assertEqual(contract_errors(api.analyze(sp, project_root=self.tmp)["plot"], "ma.plot", 2), [])

    def test_tests_text_kept_when_tests_run(self):
        doc = api.analyze(api.spec_from_argv(["--data", BCG, "--measure", "RR"], project_root=ROOT),
                          project_root=ROOT)["plot"]
        self.assertTrue(all(isinstance(doc["funnel"]["tests_text"][lg], str) for lg in ("hu", "en")))


# ================================================================ contracts C4 / correctness C8
class TestRowIndexSpace(_Tmp):
    TEXT = "study;e1;n1;e2;n2\nA;4;120;11;130\n\n;;;;\nB;10;100;12;100\nC;abc;100;5;90\nA;3;50;4;60\n"

    def test_file_producers_share_the_read_table_grid(self):
        path = os.path.join(self.tmp, "t.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.TEXT)
        h, rows, meta = api.read_table(path)
        self.assertEqual([r[0] for r in rows], ["A", "B", "C", "A"])
        self.assertEqual(meta["lines"], [2, 5, 6, 7])
        vt = api.validate_table(h, rows, "OR", lines=meta["lines"])
        vf = api.validate_file(path, "OR")

        def locs(doc):
            return sorted((f["code"], f["row"], tuple(f["rows"]), f["row_uid"], f["line"]) for f in doc["findings"])
        self.assertEqual(locs(vt), locs(vf))
        self.assertEqual([(e["study"], e["row"]) for e in vf["excluded"]], [("C", 2)])
        sp = api.spec_from_argv(["--data", path, "--measure", "OR"], project_root=self.tmp)
        for view in (api.analyze(sp, project_root=self.tmp),
                     api.analyze(sp, table={"header": h, "rows": rows, "lines": meta["lines"]},
                                 project_root=self.tmp)):
            self.assertEqual([(e["study"], e["row"], e["row_uid"]) for e in view["excluded"]],
                             [(e["study"], e["row"], e["row_uid"]) for e in vf["excluded"]])
            for st in view["plot"]["studies"]:
                self.assertEqual(rows[st["row_index"]][0], st["label"])
        uids = {f["row_uid"]: f["row"] for f in vf["findings"] if f["row"] is not None}
        for st in api.analyze(sp, project_root=self.tmp)["plot"]["studies"]:
            if st["row_uid"] in uids:
                self.assertEqual(uids[st["row_uid"]], st["row_index"])

    def test_draft_rows_keep_client_positions_everywhere(self):
        h = ["study", "e1", "n1", "e2", "n2", "rob"]
        raw = [["A", "4", "120", "11", "130", "low"], [], ["B", "", "100", "12", "100", "high"],
               ["C", "8", "90", "15", "95", "low"], ["D", "20", "200", "25", "210", "low"]]
        vt = api.validate_table(h, raw, "RR")
        b_row = [f["row"] for f in vt["findings"] if f["study"] == "B" and f["code"] == "V002"]
        self.assertEqual(b_row, [2])
        sp = api.spec_from_argv(["--data", BCG, "--measure", "RR"], project_root=ROOT)
        view = api.analyze(sp, table={"header": h, "rows": raw}, project_root=ROOT)
        self.assertEqual([(e["study"], e["row"]) for e in view["excluded"]], [("B", 2)])
        self.assertEqual([(s["label"], s["row_index"]) for s in view["plot"]["studies"]],
                         [("A", 0), ("C", 3), ("D", 4)])


# ================================================================ contracts C5
class TestRawTableRoundTrip(_Tmp):
    CASES = [
        b"study;e1;n1;e2;n2\r\nA;4;120;11;130\r\nB;10;100;12;100\r\n;;;;\r\n;;;;\r\n",
        b"study;e1;n1;e2;n2\r\nA;4;120;11;130\r\n;;;;\r\nB;10;100;12;100\r\n",
        b"study;e1;n1;e2;n2\r\nA;4;120;11;130\r\nB;10;100;12;100\r\n\r\n",
        b";;;;\r\n\r\nstudy;e1;n1;e2;n2\r\n\r\nA;4;120;11;130\r\n  \r\nB;10;100;12;100",
        b"study;e1\r\n;;\r\n",
        b"\xef\xbb\xbfstudy,e1\nA,1\n,\n\nB,2\n",
    ]

    def test_unchanged_table_writes_back_identical_bytes(self):
        for i, raw in enumerate(self.CASES):
            with self.subTest(i):
                src = os.path.join(self.tmp, "in%d.csv" % i)
                with open(src, "wb") as fh:
                    fh.write(raw)
                h, rows, meta = api.read_table(src)
                self.assertTrue(all(any(c.strip() for c in r) for r in rows), rows)
                dst = os.path.join(self.tmp, "out%d.csv" % i)
                self.assertEqual(api.write_table(dst, h, rows, meta), meta["sha256"])
                with open(dst, "rb") as fh:
                    self.assertEqual(fh.read(), raw)
                self.assertEqual(meta["sha256"], hashlib.sha256(raw).hexdigest())

    def test_edited_table_drops_stale_gaps_but_keeps_format(self):
        src = os.path.join(self.tmp, "t.csv")
        with open(src, "wb") as fh:
            fh.write(self.CASES[1])
        h, rows, meta = api.read_table(src)
        rows.append(["C", "1", "10", "2", "10"])           # új sor: a régi gaps már nem illeszkedik
        data = tableio.render_raw(h, rows, meta)
        self.assertEqual(data, b"study;e1;n1;e2;n2\r\nA;4;120;11;130\r\nB;10;100;12;100\r\nC;1;10;2;10\r\n")


# ================================================================ contracts C6
class TestNumberTextConvention(unittest.TestCase):
    def req(self, kind, inputs, target=None):
        r = {"schema": "szk.ma.convert-request/v1", "kind": kind, "inputs": inputs}
        if target is not None:
            r["target"] = target
        return r

    def test_outputs_text_follows_display_text(self):
        r = api.convert(self.req("median_to_mean_sd", {"n": 50, "median": 1, "q1": 0.2, "q3": 5}))
        self.assertEqual(r["outputs_text"]["mean"], {"hu": "2.132", "en": "2.132"})
        self.assertEqual(r["cell_text"], {"mean": "2,132", "sd": "3,664"})       # alap: a munkapad korábbi ','-je
        neg = api.convert(self.req("logor_to_d", {"y": -0.5, "v": 0.04}))
        self.assertEqual(neg["outputs_text"]["y"], {"hu": "-0.2757", "en": "−0.2757"})
        # ugyanaz a konvenció, mint a display_text-é (hu: tizedespont és kötőjel, en: U+2212)
        disp = plots.display_text(-0.41, -0.9, 0.1)
        self.assertNotIn(",", disp["hu"].replace("; ", ""))
        self.assertTrue(disp["en"].startswith("−"))
        self.assertEqual(contract_errors(neg, "ma.convert-result", 1), [])

    def test_cell_text_uses_target_format(self):
        tgt = {"dataset": "03_adatok/o1.csv", "row_uid": "rabcd1", "fields": {"sd": "sd1"}}
        for extra, want in (({"decimal_mark": "."}, "3.029"), ({"decimal_mark": ","}, "3,029"),
                            ({"decimal_mark": None, "delimiter": ","}, "3.029"),
                            ({"decimal_mark": None, "delimiter": ";"}, "3,029")):
            req = self.req("se_to_sd", {"se": "0,42", "n": "52"}, dict(tgt, **extra))
            self.assertEqual(contract_errors(req, "ma.convert-request", 1), [])
            r = api.convert(req)
            self.assertEqual(r["cell_text"]["sd"], want, extra)
            self.assertAlmostEqual(tableio.parse_number(want), r["outputs"]["sd"], places=3)
            self.assertEqual(contract_errors(r, "ma.convert-result", 1), [])


# ================================================================ contracts C7
class TestSpecContractAgreement(unittest.TestCase):
    BASE = {"schema": "szk.ma.analysis-spec/v1", "name": "o1", "outcome": "o1", "data": {"path": "03_adatok/o1.csv"},
            "options": {"measure": "RR"}}

    def mutate(self, fn):
        s = json.loads(json.dumps(self.BASE))
        fn(s)
        return s

    def test_engine_never_accepts_what_the_contract_forbids(self):
        muts = {
            "sha_null": lambda s: s["data"].update(sha256=None),
            "filters_other": lambda s: s.update(filters={"other": []}),
            "cc_negative": lambda s: s["options"].update(cc=-1),
            "parent_upper": lambda s: s.update(parent="A"),
            "path_dir": lambda s: s["data"].update(path="dir/"),
            "sha_short": lambda s: s["data"].update(sha256="AB"),
            "bias_min_k_float": lambda s: s["options"].update(bias_min_k=2.0),
            "filter_blank_col": lambda s: s.update(filters={"exclude": [" =x"]}),
        }
        for name, fn in muts.items():
            with self.subTest(name):
                doc = self.mutate(fn)
                engine_ok = not S.validate_spec(doc)
                schema_ok = not contract_errors(doc, "ma.analysis-spec", 1)
                if engine_ok:
                    self.assertTrue(schema_ok, "a motor elfogadja, a szerződés tiltja")
        self.assertTrue(S.validate_spec(self.mutate(muts["sha_null"])))
        self.assertTrue(contract_errors(self.mutate(muts["cc_negative"]), "ma.analysis-spec", 1))

    def test_integral_float_is_an_integer(self):
        doc = self.mutate(lambda s: s["options"].update(bias_min_k=2.0))
        self.assertEqual(S.validate_spec(doc), [])
        opts = S.options_from_spec(doc)
        self.assertIs(type(opts["bias_min_k"]), int)
        self.assertEqual(opts["bias_min_k"], 2)
        self.assertTrue(S.validate_spec(self.mutate(lambda s: s["options"].update(bias_min_k=2.5))))

    def test_generated_minimum_in_contract(self):
        self.assertEqual(K.load("ma.analysis-spec", 1)["properties"]["options"]["properties"]["cc"]["minimum"], 0)
        self.assertTrue(K.sync(write=False))


# ================================================================ contracts C8
class TestExploreRunJson(_Tmp):
    def test_cli_run_without_project_has_no_files(self):
        out = os.path.join(self.tmp, "x")
        code, stdout, err = run_cli("analyze", "--data", BCG, "--measure", "RR", "--out", out, "--json-summary")
        self.assertEqual(code, 0, err)
        run = read_json(os.path.join(out, "run.json"))
        self.assertEqual(json.loads(stdout), run)
        self.assertEqual((run["mode"], run["run_id"]), ("explore", None))
        self.assertNotIn("files", run)
        self.assertEqual(contract_errors(run, "ma.run", 1), [])
        self.assertTrue(os.path.isfile(os.path.join(out, "results.json")))

    def test_commit_run_lists_files(self):
        p, data = self.project()
        out = os.path.join(p, "05_elemzes", "o1", "primary")
        code, _, err = run_cli("analyze", "--data", data, "--measure", "RR", "--out", out, "--project", p)
        self.assertEqual(code, 0, err)
        run = read_json(os.path.join(out, "run.json"))
        self.assertEqual(run["mode"], "commit")
        self.assertEqual(run["files"]["results"]["path"], "05_elemzes/o1/primary/results.json")


# ================================================================ contracts C9
class TestWideTransformedAxis(_Tmp):
    def test_zcor_wide_domain_gets_readable_ticks(self):
        path = os.path.join(self.tmp, "c.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("study;r;n\nA;0.3;50\nB;0.99;40\nC;-0.5;60\nD;0.1;100\n")
        out = os.path.join(self.tmp, "o")
        code, _, err = run_cli("analyze", "--data", path, "--measure", "ZCOR", "--out", out)
        self.assertEqual(code, 0, err)
        axis = read_json(os.path.join(out, "plot_data.json"))["axis"]
        lo, hi = axis["domain"]
        self.assertGreater(hi - lo, 10)                      # a széles tartomány (HKSJ PI, k = 4)
        self.assertGreaterEqual(len(axis["ticks"]), 3)
        for t in axis["ticks"]:
            self.assertTrue(lo <= t["at"] <= hi)
            self.assertAlmostEqual(plots.back_transform("ZCOR", t["at"]), float(t["text"]), places=9)
        with open(os.path.join(out, "forest.svg"), encoding="utf-8") as fh:
            svg = fh.read()
        for t in axis["ticks"]:
            self.assertIn(">%s</text>" % t["text"], svg)

    def test_narrow_axes_unchanged(self):
        ax = plots._Axis(-0.6, 0.9, 0, 300, measure="ZCOR")
        self.assertEqual([lab for _, lab in ax.ticks()], [lab for _, lab in ax._spaced(ax._transformed_ticks(), 30)])


# ================================================================ correctness C2
class TestRunGroups(_Tmp):
    TEXT = ("study;e1;n1;e2;n2;rob;estimated\nA;4;120;11;130;low;nem\nB;10;100;12;100;low;igen\n"
            "C;8;90;15;95;high;nem\nD;20;200;25;210;low;nem\nE;3;60;9;65;low;nem\n")

    def analyze(self, p, *extra, **kw):
        code, _, err = run_cli("analyze", "--data", "03_adatok/o1.csv", "--measure", "RR", *extra, "--project", ".",
                               cwd=p, **kw)
        self.assertEqual(code, 0, err)

    def test_later_sensitivity_run_does_not_hide_stale_primary(self):
        p, data = self.project(data="o1.csv", text=self.TEXT)
        self.analyze(p, "--out", "05_elemzes/o1/primary")
        edit_first_cell(data, "A;4;", "A;5;")
        self.analyze(p, "--exclude", "rob=high", "--out", "05_elemzes/o1/sens")
        rep = A.project_audit(p, stage="FINAL")
        x = codes(rep, "X001")
        self.assertEqual([f["artifacts"][0] for f in x], ["05_elemzes/o1/primary/run.json"])
        self.assertEqual(codes(rep, "X006"), [])
        with self.assertRaises(ValueError):
            A.require_gate(p)

    def test_suggested_child_commands_do_not_cycle(self):
        p, _ = self.project(data="o1.csv", text=self.TEXT)
        self.analyze(p, "--out", "05_elemzes/o1/primary")
        for code_ in ("X005", "X006"):
            x = codes(A.project_audit(p), code_)
            self.assertEqual(len(x), 1, code_)
            code, _, err = run_cli(*x[0]["suggested_command"][1:], cwd=p)
            self.assertEqual(code, 0, err)
        rep = A.project_audit(p)
        self.assertEqual([f["code"] for f in rep["findings"] if f["code"] in ("X005", "X006")], [])

    def test_same_second_runs_are_separate_groups(self):
        p, _ = self.project(data="o1.csv", text=self.TEXT)
        with mock.patch.object(cli, "datetime", frozen()):
            self.analyze(p, "--out", "05_elemzes/o1/primary")
            self.analyze(p, "--exclude", "rob=high", "--out", "05_elemzes/o1/zz_rob")
            self.analyze(p, "--exclude", "estimated=igen", "--out", "05_elemzes/o1/aa_est")
        rep = A.project_audit(p)
        self.assertEqual([f["code"] for f in rep["findings"] if f["code"] in ("X001", "X005", "X006")], [])
        ids = [read_json(os.path.join(p, "05_elemzes", "o1", d, "run.json"))["run_id"]
               for d in ("primary", "zz_rob", "aa_est")]
        self.assertEqual(len(set(ids)), 3)


# ================================================================ correctness C5
class TestX022NumberFormat(_Tmp):
    def test_thousands_grouped_cells_are_reconcilable(self):
        root = os.path.join(self.tmp, "px22")
        projekt.init(root, "T", "Q")
        text = ("row_uid;study;m1;sd1;n1;m2;sd2;n2;rob;forras_oldal\nra001;A;12,5;3,1;1.234;11,0;3,0;1.250;low;p3\n"
                "ra002;B;13,5;3,2;\"1.234,5\";12,0;3,1;80;low;p4\n")
        p = os.path.join(root, "03_adatok", "o1.csv")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        cells = []
        for u, vals in (("ra001", {"m1": "12,5", "n1": "1.234", "n2": "1.250"}), ("ra002", {"m1": "13,5",
                                                                                           "n1": "1.234,5"})):
            for f, v in vals.items():
                cells.append({"row_uid": u, "field": f, "value_as_entered": v, "method": "reported",
                              "estimated": False, "source": {"page": 3}})
        with open(os.path.join(root, "03_adatok", "o1.prov.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": "szk.ma.provenance/v1", "table": "03_adatok/o1.csv",
                       "table_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "cells": cells}, fh)
        with open(os.path.join(root, "ma-projekt.json"), "w", encoding="utf-8") as fh:
            json.dump({"title": "T", "data_class": "A", "review_type": "intervention",
                       "outcomes": [{"id": "o1", "name": "x", "data": "03_adatok/o1.csv", "measure": "MD"}]}, fh)
        self.assertEqual(codes(A.project_audit(root), "X022"), [])
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text.replace(";low;p4", ";high;p4"))    # a számokat nem érintő szerkesztés
        self.assertEqual(codes(A.project_audit(root), "X022"), [])
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text.replace(";1.250;", ";1.260;"))       # valódi eltérés: jelezni kell
        x = codes(A.project_audit(root), "X022")
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]["cells"], [{"row_uid": "ra001", "field": "n2"}])

    def test_values_match_uses_table_context(self):
        meta = {"decimal_mark": ",", "delimiter": ";"}
        self.assertTrue(A._values_match(1234.0, "1.234", "n1", None, meta))
        self.assertTrue(A._values_match(12.5, "12,5", "m1", None, meta))
        self.assertFalse(A._values_match(1.234, "1.234", "n1", None, meta))
        self.assertTrue(A._values_match(None, "1.234,5", "n1", "1.234,5", meta))
        self.assertFalse(A._values_match(None, "1.234,5", "n1", "1.234,6", meta))


# ================================================================ correctness C6
class TestOutcomeSlug(_Tmp):
    def test_slugged_outcome_is_one_outcome(self):
        root = os.path.join(self.tmp, "pslug")
        projekt.init(root, "T", "Q")
        with open(os.path.join(root, "03_adatok", "mortality.csv"), "w", encoding="utf-8") as fh:
            fh.write("study;e1;n1;e2;n2;rob\nA;4;123;11;139;low\nB;6;306;29;303;high\nC;3;231;11;220;low\n"
                     "D;62;13598;248;12867;low\n")

        def mk(name, purpose, filters=None, parent=None):
            sp = {"schema": "szk.ma.analysis-spec/v1", "name": name, "outcome": "Mortality", "purpose": purpose,
                  "prespecified": True, "parent": parent, "data": {"path": "03_adatok/mortality.csv"},
                  "options": {"measure": "RR"}}
            if filters:
                sp["filters"] = filters
            path = os.path.join(root, S.spec_relpath(name))
            S.save_spec(path, sp)
            return path
        with mock.patch.object(api, "datetime", frozen()):
            api.analyze(mk("mort_primary", "primary"), mode="commit", project_root=root)
            api.analyze(mk("mort_norob", "sensitivity", {"exclude": ["rob=high"]}, "mort_primary"), mode="commit",
                        project_root=root)
        self.assertEqual(os.listdir(os.path.join(root, "05_elemzes")).count("mortality"), 1)
        rep = A.project_audit(root)
        self.assertEqual(rep["outcomes"], ["Mortality"])
        self.assertEqual([f["code"] for f in rep["findings"] if f["code"] in ("X001", "X005", "X006")], [])
        self.assertFalse([n for n in rep["not_checked"] if n["code"] == "X001"])


# ================================================================ correctness C7
class TestApiCommitPaths(_Tmp):
    def test_absolute_project_root_writes_relative_data_path(self):
        p, _ = self.project(data="o1.csv")
        sp = S.spec_from_argv(["analyze", "--data", os.path.join(p, "03_adatok", "o1.csv"), "--measure", "RR"], p,
                              name="o1_primary", outcome="o1", pin_data=True)
        path = os.path.join(p, S.spec_relpath("o1_primary"))
        S.save_spec(path, sp)
        view = api.analyze(path, mode="commit", project_root=os.path.abspath(p), date=DATE)
        d = os.path.join(p, os.path.dirname(view["run"]["files"]["results"]["path"]))
        res = read_json(os.path.join(d, "results.json"))
        self.assertEqual(res["input"]["path"], "03_adatok/o1.csv")
        self.assertEqual(view["results"]["input"]["path"], res["input"]["path"])
        with open(os.path.join(d, "report.md"), encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("Adat: `03_adatok/o1.csv`", report)
        self.assertNotIn(os.path.abspath(p), report)
        # ugyanaz a spec a CLI-ből (abszolút --project-tel is) bájtra azonos kimenetet ad
        cli_dir = os.path.join(self.tmp, "cli")
        code, _, err = run_cli("analyze", "--spec", path, "--out", cli_dir, "--project", os.path.abspath(p), "--date",
                               DATE, "--run-id", view["run"]["run_id"])
        self.assertEqual(code, 0, err)
        for f in ("results.json", "report.md", "plot_data.json", "effect_sizes.csv", "forest.svg"):
            with open(os.path.join(d, f), "rb") as x, open(os.path.join(cli_dir, f), "rb") as y:
                self.assertEqual(x.read(), y.read(), f)


# ================================================================ correctness C3 / C9 (dokumentáció)
class TestDocs(unittest.TestCase):
    def _text(self, *parts):
        path = os.path.join(*parts)
        if not os.path.isfile(path):
            self.skipTest("nincs meg: %s" % path)
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_agent_docs_use_an_audited_run_layout(self):
        for rel in (("skills", "metaanalizis", "SKILL.md"), ("agents", "ma-ertekelo.md")):
            text = self._text(REPO, ".claude", *rel)
            outs = re.findall(r"--out <mappa>/05_elemzes/(\S+?)`", text)
            self.assertTrue(outs, rel)
            for o in outs:
                self.assertRegex(o, r"^<kimenet>/[^/\s]+$", "%s: --out <mappa>/05_elemzes/%s" % (rel[-1], o))

    def test_install_doc_covers_zip_route(self):
        text = self._text(ROOT, "TELEPITES.md")
        zip_part = text[text.index("**ZIP-ből:**"):text.index("**Frissítés:**")]
        self.assertIn("helyben", zip_part)
        self.assertIn("metaanalizis-asszisztens/tudasbazis/tudasbazis.sqlite", zip_part)
        self.assertIn("METAELEMZES_KB", zip_part)
        self.assertIn('Bash(python "<kicsomagolt mappa>/metaanalizis-asszisztens/ma.py" *)', text)


if __name__ == "__main__":
    unittest.main()
