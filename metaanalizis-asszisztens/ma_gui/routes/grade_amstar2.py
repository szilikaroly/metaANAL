# -*- coding: utf-8 -*-
"""AMSTAR 2 önellenőrzés — a saját áttekintés (terv 2.4, 3.4, 3.5.13, 4.11, 5.4, 6.5; KB AMSTAR2-00 konvenció).

Az értékelés fájlja ugyanaz, mint az értékelés-végpontoké (``routes/appraisal*.py``; 2.4: író a munkapad):
``04_torzitas_kockazat/appraisals/review.amstar2.<értékelő>.json`` (``szk.appraisal/v1``, egység: ``review``).
Ez a végpont az AMSTAR 2 sajátosságait adja hozzá: a projekt-audit bizonyíték-javaslatait és a motor besorolását
MINDKÉT konvencióval (a „részben igen” kritikus tételen: ``meets`` | ``weakness``).

- ``GET /api/amstar2[?rater=]`` → ``szk.ma.amstar2-view/v1`` + ETag (a fájlé): ``{rater, raters[] (a meglévő AMSTAR 2
  értékelők), path, instrument (a motor szk.instrument/v1 leírása | null), instrument_source (engine | kb | null),
  kb_items (a KB AMSTAR2 ellenőrzőlistája — tartalék), appraisal (szk.appraisal/v1 | null), hints (a motor project
  audit ``amstar2_hints``-e), convention (a projekt konvenciója), consistency (a motor ``amstar2_consistency``-je a
  válaszokra, mindkét konvencióval), engine{…}}``. A motor ``amstar2_consistency`` nélkül 424 (a ``details.view``-ban
  a nézet a besorolás nélkül).
- ``PUT /api/amstar2`` ← ``{appraisal: szk.appraisal/v1 (tool: amstar2)}`` + If-Match. Az AI-vázlat (``origin:
  ai_draft``) emberi jóváhagyás (``approved_by``) nélkül nem lehet kész (11/6. döntés; nem is számít értékelőnek);
  ha az összítélet eltér a motor besorolásától, ``override_reason`` kötelező (X017). PHI-szkenner + írás-tartás,
  atomikus írás, activity-sor ``amstar2.save`` (csak értékelő, darabszám, besorolás — szabad szöveg nélkül)."""
import re

from metaelemzes import api

from .. import store
from ..router import ApiError, Result
from . import grade_engine as E
from ._common import log_activity_or_warn, phi_doc_guard

VIEW_SCHEMA = "szk.ma.amstar2-view/v1"
APPRAISAL_SCHEMA = "szk.appraisal/v1"
TOOL = "amstar2"
UNIT = "review"
APPRAISAL_DIR = "04_torzitas_kockazat/appraisals"
REL = APPRAISAL_DIR + "/" + UNIT + "." + TOOL + ".%s.json"
RATER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
RESERVED_RATERS = ("consensus", "ai")
RATINGS = ("high", "moderate", "low", "critically_low")
CONVENTIONS = ("meets", "weakness")
MAX_TEXT = 4000

_ANSWER = {"type": "object", "properties": {
    "value": {"type": ["string", "null"], "maxLength": 40},
    "rationale": {"type": ["string", "null"], "maxLength": MAX_TEXT},
    "evidence": {"type": ["object", "null"]}}}
APPRAISAL = {
    "type": "object", "required": ["answers"],
    "properties": {
        "schema": {"const": APPRAISAL_SCHEMA},
        "tool": {"const": TOOL},
        "assessor": {"type": ["string", "null"], "pattern": RATER_RE.pattern},
        "second_assessor": {"type": ["string", "null"], "pattern": RATER_RE.pattern},
        "status": {"enum": ["draft", "complete", "consensus"]},
        "origin": {"enum": ["human", "ai_draft"]},
        "approved_by": {"type": ["string", "null"], "maxLength": 64},
        "answers": {"type": "object", "additionalProperties": _ANSWER},
        "overall": {"type": ["object", "null"], "properties": {
            "judgement": {"enum": list(RATINGS) + [None]},
            "rationale": {"type": ["string", "null"], "maxLength": MAX_TEXT},
            "override_reason": {"type": ["string", "null"], "maxLength": MAX_TEXT}}},
    },
}
PUT_REQUEST = {"type": "object", "required": ["appraisal"],
               "properties": {"appraisal": APPRAISAL, "client_seq": {"type": ["integer", "null"]}},
               "additionalProperties": False}


def _check_rater(raw):
    if raw is None:
        return None
    if not RATER_RE.match(raw) or raw in RESERVED_RATERS:
        raise ApiError("BAD_REQUEST", "Érvénytelen értékelő-azonosító: betűvel kezdődő monogram (betű, szám, '_', "
                                      "'-'; legfeljebb 32 karakter); a 'consensus' és az 'ai' fenntartott.")
    return raw


def _raters(app):
    d = app.project_root / APPRAISAL_DIR
    out = []
    if d.is_dir():
        pre, suf = "%s.%s." % (UNIT, TOOL), ".json"
        for p in sorted(d.glob(pre + "*" + suf))[:200]:
            r = p.name[len(pre):-len(suf)]
            if RATER_RE.match(r):
                out.append(r)
    return out


def _convention(app):
    meta, _ = app.project_meta_safe()
    conv = (meta or {}).get("conventions") if isinstance((meta or {}).get("conventions"), dict) else {}
    v = conv.get("amstar2_partial_yes_critical")
    return v if v in CONVENTIONS else "meets"


