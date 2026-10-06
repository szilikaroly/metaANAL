# -*- coding: utf-8 -*-
"""Bekötés (Z): CLI-kapcsolók, pipeline/riport, Doi-plot, esetfuttató (új hívások, expr,
szöveges elvárások, PFT MetaXL-visszatranszformálás, float32 bemenet), aktivált forrás-esetek.

Referenciák: a forrás-esetek nyomtatott értékei (tests/reference/source_examples), és az X/Y
ágens R-rel (metafor 4.4, dmetar-forrás) ellenőrzött teljes pontosságú értékei."""
import copy
import io
import json
import math
import os
import shutil
import tempfile
import unittest
import xml.dom.minidom
from contextlib import redirect_stdout, redirect_stderr

from _helpers import ROOT, assert_close
import source_cases as SC
from metaelemzes import bias as B
from metaelemzes import cli
from metaelemzes import effect_sizes as E
from metaelemzes import models as M
from metaelemzes import moderators as MO
from metaelemzes import pipeline, report, tableio
from metaelemzes import sensitivity as SE

EXAMPLES = os.path.join(ROOT, "peldak")
CASES = {c["case_id"]: c for c in SC.load_cases()}
K22 = CASES["jslhr_tutorial__egger_k22"]["rows"]          # JSLHR tutorial, k = 22 (g, SE)


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = cli.main(list(args))
    return code, buf.getvalue(), err.getvalue()


def _case(base_id, **changes):
    c = copy.deepcopy(CASES[base_id])
    c.update(changes)
    c["status"] = "active"
    return c


def _ok(case):
    res = SC.check_case(case)
    bad = [r for r in res if not r[4]]
    return res, bad


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

    def k22_csv(self):
        lines = ["study,yi,sei"] + ["%s,%r,%r" % (r["study"], r["yi"], r["sei"]) for r in K22]
        return self.csv("\n".join(lines) + "\n", "k22.csv")


# ============================================================ 1. esetfuttató
class TestRunnerExpr(unittest.TestCase):
    def setUp(self):
        self.res = M.MetaResult(QE=77.97000087, QE_df=48, w=[1.0, 2.0, 5.0],
                                groups={"High income": M.MetaResult(estimate=0.5, se=0.1)})

    def test_arithmetic_names_and_paths(self):
        assert_close(self, SC.eval_expr("100*(QE-QE_df)/QE", self.res), 38.43786140621688, 1e-10)
        self.assertEqual(SC.eval_expr("p('groups.High income.estimate')/p('groups.High income.se')", self.res), 5.0)
        self.assertEqual(SC.eval_expr("a/b", self.res, {"a": "w.2", "b": "w.1"}), 2.5)
        self.assertEqual(SC.eval_expr("sum(w)/len(w)", self.res), 8.0 / 3)
        self.assertEqual(SC.eval_expr("max(w) - min(w)", self.res), 4.0)
        assert_close(self, SC.eval_expr("sqrt(QE_df)*exp(0)+log(e)-tanh(0)+expit(0)", self.res),
                     math.sqrt(48) + 1.5, 1e-12)
        self.assertAlmostEqual(SC.eval_expr("-2**2 % 3 + pi", self.res), (-(2 ** 2)) % 3 + math.pi)

    def test_rejects_unsafe_nodes(self):
        for bad in ("__import__('os')", "QE.__class__", "[x for x in w]", "lambda: 1", "open('x')",
                    "p(QE)", "QE if QE else 1", "'a' + 'b'", "sqrt(x=1)"):
            with self.assertRaises((ValueError, SyntaxError, KeyError, AttributeError), msg=bad):
                SC.eval_expr(bad, self.res)

    def test_expectation_with_expr_and_string_and_bool(self):
        ex = {"path": "I2_res_stata", "expr": "100*(QE-QE_df)/QE", "value": 38.44, "tol": 0.005}
        lab, want, got, tol, ok, msg = SC.check_expectation(self.res, ex)
        self.assertTrue(ok, msg)
        self.assertEqual(lab, "I2_res_stata")
        res = M.MetaResult(category="kisebb aszimmetria", flag=True)
        self.assertTrue(SC.check_expectation(res, {"path": "category", "value": "kisebb aszimmetria"})[4])
        self.assertFalse(SC.check_expectation(res, {"path": "category", "value": "kisebb  aszimmetria"})[4])
        self.assertTrue(SC.check_expectation(res, {"path": "flag", "value": True})[4])
        self.assertFalse(SC.check_expectation(res, {"path": "flag", "value": False})[4])
        r = SC.check_expectation(res, {"path": "category", "value": "x", "transform": "exp"})
        self.assertFalse(r[4])
        self.assertIn("transform", r[5])


class TestRunnerStringExpectations(unittest.TestCase):
    """A Doi-plot LFK-kategóriája szövegként (pontos egyezés), a forrás nyomtatott kategóriáival."""

    def test_lfk_categories_of_source_examples(self):
        for cid, cat in (("khan_ch05__heartburn_doi_plot_lfk", "kisebb aszimmetria"),          # 'Minor asymmetry'
                         ("khan_ch03_04__heartburn_lfk", "jelentős aszimmetria"),              # 'major asymmetry'
                         ("khan_ch08__optime_cohen_lfk_metaxl_var", "nincs aszimmetria"),      # 'No asymmetry'
                         ("khan_ch08__optime_hedges_lfk_metaxl_var", "nincs aszimmetria")):
            c = _case(cid, call="doi", expected=[{"path": "category", "value": cat}])
            res, bad = _ok(c)
            self.assertEqual(bad, [], cid)


