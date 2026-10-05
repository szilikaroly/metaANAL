#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A folyamat-képernyők (3.5.1, 3.5.14–3.5.17) fejlesztői fixture-jeinek generátora.

A számok és szövegek a VALÓDI motorból és tudásbázisból jönnek, ahol a motor már tud válaszolni:
  - PRISMA: metaelemzes.prisma.check_flow (P-megállapítások, derived) a BCG-példa kitalált szűrési számaira;
  - KB: metaelemzes.kb.show / kb.search (szabályok és tudás-tételek; a teljes szöveg (chunk) SZINTETIKUS,
    mert a jogvédett szövegrész nem kerülhet a repóba — terv 7.7);
  - kapu: a GATE_BLOCKED üzenet a metaelemzes.projekt.checkpoint valódi szövege (ideiglenes projektben).
A többi (napló, export, képességek) a 4. fejezet példáiból kézzel összeállított boríték.

Használat:
  python3 tests/gui/ui/gen_process_fixtures.py           # fixture-ök írása (ma_gui/web/fixtures/)
  python3 tests/gui/ui/gen_process_fixtures.py --check   # 1-es kilépés, ha a lemezen lévő eltér
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "ma_gui" / "web" / "fixtures"

from metaelemzes import kb, prisma, projekt  # noqa: E402

META = {"engine": "0.2.0", "elapsed_ms": 1, "project_rev": 41}
XSS = "<img src=x onerror=alert(1)>"
NOTE = {"hu": "helyi forrás — nem exportálható", "en": "local source — not for export"}
_rid = [0]


def env(schema, data, warnings=None):
    _rid[0] += 1
    return {"ok": True, "schema": schema, "data": data, "warnings": warnings or [],
            "meta": dict(META, request_id="q_pr%02d" % _rid[0])}


def err(code, http, message, details=None):
    e = {"code": code, "http": http, "message": message}
    if details is not None:
        e["details"] = details
    return {"ok": False, "error": e}


# ---------------------------------------------------------------- PRISMA (3.5.14)
# a felület toFlow()-jának kulcssorrendje: schema, a dobozok (BOXES) sorrendje, majd az okok
BOX_KEYS = ["identified_databases", "identified_registers", "dedup_removed", "automation_removed",
            "removed_before_screening_n", "screened", "excluded_screening", "sought_for_retrieval", "not_retrieved",
            "assessed_eligibility", "excluded_eligibility", "included_reports", "included_studies"]
BASE = {"identified_databases": "412", "identified_registers": "9", "dedup_removed": "96", "automation_removed": None,
        "removed_before_screening_n": None, "screened": "325", "excluded_screening": "271",
        "sought_for_retrieval": "54", "not_retrieved": "3", "assessed_eligibility": "51",
        "excluded_eligibility": "36", "included_reports": "15", "included_studies": "13"}
REASONS = [("nem RCT / kontrollált vizsgálat", "17"), ("nincs TBC-kimenet", "11"), ("nem BCG-oltás", "6")]


def ui_flow(values=None, reasons=None):
    v = dict(BASE, **(values or {}))
    out = {"schema": "szk.prisma-flow/v1"}
    for k in BOX_KEYS:
        out[k] = v[k]
    out["excluded_eligibility_reasons"] = {r: n for r, n in (reasons if reasons is not None else REASONS)}
    return out


def check(flow):
    d = prisma.check_flow(flow).to_dict()
    return {k: d[k] for k in ("template", "ok", "summary", "findings", "derived")}


def stored(flow):
    """A szerver által tárolt (int) alak: a motor counts/reasons-e a szerződés kulcsneveivel."""
    out = {"schema": "szk.prisma-flow/v1"}
    for k in BOX_KEYS:
        v = flow.get(k)
        out[k] = int(v) if v not in (None, "") and str(v).isdigit() else (v if v not in (None, "") else None)
    out["excluded_eligibility_reasons"] = {k: int(v) if str(v).isdigit() else v for k, v in flow["excluded_eligibility_reasons"].items()}
    return out


