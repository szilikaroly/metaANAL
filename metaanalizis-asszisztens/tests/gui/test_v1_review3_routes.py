# -*- coding: utf-8 -*-
"""Regressziós tesztek a v1 harmadik független átnézésének végpont-megállapításaihoz (valódi motor, élő szerver).

- UX-1 / UX-2: a GET /api/appraisals lista-elemének ``target`` mezője a cél-KULCS (szöveg) — a dokumentum
  cél-objektuma ``target_obj`` néven (a felület szűrése és a PROBAST+AI táblázat ezen múlt).
- SEC-1: AI-vázlatot csak a /approve végpont hagyhat jóvá; a PUT és az import nem veszi át a kliens approved_by-ját,
  és a jóváhagyott vázlat tartalmának módosítása visszavonja a jóváhagyást.
- SEC-2: C osztályú projektben AI-vázlat nem menthető, nem importálható és nem hagyható jóvá.
- SEC-3: az import ugyanazokat a tartalmi szabályokat alkalmazza, mint a PUT (konszenzus két független emberi
  értékelés után; 'kész' csak teljes értékelésre; X017-indoklás; az indokolt felülbírálás naplózva).
- SEC-5: a felülbírálás napló-szövegében nincs értékelő-név."""
import copy
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, "ui")):
    if p not in sys.path:
        sys.path.insert(0, p)

import test_routes_harness as H  # noqa: E402
import e2e_v1_server as E  # noqa: E402
from metaelemzes import api  # noqa: E402

STUDIES = {"schema": "szk.ma.studies/v1", "studies": [
    {"study_id": "S%d" % i, "label": "Teszt %d" % i, "design": "rct_parallel", "outcomes": ["o1"]} for i in range(1, 9)]}


def _write(proj, rel, doc):
    path = os.path.join(proj, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)


def ai_doc(proj, unit="S1"):
    """AI-vázlat a 6. döntés szerint (tételenként indoklás + idézet) — az e2e segéd mintája, a beérkezett mappából
    kivéve."""
    out = E.aidraft(proj, "rob2", unit, "o1", "assignment")
    path = os.path.join(proj, out["path"])
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    os.remove(path)
    doc["target"]["outcome"] = "o1"
    return doc


def human_doc(unit, rater, judgement="low", status="complete"):
    doc, imp = E._complete_doc("rob2", unit, "o1", rater, judgement, "assignment")
    doc["status"] = status
    return doc, imp


def path_of(unit, rater, tool="rob2", target="o1"):
    return "/api/appraisals/%s/%s?rater=%s%s" % (unit, tool, rater, "&target=" + target if target else "")


class _Base(unittest.TestCase):
    data_class = "A"

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_r3_routes_")
        cls.proj, cls.home = H.make_project(cls.tmp, data_class=cls.data_class)
        _write(cls.proj, "03_adatok/studies.json", STUDIES)
        cls.srv = H.Srv(cls.proj, cls.home, cls.tmp, name="r3")

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def file(self, rel):
        path = os.path.join(self.proj, *rel.split("/"))
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def journal_texts(self):
        env = self.srv.ok("GET", "/api/log/decision?limit=200")
        return [e.get("decision") or "" for e in env["data"].get("items") or []]


