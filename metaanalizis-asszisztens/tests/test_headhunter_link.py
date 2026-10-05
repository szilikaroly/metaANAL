# -*- coding: utf-8 -*-
"""Metaheadhunter — feloldás (L4), rekord-kapcsolás és vizsgálat-klaszterek (L5, EP3), hivatkozási mátrix és CCA
(L6), jogosultsági szűrés és PRISMA-dobozok (L7, EP4) — TERV_metaheadhunter.md 7–10. és 13. fejezet.

Minden teszt OFFLINE fut: hamis forráskliensekkel (a 20.5 szerződés metódusaival), illetve egy integrációs teszt a
repóban lévő, VALÓDI rögzített API-válaszokkal (``tests/reference/headhunter/cassettes/`` — csak olvasva; a
Tameris 2013 / NCT00953927 / NCT04975178 regresszió). A tesztadatok szintetikus azonosítói (99000001…) szándékosan
nem valós cikkek.

Lefedett szerződés-pontok:

- N1/H003/H005: azonosító csak API-válaszból; az áttekintés/felhasználó azonosítója csak API-megerősítéssel; a
  meg nem erősített azonosító nem von össze rekordot (L1);
- 7. fejezet: feloldási sorrend, elfogadási küszöbök (0,80 / 0,90), kétértelmű → ``resolution`` javaslat
  lehetőségekkel, emberi ``id_confirm`` (option:N, pmid:…, reject), elérhetetlen forrás → H014, részleges;
  regiszter-kapcsolat erőssége (DataBank/annotáció erős, CT.gov RESULT megerősítő, BACKGROUND gyenge);
- 8. fejezet: L1 automatikus (visszavonható), L1-ütközés (H007), L2 probable/possible küszöbök és pontszám,
  L3 regiszter és áttekintés-csoportosítás, L4 tipp, stabil javaslat- és vizsgálat-azonosítók, szerepek, címke,
  eseményforrású újraépítés (determinizmus), döntésnapló hash-lánca (H015), csak ember dönt (N3);
- 9. fejezet: CCA (kidolgozott példa 30%), páronként, c < 2 → null, sávhatárok, report vs study szint, wCCA csak
  ellenőrzött mintaelemszámmal, CSV;
- 10./13. fejezet: gépi és ágens-javaslatok (szó szerinti idézet, H004), döntés-validálás (ok kötelező teljes
  szöveg szinten), kettős szűrés + Cohen-kappa, CSV-import, PRISMA-dobozok a motor ``check_flow``-ján átmennek.
"""
import calendar
import copy
import json
import os
import shutil
import tempfile
import unittest

from _helpers import ROOT
from ma_gui import schema_lite
from metaelemzes import prisma as engine_prisma
from metaelemzes.headhunter import dedup, resolve, overlap, eligibility, net

CONTRACTS = os.path.join(ROOT, "metaelemzes", "headhunter", "contracts")
CDIR = os.path.join(ROOT, "tests", "reference", "headhunter", "cassettes")
REG = schema_lite.load_schema_dir(CONTRACTS)
SCHEMA = dict((k, REG["urn:szk:contract:ma.headhunter.%s:1" % k])
              for k in ("studies", "overlap", "decision", "review"))
AT = "2026-10-05T10:00:00Z"
TAMERIS_TITLE = ("Safety and efficacy of MVA85A, a new tuberculosis vaccine, in infants previously vaccinated with "
                 "BCG: a randomised, placebo-controlled phase 2b trial.")


def assert_valid(tc, doc, name):
    errs = schema_lite.validate(doc, SCHEMA[name], REG)
    tc.assertEqual([], [str(e) for e in errs][:10], "%s: sémahiba" % name)


# =============================================================================================
# tesztadat-építők
# =============================================================================================

def idv(value, source="review", via="jats.pub-id", **kw):
    d = {"value": value, "source": source, "via": via, "at": AT}
    d.update(kw)
    return d


def mk_cand(cand_id, text, title=None, first_author=None, year=None, journal=None, ids=None, role="included",
            status="confirmed", group_key=None, label=None, secondary=None, **extra):
    c = {"cand_id": cand_id, "cited_as": {"text": text, "first_author": first_author, "year": year, "title": title,
                                          "journal": journal},
         "study_label_in_review": label, "group_key": group_key, "ids": ids or {}, "rec_id": None,
         "role_in_review": role, "evidence_ids": [], "confidence": "high", "status": status,
         "secondary_data": secondary or [], "decision_ids": []}
    c.update(extra)
    return c


def mk_review(review_id, year, cands, status="selected", first_author="Reviewer"):
    ev = []
    for i, c in enumerate(cands, 1):
        eid = "ev-%s-%04d" % (review_id, i)
        c["evidence_ids"] = [eid]
        for sd in c.get("secondary_data") or []:
            sd.setdefault("evidence_id", eid)
        ev.append({"evidence_id": eid, "review_id": review_id, "kind": "table_row", "strategy": "jats_table",
                   "locator": {"label": "Table 1", "row": i}, "quote": (c["cited_as"]["text"] or "x")[:300],
                   "extracted_by": "tool:headhunter", "at": AT, "confidence": "high"})
    return {"schema": "szk.ma.headhunter.review/v1", "model": "szk.ma.headhunter/v1", "review_id": review_id,
            "status": status, "ids": {}, "bib": {"title": "Review %s" % review_id, "first_author": first_author,
                                                 "year": year},
            "found_by": [], "candidates": cands, "evidence": ev}


def mk_rec(rec_id, ids=None, title=None, first_author=None, year=None, journal=None, origins=None, flags=None,
           registry_links=None, authors=None, last_author=None, pub_types=None, status="active", **extra):
    bib = {"title": title, "first_author": first_author, "year": year, "journal": journal}
    if authors:
        bib["authors"] = authors
    if last_author:
        bib["last_author"] = last_author
    if pub_types:
        bib["pub_types"] = pub_types
    bib = dict((k, v) for k, v in bib.items() if v is not None)
    r = {"rec_id": rec_id, "ids": ids or {}, "bib": bib, "flags": flags or {}, "related": [],
         "origins": origins or [{"route": "review_extraction", "review_id": "rv-pmid-90000001", "cand_id": "c0001",
                                 "search_id": None}],
         "retrievals": [], "resolution": {"status": "resolved", "method": "pub-id", "score": 1.0},
         "status": status, "merged_into": None}
    if registry_links:
        r["registry_links"] = registry_links
    r.update(extra)
    return r


def api(value, source="pubmed", via="pubmed.esummary"):
    return {"value": value, "source": source, "via": via, "at": AT}


def summary(pmid, title, first_author, year, journal="J Synth Med", doi=None, pmcid=None, pub_types=None,
            last_author=None):
    """Egy ``pubmed.summary_record``-alakú dict (a hamis PubMed-kliens válasza)."""
    return {"pmid": pmid, "pmcid": pmcid, "doi": doi, "title": title, "authors": [first_author] if first_author else [],
            "authors_truncated": False, "first_author": first_author, "last_author": last_author, "year": year,
            "journal": journal, "volume": "1", "issue": None, "pages": "1-9",
            "pub_types": pub_types or ["Journal Article"], "language": "eng", "retracted": False,
            "retrieval": {"source": "pubmed", "endpoint": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                          "at": AT, "http_status": 200, "cache_key": None}}


class FakePubMed(object):
    def __init__(self, records=(), esearch=None, ecit=None, efetch=None, down=False):
        self.records = dict((r["pmid"], r) for r in records)
        self.esearch_map = dict(esearch or {})
        self.ecit = dict(ecit or {})
        self.efetch = dict(efetch or {})
        self.down = down
        self.calls = []

    def _check(self):
        if self.down:
            raise net.SourceUnavailable("pubmed", "unreachable")

    def esummary(self, pmids):
        self._check()
        self.calls.append(("esummary", tuple(pmids)))
        return [copy.deepcopy(self.records[p]) for p in pmids if p in self.records]

    def esearch(self, term, retmax=100, **kw):
        self._check()
        self.calls.append(("esearch", term))
        import re
        dois = re.findall(r'"([^"]+)"\[doi\]', term)
        if dois:
            ids = [p for p, r in sorted(self.records.items()) if r.get("doi") in dois]
        else:
            ids = list(self.esearch_map.get(term, []))
        return {"count": len(ids), "ids": ids[:retmax], "querytranslation": None, "warnings": []}

    def ecitmatch_detail(self, rows):
        self._check()
        self.calls.append(("ecitmatch", tuple(tuple(r) for r in rows)))
        out = {}
        for journal, year, volume, page, author, key in rows:
            p = self.ecit.get((journal, int(year), str(volume), str(page)))
            out[key] = {"pmid": p, "status": "found" if p else "not_found", "raw": p or "NOT_FOUND"}
        return out

    def efetch_records(self, pmids):
        self._check()
        self.calls.append(("efetch", tuple(pmids)))
        return [copy.deepcopy(self.efetch[p]) for p in pmids if p in self.efetch]


class FakeEPMC(object):
    def __init__(self, by_pmid=None, by_doi=None, search=None, annotations=None):
        self.by_pmid = dict(by_pmid or {})
        self.by_doi = dict(by_doi or {})
        self.search_map = dict(search or {})
        self.ann = dict(annotations or {})
        self.calls = []

    def lookup_pmid(self, pmid, result_type="core"):
        self.calls.append(("lookup_pmid", pmid))
        return copy.deepcopy(self.by_pmid.get(pmid))

    def lookup_doi(self, doi, result_type="core"):
        self.calls.append(("lookup_doi", doi))
        return copy.deepcopy(self.by_doi.get(doi))

    def lookup_pmcid(self, pmcid, result_type="core"):
        self.calls.append(("lookup_pmcid", pmcid))
        return None

    def search(self, query, result_type="core", page_size=100, max_results=None, **kw):
        self.calls.append(("search", query))
        return list(copy.deepcopy(self.search_map.get(query, [])))

    def accession_numbers(self, src, ext_id, subtype=None):
        self.calls.append(("annotations", ext_id))
        return list(self.ann.get(ext_id, []))


class FakeCtgov(object):
    def __init__(self, studies=None, citing=None):
        self.studies = dict(studies or {})
        self.citing = dict(citing or {})
        self.calls = []

    def study(self, nct, fields=None):
        self.calls.append(("study", nct))
        if nct not in self.studies:
            return None
        return {"protocolSection": {"identificationModule": {"nctId": nct, "briefTitle": self.studies[nct]},
                                    "statusModule": {"studyFirstPostDateStruct": {"date": "2009-07-31"}}}}

    def studies_citing_pmid(self, pmid):
        self.calls.append(("citing", pmid))
        return list(self.citing.get(pmid, []))


class DownClient(object):
    """Minden hívásra ``SourceUnavailable`` (pl. a sandboxból blokkolt Crossref)."""

    def __init__(self, name):
        self.name = name
        self.calls = 0

    def __getattr__(self, attr):
        def f(*a, **k):
            self.calls += 1
            raise net.SourceUnavailable(self.name, "unreachable")
        return f


class TmpProject(object):
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="hh-link-")
        self.hd = dedup.hh_dir(self.dir)
        os.makedirs(os.path.join(self.hd, "reviews"))

    def write_reviews(self, reviews):
        for r in reviews:
            dedup.write_json_atomic(os.path.join(self.hd, "reviews", r["review_id"] + ".json"), r)

    def write_state(self, state):
        dedup.write_json_atomic(os.path.join(self.hd, "state.json"), state)

    def studies(self):
        return dedup.load_studies(self.dir)

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


# =============================================================================================
# normalizálás, hasonlóság, szabályok
# =============================================================================================