def prisma_state(flow, mode="manual"):
    st = {"schema": "szk.ma.prisma/v1", "mode": mode, "path": "02_szures/prisma_flow.json",
          "source": {"kind": "manual", "updated": "2026-10-04T20:12:00Z", "actor": "SzK"},
          "override": None, "flow": stored(flow), "check": check(flow),
          "studies": {"path": "03_adatok/studies.json", "studies": 13, "reports": 15},
          "meta": [{"outcome_id": "o1", "name": {"hu": "TBC-incidencia", "en": "TB incidence"}, "k": 13,
                    "run_id": "20261004T211200Z-a1f3c2"}],
          "cross": [{"code": "X014", "severity": "info", "title": "Elemzett k (13) ≤ bevont vizsgálatok (I = 13)",
                     "detail": "05_elemzes/o1/20261004T211200Z-a1f3c2/run.json: k = 13; studies.json: 13 vizsgálat"},
                    {"code": "X015", "severity": "info", "title": "o1: included_meta 13 = a commit-futás k-ja",
                     "detail": "PRISMA „metaanalízisben” = 13"}],
          "composer_status": []}
    if mode == "composer":
        st["source"] = {"kind": "composer", "composer_version": "1.4.1", "project": "bcg",
                        "generated": "2026-10-04T20:12:00Z", "sha256": "5e" * 32}
        st["path"] = "02_szures/prisma_flow.json"
        st["composer_status"] = [{"hu": "5D-kapu: „függőben” rekord: 0 · retmax-figyelmeztetés: a PubMed-keresés 412 találata a retmax (500) alatt maradt",
                                  "en": "5D gate: 'pending' records: 0 · retmax warning: the PubMed search returned 412 hits, below retmax (500)"}]
    return st


def prisma_fixture():
    base = ui_flow()
    variants = [
        ("base — P007 (az okok összege 34 ≠ H 36)", base),
        ("javított okok — nincs megállapítás", ui_flow(reasons=[REASONS[0], ("nincs TBC-kimenet", "13"), REASONS[2]])),
        ("E üres — a motor levezeti (P011) + P007", ui_flow({"sought_for_retrieval": None})),
        ("I = 16 > J = 15 — P006 + P007", ui_flow({"included_studies": "16"})),
        ("érvénytelen szám (12a) — P001", ui_flow({"screened": "12a"})),
    ]
    routes = [{"method": "GET", "path": "/api/prisma", "etag": '"prisma-1"',
               "envelope": env("szk.ma.prisma/v1", prisma_state(base))},
              {"method": "GET", "path": "/api/prisma", "query": {"variant": "composer"}, "etag": '"prisma-c1"',
               "envelope": env("szk.ma.prisma/v1", prisma_state(base, "composer"))}]
    for label, fl in variants:
        st = prisma_state(fl)
        st["saved"] = False
        routes.append({"method": "PUT", "path": "/api/prisma/manual", "note": label,
                       "body": {"dry_run": True, "flow": fl}, "envelope": env("szk.ma.prisma/v1", st)})
    # minden más előnézet: a motor P001-et adna a hiányzó adatra — itt az alapállapot ellenőrzése (csak dev)
    routes.append({"method": "PUT", "path": "/api/prisma/manual", "body": {"dry_run": True},
                   "envelope": env("szk.ma.prisma/v1", dict(prisma_state(base), saved=False))})
    routes.append({"method": "PUT", "path": "/api/prisma/manual", "etag": '"prisma-2"',
                   "envelope": env("szk.ma.prisma/v1", dict(prisma_state(base), saved=True))})
    return {"description": "GET /api/prisma, PUT /api/prisma/manual (dry_run előnézet és mentés) — 3.5.14. A check a valódi "
                           "metaelemzes.prisma.check_flow kimenete a BCG-példa kitalált szűrési számaira; a body.flow a felület "
                           "toFlow()-jának pontos kulcssorrendjében (generálta: tests/gui/ui/gen_process_fixtures.py).",
            "routes": routes}


