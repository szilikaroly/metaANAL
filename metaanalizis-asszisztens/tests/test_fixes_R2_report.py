# -*- coding: utf-8 -*-
"""A 2. review-kör riport-, pipeline- és ábra-hibáinak regressziós tesztjei (R2-*).

Minden teszt neve a hiba azonosítója. Referenciák: R 4.3 / metafor 4.4 (a kódot lásd a
kommentekben), illetve Khan 2020 11. fejezet (Stata regress [aw=1/v], vce(robust)).
"""
import io
import json
import math
import os
import re
import shutil
import tempfile
import unittest
import xml.dom.minidom
from contextlib import redirect_stdout, redirect_stderr

from _helpers import HERE, ROOT, assert_close
from metaelemzes import cli, pipeline, report, tableio
from metaelemzes import effect_sizes as E
from metaelemzes import plots as P

EXAMPLES = os.path.join(ROOT, "peldak")
BCG = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")
NORMAND = os.path.join(EXAMPLES, "normand1999_folytonos.csv")
PRITZ = os.path.join(EXAMPLES, "pritz1997_arany.csv")

MR14 = ("study,yi,vi,x1\n" + "\n".join("s%d,%r,%r,%d" % (i + 1, y, v, x) for i, (y, v, x) in enumerate(zip(
    [0.41, -0.21, 0.88, -0.35, 0.96, 0.11, 0.73, 1.23, -0.47, 0.68, 0.58, -0.44, 0.09, 1.08],
    [0.06, 0.078, 0.044, 0.044, 0.025, 0.051, 0.033, 0.04, 0.09, 0.028, 0.062, 0.029, 0.035, 0.081],
    [1, 2, 3, 1, 4, 2, 5, 6, 1, 7, 2, 1, 3, 5]))) + "\n")
