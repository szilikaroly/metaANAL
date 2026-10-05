# -*- coding: utf-8 -*-
"""GRADE kimenetenként (terv 3.4, 3.5.12, 4.14, 6.5, 11/4. döntés) a motor GRADE-tárán és tanácsadóján át.

- ``GET /api/grade/<kimenet>[?run=<run_id>]`` → ``szk.ma.grade-view/v1`` + ETag (a mentett ítélet tartalom-ETag-je):
  ``{outcome{id, name, critical, measure, grade_start}, run (a GRADE alapja: a legutóbbi elsődleges commit-futás
  rövid leírása a motor szövegeivel; stale: X001), grade (szk.ma.grade/v1 | null), path, journal (a projektnapló
  legutóbbi GRADE-sora ehhez a kimenethez: {id, ts, certainty} | null), unresolved[], missing[], run_matches,
  conventions{grade_suspected}, vocab{domains, ratings, upgrades, certainties} (a 4.14 szókincse — a felület ebből
  épít, nincs második lista a JS-ben), engine{…: elérhető-e a motor-funkció}}``. Ha a motor GRADE-tára hiányzik:
  424 CAPABILITY_MISSING, a ``details.view``-ban ugyanez a nézet (``grade: null``) — a felület így is megmutatja a
  kimenetet, a futást és a tanácsokat.
- ``PUT /api/grade/<kimenet>`` ← ``{grade: szk.ma.grade/v1, record?: bool, certainty?: high | moderate | low |
  very low}`` + If-Match (a betöltött tartalom-ETag; új ítéletnél nem kell). Ellenőrzés: minden megadott domén-
  ítélethez és bejelölt felminősítéshez indoklás kell (422, a domén nevével — értéket soha); a publikációs
  torzítás „suspected” ítélete FELOLDATLAN (status: unresolved), amíg az ember nem választ 0-t vagy −1-et
  (11/4. döntés, X019). PHI-szkenner és írás-tartás (``phi_doc_guard``), mentés a motorral (``grade_put``: a
  bizonyosságot a motor számolja), activity-sor (``grade.save``; csak kódok és azonosítók).
  ``record: true`` — a projektnapló GRADE-sora (``grade_record`` vagy ``api.project_grade`` előjeles lépés-
  szövegekkel, hogy a ``grade_consistency`` változatlanul ellenőrizzen). A rögzítés TILTOTT (409 GATE_BLOCKED),
  ha bármely domén hiányzik vagy feloldatlan (X019), ha a futás ELAVULT (X001), vagy ha az ítélet nem a kimenet
  legutóbbi elsődleges futására hivatkozik (X007). A végső bizonyosság EMBERI ítélet (GRADE-09): a motor lépésekből
  számolt értéke csak előtöltés; rögzítéskor a kérés ``certainty`` mezője (az ember megerősítése) kötelező — nélküle
  422 ``needs_certainty`` + ``computed_certainty``; a motor ``certainty_source: human``-nal kapja.
- ``GET /api/grade/<kimenet>/advice[?run=&mid=]`` → ``szk.ma.grade-advice-view/v1``: ``{outcome_id, run, mid,
  advice}`` — az ``advice`` a motor ``grade_advice`` kimenete változatlanul (doménenkénti bizonyíték, KB-szabályok,
  kezdőknek szóló „Miért?” szöveg; csak javaslat — az ítélet mindig az emberé). A ``mid`` nyers szöveg, a motor
  parszolja.

A szöveges mezőkben (indoklás) cellaérték nem lehet; a hibaüzenetek mezőnevet mondanak, értéket nem (T10)."""
from metaelemzes import api

from ..router import ApiError, Result
from . import grade_engine as E, grade_protocol, grade_sof
from ._common import has_journal, journal_root, log_activity_or_warn, phi_doc_guard
from .grade_common import (check_if_match, content_etag, outcome_of, own_dir_writes, primary_run, require_run,
                           results_of, run_dir_abs, run_summary, text_or_none)

VIEW_SCHEMA = "szk.ma.grade-view/v1"
GRADE_SCHEMA = "szk.ma.grade/v1"
ADVICE_SCHEMA = "szk.ma.grade-advice-view/v1"
GRADE_DIR = "06_kezirat/grade"
GRADE_REL = GRADE_DIR + "/%s.grade.json"
MINUS = "−"