# ---------------------------------------------------------------- vizsgálat ↔ jelentés (4.10)
BCG = [("ARONSON1948", "Aronson 1948", "pmid:18874316"), ("FERGUSON1949", "Ferguson & Simes 1949", "pmid:18111983"),
       ("ROSENTHAL1960", "Rosenthal et al 1960", "pmid:13843893"), ("HART1977", "Hart & Sutherland 1977", "pmid:406977"),
       ("FRIMODT1973", "Frimodt-Moller et al 1973", "pmid:4590547"), ("STEIN1953", "Stein & Aronson 1953", "pmid:13079297"),
       ("VANDIVIERE1973", "Vandiviere et al 1973", "pmid:4731427"), ("TPT1980", "TPT Madras 1980", "pmid:6993658"),
       ("COETZEE1968", "Coetzee & Berjak 1968", "pmid:5690547"), ("ROSENTHAL1961", "Rosenthal et al 1961", "pmid:13691834"),
       ("COMSTOCK1974", "Comstock et al 1974", "pmid:4814797"), ("COMSTOCK1969", "Comstock & Webster 1969", "pmid:5367281"),
       ("COMSTOCK1976", "Comstock et al 1976 " + XSS, "pmid:1052460")]


def studies_doc():
    out = []
    for sid, label, rec in BCG:
        design = "rct_parallel" if sid not in ("FRIMODT1973", "STEIN1953", "ROSENTHAL1961", "COMSTOCK1974",
                                               "COMSTOCK1969", "COMSTOCK1976") else "nrsi"
        reps = [{"rec_id": rec, "role": "primary", "doc": rec if sid in ("ARONSON1948", "HART1977") else None}]
        if sid == "HART1977":
            reps.append({"rec_id": "pmid:5434386", "role": "secondary", "doc": None})
        if sid == "TPT1980":
            reps.append({"rec_id": "pmid:10343389", "role": "secondary", "doc": None})
        out.append({"study_id": sid, "label": label, "registration": None, "design": design, "outcomes": ["o1"],
                    "reports": reps})
    return {"schema": "szk.ma.studies/v1", "path": "03_adatok/studies.json", "studies": out,
            "summary": {"studies": 13, "reports": 15}, "problems": []}


def studies_fixture():
    return {"description": "GET/PUT /api/studies — szk.ma.studies/v1 (4.10) a BCG-példa 13 vizsgálatával; az egyik címke "
                           "szándékosan HTML-szerű (XSS-próba: szövegként kell megjelennie).",
            "routes": [{"method": "GET", "path": "/api/studies", "etag": '"studies-1"', "envelope": env("szk.ma.studies/v1", studies_doc())},
                       {"method": "PUT", "path": "/api/studies", "etag": '"studies-2"', "envelope": env("szk.ma.studies/v1", studies_doc())}]}


