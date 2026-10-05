# -*- coding: utf-8 -*-
"""v1 kettős kinyerés (metaelemzes/kettos.py; terv 3.5.5, 4.9, 6.4 X009; E6).

- Szintetikus A/B párok minden eltérés-fajtával: format_only ('12,3' / '12.3' / '12.30'; '1,204' / '1204'; 'Low' /
  'low risk of bias'), value + súgók (10×, SE/SD csere, felcserélt karok, felcserélt számjegyek, egy számjegy,
  eseményszám > n, százalék a darabszám helyén, előjel, ezres tagolás, kerekítés, glükóz-szorzó), missing_a/b,
  parse, category (szomszédos), csak A / csak B sor, tűrés, ismétlődő kulcs, év-utótag ('2001a' ≠ '2001b').
- κ kézi számolással (és az R vcd::Kappa ASE-jével), ICC(A,1) az R psych::ICC értékeivel (beégetett referencia).
- A hatás (impact) számai = a motor újrafuttatása: a vizsgálat yi-je és az összesített becslés az api.analyze-zal
  (ugyanaz a spec, a B értékével módosított táblán); szűrős spec, ki/bekerülés, csak-B sor.
- Egyeztetés: döntések (a/b/other, sorszintű), indoklás kötelező, elavult és árva döntés, feloldatlan → nincs
  tábla (X009); a konszenzus-tábla az A formátumában (tizedesvessző, ';', BOM, CRLF), a B számai átírva.
- Szerződések (szk.ma.compare-result/v1, szk.ma.consensus/v1, szk.ma.provenance/v1), projektfájlok, X009-állapot,
  S08-kapu, parancssor, a munkapad hívási konvenciója (dict-tábla, row_uids, consensus_table).
"""
import inspect
import io
import json
import math
import os
import shutil
import tempfile
import unittest

import _helpers  # noqa: F401 — a repó gyökere a sys.path-on
from metaelemzes import api, contracts, kettos as K, tableio
from test_mvp_contracts import MiniValidator

VALIDATOR = MiniValidator(contracts.registry())


def errors(doc, name):
    return VALIDATOR.errors(json.loads(json.dumps(doc)), contracts.load(name, 1))


def csv(text, bom=False, crlf=False):
    t = text.replace("\n", "\r\n") if crlf else text
    return (b"\xef\xbb\xbf" if bom else b"") + t.encode("utf-8")


def find(res, key, field):
    hit = [d for d in res["disagreements"] if d["key"] == key and d["field"] == field]
    return hit[0] if hit else None


def codes(d):
    return [h["code"] for h in d["hints"]]


# folytonos (SMD) A/B pár: a cellák minden ismert eltérés-fajtát lefednek
CONT_A = ("study_id;arm;study;m1;sd1;n1;m2;sd2;n2;rob;year\n"
          "S1;T;Alfa 2001;12,3;0,42;50;10,1;4,0;50;low;2001a\n"
          "S2;T;Béta 2005;20,0;5,0;100;18,0;5,0;100;high;2005\n"
          "S3;T;Gamma 2010;12,43;3,0;40;11,0;3,1;40;some concerns;2010\n"
          "S4;T;Delta 2012;7,5;2,5;64;6,0;2,4;64;low;2012\n"
          "S5;T;Epszilon 2014;8,0;1,8;30;9,0;2,0;30;low;2014\n"
          "S6;T;Zéta 2016;5,0;1,0;25;4,0;1,0;25;low;2016\n"
          "S7;T;Éta 2018;100;15;80;90;14;80;low;2018\n"
          "S8;T;Théta 2019;3,3;1,1;20;3,0;1,0;20;low;2019\n")
CONT_B = ("study_id,arm,study,m1,sd1,n1,m2,sd2,n2,rob,year\n"
          "S1,T,Alfa 2001,12.30,4.2,50,10.1,4,50,Low risk of bias,2001b\n"
          "S2,T,Béta 2005,20,0.5,100,18,5,100,some,2005\n"
          "S3,T,Gamma 2010,12.34,3,40,11,3.1,40,some concerns,2010\n"
          "S4,T,Delta 2012,6.0,2.4,64,7.5,2.5,64,low,2012\n"
          "S5,T,Epszilon 2014,8,1.8,30,9,2,30,,2014\n"
          "S6,T,Zéta 2016,5,1.0,25,4,1,25,low,2016\n"
          "S7,T,Éta 2018,100,15,80,90,15,80,low,2018\n"
          "S9,T,Iota 2020,1,1,10,1,1,10,low,2020\n")


