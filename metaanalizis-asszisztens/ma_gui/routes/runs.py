# -*- coding: utf-8 -*-
"""Commit-futások (terv 2.4, 2.6, 3.4, 3.5.7, 4.5, 4.6): a motor run.json-jai (szk.ma.run/v1) olvasva.

- ``GET /api/runs[?outcome=<id>][&primary=1][&private=0]`` → ``{runs: [...]}``, legújabb elöl. A futás-leíró
  szerveroldali mezőkkel bővül: ``outcome_id`` (a spec kimenete), ``stale`` (X001: az adattábla mostani
  sha256-ja ≠ a futásé — mint a motor auditja: abszolút vagy projekt-relatív út, hiányzó fájl = elavult;
  ``null``, ha a fájl most nem olvasható vagy a futásnak nincs adat-hash-e: ismeretlen), ``measure`` (a FUTÁS
  saját hatásmérete: run.json ``measure``, régi futásnál a plot_data.json / results.json — soha nem a most
  mentett spec), ``spec.purpose``, ``dir``, és a motor I²/PI-szövegei (``primary.i2_text``, ``primary.pi_text``
  a futás plot_data.json-jából). ``primary=1``: kimenetenként a legutóbbi elsődleges (purpose: primary) futás,
  a projektnapló legutóbbi GRADE-ítéletével (``grade: {certainty, id}``). ``private=0``: a _privat/ alatti
  futások (mappa, adatfájl vagy spec a _privat/ alatt, a linkek feloldása után is) kimaradnak — a pillanatkép
  így rögzít (7.4).
- ``GET /api/runs/<run_id>`` → a leíró + ``downloads`` (aláírt, 10 perces fájl-URL-ek a kimenetekhez:
  SVG, report.md, CSV, JSON — új lapon nyithatók token nélkül, 7.1 T5).
- ``GET /api/runs/<run_id>/results`` → results.json · ``/plot`` → plot_data.json (szk.ma.plot/v2) ·
  ``/report`` → ``{path, markdown, url}``.

Futás-forrás: 05_elemzes/<kimenet>/<run_id>/run.json, 05_elemzes/<kimenet>/run.json és a projektnapló
run-táblájának (projekt.sqlite) kimeneti mappái — csak a projekten belül, csak ``mode: commit``. Számot
a szerver nem számol: a k, a display_text és a többi szöveg a motor futás-leírójából jön."""
import json
import os
import re
import threading
from pathlib import Path

from metaelemzes import api

from .. import privacy, security, store
from ..router import ApiError, Result
from . import _contracts
from ._common import has_journal, int_arg

SCHEMA = "szk.ma.runs/v1"
RUN_SCHEMA = "szk.ma.run/v1"
PLOT_SCHEMA = "szk.ma.plot/v2"
RESULTS_SCHEMA = "szk.ma.results/v1"
REPORT_SCHEMA = "szk.ma.report/v1"
ANALYSIS_DIR = "05_elemzes"
SPEC_DIR = ANALYSIS_DIR + "/specs"
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
ARTIFACT_PREFIX = "_run:"
DOWNLOAD_EXT = (".svg", ".md", ".csv", ".json", ".png", ".pdf", ".tif", ".tiff", ".txt")
MAX_RUNS = 2000
MAX_JSON_BYTES = 64 * 1024 * 1024
RUNS_RESPONSE = {
    "type": "object", "required": ["runs"],
    "properties": {"runs": {"type": "array", "items": _contracts.ref(RUN_SCHEMA)}},
}
MSG_NO_RUN = "Nincs ilyen commit-futás a projektben (05_elemzes/…/run.json)."


# ---------------------------------------------------------------------------- futás-index
def _read_json(path):
    try:
        if os.path.getsize(path) > MAX_JSON_BYTES:
            return None
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _candidates(app):
    """A run.json-jelöltek a motor kanonikus felderítésével (api.project_run_files = spec.project_run_files): a
    project audit (X001 …), a pillanatkép és az audit-export ugyanezeket a futásokat látja. Sorrend: kimenetenként
    a 'flat' (05_elemzes/<kimenet>/run.json), majd a 'nested' (…/<run_id>/run.json) futások, végül a projektnapló
    további futás-mappái; olvashatatlan/zárolt naplónál csak a mappák."""
    try:
        found = api.project_run_files(str(app.project_root))
    except OSError:
        return []
    return [Path(path) for path, _layout, _folder, _logged in found]