# ---------------------------------------------------------------- napló (3.5.15)
FINDINGS = [
    {"id": 3, "ts": "2026-09-28T09:15:00Z", "agent": "reviewer", "stage_id": "S03", "severity": "major",
     "title": "A keresési napló nem tartalmazza a regiszter-keresés dátumát", "detail": "PRISMA-S 13. tétel", "evidence": None,
     "kb_refs": "D-S04-009", "status": "fixed", "resolution": "A 01_kereses/naplo.md kiegészítve (2026-09-29).",
     "resolved_ts": "2026-09-29T08:00:00Z", "kb_unverified": None},
    {"id": 7, "ts": "2026-10-01T11:20:00Z", "agent": "user", "stage_id": "S05", "severity": "minor",
     "title": "Vizsgálatcímke ellenőrzése: " + XSS, "detail": "A címke HTML-nek látszik — szövegként kell megjelennie.",
     "evidence": None, "kb_refs": None, "status": "open", "resolution": None, "resolved_ts": None, "kb_unverified": None},
    {"id": 12, "ts": "2026-10-03T14:02:00Z", "agent": "reviewer", "stage_id": "S04", "severity": "blocker",
     "title": "P007: a kizárási okok összege ≠ H", "detail": None, "evidence": "02_szures/prisma_flow.json",
     "kb_refs": "P007", "status": "open", "resolution": None, "resolved_ts": None, "kb_unverified": None},
    {"id": 14, "ts": "2026-10-04T08:30:00Z", "agent": "evaluator", "stage_id": "S09", "severity": "info",
     "title": "k = 13 < 10? nem — a torzítás-tesztek értelmezhetők", "detail": None, "evidence": None,
     "kb_refs": "V016", "status": "open", "resolution": None, "resolved_ts": None, "kb_unverified": None},
    {"id": 15, "ts": "2026-10-04T19:40:00Z", "agent": "reviewer", "stage_id": "S05", "severity": "blocker",
     "title": "SE/SD csere gyanú (Hart & Sutherland 1977)", "detail": None, "evidence": None,
     "kb_refs": "V011 D-S07-004", "status": "open", "resolution": None, "resolved_ts": None, "kb_unverified": None},
]
DECISIONS = [
    {"id": 1, "ts": "2026-09-20T10:05:00Z", "agent": "planner", "stage_id": "S01", "status": "active",
     "decision": "Elsődleges kimenet: TBC-incidencia (RR), véletlen hatású modell REML + HKSJ",
     "rationale": "Klinikai és módszertani sokféleség várható (1948–1980, eltérő szélességi körök).",
     "alternatives": "közös hatású modell (érzékenységi elemzésként)", "kb_refs": "D-S08-001,D-S02-006", "supersedes": None,
     "kb_unverified": None},
    {"id": 2, "ts": "2026-10-02T16:40:00Z", "agent": "user", "stage_id": "S05", "status": "active",
     "decision": "V023 „Nem hiba”: a Hart & Sutherland 1977 n1 = 13,598 ezres tagolás",
     "rationale": "A közlemény 2. táblázatában ezres tagolással: 13 598 fő.", "alternatives": None, "kb_refs": "V023",
     "supersedes": None, "kb_unverified": None},
    {"id": 3, "ts": "2026-10-03T09:00:00Z", "agent": "user", "stage_id": "S04", "status": "active",
     "decision": "Vizsgálatcímke rögzítve: " + XSS, "rationale": "XSS-próba: a címke szövegként jelenik meg.",
     "alternatives": None, "kb_refs": None, "supersedes": None, "kb_unverified": None},
]
CHECKPOINTS = [
    {"id": 1, "ts": "2026-09-20T10:00:00Z", "stage_id": "S00", "agent": "planner", "verdict": "PASS", "summary": "Kérdés és PICO rögzítve"},
    {"id": 2, "ts": "2026-09-21T10:00:00Z", "stage_id": "S01", "agent": "planner", "verdict": "PASS", "summary": "Protokoll kész"},
    {"id": 3, "ts": "2026-09-24T10:00:00Z", "stage_id": "S02", "agent": "reviewer", "verdict": "PASS", "summary": None},
    {"id": 4, "ts": "2026-09-28T10:00:00Z", "stage_id": "S03", "agent": "reviewer", "verdict": "PASS_WITH_FIXES", "summary": "#3 javítva"},
    {"id": 5, "ts": "2026-10-03T14:05:00Z", "stage_id": "S04", "agent": "reviewer", "verdict": "FAIL", "summary": "P007 (#12)"},
]
GRADES = [{"id": 1, "ts": "2026-10-04T22:00:00Z", "outcome": "o1", "k": 13, "participants": 357347,
           "effect": "RR 0,49 [0,33; 0,73]", "risk_of_bias": "−1 serious: 41% súly magas RoB-ú vizsgálatokból",
           "inconsistency": "−1 serious: I² 92%, a PI átlépi a nullát", "indirectness": "0 not serious",
           "imprecision": "0 not serious", "publication_bias": "0 undetected (döntés #9)", "upgrades": None,
           "certainty": "low", "rationale": "Kétszeres leminősítés (RoB, inkonzisztencia).", "kb_refs": "D-S13-001",
           "kb_unverified": None}]
RUNS = [{"id": 2, "ts": "2026-10-04T21:12:00Z",
         "command": "ma.py analyze --spec 05_elemzes/specs/o1_primary.json --out 05_elemzes/o1/20261004T211200Z-a1f3c2 --project .",
         "data_path": "03_adatok/o1.csv", "data_sha256": "9f3a" + "b" * 60, "outdir": "05_elemzes/o1/20261004T211200Z-a1f3c2",
         "engine_version": "0.2.0", "summary": "k=13, RR 0.49 [0.33; 0.73]"},
        {"id": 1, "ts": "2026-10-03T18:02:00Z",
         "command": "ma.py analyze --spec 05_elemzes/specs/o1_primary.json --out 05_elemzes/o1/20261003T180200Z-4c0d1e --project .",
         "data_path": "03_adatok/o1.csv", "data_sha256": "4c0d" + "c" * 60, "outdir": "05_elemzes/o1/20261003T180200Z-4c0d1e",
         "engine_version": "0.2.0", "summary": "k=13, RR 0.50 [0.34; 0.74]"}]
