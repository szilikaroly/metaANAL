# -*- coding: utf-8 -*-
"""Integrációs regressziós tesztek (A–D kötegek összeillesztése után).

A kötegek közti szerződés végponttól végpontig (CLI → pipeline → riport), és az integráció
során talált hibák. A referenciaértékek az R metafor 4.4-ből; a generáló R-kód kommentben.
"""
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _helpers import ROOT, assert_close
from metaelemzes import bias as B
from metaelemzes import cli, pipeline, report, tableio
from metaelemzes import effect_sizes as E
from metaelemzes import models as M
from metaelemzes import moderators as MO

EXAMPLES = os.path.join(ROOT, "peldak")
BCG = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")
NORMAND = os.path.join(EXAMPLES, "normand1999_folytonos.csv")
PRITZ = os.path.join(EXAMPLES, "pritz1997_arany.csv")


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

    def analyze(self, path, **opts):
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


def _bcg_es(**filters):
    rows, meta = tableio.read_table(BCG)
    if filters:
        rows = tableio.apply_filters(rows, meta=meta, **filters)
    return E.compute(rows, "RR")


# ----------------------------------------------------------------- szerződés
class TestContractEndToEnd(_Tmp):
    """A kötegek közti szerződés (resolve_column, apply_filters+report, blocking_labels,
    compute(skip_labels), row_index, pipeline-bekötés, cli filter_report) együtt."""

    def test_filter_report_reaches_results_and_report(self):
        out, md, res = self.cli_analyze(BCG, "--measure", "RR", "--exclude", "ALLOKÁCIÓ=Random", "--no-plots")
        flt = res["input"]["filters"]
        self.assertEqual(flt["n_rows_read"], 13)
        self.assertEqual(flt["n_rows_kept"], 6)
        self.assertEqual(len(flt["report"]), 1)
        self.assertEqual(flt["report"][0]["mode"], "exclude")
        self.assertEqual(flt["report"][0]["removed"], 7)
        self.assertIn("Aronson 1948", flt["report"][0]["removed_labels"])
        self.assertEqual(res["options"]["filter_report"][0]["removed"], 7)
        self.assertIn("13 sorból 6 maradt", md)

    def test_error_rows_really_excluded_and_row_index_used(self):
        p = self.write("blk.csv", "study;e1;n1;e2;n2;year;csoport\nA;4;123;11;139;2001;x\nB;6;306;29;303;2003;y\n"
                                  "1.0;150;100;10;100;2002;x\nD;3;231;11;220;;y\nE;17;1716;65;1665;2005;x\n")
        out, md, res = self.cli_analyze(p, "--measure", "RR", "--subgroup", "Csoport", "--cumulative", "év")
        self.assertEqual(res["validation"]["blocked_studies"], ["1.0"])         # címke szó szerint
        self.assertEqual(res["effect_sizes"]["labels"], ["A", "B", "D", "E"])
        self.assertEqual(res["effect_sizes"]["row_index"], [0, 1, 3, 4])
        self.assertTrue(res["effect_sizes"]["excluded"][0]["reason"].startswith("validálási hiba"))
        # 'csoport' / 'year': a beolvasó kanonikus kulcsai; a riport az eredeti fejlécet mutatja
        self.assertEqual(res["columns"], {"subgroup": "subgroup", "cumulative": "year"})
        self.assertEqual(res["column_labels"], {"subgroup": "csoport", "cumulative": "year"})
        with open(os.path.join(out, "plot_data.json"), encoding="utf-8") as fh:
            pdata = json.load(fh)
        secs = {s["title"]: (s["indices"], s["row_index"]) for s in pdata["sections"]}
        self.assertEqual(secs, {"x": ([0, 3], [0, 4]), "y": ([1, 2], [1, 3])})
        self.assertIn("1.0 — validálási hiba", md)

    def test_unknown_column_fails_before_output(self):
        out = os.path.join(self.tmp, "nincs")
        code, so, se = run_cli("analyze", "--data", BCG, "--measure", "RR", "--moderators", "nincs_ilyen",
                               "--out", out)
        self.assertNotEqual(code, 0)
        self.assertIn("ismeretlen oszlop", so + se)
        self.assertFalse(os.path.exists(out))


