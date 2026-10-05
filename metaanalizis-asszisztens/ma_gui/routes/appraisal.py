# -*- coding: utf-8 -*-
"""Értékelő eszközök és értékelések (terv 3.4, 3.5.10–3.5.13, 4.11, 5.4, 6.5; 11. fejezet 5–6. döntés).

- ``GET /api/instruments[?design=]`` → ``{instruments[], engine{available, missing}, validator{state, version,
  mode}, suggested[]?}`` — a motor eszköz-listája (natív definíciók; a validator csak keresztellenőrzés).
- ``GET /api/instruments/<tool>`` → ``szk.instrument/v1`` (a motorból).
- ``GET /api/appraisals[?tool=&unit=&target=&answers=1]`` → ``{items[], studies[], outcomes[], engine{…}}``:
  minden értékelés-fájl összesítője (értékelő, eredet, státusz, ítéletek, a motor teljesség-összesítője);
  ``answers=1``: a válaszértékek is (a TRIPOD+AI hőtérképhez; bizonyíték-szöveg nélkül).
- ``GET /api/appraisals/<unit>/<tool>?rater=&target=`` → ``{doc, check, raters[], study, …}`` + ETag; ha még
  nincs fájl, üres váz (``exists: false``, ETag nélkül).
- ``PUT /api/appraisals/<unit>/<tool>?rater=&target=`` ← ``{doc}`` + If-Match (új fájlnál nincs): alak- és
  motor-ellenőrzés; az implikálttól eltérő ítélet indoklás nélkül NEM menthető (X017), az indoklás döntésként a
  projektnaplóba kerül (``decision_id`` vissza a dokumentumba); ``complete`` csak teljes értékelésre, AI-vázlatnál
  csak jóváhagyás után; ``consensus`` csak két független emberi értékelés után; PHI-őr; activity szöveg nélkül.
- ``POST /api/appraisals/<unit>/<tool>/check?target=&rater=`` ← ``{doc}`` → ``{check, problems}`` (nem ment).
- ``POST /api/appraisals/<unit>/<tool>/approve?rater=&target=`` ← ``{approver, note?}`` + If-Match: AI-vázlat
  emberi jóváhagyása (6. döntés); a vázlat ettől sem lesz értékelő (κ, konszenzus).
- ``GET /api/appraisals/inbox`` → a ``04_torzitas_kockazat/appraisals/beerkezett/`` JSON-fájljai (5. döntés:
  a második értékelő fájlja ide is másolható — a böngészős 64 KB-os törzskorlát nélkül).
- ``POST /api/appraisals/import`` ← ``{doc | path, replace?}`` — a második értékelő JSON-ja (vagy csomagja);
  meglévő, eltérő fájlt csak ``replace: true`` ír felül (különben 409 a listával).
- ``POST /api/appraisals/export`` ← ``{unit, tool, target?, rater}`` | ``{all: true, rater?, tool?}`` →
  ``{filename, doc | bundle}`` a fájlcseréhez.

Ítéletet, teljességet, κ-t a szerver nem számol: mind a motoré (``appraisal_common.ENGINE``)."""
import os

from metaelemzes import api

from .. import security, store
from ..router import ApiError, Result
from . import appraisal_common as C
from ._common import (body_bool, body_str, has_journal, if_match, log_activity_or_warn, now_iso, phi_doc_guard,
                      phi_text_guard)

LIST_SCHEMA = "szk.ma.instruments/v1"
VIEW_SCHEMA = "szk.ma.appraisal-view/v1"
APPRAISALS_SCHEMA = "szk.ma.appraisals/v1"
CHECK_SCHEMA = "szk.ma.appraisal-check/v1"
INBOX_SCHEMA = "szk.ma.appraisal-inbox/v1"
IMPORT_SCHEMA = "szk.ma.appraisal-import/v1"
EXPORT_SCHEMA = "szk.ma.appraisal-export/v1"
MAX_BUNDLE = 500
STAGE = "S06"

_DOC_REQ = {"type": "object", "required": ["doc"],
            "properties": {"doc": {"type": "object"}, "client_seq": {"type": "integer", "minimum": 0}},
            "additionalProperties": False}
_APPROVE_REQ = {"type": "object", "required": ["approver"],
                "properties": {"approver": {"type": "string", "maxLength": 32},
                               "note": {"type": ["string", "null"], "maxLength": 4000},
                               "confirm": {"type": "boolean"}},
                "additionalProperties": False}
_IMPORT_REQ = {"type": "object",
               "properties": {"doc": {"type": "object"}, "path": {"type": "string", "maxLength": 1024},
                              "replace": {"type": "boolean"}},
               "additionalProperties": False}
_EXPORT_REQ = {"type": "object",
               "properties": {"unit": {"type": "string", "maxLength": 128}, "tool": {"type": "string", "maxLength": 32},
                              "target": {"type": ["string", "null"], "maxLength": 64},
                              "rater": {"type": ["string", "null"], "maxLength": 32}, "all": {"type": "boolean"}},
               "additionalProperties": False}


# ---------------------------------------------------------------------------- segédek
def _params(req, rater_required=True):
    unit = C.check_unit(req.params["unit"])
    tool = C.check_tool(req.params["tool"])
    target = C.check_target(req.arg("target"))
    rater = C.check_rater(req.arg("rater"), required=rater_required)
    return unit, tool, target, rater


