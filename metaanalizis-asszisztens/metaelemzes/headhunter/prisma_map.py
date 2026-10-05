# -*- coding: utf-8 -*-
"""Metaheadhunter — PRISMA 2020 leképezés (``szk.prisma-flow/v1``) és a motor-ellenőrzés (TERV_metaheadhunter.md
13. fejezet).

Kezdőknek: a PRISMA 2020 folyamatábra két ágat ismer. A **bányászott** vizsgálatok (a korábbi áttekintésekből
és a hivatkozáskövetésből) az „egyéb módszerekkel azonosított" ágba kerülnek; a **frissítő keresés** találatai
az „adatbázisokból és regiszterekből azonosított" ágba, és ezeket a már ismert (bányászott) rekordokkal szemben
duplumszűrjük. Ez a modul a projekt fájljaiból (rekordok, a te szűrési döntéseid, a frissítő keresés számai)
kiszámolja a dobozok számait, és a motor (``metaelemzes.prisma.check_flow``, ugyanaz, mint a ``ma.py prisma
check``) ellenőrzésével együtt a ``01_kereses/headhunter/prisma_flow.json``-ba írja.

A fájl **a motor kanonikus mezőneveit** használja (``metaelemzes.prisma.COUNT_FIELDS``/``REASON_FIELDS``), mert
a composer-nevek (``dedup_removed`` …) a motor ``from_composer`` útján az egyéb ág kizárási okait elveszítenék
(hamis P008; az integrátor javítja, TERV 21/16). A részletek a ``hh`` objektumban vannak (a motor figyelmen
kívül hagyja): forrásonkénti számok, már ismert rekordok, az egyéb ágban cím alapján kizártak (ez a PRISMA-ábrán
nem doboz — lábjegyzet), az áttekintésenkénti hivatkozásszám (N), a függő döntések.

Számolás (13. fejezet):

* egyéb ág: ``other_methods_identified`` = egyedi (L1 + elfogadott összevonások utáni) közlemény-rekordok;
  ``_sought`` = azonosított − cím/absztrakt alapján kizárt; ``_not_retrieved``; ``_assessed`` = keresett − nem
  elérhető; ``_excluded`` (+ ``_excluded_reasons``);
* adatbázis-ág: A1 = a frissítő keresésben letöltött rekordok (PubMed, Europe PMC, OpenAlex, Scopus), A2 = CT.gov;
  D1 = A1 + A2 − D3 − B (a frissítésen belüli duplumok + a már ismert rekordok, ``hh.already_known``); D2 = 0 (gépi
  javaslat nem döntés); D3 = letöltött, de azonosító/cím nélküli tételek; B = az új egyedi rekordok; C, E, F, G,
  H a döntésekből;
* J = a két ág teljes szöveg szinten bevont jelentései; I = a vizsgálatok, amelyeknek ≥ 1 bevont jelentése van;
  ``awaiting`` = a még el nem bírált jelentések (így az egyenletek a folyamat közben is teljesülnek; a motor P013
  figyelmeztetést ad, amíg van ilyen).
* ``own_update`` mód (a SAJÁT korábbi áttekintésed frissítése): a korábbi változat bevont vizsgálatai és
  jelentései ``previous_studies``/``previous_reports``, az összesítés ``total_studies``/``total_reports`` (P015).

Nyilvános API::

    build_flow(state, reviews, studies_doc, decisions, update_doc=None, merged=None, now=None) → (flow, warnings)
    check(flow) → {"ok", "summary", "findings", "derived"}
    run_prisma(project_dir, now=None, …) → eredmény (prisma_flow.json)
    check_command(project_dir) → a motor-ellenőrzés parancsa (szöveg)
"""
from __future__ import absolute_import

import os

from . import state as S

FLOW_SCHEMA = "szk.prisma-flow/v1"
DATABASE_SOURCES = ("pubmed", "europepmc", "openalex", "scopus")
REGISTRY_SOURCES = ("ctgov",)
_META_KEYS = ("schema", "source", "hh", "generated", "template")


def _expl(hu, en):
    return {"hu": hu, "en": en}


def _warn(code, hu, en, **extra):
    w = {"code": code, "hu": hu, "en": en}
    w.update(extra)
    return w


def _d():
    from . import dedup
    return dedup