class TestRunnerNewCalls(unittest.TestCase):
    def test_outlier_screen_reproduces_source_k19_refit(self):
        """dmetar::find.outliers a k = 22 adaton → a forrás k = 19-es újraillesztése (REML, z)."""
        k19 = CASES["jslhr_tutorial__k19_outliers_removed_re_reml"]
        dropped = sorted(set(r["study"] for r in K22) - set(r["study"] for r in k19["rows"]))
        exp = [{"path": "k_removed", "value": 3, "tol": 0},
               {"path": "flagged", "value": None}]
        exp = [e for e in exp if e["value"] is not None]
        exp += [dict(e, path="refit." + e["path"]) for e in k19["expected"]]
        # R rma(method="REML", test="z") a 19 vizsgálaton (X ágens, 1e-9)
        exp += [{"path": "refit.estimate", "value": 0.174555148556, "tol": 1e-9},
                {"path": "refit.se", "value": 0.070541704564, "tol": 1e-9},
                {"path": "refit.ci_lower", "value": 0.036295948202, "tol": 1e-9},
                {"path": "refit.ci_upper", "value": 0.312814348909, "tol": 1e-9},
                {"path": "refit.Q", "value": 17.352997883235, "tol": 1e-8}]
        c = _case("jslhr_tutorial__egger_k22", call="outlier_screen",
                  call_args={"model": "random", "tau2_method": "REML", "ci_method": "z"}, expected=exp)
        res, bad = _ok(c)
        self.assertEqual(bad, [])
        self.assertEqual(sorted(SC.execute(c).flagged), dropped)

    def test_power_dmetar_example(self):
        c = {"case_id": "z_power", "call": "power", "rows": [],
             "call_args": {"k": 18, "effect": 0.7, "n1": 15, "n2": 15, "heterogeneity": "moderate"},
             "expected": [{"path": "power", "value": 0.999983334487471, "tol": 1e-12},
                          {"path": "lambda_", "value": 6.10939, "tol": 5e-6},
                          {"path": "v_study", "value": 0.1415, "tol": 1e-4}]}
        res, bad = _ok(c)
        self.assertEqual(bad, [])
        c2 = {"case_id": "z_k", "call": "studies_needed", "rows": [],
              "call_args": {"target_power": 0.8, "effect": 0.3, "n1": 25, "n2": 25, "heterogeneity": "high"},
              "expected": [{"path": "k", "value": 15, "tol": 0}]}
        self.assertEqual(_ok(c2)[1], [])

    def test_prisma_check_and_composer(self):
        c = _case("khan_ch01_02__prisma2009_flow_d1_vs_d2_gastrectomy", call="prisma_check")
        c["expected"] = c["expected"] + [{"path": "ok", "value": True}]
        self.assertEqual(_ok(c)[1], [])
        flow = {"identified_databases": 200, "identified_registers": 10, "dedup_removed": 40,
                "removed_before_screening_n": 5, "screened": 165, "excluded_screening": 130,
                "sought_for_retrieval": 35, "not_retrieved": 3, "assessed_eligibility": 32,
                "excluded_eligibility": 27, "excluded_eligibility_reasons": {"nem RCT": 20, "rossz kimenet": 7},
                "included": 5, "undecided": 0}
        c2 = {"case_id": "z_comp", "call": "prisma_check", "flow": flow, "call_args": {"composer": True},
              "expected": [{"path": "ok", "value": True}, {"path": "included_reports", "value": 5, "tol": 0},
                           {"path": "template", "value": "PRISMA2020"}]}
        self.assertEqual(_ok(c2)[1], [])

    def test_ivhet_tau2_fixed_khan_ch10(self):
        """Khan Ch10: IVhet a könyv kerekített τ² = 0.008633-ával és z = 1.96-tal (level = 2Φ(1.96) − 1)
        a nyomtatott SE 0.046716-ot és LL* 0.451061-et adja fél egység pontossággal."""
        c = _case("khan_ch10__ivhet_fisher_z",
                  call_args={"model": "ivhet", "tau2_fixed": 0.008633, "level": 0.950004209703559},
                  expected=[{"path": "se", "value": 0.046716, "tol": 5e-7},
                            {"path": "ci_lower", "value": 0.451061, "tol": 5e-7},
                            {"path": "tau2_method", "value": "rögzített"}])
        self.assertEqual(_ok(c)[1], [])

    def test_cumulative_call(self):
        c = _case("jslhr_tutorial__egger_k22", call="cumulative",
                  call_args={"order_col": "study", "model": "fixed"}, expected=[])
        rows = SC.execute(c)
        self.assertEqual(len(rows), 22)
        es = SC._es(c)
        full = M.meta_analysis(es.yi, es.vi, "fixed")
        assert_close(self, rows[-1]["estimate"], full.estimate, 1e-12)


class TestRunnerTransformsAndStorage(unittest.TestCase):
    def test_pft_iv_equals_pft_hm_with_variance(self):
        c = CASES["khan_ch06_07__schizophrenia_metaxl_random_backtransformed"]
        res = SC.execute(copy.deepcopy(c))
        a = SC.transform(res.estimate, "pft_iv", c, res, "estimate")
        b = SC.transform(res.estimate, "pft_hm", c, res, "estimate", "variance")
        self.assertEqual(a, b)
        self.assertEqual(a, E.back_transform("PFT", res.estimate, pft_n="variance", se=res.se))
        hm = SC.transform(res.estimate, "pft_hm", c)
        self.assertNotEqual(round(hm, 6), round(a, 6))
        with self.assertRaises(ValueError):
            SC.transform(res.estimate, "pft_hm", c, res, "estimate", "median")
        with self.assertRaises(ValueError):
            SC.transform(res.estimate, "pft_iv", c)

    def test_input_storage_float32(self):
        c = CASES["khan_ch11_13__ihdchol_wls_metareg_continuous_robust_inference"]
        self.assertEqual(c["input_storage"], {"chol_reduc": "float32"})
        rows = SC._rows(c)
        self.assertNotEqual(rows[0]["chol_reduc"], c["rows"][0]["chol_reduc"])
        self.assertAlmostEqual(rows[0]["chol_reduc"], c["rows"][0]["chol_reduc"], places=7)
        self.assertEqual(c["rows"][0]["chol_reduc"], 0.55)        # az eredeti sor változatlan
        # double pontossággal a CI alsó határa 6.8e-8-cal eltér a nyomtatott .5205299-től (tol 5e-8)
        c2 = copy.deepcopy(c)
        c2.pop("input_storage")
        bad = [r for r in SC.check_case(c2) if not r[4]]
        self.assertEqual([r[0] for r in bad], ["robust.coefficients.1.ci_lower"])
        with self.assertRaises(ValueError):
            SC._rows(dict(c, input_storage={"chol_reduc": "float16"}))


