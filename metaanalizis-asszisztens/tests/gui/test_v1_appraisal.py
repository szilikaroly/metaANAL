# -*- coding: utf-8 -*-
"""Értékelés-végpontok (terv 3.4, 3.5.10–3.5.13, 4.11, 5.4, 6.5; 11. fejezet 5–6. döntés).

A motor v1 értékelő homlokzata (instruments/appraisal) párhuzamosan készül: a tesztek a
``_appraisal_engine_stub`` CSONKOT illesztik a ``metaelemzes.api``-ba, és a munkapad saját logikáját ellenőrzik
(útvonalak, fájl-elrendezés, ETag/409, X017-indoklás és naplózás, AI-vázlat jóváhagyása, konszenzus, fájlcsere,
rob-szinkron eredettel, PHI-őr, activity szöveg nélkül). Csonk nélkül a motorfüggő végpont 424-et ad."""
import contextlib
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_routes_harness as H  # noqa: E402
import _appraisal_engine_stub as STUB  # noqa: E402
from metaelemzes import api  # noqa: E402
from ma_gui.routes import appraisal_common as C  # noqa: E402

QUOTE = "Allocation by sealed opaque envelopes (Table 1)"
SECRET_RATIONALE = "nyitott elrendezés, de az elemzés ITT — titkos indoklás-szöveg"


def rob2_doc(rater="SzK", unit="S1", target="o1", answers=None, status="draft", **kw):
    doc = {"schema": "szk.appraisal/v1", "tool": "rob2", "scope": "assignment",
           "target": {"unit": unit, "study_id": unit, "key": target}, "assessor": rater, "second_assessor": None,
           "status": status, "origin": "human", "answers": answers or {}, "domain_judgements": [],
           "applicability": [], "overall": None}
    doc.update(kw)
    return doc


FULL_LOW = {"1.1": {"value": "yes", "evidence": {"text": QUOTE, "page": 4, "locator": "Table 1"}},
            "1.2": {"value": "probably_yes"}, "1.3": {"value": "no"}, "2.1": {"value": "yes"},
            "2.6": {"value": "yes"}}


def path_of(unit, tool, target, rater):
    return "/api/appraisals/%s/%s?rater=%s%s" % (unit, tool, rater, "&target=" + target if target else "")


