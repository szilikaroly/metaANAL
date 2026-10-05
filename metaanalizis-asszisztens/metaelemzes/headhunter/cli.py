# -*- coding: utf-8 -*-
"""Metaheadhunter — parancssor: ``python -m metaelemzes.headhunter <parancs> <projekt> [kapcsolók]``
(bekötés után: ``ma.py headhunter …``). TERV_metaheadhunter.md 14. fejezet.

Kezdőknek — a tipikus sorrend (minden parancs magyarul magyaráz, és kiírja a javasolt következő lépést)::

    init     <projekt> --question "…" --population "…" --intervention "…"   PICO, kizárási okok, források
    sources  <projekt> --check                       források állapota (PubMed, Europe PMC, OpenAlex, Scopus, CT.gov)
    find     <projekt>                               meglévő szisztematikus áttekintések / metaanalízisek felkutatása
    list     <projekt> reviews                       a talált áttekintések rangsorral
    confirm  <projekt> --target rv-…,rv-… --actor user:<név>         kiválasztás (EP1)
    extract  <projekt>                               a bevont vizsgálatok kinyerése bizonyítékkal (EP2)
    resolve  <projekt>                               azonosítók (PMID, DOI, PMCID, NCT) API-ból
    dedup    <projekt>                               duplikátumok és társközlemények (EP3)
    confirm / exclude <projekt> --target … --actor user:<név>        emberi döntések (EP2–EP4)
    update   <projekt> --actor user:<név>            frissítő keresés a forrás-áttekintések keresési dátuma óta
    merge    <projekt>                               egyetlen egyesített vizsgálatlista + PRISMA-számok
    signoff  <projekt> --actor user:<név>            végső lezárás (EP5)
    export   <projekt> --outcome <kimenet> [--to-project]            exportok (studies.json, kinyerő sablon, RIS)
    status   <projekt>                               hol tartasz, mi vár döntésre, mi a következő lépés

Közös kapcsolók: ``--json`` (gépi boríték: ``{"ok", "command", "data", "warnings": [{code, hu, en}], "errors",
"pending": [{checkpoint, n}], "next", "exit_code"}``), ``--lang hu|en``, ``--offline`` (csak gyorsítótár),
``--sources a,b``, ``--actor user:<név>`` (emberi döntésnél kötelező), ``--quiet``.

Kilépési kódok: 0 rendben; 1 hiba (error szintű H-kód); 2 használati hiba; 3 forrás nem érhető el, a lépés
részleges; 4 emberi döntésre vár (nyitott ellenőrzőpont).

API-kulcs parancssori kapcsolóval NEM adható meg (az argv naplóba kerülhet): csak környezeti változóból
(``MA_SCOPUS_APIKEY``, ``MA_SCOPUS_INSTTOKEN``, ``MA_OPENALEX_APIKEY``, ``MA_NCBI_APIKEY``, ``MA_CONTACT_EMAIL``).
"""
from __future__ import absolute_import, print_function

import argparse
import json
import os
import re
import sys

from . import state as S

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_SOURCE, EXIT_HUMAN = 0, 1, 2, 3, 4
PROG = "python -m metaelemzes.headhunter"


class UsageError(Exception):
    def __init__(self, hu, en=None):
        Exception.__init__(self, hu)
        self.hu = hu
        self.en = en or hu


def _expl(hu, en):
    return {"hu": hu, "en": en}


def _env(ok=True, data=None, warnings=None, errors=None, pending=None, nxt=None, exit_code=0, message=None):
    out = {"ok": ok, "data": data, "warnings": list(warnings or []), "errors": list(errors or []),
           "pending": list(pending or []), "next": nxt, "exit_code": exit_code}
    if message:
        out["message"] = message
    return out


def _norm_warnings(ws):
    out = []
    for w in ws or []:
        if isinstance(w, dict):
            d = {"code": w.get("code") or "W", "hu": w.get("hu") or (w.get("message") or {}).get("hu") or "",
                 "en": w.get("en") or (w.get("message") or {}).get("en") or ""}
            for k in ("source", "review_id", "study_id", "file"):
                if w.get(k):
                    d[k] = w[k]
            out.append(d)
        else:
            out.append({"code": "W", "hu": str(w), "en": str(w)})
    return out


def _norm_errors(es):
    out = []
    for e in es or []:
        if isinstance(e, dict):
            out.append({"code": e.get("code") or "E", "hu": e.get("hu") or str(e), "en": e.get("en") or e.get("hu")
                        or str(e)})
        else:
            out.append({"code": "E", "hu": str(e), "en": str(e)})
    return out


def _split_ids(text):
    if not text:
        return []
    items = text if isinstance(text, (list, tuple)) else re.split(r"[,\s]+", str(text))
    out = []
    for it in items:
        it = str(it).strip()
        if it and it not in out:
            out.append(it)
    return out


def _actor(a, required=True):
    actor = getattr(a, "actor", None)
    if not actor:
        if required:
            raise UsageError("Ehhez emberi döntés kell: add meg, ki dönt (--actor user:<neved>, pl. --actor user:SzK).",
                             "A human decision is required: pass --actor user:<name>.")
        return None
    if not str(actor).startswith("user:"):
        raise UsageError("Az --actor értéke 'user:<név>' legyen — az ágens és a program csak javasol, nem dönt (N3).",
                         "--actor must be 'user:<name>' (agents only propose).")
    return actor


def _http(a, project_dir=None, env=None):
    from . import net
    cache = S.path(project_dir, "cache", "http") if project_dir and S.is_initialized(project_dir) else None
    return net.HttpClient(cache_dir=cache, offline=bool(getattr(a, "offline", False)), env=env)


def _load_json_file(fp, what):
    try:
        with open(fp, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, IOError) as exc:
        raise UsageError("A(z) %s fájl nem olvasható (%s): %s" % (what, fp, exc))
    except ValueError as exc:
        raise UsageError("A(z) %s fájl nem érvényes JSON (%s): %s" % (what, fp, exc))


# =============================================================================================
# init, sources
# =============================================================================================

def cmd_init(a):
    pico, criteria, reasons = None, None, None
    if a.pico:
        doc = _load_json_file(a.pico, "PICO")
        if not isinstance(doc, dict):
            raise UsageError("A --pico fájl JSON-objektum legyen ({question, population, intervention, …, "
                             "query_blocks} vagy {pico, criteria, exclusion_reasons}).")
        pico = doc.get("pico") if isinstance(doc.get("pico"), dict) else doc
        criteria = doc.get("criteria")
        reasons = doc.get("exclusion_reasons")
    question = a.question or (pico or {}).get("question")
    if not question:
        raise UsageError("Add meg a kutatási kérdést: --question \"…\" (vagy a --pico fájlban: question).")
    if pico is None:
        pico = S.pico_from_args(question, a.population, a.intervention, a.comparator, a.outcomes, a.study_designs)
    else:
        extra = S.pico_from_args(question, a.population, a.intervention, a.comparator, a.outcomes, a.study_designs)
        if not pico.get("query_blocks") and extra["query_blocks"]:
            pico["query_blocks"] = extra["query_blocks"]
    actor = _actor(a, required=False)
    st = S.init_state(a.project, question, pico=pico, mode=a.mode, criteria=criteria, exclusion_reasons=reasons,
                      actor=actor, force=a.force)
    warnings = []
    if not st["pico"].get("query_blocks"):
        warnings.append({"code": "W-PICO", "hu": "Nincsenek keresőkifejezések (fogalomblokkok): add meg a "
                                                 "--population és --intervention kifejezéseket, vagy a find parancsnak "
                                                 "a --query kapcsolót.",
                         "en": "No query blocks: give --population/--intervention or use find --query."})
    if not actor:
        warnings.append({"code": "W-ACTOR", "hu": "A PICO jóváhagyása (criteria_set döntés) még nincs rögzítve — add "
                                                  "meg az --actor user:<neved> kapcsolót (init --force), ha "
                                                  "jóváhagyod.",
                         "en": "PICO approval not recorded (pass --actor)."})
    data = {"state": S.HH_REL + "/state.json", "mode": st["mode"], "pico": st["pico"],
            "exclusion_reasons": [r["code"] + ": " + r["label"]["hu"] for r in st["exclusion_reasons"]],
            "sources": dict((k, {"enabled": v.get("enabled"), "status": v.get("status")})
                            for k, v in st["sources"].items())}
    return _env(True, data, warnings, nxt="sources %s --check   (majd: find %s)" % (a.project, a.project),
                message=_expl("A Metaheadhunter elindult ebben a projektben.", "Metaheadhunter initialized."))


def cmd_sources(a):
    from . import sources as srcreg
    env = None
    project = a.project
    state = S.load_state(project) if project else None
    lang = a.lang
    warnings = []
    if project and (a.enable or a.disable):
        actor = _actor(a)
        cfg, changes, w = srcreg.set_enabled(state.get("sources"), _split_ids(a.enable), _split_ids(a.disable))
        warnings.extend(w)
        if changes:
            d = S.append_decision(project, "source_config", ("source", "selection"),
                                  ",".join("%s=%s" % (c["source"], "on" if c["enabled"] else "off")
                                           for c in changes)[:80], actor,
                                  reason="Forrásválasztás: %s" % ", ".join(
                                      "%s %s" % (c["source"], "bekapcsolva" if c["enabled"] else "kikapcsolva")
                                      for c in changes), kb_refs=["D-S03-106"])
            state["sources"] = cfg
            S.save_state(project, state)
            warnings.append({"code": "INFO", "hu": "Rögzítve (%s)." % d["decision_id"],
                             "en": "Recorded (%s)." % d["decision_id"]})
    http = _http(a, project) if a.check else None
    res = srcreg.sources_status(state=state, check=a.check, http=http, sources=a.sources, lang=lang)
    if project and a.check:
        def upd(st):
            st["sources"] = res["sources"]
            S.set_step(st, "sources", "done")
        S.mutate_state(project, upd, env=env)
    warnings.extend(res.get("warnings") or [])
    data = {"rows": res["rows"], "checked": res["checked"], "secrets": res.get("secrets"),
            "table": srcreg.format_table(res["rows"], lang=lang)}
    code = res.get("exit_code", 0)
    return _env(code == 0, data, warnings, exit_code=code,
                nxt=("find %s" % project) if project else "init <projekt> --question \"…\"")


# =============================================================================================
# L1 — felkutatás és kiválasztás
# =============================================================================================

