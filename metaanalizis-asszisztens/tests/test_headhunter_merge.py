# -*- coding: utf-8 -*-
"""Metaheadhunter — állapot (state.json, decisions.jsonl), frissítő keresés (L8), egyesítés (L9), PRISMA-leképezés,
exportok és a parancssor (TERV_metaheadhunter.md 4., 11–14. fejezet).

Minden teszt OFFLINE fut: szintetikus projekttel (a ``dedup.link`` építi a ``studies.json``-t) és hamis
forráskliensekkel (a 20.5 szerződés metódusaival). A PMID-ek (99100101 …) szintetikusak, nem valós cikkek.

Lefedett pontok:

- N1: az update-rekordok azonosítói API-forrásúak; az ágens/eszköz nem hozhat emberi döntést (N3);
- N2/H010: másodlagos adat ellenőrizetlenül nem kerül a kimenet-sablonba; ``verify-secondary`` után igen;
- N6/H016: titok nem kerül a state.json-ba; N9: betegadat-gyanús indoklás nem kerül a döntésnaplóba;
- 4.5/4.6: atomikus írás, hash-lánc (H015), elavulás (``stale``);
- 12. fejezet: ablak (latest/earliest/manual, átfedés, tartalék dátum H008, szórás), lekérdezések SR-szűrő nélkül
  dátummezővel, emberi ablak-jóváhagyás, duplumszűrés egymás közt és az ismert halmazzal (already_known),
  elérhetetlen forrás → H014 + kilépési kód 3, idempotens újrafuttatás, hivatkozáskövetés;
- 11. fejezet: egy rekord vizsgálatonként proveniencával, ``found_by``, másodlagos adat áttekintésenként,
  ellentmondás (H018), kimaradó (ki nem választott) áttekintés, EP5 lezárás feltételei (H009) és érvényvesztése
  (H017), exportok (studies.ma.json, kimenet-sablon, másodlagos munkalista, RIS, screening.csv), ``--to-project``
  csak nem létező célfájlba;
- 13. fejezet: a ``prisma_flow.json`` kanonikus nevekkel átmegy a motor ``check_flow``-ján; D1 = A − D3 − B; a TERV
  ellenőrzött mintája 0 hiba / 0 figyelmeztetés; hibás számok → H011; ``own_update`` (P015);
- 14. fejezet: CLI-boríték, kilépési kódok (0/1/2/4), magyar üzenetek.
"""
import contextlib
import copy
import io
import json
import sqlite3
import os
import shutil
import tempfile
import unittest

from _helpers import ROOT  # noqa: F401  (a sys.path beállítása)
from ma_gui import schema_lite
from metaelemzes import prisma as engine_prisma
from metaelemzes.headhunter import dedup, net
from metaelemzes.headhunter import state as S
from metaelemzes.headhunter import update as U
from metaelemzes.headhunter import merge as M
from metaelemzes.headhunter import prisma_map as PM
from metaelemzes.headhunter.__main__ import main as cli_main

CONTRACTS = os.path.join(ROOT, "metaelemzes", "headhunter", "contracts")
REG = schema_lite.load_schema_dir(CONTRACTS)
AT = "2026-10-05T10:00:00Z"
NOW = "2026-10-05T10:00:00Z"
RV_A, RV_B, RV_C = "rv-pmid-99000001", "rv-pmid-99000002", "rv-pmid-99000003"


def schema(name):
    return REG["urn:szk:contract:ma.headhunter.%s:1" % name]


def assert_valid(tc, doc, name):
    errs = schema_lite.validate(doc, schema(name), REG)
    tc.assertEqual([], [str(e) for e in errs][:10], "%s: sémahiba" % name)


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def idv(value, source="pubmed", via="pubmed.esummary"):
    return {"value": value, "source": source, "via": via, "at": AT}


# =============================================================================================
# szintetikus projekt
# =============================================================================================

def _ev(rid, n, quote, kind="table_row", strategy="jats_table", label="Table 1", row=None):
    return {"evidence_id": "ev-%s-%04d" % (rid, n), "review_id": rid, "kind": kind, "strategy": strategy,
            "locator": {"container": "PMC000%s" % rid[-1], "label": label, "row": row}, "quote": quote,
            "extracted_by": "tool:headhunter", "at": AT, "confidence": "high"}


def _cand(cid, rec_id, text, label, ev_ids, role="included", confidence="high", status="confirmed", secondary=None,
          group_key=None):
    return {"cand_id": cid, "cited_as": {"text": text, "first_author": label.split()[0], "year": int(label.split()[1])},
            "study_label_in_review": label, "group_key": group_key, "ids": {}, "rec_id": rec_id,
            "role_in_review": role, "evidence_ids": ev_ids, "confidence": confidence, "status": status,
            "secondary_data": secondary or [], "decision_ids": []}


def _review(rid, status, cands, evidence, search_date=None, year=2021):
    doc = {"schema": "szk.ma.headhunter.review/v1", "model": "szk.ma.headhunter/v1", "review_id": rid,
           "status": status, "ids": {"pmid": idv(rid.split("-")[-1], via="pubmed.esearch")},
           "bib": {"title": "Systematic review %s" % rid, "first_author": "Author%s" % rid[-1], "year": year,
                   "journal": "J Test"}, "found_by": ["s-pubmed-20261005T090000Z"], "candidates": cands,
           "evidence": evidence, "decision_ids": []}
    if search_date:
        doc["search_date"] = {"value": search_date, "precision": "month" if len(search_date) == 7 else "day",
                              "fallback": False, "evidence_id": evidence[0]["evidence_id"]}
    return doc


def _record(pmid, title, author, year, rid_review, cid, nct=None, journal="Trials J"):
    rec = {"rec_id": "rec-pmid-%s" % pmid, "ids": {"pmid": idv(pmid)},
           "bib": {"title": title, "first_author": author, "authors": [author, "Other B"], "year": year,
                   "journal": journal},
           "flags": {}, "related": [], "origins": [], "retrievals": [],
           "resolution": {"status": "resolved", "method": "pub-id", "score": 1.0}, "status": "active",
           "merged_into": None}
    for r, c in ([(rid_review, cid)] if rid_review else []):
        rec["origins"].append({"route": "review_extraction", "review_id": r, "cand_id": c, "search_id": None})
    if nct:
        rec["ids"]["nct"] = idv(nct)
    return rec


