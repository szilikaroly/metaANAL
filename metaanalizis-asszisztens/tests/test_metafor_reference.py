# -*- coding: utf-8 -*-
"""A motor összevetése a metafor (R) referencia-értékeivel.

A referenciát a tests/reference/generate_metafor_reference.R állítja elő; az adatok a
metadat csomag adatsorai (BCG, Normand 1999, Molloy 2014, Pritz 1997, Yusuf 1985).
"""
import unittest

from _helpers import load_reference, assert_close, as_list
from metaelemzes import effect_sizes as E
from metaelemzes import models as M
from metaelemzes import moderators as MO
from metaelemzes import bias as B
from metaelemzes import sensitivity as S

REF = load_reference()
TOL = 1e-6


def _dataset(name, measure, **kw):
    rows = REF[name]["rows"]
    return E.compute(rows, measure, **kw)


class TestEffectSizes(unittest.TestCase):
    def _cmp(self, es, ref, msg):
        want_y = [y for y in as_list(ref["yi"])]
        want_v = [v for v in as_list(ref["vi"])]
        pairs = [(y, v) for y, v in zip(want_y, want_v) if y is not None]
        self.assertEqual(len(es.yi), len(pairs), msg + ": vizsgálatszám")
        for i, (y, v) in enumerate(pairs):
            assert_close(self, es.yi[i], y, 1e-10, "%s yi[%d]" % (msg, i))
            assert_close(self, es.vi[i], v, 1e-10, "%s vi[%d]" % (msg, i))

    def test_rr_or_rd_bcg(self):
        self._cmp(_dataset("bcg", "RR"), {"yi": REF["bcg"]["yi"], "vi": REF["bcg"]["vi"]}, "BCG RR")
        self._cmp(_dataset("bcg", "OR"), {"yi": REF["bcg"]["OR_yi"], "vi": REF["bcg"]["OR_vi"]}, "BCG OR")
        self._cmp(_dataset("bcg", "RD"), {"yi": REF["bcg"]["RD_yi"], "vi": REF["bcg"]["RD_vi"]}, "BCG RD")

    def test_md_smd_rom_normand(self):
        self._cmp(_dataset("normand", "MD"), REF["normand"]["MD"], "Normand MD")
        for vt in ("LS", "LS2", "UB"):
            self._cmp(_dataset("normand", "SMD", smd_vtype=vt), REF["normand"]["SMD_" + vt], "Normand SMD " + vt)
        self._cmp(_dataset("normand", "ROM"), REF["normand"]["ROM"], "Normand ROM")

    def test_correlations_molloy(self):
        self._cmp(_dataset("molloy", "ZCOR"), REF["molloy"]["ZCOR"], "Molloy ZCOR")
        self._cmp(_dataset("molloy", "COR"), REF["molloy"]["COR"], "Molloy COR")

    def test_proportions_pritz(self):
        for ms in ("PR", "PLN", "PLO", "PAS", "PFT"):
            self._cmp(_dataset("pritz", ms), REF["pritz"][ms], "Pritz " + ms)

    def test_or_rare_events_yusuf(self):
        self._cmp(_dataset("yusuf", "OR"), REF["yusuf"]["OR"], "Yusuf OR (drop00)")