def _merge_review_doc(old, new):
    """A meglévő áttekintés-fájl frissítése egy új keresés találatával: a kiválasztás, a kinyert jelöltek, a
    bizonyítékok és a döntések megmaradnak; az azonosítók és a bibliográfiai adat kiegészül."""
    if not old:
        return new
    doc = dict(old)
    doc["found_by"] = sorted(set((old.get("found_by") or []) + (new.get("found_by") or [])))
    for k, v in (new.get("ids") or {}).items():
        doc.setdefault("ids", {}).setdefault(k, v)
    for k, v in (new.get("bib") or {}).items():
        doc.setdefault("bib", {}).setdefault(k, v)
    for k in ("flags", "proposal"):
        if new.get(k) is not None:
            doc[k] = new[k]
    for k in ("rank", "signals", "is_cochrane", "cochrane"):
        if new.get(k) is not None:
            doc[k] = new[k] if k != "signals" or not old.get("evidence") else dict(new[k], evidence_ids=(
                old.get("signals") or {}).get("evidence_ids") or [])
    if old.get("status") == "candidate" and new.get("status") == "superseded":
        doc["status"] = "superseded"
        doc["superseded_by"] = new.get("superseded_by")
    if not old.get("evidence") and new.get("evidence"):
        doc["evidence"] = new["evidence"]
        for k in ("search_date", "k_reported"):
            if new.get(k):
                doc[k] = new[k]
        if new.get("signals"):
            doc["signals"] = new["signals"]
    if (old.get("fulltext") or {}).get("route") in (None, "none") and new.get("fulltext"):
        doc["fulltext"] = new["fulltext"]
    rets = list(old.get("retrievals") or [])
    for r in new.get("retrievals") or []:
        if r not in rets and len(rets) < 20:
            rets.append(r)
    doc["retrievals"] = rets
    return doc


def cmd_find(a):
    from . import finder
    project = a.project
    state = S.load_state(project)
    query = a.query
    filters = {"max_per_source": a.max, "fulltext_dates": bool(a.fulltext_dates)}
    if a.since:
        filters["since"] = a.since
    if a.until:
        filters["until"] = a.until
    if a.query_file:
        q = _load_json_file(a.query_file, "lekérdezés")
        if isinstance(q, dict) and any(k in q for k in ("pubmed", "europepmc", "openalex", "scopus")):
            filters["query_by_source"] = dict((k, v) for k, v in q.items() if isinstance(v, str))
        elif isinstance(q, (list, dict)):
            query = q
    run = S.new_run(project, "find_reviews", argv=a._argv)
    try:
        res = finder.find_reviews(query=query, filters=filters, sources=a.sources, http=_http(a, project),
                                  state=state, progress=run.progress)
    except ValueError as exc:
        run.finish("failed", message=str(exc))
        raise UsageError(str(exc))
    written, kept = [], 0
    with S.lock(project):
        for cand in res["candidates"]:
            new = finder.to_review_doc(cand)
            fp = S.review_path(project, new["review_id"])
            old = S.read_json(fp)
            doc = _merge_review_doc(old, new)
            if old:
                kept += 1
            S.write_json_atomic(fp, doc)
            written.append(doc["review_id"])

        def upd(st):
            S.add_searches(st, res["searches"])
            for s, info in (res.get("sources") or {}).items():
                ent = st["sources"].get(s)
                if ent is None:
                    continue
                if info.get("searched"):
                    ent["status"] = "ok"
                    ent["message"] = None
                elif info.get("status") and info["status"] != "not_configured":
                    ent["status"] = info["status"]
                    ent["message"] = info.get("message")
                    ent["reset_at"] = info.get("reset_at")
            S.set_step(st, "find_reviews", "needs_human", run_id=run.run_id)
            selected = [r for r in S.load_reviews(project) if r.get("status") == "selected"]
            S.set_checkpoint(st, "EP1", "done" if selected else "pending",
                             open_items=sum(1 for c in res["candidates"] if c["status"] == "candidate"))
        S.mutate_state(project, upd)
    code = res.get("exit_code", 0)
    run.finish("done" if code == 0 else "partial", sources=res.get("sources"), stats=res.get("stats"), exit_code=code)
    rows = []
    for c in res["candidates"]:
        b = c.get("bib") or {}
        rows.append({"review_id": c["review_id"], "score": (c.get("rank") or {}).get("score"), "year": b.get("year"),
                     "first_author": b.get("first_author"), "title": (b.get("title") or "")[:160],
                     "status": c.get("status"), "cochrane": c.get("is_cochrane"),
                     "open_fulltext": (c.get("fulltext") or {}).get("available"),
                     "retracted": (c.get("flags") or {}).get("retracted"),
                     "erratum": (c.get("flags") or {}).get("erratum")})
    pending = [{"checkpoint": "EP1", "n": sum(1 for r in rows if r["status"] == "candidate")}]
    return _env(True, {"reviews": rows, "n": len(rows), "searches": [s["search_id"] for s in res["searches"]],
                       "sources": res.get("sources")}, res.get("warnings"), pending=pending,
                exit_code=EXIT_SOURCE if code == 3 else EXIT_HUMAN,
                nxt="confirm %s --target rv-…,rv-… --actor user:<név>   (válaszd ki a bányászandó áttekintéseket; "
                    "kizárás: exclude --target rv-… --reason \"…\")" % project)


def _list(a):
    project = a.project
    what = a.what
    if what == "reviews":
        from .merge import effective_reviews
        revs = effective_reviews(S.load_reviews(project), S.read_decisions(project))
        rows = []
        for r in sorted(revs, key=lambda r: -((r.get("rank") or {}).get("score") or 0)):
            if a.status and r.get("status") != a.status:
                continue
            b = r.get("bib") or {}
            rows.append({"review_id": r["review_id"], "status": r.get("status"),
                         "score": (r.get("rank") or {}).get("score"), "year": b.get("year"),
                         "first_author": b.get("first_author"), "title": (b.get("title") or "")[:160],
                         "search_date": (r.get("search_date") or {}).get("value"),
                         "search_date_fallback": (r.get("search_date") or {}).get("fallback"),
                         "k_reported": (r.get("k_reported") or {}).get("value"),
                         "n_candidates": len(r.get("candidates") or []),
                         "n_proposed": sum(1 for c in r.get("candidates") or [] if c.get("status") == "proposed"),
                         "fulltext": (r.get("fulltext") or {}).get("route"),
                         "flags": [k for k, v in sorted((r.get("flags") or {}).items()) if v is True],
                         "proposal": ((r.get("proposal") or {}).get("reason") or {}).get(
                             "en" if getattr(a, "lang", "hu") == "en" else "hu")})
        return rows
    if what == "candidates":
        rows = []
        for r in S.load_reviews(project):
            if a.review and r["review_id"] != a.review:
                continue
            if not a.review and r.get("status") != "selected":
                continue
            evs = dict((e["evidence_id"], e) for e in r.get("evidence") or [])
            for c in r.get("candidates") or []:
                if a.status and c.get("status") != a.status:
                    continue
                ev = evs.get((c.get("evidence_ids") or [None])[0]) or {}
                rows.append({"target": "%s#%s" % (r["review_id"], c["cand_id"]), "role": c.get("role_in_review"),
                             "status": c.get("status"), "confidence": c.get("confidence"),
                             "label": c.get("study_label_in_review"), "cited_as": (c.get("cited_as") or {}).get(
                                 "text", "")[:160], "quote": (ev.get("quote") or "")[:200],
                             "locator": ev.get("locator"), "rec_id": c.get("rec_id"),
                             "reasons": c.get("review_reasons")})
        return rows
    if what == "proposals":
        doc = S.read_json(S.path(project, S.FILES["studies"])) or {}
        rows = []
        for p in doc.get("proposals") or []:
            if (a.status or "pending") != "all" and p.get("status") != (a.status or "pending"):
                continue
            rows.append({"target": p["proposal_id"], "kind": p.get("kind"), "certainty": p.get("certainty"),
                         "score": p.get("score"), "rule": p.get("rule"), "items": p.get("items"),
                         "status": p.get("status"),
                         "explanation": (p.get("explanation") or {}).get(a.lang if a.lang == "en" else "hu")})
        return rows
    if what in ("studies", "records"):
        merged = S.read_json(S.path(project, S.FILES["merged"]))
        if what == "studies" and merged:
            rows = []
            for s in merged.get("studies") or []:
                if a.status and s.get("status") != a.status:
                    continue
                rows.append({"target": s["study_id"], "label": s["label"], "status": s["status"],
                             "reviews": [p["review_id"] for p in s.get("provenance") or []],
                             "found_by": s.get("found_by"), "reports": [r["rec_id"] for r in s["reports"]],
                             "flags": s.get("flags"), "conflicts": len(s.get("conflicts") or [])})
            return rows
        try:
            from .eligibility import screening_list
            rows = screening_list(project)
            if a.status:
                rows = [r for r in rows if ((r.get("full_text") or {}).get("value") or "pending") == a.status]
            return rows
        except ImportError:
            doc = S.read_json(S.path(project, S.FILES["studies"])) or {}
            return [{"target": r["rec_id"], "title": (r.get("bib") or {}).get("title"),
                     "year": (r.get("bib") or {}).get("year")} for r in doc.get("records") or []
                    if r.get("status") != "merged_into"]
    raise UsageError("Ismeretlen lista: %s (reviews | candidates | proposals | studies | records)." % what)


def cmd_list(a):
    S.load_state(a.project)
    rows = _list(a)
    return _env(True, {"what": a.what, "rows": rows, "n": len(rows)})


# =============================================================================================
# L3 — kinyerés (extract.py), show-text, agent-classify import
# =============================================================================================

def _extract_call(fn, *args, **kw):
    from .extract import ExtractError
    try:
        return fn(*args, **kw)
    except ExtractError as exc:
        raise UsageError(exc.hu, exc.en)


def cmd_extract(a):
    from . import extract as X
    project = a.project
    S.load_state(project)
    if a.pdf and a.strategy == "auto":
        a.strategy = "pdf"
    res = _extract_call(X.run_extract, project, review_ids=_split_ids(a.review) or None, strategy=a.strategy,
                        pdf=a.pdf, offline=a.offline, sources=a.sources, argv=a._argv)
    return _env(True, {"reviews": res["reviews"], "run_id": res.get("run_id")}, res.get("warnings"),
                pending=res.get("pending"), nxt=res.get("next"), exit_code=res["exit_code"])


def cmd_show_text(a):
    from . import extract as X
    S.load_state(a.project)
    parts = _split_ids(a.part) or list(X.SHOW_TEXT_PARTS)
    res = _extract_call(X.show_text, a.project, a.review, parts=parts, max_chars=a.max_chars, offline=a.offline)
    return _env(True, res, nxt="agent-classify import %s --review %s --file %s" % (
        a.project, a.review, S.rel(a.project, X.agent_file_path(a.project, a.review))))


def cmd_agent_classify(a):
    from . import extract as X
    S.load_state(a.project)
    if a.action != "import":
        raise UsageError("Használat: agent-classify import <projekt> --review rv-… --file <json>.")
    fp = a.file or X.agent_file_path(a.project, a.review)
    res = _extract_call(X.import_agent_classification, a.project, a.review, fp, offline=a.offline)
    return _env(True, dict((k, v) for k, v in res.items() if k not in ("warnings", "pending", "exit_code", "next")),
                res.get("warnings"), pending=res.get("pending"), nxt=res.get("next"), exit_code=res["exit_code"])


# =============================================================================================
# L4–L6 — feloldás, duplumszűrés, átfedés (a társmodulok futtatói)
# =============================================================================================

_NEXT_MAP = {
    "decide": "list {p} proposals   →   confirm/exclude --target p-…  (vagy: decide {p} --target p-… --value accept)",
    "overlap": "overlap {p}   (tájékoztató), majd: update {p} --dry-run",
    "screen": "list {p} records   →   confirm/exclude --target rec-…|st-… (kizárásnál --reason-code X…)",
    "resolve": "resolve {p}",
    "dedupe": "dedup {p}",
    "merge": "merge {p}",
}