class TestDerivedQuantitiesFromSources(unittest.TestCase):
    def test_borenstein_ch13_weight_ratios(self):
        """Borenstein Ch13 p. 79: Donat/Peck súlyarány FE-ben 'about five times' (4.74), RE-ben
        'only 1.8 times' (1.823) — a korábban kifejezhetetlen származtatott mennyiség."""
        fe = _case("borenstein_book__ch13_smd_fixed_effect", expected=[
            {"path": "Donat/Peck", "expr": "p('weights_pct.3')/p('weights_pct.2')", "value": 5, "tol": 0.5},
            {"path": "Donat/Peck", "expr": "w[3]/w[2]" if False else "a/b", "vars": {"a": "weights_raw.3",
                                                                                    "b": "weights_raw.2"},
             "value": 4.744, "tol": 0.0005}])
        self.assertEqual(_ok(fe)[1], [])
        re_ = _case("borenstein_book__ch13_smd_random_dl", expected=[
            {"path": "Donat/Peck", "expr": "p('weights_pct.3')/p('weights_pct.2')", "value": 1.8, "tol": 0.05}])
        self.assertEqual(_ok(re_)[1], [])

    def test_simplypsych_raw_inverse_variance_weight(self):
        c = _case("simplypsych_guide__fe_inverse_variance_weight", expected=[
            {"path": "weights_raw.0", "value": 25, "tol": 1e-12}, {"path": "sum_weights", "value": 25, "tol": 1e-12}])
        self.assertEqual(_ok(c)[1], [])

    def test_stata_modified_h2_and_begg_variants(self):
        c = _case("khan_ch11_13__ihdchol_ivhet_pooled_or", expected=[
            {"path": "heterogeneity.H2_M", "value": 0.840, "tol": 0.0005},
            {"path": "sum_weights", "value": 886.07, "tol": 0.005}])
        self.assertEqual(_ok(c)[1], [])
        mg = CASES["khan_ch11_13__magnesium_begg_normal_approx"]
        for args, p in (({"method": "normal"}, 0.506225852327387),
                        ({"method": "normal", "continuity": True}, 0.5288645203641904),
                        ({}, 0.5339737003888001)):
            c = _case(mg["case_id"], call_args=args, expected=[{"path": "p", "value": p, "tol": 1e-12}])
            self.assertEqual(_ok(c)[1], [], args)


class TestActivatedCases(unittest.TestCase):
    ACTIVATED = (
        "jslhr_tutorial__k19_h_ci_truncated_centre", "jslhr_tutorial__egger_k22_normal_ci",
        "jslhr_tutorial__trimfill_k22_meta_default", "khan_ch01_02__prisma2009_flow_d1_vs_d2_gastrectomy",
        "simplypsych_guide__prisma2009_flow_bialek2023", "khan_ch03_04__heartburn_subgroup_weight_share",
        "khan_ch05__heartburn_subgroup_weight_share", "khan_ch06_07__schizophrenia_metaxl_fixed_backtransformed",
        "khan_ch06_07__schizophrenia_metaxl_random_backtransformed",
        "khan_ch06_07__schizophrenia_metaxl_ivhet_backtransformed",
        "khan_ch06_07__schizophrenia_metaxl_subgroup_backtransformed_and_share",
        "khan_ch08__ginkgo_md_pooled_equal_var", "khan_ch08__cholesterol_paired_mean_difference",
        "khan_ch08__quant_ability_study1_variance_from_g", "khan_ch08__open_education_per_study_variance",
        "khan_ch08__optime_cohen_fe_native_variance", "khan_ch08__optime_hedges_fe_native_variance",
        "khan_ch08__optime_glass_per_study_es", "khan_ch11_13__ihdchol_wls_intercept_robust_inference",
        "khan_ch11_13__ihdchol_wls_metareg_continuous_robust_inference",
        "khan_ch11_13__ihdchol_wls_metareg_categorical_robust_inference",
        "khan_ch11_13__magnesium_begg_normal_approx", "cheung_guide__metareg_reml_kh_stata__i2_res")

    def test_activated_cases_are_active_and_pass(self):
        for cid in self.ACTIVATED:
            c = CASES[cid]
            self.assertEqual(c.get("status", "active"), "active", cid)
            self.assertNotIn("gap_reason", c, cid)
            self.assertTrue(c.get("activation_note"), cid)
            self.assertEqual([r for r in SC.check_case(c) if not r[4]], [], cid)

    def test_remaining_gaps_are_documented(self):
        gaps = [c for c in CASES.values() if c.get("status") == "known_gap"]
        self.assertEqual(len(gaps), 27)
        calls = set(c["call"] for c in gaps)
        self.assertIn("dose_response", calls)          # REMR: nincs implementálva
        for c in gaps:
            self.assertTrue(c.get("gap_reason"), c["case_id"])


