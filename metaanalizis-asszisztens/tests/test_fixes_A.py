# -*- coding: utf-8 -*-
"""Regressziós tesztek az A-csomag (statisztikai mag) javításaihoz.

A referenciaértékek az R metafor 4.4-ből származnak (szoros konvergencia-küszöbbel:
control = list(threshold = 1e-13)); a generáló R-kód a tesztek mellett, kommentben.
"""
import itertools
import math
import random
import unittest

from _helpers import assert_close
from metaelemzes import bias as B
from metaelemzes import models as M
from metaelemzes import moderators as MO
from metaelemzes import sensitivity as SE

# dat.bcg (metafor), RR
BCG_TPOS = [4, 6, 3, 62, 33, 180, 8, 505, 29, 17, 186, 5, 27]
BCG_TNEG = [119, 300, 228, 13536, 5036, 1361, 2537, 87886, 7470, 1699, 50448, 2493, 16886]
BCG_CPOS = [11, 29, 11, 248, 47, 372, 10, 499, 45, 65, 141, 3, 29]
BCG_CNEG = [128, 274, 209, 12619, 5761, 1079, 619, 87892, 7232, 1600, 27197, 2338, 17825]
BCG_ALLOC = ["random"] * 4 + ["alternate"] * 2 + ["random"] * 3 + ["systematic"] * 4


def bcg_rr():
    yi, vi = [], []
    for a, b, c, d in zip(BCG_TPOS, BCG_TNEG, BCG_CPOS, BCG_CNEG):
        yi.append(math.log((a / (a + b)) / (c / (c + d))))
        vi.append(1 / a - 1 / (a + b) + 1 / c - 1 / (c + d))
    return yi, vi