def _validator_info(app):
    """A validator plugin állapota (csak tájékoztatás; az ítéletek forrása a motor)."""
    try:
        cap = app.caps.get("validator")
    except Exception:                                       # noqa: BLE001 — a szondázás hibája ne vigye el a listát
        return None
    return {"state": cap.get("state"), "version": cap.get("version"), "mode": cap.get("mode"),
            "guards": list(cap.get("guards") or [])}


def _studies(app):
    try:
        doc, _etag = app.store.load_json(store.STUDIES_REL)
    except store.StoreError:
        return []
    if not isinstance(doc, dict) or not isinstance(doc.get("studies"), list):
        return []
    out = []
    for s in doc["studies"]:
        if isinstance(s, dict) and isinstance(s.get("study_id"), str):
            out.append({k: s.get(k) for k in ("study_id", "label", "design", "outcomes", "registration")})
    return out


def _outcomes(app):
    meta, _ = app.project_meta_safe()
    out = []
    for oc in (meta or {}).get("outcomes") or []:
        if isinstance(oc, dict) and isinstance(oc.get("id"), str):
            out.append({"id": oc["id"], "name": oc.get("name"), "data": oc.get("data"), "measure": oc.get("measure"),
                        "critical": oc.get("critical")})
    return out


def _study(app, unit):
    for s in _studies(app):
        if s["study_id"] == unit:
            return s
    return None


def _norm_etag(tag):
    if tag is None:
        return None
    t = str(tag).strip()
    if t.startswith("W/"):
        t = t[2:]
    return t.strip().strip('"').strip().lower() or None


def _precheck(rel, im, cur_etag):
    """Az If-Match előzetes ellenőrzése (a naplóbejegyzések ELŐTT; a tényleges írás újra ellenőrzi)."""
    want = _norm_etag(im)
    ok = (cur_etag is None) if want is None else (want == "*" and cur_etag is not None) or want == cur_etag
    if not ok:
        raise store.Conflict(rel, cur_etag)


def _raters(sibs):
    return [{"rater": s["rater"], "path": s["path"], "origin": s["doc"].get("origin"), "status": s["doc"].get("status"),
             "assessor": s["doc"].get("assessor"), "approved_by": s["doc"].get("approved_by"),
             "updated": s["doc"].get("updated"), "human": C.is_human(s["doc"], s["rater"])} for s in sibs]


def _view(app, unit, tool, target, rater, inst, doc, etag, exists):
    res = C.check(doc, inst, str(app.project_root))
    sibs = C.siblings(app, unit, tool, target)
    return {"schema": VIEW_SCHEMA, "unit": unit, "tool": tool, "target": target, "rater": rater,
            "path": C.relpath(unit, tool, target, rater), "exists": exists, "etag": etag, "doc": doc, "check": res,
            "instrument": {"key": inst.get("key") or tool, "name": inst.get("name"),
                           "reference_sha256": inst.get("reference_sha256"), "unit": inst.get("unit")},
            "raters": _raters(sibs), "study": _study(app, unit),
            "human_raters": sorted({s["rater"] for s in sibs if C.is_human(s["doc"], s["rater"])})}


def _load_checked(app, rel, unit):
    doc, etag = C.load(app, rel)
    if doc is not None:
        u = C.unit_of(doc)
        if u is not None and u != unit:
            raise ApiError("CONFLICT", "A(z) %s fájl egy másik vizsgálathoz (%s) tartozik — a két azonosító ugyanarra "
                                       "a fájlnévre képeződik. Nevezd át az egyik vizsgálatot a studies.json-ban."
                           % (rel, u), {"path": rel})
    return doc, etag


# ---------------------------------------------------------------------------- eszközök
def get_instruments(req):
    app = req.app
    items = C.instruments()
    data = {"schema": LIST_SCHEMA, "instruments": items, "source": "engine", "engine": C.engine_status(),
            "validator": _validator_info(app)}
    design = req.arg("design", max_len=200)
    if design:
        fn = C.engine_fn("route")
        sugg = []
        if fn is not None:
            try:
                sugg = fn(design) or []
            except ValueError:
                sugg = []
        data["suggested"] = sugg
    return Result(data, LIST_SCHEMA)


def get_instrument(req):
    inst = C.instrument(req.params["tool"])
    return Result(inst, C.SCHEMA_INSTRUMENT)


# ---------------------------------------------------------------------------- lista
def _summary(doc, res, with_answers):
    # a lista-elem 'target' mezője a fájlnév cél-KULCSA (szöveg, a felület ezzel szűr); a dokumentum cél-objektuma
    # külön néven (target_obj) — különben felülírná a kulcsot (UX-1/UX-2: a felület objektumot kapott szöveg helyett)
    out = {"origin": doc.get("origin"), "status": doc.get("status"), "assessor": doc.get("assessor"),
           "second_assessor": doc.get("second_assessor"), "approved_by": doc.get("approved_by"),
           "updated": doc.get("updated"), "scope": doc.get("scope"),
           "target_obj": doc.get("target") if isinstance(doc.get("target"), dict) else None,
           "domain_judgements": [{"domain": d.get("domain"), "pass": d.get("pass"), "judgement": d.get("judgement")}
                                 for d in doc.get("domain_judgements") or () if isinstance(d, dict)],
           "applicability": [{"domain": d.get("domain"), "pass": d.get("pass"), "judgement": d.get("judgement")}
                             for d in doc.get("applicability") or () if isinstance(d, dict)],
           "overall_passes": [{"pass": d.get("pass"), "judgement": d.get("judgement")}
                              for d in doc.get("overall_passes") or () if isinstance(d, dict)],
           "overall": {"judgement": (doc.get("overall") or {}).get("judgement")}
           if isinstance(doc.get("overall"), dict) else None}
    if with_answers:
        out["answers"] = {k: (v.get("value") if isinstance(v, dict) else None)
                          for k, v in (doc.get("answers") or {}).items()}
    if res is not None:
        keep = ("complete", "expected", "answered", "completeness_text", "per_pass", "tripod", "amstar2", "nos")
        out["check"] = {k: res.get(k) for k in keep if k in res}
        out["check"]["domains"] = [{"domain": d.get("domain"), "pass": d.get("pass"), "implied": d.get("implied"),
                                    "algorithm": d.get("algorithm")}
                                   for d in res.get("domains") or () if isinstance(d, dict)]
        ov = res.get("overall") if isinstance(res.get("overall"), dict) else {}
        out["check"]["overall"] = {"implied": ov.get("implied"), "algorithm": ov.get("algorithm")}
        out["check"]["overrides_missing"] = sum(1 for o in res.get("overrides") or () if o.get("reason_missing"))
    return out