class TestNormalizeAndRules(unittest.TestCase):

    def test_norm_text_transliteration_and_stopwords(self):
        self.assertEqual("gotzsche aerae ss d l i", dedup.norm_text("Gøtzsche Æræ ß đ ł ı"))
        self.assertEqual("effect soy crp adults", dedup.norm_title("The effect of soy on CRP in adults"))
        self.assertEqual(1.0, dedup.title_similarity("Effect of soy on CRP.", "effect of SOY on crp"))
        self.assertIsNone(dedup.title_similarity(None, "x"))

    def test_surnames(self):
        cases = {"Tameris MD": "Tameris", "van Nielen, M.": "van Nielen", "J. W. Anderson": "Anderson",
                 "Smith-Jones A": "Smith-Jones", "Ng SC": "Ng", "LEE JH": "LEE", "Tameris M, et al.": "Tameris"}
        for raw, want in cases.items():
            self.assertEqual(want, dedup.surname_display(raw), raw)
        self.assertEqual("vannielen", dedup.norm_surname("van Nielen M"))
        self.assertEqual("gotzsche", dedup.norm_surname("Gøtzsche PC"))

    def test_l2_thresholds_and_score(self):
        f = {"title_sim": 0.90, "first_author_match": True, "year_diff": 1, "journal_match": True}
        self.assertEqual(("probable", "L2-title-author-year"), dedup.classify_l2(f))
        self.assertAlmostEqual(0.88, dedup.l2_score(f))  # 0,90 + 0,03 − 0,05
        f2 = dict(f, title_sim=0.89)
        self.assertEqual("possible", dedup.classify_l2(f2)[0])
        f3 = {"title_sim": 0.85, "first_author_match": False, "year_diff": 1}
        self.assertEqual("possible", dedup.classify_l2(f3)[0])  # cím ≥ 0,80 ÉS év ≤ 1
        f4 = {"title_sim": 0.85, "first_author_match": False, "year_diff": 2}
        self.assertEqual((None, None), dedup.classify_l2(f4))
        f5 = {"title_sim": 0.79, "first_author_match": True, "year_diff": 0}
        self.assertEqual((None, None), dedup.classify_l2(f5))
        self.assertEqual(("possible", "L2-author-year-notitle"),
                         dedup.classify_l2({"title_sim": None, "first_author_match": True, "year_diff": 0}))
        self.assertEqual(1.0, dedup.l2_score({"title_sim": 1.0, "journal_match": True, "year_diff": 0}))
        self.assertEqual(0.0, dedup.l2_score({"title_sim": 0.1, "year_diff": 5}))

    def test_rec_id_forms_and_trust(self):
        self.assertEqual("rec-pmid-23391465", dedup.rec_id_for({"pmid": api("23391465"), "doi": api("10.1/x")}))
        self.assertTrue(dedup.rec_id_for({"doi": api("10.1016/S0140-6736(13)60177-4")}).startswith("rec-doi-"))
        self.assertEqual("rec-nct-nct00953927", dedup.rec_id_for({"nct": api("NCT00953927", "ctgov", "ctgov.study")}))
        # meg nem erősített (review) azonosító nem ad rec_id-t (N1)
        rid = dedup.rec_id_for({"pmid": idv("23391465")}, "Tameris MD et al. Lancet 2013;381:1021-8.")
        self.assertTrue(rid.startswith("rec-x-"))
        self.assertEqual("rec-pmid-23391465", dedup.rec_id_for({"pmid": idv("23391465", confirmed_by="pubmed.esummary")}))
        # rövid címke: az eredet is a hash része (két áttekintés 'Smith 2010'-je nem vonódik össze)
        a = dedup.rec_id_for({}, "Smith 2010", salt="rv-a#c0001")
        b = dedup.rec_id_for({}, "Smith 2010", salt="rv-b#c0001")
        self.assertNotEqual(a, b)
        long_text = "Smith J, Doe A. A long enough citation text about soy. J Synth Med 2010;1:1-9."
        self.assertEqual(dedup.rec_id_for({}, long_text, salt="rv-a#c1"), dedup.rec_id_for({}, long_text, salt="rv-b#c9"))


# =============================================================================================
# döntésnapló és zár
# =============================================================================================

class TestDecisionLog(unittest.TestCase):

    def setUp(self):
        self.p = TmpProject()

    def tearDown(self):
        self.p.cleanup()

    def test_append_chain_schema_and_tamper(self):
        d1 = dedup.append_decision(self.p.dir, "duplicate_accept", ("proposal", "p-l2-0001"), "accept", "user:SzK",
                                   now=AT, reason="ugyanaz", kb_refs=["D-S04-101"])
        d2 = dedup.append_decision(self.p.dir, "study_link", ("proposal", "p-l3-0001"), "accept", "user:SzK", now=AT)
        self.assertEqual(dedup.ZERO_SHA, d1["prev_sha256"])
        self.assertEqual(d1["sha256"], d2["prev_sha256"])
        self.assertEqual("d-20261005T100000Z-0001", d1["decision_id"])
        self.assertEqual("d-20261005T100000Z-0002", d2["decision_id"])
        ds = dedup.read_decisions(self.p.dir)
        self.assertEqual([], dedup.verify_decision_chain(ds))
        for d in ds:
            assert_valid(self, d, "decision")
        # manipulált sor → H015
        ds[0]["value"] = "reject"
        probs = dedup.verify_decision_chain(ds)
        self.assertTrue(any(x["problem"] == "sha256" for x in probs))

    def test_state_counter_respected_and_validation(self):
        self.p.write_state({"counters": {"decision_seq": 41}})
        d = dedup.append_decision(self.p.dir, "note", ("project", "x"), "megjegyzés", "user:SzK", now=AT)
        self.assertTrue(d["decision_id"].endswith("-0042"))
        with self.assertRaises(dedup.DecisionError):
            dedup.make_decision("nope", "project", "x", "v", "user:a")
        with self.assertRaises(dedup.DecisionError):
            dedup.make_decision("note", "project", "x", "v", "somebody")
        with self.assertRaises(dedup.DecisionError):
            dedup.make_decision("note", "project", "x", "v" * 81, "user:a")
        with self.assertRaises(dedup.DecisionError):
            dedup.make_decision("screen", "record", "r", "exclude", "user:a", reason_code="E1")

    def test_fallback_writer_when_state_module_missing(self):
        orig = dedup._state_module
        dedup._state_module = lambda: None
        try:
            d1 = dedup.append_decision(self.p.dir, "duplicate_accept", ("proposal", "p-l2-0001"), "accept", "user:SzK",
                                       now=AT)
            with self.assertRaises(dedup.DecisionError):
                dedup.append_decision(self.p.dir, "duplicate_accept", ("proposal", "p-l2-0002"), "accept",
                                      "agent:ma-metaheadhunter", now=AT)
            ds = dedup.read_decisions(self.p.dir)
            self.assertEqual([d1["decision_id"]], [d["decision_id"] for d in ds])
            # sérült lánc → nem fűz hozzá (H015)
            path = os.path.join(self.p.hd, "decisions.jsonl")
            with open(path, encoding="utf-8") as fh:
                line = json.loads(fh.read())
            line["value"] = "reject"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
            with self.assertRaises(dedup.DecisionError):
                dedup.append_decision(self.p.dir, "note", ("project", "x"), "y", "user:SzK", now=AT)
        finally:
            dedup._state_module = orig

    def test_hash_rule_identical_to_state_module(self):
        st = dedup._state_module()
        if st is None:
            self.skipTest("a state modul nem érhető el")
        kw = dict(seq=7, now=AT, level="full_text", reason_code="X2", reason="Nem RCT.", kb_refs=["D-S04-104"])
        a = dedup.make_decision("screen", "record", "rec-pmid-99000001", "exclude", "user:SzK", **kw)
        b = st.make_decision("screen", "record", "rec-pmid-99000001", "exclude", "user:SzK", **kw)
        self.assertEqual(a, b)
        # a PHI-szűrés a state modulon át érvényesül a dedup-hívásokra is (N9)
        with self.assertRaises(dedup.DecisionError):
            dedup.append_decision(self.p.dir, "note", ("project", "x"), "y", "user:SzK", now=AT,
                                  reason="kapcsolat: beteg@example.org")

    def test_effective_decisions_supersedes(self):
        a = dedup.make_decision("duplicate_accept", "proposal", "p-l2-0001", "accept", "user:a", seq=1, now=AT)
        b = dedup.make_decision("duplicate_reject", "proposal", "p-l2-0001", "reject", "user:a", seq=2, now=AT,
                                supersedes=a["decision_id"])
        eff = dedup.effective_decisions([a, b])
        self.assertEqual("duplicate_reject", eff[("proposal", "p-l2-0001")]["kind"])
        c = dedup.make_decision("duplicate_accept", "proposal", "p-l2-0001", "accept", "user:a", seq=3, now=AT,
                                supersedes=b["decision_id"])
        self.assertEqual("duplicate_accept", dedup.effective_decisions([a, b, c])[("proposal", "p-l2-0001")]["kind"])

    def test_lock_reentrant_stale_and_busy(self):
        with dedup.ProjectLock(self.p.dir):
            with dedup.ProjectLock(self.p.dir):
                self.assertTrue(os.path.exists(os.path.join(self.p.hd, ".lock")))
            self.assertTrue(os.path.exists(os.path.join(self.p.hd, ".lock")))
        self.assertFalse(os.path.exists(os.path.join(self.p.hd, ".lock")))
        # idegen, friss zár → LockError
        path = os.path.join(self.p.hd, ".lock")
        with open(path, "w") as fh:
            fh.write("999999 %s\n" % AT)
        with self.assertRaises(dedup.LockError):
            with dedup.ProjectLock(self.p.dir, timeout=0.3, poll=0.05):
                pass
        # elavult (10 percnél régebbi) zár → eltávolítható
        old = os.path.getmtime(path) - 3600
        os.utime(path, (old, old))
        lk = dedup.ProjectLock(self.p.dir, timeout=1)
        with lk:
            pass
        self.assertTrue(lk.stale_removed)


# =============================================================================================
# L5 — kapcsolás
# =============================================================================================

def rv_origin(review_id, cand_id):
    return [{"route": "review_extraction", "review_id": review_id, "cand_id": cand_id, "search_id": None}]


