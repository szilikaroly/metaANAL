# -*- coding: utf-8 -*-
"""Metaheadhunter — L4: a bevont-vizsgálat jelöltek feloldása azonosítókra és metaadatra
(TERV_metaheadhunter.md 7. fejezet).

Kezdőknek: az áttekintésekből kinyert tételek (jelöltek) csak hivatkozás-szövegek. Ez a lépés minden jelölthöz
megkeresi a valódi közleményt az adatbázisokban (PubMed, Europe PMC, opcionálisan OpenAlex, Scopus, Crossref),
és CSAK akkor fogadja el, ha az adatbázis válasza egyezik a hivatkozással (cím-hasonlóság, első szerző, év).
Azonosítót soha nem talál ki: minden azonosító egy API-válaszból jön (``idval.source`` = az API neve), az
áttekintés saját azonosítója (``source: review``) vagy a felhasználó által beírt azonosító (``source: user``)
pedig csak API-megerősítéssel (``confirmed_by``) számít feloldottnak (N1, H003/H005).

**Sorrend jelöltenként** (az első elfogadott eredménynél megáll):

1. az áttekintés saját azonosítója → megerősítés (PMID → ``esummary``; DOI → ``esearch "<doi>"[doi]`` vagy
   Europe PMC ``DOI:"…"``; PMCID → Europe PMC). Elfogadás: cím-hasonlóság ≥ 0,80, vagy — ha a hivatkozásban
   nincs cím — első szerző + év egyezik;
2. ``ecitmatch`` (folyóirat|év|kötet|első oldal|szerző) — egyetlen PMID, utána ``esummary``-címellenőrzés;
3. PubMed ``esearch``: ``"<cím>"[ti]``, ha 0 találat: címszavak ``[ti]`` + ``<vezetéknév>[au]`` + ``<év>[dp]``;
4. Europe PMC: ``TITLE:"…" AND AUTH:"…" AND PUB_YEAR:…``;
5. OpenAlex ``title.search`` (listás lekérdezés — kredit!; csak ha a forrás engedélyezett);
6. Scopus ``TITLE("…") AND AUTHLASTNAME(…) AND PUBYEAR = …`` (csak kulccsal);
7. Crossref ``query.bibliographic`` (csak ha elérhető).

A 3–7. lépésnél az elfogadás feltétele: pontosan egy „erős" találat (cím ≥ 0,90, és nincs ellentmondó első
szerző vagy > 1 év eltérés), a többi találat < 0,80. (Build-döntés a TERV „egyetlen találat" szabályának
pontosítására: a PubMed egyetlen találata ennek speciális esete.) Több jó találat vagy küszöb alatti
hasonlóság → ``resolution`` javaslat (``proposals[]``, EP3) a talált lehetőségekkel (``options``); sikertelen
feloldás → ``rec-x-…`` rekord ``resolution.status: unresolved``.

**Metaadat és regiszter-kapcsolat** (PMID-del feloldott rekordokra): PubMed ``efetch`` (``DataBankList`` →
*erős* regiszter-kapcsolat; ``CommentsCorrections`` → visszavonás/erratum/frissítés; publikációtípus →
visszavont, erratum, protokoll, kongresszusi absztrakt; MeSH → csak állatkísérlet jelzés); az absztraktot
CSAK memóriában nézzük (regiszterszám-minták), nem tároljuk (N4); Europe PMC annotációk (``NCT`` → *erős*);
ClinicalTrials.gov ``AREA[ReferencePMID]`` (``RESULT``/``DERIVED`` → *megerősítő*, ``BACKGROUND`` → *gyenge*,
önmagában nem kapcsol — TERV 3.1, Tameris 2013 / NCT00953927 / NCT04975178 regresszió). Az áttekintés által
közölt NCT-számot a CT.gov ``studies/{NCT}`` erősíti meg (``review`` erősség).

**Emberi döntés** (``id_confirm``, EP3): a ``resolution`` javaslatra vagy a jelöltre (``<review_id>#<cand_id>``)
``option:N`` (a javaslat N. lehetősége), ``pmid:…`` / ``doi:…`` / ``pmcid:…`` / ``nct:…`` / ``eid:…`` (a
felhasználó adja — API-val megerősítjük, ``source: user`` + ``confirmed_by``) vagy ``reject`` (feloldatlan marad).

Nyilvános API::

    res = Resolver(clients={...} | http=HttpClient, use=["pubmed", "europepmc", …]).run(reviews, prior_doc, decisions)
    res["records"], res["proposals"], res["links"], res["confirmations"], res["warnings"], res["sources"], res["stats"]
    run_resolve(project_dir, sources=None, offline=False, http=None, clients=None, now=None, force=False)
    apply_links_to_reviews(reviews, links, confirmations)   # jelölt.rec_id + a review-azonosítók confirmed_by-ja
    parse_citation(text), collect_items(reviews, …)
"""
from __future__ import absolute_import

import copy
import json
import os
import re

from . import dedup as _d

ACCEPT_ID = 0.80          # 1–2. lépés: az áttekintés azonosítója / ecitmatch → cím-ellenőrzés
ACCEPT_SEARCH = 0.90      # 3–7. lépés: cím-keresés
OPTION_FLOOR = 0.60       # ennél gyengébb találatot lehetőségként sem mutatunk
STRONG_TITLE = 0.95       # azonosító-ellenőrzésnél: (majdnem) szó szerinti cím → szerző-alak/év eltérés tűrhető
MAX_HITS = 5
TOOL_ACTOR = _d.TOOL_ACTOR
RESOLUTION_SOURCES = ("pubmed", "europepmc", "openalex", "scopus", "crossref", "ctgov")
DEFAULT_USE = ("pubmed", "europepmc", "ctgov")
METHODS = ("pub-id", "ecitmatch", "esearch-title", "europepmc-title", "openalex-title", "scopus-title",
           "crossref-title", "registry-record", "human-choice")
KB_RESOLVE = ["D-S03-103"]
#: melyik forrás erősíthet meg egy azonosító-fajtát (emberi id_confirm)
_ID_SOURCES = {"pmid": ("pubmed", "europepmc"), "doi": ("pubmed", "europepmc", "crossref"), "pmcid": ("europepmc",),
               "openalex": ("openalex",), "eid": ("scopus",), "nct": ("ctgov",)}

_REG_PATTERNS = (
    ("nct", re.compile(r"^NCT\d{8}$")),
    ("isrctn", re.compile(r"^ISRCTN\d{8}$")),
    ("actrn", re.compile(r"^ACTRN\d{14}$")),
    ("chictr", re.compile(r"^ChiCTR[-A-Za-z0-9]+$")),
    ("eudract", re.compile(r"^\d{4}-\d{6}-\d{2}$")),
    ("irct", re.compile(r"^IRCT\d+N\d+$")),
    ("ctri", re.compile(r"^CTRI/\d{4}/\d{2,3}/\d{6}$")),
    ("drks", re.compile(r"^DRKS\d{8}$")),
    ("pactr", re.compile(r"^PACTR\d{15}$")),
    ("kct", re.compile(r"^KCT\d{7}$")),
    ("umin", re.compile(r"^UMIN\d{9}$")),
    ("ntr", re.compile(r"^NTR\d+$")),
)

__all__ = ["Resolver", "run_resolve", "apply_links_to_reviews", "enrich_records", "parse_citation", "collect_items",
           "registry_kind", "ACCEPT_ID", "ACCEPT_SEARCH"]


def _expl(hu, en):
    return {"hu": hu, "en": en}


def _warn(code, hu, en, **detail):
    w = {"code": code, "hu": hu, "en": en}
    if detail:
        w["detail"] = detail
    return w


def registry_kind(value):
    """Regiszter-azonosító fajtája (``nct``, ``isrctn``, …) vagy ``None`` (nem regiszter, pl. GenBank)."""
    v = str(value or "").strip()
    for kind, rx in _REG_PATTERNS:
        if rx.match(v):
            return kind
    return None


# =============================================================================================
# hivatkozás-elemzés
# =============================================================================================

_ANCHOR = re.compile(
    r"(?P<year>(?:1[89]|20)\d{2})[a-z]?(?:\s+[A-Z][a-z]{2}(?:\s+\d{1,2})?)?\s*;\s*(?P<volume>[A-Za-z]?\d+[A-Za-z]?)"
    r"(?:\s*\((?P<issue>[^)]{1,20})\))?\s*:\s*(?P<first>[A-Za-z]?\d+)(?:\s*[-\u2013]\s*(?P<last>[A-Za-z]?\d+))?")
_SPLIT = re.compile(r"(?<=[A-Za-z0-9\)\]])\.\s+|(?<=[?!])\s+")
_AUTHORS_LIKE = re.compile(r"\bet\.?\s*al\b|^[A-Z][\w'\u2019\-]+(?:\s[A-Z][\w'\u2019\-]+)?\s[A-Z]{1,3}(?:,|$)")


def parse_citation(text):
    """Vancouver-szerű hivatkozás (``Szerzők. Cím. Folyóirat Év;Kötet(Szám):Oldal-Oldal``) →
    ``{journal, year, volume, issue, first_page, last_page, title, authors}`` (a hiányzó mező ``None``).
    Konzervatív: bizonytalan alaknál inkább ``None``, mint rossz érték (ecitmatch és cím-keresés bemenete; a
    találatot úgyis cím/szerző/év-ellenőrzés fogadja el)."""
    out = {"journal": None, "year": None, "volume": None, "issue": None, "first_page": None, "last_page": None,
           "title": None, "authors": None}
    if not text:
        return out
    s = re.sub(r"\s+", " ", str(text)).strip()
    s = re.sub(r"(?:https?://|doi:)\S+", " ", s, flags=re.I)
    m = None
    for m in _ANCHOR.finditer(s):
        pass
    if not m:
        return out
    head = s[:m.start()].strip().rstrip(".").strip()
    parts = [p.strip() for p in _SPLIT.split(head) if p and p.strip()]
    if not parts:
        return out
    journal = parts[-1].strip(" .,;")
    if not journal or len(journal) > 80 or not re.search(r"[A-Za-z]", journal):
        return out
    out["journal"] = journal
    out["year"] = int(m.group("year"))
    out["volume"] = m.group("volume")
    out["issue"] = m.group("issue")
    out["first_page"] = m.group("first")
    out["last_page"] = m.group("last")
    rest = parts[:-1]
    if rest and _AUTHORS_LIKE.search(rest[0]):
        out["authors"] = rest[0]
        rest = rest[1:]
    if rest:
        cand = ". ".join(rest).strip(" .")
        if len(cand) >= 15 and len(cand.split()) >= 3:
            out["title"] = cand
    return out


