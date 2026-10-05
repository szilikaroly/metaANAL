# -*- coding: utf-8 -*-
"""Regressziós tesztek a 2. review-kör újraellenőrzésén hiányosnak talált javításokhoz.

Minden teszt neve a megállapítás azonosítóját viseli (pl. R2_NF_05 = stats_new:R2-NF-05,
DT_2 = metafor_fuzz:DT-2, ROB_15 = robustness:R2-ROB-15, KBP_03 = kb_code:KBP-03,
AG_04 = agents_e2e:AG-04, KB_R2_03 = kb_content:KB-R2-03). Az orákulum-értékek az R metafor 4.4-ből
(escalc, ranktest, qt) származnak, a forrást a megjegyzés adja meg.
"""
import json
import os
import random
import re
import shutil
import tempfile
import unittest

from _helpers import ROOT, assert_close
from metaelemzes import bias as B
from metaelemzes import conversions as C
from metaelemzes import models as M
from metaelemzes import pipeline, prisma, projekt, report, tableio
from metaelemzes import validate as V
import test_fixes_R2_content as R2C
from test_fixes_R2_content import ITEMS, SKILL, _read, agent_docs, item, run_cli

# Q < df (I² = 0): itt tér el a csonkolt és a csonkolatlan H/I²-CI
SG = [("A", 0.21, 0.04, "x"), ("B", 0.35, 0.03, "x"), ("C", 0.12, 0.05, "x"), ("D", 0.55, 0.06, "x"),
      ("E", 0.30, 0.02, "y"), ("F", 0.41, 0.045, "y"), ("G", 0.05, 0.035, "y"), ("H", 0.25, 0.05, "y")]
KOD = ("study,m1,sd1,n1,m2,sd2,n2,kod\nA,10,2,20,8,2,20,01\nB,11,2,25,8,2,20,1\nC,12,3,25,9,3,25,01\n"
       "D,10,2,40,9,2,40,1\nE,10,2,40,9,2,40,2\nF,13,2,40,9,2,40,2\n")
JI = "| Bevont közlemények (n = J) és vizsgálatok (n = I) | %s | |\n"
DROW = "| Szűrés előtt eltávolítva: duplikátumok (D1), automatikusan kizárt (D2), egyéb (D3) | %s | |\n"


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

    def results(self, out):
        return json.loads(_read(os.path.join(out, "results.json")))


# ================================================================ R2-NF-05: trim-and-fill H/I²-középpont
class TestR2NF05TrimFillHtCentre(_Tmp):
    def test_R2_NF_05_trimfill_adjusted_follows_h_centre(self):
        yi = [r[1] for r in SG]
        vi = [r[2] for r in SG]
        tf = B.trim_and_fill(yi, vi, h_centre="untruncated")
        het = tf.adjusted.heterogeneity
        self.assertEqual(het["H_ci_centre"], "untruncated")
        ref = M.meta_analysis(yi + tf.filled_yi, vi + tf.filled_vi, h_centre="untruncated").heterogeneity
        self.assertEqual(het["I2_ci_HT"], ref["I2_ci_HT"])
        self.assertEqual(B.trim_and_fill(yi, vi).adjusted.heterogeneity["H_ci_centre"], "truncated")

    def test_R2_NF_05_every_results_block_uses_requested_centre(self):
        data = self.csv("study,yi,vi,g\n" + "".join("%s,%r,%r,%s\n" % r for r in SG))
        out = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", data, "--measure", "GEN", "--subgroup", "g",
                               "--ht-centre", "untruncated", "--out", out, "--no-plots")
        self.assertEqual(code, 0, se)
        res = self.results(out)
        found = []

        def walk(o, path):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "H_ci_centre":
                        found.append((path, v))
                    walk(v, path + "." + k)
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    walk(v, "%s[%d]" % (path, i))
        walk(res, "")
        self.assertIn(".bias.trimfill.adjusted.heterogeneity", [p for p, _ in found])
        self.assertEqual([p for p, v in found if v != "untruncated"], [])
        assert_close(self, res["bias"]["trimfill"]["adjusted"]["heterogeneity"]["I2_ci_HT"][1],
                     res["primary"]["heterogeneity"]["I2_ci_HT"][1], 1e-12)