class TestLinkL1L2(unittest.TestCase):

    def records(self):
        return [
            mk_rec("rec-pmid-99000001", {"pmid": api("99000001"), "doi": api("10.5555/syn.1")},
                   "Soy isoflavones and C-reactive protein in postmenopausal women", "Kovacs A", 2015, "J Synth Med",
                   origins=rv_origin("rv-pmid-90000001", "c0001")),
            mk_rec("rec-doi-" + dedup._sha1_10("10.5555/syn.1"), {"doi": api("10.5555/syn.1", "crossref", "crossref.works")},
                   "Soy isoflavones and C-reactive protein in postmenopausal women.", "Kovacs A", 2015, "J Synth Med",
                   origins=rv_origin("rv-pmid-90000002", "c0001")),
            # L2 probable: nincs közös azonosító, cím ≈, első szerző =, év ±1
            mk_rec("rec-pmid-99000003", {"pmid": api("99000003")},
                   "Effect of soy protein on interleukin-6: a randomized trial", "Nagy B", 2016, "Synth Nutr",
                   origins=rv_origin("rv-pmid-90000001", "c0002")),
            mk_rec("rec-x-aaaaaaaaaa", {"pmid": idv("99000003")},  # meg nem erősített review-PMID: L1 NEM vonja össze
                   "Effect of soy protein on interleukin 6 - a randomised trial", "Nagy B", 2017, "Synth Nutr",
                   origins=rv_origin("rv-pmid-90000002", "c0002"),
                   resolution={"status": "unresolved", "method": None, "score": None}),
        ]

    def reviews(self):
        return [mk_review("rv-pmid-90000001", 2018, [mk_cand("c0001", "Kovacs 2015", rec_id="rec-pmid-99000001"),
                                                      mk_cand("c0002", "Nagy 2016", rec_id="rec-pmid-99000003")]),
                mk_review("rv-pmid-90000002", 2020, [
                    mk_cand("c0001", "Kovacs 2015", rec_id="rec-doi-" + dedup._sha1_10("10.5555/syn.1")),
                    mk_cand("c0002", "Nagy 2017", rec_id="rec-x-aaaaaaaaaa")])]

    def test_l1_auto_and_revocable_l2_pending(self):
        recs, revs = self.records(), self.reviews()
        doc = dedup.link(recs, revs, [], now=AT)
        assert_valid(self, doc, "studies")
        by = dict((r["rec_id"], r) for r in doc["records"])
        doi_rec = "rec-doi-" + dedup._sha1_10("10.5555/syn.1")
        self.assertEqual("merged_into", by[doi_rec]["status"])
        self.assertEqual("rec-pmid-99000001", by[doi_rec]["merged_into"])
        l1 = [p for p in doc["proposals"] if p["rule"].startswith("L1-")]
        self.assertEqual(1, len(l1))
        self.assertEqual(("same_report", "certain", "auto_applied"), (l1[0]["kind"], l1[0]["certainty"], l1[0]["status"]))
        self.assertEqual("p-l1-0001", l1[0]["proposal_id"])
        # az unconfirmed review-PMID nem von össze — L2 javaslat lesz belőle
        self.assertEqual("active", by["rec-x-aaaaaaaaaa"]["status"])
        l2 = [p for p in doc["proposals"] if p["rule"].startswith("L2-")]
        self.assertEqual(1, len(l2))
        self.assertEqual("pending", l2[0]["status"])
        self.assertEqual(["rec-pmid-99000003", "rec-x-aaaaaaaaaa"], l2[0]["items"])
        self.assertIn(l2[0]["certainty"], ("probable", "possible"))
        # a Kovacs-vizsgálatot mindkét áttekintés bevonta (proveniencia a beolvasztott rekordon át)
        st = [s for s in doc["studies"] if s["primary_rec_id"] == "rec-pmid-99000001"][0]
        self.assertEqual(["rv-pmid-90000001", "rv-pmid-90000002"], st["reviews"])
        self.assertEqual("Kovacs 2015", st["label"])
        # H020: minden aktív rekord pontosan egy vizsgálatban
        members = [rp["rec_id"] for s in doc["studies"] for rp in s["reports"]]
        active = [r["rec_id"] for r in doc["records"] if r["status"] == "active"]
        self.assertEqual(sorted(active), sorted(members))
        self.assertEqual([], dedup.membership_problems(doc))
        broken = copy.deepcopy(doc)
        broken["studies"][0]["reports"].append(copy.deepcopy(broken["studies"][1]["reports"][0]))
        self.assertEqual(["multiple_studies"], [x["problem"] for x in dedup.membership_problems(broken)])
        # visszavonás: L1 elutasítva → nincs összevonás
        rej = dedup.make_decision("duplicate_reject", "proposal", "p-l1-0001", "reject", "user:SzK", seq=1, now=AT)
        doc2 = dedup.link(doc["records"], revs, [rej], prior=doc, now=AT)
        by2 = dict((r["rec_id"], r) for r in doc2["records"])
        self.assertEqual("active", by2[doi_rec]["status"])
        self.assertEqual("rejected", [p for p in doc2["proposals"] if p["proposal_id"] == "p-l1-0001"][0]["status"])
        # L2 elfogadása → összevonás, a javaslat-azonosító stabil
        acc = dedup.make_decision("duplicate_accept", "proposal", l2[0]["proposal_id"], "accept", "user:SzK", seq=2,
                                  now=AT)
        doc3 = dedup.link(doc2["records"], revs, [rej, acc], prior=doc2, now=AT)
        by3 = dict((r["rec_id"], r) for r in doc3["records"])
        self.assertEqual("rec-pmid-99000003", by3["rec-x-aaaaaaaaaa"]["merged_into"])
        self.assertEqual("accepted", [p for p in doc3["proposals"] if p["proposal_id"] == l2[0]["proposal_id"]][0]["status"])
        assert_valid(self, doc3, "studies")

    def test_id_conflict_not_merged_until_human(self):
        recs = [mk_rec("rec-pmid-99000011", {"pmid": api("99000011"), "doi": api("10.5555/conf")}, "Trial A", "Toth C", 2012),
                mk_rec("rec-pmid-99000012", {"pmid": api("99000012"), "doi": api("10.5555/conf")}, "Trial A", "Toth C", 2012)]
        doc = dedup.link(recs, [], [], now=AT)
        p = [x for x in doc["proposals"] if x["kind"] == "id_conflict"][0]
        self.assertEqual("pending", p["status"])
        self.assertEqual(["pmid:99000011|99000012"], p["features"]["conflicting_ids"])
        self.assertTrue(all(r["status"] == "active" for r in doc["records"]))
        self.assertEqual(1, doc["summary"]["id_conflicts_open"])
        assert_valid(self, doc, "studies")
        acc = dedup.make_decision("duplicate_accept", "proposal", p["proposal_id"], "accept", "user:SzK", seq=1, now=AT)
        doc2 = dedup.link(doc["records"], [], [acc], prior=doc, now=AT)
        self.assertEqual(1, sum(1 for r in doc2["records"] if r["status"] == "merged_into"))
        self.assertEqual("accepted", [x for x in doc2["proposals"] if x["proposal_id"] == p["proposal_id"]][0]["status"])

    def test_deterministic_and_stable_ids_when_records_added(self):
        recs, revs = self.records(), self.reviews()
        a = dedup.link(recs, revs, [], now=AT)
        b = dedup.link(recs, revs, [], prior=a, now=AT)
        self.assertEqual(dedup.dump_json(a), dedup.dump_json(b))
        # új rekord érkezik (frissítő keresés) — a meglévő javaslatok és vizsgálatok azonosítója nem változik
        extra = mk_rec("rec-pmid-99000099", {"pmid": api("99000099")}, "Another soy trial on adiponectin", "Abel Z", 2021,
                       origins=[{"route": "update_search", "review_id": None, "cand_id": None,
                                 "search_id": "s-pubmed-20261005T101200Z"}])
        c = dedup.link(recs + [extra], revs, [], prior=b, now=AT)
        old = dict((tuple(p["items"]), p["proposal_id"]) for p in b["proposals"])
        new = dict((tuple(p["items"]), p["proposal_id"]) for p in c["proposals"])
        for k, v in old.items():
            self.assertEqual(v, new[k])
        old_st = dict((s["primary_rec_id"], s["study_id"]) for s in b["studies"])
        new_st = dict((s["primary_rec_id"], s["study_id"]) for s in c["studies"])
        for k, v in old_st.items():
            self.assertEqual(v, new_st[k])
        self.assertEqual("st-%04d" % (len(old_st) + 1), new_st["rec-pmid-99000099"])