class TestModels(unittest.TestCase):
    CASES = [("bcg", "RR", None), ("normand", "MD", "MD"), ("normand", "SMD", "SMD"),
             ("molloy", "ZCOR", "ZCOR")]

    def _results(self, name, sub):
        return REF[name]["results"] if sub is None else REF[name][sub]["results"]

    def test_all_tau2_estimators(self):
        for name, measure, sub in self.CASES:
            es = _dataset(name, measure)
            res = self._results(name, sub)
            for meth in ("FE", "DL", "REML", "ML", "PM", "HE", "SJ"):
                ref = res[meth]
                if meth == "FE":
                    r = M.meta_analysis(es.yi, es.vi, "fixed")
                else:
                    r = M.meta_analysis(es.yi, es.vi, "random", meth, "z", pi_method="z")
                tag = "%s/%s/%s" % (name, measure, meth)
                tol = 1e-6 if meth not in ("REML", "ML", "PM") else 2e-6
                assert_close(self, r.estimate, ref["estimate"], tol, tag + " estimate")
                assert_close(self, r.se, ref["se"], tol, tag + " se")
                assert_close(self, r.ci_lower, ref["ci_lower"], tol, tag + " ci_lower")
                assert_close(self, r.ci_upper, ref["ci_upper"], tol, tag + " ci_upper")
                assert_close(self, r.p, ref["p"], 1e-6, tag + " p")
                assert_close(self, r.Q, ref["Q"], 1e-8, tag + " Q")
                assert_close(self, r.p_Q, ref["p_Q"], 1e-8, tag + " p_Q")
                assert_close(self, r.tau2, ref["tau2"], tol, tag + " tau2")
                if meth == "FE":
                    assert_close(self, r.I2, ref["I2"], 1e-6, tag + " I2")
                else:
                    # metafor RE: I² = τ²/(τ² + s̃²)
                    assert_close(self, r.heterogeneity["I2_from_tau2"], ref["I2"], 1e-5, tag + " I2")
                    assert_close(self, r.heterogeneity["H2_from_tau2"], ref["H2"], 1e-5, tag + " H2")
                    assert_close(self, r.pi_lower, ref["pi_lower"], tol, tag + " pi_lower")
                    assert_close(self, r.pi_upper, ref["pi_upper"], tol, tag + " pi_upper")

    def test_knha(self):
        for name, measure, sub in self.CASES:
            es = _dataset(name, measure)
            ref = self._results(name, sub)["REML_knha"]
            r = M.meta_analysis(es.yi, es.vi, "random", "REML", "hksj", pi_method="t_k-1")
            tag = "%s KNHA" % name
            assert_close(self, r.se, ref["se"], 2e-6, tag + " se")
            assert_close(self, r.ci_lower, ref["ci_lower"], 2e-6, tag + " ci_lower")
            assert_close(self, r.ci_upper, ref["ci_upper"], 2e-6, tag + " ci_upper")
            assert_close(self, r.stat, ref["stat"], 2e-6, tag + " t")
            assert_close(self, r.p, ref["p"], 1e-6, tag + " p")
            assert_close(self, r.pi_lower, ref["pi_lower"], 2e-6, tag + " pi_lower")
            refdl = self._results(name, sub)["DL_knha"]
            r = M.meta_analysis(es.yi, es.vi, "random", "DL", "hksj")
            assert_close(self, r.ci_lower, refdl["ci_lower"], 1e-6, tag + " DL ci_lower")

    def test_confint_qprofile(self):
        for name, measure, sub in self.CASES:
            es = _dataset(name, measure)
            ref = self._results(name, sub)["REML_confint"]
            r = M.meta_analysis(es.yi, es.vi, "random", "REML", "z")
            h = r.heterogeneity
            tag = "%s confint" % name
            assert_close(self, h["tau2_ci_QP"][0], ref["tau2_lo"], 1e-5, tag + " tau2_lo")
            assert_close(self, h["tau2_ci_QP"][1], ref["tau2_hi"], 1e-5, tag + " tau2_hi")
            assert_close(self, h["I2_ci_QP"][0], ref["I2_lo"], 1e-4, tag + " I2_lo")
            assert_close(self, h["I2_ci_QP"][1], ref["I2_hi"], 1e-4, tag + " I2_hi")
            assert_close(self, h["H2_ci_QP"][0], ref["H2_lo"], 1e-4, tag + " H2_lo")
            assert_close(self, h["H2_ci_QP"][1], ref["H2_hi"], 1e-4, tag + " H2_hi")

    def test_proportion_back_transform(self):
        es = _dataset("pritz", "PFT")
        r = M.meta_analysis(es.yi, es.vi, "random", "REML", "z")
        ref = REF["pritz"]["PFT"]
        assert_close(self, r.estimate, ref["REML"]["estimate"], 2e-6, "PFT REML")
        nh = E.harmonic_mean([row["n"] for row in REF["pritz"]["rows"]])
        assert_close(self, E.back_transform("PFT", r.estimate, nh), ref["back_estimate"], 1e-5, "PFT back")
        es = _dataset("pritz", "PLO")
        r = M.meta_analysis(es.yi, es.vi, "random", "REML", "z")
        assert_close(self, E.back_transform("PLO", r.estimate), REF["pritz"]["PLO"]["back_estimate"], 1e-6, "PLO back")


