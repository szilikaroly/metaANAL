# -*- coding: utf-8 -*-
"""Metaheadhunter — a bevont vizsgálatok kinyerése (L3, TERV 6. fejezet): ``run_extract``, ``show_text``,
``import_agent_classification``.

Kezdőknek — a stratégiák sorrendje (``--strategy auto``):

1. **Strukturált teljes szöveg (JATS)** a nyílt Europe PMC-ből vagy a PMC-ből (``efetch``). A szöveg CSAK
   memóriában él (N4); a kimenetbe csak bibliográfiai tény és ≤ 300 karakteres idézet kerül. Ha a kiadó nem engedi az
   XML-letöltést, a PMC csak a címlapot adja (``<body>`` nélkül) — ezt NEM tekintjük teljes szövegnek.
2. **Irodalomjegyzék API-ból** (``reflist``), ha nincs teljes szöveg vagy abból nem lett jelölt:
   Europe PMC ``/references`` → OpenAlex ``referenced_works`` (ingyenes egyedi lekérésekkel akkor is, ha a listás
   keret elfogyott) → Scopus ``view=REF`` (csak kulccsal és jogosultsággal). Ezek csak HIVATKOZÁSOK: ``unknown``
   szerepű, ``low`` bizonyosságú jelöltek — az EP2-ben ember dönti el, melyik bevont vizsgálat.
3. **A felhasználó saját PDF-je** (``--strategy pdf --pdf …``): a PDF a helyén marad, csak a hash-e tárolódik.

Élő próbán (BCG–tuberkulózis, 2026-10-05) a régi, nem nyílt metaanalízisekhez (Colditz 1994, 1995) az Europe PMC
nem adott irodalomjegyzéket, az OpenAlex igen (53 hivatkozás) — ezért a láncolat. Az Abubakar 2013 (HTA) PMC-
rekordja ``efetch``-csel csak címlap volt („does not allow downloading of the full text in XML form") — korábban ez
„teljes szöveg, 0 jelölt" lett, most irodalomjegyzék-útra vált.
"""
from __future__ import absolute_import

import hashlib
import json
import os
import re

from . import state as S

#: OpenAlex: legfeljebb ennyi hivatkozott munka egyedi lekéréssel (ingyenes, de udvariasan korlátozva)
OPENALEX_REF_CAP = 300
#: a ``show-text`` alapértelmezett részei
SHOW_TEXT_PARTS = ("methods", "results", "tables")


class ExtractError(Exception):
    """Használati hiba (a CLI 2-es kilépési kóddal adja vissza)."""

    def __init__(self, hu, en=None, code="USAGE"):
        Exception.__init__(self, hu)
        self.hu = hu
        self.en = en or hu
        self.code = code


def _expl(hu, en):
    return {"hu": hu, "en": en}


def _source_unavailable():
    from .net import SourceUnavailable
    return SourceUnavailable


def has_body(xml):
    """Valódi teljes szöveg-e a JATS (van ``<body>``)? A PMC ``efetch`` a nem letölthető cikkeknél csak a
    címlapot adja (front + absztrakt) — az nem teljes szöveg."""
    return bool(xml) and re.search(r"<body[\s>]", xml) is not None


class _Clients(object):
    """Forráskliensek lusta létrehozása (tesztben ``clients`` dict-tel helyettesíthető)."""

    def __init__(self, http, cfg, env, clients=None):
        self.http = http
        self.cfg = cfg or {}
        self.env = env
        self.given = clients or {}
        self.made = {}

    def get(self, name):
        if name in self.given:
            return self.given[name]
        if name not in self.made:
            from . import sources as srcreg
            self.made[name] = srcreg.make_client(name, http=self.http, cfg=self.cfg.get(name), env=self.env)
        return self.made[name]

    def has(self, name):
        return name in self.given or self.http is not None


def _openalex_usable(cfg, clients):
    if "openalex" in clients.given:
        return True
    ent = (cfg or {}).get("openalex") or {}
    # a listás keret kimerülése (rate_limited) nem zárja ki az ingyenes egyedi lekéréseket
    return bool(ent.get("enabled", True)) and ent.get("status") not in ("unauthorized", "forbidden")


def _scopus_usable(cfg, clients):
    if "scopus" in clients.given:
        return True
    ent = (cfg or {}).get("scopus") or {}
    return bool(ent.get("enabled")) and bool(ent.get("key_configured")) and ent.get("status") not in (
        "unauthorized", "forbidden", "not_configured")