# =============================================================================================
# számolás (tartalék és own_update; az eligibility.branch_counts szabályaival azonos)
# =============================================================================================

def _count_flow(studies_doc, reviews, decisions, state=None, exclude=()):
    """A két ág dobozai a szűrési döntésekből. ``exclude``: kanonikus rekordok, amelyek egyik ágba sem számítanak
    (``own_update`` módban a korábbi változat jelentései)."""
    from .merge import screening_status, branch_membership
    records = (studies_doc or {}).get("records") or []
    status = screening_status(records, decisions, state)
    branch = branch_membership(records, reviews)
    exclude = set(exclude or ())
    origins = _d().effective_origins(records)
    other, db = [], []
    already_known = 0
    for rid, br in sorted(branch.items()):
        if rid in exclude:
            continue
        if br == "other":
            other.append(rid)
            if "update_search" in set(o.get("route") for o in origins.get(rid, [])):
                already_known += 1
        elif br == "database":
            db.append(rid)

    def tally(ids):
        t = {"identified": len(ids), "ta_excluded": 0, "ta_pending": 0, "not_retrieved": 0, "ft_excluded": 0,
             "ft_included": 0, "ft_awaiting": 0, "ft_pending": 0, "reasons": {}, "included_ids": []}
        for rid in ids:
            st = status.get(rid) or {}
            ta, ft = st.get("title_abstract") or {}, st.get("full_text") or {}
            if ta.get("status") != "decided":
                t["ta_pending"] += 1
                continue
            if ta.get("value") == "exclude":
                t["ta_excluded"] += 1
                continue
            if ft.get("status") != "decided":
                t["ft_pending"] += 1
                continue
            v = ft.get("value")
            if v == "not_retrieved":
                t["not_retrieved"] += 1
            elif v == "exclude":
                t["ft_excluded"] += 1
                lab = S.exclusion_reason_label(state, ft.get("reason_code")) if ft.get("reason_code") else "ok nélkül"
                t["reasons"][lab] = t["reasons"].get(lab, 0) + 1
            elif v == "include":
                t["ft_included"] += 1
                t["included_ids"].append(rid)
            else:
                t["ft_awaiting"] += 1
        return t

    to, td = tally(other), tally(db)
    flow = {}
    if to["identified"]:
        sought = to["identified"] - to["ta_excluded"]
        flow.update({"other_methods_identified": to["identified"], "other_methods_sought": sought,
                     "other_methods_not_retrieved": to["not_retrieved"],
                     "other_methods_assessed": sought - to["not_retrieved"],
                     "other_methods_excluded": to["ft_excluded"],
                     "other_methods_excluded_reasons": dict(sorted(to["reasons"].items()))})
    sought = td["identified"] - td["ta_excluded"]
    flow.update({"screened": td["identified"], "excluded_screening": td["ta_excluded"], "sought": sought,
                 "not_retrieved": td["not_retrieved"], "assessed": sought - td["not_retrieved"],
                 "excluded_eligibility": td["ft_excluded"],
                 "excluded_eligibility_reasons": dict(sorted(td["reasons"].items()))})
    awaiting = sum(t[k] for t in (to, td) for k in ("ta_pending", "ft_pending", "ft_awaiting"))
    included_ids = sorted(set(to["included_ids"] + td["included_ids"]))
    flow["included_reports"] = len(included_ids)
    study_of = {}
    for s in (studies_doc or {}).get("studies") or []:
        for rp in s.get("reports") or []:
            study_of[rp["rec_id"]] = s["study_id"]
    flow["included_studies"] = len(set(study_of.get(r, r) for r in included_ids))
    if awaiting:
        flow["awaiting"] = awaiting
    hh = {"other_methods_title_excluded": to["ta_excluded"], "already_known": already_known,
          "pending": {"other": {"title_abstract": to["ta_pending"], "full_text": to["ft_pending"],
                                "awaiting": to["ft_awaiting"]},
                      "database": {"title_abstract": td["ta_pending"], "full_text": td["ft_pending"],
                                   "awaiting": td["ft_awaiting"]}},
          "included_rec_ids": included_ids}
    return {"flow": flow, "hh": hh, "complete": awaiting == 0}


