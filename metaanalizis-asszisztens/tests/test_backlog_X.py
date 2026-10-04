# -*- coding: utf-8 -*-
"""X backlog (statisztikai mag): H/I² CI-konvenció, trim-and-fill vágási modell, IVhet τ²-opciók,
Egger/Begg konvenciók, nyers súlyok és alcsoport-részesedés, kiugró-szűrés, prospektív erő,
HC1 robusztus meta-regresszió.

Referenciák (R 4.3, metafor 4.4, sandwich; a meta:::calcH és a dmetar::power.analysis /
find.outliers forráskódja szerint számolva): lásd az egyes tesztek megjegyzéseit.
"""
import math
import unittest

import _helpers  # noqa: F401  (sys.path)
from _helpers import assert_close
from source_cases import load_cases, _es

from metaelemzes import bias as B
from metaelemzes import models as M
from metaelemzes import moderators as MO
from metaelemzes import power as P
from metaelemzes import sensitivity as SE

_CASES = None


def _case(case_id):
    global _CASES
    if _CASES is None:
        _CASES = {c["case_id"]: c for c in load_cases()}
    return _CASES[case_id]


def _jslhr22():
    return _es(_case("jslhr_tutorial__k22_re_reml"))


def _jslhr19():
    return _es(_case("jslhr_tutorial__k19_outliers_removed_re_reml"))


def _ihdchol():
    return _es(_case("khan_ch11_13__ihdchol_wls_metareg_categorical_robust_inference"))