class TestTau2LocalMaximum(unittest.TestCase):
    """reml-ml-local-max: a Fisher-scoring lokális maximuma helyett a τ² = 0 határ / globális
    maximum (metafor: 'Fisher scoring algorithm may have gotten stuck at a local maximum')."""

    Y = [-0.0119, 3.0251, 0.0063, 1.0249, 0.705]
    V = [0.0051, 2.0161, 0.0058, 0.2462, 2.361]

    def test_reml_boundary_reset_matches_metafor(self):
        t, info = M.tau2_reml(self.Y, self.V)
        self.assertEqual(t, 0.0)
        self.assertTrue(info.get("boundary_reset"))
        self.assertGreater(info["tau2_fisher_scoring"], 0.1)
        self.assertGreater(M.reml_loglik(self.Y, self.V, 0.0),
                           M.reml_loglik(self.Y, self.V, info["tau2_fisher_scoring"]))
        r = M.meta_analysis(self.Y, self.V, "random", "REML", "z")
        # rma(y, v, test="z"): tau2 0, b 0.01262424917 [-0.08879448458; 0.1140429829]
        assert_close(self, r.tau2, 0.0, 1e-12)
        assert_close(self, r.estimate, 0.01262424917, 1e-8)
        assert_close(self, r.ci_lower, -0.08879448458, 1e-8)
        assert_close(self, r.ci_upper, 0.1140429829, 1e-8)
        self.assertTrue(r.tau2_info.get("boundary_reset"))
        self.assertTrue(any("lokális maximum" in w for w in r.warnings), r.warnings)

    def test_ml_boundary_reset(self):
        y, v = [2.530916, -0.253014, 1.694795], [1.861658, 0.004079, 2.261506]
        t, info = M.tau2_ml(y, v)
        self.assertEqual(t, 0.0)
        self.assertTrue(info.get("boundary_reset"))
        r = M.meta_analysis(y, v, "random", "ML", "z")
        assert_close(self, r.estimate, -0.2434393016, 1e-8)

    def test_global_interior_maximum_found(self):
        # A Fisher-scoring (HE-ből) 0-ba fut, de egy belső maximum likelihoodja nagyobb:
        # metafor: tau2 = 0, logLik -9.103665571; tau2 = 0.3842391734-nél logLik -8.674107594.
        y = [-0.7771, -0.8554, 0.659, -1.681, 1.0713, -0.8724, -0.1625]
        v = [0.00444, 0.85041, 2.87933, 1.7279, 0.35532, 0.02422, 2.51108]
        t, info = M.tau2_reml(y, v)
        assert_close(self, t, 0.3842391734, 1e-7)
        self.assertTrue(info.get("global_search"))
        assert_close(self, M.reml_loglik(y, v, t) - M.reml_loglik(y, v, 0.0),
                     -8.674107594 - (-9.103665571), 1e-7)
        r = M.meta_analysis(y, v, "random", "REML", "z")
        assert_close(self, r.estimate, -0.4849918066, 1e-7)
        self.assertTrue(any("globális" in w for w in r.warnings), r.warnings)

    def test_metaregression_ml_boundary(self):
        # rma(y, v, mods=x, method="ML"): tau2 0, b = (0.9624673615, 1.468749633)
        y = [2.6777, 1.3194, 2.1202, 2.5708]
        v = [0.24702, 0.00648, 0.00932, 0.22711]
        x = [[1.0, a] for a in (0.351, 0.273, 0.802, 0.46)]
        mr = MO.meta_regression(y, v, x, ["intercept", "x"], "ML")
        self.assertEqual(mr.tau2, 0.0)
        self.assertTrue(mr.tau2_info.get("boundary_reset"))
        assert_close(self, mr.coefficients[0]["estimate"], 0.9624673615, 1e-8)
        assert_close(self, mr.coefficients[1]["estimate"], 1.468749633, 1e-8)
        self.assertTrue(any("lokális maximum" in w for w in mr.warnings), mr.warnings)

    def test_metaregression_reml_boundary(self):
        y = [-2.4673, -0.544, -0.319, -0.031, 0.1065]
        v = [0.72398, 0.03004, 0.03203, 1.20401, 0.03464]
        x = [[1.0, a] for a in (0.63, 0.181, 0.432, 0.796, 0.94)]
        mr = MO.meta_regression(y, v, x, ["intercept", "x"], "REML")
        self.assertEqual(mr.tau2, 0.0)
        assert_close(self, mr.coefficients[0]["estimate"], -0.7060052134, 1e-8)
        assert_close(self, mr.coefficients[1]["estimate"], 0.8106872838, 1e-8)

    def test_mr_loglik_consistent_with_univariate(self):
        y, v = bcg_rr()
        x = [[1.0] for _ in y]
        for t in (0.0, 0.1, 0.5):
            assert_close(self, MO.mr_loglik(x, y, v, t, True), M.reml_loglik(y, v, t), 1e-10)
            assert_close(self, MO.mr_loglik(x, y, v, t, False), M.ml_loglik(y, v, t), 1e-10)
        assert_close(self, MO._tau2_mr(x, y, v, "REML")[0], M.tau2_reml(y, v)[0], 1e-9)


class TestRemlTinyVariance(unittest.TestCase):
    """reml-cancellation-tiny-vi: kiejtés-mentes nyomok + mértékegység-független tolerancia."""

    def test_tiny_vi_matches_metafor(self):
        y = [0.1, 0.5, 0.3, 0.8, 0.6]
        for v1 in (1e-6, 3.2e-10, 1e-10, 1e-11, 1e-12, 1e-14):
            t, info = M.tau2_reml(y, [v1, .1, .2, .05, .1])
            assert_close(self, t, 0.0778779331, 2e-6, "v1=%g" % v1)
        r = M.meta_analysis(y, [1e-10, .1, .2, .05, .1], "random", "REML", "z")
        assert_close(self, r.estimate, 0.416969872, 1e-6)
        assert_close(self, r.se, 0.1678293378, 1e-6)
        self.assertTrue(any("10⁷" in w for w in r.warnings), r.warnings)

    def test_scale_invariance(self):
        y = [0.1, 0.5, 0.3, 0.8, 0.6, -0.2, 0.45]
        v = [0.02, 0.1, 0.2, 0.05, 0.1, 0.07, 0.03]
        for kind in ("REML", "ML"):
            t0 = M.estimate_tau2(y, v, kind)[0]
            self.assertGreater(t0, 0)
            for s in (1e-5, 1e-3, 1e3):
                t = M.estimate_tau2([a * s for a in y], [b * s * s for b in v], kind)[0]
                assert_close(self, t / (s * s), t0, 1e-8, "%s s=%g" % (kind, s))

    def test_traces_match_naive_formula(self):
        rnd = random.Random(3)
        w = [rnd.uniform(0.5, 20) for _ in range(9)]
        sw, sw2, sw3 = sum(w), sum(x * x for x in w), sum(x ** 3 for x in w)
        tr_p, tr_pp = M._reml_traces(w)
        assert_close(self, tr_p, sw - sw2 / sw, 1e-12)
        assert_close(self, tr_pp, sw2 - 2 * sw3 / sw + (sw2 / sw) ** 2, 1e-12)


