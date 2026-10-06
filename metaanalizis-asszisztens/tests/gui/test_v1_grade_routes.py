# -*- coding: utf-8 -*-
"""v1 GRADE / SoF / AMSTAR 2 / Protokoll végpontok (ma_gui/routes/grade*.py; terv 3.4, 3.5.12–3.5.13, 4.14, 6.5,
7.6, 11/4–6. döntés) a VALÓDI munkapad-szerverrel (port 0, a szerver a saját válaszait is sémán ellenőrzi).

- Funkció-felismerés: a motor v1-függvényei nélkül 424 CAPABILITY_MISSING magyar üzenettel (és a GRADE / AMSTAR 2
  nézete a részletekben); a többi végpont működik.
- A motor-függvények helyén TESZT-CSONK (tests/gui/_grade_engine_stub.py): GRADE-mentés (indoklás kötelező,
  publikációs torzítás „suspected” = feloldatlan → a rögzítés 409 X019), If-Match, PHI-őr, motor-ValueError → 422,
  projektnapló-sor előjeles lépés-szövegekkel (api.project_grade), activity szabad szöveg nélkül; elavult futás
  (X001) és régi futás (X007) a rögzítés előtt; tanácsadó (mid nyers szöveg); SoF előnézet / mentés / export
  (Excel-képletinjekció-őr, BOM, CRLF, elválasztó nyelv szerint, aláírt letöltés, Markdown-escape); AMSTAR 2
  (mindkét konvenció, X017 felülbírálás indoklással, AI-vázlat jóváhagyás nélkül nem kész); Protokoll (PICO,
  típus, regisztráció If-Match-csel, PHI, előre rögzített specek listája)."""
import contextlib
import csv
import io
import json
import os
import shutil
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_routes_harness as H  # noqa: E402
import _grade_engine_stub as STUB  # noqa: E402
from metaelemzes import api  # noqa: E402
from ma_gui import activity  # noqa: E402
from ma_gui.routes import grade as grade_route  # noqa: E402
from ma_gui.routes import grade_sof  # noqa: E402

ALL_ENGINE = ("grade_get", "grade_load", "load_grade_doc", "grade_doc_load", "grade_put", "grade_save", "save_grade_doc",
              "grade_doc_save", "grade_record", "record_grade_doc", "grade_doc_record", "grade_advice", "sof",
              "sof_build", "sof_csv", "sof_markdown", "sof_problems", "amstar2_consistency", "instrument_get",
              "get_instrument", "instrument", "load_instrument")
SECRET = "Smith-féle titkos indoklás"


def rationale(text="indoklás"):
    return "%s — %s" % (text, SECRET)


def grade_doc(run_id, pb=("suspected", None), rob="serious", **extra):
    doc = {"schema": "szk.ma.grade/v1", "run_id": run_id, "start": "high", "start_reason": "RCT",
           "domains": {
               "risk_of_bias": {"rating": rob, "step": {"not serious": 0, "serious": -1, "very serious": -2}[rob],
                                "rationale": rationale("RoB")},
               "inconsistency": {"rating": "not serious", "step": 0, "rationale": rationale("I2")},
               "indirectness": {"rating": "not serious", "step": 0, "rationale": rationale("ind")},
               "imprecision": {"rating": "not serious", "step": 0, "rationale": rationale("imp")},
               "publication_bias": {"rating": pb[0], "step": pb[1], "rationale": rationale("pb")}},
           "upgrades": {"large_effect": False, "dose_response": False, "opposing_confounding": False}}
    doc.update(extra)
    return doc


