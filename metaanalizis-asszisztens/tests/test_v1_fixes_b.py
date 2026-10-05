# -*- coding: utf-8 -*-
"""v1 javítások — B köteg (GRADE / SoF / audit / ma.plot.v2): a megerősített hibák regressziós tesztjei.

- contracts:M2 — X003: a jóváhagyott AI-vázlat nem második értékelő (konszenzus > emberi > jóváhagyott AI);
- contracts:m1 — X012: a jóvá nem hagyott AI-vázlat nem az áttekintés AMSTAR 2 önellenőrzése;
- contracts:M4 (audit-oldal) — X019: a lezárt GRADE-értékelés (szk.appraisal/v1, tool grade) feloldatlan
  „suspected” publikációs torzítása találat (FINAL-kapu);
- contracts:M3 — SoF: a jóvá nem hagyott AI-vázlat GRADE-szintje nem kerül a SoF-ba (jelölve, nem menthető);
- contracts:M6 — ma.plot/v2: az E4c-megkötések (buborékpont x / y, számcellák, kumulatív k ≥ 1 …) vissza;
- contracts:m2 — egy GRADE-szótár: az aláhúzásos alakok ('very_low', 'not_serious' …) bemeneti álnevek;
- methodology:M5 — egy GRADE-szabály a két úton (felminősítés csak leminősítés nélkül), a számolt szint csak
  előtöltés: rögzítés emberi megerősítéssel;
- methodology:M6 — a SoF bizonyossága a rögzített GRADE-é (paraméter nem írja felül; mentés és X008);
- methodology:M8 — GRADE 18: ROBINS-I-gyel magas kiindulás → −2; alacsony kiindulásnál kettős számolás jelzése;
- methodology:M9 — az elrendezés nem a kiindulásból jön (SoF „Résztvevők (vizsgálatok)”, felminősítési tanács).
"""
import copy
import csv
import json
import os
import shutil
import tempfile
import unittest

from _helpers import ROOT
from metaelemzes import api, appraisal as AP, audit as A, contracts as K, grade_help as G, projekt as P

import test_v1_audit as TA          # csak a projektépítő és a segédek (tesztosztályt nem húzunk be)
import test_v1_grade as TG
import test_v1_plots as TPL
from test_v1_facade import run_cli

INSTRUMENT_GRADE = os.path.join(ROOT, "metaelemzes", "instruments", "grade.json")


def grade_answers():
    """{tétel: [válaszok]} a GRADE-eszközből — a teszt az eszköz MOSTANI szótárát használja (az A köteg a
    szk.ma.grade/v1 alakra igazíthatja; az álnév-elfogadás miatt mindkettő működik)."""
    with open(INSTRUMENT_GRADE, encoding="utf-8") as fh:
        inst = json.load(fh)
    return {it["key"]: it["answers"] for it in inst["items"]}


def grade_appraisal(start="high", downs=(0, 0, 0, 0), pb="undetected", up=("no", "no", "no"), status="complete",
                    resolution=None, unit="o1"):
    """szk.appraisal/v1 GRADE-értékelés; downs: a négy fő domén lépése (0 | -1 | -2)."""
    ans = grade_answers()
    by_step = {0: 0, -1: 1, -2: 2}
    answers = {"0.1": {"value": start}}
    for i, st in enumerate(downs, start=1):
        answers["%d.1" % i] = {"value": ans["%d.1" % i][by_step[st]]}
    pbv = {"undetected": 0, "suspected": 1, "strongly suspected": 2}[pb]
    answers["5.1"] = {"value": ans["5.1"][pbv]}
    if resolution is not None:
        answers["5.1"]["resolution"] = resolution
    for key, v in zip(("6.1", "7.1", "8.1"), up):
        answers[key] = {"value": ans[key][{"no": 0, "yes": 1, "very_large": 2}[v]]}
    return {"schema": "szk.appraisal/v1", "tool": "grade", "scope": None,
            "target": {"unit": unit, "study_id": None, "outcome": unit}, "assessor": "SzK", "second_assessor": None,
            "status": status, "origin": "human", "approved_by": None, "answers": answers, "domain_judgements": [],
            "overall": {"judgement": "low", "rationale": "x", "implied": None, "override_reason": "teszt"},
            "created": "2026-10-01T10:00:00Z", "updated": "2026-10-02T10:00:00Z"}


def grade_doc_from(start, downs, pb_step, upgrades):
    """A grade_appraisal-lel azonos ítéletek szk.ma.grade/v1 alakban."""
    names = ("risk_of_bias", "inconsistency", "indirectness", "imprecision")
    rating = {0: "not serious", -1: "serious", -2: "very serious"}
    doms = {n: {"rating": rating[st], "step": st, "rationale": "x"} for n, st in zip(names, downs)}
    doms["publication_bias"] = {"rating": "undetected" if pb_step == 0 else "strongly suspected", "step": pb_step,
                                "rationale": "x"}
    return {"schema": "szk.ma.grade/v1", "outcome_id": "o1", "run_id": None, "start": start, "domains": doms,
            "upgrades": dict(upgrades), "certainty": None}


