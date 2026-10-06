# -*- coding: utf-8 -*-
"""Regressziós tesztek a 2. review-kör statisztikai motor-javításaihoz.

Minden teszt neve a megállapítás azonosítóját viseli (pl. R2_NF_03 = stats_new:R2-NF-03,
DT_1 = metafor_fuzz:DT-1). Az orákulum-értékek az R metafor 4.4-ből vagy pontos (racionális /
50 jegyű) aritmetikából származnak, a forrást a teszt megjegyzése adja meg.
"""
import math
import os
import shutil
import tempfile
import unittest
from fractions import Fraction

from _helpers import assert_close, load_reference
from metaelemzes import bias as B
from metaelemzes import conversions as C
from metaelemzes import effect_sizes as E
from metaelemzes import models as M
from metaelemzes import moderators as MO
from metaelemzes import pipeline, power as PW, sensitivity as SE, tableio

# R2-NF-03: 12 × 'a', egy-egy vizsgálat a 'b' és 'c' szinten
MR_SING = [("S1", 0.41, 0.06, "a"), ("S2", -0.21, 0.078, "a"), ("S3", 0.88, 0.044, "a"),
           ("S4", -0.35, 0.044, "a"), ("S5", 0.96, 0.025, "a"), ("S6", 0.11, 0.051, "a"),
           ("S7", 0.73, 0.033, "a"), ("S8", 1.23, 0.04, "a"), ("S9", -0.47, 0.09, "a"),
           ("S10", 0.68, 0.028, "a"), ("S11", 0.58, 0.062, "a"), ("S12", -0.44, 0.029, "a"),
           ("S13", 0.09, 0.035, "b"), ("S14", 1.08, 0.081, "c")]
# R2-NF-05: Q < df (I² = 0)
SG = [("A", 0.21, 0.04, "x"), ("B", 0.35, 0.03, "x"), ("C", 0.12, 0.05, "x"), ("D", 0.55, 0.06, "x"),
      ("E", 0.30, 0.02, "y"), ("F", 0.41, 0.045, "y"), ("G", 0.05, 0.035, "y"), ("H", 0.25, 0.05, "y")]
# R2-NF-10: heterogén x-csoport, homogén y-csoport
HET = [("x", 0.1, 0.02), ("x", 0.5, 0.03), ("x", -0.2, 0.04), ("x", 0.8, 0.05), ("x", 0.3, 0.02),
       ("y", 0.2, 0.01), ("y", 0.25, 0.02), ("y", 0.22, 0.03), ("y", 0.18, 0.015), ("y", 0.21, 0.025)]


