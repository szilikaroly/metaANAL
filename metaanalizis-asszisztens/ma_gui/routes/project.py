# -*- coding: utf-8 -*-
"""GET/POST /api/project — a szerverhez kötött projekt (2.2, 3.4, 4.17).

A szerver egyetlen, indításkor megadott projektmappán dolgozik. ``POST`` műveletek:
``{action: "init", title, question?, data_class?, review_type?, path?}`` (``metaelemzes.projekt.init``
+ ``ma-projekt.json``, ha még nincs), ``{action: "open", path}`` (csak a mostani projekt),
``{action: "data_class", data_class, confirm?, reason?}`` (``ma-projekt.json`` frissítése, naplózva;
alacsonyabb osztályba sorolás — C → B → A, az ismeretlen osztály C-nek számít — csak ``confirm: true``-val,
és a projektnaplóba döntésként is bekerül). Másik mappához új munkapad-példány indítandó (egy projekt
= egy szerver)."""
import os

from metaelemzes import projekt

from .. import privacy, store
from ..router import ApiError, Result
from ._common import body_str

SCHEMA = "szk.ma.project/v1"
PROJECT_JSON = "ma-projekt.json"
REVIEW_TYPES = ("intervention", "exposure", "diagnostic", "prognostic_factor", "prediction_model")
REQUEST_SCHEMA = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {"enum": ["init", "open", "data_class"]},
        "path": {"type": ["string", "null"], "maxLength": 4096},
        "title": {"type": ["string", "null"], "maxLength": 500},
        "question": {"type": ["string", "object", "null"]},
        "data_class": {"type": ["string", "null"], "maxLength": 4},
        "review_type": {"type": ["string", "null"], "maxLength": 40},
        "confirm": {"type": "boolean"},
        "reason": {"type": ["string", "null"], "maxLength": 2000},
    },
    "additionalProperties": False,
}
RANK = {"A": 0, "B": 1, "C": 2}


def _question_text(q):
    if q is None:
        return None
    if isinstance(q, str):
        return q.strip() or None
    if isinstance(q, dict):
        parts = []
        for k in ("P", "I", "C", "O"):
            v = q.get(k)
            if isinstance(v, str) and v.strip():
                parts.append("%s: %s" % (k, v.strip()))
        return "; ".join(parts) or None
    raise ApiError("BAD_REQUEST", "A question szöveg vagy {P, I, C, O} objektum legyen.")


def _question_obj(q):
    if isinstance(q, dict):
        return {k: (q.get(k).strip() if isinstance(q.get(k), str) else "") for k in ("P", "I", "C", "O")}
    if isinstance(q, str) and q.strip():
        return {"text": q.strip()}
    return None


def _same_project(app, path):
    if path is None or not str(path).strip():
        return True
    p = os.path.expanduser(str(path).strip())
    if not os.path.isabs(p):
        # a felület relatív utat is küldhet: a projektmappa nevével vagy a szülőjéhez képest
        if os.path.normcase(p.replace("\\", "/").rstrip("/")) == os.path.normcase(app.project_root.name):
            return True
        p = os.path.join(str(app.project_root.parent), p)
    # macOS-en a betűméret, bind mounttal az út is eltérhet: a fizikai azonosság dönt
    try:
        if os.path.samefile(p, str(app.project_root)):
            return True
    except (OSError, ValueError):
        pass
    return os.path.normcase(os.path.realpath(p)) == os.path.normcase(str(app.project_root))


def _other_project(app):
    return ApiError("BAD_REQUEST", "Ez a munkapad a(z) „%s” projekthez indult; másik projektmappához indíts új "
                    "példányt: python -m ma_gui --project <mappa>." % app.project_root.name,
                    {"project": str(app.project_root)})


def project_data(app):
    """A projekt nézete: ma-projekt.json (ha van) + futásidejű mezők; (data, ma-projekt.json etag)."""
    meta, etag = app.project_meta()
    data = dict(meta) if isinstance(meta, dict) else {"schema": SCHEMA}
    info = {}
    initialized = (app.project_root / "projekt.sqlite").is_file()
    open_blockers = None
    if initialized:
        try:
            st = projekt.status(str(app.project_root))
            info = st.get("project") or {}
            open_blockers = sum(1 for f in st.get("open_findings") or [] if f.get("severity") == "blocker")
        except Exception:                                  # noqa: BLE001 — a napló hibája ne vigye el a nézetet
            info = {}
    if not data.get("title"):
        data["title"] = info.get("title") or app.project_root.name
    data.setdefault("schema", SCHEMA)
    dc, source = app.data_class_with_source()
    data["data_class"] = dc
    data.update({
        "data_class_source": source,
        "data_class_label": privacy.DATA_CLASS_LABELS.get(dc),
        "path": str(app.project_root),
        "name": app.project_root.name,
        "initialized": initialized,
        "has_project_json": meta is not None,
        "open_blockers": open_blockers,
        "tables": app.store.list_tables(),
        "rev": app.project_rev(),
    })
    return data, etag


def get_project(req):
    data, etag = project_data(req.app)
    return Result(data, SCHEMA, etag=etag)


