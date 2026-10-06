# -*- coding: utf-8 -*-
"""v1 / E10: GRADE-tanácsadó (grade_help.advice), Summary of Findings (grade_help.sof), AMSTAR 2-besorolás
(grade_help.amstar2_consistency), a GRADE-tár (projekt.*_grade_doc) és a 11/4. döntés (feloldatlan „suspected”, X019).

- a tanács soha nem végleges: minden domén ítélete null; doménenként számok (advisory) és kezdőknek szóló „Miért?”
  szöveg, KB-azonosítókkal (a KB-ban létezők);
- BCG (RR) és Normand (MD) futásokon a számok a futásból / kézi képletből (súlyarány, I², PI, OIS a power modullal,
  események, tesztek értelmezhetősége);
- „magas RoB nélkül” gyermek-futás felderítése egy valódi projektben (api.analyze commit);
- SoF: abszolút hatás /1000 kézi képlettel (RR, OR, RD), a totals.absolute_per_1000-val egyezően; szövegek,
  szimbólumok, megfogalmazás, lábjegyzetek a GRADE-indoklásokból; X008-összevetés;
- GRADE-tár: mentés feloldatlan publikációs torzítással (certainty null), a rögzítés tiltása (X019), feloldás
  indoklással, rögzítés a projektnaplóba előjeles lépés-szövegekkel (grade_consistency), AI-vázlat jóváhagyása;
- AMSTAR 2: a hivatalos besorolási tábla esetei mindkét konvencióval, és összevetés a validator 1.0.0
  rollup_amstar2-jével (csak olvasva);
- a szk.ma.grade/v1 és szk.ma.sof/v1 szerződés: a motor kimenete és a példák megfelelnek.
"""
import copy
import csv
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

from _helpers import ROOT
from metaelemzes import api, contracts as K, grade_help as G, projekt as P, spec as S
from metaelemzes.distributions import norm_ppf

BCG = os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv")
NORMAND = os.path.join(ROOT, "peldak", "normand1999_folytonos.csv")
EXAMPLES = os.path.join(ROOT, "tests", "reference", "contract_examples")
VALIDATOR = "/home/user/szilikaroly/szk-plugins/plugins/validator/scripts/appraise.py"
ALLOC_ROB = {"random": "low", "alternate": "high", "systematic": "high"}
RID = "20261004T211200Z-a1f3c2"


def explore(path, measure, *extra):
    rel = os.path.relpath(path, ROOT)
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        sp = S.spec_from_argv(["analyze", "--data", rel, "--measure", measure] + list(extra))
        return api.analyze(sp, mode="explore")
    finally:
        os.chdir(cwd)