def _openalex_reference_works(oa, ident, cap=OPENALEX_REF_CAP):
    """A hivatkozott munkák nyers OpenAlex-alakban. Előbb listás lekérés (gyors, kredites); ha a keret elfogyott,
    egyedi lekérések (ingyenesek). Visszaad: (munkák, megjegyzés)."""
    SU = _source_unavailable()
    wids = oa.referenced_works(ident)
    if not wids:
        return [], None
    wids = wids[:cap]
    note = None
    works = []
    try:
        works = list(oa.works_by_ids(wids))
    except SU as exc:
        if getattr(exc, "status", None) != "rate_limited":
            raise
        note = "list_quota"
        works = []
    if not works:
        from .openalex import DEFAULT_SELECT
        for w in wids:
            rec = oa.work(w, select=DEFAULT_SELECT)
            if rec:
                works.append(rec)
    order = dict((w, i) for i, w in enumerate(wids))
    from .net import norm_openalex
    works.sort(key=lambda r: order.get(norm_openalex(r.get("id")), 10 ** 6))
    return works, note


def _reflist(rid, ids, clients, cfg, at, included):
    """Irodalomjegyzék-láncolat. Visszaad: (eredmény|None, forrás|None, próbák listája)."""
    pmid = (ids.get("pmid") or {}).get("value")
    doi = (ids.get("doi") or {}).get("value")
    eid = (ids.get("eid") or {}).get("value")
    tried = []
    if pmid:
        refs = list(clients.get("europepmc").references("MED", pmid, normalize=False))
        tried.append({"source": "europepmc", "n": len(refs)})
        if refs:
            return (included.candidates_from_reference_records(rid, refs, "europepmc", at=at,
                                                               via="europepmc.references"), "europepmc", tried)
    if (pmid or doi) and _openalex_usable(cfg, clients):
        ident = ("pmid:%s" % pmid) if pmid else ("doi:%s" % doi)
        works, note = _openalex_reference_works(clients.get("openalex"), ident)
        tried.append({"source": "openalex", "n": len(works), "note": note})
        if works:
            return (included.candidates_from_reference_records(rid, works, "openalex", at=at,
                                                               via="openalex.referenced_works"), "openalex", tried)
    if (eid or doi or pmid) and _scopus_usable(cfg, clients):
        sc = clients.get("scopus")
        if not eid:
            x = sc.cross_ids(doi=doi, pmid=pmid)
            eid = (x or {}).get("eid")
        if eid:
            refs = list(sc.references(eid, normalize=False))
            tried.append({"source": "scopus", "n": len(refs)})
            if refs:
                return (included.candidates_from_reference_records(rid, refs, "scopus", at=at,
                                                                   via="scopus.references"), "scopus", tried)
    return None, None, tried


def _fetch_jats(rv, ids, clients, at):
    """(xml|None, route, license, pmcid) — a teljes szöveg csak memóriában."""
    from .europepmc import record as epmc_record
    pmid = (ids.get("pmid") or {}).get("value")
    pmcid = (ids.get("pmcid") or {}).get("value")
    license_ = (rv.get("fulltext") or {}).get("license")
    epmc = clients.get("europepmc")
    if not pmcid and pmid:
        hit = epmc.lookup_pmid(pmid, result_type="core")
        if hit:
            r = epmc_record(hit)
            if r.get("pmcid"):
                pmcid = r["pmcid"]
                ids["pmcid"] = {"value": pmcid, "source": "europepmc", "via": "europepmc.search", "at": at}
            license_ = r.get("license") or license_
    if not pmcid:
        return None, "none", license_, None
    xml = epmc.fulltext_xml(pmcid)
    if has_body(xml):
        return xml, "europepmc_oa", license_, pmcid
    xml = clients.get("pubmed").efetch_pmc(pmcid)
    if has_body(xml):
        return xml, "pmc_efetch", license_, pmcid
    return None, "none", license_, pmcid


