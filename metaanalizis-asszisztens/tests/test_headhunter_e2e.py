# -*- coding: utf-8 -*-
"""Metaheadhunter — regressziós tesztek az élő, végponttól végpontig tartó próba (2026-10-05; BCG–tuberkulózis és
szója–gyulladásos markerek) hibamódjaira, valamint a csomag-szintű összefüggésekre (cli / checks / facade / report).

Minden teszt OFFLINE: szintetikus adattal vagy a ``tests/reference/headhunter/cassettes/e2e/`` valós, rögzített
válaszaival (absztrakt kivágva; az OpenAlex-irodalomjegyzék 12 tételre korlátozva). Újrarögzítés (élő hálózat):
``python3 tests/reference/headhunter/cassettes/record_cassettes.py --only e2e``.

Lefedett hibamódok (a jelentés D-számai):

- D1  Europe PMC felkutatás mező nélkül a teljes szövegben keresett → TITLE_ABS/KW;
- D2  rangsor: a relevancia súlya, szótő- és ékezet-egyezés (Colditz 1994 a 83. helyről az első 15 közé);
- D3  hibajegyzék (Corrigendum) önálló áttekintés-jelöltként;
- D4  régi, nem nyílt áttekintés (Colditz 1994): Europe PMC-ben nincs irodalomjegyzék → OpenAlex referenced_works
      (a listás keret kimerülésekor egyedi lekérésekkel);
- D5  PMC efetch csak címlapot ad (<body> nélkül) → nem teljes szöveg, irodalomjegyzék-út;
- D6  normalizált Europe PMC-hivatkozásokból elveszett a PMID;
- D7  az EP2-ben megerősített „unknown" szerepű (irodalomjegyzék-) jelölt kimaradt a feloldásból;
- D8  szerzőnév-alakok (Jr utótag, OpenAlex „Ferguson Rg", „A. Mac DOWELL"), helykitöltő szerző („AUTHOR UNKNOWN");
- D9  azonosító-ellenőrzés: szó szerinti cím mellett az eltérő szerzőalak/év (OpenAlex digitalizálási év) ne
      akadályozza a feloldást; a PubMed-cím végére fűzött testületi szerző (cím-előtag);
- D10 követéses jelentés-sorozat (MRC 1972/1977) L4-tipp; ``_merge_same_signature`` KeyError;
- D11 Europe PMC strukturált absztrakt XML-jelölése idézetbe került (H019 hamis riasztás);
- D12 alap gépi szűrési kritériumok (init után 0 javaslat volt);
- D13 lezárás API-val meg nem erősített azonosítóval (H003) + emberi „nincs azonosító" nyilatkozat;
- D14 ``verify`` IndexError, ha a találatnak nincs javasolt parancsa; hiányzó TERV-parancsok (cli.build_parser).
"""
import io
import json
import os
import shutil
import tempfile
import unittest

from _helpers import ROOT  # noqa: F401  (a sys.path beállítása)
from metaelemzes.headhunter import (checks, cli, dedup, extract as X, facade, finder, included, merge as M, net,
                                    report, resolve, sources as srcreg, state as S)
from metaelemzes.headhunter.__main__ import main as cli_main

import test_headhunter_merge as TM

CDIR = os.path.join(ROOT, "tests", "reference", "headhunter", "cassettes", "e2e")
AT = "2026-10-05T12:00:00Z"
FIXED = 1791201600.0  # 2026-10-05T12:00:00Z


class FakeClock(object):
    def __init__(self, t=FIXED):
        self.t = float(t)

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, float(seconds))


def cassette_http(*names):
    player = net.CassettePlayer([os.path.join(CDIR, n + ".json") for n in names], env={})
    clock = FakeClock()
    return net.HttpClient(player=player, env={}, clock=clock, sleep=clock.sleep, use_env_cassette=False), player


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-e2e-")
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
# L1 — felkutatás (D1, D2, D3, D11)
# =============================================================================================