def build_project(base, mode="harvest"):
    """Három forrás-áttekintés (A, B kiválasztva; C kizárva), hat közlemény, egy közös vizsgálat (Smith 2015) két
    áttekintésben eltérő másodlagos n1-gyel, egy társközlemény (Brown 2019) az áttekintés csoportosításával."""
    proj = os.path.join(base, "proj")
    os.makedirs(proj)
    pico = S.pico_from_args("Hatásos-e az X a Y-ban?", population="adults with Y", intervention="drug X; X therapy")
    S.init_state(proj, "Hatásos-e az X a Y-ban?", pico=pico, mode=mode, actor="user:SzK", now=NOW)
    evA = [_ev(RV_A, 1, "Smith 2015 | 50 | RCT", row=1), _ev(RV_A, 2, "Jones 2016 | 80 | RCT", row=2),
           _ev(RV_A, 3, "Kim 2017 | 40 | RCT", row=3)]
    evB = [_ev(RV_B, 1, "Smith 2015 | 52", row=1), _ev(RV_B, 2, "Brown 2018 | 120", row=2),
           _ev(RV_B, 3, "Brown 2019 (follow-up of Brown 2018)", row=3)]
    evC = [_ev(RV_C, 1, "Old 2001 | 30", row=1)]
    revA = _review(RV_A, "selected", [
        _cand("c0001", "rec-pmid-99100101", "Smith A. Drug X in Y. Trials J 2015.", "Smith 2015", [evA[0]["evidence_id"]],
              secondary=[{"field": "n1", "value": 50, "evidence_id": evA[0]["evidence_id"], "status": "unverified",
                          "outcome": "mortality"},
                         {"field": "e1", "value": 5, "evidence_id": evA[0]["evidence_id"], "status": "unverified",
                          "outcome": "mortality"}]),
        _cand("c0002", "rec-pmid-99100102", "Jones B. X therapy trial. 2016.", "Jones 2016", [evA[1]["evidence_id"]]),
        _cand("c0003", "rec-pmid-99100105", "Kim C. X for Y. 2017.", "Kim 2017", [evA[2]["evidence_id"]],
              confidence="medium", status="proposed"),
    ], evA, search_date="2020-06")
    revB = _review(RV_B, "selected", [
        _cand("c0001", "rec-pmid-99100101", "Smith A et al. 2015", "Smith 2015", [evB[0]["evidence_id"]],
              secondary=[{"field": "n1", "value": 52, "evidence_id": evB[0]["evidence_id"], "status": "unverified",
                          "outcome": "mortality"}]),
        _cand("c0002", "rec-pmid-99100103", "Brown D. Large trial of X. 2018.", "Brown 2018", [evB[1]["evidence_id"]],
              group_key="g-brown"),
        _cand("c0003", "rec-pmid-99100104", "Brown D. Long-term follow-up. 2019.", "Brown 2019",
              [evB[2]["evidence_id"]], role="included_companion", group_key="g-brown"),
    ], evB, search_date="2021-03-15")
    revC = _review(RV_C, "excluded", [
        _cand("c0001", "rec-pmid-99100106", "Old E. 2001.", "Old 2001", [evC[0]["evidence_id"]]),
    ], evC, search_date="2010-01", year=2011)
    for r in (revA, revB, revC):
        S.save_review(proj, r)
    records = [
        _record("99100101", "Drug X reduces mortality in adults with Y: a randomised trial", "Smith A", 2015, RV_A,
                "c0001"),
        _record("99100102", "X therapy versus placebo in Y", "Jones B", 2016, RV_A, "c0002"),
        _record("99100103", "A large pragmatic trial of drug X", "Brown D", 2018, RV_B, "c0002", nct="NCT09000001"),
        _record("99100104", "Long-term follow-up of the drug X pragmatic trial", "Brown D", 2019, RV_B, "c0003",
                nct="NCT09000001"),
        _record("99100105", "Effect of X on Y outcomes", "Kim C", 2017, RV_A, "c0003"),
        _record("99100106", "Early experience with X", "Old E", 2001, RV_C, "c0001"),
    ]
    records[0]["origins"].append({"route": "review_extraction", "review_id": RV_B, "cand_id": "c0001",
                                  "search_id": None})
    doc = dedup.link(records, S.load_reviews(proj), S.read_decisions(proj), now=NOW)
    S.write_json_atomic(S.path(proj, "studies.json"), doc)
    return proj


def study_of(proj, rec_id):
    doc = S.read_json(S.path(proj, "studies.json"))
    for s in doc["studies"]:
        if any(r["rec_id"] == rec_id for r in s["reports"]):
            return s["study_id"]
    return None


def screen_all(proj, include=(), exclude=(), actor="user:SzK", reason_code="X4"):
    """Szűrési döntések a CLI-vel (cím/absztrakt + teljes szöveg)."""
    out = io.StringIO()
    if include:
        rc = cli_main(["confirm", proj, "--target", ",".join(include), "--actor", actor, "--json"], stdout=out)
        assert rc == 0, out.getvalue()
    if exclude:
        out = io.StringIO()
        rc = cli_main(["exclude", proj, "--target", ",".join(exclude), "--reason-code", reason_code, "--reason",
                       "nem közöl releváns kimenetet", "--actor", actor, "--json"], stdout=out)
        assert rc == 0, out.getvalue()


def accept_all_proposals(proj):
    doc = S.read_json(S.path(proj, "studies.json"))
    for p in doc["proposals"]:
        if p["status"] == "pending":
            dedup.decide_proposal(proj, p["proposal_id"], "accept", "user:SzK")
    dedup.run_dedupe(proj)


# =============================================================================================
# hamis forráskliensek (a 20.5 szerződés metódusaival)
# =============================================================================================

class FakePager(object):
    def __init__(self, items, total=None, complete=True, error=None):
        self._items = list(items)
        self.total = len(self._items) if total is None else total
        self.retrieved = 0
        self.complete = complete
        self.error = error

    def __iter__(self):
        for it in self._items:
            self.retrieved += 1
            yield it


class FakePubmed(object):
    def __init__(self, pmids):
        self.pmids = pmids
        self.calls = []

    def esearch_all(self, term, cap=5000, datetype=None, mindate=None, maxdate=None, **kw):
        self.calls.append({"term": term, "datetype": datetype, "mindate": mindate, "maxdate": maxdate})
        ids = self.pmids[:cap]
        return {"count": len(self.pmids), "ids": ids, "retrieved": len(ids), "complete": len(ids) >= len(self.pmids),
                "error": None}

    TITLES = {"99100101": "Drug X reduces mortality in adults with Y: a randomised trial",
              "99200201": "Cardiac safety of drug X: a multicentre randomised study",
              "99200202": "Quality of life after X therapy in elderly patients"}

    def esummary(self, pmids):
        out = []
        for p in pmids:
            out.append({"pmid": p, "doi": None, "pmcid": None, "title": self.TITLES.get(p, "Trial %s" % p),
                        "authors": ["Author%s A" % p[-3:]], "first_author": "Author%s A" % p[-3:], "year": 2023,
                        "journal": "Trials J", "pub_types": ["Randomized Controlled Trial"],
                        "retrieval": {"source": "pubmed", "endpoint": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
                                                                      "esummary.fcgi?db=pubmed", "at": AT,
                                      "http_status": 200, "cache_key": None}, "raw": {"secret": "x"}})
        return out


class FakeEpmc(object):
    def __init__(self, items, fail=False):
        self.items = items
        self.fail = fail
        self.queries = []

    def search(self, query, result_type="lite", page_size=100, max_results=None, **kw):
        self.queries.append(query)
        if self.fail:
            raise net.SourceUnavailable("europepmc", "unreachable")
        return FakePager(self.items)

    def citations(self, src, ext_id, max_results=None, normalize=True):
        return FakePager([{"pmid": "99300001", "doi": None, "title": "A 2024 trial citing the review", "year": 2024,
                           "first_author": "Cite A", "authors": ["Cite A"], "journal": "J C"},
                          {"pmid": "99100101", "doi": None, "title": "old known trial", "year": 2015,
                           "first_author": "Smith A", "authors": ["Smith A"], "journal": "Trials J"}])


class FakeCtgov(object):
    def studies(self, term=None, advanced=None, max_results=None, **kw):
        if "StudyFirstPostDate" in (advanced or ""):
            return FakePager([{"protocolSection": {"identificationModule": {
                "nctId": "NCT09999999", "briefTitle": "Drug X in Y (registry)", "officialTitle": "Drug X in adults with Y"},
                "statusModule": {"studyFirstPostDateStruct": {"date": "2023-05-01"}}}}])
        return FakePager([])


def epmc_items():
    return [
        {"id": "99200201", "source": "MED", "pmid": "99200201",
         "title": "Cardiac safety of drug X: a multicentre randomised study",
         "authorString": "Author201 A.", "pubYear": "2023", "journalTitle": "Trials J"},
        {"id": "PPR123", "source": "PPR", "doi": "10.9999/preprint.xyz", "title": "A preprint on X in Y",
         "authorString": "Pre P.", "pubYear": "2024", "journalTitle": "Preprint server"},
    ]


