# -*- coding: utf-8 -*-
"""PRISMA 2020 és a vizsgálat ↔ jelentés térkép (terv 2.4, 3.4, 3.5.14, 4.10, 4.13, 6.2).

- ``GET /api/prisma[?refresh=1]`` → ``{mode, path, source, override, flow, check, studies, meta, cross,
  composer_status}`` + ETag. A ``flow`` a ``02_szures/prisma_flow.json`` (``szk.prisma-flow/v1``); a
  ``check`` a motor P-ellenőrzése (``api.prisma_check`` a studies.json-nal: I, J, P017); a ``meta``
  kimenetenként a legutóbbi elsődleges commit-futás k-ja; a ``cross`` a motor projekt-auditjának
  PRISMA-hoz kötött X-találatai (X014, X015, X020, X021). Composer-módot (a composer exportjának
  másolata) a fájl ``composer_version``/``source.kind`` mezője jelez; akkor a felület csak olvas.
- ``PUT /api/prisma/manual`` ← ``{flow, dry_run?, override_reason?, client_seq?}``. ``dry_run``: csak a
  motor ellenőrzése (élő előnézet), nem ír. Mentés (If-Match): a dobozértékek NYERS szövegként jönnek,
  a motor parszol; értelmezhetetlen doboz (P001) → 422. A fájlba a motor által értelmezett egész számok
  kerülnek (``check.counts``), a kizárási okok a motor ``reasons``-éből. Composer-módban a kézi felülírás
  csak indoklással (``override_reason`` → döntés a projektnaplóba). PHI-szkenner az okokon is.
- ``GET/PUT /api/studies`` ↔ ``03_adatok/studies.json`` (``szk.ma.studies/v1``, If-Match) +
  ``summary {studies: I, reports: J}`` (a motor számolja) és ``problems``.

Statisztikát a szerver nem számol; a dobozok levezetett értékei a motor ``check.derived``-jéből jönnek."""
from metaelemzes import api

from .. import store
from ..router import ApiError, Result
from . import _contracts, runs
from ._common import (body_bool, body_str, has_journal, if_match, log_activity_or_warn, now_iso,
                      phi_doc_guard, phi_text_guard)

SCHEMA = "szk.ma.prisma/v1"
FLOW_SCHEMA = "szk.prisma-flow/v1"
STUDIES_SCHEMA = "szk.ma.studies/v1"
PRISMA_REL = "02_szures/prisma_flow.json"
STUDIES_REL = store.STUDIES_REL
CROSS_CODES = ("X014", "X015", "X020", "X021")
REASONS_KEY = "excluded_eligibility_reasons"
# a motor kanonikus doboznevei (check.counts) → a szk.prisma-flow/v1 (composer flow-json) kulcsai
COUNT_TO_FLOW = (
    ("identified_databases", "identified_databases"), ("identified_registers", "identified_registers"),
    ("duplicates_removed", "dedup_removed"), ("automation_removed", "automation_removed"),
    ("other_removed", "removed_before_screening_n"), ("screened", "screened"),
    ("excluded_screening", "excluded_screening"), ("sought", "sought_for_retrieval"),
    ("not_retrieved", "not_retrieved"), ("assessed", "assessed_eligibility"),
    ("excluded_eligibility", "excluded_eligibility"), ("included_reports", "included_reports"),
    ("included_studies", "included_studies"),
)
FLOW_KEYS = tuple(f for _c, f in COUNT_TO_FLOW) + (REASONS_KEY,)
_META_KEYS = ("schema", "source", "override", "generated", "project", "composer_version")
_BOX = {"type": ["string", "integer", "null"], "maxLength": 40}
FLOW_REQUEST = {
    "type": "object",
    "properties": dict({k: _BOX for k in FLOW_KEYS if k != REASONS_KEY},
                       schema={"const": FLOW_SCHEMA}, included=_BOX,
                       excluded_eligibility_reasons={"type": ["object", "null"],
                                                     "additionalProperties": _BOX}),
    "additionalProperties": False,
}
PUT_SCHEMA = {
    "type": "object", "required": ["flow"],
    "properties": {"flow": FLOW_REQUEST, "dry_run": {"type": "boolean"},
                   "override_reason": {"type": ["string", "null"], "maxLength": 4000},
                   "client_seq": {"type": "integer", "minimum": 0}},
    "additionalProperties": False,
}
MAX_REASONS = 100


