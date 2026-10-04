# -*- coding: utf-8 -*-
"""Regressziós tesztek a B-köteg (bemeneti réteg és adatvalidálás) javításaihoz.

Minden teszt egy review-megállapításra hivatkozik (az azonosító a teszt docstringjében).
A metafor-értékek: R metafor 4.4 (escalc / rma), a teszt mellett idézve.
"""
import argparse
import io
import json
import math
import os
import shlex
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _helpers import ROOT, assert_close
from metaelemzes import cli, conversions as C, effect_sizes as E, projekt, tableio, validate as V

EXAMPLES = os.path.join(ROOT, "peldak")


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:      # argparse-hiba
            code = exc.code
    return code, buf.getvalue(), err.getvalue()


class _TmpMixin(object):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, text, enc="utf-8"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding=enc, newline="") as fh:
            fh.write(text)
        return p

    def write_bytes(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def codes(self, findings, code):
        return [f for f in findings if f["code"] == code]


# ----------------------------------------------------------------- RD korrekció
class TestRiskDifferenceCorrection(unittest.TestCase):
    """rd-no-cc-but-methods-claims-it"""
    ROWS = [{"study": "S1", "e1": 0, "n1": 20, "e2": 4, "n2": 22},
            {"study": "S2", "e1": 3, "n1": 30, "e2": 0, "n2": 25},
            {"study": "S3", "e1": 10, "n1": 10, "e2": 7, "n2": 9},
            {"study": "S4", "e1": 5, "n1": 50, "e2": 5, "n2": 50},
            {"study": "S5", "e1": 8, "n1": 40, "e2": 12, "n2": 40}]

    def test_cc_to_all_matches_metafor(self):
        # metafor 4.4: escalc("RD", ..., to="all")
        want = [(-0.1718426501035, 0.00794907021686), (0.0936724565757, 0.00395626255800),
                (0.2045454545455, 0.02269440270473), (0.0, 0.00377305862753),
                (-0.0975609756098, 0.00917717386573)]
        es = E.compute(self.ROWS, "RD", cc_to="all")
        for (y, v), gy, gv, note in zip(want, es.yi, es.vi, es.notes):
            assert_close(self, gy, y, 1e-10)
            assert_close(self, gv, v, 1e-10)
            self.assertIn("folytonossági korrekció", note)

    def test_default_is_uncorrected_and_not_claimed(self):
        # alapértelmezés (only0): Cochrane 10.4.4.1 — nincs korrekció; = metafor to="none"
        want = [(-0.181818181818, 0.00676183320811), (0.1, 0.003), (0.222222222222, 0.01920438957476)]
        es = E.compute(self.ROWS, "RD")
        for (y, v), gy, gv, note in zip(want, es.yi, es.vi, es.notes):
            assert_close(self, gy, y, 1e-10)
            assert_close(self, gv, v, 1e-10)
            self.assertEqual(note, "")
        # a V009 ("korrekció kerül alkalmazásra") nem állít valótlant RD-nél
        self.assertFalse([f for f in V.validate(self.ROWS, "RD") if f["code"] == "V009"])
        self.assertTrue([f for f in V.validate(self.ROWS, "OR") if f["code"] == "V009"])
        self.assertTrue([f for f in V.validate(self.ROWS, "RD", options={"cc_to": "all"}) if f["code"] == "V009"])


# ------------------------------------------------------------ oszlopnevek
class TestColumnResolution(_TmpMixin, unittest.TestCase):
    """column-alias-breaks-cli-options, aliased-column-options-broken, cumulative-subgroup-column-silent"""

    def test_resolve_original_header_alias_and_case(self):
        rows, meta = tableio.read_table(os.path.join(EXAMPLES, "bcg_oltas_RR.csv"))
        for name in ("év", "Év", "ev", "year", "YEAR", "évszám"):
            self.assertEqual(tableio.resolve_column(name, meta), "year", name)
        self.assertEqual(tableio.resolve_column("vizsgálat", meta), "study")
        self.assertEqual(tableio.resolve_column("Szélesség", meta), "szélesség")
        self.assertEqual(tableio.resolve_column("allokacio", meta), "allokáció")
        with self.assertRaises(ValueError) as cm:
            tableio.resolve_column("nincs_ilyen", meta)
        self.assertIn("ismeretlen oszlop", str(cm.exception))
        self.assertIn("év (= year)", str(cm.exception))

    def test_filters_use_original_header(self):
        rows, meta = tableio.read_table(os.path.join(EXAMPLES, "bcg_oltas_RR.csv"))
        rep = []
        out = tableio.apply_filters(rows, exclude=["vizsgálat=Aronson 1948"], meta=meta, report=rep)
        self.assertEqual(len(out), 12)
        out = tableio.apply_filters(rows, exclude=["év=1948"], meta=meta, report=rep)
        self.assertEqual(len(out), 12)
        self.assertEqual(rep[-1]["removed_labels"], ["Aronson 1948"])
        with self.assertRaises(ValueError):
            tableio.apply_filters(rows, exclude=["nincs_ilyen=1"], meta=meta)
        with self.assertRaises(ValueError):
            tableio.apply_filters(rows, exclude=["rossz szűrő"], meta=meta)

    def test_cli_exclude_original_header(self):
        code, so, se = run_cli("analyze", "--data", os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), "--measure", "RR",
                               "--exclude", "év=1948", "--out", os.path.join(self.tmp, "o"), "--no-plots")
        self.assertEqual(code, 0, so + se)
        self.assertIn("k = 12", so)
        self.assertIn("1 sor kizárva — Aronson 1948", so)
        code, so, se = run_cli("analyze", "--data", os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), "--measure", "RR",
                               "--exclude", "nincs=1", "--out", os.path.join(self.tmp, "o2"), "--no-plots")
        self.assertEqual(code, 1)
        self.assertIn("ismeretlen oszlop", se)


