# -*- coding: utf-8 -*-
"""Konszenzus, forgalmi lámpa és a ``rob`` oszlop szinkronja (terv 3.5.10, 4.12 szk.rob-summary/v1, 5.4, 6.5, X003).

- ``GET /api/appraisals/consensus/<unit>/<tool>?target=&a=&b=`` → két független EMBERI értékelés egymás mellett, a
  motor egyezés-számításával (κ CI-vel, tételenkénti eltérések; ``agreement``), a meglévő konszenzus-változattal és a
  kizárt AI-vázlatok listájával (6. döntés: az AI-vázlat sosem értékelő). Kevesebb mint két emberi értékelésnél
  ``ready: false`` (nem hiba). A konszenzus mentése: ``PUT /api/appraisals/<unit>/<tool>?rater=consensus``.
- ``GET /api/appraisals/rob-summary?tool=&outcome=&target=`` → ``szk.rob-summary/v1`` a motorból (vizsgálatonkénti
  doménítéletek, összítélet, a kimenet elsődleges commit-futásának súlyai) — a forgalmi lámpa ebből rajzol.
- ``POST /api/appraisals/rob-sync`` ← ``{tool, outcome | dataset, target?, dry_run, column?}`` (+ If-Match a tábla
  ETag-jével az alkalmazáshoz): a motor javaslata a kinyerési tábla ``rob`` oszlopára; ``dry_run: false``-nál a
  tároló írja (ETag, 409), a cellák eredete ``calculated`` (forrás: az értékelés-fájl), activity érték nélkül.

A szerver itt sem számol: a κ, a súlyok és a CSV-be kerülő ítélet-szöveg a motoré."""
from .. import store
from ..router import ApiError, Result
from . import appraisal_common as C
from . import runs
from ._common import body_bool, body_str, dataset_rel, if_match, log_activity_or_warn, now_iso

CONSENSUS_SCHEMA = "szk.ma.appraisal-consensus-view/v1"
SUMMARY_SCHEMA = "szk.rob-summary/v1"
SYNC_SCHEMA = "szk.ma.rob-sync/v1"
ROB_FIELD = "rob"
MAX_HISTORY = 50

_SYNC_REQ = {"type": "object", "required": ["tool"],
             "properties": {"tool": {"type": "string", "maxLength": 32},
                            "outcome": {"type": ["string", "null"], "maxLength": 64},
                            "dataset": {"type": ["string", "null"], "maxLength": 1024},
                            "target": {"type": ["string", "null"], "maxLength": 64},
                            "dry_run": {"type": "boolean"},
                            "column": {"type": ["string", "null"], "maxLength": 64}},
             "additionalProperties": False}


# ---------------------------------------------------------------------------- konszenzus
def _side(app, s, inst):
    res = None
    try:
        res = C.check(s["doc"], inst, str(app.project_root))
    except (ApiError, ValueError):
        res = None
    return {"rater": s["rater"], "path": s["path"], "etag": s["etag"], "doc": s["doc"], "check": res}


def get_consensus(req):
    app = req.app
    app.require_open()
    unit = C.check_unit(req.params["unit"])
    tool = C.check_tool(req.params["tool"])
    target = C.check_target(req.arg("target"))
    a = C.check_rater(req.arg("a"), required=False, allow_reserved=False)
    b = C.check_rater(req.arg("b"), required=False, allow_reserved=False)
    inst = C.instrument(tool)
    sibs = C.siblings(app, unit, tool, target)
    humans = {s["rater"]: s for s in sibs if C.is_human(s["doc"], s["rater"])}
    excluded = [{"rater": s["rater"], "path": s["path"], "origin": s["doc"].get("origin"),
                 "approved_by": s["doc"].get("approved_by"), "reason": "ai_draft"}
                for s in sibs if s["doc"].get("origin") == "ai_draft"]
    cons = next((s for s in sibs if s["rater"] == C.RATER_CONSENSUS), None)
    order = sorted(humans)
    a = a or (order[0] if order else None)
    b = b or next((r for r in order if r != a), None)
    for r in (a, b):
        if r is not None and r not in humans:
            raise ApiError("NOT_FOUND", "Nincs független emberi értékelés ettől az értékelőtől: %s (AI-vázlat nem "
                                        "számít)." % r, {"human_raters": order})
    data = {"schema": CONSENSUS_SCHEMA, "unit": unit, "tool": tool, "target": target, "human_raters": order,
            "excluded": excluded, "ready": False, "a": None, "b": None, "agreement": None,
            "consensus": {"exists": cons is not None, "path": C.relpath(unit, tool, target, C.RATER_CONSENSUS),
                          "etag": cons["etag"] if cons else None, "doc": cons["doc"] if cons else None},
            "instrument": {"key": inst.get("key") or tool, "name": inst.get("name")}}
    warnings = []
    if a is None or b is None or a == b:
        data["reason"] = ("Konszenzushoz két független emberi értékelés kell ugyanerre az egységre és célra (az "
                          "AI-vázlat nem számít második értékelőnek). Most: %d." % len(order))
        if a is not None:
            data["a"] = _side(app, humans[a], inst)
        return Result(data, CONSENSUS_SCHEMA)
    fn = C.need("consensus")
    agreement = C._call(fn, humans[a]["doc"], humans[b]["doc"], instrument=inst)
    data.update(ready=True, a=_side(app, humans[a], inst), b=_side(app, humans[b], inst), agreement=agreement)
    if excluded:
        warnings.append("%d AI-vázlat kimaradt az egyezés-számításból (6. döntés: nem értékelő)." % len(excluded))
    return Result(data, CONSENSUS_SCHEMA, warnings=warnings)


