# -*- coding: utf-8 -*-
"""Y-köteg: hatásmértékek, bemenet és PRISMA-folyamat (a forrás-esetek hiánylistájából).

1. Glass Δ (SMD_GLASS) — Khan 2020 Ex. 8.12 (MetaXL / Stata metan) és metafor SMD1/SMD1H
2. Párosított elrendezés (MC, SMCC) — metafor escalc és Khan 2020 Ex. 8.2
3. SMD-variancia konvenciók (METAN_COHEN, METAN_HEDGES) és MD összevont variancia (md_vtype)
4. conversions.smd_variance (közölt SMD + karlétszámok) — Khan 2020 Ex. 8.3
5. Freeman–Tukey visszatranszformálás pft_n='variance' (MetaXL, Barendregt 2013) — Khan 2020 6. fej.
6. PRISMA folyamat-ellenőrző (metaelemzes.prisma) — Khan 2020 Fig. 1.1, Bialek et al. 2023

A metafor-referenciaértékeket R 4.3 + metafor 4.4 adta (escalc, transf.ipft).
"""
import copy
import json
import os
import tempfile
import unittest

from _helpers import assert_close
import source_cases as SC
from metaelemzes import conversions as C
from metaelemzes import effect_sizes as E
from metaelemzes import models as M
from metaelemzes import prisma as P
from metaelemzes import tableio as T
from metaelemzes import validate as V

CASES = {c["case_id"]: c for c in SC.load_cases()}

OPTIME = [  # Khan 2020 Table 8.10 (operatív idő, LARR vs ORR)
    ("Bonjer 2015", 241, 33.6, 699, 191.5, 26.2, 345), ("Fleshman 2015", 266.2, 101.9, 240, 220.6, 92.4, 222),
    ("Stevenson 2015", 210, 66.7, 238, 190, 59.2, 235), ("Jeong 2014", 244.9, 75.4, 170, 197, 62.9, 170),
    ("Ng 2014", 211.6, 53, 40, 153, 41.1, 40), ("Liang 2011", 138.08, 23.79, 169, 118.53, 21.99, 174),
    ("Lujan 2009", 193.7, 45.1, 101, 172.9, 59.4, 103), ("Ng 2008", 213.5, 46.2, 51, 163.7, 43.4, 48),
    ("Zhou 2004", 120, 18, 82, 106, 25, 89)]
OPTIME_ROWS = [dict(zip(("study", "m1", "sd1", "n1", "m2", "sd2", "n2"), r)) for r in OPTIME]

PAIRED_ROWS = [dict(study=str(i), m1=a, m2=b, sd1=c, sd2=d, r=r, n=n) for i, (a, b, c, d, r, n) in enumerate([
    (10.2, 8.1, 2.5, 2.9, 0.5, 20), (25.1, 20.0, 6, 5.5, 0.8, 35), (3.3, 3.9, 1.1, 1.4, -0.2, 12),
    (50, 44.4, 12, 10, 0.95, 50), (7.7, 7.7, 3, 3.2, 0.3, 8)])]


def _native(case, measure, options=None):
    """A GEN-bemenetű (levezetett yi/vi) esetből natív mértékű eset: a yi/vi oszlopok nélkül."""
    c = copy.deepcopy(case)
    c["rows"] = [{k: v for k, v in r.items() if k not in ("yi", "vi")} for r in c["rows"]]
    c["measure"] = measure
    c["options"] = dict(options or {})
    return c


def _failures(case):
    return [x for x in SC.check_case(case) if not x[4]]


