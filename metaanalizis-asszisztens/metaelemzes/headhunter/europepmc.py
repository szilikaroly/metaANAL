# -*- coding: utf-8 -*-
"""Europe PMC REST kliens — keresés (cursorMark-lapozás), nyílt teljes szöveg (JATS), hivatkozások,
idéző közlemények, annotációk (regiszterszámok).

Kezdőknek: az Europe PMC a PubMed-anyagon túl preprinteket és nyílt hozzáférésű teljes szövegeket is
kereshetővé tesz; kulcs nem kell.

Megfigyelt viselkedés (TERV 3.1), amelyre a kód épül:

* a ``PMID:<szám>`` mező NEM megbízható (más cikket adott) → PMID-re mindig ``EXT_ID:<pmid> AND SRC:MED``,
  DOI-ra ``DOI:"<doi>"``;
* a ``fullTextXML`` csak nyílt hozzáférésű cikkre ad 200-at, nem nyíltra HTTP 500-at (nem 404-et!) —
  ez „nincs nyílt teljes szöveg", nem hálózati hiba (``fulltext_xml`` → ``None``, újrapróbálás nélkül);
* a ``PUB_TYPE:"systematic-review"``, ``PUB_TYPE:"meta-analysis"``, ``FIRST_PDATE:[a TO b]``,
  ``CREATION_DATE:[a TO b]`` szűrők működnek.

A teljes szöveg CSAK memóriában dolgozható fel (N4): a projekt-gyorsítótárba nem kerül.
"""

from __future__ import absolute_import

import re

from .net import (BaseClient, Paged, get_path, as_list, to_int, norm_pmid, norm_pmcid, norm_doi, norm_nct)

SOURCE = "europepmc"
PLATFORM = "Europe PMC REST API"
BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
ANNOTATIONS_URL = "https://www.ebi.ac.uk/europepmc/annotations_api/annotationsByArticleIds"
PROBE_PMID = "8309034"

#: a Europe PMC ``source`` kódjai (MED = PubMed/MEDLINE, PMC, PPR = preprint, AGR, CBA, CTX, ETH, HIR, PAT)
SRC_CODES = ("MED", "PMC", "PPR", "AGR", "CBA", "CTX", "ETH", "HIR", "PAT")


def query_pmid(pmid):
    """PMID-lekérdezés a megbízható alakban (``EXT_ID:<pmid> AND SRC:MED``)."""
    return "EXT_ID:%s AND SRC:MED" % norm_pmid(pmid)


def query_doi(doi):
    return 'DOI:"%s"' % norm_doi(doi)


def query_pmcid(pmcid):
    return "PMCID:%s" % norm_pmcid(pmcid)


def _authors(r):
    lst = get_path(r, "authorList", "author") or []
    names = []
    for a in lst:
        if a.get("lastName"):
            names.append(("%s %s" % (a.get("lastName"), a.get("initials") or "")).strip())
        elif a.get("collectiveName"):
            names.append(a["collectiveName"])
        elif a.get("fullName"):
            names.append(a["fullName"])
    if not names and r.get("authorString"):
        names = [n.strip() for n in re.split(r",\s*", r["authorString"].rstrip(".")) if n.strip()]
    return names


def record(r):
    """Egy Europe PMC keresési találat normalizált alakja.

    Az ``abstract`` (csak ``resultType=core``) CSAK memóriában használható — fájlba nem írható (N4)."""
    authors = _authors(r)
    journal = get_path(r, "journalInfo", "journal", "isoabbreviation") or get_path(r, "journalInfo", "journal", "medlineAbbreviation") \
        or r.get("journalTitle") or get_path(r, "journalInfo", "journal", "title")
    pub_types = as_list(get_path(r, "pubTypeList", "pubType"))
    ft_ids = as_list(get_path(r, "fullTextIdList", "fullTextId"))
    year = to_int(r.get("pubYear")) or to_int(get_path(r, "journalInfo", "yearOfPublication"))
    return {
        "source_db": r.get("source"),
        "id": r.get("id"),
        "pmid": norm_pmid(r.get("pmid")),
        "pmcid": norm_pmcid(r.get("pmcid")),
        "doi": norm_doi(r.get("doi")),
        "title": (r.get("title") or "").strip() or None,
        "authors": authors[:50],
        "authors_truncated": len(authors) > 50,
        "first_author": authors[0] if authors else None,
        "year": year,
        "journal": journal,
        "volume": get_path(r, "journalInfo", "volume") or r.get("journalVolume"),
        "issue": get_path(r, "journalInfo", "issue") or r.get("issue"),
        "pages": r.get("pageInfo"),
        "pub_types": [str(p) for p in pub_types],
        "language": r.get("language"),
        "is_open_access": r.get("isOpenAccess") == "Y",
        "in_epmc": r.get("inEPMC") == "Y",
        "in_pmc": r.get("inPMC") == "Y",
        "has_references": r.get("hasReferences") == "Y",
        "cited_by": to_int(r.get("citedByCount")),
        "license": r.get("license"),
        "first_publication_date": r.get("firstPublicationDate"),
        "full_text_ids": [str(x) for x in ft_ids],
        "abstract": r.get("abstractText") or "",
    }