class TestLinkStudies(unittest.TestCase):

    def test_review_grouping_l3_link_roles_and_split(self):
        recs = [mk_rec("rec-pmid-99000021", {"pmid": api("99000021")}, "Main results of the SYN-1 trial", "Kiss D", 2014,
                       origins=rv_origin("rv-pmid-90000003", "c0001")),
                mk_rec("rec-pmid-99000022", {"pmid": api("99000022")}, "Five-year follow-up of the SYN-1 cohort", "Varga E",
                       2019, origins=rv_origin("rv-pmid-90000003", "c0002")),
                mk_rec("rec-pmid-99000023", {"pmid": api("99000023")}, "Protocol for the SYN-1 trial", "Kiss D", 2012,
                       flags={"protocol": True}, origins=rv_origin("rv-pmid-90000003", "c0003"))]
        rv = mk_review("rv-pmid-90000003", 2021, [
            mk_cand("c0001", "Kiss 2014", group_key="CD000001-bbs2-0001", rec_id="rec-pmid-99000021"),
            mk_cand("c0002", "Varga 2019", group_key="CD000001-bbs2-0001", rec_id="rec-pmid-99000022",
                    role="included_companion"),
            mk_cand("c0003", "Kiss 2012", group_key="CD000001-bbs2-0001", rec_id="rec-pmid-99000023")])
        doc = dedup.link(recs, [rv], [], now=AT)
        l3 = [p for p in doc["proposals"] if p["rule"] == "L3-review-grouping"]
        self.assertEqual(1, len(l3))
        self.assertEqual(("same_study", "probable", "pending"), (l3[0]["kind"], l3[0]["certainty"], l3[0]["status"]))
        self.assertEqual(["rv-pmid-90000003"], l3[0]["reviews"])
        self.assertEqual(3, len(doc["studies"]))  # döntés előtt mindegyik külön vizsgálat (H020 ép)
        # tömeges jóváhagyás áttekintésenként
        sel = dedup.select_proposals(doc["proposals"], kind="same_study", review_id="rv-pmid-90000003")
        self.assertEqual([l3[0]["proposal_id"]], [p["proposal_id"] for p in sel])
        link = dedup.make_decision("study_link", "proposal", l3[0]["proposal_id"], "accept", "user:SzK", seq=1, now=AT)
        doc2 = dedup.link(doc["records"], [rv], [link], prior=doc, now=AT)
        self.assertEqual(1, len(doc2["studies"]))
        st = doc2["studies"][0]
        roles = dict((r["rec_id"], (r["role"], r["role_source"])) for r in st["reports"])
        self.assertEqual(("primary", "rule"), roles["rec-pmid-99000021"])
        self.assertEqual(("companion", "review_grouping"), roles["rec-pmid-99000022"])
        self.assertEqual(("protocol", "rule"), roles["rec-pmid-99000023"])
        self.assertEqual("Kiss 2014", st["label"])
        self.assertEqual(min(s["study_id"] for s in doc["studies"]), st["study_id"])  # a legkisebb régi azonosító marad
        self.assertIn(link["decision_id"], st["decision_ids"])
        assert_valid(self, doc2, "studies")
        # szerep kézi beállítása, majd a követő-közlemény kiválasztása a vizsgálatból
        role = dedup.make_decision("role_set", "record", "rec-pmid-99000022", "secondary", "user:SzK", seq=2, now=AT)
        split = dedup.make_decision("study_split", "record", "rec-pmid-99000023", "split", "user:SzK", seq=3, now=AT)
        doc3 = dedup.link(doc2["records"], [rv], [link, role, split], prior=doc2, now=AT)
        self.assertEqual(2, len(doc3["studies"]))
        big = [s for s in doc3["studies"] if len(s["reports"]) == 2][0]
        self.assertEqual(st["study_id"], big["study_id"])
        self.assertIn(("rec-pmid-99000022", "secondary", "human"),
                      [(r["rec_id"], r["role"], r["role_source"]) for r in big["reports"]])
        other = [s for s in doc3["studies"] if s is not big][0]
        self.assertGreater(int(other["study_id"][3:]), max(int(s["study_id"][3:]) for s in doc2["studies"]))

    def test_registry_strength_tameris_regression(self):
        """TERV 3.1/19.3: a CT.gov BACKGROUND típus nem gyengítheti a közlemény saját regisztrációs nyilatkozatát,
        és az NCT04975178 (amely ugyanezt a PMID-et BACKGROUND-ként idézi) NEM kerülhet ugyanabba a vizsgálatba."""
        tameris = mk_rec("rec-pmid-23391465", {"pmid": api("23391465"), "nct": api("NCT00953927", "pubmed",
                                                                                     "pubmed.efetch.databank")},
                         TAMERIS_TITLE, "Tameris MD", 2013, "Lancet",
                         registry_links=[
                             {"id": "NCT00953927", "kind": "nct", "strength": "strong", "source": "pubmed",
                              "via": "pubmed.efetch.databank", "at": AT},
                             {"id": "NCT00953927", "kind": "nct", "strength": "weak", "source": "ctgov",
                              "via": "ctgov.references", "at": AT, "ref_type": "BACKGROUND"},
                             {"id": "NCT04975178", "kind": "nct", "strength": "weak", "source": "ctgov",
                              "via": "ctgov.references", "at": AT, "ref_type": "BACKGROUND"}],
                         origins=rv_origin("rv-pmid-31038197", "c0006"))
        reg1 = mk_rec("rec-nct-nct00953927", {"nct": api("NCT00953927", "ctgov", "ctgov.study")},
                      "A Phase IIb trial of MVA85A in infants", None, 2009, flags={"registry_record": True},
                      origins=[{"route": "update_search", "review_id": None, "cand_id": None,
                                "search_id": "s-ctgov-20261005T101200Z"}])
        reg2 = mk_rec("rec-nct-nct04975178", {"nct": api("NCT04975178", "ctgov", "ctgov.study")},
                      "MTBVAC in newborns", None, 2021, flags={"registry_record": True},
                      origins=[{"route": "update_search", "review_id": None, "cand_id": None,
                                "search_id": "s-ctgov-20261005T101200Z"}])
        doc = dedup.link([tameris, reg1, reg2], [], [], now=AT)
        by_rule = {}
        for p in doc["proposals"]:
            by_rule.setdefault(p["rule"], []).append(p)
        self.assertEqual([["rec-nct-nct00953927", "rec-pmid-23391465"]], [p["items"] for p in by_rule["L3-registry"]])
        self.assertEqual("probable", by_rule["L3-registry"][0]["certainty"])
        weak = by_rule.get("L4-registry-weak") or []
        self.assertEqual([["rec-nct-nct04975178", "rec-pmid-23391465"]], [p["items"] for p in weak])
        self.assertEqual("possible", weak[0]["certainty"])
        # gyenge kapcsolat nem kerül az azonosítók közé
        self.assertEqual("NCT00953927", tameris["ids"]["nct"]["value"])
        # elfogadás nélkül semmi nincs összekapcsolva
        self.assertEqual(3, len(doc["studies"]))

    def test_l4_author_tip_and_superseded(self):
        recs = [mk_rec("rec-pmid-99000031", {"pmid": api("99000031")}, "Soy and lipids: the ALPHA trial", "Horvath F",
                       2010, last_author="Szabo G"),
                mk_rec("rec-pmid-99000032", {"pmid": api("99000032")}, "Bone density outcomes of soy supplementation",
                       "Horvath F", 2013, last_author="Szabo G"),
                mk_rec("rec-pmid-99000033", {"pmid": api("99000033")}, "Bone density outcomes of soy supplementation",
                       "Horvath F", 2013, last_author="Other H")]
        doc = dedup.link(recs, [], [], now=AT)
        l4 = [p for p in doc["proposals"] if p["rule"] == "L4-author"]
        self.assertEqual([["rec-pmid-99000031", "rec-pmid-99000032"]], [p["items"] for p in l4])
        self.assertEqual("possible", l4[0]["certainty"])
        # az azonos című harmadik rekord L2 (nem L4) javaslatot kap
        self.assertTrue(any(p["rule"].startswith("L2-") and p["items"] == ["rec-pmid-99000032", "rec-pmid-99000033"]
                            for p in doc["proposals"]))

    def test_erratum_linked_to_parent(self):
        parent = mk_rec("rec-pmid-99000051", {"pmid": api("99000051")}, "Soy trial main paper", "Lukacs M", 2018,
                        related=[{"type": "erratum_in", "rec_id": "rec-pmid-99000052"}])
        err = mk_rec("rec-pmid-99000052", {"pmid": api("99000052")}, "Erratum: Soy trial main paper", "Lukacs M", 2019,
                     flags={"erratum": True}, erratum_for="rec-pmid-99000051")
        doc = dedup.link([parent, err], [], [], now=AT)
        p = [x for x in doc["proposals"] if x["rule"] == "L3-erratum"]
        self.assertEqual([["rec-pmid-99000051", "rec-pmid-99000052"]], [x["items"] for x in p])
        acc = dedup.make_decision("study_link", "proposal", p[0]["proposal_id"], "accept", "user:SzK", seq=1, now=AT)
        doc2 = dedup.link(doc["records"], [], [acc], prior=doc, now=AT)
        self.assertEqual(1, len(doc2["studies"]))
        roles = dict((r["rec_id"], r["role"]) for r in doc2["studies"][0]["reports"])
        self.assertEqual({"rec-pmid-99000051": "primary", "rec-pmid-99000052": "erratum"}, roles)
        self.assertEqual("Lukacs 2018", doc2["studies"][0]["label"])

    def test_classify_new_records_against_known(self):
        known = TestLinkL1L2().records()
        new = [mk_rec("rec-pmid-99000001", {"pmid": api("99000001")}, "Soy isoflavones and CRP", "Kovacs A", 2015),
               mk_rec("rec-pmid-99000077", {"pmid": api("99000077"), "doi": api("10.5555/syn.1", "europepmc",
                                                                                 "europepmc.search")},
                      "Soy isoflavones and C-reactive protein", "Kovacs A", 2015),
               mk_rec("rec-pmid-99000078", {"pmid": api("99000078")},
                      "Effect of soy protein on interleukin-6: a randomised trial", "Nagy B", 2016),
               mk_rec("rec-pmid-99000079", {"pmid": api("99000079")}, "Unrelated zinc study in children", "Zeta Q", 2024)]
        out = dedup.classify_new_records(new, known)
        self.assertEqual([("rec-pmid-99000001", "rec-pmid-99000001"), ("rec-pmid-99000077", "rec-pmid-99000001")],
                         [(x["rec_id"], x["known_rec_id"]) for x in out["already_known"]])
        self.assertEqual(["rec-pmid-99000078"], [x["rec_id"] for x in out["possible_duplicates"]])
        self.assertEqual("probable", out["possible_duplicates"][0]["certainty"])
        self.assertEqual(["rec-pmid-99000079"], out["new"])

    def test_label_collision_suffixes(self):
        recs = [mk_rec("rec-pmid-99000041", {"pmid": api("99000041")}, "Trial one about soy", "Szabo G", 2015),
                mk_rec("rec-pmid-99000042", {"pmid": api("99000042")}, "Completely different zinc study", "Szabo G", 2015)]
        doc = dedup.link(recs, [], [], now=AT)
        self.assertEqual(["Szabo 2015a", "Szabo 2015b"], [s["label"] for s in doc["studies"]])

    def test_duplicates_report_and_batch_decisions(self):
        p = TmpProject()
        try:
            t = TestLinkL1L2()
            recs, revs = t.records(), t.reviews()
            doc = dedup.link(recs, revs, [], now=AT)
            dups = dedup.duplicates_report(doc, revs)
            self.assertEqual(1, len(dups))
            self.assertEqual("rec-pmid-99000001", dups[0]["rec_id"])
            self.assertEqual(2, dups[0]["n_reviews"])
            self.assertTrue(all(c["evidence_ids"] for c in dups[0]["citations"]))
            p.write_reviews(revs)
            dedup.write_json_atomic(os.path.join(p.hd, "studies.json"), doc)
            with self.assertRaises(dedup.DecisionError):
                dedup.decide_batch(p.dir, "accept", "agent:ma-metaheadhunter", kind="same_report")
            out = dedup.decide_batch(p.dir, "accept", "user:SzK", now=AT, kind="same_report", min_score=0.5)
            self.assertEqual(1, len(out))
            self.assertTrue(out[0]["batch"].startswith("batch-20261005T100000Z: "))
            self.assertIn("min_score=0.5", out[0]["batch"])
            res = dedup.run_dedupe(p.dir, now=AT)
            self.assertTrue(res["ok"])
            self.assertEqual(0, res["summary"]["pending"])
            self.assertEqual(2, res["duplicates"])  # a Kovacs (L1) és az elfogadott Nagy (L2) összevonás
            assert_valid(self, p.studies(), "studies")
        finally:
            p.cleanup()


# =============================================================================================
# L4 — feloldás
# =============================================================================================

def soy_reviews():
    a = mk_review("rv-pmid-90000101", 2019, [
        # 1. az áttekintés PMID-je + egyező cím → pub-id
        mk_cand("c0001", "Kovacs A, Toth B. Soy isoflavones and CRP in women. J Synth Med 2015;12:100-9.",
                title="Soy isoflavones and CRP in women", first_author="Kovacs A", year=2015, journal="J Synth Med",
                ids={"pmid": idv("99000101")}, label="Kovacs 2015"),
        # 2. rossz review-PMID (más cikk) → nem fogadjuk el; cím-keresés oldja fel
        mk_cand("c0002", "Nagy B. Soy protein and interleukin-6 in adults. Synth Nutr 2016;3:20-8.",
                title="Soy protein and interleukin-6 in adults", first_author="Nagy B", year=2016, journal="Synth Nutr",
                ids={"pmid": idv("99000999")}, label="Nagy 2016"),
        # 3. ecitmatch (folyóirat/év/kötet/oldal), cím nélkül
        mk_cand("c0003", "Szabo G, et al. Synth Nutr 2014;2:55-60.", first_author="Szabo G", year=2014,
                journal="Synth Nutr", label="Szabo 2014"),
        # 4. kétértelmű: két erős találat
        mk_cand("c0004", "Horvath F. Soy and lipid profile: a randomized trial. J Synth Med 2010;9:1-5.",
                title="Soy and lipid profile: a randomized trial", first_author="Horvath F", year=2010, label="Horvath 2010"),
        # 5. feloldatlan (semmi nem talál), meg nem erősített DOI-val
        mk_cand("c0005", "Ghost Q. An untraceable conference poster on soy. Proc Synth 2011.",
                title="An untraceable conference poster on soy", first_author="Ghost Q", year=2011,
                ids={"doi": idv("10.5555/ghost.404")}, label="Ghost 2011"),
        # elutasított jelölt — kimarad
        mk_cand("c0006", "Excluded X. Not relevant. 2001.", status="rejected"),
    ])
    b = mk_review("rv-pmid-90000102", 2021, [
        # ugyanaz a közlemény DOI-val (PubMed esearch [doi] → ugyanaz a PMID) → ugyanaz a rekord
        mk_cand("c0001", "Kovacs A. Soy isoflavones and CRP in women. J Synth Med. 2015;12:100-9.",
                title="Soy isoflavones and CRP in women", first_author="Kovacs A", year=2015,
                ids={"doi": idv("10.5555/syn.101")}, label="Kovacs 2015",
                study_registry_in_review=[idv("NCT99000101", via="jats.table", type="nct")]),
    ])
    return [a, b]