# ------------------------------------------------------------- 1. H / I² CI
class TestHigginsThompsonCentre(unittest.TestCase):
    # R: meta:::calcH / meta:::isquared (meta R/meta-het.R forrása, ci() = TE ± qnorm(.975)·SE)
    CALCH = [
        (17.35299788323497, 19, 1.399251075126, 48.924961643203),
        (0.0, 5, 2.192833440519, 79.203573814989),
        (3.2, 6, 1.985152368718, 74.624634442048),
        (5.5, 6, 2.082045369277, 76.931485856407),
        (2.5, 3, 3.466541842061, 91.678394819928),
    ]

    def test_truncated_matches_meta_calcH(self):
        for q, k, h_hi, i2_hi in self.CALCH:
            i2_lo, i2_up, h_lo, h_up = M.i2_ci_higgins_thompson(q, k)
            self.assertEqual((i2_lo, h_lo), (0.0, 1.0))
            assert_close(self, h_up, h_hi, 1e-10, "H upper Q=%s k=%s" % (q, k))
            assert_close(self, i2_up, i2_hi, 1e-9, "I2 upper Q=%s k=%s" % (q, k))

    def test_q_above_k_unchanged(self):
        # R calcH(40, 9): H 2.108185 [1.556786638809, 2.854883471921], I² [58.738804331368, 87.730602766200]
        for centre in M.H_CENTRES:
            i2_lo, i2_hi, h_lo, h_hi = M.i2_ci_higgins_thompson(40.0, 10, h_centre=centre)
            assert_close(self, h_lo, 1.556786638809, 1e-10)
            assert_close(self, h_hi, 2.854883471921, 1e-10)
            assert_close(self, i2_lo, 58.738804331368, 1e-9)
            assert_close(self, i2_hi, 87.730602766200, 1e-9)

    def test_untruncated_reproduces_old_behaviour(self):
        q, k = 17.35299788323497, 19
        z = 1.959963984540054
        b = math.sqrt(1.0 / (2 * (k - 2)) * (1 - 1.0 / (3 * (k - 2) ** 2)))
        h_hi = math.exp(0.5 * math.log(q / (k - 1)) + z * b)
        i2_lo, i2_hi, h_lo, h_up = M.i2_ci_higgins_thompson(q, k, h_centre="untruncated")
        assert_close(self, h_up, h_hi, 1e-12)
        assert_close(self, h_up, 1.3738732, 1e-7)
        assert_close(self, i2_hi, 47.020642, 1e-6)
        # Q = 0: a régi viselkedés degenerált [0, 0] I²-intervallum
        self.assertEqual(M.i2_ci_higgins_thompson(0.0, 5, h_centre="untruncated"), (0.0, 0.0, 1.0, 1.0))

    def test_meta_analysis_jslhr_k19(self):
        es = _jslhr19()
        r = M.meta_analysis(es.yi, es.vi, "random", "REML", "z")
        h = r.heterogeneity
        self.assertEqual(h["H_ci_centre"], "truncated")
        assert_close(self, h["H_ci_HT"][1], 1.399251, 1e-6)
        assert_close(self, h["I2_ci_HT"][1], 48.924962, 1e-6)
        self.assertEqual(h["H"], 1.0)
        self.assertEqual(h["H2_M"], 0.0)
        assert_close(self, h["H2"], 17.352998 / 18, 1e-7)      # H2 = Q/df: nem csonkolt (metafor)
        r_old = M.meta_analysis(es.yi, es.vi, "random", "REML", "z", h_centre="untruncated")
        assert_close(self, r_old.heterogeneity["H_ci_HT"][1], 1.3738732, 1e-7)
        # a pontbecslés, a CI és a többi heterogenitás-mutató nem függ a középponttól
        for key in ("estimate", "se", "ci_lower", "ci_upper", "tau2", "Q", "I2"):
            self.assertEqual(getattr(r, key), getattr(r_old, key), key)

    def test_jslhr_k22_q_above_k_identical(self):
        es = _jslhr22()
        for centre in M.H_CENTRES:
            h = M.meta_analysis(es.yi, es.vi, "random", "REML", "z", h_centre=centre).heterogeneity
            assert_close(self, h["H_ci_HT"][0], 1.623765, 1e-6)
            assert_close(self, h["H_ci_HT"][1], 2.457859, 1e-6)
            assert_close(self, h["I2_ci_HT"][0], 62.072535, 1e-6)

    def test_modified_h2_admetan(self):
        # Khan 2020 p. 247 (Stata admetan): modified H² = (Q − df)/df = 0.840; H = √(Q/df)
        es = _es(_case("khan_ch11_13__ihdchol_ivhet_pooled_or"))
        r = M.meta_analysis(es.yi, es.vi, "ivhet")
        h = r.heterogeneity
        assert_close(self, h["H2_M"], 0.8403837210836778, 1e-9)
        assert_close(self, h["H"], math.sqrt(1.8403837210836778), 1e-9)
        assert_close(self, h["H2"], 1.8403837210836778, 1e-9)

    def test_k1_and_invalid_centre(self):
        h = M.heterogeneity([0.3], [0.1])
        self.assertIsNone(h["H"])
        self.assertIsNone(h["H2_M"])
        with self.assertRaises(M.ModelError):
            M.i2_ci_higgins_thompson(5.0, 6, h_centre="ln")
        with self.assertRaises(M.ModelError):
            M.meta_analysis([0.1, 0.2, 0.3], [0.1, 0.1, 0.1], h_centre="x")

    def test_tau2_bhhr_centre_and_se_form(self):
        c = 100.0
        # Q > df: a középpont-konvenció közömbös
        self.assertEqual(M.tau2_ci_bhhr(12.0, 6, c), M.tau2_ci_bhhr(12.0, 6, c, h_centre="untruncated"))
        # Q < df: csonkolt középpontnál a felső határ df(exp(2zB) − 1)/C, Q-tól független
        z = 1.959963984540054
        df = 18
        b_book = math.sqrt(1.0 / (2 * (df - 1) * (1 - 1.0 / (3 * (df - 1) ** 2))))
        lo, hi = M.tau2_ci_bhhr(8.0, 19, c)
        self.assertEqual(lo, 0.0)
        assert_close(self, hi, df * (math.exp(2 * z * b_book) - 1) / c, 1e-12)
        self.assertEqual(M.tau2_ci_bhhr(3.0, 19, c), (lo, hi))
        lo_u, hi_u = M.tau2_ci_bhhr(15.0, 19, c, h_centre="untruncated")
        assert_close(self, hi_u, df * ((15.0 / df) * math.exp(2 * z * b_book) - 1) / c, 1e-12)
        self.assertLess(hi_u, hi)
        self.assertEqual(M.tau2_ci_bhhr(8.0, 19, c, h_centre="untruncated"), (0.0, 0.0))
        # Q = 0: csonkolva nem degenerált
        self.assertGreater(M.tau2_ci_bhhr(0.0, 19, c)[1], 0.0)
        self.assertEqual(M.tau2_ci_bhhr(0.0, 19, c, h_centre="untruncated"), (0.0, 0.0))
        # se_form='higgins_thompson' + csonkolt középpont: a τ²-határ pontosan a H-határ átszámítása
        _, _, _, h_hi = M.i2_ci_higgins_thompson(8.0, 19)
        _, hi_ht = M.tau2_ci_bhhr(8.0, 19, c, se_form="higgins_thompson")
        assert_close(self, hi_ht, df * (h_hi ** 2 - 1) / c, 1e-12)
        with self.assertRaises(M.ModelError):
            M.tau2_ci_bhhr(8.0, 19, c, se_form="x")

    def test_bhhr_se_form_k3_differs(self):
        # k = 3 (df − 1 = 1): könyvi B = √(1/(2·2/3)) = 0.866, H&T B = √(½·2/3) = 0.577
        c, z = 10.0, 1.959963984540054
        _, hi_b = M.tau2_ci_bhhr(1.0, 3, c, h_centre="truncated", se_form="borenstein")
        _, hi_h = M.tau2_ci_bhhr(1.0, 3, c, h_centre="truncated", se_form="higgins_thompson")
        assert_close(self, hi_b, 2 * (math.exp(2 * z * math.sqrt(0.75)) - 1) / c, 1e-12)
        assert_close(self, hi_h, 2 * (math.exp(2 * z * math.sqrt(1.0 / 3.0)) - 1) / c, 1e-12)


