# -*- coding: utf-8 -*-
"""v1 javítások — C köteg (független újraellenőrzés): ami az A / B köteg után nyitva maradt vagy újonnan sérült.

- contracts:M1 — az api.rob_sync_proposal is továbbadja a kimenetet (outcome) a motornak;
- methodology:M5 — az emberi megerősítés útja a homlokzaton és a parancssorban: api.grade_record(certainty=…),
  `ma.py grade record --certainty`; megerősítés nélkül a CLI megmondja a számolt szintet;
- methodology:M11 — az AMSTAR 2 9. / 11. tételének RCT / NRSI részei a grade_help.amstar2_consistency-ben
  (`ma.py grade amstar2`, munkapad AMSTAR 2-lap) és az X012-ben is számítanak (bármelyik rész „Nem” → „Nem”);
- methodology:M2 (utóhatás) — a ROBINS-I 2016-os számozása ugyanazokkal az azonosítókkal más kérdést jelöl, mint a
  validator 1.0.0: a régi számozásúnak látszó (más definíció-hash-ű) értékelésre figyelmeztetés;
- methodology:M1 — a RoB 2 (2019) folyamatábráinak FÜGGETLEN újrakódolása a motor szabályai ellen.
"""
import itertools
import json
import os
import shutil
import tempfile
import unittest

from _helpers import ROOT  # noqa: F401  (sys.path)
from metaelemzes import api, appraisal as AP, audit as AU, grade_help as G, instruments as I, projekt as P

import test_v1_audit as TA          # csak a projektépítő és a segédek (tesztosztályt nem húzunk be)
import test_v1_grade as TG
from test_v1_facade import run_cli

TS = "2026-10-05T10:00:00Z"
Y, PY, PN, N, NI, NA = "yes", "probably_yes", "probably_no", "no", "no_information", "not_applicable"
YES, NO = (Y, PY), (N, PN)


def rob2_doc(unit="S1", assessor="SzK", status="complete", outcome=None, key=None, **ch):
    ans = {"1.1": Y, "1.2": Y, "1.3": N, "2.1": Y, "2.2": Y, "2.3": N, "2.4": NA, "2.5": NA, "2.6": Y, "2.7": NA,
           "3.1": Y, "3.2": NA, "3.3": NA, "3.4": NA, "4.1": N, "4.2": N, "4.3": N, "4.4": NA, "4.5": NA,
           "5.1": Y, "5.2": N, "5.3": N}
    ans.update({k.replace("_", "."): v for k, v in ch.items()})
    tg = {"unit": unit, "study_id": unit}
    if outcome:
        tg["outcome"] = outcome
    if key:
        tg["key"] = key
    d = {"schema": "szk.appraisal/v1", "tool": "rob2", "scope": "assignment", "target": tg, "assessor": assessor,
         "second_assessor": None, "status": status, "origin": "human", "approved_by": None, "approved_at": None,
         "answers": {k: {"value": v} for k, v in ans.items()}, "domain_judgements": [], "applicability": [],
         "overall": None}
    r = AP.check(d)
    d["domain_judgements"] = [{"domain": x["domain"], "pass": None, "judgement": x["implied"]} for x in r["domains"]]
    d["overall"] = {"judgement": r["overall"]["implied"], "rationale": "indok"}
    return d


# ================================================================== contracts:M1 (homlokzat)
class TestM1ApiRobSyncForwardsOutcome(unittest.TestCase):
    def test_api_rob_sync_proposal_filters_by_outcome(self):
        d = rob2_doc(unit="S3", outcome="o2", key="o2", **{"1_2": N})          # 1.2 N → magas
        self.assertEqual(d["overall"]["judgement"], "high")
        header, rows = ["study_id", "study", "rob"], [["S3", "Rosenthal 1960", "low"]]
        self.assertEqual(api.rob_sync_proposal([d], header, rows, "rob2", outcome="o1")["changes"], [],
                         "az o2-re szóló értékelés nem írhat az o1 táblájába (a homlokzaton át sem)")
        self.assertEqual(api.rob_sync_proposal([d], header, rows, "rob2", outcome="o1")["outcome"], "o1")
        self.assertEqual([c["after"] for c in api.rob_sync_proposal([d], header, rows, "rob2", outcome="o2")[
            "changes"]], ["high"])
        self.assertEqual([c["after"] for c in api.rob_sync_proposal([d], header, rows, "rob2")["changes"]], ["high"],
                         "kimenet nélkül (kimenet-független hívás) a korábbi viselkedés marad")