# ----------------------------------------------------- IVhet alcsoport-teszt
class TestIvhetSubgroupQBetween(unittest.TestCase):
    """IVhet alcsoportoknál a Q_between az √(1/Σw) inverz-variancia SE-vel számolt (fix hatású,
    túl liberális: BCG Q_b = 19.87, p < 0.001, miközben a csoport-CI-k erősen átfednek)."""

    def test_uses_ivhet_se(self):
        # R: rma(est, sei=se_ivhet, mods=~factor(1:3), method="FE") a három IVhet csoportbecslésre:
        #    QM 0.507569842708, p 0.775858654731
        es = _bcg_es()
        g = [r["allokáció"] for r in es.rows]
        r = MO.subgroup_analysis(es.yi, es.vi, g, es.labels, "ivhet", level=0.9)
        assert_close(self, r.Q_between, 0.507569842708, 1e-9)
        assert_close(self, r.p_between, 0.775858654731, 1e-9)
        self.assertEqual(r.Q_between_se, "ivhet")
        self.assertIn("IVhet", r.Q_between_note)
        # a random modell továbbra is a Wald SE-t használja (metafor: QM 1.861444946)
        r = MO.subgroup_analysis(es.yi, es.vi, g, es.labels, "random")
        assert_close(self, r.Q_between, 1.861444946, 1e-7)
        self.assertEqual(r.Q_between_se, "wald")

    def test_methods_sentence(self):
        rows, meta = tableio.read_table(BCG)
        out, es = pipeline.run(rows, {"measure": "RR", "model": "ivhet", "subgroup": "allokáció"}, meta)
        self.assertIn("based on the IVhet (heterogeneity-inflated) standard errors", report.methods_text_en(out))


# ------------------------------------------------------------ meta-regresszió
class TestMetaRegressionIntegration(_Tmp):

    def test_pm_tau2_passed_through_and_display_names(self):
        # R: rma(yi, vi, mods=~ablat+year, method="PM", test="knha") (OR, dat.bcg):
        #    tau2 0.1631972; ablat -0.02751 (se 0.01201); year 0.00686
        out, es, md = self.analyze(BCG, measure="OR", tau2="PM", moderators=["szélesség", "év"])
        mr = out["metaregression"]
        self.assertEqual(mr.tau2_method, "PM")
        assert_close(self, mr.tau2, 0.1631972, 1e-5)
        self.assertEqual([c["name"] for c in mr.coefficients], ["intercept", "szélesség", "év"])
        assert_close(self, mr.coefficients[1]["estimate"], -0.02751, 1e-4)
        self.assertIn("| év |", md)
        self.assertNotIn("| year |", md)
        self.assertIn("Mixed-effects meta-regression (the Paule–Mandel estimator;", report.methods_text_en(out))

    def test_constant_moderator_dropped_with_warning(self):
        p = BCG
        out, es, md = self.analyze(p, measure="RR", moderators=["allokáció"])
        self.assertIsNotNone(out["metaregression"])          # szűrés nélkül három szint
        rows, meta = tableio.read_table(p)
        rows = tableio.apply_filters(rows, include=["allokáció=systematic"], meta=meta)
        out, es = pipeline.run(rows, {"measure": "RR", "moderators": ["allokáció"]}, meta)
        self.assertIsNone(out.get("metaregression"))
        self.assertTrue(any("egyetlen értéket" in w for w in out["warnings"]), out["warnings"])
        out, es = pipeline.run(rows, {"measure": "RR", "moderators": ["allokáció", "szélesség"]}, meta)
        names = [c["name"] for c in out["metaregression"].coefficients]
        self.assertEqual(names, ["intercept", "szélesség"])
        self.assertIn("moderator(s) szélesség.", report.methods_text_en(out))

    def test_no_p_equals_less_than(self):
        rows, meta = tableio.read_table(BCG)
        rows = tableio.apply_filters(rows, include=["allokáció=systematic"], meta=meta)
        out, es = pipeline.run(rows, {"measure": "RR", "moderators": ["szélesség"]}, meta)
        md = report.build_report(out, date="2026-01-01")
        self.assertNotIn("p = <", md)
        self.assertIn("QE(2) = ", md)


