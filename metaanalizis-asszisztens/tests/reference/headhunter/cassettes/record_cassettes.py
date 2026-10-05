# -*- coding: utf-8 -*-
"""Metaheadhunter — tesztkazetták rögzítése (csak fejlesztői; élő hálózat kell).

Használat (a ``metaanalizis-asszisztens`` mappából):

    python3 tests/reference/headhunter/cassettes/record_cassettes.py            # élő rögzítés + kézi kazetták
    python3 tests/reference/headhunter/cassettes/record_cassettes.py --only pubmed,europepmc
    python3 tests/reference/headhunter/cassettes/record_cassettes.py --handmade  # csak a kézzel írt kazetták
    MA_SCOPUS_APIKEY=… python3 tests/reference/headhunter/cassettes/record_cassettes.py --only scopus_live

A rögzítő (``metaelemzes.headhunter.net.CassetteRecorder``) a tiltott fejléceket (Authorization, X-ELS-*,
Cookie, Set-Cookie, report-to, nel …) és URL-paramétereket (api_key, apiKey, insttoken, mailto, email, tool)
elhagyja, az absztraktot és a JATS-törzset kivágja (``redactions``), és írás előtt bájtszinten ellenőrzi, hogy
nyilvántartott titok nincs benne. A ``scopus_live`` a felhasználó gépén fut (saját kulccsal) — a Scopus-kazetták
itt kézzel írtak (``origin: hand_made``, ``unverified_live: true``), mert a sandboxból a Scopus csak 401-et ad.

A kézzel írt kazetták rekordjai SZINTETIKUSAK (``10.5555/…`` teszt-DOI-k, ``2-s2.0-00000…`` EID-k,
``W90000…`` OpenAlex-azonosítók, „(fixture)" címek) — nem valós közlemények; csak a válasz-SZERKEZETET
modellezik a hivatalos API-dokumentáció szerint.
"""

from __future__ import print_function

import argparse
import calendar
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from metaelemzes.headhunter import net  # noqa: E402
from metaelemzes.headhunter import pubmed, europepmc, openalex, scopus, ctgov, crossref, finder  # noqa: E402

#: a kézi kazetták rögzítési ideje (determinisztikus)
HANDMADE_AT = "2026-10-05T12:00:00Z"
#: a tesztek rögzített órája (2026-10-05T12:00:00Z)
FIXED_EPOCH = calendar.timegm((2026, 10, 5, 12, 0, 0, 0, 0, 0))

BCG_BLOCKS = [{"concept": "P", "terms": ["tuberculosis"], "mesh": []},
              {"concept": "I", "terms": ["BCG vaccine", "BCG vaccination"], "mesh": []}]
SOY_BLOCKS = [{"concept": "P", "terms": ["inflammation", "C-reactive protein", "inflammatory markers"], "mesh": []},
              {"concept": "I", "terms": ["soy", "isoflavones", "soy isoflavone"], "mesh": []}]


def path(*parts):
    return os.path.join(HERE, *parts)


def rec(name, notes, sub, slim=False):
    """Rögzítő HttpClient (kulcs nélküli környezet: a kazettába így sem kerülhet titok)."""
    env = {k: v for k, v in os.environ.items() if k not in net.SECRET_ENV or k == net.ENV_CONTACT_EMAIL}
    return net.recording(path(sub, name + ".json"), name=sub + "-" + name, notes=notes, env=env, slim=slim)