PFTBIG = "study,x,n,year\nBig 2001,2,400,2001\nS2,3,17,2003\nS3,2,12,2005\nS4,1,8,2007\nS5,2,10,2009\nS6,1,6,2011\n"


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

    def csv(self, text, name="adat.csv"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def run_pipeline(self, path, **opts):
        rows, meta = tableio.read_table(path)
        out, es = pipeline.run(rows, opts, meta)
        return out, es, report.build_report(out, date="2026-10-04")


def _axis_labels(svg):
    """A vízszintes tengely feliratai és x-pozíciói."""
    return [(float(x), lab) for x, lab in re.findall(
        r'<text x="([0-9.]+)" y="[0-9.]+" text-anchor="middle" fill="#1a1a1a">([^<]*)</text>', svg)]


# ------------------------------------------------------------------ robusztus meta-regresszió
class TestRobustMetaregLabel(_Tmp):
    def test_R2_NF_02_reml_weights_are_labelled_truthfully(self):
        out, es, md = self.run_pipeline(self.csv(MR14), measure="GEN", model="fixed", moderators=["x1"],
                                        metareg_robust=True)
        mr = out["metaregression"]
        self.assertEqual(mr.tau2_method, "REML")
        # = metafor robust(rma(yi, vi, mods=~x1, method="REML"), cluster=1:14, adjust=TRUE)
        assert_close(self, mr.robust["coefficients"][1]["se"], 0.06091, 1e-3)
        head = md.split("## Methods")[0]
        self.assertNotIn("t(12) próbák, mint a Stata", head)
        self.assertIn("1/(v_i + τ²)", head)
        self.assertIn("metafor `robust(", head)
        self.assertIn("csak τ² = 0 (FE-súlyok) esetén egyezik", head)
        meth = report.methods_text_en(out)
        self.assertNotIn("(as Stata regress with vce(robust))", meth)
        self.assertIn("random-effects weights 1/(v_i + τ²)", meth)
        self.assertIn("only when τ² = 0", meth)

    def test_R2_NF_02_fe_weights_reproduce_stata(self):
        # R: lm(yi ~ x1, weights = 1/vi) + sandwich::vcovHC(type = "HC1")  (= Stata regress [aw=1/v], vce(robust))
        out, es, md = self.run_pipeline(self.csv(MR14), measure="GEN", moderators=["x1"], metareg_robust=True,
                                        metareg_tau2="fe", metareg_test="z")
        mr = out["metaregression"]
        self.assertEqual(mr.tau2_method, "FE")
        rb = mr.robust
        assert_close(self, rb["coefficients"][1]["estimate"], 0.2078489, 1e-6)
        assert_close(self, rb["coefficients"][1]["se"], 0.06370106, 1e-6)
        assert_close(self, rb["coefficients"][1]["p"], 0.006792284, 1e-6)
        assert_close(self, rb["F"], 10.646381711, 1e-6)
        assert_close(self, rb["r2"], 0.5718, 1e-3)
        assert_close(self, rb["root_mse"], 0.39373, 1e-4)
        self.assertIn("FE-súlyok 1/v_i, mint a Stata `regress … [aw = 1/v], vce(robust)`", md)
        self.assertIn("Reziduális τ² = – (közös hatású meta-regresszió, τ² = 0)", md)
        meth = report.methods_text_en(out)
        self.assertIn("Fixed-effect (inverse-variance weighted, τ² = 0) meta-regression", meth)
        self.assertIn("fixed-effect weights 1/v_i (equivalent to Stata regress", meth)
        self.assertNotIn("Mixed-effects meta-regression", meth)

    def test_R2_NF_02_khan_ihdchol_through_pipeline(self):
        # Khan 2020 p. 248: regress _ES chol_reduc [aw=1/(_seES^2)], vce(robust) eform → 0.6217327 (0.5205299–0.7426115),
        # F(1, 26) = 30.23
        with open(os.path.join(HERE, "reference", "source_examples", "khan_ch11_13.json"), encoding="utf-8") as fh:
            case = [c for c in json.load(fh)
                    if c["case_id"] == "khan_ch11_13__ihdchol_wls_metareg_continuous_robust_inference"][0]
        lines = ["study,e1,n1,e2,n2,chol_reduc"] + ["%s,%s,%s,%s,%s,%s" % (
            r["study"], r["e1"], r["n1"], r["e2"], r["n2"], r["chol_reduc"]) for r in case["rows"]]
        out, es, md = self.run_pipeline(self.csv("\n".join(lines) + "\n"), measure="OR", model="fixed",
                                        moderators=["chol_reduc"], metareg_robust=True, metareg_tau2="FE")
        c = out["metaregression"].robust["coefficients"][1]
        assert_close(self, math.exp(c["estimate"]), 0.6217327, 5e-7)
        assert_close(self, math.exp(c["ci_lower"]), 0.5205299, 5e-7)
        assert_close(self, math.exp(c["ci_upper"]), 0.7426115, 5e-7)
        self.assertAlmostEqual(out["metaregression"].robust["F"], 30.23, places=2)
        # metafor is figyelmeztet: knha + FE
        self.assertTrue(any("Knapp–Hartung-próba nem közös hatású" in w for w in out["warnings"]))

    def test_R2_NF_02_invalid_metareg_tau2(self):
        rows, meta = tableio.read_table(self.csv(MR14))
        with self.assertRaises(ValueError):
            pipeline.run(rows, {"measure": "GEN", "moderators": ["x1"], "metareg_tau2": "XX"}, meta)

    def test_R2_REP_04_bcg_reml_and_fe_weights(self):
        # R: robust(rma(yi, vi, mods=~ablat, data=dat, method="REML"), cluster=1:13, adjust=TRUE) → 0.2515/0.1905,
        #    -0.0291/0.0052, F = 31.4948; method="FE" → 0.343564577/0.0971512687, -0.029236934/0.0044072221, F = 44.008
        out, es, md = self.run_pipeline(BCG, measure="RR", model="ivhet", moderators=["szélesség"], metareg_robust=True)
        rb = out["metaregression"].robust
        assert_close(self, rb["coefficients"][0]["se"], 0.1905430675, 1e-4)
        assert_close(self, rb["F"], 31.494766, 1e-4)
        self.assertIn("τ² = %s (REML)" % report._f(out["metaregression"].tau2, 4), md)
        self.assertIn("equivalent to metafor robust() with adjust = TRUE", report.methods_text_en(out))
        out, es, md = self.run_pipeline(BCG, measure="RR", moderators=["szélesség"], metareg_robust=True,
                                        metareg_tau2="FE")
        rb = out["metaregression"].robust
        assert_close(self, rb["coefficients"][0]["estimate"], 0.343564577, 1e-7)
        assert_close(self, rb["coefficients"][0]["se"], 0.0971512687, 1e-7)
        assert_close(self, rb["coefficients"][1]["se"], 0.0044072221, 1e-7)
        assert_close(self, rb["F"], 44.008218, 1e-6)
        self.assertIn("FE-súlyok 1/v_i", md)


# ------------------------------------------------------------------ PFT / arányok
class TestProportionReport(_Tmp):
    PFT_SG = "study,x,n,grp\nA,5,40,a\nB,12,60,a\nC,3,25,b\nD,20,90,b\nE,8,55,a\nF,1,30,b\nG,15,70,a\n"

    def test_R2_NF_06_variance_subgroup_note(self):
        out, es, md = self.run_pipeline(self.csv(self.PFT_SG), measure="PFT", subgroup="grp",
                                        pft_backtransform="variance")
        g = out["subgroups"].groups[0]
        assert_close(self, g.display[0], E.back_transform("PFT", g.estimate, None, "variance", g.se), 1e-12)
        self.assertNotIn("az alcsoport saját harmonikus átlag n-jével", md)
        self.assertIn("m = 1/Var(t) = 1/(4·SE²)", md)
        out, es, md = self.run_pipeline(self.csv(self.PFT_SG), measure="PFT", subgroup="grp")
        self.assertIn("_Az alcsoport-becslések visszatranszformálása az alcsoport saját harmonikus átlag n-jével._", md)

    def test_R2_ROB_01_plo_pln_without_continuity_correction(self):
        plo = self.csv("study,x,n\nA,5,30\nB,10,40\nC,7,25\nD,12,50\n", "plo.csv")
        for data, measure, flag, scale in ((plo, "PLO", ("--cc", "0"), "logit"), (PRITZ, "PLN", ("--cc-to", "none"), "log"),
                                           (plo, "PLN", ("--cc", "0"), "log"), (PRITZ, "PLO", ("--cc-to", "none"), "logit")):
            o = os.path.join(self.tmp, measure + flag[0])
            code, so, se = run_cli("analyze", "--data", data, "--measure", measure, *flag, "--out", o, "--no-plots")
            self.assertEqual(code, 0, so + se)
            with open(os.path.join(o, "report.md"), encoding="utf-8") as fh:
                md = fh.read()
            self.assertIn("No continuity correction was applied; studies with 0% or 100% events could not be "
                          "analysed on the " + scale + " scale and were excluded.", md)

    def test_R2_REP_01_build_report_pln_plo_cc_none(self):
        for measure, scale in (("PLN", "log"), ("PLO", "logit")):
            for opts in ({"cc_to": "none"}, {"cc": 0}):
                out, es, md = self.run_pipeline(PRITZ, measure=measure, **opts)
                self.assertIn("events could not be analysed on the %s scale and were excluded." % scale, md)

    def test_R2_REP_05_pft_markers_on_their_labelled_values(self):
        for path in (self.csv(PFTBIG, "pftbig.csv"), PRITZ):
            for mode in ("harmonic", "variance"):
                out, es, md = self.run_pipeline(path, measure="PFT", pft_backtransform=mode)
                svg, _, data = pipeline.make_plots(out, es)
                nax = data["axis_n"]
                for s in data["studies"]:
                    for key, d in zip(("y", "lo", "hi"), s["display"]):
                        assert_close(self, E.back_transform("PFT", s[key], nax), d, 1e-9, "%s %s" % (s["label"], key))
                for sm in data["summaries"]:
                    for key, d in zip(("estimate", "ci_lower", "ci_upper"), sm["display"]):
                        assert_close(self, E.back_transform("PFT", sm[key], nax), d, 1e-9, sm["label"])
        # a nagy vizsgálat (2/400) CI-vonala nem nyúlik a '0' tick alá; a 10/10 vizsgálat a '1' tickig ér
        out, es, md = self.run_pipeline(self.csv(PFTBIG, "pftbig.csv"), measure="PFT")
        svg = pipeline.make_plots(out, es)[0]
        x0 = [x for x, lab in _axis_labels(svg) if lab == "0"][0]
        x1 = float(re.search(r'>Big 2001</text>\n<line x1="([0-9.]+)"', svg).group(1))
        self.assertGreaterEqual(x1, x0 - 0.05)
        out, es, md = self.run_pipeline(PRITZ, measure="PFT")
        svg = pipeline.make_plots(out, es)[0]
        xone = [x for x, lab in _axis_labels(svg) if lab == "1"][0]
        m = re.search(r'>Tanabe[^<]*</text>\n<line x1="[0-9.]+" x2="([0-9.]+)"[^\n]*\n<rect x="([0-9.]+)" y="[0-9.]+" '
                      r'width="([0-9.]+)"', svg)
        self.assertLessEqual(float(m.group(1)), xone + 0.05)
        assert_close(self, float(m.group(2)) + float(m.group(3)) / 2, xone, 0.15)

    def test_R2_REP_06_pft_pooled_outside_observed_range_warns(self):
        out, es, md = self.run_pipeline(self.csv(PFTBIG, "pftbig.csv"), measure="PFT")
        w = [x for x in out["warnings"] if x.startswith("Freeman–Tukey-visszatranszformálás")]
        self.assertEqual(len(w), 1, out["warnings"])
        self.assertIn("közös hatású: 0.0000, megfigyelt tartomány [0.0050; 0.2000]", w[0])
        self.assertIn("--measure PLO", w[0])
        self.assertIn("Freeman–Tukey-visszatranszformálás (harmonikus átlag n", md.split("## Alcsoport")[0])
        out, es, md = self.run_pipeline(PRITZ, measure="PFT")
        self.assertFalse([x for x in out["warnings"] if "Freeman–Tukey-visszatranszformálás" in x])

    def test_R2_REP_16_raw_proportion_bounds_warning(self):
        # metafor: predict(rma(measure="PR", ..., test="knha")) → PI 0.5040–1.0895 (t(k−1); a számok helyesek,
        # csak jelzés kell)
        out, es, md = self.run_pipeline(PRITZ, measure="PR", pi="t_k-1")
        assert_close(self, out["back_transformed"]["pi"][1], 1.0895, 1e-3)
        w = [x for x in out["warnings"] if x.startswith("Nyers arány (PR)")]
        self.assertEqual(len(w), 1, out["warnings"])
        self.assertIn("a predikciós intervallum", w[0])
        self.assertIn("vizsgálat CI-je", w[0])
        self.assertIn("D-S07-018", w[0])
        self.assertIn("- Nyers arány (PR):", md)
        out, es, md = self.run_pipeline(PRITZ, measure="PLO")
        self.assertFalse([x for x in out["warnings"] if x.startswith("Nyers arány")])
        ok = self.csv("study,x,n\nA,20,50\nB,25,60\nC,30,70\nD,22,55\n", "ok.csv")
        out, es, md = self.run_pipeline(ok, measure="PR")
        self.assertFalse([x for x in out["warnings"] if x.startswith("Nyers arány")])


# ------------------------------------------------------------------ hatásméret-konvenciók
class TestEffectSizeMethods(_Tmp):
    TWO_ARM = ("study,m1,sd1,n1,m2,sd2,n2\nA,12,3,30,10,2.5,30\nB,14,4,25,11,3,24\nC,11,3.5,40,10.5,3.2,38\n"
               "D,15,4.2,22,12,3.9,20\n")

    def test_R2_NF_08_mixed_metan_pairings_described_as_computed(self):
        p = self.csv(self.TWO_ARM)
        out, es, md = self.run_pipeline(p, measure="SMD", smd_vtype="METAN_COHEN")
        m = report.methods_text_en(out)
        self.assertNotIn("d²/(2(N − 2))", m)
        self.assertIn("N/(n1·n2) + g²/(2(N − 2))", m)
        self.assertIn("matches neither Stata metan convention", m)
        out, es, md = self.run_pipeline(p, measure="COHEN_D", smd_vtype="METAN_HEDGES")
        m = report.methods_text_en(out)
        self.assertNotIn("J = 1 − 3/(4N − 9)", m)
        self.assertNotIn("g²/(2(N − 3.94))", m)
        self.assertIn("N/(n1·n2) + d²/(2(N − 3.94))", m)
        # a párosított konvenciók változatlanul
        out, es, md = self.run_pipeline(p, measure="SMD", smd_vtype="METAN_HEDGES")
        m = report.methods_text_en(out)
        self.assertIn("approximate correction factor J = 1 − 3/(4N − 9)", m)
        self.assertIn("Stata metan / MetaXL variance formula N/(n1·n2) + g²/(2(N − 3.94))", m)
        out, es, md = self.run_pipeline(p, measure="COHEN_D", smd_vtype="METAN_COHEN")
        self.assertIn("Stata metan / MetaXL variance formula N/(n1·n2) + d²/(2(N − 2))", report.methods_text_en(out))
        # GEN: közölt SMD varianciája — nincs J-állítás
        gen = self.csv("study,yi,n1,n2\nA,0.5,20,22\nB,0.3,30,31\nC,0.8,15,14\n", "gen.csv")
        out, es, md = self.run_pipeline(gen, measure="GEN", gen_smd_vtype="METAN_HEDGES")
        self.assertNotIn("J = 1 − 3/(4N − 9)", report.methods_text_en(out))

    def test_R2_REP_13_rom_label(self):
        out, es, md = self.run_pipeline(NORMAND, measure="ROM")
        m = report.methods_text_en(out)
        self.assertIn("Effect sizes were expressed as the ratio of means (ROM) with 95%", m)
        self.assertNotIn("log ratio of means", m)
        self.assertIn("Analyses were performed on the log scale and results were back-transformed", m)

    def test_R2_REP_15_effect_size_warnings_reach_report(self):
        out, es, md = self.run_pipeline(NORMAND, measure="COHEN_D", smd_vtype="UB")
        msg = "COHEN_D: az UB variancia a korrigált (Hedges g) becslőhöz tartozik; LS-sel számoltunk."
        self.assertIn(msg, out["warnings"])
        sec = md.split("## Értelmezési figyelmeztetések")[1].split("\n## ")[0]
        self.assertIn(msg, sec)
        # a kizárt sorok üzenete nem kerül a figyelmeztetések közé (a riport külön listázza)
        p = self.csv("study,m1,sd1,n1,m2,sd2,n2\nA,12,3,30,10,2.5,30\nB,14,4,25,11,3,24\nC,11,,40,10.5,3.2,38\n")
        out, es, md = self.run_pipeline(p, measure="SMD")
        self.assertTrue(any("sor kimaradt" in w for w in out["effect_sizes"]["warnings"]))
        self.assertFalse(any("sor kimaradt" in w for w in out["warnings"]))


# ------------------------------------------------------------------ riportszöveg
class TestReportText(_Tmp):
    def test_R2_REP_03_all_studies_outliers(self):
        p = self.csv("study,yi,vi\nA,-1,0.01\nB,-1.1,0.01\nC,1,0.01\nD,1.1,0.01\n")
        out, es, md = self.run_pipeline(p, measure="GEN", model="fixed", outliers=True)
        self.assertEqual(out["sensitivity"]["outliers"].k_removed, 4)
        m = report.methods_text_en(out)
        self.assertNotIn("no study met this criterion", m)
        self.assertIn("all 4 studies met this criterion, so no refit without them was possible.", m)
        out, es, md = self.run_pipeline(BCG, measure="RR", outliers=True)
        self.assertIn("the model was refitted without the 2 flagged studies", report.methods_text_en(out))
        p = self.csv("study,yi,vi\nA,0.1,0.04\nB,0.2,0.05\nC,0.15,0.03\nD,0.12,0.02\n", "none.csv")
        out, es, md = self.run_pipeline(p, measure="GEN", outliers=True)
        self.assertIn("no study met this criterion.", report.methods_text_en(out))

    def test_R2_REP_14_subgroup_not_claimed_prespecified(self):
        out, es, md = self.run_pipeline(BCG, measure="RR", subgroup="allokáció")
        m = report.methods_text_en(out)
        self.assertNotIn("Pre-specified", m)
        self.assertIn("subgroup analyses by allokáció were performed", m)
        out, es, md = self.run_pipeline(BCG, measure="RR", subgroup="allokáció", subgroup_prespecified=True)
        self.assertIn("Pre-specified subgroup analyses by allokáció were performed", report.methods_text_en(out))

    def test_R2_REP_09_common_effect_tau2_not_shown(self):
        out, es, md = self.run_pipeline(BCG, measure="RR", model="fixed", subgroup="allokáció", cumulative="év",
                                        outliers=True)
        self.assertIn("| random | 7 | ", md)
        for line in md.splitlines():
            if line.startswith(("| random | 7 |", "| alternate | 2 |", "| systematic | 4 |")):
                self.assertEqual(line.split("|")[4].strip(), "–", line)
        cum = md.split("### Kumulatív metaanalízis")[1].split("\n\n")[1]
        for line in cum.splitlines()[2:]:
            self.assertEqual(line.split("|")[5].strip(), "–", line)
        self.assertRegex(md, r"Újraillesztés nélkülük \(k = 7\) \[95% CI\]: \S+ \[[^]]+\]; τ² = –;")
        svg = pipeline.make_plots(out, es)[0]
        self.assertNotIn("τ² = 0;", svg)
        self.assertEqual(svg.count("τ² = –; I² ="), 3)
        # véletlen hatású modellnél a τ² marad
        out, es, md = self.run_pipeline(BCG, measure="RR", subgroup="allokáció", cumulative="év")
        self.assertRegex(md, r"\| random \| 7 \| [^|]+ \| 0\.\d{4} \|")
        self.assertIn("τ² = 0.", pipeline.make_plots(out, es)[0])

    def test_R2_REP_02_rd_absolute_effect_vs_baseline(self):
        out, es, md = self.run_pipeline(BCG, measure="RD")
        ab = out["totals"]["absolute_per_1000"]
        self.assertTrue(ab.get("incompatible_with_baseline"))
        acr = ab["assumed_control_risk_per_1000"]
        for dif, ir in zip(ab["difference"], ab["intervention_risk"]):
            if ir is not None:
                assert_close(self, ir - acr, dif, 1e-9)
            else:
                self.assertFalse(0 <= acr + dif <= 1000)
        self.assertEqual(ab["intervention_risk"][:2], [None, None])
        self.assertTrue(any(w.startswith("Abszolút hatás (RD)") for w in out["warnings"]))
        self.assertIn("**az RD ezzel nem összeegyeztethető**", md)
        # összeférő RD: nincs jelzés, a beavatkozási kockázat = kontroll + RD
        p = self.csv("study,e1,n1,e2,n2\nA,10,100,20,100\nB,12,120,25,118\nC,8,90,15,92\nD,11,100,19,100\n", "rd.csv")
        out, es, md = self.run_pipeline(p, measure="RD")
        ab = out["totals"]["absolute_per_1000"]
        self.assertNotIn("incompatible_with_baseline", ab)
        for dif, ir in zip(ab["difference"], ab["intervention_risk"]):
            assert_close(self, ir - ab["assumed_control_risk_per_1000"], dif, 1e-9)
        self.assertFalse(any(w.startswith("Abszolút hatás (RD)") for w in out["warnings"]))


# ------------------------------------------------------------------ kimeneti fájlok
class TestOutputs(_Tmp):
    def test_R2_REP_10_no_plots_report_has_no_svg_refs(self):
        out, es, md = self.run_pipeline(BCG, measure="RR")
        self.assertIn("`forest.svg`", md)
        md = report.build_report(out, date="2026-10-04", plots=False)
        self.assertNotIn(".svg", md)
        self.assertIn("Lásd: `effect_sizes.csv` (ábrák nem készültek: --no-plots).", md)
        m = report.methods_text_en(out, plots=False)
        self.assertNotIn("funnel plots", m)
        self.assertIn("Small-study effects were explored with Egger's regression test", m)
        self.assertIn("contour-enhanced funnel plots", report.methods_text_en(out))

    def test_R2_ROB_11_stale_plot_files_removed(self):
        lines = ["study,yi,vi,year"] + ["S%d,0.%d,0.0%d,%d" % (i, i, i % 5 + 1, 2000 + i) for i in range(12)]
        p = self.csv("\n".join(lines) + "\n")
        od = os.path.join(self.tmp, "eredmeny")
        out, es, md = self.run_pipeline(p, measure="GEN")
        paths = pipeline.write_outputs(out, es, od, md)
        for f in pipeline.PLOT_FILES:
            self.assertIn(f, paths)
        # k = 2 → nincs doi.svg: a korábbi doi.svg törlődik
        rows, meta = tableio.read_table(p)
        out, es = pipeline.run(rows[:2], {"measure": "GEN"}, meta)
        pipeline.write_outputs(out, es, od, report.build_report(out))
        self.assertFalse(os.path.exists(os.path.join(od, "doi.svg")))
        self.assertTrue(os.path.exists(os.path.join(od, "forest.svg")))
        # --no-plots → egyetlen régi ábrafájl sem marad
        out, es, md = self.run_pipeline(p, measure="GEN")
        pipeline.write_outputs(out, es, od, md)
        pipeline.write_outputs(out, es, od, md, plots=False)
        self.assertEqual(sorted(os.listdir(od)), ["effect_sizes.csv", "report.md", "results.json"])
        # k = 0 → sem
        pipeline.write_outputs(out, es, od, md)
        out, es = pipeline.run([], {"measure": "GEN"}, meta)
        pipeline.write_outputs(out, es, od, report.build_report(out))
        self.assertEqual(sorted(os.listdir(od)), ["effect_sizes.csv", "report.md", "results.json"])

    def test_R2_ROB_10_control_characters_in_labels(self):
        p = self.csv("study,yi,vi\nSmith\x0b2001,0.5,0.04\nJones\x1f,0.2,0.05\nKim\x01,0.3,0.03\nLee,0.4,0.02\n")
        od = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", p, "--measure", "GEN", "--out", od,
                               "--title", "Cím\x0cpróba", "--subgroup", "study")
        self.assertEqual(code, 0, so + se)
        for f in ("forest.svg", "funnel.svg", "doi.svg"):
            xml.dom.minidom.parse(os.path.join(od, f))
        with open(os.path.join(od, "forest.svg"), encoding="utf-8") as fh:
            self.assertIn(">Smith 2001<", fh.read())
        self.assertEqual(P.escape("a\x01<b>&\x0bc\td"), "a &lt;b&gt;&amp; c\td")


