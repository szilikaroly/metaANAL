# -*- coding: utf-8 -*-
"""Elemzés a meleg worker-folyamatban (terv 2.2, 2.6, 3.4, 4.4, 4.5): ``POST /api/analyze`` · ``GET /api/jobs/<id>``.

Kérés: ``{mode: explore | commit, spec: szk.ma.analysis-spec/v1, table?: {header, rows, decimal_mark?,
delimiter?, lines?} (csak explore: mentetlen piszkozat), client_seq?, lane?}``. Válasz: a feladat
pillanatképe (``jobs.py``: ``{job_id, kind, mode, client_seq, status, elapsed_ms, run_ms, result?, error?,
superseded_by?}``); ha a feladat rövid időn belül elkészül, már a ``result``-tal együtt, különben a
felület a ``GET /api/jobs/<id>``-t kérdezi le a végállapotig.

- **explore**: ``api.analyze(mode='explore')`` — semmit nem ír, nem naplóz. „Legutolsó nyer”:
  ugyanazon a sávon (``lane`` vagy a spec neve) az újabb ``client_seq`` kiszorítja a régebbit
  (``superseded``); 30 s időkorlát, túllépésnél a worker újraindul.
- **commit** („Rögzítés”): a spec mentett példánya (05_elemzes/specs/<név>.json) kell — ha még nincs,
  most mentjük; ha eltér a kérésben küldöttől, 409 (előbb mentsd). Az adattábla mostani sha256-ja
  egyezzen a spec ``data.sha256``-jával (ha megadta; különben a mostanit rögzítjük) — eltérésnél 409
  CONFLICT (elavult: futtasd újra a feltárást). ``_privat/`` alatti adatból a 05_elemzes/ alá kimenet
  nem készül (403). A workerben ``ma_gui.routes._worker.commit_analysis`` fut: a motor kimenetei + run.json, a
  projektnapló run-sora (actor) és az activity-lánc ``analyze.commit`` sora. Commit sosem szorul ki.

Statisztika itt nincs: a nézetmodell (run, plot, results, findings, excluded) a motoré."""
import threading

from metaelemzes import api

from .. import jobs as jobs_mod
from .. import privacy, security, store
from ..router import ApiError, Result
from . import _contracts, _worker, specs
from ._common import client_seq, dataset_rel, journal_root, log_activity_or_warn, phi_doc_guard

SCHEMA = "szk.ma.job/v1"
ANALYZE_TARGET = "metaelemzes.api:analyze"
INLINE_WAIT = 1.5                   # s; ennyit vár a válasz a kész eredményre, utána lekérdezés
FOLLOW_POLL = 5.0                   # s; a commit-követő ennyi időnként nézi, lezárult-e már a feladat
_TABLE = {
    "type": "object", "required": ["header", "rows"],
    "properties": {
        "header": {"type": "array", "items": {"type": "string", "maxLength": 2000}},
        "rows": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "null"]}}},
        "decimal_mark": {"enum": [",", ".", None]},
        "delimiter": {"type": ["string", "null"], "maxLength": 1},
        "lines": {"type": ["array", "null"], "items": {"type": ["integer", "null"], "minimum": 1}},
    },
    "additionalProperties": False,
}
REQUEST_SCHEMA = {
    "type": "object", "required": ["mode", "spec"],
    "properties": {
        "mode": {"enum": ["explore", "commit"]},
        "spec": _contracts.ref("szk.ma.analysis-spec/v1"),
        "table": {"oneOf": [{"type": "null"}, _TABLE]},
        "client_seq": {"type": "integer", "minimum": 0},
        "lane": {"type": ["string", "null"], "pattern": "^[A-Za-z0-9_.:-]{1,64}$"},
    },
    "additionalProperties": False,
}
_RESULT = {
    "type": "object",
    "properties": {"run": _contracts.ref("szk.ma.run/v1"), "plot": _contracts.ref("szk.ma.plot/v2", nullable=True)},
}
JOB_RESPONSE = {
    "type": "object", "required": ["job_id", "kind", "mode", "client_seq", "status"],
    "properties": {
        "job_id": {"type": "string", "pattern": "^j_[0-9a-f]{16}$"},
        "mode": {"enum": ["explore", "commit"]},
        "status": {"enum": ["queued", "running", "done", "error", "superseded", "timeout"]},
        "client_seq": {"type": "integer", "minimum": 0},
        "result": {"oneOf": [{"type": "null"}, _RESULT]},
    },
}
MSG_STALE = ("Az adattábla (%s) megváltozott, mióta ezt az elemzést előkészítetted (a spec data.sha256-ja nem a "
             "mostani fájlé). Futtasd újra a feltárást (explore) a mostani adatokon, nézd át az eredményt, majd "
             "rögzíts újra.")
MSG_SPEC_DIFFERS = ("A rögzítendő spec eltér a mentett változattól (%s). Előbb mentsd a specet (PUT /api/specs/%s), "
                    "majd rögzíts.")