# ================================================================ DT-2: Begg azonos hatásméretekkel
class TestDT2BeggIdenticalNonZero(_Tmp):
    def test_DT_2_identical_effects_always_raise(self):
        vi = [0.01 * (i + 1) for i in range(12)]
        for y in (0.1, 0.3, 0.7, -0.2, 1.0, 1e-3, 123.456, 0.0):    # metafor ranktest: tau = NA, p = NA
            with self.assertRaises(M.ModelError, msg=repr(y)):
                B.begg_test([y] * 12, vi)
        rnd = random.Random(20261005)
        for _ in range(300):                          # korábban: ~60%-ban τ = ±1, p < 0,05
            k = rnd.randint(3, 30)
            y = rnd.uniform(-3, 3)
            with self.assertRaises(M.ModelError):
                B.begg_test([y] * k, [rnd.uniform(1e-3, 1.0) for _ in range(k)])

    def test_DT_2_effects_at_the_mean_are_ties(self):
        # hat vizsgálat pontosan az összesített átlagon (t*_i = 0): kötés, nem a kerekítési zaj rangja.
        # A metafor 4.4 itt τ = 0,5455-öt ad, mert az FE-becslése 0,30000000000000009992 (zaj).
        vi = [0.01, 0.02, 0.03, 0.04, 0.06, 0.07, 0.05, 0.05]
        r = B.begg_test([0.3] * 6 + [0.1, 0.5], vi)
        self.assertEqual(r.kendall_tau, 0.0)
        self.assertEqual(r.p, 1.0)
        # szokásos adat változatlan (metafor ranktest: tau = 0,4, p = 0,483333333333)
        r = B.begg_test([0.1, 0.3, 0.5, 0.2, 0.6], [0.01, 0.02, 0.04, 0.05, 0.09])
        assert_close(self, r.kendall_tau, 0.4, 1e-12)
        assert_close(self, r.p, 0.483333333333, 1e-10)

    def test_DT_2_report_has_no_begg_line_for_identical_effects(self):
        data = self.csv("study,yi,vi\n" + "".join("S%d,0.1,%r\n" % (i + 1, 0.01 * (i + 1)) for i in range(12)))
        out = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", data, "--measure", "GEN", "--out", out, "--no-plots")
        self.assertEqual(code, 0, se)
        md = _read(os.path.join(out, "report.md"))
        self.assertNotIn("Begg–Mazumdar: Kendall", md)
        self.assertIn("Begg-teszt: nem számolható — a hatásméretek azonosak", md)
        self.assertNotIn("Begg's rank correlation", md)