# ================================================================== methodology:M5 (megerősítés a homlokzaton / CLI-ben)
class TestM5HumanConfirmationPath(TG._Project):
    def computed_doc(self):
        d = self.decided({"rating": "undetected"})
        d["start"] = "low"
        d["domains"]["inconsistency"].update(rating="not serious", rationale="I² kicsi")
        d["domains"]["imprecision"].update(rating="serious", rationale="a CI átlépi a MID-et")
        d["upgrades"] = {"large_effect": 2, "dose_response": False, "opposing_confounding": False}
        d["upgrade_details"] = {"large_effect": {"step": 2, "rationale": "RR < 0.2"}}
        s = api.grade_put(self.root, "o1", d)
        self.assertEqual((s["certainty"], s["certainty_source"]), ("very low", "computed"))
        return s

    def test_api_grade_record_takes_the_human_certainty(self):
        self.computed_doc()
        with self.assertRaises(P.GradeRecordError) as cm:
            api.grade_record(self.root, "o1", actor="user:SzK")
        self.assertEqual((cm.exception.needs_certainty, cm.exception.computed_certainty), (True, "very low"))
        with self.assertRaises(ValueError):
            api.grade_record(self.root, "o1", actor="user:SzK", certainty="közepes")
        rec = api.grade_record(self.root, "o1", actor="user:SzK", certainty="very_low")     # álnév is
        self.assertEqual((rec["doc"]["certainty"], rec["doc"]["certainty_source"], rec["doc"]["status"]),
                         ("very low", "human", "recorded"))
        self.assertEqual(TG.schema_errors(rec["doc"], "ma.grade"), [])

    def test_cli_grade_record_certainty_flag_and_hint(self):
        self.computed_doc()
        rc, out, err = run_cli("grade", "record", self.root, "--outcome", "o1", "--json")
        self.assertEqual(rc, 1)
        self.assertIn("GRADE-09", err)
        self.assertIn('--certainty "very low"', err, "a CLI megmutatja a számolt szintet és a megerősítés módját")
        self.assertEqual(api.project_list(self.root, "grades"), [])
        rc, out, err = run_cli("grade", "record", self.root, "--outcome", "o1", "--certainty", "low", "--json")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertEqual((res["doc"]["certainty"], res["doc"]["certainty_source"]), ("low", "human"))
        self.assertTrue(any("'very low'" in w for w in res["warnings"]), "az eltérés a számolttól jelezve")


# ================================================================== methodology:M11 (AMSTAR 2 részek a grade_help-ben)
def amstar(**ch):
    a = {str(i): Y for i in range(1, 17)}
    a.update({k.lstrip("q"): v for k, v in ch.items()})
    return a