# ---------------------------------------------------------------- szűrők
class TestFilters(_TmpMixin, unittest.TestCase):
    """filters-numeric-and-synonyms-noop, filters-silent-noop, exclude-rob-high-silent-noop"""
    CSV = ("study,yi,vi,year,estimated,rob\nA,0.1,0.1,2005,1,magas\nB,0.3,0.2,2006,0,low\n"
           "C,0.5,0.1,2007,igen,low\nD,0.7,0.1,2008,yes,High risk\nE,0.2,0.05,2009,nem,some concerns\n")

    def setUp(self):
        _TmpMixin.setUp(self)
        self.rows, self.meta = tableio.read_table(self.write("f.csv", self.CSV))

    def labels(self, **kw):
        return [r["study"] for r in tableio.apply_filters(self.rows, meta=self.meta, **kw)]

    def test_numeric_values(self):
        self.assertEqual(self.labels(exclude=["year=2005"]), ["B", "C", "D", "E"])
        self.assertEqual(self.labels(exclude=["year=2005.0"]), ["B", "C", "D", "E"])
        self.assertEqual(self.labels(include=["year=2007"]), ["C"])

    def test_yes_no_synonyms(self):
        for v in ("1", "igen", "yes", "y", "i", "true", "1.0", "IGEN"):
            self.assertEqual(self.labels(exclude=["estimated=" + v]), ["B", "E"], v)
        self.assertEqual(self.labels(include=["estimated=0"]), ["B", "E"])
        self.assertEqual(self.labels(include=["Estimated=nem"]), ["B", "E"])

    def test_rob_synonyms(self):
        for v in ("high", "magas", "High risk", "serious", "súlyos"):
            self.assertEqual(self.labels(exclude=["rob=" + v]), ["B", "C", "E"], v)
        self.assertEqual(self.labels(include=["rob=alacsony"]), ["B", "C"])
        for v in ("some", "unclear", "közepes", "moderate"):
            self.assertEqual(self.labels(include=["rob=" + v]), ["E"], v)
        # a torzítás-oszlop álnévvel ('torzítás', 'RoB 2') is ugyanígy szűrhető
        rows, meta = tableio.read_table(self.write("g.csv", "study;yi;vi;RoB 2\nA;0.1;0.1;magas\nB;0.2;0.1;low\n"))
        self.assertEqual([r["study"] for r in tableio.apply_filters(rows, exclude=["rob=high"], meta=meta)], ["B"])
        self.assertEqual([r["study"] for r in tableio.apply_filters(rows, exclude=["RoB 2=high"], meta=meta)], ["B"])

    def test_report(self):
        rep = []
        tableio.apply_filters(self.rows, exclude=["rob=high", "year=1999"], include=["estimated=0"],
                              meta=self.meta, report=rep)
        self.assertEqual([(r["mode"], r["removed"]) for r in rep], [("include", 3), ("exclude", 0), ("exclude", 0)])
        self.assertEqual(rep[0]["removed_labels"], ["A", "C", "D"])
        self.assertEqual(rep[0]["filter"], "estimated=0")

    def test_validator_and_filter_agree(self):
        f = V.validate(self.rows, "GEN", self.meta)
        v18 = sorted(x["study"] for x in f if x["code"] == "V018")
        v19 = sorted(x["study"] for x in f if x["code"] == "V019")
        self.assertEqual(v18, ["A", "C", "D"])
        self.assertEqual(v19, ["A", "D"])
        kept = self.labels(exclude=["estimated=igen", "rob=high"])
        self.assertEqual(sorted(set("ABCDE") - set(kept)), sorted(set(v18) | set(v19)))

    def test_cli_warns_on_noop_and_k0_is_error(self):
        p = self.write("f2.csv", self.CSV)
        code, so, _ = run_cli("analyze", "--data", p, "--measure", "GEN", "--exclude", "year=1999",
                              "--out", os.path.join(self.tmp, "o"), "--no-plots")
        self.assertEqual(code, 0)
        self.assertIn("FIGYELEM", so)
        with open(os.path.join(self.tmp, "o", "results.json"), encoding="utf-8") as fh:
            res = json.load(fh)
        self.assertEqual(res["options"]["filter_report"][0]["removed"], 0)
        code, so, se = run_cli("analyze", "--data", p, "--measure", "GEN", "--include", "year=1999",
                               "--out", os.path.join(self.tmp, "o3"), "--no-plots")
        self.assertEqual(code, 1)
        self.assertNotIn("V001", so)
        with open(os.path.join(self.tmp, "o3", "results.json"), encoding="utf-8") as fh:
            res = json.load(fh)
        self.assertFalse([x for x in res["validation"]["findings"] if x["code"] == "V001"])