def _first_author_from_text(text):
    """Első szerző egy hivatkozás-szövegből ('Tameris MD, Hatherill M, …' → 'Tameris MD'); bizonytalanul None."""
    if not text:
        return None
    try:
        from . import jats as _j
        fa = _j.first_author_from_text(text)
        if fa:
            return fa
    except Exception:  # pragma: no cover - a JATS-modul nélkül is működjön
        pass
    m = re.match(r"^\s*([A-Z][\w'\u2019\-]+(?:\s[a-z]{1,3}\s[A-Z][\w'\u2019\-]+)?)\s+[A-Z]{1,3}\b", str(text))
    return m.group(1) if m else None


def _clean_title(t):
    t = re.sub(r"[\[\]{}\"]", " ", str(t or ""))
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t


# =============================================================================================
# munkatételek
# =============================================================================================

class _Item(object):
    """Egy feloldandó jelölt (``<review_id>#<cand_id>``)."""

    def __init__(self, review, cand):
        self.review_id = review["review_id"]
        self.cand_id = cand.get("cand_id")
        self.ref = "%s#%s" % (self.review_id, self.cand_id)
        self.cand = cand
        ca = cand.get("cited_as") or {}
        self.text = ca.get("text") or ""
        parsed = parse_citation(self.text)
        self.title = _clean_title(ca.get("title") or parsed["title"]) or None
        fa = ca.get("first_author") or (_first_author_from_text(self.text) if parsed["authors"] else None)
        if not fa and cand.get("study_label_in_review"):
            m = re.match(r"^\s*([^\d(]+?)\s+(?:et al\.?\s*)?\(?((?:1[89]|20)\d{2})", cand["study_label_in_review"])
            fa = m.group(1) if m else None
        self.first_author = fa
        self.surname = _d.surname_display(fa) if fa else None
        y = ca.get("year") or parsed["year"]
        try:
            self.year = int(y) if y else None
        except (TypeError, ValueError):
            self.year = None
        self.journal = ca.get("journal") or parsed["journal"]
        self.volume = parsed["volume"]
        self.first_page = parsed["first_page"]
        ids = cand.get("ids") or {}
        self.review_ids = {}
        for k in ("pmid", "doi", "pmcid", "nct", "eid", "openalex"):
            iv = ids.get(k)
            if isinstance(iv, dict) and iv.get("value"):
                v = _d.norm_id(k, iv["value"])
                if v:
                    self.review_ids[k] = v
        self.review_registry = []
        for iv in list(ids.get("registry") or []) + list(cand.get("study_registry_in_review") or []):
            if isinstance(iv, dict) and iv.get("value"):
                self.review_registry.append(str(iv["value"]).strip())
        if self.review_ids.get("nct") and self.review_ids["nct"] not in self.review_registry:
            self.review_registry.insert(0, self.review_ids["nct"])
        self.result = None          # {"meta", "source", "via", "method", "score"}
        self.status = None          # resolved | ambiguous | unresolved
        self.options = []
        self.notes = []
        self.human = None           # id_confirm döntés

    @property
    def bib(self):
        return {"title": self.title, "first_author": self.first_author, "year": self.year, "journal": self.journal}


def collect_items(reviews, roles=_d.INCLUDED_ROLES, include_unknown=False, selected_only=True):
    """A feloldandó jelöltek: a kiválasztott áttekintések nem elutasított, bevonás-szerepű jelöltjei
    (``include_unknown``: az irodalomjegyzékből jött, még osztályozatlan ``unknown`` szerepűek is)."""
    revs = _d._selected_reviews(reviews)[0] if selected_only else list(reviews)
    wanted = set(roles) | (set(["unknown"]) if include_unknown else set())
    out = []
    for r in sorted(revs, key=lambda x: x["review_id"]):
        for c in sorted(r.get("candidates") or [], key=lambda x: x.get("cand_id") or ""):
            if c.get("status") == "rejected" or c.get("role_in_review") not in wanted:
                continue
            out.append(_Item(r, c))
    return out


# =============================================================================================
# metaadat-normalizálás forrásonként (absztrakt SOHA nem kerül a rekordba — N4)
# =============================================================================================

_META_KEYS = ("pmid", "pmcid", "doi", "openalex", "eid", "nct", "title", "authors", "authors_truncated",
              "first_author", "last_author", "year", "journal", "journal_full", "volume", "issue", "pages",
              "pub_types", "language", "issn", "essn", "retracted", "preprint", "unverified_live")


def _meta(d, **extra):
    m = dict((k, d.get(k)) for k in _META_KEYS if d.get(k) not in (None, "", []))
    m.update(dict((k, v) for k, v in extra.items() if v not in (None, "", [])))
    for k in ("pmid", "pmcid", "doi", "openalex", "eid", "nct"):
        if m.get(k):
            m[k] = _d.norm_id(k, m[k])
            if not m[k]:
                m.pop(k)
    return m


def _epmc_meta(hit):
    try:
        from . import europepmc as _e
        r = _e.record(hit) if "pubYear" in hit or "source" in hit or "authorString" in hit else hit
    except Exception:  # pragma: no cover - a modul hiánya esetén
        r = hit
    m = _meta(r)
    if (hit.get("source") == "PPR") or ("Preprint" in (m.get("pub_types") or [])):
        m["preprint"] = True
    return m


def _oa_meta(w):
    try:
        from . import openalex as _o
        r = _o.work_record(w) if "display_name" in w or "authorships" in w else w
    except Exception:  # pragma: no cover
        r = w
    m = _meta(r)
    if r.get("type"):
        m["pub_types"] = [r["type"]]
    if r.get("is_retracted"):
        m["retracted"] = True
    return m


def _scopus_meta(e):
    try:
        from . import scopus as _s
        r = _s.entry_record(e) if ("dc:title" in e or "eid" in e and "title" not in e) else e
    except Exception:  # pragma: no cover
        r = e
    m = _meta(r)
    if r.get("doctype_desc"):
        m["pub_types"] = [r["doctype_desc"]]
    if r.get("unverified_live"):
        m["unverified_live"] = True
    return m


def _accept(item, meta, threshold):
    """Elfogadás-ellenőrzés. Visszaad: ``(elfogadható, cím-hasonlóság, jellemzők)``."""
    f = _d.compare_bib(item.bib, {"title": meta.get("title"), "first_author": meta.get("first_author"),
                                  "year": meta.get("year"), "journal": meta.get("journal")})
    ts = f["title_sim"]
    fam = f["first_author_match"]
    yd = f["year_diff"]
    if ts is not None:
        ok = ts >= threshold and fam is not False and (yd is None or yd <= 1)
        if not ok and threshold <= ACCEPT_ID and ts >= STRONG_TITLE:
            # Az azonosító a hivatkozásból/API-ból jött, és a cím szó szerint egyezik — csak a szerző ALAKJA vagy az
            # ÉV tér el. Élő próba (BCG): OpenAlex régi rekordjainál a megjelenési év gyakran a digitalizálás éve
            # (33–55 év eltérés a PubMedhez képest), a szerzőnév pedig „Vezetéknév Kezdőbetűk"/fordított alakú.
            if yd is None or yd <= 1:
                ok = True
                _note(item, "accepted_author_form_differs")
            elif fam is True:
                ok = True
                _note(item, "accepted_year_differs")
    else:
        ok = fam is True and yd == 0
    return ok, ts, f


def _note(item, text):
    notes = getattr(item, "notes", None)
    if isinstance(notes, list) and text not in notes:
        notes.append(text)


def _option(meta, source, via, score, feats):
    ids = dict((k, meta[k]) for k in ("pmid", "doi", "pmcid", "openalex", "eid", "nct") if meta.get(k))
    try:
        rid = _d.rec_id_for(ids)
    except ValueError:
        rid = None
    return {"rec_id": rid, "ids": ids, "source": source, "via": via, "score": score,
            "bib": {"title": meta.get("title"), "first_author": meta.get("first_author"), "year": meta.get("year"),
                    "journal": meta.get("journal")},
            "features": {"title_sim": feats.get("title_sim"), "first_author_match": feats.get("first_author_match"),
                         "year_diff": feats.get("year_diff")}}


# =============================================================================================
# forrás-kezelés (kíméletes leromlás)
# =============================================================================================