class TestSubgroupFixes(unittest.TestCase):
    def test_common_tau2_singleton_bcg_matches_metafor_qm(self):
        # rma(yi, vi, mods=~g): QM 2.819879236, p 0.2441580255, tau2 0.2908998535;
        # mods=~g-1: se = 0.2617558372 0.2542007461 0.6019173641
        yi, vi = bcg_rr()
        g = ["A"] * 6 + ["B"] * 6 + ["C"]
        for ci in ("z", "hksj"):
            r = MO.subgroup_analysis(yi, vi, g, common_tau2=True, ci_method=ci)
            assert_close(self, r.common_tau2, 0.2908998535, 1e-8)
            assert_close(self, r.Q_between, 2.819879236, 1e-7, ci)
            assert_close(self, r.p_between, 0.2441580255, 1e-7, ci)
            c = r.groups[2]
            self.assertEqual(c.k, 1)
            assert_close(self, c.se_wald, 0.6019173641, 1e-8)
            assert_close(self, c.estimate, -0.01731394822, 1e-8)
            assert_close(self, c.tau2, 0.2908998535, 1e-8)
            assert_close(self, r.groups[0].se_wald, 0.2617558372, 1e-8)

    def test_common_tau2_singleton_small(self):
        r = MO.subgroup_analysis([0.10, 0.45, 0.80, 0.20, 0.95, 0.30],
                                 [0.02, 0.03, 0.025, 0.04, 0.02, 0.03], ["A"] * 5 + ["B"],
                                 common_tau2=True, ci_method="z")
        assert_close(self, r.groups[1].se, 0.3813522111, 1e-8)
        assert_close(self, r.Q_between, 0.245483041, 1e-8)
        assert_close(self, r.p_between, 0.6202737205, 1e-8)

    def test_common_tau2_other_estimators(self):
        yi, vi = bcg_rr()
        g = ["A"] * 6 + ["B"] * 6 + ["C"]
        ref = {"HE": (2.825847299, 0.2900834198), "SJ": (2.598825308, 0.3240783411),
               "DL": (3.68403777, 0.2029148671), "ML": (3.787240384, 0.1954041965),
               "PM": (2.823667369, 0.2903811915)}
        for meth, (qm, t2) in ref.items():
            r = MO.subgroup_analysis(yi, vi, g, tau2_method=meth, common_tau2=True, ci_method="z")
            assert_close(self, r.common_tau2, t2, 1e-7, meth)
            assert_close(self, r.Q_between, qm, 1e-6, meth)

    def test_q_between_uses_wald_se_independent_of_ci(self):
        # külön REML-illesztések, rma(est, sei, mods=~g, method="FE"): QM 1.861444946, p 0.3942687589
        yi, vi = bcg_rr()
        for ci in ("z", "t", "hksj", "hksj_adhoc", None):
            r = MO.subgroup_analysis(yi, vi, BCG_ALLOC, ci_method=ci)
            assert_close(self, r.Q_between, 1.861444946, 1e-7, str(ci))
            assert_close(self, r.p_between, 0.3942687589, 1e-7, str(ci))
            self.assertEqual(r.Q_between_se, "wald")

    def test_identical_effects_group_no_crash(self):
        # HKSJ se = 0 a 'x' csoportban; korábban ZeroDivisionError.
        r = MO.subgroup_analysis([0.1, 0.1, 0.5, 0.7, 0.2], [0.1, 0.2, 0.1, 0.1, 0.05],
                                 ["x", "x", "y", "y", "y"])
        self.assertLess(r.groups[0].se, 1e-12)
        assert_close(self, r.Q_between, 0.9875166323, 1e-8)
        assert_close(self, r.p_between, 0.3203500896, 1e-8)

    def test_common_tau2_k_le_groups_falls_back(self):
        r = MO.subgroup_analysis([0.1, 0.4, 0.5, 0.7], [0.1, 0.2, 0.1, 0.1], ["a", "b", "c", "d"],
                                 common_tau2=True)
        self.assertIsNone(r.common_tau2)
        self.assertTrue(any("Közös τ² nem becsülhető" in w for w in r.warnings), r.warnings)
        self.assertEqual(len(r.groups), 4)
        self.assertIsNotNone(r.Q_between)