class TestMHPeto(unittest.TestCase):
    def test_bcg_mh(self):
        rows = REF["bcg"]["rows"]
        args = ([r["e1"] for r in rows], [r["n1"] for r in rows], [r["e2"] for r in rows], [r["n2"] for r in rows])
        for ms in ("OR", "RR", "RD"):
            r = M.mantel_haenszel(*args, measure=ms)
            ref = REF["bcg"]["MH_" + ms]
            assert_close(self, r.estimate, ref["estimate"], 1e-8, "MH %s est" % ms)
            assert_close(self, r.se, ref["se"], 1e-8, "MH %s se" % ms)
            assert_close(self, r.Q, ref["Q"], 1e-6, "MH %s Q" % ms)
        r = M.peto(*args)
        assert_close(self, r.estimate, REF["bcg"]["PETO"]["estimate"], 1e-8, "Peto est")
        assert_close(self, r.se, REF["bcg"]["PETO"]["se"], 1e-8, "Peto se")
        assert_close(self, r.Q, REF["bcg"]["PETO"]["Q"], 1e-6, "Peto Q")

    def test_yusuf_rare(self):
        rows = REF["yusuf"]["rows"]
        args = ([r["e1"] for r in rows], [r["n1"] for r in rows], [r["e2"] for r in rows], [r["n2"] for r in rows])
        r = M.peto(*args)
        ref = REF["yusuf"]["PETO"]
        self.assertEqual(r.k, ref["k"])
        assert_close(self, r.estimate, ref["estimate"], 1e-8, "Yusuf Peto")
        assert_close(self, r.Q, ref["Q"], 1e-6, "Yusuf Peto Q")
        for ms in ("OR", "RR", "RD"):
            r = M.mantel_haenszel(*args, measure=ms)
            ref = REF["bcg"]["YUSUF_MH_" + ms]
            self.assertEqual(r.k, ref["k"], "Yusuf MH %s k" % ms)
            assert_close(self, r.estimate, ref["estimate"], 1e-8, "Yusuf MH %s" % ms)
            assert_close(self, r.se, ref["se"], 1e-8, "Yusuf MH %s se" % ms)
            assert_close(self, r.Q, ref["Q"], 1e-6, "Yusuf MH %s Q" % ms)