# ================================================================ R2-ROB-15: szűrő ↔ alcsoport-szintek
class TestR2ROB15FilterCodeLevels(_Tmp):
    def removed(self, path, cond):
        rows, meta = tableio.read_table(path)
        rep = []
        tableio.apply_filters(rows, exclude=[cond], meta=meta, report=rep)
        return rep[0]["removed_labels"]

    def test_R2_ROB_15_filter_matches_the_same_levels_as_subgroups(self):
        p = self.csv(KOD)
        self.assertEqual(self.removed(p, "kod=01"), ["A", "C"])      # korábban: A, B, C, D ('01' = '1')
        self.assertEqual(self.removed(p, "kod=1"), ["B", "D"])
        self.assertEqual(self.removed(p, "kod=2"), ["E", "F"])
        # a szó szerint egyik szinttel sem egyező számérték numerikusan hasonlít (mindkét szint)
        self.assertEqual(self.removed(p, "kod=1.0"), ["A", "B", "C", "D"])
        # kanonikus (szövegként tárolt) alcsoport-oszlopban ugyanígy
        canon = self.csv(KOD.replace(",kod\n", ",subgroup\n"), "canon.csv")
        self.assertEqual(self.removed(canon, "subgroup=01"), ["A", "C"])
        self.assertEqual(self.removed(canon, "subgroup=1"), ["B", "D"])
        # JSON-bemenet (float) mint az alcsoport-szint: 1.0 = '1'
        rows = [{"study": "A", "g": 1.0}, {"study": "B", "g": 1}, {"study": "C", "g": 2.0}]
        self.assertEqual([r["study"] for r in tableio.apply_filters(rows, exclude=["g=1"])], ["C"])
        out = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", p, "--measure", "MD", "--subgroup", "kod", "--exclude", "kod=01",
                               "--out", out, "--no-plots")
        self.assertEqual(code, 0, se)
        self.assertEqual([(g["group"], g["k"]) for g in self.results(out)["subgroups"]["groups"]],
                         [("1", 2), ("2", 2)])

    def test_R2_ROB_15_unambiguous_numbers_still_compare_numerically(self):
        p = self.csv("study,yi,vi,ev,dose\nA,0.1,0.04,2005,1\nB,0.2,0.05,2006,1.0\nC,0.3,0.03,2005,2\n")
        self.assertEqual(self.removed(p, "ev=2005.0"), ["A", "C"])
        self.assertEqual(self.removed(p, "ev=2005"), ["A", "C"])
        # '1' és '1.0' két szöveg ugyanarra a számra: a szó szerint egyező szint számít
        self.assertEqual(self.removed(p, "dose=1"), ["A"])
        self.assertEqual(self.removed(p, "dose=1.00"), ["A", "B"])


# ================================================================ R2-ROB-04: study_id-ből képzett címke
class TestR2ROB04DerivedLabelFilterAndDuplicates(_Tmp):
    SID = ("Study ID,m1,sd1,n1,m2,sd2,n2\nSmith 2001,10,2,20,8,2,20\nJones 2002,11,2,25,8,2,20\n"
           "Kim 2003,12,3,25,9,3,25\nKim 2003,12.5,3,25,9.5,3,25\n")

    def test_R2_ROB_04_study_filter_works_with_derived_labels(self):
        p = self.csv(self.SID)
        out = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", p, "--measure", "MD", "--exclude", "study=Smith 2001",
                               "--out", out, "--no-plots")
        self.assertEqual(code, 0, so + se)                     # korábban: ismeretlen oszlop: 'study'
        self.assertIn("1 sor kizárva — Smith 2001", so)
        self.assertEqual(self.results(out)["effect_sizes"]["labels"], ["Jones 2002", "Kim 2003 (1)", "Kim 2003 (2)"])
        rows, meta = tableio.read_table(p)
        self.assertEqual(tableio.resolve_column("Study", meta, rows), "study")
        with self.assertRaises(ValueError) as cm:
            tableio.resolve_column("nincs", meta, rows)
        self.assertIn("Study ID oszlopból képzett címke", str(cm.exception))

    def test_R2_ROB_04_shared_study_id_gets_v007(self):
        rows, meta = tableio.read_table(self.csv(self.SID))
        f = codes(V.validate(rows, "MD", meta), "V007")
        self.assertEqual([x["study"] for x in f], ["Kim 2003"])
        self.assertIn("2 sor ugyanazzal a(z) Study ID azonosítóval", f[0]["detail"])
        # külön címkeoszloppal a közös study_id (több kar) nem V007
        rows, meta = tableio.read_table(self.csv("study,Trial,yi,vi\nA arm 1,T1,0.1,0.04\nA arm 2,T1,0.2,0.05\n"
                                                 "B,T2,0.3,0.03\n", "k.csv"))
        self.assertEqual(codes(V.validate(rows, "GEN", meta), "V007"), [])