class ApprovalSpoofTests(_Base):
    """SEC-1."""

    def test_put_ignores_client_approval(self):
        doc = ai_doc(self.proj, "S1")
        spoof = copy.deepcopy(doc)
        spoof.update(approved_by="SzK", approved_at="2026-10-05T00:00:00Z", approval_decision_id=999, status="complete")
        res = api.appraisal_check(spoof)
        spoof["overall"] = {"judgement": (res.get("overall") or {}).get("implied"), "rationale": "x"}
        st, e = self.srv.err("PUT", path_of("S1", "ai"), {"doc": spoof})
        self.assertEqual(st, 422, e)
        self.assertIn("jóváhagyás", e["message"])
        self.assertIsNone(self.file("04_torzitas_kockazat/appraisals/S1.rob2.o1.ai.json"))
        # vázlatként menthető — de a kliens jóváhagyás-mezői nem kerülnek bele
        spoof["status"] = "draft"
        env = self.srv.ok("PUT", path_of("S1", "ai"), {"doc": spoof})
        saved = self.file("04_torzitas_kockazat/appraisals/S1.rob2.o1.ai.json")
        self.assertIsNone(saved["approved_by"])
        self.assertIsNone(saved["approved_at"])
        self.assertIsNone(saved.get("approval_decision_id"))
        self.assertEqual(saved["status"], "draft")
        summ = api.project_rob_summary(self.proj, "o1", tool="rob2")
        self.assertNotIn("ai_approved", json.dumps(summ))
        self.assertTrue(env["_etag"])

    def test_import_strips_approval(self):
        doc = ai_doc(self.proj, "S2")
        spoof = copy.deepcopy(doc)
        spoof.update(approved_by="SzK", approved_at="2026-10-05T00:00:00Z", status="complete")
        env = self.srv.ok("POST", "/api/appraisals/import", {"doc": spoof})
        self.assertTrue(any("jóváhagyás" in w for w in env["warnings"]), env["warnings"])
        saved = self.file("04_torzitas_kockazat/appraisals/S2.rob2.o1.ai.json")
        self.assertIsNone(saved["approved_by"])
        self.assertEqual(saved["status"], "draft")

    def test_edit_after_approve_revokes(self):
        doc = ai_doc(self.proj, "S3")
        env = self.srv.ok("PUT", path_of("S3", "ai"), {"doc": doc})
        env = self.srv.ok("POST", "/api/appraisals/S3/rob2/approve?rater=ai&target=o1", {"approver": "SzK"},
                          headers=[("If-Match", env["_etag"])])
        approved = env["data"]["doc"]
        self.assertEqual(approved["approved_by"], "SzK")
        self.assertIsInstance(approved.get("approval_decision_id"), int)
        # változatlan tartalom újramentése: a jóváhagyás marad
        env = self.srv.ok("PUT", path_of("S3", "ai"), {"doc": approved}, headers=[("If-Match", env["_etag"])])
        self.assertEqual(env["data"]["doc"]["approved_by"], "SzK")
        # válasz-módosítás: a jóváhagyás érvényét veszti
        changed = copy.deepcopy(env["data"]["doc"])
        changed["answers"]["1.1"]["value"] = "no"
        env = self.srv.ok("PUT", path_of("S3", "ai"), {"doc": changed}, headers=[("If-Match", env["_etag"])])
        saved = self.file("04_torzitas_kockazat/appraisals/S3.rob2.o1.ai.json")
        self.assertIsNone(saved["approved_by"])
        self.assertIsNone(saved.get("approval_decision_id"))
        self.assertEqual(saved["status"], "draft")
        self.assertTrue(any("érvényét vesztette" in w for w in env["warnings"]), env["warnings"])
        # az eredet sem „mosható át” emberire
        laundered = copy.deepcopy(env["data"]["doc"])
        laundered["origin"] = "human"
        st, e = self.srv.err("PUT", path_of("S3", "ai"), {"doc": laundered}, headers=[("If-Match", env["_etag"])])
        self.assertEqual(st, 422)