def run_index(app):
    """{run_id: (projekt-relatív mappa, run.json-dokumentum)} — csak a projekten belüli commit-futások."""
    root = app.project_root
    found = {}
    for path in _candidates(app)[:MAX_RUNS * 2]:
        try:
            if not path.is_file() or not store.is_within(path, root):
                continue
            rel_dir = Path(os.path.relpath(os.path.realpath(str(path.parent)), str(root))).as_posix()
        except (OSError, ValueError):
            continue
        doc = _read_json(str(path))
        if doc is None or doc.get("mode") != "commit":
            continue
        rid = doc.get("run_id")
        if not isinstance(rid, str) or not RUN_ID_RE.match(rid) or rid in found:
            continue
        found[rid] = (rel_dir, doc)
        if len(found) >= MAX_RUNS:
            break
    return found


def run_is_private(app, rel_dir, run):
    """A futás a _privat/ alól jön-e: a mappája, az adatfájlja vagy a specje a _privat/ alatt van (az írt út és a
    szimbolikus linkek feloldása után is). Ilyen futás a pillanatképbe és az audit-csomagba soha nem kerül (7.4)."""
    root = app.project_root
    if privacy.is_private_location(root, rel_dir):
        return True
    data = run.get("data") if isinstance(run.get("data"), dict) else {}
    sp = run.get("spec") if isinstance(run.get("spec"), dict) else {}
    return any(privacy.is_private_location(root, p) for p in (data.get("path"), sp.get("path")))


def _spec_of(app, run):
    sp = run.get("spec") or {}
    p = sp.get("path")
    if isinstance(p, str) and p.startswith(SPEC_DIR + "/") and p.endswith(".json"):
        try:
            doc, _etag = app.store.load_json(p)
        except store.StoreError:
            return None
        return doc if isinstance(doc, dict) else None
    return None


_MISSING = "missing"
_UNREADABLE = "unreadable"


def _data_sha(app, rel, cache):
    """Az adatfájl mostani sha256-ja, a motor auditjával (X001) azonos feloldással: abszolút út úgy, ahogy van,
    projekt-relatív a projekt alatt. Hiányzó fájl → _MISSING; olvashatatlan (zárolt, jogosultság) → _UNREADABLE."""
    if rel in cache:
        return cache[rel]
    sha = _UNREADABLE
    if isinstance(rel, str) and rel:
        try:
            full = Path(rel) if os.path.isabs(rel) else app.store.path(rel)
            sha = store.sha256_file(full) if full.is_file() else (_MISSING if not full.exists() else _UNREADABLE)
            if sha is None:                                  # a fájl közben eltűnt
                sha = _MISSING
        except store.StoreError:
            sha = _UNREADABLE
        except OSError:
            sha = _UNREADABLE
    cache[rel] = sha
    return sha


# a futás saját artefaktumaiból (plot_data.json, results.json) kiolvasott kész szövegek és a hatásméret;
# kulcs: (út, mtime_ns, méret) → a futás-lista nem olvassa újra minden kérésnél a nagy JSON-okat
_ARTIFACT_CACHE = {}
_ARTIFACT_LOCK = threading.Lock()
_ARTIFACT_CACHE_MAX = 512


def _artifact_facts(app, run, rel_dir):
    """{measure, i2_text, pi_text} a futás plot_data.json-jából (ennek hiányában a measure a results.json-ból)."""
    facts = {}
    for key, default, pick in (("plot", "plot_data.json", _plot_facts), ("results", "results.json", _results_facts)):
        rel = _file_rel(run, rel_dir, key, default)
        try:
            path = app.store.path(rel)
            st = path.stat()
        except (OSError, store.StoreError):
            continue
        ck = (str(path), st.st_mtime_ns, st.st_size)
        with _ARTIFACT_LOCK:
            got = _ARTIFACT_CACHE.get(ck)
        if got is None:
            doc = _read_json(str(path))
            got = pick(doc) if isinstance(doc, dict) else {}
            with _ARTIFACT_LOCK:
                if len(_ARTIFACT_CACHE) >= _ARTIFACT_CACHE_MAX:
                    _ARTIFACT_CACHE.clear()
                _ARTIFACT_CACHE[ck] = got
        for k, v in got.items():
            facts.setdefault(k, v)
        if facts.get("measure") is not None:
            break
    return facts


def _plot_facts(doc):
    out = {}
    if isinstance(doc.get("measure"), str):
        out["measure"] = doc["measure"]
    het = doc.get("heterogeneity") if isinstance(doc.get("heterogeneity"), dict) else {}
    if het.get("i2_text") is not None:
        out["i2_text"] = het["i2_text"]
    summ = [x for x in doc.get("summaries") or [] if isinstance(x, dict) and x.get("primary")]
    if summ and summ[0].get("pi_text") is not None:
        out["pi_text"] = summ[0]["pi_text"]
    return out


