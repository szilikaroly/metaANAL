#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A GRADE / SoF / Protokoll képernyők (3.5.12, „1 Protokoll”) fejlesztői fixture-jeinek generátora
(→ ma_gui/web/fixtures/grade.json).

Minden szám és motor-szöveg a VALÓDI motorból:
  - a GRADE-tanács (szk.ma.grade/v1 piszkozat: doménenként suggestion + advisory, „Miért?” szöveg, KB-azonosítók)
    és a SoF (szk.ma.sof/v1) a ``metaelemzes.grade_help.advice`` / ``.sof`` kimenete a BCG-példán (api.analyze);
    csak a futás-azonosító a fixture-futásé (``analysis_runs.json``), és egy XSS-próba szöveg kerül egy jelzésbe;
  - a futás leírása a motorból generált ``analysis_runs.json``-ból (tests/gui/ui/gen_fixtures_from_engine.py);
  - a GRADE-szókincs a szerverből (``ma_gui.routes.grade.vocab``), a protokoll a projekt- és spec-fixture-ökből.

Használat:
  python3 tests/gui/ui/gen_grade_fixtures.py           # írás
  python3 tests/gui/ui/gen_grade_fixtures.py --check   # 1-es kilépés, ha a lemezen lévő eltér
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "ma_gui" / "web" / "fixtures"
OUT = FIX / "grade.json"

from metaelemzes import api, grade_help  # noqa: E402
from ma_gui import privacy  # noqa: E402
from ma_gui.routes import grade as grade_route  # noqa: E402

META = {"engine": "0.2.0", "elapsed_ms": 1, "project_rev": 61}
RUN_ID = "20261004T211200Z-a1f3c2"
XSS = "<img src=x onerror=alert(1)>"
ALL_ENGINE = {"amstar2_consistency": True, "grade_advice": True, "grade_get": True, "grade_put": True,
              "grade_record": False, "instrument": True, "sof": True}
_rid = [0]


def env(schema, data, warnings=None):
    _rid[0] += 1
    return {"ok": True, "schema": schema, "data": data, "warnings": warnings or [],
            "meta": dict(META, request_id="q_gr%02d" % _rid[0])}


def route(method, path, envelope, query=None, status=200, etag=None):
    r = {"method": method, "path": path}
    if query:
        r["query"] = query
    r["status"] = status
    if etag:
        r["etag"] = etag
    r["envelope"] = envelope
    return r


def load(name, path, query=None):
    with open(FIX / name, encoding="utf-8") as fh:
        doc = json.load(fh)
    for r in doc["routes"]:
        if r["path"] == path and (r.get("query") or None) == (query or None) and r.get("method", "GET") == "GET":
            return r["envelope"]["data"]
    raise SystemExit("hiányzó fixture: %s %s %s" % (name, path, query))


def t(hu, en=None):
    return {"hu": hu, "en": en if en is not None else hu}