class TestMantelHaenszelPeto(unittest.TestCase):
    E1 = [0, 0, 3, 10, 5, 0, 12, 1]
    N1 = [20, 15, 30, 10, 50, 40, 12, 100]
    E2 = [4, 0, 0, 7, 5, 0, 12, 0]
    N2 = [22, 15, 25, 9, 50, 40, 12, 80]

    def test_rr_keeps_double_100_table(self):
        # rma.mh(measure="RR"): b 0.0656482, se 0.1503643, QE 4.604334
        r = M.mantel_haenszel(self.E1, self.N1, self.E2, self.N2, "RR")
        assert_close(self, r.estimate, 0.06564819203, 1e-8)
        assert_close(self, r.se, 0.1503643216, 1e-8)
        assert_close(self, r.Q, 4.604333511, 1e-8)
        self.assertEqual(r.k_estimable, 6)
        self.assertNotIn("#7", [lab for lab, _ in r.excluded])

    def test_or_unchanged(self):
        r = M.mantel_haenszel(self.E1, self.N1, self.E2, self.N2, "OR")
        assert_close(self, r.estimate, 0.20708368, 1e-7)
        assert_close(self, r.se, 0.46727714, 1e-7)
        assert_close(self, r.Q, 5.29326072, 1e-7)
        self.assertIn("#7", [lab for lab, _ in r.excluded])

    def test_rd_all_double_zero(self):
        # rma.mh(measure="RD"): b 0, se 0, zval NA, pval NA
        r = M.mantel_haenszel([0, 0, 0], [10, 20, 5], [0, 0, 0], [10, 20, 5], measure="RD")
        self.assertEqual(r.estimate, 0.0)
        self.assertEqual(r.se, 0.0)
        self.assertTrue(math.isnan(r.stat) and math.isnan(r.p))
        self.assertTrue(r.warnings)
        for rv in ("sato", "gr"):
            # rma.mh(measure="RD"): b -1, se 0, ill. b 0, se 0 (mindkét varianciaképlet 0-t ad)
            r = M.mantel_haenszel([0, 0], [50, 10], [50, 10], [50, 10], measure="RD", rd_var=rv)
            self.assertEqual((r.estimate, r.se), (-1.0, 0.0), rv)
            r = M.mantel_haenszel([10, 20], [10, 20], [10, 20], [10, 20], measure="RD", rd_var=rv)
            self.assertEqual((r.estimate, r.se), (0.0, 0.0), rv)

    def test_rd_zero_vs_full(self):
        r = M.mantel_haenszel([0], [2], [50], [50], measure="RD")
        self.assertEqual(r.estimate, -1.0)
        self.assertEqual(r.se, 0.0)
        self.assertTrue(math.isinf(r.stat) and r.stat < 0)
        self.assertEqual(r.Q, 0.0)

    def test_empty_arm_excluded(self):
        # Az üres karú tábla az MH-becsléshez semmit sem ad (metafor rma.mh b/se azonos); a Q-ból
        # is kimarad, így a referencia a 3 érvényes táblára futtatott rma.mh.
        e1, n1, e2, n2 = [4, 6, 3, 0], [123, 306, 231, 0], [11, 29, 11, 9], [139, 303, 220, 100]
        ref = {"OR": (-1.41351318, 0.31552437, 0.93976616), "RR": (-1.35092384, 0.30450558, 0.93701510),
               "RD": (-0.05693452, 0.01169045, 2.65307294)}
        for meas, (b, se, qe) in ref.items():
            r = M.mantel_haenszel(e1, n1, e2, n2, meas)
            assert_close(self, r.estimate, b, 1e-7, meas)
            assert_close(self, r.se, se, 1e-7, meas)
            assert_close(self, r.Q, qe, 1e-7, meas)
            self.assertIn("#4", [lab for lab, _ in r.excluded])
        # rma.peto: b -1.23734331, se 0.25655390, QE 0.72832141
        p = M.peto(e1, n1, e2, n2)
        assert_close(self, p.estimate, -1.23734331, 1e-7)
        assert_close(self, p.se, 0.25655390, 1e-7)
        assert_close(self, p.Q, 0.72832141, 1e-7)
        self.assertEqual(p.k_estimable, 3)
        self.assertIn("üres kar", dict(p.excluded)["#4"])