def reference_record(r):
    """A ``/references`` egy tétele normalizálva (bibliográfiai tények + a forrás azonosítói)."""
    src = r.get("source")
    ext = r.get("id")
    return {
        "source_db": src,
        "id": ext,
        "pmid": norm_pmid(ext) if src == "MED" else None,
        "pmcid": norm_pmcid(ext) if src == "PMC" else None,
        "doi": norm_doi(r.get("doi")),
        "title": (r.get("title") or "").strip() or None,
        "authors": [a.strip() for a in re.split(r",\s*", (r.get("authorString") or "").rstrip(".")) if a.strip()][:50],
        "first_author": ([a.strip() for a in re.split(r",\s*", r.get("authorString") or "") if a.strip()] or [None])[0],
        "year": to_int(r.get("pubYear")),
        "journal": r.get("journalAbbreviation") or r.get("publicationTitle"),
        "volume": r.get("volume"),
        "issue": r.get("issue"),
        "pages": r.get("pageInfo"),
        "cited_order": to_int(r.get("citedOrder")),
        "matched": r.get("match") == "Y",
    }


class Client(BaseClient):
    """Europe PMC REST kliens (a 20.5 szerződés szerinti metódusokkal)."""

    SOURCE = SOURCE
    PLATFORM = PLATFORM

    def _get(self, path, params, accept="json", cache=True, allow_status=(), url=None):
        params = list(params)
        if accept == "json" and not any(k == "format" for k, _v in params):
            params.append(("format", "json"))
        return self.http.get(SOURCE, url or (BASE + path), params=params, accept=accept, cache=cache,
                             allow_status=allow_status)

    # -- keresés -----------------------------------------------------------------------------

    def search(self, query, result_type="core", page_size=100, cursor="*", max_results=None, sort=None,
               synonym=None, normalize=False):
        """Keresés cursorMark-lapozással. Iterátor (``Paged``) nyers találat-dictekkel (``normalize=True``:
        ``record()`` alakban). Iterálás után: ``.total`` (hitCount), ``.retrieved``, ``.complete``."""
        page_size = max(1, min(int(page_size), 1000))

        def fetch(state):
            cur = cursor if state is None else state
            params = [("query", query), ("resultType", result_type), ("pageSize", page_size), ("cursorMark", cur)]
            if sort:
                params.append(("sort", sort))
            if synonym is not None:
                params.append(("synonym", "true" if synonym else "false"))
            data = self._get("search", params).json()
            items = as_list(get_path(data, "resultList", "result"))
            nxt = data.get("nextCursorMark")
            if not items or not nxt or nxt == cur:
                nxt = None
            if normalize:
                items = [record(x) for x in items]
            return items, to_int(data.get("hitCount")), nxt

        return Paged(SOURCE, fetch, max_results=max_results, query=query)

    def search_one(self, query, result_type="core"):
        """Az első találat (vagy ``None``)."""
        for item in self.search(query, result_type=result_type, page_size=1, max_results=1):
            return item
        return None

    def lookup_pmid(self, pmid, result_type="core"):
        """Egy PubMed-rekord Europe PMC-ben (``EXT_ID:<pmid> AND SRC:MED``); ``None``, ha nincs.
        Csak akkor fogad el találatot, ha a visszakapott PMID egyezik (nincs találgatás)."""
        p = norm_pmid(pmid)
        if not p:
            return None
        hit = self.search_one(query_pmid(p), result_type=result_type)
        return hit if hit and norm_pmid(hit.get("pmid")) == p else None

    def lookup_doi(self, doi, result_type="core"):
        d = norm_doi(doi)
        if not d:
            return None
        hit = self.search_one(query_doi(d), result_type=result_type)
        return hit if hit and norm_doi(hit.get("doi")) == d else None

    def lookup_pmcid(self, pmcid, result_type="core"):
        p = norm_pmcid(pmcid)
        if not p:
            return None
        hit = self.search_one(query_pmcid(p), result_type=result_type)
        return hit if hit and norm_pmcid(hit.get("pmcid")) == p else None

    def lookup_many_pmids(self, pmids, result_type="lite", chunk=50):
        """Sok PMID egyszerre (``(EXT_ID:a OR EXT_ID:b …) AND SRC:MED``). Visszaad: ``{pmid: nyers találat}``."""
        ids = [p for p in (norm_pmid(x) for x in as_list(pmids)) if p]
        out = {}
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            q = "(%s) AND SRC:MED" % " OR ".join("EXT_ID:%s" % p for p in part)
            for hit in self.search(q, result_type=result_type, page_size=len(part) + 5, max_results=len(part) * 2):
                p = norm_pmid(hit.get("pmid"))
                if p in part and p not in out:
                    out[p] = hit
        return out

    # -- teljes szöveg -----------------------------------------------------------------------

    def fulltext_xml(self, pmcid):
        """Nyílt hozzáférésű teljes szöveg (JATS XML) — CSAK memóriában (N4). ``None``, ha nincs nyílt
        teljes szöveg (az Europe PMC ilyenkor HTTP 500-at ad — ezt nem próbáljuk újra)."""
        p = norm_pmcid(pmcid)
        if not p:
            return None
        resp = self.http.get(SOURCE, BASE + "%s/fullTextXML" % p, accept="xml", cache="fulltext",
                             allow_status=(400, 404, 500))
        if resp.status != 200:
            return None
        text = resp.text
        return text if "<article" in text else None

    # -- hivatkozások és idézők ------------------------------------------------------------

    def _list_pager(self, kind, src, ext_id, page_size, max_results, normalize):
        src = (src or "MED").upper()
        ext = str(ext_id).strip()
        page_size = max(1, min(int(page_size), 1000))
        list_key, item_key = ("referenceList", "reference") if kind == "references" else ("citationList", "citation")

        def fetch(state):
            page = 1 if state is None else state
            data = self._get("%s/%s/%s" % (src, ext, kind), [("page", page), ("pageSize", page_size)]).json()
            items = as_list(get_path(data, list_key, item_key))
            total = to_int(data.get("hitCount"))
            nxt = page + 1 if items and (total is None or page * page_size < total) else None
            if normalize:
                items = [reference_record(x) for x in items]
            return items, total, nxt

        return Paged(SOURCE, fetch, max_results=max_results, query="%s/%s/%s" % (src, ext, kind))

    def references(self, src, ext_id, page_size=1000, max_results=None, normalize=False):
        """Egy közlemény irodalomjegyzéke (``/{SRC}/{id}/references``). Ezek JELÖLT-hivatkozások, nem
        bevont-vizsgálat állítások (TERV 6.2)."""
        return self._list_pager("references", src, ext_id, page_size, max_results, normalize)

    def citations(self, src, ext_id, page_size=1000, max_results=None, normalize=False):
        """Idéző közlemények (``/{SRC}/{id}/citations``) — előre irányú hivatkozáskövetéshez (12.3)."""
        return self._list_pager("citations", src, ext_id, page_size, max_results, normalize)

    # -- annotációk (regiszterszámok) --------------------------------------------------------

    def annotations(self, src, ext_id, ann_type="Accession Numbers"):
        """Az annotations API nyers annotációi egy cikkre (``[dict]``)."""
        params = [("articleIds", "%s:%s" % ((src or "MED").upper(), str(ext_id).strip())), ("type", ann_type),
                  ("format", "JSON")]
        resp = self.http.get(SOURCE, ANNOTATIONS_URL, params=params, accept="json", allow_status=(400,))
        if resp.status != 200:
            return []
        data = resp.json()
        out = []
        for art in as_list(data):
            for ann in as_list(art.get("annotations") if isinstance(art, dict) else None):
                out.append(ann)
        return out

    def accession_numbers(self, src, ext_id, subtype=None):
        """Regiszter-/adatbázis-azonosítók a cikk szövegéből (pl. ``subtype="NCT"`` → ``["NCT00953927"]``).
        A közlemény saját regisztrációs nyilatkozata *erős* kapcsolat (TERV 7. fejezet)."""
        found = []
        for ann in self.annotations(src, ext_id):
            st = (ann.get("subType") or "").lower()
            if subtype and st != str(subtype).lower():
                continue
            names = [t.get("name") for t in as_list(ann.get("tags")) if t.get("name")] or [ann.get("exact")]
            for n in names:
                if not n:
                    continue
                n = n.strip()
                if st == "nct":
                    n = norm_nct(n) or n
                if n not in found:
                    found.append(n)
        return sorted(found)

    def accession_details(self, src, ext_id):
        """``[{"id", "subtype", "section"}]`` — a lokátorhoz (melyik szakaszban szerepelt)."""
        out = []
        seen = set()
        for ann in self.annotations(src, ext_id):
            st = ann.get("subType")
            for t in as_list(ann.get("tags")) or [{"name": ann.get("exact")}]:
                n = (t.get("name") or "").strip()
                key = (n, st, ann.get("section"))
                if n and key not in seen:
                    seen.add(key)
                    out.append({"id": norm_nct(n) if (st or "").lower() == "nct" else n, "subtype": st,
                                "section": (ann.get("section") or "").split(" (")[0] or None})
        return out

    # -- állapot -----------------------------------------------------------------------------

    def _probe(self):
        pager = self.search(query_pmid(PROBE_PMID), result_type="lite", page_size=1, max_results=1)
        items = pager.all()
        if items and norm_pmid(items[0].get("pmid")) == PROBE_PMID:
            return self._result("ok", http_status=200)
        return self._result("unreachable", detail_text={
            "hu": "A próbakeresés váratlan eredményt adott.", "en": "The probe search returned an unexpected result."})
