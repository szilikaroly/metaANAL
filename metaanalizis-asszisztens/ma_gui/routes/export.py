# -*- coding: utf-8 -*-
"""Export: determinisztikus audit-csomag és kitakaró pillanatkép (terv 3.4, 3.5.17, 4.17, 7.4, 7.6).

- ``POST /api/export/audit``    ← ``{include?{…}, redact?{…}}`` → ``szk.ma.export-result/v1``:
  ``{kind: "audit", path, name, sha256, bytes, deterministic: true, created, data_class, manifest{…},
  redactions[], excluded[], url, expires_at}``
- ``POST /api/export/snapshot`` ← ``{include?, redact?, ack: true, lang?}`` → ugyanaz, ``kind: "snapshot"``,
  ``network: false``, ``state_time``, ``state_id``. Az ``ack`` kötelező: a felhasználó tudomásul vette, hogy a
  pillanatképet nem tölti fel és nem publikálja (Claude Artifactként sem).
- ``GET /f/x/<doc>/<lejárat>/<aláírás>`` — az elkészült fájl aláírt, 10 perces letöltési címe (token
  nélkül, mint a ``/f/…`` fájl-URL-ek; a ``doc`` az ``_export:<relút>`` URL-kódolt alakja). Csak az
  export-mappákból (``07_ellenorzes/audit/``, ``07_ellenorzes/pillanatkep/``, ``_privat/export/``), csak
  ``.zip`` és ``.snapshot.html``; mindig CSATOLMÁNYKÉNT (``Content-Disposition: attachment``,
  ``application/octet-stream`` a HTML-nél, ``sandbox`` CSP) — a pillanatkép nem fut a munkapad originjén.

Hely: ``07_ellenorzes/audit/<projektállapot napja>/<név>.zip`` és ``07_ellenorzes/pillanatkep/<név>``;
ha az írás-tartás (C osztály, vagy vault által követett B projekt .gitignore nélkül) ezt nem engedi,
``_privat/export/…`` (a vault nem tolja fel); ha az sem írható, 403 az indoklással.

A kitakarás szabályai a ``snapshot.resolve_policy``-ban (C osztályban adattábla → 403). Az export a
tevékenységnaplóba NEM kerül: tisztán a projektállapot függvénye (ugyanaz az állapot ugyanazt a bájtsort
adja — egy naplósor ezt a következő exportnál elrontaná); a szervernaplóba csak a fajtája és mérete."""
from urllib.parse import quote

from .. import audit_export, security, snapshot, store
from ..router import ApiError, Response, Result

SCHEMA = "szk.ma.export-result/v1"
ARTIFACT_PREFIX = "_export:"
EXPORT_ROOTS = (audit_export.OUT_DIR_REL + "/", snapshot.OUT_DIR_REL + "/", "_privat/export/")
EXPORT_SUFFIXES = (".zip", snapshot.SNAPSHOT_SUFFIX)
PRIVATE_EXPORT = "_privat/export"
DOWNLOAD_PREFIX = "/f/x/"
MSG_SIG = "A letöltési hivatkozás lejárt vagy érvénytelen; készítsd el újra az exportot a munkapadban."
MSG_ACK = ("A pillanatkép csak akkor készül el, ha tudomásul veszed: soha nem töltöd fel és nem publikálod "
           "(Claude Artifactként sem). Pipáld be a jelölőnégyzetet, majd próbáld újra.")

_TOGGLES = {"type": ["object", "null"], "additionalProperties": {"type": "boolean"}}
AUDIT_REQUEST = {
    "type": "object",
    "properties": {"include": _TOGGLES, "redact": _TOGGLES},
    "additionalProperties": False,
}
SNAPSHOT_REQUEST = {
    "type": "object",
    "properties": {"include": _TOGGLES, "redact": _TOGGLES, "ack": {"type": "boolean"},
                   "lang": {"enum": ["hu", "en", None]}},
    "additionalProperties": False,
}


def _policy_error(exc):
    return ApiError(exc.code if exc.code in security.ERROR_CODES else "BAD_REQUEST", exc.message)