# ------------------------------------------------------------------ alcsoport-szintek
class TestLevels(_Tmp):
    def test_R2_ROB_15_numtext_subgroup_levels_stay_distinct(self):
        p = self.csv("study,m1,sd1,n1,m2,sd2,n2,kod\nA,10,2,20,8,2,20,01\nB,11,2,25,8,2,20,1\nC,12,3,25,9,3,25,01\n"
                     "D,10,2,40,9,2,40,1\nE,10,2,40,9,2,40,2\nF,13,2,40,9,2,40,2\n")
        out, es, md = self.run_pipeline(p, measure="MD", subgroup="kod")
        self.assertEqual([(g.group, g.k) for g in out["subgroups"].groups], [("01", 2), ("1", 2), ("2", 2)])
        self.assertEqual([s["title"] for s in pipeline.make_plots(out, es)[2]["sections"]], ["01", "1", "2"])
        # ugyanaz kanonikus (szöveges) oszlopnévvel
        with open(p, encoding="utf-8") as fh:
            canon = self.csv(fh.read().replace(",kod\n", ",subgroup\n"), "canon.csv")
        out2, es2, md2 = self.run_pipeline(canon, measure="MD", subgroup="subgroup")
        self.assertEqual([(g.group, g.k) for g in out2["subgroups"].groups], [("01", 2), ("1", 2), ("2", 2)])
        # a numerikus moderátor állandósága továbbra is számként dől el ('1' = '1.0')
        p = self.csv("study,yi,vi,dose\nA,0.1,0.04,1\nB,0.3,0.05,1.0\nC,0.2,0.03,1\nD,0.4,0.02,1.0\n", "dose.csv")
        out, es, md = self.run_pipeline(p, measure="GEN", moderators=["dose"])
        self.assertEqual(out.get("metaregression_dropped"), ["dose"])
        self.assertIsNone(out.get("metaregression"))