# --------------------------------------------------- hibás sorok kizárása
class TestErrorRowsExcluded(unittest.TestCase):
    """validation-errors-not-excluded, error-findings-not-excluded"""

    def test_binary_error_rows_blocked(self):
        rows = [{"study": "A", "e1": 4, "n1": 123, "e2": 11, "n2": 139},
                {"study": "B", "e1": 6, "n1": 306, "e2": 29, "n2": 303},
                {"study": "Empty arm", "e1": 0, "n1": 0, "e2": 9, "n2": 100},
                {"study": "C", "e1": 3, "n1": 231, "e2": 11, "n2": 220},
                {"study": "Fractional", "e1": 2.5, "n1": 40.5, "e2": 7, "n2": 40}]
        f = V.validate(rows, "OR")
        self.assertEqual(V.blocking_labels(f), {"Empty arm", "Fractional"})
        es = E.compute(rows, "OR", skip_labels=V.blocking_labels(f))
        self.assertEqual(es.labels, ["A", "B", "C"])
        self.assertEqual(es.row_index, [0, 1, 3])
        reasons = dict(es.excluded)
        self.assertTrue(reasons["Empty arm"].startswith("validálási hiba"))
        es = E.compute(rows, "OR", skip_labels=V.blocking_reasons(f))
        self.assertEqual(dict(es.excluded)["Fractional"], "validálási hiba: V004, V006")
        self.assertIn("korrelációnál n < 4", V.RULES["V004"][2])

    def test_correlation_small_n_matches_metafor(self):
        # metafor 4.4: escalc("COR", ni <= 3) → vi = NA; rma: k = 4, 0.2270 [0.1326; 0.3214]
        rows = [{"study": s, "r": r, "n": n} for s, r, n in
                (("B", 0.2, 100), ("C", 0.25, 120), ("D", 0.15, 90), ("E", 0.3, 80), ("A", 0.9, 3))]
        es = E.compute(rows, "COR")
        self.assertEqual(es.labels, ["B", "C", "D", "E"])
        f = V.validate(rows, "COR")
        self.assertEqual(V.blocking_labels(f), {"A"})
        w = [1 / v for v in es.vi]
        est = sum(wi * y for wi, y in zip(w, es.yi)) / sum(w)   # tau² = 0 (metafor REML)
        assert_close(self, est, 0.227013278117, 1e-9)

    def test_row_index_with_duplicate_labels(self):
        rows = [{"study": "X", "yi": 0.1, "vi": 0.1}, {"study": "Y", "yi": 0.2, "vi": None},
                {"study": "X", "yi": 0.3, "vi": 0.2}]
        es = E.compute(rows, "GEN")
        self.assertEqual(es.row_index, [0, 2])


# ------------------------------------------------------------- --level
class TestLevelArgument(_TmpMixin, unittest.TestCase):
    """level-not-validated"""

    def test_level_type(self):
        self.assertEqual(cli._level("0.9"), 0.9)
        self.assertAlmostEqual(cli._level("95"), 0.95)
        self.assertAlmostEqual(cli._level("0,9"), 0.9)
        for bad in ("1", "0", "-0.1", "100", "abc"):
            with self.assertRaises(argparse.ArgumentTypeError):
                cli._level(bad)

    def test_cli(self):
        data = os.path.join(EXAMPLES, "normand1999_folytonos.csv")
        code, so, se = run_cli("analyze", "--data", data, "--measure", "MD", "--level", "95",
                               "--out", os.path.join(self.tmp, "a"), "--no-plots")
        self.assertEqual(code, 0, se)
        code2, so2, _ = run_cli("analyze", "--data", data, "--measure", "MD", "--level", "0.95",
                                "--out", os.path.join(self.tmp, "b"), "--no-plots")
        self.assertEqual(so.splitlines()[-1], so2.splitlines()[-1])
        for lv in ("1", "0"):
            code, _, se = run_cli("analyze", "--data", data, "--measure", "MD", "--level", lv,
                                  "--out", os.path.join(self.tmp, "c"), "--no-plots")
            self.assertEqual(code, 2)
            self.assertIn("--level", se)
        code, _, se = run_cli("convert", "ci", "--lower", "1", "--upper", "3", "--n", "50", "--level", "1")
        self.assertEqual(code, 2)