def soy_clients(crossref_down=True):
    pm = FakePubMed(
        records=[summary("99000101", "Soy isoflavones and CRP in women.", "Kovacs A", 2015, doi="10.5555/syn.101"),
                 summary("99000999", "A completely unrelated cardiology paper.", "Other Z", 2016),
                 summary("99000102", "Soy protein and interleukin-6 in adults.", "Nagy B", 2016),
                 summary("99000103", "Isoflavone intake and TNF-alpha in men.", "Szabo G", 2014, journal="Synth Nutr"),
                 summary("99000104", "Soy and lipid profile: a randomized trial.", "Horvath F", 2010),
                 summary("99000105", "Soy and lipid profile: a randomized trial", "Horvath F", 2010,
                         journal="Other J"),
                 summary("99000106", "An untraceable conference poster on soy.", "Ghost Q", 2011,
                         pub_types=["Congress"])],
        esearch={'"Soy protein and interleukin-6 in adults"[ti]': ["99000102"],
                 '"Soy and lipid profile: a randomized trial"[ti]': ["99000104", "99000105"]},
        ecit={("Synth Nutr", 2014, "2", "55"): "99000103"},
        efetch={"99000101": {"pmid": "99000101", "pub_types": ["Journal Article", "Randomized Controlled Trial"],
                             "databanks": [{"name": "ClinicalTrials.gov", "accessions": ["NCT99000101"]}],
                             "registry_ids": ["NCT99000101"], "comments_corrections": [], "mesh": ["Humans"],
                             "abstract": "SECRET-ABSTRACT-TEXT registered as NCT99000101.", "language": "eng"},
                "99000102": {"pmid": "99000102", "pub_types": ["Journal Article", "Retracted Publication"],
                             "databanks": [], "registry_ids": [], "mesh": [],
                             "comments_corrections": [{"type": "RetractionIn", "pmid": "99000777", "source": "x"}],
                             "abstract": "", "registry_ids_from_abstract": ["ISRCTN12345678"]}})
    ep = FakeEPMC(annotations={"99000101": ["NCT99000101"]})
    ct = FakeCtgov(studies={"NCT99000101": "Synthetic soy trial"},
                   citing={"99000101": [{"nct": "NCT99000101", "type": "RESULT", "strength": "confirming"},
                                        {"nct": "NCT99000555", "type": "BACKGROUND", "strength": "weak"}]})
    clients = {"pubmed": pm, "europepmc": ep, "ctgov": ct}
    if crossref_down:
        clients["crossref"] = DownClient("crossref")
    return clients


class TestResolve(unittest.TestCase):

    def run_resolver(self, reviews, clients=None, prior=None, decisions=()):
        clients = clients or soy_clients()
        res = resolve.Resolver(clients=clients, now=AT).run(reviews, prior, decisions)
        return res, clients

    def test_resolution_order_thresholds_and_provenance(self):
        revs = soy_reviews()
        res, cl = self.run_resolver(revs)
        links = res["links"]
        by = dict((r["rec_id"], r) for r in res["records"])
        # 1. pub-id
        r1 = by[links["rv-pmid-90000101#c0001"]]
        self.assertEqual("rec-pmid-99000101", r1["rec_id"])
        self.assertEqual(("resolved", "pub-id"), (r1["resolution"]["status"], r1["resolution"]["method"]))
        self.assertEqual({"value": "99000101", "source": "pubmed", "via": "pubmed.esummary", "at": AT},
                         dict((k, r1["ids"]["pmid"][k]) for k in ("value", "source", "via", "at")))
        # a második áttekintés DOI-ja ugyanarra a közleményre oldódik → egy rekord, két eredet
        self.assertEqual("rec-pmid-99000101", links["rv-pmid-90000102#c0001"])
        self.assertEqual(["rv-pmid-90000101", "rv-pmid-90000102"], sorted(o["review_id"] for o in r1["origins"]))
        self.assertEqual({"pmid": "pubmed.esummary"}, res["confirmations"]["rv-pmid-90000101#c0001"])
        self.assertEqual({"doi": "pubmed.esearch+esummary"}, res["confirmations"]["rv-pmid-90000102#c0001"])
        # 2. rossz review-PMID → nincs megerősítés, cím-keresés oldja fel
        r2 = by[links["rv-pmid-90000101#c0002"]]
        self.assertEqual("rec-pmid-99000102", r2["rec_id"])
        self.assertEqual("esearch-title", r2["resolution"]["method"])
        self.assertIn("review_id_title_mismatch", r2["resolution"]["notes"])
        self.assertNotIn("rv-pmid-90000101#c0002", res["confirmations"])
        # 3. ecitmatch cím nélkül: első szerző + év egyezik
        r3 = by[links["rv-pmid-90000101#c0003"]]
        self.assertEqual(("rec-pmid-99000103", "ecitmatch"), (r3["rec_id"], r3["resolution"]["method"]))
        # 4. két erős találat → kétértelmű, feloldási javaslat lehetőségekkel (nincs kitalált azonosító)
        r4 = by[links["rv-pmid-90000101#c0004"]]
        self.assertTrue(r4["rec_id"].startswith("rec-x-"))
        self.assertEqual("ambiguous", r4["resolution"]["status"])
        prop = [p for p in res["proposals"] if p["items"][0] == "rv-pmid-90000101#c0004"][0]
        self.assertEqual(("resolution", "pending", "p-res-0001"), (prop["kind"], prop["status"], prop["proposal_id"]))
        self.assertEqual(["rec-pmid-99000104", "rec-pmid-99000105"], sorted(o["rec_id"] for o in prop["options"]))
        self.assertTrue(all(o["source"] == "pubmed" for o in prop["options"]))
        # 5. feloldatlan: rec-x, a meg nem erősített DOI megmarad (H003-jelzés), de nem ad rec_id-t
        r5 = by[links["rv-pmid-90000101#c0005"]]
        self.assertEqual("unresolved", r5["resolution"]["status"])
        self.assertEqual("review", r5["ids"]["doi"]["source"])
        self.assertNotIn("confirmed_by", r5["ids"]["doi"])
        self.assertTrue(any(w["code"] == "H003" and r5["rec_id"] in w["hu"] for w in res["warnings"]))
        # elutasított jelölt nincs feldolgozva
        self.assertNotIn("rv-pmid-90000101#c0006", links)
        # a Crossref elérhetetlen → H014, a lépés részleges, a többi forrás működött
        self.assertEqual("unreachable", res["sources"]["crossref"]["status"])
        self.assertTrue(any(w["code"] == "H014" for w in res["warnings"]))
        self.assertEqual(1, cl["crossref"].calls)  # az első hiba után a forrás kimarad
        self.assertEqual({"candidates": 6, "resolved": 4, "ambiguous": 1, "unresolved": 1},
                         dict((k, res["stats"][k]) for k in ("candidates", "resolved", "ambiguous", "unresolved")))

    def test_enrichment_registry_strength_retraction_no_abstract(self):
        res, cl = self.run_resolver(soy_reviews())
        by = dict((r["rec_id"], r) for r in res["records"])
        r1 = by["rec-pmid-99000101"]
        strengths = dict(((l["id"], l["via"]), l["strength"]) for l in r1["registry_links"])
        self.assertEqual("strong", strengths[("NCT99000101", "pubmed.efetch.databank")])
        self.assertEqual("strong", strengths[("NCT99000101", "europepmc.annotations")])
        self.assertEqual("confirming", strengths[("NCT99000101", "ctgov.references")])
        self.assertEqual("weak", strengths[("NCT99000555", "ctgov.references")])
        self.assertEqual("review", strengths[("NCT99000101", "review+ctgov.study")])
        self.assertEqual("NCT99000101", r1["ids"]["nct"]["value"])
        self.assertIn(r1["ids"]["nct"]["source"], ("pubmed", "europepmc"))
        self.assertNotIn("NCT99000555", json.dumps(r1["ids"]))  # a gyenge kapcsolat nem azonosító
        r2 = by["rec-pmid-99000102"]
        self.assertTrue(r2["flags"]["retracted"])
        self.assertIn({"type": "retraction_in", "rec_id": "rec-pmid-99000777"}, r2["related"])
        self.assertEqual("ISRCTN12345678", r2["ids"]["registry"][0]["value"])
        self.assertTrue(any(w["code"] == "H013" for w in res["warnings"]))
        # az absztrakt szövege SEHOL nem kerül a kimenetbe (N4)
        self.assertNotIn("SECRET-ABSTRACT-TEXT", json.dumps(res, ensure_ascii=False))

    def test_human_decisions_option_user_id_and_reject(self):
        revs = soy_reviews()
        res, _ = self.run_resolver(revs)
        prior = {"records": res["records"], "proposals": res["proposals"]}
        pid = [p for p in res["proposals"] if p["items"][0] == "rv-pmid-90000101#c0004"][0]["proposal_id"]
        opts = [p for p in res["proposals"] if p["proposal_id"] == pid][0]["options"]
        n = [o["option"] for o in opts if o["rec_id"] == "rec-pmid-99000105"][0]
        d_opt = dedup.make_decision("id_confirm", "proposal", pid, "option:%d" % n, "user:SzK", seq=1, now=AT)
        d_user = dedup.make_decision("id_confirm", "candidate", "rv-pmid-90000101#c0005", "pmid:99000106", "user:SzK",
                                     seq=2, now=AT)
        res2, cl = self.run_resolver(revs, prior=prior, decisions=[d_opt, d_user])
        links = res2["links"]
        self.assertEqual("rec-pmid-99000105", links["rv-pmid-90000101#c0004"])
        r4 = dict((r["rec_id"], r) for r in res2["records"])["rec-pmid-99000105"]
        self.assertEqual("human-choice", r4["resolution"]["method"])
        self.assertEqual("pubmed", r4["ids"]["pmid"]["source"])  # a lehetőség API-válaszból jött
        p = [x for x in res2["proposals"] if x["proposal_id"] == pid][0]
        self.assertEqual(("accepted", d_opt["decision_id"]), (p["status"], p["decision_id"]))
        # felhasználó által beírt azonosító: source user + API-megerősítés
        r5 = dict((r["rec_id"], r) for r in res2["records"])[links["rv-pmid-90000101#c0005"]]
        self.assertEqual("rec-pmid-99000106", r5["rec_id"])
        self.assertTrue(any(o.get("cand_id") == "c0005" for o in r5["origins"]))
        self.assertEqual(("user", "pubmed.esummary"), (r5["ids"]["pmid"]["source"], r5["ids"]["pmid"]["confirmed_by"]))
        # a korábbi rec-x rekord nem vész el: merged_into az újba (a rá hivatkozó döntések megmaradnak)
        old_x = res["links"]["rv-pmid-90000101#c0004"]
        rx = dict((r["rec_id"], r) for r in res2["records"])[old_x]
        self.assertEqual(("merged_into", "rec-pmid-99000105", "resolve"), (rx["status"], rx["merged_into"], rx["merged_by"]))
        # a feloldott tételek nem kérdeznek újra a hálózaton (újrahasznosítás)
        self.assertEqual(4, res2["stats"]["reused"])
        # elutasítás: feloldatlan marad, a javaslat elutasított
        d_rej = dedup.make_decision("id_confirm", "proposal", pid, "reject", "user:SzK", seq=3, now=AT,
                                    supersedes=d_opt["decision_id"])
        res3, _ = self.run_resolver(revs, prior={"records": res2["records"], "proposals": res2["proposals"]},
                                    decisions=[d_opt, d_user, d_rej])
        self.assertTrue(res3["links"]["rv-pmid-90000101#c0004"].startswith("rec-x-"))
        self.assertEqual("rejected", [x for x in res3["proposals"] if x["proposal_id"] == pid][0]["status"])
        # az ágens nem dönthet (N3)
        d_agent = dedup.make_decision("id_confirm", "candidate", "rv-pmid-90000101#c0005", "pmid:99000103",
                                      "agent:ma-metaheadhunter", seq=4, now=AT)
        res4, _ = self.run_resolver(revs, decisions=[d_agent])
        self.assertTrue(res4["links"]["rv-pmid-90000101#c0005"].startswith("rec-x-"))
        self.assertTrue(any(w["code"] == "non_human_decision" for w in res4["warnings"]))
        # elérhetetlen forrás mellett a kézi azonosító nem ellenőrizhető → H014 (nem H005), feloldatlan marad
        cl_down = soy_clients()
        cl_down["pubmed"].down = True
        cl_down.pop("europepmc")
        res_u, _ = self.run_resolver(revs, clients=cl_down, decisions=[d_user])
        self.assertTrue(res_u["links"]["rv-pmid-90000101#c0005"].startswith("rec-x-"))
        self.assertTrue(any(w["code"] == "H014" and "rv-pmid-90000101#c0005" == (w.get("detail") or {}).get("ref")
                            for w in res_u["warnings"]))
        # a visszavont (felváltás nélküli) emberi választás nem marad érvényben
        prior2 = {"records": res2["records"], "proposals": res2["proposals"]}
        res_w, _ = self.run_resolver(revs, prior=prior2, decisions=[])
        self.assertNotEqual("rec-pmid-99000106", res_w["links"]["rv-pmid-90000101#c0005"])
        # nem létező, kézzel beírt azonosító → H005, nem fogadjuk el
        d_bad = dedup.make_decision("id_confirm", "candidate", "rv-pmid-90000101#c0005", "pmid:12", "user:SzK",
                                    seq=5, now=AT)
        res5, _ = self.run_resolver(revs, decisions=[d_bad])
        self.assertTrue(res5["links"]["rv-pmid-90000101#c0005"].startswith("rec-x-"))
        self.assertTrue(any(w["code"] == "H005" for w in res5["warnings"]))

    def test_pubmed_down_degrades_gracefully(self):
        cl = soy_clients()
        cl["pubmed"].down = True
        cl["europepmc"].by_pmid["99000101"] = {"source": "MED", "id": "99000101", "pmid": "99000101",
                                               "title": "Soy isoflavones and CRP in women.",
                                               "authorString": "Kovacs A, Toth B.", "pubYear": "2015",
                                               "journalTitle": "J Synth Med"}
        res, _ = self.run_resolver(soy_reviews(), clients=cl)
        self.assertEqual("unreachable", res["sources"]["pubmed"]["status"])
        r = dict((x["rec_id"], x) for x in res["records"])["rec-pmid-99000101"]
        self.assertEqual(("europepmc", "europepmc.search"), (r["ids"]["pmid"]["source"], r["ids"]["pmid"]["via"]))
        self.assertTrue(any(w["code"] == "H014" and w["detail"]["source"] == "pubmed" for w in res["warnings"]))

    def test_citation_parser(self):
        p = resolve.parse_citation("Colditz GA, Brewer TF, Berkey CS, et al. Efficacy of BCG vaccine in the prevention of "
                                   "tuberculosis. Meta-analysis of the published literature. JAMA 1994 Mar 2;271(9):698-702.")
        self.assertEqual(("JAMA", 1994, "271", "9", "698", "702"),
                         (p["journal"], p["year"], p["volume"], p["issue"], p["first_page"], p["last_page"]))
        self.assertTrue(p["title"].startswith("Efficacy of BCG vaccine"))
        self.assertIsNone(resolve.parse_citation("Tameris 2013 {published data only}")["journal"])

    def test_run_resolve_project_files(self):
        p = TmpProject()
        try:
            revs = soy_reviews()
            p.write_reviews(revs)
            cl = soy_clients()
            out = resolve.run_resolve(p.dir, clients=cl, now=AT)
            self.assertTrue(out["ok"])
            self.assertEqual(3, out["exit_code"])  # a Crossref elérhetetlen → részleges
            doc = p.studies()
            assert_valid(self, doc, "studies")
            self.assertTrue(doc["studies"])  # a relink után a vizsgálat-klaszterek is megvannak
            rv = dedup.read_json(os.path.join(p.hd, "reviews", "rv-pmid-90000101.json"))
            assert_valid(self, rv, "review")
            c1 = rv["candidates"][0]
            self.assertEqual("rec-pmid-99000101", c1["rec_id"])
            self.assertEqual("pubmed.esummary", c1["ids"]["pmid"]["confirmed_by"])
            c2 = rv["candidates"][1]
            self.assertNotIn("confirmed_by", c2["ids"]["pmid"])  # a rossz review-PMID nincs megerősítve
            # második futás: a feloldottak újrahasznosítva, a döntésre váró javaslat azonosítója stabil
            calls_before = len(cl["pubmed"].calls)
            out2 = resolve.run_resolve(p.dir, clients=cl, now=AT)
            doc2 = p.studies()
            self.assertEqual(4, out2["stats"]["reused"])
            self.assertEqual([x["proposal_id"] for x in doc["proposals"]], [x["proposal_id"] for x in doc2["proposals"]])
            self.assertEqual(dedup.dump_json(doc), dedup.dump_json(doc2))
            self.assertFalse(any(c[0] == "esummary" and "99000101" in c[1] for c in cl["pubmed"].calls[calls_before:]))
        finally:
            p.cleanup()