class CompareKinds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = K.compare(csv(CONT_A), csv(CONT_B))

    def test_key_auto_and_pairing(self):
        r = self.res
        self.assertEqual(r["key"], ["study_id", "arm"])
        self.assertEqual(r["key_source"], "auto")
        self.assertEqual(r["summary"]["matched"], 7)
        self.assertEqual(r["only_a"], ["S8|T"])
        self.assertEqual(r["only_b"], ["S9|T"])
        self.assertEqual(len(r["pairs"]), 7)
        self.assertTrue(all(tableio.UID_RE.match(p["row_uid_a"]) and tableio.UID_RE.match(p["row_uid_b"])
                            for p in r["pairs"]))

    def test_format_only_numbers(self):
        # '12,3' (A: tizedesvessző) = '12.30' (B: tizedespont): ugyanaz a szám → csak írásmód
        d = find(self.res, "S1|T", "m1")
        self.assertEqual(d["kind"], "format_only")
        self.assertEqual((d["a"], d["b"]), ("12,3", "12.30"))
        self.assertEqual((d["a_value"], d["b_value"]), (12.3, 12.3))
        self.assertFalse(d["needs_decision"])
        self.assertIsNone(d["impact"])
        self.assertEqual(d["kb"], "V023")
        # '10,1' ↔ '10.1', '4,0' ↔ '4' is
        self.assertEqual(find(self.res, "S1|T", "m2")["kind"], "format_only")
        self.assertEqual(find(self.res, "S1|T", "sd2")["kind"], "format_only")
        self.assertEqual(self.res["pairs"][0]["cells"]["m1"], "format_only")

    def test_format_only_category(self):
        d = find(self.res, "S1|T", "rob")
        self.assertEqual(d["kind"], "format_only")
        self.assertEqual((d["a_category"], d["b_category"]), ("low", "low"))

    def test_year_suffix_is_not_format(self):
        # '2001a' és '2001b' számként mindkettő 2001, de két különböző vizsgálatot jelöl
        d = find(self.res, "S1|T", "year")
        self.assertEqual(d["kind"], "value")
        self.assertTrue(d["needs_decision"])

    def test_unit_x10(self):
        d = find(self.res, "S1|T", "sd1")
        self.assertEqual(d["kind"], "value")
        self.assertEqual(d["hint_code"], "x10")
        self.assertEqual(d["kb"], "V012")
        self.assertIn("10×", d["hint"]["hu"])

    def test_se_sd_swap(self):
        # 5.0 / 0.5 = 10 = √100: SE/SD csere (V011) — megelőzi a 10× súgót
        d = find(self.res, "S2|T", "sd1")
        self.assertEqual(d["hint_code"], "se_sd_swap")
        self.assertIn("x10", codes(d))
        self.assertEqual(d["kb"], "V011")
        self.assertIn("B", d["hint"]["hu"])          # a B kinyerő írt be SE-t

    def test_transposed_digits(self):
        d = find(self.res, "S3|T", "m1")
        self.assertEqual(d["hint_code"], "digit_transposition")

    def test_arm_swap(self):
        for f in ("m1", "sd1", "m2", "sd2"):
            d = find(self.res, "S4|T", f)
            self.assertEqual(d["hint_code"], "arms_swapped", f)

    def test_category_adjacent(self):
        d = find(self.res, "S2|T", "rob")
        self.assertEqual(d["kind"], "category")
        self.assertEqual(d["hint_code"], "category")
        self.assertIn("Szomszédos", d["hint"]["hu"])
        self.assertEqual((d["a_category"], d["b_category"]), ("high", "some concerns"))

    def test_missing_b(self):
        d = find(self.res, "S5|T", "rob")
        self.assertEqual((d["kind"], d["status"], d["b"]), ("missing_b", "missing_b", None))
        self.assertEqual(d["hint_code"], "missing")

    def test_equal_is_not_listed(self):
        # az egyező cella nem eltérés; a csak-írásmód igen (auto), a párok cellatérképe mindkettőt mutatja
        self.assertIsNone(find(self.res, "S6|T", "n1"))
        self.assertEqual(self.res["pairs"][5]["cells"]["n1"], "equal")
        self.assertEqual(find(self.res, "S6|T", "sd1")["kind"], "format_only")     # '1,0' ↔ '1.0'
        self.assertEqual(self.res["pairs"][5]["cells"]["sd1"], "format_only")

    def test_single_digit_typo(self):
        d = find(self.res, "S7|T", "sd2")
        self.assertEqual(d["hint_code"], "digit_typo")

    def test_summary_counts(self):
        s = self.res["summary"]
        self.assertEqual(s["cells_compared"], s["agree"] + s["disagree"])
        self.assertEqual(s["agree"], s["equal"] + s["format_only"] + s["within_tolerance"])
        self.assertEqual(s["disagree"], s["differs"] + s["missing_a"] + s["missing_b"])
        self.assertAlmostEqual(s["agreement_pct"], 100.0 * s["agree"] / s["cells_compared"])
        self.assertEqual(s["agreement_pct_text"]["hu"], "%.1f%%" % s["agreement_pct"])
        n_dis = sum(1 for d in self.res["disagreements"] if d["kind"] != "format_only")
        self.assertEqual(s["disagree"], n_dis)
        for f, e in s["by_field"].items():
            self.assertEqual(e["compared"], e["agree"] + e["disagree"], f)
        self.assertEqual(s["by_field"]["rob"]["type"], "categorical")
        self.assertEqual(s["by_field"]["m1"]["type"], "numeric")
        self.assertEqual(s["by_field"]["study"]["type"], "text")

    def test_hint_codes_are_documented(self):
        used = {h["code"] for d in self.res["disagreements"] for h in d["hints"]}
        self.assertLessEqual(used, set(K.HINT_CODES))
        # a munkapad kezdőbarát magyarázatainak kulcsai (ma_gui extraction_dual.js HINT_WHY) a szótár elején
        self.assertEqual(K.HINT_CODES[:8], ("x10", "se_sd_swap", "arms_swapped", "format_only", "parse", "value",
                                            "category", "missing"))

    def test_contract(self):
        self.assertEqual(errors(self.res, "ma.compare-result"), [])
        self.assertIsNone(self.res["impact_info"]["measure"])
        self.assertFalse(self.res["impact_info"]["computed"])

    def test_auto_kinds_last(self):
        kinds = [d["kind"] for d in self.res["disagreements"]]
        first_auto = kinds.index("format_only")
        self.assertTrue(all(k == "format_only" for k in kinds[first_auto:]))