class _Base(unittest.TestCase):
    NAME = "gr"

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_v1_grade_")
        cls.proj, cls.home = H.make_project(cls.tmp)
        cls.srv = H.Srv(cls.proj, cls.home, cls.tmp, name=cls.NAME)
        sp = H.spec("o1_primary", outcome="o1")
        cls.srv.ok("PUT", "/api/specs/o1_primary", sp)
        job = cls.srv.job(cls.srv.ok("POST", "/api/analyze", {"mode": "commit", "spec": sp, "client_seq": 1})["data"])
        assert job["status"] == "done", job
        cls.run_id = job["result"]["run"]["run_id"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.stack = contextlib.ExitStack()
        STUB.CALLS[:] = []

    def tearDown(self):
        self.stack.close()

    def engine(self, only=None, absent=()):
        for p in STUB.absent([n for n in ALL_ENGINE if n not in STUB.FUNCS or (only is not None and n not in only)]
                             + list(absent)):
            self.stack.enter_context(p)
        for p in STUB.patch(only):
            self.stack.enter_context(p)

    def no_engine(self):
        for p in STUB.absent(ALL_ENGINE):
            self.stack.enter_context(p)

    def ok(self, method, path, body=None, etag=None):
        headers = [("If-Match", etag)] if etag else []
        return self.srv.ok(method, path, body, headers=headers)

    def err(self, method, path, body=None, etag=None):
        headers = [("If-Match", etag)] if etag else []
        return self.srv.err(method, path, body, headers=headers)

    def activity_text(self):
        with open(os.path.join(self.proj, activity.LOG_RELPATH), encoding="utf-8") as fh:
            return fh.read()

    def records(self):
        return activity.read_records(os.path.join(self.proj, activity.LOG_RELPATH))

    def grades(self):
        return [g for g in api.project_list(self.proj, "grades") if g["outcome"] == "o1"]


class FeatureDetectionTests(_Base):
    NAME = "fd"

    def test_missing_engine_gives_424_with_hungarian_message_and_view(self):
        self.no_engine()
        st, e = self.err("GET", "/api/grade/o1")
        self.assertEqual((st, e["code"]), (424, "CAPABILITY_MISSING"))
        self.assertIn("motor", e["message"])
        self.assertIn("metaelemzes.api.grade_get", e["message"])
        view = e["details"]["view"]
        self.assertEqual(view["outcome"]["id"], "o1")
        self.assertEqual(view["run"]["run_id"], self.run_id)
        self.assertIsNone(view["grade"])
        self.assertEqual(view["vocab"]["domains"], list(grade_route.DOMAINS))
        self.assertFalse(view["engine"]["grade_get"])
        for method, path, body in (("PUT", "/api/grade/o1", {"grade": grade_doc(self.run_id)}),
                                   ("GET", "/api/grade/o1/advice", None), ("GET", "/api/sof/o1", None),
                                   ("PUT", "/api/sof/o1", {"assumed_risks": [{"source": "control_pool"}]}),
                                   ("GET", "/api/appraisals/review/amstar2?rater=SzK", None)):
            st, e = self.err(method, path, body)
            self.assertEqual((st, e["code"]), (424, "CAPABILITY_MISSING"), (method, path))
            self.assertIn("Frissítsd a motort", e["message"])
        # az AMSTAR 2-nek EGY végpontja van (az értékelés-végpontok, review.amstar2.<értékelő>.json); a régi
        # párhuzamos /api/amstar2 megszűnt (v1 integráció)
        self.assertEqual(self.err("GET", "/api/amstar2")[0], 404)
        # a protokoll a motor v1 nélkül is megy (a meglévő homlokzattal)
        self.assertEqual(self.ok("GET", "/api/protocol")["data"]["review_types"], list(api.REVIEW_TYPES))

    def test_signature_mismatch_is_424(self):
        def odd(foo, bar):                                  # ismeretlen kötelező paraméterek
            return {}
        from unittest import mock
        self.engine()
        self.stack.enter_context(mock.patch.object(api, "grade_advice", odd, create=True))
        st, e = self.err("GET", "/api/grade/o1/advice")
        self.assertEqual((st, e["code"]), (424, "CAPABILITY_MISSING"))
        self.assertIn("foo", e["message"])

    def test_unknown_or_bad_outcome(self):
        self.engine()
        self.assertEqual(self.err("GET", "/api/grade/nincs")[0], 404)
        self.assertEqual(self.err("GET", "/api/grade/%2E%2E")[0], 400)
        self.assertEqual(self.err("GET", "/api/sof/o9")[0], 404)
        # o2-nek nincs commit-futása: a tanács és a SoF 404 érthető útmutatással
        st, e = self.err("GET", "/api/grade/o2/advice")
        self.assertEqual(st, 404)
        self.assertIn("commit", e["message"])
        d = self.ok("GET", "/api/grade/o2")["data"]
        self.assertIsNone(d["run"])


class GradeTests(_Base):
    NAME = "gd"

    def test_a_flow_unresolved_then_resolved_and_recorded(self):
        self.engine()
        env = self.ok("GET", "/api/grade/o1")
        d = env["data"]
        self.assertIsNone(d["grade"])
        self.assertIsNone(env["_etag"])
        self.assertEqual(d["run"]["run_id"], self.run_id)
        self.assertEqual(d["missing"], list(grade_route.DOMAINS))
        self.assertEqual(d["conventions"]["grade_suspected"], "unresolved")
        pb = [r for r in d["vocab"]["ratings"]["publication_bias"] if r["rating"] == "suspected"][0]
        self.assertEqual(pb["steps"], [None, 0, -1])
        n_before = len(self.grades())
        # mentés feloldatlan publikációs torzítással: engedett (piszkozat), a státusz 'unresolved'
        env = self.ok("PUT", "/api/grade/o1", {"grade": grade_doc(self.run_id)})
        d = env["data"]
        self.assertEqual(d["grade"]["domains"]["publication_bias"]["status"], "unresolved")
        self.assertEqual(d["unresolved"], ["publication_bias"])
        self.assertIsNone(d["grade"]["certainty"], "feloldatlan doménnél nincs bizonyosság")
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "06_kezirat", "grade", "o1.grade.json")))
        etag = env["_etag"]
        self.assertTrue(etag)
        self.assertEqual(self.ok("GET", "/api/grade/o1")["_etag"], etag)
        # rögzítés feloldatlanul: 409 GATE_BLOCKED X019, nincs naplósor
        st, e = self.err("PUT", "/api/grade/o1", {"grade": grade_doc(self.run_id), "record": True}, etag=etag)
        self.assertEqual((st, e["code"]), (409, "GATE_BLOCKED"))
        self.assertEqual(e["details"]["code"], "X019")
        self.assertIn("0 vagy −1", e["message"])
        self.assertEqual(len(self.grades()), n_before)
        # If-Match nélkül a meglévő ítélet nem írható felül; régi ETag-gel sem
        self.assertEqual(self.err("PUT", "/api/grade/o1", {"grade": grade_doc(self.run_id)})[0], 409)
        self.assertEqual(self.err("PUT", "/api/grade/o1", {"grade": grade_doc(self.run_id)}, etag="0" * 64)[0], 409)
        # 'suspected' + −2: nem engedett lépés
        st, e = self.err("PUT", "/api/grade/o1", {"grade": grade_doc(self.run_id, pb=("suspected", -2))}, etag=etag)
        self.assertEqual(st, 422)
        self.assertIn("csak 0 vagy −1", e["message"])
        # feloldás: −1 indoklással; rögzítés az ember megerősítése nélkül → 422: a motor számolt szintje csak
        # előtöltés, a végső bizonyosság emberi ítélet (GRADE-09, M5) — naplósor nem keletkezik
        body = {"grade": grade_doc(self.run_id, pb=("suspected", -1)), "record": True}
        st, e = self.err("PUT", "/api/grade/o1", body, etag=etag)
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        self.assertTrue(e["details"]["needs_certainty"])
        self.assertEqual(e["details"]["computed_certainty"], "low")
        self.assertEqual(len(self.grades()), n_before)
        etag = self.ok("GET", "/api/grade/o1")["_etag"]           # a piszkozat elmentődött
        # az ember megerősíti az előtöltött szintet → naplósor előjeles lépés-szövegekkel
        env = self.ok("PUT", "/api/grade/o1", dict(body, certainty="low"), etag=etag)
        d = env["data"]
        self.assertEqual(d["grade"]["domains"]["publication_bias"]["status"], "resolved")
        self.assertEqual(d["grade"]["certainty"], "low", "a csonk motor: magas −1 (RoB) −1 (PB) = alacsony")
        rec = d["recorded"]
        self.assertEqual(rec["certainty"], "low")
        rows = self.grades()
        self.assertEqual(len(rows), n_before + 1)
        row = api.project_show(self.proj, "grade", rec["id"])
        self.assertTrue(row["risk_of_bias"].startswith("−1 serious: "), row["risk_of_bias"])
        self.assertTrue(row["publication_bias"].startswith("−1 suspected: "), row["publication_bias"])
        self.assertTrue(row["inconsistency"].startswith("0 not serious: "))
        self.assertEqual(row["upgrades"], "0 nincs felminősítés")
        self.assertEqual(row["k"], 13)
        self.assertEqual(row["participants"], 357347)
        self.assertTrue(row["effect"].startswith("RR 0.49 [0.33; 0.73]"), row["effect"])
        self.assertEqual(row["actor"], "user")
        self.assertEqual(d["journal"]["id"], rec["id"])
        # activity: grade.save + grade.record, szabad szöveg (indoklás) nélkül
        acts = [r["action"] for r in self.records()]
        self.assertIn("grade.save", acts)
        self.assertIn("grade.record", acts)
        self.assertNotIn(SECRET, self.activity_text())
        self.assertIn(("grade_put", "o1", "user"), STUB.CALLS)

    def test_b_validation_messages_name_fields_not_values(self):
        self.engine()
        env = self.ok("GET", "/api/grade/o1")
        doc = grade_doc(self.run_id)
        doc["domains"]["indirectness"]["rationale"] = "   "
        doc["upgrades"]["large_effect"] = True
        doc["upgrade_details"] = {"large_effect": {"step": 1, "rationale": ""}}
        st, e = self.err("PUT", "/api/grade/o1", {"grade": doc}, etag=env["_etag"])
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        self.assertIn("indirektség: az ítélethez indoklás kell", e["message"])
        self.assertIn("nagy hatás: a felminősítéshez indoklás kell", e["message"])
        self.assertNotIn(SECRET, e["message"])
        # a motor ValueError-ja szó szerint, 422
        st, e = self.err("PUT", "/api/grade/o1", {"grade": grade_doc(self.run_id, start_reason="boom")},
                         etag=env["_etag"])
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        self.assertIn("A motor elutasította", e["message"])
        # sémán kívüli mező / érték: 400
        bad = grade_doc(self.run_id)
        bad["domains"]["inconsistency"]["step"] = True
        self.assertEqual(self.err("PUT", "/api/grade/o1", {"grade": bad}, etag=env["_etag"])[0], 400)
        self.assertEqual(self.err("PUT", "/api/grade/o1", {"grade": grade_doc(self.run_id), "x": 1},
                                  etag=env["_etag"])[0], 400)

    def test_c_phi_guard_blocks_and_writes_nothing(self):
        self.engine()
        env = self.ok("GET", "/api/grade/o1")
        before = env["data"]["grade"]
        doc = grade_doc(self.run_id)
        doc["domains"]["imprecision"]["rationale"] = "A beteg TAJ-száma %s" % H.taj()
        st, e = self.err("PUT", "/api/grade/o1", {"grade": doc}, etag=env["_etag"])
        self.assertEqual((st, e["code"]), (403, "FORBIDDEN"))
        self.assertNotIn(H.taj(), json.dumps(e, ensure_ascii=False))
        self.assertEqual(self.ok("GET", "/api/grade/o1")["data"]["grade"], before)

    def test_d_human_certainty_fallback_when_engine_has_no_rollup(self):
        self.engine()
        from unittest import mock

        def put_no_rollup(project_dir, outcome_id, doc, actor=None):
            return dict(STUB.grade_put(project_dir, outcome_id, doc, actor), certainty=None)
        self.stack.enter_context(mock.patch.object(api, "grade_put", put_no_rollup, create=True))
        etag = self.ok("GET", "/api/grade/o1")["_etag"]
        body = {"grade": grade_doc(self.run_id, pb=("undetected", 0), rob="not serious"), "record": True}
        st, e = self.err("PUT", "/api/grade/o1", body, etag=etag)
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        self.assertTrue(e["details"]["needs_certainty"])
        etag = self.ok("GET", "/api/grade/o1")["_etag"]           # a piszkozat elmentődött
        body["certainty"] = "moderate"                            # az ember ítélete — a motor figyelmeztet
        env = self.ok("PUT", "/api/grade/o1", body, etag=etag)
        self.assertEqual(env["data"]["recorded"]["certainty"], "moderate")
        self.assertTrue(any("nem egyeztethető" in w for w in env["warnings"]), env["warnings"])

    def test_e_advice_passes_raw_mid_and_run(self):
        self.engine()
        d = self.ok("GET", "/api/grade/o1/advice?mid=0%2C75%E2%80%931%2C25")["data"]
        self.assertEqual(d["mid"], "0,75–1,25")
        self.assertEqual(d["run"]["run_id"], self.run_id)
        self.assertEqual(d["advice"]["domains"]["inconsistency"]["suggested_rating"], "serious")
        self.assertEqual(STUB.CALLS[-1], ("grade_advice", self.run_id, "0,75–1,25"))
        self.assertEqual(self.err("GET", "/api/grade/o1/advice?run=nemfutas")[0], 400)