# ============================================================ 2. pipeline + riport
class TestPipelineDefaultsAndOptions(_Tmp):
    BCG = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")

    def test_tau2_default_resolved_per_model(self):
        self.assertIsNone(pipeline.DEFAULTS["tau2"])
        out, es, md = self.run_pipeline(self.BCG, measure="RR", model="ivhet", subgroup="allokáció",
                                        cumulative="év", outliers=True)
        self.assertEqual(out["primary"].tau2_method, "DL")
        self.assertEqual(out["random"].tau2_method, "REML")           # érzékenységi véletlen hatású modell
        self.assertEqual(out["bias"]["trimfill"].adjusted.tau2_method, "DL")
        self.assertEqual(set(g.tau2_method for g in out["subgroups"].groups if g.k > 1), {"DL"})
        self.assertEqual(out["sensitivity"]["outliers"].full.tau2_method, "DL")
        loo = SE.leave_one_out(es.yi, es.vi, es.labels, "ivhet", "DL", None, 0.95)
        for a, b in zip(out["sensitivity"]["leave_one_out"], loo):
            assert_close(self, a["ci_lower"], b["ci_lower"], 1e-12)
        self.assertFalse(any("eltér a publikált IVhet" in w for w in out["warnings"]))
        self.assertIn("IVhet (Doi 2015; DL τ²)", md)
        out, es, md = self.run_pipeline(self.BCG, measure="RR")
        self.assertEqual(out["primary"].tau2_method, "REML")

    def test_explicit_tau2_for_ivhet(self):
        out, es, md = self.run_pipeline(self.BCG, measure="RR", model="ivhet", tau2="pm")
        self.assertEqual(out["options"]["tau2"], "PM")
        self.assertEqual(out["primary"].tau2_method, "PM")
        self.assertTrue(any("eltér a publikált IVhet" in w for w in out["warnings"]))
        self.assertIn("IVhet (Doi 2015; PM τ²)", md)
        self.assertIn("inflated by the Paule–Mandel estimate of τ² (the published IVhet model uses the "
                      "DerSimonian–Laird estimator)", report.methods_text_en(out))

    def test_invalid_options_rejected(self):
        rows, meta = tableio.read_table(self.BCG)
        for bad in ({"pft_backtransform": "median"}, {"trimfill_trim_model": "ivhet"}, {"tau2": "XX"},
                    {"egger_ci_dist": "z"}, {"begg_method": "kendall"}, {"h_centre": "centre"},
                    {"smd_vtype": "METAXL"}, {"md_vtype": "welch"}, {"glass_vtype": "X"}, {"model": "bayes"}):
            with self.assertRaises(ValueError, msg=bad):
                pipeline.run(rows, dict(bad, measure="RR"), meta)


class TestPipelineBias(_Tmp):
    BCG = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")

    def test_binary_harbord_peters_lfk(self):
        out, es, md = self.run_pipeline(self.BCG, measure="OR")
        b = out["bias"]
        cols = [[r[c] for r in es.rows] for c in ("e1", "n1", "e2", "n2")]
        hb, pt = B.harbord_test(*cols), B.peters_test(*cols)
        assert_close(self, b["harbord"].p, hb.p, 1e-12)
        assert_close(self, b["peters"].p, pt.p, 1e-12)
        assert_close(self, b["lfk"].lfk, B.doi_plot_data(es.yi, es.vi).lfk, 1e-12)
        self.assertIn("Sterne et al. 2011", b["binary_note"])
        self.assertIn("Harbord-teszt", md)
        self.assertIn("Peters-teszt", md)
        self.assertIn("Egger-teszt (bináris kimenetnél csak tájékoztató)", md)
        self.assertIn("LFK-index (heurisztikus, érzékenységi jellegű", md)
        self.assertIn(b["lfk"].category, md)
        m = report.methods_text_en(out)
        self.assertIn("Harbord's score-based test, Peters' test", m)
        self.assertIn("prone to false-positive results with odds ratios (Sterne et al. 2011)", m)
        self.assertIn("the Doi plot with the LFK index", m)
        out, es, md = self.run_pipeline(self.BCG, measure="RR")
        self.assertIn("akkor is, ha az elemzés RR skálán történt", out["bias"]["binary_note"])

    def test_no_harbord_for_continuous_and_independent_failures(self):
        p = self.csv("study,yi,vi\nA,0.2,0.04\nB,0.2,0.05\nC,0.2,0.09\nD,0.2,0.02\n")
        out, es, md = self.run_pipeline(p, measure="GEN")
        b = out["bias"]
        self.assertNotIn("harbord", b)
        self.assertNotIn("lfk", b)                          # azonos hatásméretek → az LFK nem számolható
        self.assertTrue(any(w.startswith("Torzítás-elemzés (LFK-index)") for w in out["warnings"]))
        self.assertIn("egger", b)                           # a többi teszt ettől még lefut
        # azonos hatásoknál a Kendall-τ nem definiált (metafor ranktest: NA) — metafor_fuzz:DT-2
        self.assertNotIn("begg", b)
        self.assertTrue(any(w.startswith("Torzítás-elemzés (Begg)") for w in out["warnings"]))
        # azonos hatásoknál a trim-and-fill nem konvergál (a metafor 4.4 trimfill is hibát ad):
        # külön figyelmeztetés, és az utána következő LFK is külön próbálkozik
        self.assertTrue(any(w.startswith("Torzítás-elemzés (trim-and-fill)") for w in out["warnings"]))

    def test_options_passed_to_bias_tests(self):
        p = self.k22_csv()
        out, es, md = self.run_pipeline(p, measure="GEN", trimfill_trim_model="fixed", egger_ci_dist="norm",
                                        begg_method="normal", begg_continuity=True, tau2="REML", ci="z")
        b = out["bias"]
        assert_close(self, b["egger"].ci_lower, 0.270842689912, 1e-9)     # dmetar::eggers.test
        assert_close(self, b["egger"].ci_upper, 3.642593873302, 1e-9)
        tf = B.trim_and_fill(es.yi, es.vi, es.labels, "random", None, "L0", ci_method="z", trim_model="fixed")
        self.assertEqual(b["trimfill"].k0, tf.k0)
        self.assertEqual(b["trimfill"].trim_model, "fixed")
        assert_close(self, b["trimfill"].adjusted.estimate, tf.adjusted.estimate, 1e-12)
        bg = B.begg_test(es.yi, es.vi, "normal", True)
        assert_close(self, b["begg"].p, bg.p, 1e-14)
        self.assertIn("vágás a közös hatású modellel", md)
        self.assertIn("z-kvantilis", md)
        m = report.methods_text_en(out)
        self.assertIn("intercept CI with the normal quantile, as in dmetar", m)
        self.assertIn("normal approximation with continuity correction", m)
        self.assertIn("trimming based on the common-effect model", m)


