# -*- coding: utf-8 -*-
"""Metaheadhunter — a bevont vizsgálatok kinyerése (TERV_metaheadhunter.md 6. fejezet; ``jats.py`` + ``included.py``).

Minden teszt OFFLINE fut a ``tests/reference/headhunter/jats/`` fixture-ökön (szintetikus és vágott valós JATS).
Az élő próba (``MA_LIVE_TESTS=1``) az Europe PMC-ről tölti le a valós áttekintéseket, és az eredményt a rögzített
összefoglalóval veti össze.

Lefedett szerződés-pontok:

- 6.1 a1 Cochrane-szakaszok (bevont / kizárt / besorolásra váró / folyamatban lévő; csoport = vizsgálat;
  elsődleges és társközlemény; jellemzők-táblák megerősítésként; kizárási okok);
- 6.1 a2 bevont-vizsgálat táblák: xref → ``high``; KIZÁRÓLAG első szerző + év (Adetifa-csapda); egyedi → ``medium``,
  több/nincs → ``low`` + ``ref_hint`` azonosító nélkül; „2.1/2.2", ``rowspan``, kétsoros címke = egy jelölt;
- 6.1 a3 adattáblák: egyértelmű fejlécű számok ``secondary_value``-ként (``unverified``) cellánkénti lokátorral;
  kétértelmű fejlécnél nincs szám (N1, N2);
- 6.1 a4 „We included N studies [12–24]": megerősítés, önálló jelölt csak konzisztens mondatból;
- 6.2 tartalék: irodalomjegyzék → ``unknown``/``low``/``needs_review``; API-irodalomjegyzék;
- 6.2 ágens-import: szó szerinti idézet (H004), azonosító eldobása, ``low``/``proposed``;
- 6.0/2–3 keresési dátum és közölt k mintái, tartalék dátum (H008), k-eltérés (H006);
- N1/N4 invariánsok: minden bevont-állításnak létező bizonyítéka van, az idézet ≤ 300 karakter és cellánként szó
  szerint megvan a dokumentumban; az azonosítók ``source: review`` + ``via: jats.*``; sémahelyes review-dokumentum;
  determinisztikus és idempotens kimenet; a fixture-ökben nincs teljes szöveg.
"""
import copy
import json
import os
import re
import time
import unittest

import _helpers  # noqa: F401  (sys.path)

from metaelemzes.headhunter import included as I
from metaelemzes.headhunter import jats as J

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "reference", "headhunter", "jats")
REAL = os.path.join(FIX, "real")
CONTRACTS = os.path.join(os.path.dirname(HERE), "metaelemzes", "headhunter", "contracts")
AT = "2026-10-05T10:00:00Z"
LIVE = os.environ.get("MA_LIVE_TESTS") == "1"

_DOCS = {}
_RESULTS = {}


def doc(name):
    """Fixture beolvasása (gyorsítótárazva): 'cochrane_nested_reflist' vagy 'real/PMC6488980'."""
    if name not in _DOCS:
        with open(os.path.join(FIX, name + ".xml"), "rb") as fh:
            _DOCS[name] = J.parse(fh.read())
    return _DOCS[name]


def result(name, review_id=None):
    key = (name, review_id)
    if key not in _RESULTS:
        _RESULTS[key] = I.extract_included(doc(name), review_id or "rv-pmid-99990000", at=AT)
    return _RESULTS[key]


def by_label(res, label):
    return [c for c in res["candidates"] if c.get("study_label_in_review") == label]


def by_ref(res, ref_id):
    out = [c for c in res["candidates"] if c.get("ref_id") == ref_id]
    return out[0] if out else None


def ev_map(res):
    return {e["evidence_id"]: e for e in res["evidence"]}


def secondary(cand):
    return {(s["field"], s["evidence_id"]): s for s in cand["secondary_data"]}


def fields(cand, element_id=None, res=None):
    """{mező: érték} egy jelölt másodlagos adataiból (opcionálisan egy táblára szűrve)."""
    evs = ev_map(res) if res is not None else {}
    out = {}
    for s in cand["secondary_data"]:
        if element_id is not None and evs[s["evidence_id"]]["locator"].get("element_id") != element_id:
            continue
        out[s["field"]] = s["value"]
    return out


def skeleton(review_id):
    return {"schema": "szk.ma.headhunter.review/v1", "model": "szk.ma.headhunter/v1", "review_id": review_id,
            "status": "selected", "ids": {}, "bib": {}, "found_by": [], "candidates": [], "evidence": []}


SYNTHETIC = ["cochrane_nested_reflist", "bmj_table_author_year", "springer_table_multirow", "forest_data_table",
             "no_structure", "statement_only"]


def real_names():
    if not os.path.isdir(REAL):
        return []
    return sorted("real/" + f[:-4] for f in os.listdir(REAL) if f.endswith(".xml"))


