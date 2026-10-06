# -*- coding: utf-8 -*-
"""Regressziós tesztek a 2. ellenőrzési kör tesztminőségi megállapításaihoz (TS-02 … TS-09).

Minden teszt egy review-megállapításra hivatkozik (az azonosító a teszt docstringjében). Az
orákulumok függetlenek a motortól: metafor 4.4 (R; conv.fivenum, fsn), zárt alak R-ben
(qnorm / qt), illetve a forrás nyomtatott értéke a nyomtatott pontosságnak megfelelő tűréssel.
"""
import copy
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _helpers import ROOT, assert_close, load_reference
import source_cases as SC
from metaelemzes import bias as B
from metaelemzes import cli
from metaelemzes import conversions as C
from metaelemzes import distributions as D
from metaelemzes import effect_sizes as E
from metaelemzes import kb, projekt

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = {c["case_id"]: c for c in SC.load_cases()}


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = cli.main(list(args))
    return code, buf.getvalue(), err.getvalue()


# ============================================================ TS-02: konverziók orákulummal
class TestConversionOracles(unittest.TestCase):
    """TS-02: a medián/IQR/tartomány-konverziók a metafor 4.4 conv.fivenum értékeihez mérve
    (aszimmetrikus bemenet, hogy a Luo-súlyok és a Wan-féle S3 SD ténylegesen számítson)."""

    # (n, min, Q1, medián, Q3, max) → metafor: S1/S2/S3 Luo-átlag (alapértelmezés), Wan 2014 SD
    # conv.fivenum(..., test = FALSE); S3 SD: method = "wan2014" (a metafor alap-SD-je itt Shi 2020)
    FIVENUM = {
        7: {"S1": (15.021544795358952, 14.29106691143328), "S2": (10.889285714285714, 10.103962147784886),
            "S3": (14.361220613598714, 12.197514529609084)},
        30: {"S1": (11.972897250696676, 9.5575055212413016), "S2": (10.782500000000001, 8.5626224066574625),
             "S3": (12.3038216208361, 9.0600639639493821)},
        100: {"S1": (10.403596372036779, 7.804399930371213), "S2": (10.75975, 8.2742542328822122),
              "S3": (11.420082492316839, 8.0393270816267126)},
    }

    def test_luo_mean_wan_sd_vs_metafor(self):
        mn, q1, md, q3, mx = 2, 6, 9, 17, 41
        for n, ref in self.FIVENUM.items():
            got = {"S1": (C.mean_from_median(n, md, minimum=mn, maximum=mx), C.sd_from_median(n, minimum=mn, maximum=mx)),
                   "S2": (C.mean_from_median(n, md, q1=q1, q3=q3), C.sd_from_median(n, q1=q1, q3=q3)),
                   "S3": (C.mean_from_median(n, md, q1, q3, mn, mx), C.sd_from_median(n, q1, q3, mn, mx, median=md))}
            for sc, (m, s) in ref.items():
                assert_close(self, got[sc][0], m, 1e-12, "n=%d %s átlag" % (n, sc), rel=True)
                assert_close(self, got[sc][1], s, 1e-12, "n=%d %s SD" % (n, sc), rel=True)

    def test_review_example_values(self):
        # a megállapításban idézett metafor-értékek (n = 100; min 0, Q1 10, medián 15, Q3 22, max 50)
        assert_close(self, C.mean_from_median(100, 15, minimum=0, maximum=50), 16.122877097629424, 1e-12, rel=True)
        assert_close(self, C.sd_from_median(100, minimum=0, maximum=50), 10.00564093637335, 1e-12, rel=True)
        assert_close(self, C.mean_from_median(100, 15, q1=10, q3=22), 15.7039, 1e-12, rel=True)
        assert_close(self, C.sd_from_median(100, q1=10, q3=22), 9.0264591631442315, 1e-12, rel=True)
        assert_close(self, C.mean_from_median(100, 15, 10, 22, 0, 50), 16.293257626753618, 1e-12, rel=True)
        assert_close(self, C.sd_from_median(100, 10, 22, 0, 50), 9.5160500497587908, 1e-12, rel=True)

    def test_hozo_mean_small_n_vs_metafor(self):
        # metafor conv.fivenum(method = "hozo2005"): n <= 25-nél (a + 2m + b)/4 (n = 7: 15.25; n = 20: 20).
        # n > 25-nél Hozo (és a metafor) a mediánt adja; a motor --method hozo ága szándékosan a
        # (a+2m+b)/4 képletet tartja (tudásbázis: rules_S05_S07) — ezt a teszt nem rögzíti.
        self.assertEqual(C.mean_from_median(7, 9, minimum=2, maximum=41, method="hozo"), 15.25)
        self.assertEqual(C.mean_from_median(20, 15, minimum=0, maximum=50, method="hozo"), 20.0)
        self.assertEqual(C.mean_from_median(25, 15, minimum=0, maximum=50, method="hozo"), 20.0)

    def test_se_sd_from_ci_closed_form(self):
        # R: (3 - 1)/(2*qnorm(0.975)); log-skála: (log 2 - log 0.5)/(2*qnorm(0.975)); qt(0.975, 38); qnorm(0.95)
        assert_close(self, C.se_from_ci(1, 3), 0.51021345692465403, 1e-12, rel=True)
        assert_close(self, C.se_from_ci(0.5, 2, log_scale=True), 0.35365301915106706, 1e-12, rel=True)
        assert_close(self, C.se_from_ci(1, 3, df=38), 0.49397494708618661, 1e-12, rel=True)
        assert_close(self, C.se_from_ci(1, 3, level=0.90), 0.60795683191176941, 1e-12, rel=True)
        # R: sqrt(25)*(56 - 44)/(2*qt(0.975, 24)); sqrt(100)*12/(2*qt(0.975, 99))
        assert_close(self, C.sd_from_ci(44, 56, 25), 14.535598094673643, 1e-12, rel=True)
        assert_close(self, C.sd_from_ci(44, 56, 100), 30.238628871721371, 1e-12, rel=True)

    def test_t_statistic_conversions(self):
        # Cochrane 6.5.2.3: SE = |MD/t|, SD = SE/sqrt(1/n1 + 1/n2); d = t*sqrt(1/n1 + 1/n2)
        assert_close(self, C.sd_diff_from_t(2, 20, 20, 4), 2 / math.sqrt(0.1), 1e-14, rel=True)
        assert_close(self, C.sd_diff_from_t(-2.5, 12, 30, 1.7), 0.68 / math.sqrt(1 / 12.0 + 1 / 30.0), 1e-14, rel=True)
        assert_close(self, C.d_from_t(2, 20, 20), 2 * math.sqrt(0.1), 1e-14, rel=True)
        assert_close(self, C.d_from_t(-1.5, 12, 30), -1.5 * math.sqrt(1 / 12.0 + 1 / 30.0), 1e-14, rel=True)

    def test_failsafe_rosenthal_vs_metafor(self):
        # metafor fsn(yi, vi, type = "Rosenthal") a BCG RR-adatokon: 598 (alpha .05) és 293 (alpha .01);
        # a metafor felfelé kerekít, a kerekítetlen R-érték sum(zi)^2/qnorm(1 - alpha)^2 - k
        es = E.compute(load_reference()["bcg"]["rows"], "RR")
        for alpha, unrounded, fsnum in ((0.05, 597.72885509664468, 598), (0.01, 292.318863326756, 293)):
            r = B.failsafe_n_rosenthal(es.yi, es.vi, alpha=alpha)
            assert_close(self, r.N, unrounded, 1e-10, "fsn alpha=%g" % alpha, rel=True)
            self.assertEqual(math.ceil(r.N), fsnum)
            self.assertEqual(r.k, 13)