def _results_facts(doc):
    es = doc.get("effect_sizes") if isinstance(doc.get("effect_sizes"), dict) else {}
    return {"measure": es["measure"]} if isinstance(es.get("measure"), str) else {}


def decorate(app, rel_dir, run, cache=None):
    """A futás-leíró a szerveroldali mezőkkel (outcome_id, stale, measure, spec.purpose, dir, primary.i2_text /
    pi_text). A futás idejére vonatkozó tényt (hatásméret, cél) a futás saját fájljaiból veszünk, nem a
    később módosítható specfájlból; a cél csak a régi (purpose nélküli) run.json-oknál jön a specből."""
    cache = {} if cache is None else cache
    out = dict(run)
    spec = _spec_of(app, run)
    parts = rel_dir.split("/")
    outcome = spec.get("outcome") if spec else None
    if outcome is None and len(parts) >= 2 and parts[0] == ANALYSIS_DIR:
        outcome = parts[1]
    sp = dict(run.get("spec") or {})
    if spec is not None and sp.get("purpose") is None:
        sp["purpose"] = spec.get("purpose")
    out["spec"] = sp
    out["outcome_id"] = outcome
    facts = _artifact_facts(app, run, rel_dir)
    out["measure"] = run.get("measure") if isinstance(run.get("measure"), str) else facts.get("measure")
    if isinstance(run.get("primary"), dict):
        # a motor kész I²- és PI-szövege (az áttekintő és a terv futás-táblájának oszlopai); számot itt nem formázunk
        prim = dict(run["primary"])
        for k in ("i2_text", "pi_text"):
            if facts.get(k) is not None:
                prim.setdefault(k, facts[k])
        out["primary"] = prim
    data = run.get("data") or {}
    current = _data_sha(app, data.get("path"), cache) if data.get("sha256") else _UNREADABLE
    if not data.get("sha256") or current == _UNREADABLE:
        out["stale"] = None                                  # ismeretlen (a motor auditja sem ellenőrizheti)
        out["data_current_sha256"] = None
    elif current == _MISSING:
        out["stale"] = True                                  # mint az X001: az adatfájl már nem létezik
        out["data_current_sha256"] = None
    else:
        out["stale"] = current != str(data.get("sha256")).lower()
        out["data_current_sha256"] = current
    out["dir"] = rel_dir
    return out


def _latest_grades(app):
    """{kimenet: {certainty, id}} — a projektnapló kimenetenként legutóbbi GRADE-ítélete (api.project_list
    'grades'); az áttekintő GRADE-oszlopa. Napló nélkül vagy zárolt naplónál üres."""
    if not has_journal(app):
        return {}
    try:
        rows = api.project_list(str(app.project_root), "grades")
    except Exception:                                       # noqa: BLE001 — zárolt napló: nincs GRADE-oszlop
        return {}
    out = {}
    for r in rows or ():
        if isinstance(r, dict) and isinstance(r.get("outcome"), str) and r.get("certainty"):
            out[r["outcome"]] = {"certainty": r["certainty"], "id": r.get("id")}     # id-sorrend: az utolsó nyer
    return out


def list_runs(app, outcome=None, primary=False, include_private=True):
    cache = {}
    runs = [decorate(app, d, r, cache) for d, r in run_index(app).values()
            if include_private or not run_is_private(app, d, r)]
    if outcome is not None:
        runs = [r for r in runs if r.get("outcome_id") == outcome]
    runs.sort(key=lambda r: (str(r.get("started") or r.get("finished") or ""), r.get("run_id")), reverse=True)
    if primary:
        seen, out = set(), []
        grades = _latest_grades(app)
        for r in runs:
            purpose = (r.get("spec") or {}).get("purpose")
            is_primary = purpose == "primary" or (purpose is None and not (r.get("spec") or {}).get("parent"))
            if is_primary and r.get("outcome_id") not in seen:
                seen.add(r.get("outcome_id"))
                if r.get("outcome_id") in grades:
                    r = dict(r, grade=grades[r["outcome_id"]])
                out.append(r)
        runs = out
    return runs


def find_run(app, run_id):
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise ApiError("BAD_REQUEST", "Érvénytelen futás-azonosító (alak: 20261004T211200Z-a1f3c2).")
    hit = run_index(app).get(run_id)
    if hit is None:
        raise ApiError("NOT_FOUND", MSG_NO_RUN)
    return hit


