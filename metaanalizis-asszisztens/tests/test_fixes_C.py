# -*- coding: utf-8 -*-
"""Regressziós tesztek a C-köteg (pipeline / riport / ábrák) javításaihoz.

Minden teszt egy review-megállapításra hivatkozik (az azonosító a teszt docstringjében).
A referenciaértékek az R metafor 4.4-ből származnak; a generáló R-kód kommentben.
"""
import io
import json
import math
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from xml.etree import ElementTree

from _helpers import ROOT, assert_close
from metaelemzes import cli, pipeline, plots as P, report, tableio
from metaelemzes import effect_sizes as E

EXAMPLES = os.path.join(ROOT, "peldak")

PFT_SG_CSV = "study,x,n,grp\nS1,2,40,a\nS2,5,60,a\nS3,0,25,a\nS4,10,50,b\nS5,12,45,b\nS6,8,70,b\nS7,1,90,a\n"


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:
            code = exc.code
    return code, buf.getvalue(), err.getvalue()


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return p

    def analyze(self, csv_text=None, path=None, **opts):
        """read_table → pipeline.run → build_report (mint a CLI)."""
        if path is None:
            path = self.write("d.csv", csv_text)
        rows, meta = tableio.read_table(path)
        out, es = pipeline.run(rows, opts, meta)
        return out, es, report.build_report(out, date="2026-01-01")

    def cli_analyze(self, data, *extra):
        out = os.path.join(self.tmp, "o%d" % len(os.listdir(self.tmp)))
        code, so, se = run_cli("analyze", "--data", data, "--out", out, "--date", "2026-01-01", *extra)
        self.assertEqual(code, 0, so + se)
        with open(os.path.join(out, "report.md"), encoding="utf-8") as fh:
            md = fh.read()
        with open(os.path.join(out, "results.json"), encoding="utf-8") as fh:
            res = json.load(fh)
        return out, md, res


def _line(md, startswith):
    for ln in md.splitlines():
        if ln.startswith(startswith):
            return ln
    raise AssertionError("nincs ilyen sor: %r" % startswith)


def _cells(row):
    """Markdown-táblázatsor cellái (a '\\|' escape-elt cső nem cellahatár)."""
    parts = re.split(r"(?<!\\)\|", row.strip())
    return [p.strip() for p in parts[1:-1]]


def _numbers(s):
    return [float(x) for x in re.findall(r"-?\d+\.\d+", s)]