class CompareHints(unittest.TestCase):
    """Bináris és egyéb súgók, parse-hiba, tűrés, ismétlődő kulcs, kulcs-hibák."""

    def test_binary_hints(self):
        a = ("study;e1;n1;e2;n2\n"
             "P1;30;120;20;118\n"
             "P2;15;100;10;100\n"
             "P3;1,204;5000;900;5000\n"
             "P4;12;50;10;50\n"
             "P5;7;80;9;80\n")
        b = ("study;e1;n1;e2;n2\n"
             "P1;25;120;20;118\n"
             "P2;150;100;10;100\n"
             "P3;1204;5000;900;5000\n"
             "P4;-12;50;10;50\n"
             "P5;7;80;9;80\n")
        r = K.compare(csv(a), csv(b))
        self.assertEqual(r["key"], ["study"])
        self.assertEqual(find(r, "P1", "e1")["hint_code"], "percent_for_count")    # 30/120 = 25%
        d = find(r, "P2", "e1")
        self.assertEqual(d["hint_code"], "events_gt_n")
        self.assertEqual(d["kb"], "V006")
        # '1,204' egy ';'-s, tizedesvesszős fájlban darabszám-oszlopban: ezres tagolás → 1204 = '1204'
        self.assertEqual(find(r, "P3", "e1")["kind"], "format_only")
        self.assertEqual(find(r, "P4", "e1")["hint_code"], "sign")

    def test_thousands_decimal_and_parse(self):
        a = "study;m1;sd1;n1\nQ1;1,204;2,5;30\nQ2;4,5;1,2;30\nQ3;12,3;2,0;20\n"
        b = "study;m1;sd1;n1\nQ1;1204;2,5;30\nQ2;4,5 (SE);1,2;30\nQ3;12,34;2,0;20\n"
        r = K.compare(csv(a), csv(b))
        d = find(r, "Q1", "m1")
        self.assertEqual(d["hint_code"], "thousands_decimal")
        self.assertEqual(d["kb"], "V023")
        d = find(r, "Q2", "m1")
        self.assertEqual((d["kind"], d["hint_code"], d["kb"]), ("parse", "parse", "V003"))
        self.assertIsNone(d["b_value"])
        self.assertEqual(find(r, "Q3", "m1")["hint_code"], "rounding")

    def test_unit_factor_and_variance(self):
        a = "study,m1,sd1,n1\nG1,5.5,1.2,40\nG2,6.0,2.0,40\n"
        b = "study,m1,sd1,n1\nG1,99,1.2,40\nG2,6.0,4.0,40\n"
        r = K.compare(csv(a), csv(b))
        self.assertIn("unit_factor", codes(find(r, "G1", "m1")))
        self.assertIn("glükóz", find(r, "G1", "m1")["hint"]["hu"])
        self.assertEqual(find(r, "G2", "sd1")["hint_code"], "variance_sd")

    def test_tolerance(self):
        a = "study,m1,sd1,n1\nT1,10.00,2.0,30\nT2,10.00,2.0,30\n"
        b = "study,m1,sd1,n1\nT1,10.04,2.0,30\nT2,10.2,2.0,30\n"
        r0 = K.compare(csv(a), csv(b))
        self.assertEqual(r0["summary"]["by_field"]["m1"]["disagree"], 2)
        r = K.compare(csv(a), csv(b), tolerance={"m1": 0.05})
        self.assertEqual(r["summary"]["by_field"]["m1"]["within_tolerance"], 1)
        self.assertEqual(r["summary"]["by_field"]["m1"]["disagree"], 1)
        self.assertEqual(r["pairs"][0]["cells"]["m1"], "within_tolerance")
        self.assertIsNone(find(r, "T1", "m1"))
        r = K.compare(csv(a), csv(b), tolerance={"*": {"rel": 0.03}})
        self.assertEqual(r["summary"]["by_field"]["m1"]["within_tolerance"], 2)
        with self.assertRaises(K.KettosError):
            K.compare(csv(a), csv(b), tolerance=-1)

    def test_duplicate_key_and_missing_column(self):
        a = "study_id,m1\nX,1\nX,2\nY,3\n"
        b = "study_id,m1\nX,1\nX,5\nY,3\n"
        r = K.compare(csv(a), csv(b))
        self.assertEqual(r["key"], ["study_id"])
        self.assertEqual([p["key"] for p in r["pairs"]], ["X", "X#2", "Y"])
        self.assertEqual(find(r, "X#2", "m1")["b"], "5")
        self.assertTrue(any("ismétlődik" in w["hu"] for w in r["warnings"]))
        with self.assertRaises(K.KettosError) as cm:
            K.compare(csv(a), csv(b), key=["arm"])
        self.assertIn("arm", str(cm.exception))
        self.assertNotIn("X", str(cm.exception).split("oszlopai")[0])     # cellaérték nem kerül az üzenetbe

    def test_auto_key_with_spelled_arm_column(self):
        a = "Study ID;Arm;m1\nNCT1;T;1\nNCT1;C;2\n"
        b = "study_id;arm;m1\nNCT1;T;1\nNCT1;C;3\n"
        r = K.compare(csv(a), csv(b))
        self.assertEqual(r["key"], ["study_id", "Arm"])
        self.assertEqual(find(r, "NCT1|C", "m1")["b"], "3")

    def test_hash_in_key_is_not_a_duplicate(self):
        a = "study,m1\nTrial #3,1\nTrial #4,2\n"
        r = K.compare(csv(a), csv(a))
        self.assertEqual(r["warnings"], [])
        self.assertEqual([p["key"] for p in r["pairs"]], ["Trial #3", "Trial #4"])

    def test_key_by_original_header_and_case(self):
        a = "Study ID;Kar;m1\nNCT1;T;1\nNCT2;T;2\n"
        b = "study_id;kar;m1\nnct1;t;1\nNCT2;T;3\n"
        r = K.compare(csv(a), csv(b), key=["Study ID", "Kar"])
        self.assertEqual(r["key"], ["study_id", "Kar"])            # 'Kar' ↔ 'kar': ugyanaz az oszlop
        with self.assertRaises(K.KettosError) as cm:
            K.compare(csv(a), csv("study_id;m1\nNCT1;1\n"), key=["study_id", "Kar"])
        self.assertIn("a B táblából", str(cm.exception))
        self.assertEqual(r["summary"]["matched"], 2)          # a kulcs kis/nagybetű-független
        self.assertEqual(r["pairs"][0]["key"], "NCT1|T")       # a megjelenített kulcs az A szövege

    def test_columns_only_in_one_table_and_notes_ignored(self):
        a = "study,m1,sd1,megjegyzés\nA1,1,2,saját jegyzet\n"
        b = "study,m1,forras_oldal,megjegyzés\nA1,1,p.5,másik jegyzet\n"
        r = K.compare(csv(a), csv(b))
        s = r["summary"]
        self.assertEqual(s["fields_only_a"], ["sd1"])
        self.assertEqual(s["fields_only_b"], ["forras_oldal"])
        self.assertEqual(find(r, "A1", "sd1")["kind"], "missing_b")
        self.assertEqual(find(r, "A1", "forras_oldal")["kind"], "missing_a")
        self.assertNotIn("megjegyzés", s["by_field"])
        r2 = K.compare(csv(a), csv(b), ignore=[])
        self.assertIn("megjegyzés", r2["summary"]["by_field"])

    def test_robins_levels_are_not_merged(self):
        # ROBINS-I: a 'serious' és a 'critical' két külön szint (nem csak írásmód)
        a = "study,rob\nR1,Serious risk of bias\nR2,Low\n"
        b = "study,rob\nR1,critical\nR2,low risk\n"
        r = K.compare(csv(a), csv(b))
        self.assertEqual(find(r, "R1", "rob")["kind"], "category")
        self.assertEqual(find(r, "R2", "rob")["kind"], "format_only")


