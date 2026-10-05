# -*- coding: utf-8 -*-
"""v1 kettős kinyerés és egyeztetés (ma_gui/routes/extraction_dual*.py; terv 2.4, 3.4, 3.5.5, 4.8, 4.9, 6.4 X009, 7.4;
11. fejezet 5. döntés) a VALÓDI munkapad-szerverrel (port 0; a szerver a saját válaszait is sémán ellenőrzi).

- Funkció-felismerés: a motor ``compare`` függvénye nélkül a ``POST /api/compare`` és ``/api/reconcile`` 424
  CAPABILITY_MISSING magyar üzenettel; az A/B import–export (fájlcsere) ettől függetlenül működik.
- A motor helyén TESZT-CSONK (tests/gui/_extraction_engine_stub.py): eltérések, súgók, hatás, κ — a munkapad csak
  hozzárendeli a döntéseket, számot nem számol.
- Import (base64 és beérkezett mappa), bájtra azonos mentés, felülírás csak jelöléssel, PHI-szkenner értékek nélkül,
  út-bejárás tiltva; export és sablon a második kinyerőnek.
- Egyeztetés: indoklás kötelező, ismeretlen / formátum-eltérés / sorszintű „saját érték” → 422, If-Match → 409,
  dry-run, feloldatlan eltérésnél a CSV-írás 409 GATE_BLOCKED (X009), elavult döntés a tábla cseréje után, a
  konszenzus-CSV + eredet-oldalfájl (reconciled) atomikusan, a kimenet adattáblájába írás outcome_if_match-csel.
- Adatvédelem: az activity-naplóba és a projektnaplóba cellaérték, kulcsérték és indoklás-szöveg nem kerül
  (a projektnaplóba csak mezőnév + választás + indoklás); C osztályban a fájlok a _privat/kettos/ alá kerülnek.
- Ha a valódi motor (``metaelemzes.api.compare``) már elérhető, egy próba a csonk nélkül is fut (különben kihagyva)."""
import base64
import contextlib
import json
import os
import shutil
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_routes_harness as H  # noqa: E402
import _extraction_engine_stub as STUB  # noqa: E402
from metaelemzes import api  # noqa: E402
from ma_gui import activity  # noqa: E402
from ma_gui.routes import extraction_dual_common as C  # noqa: E402

SECRET = "Titkos-indoklás-Smith"
A_CSV = ("vizsgálat;esemény1;n1;esemény2;n2;rob\n"
         "Aronson 1948;4;123;11;139;low\n"
         "Ferguson & Simes 1949;6;306;29;303;some\n"
         "Rosenthal et al 1960;3;231;11;220;low\n"
         "Hart & Sutherland 1977;62;13598;248;12867;high\n"
         "Csak-A 2001;1;10;2;20;low\n")
B_CSV = ("vizsgálat;esemény1;n1;esemény2;n2;rob\n"
         "Aronson 1948;4;123.0;11;139;low\n"
         "Ferguson & Simes 1949;60;306;29;303;some\n"
         "Rosenthal et al 1960;11;220;3;231;low\n"
         "Hart & Sutherland 1977;62;13598;248;12867;some\n"
         "Csak-B 2002;7;70;8;80;low\n")
# ezek a cellaértékek és kulcsok sosem kerülhetnek naplóba
VALUES = ("13598", "12867", "Ferguson", "Rosenthal", "Csak-A", "Csak-B", SECRET)
ENGINE_NAMES = tuple(n for k in C.ENGINE for n in (C.ENGINE[k][0],) + tuple(C.ENGINE[k][1]))