def bcg_rows():
    with open(BCG, encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    return rows[0], rows[1:]


def bcg_rob():
    _h, rows = bcg_rows()
    return {r[0]: ALLOC_ROB[r[7]] for r in rows}


_REG = None


def schema_errors(doc, name):
    """A szerződés szerinti hibák (a test_mvp_contracts kis validátorával; ha az nem tölthető, a ma_gui
    schema_lite-tal)."""
    global _REG
    doc = json.loads(json.dumps(doc, allow_nan=False))
    if _REG is None:
        _REG = K.registry()
    try:
        from test_mvp_contracts import MiniValidator
        return MiniValidator(_REG).errors(doc, _REG[K.urn(name, 1)])
    except ImportError:
        from ma_gui import schema_lite as sl
        return [str(e) for e in sl.validate(doc, _REG[K.urn(name, 1)], _REG)]


# ------------------------------------------------------------------ tanács: BCG (RR)
class TestAdviceBCG(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = explore(BCG, "RR")
        cls.res = cls.view["results"]
        cls.doc = G.advice(cls.view, rob_by_row=bcg_rob(), outcome_id="o1", start="high")

    def test_never_final_and_conforms(self):
        d = self.doc
        self.assertEqual(d["schema"], "szk.ma.grade/v1")
        self.assertIsNone(d["certainty"])
        self.assertEqual(d["status"], "draft")
        for name, dom in d["domains"].items():
            with self.subTest(domain=name):
                self.assertIsNone(dom["rating"])
                self.assertIsNone(dom["step"])
                self.assertIsNone(dom["rationale"])
                self.assertIn(dom["suggestion"]["status"], ("suggested", "borderline", "human_judgement",
                                                             "not_applicable", "insufficient_data"))
        self.assertEqual(d["domains"]["publication_bias"]["status"], "open")
        self.assertEqual(schema_errors(d, "ma.grade"), [])
        self.assertEqual(P.validate_grade_doc(d)[1], [])

    def test_beginner_why_texts_in_both_languages(self):
        """11. döntés, 6. pont: javasolt ítélet, bizonyíték, indoklás egyszerű nyelven (mit kérdez, miért ez a
        javaslat, mi változtatná meg), bizonytalanság jelölése."""
        for name, dom in self.doc["domains"].items():
            sug = dom["suggestion"]
            with self.subTest(domain=name):
                for part in ("asks", "because", "change"):
                    self.assertGreater(len(sug["why"][part]["hu"]), 40)
                    self.assertGreater(len(sug["why"][part]["en"]), 40)
                self.assertIn("uncertain", sug["why"])
                self.assertTrue(sug["summary"]["hu"] and sug["summary"]["en"])
                self.assertTrue(sug["kb_refs"])
                self.assertIsInstance(sug["concern"], bool)
                for ev in sug["evidence"]:
                    self.assertTrue(ev["label"]["hu"] and ev["text"]["en"])
        self.assertTrue(self.doc["domains"]["inconsistency"]["suggestion"]["concern"])         # −1 javaslat
        self.assertFalse(self.doc["domains"]["imprecision"]["suggestion"]["concern"])
        rob = self.doc["domains"]["risk_of_bias"]["suggestion"]
        self.assertIn("Határeset", rob["why"]["uncertain"]["hu"])                             # borderline
        self.assertEqual(rob["evidence"][0]["text"]["hu"], "49.5% (6 vizsgálat)")
        pb = self.doc["domains"]["publication_bias"]["suggestion"]
        self.assertEqual([e["label"]["en"] for e in pb["evidence"]][:3], ["k", "Harbord", "Peters"])

    def test_kb_refs_exist_in_knowledge_base(self):
        from metaelemzes import kb
        refs = set(self.doc["advice"]["kb_refs"])
        for dom in self.doc["domains"].values():
            refs |= set(dom["suggestion"]["kb_refs"])
        refs |= {r for v in G.KB.values() for r in v} | set(G.X_KB_REFS["X019"])
        try:
            have = set(kb.existing_ids(sorted(refs)))
        except Exception as exc:                      # noqa: BLE001 — tudásbázis nélkül nem ellenőrizhető
            self.skipTest("a tudásbázis nem érhető el: %s" % exc)
        self.assertEqual(sorted(refs - have), [])

    def test_risk_of_bias_weight_share_by_hand(self):
        prim = self.res["primary"]
        rob = bcg_rob()
        high = sum(w for lab, w in zip(prim["labels"], prim["weights_pct"]) if rob[lab] == "high")
        adv = self.doc["domains"]["risk_of_bias"]["advisory"]
        self.assertAlmostEqual(adv["high_rob_weight_pct"], high, places=9)
        self.assertAlmostEqual(adv["high_rob_weight_pct"], 49.4813, places=3)
        self.assertAlmostEqual(adv["low_rob_weight_pct"], 100 - high, places=9)
        self.assertEqual(adv["k_assessed"], 13)
        self.assertEqual(len(adv["high_rob_studies"]), 6)
        self.assertEqual(adv["high_rob_studies"][0], "Stein & Aronson 1953")       # a legnagyobb súlyú elöl
        sug = self.doc["domains"]["risk_of_bias"]["suggestion"]
        # 49.5% < 50%: határeset (nem a többség), gyermek-futás nélkül
        self.assertEqual((sug["rating"], sug["step"], sug["status"]), ("not serious", 0, "borderline"))
        self.assertIn("49.5%", sug["summary"]["hu"])
        self.assertIsNone(adv["sensitivity_run"])

    def test_risk_of_bias_without_data_and_majority(self):
        d = G.advice(self.view, outcome_id="o1")
        sug = d["domains"]["risk_of_bias"]["suggestion"]
        self.assertEqual((sug["rating"], sug["status"]), (None, "insufficient_data"))
        all_high = {lab: "High risk of bias" for lab in self.res["primary"]["labels"]}
        d = G.advice(self.view, rob_by_row=all_high, outcome_id="o1")
        sug = d["domains"]["risk_of_bias"]["suggestion"]
        self.assertEqual((sug["rating"], sug["step"], sug["status"]), ("serious", -1, "suggested"))
        self.assertIn("very_serious_possible", [f["code"] for f in sug["flags"]])
        self.assertAlmostEqual(d["domains"]["risk_of_bias"]["advisory"]["high_rob_weight_pct"], 100.0, places=9)
        # lista a vizsgálatok sorrendjében; rossz hossz → hiba
        lst = ["low"] * 13
        d = G.advice(self.view, rob_by_row=lst, outcome_id="o1")
        self.assertEqual(d["domains"]["risk_of_bias"]["suggestion"]["status"], "suggested")
        with self.assertRaises(ValueError):
            G.advice(self.view, rob_by_row=["low"] * 3, outcome_id="o1")

    def test_explicit_child_run_that_changes_conclusion(self):
        child = explore(BCG, "RR", "--include", "allokáció=systematic")
        self.assertLessEqual(child["results"]["back_transformed"]["estimate_ci"][1], 1.0)
        self.assertGreaterEqual(child["results"]["back_transformed"]["estimate_ci"][2], 1.0)
        d = G.advice(self.view, rob_by_row=bcg_rob(), outcome_id="o1", rob_child=child)
        sens = d["domains"]["risk_of_bias"]["advisory"]["sensitivity_run"]
        self.assertTrue(sens["conclusion_changed"])
        self.assertEqual(sens["k"], 4)
        sug = d["domains"]["risk_of_bias"]["suggestion"]
        self.assertEqual((sug["rating"], sug["step"]), ("serious", -1))
        self.assertIn("MEGVÁLTOZIK", sug["why"]["because"]["hu"])
        self.assertIn("a következtetés változik", sug["evidence"][-1]["text"]["hu"])

    def test_inconsistency(self):
        adv = self.doc["domains"]["inconsistency"]["advisory"]
        prim = self.res["primary"]
        self.assertEqual(adv["I2"], prim["I2"])
        self.assertEqual(adv["I2_ci_HT"], prim["heterogeneity"]["I2_ci_HT"])
        self.assertEqual(adv["pi"], self.res["back_transformed"]["pi"])
        self.assertTrue(adv["pi_crosses_null"])
        self.assertFalse(adv["ci_crosses_null"])
        self.assertFalse(adv["opposite_significant"])
        # a nullhatás másik oldalán: TPT Madras (log RR > 0) és Comstock & Webster 1969
        other = sum(w for y, w in zip(prim["yi"], prim["weights_pct"]) if y > 0)
        self.assertAlmostEqual(adv["minority_weight_pct"], other, places=9)
        sug = self.doc["domains"]["inconsistency"]["suggestion"]
        self.assertEqual((sug["rating"], sug["step"]), ("serious", -1))
        self.assertIn("I² 92% [88; 95]", sug["summary"]["hu"])
        self.assertIn("PI 0.13–1.79", sug["summary"]["hu"])
        self.assertIn("⚠", sug["summary"]["hu"])

    def test_imprecision_ois_by_closed_form(self):
        adv = self.doc["domains"]["imprecision"]["advisory"]
        tot = self.res["totals"]
        p0 = tot["events2"] / tot["n2"]
        p1 = p0 * 0.75
        z = norm_ppf(0.975) + norm_ppf(0.8)
        n_closed = z * z * (p0 * (1 - p0) + p1 * (1 - p1)) / (p0 - p1) ** 2
        ois = adv["ois_assumptions"]
        self.assertLessEqual(abs(ois["per_arm"] - math.ceil(n_closed)), 1)
        self.assertEqual(adv["ois"], 2 * ois["per_arm"])
        self.assertAlmostEqual(ois["control_risk"], p0, places=15)
        self.assertTrue(adv["ois_met"])
        self.assertEqual(adv["participants"], 357347)
        self.assertEqual(adv["events"], 1065 + 1510)
        self.assertFalse(adv["events_below_300"])
        self.assertFalse(adv["ci_crosses_null"])
        sug = self.doc["domains"]["imprecision"]["suggestion"]
        self.assertEqual((sug["rating"], sug["step"]), ("not serious", 0))
        # MID-del (0,75–1,25): a CI felső határa 0.73 < 0.75 → nem lépi át
        d = G.advice(self.view, mid="0,75–1,25", outcome_id="o1")
        adv = d["domains"]["imprecision"]["advisory"]
        self.assertFalse(adv["ci_crosses_mid"])
        self.assertEqual(adv["threshold_basis"], "mid")
        self.assertEqual(d["mid"]["lower"], 0.75)
        # szigorúbb MID (0,8): a CI átlépi → −1
        d = G.advice(self.view, mid=0.6, outcome_id="o1")
        self.assertTrue(d["domains"]["imprecision"]["advisory"]["ci_crosses_mid"])
        self.assertEqual(d["domains"]["imprecision"]["suggestion"]["step"], -1)

    def test_ois_functions_against_power_module(self):
        res = G.ois_binary(0.1, 0.25)
        p0, p1 = 0.1, 0.075
        z = norm_ppf(0.975) + norm_ppf(0.8)
        self.assertLessEqual(abs(res["per_arm"] - math.ceil(z * z * (p0 * .9 + p1 * (1 - p1)) / (p0 - p1) ** 2)), 1)
        res = G.ois_continuous(5.0, sd=20.0)
        self.assertLessEqual(abs(res["per_arm"] - math.ceil(z * z * 2 * 400 / 25)), 1)
        res = G.ois_continuous(0.2, standardized=True)
        self.assertLessEqual(abs(res["per_arm"] - math.ceil(z * z * 2 / 0.04)), 2)    # + d²/(2N) tag
        for bad in (lambda: G.ois_binary(0), lambda: G.ois_binary(0.1, 1.2), lambda: G.ois_continuous(0, 1),
                    lambda: G.ois_continuous(5)):
            with self.assertRaises(ValueError):
                bad()

    def test_publication_bias_interpretability_and_test_choice(self):
        adv = self.doc["domains"]["publication_bias"]["advisory"]
        bias = self.res["bias"]
        self.assertTrue(adv["tests_interpretable"])
        self.assertEqual(adv["k"], 13)
        self.assertEqual(adv["recommended_tests"], ["harbord", "peters"])
        self.assertEqual(adv["harbord_p"], bias["harbord"]["p"])
        self.assertEqual(adv["peters_p"], bias["peters"]["p"])
        self.assertAlmostEqual(adv["lfk"], -4.1024, places=3)
        self.assertFalse(adv["asymmetry_signal"])
        sug = self.doc["domains"]["publication_bias"]["suggestion"]
        self.assertEqual((sug["rating"], sug["step"]), ("undetected", 0))
        self.assertEqual(sug["status"], "borderline")                  # LFK heurisztika jelez
        codes = [f["code"] for f in sug["flags"]]
        self.assertIn("suspected_unresolved", codes)
        self.assertIn("lfk_heuristic", codes)
        self.assertIn("FELOLDATLAN", sug["why"]["because"]["hu"])
        self.assertIn("UNRESOLVED", sug["why"]["because"]["en"])
        self.assertIsNotNone(sug["why"]["uncertain"])                                       # LFK-határeset
        self.assertIn("Harbord p 0.23", sug["summary"]["hu"])

    def test_test_choice_per_measure(self):
        self.assertEqual(G.recommended_bias_tests("MD", {}), ["egger"])
        self.assertEqual(G.recommended_bias_tests("SMD", {}), ["egger"])
        self.assertEqual(G.recommended_bias_tests("OR", {"harbord": {"p": 0.5}, "peters": {"p": 0.4}}),
                         ["harbord", "peters"])
        self.assertEqual(G.recommended_bias_tests("OR", {}), ["egger"])
        self.assertEqual(G.recommended_bias_tests("PFT", {}), [])

    def test_indirectness_is_only_a_prompt(self):
        dom = self.doc["domains"]["indirectness"]
        self.assertEqual(dom["suggestion"]["status"], "human_judgement")
        self.assertIsNone(dom["suggestion"]["rating"])
        self.assertIn("PICO", dom["suggestion"]["why"]["asks"]["hu"])
        self.assertEqual(dom["suggestion"]["evidence"], [])

    def test_upgrade_advice_and_determinism(self):
        up = self.doc["advice"]["upgrades"]
        self.assertEqual(up["large_effect"]["candidate_step"], 1)              # RR 0.49 < 0.5
        self.assertFalse(up["large_effect"]["suggested"])                      # RCT-kiindulás
        self.assertIn("RCT", up["large_effect"]["why"]["because"]["hu"])
        self.assertEqual((up["dose_response"]["summary"]["hu"], up["opposing_confounding"]["suggested"]),
                         ("emberi ítélet", False))
        rd = G.RunData({"options": {"measure": "RR"}, "back_transformed": {"estimate_ci": [0.3, 0.2, 0.45]}})
        up2 = G._advise_upgrades(rd, "low")["large_effect"]
        self.assertEqual((up2["suggested"], up2["candidate_step"]), (True, 1))
        rd = G.RunData({"options": {"measure": "OR"}, "back_transformed": {"estimate_ci": [6.0, 5.5, 9.0]}})
        self.assertEqual(G._advise_upgrades(rd, "low")["large_effect"]["candidate_step"], 2)
        rd = G.RunData({"options": {"measure": "MD"}, "back_transformed": {"estimate_ci": [6.0, 5.5, 9.0]}})
        self.assertIsNone(G._advise_upgrades(rd, "low")["large_effect"]["candidate_step"])
        obs = G.advice(self.view, outcome_id="o1", start="low")["advice"]["upgrades"]["large_effect"]
        self.assertFalse(obs["suggested"])                                     # a CI felső határa (0.73) > 0.5
        self.assertEqual(obs["evidence"][1]["text"]["hu"], "nem")
        again = G.advice(self.view, rob_by_row=bcg_rob(), outcome_id="o1", start="high")
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.doc, sort_keys=True))
        self.assertEqual(self.doc["run_summary"]["participants"], 357347)
        self.assertEqual(self.doc["run_summary"]["effect_text"]["hu"], "RR 0.49 [0.33; 0.73]")