# ------------------------------------------------------------------ 1. Glass Δ
class TestGlassDelta(unittest.TestCase):
    def test_metan_matches_khan_derived_inputs_exactly(self):
        case = CASES["khan_ch08__optime_glass_fe_metaxl_var"]
        es = E.compute(_native(case, "SMD_GLASS")["rows"], "SMD_GLASS")
        for got, r in zip(zip(es.yi, es.vi), case["rows"]):
            assert_close(self, got[0], r["yi"], 1e-12, r["study"])
            assert_close(self, got[1], r["vi"], 1e-12, r["study"])

    def test_khan_per_study_es_gap_case_passes(self):
        c = copy.deepcopy(CASES["khan_ch08__optime_glass_per_study_es"])
        c["measure"] = "SMD_GLASS"
        self.assertEqual(_failures(c), [])

    def test_khan_pooled_glass_cases_pass_natively(self):
        for cid in ("khan_ch08__optime_glass_fe_metaxl_var", "khan_ch08__optime_glass_re_dl_metaxl_var",
                    "khan_ch08__optime_glass_ivhet_metaxl_var", "khan_ch08__optime_glass_per_study_ci_metaxl_var"):
            self.assertEqual(_failures(_native(CASES[cid], "SMD_GLASS")), [], cid)

    def test_metafor_smd1_conventions(self):
        # escalc("SMD1"/"SMD1H", ...) — metafor 4.4
        ref = {"LS": (1.88519033968499, 0.00947980736284193), "LS2": (1.88519033968499, 0.00946093477896318),
               "UB": (1.88519033968499, 0.00952118476865905), "SMD1H": (1.88519033968499, 0.0104020668794839)}
        bonjer = OPTIME[0][1:]
        for vt, (y, v) in ref.items():
            gy, gv = E.glass_delta(*bonjer, vtype=vt)
            assert_close(self, gy, y, 1e-11, vt)
            assert_close(self, gv, v, 1e-11, vt)
        gy, gv = E.glass_delta(*bonjer, vtype="LS", correct=False)        # escalc(correct=FALSE)
        assert_close(self, gy, 1.88931297709924, 1e-12)
        assert_close(self, gv, 0.00950235940428246, 1e-12)
        es = E.compute(OPTIME_ROWS, "SMD_GLASS", glass_vtype="LS")
        assert_close(self, es.yi[1], 0.491829484179025, 1e-11)
        assert_close(self, es.vi[2], 0.00869828287675033, 1e-11)

    def test_standardised_by_control_sd(self):
        y, v = E.glass_delta(12, 99, 30, 10, 4, 25)      # sd1 nem számít (METAN)
        self.assertAlmostEqual(y, 0.5)
        self.assertAlmostEqual(v, 55.0 / 750 + 0.25 / 48)

    def test_invalid_vtype_and_columns(self):
        with self.assertRaises(E.EffectSizeError):
            E.compute(OPTIME_ROWS, "SMD_GLASS", glass_vtype="XYZ")
        self.assertIn("SMD_GLASS", E.CONTINUOUS)
        self.assertEqual(E.REQUIRED_COLUMNS["SMD_GLASS"], E.REQUIRED_COLUMNS["SMD"])
        bad = [dict(OPTIME_ROWS[0], sd2=0)]
        es = E.compute(bad, "SMD_GLASS")
        self.assertEqual(len(es), 0)
        self.assertEqual(len(es.excluded), 1)

    def test_extreme_glass_flagged(self):
        es = E.compute([dict(study="x", m1=50, sd1=5, n1=20, m2=10, sd2=5, n2=20)], "SMD_GLASS")
        self.assertIn("V014", [f["code"] for f in V.check_effect_sizes(es)])