# ================================================================== audit: X003, X012, X019 (M2, m1, M4)
class TestAuditAIDraftAndGradeAppraisal(TA._Base):
    def test_M2_x003_approved_ai_draft_is_not_a_second_rater(self):
        """contracts:M2 — emberi 'low' + jóváhagyott AI 'high', a tábla rob-ja 'high' → X003 hiba (eddig: kimaradt)."""
        pj = self.pj
        pj.ready()
        rows = TA.bcg_rows()
        rows[0]["rob"] = "high"
        pj.csv(rows)
        pj.engine(minutes=1)
        human = pj.appraisal("S01", judgement="low")
        ai = pj.appraisal("S01", judgement="high", origin="ai_draft", approved_by="user:SzK")
        rep = pj.audit(stage="FINAL")
        x = TA.codes(rep, "X003")
        self.assertEqual(len(x), 1, rep["not_checked"])
        self.assertIn("rob = 'high'", x[0]["detail"])
        self.assertIn("'low'", x[0]["detail"])
        self.assertIn(human, x[0]["artifacts"])
        self.assertNotIn(ai, x[0]["artifacts"], "az emberi ítélet mellett az AI-vázlat nem bizonyíték")
        self.assertFalse([n for n in TA.skipped(rep, "X003") if "S01" in n["reason"]])
        # a tábla az emberi ítéletet követi → nincs X003, és az AI-eltérés sem „konszenzus nélküli” kimaradás
        pj.csv(TA.bcg_rows())
        pj.engine(minutes=2)
        rep = pj.audit(stage="FINAL")
        self.assertEqual(TA.codes(rep, "X003"), [])
        self.assertFalse([n for n in TA.skipped(rep, "X003") if "S01" in n["reason"]])

    def test_M2_x003_ai_only_fallback_and_human_conflict(self):
        pj = self.pj
        pj.ready()
        # csak jóváhagyott AI-vázlat: ez a végső (appraisal._finals: ai_approved) → a 'low' tábla hibás
        pj.appraisal("S02", judgement="high", origin="ai_draft", approved_by="user:SzK")
        x = TA.codes(pj.audit(), "X003")
        self.assertEqual(len(x), 1)
        self.assertIn("Ferguson", x[0]["detail"])
        # a jóvá nem hagyott AI-vázlat semmit nem számít
        pj.appraisal("S02", judgement="high", origin="ai_draft", approved_by=None)
        self.assertEqual(TA.codes(pj.audit(), "X003"), [])
        # két eltérő EMBERI ítélet konszenzus nélkül → kimarad (okkal)
        pj.appraisal("S03", judgement="low", assessor="SzK")
        pj.appraisal("S03", judgement="high", assessor="KB")
        rep = pj.audit()
        self.assertTrue([n for n in TA.skipped(rep, "X003") if "emberi értékelések" in n["reason"]])

    def test_m1_x012_unapproved_ai_draft_is_not_the_self_assessment(self):
        """contracts:m1 — csak jóvá nem hagyott AI AMSTAR 2 (16/16, high) → S14/FINAL: X012 jelez."""
        pj = self.pj
        pj.ready(appraisal_tools=["rob2", "amstar2"])
        pj.appraise_all()
        rel = pj.appraisal("review", tool="amstar2", status="draft", outcome=None, origin="ai_draft",
                           answers={str(i): {"value": "yes"} for i in range(1, 17)},
                           overall={"judgement": "high", "rationale": "AI"})
        for stage in ("S14", "FINAL"):
            x = TA.codes(pj.audit(stage=stage), "X012")
            self.assertEqual(len(x), 1, stage)
            self.assertIn("AI-vázlat", x[0]["detail"])
            self.assertIn(rel, x[0]["artifacts"])
        rep = pj.audit(stage="S06")
        self.assertEqual(TA.codes(rep, "X012"), [])
        self.assertIn("AI-vázlat", TA.skipped(rep, "X012", None)[0]["reason"])
        # jóváhagyva és lezárva már az önellenőrzés (a besorolás egyezik: nincs találat)
        pj.appraisal("review", tool="amstar2", status="complete", outcome=None, origin="ai_draft",
                     approved_by="user:SzK", answers={str(i): {"value": "yes"} for i in range(1, 17)},
                     overall={"judgement": "high", "rationale": "AI, jóváhagyva"})
        self.assertEqual(TA.codes(pj.audit(stage="FINAL"), "X012"), [])

    def test_M4_x019_final_grade_appraisal_with_unresolved_suspected(self):
        """contracts:M4 (audit-oldal) — tool grade, status complete, 5.1 'suspected' feloldás nélkül → X019."""
        pj = self.pj
        pj.ready()
        rel = "04_torzitas_kockazat/appraisals/o1.grade.SzK.json"
        pj.write(rel, grade_appraisal(pb="suspected"))
        rep = pj.audit(stage="FINAL")
        x = TA.codes(rep, "X019")
        self.assertEqual(len(x), 1)
        self.assertEqual((x[0]["outcome"], x[0]["severity"], x[0]["artifacts"]), ("o1", "error", [rel]))
        self.assertIn("X019", [f["code"] for f in A.checkpoint_gate_errors(pj.root, "FINAL")])
        self.assertEqual(TA.codes(pj.audit(stage="S12"), "X019")[0]["severity"], "warning")
        # indoklással feloldva (0 lépés) → nincs találat
        pj.write(rel, grade_appraisal(pb="suspected", resolution={"step": 0, "rationale": "teljes keresés"}))
        self.assertEqual(TA.codes(pj.audit(stage="FINAL"), "X019"), [])
        # feloldás indoklás nélkül: továbbra is feloldatlan
        pj.write(rel, grade_appraisal(pb="suspected", resolution={"step": -1, "rationale": ""}))
        self.assertEqual(len(TA.codes(pj.audit(stage="FINAL"), "X019")), 1)
        # piszkozat: nem végleges, az X019 nem a lezárt értékelés miatt jelez
        pj.write(rel, grade_appraisal(pb="suspected", status="draft"))
        self.assertEqual(TA.codes(pj.audit(stage="FINAL"), "X019"), [])

    def test_M4_fallback_without_engine_check(self):
        """Az appraisal.check nélkül (hibás dokumentum) is a válaszokból dönt."""
        bad = grade_appraisal(pb="suspected")
        self.assertTrue(A._grade_appraisal_unresolved(bad))
        bad["tool"] = "nincs-ilyen-eszkoz"          # a motor-ellenőrzés kivételt dob → tárolt válaszok
        self.assertTrue(A._grade_appraisal_unresolved(bad))
        ok = grade_appraisal(pb="suspected", resolution={"step": -1, "rationale": "kevés kis vizsgálat"})
        ok["tool"] = "nincs-ilyen-eszkoz"
        self.assertFalse(A._grade_appraisal_unresolved(ok))