def _next_for(nxt, project):
    if not nxt:
        return nxt
    key = str(nxt).split(" ", 1)[0]
    if key in _NEXT_MAP and str(nxt).strip() == key:
        return _NEXT_MAP[key].replace("{p}", project)
    return nxt


def _from_step(res, step):
    """Egy társmodul eredménye → CLI-boríték."""
    res = res or {}
    code = res.get("exit_code")
    if code is None:
        code = EXIT_ERROR if not res.get("ok", True) else (EXIT_HUMAN if res.get("pending") else EXIT_OK)
    data = dict((k, v) for k, v in res.items() if k not in ("ok", "warnings", "errors", "pending", "next",
                                                           "exit_code"))
    return _env(bool(res.get("ok", code in (0, 3, 4))), data, res.get("warnings"), res.get("errors"),
                res.get("pending"), res.get("next"), code)


def _set_step_after(project, step, env_res, run_id=None):
    code = env_res.get("exit_code", 0)
    status = {0: "done", 4: "needs_human", 3: "done", 1: "failed", 2: "failed"}.get(code, "done")

    def upd(st):
        S.set_step(st, step, status, run_id=run_id, stale_downstream=True)
        for p in env_res.get("pending") or []:
            if p.get("checkpoint") in S.CHECKPOINTS:
                S.set_checkpoint(st, p["checkpoint"], "pending", open_items=p.get("n"))
    S.mutate_state(project, upd)


def cmd_resolve(a):
    from . import resolve
    project = a.project
    S.load_state(project)
    run = S.new_run(project, "resolve", argv=a._argv)
    res = resolve.run_resolve(project, sources=a.sources, offline=a.offline, force=a.force,
                              include_unknown=a.include_unknown)
    out = _from_step(res, "resolve")
    _set_step_after(project, "resolve", out, run.run_id)
    run.finish("done", exit_code=out["exit_code"])
    out["next"] = _next_for(out.get("next"), project) or "dedup %s" % project
    return out


def cmd_dedup(a):
    from . import dedup
    project = a.project
    S.load_state(project)
    res = dedup.run_dedupe(project)
    out = _from_step(res, "dedupe")
    if res.get("ok") is False:
        out["exit_code"] = EXIT_USAGE
    else:
        _set_step_after(project, "dedupe", out)
    out["next"] = ("list %s proposals   (majd confirm/exclude --target p-…; tömegesen: confirm --all-proposals "
                   "--min-score 0.97 --kind same_report)" % project) if out["pending"] else "merge %s" % project
    return out


def cmd_overlap(a):
    from . import overlap
    project = a.project
    S.load_state(project)
    res = overlap.run_overlap(project, level=a.level, csv=a.csv)
    out = _from_step(res, "overlap")
    if res.get("ok") is False:
        out["exit_code"] = EXIT_USAGE
    else:
        _set_step_after(project, "overlap", out)
    out["next"] = _next_for(out.get("next"), project)
    return out


# =============================================================================================
# emberi döntések: confirm / exclude (EP1–EP4)
# =============================================================================================

_RV = re.compile(r"^rv-[a-z0-9][a-z0-9-]{2,80}$")
_CAND = re.compile(r"^(rv-[a-z0-9][a-z0-9-]{2,80})#(c\d{4,5})$")
_PROP = re.compile(r"^p-[a-z0-9][a-z0-9-]{2,80}$")
_REC = re.compile(r"^rec-[a-z0-9][a-z0-9-]{2,80}$")
_STUDY = re.compile(r"^st-\d{4,6}$")


def _screen(project, rec_id, level, value, actor, reason_code=None, reason=None, batch=None):
    """Szűrési döntés (EP4): az ``eligibility.record_screen_decision`` (ha elérhető) ellenőrzi az okkódot. Tömeges
    döntésnél (``batch``) a szűrőfeltétel a döntés ``batch`` mezőjébe kerül (N3) — ezt közvetlenül írjuk (a célt és az
    okkódot a ``_validate_targets`` már ellenőrizte)."""
    if not batch:
        try:
            from .eligibility import record_screen_decision
            return record_screen_decision(project, rec_id, level, value, actor, reason_code=reason_code, reason=reason)
        except ImportError:
            pass
    if level == "full_text" and value == "exclude" and not reason_code:
        raise S.DecisionError("Teljes szöveg szintű kizárásnál kötelező az ok (--reason-code X…; PRISMA 16a).")
    return S.append_decision(project, "screen", ("record", rec_id), value, actor, level=level,
                             reason_code=reason_code, reason=reason, batch=batch, kb_refs=["D-S04-104"])


def _screen_state(project):
    studies = S.read_json(S.path(project, S.FILES["studies"])) or {}
    from .merge import screening_status
    return studies, screening_status(studies.get("records") or [], S.read_decisions(project),
                                     S.load_state(project))


def _study_reports(project, sid, studies):
    from .merge import branch_membership, effective_reviews
    revs = effective_reviews(S.load_reviews(project), S.read_decisions(project))
    br = branch_membership(studies.get("records") or [], revs)
    st = next((s for s in studies.get("studies") or [] if s.get("study_id") == sid), None)
    if st is None:
        raise S.DecisionError("Nincs ilyen vizsgálat: %s (list <projekt> studies)." % sid)
    recs = [rp["rec_id"] for rp in st.get("reports") or [] if br.get(rp["rec_id"])]
    if not recs:
        raise S.DecisionError("A(z) %s vizsgálatnak nincs szűrendő (a PRISMA-ágakba számító) közleménye." % sid)
    return recs


def _decide_records(project, rec_ids, value, level, actor, reason_code, reason, batch, out, only_pending=False):
    studies, status = _screen_state(project)
    cmap = {}
    try:
        from . import dedup
        cmap = dedup.canonical_map(studies.get("records") or [])
    except Exception:  # pragma: no cover
        pass
    for rid in rec_ids:
        rid = cmap.get(rid, rid)
        st = status.get(rid) or {}
        ta = st.get("title_abstract") or {}
        ft = st.get("full_text") or {}
        if only_pending and (ft.get("status") == "decided" or (ta.get("status") == "decided" and
                                                                 ta.get("value") == "exclude")):
            continue  # tömeges döntés nem írja felül a korábbi emberi döntést
        levels = []
        if value in ("include",):
            if level in ("both", "title_abstract") and not (ta.get("status") == "decided" and ta.get("value") ==
                                                            "include"):
                levels.append(("title_abstract", "include", None, reason))
            if level in ("both", "full_text"):
                levels.append(("full_text", "include", None, reason))
        else:
            if level == "title_abstract":
                levels.append(("title_abstract", value, reason_code, reason))
            else:
                if not (ta.get("status") == "decided" and ta.get("value") in ("include", "unclear")):
                    levels.append(("title_abstract", "include", None,
                                   "Cím/absztrakt szinten továbbengedve (a teljes szöveg szintű döntés előfeltétele)."))
                levels.append(("full_text", value, reason_code, reason))
        for lvl, val, rc, rs in levels:
            d = _screen(project, rid, lvl, val, actor, reason_code=rc, reason=rs, batch=batch)
            out.append({"target": rid, "kind": "screen", "level": lvl, "value": val, "decision_id": d["decision_id"]})


def _validate_targets(project, targets, value, reason, reason_code, level, raw_value=None):
    """Első fázis: minden cél és feltétel ellenőrzése ÍRÁS ELŐTT (egy hibás tétel miatt ne maradjon félig rögzített
    döntéssor)."""
    state = S.load_state(project)
    codes = [r.get("code") for r in state.get("exclusion_reasons") or []]
    if reason_code is not None and codes and reason_code not in codes:
        raise UsageError("Ismeretlen kizárási ok: %s (a szótárban: %s)." % (reason_code, ", ".join(codes)))
    studies = None
    for t in targets:
        m_c = _CAND.match(t)
        if m_c:
            rv = S.load_review(project, m_c.group(1))
            if not any(c.get("cand_id") == m_c.group(2) for c in rv.get("candidates") or []):
                raise UsageError("Nincs ilyen jelölt: %s (list <projekt> candidates)." % t)
            if value != "include" and not (reason or reason_code):
                raise UsageError("A jelölt elvetéséhez írd le az okát (--reason \"…\").")
        elif _RV.match(t):
            S.load_review(project, t)
            if value != "include" and not (reason or reason_code):
                raise UsageError("Az áttekintés kizárásához add meg az okát (--reason \"más PICO\" / \"újabb változata "
                                 "van\" / \"nem szisztematikus\" / \"visszavont\" / …).")
        elif _PROP.match(t):
            doc = S.read_json(S.path(project, S.FILES["studies"])) or {}
            prop = next((p for p in doc.get("proposals") or [] if p.get("proposal_id") == t), None)
            if prop is None:
                raise UsageError("Nincs ilyen javaslat: %s (list <projekt> proposals)." % t)
            if prop.get("kind") == "resolution" and value == "include" and \
                    not str(raw_value or "").lower().startswith(_RESOLUTION_PREFIXES):
                raise UsageError("Feloldási javaslatnál add meg, melyik közleményt fogadod el: --value option:<N> "
                                 "(a javaslat lehetőségei közül) vagy --value pmid:<szám>; elutasítás: exclude/decide "
                                 "--value reject. Az azonosítót a program API-val ellenőrzi (N1).")
        elif _REC.match(t) or _STUDY.match(t):
            if studies is None:
                studies = S.read_json(S.path(project, S.FILES["studies"])) or {}
            if _REC.match(t) and not any(r.get("rec_id") == t for r in studies.get("records") or []):
                raise UsageError("Nincs ilyen közlemény (rekord): %s (list <projekt> records)." % t)
            if _STUDY.match(t) and not any(s.get("study_id") == t for s in studies.get("studies") or []):
                raise UsageError("Nincs ilyen vizsgálat: %s (list <projekt> studies)." % t)
            lvl = level or ("both" if value == "include" else "full_text")
            if value == "exclude" and lvl != "title_abstract" and not reason_code and (codes or not reason):
                raise UsageError("Teljes szöveg szintű kizárásnál kötelező az ok kódja (--reason-code X…; PRISMA 16a). "
                                 "A szótár: %s." % ", ".join(codes))
        else:
            raise UsageError("Ismeretlen azonosító: %r (rv-…, rv-…#c…, p-…, rec-…, st-…)." % t)