ACTIVITY = [
    {"schema": "szk.ma.activity/v1", "seq": 410, "ts": "2026-10-04T21:10:00Z", "actor": "user:SzK", "action": "table.save",
     "argv": None, "inputs": {}, "outputs": {"03_adatok/o1.csv": "9f3a" + "b" * 60}, "result": {"exit_code": 0, "summary": "13 sor"},
     "prev": "aa" * 32},
    {"schema": "szk.ma.activity/v1", "seq": 411, "ts": "2026-10-04T21:12:00Z", "actor": "user:SzK", "action": "analyze.commit",
     "argv": ["ma.py", "analyze", "--spec", "05_elemzes/specs/o1_primary.json", "--out", "05_elemzes/o1/20261004T211200Z-a1f3c2"],
     "inputs": {"03_adatok/o1.csv": "9f3a" + "b" * 60}, "outputs": {}, "result": {"exit_code": 0, "summary": "k=13, RR 0.49 [0.33; 0.73]"},
     "prev": "bb" * 32},
    {"schema": "szk.ma.activity/v1", "seq": 412, "ts": "2026-10-04T21:15:00Z", "actor": "external", "action": "file.external_change",
     "argv": None, "inputs": {}, "outputs": {"03_adatok/o1.csv": "9f3a" + "b" * 60}, "result": None, "prev": "cc" * 32},
]


def gate_message():
    """A motor valódi elutasító üzenete nyitott blocker mellett (ideiglenes projektben)."""
    with tempfile.TemporaryDirectory() as d:
        projekt.init(d, "BCG")
        projekt.add_finding(d, "reviewer", "blocker", "SE/SD csere gyanú (Hart & Sutherland 1977)", stage="S05")
        try:
            projekt.checkpoint(d, "S05", "user", "PASS")
        except ValueError as e:
            return str(e).replace("#1 ", "#15 ")
    raise RuntimeError("a motor nem utasította el a PASS-t")


def log_fixtures():
    msg = gate_message()
    return {
        "log_decision.json": {"description": "GET /api/log/decision — projekt.list_items('decisions') sorai (3.5.15).",
                              "routes": [{"method": "GET", "path": "/api/log/decision", "envelope": env("szk.ma.log/v1", {"kind": "decision", "items": DECISIONS})}]},
        "log_grade.json": {"description": "GET /api/log/grade — projekt.list_items('grades') (3.5.15).",
                           "routes": [{"method": "GET", "path": "/api/log/grade", "envelope": env("szk.ma.log/v1", {"kind": "grade", "items": GRADES})}]},
        "log_run.json": {"description": "GET /api/log/run — projekt.list_items('runs') (3.5.15).",
                         "routes": [{"method": "GET", "path": "/api/log/run", "envelope": env("szk.ma.log/v1", {"kind": "run", "items": RUNS})}]},
        "log_activity.json": {"description": "GET /api/log/activity — hash-láncolt tevékenységnapló + láncellenőrzés (4.16; ma_gui/routes/log.py).",
                              "routes": [{"method": "GET", "path": "/api/log/activity", "envelope": env("szk.ma.activity-log/v1", {
                                  "kind": "activity", "items": ACTIVITY, "total": 412,
                                  "verify": {"ok": True, "first_bad_seq": None, "message": None}, "head": "dd" * 32})}]},
        "log_write.json": {"description": "POST /api/log/{finding,resolve,checkpoint} — szk.ma.log-write/v1; a GATE_BLOCKED üzenet a "
                                          "motor (projekt.checkpoint) valódi szövege (4.2, 6.6).",
                           "routes": [
                               {"method": "POST", "path": "/api/log/finding", "envelope": env("szk.ma.log-write/v1", {"kind": "finding", "id": 16, "warnings": []})},
                               {"method": "POST", "path": "/api/log/resolve", "envelope": env("szk.ma.log-write/v1", {"kind": "resolve", "id": 7, "warnings": []})},
                               {"method": "POST", "path": "/api/log/checkpoint", "envelope": env("szk.ma.log-write/v1", {"kind": "checkpoint", "id": 6, "warnings": []})},
                               {"method": "POST", "path": "/api/log/checkpoint", "body": {"stage": "S05", "verdict": "PASS"}, "status": 409,
                                "envelope": err("GATE_BLOCKED", 409, msg, {"blockers": [{"id": 15, "stage": "S05", "title": "SE/SD csere gyanú (Hart & Sutherland 1977)", "status": "open"}],
                                                                       "stage": "S05", "verdict": "PASS"})}]},
    }