def _file_rel(run, rel_dir, key, default_name):
    f = (run.get("files") or {}).get(key)
    rel = f.get("path") if isinstance(f, dict) else None
    if not isinstance(rel, str) or not rel.startswith(rel_dir + "/"):
        rel = rel_dir + "/" + default_name
    return rel


def downloads(app, run, rel_dir, ttl=None):
    """{szerep: {path, url, expires_at}} — aláírt fájl-URL a futás letölthető kimeneteihez."""
    ttl = ttl or security.FILE_URL_TTL
    out = {}
    for key, f in sorted((run.get("files") or {}).items()):
        rel = f.get("path") if isinstance(f, dict) else None
        if not isinstance(rel, str) or not rel.startswith(rel_dir + "/") or not rel.lower().endswith(DOWNLOAD_EXT):
            continue
        if not rel.startswith(("05_elemzes/", "06_kezirat/")):
            continue
        try:
            security.check_relpath(rel)
        except security.UnsafePath:
            continue
        if not security.check_extension(rel, security.RUN_ARTIFACT_EXTENSIONS):
            continue
        url = app.security.sign_file_url(ARTIFACT_PREFIX + rel, rel, ttl=ttl)
        parsed = app.security.parse_file_url(url)
        out[key] = {"path": rel, "url": url, "expires_at": parsed[1] if parsed else None}
    return out


# ---------------------------------------------------------------------------- végpontok
def get_runs(req):
    app = req.app
    app.require_open()
    outcome = req.arg("outcome", max_len=100) or None
    primary = (req.arg("primary", max_len=5) or "") in ("1", "true", "igen")
    include_private = (req.arg("private", max_len=5) or "1") not in ("0", "false", "nem")
    limit = int_arg(req, "limit", 500, 1, MAX_RUNS)
    return Result({"runs": list_runs(app, outcome, primary, include_private)[:limit]}, SCHEMA)


def get_run(req):
    app = req.app
    app.require_open()
    rel_dir, run = find_run(app, req.params["run_id"])
    out = decorate(app, rel_dir, run)
    out["downloads"] = downloads(app, run, rel_dir)
    return Result(out, RUN_SCHEMA)


def _load_artifact(app, rel):
    try:
        path = app.store.path(rel)
    except store.StoreError:
        raise ApiError("NOT_FOUND", "A futás kimenete nem található.") from None
    doc = _read_json(str(path)) if path.is_file() else None
    if doc is None:
        raise ApiError("NOT_FOUND", "A futás kimenete (%s) hiányzik vagy nem olvasható." % rel)
    return doc


def get_results(req):
    app = req.app
    app.require_open()
    rel_dir, run = find_run(app, req.params["run_id"])
    return Result(_load_artifact(app, _file_rel(run, rel_dir, "results", "results.json")), RESULTS_SCHEMA)


def get_plot(req):
    app = req.app
    app.require_open()
    rel_dir, run = find_run(app, req.params["run_id"])
    doc = _load_artifact(app, _file_rel(run, rel_dir, "plot", "plot_data.json"))
    return Result(doc, doc.get("schema") if isinstance(doc.get("schema"), str) else PLOT_SCHEMA)


def get_report(req):
    app = req.app
    app.require_open()
    rel_dir, run = find_run(app, req.params["run_id"])
    rel = _file_rel(run, rel_dir, "report", "report.md")
    try:
        path = app.store.path(rel)
        raw = path.read_bytes() if path.is_file() else None
    except (OSError, store.StoreError):
        raw = None
    if raw is None:
        raise ApiError("NOT_FOUND", "A futás riportja (%s) hiányzik." % rel)
    links = downloads(app, run, rel_dir)
    link = next((v for v in links.values() if v["path"] == rel), None)
    return Result({"run_id": run.get("run_id"), "path": rel, "markdown": raw.decode("utf-8", "replace"),
                   "url": link["url"] if link else None, "expires_at": link["expires_at"] if link else None},
                  REPORT_SCHEMA)


def register(router):
    router.add("GET", "/api/runs", get_runs, schema=SCHEMA, response_schema=RUNS_RESPONSE)
    router.add("GET", "/api/runs/<run_id>", get_run, schema=RUN_SCHEMA, response_schema=_contracts.ref(RUN_SCHEMA))
    router.add("GET", "/api/runs/<run_id>/results", get_results, schema=RESULTS_SCHEMA)
    router.add("GET", "/api/runs/<run_id>/plot", get_plot, schema=PLOT_SCHEMA)
    router.add("GET", "/api/runs/<run_id>/report", get_report, schema=REPORT_SCHEMA)