def _apply_targets(a, value):
    """A ``confirm`` (``value='include'``) és az ``exclude`` (``value='exclude'``/``not_retrieved``/``awaiting``)
    közös végrehajtója. Visszaad: (döntések, figyelmeztetések, mit kell újraszámolni)."""
    project = a.project
    actor = _actor(a)
    targets = _split_ids(a.target)
    decisions, warnings = [], []
    redo = set()
    batch = None
    only_pending = False
    if getattr(a, "all_pending", False):
        # a FRISS állapotból (nem a merged.json-ból, amely elavult lehet) — korábbi emberi döntést nem ír felül
        from . import merge as _m
        studies_doc = S.read_json(S.path(project, S.FILES["studies"]))
        if not studies_doc:
            raise UsageError("Tömeges döntéshez előbb futtasd a resolve és a dedup lépést.")
        merged, _w = _m.build_merged(S.load_state(project), S.load_reviews(project), studies_doc,
                                     S.read_decisions(project))
        only_pending = True
        filt = {"status": "pending", "min_reviews": getattr(a, "min_reviews", None),
                "from_reviews_only": bool(getattr(a, "from_reviews_only", False))}
        for s in merged.get("studies") or []:
            if s.get("status") != "pending":
                continue
            if filt["min_reviews"] and len(s.get("provenance") or []) < int(filt["min_reviews"]):
                continue
            if filt["from_reviews_only"] and "previous_reviews" not in (s.get("found_by") or []):
                continue
            targets.append(s["study_id"])
        batch = S.batch_label(dict(filt, value=value))
    if getattr(a, "all_candidates", False):
        if not a.review:
            raise UsageError("A --all-candidates mellé add meg az áttekintést: --review rv-….")
        rv = S.load_review(project, a.review)
        for c in rv.get("candidates") or []:
            if c.get("status") == "proposed":
                targets.append("%s#%s" % (rv["review_id"], c["cand_id"]))
        batch = S.batch_label({"review": a.review, "status": "proposed", "value": value})
    if getattr(a, "all_proposals", False):
        from . import dedup
        filters = {"kind": a.kind, "min_score": a.min_score, "certainty": a.certainty}
        filters = dict((k, v) for k, v in filters.items() if v is not None)
        ds = dedup.decide_batch(project, "accept" if value == "include" else "reject", actor, reason=a.reason,
                                **filters)
        for d in ds:
            decisions.append({"target": d["target"]["id"], "kind": d["kind"], "value": d["value"],
                              "decision_id": d["decision_id"]})
        if ds:
            redo.add("dedupe")
    if len(targets) > 1 and batch is None:
        batch = S.batch_label({"targets": ",".join(targets[:20]) + ("…" if len(targets) > 20 else ""),
                               "value": value})
    if not targets and not decisions:
        raise UsageError("Adj meg célt: --target <azonosító>[,…] (rv-…, rv-…#c…, p-…, rec-…, st-…), vagy "
                         "tömeges kapcsolót (--all-pending, --all-candidates, --all-proposals).")
    reason = a.reason
    reason_code = getattr(a, "reason_code", None)
    level = getattr(a, "level", None)
    _validate_targets(project, targets, value, reason, reason_code, level, raw_value=getattr(a, "value", None))
    rec_targets = []
    studies = None
    for t in targets:
        m_c = _CAND.match(t)
        if m_c:
            rid, cid = m_c.group(1), m_c.group(2)
            kind = "candidate_confirm" if value == "include" else "candidate_reject"
            if kind == "candidate_reject" and not (reason or reason_code):
                raise UsageError("A jelölt elvetéséhez írd le az okát (--reason \"…\").")
            d = S.append_decision(project, kind, ("candidate", t), "confirm" if value == "include" else "reject", actor,
                                  reason=reason, batch=batch, kb_refs=["D-S03-102"])
            S.apply_candidate_decision(project, rid, cid, "confirm" if value == "include" else "reject",
                                       d["decision_id"])
            decisions.append({"target": t, "kind": kind, "value": d["value"], "decision_id": d["decision_id"]})
            redo.add("extract")
        elif _RV.match(t):
            if value != "include" and not (reason or reason_code):
                raise UsageError("Az áttekintés kizárásához add meg az okát (--reason \"más PICO\" / \"újabb változata "
                                 "van\" / \"nem szisztematikus\" / \"visszavont\" / …).")
            S.load_review(project, t)
            d = S.append_decision(project, "review_select", ("review", t), "include" if value == "include" else
                                  "exclude", actor, reason=reason, reason_code=reason_code, batch=batch,
                                  kb_refs=["D-S03-101"])
            S.apply_review_decision(project, t, "include" if value == "include" else "exclude", d["decision_id"])
            decisions.append({"target": t, "kind": "review_select", "value": d["value"],
                              "decision_id": d["decision_id"]})
            redo.add("select_reviews")
        elif _PROP.match(t):
            from . import dedup
            v = getattr(a, "value", None)
            if v in (None, "", "include", "exclude", "confirm", "accept", "reject", "yes", "no"):
                v = "accept" if value == "include" else "reject"
            d = dedup.decide_proposal(project, t, v, actor, reason=reason, batch=batch)
            decisions.append({"target": t, "kind": d["kind"], "value": d["value"], "decision_id": d["decision_id"]})
            redo.add("resolve" if d["kind"] == "id_confirm" else "dedupe")
        elif _REC.match(t):
            rec_targets.append(t)
        elif _STUDY.match(t):
            if studies is None:
                studies = S.read_json(S.path(project, S.FILES["studies"])) or {}
            rec_targets.extend(_study_reports(project, t, studies))
        else:
            raise UsageError("Ismeretlen azonosító: %r (rv-…, rv-…#c…, p-…, rec-…, st-…)." % t)
    if rec_targets:
        lvl = level or ("both" if value == "include" else "full_text")
        if value != "include" and lvl == "both":
            lvl = "full_text"
        _decide_records(project, rec_targets, value, lvl, actor, reason_code, reason, batch, decisions,
                        only_pending=only_pending)
        redo.add("screen")
    return decisions, warnings, redo


def _after_decisions(project, redo):
    """A döntések utáni újraszámolás és elavulás-jelölés."""
    out_warn = []
    if "dedupe" in redo:
        from . import dedup
        res = dedup.run_dedupe(project)
        out_warn.extend(res.get("warnings") or [])

    reviews = S.load_reviews(project)
    studies = S.read_json(S.path(project, S.FILES["studies"])) or {}
    open_ep2 = sum(1 for r in reviews if r.get("status") == "selected" for c in r.get("candidates") or []
                   if c.get("status") == "proposed")
    open_ep3 = sum(1 for p in studies.get("proposals") or [] if p.get("status") == "pending")

    def upd(st):
        if "select_reviews" in redo:
            S.set_step(st, "select_reviews", "done", stale_downstream=True)
            if any(r.get("status") == "selected" for r in reviews):
                S.set_checkpoint(st, "EP1", "done", open_items=0)
                if (st["steps"].get("find_reviews") or {}).get("status") == "needs_human":
                    S.set_step(st, "find_reviews", "done")
        if "extract" in redo:
            S.set_checkpoint(st, "EP2", "pending" if open_ep2 else "done", open_items=open_ep2)
            if not open_ep2 and (st["steps"].get("extract") or {}).get("status") == "needs_human":
                S.set_step(st, "extract", "done")
        if "dedupe" in redo:
            S.set_checkpoint(st, "EP3", "pending" if open_ep3 else "done", open_items=open_ep3)
            if not open_ep3 and (st["steps"].get("dedupe") or {}).get("status") == "needs_human":
                S.set_step(st, "dedupe", "done")
        if "extract" in redo:
            S.mark_stale(st, "extract")
        if "dedupe" in redo:
            S.mark_stale(st, "dedupe")
        if "resolve" in redo:
            S.set_step(st, "resolve", "stale")
            S.mark_stale(st, "resolve")
        if "screen" in redo:
            S.set_step(st, "screen", "needs_human", stale_downstream=True)
    S.mutate_state(project, upd)
    return out_warn


def _decision_cmd(a, value):
    project = a.project
    S.load_state(project)
    decisions, warnings, redo = _apply_targets(a, value)
    warnings.extend(_after_decisions(project, redo))
    nxt = "merge %s   (az egyesített lista és a PRISMA-számok frissítése)" % project
    if "select_reviews" in redo:
        nxt = "extract %s" % project
    elif "resolve" in redo:
        nxt = "resolve %s   (a feloldási döntés alkalmazása; API-val ellenőrizve)" % project
    elif redo == set(["extract"]):
        nxt = "resolve %s" % project
    return _env(True, {"decisions": decisions, "n": len(decisions)}, warnings, nxt=nxt,
                message=_expl("%d döntés rögzítve a decisions.jsonl-ban." % len(decisions),
                              "%d decisions recorded." % len(decisions)))


def cmd_confirm(a):
    return _decision_cmd(a, "include")


#: feloldási javaslatra adható értékek előtagjai (a resolve ``_human_pass`` mindet API-val ellenőrzi)
_RESOLUTION_PREFIXES = ("option:", "pmid:", "doi:", "pmcid:", "nct:", "eid:", "openalex:")

_DECIDE_VALUES = {"no_identifier": "no_identifier", "keep_retracted": "keep_retracted",
                  "accept": "include", "include": "include", "confirm": "include", "yes": "include",
                  "reject": "exclude", "exclude": "exclude", "no": "exclude", "not_retrieved": "not_retrieved",
                  "awaiting": "awaiting"}


def cmd_decide(a):
    """A TERV 14. fejezet általános ``decide`` parancsa: ``--value accept|reject|include|exclude|not_retrieved|
    awaiting`` (feloldási javaslatnál ``pmid:…`` / ``option:N`` is) — a ``confirm``/``exclude`` útján."""
    raw = (a.value or "").strip()
    if raw.lower().startswith(_RESOLUTION_PREFIXES):
        return _decision_cmd(a, "include")
    if raw not in _DECIDE_VALUES:
        raise UsageError("A --value: accept | reject | include | exclude | not_retrieved | awaiting | no_identifier | "
                         "keep_retracted (feloldási javaslatnál: pmid:… / doi:… / pmcid:… / nct:… / option:N).")
    kind = _DECIDE_VALUES[raw]
    if kind == "no_identifier":
        return _no_identifier(a)
    if kind == "keep_retracted":
        return _keep_retracted(a)
    a.value = None
    if kind == "include":
        return _decision_cmd(a, "include")
    if kind != "exclude" and (a.level or "full_text") != "full_text":
        raise UsageError("A 'nem elérhető' / 'elbírálásra vár' csak teljes szöveg szinten adható.")
    return _decision_cmd(a, kind)


def _no_identifier(a):
    """Emberi nyilatkozat: a közleménynek nincs API-ban elérhető azonosítója (H003 nyugtázása). Csak feloldatlan
    rekordra, indoklással (hol ellenőrizted: könyvtári katalógus, a folyóirat archívuma …)."""
    project = a.project
    actor = _actor(a)
    targets = _split_ids(a.target)
    if not targets or not all(_REC.match(t) for t in targets):
        raise UsageError("A --value no_identifier célja feloldatlan közlemény-rekord (rec-…).")
    if not a.reason:
        raise UsageError("Írd le, hol ellenőrizted, hogy nincs azonosító (--reason \"…\").")
    studies = S.read_json(S.path(project, S.FILES["studies"])) or {}
    recs = dict((r.get("rec_id"), r) for r in studies.get("records") or [])
    out = []
    for t in targets:
        r = recs.get(t)
        if r is None:
            raise UsageError("Nincs ilyen közlemény (rekord): %s." % t)
        if (r.get("resolution") or {}).get("status") == "resolved":
            raise UsageError("%s API-val feloldott — a nyilatkozat csak feloldatlan rekordra adható." % t)
    for t in targets:
        d = S.append_decision(project, "id_confirm", ("record", t), "no_identifier", actor, reason=a.reason,
                              kb_refs=["D-S03-103"])
        out.append({"target": t, "kind": "id_confirm", "value": "no_identifier", "decision_id": d["decision_id"]})
    return _env(True, {"decisions": out, "n": len(out)}, nxt="merge %s" % project,
                message=_expl("Rögzítve: nincs API-azonosító (%d rekord)." % len(out),
                              "Recorded: no API identifier (%d records)." % len(out)))