def record_pubmed():
    with rec("check", "PubMed próbakérés (sources --check): esearch 8309034[pmid]", "pubmed") as h:
        pubmed.Client(h).check()
    with rec("esearch_bcg_reviews", "BCG-áttekintések keresése (TERV 5.1 szűrő), retmax=5", "pubmed") as h:
        q = finder.build_queries(BCG_BLOCKS)["pubmed"]["term"]
        pubmed.Client(h).esearch(q, retmax=5)
    with rec("esummary_kashangura_tameris", "esummary: Kashangura 2019 (31038197) és Tameris 2013 (23391465)",
             "pubmed") as h:
        pubmed.Client(h).esummary(["31038197", "23391465"])
    with rec("efetch_tameris", "efetch 23391465 (absztrakt kivágva): DataBankList NCT00953927, ReferenceList",
             "pubmed") as h:
        pubmed.Client(h).efetch_records(["23391465"])
    with rec("ecitmatch_colditz_tameris", "ecitmatch: Colditz 1994 → 8309034, Tameris 2013 → 23391465, ismeretlen",
             "pubmed") as h:
        pubmed.Client(h).ecitmatch([("jama", 1994, 271, 698, "colditz ga", "ref1"),
                                    ("lancet", 2013, 381, 1021, "tameris md", "ref2"),
                                    ("foo", 1999, 1, 1, "bar", "ref3")])
    with rec("elink_roy2014_refs", "elink pubmed_pubmed_refs: Roy 2014 (25097193) irodalomjegyzéke", "pubmed") as h:
        pubmed.Client(h).elink("25097193", "pubmed_pubmed_refs")
    with rec("idconv_fallback", "idconv tartalék-útja (az NCBI ID-konverter blokkolt): esearch [doi] + esummary",
             "pubmed") as h:
        c = pubmed.Client(h)
        c.pmid_for_doi("10.1002/14651858.CD012915.pub2")
        c.esummary(["31038197"])


def record_europepmc():
    with rec("check", "Europe PMC próbakérés: EXT_ID:8309034 AND SRC:MED (lite)", "europepmc") as h:
        europepmc.Client(h).check()
    with rec("search_bcg_reviews", "BCG-áttekintések (core, 5 találat; absztrakt kivágva)", "europepmc") as h:
        q = finder.build_queries(BCG_BLOCKS)["europepmc"]["query"]
        europepmc.Client(h).search(q, result_type="core", page_size=5, max_results=5).all()
    with rec("lookup_kashangura", "EXT_ID:31038197 AND SRC:MED (core): nyílt teljes szöveg, PMC6488980, licenc",
             "europepmc") as h:
        europepmc.Client(h).lookup_pmid("31038197")
    with rec("fulltext_not_oa_500", "fullTextXML nem nyílt cikkre (PMC8555740): HTTP 500 = nincs nyílt teljes szöveg",
             "europepmc") as h:
        europepmc.Client(h).fulltext_xml("PMC8555740")
    with rec("references_roy2014", "MED/25097193/references (Roy 2014 irodalomjegyzéke)", "europepmc") as h:
        europepmc.Client(h).references("MED", "25097193").all()
    with rec("citations_colditz_p1", "MED/8309034/citations, 1. lap (pageSize=25; a hitCount 1268 körüli)",
             "europepmc") as h:
        europepmc.Client(h).citations("MED", "8309034", page_size=25, max_results=25).all()
    with rec("annotations_tameris", "annotations API: Accession Numbers (NCT00953927 az absztraktban és a Methodsban)",
             "europepmc") as h:
        europepmc.Client(h).accession_numbers("MED", "23391465", subtype="NCT")


def record_openalex():
    with rec("check", "OpenAlex próbakérés: /works/pmid:8309034?select=id (ingyenes) — keret-fejlécekkel", "openalex") as h:
        openalex.Client(h).check()
    with rec("work_pieper2014", "/works/pmid:24581293 (Pieper 2014, CCA) — DEFAULT_SELECT", "openalex") as h:
        openalex.Client(h).work("pmid:24581293", select=openalex.DEFAULT_SELECT)
    with rec("referenced_works_pieper2014", "/works/doi:10.1016/j.jclinepi.2013.11.007?select=id,referenced_works",
             "openalex") as h:
        openalex.Client(h).referenced_works("10.1016/j.jclinepi.2013.11.007")
    with rec("work_404", "/works/doi:10.9999/does-not-exist-xyz → 404", "openalex") as h:
        openalex.Client(h).work("doi:10.9999/does-not-exist-xyz")
    with rec("list_429", "VALÓS 429: listás lekérdezés kulcs nélkül, a közös napi keret elfogyott (Retry-After)",
             "openalex") as h:
        try:
            openalex.Client(h).works(filter={"type": "review", "cites": "W2075477269"}, per_page=5,
                                     max_results=5).all()
        except net.SourceUnavailable as exc:
            print("  openalex list:", exc.status, exc.reset_at)