# ================================================================== X008: SoF-bizonyosság vs. rögzített GRADE (M6)
class TestX008Certainty(TA._Base):
    def grade(self, d, certainty="high", status="recorded", pb=None, run_id="auto", origin="human"):
        doc = {"schema": "szk.ma.grade/v1", "outcome_id": "o1", "run_id": d["run_id"] if run_id == "auto" else run_id,
               "start": "high", "domains": {k: {"rating": "not serious", "step": 0, "rationale": "ok"} for k in
                                            ("risk_of_bias", "inconsistency", "indirectness", "imprecision")},
               "upgrades": {"large_effect": False, "dose_response": False, "opposing_confounding": False},
               "certainty": certainty, "certainty_source": "human", "status": status, "origin": origin}
        doc["domains"]["publication_bias"] = pb or {"rating": "undetected", "step": 0, "status": "resolved"}
        return self.pj.write("06_kezirat/grade/o1.grade.json", doc)

    def sof(self, d, certainty):
        n = TA.run_numbers(d)
        est, lo, hi = n["bt"]
        row = {"outcome_id": "o1", "k": 13, "participants": n["participants"], "studies_text": {"hu": "x", "en": "x"},
               "relative": {"measure": "RR", "estimate": est, "ci_lower": lo, "ci_upper": hi, "level": 0.95,
                            "display_text": dict(n["display_text"]), "text": {"hu": "RR", "en": "RR"}},
               "effect": None, "absolute": [], "certainty": certainty, "footnotes": [], "sources": {}}
        self.pj.write("06_kezirat/sof/o1.sof.json", {"schema": "szk.ma.sof/v1", "outcome_id": "o1",
                                                     "run_id": d["run_id"], "measure": "RR", "rows": [row]})

    def x008(self):
        return TA.codes(self.pj.audit(stage="FINAL"), "X008")

    def test_M6_sof_certainty_must_equal_recorded_grade(self):
        pj = self.pj
        d = pj.ready()
        self.sof(d, "high")
        x = self.x008()
        self.assertIn("nincs rögzített GRADE", x[0]["detail"])          # GRADE nélkül
        self.grade(d, "high")
        self.assertEqual(self.x008(), [])
        self.sof(d, "moderate")
        x = self.x008()
        self.assertEqual(len(x), 1)
        self.assertIn("≠ a rögzített GRADE-é (high", x[0]["detail"])
        self.assertIn("06_kezirat/grade/o1.grade.json", x[0]["artifacts"])
        self.sof(d, "very_low")                                            # álnév is összevetődik
        self.assertIn("very low", self.x008()[0]["detail"])
        self.sof(d, None)                                                  # bizonyosság nélkül rendben
        self.assertEqual(self.x008(), [])

    def test_M6_draft_unresolved_ai_and_other_run(self):
        pj = self.pj
        d = pj.ready()
        self.sof(d, "high")
        self.grade(d, "high", status="draft")
        self.assertIn("még nincs rögzítve", self.x008()[0]["detail"])
        self.grade(d, None, status="draft", pb={"rating": "suspected", "step": None, "status": "unresolved"})
        self.assertIn("feloldatlan", self.x008()[0]["detail"])
        self.grade(d, "high", origin="ai_draft")
        self.assertIn("AI-vázlat", self.x008()[0]["detail"])
        self.grade(d, "high", run_id="20260101T000000Z-abcdef")
        self.assertIn("futásra vonatkozik", self.x008()[0]["detail"])

    def test_M6_journal_row_is_a_recorded_grade(self):
        pj = self.pj
        d = pj.ready()
        self.sof(d, "moderate")
        P.add_grade(pj.root, "o1", "moderate", check_kb=False)
        self.assertEqual(self.x008(), [])
        P.add_grade(pj.root, "o1", "low", check_kb=False)                  # a legutóbbi sor számít
        self.assertIn("(low", self.x008()[0]["detail"])