class TestConvertCLINumeric(unittest.TestCase):
    """TS-02: a `convert` fajták számértéke (nem csak a kulcs megléte) a CLI-n keresztül."""

    def conv(self, *args):
        code, so, se = run_cli("convert", *args)
        self.assertEqual(code, 0, so + se)
        return json.loads(so)

    def test_median_s3_and_hozo(self):
        r = self.conv("median", "--n", "100", "--median", "15", "--min", "0", "--q1", "10", "--q3", "22", "--max", "50")
        assert_close(self, r["mean"], 16.293257626753618, 1e-12, "Luo S3", rel=True)
        assert_close(self, r["sd"], 9.5160500497587908, 1e-12, "Wan S3", rel=True)
        r = self.conv("median", "--n", "20", "--median", "15", "--min", "0", "--max", "50", "--method", "hozo")
        self.assertEqual(r["mean"], 20.0)                                   # metafor hozo2005, n = 20
        assert_close(self, r["sd"], 13.381566530707092, 1e-12, "Wan S1", rel=True)

    def test_se_ci_combine_change(self):
        self.assertEqual(self.conv("se", "--se", "2", "--n", "25")["sd"], 10.0)
        assert_close(self, self.conv("ci", "--lower", "44", "--upper", "56", "--n", "25")["sd"],
                     14.535598094673643, 1e-12, "ci → SD", rel=True)
        r = self.conv("combine", "--n1", "10", "--m1", "5", "--sd1", "2", "--n2", "15", "--m2", "7", "--sd2", "3")
        # összes SS = 9*4 + 14*9 + 10*1.2^2 + 15*0.8^2 = 186, df = 24
        self.assertEqual(r["n"], 25)
        assert_close(self, r["mean"], 6.2, 1e-14, rel=True)
        assert_close(self, r["sd"], math.sqrt(186 / 24.0), 1e-14, rel=True)
        assert_close(self, self.conv("change", "--sd-baseline", "10", "--sd-final", "12", "--corr", "0.5")["sd_change"],
                     math.sqrt(124.0), 1e-14, "change", rel=True)

    def test_se_from_ci_variants(self):
        for args, want in ((("--lower", "1", "--upper", "3"), 0.51021345692465403),
                           (("--lower", "0.5", "--upper", "2", "--log"), 0.35365301915106706),
                           (("--lower", "1", "--upper", "3", "--df", "38"), 0.49397494708618661),
                           (("--lower", "1", "--upper", "3", "--level", "90"), 0.60795683191176941)):
            assert_close(self, self.conv("se-from-ci", *args)["se"], want, 1e-12, " ".join(args), rel=True)

    def test_t_based_kinds(self):
        assert_close(self, self.conv("sd-from-t", "--t", "2", "--n1", "20", "--n2", "20", "--md", "4")["sd"],
                     2 / math.sqrt(0.1), 1e-14, "sd-from-t", rel=True)
        r = self.conv("d-from-t", "--t", "2", "--n1", "20", "--n2", "20")
        assert_close(self, r["d"], 2 * math.sqrt(0.1), 1e-14, "d-from-t", rel=True)
        # metafor escalc("SMD", di = d, n1i = n2i = 20, vtype = "LS", correct = FALSE): vi = 0.105
        assert_close(self, r["vi"], 0.105, 1e-14, "d-from-t vi", rel=True)