# ------------------------------------------------------------- 2. trim-and-fill
class TestTrimFillTrimModel(unittest.TestCase):

    def test_meta_default_common_random(self):
        # R: trimfill(rma(yi, sei=sei, method="FE"), side="left") → k0 6; rma(tf$yi, tf$vi, "REML"):
        #    0.062123992123 [-0.493566191877; 0.617814176123], tau2 1.925042704343 (metafor threshold 1e-5)
        es = _jslhr22()
        t = B.trim_and_fill(es.yi, es.vi, es.labels, "random", "REML", "L0", side="left", ci_method="z",
                            trim_model="fixed")
        self.assertEqual(t.k0, 6)
        self.assertEqual(t.trim_model, "fixed")
        assert_close(self, t.trim_estimate, 0.0776988, 1e-6)
        a = t.adjusted
        self.assertEqual((a.model, a.k, a.tau2_method), ("random", 28, "REML"))
        assert_close(self, a.estimate, 0.062123992123, 1e-7)
        assert_close(self, a.ci_lower, -0.493566191877, 1e-6)
        assert_close(self, a.ci_upper, 0.617814176123, 1e-6)
        assert_close(self, a.tau2, 1.925042704343, 1e-5)
        # nyomtatott meta-kimenet (JSLHR 2022 p. 12): k0 = 6, 0.0634 [-0.4924; 0.6191], tau² 1.9256
        assert_close(self, a.estimate, 0.0634, 0.002)
        assert_close(self, a.tau2, 1.9256, 0.002)

    def test_auto_side_is_egger_sign(self):
        es = _jslhr22()
        t = B.trim_and_fill(es.yi, es.vi, es.labels, "random", "REML", trim_model="fixed")
        eg = B.egger_test(es.yi, es.vi)
        self.assertEqual(t.side, "left" if eg.intercept > 0 else "right")
        self.assertIn("Egger", t.side_rule)
        self.assertEqual(t.k0, 6)

    def test_default_is_metafor(self):
        es = _jslhr22()
        t = B.trim_and_fill(es.yi, es.vi, es.labels, "random", "REML", "L0", side="left", ci_method="z")
        self.assertEqual(t.k0, 0)
        self.assertEqual(t.trim_model, "random")
        self.assertIsNone(t.side_rule)
        # trim_model='random' ugyanaz, mint a None (random jelentett modellnél)
        t2 = B.trim_and_fill(es.yi, es.vi, es.labels, "random", "REML", "L0", side="left", ci_method="z",
                             trim_model="RE")
        self.assertEqual((t2.k0, t2.adjusted.estimate), (t.k0, t.adjusted.estimate))
        with self.assertRaises(M.ModelError):
            B.trim_and_fill(es.yi, es.vi, es.labels, trim_model="ivhet")

    def test_fixed_trim_fixed_report_equals_none(self):
        es = _jslhr22()
        a = B.trim_and_fill(es.yi, es.vi, es.labels, "fixed", side="left", trim_model="fixed")
        b = B.trim_and_fill(es.yi, es.vi, es.labels, "fixed", side="left")
        self.assertEqual((a.k0, a.adjusted.estimate), (b.k0, b.adjusted.estimate))
        assert_close(self, a.adjusted.estimate, 0.0776988, 1e-6)