def finding_fixture():
    blockers = [f for f in FINDINGS if f["severity"] == "blocker" and f["status"] == "open"]
    return {"description": "GET /api/log/finding — projekt.list_items('findings') sorai (3.5.1, 3.5.15); a szűrt változat a "
                            "nyitott blockereké.",
            "routes": [{"method": "GET", "path": "/api/log/finding", "envelope": env("szk.ma.log/v1", {"kind": "finding", "items": FINDINGS})},
                       {"method": "GET", "path": "/api/log/finding", "query": {"status": "open", "severity": "blocker"},
                        "envelope": env("szk.ma.log/v1", {"kind": "finding", "items": blockers})}]}


# ---------------------------------------------------------------- KB (3.5.15, 7.7)
KB_IDS = ["V006", "V011", "V023", "P006", "P007", "P011", "D-S04-009", "D-S07-004", "D-S13-001", "K-SIM24-059"]
CHUNK_REF = "cochrane_handbook#412"


def kb_item(i):
    it = kb.show(i)
    if it is None:
        return None
    it = dict(it)
    table = it.pop("_table", None)
    return {"id": i, "table": table, "item": it, "local_only": table == "chunk", "note": NOTE}


def kb_fixture():
    routes = []
    q = "predikciós intervallum"
    for sc in ("rule", "knowledge"):
        res = kb.search(q, 6, None, (sc,))
        routes.append({"method": "GET", "path": "/api/kb/search", "query": {"scope": sc},
                       "envelope": env("szk.ma.kb-search/v1", {"query": q, "scopes": [sc], "results": res, "local_only": True, "note": NOTE})})
    chunk = [{"id": 9412, "ref": CHUNK_REF, "source_id": "cochrane_handbook", "seq": 412, "locator": "10.10.4.3",
              "snippet": "… a [predikciós intervallum] (SZINTETIKUS minta a fejlesztői fixture-ben — a jogvédett teljes "
                         "szöveg nem kerül a repóba) … <script>alert(1)</script> …", "score": -7.1}]
    routes.append({"method": "GET", "path": "/api/kb/search", "query": {"scope": "chunk"},
                   "envelope": env("szk.ma.kb-search/v1", {"query": q, "scopes": ["chunk"], "results": {"chunk": chunk}, "local_only": True, "note": NOTE})})
    routes.append({"method": "GET", "path": "/api/kb/search", "query": {"q": "nincs-ilyen-kifejezes"},
                   "envelope": env("szk.ma.kb-search/v1", {"query": "nincs-ilyen-kifejezes", "scopes": ["rule"], "results": {"rule": []}, "local_only": True, "note": NOTE})})
    for i in KB_IDS:
        d = kb_item(i)
        if d is None:
            raise RuntimeError("nincs ilyen KB-tétel: %s" % i)
        routes.append({"method": "GET", "path": "/api/kb/item/" + i, "envelope": env("szk.ma.kb-item/v1", d)})
    routes.append({"method": "GET", "path": "/api/kb/item/cochrane_handbook%23412", "envelope": env("szk.ma.kb-item/v1", {
        "id": CHUNK_REF, "table": "chunk", "local_only": True, "note": NOTE,
        "item": {"chunk_id": 9412, "source_id": "cochrane_handbook", "seq": 412, "locator": "10.10.4.3", "ref": CHUNK_REF,
                 "text": "SZINTETIKUS szövegrész a fejlesztői fixture-ben. A valódi teljes szöveg jogvédett, csak a "
                         "felhasználó gépén olvasható (tudasbazis.sqlite), és soha nem kerül exportba vagy a repóba."}})})
    for i in ("X001", "X005", "X010"):
        routes.append({"method": "GET", "path": "/api/kb/item/" + i, "status": 404,
                       "envelope": err("NOT_FOUND", 404, "Nincs ilyen azonosító a tudásbázisban.")})
    return {"description": "GET /api/kb/search (rule/knowledge: a valódi kb.search; chunk: SZINTETIKUS) és GET /api/kb/item/<id> "
                           "(a valódi kb.show; X-szabály még nincs a KB-ban → 404) — 3.5.15, „Miért?”.",
            "routes": routes}