class TestPipelineSensitivityAndHeterogeneity(_Tmp):
    def test_outlier_screen_in_pipeline_and_report(self):
        out, es, md = self.run_pipeline(self.k22_csv(), measure="GEN", tau2="REML", ci="z", outliers=True)
        ol = out["sensitivity"]["outliers"]
        self.assertEqual(sorted(ol.flagged), ["Davis_2015(1)", "Davis_2015(2)", "Shehata_2013"])
        assert_close(self, ol.refit.estimate, 0.174555148556, 1e-9)
        self.assertIn("### Kiugró vizsgálatok szűrése", md)
        self.assertIn("Kiugró vizsgálat(ok) (3)", md)
        self.assertIn("Újraillesztés nélkülük (k = 19)", md)
        self.assertIn("refitted without the 3 flagged studies", report.methods_text_en(out))
        out, es, md = self.run_pipeline(self.k22_csv(), measure="GEN")
        self.assertNotIn("outliers", out["sensitivity"])
        self.assertNotIn("find.outliers", report.methods_text_en(out))

    def test_h_and_modified_h2_rows_and_centre(self):
        p = self.k22_csv()
        rows, meta = tableio.read_table(p)
        # a k = 19 adaton Q < df: itt tér el a két középpont-konvenció
        k19 = set(r["study"] for r in CASES["jslhr_tutorial__k19_outliers_removed_re_reml"]["rows"])
        rows = [r for r in rows if r["study"] in k19]
        out, es = pipeline.run(rows, {"measure": "GEN", "tau2": "REML", "ci": "z"}, meta)
        h = out["primary"].heterogeneity
        assert_close(self, h["H_ci_HT"][1], 1.399251075126, 1e-9)        # meta:::calcH
        self.assertEqual(h["H"], 1.0)
        md = report.build_report(out)
        self.assertIn("| H (Q-alapú) [95% CI, Higgins–Thompson] ; módosított H² | 1.00 [1.00; 1.40] ; 0.000 |", md)
        self.assertIn("centred on ln max(1, H) as in the R package meta", report.methods_text_en(out))
        out2, es = pipeline.run(rows, {"measure": "GEN", "tau2": "REML", "ci": "z", "h_centre": "untruncated"}, meta)
        h2 = out2["primary"].heterogeneity
        self.assertLess(h2["H_ci_HT"][1], h["H_ci_HT"][1])
        self.assertIn("középpont ½·ln(Q/df)", report.build_report(out2))
        self.assertIn("½·ln(Q/df) (untruncated", report.methods_text_en(out2))

    def test_robust_metaregression(self):
        bcg = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")
        out, es, md = self.run_pipeline(bcg, measure="RR", moderators=["szélesség"], metareg_robust=True)
        mr = out["metaregression"]
        self.assertIsNotNone(mr.robust)
        x, names = MO.design_matrix([{"szélesség": r["latitude"] if "latitude" in r else r.get("szélesség")}
                                     for r in es.rows], ["szélesség"])
        direct = MO.meta_regression(es.yi, es.vi, x, names, "REML", "knha", 0.95, robust=True)
        assert_close(self, mr.robust["coefficients"][1]["se"], direct.robust["coefficients"][1]["se"], 1e-12)
        self.assertIn("**Robusztus (HC1 szendvics) standard hibák**", md)
        self.assertIn("HC1 sandwich", report.methods_text_en(out))
        out, es, md = self.run_pipeline(bcg, measure="RR", moderators=["szélesség"])
        self.assertIsNone(out["metaregression"].robust)
        self.assertNotIn("Robusztus (HC1", md)

    def test_subgroup_weight_share_table(self):
        bcg = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")
        out, es, md = self.run_pipeline(bcg, measure="RR", subgroup="allokáció")
        shares = [g.weight_share_pct for g in out["subgroups"].groups]
        assert_close(self, sum(shares), 100.0, 1e-9)
        self.assertIn("| Alcsoport | Súlyrészesedés a teljes modellben² | Nyers súlyösszeg |", md)
        self.assertIn("| random | %.1f%% |" % shares[0], md)

    def test_level_is_honoured_in_new_sections(self):
        bcg = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")
        out, es, md = self.run_pipeline(bcg, measure="OR", level=0.9, outliers=True, moderators=["szélesség"],
                                        metareg_robust=True, subgroup="allokáció")
        head = md.split("## Methods")[0]
        self.assertNotIn("[95%", head)               # (egy alcsoport I²-e lehet 95% — az nem CI-felirat)
        self.assertNotIn("95%-os", head)
        self.assertNotIn("95% CI", head)
        self.assertIn("90%-os CI-je", md)
        self.assertEqual(out["bias"]["egger"].level, 0.9)