# ------------------------------------------------------------- 3. IVhet
class TestIvhetOptions(unittest.TestCase):

    def setUp(self):
        self.es = _es(_case("khan_ch10__ivhet_fisher_z"))

    def test_default_dl_unchanged(self):
        r = M.meta_analysis(self.es.yi, self.es.vi, "ivhet")
        self.assertEqual(r.tau2_method, "DL")
        assert_close(self, r.tau2, 0.008632618724092642, 1e-12)
        assert_close(self, r.se, 0.04671514423459983, 1e-12)
        self.assertEqual(r.warnings, [])

    def test_tau2_fixed(self):
        # Khan 2020 p. 234: τ² kerekítve 0.008633 → SE 0.046716, LL* 0.451061 (1,96-tal)
        r = M.meta_analysis(self.es.yi, self.es.vi, "ivhet", tau2_fixed=0.008633)
        self.assertEqual(r.tau2_method, "rögzített")
        self.assertEqual(r.tau2, 0.008633)
        assert_close(self, r.se, 0.046716, 5e-7)
        r196 = M.meta_analysis(self.es.yi, self.es.vi, "ivhet", tau2_fixed=0.008633, level=0.950004209703559)
        assert_close(self, r196.ci_lower, 0.451061, 5e-7)
        with self.assertRaises(M.ModelError):
            M.meta_analysis(self.es.yi, self.es.vi, "ivhet", tau2_fixed=-1.0)

    def test_other_tau2_method_warns(self):
        r = M.meta_analysis(self.es.yi, self.es.vi, "ivhet", tau2_method="REML")
        t_reml = M.estimate_tau2(self.es.yi, self.es.vi, "REML")[0]
        self.assertEqual(r.tau2_method, "REML")
        assert_close(self, r.tau2, t_reml, 1e-14)
        w = [1 / v for v in self.es.vi]
        sw = sum(w)
        se = math.sqrt(sum((wi / sw) ** 2 * (v + t_reml) for wi, v in zip(w, self.es.vi)))
        assert_close(self, r.se, se, 1e-14)
        self.assertTrue(any("eltér a publikált IVhet" in x for x in r.warnings))
        r_dl = M.meta_analysis(self.es.yi, self.es.vi, "ivhet", tau2_method="DL")
        self.assertEqual(r_dl.warnings, [])

    def test_ci_method_ignored_with_warning(self):
        r = M.meta_analysis(self.es.yi, self.es.vi, "ivhet", ci_method="hksj")
        self.assertEqual(r.ci_method, "z")
        self.assertTrue(any("mindig z-alapú" in x for x in r.warnings))
        self.assertEqual(M.meta_analysis(self.es.yi, self.es.vi, "ivhet", ci_method="z").warnings, [])
        with self.assertRaises(M.ModelError):
            M.meta_analysis(self.es.yi, self.es.vi, "ivhet", ci_method="foo")

    def test_secondary_defaults_keep_dl(self):
        es = self.es
        g = ["a", "a", "a", "a", "b", "b", "b", "b"][:len(es.yi)]
        sg = MO.subgroup_analysis(es.yi, es.vi, g, es.labels, "ivhet")
        self.assertTrue(all(x.tau2_method == "DL" for x in sg.groups))
        self.assertEqual(sg.overall.tau2_method, "DL")
        loo = SE.leave_one_out(es.yi, es.vi, es.labels, "ivhet")
        full_wo0 = M.meta_analysis(es.yi[1:], es.vi[1:], "ivhet")
        assert_close(self, loo[0]["se"], full_wo0.se, 1e-14)


# ------------------------------------------------------------- 4. Egger / Begg
class TestEggerBeggConventions(unittest.TestCase):

    def test_egger_normal_ci_dmetar(self):
        # R: lm(yi/sei ~ I(1/sei)): 1.956718281607 ± qnorm(.975)·0.860156413584
        es = _jslhr22()
        r = B.egger_test(es.yi, es.vi, ci_dist="norm")
        assert_close(self, r.ci_lower, 0.270842689912, 1e-9)
        assert_close(self, r.ci_upper, 3.642593873302, 1e-9)
        self.assertEqual(r.df, 20)
        t = B.egger_test(es.yi, es.vi)
        self.assertEqual(t.ci_dist, "t")
        self.assertEqual((t.t, t.p, t.intercept), (r.t, r.p, r.intercept))
        assert_close(self, t.ci_lower, 0.162463, 1e-6)
        with self.assertRaises(ValueError):
            B.egger_test(es.yi, es.vi, ci_dist="z")

    def test_begg_methods_magnesium(self):
        # R: ranktest(yi, vi) p 0.5339737003888; ranktest(..., exact=FALSE) 0.506225852327387;
        # Stata metabias 'continuity corrected' 0.5288645203641904
        es = _es(_case("khan_ch11_13__magnesium_begg_normal_approx"))
        auto = B.begg_test(es.yi, es.vi)
        assert_close(self, auto.p, 0.5339737003888, 1e-12)
        self.assertEqual(auto.method, "pontos")
        assert_close(self, B.begg_test(es.yi, es.vi, method="exact").p, auto.p, 1e-15)
        nrm = B.begg_test(es.yi, es.vi, method="normal")
        assert_close(self, nrm.p, 0.506225852327387, 1e-12)
        assert_close(self, nrm.sd_S, 28.583211855912904, 1e-9)
        self.assertEqual(nrm.S, 19)
        cc = B.begg_test(es.yi, es.vi, method="normal", continuity=True)
        assert_close(self, cc.p, 0.5288645203641904, 1e-12)
        self.assertIn("folytonossági", cc.method)
        with self.assertRaises(ValueError):
            B.begg_test(es.yi, es.vi, method="kendall")

    def test_begg_exact_with_ties_falls_back(self):
        yi = [0.1, 0.3, 0.2, 0.5, 0.4]
        vi = [0.04, 0.04, 0.02, 0.05, 0.03]
        r = B.begg_test(yi, vi, method="exact")
        self.assertEqual(r.method, "normális közelítés")
        self.assertTrue(any("pontos Kendall-eloszlás nem használható" in w for w in r.warnings))
        self.assertEqual(r.p, B.begg_test(yi, vi).p)