# ---------------------------------------------------------------- export (3.5.17)
def export_fixture():
    files = [{"path": p, "sha256": h * 64, "bytes": b, "role": r} for p, h, b, r in (
        ("manifest.json", "1", 2310, "manifest"), ("07_ellenorzes/activity.jsonl", "2", 88412, "activity"),
        ("projekt/naplo.md", "3", 14022, "log"), ("projekt/naplo.json", "4", 30110, "log"),
        ("05_elemzes/specs/o1_primary.json", "5", 1204, "spec"), ("05_elemzes/o1/20261004T211200Z-a1f3c2/run.json", "6", 1730, "run"),
        ("02_szures/prisma_flow.json", "7", 612, "prisma"), ("03_adatok/studies.json", "8", 3301, "studies"),
        ("rerun.cmd", "9", 640, "rerun"), ("rerun.sh", "a", 702, "rerun"))]
    red = [{"hu": "értékelők → monogram", "en": "assessors → initials"}, {"hu": "eredet-idézetek ki", "en": "provenance quotes removed"},
           {"hu": "abszolút utak ki", "en": "absolute paths removed"}]
    exc = [{"pattern": "_privat/**", "reason": {"hu": "C osztály — soha", "en": "class C — never"}},
           {"pattern": "**/*.pdf", "reason": {"hu": "D osztály — csak doc-id + oldal + sha256", "en": "class D — doc id + page + sha256 only"}},
           {"pattern": "03_adatok/*.csv", "reason": {"hu": "adattábla kikapcsolva", "en": "data tables switched off"}}]
    audit = {"path": "07_ellenorzes/audit/2026-10-05/bcg-oltas-audit.zip", "sha256": "e3b4" + "7" * 60, "bytes": 48213,
             "deterministic": True,
             "manifest": {"schema": "szk.ma.audit-bundle/v1", "project": "bcg-oltas", "created": "2026-10-05T09:00:00Z",
                          "data_class": "A", "redactions": red, "files": files, "excluded": exc, "activity_head": "dd" * 32,
                          "rerun_scripts": ["rerun.cmd", "rerun.sh"], "kb_snapshot": {"ids": ["D-S08-001", "P007"], "db_sha256": "f0" * 32}}}
    snap = {"path": "07_ellenorzes/pillanatkep/bcg-oltas-2026-10-05.snapshot.html", "sha256": "9c1d" + "e" * 60, "bytes": 512330,
            "redactions": red, "excluded": exc[:2], "network": False}
    return {"description": "POST /api/export/audit és /api/export/snapshot — audit-ZIP (4.17) és kitakaró pillanatkép (7.6).",
            "routes": [{"method": "POST", "path": "/api/export/audit", "envelope": env("szk.ma.export-result/v1", audit)},
                       {"method": "POST", "path": "/api/export/snapshot", "envelope": env("szk.ma.export-result/v1", snap)}]}


# ---------------------------------------------------------------- képességek (3.5.16) — a Caps.report() valódi alakja
def _cap(i, label, state, mode, version, handshake, **kw):
    c = {"id": i, "plugin": i, "label": label, "state": state, "mode": mode, "version": version, "handshake": handshake,
         "home": None, "source": None, "python_version": None, "commands": [], "features": [], "contracts": [], "drift": [],
         "guards": [], "known_issues": [], "missing_modules": [], "problems": [], "todo": None, "note": None, "elapsed_ms": 3}
    c.update(kw)
    return c