MSG_PRIVATE = ("A spec a _privat/ alatti adattáblát elemzi (%s): a commit kimenetei (results.json, ábrák, riport) a "
               "05_elemzes/ alá, a privát mappán kívülre kerülnének. A munkapad ezt nem teszi meg; a feltárás "
               "(explore) használható, rögzíteni a parancssorból lehet a _privat/ alá: python ma.py analyze --spec "
               "%s --out _privat/05_elemzes/<mappa> --project .")


def _client_error(err):
    """A feladat hibája a kliensnek: csak kód és magyar üzenet (+ gépi részletek, ha vannak) — traceback, abszolút
    telepítési út és forrássor soha (router-szabály, 3.4; WS-3). A diagnosztika a szervernaplóban: csak a kód."""
    if not isinstance(err, dict):
        return err
    out = {"code": err.get("code") or "INTERNAL", "message": err.get("message") or ""}
    if isinstance(err.get("details"), dict):
        out["details"] = err["details"]
    return out


def _snapshot(app, snap):
    """A feladat-pillanatkép a szerveroldali kiegészítésekkel (a commit-futás kimenete: outcome_id); a hiba
    traceback nélkül."""
    if snap is None:
        return None
    snap = dict(snap)
    if snap.get("error") is not None:
        if isinstance(snap["error"], dict) and snap["error"].get("traceback_tail"):
            app.log("feladat-hiba: %s (%s)" % (snap["error"].get("code") or "?", snap.get("job_id")))
        snap["error"] = _client_error(snap["error"])
    info = app.job_info(snap.get("job_id")) or {}
    res = snap.get("result")
    if isinstance(res, dict) and isinstance(res.get("run"), dict):
        res = dict(res)
        run = dict(res["run"])
        if info.get("outcome") is not None:
            run.setdefault("outcome_id", info.get("outcome"))
        if snap.get("mode") == "commit":
            run.setdefault("stale", False)
        res["run"] = run
        snap["result"] = res
    if info.get("spec"):
        snap["spec"] = info["spec"]
    return snap


def _check_data(req, spec):
    """A spec adattáblája: projekt-relatív, létező, tábla-kiterjesztésű fájl → (rel, mostani sha256)."""
    rel = dataset_rel(req, (spec.get("data") or {}).get("path"))
    try:
        sha = store.sha256_file(req.app.store.path(rel))
    except PermissionError:
        raise store.Locked(rel) from None
    if sha is None:
        raise ApiError("NOT_FOUND", "A spec adattáblája nem található: %s" % rel, {"dataset": rel})
    return rel, sha


def _explore(req, body, spec, seq):
    app = req.app
    table = body.get("table")
    if table is None:
        _check_data(req, spec)
    else:
        security.check_table_limits(table["header"], table["rows"])
        dataset_rel(req, (spec.get("data") or {}).get("path"))
    lane = body.get("lane") or spec.get("name") or "spec"
    kwargs = {"table": table, "mode": "explore", "project_root": str(app.project_root), "client_seq": seq}
    job_id = _submit(app, "explore:%s" % lane, ANALYZE_TARGET, [spec], kwargs, seq, "explore")
    app.remember_job(job_id, {"mode": "explore", "outcome": spec.get("outcome"), "spec": spec.get("name")})
    return job_id


def _submit(app, kind, target, args, kwargs, seq, mode):
    try:
        return app.get_jobs().submit(kind, target, args=args, kwargs=kwargs, client_seq=seq, mode=mode)
    except jobs_mod.JobQueueFull:
        raise ApiError("CONFLICT", "Túl sok elemzés vár a sorára; várd meg, amíg a futók elkészülnek, majd próbáld "
                                   "újra.", {"reason": "queue_full"}) from None
    except RuntimeError:
        raise ApiError("INTERNAL", "A feladatkezelő leállt; indítsd újra a munkapadot.") from None


def _ensure_saved_spec(req, spec, warnings):
    """A commit spec-fájlja: a mentett változat (azonos tartalommal), vagy most mentjük → projekt-relatív út."""
    app = req.app
    name = spec["name"]
    rel = specs.spec_rel(name)
    cur, _etag = specs.load(app, name)
    if cur is not None:
        if not specs.same_content(cur, spec):
            raise ApiError("CONFLICT", MSG_SPEC_DIFFERS % (rel, name), {"reason": "spec_differs", "path": rel})
        return rel
    phi_doc_guard(app, spec, rel, "elemzési spec")
    pinned = specs._without_pin(spec)
    sha = app.store.write_with(rel, lambda path: api.save_spec(path, pinned), if_match=None)
    log_activity_or_warn(app, "spec.save", warnings, outputs=[(rel, sha)],
                         details={"name": name, "outcome": spec.get("outcome"), "purpose": spec.get("purpose"),
                                  "parent": spec.get("parent"), "prespecified": spec.get("prespecified"),
                                  "on_commit": True})
    return rel


