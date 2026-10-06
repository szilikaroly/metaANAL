# -*- coding: utf-8 -*-
"""Regressziós tesztek a fuzz-megerősítés (R2b CONFIRM) három maradék numerikus hibájához.

1. Mértékegység-függő kollinearitás-vizsgálat (linalg.WeightedQR): minden pivotot a legnagyobb
   oszlopnormához mért, így egy dollárban mért GDP mellett egy arányként mért moderátort
   kollineárisnak vett ('szinguláris mátrix'). Most minden oszlop a SAJÁT eredeti súlyozott
   normájához mér (skálázott QR), mint az R lm() dqrdc2-tesztje.
2. Nem eltolás-független tökéletes-illeszkedés-vizsgálat (moderators KH-őr, bias.egger_test): a
   rss <= 1e-20·Σwy² feltétel a ~10 jegyig egyező hatásokat (1000 + 1e-8-os különbségek) is
   kerekítési zajnak vette (t = NaN / se = 0). Most: eltolt hatásokkal számolt rss, a centrált
   variációhoz mért 1e-26 vagy a bemenetek ábrázolási pontosságához mért 1e-28 küszöb.
3. (RE)ML leállási szabály: a Fisher-scoring lineáris konvergenciája miatt a τ²-ben ~3e-7 relatív
   hiba maradt, ami 0 közeli származtatott értékekben (ML PI alsó határa, R²) látszott. Most
   szelő-lépéses finomítás (models.polish_tau2).

Referenciák: R 4.3.3 / metafor 4.4.0 (control = list(threshold = 1e-15·medián(v_i))), illetve
pontos racionális aritmetika ugyanazokon a double bemeneteken (fractions.Fraction).
"""
import math
import os
import sys
import unittest
from fractions import Fraction as F

from _helpers import assert_close
from metaelemzes import bias as B
from metaelemzes import linalg as la
from metaelemzes import models as M
from metaelemzes import moderators as MO

HERE = os.path.dirname(os.path.abspath(__file__))
FUZZ = os.path.join(HERE, "fuzz")
if FUZZ not in sys.path:
    sys.path.insert(0, FUZZ)

# --------------------------------------------------------------- 1. GDP (dollár) + arány moderátor
GP_Y = [0.10, 0.35, 0.20, 0.55, 0.30, 0.62, 0.15, 0.48]
GP_V = [0.04, 0.05, 0.03, 0.06, 0.04, 0.05, 0.03, 0.06]
GP_GDP = [31000.0, 45000.0, 52000.0, 38000.0, 61000.0, 29000.0, 47000.0, 55000.0]
GP_PROP = [0.0012, 0.0035, 0.0021, 0.0050, 0.0027, 0.0061, 0.0016, 0.0043]
# metafor rma(yi, vi, mods = ~ gdp + prop, method = "REML") — τ² = 0 (FE, DL, ML ugyanez)
GP_B = [-0.0834693686143193, 1.19193266012613e-06, 112.367715608745]
GP_SE_Z = [0.396188988814586, 7.20490329955853e-06, 47.8641908767251]
GP_SE_KH = [0.033716367720619, 6.13149723737281e-07, 4.0733253720103]     # test = "knha"
GP_QM_Z, GP_QM_KH, GP_QE = 5.71878263116462, 394.817898547791, 0.0362115208821499

# --------------------------------------------------------------- 3. leállási szabály
# fuzz 20261007-02954 (k = 23, ML, PI alsó határa ≈ 0)
ML_Y = [0.0014585795634036378, -0.002040429143705775, 0.28365015813597877, 0.027364579899773596,
        5.422724053800051, 0.03854579675890207, 0.10433736578636588, 0.4675014429060657,
        -0.41504926068700926, 0.04345475983762462, 0.00927086052836604, -0.04990118921619224,
        -0.06496797313364105, 0.4204421004600587, 0.12073427745461049, 0.0362284989278174,
        -0.09932247932710733, 0.45547495340727906, -0.028291194359806968, 0.03747870937925073,
        -0.15819525795529557, 0.6073302305558085, 0.7408610200071609]