class _Sources(object):
    """A lépés forrásai: kliensek lusta létrehozása, elérhetetlenség jelzése (a forrás a futás hátralevő részében
    kimarad — H014), és a hibák kezdőbarát üzenete."""

    def __init__(self, clients=None, http=None, use=None, cfg=None, env=None):
        self._clients = dict(clients or {})
        self.http = http
        self.use = list(use) if use is not None else [s for s in DEFAULT_USE]
        if clients and use is None:
            self.use = [s for s in RESOLUTION_SOURCES if s in self._clients]
        self.cfg = cfg or {}
        self.env = env
        self.status = dict((s, {"status": "ok", "message": None}) for s in self.use)
        self.warnings = []
        self.calls = {}

    def client(self, name):
        if name not in self.use or self.status.get(name, {}).get("status") != "ok":
            return None
        if name not in self._clients:
            try:
                from . import sources as _src
                self._clients[name] = _src.make_client(name, http=self.http, cfg=self.cfg.get(name), env=self.env)
            except Exception as exc:  # a kliensmodul nem érhető el
                self._down(name, "unreachable", _expl("A(z) %s kliens nem tölthető be: %s" % (name, type(exc).__name__),
                                                      "The %s client cannot be loaded: %s" % (name, type(exc).__name__)))
                return None
        return self._clients[name]

    def _down(self, name, status, message, reset_at=None):
        if self.status.get(name, {}).get("status") == "ok":
            self.status[name] = {"status": status, "message": message, "reset_at": reset_at}
            self.warnings.append(_warn(
                "H014", "Forrás nem érhető el (%s: %s) — a feloldás nélküle folytatódik, az eredmény részleges."
                % (name, status),
                "Source unavailable (%s: %s) — resolution continues without it; the result is partial." % (name, status),
                source=name, status=status))

    def call(self, name, fn, *args, **kwargs):
        """``(eredmény, True)`` vagy ``(None, False)``; a ``SourceUnavailable`` a forrást a futásra kikapcsolja."""
        self.calls[name] = self.calls.get(name, 0) + 1
        try:
            from . import net as _net
            unavailable = _net.SourceUnavailable
            soft = (_net.HttpError, _net.ParseError)
        except Exception:  # pragma: no cover
            unavailable, soft = (), ()
        try:
            return fn(*args, **kwargs), True
        except Exception as exc:
            if unavailable and isinstance(exc, unavailable):
                expl = getattr(exc, "explain", None) or _expl(str(exc)[:200], str(exc)[:200])
                self._down(name, getattr(exc, "status", "unreachable") or "unreachable", expl,
                           getattr(exc, "reset_at", None))
                return None, False
            if soft and isinstance(exc, soft):
                self.warnings.append(_warn("source_error", "Váratlan válasz a(z) %s forrástól (%s); ez a lekérés kimarad."
                                           % (name, type(exc).__name__),
                                           "Unexpected response from %s (%s); this request is skipped."
                                           % (name, type(exc).__name__), source=name))
                return None, False
            if isinstance(exc, (AttributeError, TypeError, KeyError, IndexError, ValueError)):
                self.warnings.append(_warn("shape_error", "Váratlan válaszszerkezet (%s, %s) — a lekérés kimarad."
                                           % (name, type(exc).__name__),
                                           "Unexpected response structure (%s, %s) — request skipped."
                                           % (name, type(exc).__name__), source=name, error=type(exc).__name__))
                return None, False
            raise


# =============================================================================================
# Resolver
# =============================================================================================