class TestAdviceNormand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = explore(NORMAND, "MD")
        cls.doc = G.advice(cls.view, mid=10, outcome_id="o2", start="high")

    def test_md_with_mid(self):
        d = self.doc
        self.assertEqual(schema_errors(d, "ma.grade"), [])
        imp = d["domains"]["imprecision"]
        self.assertTrue(imp["advisory"]["ci_crosses_mid"])
        self.assertTrue(imp["advisory"]["ci_crosses_null"])
        self.assertFalse(imp["advisory"]["ci_crosses_both_mid"])
        self.assertEqual(imp["suggestion"]["step"], -1)
        self.assertIn("MID ±10", imp["suggestion"]["summary"]["hu"])
        # OIS: a tipikus SD a varianciákból (sd² = v / (1/n1 + 1/n2)), (n − 2)-vel súlyozva
        ois = imp["advisory"]["ois_assumptions"]
        prim = self.view["results"]["primary"]
        cells = [s["cells"] for s in self.view["plot"]["studies"]]
        num = den = 0.0
        for v, c in zip(prim["vi"], cells):
            n1, n2 = float(c["n1"]), float(c["n2"])
            num += (n1 + n2 - 2) * v / (1 / n1 + 1 / n2)
            den += n1 + n2 - 2
        sd = math.sqrt(num / den)
        self.assertAlmostEqual(ois["sd"], sd, places=9)
        z = norm_ppf(0.975) + norm_ppf(0.8)
        self.assertLessEqual(abs(ois["per_arm"] - math.ceil(z * z * 2 * sd * sd / 100.0)), 1)
        self.assertTrue(imp["advisory"]["ois_met"])                       # 1158 ≥ ~386

    def test_md_inconsistency_and_publication_bias(self):
        inc = self.doc["domains"]["inconsistency"]
        self.assertEqual(inc["suggestion"]["step"], -1)
        self.assertGreater(inc["advisory"]["I2"], 90)
        pb = self.doc["domains"]["publication_bias"]
        self.assertFalse(pb["advisory"]["tests_interpretable"])            # k = 9 < 10
        self.assertEqual(pb["advisory"]["recommended_tests"], ["egger"])
        self.assertEqual(pb["suggestion"]["status"], "human_judgement")
        self.assertIsNone(pb["suggestion"]["rating"])
        self.assertIn("k = 9 < 10", pb["suggestion"]["summary"]["hu"])

    def test_mid_with_explicit_sd(self):
        d = G.advice(self.view, mid={"value": 10, "sd": 20, "source": "protokoll"}, outcome_id="o2")
        ois = d["domains"]["imprecision"]["advisory"]["ois_assumptions"]
        self.assertEqual(ois["sd"], 20.0)
        self.assertEqual(d["mid"]["source"], "protokoll")


class TestParseMid(unittest.TestCase):
    def test_forms(self):
        self.assertEqual((G.parse_mid("0,75–1,25", "RR")["lower"], G.parse_mid("0,75–1,25", "RR")["upper"]),
                         (0.75, 1.25))
        m = G.parse_mid(0.8, "RR")
        self.assertEqual((m["lower"], m["upper"]), (0.8, 1.25))
        m = G.parse_mid("1.25", "OR")
        self.assertAlmostEqual(m["lower"], 0.8, places=12)
        self.assertEqual(m["upper"], 1.25)
        m = G.parse_mid("5", "MD")
        self.assertEqual((m["lower"], m["upper"]), (-5.0, 5.0))
        m = G.parse_mid("-5; 5", "MD")
        self.assertEqual((m["lower"], m["upper"]), (-5.0, 5.0))
        m = G.parse_mid("−3 – 4", "MD")
        self.assertEqual((m["lower"], m["upper"]), (-3.0, 4.0))
        m = G.parse_mid("MID: 0.75-1.25", "RR")
        self.assertEqual((m["lower"], m["upper"]), (0.75, 1.25))
        m = G.parse_mid({"lower": 0.8, "upper": None}, "RR")
        self.assertEqual((m["lower"], m["upper"]), (0.8, None))
        self.assertIsNone(G.parse_mid(None, "RR"))
        self.assertIsNone(G.parse_mid("  ", "MD"))

    def test_errors(self):
        for mid, measure in ((1, "RR"), (-0.5, "RR"), ("0", "MD"), ("1.2–1.5", "RR"), ("x", "MD"), (0.1, "PFT"),
                             ("1;2;3", "MD"), ({"text": None}, "MD"), (1.5, "COR"), (float("nan"), "MD"),
                             ({"value": 5, "sd": -1}, "MD"), ({"lower": None, "upper": None}, "MD")):
            with self.subTest(mid=mid, measure=measure):
                with self.assertRaises(ValueError):
                    G.parse_mid(mid, measure)


