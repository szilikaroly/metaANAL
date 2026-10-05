# -*- coding: utf-8 -*-
"""Dokumentum-jegyzék és aláírt fájl-URL-ek (3.4, 4.8 vége, 7.1 T5/T7).

- ``GET/PUT /api/documents`` ↔ ``03_adatok/documents.json`` (``szk.ma.documents/v1``, If-Match).
- ``POST /api/fileurl`` ← ``{doc}`` (jegyzékbeli dokumentum) vagy ``{path}`` (futás-artefaktum a
  ``05_elemzes/`` vagy ``06_kezirat/`` alatt) → ``{url: "/f/<doc>/<lejárat>/<aláírás>", expires_at}``.
- ``GET /f/<doc>/<lejárat>/<aláírás>``: token nélkül, csak érvényes, le nem járt aláírással; a fájl
  csak a jegyzék gyökereiből vagy a futás-artefaktumok közül, kiterjesztés-allowlisttel, a gyökéren
  kívülre mutató symlink nélkül. SVG ``sandbox`` CSP-vel, PDF/kép a ``file`` fejlécekkel.

Az artefaktum-azonosító ``_run:<relút>`` alakú: ``documents.json``-beli azonosító nem lehet ilyen
(az ``^[a-z]…`` minta miatt), így a két forrás nem keverhető össze."""
import os
from pathlib import Path
from urllib.parse import quote

from .. import security, store
from ..router import ApiError, Response, Result
from ._common import body_int, body_str, if_match