def load_expected():
    with open(os.path.join(REAL, "expected.json"), encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# jats.py — normalizálás, címkék, beolvasás
# ---------------------------------------------------------------------------

class TestNormalizers(unittest.TestCase):

    def test_doi(self):
        self.assertEqual(J.normalize_doi("https://doi.org/10.1016/S0140-6736(13)60177-4."),
                         "10.1016/s0140-6736(13)60177-4")
        self.assertEqual(J.normalize_doi("doi: 10.5588/IJTLD.16.0709"), "10.5588/ijtld.16.0709")
        self.assertEqual(J.normalize_doi("dx.doi.org/10.1093/cid/cix834)"), "10.1093/cid/cix834")
        self.assertIsNone(J.normalize_doi("not a doi"))
        self.assertIsNone(J.normalize_doi(None))

    def test_pmid_pmcid_nct(self):
        self.assertEqual(J.normalize_pmid("PMID: 23391465"), "23391465")
        self.assertEqual(J.normalize_pmid("https://pubmed.ncbi.nlm.nih.gov/23391465/"), "23391465")
        self.assertIsNone(J.normalize_pmid("2339x465"))
        self.assertEqual(J.normalize_pmcid("pmc6488980"), "PMC6488980")
        self.assertEqual(J.normalize_pmcid("6488980"), "PMC6488980")
        self.assertEqual(J.normalize_nct("registered as nct 00953927."), "NCT00953927")
        self.assertIsNone(J.normalize_nct("NCT123"))

    def test_registry_ids_in_text(self):
        got = J.registry_ids_in_text("Trials ISRCTN12345678, ACTRN12611000123456, EudraCT 2010-012345-12, "
                                     "NCT00953927 and ChiCTR2000029559; again NCT00953927.")
        self.assertIn(("isrctn", "ISRCTN12345678"), got)
        self.assertIn(("actrn", "ACTRN12611000123456"), got)
        self.assertIn(("eudract", "2010-012345-12"), got)
        self.assertIn(("chictr", "ChiCTR2000029559"), got)
        self.assertEqual([v for k, v in got if k == "nct"], ["NCT00953927"])  # ismétlés nélkül

    def test_name_normalization(self):
        self.assertEqual(J.norm_name("van Nielen"), "vannielen")
        self.assertEqual(J.norm_name("Brønfort"), J.norm_name("Bronfort"))
        self.assertEqual(J.norm_name("Groß"), "gross")
        self.assertEqual(J.norm_name("D'Haens"), "dhaens")

    def test_parse_author_year(self):
        cases = {
            "Adetifa 2010": ("Adetifa", 2010, None, None),
            "1 Acharjee, S. 2015 Israel": ("Acharjee", 2015, None, "1"),
            "2.1 Anderson, J. W. 2007 USA": ("Anderson", 2007, None, "2.1"),
            "Reverri, E. J. USA 2015": ("Reverri", 2015, None, None),
            "van Nielen, M. 2014": ("van Nielen", 2014, None, None),
            "Smith et al. (2010)": ("Smith", 2010, None, None),
            "Sands 2004b": ("Sands", 2004, "b", None),
            "EPIC‐HR 2021": ("EPIC‐HR", 2021, None, None),
        }
        for label, (sn, year, suf, code) in cases.items():
            p = J.parse_author_year(label)
            self.assertIsNotNone(p, label)
            self.assertEqual((p["surname"], p["year"], p["suffix"], p["code"]), (sn, year, suf, code), label)
        for bad in ("Fixed effects model", "Pain/short term", "", None, "2012 [47]"):
            self.assertIsNone(J.parse_author_year(bad), bad)

    def test_first_author_and_year_from_text(self):
        self.assertEqual(J.first_author_from_text("Adetifa IM, Ota MO, Jeffries DJ. Commercial assays. "
                                                  "Pediatr Infect Dis J 2010;29:439-43."), "Adetifa")
        self.assertEqual(J.first_author_from_text("J. W. Anderson, B. M. Smith. Title."), "Anderson")
        self.assertEqual(J.year_from_text("Smith A. Title. J 2015;64(2):1-9. doi:10.1000/2019.12"), (2015, None))
        self.assertEqual(J.year_from_text("Smith A (2013a) Title"), (2013, "a"))
        self.assertEqual(J.year_from_text("no year here"), (None, None))

    def test_section_classification(self):
        self.assertEqual(J.classify_ref_section("References to studies included in this review"), "included")
        self.assertEqual(J.classify_ref_section("References to studies excluded from this review"), "excluded")
        self.assertEqual(J.classify_ref_section("References to studies awaiting assessment"), "awaiting")
        self.assertEqual(J.classify_ref_section("References to ongoing studies"), "ongoing")
        self.assertEqual(J.classify_ref_section("Additional references"), "additional")
        self.assertEqual(J.classify_ref_section("References to other published versions of this review"),
                         "other_versions")
        self.assertIsNone(J.classify_ref_section("References"))
        self.assertEqual(J.clean_group_title("Tameris 2013 {published data only}"), "Tameris 2013")
        self.assertEqual(J.section_kind("Search methods for identification of studies"), "methods")
        self.assertEqual(J.section_kind("Description of studies"), "results")


class TestJatsParse(unittest.TestCase):

    def test_meta_cochrane(self):
        d = doc("cochrane_nested_reflist")
        m = d.meta
        self.assertEqual(m["ids"], {"pmid": "99990100", "pmcid": "PMC99990100",
                                    "doi": "10.1002/14651858.cd999901.pub2"})
        self.assertTrue(m["is_cochrane"])
        self.assertEqual(m["cochrane"], {"cd_number": "CD999901", "version": 2})
        self.assertEqual(m["pub_date"], "2019-04-29")
        self.assertEqual(m["license"], "cc by-nc")
        self.assertEqual(d.container, "PMC99990100")
        with open(os.path.join(FIX, "cochrane_nested_reflist.xml"), "rb") as fh:
            self.assertEqual(J.parse(fh.read(), container="X1").container, "X1")

    def test_license_variants(self):
        self.assertEqual(doc("bmj_table_author_year").meta["license"], "cc by-nc")
        self.assertEqual(doc("springer_table_multirow").meta["license"], "cc by-nc-nd")
        self.assertIsNone(doc("no_structure").meta["license"])
        self.assertEqual(doc("no_structure").meta["pub_date"], "2020-02")

    def test_reference_ids_from_both_places(self):
        d = doc("cochrane_nested_reflist")
        self.assertEqual(d.refs["CD999901-bib-0001"].ids["pmid"], ("99990001", "jats.pub-id"))
        self.assertEqual(d.refs["CD999901-bib-0001"].ids["doi"], ("10.5555/hh.0001", "jats.pub-id"))
        self.assertEqual(d.refs["CD999901-bib-0002"].ids["pmid"], ("99990002", "jats.ext-link"))
        self.assertEqual(d.refs["CD999901-bib-0004"].ids["doi"], ("10.5555/hh.0004", "jats.text"))
        self.assertEqual(d.refs["CD999901-bib-0005"].ids["nct"], ("NCT99990005", "jats.text"))
        b = doc("bmj_table_author_year")
        self.assertEqual(b.refs["ref1"].ids["doi"], ("10.5555/hh.0011", "jats.ext-link"))
        self.assertEqual(b.refs["ref10"].ids["pmid"], ("99990020", "jats.text"))

    def test_reference_fields(self):
        d = doc("cochrane_nested_reflist")
        r = d.refs["CD999901-bib-0001"]
        self.assertTrue(r.primary_marked)
        self.assertFalse(r.text.startswith("*"))
        self.assertEqual((r.first_author, r.year, r.journal), ("Alfa", 2010, "Synthetic Journal of Medicine"))
        self.assertEqual(r.title, "Vaccine X in adults: a randomised trial")
        self.assertEqual(r.section, "included")
        self.assertEqual(r.group_id, "CD999901-bbs2-0001")
        b = doc("bmj_table_author_year")
        self.assertEqual(b.refs["ref2"].first_author, "Golf")
        self.assertEqual(b.refs["ref2"].authors, ["Golf", "Alfa"])
        self.assertEqual((b.refs["ref5"].year, b.refs["ref5"].year_suffix), (2008, "a"))
        self.assertEqual(b.refs["ref1"].label, "1")
        ns = doc("no_structure")
        self.assertNotIn("20152", ns.refs["R1"].text)  # element-citation: év és kötet nem olvad össze

    def test_cochrane_sections_and_groups(self):
        d = doc("cochrane_nested_reflist")
        kinds = [s.kind for s in d.ref_sections]
        self.assertEqual(kinds, ["included", "excluded", "awaiting", "ongoing", "additional", "other_versions"])
        inc = d.ref_sections[0]
        self.assertEqual(inc.group_ids, ["CD999901-bbs2-0001", "CD999901-bbs2-0002", "CD999901-bbs2-0003",
                                         "CD999901-bbs2-0004"])
        g = d.groups["CD999901-bbs2-0001"]
        self.assertEqual((g.label, g.title), ("Alfa 2010", "Alfa 2010 {published data only}"))
        self.assertEqual(g.ref_ids, ["CD999901-bib-0001", "CD999901-bib-0002"])
        self.assertEqual(d.refs_for_rid("CD999901-bbs2-0001"), ["CD999901-bib-0001", "CD999901-bib-0002"])
        self.assertEqual(d.summary()["groups"]["included"], 4)

    def test_table_grid_and_header_paths(self):
        d = doc("forest_data_table")
        t2 = [t for t in d.tables if t.element_id == "t2"][0]
        self.assertEqual(t2.label, "Table 2")
        self.assertEqual(t2.columns, ["Study", "Treatment T / Events", "Treatment T / Total", "Placebo / Events",
                                      "Placebo / Total", "Risk ratio (95% CI)"])
        self.assertEqual(len(t2.body), 2)
        self.assertTrue(all(len(r) == t2.ncols for r in t2.body))
        s = doc("springer_table_multirow").tables[0]
        q1, q2 = s.body[8][0], s.body[9][0]  # 'Quebec' rowspan=2
        self.assertFalse(q1.spanned)
        self.assertTrue(q2.spanned)
        self.assertEqual(q2.origin, q1.origin)
        self.assertEqual(s.body[0][0].paragraphs, ["1", "Kilo, S.", "2015", "Israel"])
        self.assertEqual(s.body[0][0].text, "1 Kilo, S. 2015 Israel")

    def test_cell_label_drops_citation_marks(self):
        b = doc("bmj_table_author_year").tables[0]
        fox = b.body[6][0]
        self.assertEqual(fox.text, "Foxtrot 20139")
        self.assertEqual(fox.text_label, "Foxtrot 2013")
        self.assertEqual([x["rid"] for x in fox.xrefs], ["ref9"])
        hotel = b.body[7][0]
        self.assertEqual(hotel.sups, ["10"])
        self.assertEqual(hotel.text_label, "Hotel 2014")
        alfa_n = b.body[0][2]
        self.assertEqual((alfa_n.text, alfa_n.text_nosup), ("236†", "236"))

    def test_footnote_letter_superscript_in_label(self):
        xml = (b'<article><body><table-wrap id="t1"><caption><title>Characteristics of included studies</title>'
               b'</caption><table><thead><tr><th>Study</th><th>N</th></tr></thead><tbody><tr><td>Egeland'
               b'<xref rid="fn1" ref-type="table-fn"><sup>i</sup></xref> (2013)<xref rid="b1" ref-type="bibr">'
               b'<sup>1</sup></xref></td><td>67</td></tr></tbody></table></table-wrap></body><back><ref-list>'
               b'<ref id="b1"><label>1</label><mixed-citation>Egeland J. Title. J 2013;1:1.</mixed-citation></ref>'
               b'</ref-list></back></article>')
        cell = J.parse(xml).tables[0].body[0][0]
        self.assertEqual(cell.text_label, "Egeland (2013)")
        self.assertEqual(J.parse_author_year(cell.text_label)["label"], "Egeland 2013")

    def test_xref_range_expansion(self):
        d = doc("statement_only")
        p = [p for p in d.paragraphs if p.kind == "results"][0]
        self.assertEqual(d.bibr_refs(p.xrefs, p.text), ["r3", "r4", "r5", "r6", "r7"])
        self.assertEqual(d.expand_range("r3", "r5"), ["r3", "r4", "r5"])
        self.assertEqual(d.refs_for_number(10), ("r10", "label"))

    def test_sections_paragraph_kinds(self):
        d = doc("statement_only")
        kinds = [(p.section_path[-1], p.kind) for p in d.paragraphs]
        self.assertEqual(kinds, [("Introduction", "introduction"), ("Methods", "methods"), ("Results", "results"),
                                 ("Discussion", "discussion")])
        c = doc("cochrane_nested_reflist")
        self.assertTrue(any(p.in_abstract for p in c.paragraphs))

    def test_plain_text_and_find_quote(self):
        d = doc("statement_only")
        txt = d.plain_text(parts=("methods", "results"))
        self.assertIn("The date of the last search was 14 February 2021.", txt)
        self.assertNotIn("A previous review", txt)
        self.assertIn("[r3]", d.plain_text(parts=("references",)))
        self.assertTrue(J.find_quote(d, "We  included five\n trials"))
        self.assertFalse(J.find_quote(d, "We included six trials"))
        self.assertFalse(J.find_quote(d, "   "))
        self.assertTrue(J.find_quote("a  b\nc", "a b c"))

    def test_security_and_errors(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "xx">]><article><body><p>&x;</p></body></article>'
        with self.assertRaises(J.JatsError):
            J.parse(bomb)
        with self.assertRaises(J.JatsError):
            J.parse(b"<article><body><p>broken</body></article>")
        with self.assertRaises(J.JatsError):
            J.parse(b"<html><body/></html>")
        d = J.parse("<article><body><sec><title>Methods</title><p>A&nbsp;B &amp; C</p></sec></body></article>")
        self.assertEqual(d.paragraphs[0].text, "A B & C")  # &nbsp; → U+00A0 → szóköz (normalize_ws)
        latin = '<?xml version="1.0" encoding="ISO-8859-1"?><article><body><p>Gr\xf6\xdf</p></body></article>'
        self.assertEqual(J.parse(latin.encode("latin-1")).paragraphs[0].text, "Größ")
        wrapped = b"<pmc-articleset><article><body><p>x</p></body></article></pmc-articleset>"
        self.assertEqual(J.parse(wrapped).paragraphs[0].text, "x")


# ---------------------------------------------------------------------------
# a1 — Cochrane
# ---------------------------------------------------------------------------

class TestCochraneA1(unittest.TestCase):

    def setUp(self):
        self.r = result("cochrane_nested_reflist", "rv-pmid-99990100")

    def test_included_candidates(self):
        inc = [c for c in self.r["candidates"] if c["role_in_review"] in ("included", "included_companion")]
        self.assertEqual([c["study_label_in_review"] for c in inc],
                         ["Alfa 2010", "Alfa 2010", "Bravo 2012", "Charlie 2015a", "Charlie 2015b"])
        for c in inc:
            self.assertEqual((c["confidence"], c["status"]), ("high", "confirmed"))
            self.assertFalse(c["needs_review"])
        a1, a2 = by_ref(self.r, "CD999901-bib-0001"), by_ref(self.r, "CD999901-bib-0002")
        self.assertEqual((a1["role_in_review"], a2["role_in_review"]), ("included", "included_companion"))
        self.assertTrue(a1["primary_marked"])
        self.assertEqual(a1["group_key"], a2["group_key"])
        self.assertEqual(self.r["n_study_groups"], 4)
        self.assertEqual(self.r["strategies"][0], "jats_cochrane_included")

    def test_awaiting_ongoing_excluded(self):
        aw = by_ref(self.r, "CD999901-bib-0008")
        on = by_ref(self.r, "CD999901-bib-0009")
        self.assertEqual((aw["role_in_review"], aw["status"]), ("awaiting", "proposed"))
        self.assertEqual(on["role_in_review"], "ongoing")
        self.assertEqual([x["value"] for x in on["study_registry_in_review"]], ["NCT99990003"])
        ex = self.r["excluded_by_review"]
        self.assertEqual([(x["study_label"], x["reason_quote"]) for x in ex],
                         [("Delta 2009", "Not randomised."), ("Echo 2011", "Wrong comparator (active vaccine).")])
        evs = ev_map(self.r)
        for x in ex:
            self.assertIn(x["evidence_id"], evs)
            self.assertEqual(evs[x["reason_evidence_id"]]["locator"]["column"], "Reason for exclusion")
        # 'Additional references' és 'other versions' nem jelölt
        self.assertIsNone(by_ref(self.r, "CD999901-bib-0010"))
        self.assertIsNone(by_ref(self.r, "CD999901-bib-0011"))

    def test_evidence_locator_and_quote(self):
        evs = ev_map(self.r)
        a1 = by_ref(self.r, "CD999901-bib-0001")
        first = evs[a1["evidence_ids"][0]]
        self.assertEqual(first["kind"], "reference_section")
        self.assertEqual(first["strategy"], "jats_cochrane_included")
        self.assertEqual(first["locator"]["container"], "PMC99990100")
        self.assertEqual(first["locator"]["element_id"], "CD999901-bbs2-0001")
        self.assertEqual(first["locator"]["section"], "References to studies included in this review")
        self.assertEqual(first["quote"], "Alfa 2010 {published data only}")
        # a jellemzők-tábla megerősít (a felirat egy xref a csoportra)
        confirm = [evs[e] for e in a1["evidence_ids"] if evs[e]["locator"].get("element_id") == "CD999901-tbl-0010"]
        self.assertEqual(len(confirm), 1)

    def test_ids_are_review_sourced(self):
        a1 = by_ref(self.r, "CD999901-bib-0001")
        self.assertEqual(a1["ids"]["pmid"], {"value": "99990001", "source": "review", "via": "jats.pub-id",
                                             "at": AT})
        self.assertEqual(by_ref(self.r, "CD999901-bib-0002")["ids"]["pmid"]["via"], "jats.ext-link")

    def test_secondary_data_on_primary_report_only(self):
        a1, a2 = by_ref(self.r, "CD999901-bib-0001"), by_ref(self.r, "CD999901-bib-0002")
        self.assertEqual(fields(a1), {"e1": 12, "n1": 100, "e2": 9, "n2": 98})
        self.assertEqual(a2["secondary_data"], [])
        evs = ev_map(self.r)
        cell_ev = set(s["evidence_id"] for s in a1["secondary_data"])
        self.assertTrue(cell_ev <= set(a2["evidence_ids"]))  # a társközlemény a cella-bizonyítékot megkapja
        self.assertTrue(all(evs[e]["locator"]["element_id"] == "CD999901-tbl-0001" for e in cell_ev))
        bravo = by_ref(self.r, "CD999901-bib-0003")
        self.assertEqual(fields(bravo), {"n1": 250, "n2": 251})  # 'NR' kimarad
        for s in a1["secondary_data"]:
            self.assertEqual((s["status"], s["data_source"]), ("unverified", "secondary"))
            self.assertIn(s["arm"], ("Vaccine X", "Placebo"))

    def test_counts_and_search_date(self):
        self.assertEqual((self.r["k_reported"]["value"], self.r["k_reported"]["unit"]), (4, "studies"))
        self.assertTrue(self.r["completeness"]["match"])
        self.assertEqual(self.r["search_date"]["value"], "2018-05-15")  # a legkésőbbi (Módszertan)
        self.assertFalse(self.r["search_date"]["fallback"])
        self.assertEqual(self.r["warnings"], [])

    def test_strategy_subset(self):
        r = I.extract_included(doc("cochrane_nested_reflist"), "rv-pmid-99990100", at=AT, strategies=("a1",))
        self.assertEqual(r["strategies"], ["jats_cochrane_included"])
        self.assertTrue(all(not c["secondary_data"] for c in r["candidates"]))


# ---------------------------------------------------------------------------
# a2 — bevont-vizsgálat táblák
# ---------------------------------------------------------------------------

class TestTableA2(unittest.TestCase):

    def setUp(self):
        self.r = result("bmj_table_author_year", "rv-pmid-99990200")

    def test_first_author_trap(self):
        alfa = by_label(self.r, "Alfa 2010")
        self.assertEqual(len(alfa), 1)
        self.assertEqual(alfa[0]["ref_id"], "ref1")  # a ref2-ben Alfa csak társszerző
        self.assertEqual(alfa[0]["confidence"], "medium")
        self.assertIn("author_year_match", alfa[0]["review_reasons"])
        self.assertTrue(alfa[0]["needs_review"])
        self.assertIsNone(by_ref(self.r, "ref2"))
        self.assertEqual(alfa[0]["ids"]["pmid"]["via"], "jats.ext-link")

    def test_suffix_and_ambiguity(self):
        self.assertEqual(by_label(self.r, "Delta 2008a")[0]["ref_id"], "ref5")
        self.assertEqual(by_label(self.r, "Delta 2008b")[0]["ref_id"], "ref6")
        echo = by_label(self.r, "Echo 2012")[0]
        self.assertIsNone(echo["ref_id"])
        self.assertEqual(echo["confidence"], "low")
        self.assertEqual(echo["ids"], {})  # kétértelmű illesztésből SOHA nincs azonosító
        self.assertEqual(echo["ref_hint"]["reason"], "ambiguous_first_author_year")
        self.assertEqual(echo["ref_hint"]["ref_ids"], ["ref7", "ref8"])
        self.assertNotIn("ids", echo["ref_hint"])
        charlie = by_label(self.r, "Charlie 2009")[0]
        self.assertEqual(charlie["ref_hint"]["reason"], "similar_first_author_same_year")
        self.assertEqual(charlie["ref_hint"]["ref_ids"], ["ref4"])
        self.assertEqual(charlie["ids"], {})

    def test_xref_and_superscript_rows_are_high(self):
        fox = by_label(self.r, "Foxtrot 2013")[0]
        hotel = by_label(self.r, "Hotel 2014")[0]
        self.assertEqual((fox["ref_id"], fox["confidence"]), ("ref9", "high"))
        self.assertEqual((hotel["ref_id"], hotel["confidence"]), ("ref10", "high"))
        self.assertFalse(fox["needs_review"])

    def test_participant_counts_secondary(self):
        self.assertEqual(fields(by_label(self.r, "Alfa 2010")[0]), {"n_total": 236})
        self.assertEqual(fields(by_label(self.r, "Foxtrot 2013")[0]), {"n_total": 1234})
        self.assertEqual(fields(by_label(self.r, "Hotel 2014")[0]), {"n_total": 45})
        self.assertEqual(fields(by_label(self.r, "Charlie 2009")[0]), {})  # 'NS'
        s = by_label(self.r, "Alfa 2010")[0]["secondary_data"][0]
        self.assertIsNone(s["outcome"])  # a jellemzők-tábla felirata nem kimenet
        self.assertEqual(s["raw"], "236")

    def test_table_locator(self):
        evs = ev_map(self.r)
        e = evs[by_label(self.r, "Bravo 2011")[0]["evidence_ids"][0]]
        self.assertEqual((e["kind"], e["strategy"]), ("table_row", "jats_table"))
        self.assertEqual(e["locator"]["element_id"], "tbl1")
        self.assertEqual(e["locator"]["label"], "Table 1")
        self.assertEqual(e["locator"]["row"], 2)
        self.assertEqual(e["locator"]["column"], "Study")
        self.assertEqual(e["quote"], "Bravo 2011 | Outbreak")

    def test_counts(self):
        self.assertEqual(self.r["k_reported"]["value"], 8)
        self.assertEqual(self.r["search_date"]["value"], "2013-11")
        self.assertEqual(self.r["n_study_groups"], 8)


class TestMultirowA2(unittest.TestCase):

    def setUp(self):
        self.r = result("springer_table_multirow", "rv-pmid-99990300")
        self.evs = ev_map(self.r)

    def rows(self, label):
        c = by_label(self.r, label)
        self.assertEqual(len(c), 1, label)
        e = self.evs[c[0]["evidence_ids"][0]]
        return c[0], e["locator"].get("rows") or [e["locator"]["row"]]

    def test_one_candidate_per_study(self):
        labels = [c["study_label_in_review"] for c in self.r["candidates"]]
        self.assertEqual(labels, ["Kilo 2015", "Lima 2007", "Mike 2007", "Mike 2008", "van Nielsen 2014",
                                  "Oscar 2015", "Quebec 2012", "Papa 2016"])
        self.assertEqual(self.rows("Lima 2007")[1], [2, 3])
        self.assertEqual(self.rows("Mike 2007")[1], [4, 5])
        self.assertEqual(self.rows("Mike 2008")[1], [6])
        self.assertEqual(self.rows("Quebec 2012")[1], [9, 10])
        papa, rows = self.rows("Papa 2016")
        self.assertEqual(rows, [11, 12])
        self.assertEqual(papa["ref_id"], "CR8")
        self.assertEqual(self.evs[papa["evidence_ids"][0]]["quote"], "Papa, | 2016 | Adults N = 50")

    def test_matching(self):
        want = {"Kilo 2015": "CR1", "Lima 2007": "CR2", "Mike 2007": "CR3", "Mike 2008": "CR4",
                "van Nielsen 2014": "CR5", "Oscar 2015": "CR6", "Quebec 2012": "CR7"}
        for lab, rid in want.items():
            self.assertEqual(by_label(self.r, lab)[0]["ref_id"], rid, lab)
        self.assertIsNone(by_ref(self.r, "CR9"))  # Kilo csak társszerző

    def test_statement_confirms(self):
        kilo = by_label(self.r, "Kilo 2015")[0]
        strategies = set(self.evs[e]["strategy"] for e in kilo["evidence_ids"])
        self.assertEqual(strategies, {"jats_table", "jats_xref"})
        self.assertEqual((self.r["k_reported"]["value"], self.r["k_reported"]["unit"]), (8, "trials"))
        self.assertTrue(self.r["completeness"]["match"])
        self.assertEqual(self.r["warnings"], [])


# ---------------------------------------------------------------------------
# a3 — adattáblák, másodlagos adatok
# ---------------------------------------------------------------------------

class TestForestA3(unittest.TestCase):

    def setUp(self):
        self.r = result("forest_data_table", "rv-doi-hh05000000")
        self.y = by_label(self.r, "Yankee 2015")[0]
        self.z = by_label(self.r, "Zulu 2017")[0]
        self.a = by_label(self.r, "Alpha 2019")[0]

    def test_dichotomous(self):
        self.assertEqual(fields(self.y, "t2", self.r), {"e1": 12, "n1": 100, "e2": 20, "n2": 101, "effect": 0.61,
                                                        "ci_lo": 0.31, "ci_hi": 1.17, "measure": "RR"})
        # NR kimarad; ezres elválasztó és lábjegyzet-jel; '[lo, hi]' CI
        self.assertEqual(fields(self.z, "t2", self.r), {"n1": 1250, "e2": 9, "n2": 1248, "effect": 0.85,
                                                        "ci_lo": 0.4, "ci_hi": 1.8, "measure": "RR"})

    def test_continuous_and_separate_ci(self):
        self.assertEqual(fields(self.y, "t3", self.r), {"m1": -1.5, "sd1": 2.25, "n1": 100, "m2": -0.5, "sd2": 2.0,
                                                        "e2": 3, "n2": 101, "effect": -0.47, "measure": "SMD",
                                                        "ci_lo": -0.75, "ci_hi": -0.19})
        # 3.2×10⁷ (számos felső index) nem rögzül; 'NR' sem
        self.assertEqual(fields(self.a, "t3", self.r), {"sd1": 1.1, "n1": 48, "e2": 5, "n2": 50, "effect": 0.12,
                                                        "measure": "SMD", "ci_lo": -0.28, "ci_hi": 0.52})

    def test_ambiguous_headers_not_captured(self):
        evs = ev_map(self.r)
        for c in self.r["candidates"]:
            for s in c["secondary_data"]:
                self.assertNotEqual(evs[s["evidence_id"]]["locator"]["element_id"], "t4")
        info = I.classify_table([t for t in doc("forest_data_table").tables if t.element_id == "t4"][0],
                                doc("forest_data_table"))
        self.assertTrue(all(col["field"] is None for col in info["columns"].values()))

    def test_secondary_value_shape(self):
        evs = ev_map(self.r)
        for c in self.r["candidates"]:
            for s in c["secondary_data"]:
                self.assertEqual(s["status"], "unverified")
                self.assertEqual(s["data_source"], "secondary")
                self.assertIsNone(s["verified_decision"])
                e = evs[s["evidence_id"]]
                self.assertEqual((e["kind"], e["strategy"], e["confidence"]), ("forest_plot", "jats_forest", "medium"))
                self.assertIsNotNone(e["locator"]["row"])
                self.assertEqual(e["locator"]["column"], s["column"])
                self.assertTrue(s["outcome"].startswith("Analysis 1."))
        arm = {s["field"]: s["arm"] for s in self.y["secondary_data"] if s["field"] in ("m1", "m2")}
        self.assertEqual(arm, {"m1": "Experimental", "m2": "Control"})

    def test_candidates_are_medium_proposed(self):
        for c in self.r["candidates"]:
            self.assertEqual((c["confidence"], c["status"], c["role_in_review"]), ("medium", "proposed", "included"))
        self.assertEqual(self.r["search_date"]["value"], "2020-03")  # 'between January 2000 and March 2020'


class TestClassifyColumn(unittest.TestCase):

    def test_measures(self):
        cc = I.classify_column
        self.assertEqual(cc(["Intervention", "Events"])["field"], "e")
        self.assertEqual(cc(["Intervention", "Events"])["arm_index"], 1)
        self.assertEqual(cc(["Control", "Mean (SD)"])["measure"], "m_sd")
        self.assertEqual(cc(["Placebo", "n/N"])["measure"], "e_n")
        self.assertEqual(cc(["Total"])["field"], "n_total")
        self.assertEqual(cc(["Odds ratio (95% CI)"])["effect_measure"], "OR")
        self.assertEqual(cc(["Hedges' g"])["effect_measure"], "SMD")
        self.assertEqual(cc(["95% CI"])["measure"], "ci")
        self.assertIsNone(cc(["Age (mean ± SD)"]))
        self.assertIsNone(cc(["Inclusion criteria"]))
        self.assertIsNone(cc(["Weight (%)"]))
        # karszó nélkül a mező kétértelmű (nem rögzítünk számot)
        self.assertIsNone(cc(["Dose A", "n"])["field"])
        self.assertTrue(cc(["Dose A", "n"])["ambiguous"])
        # az 'or' kötőszó nem esélyhányados
        self.assertIsNone(cc(["Mean or median"]))

    def test_parse_cell_values(self):
        pv = I.parse_cell_values
        self.assertEqual(pv("12/50 (24%)", "e_n"), [("e", 12, "12/50 (24%)"), ("n", 50, "12/50 (24%)")])
        self.assertEqual(pv("60/50", "e_n"), [])  # e > n: nem érvényes
        self.assertEqual(pv("5.1 (2.1)", "m_sd"), [("m", 5.1, "5.1 (2.1)"), ("sd", 2.1, "5.1 (2.1)")])
        self.assertEqual(pv("3.7 ± 1.2", "m_sd"), [("m", 3.7, "3.7 ± 1.2"), ("sd", 1.2, "3.7 ± 1.2")])
        self.assertEqual(pv("−0.47 (−0.75 to −0.19)", "effect")[1:],
                         [("ci_lo", -0.75, "−0.47 (−0.75 to −0.19)"), ("ci_hi", -0.19, "−0.47 (−0.75 to −0.19)")])
        self.assertEqual(pv("1.2 (2.0 to 0.5)", "effect"), [])  # fordított CI: nem érvényes
        self.assertEqual(pv("NR", "n"), [])
        self.assertEqual(pv("-5", "n"), [])
        self.assertEqual(pv("3.2", "n"), [])
        self.assertEqual(pv("1 234", "n"), [("n", 1234, "1 234")])
        self.assertEqual(pv("12", "n", sups=("7",)), [])


# ---------------------------------------------------------------------------
# a4 — szöveges állítás; tartalék
# ---------------------------------------------------------------------------

class TestStatementA4AndFallback(unittest.TestCase):

    def test_statement_only(self):
        r = result("statement_only", "rv-pmid-99990600")
        self.assertEqual([c["ref_id"] for c in r["candidates"]], ["r3", "r4", "r5", "r6", "r7"])
        for c in r["candidates"]:
            self.assertEqual((c["confidence"], c["status"], c["group_key"]), ("medium", "proposed", None))
        self.assertEqual(r["strategies"], ["jats_xref"])
        self.assertEqual(r["n_study_groups"], 5)
        self.assertEqual(r["search_date"]["value"], "2021-02-14")
        self.assertEqual(r["warnings"], [])  # a Bevezetés hivatkozásai nem „gazdátlan" bevont vizsgálatok
        e = ev_map(r)[r["candidates"][0]["evidence_ids"][0]]
        self.assertEqual(e["quote"], "We included five trials [3–7] with 640 participants.")
        self.assertEqual(e["locator"]["section"], "Results")

    def test_inconsistent_statement_does_not_create(self):
        xml = (b'<article><body><sec><title>Results</title><p>We included 14 studies; three of them reported '
               b'adverse events [<xref ref-type="bibr" rid="b1">1</xref>].</p></sec></body><back><ref-list>'
               b'<ref id="b1"><mixed-citation>Aa A. T. J 2010;1:1.</mixed-citation></ref>'
               b'<ref id="b2"><mixed-citation>Bb B. T. J 2011;1:1.</mixed-citation></ref></ref-list></back></article>')
        r = I.extract_included(J.parse(xml), "rv-pmid-1", at=AT)
        self.assertTrue(all(c["role_in_review"] == "unknown" for c in r["candidates"]))
        self.assertEqual(r["strategies"], ["reflist_api"])

    def test_fallback_reference_list(self):
        r = result("no_structure", "rv-pmid-99990400")
        self.assertEqual(r["strategies"], ["reflist_api"])
        self.assertEqual([c["ref_id"] for c in r["candidates"]], ["R1", "R2", "R3", "R4"])
        for c in r["candidates"]:
            self.assertEqual((c["role_in_review"], c["confidence"], c["status"]), ("unknown", "low", "proposed"))
            self.assertTrue(c["needs_review"])
            self.assertIn("role_unknown", c["review_reasons"])
        ctx = by_ref(r, "R3")["context"]
        self.assertEqual((ctx["n_citations"], ctx["cited_in"]), (1, ["results"]))
        self.assertEqual(by_ref(r, "R4")["context"]["n_citations"], 0)
        codes = [w["code"] for w in r["warnings"]]
        self.assertEqual(codes, ["included_table_not_structured", "no_included_structure", "search_date_ambiguous"])
        for w in r["warnings"]:
            self.assertTrue(w["hu"] and w["en"])
        self.assertEqual((r["search_date"]["value"], r["search_date"]["precision"]), ("2019", "year"))
        self.assertTrue(r["search_date"]["ambiguous_numeric_date"])
        self.assertEqual(r["k_reported"]["value"], 16)

    def test_fallback_always_and_never(self):
        r = I.extract_included(doc("bmj_table_author_year"), "rv-pmid-1", at=AT, fallback="always")
        unknown = [c["ref_id"] for c in r["candidates"] if c["role_in_review"] == "unknown"]
        self.assertEqual(unknown, ["ref2", "ref4", "ref7", "ref8"])
        r = I.extract_included(doc("no_structure"), "rv-pmid-1", at=AT, fallback="never")
        self.assertEqual(r["candidates"], [])
        r = I.extract_included(doc("cochrane_nested_reflist"), "rv-pmid-1", at=AT, fallback="always")
        unknown = [c["ref_id"] for c in r["candidates"] if c["role_in_review"] == "unknown"]
        self.assertEqual(unknown, ["CD999901-bib-0010"])  # kizárt és korábbi-változat hivatkozás nem jelölt


# ---------------------------------------------------------------------------
# 6.0 — közölt k és keresési dátum
# ---------------------------------------------------------------------------

def _k(text):
    return [(s["value"], s["unit"]) for s in I.text_statements("Results\n\n" + text)["k_statements"]]


def _sd(text):
    return [(s["value"], s["precision"]) for s in I.text_statements("Methods\n\n" + text)["search_date_statements"]]


class TestCountsAndDates(unittest.TestCase):

    def test_k_patterns(self):
        self.assertEqual(_k("We included 12 randomised controlled trials (1500 participants)."), [(12, "trials")])
        self.assertEqual(_k("Twelve trials met the inclusion criteria."), [(12, "trials")])
        self.assertEqual(_k("Results 12 reports (13 randomised trials) were included."),
                         [(12, "reports"), (13, "trials")])
        self.assertEqual(_k("65 trials comprising 72 interventions and N = 8608 participants were included."),
                         [(65, "trials")])
        self.assertEqual(_k("This meta-analysis included study-level data from 13 trials."), [(13, "trials")])
        self.assertEqual(_k("Fifteen trials (reported in 16 papers) met entry criteria."),
                         [(15, "trials"), (16, "reports")])
        self.assertEqual(_k("According to the assessment, the five studies included were all RCTs."),
                         [(5, "studies")])
        self.assertEqual(_k("Twenty-one studies were included in the review."), [(21, "studies")])
        self.assertEqual(_k("Studies published between 2000 and 2019 were included."), [])
        self.assertEqual(_k("More than 10 studies were included."), [])
        self.assertEqual(_k("Ten studies were not included."), [])

    def test_best_k_prefers_abstract_then_results_max(self):
        stmts = I.text_statements("Abstract\n\nWe included 20 trials.\n\nResults\n\nWe included 22 trials. "
                                  "One trial was included in the subgroup.")["k_statements"]
        for i, s in enumerate(stmts):
            s["order"] = i
        best, conflicts = I._best_k(stmts)
        self.assertEqual(best["value"], 20)
        self.assertEqual(conflicts, [(20, "trials"), (22, "trials")])
        stmts = I.text_statements("Results\n\nIn all, 28 RCTs were finally included in the present "
                                  "meta-analysis. TSA included 13 RCTs.")["k_statements"]
        for i, s in enumerate(stmts):
            s["order"] = i
            s["section_kind"] = "results"
        self.assertEqual(I._best_k(stmts)[0]["value"], 28)

    def test_search_date_patterns(self):
        self.assertEqual(_sd("We searched CENTRAL up to 10 May 2018."), [("2018-05-10", "day")])
        self.assertEqual(_sd("We searched Embase (1980 until November 2013)."), [("2013-11", "month")])
        self.assertEqual(_sd("Each database was searched from its inception until 31/01/2019."),
                         [("2019-01-31", "day")])
        self.assertEqual(_sd("We searched from inception until 03/04/2019."), [("2019", "year")])
        self.assertEqual(_sd("Two investigators performed a systematic search on July 15, 2016 and updated on "
                             "November 15, 2016."), [("2016-07-15", "day"), ("2016-11-15", "day")])
        self.assertEqual(_sd("All RCTs were included from the available databases up to April, 2016."),
                         [("2016-04", "month")])
        self.assertEqual(_sd("We searched MEDLINE between January 2000 and March 2016."), [("2016-03", "month")])
        self.assertEqual(_sd("The literature search was last updated in August 2018."), [("2018-08", "month")])
        self.assertEqual(_sd("SilverPlatter Medline (1950 to October 2014) was searched."), [("2014-10", "month")])
        self.assertEqual(_sd("The search date was 2019-01-21."), [("2019-01-21", "day")])
        self.assertEqual(_sd("We searched for trials published in 2015."), [])
        self.assertEqual(_sd("We searched MEDLINE (1946 to present)."), [])
        self.assertEqual(_sd("The protocol was registered in PROSPERO on 1 June 2019."), [])

    def test_search_date_tiers(self):
        res = I.extract_counts_from_text("Introduction\n\nAn earlier review searched databases up to 2015.\n\n"
                                         "Methods\n\nWe searched MEDLINE up to 1 March 2019.", "rv-pmid-1", at=AT)
        self.assertEqual(res["search_date"]["value"], "2019-03-01")
        res = I.extract_counts_from_text("Introduction\n\nOngoing extensive searches of MEDLINE up to March 2017 "
                                         "identified 16 trials.", "rv-pmid-1", at=AT)
        self.assertEqual(res["search_date"]["value"], "2017-03")
        self.assertTrue(res["search_date"]["from_introduction"])
        self.assertIn("search_date_from_introduction", [w["code"] for w in res["warnings"]])

    def test_fallback_search_date(self):
        self.assertEqual(I.fallback_search_date("2019-04-29"), ("2018-04-29", "day"))
        self.assertEqual(I.fallback_search_date("2020-02-29"), ("2019-02-28", "day"))
        self.assertEqual(I.fallback_search_date("2019-04"), ("2018-04", "month"))
        self.assertEqual(I.fallback_search_date("2019"), ("2018", "year"))
        self.assertEqual(I.fallback_search_date("tavaly"), (None, None))
        r = I.extract_included(doc("forest_data_table"), "rv-pmid-1", at=AT)  # van dátum → nincs tartalék
        self.assertFalse(r["search_date"]["fallback"])
        xml = (b'<article><front><article-meta><pub-date pub-type="epub"><day>06</day><month>04</month>'
               b'<year>2019</year></pub-date></article-meta></front><body><sec><title>Results</title>'
               b'<p>We included three trials.</p></sec></body></article>')
        r = I.extract_included(J.parse(xml), "rv-pmid-1", at=AT)
        self.assertEqual(r["search_date"], {"value": "2018-04-06", "precision": "day", "fallback": True,
                                            "evidence_id": None, "basis": "pub_date_minus_12m",
                                            "pub_date": "2019-04-06"})
        self.assertIn("search_date_fallback", [w["code"] for w in r["warnings"]])
        r = I.extract_included(J.parse(xml), "rv-pmid-1", at=AT, pub_date="2019")
        self.assertEqual(r["search_date"]["value"], "2018")
        r = I.extract_included(J.parse(xml), "rv-pmid-1", at=AT, search_date_fallback=False)
        self.assertIsNone(r["search_date"])
        r = I.extract_included(J.parse(b"<article><body><p>x</p></body></article>"), "rv-pmid-1", at=AT)
        self.assertEqual((r["search_date"]["value"], r["search_date"]["precision"]), (None, "unknown"))

    def test_k_mismatch_warning(self):
        xml = (b'<article><body><sec><title>Results</title><p>We included three studies.</p>'
               b'<table-wrap id="t1"><caption><title>Characteristics of included studies</title></caption><table>'
               b'<thead><tr><th>Study</th><th>N</th></tr></thead><tbody>'
               b'<tr><td>Aa 2010</td><td>10</td></tr><tr><td>Bb 2011</td><td>12</td></tr></tbody></table>'
               b'</table-wrap></sec></body><back><ref-list><ref id="b1"><mixed-citation>Aa A. T. J 2010;1:1.'
               b'</mixed-citation></ref><ref id="b2"><mixed-citation>Bb B. T. J 2011;1:1.</mixed-citation></ref>'
               b'</ref-list></back></article>')
        r = I.extract_included(J.parse(xml), "rv-pmid-1", at=AT)
        self.assertEqual(r["completeness"], {"k_reported": 3, "unit": "studies", "n_study_groups": 2,
                                             "n_reports": 2, "match": False})
        w = [w for w in r["warnings"] if w["code"] == "k_mismatch"][0]
        self.assertIn("H006", w["hu"])

    def test_user_pdf_pages(self):
        res = I.extract_counts_from_text([(3, "Abstract\n\nTwelve trials were included."),
                                          (5, "Methods\n\nThe search was last updated on 1 April 2019.")],
                                         "rv-pmid-1", at=AT, container="user-pdf:ab12cd34")
        self.assertEqual(res["candidates"], [])
        self.assertEqual((res["k_reported"]["value"], res["search_date"]["value"]), (12, "2019-04-01"))
        evs = ev_map(res)
        k_ev = evs[res["k_reported"]["evidence_id"]]
        self.assertEqual((k_ev["kind"], k_ev["strategy"]), ("user_pdf", "user_pdf"))
        self.assertEqual(k_ev["locator"]["page"], "3")
        self.assertEqual(k_ev["locator"]["container"], "user-pdf:ab12cd34")
        self.assertEqual(evs[res["search_date"]["evidence_id"]]["locator"]["page"], "5")

    def test_pathological_text_is_fast(self):
        text = "Results\n\n" + ("Case. 12 27 28 29 30. " * 4000) + "We included 12 trials. " + ("1. 2. 3. " * 4000)
        t0 = time.time()
        I.text_statements(text)
        self.assertLess(time.time() - t0, 10.0)


# ---------------------------------------------------------------------------
# 6.2 — ágens-osztályozás importja (H004, azonosító eldobása) és API-irodalomjegyzék
# ---------------------------------------------------------------------------

class TestAgentImport(unittest.TestCase):

    def agent_doc(self, items, review_id="rv-pmid-99990400"):
        return {"review_id": review_id, "agent": "agent:ma-metaheadhunter", "created": AT, "items": items}

    def test_verbatim_quote_and_id_dropping(self):
        d = doc("no_structure")
        items = [
            {"ref_key": "R3", "role": "included", "quote": "Sixteen trials were\n included in the analyses",
             "pmid": "12345678", "doi": "10.9999/fake", "locator": {"section": "Results", "page": None}},
            {"ref_key": "R4", "role": "included", "quote": "Twenty trials were included in the analyses."},
            {"ref_key": "nope", "role": "included", "quote": "Sixteen trials were included in the analyses"},
            {"ref_key": "R1", "role": "maybe", "quote": "Sixteen trials were included in the analyses"},
            {"ref_key": "R2", "role": "background", "quote": "x" * 301},
            {"ref_key": "R2", "role": "background", "quote": "  "},
        ]
        res = I.candidates_from_agent_classification(d, self.agent_doc(items), "rv-pmid-99990400", at=AT)
        self.assertEqual([(x["index"], x["code"]) for x in res["rejected"]],
                         [(1, "H004"), (2, "unknown_ref_key"), (3, "invalid_role"), (4, "quote_too_long"),
                          (5, "missing_quote")])
        self.assertEqual(res["dropped"], [{"index": 0, "fields": ["doi", "pmid"]}])
        self.assertEqual(len(res["candidates"]), 1)
        c = res["candidates"][0]
        self.assertEqual((c["ref_id"], c["role_in_review"], c["confidence"], c["status"]),
                         ("R3", "included", "low", "proposed"))
        self.assertTrue(c["needs_review"])
        self.assertIn("agent_classified", c["review_reasons"])
        # azonosító csak a dokumentumból, soha az ágenstől
        self.assertEqual(c["ids"], {"pmid": {"value": "99990403", "source": "review", "via": "jats.pub-id",
                                             "at": AT}})
        e = res["evidence"][0]
        self.assertEqual((e["strategy"], e["extracted_by"], e["confidence"]),
                         ("reflist_agent", "agent:ma-metaheadhunter", "low"))
        self.assertEqual(e["quote"], "Sixteen trials were included in the analyses")
        self.assertEqual(e["locator"], {"container": None, "section": "Results", "ref_id": "R3"})

    def test_bad_agent_documents(self):
        d = doc("no_structure")
        with self.assertRaises(ValueError):
            I.candidates_from_agent_classification(d, self.agent_doc([], "rv-pmid-1"), "rv-pmid-2")
        bad = self.agent_doc([])
        bad["agent"] = "user:SzK"
        with self.assertRaises(ValueError):
            I.candidates_from_agent_classification(d, bad, "rv-pmid-99990400")
        bad = self.agent_doc([])
        bad["items"] = {"a": 1}
        with self.assertRaises(ValueError):
            I.candidates_from_agent_classification(d, bad, "rv-pmid-99990400")
        with self.assertRaises(ValueError):
            I.candidates_from_agent_classification(d, [], "rv-pmid-99990400")

    def test_plain_text_source(self):
        text = [(4, "Results\n\nWe included: Smith J. A trial of X. Lancet 2010;1:1-9. "
                    "Jones K. Another trial. BMJ 2012;2:3-4.")]
        items = [{"cited_as": "Smith J. A trial of X. Lancet 2010;1:1-9.", "role": "included",
                  "quote": "We included: Smith J. A trial of X.", "locator": {"page": 4}, "pmid": "1"},
                 {"cited_as": "Brown L. Invented trial. JAMA 2011;1:1.", "role": "included",
                  "quote": "We included: Smith J."}]
        res = I.candidates_from_agent_classification(text, self.agent_doc(items, "rv-pmid-5"), "rv-pmid-5", at=AT,
                                                     container="user-pdf:ab12")
        self.assertEqual([x["code"] for x in res["rejected"]], ["H004"])
        c = res["candidates"][0]
        self.assertEqual((c["cited_as"]["first_author"], c["cited_as"]["year"], c["ids"]), ("Smith", 2010, {}))
        self.assertEqual(res["evidence"][0]["locator"], {"container": "user-pdf:ab12", "page": "4"})

    def test_merge_updates_unknown_role(self):
        d = doc("no_structure")
        base = I.merge_into_review(skeleton("rv-pmid-99990400"), result("no_structure", "rv-pmid-99990400"))
        res = I.candidates_from_agent_classification(
            d, self.agent_doc([{"ref_key": "R3", "role": "included",
                                "quote": "Sixteen trials were included in the analyses"}]), "rv-pmid-99990400", at=AT)
        rv = I.merge_into_review(base, res)
        self.assertEqual(len(rv["candidates"]), len(base["candidates"]))
        c = [c for c in rv["candidates"] if c["ref_id"] == "R3"][0]
        self.assertEqual(c["role_in_review"], "included")
        self.assertNotIn("role_unknown", c["review_reasons"])
        self.assertIn("agent_classified", c["review_reasons"])
        self.assertEqual(c["status"], "proposed")
        self.assertEqual(I.merge_into_review(rv, res), rv)  # idempotens

    def test_merge_role_conflict_on_confirmed(self):
        d = doc("cochrane_nested_reflist")
        base = I.merge_into_review(skeleton("rv-pmid-99990100"), result("cochrane_nested_reflist", "rv-pmid-99990100"))
        res = I.candidates_from_agent_classification(
            d, self.agent_doc([{"ref_key": "CD999901-bib-0003", "role": "background",
                                "quote": "Bravo 2012 {published and unpublished data}"}], "rv-pmid-99990100"),
            "rv-pmid-99990100", at=AT)
        rv = I.merge_into_review(base, res)
        c = [c for c in rv["candidates"] if c["ref_id"] == "CD999901-bib-0003"][0]
        self.assertEqual((c["role_in_review"], c["status"]), ("included", "confirmed"))  # nem írja felül
        self.assertEqual(c["role_alternatives"][0]["role"], "background")
        self.assertIn("role_conflict", c["review_reasons"])
        self.assertTrue(c["needs_review"])


class TestReferenceRecords(unittest.TestCase):

    def test_api_reference_lists(self):
        records = [
            {"id": "23391465", "source": "MED", "authorString": "Tameris MD, Hatherill M, Landry BS.",
             "title": "Safety and efficacy of MVA85A", "pubYear": "2013", "journalAbbreviation": "Lancet"},
            {"id": "https://openalex.org/W2033432386", "display_name": "A work", "publication_year": 2013,
             "ids": {"doi": "https://doi.org/10.1016/S0140-6736(13)60177-4"},
             "authorships": [{"author": {"display_name": "Michele D. Tameris"}}]},
            {"citation": "Doe J. Untitled. 2001.", "article_ids": {"pubmed": "11111111"}},
            {"eid": "2-s2.0-84874743839", "sourcetitle": "Lancet", "ref-title": {"ref-titletext": "Safety"},
             "author-list": {"author": [{"ce:surname": "Scriba"}]}, "prism:doi": "10.5555/HH.0700"},
            {"id": "23391465", "source": "MED", "authorString": "Tameris MD.", "title": "dup", "pubYear": "2013"},
            {},
        ]
        res = I.candidates_from_reference_records("rv-pmid-31038197", records, "europepmc", at=AT)
        cands = res["candidates"]
        self.assertEqual(len(cands), 4)  # az ismétlődő és az üres rekord kimarad
        for c in cands:
            self.assertEqual((c["role_in_review"], c["confidence"], c["status"]), ("unknown", "low", "proposed"))
            self.assertTrue(c["needs_review"])
            for k, v in c["ids"].items():
                self.assertEqual((v["source"], v["via"]), ("europepmc", "europepmc.references"), k)
        self.assertEqual(cands[0]["ids"]["pmid"]["value"], "23391465")
        self.assertEqual(cands[0]["study_label_in_review"], "Tameris 2013")
        self.assertEqual(cands[1]["ids"]["openalex"]["value"], "W2033432386")
        self.assertEqual(cands[1]["ids"]["doi"]["value"], "10.1016/s0140-6736(13)60177-4")
        self.assertEqual(cands[1]["cited_as"]["first_author"], "Tameris")
        self.assertEqual(cands[2]["ids"]["pmid"]["value"], "11111111")
        self.assertEqual(cands[3]["ids"]["eid"]["value"], "2-s2.0-84874743839")
        self.assertEqual(cands[3]["ids"]["doi"]["value"], "10.5555/hh.0700")
        self.assertEqual(cands[3]["cited_as"]["first_author"], "Scriba")
        # azonos DOI más API-alakban: ugyanaz a hivatkozás, nem lesz külön jelölt
        dup = I.candidates_from_reference_records("rv-pmid-1", [records[1], {"doi": "10.1016/s0140-6736(13)60177-4"}],
                                                  "openalex", at=AT)
        self.assertEqual(len(dup["candidates"]), 1)
        self.assertEqual(res["strategies"], ["reflist_api"])
        self.assertEqual(res["evidence"][0]["kind"], "reference_list")


# ---------------------------------------------------------------------------
# invariánsok minden fixture-ön (N1, N4, séma, determinizmus)
# ---------------------------------------------------------------------------

class TestInvariants(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from ma_gui import schema_lite
        except ImportError:  # pragma: no cover - a plugin része
            raise unittest.SkipTest("ma_gui.schema_lite nem érhető el")
        cls.sl = schema_lite
        cls.reg = schema_lite.load_schema_dir(CONTRACTS)
        with open(os.path.join(CONTRACTS, "ma.headhunter.review.v1.schema.json"), encoding="utf-8") as fh:
            cls.schema = json.load(fh)

    def names(self):
        return SYNTHETIC + real_names()

    def test_schema_valid_review_documents(self):
        for name in self.names():
            res = result(name, "rv-pmid-31038197")
            rv = I.merge_into_review(skeleton("rv-pmid-31038197"), res)
            errs = self.sl.validate(rv, self.schema, self.reg)
            self.assertEqual(errs, [], "%s: %s" % (name, errs[:3]))

    def test_every_claim_has_existing_evidence_and_verbatim_quote(self):
        for name in self.names():
            d = doc(name)
            hay = d.all_text_normalized()
            res = result(name)
            evs = ev_map(res)
            self.assertEqual(len(evs), len(res["evidence"]), name)  # egyedi azonosítók
            for e in res["evidence"]:
                self.assertTrue(0 < len(e["quote"]) <= 300, name)
                for piece in e["quote"].split(" | "):
                    self.assertIn(J.normalize_ws(piece), hay, "%s: %r" % (name, piece))
            for c in res["candidates"]:
                self.assertTrue(c["evidence_ids"], name)
                for x in c["evidence_ids"]:
                    self.assertIn(x, evs, name)
                for s in c["secondary_data"]:
                    self.assertIn(s["evidence_id"], c["evidence_ids"], name)
                    self.assertEqual(s["status"], "unverified", name)
                self.assertLessEqual(len(c["cited_as"]["text"]), 300, name)
                if c["confidence"] != "high" or c["status"] != "confirmed":
                    self.assertTrue(c["needs_review"] or c["role_in_review"] in ("awaiting", "ongoing")
                                    or (c["confidence"] == "high"), "%s %s" % (name, c["cand_id"]))
            for k in ("k_reported", "search_date"):
                v = res.get(k)
                if v and v.get("evidence_id"):
                    self.assertIn(v["evidence_id"], evs, name)

    def test_identifiers_only_from_the_document(self):
        for name in self.names():
            for c in result(name)["candidates"]:
                for k, v in c["ids"].items():
                    vals = v if k == "registry" else [v]
                    for iv in vals:
                        self.assertEqual(iv["source"], "review", name)
                        self.assertTrue(iv["via"].startswith("jats."), name)

    def test_no_long_text_in_output(self):
        for name in self.names():
            blob = json.dumps(result(name), ensure_ascii=False)
            for s in re.findall(r'"((?:[^"\\]|\\.)*)"', blob):
                self.assertLessEqual(len(s), 2000, name)

    def test_deterministic_and_idempotent(self):
        for name in ("cochrane_nested_reflist", "bmj_table_author_year", "forest_data_table"):
            with open(os.path.join(FIX, name + ".xml"), "rb") as fh:
                raw = fh.read()
            a = I.extract_included(J.parse(raw), "rv-pmid-7", at=AT)
            b = I.extract_included(J.parse(raw), "rv-pmid-7", at=AT)
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
            rv1 = I.merge_into_review(skeleton("rv-pmid-7"), a)
            rv2 = I.merge_into_review(rv1, b)
            self.assertEqual(rv1, rv2)
            self.assertEqual([c["cand_id"] for c in rv1["candidates"]],
                             ["c%04d" % i for i in range(1, len(rv1["candidates"]) + 1)])

    def test_merge_preserves_human_state(self):
        res = result("bmj_table_author_year", "rv-pmid-99990200")
        rv = I.merge_into_review(skeleton("rv-pmid-99990200"), res)
        rv2 = copy.deepcopy(rv)
        c = rv2["candidates"][0]
        c["status"] = "rejected"
        c["decision_ids"] = ["d-20261005T102000Z-0001"]
        c["rec_id"] = "rec-pmid-99990011"
        c["ids"]["pmid"]["confirmed_by"] = "pubmed.esummary"
        rv3 = I.merge_into_review(rv2, res)
        self.assertEqual(rv3["candidates"][0], c)

    def test_fixture_files_hold_no_full_text(self):
        import xml.etree.ElementTree as ET
        for name in self.names():
            with open(os.path.join(FIX, name + ".xml"), encoding="utf-8") as fh:
                t = fh.read()
            root = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", t))
            for p in root.iter():
                if p.tag.split("}")[-1] != "p":
                    continue
                txt = J.normalize_ws("".join(p.itertext()))
                for a, b in I._sentences(txt):
                    self.assertLessEqual(b - a, 400, "%s: %s" % (name, txt[a:a + 80]))
            self.assertNotIn("<!ENTITY", t)


class TestTrimForFixture(unittest.TestCase):

    def test_trim_keeps_extraction_result(self):
        for name in ("cochrane_nested_reflist", "springer_table_multirow", "forest_data_table", "statement_only"):
            with open(os.path.join(FIX, name + ".xml"), "rb") as fh:
                raw = fh.read()
            trimmed = J.trim_for_fixture(raw, keep_patterns=[I.fixture_sentence_filter], source_note="test -- note")
            self.assertIn("<!-- test - note -->", trimmed)
            a = I.extract_included(J.parse(raw), "rv-pmid-1", at=AT)
            b = I.extract_included(J.parse(trimmed), "rv-pmid-1", at=AT)
            sig = lambda r: [(c.get("ref_id"), c["role_in_review"], c["confidence"], len(c["secondary_data"]))
                             for c in r["candidates"]]
            self.assertEqual(sig(a), sig(b), name)
            self.assertEqual((a["k_reported"] or {}).get("value"), (b["k_reported"] or {}).get("value"), name)

    def test_trim_drops_other_text(self):
        xml = (b'<article><front><article-meta><abstract><p>Background text that is long. We included 3 trials.'
               b'</p></abstract></article-meta></front><body><sec><title>Discussion</title><p>Some discussion '
               b'sentence that must go.</p></sec><table-wrap id="t1"><caption><title>Summary</title></caption>'
               b'<table><tbody><tr><td>Outcome A</td><td>long narrative cell text</td></tr><tr><td>Outcome B</td>'
               b'<td>x</td></tr></tbody></table></table-wrap></body></article>')
        t = J.trim_for_fixture(xml)
        self.assertNotIn("Background text", t)
        self.assertIn("We included 3 trials.", t)
        self.assertNotIn("must go", t)
        self.assertNotIn("long narrative", t)
        self.assertNotIn("Outcome B", t)  # vizsgálat-sor nélküli tábla: csak az első sor marad


# ---------------------------------------------------------------------------
# valós (vágott) áttekintések — a terv élő füstpróbájának offline megfelelője (19.3)
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.path.isdir(REAL), "nincsenek valós fixture-ök")
class TestRealReviews(unittest.TestCase):

    def test_kashangura_2019_cochrane_six_studies(self):
        r = result("real/PMC6488980", "rv-pmid-31038197")
        inc = [c for c in r["candidates"] if c["role_in_review"] in ("included", "included_companion")]
        self.assertEqual(sorted(c["study_label_in_review"] for c in inc),
                         ["Andrews 2017", "Bunyasi 2017", "Ndiaye 2015", "Nemes 2018", "Scriba 2011", "Tameris 2013"])
        self.assertTrue(all((c["confidence"], c["status"]) == ("high", "confirmed") for c in inc))
        tam = by_label(r, "Tameris 2013")[0]
        self.assertEqual(tam["ids"]["pmid"]["value"], "23391465")
        self.assertEqual(tam["ids"]["pmid"]["source"], "review")  # L4-ben API-val megerősítendő
        self.assertEqual(tam["group_key"], "CD012915-bbs2-0006")
        self.assertEqual(r["k_reported"]["value"], 6)
        self.assertEqual(r["search_date"]["value"], "2018-05-10")
        self.assertGreater(len(r["excluded_by_review"]), 0)

    def test_roy_2014_adetifa_first_author_trap(self):
        r = result("real/PMC4122754", "rv-pmid-25097193")
        d = doc("real/PMC4122754")
        ad = by_label(r, "Adetifa 2010")[0]
        self.assertEqual(ad["ref_id"], "ref26")
        self.assertNotEqual(d.refs["ref24"].first_author, "Adetifa")
        self.assertEqual(len(r["candidates"]), 14)
        self.assertEqual(r["search_date"]["value"], "2013-11")

    def test_gholami_2025_multirow(self):
        r = result("real/PMC12070792", "rv-pmid-40355968")
        evs = ev_map(r)
        self.assertEqual(by_label(r, "Acharjee 2015")[0]["ref_id"], "CR24")
        anderson = by_label(r, "Anderson 2007")
        self.assertEqual(len(anderson), 1)
        self.assertEqual(evs[anderson[0]["evidence_ids"][0]]["locator"]["rows"], [2, 3])
        self.assertEqual(by_label(r, "Azadbakht 2007")[0]["ref_id"], "CR39")
        self.assertEqual(by_label(r, "Azadbakht 2008")[0]["ref_id"], "CR25")
        self.assertEqual(r["k_reported"]["value"], 27)

    def test_ebert_2015_two_line_labels(self):
        r = result("real/PMC4364968", "rv-pmid-25786025")
        f = by_label(r, "Fleming 2012")[0]
        self.assertEqual((f["ref_id"], f["confidence"]), ("pone.0119895.ref047", "high"))
        self.assertEqual(ev_map(r)[f["evidence_ids"][0]]["locator"]["rows"], [1, 2])
        self.assertEqual(r["search_date"]["value"], "2013-12-04")

    def test_rubinstein_2019_statement_supplements_table(self):
        r = result("real/PMC6396088", "rv-pmid-30867144")
        reasons = [c["review_reasons"] for c in r["candidates"]]
        self.assertEqual(sum(1 for x in reasons if "statement_only" in x), 25)
        self.assertEqual(r["k_reported"]["value"], 47)

    def test_katsanos_2018_acronyms_and_superscript_trap(self):
        r = result("real/PMC6405619", "rv-pmid-30561254")
        self.assertEqual(r["k_reported"]["value"], 28)  # nem a 'TSA included 13 RCTs'
        self.assertEqual(r["n_study_groups"], 28)
        self.assertIsNone(by_ref(r, "jah33717-bib-0002"))  # 'mm²' nem hivatkozás
        zilver = [c for c in r["candidates"] if c["group_key"] == "jah33717-tbl-0001:r1"]
        self.assertEqual(len(zilver), 3)

    def test_machado_2015_secondary_values(self):
        r = result("real/PMC4381278", "rv-pmid-25828856")
        flds = set(s["field"] for c in r["candidates"] for s in c["secondary_data"])
        self.assertEqual(flds, {"n1", "n2", "effect", "ci_lo", "ci_hi", "measure"})  # nincs m/sd: 'SD or SE'
        w = by_label(r, "Wetzel 2014")[0]
        self.assertEqual(fields(w, "tbl2", r), {"n1": 36, "n2": 36, "effect": 0.0, "ci_lo": -9.7, "ci_hi": 9.7,
                                                "measure": "MD"})

    def test_cortese_2015_footnote_letters(self):
        r = result("real/PMC4382075", "rv-pmid-25721181")
        labels = [c["study_label_in_review"] for c in r["candidates"]]
        self.assertIn("Egeland 2013", labels)
        self.assertIn("Steiner 2014", labels)
        self.assertEqual(r["k_reported"]["value"], 15)
        self.assertIn("k_mismatch", [w["code"] for w in r["warnings"]])

    def test_regression_against_recorded_summary(self):
        exp = load_expected()
        self.assertEqual(sorted(exp), sorted(n.split("/")[1] for n in real_names()))
        for pmcid, meta in exp.items():
            self.assertTrue(meta["license"].startswith("cc "), pmcid)
            got = I.summarize_result(result("real/" + pmcid, meta["review_id"]))
            self.assertEqual(got, meta["expected"], pmcid)


@unittest.skipUnless(LIVE, "élő teszt: MA_LIVE_TESTS=1")
class TestLiveEuropePMC(unittest.TestCase):
    """Élő próba: a rögzített valós fixture-ök forrását újra letölti (Europe PMC fullTextXML), a teljes szövegen
    futtatja a kinyerést, és összeveti a rögzített összefoglalóval. A teljes szöveg csak memóriában él."""

    def test_live_reviews_match_recorded_summary(self):
        import urllib.request
        mail = (os.environ.get("MA_CONTACT_EMAIL") or "").strip()
        ua = "metaelemzes-headhunter/1.0.0 (python-urllib%s)" % ("; mailto:" + mail if mail else "")
        exp = load_expected()
        for i, (pmcid, meta) in enumerate(sorted(exp.items())):
            if i:
                time.sleep(1.0)
            req = urllib.request.Request("https://www.ebi.ac.uk/europepmc/webservices/rest/%s/fullTextXML" % pmcid,
                                         headers={"User-Agent": ua, "Accept": "application/xml"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()  # csak memóriában (N4)
            got = I.summarize_result(I.extract_included(J.parse(raw), meta["review_id"], at=AT))
            self.assertEqual(got, meta["expected"], pmcid)

    def test_live_via_europepmc_client(self):
        try:
            from metaelemzes.headhunter import europepmc, net
        except ImportError:  # pragma: no cover
            self.skipTest("a forráskliens még nincs meg")
        client = europepmc.Client(net.HttpClient(cache_dir=None, use_env_cassette=False))
        xml = client.fulltext_xml("PMC6488980")
        self.assertIsNotNone(xml)
        r = I.extract_included(J.parse(xml), "rv-pmid-31038197", at=AT)
        inc = sorted(c["study_label_in_review"] for c in r["candidates"] if c["role_in_review"] == "included")
        self.assertEqual(inc, ["Andrews 2017", "Bunyasi 2017", "Ndiaye 2015", "Nemes 2018", "Scriba 2011",
                               "Tameris 2013"])


if __name__ == "__main__":
    unittest.main()
