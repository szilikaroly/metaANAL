# -*- coding: utf-8 -*-
"""A 2. review-kör kötegek közötti (cross-file) változásainak tesztjei: a motor-oldali javítások
végponttól végpontig (CLI, pipeline, riport) is érvényesülnek.

A tesztnevek a megállapítás azonosítóját viselik (pl. R2_ROB_02 = robustness:R2-ROB-02).
Orákulumok: R metafor 4.4 (a számértékeknél megjelölve).
"""
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _helpers import ROOT, assert_close
from metaelemzes import cli, kb, pipeline, prisma, projekt, report, tableio
from metaelemzes import effect_sizes as E
from metaelemzes import models as M
from metaelemzes import validate as V

BCG = os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv")


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        try:
            code = cli.main([str(a) for a in args])
        except SystemExit as exc:      # argparse-hiba
            code = exc.code
    return code, buf.getvalue(), err.getvalue()


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def csv(self, text, name="adat.csv"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def results(self, outdir):
        with open(os.path.join(outdir, "results.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def report_md(self, outdir):
        with open(os.path.join(outdir, "report.md"), encoding="utf-8") as fh:
            return fh.read()


# két sor ugyanazzal a címkével (több karú vizsgálat), az egyikből hiányzik az sd1
DUP_CSV = ("study,m1,sd1,n1,m2,sd2,n2\n"
           "Smith 2001,10,2,20,8,2,20\n"
           "Smith 2001,11,,20,8,2,20\n"
           "Jones,9,2,30,8,2,30\n"
           "Kim,12,3,25,9,3,25\n"
           "Lee,10,2,40,9,2,40\n")


class TestR2ROB02RowBlockingEndToEnd(_Tmp):
    def test_R2_ROB_02_pipeline_keeps_valid_row_with_same_label(self):
        rows, meta = tableio.read_table(self.csv(DUP_CSV))
        out, es = pipeline.run(rows, {"measure": "MD", "ci": "z"}, meta)
        self.assertEqual(es.row_index, [0, 2, 3, 4])
        self.assertEqual(out["validation"]["blocked_studies"], ["Smith 2001"])
        self.assertEqual([(x["study"], x["reason"]) for x in out["effect_sizes"]["excluded"]],
                         [("Smith 2001", "validálási hiba: V002")])
        assert_close(self, out["primary"].estimate, 1.5513660790, 1e-6)      # metafor escalc MD + rma REML (k = 4)
        v002 = [f for f in out["validation"]["findings"] if f["code"] == "V002"]
        self.assertEqual([f["row"] for f in v002], [1])

    def test_R2_ROB_02_cli_validate_and_es(self):
        data = self.csv(DUP_CSV)
        code, so, se = run_cli("validate", "--data", data, "--measure", "MD", "--json")
        self.assertEqual(code, 1, se)                     # a V002 hiba marad, de csak az a sor esik ki
        res = json.loads(so)
        self.assertEqual(res["k"], 4)
        self.assertEqual(len(res["excluded"]), 1)
        target = os.path.join(self.tmp, "es.csv")
        code, so, se = run_cli("es", "--data", data, "--measure", "MD", "--out", target)
        self.assertEqual(code, 0, se)
        es_rows, _ = tableio.read_table(target)
        self.assertEqual([r["study"] for r in es_rows], ["Smith 2001", "Jones", "Kim", "Lee"])

    def test_R2_ROB_02_parse_error_blocks_only_its_row(self):
        data = self.csv(DUP_CSV.replace("Smith 2001,11,,20", "Smith 2001,11,abc,20"))
        rows, meta = tableio.read_table(data)
        f = V.validate(rows, "MD", meta)
        self.assertEqual(V.blocking_rows(f), {1: "V003, V002"})
        es = E.compute(rows, "MD", skip_rows=V.blocking_rows(f))
        self.assertEqual(len(es), 4)


class TestR2ROB16GenValidation(unittest.TestCase):
    def test_R2_ROB_16_gen_errors_are_blocking(self):
        rows = [{"study": "A", "yi": 0.5, "sei": -0.2}, {"study": "B", "yi": 0.3, "sei": 0.1, "n": 10.5},
                {"study": "C", "yi": 0.4, "sei": 0.15, "n": 0}, {"study": "D", "yi": 0.2, "sei": 0.12, "n": 20},
                {"study": "E", "yi": 0.6, "sei": 0.2, "n": 30},
                {"study": "F", "yi": 0.1, "vi": 0.01, "sei": -0.1}]     # a vi számít, a sei nem
        f = V.validate(rows, "GEN")
        self.assertEqual(V.blocking_rows(f), {0: "V005", 1: "V004", 2: "V004"})
        self.assertEqual([x["code"] for x in f if x["code"] == "V022"], [])
        out, es = pipeline.run(rows, {"measure": "GEN"})
        self.assertEqual(es.labels, ["D", "E", "F"])
        why = {x["study"]: x["reason"] for x in out["effect_sizes"]["excluded"]}
        self.assertEqual(why, {"A": "validálási hiba: V005", "B": "validálási hiba: V004",
                               "C": "validálási hiba: V004"})

    def test_R2_ROB_16_gen_smd_vtype_arm_sizes(self):
        rows = [{"study": "A", "yi": 0.5, "n1": 10.5, "n2": 12}, {"study": "B", "yi": 0.3, "n1": 15, "n2": 14},
                {"study": "C", "yi": 0.2, "vi": 0.05, "n1": 0.5, "n2": 1}]       # a vi miatt az n1/n2 nem kell
        f = V.validate(rows, "GEN", options={"gen_smd_vtype": "LS"})
        self.assertEqual(V.blocking_rows(f), {0: "V004"})


class TestR2NF05PipelineHtCentre(unittest.TestCase):
    def test_R2_NF_05_subgroup_mh_peto_follow_ht_centre(self):
        rows = [{"study": s, "e1": e1, "n1": 100, "e2": e2, "n2": 100, "g": g} for s, e1, e2, g in (
            ("A", 10, 15, "x"), ("B", 12, 16, "x"), ("C", 9, 14, "x"), ("D", 11, 15, "y"), ("E", 10, 16, "y"))]
        out, _ = pipeline.run(rows, {"measure": "OR", "h_centre": "untruncated", "subgroup": "g",
                                     "mh": True, "peto": True})
        prim = out["primary"].heterogeneity
        self.assertEqual(prim["H_ci_centre"], "untruncated")
        self.assertEqual(out["subgroups"].overall.heterogeneity["I2_ci_HT"], prim["I2_ci_HT"])
        for key in ("mh", "peto"):
            self.assertEqual(out[key].heterogeneity["H_ci_centre"], "untruncated", key)
        assert_close(self, out["mh"].Q, 0.176398, 1e-5)                   # metafor rma.mh QE


class TestCliOptionsReachPipeline(_Tmp):
    def test_R2_NF_02_metareg_tau2_fe_robust_cli(self):
        out = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", BCG, "--measure", "RR", "--moderators", "szélesség", "--robust",
                               "--metareg-tau2", "fe", "--metareg-test", "z", "--out", out, "--no-plots")
        self.assertEqual(code, 0, se)
        res = self.results(out)
        self.assertEqual(res["options"]["metareg_tau2"], "FE")
        rob = res["metaregression"]["robust"]
        # metafor: robust(rma(yi, vi, mods = ~ablat, method = "FE"), cluster = 1:13, adjust = TRUE)
        assert_close(self, rob["coefficients"][0]["estimate"], 0.343564577, 1e-6)
        assert_close(self, rob["coefficients"][0]["se"], 0.0971512687, 1e-6)
        assert_close(self, rob["coefficients"][1]["estimate"], -0.029236934, 1e-6)
        assert_close(self, rob["coefficients"][1]["se"], 0.0044072221, 1e-6)
        self.assertAlmostEqual(rob["F"], 44.008, places=3)
        code, _, se = run_cli("analyze", "--data", BCG, "--measure", "RR", "--moderators", "szélesség",
                              "--metareg-tau2", "XX", "--out", out)
        self.assertEqual(code, 2)

    def test_R2_REP_14_subgroup_prespecified_flag(self):
        for flag, phrase in (([], "In addition, subgroup analyses by allokáció"),
                             (["--subgroup-prespecified"], "Pre-specified subgroup analyses by allokáció")):
            out = os.path.join(self.tmp, "sg%d" % len(flag))
            code, _, se = run_cli("analyze", "--data", BCG, "--measure", "RR", "--subgroup", "allokáció",
                                  "--out", out, "--no-plots", *flag)
            self.assertEqual(code, 0, se)
            self.assertIn(phrase, self.report_md(out))

    def test_R2_REP_10_no_plots_report_has_no_svg_refs(self):
        out = os.path.join(self.tmp, "np")
        code, _, se = run_cli("analyze", "--data", BCG, "--measure", "RR", "--out", out, "--no-plots")
        self.assertEqual(code, 0, se)
        md = self.report_md(out)
        for name in ("forest.svg", "funnel.svg", "doi.svg"):
            self.assertNotIn(name, md)
            self.assertFalse(os.path.exists(os.path.join(out, name)))

    def test_R2_REP_15_cli_prints_warnings(self):
        data = self.csv("study,m1,sd1,n1,m2,sd2,n2\nA,10,2,20,8,2,20\nB,9,2,30,8,2,30\nC,12,3,25,9,3,25\n")
        code, so, se = run_cli("analyze", "--data", data, "--measure", "COHEN_D", "--smd-vtype", "UB",
                               "--out", os.path.join(self.tmp, "w"), "--no-plots")
        self.assertEqual(code, 0, se)
        self.assertIn("FIGYELEM: COHEN_D: az UB variancia", so)

    def test_R2_NF_10_ivhet_common_tau2_described(self):
        out = os.path.join(self.tmp, "iv")
        code, _, se = run_cli("analyze", "--data", BCG, "--measure", "RR", "--model", "ivhet", "--subgroup",
                              "allokáció", "--common-tau2", "--out", out, "--no-plots")
        self.assertEqual(code, 0, se)
        md = self.report_md(out)
        self.assertIn("IVhet modell alcsoportonként, közös τ² az alcsoportokban (τ² = ", md)
        self.assertIn("IVhet models within subgroups with a common τ² across subgroups (DerSimonian–Laird", md)


class TestR2NF08MetanPairingWarnings(unittest.TestCase):
    ROWS = [{"study": s, "m1": m1, "sd1": 2.0, "n1": 20, "m2": 8.0, "sd2": 2.0, "n2": 22}
            for s, m1 in (("A", 10.0), ("B", 9.0), ("C", 11.0))]

    def warnings(self, measure, vtype):
        out, _ = pipeline.run(self.ROWS, {"measure": measure, "smd_vtype": vtype})
        return [w for w in out["warnings"] if "metan" in w]

    def test_R2_NF_08_mismatched_pairings_warn(self):
        self.assertEqual(len(self.warnings("SMD", "METAN_COHEN")), 1)
        self.assertEqual(len(self.warnings("COHEN_D", "METAN_HEDGES")), 1)
        out, _ = pipeline.run(self.ROWS, {"measure": "SMD", "smd_vtype": "METAN_COHEN"})
        self.assertIn("METAN_COHEN variancia", report.build_report(out, "t", "2026-01-01", plots=False))

    def test_R2_NF_08_matching_pairings_silent(self):
        self.assertEqual(self.warnings("SMD", "METAN_HEDGES"), [])
        self.assertEqual(self.warnings("COHEN_D", "METAN_COHEN"), [])


class TestR2ROB07Overflow(_Tmp):
    def test_R2_ROB_07_tiny_variance_refused_with_study_name(self):
        with self.assertRaises(M.ModelError) as cm:
            M.meta_analysis([0.1, 0.2, 0.3], [1e-300, 0.04, 0.05], labels=["A", "B", "C"])
        self.assertIn("A vizsgálat", str(cm.exception))
        data = self.csv("study,yi,vi\nA,0.1,1e-300\nB,0.2,0.04\nC,0.3,0.05\n")
        code, _, se = run_cli("analyze", "--data", data, "--measure", "GEN", "--out", os.path.join(self.tmp, "o"))
        self.assertEqual(code, 1)
        self.assertIn("ModelError", se)
        self.assertNotIn("OverflowError", se)
        # szokatlanul kicsi, de kezelhető skála: marad
        M.meta_analysis([0.1, 0.2, 0.3], [1e-40, 0.04, 0.05])

    def test_R2_ROB_07_non_finite_effect_excluded(self):
        es = E.EffectSizes("GEN")
        es.add("A", float("inf"), 0.1)
        es.add("B", 0.2, 0.1)
        self.assertEqual(es.labels, ["B"])
        self.assertIn("nem véges", es.excluded[0][1])


class TestR2ROB10ControlCharacters(_Tmp):
    def test_R2_ROB_10_labels_are_sanitised_on_read(self):
        rows, _ = tableio.read_table(self.csv("study,yi,vi\nSmith\x072001,0.1,0.04\nJones,0.2,0.05\n"))
        self.assertEqual(rows[0]["study"], "Smith 2001")


class TestConvertInputChecks(unittest.TestCase):
    def test_AG_06_invalid_inputs_are_errors(self):
        for args in (("split-control", "--n", 41.5, "--arms", 2), ("split-control", "--n", 41, "--arms", 0),
                     ("split-control", "--n", 10, "--arms", 2, "--events", 11),
                     ("se-from-p", "--estimate", 0.5, "--p", 1.2), ("se-from-p", "--estimate", -0.5, "--p", 0.03, "--log"),
                     ("sd-from-t", "--t", 0, "--n1", 10, "--n2", 10, "--md", 1),
                     ("r-to-d", "--y", 1.0, "--v", 0.01), ("logor-to-d", "--y", 0.3, "--v", -0.01),
                     ("d-to-r", "--y", 0.5, "--v", 0.04, "--n1", 0, "--n2", 10)):
            code, so, se = run_cli("convert", *args)
            self.assertEqual(code, 1, args)
            self.assertIn("HIBA: ConversionError", se, args)

    def test_AG_06_missing_argument_named(self):
        code, _, se = run_cli("convert", "d-to-r", "--y", 0.5, "--v", 0.04)
        self.assertEqual(code, 1)
        self.assertIn("--n1", se)


class TestProjectResolveOpen(_Tmp):
    def test_AG_02_cli_reopen_and_blocker_wontfix(self):
        d = os.path.join(self.tmp, "p")
        projekt.init(d, "teszt")
        fid = projekt.add_finding(d, "reviewer", "blocker", "hiba", stage="S05")
        code, _, se = run_cli("project", "resolve", d, fid, "--status", "wontfix", "--resolution", "nem javítjuk")
        self.assertEqual(code, 1)
        code, _, se = run_cli("project", "resolve", d, fid, "--status", "fixed", "--resolution", "javítva")
        self.assertEqual(code, 0, se)
        code, so, se = run_cli("project", "resolve", d, fid, "--status", "open", "--resolution", "nem elég")
        self.assertEqual(code, 0, se)
        self.assertEqual(projekt.get_item(d, "finding", fid)["status"], "open")


class TestKbCrossFile(_Tmp):
    def setUp(self):
        super().setUp()
        self.db = os.path.join(self.tmp, "kb.sqlite")
        kb.build(self.db)

    def test_KB_R2_07_checklist_constant_matches_seed(self):
        self.assertEqual(sorted(kb.CHECKLISTS), sorted(kb.checklist_names(self.db)))

    def test_AG_10_chunk_hits_printed_with_stable_ref(self):
        p = self.csv("Zebra fulltext paragraph about heterogeneity.", "sajat.txt")
        kb.ingest(p, source_id="sajat", db=self.db)
        code, so, se = run_cli("kb", "--db", self.db, "search", "zebra", "--scope", "chunk")
        self.assertEqual(code, 0, se)
        self.assertIn("[sajat#0]", so)

    def test_KBP_13_source_filter_counts_only_matching_rules(self):
        # a --limit a szűrt szabályokat számolja: = a teljes (szűretlen) rangsor első 2 illeszkedő eleme
        everything = kb.search("heterogeneity", 10000, self.db, ("rule",))["rule"]
        for src in ("veroniki2016", "engine"):
            want = [r["id"] for r in everything if src in [x.strip() for x in (r["source_ids"] or "").split(",")]][:2]
            self.assertEqual(len(want), 2, src)
            self.assertEqual([r["id"] for r in kb.search("heterogeneity", 2, self.db, ("rule",), src)["rule"]], want)

    def test_R2_ROB_17_query_guards(self):
        with self.assertRaises(kb.KBError):
            kb.query("SELECT 1", db=self.db, max_rows=-1)
        self.assertEqual(kb.query("SELECT 1", db=self.db, timeout=-5)[1], [(1,)])


class TestPrismaCrossFile(_Tmp):
    def test_KBP_03_p017_is_a_known_rule(self):
        self.assertEqual(prisma.RULES["P017"], cli._P017)
        self.assertEqual(prisma.RULES["P017"][0], "error")

    def test_AG_01_empty_md_table_message(self):
        md = self.csv("| Lépés | Szám | Ellenőrzés |\n|---|---|---|\n| Szűrt rekordok (n = B) | | |\n", "p.md")
        code, _, se = run_cli("prisma", "check", "--md", md)
        self.assertEqual(code, 1)
        self.assertIn("Szám oszlopa üres", se)

    def test_KBP_02_other_methods_options(self):
        args = ["prisma", "check", "--A1", 100, "--D1", 10, "--B", 90, "--C", 70, "--E", 20, "--F", 0, "--G", 20,
                "--H", 15, "--reasons", "nem RCT: 15", "--J", 8, "--I", 8, "--om-identified", 5, "--om-sought", 4,
                "--om-assessed", 4, "--om-excluded", 1, "--om-reasons", "nem RCT: 1", "--out-format", "json"]
        code, so, se = run_cli(*args)
        self.assertEqual(code, 0, se)
        self.assertEqual(json.loads(so)["derived"]["other_methods_included_reports"], 3)
        args[args.index("--J") + 1] = 9          # 5 + 3 ≠ 9
        code, _, _ = run_cli(*args)
        self.assertEqual(code, 1)

    def test_KBP_02_template_other_branch_rows_parse(self):
        with open(os.path.join(ROOT, "tudasbazis", "sablonok", "prisma_folyamat_sablon.md"), encoding="utf-8") as fh:
            t = fh.read()
        for row, val in (("Egyéb forrásból (hivatkozás-követés, szakértő) — külön ág", "5"),
                         ("Egyéb ág: teljes szövegre keresett", "4"), ("Egyéb ág: nem elérhető teljes szöveg", "0"),
                         ("Egyéb ág: teljes szövegben értékelt", "4"), ("Egyéb ág: kizárt okokkal", "1 (nem RCT: 1)")):
            self.assertIn("| %s | |" % row, t)
            t = t.replace("| %s | |" % row, "| %s | %s |" % (row, val))
        f = prisma.parse_markdown_table(t)
        self.assertEqual({k: f[k] for k in f if k.startswith("other_methods")},
                         {"other_methods_identified": 5, "other_methods_sought": 4, "other_methods_not_retrieved": 0,
                          "other_methods_assessed": 4, "other_methods_excluded": 1,
                          "other_methods_excluded_reasons": {"nem RCT": 1}})


if __name__ == "__main__":
    unittest.main()