ML_V = [0.0989226904467716, 0.002116543374771461, 0.20314457857049975, 0.00356706783606041,
        5.809149583772407, 0.00037759340893726823, 0.016341657661337047, 0.3045505992844138,
        1.5689195480339495, 0.00015071889258531918, 0.0008928896771998242, 0.03221608380782546,
        0.001873072179338495, 0.09007504512260804, 0.002586049160929056, 0.0006379421442650299,
        0.0025262572806806637, 0.11558766920434149, 0.005502526963648037, 0.04618273050184851,
        0.054423171228669555, 0.60074328463009, 1.6712463550399923]
# fuzz 777-00604 (k = 9, ML meta-regresszió, R² két közeli τ² arányából)
R2_Y = [4.1464093639393935, -0.4304338227337516, 1.2413653731288004, -2.052230084716958,
        -2.0554262126305707, 0.4730951323488119, -2.1787021151057933, -1.1122446284929972,
        -0.6623098520319055]
R2_V = [3.189055201597537, 1.2460744524319773, 5.70844477326102, 1.9833940480172596, 6.998580104293605,
        6.5039168540749435, 6.855141669221535, 1.1809849522994407, 5.632585352525055]
R2_X = [7.33039145522701, 4.530039718061936, 0.037554306429791184, 6.753338540854844, 7.311912317660627,
        2.865683317903016, 2.6085638224528584, 5.174317019752538, 8.020518384883005]


def _exact_kh(x, y, v):
    """Pontos FE-súlyú WLS + Knapp–Hartung (s² = rss/(k − p)): [(b_j, se_j)], F-statisztika (csak
    tengelymetszet + 1 moderátor)."""
    k, p = len(y), len(x[0])
    w = [1 / F(a) for a in v]
    X = [[F(c) for c in row] for row in x]
    A = [[sum(w[i] * X[i][a] * X[i][b] for i in range(k)) for b in range(p)] for a in range(p)]
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    Mi = [[A[1][1] / det, -A[0][1] / det], [-A[1][0] / det, A[0][0] / det]]
    rhs = [sum(w[i] * X[i][a] * F(y[i]) for i in range(k)) for a in range(p)]
    b = [sum(Mi[a][c] * rhs[c] for c in range(p)) for a in range(p)]
    rss = sum(w[i] * (F(y[i]) - sum(X[i][a] * b[a] for a in range(p))) ** 2 for i in range(k))
    s2 = rss / (k - p)
    return [(float(b[a]), math.sqrt(float(s2 * Mi[a][a]))) for a in range(p)], float(b[1] ** 2 / (s2 * Mi[1][1]))


def _exact_egger(y, v):
    """Pontos Egger-regresszió (y/se ~ 1/se, OLS) a motorral azonos double se_i = sqrt(v_i)-kkel:
    (tengelymetszet, se, t)."""
    k = len(y)
    se = [math.sqrt(a) for a in v]
    zs = [F(a) / F(s) for a, s in zip(y, se)]
    pr = [1 / F(s) for s in se]
    xm, ym = sum(pr) / k, sum(zs) / k
    sxx = sum((x - xm) ** 2 for x in pr)
    sl = sum((x - xm) * (z - ym) for x, z in zip(pr, zs)) / sxx
    a = ym - sl * xm
    rss = sum((z - a - sl * x) ** 2 for x, z in zip(pr, zs))
    sei = math.sqrt(float(rss / (k - 2) * (F(1, k) + xm * xm / sxx)))
    return float(a), sei, float(a) / sei


