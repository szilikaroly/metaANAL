# -*- coding: utf-8 -*-
"""OpenAlex kliens — egyedi munka-lekérés, szűrős/kereső listák (cursor-lapozás), irodalomjegyzék
(``referenced_works``) és idéző közlemények (``cites:``).

Kezdőknek: az OpenAlex egy nyílt, ingyenes tudományos katalógus. Kulcs nem kötelező, de AJÁNLOTT:

* az egyedi lekérés (``/works/pmid:…``, ``/works/doi:…``, ``/works/W…``) ingyenes,
* a listás/szűrős lekérdezés (``/works?filter=…``) kreditbe kerül; kulcs nélkül egy, az IP-címen osztozó
  napi keretből megy, ami gyorsan elfogyhat — ilyenkor HTTP 429 jön ``Retry-After``-rel (akár ~14 óra).
  Ingyenes kulccsal (``MA_OPENALEX_APIKEY``) saját keretet kapsz; a kulcsot ``Authorization: Bearer``
  fejlécben küldjük (soha nem URL-ben).
* az udvarias azonosítás: ``mailto=<MA_CONTACT_EMAIL>`` (naplóból/kazettából kivágva).

A listás lekérdezések külön „vödörben" (``openalex:list``) vannak: ha a listák kerete elfogy, az ingyenes
egyedi lekérések (pl. irodalomjegyzék) tovább működnek.
"""

from __future__ import absolute_import

import re
import urllib.parse

from . import net
from .net import (BaseClient, Paged, get_env, get_path, as_list, to_int, norm_pmid, norm_pmcid,
                  norm_doi, norm_openalex)

SOURCE = "openalex"
PLATFORM = "OpenAlex API"
BASE = "https://api.openalex.org/"
LIST_BUCKET = "openalex:list"
PROBE_PMID = "8309034"

#: alapértelmezett mezők (csak bibliográfiai tények; az absztrakt — ``abstract_inverted_index`` — nincs benne)
DEFAULT_SELECT = ("id,doi,ids,display_name,publication_year,publication_date,type,primary_location,authorships,"
                  "biblio,cited_by_count,is_retracted,open_access,referenced_works_count")


def work_path(identifier):
    """Azonosító → ``works/…`` útvonal. Elfogad: ``W123``, OpenAlex URL, ``pmid:…``, ``pmcid:…``,
    ``doi:…``, puszta DOI (``10.…``), puszta számjegy (PMID). ``None``, ha nem értelmezhető."""
    if identifier is None:
        return None
    s = str(identifier).strip()
    w = norm_openalex(s)
    if w:
        return "works/" + w
    low = s.lower()
    if low.startswith("pmid:"):
        p = norm_pmid(s[5:])
        return "works/pmid:" + p if p else None
    if low.startswith("pmcid:"):
        p = norm_pmcid(s[6:])
        return "works/pmcid:" + p if p else None
    if low.startswith("doi:") or norm_doi(s):
        d = norm_doi(s[4:] if low.startswith("doi:") else s)
        return "works/doi:" + urllib.parse.quote(d, safe="/:;()-._") if d else None
    if re.match(r"^\d{1,9}$", s):
        return "works/pmid:" + s
    return None


def work_record(w):
    """Egy OpenAlex-munka normalizált alakja (bibliográfiai tények; absztrakt nincs)."""
    ids = w.get("ids") or {}
    authors = []
    for a in as_list(w.get("authorships")):
        name = get_path(a, "author", "display_name") or a.get("raw_author_name")
        if name:
            authors.append(name)
    biblio = w.get("biblio") or {}
    pages = None
    if biblio.get("first_page"):
        pages = biblio["first_page"] + ("-" + biblio["last_page"] if biblio.get("last_page") and
                                        biblio.get("last_page") != biblio.get("first_page") else "")
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    return {
        "openalex": norm_openalex(w.get("id")),
        "doi": norm_doi(w.get("doi") or ids.get("doi")),
        "pmid": norm_pmid(ids.get("pmid")),
        "pmcid": norm_pmcid(ids.get("pmcid")),
        "title": (w.get("display_name") or w.get("title") or "").strip() or None,
        "authors": authors[:50],
        "authors_truncated": len(authors) > 50,
        "first_author": authors[0] if authors else None,
        "year": to_int(w.get("publication_year")),
        "date": w.get("publication_date"),
        "type": w.get("type"),
        "journal": src.get("display_name"),
        "issn": src.get("issn_l"),
        "volume": biblio.get("volume"),
        "issue": biblio.get("issue"),
        "pages": pages,
        "cited_by": to_int(w.get("cited_by_count")),
        "is_retracted": bool(w.get("is_retracted")),
        "is_oa": bool(get_path(w, "open_access", "is_oa")),
        "oa_status": get_path(w, "open_access", "oa_status"),
        "referenced_works_count": to_int(w.get("referenced_works_count")),
    }