def record_scopus_401():
    env = {k: v for k, v in os.environ.items() if k not in net.SECRET_ENV}
    fake = "TESTKEY-SCOPUS-0000000000000000"
    for name, hdrs, notes in (
            ("unauthorized_no_key_401", {"Accept": "application/json"},
             "VALÓS 401 kulcs nélkül: AUTHENTICATION_ERROR (service-error alak)"),
            ("unauthorized_invalid_key_401", {"Accept": "application/json", "X-ELS-APIKey": fake},
             "VALÓS 401 érvénytelen kulccsal: APIKEY_INVALID (error-response alak) — a kulcs fejlécben ment, a "
             "kazettából kimaradt")):
        env2 = dict(env)
        env2[net.ENV_SCOPUS_APIKEY] = fake  # a hamis kulcs is titokként redaktálandó
        rec_ = net.CassetteRecorder(path("scopus", name + ".json"), name="scopus-" + name.replace("_", "-"),
                                    notes=notes, env=env2)
        h = net.HttpClient(recorder=rec_, env=env2, use_env_cassette=False)
        try:
            h.get("scopus", scopus.SEARCH_URL, params=[("query", "PMID(%s)" % scopus.PROBE_PMID), ("count", 1),
                                                       ("field", "dc:identifier,eid")], headers=hdrs)
        except net.SourceUnavailable as exc:
            print("  scopus:", name, exc.status)
        rec_.save()


def record_ctgov():
    with rec("check", "ClinicalTrials.gov próbakérés: /api/v2/version", "ctgov") as h:
        ctgov.Client(h).check()
    with rec("study_nct00953927", "NCT00953927 (MVA85A 020): a saját eredményközlését BACKGROUND-ként sorolja", "ctgov") as h:
        ctgov.Client(h).study("NCT00953927", fields="protocolSection")
    with rec("referencepmid_23391465", "AREA[ReferencePMID]23391465 → NCT00953927 és NCT04975178, mindkettő BACKGROUND",
             "ctgov") as h:
        ctgov.Client(h).studies_citing_pmid("23391465")
    with rec("study_404", "NCT99999999 → 404", "ctgov") as h:
        ctgov.Client(h).study("NCT99999999")
    with rec("studies_update_window", "frissítő keresés: AREA[StudyFirstPostDate]RANGE[2025-01-01,MAX] + BCG (3/lap, 5)",
             "ctgov") as h:
        ctgov.Client(h).studies(term="BCG tuberculosis vaccine",
                                advanced="AREA[StudyFirstPostDate]RANGE[2025-01-01,MAX]",
                                fields="protocolSection.identificationModule.nctId", page_size=3, max_results=5).all()


def record_finder():
    with rec("bcg_reviews", "find_reviews(BCG blokkok, max_per_source=6, sources=pubmed,europepmc,openalex) — "
             "a teljes L1-folyamat (OpenAlex: valós 429); slim: affiliációk és PubMed ReferenceList nélkül", "finder",
             slim=True) as h:
        res = finder.find_reviews(BCG_BLOCKS, {"max_per_source": 6}, sources="pubmed,europepmc,openalex", http=h)
        print("  finder bcg:", len(res["candidates"]), [w["code"] for w in res["warnings"]])
    with rec("soy_reviews", "find_reviews(szója/izoflavon + gyulladás, max_per_source=4, pubmed,europepmc); slim",
             "finder", slim=True) as h:
        res = finder.find_reviews(SOY_BLOCKS, {"max_per_source": 4}, sources="pubmed,europepmc", http=h)
        print("  finder soy:", len(res["candidates"]), [w["code"] for w in res["warnings"]])