class TestFinderFixes(unittest.TestCase):

    BLOCKS = [{"concept": "P", "terms": ["tuberculosis"], "mesh": []},
              {"concept": "I", "terms": ["BCG vaccine", "Bacillus Calmette-Guerin"], "mesh": []}]

    def test_d1_europepmc_terms_restricted_to_title_abstract_keywords(self):
        q = finder.build_queries(self.BLOCKS)["europepmc"]["query"]
        self.assertIn("TITLE_ABS:tuberculosis", q)
        self.assertIn('TITLE_ABS:"BCG vaccine"', q)
        self.assertIn('KW:"Bacillus Calmette-Guerin"', q)
        self.assertNotIn("((tuberculosis)", q)

    def test_d2_relevance_stemming_and_accents(self):
        rel = [["tuberculosis"], ["BCG vaccine", "Bacillus Calmette-Guerin"]]
        self.assertEqual(finder._group_hits(rel, "Effect of BCG vaccination on childhood tuberculous meningitis"),
                         [False, True])
        self.assertEqual(finder._group_hits(rel, "Duration of protection by bacillus Calmette-Guérin vaccination "
                                                 "against tuberculosis"), [True, True])
        self.assertEqual(finder._group_hits(rel, "Xpert MTB/RIF Ultra assay for tuberculosis in children"),
                         [True, False])

    def _cand(self, title, cochrane=False, oa=False, year=2025, abstract_hits=None):
        return {"bib": {"title": title, "year": year}, "search_date": None, "k_reported": None,
                "signals": {"meta_analysis": True}, "flags": {"retracted": False, "narrative_suspect": False},
                "is_cochrane": cochrane, "fulltext": {"available": oa}, "_abstract_hits": abstract_hits or []}

    def test_d2_topic_review_outranks_offtopic_open_cochrane(self):
        rel = [["tuberculosis"], ["BCG vaccine", "Bacillus Calmette-Guerin"]]
        w = dict(finder.DEFAULT_WEIGHTS)
        colditz = self._cand("Efficacy of BCG vaccine in the prevention of tuberculosis. Meta-analysis of the "
                             "published literature.", year=1994)
        xpert = self._cand("Xpert MTB/RIF Ultra assay for tuberculosis disease and rifampicin resistance in "
                           "children.", cochrane=True, oa=True, year=2025)
        for c in (colditz, xpert):
            finder._rank(c, rel, "2026-10-05", w)
        self.assertGreater(colditz["rank"]["score"], xpert["rank"]["score"])
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)

    def test_d3_erratum_flag_and_zero_score(self):
        rel = [["tuberculosis"]]
        c = self._cand("Corrigendum: A Meta-Analysis of the Effect of BCG Vaccination Against Bovine Tuberculosis")
        c["flags"]["erratum"] = True
        finder._rank(c, rel, "2026-10-05", finder.DEFAULT_WEIGHTS)
        self.assertEqual(c["rank"]["score"], 0.0)

    def test_d11_abstract_markup_removed_before_quoting(self):
        raw = ('Random effects meta-analysis was performed in R software.</sec><sec id="st3"><title>RESULTS</title>'
               'We identified 1,806 references &amp; 12 studies.')
        clean = finder.clean_abstract(raw)
        self.assertNotIn("<", clean)
        self.assertIn("RESULTS We identified 1,806 references & 12 studies.", clean)
        k = finder.extract_k(clean)
        self.assertNotIn("<sec", json.dumps(k))


# =============================================================================================
# L3 — kinyerés: irodalomjegyzék-láncolat (D4, D5, D6) — valós, rögzített válaszokkal
# =============================================================================================

def _review_doc(rid, pmid, title, first_author, year, pmcid=None):
    ids = {"pmid": {"value": pmid, "source": "pubmed", "via": "pubmed.esearch", "at": AT}}
    if pmcid:
        ids["pmcid"] = {"value": pmcid, "source": "pubmed", "via": "pubmed.esummary", "at": AT}
    return {"schema": "szk.ma.headhunter.review/v1", "model": "szk.ma.headhunter/v1", "review_id": rid,
            "status": "selected", "superseded_by": None, "ids": ids,
            "bib": {"title": title, "first_author": first_author, "year": year},
            "found_by": [], "candidates": [], "evidence": [], "decision_ids": []}