DOMAINS = ("risk_of_bias", "inconsistency", "indirectness", "imprecision", "publication_bias")
UPGRADES = ("large_effect", "dose_response", "opposing_confounding")
CERTAINTIES = ("high", "moderate", "low", "very low")
STARTS = ("high", "low")
# a 4.14 szókincse: ítélet → engedett lépések (az első az alapértelmezés); 'suspected': None = feloldatlan
RATINGS = {
    "risk_of_bias": (("not serious", (0,)), ("serious", (-1,)), ("very serious", (-2,))),
    "inconsistency": (("not serious", (0,)), ("serious", (-1,)), ("very serious", (-2,))),
    "indirectness": (("not serious", (0,)), ("serious", (-1,)), ("very serious", (-2,))),
    "imprecision": (("not serious", (0,)), ("serious", (-1,)), ("very serious", (-2,))),
    "publication_bias": (("undetected", (0,)), ("suspected", (None, 0, -1)), ("strongly suspected", (-1, -2))),
}
UPGRADE_STEPS = {"large_effect": (1, 2), "dose_response": (1,), "opposing_confounding": (1,)}
DOMAIN_HU = {"risk_of_bias": "torzítási kockázat", "inconsistency": "inkonzisztencia", "indirectness": "indirektség",
             "imprecision": "pontatlanság", "publication_bias": "publikációs torzítás"}
UPGRADE_HU = {"large_effect": "nagy hatás", "dose_response": "dózis–válasz összefüggés",
              "opposing_confounding": "ellentétes irányú zavaró tényezők"}
UPGRADE_EN = {"large_effect": "large effect", "dose_response": "dose-response",
              "opposing_confounding": "opposing plausible confounding"}
MAX_RATIONALE = 4000
MAX_MID = 64

_DOMAIN = {"type": ["object", "null"], "properties": {
    "rating": {"type": ["string", "null"], "maxLength": 40},
    "step": {"enum": [0, -1, -2, None]},
    "rationale": {"type": ["string", "null"], "maxLength": MAX_RATIONALE},
    "status": {"type": ["string", "null"], "maxLength": 20},
    "advisory": {"type": ["object", "null"]}}}
_UPDETAIL = {"type": ["object", "null"], "properties": {
    "step": {"enum": [1, 2, None]},
    "rationale": {"type": ["string", "null"], "maxLength": MAX_RATIONALE}}}
GRADE_DOC = {
    "type": "object", "required": ["domains"],
    "properties": {
        "schema": {"const": GRADE_SCHEMA},
        "outcome_id": {"type": ["string", "null"], "maxLength": 64},
        "importance": {"enum": ["critical", "important", "limited", "not important", None]},
        "run_id": {"type": ["string", "null"], "pattern": r"^\d{8}T\d{6}Z-[0-9a-f]{6}$"},
        "start": {"enum": list(STARTS) + [None]},
        "start_reason": {"type": ["string", "null"], "maxLength": 500},
        "domains": {"type": "object", "additionalProperties": False,
                    "properties": {d: _DOMAIN for d in DOMAINS}},
        "upgrades": {"type": ["object", "null"], "additionalProperties": False,
                     "properties": {u: {"type": ["boolean", "null"]} for u in UPGRADES}},
        "upgrade_details": {"type": ["object", "null"], "additionalProperties": False,
                            "properties": {u: _UPDETAIL for u in UPGRADES}},
        "certainty": {"enum": list(CERTAINTIES) + [None]},
        "mid_text": {"type": ["string", "null"], "maxLength": MAX_MID},
    },
}
PUT_REQUEST = {
    "type": "object", "required": ["grade"],
    "properties": {"grade": GRADE_DOC, "record": {"type": "boolean"},
                   "certainty": {"enum": list(CERTAINTIES) + [None]},
                   "client_seq": {"type": ["integer", "null"]}},
    "additionalProperties": False,
}

MSG_STALE = ("A GRADE alapjául szolgáló futás ELAVULT: az adattábla megváltozott a futás óta (X001). Futtasd újra "
             "az elsődleges elemzést (Elemzés → Rögzítés), és az új futásra rögzítsd a GRADE-ítéletet.")
MSG_OLD_RUN = ("Az ítélet nem a kimenet legutóbbi elsődleges futására hivatkozik (X007): nézd át a domén-ítéleteket "
               "az új futás számaival, majd mentsd újra.")