class Agreement(unittest.TestCase):
    """κ kézi számolással és R-referenciával; ICC(A,1) a psych::ICC értékeivel."""
    ROB_A = ["low", "low", "some", "high", "low", "some", "high", "high", "low", "some"]
    ROB_B = ["low", "some", "some", "high", "Low risk of bias", "low", "High", "some", "low", "some concerns"]

    def test_kappa_by_hand(self):
        a = "study,rob\n" + "".join("K%d,%s\n" % (i, v) for i, v in enumerate(self.ROB_A))
        b = "study,rob\n" + "".join("K%d,%s\n" % (i, v) for i, v in enumerate(self.ROB_B))
        r = K.compare(csv(a), csv(b))
        e = r["summary"]["by_field"]["rob"]
        # kézzel: egyező párok 7/10 (high-high 2, low-low 3, some-some 2); marginálisok A: high .3, low .4, some .3;
        # B: high .2, low .4, some .4 → pe = .06 + .16 + .12 = .34; κ = (.70 − .34) / (1 − .34) = 6/11
        self.assertAlmostEqual(e["kappa_po"], 0.7, places=12)
        self.assertAlmostEqual(e["kappa_pe"], 0.34, places=12)
        self.assertAlmostEqual(e["kappa"], 6.0 / 11.0, places=12)
        self.assertEqual(e["kappa_n"], 10)
        # R: vcd::Kappa(table(x, y))$Unweighted ASE (Fleiss, Cohen & Everitt 1969)
        self.assertAlmostEqual(e["kappa_se"], 0.219350184434098, places=10)
        z = 1.959963984540054
        self.assertAlmostEqual(e["kappa_ci"][0], 6.0 / 11.0 - z * 0.219350184434098, places=9)
        self.assertAlmostEqual(e["kappa_ci"][1], 6.0 / 11.0 + z * 0.219350184434098, places=9)
        self.assertEqual(e["categories"], ["high", "low", "some concerns"])
        self.assertEqual(e["kappa_text"]["hu"], "κ = 0.55 [0.12; 0.98]")
        self.assertEqual(e["kappa_label"]["hu"], "közepes")
        # format_only ('Low risk of bias', 'High', 'some concerns') egyezésnek számít
        self.assertEqual(e["agree"], 7)
        self.assertEqual(e["format_only"], 3)

    def test_kappa_two_categories_reference(self):
        x = "igen igen nem nem igen nem igen igen".split()
        y = "igen nem nem nem igen igen igen igen".split()
        k = K.cohen_kappa(list(zip(x, y)))
        self.assertAlmostEqual(k["kappa"], (0.75 - 0.53125) / (1 - 0.53125), places=12)
        self.assertAlmostEqual(k["se"], 0.322704619745268, places=10)          # vcd::Kappa
        self.assertEqual(k["ci"][1], 1.0)                                        # a felső határ 1-re vágva
        self.assertIsNone(K.cohen_kappa([("a", "a"), ("a", "a")])["kappa"])     # egyetlen kategória
        self.assertIsNone(K.cohen_kappa([])["kappa"])

    def test_kappa_matches_appraisal_module(self):
        try:
            from metaelemzes.appraisal import cohen_kappa as other
        except ImportError:
            self.skipTest("nincs metaelemzes.appraisal.cohen_kappa")
        pairs = list(zip(self.ROB_A, ["low", "some", "some", "high", "low", "low", "high", "some", "low", "some"]))
        a, b = K.cohen_kappa(pairs), other(pairs)
        self.assertAlmostEqual(a["kappa"], b["kappa"], places=12)
        self.assertAlmostEqual(a["se"], b["se"], places=12)

    def test_icc_reference(self):
        # R: psych::ICC(cbind(a, b), lmer = FALSE)$results["Single_random_raters", ]
        a = [4.2, 5.1, 3.3, 6.0, 2.8, 7.4, 5.5, 4.9]
        b = [4.2, 5.0, 3.3, 6.3, 2.8, 7.4, 5.9, 4.9]
        ic = K.icc_a1(list(zip(a, b)))
        self.assertAlmostEqual(ic["icc"], 0.992883397200282, places=11)
        self.assertAlmostEqual(ic["ci"][0], 0.967748432422459, places=9)
        self.assertAlmostEqual(ic["ci"][1], 0.998547217927542, places=9)
        a = [10.1, 12.4, 9.8, 15.2, 11.0, 13.3]
        b = [10.9, 12.9, 10.1, 16.0, 11.2, 14.4]                                # B szisztematikusan nagyobb
        ic = K.icc_a1(list(zip(a, b)))
        self.assertAlmostEqual(ic["icc"], 0.9515120797432, places=11)
        self.assertAlmostEqual(ic["ci"][0], 0.0913783467195497, places=9)
        self.assertAlmostEqual(ic["ci"][1], 0.994342443952342, places=9)
        self.assertEqual(K.icc_a1([(1, 1), (2, 2), (3, 3)])["icc"], 1.0)       # tökéletes egyezés
        self.assertIsNone(K.icc_a1([(1, 1)])["icc"])

    def test_icc_in_compare_and_bland_altman(self):
        a = [4.2, 5.1, 3.3, 6.0, 2.8, 7.4, 5.5, 4.9]
        b = [4.2, 5.0, 3.3, 6.3, 2.8, 7.4, 5.9, 4.9]
        ta = "study,m1\n" + "".join("I%d,%s\n" % (i, v) for i, v in enumerate(a))
        tb = "study,m1\n" + "".join("I%d,%s\n" % (i, v) for i, v in enumerate(b))
        e = K.compare(csv(ta), csv(tb))["summary"]["by_field"]["m1"]
        self.assertAlmostEqual(e["icc"], 0.992883397200282, places=11)
        diffs = [x - y for x, y in zip(a, b)]
        md = sum(diffs) / len(diffs)
        self.assertAlmostEqual(e["mean_diff"], md, places=12)
        sd = math.sqrt(sum((d - md) ** 2 for d in diffs) / (len(diffs) - 1))
        self.assertAlmostEqual(e["loa"][0], md - 1.959963984540054 * sd, places=12)
        self.assertAlmostEqual(e["max_abs_diff"], 0.4, places=12)
        self.assertEqual(e["icc_label"]["en"], "excellent")

    def test_agreement_report(self):
        a = "study,rob\n" + "".join("K%d,%s\n" % (i, v) for i, v in enumerate(self.ROB_A))
        b = "study,rob\n" + "".join("K%d,%s\n" % (i, v) for i, v in enumerate(self.ROB_B))
        r = K.compare(csv(a), csv(b))
        rep = K.agreement_report(r)
        self.assertIn("Cohen-féle κ (95% CI): rob 0.55 [0.12; 0.98].", rep["text"]["hu"])
        self.assertIn("Cohen's κ", rep["text"]["en"])
        self.assertEqual(r["agreement_text"], rep["text"])
        self.assertEqual(rep["table"][0]["field"], "rob")
        cons = K.reconcile(r, [])["consensus"]
        self.assertIn("feloldatlan", K.agreement_report(r, cons)["text"]["hu"])


