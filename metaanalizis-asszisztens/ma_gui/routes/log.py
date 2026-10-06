# -*- coding: utf-8 -*-
"""Projektnapló és kapuk (3.4, 3.5.15, 6.6) a motor-homlokzaton át (``metaelemzes.api.project_*``).

- ``GET /api/log/<kind>`` (finding | decision | checkpoint | grade | run, többes számban is;
  ``status``: ``api.project_status``; ``activity``: a hash-láncolt tevékenységnapló + láncellenőrzés)
  ``?status=&severity=&stage=``; ``GET /api/log/<kind>/<id>`` egy tétel.
- ``POST /api/log/decision | finding | resolve | checkpoint``. A kapu a motoré: ha a
  ``project_checkpoint`` nyitott blocker miatt elutasít, 409 GATE_BLOCKED a blockerek listájával és
  a motor üzenetével szó szerint; egyéb motor-``ValueError`` → 422 VALIDATION.

A szereplő (``actor``) a munkapad felhasználója (``app.actor``): a napló minden sora így a CLI-vel azonos
módon jelöli, ki írta. Az activity-sorba csak a tétel fajtája, azonosítója, kódjai és (döntésnél) a
gépi ``context`` kerül — szabad szöveg és cellaérték nem (T10). A „Nem hiba — indoklás” döntés
``context``-je ({kind: validation, dataset, row_uid, code, fields}) alapján jelöli a
``POST /api/validate`` a megállapítást ``acknowledged``-ként. A szabad szöveges mezők a projekt.sqlite-ba
(a vault feltolja) írás előtt PHI-őrön mennek át (``_common.phi_text_guard``)."""
from metaelemzes import api

from ..router import ApiError, Result
from ._common import body_int, body_str, int_arg, journal_root, kb_refs_text, phi_text_guard

SCHEMA = "szk.ma.log/v1"
ITEM_SCHEMA = "szk.ma.log-item/v1"
WRITE_SCHEMA = "szk.ma.log-write/v1"
STATUS_SCHEMA = "szk.ma.project-status/v1"
ACTIVITY_SCHEMA = "szk.ma.activity-log/v1"
# a projektnapló szókincse a homlokzatról (api.KNOWN_AGENTS … = projekt.*)
KNOWN_AGENTS = tuple(api.KNOWN_AGENTS)
SEVERITIES = tuple(api.SEVERITIES)
VERDICTS = tuple(api.VERDICTS)
RESOLVE_STATUSES = tuple(api.RESOLVE_STATUSES)
FINAL = "FINAL"
KINDS = {"finding": "findings", "findings": "findings", "decision": "decisions", "decisions": "decisions",
         "checkpoint": "checkpoints", "checkpoints": "checkpoints", "grade": "grades", "grades": "grades",
         "run": "runs", "runs": "runs"}
ITEM_KINDS = {"finding": "finding", "findings": "finding", "decision": "decision", "decisions": "decision",
              "checkpoint": "checkpoint", "checkpoints": "checkpoint", "grade": "grade", "grades": "grade",
              "run": "run", "runs": "run"}
MAX_ACTIVITY = 1000

_STR = {"type": ["string", "null"], "maxLength": 20000}
_STAGE = {"type": ["string", "null"], "maxLength": 100}
_AGENT = {"type": ["string", "null"], "maxLength": 40}
_KB = {"type": ["string", "array", "null"], "items": {"type": "string", "maxLength": 200}}
# a döntés gépi kontextusa: csak azonosítók és mezőnevek (cellaérték nem — az activity-naplóba is kerül)
CONTEXT_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "kind": {"enum": ["validation", "analysis", "prisma", "other"]},
        "dataset": {"type": ["string", "null"], "maxLength": 1024},
        "row_uid": {"type": ["string", "null"], "pattern": "^r[0-9a-z]{4,12}$"},
        "code": {"type": ["string", "null"], "pattern": "^[VPX][0-9]{3}$"},
        "fields": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 100}},
        "run_id": {"type": ["string", "null"], "maxLength": 64},
        "spec": {"type": ["string", "null"], "maxLength": 64},
        "outcome": {"type": ["string", "null"], "maxLength": 100},
        # protokoll-eltérés (X016): a megváltozott elemzési opciók (opcióértékek, nem cellaértékek)
        "changes": {"type": "array", "maxItems": 100, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"key": {"type": "string", "maxLength": 100},
                           "before": {"type": ["string", "number", "boolean", "null", "array"]},
                           "after": {"type": ["string", "number", "boolean", "null", "array"]}}}},
    },
    "additionalProperties": False,
}
DECISION_SCHEMA = {
    "type": "object", "required": ["decision"],
    "properties": {"agent": _AGENT, "decision": _STR, "rationale": _STR, "stage": _STAGE, "kb_refs": _KB,
                   "alternatives": _STR, "supersedes": {"type": ["integer", "null"]},
                   "context": CONTEXT_SCHEMA, "strict": {"type": "boolean"}},
    "additionalProperties": False,
}
FINDING_SCHEMA = {
    "type": "object", "required": ["severity", "title"],
    "properties": {"agent": _AGENT, "severity": {"enum": list(SEVERITIES)}, "title": _STR, "detail": _STR,
                   "stage": _STAGE, "evidence": _STR, "kb_refs": _KB, "strict": {"type": "boolean"}},
    "additionalProperties": False,
}
RESOLVE_SCHEMA = {
    "type": "object", "required": ["id", "status"],
    "properties": {"id": {"type": "integer"}, "status": {"enum": list(RESOLVE_STATUSES)},
                   "resolution": _STR},
    "additionalProperties": False,
}
CHECKPOINT_SCHEMA = {
    "type": "object", "required": ["stage", "verdict"],
    "properties": {"agent": _AGENT, "stage": _STAGE, "verdict": {"enum": list(VERDICTS)}, "summary": _STR,
                   "audit_gate": {"type": "boolean"}},
    "additionalProperties": False,
}