class TestResolveRecordedCassettes(unittest.TestCase):
    """Valódi, rögzített API-válaszok (csak olvasva) — Tameris 2013 (PMID 23391465)."""

    def player(self):
        docs = []
        for rel in ("pubmed/efetch_tameris.json", "europepmc/annotations_tameris.json",
                    "ctgov/referencepmid_23391465.json"):
            with open(os.path.join(CDIR, rel), encoding="utf-8") as fh:
                docs.append(json.load(fh))
        with open(os.path.join(CDIR, "pubmed/esummary_kashangura_tameris.json"), encoding="utf-8") as fh:
            es = json.load(fh)
        it = copy.deepcopy(es["interactions"][0])
        it["request"]["url"] = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=23391465&retmode=json"
        docs.append({"schema": net.CASSETTE_SCHEMA, "name": "derived-esummary-tameris", "origin": "recorded",
                     "interactions": [it]})
        return net.CassettePlayer.from_documents(docs, env={})

    def test_tameris_registry_link_regression(self):
        fixed = calendar.timegm((2026, 10, 5, 12, 0, 0, 0, 0, 0))
        http = net.HttpClient(player=self.player(), env={}, clock=lambda: fixed, sleep=lambda s: None,
                              use_env_cassette=False)
        rv = mk_review("rv-pmid-31038197", 2019, [
            mk_cand("c0006", "Tameris MD, Hatherill M, Landry BS, et al. " + TAMERIS_TITLE + " Lancet 2013;381:1021-8.",
                    title=TAMERIS_TITLE, first_author="Tameris MD", year=2013, journal="Lancet",
                    ids={"pmid": idv("23391465")}, label="Tameris 2013", group_key="CD012915-bbs2-0006")])
        res = resolve.Resolver(http=http, use=["pubmed", "europepmc", "ctgov"], now=AT, env={}).run([rv])
        self.assertEqual({}, dict((s, v) for s, v in res["sources"].items() if v["status"] != "ok"))
        rec = dict((r["rec_id"], r) for r in res["records"])["rec-pmid-23391465"]
        self.assertEqual("pub-id", rec["resolution"]["method"])
        self.assertEqual("Tameris MD", rec["bib"]["first_author"])
        strengths = {}
        for l in rec["registry_links"]:
            strengths.setdefault(l["id"], set()).add(l["strength"])
        self.assertIn("strong", strengths["NCT00953927"])     # PubMed DataBank + Europe PMC annotáció
        self.assertEqual({"weak"}, strengths["NCT04975178"])  # csak CT.gov BACKGROUND
        self.assertEqual("NCT00953927", rec["ids"]["nct"]["value"])
        self.assertNotIn("NCT04975178", json.dumps(rec["ids"]))
        self.assertEqual({"pmid": "pubmed.esummary"}, res["confirmations"]["rv-pmid-31038197#c0006"])
        self.assertEqual([], http.player.misses)


# =============================================================================================
# L6 — átfedés
# =============================================================================================

class TestOverlap(unittest.TestCase):

    def test_cca_worked_example_and_edges(self):
        self.assertEqual(30.0, overlap.cca(16, 10, 3))
        self.assertEqual("very_high", overlap.band(overlap.cca(16, 10, 3)))
        self.assertIsNone(overlap.cca(5, 5, 1))
        self.assertIsNone(overlap.cca(0, 0, 3))
        # páronként: (N − r) / r; Ying 2025 1. példa: 6 és 14 vizsgálat, 1 közös → 5,3% „slight"
        p = overlap.pair_cca(1, 6, 14)
        self.assertEqual(5.3, overlap.round1(p))
        self.assertEqual("slight", overlap.band(p))
        self.assertEqual(100.0, overlap.pair_cca(4, 4, 4))
        self.assertEqual(0.0, overlap.pair_cca(0, 3, 4))

    def test_band_boundaries(self):
        for v, want in ((0.0, "slight"), (5.0, "slight"), (5.49, "slight"), (5.5, "moderate"), (10.0, "moderate"),
                        (10.49, "moderate"), (10.5, "high"), (15.0, "high"), (15.49, "high"), (15.5, "very_high"),
                        (100.0, "very_high")):
            self.assertEqual(want, overlap.band(v), v)
        self.assertIsNone(overlap.band(None))

    def test_wcca_formula(self):
        # wCCA = (wN − wr) / (wr·c − wr): 2 áttekintés; 3 sor: √100 (mindkettőben), √25, √400
        w = overlap.wcca([2, 1, 1], [10.0, 5.0, 20.0], 2)
        self.assertAlmostEqual(100.0 * (45.0 - 35.0) / 35.0, w)
        self.assertIsNone(overlap.wcca([2, 1], [10.0, None], 2))
        self.assertIsNone(overlap.wcca([1], [1.0], 1))

    def make(self):
        recs = [mk_rec("rec-pmid-99000201", {"pmid": api("99000201")}, "Main trial report", "Kiss D", 2014),
                mk_rec("rec-pmid-99000202", {"pmid": api("99000202")}, "Follow-up of main trial", "Kiss D", 2016),
                mk_rec("rec-pmid-99000203", {"pmid": api("99000203")}, "Another trial", "Bodo K", 2015),
                mk_rec("rec-pmid-99000204", {"pmid": api("99000204")}, "Third trial", "Pal L", 2017)]
        sd = lambda n, st: [{"field": "n_total", "value": n, "outcome": "CRP", "status": st}]
        a = mk_review("rv-pmid-90000301", 2018, [
            mk_cand("c0001", "Kiss 2014", rec_id="rec-pmid-99000201", secondary=sd(100, "verified")),
            mk_cand("c0002", "Bodo 2015", rec_id="rec-pmid-99000203", secondary=sd(25, "verified")),
            mk_cand("c0003", "Kiss 2016 (rejected)", rec_id="rec-pmid-99000202", status="rejected")], first_author="Alfa")
        b = mk_review("rv-pmid-90000302", 2020, [
            mk_cand("c0001", "Kiss 2016", rec_id="rec-pmid-99000202", secondary=sd(100, "verified")),
            mk_cand("c0002", "Pal 2017", rec_id="rec-pmid-99000204", secondary=sd(400, "unverified"))], first_author="Beta")
        c = mk_review("rv-pmid-90000303", 2022, [], status="excluded")
        doc = dedup.link(recs, [a, b, c], [], now=AT)
        sp = [p for p in doc["proposals"] if p["rule"] == "L4-author"]
        link = dedup.make_decision("study_link", "proposal", sp[0]["proposal_id"], "accept", "user:SzK", seq=1, now=AT)
        doc = dedup.link(doc["records"], [a, b, c], [link], prior=doc, now=AT)
        return [a, b, c], doc

    def test_matrix_report_vs_study_level_wcca_and_schema(self):
        revs, studies = self.make()
        rep = overlap.compute_overlap(revs, studies, level="report", now=AT)
        stu = overlap.compute_overlap(revs, studies, level="study", now=AT)
        assert_valid(self, rep, "overlap")
        assert_valid(self, stu, "overlap")
        self.assertEqual(["rv-pmid-90000301", "rv-pmid-90000302"], rep["reviews"])  # a kizárt áttekintés nem oszlop
        self.assertEqual((4, 4, 2, 0.0, "slight"), (rep["N"], rep["r"], rep["c"], rep["cca_pct"], rep["band"]))
        # vizsgálat-szinten a fő közlemény és a követés egy sor → mindkét áttekintés bevonta
        self.assertEqual((4, 3, 2), (stu["N"], stu["r"], stu["c"]))
        self.assertEqual(33.3, stu["cca_pct"])
        self.assertEqual("very_high", stu["band"])
        self.assertEqual([{"a": "rv-pmid-90000301", "b": "rv-pmid-90000302", "n_a": 2, "n_b": 2, "shared": 1,
                           "cca_pct": 33.3, "band": "very_high"}], stu["pairs"])
        idx = dict((r["label"], r["index_review"]) for r in stu["rows"])
        self.assertEqual("rv-pmid-90000301", idx["Kiss 2014"])
        # wCCA: a Pal 2017 mintaelemszáma nem ellenőrzött → null + magyarázat
        self.assertIsNone(stu["wcca_pct"])
        self.assertTrue(any(n["code"] == "wcca_unavailable" for n in stu["warnings"]))
        # a hívó által adott, ellenőrzött mintaelemszámokkal kiszámolható
        sizes = dict((r["key"], n) for r, n in zip(stu["rows"], (100, 25, 400)))
        w = overlap.compute_overlap(revs, studies, level="study", sample_sizes=sizes, now=AT)
        self.assertEqual(overlap.round1(100.0 * (45.0 - 35.0) / 35.0), w["wcca_pct"])
        self.assertIn("CCA = 33.3%", stu["notes"]["en"])

    def test_scope_csv_and_run(self):
        revs, studies = self.make()
        sc = overlap.compute_overlap(revs, studies, level="report", scope="crp", now=AT)
        self.assertEqual(4, sc["r"])
        sc2 = overlap.compute_overlap(revs, studies, level="report", scope="IL-6", now=AT)
        self.assertEqual((0, None), (sc2["r"], sc2["cca_pct"]))
        csv_text = overlap.matrix_csv(overlap.compute_overlap(revs, studies, level="study", now=AT), revs)
        lines = csv_text.splitlines()
        self.assertEqual("kulcs;cimke;Alfa 2018 (rv-pmid-90000301);Beta 2020 (rv-pmid-90000302);index_attekintes;"
                         "attekintesek_szama", lines[0])
        self.assertIn("CCA_%;33.3", lines)
        p = TmpProject()
        try:
            p.write_reviews(revs)
            dedup.write_json_atomic(os.path.join(p.hd, "studies.json"), studies)
            out = overlap.run_overlap(p.dir, level="study", csv=True, now=AT)
            self.assertEqual((33.3, "very_high"), (out["cca_pct"], out["band"]))
            self.assertTrue(os.path.exists(os.path.join(p.hd, "exports", "overlap_matrix.csv")))
            assert_valid(self, dedup.read_json(os.path.join(p.hd, "overlap.json")), "overlap")
        finally:
            p.cleanup()