def _keep_retracted(a):
    """Emberi döntés: a visszavont (retracted) közleményű vizsgálatot tudatosan a bevont halmazban tartod (H013) —
    indoklás kötelező (pl. „csak érzékenységi elemzésben, a visszavonás oka nem az adatok hibája"). Enélkül a
    lezárás (EP5) nem lehetséges."""
    project = a.project
    actor = _actor(a)
    targets = _split_ids(a.target)
    if not targets or not all(_STUDY.match(t) for t in targets):
        raise UsageError("A --value keep_retracted célja vizsgálat (st-…).")
    if not (a.reason and a.reason.strip()):
        raise UsageError("Írd le, miért tartod meg a visszavont közleményű vizsgálatot (--reason \"…\").")
    merged = S.read_json(S.path(project, S.FILES["merged"])) or {}
    by = dict((st.get("study_id"), st) for st in merged.get("studies") or [])
    for t in targets:
        st = by.get(t)
        if st is None:
            raise UsageError("Nincs ilyen vizsgálat az egyesített halmazban: %s (futtasd: merge)." % t)
        if "retracted" not in (st.get("flags") or []):
            raise UsageError("%s nem tartalmaz visszavont közleményt — erre nincs szükség." % t)
    out = []
    for t in targets:
        d = S.append_decision(project, "final_inclusion", ("study", t), "keep_retracted", actor, reason=a.reason,
                              level="final", kb_refs=["D-S04-103"])
        out.append({"target": t, "kind": "final_inclusion", "value": "keep_retracted", "decision_id": d["decision_id"]})
    return _env(True, {"decisions": out, "n": len(out)}, nxt="merge %s" % project,
                message=_expl("Rögzítve: a visszavont közleményű vizsgálat(ok) tudatos megtartása (%d)." % len(out),
                              "Recorded: retracted study kept deliberately (%d)." % len(out)))


def cmd_exclude(a):
    value = "exclude"
    if a.not_retrieved:
        value = "not_retrieved"
    elif a.awaiting:
        value = "awaiting"
    if value != "exclude" and (a.level or "full_text") != "full_text":
        raise UsageError("A 'nem elérhető' / 'elbírálásra vár' csak teljes szöveg szinten adható.")
    return _decision_cmd(a, value)


# =============================================================================================
# L8 — frissítés; L9 — egyesítés, PRISMA, lezárás, EP6, export
# =============================================================================================

def cmd_update(a):
    from . import update as U
    project = a.project
    S.load_state(project)
    qbs = None
    query = a.query
    if a.query_file:
        q = _load_json_file(a.query_file, "lekérdezés")
        if isinstance(q, dict) and any(k in q for k in U.UPDATE_SOURCES):
            qbs = dict((k, v) for k, v in q.items() if isinstance(v, str))
        else:
            query = q
    try:
        if a.dry_run:
            plan = U.plan_update(project, anchor=a.anchor, overlap_months=a.overlap_months, start=a.start, end=a.end,
                                 sources=a.sources, query=query, query_by_source=qbs)
            ws = plan["window"].pop("warnings", [])
            return _env(True, {"plan": plan}, ws, nxt="update %s --actor user:<név>   (az ablak jóváhagyása és a "
                                                      "keresés futtatása)" % project,
                        message=_expl("Terv (nem futott keresés).", "Plan only (no search run)."))
        run = S.new_run(project, "update_search", argv=a._argv)
        res = U.run_update(project, actor=a.actor if a.actor and a.actor.startswith("user:") else None,
                           anchor=a.anchor, overlap_months=a.overlap_months, start=a.start, end=a.end, cap=a.cap,
                           sources=a.sources, query=query, query_by_source=qbs, offline=a.offline, run=run,
                           progress=run.progress)
        if a.cite and res.get("ok"):
            cres = U.run_cite_search(project, direction=a.cite, seeds=a.seeds, offline=a.offline, run=run)
            res["warnings"] = (res.get("warnings") or []) + (cres.get("warnings") or [])
            res.setdefault("data", {})["citation_search"] = cres.get("data")
            res["pending"] = cres.get("pending") or res.get("pending")
            res["exit_code"] = max(res.get("exit_code", 0), cres.get("exit_code", 0)) \
                if 3 not in (res.get("exit_code"), cres.get("exit_code")) else 3
        run.finish("done" if res.get("ok") else "needs_human", sources=(res.get("data") or {}).get("sources"),
                   exit_code=res.get("exit_code"))
    except U.UpdateError as exc:
        if exc.code in ("BAD_REQUEST", "HH_NO_SEARCH_DATE", "HH_NO_SEEDS"):
            raise UsageError(exc.hu, exc.en)
        raise
    out = _env(res.get("ok"), res.get("data"), res.get("warnings"), res.get("errors"), res.get("pending"),
               res.get("next"), res.get("exit_code", 0), res.get("message"))
    return out


def cmd_cite(a):
    from . import update as U
    project = a.project
    S.load_state(project)
    run = S.new_run(project, "update_search", argv=a._argv)
    try:
        res = U.run_cite_search(project, direction=a.direction, seeds=a.seeds, sources=a.sources, offline=a.offline,
                                run=run)
    except U.UpdateError as exc:
        raise UsageError(exc.hu, exc.en)
    run.finish("done", exit_code=res.get("exit_code"))
    return _env(res.get("ok"), res.get("data"), res.get("warnings"), res.get("errors"), res.get("pending"),
                res.get("next"), res.get("exit_code", 0))


def cmd_merge(a):
    from . import merge
    S.load_state(a.project)
    res = merge.run_merge(a.project)
    return _env(res["ok"], res.get("data"), res.get("warnings"), res.get("errors"), res.get("pending"),
                res.get("next"), res.get("exit_code", 0))


def cmd_prisma(a):
    from . import prisma_map
    S.load_state(a.project)
    res = prisma_map.run_prisma(a.project)
    flow = res["flow"]
    boxes = dict((k, v) for k, v in flow.items() if k not in ("schema", "source", "hh"))
    code = EXIT_OK if res["ok"] else EXIT_ERROR
    return _env(res["ok"], {"file": res["file"], "boxes": boxes, "summary": res["summary"],
                            "findings": res["findings"], "check_command": res["check_command"],
                            "footnotes": {"other_methods_title_excluded": flow["hh"].get("other_methods_title_excluded"),
                                          "already_known": flow["hh"].get("already_known"),
                                          "citations_total": flow["hh"].get("citations_total")}},
                res.get("warnings"), exit_code=code,
                nxt="Motor-ellenőrzés: %s" % res["check_command"])


def cmd_signoff(a):
    from . import merge
    S.load_state(a.project)
    actor = _actor(a)
    res = merge.signoff(a.project, actor, reason=a.reason)
    return _env(res["ok"], res.get("data"), res.get("warnings"), res.get("errors"), res.get("pending"),
                res.get("next") or "export %s --outcome <kimenet> --to-project" % a.project, res.get("exit_code", 0))


def cmd_verify_secondary(a):
    from . import merge
    S.load_state(a.project)
    actor = _actor(a)
    pv = a.primary_value
    if pv is not None:
        try:
            pv = float(pv.replace(",", "."))
        except ValueError:
            pass
    d = merge.verify_secondary(a.project, a.study, a.field, a.status, actor, review_id=a.review,
                               evidence_id=a.evidence, outcome=a.outcome, arm=a.arm,
                               primary_locator=a.primary_locator, primary_value=pv, reason=a.reason)
    return _env(True, {"decision_id": d["decision_id"], "study_id": a.study, "field": a.field, "status": a.status},
                nxt="merge %s" % a.project)


def cmd_export(a):
    from . import merge
    S.load_state(a.project)
    actor = _actor(a, required=bool(a.force))
    res = merge.run_export(a.project, outcome=a.outcome, to_project=a.to_project, prisma=a.prisma,
                           for_analysis=a.for_analysis, force=a.force, actor=actor)
    return _env(res["ok"], res.get("data"), res.get("warnings"), res.get("errors"), res.get("pending"),
                res.get("next"), res.get("exit_code", 0))


# =============================================================================================
# status, verify
# =============================================================================================

_NEXT_BY_STEP = (
    ("find_reviews", "find {p}"),
    ("select_reviews", "list {p} reviews   →   confirm {p} --target rv-… --actor user:<név>"),
    ("extract", "extract {p}"),
    ("resolve", "resolve {p}"),
    ("dedupe", "dedup {p}"),
    ("update_search", "update {p} --dry-run   →   update {p} --actor user:<név>"),
    ("merge", "merge {p}"),
)


def _checks(project, for_analysis=False):
    from . import checks as C
    res = C.verify(project, for_analysis=for_analysis)
    return res["findings"], res["checker"]


def cmd_status(a):
    from .merge import checkpoint_items, effective_reviews
    project = a.project
    state = S.load_state(project)
    decisions = S.read_decisions(project)
    reviews = S.load_reviews(project)
    studies = S.read_json(S.path(project, S.FILES["studies"]))
    merged = S.read_json(S.path(project, S.FILES["merged"]))
    update_doc = S.read_json(S.path(project, S.FILES["update"]))
    eff = effective_reviews(reviews, decisions)
    items = checkpoint_items(merged, reviews, decisions)
    if not merged:
        sel = [r for r in eff if r.get("status") == "selected"]
        items["EP2"] = sum(1 for r in sel for c in r.get("candidates") or [] if c.get("status") == "proposed")
        items["EP3"] = sum(1 for p in (studies or {}).get("proposals") or [] if p.get("status") == "pending")
    findings, checker = _checks(project)
    steps = dict((s, (state["steps"].get(s) or {}).get("status")) for s in S.STEPS)
    nxt = None
    for ep in ("EP1", "EP2", "EP3", "EP4"):
        if items.get(ep):
            nxt = {"EP1": "list {p} reviews   →   confirm {p} --target rv-… --actor user:<név>",
                   "EP2": "list {p} candidates --status proposed   →   confirm/exclude --target rv-…#c…",
                   "EP3": "list {p} proposals   →   confirm/exclude --target p-…",
                   "EP4": "list {p} studies --status pending   →   confirm/exclude --target st-…"}[ep]
            break
    if nxt is None:
        for step, cmd in _NEXT_BY_STEP:
            if steps.get(step) in ("not_started", "stale", "failed"):
                if step == "select_reviews" and any(r.get("status") == "selected" for r in eff):
                    continue
                if step == "find_reviews" and reviews:
                    continue
                nxt = cmd
                break
    if nxt is None:
        if not (merged or {}).get("final"):
            nxt = "signoff {p} --actor user:<név>" if merged else "merge {p}"
        elif items.get("EP6"):
            nxt = "verify-secondary {p} --study st-… --field … --status verified --primary-locator … --actor user:<név>"
        else:
            nxt = "export {p} --outcome <kimenet> --to-project"
    nxt = nxt.replace("{p}", project)
    pending = [{"checkpoint": ep, "n": n, "what": S.CHECKPOINT_INFO[ep][a.lang if a.lang == "en" else "hu"]}
               for ep, n in sorted(items.items()) if n and (ep != "EP5" or merged)]
    data = {"mode": state.get("mode"), "question": (state.get("pico") or {}).get("question"), "steps": steps,
            "checkpoints": dict((ep, n) for ep, n in sorted(items.items())),
            "reviews": {"total": len(reviews), "selected": sum(1 for r in eff if r.get("status") == "selected")},
            "records": len([r for r in (studies or {}).get("records") or [] if r.get("status") != "merged_into"]),
            "studies": len((studies or {}).get("studies") or []),
            "merged": (merged or {}).get("counts"), "final": bool((merged or {}).get("final")),
            "decisions": len(decisions), "update_window": (update_doc or {}).get("window", {}).get("start_date"),
            "sources": dict((k, v.get("status")) for k, v in (state.get("sources") or {}).items()),
            "findings": findings, "checker": checker}
    errs = [f for f in findings if f["severity"] == "error"]
    code = EXIT_ERROR if errs else (EXIT_HUMAN if any(p["checkpoint"] in ("EP1", "EP2", "EP3", "EP4")
                                                       for p in pending) else EXIT_OK)
    return _env(not errs, data, [{"code": f["code"], "hu": f["hu"], "en": f["en"]} for f in findings
                                 if f["severity"] != "error"],
                [{"code": f["code"], "hu": f["hu"], "en": f["en"]} for f in errs], pending, nxt, code)


