# -*- coding: utf-8 -*-
"""Projektnapló és kapuk (3.4, 3.5.15, 6.6) a ``metaelemzes.projekt`` függvényein át.

- ``GET /api/log/<kind>`` (finding | decision | checkpoint | grade | run, többes számban is;
  ``status``: ``projekt.status``; ``activity``: a hash-láncolt tevékenységnapló + láncellenőrzés)
  ``?status=&severity=&stage=``; ``GET /api/log/<kind>/<id>`` egy tétel.
- ``POST /api/log/decision | finding | resolve | checkpoint``. A kapu a motoré: ha a
  ``projekt.checkpoint`` nyitott blocker miatt elutasít, 409 GATE_BLOCKED a blockerek listájával és
  a motor üzenetével szó szerint; egyéb motor-``ValueError`` → 422 VALIDATION.

Az activity-sorba csak a tétel fajtája, azonosítója és kódjai kerülnek (szöveg nem, T10)."""
from metaelemzes import projekt

from ..router import ApiError, Result
from ._common import body_int, body_str, int_arg, kb_refs_text

SCHEMA = "szk.ma.log/v1"
ITEM_SCHEMA = "szk.ma.log-item/v1"
WRITE_SCHEMA = "szk.ma.log-write/v1"
STATUS_SCHEMA = "szk.ma.project-status/v1"
ACTIVITY_SCHEMA = "szk.ma.activity-log/v1"
KINDS = {"finding": "findings", "findings": "findings", "decision": "decisions", "decisions": "decisions",
         "checkpoint": "checkpoints", "checkpoints": "checkpoints", "grade": "grades", "grades": "grades",
         "run": "runs", "runs": "runs"}
ITEM_KINDS = {"finding": "finding", "findings": "finding", "decision": "decision", "decisions": "decision",
              "checkpoint": "checkpoint", "checkpoints": "checkpoint", "grade": "grade", "grades": "grade",
              "run": "run", "runs": "run"}
MSG_NO_LOG = ("Nincs projektnapló (projekt.sqlite) ebben a projektmappában. Hozd létre: Projekt → "
              "Új projekt (POST /api/project {action: 'init'}) vagy: python ma.py project init <mappa> --title …")
MAX_ACTIVITY = 1000

_STR = {"type": ["string", "null"], "maxLength": 20000}
_STAGE = {"type": ["string", "null"], "maxLength": 100}
_AGENT = {"type": ["string", "null"], "maxLength": 40}
_KB = {"type": ["string", "array", "null"], "items": {"type": "string", "maxLength": 200}}
DECISION_SCHEMA = {
    "type": "object", "required": ["decision"],
    "properties": {"agent": _AGENT, "decision": _STR, "rationale": _STR, "stage": _STAGE, "kb_refs": _KB,
                   "alternatives": _STR, "supersedes": {"type": ["integer", "null"]},
                   "context": {"type": ["object", "null"]}, "strict": {"type": "boolean"}},
    "additionalProperties": False,
}
FINDING_SCHEMA = {
    "type": "object", "required": ["severity", "title"],
    "properties": {"agent": _AGENT, "severity": {"enum": list(projekt.SEVERITIES)}, "title": _STR, "detail": _STR,
                   "stage": _STAGE, "evidence": _STR, "kb_refs": _KB, "strict": {"type": "boolean"}},
    "additionalProperties": False,
}
RESOLVE_SCHEMA = {
    "type": "object", "required": ["id", "status"],
    "properties": {"id": {"type": "integer"}, "status": {"enum": list(projekt.RESOLVE_STATUSES)},
                   "resolution": _STR},
    "additionalProperties": False,
}
CHECKPOINT_SCHEMA = {
    "type": "object", "required": ["stage", "verdict"],
    "properties": {"agent": _AGENT, "stage": _STAGE, "verdict": {"enum": list(projekt.VERDICTS)}, "summary": _STR},
    "additionalProperties": False,
}


def _root(app):
    if not (app.project_root / "projekt.sqlite").is_file():
        raise ApiError("NOT_FOUND", MSG_NO_LOG)
    return str(app.project_root)


def _agent(body):
    agent = body.get("agent") or "user"
    if agent not in projekt.KNOWN_AGENTS:
        raise ApiError("BAD_REQUEST", "Ismeretlen ágens (ismert: %s)." % ", ".join(projekt.KNOWN_AGENTS))
    return agent


# ---------------------------------------------------------------------------- olvasás
def _activity(req):
    app = req.app
    limit = int_arg(req, "limit", 200, 1, MAX_ACTIVITY)
    records = app.activity.read()
    ok, bad_seq, message = app.activity.verify()
    return Result({"kind": "activity", "items": records[-limit:], "total": len(records),
                   "verify": {"ok": ok, "first_bad_seq": bad_seq, "message": message},
                   "head": app.activity.head()}, ACTIVITY_SCHEMA)