def vocab():
    """A 4.14 GRADE-szókincse a felületnek (egyetlen forrás: ez a modul / a szerződés)."""
    return {
        "domains": list(DOMAINS),
        "ratings": {d: [{"rating": r, "steps": list(steps)} for r, steps in RATINGS[d]] for d in DOMAINS},
        "upgrades": [{"id": u, "steps": list(UPGRADE_STEPS[u])} for u in UPGRADES],
        "certainties": list(CERTAINTIES),
        "starts": list(STARTS),
    }


def _allowed_steps(domain, rating):
    for r, steps in RATINGS[domain]:
        if r == rating:
            return steps
    return None


def _str(v):
    return v.strip() if isinstance(v, str) else ""


def problems(doc):
    """(hibák (422), feloldatlan domének, hiányzó domén-ítéletek) — csak a kitöltöttség és a szókincs ellenőrzése;
    a domén-lépések összhangja és a bizonyosság a motoré."""
    errors, unresolved, missing = [], [], []
    domains = (doc or {}).get("domains") or {}
    for d in DOMAINS:
        dom = domains.get(d) or {}
        rating = dom.get("rating")
        if rating in (None, ""):
            missing.append(d)
            continue
        steps = _allowed_steps(d, rating)
        if steps is None:
            errors.append("%s: ismeretlen ítélet" % DOMAIN_HU[d])
            continue
        step = dom.get("step")
        if step not in steps:
            if d == "publication_bias" and rating == "suspected":
                errors.append("%s: „gyanított” torzításnál csak 0 vagy −1 lépés választható" % DOMAIN_HU[d])
            else:
                errors.append("%s: a lépés nem illik az ítélethez" % DOMAIN_HU[d])
            continue
        if not _str(dom.get("rationale")):
            errors.append("%s: az ítélethez indoklás kell" % DOMAIN_HU[d])
        if d == "publication_bias" and rating == "suspected" and step is None:
            unresolved.append(d)
    ups = (doc or {}).get("upgrades") or {}
    det = (doc or {}).get("upgrade_details") or {}
    for u in UPGRADES:
        if ups.get(u):
            info = det.get(u) or {}
            if info.get("step") not in UPGRADE_STEPS[u]:
                errors.append("%s: a felminősítés lépése %s lehet" % (UPGRADE_HU[u], " vagy ".join(
                    "+%d" % s for s in UPGRADE_STEPS[u])))
            if not _str(info.get("rationale")):
                errors.append("%s: a felminősítéshez indoklás kell" % UPGRADE_HU[u])
    return errors, unresolved, missing


def normalize(doc, outcome, run):
    """A felületről jött ítélet a szerződés alakjában (schema, kimenet, futás, fontosság, publikációs torzítás
    állapota). A számított mezőket (certainty, consistency_warning, validator_rollup) a motor tölti."""
    d = dict(doc)
    d["schema"] = GRADE_SCHEMA
    d["outcome_id"] = outcome["id"]
    if d.get("importance") is None:
        d["importance"] = "critical" if outcome.get("critical") else "important"
    d["run_id"] = run["run_id"]
    if d.get("start") is None:
        d["start"] = outcome.get("grade_start") if outcome.get("grade_start") in STARTS else "high"
    domains = {}
    for k in DOMAINS:
        dom = dict((d.get("domains") or {}).get(k) or {})
        if dom.get("rating") in (None, ""):
            dom = {"rating": None, "step": None, "rationale": _str(dom.get("rationale")) or None}
        else:
            dom["rationale"] = _str(dom.get("rationale")) or None
        if k == "publication_bias":
            unresolved = dom.get("rating") == "suspected" and dom.get("step") is None
            dom["status"] = "unresolved" if unresolved else ("resolved" if dom.get("rating") else None)
        domains[k] = dom
    d["domains"] = domains
    ups = d.get("upgrades") or {}
    det = d.get("upgrade_details") or {}
    d["upgrades"] = {u: bool(ups.get(u)) for u in UPGRADES}
    d["upgrade_details"] = {u: {"step": (det.get(u) or {}).get("step"),
                                "rationale": _str((det.get(u) or {}).get("rationale")) or None}
                            for u in UPGRADES if ups.get(u)}
    d.pop("certainty", None)                # a motor számolja (grade_put)
    d["mid_text"] = text_or_none(d.get("mid_text"), MAX_MID, "mid_text")
    return d