def _agent(body):
    agent = body.get("agent") or "user"
    if agent not in KNOWN_AGENTS:
        raise ApiError("BAD_REQUEST", "Ismeretlen ágens (ismert: %s)." % ", ".join(KNOWN_AGENTS))
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
        return Result(api.project_status(journal_root(app)), STATUS_SCHEMA)
    plural = KINDS.get(kind)
    if plural is None:
        raise ApiError("NOT_FOUND", "Ismeretlen naplófajta (finding, decision, checkpoint, grade, run, status, activity).")
    items = api.project_list(journal_root(app), plural, status=req.arg("status", max_len=20) or None,
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
    root = journal_root(app)
    try:
        item = api.project_show(root, kind, int(raw))
    except ValueError:
        raise ApiError("NOT_FOUND", "Nincs ilyen naplótétel.") from None
    return Result({"kind": kind, "item": item}, ITEM_SCHEMA)


# ---------------------------------------------------------------------------- írás
def _done(app, kind, item_id, warnings, details):
    details = dict(details, kind=kind, id=item_id)
    details = {k: v for k, v in details.items() if v is not None}
    if app.log_activity("log.%s" % kind, details=details) is None:
        warnings = list(warnings) + [app.ACTIVITY_WARNING]
    return Result({"kind": kind, "id": item_id, "warnings": list(warnings)}, WRITE_SCHEMA, warnings=warnings)


def _text_leaves(node, path, limit=500):
    """[(út, szöveg)] egy JSON-érték szöveges leveleiből (a PHI-őrnek; az út mezőnevet mond, értéket nem)."""
    out, stack = [], [(node, path)]
    while stack and len(out) < limit:
        n, p = stack.pop()
        if isinstance(n, str):
            out.append((p, n))
        elif isinstance(n, dict):
            stack.extend((v, "%s.%s" % (p, k)) for k, v in n.items())
        elif isinstance(n, list):
            stack.extend((v, "%s[%d]" % (p, i)) for i, v in enumerate(n))
    return sorted(out)


def _context(body):
    ctx = body.get("context")
    if not isinstance(ctx, dict) or not ctx:
        return None
    return {k: v for k, v in ctx.items() if v is not None}


def post_decision(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    root = journal_root(app)
    ctx = _context(body)
    # a gépi kontextus szöveg-levelei is (changes[].before/after, fields, dataset, spec, outcome …): a projekt.sqlite-
    # ba és a hash-láncolt activity-naplóba kerülnek, onnan pedig az auditba (PRIV-4)
    warns = phi_text_guard(app, [(k, body.get(k)) for k in ("decision", "rationale", "alternatives")]
                           + _text_leaves(ctx, "context"))
    kw = {}
    if ctx is not None and accepts_context():
        kw["context"] = ctx
    res = api.project_log(root, _agent(body), body_str(body, "decision", required=True),
                          rationale=body_str(body, "rationale"), stage=body_str(body, "stage", max_len=100),
                          kb_refs=kb_refs_text(body.get("kb_refs")), alternatives=body_str(body, "alternatives"),
                          supersedes=body_int(body, "supersedes", minimum=1), kb_db=app.kb_db,
                          strict=bool(body.get("strict")), actor=app.actor, **kw)
    warns = list(warns) + list(res.get("warnings") or [])
    return _done(app, "decision", res["id"], warns, {"stage": body_str(body, "stage", max_len=100),
                                                     "context": ctx,
                                                     "supersedes": body_int(body, "supersedes", minimum=1)})


def accepts_context():
    """Tárolja-e a motor projektnaplója a döntés gépi kontextusát (api.project_log(context=…))."""
    from ._common import accepts
    return accepts(api.project_log, "context")


def post_finding(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    root = journal_root(app)
    warns = phi_text_guard(app, [(k, body.get(k)) for k in ("title", "detail", "evidence")])
    res = api.project_finding(root, _agent(body), body["severity"], body_str(body, "title", required=True),
                              detail=body_str(body, "detail"), stage=body_str(body, "stage", max_len=100),
                              evidence=body_str(body, "evidence"), kb_refs=kb_refs_text(body.get("kb_refs")),
                              kb_db=app.kb_db, strict=bool(body.get("strict")), actor=app.actor)
    warns = list(warns) + list(res.get("warnings") or [])
    return _done(app, "finding", res["id"], warns, {"severity": body["severity"],
                                                    "stage": body_str(body, "stage", max_len=100)})


def post_resolve(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    root = journal_root(app)
    fid = body_int(body, "id", required=True, minimum=1)
    warns = phi_text_guard(app, [("resolution", body.get("resolution"))])
    api.project_resolve(root, fid, body["status"], body_str(body, "resolution"), actor=app.actor)
    return _done(app, "resolve", fid, warns, {"status": body["status"]})


def _blockers(root, stage):
    """A megadott szakasz(ok) PASS-át akadályozó blockerek (nyitott és wontfix-szel lezárt) a motor
    listázójával; FINAL: bármely szakaszé."""
    final = str(stage or "").strip().upper() == FINAL
    out = []
    for status in ("open", "wontfix"):
        try:
            rows = api.project_list(root, "findings", status=status, severity="blocker",
                                    stage=None if final else stage)
        except ValueError:
            return []
        out += [{"id": r["id"], "stage": r.get("stage_id"), "title": r.get("title"), "status": r.get("status")}
                for r in rows]
    return sorted(out, key=lambda r: r["id"])


def post_checkpoint(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    root = journal_root(app)
    stage = body_str(body, "stage", required=True, max_len=100)
    verdict = body["verdict"]
    # a záró (FINAL) kapu a munkapadon alapból az audit-kapuval megy: a „kész” a fájlok összhangját is jelenti
    # (6.4, 6.6); kifejezett audit_gate: false-szal kikapcsolható
    audit_gate = body.get("audit_gate")
    if audit_gate is None:
        audit_gate = str(stage).strip().upper() == FINAL and verdict in ("PASS", "PASS_WITH_FIXES")
    audit_gate = bool(audit_gate)
    warns = phi_text_guard(app, [("summary", body.get("summary"))])
    try:
        res = api.project_checkpoint(root, stage, _agent(body), verdict, body_str(body, "summary"),
                                     actor=app.actor, audit_gate=audit_gate)
    except ValueError as exc:
        blockers = _blockers(root, stage) if verdict in ("PASS", "PASS_WITH_FIXES") else []
        if blockers:
            raise ApiError("GATE_BLOCKED", str(exc), {"blockers": blockers, "stage": stage, "verdict": verdict}) from None
        if audit_gate and verdict in ("PASS", "PASS_WITH_FIXES"):
            errors = api.audit_gate_errors(root)
            if errors:
                raise ApiError("GATE_BLOCKED", str(exc), {"blockers": [], "audit_errors": errors, "stage": stage,
                                                          "verdict": verdict}) from None
        raise
    warns = list(warns) + list(res.get("warnings") or [])
    return _done(app, "checkpoint", res["id"], warns, {"stage": stage, "verdict": verdict,
                                                       "audit_gate": audit_gate or None})


def register(router):
    router.add("GET", "/api/log/<kind>", get_log, schema=SCHEMA)
    router.add("GET", "/api/log/<kind>/<item_id>", get_item, schema=ITEM_SCHEMA)
    router.add("POST", "/api/log/decision", post_decision, schema=WRITE_SCHEMA, request_schema=DECISION_SCHEMA)
    router.add("POST", "/api/log/finding", post_finding, schema=WRITE_SCHEMA, request_schema=FINDING_SCHEMA)
    router.add("POST", "/api/log/resolve", post_resolve, schema=WRITE_SCHEMA, request_schema=RESOLVE_SCHEMA)
    router.add("POST", "/api/log/checkpoint", post_checkpoint, schema=WRITE_SCHEMA, request_schema=CHECKPOINT_SCHEMA)