class Resolver(object):
    """L4 feloldó. ``clients``: ``{forrás: kliens}`` (tesztben hamis kliensek), vagy ``http`` (HttpClient) +
    ``use`` (a lépés forrásai; ``sources.select_sources``). ``now``: rögzített idő (determinizmus)."""

    def __init__(self, clients=None, http=None, use=None, cfg=None, env=None, now=None, enrich=True,
                 epmc_annotations=True, ctgov_links=True, max_hits=MAX_HITS, force=False):
        self.src = _Sources(clients=clients, http=http, use=use, cfg=cfg, env=env)
        self.at = _d.utc_now(now)
        self.enrich = enrich
        self.epmc_annotations = epmc_annotations
        self.ctgov_links = ctgov_links
        self.max_hits = int(max_hits)
        self.force = force
        self.warnings = []

    # -- segédek ---------------------------------------------------------------------------------

    def _resolve(self, item, meta, source, via, method, score, retrieval=None):
        item.result = {"meta": meta, "source": source, "via": via, "method": method, "score": score,
                       "retrieval": retrieval}
        item.status = "resolved"

    def _consider(self, item, metas, source, via, method, threshold, single_required=False):
        """Lista-találatok mérlegelése: pontosan egy erős találat → elfogadás; különben lehetőségek."""
        scored = []
        for meta in metas:
            ok, ts, f = _accept(item, meta, threshold)
            scored.append((ok, ts, f, meta))
        strong = [x for x in scored if x[0]]
        others_hi = [x for x in scored if not x[0] and (x[1] or 0) >= ACCEPT_ID]
        if len(strong) == 1 and not others_hi and (not single_required or len(scored) == 1):
            ok, ts, f, meta = strong[0]
            self._resolve(item, meta, source, via, method, ts, meta.get("_retrieval"))
            return True
        for ok, ts, f, meta in scored:
            if ts is None and f.get("first_author_match") is not True:
                continue
            if ts is not None and ts < OPTION_FLOOR:
                continue
            opt = _option(meta, source, via, ts, f)
            if opt["rec_id"] and opt["rec_id"] not in [o["rec_id"] for o in item.options]:
                item.options.append(opt)
        return False

    # -- 0. emberi döntések ------------------------------------------------------------------------

    def _human_pass(self, items, decisions, prior_doc):
        prior_props = dict((p.get("proposal_id"), p) for p in (prior_doc or {}).get("proposals") or []
                           if p.get("kind") == "resolution")
        by_ref = {}
        eff = _d.effective_decisions(decisions or [], kinds=("id_confirm",))
        for (ttype, tid), d in sorted(eff.items(), key=lambda kv: kv[1]["_order"]):
            if ttype == "proposal" and tid in prior_props:
                ref = (prior_props[tid].get("items") or [None])[0]
                by_ref[ref] = (d, prior_props[tid])
            elif ttype == "candidate":
                by_ref[tid] = (d, None)
        for it in items:
            if it.ref not in by_ref:
                continue
            d, prop = by_ref[it.ref]
            if not str(d.get("actor") or "").startswith("user:"):
                self.warnings.append(_warn("non_human_decision", "Az id_confirm döntést nem ember hozta (%s) — "
                                           "figyelmen kívül hagyva." % d.get("actor"),
                                           "The id_confirm decision was not made by a human (%s) — ignored."
                                           % d.get("actor"), decision_id=d.get("decision_id")))
                continue
            it.human = d
            val = str(d.get("value") or "").strip()
            low = val.lower()
            if low in ("reject", "none", "unresolvable", "nincs"):
                it.status = "unresolved"
                it.notes.append("human_rejected")
                continue
            kind, ident, option = None, None, None
            m = re.match(r"^option:(\d+)$", low)
            if m and prop:
                opts = prop.get("options") or []
                n = int(m.group(1))
                if 1 <= n <= len(opts):
                    option = opts[n - 1]
                    for k in ("pmid", "doi", "pmcid", "eid", "openalex", "nct"):
                        if (option.get("ids") or {}).get(k):
                            kind, ident = k, option["ids"][k]
                            break
            else:
                m = re.match(r"^(pmid|doi|pmcid|nct|eid|openalex)\s*:\s*(.+)$", val, re.I)
                if m:
                    kind, ident = m.group(1).lower(), _d.norm_id(m.group(1).lower(), m.group(2))
                    # ha a beírt azonosító a javaslat egyik (API-ból jött) lehetősége, az API-proveniencia marad
                    for o in (prop or {}).get("options") or []:
                        if ident and (o.get("ids") or {}).get(kind) == ident:
                            option = o
                            break
            if not kind or not ident:
                self.warnings.append(_warn("H005", "Érvénytelen id_confirm érték (%s) — a jelölt feloldatlan marad."
                                           % val, "Invalid id_confirm value (%s) — the candidate stays unresolved." % val,
                                           ref=it.ref, decision_id=d.get("decision_id")))
                it.status = "unresolved"
                it.notes.append("human_value_invalid")
                continue
            meta, source, via = self._fetch_by_id(kind, ident)
            if not meta:
                usable = [s for s in _ID_SOURCES.get(kind, ()) if self.src.client(s) is not None]
                if not usable:
                    self.warnings.append(_warn(
                        "H014", "A megadott azonosító (%s:%s) most nem ellenőrizhető: a hozzá szükséges forrás nem "
                                "érhető el vagy nincs beállítva (%s). Később futtasd újra a feloldást."
                        % (kind, ident, ", ".join(_ID_SOURCES.get(kind, ()))),
                        "The given identifier (%s:%s) cannot be verified now: the required source is unavailable or "
                        "not configured (%s). Re-run resolve later." % (kind, ident, ", ".join(_ID_SOURCES.get(kind, ()))),
                        ref=it.ref, decision_id=d.get("decision_id")))
                    it.notes.append("human_id_unverifiable")
                else:
                    self.warnings.append(_warn(
                        "H005", "A megadott azonosítót (%s:%s) egyik elérhető API sem ismeri — nem fogadható el."
                        % (kind, ident),
                        "No available API knows the given identifier (%s:%s) — it cannot be accepted." % (kind, ident),
                        ref=it.ref, decision_id=d.get("decision_id")))
                    it.notes.append("human_id_not_found")
                it.status = "unresolved"
                continue
            ok, ts, f = _accept(it, meta, ACCEPT_ID)
            if not ok:
                # az ember döntése érvényes (N3), de szólunk: elírt PMID/DOI esetén a hivatkozás egy MÁSIK közleményhez
                # kötődne (felülvizsgálat: korábban figyelmeztetés nélkül fogadtuk el)
                it.notes.append("human_choice_bib_mismatch")
                self.warnings.append(_warn(
                    "id_title_mismatch",
                    "%s: a megadott azonosító (%s:%s) közleménye nem egyezik a hivatkozással (cím-hasonlóság %s, első "
                    "szerző %s, évkülönbség %s: „%s”). A döntésedet rögzítettük — ha elírás, adj új döntést "
                    "(decide … --value pmid:<helyes>)."
                    % (it.ref, kind, ident, "—" if ts is None else "%.2f" % ts,
                       {True: "egyezik", False: "eltér", None: "ismeretlen"}[f.get("first_author_match")],
                       "—" if f.get("year_diff") is None else f.get("year_diff"), (meta.get("title") or "")[:120]),
                    "%s: the record of the given identifier (%s:%s) does not match the citation (title similarity %s). "
                    "Your decision was recorded — if it is a typo, decide again." % (it.ref, kind, ident,
                                                                                  "—" if ts is None else "%.2f" % ts),
                    ref=it.ref, decision_id=d.get("decision_id")))
            meta = dict(meta)
            meta["_human"] = {"kind": kind, "value": ident, "decision_id": d.get("decision_id"),
                              "from_option": bool(option), "option_source": (option or {}).get("source"),
                              "option_via": (option or {}).get("via")}
            self._resolve(it, meta, source, via, "human-choice", ts, meta.get("_retrieval"))

    def _fetch_by_id(self, kind, ident):
        """Egy azonosító API-megerősítése. Visszaad: ``(meta|None, forrás, végpont)``."""
        if kind == "pmid":
            pm = self.src.client("pubmed")
            if pm:
                recs, ok = self.src.call("pubmed", pm.esummary, [ident])
                for r in recs or []:
                    if r.get("pmid") == ident:
                        return _meta(r, _retrieval=r.get("retrieval")), "pubmed", "pubmed.esummary"
            ep = self.src.client("europepmc")
            if ep:
                hit, ok = self.src.call("europepmc", ep.lookup_pmid, ident, "lite")
                if hit:
                    return _epmc_meta(hit), "europepmc", "europepmc.search"
        elif kind == "doi":
            got = self._dois_pubmed([ident]).get(ident)
            if got:
                return got, "pubmed", "pubmed.esearch+esummary"
            ep = self.src.client("europepmc")
            if ep:
                hit, ok = self.src.call("europepmc", ep.lookup_doi, ident, "lite")
                if hit:
                    return _epmc_meta(hit), "europepmc", "europepmc.search"
            cr = self.src.client("crossref")
            if cr:
                rec, ok = self.src.call("crossref", cr.work, ident)
                if rec:
                    return _meta(rec), "crossref", "crossref.works"
        elif kind == "pmcid":
            ep = self.src.client("europepmc")
            if ep:
                hit, ok = self.src.call("europepmc", ep.lookup_pmcid, ident, "lite")
                if hit:
                    return _epmc_meta(hit), "europepmc", "europepmc.search"
        elif kind == "openalex":
            oa = self.src.client("openalex")
            if oa:
                w, ok = self.src.call("openalex", oa.work, ident)
                if w:
                    return _oa_meta(w), "openalex", "openalex.work"
        elif kind == "eid":
            sc = self.src.client("scopus")
            if sc:
                ab, ok = self.src.call("scopus", sc.abstract, ident, "eid")
                if ab:
                    return _meta(ab, unverified_live=ab.get("unverified_live")), "scopus", "scopus.abstract"
        elif kind == "nct":
            return self._fetch_nct(ident)
        return None, None, None

    def _fetch_nct(self, nct):
        ct = self.src.client("ctgov")
        if not ct:
            return None, None, None
        st, ok = self.src.call("ctgov", ct.study, nct, "protocolSection")
        if not st:
            return None, None, None
        try:
            from . import ctgov as _c
            r = _c.study_record(st)
        except Exception:  # pragma: no cover
            r = {"nct": nct}
        if _d.norm_id("nct", r.get("nct")) != nct:
            return None, None, None
        title = r.get("official_title") or r.get("brief_title")
        y = None
        m = re.match(r"^(\d{4})", str(r.get("first_post_date") or r.get("start_date") or ""))
        if m:
            y = int(m.group(1))
        meta = {"nct": nct, "title": title, "year": y, "pub_types": ["Registry record"], "registry_record": True}
        if r.get("lead_sponsor"):
            meta["journal"] = "ClinicalTrials.gov"
        return meta, "ctgov", "ctgov.study"

    def _dois_pubmed(self, dois):
        """DOI-k → PubMed-metaadat (``"d1"[doi] OR "d2"[doi]`` kötegben, majd ``esummary``; a DOI-egyezést a
        visszakapott rekord DOI-ja igazolja). Visszaad: ``{doi: meta}``."""
        pm = self.src.client("pubmed")
        out = {}
        if not pm or not dois:
            return out
        dois = sorted(set(dois))
        for i in range(0, len(dois), 20):
            chunk = dois[i:i + 20]
            term = " OR ".join('"%s"[doi]' % d for d in chunk)
            res, ok = self.src.call("pubmed", pm.esearch, term, retmax=len(chunk) * 2 + 5)
            if not res or not res.get("ids"):
                continue
            recs, ok = self.src.call("pubmed", pm.esummary, res["ids"])
            for r in recs or []:
                d = _d.norm_id("doi", r.get("doi"))
                if d in chunk and d not in out:
                    out[d] = _meta(r, _retrieval=r.get("retrieval"))
        return out

    # -- 1. az áttekintés saját azonosítói ---------------------------------------------------------

    def _pass_review_ids(self, items):
        todo = [it for it in items if it.status is None and any(k in it.review_ids for k in ("pmid", "doi", "pmcid"))]
        if not todo:
            return
        pm = self.src.client("pubmed")
        by_pmid = {}
        pmids = sorted(set(it.review_ids["pmid"] for it in todo if it.review_ids.get("pmid")))
        if pm and pmids:
            recs, ok = self.src.call("pubmed", pm.esummary, pmids)
            for r in recs or []:
                by_pmid[r.get("pmid")] = _meta(r, _retrieval=r.get("retrieval"))
            if not ok:
                pm = self.src.client("pubmed")  # elérhetetlenné vált → Europe PMC tartalék
            if ok:
                for p in pmids:
                    if p not in by_pmid:
                        for it in todo:
                            if it.review_ids.get("pmid") == p:
                                it.notes.append("review_pmid_not_found")
                                self.warnings.append(_warn(
                                    "H005", "Az áttekintés által adott PMID (%s) nem létezik a PubMedben." % p,
                                    "The PMID given by the review (%s) does not exist in PubMed." % p, ref=it.ref))
        if not pm and pmids:
            ep = self.src.client("europepmc")
            for p in pmids:
                if not ep:
                    break
                hit, ok = self.src.call("europepmc", ep.lookup_pmid, p, "lite")
                if hit:
                    by_pmid[p] = _epmc_meta(hit)
                    by_pmid[p]["_via"] = "europepmc.search"
        for it in todo:
            p = it.review_ids.get("pmid")
            if p and p in by_pmid and by_pmid[p].get("_via") == "europepmc.search":
                ok, ts, f = _accept(it, by_pmid[p], ACCEPT_ID)
                if ok:
                    self._resolve(it, by_pmid[p], "europepmc", "europepmc.search", "pub-id", ts)
                else:
                    it.notes.append("review_id_title_mismatch")
                    it.options.append(_option(by_pmid[p], "europepmc", "europepmc.search", ts, f))
                continue
            if p and p in by_pmid:
                ok, ts, f = _accept(it, by_pmid[p], ACCEPT_ID)
                if ok:
                    self._resolve(it, by_pmid[p], "pubmed", "pubmed.esummary", "pub-id", ts,
                                  by_pmid[p].get("_retrieval"))
                else:
                    it.notes.append("review_id_title_mismatch")
                    it.options.append(_option(by_pmid[p], "pubmed", "pubmed.esummary", ts, f))
        # DOI
        dtodo = [it for it in todo if it.status is None and it.review_ids.get("doi")]
        by_doi = self._dois_pubmed([it.review_ids["doi"] for it in dtodo])
        ep = self.src.client("europepmc")
        for it in dtodo:
            d = it.review_ids["doi"]
            meta, source, via = by_doi.get(d), "pubmed", "pubmed.esearch+esummary"
            if meta is None and ep:
                hit, ok = self.src.call("europepmc", ep.lookup_doi, d, "lite")
                if hit:
                    meta, source, via = _epmc_meta(hit), "europepmc", "europepmc.search"
            if meta is None:
                it.notes.append("review_doi_not_found")
                continue
            ok, ts, f = _accept(it, meta, ACCEPT_ID)
            if ok:
                self._resolve(it, meta, source, via, "pub-id", ts, meta.get("_retrieval"))
            else:
                it.notes.append("review_id_title_mismatch")
                it.options.append(_option(meta, source, via, ts, f))
        # PMCID
        for it in [x for x in todo if x.status is None and x.review_ids.get("pmcid")]:
            if not ep:
                break
            hit, ok = self.src.call("europepmc", ep.lookup_pmcid, it.review_ids["pmcid"], "lite")
            if not hit:
                it.notes.append("review_pmcid_not_found")
                continue
            meta = _epmc_meta(hit)
            ok, ts, f = _accept(it, meta, ACCEPT_ID)
            if ok:
                self._resolve(it, meta, "europepmc", "europepmc.search", "pub-id", ts)
            else:
                it.notes.append("review_id_title_mismatch")
                it.options.append(_option(meta, "europepmc", "europepmc.search", ts, f))

    # -- 1b. csak regiszterszám (regiszter-rekord) ---------------------------------------------------

    def _pass_registry_only(self, items):
        for it in items:
            if it.status is not None or not it.review_ids.get("nct"):
                continue
            if it.title or it.journal or any(k in it.review_ids for k in ("pmid", "doi", "pmcid")):
                continue
            meta, source, via = self._fetch_nct(it.review_ids["nct"])
            if meta:
                self._resolve(it, meta, source, via, "registry-record", None)

    # -- 2. ecitmatch -------------------------------------------------------------------------------

    def _pass_ecitmatch(self, items):
        pm = self.src.client("pubmed")
        todo = [it for it in items if it.status is None and it.journal and it.year and it.volume and it.first_page]
        if not pm or not todo:
            return
        rows = []
        keymap = {}
        for i, it in enumerate(todo):
            key = "k%d" % (i + 1)
            keymap[key] = it
            rows.append((it.journal, it.year, it.volume, it.first_page, (it.surname or "").lower(), key))
        res, ok = self.src.call("pubmed", pm.ecitmatch_detail, rows)
        if not res:
            return
        found = dict((k, v["pmid"]) for k, v in res.items() if v.get("status") == "found" and v.get("pmid"))
        if not found:
            return
        recs, ok = self.src.call("pubmed", pm.esummary, sorted(set(found.values())))
        by_pmid = dict((r.get("pmid"), _meta(r, _retrieval=r.get("retrieval"))) for r in recs or [])
        for key, pmid in sorted(found.items()):
            it = keymap.get(key)
            meta = by_pmid.get(pmid)
            if not it or not meta or it.status is not None:
                continue
            ok, ts, f = _accept(it, meta, ACCEPT_ID)
            if ok:
                self._resolve(it, meta, "pubmed", "pubmed.ecitmatch+esummary", "ecitmatch", ts, meta.get("_retrieval"))
            else:
                it.notes.append("ecitmatch_title_mismatch")
                it.options.append(_option(meta, "pubmed", "pubmed.ecitmatch+esummary", ts, f))

    # -- 3. PubMed cím-keresés ----------------------------------------------------------------------

    def _pass_esearch(self, items):
        pm = self.src.client("pubmed")
        if not pm:
            return
        for it in items:
            if it.status is not None or not it.title:
                continue
            pm = self.src.client("pubmed")
            if not pm:
                return
            t = it.title if len(it.title) <= 250 else it.title[:250].rsplit(" ", 1)[0]
            res, ok = self.src.call("pubmed", pm.esearch, '"%s"[ti]' % t, retmax=self.max_hits)
            if res is not None and res.get("count", 0) == 0:
                words = [w for w in _d.title_tokens(it.title) if len(w) >= 3 and not w.isdigit()][:8]
                if len(words) >= 3:
                    q = " AND ".join("%s[ti]" % w for w in words)
                    if it.surname:
                        q += ' AND "%s"[au]' % it.surname if " " in it.surname else " AND %s[au]" % it.surname
                    if it.year:
                        q += " AND %d[dp]" % it.year
                    res, ok = self.src.call("pubmed", pm.esearch, q, retmax=self.max_hits)
            if not res or not res.get("ids"):
                continue
            recs, ok = self.src.call("pubmed", pm.esummary, res["ids"][:self.max_hits])
            metas = [_meta(r, _retrieval=r.get("retrieval")) for r in recs or []]
            if res.get("count", 0) > len(metas):
                it.notes.append("esearch_many_hits")
            self._consider(it, metas, "pubmed", "pubmed.esearch+esummary", "esearch-title", ACCEPT_SEARCH)

    # -- 4. Europe PMC ------------------------------------------------------------------------------

    def _pass_europepmc(self, items):
        for it in items:
            if it.status is not None or not it.title:
                continue
            ep = self.src.client("europepmc")
            if not ep:
                return
            q = 'TITLE:"%s"' % it.title.replace('"', " ")
            if it.surname:
                q += ' AND AUTH:"%s"' % it.surname.replace('"', " ")
            if it.year:
                q += " AND PUB_YEAR:%d" % it.year
            pager, ok = self.src.call("europepmc", ep.search, q, result_type="lite", page_size=self.max_hits,
                                      max_results=self.max_hits)
            if not pager:
                continue
            hits, ok = self.src.call("europepmc", list, pager)
            metas = [_epmc_meta(h) for h in hits or []]
            metas = [m for m in metas if any(m.get(k) for k in ("pmid", "doi", "pmcid"))]
            if metas:
                self._consider(it, metas, "europepmc", "europepmc.search", "europepmc-title", ACCEPT_SEARCH)

    # -- 5–7. OpenAlex, Scopus, Crossref ------------------------------------------------------------

    def _pass_openalex(self, items):
        for it in items:
            if it.status is not None or not it.title:
                continue
            oa = self.src.client("openalex")
            if not oa:
                return
            works, ok = self.src.call("openalex", oa.search_title, it.title, it.year, self.max_hits)
            metas = [_oa_meta(w) for w in works or []]
            if metas:
                self._consider(it, metas, "openalex", "openalex.works", "openalex-title", ACCEPT_SEARCH)

    def _pass_scopus(self, items):
        try:
            from . import scopus as _s
        except Exception:  # pragma: no cover
            return
        for it in items:
            if it.status is not None or not it.title:
                continue
            sc = self.src.client("scopus")
            if not sc:
                return
            q = _s.title_query(it.title, it.surname, it.year)
            if not q:
                continue
            pager, ok = self.src.call("scopus", sc.search, q, count=self.max_hits, max_results=self.max_hits,
                                      normalize=True)
            if not pager:
                continue
            hits, ok = self.src.call("scopus", list, pager)
            metas = [_scopus_meta(h) for h in hits or []]
            if metas:
                self._consider(it, metas, "scopus", "scopus.search", "scopus-title", ACCEPT_SEARCH)

    def _pass_crossref(self, items):
        for it in items:
            if it.status is not None or not (it.title or it.text):
                continue
            cr = self.src.client("crossref")
            if not cr:
                return
            recs, ok = self.src.call("crossref", cr.search_bibliographic, it.text or it.title, self.max_hits)
            metas = [_meta(r) for r in recs or []]
            if metas and it.title:
                self._consider(it, metas, "crossref", "crossref.works", "crossref-title", ACCEPT_SEARCH)

    # -- dúsítás: regiszter, visszavonás, publikációtípus ----------------------------------------

    def _enrich_records(self, recs, only=None):
        """PMID-del feloldott, még nem dúsított rekordok: PubMed efetch, Europe PMC annotációk, CT.gov hivatkozások.
        ``only``: csak ezek a ``rec_id``-k (alapból a bányászott — áttekintésből jött — rekordok; a frissítő
        keresés ezreit nem kérdezzük le egyenként)."""
        todo = {}
        for rid, r in recs.items():
            p = ((r.get("ids") or {}).get("pmid") or {}).get("value")
            if p and _d.trusted(r["ids"]["pmid"]) and r.get("status") != "merged_into" and \
                    (self.force or not r.get("enrichment")) and \
                    (only is None or rid in only):
                todo[p] = rid
        if not todo:
            return
        via = []
        pm = self.src.client("pubmed")
        if pm:
            parsed, ok = self.src.call("pubmed", pm.efetch_records, sorted(todo))
            if ok:
                via.append("pubmed.efetch")
            for e in parsed or []:
                rid = todo.get(e.get("pmid"))
                if rid:
                    self._apply_efetch(recs[rid], e)
        if self.epmc_annotations:
            for p in sorted(todo):
                ep = self.src.client("europepmc")
                if not ep:
                    break
                ncts, ok = self.src.call("europepmc", ep.accession_numbers, "MED", p, "NCT")
                if ok and "europepmc.annotations" not in via:
                    via.append("europepmc.annotations")
                for n in ncts or []:
                    v = _d.norm_id("nct", n)
                    if v:
                        self._add_link(recs[todo[p]], v, "strong", "europepmc", "europepmc.annotations")
        if self.ctgov_links:
            for p in sorted(todo):
                ct = self.src.client("ctgov")
                if not ct:
                    break
                links, ok = self.src.call("ctgov", ct.studies_citing_pmid, p)
                if ok and "ctgov.references" not in via:
                    via.append("ctgov.references")
                for l in links or []:
                    v = _d.norm_id("nct", l.get("nct"))
                    if v:
                        self._add_link(recs[todo[p]], v, l.get("strength") or "weak", "ctgov", "ctgov.references",
                                       ref_type=l.get("type"))
        for p, rid in todo.items():
            if via:
                recs[rid]["enrichment"] = {"at": self.at, "via": list(via)}
            self._registry_ids_from_links(recs[rid])

    def _apply_efetch(self, rec, e):
        fl = rec.setdefault("flags", {})
        pts = [str(x) for x in e.get("pub_types") or []]
        b = rec.setdefault("bib", {})
        if pts:
            b["pub_types"] = sorted(set((b.get("pub_types") or []) + pts))
        if "Retracted Publication" in pts or e.get("retracted"):
            fl["retracted"] = True
        if "Published Erratum" in pts:
            fl["erratum"] = True
        if "Clinical Trial Protocol" in pts:
            fl["protocol"] = True
        if "Congress" in pts:
            fl["conference_abstract"] = True
        if "Preprint" in pts:
            fl["preprint"] = True
        mesh = set(e.get("mesh") or [])
        if "Animals" in mesh and "Humans" not in mesh:
            fl["animal_only"] = True
        if e.get("language") and not b.get("language"):
            b["language"] = e["language"]
        rel = rec.setdefault("related", [])
        tmap = {"RetractionIn": "retraction_in", "ErratumIn": "erratum_in", "UpdateIn": "update_in",
                "CommentIn": "comment_in"}
        for cc in e.get("comments_corrections") or []:
            t = tmap.get(cc.get("type"))
            if t and cc.get("pmid"):
                item = {"type": t, "rec_id": "rec-pmid-%s" % cc["pmid"]}
                if item not in rel and len(rel) < 30:
                    rel.append(item)
            elif cc.get("type") == "ErratumFor" and cc.get("pmid"):
                # az erratum nem önálló közlemény: a szülőhöz kötjük (TERV 8.2) — a dedupe L3-erratum javaslata
                rec["erratum_for"] = "rec-pmid-%s" % cc["pmid"]
        rel.sort(key=lambda x: (x["type"], x["rec_id"]))
        for db in e.get("databanks") or []:
            for acc in db.get("accessions") or []:
                kind = registry_kind(acc)
                if kind:
                    self._add_link(rec, acc if kind != "nct" else _d.norm_id("nct", acc), "strong", "pubmed",
                                   "pubmed.efetch.databank", databank=db.get("name"))
        for acc in e.get("registry_ids_from_abstract") or []:
            kind = registry_kind(acc)
            if kind:
                self._add_link(rec, acc if kind != "nct" else _d.norm_id("nct", acc), "strong", "pubmed",
                               "pubmed.efetch.abstract")

    def _add_link(self, rec, rid, strength, source, via, **extra):
        links = rec.setdefault("registry_links", [])
        for l in links:
            if l["id"] == rid and l["via"] == via:
                if _d.STRENGTH_RANK.get(strength, 0) > _d.STRENGTH_RANK.get(l["strength"], 0):
                    l["strength"] = strength
                return
        item = {"id": rid, "kind": registry_kind(rid) or "other", "strength": strength, "source": source, "via": via,
                "at": self.at}
        for k, v in extra.items():
            if v:
                item[k] = v
        links.append(item)
        links.sort(key=lambda l: (l["id"], l["via"]))

    def _registry_ids_from_links(self, rec):
        """``ids.nct`` / ``ids.registry``: csak *erős* (a közlemény saját nyilatkozata) vagy *megerősítő* (CT.gov
        RESULT/DERIVED) kapcsolat; a gyenge (BACKGROUND) és az áttekintés-eredetű csak a ``registry_links``-ben."""
        best = {}
        for l in rec.get("registry_links") or []:
            if l["strength"] not in ("strong", "confirming"):
                continue
            cur = best.get(l["id"])
            if cur is None or _d.STRENGTH_RANK[l["strength"]] > _d.STRENGTH_RANK[cur["strength"]]:
                best[l["id"]] = l
        if not best:
            return
        ids = rec.setdefault("ids", {})
        ncts = sorted(k for k in best if registry_kind(k) == "nct")
        others = sorted(k for k in best if registry_kind(k) != "nct")
        if ncts and not (isinstance(ids.get("nct"), dict) and _d.trusted(ids["nct"])):
            l = best[ncts[0]]
            ids["nct"] = {"value": ncts[0], "source": l["source"], "via": l["via"], "at": l["at"]}
        reg = [iv for iv in (ids.get("registry") or []) if _d.trusted(iv)]
        have = set(iv["value"] for iv in reg)
        for k in ncts[1:] + others:
            if k not in have:
                l = best[k]
                reg.append({"value": k, "source": l["source"], "via": l["via"], "at": l["at"],
                            "type": registry_kind(k) or "other"})
        if reg:
            ids["registry"] = sorted(reg, key=lambda iv: iv["value"])

    def _review_registry(self, items, recs, links):
        """Az áttekintés által közölt regiszterszám: NCT → CT.gov ``studies/{NCT}`` megerősítés (``review``
        erősség); más regiszter (nincs nyilvános API) → ``review_unconfirmed``."""
        checked = {}
        for it in items:
            rid = links.get(it.ref)
            if not rid or not it.review_registry:
                continue
            have = set((l.get("id"), l.get("via")) for l in recs[rid].get("registry_links") or [])
            for value in it.review_registry:
                kind = registry_kind(value)
                if kind == "nct":
                    if (value, "review+ctgov.study") in have:
                        continue
                    if value not in checked:
                        meta, source, via = self._fetch_nct(value)
                        checked[value] = bool(meta)
                        if not meta and self.src.client("ctgov"):
                            self.warnings.append(_warn(
                                "H005", "Az áttekintés által közölt regiszterszám (%s) nem található a "
                                        "ClinicalTrials.gov-on." % value,
                                "The registration number given by the review (%s) is not found on ClinicalTrials.gov."
                                % value, ref=it.ref))
                    if checked[value]:
                        self._add_link(recs[rid], value, "review", "ctgov", "review+ctgov.study", ref=it.ref)
                elif kind:
                    self._add_link(recs[rid], value, "review_unconfirmed", "review", "review", ref=it.ref)

    # -- rekordépítés -----------------------------------------------------------------------------

    def _ids_from_meta(self, it):
        r = it.result
        meta = r["meta"]
        human = meta.get("_human")
        ids = {}
        for k in ("pmid", "doi", "pmcid", "openalex", "eid", "nct"):
            v = meta.get(k)
            if not v:
                continue
            iv = {"value": v, "source": r["source"], "via": r["via"], "at": self.at}
            ret = r.get("retrieval") or {}
            if isinstance(ret, dict) and ret.get("cache_key"):
                iv["retrieval"] = ret["cache_key"]
            if human and human.get("kind") == k and human.get("value") == v and not human.get("from_option"):
                iv = {"value": v, "source": "user", "via": "decision:%s" % human.get("decision_id"),
                      "at": self.at, "confirmed_by": r["via"]}
            ids[k] = iv
        return ids

    def _record_for(self, it):
        if it.status == "resolved":
            meta = it.result["meta"]
            ids = self._ids_from_meta(it)
            rid = _d.rec_id_for(ids)
            bib = {}
            for k in ("title", "authors", "authors_truncated", "first_author", "last_author", "year", "journal",
                      "journal_full", "volume", "issue", "pages", "pub_types", "language", "issn", "essn"):
                v = meta.get(k)
                if v not in (None, "", []):
                    bib[k] = v
            if isinstance(bib.get("year"), str):
                try:
                    bib["year"] = int(bib["year"][:4])
                except ValueError:
                    bib.pop("year")
            if bib.get("authors"):
                bib["authors"] = [str(a) for a in bib["authors"]][:50]
            flags = {}
            if meta.get("retracted"):
                flags["retracted"] = True
            if meta.get("preprint"):
                flags["preprint"] = True
            if meta.get("registry_record"):
                flags["registry_record"] = True
            pts = " ".join(bib.get("pub_types") or []).lower()
            if "erratum" in pts:
                flags["erratum"] = True
            if re.search(r"(?:^|:\s*)(?:a\s+)?(?:study\s+)?protocol\b|\bprotocol for an?\b", (bib.get("title") or "").lower()):
                flags.setdefault("protocol", True)
            if re.search(r"\b(?:secondary|post[- ]hoc|exploratory|ancillary)\s+analys", (bib.get("title") or "").lower()):
                flags["secondary_analysis"] = True
            rec = {"rec_id": rid, "ids": ids, "bib": bib, "flags": flags, "related": [], "origins": [],
                   "retrievals": [], "resolution": {"status": "resolved", "method": it.result["method"],
                                                    "score": it.result["score"]},
                   "status": "active", "merged_into": None}
            ret = it.result.get("retrieval")
            if isinstance(ret, dict) and ret.get("source") in _d.API_SOURCES and ret.get("endpoint") and ret.get("at"):
                rec["retrievals"].append(dict((k, ret.get(k)) for k in ("source", "endpoint", "at", "http_status",
                                                                         "cache_key")))
            if meta.get("unverified_live"):
                rec["resolution"]["unverified_live"] = True
            if it.notes:
                rec["resolution"]["notes"] = sorted(set(it.notes))
            return rec
        # feloldatlan / kétértelmű: rec-x (az áttekintés meg nem erősített azonosítói megmaradnak, H003)
        ids = {}
        for k, iv in (it.cand.get("ids") or {}).items():
            if k in ("pmid", "doi", "pmcid", "nct", "eid", "openalex") and isinstance(iv, dict) and iv.get("value"):
                ids[k] = dict((kk, vv) for kk, vv in iv.items() if kk in ("value", "source", "via", "at",
                                                                         "retrieval"))
        rid = _d.rec_id_for({}, it.text or it.title or it.ref, salt=it.ref)
        bib = {"title": it.title, "first_author": it.first_author, "year": it.year, "journal": it.journal}
        bib = dict((k, v) for k, v in bib.items() if v not in (None, ""))
        if bib.get("year") is not None and not 1800 <= int(bib["year"]) <= 2100:
            bib.pop("year")
        rec = {"rec_id": rid, "ids": ids, "bib": bib, "flags": {}, "related": [], "origins": [], "retrievals": [],
               "resolution": {"status": "ambiguous" if it.status == "ambiguous" else "unresolved",
                              "method": "human_rejected" if "human_rejected" in it.notes else None,
                              "score": max([o["score"] for o in it.options if o.get("score") is not None] or [None])
                              if it.options else None},
               "status": "active", "merged_into": None,
               "cited_as": {"text": (it.text or it.title or it.ref)[:300]}}
        if it.notes:
            rec["resolution"]["notes"] = sorted(set(it.notes))
        return rec

    @staticmethod
    def _merge_record(into, new, conflicts):
        """Két, azonos ``rec_id``-jű rekord egyesítése (több jelölt ugyanarra a közleményre): eredet unió,
        azonosítók (API-forrás elsőbbséggel), hiányzó bibliográfiai mezők pótlása; ellentmondó azonosító →
        ``conflicts``."""
        for o in new.get("origins") or []:
            if o not in into["origins"]:
                into["origins"].append(o)
        ids = into.setdefault("ids", {})
        for k, iv in (new.get("ids") or {}).items():
            if k == "registry":
                cur = ids.setdefault("registry", [])
                for x in iv:
                    if x["value"] not in [c["value"] for c in cur]:
                        cur.append(x)
                continue
            if k not in ids:
                ids[k] = iv
            elif _d.norm_id(k, ids[k].get("value")) != _d.norm_id(k, iv.get("value")):
                if _d.trusted(iv) and not _d.trusted(ids[k]):
                    ids[k] = iv
                elif _d.trusted(iv) and _d.trusted(ids[k]):
                    conflicts.append((into["rec_id"], k, ids[k]["value"], iv["value"]))
        for k, v in (new.get("bib") or {}).items():
            into.setdefault("bib", {}).setdefault(k, v)
        for k, v in (new.get("flags") or {}).items():
            if v:
                into.setdefault("flags", {})[k] = True
        for x in new.get("retrievals") or []:
            if x not in into.setdefault("retrievals", []) and len(into["retrievals"]) < 10:
                into["retrievals"].append(x)
        notes = sorted(set((into.get("resolution") or {}).get("notes", []) + (new.get("resolution") or {}).get("notes", [])))
        if notes:
            into.setdefault("resolution", {})["notes"] = notes

    # -- futtatás ---------------------------------------------------------------------------------

    def run(self, reviews, prior_doc=None, decisions=(), include_unknown=False):
        """A feloldás. Visszaad: ``{records, proposals, links, confirmations, warnings, sources, stats}``.

        ``records``: a teljes új ``records[]`` (a más lépésekből — frissítő keresés, hivatkozáskövetés — jött
        rekordok változatlanul megmaradnak); ``proposals``: a ``resolution`` (és az R-szintű ``id_conflict``)
        javaslatok stabil ``p-res-…`` azonosítóval."""
        prior_doc = prior_doc or {}
        items = collect_items(reviews, include_unknown=include_unknown)
        prior_records = dict((r["rec_id"], copy.deepcopy(r)) for r in prior_doc.get("records") or [])
        prior_by_ref = {}
        for r in sorted(prior_records.values(), key=lambda x: (x.get("merged_by") == "resolve", x["rec_id"])):
            for o in r.get("origins") or []:
                if o.get("route") == "review_extraction" and o.get("review_id") and o.get("cand_id"):
                    prior_by_ref.setdefault("%s#%s" % (o["review_id"], o["cand_id"]), r)

        # 0. emberi döntések, majd a korábban már feloldottak újrahasznosítása (hálózat nélkül)
        self._human_pass(items, decisions, prior_doc)
        reused = set()
        for it in items:
            if it.status is not None or self.force:
                continue
            pr = prior_by_ref.get(it.ref)
            # (a visszavont emberi választás nem marad érvényben: a „human-choice" rekordot csak hatályos döntés tartja)
            if pr and (pr.get("resolution") or {}).get("status") == "resolved" and \
                    (pr.get("resolution") or {}).get("method") != "human-choice" and \
                    pr.get("merged_by") != "resolve" and any(_d.trusted(v) for k, v in (pr.get("ids") or {}).items()
                                                             if k != "registry" and isinstance(v, dict)):
                pr = copy.deepcopy(pr)
                if pr.get("merged_by") == "dedupe":  # a dedupe-összevonást a link() újraszámolja
                    pr["status"] = "active"
                    pr["merged_into"] = None
                    pr.pop("merged_by", None)
                it.status = "reused"
                reused.add(it.ref)
                it.result = {"record": pr}

        # 1–7. automatikus lépések
        self._pass_review_ids(items)
        self._pass_registry_only(items)
        self._pass_ecitmatch(items)
        self._pass_esearch(items)
        self._pass_europepmc(items)
        self._pass_openalex(items)
        self._pass_scopus(items)
        self._pass_crossref(items)
        for it in items:
            if it.status is None:
                it.status = "ambiguous" if it.options else "unresolved"

        # rekordok
        records = {}
        keep_other = {}
        for rid, r in prior_records.items():
            others = [o for o in r.get("origins") or [] if o.get("route") != "review_extraction"]
            if others:
                rr = copy.deepcopy(r)
                rr["origins"] = others
                keep_other[rid] = rr
        records.update(copy.deepcopy(keep_other))
        links = {}
        conflicts = []
        for it in items:
            if it.status == "reused":
                rec = copy.deepcopy(it.result["record"])
                rec["origins"] = []
            else:
                rec = self._record_for(it)
            origin = {"route": "review_extraction", "review_id": it.review_id, "cand_id": it.cand_id,
                      "search_id": None}
            rec["origins"] = [origin]
            rid = rec["rec_id"]
            links[it.ref] = rid
            if rid in records:
                self._merge_record(records[rid], rec, conflicts)
            else:
                records[rid] = rec
        # a korábbi, most más rec_id-re feloldott rekordok: merged_into (döntések hivatkozása megmarad)
        for rid, r in prior_records.items():
            if rid in records:
                continue
            refs = ["%s#%s" % (o.get("review_id"), o.get("cand_id")) for o in r.get("origins") or []
                    if o.get("route") == "review_extraction"]
            targets = sorted(set(links[x] for x in refs if x in links and links[x] != rid))
            if targets:
                rr = copy.deepcopy(r)
                rr["status"] = "merged_into"
                rr["merged_into"] = targets[0]
                rr["merged_by"] = "resolve"
                records[rid] = rr
        # dúsítás
        if self.enrich:
            mined = set(rid for rid, r in records.items()
                        if any(o.get("route") == "review_extraction" for o in r.get("origins") or []))
            self._enrich_records(records, only=mined)
        self._review_registry(items, records, links)
        for r in records.values():
            r["origins"].sort(key=lambda o: (o.get("route") or "", o.get("review_id") or "", o.get("cand_id") or "",
                                             o.get("search_id") or ""))
            if (r.get("flags") or {}).get("retracted"):
                self.warnings.append(_warn(
                    "H013", "Visszavont közlemény: %s — jelöld, és a jogosultsági szűrésnél indokold a kizárást."
                    % r["rec_id"], "Retracted publication: %s — flag it and justify the exclusion at screening."
                    % r["rec_id"], rec_id=r["rec_id"]))
            unverified = (r.get("resolution") or {}).get("status") in _d.UNVERIFIED_RESOLUTION
            for k, iv in (r.get("ids") or {}).items():
                if k != "registry" and isinstance(iv, dict) and (unverified or not _d.trusted(iv)) and \
                        r.get("status") != "merged_into":
                    self.warnings.append(_warn(
                        "H003", "API-val meg nem erősített azonosító (%s:%s) a %s rekordban — a végső halmazba így nem "
                                "kerülhet." % (k, iv.get("value"), r["rec_id"]),
                        "Identifier not confirmed by an API (%s:%s) in %s — it cannot enter the final set like this."
                        % (k, iv.get("value"), r["rec_id"]), rec_id=r["rec_id"]))

        # automatikus elfogadás eltérő évvel (a cím szó szerint egyezik — pl. OpenAlex digitalizálási év, vagy a
        # követéses jelentés-sorozat másik tagja!): ember nézze meg (felülvizsgálat: korábban csak a rekordban látszott)
        for it in items:
            if it.status == "resolved" and "accepted_year_differs" in it.notes and not it.human:
                m = it.result["meta"]
                self.warnings.append(_warn(
                    "resolution_year_differs",
                    "%s: a hivatkozás (%s) a(z) %s rekordhoz kötve, mert a cím és az első szerző egyezik, de az év eltér "
                    "(%s ↔ %s). Ellenőrizd: nem ugyanannak a vizsgálatnak egy másik (követéses) jelentése-e — ha igen, "
                    "adj feloldási döntést (decide --target %s --value pmid:<helyes>)."
                    % (it.ref, it.year, links.get(it.ref), it.year, m.get("year"), it.ref),
                    "%s: linked to %s because title and first author match, but the year differs (%s vs %s) — check "
                    "that it is not another (follow-up) report." % (it.ref, links.get(it.ref), it.year, m.get("year")),
                    ref=it.ref))
        # javaslatok
        proposals = self._proposals(items, links, conflicts, prior_doc, decisions)
        confirmations = self._confirmations(items)
        stats = {"candidates": len(items), "reused": len(reused),
                 "resolved": sum(1 for it in items if it.status in ("resolved", "reused")),
                 "ambiguous": sum(1 for it in items if it.status == "ambiguous"),
                 "unresolved": sum(1 for it in items if it.status == "unresolved"),
                 "by_method": {}, "calls": dict(sorted(self.src.calls.items()))}
        for it in items:
            if it.status == "resolved":
                m = it.result["method"]
                stats["by_method"][m] = stats["by_method"].get(m, 0) + 1
        stats["by_method"] = dict(sorted(stats["by_method"].items()))
        return {"records": [records[k] for k in sorted(records)], "proposals": proposals, "links": links,
                "confirmations": confirmations, "warnings": self.src.warnings + self.warnings,
                "sources": dict(sorted(self.src.status.items())), "stats": stats}

    def _confirmations(self, items):
        """A jelöltek áttekintés-eredetű azonosítóinak megerősítése: ``{ref: {fajta: végpont}}`` — csak ha a
        feloldott rekord ugyanazt az azonosítót hordozza (eltérésnél NINCS megerősítés)."""
        out = {}
        for it in items:
            if it.status != "resolved":
                continue
            meta = it.result["meta"]
            conf = {}
            for k, v in it.review_ids.items():
                if meta.get(k) and _d.norm_id(k, meta[k]) == v:
                    conf[k] = it.result["via"]
            if conf:
                out[it.ref] = conf
        return out

    def _proposals(self, items, links, conflicts, prior_doc, decisions):
        prior = [p for p in (prior_doc or {}).get("proposals") or [] if p.get("kind") == "resolution"
                 or (p.get("kind") == "id_conflict" and str(p.get("rule") or "").startswith("R-"))]
        prior_by_key = {}
        seq = 0
        for p in prior:
            key = (p["kind"], (p.get("items") or [None])[0])
            prior_by_key.setdefault(key, p)
            m = re.match(r"^p-res-(\d+)$", str(p.get("proposal_id") or ""))
            if m:
                seq = max(seq, int(m.group(1)))
        seq = max(seq, int((((prior_doc or {}).get("counters") or {}).get("proposal_seq") or {}).get("res") or 0))
        eff = _d.effective_decisions(decisions or [], kinds=("id_confirm",))
        out = []
        new = []
        for it in items:
            human = it.human
            prev = prior_by_key.get(("resolution", it.ref))
            if human and prev and not it.options:
                p = copy.deepcopy(prev)
                d = eff.get(("proposal", p.get("proposal_id"))) or eff.get(("candidate", it.ref))
                p["decision_id"] = d["decision_id"] if d else human.get("decision_id")
                p["status"] = "rejected" if ("human_rejected" in it.notes or it.status != "resolved") else "accepted"
                p["rec_id"] = links.get(it.ref)
                out.append(p)
                continue
            if it.status == "ambiguous" or (human and it.options) or (it.options and "human_rejected" in it.notes):
                opts, seen_opt = [], set()
                for o in sorted(it.options, key=lambda o: (-(o.get("score") or 0), o.get("rec_id") or "")):
                    k = o.get("rec_id") or json.dumps(o.get("ids"), sort_keys=True)
                    if k in seen_opt:
                        continue  # ugyanaz a közlemény két úton (PubMed + Europe PMC) — egyszer mutatjuk
                    seen_opt.add(k)
                    opts.append(o)
                for i, o in enumerate(opts, 1):
                    o["option"] = i
                rule = "R-id-title-mismatch" if "review_id_title_mismatch" in it.notes else (
                    "R-ecitmatch-mismatch" if "ecitmatch_title_mismatch" in it.notes else "R-ambiguous")
                p = {"kind": "resolution", "items": [it.ref] + [o["rec_id"] for o in opts if o.get("rec_id")],
                     "certainty": "possible", "score": opts[0].get("score") if opts else None, "rule": rule,
                     "features": {"title_sim": opts[0].get("score") if opts else None,
                                  "first_author_match": (opts[0].get("features") or {}).get("first_author_match")
                                  if opts else None, "shared_ids": []},
                     "options": opts, "status": "pending", "decision_id": None,
                     "rec_id": links.get(it.ref),
                     "explanation": _resolution_expl(it, rule, opts)}
                if prev:
                    p["proposal_id"] = prev["proposal_id"]
                    if prev.get("options") and human:
                        p["options"] = prev["options"]  # a döntés a korábbi lehetőség-sorszámra hivatkozik
                else:
                    new.append(p)
                d = eff.get(("proposal", p.get("proposal_id"))) or eff.get(("candidate", it.ref))
                if d and human:
                    p["decision_id"] = d["decision_id"]
                    p["status"] = "rejected" if "human_rejected" in it.notes or it.status != "resolved" else "accepted"
                out.append(p)
        for rid, k, a, b in conflicts:
            p = {"kind": "id_conflict", "items": [rid], "score": None, "rule": "R-id-conflict",
                 "features": {"conflicting_ids": ["%s:%s|%s:%s" % (k, a, k, b)], "shared_ids": []},
                 "status": "pending", "decision_id": None,
                 "explanation": _expl(
                     "Ugyanahhoz a közleményhez két API eltérő %s-t adott (%s vs %s). Ellenőrizd a forrásokat (H007)."
                     % (k, a, b), "Two APIs returned different %s values for the same report (%s vs %s). Check the "
                                  "sources (H007)." % (k, a, b))}
            prev = prior_by_key.get(("id_conflict", rid))
            if prev:
                p["proposal_id"] = prev["proposal_id"]
            else:
                new.append(p)
            out.append(p)
        for p in sorted(new, key=lambda x: x["items"][0]):
            seq += 1
            p["proposal_id"] = "p-res-%04d" % seq
        self.proposal_seq = seq
        return sorted(out, key=lambda p: p["proposal_id"])