def _target(app, rel):
    """A kért hely, vagy írás-tartásnál a _privat/export/ alatti → (rel, figyelmeztetések). A szabály közös a
    parancssorral (snapshot.export_target: ``ma.py gui snapshot`` / ``gui audit-export`` ugyanígy dönt)."""
    try:
        return snapshot.export_target(app, rel)
    except snapshot.SnapshotError as exc:
        raise ApiError("FORBIDDEN", exc.message, {"path": rel}) from None


def _download(app, rel):
    """Aláírt letöltési cím (/f/x/<doc>/<lejárat>/<aláírás>) és lejárat."""
    url = app.security.sign_file_url(ARTIFACT_PREFIX + rel, rel, ttl=security.FILE_URL_TTL)
    parsed = app.security.parse_file_url(url)
    return DOWNLOAD_PREFIX + url[len("/f/"):], parsed[1] if parsed else None


def _save(app, rel, data):
    rel, warnings = _target(app, rel)
    app.store.write_bytes(rel, data)
    url, exp = _download(app, rel)
    return rel, url, exp, warnings


def _body(req):
    body = req.json_object() if req.body else {}
    return body if isinstance(body, dict) else {}


def post_audit(req):
    app = req.app
    app.require_open()
    body = _body(req)
    try:
        data, info = audit_export.build(app=app, include=body.get("include"), redact=body.get("redact"))
    except snapshot.SnapshotError as exc:
        raise _policy_error(exc) from None
    rel, url, exp, warnings = _save(app, audit_export.default_rel(info), data)
    app.log("export: audit-csomag (%d bájt)" % info["bytes"])
    out = {"kind": "audit", "path": rel, "url": url, "expires_at": exp}
    out.update(info)
    return Result(out, SCHEMA, warnings=warnings)


def post_snapshot(req):
    app = req.app
    app.require_open()
    body = _body(req)
    if body.get("ack") is not True:
        raise ApiError("BAD_REQUEST", MSG_ACK)
    try:
        html, info = snapshot.build(app=app, include=body.get("include"), redact=body.get("redact"),
                                    lang=body.get("lang") or app.lang)
    except snapshot.SnapshotError as exc:
        raise _policy_error(exc) from None
    rel, url, exp, warnings = _save(app, "%s/%s" % (snapshot.OUT_DIR_REL, info["name"]), html)
    app.log("export: pillanatkép (%d bájt)" % info["bytes"])
    out = {"kind": "snapshot", "path": rel, "url": url, "expires_at": exp}
    out.update(info)
    return Result(out, SCHEMA, warnings=warnings)


def _export_rel(rel):
    """Engedett export-fájl relatív útja (formai ellenőrzéssel) vagy None."""
    if not isinstance(rel, str) or not rel.startswith(EXPORT_ROOTS) or not rel.endswith(EXPORT_SUFFIXES):
        return None
    try:
        security.check_relpath(rel)
    except security.UnsafePath:
        return None
    return rel


def get_download(req):
    app = req.app
    tail = req.path[len(DOWNLOAD_PREFIX):] if req.path.startswith(DOWNLOAD_PREFIX) else ""
    url = "/f/" + tail

    def lookup(doc_id):
        if not doc_id.startswith(ARTIFACT_PREFIX):
            return None
        return _export_rel(doc_id[len(ARTIFACT_PREFIX):])

    hit = app.security.verify_file_url(url, lookup)
    if hit is None:
        raise ApiError("FORBIDDEN", MSG_SIG)
    app.require_open()
    _doc, rel = hit
    try:
        full = app.store.path(rel)
    except store.StoreError:
        raise ApiError("FORBIDDEN", MSG_SIG) from None
    if not full.is_file():
        raise ApiError("NOT_FOUND", "A fájl nem található (törölték vagy áthelyezték).")
    name = full.name
    ctype = "application/zip" if name.endswith(".zip") else "application/octet-stream"
    headers = [("Content-Disposition", "attachment; filename*=UTF-8''%s" % quote(name, safe=""))]
    return Response(None, ctype, kind="api", headers=headers, file_path=full, length=full.stat().st_size)


def register(router):
    router.add("POST", "/api/export/audit", post_audit, schema=SCHEMA, request_schema=AUDIT_REQUEST)
    router.add("POST", "/api/export/snapshot", post_snapshot, schema=SCHEMA, request_schema=SNAPSHOT_REQUEST)
    router.add("GET", "/f/x/<doc>/<exp>/<sig>", get_download)