# ------------------------------------------------------------ konverziók
class TestConversionInputChecks(unittest.TestCase):
    """convert-negative-sd"""

    def test_order_and_n(self):
        with self.assertRaises(C.ConversionError):
            C.sd_from_median(50, q1=8, q3=3)
        with self.assertRaises(C.ConversionError):
            C.sd_from_median(50, minimum=10, maximum=1)
        with self.assertRaises(C.ConversionError):
            C.mean_from_median(50, 20, q1=3, q3=8)          # medián a kvartiliseken kívül
        with self.assertRaises(C.ConversionError):
            C.sd_from_median(1, q1=3, q3=8)
        with self.assertRaises(C.ConversionError):
            C.sd_from_ci(3, 1, 50)
        with self.assertRaises(C.ConversionError):
            C.sd_from_ci(1, 3, 50, level=1.0)
        self.assertGreater(C.sd_from_median(50, q1=3, q3=8, median=5), 0)

    def test_cli_messages(self):
        code, _, se = run_cli("convert", "median", "--n", "50", "--median", "5", "--q1", "8", "--q3", "3")
        self.assertEqual(code, 1)
        self.assertIn("min <= Q1 <= medián <= Q3 <= max", se)
        code, _, se = run_cli("convert", "median", "--median", "5", "--q1", "3", "--q3", "8")
        self.assertEqual(code, 1)
        self.assertIn("--n", se)
        self.assertNotIn("TypeError", se)
        code, _, se = run_cli("convert", "median", "--n", "1", "--median", "5", "--q1", "3", "--q3", "8")
        self.assertEqual(code, 1)
        self.assertNotIn("ZeroDivisionError", se)


# -------------------------------------------------------- címkék és értékek
class TestLabelsKeptAsWritten(_TmpMixin, unittest.TestCase):
    """numeric-study-labels-mangled, id-column-hijacks-labels"""

    def test_numeric_labels(self):
        rows, meta = tableio.read_table(self.write("lab.csv", "study,yi,vi\n0,0.1,0.1\n1,0.3,0.2\n2005,0.5,0.1\n001,0.2,0.1\n"))
        es = E.compute(rows, "GEN")
        self.assertEqual(es.labels, ["0", "1", "2005", "001"])
        f = V.validate(rows, "GEN", meta)
        self.assertFalse([x for x in f if x["code"] == "V007"])
        code, so, _ = run_cli("es", "--data", os.path.join(self.tmp, "lab.csv"), "--measure", "GEN",
                              "--out", os.path.join(self.tmp, "es.csv"))
        with open(os.path.join(self.tmp, "es.csv"), encoding="utf-8") as fh:
            self.assertEqual([ln.split(",")[0] for ln in fh.read().splitlines()[1:]], ["0", "1", "2005", "001"])

    def test_subgroup_codes_stay_distinct_and_numbers_keep_text(self):
        rows, _ = tableio.read_table(self.write("g.csv", "study,yi,vi,subgroup,grp,year\n"
                                                         "A,0.1,0.1,01,01,2005\nB,0.2,0.1,1,1,2006\nC,0.3,0.1,1.0,1.0,2007\n"))
        self.assertEqual([r["subgroup"] for r in rows], ["01", "1", "1.0"])
        # nem kanonikus oszlop: szám (moderátorként float), de str() az eredeti szöveg
        self.assertEqual([str(r["grp"]) for r in rows], ["01", "1", "1.0"])
        self.assertTrue(all(isinstance(r["grp"], float) for r in rows))
        self.assertEqual(rows[0]["grp"] + 1, 2.0)
        self.assertEqual(str(rows[0]["year"]), "2005")
        self.assertEqual(json.dumps(rows[0]["year"]), "2005.0")

    def test_id_column_does_not_hijack_label(self):
        p = self.write("id.csv", "ID;Szerző;Év;esemény1;n1;esemény2;n2\n1;Aronson;1948;4;123;11;139\n"
                                 "2;Ferguson;1949;6;306;29;303\n3;Rosenthal;1960;3;231;11;220\n")
        rows, meta = tableio.read_table(p)
        self.assertEqual(meta["mapping"]["Szerző"], "study")
        self.assertEqual(meta["mapping"]["ID"], "ID")
        self.assertEqual(E.compute(rows, "RR").labels, ["Aronson", "Ferguson", "Rosenthal"])
        # ha nincs más címkeoszlop, az ID a címke
        self.assertEqual(tableio.canonical_columns(["ID", "yi", "vi"])["ID"], "study")

    def test_header_normalisation_and_metafor_ci(self):
        m = tableio.canonical_columns(["RoB 2", "x_1"])
        self.assertEqual(m["RoB 2"], "rob")
        self.assertEqual(tableio.canonical_columns(["RoB-2"])["RoB-2"], "rob")
        self.assertEqual(tableio.canonical_columns(["rob_2"])["rob_2"], "rob")
        m = tableio.canonical_columns(["slab", "ai", "n1i", "ci", "n2i"])
        self.assertEqual((m["ai"], m["ci"]), ("e1", "e2"))
        self.assertEqual(tableio.canonical_columns(["study", "CI", "yi"])["CI"], "CI")
        # a 'hiba' (error) nem standard hiba
        self.assertEqual(tableio.canonical_columns(["study", "hiba"])["hiba"], "hiba")