class TestZeroOverZero(unittest.TestCase):
    def test_hksj_zero_estimate_identical_effects(self):
        r = M.meta_analysis([0.0] * 4, [.1, .2, .3, .05], "random")
        self.assertEqual(r.estimate, 0.0)
        self.assertEqual((r.ci_lower, r.ci_upper), (0.0, 0.0))
        self.assertTrue(math.isnan(r.stat))
        self.assertTrue(math.isnan(r.p))
        self.assertTrue(any("0/0" in w for w in r.warnings), r.warnings)

    def test_egger_perfect_fit_is_nan(self):
        for c in (0.0, 0.5, -1.3):
            e = B.egger_test([c] * 5, [.1, .2, .3, .05, .4])
            self.assertTrue(math.isnan(e.t), c)
            self.assertTrue(math.isnan(e.p), c)
            self.assertTrue(any("nem értelmezhető" in w for w in e.warnings))
        # normál adatnál változatlan
        e = B.egger_test([0.1, 0.3, 0.2, 0.5, 0.4], [0.01, 0.04, 0.02, 0.09, 0.05])
        self.assertTrue(math.isfinite(e.t) and 0 <= e.p <= 1)

    def test_ratio_stat(self):
        self.assertTrue(math.isnan(M.ratio_stat(0.0, 0.0)))
        self.assertEqual(M.ratio_stat(-2.0, 0.0), -math.inf)
        self.assertEqual(M.ratio_stat(2.0, 0.0), math.inf)
        self.assertEqual(M.ratio_stat(1.0, 0.5), 2.0)


class TestTrimFillSide(unittest.TestCase):
    Y = [-0.0315, -0.2783, -0.2586, -0.2172, 0.1899]
    V = [0.1352, 0.006, 0.1022, 0.643, 0.0172]

    def test_pm_side_regression_uses_pm(self):
        # trimfill(rma(y, v, method="PM")): side "left", k0 0, b -0.1030098166
        t = B.trim_and_fill(self.Y, self.V, None, "random", "PM", "L0")
        self.assertEqual(t.side, "left")
        self.assertEqual(t.k0, 0)
        assert_close(self, t.adjusted.estimate, -0.1030098166, 1e-8)

    def test_metaregression_pm_he_sj(self):
        # rma(y, v, mods=sqrt(v), method=m): tau2, b0, b1
        ref = {"PM": (0.02851429088, -0.09989092823, 0.01808576258),
               "HE": (0.0, -0.2217753146, 0.5634522059),
               "SJ": (0.02950082161, -0.09866537706, 0.01255924085),
               "DL": (0.06722817229, -0.0755056041, -0.09250510469)}
        x = [[1.0, math.sqrt(v)] for v in self.V]
        for m, (t2, b0, b1) in ref.items():
            mr = MO.meta_regression(self.Y, self.V, x, ["intercept", "sei"], m)
            assert_close(self, mr.tau2, t2, 1e-7, m)
            assert_close(self, mr.coefficients[0]["estimate"], b0, 1e-7, m)
            assert_close(self, mr.coefficients[1]["estimate"], b1, 1e-7, m)