def _previous_set(studies_doc, reviews):
    """``own_update``: a saját korábbi áttekintés (a kiválasztott áttekintés(ek)) bevont jelentései és
    vizsgálatai."""
    d = _d()
    records = (studies_doc or {}).get("records") or []
    cmap = d.canonical_map(records)
    recs = set()
    for r in reviews:
        if r.get("status") != "selected":
            continue
        for c in r.get("candidates") or []:
            if c.get("status") != "rejected" and c.get("role_in_review") in ("included", "included_companion") \
                    and c.get("rec_id"):
                recs.add(cmap.get(c["rec_id"], c["rec_id"]))
    studies = set()
    for s in (studies_doc or {}).get("studies") or []:
        if any(rp.get("rec_id") in recs for rp in s.get("reports") or []):
            studies.add(s["study_id"])
    return recs, studies


# =============================================================================================
# leképezés
# =============================================================================================

def build_flow(state, reviews, studies_doc, decisions, update_doc=None, merged=None, now=None):
    """A ``prisma_flow.json`` (``szk.prisma-flow/v1`` + ``hh``). Visszaad: ``(flow, warnings)``."""
    from .merge import effective_reviews
    state = state or {}
    reviews = effective_reviews(reviews, decisions)
    mode = state.get("mode") or "harvest"
    warnings = []
    prev_recs, prev_studies = (set(), set())
    bc = None
    if mode == "own_update":
        prev_recs, prev_studies = _previous_set(studies_doc, reviews)
        bc = _count_flow(studies_doc, reviews, decisions, state, exclude=prev_recs)
        engine = "headhunter.prisma_map"
    else:
        try:
            from .eligibility import branch_counts
            bc = branch_counts(studies_doc, reviews, decisions, state, update_doc)
            engine = "headhunter.eligibility.branch_counts"
        except ImportError:
            bc = _count_flow(studies_doc, reviews, decisions, state)
            engine = "headhunter.prisma_map"
    flow = dict((k, v) for k, v in bc["flow"].items() if v is not None)
    hh = dict(bc.get("hh") or {})

    # ---- adatbázis-ág azonosítása (A1, A2, D1, D3) a frissítő keresésből
    b = int(flow.get("screened") or 0)
    res = (update_doc or {}).get("results") or {}
    by_source = res.get("by_source") or {}
    if update_doc:
        a1 = res.get("databases_retrieved")
        a2 = res.get("registers_retrieved")
        if a1 is None:
            a1 = sum(int(e.get("count_retrieved") or 0) for e in update_doc.get("queries") or []
                     if e.get("source") in DATABASE_SOURCES)
        if a2 is None:
            a2 = sum(int(e.get("count_retrieved") or 0) for e in update_doc.get("queries") or []
                     if e.get("source") in REGISTRY_SOURCES)
        d3 = int(res.get("unusable") or 0)
        d1 = int(a1) + int(a2) - d3 - b
        if d1 < 0:
            warnings.append(_warn("H011", "A frissítő keresés letöltött száma (%d) kisebb, mint az új egyedi rekordoké "
                                          "(%d) — az update_search.json elavult; futtasd újra az update lépést."
                                  % (int(a1) + int(a2), b),
                                  "Update search retrieved count (%d) is below the number of new unique records (%d); "
                                  "re-run update." % (int(a1) + int(a2), b)))
            a1 = int(a1) - d1
            d1 = 0
        flow.update({"identified_databases": int(a1), "identified_registers": int(a2), "duplicates_removed": d1,
                     "automation_removed": 0, "other_removed": d3})
        hh["databases"] = dict((k, v) for k, v in sorted(by_source.items()) if k in DATABASE_SOURCES)
        hh["registers"] = dict((k, v) for k, v in sorted(by_source.items()) if k in REGISTRY_SOURCES)
        hh["duplicates_within_update"] = max(0, d1 - int(hh.get("already_known") or 0))
        hh["unusable_removed"] = d3
        hh["update_window"] = dict((k, (update_doc.get("window") or {}).get(k)) for k in
                                   ("start_date", "end_date", "anchor", "overlap_months", "decision_id"))
        hh["incomplete_searches"] = [q["search_id"] for q in update_doc.get("queries") or []
                                     if q.get("status") != "done"]
        if hh["incomplete_searches"]:
            warnings.append(_warn("H012", "Hiányos frissítő keresés (%s): a PRISMA-szám nem véglegesíthető, amíg a keresés "
                                          "nem teljes." % ", ".join(hh["incomplete_searches"]),
                                  "Incomplete update search (%s): PRISMA numbers are not final."
                                  % ", ".join(hh["incomplete_searches"])))
        if update_doc.get("citation_search"):
            hh["citation_search"] = [dict((k, c.get(k)) for k in ("search_id", "direction", "source", "status", "count",
                                                                  "seed_kind", "since_year", "iterations"))
                                     for c in update_doc["citation_search"]]
    else:
        for k in ("identified_databases", "identified_registers", "duplicates_removed", "automation_removed",
                  "other_removed", "screened", "excluded_screening", "sought", "not_retrieved", "assessed",
                  "excluded_eligibility"):
            flow.setdefault(k, 0)
            if k in ("identified_databases", "identified_registers", "duplicates_removed", "automation_removed",
                     "other_removed"):
                flow[k] = 0
        hh["no_database_branch"] = True
        if b:
            warnings.append(_warn("H011", "Frissítő keresésből származó rekordok vannak, de nincs update_search.json — az "
                                          "azonosítási dobozok nem számolhatók; futtasd újra az update lépést.",
                                  "Records from an update search exist but update_search.json is missing."))
            flow["identified_databases"] = b

    if not flow.get("excluded_eligibility_reasons"):
        flow.pop("excluded_eligibility_reasons", None)
    if "other_methods_excluded_reasons" in flow and not flow["other_methods_excluded_reasons"]:
        flow.pop("other_methods_excluded_reasons")

    # ---- own_update: korábbi változat
    if mode == "own_update":
        flow["previous_studies"] = len(prev_studies)
        flow["previous_reports"] = len(prev_recs)
        flow["total_studies"] = len(prev_studies) + int(flow.get("included_studies") or 0)
        flow["total_reports"] = len(prev_recs) + int(flow.get("included_reports") or 0)

    # ---- összevetés a merged.json-nal
    if merged is not None:
        mc = merged.get("counts") or {}
        if mode != "own_update":
            for fk, mk in (("included_studies", "studies_included"), ("included_reports", "reports_included")):
                if mc.get(mk) is not None and flow.get(fk) is not None and int(mc[mk]) != int(flow[fk]):
                    warnings.append(_warn("H011", "Eltérés: PRISMA %s = %s, merged.json %s = %s — futtasd újra a merge "
                                                  "lépést." % (fk, flow[fk], mk, mc[mk]),
                                          "Mismatch: PRISMA %s = %s vs merged.json %s = %s." % (fk, flow[fk], mk,
                                                                                                mc[mk])))
        hh["citations_total"] = mc.get("citations_total", hh.get("citations_total"))
        hh["reviews_selected"] = mc.get("reviews_selected")
    sel = [r for r in reviews if r.get("status") == "selected"]
    hh["source_reviews"] = [{"review_id": r["review_id"],
                             "label": "%s %s" % ((r.get("bib") or {}).get("first_author") or "",
                                                 (r.get("bib") or {}).get("year") or "").strip() or r["review_id"],
                             "pmid": ((r.get("ids") or {}).get("pmid") or {}).get("value"),
                             "search_date": (r.get("search_date") or {}).get("value"),
                             "included_candidates": sum(1 for c in r.get("candidates") or []
                                                        if c.get("status") != "rejected" and
                                                        c.get("role_in_review") in ("included", "included_companion"))}
                            for r in sel]
    hh["counted_by"] = engine
    hh["mode"] = mode
    hh["complete"] = bool(bc.get("complete")) and not flow.get("awaiting")
    hh["note"] = _expl("A Metaheadhunter számolta: egyéb módszerek ág = a forrás-áttekintésekből (és a "
                       "hivatkozáskövetésből) bányászott egyedi közlemények; adatbázis-ág = a frissítő keresés, a már "
                       "ismert rekordokkal szemben duplumszűrve. Az egyéb ágban a cím/absztrakt alapján kizártak "
                       "(%d) a PRISMA-ábrán nem doboz: lábjegyzetben közöld." % int(hh.get("other_methods_title_excluded")
                                                                                   or 0),
                       "Computed by Metaheadhunter: other-methods branch = unique reports mined from the source reviews "
                       "(and citation searching); database branch = the update search deduplicated against known "
                       "records. Title/abstract exclusions in the other branch (%d) are not a PRISMA box: report "
                       "them in a footnote." % int(hh.get("other_methods_title_excluded") or 0))
    out = {"schema": FLOW_SCHEMA,
           "source": {"kind": "headhunter", "tool_version": S._tool_version(),
                      "file": S.HH_REL + "/" + S.FILES["prisma"], "generated": S.utc_now(now)}}
    for k in sorted(flow):
        out[k] = flow[k]
    out["hh"] = hh
    return out, warnings