# ------------------------------------------------- 5. nyers súlyok, alcsoport-részesedés
class TestRawWeightsAndShares(unittest.TestCase):

    def test_raw_weights(self):
        es = _jslhr22()
        f = M.meta_analysis(es.yi, es.vi, "fixed")
        assert_close(self, f.sum_weights, 1.0 / f.se_wald ** 2, 1e-12)
        self.assertEqual(f.weights_raw, [1.0 / v for v in es.vi])
        r = M.meta_analysis(es.yi, es.vi, "random", "REML", "z")
        for w, v, pct in zip(r.weights_raw, es.vi, r.weights_pct):
            assert_close(self, w, 1.0 / (v + r.tau2), 1e-14)
            assert_close(self, pct, 100.0 * w / r.sum_weights, 1e-12)
        iv = M.meta_analysis(es.yi, es.vi, "ivhet")
        self.assertEqual(iv.sum_weights, f.sum_weights)

    def test_khan_ihdchol_sum_of_weights(self):
        # Stata 'sum of wgt is 8.8607e+02'
        es = _ihdchol()
        assert_close(self, M.meta_analysis(es.yi, es.vi, "fixed").sum_weights, 886.07, 0.005)

    def test_mh_peto_sum_weights(self):
        e1, n1, e2, n2 = [4, 6, 3], [50, 60, 40], [8, 9, 5], [50, 58, 41]
        mh = M.mantel_haenszel(e1, n1, e2, n2, "OR")
        assert_close(self, mh.sum_weights, sum(mh.weights_raw_by_label.values()), 1e-14)
        pt = M.peto(e1, n1, e2, n2)
        assert_close(self, pt.sum_weights, sum(pt.weights_raw), 1e-14)
        assert_close(self, pt.se, 1.0 / math.sqrt(pt.sum_weights), 1e-14)

    def test_subgroup_weight_share_khan(self):
        # MetaXL / RevMan subtotal % weight: Khan Fig. 4.5 (67.8 / 32.2), Fig. 5.5 (64.4 / 35.6),
        # Fig. 6.4 (FE, 67.6 / 32.4)
        expect = [("khan_ch03_04__heartburn_subgroup_weight_share", "period",
                   {"old": 67.79171660285752, "recent": 32.20828339714247}),
                  ("khan_ch05__heartburn_subgroup_weight_share", "subgroup",
                   {"Old studies": 64.4292081189131, "Recent studies": 35.57079188108692}),
                  ("khan_ch06_07__schizophrenia_metaxl_subgroup_backtransformed_and_share", "income",
                   {"High income": 67.60655127489298, "Low/middle income": 32.393448725107014})]
        for cid, col, want in expect:
            c = _case(cid)
            es = _es(c)
            a = dict(c["call_args"])
            a.pop("group_col")
            r = MO.subgroup_analysis(es.yi, es.vi, [str(x.get(col)) for x in es.rows], es.labels, **a)
            got = {g.group: g.weight_share_pct for g in r.groups}
            self.assertAlmostEqual(sum(got.values()), 100.0, places=10)
            for g, v in want.items():
                assert_close(self, got[g], v, 1e-9, cid + " " + g)
            for g in r.groups:
                assert_close(self, g.weight_share_raw, g.weight_share_pct / 100.0 * r.overall.sum_weights, 1e-9)
            # a nyomtatott 1 tizedes érték
            for g, v in want.items():
                self.assertLess(abs(round(got[g], 1) - round(v, 1)), 1e-9)


