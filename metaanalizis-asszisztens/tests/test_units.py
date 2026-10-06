# -*- coding: utf-8 -*-
"""Egységtesztek: konverziók, eloszlások, validálás, CSV-beolvasás, ábrák, modell-élhelyzetek."""
import math
import os
import random
import statistics
import tempfile
import unittest
from xml.etree import ElementTree

from _helpers import assert_close
from metaelemzes import conversions as C
from metaelemzes import distributions as D
from metaelemzes import effect_sizes as E
from metaelemzes import models as M
from metaelemzes import tableio, validate, plots


class TestDistributions(unittest.TestCase):
    # ismert táblázati értékek
    def test_known_quantiles(self):
        assert_close(self, D.norm_ppf(0.975), 1.959963984540054, 1e-12)
        assert_close(self, D.t_ppf(0.975, 10), 2.228138851986274, 1e-10)
        assert_close(self, D.t_ppf(0.975, 1), 12.70620473617471, 1e-9)
        assert_close(self, D.chi2_ppf(0.95, 1), 3.841458820694124, 1e-10)
        assert_close(self, D.chi2_ppf(0.025, 12), 4.403788506981792, 1e-9)
        assert_close(self, D.chi2_sf(3.841458820694124, 1), 0.05, 1e-10)
        assert_close(self, D.f_sf(4.964602743730711, 1, 10), 0.05, 1e-9)

    def test_tail_probabilities_relative(self):
        # R 4.x (pchisq / pnorm / pt / pf, lower.tail = FALSE), 17 értékes jegy; relatív tolerancia,
        # hogy a 0-ra alulcsorduló farok (pl. 1 − cdf alakú számítás) hibának számítson
        for got, want in ((D.chi2_sf(200, 12), 3.2614563667204693e-36),
                          (D.chi2_sf(152.2330080823733, 12), 1.9967645908459738e-26),   # BCG RR Q, df = 12
                          (D.chi2_sf(238.9158108620096, 8), 3.8418092854003323e-47),    # Normand MD Q, df = 8
                          (D.chi2_sf(1000, 3), 1.7994208765314476e-216),
                          (D.z_two_sided_p(10.5), 8.638012635618462e-26),
                          (D.z_two_sided_p(-37.5), 9.2107060191639104e-308),
                          (D.norm_sf(30), 4.9067139271481872e-198),
                          (D.t_two_sided_p(-12, 5), 7.0894925171615278e-05),
                          (D.t_sf(30, 8), 8.2676283758948577e-10),
                          (D.f_sf(80, 1, 11), 2.2274062839716295e-06)):
            assert_close(self, got, want, 1e-10, "farok %r" % want, rel=True)

    def test_symmetry_and_roundtrip(self):
        for df in (1, 2, 5, 30, 200):
            for p in (0.001, 0.05, 0.3):
                t = D.t_ppf(p, df)
                assert_close(self, D.t_cdf(t, df), p, 1e-10)
                assert_close(self, t, -D.t_ppf(1 - p, df), 1e-9)


class TestConversions(unittest.TestCase):
    def test_combine_groups_exact(self):
        rnd = random.Random(42)
        a = [rnd.gauss(10, 3) for _ in range(23)]
        b = [rnd.gauss(14, 5) for _ in range(31)]
        n, m, sd = C.combine_groups(len(a), statistics.mean(a), statistics.stdev(a),
                                    len(b), statistics.mean(b), statistics.stdev(b))
        self.assertEqual(n, 54)
        assert_close(self, m, statistics.mean(a + b), 1e-12)
        assert_close(self, sd, statistics.stdev(a + b), 1e-12)

    def test_sd_from_ci_t(self):
        n, mean, sd = 25, 50.0, 8.0
        half = D.t_ppf(0.975, n - 1) * sd / math.sqrt(n)
        assert_close(self, C.sd_from_ci(mean - half, mean + half, n), sd, 1e-12)
        assert_close(self, C.sd_from_se(sd / math.sqrt(n), n), sd, 1e-12)

    def test_wan_luo_formulas(self):
        n = 100
        eta = 2 * D.norm_ppf((0.75 * n - 0.125) / (n + 0.25))
        assert_close(self, C.sd_from_median(n, q1=10, q3=20), 10 / eta, 1e-12)
        xi = 2 * D.norm_ppf((n - 0.375) / (n + 0.25))
        assert_close(self, C.sd_from_median(n, minimum=0, maximum=50), 50 / xi, 1e-12)
        w = 0.7 + 0.39 / n
        assert_close(self, C.mean_from_median(n, 15, q1=10, q3=22), w * 16 + (1 - w) * 15, 1e-12)
        w = 4 / (4 + n ** 0.75)
        assert_close(self, C.mean_from_median(n, 15, minimum=0, maximum=50), w * 25 + (1 - w) * 15, 1e-12)
        # nagy n-nél az IQR/1.349 közelítés
        self.assertAlmostEqual(C.sd_from_median(100000, q1=0, q3=1.349), 1.0, places=3)
        # szimmetrikus eloszlásnál az átlag = medián
        assert_close(self, C.mean_from_median(50, 20, 15, 25, 5, 35), 20, 1e-12)

    def test_effect_conversions_roundtrip(self):
        d, vd = C.logor_to_d(0.8, 0.04)
        lo, vlo = C.d_to_logor(d, vd)
        assert_close(self, lo, 0.8, 1e-12)
        assert_close(self, vlo, 0.04, 1e-12)
        r, vr = C.d_to_r(0.5, 0.02, 50, 50)
        d2, vd2 = C.r_to_d(r, vr)
        assert_close(self, d2, 0.5, 1e-12)
        assert_close(self, vd2, 0.02, 1e-12)

    def test_change_sd(self):
        assert_close(self, C.sd_change(10, 10, 0.5), 10.0, 1e-12)
        assert_close(self, C.corr_from_change(10, 12, C.sd_change(10, 12, 0.7)), 0.7, 1e-12)
        self.assertEqual(sum(C.split_shared_control(101, 3)), 101)