# ---------------------------------------------------------------- V001
class TestMissingRequiredColumn(_TmpMixin, unittest.TestCase):
    """v001-keyerror-crash"""

    def test_missing_columns_reported_not_crash(self):
        for header, row, measure, col in (("study,m1,sd1,n1,m2,n2", "A,10,2,20,12,22", "MD", "sd2"),
                                          ("study,e1,n1,n2", "A,10,20,22", "OR", "e2"),
                                          ("study,x", "A,3", "PLO", "n")):
            rows, meta = tableio.read_table(self.write("m.csv", header + "\n" + row + "\n"))
            f = V.validate(rows, measure, meta)
            self.assertEqual([x["detail"] for x in f if x["code"] == "V001"], ["hiányzó oszlop: %s" % col])
            code, so, se = run_cli("validate", "--data", os.path.join(self.tmp, "m.csv"), "--measure", measure)
            self.assertEqual(code, 1)
            self.assertIn("V001", so)
            self.assertNotIn("KeyError", so + se)
        code, so, se = run_cli("validate", "--data", os.path.join(self.tmp, "m.csv"), "--measure", "PLO", "--json")
        self.assertEqual(json.loads(so)["summary"]["error"], 1)

    def test_header_only_has_no_false_v001(self):
        rows, meta = tableio.read_table(self.write("h.csv", "study,m1,sd1,n1,m2,sd2,n2\n"))
        self.assertEqual(rows, [])
        self.assertFalse([x for x in V.validate(rows, "MD", meta) if x["code"] == "V001"])