def engine_input(flow):
    """A motornak átadandó szótár (a meta-kulcsok nélkül)."""
    return dict((k, v) for k, v in (flow or {}).items() if k not in _META_KEYS)


def check(flow):
    """A motor PRISMA-ellenőrzése (``metaelemzes.prisma.check_flow`` — a ``ma.py prisma check --json`` magja).
    Visszaad: ``{"ok", "summary": {error, warning, info}, "findings": [...], "derived": {...}}``."""
    from .. import prisma as engine_prisma
    res = engine_prisma.check_flow(engine_input(flow), "PRISMA2020")
    findings = []
    for f in res.findings:
        findings.append(dict((k, f.get(k)) for k in ("code", "severity", "title", "detail", "advice", "fields")
                             if k in f))
    return {"ok": bool(res.ok), "summary": res.summary(), "findings": findings,
            "derived": dict((k, v) for k, v in (res.derived or {}).items() if isinstance(v, (int, float, str)))}


def check_command(project_dir=None):
    """A motor-ellenőrzés parancsa (a felhasználó/ágens maga is lefuttathatja)."""
    p = S.HH_REL + "/" + S.FILES["prisma"]
    if project_dir:
        p = os.path.join(project_dir, *p.split("/"))
    return "python3 ma.py prisma check --json %s --out-format json" % p