def cmd_verify(a):
    project = a.project
    S.load_state(project)
    findings, checker = _checks(project, for_analysis=bool(getattr(a, "for_analysis", False)))
    errs = [f for f in findings if f.get("severity") == "error"]
    warns = [f for f in findings if f.get("severity") != "error"]
    return _env(not errs, {"findings": findings, "checker": checker, "n_error": len(errs), "n_warning": len(warns)},
                [{"code": f.get("code"), "hu": f.get("hu") or f.get("title"), "en": f.get("en") or f.get("title")}
                 for f in warns],
                [{"code": f.get("code"), "hu": f.get("hu") or f.get("title"), "en": f.get("en") or f.get("title")}
                 for f in errs], exit_code=EXIT_ERROR if errs else EXIT_OK,
                nxt=next((f["suggested_command"][0] for f in errs + warns if f.get("suggested_command")), None))


# =============================================================================================
# a TERV 14. fejezet további parancsai: reviews, select-reviews, proposals, screen, rebuild, report
# =============================================================================================

def cmd_reviews(a):
    a.what = "reviews"
    a.review = None
    return cmd_list(a)


def cmd_select_reviews(a):
    """EP1 egy lépésben: ``--include rv-…,… --exclude rv-…,… --reason …`` (minden cél ellenőrzése írás előtt)."""
    project = a.project
    S.load_state(project)
    inc, exc = _split_ids(a.include), _split_ids(a.exclude)
    both = set(inc) & set(exc)
    if both:
        raise UsageError("Ugyanaz az áttekintés nem lehet egyszerre bevont és kizárt: %s." % ", ".join(sorted(both)))
    if not inc and not exc:
        raise UsageError("Adj meg legalább egy áttekintést: --include rv-…,… és/vagy --exclude rv-…,… (--reason …).")
    for t in inc + exc:
        if not _RV.match(t):
            raise UsageError("Az EP1 célja áttekintés-azonosító (rv-…): %r." % t)
    _actor(a)
    _validate_targets(project, inc, "include", a.reason, None, None)
    _validate_targets(project, exc, "exclude", a.reason, getattr(a, "reason_code", None), None)
    decisions, warnings = [], []
    redo = set()
    for targets, value in ((inc, "include"), (exc, "exclude")):
        if not targets:
            continue
        a.target = ",".join(targets)
        d, w, r = _apply_targets(a, value)
        decisions.extend(d)
        warnings.extend(w)
        redo |= r
    warnings.extend(_after_decisions(project, redo))
    return _env(True, {"decisions": decisions, "n": len(decisions)}, warnings, nxt="extract %s" % project,
                message=_expl("%d áttekintés-döntés rögzítve." % len(decisions),
                              "%d review decisions recorded." % len(decisions)))


def cmd_proposals(a):
    S.load_state(a.project)
    a.what = "proposals"
    rows = _list(a)
    if a.kind:
        rows = [r for r in rows if r.get("kind") == a.kind]
    pend = sum(1 for r in rows if r.get("status") == "pending")
    return _env(True, {"what": "proposals", "rows": rows, "n": len(rows)},
                pending=[{"checkpoint": "EP3", "n": pend}] if pend else [],
                nxt=("confirm/exclude %s --target p-…   (tömegesen: confirm --all-proposals --kind same_report "
                     "--min-score 0.97)" % a.project) if pend else "merge %s" % a.project)


def cmd_screen(a):
    from . import eligibility as E
    project = a.project
    S.load_state(project)
    if a.action == "list":
        rows = E.screening_list(project)
        return _env(True, {"what": "records", "rows": rows, "n": len(rows)},
                    nxt="confirm/exclude %s --target rec-…|st-… (kizárásnál --reason-code X…)" % project)
    if a.action == "import":
        if not a.file:
            raise UsageError("Add meg a CSV-t: screen <projekt> import <fájl.csv> (rec_id;level;decision;"
                             "reason_code;actor).")
        res = E.import_screening_csv(project, a.file)
        _after_decisions(project, {"screen"})
        errs = [dict(e, code="CSV") for e in res.get("errors") or []]
        return _env(not errs, {"imported": res.get("imported"), "n": len(res.get("imported") or [])},
                    errors=errs, exit_code=EXIT_USAGE if errs and not res.get("imported") else EXIT_OK,
                    nxt="merge %s" % project)
    agent_doc = _load_json_file(a.file, "ágens-javaslat") if a.file else None
    res = E.run_screen_propose(project, agent_doc=agent_doc)
    out = _from_step(res, "screen")
    _set_step_after(project, "screen", out)
    out["next"] = ("screen %s list   →   confirm/exclude --target rec-…|st-… --actor user:<név>" % project) \
        if out.get("pending") else "update %s --dry-run" % project
    return out


def cmd_rebuild(a):
    """A ``studies.json`` (és ha volt, a ``merged.json``) újraépítése a meglévő rekordokból és a döntésnaplóból
    — hálózat nélkül."""
    from . import dedup, merge
    project = a.project
    S.load_state(project)
    res = dedup.run_dedupe(project)
    out = _from_step(res, "dedupe")
    if S.read_json(S.path(project, S.FILES["merged"])):
        m = merge.run_merge(project)
        out["warnings"] = list(out.get("warnings") or []) + list(m.get("warnings") or [])
        out["data"] = dict(out.get("data") or {}, merged=(m.get("data") or {}).get("counts"))
    out["next"] = "status %s" % project
    return out


def cmd_report(a):
    from . import report
    S.load_state(a.project)
    res = report.write_report(a.project, lang=a.lang)
    return _env(True, {"file": res["file"], "sections": res["sections"]}, res.get("warnings"),
                nxt="verify %s" % a.project, message=_expl("Jelentés: %s" % res["file"], "Report: %s" % res["file"]))


# =============================================================================================
# argparse
# =============================================================================================

def _common():
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("közös kapcsolók")
    g.add_argument("--json", action="store_true", help="gépi JSON-boríték a kimeneten")
    g.add_argument("--lang", choices=("hu", "en"), default="hu", help="üzenetek nyelve (alap: hu)")
    g.add_argument("--offline", action="store_true", help="hálózat nélkül, csak a gyorsítótárból")
    g.add_argument("--sources", default=None, help="forráslista erre a futásra (pl. pubmed,europepmc)")
    g.add_argument("--actor", default=None, help="ki dönt: user:<név> (emberi döntésnél kötelező)")
    g.add_argument("--quiet", action="store_true", help="csak a lényeg (szöveges módban)")
    return p