def _latest_journal(app, outcome_id):
    if not has_journal(app):
        return None
    try:
        rows = api.project_list(str(app.project_root), "grades")
    except Exception:                                      # noqa: BLE001 — zárolt napló: nincs naplósor
        return None
    hit = None
    for r in rows or ():
        if isinstance(r, dict) and r.get("outcome") == outcome_id:
            hit = {"id": r.get("id"), "ts": r.get("ts"), "certainty": r.get("certainty")}
    return hit


def _outcome_info(outcome):
    return {"id": outcome.get("id"), "name": outcome.get("name"), "critical": bool(outcome.get("critical")),
            "measure": outcome.get("measure"), "grade_start": outcome.get("grade_start"),
            "data": outcome.get("data")}


def _computed(doc):
    fn = getattr(api, "grade_computed_certainty", None)
    if not callable(fn) or not isinstance(doc, dict):
        return None
    try:
        v = fn(doc)
    except Exception:                                      # noqa: BLE001 — csak tájékoztató mező
        return None
    return v if v in CERTAINTIES else None


def view(app, outcome, meta, run, doc):
    oid = outcome["id"]
    _errors, unresolved, missing = problems(doc) if doc else ([], [], list(DOMAINS))
    conv = (meta or {}).get("conventions") if isinstance((meta or {}).get("conventions"), dict) else {}
    return {
        "schema": VIEW_SCHEMA,
        "outcome": _outcome_info(outcome),
        "run": run_summary(run),
        "grade": doc,
        # a lépésekből adódó szint (motor) — a rögzített emberi bizonyosság mellett, ha eltér (FID-3)
        "computed_certainty": _computed(doc),
        "path": GRADE_REL % oid,
        "journal": _latest_journal(app, oid),
        "unresolved": unresolved,
        "missing": missing,
        "run_matches": None if not doc or not run else doc.get("run_id") == run.get("run_id"),
        "conventions": {"grade_suspected": conv.get("grade_suspected") or "unresolved"},
        "vocab": vocab(),
        "engine": E.available(),
    }


def _load(app, oid):
    """A mentett ítélet (a motor GRADE-tárából) vagy None; a tár hiánya 424."""
    doc = E.call("grade_get", project_dir=str(app.project_root), outcome=oid)
    if doc is not None and not isinstance(doc, dict):
        raise ApiError("INTERNAL", "A motor GRADE-tára nem objektumot adott.")
    return doc


def get_grade(req):
    app = req.app
    app.require_open()
    outcome, meta = outcome_of(app, req.params["outcome"])
    run = primary_run(app, outcome["id"], req.arg("run", max_len=40) or None)
    if not E.has("grade_get"):
        v = view(app, outcome, meta, run, None)
        try:
            E.need("grade_get")
        except ApiError as exc:
            exc.details = dict(exc.details or {}, view=v)
            raise
    doc = _load(app, outcome["id"])
    return Result(view(app, outcome, meta, run, doc), VIEW_SCHEMA, etag=content_etag(doc))


# ---------------------------------------------------------------------------- napló
def _signed(step):
    if not step:
        return "0"
    return ("+%d" % step) if step > 0 else ("%s%d" % (MINUS, -step))


def _journal_fields(app, doc, run, outcome):
    """A projektnapló GRADE-sorának mezői (projekt.add_grade): előjeles lépés-szövegek a doménekből, a k /
    résztvevő / hatás-szöveg a futásból (motor-számok és -szövegek, változatlanul — X007)."""
    out = {}
    for d in DOMAINS:
        dom = doc["domains"][d]
        out[d] = "%s %s: %s" % (_signed(dom.get("step")), dom.get("rating"), dom.get("rationale") or "")
    ups = [u for u in UPGRADES if (doc.get("upgrades") or {}).get(u)]
    if ups:
        det = doc.get("upgrade_details") or {}
        total = 0
        for u in ups:
            total += int((det.get(u) or {}).get("step") or 0)
        out["upgrades"] = "%s %s" % (_signed(total), "; ".join(
            "%s: %s" % (UPGRADE_EN[u], (det.get(u) or {}).get("rationale") or "") for u in ups))
    else:
        out["upgrades"] = "0 nincs felminősítés"
    if isinstance(run.get("k"), int):
        out["k"] = run["k"]
    res = results_of(app, run) or {}
    part = (res.get("totals") or {}).get("participants") if isinstance(res.get("totals"), dict) else None
    if isinstance(part, (int, float)) and not isinstance(part, bool) and float(part).is_integer():
        out["participants"] = int(part)
    prim = run.get("primary") if isinstance(run.get("primary"), dict) else {}
    disp = prim.get("display_text") if isinstance(prim.get("display_text"), dict) else {}
    if disp.get("hu"):
        out["effect"] = ("%s %s" % (run.get("measure") or "", disp["hu"])).strip()
    out["rationale"] = "GRADE a munkapadon (%s) · futás %s" % (GRADE_REL % outcome["id"], run.get("run_id"))
    return out


