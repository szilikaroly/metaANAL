# -*- coding: utf-8 -*-
"""Regressziós tesztek a 2. review-kör beolvasási / validálási / CLI-javításaihoz.

Minden teszt neve a megállapítás azonosítóját viseli (pl. R2_ROB_04 = robustness:R2-ROB-04,
KBP_03 = kb_code:KBP-03, AG_04 = agents_e2e:AG-04). Az AG-04 orákuluma az R metafor 4.4
(escalc RR + rma REML, test = "knha", a két magas RoB-ú vizsgálat nélkül).
"""
import csv
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _helpers import ROOT, assert_close
from metaelemzes import cli, kb, pipeline, tableio
from metaelemzes import effect_sizes as E
from metaelemzes import validate as V

BCG = os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv")


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:      # argparse-hiba
            code = exc.code
    return code, buf.getvalue(), err.getvalue()


def codes(findings, code):
    return [f for f in findings if f["code"] == code]


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def csv(self, text, name="adat.csv"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def read(self, p):
        with open(p, encoding="utf-8") as fh:
            return fh.read()


# ------------------------------------------------------------ R2-ROB-04
SID = "Study ID,m1,sd1,n1,m2,sd2,n2\nSmith 2001,10,2,20,8,2,20\nJones 2002,11,2,25,8,2,20\nKim 2003,12,3,25,9,3,25\n"


class TestR2ROB04StudyIdLabels(_Tmp):
    def test_R2_ROB_04_study_id_column_gives_labels(self):
        for head in ("Study ID", "StudyID", "Trial", "Trial ID", "study_id"):
            rows, meta = tableio.read_table(self.csv(SID.replace("Study ID", head, 1)))
            self.assertEqual([r["study"] for r in rows], ["Smith 2001", "Jones 2002", "Kim 2003"], head)
            self.assertEqual([r["study_id"] for r in rows], ["Smith 2001", "Jones 2002", "Kim 2003"], head)
            self.assertEqual(meta["label_column"], head)
            self.assertFalse(codes(V.validate(rows, "MD", meta), "V029"), head)

    def test_R2_ROB_04_cli_outputs_use_names(self):
        od = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", self.csv(SID), "--measure", "MD", "--out", od)
        self.assertEqual(code, 0, so + se)
        with open(os.path.join(od, "effect_sizes.csv"), encoding="utf-8") as fh:
            labels = [r[0] for r in list(csv.reader(fh))[1:]]
        self.assertEqual(labels, ["Smith 2001", "Jones 2002", "Kim 2003"])
        self.assertIn(">Smith 2001<", self.read(os.path.join(od, "forest.svg")))
        self.assertNotIn("#1", self.read(os.path.join(od, "report.md")))

    def test_R2_ROB_04_multi_arm_study_id_labels_are_unique(self):
        p = self.csv("Trial,m1,sd1,n1,m2,sd2,n2\nSmith 2001,10,2,20,8,2,20\nSmith 2001,12,2,20,8,2,20\n"
                     "Kim 2003,12,3,25,9,3,25\n")
        rows, meta = tableio.read_table(p)
        self.assertEqual([r["study"] for r in rows], ["Smith 2001 (1)", "Smith 2001 (2)", "Kim 2003"])
        f = V.validate(rows, "MD", meta)
        self.assertEqual([x["study"] for x in codes(f, "V017")], ["Smith 2001"])     # a közös kontroll jelzése marad
        # a közös azonosító (több kar vagy kettős közlés?) V007-et kap, a sorszám-utótag ellenére is
        self.assertEqual([x["study"] for x in codes(f, "V007")], ["Smith 2001"])
        self.assertIn("Trial azonosítóval", codes(f, "V007")[0]["detail"])

    def test_R2_ROB_04_separate_label_column_wins(self):
        p = self.csv("study,Trial,yi,vi\nA arm 1,T1,0.1,0.04\nA arm 2,T1,0.2,0.05\nB,T2,0.3,0.03\n")
        rows, meta = tableio.read_table(p)
        self.assertEqual([r["study"] for r in rows], ["A arm 1", "A arm 2", "B"])
        self.assertNotIn("label_column", meta)

    def test_R2_ROB_04_missing_label_is_warned(self):
        p = self.csv("Reference,yi,vi\nSmith 2001,0.1,0.04\nJones 2002,0.2,0.05\nKim 2003,0.3,0.03\n")
        rows, meta = tableio.read_table(p)
        f = codes(V.validate(rows, "GEN", meta), "V029")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "warning")
        self.assertIn("#1, #2, #3", f[0]["detail"])
        # egyetlen üres címkecella is jelzett
        rows, meta = tableio.read_table(self.csv("study,yi,vi\nA,0.1,0.04\n,0.2,0.05\nC,0.3,0.03\n"))
        self.assertIn("1 sor címke nélkül: #2", codes(V.validate(rows, "GEN", meta), "V029")[0]["detail"])


