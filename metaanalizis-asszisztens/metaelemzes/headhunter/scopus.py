# -*- coding: utf-8 -*-
"""Scopus (Elsevier APIs) kliens — Scopus Search (cursor-lapozás, mezőszűkítés), Abstract Retrieval
(``view=REF`` irodalomjegyzék, ``view=META`` metaadat), DOI/EID/PMID kereszt-azonosítók.

Kezdőknek: a Scopus előfizetéses adatbázis. Csak akkor használjuk, ha beállítottad a kulcsot:

* ``MA_SCOPUS_APIKEY`` — kulcs a dev.elsevier.com oldalról (intézményi hálózatról igényelve; Magyarországon
  az intézményi Scopus-hozzáférés jellemzően az EISZ-en át érhető el),
* ``MA_SCOPUS_INSTTOKEN`` — opcionális intézményi token (hálózaton kívüli, pl. otthoni használathoz; az
  intézményi könyvtár kéri az Elseviertől).

A kulcsot és a tokent KIZÁRÓLAG fejlécben küldjük (``X-ELS-APIKey``, ``X-ELS-Insttoken``) — soha nem URL-ben,
naplóban, állapotfájlban, hibaüzenetben vagy kazettában. Kulcs nélkül a kliens nem küld kérést: minden hívás
``SourceUnavailable("scopus", "not_configured")``.

Felhasználási feltételek: csak a szűréshez szükséges bibliográfiai metaadatot adjuk vissza (cím, szerzők,
forrás, év, kötet/oldal, DOI, PMID, EID, dokumentumtípus, idézettség) — absztraktot (``dc:description``) nem
kérünk és nem adunk vissza.

FIGYELEM — élőben nem igazolt innen (TERV 23/1): a kliens az Elsevier hivatalos API-dokumentációja
(Scopus Search API, Abstract Retrieval API, keresési szintaxis) alapján készült; a sandboxból csak a 401-es
válasz volt megfigyelhető. Igazolás a saját gépeden: ``python -m metaelemzes.headhunter sources --check``
(tartalék: ``python -m metaelemzes.headhunter.sources --check``), majd
``MA_LIVE_TESTS=1 python3 -m unittest tests.test_headhunter_live``.
"""

from __future__ import absolute_import

import re
import urllib.parse

from . import net
from .net import (BaseClient, Paged, SourceUnavailable, HttpError, get_env, get_path, as_list, to_int, norm_pmid,
                  norm_doi, norm_eid, status_explain)

SOURCE = "scopus"
PLATFORM = "Scopus (Elsevier Scopus Search API)"
BASE = "https://api.elsevier.com/content/"
SEARCH_URL = BASE + "search/scopus"
PROBE_PMID = "8309034"
UNVERIFIED_LIVE = True

#: mezőszűkítés (``field=``): csak bibliográfiai tények — az absztrakt (``dc:description``) NINCS benne
DEFAULT_FIELDS = ("dc:identifier,eid,dc:title,dc:creator,prism:publicationName,prism:issn,prism:eIssn,"
                  "prism:volume,prism:issueIdentifier,prism:pageRange,prism:coverDate,prism:doi,pubmed-id,"
                  "citedby-count,subtype,subtypeDescription,openaccess")

#: az Elsevier két hibaalakja (mindkettőt kezelni kell, TERV 3.1)
#:   {"service-error": {"status": {"statusCode": "AUTHENTICATION_ERROR", "statusText": "Invalid API Key"}}}
#:   {"error-response": {"error-code": "APIKEY_INVALID", "error-message": "Invalid API Key"}}


def error_code(body_text):
    """Az Elsevier-hibakód kiolvasása a válaszból (``AUTHENTICATION_ERROR``, ``APIKEY_INVALID``,
    ``AUTHORIZATION_ERROR``, ``QUOTA_EXCEEDED``, ``TOO_MANY_REQUESTS``, ``INVALID_INPUT`` …)."""
    if not body_text:
        return None
    try:
        data = net.parse_json(body_text)
    except Exception:
        m = re.search(r'"(?:statusCode|error-code)"\s*:\s*"([A-Z_]+)"', body_text)
        return m.group(1) if m else None
    code = get_path(data, "service-error", "status", "statusCode") or get_path(data, "error-response", "error-code")
    return code