def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class _Base(unittest.TestCase):
    NAME = "kx"
    DATA_CLASS = "A"

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_v1_dual_")
        cls.proj, cls.home = H.make_project(cls.tmp, data_class=cls.DATA_CLASS)
        cls.prepare_project()
        cls.srv = H.Srv(cls.proj, cls.home, cls.tmp, name=cls.NAME)

    @classmethod
    def prepare_project(cls):
        pass

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.eng = STUB.StubEngine()

    def tearDown(self):
        self.stack.close()

    # -- motor
    def absent(self, names=ENGINE_NAMES):
        """A motor-függvények (és álneveik) biztosan hiányoznak (a párhuzamos motor-munka már beírhatta őket)."""
        for n in names:
            if hasattr(api, n):
                self.stack.enter_context(mock.patch.object(api, n, None))

    def engine(self, names=STUB.NAMES):
        self.absent()
        for p in STUB.patch(self.eng, names):
            self.stack.enter_context(p)

    # -- HTTP
    def ok(self, method, path, body=None, etag=None):
        return self.srv.ok(method, path, body, headers=[("If-Match", etag)] if etag else [])

    def err(self, method, path, body=None, etag=None):
        return self.srv.err(method, path, body, headers=[("If-Match", etag)] if etag else [])

    def path(self, rel):
        return os.path.join(self.proj, *rel.split("/"))

    def read(self, rel, mode="r"):
        with open(self.path(rel), mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
            return fh.read()

    def records(self):
        return activity.read_records(os.path.join(self.proj, activity.LOG_RELPATH))

    def activity_text(self):
        p = os.path.join(self.proj, activity.LOG_RELPATH)
        if not os.path.isfile(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def import_both(self, outcome="o1", a=A_CSV, b=B_CSV, replace=False):
        ea = self.ok("POST", "/api/kettos/import", {"outcome": outcome, "side": "A", "rater": "SzK",
                                                    "content_b64": b64(a), "replace": replace})
        eb = self.ok("POST", "/api/kettos/import", {"outcome": outcome, "side": "B", "rater": "KP",
                                                    "content_b64": b64(b), "replace": replace})
        return ea, eb

    def reset(self, outcome="o1"):
        d = self.path("03_adatok/kettos")
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.startswith(outcome + "."):
                    os.remove(os.path.join(d, f))

    def decide_all(self, view, chosen="a", reason="Table 2 lábjegyzete"):
        return [{"key": it["key"], "field": it["field"], "chosen": chosen, "reason": reason + " — " + SECRET}
                for it in view["items"] if it["needs_decision"]]


# =============================================================================== funkció-felismerés
class FeatureDetectionTests(_Base):
    NAME = "kfd"

    def test_missing_engine_gives_424_but_file_exchange_works(self):
        self.absent()
        lst = self.ok("GET", "/api/kettos")["data"]
        self.assertFalse(lst["engine"]["available"])
        self.assertEqual(lst["engine"]["missing"], ["compare"])
        self.assertEqual([o["id"] for o in lst["outcomes"]], ["o1", "o2"])
        self.import_both()
        st, e = self.err("POST", "/api/compare", {"outcome": "o1"})
        self.assertEqual((st, e["code"]), (424, "CAPABILITY_MISSING"))
        self.assertIn("kettős kinyerés", e["message"])
        self.assertIn("metaelemzes.api.compare", e["message"])
        self.assertEqual(e["details"]["engine_functions"], ["compare"])
        st, e = self.err("POST", "/api/reconcile", {"outcome": "o1", "decisions": []})
        self.assertEqual((st, e["code"]), (424, "CAPABILITY_MISSING"))
        ex = self.ok("POST", "/api/kettos/export", {"outcome": "o1", "side": "B"})["data"]
        self.assertEqual(base64.b64decode(ex["content_b64"]).decode("utf-8"), B_CSV)

    def test_alias_names_are_detected(self):
        self.absent()
        self.stack.enter_context(mock.patch.object(api, "kettos_compare", self.eng.compare, create=True))
        self.import_both()
        lst = self.ok("GET", "/api/kettos")["data"]
        self.assertTrue(lst["engine"]["available"])
        self.assertEqual(lst["engine"]["functions"]["compare"], "compare")   # a csonk metódusának neve
        self.ok("POST", "/api/compare", {"outcome": "o1"})

    def test_engine_output_violating_contract_is_internal_error_without_values(self):
        self.absent()

        def bad(a, b, key=None, tolerance=None, measure=None):
            out = self.eng.compare(a, b, key=key, tolerance=tolerance, measure=measure)
            out["disagreements"][0]["kind"] = "nonsense"
            return out
        self.stack.enter_context(mock.patch.object(api, "compare", bad, create=True))
        self.import_both()
        st, e = self.err("POST", "/api/compare", {"outcome": "o1"})
        self.assertEqual((st, e["code"]), (500, "INTERNAL"))      # a szerver a 500-as üzenetet általánosra cseréli
        blob = json.dumps(e, ensure_ascii=False)
        for v in ("Ferguson", "13598"):
            self.assertNotIn(v, blob)

    def test_engine_value_error_is_422(self):
        self.engine()
        self.import_both()
        st, e = self.err("POST", "/api/compare", {"outcome": "o1", "key": ["nincs_ilyen"]})
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))