class TestExtractFallbackCassettes(_Env):

    def setUp(self):
        _Env.setUp(self)
        self.old_cap = X.OPENALEX_REF_CAP
        X.OPENALEX_REF_CAP = 12  # a kazetta 12 hivatkozással készült

    def tearDown(self):
        X.OPENALEX_REF_CAP = self.old_cap
        _Env.tearDown(self)

    def _extract(self, rid, doc, cassette):
        proj = os.path.join(self.tmp, "p")
        os.makedirs(proj)
        S.init_state(proj, "BCG", actor=None)
        S.save_review(proj, doc)
        h, player = cassette_http(cassette)
        cfg = srcreg.config_from_state(S.load_state(proj))
        row, ws, down = X.extract_review(proj, rid, "auto", None, X._Clients(h, cfg, {}), cfg, AT)
        self.assertEqual(player.misses, [])
        return proj, row, ws, down

    def test_d4_colditz1994_reference_list_from_openalex(self):
        rid = "rv-pmid-8309034"
        proj, row, ws, down = self._extract(rid, _review_doc(
            rid, "8309034", "Efficacy of BCG vaccine in the prevention of tuberculosis. Meta-analysis of the "
                            "published literature.", "Colditz GA", 1994), "extract_pmid8309034")
        self.assertFalse(down)
        self.assertEqual((row["strategy"], row["reflist_source"]), ("reflist_api", "openalex"))
        self.assertEqual(row["n_candidates"], 12)
        rv = S.load_review(proj, rid)
        self.assertEqual(set(c["role_in_review"] for c in rv["candidates"]), {"unknown"})
        self.assertEqual(set(c["confidence"] for c in rv["candidates"]), {"low"})
        # minden azonosító API-ból (OpenAlex), nem kitalált (N1)
        for c in rv["candidates"]:
            for v in (c.get("ids") or {}).values():
                self.assertEqual(v["source"], "openalex")
                self.assertEqual(v["via"], "openalex.referenced_works")
        # a „Vezetéknév Kezdőbetűk" OpenAlex-alak nem kezdőbetűt ad vezetéknévnek
        firsts = [c["cited_as"].get("first_author") for c in rv["candidates"]]
        self.assertFalse([f for f in firsts if f and len(f) <= 2], firsts)
        tried = rv["extraction_runs"][-1]["reflist_tried"]
        self.assertEqual([t["source"] for t in tried], ["europepmc", "openalex"])
        self.assertEqual(tried[0]["n"], 0)
        self.assertEqual(tried[1]["note"], "list_quota")  # a listás keret 429 → egyedi lekérések

    def test_d5_pmc_title_page_only_is_not_full_text(self):
        rid = "rv-pmid-24021245"
        proj, row, ws, down = self._extract(rid, _review_doc(
            rid, "24021245", "Systematic review and meta-analysis of the current evidence on the duration of "
                             "protection by bacillus Calmette-Guérin vaccination against tuberculosis.",
            "Abubakar I", 2013, pmcid="PMC4781620"), "extract_pmid24021245")
        self.assertEqual(row["route"], "none")
        self.assertEqual(row["reflist_source"], "openalex")
        self.assertGreater(row["n_candidates"], 0)
        self.assertIn("fulltext_not_downloadable", [w["code"] for w in ws])
        rv = S.load_review(proj, rid)
        self.assertFalse(rv["fulltext"]["available"])
        self.assertEqual(rv["fulltext"].get("note"), "pmc_no_body")
        self.assertFalse(X.has_body('<article><front><abstract>x</abstract></front></article>'))
        self.assertTrue(X.has_body('<article><front/><body><sec>x</sec></body></article>'))

    def test_d6_normalized_europepmc_reference_keeps_pmid(self):
        n = included._norm_record({"source_db": "MED", "id": "18891458", "pmid": "18891458", "title": "Protective "
                                   "vaccination against tuberculosis", "first_author": "Aronson JD", "year": 1948},
                                  "europepmc")
        self.assertEqual(n["ids"].get("pmid"), "18891458")