def get_log(req):
    app = req.app
    app.require_open()
    kind = req.params["kind"]
    if kind == "activity":
        return _activity(req)
    if kind == "status":
        return Result(projekt.status(_root(app)), STATUS_SCHEMA)
    plural = KINDS.get(kind)
    if plural is None:
        raise ApiError("NOT_FOUND", "Ismeretlen naplófajta (finding, decision, checkpoint, grade, run, status, activity).")
    items = projekt.list_items(_root(app), plural, status=req.arg("status", max_len=20) or None,
                               severity=req.arg("severity", max_len=20) or None,
                               stage=req.arg("stage", max_len=100) or None)
    return Result({"kind": ITEM_KINDS[kind], "items": items}, SCHEMA)


def get_item(req):
    app = req.app
    app.require_open()
    kind = ITEM_KINDS.get(req.params["kind"])
    raw = req.params["item_id"]
    if kind is None:
        raise ApiError("NOT_FOUND", "Ismeretlen naplófajta.")
    if not raw.isdigit() or len(raw) > 12:
        raise ApiError("BAD_REQUEST", "A tétel azonosítója pozitív egész szám.")
    try:
        item = projekt.get_item(_root(app), kind, int(raw))
    except ValueError:
        raise ApiError("NOT_FOUND", "Nincs ilyen naplótétel.") from None
    return Result({"kind": kind, "item": item}, ITEM_SCHEMA)


# ---------------------------------------------------------------------------- írás
def _done(app, kind, item_id, warnings, details):
    details = dict(details, kind=kind, id=item_id)
    if app.log_activity("log.%s" % kind, details=details) is None:
        warnings = list(warnings) + [app.ACTIVITY_WARNING]
    return Result({"kind": kind, "id": item_id, "warnings": list(warnings)}, WRITE_SCHEMA, warnings=warnings)


def post_decision(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    root = _root(app)
    warns = []
    did = projekt.log_decision(root, _agent(body), body_str(body, "decision", required=True),
                               rationale=body_str(body, "rationale"), stage=body_str(body, "stage", max_len=100),
                               kb_refs=kb_refs_text(body.get("kb_refs")),
                               alternatives=body_str(body, "alternatives"),
                               supersedes=body_int(body, "supersedes", minimum=1), kb_db=app.kb_db,
                               strict=bool(body.get("strict")), warnings=warns)
    return _done(app, "decision", did, warns, {"stage": body_str(body, "stage", max_len=100)})


def post_finding(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    root = _root(app)
    warns = []
    fid = projekt.add_finding(root, _agent(body), body["severity"], body_str(body, "title", required=True),
                              detail=body_str(body, "detail"), stage=body_str(body, "stage", max_len=100),
                              evidence=body_str(body, "evidence"), kb_refs=kb_refs_text(body.get("kb_refs")),
                              kb_db=app.kb_db, strict=bool(body.get("strict")), warnings=warns)
    return _done(app, "finding", fid, warns, {"severity": body["severity"],
                                             "stage": body_str(body, "stage", max_len=100)})


def post_resolve(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    root = _root(app)
    fid = body_int(body, "id", required=True, minimum=1)
    projekt.resolve_finding(root, fid, body["status"], body_str(body, "resolution"))
    return _done(app, "resolve", fid, [], {"status": body["status"]})


def _blockers(root, stage):
    """A megadott szakasz(ok) PASS-át akadályozó blockerek a motor kapulogikájával."""
    try:
        stages = projekt.parse_stage(stage)
    except ValueError:
        return []
    if not stages:
        return []
    con = projekt.connect(root)
    try:
        rows = projekt.open_blockers(con, stages)
        return [{"id": r["id"], "stage": r["stage_id"], "title": r["title"], "status": r["status"]} for r in rows]
    finally:
        con.close()


def post_checkpoint(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    root = _root(app)
    stage = body_str(body, "stage", required=True, max_len=100)
    verdict = body["verdict"]
    warns = []
    try:
        cid = projekt.checkpoint(root, stage, _agent(body), verdict, body_str(body, "summary"), warnings=warns)
    except ValueError as exc:
        blockers = _blockers(root, stage) if verdict in ("PASS", "PASS_WITH_FIXES") else []
        if blockers:
            raise ApiError("GATE_BLOCKED", str(exc), {"blockers": blockers, "stage": stage, "verdict": verdict}) from None
        raise
    return _done(app, "checkpoint", cid, warns, {"stage": stage, "verdict": verdict})


def register(router):
    router.add("GET", "/api/log/<kind>", get_log, schema=SCHEMA)
    router.add("GET", "/api/log/<kind>/<item_id>", get_item, schema=ITEM_SCHEMA)
    router.add("POST", "/api/log/decision", post_decision, schema=WRITE_SCHEMA, request_schema=DECISION_SCHEMA)
    router.add("POST", "/api/log/finding", post_finding, schema=WRITE_SCHEMA, request_schema=FINDING_SCHEMA)
    router.add("POST", "/api/log/resolve", post_resolve, schema=WRITE_SCHEMA, request_schema=RESOLVE_SCHEMA)
    router.add("POST", "/api/log/checkpoint", post_checkpoint, schema=WRITE_SCHEMA, request_schema=CHECKPOINT_SCHEMA)