# ------------------------------------------------------------ 2. párosított
class TestPaired(unittest.TestCase):
    def test_mc_matches_metafor(self):
        es = E.compute(PAIRED_ROWS, "MC")
        for got, want in zip(es.yi[:3], (2.1, 5.1, -0.6)):
            assert_close(self, got, want, 1e-12)
        for got, want in zip(es.vi[:3], (0.3705, 0.384285714285714, 0.3155)):
            assert_close(self, got, want, 1e-12)
        self.assertEqual(es.ni, [20, 35, 12, 50, 8])

    def test_smcc_matches_metafor(self):
        ref = {"LS": ((0.740526946617668, 1.35968071760367, -0.286764327524793),
                      (0.0637095039666722, 0.0549818807689032, 0.0867597408141978)),
               "LS2": ((0.740526946617668, 1.35968071760367, -0.286764327524793),
                       (0.0597808982492304, 0.0537246069115102, 0.0754951781617133))}
        for vt, (ys, vs) in ref.items():
            es = E.compute(PAIRED_ROWS, "SMCC", smd_vtype=vt)
            for got, want in zip(es.yi[:3], ys):
                assert_close(self, got, want, 1e-12, vt)
            for got, want in zip(es.vi[:3], vs):
                assert_close(self, got, want, 1e-12, vt)
            self.assertEqual(es.warnings, [])

    def test_input_alternatives_agree(self):
        r = PAIRED_ROWS[0]
        sdd = (r["sd1"] ** 2 + r["sd2"] ** 2 - 2 * r["r"] * r["sd1"] * r["sd2"]) ** 0.5
        alt = [dict(study="a", m1=r["m1"], m2=r["m2"], sd_diff=sdd, n=r["n"]),
               dict(study="b", mdiff=r["m1"] - r["m2"], sd_diff=sdd, n=r["n"]),
               dict(study="c", sum_d=(r["m1"] - r["m2"]) * r["n"], sum_sq_dev_d=sdd ** 2 * (r["n"] - 1), n=r["n"])]
        for meas in ("MC", "SMCC"):
            base = E.compute([r], meas)
            es = E.compute(alt, meas)
            self.assertEqual(len(es), 3)
            for y, v in zip(es.yi, es.vi):
                assert_close(self, y, base.yi[0], 1e-12, meas)
                assert_close(self, v, base.vi[0], 1e-12, meas)

    def test_khan_cholesterol_gap_case_passes(self):
        c = copy.deepcopy(CASES["khan_ch08__cholesterol_paired_mean_difference"])
        c["measure"] = "MC"
        self.assertEqual(_failures(c), [])
        es = E.compute(c["rows"], "MC")
        assert_close(self, es.yi[0], 16.9333, 5e-5)
        assert_close(self, es.vi[0], 13.3759, 5e-5)
        # metafor escalc("SMCC", m1i=D̄, m2i=0, sd1i=S_D, sd2i=0, ri=0, ni=15)
        es = E.compute(c["rows"], "SMCC")
        assert_close(self, es.yi[0], 1.13005552365004, 1e-12)
        assert_close(self, es.vi[0], 0.109234182884399, 1e-12)

    def test_paired_from_sums(self):
        m, s = C.paired_from_sums(15, 254, 2808.9333)
        assert_close(self, m, 16.933333333333334, 1e-12)
        assert_close(self, s ** 2, 200.6380928571, 1e-9)
        with self.assertRaises(C.ConversionError):
            C.paired_from_sums(1, 3, 2)
        with self.assertRaises(C.ConversionError):
            C.paired_from_sums(10, 3, -2)

    def test_smcc_small_n_and_vtype_fallback(self):
        es = E.compute([dict(study="a", mdiff=1, sd_diff=1, n=2)], "SMCC")
        self.assertEqual(len(es), 0)
        es = E.compute(PAIRED_ROWS[:1], "SMCC", smd_vtype="UB")
        ref = E.compute(PAIRED_ROWS[:1], "SMCC", smd_vtype="LS")
        self.assertEqual(es.vi, ref.vi)
        self.assertTrue(any("SMCC" in w for w in es.warnings))

    def test_validate_paired(self):
        rows = [dict(study="ok", m1=10, m2=8, sd1=2, sd2=3, r=0.5, n=20),
                dict(study="nosd", m1=10, m2=8, n=12),
                dict(study="badr", m1=5, m2=4, sd1=2, sd2=2, r=1.4, n=12),
                dict(study="badn", mdiff=1, sd_diff=0, n=2.5)]
        f = V.validate(rows, "MC")
        codes = {(x["code"], x["study"]) for x in f}
        self.assertIn(("V002", "nosd"), codes)
        self.assertIn(("V010", "badr"), codes)
        self.assertIn(("V004", "badn"), codes)
        self.assertIn(("V005", "badn"), codes)
        self.assertNotIn("ok", V.blocking_labels(f))
        es = E.compute(rows, "MC", skip_labels=V.blocking_reasons(f))
        self.assertEqual(es.labels, ["ok"])
        # oszlopszinten: nincs SD-alternatíva → V001
        f = V.validate([dict(study="a", m1=1, m2=2, n=5)], "SMCC")
        self.assertEqual([x["code"] for x in f], ["V001"])
        self.assertIn("sd_diff", f[0]["detail"])

    def test_tableio_aliases(self):
        m = T.canonical_columns(["Study", "Mean change", "SD_change", "N", "sum_d", "SS_D"])
        self.assertEqual(m["Mean change"], "mdiff")
        self.assertEqual(m["SD_change"], "sd_diff")
        self.assertEqual(m["SS_D"], "sum_sq_dev_d")
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "p.csv")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("study;átlagos változás;sd_különbség;n\nA;3,5;2,1;20\nB;x;2;15\n")
            rows, meta = T.read_table(p)
            self.assertEqual(rows[0]["mdiff"], 3.5)
            self.assertEqual(rows[0]["sd_diff"], 2.1)
            f = V.validate(rows, "MC", meta)
            self.assertIn(("V003", "B"), {(x["code"], x["study"]) for x in f})