# ================================================================== GRADE-tár és SoF a valódi motorral
class TestGradeFlow(TG._Project):
    # ---- methodology:M5
    def test_M5_one_rule_on_both_engine_paths(self):
        for start in ("high", "low"):
            for downs in ((0, 0, 0, 0), (0, 0, 0, -1), (-1, -1, 0, 0), (-2, 0, 0, -1)):
                for pb_step in (0, -1):
                    for up in (("no", "no", "no"), ("very_large", "no", "no"), ("yes", "yes", "no")):
                        with self.subTest(start=start, downs=downs, pb=pb_step, up=up):
                            app = grade_appraisal(start, downs, "undetected" if pb_step == 0 else "strongly suspected",
                                                  up)
                            a_cert = AP.check(app)["grade"]["certainty"]
                            ups = {"large_effect": {"no": 0, "yes": 1, "very_large": 2}[up[0]],
                                   "dose_response": 1 if up[1] == "yes" else 0,
                                   "opposing_confounding": 1 if up[2] == "yes" else 0}
                            doc, errs = P.validate_grade_doc(grade_doc_from(start, downs, pb_step, ups))
                            self.assertEqual(errs, [])
                            self.assertEqual(P.grade_token(a_cert), P.grade_doc_certainty(doc))
        r = P.grade_arithmetic("low", [0, 0, 0, -1, 0], 2)
        self.assertEqual((r["certainty"], r["upgrades_applied"], r["downgrade_total"]), ("very low", False, 1))
        r = P.grade_arithmetic("low", [0, 0, 0, 0, 0], 2)
        self.assertEqual((r["certainty"], r["upgrades_applied"]), ("high", True))
        self.assertIsNone(P.grade_arithmetic("high", [None, 0, 0, 0, 0])["certainty"])
        self.assertIsNone(P.grade_arithmetic(None, [0] * 5)["certainty"])
        # a napló összhang-ellenőrzése ugyanazt a szabályt követi
        signed = dict(risk_of_bias="0", inconsistency="0", indirectness="0", imprecision="−1 súlyos",
                      publication_bias="0")
        self.assertIsNone(P.grade_consistency("very low", upgrades="+2", **signed))      # alacsony − 1 (a +2 nem)
        self.assertIsNone(P.grade_consistency("moderate", upgrades="+2", **signed))      # magas − 1
        msg = P.grade_consistency("high", upgrades="+2", **signed)        # régen elfogadva (magas − 1 + 2)
        self.assertIn("D-S13-009", msg)

    def test_M5_reproduction_grec_low_start_upgrade_with_downgrade(self):
        """A verifier grec.py esete: alacsony kiindulás, pontatlanság −1, nagy hatás +2 → a motor 'very low'-t
        számol (nem 'moderate'-et), és ember nélkül nem rögzít."""
        d = self.decided({"rating": "undetected"})
        d["start"] = "low"
        d["domains"]["inconsistency"].update(rating="not serious", rationale="I² kicsi")
        d["domains"]["imprecision"].update(rating="serious", rationale="a CI átlépi a MID-et")
        d["upgrades"] = {"large_effect": 2, "dose_response": False, "opposing_confounding": False}
        d["upgrade_details"] = {"large_effect": {"step": 2, "rationale": "RR < 0.2"}}
        s = api.grade_put(self.root, "o1", d)
        self.assertEqual((s["certainty"], s["certainty_source"]), ("very low", "computed"))
        self.assertIn("upgrade_with_downgrade", [w["code"] for w in s["override_warnings"]])
        with self.assertRaises(P.GradeRecordError) as cm:
            api.grade_record(self.root, "o1", actor="user:SzK")
        self.assertTrue(cm.exception.needs_certainty)
        self.assertEqual(cm.exception.computed_certainty, "very low")
        self.assertIn("GRADE-09", str(cm.exception))
        self.assertEqual(api.project_list(self.root, "grades"), [], "semmi nem került a naplóba")
        # az ember a felminősítést mégis alkalmazza: 'moderate', indokolva → rögzíthető, figyelmeztetéssel
        s["certainty"], s["certainty_source"] = "moderate", "human"
        rec = api.grade_record(self.root, "o1", doc=s, actor="user:SzK")
        self.assertEqual((rec["doc"]["certainty"], rec["doc"]["certainty_source"]), ("moderate", "human"))
        self.assertTrue(any("'very low'" in w for w in rec["warnings"]))
        self.assertEqual(api.project_show(self.root, "grade", rec["id"])["certainty"], "moderate")

    def test_M5_record_needs_human_confirmation(self):
        s = P.save_grade_doc(self.root, self.decided({"rating": "undetected"}))
        self.assertEqual((s["certainty"], s["certainty_source"]), ("moderate", "computed"))
        with self.assertRaises(P.GradeRecordError) as cm:
            P.record_grade_doc(self.root, s, actor="user:SzK")
        self.assertIsInstance(cm.exception, ValueError)
        rec = P.record_grade_doc(self.root, s, actor="user:SzK", certainty="moderate")
        self.assertEqual(rec["doc"]["certainty_source"], "human")
        self.assertEqual(TG.schema_errors(rec["doc"], "ma.grade"), [])
        with self.assertRaises(ValueError):
            P.record_grade_doc(self.root, s, certainty="közepes")
        # a jóvá nem hagyott AI-vázlat szintje nem emberi ítélet
        ai = self.decided({"rating": "undetected"})
        ai.update(origin="ai_draft", certainty="high")
        s = P.save_grade_doc(self.root, ai)
        self.assertEqual((s["certainty"], s["certainty_source"]), ("moderate", "computed"))

    # ---- contracts:M3
    def test_M3_sof_ignores_unapproved_ai_draft_certainty(self):
        d = self.decided({"rating": "undetected"})
        d.update(origin="ai_draft", certainty="moderate")
        saved = api.grade_put(self.root, "o1", d)
        self.assertEqual(saved["origin"], "ai_draft")
        so = api.sof(self.run_dir, project_dir=self.root)
        row = so["rows"][0]
        self.assertIsNone(row["certainty"])
        self.assertEqual(row["certainty_basis"], "ai_draft")
        self.assertIn("AI-vázlat", row["certainty_cell"]["hu"])
        self.assertTrue(any("AI-vázlat" in w["hu"] for w in row["warnings"]))
        self.assertIn("AI-vázlat", api.sof_markdown(so, "hu"))
        self.assertIsNone(row["statement"])
        self.assertEqual(TG.schema_errors(so, "ma.sof"), [])
        with self.assertRaises(ValueError):                    # paraméterrel sem kerülhető meg
            api.sof(self.run_dir, project_dir=self.root, certainty="moderate")
        rc, _out, err = run_cli("grade", "sof", "--run", self.run_dir, "--project", self.root, "--certainty",
                                "moderate", "--save")
        self.assertEqual(rc, 1, err)
        self.assertFalse(os.path.isfile(G.sof_path(self.root, "o1")))
        forged = copy.deepcopy(so)
        forged["rows"][0]["certainty"] = "moderate"
        with self.assertRaises(ValueError) as cm:
            G.save_sof(self.root, forged)
        self.assertIn("AI-vázlat", str(cm.exception))
        G.save_sof(self.root, so)                              # bizonyosság nélkül menthető
        # jóváhagyás + emberi rögzítés után már a SoF-ba kerül
        saved["approved_by"] = "user:SzK"
        P.record_grade_doc(self.root, saved, actor="user:SzK", certainty="moderate")
        row = api.sof(self.run_dir, project_dir=self.root)["rows"][0]
        self.assertEqual((row["certainty"], row["certainty_basis"]), ("moderate", "recorded"))

    # ---- methodology:M6
    def test_M6_explicit_certainty_cannot_bypass_unresolved_or_recorded_grade(self):
        api.grade_put(self.root, "o1", self.decided())                        # 'suspected' feloldatlan
        with self.assertRaises(ValueError) as cm:
            api.sof(self.run_dir, project_dir=self.root, certainty="high")
        self.assertIn("4. döntés", str(cm.exception))
        rc, _out, err = run_cli("grade", "sof", "--run", self.run_dir, "--project", self.root, "--certainty",
                                "high", "--save")
        self.assertEqual(rc, 1)
        so = api.sof(self.run_dir, project_dir=self.root)
        self.assertEqual((so["rows"][0]["certainty"], so["rows"][0]["certainty_basis"]), (None, "unresolved"))
        self.assertIn("X019", so["rows"][0]["certainty_cell"]["hu"])
        forged = copy.deepcopy(so)
        forged["rows"][0]["certainty"] = "high"
        with self.assertRaises(ValueError):
            api.sof_save(self.root, forged)
        # feloldva és 'low'-ként rögzítve: a SoF csak 'low' lehet
        d = self.decided({"rating": "suspected", "step": -1, "rationale": "kis vizsgálatok hatása"})
        rec = P.record_grade_doc(self.root, d, actor="user:SzK", certainty="low")
        self.assertEqual(rec["doc"]["certainty"], "low")
        with self.assertRaises(ValueError):
            api.sof(self.run_dir, project_dir=self.root, certainty="high")
        so = api.sof(self.run_dir, project_dir=self.root, certainty="low")
        self.assertEqual((so["rows"][0]["certainty"], so["rows"][0]["certainty_basis"]), ("low", "recorded"))
        api.sof_save(self.root, so)
        forged = copy.deepcopy(so)
        forged["rows"][0]["certainty"] = "high"
        with self.assertRaises(ValueError) as cm:
            api.sof_save(self.root, forged)
        self.assertIn("≠ a rögzített GRADE-é (low)", str(cm.exception))
        self.assertEqual(api.sof_load(self.root, "o1")["rows"][0]["certainty"], "low")

    def test_M6_draft_grade_is_provisional_and_not_saved(self):
        d = self.decided({"rating": "undetected"})
        d["certainty"] = "moderate"
        api.grade_put(self.root, "o1", d)
        so = api.sof(self.run_dir, project_dir=self.root)
        row = so["rows"][0]
        self.assertEqual((row["certainty"], row["certainty_basis"]), ("moderate", "draft"))
        self.assertIn("piszkozat", row["certainty_cell"]["hu"])
        with self.assertRaises(ValueError) as cm:
            G.save_sof(self.root, so)
        self.assertIn("nincs rögzített GRADE", str(cm.exception))
        # GRADE nélkül, paraméterrel: előnézet igen (jelölve), mentés nem
        other = tempfile.mkdtemp(prefix="szk_sof_")
        self.addCleanup(shutil.rmtree, other, True)
        P.init(other, "üres")
        so = G.sof(self.run_dir, certainty="high", project_dir=other, outcome_id="o1")
        self.assertEqual(so["rows"][0]["certainty_basis"], "parameter")
        with self.assertRaises(ValueError):
            G.save_sof(other, so)
        P.add_grade(other, "o1", "high", check_kb=False)                     # a régi napló-út is rögzítés
        G.save_sof(other, so)

    # ---- contracts:m2
    def test_m2_one_grade_vocabulary_aliases_on_input(self):
        doc, errs = P.validate_grade_doc({
            "outcome_id": "o9", "certainty": "very_low", "importance": "not_important",
            "domains": {"inconsistency": {"rating": "not_serious"}, "imprecision": {"rating": "very_serious"},
                        "publication_bias": {"rating": "strongly_suspected"}}})
        self.assertEqual(errs, [])
        self.assertEqual((doc["certainty"], doc["importance"]), ("very low", "not important"))
        self.assertEqual(doc["domains"]["inconsistency"]["rating"], "not serious")
        self.assertEqual(doc["domains"]["imprecision"], {"rating": "very serious", "step": -2})
        self.assertEqual(doc["domains"]["publication_bias"]["rating"], "strongly suspected")
        self.assertTrue(P.validate_grade_doc({"outcome_id": "o9", "certainty": "kozepes"})[1])
        # az értékelés-oldal (appraisal_check) szintje közvetlenül a SoF-ba és a GRADE-tárba mehet
        app = grade_appraisal("low", (0, 0, 0, -1))
        cert = api.appraisal_check(app)["grade"]["certainty"]
        so = api.sof(self.run_dir, certainty=cert, outcome_id="o1")
        self.assertEqual(so["rows"][0]["certainty"], "very low")
        self.assertEqual(api.sof(self.run_dir, certainty="very_low", outcome_id="o1")["rows"][0]["certainty_symbols"],
                         "⊕◯◯◯")
        d = self.decided({"rating": "strongly_suspected"})
        d["domains"]["inconsistency"]["rating"] = "not_serious"
        d["domains"]["imprecision"]["rating"] = "very_serious"
        s = api.grade_put(self.root, "o1", d)
        self.assertEqual(s["domains"]["publication_bias"]["rating"], "strongly suspected")
        self.assertEqual(s["domains"]["imprecision"]["step"], -2)
        self.assertEqual(TG.schema_errors(s, "ma.grade"), [])
        P.add_grade(self.root, "o1", "very_low", check_kb=False)
        self.assertEqual(api.project_list(self.root, "grades")[-1]["certainty"], "very low")
        self.assertIsNone(P.grade_consistency("very_low", risk_of_bias="-2", imprecision="-1"))
        self.assertIsNotNone(G.statement("very_low", "reduce"))

    # ---- methodology:M9
    def test_M9_design_is_not_inferred_from_start(self):
        with open(os.path.join(TG.EXAMPLES, "ma.grade.v1.rogzitett.json"), encoding="utf-8") as fh:
            grade = json.load(fh)
        grade.pop("design")
        grade.update(run_id=None, start="high", start_reason="NRSI assessed with ROBINS-I (GRADE 18)")
        row = G.sof(self.run_dir, grade=grade, outcome_id="o1")["rows"][0]
        self.assertIsNone(row["design"])
        self.assertEqual(row["studies_text"]["en"], "357347 (13 studies)")
        grade["design"] = "NRSI"
        row = G.sof(self.run_dir, grade=grade, outcome_id="o1")["rows"][0]
        self.assertEqual(row["design"], "NRSI")
        self.assertEqual(row["studies_text"]["en"], "357347 (13 non-randomised studies)")
        self.assertEqual(TG.schema_errors(G.sof(self.run_dir, grade=grade, outcome_id="o1"), "ma.sof"), [])
        # az elrendezés a studies.json-ból (mind RCT → RCT; egy kohorsz → vegyes)
        header, rows = TG.bcg_rows()
        st = [{"study_id": "s%02d" % i, "label": r[0], "design": "rct_parallel"} for i, r in enumerate(rows)]
        path = os.path.join(self.root, "03_adatok", "studies.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": "szk.ma.studies/v1", "studies": st}, fh, ensure_ascii=False)
        grade.pop("design")
        row = G.sof(self.run_dir, grade=grade, project_dir=self.root, outcome_id="o1")["rows"][0]
        self.assertEqual(row["design"], "RCT")
        st[0]["design"] = "cohort"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": "szk.ma.studies/v1", "studies": st}, fh, ensure_ascii=False)
        row = G.sof(self.run_dir, grade=grade, project_dir=self.root, outcome_id="o1")["rows"][0]
        self.assertEqual(row["design"], "mixed")

    def test_M9_upgrade_advice_follows_design(self):
        rd = G.RunData({"options": {"measure": "RR"}, "back_transformed": {"estimate_ci": [0.3, 0.2, 0.45]}})
        up = G._advise_upgrades(rd, "high", design="NRSI")["large_effect"]
        self.assertEqual((up["suggested"], up["candidate_step"], up["design"]), (True, 1, "NRSI"))
        self.assertNotIn("RCT", up["why"]["because"]["en"])
        up = G._advise_upgrades(rd, "low", design="RCT")["large_effect"]
        self.assertFalse(up["suggested"])
        self.assertIn("Randomised trials", up["why"]["uncertain"]["en"])
        up = G._advise_upgrades(rd, "low")["large_effect"]                    # ismeretlen: jelölt feltételezés
        self.assertTrue(up["suggested"])
        self.assertIn("assumed", up["why"]["uncertain"]["en"])
        doc = G.advice(self.run_dir, project_dir=self.root, start="high", rob_tool="robins-i")
        self.assertEqual(doc["design"], "NRSI")
        self.assertNotIn("RCT start", json.dumps(doc["advice"]["upgrades"]["large_effect"]["why"]))

    # ---- methodology:M8
    def test_M8_grade18_robins_i(self):
        serious = {lab: "Serious" for lab in TG.bcg_rob()}
        hi = G.advice(self.run_dir, rob_by_row=serious, outcome_id="o1", start="high", rob_tool="robins-i")
        sug = hi["domains"]["risk_of_bias"]["suggestion"]
        self.assertEqual((sug["rating"], sug["step"], sug["status"]), ("very serious", -2, "suggested"))
        codes = [f["code"] for f in sug["flags"]]
        self.assertIn("grade18_two_levels", codes)
        self.assertNotIn("very_serious_possible", codes)
        self.assertIn("GRADE 18", sug["why"]["because"]["en"])
        self.assertIn("D-S13-001", sug["kb_refs"])
        lo = G.advice(self.run_dir, rob_by_row=serious, outcome_id="o1", start="low", rob_tool="robins-i")
        sug = lo["domains"]["risk_of_bias"]["suggestion"]
        self.assertEqual(sug["status"], "borderline")
        self.assertIn("grade18_double_count", [f["code"] for f in sug["flags"]])
        self.assertIn("double count", [f for f in sug["flags"] if f["code"] == "grade18_double_count"][0]["text"]["en"])
        # eszköz megadása nélkül a rob-oszlop ROBINS-I-szókincséből (a verifier g3.py esete)
        voc = G.advice(self.run_dir, rob_by_row=serious, outcome_id="o1", start="high")
        sug = voc["domains"]["risk_of_bias"]["suggestion"]
        self.assertEqual(sug["step"], -2)
        self.assertIn("rob_tool_from_vocabulary", [f["code"] for f in sug["flags"]])
        self.assertEqual(voc["domains"]["risk_of_bias"]["advisory"]["rob_tool_source"], "vocabulary")
        self.assertEqual(voc["design"], "NRSI")
        # RoB 2-vel (RCT) változatlan: −1 és a „very_serious_possible” jelzés
        rct = G.advice(self.run_dir, rob_by_row=serious, outcome_id="o1", start="high", rob_tool="rob2")
        sug = rct["domains"]["risk_of_bias"]["suggestion"]
        self.assertEqual((sug["rating"], sug["step"]), ("serious", -1))
        self.assertIn("very_serious_possible", [f["code"] for f in sug["flags"]])
        # az eszköz a projektből (appraisal_tools)
        meta = P.load_project_meta(self.root)
        meta["appraisal_tools"] = ["robins-i", "grade"]
        P.save_project_meta(self.root, meta)
        auto = G.advice(self.run_dir, rob_by_row=serious, project_dir=self.root, start="high")
        self.assertEqual(auto["domains"]["risk_of_bias"]["advisory"]["rob_tool"], "robins-i")
        self.assertEqual(auto["domains"]["risk_of_bias"]["suggestion"]["step"], -2)
        self.assertEqual(TG.schema_errors(auto, "ma.grade"), [])
        from metaelemzes import kb
        refs = set(auto["domains"]["risk_of_bias"]["suggestion"]["kb_refs"])
        try:
            have = set(kb.existing_ids(sorted(refs)))
        except Exception as exc:                  # noqa: BLE001 — tudásbázis nélkül nem ellenőrizhető
            self.skipTest("a tudásbázis nem érhető el: %s" % exc)
        self.assertEqual(sorted(refs - have), [])