class TestR2cScaleInvariantRank(unittest.TestCase):
    X = [[1.0, a, b] for a, b in zip(GP_GDP, GP_PROP)]

    def test_gdp_and_proportion_moderators_match_metafor(self):
        # korábban: SingularMatrixError('szinguláris mátrix (kollineáris moderátorok?)')
        for m in ("REML", "ML", "DL", "FE"):
            for test, ses, qm in (("z", GP_SE_Z, GP_QM_Z), ("knha", GP_SE_KH, GP_QM_KH)):
                r = MO.meta_regression(GP_Y, GP_V, self.X, ["i", "gdp", "prop"], m, test)
                self.assertEqual(r.tau2, 0.0)
                for c, b, se in zip(r.coefficients, GP_B, ses):
                    assert_close(self, c["estimate"], b, 1e-10, "%s %s %s" % (m, test, c["name"]), rel=True)
                    assert_close(self, c["se"], se, 1e-10, "%s %s %s se" % (m, test, c["name"]), rel=True)
                assert_close(self, r.QM, qm, 1e-10, rel=True)
                assert_close(self, r.QE, GP_QE, 1e-10, rel=True)
        r = MO.meta_regression(GP_Y, GP_V, self.X, ["i", "gdp", "prop"], "REML")
        assert_close(self, r.coefficients[2]["estimate"], 112.3677156, 1e-9, rel=True)
        assert_close(self, r.coefficients[2]["se"], 47.86419088, 1e-9, rel=True)

    def test_result_does_not_depend_on_moderator_units(self):
        # GDP dollárban / ezer dollárban / milliárdban, arány / ezrelék / ppm: b és se a skálával
        # fordítottan arányos, a z-k, a p-k, a QM és a τ² változatlan (metafor: ugyanígy)
        base = MO.meta_regression(GP_Y, GP_V, self.X, ["i", "gdp", "prop"], "REML")
        for sg in (1e-9, 1e-3, 1.0, 1e3, 1e8):
            for sp in (1e-6, 1.0, 1e3, 1e6):
                x = [[1.0, a * sg, b * sp] for a, b in zip(GP_GDP, GP_PROP)]
                r = MO.meta_regression(GP_Y, GP_V, x, ["i", "gdp", "prop"], "REML")
                for c, c0, s in zip(r.coefficients, base.coefficients, (1.0, sg, sp)):
                    assert_close(self, c["estimate"] * s, c0["estimate"], 1e-10, rel=True)
                    assert_close(self, c["se"] * s, c0["se"], 1e-10, rel=True)
                    assert_close(self, c["stat"], c0["stat"], 1e-10, rel=True)
                assert_close(self, r.QM, base.QM, 1e-10, rel=True)
                self.assertEqual(r.tau2, base.tau2)

    def test_weighted_qr_column_scaling(self):
        x = [[1.0, a, b, a * b] for a, b in zip(GP_GDP, GP_PROP)]
        w = [1.0 / v for v in GP_V]
        f0 = la.WeightedQR(x, w)
        b0, e0, rss0 = f0.solve(GP_Y)
        cov0, ld0 = f0.cov(), f0.logdet()
        h0, m0 = f0.leverages()
        s = (1.0, 1e-7, 1e9, 3e-4)
        f1 = la.WeightedQR([[c * t for c, t in zip(row, s)] for row in x], w)
        b1, e1, rss1 = f1.solve(GP_Y)
        for j in range(4):
            assert_close(self, b1[j] * s[j], b0[j], 1e-9, rel=True)
            assert_close(self, f1.cov()[j][j] * s[j] ** 2, cov0[j][j], 1e-9, rel=True)
        assert_close(self, f1.logdet(), ld0 + 2.0 * sum(math.log(t) for t in s), 1e-12, rel=True)
        assert_close(self, rss1, rss0, 1e-9, rel=True)
        for a, b in zip(f1.leverages()[0], h0):
            assert_close(self, a, b, 1e-12)

    def test_collinearity_is_judged_relative_to_each_column(self):
        g = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        w = [1.0, 2.0, 1.5, 0.5, 1.0, 3.0]
        rnd = [0.3, -0.7, 0.1, 0.9, -0.4, 0.2]
        for sc in (1e-8, 1.0, 1e8):
            # pontos lineáris kombináció (bármely egységben): szinguláris
            with self.assertRaises(la.SingularMatrixError):
                la.WeightedQR([[1.0, a, sc * (2.0 * a + 1.0)] for a in g], w)
            # a saját normájához mért 1e-9-es relatív maradék: kollineáris (rtol = 1e-7 alatt)
            with self.assertRaises(la.SingularMatrixError):
                la.WeightedQR([[1.0, a, sc * (a + 1e-9 * r)] for a, r in zip(g, rnd)], w)
            # 1e-5-ös relatív maradék: még illeszthető, egységtől függetlenül
            la.WeightedQR([[1.0, a, sc * (a + 1e-4 * r)] for a, r in zip(g, rnd)], w)
        with self.assertRaises(la.SingularMatrixError):        # csupa-nulla oszlop
            la.WeightedQR([[1.0, a, 0.0] for a in g], w)