# ---------------------------------------------------------- beolvasás
class TestReadTableRobustness(_TmpMixin, unittest.TestCase):
    """ragged-row-silent-shift, thousands-separator-as-decimal, wrong-line-numbers, utf16-not-detected,
    v003-error-nonrequired-cols"""

    def test_ragged_row_is_error_and_excluded(self):
        p = self.write("r.csv", "study,m1,sd1,n1,m2,sd2,n2\nA,10.2,5.1,30,12.4,5.3,30\nB,11,5,40,14,6,40\n"
                                "C,9,4,5,35,13,5,33\nD,10.1,6.2,50,11.3,5.4,52\nE,12,5,25,15,5,24,\n")
        rows, meta = tableio.read_table(p)
        rag = [pe for pe in meta["parse_errors"] if pe.get("kind") == "ragged"]
        self.assertEqual([pe["line"] for pe in rag], [4])      # E záró üres cellája nem hiba
        self.assertIsNone(rows[2]["m1"])
        f = V.validate(rows, "MD", meta)
        self.assertEqual([x["study"] for x in f if x["code"] == "V024"], ["C"])
        self.assertIn("C", V.blocking_labels(f))
        es = E.compute(rows, "MD", skip_labels=V.blocking_labels(f))
        self.assertEqual(es.labels, ["A", "B", "D", "E"])
        code, so, _ = run_cli("validate", "--data", p, "--measure", "MD")
        self.assertEqual(code, 1)

    def test_thousands_separator(self):
        rows, meta = tableio.read_table(self.write("t.csv", 'study,m1,sd1,n1,m2,sd2,n2\nBig,120,30,"2,000",110,28,"3,000"\n'
                                                            'B,130,31,150,118,30,148\nM,128,29,"1,234,567",115,31,225\n'))
        self.assertEqual((rows[0]["n1"], rows[0]["n2"], rows[2]["n1"]), (2000.0, 3000.0, 1234567.0))
        self.assertEqual(len(meta["ambiguous"]), 2)
        f = V.validate(rows, "MD", meta)
        self.assertEqual(len([x for x in f if x["code"] == "V023"]), 2)
        self.assertFalse([x for x in f if x["code"] == "V020"])
        # mérési érték: tizedesvessző-bizonyíték nélkül kétértelmű → hiba (nem csendes 13.598)
        rows, meta = tableio.read_table(self.write("t2.csv", 'study,m1,sd1,n1,m2,sd2,n2\nX,"13,598",30,200,110,28,300\n'
                                                             'B,130,31,150,118,30,148\n'))
        self.assertIsNone(rows[0]["m1"])
        self.assertIn("kétértelmű", meta["parse_errors"][0]["note"])
        # ha a fájl máshol tizedesvesszőt használ, a '13,598' tizedestört
        rows, meta = tableio.read_table(self.write("t3.csv", 'study,m1,sd1,n1,m2,sd2,n2\nX,"13,598",30,200,110,28,300\n'
                                                             'B,"130,5",31,150,118,30,148\n'))
        self.assertEqual(rows[0]["m1"], 13.598)
        self.assertEqual(meta["parse_errors"], [])
        # pontosvesszős (európai) fájl pontos ezres tagolással
        rows, meta = tableio.read_table(self.write("t4.csv", "study;m1;sd1;n1;m2;sd2;n2\nX;120,5;30;2.000;110;28;3.000\n"))
        self.assertEqual((rows[0]["m1"], rows[0]["n1"], rows[0]["n2"]), (120.5, 2000.0, 3000.0))
        # '0.187' sosem ezres tagolás; a korreláció sem
        rows, meta = tableio.read_table(self.write("t5.csv", "study;r;n\nA;0.187;109\nB;1.000;50\n"))
        self.assertEqual((rows[0]["r"], rows[1]["r"]), (0.187, 1.0))

    def test_physical_line_numbers(self):
        p = self.write("l.csv", "study;m1;sd1;n1;m2;sd2;n2\n\nA;10;5;30;12;5;30\n;;;;;;\nB;11;5;40;14;6;40\n\n"
                                "C;9;4;35;13;x5;33\n")
        rows, meta = tableio.read_table(p)
        self.assertEqual(meta["parse_errors"][0]["line"], 7)
        self.assertEqual([r["_line"] for r in rows], [3, 5, 7])
        p = self.write("l2.csv", 'study,m1,sd1,n1,m2,sd2,n2\n"A\nmulti",10,5,30,12,5,30\nB,11,5,40,14,6,40\n'
                                 "C,9,4,35,13,x5,33\n")
        rows, meta = tableio.read_table(p)
        self.assertEqual(meta["parse_errors"][0]["line"], 5)
        f = V.validate(rows, "MD", meta)
        self.assertIn("5. sor", [x for x in f if x["code"] == "V003"][0]["detail"])

    def test_utf16(self):
        text = "study\tm1\tsd1\tn1\tm2\tsd2\tn2\r\nA\t1,5\t0,5\t20\t1,2\t0,4\t22\r\nB\t1,6\t0,5\t30\t1,1\t0,4\t32\r\n"
        for enc, data in (("utf-16", text.encode("utf-16")), ("utf-16-le", text.encode("utf-16-le")),
                          ("utf-16-be", text.encode("utf-16-be"))):
            rows, meta = tableio.read_table(self.write_bytes("u.txt", data))
            self.assertEqual(meta["delimiter"], "\t", enc)
            self.assertEqual([r["study"] for r in rows], ["A", "B"], enc)
            self.assertEqual(rows[0]["m1"], 1.5, enc)
            self.assertEqual(meta["parse_errors"], [], enc)

    def test_nonrequired_column_parse_errors_are_warnings(self):
        p = self.write("y.csv", "study;év;m1;sd1;n1;m2;sd2;n2\nSmith;2001a;10;5;30;12;5;30\n"
                                "Smith2;2001b;11;5;40;14;6;40\nDoe;in press;9;4;35;13;5;33\n")
        rows, meta = tableio.read_table(p)
        self.assertEqual((rows[0]["year"], str(rows[0]["year"])), (2001.0, "2001a"))
        f = V.validate(rows, "SMD", meta)
        self.assertEqual(V.summarize(f)["error"], 0)
        self.assertEqual([x["study"] for x in f if x["code"] == "V021"], ["Doe"])
        code, _, _ = run_cli("validate", "--data", p, "--measure", "SMD")
        self.assertEqual(code, 0)
        # kötelező oszlop nem szám értéke továbbra is vizsgálat-szintű hiba
        rows, meta = tableio.read_table(self.write("z.csv", "study,e1,n1,e2,n2\nA,3,abc,4,20\nB,3,20,4,20\n"))
        f = V.validate(rows, "OR", meta)
        self.assertEqual([x["study"] for x in f if x["code"] == "V003"], ["A"])
        self.assertEqual(V.blocking_labels(f), {"A"})

    def test_filtered_rows_parse_errors_ignored(self):
        rows, meta = tableio.read_table(self.write("q.csv", "study,e1,n1,e2,n2\nA,3,abc,4,20\nB,3,20,4,20\nC,2,20,5,20\n"))
        kept = tableio.apply_filters(rows, exclude=["study=A"], meta=meta)
        self.assertFalse([x for x in V.validate(kept, "OR", meta) if x["code"] == "V003"])