# ================================================================== ma.plot/v2: E4c-megkötések (contracts:M6)
class TestPlotContractE4c(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out, es = TPL.analyze(TPL.BCG, measure="RR", cumulative="év", moderators=["szélesség"])
        from metaelemzes import pipeline
        cls.doc = json.loads(json.dumps(pipeline.to_jsonable(pipeline.plot_document(out, es)), allow_nan=False))
        cls.reg = K.registry()
        cls.schema = cls.reg[K.urn("ma.plot", 2)]
        from test_mvp_contracts import MiniValidator
        cls.v = MiniValidator(cls.reg)

    def errors(self, doc):
        return self.v.errors(doc, self.schema)

    def test_engine_document_conforms_and_string_moderator_still_valid(self):
        self.assertEqual(self.errors(self.doc), [])
        d = copy.deepcopy(self.doc)
        d["bubble"]["moderator"] = "szélesség"
        self.assertEqual(self.errors(d), [])

    def test_restored_constraints_reject_malformed_blocks(self):
        cases = []
        d = copy.deepcopy(self.doc)
        del d["bubble"]["points"][0]["x"], d["bubble"]["points"][0]["y"]
        cases.append(("point without x/y", d))
        d = copy.deepcopy(self.doc)
        d["bubble"]["band"][0] = ["a", "b", "c"]
        cases.append(("non-numeric band", d))
        d = copy.deepcopy(self.doc)
        d["bubble"]["line"][0] = [None, "x"]
        cases.append(("non-numeric line", d))
        d = copy.deepcopy(self.doc)
        d["bubble"]["pi_band"][0] = [1.0, "x", 2.0]
        cases.append(("non-numeric pi_band", d))
        d = copy.deepcopy(self.doc)
        d["bubble"]["vcov"][0][0] = "x"
        cases.append(("non-numeric vcov", d))
        d = copy.deepcopy(self.doc)
        d["cumulative"]["entries"][0].update(k=0)
        cases.append(("cumulative k = 0", d))
        d = copy.deepcopy(self.doc)
        d["cumulative"]["entries"][0].update(estimate="abc")
        cases.append(("cumulative estimate text", d))
        d = copy.deepcopy(self.doc)
        d["cumulative"]["order"]["n_missing"] = -3
        cases.append(("negative n_missing", d))
        d = copy.deepcopy(self.doc)
        d["bubble"]["moderator"] = {"name": "x"}
        cases.append(("moderator object without type", d))
        for name, bad in cases:
            with self.subTest(case=name):
                self.assertTrue(self.errors(bad), name)

    def test_engine_fields_are_documented_again(self):
        bub = self.schema["properties"]["bubble"]["oneOf"][1]["properties"]
        for key in ("coefficients", "grid_n", "x_range", "line_label", "groups", "model", "vcov", "pi_band"):
            self.assertIn(key, bub)
        for key in ("k", "p", "R2", "QM", "QM_p", "crit", "tau2"):
            self.assertIn(key, bub["model"]["properties"])
        pts = bub["points"]["items"]
        self.assertEqual(pts["required"], ["row_uid", "x", "y", "weight_pct"])
        for key in ("se", "display", "flags", "label", "row_index"):
            self.assertIn(key, pts["properties"])
            self.assertIn(key, self.doc["bubble"]["points"][0], "a motor írja: %s" % key)
        for key in ("coefficients", "grid_n", "x_range", "line_label"):
            self.assertIn(key, self.doc["bubble"], "a motor írja: %s" % key)
        ent = self.schema["properties"]["cumulative"]["oneOf"][1]["properties"]["entries"]["items"]["properties"]
        for key in ("estimate", "ci_lower", "ci_upper", "display", "analysis"):
            self.assertIn(key, ent)
        self.assertEqual(ent["k"]["minimum"], 1)


if __name__ == "__main__":
    unittest.main()