class SofTests(_Base):
    NAME = "sf"

    def test_a_sof_preview_save_and_export(self):
        self.engine()
        env = self.ok("GET", "/api/sof/o1")
        d = env["data"]
        self.assertIsNone(d["saved"])
        self.assertEqual(d["inputs"]["assumed_risks"][0]["source"], "control_pool")
        self.assertEqual(d["preview"]["rows"][0]["relative_text"]["hu"], "0.49 [0.33; 0.73]")
        # külső alapkockázat érték nélkül: 422
        st, e = self.err("PUT", "/api/sof/o1", {"assumed_risks": [{"source": "external", "label": "Magyar regiszter"}],
                                               "dry_run": True})
        self.assertEqual(st, 422)
        # előnézet: nem ír
        d = self.ok("PUT", "/api/sof/o1", {"assumed_risks": [{"source": "control_pool"},
                                                             {"source": "external", "label": "Regiszter",
                                                              "per_1000": "12,5"}], "dry_run": True})["data"]
        self.assertEqual(d["preview"]["rows"][1]["assumed_risk"]["text"]["hu"], "12,5 / 1000",
                         "a nyers szöveg a motorhoz megy")
        self.assertFalse(os.path.exists(os.path.join(self.proj, "06_kezirat", "sof", "o1.sof.json")))
        # export mentés előtt: 404 útmutatással
        st, e = self.err("POST", "/api/sof/o1/export", {"format": "csv"})
        self.assertEqual(st, 404)
        # mentés
        env = self.ok("PUT", "/api/sof/o1", {"assumed_risks": [{"source": "control_pool"}],
                                             "footnotes": [{"text": "Saját lábjegyzet"}]})
        d = env["data"]
        self.assertEqual(d["saved"]["inputs"]["footnotes"][0]["text"], "Saját lábjegyzet")
        self.assertTrue(d["saved_matches_run"])
        self.assertTrue(env["_etag"])
        self.assertTrue(any("GRADE" in w for w in env["warnings"]), "bizonyosság nélkül figyelmeztet")
        # újramentés If-Match nélkül: 409; az ETag-gel megy
        self.assertEqual(self.err("PUT", "/api/sof/o1", {"assumed_risks": [{"source": "control_pool"}]})[0], 409)
        self.ok("PUT", "/api/sof/o1", {"assumed_risks": [{"source": "control_pool"}],
                                       "footnotes": [{"text": "Saját lábjegyzet"}]}, etag=env["_etag"])
        # CSV export (angol): BOM, CRLF, ',' — és Excel-képletinjekció-őr minden képletnek látszó szöveges cellán
        x = self.ok("POST", "/api/sof/o1/export", {"format": "csv", "lang": "en"})["data"]
        self.assertEqual(x["path"], "06_kezirat/sof/o1.sof.en.csv")
        with open(os.path.join(self.proj, x["path"]), "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        self.assertIn("\r\n", text)
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0][0], "Outcome")
        flat = [c for r in rows for c in r]
        for want in ("'=HYPERLINK(\"x\")", "'-0.5", "'+1 | 2", "'@SUM(A1)", "'\tx"):
            self.assertIn(want, flat)
        self.assertIn("0.49 [0.33; 0.73]", flat, "a motor szövege változatlanul (nem képlet)")
        self.assertFalse([c for c in flat if c[:1] in ("=", "+", "-", "@", "\t", "\r")])
        # a magyar CSV ';' elválasztóval
        xh = self.ok("POST", "/api/sof/o1/export", {"format": "csv", "lang": "hu"})["data"]
        with open(os.path.join(self.proj, xh["path"]), encoding="utf-8-sig") as fh:
            hu = fh.read()
        self.assertTrue(hu.splitlines()[0].startswith("Kimenet;"))
        # aláírt letöltés
        st, h, body = self.srv.raw("GET", x["url"])
        self.assertEqual(st, 200)
        self.assertEqual(body, raw)
        # Markdown: a cső escape-elve, a szöveg a válaszban
        m = self.ok("POST", "/api/sof/o1/export", {"format": "md"})["data"]
        self.assertIn("+1 \\| 2", m["content"])
        self.assertTrue(m["content"].startswith("## "))
        self.assertEqual(self.err("POST", "/api/sof/o1/export", {"format": "xlsx"})[0], 400)
        # activity: értékek nélkül
        acts = [r["action"] for r in self.records()]
        self.assertIn("sof.save", acts)
        self.assertIn("sof.export", acts)
        self.assertNotIn("Saját lábjegyzet", self.activity_text())

    def test_excel_safe_unit(self):
        for s, want in (("=1+1", "'=1+1"), ("+x", "'+x"), ("-1", "'-1"), ("@a", "'@a"), ("\tx", "'\tx"),
                        ("\rx", "'\rx"), ("0.49", "0.49"), ("−0.5", "−0.5"), ("", ""), (None, "")):
            self.assertEqual(grade_sof.excel_safe(s), want)

    def test_b_sof_footnotes_from_grade_rationales(self):
        self.engine()
        etag = self.ok("GET", "/api/grade/o1")["_etag"]
        self.ok("PUT", "/api/grade/o1", {"grade": grade_doc(self.run_id, pb=("suspected", 0))}, etag=etag)
        d = self.ok("GET", "/api/sof/o1")["data"]
        self.assertEqual(d["certainty"], "moderate")
        self.assertEqual(d["certainty_source"], "grade")
        sof_call = [c for c in STUB.CALLS if c[0] == "sof"][-1]
        self.assertEqual(sof_call[3], "moderate")
        self.assertEqual(sof_call[4], 3, "a RoB (−1) és a gyanított PB indoklása + a mentett saját lábjegyzet")