# =============================================================================================
# L7 — szűrés, PRISMA
# =============================================================================================

STATE = {
    "criteria": [
        {"id": "E1", "type": "include", "domain": "S", "text": "RCT",
         "machine_hint": {"pub_types_exclude": ["Review", "Editorial"]}},
        {"id": "E2", "type": "include", "domain": "T", "text": "1990 után", "machine_hint": {"year_min": 1990}},
        {"id": "E3", "type": "include", "domain": "P", "text": "ember", "machine_hint": {"humans_only": True,
                                                                                        "title_exclude_any": ["rats"]}},
        {"id": "E4", "type": "include", "domain": "L", "text": "angol", "machine_hint": {"languages": ["eng"]}},
    ],
    "exclusion_reasons": [
        {"code": "X1", "criterion": "E1", "label": {"hu": "Nem RCT", "en": "Not an RCT"}},
        {"code": "X2", "criterion": "E3", "label": {"hu": "Nem a célpopuláció", "en": "Wrong population"}},
        {"code": "X3", "criterion": None, "label": {"hu": "Visszavont közlemény", "en": "Retracted publication"}},
    ],
    "settings": {},
}


class TestScreening(unittest.TestCase):

    def records(self):
        return [
            mk_rec("rec-pmid-99000301", {"pmid": api("99000301")}, "A narrative review of soy", "Rev A", 2015,
                   pub_types=["Review"]),
            mk_rec("rec-pmid-99000302", {"pmid": api("99000302")}, "Soy in 1985", "Old B", 1985),
            mk_rec("rec-pmid-99000303", {"pmid": api("99000303")}, "Soy feeding in rats", "Rat C", 2010,
                   flags={"animal_only": True}),
            mk_rec("rec-pmid-99000304", {"pmid": api("99000304")}, "Retracted soy trial", "Ret D", 2012,
                   flags={"retracted": True}),
            mk_rec("rec-pmid-99000305", {"pmid": api("99000305")}, "A good soy RCT", "Good E", 2016,
                   pub_types=["Randomized Controlled Trial"]),
        ]

    def test_machine_proposals(self):
        props = eligibility.machine_proposals(self.records(), STATE, now=AT)
        got = sorted((p["rec_id"], p["rule"], p["suggestion"], p["reason_code"]) for p in props)
        self.assertEqual(sorted([
            ("rec-pmid-99000301", "M-pub-type", "exclude", "X1"),
            ("rec-pmid-99000302", "M-year", "exclude", None),
            ("rec-pmid-99000303", "M-animal", "exclude", "X2"),
            ("rec-pmid-99000303", "M-title-terms", "unclear", None),
            ("rec-pmid-99000304", "M-retracted", "exclude", "X3"),
        ]), got)
        self.assertTrue(all(p["proposed_by"] == "tool:headhunter" and p["explanation"]["hu"] for p in props))
        self.assertFalse(any(p["suggestion"] == "include" for p in props))  # a gép nem javasol bevonást

    def test_agent_proposals_quote_check_and_id_drop(self):
        recs = self.records()
        abstracts = {"rec-pmid-99000305": "Background: postmenopausal women. Methods: 120 participants were randomised."}
        doc = {"actor": "agent:ma-metaheadhunter", "items": [
            {"rec_id": "rec-pmid-99000305", "level": "title_abstract", "suggestion": "include", "criterion": "E1",
             "quote": "120 participants were  randomised.", "pmid": "11111111", "rationale": "RCT nőkön."},
            {"rec_id": "rec-pmid-99000305", "level": "title_abstract", "suggestion": "exclude",
             "quote": "men only were enrolled"},
            {"rec_id": "rec-pmid-00000000", "level": "title_abstract", "suggestion": "exclude"},
            {"rec_id": "rec-pmid-99000305", "level": "title_abstract", "suggestion": "maybe"},
            {"rec_id": "rec-pmid-99000305", "level": "title_abstract", "suggestion": "include", "quote": "x" * 301}]}
        acc, rej = eligibility.import_agent_proposals(doc, recs, lambda r: abstracts.get(r["rec_id"]), now=AT)
        self.assertEqual(1, len(acc))
        self.assertEqual(("include", "agent:ma-metaheadhunter", "120 participants were randomised."),
                         (acc[0]["suggestion"], acc[0]["proposed_by"], acc[0]["quote"]))
        self.assertNotIn("11111111", json.dumps(acc))
        self.assertEqual(["pmid"], acc[0]["basis"]["dropped_id_fields"])
        self.assertEqual(["H004", "unknown_record", "value", "quote_too_long"], [r["code"] for r in rej])
        acc2, rej2 = eligibility.import_agent_proposals({"actor": "user:x", "items": []}, recs, None)
        self.assertEqual("actor", rej2[0]["code"])

    def setup_project(self):
        p = TmpProject()
        recs = self.records()
        p.write_state(STATE)
        dedup.write_json_atomic(os.path.join(p.hd, "studies.json"), dedup.link(recs, [], [], now=AT))
        return p

    def test_decision_validation_and_proposal_carry(self):
        p = self.setup_project()
        try:
            with self.assertRaises(dedup.DecisionError):
                eligibility.record_screen_decision(p.dir, "rec-pmid-99000305", "title_abstract", "include",
                                                   "agent:ma-metaheadhunter")
            with self.assertRaises(dedup.DecisionError):
                eligibility.record_screen_decision(p.dir, "rec-pmid-99000305", "full_text", "exclude", "user:SzK")
            with self.assertRaises(dedup.DecisionError):
                eligibility.record_screen_decision(p.dir, "rec-pmid-99000305", "full_text", "exclude", "user:SzK",
                                                   reason_code="X9")
            with self.assertRaises(dedup.DecisionError):
                eligibility.record_screen_decision(p.dir, "rec-pmid-99000305", "title_abstract", "awaiting", "user:SzK")
            with self.assertRaises(dedup.DecisionError):
                eligibility.record_screen_decision(p.dir, "rec-nope", "title_abstract", "include", "user:SzK")
            out = eligibility.run_screen_propose(p.dir, now=AT)
            self.assertEqual(4, out["exit_code"])
            scr = dedup.read_json(os.path.join(p.hd, "screening.json"))
            pid = [x for x in scr["proposals"] if x["rule"] == "M-pub-type"][0]["proposal_id"]
            d = eligibility.record_screen_decision(p.dir, "rec-pmid-99000301", "title_abstract", "exclude", "user:SzK",
                                                   reason_code="X1", proposal_id=pid, now=AT)
            self.assertEqual(("screen", "title_abstract", "X1", "tool:headhunter", ["D-S04-104"]),
                             (d["kind"], d["level"], d["reason_code"], d["proposed_by"], d["kb_refs"]))
            assert_valid(self, d, "decision")
            eligibility.run_screen_propose(p.dir, now=AT)
            scr2 = dedup.read_json(os.path.join(p.hd, "screening.json"))
            self.assertEqual([x["proposal_id"] for x in scr["proposals"]], [x["proposal_id"] for x in scr2["proposals"]])
            self.assertEqual("decided", [x for x in scr2["proposals"] if x["proposal_id"] == pid][0]["status"])
        finally:
            p.cleanup()

    def test_dual_screening_conflict_resolution_and_kappa(self):
        recs = self.records()
        st = dict(STATE, settings={"reviewers": ["user:A", "user:B"]})
        mk = lambda seq, actor, rid, val, sup=None: dedup.make_decision(
            "screen", "record", rid, val, actor, seq=seq, now=AT, level="title_abstract", supersedes=sup)
        a1 = mk(1, "user:A", "rec-pmid-99000305", "include")
        b1 = mk(2, "user:B", "rec-pmid-99000305", "exclude")
        a2 = mk(3, "user:A", "rec-pmid-99000301", "exclude")
        b2 = mk(4, "user:B", "rec-pmid-99000301", "exclude")
        a3 = mk(5, "user:A", "rec-pmid-99000302", "exclude")
        s = eligibility.screening_status(recs, [a1, b1, a2, b2, a3], st)
        self.assertEqual("conflict", s["rec-pmid-99000305"]["title_abstract"]["status"])
        self.assertEqual(("decided", "exclude"), (s["rec-pmid-99000301"]["title_abstract"]["status"],
                                                  s["rec-pmid-99000301"]["title_abstract"]["value"]))
        self.assertEqual("partial", s["rec-pmid-99000302"]["title_abstract"]["status"])
        fix = mk(6, "user:C", "rec-pmid-99000305", "include", sup=b1["decision_id"])
        s2 = eligibility.screening_status(recs, [a1, b1, a2, b2, a3, fix], st)
        self.assertEqual(("decided", "include"), (s2["rec-pmid-99000305"]["title_abstract"]["status"],
                                                  s2["rec-pmid-99000305"]["title_abstract"]["value"]))
        ag = eligibility.agreement([a1, b1, a2, b2, a3], "title_abstract", ["user:A", "user:B"], recs)
        self.assertEqual((2, 50.0, ["rec-pmid-99000305"]), (ag["n"], ag["percent"], ag["conflicts"]))
        pairs = [("i", "i")] * 20 + [("e", "e")] * 15 + [("i", "e")] * 5 + [("e", "i")] * 10
        self.assertEqual((70.0, 0.4, 50), eligibility.cohen_kappa(pairs))
        self.assertEqual((100.0, None, 3), eligibility.cohen_kappa([("i", "i")] * 3))

    def test_csv_import(self):
        p = self.setup_project()
        try:
            text = ("rec_id;level;decision;reason_code;actor\n"
                    "rec-pmid-99000305;title_abstract;include;;user:SzK\n"
                    "rec-pmid-99000305;full_text;exclude;;user:SzK\n"
                    "rec-pmid-99000301;title_abstract;exclude;X1;agent:bot\n"
                    "rec-pmid-99000302;title_abstract;exclude;X1;user:SzK\n")
            out = eligibility.import_screening_csv(p.dir, text, now=AT)
            self.assertEqual(2, len(out["imported"]))
            self.assertEqual([3, 4], [e["line"] for e in out["errors"]])
            bad = eligibility.import_screening_csv(p.dir, "a;b\n1;2\n")
            self.assertEqual(1, bad["errors"][0]["line"])
        finally:
            p.cleanup()