class TestExtractChainFakes(_Env):
    """A láncolat ágai hamis kliensekkel (hálózat nélkül)."""

    class EPMC(object):
        def __init__(self, refs=()):
            self.refs = list(refs)

        def lookup_pmid(self, pmid, result_type="core"):
            return None

        def fulltext_xml(self, pmcid):
            return None

        def references(self, src, ext_id, normalize=False, **kw):
            return iter(self.refs)

    class OA(object):
        def __init__(self, n=3):
            self.n = n
            self.calls = []

        def referenced_works(self, ident):
            self.calls.append(("refs", ident))
            return ["W%d" % (100 + i) for i in range(self.n)]

        def works_by_ids(self, wids, **kw):
            raise net.SourceUnavailable("openalex", "rate_limited", reset_at="2026-10-06T00:00:00Z")

        def work(self, w, select=None):
            self.calls.append(("work", w))
            i = int(w[1:]) - 100
            return {"id": "https://openalex.org/%s" % w, "display_name": "Synthetic BCG trial report %d" % i,
                    "publication_year": 1950 + i, "authorships": [{"author": {"display_name": "Synth%c Ab" % (65 + i)}}],
                    "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/9990000%d" % i}}

    def _proj(self):
        proj = os.path.join(self.tmp, "p")
        os.makedirs(proj)
        S.init_state(proj, "BCG", actor=None)
        S.save_review(proj, _review_doc("rv-pmid-99999901", "99999901", "Synthetic review", "Rev A", 2000))
        return proj

    def test_openalex_single_lookups_when_list_quota_exhausted(self):
        proj = self._proj()
        oa = self.OA(3)
        res = X.run_extract(proj, clients={"europepmc": self.EPMC(), "openalex": oa})
        self.assertEqual(res["exit_code"], 4)
        self.assertEqual(res["reviews"][0]["reflist_source"], "openalex")
        self.assertEqual([c for c in oa.calls if c[0] == "work"], [("work", "W100"), ("work", "W101"),
                                                                  ("work", "W102")])
        rv = S.load_review(proj, "rv-pmid-99999901")
        self.assertEqual([c["cited_as"]["first_author"] for c in rv["candidates"]], ["SynthA", "SynthB", "SynthC"])
        self.assertEqual(rv["candidates"][0]["ids"]["pmid"]["value"], "99900000")

    def test_no_reference_list_anywhere_warns_with_next_steps(self):
        proj = self._proj()
        res = X.run_extract(proj, clients={"europepmc": self.EPMC(), "openalex": self.OA(0)})
        codes = [w["code"] for w in res["warnings"]]
        self.assertIn("no_reference_list", codes)
        self.assertEqual(res["reviews"][0]["status"], "partial")

    def test_pdf_route_needs_single_review(self):
        proj = self._proj()
        S.save_review(proj, _review_doc("rv-pmid-99999902", "99999902", "Second", "Rev B", 2001))
        with self.assertRaises(X.ExtractError):
            X.run_extract(proj, strategy="pdf", pdf="x.pdf", clients={})


# =============================================================================================
# EP2 → L4 (D7), nevek és azonosító-ellenőrzés (D8, D9) — valós PubMed-rekordokkal
# =============================================================================================

class TestCandidateConfirmPromotesRole(_Env):

    def test_d7_confirmed_unknown_reference_becomes_included_and_is_resolved(self):
        proj = os.path.join(self.tmp, "p")
        os.makedirs(proj)
        S.init_state(proj, "BCG", actor=None)
        rv = _review_doc("rv-pmid-99999901", "99999901", "Synthetic review", "Rev A", 2000)
        rv["candidates"] = [{"cand_id": "c0001", "cited_as": {"text": "Aronson JD. Protective vaccination. 1948"},
                             "ids": {}, "rec_id": None, "role_in_review": "unknown", "evidence_ids": [],
                             "confidence": "low", "status": "proposed", "decision_ids": []}]
        S.save_review(proj, rv)
        d = S.append_decision(proj, "candidate_confirm", ("candidate", "rv-pmid-99999901#c0001"), "confirm",
                              "user:SzK")
        cand = S.apply_candidate_decision(proj, "rv-pmid-99999901", "c0001", "confirm", d["decision_id"])
        self.assertEqual(cand["role_in_review"], "included")
        self.assertEqual(cand["role_origin"], {"extracted": "unknown", "set_by": d["decision_id"]})
        items = resolve.collect_items(S.load_reviews(proj))
        self.assertEqual([it.ref for it in items], ["rv-pmid-99999901#c0001"])