# ------------------------------------------------------------- 6. kiugró-szűrés
class TestOutlierScreen(unittest.TestCase):

    def test_jslhr_find_outliers(self):
        # dmetar::find.outliers (REML, z-CI): Davis_2015(1), Davis_2015(2), Shehata_2013;
        # R rma(..., method="REML") a maradék 19-re: 0.174555148556 (se 0.070541704564),
        # CI [0.036295948202, 0.312814348909], z 2.474495755878, p 0.013342440606, tau2 0, QE 17.352997883235
        es = _jslhr22()
        r = SE.outlier_screen(es.yi, es.vi, es.labels, "random", "REML", 0.95, ci_method="z")
        self.assertEqual(r.flagged, ["Davis_2015(1)", "Davis_2015(2)", "Shehata_2013"])
        self.assertEqual((r.k, r.k_removed), (22, 3))
        f = r.refit
        self.assertEqual(f.k, 19)
        assert_close(self, f.estimate, 0.174555148556, 1e-9)
        assert_close(self, f.se, 0.070541704564, 1e-9)
        assert_close(self, f.ci_lower, 0.036295948202, 1e-9)
        assert_close(self, f.ci_upper, 0.312814348909, 1e-9)
        assert_close(self, f.stat, 2.474495755878, 1e-9)
        assert_close(self, f.p, 0.013342440606, 1e-9)
        self.assertEqual(f.tau2, 0.0)
        assert_close(self, f.Q, 17.352997883235, 1e-9)
        # a refit H/I² CI-je a meta-konvenció szerint (JSLHR p. 11: H 1.00 [1.00; 1.40], I² [0; 48.9])
        assert_close(self, f.heterogeneity["H_ci_HT"][1], 1.399251, 1e-6)
        # a jelölt vizsgálatok CI-je tényleg teljesen kívül esik
        for i in r.flagged_index:
            lo, hi = r.study_ci[i]
            self.assertTrue(hi < r.full.ci_lower or lo > r.full.ci_upper)

    def test_no_outliers(self):
        r = SE.outlier_screen([0.10, 0.20, 0.15, 0.12], [0.04, 0.05, 0.03, 0.04], None, "random", "REML",
                              ci_method="z")
        self.assertEqual(r.flagged, [])
        self.assertIsNone(r.refit)
        self.assertEqual(r.k_removed, 0)

    def test_single_pass_like_dmetar(self):
        # a dmetar egylépéses: a k = 19-es újraillesztés CI-jén kívül eső vizsgálatot (Perrachione_2011(1))
        # az első menet nem jelöli; egy második futtatás már igen
        es = _jslhr19()
        r = SE.outlier_screen(es.yi, es.vi, es.labels, "random", "REML", ci_method="z")
        self.assertEqual(r.flagged, ["Perrachione_2011(1)"])