def build_parser(parser_class=None):
    common = _common()
    p = (parser_class or argparse.ArgumentParser)(
        prog=PROG, formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Metaheadhunter — meglévő metaanalízisek bányászata: felkutatás, a bevont vizsgálatok kinyerése "
                    "bizonyítékkal, azonosítás, duplumszűrés, kizárás a saját PICO szerint, egyesítés és frissítés "
                    "az újabb irodalommal. Minden döntést ember hoz; a program javasol és naplóz.",
        epilog="Kilépési kódok: 0 rendben · 1 hiba · 2 használati hiba · 3 forrás nem érhető el (részleges) · "
               "4 emberi döntésre vár.\nRészletek: TERV_metaheadhunter.md")
    sub = p.add_subparsers(dest="cmd", metavar="<parancs>")

    def add(name, func, help_, aliases=(), project=True, project_optional=False):
        sp = sub.add_parser(name, parents=[common], help=help_, description=help_, aliases=list(aliases))
        if project:
            if project_optional:
                sp.add_argument("project", nargs="?", default=None, help="a projektmappa (elhagyható)")
            else:
                sp.add_argument("project", help="a projektmappa")
        sp.set_defaults(func=func)
        return sp

    sp = add("init", cmd_init, "a Metaheadhunter indítása a projektben (PICO, kizárási okok, források)")
    sp.add_argument("--question", help="a kutatási kérdés (kötelező, ha nincs --pico)")
    sp.add_argument("--pico", help="PICO JSON-fájl ({question, population, …, query_blocks} vagy {pico, criteria, "
                                   "exclusion_reasons})")
    sp.add_argument("--population", help="populáció keresőkifejezései (';' elválasztva)")
    sp.add_argument("--intervention", help="beavatkozás/expozíció keresőkifejezései (';' elválasztva)")
    sp.add_argument("--comparator", help="összehasonlítás (opcionális)")
    sp.add_argument("--outcomes", help="kimenetek (';' elválasztva)")
    sp.add_argument("--study-designs", dest="study_designs", help="vizsgálattípusok (';' elválasztva)")
    sp.add_argument("--mode", choices=("harvest", "own_update"), default="harvest",
                    help="harvest: más áttekintések bányászata; own_update: a saját korábbi áttekintésed frissítése")
    sp.add_argument("--force", action="store_true", help="létező állapot felülírása (a döntésnapló megmarad)")

    sp = add("sources", cmd_sources, "a források állapota (és --check próbakérésekkel); ki/bekapcsolás",
             project_optional=True)
    sp.add_argument("--check", action="store_true", help="próbakérés forrásonként")
    sp.add_argument("--enable", help="bekapcsolandó források (pl. scopus,openalex)")
    sp.add_argument("--disable", help="kikapcsolandó források")

    sp = add("find", cmd_find, "L1: szisztematikus áttekintések / metaanalízisek felkutatása", aliases=("find-reviews",))
    sp.add_argument("--query", help="témakifejezés (alap: a PICO fogalomblokkjai)")
    sp.add_argument("--query-file", dest="query_file", help="JSON: fogalomblokkok vagy forrásonkénti lekérdezések")
    sp.add_argument("--since", help="megjelenés legkorábban (ÉÉÉÉ[-HH[-NN]])")
    sp.add_argument("--until", help="megjelenés legkésőbb")
    sp.add_argument("--max", type=int, default=200, help="legfeljebb ennyi találat forrásonként (alap 200)")
    sp.add_argument("--fulltext-dates", dest="fulltext_dates", action="store_true",
                    help="a nyílt teljes szövegből is keres keresési dátumot (lassabb)")

    sp = add("list", cmd_list, "listák: reviews | candidates | proposals | studies | records", aliases=("show",))
    sp.add_argument("what", choices=("reviews", "candidates", "proposals", "studies", "records"))
    sp.add_argument("--status", help="szűrés állapotra (pl. proposed, pending, candidate, all)")
    sp.add_argument("--review", help="egy áttekintés (candidates)")

    sp = add("reviews", cmd_reviews, "a talált áttekintések rangsorral és jelzésekkel (= list reviews)")
    sp.add_argument("--status", help="szűrés állapotra (candidate, selected, excluded, superseded)")

    sp = add("select-reviews", cmd_select_reviews, "EP1: áttekintések kiválasztása/kizárása egy lépésben")
    sp.add_argument("--include", help="bevont áttekintések (rv-…, vesszővel)")
    sp.add_argument("--exclude", help="kizárt áttekintések (rv-…, vesszővel; --reason kötelező)")
    sp.add_argument("--reason", help="indoklás (pl. 'más PICO', 'újabb változata van', 'nem szisztematikus')")
    sp.add_argument("--reason-code", dest="reason_code", help="kizárási ok kódja (opcionális)")

    sp = add("show-text", cmd_show_text, "a teljes szöveg részei az ágensnek (csak kiírja, nem ment; N4)")
    sp.add_argument("--review", required=True, help="rv-…")
    sp.add_argument("--part", default="methods,results,tables",
                    help="részek: abstract,introduction,methods,results,discussion,tables,references")
    sp.add_argument("--max-chars", dest="max_chars", type=int, default=None, help="legfeljebb ennyi karakter")

    sp = add("agent-classify", cmd_agent_classify, "ágens-osztályozás ellenőrzött beolvasása (szó szerinti idézet, "
                                                   "azonosító nélkül; H004)", project=False)
    sp.add_argument("action", choices=("import",))
    sp.add_argument("project", help="a projektmappa")
    sp.add_argument("--review", required=True, help="rv-…")
    sp.add_argument("--file", help="agent_classification/<rv-…>.json (alap)")

    sp = add("proposals", cmd_proposals, "duplikátum-, feloldási és ütközés-javaslatok (EP3)")
    sp.add_argument("--status", help="pending (alap) | accepted | rejected | all")
    sp.add_argument("--kind", help="same_report | same_study | resolution | id_conflict …")

    sp = add("screen", cmd_screen, "L7: szűrés a saját PICO szerint — list | import <csv> | propose", project=False)
    sp.add_argument("project", help="a projektmappa")
    sp.add_argument("action", choices=("list", "import", "propose"))
    sp.add_argument("file", nargs="?", default=None, help="import: CSV (rec_id;level;decision;reason_code;actor); "
                                                          "propose: ágens-javaslat JSON (opcionális)")

    add("rebuild", cmd_rebuild, "studies.json / merged.json újraépítése a döntésnaplóból (hálózat nélkül)")
    add("report", cmd_report, "exports/report.md: összefoglaló jelentés (forrás-áttekintések, PRISMA, átfedés, "
                              "H-kódok)")

    sp = add("extract", cmd_extract, "L3: a bevont vizsgálatok kinyerése bizonyítékkal (EP2)")
    sp.add_argument("--review", help="csak ez/ezek az áttekintés(ek) (rv-…, vesszővel)")
    sp.add_argument("--strategy", choices=("auto", "jats", "reflist", "pdf"), default="auto")
    sp.add_argument("--pdf", help="saját PDF (--strategy pdf); a PDF a helyén marad, csak a hash-e tárolódik")

    sp = add("resolve", cmd_resolve, "L4: azonosítók és metaadatok API-ból (PMID, DOI, PMCID, NCT)")
    sp.add_argument("--force", action="store_true", help="a már feloldottakat is újra")
    sp.add_argument("--include-unknown", dest="include_unknown", action="store_true",
                    help="az 'ismeretlen' szerepű (irodalomjegyzék) jelölteket is")

    add("dedup", cmd_dedup, "L5: duplikátumok és társközlemények (javaslatok, EP3)", aliases=("dedupe",))

    sp = add("overlap", cmd_overlap, "L6: átfedés a forrás-áttekintések között (CCA)")
    sp.add_argument("--level", choices=("study", "report"), default="study")
    sp.add_argument("--csv", action="store_true", help="exports/overlap_matrix.csv is")

    for name, func, hp in (("confirm", cmd_confirm, "emberi megerősítés: áttekintés kiválasztása (EP1), jelölt "
                                                    "megerősítése (EP2), javaslat elfogadása (EP3), bevonás (EP4)"),
                           ("exclude", cmd_exclude, "emberi kizárás/elvetés okkal: áttekintés (EP1), jelölt (EP2), "
                                                    "javaslat (EP3), közlemény/vizsgálat a saját PICO szerint (EP4)"),
                           ("decide", cmd_decide, "általános döntés (--value accept|reject|include|exclude|"
                                                  "not_retrieved|awaiting; feloldásnál pmid:… / option:N)")):
        sp = add(name, func, hp, aliases=(("select",) if name == "confirm" else ()))
        sp.add_argument("--target", help="azonosító(k), vesszővel: rv-…, rv-…#c…, p-…, rec-…, st-…")
        sp.add_argument("--reason", help="indoklás (betegadat nélkül)")
        sp.add_argument("--level", choices=("title_abstract", "full_text", "both") if name != "exclude" else
                        ("title_abstract", "full_text"), default=None,
                        help="szűrési szint közleménynél/vizsgálatnál (confirm alap: both; exclude alap: full_text)")
        sp.add_argument("--value", required=(name == "decide"),
                        help="decide: a döntés; javaslatnál egyedi érték (pl. feloldásnál pmid:… / doi:… / pmcid:… / "
                             "nct:… vagy option:N; feloldatlan rekordnál: no_identifier; visszavont közleményű "
                             "vizsgálat tudatos megtartása: keep_retracted)")
        sp.add_argument("--review", help="--all-candidates mellé: az áttekintés")
        sp.add_argument("--all-pending", dest="all_pending", action="store_true",
                        help="tömegesen: minden függő vizsgálat (merge után; a szűrő a döntésben rögzül)")
        sp.add_argument("--min-reviews", dest="min_reviews", type=int, help="--all-pending: legalább N áttekintés vonta be")
        sp.add_argument("--from-reviews-only", dest="from_reviews_only", action="store_true",
                        help="--all-pending: csak az áttekintésekből bányászottak")
        sp.add_argument("--all-candidates", dest="all_candidates", action="store_true",
                        help="tömegesen: egy áttekintés minden javasolt jelöltje (--review)")
        sp.add_argument("--all-proposals", dest="all_proposals", action="store_true",
                        help="tömegesen: a szűrőnek megfelelő függő duplikátum-javaslatok")
        sp.add_argument("--kind", help="--all-proposals: javaslat-fajta (same_report, same_study …)")
        sp.add_argument("--min-score", dest="min_score", type=float, help="--all-proposals: legalább ekkora pontszám")
        sp.add_argument("--certainty", choices=("probable", "possible"), help="--all-proposals: bizonyosság")
        if name in ("exclude", "decide"):
            sp.add_argument("--reason-code", dest="reason_code", help="kizárási ok kódja (X1…; lásd init kimenetét)")
        if name == "exclude":
            sp.add_argument("--not-retrieved", dest="not_retrieved", action="store_true",
                            help="a teljes szöveg nem szerezhető be (PRISMA F)")
            sp.add_argument("--awaiting", action="store_true", help="elbírálásra vár (pl. szerzőtől kért adat)")

    sp = add("update", cmd_update, "L8: frissítő keresés a forrás-áttekintések keresési dátuma óta",
             aliases=("update-search",))
    sp.add_argument("--anchor", choices=("latest", "earliest", "manual"), default="latest")
    sp.add_argument("--start", help="kézi ablak kezdete (ÉÉÉÉ-HH-NN; --anchor manual)")
    sp.add_argument("--end", help="az ablak vége (alap: ma)")
    sp.add_argument("--overlap-months", dest="overlap_months", type=int, default=None,
                    help="átfedési idő hónapban (alap 6; pragmatikus alapérték)")
    sp.add_argument("--cap", type=int, default=5000, help="felső korlát forrásonként (alap 5000)")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true", help="csak a terv: lekérdezések és dátumok")
    sp.add_argument("--query", help="témakifejezés (alap: a PICO fogalomblokkjai)")
    sp.add_argument("--query-file", dest="query_file", help="JSON: fogalomblokkok vagy forrásonkénti lekérdezések")
    sp.add_argument("--cite", choices=("forward", "backward", "both"), help="hivatkozáskövetés is")
    sp.add_argument("--seeds", choices=("reviews", "included", "both"), default="reviews")

    sp = add("cite", cmd_cite, "hivatkozáskövetés (előre/hátra) a kiválasztott áttekintésekből vagy a bevont "
                               "vizsgálatokból", aliases=("cite-search",))
    sp.add_argument("--direction", choices=("forward", "backward", "both"), default="forward")
    sp.add_argument("--seeds", choices=("reviews", "included", "both"), default="reviews")

    add("merge", cmd_merge, "L9: egyetlen egyesített vizsgálatlista proveniencával + PRISMA-számok")
    sp = add("prisma", cmd_prisma, "prisma_flow.json és a motor ellenőrzése (ma.py prisma check)")
    sp.add_argument("--check", action="store_true", help="(alapértelmezett) ellenőrzés a motorral")
    sp = add("signoff", cmd_signoff, "EP5: végső lezárás (csak nyitott döntés nélkül)")
    sp.add_argument("--reason", help="megjegyzés a lezáráshoz")

    sp = add("verify-secondary", cmd_verify_secondary, "EP6: másodlagos érték ellenőrzése az elsődleges közleménnyel")
    sp.add_argument("--study", required=True, help="st-…")
    sp.add_argument("--field", required=True, help="n1, e1, m1, sd1, n2, e2, m2, sd2, effect …")
    sp.add_argument("--status", required=True, choices=("verified", "discrepant", "not_applicable"))
    sp.add_argument("--review", help="ha több áttekintés közölt értéket: rv-…")
    sp.add_argument("--evidence", help="a másodlagos érték bizonyítéka (ev-…)")
    sp.add_argument("--outcome", help="kimenet az áttekintésben")
    sp.add_argument("--arm", help="kar")
    sp.add_argument("--primary-locator", dest="primary_locator", help="hol ellenőrizted: 'rec-pmid-… p. 5, Table 2'")
    sp.add_argument("--primary-value", dest="primary_value", help="az elsődleges közlemény értéke (eltérésnél)")
    sp.add_argument("--reason", help="megjegyzés")

    sp = add("export", cmd_export, "exportok: studies.json, kimenet-sablon CSV, másodlagos munkalista, RIS")
    sp.add_argument("--outcome", help="a kimenet neve (03_adatok/<kimenet>.csv sablon)")
    sp.add_argument("--to-project", dest="to_project", action="store_true",
                    help="03_adatok/studies.json (+ <kimenet>.csv), csak ha még nem létezik")
    sp.add_argument("--prisma", action="store_true", help="02_szures/prisma_flow.json, csak ha még nem létezik")
    sp.add_argument("--for-analysis", dest="for_analysis", action="store_true",
                    help="csak lezárt halmazból, csak ellenőrzött másodlagos értékkel")
    sp.add_argument("--force", action="store_true", help="létező célfájl felülírása (--actor kötelező, naplózva)")

    add("status", cmd_status, "hol tartasz: lépések, ellenőrzőpontok, H-kódok, következő lépés")
    sp = add("verify", cmd_verify, "gépi ellenőrzések (H001–H020: sémák, bizonyítékok, hash-lánc, szivárgás, teljes "
                                   "szöveg, PRISMA)")
    sp.add_argument("--for-analysis", dest="for_analysis", action="store_true",
                    help="elemzési szigor: az ellenőrizetlen másodlagos adat hiba (H010)")
    return p