class TestNameForms(unittest.TestCase):

    def test_d8_suffix_particles_placeholders(self):
        self.assertEqual(dedup.norm_surname("MacDOWELL A Jr"), "macdowell")
        self.assertEqual(dedup.norm_surname("Smith AB 3rd"), "smith")
        self.assertIsNone(dedup.surname_display("AUTHOR UNKNOWN"))
        self.assertIsNone(dedup.surname_display("[No authors listed]"))
        self.assertEqual(included.openalex_surname("Ferguson Rg"), "Ferguson")
        self.assertEqual(included.openalex_surname("Comstock Gw"), "Comstock")
        self.assertEqual(included.openalex_surname("A. Mac DOWELL"), "Mac DOWELL")
        self.assertEqual(included.openalex_surname("Jianping Wu"), "Wu")
        self.assertEqual(included.openalex_surname("Jan van Nielen"), "van Nielen")
        self.assertTrue(dedup.first_author_match("Ferguson Rg", "FERGUSON RG"))
        self.assertTrue(dedup.first_author_match("Mac DOWELL", "MacDOWELL A Jr"))
        self.assertTrue(dedup.first_author_match("Pérez", "Cano Pérez G"))
        self.assertFalse(dedup.first_author_match("Smith", "Smyth A"))
        self.assertIsNone(dedup.first_author_match("AUTHOR UNKNOWN", "Hart PD"))

    def test_d9_title_prefix_with_appended_corporate_author(self):
        a = "Fifteen year follow up of trial of BCG vaccines in south India for tuberculosis prevention"
        b = a + ". Tuberculosis Research Centre (ICMR), Chennai."
        self.assertGreaterEqual(dedup.title_similarity(a, b), 0.95)
        self.assertLess(dedup.title_similarity("BCG vaccination", "BCG vaccination: a review of trials in India"),
                        0.95)


class TestResolveAcceptanceWithRealRecords(unittest.TestCase):
    """A ``resolve._accept`` a valós PubMed-rekordokkal (esummary kazetta), az OpenAlex-irodalomjegyzék
    hivatkozás-alakjaival (élő próba, BCG)."""

    @classmethod
    def setUpClass(cls):
        from metaelemzes.headhunter import pubmed
        h, player = cassette_http("esummary_name_forms")
        recs = pubmed.Client(h).esummary(["18102809", "15392668", "15417251", "4537855", "10573656"])
        cls.meta = dict((r["pmid"], resolve._meta(r)) for r in recs)
        assert not player.misses

    def item(self, title, first_author, year, pmid):
        rv = {"review_id": "rv-pmid-99999901"}
        cand = {"cand_id": "c0001", "cited_as": {"text": title, "title": title, "first_author": first_author,
                                                 "year": year},
                "ids": {"pmid": {"value": pmid, "source": "openalex", "via": "openalex.referenced_works", "at": AT}}}
        return resolve._Item(rv, cand)

    def test_d8_openalex_initials_after_surname(self):
        it = self.item("BCG vaccination of Indian infants in Saskatchewan", "Ferguson Rg", 1949, "18102809")
        ok, ts, f = resolve._accept(it, self.meta["18102809"], resolve.ACCEPT_ID)
        self.assertTrue(ok, f)
        self.assertTrue(f["first_author_match"])

    def test_d8_compound_surname_with_suffix(self):
        it = self.item("Results of Oral BCG Vaccination on 348 Families", "Mac DOWELL", 1949, "15392668")
        ok, ts, f = resolve._accept(it, self.meta["15392668"], resolve.ACCEPT_ID)
        self.assertTrue(ok, f)

    def test_d9_digitisation_year_from_openalex(self):
        m = self.meta["15417251"]
        it = self.item(m["title"].rstrip("."), m.get("first_author"), int(m["year"]) + 55, "15417251")
        ok, ts, f = resolve._accept(it, m, resolve.ACCEPT_ID)
        self.assertTrue(ok, f)
        self.assertIn("accepted_year_differs", it.notes)

    def test_d8_placeholder_author_and_collective_name(self):
        m = self.meta["4537855"]
        it = self.item("BCG and vole bacillus vaccines in the prevention of tuberculosis in adolescence and early "
                       "adult life", "AUTHOR UNKNOWN", int(m["year"]), "4537855")
        ok, ts, f = resolve._accept(it, m, resolve.ACCEPT_ID)
        self.assertTrue(ok, f)

    def test_d9_corporate_author_appended_to_pubmed_title(self):
        m = self.meta["10573656"]
        it = self.item("Fifteen year follow up of trial of BCG vaccines in south India for tuberculosis prevention",
                       "Tuberculosis Research Centre (ICMR).", int(m["year"]), "10573656")
        ok, ts, f = resolve._accept(it, m, resolve.ACCEPT_ID)
        self.assertTrue(ok, f)

    def test_wrong_review_identifier_still_rejected(self):
        # szója-próba: az áttekintés (Gholami 2025) a Nourieh 2012-hez a Keshavarz 2012 PMID-jét adta — ilyenkor a
        # cím eltér, a feloldás javaslat marad (N1)
        m = dict(self.meta["18102809"])
        it = self.item("Effects of soymilk consumption on inflammatory markers and lipid profiles among "
                       "non-menopausal overweight and obese female adults", "Nourieh", 1949, "18102809")
        ok, ts, f = resolve._accept(it, m, resolve.ACCEPT_ID)
        self.assertFalse(ok)