def run_prisma(project_dir, now=None, merged=None, state=None, reviews=None, studies_doc=None, decisions=None,
               update_doc=None):
    """``prisma_flow.json`` írása és ellenőrzése (H011, ha a motor hibát talál). Visszaad: ``{ok, file, flow,
    summary, findings, warnings, check_command}``."""
    state = state if state is not None else S.load_state(project_dir)
    reviews = reviews if reviews is not None else S.load_reviews(project_dir)
    studies_doc = studies_doc if studies_doc is not None else (S.read_json(S.path(project_dir, S.FILES["studies"]))
                                                               or {})
    decisions = decisions if decisions is not None else S.read_decisions(project_dir)
    update_doc = update_doc if update_doc is not None else S.read_json(S.path(project_dir, S.FILES["update"]))
    merged = merged if merged is not None else S.read_json(S.path(project_dir, S.FILES["merged"]))
    flow, warnings = build_flow(state, reviews, studies_doc, decisions, update_doc, merged, now=now)
    chk = check(flow)
    flow["hh"]["engine_check"] = {"ok": chk["ok"], "summary": chk["summary"],
                                  "codes": sorted(set(f["code"] for f in chk["findings"]))}
    if not chk["ok"]:
        errs = [f for f in chk["findings"] if f.get("severity") == "error"]
        warnings.append(_warn("H011", "A PRISMA-számok nem mennek át a motor ellenőrzésén: %s"
                              % "; ".join("%s %s" % (f["code"], f.get("detail") or f.get("title")) for f in errs[:5]),
                              "The PRISMA numbers fail the engine check: %s" % ", ".join(f["code"] for f in errs[:5])))
    elif any(f.get("code") == "P013" for f in chk["findings"]):
        warnings.append(_warn("P013", "A PRISMA-ábra még nem végleges: %s jelentés vár döntésre (EP4)."
                              % flow.get("awaiting"),
                              "The PRISMA flow is not final yet: %s reports await a decision." % flow.get("awaiting")))
    fp = S.path(project_dir, S.FILES["prisma"])
    with S.lock(project_dir):
        S.write_json_atomic(fp, flow)
    return {"ok": chk["ok"], "file": S.HH_REL + "/" + S.FILES["prisma"], "flow": flow, "summary": chk["summary"],
            "findings": chk["findings"], "warnings": warnings, "check_command": check_command(project_dir)}