def _resolution_expl(it, rule, opts):
    label = it.cand.get("study_label_in_review") or (it.text[:80] if it.text else it.ref)
    if rule == "R-id-title-mismatch":
        return _expl(
            "Az áttekintés által adott azonosító olyan közleményre mutat, amelynek a címe/szerzője nem egyezik a "
            "hivatkozással (%s). Lehet, hogy az áttekintés rossz azonosítót közölt. Válaszd ki a helyes lehetőséget "
            "(option:N), adj meg azonosítót (pmid:…), vagy utasítsd el (reject)." % label,
            "The identifier given by the review points to a report whose title/author does not match the citation "
            "(%s). The review may have given a wrong identifier. Choose the right option (option:N), enter an "
            "identifier (pmid:…), or reject." % label)
    if not opts:
        return _expl("Nem sikerült egyértelműen feloldani (%s)." % label, "Could not be resolved unambiguously (%s)." % label)
    return _expl(
        "Több lehetséges közlemény, vagy a hasonlóság a küszöb alatt van (%s; legjobb cím-hasonlóság %s). Válaszd ki "
        "a helyeset (option:N), adj meg azonosítót (pmid:…), vagy utasítsd el (reject)."
        % (label, "%.2f" % opts[0]["score"] if opts[0].get("score") is not None else "—"),
        "Several possible reports, or the similarity is below the threshold (%s; best title similarity %s). Choose "
        "the right one (option:N), enter an identifier (pmid:…), or reject."
        % (label, "%.2f" % opts[0]["score"] if opts[0].get("score") is not None else "—"))