class ImportRulesTests(_Base):
    """SEC-3 (+ SEC-5: napló-szöveg értékelő-név nélkül)."""

    def test_consensus_needs_two_human_raters(self):
        cons, _imp = human_doc("S4", "AB", status="consensus")
        cons["second_assessor"] = "CD"
        st, e = self.srv.err("POST", "/api/appraisals/import", {"doc": cons})
        self.assertEqual(st, 422, e)
        self.assertIn("Konszenzus", e["message"])
        self.assertIsNone(self.file("04_torzitas_kockazat/appraisals/S4.rob2.o1.consensus.json"))
        # ugyanabban a csomagban érkező két emberi értékelés elég
        a, _ = human_doc("S4", "AB")
        b, _ = human_doc("S4", "CD")
        env = self.srv.ok("POST", "/api/appraisals/import",
                          {"doc": {"schema": "szk.appraisal-bundle/v1", "items": [cons, a, b]}})
        self.assertEqual(len(env["data"]["imported"]), 3)

    def test_incomplete_complete_rejected(self):
        doc, _ = human_doc("S5", "KP")
        doc["answers"] = {"1.1": {"value": "yes"}}
        st, e = self.srv.err("POST", "/api/appraisals/import", {"doc": doc})
        self.assertEqual(st, 422)
        self.assertIn("nem teljes", e["message"])
        self.assertIsNone(self.file("04_torzitas_kockazat/appraisals/S5.rob2.o1.KP.json"))

    def test_override_needs_reason_and_is_logged(self):
        doc, imp = human_doc("S6", "Kovacs_Bela")
        other = "high" if imp != "high" else "low"
        doc["overall"] = {"judgement": other, "rationale": "x", "override_reason": None, "decision_id": None}
        st, e = self.srv.err("POST", "/api/appraisals/import", {"doc": doc})
        self.assertEqual(st, 422)
        self.assertIn("X017", e["message"])
        doc["overall"]["override_reason"] = "teszt: a közlemény szerint a besorolás nem volt rejtett"
        self.srv.ok("POST", "/api/appraisals/import", {"doc": doc})
        saved = self.file("04_torzitas_kockazat/appraisals/S6.rob2.o1.Kovacs_Bela.json")
        self.assertIsInstance(saved["overall"]["decision_id"], int)
        texts = [t for t in self.journal_texts() if "felülbírálása" in t]
        self.assertTrue(texts)
        self.assertFalse([t for t in texts if "Kovacs_Bela" in t], texts)


class ClassCTests(_Base):
    """SEC-2."""
    data_class = "C"

    def test_ai_draft_blocked_everywhere(self):
        doc = ai_doc(self.proj, "S1")
        st, e = self.srv.err("PUT", path_of("S1", "ai"), {"doc": doc})
        self.assertEqual(st, 422, e)
        self.assertIn("C osztály", e["message"])
        st, e = self.srv.err("POST", "/api/appraisals/import", {"doc": doc})
        self.assertEqual(st, 422, e)
        self.assertIn("C osztály", e["message"])
        self.assertIsNone(self.file("04_torzitas_kockazat/appraisals/S1.rob2.o1.ai.json"))
        # egy (kézzel bemásolt) meglévő vázlat sem hagyható jóvá
        _write(self.proj, "04_torzitas_kockazat/appraisals/S1.rob2.o1.ai.json", doc)
        env = self.srv.ok("GET", path_of("S1", "ai"))
        st, e = self.srv.err("POST", "/api/appraisals/S1/rob2/approve?rater=ai&target=o1", {"approver": "SzK"},
                             headers=[("If-Match", env["_etag"])])
        self.assertEqual(st, 422, e)
        self.assertIsNone(self.file("04_torzitas_kockazat/appraisals/S1.rob2.o1.ai.json")["approved_by"])