def extract_review(project_dir, rid, strategy, pdf, clients, cfg, at, now=None):
    """Egy áttekintés kinyerése. Visszaad: (eredménysor, figyelmeztetések, forrás-kiesés?)."""
    from . import jats, included
    SU = _source_unavailable()
    rv = S.load_review(project_dir, rid)
    ids = rv.get("ids") or {}
    entry = {"run_id": S.new_run_id(now), "strategy": "jats", "status": "no_fulltext", "at": at,
             "n_candidates": 0, "message": None}
    res, route, license_ = None, "none", None
    warnings = []
    down = False
    reflist_source = None
    try:
        if strategy == "pdf":
            if not pdf:
                raise ExtractError("A --strategy pdf mellé add meg a PDF útját: --pdf <fájl>.",
                                   "--strategy pdf needs --pdf <file>.")
            from .. import kb
            pages = kb.pdf_pages(pdf)
            with open(pdf, "rb") as fh:
                sha = hashlib.sha256(fh.read()).hexdigest()
            res = included.extract_counts_from_text(list(enumerate(pages, 1)), rid, at=at,
                                                    container="user-pdf:%s" % sha[:12])
            route = "user_pdf"
            entry["strategy"] = "user_pdf"
            rv["fulltext"] = {"available": True, "route": "user_pdf", "license": None, "checked_at": at,
                              "user_pdf_sha256": sha}
        if res is None and strategy in ("auto", "jats"):
            xml, route, license_, pmcid = _fetch_jats(rv, ids, clients, at)
            rv["ids"] = ids
            if xml:
                doc = jats.parse(xml, container=pmcid)
                xml = None  # a teljes szöveg csak memóriában élt (N4)
                pub = (rv.get("bib") or {}).get("pubdate") or None
                res = included.extract_included(doc, rid, at=at, pub_date=pub)
                rv["fulltext"] = {"available": True, "route": route, "license": license_, "checked_at": at}
                if not res.get("candidates") and strategy == "auto":
                    # a JATS-ból nem lett jelölt (pl. irodalomjegyzék nélküli XML) → irodalomjegyzék-út is
                    warnings.append({"code": "jats_empty", "review_id": rid,
                                     "hu": "%s: a teljes szövegből nem lett jelölt — az irodalomjegyzéket API-ból is "
                                           "lekérem (unknown szerepű jelöltek, EP2)." % rid,
                                     "en": "%s: no candidates from the full text — also fetching the reference list "
                                           "from the APIs." % rid})
                    jres = res
                    rres, reflist_source, tried = _reflist(rid, ids, clients, cfg, at, included)
                    entry["reflist_tried"] = tried
                    if rres is not None and rres.get("candidates"):
                        # a JATS keresési dátuma/k-ja megmarad, a jelöltek az irodalomjegyzékből jönnek
                        for k in ("search_date", "k_reported", "completeness"):
                            if jres.get(k) and not rres.get(k):
                                rres[k] = jres[k]
                        rres["evidence"] = list(jres.get("evidence") or []) + list(rres.get("evidence") or [])
                        rres["warnings"] = list(jres.get("warnings") or []) + list(rres.get("warnings") or [])
                        res = rres
                        entry["strategy"] = "reflist_api"
            else:
                fulltext = {"available": False, "route": "none", "license": license_, "checked_at": at}
                if pmcid:
                    fulltext["note"] = "pmc_no_body"
                    warnings.append({"code": "fulltext_not_downloadable", "review_id": rid,
                                     "hu": "%s: a PMC-ben van (%s), de a kiadó nem engedi a teljes szöveg XML-"
                                           "letöltését (csak címlap) — irodalomjegyzék-út; a saját PDF-ed is "
                                           "használható (--strategy pdf)." % (rid, pmcid),
                                     "en": "%s: in PMC (%s) but the publisher does not allow XML download — "
                                           "falling back to the reference list (or use your own PDF)."
                                           % (rid, pmcid)})
                rv["fulltext"] = fulltext
        if res is None and strategy in ("auto", "reflist"):
            entry["strategy"] = "reflist_api"
            res, reflist_source, tried = _reflist(rid, ids, clients, cfg, at, included)
            entry["reflist_tried"] = tried
            if res is None:
                res = included.candidates_from_reference_records(rid, [], "europepmc", at=at)
                warnings.append({"code": "no_reference_list", "review_id": rid,
                                 "hu": "%s: sem teljes szöveg, sem API-ból elérhető irodalomjegyzék (%s). Teendő: "
                                       "a saját PDF-ed (--strategy pdf --pdf …), vagy Scopus-kulccsal a Scopus-"
                                       "irodalomjegyzék." % (rid, ", ".join("%s: %d" % (t["source"], t["n"])
                                                                            for t in tried) or "nincs azonosító"),
                                 "en": "%s: neither full text nor an API reference list was available." % rid})
            rv.setdefault("fulltext", {"available": False, "route": "none", "license": None, "checked_at": at})
            rv["fulltext"]["checked_at"] = at
    except SU as exc:
        down = True
        msg = getattr(exc, "explain", None) or _expl(str(exc), str(exc))
        warnings.append({"code": "H014", "hu": "%s: %s" % (rid, msg["hu"]), "en": "%s: %s" % (rid, msg["en"]),
                         "review_id": rid, "source": getattr(exc, "source", None)})
        entry["status"] = "failed"
        entry["message"] = msg
    if res is not None:
        rv = included.merge_into_review(rv, res)
        entry["status"] = "done" if res.get("candidates") or strategy == "pdf" else "partial"
        entry["n_candidates"] = len(res.get("candidates") or [])
        if reflist_source:
            entry["reflist_source"] = reflist_source
        for w in res.get("warnings") or []:
            w = dict(w)
            w["review_id"] = rid
            warnings.append(w)
    rv.setdefault("extraction_runs", []).append(entry)
    S.save_review(project_dir, rv)
    n_prop = sum(1 for c in rv.get("candidates") or [] if c.get("status") == "proposed")
    roles = {}
    for c in rv.get("candidates") or []:
        roles[c.get("role_in_review") or "unknown"] = roles.get(c.get("role_in_review") or "unknown", 0) + 1
    row = {"review_id": rid, "status": entry["status"], "route": route, "strategy": entry["strategy"],
           "reflist_source": reflist_source, "n_candidates": len(rv.get("candidates") or []), "n_proposed": n_prop,
           "roles": roles, "k_reported": (rv.get("k_reported") or {}).get("value"),
           "search_date": (rv.get("search_date") or {}).get("value"),
           "search_date_fallback": (rv.get("search_date") or {}).get("fallback")}
    return row, warnings, down