# ============================================================ TS-03: relatív p-tolerancia
class TestRelativePValueChecks(unittest.TestCase):
    """TS-03: a p-típusú összevetés relatív; a 0-ra alulcsorduló farok-valószínűség hiba."""

    def test_rel_mode_has_no_absolute_floor(self):
        assert_close(self, 2.0e-26 * (1 + 1e-9), 2.0e-26, 1e-8, rel=True)
        with self.assertRaises(AssertionError):
            assert_close(self, 0.0, 1.9967645908459738e-26, 1e-8, rel=True)
        assert_close(self, 0.0, 1.9967645908459738e-26, 1e-8)        # a régi (abszolút) mód átengedte
        assert_close(self, 0.0, 0.0, 1e-8, rel=True)
        with self.assertRaises(AssertionError):
            assert_close(self, 1e-300, 0.0, 1e-8, rel=True)

    def test_tail_underflow_mutant_fails_metafor_reference(self):
        import test_metafor_reference as TMR
        chi2, zp = D.chi2_sf, D.z_two_sided_p
        flush = [mock.patch.object(D, "chi2_sf", lambda x, df: (lambda p: 0.0 if p < 1e-10 else p)(chi2(x, df))),
                 mock.patch.object(D, "z_two_sided_p", lambda z: (lambda p: 0.0 if p < 1e-13 else p)(zp(z)))]
        for p in flush:
            p.start()
            self.addCleanup(p.stop)
        loader = unittest.TestLoader()
        suite = unittest.TestSuite([loader.loadTestsFromName("test_all_tau2_estimators", TMR.TestModels),
                                    loader.loadTestsFromName("test_metaregression", TMR.TestModeratorsBias)])
        res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        self.assertEqual(len(res.failures), 2, [str(t) for t, _ in res.failures])