# --------------------------------------------------------------- PFT skála
class TestProportionScaleEverywhere(_Tmp):
    """pft-report-untransformed, pft-report-untransformed-subgroups-loo, pft-report-wrong-scale"""

    # R: dat <- escalc("PFT", xi=x, ni=n, data=d); for (g in c("a","b")) predict(rma(yi, vi,
    #    subset=grp==g, test="knha"), transf=transf.ipft.hm, targs=list(ni=n[grp==g]))
    #    → a: 0.0291 [0.0000; 0.1110], b: 0.1841 [0.0329; 0.4111]
    #    leave1out(rma(yi, vi, test="knha"), transf=transf.ipft.hm, targs=list(ni=n)) → 0.0631 – 0.1061
    #    predict(rma(yi, vi, method="FE"), transf=transf.ipft.hm, targs=list(ni=n)) → 0.0804 [0.0536; 0.1115]
    def test_pft_subgroups_loo_fixed_trimfill_back_transformed(self):
        out, es, md = self.analyze(PFT_SG_CSV, measure="PFT", subgroup="grp")
        disp = {g.group: g.display for g in out["subgroups"].groups}
        for got, want in zip(disp["a"], (0.0291, 0.0, 0.1110)):
            assert_close(self, got, want, 6e-4, "a")
        for got, want in zip(disp["b"], (0.1841, 0.0329, 0.4111)):
            assert_close(self, got, want, 6e-4, "b")
        self.assertIn("| a | 4 | 0.03 [0.00; 0.11] |", md)
        self.assertIn("| b | 3 | 0.18 [0.03; 0.41] |", md)
        self.assertIn("Leave-one-out: az összesített becslés tartománya 0.06 – 0.11.", md)
        self.assertIn("közös hatású modell [95% CI]: 0.08 [0.05; 0.11]", md)
        tf = _line(md, "- Trim-and-fill")
        self.assertNotIn("–;", tf)
        self.assertTrue(all(0 <= v <= 1 for v in _numbers(tf.split("korrigált")[1])))
        # a forest-ábra alcsoport-összesítése ugyanaz, mint a riporté
        _, _, data = pipeline.make_plots(out, es)
        self.assertEqual([s["summary"]["display"] for s in data["sections"]], [disp["a"], disp["b"]])

    def test_pritz_loo_and_fixed_match_metafor(self):
        # R: leave1out(rma(yi, vi, test="knha"), transf=transf.ipft.hm, targs=list(ni=n)) → 0.78–0.83;
        #    predict(rma(yi, vi, method="FE"), transf=transf.ipft.hm, ...) → 0.78 [0.73; 0.83]
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "pritz1997_arany.csv"), measure="PFT")
        self.assertIn("Leave-one-out: az összesített becslés tartománya 0.78 – 0.83.", md)
        self.assertIn("közös hatású modell [95% CI]: 0.78 [0.73; 0.83]", md)
        self.assertNotIn("– [–; –]", md)

    def test_zcor_secondary_results_on_r_scale(self):
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "molloy2014_korrelacio.csv"), measure="ZCOR")
        loo = [math.tanh(r["estimate"]) for r in out["sensitivity"]["leave_one_out"]]
        nd = P.decimals((min(loo), max(loo)))
        self.assertIn("tartománya %s – %s." % (report._f(min(loo), nd), report._f(max(loo), nd)), md)
        fx = out["fixed"]
        self.assertIn("közös hatású modell [95% CI]: " + report._tri([math.tanh(x) for x in
                                                                     (fx.estimate, fx.ci_lower, fx.ci_upper)]), md)


# --------------------------------------------------------------- I² és CI
class TestI2PointAndIntervalConsistent(_Tmp):
    """i2-outside-qp-ci, i2-point-outside-ci"""

    def _check(self, csv_text, want_i2, want_lo, want_hi):
        out, es, md = self.analyze(csv_text, measure="GEN")
        h = out["primary"].heterogeneity
        assert_close(self, h["I2_from_tau2"], want_i2, 1e-4, "I2 tau2-alapú")
        assert_close(self, h["I2_ci_QP"][0], want_lo, 2e-3, "QP alsó")
        assert_close(self, h["I2_ci_QP"][1], want_hi, 2e-3, "QP felső")
        row_t = _line(md, "| I² (τ²-alapú")
        self.assertIn("%.1f%% [%.1f; %.1f]" % (want_i2, want_lo, want_hi), row_t)
        row_q = _line(md, "| I² (Q-alapú)")
        self.assertIn("Higgins–Thompson", row_q)
        for row in (row_t, row_q):
            pt, lo, hi = _numbers(_cells(row)[1])
            self.assertTrue(lo <= pt <= hi, row)
        self.assertNotIn("I² [95% CI, Q-profile]", md)

    def test_example_1(self):
        # R: confint(rma(yi, vi, test="knha")) → I² 99.1468 [96.9668; 99.8295]
        self._check("study,yi,vi\nS1,0.5828,0.5471\nS2,1.0635,0.2120\nS3,-0.7203,0.0331\nS4,-0.1415,0.5548\n"
                    "S5,0.3577,0.0100\nS6,0.5470,0.0001\nS7,0.8640,0.0010\n", 99.14678415, 96.96682, 99.82953)

    def test_example_2(self):
        # R: confint(rma(yi, vi)) → I² 98.9588 [92.8488; 99.9055]
        self._check("study,yi,vi\nA,0.1012,0.002\nB,0.6192,0.5\nC,-0.1704,0.001\nD,1.1506,0.5\nE,1.9544,1.0\n"
                    "F,2.4747,1.0\nG,-0.3057,0.002\n", 98.95878777, 92.84883, 99.90546)