# =============================================================================================
# kimenet
# =============================================================================================

def _render_text(cmd, env, lang, quiet=False):
    L = "en" if lang == "en" else "hu"
    lines = []
    head = {0: ("RENDBEN", "OK"), 1: ("HIBA", "ERROR"), 2: ("HASZNÁLATI HIBA", "USAGE ERROR"),
            3: ("RÉSZLEGES (forrás nem érhető el)", "PARTIAL (source unavailable)"),
            4: ("EMBERI DÖNTÉSRE VÁR", "WAITING FOR A HUMAN DECISION")}.get(env["exit_code"], ("?", "?"))
    msg = env.get("message")
    lines.append("[%s] %s%s" % (cmd, head[0] if L == "hu" else head[1],
                                (" — " + msg.get(L, "")) if isinstance(msg, dict) and msg.get(L) else ""))
    data = env.get("data") or {}
    if isinstance(data, dict) and not quiet:
        if data.get("table"):
            lines.append(data["table"])
        if cmd in ("find", "find-reviews") and data.get("reviews"):
            lines.append("")
            lines.append("Talált áttekintések (rangsor; nem minőségítélet):" if L == "hu" else "Reviews found (ranked):")
            for r in data["reviews"][:30]:
                tags = [t for t, on in (("Cochrane", r.get("cochrane")), ("nyílt szöveg" if L == "hu" else "OA",
                                                                          r.get("open_fulltext")),
                                        ("VISSZAVONT" if L == "hu" else "RETRACTED", r.get("retracted")),
                                        ("HIBAJEGYZÉK" if L == "hu" else "ERRATUM", r.get("erratum")),
                                        (r.get("status") if r.get("status") != "candidate" else None,
                                         r.get("status") != "candidate")) if on and t]
                lines.append("  %-24s %5s  %s %s  %s%s" % (r["review_id"], "%.2f" % r["score"] if r.get("score") is not
                                                          None else "-", r.get("first_author") or "",
                                                          r.get("year") or "", (r.get("title") or "")[:90],
                                                          (" [" + ", ".join(tags) + "]") if tags else ""))
            if len(data["reviews"]) > 30:
                lines.append("  … (+%d; list <projekt> reviews)" % (len(data["reviews"]) - 30))
        if data.get("rows") is not None and cmd in ("list", "show"):
            for r in data["rows"][:200]:
                lines.append("  " + json.dumps(r, ensure_ascii=False)[:300])
        plan = data.get("plan")
        if isinstance(plan, dict) and plan.get("window"):
            w = plan["window"]
            lines.append(("Ablak: %s – %s (horgony: %s, átfedés: %s hónap; legfrissebb forrás-keresés: %s)" if L == "hu"
                          else "Window: %s – %s (anchor: %s, overlap: %s months; latest source search: %s)")
                         % (w.get("start_date"), w.get("end_date"), w.get("anchor"), w.get("overlap_months"),
                            w.get("latest_source_search")))
            for pr in w.get("per_review") or []:
                lines.append("  %s: %s%s" % (pr["review_id"], pr.get("search_date"),
                                             (" (becsült)" if L == "hu" else " (estimated)") if pr.get("fallback")
                                             else ""))
            for src, qs in sorted((plan.get("queries") or {}).items()):
                for q in qs:
                    lines.append("  [%s] %s" % (src, q.get("query")))
            for sk in plan.get("skipped") or []:
                lines.append("  [%s] %s" % (sk.get("source"), (sk.get("message") or {}).get(L, sk.get("status"))))
        for key in ("counts", "boxes", "results", "window", "checkpoints", "steps", "merged"):
            if isinstance(data.get(key), dict):
                lines.append("%s: %s" % (key, json.dumps(data[key], ensure_ascii=False)[:600]))
        if isinstance(data.get("decisions"), list):
            for d in data["decisions"][:50]:
                lines.append("  %s  %s → %s (%s)" % (d.get("decision_id"), d.get("target"), d.get("value"),
                                                   d.get("level") or d.get("kind")))
        if isinstance(data.get("reviews"), list) and cmd == "extract":
            for r in data["reviews"]:
                lines.append("  %s: %s, %d jelölt (%d megerősítendő), k=%s, keresési dátum: %s%s" % (
                    r["review_id"], r["status"], r["n_candidates"], r["n_proposed"], r.get("k_reported"),
                    r.get("search_date"), " (becsült)" if r.get("search_date_fallback") else ""))
        if data.get("files"):
            lines.append(("Fájlok: " if L == "hu" else "Files: ") + ", ".join(str(f) for f in data["files"] if f))
        if data.get("check_command"):
            lines.append(("Motor-ellenőrzés: " if L == "hu" else "Engine check: ") + data["check_command"])
    if cmd == "show-text" and isinstance(data, dict) and data.get("text"):
        lines.append(data["text"])
    if cmd == "verify" and isinstance(data, dict) and data.get("findings") and not quiet:
        for f in data["findings"][:60]:
            lines.append("  [%s %s] %s%s" % (f.get("code"), f.get("severity"), f.get(L) or f.get("hu"),
                                             ("  → " + f["suggested_command"][0]) if f.get("suggested_command") else ""))
    if env.get("errors"):
        lines.append("Hibák:" if L == "hu" else "Errors:")
        for e in env["errors"]:
            lines.append("  [%s] %s" % (e.get("code"), e.get(L) or e.get("hu")))
    if env.get("warnings") and not quiet:
        lines.append("Figyelmeztetések:" if L == "hu" else "Warnings:")
        for w in env["warnings"][:40]:
            lines.append("  [%s] %s" % (w.get("code"), w.get(L) or w.get("hu")))
        if len(env["warnings"]) > 40:
            lines.append("  … (+%d)" % (len(env["warnings"]) - 40))
    if env.get("pending"):
        lines.append("Döntésre vár:" if L == "hu" else "Waiting for decisions:")
        for p in env["pending"]:
            info = S.CHECKPOINT_INFO.get(p.get("checkpoint"), {}).get(L, "")
            lines.append("  %s: %s tétel%s" % (p.get("checkpoint"), p.get("n"), (" — " + info) if info else ""))
    if env.get("next"):
        nxt = str(env["next"])
        first = nxt.split(" ", 1)[0]
        if first in _COMMANDS:
            nxt = "%s %s" % (PROG, nxt)
        lines.append(("Következő lépés: " if L == "hu" else "Next: ") + nxt)
    return "\n".join(lines)


_COMMANDS = ("init", "sources", "find", "find-reviews", "list", "show", "reviews", "select-reviews", "show-text",
             "agent-classify", "extract", "resolve", "dedup", "dedupe", "proposals", "overlap", "screen", "confirm",
             "select", "exclude", "decide", "update", "update-search", "cite", "cite-search", "merge", "prisma",
             "signoff", "verify-secondary", "export", "status", "verify", "rebuild", "report")


class _ParseError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """``execute``-hoz: a használati hiba kivétel, nem ``sys.exit`` (a homlokzat és a GUI is hívhatja)."""

    def error(self, message):
        raise _ParseError(message)


def execute(argv):
    """Egy parancs végrehajtása kiírás nélkül. Visszaad: a JSON-boríték (``{ok, command, data, warnings, errors,
    pending, next, exit_code}``) — a ``facade`` és a ``main`` közös magja."""
    argv = [str(x) for x in argv]
    parser = build_parser(parser_class=_Parser)
    try:
        a = parser.parse_args(argv)
    except (_ParseError, SystemExit) as exc:
        env = _env(False, None, errors=[{"code": "USAGE", "hu": "Használati hiba: %s" % exc,
                                         "en": "Usage error: %s" % exc}], exit_code=EXIT_USAGE)
        env["command"] = argv[0] if argv else None
        return env, None
    return _run_parsed(a, argv), a


def main(argv=None, stdout=None):
    """Belépési pont. Visszaad: kilépési kód (0–4)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    out = stdout or sys.stdout
    parser = build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help(out)
        return EXIT_OK if argv else EXIT_USAGE
    try:
        a = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code not in (0, None) else EXIT_OK
    env = _run_parsed(a, argv)
    if a.json:
        text = json.dumps(env, ensure_ascii=False, indent=2, default=str)
    else:
        text = _render_text(env["command"], env, a.lang, quiet=a.quiet)
    try:
        text = _redact_out(text)
    except Exception:  # pragma: no cover
        pass
    out.write(text + "\n")
    return int(env.get("exit_code") or 0)


def _run_parsed(a, argv):
    a._argv = argv
    cmd = argv[0]
    if getattr(a, "project", None):
        a.project = os.path.abspath(a.project)
    try:
        env = a.func(a)
    except UsageError as exc:
        env = _env(False, None, errors=[{"code": "USAGE", "hu": exc.hu, "en": exc.en}], exit_code=EXIT_USAGE)
    except S.StateError as exc:
        env = _env(False, None, errors=[{"code": exc.code, "hu": exc.hu, "en": exc.en}],
                   exit_code=EXIT_ERROR if exc.code in ("H016", "H001") else EXIT_USAGE,
                   nxt="init <projekt> --question \"…\"" if exc.code == "HH_NOT_INITIALIZED" else None)
    except S.DecisionError as exc:
        env = _env(False, None, errors=[{"code": "DECISION", "hu": str(exc), "en": str(exc)}], exit_code=EXIT_USAGE)
    except S.LockError as exc:
        env = _env(False, None, errors=[{"code": "LOCKED", "hu": str(exc), "en": str(exc)}], exit_code=EXIT_ERROR)
    except Exception as exc:  # a dedup/eligibility saját DecisionError-ja és a váratlan hibák
        name = type(exc).__name__
        text = str(exc)
        try:
            from . import net
            text = net.redact(text)
        except Exception:  # pragma: no cover
            pass
        if name in ("DecisionError", "MergeError", "UpdateError", "ValueError"):
            env = _env(False, None, errors=[{"code": getattr(exc, "code", "DECISION") or "DECISION", "hu": text,
                                             "en": text}], exit_code=EXIT_USAGE)
        else:
            env = _env(False, None, errors=[{"code": "INTERNAL", "hu": "Váratlan hiba (%s): %s" % (name, text[:500]),
                                             "en": "Unexpected error (%s): %s" % (name, text[:500])}],
                       exit_code=EXIT_ERROR)
    env["command"] = cmd
    env["warnings"] = _norm_warnings(env.get("warnings"))
    env["errors"] = _norm_errors(env.get("errors"))
    return env


def _redact_out(text):
    """Utolsó védővonal (H016): a kimenetbe került titkok kitakarása."""
    from . import net
    return net.redact(text)


if __name__ == "__main__":
    sys.exit(main())