def list_appraisals(req):
    app = req.app
    app.require_open()
    tool = C.check_tool(req.arg("tool")) if req.arg("tool") else None
    unit = C.check_unit(req.arg("unit", max_len=128)) if req.arg("unit") else None
    target = C.check_target(req.arg("target"))
    with_answers = req.arg("answers") in ("1", "true")
    status = C.engine_status()
    can_check = status["available"]
    insts = {}
    items = []
    warnings = []
    for f in C.list_files(app):
        if tool and f["tool"] != tool:
            continue
        if unit and f["slug"] != C.slug(unit):
            continue
        if req.arg("target") is not None and f["target"] != target:
            continue
        item = {"path": f["path"], "tool": f["tool"], "target": f["target"], "rater": f["rater"], "unit": f["slug"]}
        try:
            doc, etag = C.load(app, f["path"])
        except store.StoreError as exc:
            item["error"] = exc.message if hasattr(exc, "message") else str(exc)
            items.append(item)
            continue
        if doc is None:
            continue
        item["unit"] = C.unit_of(doc) or f["slug"]
        if unit and item["unit"] != unit:
            continue
        item["etag"] = etag
        item["human"] = C.is_human(doc, f["rater"])
        res = None
        if can_check:
            try:
                if f["tool"] not in insts:
                    insts[f["tool"]] = C.instrument(f["tool"])
                res = C.check(doc, insts[f["tool"]], str(app.project_root))
            except ApiError as exc:
                item["check_error"] = exc.message
            except ValueError as exc:
                item["check_error"] = str(exc)[:500]
        item.update(_summary(doc, res, with_answers))
        items.append(item)
    if not can_check:
        warnings.append("A motor értékelő funkciói (%s) ebben a motorváltozatban még nem érhetők el: a lista a "
                        "fájlokból készült, teljesség és implikált ítélet nélkül." % ", ".join(status["missing"]))
    data = {"schema": APPRAISALS_SCHEMA, "dir": C.APPRAISAL_DIR, "items": items, "studies": _studies(app),
            "outcomes": _outcomes(app), "engine": status}
    return Result(data, APPRAISALS_SCHEMA, warnings=warnings)


# ---------------------------------------------------------------------------- egy értékelés
def get_appraisal(req):
    app = req.app
    app.require_open()
    unit, tool, target, rater = _params(req)
    inst = C.instrument(tool)
    rel = C.relpath(unit, tool, target, rater)
    doc, etag = _load_checked(app, rel, unit)
    exists = doc is not None
    if not exists:
        doc = C.skeleton(unit, tool, target, rater, inst)
    return Result(_view(app, unit, tool, target, rater, inst, doc, etag, exists), VIEW_SCHEMA, etag=etag)


def _human_pair(app, unit, tool, target, doc, planned=()):
    """A konszenzus-változathoz: a két megnevezett értékelő független emberi értékelése megvan-e. planned: az importált
    csomag többi (még nem írt) eleme — ugyanabban a csomagban érkező két emberi értékelés is számít (SEC-3)."""
    sibs = {s["rater"]: s for s in C.siblings(app, unit, tool, target) if C.is_human(s["doc"], s["rater"])}
    for p in planned:
        if (p["unit"], p["tool"], p["target"]) == (unit, tool, target) and C.is_human(p["doc"], p["rater"]):
            sibs[p["rater"]] = p
    missing = [r for r in (doc.get("assessor"), doc.get("second_assessor")) if r not in sibs]
    if missing:
        raise ApiError("VALIDATION", "Konszenzus csak két független emberi értékelés után menthető (AI-vázlat nem "
                                     "számít értékelőnek). Hiányzik: %s." % ", ".join(str(m) for m in missing),
                       {"missing_raters": missing, "human_raters": sorted(sibs)})