# ------------------------------------------------------------------ hatás = a motor újrafuttatása
SMD_A = ("study;m1;sd1;n1;m2;sd2;n2;rob\n"
         "Alfa;12,3;4,2;50;10,1;4,0;50;low\n"
         "Béta;20,0;5,0;100;18,0;5,0;100;high\n"
         "Gamma;12,4;3,0;40;11,0;3,1;40;low\n"
         "Delta;7,5;2,5;64;6,0;2,4;64;some\n"
         "Epszilon;8,0;1,8;30;9,0;2,0;30;low\n"
         "Zéta;5,0;1,0;25;4,0;1,0;25;low\n")
SMD_B = ("study,m1,sd1,n1,m2,sd2,n2,rob\n"
         "Alfa,12.3,0.42,50,10.1,4.0,50,low\n"
         "Béta,20,5,100,18,5,100,low\n"
         "Gamma,12.4,3,40,11,3.1,40,low\n"
         "Delta,6.0,2.4,64,7.5,2.5,64,some\n"
         "Epszilon,8,1.8,30,9,2,3,low\n"
         "Zéta,5,-1,25,4,1,25,low\n"
         "Éta,10,2,30,8,2,30,low\n")


class ImpactEqualsEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = api.spec_from_argv(["analyze", "--data", "03_adatok/o1.csv", "--measure", "SMD",
                                       "--exclude", "rob=high"])
        cls.res = K.compare(csv(SMD_A), csv(SMD_B), spec=cls.spec)
        hdr, rows, _ = tableio.read_raw(raw=csv(SMD_A))
        cls.table_a = {"header": hdr, "rows": rows, "delimiter": ";"}
        cls.base = api.analyze(cls.spec, table=cls.table_a)

    def _row_y(self, view, label):
        st = [s for s in view["plot"]["studies"] if s["label"] == label]
        return st[0]["y"] if st else None

    def _variant(self, key, field):
        t = K.variant_table(self.res, key, field)
        t["delimiter"] = ";"
        return api.analyze(self.spec, table=t)

    def test_baseline_pooled_equals_analyze(self):
        b = self.res["impact_info"]["baseline"]
        p = self.base["results"]["primary"]
        self.assertEqual(b["estimate"], p["estimate"])
        self.assertEqual(b["ci_lower"], p["ci_lower"])
        self.assertEqual(b["ci_upper"], p["ci_upper"])
        self.assertEqual(b["k"], self.base["results"]["effect_sizes"]["k"])
        self.assertEqual(b["k"], 5)                                             # rob=high kizárva

    def test_study_and_pooled_impact_equal_reruns(self):
        checked = 0
        for d in self.res["disagreements"]:
            imp = d["impact"]
            if not imp or d["kind"] == "format_only":
                continue
            v = self._variant(d["key"], d["field"])
            self.assertEqual(imp["pooled_b"]["estimate"], v["results"]["primary"]["estimate"], d["field"])
            self.assertEqual(imp["pooled_b"]["k"], v["results"]["effect_sizes"]["k"], d["field"])
            self.assertEqual(imp["pooled_a"]["estimate"], self.base["results"]["primary"]["estimate"])
            if imp["yi_b"] is not None and self._row_y(v, d["key"]) is not None:
                self.assertEqual(imp["yi_b"], self._row_y(v, d["key"]), d["field"])
            if imp["yi_a"] is not None and self._row_y(self.base, d["key"]) is not None:
                self.assertEqual(imp["yi_a"], self._row_y(self.base, d["key"]), d["field"])
            checked += 1
        self.assertGreaterEqual(checked, 5)

    def test_study_yi_equals_engine_without_filter(self):
        # a kiszűrt (rob=high) Béta sora a szűrő nélküli futásban van benne: a yi akkor is a motoré
        spec = api.spec_from_argv(["analyze", "--data", "03_adatok/o1.csv", "--measure", "SMD"])
        r = K.compare(csv(SMD_A), csv(SMD_B), spec=spec)
        d = find(r, "Alfa", "sd1")
        base = api.analyze(spec, table=self.table_a)
        t = K.variant_table(r, "Alfa", "sd1")
        t["delimiter"] = ";"
        v = api.analyze(spec, table=t)
        self.assertEqual(d["impact"]["yi_a"], self._row_y(base, "Alfa"))
        self.assertEqual(d["impact"]["yi_b"], self._row_y(v, "Alfa"))
        self.assertEqual(d["impact"]["pooled_b"]["estimate"], v["results"]["primary"]["estimate"])
        self.assertIn("SMD", d["impact"]["text"]["hu"])
        self.assertIn("→", d["impact"]["pooled_text"]["hu"])
        # a B értéke (0.42) az A formátumában (tizedesvessző) került a változat-táblába
        self.assertEqual(t["rows"][0][2], "0,42")

    def test_filter_field_changes_inclusion(self):
        # Béta: rob high (A) ↔ low (B); a spec kizárja a high sorokat → a B értékével a sor bekerül
        d = find(self.res, "Béta", "rob")
        imp = d["impact"]
        self.assertIsNotNone(imp)
        self.assertTrue(imp["changes_inclusion"])
        self.assertEqual((imp["pooled_a"]["k"], imp["pooled_b"]["k"]), (5, 6))
        self.assertIn("k = 5 → 6", imp["pooled_text"]["hu"])
        self.assertIs(self.res["disagreements"][0]["impact"]["changes_inclusion"], True)   # előre kerül

    def test_not_computable_side(self):
        # Zéta: sd1 = -1 a B-nél → a sor kiesik (V005); Epszilon: n2 = 3 → a B-nél is számolható
        d = find(self.res, "Zéta", "sd1")
        imp = d["impact"]
        self.assertIsNone(imp["yi_b"])
        self.assertIn("V005", imp["reason_b"])
        self.assertTrue(imp["changes_inclusion"])
        self.assertIn("nem számolható", imp["text"]["hu"])
        self.assertIn("not computable", imp["text"]["en"])
        v = self._variant("Zéta", "sd1")
        self.assertEqual(imp["pooled_b"]["k"], v["results"]["effect_sizes"]["k"])

    def test_row_impact_only_b(self):
        ri = [x for x in self.res["row_impacts"] if x["key"] == "Éta"]
        self.assertEqual(len(ri), 1)
        imp = ri[0]["impact"]
        hdr, rows, _ = tableio.read_raw(raw=csv(SMD_A))
        rows = rows + [["Éta", "10", "2", "30", "8", "2", "30", "low"]]
        v = api.analyze(self.spec, table={"header": hdr, "rows": rows, "delimiter": ";"})
        self.assertEqual(imp["pooled_b"]["estimate"], v["results"]["primary"]["estimate"])
        self.assertEqual(imp["yi_b"], self._row_y(v, "Éta"))

    def test_material_and_order(self):
        ranks = [d["impact"]["rank"] for d in self.res["disagreements"]
                 if d["impact"] and not d["impact"]["changes_inclusion"] and d["impact"]["rank"] is not None]
        self.assertEqual(ranks, sorted(ranks, reverse=True))
        self.assertEqual(errors(self.res, "ma.compare-result"), [])

    def test_pooled_equals_analyze_for_models(self):
        # minden elsődleges modellnél (és τ²/CI-változatnál) a hatás pooled_a / pooled_b = az api.analyze becslése
        for argv in (["--model", "fixed"], ["--model", "ivhet"], ["--tau2", "DL", "--ci", "z"],
                     ["--model", "fixed", "--ci", "t"], ["--measure", "MD"], ["--measure", "ROM"]):
            spec = api.spec_from_argv(["analyze", "--data", "x.csv", "--measure", "SMD"] + argv)
            r = K.compare(csv(SMD_A), csv(SMD_B), spec=spec)
            base = api.analyze(spec, table=self.table_a)
            self.assertEqual(r["impact_info"]["baseline"]["estimate"], base["results"]["primary"]["estimate"], argv)
            self.assertEqual(r["impact_info"]["baseline"]["ci_lower"], base["results"]["primary"]["ci_lower"], argv)
            d = find(r, "Delta", "m1")
            t = K.variant_table(r, "Delta", "m1")
            t["delimiter"] = ";"
            v = api.analyze(spec, table=t)
            self.assertEqual(d["impact"]["pooled_b"]["estimate"], v["results"]["primary"]["estimate"], argv)
            self.assertEqual(d["impact"]["pooled_b"]["ci_upper"], v["results"]["primary"]["ci_upper"], argv)
            self.assertEqual(d["impact"]["yi_b"], self._row_y(v, "Delta"), argv)
            disp = v["results"]["back_transformed"]["estimate_ci"]
            self.assertEqual(d["impact"]["pooled_b"]["display"], list(disp), argv)

    def test_measure_only_study_level(self):
        r = K.compare(csv(SMD_A), csv(SMD_B), measure="SMD")
        d = find(r, "Alfa", "sd1")
        self.assertIsNone(d["impact"].get("pooled_a"))
        self.assertIsNotNone(d["impact"]["yi_b"])
        self.assertTrue(r["impact_info"]["computed"])
        self.assertFalse(r["impact_info"]["pooled"])
        self.assertIsNone(find(r, "Béta", "rob")["impact"])          # a rob nem érinti a hatásméretet

    def test_binary_ratio_display(self):
        a = "study,e1,n1,e2,n2\nB1,6,306,29,303\nB2,4,123,11,139\nB3,3,231,11,220\n"
        b = "study,e1,n1,e2,n2\nB1,60,306,29,303\nB2,4,123,11,139\nB3,3,231,11,220\n"
        spec = api.spec_from_argv(["analyze", "--data", "x.csv", "--measure", "RR"])
        r = K.compare(csv(a), csv(b), spec=spec)
        imp = find(r, "B1", "e1")["impact"]
        self.assertAlmostEqual(imp["display_a"], math.exp(imp["yi_a"]), places=12)
        self.assertTrue(imp["text"]["hu"].startswith("RR 0.20 → 2.05"))
        hdr, rows, _ = tableio.read_raw(raw=csv(b))
        v = api.analyze(spec, table={"header": hdr, "rows": rows})
        self.assertEqual(imp["pooled_b"]["estimate"], v["results"]["primary"]["estimate"])