def caps_doc():
    from ma_gui import caps as capsmod
    comps = [
        _cap("metaelemzes", {"hu": "motor", "en": "engine"}, "ok", "api", "0.2.0", "ok", source="builtin", python_version="3.11.9",
             features=["analyze", "validate", "convert", "prisma", "project", "kb"],
             contracts=[{"name": "szk.ma.validation/v1", "version": 1, "dir": ["out"], "sha256": "a1" * 32, "local": "match", "ok": True},
                        {"name": "szk.ma.plot/v2", "version": 2, "dir": ["out"], "sha256": "a2" * 32, "local": "match", "ok": True}]),
        _cap("validator", "validator", "legacy", "bridge", "1.0.0", "missing", source="marketplace", python_version="3.11.9",
             guards=["H1", "H2", "H3", "H4"],
             problems=[{"code": "no_handshake", "level": "warning", "hu": "Nincs --capabilities: verzióhoz rögzített bridge-adapter (V1 kell a JSON-szerződéshez)",
                        "en": "No --capabilities: version-pinned bridge adapter (V1 needed for the JSON contract)"}],
             known_issues=[{"id": "H3", "summary": "GRADE publication bias 'suspected' unresolved", "fixed_in": "1.1.0"}]),
        _cap("figure-forge", "figure-forge", "unusable", None, "0.2.1", "failed", source="marketplace", missing_modules=["matplotlib"],
             problems=[{"code": "missing_modules", "level": "error", "hu": "matplotlib hiányzik", "en": "matplotlib missing"}],
             todo={"hu": "Állítsd be a FIGURE_FORGE_PYTHON-t egy matplotlibes értelmezőre.", "en": "Set FIGURE_FORGE_PYTHON to an interpreter with matplotlib."}),
        _cap("composer", "composer", "ok", "flow-json", "1.4.1", "ok", source="marketplace", python_version="3.11.9",
             features=["prisma"], contracts=[{"name": "szk.prisma-flow/v1", "version": 1, "dir": ["out"], "sha256": "c1" * 32, "local": "missing", "ok": True}],
             note={"hu": "shebang: explicit interpreterrel hívva", "en": "shebang: called with an explicit interpreter"}),
        _cap("presubmit", "presubmit", "absent", None, None, "not_run",
             todo={"hu": "Telepítés: claude plugin install presubmit@szk-plugins — vagy add meg a plugin mappáját az SZK_PRESUBMIT_HOME környezeti változóban.",
                   "en": "Install: claude plugin install presubmit@szk-plugins — or set SZK_PRESUBMIT_HOME to the plugin folder."}),
    ]
    plugins = {c["id"]: c for c in comps[1:]}
    matrix = capsmod.build_matrix(plugins)
    return {"schema": "szk.ma.capabilities-matrix/v1", "generated": "2026-10-04T21:00:00Z", "probing": False,
            "components": comps, "matrix": matrix, "problems": []}


def caps_fixture():
    d = caps_doc()
    return {"description": "GET /api/capabilities, POST /api/capabilities/refresh — ma_gui.caps.Caps.report() alakja "
                           "(components[] + matrix (caps.build_matrix) + problems); állapotok 4.1: ok | unusable | legacy | absent.",
            "routes": [{"method": "GET", "path": "/api/capabilities", "envelope": env("szk.ma.capabilities-matrix/v1", d)},
                       {"method": "POST", "path": "/api/capabilities/refresh", "envelope": env("szk.ma.capabilities-matrix/v1", dict(d, generated="2026-10-05T09:30:00Z"))}]}


# ---------------------------------------------------------------- írás / ellenőrzés
def all_fixtures():
    out = {"prisma.json": prisma_fixture(), "studies.json": studies_fixture(), "kb_process.json": kb_fixture(),
           "export.json": export_fixture(), "capabilities.json": caps_fixture(), "log_finding.json": finding_fixture()}
    out.update(log_fixtures())
    return out


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    _rid[0] = 0
    bad = []
    for name, obj in all_fixtures().items():
        path = FIX / name
        text = dump(obj)
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                bad.append(name)
        else:
            path.write_text(text, encoding="utf-8")
            print("írva: %s (%d bájt)" % (path.relative_to(ROOT), len(text.encode("utf-8"))))
    if bad:
        print("ELAVULT fixture: %s — futtasd: python3 tests/gui/ui/gen_process_fixtures.py" % ", ".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