# =============================================================================================
# L5 — jelentés-sorozat tipp és a javaslat-összevonás (D10)
# =============================================================================================

class TestDedupSeries(unittest.TestCase):

    def rec(self, rid, title, fa, year):
        return {"rec_id": rid, "ids": {}, "bib": {"title": title, "first_author": fa, "year": year}, "flags": {},
                "related": [], "origins": [{"route": "review_extraction", "review_id": "rv-pmid-99999901",
                                            "cand_id": "c%04d" % year, "search_id": None}],
                "retrievals": [], "resolution": {"status": "unresolved", "method": None, "score": None},
                "status": "active", "merged_into": None}

    def test_d10_follow_up_report_series_is_a_tip(self):
        t = "BCG and vole bacillus vaccines in the prevention of tuberculosis in adolescence and early adult life"
        recs = [self.rec("rec-x-aaaaaaaaa1", t, "Hart PD", 1977),
                self.rec("rec-x-aaaaaaaaa2", t, "Fourth Report to the Medical Research Council", 1972),
                self.rec("rec-x-aaaaaaaaa3", "Isoniazid prophylaxis in Alaska: a controlled trial", "Comstock GW",
                         1972)]
        doc = dedup.link(recs, [], [], now=AT)
        series = [p for p in doc["proposals"] if p["rule"] == "L4-title-series"]
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["items"], ["rec-x-aaaaaaaaa1", "rec-x-aaaaaaaaa2"])
        self.assertEqual((series[0]["kind"], series[0]["certainty"], series[0]["status"]),
                         ("same_study", "possible", "pending"))
        self.assertEqual(len(doc["studies"]), 3)  # tipp: ember dönt, automatikus kapcsolás nincs

    def test_d10_merge_same_signature_without_reviews(self):
        p = {"kind": "same_study", "rule": "L4-author", "items": ["rec-x-1", "rec-x-2"]}
        out = dedup._merge_same_signature([dict(p), dict(p)])
        self.assertEqual(len(out), 1)


# =============================================================================================
# EP4/EP5 — alap kritériumok (D12), H003-lezárás (D13), checks/verify (D14)
# =============================================================================================