class AgreementAndImpliedTests(_Base):
    """F5: a kettős értékelés megbízhatósága a motor összevont egyezéséből (GET /api/appraisals/agreement), csak
    emberi párokból; F4: a PROBAST+AI menetenkénti összítéletei is párt adnak; F6: a forgalmi lámpa megkülönbözteti a
    motor implikált (nem emberi) ítéletét."""

    @staticmethod
    def with_domains(doc, change=None):
        """A doménítéletek a motor implikált ítéletei (change: {domain: (ítélet, felülbírálási ok)})."""
        res = api.appraisal_check(doc)
        doc["domain_judgements"] = []
        for d in res.get("domains") or []:
            j, why = (change or {}).get(str(d["domain"]), (d.get("implied"), None))
            doc["domain_judgements"].append({"domain": d["domain"], "pass": d.get("pass"), "judgement": j,
                                             "rationale": "teszt", "override_reason": why, "decision_id": None})
        return doc

    def test_agreement_route_pools_human_pairs_only(self):
        a, _ = human_doc("S1", "KP")
        b, _ = human_doc("S1", "SzK")
        a = self.with_domains(a)
        b = self.with_domains(b, {"1": ("high", "teszt: a randomizáció leírása hiányos")})
        self.srv.ok("PUT", path_of("S1", "KP"), {"doc": a})
        self.srv.ok("PUT", path_of("S1", "SzK"), {"doc": b})
        self.srv.ok("PUT", path_of("S1", "ai"), {"doc": ai_doc(self.proj, "S1")})       # AI-vázlat: sosem értékelő
        d = self.srv.ok("GET", "/api/appraisals/agreement?tool=rob2&target=o1")["data"]
        self.assertEqual(d["units"], [{"unit": "S1", "target": "o1", "a": "KP", "b": "SzK"}])
        da = self.file("04_torzitas_kockazat/appraisals/S1.rob2.o1.KP.json")
        db = self.file("04_torzitas_kockazat/appraisals/S1.rob2.o1.SzK.json")
        want = api.appraisal_agreement_pooled([(da, db)], instrument=api.instrument_get("rob2"))
        self.assertEqual(json.dumps(d["agreement"], sort_keys=True), json.dumps(want, sort_keys=True),
                         "a számok a motorból, változatlanul")
        self.assertIn("judgement_kappa", d["agreement"])
        self.assertIn("kappa_text", d["agreement"]["judgement_kappa"])
        none = self.srv.ok("GET", "/api/appraisals/agreement?tool=quadas2")["data"]
        self.assertEqual((none["units"], none["agreement"]), ([], None))

    def test_probast_pass_overalls_are_pairs(self):
        a, _ = E._complete_doc("probast-ai", "S8", "M1", "KP", "low", "both")
        b, _ = E._complete_doc("probast-ai", "S8", "M1", "SzK", "low", "both")
        a["overall_passes"] = [{"pass": "development", "judgement": "low"}, {"pass": "evaluation", "judgement": "high"}]
        b["overall_passes"] = [{"pass": "development", "judgement": "low"}, {"pass": "evaluation", "judgement": "low"}]
        ag = api.appraisal_consensus(a, b, instrument=api.instrument_get("probast-ai"))
        self.assertEqual((ag["overall_kappa"]["n"], ag["overall_kappa"]["agree"], ag["overall_kappa"]["disagree"]), (2, 1, 1))

    def test_rob_summary_marks_implied(self):
        d, _ = human_doc("S2", "KP")
        d["domain_judgements"] = []                                  # csak válaszok: a motor implikál
        self.srv.ok("PUT", path_of("S2", "KP"), {"doc": d})
        summ = self.srv.ok("GET", "/api/appraisals/rob-summary?tool=rob2&outcome=o1")["data"]
        st = [x for x in summ["studies"] if x["study_id"] == "S2"][0]
        self.assertTrue(st["domains"] and all(x["from"] == "implied" for x in st["domains"]), st["domains"])
        st1 = [x for x in summ["studies"] if x["study_id"] == "S1"]
        if st1:
            self.assertTrue(all(x["from"] == "judgement" for x in st1[0]["domains"] if x.get("judgement")))
        self.assertIn(st.get("overall_from"), ("judgement", "implied"))
        for w in summ.get("weighted") or []:
            self.assertIn("n_implied", w)


class ListTargetTests(_Base):
    """UX-1 / UX-2: a lista-elem target mezője szöveg (a felület ezzel szűr)."""

    def test_target_is_key_string(self):
        a, _ = human_doc("S7", "KP")
        self.srv.ok("PUT", path_of("S7", "KP"), {"doc": a})
        b, _ = human_doc("S7", "SzK")
        self.srv.ok("PUT", path_of("S7", "SzK"), {"doc": b})
        items = self.srv.ok("GET", "/api/appraisals?tool=rob2")["data"]["items"]
        mine = [x for x in items if x["unit"] == "S7"]
        self.assertEqual(len(mine), 2)
        for x in mine:
            self.assertEqual(x["target"], "o1")
            self.assertIsInstance(x["target_obj"], dict)
            self.assertEqual(x["target_obj"]["key"], "o1")
        # a target szűrés is a kulcsra megy
        only = self.srv.ok("GET", "/api/appraisals?tool=rob2&target=o1")["data"]["items"]
        self.assertTrue(all(x["target"] == "o1" for x in only))