class TestModeratorsBias(unittest.TestCase):
    def setUp(self):
        self.es = _dataset("bcg", "RR")

    def test_metaregression(self):
        x, names = MO.design_matrix(self.es.rows, ["ablat"])
        for meth in ("REML", "ML", "DL", "FE"):
            for test in ("z", "knha"):
                ref = REF["bcg"]["metareg"]["%s_%s" % (meth, test)]
                r = MO.meta_regression(self.es.yi, self.es.vi, x, names, meth, test)
                tag = "metareg %s %s" % (meth, test)
                for j in range(2):
                    assert_close(self, r.coefficients[j]["estimate"], ref["b"][j], 1e-6, tag + " b%d" % j)
                    assert_close(self, r.coefficients[j]["se"], ref["se"][j], 1e-6, tag + " se%d" % j)
                    assert_close(self, r.coefficients[j]["p"], ref["p"][j], 1e-6, tag + " p%d" % j)
                assert_close(self, r.tau2, ref["tau2"], 1e-6, tag + " tau2")
                assert_close(self, r.QM, ref["QM"], 1e-5, tag + " QM")
                assert_close(self, r.QM_p, ref["QMp"], 1e-6, tag + " QMp")
                assert_close(self, r.QE, ref["QE"], 1e-6, tag + " QE")
                assert_close(self, r.I2_res, ref["I2"], 1e-5, tag + " I2")
                if meth != "FE":
                    assert_close(self, r.R2, ref["R2"], 1e-4, tag + " R2")
        x, names = MO.design_matrix(self.es.rows, ["alloc"])
        r = MO.meta_regression(self.es.yi, self.es.vi, x, names, "REML")
        ref = REF["bcg"]["metareg"]["REML_alloc"]
        assert_close(self, r.tau2, ref["tau2"], 1e-6, "alloc tau2")
        assert_close(self, r.QM, ref["QM"], 1e-5, "alloc QM")

    def test_subgroups(self):
        groups = [r["alloc"] for r in self.es.rows]
        sg = MO.subgroup_analysis(self.es.yi, self.es.vi, groups, self.es.labels, "random", "REML", "z")
        for g in sg.groups:
            ref = REF["bcg"]["subgroups_REML"][g.group]
            self.assertEqual(g.k, ref["k"])
            assert_close(self, g.estimate, ref["estimate"], 2e-6, "subgroup " + g.group)
            assert_close(self, g.tau2, ref["tau2"], 2e-6, "subgroup tau2 " + g.group)

    def test_egger_begg(self):
        for name, measure, sub in TestModels.CASES:
            es = _dataset(name, measure)
            res = REF[name]["results"] if sub is None else REF[name][sub]["results"]
            eg = B.egger_test(es.yi, es.vi)
            assert_close(self, eg.intercept, res["egger_lm"]["intercept"], 1e-8, name + " Egger")
            assert_close(self, eg.p, res["egger_lm"]["p"], 1e-8, name + " Egger p")
            bg = B.begg_test(es.yi, es.vi)
            assert_close(self, bg.kendall_tau, res["ranktest"]["tau"], 1e-8, name + " Begg tau")
            assert_close(self, bg.p, res["ranktest"]["p"], 1e-6, name + " Begg p")

    def test_trimfill(self):
        for name, measure, sub in TestModels.CASES:
            es = _dataset(name, measure)
            res = REF[name]["results"] if sub is None else REF[name][sub]["results"]
            for est in ("L0", "R0"):
                ref = res["trimfill_REML_" + est]
                tf = B.trim_and_fill(es.yi, es.vi, es.labels, "random", "REML", est)
                tag = "%s trimfill %s" % (name, est)
                self.assertEqual(tf.k0, ref["k0"], tag + " k0")
                self.assertEqual(tf.side, ref["side"], tag + " side")
                assert_close(self, tf.se_k0, ref["se_k0"], 1e-8, tag + " se_k0")
                assert_close(self, tf.adjusted.estimate, ref["estimate"], 2e-6, tag + " est")
                ref = res["trimfill_FE_" + est]
                tf = B.trim_and_fill(es.yi, es.vi, es.labels, "fixed", "REML", est)
                self.assertEqual(tf.k0, ref["k0"], tag + " FE k0")
                assert_close(self, tf.adjusted.estimate, ref["estimate"], 1e-8, tag + " FE est")

    def test_leave1out_influence(self):
        for name, measure, sub in TestModels.CASES:
            es = _dataset(name, measure)
            res = REF[name]["results"] if sub is None else REF[name][sub]["results"]
            loo = S.leave_one_out(es.yi, es.vi, es.labels, "random", "REML", "z")
            for i, row in enumerate(loo):
                assert_close(self, row["estimate"], res["leave1out"]["estimate"][i], 2e-6, "%s loo est %d" % (name, i))
                assert_close(self, row["tau2"], res["leave1out"]["tau2"][i], 2e-6, "%s loo tau2 %d" % (name, i))
            inf = S.influence(es.yi, es.vi, es.labels, "random", "REML")
            ref = res["influence"]
            for i, row in enumerate(inf):
                for key, rkey in (("rstudent", "rstudent"), ("dffits", "dffits"), ("cook_d", "cook_d"),
                                  ("cov_ratio", "cov_ratio"), ("hat", "hat"), ("dfbetas", "dfbetas"),
                                  ("tau2_del", "tau2_del"), ("Q_del", "Q_del")):
                    assert_close(self, row[key], ref[rkey][i], 2e-5, "%s infl %s %d" % (name, key, i))
                self.assertEqual(row["influential"], ref["influential"][i], "%s influential %d" % (name, i))


if __name__ == "__main__":
    unittest.main()