def entry_record(e):
    """Egy Scopus Search találat normalizált alakja (bibliográfiai tények; absztrakt nincs)."""
    eid = norm_eid(e.get("eid")) or norm_eid((e.get("dc:identifier") or "").replace("SCOPUS_ID:", ""))
    cover = e.get("prism:coverDate") or None
    first = e.get("dc:creator") or None
    return {
        "eid": eid,
        "scopus_id": eid[7:] if eid else None,
        "doi": norm_doi(e.get("prism:doi")),
        "pmid": norm_pmid(e.get("pubmed-id")),
        "title": (e.get("dc:title") or "").strip() or None,
        "authors": [first] if first else [],
        "first_author": first,
        "year": to_int((cover or "")[:4]),
        "cover_date": cover,
        "journal": e.get("prism:publicationName"),
        "issn": e.get("prism:issn"),
        "eissn": e.get("prism:eIssn"),
        "volume": e.get("prism:volume"),
        "issue": e.get("prism:issueIdentifier"),
        "pages": e.get("prism:pageRange"),
        "doctype": e.get("subtype"),
        "doctype_desc": e.get("subtypeDescription"),
        "cited_by": to_int(e.get("citedby-count")),
        "openaccess": str(e.get("openaccess")) == "1" or e.get("openaccessFlag") is True,
        "unverified_live": UNVERIFIED_LIVE,
    }


def reference_record(ref):
    """Az Abstract Retrieval ``view=REF`` egy hivatkozása normalizálva. A REF nézet és a FULL nézet
    (``ref-info``) mezőneveit is kezeli (a dokumentáció szerint; élőben igazolandó)."""
    info = ref.get("ref-info") or ref
    authors = []
    for a in as_list(get_path(info, "author-list", "author")) + as_list(get_path(info, "ref-authors", "author")):
        sur = a.get("ce:surname") or a.get("ce:indexed-name")
        ini = a.get("ce:initials") or ""
        if sur:
            authors.append(("%s %s" % (sur, ini)).strip())
    scopus_id = info.get("scopus-id")
    eid = norm_eid(info.get("scopus-eid")) or norm_eid(scopus_id)
    doi = info.get("ce:doi")
    if not doi:
        for it in as_list(get_path(info, "refd-itemidlist", "itemid")):
            if isinstance(it, dict) and it.get("@idtype") == "DOI":
                doi = it.get("$")
    title = info.get("title") or get_path(info, "ref-title", "ref-titletext")
    year = to_int((info.get("prism:coverDate") or "")[:4]) or to_int(get_path(info, "ref-publicationyear", "@first"))
    vol = get_path(info, "volisspag", "voliss", "@volume") or info.get("prism:volume")
    first_page = get_path(info, "volisspag", "pagerange", "@first")
    last_page = get_path(info, "volisspag", "pagerange", "@last")
    pages = info.get("prism:pageRange") or (("%s-%s" % (first_page, last_page)) if first_page and last_page
                                           else first_page)
    return {
        "position": to_int(ref.get("@id")),
        "eid": eid,
        "scopus_id": eid[7:] if eid else None,
        "doi": norm_doi(doi),
        "title": (title or "").strip() or None,
        "journal": info.get("sourcetitle") or info.get("ref-sourcetitle"),
        "year": year,
        "volume": vol,
        "pages": pages,
        "authors": authors[:50],
        "first_author": authors[0] if authors else None,
        "cited_by": to_int(info.get("citedby-count")),
        "text": (ref.get("ref-fulltext") or info.get("ref-text") or None),
        "unverified_live": UNVERIFIED_LIVE,
    }


def quote_term(term):
    """Kifejezés idézőjelbe tétele a Scopus-szintaxishoz (a belső idézőjel eldobva)."""
    t = str(term).replace('"', " ").strip()
    return '"%s"' % t if re.search(r"\s|-", t) else t