# ---------------------------------------------------------------------------- olvasás
def _load_flow(app):
    doc, etag = app.store.load_json(PRISMA_REL)
    if doc is not None and not isinstance(doc, dict):
        raise store.Invalid("A %s gyökere objektum legyen." % PRISMA_REL, {"path": PRISMA_REL})
    return doc, etag


def _mode(doc):
    if not isinstance(doc, dict):
        return "manual"
    src = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    if src.get("kind") == "composer" or (doc.get("composer_version") and src.get("kind") != "manual"):
        return "composer"
    return "manual"


def _source(doc, mode):
    src = dict(doc.get("source") or {}) if isinstance(doc, dict) and isinstance(doc.get("source"), dict) else {}
    src.setdefault("kind", mode)
    if mode == "composer" and isinstance(doc, dict):
        for k in ("composer_version", "project", "generated"):
            if doc.get(k) is not None:
                src.setdefault(k, doc[k])
    return src


def _flow_view(doc):
    """A felületnek: a flow (a tárolt dokumentum a munkapad-metaadat nélkül)."""
    if not isinstance(doc, dict):
        return {"schema": FLOW_SCHEMA}
    out = {k: v for k, v in doc.items() if k not in ("source", "override")}
    out.setdefault("schema", FLOW_SCHEMA)
    return out


def _studies(app):
    """(studies.json vagy None, {path, studies, reports} a motor számolásával)."""
    try:
        doc, _etag = app.store.load_json(STUDIES_REL)
    except store.StoreError:
        doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get("studies"), list) or store.studies_problems(doc):
        return None, {"path": STUDIES_REL}                  # ismeretlen I és J: a felület „—”-t mutat
    counts = api.prisma_check({}, studies=doc).get("counts") or {}
    return doc, _counts(counts, {"path": STUDIES_REL})


def _counts(counts, out):
    """I (studies) és J (reports) a motor számolásából — csak ami ismert (a hiányzót a felület „—”-ként mutatja)."""
    for key, canon in (("studies", "included_studies"), ("reports", "included_reports")):
        if counts.get(canon) is not None:
            out[key] = counts[canon]
    return out


def _parsed_flow(check):
    """A motor által értelmezett dobozértékek (check.counts, check.reasons) szk.prisma-flow/v1 kulcsokkal."""
    counts = check.get("counts") or {}
    out = {"schema": FLOW_SCHEMA}
    for canon, key in COUNT_TO_FLOW:
        out[key] = counts.get(canon)
    reasons = (check.get("reasons") or {}).get(REASONS_KEY)
    out[REASONS_KEY] = dict(reasons) if isinstance(reasons, dict) and reasons else None
    return out


def _check(flow, studies_doc, warnings):
    """A motor P-ellenőrzése. Ha van érvényes studies.json, a motor a dobozokat azzal is összeveti (I, J,
    P017; a nyers szöveges dobozokat ugyanúgy értelmezi, mint a P001-nél); értelmezhetetlen doboznál (P001) az
    összevetés értelmetlen, ilyenkor a sima ellenőrzés marad."""
    plain = api.prisma_check(flow)
    if studies_doc is None or any(f.get("code") == "P001" for f in plain.get("findings") or ()):
        return plain
    try:
        return api.prisma_check(flow, studies=studies_doc)
    except ValueError:
        warnings.append("A PRISMA-ellenőrzés a studies.json nélkül futott (a motor nem tudta összevetni).")
        return plain