# -------------------------------------------------- 3. SMD / MD konvenciók
class TestVarianceConventions(unittest.TestCase):
    def test_metan_cohen_and_hedges_match_khan_derived_inputs(self):
        for cid, meas, vt in (("khan_ch08__optime_cohen_fe_metaxl_var", "COHEN_D", "METAN_COHEN"),
                              ("khan_ch08__optime_hedges_fe_metaxl_var", "SMD", "METAN_HEDGES")):
            case = CASES[cid]
            for jm in ("exact", "approx"):    # METAN_HEDGES: mindig a metan közelítő J-je
                es = E.compute(_native(case, meas)["rows"], meas, smd_vtype=vt, j_method=jm)
                for (y, v), r in zip(zip(es.yi, es.vi), case["rows"]):
                    assert_close(self, y, r["yi"], 1e-12, "%s %s" % (cid, r["study"]))
                    assert_close(self, v, r["vi"], 1e-12, "%s %s" % (cid, r["study"]))

    def test_khan_native_variance_gap_cases_pass(self):
        for cid, vt in (("khan_ch08__optime_cohen_fe_native_variance", "METAN_COHEN"),
                        ("khan_ch08__optime_hedges_fe_native_variance", "METAN_HEDGES")):
            c = copy.deepcopy(CASES[cid])
            c["options"] = dict(c.get("options") or {}, smd_vtype=vt)
            self.assertEqual(_failures(c), [], cid)

    def test_khan_metaxl_cases_pass_natively(self):
        for stem, meas, vt in (("cohen", "COHEN_D", "METAN_COHEN"), ("hedges", "SMD", "METAN_HEDGES")):
            for kind in ("fe", "re_dl", "ivhet", "lfk", "per_study_ci"):
                cid = "khan_ch08__optime_%s_%s_metaxl_var" % (stem, kind)
                self.assertEqual(_failures(_native(CASES[cid], meas, {"smd_vtype": vt})), [], cid)

    def test_defaults_unchanged(self):
        r = OPTIME[4][1:]
        y, v = E.smd(*r)
        n1, n2 = r[2], r[5]
        assert_close(self, v, 1.0 / n1 + 1.0 / n2 + y ** 2 / (2.0 * (n1 + n2)), 1e-15)
        es_ub = E.compute(OPTIME_ROWS[:2], "COHEN_D", smd_vtype="UB")
        es_ls = E.compute(OPTIME_ROWS[:2], "COHEN_D", smd_vtype="LS")
        self.assertEqual(es_ub.vi, es_ls.vi)
        self.assertTrue(any("UB" in w for w in es_ub.warnings))
        with self.assertRaises(E.EffectSizeError):
            E.smd(*r, vtype="NOPE")

    def test_md_pooled_variance(self):
        row = [dict(study="ginkgo", m1=58.4, sd1=3.8, n1=28, m2=56.8, sd2=4.3, n2=24)]
        self.assertAlmostEqual(E.compute(row, "MD").vi[0], 1.2861309523809523, places=14)
        for name in ("pooled", "HO", "ho"):
            assert_close(self, E.compute(row, "MD", md_vtype=name).vi[0], 1.2615416666666668, 1e-14, name)
        c = copy.deepcopy(CASES["khan_ch08__ginkgo_md_pooled_equal_var"])
        c["options"] = {"md_vtype": "pooled"}
        self.assertEqual(_failures(c), [])
        # metafor escalc("MD", vtype="HO")
        es = E.compute(OPTIME_ROWS, "MD", md_vtype="pooled")
        for got, want in zip(es.vi[:3], (4.25500270734041, 82.3483736657854, 33.6569324929592)):
            assert_close(self, got, want, 1e-12)
        with self.assertRaises(E.EffectSizeError):
            E.compute(row, "MD", md_vtype="weird")