def engine_view():
    """A BCG-példa elemzése a VALÓDI motorral (api.analyze, explore) — a grade_help bemenete."""
    tmp = tempfile.mkdtemp(prefix="ma_grfx_")
    try:
        os.makedirs(os.path.join(tmp, "03_adatok"))
        shutil.copy(str(ROOT / "peldak" / "bcg_oltas_RR.csv"), os.path.join(tmp, "03_adatok", "o1.csv"))
        spec = {"schema": "szk.ma.analysis-spec/v1", "name": "o1_primary", "outcome": "o1",
                "data": {"path": "03_adatok/o1.csv"}, "options": {"measure": "RR"}}
        return api.analyze(spec, mode="explore", project_root=tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_summary(run):
    prim = run.get("primary") or {}
    return {"run_id": run["run_id"], "dir": run["dir"], "stale": run["stale"], "k": run["k"], "measure": run["measure"],
            "display_text": prim.get("display_text"), "i2_text": prim.get("i2_text"), "pi_text": prim.get("pi_text"),
            "participants_text": run.get("participants_text"), "spec": (run.get("spec") or {}).get("name"),
            "finished": run.get("finished")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)

    project = load("project.json", "/api/project")
    run = load("analysis_runs.json", "/api/runs/%s" % RUN_ID)
    grades = load("log_grade.json", "/api/log/grade")["items"]
    specs = {n: load("analysis_specs.json", "/api/specs/%s" % n) for n in ("o1_primary", "o1_primary_no_estim", "o3_primary")}
    o1 = [o for o in project["outcomes"] if o["id"] == "o1"][0]
    o2 = [o for o in project["outcomes"] if o["id"] == "o2"][0]
    j = [g for g in grades if g["outcome"] == "o1"][-1]
    journal = {"id": j["id"], "ts": j["ts"], "certainty": j["certainty"]}
    rs = run_summary(run)

    def outcome_info(o):
        return {"id": o["id"], "name": o["name"], "critical": bool(o.get("critical")), "measure": o.get("measure"),
                "grade_start": o.get("grade_start"), "data": o.get("data")}

    def view(o, r, journal_row):
        return {"schema": "szk.ma.grade-view/v1", "outcome": outcome_info(o), "run": r, "grade": None,
                "path": "06_kezirat/grade/%s.grade.json" % o["id"], "journal": journal_row, "unresolved": [],
                "missing": list(grade_route.DOMAINS), "run_matches": None,
                "conventions": {"grade_suspected": "unresolved"}, "vocab": grade_route.vocab(), "engine": ALL_ENGINE}

    # ---------------------------------------------------------------- tanács és SoF: a VALÓDI motor (grade_help)
    view_bcg = engine_view()
    advice = grade_help.advice(view_bcg, outcome_id="o1", start="high", importance="critical")
    advice["run_id"] = RUN_ID                       # a fixture-futás azonosítója (a számok ugyanabból a BCG-adatból)
    advice["advice"]["notes"] = [n for n in advice["advice"]["notes"] if "commit" not in n["hu"]]
    # XSS-próba: HTML-szerű szöveg a motor-szövegek helyén — a felület csak szövegként jelenítheti meg
    advice["domains"]["indirectness"]["suggestion"]["flags"].append(
        {"code": "xss_probe", "level": "info", "text": t(XSS)})
    sof_doc = grade_help.sof(view_bcg, assumed_risks=None, certainty=journal["certainty"], outcome_id="o1",
                             label=o1["name"], design="RCT", importance="critical")
    sof_doc["run_id"] = RUN_ID
    sof_view = {"schema": "szk.ma.sof-view/v1",
                "outcome": {"id": "o1", "name": o1["name"], "measure": o1["measure"], "critical": True},
                "run": rs, "path": "06_kezirat/sof/o1.sof.json", "saved": None, "saved_matches_run": None,
                "preview": sof_doc,
                "inputs": {"assumed_risks": [{"label": None, "source": "control_pool", "per_1000": None, "note": None}],
                           "footnotes": []},
                "certainty": journal["certainty"], "certainty_source": "journal", "engine": ALL_ENGINE}

    # ---------------------------------------------------------------- protokoll
    spec_rows = []
    for name, sp in sorted(specs.items()):
        spec_rows.append({"name": name, "path": "05_elemzes/specs/%s.json" % name, "etag": "\"spec-%s-1\"" % name,
                          "outcome": sp.get("outcome"), "purpose": sp.get("purpose"), "parent": sp.get("parent"),
                          "prespecified": sp.get("prespecified"), "protocol_ref": sp.get("protocol_ref"), "problems": []})
    protocol = {"schema": "szk.ma.protocol/v1", "path": "ma-projekt.json", "has_project_json": True,
                "title": project["title"], "question": {k: project["question"].get(k) or "" for k in "PICO"},
                "question_text": None, "review_type": project.get("review_type") or "intervention",
                "review_types": list(api.REVIEW_TYPES), "data_class": project["data_class"],
                "data_class_source": "ma-projekt.json", "data_class_label": privacy.DATA_CLASS_LABELS.get(project["data_class"]),
                "registration": {"registry": "PROSPERO", "id": "CRD42024000001"},
                "outcomes": project["outcomes"], "specs": spec_rows, "conventions": project.get("conventions") or {}}

    o2_view = view(o2, None, None)
    doc = {"description": "GRADE / SoF / Protokoll (3.5.12; „1 Protokoll”) — generálta: tests/gui/ui/gen_grade_fixtures.py. "
                          "A számok a motorból (futás- és ábra-fixture-ök, motor-formázó); a tanácsok és a SoF-mondat "
                          "szövege illusztratív, amíg a motor grade_help / sof függvénye el nem készül. A PUT/POST "
                          "útvonalakat a src/dev/grade_backend.js kezeli (állapottartó).",
           "routes": [
               route("GET", "/api/grade/o1", env("szk.ma.grade-view/v1", view(o1, rs, journal))),
               route("GET", "/api/grade/o2", env("szk.ma.grade-view/v1", o2_view)),
               route("GET", "/api/grade/o1/advice", env("szk.ma.grade-advice-view/v1",
                                                         {"schema": "szk.ma.grade-advice-view/v1", "outcome_id": "o1",
                                                          "run": rs, "mid": None, "advice": advice})),
               route("GET", "/api/sof/o1", env("szk.ma.sof-view/v1", sof_view)),
               route("GET", "/api/protocol", env("szk.ma.protocol/v1", protocol), etag="\"pr-1\""),
           ]}
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if a.check:
        cur = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if cur != text:
            print("eltér: %s — futtasd: python3 tests/gui/ui/gen_grade_fixtures.py" % OUT)
            return 1
        print("rendben: %s" % OUT)
        return 0
    OUT.write_text(text, encoding="utf-8")
    print("írva: %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