class TestLevelLabels(_Tmp):
    """report-hardcoded-95-het-ci, het-ci-level-mislabel"""

    def test_level_90_labels(self):
        # R: confint(rma(yi, vi, data=dat.bcg RR, test="knha", level=90)) →
        #    τ² 0.3132 [0.1410; 0.9098], I² 92.22 [84.22; 97.18]
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), measure="RR", level=0.90)
        self.assertIn("| I² (τ²-alapú, mint a metafor) [90% CI, Q-profile] | 92.2% [84.2; 97.2] |", md)
        self.assertIn("| τ² [90% CI, Q-profile] ; τ | 0.3132 [0.1410; 0.9098] ; 0.5597 |", md)
        self.assertIn("[90% CI, Higgins–Thompson]", md)
        self.assertIn("korrigált becslés [90% CI", md)
        self.assertNotIn("95%", md.split("## Methods")[0])


# --------------------------------------------------------------- Methods
class TestMethodsDescribesWhatWasDone(_Tmp):
    """methods-text-claims-uncomputed-analyses, methods-describes-unperformed-analyses"""
    K2 = "study,m1,sd1,n1,m2,sd2,n2\nA,10,4,30,12,4,30\nB,10,4,40,15,4,40\n"

    def test_k1_fallback(self):
        out, es, md = self.analyze("study,m1,sd1,n1,m2,sd2,n2\nA,10,4,30,12,4,30\n", measure="MD")
        self.assertEqual(out["primary_model"], "fixed")
        self.assertEqual(out["model_fallback"]["requested"], "random")
        self.assertTrue(any(w.startswith("k = 1") for w in out["warnings"]))
        m = report.methods_text_en(out)
        for bad in ("random-effects model was used", "prediction interval was calculated", "Influence was examined",
                    "fitted as a sensitivity analysis", "Small-study effects were explored"):
            self.assertNotIn(bad, m)
        self.assertIn("Only one study was available", m)
        self.assertIn("Becslés (egyetlen vizsgálat)", md)

    def test_k2_no_pi_no_influence(self):
        out, es, md = self.analyze(self.K2, measure="MD")
        self.assertIsNone(out["primary"].pi_lower)
        m = report.methods_text_en(out)
        self.assertNotIn("prediction interval was calculated", m)
        self.assertIn("A prediction interval was not calculated", m)
        self.assertNotIn("Influence was examined", m)
        self.assertIn("Leave-one-out and influence analyses were not performed", m)

    def test_pi_df_and_peto_mh_ivhet(self):
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), measure="OR", mh=True,
                                   peto=True, model="ivhet")
        m = report.methods_text_en(out)
        self.assertIn("Peto odds ratios were additionally calculated", m)
        self.assertIn("Mantel–Haenszel", m)
        self.assertIn("inverse variance heterogeneity (IVhet)", m)
        # a Methods szerint a véletlen hatású modell érzékenységi elemzés → a riportban szerepel
        self.assertIn("Érzékenység — véletlen hatású modell", md)
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), measure="RR")
        self.assertIn("k − 2 = 11 degrees of freedom", report.methods_text_en(out))
        self.assertIn("Predikciós intervallum [95%, t(k−2 = 11)]", md)

    def test_subgroup_sentence_follows_model(self):
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), measure="RR",
                                   subgroup="allokáció", model="fixed")
        m = report.methods_text_en(out)
        self.assertIn("common-effect models within subgroups", m)
        self.assertNotIn("separate τ²", m)