# ------------------------------------------------------------- 7. prospektív erő
class TestPower(unittest.TestCase):
    # R: a dmetar::power.analysis képlete (forrás: dmetar R/power.analysis.R, a rajz nélkül)
    def test_dmetar_jslhr_example(self):
        # JSLHR 2022 p. 5: power.analysis(d = 0.7, k = 18, n1 = 15, n2 = 15, heterogeneity = "moderate")
        # → 'higher than 90%'; dmetar: 0.999983334487471
        r = P.power_analysis(18, 0.7, 15, 15, heterogeneity="moderate")
        assert_close(self, r.power, 0.999983334487471, 1e-13)
        self.assertGreater(r.power, 0.9)
        assert_close(self, r.v_study, 0.1415, 1e-12)
        assert_close(self, r.v_pooled, 0.013128055555555553, 1e-14)
        assert_close(self, r.lambda_, 6.10939, 1e-5)
        self.assertEqual(r.model, "random")

    def test_dmetar_grid(self):
        want = {"fixed": (0.999999998531736, 0.606639456465367), "low": (0.999999485294557, 0.489727278112917),
                "moderate": (0.999983334487471, 0.407624925733601), "high": (0.999854225058047, 0.351149883612581)}
        for het, (p1, p2) in want.items():
            assert_close(self, P.power_analysis(18, 0.7, 15, 15, heterogeneity=het).power, p1, 1e-13)
            assert_close(self, P.power_analysis(10, 0.2, 25, 25, heterogeneity=het).power, p2, 1e-13)
        self.assertEqual(P.power_analysis(10, 0.2, 25, 25).model, "fixed")

    def test_or_input(self):
        # dmetar: d = ln(OR)·√3/π; power.analysis(OR = 1.3, k = 12, n1 = n2 = 50, "moderate") = 0.490570049665157
        r = P.power_analysis(12, OR=1.3, n1=50, n2=50, heterogeneity="moderate")
        assert_close(self, r.power, 0.490570049665157, 1e-13)
        assert_close(self, r.effect, math.log(1.3) * math.sqrt(3) / math.pi, 1e-15)
        # az előjel közömbös (kétoldali teszt)
        assert_close(self, P.power_analysis(12, OR=1 / 1.3, n1=50, n2=50, heterogeneity="moderate").power,
                     P.power_analysis(12, -r.effect, 50, 50, heterogeneity="moderate").power, 1e-15)

    def test_tau2_i2_and_hedges_pigott(self):
        v = P.smd_variance(0.3, 30, 30)
        a = P.power_analysis(15, 0.3, 30, 30, tau2=v)
        b = P.power_analysis(15, 0.3, 30, 30, i2=50)
        c = P.power_analysis(15, 0.3, 30, 30, heterogeneity="high")
        assert_close(self, a.power, b.power, 1e-14)
        assert_close(self, a.power, c.power, 1e-14)
        hp = P.power_analysis(15, 0.3, 30, 30, heterogeneity="moderate", factors="hedges_pigott")
        assert_close(self, hp.tau2, 2.0 * v / 3.0, 1e-14)
        assert_close(self, hp.power, P.power_analysis(15, 0.3, 30, 30, i2=40).power, 1e-12)
        # nyers MD közös SD-vel, illetve közvetlen v
        md = P.power_analysis(10, 2.0, 40, 40, sd=8.0, measure="MD")
        assert_close(self, md.v_study, 64.0 * 80 / 1600, 1e-14)
        gen = P.power_analysis(10, 0.2, v=0.05, measure="GEN")
        lam = 0.2 / math.sqrt(0.005)
        z = 1.959963984540054
        assert_close(self, gen.power, 1 - M.dist.norm_cdf(z - lam) + M.dist.norm_cdf(-z - lam), 1e-12)

    def test_one_sided(self):
        r = P.power_analysis(10, 0.2, v=0.05, measure="GEN", tails=1)
        lam = 0.2 / math.sqrt(0.005)
        assert_close(self, r.power, 1 - M.dist.norm_cdf(1.6448536269514722 - lam), 1e-12)

    def test_studies_needed(self):
        s = P.studies_needed(0.8, effect=0.2, n1=25, n2=25, heterogeneity="moderate")
        self.assertEqual(s.k, 27)
        self.assertGreaterEqual(s.power, 0.8)
        self.assertLess(P.power_analysis(26, 0.2, 25, 25, heterogeneity="moderate").power, 0.8)
        none = P.studies_needed(0.8, effect=0.0, v=0.1, measure="GEN", max_k=50)
        self.assertIsNone(none.k)

    def test_errors(self):
        for kw in ({"k": 0, "effect": 0.5, "n1": 10, "n2": 10}, {"k": 5, "n1": 10, "n2": 10},
                   {"k": 5, "effect": 0.5}, {"k": 5, "effect": 0.5, "OR": 2.0, "n1": 10, "n2": 10},
                   {"k": 5, "effect": 0.5, "n1": 10, "n2": 10, "heterogeneity": "huge"},
                   {"k": 5, "effect": 0.5, "n1": 10, "n2": 10, "i2": 100},
                   {"k": 5, "effect": 2.0, "n1": 10, "n2": 10, "measure": "MD"},
                   {"k": 5, "effect": 0.5, "n1": 10, "n2": 10, "tails": 3}):
            with self.assertRaises(M.ModelError, msg=str(kw)):
                P.power_analysis(**kw)