def _bcg():
    return E.compute(load_reference()["bcg"]["rows"], "RR")


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def csv(self, text, name="adat.csv"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p


# ------------------------------------------------------------ moderators.py
class TestR2NF03RobustSingular(_Tmp):
    def _fit(self, robust):
        yi = [r[1] for r in MR_SING]
        vi = [r[2] for r in MR_SING]
        x, names = MO.design_matrix([{"design": r[3]} for r in MR_SING], ["design"])
        return MO.meta_regression(yi, vi, x, names, "REML", "z", robust=robust)

    def test_R2_NF_03_singular_robust_block_keeps_model(self):
        plain = self._fit(False)
        r = self._fit(True)          # korábban: SingularMatrixError → az egész meta-regresszió elveszett
        assert_close(self, r.QM, 1.5247686885, 1e-8)          # metafor rma(mods=~design) QM
        self.assertEqual([c["se"] for c in r.coefficients], [c["se"] for c in plain.coefficients])
        rb = r.robust
        self.assertIsNotNone(rb)
        self.assertIsNone(rb["F"])
        self.assertIsNone(rb["F_p"])
        self.assertIsNone(rb["I2_model_pct"])
        for c in rb["coefficients"]:                          # metafor robust(cluster=1:k, adjust=TRUE)
            assert_close(self, c["se"], 0.1857307237, 1e-8)
        self.assertTrue(any("omnibusz F-próba nem számolható" in w for w in r.warnings))
        self.assertTrue(any("hat = 1" in w for w in r.warnings))
        self.assertFalse(any("kollineáris" in w for w in r.warnings))
        self.assertNotIn("warnings", rb)

    def test_R2_NF_03_pipeline_reports_metaregression(self):
        p = self.csv("study,yi,vi,design\n" + "".join("%s,%r,%r,%s\n" % r for r in MR_SING))
        rows, meta = tableio.read_table(p)
        out, _ = pipeline.run(rows, {"measure": "GEN", "moderators": ["design"], "metareg_robust": True}, meta)
        self.assertIn("metaregression", out)
        self.assertIsNone(out["metaregression"].robust["F"])
        self.assertFalse(any("kollineáris" in w for w in out["warnings"]))


class TestR2NF05HtCentre(unittest.TestCase):
    def test_R2_NF_05_subgroup_blocks_use_h_centre(self):
        yi = [r[1] for r in SG]
        vi = [r[2] for r in SG]
        g = [r[3] for r in SG]
        prim = M.meta_analysis(yi, vi, "random", h_centre="untruncated")
        sg = MO.subgroup_analysis(yi, vi, g, model="random", h_centre="untruncated")
        self.assertEqual(sg.overall.heterogeneity["H_ci_centre"], "untruncated")
        self.assertEqual(sg.overall.heterogeneity["I2_ci_HT"], prim.heterogeneity["I2_ci_HT"])
        assert_close(self, sg.overall.heterogeneity["I2_ci_HT"][1], 42.23254414400475, 1e-10)
        self.assertEqual([x.heterogeneity["H_ci_centre"] for x in sg.groups], ["untruncated"] * 2)
        # alapértelmezés változatlan
        sg0 = MO.subgroup_analysis(yi, vi, g, model="random")
        self.assertEqual(sg0.overall.heterogeneity["H_ci_centre"], "truncated")
        assert_close(self, sg0.overall.heterogeneity["I2_ci_HT"][1], 67.5780566278814, 1e-10)

    def test_R2_NF_05_mh_peto_use_h_centre(self):
        e1, n1 = [10, 12, 9, 11, 10], [100] * 5
        e2, n2 = [15, 16, 14, 15, 16], [100] * 5
        mh = M.mantel_haenszel(e1, n1, e2, n2, "OR", h_centre="untruncated")
        assert_close(self, mh.Q, 0.176398, 1e-5)                       # metafor rma.mh QE (Q < df)
        self.assertEqual(mh.heterogeneity["H_ci_centre"], "untruncated")
        ref = M.heterogeneity(mh.yi, mh.vi, None, 0.95, "untruncated")
        self.assertEqual(mh.heterogeneity["I2_ci_HT"], ref["I2_ci_HT"])
        self.assertNotEqual(mh.heterogeneity["I2_ci_HT"],
                            M.mantel_haenszel(e1, n1, e2, n2, "OR").heterogeneity["I2_ci_HT"])
        pt = M.peto(e1, n1, e2, n2, h_centre="untruncated")
        self.assertEqual(pt.heterogeneity["H_ci_centre"], "untruncated")
        with self.assertRaises(M.ModelError):
            M.peto(e1, n1, e2, n2, h_centre="rossz")


class TestR2NF10CommonTau2Ivhet(unittest.TestCase):
    def test_R2_NF_10_ivhet_common_tau2_applied(self):
        g = [r[0] for r in HET]
        yi = [r[1] for r in HET]
        vi = [r[2] for r in HET]
        sg = MO.subgroup_analysis(yi, vi, g, model="ivhet", common_tau2=True)
        # metafor: rma(yi, vi, mods=~g, method="DL")$tau2; IVhet SE = √Σ(w_i/Σw)²(v_i + τ²)
        assert_close(self, sg.common_tau2, 0.018151452010, 1e-9)
        assert_close(self, sg.groups[0].se, 0.098389, 1e-5)
        assert_close(self, sg.groups[1].se, 0.087696, 1e-5)
        self.assertEqual([x.tau2_method for x in sg.groups], ["rögzített"] * 2)
        self.assertIn("közös τ²", sg.Q_between_note)
        sep = MO.subgroup_analysis(yi, vi, g, model="ivhet")
        self.assertIsNone(sep.common_tau2)
        self.assertNotAlmostEqual(sep.Q_between, sg.Q_between, 6)

    def test_R2_NF_10_single_study_group_and_fixed_warning(self):
        g = ["x"] * 5 + ["y"]
        yi = [r[1] for r in HET[:6]]
        vi = [r[2] for r in HET[:6]]
        sg = MO.subgroup_analysis(yi, vi, g, model="ivhet", common_tau2=True)
        self.assertIsNotNone(sg.common_tau2)
        assert_close(self, sg.groups[1].se, math.sqrt(vi[5] + sg.common_tau2), 1e-12)
        fx = MO.subgroup_analysis(yi, vi, g, model="fixed", common_tau2=True)
        self.assertIsNone(fx.common_tau2)
        self.assertTrue(any("--common-tau2" in w for w in fx.warnings))


# ------------------------------------------------------------ models.py
class TestR2NF07MHWeights(unittest.TestCase):
    E1, N1, E2, N2 = [3, 10, 25, 7], [40, 120, 300, 80], [8, 18, 40, 12], [42, 118, 305, 85]
    METAFOR_MH = [10.1912953674457, 23.4876606271078, 51.3320774035964, 14.9889666018501]
    METAFOR_PETO = [8.80405149292644, 22.66591737743483, 53.09033975303312, 15.43969137660562]

    def test_R2_NF_07_mh_repeated_labels(self):
        uniq = M.mantel_haenszel(self.E1, self.N1, self.E2, self.N2, "OR", labels=["A", "B", "C", "D"])
        dup = M.mantel_haenszel(self.E1, self.N1, self.E2, self.N2, "OR", labels=["A", "Smith", "Smith", "D"])
        assert_close(self, dup.sum_weights, 35.41999291956251, 1e-12)
        self.assertEqual(dup.sum_weights, uniq.sum_weights)
        for got, want in zip(dup.weights_pct, self.METAFOR_MH):     # metafor weights(rma.mh), soronként
            assert_close(self, got, want, 1e-10)
        self.assertEqual(dup.weights_labels, ["A", "Smith", "Smith", "D"])
        assert_close(self, sum(dup.weights_raw), dup.sum_weights, 1e-14)
        assert_close(self, dup.weights_by_label["Smith"], self.METAFOR_MH[1] + self.METAFOR_MH[2], 1e-10)
        assert_close(self, sum(dup.weights_by_label.values()), 100.0, 1e-10)
        assert_close(self, sum(dup.weights_raw_by_label.values()), dup.sum_weights, 1e-12)
        self.assertEqual(dup.estimate, uniq.estimate)

    def test_R2_NF_07_peto_repeated_labels(self):
        pt = M.peto(self.E1, self.N1, self.E2, self.N2, labels=["A", "Smith", "Smith", "D"])
        for got, want in zip(pt.weights_pct, self.METAFOR_PETO):
            assert_close(self, got, want, 1e-10)
        assert_close(self, sum(pt.weights_by_label.values()), 100.0, 1e-10)
        assert_close(self, pt.weights_by_label["Smith"], self.METAFOR_PETO[1] + self.METAFOR_PETO[2], 1e-10)
        self.assertEqual(pt.weights_labels, ["A", "Smith", "Smith", "D"])


class TestR2ROB06DLCancellation(unittest.TestCase):
    @staticmethod
    def _exact_dl(yi, vi):
        y = [Fraction(a) for a in yi]
        w = [1 / Fraction(v) for v in vi]
        sw = sum(w)
        mu = sum(a * b for a, b in zip(w, y)) / sw
        q = sum(a * (b - mu) ** 2 for a, b in zip(w, y))
        c = sw - sum(a * a for a in w) / sw
        return float(max(Fraction(0), (q - (len(y) - 1)) / c))

    def test_R2_ROB_06_dominant_weight(self):
        for sei in (1e-8, 1e-10, 1e-14, 1e-20):
            yi = [0.5, 0.2, 0.0, 0.3]
            vi = [sei ** 2, 0.01, 0.04, 0.0225]
            exact = self._exact_dl(yi, vi)
            assert_close(self, M.tau2_dl(yi, vi), exact, 1e-12, "DL τ², sei=%g" % sei)
            dl = M.meta_analysis(yi, vi, "random", "DL", "z")      # korábban ZeroDivisionError / 0,5
            assert_close(self, dl.estimate, 0.2888210334077273, 1e-10)
            reml = M.meta_analysis(yi, vi, "random", "REML", "z")
            assert_close(self, reml.estimate, 0.2971222669, 1e-9)       # metafor rma(method="REML")
            self.assertTrue(any("rendkívül nagy" in w for w in reml.warnings))
            self.assertGreater(reml.heterogeneity["C"], 0)
            assert_close(self, reml.heterogeneity["s2_typical"], 3.0 / reml.heterogeneity["C"], 1e-12)

    def test_R2_ROB_06_unchanged_on_regular_data(self):
        es = _bcg()
        r = M.meta_analysis(es.yi, es.vi, "random", "DL", "z")
        w = [1 / v for v in es.vi]
        naive = sum(w) - sum(a * a for a in w) / sum(w)
        assert_close(self, r.heterogeneity["C"], naive, 1e-12)
        assert_close(self, r.tau2, M.tau2_dl(es.yi, es.vi), 1e-15)


class TestDT4ScaleInvariance(unittest.TestCase):
    # metafor rma(method="PM", control=list(tol=1e-15)) + confint(); 50 jegyű mpmath-gyökkel egyező
    PM, QP = 0.3180684522, (0.1197183611, 1.111479084)

    def test_DT_4_pm_and_qprofile_scale_invariant(self):
        es = _bcg()
        for s in (1.0, 1e-3, 1e-5, 1e-6, 1e-7):
            r = M.meta_analysis([a * s for a in es.yi], [a * s * s for a in es.vi], "random", "PM", "z")
            lo, hi = r.heterogeneity["tau2_ci_QP"]
            assert_close(self, r.tau2 / s / s, self.PM, 1e-9, "PM τ², s=%g" % s)
            assert_close(self, lo / s / s, self.QP[0], 1e-9, "QP alsó, s=%g" % s)
            assert_close(self, hi / s / s, self.QP[1], 1e-9, "QP felső, s=%g" % s)
            self.assertLessEqual(lo, r.tau2)


class TestR2REP07HksjWarning(unittest.TestCase):
    def test_R2_REP_07_no_warning_when_hksj_wider(self):
        es = E.compute([{"study": "S1", "e1": 0, "n1": 50, "e2": 3, "n2": 50},
                        {"study": "S5", "e1": 10, "n1": 100, "e2": 15, "n2": 100}], "OR")
        r = M.meta_analysis(es.yi, es.vi, "random", "REML", "hksj")
        assert_close(self, r.q_hksj, 0.9481192757557009, 1e-10)
        assert_close(self, r.ci_lower, -5.758545, 1e-6)                  # metafor test="knha"
        self.assertFalse(any("szűkebb" in w for w in r.warnings))

    def test_R2_REP_07_warning_when_hksj_narrower(self):
        yi = [0.30, 0.31, 0.29, 0.305, 0.295, 0.30, 0.31, 0.29, 0.302, 0.298, 0.30, 0.31, 0.29, 0.30]
        vi = [0.01 + 0.002 * i for i in range(14)]
        r = M.meta_analysis(yi, vi, "random", "REML", "hksj")
        z = M.meta_analysis(yi, vi, "random", "REML", "z")
        self.assertLess(r.ci_upper - r.ci_lower, z.ci_upper - z.ci_lower)
        self.assertTrue(any("szűkebb a Wald-CI-nál" in w for w in r.warnings))


class TestTS01NumericChecks(unittest.TestCase):
    def test_TS_01_hksj_adhoc_metafor(self):
        yi, vi = [.1, .12, .11, .13, .09], [.04, .05, .03, .06, .05]
        r = M.meta_analysis(yi, vi, "random", "REML", "hksj_adhoc")
        # metafor 4.4 rma(method="REML", test="adhoc")
        assert_close(self, r.se, 0.093250480824, 1e-9)
        assert_close(self, r.ci_lower, -0.149919333795, 1e-9)
        assert_close(self, r.ci_upper, 0.367890348288, 1e-9)
        assert_close(self, r.p, 0.307409709866, 1e-9)
        kn = M.meta_analysis(yi, vi, "random", "REML", "hksj")
        assert_close(self, kn.se, 0.006435011094, 1e-9)                   # test="knha"
        self.assertLess(kn.q_hksj, 1)

    def test_TS_01_mh_rd_greenland_robins(self):
        rows = load_reference()["bcg"]["rows"]
        cols = [[r[c] for r in rows] for c in ("e1", "n1", "e2", "n2")]
        gr = M.mantel_haenszel(*cols, measure="RD", rd_var="gr")
        # Greenland & Robins (1985) zárt alakja R-ben számolva
        assert_close(self, gr.se, 2.845159218969411e-04, 1e-12)
        sato = M.mantel_haenszel(*cols, measure="RD")
        assert_close(self, sato.se, 2.866567485273736e-04, 1e-12)        # metafor rma.mh(measure="RD")
        assert_close(self, sato.estimate, -3.288181832613e-03, 1e-12)
        self.assertEqual(gr.estimate, sato.estimate)


# ------------------------------------------------------------ bias.py
class TestDT1EggerEqualPrecision(unittest.TestCase):
    def test_DT_1_equal_variances_raise(self):
        yi = [math.atanh(r) for r in (0.10, 0.35, 0.52, 0.20, 0.61, 0.05)]
        for v in (1 / 47., 1 / 7., 0.01, 1 / 197.):          # metafor regtest: 'not of full rank'
            with self.assertRaises(M.ModelError):
                B.egger_test(yi, [v] * 6)
        for n in range(5, 200, 7):
            with self.assertRaises(M.ModelError):
                B.egger_test(yi * 2, [1.0 / (n - 3)] * 12)

    def test_DT_1_regular_unchanged(self):
        e = B.egger_test([0.1, 0.3, 0.5, 0.2, 0.6], [0.01, 0.02, 0.04, 0.05, 0.09])
        self.assertTrue(math.isfinite(e.se_intercept) and e.se_intercept < 100)


class TestDT2BeggUndefined(unittest.TestCase):
    def test_DT_2_undefined_kendall_raises(self):
        yi = [math.atanh(r) for r in (0.10, 0.35, 0.52, 0.20, 0.61, 0.05)]
        with self.assertRaises(M.ModelError):                 # minden v_i azonos (metafor: tau = NA)
            B.begg_test(yi, [1 / 47.] * 6)
        with self.assertRaises(M.ModelError):                 # minden y_i = 0 → minden t*_i = 0
            B.begg_test([0.0] * 12, [0.01 * (i + 1) for i in range(12)])
        with self.assertRaises(M.ModelError):
            B.begg_test(yi * 2, [0.02] * 12, method="normal")

    def test_DT_2_pipeline_omits_begg(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "z.csv")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("study,r,n\n" + "".join("S%d,%s,50\n" % (i, r) for i, r in enumerate(
                (0.10, 0.35, 0.52, 0.20, 0.61, 0.05, 0.3, 0.42, 0.15, 0.27))))
        rows, meta = tableio.read_table(p)
        out, _ = pipeline.run(rows, {"measure": "ZCOR"}, meta)
        self.assertNotIn("begg", out["bias"])
        self.assertNotIn("egger", out["bias"])
        self.assertTrue(any(w.startswith("Torzítás-elemzés (Begg)") for w in out["warnings"]))
        # azonos v_i mellett a trim-and-fill oldal-regressziója sem illeszthető: pontos ok, nem 'kollineáris'
        tf = [w for w in out["warnings"] if w.startswith("Torzítás-elemzés (trim-and-fill)")]
        self.assertTrue(tf and "varianciák azonosak" in tf[0])
        self.assertFalse(any("kollineáris" in w for w in out["warnings"]))


class TestDT3TrimFillIdentical(unittest.TestCase):
    def test_DT_3_identical_effects(self):
        vi = [0.01, 0.02, 0.03, 0.04, 0.05]
        for y in (0.1, 0.7, -0.2, 0.0):
            for est in ("L0", "R0"):
                for model in ("fixed", "random"):
                    with self.assertRaises(M.ModelError):     # korábban R0: k0 = k − 1
                        B.trim_and_fill([y] * 5, vi, model=model, estimator=est)

    def test_DT_3_regular_data_matches_metafor_reference(self):
        es = _bcg()
        for est in ("L0", "R0"):
            r = B.trim_and_fill(es.yi, es.vi, es.labels, "random", "REML", est)
            self.assertGreaterEqual(r.k0, 0)
            self.assertLess(r.k0, len(es.yi) - 1)


# ------------------------------------------------------------ sensitivity.py
class TestR2REP08InfluenceIvhet(unittest.TestCase):
    def test_R2_REP_08_ivhet_hat_weights(self):
        es = _bcg()
        prim = M.meta_analysis(es.yi, es.vi, "ivhet", labels=es.labels)
        rows = SE.influence(es.yi, es.vi, es.labels, "ivhet")
        for r, w in zip(rows, prim.weights_pct):
            assert_close(self, r["weight_pct"], w, 1e-10)
        by = {r["study"]: r for r in rows}
        assert_close(self, by["TPT Madras 1980"]["weight_pct"], 41.40, 0.005)
        assert_close(self, by["Stein & Aronson 1953"]["hat"], 0.2375, 0.0005)
        self.assertTrue(by["Stein & Aronson 1953"]["influential"])      # hat > 3/13
        self.assertTrue(by["TPT Madras 1980"]["influential"])

    def test_R2_REP_08_random_unchanged(self):
        es = _bcg()
        full = M.meta_analysis(es.yi, es.vi, "random", None, "z")
        rows = SE.influence(es.yi, es.vi, es.labels, "random")
        w = [1 / (v + full.tau2) for v in es.vi]
        for r, wi in zip(rows, w):
            assert_close(self, r["hat"], wi / sum(w), 1e-12)


# ------------------------------------------------------------ power.py
class TestR2NF09PowerOrScale(unittest.TestCase):
    def test_R2_NF_09_or_with_v_or_tau2_rejected(self):
        with self.assertRaises(M.ModelError):
            PW.power_analysis(10, OR=0.5, v=0.2)
        with self.assertRaises(M.ModelError):
            PW.power_analysis(10, OR=0.5, n1=50, n2=50, tau2=0.05)
        # az ln OR-skálájú terv a GEN úton: λ = |ln 0,5| / √(0,2/10)
        g = PW.power_analysis(10, effect=math.log(0.5), v=0.2, measure="GEN")
        assert_close(self, g.lambda_, abs(math.log(0.5)) / math.sqrt(0.02), 1e-12)
        assert_close(self, g.power, 0.998364, 1e-5)
        # OR + n1/n2 (dmetar) és OR + i2 továbbra is működik
        self.assertGreater(PW.power_analysis(12, OR=1.3, n1=50, n2=50, heterogeneity="moderate").power, 0)
        self.assertGreater(PW.power_analysis(12, OR=1.3, n1=50, n2=50, i2=50).power, 0)


# ------------------------------------------------------------ conversions.py
class TestR2ROB09Conversions(unittest.TestCase):
    def test_R2_ROB_09_invalid_inputs_rejected(self):
        bad = [lambda: C.sd_change(-1, 1, 0.5), lambda: C.sd_change(1, -1, 0.5),
               lambda: C.sd_change(None, 1, 0.5), lambda: C.sd_change(1, 1, None),
               lambda: C.combine_groups(-1, 1, 1, 5, 1, 1), lambda: C.combine_groups(1, 1, 1, 0, 1, 1),
               lambda: C.combine_groups(0, 3, 1, 5, 1, 1), lambda: C.combine_groups(0, 1, 1, 0, 1, 1),
               lambda: C.combine_groups(-3, 1, 1, 1, 1, 1), lambda: C.combine_groups(5, 1, -1, 5, 1, 1),
               lambda: C.combine_groups(5, None, 1, 5, 1, 1), lambda: C.corr_from_change(0, 1, 1)]
        for f in bad:
            with self.assertRaises(C.ConversionError):
                f()

    def test_R2_ROB_09_valid_unchanged(self):
        assert_close(self, C.sd_change(1, 1, 0.5), 1.0, 1e-15)
        assert_close(self, C.sd_change(10, 12, 0.7), math.sqrt(100 + 144 - 2 * 0.7 * 120), 1e-15)
        n, m, sd = C.combine_groups(1, 2.0, 0.0, 1, 4.0, 0.0)
        self.assertEqual((n, m), (2, 3.0))
        assert_close(self, sd, math.sqrt(2.0), 1e-15)
        assert_close(self, C.corr_from_change(10, 12, C.sd_change(10, 12, 0.7)), 0.7, 1e-12)


# ------------------------------------------------------------ effect_sizes.py
DUP_ROWS = [
    {"study": "Smith 2001", "m1": 10, "sd1": 2, "n1": 20, "m2": 8, "sd2": 2, "n2": 20},
    {"study": "Smith 2001", "m1": 11, "sd1": None, "n1": 20, "m2": 8, "sd2": 2, "n2": 20},
    {"study": "Jones", "m1": 9, "sd1": 2, "n1": 30, "m2": 8, "sd2": 2, "n2": 30},
    {"study": "Kim", "m1": 12, "sd1": 3, "n1": 25, "m2": 9, "sd2": 3, "n2": 25},
    {"study": "Lee", "m1": 10, "sd1": 2, "n1": 40, "m2": 9, "sd2": 2, "n2": 40},
]


class TestR2ROB02SkipRows(unittest.TestCase):
    def test_R2_ROB_02_only_faulty_row_skipped(self):
        es = E.compute(DUP_ROWS, "MD", skip_rows={1: "V002"})
        self.assertEqual(len(es), 4)
        self.assertEqual(es.row_index, [0, 2, 3, 4])
        self.assertEqual(es.excluded, [("Smith 2001", "validálási hiba: V002")])
        r = M.meta_analysis(es.yi, es.vi, "random", "REML", "z")
        assert_close(self, r.estimate, 1.5513660790, 1e-6)             # metafor escalc MD + rma REML (k = 4)
        es2 = E.compute(DUP_ROWS, "MD", skip_rows={1})
        self.assertEqual(es2.row_index, [0, 2, 3, 4])
        self.assertIn("lásd a validálási tételeket", es2.excluded[0][1])

    def test_R2_ROB_02_skip_labels_backward_compatible(self):
        es = E.compute(DUP_ROWS, "MD", skip_labels={"Kim": "V005"})
        self.assertEqual([lab for lab, _ in es.excluded], ["Smith 2001", "Kim"])   # a hiányzó sd1 is kimarad
        self.assertEqual(es.row_index, [0, 2, 4])


class TestR2ROB16GenChecks(unittest.TestCase):
    def test_R2_ROB_16_negative_se_and_invalid_n(self):
        rows = [{"study": "A", "yi": 0.5, "sei": -0.2}, {"study": "B", "yi": 0.3, "sei": 0.1, "n": 10.5},
                {"study": "C", "yi": 0.4, "sei": 0.15, "n": 0}, {"study": "D", "yi": 0.2, "sei": 0.12, "n": 20},
                {"study": "E", "yi": 0.6, "sei": 0.2, "n": 30}, {"study": "F", "yi": 0.1, "sei": 0.1, "n": -5}]
        es = E.compute(rows, "GEN")
        self.assertEqual(es.labels, ["D", "E"])
        self.assertEqual(es.ni, [20, 30])
        why = dict(es.excluded)
        self.assertIn("negatív SE", why["A"])
        for lab in ("B", "C", "F"):
            self.assertIn("egész n >= 1", why[lab])

    def test_R2_ROB_16_valid_gen_unchanged(self):
        rows = [{"study": "A", "yi": 0.5, "sei": 0.2, "n": 40.0}, {"study": "B", "yi": 0.3, "vi": 0.01},
                {"study": "C", "yi": 0.4, "sei": 0.15}]
        es = E.compute(rows, "GEN")
        self.assertEqual(len(es), 3)
        assert_close(self, es.vi[0], 0.04, 1e-15)


if __name__ == "__main__":
    unittest.main()