class TestMethodsZeroCells(_Tmp):
    """methods-text-ignores-cc-drop00, methods-cc-misstated"""
    CSV = "study,e1,n1,e2,n2\nA,0,50,0,52\nB,1,40,0,38\nC,0,100,3,98\nD,2,60,5,61\nE,1,200,4,190\n"

    def test_cc_all_and_drop00_no(self):
        out, es, md = self.analyze(self.CSV, measure="OR", cc=0.25, cc_to="all", drop00=False)
        self.assertEqual(out["effect_sizes"]["k"], 5)
        m = report.methods_text_en(out)
        self.assertIn("A continuity correction of 0.25 was added to all cells of all studies.", m)
        self.assertIn("were retained in the analysis (1 study)", m)
        self.assertNotIn("were excluded", m.split("Because")[0])

    def test_cc_none(self):
        out, es, md = self.analyze(self.CSV, measure="RR", cc_to="none")
        m = report.methods_text_en(out)
        self.assertIn("No continuity correction was applied", m)
        self.assertNotIn("A continuity correction of", m)

    def test_default_only0_counts(self):
        out, es, md = self.analyze(self.CSV, measure="OR")
        m = report.methods_text_en(out)
        self.assertIn("studies with a zero cell (2 studies)", m)
        self.assertIn("in both arms were excluded (1 study)", m)

    def test_rd_and_pft_no_correction_claim(self):
        out, es, md = self.analyze(self.CSV, measure="RD")
        m = report.methods_text_en(out)
        self.assertIn("Risk differences were calculated without continuity correction", m)
        self.assertNotIn("added to all cells", m)
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "pritz1997_arany.csv"), measure="PFT")
        m = report.methods_text_en(out)
        self.assertIn("No continuity correction was needed", m)
        self.assertNotIn("contour-enhanced", m)


# --------------------------------------------------------------- ábrák
class TestForestSubgroupDuplicateLabels(_Tmp):
    """forest-subgroup-duplicate-labels, subgroup-forest-duplicate-labels"""

    def test_sections_use_row_positions(self):
        csv_text = ("study,m1,sd1,n1,m2,sd2,n2,grp\nTrial X,10,4,30,12,4,30,a\nTrial X,20,4,30,12,4,30,b\n"
                    "Y,10,4,40,15,4,40,a\nZ,10,4,35,10.5,4,35,b\nW,11,4,35,10.5,4,35,b\n")
        out, es, md = self.analyze(csv_text, measure="MD", subgroup="grp")
        svg, _, data = pipeline.make_plots(out, es)
        self.assertEqual([s["indices"] for s in data["sections"]], [[0, 2], [1, 3, 4]])
        self.assertEqual([s["row_index"] for s in data["sections"]], [[0, 2], [1, 3, 4]])
        self.assertIn("8.00 [", svg)          # a második 'Trial X' (MD +8) is a b-alcsoportban
        out, es, md = self.analyze("study,yi,vi,grp\nSmith 2010,0.10,0.1,dose_low\nJones 2011,0.30,0.2,dose_low\n"
                                   "Smith 2010,0.90,0.1,dose_high\nKim 2012,0.70,0.1,dose_high\n",
                                   measure="GEN", subgroup="grp")
        self.assertEqual([s["indices"] for s in pipeline.make_plots(out, es)[2]["sections"]], [[0, 1], [2, 3]])


