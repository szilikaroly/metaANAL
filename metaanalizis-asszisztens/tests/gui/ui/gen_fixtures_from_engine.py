#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A felület fixture-jei a VALÓDI motorból (BCG + Normand) — hogy a fixture-ök ne sodródhassanak el a motortól.

    python3 tests/gui/ui/gen_fixtures_from_engine.py           # újraírja a fixture-öket
    python3 tests/gui/ui/gen_fixtures_from_engine.py --check   # eltérésnél kilépési kód 1 (a CI-ben / tesztben)
    python3 tests/gui/ui/gen_fixtures_from_engine.py --list    # mit ír

Mit és honnan (ma_gui/web/fixtures/):

- ``analysis_plots.json``  — GET /api/runs/<id>/plot: a motor ``szk.ma.plot/v2``-je (``api.analyze`` commit,
  ideiglenes projektben) a BCG-példa hét változatára (elsődleges REML + HKSJ, alcsoport: allokáció, kumulatív: év;
  elavult adat; becsült nélkül; magas RoB nélkül; fix hatás; DL τ²; kiugrók nélkül) és a Normand-példára (MD).
- ``analysis_runs.json``, ``runs.json`` — GET /api/runs…: a motor ``run.json``-jai (``szk.ma.run/v1``) a szerver
  ``decorate`` mezőivel (outcome_id, stale, measure, spec.purpose, dir, data_current_sha256).
- ``analysis_specs.json``  — GET /api/specs/<név>: a motor által érvényesnek ítélt spec-ek (teljes options).
- ``analysis_jobs.json``   — POST /api/analyze statikus tartalék: az ``api.analyze`` explore-nézetmodellje.
- ``validate.json``        — POST /api/validate: ``api.validate_table`` a kinyerés-tábla (table.json) celláin
  (``acknowledged``: null — a fixture-projektben nincs „Nem hiba” döntés).
- ``convert.json``         — POST /api/convert: ``api.convert`` a kinyerés-spec bemeneteivel.
- ``engine.json``          — GET /api/engine: ``api.engine_info()``.
- ``table.json``           — a meglévő táblák kiegészítése a motor ``column_map``-jével (``api.column_map``).