class TestR2cShiftInvariantPerfectFit(unittest.TestCase):
    KX = [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0]]

    def test_knha_effects_agreeing_to_ten_digits_are_not_a_perfect_fit(self):
        # korábban (FE, DL, REML): b1 = 0, se = 0, t = NaN + 'tökéletesen illeszkedik' figyelmeztetés;
        # metafor is téved (se = 0, t = Inf, abszolút küszöb). Pontos t = 5,196152422706631 (= 3√3)
        y = [1000.0, 1000.0 + 1e-8, 1000.0 + 3e-8]
        want, f_exact = _exact_kh(self.KX, y, [1.0] * 3)
        assert_close(self, want[1][1] and want[1][0] / want[1][1], 5.1961524227066311, 1e-15, rel=True)
        for m in MO.MR_TAU2_METHODS:
            r = MO.meta_regression(y, [1.0] * 3, self.KX, ["i", "x"], m, "knha")
            c = r.coefficients[1]
            assert_close(self, c["estimate"], 1.5000011899246601e-08, 1e-9, m, rel=True)
            assert_close(self, c["se"], 2.8867536359592052e-09, 1e-9, m, rel=True)
            assert_close(self, c["stat"], 5.1961524227066311, 1e-9, m, rel=True)
            assert_close(self, r.QM, f_exact, 1e-9, m, rel=True)              # F = t² = 27
            self.assertFalse(any("tökéletesen" in w for w in r.warnings), m)
            self.assertLess(r.tau2, 1e-20)

    def test_knha_t_is_shift_invariant(self):
        base = [0.0, 1e-8, 3e-8]
        for c in (0.0, 0.5, -3.0, 1000.0, 2.5e4):
            y = [c + d for d in base]
            (_, (b1, se1)), _ = _exact_kh(self.KX, y, [1.0] * 3)
            r = MO.meta_regression(y, [1.0] * 3, self.KX, ["i", "x"], "FE", "knha")
            assert_close(self, r.coefficients[1]["stat"], b1 / se1, 1e-8, "c = %g" % c, rel=True)
            assert_close(self, r.coefficients[1]["stat"], 3.0 * math.sqrt(3.0), 1e-3, "c = %g" % c, rel=True)

    def test_egger_effects_agreeing_to_ten_digits(self):
        # korábban: t = NaN ('nulla reziduális variancia'); pontos t = -5,7091053339946773,
        # metafor regtest(model = "lm"): -5,70916387859122 (a z_i = y_i/se_i kiejtése miatt pontatlan)
        y = [1000.0 + d * 1e-8 for d in (0.0, 1.0, 3.0, -2.0)]
        v = [1.0, 0.5, 0.25, 2.0]
        r = B.egger_test(y, v)
        assert_close(self, r.t, -5.7091053339946773, 1e-9, rel=True)
        assert_close(self, r.intercept, -5.7202374749559577e-08, 1e-9, rel=True)
        assert_close(self, r.se_intercept, 1.0019498923754295e-08, 1e-9, rel=True)
        assert_close(self, r.slope, 1000.0000000563936, 1e-15, rel=True)
        assert_close(self, r.t, -5.70916387859122, 2e-5, rel=True)            # metafor (5 jegyig)
        self.assertFalse(any("nulla reziduális" in w for w in r.warnings))

    def test_egger_t_is_shift_invariant(self):
        v = [1.0, 0.5, 0.25, 2.0, 0.8]
        d = [0.0, 1.0, 3.0, -2.0, 0.5]
        for c in (0.0, 0.25, -7.0, 1000.0, 3e4):
            y = [c + a * 1e-8 for a in d]
            a, sei, t = _exact_egger(y, v)
            r = B.egger_test(y, v)
            assert_close(self, r.t, t, 1e-8, "c = %g" % c, rel=True)
            assert_close(self, r.intercept, a, 1e-8, "c = %g" % c, rel=True)

    def test_identical_effects_remain_a_perfect_fit(self):
        for y0 in (0.0, 0.3, -2.0, 1000.0, 1e6):
            r = B.egger_test([y0] * 5, [0.1, 0.2, 0.3, 0.4, 0.5])
            self.assertTrue(math.isnan(r.t))
            self.assertEqual(r.intercept, 0.0)
            self.assertEqual(r.slope, y0)
            r = MO.meta_regression([y0] * 4, [0.1, 0.2, 0.3, 0.4], [[1.0, a] for a in (0.0, 1.0, 1.0, 0.0)],
                                   ["i", "x"], "REML", "knha")
            self.assertEqual([c["se"] for c in r.coefficients], [0.0, 0.0])
            self.assertEqual(r.coefficients[0]["estimate"], y0)
            self.assertEqual(r.coefficients[1]["estimate"], 0.0)
            self.assertIsNone(r.QM)

    def test_rounded_linear_effects_are_a_perfect_fit(self):
        # y_i = 2,5 + b·x_i kerekítve: a reziduum csak az y_i-k saját kerekítése (néhány ulp),
        # ezért tökéletes illeszkedés (se = 0, t = ±Inf, QM = NA, mint a metaforban) — kis b-nél is,
        # ahol a centrált variáció is alig nagyobb a kerekítésnél (ábrázolási padló, 1e-28)
        x = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        v = [0.1, 0.2, 0.15, 0.3, 0.25, 0.12]
        for b in (0.3, 1e-3, 1e-7, 1e-12):
            r = MO.meta_regression([2.5 + b * a for a in x], v, [[1.0, a] for a in x], ["i", "x"], "FE", "knha")
            self.assertEqual([c["se"] for c in r.coefficients], [0.0, 0.0], b)
            assert_close(self, r.coefficients[1]["estimate"], b, 1e-4, rel=True)
            self.assertEqual(r.coefficients[1]["stat"], math.inf)
            self.assertIsNone(r.QM)
            self.assertTrue(any("tökéletesen" in w for w in r.warnings))

    def test_fuzz_genuine_perfect_fits_still_detected(self):
        # a fuzz valódi tökéletes illeszkedései (y = μ + b·x1 kerekítve, egy részük v_i-aránya >= 1e7)
        # közül a legnagyobb centrált rss-arányúak (7,9e-28, 7,8e-29, 6,3e-29) és a legrosszabb
        # kondíciójúak: mind tökéletes illeszkedés marad (se = 0)
        import gen
        for did in ("20261007-01745", "20261006-00016", "20261007-00135", "20261006-01238", "20261006-01251",
                    "20261006-02171", "20261007-01623"):
            seed, idx = did.rsplit("-", 1)
            ds = gen.generate(int(seed), int(idx))
            x = [[1.0] + [float(a) for a in row] for row in ds["mods"]]
            names = ["i"] + ["x%d" % j for j in range(len(x[0]) - 1)]
            for m in ("FE", "DL"):
                r = MO.meta_regression(ds["yi"], ds["vi"], x, names, m, "knha")
                self.assertTrue(all(c["se"] == 0.0 for c in r.coefficients), "%s %s" % (did, m))
                self.assertIsNone(r.QM, did)

    def test_thresholds(self):
        self.assertEqual(MO.PERFECT_FIT_RTOL, 1e-26)
        self.assertEqual(MO.PERFECT_FIT_FLOOR, 1e-28)
        self.assertTrue(MO.is_perfect_fit(0.0, 0.0, 0.0))
        self.assertTrue(MO.is_perfect_fit(1e-27, 1.0, 1.0))
        self.assertFalse(MO.is_perfect_fit(5.6e-24 * 3e6, 4.7e-16, 3e6))      # az 1000 + 1e-8-os példa
        self.assertTrue(MO.is_perfect_fit(1e-30, 1e-12, 1.0))                  # ábrázolási padló