class TestForestAxisScale(_Tmp):
    """forest-axis-transformed-scale"""

    def _axis_consistent(self, axis, measure, nh=None):
        ticks = axis.ticks()
        self.assertGreaterEqual(len(ticks), 2)
        for pos, lab in ticks:
            assert_close(self, E.back_transform(measure, pos, nh), float(lab), 1e-9, "%s tick %s" % (measure, lab))
        return [float(lab) for _, lab in ticks]

    def test_proportion_axes_back_transformed_without_null_line(self):
        for measure in ("PLO", "PFT", "PAS", "PLN", "PR"):
            out, es, md = self.analyze(path=os.path.join(EXAMPLES, "pritz1997_arany.csv"), measure=measure)
            svg, funnel, data = pipeline.make_plots(out, es)
            ElementTree.fromstring(svg)
            self.assertNotIn("stroke-dasharray=\"3,3\"", svg, measure)      # nincs nullhatás-vonal
            labels = [float(x) for x in re.findall(r'text-anchor="middle" fill="#1a1a1a">([-0-9.]+)<', svg)]
            self.assertTrue(labels and all(0 <= x <= 1 for x in labels), (measure, labels))
            self.assertIsNone(data["null_value"])
            self.assertTrue(data["axis_title"].startswith("Arány"))
            if measure != "PR":
                self.assertIn("skálán ábrázolva", data["axis_title"])
            axis = P._Axis(min(s["lo"] for s in data["studies"]), max(s["hi"] for s in data["studies"]),
                           300, 640, False, measure, data.get("axis_n"))
            self._axis_consistent(axis, measure, data.get("axis_n"))
            # funnel: nincs kontúr, a tengely is arányban
            self.assertNotIn("<polygon", funnel, measure)
            self.assertIn(">Funnel plot<", funnel)

    def test_zcor_axis_in_r_with_null_at_zero(self):
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "molloy2014_korrelacio.csv"), measure="ZCOR")
        svg, funnel, data = pipeline.make_plots(out, es)
        self.assertIn("stroke-dasharray=\"3,3\"", svg)
        self.assertEqual(data["null_value"], 0.0)
        axis = P._Axis(-0.3, 0.6, 300, 640, False, "ZCOR")
        self.assertIn(0.0, self._axis_consistent(axis, "ZCOR"))

    def test_ratio_axis_narrow_range_has_several_ticks(self):
        """ratio-axis-single-tick"""
        pos, ticks = P._ratio_ticks(math.log(0.88), math.log(1.12))
        self.assertGreaterEqual(len(ticks), 3)
        self.assertIn(1, ticks)
        out, es, md = self.analyze("study,e1,n1,e2,n2\nA,900,5000,950,5000\nB,1800,10000,1850,10000\n"
                                   "C,1500,8000,1560,8000\nD,2100,12000,2150,12000\n", measure="RR")
        svg, funnel, data = pipeline.make_plots(out, es)
        for doc in (svg, funnel):
            self.assertGreaterEqual(len(re.findall(r'text-anchor="middle" fill="#1a1a1a">[0-9.]+<', doc)), 3)


class TestForestClipping(_Tmp):
    """forest-clipped-pi-diamond"""

    def test_pi_beyond_axis_gets_arrows(self):
        out, es, md = self.analyze("study,m1,sd1,n1,m2,sd2,n2\nA,10,4,30,12,4,30\nB,10,4,40,15,4,40\n"
                                   "C,10,4,35,10.5,4,35\n", measure="MD")
        svg, _, _ = pipeline.make_plots(out, es)
        self.assertEqual(len(re.findall(r'<polygon points="[^"]+" fill="%s"/>' % P.PI_COLOR, svg)), 2)

    def test_diamond_beyond_axis_gets_arrows(self):
        data = P.forest_data("MD", ["a", "b", "c", "d", "e", "f", "g"], [0.0, 0.1, 0.2, -0.1, 0.05, 0.15, 0.12],
                             [0.01] * 7, None, [])
        data["summaries"] = [{"label": "S", "estimate": 0.1, "ci_lower": 0.0, "ci_upper": 0.2,
                              "pi_lower": None, "pi_upper": None}]
        data["sections"] = [{"title": "x", "indices": list(range(7)), "summary": {
            "label": "wide", "estimate": 0.1, "ci_lower": -50.0, "ci_upper": 50.0, "pi_lower": None, "pi_upper": None}}]
        svg = P.forest_svg(data)
        ElementTree.fromstring(svg)
        arrows = re.findall(r'<polygon points="([0-9.]+),[0-9.]+ [^"]+" fill="#1a1a1a"/>', svg)
        self.assertEqual(sorted(arrows), ["300.0", "640.0"])