def _meta(app):
    names = {}
    meta, _ = app.project_meta_safe()
    for oc in (meta or {}).get("outcomes") or []:
        if isinstance(oc, dict) and oc.get("id"):
            names[oc["id"]] = oc.get("name")
    out = []
    for r in runs.list_runs(app, primary=True):
        out.append({"outcome_id": r.get("outcome_id"), "name": names.get(r.get("outcome_id")), "k": r.get("k"),
                    "run_id": r.get("run_id"), "stale": r.get("stale")})
    return out


def _cross(app, warnings):
    try:
        audit = api.project_audit(str(app.project_root))
    except Exception:                                       # noqa: BLE001 — az audit hibája ne vigye el a nézetet
        warnings.append("A projekt-audit (X014/X015) most nem futott le.")
        return []
    return [{k: f.get(k) for k in ("code", "severity", "title", "detail", "outcome", "kb_refs")}
            for f in audit.get("findings") or [] if f.get("code") in CROSS_CODES]


def _view(app, doc, flow, check, warnings, cross=True):
    mode = _mode(doc)
    _sdoc, summary = _studies(app)
    return {"schema": SCHEMA, "mode": mode, "path": PRISMA_REL, "source": _source(doc, mode),
            "override": (doc or {}).get("override") if isinstance(doc, dict) else None,
            "flow": flow, "check": check, "studies": summary, "meta": _meta(app),
            "cross": _cross(app, warnings) if cross else [], "composer_status": []}


def get_prisma(req):
    app = req.app
    app.require_open()
    doc, etag = _load_flow(app)
    warnings = []
    sdoc, _summary = _studies(app)
    flow = _flow_view(doc)
    check = _check(flow if doc is not None else {}, sdoc, warnings)
    return Result(_view(app, doc, flow, check, warnings), SCHEMA, warnings=warnings, etag=etag)


# ---------------------------------------------------------------------------- mentés
def _reasons_guard(app, flow):
    reasons = flow.get(REASONS_KEY)
    if isinstance(reasons, dict) and len(reasons) > MAX_REASONS:
        raise ApiError("BAD_REQUEST", "Túl sok kizárási ok (legfeljebb %d)." % MAX_REASONS)
    phi_doc_guard(app, flow, PRISMA_REL, "PRISMA-folyamat")      # a kizárási okok (kulcsok) is


def _stored_doc(app, check, override):
    doc = _parsed_flow(check)
    doc["generated"] = now_iso()
    doc["source"] = {"kind": "manual", "updated": doc["generated"], "actor": app.actor}
    if override:
        doc["override"] = override
    return doc