def _require_rules(doc, res, inst):
    """A mentés tartalmi szabályai (422): indoklás nélküli felülbírálás (X017), 'complete' feltételei."""
    miss = [o for o in res.get("overrides") or () if o.get("reason_missing")]
    if miss:
        names = ", ".join("%s%s" % ("összítélet" if o.get("domain") == "overall" else "D" + str(o.get("domain")),
                                    " (%s)" % o["pass"] if o.get("pass") else "") for o in miss)
        raise ApiError("VALIDATION", "Az ítéleted eltér a motor implikált ítéletétől (%s), de nincs indoklás. Írd le "
                                     "röviden, miért döntöttél másképp — az indoklás döntésként a projektnaplóba kerül "
                                     "(X017)." % names, {"overrides": miss})
    if doc.get("status") in ("complete", "consensus"):
        if res.get("complete") is not True:
            raise ApiError("VALIDATION", "Az értékelés még nem teljes (kitöltve: %s / %s); 'kész' státusz csak minden "
                                         "tétel megválaszolása után adható. A hiányzó tételek: lásd check.missing."
                           % (res.get("answered", "—"), res.get("expected", "—")),
                           {"missing": res.get("missing") or []})
        if doc.get("origin") == "ai_draft" and not doc.get("approved_by"):
            raise ApiError("VALIDATION", "AI-vázlat csak emberi jóváhagyás után lehet kész (6. döntés). Nézd át a "
                                         "tételeket, majd használd a „Jóváhagyás” gombot.")
        ov = doc.get("overall") if isinstance(doc.get("overall"), dict) else {}
        alg = ((res.get("overall") or {}) if isinstance(res.get("overall"), dict) else {}).get("algorithm")
        holistic = [ov] + [x for x in doc.get("overall_passes") or () if isinstance(x, dict)]
        if alg == "none" and any(x.get("judgement") and not (isinstance(x.get("rationale"), str)
                                                             and x["rationale"].strip()) for x in holistic):
            raise ApiError("VALIDATION", "Az összítélet ennél az eszköznél holisztikus (nincs hozzá algoritmus): az "
                                         "indoklás kötelező (PROBAST+AI-nál menetenként is).")


def _log_overrides(app, doc, res, unit, tool, target, rel, warnings):
    """Az indokolt felülbírálások döntésként a naplóba (csak az új / megváltozott indoklásúak) → decision_id."""
    if not has_journal(app):
        if any(not o.get("reason_missing") for o in res.get("overrides") or ()):
            warnings.append("Nincs projektnapló (projekt.sqlite): a felülbírálás indoklása csak az értékelés-fájlba "
                            "került (decision_id nélkül).")
        return 0
    n = 0
    for o in res.get("overrides") or ():
        if o.get("reason_missing"):
            continue
        if o.get("domain") == "overall":
            target_obj = doc.get("overall")
        else:
            target_obj = next((d for d in doc.get("domain_judgements") or () if isinstance(d, dict)
                               and str(d.get("domain")) == str(o.get("domain")) and d.get("pass") == o.get("pass")), None)
        if not isinstance(target_obj, dict) or target_obj.get("decision_id"):
            continue
        reason = target_obj.get("override_reason")
        warnings += phi_text_guard(app, [("override_reason", reason)])
        where = "összítélet" if o["domain"] == "overall" else "D%s%s" % (o["domain"], " (%s)" % o["pass"] if o.get("pass") else "")
        # az értékelő neve NEM kerül a napló szövegébe (SEC-5: B/C osztályban a pillanatkép és az audit-csomag
        # monogrammá álnevesíti a neveket; a szabad szövegben ez nem menne) — az értékelés-fájl útja a context-ben van
        text = "Értékelés felülbírálása — %s · %s%s · %s: implikált %s → %s" % (
            tool, unit, " · " + target if target else "", where, o.get("implied"), o.get("judgement"))
        ctx = {"kind": "other", "code": "X017", "dataset": rel, "fields": [where]}
        try:
            out = api.project_log(str(app.project_root), "user", text, rationale=reason, stage=STAGE, kb_db=app.kb_db,
                                  actor=app.actor, context=ctx)
        except ValueError:
            out = api.project_log(str(app.project_root), "user", text, rationale=reason, stage=STAGE, kb_db=app.kb_db,
                                  actor=app.actor)
        target_obj["decision_id"] = out.get("id")
        warnings += list(out.get("warnings") or [])
        n += 1
    return n


def _prepare(app, doc, cur, unit, inst):
    new = dict(doc)
    tg = dict(new.get("target") or {})
    tg["unit"] = unit
    if unit not in C.SPECIAL_UNITS:
        tg.setdefault("study_id", unit)
        if tg.get("study_id") is None:
            tg["study_id"] = unit
    key = tg.get("key")
    if key and tg.get("outcome") is None and any(o["id"] == key for o in _outcomes(app)):
        tg["outcome"] = key                 # RoB 2 / ROBINS: eredményenként — a motor X003-a a target.outcome-ot nézi
    new["target"] = tg
    now = now_iso()
    new["created"] = (cur or {}).get("created") or new.get("created") or now
    new["updated"] = now
    if not new.get("instrument_sha256"):
        # a motor definíciójának hash-e (nem a validator-forrásfájlé): a motor ezzel ismeri fel, hogy a dokumentum a
        # mostani tételszámozással készült (ROBINS-I 2016 / QUIPS átszámozás — numbering_note)
        new["instrument_sha256"] = inst.get("sha256") or inst.get("reference_sha256")
    new.setdefault("schema", C.SCHEMA_DOC)
    return new


APPROVAL_FIELDS = ("approved_by", "approved_at", "approval_decision_id")
_CONTENT_FIELDS = ("answers", "domain_judgements", "applicability", "overall", "overall_passes", "scope")


def _content_of(doc):
    """Az értékelés érdemi tartalma (válaszok, ítéletek, hatókör) — a jóváhagyás ehhez kötődik."""
    return store.json_bytes({k: (doc or {}).get(k) for k in _CONTENT_FIELDS})