# ------------------------------------------------------------ R2-ROB-05
class TestR2ROB05NoAnalysableStudy(_Tmp):
    def test_R2_ROB_05_validate_fails_like_analyze_when_k0(self):
        p = self.csv("study,e1,n1,e2,n2\nA,0,10,0,10\nB,0,20,0,20\nC,0,5,0,5\n")
        code, so, se = run_cli("validate", "--data", p, "--measure", "OR")
        self.assertEqual(code, 1, so + se)
        self.assertIn("[V026] error", so)
        code, so, se = run_cli("validate", "--data", p, "--measure", "OR", "--json")
        self.assertEqual(code, 1)
        res = json.loads(so)
        self.assertEqual((res["summary"]["error"], res["k"]), (1, 0))
        code, so, se = run_cli("analyze", "--data", p, "--measure", "OR", "--out", os.path.join(self.tmp, "o"))
        self.assertEqual(code, 1)
        self.assertIn("1 hiba", so)

    def test_R2_ROB_05_rom_nonpositive_and_header_only(self):
        p = self.csv("study,m1,sd1,n1,m2,sd2,n2\nA,-1,2,20,8,2,20\nB,0,2,20,8,2,20\n")
        self.assertEqual(run_cli("validate", "--data", p, "--measure", "ROM")[0], 1)
        rows, meta = tableio.read_table(self.csv("study,m1,sd1,n1,m2,sd2,n2\n", "h.csv"))
        f = V.validate(rows, "MD", meta)
        self.assertEqual([x["severity"] for x in codes(f, "V026")], ["error"])
        self.assertFalse(codes(f, "V001"))

    def test_R2_ROB_05_no_v026_when_analysable(self):
        rows, meta = tableio.read_table(self.csv(SID))
        self.assertFalse(codes(V.validate(rows, "MD", meta), "V026"))


# ------------------------------------------------------------ R2-ROB-07
class TestR2ROB07Overflow(_Tmp):
    def test_R2_ROB_07_overflowing_literal_is_v003(self):
        with self.assertRaises(ValueError):
            tableio.parse_number("1e400")
        with self.assertRaises(ValueError):
            tableio.parse_number("-1,5e999")
        self.assertEqual(tableio.parse_number("1e-400"), 0.0)
        p = self.csv("study,yi,vi\nA,1e400,0.1\nB,0.2,0.1\nC,0.3,0.1\n")
        rows, meta = tableio.read_table(p)
        self.assertIsNone(rows[0]["yi"])
        f = V.validate(rows, "GEN", meta)
        self.assertEqual([x["study"] for x in codes(f, "V003")], ["A"])
        self.assertIn("nem véges", codes(f, "V003")[0]["detail"])
        code, so, se = run_cli("analyze", "--data", p, "--measure", "GEN", "--out", os.path.join(self.tmp, "o"))
        self.assertEqual(code, 0, so + se)
        self.assertIn("Összesített becslés: 0.25 [", so)
        self.assertIn("k = 2", so)

    def test_R2_ROB_07_infinite_n_does_not_crash_validate(self):
        rows = [{"study": "A", "m1": 1, "sd1": 1, "n1": float("inf"), "m2": 0, "sd2": 1, "n2": 10},
                {"study": "B", "m1": 1, "sd1": 1, "n1": float("nan"), "m2": 0, "sd2": 1, "n2": 10},
                {"study": "C", "m1": 2, "sd1": 1, "n1": 10, "m2": 0, "sd2": 1, "n2": 10}]
        f = V.validate(rows, "MD")             # korábban: OverflowError / ValueError (int(inf), int(nan))
        self.assertEqual(sorted(x["study"] for x in codes(f, "V004")), ["A", "B"])

    def test_R2_ROB_07_overflowing_sd_excludes_only_that_row(self):
        # a compute ArithmeticError-ágát feltétel nélkül védjük (korábban skipUnless mögött)
        es = E.compute([{"study": "B", "m1": 1, "sd1": 1e200, "n1": 10, "m2": 0, "sd2": 1, "n2": 10}], "MD")
        self.assertEqual((len(es), len(es.excluded)), (0, 1))
        p = self.csv("study,m1,sd1,n1,m2,sd2,n2\nA,1,1,10,0,1,10\nB,1,1e200,10,0,1,10\nC,2,1,10,0,1,10\n"
                     "D,1.5,1,10,0,1,10\n")
        code, so, se = run_cli("validate", "--data", p, "--measure", "MD")
        self.assertNotIn("OverflowError: (34", se)
        self.assertIn("k = 3", so)
        self.assertIn("B", "".join(l for l in so.splitlines() if "kimarad" in l))