# ------------------------------------------------------- 8. HC1 robusztus meta-regresszió
class TestRobustMetaRegression(unittest.TestCase):
    # R: sandwich::vcovHC(lm(yi ~ ..., weights = 1/vi), type = "HC1"); Khan 2020 11. fejezet (Stata
    # regress [aw = 1/v], robust)

    def setUp(self):
        self.es = _ihdchol()
        self.rows = self.es.rows

    def _x(self, cols):
        return [[1.0] + [float(r[c]) for c in cols] for r in self.rows]

    def test_intercept_only(self):
        r = MO.meta_regression(self.es.yi, self.es.vi, self._x([]), ["_cons"], "FE", robust=True)
        rb = r.robust
        c0 = rb["coefficients"][0]
        assert_close(self, c0["estimate"], -0.192886733765, 1e-10)
        assert_close(self, c0["se"], 0.040019901593, 1e-10)
        assert_close(self, c0["se_expb"], 0.0329994, 5e-8)
        assert_close(self, c0["t"], -4.8198, 5e-5)
        assert_close(self, math.exp(c0["ci_lower"]), 0.7595715, 5e-8)
        assert_close(self, math.exp(c0["ci_upper"]), 0.8951422, 5e-8)
        assert_close(self, rb["root_mse"], 0.24116, 5e-6)
        self.assertLess(abs(rb["r2"]), 1e-12)
        self.assertIsNone(rb["F"])
        assert_close(self, rb["sum_w"], 886.07, 0.005)
        self.assertEqual(rb["df"], 27)

    def test_continuous(self):
        r = MO.meta_regression(self.es.yi, self.es.vi, self._x(["chol_reduc"]), ["_cons", "chol"], "FE",
                               robust=True)
        rb = r.robust
        assert_close(self, rb["coefficients"][0]["se"], 0.067793551509, 1e-10)
        assert_close(self, rb["coefficients"][1]["se"], 0.086431635397, 1e-10)
        assert_close(self, rb["F"], 30.233591239871, 1e-9)
        assert_close(self, rb["r2"], 0.237954891284, 1e-10)
        assert_close(self, rb["r2_adj"], 0.208645464026, 1e-10)
        assert_close(self, rb["root_mse"], 0.21453, 5e-6)
        assert_close(self, rb["coefficients"][1]["t"], -5.50, 0.005)
        assert_close(self, rb["coefficients"][1]["se_expb"], 0.0537374, 5e-8)
        assert_close(self, math.exp(rb["coefficients"][1]["ci_upper"]), 0.7426115, 5e-8)
        assert_close(self, math.exp(rb["coefficients"][0]["ci_lower"]), 0.9815813, 5e-8)
        assert_close(self, rb["coefficients"][0]["p"], 0.087, 0.0005)
        assert_close(self, rb["I2_model_pct"], 13.99, 0.015)
        # a fő blokk (modell-alapú z) változatlan
        r0 = MO.meta_regression(self.es.yi, self.es.vi, self._x(["chol_reduc"]), ["_cons", "chol"], "FE")
        self.assertEqual(r0.coefficients, r.coefficients)
        self.assertIsNone(r0.robust)

    def test_categorical_and_alias(self):
        r = MO.meta_regression(self.es.yi, self.es.vi, self._x(["chol_grp_1", "chol_grp_2"]),
                               ["_cons", "g1", "g2"], "FE", test="robust_hc1")
        self.assertEqual(r.test, "z")
        rb = r.robust
        for c, se in zip(rb["coefficients"], (0.110160605853, 0.117733761512, 0.132055573635)):
            assert_close(self, c["se"], se, 1e-10)
        assert_close(self, rb["F"], 8.315863198855, 1e-9)
        assert_close(self, rb["F_p"], 0.0017, 5e-5)
        self.assertEqual((rb["F_df1"], rb["F_df2"]), (2, 25))
        assert_close(self, rb["r2"], 0.186345480083, 1e-10)
        assert_close(self, rb["r2_adj"], 0.121253118489, 1e-10)
        assert_close(self, rb["root_mse"], 0.22606, 5e-6)
        self.assertEqual(rb["I2_model_pct"], 0.0)          # F < df_r → 0-nál csonkolva
        with self.assertRaises(M.ModelError):
            MO.meta_regression(self.es.yi, self.es.vi, self._x([]), ["_cons"], "FE", test="hc3")

    def test_reml_weights_match_metafor_robust(self):
        # R: robust(rma(yi, vi, mods=~chol, method="REML", control=list(threshold=1e-12)),
        #           cluster=1:28, adjust=TRUE): se 0.096436168697 0.106196708616, QM(F) 22.674500466826
        r = MO.meta_regression(self.es.yi, self.es.vi, self._x(["chol_reduc"]), ["_cons", "chol"], "REML",
                               robust=True)
        assert_close(self, r.tau2, 0.009699803178, 1e-9)
        rb = r.robust
        assert_close(self, rb["coefficients"][0]["se"], 0.096436168697, 1e-8)
        assert_close(self, rb["coefficients"][1]["se"], 0.106196708616, 1e-8)
        assert_close(self, rb["F"], 22.674500466826, 1e-7)
        assert_close(self, rb["F_p"], 6.30299176378e-05, 1e-10)


if __name__ == "__main__":
    unittest.main()