# ------------------------------------------------ 4. SMD-variancia közölt g-ből
class TestSmdVarianceFromG(unittest.TestCase):
    def test_khan_quant_ability(self):
        v = C.smd_variance(0.72, 38, 38, vtype="METAN_COHEN")
        assert_close(self, v, 0.05613428165007112, 1e-15)
        assert_close(self, v ** 0.5, 0.236927, 5e-7)

    def test_metafor_conventions(self):
        # escalc("SMD", di = g/J, n1i = n2i = 38, vtype = ...) — metafor 4.4
        assert_close(self, C.smd_variance(0.72, 38, 38, "LS"), 0.05604210526315789, 1e-15)
        assert_close(self, C.smd_variance(0.72, 38, 38, "LS2"), 0.05497647507361043, 1e-12)
        assert_close(self, C.smd_variance(0.72, 38, 38, "UB"), 0.05621899291919748, 1e-12)
        assert_close(self, C.smd_variance(0.72, 38, 38, "METAN_HEDGES"),
                     76 / 1444.0 + 0.72 ** 2 / (2 * (76 - 3.94)), 1e-15)

    def test_errors(self):
        for args in ((0.5, 0, 10), (0.5, 10, None), (None, 10, 10)):
            with self.assertRaises(C.ConversionError):
                C.smd_variance(*args)
        with self.assertRaises(C.ConversionError):
            C.smd_variance(0.5, 10, 10, vtype="XX")

    def test_gen_rows_with_arm_sizes(self):
        c = copy.deepcopy(CASES["khan_ch08__quant_ability_study1_variance_from_g"])
        c["options"] = {"gen_smd_vtype": "METAN_COHEN"}
        self.assertEqual(_failures(c), [])
        es = E.compute(c["rows"], "GEN", gen_smd_vtype="METAN_COHEN")
        self.assertEqual(es.ni, [76])
        self.assertIn("METAN_COHEN", es.notes[0])
        # opció nélkül a régi viselkedés: vi/sei kell
        self.assertEqual(len(E.compute(c["rows"], "GEN")), 0)
        f = V.validate(c["rows"], "GEN")
        self.assertIn("V001", [x["code"] for x in f])
        f = V.validate(c["rows"], "GEN", options={"gen_smd_vtype": "METAN_COHEN"})
        self.assertFalse([x for x in f if x["severity"] == "error"], f)
        # a közölt vi elsőbbséget élvez
        es = E.compute([dict(yi=0.5, vi=0.1, n1=10, n2=10)], "GEN", gen_smd_vtype="LS")
        self.assertEqual(es.vi, [0.1])
        with self.assertRaises(E.EffectSizeError):
            E.compute(c["rows"], "GEN", gen_smd_vtype="nope")


# ----------------------------------------- 5. Freeman–Tukey visszatranszformálás
class TestPftBackTransform(unittest.TestCase):
    ROWS = [dict(study=s, x=x, n=n) for s, x, n in (
        ("Bondestam", 6.0, 10), ("Shen", 153.6, 300), ("Zharikov", 714.5, 1429),
        ("Babigian", 2057.78, 3319), ("Fichter", 5.25, 7), ("Keith", 210.45, 305))]

    def _pooled(self, model):
        es = E.compute(self.ROWS, "PFT")
        if model == "fixed":
            return M.meta_analysis(es.yi, es.vi, "fixed", ci_method="z")
        return M.meta_analysis(es.yi, es.vi, "random", "DL", ci_method="z")

    def test_variance_method_matches_metafor_ipft(self):
        # metafor: transf.ipft(c(b, ci.lb, ci.ub), ni = 1/(4·se²)) a rma(method="FE"/"DL") eredményére
        ref = {"fixed": (5373.0, (0.586633396776813, 0.573434353273889, 0.599770504892538)),
               "random": (159.015921455582, (0.587538821653717, 0.509785114190463, 0.663181777177906))}
        for model, (m, want) in ref.items():
            r = self._pooled(model)
            assert_close(self, E.pft_backtransform_n("variance", se=r.se), m, 1e-9, model)
            got = [E.back_transform("PFT", v, pft_n="variance", se=r.se)
                   for v in (r.estimate, r.ci_lower, r.ci_upper)]
            for g, w in zip(got, want):
                assert_close(self, g, w, 1e-12, model)

    def test_harmonic_default_unchanged(self):
        r = self._pooled("fixed")
        nh = E.harmonic_mean([x["n"] for x in self.ROWS])
        self.assertEqual(E.back_transform("PFT", r.estimate, nh),
                         E.pft_inverse(r.estimate, nh))
        assert_close(self, E.back_transform("PFT", r.ci_lower, nh), 0.576422850814848, 1e-12)
        self.assertEqual(E.back_transform("PFT", r.estimate, pft_n=nh), E.back_transform("PFT", r.estimate, nh))

    def test_single_study_is_n_plus_half(self):
        es = E.compute(self.ROWS[:1], "PFT")
        self.assertAlmostEqual(E.pft_backtransform_n("variance", se=es.vi[0] ** 0.5), 10.5)

    def test_errors(self):
        with self.assertRaises(E.EffectSizeError):
            E.back_transform("PFT", 0.8)
        with self.assertRaises(E.EffectSizeError):
            E.back_transform("PFT", 0.8, pft_n="variance")
        with self.assertRaises(E.EffectSizeError):
            E.back_transform("PFT", 0.8, pft_n="median", n_harmonic=10)
        with self.assertRaises(E.EffectSizeError):
            E.back_transform("PFT", 0.8, pft_n=0)

    def test_khan_metaxl_backtransformed_gap_cases(self):
        """A MetaXL-féle visszatranszformálás (m = 1/Var(t)) a nyomtatott 2 tizedesjegyes
        értékeket adja (Fig. 6.1–6.4); a harmonikus átlag nem (pl. FE alsó határ 0.58 vs 0.57)."""
        for cid in ("khan_ch06_07__schizophrenia_metaxl_fixed_backtransformed",
                    "khan_ch06_07__schizophrenia_metaxl_random_backtransformed",
                    "khan_ch06_07__schizophrenia_metaxl_ivhet_backtransformed",
                    "khan_ch06_07__schizophrenia_metaxl_subgroup_backtransformed_and_share"):
            case = CASES[cid]
            res = SC.execute(copy.deepcopy(case))
            checked = 0
            for ex in case["expected"]:
                if ex.get("transform") != "pft_hm":
                    continue
                parent = ".".join(ex["path"].split(".")[:-1])
                holder = SC.resolve(res, parent) if parent else res
                se = holder.se
                got = E.back_transform("PFT", float(SC.resolve(res, ex["path"])), pft_n="variance", se=se)
                self.assertLessEqual(abs(got - ex["value"]), ex["tol"], "%s %s: %r" % (cid, ex["path"], got))
                checked += 1
            self.assertGreater(checked, 0)