# ================================================================ R2-ROB-05: k = 0
class TestR2ROB05NoSmallKAdviceAtZero(_Tmp):
    def test_R2_ROB_05_k0_reports_only_v026(self):
        p = self.csv("study,e1,n1,e2,n2\nA,0,10,0,10\nB,0,20,0,20\n")
        code, so, se = run_cli("validate", "--data", p, "--measure", "OR", "--json")
        self.assertEqual(code, 1)
        found = {f["code"] for f in json.loads(so)["findings"]}
        self.assertIn("V026", found)
        self.assertFalse(found & {"V015", "V016"}, found)
        rows, meta = tableio.read_table(self.csv("study,m1,sd1,n1,m2,sd2,n2\n", "ures.csv"))
        found = {f["code"] for f in V.validate(rows, "MD", meta)}
        self.assertIn("V026", found)
        self.assertFalse(found & {"V015", "V016"})
        # k > 0 esetén a k-szabályok változatlanok
        rows, meta = tableio.read_table(self.csv("study,yi,vi\nA,0.1,0.04\nB,0.2,0.05\n", "k2.csv"))
        found = {f["code"] for f in V.validate(rows, "GEN", meta)}
        self.assertTrue({"V015", "V016"} <= found)


# ================================================================ R2-ROB-07: túl kicsi variancia a validate-ben
class TestR2ROB07TinyVarianceInValidate(_Tmp):
    def test_R2_ROB_07_validate_and_analyze_agree(self):
        p = self.csv("study,yi,vi\nA,0.1,1e-300\nB,0.2,0.04\nC,0.3,0.05\n")
        code, so, se = run_cli("validate", "--data", p, "--measure", "GEN", "--json")
        self.assertEqual(code, 1, so + se)                     # korábban: 0 hiba, rc = 0
        res = json.loads(so)
        v030 = [f for f in res["findings"] if f["code"] == "V030"]
        self.assertEqual(len(v030), 1)
        self.assertEqual(v030[0]["severity"], "error")
        self.assertIn("A vizsgálat", v030[0]["detail"])
        code, so, se = run_cli("analyze", "--data", p, "--measure", "GEN", "--out", os.path.join(self.tmp, "o"))
        self.assertEqual(code, 1)
        ok = self.csv("study,yi,vi\nA,0.1,1e-40\nB,0.2,0.04\nC,0.3,0.05\n", "ok.csv")
        rows, meta = tableio.read_table(ok)
        self.assertEqual(codes(V.validate(rows, "GEN", meta), "V030"), [])


# ================================================================ R2-ROB-08: névelő
class TestR2ROB08Article(_Tmp):
    def test_R2_ROB_08_hungarian_article_before_numbers(self):
        self.assertEqual([V._az(n) for n in (1, 2, 5, 10, 15, 50, 100, 500, 1000, 2000, 5000)],
                         ["az", "a", "az", "a", "a", "az", "a", "az", "az", "a", "az"])
        rows, meta = tableio.read_table(self.csv("Study ID,Study ID,yi,vi\nA,X,0.1,0.04\nB,Y,0.2,0.05\n"))
        d = codes(V.validate(rows, "GEN", meta), "V028")[0]["detail"]
        self.assertIn("az 1. oszlop számít, a 2. oszlop", d)
        self.assertNotIn("a 1. oszlop", d)


# ================================================================ KBP-03: kizárási okok összevetése
COMP = {"identified_databases": 200, "identified_registers": 10, "identified_other": 0, "dedup_removed": 40,
        "removed_before_screening_n": 0, "screened": 170, "excluded_screening": 130, "sought_for_retrieval": 40,
        "not_retrieved": 2, "assessed_eligibility": 38, "excluded_eligibility": 30,
        "excluded_eligibility_reasons": {"nem RCT": 20, "rossz kimenet": 10}, "included": 8, "undecided": 0}


def _md(h_cell):
    return "\n".join([
        "| Lépés | Szám | Ellenőrzés |", "|---|---|---|",
        "| Azonosított rekordok — adatbázisok (n = A1) | 200 | |",
        "| Azonosított rekordok — regiszterek (n = A2) | 10 | |",
        "| Eltávolítva szűrés előtt: duplikátum (n = D1), automatikus (n = D2), egyéb (n = D3) | 40 / 0 / 0 | |",
        "| Szűrt rekordok (n = B) | 170 | |", "| Kizárt rekordok (n = C) | 130 | |",
        "| Teljes szövegre keresett (n = E) | 40 | |", "| Nem elérhető (n = F) | 2 | |",
        "| Értékelt teljes szöveg (n = G) | 38 | |",
        "| Kizárt jelentések (n = H) | %s | |" % h_cell,
        "| Bevont jelentések / vizsgálatok (n = J, n = I) | 8 / 7 | |"])


