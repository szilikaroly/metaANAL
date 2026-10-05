# -*- coding: utf-8 -*-
"""PRISMA composer-módban: a composer állapotának olvasása, csak olvasva (terv 2.4, 3.4, 3.5.14, 4.13, 5.5).

- ``GET /api/prisma/composer`` → ``szk.ma.prisma-composer/v1``: a composer-adapter állapota (verzió, mód,
  H7-őr, teendő), a helyfeloldás (``ma-projekt.json`` ``composer.{outdir, project}``, ennek hiányában a
  ``COMPOSER_OUTDIR``), a PRISMA-fájl mostani módja (kézi / composer) és ETag-je, és hogy frissíthető-e.
- ``PUT /api/prisma/composer/config`` ← ``{outdir, project}`` (+ If-Match: a ma-projekt.json ETag-je) — a composer
  helyének beállítása (a mappának léteznie kell; a projekt-név szigorú mintájú). Naplózva (út nélkül).
- ``POST /api/prisma/composer/refresh`` ← ``{dry_run?, confirm_replace_manual?}`` (+ If-Match: a
  ``02_szures/prisma_flow.json`` ETag-je, ha a fájl létezik) — „Frissítés a composerből” (5.5):
  1. ``[python, composer/scripts/prisma, --outdir D, --project P, export, --format, flow-json, --out <tmp>]``
     (explicit interpreterrel, H7; csonka JSON-nál újrapróbálva);
  2. leképezés ``szk.prisma-flow/v1``-re és a motor P-ellenőrzése (``api.prisma_check``);
  3. ``dry_run``: csak előnézet; különben írás a ``02_szures/prisma_flow.json``-ba ``source.kind: composer``
     jelöléssel (a composer ``status`` figyelmeztetéseivel), majd activity-sor a sha256-tal és a composer-
     verzióval. Kézi számokat csak ``confirm_replace_manual: true`` ír felül (különben 409). A visszaváltás kézi
     módra a PRISMA-képernyő „Kézi felülírás indoklással” útján megy (``PUT /api/prisma/manual``).

A munkapad SOHA nem írja a composer állapotát; számot nem számol (a dobozok ellenőrzése a motoré)."""
from metaelemzes import api

from .. import store
from ..adapters import base as adapter_base
from ..adapters import composer as composer_ad
from ..router import ApiError, Result
from ._common import body_bool, body_str, if_match, log_activity_or_warn, now_iso, phi_doc_guard
from .adapters import raise_for
from .prisma import PRISMA_REL

SCHEMA = "szk.ma.prisma-composer/v1"
REFRESH_SCHEMA = "szk.ma.prisma-composer-refresh/v1"
PROJECT_JSON = "ma-projekt.json"
CONFIG_REQUEST = {
    "type": "object", "required": ["outdir", "project"],
    "properties": {"outdir": {"type": "string", "maxLength": 1024}, "project": {"type": "string", "maxLength": 64}},
    "additionalProperties": False,
}
REFRESH_REQUEST = {
    "type": "object",
    "properties": {"dry_run": {"type": "boolean"}, "confirm_replace_manual": {"type": "boolean"},
                   "client_seq": {"type": "integer", "minimum": 0}},
    "additionalProperties": False,
}


def _i18n(hu, en):
    return {"hu": hu, "en": en}


def _current(app):
    try:
        doc, etag = app.store.load_json(PRISMA_REL)
    except store.StoreError:
        return None, None, "manual"
    if not isinstance(doc, dict):
        return None, etag, "manual"
    src = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    mode = "composer" if src.get("kind") == "composer" or (doc.get("composer_version") and src.get("kind") != "manual") \
        else "manual"
    return doc, etag, mode


def _view(app):
    meta, _etag = app.project_meta_safe()
    loc = composer_ad.location(meta or {}, app.project_root, app.caps.env)
    adapter = composer_ad.ComposerAdapter(app.caps)
    st = adapter.status(loc)
    doc, etag, mode = _current(app)
    src = dict(doc.get("source") or {}) if isinstance(doc, dict) and isinstance(doc.get("source"), dict) else {}
    has_numbers = isinstance(doc, dict) and any(isinstance(doc.get(k), int) for k in composer_ad.COUNT_KEYS)
    reason = None
    if st["state"] not in adapter_base.USABLE_STATES:
        reason = st["remedy"]
    elif not loc["configured"]:
        reason = st["remedy"]
    elif not loc["state_exists"]:
        reason = (loc["problems"] or [None])[-1]
    return {"schema": SCHEMA, "status": st,
            "location": {"configured": loc["configured"], "outdir": loc["outdir"], "project": loc["project"],
                         "source": loc["source"], "state_exists": loc["state_exists"], "problems": loc["problems"]},
            "current": {"path": PRISMA_REL, "mode": mode, "etag": etag, "has_numbers": has_numbers,
                        "source": {k: src.get(k) for k in ("kind", "composer_version", "project", "refreshed",
                                                            "export_sha256", "mode", "actor", "updated")},
                        "status_warnings": [w for w in src.get("status") or [] if isinstance(w, dict)]},
            "can_refresh": bool(st["can_refresh"]), "reason": reason}


def get_composer(req):
    app = req.app
    app.require_open()
    data = _view(app)
    return Result(data, SCHEMA, etag=data["current"]["etag"])