# ------------------------------------------------------------- k-szabályok
class TestKRulesUseAnalysableK(unittest.TestCase):
    """k-rules-ignore-exclusions"""

    def test_double_zero_reduces_k(self):
        rows = [{"study": s, "e1": a, "n1": b, "e2": c, "n2": d} for s, a, b, c, d in
                (("A", 0, 50, 0, 50), ("B", 0, 40, 0, 42), ("C", 3, 60, 8, 61), ("D", 2, 70, 6, 69),
                 ("E", 4, 55, 9, 57), ("F", 1, 45, 5, 44))]
        f = V.validate(rows, "OR")
        self.assertEqual([x["detail"] for x in f if x["code"] == "V016"], ["k = 4"])
        self.assertTrue([x for x in f if x["code"] == "V015"])
        self.assertFalse([x for x in f if x["code"] == "V022"])     # a V008 már elmondja
        f = V.validate(rows, "OR", options={"drop00": False})
        self.assertEqual([x["detail"] for x in f if x["code"] == "V016"], ["k = 6"])
        f = V.validate(rows, "RD")
        self.assertEqual([x["detail"] for x in f if x["code"] == "V016"], ["k = 6"])

    def test_engine_exclusions_are_reported(self):
        rows = [{"study": s, "m1": a, "sd1": 5, "n1": 30, "m2": b, "sd2": 5, "n2": 30} for s, a, b in
                (("A", 10, 12), ("B", -1, 14), ("C", 9, 0), ("D", 10, 11), ("E", 12, 15))]
        f = V.validate(rows, "ROM")
        self.assertEqual(sorted(x["study"] for x in f if x["code"] == "V022"), ["B", "C"])
        self.assertTrue([x for x in f if x["code"] == "V015" and x["detail"].startswith("k = 3")])


# ------------------------------------------------------------ heurisztikák
class TestHeuristics(_TmpMixin, unittest.TestCase):
    """v011-false-positive-normand, v012-false-positive-change-scores, v017-not-clearable"""

    def test_v011_normand_no_false_positive(self):
        rows, meta = tableio.read_table(os.path.join(EXAMPLES, "normand1999_folytonos.csv"))
        for measure in ("SMD", "MD"):
            self.assertFalse([x for x in V.validate(rows, measure, meta) if x["code"] == "V011"])

    def test_v011_true_positives_kept(self):
        base = [{"study": "s%d" % i, "m1": 50 + i, "sd1": 10, "n1": 100, "m2": 48, "sd2": 10, "n2": 100} for i in range(5)]
        both = base + [{"study": "both", "m1": 52, "sd1": 1.0, "n1": 100, "m2": 47, "sd2": 1.0, "n2": 100}]
        one = base + [{"study": "one", "m1": 52, "sd1": 10, "n1": 100, "m2": 47, "sd2": 1.0, "n2": 100}]
        self.assertEqual(len([x for x in V.validate(both, "MD") if x["code"] == "V011"]), 2)
        hits = [x for x in V.validate(one, "MD") if x["code"] == "V011"]
        self.assertEqual([x["study"] for x in hits], ["one"])
        self.assertIn("sd2=1", hits[0]["detail"])

    def test_v012_change_scores_no_false_positive(self):
        rows = [{"study": s, "m1": a, "sd1": b, "n1": c, "m2": d, "sd2": e, "n2": g} for s, a, b, c, d, e, g in
                (("A", -0.9, 1.0, 60, -0.30, 1.0, 58), ("B", -1.1, 1.1, 80, -0.20, 1.0, 82),
                 ("C", -0.7, 0.9, 45, 0.02, 1.0, 44), ("D", -0.6, 1.0, 70, -0.10, 0.9, 71),
                 ("E", -1.0, 1.2, 90, -0.03, 1.1, 88))]
        self.assertFalse([x for x in V.validate(rows, "MD") if x["code"] == "V012"])

    def test_v012_true_positives_kept(self):
        rows = [{"study": "s%d" % i, "m1": 5.5 + i * 0.1, "sd1": 1, "n1": 30, "m2": 5.2, "sd2": 1, "n2": 30}
                for i in range(4)]
        hi = rows + [{"study": "mg/dL", "m1": 99, "sd1": 18, "n1": 30, "m2": 94, "sd2": 18, "n2": 30}]
        self.assertTrue([x for x in V.validate(hi, "MD") if x["code"] == "V012" and x["study"] == "mg/dL"])
        mg = [{"study": "s%d" % i, "m1": 99 + i, "sd1": 18, "n1": 30, "m2": 94, "sd2": 18, "n2": 30} for i in range(4)]
        lo = mg + [{"study": "mmol", "m1": 5.5, "sd1": 1, "n1": 30, "m2": 5.2, "sd2": 1, "n2": 30}]
        self.assertTrue([x for x in V.validate(lo, "MD") if x["code"] == "V012" and x["study"] == "mmol"])

    def test_v017_marker_clears_and_uneven_split_still_flagged(self):
        base = "study,study_id,m1,sd1,n1,m2,sd2,n2%s\nSmith low,S1,10,5,30,12,5,%s%s\nSmith high,S1,9,5,31,12,5,%s%s\nDoe,D1,9,4,35,13,5,33%s\n"
        rows, meta = tableio.read_table(self.write("a.csv", base % ("", 15, "", 15, "", "")))
        f = [x for x in V.validate(rows, "MD", meta) if x["code"] == "V017"]
        self.assertEqual(len(f), 1)
        self.assertIn("15 + 15 = 30", f[0]["detail"])
        rows, meta = tableio.read_table(self.write("b.csv", base % ("", 15, "", 16, "", "")))
        self.assertEqual(len([x for x in V.validate(rows, "MD", meta) if x["code"] == "V017"]), 1)
        rows, meta = tableio.read_table(self.write("c.csv", base % (",kontroll_felosztva", 15, ",igen", 15, ",igen", ",")))
        self.assertFalse([x for x in V.validate(rows, "MD", meta) if x["code"] == "V017"])
        # bináris: egyenletes felosztás (8/20 → 4/10 + 4/10) is jelez, a jelölés törli
        b = [{"study": "A1", "study_id": "A", "e1": 3, "n1": 10, "e2": 4, "n2": 10},
             {"study": "A2", "study_id": "A", "e1": 2, "n1": 10, "e2": 4, "n2": 10}]
        self.assertEqual(len([x for x in V.validate(b, "OR") if x["code"] == "V017"]), 1)
        for r in b:
            r["control_split"] = "1"
        self.assertFalse([x for x in V.validate(b, "OR") if x["code"] == "V017"])