class TestPftVarianceBackTransform(_Tmp):
    CSV = ("study,x,n,grp,year\nA,10,20,a,2001\nB,30,40,a,2002\nC,5,25,b,2003\nD,40,60,b,2004\n"
           "E,12,15,a,2005\nF,20,50,b,2006\n")

    def test_every_pooled_value_uses_its_own_se(self):
        p = self.csv(self.CSV)
        out, es, md = self.run_pipeline(p, measure="PFT", pft_backtransform="variance", subgroup="grp",
                                        cumulative="year")
        pr = out["primary"]
        bt = out["back_transformed"]
        want = [E.back_transform("PFT", v, pft_n="variance", se=pr.se) for v in (pr.estimate, pr.ci_lower, pr.ci_upper)]
        for a, b in zip(bt["estimate_ci"], want):
            assert_close(self, a, b, 1e-14)
        assert_close(self, bt["pft_m"], 1 / (4 * pr.se ** 2), 1e-12)
        nh = E.harmonic_mean(es.ni)
        assert_close(self, bt["pi"][0], E.back_transform("PFT", pr.pi_lower, nh), 1e-14)   # PI: harmonikus
        for g in out["subgroups"].groups:
            assert_close(self, g.display[1], E.back_transform("PFT", g.ci_lower, pft_n="variance", se=g.se), 1e-14)
        # a kumulatív sorok SE-je a CI-ből = a tényleges illesztés SE-je
        cum = out["sensitivity"]["cumulative"]
        order = SE.cumulative_order([r["year"] for r in es.rows])
        for n in (1, 3, 6):
            idx = order[:n]
            r = M.meta_analysis([es.yi[i] for i in idx], [es.vi[i] for i in idx],
                                "fixed" if n == 1 else "random", None, "z" if n == 1 else "hksj")
            assert_close(self, cum[n - 1]["se"], r.se, 1e-10)
        fx = out["fixed"]
        self.assertIn("közös hatású modell [95% CI]: " + report._tri(
            [E.back_transform("PFT", v, pft_n="variance", se=fx.se) for v in (fx.estimate, fx.ci_lower, fx.ci_upper)]), md)
        self.assertIn("MetaXL-konvenció", md)
        self.assertIn("m = 1/Var(t) of that estimate (MetaXL convention", report.methods_text_en(out))
        _, _, data = pipeline.make_plots(out, es)
        self.assertEqual(data["summaries"][0]["display"], bt["estimate_ci"])

    def test_default_is_unchanged(self):
        p = self.csv(self.CSV)
        out, es, md = self.run_pipeline(p, measure="PFT")
        pr = out["primary"]
        nh = E.harmonic_mean(es.ni)
        assert_close(self, out["back_transformed"]["estimate_ci"][0], E.back_transform("PFT", pr.estimate, nh), 1e-14)
        self.assertIsNone(out["back_transformed"]["pft_m"])


class TestMethodsEffectSizeConventions(_Tmp):
    TWO_ARM = ("study,m1,sd1,n1,m2,sd2,n2\nA,12,3,30,10,2.5,30\nB,14,4,25,11,3,24\nC,11,3.5,40,10.5,3.2,38\n"
               "D,15,4.2,22,12,3.9,20\n")

    def test_sentences(self):
        p = self.csv(self.TWO_ARM)
        cases = [({"measure": "SMD", "smd_vtype": "METAN_HEDGES"}, "Stata metan / MetaXL variance formula N/(n1·n2) + g²"),
                 ({"measure": "COHEN_D", "smd_vtype": "METAN_COHEN"}, "d²/(2(N − 2))"),
                 ({"measure": "SMD"}, "exact small-sample correction factor and the large-sample variance formula"),
                 ({"measure": "MD", "md_vtype": "pooled"}, "assuming equal variances in the two groups"),
                 ({"measure": "MD"}, "without assuming equal variances"),
                 ({"measure": "SMD_GLASS", "glass_vtype": "UB"}, "Glass's Δ was computed with the unbiased variance"),
                 ({"measure": "SMD_GLASS"}, "Glass's Δ was computed with the Stata metan / MetaXL variance")]
        for opts, phrase in cases:
            rows, meta = tableio.read_table(p)
            out, es = pipeline.run(rows, opts, meta)
            self.assertIn(phrase, report.methods_text_en(out), opts)
        pr = self.csv("study,n,m1,m2,sd1,sd2,r\nA,20,12.1,10.2,3.1,3.3,0.6\nB,35,11.5,10.9,2.8,2.9,0.5\n"
                      "C,18,13.0,10.1,3.5,3.0,0.7\n", "paired.csv")
        rows, meta = tableio.read_table(pr)
        out, es = pipeline.run(rows, {"measure": "SMCC"}, meta)
        m = report.methods_text_en(out)
        self.assertIn("standardised mean change (paired design", m)
        self.assertIn("Paired (before–after or matched) designs", m)
        gen = self.csv("study,yi,n1,n2\nA,0.5,20,22\nB,0.3,30,31\nC,0.8,15,14\n", "gen.csv")
        rows, meta = tableio.read_table(gen)
        out, es = pipeline.run(rows, {"measure": "GEN", "gen_smd_vtype": "METAN_COHEN"}, meta)
        self.assertEqual(len(es), 3)
        assert_close(self, es.vi[0], 42.0 / 440 + 0.25 / 80, 1e-14)
        self.assertIn("only a standardised mean difference and the group sizes", report.methods_text_en(out))


class TestDoiPlot(_Tmp):
    def test_svg_and_plot_data(self):
        bcg = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")
        out, es, md = self.run_pipeline(bcg, measure="OR")
        svg = pipeline.make_doi_plot(out, es)
        doc = xml.dom.minidom.parseString(svg)
        lf = out["bias"]["lfk"]
        self.assertEqual(len(doc.getElementsByTagName("circle")), len(es))
        self.assertEqual(len(doc.getElementsByTagName("polyline")), 1)
        self.assertIn("LFK-index: %s (%s)" % (report._f(lf.lfk, 2), lf.category), svg)
        self.assertIn("|Z-pontszám|", svg)
        from xml.sax.saxutils import escape
        self.assertIn(escape(es.labels[lf.points[0]["study_index"]]), svg)
        _, _, data = pipeline.make_plots(out, es)
        self.assertEqual(data["doi"]["lfk"], lf.lfk)
        self.assertEqual([p["abs_z"] for p in data["doi"]["points"]], [p["abs_z"] for p in lf.points])
        paths = pipeline.write_outputs(out, es, os.path.join(self.tmp, "o"), md)
        self.assertIn("doi.svg", paths)
        self.assertIn("`doi.svg`", md)
        # |Z| = 0 felül: a legkisebb |Z|-jű pont a legmagasabban (legkisebb cy)
        cys = [float(c.getAttribute("cy")) for c in doc.getElementsByTagName("circle")]
        zs = [p["abs_z"] for p in lf.points]
        self.assertEqual(cys.index(min(cys)), zs.index(min(zs)))

    def test_no_doi_for_k_below_3(self):
        p = self.csv("study,yi,vi\nA,0.2,0.04\nB,0.5,0.05\n")
        out, es, md = self.run_pipeline(p, measure="GEN")
        self.assertIsNone(pipeline.make_doi_plot(out, es))
        paths = pipeline.write_outputs(out, es, os.path.join(self.tmp, "o"), md)
        self.assertNotIn("doi.svg", paths)