class Client(BaseClient):
    """Scopus kliens (a 20.5 szerződés szerinti ``search`` és ``references`` metódusokkal)."""

    SOURCE = SOURCE
    PLATFORM = PLATFORM

    def key_configured(self):
        return get_env(net.ENV_SCOPUS_APIKEY, self.env) is not None

    def insttoken_configured(self):
        return get_env(net.ENV_SCOPUS_INSTTOKEN, self.env) is not None

    @property
    def configured(self):
        return self.key_configured()

    def _require_key(self):
        if not self.key_configured():
            raise SourceUnavailable(SOURCE, "not_configured")

    def _headers(self):
        h = {"X-ELS-APIKey": get_env(net.ENV_SCOPUS_APIKEY, self.env), "Accept": "application/json"}
        tok = get_env(net.ENV_SCOPUS_INSTTOKEN, self.env)
        if tok:
            h["X-ELS-Insttoken"] = tok
        return h

    def _refine(self, exc):
        """Elsevier-specifikus, kezdőbarát magyarázat a 401/403/429 hibákhoz."""
        code = error_code(exc.body_excerpt) or ""
        status = exc.status
        detail = None
        if status == "unauthorized":
            detail = {"hu": "Elsevier-hibakód: %s. A kulcs érvénytelen vagy nem ehhez az API-hoz szól "
                            "(dev.elsevier.com → My API Key)." % (code or "401"),
                      "en": "Elsevier error code: %s. The key is invalid or not enabled for this API "
                            "(dev.elsevier.com → My API Key)." % (code or "401")}
        elif status == "forbidden":
            detail = {"hu": "Elsevier-hibakód: %s." % (code or "403"), "en": "Elsevier error code: %s." % (code or "403")}
        elif status == "rate_limited":
            if "QUOTA" in code.upper():
                detail = {"hu": "A kulcs heti kvótája elfogyott (QUOTA_EXCEEDED).",
                          "en": "The key's weekly quota is exhausted (QUOTA_EXCEEDED)."}
            else:
                detail = {"hu": "Túl sok kérés rövid idő alatt (%s)." % (code or "429"),
                          "en": "Too many requests in a short time (%s)." % (code or "429")}
        if detail is None:
            return exc
        new = SourceUnavailable(SOURCE, status, reset_at=exc.reset_at, http_status=exc.http_status,
                                endpoint=exc.endpoint, body_excerpt=exc.body_excerpt, headers=exc.headers,
                                explain=status_explain(SOURCE, status, exc.reset_at, detail))
        return new

    def _get(self, url, params, bucket=None, allow_status=()):
        self._require_key()
        try:
            return self.http.get(SOURCE, url, params=params, headers=self._headers(), accept="json",
                                 bucket=bucket, allow_status=allow_status)
        except SourceUnavailable as exc:
            raise self._refine(exc)

    # -- keresés -----------------------------------------------------------------------------

    def search(self, query, fields=DEFAULT_FIELDS, count=25, view="STANDARD", max_results=None, sort=None,
               date=None, use_cursor=True, normalize=False):
        """Scopus Search (``TITLE-ABS-KEY(…) AND DOCTYPE(re) AND PUBYEAR > 2019`` …) ``Paged`` iterátorként.
        Cursor-lapozás (``cursor=*`` → ``cursor.@next``), mezőszűkítés (``field=``). Iterálás után ``.total``
        (``opensearch:totalResults``), ``.retrieved``, ``.complete``."""
        count = max(1, min(int(count), 200))

        def fetch(state):
            params = [("query", query), ("count", count), ("view", view)]
            if fields:
                params.append(("field", fields))
            if sort:
                params.append(("sort", sort))
            if date:
                params.append(("date", date))
            if use_cursor:
                params.append(("cursor", "*" if state is None else state))
            else:
                params.append(("start", 0 if state is None else state))
            resp = self._get(SEARCH_URL, params)
            if resp.status != 200:
                return [], 0, None
            sr = resp.json().get("search-results") or {}
            total = to_int(sr.get("opensearch:totalResults"), 0)
            entries = [e for e in as_list(sr.get("entry")) if isinstance(e, dict) and not e.get("error")]
            if use_cursor:
                nxt = get_path(sr, "cursor", "@next")
                cur = "*" if state is None else state
                if not entries or not nxt or nxt == cur:
                    nxt = None
            else:
                start = (0 if state is None else state) + len(entries)
                nxt = start if entries and start < total and start < 5000 else None
            if normalize:
                entries = [entry_record(e) for e in entries]
            return entries, total, nxt

        return Paged(SOURCE, fetch, max_results=max_results, query=query)

    def search_reviews(self, p_terms, i_terms, since_year=None, max_results=200, normalize=True):
        """Áttekintés-keresés a TERV 5.1 szerint: ``TITLE-ABS-KEY(<P>) AND TITLE-ABS-KEY(<I>) AND
        (TITLE-ABS-KEY("meta-analysis") OR TITLE-ABS-KEY("systematic review")) AND DOCTYPE(re)``."""
        q = build_review_query(p_terms, i_terms, since_year)
        return self.search(q, max_results=max_results, normalize=normalize)

    # -- Abstract Retrieval ------------------------------------------------------------------

    def abstract(self, identifier, id_type=None, view="META"):
        """Egy dokumentum metaadata (``view=META``: absztrakt nélkül). ``id_type``: ``eid`` | ``doi`` |
        ``pubmed_id`` | ``scopus_id`` (ha ``None``, az azonosító alakjából). ``None``, ha nincs."""
        id_type, ident = _id_path(identifier, id_type)
        if not ident:
            return None
        url = BASE + "abstract/%s/%s" % (id_type, urllib.parse.quote(ident, safe="/:;()-._"))
        resp = self._get(url, [("view", view)], bucket="scopus:abstract")
        if resp.status != 200:
            return None
        data = resp.json().get("abstracts-retrieval-response") or {}
        core = data.get("coredata") or {}
        return {
            "eid": norm_eid(core.get("eid")),
            "doi": norm_doi(core.get("prism:doi")),
            "pmid": norm_pmid(core.get("pubmed-id")),
            "title": core.get("dc:title"),
            "journal": core.get("prism:publicationName"),
            "cover_date": core.get("prism:coverDate"),
            "year": to_int((core.get("prism:coverDate") or "")[:4]),
            "doctype": core.get("subtype"),
            "cited_by": to_int(core.get("citedby-count")),
            "unverified_live": UNVERIFIED_LIVE,
        }

    def references(self, eid, startref=1, refcount=40, max_results=None, normalize=False):
        """Irodalomjegyzék (Abstract Retrieval ``view=REF``) ``Paged`` iterátorként; lapozás ``startref``/
        ``refcount`` (≤ 40). Jogosultság kell (intézményi előfizetés) — ennek hiánya 401/403 →
        ``SourceUnavailable`` (``forbidden``). Ezek JELÖLT-hivatkozások (TERV 6.2)."""
        e = norm_eid(eid)
        if not e:
            return Paged(SOURCE, lambda state: ([], 0, None), query="REF ?")
        refcount = max(1, min(int(refcount), 40))
        url = BASE + "abstract/eid/%s" % e

        def fetch(state):
            start = int(startref) if state is None else state
            resp = self._get(url, [("view", "REF"), ("startref", start), ("refcount", refcount)],
                             bucket="scopus:abstract")
            if resp.status != 200:
                return [], 0, None
            refs = get_path(resp.json(), "abstracts-retrieval-response", "references") or {}
            total = to_int(refs.get("@total-references"))
            items = [r for r in as_list(refs.get("reference")) if isinstance(r, dict)]
            nxt = start + len(items)
            if not items or (total is not None and nxt > total):
                nxt = None
            if normalize:
                items = [reference_record(r) for r in items]
            return items, total, nxt

        return Paged(SOURCE, fetch, max_results=max_results, query="REF %s" % e)

    def cross_ids(self, doi=None, pmid=None, eid=None):
        """DOI/PMID/EID kereszt-azonosítók a Search API-val (mezőszűkítve). ``None``, ha nincs egyértelmű
        találat. Visszaad: ``{"eid", "doi", "pmid", "scopus_id"}``."""
        if eid and norm_eid(eid):
            q = "EID(%s)" % norm_eid(eid)
        elif doi and norm_doi(doi):
            q = "DOI(%s)" % norm_doi(doi)
        elif pmid and norm_pmid(pmid):
            q = "PMID(%s)" % norm_pmid(pmid)
        else:
            return None
        hits = self.search(q, fields="dc:identifier,eid,prism:doi,pubmed-id", count=2, max_results=2,
                           normalize=True).all()
        if len(hits) != 1:
            return None
        h = hits[0]
        return {"eid": h["eid"], "doi": h["doi"], "pmid": h["pmid"], "scopus_id": h["scopus_id"]}

    def citing_query(self, eid):
        """Előre irányú hivatkozáskövetés lekérdezése: ``REFEID(2-s2.0-…)``."""
        e = norm_eid(eid)
        return "REFEID(%s)" % e if e else None

    # -- állapot -----------------------------------------------------------------------------

    def quota(self, headers):
        h = headers or {}
        reset = h.get("x-ratelimit-reset")
        reset_at = None
        try:
            r = float(reset)
            reset_at = net.utc_ts(r) if r > 1e9 else net.utc_ts(self.http.now() + r)
        except (TypeError, ValueError):
            pass
        return {"limit": to_int(h.get("x-ratelimit-limit")), "remaining": to_int(h.get("x-ratelimit-remaining")),
                "reset_at": reset_at}

    def _result(self, status, **kw):
        res = BaseClient._result(self, status, **kw)
        res["insttoken_configured"] = self.insttoken_configured()
        res["unverified_live"] = UNVERIFIED_LIVE
        return res

    def check(self):
        """Próba: ``search?query=PMID(8309034)&count=1&field=dc:identifier,eid``; ha van EID:
        ``abstract/eid/{eid}?view=REF&refcount=1`` → ``entitlement`` = ``search_and_ref`` | ``search_only``.
        Kulcs nélkül nincs hálózati kérés: ``not_configured``."""
        if not self.key_configured():
            return self._result("not_configured", entitlement="none")
        try:
            resp = self._get(SEARCH_URL, [("query", "PMID(%s)" % PROBE_PMID), ("count", 1),
                                          ("field", "dc:identifier,eid")])
        except SourceUnavailable as exc:
            ent = "none" if exc.status in ("unauthorized", "forbidden") else "unknown"
            return self._result(exc.status, message=exc.explain, reset_at=exc.reset_at, http_status=exc.http_status,
                                entitlement=ent)
        except HttpError as exc:
            return self._result("unreachable", http_status=exc.status, entitlement="unknown",
                                detail_text={"hu": "Váratlan válasz (HTTP %s)." % exc.status,
                                             "en": "Unexpected response (HTTP %s)." % exc.status})
        details = {"quota": self.quota(resp.headers)}
        entries = [e for e in as_list(get_path(resp.json(), "search-results", "entry")) if not e.get("error")]
        eid = norm_eid(entries[0].get("eid")) if entries else None
        if not eid:
            return self._result("ok", http_status=200, entitlement="search_only", details=details, detail_text={
                "hu": "A keresés működik; a hivatkozáslista-jogosultságot nem tudtuk ellenőrizni.",
                "en": "Search works; reference-list entitlement could not be checked."})
        try:
            ref = self._get(BASE + "abstract/eid/%s" % eid, [("view", "REF"), ("refcount", 1)],
                            bucket="scopus:abstract")
            entitlement = "search_and_ref" if ref.status == 200 else "search_only"
        except SourceUnavailable as exc:
            if exc.status == "rate_limited":
                entitlement = "unknown"
            else:
                entitlement = "search_only"
        except HttpError:
            entitlement = "search_only"
        text = None
        if entitlement == "search_only":
            text = {"hu": ("A keresés működik, de az irodalomjegyzék (view=REF) nem érhető el ezzel a kulccsal — "
                           "intézményi hálózat vagy MA_SCOPUS_INSTTOKEN kell hozzá."),
                    "en": ("Search works, but reference lists (view=REF) are not available with this key — an "
                           "institutional network or MA_SCOPUS_INSTTOKEN is needed.")}
        return self._result("ok", http_status=200, entitlement=entitlement, details=details, detail_text=text)