class TestFunnelContours(_Tmp):
    """funnel-contours-proportions"""

    def test_contours_centred_on_null(self):
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), measure="RR")
        _, funnel, data = pipeline.make_plots(out, es)
        polys = re.findall(r'<polygon points="([0-9.]+),', funnel)
        self.assertEqual(len(polys), 3)
        self.assertTrue(data["funnel"]["contour"])
        self.assertEqual(len(set(polys)), 1)              # közös csúcs: a nullhatás (log RR = 0)

    def test_no_contours_for_proportions(self):
        svg = P.funnel_svg([0.9, 1.0, 1.2], [0.01, 0.02, 0.03], 1.0, "PFT", title="F", n_harmonic=30)
        self.assertNotIn("<polygon", svg)
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "pritz1997_arany.csv"), measure="PFT")
        self.assertFalse(pipeline.make_plots(out, es)[2]["funnel"]["contour"])


# --------------------------------------------------------------- riport-tartalom
class TestCumulativeInReport(_Tmp):
    """cumulative-not-reported"""

    def test_cumulative_table_and_methods(self):
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), measure="RR", cumulative="év")
        self.assertEqual(out["columns"]["cumulative"], "year")
        self.assertEqual(len(out["sensitivity"]["cumulative"]), 13)
        self.assertIn("### Kumulatív metaanalízis (rendezés: év)", md)
        rows = [ln for ln in md.split("### Kumulatív")[1].splitlines() if re.match(r"\| \d+ \|", ln)]
        self.assertEqual(len(rows), 13)
        last = out["sensitivity"]["cumulative"][-1]
        self.assertIn(report._tri([math.exp(x) for x in (last["estimate"], last["ci_lower"], last["ci_upper"])]),
                      rows[-1])
        self.assertIn("A cumulative meta-analysis ordered by év was performed.", report.methods_text_en(out))

    def test_unknown_column_is_clear_error(self):
        p = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")
        rows, meta = tableio.read_table(p)
        for key in ("cumulative", "subgroup"):
            with self.assertRaises(ValueError) as cm:
                pipeline.run(rows, {"measure": "RR", key: "nincs_ilyen"}, meta)
            self.assertIn("--%s" % key, str(cm.exception))
            self.assertIn("nincs_ilyen", str(cm.exception))
        with self.assertRaises(ValueError):
            pipeline.run(rows, {"measure": "RR", "moderators": ["szélesség", "xx"]}, meta)


class TestNumberFormatting(_Tmp):
    """rd-two-decimals"""

    def test_rd_magnitude_aware(self):
        # R: rma.mh(measure="RD", dat.bcg) → -0.0033 [-0.0039; -0.0027];
        #    rma(yi, vi, method="FE") (RD) → -0.0009 [-0.0014; -0.0005]
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), measure="RD", mh=True)
        self.assertIn("Mantel–Haenszel [95% CI]: -0.0033 [-0.0039; -0.0027], p < 0.001.", md)
        self.assertIn("közös hatású modell [95% CI]: -0.0009 [-0.0014; -0.0005].", md)
        self.assertNotIn("-0.00 ", md)
        self.assertNotIn("p = <", md)

    def test_helpers(self):
        self.assertEqual(P.fmt_triple(-0.54, -1.25, 0.18), "-0.54 [-1.25; 0.18]")
        self.assertEqual(P.fmt_triple(-0.0001, -0.2, 0.3), "0.00 [-0.20; 0.30]")   # nincs '-0.00'
        self.assertEqual(report._p_eq(0.0001), "p < 0.001")
        self.assertEqual(report._p_eq(0.042), "p = 0.042")