# =============================================================================== import / export (5. döntés)
class ExchangeTests(_Base):
    NAME = "kex"

    def setUp(self):
        super().setUp()
        self.reset()

    def test_import_is_byte_identical_and_logged_without_values(self):
        ea, eb = self.import_both()
        self.assertEqual(ea["data"]["state"], "new")
        self.assertEqual(self.read("03_adatok/kettos/o1.A.csv", "rb"), A_CSV.encode("utf-8"))
        self.assertEqual(self.read("03_adatok/kettos/o1.B.csv", "rb"), B_CSV.encode("utf-8"))
        doc = json.loads(self.read("03_adatok/kettos/o1.consensus.json"))
        H.check_contract(self, doc, "szk.ma.consensus/v1")
        self.assertEqual(doc["raters"], {"a": "SzK", "b": "KP"})
        self.assertNotIn("key", doc)                  # ismeretlen kulcs: nem null (a szerződés nem üres listát kér)
        recs = [r for r in self.records() if r["action"] == "kettos.import"]
        self.assertGreaterEqual(len(recs), 2)
        self.assertEqual(recs[-1]["details"]["n_rows"], 5)
        text = self.activity_text()
        for v in VALUES:
            self.assertNotIn(v, text)
        lst = self.ok("GET", "/api/kettos")["data"]["outcomes"][0]
        self.assertTrue(lst["a"]["exists"] and lst["b"]["exists"])
        self.assertEqual(lst["consensus"]["raters"], {"a": "SzK", "b": "KP"})

    def test_same_content_is_noop_different_needs_replace(self):
        self.import_both()
        again = self.ok("POST", "/api/kettos/import", {"outcome": "o1", "side": "A", "content_b64": b64(A_CSV)})
        self.assertEqual(again["data"]["state"], "same")
        changed = A_CSV.replace("Aronson 1948;4;", "Aronson 1948;5;")
        st, e = self.err("POST", "/api/kettos/import", {"outcome": "o1", "side": "A", "content_b64": b64(changed)})
        self.assertEqual((st, e["code"]), (409, "CONFLICT"))
        self.assertIn("felülírás", e["message"])
        rep = self.ok("POST", "/api/kettos/import", {"outcome": "o1", "side": "A", "content_b64": b64(changed),
                                                     "replace": True})
        self.assertEqual(rep["data"]["state"], "replaced")
        self.assertEqual(self.read("03_adatok/kettos/o1.A.csv"), changed)

    def test_import_from_inbox_and_path_traversal_is_refused(self):
        inbox = self.path("03_adatok/kettos/beerkezett")
        os.makedirs(inbox, exist_ok=True)
        with open(os.path.join(inbox, "o1.B.csv"), "w", encoding="utf-8") as fh:
            fh.write(B_CSV)
        lst = self.ok("GET", "/api/kettos")["data"]
        hit = [f for f in lst["inbox"] if f["name"] == "o1.B.csv"]
        self.assertEqual(len(hit), 1)
        self.assertEqual((hit[0]["side_guess"], hit[0]["outcome_guess"]), ("B", "o1"))
        env = self.ok("POST", "/api/kettos/import", {"outcome": "o1", "side": "B",
                                                     "path": "03_adatok/kettos/beerkezett/o1.B.csv"})
        self.assertEqual(env["data"]["source"], "03_adatok/kettos/beerkezett/o1.B.csv")
        self.assertEqual(self.read("03_adatok/kettos/o1.B.csv"), B_CSV)
        rec = [r for r in self.records() if r["action"] == "kettos.import"][-1]
        self.assertIn("03_adatok/kettos/beerkezett/o1.B.csv", rec["inputs"])
        for bad in ("03_adatok/o1.csv", "03_adatok/kettos/beerkezett/../../o1.csv", "../x.csv", "/etc/passwd",
                    "03_adatok\\kettos\\beerkezett\\o1.B.csv", "C:/x.csv"):
            st, e = self.err("POST", "/api/kettos/import", {"outcome": "o1", "side": "B", "path": bad})
            self.assertIn(st, (400, 403), bad)

    def test_import_validation(self):
        st, e = self.err("POST", "/api/kettos/import", {"outcome": "o1", "side": "A"})
        self.assertEqual(st, 400)
        st, e = self.err("POST", "/api/kettos/import", {"outcome": "o1", "side": "C", "content_b64": b64(A_CSV)})
        self.assertEqual(st, 400)
        st, e = self.err("POST", "/api/kettos/import", {"outcome": "o1", "side": "A", "content_b64": "!!nem-base64"})
        self.assertEqual(st, 400)
        st, e = self.err("POST", "/api/kettos/import", {"outcome": "o1", "side": "A", "content_b64": b64("  \n")})
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        st, e = self.err("POST", "/api/kettos/import", {"outcome": "nincs", "side": "A", "content_b64": b64(A_CSV)})
        self.assertEqual((st, e["code"]), (404, "NOT_FOUND"))
        st, e = self.err("POST", "/api/kettos/import", {"outcome": "../o1", "side": "A", "content_b64": b64(A_CSV)})
        self.assertEqual(st, 400)
        st, e = self.err("POST", "/api/kettos/import", {"outcome": "o1", "side": "A", "rater": "1rossz monogram",
                                                       "content_b64": b64(A_CSV)})
        self.assertEqual(st, 400)

    def test_phi_in_import_is_refused_without_echoing_the_value(self):
        taj = H.taj()
        bad = A_CSV.replace("Aronson 1948;4;123", "Aronson 1948;4;%s" % taj)
        st, e = self.err("POST", "/api/kettos/import", {"outcome": "o1", "side": "A", "content_b64": b64(bad)})
        self.assertEqual((st, e["code"]), (403, "FORBIDDEN"))
        self.assertNotIn(taj, json.dumps(e))
        self.assertFalse(os.path.exists(self.path("03_adatok/kettos/o1.A.csv")))
        self.assertNotIn(taj, self.activity_text())

    def test_export_sides_and_template(self):
        self.import_both()
        st, e = self.err("POST", "/api/kettos/export", {"outcome": "o1", "side": "consensus"})
        self.assertEqual((st, e["code"]), (404, "NOT_FOUND"))
        ex = self.ok("POST", "/api/kettos/export", {"outcome": "o1", "side": "A"})["data"]
        raw = base64.b64decode(ex["content_b64"])
        self.assertEqual(raw, A_CSV.encode("utf-8"))
        self.assertEqual(ex["filename"], "o1.A.csv")
        self.assertEqual(ex["bytes"], len(raw))
        tpl = self.ok("POST", "/api/kettos/export", {"outcome": "o1", "side": "A", "template": True})["data"]
        self.assertEqual(tpl["filename"], "o1.B.sablon.csv")
        lines = base64.b64decode(tpl["content_b64"]).decode("utf-8").splitlines()
        self.assertEqual(lines[0], "vizsgálat;esemény1;n1;esemény2;n2;rob")      # row_uid nélkül (determinisztikus)
        again = self.ok("POST", "/api/kettos/export", {"outcome": "o1", "side": "A", "template": True})["data"]
        self.assertEqual(again["content_b64"], tpl["content_b64"], "a sablon determinisztikus")
        # csak a kulcsoszlop (study) marad kitöltve: a második kinyerő a saját értékeit írja be
        self.assertTrue(lines[1].startswith("Aronson 1948;;;;;"), lines[1])
        self.assertEqual(tpl["rows"], 5)
        rec = [r for r in self.records() if r["action"] == "kettos.export"][-1]
        self.assertTrue(rec["details"]["template"])