class TestOtherMeasures(unittest.TestCase):
    def test_proportion_and_correlation(self):
        d = G.advice(explore(os.path.join(ROOT, "peldak", "pritz1997_arany.csv"), "PFT"), outcome_id="o3")
        self.assertEqual(schema_errors(d, "ma.grade"), [])
        self.assertEqual(d["domains"]["inconsistency"]["suggestion"]["status"], "human_judgement")
        self.assertEqual(d["domains"]["publication_bias"]["advisory"]["recommended_tests"], [])
        self.assertEqual(d["domains"]["imprecision"]["suggestion"]["status"], "human_judgement")
        d = G.advice(explore(os.path.join(ROOT, "peldak", "molloy2014_korrelacio.csv"), "ZCOR"), outcome_id="o4")
        self.assertEqual(schema_errors(d, "ma.grade"), [])
        self.assertIsNotNone(d["domains"]["imprecision"]["advisory"]["ci_crosses_null"])

    def test_load_run_forms(self):
        view = explore(BCG, "RR")
        a = G.load_run(view)
        b = G.load_run(view["results"])
        self.assertEqual(a.bt, b.bt)
        self.assertIsNone(b.plot)
        with self.assertRaises(ValueError):
            G.load_run({"schema": "x"})
        with self.assertRaises(ValueError):
            G.load_run(RID)                                   # run_id projektmappa nélkül
        with self.assertRaises(ValueError):
            G.load_run(os.path.join(ROOT, "nincs-ilyen-mappa"))
        # a results.json-ból (plot nélkül) a súlyarány ugyanaz
        d1 = G.advice(view, rob_by_row=bcg_rob(), outcome_id="o1")
        d2 = G.advice(view["results"], rob_by_row=bcg_rob(), outcome_id="o1")
        self.assertAlmostEqual(d1["domains"]["risk_of_bias"]["advisory"]["high_rob_weight_pct"],
                               d2["domains"]["risk_of_bias"]["advisory"]["high_rob_weight_pct"], places=9)
        with self.assertRaises(ValueError):
            G.advice(view, outcome_id="rossz azonosító")