# --------------------------------------------------------------- riport-szöveg
class TestReportConsistency(_Tmp):

    def test_column_labels_are_csv_headers(self):
        out, es, md = self.analyze(BCG, measure="RR", subgroup="ALLOKÁCIÓ", cumulative="ÉV")
        self.assertEqual(out["column_labels"], {"subgroup": "allokáció", "cumulative": "év"})
        self.assertIn("## Alcsoport-elemzés (allokáció)", md)
        self.assertIn("### Kumulatív metaanalízis (rendezés: év)", md)
        self.assertIn("subgroup analyses by allokáció", report.methods_text_en(out))
        self.assertIn("| τ² | I² (Q-alapú) |", md)

    def test_tau2_outside_qp_ci_is_explained(self):
        # R: confint(rma(yi, vi, method="DL")) (normand1999 MD): tau2 205.4094, QP CI [292.9181; ...]
        out, es, md = self.analyze(NORMAND, measure="MD", model="ivhet")
        tci = out["primary"].heterogeneity["tau2_ci_QP"]
        assert_close(self, out["primary"].tau2, 205.4094, 1e-4)
        assert_close(self, tci[0], 292.9181, 1e-4)
        self.assertIn("kívül esik a Q-profile CI-n", md)
        out, es, md = self.analyze(NORMAND, measure="MD")      # REML: a pontbecslés a CI-n belül
        self.assertNotIn("kívül esik a Q-profile CI-n", md)

    def test_no_double_period_and_no_dash_bullet(self):
        out, es, md = self.analyze(PRITZ, measure="PLO", model="ivhet", level=0.9)
        self.assertNotIn("et al..", md)
        self.assertNotIn("\n- —\n", md)

    def test_nan_p_in_plot_footer(self):
        self.assertEqual(pipeline._p(float("nan")), "= –")


class TestDefensiveWrappers(unittest.TestCase):

    def test_subgroup_model_error_becomes_warning(self):
        rows, meta = tableio.read_table(BCG)
        with mock.patch.object(MO, "subgroup_analysis", side_effect=M.ModelError("teszt-hiba")):
            out, es = pipeline.run(rows, {"measure": "RR", "subgroup": "allokáció"}, meta)
        self.assertNotIn("subgroups", out)
        self.assertIn("Alcsoport-elemzés: teszt-hiba", out["warnings"])
        report.build_report(out, date="2026-01-01")


class TestTrimFillMetaforRefit(unittest.TestCase):
    """k0 > 0: a metafor 4.4 trimfill a kitöltött adatokat test='z'-vel, 95%-on illeszti újra."""

    def test_z_refit_matches_metafor(self):
        # R: trimfill(rma(yi, vi, data=dat.bcg[alloc != "random"], test="knha")):
        #    k0 1 (left), 0.5804713 [0.3832732; 0.8791299]
        es = _bcg_es(exclude=["allokáció=random"])
        t = B.trim_and_fill(es.yi, es.vi, es.labels, "random", "REML", "L0", ci_method="z", level=0.95)
        self.assertEqual((t.k0, t.side), (1, "left"))
        for got, want in zip((t.adjusted.estimate, t.adjusted.ci_lower, t.adjusted.ci_upper),
                             (0.5804713, 0.3832732, 0.8791299)):
            assert_close(self, math.exp(got), want, 2e-6)


if __name__ == "__main__":
    unittest.main()