class TestKBP03ReasonBreakdownCompared(_Tmp):
    def check(self, h_cell, *extra):
        comp = self.csv(json.dumps(COMP, ensure_ascii=False), "prisma-flow.json")
        md = self.csv(_md(h_cell), "prisma_folyamat.md")
        return run_cli("prisma", "check", "--composer", comp, "--md", md, *extra)

    def test_KBP_03_different_reason_counts_or_names_are_p017(self):
        for cell in ("30 (nem RCT: 25; rossz kimenet: 5)", "30 (nem RCT: 20; rossz populáció: 10)"):
            code, so, se = self.check(cell)
            self.assertEqual(code, 1, cell + so + se)          # korábban: RENDBEN, rc = 0
            self.assertIn("[P017]", so)
            self.assertIn("excluded_eligibility_reasons", so)
        code, so, se = self.check("30 (nem RCT: 25; rossz kimenet: 5)", "--out-format", "json")
        p017 = [f for f in json.loads(so)["findings"] if f["code"] == "P017"]
        self.assertEqual([f["fields"] for f in p017], [["excluded_eligibility_reasons"]])

    def test_KBP_03_same_reasons_in_other_spelling_pass(self):
        for cell in ("30 (nem RCT: 20; rossz kimenet: 10)", "30 (Nem  RCT: 20; Rossz kimenet: 10)",
                     "30 (rossz kimenet: 10; nem RCT: 20)", "30"):
            code, so, se = self.check(cell)
            self.assertNotIn("[P017]", so, cell)
        # az okonkénti bontás nélküli md nem eltérés (csak kevesebb részlet; a P008-at a check_flow adja)
        self.assertNotIn("P017", self.check("30")[1])


# ================================================================ AG-04: RoB-írásmódok alcsoportban
class TestAG04RobSubgroupSpellings(_Tmp):
    def test_AG_04_spelling_variants_are_one_subgroup(self):
        from _helpers import load_reference
        rows = [dict(r, rob="Low risk of bias") for r in load_reference()["bcg"]["rows"]]
        rows[0]["rob"] = "High risk of bias"
        rows[1]["rob"] = "high"
        rows[2]["rob"] = "Magas torzítási kockázat"
        rows[3]["rob"] = "serious"
        rows[-1]["rob"] = "alacsony"
        out, _ = pipeline.run(rows, {"measure": "RR", "subgroup": "rob"})
        self.assertEqual([(g.group, g.k) for g in out["subgroups"].groups],
                         [("High risk of bias", 3), ("serious", 1), ("Low risk of bias", 9)])
        w = [x for x in out["warnings"] if x.startswith("Alcsoport (rob)")]
        self.assertEqual(len(w), 1)
        self.assertIn("'high' = 'High risk of bias'", w[0])
        self.assertIn("'alacsony' = 'Low risk of bias'", w[0])
        # a szűrő ugyanígy egy kategóriának veszi (és a 'serious'-t is high-nak)
        kept = tableio.apply_filters(rows, exclude=["rob=high"])
        self.assertEqual(len(kept), 9)
        # nem RoB-oszlopban nincs összevonás
        rows2 = [dict(r, csoport="high" if i % 2 else "High risk") for i, r in enumerate(rows)]
        out3, _ = pipeline.run(rows2, {"measure": "RR", "subgroup": "csoport"})
        self.assertEqual(len(out3["subgroups"].groups), 2)

    def test_AG_04_kb_text_matches_engine(self):
        rec = item("D-S12-002")["recommendation"]
        self.assertNotIn("a vegyes vagy fel nem ismert szókészletet", rec)
        self.assertIn("egy kategóriának veszi", rec)
        self.assertIn("külön szint marad", item("D-S06-008")["recommendation"])