def filter_string(flt):
    """``{"type": "review", "from_publication_date": "2020-01-01"}`` → ``"type:review,from_publication_date:2020-01-01"``.
    Szöveg változatlanul megy tovább."""
    if flt is None:
        return None
    if isinstance(flt, str):
        return flt
    parts = []
    items = flt.items() if isinstance(flt, dict) else flt
    for k, v in items:
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            v = "|".join(str(x) for x in v)
        elif isinstance(v, bool):
            v = "true" if v else "false"
        parts.append("%s:%s" % (k, v))
    return ",".join(parts)


class Client(BaseClient):
    """OpenAlex kliens (a 20.5 szerződés szerinti ``work`` és ``works`` metódusokkal)."""

    SOURCE = SOURCE
    PLATFORM = PLATFORM

    def key_configured(self):
        return get_env(net.ENV_OPENALEX_APIKEY, self.env) is not None

    def _headers(self):
        key = get_env(net.ENV_OPENALEX_APIKEY, self.env)
        return {"Authorization": "Bearer " + key} if key else None

    def _params(self, params):
        params = [(k, v) for k, v in params if v is not None]
        mail = get_env(net.ENV_CONTACT_EMAIL, self.env)
        if mail:
            params.append(("mailto", mail))
        return params

    # -- egyedi lekérés (ingyenes) -----------------------------------------------------------

    def work_response(self, identifier, select=None):
        path = work_path(identifier)
        if not path:
            return None
        params = self._params([("select", select)] if select else [])
        return self.http.get(SOURCE, BASE + path, params=params, headers=self._headers(), accept="json")

    def work(self, identifier, select=None):
        """Egy munka (``dict``) vagy ``None`` (nincs ilyen / nem értelmezhető azonosító). Nyers OpenAlex-alak;
        a ``work_record()`` normalizál."""
        resp = self.work_response(identifier, select=select)
        if resp is None or resp.status != 200:
            return None
        return resp.json()

    def referenced_works(self, identifier):
        """A munka irodalomjegyzéke OpenAlex-azonosítókként (``W…``) — JELÖLT-hivatkozások (TERV 6.2),
        alacsonyabb bizonyosság, mint a bevont-vizsgálat táblák. Ingyenes egyedi lekérés."""
        w = self.work(identifier, select="id,referenced_works")
        if not w:
            return []
        return [x for x in (norm_openalex(r) for r in as_list(w.get("referenced_works"))) if x]

    def works_by_ids(self, work_ids, select=DEFAULT_SELECT, chunk=50):
        """Sok munka metaadata egyszerre (``filter=openalex:W1|W2…``) — LISTÁS lekérdezés (kredit!)."""
        ids = [x for x in (norm_openalex(w) for w in as_list(work_ids)) if x]
        out = []
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            out.extend(self.works(filter={"openalex": part}, select=select, per_page=len(part)))
        return out

    # -- listás lekérdezés (kredit; cursor-lapozás) -------------------------------------------

    def works(self, filter=None, search=None, select=DEFAULT_SELECT, per_page=200, sort=None, max_results=None,
              cursor="*"):
        """Szűrős/kereső lista ``Paged`` iterátorként (nyers munka-dictek). Iterálás után ``.total``
        (``meta.count``), ``.retrieved``, ``.complete``. 429 (keret elfogyott) → az első lapnál
        ``SourceUnavailable(rate_limited, reset_at)``, később részleges eredmény ``complete=False``-szal."""
        flt = filter_string(filter)
        per_page = max(1, min(int(per_page), 200))

        def fetch(state):
            cur = cursor if state is None else state
            params = self._params([("filter", flt), ("search", search), ("select", select),
                                   ("per_page", per_page), ("sort", sort), ("cursor", cur)])
            resp = self.http.get(SOURCE, BASE + "works", params=params, headers=self._headers(), accept="json",
                                 bucket=LIST_BUCKET)
            if resp.status != 200:
                return [], 0, None
            data = resp.json()
            items = as_list(data.get("results"))
            nxt = get_path(data, "meta", "next_cursor")
            if not items or not nxt or nxt == cur:
                nxt = None
            return items, to_int(get_path(data, "meta", "count")), nxt

        query = "filter=%s; search=%s" % (flt or "", search or "")
        return Paged(SOURCE, fetch, max_results=max_results, query=query)

    def search_reviews(self, search, from_date=None, to_date=None, extra_filter=None, max_results=200,
                       select=DEFAULT_SELECT):
        """Áttekintések keresése (``filter=type:review`` + dátum). A ``type:review`` narratív áttekintést is ad —
        a rangsor ezt lejjebb sorolja, ha a cím nem SR/MA (TERV 5.1)."""
        flt = [("type", "review")]
        if from_date:
            flt.append(("from_publication_date", from_date))
        if to_date:
            flt.append(("to_publication_date", to_date))
        for k, v in (extra_filter or {}).items():
            flt.append((k, v))
        return self.works(filter=flt, search=search, select=select, max_results=max_results)

    def cited_by(self, identifier, from_date=None, to_date=None, select=DEFAULT_SELECT, max_results=None):
        """Idéző közlemények (``filter=cites:W…``) — előre irányú hivatkozáskövetés (12.3; kredit!)."""
        w = norm_openalex(identifier)
        if not w:
            work = self.work(identifier, select="id")
            w = norm_openalex(work.get("id")) if work else None
        if not w:
            return Paged(SOURCE, lambda state: ([], 0, None), query="cites:?")
        flt = [("cites", w)]
        if from_date:
            flt.append(("from_publication_date", from_date))
        if to_date:
            flt.append(("to_publication_date", to_date))
        return self.works(filter=flt, select=select, max_results=max_results)

    # -- állapot -----------------------------------------------------------------------------

    def budget(self, resp):
        """A keret-fejlécek értelmezése (``x-ratelimit-*``): ``{remaining_usd, limit_usd, reset_seconds, cost_usd}``."""
        h = resp.headers if resp is not None else {}

        def f(name):
            try:
                return float(h.get(name))
            except (TypeError, ValueError):
                return None
        return {"remaining_usd": f("x-ratelimit-remaining-usd"), "limit_usd": f("x-ratelimit-limit-usd"),
                "reset_seconds": f("x-ratelimit-reset"), "cost_usd": f("x-ratelimit-cost-usd")}

    def _probe(self):
        resp = self.work_response("pmid:" + PROBE_PMID, select="id")
        if resp is None or resp.status != 200 or not norm_openalex((resp.json() or {}).get("id")):
            return self._result("unreachable", http_status=getattr(resp, "status", None), detail_text={
                "hu": "A próbalekérés váratlan eredményt adott.", "en": "The probe lookup returned an unexpected result."})
        budget = self.budget(resp)
        details = {"single_lookup": "ok", "budget": budget}
        blocked = self.http.blocked(LIST_BUCKET)
        if blocked is not None:
            return self._result("rate_limited", reset_at=blocked.reset_at, http_status=429, details=details,
                                detail_text={"hu": "Az egyedi lekérések (pl. irodalomjegyzék) továbbra is működnek.",
                                             "en": "Single-record lookups (e.g. reference lists) still work."})
        if not self.key_configured() and budget["remaining_usd"] is not None and budget["remaining_usd"] <= 0:
            reset_at = None
            if budget["reset_seconds"] is not None:
                reset_at = net.utc_ts(self.http.now() + budget["reset_seconds"])
            return self._result("rate_limited", reset_at=reset_at, http_status=200, details=details, detail_text={
                "hu": ("A listás lekérdezések közös napi kerete elfogyott; az egyedi lekérések (pl. irodalomjegyzék) "
                       "továbbra is működnek."),
                "en": "The shared daily budget for list queries is used up; single-record lookups still work."})
        return self._result("ok", http_status=200, details=details)