class TestBeggRankTest(unittest.TestCase):
    def test_ties_in_both_variables_full_variance(self):
        # ranktest(...)$pval = 0.01056978437 (cor.test kendall, kötés-korrigált variancia)
        y = [0.1, 0.1, 0.3, 0.3, 0.5, -0.2, 0.4, 0.1, 0.6, 0.6, 0.2, 0.25]
        v = [0.01, 0.01, 0.02, 0.02, 0.05, 0.05, 0.03, 0.01, 0.08, 0.08, 0.02, 0.04]
        r = B.begg_test(y, v)
        assert_close(self, r.p, 0.01056978437, 1e-9)
        self.assertEqual(r.method, "normális közelítés")

    def test_exact_for_k_above_50(self):
        # set.seed(11)-es szimuláció, k = 60, nincs kötés: ranktest p = 0.282424229644709 (pontos)
        y = [0.238503, 0.03226, 0.715899, 0.190728, 0.749853, 0.4932, 0.882064, 0.039999, 0.366462,
             -0.792492, -0.288356, 0.359417, 0.401486, 0.5148, -0.591368, 0.250004, 0.244703,
             -0.242715, 0.821715, 1.294677, -0.068508, 1.509857, -0.162051, 0.259807, -0.330581,
             0.642407, -0.263762, 0.622086, 0.019128, 0.108881, 0.271626, -0.243748, 0.096226,
             0.556385, 0.797649, -0.29111, 1.075123, -0.291747, 0.427191, 0.500688, -0.611588,
             -0.044191, -0.295596, 0.65288, 0.893712, 0.066854, -0.08789, -0.291099, 0.016015,
             0.510726, -0.954093, -0.195994, -0.134917, 0.022225, 0.114515, 0.119429, 1.156419,
             0.415952, -0.313888, 0.123518]
        v = [0.085596, 0.06413, 0.140092, 0.066847, 0.236705, 0.370684, 0.44906, 0.031992, 0.341322,
             0.299054, 0.120589, 0.195318, 0.181335, 0.445916, 0.143056, 0.13505, 0.044135, 0.192369,
             0.306568, 0.498032, 0.356135, 0.401613, 0.104785, 0.113389, 0.26841, 0.22449, 0.183944,
             0.083515, 0.411948, 0.04846, 0.247165, 0.410864, 0.076476, 0.14451, 0.17641, 0.180033,
             0.329376, 0.085212, 0.071981, 0.222985, 0.45815, 0.261074, 0.076327, 0.255166, 0.390319,
             0.456115, 0.332611, 0.034074, 0.487608, 0.474869, 0.348881, 0.345664, 0.493432, 0.034735,
             0.423241, 0.082951, 0.250688, 0.270688, 0.45833, 0.28516]
        r = B.begg_test(y, v)
        self.assertEqual(r.method, "pontos")
        assert_close(self, r.p, 0.282424229644709, 1e-10)

    def test_exact_distribution_matches_enumeration(self):
        for n in (3, 5, 6):
            counts = {}
            for perm in itertools.permutations(range(n)):
                s = sum((1 if perm[j] > perm[i] else -1) for i in range(n) for j in range(i + 1, n))
                counts[s] = counts.get(s, 0) + 1
            tot = float(sum(counts.values()))
            for s_abs in range(0, n * (n - 1) // 2 + 1):
                want = sum(c for s, c in counts.items() if abs(s) >= s_abs) / tot
                assert_close(self, B._kendall_exact_sf(s_abs, n), want, 1e-13)

    def test_large_k_no_overflow(self):
        rnd = random.Random(5)
        for k in (170, 200):
            v = [rnd.uniform(0.01, 0.5) for _ in range(k)]
            y = [rnd.gauss(0, math.sqrt(a)) for a in v]
            r = B.begg_test(y, v)
            self.assertEqual(r.method, "pontos" if k <= 170 else "normális közelítés")
            self.assertTrue(0 <= r.p <= 1)


class TestCumulativeOrdering(unittest.TestCase):
    def test_mixed_types_no_crash(self):
        keys = [2019.0, "2019-05", 2020.0, 2021.0]
        out = SE.cumulative([0.1, 0.3, 0.5, 0.7], [0.1, 0.2, 0.1, 0.1], list("ABCD"), keys)
        self.assertEqual([r["added"] for r in out], ["A", "B", "C", "D"])

    def test_missing_keys_last_like_metafor(self):
        out = SE.cumulative([0.1, 0.3, 0.5, 0.7], [0.1, 0.2, 0.1, 0.1], list("ABCD"),
                            [2019.0, None, 2005.0, 2021.0])
        self.assertEqual([r["added"] for r in out], ["C", "A", "D", "B"])
        self.assertEqual(SE.cumulative_order([10.0, 9.0, None, 2.0]), [3, 1, 0, 2])
        self.assertEqual(SE.cumulative_order(["2019a", 2019.0, "2018", ""]), [2, 1, 0, 3])


if __name__ == "__main__":
    unittest.main()