def fake_clients(fail_epmc=False):
    return {"pubmed": FakePubmed(["99100101", "99200201", "99200202"]), "europepmc": FakeEpmc(epmc_items(), fail_epmc),
            "ctgov": FakeCtgov()}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-merge-")
        self.env_backup = dict(os.environ)
        for k in ("MA_SCOPUS_APIKEY", "MA_OPENALEX_APIKEY", "MA_NCBI_APIKEY", "MA_CONTACT_EMAIL",
                  "MA_SCOPUS_INSTTOKEN", "MA_HH_CASSETTE", "MA_HH_CASSETTE_FILE"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        out = io.StringIO()
        rc = cli_main(list(args) + ["--json"], stdout=out)
        return rc, json.loads(out.getvalue())


# =============================================================================================
# állapot és döntésnapló
# =============================================================================================

class TestState(_Base):
    def test_init_creates_valid_state_and_decision(self):
        proj = os.path.join(self.tmp, "p")
        os.makedirs(proj)
        st = S.init_state(proj, "Kérdés?", pico=S.pico_from_args("Kérdés?", population="a; b", intervention="c"),
                          actor="user:SzK", now=NOW)
        assert_valid(self, S.load_state(proj), "state")
        self.assertEqual(["P", "I"], [b["concept"] for b in st["pico"]["query_blocks"]])
        self.assertEqual(["a", "b"], st["pico"]["query_blocks"][0]["terms"])
        for sub in ("reviews", "exports", "runs", os.path.join("cache", "http")):
            self.assertTrue(os.path.isdir(S.path(proj, sub)), sub)
        ds = S.read_decisions(proj)
        self.assertEqual(1, len(ds))
        self.assertEqual("criteria_set", ds[0]["kind"])
        assert_valid(self, ds[0], "decision")
        self.assertEqual([], S.verify_chain(ds))
        self.assertEqual(sorted(S.CHECKPOINTS), [c["id"] for c in S.load_state(proj)["checkpoints"]])
        with self.assertRaises(S.StateError):
            S.init_state(proj, "Másik?", now=NOW)
        S.init_state(proj, "Másik?", now=NOW, force=True)
        self.assertEqual(1, len(S.read_decisions(proj)), "a döntésnapló megmarad")

    def test_not_initialized(self):
        with self.assertRaises(S.StateError) as cm:
            S.load_state(self.tmp)
        self.assertEqual("HH_NOT_INITIALIZED", cm.exception.code)

    def test_decisions_chain_human_only_and_phi_guard(self):
        proj = build_project(self.tmp)
        d1 = S.append_decision(proj, "note", ("project", "x"), "megjegyzés", "tool:headhunter", now=NOW)
        with self.assertRaises(S.DecisionError):
            S.append_decision(proj, "screen", ("record", "rec-pmid-99100101"), "include", "agent:ma-metaheadhunter",
                              level="full_text")
        for bad in ("A beteg TAJ-száma 123 456 788.", "kapcsolat: valaki@example.org", "szül. 1970-01-01 beteg"):
            with self.assertRaises(S.DecisionError):
                S.append_decision(proj, "screen", ("record", "rec-pmid-99100101"), "include", "user:SzK",
                                  level="full_text", reason=bad)
        d2 = S.append_decision(proj, "secondary_verify", ("study", "st-0001"), "verified", "user:SzK", now=NOW,
                               field="n1", review_id=RV_A, primary_locator="p. 5, Table 2")
        self.assertEqual("n1", d2["field"])
        self.assertEqual(d1["sha256"], d2["prev_sha256"])
        assert_valid(self, d2, "decision")
        ds = S.read_decisions(proj)
        self.assertEqual([], S.verify_chain(ds))
        seqs = [int(d["decision_id"].rsplit("-", 1)[1]) for d in ds]
        self.assertEqual(seqs, sorted(set(seqs)), "szigorúan növekvő sorszám")
        # utólagos átírás → H015, és új döntés nem fűzhető hozzá
        fp = S.path(proj, "decisions.jsonl")
        lines = _read(fp).splitlines()
        tampered = json.loads(lines[0])
        tampered["value"] = "rejected"
        lines[0] = json.dumps(tampered, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self.assertTrue(S.verify_chain(S.read_decisions(proj)))
        with self.assertRaises(S.DecisionError):
            S.append_decision(proj, "note", ("project", "x"), "y", "user:SzK")

    def test_dedup_and_state_hash_rules_agree(self):
        proj = build_project(self.tmp)
        d = dedup.append_decision(proj, "note", ("project", "x"), "dedup-ból", "user:SzK", now=NOW)
        S.append_decision(proj, "note", ("project", "y"), "state-ből", "user:SzK", now=NOW)
        ds = S.read_decisions(proj)
        self.assertEqual([], S.verify_chain(ds))
        self.assertEqual([], dedup.verify_decision_chain(ds))
        self.assertEqual(S.decision_sha256(d), dedup.decision_sha256(d))

    def test_secret_never_in_state(self):
        os.environ["MA_SCOPUS_APIKEY"] = "TESTKEY-SCOPUS-1234567890"
        proj = os.path.join(self.tmp, "p")
        os.makedirs(proj)
        S.init_state(proj, "Kérdés?", now=NOW)
        text = _read(S.path(proj, "state.json"))
        self.assertNotIn("TESTKEY-SCOPUS", text)
        st = S.load_state(proj)
        self.assertTrue(st["sources"]["scopus"]["key_configured"])
        st["pico"]["other"] = "kulcs: TESTKEY-SCOPUS-1234567890"
        with self.assertRaises(S.StateError) as cm:
            S.save_state(proj, st)
        self.assertEqual("H016", cm.exception.code)
        self.assertNotIn("TESTKEY-SCOPUS", _read(S.path(proj, "state.json")))

    def test_stale_propagation_and_atomic_write(self):
        st = S.new_state("Q?", now=NOW)
        for s in ("extract", "resolve", "dedupe", "merge", "prisma"):
            S.set_step(st, s, "done", now=NOW)
        S.set_step(st, "extract", "done", now=NOW, stale_downstream=True)
        self.assertEqual("stale", st["steps"]["resolve"]["status"])
        self.assertEqual("stale", st["steps"]["prisma"]["status"])
        self.assertEqual("done", st["steps"]["extract"]["status"])
        d = os.path.join(self.tmp, "w")
        S.write_json_atomic(os.path.join(d, "a.json"), {"x": 1})
        self.assertEqual(["a.json"], os.listdir(d), "nincs ottmaradt ideiglenes fájl")
        self.assertTrue(_read(os.path.join(d, "a.json")).endswith("}\n"))


# =============================================================================================
# frissítő keresés (L8)
# =============================================================================================

class TestWindow(unittest.TestCase):
    def reviews(self):
        return [{"review_id": RV_A, "search_date": {"value": "2020-06", "precision": "month", "fallback": False,
                                                    "evidence_id": "ev-%s-0001" % RV_A}},
                {"review_id": RV_B, "search_date": {"value": "2023-03-31", "precision": "day", "fallback": False,
                                                    "evidence_id": None}}]

    def test_latest_anchor_with_overlap(self):
        w = U.compute_window(self.reviews(), today="2026-10-05")
        self.assertEqual("2023-03-31", w["latest_source_search"])
        self.assertEqual("2020-06", w["earliest_source_search"])
        self.assertEqual("2022-09-30", w["start_date"], "hónap-aritmetika a hónap végéhez igazodik")
        self.assertEqual("2026-10-05", w["end_date"])
        self.assertTrue(any(x["code"] == "W-SPREAD" for x in w["warnings"]), "33 hónapos szórás")
        w2 = U.compute_window(self.reviews(), anchor="earliest", overlap_months=0, today="2026-10-05")
        self.assertEqual("2020-06-01", w2["start_date"])
        w3 = U.compute_window(self.reviews(), anchor="manual", start="2024-01-15", today="2026-10-05")
        self.assertEqual("2024-01-15", w3["start_date"])
        with self.assertRaises(U.UpdateError):
            U.compute_window(self.reviews(), anchor="manual", today="2026-10-05")
        with self.assertRaises(U.UpdateError):
            U.compute_window(self.reviews(), start=None, end="2001-01-01", today="2026-10-05")

    def test_fallback_date_h008_and_missing(self):
        revs = [{"review_id": RV_C, "bib": {"year": 2019}}]
        w = U.compute_window(revs, today="2026-10-05")
        self.assertEqual("2018", w["per_review"][0]["search_date"])
        self.assertTrue(w["per_review"][0]["fallback"])
        self.assertEqual("2017-07-01", w["start_date"])
        self.assertTrue(any(x["code"] == "H008" for x in w["warnings"]))
        with self.assertRaises(U.UpdateError) as cm:
            U.compute_window([{"review_id": RV_C, "bib": {}}], today="2026-10-05")
        self.assertEqual("HH_NO_SEARCH_DATE", cm.exception.code)

    def test_queries_without_sr_filter_with_date_fields(self):
        w = U.compute_window(self.reviews(), today="2026-10-05")
        blocks = [{"concept": "P", "terms": ["adults with Y"], "mesh": ["Y Disease"]},
                  {"concept": "I", "terms": ["drug X"], "mesh": []},
                  {"concept": "O", "terms": ["mortality"], "mesh": []}]
        q = U.build_update_queries(blocks, w, U.UPDATE_SOURCES)
        pm = q["pubmed"][0]
        self.assertEqual("edat", pm["datetype"])
        self.assertEqual("2022/09/30", pm["mindate"])
        self.assertEqual("2026/10/05", pm["maxdate"])
        self.assertIn('"Y Disease"[mh]', pm["term"])
        self.assertNotIn("mortality", pm["term"], "alapból csak P ÉS I")
        for src, lst in q.items():
            for x in lst:
                self.assertNotIn("systematic", x["query"].lower(), src)
                self.assertNotIn("meta-analysis", x["query"].lower(), src)
        self.assertIn("CREATION_DATE:[2022-09-30 TO 2026-10-05]", q["europepmc"][0]["query"])
        self.assertIn("PUBYEAR > 2021", q["scopus"][0]["query"])
        self.assertEqual(["StudyFirstPostDate", "ResultsFirstPostDate"], [x["date_field"] for x in q["ctgov"]])
        self.assertEqual(("from_publication_date", "2022-09-30"), q["openalex"][0]["filter"][0])
        over = U.build_update_queries(blocks, w, ["europepmc"], query_by_source={"europepmc": "(foo) AND (bar)"})
        self.assertTrue(over["europepmc"][0]["query"].startswith("((foo) AND (bar)) AND CREATION_DATE"))


class TestDedupeHits(unittest.TestCase):
    def test_chain_union_and_rec_id_from_strongest(self):
        a = U.make_record({"pmid": "99400001", "title": "T one"}, "pubmed", "pubmed.esearch", "s-pubmed-1", AT)
        b = U.make_record({"doi": "10.1000/abc", "title": "T one"}, "openalex", "openalex.works", "s-openalex-1", AT)
        c = U.make_record({"pmid": "99400001", "doi": "10.1000/ABC", "title": "T one"}, "europepmc", "europepmc.search",
                          "s-europepmc-1", AT)
        d = U.make_record({"pmid": "99400002", "title": "T two"}, "pubmed", "pubmed.esearch", "s-pubmed-1", AT)
        uniq, dup = U.dedupe_hits([b, a, d, c])
        self.assertEqual(2, dup)
        self.assertEqual(["rec-pmid-99400001", "rec-pmid-99400002"], [r["rec_id"] for r in uniq])
        merged = uniq[0]
        self.assertEqual({"pmid", "doi"}, set(merged["ids"]))
        self.assertEqual(3, len(merged["origins"]))
        self.assertEqual("10.1000/abc", merged["ids"]["doi"]["value"])
        uniq2, _ = U.dedupe_hits([c, d, a, b])
        self.assertEqual(json.dumps(uniq, sort_keys=True), json.dumps(uniq2, sort_keys=True), "sorrendfüggetlen")

    def test_make_record_never_takes_unsourced_ids(self):
        r = U.make_record({"title": "No ids here", "year": 2024}, "europepmc", "europepmc.search", "s-europepmc-1", AT)
        self.assertTrue(r["rec_id"].startswith("rec-x-"))
        self.assertEqual({}, r["ids"])
        self.assertEqual("unresolved", r["resolution"]["status"])
        self.assertIsNone(U.make_record({}, "pubmed", "pubmed.esearch", "s-pubmed-1", AT))


class TestUpdateRun(_Base):
    def test_requires_human_window_approval(self):
        proj = build_project(self.tmp)
        res = U.run_update(proj, clients=fake_clients(), now=NOW)
        self.assertEqual(4, res["exit_code"])
        self.assertEqual("update_window", res["pending"][0]["checkpoint"])
        self.assertIn("plan", res["data"])
        self.assertFalse(os.path.exists(S.path(proj, "update_search.json")))
        with self.assertRaises(S.DecisionError):
            U.run_update(proj, actor="agent:ma-metaheadhunter", clients=fake_clients(), now=NOW)

    def test_update_dedupes_and_records_provenance(self):
        proj = build_project(self.tmp)
        cl = fake_clients()
        res = U.run_update(proj, actor="user:SzK", clients=cl, now=NOW, today="2026-10-05")
        self.assertTrue(res["ok"], res)
        self.assertEqual(4, res["exit_code"], "új rekordok → EP4")
        self.assertEqual("2020-09-15", res["data"]["window"]["start_date"])
        self.assertEqual("2020/09/15", cl["pubmed"].calls[0]["mindate"])
        doc = S.read_json(S.path(proj, "update_search.json"))
        assert_valid(self, doc, "update-search")
        r = doc["results"]
        self.assertEqual(6, r["retrieved_total"])           # 3 PubMed + 2 Europe PMC + 1 CT.gov
        self.assertEqual(1, r["duplicates_within"])         # 99200201 PubMedben és Europe PMC-ben is
        self.assertEqual(1, r["already_known"])             # 99100101: az áttekintésekből ismert
        self.assertEqual(sorted(["rec-pmid-99200201", "rec-pmid-99200202", "rec-nct-nct09999999",
                                 "rec-doi-%s" % net.sha1_10("10.9999/preprint.xyz")]), r["new_records"])
        self.assertEqual(["rec-nct-nct09999999"], r["registry_records"])
        self.assertEqual(5, r["databases_retrieved"])
        self.assertEqual(1, r["registers_retrieved"])
        studies = S.read_json(S.path(proj, "studies.json"))
        assert_valid(self, studies, "studies")
        recs = dict((x["rec_id"], x) for x in studies["records"])
        new = recs["rec-pmid-99200201"]
        self.assertEqual({"pubmed", "europepmc"}, set(o["search_id"].split("-")[1] for o in new["origins"]))
        self.assertEqual("pubmed", new["ids"]["pmid"]["source"], "API-forrású azonosító (N1)")
        self.assertNotIn("abstract", json.dumps(new))
        known = recs["rec-pmid-99100101"]
        self.assertEqual({"review_extraction", "update_search"}, set(o["route"] for o in known["origins"]))
        self.assertTrue(recs["rec-nct-nct09999999"]["flags"]["registry_record"])
        ds = [d for d in S.read_decisions(proj) if d["kind"] == "update_window"]
        self.assertEqual(1, len(ds))
        self.assertEqual("2020-09-15..2026-10-05", ds[0]["value"])
        self.assertEqual(ds[0]["decision_id"], doc["window"]["decision_id"])
        st = S.load_state(proj)
        purposes = sorted(set(s["purpose"] for s in st["searches"]))
        self.assertEqual(["update_database", "update_registry"], purposes)
        self.assertEqual("needs_human", st["steps"]["update_search"]["status"])
        # idempotens újrafuttatás: ugyanaz a rekordkészlet, a jóváhagyott ablak nem kér új döntést
        U.run_update(proj, clients=fake_clients(), now="2026-10-05T11:00:00Z", today="2026-10-05")
        studies2 = S.read_json(S.path(proj, "studies.json"))
        self.assertEqual(sorted(recs), sorted(x["rec_id"] for x in studies2["records"]))
        self.assertEqual(1, len([d for d in S.read_decisions(proj) if d["kind"] == "update_window"]))
        r2 = dict((x["rec_id"], x) for x in studies2["records"])["rec-pmid-99200201"]
        self.assertEqual(2, len(r2["origins"]), "a korábbi futás eredete lecserélődik, nem halmozódik")

    def test_source_unavailable_partial_exit3(self):
        proj = build_project(self.tmp)
        res = U.run_update(proj, actor="user:SzK", clients=fake_clients(fail_epmc=True), now=NOW, today="2026-10-05")
        self.assertEqual(3, res["exit_code"])
        self.assertTrue(any(w["code"] == "H014" and w.get("source") == "europepmc" for w in res["warnings"]))
        doc = S.read_json(S.path(proj, "update_search.json"))
        st = dict((q["source"], q["status"]) for q in doc["queries"])
        self.assertEqual("skipped_source_unavailable", st["europepmc"])
        self.assertEqual("done", st["pubmed"])

    def test_cite_search_forward(self):
        proj = build_project(self.tmp)
        U.run_update(proj, actor="user:SzK", clients=fake_clients(), now=NOW, today="2026-10-05")
        res = U.run_cite_search(proj, clients={"europepmc": FakeEpmc([])}, sources="europepmc", now=NOW)
        self.assertTrue(res["ok"])
        self.assertEqual(["rec-pmid-99300001"], res["data"]["new_records"], "a horgony előtti idéző kimarad")
        doc = S.read_json(S.path(proj, "update_search.json"))
        assert_valid(self, doc, "update-search")
        self.assertEqual("forward", doc["citation_search"][0]["direction"])
        rec = [r for r in S.read_json(S.path(proj, "studies.json"))["records"] if r["rec_id"] == "rec-pmid-99300001"][0]
        self.assertEqual("citation_search", rec["origins"][0]["route"])


# =============================================================================================
# egyesítés (L9), EP5, EP6, exportok
# =============================================================================================

class TestMerge(_Base):
    def test_merged_provenance_secondary_and_conflicts(self):
        proj = build_project(self.tmp)
        res = M.run_merge(proj, now=NOW)
        self.assertEqual(4, res["exit_code"], res)
        merged = S.read_json(S.path(proj, "merged.json"))
        assert_valid(self, merged, "merged")
        by_label = dict((s["label"], s) for s in merged["studies"])
        self.assertNotIn("Old 2001", by_label, "a ki nem választott áttekintés vizsgálata kimarad")
        self.assertEqual(1, merged["dropped"])
        smith = by_label["Smith 2015"]
        self.assertEqual([RV_A, RV_B], [p["review_id"] for p in smith["provenance"]])
        for p in smith["provenance"]:
            self.assertTrue(p["evidence_ids"], "minden bevonás-állításnak van bizonyítéka (N1)")
        self.assertEqual(["previous_reviews"], smith["found_by"])
        self.assertEqual("pending", smith["status"], "korábbi bevonás ≠ jogosultság (EP4)")
        vals = [v for blk in smith["secondary_data"] for v in blk["values"]]
        self.assertTrue(all(v["status"] == "unverified" for v in vals), "N2")
        self.assertEqual(1, len(smith["conflicts"]))
        self.assertEqual("n1", smith["conflicts"][0]["field"])
        self.assertIn("secondary_conflict", smith["flags"])
        self.assertTrue(any(w["code"] == "H018" for w in res["warnings"]))
        self.assertIn("candidate_unconfirmed", by_label["Kim 2017"]["flags"])
        c = merged["counts"]
        self.assertEqual(2, c["reviews_selected"])
        self.assertEqual(6, c["citations_total"])
        self.assertEqual(0, c["studies_included"])
        self.assertEqual(3, c["secondary_unverified"])
        self.assertGreaterEqual(c["pending_decisions"], 1)
        self.assertEqual("EP4", [p for p in res["pending"] if p["checkpoint"] == "EP4"][0]["checkpoint"])

    def test_screening_inclusion_companion_and_prisma(self):
        proj = build_project(self.tmp)
        accept_all_proposals(proj)      # Brown 2018 + 2019 egy vizsgálat (az áttekintés csoportosítása)
        S.apply_candidate_decision(proj, RV_A, "c0003", "confirm", S.append_decision(
            proj, "candidate_confirm", ("candidate", RV_A + "#c0003"), "confirm", "user:SzK")["decision_id"])
        brown = study_of(proj, "rec-pmid-99100103")
        self.assertEqual(brown, study_of(proj, "rec-pmid-99100104"))
        screen_all(proj, include=["rec-pmid-99100101", "rec-pmid-99100102", brown],
                   exclude=["rec-pmid-99100105"])
        res = M.run_merge(proj, now=NOW)
        self.assertEqual(0, res["exit_code"], res)
        merged = S.read_json(S.path(proj, "merged.json"))
        by_label = dict((s["label"], s) for s in merged["studies"])
        b = by_label["Brown 2018"]
        self.assertEqual("included", b["status"])
        self.assertEqual(["primary", "companion"], [r["role"] for r in b["reports"]])
        self.assertEqual("excluded", by_label["Kim 2017"]["status"])
        self.assertEqual("X4", by_label["Kim 2017"]["eligibility"]["full_text"]["reason_code"])
        self.assertEqual(3, merged["counts"]["studies_included"])
        self.assertEqual(4, merged["counts"]["reports_included"])
        flow = S.read_json(S.path(proj, "prisma_flow.json"))
        self.assertEqual("szk.prisma-flow/v1", flow["schema"])
        self.assertNotIn("dedup_removed", flow, "kanonikus motor-nevek (TERV 13.)")
        self.assertEqual(5, flow["other_methods_identified"])
        self.assertEqual(1, flow["other_methods_excluded"])
        self.assertEqual({"nem közöl releváns kimenetet": 1}, flow["other_methods_excluded_reasons"])
        self.assertEqual(4, flow["included_reports"])
        self.assertEqual(3, flow["included_studies"])
        self.assertEqual(0, flow["identified_databases"])
        chk = engine_prisma.check_flow(PM.engine_input(flow))
        self.assertTrue(chk.ok, chk.findings)
        self.assertEqual({"error": 0, "warning": 0}, dict((k, v) for k, v in chk.summary().items() if k != "info"))

    def test_update_branch_prisma_d1_and_signoff(self):
        proj = build_project(self.tmp)
        accept_all_proposals(proj)
        S.apply_candidate_decision(proj, RV_A, "c0003", "confirm", S.append_decision(
            proj, "candidate_confirm", ("candidate", RV_A + "#c0003"), "confirm", "user:SzK")["decision_id"])
        U.run_update(proj, actor="user:SzK", clients=fake_clients(), now=NOW, today="2026-10-05")
        res = M.run_merge(proj, now=NOW)
        self.assertEqual(4, res["exit_code"])
        so = M.signoff(proj, "user:SzK", now=NOW)
        self.assertFalse(so["ok"])
        self.assertTrue(any("H009" in e["hu"] for e in so["errors"]))
        merged = S.read_json(S.path(proj, "merged.json"))
        self.assertFalse(merged["final"])
        brown = study_of(proj, "rec-pmid-99100103")
        screen_all(proj, include=["rec-pmid-99100101", "rec-pmid-99100102", brown, "rec-pmid-99200201"],
                   exclude=["rec-pmid-99100105", "rec-pmid-99200202", "rec-nct-nct09999999",
                            "rec-doi-%s" % net.sha1_10("10.9999/preprint.xyz")])
        res = M.run_merge(proj, now=NOW)
        self.assertEqual(0, res["exit_code"], res)
        flow = S.read_json(S.path(proj, "prisma_flow.json"))
        self.assertEqual(5, flow["identified_databases"])
        self.assertEqual(1, flow["identified_registers"])
        self.assertEqual(4, flow["screened"])
        self.assertEqual(2, flow["duplicates_removed"], "D1 = A1 + A2 − D3 − B = 6 − 0 − 4")
        self.assertEqual(1, flow["hh"]["already_known"])
        self.assertEqual(1, flow["hh"]["duplicates_within_update"])
        self.assertEqual(5, flow["included_reports"])
        self.assertEqual(4, flow["included_studies"])
        chk = PM.check(flow)
        self.assertTrue(chk["ok"], chk["findings"])
        self.assertEqual(0, chk["summary"]["warning"], chk["findings"])
        merged = S.read_json(S.path(proj, "merged.json"))
        upd_only = [s for s in merged["studies"] if s["found_by"] == ["update_search"]]
        self.assertEqual(4, len(upd_only))
        self.assertEqual(4, merged["counts"]["studies_from_update"])
        smith = [s for s in merged["studies"] if s["label"] == "Smith 2015"][0]
        self.assertEqual(["previous_reviews", "update_search"], smith["found_by"])
        # EP5 (a projektnaplóba is tükröződik, ha van projektnapló)
        from metaelemzes import projekt
        projekt.init(proj, "Teszt-projekt")
        so = M.signoff(proj, "user:SzK", now=NOW)
        self.assertTrue(so["ok"], so)
        self.assertTrue(so["data"]["project_log_id"])
        con = sqlite3.connect(projekt.db_path(proj))
        try:
            rows = con.execute("SELECT agent, actor, decision FROM decision").fetchall()
        finally:
            con.close()
        self.assertTrue(any(r[0] == "planner" and r[1] == "user:SzK" and "EP5" in r[2] for r in rows), rows)
        merged = S.read_json(S.path(proj, "merged.json"))
        self.assertTrue(merged["final"])
        self.assertTrue(all(s["final"] for s in merged["studies"] if s["status"] in ("included", "excluded")))
        assert_valid(self, merged, "merged")
        st = S.load_state(proj)
        self.assertEqual("done", [c for c in st["checkpoints"] if c["id"] == "EP5"][0]["status"])
        # lezárás után változás → H017, final = false
        S.append_decision(proj, "screen", ("record", "rec-pmid-99200201"), "exclude", "user:SzK", level="full_text",
                          reason_code="X5", reason="nem RCT")
        res = M.run_merge(proj, now=NOW)
        merged = S.read_json(S.path(proj, "merged.json"))
        self.assertFalse(merged["final"])
        self.assertTrue(any(w["code"] == "H017" for w in res["warnings"]))

    def test_secondary_verification_and_outcome_export(self):
        proj = build_project(self.tmp)
        accept_all_proposals(proj)
        smith = study_of(proj, "rec-pmid-99100101")
        screen_all(proj, include=["rec-pmid-99100101"])
        M.run_merge(proj, now=NOW)
        merged = S.read_json(S.path(proj, "merged.json"))
        header, rows, skipped = M.outcome_rows(merged, "mortality", for_analysis=True)
        self.assertEqual(list(M.TEMPLATE_COLUMNS), header[:len(M.TEMPLATE_COLUMNS)])
        row = rows[0]
        self.assertEqual("", row["n1"], "ellenőrizetlen másodlagos szám nem kerül a sablonba (N2)")
        self.assertEqual("nem", row["ellenorizve"])
        self.assertEqual(3, skipped, "n1 (A), e1 (A), n1 (B) — mind ellenőrizetlen")
        with self.assertRaises(S.DecisionError):
            M.verify_secondary(proj, smith, "n1", "verified", "user:SzK", primary_locator="p. 3")  # két áttekintés
        with self.assertRaises(S.DecisionError):
            M.verify_secondary(proj, smith, "n1", "verified", "user:SzK", review_id=RV_A)        # lokátor nélkül
        d = M.verify_secondary(proj, smith, "n1", "verified", "user:SzK", review_id=RV_A,
                               primary_locator="rec-pmid-99100101 p. 3, Table 1")
        assert_valid(self, d, "decision")
        M.verify_secondary(proj, smith, "n1", "discrepant", "user:SzK", review_id=RV_B,
                           primary_locator="rec-pmid-99100101 p. 3, Table 1", primary_value=50)
        M.run_merge(proj, now=NOW)
        merged = S.read_json(S.path(proj, "merged.json"))
        st = [s for s in merged["studies"] if s["study_id"] == smith][0]
        stat = dict((blk["review_id"], [v["status"] for v in blk["values"] if v["field"] == "n1"][0])
                    for blk in st["secondary_data"])
        self.assertEqual({RV_A: "verified", RV_B: "discrepant"}, stat)
        _h, rows, _s = M.outcome_rows(merged, "mortality", for_analysis=True)
        self.assertEqual("50", rows[0]["n1"])
        self.assertEqual("", rows[0]["e1"], "a még ellenőrizetlen e1 nem kerül be")
        self.assertEqual("masodlagos_ellenorzott", rows[0]["adat_forras"])
        _h, srows = M.secondary_rows(merged, dict((r["review_id"], r) for r in S.load_reviews(proj)))
        self.assertTrue(all(r["adat_forras"] == "masodlagos" for r in srows))
        self.assertEqual({"igen", "elteres", "nem"} & set(r["ellenorizve"] for r in srows),
                         set(r["ellenorizve"] for r in srows))
        self.assertTrue(any(r["forras_lokator"] and r["bizonyitek"] for r in srows))

    def test_exports_and_to_project_never_overwrites(self):
        proj = build_project(self.tmp)
        accept_all_proposals(proj)
        screen_all(proj, include=["rec-pmid-99100101", "rec-pmid-99100102"])
        M.run_merge(proj, now=NOW)
        res = M.run_export(proj, outcome="Halálozás (30 nap)", to_project=True, prisma=True, now=NOW)
        self.assertTrue(res["ok"], res)
        names = sorted(os.path.basename(f) for f in res["data"]["files"])
        self.assertEqual(sorted(["studies.ma.json", "records.ris", "screening.csv", "masodlagos_adatok.csv",
                                 "halalozas_30_nap_sablon.csv"]), names)
        sm = json.loads(_read(os.path.join(proj, "03_adatok", "studies.json")))
        self.assertEqual("szk.ma.studies/v1", sm["schema"])
        counts = engine_prisma.studies_counts(sm)
        self.assertEqual(2, counts["I"])
        self.assertTrue(os.path.exists(os.path.join(proj, "03_adatok", "halalozas_30_nap.csv")))
        self.assertTrue(os.path.exists(os.path.join(proj, "02_szures", "prisma_flow.json")))
        csv_text = _read(os.path.join(proj, "03_adatok", "halalozas_30_nap.csv"))
        self.assertTrue(csv_text.startswith("study;study_id;year;"))
        self.assertIn("ellenorizve", csv_text.splitlines()[0])
        ris = _read(S.path(proj, "exports", "records.ris"))
        self.assertIn("AN  - PMID:99100101", ris)
        self.assertIn("ER  - ", ris)
        # a felhasználó szerkeszti → újabb export NEM írja felül
        with open(os.path.join(proj, "03_adatok", "studies.json"), "w", encoding="utf-8") as fh:
            fh.write('{"schema": "szk.ma.studies/v1", "studies": []}\n')
        res2 = M.run_export(proj, to_project=True, now=NOW)
        pf = [p for p in res2["data"]["project_files"] if p["file"] == "03_adatok/studies.json"][0]
        self.assertEqual("exists", pf["status"])
        self.assertIn("studies", _read(os.path.join(proj, "03_adatok", "studies.json")))
        self.assertEqual([], json.loads(_read(os.path.join(proj, "03_adatok", "studies.json")))[
            "studies"])
        with self.assertRaises(M.MergeError):
            M.run_export(proj, to_project=True, force=True, now=NOW)
        res3 = M.run_export(proj, to_project=True, force=True, actor="user:SzK", now=NOW)
        self.assertTrue(res3["ok"])
        self.assertTrue(any(d["kind"] == "note" and d["value"] == "overwrite" for d in S.read_decisions(proj)))
        # elemzési export csak lezárás után
        res4 = M.run_export(proj, for_analysis=True, now=NOW)
        self.assertEqual(4, res4["exit_code"])


# =============================================================================================
# PRISMA-leképezés
# =============================================================================================

class TestPrismaMap(_Base):
    SPEC_SAMPLE = {"identified_databases": 420, "identified_registers": 16, "duplicates_removed": 130,
                   "automation_removed": 0, "other_removed": 0, "screened": 306, "excluded_screening": 280,
                   "sought": 26, "not_retrieved": 2, "assessed": 24, "excluded_eligibility": 18,
                   "excluded_eligibility_reasons": {"nem megfelelő populáció": 10, "nem RCT": 8},
                   "other_methods_identified": 60, "other_methods_sought": 52, "other_methods_not_retrieved": 3,
                   "other_methods_assessed": 49, "other_methods_excluded": 9,
                   "other_methods_excluded_reasons": {"nem megfelelő beavatkozás": 5, "nincs releváns kimenet": 4},
                   "included_reports": 46, "included_studies": 38}

    def test_spec_sample_passes_engine(self):
        flow = dict(self.SPEC_SAMPLE, schema="szk.prisma-flow/v1", source={"kind": "headhunter"}, hh={"x": 1})
        chk = PM.check(flow)
        self.assertTrue(chk["ok"])
        self.assertEqual(0, chk["summary"]["error"])
        self.assertEqual(0, chk["summary"]["warning"])
        # ugyanez fájlból, a motor betöltőjével (a ma.py prisma check --json útja)
        fp = os.path.join(self.tmp, "flow.json")
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(flow, fh)
        res = engine_prisma.load(fp)
        self.assertTrue(res.ok)
        self.assertEqual(0, res.summary()["warning"])

    def test_wrong_numbers_give_h011(self):
        proj = build_project(self.tmp)
        accept_all_proposals(proj)
        screen_all(proj, include=["rec-pmid-99100101"])
        M.run_merge(proj, now=NOW)
        flow = S.read_json(S.path(proj, "prisma_flow.json"))
        bad = dict(flow, included_reports=flow["included_reports"] + 5)
        self.assertFalse(PM.check(bad)["ok"])
        # hiányos frissítő keresés → H012 a leképezésben
        upd = {"schema": U.UPDATE_SCHEMA, "model": S.MODEL, "window": {"latest_source_search": "2021-03",
                                                                        "overlap_months": 6, "start_date": "2020-09-01",
                                                                        "end_date": "2026-10-05"},
               "queries": [{"search_id": "s-pubmed-20261005T100000Z", "source": "pubmed", "query": "x",
                            "date_field": "edat", "status": "partial", "count_retrieved": 0}],
               "results": {"retrieved_total": 0, "duplicates_within": 0, "already_known": 0, "new_records": []}}
        f2, warns = PM.build_flow(S.load_state(proj), S.load_reviews(proj), S.read_json(S.path(proj, "studies.json")),
                                  S.read_decisions(proj), upd)
        self.assertTrue(any(w["code"] == "H012" for w in warns))
        self.assertTrue(PM.check(f2)["ok"])

    def test_own_update_mode_previous_and_totals(self):
        proj = build_project(self.tmp, mode="own_update")
        accept_all_proposals(proj)
        U.run_update(proj, actor="user:SzK", clients=fake_clients(), now=NOW, today="2026-10-05")
        screen_all(proj, include=["rec-pmid-99200201", "rec-pmid-99200202"],
                   exclude=["rec-nct-nct09999999", "rec-doi-%s" % net.sha1_10("10.9999/preprint.xyz")])
        res = PM.run_prisma(proj, now=NOW)
        flow = res["flow"]
        self.assertEqual("own_update", flow["hh"]["mode"])
        self.assertEqual(4, flow["previous_studies"])     # Smith, Jones, Kim, Brown (A + B, kiválasztva)
        self.assertEqual(5, flow["previous_reports"])
        self.assertEqual(2, flow["included_studies"])
        self.assertEqual(flow["previous_studies"] + flow["included_studies"], flow["total_studies"])
        self.assertEqual(flow["previous_reports"] + flow["included_reports"], flow["total_reports"])
        self.assertTrue(res["ok"], res["findings"])
        self.assertFalse(any(f["code"] == "P015" for f in res["findings"]))


# =============================================================================================
# parancssor
# =============================================================================================

class TestCLI(_Base):
    def test_envelope_init_status_and_exit_codes(self):
        proj = os.path.join(self.tmp, "cli")
        os.makedirs(proj)
        rc, env = self.run_cli("status", proj)
        self.assertEqual(2, rc)
        self.assertEqual("HH_NOT_INITIALIZED", env["errors"][0]["code"])
        self.assertTrue(set(["ok", "data", "warnings", "errors", "pending", "next", "exit_code", "command"])
                        <= set(env))
        rc, env = self.run_cli("init", proj, "--question", "Hatásos-e az X?", "--population", "adults with Y",
                               "--intervention", "drug X", "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        self.assertEqual("harvest", env["data"]["mode"])
        self.assertIn("X1", env["data"]["exclusion_reasons"][0])
        rc, env = self.run_cli("status", proj)
        self.assertEqual(0, rc, env)
        self.assertIn("find", env["next"])
        rc, env = self.run_cli("confirm", proj, "--target", "rv-pmid-1")
        self.assertEqual(2, rc)
        self.assertIn("--actor", env["errors"][0]["hu"])
        rc, env = self.run_cli("confirm", proj, "--target", "rv-pmid-1", "--actor", "agent:ma-metaheadhunter")
        self.assertEqual(2, rc)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(2, cli_main(["nincs-ilyen-parancs"], stdout=out))
        self.assertIn("usage", err.getvalue())

    def test_full_offline_flow_via_cli(self):
        proj = build_project(self.tmp)
        rc, env = self.run_cli("list", proj, "proposals")
        self.assertEqual(0, rc)
        pids = [r["target"] for r in env["data"]["rows"]]
        self.assertTrue(pids)
        rc, env = self.run_cli("confirm", proj, "--target", ",".join(pids), "--actor", "user:SzK",
                               "--reason", "az áttekintés csoportosítása szerint ugyanaz a vizsgálat")
        self.assertEqual(0, rc, env)
        rc, env = self.run_cli("list", proj, "candidates", "--status", "proposed")
        self.assertEqual([RV_A + "#c0003"], [r["target"] for r in env["data"]["rows"]])
        rc, env = self.run_cli("exclude", proj, "--target", RV_A + "#c0003", "--actor", "user:SzK")
        self.assertEqual(2, rc, "elvetéshez indoklás kell")
        rc, env = self.run_cli("exclude", proj, "--target", RV_A + "#c0003", "--reason", "nem RCT a táblázat szerint",
                               "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        self.assertEqual("rejected", [c for c in S.load_review(proj, RV_A)["candidates"]
                                      if c["cand_id"] == "c0003"][0]["status"])
        rc, env = self.run_cli("merge", proj)
        self.assertEqual(4, rc, env)
        self.assertTrue(any(p["checkpoint"] == "EP4" for p in env["pending"]))
        rc, env = self.run_cli("exclude", proj, "--target", "rec-pmid-99100102", "--actor", "user:SzK")
        self.assertEqual(2, rc, "teljes szöveg szintű kizárásnál kötelező az okkód")
        rc, env = self.run_cli("exclude", proj, "--target", "rec-pmid-99100102", "--reason-code", "X1",
                               "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        levels = [d["level"] for d in env["data"]["decisions"]]
        self.assertEqual(["title_abstract", "full_text"], levels, "a cím/absztrakt továbbengedés is rögzül")
        rc, env = self.run_cli("confirm", proj, "--all-pending", "--min-reviews", "1", "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        batch = [d for d in S.read_decisions(proj) if d.get("batch")]
        self.assertTrue(batch and "min_reviews=1" in batch[-1]["batch"], "a tömeges szűrő a döntésben rögzül")
        rc, env = self.run_cli("merge", proj)
        self.assertEqual(0, rc, env)
        self.assertEqual(2, env["data"]["counts"]["studies_included"])
        rc, env = self.run_cli("prisma", proj)
        self.assertEqual(0, rc, env)
        self.assertIn("ma.py prisma check --json", env["data"]["check_command"])
        rc, env = self.run_cli("signoff", proj, "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        self.assertTrue(env["data"]["final"])
        rc, env = self.run_cli("export", proj, "--outcome", "mortality", "--to-project")
        self.assertEqual(0, rc, env)
        self.assertTrue(os.path.exists(os.path.join(proj, "03_adatok", "mortality.csv")))
        rc, env = self.run_cli("status", proj)
        self.assertIn(rc, (0,), env)
        self.assertTrue(env["data"]["final"])
        rc, env = self.run_cli("verify", proj)
        self.assertEqual(0, rc, env)
        self.assertEqual([], S.verify_chain(S.read_decisions(proj)))
        # szöveges (magyar) kimenet
        out = io.StringIO()
        cli_main(["status", proj], stdout=out)
        self.assertIn("Következő lépés", out.getvalue())

    def test_decide_command_and_resolution_guard(self):
        proj = build_project(self.tmp)
        rc, env = self.run_cli("list", proj, "proposals")
        pid = env["data"]["rows"][0]["target"]
        rc, env = self.run_cli("decide", proj, "--target", pid, "--value", "maybe", "--actor", "user:SzK")
        self.assertEqual(2, rc)
        rc, env = self.run_cli("decide", proj, "--target", pid, "--value", "reject", "--reason", "külön vizsgálat",
                               "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        self.assertEqual("study_link", env["data"]["decisions"][0]["kind"])
        self.assertEqual("reject", env["data"]["decisions"][0]["value"])
        # feloldási javaslatnál a puszta 'accept' nem elég (melyik közlemény? — N1)
        doc = S.read_json(S.path(proj, "studies.json"))
        doc["proposals"].append({"proposal_id": "p-res-0001", "kind": "resolution", "items": [RV_A + "#c0002"],
                                 "certainty": "possible", "rule": "R-title", "status": "pending", "score": 0.85,
                                 "decision_id": None})
        S.write_json_atomic(S.path(proj, "studies.json"), doc)
        n = len(S.read_decisions(proj))
        rc, env = self.run_cli("decide", proj, "--target", "p-res-0001", "--value", "accept", "--actor", "user:SzK")
        self.assertEqual(2, rc)
        self.assertIn("option:", env["errors"][0]["hu"])
        self.assertEqual(n, len(S.read_decisions(proj)), "hibás kérésből nem marad félig rögzített döntés")
        rc, env = self.run_cli("decide", proj, "--target", "p-res-0001", "--value", "option:1", "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        self.assertEqual("id_confirm", env["data"]["decisions"][0]["kind"])
        self.assertIn("resolve", env["next"])

    def test_status_h012_scope(self):
        proj = build_project(self.tmp)
        st = S.load_state(proj)
        S.add_searches(st, [{"search_id": "s-pubmed-20261005T090000Z", "purpose": "review_discovery",
                             "source": "pubmed", "query": "x", "run_at": AT, "count_total": 500,
                             "count_retrieved": 200, "complete": False},
                            {"search_id": "s-europepmc-20261005T091000Z", "purpose": "update_database",
                             "source": "europepmc", "query": "y", "run_at": AT, "count_total": 900,
                             "count_retrieved": 300, "complete": False}])
        S.save_state(proj, st)
        rc, env = self.run_cli("status", proj)
        self.assertEqual([], [e for e in env["errors"] if e["code"] == "H012"],
                         "felváltott frissítés / áttekintés-keresés nem hiba")
        self.assertTrue(any(w["code"] == "H012" for w in env["warnings"]))
        upd = {"schema": U.UPDATE_SCHEMA, "model": S.MODEL,
               "window": {"latest_source_search": None, "overlap_months": 6, "start_date": "2020-01-01",
                          "end_date": "2026-10-05"},
               "queries": [{"search_id": "s-europepmc-20261005T091000Z", "source": "europepmc", "query": "y",
                            "date_field": "CREATION_DATE", "status": "partial"}],
               "results": {"retrieved_total": 300, "duplicates_within": 0, "already_known": 0, "new_records": []}}
        S.write_json_atomic(S.path(proj, "update_search.json"), upd)
        rc, env = self.run_cli("status", proj)
        self.assertEqual(1, rc)
        self.assertTrue(any(e["code"] == "H012" for e in env["errors"]), "a hatályos frissítő keresés hiányos")

    def test_update_dry_run_via_cli_has_no_side_effects(self):
        proj = build_project(self.tmp)
        before = sorted(os.listdir(S.hh_dir(proj)))
        n_dec = len(S.read_decisions(proj))
        rc, env = self.run_cli("update", proj, "--dry-run", "--sources", "pubmed,europepmc,ctgov")
        self.assertEqual(0, rc, env)
        plan = env["data"]["plan"]
        self.assertEqual("2021-03-15", plan["window"]["latest_source_search"])
        self.assertEqual("2020-09-15", plan["window"]["start_date"])
        self.assertIn("edat", plan["queries"]["pubmed"][0]["query"])
        self.assertEqual(n_dec, len(S.read_decisions(proj)))
        self.assertEqual(before, sorted(os.listdir(S.hh_dir(proj))))

    def test_secret_in_env_never_printed(self):
        os.environ["MA_SCOPUS_APIKEY"] = "TESTKEY-SCOPUS-abcdef123456"
        os.environ["MA_CONTACT_EMAIL"] = "kutato@example.org"
        proj = build_project(self.tmp)
        for args in (("status", proj), ("sources", proj), ("merge", proj)):
            out = io.StringIO()
            cli_main(list(args) + ["--json"], stdout=out)
            self.assertNotIn("TESTKEY-SCOPUS", out.getvalue())
            self.assertNotIn("kutato@example.org", out.getvalue())
        for root, _dirs, files in os.walk(S.hh_dir(proj)):
            for fn in files:
                text = _read(os.path.join(root, fn))
                self.assertNotIn("TESTKEY-SCOPUS", text, fn)
                self.assertNotIn("kutato@example.org", text, fn)


if __name__ == "__main__":
    unittest.main()