# =============================================================================== összevetés + egyeztetés
class ReconcileTests(_Base):
    NAME = "krc"

    def setUp(self):
        super().setUp()
        self.reset()
        self.engine()
        self.import_both()

    def view(self, **body):
        body.setdefault("outcome", "o1")
        return self.ok("POST", "/api/compare", body)

    def test_compare_view_maps_engine_result(self):
        env = self.view()
        d = env["data"]
        H.check_contract(self, d["compare"], "szk.ma.compare-result/v1")
        self.assertEqual(d["key"], ["study"])
        kinds = sorted(it["kind"] for it in d["items"])
        self.assertEqual(kinds, ["category", "format_only", "only_a", "only_b", "value", "value", "value", "value",
                                 "value"])
        fmt = [it for it in d["items"] if it["kind"] == "format_only"][0]
        self.assertTrue(fmt["auto"])
        self.assertFalse(fmt["needs_decision"])
        rowa = [it for it in d["items"] if it["kind"] == "only_a"][0]
        self.assertEqual((rowa["level"], rowa["field"], rowa["key"]), ("row", "*", "Csak-A 2001"))
        self.assertTrue(rowa["row_uid_a"])
        x10 = [it for it in d["items"] if it["field"] == "e1" and it["key"].startswith("Ferguson")][0]
        self.assertEqual(x10["hint_code"], "x10")             # a súgó a motoré
        self.assertIn("impact", x10)
        self.assertEqual(d["progress"], {"total": 8, "decided": 0, "unresolved": 8, "auto": 1, "stale": 0,
                                         "orphans": 0})
        self.assertTrue(d["gate"]["blocked"])
        self.assertEqual(d["gate"]["code"], "X009")
        self.assertEqual(d["raters"], {"a": "SzK", "b": "KP"})
        self.assertEqual(d["a"]["n_rows"], 5)
        self.assertEqual(len(d["b"]["rows"]), 5)
        self.assertTrue(d["agreement_text"]["en"].startswith("Two extractors"))
        self.assertEqual(d["pairs_source"], "engine")
        self.assertEqual(len(d["pairs"]), 4)
        self.assertEqual(self.eng.calls[-1], ("compare", None, "RR"))      # a kimenet mértéke a motornak
        no_tables = self.view(tables=False)["data"]
        self.assertNotIn("a", no_tables)
        self.view(key=["study"])
        self.assertEqual(self.eng.calls[-1][1], ["study"])

    def test_reconcile_requires_reason_and_known_items(self):
        env = self.view()
        d, etag = env["data"], env["_etag"]
        it = [i for i in d["items"] if i["kind"] == "value"][0]
        base = {"outcome": "o1"}
        for dec, code in (({"key": it["key"], "field": it["field"], "chosen": "a", "reason": "   "}, "reason"),
                          ({"key": "nincs", "field": "e1", "chosen": "a", "reason": "x"}, "unknown"),
                          ({"key": it["key"], "field": it["field"], "chosen": "other", "reason": "x"}, "value")):
            st, e = self.err("POST", "/api/reconcile", dict(base, decisions=[dec]), etag=etag)
            self.assertEqual((st, e["code"]), (422, "VALIDATION"), code)
            self.assertNotIn(it["a"] if it["a"] and len(it["a"]) > 2 else "§§", e["message"])
        fmt = [i for i in d["items"] if i["kind"] == "format_only"][0]
        st, e = self.err("POST", "/api/reconcile", dict(base, decisions=[{"key": fmt["key"], "field": fmt["field"],
                                                                         "chosen": "b", "reason": "x"}]), etag=etag)
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        row = [i for i in d["items"] if i["kind"] == "only_b"][0]
        st, e = self.err("POST", "/api/reconcile", dict(base, decisions=[{"key": row["key"], "field": "*",
                                                                         "chosen": "other", "value": "1",
                                                                         "reason": "x"}]), etag=etag)
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        dup = {"key": it["key"], "field": it["field"], "chosen": "a", "reason": "x"}
        st, e = self.err("POST", "/api/reconcile", dict(base, decisions=[dup, dup]), etag=etag)
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        st, e = self.err("POST", "/api/reconcile", dict(base, decisions=[dict(dup, extra=1)]), etag=etag)
        self.assertEqual(st, 400)
        self.assertFalse(os.path.exists(self.path("03_adatok/kettos/o1.consensus.csv")))

    def test_if_match_dry_run_gate_and_consensus_csv(self):
        env = self.view()
        etag = env["_etag"]
        decs = self.decide_all(env["data"])
        # piszkozat: semmi sem íródik
        before = self.read("03_adatok/kettos/o1.consensus.json")
        dry = self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs, "write": "kettos",
                                                 "dry_run": True})["data"]
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["progress"]["unresolved"], 0)
        self.assertEqual(dry["written"]["csv"]["rows"], 5)                # Csak-B (chosen a) kimarad
        self.assertEqual(self.read("03_adatok/kettos/o1.consensus.json"), before)
        self.assertFalse(os.path.exists(self.path("03_adatok/kettos/o1.consensus.csv")))
        # If-Match nélkül / rossz etaggel: 409
        st, e = self.err("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs[:1]})
        self.assertEqual((st, e["code"]), (409, "CONFLICT"))
        st, e = self.err("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs[:1]}, etag='"rossz"')
        self.assertEqual((st, e["code"]), (409, "CONFLICT"))
        # részleges döntés + CSV-kérés → GATE_BLOCKED (X009), semmi sem íródik
        st, e = self.err("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs[:2], "write": "kettos"},
                         etag=etag)
        self.assertEqual((st, e["code"]), (409, "GATE_BLOCKED"))
        self.assertEqual(e["details"]["code"], "X009")
        self.assertEqual(e["details"]["unresolved"], 6)
        self.assertEqual(self.read("03_adatok/kettos/o1.consensus.json"), before)
        # részleges döntések mentése CSV nélkül
        part = self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs[:2]}, etag=etag)
        self.assertEqual(part["data"]["progress"]["decided"], 2)
        self.assertTrue(part["data"]["gate"]["blocked"])
        self.assertEqual(part["data"]["written"]["changed"], 2)
        doc = json.loads(self.read("03_adatok/kettos/o1.consensus.json"))
        H.check_contract(self, doc, "szk.ma.consensus/v1")
        self.assertEqual(doc["key"], ["study"])
        self.assertEqual(doc["decisions"][0]["actor"], "user")
        self.assertIn(SECRET, doc["decisions"][0]["reason"])
        # ugyanaz újra: nincs változás
        same = self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs[:2]}, etag=part["_etag"])
        self.assertEqual(same["data"]["written"]["changed"], 0)
        # a maradék + CSV
        full = self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs[2:], "write": "kettos"},
                       etag=same["_etag"])
        self.assertFalse(full["data"]["gate"]["blocked"])
        w = full["data"]["written"]["csv"]
        self.assertEqual(w["path"], "03_adatok/kettos/o1.consensus.csv")
        self.assertEqual(w["builder"], "server")
        csv_text = self.read(w["path"])
        lines = csv_text.splitlines()
        self.assertTrue(lines[0].startswith("vizsgálat;esemény1;n1;esemény2;n2;rob"))   # az A formátuma
        self.assertEqual(len(lines), 6)
        self.assertNotIn("Csak-B", csv_text)
        self.assertIn("Csak-A 2001", csv_text)
        prov = json.loads(self.read("03_adatok/kettos/o1.consensus.prov.json"))
        H.check_contract(self, prov, "szk.ma.provenance/v1")
        self.assertTrue(prov["cells"])
        self.assertTrue(all(c["method"] == "reconciled" for c in prov["cells"]))
        self.assertEqual({c["field"] for c in prov["cells"]}, {"e1", "e2", "n1", "n2", "rob"})
        doc = json.loads(self.read("03_adatok/kettos/o1.consensus.json"))
        self.assertEqual(doc["csv"]["path"], w["path"])
        self.assertEqual(doc["sources"]["a"]["path"], "03_adatok/kettos/o1.A.csv")
        # activity: cellaérték, kulcs és indoklás nélkül; a CSV a kimenetek között
        rec = [r for r in self.records() if r["action"] == "kettos.reconcile"][-1]
        self.assertIn("03_adatok/kettos/o1.consensus.csv", rec["outputs"])
        self.assertIn("03_adatok/kettos/o1.A.csv", rec["inputs"])
        text = self.activity_text()
        for v in VALUES:
            self.assertNotIn(v, text)
        # projektnapló: mezőnév + választás + indoklás, cellaérték és kulcs nélkül
        decs_log = api.project_list(self.proj, "decisions")
        mine = [x for x in decs_log if "Kettős kinyerés" in (x.get("decision") or "")]
        self.assertTrue(mine)
        blob = json.dumps(mine, ensure_ascii=False)
        for v in ("13598", "Ferguson", "Csak-A"):
            self.assertNotIn(v, blob)
        self.assertIn("az A változata", blob)
        # export: a konszenzus-CSV letölthető
        ex = self.ok("POST", "/api/kettos/export", {"outcome": "o1", "side": "consensus"})["data"]
        self.assertEqual(base64.b64decode(ex["content_b64"]).decode("utf-8"), csv_text)

    def test_choices_other_value_row_b_and_clear(self):
        env = self.view()
        d = env["data"]
        x10 = [i for i in d["items"] if i["field"] == "e1" and i["key"].startswith("Ferguson")][0]
        decs = [dict(x, chosen="b") if x["key"] == "Csak-B 2002" else x for x in self.decide_all(d)]
        decs = [dict(x, chosen="other", value="6") if (x["key"], x["field"]) == (x10["key"], "e1") else x
                for x in decs]
        rob = [i for i in d["items"] if i["kind"] == "category"][0]
        decs = [dict(x, chosen="b") if (x["key"], x["field"]) == (rob["key"], "rob") else x for x in decs]
        env2 = self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs, "write": "kettos"},
                       etag=env["_etag"])
        csv_text = self.read("03_adatok/kettos/o1.consensus.csv")
        self.assertIn("Csak-B 2002;7;70;8;80;low", csv_text)
        self.assertIn("Ferguson & Simes 1949;6;306", csv_text)
        self.assertIn("Hart & Sutherland 1977;62;13598;248;12867;some", csv_text)
        other = [x for x in env2["data"]["items"] if x["key"] == x10["key"] and x["field"] == "e1"][0]
        self.assertEqual(other["decision"]["chosen"], "other")
        self.assertEqual(other["decision"]["value"], "6")
        # visszavonás: a döntés törlődik, újra feloldatlan
        env3 = self.ok("POST", "/api/reconcile", {"outcome": "o1", "clear": [{"key": x10["key"], "field": "e1"}]},
                       etag=env2["_etag"])
        self.assertEqual(env3["data"]["progress"]["unresolved"], 1)
        self.assertEqual(env3["data"]["written"]["cleared"], 1)

    def test_stale_decision_after_table_replacement(self):
        env = self.view()
        decs = self.decide_all(env["data"])
        env2 = self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs}, etag=env["_etag"])
        self.assertEqual(env2["data"]["progress"]["unresolved"], 0)
        # B-ben a 10×-es érték javítása helyett más elírás → a régi döntés elavult
        newb = B_CSV.replace("Ferguson & Simes 1949;60;", "Ferguson & Simes 1949;61;")
        imp = self.ok("POST", "/api/kettos/import", {"outcome": "o1", "side": "B", "content_b64": b64(newb),
                                                     "replace": True})
        self.assertTrue(any("elavul" in w for w in imp["warnings"]))
        v = self.view()["data"]
        self.assertEqual(v["progress"]["stale"], 1)
        self.assertEqual(v["progress"]["unresolved"], 1)
        st_items = [i for i in v["items"] if i["stale"]]
        self.assertEqual(st_items[0]["field"], "e1")
        self.assertTrue(any("megváltozott" in w for w in self.view()["warnings"]))
        st, e = self.err("POST", "/api/reconcile", {"outcome": "o1", "write": "kettos"}, etag=v["consensus"]["etag"])
        self.assertEqual((st, e["code"]), (409, "GATE_BLOCKED"))

    def test_write_into_outcome_table_needs_its_etag(self):
        env = self.view()
        decs = self.decide_all(env["data"])
        st, e = self.err("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs, "write": "outcome"},
                         etag=env["_etag"])
        self.assertEqual((st, e["code"]), (409, "CONFLICT"))
        self.assertIn("outcome_if_match", e["message"])
        tag = self.ok("GET", "/api/table?dataset=03_adatok/o1.csv")["_etag"]
        out = self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs, "write": "outcome",
                                                 "outcome_if_match": tag}, etag=env["_etag"])
        self.assertEqual(out["data"]["written"]["csv"]["path"], "03_adatok/o1.csv")
        text = self.read("03_adatok/o1.csv")
        self.assertIn("Csak-A 2001", text)
        prov = json.loads(self.read("03_adatok/o1.prov.json"))
        H.check_contract(self, prov, "szk.ma.provenance/v1")
        self.assertTrue(any(c["method"] == "reconciled" for c in prov["cells"]))
        # a kimenet táblája a szokásos végponttal olvasható, a 'reconciled' eredet a rácsban jelölhető
        t = self.ok("GET", "/api/table?dataset=03_adatok/o1.csv")["data"]
        self.assertEqual(len(t["rows"]), 5)

    def test_engine_consensus_table_is_used_when_present(self):
        self.stack.close()
        self.stack = contextlib.ExitStack()
        self.engine(STUB.NAMES_FULL)
        env = self.view()
        decs = self.decide_all(env["data"])
        out = self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs, "write": "kettos"},
                      etag=env["_etag"])
        self.assertEqual(out["data"]["written"]["csv"]["builder"], "engine")
        self.assertIn(("consensus_table", ["study"]), self.eng.calls)
        prov = json.loads(self.read("03_adatok/kettos/o1.consensus.prov.json"))
        self.assertTrue(all(c["method"] == "reconciled" for c in prov["cells"]))

    def test_phi_in_reason_is_refused(self):
        env = self.view()
        it = [i for i in env["data"]["items"] if i["needs_decision"]][0]
        taj = H.taj("87654321")
        st, e = self.err("POST", "/api/reconcile", {"outcome": "o1", "decisions": [
            {"key": it["key"], "field": it["field"], "chosen": "a", "reason": "beteg TAJ %s" % taj}]}, etag=env["_etag"])
        self.assertEqual((st, e["code"]), (403, "FORBIDDEN"))
        self.assertNotIn(taj, json.dumps(e))

    def test_concurrent_change_of_a_during_reconcile_is_detected(self):
        env = self.view()
        decs = self.decide_all(env["data"])
        real = C.atomic_write

        def racing(app, items):
            with open(self.path("03_adatok/kettos/o1.A.csv"), "a", encoding="utf-8") as fh:
                fh.write("Közben 2003;1;2;3;4;low\n")
            return real(app, items)
        self.stack.enter_context(mock.patch.object(C, "atomic_write", racing))
        st, e = self.err("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs, "write": "kettos"},
                         etag=env["_etag"])
        self.assertEqual((st, e["code"]), (409, "CONFLICT"))
        self.assertFalse(os.path.exists(self.path("03_adatok/kettos/o1.consensus.csv")))