def run_extract(project_dir, review_ids=None, strategy="auto", pdf=None, offline=False, sources=None, http=None,
                clients=None, now=None, env=None, argv=None, progress=None):
    """L3 a projekt kiválasztott (vagy megadott) áttekintésein. Visszaad: ``{ok, reviews, warnings, pending,
    exit_code, next}`` (0 rendben; 3 forrás nem érhető el; 4 EP2-döntésre vár)."""
    state = S.load_state(project_dir)
    if review_ids:
        ids = list(review_ids)
    else:
        ids = [r["review_id"] for r in S.load_reviews(project_dir) if r.get("status") == "selected"]
    if not ids:
        raise ExtractError("Nincs kiválasztott forrás-áttekintés: előbb válassz (confirm --target rv-… --actor "
                           "user:<név>), vagy add meg: --review rv-….",
                           "No selected source review: select one first or pass --review rv-….")
    if strategy == "pdf" and len(ids) != 1:
        raise ExtractError("A PDF-út egyszerre egy áttekintésre szól: --review rv-… --pdf <fájl>.",
                           "The PDF route works on one review at a time.")
    from . import sources as srcreg
    cfg = srcreg.config_from_state(state, env)
    if http is None and clients is None:
        from . import net
        http = net.HttpClient(cache_dir=S.path(project_dir, "cache", "http"), offline=offline, env=env)
    cl = _Clients(http, cfg, env, clients)
    run = S.new_run(project_dir, "extract", argv=argv, env=env)
    at = S.utc_now(now)
    results, warnings = [], []
    down = False
    for i, rid in enumerate(ids, 1):
        if run.cancelled():
            warnings.append({"code": "CANCELLED", "hu": "Megszakítva (CANCEL) — a már kinyert áttekintések mentve.",
                             "en": "Cancelled — reviews extracted so far are saved."})
            down = True
            break
        run.progress({"phase": "review", "done": i - 1, "total": len(ids), "message": _expl(
            "Kinyerés: %s" % rid, "Extracting: %s" % rid)})
        if progress:
            progress({"phase": "review", "done": i - 1, "total": len(ids), "review_id": rid})
        row, ws, d = extract_review(project_dir, rid, strategy, pdf, cl, cfg, at, now=now)
        results.append(row)
        warnings.extend(ws)
        down = down or d
    n_prop = sum(r["n_proposed"] for r in results)

    def upd(st):
        S.set_step(st, "extract", "needs_human" if n_prop else ("failed" if down and not results else "done"),
                   run_id=run.run_id, stale_downstream=True)
        S.set_checkpoint(st, "EP2", "pending" if n_prop else "done", open_items=n_prop)
    S.mutate_state(project_dir, upd, env=env)
    code = 3 if down else (4 if n_prop else 0)
    run.finish("done" if not down else "partial", exit_code=code,
               stats={"reviews": len(results), "candidates": sum(r["n_candidates"] for r in results)})
    return {"ok": True, "reviews": results, "warnings": warnings,
            "pending": [{"checkpoint": "EP2", "n": n_prop}] if n_prop else [], "exit_code": code,
            "run_id": run.run_id,
            "next": ("list {p} candidates --status proposed   (majd confirm/exclude --target rv-…#c…; ágens: "
                     "show-text → agent-classify import)" if n_prop else "resolve {p}").replace("{p}", project_dir)}