class _Base(unittest.TestCase):
    stub_names = STUB.NAMES

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_v1_appr_")
        cls.proj, cls.home = H.make_project(cls.tmp)
        studies = {"schema": "szk.ma.studies/v1", "studies": [
            {"study_id": "S1", "label": "Aronson 1948", "design": "rct_parallel", "outcomes": ["o1"]},
            {"study_id": "S2", "label": "Ferguson & Simes 1949", "design": "rct_parallel", "outcomes": ["o1"]}]}
        with open(os.path.join(cls.proj, "03_adatok", "studies.json"), "w", encoding="utf-8") as fh:
            json.dump(studies, fh, ensure_ascii=False)
        cls.engine = STUB.StubEngine()
        cls.stack = contextlib.ExitStack()
        for p in STUB.patch(cls.engine, cls.stub_names):
            cls.stack.enter_context(p)
        cls.srv = H.Srv(cls.proj, cls.home, cls.tmp, name="appr")

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.stack.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ok(self, *a, **kw):
        return self.srv.ok(*a, **kw)

    def err(self, *a, **kw):
        return self.srv.err(*a, **kw)

    def file(self, rel):
        return os.path.join(self.proj, *rel.split("/"))

    def activity_text(self):
        p = self.file("07_ellenorzes/activity.jsonl")
        if not os.path.isfile(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def put(self, doc, unit="S1", tool="rob2", target="o1", rater="SzK", etag=None, expect_ok=True):
        hdrs = [("If-Match", etag)] if etag else []
        call = self.ok if expect_ok else self.err
        return call("PUT", path_of(unit, tool, target, rater), {"doc": doc}, headers=hdrs)


class EngineMissingTests(unittest.TestCase):
    """Csonk nélkül (a mai motor): a motorfüggő végpontok 424 + magyar üzenet; a lista a fájlokból készül."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_v1_appr0_")
        cls.proj, cls.home = H.make_project(cls.tmp)
        cls.srv = H.Srv(cls.proj, cls.home, cls.tmp, name="appr0")

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_capability_missing(self):
        if C.engine_fn("instruments") is not None:
            self.skipTest("a motor már tartalmazza az értékelő függvényeket")
        st, e = self.srv.err("GET", "/api/instruments")
        self.assertEqual((st, e["code"]), (424, "CAPABILITY_MISSING"))
        self.assertIn("motor", e["message"])
        self.assertIn("instruments_list", e["details"]["engine_functions"])
        st, e = self.srv.err("GET", path_of("S1", "rob2", "o1", "SzK"))
        self.assertEqual(st, 424)
        env = self.srv.ok("GET", "/api/appraisals")
        self.assertFalse(env["data"]["engine"]["available"])
        self.assertEqual(env["data"]["items"], [])
        self.assertTrue(env["warnings"])


class InstrumentAndCrudTests(_Base):
    def test_instruments(self):
        env = self.ok("GET", "/api/instruments")
        keys = [x["key"] for x in env["data"]["instruments"]]
        self.assertIn("rob2", keys)
        self.assertTrue(env["data"]["engine"]["available"])
        self.assertIn("validator", env["data"])
        inst = self.ok("GET", "/api/instruments/rob2")["data"]
        self.assertEqual(inst["schema"], "szk.instrument/v1")
        st, e = self.err("GET", "/api/instruments/nincs-ilyen")
        self.assertEqual(st, 404)
        st, e = self.err("GET", "/api/instruments/ROB2")
        self.assertEqual(st, 400)

    def test_skeleton_save_conflict_and_layout(self):
        env = self.ok("GET", path_of("S2", "rob2", "o1", "SzK"))
        d = env["data"]
        self.assertFalse(d["exists"])
        self.assertIsNone(env["_etag"])
        self.assertEqual(d["doc"]["schema"], "szk.appraisal/v1")
        self.assertEqual(d["check"]["expected"], 5)              # assignment-hatókör: a 2.9 (adherence) kimarad
        self.assertEqual(d["path"], "04_torzitas_kockazat/appraisals/S2.rob2.o1.SzK.json")
        doc = rob2_doc(unit="S2", answers={"1.1": {"value": "yes", "evidence": {"text": QUOTE, "page": 4}}})
        env = self.put(doc, unit="S2")
        etag = env["_etag"]
        self.assertTrue(etag)
        self.assertTrue(os.path.isfile(self.file(d["path"])))
        with open(self.file(d["path"]), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["target"]["outcome"], "o1")       # a motor X003-a ezt nézi
        self.assertEqual(saved["target"]["unit"], "S2")
        self.assertTrue(saved["created"] and saved["updated"])
        # új fájl If-Match nélkül, meglévőre → 409; rossz ETag → 409; jó → 200
        st, e = self.put(doc, unit="S2", expect_ok=False)
        self.assertEqual((st, e["code"]), (409, "CONFLICT"))
        st, e = self.put(doc, unit="S2", etag='"' + "0" * 64 + '"', expect_ok=False)
        self.assertEqual(st, 409)
        env = self.put(rob2_doc(unit="S2", answers=dict(FULL_LOW)), unit="S2", etag=etag)
        self.assertTrue(env["data"]["check"]["complete"])
        self.assertNotEqual(env["_etag"], etag)
        # az activity-sor nem tartalmaz értékelés-szöveget (T10)
        act = self.activity_text()
        self.assertIn("appraisal.save", act)
        self.assertNotIn(QUOTE, act)
        # lista
        lst = self.ok("GET", "/api/appraisals?tool=rob2&answers=1")["data"]
        it = [x for x in lst["items"] if x["unit"] == "S2"][0]
        self.assertEqual(it["rater"], "SzK")
        self.assertEqual(it["answers"]["1.1"], "yes")
        self.assertNotIn("evidence", json.dumps(it))
        self.assertTrue(it["check"]["complete"])
        self.assertEqual([s["study_id"] for s in lst["studies"]], ["S1", "S2"])
        self.assertEqual([o["id"] for o in lst["outcomes"]], ["o1", "o2"])

    def test_shape_errors(self):
        bad = rob2_doc(unit="S1", target="o2", answers={"1.1": {"value": "talán"}})
        st, e = self.put(bad, unit="S1", target="o2", expect_ok=False)
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        self.assertTrue(any("value" in p for p in e["details"]["problems"]))
        self.assertNotIn("talán", e["message"])                 # értéket nem idézünk
        st, e = self.put(rob2_doc(rater="KP", unit="S1", target="o2"), unit="S1", target="o2", rater="SzK",
                         expect_ok=False)
        self.assertEqual(st, 422)
        st, e = self.err("GET", path_of("S1", "rob2", "o1", "consensus").replace("/S1/", "/consensus/"))
        self.assertEqual(st, 400)
        st, e = self.err("GET", "/api/appraisals/S1/rob2")
        self.assertEqual(st, 400)                                # rater kötelező

    def test_override_needs_reason_and_is_logged(self):
        answers = dict(FULL_LOW, **{"2.6": {"value": "no"}})        # D2 implikált: high
        dj = [{"domain": "2", "pass": None, "judgement": "some_concerns", "rationale": "x", "override_reason": None}]
        doc = rob2_doc(unit="S1", target="o2", answers=answers, domain_judgements=dj)
        chk = self.ok("POST", "/api/appraisals/S1/rob2/check?target=o2&rater=SzK", {"doc": doc})["data"]
        self.assertTrue(chk["check"]["overrides"][0]["reason_missing"])
        self.assertFalse(os.path.exists(self.file("04_torzitas_kockazat/appraisals/S1.rob2.o2.SzK.json")))
        st, e = self.put(doc, unit="S1", target="o2", expect_ok=False)
        self.assertEqual(st, 422)
        self.assertIn("X017", e["message"])
        self.assertEqual(e["details"]["overrides"][0]["implied"], "high")
        dj[0]["override_reason"] = SECRET_RATIONALE
        env = self.put(doc, unit="S1", target="o2")
        saved = env["data"]["doc"]
        did = saved["domain_judgements"][0]["decision_id"]
        self.assertIsInstance(did, int)
        decisions = api.project_list(self.proj, "decisions")
        hit = [d for d in decisions if d.get("id") == did][0]
        self.assertIn("X017", json.dumps(hit, ensure_ascii=False))
        self.assertIn("D2", hit["decision"])
        self.assertNotIn(SECRET_RATIONALE, self.activity_text())
        # ismételt mentés: nincs új döntés (a decision_id megmarad)
        env2 = self.put(saved, unit="S1", target="o2", etag=env["_etag"])
        self.assertEqual(env2["data"]["doc"]["domain_judgements"][0]["decision_id"], did)
        self.assertEqual(len(api.project_list(self.proj, "decisions")), len(decisions))

    def test_complete_requires_all_items(self):
        doc = rob2_doc(unit="S1", target="o3x", answers={"1.1": {"value": "yes"}}, status="complete")
        st, e = self.put(doc, unit="S1", target="o3x", expect_ok=False)
        self.assertEqual(st, 422)
        self.assertTrue(e["details"]["missing"])

    def test_phi_in_free_text_is_refused_without_echo(self):
        t = H.taj()
        doc = rob2_doc(unit="S1", target="phi", answers={"1.1": {"value": "yes", "comment": "beteg TAJ " + t}})
        st, e = self.put(doc, unit="S1", target="phi", expect_ok=False)
        self.assertEqual((st, e["code"]), (403, "FORBIDDEN"))
        self.assertNotIn(t, json.dumps(e, ensure_ascii=False))

    def test_slug_collision(self):
        self.put(rob2_doc(unit="A B", target=None), unit="A%20B", target=None)
        st, e = self.err("GET", path_of("A_B", "rob2", None, "SzK"))
        self.assertEqual((st, e["code"]), (409, "CONFLICT"))


class AiDraftAndConsensusTests(_Base):
    def test_ai_draft_never_rater_and_approval(self):
        ai = rob2_doc(rater="ai", unit="S1", answers=dict(FULL_LOW), origin="ai_draft", assessor="ai",
                      status="complete")
        ai["answers"]["1.1"] = {"value": "yes", "evidence": {"text": QUOTE, "page": 4},
                                "rationale": {"asks": "Véletlen volt-e a besorolás?", "because": "számítógépes lista",
                                              "change": "ha váltakozó besorolás lenne", "uncertain": None}}
        st, e = self.put(ai, rater="ai", expect_ok=False)
        self.assertEqual(st, 422)                                # kész csak jóváhagyás után
        ai["status"] = "draft"
        env = self.put(ai, rater="ai")
        etag = env["_etag"]
        self.assertEqual(env["data"]["human_raters"], [])
        st, e = self.err("POST", "/api/appraisals/S1/rob2/approve?rater=ai&target=o1", {"approver": "SzK"})
        self.assertEqual(st, 400)                                # If-Match nélkül nem
        st, e = self.err("POST", "/api/appraisals/S1/rob2/approve?rater=ai&target=o1", {"approver": "ai"},
                         headers=[("If-Match", etag)])
        self.assertEqual(st, 400)                                # az AI nem hagyhatja jóvá önmagát
        env = self.ok("POST", "/api/appraisals/S1/rob2/approve?rater=ai&target=o1", {"approver": "SzK"},
                      headers=[("If-Match", etag)])
        doc = env["data"]["doc"]
        self.assertEqual(doc["approved_by"], "SzK")
        self.assertEqual(doc["status"], "complete")
        self.assertEqual(doc["origin"], "ai_draft")              # az eredet megmarad
        self.assertIsInstance(doc.get("approval_decision_id"), int)
        st, e = self.err("POST", "/api/appraisals/S1/rob2/approve?rater=ai&target=o1", {"approver": "SzK"},
                         headers=[("If-Match", env["_etag"])])
        self.assertEqual(st, 409)
        # konszenzus: egy emberi értékelés + AI → nem kész; az AI kimarad
        self.put(rob2_doc(rater="SzK", unit="S1", answers=dict(FULL_LOW)))
        cons = self.ok("GET", "/api/appraisals/consensus/S1/rob2?target=o1")["data"]
        self.assertFalse(cons["ready"])
        self.assertEqual([x["rater"] for x in cons["excluded"]], ["ai"])
        kp = dict(FULL_LOW, **{"1.2": {"value": "no_information"}})
        self.put(rob2_doc(rater="KP", unit="S1", answers=kp), rater="KP")
        env = self.ok("GET", "/api/appraisals/consensus/S1/rob2?target=o1")
        cons = env["data"]
        self.assertTrue(cons["ready"])
        self.assertEqual((cons["a"]["rater"], cons["b"]["rater"]), ("KP", "SzK"))
        self.assertEqual(cons["agreement"]["disagree"], 1)
        self.assertIn("κ", cons["agreement"]["kappa_text"]["hu"])
        self.assertTrue(env["warnings"])
        # konszenzus-változat mentése
        cdoc = rob2_doc(rater="consensus", unit="S1", answers=dict(FULL_LOW), status="consensus", assessor="SzK",
                        second_assessor="KP")
        env = self.put(cdoc, rater="consensus")
        self.assertEqual(env["data"]["doc"]["status"], "consensus")
        self.assertTrue(os.path.isfile(self.file("04_torzitas_kockazat/appraisals/S1.rob2.o1.consensus.json")))
        # második értékelő nélkül nem
        bad = dict(cdoc, second_assessor="XY")
        st, e = self.put(bad, rater="consensus", etag=env["_etag"], expect_ok=False)
        self.assertEqual(st, 422)
        self.assertEqual(e["details"]["missing_raters"], ["XY"])


class ExchangeTests(_Base):
    def test_export_import_and_inbox(self):
        self.put(rob2_doc(rater="SzK", unit="S2", target="o1", answers=dict(FULL_LOW)), unit="S2")
        ex = self.ok("POST", "/api/appraisals/export", {"unit": "S2", "tool": "rob2", "target": "o1", "rater": "SzK"})
        self.assertEqual(ex["data"]["filename"], "S2.rob2.o1.SzK.json")
        self.assertEqual(ex["data"]["doc"]["assessor"], "SzK")
        # a második értékelő fájlja (5. döntés)
        kp = rob2_doc(rater="KP", unit="S2", target="o1", answers=dict(FULL_LOW))
        env = self.ok("POST", "/api/appraisals/import", {"doc": kp})
        self.assertEqual(env["data"]["imported"][0]["state"], "new")
        self.assertEqual(env["data"]["imported"][0]["path"], "04_torzitas_kockazat/appraisals/S2.rob2.o1.KP.json")
        env = self.ok("POST", "/api/appraisals/import", {"doc": kp})
        self.assertEqual(env["data"]["imported"][0]["state"], "same")
        kp2 = json.loads(json.dumps(kp))
        kp2["answers"]["1.2"] = {"value": "no"}
        st, e = self.err("POST", "/api/appraisals/import", {"doc": kp2})
        self.assertEqual((st, e["code"]), (409, "CONFLICT"))
        self.assertEqual(e["details"]["conflicts"][0]["rater"], "KP")
        env = self.ok("POST", "/api/appraisals/import", {"doc": kp2, "replace": True})
        self.assertEqual(env["data"]["imported"][0]["state"], "replaced")
        # csomag a beérkezett mappán át (a 64 KB-os törzskorlát nélkül)
        bundle = {"schema": "szk.appraisal-bundle/v1", "items": [rob2_doc(rater="KP", unit="S1", target="o9",
                                                                          answers=dict(FULL_LOW))]}
        inbox = self.file(C.INBOX_DIR)
        os.makedirs(inbox, exist_ok=True)
        with open(os.path.join(inbox, "kp.json"), "w", encoding="utf-8") as fh:
            json.dump(bundle, fh)
        lst = self.ok("GET", "/api/appraisals/inbox")["data"]
        self.assertEqual([(f["name"], f["kind"], f["items"]) for f in lst["files"]], [("kp.json", "bundle", 1)])
        env = self.ok("POST", "/api/appraisals/import", {"path": C.INBOX_DIR + "/kp.json"})
        self.assertEqual(env["data"]["imported"][0]["path"], "04_torzitas_kockazat/appraisals/S1.rob2.o9.KP.json")
        self.assertEqual(env["data"]["source"], C.INBOX_DIR + "/kp.json")
        st, e = self.err("POST", "/api/appraisals/import", {"path": "03_adatok/studies.json"})
        self.assertEqual(st, 403)
        st, e = self.err("POST", "/api/appraisals/import", {"doc": {"schema": "valami"}})
        self.assertEqual(st, 422)
        # minden értékelés egy csomagban
        b = self.ok("POST", "/api/appraisals/export", {"all": True, "rater": "KP"})["data"]
        self.assertEqual(b["bundle"]["schema"], "szk.appraisal-bundle/v1")
        self.assertEqual(b["n"], 2)
        self.assertNotIn(QUOTE, self.activity_text())


class RobSummarySyncTests(_Base):
    def test_summary_and_sync_with_provenance(self):
        dj = [{"domain": "1", "pass": None, "judgement": "low"}, {"domain": "2", "pass": None, "judgement": "high",
                                                                   "override_reason": "ok"}]
        answers = dict(FULL_LOW, **{"2.6": {"value": "no"}})
        doc = rob2_doc(rater="SzK", unit="S1", answers=answers, status="complete", domain_judgements=dj,
                       overall={"judgement": "high", "rationale": "D2"})
        self.put(doc)
        summ = self.ok("GET", "/api/appraisals/rob-summary?tool=rob2&outcome=o1")
        rows = summ["data"]["studies"]
        self.assertEqual([(r["study_id"], r["overall"]) for r in rows], [("S1", "high")])
        self.assertTrue(summ["warnings"])                        # még nincs commit-futás → súly nélkül
        st, e = self.err("GET", "/api/appraisals/rob-summary")
        self.assertEqual(st, 400)
        pre = self.ok("POST", "/api/appraisals/rob-sync", {"tool": "rob2", "outcome": "o1", "dry_run": True})
        d = pre["data"]
        self.assertFalse(d["applied"])
        self.assertFalse(d["column_exists"])
        ch = d["proposal"]["changes"]
        self.assertEqual([(c["label"], c["before"], c["after"]) for c in ch], [("Aronson 1948", "", "high")])
        st, e = self.err("POST", "/api/appraisals/rob-sync", {"tool": "rob2", "outcome": "o1", "dry_run": False})
        self.assertEqual(st, 400)                                # If-Match nélkül nem
        env = self.ok("POST", "/api/appraisals/rob-sync", {"tool": "rob2", "outcome": "o1", "dry_run": False},
                      headers=[("If-Match", '"%s"' % d["etag"])])
        self.assertTrue(env["data"]["applied"])
        tab = self.ok("GET", "/api/table?dataset=" + H.O1)["data"]
        self.assertEqual(tab["header"][-1], "rob")
        row = [r for r in tab["rows"] if r["cells"][0] == "Aronson 1948"][0]
        self.assertEqual(row["cells"][-1], "high")
        prov = self.ok("GET", "/api/provenance?dataset=" + H.O1)["data"]["provenance"]
        cell = [c for c in prov["cells"] if c["row_uid"] == row["row_uid"] and c["field"] == "rob"][0]
        self.assertEqual(cell["method"], "calculated")
        self.assertEqual(cell["source"]["locator"], "single")
        self.assertEqual(prov["table_sha256"], tab["etag"])
        act = self.activity_text()
        self.assertIn("appraisal.rob_sync", act)
        # ismételt előnézet: nincs több változás
        again = self.ok("POST", "/api/appraisals/rob-sync", {"tool": "rob2", "outcome": "o1", "dry_run": True})["data"]
        self.assertEqual(again["proposal"]["changes"], [])
        self.assertTrue(again["column_exists"])


# ---------------------------------------------------------------------------- a valódi motor-modullal
def real_engine_functions():
    """A motor értékelő függvényei: a homlokzatról (ha már ott vannak), különben a ``metaelemzes.appraisal`` modulból
    (a homlokzat leendő kötése — a munkapad maga sosem importálja a belső modult, 6.8). None, ha egyik sincs."""
    names = {"instruments_list": "instruments_list", "instrument_get": "instrument_get", "appraisal_check": "check",
             "appraisal_validate": "validate", "appraisal_consensus": "appraisal_consensus", "rob_summary": "rob_summary",
             "rob_sync_proposal": "rob_sync_proposal", "instrument_route": "instrument_route"}
    if all(callable(getattr(api, n, None)) for n in names):
        return {}
    try:
        import importlib
        mod = importlib.import_module("metaelemzes.appraisal")
    except ImportError:
        return None
    out = {}
    for n, m in names.items():
        if callable(getattr(api, n, None)):
            continue
        fn = getattr(mod, n, None) or getattr(mod, m, None)
        if not callable(fn):
            return None
        out[n] = fn
    return out


class RealEngineTests(unittest.TestCase):
    """A végpontok a motor VALÓDI értékelő-implementációjával (natív eszköz-definíciók, check, κ, forgalmi lámpa)."""

    @classmethod
    def setUpClass(cls):
        funcs = real_engine_functions()
        if funcs is None:
            raise unittest.SkipTest("a motor értékelő modulja (metaelemzes.appraisal) még nincs meg")
        from unittest import mock
        cls.stack = contextlib.ExitStack()
        for n, fn in funcs.items():
            cls.stack.enter_context(mock.patch.object(api, n, fn, create=True))
        cls.tmp = H.tmpdir("ma_v1_apprR_")
        cls.proj, cls.home = H.make_project(cls.tmp)
        cls.srv = H.Srv(cls.proj, cls.home, cls.tmp, name="apprR")

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.stack.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def full(self, rater):
        inst = self.srv.ok("GET", "/api/instruments/rob2")["data"]
        view = self.srv.ok("GET", path_of("R1", "rob2", "o1", rater))["data"]
        doc = view["doc"]
        doc["scope"] = doc.get("scope") or "assignment"
        for it in inst["items"]:
            if doc["scope"] in (it.get("scope") or it.get("scopes") or ["all"]) or "all" in (it.get("scope") or []):
                doc["answers"][it["key"]] = {"value": "no" if it.get("polarity") == "reverse" else "yes"}
        return inst, view, doc

    def test_real_engine_flow(self):
        lst = self.srv.ok("GET", "/api/instruments")["data"]
        self.assertTrue({"rob2", "probast-ai", "tripod-ai", "amstar2"} <= {x["key"] for x in lst["instruments"]})
        inst, view, doc = self.full("SzK")
        self.assertFalse(view["exists"])
        self.assertEqual(view["check"]["answered"], 0)
        env = self.srv.ok("PUT", path_of("R1", "rob2", "o1", "SzK"), {"doc": doc})
        chk = env["data"]["check"]
        self.assertTrue(chk["complete"], chk.get("missing"))
        self.assertEqual({d["algorithm"] for d in chk["domains"]}, {"conservative"})
        # felülbírálás indoklás nélkül → 422 (X017), indoklással → napló-döntés
        saved = env["data"]["doc"]
        saved["domain_judgements"] = [{"domain": "1", "pass": None, "judgement": "high", "rationale": None,
                                       "override_reason": None, "decision_id": None}]
        st, e = self.srv.err("PUT", path_of("R1", "rob2", "o1", "SzK"), {"doc": saved},
                             headers=[("If-Match", env["_etag"])])
        self.assertEqual(st, 422)
        self.assertIn("X017", e["message"])
        saved["domain_judgements"][0]["override_reason"] = "teszt: a randomizáció leírása hiányos"
        env = self.srv.ok("PUT", path_of("R1", "rob2", "o1", "SzK"), {"doc": saved}, headers=[("If-Match", env["_etag"])])
        self.assertIsInstance(env["data"]["doc"]["domain_judgements"][0]["decision_id"], int)
        # második értékelő → κ a motorból
        _inst, _v, doc_kp = self.full("KP")
        self.srv.ok("PUT", path_of("R1", "rob2", "o1", "KP"), {"doc": doc_kp})
        cons = self.srv.ok("GET", "/api/appraisals/consensus/R1/rob2?target=o1")["data"]
        self.assertTrue(cons["ready"])
        self.assertIn("kappa_text", cons["agreement"])
        self.assertEqual(cons["agreement"]["disagree"], 0)
        # forgalmi lámpa és rob-szinkron (még nincs kész értékelés → üres javaslat is érvényes)
        summ = self.srv.ok("GET", "/api/appraisals/rob-summary?tool=rob2&outcome=o1")["data"]
        self.assertEqual(summ["schema"], "szk.rob-summary/v1")
        sync = self.srv.ok("POST", "/api/appraisals/rob-sync", {"tool": "rob2", "outcome": "o1", "dry_run": True})["data"]
        self.assertIn("changes", sync["proposal"])


if __name__ == "__main__":
    unittest.main()