# ================================================================ R2-NF-01: J lehetetlen egyéb ág mellett
class TestR2NF01MainBranchJStillChecked(unittest.TestCase):
    FLOW = {"identified_databases": 100, "duplicates_removed": 10, "screened": 90, "excluded_screening": 60,
            "sought": 30, "not_retrieved": 2, "assessed": 28, "excluded_eligibility": 20,
            "excluded_eligibility_reasons": {"x": 20}, "other_methods_identified": 6, "other_methods_sought": 5,
            "other_methods_assessed": 5, "other_methods_excluded": 9, "other_methods_excluded_reasons": {"y": 9}}

    def p005(self, j):
        res = prisma.check_flow(dict(self.FLOW, included_reports=j, included_studies=min(j, 4)))
        self.assertFalse(res.ok)
        return [f["detail"] for f in res.findings if f["code"] == "P005"]

    def test_R2_NF_01_j_outside_possible_range_is_reported(self):
        for j in (4, 14):                                     # G − H = 8; az egyéb ág legfeljebb 5-öt adhat
            d = self.p005(j)
            self.assertEqual(len(d), 2, d)
            self.assertIn("Bevont jelentések (J) = %d, de G − H = 28 − 20 = 8" % j, d[1])
        for j in (8, 10, 13):
            self.assertEqual(len(self.p005(j)), 1, j)          # csak az egyéb ág hibája


# ================================================================ R2-ROB-03 / KBP-01 / AG-01: többértékű sorok
class TestMultiValueRows(unittest.TestCase):
    def parse(self, row, cell):
        return prisma.parse_markdown_table(row % cell)

    def test_ROB_03_space_or_comma_lists_are_lists_again(self):
        for cell in ("118 115", "118,115", "118, 115"):
            self.assertEqual(self.parse(JI, cell), {"included_reports": 118, "included_studies": 115}, cell)
        self.assertEqual(self.parse(DROW, "100 200 300"),
                         {"duplicates_removed": 100, "automation_removed": 200, "other_removed": 300})

    def test_KBP_01_grouped_values_still_parse(self):
        self.assertEqual(self.parse(JI, "1 018 / 1 015"), {"included_reports": 1018, "included_studies": 1015})
        self.assertEqual(self.parse(JI, "1 018 1 015"), {"included_reports": 1018, "included_studies": 1015})
        self.assertEqual(self.parse(JI, "1 018"), {"included_reports": 1018})       # '018' csak ezres csoport
        self.assertEqual(self.parse(JI, "J=1 018; I=1 015"), {"included_reports": 1018, "included_studies": 1015})
        self.assertEqual(self.parse(DROW, "1 010 / 0 / 0"),
                         {"duplicates_removed": 1010, "automation_removed": 0, "other_removed": 0})
        self.assertEqual(self.parse(DROW, "1.345 / 0 / 0")["duplicates_removed"], 1345)

    def test_AG_01_empty_slot_keeps_position(self):
        self.assertEqual(self.parse(DROW, "12 / / 3"), {"duplicates_removed": 12, "other_removed": 3})
        self.assertEqual(self.parse(JI, "– / 6"), {"included_studies": 6})

    def test_AG_01_several_numbers_in_single_value_cell_is_p001(self):
        flow = prisma.parse_markdown_table("| Szűrt rekordok (n = B) | 1 200 + 180 | |\n")
        self.assertEqual(flow, {"screened": "1 200 + 180"})
        res = prisma.check_flow(flow)
        self.assertEqual([f["code"] for f in res.findings if f["severity"] == "error"], ["P001"])
        # a zárójeles megjegyzés nem számít; a kizárási okok sora több számot tartalmazhat
        self.assertEqual(prisma.parse_markdown_table("| Szűrt rekordok (n = B) | 1 380 (dedup után 1 400) | |\n"),
                         {"screened": 1380})
        self.assertEqual(prisma.parse_markdown_table(
            "| Kizárt jelentések (n = H) | 30 (nem RCT: 20; rossz kimenet: 10) | |\n")["excluded_eligibility"], 30)

    def test_AG_01_comma_separated_j_and_i_letters(self):
        self.assertEqual(prisma.parse_markdown_table(
            "| Bevont jelentések / vizsgálatok (n = J, n = I) | 8 / 7 | |\n"),
            {"included_reports": 8, "included_studies": 7})          # korábban: az I elveszett

    def test_AG_01_unreadable_removal_box_is_not_missing(self):
        res = prisma.check_flow({"identified_databases": 100, "duplicates_removed": "kb. sok", "screened": 90})
        self.assertEqual([f["code"] for f in res.findings if f["severity"] == "error"], ["P001"])
        self.assertNotIn("P011", [f["code"] for f in res.findings])
        self.assertNotIn("hiányoznak", " ".join(f["detail"] for f in res.findings))