# ------------------------------------------------------------------ 6. PRISMA
class TestPrismaFlow(unittest.TestCase):
    def test_book_cases_pass(self):
        for cid in ("khan_ch01_02__prisma2009_flow_d1_vs_d2_gastrectomy",
                    "simplypsych_guide__prisma2009_flow_bialek2023"):
            case = CASES[cid]
            res = P.check_flow(case["rows"][0], **case["call_args"])
            self.assertTrue(res.ok, res.findings)
            self.assertEqual(res.template, "PRISMA2009")
            self.assertEqual(P.check_flow(case["rows"][0]).template, "PRISMA2009")   # felismerés
            for ex in case["expected"]:
                got = SC.resolve(res, ex["path"])
                self.assertEqual(got, ex["value"], "%s %s" % (cid, ex["path"]))
            self.assertIn("P011", [f["code"] for f in res.findings])    # 0 duplikátum levezetve
        res = P.check_flow(CASES["simplypsych_guide__prisma2009_flow_bialek2023"]["rows"][0])
        self.assertIn("P008", [f["code"] for f in res.findings])         # 78 kizárás ok nélkül

    def test_2009_inconsistencies(self):
        base = dict(CASES["khan_ch01_02__prisma2009_flow_d1_vs_d2_gastrectomy"]["rows"][0])
        bad = dict(base, fulltext_assessed_for_eligibility=16)
        codes = [f["code"] for f in P.check_flow(bad).findings]
        self.assertIn("P003", codes)
        bad = dict(base, studies_included_quantitative_synthesis=7)
        self.assertIn("P010", [f["code"] for f in P.check_flow(bad).findings])
        bad = dict(base, fulltext_excluded_reasons={"a": 5, "b": 3})
        self.assertIn("P007", [f["code"] for f in P.check_flow(bad).findings])
        bad = dict(base, records_after_duplicates_removed=30)
        self.assertIn("P002", [f["code"] for f in P.check_flow(bad).findings])
        few = dict(base, studies_included_qualitative_synthesis=5, studies_included_quantitative_synthesis=5)
        res = P.check_flow(few)
        self.assertTrue(res.ok)
        self.assertIn("P014", [f["code"] for f in res.findings])
        many = dict(base, studies_included_qualitative_synthesis=7, studies_included_quantitative_synthesis=6)
        self.assertIn("P005", [f["code"] for f in P.check_flow(many).findings])

    FLOW_2020 = dict(identified_databases=1200, identified_registers=45, duplicates_removed=300,
                     automation_removed=0, other_removed=5, screened=940, excluded_screening=880,
                     sought=60, not_retrieved=3, assessed=57, excluded_eligibility=45,
                     excluded_eligibility_reasons={"nem RCT": 30, "rossz populáció": 10, "nincs kimenet": 5},
                     included_reports=12, included_studies=10, included_meta=8)

    def test_2020_consistent(self):
        res = P.check_flow(self.FLOW_2020)
        self.assertEqual(res.template, "PRISMA2020")
        self.assertEqual(res.findings, [])
        self.assertTrue(res.ok)
        self.assertEqual(res.identified_total, 1245)
        self.assertEqual(res.value("removed_before_screening_total"), 305)
        self.assertEqual(res.to_dict()["summary"], {"error": 0, "warning": 0, "info": 0})

    def test_2020_each_equation(self):
        cases = {"P002": dict(screened=941), "P003": dict(sought=61), "P004": dict(assessed=56),
                 "P005": dict(included_reports=11), "P006": dict(included_studies=13),
                 "P007": dict(excluded_eligibility_reasons={"nem RCT": 30}), "P010": dict(included_meta=11)}
        for code, change in cases.items():
            res = P.check_flow(dict(self.FLOW_2020, **change))
            self.assertIn(code, [f["code"] for f in res.findings], code)
            self.assertFalse(res.ok, code)
            for f in res.findings:
                self.assertTrue(f["title"] and f["advice"] and f["source"], f)

    def test_invalid_numbers(self):
        for bad in (-1, 2.5, "sok", True):
            res = P.check_flow(dict(self.FLOW_2020, not_retrieved=bad))
            self.assertIn("P001", [f["code"] for f in res.findings], bad)
            self.assertFalse(res.ok)
        res = P.check_flow(dict(self.FLOW_2020, screened="940", sought=60.0))
        self.assertTrue(res.ok, res.findings)
        res = P.check_flow(dict(self.FLOW_2020, excluded_eligibility_reasons={"a": -1, "b": 46}))
        self.assertIn("P001", [f["code"] for f in res.findings])

    def test_missing_boxes_derived_or_reported(self):
        flow = {k: v for k, v in self.FLOW_2020.items() if k not in ("not_retrieved", "included_reports")}
        res = P.check_flow(flow)
        self.assertTrue(res.ok, res.findings)
        self.assertEqual(res.value("not_retrieved"), 3)
        self.assertEqual(res.value("included_reports"), 12)
        self.assertIn("P011", [f["code"] for f in res.findings])
        res = P.check_flow(dict(identified_databases=100, screened=80))
        self.assertIn("P012", [f["code"] for f in res.findings])
        self.assertTrue(res.ok)
        flow = dict(self.FLOW_2020, excluded_eligibility_reasons=None)
        self.assertIn("P008", [f["code"] for f in P.check_flow(flow).findings])

    def test_other_methods_branch_and_updates(self):
        flow = dict(self.FLOW_2020, other_methods_identified=20, other_methods_sought=8,
                    other_methods_not_retrieved=1, other_methods_assessed=7, other_methods_excluded=4,
                    other_methods_excluded_reasons={"nem RCT": 4}, included_reports=15, included_studies=12)
        res = P.check_flow(flow)
        self.assertEqual(res.findings, [], res.findings)
        self.assertEqual(res.value("other_methods_included_reports"), 3)
        bad = dict(flow, other_methods_assessed=6)
        self.assertIn("P009", [f["code"] for f in P.check_flow(bad).findings])
        bad = dict(flow, other_methods_sought=25, other_methods_assessed=24, other_methods_excluded=21,
                   other_methods_excluded_reasons={"x": 21})
        self.assertIn("P016", [f["code"] for f in P.check_flow(bad).findings])
        bad = dict(flow, included_reports=12)
        self.assertIn("P005", [f["code"] for f in P.check_flow(bad).findings])
        upd = dict(flow, previous_studies=20, previous_reports=25, total_studies=32, total_reports=40)
        self.assertTrue(P.check_flow(upd).ok)
        bad = dict(upd, total_studies=31)
        self.assertIn("P015", [f["code"] for f in P.check_flow(bad).findings])

    COMPOSER = {  # a composer plugin `prisma export --format flow-json` kimenete (compute_flow)
        "databases": [{"source": "PubMed", "kind": "database", "count_total": 120, "retrieved": 120},
                      {"source": "Embase", "kind": "database", "count_total": 80, "retrieved": 80}],
        "registers": [{"source": "CT.gov", "kind": "register", "count_total": 10, "retrieved": 10}],
        "other_sources": [], "identified_databases": 200, "identified_registers": 10, "identified_other": 0,
        "identified_total": 210, "retrieved_total": 210, "retrieval_gap": 0, "dedup_removed": 40,
        "removed_before_screening": [{"reason": "nem angol nyelvű", "count": 5}],
        "removed_before_screening_n": 5, "screened": 165, "excluded_screening": 130,
        "sought_for_retrieval": 35, "not_retrieved": 3, "assessed_eligibility": 32, "excluded_eligibility": 27,
        "excluded_eligibility_reasons": {"nem RCT": 20, "rossz kimenet": 7}, "included": 5, "undecided": 0}

    def test_composer_flow(self):
        res = P.check_flow(P.from_composer(self.COMPOSER))
        self.assertEqual(res.findings, [])
        self.assertEqual(res.value("included_reports"), 5)
        self.assertEqual(res.value("other_removed"), 5)
        self.assertTrue(P.check_flow(self.COMPOSER).ok)        # közvetlenül is felismeri a mezőket
        pending = dict(self.COMPOSER, excluded_eligibility=25,
                       excluded_eligibility_reasons={"nem RCT": 18, "rossz kimenet": 7}, undecided=2)
        res = P.check_flow(P.from_composer(pending))
        self.assertTrue(res.ok, res.findings)
        self.assertIn("P013", [f["code"] for f in res.findings])
        gap = dict(self.COMPOSER, identified_databases=230, retrieval_gap=30)
        res = P.check_flow(P.from_composer(gap))
        p2 = [f for f in res.findings if f["code"] == "P002"]
        self.assertEqual(len(p2), 1)
        self.assertIn("retrieval_gap", p2[0]["detail"])

    def test_markdown_table(self):
        md = "\n".join([
            "| Lépés | Szám | Ellenőrzés |", "|---|---|---|",
            "| Azonosított rekordok — adatbázisok (n = A1) | 1200 | |",
            "| Azonosított rekordok — regiszterek (n = A2) | 45 | |",
            "| Szűrés előtt eltávolítva: duplikátumok (D1), automatikusan kizárt (D2), egyéb (D3) | 300 / 0 / 5 | |",
            "| Szűrt rekordok (n = B) | 940 | B = A1 + A2 − D1 − D2 − D3 |",
            "| Kizárt rekordok (cím/absztrakt) (n = C) | 880 | |",
            "| Teljes szövegre keresett (n = E) | 60 | E = B − C |",
            "| Nem elérhető teljes szöveg (n = F) | 3 | |",
            "| Teljes szövegben értékelt (n = G) | 57 | G = E − F |",
            "| Kizárt teljes szöveg okokkal (n = H = H1 + H2 + …) | 45 (nem RCT: 30; rossz populáció: 10; "
            "nincs kimenet: 5) | okonként felsorolva |",
            "| Bevont közlemények (n = J) és vizsgálatok (n = I) | J=12, I=10 | I ≤ J |",
            "| Ebből metaanalízisben (kimenetenként) | 8 | |"])
        flow = P.parse_markdown_table(md)
        self.assertEqual(flow, self.FLOW_2020)
        tpl_path = os.path.join(os.path.dirname(SC.HERE), "tudasbazis", "sablonok", "prisma_folyamat_sablon.md")
        if os.path.exists(tpl_path):
            with open(tpl_path, encoding="utf-8") as fh:
                self.assertEqual(P.parse_markdown_table(fh.read()), {})     # kitöltetlen sablon
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "prisma_folyamat.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(md.replace("| 880 |", "| 870 |"))
            res = P.load(p)
            self.assertIn("P003", [f["code"] for f in res.findings])
            q = os.path.join(td, "prisma-flow.json")
            with open(q, "w", encoding="utf-8") as fh:
                json.dump(self.COMPOSER, fh)
            self.assertTrue(P.load(q).ok)

    def test_template_argument(self):
        with self.assertRaises(P.PrismaError):
            P.check_flow(self.FLOW_2020, template="PRISMA2015")
        self.assertEqual(P.check_flow(self.FLOW_2020, template="prisma 2020").template, "PRISMA2020")
        self.assertTrue(all(code.startswith("P") and len(v) == 4 for code, v in P.RULES.items()))


if __name__ == "__main__":
    unittest.main()