# ---------------------------------------------------------------------------- forgalmi lámpa
def _studies_doc(app):
    try:
        doc, _etag = app.store.load_json(store.STUDIES_REL)
    except store.StoreError:
        return None
    return doc if isinstance(doc, dict) and not store.studies_problems(doc) else None


def _docs_for(app, tool, target):
    """Az eszköz értékelései (cél szerint szűrve): [{path, rater, unit, doc}] — a motor választja ki vizsgálatonként a
    végső változatot (konszenzus > kész emberi > jóváhagyott AI-vázlat)."""
    out = []
    for f in C.list_files(app):
        if f["tool"] != tool or (target is not None and f["target"] != target):
            continue
        try:
            doc, etag = C.load(app, f["path"])
        except store.StoreError:
            continue
        if isinstance(doc, dict):
            out.append({"path": f["path"], "rater": f["rater"], "unit": C.unit_of(doc) or f["slug"], "etag": etag,
                        "doc": doc})
    return out


def _primary_plot(app, outcome):
    """A kimenet legutóbbi elsődleges commit-futásának plot_data.json-ja (a súlyokhoz) → (plot | None, run_id, stale)."""
    if not outcome:
        return None, None, None
    try:
        rr = runs.list_runs(app, outcome=outcome, primary=True)
    except Exception:                                       # noqa: BLE001 — súly nélkül is rajzolható
        return None, None, None
    if not rr:
        return None, None, None
    run = rr[0]
    try:
        plot = runs._load_artifact(app, runs._file_rel(run, run.get("dir") or "", "plot", "plot_data.json"))
    except ApiError:
        plot = None
    return plot, run.get("run_id"), run.get("stale")


def get_rob_summary(req):
    app = req.app
    app.require_open()
    tool = C.check_tool(req.arg("tool"))
    outcome = req.arg("outcome", max_len=64) or None
    target = C.check_target(req.arg("target")) if req.arg("target") is not None else (
        C.check_target(outcome) if outcome else None)
    C.instrument(tool)
    fn = C.need("rob_summary")
    docs = _docs_for(app, tool, target)
    plot, run_id, stale = _primary_plot(app, outcome)
    res = C._call(fn, [d["doc"] for d in docs], tool=tool, outcome=outcome, plot=plot, studies=_studies_doc(app),
                  paths=[d["path"] for d in docs])
    res = dict(res or {})
    res.setdefault("schema", SUMMARY_SCHEMA)
    res.setdefault("tool", tool)
    res.setdefault("outcome", outcome)
    res["source_run"] = {"run_id": run_id, "stale": stale} if run_id else None
    warnings = []
    if outcome and run_id is None:
        warnings.append("A kimenetnek még nincs elsődleges commit-futása: a súlyozott összesítő súlyok nélkül készült.")
    elif stale:
        warnings.append("A súlyok forrása ELAVULT futás (az adattábla azóta változott, X001): futtasd újra az elemzést.")
    return Result(res, SUMMARY_SCHEMA, warnings=warnings)


# ---------------------------------------------------------------------------- rob oszlop szinkron
def _dataset_of(app, req, body):
    if body.get("dataset"):
        return dataset_rel(req, body["dataset"])
    outcome = body_str(body, "outcome", max_len=64)
    if not outcome:
        raise ApiError("BAD_REQUEST", "Add meg a kimenetet (outcome) vagy az adattáblát (dataset).")
    meta, _ = app.project_meta_safe()
    for oc in (meta or {}).get("outcomes") or []:
        if isinstance(oc, dict) and oc.get("id") == outcome and isinstance(oc.get("data"), str):
            return dataset_rel(req, oc["data"])
    raise ApiError("NOT_FOUND", "A(z) %s kimenetnek nincs adattáblája a ma-projekt.json-ban." % outcome)


def _rob_column(header, column):
    """(oszlopindex | None, oszlopnév) — a meglévő rob oszlop a motor oszlopfelismerésével; új oszlop neve: column."""
    cmap = store.column_map(header)
    name = cmap.get(ROB_FIELD)
    if name is not None and name in header:
        return header.index(name), name
    return None, column or ROB_FIELD