# ================================================================ KBP-12: „nincs” felminősítés
class TestKBP12NoneTextIsZero(unittest.TestCase):
    DOWN = dict(risk_of_bias="−1", inconsistency="−1", indirectness="0", imprecision="0", publication_bias="0")

    def test_KBP_12_none_upgrade_keeps_upper_bound(self):
        for up in ("nincs", "nincs felminősítés", "none", "–", "-", "n/a", "Nincs (nincs dózis-hatás)"):
            msg = projekt.grade_consistency("high", upgrades=up, **self.DOWN)
            self.assertIsNotNone(msg, up)                      # korábban: None (hi = 4)
            self.assertIn("'low' (RCT/ROBINS-I kiindulás) vagy 'very low'", msg)
            self.assertIsNone(projekt.grade_consistency("low", upgrades=up, **self.DOWN), up)
        # a ténylegesen felminősítést leíró szabad szöveg továbbra is nyitva hagyja a felső határt
        self.assertIsNone(projekt.grade_consistency("high", upgrades="nagy hatás", **self.DOWN))
        self.assertIsNone(projekt.grade_consistency("high", upgrades="nincs nagy hatás, de dózis-hatás", **self.DOWN))
        # 'nincs' leminősítési doménben is 0
        self.assertEqual(projekt._grade_step("nincs"), 0)
        self.assertIsNone(projekt._grade_step("nem súlyos"))


# ================================================================ AG-14: --strict a sablonokban
class TestAG14StrictInTemplates(unittest.TestCase):
    def test_AG_14_every_logged_kb_reference_template_is_strict(self):
        docs = dict(agent_docs(), SKILL=_read(SKILL))
        bad, seen = [], 0
        for name, text in docs.items():
            for cmd in re.findall(r"`([^`]*\bproject (?:log|finding|grade|checkpoint)\b[^`]*)`", text):
                if re.search(r"--kb\s+[^\s-]", cmd):          # parancssablon (a kapcsoló puszta említése nem)
                    seen += 1
                    if "--strict" not in cmd:
                        bad.append((name, cmd))
        self.assertGreaterEqual(seen, 5)
        self.assertEqual(bad, [])


# ================================================================ KB-R2-03 / KB-R2-04: Doi/LFK mint alternatíva
class TestKBR203DoiNotAnAlternative(unittest.TestCase):
    CAVEAT = R2C.TestDoiLfkCaveats.CAVEAT
    IMPERATIVE = re.compile(r"\b(?:Detect|Assess|Screen)\w*\s+(?:it\s+|them\s+)?(?:with|using|by)\b[^.;]*"
                            r"\b(?:Doi|LFK)", re.I)

    def test_KB_R2_03_pitfall_unit_carries_caveat(self):
        body = item("K-KHN0102-063")["body"]
        self.assertNotIn("Detect with a funnel or Doi plot/LFK index", body)
        self.assertRegex(body, r"D-S11-015")
        self.assertIn("never as a substitute", body)

    def test_KB_R2_03_no_unit_of_any_kind_prescribes_doi_without_caveat(self):
        bad = [iid for iid, it in ITEMS.items()
               if iid.startswith("K-") and it.get("stage_id") in ("S11", "S13", "S14")
               and self.IMPERATIVE.search(it["body"]) and not self.CAVEAT.search(it["body"])]
        self.assertEqual(bad, [])