def _commit(req, body, spec, seq):
    app = req.app
    if body.get("table") is not None:
        raise ApiError("BAD_REQUEST", "Commit-futás csak mentett adattáblából indítható (a table mező explore-hoz való).")
    root = journal_root(app)
    data_rel, current = _check_data(req, spec)
    if (privacy.is_private_path(data_rel, app.privacy_kw.get("platform"))
            or privacy.is_private_location(app.project_root, data_rel)):
        raise ApiError("FORBIDDEN", MSG_PRIVATE % (data_rel, specs.spec_rel(spec["name"])), {"dataset": data_rel})
    pinned = (spec.get("data") or {}).get("sha256")
    if pinned and pinned != current:
        raise ApiError("CONFLICT", MSG_STALE % data_rel, {"reason": "stale_data", "dataset": data_rel,
                                                          "expected_sha256": pinned, "current_sha256": current})
    ok, reason = app.can_write_meta("05_elemzes/run.json")      # a kimenetek helye (írás-tartás, 7.4)
    if not ok:
        raise ApiError("FORBIDDEN", reason, {"path": "05_elemzes/"})
    warnings = []
    spec_rel = _ensure_saved_spec(req, spec, warnings)
    spec = dict(spec)
    spec["data"] = dict(spec.get("data") or {}, sha256=current)
    token = app.watcher.own_prefix("05_elemzes/")
    try:
        job_id = _submit(app, "commit", _worker.COMMIT_TARGET, [root, spec, str(app.store.path(spec_rel))],
                         {"client_seq": seq, "actor": app.actor}, seq, "commit")
    except BaseException:
        app.watcher.release_prefix(token, grace=0.0)
        raise
    app.remember_job(job_id, {"mode": "commit", "outcome": spec.get("outcome"), "spec": spec.get("name")})
    _follow(app, job_id, token)
    return job_id, warnings


def _follow(app, job_id, token):
    """A commit végén a 05_elemzes/ saját-írás jelölése lejár (türelmi idővel), és a változásfigyelő
    azonnal szkennel, hogy a felület a long-pollon lássa az új futást. A követő addig vár, amíg a feladat
    TÉNYLEG lezárul — a sorban várakozás idejére nincs határidő (a futás időkorlátját a feladatkezelő
    érvényesíti); különben egy lassú explore-ok mögött várakozó commit saját run.json-ja „külső
    szerkesztésként” kerülne a hash-láncba (WS-5)."""
    jobs = app.get_jobs()

    def run():
        try:
            while True:
                snap = jobs.wait(job_id, timeout=FOLLOW_POLL)
                if snap is None or snap.get("status") in jobs_mod.TERMINAL:
                    break
                if (jobs.info() or {}).get("closed"):
                    jobs.wait(job_id, timeout=FOLLOW_POLL)     # a leállás a futót is lezárja
                    break
        finally:
            try:
                app.watcher.scan()
            except Exception:                               # noqa: BLE001
                pass
            app.watcher.release_prefix(token)

    threading.Thread(target=run, name="ma-gui-commit-follow", daemon=True).start()


def post_analyze(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    spec = body["spec"]
    seq = client_seq(req, body)
    specs.check_spec(req, spec)
    warnings = []
    if body["mode"] == "explore":
        job_id = _explore(req, body, spec, seq)
    else:
        job_id, warnings = _commit(req, body, spec, seq)
    snap = app.get_jobs().wait(job_id, timeout=INLINE_WAIT)
    snap = _snapshot(app, snap)
    if snap is None:
        raise ApiError("INTERNAL", "A feladat eltűnt a feladatkezelőből.")
    return Result(snap, SCHEMA, warnings=warnings)


def get_job(req):
    app = req.app
    app.require_open()
    job_id = req.params["job_id"]
    if len(job_id) > 40 or not job_id.startswith("j_"):
        raise ApiError("NOT_FOUND", "Nincs ilyen feladat.")
    wait = req.arg("wait", max_len=5)
    jobs = app.jobs_if_started()
    if jobs is None:
        raise ApiError("NOT_FOUND", "Nincs ilyen feladat (ebben a munkamenetben még nem indult elemzés).")
    if wait and wait.isdigit():
        snap = jobs.wait(job_id, timeout=min(float(wait), 10.0))
    else:
        snap = jobs.get(job_id)
    if snap is None:
        raise ApiError("NOT_FOUND", "Nincs ilyen feladat (lejárt vagy a szerver újraindult).")
    return Result(_snapshot(app, snap), SCHEMA)


def register(router):
    router.add("POST", "/api/analyze", post_analyze, schema=SCHEMA, request_schema=REQUEST_SCHEMA,
               response_schema=JOB_RESPONSE)
    router.add("GET", "/api/jobs/<job_id>", get_job, schema=SCHEMA, response_schema=JOB_RESPONSE)