# ------------------------------------------------------------------ SoF
class TestSoF(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = explore(BCG, "RR")
        cls.res = cls.view["results"]

    def test_absolute_effects_by_hand_rr(self):
        tot = self.res["totals"]
        acr = tot["events2"] / tot["n2"]
        rr = self.res["back_transformed"]["estimate_ci"]
        doc = G.sof(self.view, assumed_risks=[{"source": "control_pool"},
                                              {"label": "magas", "per_1000": "40", "source": "kohorsz"}],
                    certainty="moderate", outcome_id="o1", design="RCT")
        self.assertEqual(schema_errors(doc, "ma.sof"), [])
        row = doc["rows"][0]
        pool, ext = row["absolute"]
        for i in range(3):
            self.assertAlmostEqual(pool["difference_per_1000"][i], 1000 * acr * (rr[i] - 1), places=9)
            self.assertAlmostEqual(pool["risk_per_1000"][i], 1000 * acr * rr[i], places=9)
            self.assertAlmostEqual(ext["difference_per_1000"][i], 40 * (rr[i] - 1), places=9)
            # a pipeline totals.absolute_per_1000 általánosítása: a pool-sor ugyanaz
            self.assertAlmostEqual(pool["difference_per_1000"][i], tot["absolute_per_1000"]["difference"][i],
                                   places=9)
        self.assertAlmostEqual(pool["assumed_risk_per_1000"], 1000 * acr, places=12)
        self.assertEqual(pool["text"]["en"], "5 fewer per 1,000 (from 6 fewer to 2 fewer)")
        self.assertEqual(pool["text"]["hu"], "1000 főre 5 kevesebb (6 kevesebbtől 2 kevesebbig)")
        self.assertEqual(pool["baseline_text"]["en"], "9 per 1,000")
        self.assertEqual(ext["text"]["en"], "20 fewer per 1,000 (from 27 fewer to 11 fewer)")
        self.assertEqual(ext["source"], "kohorsz")
        self.assertEqual(row["studies_text"], {"hu": "357347 (13 RCT)", "en": "357347 (13 RCTs)"})
        self.assertEqual(row["relative"]["display_text"], self.view["plot"]["summaries"][0]["display_text"])
        self.assertEqual(row["relative"]["text"]["en"], "RR 0.49 (95% CI 0.33 to 0.73)")
        self.assertEqual(row["certainty_symbols"], "⊕⊕⊕◯")
        self.assertEqual(row["certainty_text"]["hu"], "⊕⊕⊕◯ mérsékelt")
        self.assertIn("probably reduces", row["statement"]["en"])
        self.assertIn("valószínűleg csökkenti", row["statement"]["hu"])
        self.assertEqual(pool["footnotes"], ["a"])
        self.assertEqual(ext["footnotes"], ["b"])
        self.assertEqual(row["sources"]["relative"], "back_transformed.estimate_ci")

    def test_absolute_effect_or_and_rd_formulas(self):
        orv = [0.5, 0.25, 1.5]
        a = G.absolute_effect("OR", orv, 0.2)
        for i, x in enumerate(orv):
            r = 0.2 * x / (1 - 0.2 + 0.2 * x)
            self.assertAlmostEqual(a["risk_per_1000"][i], 1000 * r, places=12)
            self.assertAlmostEqual(a["difference_per_1000"][i], 1000 * (r - 0.2), places=12)
        rd = [-0.02, -0.05, 0.01]
        a = G.absolute_effect("RD", rd, 0.1)
        self.assertEqual([round(x, 9) for x in a["difference_per_1000"]], [-20.0, -50.0, 10.0])
        self.assertFalse(a["incompatible"])
        a = G.absolute_effect("RD", rd, 0.03)                         # 0.03 − 0.05 < 0
        self.assertTrue(a["incompatible"])
        self.assertIsNone(a["risk_per_1000"][1])
        self.assertAlmostEqual(a["difference_per_1000"][1], -50.0, places=9)
        a = G.absolute_effect("RR", [2.0, 1.5, 6.0], 0.2)             # 0.2 · 6 > 1
        self.assertTrue(a["incompatible"])
        self.assertIsNone(a["risk_per_1000"][2])
        for bad in (lambda: G.absolute_effect("RR", rd, 0), lambda: G.absolute_effect("RR", rd, 1),
                    lambda: G.absolute_effect("MD", rd, 0.1)):
            with self.assertRaises(ValueError):
                bad()

    def test_or_sof_matches_engine_totals(self):
        view = explore(BCG, "OR")
        doc = G.sof(view, outcome_id="o1")
        ab = view["results"]["totals"]["absolute_per_1000"]
        for i in range(3):
            self.assertAlmostEqual(doc["rows"][0]["absolute"][0]["difference_per_1000"][i], ab["difference"][i],
                                   places=9)
            self.assertAlmostEqual(doc["rows"][0]["absolute"][0]["risk_per_1000"][i], ab["intervention_risk"][i],
                                   places=9)
        self.assertIsNone(doc["rows"][0]["certainty"])
        self.assertIsNone(doc["rows"][0]["statement"])

    def test_rd_sof_and_continuous(self):
        view = explore(BCG, "RD")
        doc = G.sof(view, outcome_id="o1", certainty="low")
        self.assertEqual(schema_errors(doc, "ma.sof"), [])
        row = doc["rows"][0]
        self.assertIsNone(row["relative"])
        self.assertEqual(row["effect"]["kind"], "risk_difference")
        bt = view["results"]["back_transformed"]["estimate_ci"]
        self.assertAlmostEqual(row["absolute"][0]["difference_per_1000"][0], 1000 * bt[0], places=9)
        self.assertTrue(row["absolute"][0]["incompatible"])
        self.assertTrue(row["warnings"])
        view = explore(NORMAND, "MD")
        doc = G.sof(view, outcome_id="o2", certainty="low", mid=10)
        self.assertEqual(schema_errors(doc, "ma.sof"), [])
        row = doc["rows"][0]
        self.assertEqual(row["absolute"], [])
        self.assertEqual(row["effect"]["text"]["en"], "MD 15.11 lower (from 36.32 lower to 6.11 higher)")
        self.assertEqual(row["effect"]["text"]["hu"], "MD 15.11 alacsonyabb (36.32 alacsonyabbtól 6.11 magasabbig)")
        self.assertEqual(row["effect"]["display_text"], view["plot"]["summaries"][0]["display_text"])
        # a pontbecslés (−15.1) a MID-en (±10) kívül → fontos hatás: „may reduce”
        self.assertIn("may reduce", row["statement"]["en"])
        doc = G.sof(view, outcome_id="o2", certainty="low", mid=20)
        self.assertIn("little to no difference", doc["rows"][0]["statement"]["en"])
        self.assertIn("kevés vagy semmi különbséget", doc["rows"][0]["statement"]["hu"])
        doc = G.sof(view, outcome_id="o2", assumed_risks=[{"per_1000": "12"}])      # folytonos: figyelmen kívül
        self.assertEqual(doc["rows"][0]["absolute"], [])
        self.assertTrue(doc["rows"][0]["warnings"])
        rr = explore(BCG, "RR")
        for bad in ("1000", "0", "sok", "-3"):
            with self.subTest(per_1000=bad):
                with self.assertRaises(ValueError):
                    G.sof(rr, outcome_id="o1", assumed_risks=[{"per_1000": bad}])
        with self.assertRaises(ValueError):                    # külső alapkockázat érték nélkül: nem lesz pool
            G.sof(rr, outcome_id="o1", assumed_risks=[{"label": "x", "source": "external", "per_1000": None}])
        doc = G.sof(rr, outcome_id="o1", assumed_risks=[{"label": None, "source": "control_pool", "per_1000": None}])
        self.assertEqual(doc["rows"][0]["absolute"][0]["kind"], "control_pool")
        doc = G.sof(rr, outcome_id="o1", assumed_risks=[{"label": "regiszter", "per_1000": "12,5"}])
        self.assertEqual(doc["rows"][0]["absolute"][0]["assumed_risk_per_1000"], 12.5)
        self.assertEqual(doc["rows"][0]["absolute"][0]["kind"], "external")

    def test_statements_and_symbols(self):
        self.assertEqual([G.certainty_symbols(c) for c in ("high", "moderate", "low", "very low")],
                         ["⊕⊕⊕⊕", "⊕⊕⊕◯", "⊕⊕◯◯", "⊕◯◯◯"])
        o = {"hu": "halálozás", "en": "mortality"}
        cases = {("high", "reduce"): ("X reduces mortality.", "Halálozás: X csökkenti."),
                 ("moderate", "increase"): ("X probably increases mortality.", "Halálozás: X valószínűleg növeli."),
                 ("low", "reduce"): ("X may reduce mortality.", "Halálozás: X csökkentheti.")}
        for (c, d), (en, hu) in cases.items():
            st = G.statement(c, d, True, o, "X")
            self.assertEqual((st["en"], st["hu"]), (en, hu))
        st = G.statement("very low", "reduce", True, o, "X")
        self.assertEqual(st["en"], "The evidence is very uncertain about the effect of X on mortality.")
        st = G.statement("moderate", "reduce", False, o, "X")
        self.assertEqual(st["en"], "X probably results in little to no difference in mortality.")
        self.assertIsNone(G.statement(None, "reduce"))

    def test_sof_from_grade_doc_and_exports(self):
        with open(os.path.join(EXAMPLES, "ma.grade.v1.rogzitett.json"), encoding="utf-8") as fh:
            grade = json.load(fh)
        grade["run_id"] = None
        doc = G.sof(self.view, grade=grade, outcome_id="o1", label={"hu": "Tbc", "en": "TB"},
                    footnotes=["=SUM(A1) kézi megjegyzés"])
        row = doc["rows"][0]
        self.assertEqual(row["certainty"], "moderate")
        self.assertEqual(row["design"], "RCT")
        kinds = [(f["kind"], f.get("domain")) for f in row["footnotes"]]
        self.assertIn(("grade", "inconsistency"), kinds)
        self.assertIn(("grade", "publication_bias"), kinds)         # feloldott „gyanított” 0 lépéssel is lábjegyzet
        self.assertNotIn(("grade", "risk_of_bias"), kinds)
        self.assertEqual(row["footnote_refs"]["certainty"], ["a", "b"])
        md = G.sof_markdown(doc, "en")
        self.assertIn("| Outcome | Participants (studies) |", md)
        self.assertIn("a) Inconsistency: −1 (serious)", md)
        csv_text = G.sof_csv(dict(doc, rows=[dict(row, label={"hu": "=1+1", "en": "=1+1"})]), "hu")
        self.assertIn("'=1+1;", csv_text)                                   # Excel-biztos: képlet helyett szöveg
        self.assertIn("d) =SUM(A1) kézi megjegyzés", csv_text)
        html_text = G.sof_html(dict(doc, rows=[dict(row, label={"hu": "<b>x</b>", "en": "x"})]), "hu")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html_text)
        self.assertNotIn("<b>x</b>", html_text)

    def test_flat_cells_and_columns(self):
        doc = G.sof(self.view, assumed_risks=[{"source": "control_pool"},
                                              {"label": "magas", "source": "external", "per_1000": "40",
                                               "note": "kohorsz 2020"}],
                    certainty="moderate", outcome_id="o1", footnotes=[{"id": None, "text": "saját megjegyzés"}])
        row = doc["rows"][0]
        self.assertEqual([c["key"] for c in doc["columns"]], list(G.SOF_COLUMN_ORDER))
        for c in doc["columns"]:
            self.assertIn(c["key"], row)
            self.assertTrue(row[c["key"]]["hu"] and row[c["key"]]["en"])
        self.assertEqual(row["absolute_text"]["en"], "5 fewer per 1,000 (from 6 fewer to 2 fewer); "
                                                     "20 fewer per 1,000 (from 27 fewer to 11 fewer)")
        self.assertEqual(row["assumed_risk_text"]["hu"], "kontroll-pool: 1000 főre 9 a; magas: 1000 főre 40 b")
        self.assertEqual(row["absolute"][1]["source"], "kohorsz 2020")
        self.assertEqual(row["certainty_text"]["hu"], "⊕⊕⊕◯ mérsékelt")              # hivatkozás nélkül
        self.assertEqual(row["comments_text"]["en"], row["statement"]["en"] + " c")
        self.assertEqual([f["id"] for f in doc["footnotes"]], ["a", "b", "c"])
        self.assertEqual(doc["statement"], row["statement"])
        with self.assertRaises(ValueError):
            G.sof(self.view, outcome_id="o1", run_id=RID)                 # explore-futás: nincs run_id
        self.assertIsNone(G.sof(self.view, outcome_id="o1", run_id=None)["run_id"])

    def test_sof_mismatch_detection(self):
        doc = G.sof(self.view, certainty="high", outcome_id="o1")
        self.assertEqual(G.sof_run_mismatches(doc, self.view), [])
        bad = copy.deepcopy(doc)
        bad["rows"][0]["relative"]["display_text"] = {"hu": "0.50 [0.33; 0.73]", "en": "0.50 [0.33; 0.73]"}
        bad["rows"][0]["absolute"][0]["text"] = {"hu": "x", "en": "x"}
        probs = G.sof_run_mismatches(bad, self.view)
        self.assertEqual(len(probs), 2)