# ------------------------------------------------------------ R2-ROB-08
class TestR2ROB08DuplicateHeaders(_Tmp):
    def _m1(self, header_m1s):
        p = self.csv("study,%s,sd1,n1,m2,sd2,n2\nA,1,100,1,10,0,1,10\nB,1,100,1,10,0,1,10\nC,2,100,1,10,0,1,10\n"
                     % header_m1s)
        return p, tableio.read_table(p)

    def test_R2_ROB_08_first_identical_column_wins_and_is_warned(self):
        p, (rows, meta) = self._m1("m1,m1")
        self.assertEqual([r["m1"] for r in rows], [1, 1, 2])
        self.assertEqual([r["m1.1"] for r in rows], [100, 100, 100])
        f = codes(V.validate(rows, "MD", meta), "V028")
        self.assertEqual(len(f), 1)
        self.assertIn("'m1' (3. oszlop)", f[0]["detail"])
        self.assertIn("'m1' (2. oszlop)", f[0]["detail"])
        code, so, se = run_cli("analyze", "--data", p, "--measure", "MD", "--out", os.path.join(self.tmp, "o"),
                               "--no-plots")
        self.assertEqual(code, 0, so + se)
        self.assertNotIn("Összesített becslés: 100", so)

    def test_R2_ROB_08_case_and_space_variants(self):
        for head in ('M1,m1', 'm1,"m1 "', 'mean1,m1'):
            _, (rows, meta) = self._m1(head)
            want = [100, 100, 100] if head == "mean1,m1" else [1, 1, 2]     # 'mean1,m1': az 'm1' az elsőbbségi szinonima
            self.assertEqual([r["m1"] for r in rows], want, head)
            self.assertEqual(len(codes(V.validate(rows, "MD", meta), "V028")), 1, head)
        self.assertEqual(tableio.canonical_columns(["M1", "m1"]), {"M1": "m1", "m1": "m1.1"})
        self.assertEqual(tableio.canonical_columns(["M1", "m1", "m1.1"]), {"M1": "m1", "m1": "m1.2", "m1.1": "m1.1"})

    def test_R2_ROB_08_label_alias_priority_is_not_a_duplicate(self):
        rows, meta = tableio.read_table(self.csv("Szerző,ID,yi,vi\nSmith,1,0.1,0.04\nKim,2,0.2,0.05\n"))
        self.assertEqual([r["study"] for r in rows], ["Smith", "Kim"])
        self.assertFalse(codes(V.validate(rows, "GEN", meta), "V028"))


# ------------------------------------------------------------ R2-ROB-13
class TestR2ROB13ExportBareName(_Tmp):
    def test_R2_ROB_13_export_to_bare_file_name(self):
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        self.assertEqual(run_cli("project", "init", "prjx", "--title", "T")[0], 0)
        code, so, se = run_cli("project", "export", "prjx", "--out", "naplo.md")
        self.assertEqual(code, 0, so + se)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "naplo.md")))


