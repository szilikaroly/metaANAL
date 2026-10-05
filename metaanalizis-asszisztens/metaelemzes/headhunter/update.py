# -*- coding: utf-8 -*-
"""Metaheadhunter — L8: frissítő keresés és hivatkozáskövetés (TERV_metaheadhunter.md 12. fejezet).

Kezdőknek: a korábbi áttekintések csak azt a vizsgálatot találhatták meg, ami a *keresésük napjáig* megjelent.
Ez a lépés kiegészíti a bányászott halmazt a frissebb irodalommal:

1. **Ablak** (12.1): a kiválasztott forrás-áttekintések bizonyítékkal rögzített keresési dátumaiból a horgony
   alapból a *legfrissebb* (``latest``; ``earliest`` érzékenyebb, ``manual`` kézi). Az ablak kezdete a horgony
   mínusz egy átfedési idő (alap 6 hónap az indexelési késés miatt — pragmatikus alapérték, nem irodalmi
   szabály), a vége a mai nap. Ha egy áttekintés nem közölte a keresési dátumát, a becslés a megjelenés
   dátuma − 12 hónap (H008) — ezt neked kell jóváhagynod. Az ablakot ember hagyja jóvá (``update_window``
   döntés); a ``--dry-run`` futtatás nélkül megmutatja a lekérdezéseket és a dátumokat.
2. **Lekérdezések** (12.2) a PICO-blokkokból, SR-szűrő NÉLKÜL, forrásonként a megfelelő dátummezővel:
   PubMed ``datetype=edat`` (bekerülés dátuma — a késve indexelteket is elkapja), Europe PMC ``CREATION_DATE``,
   OpenAlex ``from_publication_date``/``to_publication_date`` (listás lekérdezés — kredit!), Scopus
   ``PUBYEAR >`` (élőben nem igazolt kliens), ClinicalTrials.gov ``AREA[StudyFirstPostDate]RANGE[…]`` (+ a
   ``ResultsFirstPostDate`` szerinti futás). Felső korlát forrásonként (``cap``, alap 5000); ha elérjük vagy a
   lapozás megszakad, a keresés nem teljes (H012).
3. **Duplumszűrés** (12.4): a frissítés találatai előbb egymás közt (azonos PMID/DOI/PMCID/EID/OpenAlex/NCT →
   egy rekord), majd az ismert halmazzal szemben (L1): egyezés → „már ismert" (PRISMA D1), az új rekordok a
   ``studies.json``-ba kerülnek ``update_search`` eredettel; a cím–szerző–év alapú lehetséges egyezéseket a
   ``dedup`` javaslatként adja (EP3), a szűrés (EP4) emberi döntés.
4. **Hivatkozáskövetés** (12.3, ``run_cite_search``): előre (idéző közlemények) a kiválasztott áttekintésekből
   (vagy a bevont vizsgálatokból), Europe PMC ``/citations`` (és ha engedélyezett, OpenAlex ``cites:``); az
   eredet ``citation_search`` — a PRISMA „egyéb módszerek" ágába számít.

Azonosítót csak API-válaszból vesz át (N1): minden ``idval`` ``source`` mezője az API neve. Absztraktot nem
tárol. Elérhetetlen forrás mellett a lépés részlegesen lefut (H014, kilépési kód 3).

Nyilvános API::

    compute_window(reviews, anchor="latest", overlap_months=6, start=None, end=None, today=None) → ablak
    build_update_queries(query, window, sources, concepts=("P", "I")) → {forrás: lekérdezés}
    plan_update(project_dir, ...) → terv (hálózat nélkül; --dry-run)
    run_update(project_dir, actor=None, ...) → eredmény (update_search.json + studies.json)
    run_cite_search(project_dir, direction="forward", seeds="reviews", ...) → eredmény
"""
from __future__ import absolute_import

import calendar
import copy
import os
import re
from datetime import date

from . import state as S

UPDATE_SCHEMA = "szk.ma.headhunter.update-search/v1"
DATABASE_SOURCES = ("pubmed", "europepmc", "openalex", "scopus")
REGISTRY_SOURCES = ("ctgov",)
UPDATE_SOURCES = DATABASE_SOURCES + REGISTRY_SOURCES
DEFAULT_CAP = 5000
DEFAULT_OVERLAP_MONTHS = 6
SPREAD_WARN_MONTHS = 24
DEFAULT_SEED_CAP = 200
KB_UPDATE = ["D-S03-104"]
L1_KEYS = ("pmid", "doi", "pmcid", "eid", "openalex", "nct")
_BIB_PRIORITY = ("pubmed", "europepmc", "scopus", "openalex", "ctgov")
_PLATFORM = {"pubmed": "PubMed (NCBI E-utilities API)", "europepmc": "Europe PMC REST API",
             "openalex": "OpenAlex API", "scopus": "Scopus Search API (Elsevier)",
             "ctgov": "ClinicalTrials.gov API v2"}


class UpdateError(ValueError):
    """A frissítő keresés nem indítható (pl. nincs keresési dátum, hibás kézi ablak). ``code``: gépi kód."""

    def __init__(self, hu, en=None, code="HH_UPDATE"):
        ValueError.__init__(self, hu)
        self.hu = hu
        self.en = en or hu
        self.code = code


def _expl(hu, en):
    return {"hu": hu, "en": en}


def _warn(code, hu, en, **extra):
    w = {"code": code, "hu": hu, "en": en}
    w.update(extra)
    return w


# =============================================================================================
# dátumok és ablak (12.1)
# =============================================================================================

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


def parse_date(value):
    """``ÉÉÉÉ`` | ``ÉÉÉÉ-HH`` | ``ÉÉÉÉ-HH-NN`` → ``(date, pontosság)``; a hiányzó rész az időszak ELEJE (a korábbi
    dátum szélesebb, tehát érzékenyebb ablakot ad). Érvénytelenre ``(None, None)``."""
    m = _DATE_RE.match(str(value or "").strip().replace("/", "-"))
    if not m:
        return None, None
    y = int(m.group(1))
    mo = int(m.group(2) or 1)
    d = int(m.group(3) or 1)
    try:
        return date(y, mo, d), ("day" if m.group(3) else "month" if m.group(2) else "year")
    except ValueError:
        return None, None