def _server_approval(doc, cur, warnings):
    """SEC-1 (6. döntés): AI-vázlatot CSAK a /approve végpont hagyhat jóvá (If-Match + napló-döntés). A kliens által
    küldött approved_by / approved_at / approval_decision_id NEM számít: a mentett fájl értéke marad (új fájlnál
    null). Ha egy jóváhagyott AI-vázlat tartalma (válasz, ítélet) megváltozik, a jóváhagyás érvényét veszti: törlődik,
    és a státusz visszaáll vázlatra — az új tartalmat újra jóvá kell hagyni. Eredetet sem lehet „átmosni”: meglévő
    AI-vázlatból mentéssel nem lesz emberi értékelés."""
    new = dict(doc)
    for k in APPROVAL_FIELDS:
        new[k] = (cur or {}).get(k) if cur is not None else None
    if cur is not None and cur.get("origin") == "ai_draft" and new.get("origin") != "ai_draft":
        raise ApiError("VALIDATION", "Ez a fájl AI-vázlat: mentéssel nem válhat emberi értékeléssé (6. döntés). Töltsd "
                                     "ki a saját értékelésedet a saját monogramoddal.")
    if new.get("origin") == "ai_draft" and new.get("approved_by") and _content_of(new) != _content_of(cur):
        for k in APPROVAL_FIELDS:
            new[k] = None
        if new.get("status") == "complete":
            new["status"] = "draft"
        warnings.append("A jóváhagyott AI-vázlat tartalma megváltozott, ezért a jóváhagyás érvényét vesztette: a "
                        "vázlat újra vázlat — nézd át, és hagyd jóvá újra.")
    return new


def put_appraisal(req):
    app = req.app
    app.require_open()
    unit, tool, target, rater = _params(req)
    body = req.json_object()
    inst = C.instrument(tool)
    rel = C.relpath(unit, tool, target, rater)
    cur, cur_etag = _load_checked(app, rel, unit)
    warnings = []
    doc = body["doc"]
    if isinstance(doc, dict):
        doc = _server_approval(doc, cur, warnings)
    probs = C.doc_problems(doc, inst, unit, tool, target, rater)
    if not probs:
        probs = C.validate_engine(doc, inst, str(app.project_root))
    if probs:
        raise ApiError("VALIDATION", "Az értékelés alakja hibás: %s%s" % ("; ".join(probs[:6]), " …" if len(probs) > 6 else ""),
                       {"problems": probs[:200]})
    res = C.check(doc, inst, str(app.project_root))
    _require_rules(doc, res, inst)
    if rater == C.RATER_CONSENSUS:
        _human_pair(app, unit, tool, target, doc)
    phi_doc_guard(app, doc, rel, "értékelés")
    im = if_match(req)
    _precheck(rel, im, cur_etag)
    new = _prepare(app, doc, cur, unit, inst)
    n_dec = _log_overrides(app, new, res, unit, tool, target, rel, warnings)
    sha = app.store.write_bytes(rel, store.json_bytes(new), if_match=im)
    log_activity_or_warn(app, "appraisal.save", warnings, outputs=[(rel, sha)],
                         details={"tool": tool, "unit": unit, "target": target, "rater": rater,
                                  "origin": new.get("origin"), "status": new.get("status"),
                                  "complete": res.get("complete"), "decisions": n_dec})
    saved, etag = C.load(app, rel)
    return Result(_view(app, unit, tool, target, rater, inst, saved, etag, True), VIEW_SCHEMA, warnings=warnings,
                  etag=etag)


def post_check(req):
    app = req.app
    app.require_open()
    unit = C.check_unit(req.params["unit"])
    tool = C.check_tool(req.params["tool"])
    target = C.check_target(req.arg("target"))
    body = req.json_object()
    doc = body["doc"]
    inst = C.instrument(tool)
    rater = C.check_rater(req.arg("rater") or doc.get("assessor"), required=False) or doc.get("assessor")
    probs = C.doc_problems(doc, inst, unit, tool, target, rater)
    try:
        res = C.check(doc, inst, str(app.project_root))
    except ValueError as exc:
        return Result({"schema": CHECK_SCHEMA, "check": None, "problems": probs + [str(exc)[:500]]}, CHECK_SCHEMA)
    return Result({"schema": CHECK_SCHEMA, "check": res, "problems": probs}, CHECK_SCHEMA)