class TestPrismaBranchCounts(unittest.TestCase):

    def scenario(self, pending=False):
        # egyéb ág: 2 áttekintés, 6 egyedi közlemény (az egyiket mindkettő bevonta), 2 közlemény egy vizsgálat
        ids = ["9900040%d" % i for i in range(1, 7)]
        recs = [mk_rec("rec-pmid-%s" % x, {"pmid": api(x)}, "Synthetic report %s on soy" % x, "Au%s X" % x[-1], 2010 + i,
                       origins=rv_origin("rv-pmid-90000401", "c%04d" % (i + 1)))
                for i, x in enumerate(ids)]
        # adatbázis-ág: 3 új rekord + 1 már ismert (egyéb ágban is)
        upd = lambda x: [{"route": "update_search", "review_id": None, "cand_id": None,
                          "search_id": "s-pubmed-20261005T101200Z"}]
        recs += [mk_rec("rec-pmid-9900050%d" % i, {"pmid": api("9900050%d" % i)}, "Update hit %d" % i, "Up%d Y" % i,
                        2024, origins=upd(i)) for i in range(1, 4)]
        recs[0]["origins"] += upd(0)
        ra = mk_review("rv-pmid-90000401", 2019, [mk_cand("c%04d" % (i + 1), "x", rec_id="rec-pmid-%s" % x)
                                                   for i, x in enumerate(ids[:4])])
        rb = mk_review("rv-pmid-90000402", 2021, [mk_cand("c0001", "x", rec_id="rec-pmid-%s" % ids[3]),
                                                   mk_cand("c0002", "x", rec_id="rec-pmid-%s" % ids[4]),
                                                   mk_cand("c0003", "x", rec_id="rec-pmid-%s" % ids[5]),
                                                   mk_cand("c0004", "unknown role", role="unknown", status="proposed")])
        doc = dedup.link(recs, [ra, rb], [], now=AT)
        # a 4. és 5. közlemény ugyanaz a vizsgálat (kézi kapcsolás egy L4/L3 nélkül: study_link + javaslat helyett
        # szerep — itt egyszerűen a dedupe L4-tippjét nem használjuk; a vizsgálat-számot külön ellenőrizzük)
        seq = [0]

        def d(rid, level, value, rc=None):
            seq[0] += 1
            return dedup.make_decision("screen", "record", rid, value, "user:SzK", seq=seq[0], now=AT, level=level,
                                       reason_code=rc)
        r = lambda x: "rec-pmid-%s" % x
        decs = [d(r(ids[0]), "title_abstract", "exclude"),
                d(r(ids[1]), "title_abstract", "include"), d(r(ids[1]), "full_text", "not_retrieved"),
                d(r(ids[2]), "title_abstract", "unclear"), d(r(ids[2]), "full_text", "exclude", "X2"),
                d(r(ids[3]), "title_abstract", "include"), d(r(ids[3]), "full_text", "include"),
                d(r(ids[4]), "title_abstract", "include"), d(r(ids[4]), "full_text", "include"),
                d(r(ids[5]), "title_abstract", "include")]
        if not pending:
            decs.append(d(r(ids[5]), "full_text", "include"))
        decs += [d("rec-pmid-99000501", "title_abstract", "exclude"),
                 d("rec-pmid-99000502", "title_abstract", "include"), d("rec-pmid-99000502", "full_text", "include"),
                 d("rec-pmid-99000503", "title_abstract", "include"), d("rec-pmid-99000503", "full_text", "exclude", "X1")]
        return doc, [ra, rb], decs

    def test_counts_pass_engine_check(self):
        doc, revs, decs = self.scenario()
        out = eligibility.branch_counts(doc, revs, decs, STATE,
                                        update_doc={"results": {"retrieved_total": 5, "duplicates_within": 1,
                                                                "already_known": 1, "new_records": []}})
        f = out["flow"]
        self.assertEqual({"other_methods_identified": 6, "other_methods_sought": 5, "other_methods_not_retrieved": 1,
                          "other_methods_assessed": 4, "other_methods_excluded": 1,
                          "other_methods_excluded_reasons": {"Nem a célpopuláció": 1}},
                         dict((k, f[k]) for k in f if k.startswith("other_methods")))
        self.assertEqual((3, 1, 2, 0, 2, 1, {"Nem RCT": 1}),
                         (f["screened"], f["excluded_screening"], f["sought"], f["not_retrieved"], f["assessed"],
                          f["excluded_eligibility"], f["excluded_eligibility_reasons"]))
        self.assertEqual((4, 4), (f["included_reports"], f["included_studies"]))
        self.assertEqual(2, f["duplicates_removed"])
        self.assertNotIn("awaiting", f)
        self.assertEqual((1, 7, 1), (out["hh"]["other_methods_title_excluded"], out["hh"]["citations_total"],
                                     out["hh"]["already_known"]))
        self.assertEqual(1, out["hh"]["pending_candidates"])
        flow = dict(f, identified_databases=5, identified_registers=0, automation_removed=0, other_removed=0)
        chk = engine_prisma.check_flow(flow, template="PRISMA2020")
        errors = [x for x in chk.findings if x["severity"] == "error"]
        self.assertEqual([], [(x["code"], x["detail"]) for x in errors])
        self.assertFalse(any(x["code"] in ("P005", "P007", "P008", "P009", "P016") for x in chk.findings))

    def test_pending_counts_keep_equations(self):
        doc, revs, decs = self.scenario(pending=True)
        out = eligibility.branch_counts(doc, revs, decs, STATE)
        f = out["flow"]
        self.assertEqual(1, f["awaiting"])
        self.assertEqual(3, f["included_reports"])
        self.assertFalse(out["complete"])
        flow = dict(f, identified_databases=3, identified_registers=0, duplicates_removed=0)
        chk = engine_prisma.check_flow(flow, template="PRISMA2020")
        self.assertEqual([], [(x["code"], x["detail"]) for x in chk.findings if x["severity"] == "error"])

    def test_mining_only_project(self):
        doc, revs, decs = self.scenario()
        recs = [r for r in doc["records"] if not r["rec_id"].startswith("rec-pmid-990005")]
        for r in recs:
            r["origins"] = [o for o in r["origins"] if o["route"] != "update_search"]
        doc2 = dedup.link(recs, revs, [], now=AT)
        out = eligibility.branch_counts(doc2, revs, decs, STATE)
        self.assertTrue(out["hh"]["no_database_branch"])
        self.assertEqual(0, out["flow"]["identified_databases"])
        chk = engine_prisma.check_flow(out["flow"], template="PRISMA2020")
        self.assertEqual([], [(x["code"], x["detail"]) for x in chk.findings if x["severity"] == "error"])
        self.assertEqual(3, out["flow"]["included_reports"])


class TestProjectFlow(unittest.TestCase):
    """Végponttól végpontig a projektmappán (hálózat nélkül): resolve → EP3 → dedupe → overlap → screen → PRISMA."""

    def test_flow(self):
        p = TmpProject()
        try:
            revs = soy_reviews()
            p.write_reviews(revs)
            p.write_state(STATE)
            cl = soy_clients()
            out = resolve.run_resolve(p.dir, clients=cl, now=AT)
            self.assertEqual(3, out["exit_code"])
            self.assertEqual([{"checkpoint": "EP3", "n": out["pending"][0]["n"]}], out["pending"])
            # EP3: a kétértelmű feloldás emberi választása, a többi függő javaslat elutasítása (tömegesen, szűrővel)
            doc = p.studies()
            res_p = [x for x in doc["proposals"] if x["kind"] == "resolution"][0]
            dedup.decide_proposal(p.dir, res_p["proposal_id"], "option:1", "user:SzK", now=AT)
            resolve.run_resolve(p.dir, clients=cl, now=AT)
            dedup.decide_batch(p.dir, "reject", "user:SzK", reason="Különböző közlemények.", now=AT,
                               kind="same_report")
            dedup.decide_batch(p.dir, "reject", "user:SzK", reason="Nem ugyanaz a vizsgálat.", now=AT,
                               kind="same_study")
            res = dedup.run_dedupe(p.dir, now=AT)
            self.assertEqual(0, res["summary"]["pending"])
            doc = p.studies()
            assert_valid(self, doc, "studies")
            self.assertEqual([], dedup.membership_problems(doc))
            self.assertEqual([], dedup.verify_decision_chain(dedup.read_decisions(p.dir)))
            for d in dedup.read_decisions(p.dir):
                assert_valid(self, d, "decision")
            ov = overlap.run_overlap(p.dir, level="study", csv=True, now=AT)
            self.assertEqual(2, ov["c"])
            self.assertEqual(1, ov["pairs"][0]["shared"])  # a Kovacs-vizsgálatot mindkét áttekintés bevonta
            out = eligibility.run_screen_propose(p.dir, now=AT)
            self.assertEqual(4, out["exit_code"])
            rows = eligibility.screening_list(p.dir)
            self.assertTrue(rows)
            kov = [r for r in rows if r["rec_id"] == "rec-pmid-99000101"][0]
            self.assertEqual(["rv-pmid-90000101", "rv-pmid-90000102"], kov["included_by_reviews"])
            for r in rows:
                if "retracted" in r["flags"]:
                    eligibility.record_screen_decision(p.dir, r["rec_id"], "title_abstract", "exclude", "user:SzK",
                                                       reason_code="X3", now=AT)
                    continue
                eligibility.record_screen_decision(p.dir, r["rec_id"], "title_abstract", "include", "user:SzK", now=AT)
                eligibility.record_screen_decision(p.dir, r["rec_id"], "full_text", "include", "user:SzK", now=AT)
            out = eligibility.run_screen_propose(p.dir, now=AT)
            self.assertEqual(0, out["exit_code"])
            counts = eligibility.branch_counts(p.studies(), dedup.load_reviews(p.dir), dedup.read_decisions(p.dir),
                                               STATE)
            f = counts["flow"]
            self.assertEqual(len(rows), f["other_methods_identified"])
            self.assertEqual(len(rows) - 1, f["included_reports"])
            chk = engine_prisma.check_flow(f, template="PRISMA2020")
            self.assertEqual([], [(x["code"], x["detail"]) for x in chk.findings if x["severity"] == "error"])
            self.assertTrue(counts["complete"] or counts["hh"]["pending_candidates"] == 0)
        finally:
            p.cleanup()


if __name__ == "__main__":
    unittest.main()