def _proposal(app, tool, target, table, column):
    fn = C.need("rob_sync")
    docs = _docs_for(app, tool, target)
    prop = C._call(fn, [d["doc"] for d in docs], list(table.header), [list(r["cells"]) for r in table.rows],
                   tool=tool, row_uids=table.row_uids, studies=_studies_doc(app), column=column,
                   paths=[d["path"] for d in docs])
    prop = dict(prop or {})
    prop.setdefault("schema", SYNC_SCHEMA)
    changes = []
    uids = set(table.row_uids)
    for ch in prop.get("changes") or ():
        if isinstance(ch, dict) and ch.get("row_uid") in uids and isinstance(ch.get("after"), str):
            changes.append(ch)
    prop["changes"] = changes
    return prop


def _prov_update(app, changes, prov, now):
    cells = [dict(c) for c in (prov.get("cells") or []) if isinstance(c, dict)]
    by_key = {(c.get("row_uid"), c.get("field")): i for i, c in enumerate(cells)}
    for ch in changes:
        entry = {"row_uid": ch["row_uid"], "field": ROB_FIELD, "method": "calculated", "estimated": False,
                 "value_as_entered": None,
                 "source": {"doc": None, "page": None, "locator": ch.get("source") if isinstance(ch.get("source"), str)
                            else None},
                 "extracted_by": app.actor, "extracted_at": now}
        key = (ch["row_uid"], ROB_FIELD)
        if key in by_key:
            old = cells[by_key[key]]
            hist = list(old.get("history") or [])
            hist.append({k: old.get(k) for k in ("method", "value_as_entered", "extracted_by", "extracted_at")})
            entry["history"] = hist[-MAX_HISTORY:]
            cells[by_key[key]] = entry
        else:
            by_key[key] = len(cells)
            cells.append(entry)
    out = dict(prov)
    out["cells"] = cells
    return out


def post_rob_sync(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    tool = C.check_tool(body.get("tool"))
    C.instrument(tool)
    rel = _dataset_of(app, req, body)
    outcome = body_str(body, "outcome", max_len=64)
    target = C.check_target(body.get("target")) if body.get("target") is not None else (
        C.check_target(outcome) if outcome else None)
    column = body_str(body, "column", max_len=64)
    dry = body_bool(body, "dry_run", True)
    table = app.store.load_table(rel)
    prop = _proposal(app, tool, target, table, column)
    idx, name = _rob_column(table.header, column or prop.get("column"))
    data = {"schema": SYNC_SCHEMA, "dataset": rel, "etag": table.etag, "column": name, "column_exists": idx is not None,
            "proposal": prop, "applied": False}
    if dry or not prop["changes"]:
        return Result(data, SYNC_SCHEMA, etag=table.etag)
    im = if_match(req)
    if im is None:
        raise ApiError("BAD_REQUEST", "Az alkalmazáshoz If-Match kell (az előnézetben látott tábla ETag-je).")
    prel = store.provenance_relpath(rel)
    for target_rel in (rel, prel):
        ok, reason = app.can_write(target_rel)
        if not ok:
            raise ApiError("FORBIDDEN", reason, {"path": target_rel})
    header = list(table.header)
    rows = [{"row_uid": r["row_uid"], "cells": list(r["cells"])} for r in table.rows]
    if idx is None:
        header.append(name)
        idx = len(header) - 1
    for r in rows:
        while len(r["cells"]) < len(header):
            r["cells"].append("")
    by_uid = {r["row_uid"]: r for r in rows}
    for ch in prop["changes"]:
        by_uid[ch["row_uid"]]["cells"][idx] = ch["after"]
    prov, prov_etag, _state = app.store.load_provenance(rel)
    new_prov = _prov_update(app, prop["changes"], prov, now_iso())
    saved = app.store.save_table(rel, header, rows, im, provenance=new_prov, provenance_if_match=prov_etag)
    warnings = []
    log_activity_or_warn(app, "appraisal.rob_sync", warnings,
                         outputs=[(rel, saved.etag), (prel, saved.provenance_etag)],
                         details={"tool": tool, "target": target, "n_changes": len(prop["changes"]),
                                  "column_added": not data["column_exists"]})
    data.update(applied=True, etag=saved.etag, provenance_etag=saved.provenance_etag)
    return Result(data, SYNC_SCHEMA, warnings=warnings, etag=saved.etag)


def register(router):
    router.add("GET", "/api/appraisals/consensus/<unit>/<tool>", get_consensus, schema=CONSENSUS_SCHEMA)
    router.add("GET", "/api/appraisals/rob-summary", get_rob_summary, schema=SUMMARY_SCHEMA)
    router.add("POST", "/api/appraisals/rob-sync", post_rob_sync, schema=SYNC_SCHEMA, request_schema=_SYNC_REQ)
