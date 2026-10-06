# -*- coding: utf-8 -*-
"""Protokoll fül (terv 3.5.0 „1 Protokoll”, 4.17, 6.4 X016, AMSTAR 2 2. tétel): a ma-projekt.json protokoll-mezői.

- ``GET /api/protocol`` → ``szk.ma.protocol/v1`` + ETag (a ma-projekt.json-é): ``{path, has_project_json, title,
  question{P, I, C, O}, review_type, review_types[] (a motor szókincse), data_class, data_class_source,
  data_class_label, registration{registry, id} | null, outcomes[] (a ma-projekt.json kimenetei), specs[{name,
  outcome, purpose, parent, prespecified, protocol_ref, etag, problems}] (05_elemzes/specs/ — az előre rögzített
  elemzések), conventions}``.
- ``PUT /api/protocol`` ← ``{title?, question?{P, I, C, O}, review_type?, registration?{registry, id} | null}`` +
  If-Match (a betöltött ETag; ha még nincs ma-projekt.json, létrejön a motor alapértékeivel). A motor
  ``validate_project_meta``-ja ellenőriz és normalizál (422 a hibalistával); PHI-szkenner + írás-tartás
  (``phi_doc_guard``); atomikus írás; activity-sor ``project.protocol`` (csak a megváltozott mezők NEVE).

A kimenetek felvétele / módosítása a meglévő ``POST /api/project {action: 'outcome', replace?}``, az adatosztályé
a ``POST /api/project {action: 'data_class', confirm?}``, az előre rögzített jelölés a ``PUT /api/specs/<név>``
útján megy (a felület ezeket hívja) — itt nincs második út ugyanahhoz."""
from metaelemzes import api

from .. import privacy, store
from ..router import ApiError, Result
from . import specs as _specs
from ._common import log_activity_or_warn, phi_doc_guard
from .grade_common import MAX_LIST, PROJECT_JSON, text_or_none

SCHEMA = "szk.ma.protocol/v1"
PROJECT_SCHEMA = "szk.ma.project/v1"
PICO = ("P", "I", "C", "O")
FIELDS = ("title", "question", "review_type", "registration")
_TXT = {"type": ["string", "null"], "maxLength": 2000}
PUT_REQUEST = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"], "maxLength": 500},
        "question": {"type": ["object", "null"], "additionalProperties": False,
                     "properties": {k: _TXT for k in PICO}},
        "review_type": {"enum": list(api.REVIEW_TYPES)},
        "registration": {"type": ["object", "null"], "additionalProperties": False,
                         "properties": {"registry": {"type": ["string", "null"], "maxLength": 60},
                                        "id": {"type": ["string", "null"], "maxLength": 120}}},
        "client_seq": {"type": ["integer", "null"]},
    },
    "additionalProperties": False,
}


def _question(q):
    q = q if isinstance(q, dict) else {}
    return {k: (q.get(k) if isinstance(q.get(k), str) else "") for k in PICO}


def _registration(meta):
    prot = meta.get("protocol") if isinstance(meta.get("protocol"), dict) else {}
    reg = prot.get("registration")
    if not isinstance(reg, dict) or not any(reg.get(k) for k in ("registry", "id")):
        return None
    return {"registry": reg.get("registry"), "id": reg.get("id")}


def _spec_list(app):
    out = []
    d = app.project_root / _specs.SPEC_DIR
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json"))[:MAX_LIST]:
        name = p.name[:-5]
        if not _specs.NAME_RE.match(name) or not p.is_file():
            continue
        try:
            doc, etag = _specs.load(app, name)
        except store.StoreError as exc:
            out.append({"name": name, "path": _specs.spec_rel(name), "etag": None, "problems": [exc.message]})
            continue
        doc = doc if isinstance(doc, dict) else {}
        out.append({"name": name, "path": _specs.spec_rel(name), "etag": etag, "outcome": doc.get("outcome"),
                    "purpose": doc.get("purpose"), "parent": doc.get("parent"),
                    "prespecified": doc.get("prespecified"), "protocol_ref": doc.get("protocol_ref"),
                    "problems": api.validate_spec(doc) if doc else ["nem objektum"]})
    return out