class Amstar2Tests(_Base):
    """Az AMSTAR 2 önellenőrzés EGYETLEN végpontja: ``/api/appraisals/review/amstar2`` (a 6 GRADE/SoF › AMSTAR 2
    képernyő ezt használja; fájl: 04_torzitas_kockazat/appraisals/review.amstar2.<értékelő>.json) — a valódi motor
    eszköz-definíciójával és besorolásával, mindkét „részben igen” konvencióval (KB AMSTAR2-00)."""
    NAME = "am"
    PATH = "/api/appraisals/review/amstar2?rater=%s"

    @staticmethod
    def amstar2_doc(rater="SzK", **kw):
        answers = {str(i): {"value": "yes"} for i in range(1, 17)}
        answers["2"] = {"value": "partial_yes", "rationale": "protokoll van, regisztráció nincs"}
        answers["4"] = {"value": "partial_yes"}
        for i in ("9", "11"):
            answers[i] = {"value": "yes", "parts": {"RCT": "yes", "NRSI": "not_applicable"}}
        doc = {"schema": "szk.appraisal/v1", "tool": "amstar2", "scope": None,
               "target": {"unit": "review", "study_id": None, "key": None}, "assessor": rater,
               "second_assessor": None, "status": "draft", "origin": "human", "answers": answers,
               "domain_judgements": [], "applicability": [], "overall": {"judgement": "high", "rationale": "x"}}
        doc.update(kw)
        return doc

    def test_amstar2_both_conventions_override_and_ai_draft(self):
        self.assertEqual(self.err("GET", "/api/amstar2")[0], 404, "nincs második AMSTAR 2 végpont")
        d = self.ok("GET", self.PATH % "SzK")["data"]
        self.assertFalse(d["exists"])
        doc = self.amstar2_doc()
        env = self.ok("PUT", self.PATH % "SzK", {"doc": doc})
        am = env["data"]["check"]["amstar2"]
        # a projekt konvenciója (meets): két „részben igen” kritikus tétel nem gyengeség → magas; a másik
        # konvencióval (weakness) két nem kritikus gyengeség → mérsékelt; a motor mindkettőt megadja
        self.assertEqual((am["convention"], am["rating"]), ("meets", "high"))
        self.assertEqual((am["alternative"]["convention"], am["alternative"]["rating"]), ("weakness", "moderate"))
        self.assertTrue(am["differs"])
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "04_torzitas_kockazat", "appraisals",
                                                    "review.amstar2.SzK.json")))
        # felülbírálás indoklás nélkül: 422 X017
        doc2 = dict(doc, overall={"judgement": "moderate", "rationale": "x", "override_reason": None})
        st, e = self.err("PUT", self.PATH % "SzK", {"doc": doc2}, etag=env["_etag"])
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        self.assertIn("X017", e["message"])
        doc2["overall"]["override_reason"] = "a 2. tételt szigorúbban ítéljük"
        env = self.ok("PUT", self.PATH % "SzK", {"doc": doc2}, etag=env["_etag"])
        # If-Match nélkül nem írható felül
        self.assertEqual(self.err("PUT", self.PATH % "SzK", {"doc": doc})[0], 409)
        # AI-vázlat jóváhagyás nélkül nem lehet kész (6. döntés), és az AI nem emberi értékelő
        ai = self.amstar2_doc(rater="ai", assessor="ai", origin="ai_draft", status="complete")
        st, e = self.err("PUT", self.PATH % "ai", {"doc": ai})
        self.assertEqual(st, 422)
        self.assertIn("AI-vázlat", e["message"])
        self.assertNotIn("a 2. tételt szigorúbban", self.activity_text())