# ================================================================ AG-06: konverziók
class TestAG06ConvertGaps(unittest.TestCase):
    def convert(self, *args):
        code, so, se = run_cli("convert", *args)
        return code, (json.loads(so) if code == 0 else None), se

    def test_AG_06_corr_from_change_rejects_inconsistent_sds(self):
        with self.assertRaises(C.ConversionError):
            C.corr_from_change(10, 12, 30)                      # korábban: corr = −2,733
        code, res, se = self.convert("corr-from-change", "--sd-baseline", 10, "--sd-final", 12, "--sd-change", 30)
        self.assertEqual(code, 1)
        self.assertIn("2 és 22 között", se)
        self.assertEqual(C.corr_from_change(10, 12, 22), -1.0)   # határeset
        self.assertEqual(C.corr_from_change(10, 12, 2), 1.0)

    def test_AG_06_d_from_t_hedges_matches_metafor(self):
        # metafor: escalc(measure = "SMD", ti = 2.5, n1i = 20, n2i = 22) → yi = 0.757804167471, vi = 0.102291059219
        code, res, se = self.convert("d-from-t", "--t", 2.5, "--n1", 20, "--n2", 22, "--hedges")
        self.assertEqual(code, 0, se)
        assert_close(self, res["g"], 0.757804167471, 1e-10)
        assert_close(self, res["vi"], 0.102291059219, 1e-10)
        assert_close(self, res["d"], C.d_from_t(2.5, 20, 22), 1e-12)
        code, res, se = self.convert("d-from-t", "--t", 2.5, "--n1", 20, "--n2", 22)
        self.assertNotIn("g", res)
        self.assertNotIn("megjegyzés", res)
        code, res, se = self.convert("d-from-t", "--t", 2.5, "--n1", 20, "--n2", 22, "--vtype", "UB")
        self.assertIn("--hedges", res["megjegyzés"])

    def test_AG_06_se_from_ci_df_uses_t(self):
        # R: (3.1 - 0.5) / (2 * qt(0.975, 20)) = 0.623213221547
        code, res, se = self.convert("se-from-ci", "--lower", 0.5, "--upper", 3.1, "--df", 20)
        self.assertEqual(code, 0, se)
        assert_close(self, res["se"], 0.623213221547, 1e-10)
        assert_close(self, C.se_from_ci(0.5, 3.1), 2.6 / (2 * 1.959963984540054), 1e-12)
        with self.assertRaises(C.ConversionError):
            C.se_from_ci(0.5, 3.1, df=0)
        rec = item("D-S05-008")["recommendation"]
        self.assertNotIn("kézzel", rec)
        self.assertIn("--df", rec)


# ================================================================ AG-12: GEN + közölt SMD
class TestAG12GenSmdNote(unittest.TestCase):
    ROWS = [{"study": "S%d" % i, "yi": y, "n1": 20 + i, "n2": 22 + i}
            for i, y in enumerate((0.1, 0.3, 0.5, 0.2, 0.6, 0.4, 0.8, 0.15, 0.35, 0.55, 0.25, 0.45))]

    def test_AG_12_gen_with_smd_vtype_gets_the_note(self):
        out, _ = pipeline.run(self.ROWS, {"measure": "GEN", "gen_smd_vtype": "LS"})
        self.assertTrue(out["bias"].get("smd_note"))
        self.assertIn("Egger-teszt (SMD-nél csak tájékoztató)", report.build_report(out, "t", "2026-01-01", plots=False))
        rows = [dict(r, vi=0.05 + 0.01 * i) for i, r in enumerate(self.ROWS)]
        out, _ = pipeline.run(rows, {"measure": "GEN"})
        self.assertFalse(out["bias"].get("smd_note"))


if __name__ == "__main__":
    unittest.main()