class RaterRedactionTests(_Base):
    """SEC-5: B osztályban az értékelő teljes neve sem a pillanatképben, sem az audit-csomagban (az activity.jsonl
    szándékosan változatlan — hash-lánc), a régi „(értékelő: név)” napló-szövegben sem."""
    data_class = "B"
    NAME = "Kovacs_Bela"

    def test_snapshot_and_audit_zip_use_initials(self):
        import io
        import zipfile
        import test_snapshot as TS
        from ma_gui import audit_export
        doc, imp = human_doc("S1", self.NAME)
        other = "high" if imp != "high" else "low"
        doc["overall"] = {"judgement": other, "rationale": "x", "override_reason": "teszt-indoklás",
                          "decision_id": None}
        self.srv.ok("PUT", path_of("S1", self.NAME), {"doc": doc})
        ai = ai_doc(self.proj, "S2")
        env = self.srv.ok("PUT", path_of("S2", "ai"), {"doc": ai})
        self.srv.ok("POST", "/api/appraisals/S2/rob2/approve?rater=ai&target=o1", {"approver": self.NAME},
                    headers=[("If-Match", env["_etag"])])
        # egy korábbi változat napló-szövege (a név a szövegben)
        api.project_log(self.proj, "user", "Értékelés felülbírálása — rob2 · S1 · o1 · D1: implikált low → high "
                        "(értékelő: %s)" % self.NAME, rationale="régi bejegyzés", stage="S06")
        html, _info = TS.build(self.proj, self.home)
        routes = TS.embedded(html)["routes"]
        leaks = [k for k, v in routes.items() if self.NAME in json.dumps(v, ensure_ascii=False)]
        self.assertEqual(leaks, [])
        app = TS.open_app(self.proj, self.home)
        try:
            data, _info = audit_export.build(app=app)
        finally:
            app.close()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            leaks = [n for n in zf.namelist() if n != "07_ellenorzes/activity.jsonl"
                     and self.NAME.encode("utf-8") in zf.read(n)]
            naplo = zf.read("projekt/naplo.md").decode("utf-8")
        self.assertEqual(leaks, [])
        self.assertIn("(értékelő: KB)", naplo)