def enrich_records(records, clients=None, http=None, use=None, now=None, only=None, epmc_annotations=True,
                   ctgov_links=True, env=None):
    """Regiszter- és visszavonás-dúsítás tetszőleges rekordokra (pl. a frissítő keresés bevont jelöltjeire):
    ugyanaz a logika, mint a feloldásnál. ``records``: ``{rec_id: rekord}`` (helyben módosul). Visszaad:
    ``{"sources": {...}, "warnings": [...]}``."""
    r = Resolver(clients=clients, http=http, use=use, now=now, epmc_annotations=epmc_annotations,
                 ctgov_links=ctgov_links, env=env)
    r._enrich_records(records, only=set(only) if only is not None else None)
    return {"sources": dict(sorted(r.src.status.items())), "warnings": r.src.warnings + r.warnings}


# =============================================================================================
# jelöltek frissítése és projekt-szintű lépés
# =============================================================================================

def apply_links_to_reviews(reviews, links, confirmations):
    """A jelöltek ``rec_id``-je és az áttekintés-eredetű azonosítóik ``confirmed_by``-ja (csak egyező azonosítónál).
    Visszaad: a megváltozott ``review_id``-k listája (a dokumentumok helyben módosulnak)."""
    changed = []
    for r in reviews:
        ch = False
        for c in r.get("candidates") or []:
            ref = "%s#%s" % (r["review_id"], c.get("cand_id"))
            if ref in links and c.get("rec_id") != links[ref]:
                c["rec_id"] = links[ref]
                ch = True
            for k, via in (confirmations.get(ref) or {}).items():
                iv = (c.get("ids") or {}).get(k)
                if isinstance(iv, dict) and iv.get("source") in ("review", "user") and iv.get("confirmed_by") != via:
                    iv["confirmed_by"] = via
                    ch = True
        if ch:
            changed.append(r["review_id"])
    return changed