# =============================================================================== C osztály: _privat/kettos/
class PrivateClassTests(_Base):
    NAME = "kpc"
    DATA_CLASS = "C"

    @classmethod
    def prepare_project(cls):
        meta_p = os.path.join(cls.proj, "ma-projekt.json")
        with open(meta_p, encoding="utf-8") as fh:
            meta = json.load(fh)
        os.makedirs(os.path.join(cls.proj, "_privat"), exist_ok=True)
        shutil.move(os.path.join(cls.proj, "03_adatok", "o1.csv"), os.path.join(cls.proj, "_privat", "o1.csv"))
        meta["outcomes"][0]["data"] = "_privat/o1.csv"
        with open(meta_p, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False)

    def test_files_live_under_private_and_class_c_outcome_elsewhere_is_refused(self):
        self.engine()
        lst = self.ok("GET", "/api/kettos")["data"]
        o1 = [o for o in lst["outcomes"] if o["id"] == "o1"][0]
        self.assertEqual(o1["dir"], "_privat/kettos")
        self.import_both()
        self.assertTrue(os.path.isfile(self.path("_privat/kettos/o1.A.csv")))
        env = self.ok("POST", "/api/compare", {"outcome": "o1"})
        decs = self.decide_all(env["data"])
        out = self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs, "write": "kettos"},
                      etag=env["_etag"])
        self.assertEqual(out["data"]["written"]["csv"]["path"], "_privat/kettos/o1.consensus.csv")
        # o2 adattáblája nem a _privat/ alatt van: C osztályban oda nem írható
        st, e = self.err("POST", "/api/kettos/import", {"outcome": "o2", "side": "A", "content_b64": b64(A_CSV)})
        self.assertEqual((st, e["code"]), (403, "FORBIDDEN"))