def post_approve(req):
    app = req.app
    app.require_open()
    unit, tool, target, rater = _params(req)
    body = req.json_object()
    approver = C.check_rater(body.get("approver"), allow_reserved=False)
    note = body_str(body, "note", max_len=4000)
    inst = C.instrument(tool)
    rel = C.relpath(unit, tool, target, rater)
    cur, cur_etag = _load_checked(app, rel, unit)
    if cur is None:
        raise ApiError("NOT_FOUND", "Nincs ilyen értékelés: %s" % rel)
    if cur.get("origin") != "ai_draft":
        raise ApiError("VALIDATION", "Csak AI-vázlat hagyható jóvá; ez az értékelés ember által kitöltött.")
    if cur.get("approved_by"):
        raise ApiError("CONFLICT", "Ezt az AI-vázlatot már jóváhagyta: %s." % cur["approved_by"], {"path": rel})
    im = if_match(req)
    if im is None:
        raise ApiError("BAD_REQUEST", "A jóváhagyáshoz If-Match kell (a betöltött változat ETag-je): csak azt hagyhatod "
                                      "jóvá, amit láttál.")
    _precheck(rel, im, cur_etag)
    new = dict(cur)
    new["approved_by"] = approver
    new["approved_at"] = now_iso()
    res = C.check(new, inst, str(app.project_root))
    _require_rules(dict(new, status="draft"), res, inst)
    warnings = []
    if res.get("complete") is True:
        new["status"] = "complete"
    else:
        warnings.append("A vázlat jóváhagyva, de még nem teljes (kitöltve: %s / %s): a 'kész' státuszhoz töltsd ki a "
                        "hiányzó tételeket." % (res.get("answered", "—"), res.get("expected", "—")))
    # 6. döntés a motorral: tételenkénti egyszerű nyelvű indoklás + idézet (vagy bizonytalanság-jelzés) nélkül nincs
    # jóváhagyás; a lezáráshoz (complete) a GRADE-feloldás (4. döntés) és a többi motor-szabály is kell — ha csak a
    # lezárás akad el, a vázlat jóváhagyva, de vázlat marad
    probs = C.validate_engine(new, inst, str(app.project_root))
    if probs and new.get("status") == "complete":
        if not C.validate_engine(dict(new, status="draft"), inst, str(app.project_root)):
            warnings.append("A vázlat jóváhagyva, de még nem zárható le: %s" % "; ".join(probs[:3]))
            new["status"], probs = "draft", []
    if probs:
        raise ApiError("VALIDATION", "Az AI-vázlat nem hagyható jóvá (6. döntés: minden kitöltött tételhez egyszerű "
                                     "nyelvű indoklás és idézet vagy bizonytalanság-jelzés kell): %s%s"
                       % ("; ".join(probs[:6]), " …" if len(probs) > 6 else ""), {"problems": probs[:200]})
    new["updated"] = new["approved_at"]
    decision_id = None
    if has_journal(app):
        if note:
            warnings += phi_text_guard(app, [("note", note)])
        text = ("AI-vázlat jóváhagyva — %s · %s%s (jóváhagyó: %s). A vázlat nem számít értékelőnek (κ, konszenzus)."
                % (tool, unit, " · " + target if target else "", approver))
        out = api.project_log(str(app.project_root), "user", text, rationale=note, stage=STAGE, kb_db=app.kb_db,
                              actor=app.actor)
        decision_id = out.get("id")
        warnings += list(out.get("warnings") or [])
        new["approval_decision_id"] = decision_id
    sha = app.store.write_bytes(rel, store.json_bytes(new), if_match=im)
    log_activity_or_warn(app, "appraisal.approve", warnings, outputs=[(rel, sha)],
                         details={"tool": tool, "unit": unit, "target": target, "rater": rater, "approver": approver,
                                  "complete": res.get("complete"), "decision_id": decision_id})
    saved, etag = C.load(app, rel)
    return Result(_view(app, unit, tool, target, rater, inst, saved, etag, True), VIEW_SCHEMA, warnings=warnings,
                  etag=etag)