def put_config(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    raw_outdir = body_str(body, "outdir", required=True, max_len=1024)
    project = body_str(body, "project", required=True, max_len=64)
    try:
        composer_ad.check_slug(project)
        adapter_base.validate_option_value(raw_outdir.strip())
    except ValueError as exc:
        raise ApiError("BAD_REQUEST", str(exc)) from None
    resolved = composer_ad.resolve_outdir(raw_outdir, app.project_root)
    if resolved is None or not resolved.is_dir():
        raise ApiError("VALIDATION", "A megadott composer-mappa nem létezik vagy nem mappa. Add meg azt a mappát, "
                                     "ahol a composer collect/prisma parancsai dolgoznak (benne a prisma/ almappával).")
    meta, etag = app.project_meta()
    if meta is None:
        raise ApiError("NOT_FOUND", "Nincs ma-projekt.json ebben a projektben; előbb hozd létre a projektet "
                                    "(Projekt → Új projekt).")
    ok, reason = app.can_write_meta(PROJECT_JSON)
    if not ok:
        raise ApiError("FORBIDDEN", reason, {"path": PROJECT_JSON})
    doc = dict(meta)
    doc["composer"] = {"outdir": raw_outdir.strip(), "project": project}
    sha = app.store.write_bytes(PROJECT_JSON, store.json_bytes(doc), if_match=if_match(req) or etag)
    warnings = []
    log_activity_or_warn(app, "prisma.composer_config", warnings, outputs=[(PROJECT_JSON, sha)],
                         details={"project": project, "outdir_exists": True})
    data = _view(app)
    return Result(data, SCHEMA, warnings=warnings, etag=data["current"]["etag"])


def post_refresh(req):
    app = req.app
    app.require_open()
    body = req.json_object({})
    dry = body_bool(body, "dry_run")
    view = _view(app)
    loc = view["location"]
    st = view["status"]
    if st["state"] not in adapter_base.USABLE_STATES:
        raise ApiError("CAPABILITY_MISSING", (st["remedy"] or {}).get("hu") or "A composer plugin nem érhető el.",
                       {"plugin": "composer", "state": st["state"], "todo": st["remedy"]})
    if not loc["configured"]:
        raise ApiError("BAD_REQUEST", "Előbb add meg a composer kimeneti mappáját és a projekt nevét.",
                       {"needs_config": True})
    adapter = composer_ad.ComposerAdapter(app.caps)
    res = raise_for(adapter.export_flow(loc["outdir"], loc["project"]))
    flow = res["data"]["flow"]
    warnings = []
    stat = adapter.status_warnings(loc["outdir"], loc["project"])
    status_list = stat["data"]["warnings"] if stat.get("ok") else []
    if not stat.get("ok"):
        warnings.append("A composer állapot-figyelmeztetései (prisma status) most nem olvashatók.")
    check = api.prisma_check(flow)
    out = {"schema": REFRESH_SCHEMA, "flow": flow, "check": check, "status_warnings": status_list,
           "composer_version": res["data"]["composer_version"], "mode": res["data"]["mode"],
           "export_sha256": res["data"]["raw_sha256"], "attempts": res["data"]["attempts"], "written": False,
           "path": PRISMA_REL}
    if dry:
        return Result(out, REFRESH_SCHEMA, warnings=warnings)
    cur, cur_etag, cur_mode = _current(app)
    if cur_mode == "manual" and view["current"]["has_numbers"] and not body_bool(body, "confirm_replace_manual"):
        raise ApiError("CONFLICT", "A PRISMA-számok most kézi bevitelből jönnek; a composer-mód felülírja őket. Ha ezt "
                                   "akarod, erősítsd meg (a kézi számok a tevékenységnapló korábbi változatában "
                                   "megmaradnak).", {"needs_confirm": True, "path": PRISMA_REL})
    im = if_match(req)
    if cur_etag is not None and im is None:
        raise ApiError("BAD_REQUEST", "A PRISMA-fájl már létezik: a frissítéshez az If-Match fejléc kell (a GET "
                                      "/api/prisma/composer ETag-je), hogy ne írj felül közben történt változást.")
    phi_doc_guard(app, flow, PRISMA_REL, "PRISMA-folyamat (composer)")
    ts = now_iso()
    doc = dict(flow)
    doc["generated"] = flow.get("generated") or ts
    doc["source"] = {"kind": "composer", "composer_version": res["data"]["composer_version"],
                     "project": loc["project"], "refreshed": ts, "actor": app.actor, "mode": res["data"]["mode"],
                     "export_sha256": res["data"]["raw_sha256"], "status": status_list}
    sha = app.store.write_bytes(PRISMA_REL, store.json_bytes(doc), if_match=im if cur_etag is not None else None)
    log_activity_or_warn(app, "prisma.composer_refresh", warnings, outputs=[(PRISMA_REL, sha)],
                         details={"composer_version": res["data"]["composer_version"],
                                  "export_sha256": res["data"]["raw_sha256"], "mode": res["data"]["mode"],
                                  "project": loc["project"], "replaced_manual": cur_mode == "manual" and cur is not None,
                                  "warnings": len(status_list)})
    out.update(written=True, sha256=sha)
    return Result(out, REFRESH_SCHEMA, warnings=warnings, etag=sha)


def register(router):
    router.add("GET", "/api/prisma/composer", get_composer, schema=SCHEMA)
    router.add("PUT", "/api/prisma/composer/config", put_config, schema=SCHEMA, request_schema=CONFIG_REQUEST)
    router.add("POST", "/api/prisma/composer/refresh", post_refresh, schema=REFRESH_SCHEMA,
               request_schema=REFRESH_REQUEST)