# ------------------------------------------------------------------ egyeztetés
REC_A = "study_id;arm;m1;sd1;n1;rob\nNCT1;T;12,3;0,42;50;low\nNCT1;C;11,0;4,1;48;low\nNCT2;T;5,5;3,3;30;high\n"
REC_B = ("study_id,arm,m1,sd1,n1,rob\nNCT1,T,12.30,4.2,50,Low risk of bias\nNCT1,C,11,4.1,48,low\n"
         "NCT2,T,5.5,3.3,31,some concerns\nNCT3,T,5,1.5,10,low\n")


class Reconcile(unittest.TestCase):
    def setUp(self):
        self.a = csv(REC_A, bom=True, crlf=True)
        self.b = csv(REC_B)
        self.res = K.compare(self.a, self.b)

    def decisions(self):
        return [{"key": "NCT1|T", "field": "sd1", "chosen": "b", "reason": "Table 2: SD = 4.2"},
                {"cell": {"key": "NCT2|T", "field": "n1"}, "choice": "value", "value": "31",
                 "reason": "a CONSORT-ábra szerint 31"},
                {"key": "NCT2|T", "field": "rob", "chosen": "other", "value": "some concerns", "reason": "egyeztetés"},
                {"key": "NCT3|T", "field": "*", "chosen": "b", "reason": "a B helyesen vette fel"}]

    def test_full_reconcile_keeps_a_format(self):
        out = K.reconcile(self.res, self.decisions(), actor="SzK", now="2026-10-05T10:00:00Z")
        cons = out["consensus"]
        self.assertTrue(cons["complete"])
        self.assertEqual(cons["unresolved"], [])
        self.assertEqual(errors(cons, "ma.consensus"), [])
        self.assertEqual(cons["summary"]["decided"], 4)
        self.assertEqual(cons["summary"]["auto"], 6)          # 4× tizedesvessző ↔ pont, 12,3 ↔ 12.30, rob
        d = [x for x in cons["decisions"] if x["field"] == "sd1"][0]
        self.assertEqual((d["chosen"], d["value"], d["a"], d["b"], d["actor"], d["ts"]),
                         ("b", "4,2", "0,42", "4.2", "SzK", "2026-10-05T10:00:00Z"))
        t = out["table"]
        self.assertEqual(t["header"], ["study_id", "arm", "m1", "sd1", "n1", "rob"])
        cells = [r["cells"] for r in t["rows"]]
        self.assertEqual(cells[0], ["NCT1", "T", "12,3", "4,2", "50", "low"])     # B 4.2 → '4,2'; A '12,3' marad
        self.assertEqual(cells[2], ["NCT2", "T", "5,5", "3,3", "31", "some concerns"])
        self.assertEqual(cells[3], ["NCT3", "T", "5", "1,5", "10", "low"])        # csak-B sor, A formátumban
        data = K.render_table(t)
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))                         # BOM, mint az A-ban
        text = data.decode("utf-8-sig")
        self.assertIn("\r\n", text)
        lines = text.split("\r\n")
        self.assertEqual(lines[0], "study_id;arm;m1;sd1;n1;rob;row_uid")
        self.assertTrue(lines[2].startswith("NCT1;C;11,0;4,1;48;low;"))           # változatlan sor: A szövege
        # visszaolvasva a számok helyesek
        rows, _meta = tableio.read_table_bytes(data)
        self.assertEqual([r["sd1"] for r in rows], [4.2, 4.1, 3.3, 1.5])
        rec = {(r["row_uid"], r["field"]) for r in t["reconciled"]}
        self.assertIn((t["rows"][0]["row_uid"], "sd1"), rec)
        self.assertEqual(len([r for r in t["reconciled"] if r["level"] == "row"]), 6)

    def test_unresolved_blocks_table(self):
        out = K.reconcile(self.res, self.decisions()[:2])
        cons = out["consensus"]
        self.assertIsNone(out["table"])
        self.assertFalse(cons["complete"])
        self.assertEqual({(u["key"], u["field"]) for u in cons["unresolved"]}, {("NCT2|T", "rob"), ("NCT3|T", "*")})
        self.assertEqual(errors(cons, "ma.consensus"), [])
        draft = K.reconcile(self.res, self.decisions()[:2], allow_unresolved=True)
        self.assertEqual(len(draft["table"]["rows"]), 3)                          # a csak-B sor döntés nélkül kimarad
        self.assertEqual(draft["table"]["rows"][2]["cells"][5], "high")           # feloldatlan cella: az A-é
        with self.assertRaises(K.KettosError) as cm:
            K.consensus_table(self.a, self.b, cons)
        self.assertIn("X009", str(cm.exception))

    def test_stale_and_orphan_decisions(self):
        decs = self.decisions()
        decs[0]["a"] = "0,40"                                     # a döntéskori A-szöveg más volt → elavult
        decs[0]["b"] = "4.2"
        decs.append({"key": "NCT1|C", "field": "sd1", "chosen": "a", "reason": "régi"})   # már nem eltérés
        out = K.reconcile(self.res, decs)
        cons = out["consensus"]
        stale = [u for u in cons["unresolved"] if u.get("stale")]
        self.assertEqual([(u["key"], u["field"]) for u in stale], [("NCT1|T", "sd1")])
        self.assertEqual(cons["summary"]["stale"], 1)
        self.assertEqual([o["key"] for o in cons["orphans"]], ["NCT1|C"])
        self.assertIsNone(out["table"])

    def test_invalid_decisions(self):
        with self.assertRaises(K.KettosError) as cm:
            K.reconcile(self.res, [{"key": "NCT1|T", "field": "sd1", "chosen": "b", "reason": "  "}])
        self.assertIn("Indoklás", str(cm.exception))
        with self.assertRaises(K.KettosError):
            K.reconcile(self.res, [{"key": "NCT1|T", "field": "sd1", "chosen": "c", "reason": "x"}])
        with self.assertRaises(K.KettosError):
            K.reconcile(self.res, [{"key": "NCT1|T", "field": "sd1", "chosen": "other", "reason": "x"}])
        with self.assertRaises(K.KettosError):
            K.reconcile(self.res, [{"key": "NCT3|T", "field": "*", "chosen": "other", "value": "", "reason": "x"}])

    def test_row_level_choices(self):
        decs = self.decisions()
        decs[3]["chosen"] = "a"                                   # csak-B sor: az A változata = kimarad
        out = K.reconcile(self.res, decs)
        self.assertEqual([r["cells"][0] for r in out["table"]["rows"]], ["NCT1", "NCT1", "NCT2"])
        # csak-A sor: b → kimarad
        res = K.compare(self.b, self.a)                           # most NCT3 csak az „A”-ban
        decs = [{"key": d["key"], "field": d["field"], "chosen": "a", "reason": "x"}
                for d in res["disagreements"] if d["needs_decision"]]
        decs.append({"key": "NCT3|T", "field": "*", "chosen": "b", "reason": "nem bevont"})
        out = K.reconcile(res, decs)
        self.assertEqual(len(out["table"]["rows"]), 3)

    def test_plain_dict_result_with_tables(self):
        plain = json.loads(json.dumps(self.res))
        with self.assertRaises(K.KettosError):
            K.reconcile(plain, self.decisions())
        out = K.reconcile(plain, self.decisions(), a=self.a, b=self.b)
        self.assertTrue(out["consensus"]["complete"])

    def test_gui_calling_convention(self):
        # a munkapad (ma_gui extraction_dual_common.table_arg): {header, rows, row_uids, dataset, decimal_mark}
        # a munkapad az első paraméter nevéből dönt: '*path*' → fájlút, különben {header, rows, …}
        self.assertNotIn("path", list(inspect.signature(K.compare).parameters)[0])
        self.assertNotIn("path", list(inspect.signature(K.consensus_table).parameters)[0])
        ha, ra, _ = tableio.read_raw(raw=self.a)
        hb, rb, _ = tableio.read_raw(raw=self.b)
        ua = ["ra%04d" % i for i in range(len(ra))]
        ub = ["rb%04d" % i for i in range(len(rb))]
        ta = {"header": ha, "rows": ra, "row_uids": ua, "dataset": "03_adatok/kettos/o1.A.csv", "decimal_mark": ","}
        tb = {"header": hb, "rows": rb, "row_uids": ub, "dataset": "03_adatok/kettos/o1.B.csv", "decimal_mark": "."}
        res = K.compare(ta, tb, key=None, tolerance=None, measure=None)
        self.assertEqual(errors(res, "ma.compare-result"), [])
        self.assertEqual(res["pairs"][0]["row_uid_a"], "ra0000")
        self.assertEqual(find(res, "NCT1|T", "sd1")["row_uid_b"], "rb0000")
        self.assertEqual(res["sources"]["a"]["path"], "03_adatok/kettos/o1.A.csv")
        doc = {"schema": K.CONSENSUS_SCHEMA, "key": res["key"],
               "decisions": [dict(d, actor="SzK", ts="2026-10-05T10:00:00Z") for d in [
                   {"key": "NCT1|T", "field": "sd1", "chosen": "b", "value": "4.2", "reason": "r"},
                   {"key": "NCT2|T", "field": "n1", "chosen": "b", "value": "31", "reason": "r"},
                   {"key": "NCT2|T", "field": "rob", "chosen": "a", "value": "high", "reason": "r"},
                   {"key": "NCT3|T", "field": "*", "chosen": "b", "value": None, "reason": "r"}]]}
        out = K.consensus_table(ta, tb, doc, key=res["key"])
        self.assertEqual(out["header"], ha)
        self.assertEqual([r["row_uid"] for r in out["rows"]], ua + ["rb0003"])
        self.assertEqual(out["rows"][0]["cells"][3], "4,2")
        self.assertTrue(all(set(r) >= {"row_uid", "field", "key"} for r in out["reconciled"]))
        self.assertEqual(K.agreement_report(res)["text"]["hu"], res["agreement_text"]["hu"])

    def test_uid_column_is_kept_and_not_compared(self):
        a = "row_uid;study;m1\nraaaa1;S1;1,5\nraaaa2;S2;2,0\n"
        b = "study,m1,row_uid\nS1,1.5,rbbbb1\nS2,2.5,rbbbb2\n"
        res = K.compare(csv(a), csv(b))
        self.assertEqual(res["key"], ["study"])                   # a két fájl uid-ja különbözik
        self.assertNotIn("row_uid", res["summary"]["by_field"])
        out = K.reconcile(res, [{"key": "S2", "field": "m1", "chosen": "b", "reason": "x"}])
        self.assertEqual(out["table"]["uid_column"], 0)
        text = K.render_table(out["table"]).decode("utf-8")
        self.assertTrue(text.startswith("row_uid;study;m1\nraaaa1;S1;1,5\nraaaa2;S2;2,5"))