def view(app):
    meta, etag = app.project_meta()
    m = meta if isinstance(meta, dict) else {}
    dc, source = app.data_class_with_source()
    return {
        "schema": SCHEMA,
        "path": PROJECT_JSON,
        "has_project_json": meta is not None,
        "title": m.get("title") or app.project_root.name,
        "question": _question(m.get("question")),
        "question_text": (m.get("question") or {}).get("text") if isinstance(m.get("question"), dict) else None,
        "review_type": m.get("review_type") or "intervention",
        "review_types": list(api.REVIEW_TYPES),
        "data_class": dc,
        "data_class_source": source,
        "data_class_label": privacy.DATA_CLASS_LABELS.get(dc),
        "registration": _registration(m),
        "outcomes": [o for o in (m.get("outcomes") or []) if isinstance(o, dict)][:MAX_LIST],
        "specs": _spec_list(app),
        "conventions": m.get("conventions") if isinstance(m.get("conventions"), dict) else {},
    }, etag


def get_protocol(req):
    app = req.app
    app.require_open()
    data, etag = view(app)
    return Result(data, SCHEMA, etag=etag)


def put_protocol(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    meta, etag = app.project_meta()
    doc = dict(meta) if isinstance(meta, dict) else {"schema": PROJECT_SCHEMA, "title": app.project_root.name,
                                                     "outcomes": [], "locale": app.lang}
    if doc.get("data_class") in (None, ""):
        doc["data_class"] = app.data_class()          # a ténylegesen érvényes osztály (ismeretlennél a szigorúbb)
    changed = []
    if "title" in body:
        title = text_or_none(body.get("title"), 500, "title")
        if not title:
            raise ApiError("VALIDATION", "A projekt címe nem lehet üres.", {"field": "title"})
        if title != doc.get("title"):
            doc["title"] = title
            changed.append("title")
    if "question" in body:
        old_q = doc.get("question") if isinstance(doc.get("question"), dict) else {}
        q = dict(old_q)                               # a nem PICO kulcsok (pl. a project init 'text'-je) maradnak
        q.update(_question(old_q))
        for k in PICO:
            if k in (body.get("question") or {}):
                q[k] = text_or_none((body.get("question") or {}).get(k), 2000, "question.%s" % k) or ""
        if q != old_q:
            doc["question"] = q
            changed.append("question")
    if "review_type" in body and body["review_type"] != doc.get("review_type"):
        doc["review_type"] = body["review_type"]
        changed.append("review_type")
    if "registration" in body:
        reg = body.get("registration") or {}
        new = {"registry": text_or_none(reg.get("registry"), 60, "registration.registry"),
               "id": text_or_none(reg.get("id"), 120, "registration.id")}
        prot = dict(doc.get("protocol")) if isinstance(doc.get("protocol"), dict) else {}
        if not any(new.values()):
            new = None
        if new != _registration(doc):
            if new is None:
                prot.pop("registration", None)
            else:
                prot["registration"] = new
            doc["protocol"] = prot
            changed.append("registration")
    norm, errors = api.validate_project_meta(doc)
    if errors:
        raise ApiError("VALIDATION", "A ma-projekt.json a módosítással érvénytelen lenne: %s" % "; ".join(errors[:10]),
                       {"errors": errors})
    phi_doc_guard(app, {"title": norm.get("title"), "question": norm.get("question"),
                        "protocol": norm.get("protocol")}, PROJECT_JSON, "protokoll")
    warnings = []
    if changed or meta is None:
        want = req.header("If-Match") or (None if meta is None else etag)
        sha = app.store.write_bytes(PROJECT_JSON, store.json_bytes(norm), if_match=want)
        log_activity_or_warn(app, "project.protocol", warnings, outputs=[{"path": PROJECT_JSON, "sha256": sha}],
                             details={"fields": changed, "created": meta is None})
    data, etag = view(app)
    data["changed"] = changed
    return Result(data, SCHEMA, warnings=warnings, etag=etag)


def register(router):
    router.add("GET", "/api/protocol", get_protocol, schema=SCHEMA)
    router.add("PUT", "/api/protocol", put_protocol, schema=SCHEMA, request_schema=PUT_REQUEST)