# ------------------------------------------------------------------ GRADE-tár, napló, X019
class _Project(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="szk_grade_")
        P.init(self.root, "BCG próba")
        header, rows = bcg_rows()
        with open(os.path.join(self.root, "03_adatok", "o1.csv"), "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(header + ["rob"])
            for r in rows:
                w.writerow(r + [ALLOC_ROB[r[7]]])
        P.add_outcome(self.root, {"id": "o1", "name": {"hu": "Tbc", "en": "Tuberculosis"}, "data": "03_adatok/o1.csv",
                                  "measure": "RR", "critical": True, "grade_start": "high"})
        os.makedirs(os.path.join(self.root, "05_elemzes", "specs"), exist_ok=True)
        sp = S.spec_from_argv(["analyze", "--data", os.path.join(self.root, "03_adatok", "o1.csv"), "--measure", "RR"],
                              project_root=self.root, name="o1_primary", outcome="o1")
        self.spec_path = os.path.join(self.root, "05_elemzes", "specs", "o1_primary.json")
        api.save_spec(self.spec_path, sp)
        self.primary = api.analyze(self.spec_path, mode="commit", project_root=self.root)
        self.run_dir = os.path.join(self.root, os.path.dirname(self.primary["run"]["files"]["results"]["path"]))
        child = json.loads(json.dumps(sp))
        child.update(name="o1_magas_rob_nelkul", purpose="sensitivity", parent="o1_primary",
                     filters={"exclude": ["rob=high"], "include": []})
        child.pop("prespecified", None)
        cp = os.path.join(self.root, "05_elemzes", "specs", "o1_magas_rob_nelkul.json")
        api.save_spec(cp, child)
        self.child = api.analyze(cp, mode="commit", project_root=self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def decided(self, pb=None):
        d = G.advice(self.run_dir, project_dir=self.root)
        d["domains"]["risk_of_bias"].update(rating="not serious", rationale="A kizárás nem változtat.")
        d["domains"]["inconsistency"].update(rating="serious", rationale="A PI átnyúlik az 1-en.")
        d["domains"]["indirectness"].update(rating="not serious", rationale="PICO egyezik.")
        d["domains"]["imprecision"].update(rating="not serious", rationale="OIS teljesül.")
        d["domains"]["publication_bias"].update(pb or {"rating": "suspected"})
        return d


class TestProjectFlow(_Project):
    def test_advice_from_run_dir_finds_child_run_and_meta(self):
        d = G.advice(self.run_dir, project_dir=self.root)
        self.assertEqual((d["outcome_id"], d["run_id"], d["start"], d["importance"]),
                         ("o1", self.primary["run"]["run_id"], "high", "critical"))
        sens = d["domains"]["risk_of_bias"]["advisory"]["sensitivity_run"]
        self.assertEqual(sens["run_id"], self.child["run"]["run_id"])
        self.assertEqual(sens["k"], 7)
        self.assertEqual(sens["display_text"], self.child["run"]["primary"]["display_text"])
        self.assertFalse(sens["conclusion_changed"])
        # a futás saját rob oszlopából (plot/v2 flags.rob)
        self.assertAlmostEqual(d["domains"]["risk_of_bias"]["advisory"]["high_rob_weight_pct"], 49.4813, places=3)
        # run_id + projektmappa, és a run.json útja is működik
        d2 = G.advice(self.primary["run"]["run_id"], project_dir=self.root)
        d3 = G.advice(os.path.join(self.run_dir, "run.json"), project_dir=self.root)
        self.assertEqual(json.dumps(d2, sort_keys=True), json.dumps(d, sort_keys=True))
        self.assertEqual(json.dumps(d3, sort_keys=True), json.dumps(d, sort_keys=True))
        self.assertEqual(G.grade_run_mismatches(d, self.run_dir), [])

    def test_unresolved_blocks_recording_then_resolve_and_record(self):
        saved = P.save_grade_doc(self.root, self.decided(), actor="user:SzK")
        self.assertIsNone(saved["certainty"])                                # 4.14: addig null
        self.assertEqual(saved["domains"]["publication_bias"]["status"], "unresolved")
        self.assertEqual(saved["actor"], "user:SzK")
        self.assertEqual(schema_errors(saved, "ma.grade"), [])
        with self.assertRaises(ValueError) as cm:
            P.record_grade_doc(self.root, saved)
        self.assertIn("feloldatlan", str(cm.exception))
        self.assertIn("X019", str(cm.exception))
        un = G.unresolved_publication_bias(self.root)
        self.assertEqual([(u["outcome_id"], u["path"]) for u in un], [("o1", "06_kezirat/grade/o1.grade.json")])
        f = G.x019_findings(self.root)
        self.assertEqual([x["code"] for x in f], ["X019"])
        self.assertEqual(f[0]["kb_refs"], ["D-S13-008", "GRADE-07"])
        self.assertEqual(api.project_list(self.root, "grades"), [])            # semmi nem került a naplóba
        # feloldás indoklás nélkül: hiba; indoklással: rögzíthető
        bad = copy.deepcopy(saved)
        bad["domains"]["publication_bias"].update(step=0, rationale=None)
        with self.assertRaises(ValueError):
            P.save_grade_doc(self.root, bad)
        saved["domains"]["publication_bias"].update(step=0, rationale="Harbord és Peters nem jelez; a "
                                                                     "regiszterkeresés teljes.")
        s2 = P.save_grade_doc(self.root, saved)
        self.assertEqual((s2["certainty"], s2["certainty_source"]), ("moderate", "computed"))
        self.assertEqual(s2["domains"]["publication_bias"]["status"], "resolved")
        self.assertEqual(G.unresolved_publication_bias(self.root), [])
        # methodology:M5 — a számolt szint csak előtöltés: rögzítés emberi megerősítéssel
        with self.assertRaises(P.GradeRecordError) as cm:
            P.record_grade_doc(self.root, s2, actor="user:SzK")
        self.assertTrue(cm.exception.needs_certainty)
        self.assertEqual(cm.exception.computed_certainty, "moderate")
        rec = P.record_grade_doc(self.root, s2, actor="user:SzK", certainty="moderate")
        self.assertEqual(rec["warnings"], [])
        self.assertEqual(rec["doc"]["status"], "recorded")
        self.assertEqual(rec["doc"]["journal_id"], rec["id"])
        row = api.project_show(self.root, "grade", rec["id"])
        self.assertEqual((row["outcome"], row["certainty"], row["k"], row["participants"]),
                         ("o1", "moderate", 13, 357347))
        self.assertEqual(row["effect"], "RR 0.49 [0.33; 0.73]")
        self.assertTrue(row["inconsistency"].startswith("−1 serious: "))
        self.assertTrue(row["publication_bias"].startswith("0 suspected (resolved): "))
        self.assertTrue(row["rationale"].startswith("Kiindulás: magas"))
        self.assertIsNone(P.grade_consistency(row["certainty"], **{d: row[d] for d in P.GRADE_DOMAINS + ("upgrades",)}))
        self.assertEqual(G.grade_run_mismatches(row, self.run_dir), [])
        loaded = P.load_grade_doc(self.root, "o1")
        self.assertEqual(loaded["status"], "recorded")
        self.assertEqual(schema_errors(loaded, "ma.grade"), [])
        # SoF a mentett ítéletből (lábjegyzetek az indoklásokból), mentés és X008-összevetés
        so = G.sof(self.run_dir, project_dir=self.root)
        self.assertEqual(so["rows"][0]["certainty"], "moderate")
        self.assertEqual(so["rows"][0]["label"], {"hu": "Tbc", "en": "Tuberculosis"})
        G.save_sof(self.root, so)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "06_kezirat", "sof", "o1.sof.json")))
        self.assertEqual(G.sof_run_mismatches(G.load_sof(self.root, "o1"), self.run_dir), [])
        self.assertEqual(schema_errors(G.load_sof(self.root, "o1"), "ma.sof"), [])

    def test_strongly_suspected_human_certainty_and_override_warnings(self):
        d = self.decided({"rating": "strongly suspected"})
        d["domains"]["inconsistency"]["rationale"] = None          # a tanács −1, az ember is −1, de indoklás nélkül
        d["domains"]["imprecision"].update(rating="not serious", rationale=None)
        d["domains"]["imprecision"]["suggestion"] = dict(d["domains"]["imprecision"]["suggestion"], step=-1,
                                                         rating="serious")
        d["certainty"] = "moderate"                                # ember adta, de a lépésekből 'low' adódik
        s = P.save_grade_doc(self.root, d)
        self.assertEqual(s["domains"]["publication_bias"]["step"], -1)
        self.assertEqual(s["certainty_source"], "human")
        self.assertIn("'low'", s["consistency_warning"])
        codes = sorted((w["code"], w["domain"]) for w in s["override_warnings"])
        self.assertIn(("rationale_missing", "inconsistency"), codes)
        self.assertIn(("rationale_missing", "publication_bias"), codes)
        self.assertIn(("advice_downgrade_unexplained", "imprecision"), codes)
        rec = P.record_grade_doc(self.root, s)
        self.assertTrue(any("'low'" in w for w in rec["warnings"]))
        # −2 csak indoklással
        d2 = self.decided({"rating": "strongly suspected", "step": -2})
        with self.assertRaises(ValueError):
            P.save_grade_doc(self.root, d2)

    def test_ai_draft_needs_approval_and_explore_runs_cannot_be_recorded(self):
        d = self.decided({"rating": "undetected"})
        d["origin"] = "ai_draft"
        s = P.save_grade_doc(self.root, d)
        with self.assertRaises(ValueError) as cm:
            P.record_grade_doc(self.root, s)
        self.assertIn("AI-vázlat", str(cm.exception))
        s["approved_by"] = "user:SzK"
        self.assertTrue(P.record_grade_doc(self.root, s, certainty=s["certainty"])["id"] >= 1)   # emberi megerősítés
        draft = G.advice(explore(BCG, "RR"), outcome_id="o1", start="high")
        for dom in draft["domains"].values():
            dom.update(rating="not serious" if dom is not draft["domains"]["publication_bias"] else "undetected",
                       rationale="x")
        with self.assertRaises(ValueError) as cm:
            P.record_grade_doc(self.root, draft)
        self.assertIn("commit-futás", str(cm.exception))

    def test_upgrades_with_details(self):
        d = self.decided({"rating": "undetected", "rationale": "teljes keresés"})
        d["start"] = "low"
        d["upgrades"] = {"large_effect": True, "dose_response": False, "opposing_confounding": False}
        d["upgrade_details"] = {"large_effect": {"step": 2, "rationale": "RR 0.19, konzisztens"}}
        s = P.save_grade_doc(self.root, d)
        self.assertEqual(P.grade_upgrade_steps(s), {"large_effect": 2})
        # methodology:M5 — egy szabály: felminősítés leminősítés mellett nem számít (alacsony − 1, a +2 nem)
        self.assertEqual(s["certainty"], "very low")
        self.assertTrue(P.grade_doc_texts(s)["upgrades"].startswith("+2 large_effect (+2)"))
        self.assertIn(("upgrade_with_downgrade", None), [(w["code"], w["domain"]) for w in s["override_warnings"]])
        bad = copy.deepcopy(d)
        bad["upgrade_details"] = {"dose_response": {"step": 2}}
        with self.assertRaises(ValueError):
            P.save_grade_doc(self.root, bad)