# ------------------------------------------------------------ projektnapló
class TestProjectRunLog(_TmpMixin, unittest.TestCase):
    """project-log-not-reproducible"""

    def test_replayable_command_absolute_paths_and_scale(self):
        proj = os.path.join(self.tmp, "p")
        projekt.init(proj, "T")
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            code, so, se = run_cli("analyze", "--data", "peldak/bcg_oltas_RR.csv", "--measure", "RR",
                                   "--title", "BCG oltás RR", "--out", os.path.join(self.tmp, "fo"),
                                   "--project", proj, "--no-plots")
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, se)
        con = sqlite3.connect(os.path.join(proj, "projekt.sqlite"))
        command, data_path, outdir, summary = con.execute("SELECT command, data_path, outdir, summary FROM run").fetchone()
        con.close()
        argv = shlex.split(command)
        self.assertTrue(os.path.isabs(argv[1]) and argv[1].endswith("ma.py"))
        self.assertEqual(argv[argv.index("--title") + 1], "BCG oltás RR")
        data_arg = argv[argv.index("--data") + 1]
        self.assertTrue(os.path.isabs(data_arg) and os.path.exists(data_arg))
        self.assertTrue(os.path.isabs(data_path) and os.path.exists(data_path))
        self.assertTrue(os.path.isabs(outdir))
        # a naplózott parancs más munkakönyvtárból is újrafuttatható (a program-előtagot és a
        # --project-et elhagyva, új kimeneti mappával)
        replay = argv[2:]
        i = replay.index("--project")
        del replay[i:i + 2]
        replay[replay.index("--out") + 1] = os.path.join(self.tmp, "fo2")
        os.chdir(self.tmp)
        try:
            code2, so2, _ = run_cli(*replay)
        finally:
            os.chdir(cwd)
        self.assertEqual(code2, 0)
        self.assertEqual(so.splitlines()[-1], so2.splitlines()[-1])
        s = json.loads(summary)
        self.assertEqual(s["scale"], "log")
        assert_close(self, s["estimate_display"], math.exp(s["estimate"]), 1e-12)
        assert_close(self, s["ci_display"][0], math.exp(s["ci"][0]), 1e-12)
        self.assertIn("0.4894", so)

    def test_uninitialised_project_fails_before_output(self):
        out = os.path.join(self.tmp, "out")
        code, _, se = run_cli("analyze", "--data", os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), "--measure", "RR",
                              "--out", out, "--project", os.path.join(self.tmp, "nincs"), "--no-plots")
        self.assertEqual(code, 1)
        self.assertIn("projektnapló", se)
        self.assertFalse(os.path.exists(out))

    def test_replay_command_quoting(self):
        ns = argparse.Namespace(out=None, _argv=["analyze", "--data=rel/x.csv", "--measure", "RR",
                                                "--title", "a b'c", "--pro", "pp"])
        cmd = cli._replay_command(ns, "outdir")
        argv = shlex.split(cmd)
        self.assertIn("--data=" + os.path.abspath("rel/x.csv"), argv)
        self.assertEqual(argv[argv.index("--title") + 1], "a b'c")
        self.assertEqual(argv[argv.index("--pro") + 1], os.path.abspath("pp"))
        self.assertEqual(argv[argv.index("--out") + 1], os.path.abspath("outdir"))


if __name__ == "__main__":
    unittest.main()