def _id_path(identifier, id_type=None):
    s = str(identifier or "").strip()
    if id_type in ("eid", None) and norm_eid(s) and (id_type == "eid" or s.lower().startswith("2-s2.0-")):
        return "eid", norm_eid(s)
    if id_type in ("doi", None) and norm_doi(s):
        return "doi", norm_doi(s)
    if id_type in ("pubmed_id", "pmid", None) and norm_pmid(s):
        return "pubmed_id", norm_pmid(s)
    if id_type == "scopus_id" and re.match(r"^\d+$", s):
        return "scopus_id", s
    return id_type or "eid", None


def _block(terms):
    terms = [t for t in as_list(terms) if t and str(t).strip()]
    if not terms:
        return None
    return "TITLE-ABS-KEY(%s)" % " OR ".join(quote_term(t) for t in terms)


def build_review_query(p_terms, i_terms, since_year=None):
    """A TERV 5.1 Scopus-szűrője fogalomblokkokból."""
    parts = [b for b in (_block(p_terms), _block(i_terms)) if b]
    parts.append('(TITLE-ABS-KEY("meta-analysis") OR TITLE-ABS-KEY("systematic review"))')
    parts.append("DOCTYPE(re)")
    q = " AND ".join(parts)
    if since_year:
        q += " AND PUBYEAR > %d" % (int(since_year) - 1)
    return q


def build_update_query(blocks_query, start_date):
    """Frissítő keresés (12.2): ``<blokkok> AND PUBYEAR > <kezdet éve − 1>`` és — élőben igazolandó —
    ``ORIG-LOAD-DATE AFT <ééééhhnn>``. Visszaad: ``(lekérdezés, unverified_live)``."""
    y = int(str(start_date)[:4])
    ymd = re.sub(r"\D", "", str(start_date))
    ymd = (ymd + "0101")[:8] if len(ymd) < 8 else ymd[:8]
    return "(%s) AND PUBYEAR > %d AND ORIG-LOAD-DATE AFT %s" % (blocks_query, y - 1, ymd), True