class TestTrimFillCI(_Tmp):
    """trimfill-ci-inconsistent"""

    def test_k0_zero_equals_primary(self):
        # R: trimfill(rma(yi, vi, test="knha")) (normand1999 SMD): k0 = 0, -0.54 [-1.25; 0.18]
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "normand1999_folytonos.csv"), measure="SMD")
        tf = out["bias"]["trimfill"]
        self.assertEqual(tf.k0, 0)
        for a, b in ((tf.adjusted.ci_lower, out["primary"].ci_lower), (tf.adjusted.ci_upper, out["primary"].ci_upper)):
            assert_close(self, a, b, 1e-10)
        self.assertIn("korrigált becslés [95% CI, HKSJ]: -0.54 [-1.25; 0.18]", md)
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "normand1999_folytonos.csv"), measure="SMD", level=0.9)
        self.assertEqual(out["bias"]["trimfill"].adjusted.level, 0.9)
        self.assertIn("korrigált becslés [90% CI, HKSJ]", md)


class TestFiltersAndFixedCI(_Tmp):
    """report-ignores-filters-and-fixed-ci"""

    def test_filters_recorded_in_report_and_methods(self):
        out, md, res = self.cli_analyze(os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), "--measure", "RR",
                                        "--exclude", "allokáció=random", "--no-plots")
        flt = res["input"]["filters"]
        self.assertEqual((flt["n_rows_read"], flt["n_rows_kept"]), (13, 6))
        self.assertEqual(flt["report"][0]["removed"], 7)
        self.assertIn("**Szűrés (érzékenységi elemzés):** 13 sorból 6 maradt.", md)
        self.assertIn("--exclude `allokáció=random`: 7 sor kizárva", md)
        self.assertIn("Aronson 1948", md.split("## Fő")[0])
        self.assertIn("This is a restricted (sensitivity) analysis", md)

    def test_fixed_model_honours_ci_t(self):
        # R: rma(yi, vi, method="FE", test="t") (normand1999 SMD) → -0.4106 [-0.5527; -0.2686]
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "normand1999_folytonos.csv"), measure="SMD",
                                   model="fixed", ci="t")
        pr = out["primary"]
        self.assertEqual(pr.ci_method, "t")
        assert_close(self, pr.ci_lower, -0.5526676, 1e-6)
        assert_close(self, pr.ci_upper, -0.2685552, 1e-6)
        self.assertIn("t-distribution based confidence intervals", report.methods_text_en(out))


class TestMarkdownEscaping(_Tmp):
    """markdown-label-injection"""

    def test_pipes_and_html_escaped(self):
        csv_text = ('study,e1,n1,e2,n2,arm\n"Smith | 2001",0,50,0,52,"low | dose"\nJones,1,40,3,38,"low | dose"\n'
                    '"Lee <b>x</b>",4,100,9,98,high\nKim,2,60,5,61,high\nPark,3,70,6,72,high\n')
        out, es, md = self.analyze(csv_text, measure="OR", subgroup="arm")
        row = _line(md, "| low \\| dose")
        self.assertEqual(len(_cells(row)), 5)
        v008 = _line(md, "| V008")
        self.assertEqual(len(_cells(v008)), 5)
        self.assertNotIn("<b>", md)
        self.assertEqual(report._md("a|b<i>c</i>\nd"), "a\\|b&lt;i>c&lt;/i> d")
        self.assertEqual(report._md("k < 10"), "k < 10")