class TestR2cStoppingRule(unittest.TestCase):
    def test_ml_prediction_interval_near_zero(self):
        # metafor (threshold = 1e-15·medián v_i): τ² = 9,497095212357415e-05, PI alsó = -4,9013919437960324e-05;
        # korábban a motor -4,90170606e-05 (6e-5 relatív eltérés; metafor 1e-12-es küszöbbel -4,9013951e-05)
        r = M.meta_analysis(ML_Y, ML_V, "random", "ML", "z", pi_method="t_k-2")
        assert_close(self, r.tau2, 9.497095212357415e-05, 1e-9, rel=True)
        assert_close(self, r.estimate, 0.028366019445161709, 1e-11, rel=True)
        assert_close(self, r.se, 0.0095772280510510194, 1e-11, rel=True)
        assert_close(self, r.pi_lower, -4.9013919437960324e-05, 1e-7, rel=True)
        assert_close(self, r.pi_upper, 0.056781052809761381, 1e-11, rel=True)
        t2, _ = M.estimate_tau2(ML_Y, ML_V, "REML")
        assert_close(self, t2, 0.00072349252880092097, 1e-10, rel=True)

    def test_ml_meta_regression_r2(self):
        # metafor (1e-15): τ² = 0,015892837462901026, τ²_0 = 0,016062497882816086, R² = 1,0562517807181473 %;
        # korábban a motor R² = 1,0562389 (1,2e-5 relatív eltérés: két közeli τ² aránya felnagyítja)
        x = [[1.0, a] for a in R2_X]
        r = MO.meta_regression(R2_Y, R2_V, x, ["i", "x"], "ML")
        assert_close(self, r.tau2, 0.015892837462901026, 1e-10, rel=True)
        assert_close(self, M.estimate_tau2(R2_Y, R2_V, "ML")[0], 0.016062497882816086, 1e-10, rel=True)
        assert_close(self, r.R2, 1.0562517807181473, 1e-7, rel=True)
        r = MO.meta_regression(R2_Y, R2_V, x, ["i", "x"], "REML")
        assert_close(self, r.tau2, 1.2548236915814051, 1e-10, rel=True)

    def test_polished_tau2_solves_the_score_equation(self):
        for y, v in ((ML_Y, ML_V), (R2_Y, R2_V)):
            scale = M.variance_scale(v)
            for kind in ("REML", "ML"):
                t2, info = M.estimate_tau2(y, v, kind)
                self.assertTrue(info["converged"])
                if t2 > 0:
                    self.assertLessEqual(abs(M._fs_adj(y, v, kind, t2)), 1e-13 * max(t2, scale), kind)
        x = [[1.0, a] for a in R2_X]
        for kind in ("REML", "ML"):
            t2, info = MO._tau2_mr(x, R2_Y, R2_V, kind)
            self.assertLessEqual(abs(MO._fs_mr_adj(x, R2_Y, R2_V, kind, t2)), 1e-13 * M.variance_scale(R2_V))

    def test_polish_keeps_the_fisher_scoring_value_when_unsafe(self):
        # növekvő lépésfüggvény (nem maximum), negatív szelő-gyök és túl nagy ugrás: nincs változás
        self.assertEqual(M.polish_tau2(lambda t: 1e-12 + (t - 1.0), 0.9, 1e-12 - 0.1, 1.0, 1.0), 1.0)
        g = lambda t: -0.5 * (t + 0.01)          # a gyök (-0,01) a megengedett tartományon kívül
        self.assertEqual(M.polish_tau2(g, 0.002, g(0.002), 0.001, 1.0), 0.001)
        self.assertEqual(M.polish_tau2(lambda t: 1e-9 * (2.0 - t), 0.99, 1e-9 * 1.01, 1.0, 1.0), 1.0)
        self.assertEqual(M.polish_tau2(lambda t: None, 0.9, 0.1, 1.0, 1.0), 1.0)
        # szabályos eset: lineáris konvergencia ρ = 0,9 -> a gyök egy lépésben
        root = 0.5
        f = lambda t: 0.1 * (root - t)
        assert_close(self, M.polish_tau2(f, root - 1e-9 / 0.9, f(root - 1e-9 / 0.9), root - 1e-10, 1.0), root, 1e-15)