# ------------------------------------------------------------ R2-ROB-14
class TestR2ROB14NoOverwriteOfInput(_Tmp):
    def test_R2_ROB_14_es_refuses_to_overwrite_data(self):
        p = self.csv(SID, "v.csv")
        for out in (p, os.path.join(self.tmp, ".", "v.csv")):
            code, so, se = run_cli("es", "--data", p, "--measure", "MD", "--out", out)
            self.assertEqual(code, 1, so + se)
            self.assertIn("megegyezik a bemeneti adatfájllal", se)
            self.assertEqual(self.read(p), SID)
        code, so, se = run_cli("es", "--data", p, "--measure", "MD", "--out", os.path.join(self.tmp, "es.csv"))
        self.assertEqual(code, 0, so + se)

    def test_R2_ROB_14_analyze_refuses_to_overwrite_data(self):
        od = os.path.join(self.tmp, "res")
        os.makedirs(od)
        for name in ("effect_sizes.csv", "report.md", "forest.svg"):
            p = os.path.join(od, name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(SID)
            code, so, se = run_cli("analyze", "--data", p, "--measure", "MD", "--out", od)
            self.assertEqual(code, 1, so + se)
            self.assertIn("megegyezik a bemeneti adatfájllal", se)
            self.assertEqual(self.read(p), SID)
            os.remove(p)


# ------------------------------------------------------------ R2-ROB-17
class TestR2ROB17OptionRanges(_Tmp):
    @classmethod
    def setUpClass(cls):
        cls.kbdir = tempfile.mkdtemp()
        cls.db = os.path.join(cls.kbdir, "kb.sqlite")
        kb.build(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.kbdir, True)

    def test_R2_ROB_17_invalid_values_are_argparse_errors(self):
        data = self.csv(SID)
        prj = os.path.join(self.tmp, "pg")
        self.assertEqual(run_cli("project", "init", prj, "--title", "t")[0], 0)
        bad = [("kb", "--db", self.db, "sql", "select 1", "--max-rows", "-1"),
               ("kb", "--db", self.db, "sql", "select 1", "--timeout", "-1"),
               ("kb", "--db", self.db, "search", "heterogeneity", "--limit", "0"),
               ("project", "grade", prj, "--outcome", "o", "--certainty", "high", "--k", "-5"),
               ("project", "grade", prj, "--outcome", "o", "--certainty", "high", "--participants", "-10"),
               ("analyze", "--data", data, "--measure", "MD", "--cc", "-1"),
               ("es", "--data", data, "--measure", "MD", "--cc", "nan"),
               ("es", "--data", data, "--measure", "MD", "--cc", "inf")]
        for args in bad:
            code, so, se = run_cli(*args)
            self.assertEqual(code, 2, args)
            self.assertIn("szükséges", se, args)
        self.assertEqual(run_cli("project", "list", prj, "grades")[1].strip(), "[]")

    def test_R2_ROB_17_valid_boundaries_still_work(self):
        code, so, se = run_cli("kb", "--db", self.db, "sql", "select 1", "--max-rows", "0", "--timeout", "0")
        self.assertEqual(code, 0, se)
        self.assertNotIn("csonkolva", se)
        code, so, se = run_cli("es", "--data", self.csv(SID), "--measure", "MD", "--cc", "0",
                               "--out", os.path.join(self.tmp, "es.csv"))
        self.assertEqual(code, 0, so + se)


# ------------------------------------------------------------ KBP-03
COMP = {"identified_databases": 200, "identified_registers": 10, "identified_other": 0, "dedup_removed": 40,
        "removed_before_screening_n": 0, "screened": 170, "excluded_screening": 130, "sought_for_retrieval": 40,
        "not_retrieved": 2, "assessed_eligibility": 38, "excluded_eligibility": 30,
        "excluded_eligibility_reasons": {"nem RCT": 20, "rossz kimenet": 10}, "included": 8, "undecided": 0}


def _md(a1, d1):
    return "\n".join([
        "| Lépés | Szám | Ellenőrzés |", "|---|---|---|",
        "| Azonosított rekordok — adatbázisok (n = A1) | %d | |" % a1,
        "| Azonosított rekordok — regiszterek (n = A2) | 10 | |",
        "| Eltávolítva szűrés előtt: duplikátum (n = D1), automatikus (n = D2), egyéb (n = D3) | %d / 0 / 0 | |" % d1,
        "| Szűrt rekordok (n = B) | 170 | |", "| Kizárt rekordok (n = C) | 130 | |",
        "| Teljes szövegre keresett (n = E) | 40 | |", "| Nem elérhető (n = F) | 2 | |",
        "| Értékelt teljes szöveg (n = G) | 38 | |",
        "| Kizárt jelentések (n = H) | 30 (nem RCT: 20; rossz kimenet: 10) | |",
        "| Bevont jelentések / vizsgálatok (n = J, n = I) | 8 / 7 | |"])


class TestKBP03PrismaSourcesCompared(_Tmp):
    def test_KBP_03_conflicting_composer_and_md_is_error(self):
        comp = self.csv(json.dumps(COMP, ensure_ascii=False), "prisma-flow.json")
        md = self.csv(_md(250, 90), "prisma_folyamat.md")
        # mindkét forrás önmagában konzisztens
        self.assertEqual(run_cli("prisma", "check", "--composer", comp)[0], 0)
        self.assertEqual(run_cli("prisma", "check", "--md", md)[0], 0)
        for order in (("--composer", comp, "--md", md), ("--md", md, "--composer", comp)):
            code, so, se = run_cli("prisma", "check", *order)
            self.assertEqual(code, 1, so + se)
            self.assertIn("HIBÁS", so)
            self.assertIn("identified_databases (A1): %s = 200, %s = 250" % (comp, md), so)
            self.assertIn("duplicates_removed (D1): %s = 40, %s = 90" % (comp, md), so)
            self.assertIn("%s + %s" % (comp, md), so)
        code, so, se = run_cli("prisma", "check", "--composer", comp, "--md", md, "--out-format", "json")
        res = json.loads(so)
        self.assertEqual(sorted(f["fields"][0] for f in res["findings"] if f["code"] == "P017"),
                         ["duplicates_removed", "identified_databases"])
        self.assertEqual(res["derived"]["identified_total"], 210)     # a composer a számok forrása (D-S04-009)

    def test_KBP_03_agreeing_sources_pass(self):
        comp = self.csv(json.dumps(COMP, ensure_ascii=False), "prisma-flow.json")
        md = self.csv(_md(200, 40), "prisma_folyamat.md")
        code, so, se = run_cli("prisma", "check", "--composer", comp, "--md", md)
        self.assertEqual(code, 0, so + se)
        self.assertNotIn("P017", so)


# ------------------------------------------------------------ KBP-13
class TestKBP13SearchScopeSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kbdir = tempfile.mkdtemp()
        cls.db = os.path.join(cls.kbdir, "kb.sqlite")
        kb.build(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.kbdir, True)

    def test_KBP_13_unknown_scope_is_error(self):
        code, so, se = run_cli("kb", "--db", self.db, "search", "heterogeneity", "--scope", "rules,chunks")
        self.assertEqual(code, 2)
        self.assertIn("ismeretlen kör: rules, chunks", se)
        self.assertIn("rule, knowledge, chunk", se)
        self.assertNotIn("Nincs találat", so)

    def test_KBP_13_scope_names_are_stripped(self):
        code, so, se = run_cli("kb", "--db", self.db, "search", "heterogeneity", "--scope", "rule, knowledge",
                               "--limit", "2")
        self.assertEqual(code, 0, se)
        self.assertIn("== SZABÁLYOK", so)
        self.assertIn("== TUDÁS", so)
        self.assertNotIn("== TELJES SZÖVEG", so)

    def test_KBP_13_source_filter_applies_to_rules(self):
        code, so, se = run_cli("kb", "--db", self.db, "search", "heterogeneity", "--source", "nonexistent_src",
                               "--limit", "2", "--json")
        self.assertEqual(json.loads(so).get("rule"), [])
        code, so, se = run_cli("kb", "--db", self.db, "search", "heterogeneity", "--source", "higgins2002",
                               "--limit", "5", "--json")
        for r in json.loads(so).get("rule") or []:
            self.assertIn("higgins2002", [x.strip() for x in r["source_ids"].split(",")], r["id"])


# ------------------------------------------------------------ AG-04
def _bcg_with_rob(tmp, values):
    """A BCG-példa rob oszloppal: values = {címkerészlet: rob érték}, a többi 'low'."""
    with open(BCG, encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    out = [rows[0] + ["rob"]]
    for r in rows[1:]:
        if r:
            out.append(r + [next((v for k, v in values.items() if k in r[0]), "low")])
    p = os.path.join(tmp, "bcg_rob.csv")
    with open(p, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, delimiter=";").writerows(out)
    return p


class TestAG04RobWording(_Tmp):
    def test_AG_04_official_wording_is_recognised(self):
        for v in ("High risk of bias", "Serious risk of bias", "Critical risk of bias", "Magas torzítási kockázat",
                  "high-risk", "HIGH"):
            self.assertTrue(tableio.values_equal(v, "high", "rob"), v)
            self.assertEqual(tableio.rob_category(v), "high", v)
        for v, cat in (("Low risk of bias", "low"), ("Moderate risk of bias", "some"), ("Some concerns", "some"),
                       ("Unclear risk of bias", "some"), ("No information", "no_info")):
            self.assertEqual(tableio.rob_category(v), cat, v)
            self.assertFalse(tableio.values_equal(v, "high", "rob"), v)
        self.assertTrue(tableio.values_equal("Low risk of bias", "low", "rob"))

    def test_AG_04_exclude_rob_high_matches_metafor(self):
        p = _bcg_with_rob(self.tmp, {"Frimodt": "High risk of bias", "Stein": "high"})
        rows, meta = tableio.read_table(p)
        self.assertEqual(sorted(x["study"] for x in codes(V.validate(rows, "RR", meta), "V019")),
                         ["Frimodt-Moller et al 1973", "Stein & Aronson 1953"])
        rep = []
        kept = tableio.apply_filters(rows, ["rob=high"], None, meta=meta, report=rep)
        self.assertEqual(rep[0]["removed"], 2)
        out, es = pipeline.run(kept, {"measure": "RR"}, meta)
        self.assertEqual(out["effect_sizes"]["k"], 11)
        # metafor 4.4: rma(yi, vi, method = "REML", test = "knha"), a két magas RoB-ú vizsgálat nélkül
        for got, want in zip(out["back_transformed"]["estimate_ci"], (0.4649032520, 0.2885196243, 0.7491172716)):
            assert_close(self, got, want, 1e-7)
        low = tableio.apply_filters(rows, None, ["rob=low"], meta=meta)
        self.assertEqual(len(low), 11)

    def test_AG_04_unrecognised_values_are_warned(self):
        p = _bcg_with_rob(self.tmp, {"Frimodt": "H", "Stein": "high"})
        rows, meta = tableio.read_table(p)
        rows[0]["estimated"] = "részben"
        rows[1]["estimated"] = "igen"
        f = codes(V.validate(rows, "RR", meta), "V027")
        self.assertEqual(sorted((x["study"], x["detail"]) for x in f),
                         [("Aronson 1948", "estimated = 'részben'"), ("Frimodt-Moller et al 1973", "rob = 'H'")])
        self.assertEqual({x["severity"] for x in f}, {"warning"})


# ------------------------------------------------------------ AG-09
class TestAG09KbRulesStageRanges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kbdir = tempfile.mkdtemp()
        cls.db = os.path.join(cls.kbdir, "kb.sqlite")
        kb.build(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.kbdir, True)

    def _ids(self, stage):
        code, so, se = run_cli("kb", "--db", self.db, "rules", "--stage", stage, "--agent", "reviewer", "--json")
        self.assertEqual(code, 0, se)
        return [r["rule_id"] for r in json.loads(so)]

    def test_AG_09_ranges_are_expanded(self):
        both = self._ids("S01") + self._ids("S02")
        self.assertTrue(both)
        self.assertEqual(self._ids("S01-S02"), both)
        self.assertEqual(self._ids("S01–S02"), both)
        self.assertEqual(self._ids("S07–S12"), [i for s in range(7, 13) for i in self._ids("S%02d" % s)])

    def test_AG_09_final_means_s14(self):
        self.assertEqual(self._ids("FINAL"), self._ids("S14"))

    def test_AG_09_unknown_stage_still_rejected(self):
        for st in ("S15", "foo", "S03-S01"):
            code, so, se = run_cli("kb", "--db", self.db, "rules", "--stage", st, "--agent", "reviewer")
            self.assertEqual(code, 1, st)
            self.assertIn("Ismeretlen szakasz", se)


if __name__ == "__main__":
    unittest.main()