class SnapshotGradeAdviceTests(unittest.TestCase):
    """FID-6: a pillanatkép a GRADE-tanácsot ugyanazzal a lekérdezéssel rögzíti, amit a felület küld (mentett MID +
    futás) — így a pontatlanság-ellenőrzés a MID-hez mér, mint élőben."""

    def test_advice_recorded_with_mid(self):
        import tempfile
        import test_snapshot as TS
        import test_snapshot_v1 as T1
        from ma_gui import snapshot
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_r3_snapmid_"))
        try:
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            root, run_id = T1.make_v1_project(tmp, "A", "p")
            g = api.grade_get(root, "o1")
            g["mid_text"] = "0.90–1.10"
            api.grade_put(root, "o1", g, actor="user")
            html, _info = TS.build(root, home)
            routes = TS.embedded(html)["routes"]
            key = snapshot.route_key("GET", "/api/grade/o1/advice", {"mid": "0.90–1.10", "run": run_id})
            self.assertIn(key, routes, [k for k in routes if "/advice" in k])
            adv = routes[key]["data"]
            self.assertEqual(adv.get("mid"), "0.90–1.10")
            imp = json.dumps(adv["advice"]["domains"]["imprecision"], ensure_ascii=False)
            self.assertIn("MID", imp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class HeadhunterSafeArgvTests(unittest.TestCase):
    """SEC-4: a döntés activity-rekordjába a PHI-szűrt szabad szövegek egyike sem kerülhet (T10)."""

    def test_verify_secondary_free_texts_masked(self):
        from ma_gui.routes import headhunter as HR

        class _App(object):
            project_root = "/tmp/projekt"
            actor = "user:teszt"

        body = {"kind": "verify_secondary", "reason": "FREETEXT-REASON ok",
                "options": {"study": "st-0001", "field": "events", "status": "verified",
                            "outcome": "FREETEXT-OUTCOME Halalozas 30 napon", "arm": "FREETEXT-ARM kontroll",
                            "primary_locator": "FREETEXT-LOC 5. oldal", "primary_value": "FREETEXT-VAL 12"}}
        argv, _rel, texts, _details = HR.build_decide_argv(_App(), body)
        self.assertTrue({"outcome", "arm", "primary_locator", "primary_value", "reason"} <= set(texts))
        safe = " ".join(HR._safe_argv(argv))
        for name, value in texts.items():
            self.assertNotIn(value, safe, name)
        self.assertNotIn("FREETEXT", safe)
        self.assertIn("--outcome=«szöveg»", safe)
        self.assertIn("--arm=«szöveg»", safe)
        self.assertIn("--study=st-0001", safe)                   # az azonosítók maradnak (visszakereshetőség)


class HeadhunterGuiWordingTests(unittest.TestCase):
    """UX-7: a felület a gombot/mezőt nevezi meg, nem a CLI-parancsot (a CLI saját kimenete változatlan)."""
    CLI_HINT = ("sources --check", "--enable", "--max)", "--cap)", "--cap / --max", "ma.py headhunter")

    def assert_gui(self, text):
        for bad in self.CLI_HINT:
            self.assertNotIn(bad, text)

    def test_source_status_messages(self):
        from ma_gui.routes import headhunter as HR
        from metaelemzes.headhunter import net
        for src in ("pubmed", "europepmc", "openalex", "scopus", "ctgov", "crossref"):
            for status in ("unknown", "unreachable", "disabled", "ok", "rate_limited", "not_configured"):
                msg = net.status_explain(src, status)
                out = HR.gui_wording({"message": msg})["message"]
                self.assert_gui(out["hu"] + " " + out["en"])
        out = HR.gui_wording({"message": net.status_explain("pubmed", "unknown")})["message"]
        self.assertIn("„Források ellenőrzése”", out["hu"])
        self.assertIn("“Check sources”", out["en"])
        out = HR.gui_wording({"message": net.status_explain("openalex", "disabled")})["message"]
        self.assertIn("„Bekapcsolva”", out["hu"])

    def test_findings_name_the_gui_field(self):
        from ma_gui.routes import headhunter as HR
        from metaelemzes.headhunter import checks
        hu = [checks.RULES[k][2] for k in ("H012", "H014")]
        hu += ["Az áttekintés-keresés nem teljes: s-1 (pubmed) — a felső korlát (--max) vagy a lapozás megállította.",
               "Hiányos keresés: s-2 (pubmed) — a PRISMA-szám nem véglegesíthető; szűkíts vagy emeld a korlátot (--cap), "
               "és futtasd újra."]
        for text in hu:
            out = HR.gui_wording({"findings": [{"hu": text, "advice": text}]})["findings"][0]
            self.assert_gui(out["hu"] + out["advice"])
        out = HR.gui_wording({"hu": hu[-2]})["hu"]
        self.assertIn("„Legfeljebb forrásonként”", out)
        out = HR.gui_wording({"hu": hu[-1]})["hu"]
        self.assertIn("„Legfeljebb találat forrásonként”", out)

    def test_ids_and_commands_untouched(self):
        from ma_gui.routes import headhunter as HR
        doc = {"next": "sources <projekt> --check", "query": "x --max y", "rows": [{"source": "pubmed"}]}
        self.assertEqual(HR.gui_wording(doc), doc)

    def test_route_output_in_fixtures(self):
        """A fixture-ök a valódi route kimenetei (sodródás-őr: test_headhunter_fixtures)."""
        for name in ("headhunter_status.json", "headhunter_merge.json"):
            with open(os.path.join(H.ROOT, "ma_gui", "web", "fixtures", name), encoding="utf-8") as fh:
                doc = json.load(fh)
            for r in doc["routes"]:
                data = json.dumps(r["envelope"]["data"], ensure_ascii=False)
                if r["path"] in ("/api/headhunter/sources", "/api/headhunter/update"):
                    self.assertNotIn("sources --check", data, r["path"])
                    self.assertNotIn("--enable", data, r["path"])
                if r["path"] == "/api/headhunter/sources" and (r.get("query") or {}).get("lang") == "hu":
                    self.assertIn("Források ellenőrzése", data)


if __name__ == "__main__":
    unittest.main()