# ------------------------------------------------------------------ projektfájlok, X009, S08-kapu, CLI
class Project(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ma_kettos_")
        self.p = K.outcome_paths(self.tmp, "o1")
        os.makedirs(os.path.join(self.tmp, "03_adatok", "kettos"))
        for side, data in (("a", csv(REC_A, bom=True, crlf=True)), ("b", csv(REC_B))):
            with open(os.path.join(self.tmp, *self.p[side].split("/")), "wb") as fh:
                fh.write(data)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_paths(self):
        self.assertEqual(self.p["a"], "03_adatok/kettos/o1.A.csv")
        self.assertEqual(self.p["json"], "03_adatok/kettos/o1.consensus.json")
        self.assertEqual(K.outcome_paths(self.tmp, "o2", "_privat/o2.csv")["b"], "_privat/kettos/o2.B.csv")
        with self.assertRaises(K.KettosError):
            K.outcome_paths(self.tmp, "../x")

    def test_status_gate_and_reconcile_project(self):
        st = K.project_status(self.tmp)
        self.assertEqual([o["outcome"] for o in st["outcomes"]], ["o1"])
        self.assertEqual(st["unresolved_total"], 4)
        found, skipped = K.x009_findings(self.tmp)
        self.assertEqual(skipped, [])
        self.assertEqual(found[0]["unresolved"], 4)
        self.assertIn("4 feloldatlan", found[0]["detail"])
        self.assertNotIn("NCT", found[0]["detail"])               # kulcsérték nem kerül a találatba
        with self.assertRaises(ValueError) as cm:
            K.require_s08_gate(self.tmp)
        self.assertIn("X009", str(cm.exception))
        # két részletben döntünk; a második hívás összefésül
        K.reconcile_project(self.tmp, "o1", Reconcile.decisions(None)[:2], "SzK")
        self.assertEqual(K.project_status(self.tmp)["unresolved_total"], 2)
        with self.assertRaises(K.KettosError):
            K.reconcile_project(self.tmp, "o1", [], "SzK", write_csv=True)
        out = K.reconcile_project(self.tmp, "o1", Reconcile.decisions(None)[2:], "KP", write_csv=True)
        self.assertTrue(out["consensus"]["complete"])
        K.require_s08_gate(self.tmp)                              # most már átengedi
        self.assertEqual(K.x009_findings(self.tmp)[0], [])
        cons = K.load_consensus(os.path.join(self.tmp, *self.p["json"].split("/")))
        self.assertEqual(errors(cons, "ma.consensus"), [])
        self.assertEqual(cons["sources"]["a"]["path"], self.p["a"])
        self.assertEqual({d["actor"] for d in cons["decisions"]}, {"SzK", "KP"})
        with open(os.path.join(self.tmp, *self.p["prov"].split("/")), encoding="utf-8") as fh:
            prov = json.load(fh)
        self.assertEqual(errors(prov, "ma.provenance"), [])
        self.assertTrue(all(c["method"] == "reconciled" for c in prov["cells"]))
        with open(os.path.join(self.tmp, *self.p["csv"].split("/")), "rb") as fh:
            data = fh.read()
        self.assertEqual(prov["table_sha256"], cons["csv"]["sha256"])
        import hashlib
        self.assertEqual(hashlib.sha256(data).hexdigest(), prov["table_sha256"])
        # a tábla változása után a döntés elavul → újra feloldatlan
        with open(os.path.join(self.tmp, *self.p["b"].split("/")), "wb") as fh:
            fh.write(csv(REC_B.replace("4.2", "4.3")))
        st = K.project_status(self.tmp)["outcomes"][0]
        self.assertEqual((st["unresolved"], st["stale"]), (1, 1))

    def test_missing_side_is_not_checked(self):
        os.remove(os.path.join(self.tmp, *self.p["b"].split("/")))
        found, skipped = K.x009_findings(self.tmp)
        self.assertEqual(found, [])
        self.assertEqual(skipped[0]["outcome"], "o1")

    def test_cli(self):
        out = io.StringIO()
        rc = K.cli_main(["compare", "--project", self.tmp, "--outcome", "o1", "--json"], out=out)
        self.assertEqual(rc, 0)
        doc = json.loads(out.getvalue())
        self.assertEqual(errors(doc, "ma.compare-result"), [])
        out = io.StringIO()
        self.assertEqual(K.cli_main(["status", self.tmp], out=out), 1)
        self.assertIn("4 feloldatlan", out.getvalue())
        dec = os.path.join(self.tmp, "d.json")
        with open(dec, "w", encoding="utf-8") as fh:
            json.dump(Reconcile.decisions(None), fh)
        out = io.StringIO()
        rc = K.cli_main(["reconcile", "--project", self.tmp, "--outcome", "o1", "--decisions", dec,
                         "--actor", "SzK", "--write-csv"], out=out)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(K.cli_main(["status", self.tmp], out=io.StringIO()), 0)
        out = io.StringIO()
        a, b = (os.path.join(self.tmp, *self.p[s].split("/")) for s in ("a", "b"))
        self.assertEqual(K.cli_main(["compare", a, b, "--measure", "SMD"], out=out), 0)
        self.assertIn("Kettős kinyerés", out.getvalue())
        out = io.StringIO()
        self.assertEqual(K.cli_main(["report", a, b, "--lang", "en"], out=out), 0)
        self.assertIn("independently", out.getvalue())


if __name__ == "__main__":
    unittest.main()