class TestEffectSizeEdgeCases(unittest.TestCase):
    def test_zero_cells(self):
        rows = [{"study": "a", "e1": 0, "n1": 10, "e2": 3, "n2": 10},
                {"study": "b", "e1": 0, "n1": 10, "e2": 0, "n2": 12}]
        es = E.compute(rows, "OR")
        self.assertEqual(len(es), 1)
        self.assertIn("kettős nulla", es.excluded[0][1])
        assert_close(self, es.yi[0], math.log((0.5 * 7.5) / (10.5 * 3.5)), 1e-12)
        es = E.compute(rows, "RD")
        self.assertEqual(len(es), 2)
        assert_close(self, es.yi[0], -0.3, 1e-12)

    def test_bad_rows_are_reported_not_dropped_silently(self):
        rows = [{"study": "x", "m1": 1, "sd1": 0, "n1": 10, "m2": 1, "sd2": 1, "n2": 10},
                {"study": "y", "m1": None, "sd1": 1, "n1": 10, "m2": 1, "sd2": 1, "n2": 10},
                {"study": "z", "m1": 2, "sd1": 1, "n1": 10, "m2": 1, "sd2": 1, "n2": 10}]
        es = E.compute(rows, "SMD")
        self.assertEqual(es.labels, ["z"])
        self.assertEqual(len(es.excluded), 2)

    def test_hedges_j(self):
        assert_close(self, E.hedges_j(10, "approx"), 1 - 3 / 39.0, 1e-15)
        self.assertLess(abs(E.hedges_j(10) - E.hedges_j(10, "approx")), 1e-3)

    def test_pft_inverse_roundtrip(self):
        for n in (10, 57, 300):
            for p in (0.0, 0.03, 0.5, 0.9, 1.0):
                assert_close(self, E.pft_inverse(E.pft_of_p(p, n), n), p, 1e-9, "n=%d p=%g" % (n, p))


class TestModelEdgeCases(unittest.TestCase):
    def test_homogeneous_tau2_zero(self):
        yi = [0.5, 0.5, 0.5, 0.5]
        vi = [0.1, 0.2, 0.05, 0.3]
        for meth in M.TAU2_METHODS:
            r = M.meta_analysis(yi, vi, "random", meth, "z")
            self.assertEqual(r.tau2, 0.0, meth)
            assert_close(self, r.estimate, 0.5, 1e-12)
        r = M.meta_analysis(yi, vi, "random", "REML", "hksj")
        self.assertTrue(any("HKSJ" in w for w in r.warnings))

    def test_single_study(self):
        r = M.meta_analysis([0.3], [0.04], "random")
        assert_close(self, r.estimate, 0.3, 1e-12)
        self.assertEqual(r.ci_method, "z")

    def test_random_equals_fixed_when_tau2_zero(self):
        yi, vi = [0.1, 0.2, 0.15], [0.5, 0.6, 0.55]
        f = M.meta_analysis(yi, vi, "fixed")
        r = M.meta_analysis(yi, vi, "random", "DL", "z")
        assert_close(self, f.estimate, r.estimate, 1e-12)
        assert_close(self, f.se, r.se, 1e-12)

    def test_invalid_input(self):
        with self.assertRaises(M.ModelError):
            M.meta_analysis([0.1, 0.2], [0.1, 0.0])
        with self.assertRaises(M.ModelError):
            M.meta_analysis([0.1, 0.2], [0.1, 0.2], tau2_method="XYZ")