# ------------------------------------------------------------------ tengelyek
class TestAxisTicks(_Tmp):
    def test_R2_REP_11_no_negative_zero_and_enough_ticks(self):
        t = P._nice_ticks(-0.17, 0.05)
        self.assertEqual(t, [-0.15, -0.1, -0.05, 0.0, 0.05])
        self.assertNotIn("-0", ["%g" % x for x in t])
        t = P._nice_ticks(-0.178594, 0.072736)
        self.assertGreaterEqual(len(t), 4)
        self.assertIn(0.0, t)
        self.assertNotIn("-0", ["%g" % x for x in t])
        self.assertEqual(P._nice_ticks(0, 1.2, 6), [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2])
        out, es, md = self.run_pipeline(BCG, measure="RD")
        f_svg, fu_svg, data = pipeline.make_plots(out, es)
        labs = [lab for _, lab in _axis_labels(f_svg)]
        self.assertGreaterEqual(len(labs), 4, labs)
        self.assertIn("0", labs)
        for svg in (f_svg, fu_svg):
            self.assertNotIn(">-0<", svg)

    def test_R2_REP_12_null_tick_always_labelled(self):
        out, es, md = self.run_pipeline(BCG, measure="OR", subgroup="allokáció")
        labs = [lab for _, lab in _axis_labels(pipeline.make_plots(out, es)[0])]
        self.assertIn("1", labs)
        for lo, hi in ((0.05, 30), (0.02, 20)):
            ax = P._Axis(math.log(lo), math.log(hi), 300, 530, True, "OR")
            ticks = ax.ticks()
            self.assertIn("1", [lab for _, lab in ticks], (lo, hi))
            xs = [ax.x(pos) for pos, _ in ticks]
            self.assertEqual(xs, sorted(xs))
            self.assertTrue(all(b - a >= 30 for a, b in zip(xs, xs[1:])), xs)
        # MD: 0 megmarad akkor is, ha sűrűk a tickek
        ax = P._Axis(-3.0, 1.0, 300, 400, False, "MD")
        self.assertIn("0", [lab for _, lab in ax.ticks()])


if __name__ == "__main__":
    unittest.main()