class TestM11Amstar2PartsInConsistency(unittest.TestCase):
    def test_no_on_either_part_is_a_critical_flaw(self):
        for item in ("9", "11"):
            for parts in ({"RCT": Y, "NRSI": N}, {"RCT": N, "NRSI": Y}):
                with self.subTest(item=item, parts=parts):
                    r = G.amstar2_consistency(amstar(**{item: {"parts": parts}}))
                    self.assertEqual((r["rating"], r["critical_flaws"], r["complete"]), ("low", [item], True))
                    doc = {"schema": "szk.appraisal/v1", "tool": "amstar2",
                           "answers": {k: (v if isinstance(v, dict) else {"value": v})
                                       for k, v in amstar(**{item: {"parts": parts}}).items()}}
                    self.assertEqual(G.amstar2_consistency(doc)["rating"], "low", "szk.appraisal/v1 dokumentumból is")
                    self.assertEqual(api.amstar2_consistency(doc)["rating"],
                                     api.appraisal_check(dict(doc, scope="all"))["amstar2"]["rating"],
                                     "ugyanaz a besorolás, mint az értékelés-motorban")

    def test_only_one_design_and_no_meta_analysis(self):
        r = G.amstar2_consistency(amstar(**{"9": {"parts": {"RCT": Y, "NRSI": NA}},
                                            "11": {"parts": {"RCT": NA, "NRSI": NA}}}))
        self.assertEqual((r["rating"], r["not_applicable"], r["parts_missing"]), ("high", ["11"], []))
        r = G.amstar2_consistency(amstar(**{"9": {"parts": {"RCT": "partial_yes", "NRSI": Y}}}))
        self.assertEqual((r["rating"], r["partial_yes_critical"]), ("high", ["9"]))

    def test_contradicting_value_is_invalid_and_missing_parts_noted(self):
        r = G.amstar2_consistency(amstar(**{"9": {"value": Y, "parts": {"RCT": Y, "NRSI": N}}}))
        self.assertEqual([x["item"] for x in r["invalid"]], ["9"])
        self.assertTrue(r["provisional"], "ellentmondó érték: a besorolás csak ideiglenes")
        r = G.amstar2_consistency(amstar())
        self.assertEqual((r["rating"], r["parts_missing"]), ("high", ["9", "11"]))
        self.assertTrue(any("RCT" in n["hu"] and "NRSI" in n["hu"] for n in r["notes"]))

    def test_x012_uses_the_parts(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        pj = TA.Proj(tmp)
        pj.ready(appraisal_tools=["rob2", "amstar2"])
        pj.appraise_all()
        answers = {str(i): {"value": Y} for i in range(1, 17)}
        answers["9"] = {"parts": {"RCT": Y, "NRSI": N}}               # érték nélkül, csak részekkel
        rel = pj.appraisal("review", tool="amstar2", status="complete", outcome=None, answers=answers,
                           overall={"judgement": "high", "rationale": "x"})
        x = TA.codes(pj.audit(stage="FINAL"), "X012")
        self.assertEqual(len(x), 1, "a részekből 'Nem' (kritikus hiba) → a rögzített 'high' nem egyezik")
        self.assertIn("low", x[0]["detail"])
        self.assertNotIn("hiányos", x[0]["detail"], "a részekkel megadott tétel megválaszoltnak számít")
        pj.appraisal("review", tool="amstar2", status="complete", outcome=None, answers=answers,
                     overall={"judgement": "low", "rationale": "x"})
        self.assertEqual(TA.codes(pj.audit(stage="FINAL"), "X012"), [])
        answers["9"] = {"value": Y, "parts": {"RCT": Y, "NRSI": N}}  # ellentmondó érték
        pj.appraisal("review", tool="amstar2", status="complete", outcome=None, answers=answers,
                     overall={"judgement": "high", "rationale": "x"})
        x = TA.codes(pj.audit(stage="FINAL"), "X012")
        self.assertEqual(len(x), 1)
        self.assertIn("ellentmond", x[0]["detail"])
        self.assertIn(rel, x[0]["artifacts"])


# ================================================================== methodology:M2 utóhatás (ROBINS-I számozás)
class TestRobinsINumberingNote(unittest.TestCase):
    def setUp(self):
        self.inst = I.load("robins-i")
        self.ref = self.inst.doc["reference_sha256"]

    def doc(self, sha, **answers):
        return {"schema": "szk.appraisal/v1", "tool": "robins-i", "scope": "adherence", "instrument_sha256": sha,
                "target": {"unit": "S1", "study_id": "S1"}, "answers": {k: {"value": v} for k, v in answers.items()}}

    def notes(self, doc):
        return [w for w in AP.check(doc)["warnings"] if "4.4" in w["en"] and "numbering" in w["en"]]

    def test_old_numbering_suspected_only_with_another_definition(self):
        old = self.doc(self.ref, **{"4.4": Y, "4.5": Y, "4.6": Y, "5.3": Y})          # validator-számozás gyanúja
        self.assertEqual(len(self.notes(old)), 1)
        self.assertTrue(any("4.4" in w and "számozás" in w for w in AP.problems(old)["warnings"]))
        self.assertEqual(self.notes(self.doc(self.inst.sha256, **{"4.4": Y})), [], "a mostani definícióval: nincs")
        self.assertEqual(self.notes(self.doc(None, **{"4.4": Y})), [], "hash nélkül (új dokumentum): nincs")
        newer = self.doc(self.ref, **{"4.4": Y, "6.4": N})                             # csak-új tétel → új számozás
        self.assertEqual(self.notes(newer), [])
        self.assertEqual(self.notes(self.doc(self.ref, **{"1.1": N, "6.1": N})), [], "érintett tétel nélkül: nincs")

    def test_rob2_and_save_unaffected(self):
        d = rob2_doc()
        d["instrument_sha256"] = "0" * 64
        self.assertFalse(any("numbering" in w["en"] for w in AP.check(d)["warnings"]))
        self.assertIsNone(AP.numbering_note(I.load("rob2"), d))


# ================================================================== methodology:M1 (független folyamatábra-kódolás)
def _worst(*t):
    order = {"low": 0, "some_concerns": 1, "high": 2}
    return max(t, key=order.get)


def ref_d1(a):
    if a["1.2"] in NO:
        return "high"
    if a["1.2"] == NI:
        return "high" if a["1.3"] in YES else "some_concerns"
    if a["1.1"] in NO:                     # a 2019-es kritériumtábla (PMC8191126, 2. táblázat): „némi aggály”, akkor
        return "some_concerns"             # is, ha az 1.3 is problémára utal (rejtett szekvencia mellett nincs „magas”)
    return "some_concerns" if a["1.3"] in YES else "low"


def ref_d2(a):
    if (a["2.1"] in NO and a["2.2"] in NO) or a["2.3"] in NO:
        p1 = "low"
    elif a["2.3"] == NI or a["2.4"] in NO or a["2.5"] in YES:
        p1 = "some_concerns"
    else:
        p1 = "high"
    p2 = "low" if a["2.6"] in YES else ("some_concerns" if a["2.7"] in NO else "high")
    return _worst(p1, p2)


def ref_d3(a):
    if a["3.1"] in YES or a["3.2"] in YES or a["3.3"] in NO:
        return "low"
    return "some_concerns" if a["3.4"] in NO else "high"


def ref_d4(a):
    if a["4.1"] in YES or a["4.2"] in YES:
        return "high"
    if a["4.3"] in NO or a["4.4"] in NO:
        return "low" if a["4.2"] in NO else "some_concerns"
    return "some_concerns" if a["4.5"] in NO else "high"


def ref_d5(a):
    if a["5.2"] in YES or a["5.3"] in YES:
        return "high"
    if NI in (a["5.2"], a["5.3"]):
        return "some_concerns"
    return "low" if a["5.1"] in YES else "some_concerns"


class TestM1Rob2IndependentFlowcharts(unittest.TestCase):
    """A RoB 2 (2019) doménenkénti folyamatábráinak független újrakódolása: a motor szabályainak minden ágon ugyanazt
    kell adnia (a válaszok Y / PN / NI mintájával — a PY = Y és az N = PN ágakat a szabályok azonosan kezelik)."""
    BASE = {"1.1": Y, "1.2": Y, "1.3": N, "2.1": N, "2.2": N, "2.6": Y, "3.1": Y, "4.1": N, "4.2": N, "4.3": N,
            "5.1": Y, "5.2": N, "5.3": N}
    DOMAINS = {"1": (ref_d1, ["1.1", "1.2", "1.3"]),
               "2": (ref_d2, ["2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"]),
               "3": (ref_d3, ["3.1", "3.2", "3.3", "3.4"]),
               "4": (ref_d4, ["4.1", "4.2", "4.3", "4.4", "4.5"]),
               "5": (ref_d5, ["5.1", "5.2", "5.3"])}

    def test_every_branch_matches(self):
        bad = []
        for did, (fn, keys) in self.DOMAINS.items():
            choices = [[Y, PN] if k == "3.2" else [Y, PN, NI] for k in keys]
            for combo in itertools.product(*choices):
                ans = {k: v for k, v in self.BASE.items() if not k.startswith(did + ".")}
                ans.update(zip(keys, combo))
                doc = {"schema": "szk.appraisal/v1", "tool": "rob2", "answers": {k: {"value": v}
                                                                                 for k, v in ans.items()}}
                got = next(d["implied"] for d in AP.check(doc)["domains"] if d["domain"] == did)
                want = fn(dict(zip(keys, combo)))
                if got != want:
                    bad.append((did, dict(zip(keys, combo)), want, got))
        self.assertEqual(bad[:5], [], "%d eltérés a folyamatábrától" % len(bad))

    def test_not_applicable_on_an_asked_question_is_no_information(self):
        # 2.3 I, 2.4 NI → a 2.5 kérdezendő; a 'Nem alkalmazható' NI-ként számít → magas (a régi motor: alacsony)
        doc = {"schema": "szk.appraisal/v1", "tool": "rob2", "answers": {k: {"value": v} for k, v in dict(
            self.BASE, **{"2.1": Y, "2.2": Y, "2.3": Y, "2.4": NI, "2.5": NA}).items()}}
        d2 = next(d for d in AP.check(doc)["domains"] if d["domain"] == "2")
        self.assertEqual((d2["implied"], d2["routing_conflicts"]), ("high", ["2.5"]))


if __name__ == "__main__":
    unittest.main()