def record_scopus_live():
    """A FELHASZNÁLÓ gépén, saját kulccsal: a kézi Scopus-kazetták élő megfelelői (``scopus/live_*.json``)."""
    if not net.get_env(net.ENV_SCOPUS_APIKEY):
        print("MA_SCOPUS_APIKEY nincs beállítva — a scopus_live rögzítés kimarad.")
        return
    env = dict(os.environ)
    for name, fn in (
        ("live_check", lambda c: c.check()),
        ("live_search_bcg", lambda c: c.search(scopus.build_review_query(["tuberculosis"], ["BCG vaccine"]),
                                               count=2, max_results=4).all()),
    ):
        r = net.CassetteRecorder(path("scopus", name + ".json"), name="scopus-" + name, env=env,
                                 notes="élő Scopus-rögzítés a felhasználó gépén")
        h = net.HttpClient(recorder=r, env=env, use_env_cassette=False)
        try:
            fn(scopus.Client(h, env=env))
        except net.SourceUnavailable as exc:
            print("  scopus live:", exc.status, exc.explain["hu"])
        r.save()


# ---------------------------------------------------------------------------------------------
# Kézzel írt kazetták (hivatalos dokumentáció alapján; szintetikus rekordok)
# ---------------------------------------------------------------------------------------------

def _doc(name, notes, interactions, unverified=True):
    return {"schema": net.CASSETTE_SCHEMA, "name": name, "recorded_at": HANDMADE_AT, "origin": "hand_made",
            "unverified_live": unverified, "license_note": None, "notes": notes, "interactions": interactions}


def _it(method, url, params, status, body_json=None, body_text=None, headers=None):
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    resp = {"status": status, "headers": headers or {"content-type": "application/json"}}
    if body_json is not None:
        resp["body_json"] = body_json
    else:
        resp["body_text"] = body_text or ""
    return {"request": {"method": method, "url": net.canonical_url(full, {})}, "response": resp, "delay_ms": None}


def _scopus_entry(n, year, title_suffix):
    return {"@_fa": "true", "dc:identifier": "SCOPUS_ID:000000000%d" % n, "eid": "2-s2.0-000000000%d" % n,
            "dc:title": "Synthetic systematic review %s (fixture)" % title_suffix, "dc:creator": "Fixture A.",
            "prism:publicationName": "Fixture Journal of Evidence", "prism:issn": "00000000",
            "prism:volume": str(10 + n), "prism:issueIdentifier": "1", "prism:pageRange": "1-10",
            "prism:coverDate": "%d-03-01" % year, "prism:doi": "10.5555/fixture.scopus.%d" % n,
            "pubmed-id": None if n == 2 else "9000000%d" % n, "citedby-count": str(3 * n), "subtype": "re",
            "subtypeDescription": "Review", "openaccess": "0"}