class TestDefaultCriteria(_Env):

    def test_d12_default_criteria_give_machine_proposals(self):
        st = S.new_state("Q")
        self.assertEqual([c["id"] for c in st["criteria"]], ["E1", "E2"])
        self.assertIn("Review", st["criteria"][0]["machine_hint"]["pub_types_exclude"])
        x6 = [r for r in st["exclusion_reasons"] if r["code"] == "X6"][0]
        self.assertEqual(x6["criterion"], "E1")
        self.assertEqual(S.new_state("Q", criteria=[])["criteria"], [])  # a felhasználó üres listája megmarad
        from metaelemzes.headhunter import eligibility as E
        recs = [{"rec_id": "rec-pmid-99100001", "bib": {"pub_types": ["Journal Article", "Review"]}, "flags": {}},
                {"rec_id": "rec-pmid-99100002", "bib": {"pub_types": ["Randomized Controlled Trial"]}, "flags": {}}]
        props = E.machine_proposals(recs, st, now=AT)
        self.assertEqual([(p["rec_id"], p["reason_code"]) for p in props], [("rec-pmid-99100001", "X6")])


class TestSignoffRequiresConfirmedIds(_Env):

    def test_d13_unresolved_identifier_blocks_signoff_until_human_statement(self):
        proj = TM.build_project(self.tmp)
        TM.accept_all_proposals(proj)
        S.apply_candidate_decision(proj, TM.RV_A, "c0003", "confirm", S.append_decision(
            proj, "candidate_confirm", ("candidate", TM.RV_A + "#c0003"), "confirm", "user:SzK")["decision_id"])
        doc = S.read_json(S.path(proj, "studies.json"))
        for r in doc["records"]:
            if r["rec_id"] == "rec-pmid-99100102":
                r["resolution"] = {"status": "unresolved", "method": None, "score": None}
        S.write_json_atomic(S.path(proj, "studies.json"), doc)
        brown = TM.study_of(proj, "rec-pmid-99100103")
        TM.screen_all(proj, include=["rec-pmid-99100101", "rec-pmid-99100102", brown, "rec-pmid-99100105"])
        so = M.signoff(proj, "user:SzK")
        self.assertFalse(so["ok"])
        self.assertEqual(so["exit_code"], 4)
        self.assertTrue(any("H003" in e["hu"] for e in so["errors"]), so["errors"])
        rc, env = self.run_cli("decide", proj, "--target", "rec-pmid-99100102", "--value", "no_identifier",
                               "--actor", "user:SzK")
        self.assertEqual(rc, 2, env)  # indoklás nélkül nem
        rc, env = self.run_cli("decide", proj, "--target", "rec-pmid-99100101", "--value", "no_identifier",
                               "--reason", "x", "--actor", "user:SzK")
        self.assertEqual(rc, 2, env)  # feloldott rekordra nem adható
        rc, env = self.run_cli("decide", proj, "--target", "rec-pmid-99100102", "--value", "no_identifier",
                               "--reason", "Könyvtári katalógusban ellenőrizve; a PubMedben nincs.",
                               "--actor", "user:SzK")
        self.assertEqual(rc, 0, env)
        so = M.signoff(proj, "user:SzK")
        self.assertTrue(so["ok"], so.get("errors"))
        merged = S.read_json(S.path(proj, "merged.json"))
        st = [s for s in merged["studies"] if any(r["rec_id"] == "rec-pmid-99100102" for r in s["reports"])][0]
        self.assertIn("no_identifier_acknowledged", st["flags"])
        self.assertNotIn("unresolved_ids", st["flags"])