class TestR2cExactAdjudicator(unittest.TestCase):
    """A fuzz döntőbírájának (tests/fuzz/exact.py) új zárt képletei a motorral egyeznek ott, ahol a
    motor pontos: így a fuzz 'exact=engine' címkéje a metafor pontatlanságát jelzi, nem fordítva."""

    def test_fit_stats_match_the_engine(self):
        import exact
        x = [[1.0, a, b] for a, b in zip(GP_GDP, GP_PROP)]
        for test in ("z", "knha"):
            want = exact.fit_stats(x, GP_Y, GP_V, 0.0, test)
            r = MO.meta_regression(GP_Y, GP_V, x, ["i", "gdp", "prop"], "FE", test)
            for j, c in enumerate(r.coefficients):
                assert_close(self, c["estimate"], want["b"][j], 1e-12, rel=True)
                assert_close(self, c["se"], want["se"][j], 1e-12, rel=True)
                assert_close(self, c["stat"], want["stat"][j], 1e-12, rel=True)
            assert_close(self, r.QM, want["QM"], 1e-12, rel=True)

    def test_closed_form_tau2_and_pm_equation(self):
        import exact
        from test_fixes_R2b_numerics import C_Y, C_V, C_M, _x
        x = _x(C_M)
        for m, fn in (("HE", exact.tau2_he), ("SJ", exact.tau2_sj), ("DL", exact.tau2_dl)):
            t_eng, _ = MO._tau2_mr(x, C_Y, C_V, m)
            assert_close(self, t_eng, fn(x, C_Y, C_V), 1e-9, m, rel=True)
        t_pm, _ = MO._tau2_mr(x, C_Y, C_V, "PM")
        self.assertGreater(t_pm, 0.0)
        assert_close(self, exact.pm_equation(x, C_Y, C_V, t_pm), 1.0, 1e-9)
        self.assertGreater(exact.pm_equation(x, C_Y, C_V, 0.0), 1.0)      # τ² = 0 itt nem PM-megoldás
        # tökéletes illeszkedés pontos aritmetikával: azonos y igen, az 1000 + 1e-8-os példa nem
        kx = [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0]]
        self.assertTrue(exact.is_perfect_fit(kx, [3.0] * 3, [1.0] * 3))
        self.assertFalse(exact.is_perfect_fit(kx, [1000.0, 1000.0 + 1e-8, 1000.0 + 3e-8], [1.0] * 3))


if __name__ == "__main__":
    unittest.main()