# =============================================================================== a motor kettos-modulja (homlokzat előtt)
try:
    from metaelemzes import kettos as KET  # noqa: E402 — a párhuzamos motor-munka v1 modulja
except ImportError:                         # pragma: no cover
    KET = None


class ModuleEngineTests(_Base):
    """A motor v1 ``metaelemzes/kettos.py`` modulja a homlokzat (``metaelemzes.api``) helyén — amíg az integrátor be
    nem köti a homlokzatba (compare, consensus_table, agreement_report). Ha a modul hiányzik: kihagyva."""
    NAME = "kme"

    def setUp(self):
        super().setUp()
        if KET is None or not callable(getattr(KET, "compare", None)):
            self.skipTest("a metaelemzes.kettos modul (motor v1) még nincs meg")
        self.absent()
        for n in ("compare", "consensus_table", "agreement_report"):
            if callable(getattr(KET, n, None)):
                self.stack.enter_context(mock.patch.object(api, n, getattr(KET, n), create=True))
        self.reset()
        self.import_both()

    def test_full_flow_with_module_engine(self):
        env = self.ok("POST", "/api/compare", {"outcome": "o1"})
        d = env["data"]
        H.check_contract(self, d["compare"], "szk.ma.compare-result/v1")
        kinds = {it["kind"] for it in d["items"]}
        self.assertTrue({"value", "format_only", "category", "only_a", "only_b"} <= kinds, kinds)
        x10 = [it for it in d["items"] if it["field"] == "e1" and it["key"].startswith("Ferguson")][0]
        self.assertTrue(x10["hint"] and x10["hint_code"], "a motor súgója")
        self.assertTrue(x10["impact"] and x10["impact"].get("text"), "a motor hatás-szövege")
        self.assertTrue(d["agreement_text"] and d["agreement_text"]["hu"], "Methods-szöveg a motortól")
        self.assertTrue(d["compare"]["summary"].get("agreement_pct_text"))
        decs = self.decide_all(d)
        out = self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": decs, "write": "kettos"},
                      etag=env["_etag"])
        self.assertFalse(out["data"]["gate"]["blocked"])
        w = out["data"]["written"]["csv"]
        self.assertEqual(w["builder"], "engine")
        text = self.read(w["path"])
        self.assertTrue(text.startswith("vizsgálat;esemény1"))
        self.assertIn("Csak-A 2001", text)
        self.assertNotIn("Csak-B 2002", text)
        prov = json.loads(self.read("03_adatok/kettos/o1.consensus.prov.json"))
        H.check_contract(self, prov, "szk.ma.provenance/v1")
        self.assertTrue(prov["cells"] and all(c["method"] == "reconciled" for c in prov["cells"]))
        for v in VALUES:
            self.assertNotIn(v, self.activity_text())

    def test_engine_x009_reads_the_server_written_consensus(self):
        """A motor X009-szabálya (kettos.x009_findings: S08 PASS előtt) a munkapad által írt konszenzus-fájlt olvassa:
        döntések előtt hiba, minden döntés után tiszta."""
        if not callable(getattr(KET, "x009_findings", None)):
            self.skipTest("a motor X009-függvénye még nincs meg")
        env = self.ok("POST", "/api/compare", {"outcome": "o1"})

        def n_errors():
            out = KET.x009_findings(self.proj)
            errs = out[0] if isinstance(out, tuple) or (isinstance(out, list) and out and isinstance(out[0], list)) else out
            return len([f for f in errs if isinstance(f, dict) and f.get("outcome") == "o1"])
        self.assertEqual(n_errors(), 1)
        self.ok("POST", "/api/reconcile", {"outcome": "o1", "decisions": self.decide_all(env["data"])}, etag=env["_etag"])
        self.assertEqual(n_errors(), 0)


# =============================================================================== valódi motor (ha már elérhető)
class RealEngineTests(_Base):
    NAME = "kre"

    def test_real_engine_compare_if_available(self):
        if C.engine_fn("compare") is None:
            self.skipTest("a metaelemzes.api kettős kinyerés-függvénye (compare) még nincs a motorban")
        self.reset()
        self.import_both()
        d = self.ok("POST", "/api/compare", {"outcome": "o1", "key": ["study"]})["data"]
        H.check_contract(self, d["compare"], "szk.ma.compare-result/v1")
        self.assertTrue(d["items"])
        self.assertIn("X009", d["gate"]["code"])


if __name__ == "__main__":
    unittest.main()