class TestTotals(_Tmp):
    """agents-need-participants"""

    def test_binary_totals(self):
        # dat.bcg: Σ(n1+n2) = 357347; Σtpos = 1065 / Σn1 = 191064; Σcpos = 1510 / Σn2 = 166283
        out, md, res = self.cli_analyze(os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), "--measure", "RR")
        t = res["totals"]
        self.assertEqual(t["participants"], 357347)
        self.assertEqual((t["events1"], t["n1"], t["events2"], t["n2"]), (1065, 191064, 1510, 166283))
        assert_close(self, t["control_risk"], 1510 / 166283.0, 1e-12)
        rr = res["back_transformed"]["estimate_ci"]
        assert_close(self, t["absolute_per_1000"]["difference"][0], 1000 * (1510 / 166283.0) * (rr[0] - 1), 1e-9)
        self.assertIn("| Résztvevők (N, elemzett vizsgálatok) | 357347 |", md)
        self.assertIn("| Események: 1. kar / 2. (kontroll) kar | 1065/191064", md)
        with open(os.path.join(out, "effect_sizes.csv"), encoding="utf-8") as fh:
            head, first = fh.readline().strip(), fh.readline().strip()
        self.assertEqual(head.split(",")[7], "n")
        self.assertEqual(first.split(",")[7], "262")          # Aronson 1948: 123 + 139

    def test_continuous_and_proportion_totals(self):
        out, es, md = self.analyze(path=os.path.join(EXAMPLES, "pritz1997_arany.csv"), measure="PLO")
        rows, _ = tableio.read_table(os.path.join(EXAMPLES, "pritz1997_arany.csv"))
        self.assertEqual(out["totals"]["participants"], int(sum(r["n"] for r in rows)))
        self.assertEqual(out["totals"]["events"], int(sum(r["x"] for r in rows)))
        out, es, md = self.analyze("study,yi,vi\nA,0.1,0.1\nB,0.2,0.1\n", measure="GEN")
        self.assertIsNone(out["totals"]["participants"])
        self.assertIn("| Résztvevők (N) | – ", md)


# --------------------------------------------------------------- szerződés
class TestPipelineContract(_Tmp):
    """C-oldali szerződés: oszlopnév-feloldás, validálási hibák kizárása, szűrőnapló"""

    def test_validation_errors_really_excluded(self):
        csv_text = "study,e1,n1,e2,n2\nA,12,10,3,10\nB,1,10,2,10\nC,3,20,5,20\nD,4,30,6,30\n"
        out, es, md = self.analyze(csv_text, measure="OR")
        self.assertEqual(out["effect_sizes"]["k"], 3)
        self.assertNotIn("A", es.labels)
        reasons = {e["study"]: e["reason"] for e in out["effect_sizes"]["excluded"]}
        self.assertTrue(reasons["A"].startswith("validálási hiba"))
        self.assertIn("A hibás sorok (1) kimaradtak", md)
        self.assertEqual(out["validation"]["blocked_studies"], ["A"])
        self.assertIn("could not be analysed and was excluded (1 because of data errors", report.methods_text_en(out))

    def test_subgroup_resolved_by_original_header_and_alias(self):
        p = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")
        rows, meta = tableio.read_table(p)
        out, es = pipeline.run(rows, {"measure": "RR", "subgroup": "ALLOKACIO", "cumulative": "year",
                                      "moderators": ["év"]}, meta)
        self.assertEqual(out["columns"], {"subgroup": "allokáció", "cumulative": "year", "moderators": ["year"]})
        self.assertEqual([g.group for g in out["subgroups"].groups], ["random", "alternate", "systematic"])
        self.assertEqual(sum(len(g.indices) for g in out["subgroups"].groups), 13)

    def test_numeric_subgroup_levels_are_clean_strings(self):
        out, es, md = self.analyze("study,yi,vi,dose\nA,0.1,0.1,1\nB,0.2,0.1,1\nC,0.4,0.1,2\nD,0.5,0.1,2\n",
                                   measure="GEN", subgroup="dose")
        self.assertEqual([g.group for g in out["subgroups"].groups], ["1", "2"])

    def test_filter_report_passed_through(self):
        p = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")
        rows, meta = tableio.read_table(p)
        rep = []
        rows = tableio.apply_filters(rows, exclude=["év=1948"], meta=meta, report=rep)
        meta["filters"] = {"exclude": ["év=1948"], "include": None}
        out, es = pipeline.run(rows, {"measure": "RR", "filter_report": rep}, meta)
        self.assertEqual(out["input"]["filters"]["report"][0]["removed_labels"], ["Aronson 1948"])
        self.assertEqual(out["effect_sizes"]["k"], 12)
        self.assertEqual(len(out["effect_sizes"]["row_index"]), 12)


if __name__ == "__main__":
    unittest.main()