def add_months(d, months):
    """Hónap-aritmetika (a nap a hónap végéhez igazodik)."""
    total = d.year * 12 + (d.month - 1) + int(months)
    y, m = divmod(total, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def months_between(a, b):
    return abs((b.year - a.year) * 12 + (b.month - a.month))


def _fallback_from_pub(pub):
    """Tartalék keresési dátum a megjelenésből (− 12 hónap, H008) — az ``included`` modul szabályával."""
    try:
        from .included import fallback_search_date
        return fallback_search_date(pub)
    except Exception:
        dt, prec = parse_date(pub)
        if dt is None:
            return None, None
        y = dt.year - 1
        if prec == "day":
            return "%04d-%02d-%02d" % (y, dt.month, min(dt.day, 28 if dt.month == 2 else dt.day)), "day"
        if prec == "month":
            return "%04d-%02d" % (y, dt.month), "month"
        return "%04d" % y, "year"


def review_search_dates(reviews):
    """A forrás-áttekintések keresési dátumai (bizonyítékkal vagy tartalékkal). Visszaad: ``[{review_id,
    search_date, precision, fallback, evidence_id, basis}]`` (``basis``: ``reported`` | ``fallback`` |
    ``fallback_pubdate`` | ``missing``)."""
    out = []
    for r in reviews:
        sd = r.get("search_date") or {}
        val = sd.get("value")
        if val and parse_date(val)[0] is not None:
            out.append({"review_id": r["review_id"], "search_date": val, "precision": sd.get("precision") or
                        parse_date(val)[1], "fallback": bool(sd.get("fallback")), "evidence_id": sd.get("evidence_id"),
                        "basis": "fallback" if sd.get("fallback") else "reported"})
            continue
        bib = r.get("bib") or {}
        pub = bib.get("pubdate") or bib.get("date") or (str(bib["year"]) if bib.get("year") else None)
        pub_m = re.match(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?", str(pub or ""))
        fb, prec = _fallback_from_pub(pub_m.group(0)) if pub_m else (None, None)
        out.append({"review_id": r["review_id"], "search_date": fb, "precision": prec or "unknown",
                    "fallback": True, "evidence_id": None, "basis": "fallback_pubdate" if fb else "missing"})
    return out


def compute_window(reviews, anchor="latest", overlap_months=DEFAULT_OVERLAP_MONTHS, start=None, end=None, today=None):
    """A frissítő keresés ablaka (``update-search.v1#/window``) a kiválasztott áttekintésekből.

    ``anchor``: ``latest`` (alap) | ``earliest`` | ``manual`` (ekkor ``start`` kötelező). ``end``: alapból ``today``.
    Visszaad: az ablak-dict + ``warnings`` (H008 tartalék dátum; 24 hónapnál nagyobb szórás) — a ``warnings`` és az
    ``earliest_source_search`` additív mezők."""
    if anchor not in ("latest", "earliest", "manual"):
        raise UpdateError("A horgony 'latest', 'earliest' vagy 'manual' lehet.", code="BAD_REQUEST")
    try:
        overlap_months = int(overlap_months)
    except (TypeError, ValueError):
        raise UpdateError("Az átfedési idő hónapban megadott egész szám.", code="BAD_REQUEST")
    if not 0 <= overlap_months <= 60:
        raise UpdateError("Az átfedési idő 0 és 60 hónap között lehet.", code="BAD_REQUEST")
    per = review_search_dates(reviews)
    usable = [(parse_date(p["search_date"])[0], p) for p in per if p["search_date"]]
    warnings = []
    for p in per:
        if p["fallback"] and p["search_date"]:
            warnings.append(_warn("H008", "%s: a keresési dátum nem közölt, becsült (megjelenés − 12 hónap = %s). Ellenőrizd "
                                          "az áttekintés Módszertan részét, és hagyd jóvá az ablakot."
                                  % (p["review_id"], p["search_date"]),
                                  "%s: search date not reported, estimated (publication − 12 months = %s). Check the "
                                  "review's methods and approve the window." % (p["review_id"], p["search_date"]),
                                  review_id=p["review_id"]))
        elif not p["search_date"]:
            warnings.append(_warn("H008", "%s: sem keresési, sem megjelenési dátum nem ismert — ez az áttekintés nem "
                                          "horgonyozhatja az ablakot." % p["review_id"],
                                  "%s: neither search nor publication date known — cannot anchor the window."
                                  % p["review_id"], review_id=p["review_id"]))
    latest = max(usable, key=lambda x: x[0]) if usable else None
    earliest = min(usable, key=lambda x: x[0]) if usable else None
    if anchor == "manual":
        if not start:
            raise UpdateError("Kézi horgonynál add meg a kezdő dátumot (--start ÉÉÉÉ-HH-NN).", code="BAD_REQUEST")
        a_date, _p = parse_date(start)
        if a_date is None:
            raise UpdateError("Érvénytelen kezdő dátum: %r (ÉÉÉÉ-HH-NN)." % start, code="BAD_REQUEST")
        start_d = a_date
    else:
        if not usable:
            raise UpdateError("Nincs használható keresési dátum: előbb válassz ki áttekintést (EP1) és futtasd a "
                              "kinyerést (extract), vagy adj meg kézi ablakot (--anchor manual --start …).",
                              "No usable search date: select reviews and run extract, or use --anchor manual.",
                              code="HH_NO_SEARCH_DATE")
        a_date = (latest if anchor == "latest" else earliest)[0]
        start_d = add_months(a_date, -overlap_months)
    today_d = parse_date(today)[0] if today else date.today()
    end_d = parse_date(end)[0] if end else today_d
    if end_d is None:
        raise UpdateError("Érvénytelen záró dátum: %r." % end, code="BAD_REQUEST")
    if start_d > end_d:
        raise UpdateError("Az ablak kezdete (%s) későbbi, mint a vége (%s)." % (start_d, end_d), code="BAD_REQUEST")
    if latest and earliest and months_between(earliest[0], latest[0]) > SPREAD_WARN_MONTHS:
        warnings.append(_warn("W-SPREAD", "A forrás-áttekintések keresési dátumai %d hónapra szóródnak (%s … %s): a régebbi "
                                          "áttekintés által lefedett időszak eltér. Az 'earliest' horgony érzékenyebb."
                              % (months_between(earliest[0], latest[0]), earliest[1]["search_date"],
                                 latest[1]["search_date"]),
                              "Source review search dates span %d months (%s … %s); the 'earliest' anchor is more "
                              "sensitive." % (months_between(earliest[0], latest[0]), earliest[1]["search_date"],
                                              latest[1]["search_date"])))
    return {
        "latest_source_search": latest[1]["search_date"] if latest else None,
        "earliest_source_search": earliest[1]["search_date"] if earliest else None,
        "per_review": [{"review_id": p["review_id"], "search_date": p["search_date"], "fallback": bool(p["fallback"]),
                        "evidence_id": p["evidence_id"], "basis": p["basis"]} for p in per],
        "anchor": anchor,
        "overlap_months": overlap_months if anchor != "manual" else 0,
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
        "decision_id": None,
        "warnings": warnings,
    }


def window_value(window):
    """Az ``update_window`` döntés értéke (≤ 80 karakter): ``<kezdet>..<vég>``."""
    return "%s..%s" % (window["start_date"], window["end_date"])


# =============================================================================================
# lekérdezések (12.2)
# =============================================================================================

def _quote(term):
    t = str(term).replace('"', " ").strip()
    if not t:
        return None
    if "*" in t and " " not in t:
        return t
    return '"%s"' % t if (" " in t or "-" in t) else t


def _blocks(query):
    """A ``state.pico.query_blocks`` (lista) vagy ``{"P": [...], "I": [...]}`` alak egységesítése."""
    out = []
    if isinstance(query, dict):
        if "query_blocks" in query:
            return _blocks(query["query_blocks"])
        for concept, val in query.items():
            if isinstance(val, dict):
                out.append({"concept": concept, "terms": list(val.get("terms") or []), "mesh": list(val.get("mesh") or [])})
            else:
                out.append({"concept": concept, "terms": list(val if isinstance(val, list) else [val]), "mesh": []})
    else:
        for b in query or []:
            if isinstance(b, dict):
                out.append({"concept": b.get("concept"), "terms": list(b.get("terms") or []),
                            "mesh": list(b.get("mesh") or [])})
    return [b for b in out if b["terms"] or b["mesh"]]


def _topic(query, concepts=("P", "I")):
    """A téma forrásonkénti alakja SR-szűrő nélkül. ``query``: szöveg vagy fogalomblokkok."""
    if isinstance(query, str):
        q = query.strip()
        if not q:
            raise UpdateError("Üres lekérdezés.", code="BAD_REQUEST")
        plain = re.sub(r"\s+", " ", re.sub(r"\[[A-Za-z :/_-]{1,40}\]", "", q)).strip()
        return {"pubmed": q, "plain": plain, "openalex": plain, "scopus": "TITLE-ABS-KEY(%s)" % plain, "ctgov": plain}
    blocks = _blocks(query)
    chosen = [b for b in blocks if b["concept"] in concepts] or blocks
    if not chosen:
        raise UpdateError("Nincs keresőkifejezés: add meg a PICO fogalomblokkjait (init --population/--intervention "
                          "vagy --pico), vagy adj meg lekérdezést (--query / --query-file).",
                          "No query terms: provide PICO blocks or --query.", code="BAD_REQUEST")
    pm, ep, oa, sc, ct = [], [], [], [], []
    for b in chosen:
        qt = [x for x in (_quote(t) for t in b["terms"]) if x]
        mesh = [str(m).replace('"', "") for m in b["mesh"] if str(m).strip()]
        pm.append("(%s)" % " OR ".join(["%s[tiab]" % t for t in qt] + ['"%s"[mh]' % m for m in mesh]))
        # Europe PMC: cím/absztrakt/kulcsszó (a PubMed [tiab] megfelelője) — az alapértelmezett mező a nyílt teljes
        # szövegben is keres, ami a frissítésnél sokszoros zajt ad (élőben: 338 vs 27 találat ugyanarra)
        ep.append("(%s)" % " OR ".join(["(TITLE:%s OR ABSTRACT:%s OR KW:%s)" % (t, t, t) for t in qt] +
                                       ['MESH:"%s"' % m for m in mesh]))
        if qt:
            oa.append("(%s)" % " OR ".join(qt))
            sc.append("TITLE-ABS-KEY(%s)" % " OR ".join(qt))
            ct.append("(%s)" % " OR ".join(qt))
    return {"pubmed": " AND ".join(pm), "plain": " AND ".join(ep), "openalex": " AND ".join(oa),
            "scopus": " AND ".join(sc), "ctgov": " AND ".join(ct)}


def build_update_queries(query, window, sources=UPDATE_SOURCES, concepts=("P", "I"), query_by_source=None):
    """Forrásonkénti frissítő lekérdezések a dátumkorláttal (12.2). ``query_by_source``: forrásonként szó
    szerint futtatandó TÉMA-lekérdezés (pl. a forrás-áttekintés közölt stratégiája, ``--query-file``) — a
    dátumkorlátot a program akkor is hozzáteszi. Visszaad: ``{forrás: [{"query", "date_field", …paraméterek}]}``
    (a CT.gov-nál két futás: ``StudyFirstPostDate`` és ``ResultsFirstPostDate``)."""
    over = dict(query_by_source or {})
    topic = _topic(query, concepts) if (query or not over) else {}
    start, end = window["start_date"], window["end_date"]
    out = {}
    for s in sources:
        if s == "pubmed":
            term = over.get("pubmed") or topic.get("pubmed")
            out[s] = [{"term": term, "datetype": "edat", "mindate": start.replace("-", "/"),
                       "maxdate": end.replace("-", "/"), "date_field": "edat",
                       "query": "%s [edat %s–%s]" % (term, start, end)}]
        elif s == "europepmc":
            base = over.get("europepmc") or topic.get("plain")
            q = "(%s) AND CREATION_DATE:[%s TO %s]" % (base, start, end)
            out[s] = [{"query": q, "date_field": "CREATION_DATE"}]
        elif s == "openalex":
            search = over.get("openalex") or topic.get("openalex")
            flt = [("from_publication_date", start), ("to_publication_date", end)]
            out[s] = [{"search": search, "filter": flt, "date_field": "from_publication_date",
                       "query": "search=%s; filter=from_publication_date:%s,to_publication_date:%s" % (search, start,
                                                                                                   end)}]
        elif s == "scopus":
            base = over.get("scopus") or topic.get("scopus")
            q = "%s AND PUBYEAR > %d" % (base, int(start[:4]) - 1)
            out[s] = [{"query": q, "date_field": "PUBYEAR", "unverified_live": True}]
        elif s == "ctgov":
            term = over.get("ctgov") or topic.get("ctgov")
            out[s] = [
                {"term": term, "advanced": "AREA[StudyFirstPostDate]RANGE[%s,%s]" % (start, end),
                 "date_field": "StudyFirstPostDate",
                 "query": "query.term=%s; filter.advanced=AREA[StudyFirstPostDate]RANGE[%s,%s]" % (term, start, end)},
                {"term": term, "advanced": "AREA[ResultsFirstPostDate]RANGE[%s,%s]" % (start, end),
                 "date_field": "ResultsFirstPostDate",
                 "query": "query.term=%s; filter.advanced=AREA[ResultsFirstPostDate]RANGE[%s,%s]" % (term, start, end)},
            ]
    return out


# =============================================================================================
# rekordok (API-válasz → studies.json rekord)
# =============================================================================================

def _d():
    from . import dedup
    return dedup


def _norm(kind, value):
    try:
        return _d().norm_id(kind, value)
    except Exception:  # pragma: no cover
        v = str(value or "").strip()
        return v.lower() if kind == "doi" else v


def _clean_bib(meta):
    bib = {}
    for k in ("title", "authors", "authors_truncated", "first_author", "last_author", "year", "journal", "volume",
              "issue", "pages", "pub_types", "language"):
        v = meta.get(k)
        if v in (None, "", []):
            continue
        bib[k] = v
    if isinstance(bib.get("year"), str):
        try:
            bib["year"] = int(bib["year"][:4])
        except ValueError:
            bib.pop("year")
    if bib.get("year") is not None and not 1800 <= int(bib["year"]) <= 2100:
        bib.pop("year")
    if bib.get("authors"):
        bib["authors"] = [str(a) for a in bib["authors"] if a][:50]
    if bib.get("pub_types"):
        bib["pub_types"] = sorted(set(str(p) for p in bib["pub_types"] if p))
    if bib.get("title"):
        bib["title"] = str(bib["title"])[:1000]
    return bib


def make_record(meta, source, via, search_id, at, retrieval=None, route="update_search"):
    """Egy API-találat → ``studies.json`` rekord (``origins: [{route, search_id}]``). Az azonosítók ``source``-a az
    API neve (N1). ``None``, ha a találatnak sem azonosítója, sem címe nincs."""
    ids = {}
    for k in L1_KEYS:
        v = meta.get(k)
        if v:
            nv = _norm(k, v)
            if nv:
                ids[k] = {"value": nv, "source": source, "via": via, "at": at}
    bib = _clean_bib(meta)
    if not ids and not bib.get("title"):
        return None
    flags = {}
    if meta.get("retracted") or meta.get("is_retracted"):
        flags["retracted"] = True
    if source == "ctgov":
        flags["registry_record"] = True
    pts = " ".join(bib.get("pub_types") or []).lower()
    if "preprint" in pts or meta.get("source_db") == "PPR":
        flags["preprint"] = True
    if "erratum" in pts:
        flags["erratum"] = True
    if any(x in pts for x in ("review", "meta-analysis", "editorial", "comment", "letter")):
        flags["not_primary_hint"] = True
    d = _d()
    if ids:
        rid = d.rec_id_for(ids)
    else:
        text = " ".join(str(x) for x in (bib.get("first_author"), bib.get("year"), bib.get("title")) if x)
        rid = d.rec_id_for({}, text, salt="%s:%s" % (source, meta.get("id") or ""))
    rec = {"rec_id": rid, "ids": ids, "bib": bib, "flags": flags, "related": [],
           "origins": [{"route": route, "review_id": None, "cand_id": None, "search_id": search_id}],
           "retrievals": [], "resolution": {"status": "resolved" if ids else "unresolved",
                                             "method": "%s:%s" % (route.replace("_", "-"), source), "score": None},
           "status": "active", "merged_into": None}
    if isinstance(retrieval, dict) and retrieval.get("source") and retrieval.get("endpoint") and retrieval.get("at"):
        rec["retrievals"].append(dict((k, retrieval.get(k)) for k in ("source", "endpoint", "at", "http_status",
                                                                       "cache_key")))
    if source == "scopus" or meta.get("unverified_live"):
        rec["resolution"]["unverified_live"] = True
    return rec


def _ctgov_meta(raw):
    from .ctgov import study_record
    r = study_record(raw)
    year = None
    m = re.match(r"^(\d{4})", str(r.get("first_post_date") or r.get("start_date") or ""))
    if m:
        year = int(m.group(1))
    return {"nct": r.get("nct"), "title": r.get("official_title") or r.get("brief_title"), "year": year,
            "first_author": None, "journal": "ClinicalTrials.gov", "pub_types": ["Registry record"]}


def _keys(rec):
    out = []
    for k in L1_KEYS:
        iv = (rec.get("ids") or {}).get(k)
        if isinstance(iv, dict) and iv.get("value"):
            out.append((k, _norm(k, iv["value"])))
    return out


def _merge_into(into, new, prio):
    """Két (ugyanarra a közleményre mutató) frissítő-rekord egyesítése: eredet unió, hiányzó azonosítók és
    bibliográfiai mezők pótlása (a ``prio`` szerinti forrás-elsőbbséggel)."""
    for o in new.get("origins") or []:
        if o not in into["origins"]:
            into["origins"].append(o)
    for k, iv in (new.get("ids") or {}).items():
        into["ids"].setdefault(k, iv)
    if prio(new) < prio(into):
        merged = dict(into["bib"])
        merged.update(new.get("bib") or {})
        into["bib"] = merged
    else:
        for k, v in (new.get("bib") or {}).items():
            into["bib"].setdefault(k, v)
    for k, v in (new.get("flags") or {}).items():
        if v and k != "registry_record":
            into["flags"][k] = True
    for x in new.get("retrievals") or []:
        if x not in into["retrievals"] and len(into["retrievals"]) < 10:
            into["retrievals"].append(x)


def dedupe_hits(records):
    """A frissítés találatainak duplumszűrése egymás közt (L1: bármely közös PMID/DOI/PMCID/EID/OpenAlex/NCT —
    unió-kereséssel, így a lánc-egyezés is egy rekord lesz). Visszaad: ``(egyedi rekordok, duplikátumok száma)``.
    Determinisztikus (a bemenet sorrendjétől független kimeneti sorrend)."""
    def prio(rec):
        src = None
        for iv in (rec.get("ids") or {}).values():
            if isinstance(iv, dict):
                src = iv.get("source")
                break
        return _BIB_PRIORITY.index(src) if src in _BIB_PRIORITY else len(_BIB_PRIORITY)
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first = {}
    for i, rec in enumerate(records):
        keys = _keys(rec) or [("rec_id", rec["rec_id"])]
        for k in keys:
            if k in first:
                a, b = find(first[k]), find(i)
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                first[k] = i
    groups = {}
    for i in range(len(records)):
        groups.setdefault(find(i), []).append(i)
    d = _d()
    uniq = []
    for _root, idx in sorted(groups.items()):
        members = sorted((records[i] for i in idx), key=lambda r: (prio(r), r["rec_id"]))
        base = copy.deepcopy(members[0])
        for other in members[1:]:
            _merge_into(base, other, prio)
        if base["ids"]:
            base["rec_id"] = d.rec_id_for(base["ids"])  # a legerősebb azonosítóból
        uniq.append(base)
    uniq.sort(key=lambda r: d.rec_sort_key(r["rec_id"]))
    return uniq, len(records) - len(uniq)


def _strip_route(records, route):
    """A korábbi futás ``route`` eredetének eltávolítása (újrafuttatás idempotens): a csak ebből az eredetből
    származó rekord kiesik, a többiről csak ez az eredet."""
    out = []
    for r in records:
        rr = copy.deepcopy(r)
        others = [o for o in rr.get("origins") or [] if o.get("route") != route]
        if len(others) != len(rr.get("origins") or []):
            if not others:
                continue
            rr["origins"] = others
        out.append(rr)
    return out


def _route_set(rec):
    return set(o.get("route") for o in rec.get("origins") or [])


def integrate(prior_records, new_records, route, search_ids=None):
    """Az új (frissítő / hivatkozáskövető) rekordok beillesztése az ismert rekordok közé (12.4).

    Visszaad: ``(rekordok, stat)`` — ``stat``: ``already_known`` (az egyéb-módszerek ágában — áttekintésből,
    hivatkozáskövetésből, kézzel — már ismert rekordok száma), ``known_other_route`` (más útvonalon — pl. a
    frissítésben — már ismert), ``new_records`` (új ``rec_id``-k), ``registry_records``."""
    records = _strip_route(prior_records, route)
    d = _d()
    cmap = d.canonical_map(records)
    by_id = dict((r["rec_id"], r) for r in records)
    index = {}
    for r in sorted(records, key=lambda x: (x.get("status") == "merged_into", x["rec_id"])):
        canon = cmap.get(r["rec_id"], r["rec_id"])
        for k, v in d.trusted_ids(r).items():
            index.setdefault((k, v), canon)
        nct = (r.get("ids") or {}).get("nct")
        if d.trusted(nct) and (r.get("flags") or {}).get("registry_record"):
            index.setdefault(("nct", _norm("nct", nct["value"])), canon)
    stat = {"already_known": 0, "known_other_route": 0, "new_records": [], "registry_records": [],
            "matched": []}
    other_routes = ("review_extraction", "citation_search", "manual")
    for nr in new_records:
        keys = [(k, v) for k, v in _keys(nr) if k != "nct" or (nr.get("flags") or {}).get("registry_record")]
        hit = None
        for k in keys:
            if k in index:
                hit = index[k]
                break
        if hit is None and nr["rec_id"] in by_id:
            hit = nr["rec_id"]
        if hit is not None:
            tgt = by_id[cmap.get(hit, hit)]
            for o in nr["origins"]:
                if o not in tgt.setdefault("origins", []):
                    tgt["origins"].append(o)
            for k, iv in (nr.get("ids") or {}).items():
                if k not in (tgt.get("ids") or {}) and k != "registry":
                    tgt.setdefault("ids", {})[k] = iv
            routes = _route_set(tgt) - set([route])
            if routes & set(other_routes):
                stat["already_known"] += 1
            else:
                stat["known_other_route"] += 1
            stat["matched"].append(tgt["rec_id"])
            continue
        rec = copy.deepcopy(nr)
        records.append(rec)
        by_id[rec["rec_id"]] = rec
        cmap[rec["rec_id"]] = rec["rec_id"]
        for k in keys:
            index.setdefault(k, rec["rec_id"])
        stat["new_records"].append(rec["rec_id"])
        if (rec.get("flags") or {}).get("registry_record"):
            stat["registry_records"].append(rec["rec_id"])
    records.sort(key=lambda r: r["rec_id"])
    stat["new_records"] = sorted(set(stat["new_records"]))
    stat["registry_records"] = sorted(set(stat["registry_records"]))
    return records, stat


# =============================================================================================
# források futtatása
# =============================================================================================

def _client_factory(state, http, env, clients, offline, project_dir):
    from . import sources as srcreg
    cache = {}

    def get(name):
        if clients is not None:
            return clients.get(name)
        if name not in cache:
            h = http
            if h is None:
                from . import net
                h = net.HttpClient(cache_dir=S.path(project_dir, "cache", "http"), offline=offline, env=env)
            cache[name] = srcreg.make_client(name, http=h, env=env)
        return cache[name]
    return get


def _select(state, sources, env, clients, allowed):
    """A használandó források (a projekt-beállítás vagy ``--sources``) és a kihagyottak magyar indoklással."""
    if clients is not None:
        wanted = [s.strip() for s in (sources.split(",") if isinstance(sources, str) else (sources or clients))]
        use = [s for s in allowed if s in wanted and s in clients]
        return use, []
    from . import sources as srcreg
    sel = srcreg.select_sources(state=state, override=sources, env=env)
    use = [s for s in sel["use"] if s in allowed]
    skipped = [sk for sk in sel["skipped"] if sk["source"] in allowed]
    return use, skipped


def _unavailable(exc):
    status = getattr(exc, "status", None) or "unreachable"
    msg = getattr(exc, "explain", None) or _expl("A forrás nem érhető el: %s" % exc, "Source unavailable: %s" % exc)
    return status, msg


def _shape_errors():
    try:
        from . import net
        return (net.HttpError, net.ParseError) + tuple(net.SHAPE_ERRORS)
    except Exception:  # pragma: no cover
        return (ValueError, KeyError, TypeError)


def _source_unavailable_cls():
    try:
        from .net import SourceUnavailable
        return SourceUnavailable
    except Exception:  # pragma: no cover
        class _Never(Exception):
            pass
        return _Never


def _run_query(source, client, q, cap, sid, at):
    """Egy lekérdezés futtatása. Visszaad: ``(rekordok, info)`` — ``info``: ``count_total``, ``count_retrieved``,
    ``complete``, ``error`` (``None`` vagy ``{status, message}``)."""
    recs = []
    if source == "pubmed":
        res = client.esearch_all(q["term"], cap=cap, datetype=q["datetype"], mindate=q["mindate"],
                                 maxdate=q["maxdate"])
        if res["ids"]:
            for s in client.esummary(res["ids"]):
                meta = dict(s)
                ret = meta.pop("retrieval", None)
                meta.pop("raw", None)
                rec = make_record(meta, "pubmed", "pubmed.esearch", sid, at, ret)
                if rec:
                    recs.append(rec)
        info = {"count_total": res["count"], "count_retrieved": res.get("retrieved", len(res["ids"])),
                "complete": bool(res.get("complete")), "error": res.get("error")}
        return recs, info
    if source == "europepmc":
        from .europepmc import record as epmc_record
        pager = client.search(q["query"], result_type="lite", page_size=min(1000, max(1, cap)), max_results=cap)
        for raw in pager:
            meta = epmc_record(raw)
            meta.pop("abstract", None)
            rec = make_record(meta, "europepmc", "europepmc.search", sid, at)
            if rec:
                recs.append(rec)
        return recs, _pager_info(pager)
    if source == "openalex":
        from .openalex import work_record
        pager = client.works(filter=q["filter"], search=q["search"], max_results=cap)
        for raw in pager:
            meta = work_record(raw)
            meta["pub_types"] = [meta.get("type")] if meta.get("type") else []
            rec = make_record(meta, "openalex", "openalex.works", sid, at)
            if rec:
                recs.append(rec)
        return recs, _pager_info(pager)
    if source == "scopus":
        pager = client.search(q["query"], max_results=cap, normalize=True)
        for meta in pager:
            meta = dict(meta)
            meta["pub_types"] = [x for x in (meta.get("doctype_desc"),) if x]
            rec = make_record(meta, "scopus", "scopus.search", sid, at)
            if rec:
                recs.append(rec)
        return recs, _pager_info(pager)
    if source == "ctgov":
        pager = client.studies(term=q["term"], advanced=q["advanced"], max_results=cap)
        for raw in pager:
            rec = make_record(_ctgov_meta(raw), "ctgov", "ctgov.studies", sid, at)
            if rec:
                recs.append(rec)
        return recs, _pager_info(pager)
    raise ValueError("Ismeretlen forrás: %r" % source)


def _pager_info(pager):
    err = None
    if getattr(pager, "error", None) is not None:
        err = {"status": getattr(pager.error, "status", "unreachable"), "message": getattr(pager.error, "explain", None)}
    total = pager.total if pager.total is not None else pager.retrieved
    return {"count_total": total, "count_retrieved": pager.retrieved, "complete": bool(pager.complete),
            "error": err}


# =============================================================================================
# terv és futtatás
# =============================================================================================

def _load_inputs(project_dir):
    state = S.load_state(project_dir)
    reviews = S.load_reviews(project_dir)
    selected = S.selected_reviews(reviews)
    return state, reviews, selected


def _query_input(state, query):
    if query:
        return query
    blocks = ((state.get("pico") or {}).get("query_blocks")) or []
    return blocks


def plan_update(project_dir, anchor="latest", overlap_months=None, start=None, end=None, sources=None, query=None,
                query_by_source=None, today=None, env=None, clients=None):
    """A frissítő keresés terve hálózat nélkül (``--dry-run``): ablak, forrásonkénti lekérdezések és dátumok, a
    kihagyott források oka. Fájlt nem ír."""
    state, _reviews, selected = _load_inputs(project_dir)
    if overlap_months is None:
        overlap_months = (state.get("settings") or {}).get("overlap_window_months", DEFAULT_OVERLAP_MONTHS)
    window = compute_window(selected, anchor=anchor, overlap_months=overlap_months, start=start, end=end, today=today)
    use, skipped = _select(state, sources, env, clients, UPDATE_SOURCES)
    queries = build_update_queries(_query_input(state, query), window, use, query_by_source=query_by_source)
    approved = _approved_window(S.read_decisions(project_dir), window)
    return {"window": window, "queries": queries, "use": use, "skipped": skipped, "selected_reviews":
            [r["review_id"] for r in selected], "approved_decision": approved["decision_id"] if approved else None}


def _approved_window(decisions, window):
    eff = S.effective_decisions(decisions, kinds=("update_window",))
    d = eff.get(("window", "update"))
    if d and d.get("value") == window_value(window) and str(d.get("actor") or "").startswith("user:"):
        return d
    return None


def run_update(project_dir, actor=None, anchor="latest", overlap_months=None, start=None, end=None, cap=DEFAULT_CAP,
               sources=None, query=None, query_by_source=None, reason=None, today=None, now=None, http=None,
               clients=None, env=None, offline=False, run=None, progress=None):
    """L8 frissítő keresés a projekten.

    Az ablakot ember hagyja jóvá: ha még nincs a mostani ablakkal egyező ``update_window`` döntés, ``actor``
    (``user:…``) megadásával a futás rögzíti; ``actor`` nélkül a lépés NEM fut (``exit_code`` 4, a terv a
    válaszban). Kimenet: ``update_search.json``, bővített ``studies.json`` (új rekordok ``update_search``
    eredettel, újraszámolt duplikátum-javaslatok), ``state.searches[]``. Visszaad: ``{ok, data, warnings,
    errors, pending, exit_code, next}``."""
    state, reviews, selected = _load_inputs(project_dir)
    if overlap_months is None:
        overlap_months = (state.get("settings") or {}).get("overlap_window_months", DEFAULT_OVERLAP_MONTHS)
    window = compute_window(selected, anchor=anchor, overlap_months=overlap_months, start=start, end=end,
                            today=today or (S.today(now) if now else None))
    warnings = list(window.pop("warnings"))
    decisions = S.read_decisions(project_dir)
    approved = _approved_window(decisions, window)
    if approved is None:
        if not actor:
            plan = plan_update(project_dir, anchor=anchor, overlap_months=overlap_months, start=start, end=end,
                               sources=sources, query=query, query_by_source=query_by_source,
                               today=today or (S.today(now) if now else None), env=env, clients=clients)
            return {"ok": False, "data": {"plan": plan}, "warnings": warnings, "errors": [],
                    "pending": [{"checkpoint": "update_window", "n": 1}], "exit_code": 4,
                    "next": "update %s --actor user:<név>   (az ablak jóváhagyása: %s)"
                            % ("<projekt>", window_value(window)),
                    "message": _expl("A frissítési ablakot (%s) neked kell jóváhagynod: futtasd újra --actor "
                                     "user:<neved> kapcsolóval (a --dry-run a lekérdezéseket is megmutatja)."
                                     % window_value(window),
                                     "Approve the update window (%s): re-run with --actor user:<name>."
                                     % window_value(window))}
        reason_txt = reason or ("Frissítő keresés ablaka: horgony=%s (%s), átfedés=%d hónap, %s."
                                % (window["anchor"], window.get("latest_source_search") if anchor == "latest" else
                                   window.get("earliest_source_search") if anchor == "earliest" else "kézi",
                                   window["overlap_months"], window_value(window)))
        if any(w["code"] == "H008" for w in warnings):
            reason_txt += " A becsült (nem közölt) keresési dátumokat jóváhagytam."
        approved = S.append_decision(project_dir, "update_window", ("window", "update"), window_value(window),
                                     actor, now=now, reason=reason_txt[:2000], kb_refs=KB_UPDATE)
    window["decision_id"] = approved["decision_id"]

    use, skipped = _select(state, sources, env, clients, UPDATE_SOURCES)
    queries = build_update_queries(_query_input(state, query), window, use, query_by_source=query_by_source)
    get_client = _client_factory(state, http, env, clients, offline, project_dir)
    at = S.utc_now(now)
    taken = set(s.get("search_id") for s in state.get("searches") or [])
    q_entries, searches, hits = [], [], []
    src_status = {}
    for sk in skipped:
        src_status[sk["source"]] = {"status": sk["status"], "searched": False}
        if sk["status"] != "not_configured" or sources is not None:
            warnings.append(_warn("H014", sk["message"]["hu"], sk["message"]["en"], source=sk["source"]))
    SU = _source_unavailable_cls()
    shape = _shape_errors()
    cancelled = False
    for i, s in enumerate(use):
        if run is not None and run.cancelled():
            cancelled = True
            warnings.append(_warn("H012", "A futást megszakították (CANCEL): a hátralévő források kimaradtak.",
                                  "Run cancelled: remaining sources were skipped."))
            break
        client = get_client(s)
        for q in queries.get(s, []):
            sid = S.unique_search_id(s, at, taken)
            if progress:
                progress({"phase": "search", "source": s, "done": i, "total": len(use),
                          "message": _expl("Frissítő keresés: %s" % s, "Update search: %s" % s)})
            entry = {"search_id": sid, "source": s, "query": q["query"], "date_field": q["date_field"],
                     "status": "planned", "count_total": None, "count_retrieved": None, "message": None,
                     "date_from": window["start_date"], "date_to": window["end_date"]}
            try:
                recs, info = _run_query(s, client, q, int(cap), sid, at)
            except SU as exc:
                st, msg = _unavailable(exc)
                entry.update(status="skipped_source_unavailable", message=msg)
                src_status[s] = {"status": st, "searched": False, "reset_at": getattr(exc, "reset_at", None)}
                warnings.append(_warn("H014", msg["hu"], msg["en"], source=s))
                q_entries.append(entry)
                break
            except shape as exc:
                detail = str(exc)[:200]
                try:
                    from . import net
                    detail = net.redact(detail, env)
                except Exception:  # pragma: no cover
                    pass
                msg = _expl("%s: a keresés hibát adott — %s" % (s, detail), "%s: the search failed — %s" % (s, detail))
                entry.update(status="failed", message=msg)
                src_status[s] = {"status": "unreachable", "searched": False}
                warnings.append(_warn("H014", msg["hu"], msg["en"], source=s))
                q_entries.append(entry)
                break
            complete = bool(info["complete"])
            entry.update(status="done" if complete and not info.get("error") else "partial",
                         count_total=info["count_total"], count_retrieved=info["count_retrieved"],
                         complete=complete)
            if info.get("error"):
                err = info["error"]
                m = err.get("message") or _expl("a lapozás megszakadt", "paging interrupted")
                entry["message"] = m
                warnings.append(_warn("H014", m.get("hu", ""), m.get("en", ""), source=s))
            if not complete:
                warnings.append(_warn("H012", "%s: %s találatból %s letöltve (felső korlát: %d vagy megszakadt lapozás) — "
                                              "szűkíts, vagy emeld a korlátot (--cap)."
                                      % (s, info["count_total"], info["count_retrieved"], int(cap)),
                                      "%s: %s of %s hits retrieved (cap %d or interrupted paging)."
                                      % (s, info["count_retrieved"], info["count_total"], int(cap)), source=s))
            src_status.setdefault(s, {"status": "ok", "searched": True})
            q_entries.append(entry)
            searches.append({"search_id": sid, "purpose": "update_registry" if s in REGISTRY_SOURCES else
                             "update_database", "source": s, "platform": _PLATFORM.get(s),
                             "query": q["query"], "filters": dict((k, v) for k, v in q.items()
                                                                  if k in ("datetype", "mindate", "maxdate", "advanced")
                                                                  ) or None,
                             "date_field": q["date_field"], "date_from": window["start_date"],
                             "date_to": window["end_date"], "run_at": at, "count_total": info["count_total"],
                             "count_retrieved": info["count_retrieved"], "complete": complete,
                             "export": None, "note": "Scopus: élőben nem igazolt kliens (unverified-live)"
                             if s == "scopus" else None})
            hits.extend(recs)

    retrieved_total = sum(int(e.get("count_retrieved") or 0) for e in q_entries)
    uniq, dup_within = dedupe_hits(hits)
    # letöltött, de azonosító és cím nélküli (vagy a metaadat-lekérésből kimaradt) tételek: PRISMA D3
    unusable = max(0, retrieved_total - len(hits))
    with S.lock(project_dir):
        prior = S.read_json(S.path(project_dir, S.FILES["studies"])) or {}
        records, stat = integrate(prior.get("records") or [], uniq, "update_search")
        d = _d()
        doc = d.link(records, S.load_reviews(project_dir), S.read_decisions(project_dir), prior=prior, now=now)
        S.write_json_atomic(S.path(project_dir, S.FILES["studies"]), doc)
        upd = S.read_json(S.path(project_dir, S.FILES["update"])) or {}
        by_source = {}
        for e in q_entries:
            b = by_source.setdefault(e["source"], {"count_total": 0, "count_retrieved": 0, "complete": True})
            b["count_total"] += int(e.get("count_total") or 0)
            b["count_retrieved"] += int(e.get("count_retrieved") or 0)
            b["complete"] = b["complete"] and e["status"] == "done"
        update_doc = {
            "schema": UPDATE_SCHEMA, "model": S.MODEL, "generated": at, "window": window,
            "queries": q_entries,
            "citation_search": upd.get("citation_search") or [],
            "results": {"retrieved_total": retrieved_total, "duplicates_within": dup_within,
                        "unusable": unusable,
                        "already_known": stat["already_known"], "known_from_update": stat["known_other_route"],
                        "new_records": stat["new_records"], "registry_records": stat["registry_records"],
                        "by_source": by_source,
                        "databases_retrieved": sum(b["count_retrieved"] for k, b in by_source.items()
                                                   if k in DATABASE_SOURCES),
                        "registers_retrieved": sum(b["count_retrieved"] for k, b in by_source.items()
                                                   if k in REGISTRY_SOURCES)},
            "citation_results": upd.get("citation_results") or None,
            "sources": src_status, "warnings": warnings, "cancelled": cancelled,
        }
        if update_doc["citation_results"] is None:
            update_doc.pop("citation_results")
        errs = S.schema_errors(update_doc, "update-search")
        if errs:
            raise UpdateError("Az update_search.json nem felel meg a sémának (H001): %s" % "; ".join(errs[:5]))
        S.write_json_atomic(S.path(project_dir, S.FILES["update"]), update_doc)
        st = S.load_state(project_dir)
        S.add_searches(st, searches)
        pend = (doc.get("summary") or {}).get("pending", 0)
        down = [s for s, v in src_status.items() if v.get("status") != "ok"]
        status = "needs_human" if (stat["new_records"] or pend) else "done"
        S.set_step(st, "update_search", "failed" if cancelled else status,
                   run_id=run.run_id if run is not None else None, now=now, stale_downstream=True)
        if stat["new_records"]:
            S.set_checkpoint(st, "EP4", "pending", open_items=len(stat["new_records"]))
        if pend:
            S.set_checkpoint(st, "EP3", "pending", open_items=pend)
        S.save_state(project_dir, st, env=env, now=now)
    exit_code = 3 if (down or cancelled) else (4 if (stat["new_records"] or pend) else 0)
    pending = []
    if pend:
        pending.append({"checkpoint": "EP3", "n": pend})
    if stat["new_records"]:
        pending.append({"checkpoint": "EP4", "n": len(stat["new_records"])})
    return {"ok": True, "data": {"window": window, "queries": q_entries, "results": update_doc["results"],
                                 "sources": src_status,
                                 "files": [S.HH_REL + "/" + S.FILES["update"], S.HH_REL + "/" + S.FILES["studies"]]},
            "warnings": warnings, "errors": [], "pending": pending, "exit_code": exit_code,
            "next": "confirm/exclude (szűrés, EP4), majd merge" if stat["new_records"] else "merge"}


# =============================================================================================
# hivatkozáskövetés (12.3)
# =============================================================================================

def _seed_pmids(project_dir, seeds, reviews):
    out = []
    if seeds in ("reviews", "both"):
        for r in S.selected_reviews(reviews):
            p = ((r.get("ids") or {}).get("pmid") or {}).get("value")
            if p:
                out.append(("review", r["review_id"], str(p)))
    if seeds in ("included", "both"):
        merged = S.read_json(S.path(project_dir, S.FILES["merged"])) or {}
        for s in merged.get("studies") or []:
            if s.get("status") != "included":
                continue
            for rp in s.get("reports") or []:
                p = ((rp.get("ids") or {}).get("pmid") or {}).get("value")
                if p:
                    out.append(("study", s["study_id"], str(p)))
    seen, uniq = set(), []
    for x in out:
        if x[2] not in seen:
            seen.add(x[2])
            uniq.append(x)
    return uniq


def run_cite_search(project_dir, actor=None, direction="forward", seeds="reviews", sources=None,
                    cap_seeds=DEFAULT_SEED_CAP, cap_per_seed=1000, since=None, now=None, http=None, clients=None,
                    env=None, offline=False, run=None, progress=None):
    """Hivatkozáskövetés (12.3): előre (``forward``: idéző közlemények) vagy hátra (``backward``: irodalomjegyzék),
    magok: a kiválasztott áttekintések (``reviews``), a bevont vizsgálatok (``included``) vagy mindkettő. Forrás:
    Europe PMC (alap; ``/citations``, ``/references``), OpenAlex ``cites:`` (ha a forráslista tartalmazza;
    kredit!). Dátumszűrés előre irányban: csak a ``since`` (alap: a frissítő ablak kezdete) utáni év(ek).
    Az eredet ``citation_search`` (PRISMA: egyéb módszerek ág). TARCiS-adatok a ``citation_search[]``-ben."""
    if direction not in ("forward", "backward", "both"):
        raise UpdateError("Az irány 'forward', 'backward' vagy 'both'.", code="BAD_REQUEST")
    if seeds not in ("reviews", "included", "both"):
        raise UpdateError("A magok 'reviews', 'included' vagy 'both'.", code="BAD_REQUEST")
    state = S.load_state(project_dir)
    reviews = S.load_reviews(project_dir)
    seed_list = _seed_pmids(project_dir, seeds, reviews)[:int(cap_seeds)]
    if not seed_list:
        raise UpdateError("Nincs mag (PMID-del rendelkező kiválasztott áttekintés vagy bevont vizsgálat).",
                          "No seeds with a PMID.", code="HH_NO_SEEDS")
    upd = S.read_json(S.path(project_dir, S.FILES["update"])) or {}
    if since is None:
        since = ((upd.get("window") or {}).get("start_date"))
    since_year = int(str(since)[:4]) if since else None
    use, skipped = _select(state, sources or ["europepmc"], env, clients, ("europepmc", "openalex"))
    get_client = _client_factory(state, http, env, clients, offline, project_dir)
    at = S.utc_now(now)
    taken = set(s.get("search_id") for s in state.get("searches") or [])
    taken |= set(c.get("search_id") for c in upd.get("citation_search") or [])
    entries, searches, hits, warnings = [], [], [], []
    for sk in skipped:
        warnings.append(_warn("H014", sk["message"]["hu"], sk["message"]["en"], source=sk["source"]))
    SU = _source_unavailable_cls()
    shape = _shape_errors()
    dirs = ("forward", "backward") if direction == "both" else (direction,)
    for s in use:
        client = get_client(s)
        for dr in dirs:
            if s == "openalex" and dr == "backward":
                continue
            sid = S.unique_search_id(s, at, taken)
            count, status, retrieved = 0, "done", 0
            for kind, owner, pmid in seed_list:
                if run is not None and run.cancelled():
                    status = "partial"
                    break
                try:
                    if s == "europepmc":
                        pager = (client.citations if dr == "forward" else client.references)(
                            "MED", pmid, max_results=int(cap_per_seed), normalize=True)
                        items = list(pager)
                        if pager.complete is False:
                            status = "partial"
                        via = "europepmc.%s" % ("citations" if dr == "forward" else "references")
                    else:
                        pager = client.cited_by("pmid:%s" % pmid, from_date="%d-01-01" % since_year if since_year
                                                else None, max_results=int(cap_per_seed))
                        from .openalex import work_record
                        items = [work_record(w) for w in pager]
                        if pager.complete is False:
                            status = "partial"
                        via = "openalex.cites"
                except SU as exc:
                    st, msg = _unavailable(exc)
                    warnings.append(_warn("H014", msg["hu"], msg["en"], source=s))
                    status = "skipped_source_unavailable" if count == 0 else "partial"
                    break
                except shape as exc:
                    warnings.append(_warn("H014", "%s: hivatkozáskövetési hiba (%s)" % (s, type(exc).__name__),
                                          "%s: citation search error (%s)" % (s, type(exc).__name__), source=s))
                    status = "partial"
                    continue
                retrieved += len(items)
                for meta in items:
                    if dr == "forward" and since_year and meta.get("year") and int(meta["year"]) < since_year:
                        continue
                    rec = make_record(meta, s, via, sid, at, route="citation_search")
                    if rec:
                        hits.append(rec)
                        count += 1
            entries.append({"search_id": sid, "direction": dr, "seeds": [p for _k, _o, p in seed_list],
                            "source": s, "status": status, "count": count, "retrieved": retrieved,
                            "since_year": since_year if dr == "forward" else None, "iterations": 1,
                            "seed_kind": seeds, "run_at": at})
            searches.append({"search_id": sid, "purpose": "citation_%s" % dr, "source": s,
                             "platform": _PLATFORM.get(s), "query": "%s %s: %d mag (PMID)" % (
                                 s, "citations" if dr == "forward" else "references", len(seed_list)),
                             "filters": {"since_year": since_year} if (dr == "forward" and since_year) else None,
                             "date_field": "pubYear" if dr == "forward" and since_year else None,
                             "date_from": ("%d" % since_year) if dr == "forward" and since_year else None,
                             "date_to": None, "run_at": at, "count_total": retrieved, "count_retrieved": retrieved,
                             "complete": status == "done", "export": None,
                             "note": "TARCiS: irány=%s, magok=%s, iteráció=1" % (dr, seeds)})
            if status != "done":
                warnings.append(_warn("H012", "%s: a hivatkozáskövetés nem teljes (%s)." % (s, status),
                                      "%s: citation search incomplete (%s)." % (s, status), source=s))
    uniq, dup_within = dedupe_hits(hits)
    with S.lock(project_dir):
        prior = S.read_json(S.path(project_dir, S.FILES["studies"])) or {}
        records, stat = integrate(prior.get("records") or [], uniq, "citation_search")
        d = _d()
        doc = d.link(records, S.load_reviews(project_dir), S.read_decisions(project_dir), prior=prior, now=now)
        S.write_json_atomic(S.path(project_dir, S.FILES["studies"]), doc)
        upd = S.read_json(S.path(project_dir, S.FILES["update"])) or {}
        if not upd:
            upd = {"schema": UPDATE_SCHEMA, "model": S.MODEL, "generated": at,
                   "window": {"latest_source_search": None, "overlap_months": 0,
                              "start_date": str(since)[:10] if since else at[:10], "end_date": at[:10],
                              "anchor": "manual", "per_review": [], "decision_id": None},
                   "queries": [], "results": {"retrieved_total": 0, "duplicates_within": 0, "already_known": 0,
                                              "new_records": [], "registry_records": []}}
        upd["citation_search"] = entries
        upd["citation_results"] = {"retrieved": len(hits) + dup_within, "duplicates_within": dup_within,
                                   "already_known": stat["already_known"] + stat["known_other_route"],
                                   "new_records": stat["new_records"]}
        upd["generated"] = at
        errs = S.schema_errors(upd, "update-search")
        if errs:
            raise UpdateError("Az update_search.json nem felel meg a sémának (H001): %s" % "; ".join(errs[:5]))
        S.write_json_atomic(S.path(project_dir, S.FILES["update"]), upd)
        st = S.load_state(project_dir)
        S.add_searches(st, searches)
        pend = (doc.get("summary") or {}).get("pending", 0)
        S.set_step(st, "update_search", "needs_human" if (stat["new_records"] or pend) else "done",
                   run_id=run.run_id if run is not None else None, now=now, stale_downstream=True)
        if stat["new_records"]:
            S.set_checkpoint(st, "EP4", "pending", open_items=len(stat["new_records"]))
        S.save_state(project_dir, st, env=env, now=now)
    down = any(w["code"] == "H014" for w in warnings)
    pending = []
    if pend:
        pending.append({"checkpoint": "EP3", "n": pend})
    if stat["new_records"]:
        pending.append({"checkpoint": "EP4", "n": len(stat["new_records"])})
    return {"ok": True, "data": {"citation_search": entries, "new_records": stat["new_records"],
                                 "already_known": stat["already_known"] + stat["known_other_route"],
                                 "duplicates_within": dup_within},
            "warnings": warnings, "errors": [], "pending": pending,
            "exit_code": 3 if down else (4 if pending else 0), "next": "merge"}