def _gate(doc, run, current):
    """A rögzítés feltételei → (kód, üzenet, részletek) vagy None."""
    _errors, unresolved, missing = problems(doc)
    if missing:
        return ("GATE_BLOCKED", "A rögzítéshez mind az öt domén ítélete kell; hiányzik: %s."
                % ", ".join(DOMAIN_HU[d] for d in missing), {"missing": missing})
    if unresolved:
        return ("GATE_BLOCKED", "A publikációs torzítás „gyanított” (suspected) ítélete feloldatlan (X019, 11/4. "
                "döntés): válaszd ki, hogy 0 vagy −1 lépés legyen, és indokold; addig a GRADE nem rögzíthető.",
                {"unresolved": unresolved, "code": "X019", "kb_refs": ["X019"]})
    if current is not None and current.get("run_id") != run.get("run_id"):
        return ("GATE_BLOCKED", MSG_OLD_RUN, {"code": "X007", "run_id": run.get("run_id"),
                                              "latest_run_id": current.get("run_id")})
    if run.get("stale") is True:
        return ("GATE_BLOCKED", MSG_STALE, {"code": "X001", "run_id": run.get("run_id")})
    return None


def _record(app, outcome, saved, run, human_certainty, warnings):
    """A projektnapló GRADE-sora. A végső bizonyosság EMBERI ítélet (GRADE-09, M5): a motor lépésekből számolt
    értéke (``saved.certainty``) csak előtöltés — a rögzítéshez a kérés ``certainty`` mezője kell (a felület az
    előtöltött választót küldi, az ember megerősíti vagy átírja), és a motor ``certainty_source: human``-nal kapja.
    Hiányában 422 ``needs_certainty`` + ``computed_certainty`` (az előtöltéshez)."""
    root = journal_root(app)
    oid = outcome["id"]
    computed = saved.get("certainty") if saved.get("certainty") in CERTAINTIES else None
    if human_certainty not in CERTAINTIES:
        raise ApiError("VALIDATION", "Az ítéletet elmentettem; a naplóba rögzítéshez erősítsd meg a bizonyosság "
                                     "szintjét (magas, mérsékelt, alacsony, nagyon alacsony): a végső bizonyosság "
                                     "emberi ítélet (GRADE-09)%s." % (
                                         " — a motor a lépésekből „%s” szintet számolt, ez csak előtöltés" % computed
                                         if computed else ""),
                       {"needs_certainty": True, "computed_certainty": computed, "kb_refs": ["GRADE-09"]})
    certainty = human_certainty
    doc = dict(saved, certainty=certainty, certainty_source="human")
    try:
        with own_dir_writes(app, [GRADE_DIR]):
            done, res = E.call_optional("grade_record", project_dir=root, outcome=oid, doc=doc, certainty=certainty,
                                        actor=app.actor, kb_db=app.kb_db)
    except ValueError as exc:
        # a motor elutasítása (X019, hiányzó domén, nem emberi bizonyosság, jóvá nem hagyott AI-vázlat) — szó szerint
        det = {"needs_certainty": bool(getattr(exc, "needs_certainty", False))}
        if getattr(exc, "computed_certainty", None):
            det["computed_certainty"] = getattr(exc, "computed_certainty")
        raise ApiError("VALIDATION", "A GRADE-ítélet nem rögzíthető: %s" % exc, det) from None
    if not done:
        res = api.project_grade(root, oid, certainty, kb_db=app.kb_db, actor=app.actor,
                                **_journal_fields(app, saved, run, outcome))
    res = res if isinstance(res, dict) else {}
    warnings.extend(_text(w) for w in res.get("warnings") or [])
    log_activity_or_warn(app, "grade.record", warnings,
                         details={"outcome": oid, "id": res.get("id"), "certainty": certainty,
                                  "run_id": run.get("run_id"), "certainty_source": "human",
                                  "computed_certainty": computed, "via": "engine" if done else "project_grade"})
    return {"id": res.get("id"), "certainty": certainty, "warnings": [_text(w) for w in res.get("warnings") or []],
            "doc": res.get("doc") if isinstance(res.get("doc"), dict) else None}