def _answers(doc):
    out = {}
    for k, v in ((doc or {}).get("answers") or {}).items():
        val = v.get("value") if isinstance(v, dict) else v
        if val not in (None, ""):
            out[k] = val
    return out


def _instrument(app):
    done, inst = E.call_optional("instrument", tool=TOOL)
    if done and isinstance(inst, dict):
        return inst, "engine", None
    try:
        app.kb_ready()
        items = api.kb_checklist("AMSTAR2", db=app.kb_db)
    except Exception:                                      # noqa: BLE001 — a KB épülhet még: tartalék nélkül
        items = None
    kb = [{"id": it.get("item_id"), "section": it.get("section"), "text": it.get("text"), "ord": it.get("ord")}
          for it in items or [] if isinstance(it, dict)]
    return None, ("kb" if kb else None), kb


def _hints(app):
    try:
        rep = api.project_audit(str(app.project_root))
    except Exception:                                      # noqa: BLE001 — a javaslat nem kötelező
        return {}
    h = rep.get("amstar2_hints") if isinstance(rep, dict) else None
    return h if isinstance(h, dict) else {}


def _consistency(doc, convention):
    answers = _answers(doc)
    if not answers:
        return None
    return E.call("amstar2_consistency", answers=answers, convention=convention)


def _rating_of(consistency, convention):
    if not isinstance(consistency, dict):
        return None
    by = consistency.get("by_convention")
    if isinstance(by, dict) and by.get(convention) is not None:
        v = by[convention]
        return v.get("rating") if isinstance(v, dict) else v
    return consistency.get("rating")


def _load(app, rater):
    if rater is None:
        return None, None
    doc, etag = app.store.load_json(REL % rater)
    if doc is not None and not isinstance(doc, dict):
        raise store.Invalid("Az AMSTAR 2 értékelés gyökere objektum legyen.", {"path": REL % rater})
    return doc, etag


def _view(app, rater, doc, with_consistency=True):
    inst, source, kb = _instrument(app)
    conv = _convention(app)
    return {"schema": VIEW_SCHEMA, "rater": rater, "raters": _raters(app), "unit": UNIT, "tool": TOOL,
            "path": REL % rater if rater else None, "instrument": inst, "instrument_source": source, "kb_items": kb,
            "appraisal": doc, "hints": _hints(app), "convention": conv,
            "consistency": _consistency(doc, conv) if with_consistency else None, "engine": E.available()}


def get_amstar2(req):
    app = req.app
    app.require_open()
    rater = _check_rater(req.arg("rater", max_len=32) or None)
    if rater is None:
        found = _raters(app)
        rater = found[0] if found else None
    doc, etag = _load(app, rater)
    if not E.has("amstar2_consistency"):
        v = _view(app, rater, doc, with_consistency=False)
        try:
            E.need("amstar2_consistency")
        except ApiError as exc:
            exc.details = dict(exc.details or {}, view=v)
            raise
    return Result(_view(app, rater, doc), VIEW_SCHEMA, etag=etag)


def put_amstar2(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    E.need("amstar2_consistency")
    doc = dict(body["appraisal"])
    rater = _check_rater(doc.get("assessor") or req.arg("rater", max_len=32) or None)
    if not rater:
        raise ApiError("VALIDATION", "Add meg az értékelő azonosítóját (monogram): az értékelés értékelőnként külön "
                                     "fájlba kerül.", {"field": "assessor"})
    doc.update({"schema": APPRAISAL_SCHEMA, "tool": TOOL, "assessor": rater})
    doc.setdefault("origin", "human")
    doc.setdefault("status", "draft")
    target = doc.get("target") if isinstance(doc.get("target"), dict) else {}
    doc["target"] = dict(target, study_id=UNIT)
    if doc["origin"] == "ai_draft" and doc["status"] != "draft" and not (doc.get("approved_by") or "").strip():
        raise ApiError("VALIDATION", "AI-vázlat csak emberi jóváhagyás után lehet kész (11/6. döntés): nézd át a "
                                     "tételeket, és hagyd jóvá a vázlatot.", {"origin": "ai_draft"})
    conv = _convention(app)
    consistency = _consistency(doc, conv)
    overall = doc.get("overall") if isinstance(doc.get("overall"), dict) else {}
    implied = _rating_of(consistency, conv)
    if overall.get("judgement") and implied and overall["judgement"] != implied and \
            not (overall.get("override_reason") or "").strip():
        raise ApiError("VALIDATION", "Az összítélet eltér a motor besorolásától (%s): az eltéréshez indoklás kell "
                                     "(override_reason; X017)." % implied, {"implied": implied, "code": "X017"})
    rel = REL % rater
    phi_doc_guard(app, doc, rel, "AMSTAR 2 értékelés")
    sha = app.store.write_bytes(rel, store.json_bytes(doc), if_match=req.header("If-Match") or None)
    warnings = []
    log_activity_or_warn(app, "amstar2.save", warnings, outputs=[{"path": rel, "sha256": sha}],
                         details={"rater": rater, "answered": len(_answers(doc)), "status": doc.get("status"),
                                  "origin": doc.get("origin"), "rating": implied, "convention": conv,
                                  "override": bool((overall.get("override_reason") or "").strip())})
    saved, etag = _load(app, rater)
    return Result(_view(app, rater, saved), VIEW_SCHEMA, warnings=warnings, etag=etag)


def register(router):
    router.add("GET", "/api/amstar2", get_amstar2, schema=VIEW_SCHEMA)
    router.add("PUT", "/api/amstar2", put_amstar2, schema=VIEW_SCHEMA, request_schema=PUT_REQUEST)