# ---------------------------------------------------------------------------------------------
# show-text és ágens-osztályozás (6.2)
# ---------------------------------------------------------------------------------------------

def _load_doc(project_dir, rid, http=None, offline=False, env=None, clients=None):
    from . import jats
    rv = S.load_review(project_dir, rid)
    ids = rv.get("ids") or {}
    if http is None and clients is None:
        from . import net
        http = net.HttpClient(cache_dir=S.path(project_dir, "cache", "http"), offline=offline, env=env)
    from . import sources as srcreg
    cfg = srcreg.config_from_state(S.load_state(project_dir), env)
    cl = _Clients(http, cfg, env, clients)
    xml, route, _lic, pmcid = _fetch_jats(dict(rv), dict(ids), cl, S.utc_now())
    if not xml:
        raise ExtractError("%s: nincs letölthető teljes szöveg (JATS) — az ágens-osztályozáshoz a felhasználó PDF-je "
                           "kell (extract --strategy pdf), vagy kézi döntés az EP2-ben." % rid,
                           "%s: no downloadable full text (JATS)." % rid, code="HH_NO_FULLTEXT")
    return jats.parse(xml, container=pmcid), route


def show_text(project_dir, review_id, parts=SHOW_TEXT_PARTS, max_chars=None, http=None, offline=False, env=None,
              clients=None):
    """A teljes szöveg kért részei az ágensnek — CSAK a kimenetre, fájlba nem ment (N4)."""
    doc, route = _load_doc(project_dir, review_id, http=http, offline=offline, env=env, clients=clients)
    parts = tuple(p.strip() for p in (parts or SHOW_TEXT_PARTS) if p and p.strip())
    text = doc.plain_text(parts, max_chars=max_chars)
    return {"ok": True, "review_id": review_id, "route": route, "parts": list(parts), "text": text,
            "n_chars": len(text), "note": _expl("A szöveg csak megjelenítésre szolgál; ne mentsd fájlba (N4). Idézz "
                                                "szó szerint (≤ 300 karakter), azonosítót ne írj.",
                                                "Display only; do not save (N4). Quote verbatim, no identifiers.")}


def import_agent_classification(project_dir, review_id, file_path, http=None, offline=False, env=None,
                                clients=None):
    """``agent_classification/<id>.json`` beolvasása: az idézeteket a program a saját szövegében ellenőrzi (H004),
    azonosítót nem vesz át; minden tétel ``low``/``proposed`` (EP2)."""
    from . import included
    try:
        with open(file_path, encoding="utf-8-sig") as fh:
            agent_doc = json.load(fh)
    except (OSError, IOError, ValueError) as exc:
        raise ExtractError("Az ágens-fájl nem olvasható vagy nem JSON (%s): %s" % (file_path, exc))
    doc, _route = _load_doc(project_dir, review_id, http=http, offline=offline, env=env, clients=clients)
    try:
        res = included.candidates_from_agent_classification(doc, agent_doc, review_id)
    except ValueError as exc:
        raise ExtractError(str(exc))
    rv = S.load_review(project_dir, review_id)
    rv = included.merge_into_review(rv, res)
    S.save_review(project_dir, rv)
    rejected = res.get("rejected") or []
    dropped = res.get("dropped") or []
    n_prop = sum(1 for c in rv.get("candidates") or [] if c.get("status") == "proposed")

    def upd(st):
        S.set_checkpoint(st, "EP2", "pending" if n_prop else "done", open_items=n_prop)
    S.mutate_state(project_dir, upd, env=env)
    warnings = [{"code": r.get("code") or "H004", "hu": r.get("hu"), "en": r.get("en"), "review_id": review_id}
                for r in rejected]
    return {"ok": True, "review_id": review_id, "imported": len(res.get("candidates") or []),
            "rejected": rejected, "dropped": dropped, "warnings": warnings,
            "pending": [{"checkpoint": "EP2", "n": n_prop}] if n_prop else [],
            "exit_code": 4 if n_prop else 0,
            "next": "list %s candidates --review %s --status proposed" % (project_dir, review_id)}


def agent_file_path(project_dir, review_id):
    return S.path(project_dir, "agent_classification", "%s.json" % review_id)


def pdf_sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


__all__ = ["run_extract", "extract_review", "show_text", "import_agent_classification", "has_body", "ExtractError",
           "OPENALEX_REF_CAP", "agent_file_path"]