class TestTableIO(unittest.TestCase):
    def _write(self, text, enc="utf-8"):
        fd, p = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(p, "w", encoding=enc) as fh:
            fh.write(text)
        self.addCleanup(os.remove, p)
        return p

    def test_hungarian_excel_csv(self):
        p = self._write("Vizsgálat;Átlag1;SD1;n1;Átlag2;SD2;n2;Alcsoport\n"
                        "Kovács 2020;12,5;3,1;40;10,2;2,9;42;felnőtt\n"
                        "Nagy 2021;NA;3,0;35;9,9;3,3;33;gyermek\n", enc="cp1250")
        rows, meta = tableio.read_table(p)
        self.assertEqual(meta["delimiter"], ";")
        self.assertEqual(meta["encoding"], "cp1250")
        self.assertEqual(rows[0]["m1"], 12.5)
        self.assertEqual(rows[0]["study"], "Kovács 2020")
        self.assertIsNone(rows[1]["m1"])
        self.assertEqual(rows[0]["subgroup"], "felnőtt")

    def test_parse_errors_reported(self):
        p = self._write("study,e1,n1,e2,n2\nA,3,abc,4,20\n")
        rows, meta = tableio.read_table(p)
        self.assertEqual(len(meta["parse_errors"]), 1)
        f = validate.validate(rows, "OR", meta)
        self.assertTrue(any(x["code"] == "V003" for x in f))
        self.assertTrue(any(x["code"] == "V002" for x in f))


class TestValidation(unittest.TestCase):
    def test_se_reported_as_sd(self):
        rows = [{"study": "s%d" % i, "m1": 50 + i, "sd1": 10, "n1": 100, "m2": 48, "sd2": 10, "n2": 100} for i in range(5)]
        rows.append({"study": "gyanús", "m1": 52, "sd1": 1.0, "n1": 100, "m2": 47, "sd2": 1.0, "n2": 100})
        f = validate.validate(rows, "MD")
        self.assertTrue(any(x["code"] == "V011" and x["study"] == "gyanús" for x in f))

    def test_unit_mismatch_and_double_zero(self):
        rows = [{"study": "s%d" % i, "m1": 5.5 + i * 0.1, "sd1": 1, "n1": 30, "m2": 5.2, "sd2": 1, "n2": 30} for i in range(4)]
        rows.append({"study": "mg/dL", "m1": 99, "sd1": 18, "n1": 30, "m2": 94, "sd2": 18, "n2": 30})
        f = validate.validate(rows, "MD")
        self.assertTrue(any(x["code"] == "V012" and x["study"] == "mg/dL" for x in f))
        f = validate.validate([{"study": "a", "e1": 0, "n1": 10, "e2": 0, "n2": 10},
                               {"study": "a", "e1": 12, "n1": 10, "e2": 1, "n2": 10}], "OR")
        codes = [x["code"] for x in f]
        for c in ("V008", "V006", "V007", "V026"):
            self.assertIn(c, codes)
        # k = 0: a kevés vizsgálatra vonatkozó tanács (V015/V016) nem jelenik meg (robustness:R2-ROB-05)
        self.assertNotIn("V015", codes)
        self.assertNotIn("V016", codes)

    def test_rules_have_metadata(self):
        for code, (sev, title, advice, src) in validate.RULES.items():
            self.assertIn(sev, ("error", "warning", "info"), code)
            self.assertTrue(title and advice and src, code)


class TestPlots(unittest.TestCase):
    def test_svgs_are_wellformed(self):
        yi = [-0.5, -0.2, 0.1, -0.8, -0.3]
        vi = [0.04, 0.09, 0.02, 0.2, 0.05]
        labs = ["A & B <2020>", "C", "D", "E", "F"]
        r = M.meta_analysis(yi, vi, "random")
        for measure in ("OR", "MD", "SMD", "PLO"):
            data = plots.forest_data(measure, labs, yi, vi, r.weights_pct, [{
                "label": "RE", "estimate": r.estimate, "ci_lower": r.ci_lower, "ci_upper": r.ci_upper,
                "pi_lower": r.pi_lower, "pi_upper": r.pi_upper}])
            svg = plots.forest_svg(data, title="Teszt & ábra", left_label="<bal>", right_label="jobb",
                                   footer=["Q = 1"])
            ElementTree.fromstring(svg)
            ElementTree.fromstring(plots.funnel_svg(yi, vi, r.estimate, measure, title="F", filled_yi=[0.3], filled_vi=[0.1]))


if __name__ == "__main__":
    unittest.main()