SCHEMA = "szk.ma.documents/v1"
FILEURL_SCHEMA = "szk.ma.fileurl/v1"
ARTIFACT_PREFIX = "_run:"
ARTIFACT_ROOTS = ("05_elemzes/", "06_kezirat/")
MSG_SIG = "A fájl-hivatkozás lejárt vagy érvénytelen; nyisd meg újra a munkapadból."
FILEURL_REQUEST = {
    "type": "object",
    "properties": {
        "doc": {"type": ["string", "null"], "maxLength": 300},
        "path": {"type": ["string", "null"], "maxLength": 1024},
        "ttl": {"type": ["integer", "null"]},
    },
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------- jegyzék
def get_documents(req):
    app = req.app
    app.require_open()
    doc, etag = app.store.load_documents()
    return Result(doc, SCHEMA, etag=etag)


def put_documents(req):
    app = req.app
    app.require_open()
    doc = req.json_object()
    ok, reason = app.can_write(store.DOCUMENTS_REL)
    if not ok:
        raise ApiError("FORBIDDEN", reason, {"path": store.DOCUMENTS_REL})
    new_etag = app.store.save_documents(doc, if_match(req))
    warnings = []
    docs = doc.get("docs")
    if app.log_activity("documents.save", outputs=[{"path": store.DOCUMENTS_REL, "sha256": new_etag}],
                        details={"n_docs": len(docs) if isinstance(docs, list) else 0}) is None:
        warnings.append(app.ACTIVITY_WARNING)
    saved, etag = app.store.load_documents()
    return Result(saved, SCHEMA, warnings=warnings, etag=etag)


# ---------------------------------------------------------------------------- feloldás
def _composer_outdir(app):
    meta, _ = app.project_meta_safe()
    comp = meta.get("composer") if isinstance(meta, dict) else None
    outdir = comp.get("outdir") if isinstance(comp, dict) else None
    if not isinstance(outdir, str) or not outdir.strip():
        return None
    p = Path(os.path.expanduser(outdir.strip()))
    return p if p.is_absolute() else app.project_root / p


def _doc_entry(app, doc_id):
    doc, _ = app.store.load_documents()
    for d in doc.get("docs") or []:
        if isinstance(d, dict) and d.get("id") == doc_id:
            return d
    return None


def _artifact_rel(rel):
    """Futás-artefaktum relatív útja (formai ellenőrzéssel) vagy None."""
    if not isinstance(rel, str) or not rel.startswith(ARTIFACT_ROOTS):
        return None
    try:
        security.check_relpath(rel)
    except security.UnsafePath:
        return None
    if not security.check_extension(rel, security.RUN_ARTIFACT_EXTENSIONS):
        return None
    return rel


def _resolve(app, doc_id, must_exist=True):
    """Azonosító → (abszolút út, aláírt relatív út). Jegyzékben nem szereplő vagy tiltott → hiba."""
    if doc_id.startswith(ARTIFACT_PREFIX):
        rel = _artifact_rel(doc_id[len(ARTIFACT_PREFIX):])
        if rel is None:
            raise ApiError("FORBIDDEN", "Csak a 05_elemzes/ vagy 06_kezirat/ alatti futás-artefaktum nyitható meg "
                                        "(SVG, PNG, PDF, TIFF, JSON, MD, CSV, TXT).")
        return security.safe_resolve(app.project_root, rel, security.RUN_ARTIFACT_EXTENSIONS, must_exist), rel
    entry = _doc_entry(app, doc_id)
    if entry is None:
        raise ApiError("NOT_FOUND", "Nincs ilyen dokumentum a jegyzékben (03_adatok/documents.json).")
    rel = entry.get("path")
    if entry.get("root") == "project":
        root = app.project_root
    else:
        root = _composer_outdir(app)
        if root is None:
            raise ApiError("NOT_FOUND", "A composer kimeneti mappája nincs beállítva (ma-projekt.json composer.outdir).")
    return security.safe_resolve(root, rel, security.DOC_EXTENSIONS, must_exist), rel


def post_fileurl(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    doc_id = body_str(body, "doc", max_len=300)
    path = body_str(body, "path", max_len=1024)
    ttl = body_int(body, "ttl", minimum=1)
    if ttl is None:
        ttl = security.FILE_URL_TTL
    if ttl > security.FILE_URL_MAX_TTL:
        raise ApiError("BAD_REQUEST", "A ttl legfeljebb %d másodperc lehet." % security.FILE_URL_MAX_TTL)
    if (doc_id is None) == (path is None):
        raise ApiError("BAD_REQUEST", "Pontosan egy mező adható meg: doc (jegyzékbeli dokumentum) vagy path "
                                      "(futás-artefaktum).")
    if doc_id is None:
        doc_id = ARTIFACT_PREFIX + path
    elif doc_id.startswith(ARTIFACT_PREFIX):
        raise ApiError("BAD_REQUEST", "Érvénytelen dokumentum-azonosító.")
    _abs, rel = _resolve(app, doc_id)
    url = app.security.sign_file_url(doc_id, rel, ttl=ttl)
    parsed = app.security.parse_file_url(url)
    return Result({"url": url, "expires_at": parsed[1] if parsed else None, "ttl": ttl}, FILEURL_SCHEMA)


def get_file(req):
    app = req.app
    parsed = app.security.parse_file_url(req.path)
    if parsed is None:
        raise ApiError("FORBIDDEN", MSG_SIG)

    def lookup(doc_id):
        if doc_id.startswith(ARTIFACT_PREFIX):
            return _artifact_rel(doc_id[len(ARTIFACT_PREFIX):])
        try:
            entry = _doc_entry(app, doc_id)
        except store.StoreError:
            return None
        return entry.get("path") if entry else None

    if app.security.verify_file_url(req.path, lookup) is None:
        raise ApiError("FORBIDDEN", MSG_SIG)
    app.require_open()
    full, _rel = _resolve(app, parsed[0])
    if not full.is_file():
        raise ApiError("NOT_FOUND", "A fájl nem található.")
    name = full.name
    headers = [("Content-Disposition", "inline; filename*=UTF-8''%s" % quote(name, safe=""))]
    return Response(None, security.content_type_for(name), kind=security.file_kind(name), headers=headers,
                    file_path=full, length=full.stat().st_size)


def register(router):
    router.add("GET", "/api/documents", get_documents, schema=SCHEMA)
    router.add("PUT", "/api/documents", put_documents, schema=SCHEMA)
    router.add("POST", "/api/fileurl", post_fileurl, schema=FILEURL_SCHEMA, request_schema=FILEURL_REQUEST)
    router.add("GET", "/f/<doc>/<exp>/<sig>", get_file)