class ProtocolTests(_Base):
    NAME = "pr"

    def test_protocol_read_write(self):
        env = self.ok("GET", "/api/protocol")
        d = env["data"]
        self.assertEqual(d["title"], "BCG és Normand — teszt")
        self.assertEqual([o["id"] for o in d["outcomes"]], ["o1", "o2"])
        self.assertEqual(d["data_class"], "A")
        self.assertIsNone(d["registration"])
        spec = [s for s in d["specs"] if s["name"] == "o1_primary"][0]
        self.assertIs(spec["prespecified"], False)
        etag = env["_etag"]
        body = {"question": {"P": "oltatlan személyek", "I": "BCG", "C": "kontroll", "O": "TBC"},
                "review_type": "intervention", "registration": {"registry": "PROSPERO", "id": "CRD42024000001"}}
        env = self.ok("PUT", "/api/protocol", body, etag=etag)
        d = env["data"]
        self.assertEqual(sorted(d["changed"]), ["question", "registration", "review_type"])
        self.assertEqual(d["registration"], {"registry": "PROSPERO", "id": "CRD42024000001"})
        with open(os.path.join(self.proj, "ma-projekt.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        self.assertEqual(meta["protocol"]["registration"]["id"], "CRD42024000001")
        self.assertEqual(meta["question"]["P"], "oltatlan személyek")
        self.assertEqual(meta["outcomes"][0]["name"], {"hu": "TBC-incidencia", "en": "TB incidence"})
        # régi ETag: 409; ugyanaz újra: nincs változás, nincs írás
        self.assertEqual(self.err("PUT", "/api/protocol", {"title": "Új cím"}, etag=etag)[0], 409)
        n = len(self.records())
        same = self.ok("PUT", "/api/protocol", body, etag=env["_etag"])
        self.assertEqual(same["data"]["changed"], [])
        self.assertEqual(len(self.records()), n)
        # activity: csak a mezők neve
        rec = [r for r in self.records() if r["action"] == "project.protocol"][-1]
        self.assertEqual(sorted(rec["details"]["fields"]), ["question", "registration", "review_type"])
        self.assertNotIn("oltatlan személyek", self.activity_text())
        # ismeretlen típus / mező: 400; üres cím: 422; PHI: 403 (érték nélkül)
        self.assertEqual(self.err("PUT", "/api/protocol", {"review_type": "meta"}, etag=same["_etag"])[0], 400)
        self.assertEqual(self.err("PUT", "/api/protocol", {"foo": 1}, etag=same["_etag"])[0], 400)
        self.assertEqual(self.err("PUT", "/api/protocol", {"title": "  "}, etag=same["_etag"])[0], 422)
        st, e = self.err("PUT", "/api/protocol", {"question": {"P": "beteg TAJ %s" % H.taj()}}, etag=same["_etag"])
        self.assertEqual(st, 403)
        self.assertNotIn(H.taj(), json.dumps(e, ensure_ascii=False))
        # regisztráció törlése
        d = self.ok("PUT", "/api/protocol", {"registration": None}, etag=same["_etag"])["data"]
        self.assertIsNone(d["registration"])
        self.assertEqual(d["changed"], ["registration"])

    def test_protocol_creates_project_json_when_missing(self):
        tmp = H.tmpdir("ma_v1_prot_new_")
        try:
            proj, home = H.make_project(tmp, name="uj")
            os.remove(os.path.join(proj, "ma-projekt.json"))
            srv = H.Srv(proj, home, tmp, name="uj")
            try:
                env = srv.ok("GET", "/api/protocol")
                self.assertFalse(env["data"]["has_project_json"])
                d = srv.ok("PUT", "/api/protocol", {"question": {"P": "felnőttek"}})["data"]
                self.assertTrue(d["has_project_json"])
                with open(os.path.join(proj, "ma-projekt.json"), encoding="utf-8") as fh:
                    meta = json.load(fh)
                self.assertEqual(meta["data_class"], "A")
                self.assertEqual(meta["question"]["P"], "felnőttek")
            finally:
                srv.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class StaleRunTests(_Base):
    NAME = "st"

    def test_stale_and_old_run_block_recording(self):
        self.engine()
        etag = self.ok("GET", "/api/grade/o1")["_etag"]
        doc = grade_doc(self.run_id, pb=("undetected", 0))
        etag = self.ok("PUT", "/api/grade/o1", {"grade": doc}, etag=etag)["_etag"]
        with open(os.path.join(self.proj, H.O1), "a", encoding="utf-8") as fh:
            fh.write("\n")                                   # az adattábla változott → a futás ELAVULT (X001)
        st, e = self.err("PUT", "/api/grade/o1", {"grade": doc, "record": True}, etag=etag)
        self.assertEqual((st, e["code"]), (409, "GATE_BLOCKED"))
        self.assertEqual(e["details"]["code"], "X001")
        self.assertTrue(self.ok("GET", "/api/grade/o1")["data"]["run"]["stale"])
        # új elsődleges futás: a régi futásra hivatkozó ítélet nem rögzíthető (X007), az újra igen
        sp = H.spec("o1_primary", outcome="o1")
        time.sleep(1.1)                                      # a futás-azonosító másodperc-pontosságú
        job = self.srv.job(self.ok("POST", "/api/analyze", {"mode": "commit", "spec": sp, "client_seq": 9})["data"])
        new_id = job["result"]["run"]["run_id"]
        self.assertNotEqual(new_id, self.run_id)
        env = self.ok("GET", "/api/grade/o1")
        self.assertEqual(env["data"]["run"]["run_id"], new_id)
        self.assertFalse(env["data"]["run_matches"])
        st, e = self.err("PUT", "/api/grade/o1", {"grade": doc, "record": True}, etag=env["_etag"])
        self.assertEqual(e["details"]["code"], "X007")
        d = self.ok("PUT", "/api/grade/o1", {"grade": dict(doc, run_id=new_id), "record": True,
                                             "certainty": "moderate"}, etag=env["_etag"])["data"]
        self.assertTrue(d["run_matches"])
        self.assertEqual(d["recorded"]["certainty"], "moderate")


class RealEngineTests(_Base):
    """A motor MÁR MEGLÉVŐ v1-függvényei (metaelemzes.grade_help, metaelemzes.projekt) a homlokzat helyére kötve — így
    az integrátor kiközvetése előtt is ellenőrizzük, hogy a végpontok a valódi motor aláírásaival és alakjaival
    működnek. Ha a motor-modul hiányzik vagy nem teljes: kihagyva."""
    NAME = "re"

    def setUp(self):
        super().setUp()
        try:
            from metaelemzes import grade_help, projekt
        except ImportError as exc:                          # pragma: no cover — a motor-munkafolyamat előtt
            self.skipTest("a motor grade_help modulja nem tölthető: %s" % exc)
        mapping = {"grade_get": (projekt, "load_grade_doc"), "grade_put": (projekt, "save_grade_doc"),
                   "grade_record": (projekt, "record_grade_doc"), "grade_advice": (grade_help, "advice"),
                   "sof": (grade_help, "sof"), "sof_csv": (grade_help, "sof_csv"),
                   "sof_markdown": (grade_help, "sof_markdown"),
                   "sof_problems": (grade_help, "sof_certainty_problems"),
                   "amstar2_consistency": (grade_help, "amstar2_consistency")}
        missing = ["%s.%s" % (m.__name__, n) for m, n in mapping.values() if not callable(getattr(m, n, None))]
        if missing:
            self.skipTest("a motor v1-függvényei még hiányoznak: %s" % ", ".join(missing))
        from unittest import mock
        # a valódi eszköz-definíció marad (az AMSTAR 2 az értékelés-végponton a motor instrument_get-jével megy)
        for p in STUB.absent([n for n in ALL_ENGINE if n != "instrument_get"]):
            self.stack.enter_context(p)
        for name, (m, n) in mapping.items():
            self.stack.enter_context(mock.patch.object(api, name, getattr(m, n), create=True))

    def test_recorded_human_certainty_and_computed_level(self):
        """FID-3: a rögzített EMBERI bizonyosság a nézetben emberiként (certainty_source: human), a lépésekből adódó
        motor-szint külön mezőben (computed_certainty) — a felület így nem nevezi a kettőt egynek. F11: a külső
        alapkockázat forrása (note) a SoF lábjegyzetébe kerül."""
        doc = grade_doc(self.run_id, pb=("suspected", -1))
        cur = self.ok("GET", "/api/grade/o1")
        env = self.ok("PUT", "/api/grade/o1", {"grade": doc}, etag=cur["_etag"]) if cur["_etag"] else \
            self.ok("PUT", "/api/grade/o1", {"grade": doc})
        self.assertEqual(env["data"]["computed_certainty"], "low")
        env = self.ok("PUT", "/api/grade/o1", {"grade": doc, "record": True, "certainty": "moderate"}, etag=env["_etag"])
        g = self.ok("GET", "/api/grade/o1")["data"]
        self.assertEqual(g["grade"]["certainty"], "moderate")
        self.assertEqual(g["grade"].get("certainty_source"), "human")
        self.assertEqual(g["computed_certainty"], "low", "a motor szintje a lépésekből (emberi ítélet mellett)")
        sv = self.ok("PUT", "/api/sof/o1", {"assumed_risks": [{"source": "control_pool"},
                                                               {"source": "external", "label": "Regiszter", "per_1000": "12,5",
                                                                "note": "KSH Népegészségügyi Adattár 2023"}],
                                            "dry_run": True})["data"]
        notes = json.dumps(sv["preview"].get("footnotes") or [], ensure_ascii=False)
        self.assertIn("KSH Népegészségügyi Adattár 2023", notes)
        self.assertNotIn("nincs megadva", json.dumps(sv["preview"]["rows"][0], ensure_ascii=False))

    def test_grade_sof_amstar2_with_the_real_engine(self):
        adv = self.ok("GET", "/api/grade/o1/advice?mid=0%2C75%E2%80%931%2C25")["data"]["advice"]
        self.assertEqual(adv["schema"], "szk.ma.grade/v1")
        self.assertEqual(adv["run_id"], self.run_id)
        for d in grade_route.DOMAINS:
            self.assertIsNone(adv["domains"][d]["rating"], "a motor soha nem dönt")
            self.assertIn("summary", adv["domains"][d]["suggestion"])
        env = self.ok("GET", "/api/grade/o1")
        self.assertIsNone(env["data"]["grade"])
        doc = grade_doc(self.run_id)
        doc["run_summary"] = adv["run_summary"]
        for d in grade_route.DOMAINS:
            doc["domains"][d]["suggestion"] = adv["domains"][d]["suggestion"]
        env = self.ok("PUT", "/api/grade/o1", {"grade": doc})
        g = env["data"]["grade"]
        self.assertEqual(g["domains"]["publication_bias"]["status"], "unresolved")
        self.assertIsNone(g["certainty"])
        st, e = self.err("PUT", "/api/grade/o1", {"grade": doc, "record": True}, etag=env["_etag"])
        self.assertEqual((st, e["details"]["code"]), (409, "X019"))
        doc["domains"]["publication_bias"]["step"] = -1
        # a motor számolt szintje ('low') csak előtöltés: megerősítés nélkül a motor is, a munkapad is elutasít
        st, e = self.err("PUT", "/api/grade/o1", {"grade": doc, "record": True}, etag=env["_etag"])
        self.assertEqual(st, 422)
        self.assertTrue(e["details"]["needs_certainty"])
        self.assertEqual(e["details"]["computed_certainty"], "low")
        etag = self.ok("GET", "/api/grade/o1")["_etag"]
        # a még nem rögzített (piszkozat) GRADE bizonyossága nem kerülhet a mentett SoF-ba (4. döntés, X008)
        sv = self.ok("GET", "/api/sof/o1")["data"]
        row0 = sv["preview"]["rows"][0]
        self.assertEqual((row0["certainty"], row0["certainty_basis"]), ("low", "draft"), "az előnézet piszkozatként jelöl")
        st, e = self.err("PUT", "/api/sof/o1", {"assumed_risks": [{"source": "control_pool"}]})
        self.assertEqual((st, e["details"]["code"]), (422, "X008"))
        self.assertFalse(os.path.isfile(os.path.join(self.proj, "06_kezirat", "sof", "o1.sof.json")))
        env = self.ok("PUT", "/api/grade/o1", {"grade": doc, "record": True, "certainty": "low"}, etag=etag)
        d = env["data"]
        self.assertEqual(d["grade"]["certainty"], "low")
        self.assertEqual(d["grade"]["status"], "recorded", "a motor a fájlt 'recorded'-re állítja")
        self.assertEqual(d["grade"]["journal_id"], d["recorded"]["id"])
        self.assertEqual(self.ok("GET", "/api/grade/o1")["_etag"], env["_etag"], "a válasz ETag-je a rögzítés utáni")
        row = api.project_show(self.proj, "grade", d["recorded"]["id"])
        self.assertEqual(row["certainty"], "low")
        self.assertEqual(row["k"], 13)
        self.assertTrue(row["effect"].startswith("RR 0.49"), row["effect"])
        self.assertIn("serious", row["risk_of_bias"])
        # SoF a motorral: a bizonyosság és a lábjegyzetek a mentett GRADE-ből; export a motor CSV-jével + BOM
        sv = self.ok("GET", "/api/sof/o1")["data"]
        self.assertEqual(sv["certainty"], "low")
        row0 = sv["preview"]["rows"][0]
        self.assertIn("0.49", row0["relative_text"]["hu"])
        self.assertTrue(sv["preview"]["footnotes"], "lábjegyzetek a GRADE-indoklásokból")
        self.ok("PUT", "/api/sof/o1", {"assumed_risks": [{"source": "control_pool"},
                                                         {"source": "external", "label": "Regiszter", "per_1000": "12,5"}]})
        x = self.ok("POST", "/api/sof/o1/export", {"format": "csv", "lang": "hu"})["data"]
        with open(os.path.join(self.proj, x["path"]), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("\ufeff"))
        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff")), delimiter=";"))
        self.assertEqual(rows[0][0], "Kimenet")
        self.assertFalse([c for r in rows for c in r if c[:1] in ("=", "+", "-", "@", "\t", "\r")])
        m = self.ok("POST", "/api/sof/o1/export", {"format": "md", "lang": "en"})["data"]
        self.assertIn("| Outcome |", m["content"])
        # AMSTAR 2 a motor algoritmusával, mindkét konvencióval — az egyetlen AMSTAR 2 végponton
        doc = Amstar2Tests.amstar2_doc()
        doc["answers"]["2"] = {"value": "yes"}
        a = self.ok("PUT", Amstar2Tests.PATH % "SzK", {"doc": doc})["data"]["check"]["amstar2"]
        self.assertEqual(a["rating"], "high")
        self.assertEqual(a["alternative"]["rating"], "high", "egy nem kritikus gyengeség még „magas”")
        cons = api.amstar2_consistency({k: v["value"] for k, v in doc["answers"].items()})
        self.assertEqual(cons["by_convention"]["weakness"]["rating"], "high", "a GRADE-modul ugyanígy számol")
        self.assertNotIn("gyenge tesztek", self.activity_text())


if __name__ == "__main__":
    unittest.main()