class TestChecksAndCli(_Env):

    def test_checks_rules_cover_spec(self):
        self.assertEqual(sorted(checks.RULES), ["H%03d" % i for i in range(1, 21)])
        self.assertEqual(set(checks.RULE_STAGES), set(checks.RULES))
        for code, (sev, title, action, src) in checks.RULES.items():
            self.assertIn(sev, ("error", "warning"))
            self.assertTrue(title and action and src)
        self.assertNotIn("H001", checks.KB_REFS)
        self.assertEqual(checks.KB_REFS["H020"], ["D-S04-102"])

    def test_verify_finding_shape_and_no_index_error(self):
        proj = TM.build_project(self.tmp)

        def upd(st):
            S.set_step(st, "overlap", "stale")
        S.mutate_state(proj, upd)
        res = checks.verify(proj)
        f = [x for x in res["findings"] if x["code"] == "H017"][0]
        for k in ("code", "severity", "stage", "title", "detail", "artifacts", "suggested_command", "kb_refs",
                  "explain", "hu", "en"):
            self.assertIn(k, f)
        self.assertEqual(f["suggested_command"], [])
        rc, env = self.run_cli("verify", proj)  # korábban IndexError (javasolt parancs nélküli találat)
        self.assertIn(rc, (0, 1), env)
        self.assertEqual(env["data"]["checker"], "metaelemzes.headhunter.checks")

    def test_h019_on_jats_fragment_in_review_file(self):
        proj = TM.build_project(self.tmp)
        rv = S.load_review(proj, TM.RV_A)
        rv["evidence"][0]["quote"] = 'text</sec><sec id="st3"><title>RESULTS</title>'
        S.save_review(proj, rv)
        codes = [(f["code"], f["severity"]) for f in checks.verify(proj)["findings"]]
        self.assertIn(("H019", "error"), codes)

    def test_parser_has_every_spec_command_used_by_the_agent(self):
        import argparse
        p = cli.build_parser()
        subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)][0].choices
        for cmd in ("sources", "init", "find-reviews", "reviews", "select-reviews", "extract", "show-text",
                    "agent-classify", "resolve", "dedupe", "proposals", "decide", "overlap", "screen",
                    "update-search", "cite-search", "merge", "prisma", "signoff", "verify-secondary", "export",
                    "status", "verify", "rebuild", "report"):
            self.assertIn(cmd, subs)

    def test_select_reviews_validates_before_writing(self):
        proj = TM.build_project(self.tmp)
        n0 = len(S.read_decisions(proj))
        rc, env = self.run_cli("select-reviews", proj, "--include", TM.RV_A, "--exclude", TM.RV_C,
                               "--actor", "user:SzK")
        self.assertEqual(rc, 2, env)  # kizárás ok nélkül
        self.assertEqual(len(S.read_decisions(proj)), n0)
        rc, env = self.run_cli("select-reviews", proj, "--include", TM.RV_A, "--exclude", TM.RV_C, "--reason",
                               "más PICO", "--actor", "user:SzK")
        self.assertEqual(rc, 0, env)
        self.assertEqual(env["data"]["n"], 2)
        rc, env = self.run_cli("select-reviews", proj, "--include", TM.RV_A, "--actor", "agent:x")
        self.assertEqual(rc, 2, env)

    def test_execute_returns_envelope_and_usage_errors(self):
        env, a = cli.execute(["status", os.path.join(self.tmp, "missing")])
        self.assertEqual(env["exit_code"], 2)
        env, a = cli.execute(["no-such-command"])
        self.assertEqual(env["exit_code"], 2)
        self.assertEqual(env["errors"][0]["code"], "USAGE")

    def test_report_written_from_project_files(self):
        proj = TM.build_project(self.tmp)
        res = report.write_report(proj)
        self.assertEqual(res["file"], "01_kereses/headhunter/exports/report.md")
        with io.open(S.path(proj, "exports", "report.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("## Forrás-áttekintések (EP1)", text)
        self.assertIn("másodlagos adatok", text)
        self.assertNotIn("<body", text)


class TestFacade(_Env):

    def test_run_step_and_decide_go_through_the_cli(self):
        proj = TM.build_project(self.tmp)
        env = facade.status(proj)
        self.assertIn(env["exit_code"], (0, 1, 4))
        self.assertEqual(env["command"], "status")
        env = facade.run_step(proj, "overlap", level="study")
        self.assertTrue(env["ok"], env)
        with self.assertRaises(facade.FacadeError):
            facade.run_step(proj, "nope")
        with self.assertRaises(facade.FacadeError):
            facade.decide(proj, "review_select", TM.RV_A, "include", "agent:x")
        env = facade.decide(proj, "review_select", TM.RV_C, "exclude", "user:SzK", reason="más PICO")
        self.assertEqual(env["exit_code"], 0, env)
        self.assertEqual(S.load_review(proj, TM.RV_C)["status"], "excluded")
        env = facade.verify(proj)
        self.assertEqual(env["command"], "verify")


if __name__ == "__main__":
    unittest.main()