# ============================================================ TS-05: MetaXL csonkolt cellák
class TestMetaXLTruncatedCells(unittest.TestCase):
    """TS-05: a Khan Fig. 11.2 MetaXL-cellák fix 9 karakter szélesek (előjel nélkül 7, negatívnál 6
    tizedes), csonkolva; az Excel 'General' formátum elhagyja a záró nullát. Minden ilyen elvárás a
    valódi csonkolási intervallum közepe, a tolerancia fél egység (a súly-oszlop százalékban: ×100)."""

    CELL = re.compile(r"printed (-?\d+\.\d+)")

    def test_truncation_midpoints_use_display_width(self):
        n = 0
        for cid in ("khan_ch11_13__ihdchol_per_study_log_or", "khan_ch11_13__ihdchol_ivhet_metaxl_weights"):
            for e in CASES[cid]["expected"]:
                if "TRUNCATED" not in e.get("note", ""):
                    continue
                printed = self.CELL.search(e["note"]).group(1)
                dp = 9 - len(printed.split(".")[0]) - 1
                self.assertGreaterEqual(dp, len(printed.split(".")[1]), (cid, e["path"], printed))
                unit = 10.0 ** -dp
                x = float(printed)
                mid = x + unit / 2 if x >= 0 else x - unit / 2
                scale = 100.0 if e["path"].startswith("weights_pct") else 1.0
                tag = "%s %s %s (%s)" % (cid, e["path"], e.get("transform"), printed)
                assert_close(self, e["value"], scale * mid, 1e-12 * scale, tag)
                assert_close(self, e["tol"], scale * unit / 2, 1e-12 * scale * unit, tag)
                n += 1
        self.assertEqual(n, 115)

    def test_dropped_zero_cells_pass_at_half_unit(self):
        c = copy.deepcopy(CASES["khan_ch11_13__ihdchol_per_study_log_or"])
        res = SC.execute(c)
        for path, tr, lo in (("yi.0", "exp", 0.8123100), ("yi.6", "exp", 0.7939370), ("vi.10", None, 0.0307770),
                             ("yi.14", "exp", 0.7979520), ("yi.17", None, 0.3993860), ("yi.17", "exp", 1.4909090),
                             ("vi.20", None, 0.0668910), ("vi.20", "sqrt", 0.2586330)):
            got = SC.transform(float(SC.resolve(res, path)), tr)
            self.assertTrue(lo <= got < lo + 1e-7, (path, tr, got))
        got = SC.resolve(res, "yi.19")
        self.assertTrue(-0.570841 < got <= -0.570840, got)