class TestGradeDocValidation(unittest.TestCase):
    def base(self):
        with open(os.path.join(EXAMPLES, "ma.grade.v1.rogzitett.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_examples_and_rules(self):
        self.assertEqual(P.validate_grade_doc(self.base())[1], [])
        cases = [
            (("inconsistency", {"rating": "serious", "step": 0}), "−1"),
            (("publication_bias", {"rating": "suspected", "step": None, "status": "resolved"}), "resolved"),
            (("publication_bias", {"rating": "suspected", "step": -1}), "indoklás"),
            (("publication_bias", {"rating": "suspected", "step": -2, "rationale": "x"}), "0 vagy −1"),
            (("publication_bias", {"rating": "undetected", "step": -1}), "0 lépés"),
            (("publication_bias", {"rating": "strongly suspected", "step": 0}), "−1"),
            (("risk_of_bias", {"rating": "rossz"}), "rating"),
            (("risk_of_bias", {"rating": None, "step": -1}), "ítélet nélkül"),
        ]
        for (dom, val), needle in cases:
            doc = self.base()
            doc["domains"][dom] = val
            with self.subTest(domain=dom, value=val):
                errs = P.validate_grade_doc(doc)[1]
                self.assertTrue(errs)
                self.assertIn(needle, "; ".join(errs))
                self.assertTrue(schema_errors(doc, "ma.grade"))           # a szerződés is elutasítja
        for key, val in (("outcome_id", "rossz id"), ("run_id", "x"), ("start", "közepes"), ("certainty", "jó"),
                         ("importance", "x"), ("status", "kész"), ("origin", "robot"), ("journal_id", 0)):
            doc = self.base()
            doc[key] = val
            with self.subTest(key=key):
                self.assertTrue(P.validate_grade_doc(doc)[1])
                self.assertTrue(schema_errors(doc, "ma.grade"))
        n, e = P.validate_grade_doc({"outcome_id": "o9"})                     # minimális piszkozat
        self.assertEqual(e, [])
        self.assertEqual(n["domains"]["publication_bias"]["status"], "open")
        self.assertEqual(schema_errors(n, "ma.grade"), [])
        self.assertTrue(P.validate_grade_doc({"outcome_id": "o9", "x": float("nan")})[1])

    def test_expected_certainty(self):
        doc = self.base()
        self.assertEqual(P.grade_doc_certainty(doc), "moderate")
        doc["start"] = "low"
        self.assertEqual(P.grade_doc_certainty(doc), "very low")
        doc["domains"]["publication_bias"] = {"rating": "suspected", "step": None, "status": "unresolved"}
        self.assertIsNone(P.grade_doc_certainty(doc))
        self.assertEqual(P.grade_doc_open(doc)["unresolved"], ["publication_bias"])


class TestAddGradeSuspectedRule(_Project):
    def test_journal_add_grade_refuses_unresolved_suspected(self):
        z = dict(risk_of_bias="0", inconsistency="0", indirectness="0", imprecision="0")
        for text in ("suspected", "gyanított", "Suspected — kevés vizsgálat", "0 suspected", "−1 gyanított"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    P.add_grade(self.root, "o1", "high", publication_bias=text, **z)
        warns = []
        P.add_grade(self.root, "o1", "high", publication_bias="0 gyanított (feloldva): a regiszterek teljesek",
                    warnings=warns, check_kb=False, **z)
        self.assertEqual(warns, [])
        warns = []
        P.add_grade(self.root, "o1", "moderate", publication_bias="strongly suspected", warnings=warns,
                    check_kb=False, **z)
        self.assertTrue(any("erősen gyanított" in w for w in warns))
        with self.assertRaises(ValueError):
            P.add_grade(self.root, "o1", "moderate", publication_bias="−2 erősen gyanított", **z)
        P.add_grade(self.root, "o1", "low", publication_bias="−2 erősen gyanított: 5 regisztrált, nem közölt",
                    check_kb=False, **z)


# ------------------------------------------------------------------ AMSTAR 2
def amstar(**over):
    a = {str(i): "yes" for i in range(1, 17)}
    a.update({k.lstrip("q"): v for k, v in over.items()})
    return a


class TestAmstar2(unittest.TestCase):
    TABLE = [
        # (válaszok, meets, weakness)
        (amstar(), "high", "high"),
        (amstar(q10="no"), "high", "high"),
        (amstar(q10="no", q16="no"), "moderate", "moderate"),
        (amstar(q7="no"), "low", "low"),
        (amstar(q7="no", q10="no", q16="no"), "low", "low"),
        (amstar(q2="no", q15="no"), "critically_low", "critically_low"),
        (amstar(q2="partial_yes"), "high", "high"),
        (amstar(q2="partial_yes", q4="partial_yes"), "high", "moderate"),
        (amstar(q2="partial_yes", q10="no"), "high", "moderate"),
        (amstar(q8="partial_yes", q10="no"), "high", "high"),
        (amstar(q11="no_meta_analysis", q12="no_meta_analysis", q15="no_meta_analysis"), "high", "high"),
        (amstar(q9="partial_yes", q13="no"), "low", "low"),
    ]

    def test_rating_table_both_conventions(self):
        for answers, meets, weak in self.TABLE:
            with self.subTest(answers={k: v for k, v in answers.items() if v != "yes"}):
                r = G.amstar2_consistency(answers)
                self.assertEqual(r["convention"], "meets")
                self.assertEqual(r["rating"], meets)
                self.assertEqual(r["by_convention"]["meets"]["rating"], meets)
                self.assertEqual(r["by_convention"]["weakness"]["rating"], weak)
                self.assertEqual(r["convention_sensitive"], meets != weak)
                self.assertEqual(G.amstar2_consistency(answers, "weakness")["rating"], weak)
                self.assertTrue(r["complete"])
                self.assertIsNotNone(r["sensitivity_text"]) if meets != weak else self.assertIsNone(
                    r["sensitivity_text"])

    def test_details(self):
        r = G.amstar2_consistency(amstar(q2="partial_yes", q4="partial_yes", q10="no"))
        self.assertEqual(r["partial_yes_critical"], ["2", "4"])
        self.assertEqual(r["weaknesses"], ["10"])
        self.assertEqual(r["by_convention"]["weakness"]["weaknesses"], ["2", "4", "10"])
        self.assertEqual(r["label"], {"hu": "MAGAS", "en": "HIGH"})
        self.assertIn("MÉRSÉKELT", r["sensitivity_text"]["hu"])
        self.assertEqual(r["kb_refs"], ["AMSTAR2-00", "D-S13-021"])
        self.assertTrue(any("≠ GRADE" in n["hu"] for n in r["notes"]))

    def test_aliases_missing_invalid_and_appraisal_doc(self):
        r = G.amstar2_consistency({"AMSTAR2-01": "igen", "02": "részben igen", 3: "Nem", "4": {"value": "PY"}})
        self.assertEqual(r["answered"], 4)
        self.assertTrue(r["provisional"])
        self.assertEqual(len(r["missing"]), 12)
        self.assertIn("IDEIGLENES", r["text"]["hu"])
        self.assertTrue(any("H4" in n["hu"] for n in r["notes"]))           # 'PY' → részben igen
        self.assertEqual(r["partial_yes_critical"], ["2", "4"])
        r = G.amstar2_consistency(amstar(q11="NA", q12="Nem volt metaanalízis", q15="not_applicable"))
        self.assertEqual((r["rating"], r["not_applicable"]), ("high", ["11", "12", "15"]))
        r = G.amstar2_consistency(amstar(q1="partial_yes", q3="no_meta_analysis", q17="yes", q5="talán"))
        self.assertEqual(sorted(x["item"] for x in r["invalid"]), ["1", "17", "3", "5"])
        self.assertFalse(r["complete"])
        doc = {"schema": "szk.appraisal/v1", "tool": "amstar2",
               "answers": {k: {"value": v} for k, v in amstar(q7="no").items()}}
        self.assertEqual(G.amstar2_consistency(doc)["rating"], "low")
        with self.assertRaises(ValueError):
            G.amstar2_consistency(amstar(), convention="szigorú")
        with self.assertRaises(ValueError):
            G.amstar2_consistency(["yes"])

    def test_claimed_rating(self):
        a = amstar(q2="partial_yes", q4="partial_yes")
        r = G.amstar2_consistency(a, claimed="HIGH")
        self.assertIsNone(r["consistency_warning"])
        r = G.amstar2_consistency(a, claimed="Moderate")                     # a validator 1.0.0 'weakness'-e
        self.assertIn("validator 1.0.0", r["consistency_warning"])
        r = G.amstar2_consistency(amstar(q10="no", q16="no"), claimed="alacsony")
        self.assertIsNone(r["consistency_warning"])                           # mérsékelt → alacsony megengedett
        self.assertTrue(any("indokold" in n["hu"] for n in r["notes"]))
        r = G.amstar2_consistency(amstar(), claimed="kiváló")
        self.assertIn("nem AMSTAR 2-szint", r["consistency_warning"])
        r = G.amstar2_consistency(amstar(q7="no"), claimed="critically low")
        self.assertIn("nem egyezik", r["consistency_warning"])

    @unittest.skipUnless(os.path.isfile(VALIDATOR), "nincs meg a validator plugin (csak olvasva használjuk)")
    def test_weakness_convention_equals_validator_1_0_0(self):
        spec = importlib.util.spec_from_file_location("szk_validator_appraise_ro", VALIDATOR)
        mod = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode, old = True, sys.dont_write_bytecode
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:                                            # noqa: BLE001
            self.skipTest("a validator nem tölthető be: %s" % exc)
        finally:
            sys.dont_write_bytecode = old

        class Inst(object):
            meta = {"critical": "2, 4, 7, 9, 11, 13, 15"}
        items = [{"id": str(i)} for i in range(1, 17)]
        words = {"yes": "Yes", "partial_yes": "Partial yes", "no": "No"}
        for answers, _meets, weak in self.TABLE:
            if "no_meta_analysis" in answers.values():                      # a validator 1.0.0 ezt hibának veszi
                continue
            lines = mod.rollup_amstar2(Inst(), {k: words[v] for k, v in answers.items()}, items)
            got = [x for x in lines if "OVERALL CONFIDENCE" in x][0].split(":")[1].strip().lower().replace(" ", "_")
            with self.subTest(answers={k: v for k, v in answers.items() if v != "yes"}):
                self.assertEqual(got, weak)


# ------------------------------------------------------------------ szerződések, X019-metaadat
class TestContracts(unittest.TestCase):
    def test_schema_files(self):
        for name in ("ma.grade", "ma.sof"):
            s = K.load(name, 1)
            with self.subTest(contract=name):
                self.assertEqual(s["$id"], "urn:szk:contract:%s:1" % name)
                self.assertEqual(s["properties"]["schema"]["const"], "szk.%s/v1" % name)
                self.assertEqual(s.get("$comment"), K.PROSE_COMMENT)
                self.assertEqual(K.raw(name, 1).decode("utf-8"), K.canonical_text(s))

    def test_examples(self):
        names = sorted(f for f in os.listdir(EXAMPLES) if f.startswith(("ma.grade.v1.", "ma.sof.v1.")))
        self.assertGreaterEqual(len(names), 10)
        for fn in names:
            with open(os.path.join(EXAMPLES, fn), encoding="utf-8") as fh:
                doc = json.load(fh)
            name = "ma.grade" if fn.startswith("ma.grade") else "ma.sof"
            errs = schema_errors(doc, name)
            with self.subTest(example=fn):
                if ".invalid-" in fn:
                    self.assertTrue(errs)
                    if name == "ma.grade":
                        self.assertTrue(P.validate_grade_doc(doc)[1])          # a motor ellenőrzője is elutasítja
                else:
                    self.assertEqual(errs, [])
                    if name == "ma.grade":
                        self.assertEqual(P.validate_grade_doc(doc)[1], [])

    def test_x019_metadata(self):
        sev, title, advice, source = G.X_RULES["X019"]
        self.assertEqual(sev, "error")
        self.assertEqual((G.X_RULE_STAGES["X019"], G.X_ESCALATION["X019"]), ("S13", "S13"))
        self.assertIn("suspected", title)
        self.assertTrue(G.X_TITLES_EN["X019"])


if __name__ == "__main__":
    unittest.main()