def _select_sources(state, override, env=None):
    try:
        from . import sources as _src
        sel = _src.select_sources(state=state, override=override, env=env, include_automatic=True)
        cfg = _src.config_from_state(state, env)
        use = [s for s in sel["use"] if s in RESOLUTION_SOURCES]
        return use, sel.get("skipped") or [], cfg
    except Exception:  # pragma: no cover - a forrás-regiszter hiányában
        return list(DEFAULT_USE), [], {}


def run_resolve(project_dir, sources=None, offline=False, http=None, clients=None, now=None, force=False,
                include_unknown=False, relink=True, env=None):
    """L4 a projekten: ``reviews/*.json`` (+ korábbi ``studies.json``, ``decisions.jsonl``) → ``studies.json``
    rekordjai és feloldási javaslatai; a jelöltek ``rec_id``-je a ``reviews/<id>.json``-ban. ``relink``: utána a
    vizsgálat-klasztereket is újraszámolja (``dedup.link`` — hálózat nélkül), hogy a ``studies.json`` mindig
    konzisztens legyen. Visszaad: ``{ok, stats, sources, skipped, warnings, pending, exit_code, next}``
    (kilépési kód: 0 rendben, 3 forrás nem érhető el — részleges, 4 emberi döntésre vár)."""
    hd = _d.hh_dir(project_dir)
    state = _d.read_json(os.path.join(hd, "state.json"), {}) or {}
    use, skipped, cfg = _select_sources(state, sources, env)
    if clients is None and http is None:
        try:
            from . import net as _net
            http = _net.HttpClient(cache_dir=os.path.join(hd, "cache", "http"), offline=offline, env=env)
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "exit_code": 3, "errors": [_expl("A hálózati réteg nem tölthető be: %s" % exc,
                                                                   "The network layer cannot be loaded: %s" % exc)]}
    with _d.ProjectLock(project_dir):
        reviews = _d.load_reviews(project_dir)
        prior = _d.load_studies(project_dir) or {}
        decisions = _d.read_decisions(project_dir)
        res = Resolver(clients=clients, http=http, use=use if clients is None else
                       ([s for s in use if s in clients] if sources is not None else None),
                       cfg=cfg, env=env, now=now, force=force).run(reviews, prior, decisions,
                                                                    include_unknown=include_unknown)
        kept = [p for p in prior.get("proposals") or [] if not (p.get("kind") == "resolution" or (
            p.get("kind") == "id_conflict" and str(p.get("rule") or "").startswith("R-")))]
        counters = copy.deepcopy(prior.get("counters") or {})
        ps = dict(counters.get("proposal_seq") or {})
        ps["res"] = int(ps.get("res") or 0)
        for p in res["proposals"]:
            m = re.match(r"^p-res-(\d+)$", p["proposal_id"])
            if m:
                ps["res"] = max(ps["res"], int(m.group(1)))
        counters["proposal_seq"] = dict(sorted(ps.items()))
        doc = {"schema": _d.STUDIES_SCHEMA, "model": _d.MODEL, "generated": _d.utc_now(now),
               "records": res["records"], "studies": prior.get("studies") or [],
               "proposals": sorted(kept + res["proposals"], key=lambda p: p["proposal_id"]), "counters": counters}
        changed = apply_links_to_reviews(reviews, res["links"], res["confirmations"])
        if relink:
            doc = _d.link(doc["records"], reviews, decisions, prior=doc, now=now)
        for r in reviews:
            if r["review_id"] in changed:
                _d.write_json_atomic(os.path.join(hd, "reviews", "%s.json" % r["review_id"]), r)
        _d.write_json_atomic(os.path.join(hd, "studies.json"), doc)
    pending_res = sum(1 for p in res["proposals"] if p["status"] == "pending")
    pending_dedupe = (doc.get("summary") or {}).get("pending", 0) if relink else 0
    down = [s for s, v in res["sources"].items() if v.get("status") != "ok"]
    exit_code = 3 if down else (4 if (pending_res or pending_dedupe) else 0)
    return {"ok": True, "stats": res["stats"], "sources": res["sources"], "skipped": skipped,
            "warnings": res["warnings"], "reviews_updated": changed,
            "pending": [x for x in ([{"checkpoint": "EP3", "n": pending_res + pending_dedupe}]
                                    if (pending_res or pending_dedupe) else [])],
            "exit_code": exit_code, "next": "decide" if (pending_res or pending_dedupe) else "overlap"}