# ============================================================ TS-06: IVhet rögzített τ²-tel
class TestIVhetTau2Rounded(unittest.TestCase):
    """TS-06: a könyv IVhet-láncait (kerekített τ², z = 1.96) a tau2_fixed-del fél egység
    pontossággal reprodukáljuk; a 'nincs tau2_fixed IVhet-hez' megjegyzések elavultak."""

    SIBLINGS = {"khan_ch06_07__aspirin_mi_rd_ivhet_tau2_rounded": 0.000111,
                "khan_ch06_07__schizophrenia_raw_ivhet_tau2_rounded": 0.006325,
                "khan_ch10__ivhet_tau2_rounded": 0.008633}

    def test_sibling_cases_active_and_pass(self):
        for cid, t2 in self.SIBLINGS.items():
            c = CASES[cid]
            self.assertEqual(c.get("status"), "active", cid)
            self.assertEqual(c["call_args"]["model"], "ivhet", cid)
            self.assertEqual(c["call_args"]["tau2_fixed"], t2, cid)
            self.assertAlmostEqual(D.norm_ppf(0.5 + c["call_args"]["level"] / 2), 1.96, places=12)
            self.assertEqual([r for r in SC.check_case(c) if not r[4]], [], cid)

    def test_no_tau2_rounding_allowance_left_in_ivhet_cases(self):
        for cid in ("khan_ch06_07__aspirin_mi_rd_ivhet", "khan_ch06_07__schizophrenia_raw_ivhet",
                    "khan_ch10__ivhet_fisher_z") + tuple(self.SIBLINGS):
            for e in CASES[cid]["expected"]:
                self.assertNotIn("tau^2-rounding", e.get("note", ""), (cid, e["path"]))
        stale = ("no tau2_fixed for IVhet", "ignores tau2_fixed")
        for c in CASES.values():
            texts = [c.get("note", ""), c.get("notes", "")] + [e.get("note", "") for e in c["expected"]]
            for s in stale:
                self.assertFalse(any(s in t for t in texts), (c["case_id"], s))

    def test_aspirin_tau2_rounded_beats_unrounded(self):
        """A kerekített τ²-tel a nyomtatott z (-1.88121) fél egységen belül; a kerekítetlennel nem."""
        c = copy.deepcopy(CASES["khan_ch06_07__aspirin_mi_rd_ivhet_tau2_rounded"])
        self.assertLess(abs(SC.execute(c).stat - (-1.88121)), 5e-6)
        c["call_args"].pop("tau2_fixed")
        self.assertGreater(abs(SC.execute(c).stat - (-1.88121)), 2e-3)


# ============================================================ TS-07: aktiválható known_gap-részek
class TestCheungWilsonWeightedSD(unittest.TestCase):
    """TS-07: Wilson súlyozott SD-je (sqrt(Q/Σw)) az expr-rel számolható → aktív eset; csak a −2LL
    marad known_gap; az aktív esetek megjegyzései nem 'placeholder path' / 'known gap'."""

    def test_weighted_sd_active_loglik_gap(self):
        c = CASES["cheung_guide__fixed_effect__wilson_weighted_sd"]
        self.assertEqual(c["status"], "active")
        self.assertEqual([r for r in SC.check_case(c) if not r[4]], [])
        gap = CASES["cheung_guide__fixed_effect__loglik"]
        self.assertEqual(gap["status"], "known_gap")
        self.assertEqual([e["path"] for e in gap["expected"]], ["minus2LL"])
        self.assertNotIn("cheung_guide__fixed_effect__loglik_wilson_sd", CASES)

    def test_active_case_notes_are_current(self):
        for c in CASES.values():
            if not SC.is_active(c):
                continue
            for e in c["expected"]:
                self.assertNotIn("placeholder path", e.get("note", ""), (c["case_id"], e.get("path")))
            for text in (c.get("note", ""), c.get("notes", "")):
                for ref in re.findall(r"known gap: (\w+)", text):
                    self.assertEqual(CASES[ref].get("status"), "known_gap", (c["case_id"], ref))

    def test_simplypsych_raw_weight_direct(self):
        c = CASES["simplypsych_guide__fe_inverse_variance_weight"]
        paths = [e["path"] for e in c["expected"]]
        self.assertIn("weights_raw.0", paths)
        self.assertIn("sum_weights", paths)
        self.assertNotIn("not raw 1/vi weights", c["note"])