def handmade():
    out = {}
    fields = scopus.DEFAULT_FIELDS
    q = scopus.build_review_query(["tuberculosis"], ["BCG vaccine"])
    p1 = [("query", q), ("count", 2), ("view", "STANDARD"), ("field", fields), ("cursor", "*")]
    p2 = [("query", q), ("count", 2), ("view", "STANDARD"), ("field", fields), ("cursor", "AoJ0Zml4dHVyZTE=")]

    def sr(entries, cur, nxt, total=3):
        return {"search-results": {"opensearch:totalResults": str(total), "opensearch:startIndex": "0",
                                   "opensearch:itemsPerPage": str(len(entries)),
                                   "opensearch:Query": {"@role": "request", "@searchTerms": q, "@startPage": "0"},
                                   "cursor": {"@current": cur, "@next": nxt}, "entry": entries}}
    quota_headers = {"content-type": "application/json", "x-ratelimit-limit": "20000",
                     "x-ratelimit-remaining": "19990", "x-ratelimit-reset": str(FIXED_EPOCH + 3 * 86400)}
    out[("scopus", "search_cursor_handmade")] = _doc(
        "scopus-search-cursor-handmade",
        "KÉZI (Scopus Search API dokumentáció): cursor-lapozás 2 lapon (count=2, totalResults=3), mezőszűkítés; "
        "szintetikus rekordok — élőben igazolandó (sources --check, test_headhunter_live)",
        [_it("GET", scopus.SEARCH_URL, p1, 200, sr([_scopus_entry(1, 2021, "A"), _scopus_entry(2, 2019, "B")], "*",
                                                   "AoJ0Zml4dHVyZTE="), quota_headers),
         _it("GET", scopus.SEARCH_URL, p2, 200, sr([_scopus_entry(3, 2016, "C")], "AoJ0Zml4dHVyZTE=",
                                                   "AoJ0Zml4dHVyZTI="), quota_headers)])

    def refs(start, items, total=3):
        return {"abstracts-retrieval-response": {"references": {"@total-references": str(total), "reference": items}}}

    def ref(n):
        return {"@id": str(n), "scopus-id": "00000002%02d" % n, "scopus-eid": "2-s2.0-00000002%02d" % n,
                "ce:doi": "10.5555/fixture.ref.%d" % n, "title": "Synthetic trial %d (fixture)" % n,
                "sourcetitle": "Fixture Trials", "prism:coverDate": "%d-01-01" % (2008 + n),
                "author-list": {"author": [{"@seq": "1", "ce:surname": "Alpha%d" % n, "ce:initials": "A.",
                                            "ce:indexed-name": "Alpha%d A." % n}]},
                "citedby-count": str(n), "type": "resolvedReference"}
    eid = "2-s2.0-0000000001"
    rurl = scopus.BASE + "abstract/eid/" + eid
    out[("scopus", "references_ref_view_handmade")] = _doc(
        "scopus-references-ref-view-handmade",
        "KÉZI (Abstract Retrieval API, view=REF): startref/refcount lapozás (refcount=2, @total-references=3); "
        "szintetikus hivatkozások — élőben igazolandó (a REF nézet mezőnevei is)",
        [_it("GET", rurl, [("view", "REF"), ("startref", 1), ("refcount", 2)], 200, refs(1, [ref(1), ref(2)])),
         _it("GET", rurl, [("view", "REF"), ("startref", 3), ("refcount", 2)], 200, refs(3, [ref(3)]))])

    probe = [("query", "PMID(%s)" % scopus.PROBE_PMID), ("count", 1), ("field", "dc:identifier,eid")]
    probe_ok = {"search-results": {"opensearch:totalResults": "1", "entry": [
        {"@_fa": "true", "dc:identifier": "SCOPUS_ID:0000000101", "eid": "2-s2.0-0000000101"}]}}
    ref_url = scopus.BASE + "abstract/eid/2-s2.0-0000000101"
    out[("scopus", "check_entitled_handmade")] = _doc(
        "scopus-check-entitled-handmade",
        "KÉZI: sources --check jogosult kulccsal — keresés 200 + view=REF 200 → search_and_ref (szintetikus EID)",
        [_it("GET", scopus.SEARCH_URL, probe, 200, probe_ok, headers=quota_headers),
         _it("GET", ref_url, [("view", "REF"), ("refcount", 1)], 200, refs(1, [ref(1)], total=1))])
    forbidden = {"service-error": {"status": {"statusCode": "AUTHORIZATION_ERROR",
                                              "statusText": "The requestor is not authorized to access the requested "
                                                            "view or fields of the resource"}}}
    out[("scopus", "check_search_only_403_handmade")] = _doc(
        "scopus-check-search-only-403-handmade",
        "KÉZI: keresés 200, de view=REF 403 AUTHORIZATION_ERROR (nem intézményi hálózat) → search_only",
        [_it("GET", scopus.SEARCH_URL, probe, 200, probe_ok, headers=quota_headers),
         _it("GET", ref_url, [("view", "REF"), ("refcount", 1)], 403, forbidden,
             headers={"content-type": "application/json", "x-els-status": "AUTHORIZATION_ERROR"})])
    quota = {"service-error": {"status": {"statusCode": "QUOTA_EXCEEDED", "statusText": "Quota Exceeded"}}}
    out[("scopus", "quota_429_handmade")] = _doc(
        "scopus-quota-429-handmade",
        "KÉZI: 429 QUOTA_EXCEEDED, X-RateLimit-Reset = rögzített óra + 3 nap (epoch) → rate_limited + reset_at",
        [_it("GET", scopus.SEARCH_URL, probe, 429, quota,
             headers={"content-type": "application/json", "x-els-status": "QUOTA_EXCEEDED - Quota Exceeded",
                      "x-ratelimit-limit": "20000", "x-ratelimit-remaining": "0",
                      "x-ratelimit-reset": str(FIXED_EPOCH + 3 * 86400)})])

    # OpenAlex listás siker kulccsal (itt a közös keret elfogyott → kézi)
    sel = openalex.DEFAULT_SELECT

    def oa_work(n, year):
        return {"id": "https://openalex.org/W90000000%02d" % n, "doi": "https://doi.org/10.5555/fixture.oa.%d" % n,
                "ids": {"openalex": "https://openalex.org/W90000000%02d" % n,
                        "doi": "https://doi.org/10.5555/fixture.oa.%d" % n},
                "display_name": "Synthetic meta-analysis %d (fixture)" % n, "publication_year": year,
                "publication_date": "%d-06-01" % year, "type": "review",
                "primary_location": {"source": {"display_name": "Fixture Reviews", "issn_l": "0000-0000"}},
                "authorships": [{"author": {"display_name": "Fixture Author %d" % n}}],
                "biblio": {"volume": "5", "issue": "2", "first_page": "100", "last_page": "110"},
                "cited_by_count": n, "is_retracted": n == 3, "open_access": {"is_oa": n == 1, "oa_status": "gold"},
                "referenced_works_count": 30}
    flt = "type:review,from_publication_date:2015-01-01"
    base = [("filter", flt), ("search", "(tuberculosis) AND (\"BCG vaccine\")"), ("select", sel), ("per_page", 2)]
    out[("openalex", "list_cursor_handmade")] = _doc(
        "openalex-list-cursor-handmade",
        "KÉZI (OpenAlex dokumentáció): /works lista cursor-lapozással (per_page=2, meta.count=3), kulccsal "
        "(Authorization: Bearer — a kazettában nincs); szintetikus munkák",
        [_it("GET", openalex.BASE + "works", base + [("cursor", "*")], 200,
             {"meta": {"count": 3, "db_response_time_ms": 10, "page": None, "per_page": 2, "next_cursor": "IlsxXSI="},
              "results": [oa_work(1, 2022), oa_work(2, 2020)]}),
         _it("GET", openalex.BASE + "works", base + [("cursor", "IlsxXSI=")], 200,
             {"meta": {"count": 3, "db_response_time_ms": 10, "page": None, "per_page": 2, "next_cursor": None},
              "results": [oa_work(3, 2018)]})])

    # Crossref (itt blokkolt) — DOI-lekérés szintetikus DOI-ra
    cr = {"status": "ok", "message-type": "work", "message": {
        "DOI": "10.5555/fixture.crossref.1", "title": ["Synthetic randomised trial (fixture)"],
        "author": [{"family": "Fixture", "given": "Anna B"}, {"family": "Second", "given": "C"}],
        "issued": {"date-parts": [[2017, 5, 2]]}, "container-title": ["Fixture Medical Journal"],
        "short-container-title": ["Fixture Med J"], "volume": "12", "issue": "3", "page": "200-210",
        "type": "journal-article", "ISSN": ["0000-0000"], "abstract": "<jats:p>synthetic</jats:p>"}}
    redacted, labels = net.redact_json_obj(cr)
    it = _it("GET", crossref.BASE + "works/10.5555/fixture.crossref.1", [], 200, redacted)
    it["response"]["redactions"] = labels
    out[("crossref", "work_handmade")] = _doc(
        "crossref-work-handmade",
        "KÉZI (Crossref REST dokumentáció): /works/{doi} — itt a Crossref blokkolt; szintetikus DOI (10.5555 teszt-előtag)",
        [it])

    # NCBI ID-konverter: a sandboxban a proxy blokkolja (hálózati hiba, nem rögzíthető) → 503-mal modellezve
    out[("pubmed", "idconv_unreachable_handmade")] = _doc(
        "pubmed-idconv-unreachable-handmade",
        "KÉZI: az NCBI PMC ID-konverter elérhetetlen (itt a proxy blokkolja) — tartós 503-mal modellezve, hogy a "
        "pubmed.Client.idconv tartalék-útja (esearch [doi] + esummary) offline is tesztelhető legyen",
        [_it("GET", pubmed.IDCONV_URL, [("ids", "10.1002/14651858.CD012915.pub2"), ("format", "json")], 503,
             body_text="Service Unavailable", headers={"content-type": "text/plain"})], unverified=False)

    # Europe PMC nyílt teljes szöveg: SZINTETIKUS minimális JATS (valós cikkszöveg nem kerül a repóba)
    jats = ('<?xml version="1.0" encoding="UTF-8"?><article xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<front><article-meta><article-id pub-id-type="pmcid">PMC0000001</article-id>'
            '<title-group><article-title>Synthetic review (fixture)</article-title></title-group></article-meta>'
            '</front><body><sec><title>Methods</title><p>We searched MEDLINE, Embase and CENTRAL from inception to '
            '15 March 2020.</p></sec></body><back><ref-list><ref id="r1"><mixed-citation>Alpha A. Synthetic trial. '
            'Fixture J. 2010;1:1-2.</mixed-citation></ref></ref-list></back></article>')
    out[("europepmc", "fulltext_oa_synthetic_handmade")] = _doc(
        "europepmc-fulltext-oa-synthetic-handmade",
        "KÉZI: /PMC0000001/fullTextXML 200 — SZINTETIKUS minimális JATS (a keresési dátum Methods-ból olvasható); "
        "valós teljes szöveg nem kerül a repóba (N4)",
        [_it("GET", europepmc.BASE + "PMC0000001/fullTextXML", [], 200, body_text=jats,
             headers={"content-type": "application/xml"})], unverified=False)
    out[("europepmc", "fulltext_oa_synthetic_handmade")]["license_note"] = "szintetikus szöveg (nem valós cikk)"
    return out


def write_handmade():
    for (sub, name), doc in sorted(handmade().items()):
        text = net.dump_json(doc)
        leaks = net.find_secret_leaks(text, os.environ)
        if leaks:
            raise SystemExit("titok a kézi kazettában: %s" % leaks)
        p = path(sub, name + ".json")
        net._atomic_write(p, text)
        print("  kézi:", os.path.relpath(p, ROOT))


STEPS = {"pubmed": record_pubmed, "europepmc": record_europepmc, "openalex": record_openalex,
         "scopus_401": record_scopus_401, "ctgov": record_ctgov, "finder": record_finder,
         "scopus_live": record_scopus_live}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", default=None, help="vesszővel: " + ",".join(sorted(STEPS)))
    ap.add_argument("--handmade", action="store_true", help="csak a kézi kazetták írása (hálózat nélkül)")
    args = ap.parse_args(argv)
    if args.handmade:
        write_handmade()
        return 0
    steps = args.only.split(",") if args.only else [s for s in sorted(STEPS) if s != "scopus_live"]
    for s in steps:
        print("rögzítés:", s)
        STEPS[s.strip()]()
    if not args.only:
        write_handmade()
    return 0


if __name__ == "__main__":
    sys.exit(main())