Minden szám és szöveg a motoré. Csak a gépfüggő vagy a fejlesztői háttér (src/dev/*.js) által rögzített
értékek normalizáltak, dokumentáltan: az o1-adattábla sha256-ja a háttér helykitöltője (teljes adat:
'9f3a' + 60×'b', elavult adat: '4c0d' + 60×'c'), a futások időbélyege és elapsed_ms-e a futás-azonosítóé, a
Python-verzió, a KB-útvonal és az önteszt ideje. A row_uid-ok a kinyerés-fixtúra uid-jai (a CSV row_uid oszlopából,
mint a valódi projektben), a forrás-lokátorok (PDF-oldal) a provenance.json eredet-oldalfájljából.
"""
import argparse
import collections
import contextlib
import copy
import csv
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from metaelemzes import __version__, api, spec as S  # noqa: E402

FX = ROOT / "ma_gui" / "web" / "fixtures"
PELDAK = ROOT / "peldak"
XSS = "<img src=x onerror=alert(1)>"
DATA_SHA = {"full": "9f3a" + "b" * 60, "stale": "4c0d" + "c" * 60}
PROJECT_REV = 41

RUNS = collections.OrderedDict((
    # változat: (run_id, időbélyeg, client_seq, spec-név, cél, szülő, opció-eltérések, kizárás, kb_refs)
    ("stale", ("20261003T180200Z-4c0d1e", "2026-10-03T18:02:00Z", 12, "o1_primary", "primary", None, {}, [],
               ["D-S08-001", "D-S09-007", "D-S12-005"])),
    ("primary", ("20261004T211200Z-a1f3c2", "2026-10-04T21:12:00Z", 57, "o1_primary", "primary", None, {}, [],
                 ["D-S08-001", "D-S09-007", "D-S12-005"])),
    ("no_estim", ("20261004T211500Z-c9d2a7", "2026-10-04T21:15:00Z", None, "o1_primary_no_estim", "sensitivity",
                  "o1_primary", {}, ["estimated=igen"], ["D-S12-003"])),
    ("no_rob", ("20261005T091600Z-a7b8c9", "2026-10-05T09:16:00Z", None, "o1_primary_no_rob_high", "sensitivity",
                "o1_primary", {}, ["rob=high"], ["D-S12-002"])),
    ("fixed", ("20261005T091500Z-f1e2d3", "2026-10-05T09:15:00Z", None, "o1_primary_fixed", "sensitivity",
               "o1_primary", {"model": "fixed"}, [], ["D-S12-003"])),
    ("dl", ("20261005T091500Z-d1a2b3", "2026-10-05T09:15:00Z", None, "o1_primary_dl", "sensitivity", "o1_primary",
            {"tau2": "DL"}, [], ["D-S12-003"])),
    ("outliers", ("20261005T091600Z-0f1e2d", "2026-10-05T09:16:00Z", None, "o1_primary_no_outliers", "sensitivity",
                  "o1_primary", {"outliers": True}, [], ["D-S12-003"])),
))
O1_OPTIONS = {"measure": "RR", "subgroup": "allokáció", "cumulative": "év"}
NORMAND_RUN = ("20261005T101000Z-3e7a51", "2026-10-05T10:10:00Z")
VARIANT_TEXT = collections.OrderedDict((
    ("primary", "elsődleges commit (AKTUÁLIS)"),
    ("stale", "régi commit (ELAVULT, X001; a régi adatban Aronson 1948 e1 = 6 és egy HTML-darabot tartalmazó címke — "
              "DOM-biztonsági teszt)"),
    ("no_estim", "gyermek: becsült sorok nélkül (exclude estimated=igen)"),
    ("no_rob", "gyermek: magas RoB nélkül (exclude rob=high)"),
    ("fixed", "gyermek: közös (fix) hatás"),
    ("dl", "gyermek: DL τ²"),
    ("outliers", "gyermek: kiugrók nélkül (--outliers)"),
))
# a kinyerés-képernyő átváltó-tesztjének bemenetei (tests/gui/ui/extraction.spec.js) + a többi fajta mintája
CONVERT_CASES = (
    ({"kind": "median_to_mean_sd"}, {"n": "8", "median": "12,5", "q1": "10", "q3": "16"}, "luo"),
    ({"kind": "median_to_mean_sd", "method": "hozo"}, {"n": "40", "median": "12,5", "min": "4", "max": "30"}, "hozo"),
    ({"kind": "se_to_sd"}, {"se": "0,42", "n": "52"}, None),
    ({"kind": "ci_to_sd"}, {"lower": "10,1", "upper": "14,3", "n": "48"}, None),
    ({"kind": "combine_groups"}, {"n1": "25", "m1": "12,1", "sd1": "3,2", "n2": "30", "m2": "13,1", "sd2": "2,8"}, None),
    ({"kind": "change_sd"}, {"sd_baseline": "5", "sd_final": "4", "corr": "0,5"}, None),
)
CONVERT_TARGET = {"dataset": "03_adatok/o3.csv", "row_uid": "rnrm05", "decimal_mark": ","}
CONVERT_FIELDS = {"median_to_mean_sd": {"mean": "m1", "sd": "sd1"}, "se_to_sd": {"sd": "sd1"},
                  "ci_to_sd": {"sd": "sd1"}, "combine_groups": {"n": "n1", "mean": "m1", "sd": "sd1"},
                  "change_sd": {"sd_change": "sd1"}}
MEASURES = {"03_adatok/o1.csv": "RR", "03_adatok/o2.csv": "RR", "03_adatok/o3.csv": "MD"}

_req = [0]


def _meta(prefix, ms=1):
    _req[0] += 1
    return collections.OrderedDict((("engine", __version__), ("elapsed_ms", ms), ("project_rev", PROJECT_REV),
                                    ("request_id", "q_%s%02d" % (prefix, _req[0]))))


def env(schema, data, prefix, ms=1, warnings=None):
    return collections.OrderedDict((("ok", True), ("schema", schema), ("data", data),
                                    ("warnings", list(warnings or [])), ("meta", _meta(prefix, ms))))


def err(code, http, message):
    return {"ok": False, "error": {"code": code, "http": http, "message": message}}


def _replace_strings(obj, mapping):
    if isinstance(obj, dict):
        return obj.__class__((k, _replace_strings(v, mapping)) for k, v in obj.items())
    if isinstance(obj, list):
        return [_replace_strings(v, mapping) for v in obj]
    if isinstance(obj, str):
        for a, b in mapping.items():
            obj = obj.replace(a, b)
    return obj


# ------------------------------------------------------------------------------------- bemenetek
def _fixture(name):
    with open(FX / name, encoding="utf-8") as fh:
        return json.load(fh, object_pairs_hook=collections.OrderedDict)


def _table_fixture(dataset):
    for r in _fixture("table.json")["routes"]:
        if r["method"] == "GET" and (r.get("query") or {}).get("dataset") == dataset:
            return r["envelope"]["data"]
    raise SystemExit("hiányzik a table.json %s táblája" % dataset)


def _provenance_fixture(dataset):
    for r in _fixture("provenance.json")["routes"]:
        if r["method"] == "GET" and (r.get("query") or {}).get("dataset") == dataset:
            return r["envelope"]["data"]["provenance"]
    return None


def _peldak(name):
    with open(PELDAK / name, encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    return rows[0], rows[1:]


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";", lineterminator="\r\n")
        w.writerow(header)
        w.writerows(rows)


def bcg_rows(stale=False):
    """A BCG-példa (peldak/) a kinyerés-tábla row_uid-, rob- és estimated-oszlopával. Az elavult változatban
    Aronson 1948 e1 = 6 és a Comstock et al 1976 címkéje HTML-darabot tartalmaz (DOM-biztonsági teszt)."""
    header, rows = _peldak("bcg_oltas_RR.csv")
    t = _table_fixture("03_adatok/o1.csv")
    meta = {r["cells"][0]: (r["row_uid"], r["cells"][t["header"].index("rob")],
                            r["cells"][t["header"].index("estimated")]) for r in t["rows"]}
    out = []
    for r in rows:
        r = list(r)
        uid, rob, est = meta[r[0]]
        if stale and r[0] == "Aronson 1948":
            r[header.index("esemény1")] = "6"
        if stale and r[0] == "Comstock et al 1976":
            r[0] = r[0] + " " + XSS
        out.append([uid] + r + [rob, est])
    return ["row_uid"] + header + ["rob", "estimated"], out


def normand_rows():
    header, rows = _peldak("normand1999_folytonos.csv")
    t = _table_fixture("03_adatok/o3.csv")
    uids = {r["cells"][0]: r["row_uid"] for r in t["rows"]}
    return ["row_uid"] + header + ["estimated"], [[uids[r[0]]] + r + ["nem"] for r in rows]


def spec_doc(name, purpose, parent, opts, exclude, kb_refs, outcome="o1", data="03_adatok/o1.csv"):
    """Teljes spec (minden DEFAULTS-kulcs, kanonikus értékekkel — a motor options_from_spec-je szerint)."""
    base = {"schema": "szk.ma.analysis-spec/v1", "name": name, "outcome": outcome, "data": {"path": data},
            "options": dict(opts)}
    full = S.options_from_spec(base)
    sp = collections.OrderedDict((
        ("schema", "szk.ma.analysis-spec/v1"), ("name", name), ("outcome", outcome), ("purpose", purpose),
        ("prespecified", purpose == "primary"), ("protocol_ref", "protokoll 9.2" if purpose == "primary" else None),
        ("parent", parent), ("data", {"path": data}), ("options", full),
        ("filters", {"include": [], "exclude": list(exclude)}), ("kb_refs", list(kb_refs))))
    errs = api.validate_spec(sp)
    if errs:
        raise SystemExit("a motor elutasította a(z) %s specet: %s" % (name, "; ".join(errs)))
    return sp


# ------------------------------------------------------------------------------------- motorfuttatás
class Engine(object):
    """Ideiglenes projekt: adattáblák, eredet-oldalfájl, spec-ek, commit-futások — mind az api-n át."""

    def __init__(self, root):
        self.root = root
        api.project_init(root, "BCG-oltás és tuberkulózis")
        self.runs, self.plots, self.specs, self.shas = {}, {}, {}, {}
        prov = _provenance_fixture("03_adatok/o1.csv")
        self.prov = prov

    def put_table(self, rel, header, rows, prov=None):
        p = os.path.join(self.root, rel)
        _write_csv(p, header, rows)
        if prov is not None:
            with open(os.path.splitext(p)[0] + ".prov.json", "w", encoding="utf-8") as fh:
                json.dump(prov, fh, ensure_ascii=False)
        return S.sha256_file(p)

    def commit(self, key, sp, run_id, started, client_seq):
        rel = "05_elemzes/specs/%s.json" % sp["name"]
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.isfile(path):
            api.save_spec(path, sp)
        self.specs[sp["name"]] = sp
        with contextlib.redirect_stdout(io.StringIO()):
            view = api.analyze(path, mode="commit", project_root=self.root, run_id=run_id, client_seq=client_seq,
                               date=started[:10])
        run = view["run"]
        run["started"] = run["finished"] = started
        run["elapsed_ms"] = 84
        self.runs[key] = run
        self.plots[key] = view["plot"]
        return view

    def explore(self, sp, client_seq):
        with contextlib.redirect_stdout(io.StringIO()):
            view = api.analyze(sp, mode="explore", project_root=self.root, client_seq=client_seq)
        view["run"]["elapsed_ms"] = 84
        view["run"].pop("started", None)
        view["run"].pop("finished", None)
        return view


def decorate(run, purpose, outcome, current_sha, plot=None, server_extra=None):
    """A szerver ma_gui/routes/runs.py decorate()-jének mezői (outcome_id, stale, measure, spec.purpose, dir, és a
    futás plot_data.json-jából a primary.i2_text / pi_text). A measure a FUTÁS saját hatásmérete (run.json), nem a
    spec-fájlé (FID-2); az I²/PI-szöveg minden futáson ott van, nem csak a ?primary=1 listában (FID-3)."""
    out = collections.OrderedDict(run)
    sp = collections.OrderedDict(run["spec"])
    if sp.get("purpose") is None:
        sp["purpose"] = purpose
    out["spec"] = sp
    out["outcome_id"] = outcome
    out["measure"] = run.get("measure")
    if plot is not None and isinstance(run.get("primary"), dict):
        out["primary"] = with_texts(run, plot)["primary"]
    out["stale"] = bool(run["data"]["sha256"]) and run["data"]["sha256"] != current_sha
    out["data_current_sha256"] = current_sha
    out["dir"] = os.path.dirname(run["files"]["results"]["path"]) if run.get("files") else None
    out.update(server_extra or {})
    return out


def with_texts(run, plot):
    """A szerver ma_gui/routes/runs.py _artifact_facts()-ja: az áttekintő és a terv futás-táblájának I²- és
    PI-szövege a futás plot_data.json-jából (a motor kész szövegei; számot a szerver nem formáz)."""
    out = collections.OrderedDict(run)
    prim = collections.OrderedDict(run.get("primary") or {})
    het = plot.get("heterogeneity") or {}
    if het.get("i2_text") is not None:
        prim.setdefault("i2_text", het["i2_text"])
    summ = [x for x in plot.get("summaries") or [] if x.get("primary")]
    if summ and summ[0].get("pi_text") is not None:
        prim.setdefault("pi_text", summ[0]["pi_text"])
    out["primary"] = prim
    return out


def generate():
    tmp = tempfile.mkdtemp(prefix="ma_fx_engine_")
    try:
        return _generate(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _generate(tmp):
    E = Engine(tmp)
    out = collections.OrderedDict()
    # ---- futások: előbb a régi adat (elavult futás), aztán a mostani
    header, stale_rows = bcg_rows(stale=True)
    real_stale = E.put_table("03_adatok/o1.csv", header, stale_rows, E.prov)
    header, rows = bcg_rows()
    specs = {}
    for key, (rid, started, seq, name, purpose, parent, delta, excl, kb) in RUNS.items():
        if key == "primary":
            real_full = E.put_table("03_adatok/o1.csv", header, rows, E.prov)
        opts = dict(O1_OPTIONS, **delta)
        specs[key] = spec_doc(name, purpose, parent, opts, excl, kb)
        E.commit(key, specs[key], rid, started, seq)
    n_header, n_rows = normand_rows()
    real_normand = E.put_table("03_adatok/o3.csv", n_header, n_rows)
    o3_spec = spec_doc("o3_primary", "primary", None, {"measure": "MD"}, [], ["D-S08-001"], outcome="o3",
                       data="03_adatok/o3.csv")
    E.commit("normand", o3_spec, NORMAND_RUN[0], NORMAND_RUN[1], None)
    explore = E.explore(specs["primary"], 1)
    sha_map = {real_full: DATA_SHA["full"], real_stale: DATA_SHA["stale"]}

    def norm(obj):
        return _replace_strings(copy.deepcopy(obj), sha_map)

    # ---- futás-leírók a szerver mezőivel
    current = {"o1": DATA_SHA["full"], "o3": real_normand}
    dec = collections.OrderedDict()
    for key, (rid, started, seq, name, purpose, parent, delta, excl, kb) in RUNS.items():
        dec[key] = decorate(norm(E.runs[key]), purpose, "o1", current["o1"], norm(E.plots[key]))
    dec["normand"] = decorate(norm(E.runs["normand"]), "primary", "o3", current["o3"], norm(E.plots["normand"]))

    # ---- analysis_plots.json
    routes = []
    for key, desc in VARIANT_TEXT.items():
        routes.append({"method": "GET", "path": "/api/runs/%s/plot" % RUNS[key][0],
                       "envelope": env("szk.ma.plot/v2", norm(E.plots[key]), "an", 3)})
    routes.append({"method": "GET", "path": "/api/runs/%s/plot" % NORMAND_RUN[0],
                   "envelope": env("szk.ma.plot/v2", norm(E.plots["normand"]), "an", 3)})
    fallback = norm(E.plots["primary"])
    fallback["meta"]["run_id"] = None
    routes.append({"method": "GET", "path": "/api/runs/*/plot", "envelope": env("szk.ma.plot/v2", fallback, "an", 3)})
    out["analysis_plots.json"] = {
        "description": "GET /api/runs/<run_id>/plot — szk.ma.plot/v2 (4.6) a VALÓDI motorból (tests/gui/ui/"
                       "gen_fixtures_from_engine.py: api.analyze commit egy ideiglenes projektben): BCG-példa (13 "
                       "vizsgálat, RR, REML + HKSJ, alcsoport: allokáció, kumulatív: év) — %s; valamint a Normand-"
                       "példa (o3, MD, %s). A szövegek a motor kész szövegei (4.0: tizedespont; hu: '-', en: U+2212); "
                       "a forrás-lokátor (PDF-oldal) az eredet-oldalfájlból (provenance.json). Ismeretlen futás-"
                       "azonosító → az elsődleges nézetmodell." % ("; ".join(VARIANT_TEXT.values()), NORMAND_RUN[0]),
        "routes": routes}

    # ---- analysis_runs.json
    run_routes = [
        {"method": "GET", "path": "/api/runs", "query": {"outcome": "o1"},
         "envelope": env("szk.ma.runs/v1", {"runs": [dec["primary"], dec["no_estim"], dec["stale"]]}, "an")},
        {"method": "GET", "path": "/api/runs", "query": {"outcome": "o2"},
         "envelope": env("szk.ma.runs/v1", {"runs": []}, "an")},
        {"method": "GET", "path": "/api/runs", "query": {"outcome": "o3"},
         "envelope": env("szk.ma.runs/v1", {"runs": [dec["normand"]]}, "an")},
    ]
    for key in list(VARIANT_TEXT) + ["normand"]:
        rid = NORMAND_RUN[0] if key == "normand" else RUNS[key][0]
        run_routes.append({"method": "GET", "path": "/api/runs/%s" % rid, "envelope": env("szk.ma.run/v1", dec[key], "an")})
    out["analysis_runs.json"] = {
        "description": "GET /api/runs?outcome=<id> és GET /api/runs/<run_id> — a motor run.json-jai (szk.ma.run/v1: "
                       "participants_text, rob_high, expanded_argv) a szerver mezőivel "
                       "(outcome_id, stale = X001, measure, spec.purpose, dir, data_current_sha256). Generálta: "
                       "tests/gui/ui/gen_fixtures_from_engine.py.",
        "routes": run_routes}

    # ---- runs.json (áttekintő; ?primary=1: kimenetenként a legutóbbi elsődleges)
    grade = {"grade": {"certainty": "very low"}}
    prim = decorate(norm(E.runs["primary"]), "primary", "o1", current["o1"], norm(E.plots["primary"]), grade)
    stale = decorate(norm(E.runs["stale"]), "primary", "o1", current["o1"], norm(E.plots["stale"]), grade)
    out["runs.json"] = {
        "description": "GET /api/runs — commit-futások a motor run.json-jaiból (szk.ma.run/v1) + a szerver mezői "
                       "(outcome_id, stale = X001, measure, spec.purpose, dir); a grade a projektnapló GRADE-sorából. "
                       "A ?primary=1 kimenetenként a legutóbbi elsődlegeset adja, a futás plot_data.json-jának I²- és "
                       "PI-szövegével (primary.i2_text, pi_text). Generálta: "
                       "tests/gui/ui/gen_fixtures_from_engine.py.",
        "routes": [
            {"method": "GET", "path": "/api/runs", "envelope": env("szk.ma.runs/v1", {"runs": [prim, stale]}, "fx")},
            {"method": "GET", "path": "/api/runs", "query": {"primary": "1"},
             "envelope": env("szk.ma.runs/v1", {"runs": [prim]}, "fx")}]}

    # ---- analysis_specs.json
    out["analysis_specs.json"] = {
        "description": "GET /api/specs/<név> — szk.ma.analysis-spec/v1 (4.4): a motor options_from_spec-jével "
                       "teljessé tett, a motor validate_spec-je szerint érvényes spec-ek (options = a DEFAULTS kulcsai). "
                       "PUT/új spec: dinamikus fixture (src/dev/analysis_fixtures.js). Ismeretlen név → 404.",
        "routes": [
            {"method": "GET", "path": "/api/specs/o1_primary", "etag": '"spec-o1_primary-1"',
             "envelope": env("szk.ma.analysis-spec/v1", specs["primary"], "an")},
            {"method": "GET", "path": "/api/specs/o1_primary_no_estim", "etag": '"spec-o1_primary_no_estim-1"',
             "envelope": env("szk.ma.analysis-spec/v1", specs["no_estim"], "an")},
            {"method": "GET", "path": "/api/specs/o3_primary", "etag": '"spec-o3_primary-1"',
             "envelope": env("szk.ma.analysis-spec/v1", o3_spec, "an")},
            {"method": "GET", "path": "/api/specs/*", "status": 404,
             "envelope": err("NOT_FOUND", 404, "Nincs ilyen elemzési spec (05_elemzes/specs/).")}]}

    # ---- analysis_jobs.json (statikus tartalék; a fejlesztői háttér dinamikusan válaszol)
    ex = norm(explore)
    ex_run = decorate(ex["run"], "primary", "o1", current["o1"])
    ex_run["stale"] = False
    ex_result = collections.OrderedDict((
        ("schema", ex["schema"]), ("run", ex_run), ("plot", ex["plot"]),
        ("validation_summary", ex["validation_summary"]), ("findings", ex["findings"]),
        ("excluded", ex["excluded"]), ("filter_report", ex["filter_report"]), ("warnings", ex["warnings"]),
        ("command", ex["command"])))
    commit_run = dec["primary"]
    out["analysis_jobs.json"] = {
        "description": "POST /api/analyze + GET /api/jobs/<id> — statikus tartalék (jobs.py pillanatkép-alak: job_id, "
                       "kind, mode, client_seq, status, elapsed_ms, result | error). Az explore-eredmény az "
                       "api.analyze(mode='explore') nézetmodellje (a nagy 'results' kulcs nélkül); a fejlesztői build "
                       "dinamikusan válaszol (src/dev/analysis_fixtures.js).",
        "routes": [
            {"method": "POST", "path": "/api/analyze", "body": {"mode": "explore"},
             "envelope": env("szk.ma.job/v1", collections.OrderedDict((
                 ("job_id", "j_fx_explore"), ("kind", "explore:o1"), ("mode", "explore"), ("client_seq", 1),
                 ("status", "done"), ("elapsed_ms", 84), ("run_ms", 80), ("result", ex_result))), "an", 84)},
            {"method": "POST", "path": "/api/analyze", "body": {"mode": "commit"},
             "envelope": env("szk.ma.job/v1", {"job_id": "j_fx_commit", "kind": "commit:o1", "mode": "commit",
                                               "client_seq": 2, "status": "queued", "elapsed_ms": 0, "run_ms": 0}, "an")},
            {"method": "GET", "path": "/api/jobs/j_fx_commit", "sequence": [
                {"status": 200, "envelope": env("szk.ma.job/v1", {
                    "job_id": "j_fx_commit", "kind": "commit:o1", "mode": "commit", "client_seq": 2,
                    "status": "running", "elapsed_ms": 40, "run_ms": 20}, "an")},
                {"status": 200, "envelope": env("szk.ma.job/v1", collections.OrderedDict((
                    ("job_id", "j_fx_commit"), ("kind", "commit:o1"), ("mode", "commit"), ("client_seq", 2),
                    ("status", "done"), ("elapsed_ms", 120), ("run_ms", 96),
                    ("result", collections.OrderedDict((
                        ("schema", ex["schema"]), ("run", commit_run), ("plot", None),
                        ("validation_summary", commit_run["validation_summary"]), ("excluded", [])))))), "an")}]},
            {"method": "GET", "path": "/api/jobs/*", "status": 404,
             "envelope": err("NOT_FOUND", 404, "Nincs ilyen feladat (lejárt vagy ismeretlen job_id).")}]}

    # ---- validate.json (a kinyerés-tábla piszkozata, nyers cellákkal)
    v_routes = []
    for ds, measure in MEASURES.items():
        t = _table_fixture(ds)
        doc = api.validate_table(["row_uid"] + list(t["header"]), [[r["row_uid"]] + list(r["cells"]) for r in t["rows"]],
                                 measure, {}, decimal_mark=t["format"].get("decimal_mark"),
                                 delimiter=t["format"].get("delimiter"), lines=[r.get("line") for r in t["rows"]],
                                 project_dir=tmp, dataset=ds)
        v_routes.append({"method": "POST", "path": "/api/validate", "body": {"dataset": ds},
                         "envelope": env("szk.ma.validation/v1", doc, "ex")})
    out["validate.json"] = {
        "description": "POST /api/validate — szk.ma.validation/v1 (4.3): api.validate_table a kinyerés-tábla "
                       "(table.json) nyers celláin, a row_uid oszloppal; acknowledged: a projektnapló „Nem hiba” "
                       "döntései szerint (itt nincs ilyen → null). A fejlesztői build a src/dev/extraction_backend.js-"
                       "ben a szerkesztésekre is válaszol. Generálta: tests/gui/ui/gen_fixtures_from_engine.py.",
        "routes": v_routes}

    # ---- convert.json
    c_routes = []
    for body, inputs, method in CONVERT_CASES:
        kind = body["kind"]
        req = {"schema": "szk.ma.convert-request/v1", "kind": kind, "inputs": inputs,
               "target": dict(CONVERT_TARGET, fields=CONVERT_FIELDS[kind])}
        if method:
            req["method"] = method
        res = api.convert(req)
        c_routes.append({"method": "POST", "path": "/api/convert", "body": body,
                         "envelope": env("szk.ma.convert-result/v1", res, "ex")})
    try:
        api.convert({"schema": "szk.ma.convert-request/v1", "kind": "ci_to_sd",
                     "inputs": {"lower": "14,3", "upper": "10,1", "n": "48"}})
        msg = None
    except ValueError as exc:
        msg = str(exc)
    c_routes.append({"method": "POST", "path": "/api/convert", "body": {"kind": "ci_to_sd", "method": "invalid"},
                     "status": 422, "envelope": err("VALIDATION", 422, msg or "érvénytelen bemenet")})
    out["convert.json"] = {
        "description": "POST /api/convert — szk.ma.convert-request/v1 → szk.ma.convert-result/v1 (4.7): api.convert "
                       "(a motor conversions-függvényei) a kinyerés-spec bemeneteivel (n = 8, medián 12,5, Q1 10, Q3 16; "
                       "SE 0,42, n = 52; …), a cél tábla tizedesjelével (cell_text: ','). outputs_text: kijelzési "
                       "szöveg (4.0: tizedespont); assumptions / warnings: {hu, en}. Az estimated a motor döntése. "
                       "Generálta: tests/gui/ui/gen_fixtures_from_engine.py.",
        "routes": c_routes}

    # ---- engine.json
    info = api.engine_info(selftest=True)
    info["python"] = "3.x"
    info["kb"] = collections.OrderedDict((("db", "tudasbazis/tudasbazis.sqlite"), ("built", True), ("fresh", True)))
    if isinstance(info.get("selftest"), dict):
        info["selftest"]["elapsed_ms"] = 200
    out["engine.json"] = {
        "description": "GET /api/engine — api.engine_info(): verzió, önteszt, mértékek, opció- és szabály-metaadat "
                       "(3.2, 3.4), a valódi motorból. Normalizálva (gépfüggő): python, kb.db (relatív), "
                       "selftest.elapsed_ms. Generálta: tests/gui/ui/gen_fixtures_from_engine.py.",
        "routes": [{"method": "GET", "path": "/api/engine", "envelope": env("szk.ma.engine-info/v1", info, "fx", 3)}]}

    # ---- table.json: + column_map (a GET /api/table a motor oszlopfelismerését is adja)
    tab = _fixture("table.json")
    for r in tab["routes"]:
        data = (r.get("envelope") or {}).get("data")
        if isinstance(data, dict) and isinstance(data.get("header"), list):
            data["column_map"] = api.column_map(list(data["header"]))
    out["table.json"] = tab
    return out


# ------------------------------------------------------------------------------------- kiírás
COMPACT = ("analysis_plots.json", "analysis_jobs.json")


def render(name, obj):
    if name in COMPACT:
        lines = ['{"description": %s,' % json.dumps(obj["description"], ensure_ascii=False), ' "routes": [']
        lines.append(",\n".join("  " + json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in obj["routes"]))
        return "\n".join(lines) + "\n ]}\n"
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="csak ellenőriz: eltérésnél kilépési kód 1")
    ap.add_argument("--list", action="store_true", help="a generált fixture-fájlok listája")
    a = ap.parse_args(argv)
    files = generate()
    if a.list:
        print("\n".join(files))
        return 0
    drift = []
    for name, obj in files.items():
        text = render(name, obj)
        path = FX / name
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old == text:
            continue
        drift.append(name)
        if not a.check:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print("%-24s %8d bájt" % (name, len(text.encode("utf-8"))))
    if a.check:
        if drift:
            print("a fixture-ök eltérnek a motortól: %s — futtasd: python3 tests/gui/ui/gen_fixtures_from_engine.py"
                  % ", ".join(drift))
            return 1
        print("a fixture-ök a motorral egyeznek (%d fájl)" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