# ============================================================ TS-08: projektnapló és repó-KB
class TestProjectLogUsesTempKB(unittest.TestCase):
    """TS-08: METAELEMZES_KB-vel a `project log --kb` ellenőrzése az ideiglenes tudásbázist használja
    (hiányzó fájlnál ott építi fel), a repó tudasbazis.sqlite-ját nem olvassa és nem írja."""

    def test_cli_with_env_kb_leaves_repo_kb_untouched(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        repo_db = os.path.join(kb.KB_DIR, "tudasbazis.sqlite")

        def state():
            try:
                st = os.stat(repo_db)
                return st.st_mtime_ns, st.st_size
            except OSError:
                return None

        before = state()
        db = os.path.join(tmp, "kb.sqlite")
        env = dict(os.environ, METAELEMZES_KB=db, PYTHONDONTWRITEBYTECODE="1")
        ma = os.path.join(ROOT, "ma.py")
        proj = os.path.join(tmp, "proj")
        for args in (["project", "init", proj, "--title", "Teszt"],
                     ["project", "log", proj, "--agent", "planner", "--decision", "REML", "--kb", "V011,NINCS-999"]):
            p = subprocess.run([sys.executable, ma] + args, env=env, cwd=tmp, capture_output=True, text=True,
                               timeout=300)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(os.path.exists(db))                    # a változó szerinti helyen épült fel
        self.assertEqual(state(), before)
        con = projekt.connect(proj)
        try:
            row = tuple(con.execute("SELECT kb_refs, kb_unverified FROM decision").fetchone())
        finally:
            con.close()
        self.assertEqual(row, ("V011,NINCS-999", "NINCS-999"))


# ============================================================ TS-09: esetállapot
class TestCaseStatusFilter(unittest.TestCase):
    """TS-09: ismeretlen status (pl. elírt 'Active') mindkét futtatóban hiba; a szűrő közös."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        c = copy.deepcopy(CASES["simplypsych_guide__fe_inverse_variance_weight"])
        c.pop("_file", None)
        c["case_id"] = "zz_typo_case"
        c["status"] = "Active"
        c["expected"][0]["value"] = 999
        self.path = os.path.join(self.tmp, "zz_typo.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump([c], fh)

    def test_shared_predicate(self):
        self.assertTrue(SC.is_active({"case_id": "a"}))
        self.assertTrue(SC.is_active({"case_id": "a", "status": "active"}))
        self.assertFalse(SC.is_active({"case_id": "a", "status": "known_gap"}))
        for bad in ("Active", "knowngap", "gap", ""):
            with self.assertRaises(ValueError):
                SC.is_active({"case_id": "a", "status": bad})

    def test_unittest_runner_rejects_typo(self):
        import test_source_examples as TSE
        with mock.patch.object(TSE, "load_cases", lambda: SC.load_cases(self.tmp)):
            for name in ("test_cases", "test_case_metadata"):
                res = unittest.TestResult()
                TSE.TestSourceExamples(name).run(res)
                self.assertEqual(len(res.failures), 1, name)
                self.assertIn("zz_typo_case", res.failures[0][1], name)

    def test_cli_runner_rejects_typo(self):
        p = subprocess.run([sys.executable, os.path.join(HERE, "source_cases.py"), self.path],
                           capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("ismeretlen status", p.stdout)
        self.assertIn("0 aktív", p.stdout)


if __name__ == "__main__":
    unittest.main()