# ---------------------------------------------------------------------------- fájlcsere (5. döntés)
def _read_project_json(app, rel):
    path = app.store.path(rel)
    try:
        st = path.stat()
    except FileNotFoundError:
        raise ApiError("NOT_FOUND", "Nincs ilyen fájl: %s" % rel) from None
    if st.st_size > C.MAX_IMPORT_BYTES:
        raise ApiError("PAYLOAD_TOO_LARGE", "A fájl túl nagy (legfeljebb %d MB)." % (C.MAX_IMPORT_BYTES // (1024 * 1024)))
    raw = path.read_bytes()
    return security.loads_limited(raw, max_bytes=C.MAX_IMPORT_BYTES), store.sha256_bytes(raw)


def _docs_of(payload):
    if isinstance(payload, dict) and payload.get("schema") == C.SCHEMA_BUNDLE:
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise ApiError("VALIDATION", "Az értékelés-csomag üres vagy hibás (items lista kell).")
        if len(items) > MAX_BUNDLE:
            raise ApiError("PAYLOAD_TOO_LARGE", "Túl sok értékelés egy csomagban (legfeljebb %d)." % MAX_BUNDLE)
        return list(items), "bundle"
    if isinstance(payload, dict) and payload.get("schema") == C.SCHEMA_DOC:
        return [payload], "doc"
    raise ApiError("VALIDATION", "A fájl nem értékelés (szk.appraisal/v1) és nem értékelés-csomag (%s)." % C.SCHEMA_BUNDLE)


def _dest_of(doc):
    if not isinstance(doc, dict):
        raise ApiError("VALIDATION", "Az értékelés objektum legyen.")
    tool = C.check_tool(doc.get("tool"))
    unit = C.unit_of(doc)
    if unit is None:
        raise ApiError("VALIDATION", "Az értékelésből hiányzik a vizsgálat azonosítója (target.study_id).")
    C.check_unit(unit)
    tg = doc.get("target") if isinstance(doc.get("target"), dict) else {}
    key = tg.get("key")
    if not key:
        try:
            key = C.target_key(doc, C.instrument(tool))
        except ApiError:
            key = None
    target = C.check_target(key)
    if C.is_consensus_doc(doc):
        rater = C.RATER_CONSENSUS
    elif doc.get("origin") == "ai_draft" and not (isinstance(doc.get("assessor"), str)
                                                  and C.RATER_RE.match(doc["assessor"])
                                                  and doc["assessor"].lower() not in C.RESERVED_RATERS):
        rater = C.RATER_AI
    else:
        rater = C.check_rater(doc.get("assessor"))
    return unit, tool, target, rater


def _import_approval(doc, where, warnings):
    """SEC-1 az importnál: a beérkezett fájl jóváhagyás-mezői nem számítanak (jóváhagyni csak a /approve végponton,
    a saját projektnaplóba írt döntéssel lehet). Jóváhagyottnak jelölt AI-vázlat → a jelölés törlődik, és vázlatként
    kerül be (a 'kész' státusz is vázlatra áll vissza)."""
    if not isinstance(doc, dict):
        return doc
    if not any(doc.get(k) for k in APPROVAL_FIELDS):
        return doc
    new = dict(doc)
    for k in APPROVAL_FIELDS:
        new[k] = None
    if new.get("origin") == "ai_draft":
        if new.get("status") == "complete":
            new["status"] = "draft"
        warnings.append("%s: AI-vázlat jóváhagyás-jelöléssel érkezett — a jelölést az import nem veszi át (6. döntés: "
                        "jóváhagyni csak itt, a „Jóváhagyás” gombbal lehet). Vázlatként importálva." % where.capitalize())
    return new


def get_inbox(req):
    app = req.app
    app.require_open()
    d = app.store.path(C.INBOX_DIR)
    files = []
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.name.startswith(".") or not p.name.lower().endswith(".json"):
                continue
            rel = "%s/%s" % (C.INBOX_DIR, p.name)
            item = {"path": rel, "name": p.name, "bytes": p.stat().st_size}
            try:
                payload, sha = _read_project_json(app, rel)
                docs, kind = _docs_of(payload)
                item.update(sha256=sha, kind=kind, items=len(docs),
                            preview=[{"tool": x.get("tool"), "unit": C.unit_of(x), "assessor": x.get("assessor"),
                                      "origin": x.get("origin"), "status": x.get("status")}
                                     for x in docs[:20] if isinstance(x, dict)])
            except (ApiError, security.SecurityError, store.StoreError, OSError) as exc:
                item.update(kind="unknown", error=getattr(exc, "message", None) or type(exc).__name__)
            files.append(item)
    return Result({"schema": INBOX_SCHEMA, "dir": C.INBOX_DIR, "files": files}, INBOX_SCHEMA)


def post_import(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    replace = body_bool(body, "replace")
    src = None
    if body.get("path") is not None:
        rel = app.store.rel(body["path"])
        if not (rel.startswith(C.APPRAISAL_DIR + "/") and rel.lower().endswith(".json")):
            raise ApiError("FORBIDDEN", "Importálni csak a %s/ mappából lehet (JSON-fájl)." % C.APPRAISAL_DIR)
        try:
            security.check_relpath(rel)
        except security.UnsafePath as exc:
            raise ApiError("FORBIDDEN", exc.message) from None
        payload, sha = _read_project_json(app, rel)
        src = (rel, sha)
    elif isinstance(body.get("doc"), dict):
        payload = body["doc"]
    else:
        raise ApiError("BAD_REQUEST", "Az import törzse {doc} (a fájl tartalma) vagy {path} (a beérkezett mappában).")
    docs, kind = _docs_of(payload)
    plan = []
    insts = {}
    warnings = []
    root = str(app.project_root)
    for i, doc in enumerate(docs):
        where = "%d. értékelés" % (i + 1) if len(docs) > 1 else "az értékelés"
        unit, tool, target, rater = _dest_of(doc)
        if tool not in insts:
            insts[tool] = C.instrument(tool)
        inst = insts[tool]
        rel = C.relpath(unit, tool, target, rater)
        if any(p["path"] == rel for p in plan):
            raise ApiError("VALIDATION", "A csomagban kétszer szerepel ugyanaz az értékelés: %s" % rel)
        cur, cur_etag = _load_checked(app, rel, unit)
        doc = _import_approval(doc, where, warnings)
        probs = C.doc_problems(doc, inst, unit, tool, target, rater) or C.validate_engine(doc, inst, root)
        if probs:
            raise ApiError("VALIDATION", "Hibás %s (%s · %s): %s" % (where, tool, unit, "; ".join(probs[:5])),
                           {"index": i, "problems": probs[:100]})
        # SEC-3: ugyanazok a tartalmi szabályok, mint a PUT-nál (teljesség a 'kész' státuszhoz, X017-indoklás,
        # AI-vázlat jóváhagyása, holisztikus összítélet indoklása)
        res = C.check(doc, inst, root)
        try:
            _require_rules(doc, res, inst)
        except ApiError as exc:
            raise ApiError(exc.code, "Hibás %s (%s · %s): %s" % (where, tool, unit, exc.message),
                           dict(exc.details or {}, index=i)) from None
        phi_doc_guard(app, doc, rel, "értékelés")
        data = store.json_bytes(doc)
        state = "new"
        if cur is not None:
            state = "same" if store.json_bytes(cur) == data else "conflict"
        plan.append({"path": rel, "unit": unit, "tool": tool, "target": target, "rater": rater, "data": data,
                     "doc": doc, "res": res, "index": i, "where": where,
                     "state": state, "etag": cur_etag, "origin": doc.get("origin"), "status": doc.get("status")})
    # konszenzus csak két független emberi értékelés után — a már mentett fájlok ÉS a csomag többi eleme alapján
    for p in plan:
        if p["rater"] == C.RATER_CONSENSUS:
            others = [q for q in plan if q is not p]
            try:
                _human_pair(app, p["unit"], p["tool"], p["target"], p["doc"], others)
            except ApiError as exc:
                raise ApiError(exc.code, "Hibás %s (%s · %s): %s" % (p["where"], p["tool"], p["unit"], exc.message),
                               dict(exc.details or {}, index=p["index"])) from None
    conflicts = [p for p in plan if p["state"] == "conflict"]
    if conflicts and not replace:
        raise ApiError("CONFLICT", "%d értékelés már létezik ugyanezzel az értékelővel, más tartalommal. Ha a beérkezett "
                                   "változat a helyes, importáld újra „felülírás” jelöléssel." % len(conflicts),
                       {"conflicts": [{k: p[k] for k in ("path", "unit", "tool", "target", "rater")} for p in conflicts]})
    outputs, result = [], []
    n_dec = 0
    for p in plan:
        etag = p["etag"]
        if p["state"] != "same":
            # az indokolt felülbírálások döntésként a naplóba (mint a PUT-nál) — a decision_id a fájlba kerül
            n = _log_overrides(app, p["doc"], p["res"], p["unit"], p["tool"], p["target"], p["path"], warnings)
            if n:
                n_dec += n
                p["data"] = store.json_bytes(p["doc"])
            etag = app.store.write_bytes(p["path"], p["data"], if_match=p["etag"])
            outputs.append((p["path"], etag))
        result.append({"path": p["path"], "unit": p["unit"], "tool": p["tool"], "target": p["target"],
                       "rater": p["rater"], "etag": etag, "origin": p["origin"], "status": p["status"],
                       "state": "replaced" if p["state"] == "conflict" else p["state"]})
    if any(p["origin"] == "ai_draft" for p in plan):
        warnings.append("Az importált fájlok között AI-vázlat is van: emberi jóváhagyás nélkül nem használható, és "
                        "értékelőként nem számít (κ, konszenzus).")
    log_activity_or_warn(app, "appraisal.import", warnings, inputs=[src] if src else (), outputs=outputs,
                         details={"kind": kind, "n": len(plan), "written": len(outputs), "replace": replace,
                                  "tools": sorted({p["tool"] for p in plan}), "from_inbox": src is not None,
                                  "decisions": n_dec})
    return Result({"schema": IMPORT_SCHEMA, "imported": result, "source": src[0] if src else None}, IMPORT_SCHEMA,
                  warnings=warnings)


def post_export(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    if body_bool(body, "all"):
        rater = C.check_rater(body.get("rater"), required=False)
        tool = C.check_tool(body["tool"]) if body.get("tool") else None
        items, paths = [], []
        for f in C.list_files(app):
            if (rater and f["rater"] != rater) or (tool and f["tool"] != tool):
                continue
            doc, etag = C.load(app, f["path"])
            if doc is not None:
                items.append(doc)
                paths.append((f["path"], etag))
        if not items:
            raise ApiError("NOT_FOUND", "Nincs exportálható értékelés ezzel a szűréssel.")
        bundle = {"schema": C.SCHEMA_BUNDLE, "exported_at": now_iso(), "rater": rater, "tool": tool, "items": items}
        name = "ertekelesek_%s%s.json" % (rater or "mind", "_" + tool if tool else "")
        warnings = []
        log_activity_or_warn(app, "appraisal.export", warnings, inputs=paths, details={"n": len(items), "bundle": True})
        return Result({"schema": EXPORT_SCHEMA, "filename": name, "bundle": bundle, "n": len(items)}, EXPORT_SCHEMA,
                      warnings=warnings)
    unit = C.check_unit(body.get("unit"))
    tool = C.check_tool(body.get("tool"))
    target = C.check_target(body.get("target"))
    rater = C.check_rater(body.get("rater"))
    rel = C.relpath(unit, tool, target, rater)
    doc, etag = _load_checked(app, rel, unit)
    if doc is None:
        raise ApiError("NOT_FOUND", "Nincs ilyen értékelés: %s" % rel)
    warnings = []
    log_activity_or_warn(app, "appraisal.export", warnings, inputs=[(rel, etag)], details={"n": 1, "bundle": False})
    return Result({"schema": EXPORT_SCHEMA, "filename": os.path.basename(rel), "doc": doc, "sha256": etag,
                   "path": rel}, EXPORT_SCHEMA, warnings=warnings)


def register(router):
    router.add("GET", "/api/instruments", get_instruments, schema=LIST_SCHEMA)
    router.add("GET", "/api/instruments/<tool>", get_instrument, schema=C.SCHEMA_INSTRUMENT)
    router.add("GET", "/api/appraisals", list_appraisals, schema=APPRAISALS_SCHEMA)
    router.add("GET", "/api/appraisals/inbox", get_inbox, schema=INBOX_SCHEMA)
    router.add("POST", "/api/appraisals/import", post_import, schema=IMPORT_SCHEMA, request_schema=_IMPORT_REQ)
    router.add("POST", "/api/appraisals/export", post_export, schema=EXPORT_SCHEMA, request_schema=_EXPORT_REQ)
    router.add("GET", "/api/appraisals/<unit>/<tool>", get_appraisal, schema=VIEW_SCHEMA)
    router.add("PUT", "/api/appraisals/<unit>/<tool>", put_appraisal, schema=VIEW_SCHEMA, request_schema=_DOC_REQ)
    router.add("POST", "/api/appraisals/<unit>/<tool>/check", post_check, schema=CHECK_SCHEMA, request_schema=_DOC_REQ)
    router.add("POST", "/api/appraisals/<unit>/<tool>/approve", post_approve, schema=VIEW_SCHEMA,
               request_schema=_APPROVE_REQ)