def _text(w):
    """A motor figyelmeztetése szövegként (a {hu, en} párból a magyar)."""
    if isinstance(w, dict):
        t = w.get("text") if isinstance(w.get("text"), dict) else w
        return str(t.get("hu") or t.get("en") or "")
    return str(w)


def put_grade(req):
    app = req.app
    app.require_open()
    outcome, meta = outcome_of(app, req.params["outcome"])
    oid = outcome["id"]
    body = req.json_object()
    E.need("grade_get")
    E.need("grade_put")
    cur = _load(app, oid)
    check_if_match(req, content_etag(cur))
    doc_in = body.get("grade") or {}
    run = require_run(app, oid, doc_in.get("run_id"))
    doc = normalize(doc_in, outcome, run)
    errors, unresolved, _missing = problems(doc)
    if errors:
        raise ApiError("VALIDATION", "A GRADE-ítélet hiányos vagy ellentmondásos: %s." % "; ".join(errors),
                       {"errors": errors})
    record = body.get("record") is True
    latest = primary_run(app, oid)
    if record:
        blocked = _gate(doc, run, latest)
        if blocked:
            raise ApiError(blocked[0], blocked[1], blocked[2])
    rel = GRADE_REL % oid
    phi_doc_guard(app, doc, rel, "GRADE-ítélet")
    with own_dir_writes(app, [GRADE_DIR]):
        saved = E.call("grade_put", project_dir=str(app.project_root), outcome=oid, doc=doc, actor=app.actor)
    if not isinstance(saved, dict):
        saved = _load(app, oid) or doc
    warnings = []
    cw = saved.get("consistency_warning")
    if cw:
        warnings.append(_text(cw))
    log_activity_or_warn(app, "grade.save", warnings, outputs=[{"path": rel, "sha256": content_etag(saved)}],
                         details={"outcome": oid, "run_id": run.get("run_id"), "unresolved": unresolved,
                                  "certainty": saved.get("certainty")})
    recorded = _record(app, outcome, saved, run, body.get("certainty"), warnings) if record else None
    if recorded is not None:
        # a motor a rögzítéskor a fájlt is frissíti ('recorded', journal_id): a friss változat és ETag kell
        saved = _load(app, oid) or recorded.pop("doc", None) or saved
        recorded.pop("doc", None)
    data = view(app, outcome, meta, latest or run, saved)
    data["recorded"] = recorded
    return Result(data, VIEW_SCHEMA, warnings=warnings, etag=content_etag(saved))


# ---------------------------------------------------------------------------- tanácsadó
def get_advice(req):
    app = req.app
    app.require_open()
    outcome, _meta = outcome_of(app, req.params["outcome"])
    oid = outcome["id"]
    mid = text_or_none(req.arg("mid", max_len=MAX_MID), MAX_MID, "mid")
    E.need("grade_advice")
    run = require_run(app, oid, req.arg("run", max_len=40) or None)
    advice = E.call("grade_advice", run=run_dir_abs(app, run), rob_by_row=None, mid=mid,
                    project_dir=str(app.project_root), outcome=oid, run_id=run.get("run_id"))
    if not isinstance(advice, dict):
        raise ApiError("INTERNAL", "A motor GRADE-tanácsadója nem objektumot adott.")
    return Result({"schema": ADVICE_SCHEMA, "outcome_id": oid, "run": run_summary(run), "mid": mid, "advice": advice},
                  ADVICE_SCHEMA)


def register(router):
    router.add("GET", "/api/grade/<outcome>", get_grade, schema=VIEW_SCHEMA)
    router.add("PUT", "/api/grade/<outcome>", put_grade, schema=VIEW_SCHEMA, request_schema=PUT_REQUEST)
    router.add("GET", "/api/grade/<outcome>/advice", get_advice, schema=ADVICE_SCHEMA)
    grade_sof.register(router)
    grade_protocol.register(router)