def _new_project_doc(app, title, question, data_class, review_type):
    return {
        "schema": SCHEMA,
        "title": title,
        "question": _question_obj(question),
        "review_type": review_type,
        "data_class": data_class,
        "outcomes": [],
        "appraisal_tools": [],
        "composer": None,
        "doc_roots": [],
        "locale": app.lang,
        "conventions": {"amstar2_partial_yes_critical": "meets", "grade_suspected": "unresolved"},
    }


def _init(req, body):
    app = req.app
    if not _same_project(app, body.get("path")):
        raise _other_project(app)
    title = body_str(body, "title", required=True, max_len=500)
    question = body.get("question")
    qtext = _question_text(question)
    dc = privacy.normalize_data_class(body.get("data_class") or "A")
    review_type = body.get("review_type") or "intervention"
    if review_type not in REVIEW_TYPES:
        raise ApiError("BAD_REQUEST", "Ismeretlen review_type (%s)." % " | ".join(REVIEW_TYPES))
    warnings = []
    with app.own_writes() as note:
        copied = projekt.init(str(app.project_root), title, qtext)
        for rel in copied:
            note(rel)
    outputs = [{"path": "projekt.sqlite"}] + [{"path": c} for c in copied]
    meta, _etag = app.project_meta()
    if meta is None:
        doc = _new_project_doc(app, title, question, dc, review_type)
        sha = app.store.write_bytes(PROJECT_JSON, store.json_bytes(doc), if_match=None)
        outputs.append({"path": PROJECT_JSON, "sha256": sha})
    else:
        warnings.append("A ma-projekt.json már létezik; a címet és az adatosztályt nem írtam felül.")
    rec = app.log_activity("project.init", outputs=outputs, details={"data_class": app.data_class(),
                                                                    "templates": len(copied)})
    if rec is None:
        warnings.append(app.ACTIVITY_WARNING)
    app.refresh_privacy()
    data, etag = project_data(app)
    data["templates_copied"] = copied
    return Result(data, SCHEMA, warnings=warnings, etag=etag)


def _log_downgrade(app, old, new, reason, warnings):
    """Az osztály csökkentése döntésként a projektnaplóba (ha van) → döntés-azonosító vagy None."""
    if not (app.project_root / "projekt.sqlite").is_file():
        warnings.append("Nincs projektnapló (projekt.sqlite): az osztály csökkentése csak az activity-naplóba került.")
        return None
    try:
        return projekt.log_decision(str(app.project_root), "user",
                                    "Adatosztály csökkentve: %s → %s" % (old, new),
                                    rationale=reason or "A felhasználó megerősítette (munkapad).", stage=None,
                                    kb_db=app.kb_db, check_kb=False)
    except Exception:                                      # noqa: BLE001 — a váltás már megtörtént
        warnings.append("Az osztály csökkentésének döntését nem sikerült a projektnaplóba írni.")
        return None


def _set_data_class(req, body):
    app = req.app
    dc = privacy.normalize_data_class(body.get("data_class"))
    old_effective, old_source = app.data_class_with_source()
    downgrade = RANK[dc] < RANK.get(old_effective, 0) and old_source != "default"
    reason = body_str(body, "reason", max_len=2000)
    if downgrade and body.get("confirm") is not True:
        raise ApiError("BAD_REQUEST", "Az adatosztály csökkentése (%s → %s) gyengíti az adatvédelmi szabályokat; "
                                      "csak kifejezett megerősítéssel (confirm: true) lehetséges." % (old_effective, dc),
                       {"needs_confirm": True, "from": old_effective, "to": dc})
    header_etag = req.header("If-Match")
    meta, etag = app.project_meta()
    if meta is None:
        doc = _new_project_doc(app, app.project_root.name, None, dc, "intervention")
        expected = None
    else:
        doc = dict(meta)
        expected = etag
    if header_etag:
        expected = header_etag
    old = doc.get("data_class")
    doc["data_class"] = dc
    sha = app.store.write_bytes(PROJECT_JSON, store.json_bytes(doc), if_match=expected)
    warnings = []
    details = {"from": old if old in privacy.DATA_CLASSES else None, "to": dc}
    if downgrade:
        details["downgrade"] = True
        details["from_effective"] = old_effective
        did = _log_downgrade(app, old_effective, dc, reason, warnings)
        if did is not None:
            details["decision_id"] = did
    rec = app.log_activity("project.data_class", outputs=[{"path": PROJECT_JSON, "sha256": sha}], details=details)
    if rec is None:
        warnings.append(app.ACTIVITY_WARNING)
    app.refresh_privacy()
    data, etag = project_data(app)
    return Result(data, SCHEMA, warnings=warnings, etag=etag)


def post_project(req):
    body = req.json_object()
    action = body.get("action")
    if action == "init":
        return _init(req, body)
    if action == "data_class":
        return _set_data_class(req, body)
    if not _same_project(req.app, body.get("path")):
        raise _other_project(req.app)
    data, etag = project_data(req.app)
    return Result(data, SCHEMA, etag=etag)


def register(router):
    router.add("GET", "/api/project", get_project, schema=SCHEMA)
    router.add("POST", "/api/project", post_project, schema=SCHEMA, request_schema=REQUEST_SCHEMA)