def put_manual(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    flow = dict(body["flow"])
    flow.setdefault("schema", FLOW_SCHEMA)
    warnings = []
    sdoc, _summary = _studies(app)
    if body_bool(body, "dry_run"):
        check = _check(flow, sdoc, warnings)
        cur, _etag = _load_flow(app)
        view = _view(app, cur, flow, check, warnings, cross=False)
        view["saved"] = False
        return Result(view, SCHEMA, warnings=warnings)
    _reasons_guard(app, flow)
    cur, _etag = _load_flow(app)
    reason = body_str(body, "override_reason", max_len=4000)
    if _mode(cur) == "composer" and reason is None:
        raise ApiError("BAD_REQUEST", "A PRISMA-számok most a composer exportjából jönnek (composer-mód). Kézi "
                                      "felülírás csak indoklással lehetséges (override_reason) — az indoklás a "
                                      "projektnaplóba kerül.", {"needs_override": True})
    plain = api.prisma_check(flow)                       # a tárolt számok: a studies.json nélküli értelmezés
    bad = [f for f in plain.get("findings") or [] if f.get("code") == "P001"]
    if bad:
        raise ApiError("VALIDATION", "Van olyan PRISMA-doboz, amelynek az értéke nem értelmezhető számként (P001); "
                                     "javítsd, majd mentsd újra.", {"findings": bad})
    override = None
    if reason is not None:
        # az indoklás a PRISMA-fájlba (projekt-metaadat) is bekerül: ugyanaz a kemény PHI-őr, mint a fájl többi
        # szövegére (TAJ-gyanú → 403, osztálytól függetlenül; PRIV-5); a naplóba írás előtt a szöveg-őr is fut
        phi_doc_guard(app, {"override_reason": reason}, PRISMA_REL, "PRISMA-folyamat")
        warnings += phi_text_guard(app, [("override_reason", reason)])
        decision_id = None
        if has_journal(app):
            res = api.project_log(str(app.project_root), "user", "PRISMA-számok kézi felülírása (composer-mód helyett)",
                                  rationale=reason, stage="S04", kb_db=app.kb_db, actor=app.actor)
            decision_id = res["id"]
            warnings += list(res.get("warnings") or [])
        else:
            warnings.append("Nincs projektnapló (projekt.sqlite): a felülírás indoklása csak a PRISMA-fájlba került.")
        override = {"reason": reason, "decision_id": decision_id}
    doc = _stored_doc(app, plain, override)
    sha = app.store.write_bytes(PRISMA_REL, store.json_bytes(doc), if_match=if_match(req))
    log_activity_or_warn(app, "prisma.save", warnings, outputs=[(PRISMA_REL, sha)],
                         details={"mode": "manual", "override": override is not None,
                                  "decision_id": (override or {}).get("decision_id")})
    saved, etag = _load_flow(app)
    flow_view = _flow_view(saved)
    view = _view(app, saved, flow_view, _check(flow_view, sdoc, warnings), warnings)
    view["saved"] = True
    return Result(view, SCHEMA, warnings=warnings, etag=etag)


# ---------------------------------------------------------------------------- studies.json
def _studies_view(app, doc):
    problems = store.studies_problems(doc)
    counts = {}
    if not problems:
        counts = api.prisma_check({}, studies=doc).get("counts") or {}
    out = {"schema": STUDIES_SCHEMA, "path": STUDIES_REL, "studies": list(doc.get("studies") or [])}
    out["summary"] = _counts(counts, {})
    out["problems"] = problems
    return out


def get_studies(req):
    app = req.app
    app.require_open()
    doc, etag = app.store.load_json(STUDIES_REL, default=store.empty_studies)
    if not isinstance(doc, dict):
        raise store.Invalid("A %s gyökere objektum legyen." % STUDIES_REL, {"path": STUDIES_REL})
    return Result(_studies_view(app, doc), STUDIES_SCHEMA, etag=etag)


def put_studies(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    doc = {"schema": STUDIES_SCHEMA, "studies": body.get("studies")}
    problems = store.studies_problems(doc)
    if problems:
        raise ApiError("VALIDATION", "A vizsgálat ↔ jelentés térkép hibás: %s" % "; ".join(problems[:10]),
                       {"problems": problems})
    phi_doc_guard(app, doc, STUDIES_REL, "vizsgálat ↔ jelentés térkép")
    sha = app.store.save_studies(doc, if_match(req))
    warnings = []
    log_activity_or_warn(app, "studies.save", warnings, outputs=[(STUDIES_REL, sha)],
                         details={"n_studies": len(doc["studies"])})
    saved, etag = app.store.load_json(STUDIES_REL, default=store.empty_studies)
    return Result(_studies_view(app, saved), STUDIES_SCHEMA, warnings=warnings, etag=etag)


def register(router):
    router.add("GET", "/api/prisma", get_prisma, schema=SCHEMA)
    router.add("PUT", "/api/prisma/manual", put_manual, schema=SCHEMA, request_schema=PUT_SCHEMA)
    router.add("GET", "/api/studies", get_studies, schema=STUDIES_SCHEMA,
               response_schema=_contracts.ref(STUDIES_SCHEMA))
    router.add("PUT", "/api/studies", put_studies, schema=STUDIES_SCHEMA,
               request_schema=_contracts.ref(STUDIES_SCHEMA), response_schema=_contracts.ref(STUDIES_SCHEMA))