# ============================================================ 3. CLI
class TestCLIAnalyzeFlags(_Tmp):
    def test_analyze_with_new_flags(self):
        out = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), "--measure", "OR",
                               "--out", out, "--date", "2026-10-04", "--ht-centre", "untruncated",
                               "--trimfill-trim-model", "fixed", "--egger-ci-dist", "norm", "--begg-method", "normal",
                               "--begg-continuity", "--outliers", "--moderators", "szélesség", "--robust",
                               "--subgroup", "allokáció")
        self.assertEqual(code, 0, so + se)
        self.assertIn("Kiugró-szűrés: 2 kiugró vizsgálat", so)
        for f in ("doi.svg", "funnel.svg", "forest.svg", "report.md", "results.json"):
            self.assertTrue(os.path.exists(os.path.join(out, f)), f)
        with open(os.path.join(out, "results.json"), encoding="utf-8") as fh:
            res = json.load(fh)
        o = res["options"]
        self.assertEqual((o["h_centre"], o["trimfill_trim_model"], o["egger_ci_dist"], o["begg_method"],
                          o["begg_continuity"], o["metareg_robust"], o["outliers"]),
                         ("untruncated", "fixed", "norm", "normal", True, True, True))
        self.assertIsNone(o["tau2"])
        self.assertEqual(res["primary"]["heterogeneity"]["H_ci_centre"], "untruncated")
        self.assertIsNotNone(res["metaregression"]["robust"])
        self.assertIn("harbord", res["bias"])

    def test_robust_without_moderators_warns(self):
        code, so, se = run_cli("analyze", "--data", os.path.join(EXAMPLES, "normand1999_folytonos.csv"), "--measure",
                               "SMD", "--out", os.path.join(self.tmp, "o"), "--robust", "--no-plots")
        self.assertEqual(code, 0, se)
        self.assertIn("--robust csak meta-regresszióval", so)

    def test_measure_conventions_flags(self):
        for measure, extra in (("SMD", ["--smd-vtype", "metan_hedges", "--j-method", "approx"]),
                               ("COHEN_D", ["--smd-vtype", "METAN_COHEN"]),
                               ("MD", ["--md-vtype", "HO"]),
                               ("SMD_GLASS", ["--glass-vtype", "LS2"])):
            out = os.path.join(self.tmp, measure)
            code, so, se = run_cli("analyze", "--data", os.path.join(EXAMPLES, "normand1999_folytonos.csv"),
                                   "--measure", measure, "--out", out, "--no-plots", *extra)
            self.assertEqual(code, 0, "%s %s" % (so, se))
        with open(os.path.join(self.tmp, "MD", "results.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["options"]["md_vtype"], "pooled")
        with self.assertRaises(SystemExit) as cm:
            run_cli("analyze", "--data", os.path.join(EXAMPLES, "normand1999_folytonos.csv"),
                    "--measure", "MD", "--md-vtype", "welch")
        self.assertEqual(cm.exception.code, 2)
        code, so, se = run_cli("analyze", "--data", os.path.join(EXAMPLES, "pritz1997_arany.csv"), "--measure", "PFT",
                               "--pft-backtransform", "variance", "--out", os.path.join(self.tmp, "p"), "--no-plots")
        self.assertEqual(code, 0, se)

    def test_help_documents_new_measures(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                cli.main(["analyze", "-h"])
        h = buf.getvalue()
        for s in ("SMD_GLASS", "MC ", "SMCC", "sd_diff", "sum_sq_dev_d", "METAN_COHEN", "--outliers", "--robust",
                  "--ht-centre", "--trimfill-trim-model", "--egger-ci-dist", "--begg-method", "--md-vtype",
                  "--pft-backtransform"):
            self.assertIn(s, h)

    def test_validate_and_es_with_conventions(self):
        p = self.csv("study,n,sum_d,sum_sq_dev_d\nCholesterol,15,254,2808.9333\n")
        code, so, se = run_cli("validate", "--data", p, "--measure", "MC")
        self.assertEqual(code, 0, so + se)
        target = os.path.join(self.tmp, "es.csv")
        code, so, se = run_cli("es", "--data", p, "--measure", "MC", "--out", target)
        self.assertEqual(code, 0, so + se)
        rows, _ = tableio.read_table(target)
        assert_close(self, rows[0]["yi"], 16.933333333333334, 1e-12)
        g = self.csv("study,m1,sd1,n1,m2,sd2,n2\nGinkgo,58.4,3.8,28,56.8,4.3,24\n", "g.csv")
        target = os.path.join(self.tmp, "es2.csv")
        code, so, se = run_cli("es", "--data", g, "--measure", "MD", "--md-vtype", "pooled", "--out", target)
        self.assertEqual(code, 0, so + se)
        rows, _ = tableio.read_table(target)
        assert_close(self, rows[0]["vi"], 1.2615416666666668, 1e-12)        # Khan Ch8, pooled SD

    def test_convert_new_kinds(self):
        code, so, se = run_cli("convert", "smd-var", "--g", "0.5", "--n1", "20", "--n2", "22", "--vtype", "METAN_COHEN")
        self.assertEqual(code, 0, se)
        assert_close(self, json.loads(so)["vi"], 42.0 / 440 + 0.25 / 80, 1e-14)
        code, so, se = run_cli("convert", "paired-sums", "--n", "15", "--sum-d", "254", "--sum-sq-dev", "2808.93")
        self.assertEqual(code, 0, se)
        assert_close(self, json.loads(so)["mdiff"], 254 / 15.0, 1e-12)
        code, so, se = run_cli("convert", "smd-var", "--g", "0.5")
        self.assertEqual(code, 1)
        self.assertIn("--n1", se)


class TestCLIPower(unittest.TestCase):
    def test_power_text_and_json(self):
        code, so, se = run_cli("power", "--k", "18", "--effect", "0.7", "--n1", "15", "--n2", "15",
                               "--heterogeneity", "moderate", "--json")
        self.assertEqual(code, 0, se)
        assert_close(self, json.loads(so)["power"], 0.999983334487471, 1e-12)
        code, so, se = run_cli("power", "--k", "18", "--effect", "0.7", "--n1", "15", "--n2", "15",
                               "--heterogeneity", "moderate")
        self.assertIn("erő = 0.999983", so)
        code, so, se = run_cli("power", "--effect", "0.3", "--n1", "25", "--n2", "25", "--heterogeneity", "high",
                               "--target-power", "0.8")
        self.assertEqual(code, 0, se)
        self.assertIn("k = 15", so)
        code, so, se = run_cli("power", "--or", "1.5", "--k", "10", "--n1", "50", "--n2", "50", "--json")
        self.assertEqual(code, 0, se)
        assert_close(self, json.loads(so)["effect"], math.log(1.5) * math.sqrt(3) / math.pi, 1e-14)

    def test_power_errors(self):
        code, so, se = run_cli("power", "--effect", "0.5", "--n1", "20", "--n2", "20")
        self.assertEqual(code, 1)
        self.assertIn("--k", se)
        code, so, se = run_cli("power", "--k", "5", "--n1", "20", "--n2", "20")
        self.assertEqual(code, 1)


class TestCLIPrisma(_Tmp):
    def test_letters_ok_and_inconsistent(self):
        code, so, se = run_cli("prisma", "check", "--A1", "28", "--other", "1", "--B", "29", "--C", "14", "--G", "15",
                               "--H", "9", "--reasons", "longitudinal: 5; interim: 4", "--I", "6", "--meta", "6",
                               "--template", "prisma2009")
        self.assertEqual(code, 0, so + se)
        self.assertIn("RENDBEN", so)
        code, so, se = run_cli("prisma", "check", "--A1", "100", "--D1", "10", "--B", "95", "--C", "50", "--E", "45",
                               "--F", "2", "--G", "43", "--H", "30", "--J", "13", "--I", "14")
        self.assertEqual(code, 1)
        self.assertIn("[P002] error", so)
        self.assertIn("[P006] error", so)
        self.assertIn("HIBÁS", so)

    def test_json_composer_md_inputs(self):
        flow = {k: v for k, v in CASES["simplypsych_guide__prisma2009_flow_bialek2023"]["rows"][0].items()
                if k != "study"}
        p = self.csv(json.dumps(flow), "flow.json")
        code, so, se = run_cli("prisma", "check", "--json", p, "--out-format", "json")
        self.assertEqual(code, 0, se)
        res = json.loads(so)
        self.assertTrue(res["ok"])
        self.assertEqual(res["template"], "PRISMA2009")
        self.assertIn("P008", [f["code"] for f in res["findings"]])
        comp = {"identified_databases": 200, "identified_registers": 10, "identified_other": 0, "dedup_removed": 40,
                "removed_before_screening_n": 5, "screened": 165, "excluded_screening": 130,
                "sought_for_retrieval": 35, "not_retrieved": 3, "assessed_eligibility": 32,
                "excluded_eligibility": 27, "excluded_eligibility_reasons": {"nem RCT": 20, "rossz kimenet": 7},
                "included": 5, "undecided": 0}
        p = self.csv(json.dumps(comp, ensure_ascii=False), "prisma-flow.json")
        code, so, se = run_cli("prisma", "check", "--composer", p)
        self.assertEqual(code, 0, so + se)
        code, so, se = run_cli("prisma", "check", "--composer", p, "--J", "6")      # felülírás → J ≠ G − H
        self.assertEqual(code, 1)
        self.assertIn("P005", so)
        md = "\n".join(["| Lépés | Szám | Ellenőrzés |", "|---|---|---|",
                        "| Azonosított rekordok — adatbázisok (n = A1) | 120 | |",
                        "| Eltávolítva szűrés előtt: duplikátum (n = D1), automatikus (n = D2), egyéb (n = D3) | 20 / 0 / 0 | |",
                        "| Szűrt rekordok (n = B) | 100 | |", "| Kizárt rekordok (n = C) | 80 | |",
                        "| Teljes szövegre keresett (n = E) | 20 | |", "| Nem elérhető (n = F) | 0 | |",
                        "| Értékelt teljes szöveg (n = G) | 20 | |", "| Kizárt jelentések (n = H) | 12 (nem RCT: 12) | |",
                        "| Bevont jelentések / vizsgálatok (n = J, n = I) | 8 / 6 | |"])
        p = self.csv(md, "prisma_folyamat.md")
        code, so, se = run_cli("prisma", "check", "--md", p)
        self.assertEqual(code, 0, so + se)

    def test_no_input_is_an_error(self):
        code, so, se = run_cli("prisma", "check")
        self.assertEqual(code, 1)
        self.assertIn("adj meg bemenetet", se)
        code, so, se = run_cli("prisma", "check", "--reasons", "rossz")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
