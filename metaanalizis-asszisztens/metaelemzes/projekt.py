# -*- coding: utf-8 -*-
"""Projektnapló (SQLite): döntések, ellenőrzési megállapítások, ellenőrzőpontok, GRADE-értékelések
és futtatások — az ágensek ide írnak, így minden döntés visszakereshető és a tudásbázis
szabályaira hivatkozik (kb_refs).
"""
import datetime
import hashlib
import json
import os
import shutil
import sqlite3

from .kb import KB_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS project (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS decision (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, agent TEXT NOT NULL, stage_id TEXT,
    decision TEXT NOT NULL, rationale TEXT, alternatives TEXT, kb_refs TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded','reverted')),
    supersedes INTEGER REFERENCES decision(id)
);
CREATE TABLE IF NOT EXISTS finding (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, agent TEXT NOT NULL, stage_id TEXT,
    severity TEXT NOT NULL CHECK (severity IN ('blocker','major','minor','info')),
    title TEXT NOT NULL, detail TEXT, evidence TEXT, kb_refs TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','fixed','wontfix','invalid')),
    resolution TEXT, resolved_ts TEXT
);
CREATE TABLE IF NOT EXISTS checkpoint (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, stage_id TEXT NOT NULL, agent TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('PASS','PASS_WITH_FIXES','FAIL')), summary TEXT
);
CREATE TABLE IF NOT EXISTS grade (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, outcome TEXT NOT NULL, k INTEGER, participants INTEGER,
    effect TEXT, risk_of_bias TEXT, inconsistency TEXT, indirectness TEXT, imprecision TEXT,
    publication_bias TEXT, upgrades TEXT,
    certainty TEXT NOT NULL CHECK (certainty IN ('high','moderate','low','very low')),
    rationale TEXT, kb_refs TEXT
);
CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, command TEXT, data_path TEXT, data_sha256 TEXT,
    outdir TEXT, engine_version TEXT, summary TEXT
);
"""

FOLDERS = ["00_protokoll", "01_kereses", "02_szures", "03_adatok", "04_torzitas_kockazat",
           "05_elemzes", "06_kezirat", "07_ellenorzes"]

SEVERITIES = ("blocker", "major", "minor", "info")


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def db_path(project_dir):
    return os.path.join(project_dir, "projekt.sqlite")


def connect(project_dir):
    p = db_path(project_dir)
    if not os.path.exists(p):
        raise FileNotFoundError("Nincs projektnapló: %s (futtasd: project init)" % p)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init(project_dir, title, question=None):
    os.makedirs(project_dir, exist_ok=True)
    for f in FOLDERS:
        os.makedirs(os.path.join(project_dir, f), exist_ok=True)
    con = sqlite3.connect(db_path(project_dir))
    con.executescript(SCHEMA)
    for k, v in (("title", title), ("question", question or ""), ("created", _now())):
        con.execute("INSERT OR IGNORE INTO project (key, value) VALUES (?, ?)", (k, v))
    con.commit()
    con.close()
    tdir = os.path.join(KB_DIR, "sablonok")
    copied = []
    targets = {"protokoll_sablon.md": "00_protokoll/protokoll.md",
               "kereses_naplo_sablon.md": "01_kereses/kereses_naplo.md",
               "prisma_folyamat_sablon.md": "02_szures/prisma_folyamat.md",
               "adatkinyero_sablon.csv": "03_adatok/adatkinyeres.csv",
               "rob_tabla_sablon.csv": "04_torzitas_kockazat/rob.csv",
               "grade_sof_sablon.md": "06_kezirat/grade_sof.md"}
    for src, dst in targets.items():
        sp, dp = os.path.join(tdir, src), os.path.join(project_dir, dst)
        if os.path.exists(sp) and not os.path.exists(dp):
            shutil.copyfile(sp, dp)
            copied.append(dst)
    return copied


def log_decision(project_dir, agent, decision, rationale=None, stage=None, kb_refs=None,
                 alternatives=None, supersedes=None):
    con = connect(project_dir)
    cur = con.execute("INSERT INTO decision (ts, agent, stage_id, decision, rationale, alternatives, kb_refs, supersedes) "
                      "VALUES (?,?,?,?,?,?,?,?)", (_now(), agent, stage, decision, rationale, alternatives,
                                                   kb_refs, supersedes))
    if supersedes:
        con.execute("UPDATE decision SET status='superseded' WHERE id=?", (supersedes,))
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def add_finding(project_dir, agent, severity, title, detail=None, stage=None, evidence=None, kb_refs=None):
    if severity not in SEVERITIES:
        raise ValueError("súlyosság: %s" % ", ".join(SEVERITIES))
    con = connect(project_dir)
    cur = con.execute("INSERT INTO finding (ts, agent, stage_id, severity, title, detail, evidence, kb_refs) "
                      "VALUES (?,?,?,?,?,?,?,?)", (_now(), agent, stage, severity, title, detail, evidence, kb_refs))
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def resolve_finding(project_dir, finding_id, status, resolution):
    con = connect(project_dir)
    n = con.execute("UPDATE finding SET status=?, resolution=?, resolved_ts=? WHERE id=?",
                    (status, resolution, _now(), finding_id)).rowcount
    con.commit()
    con.close()
    if not n:
        raise ValueError("nincs ilyen megállapítás: %s" % finding_id)


def checkpoint(project_dir, stage, agent, verdict, summary=None):
    con = connect(project_dir)
    if verdict in ("PASS", "PASS_WITH_FIXES"):
        blockers = con.execute("SELECT COUNT(*) FROM finding WHERE status='open' AND severity='blocker' "
                               "AND (stage_id=? OR stage_id IS NULL)", (stage,)).fetchone()[0]
        if blockers:
            con.close()
            raise ValueError("%d nyitott 'blocker' megállapítás van ennél a szakasznál — PASS nem adható." % blockers)
    cur = con.execute("INSERT INTO checkpoint (ts, stage_id, agent, verdict, summary) VALUES (?,?,?,?,?)",
                      (_now(), stage, agent, verdict, summary))
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def add_grade(project_dir, outcome, certainty, **kw):
    cols = ["k", "participants", "effect", "risk_of_bias", "inconsistency", "indirectness", "imprecision",
            "publication_bias", "upgrades", "rationale", "kb_refs"]
    con = connect(project_dir)
    cur = con.execute("INSERT INTO grade (ts, outcome, certainty, %s) VALUES (?,?,?,%s)" % (
        ",".join(cols), ",".join("?" * len(cols))), [_now(), outcome, certainty] + [kw.get(c) for c in cols])
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def log_run(project_dir, command, data_path, outdir, engine_version, summary=None):
    digest = None
    if data_path and os.path.exists(data_path):
        with open(data_path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
    con = connect(project_dir)
    con.execute("INSERT INTO run (ts, command, data_path, data_sha256, outdir, engine_version, summary) "
                "VALUES (?,?,?,?,?,?,?)", (_now(), command, data_path, digest, outdir, engine_version,
                                          json.dumps(summary, ensure_ascii=False) if summary is not None else None))
    con.commit()
    con.close()
    return digest


def status(project_dir):
    con = connect(project_dir)
    info = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM project")}
    out = {
        "project": info,
        "open_findings": [dict(r) for r in con.execute(
            "SELECT id, severity, stage_id, title, agent, ts FROM finding WHERE status='open' "
            "ORDER BY CASE severity WHEN 'blocker' THEN 0 WHEN 'major' THEN 1 WHEN 'minor' THEN 2 ELSE 3 END, id")],
        "checkpoints": [dict(r) for r in con.execute(
            "SELECT stage_id, verdict, agent, ts FROM checkpoint c WHERE id = "
            "(SELECT MAX(id) FROM checkpoint WHERE stage_id = c.stage_id) ORDER BY stage_id")],
        "decisions": con.execute("SELECT COUNT(*) FROM decision WHERE status='active'").fetchone()[0],
        "runs": [dict(r) for r in con.execute("SELECT id, ts, command, data_sha256, outdir FROM run ORDER BY id DESC LIMIT 5")],
        "grade": [dict(r) for r in con.execute("SELECT outcome, certainty, ts FROM grade ORDER BY id")],
    }
    con.close()
    return out


def export_markdown(project_dir):
    """Döntési napló és ellenőrzési nyomvonal Markdownban (kiegészítő anyaghoz)."""
    con = connect(project_dir)
    info = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM project")}
    L = ["# Döntési és ellenőrzési napló — %s" % info.get("title", ""), ""]
    if info.get("question"):
        L += ["**Kérdés:** %s" % info["question"], ""]
    L += ["## Döntések", "", "| # | Idő | Ágens | Szakasz | Döntés | Indoklás | KB | Állapot |", "|---|---|---|---|---|---|---|---|"]
    for r in con.execute("SELECT * FROM decision ORDER BY id"):
        L.append("| %d | %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], r["ts"], r["agent"], r["stage_id"] or "", _cell(r["decision"]), _cell(r["rationale"]),
            r["kb_refs"] or "", r["status"]))
    L += ["", "## Ellenőrzési megállapítások", "", "| # | Súlyosság | Szakasz | Cím | Állapot | Megoldás |", "|---|---|---|---|---|---|"]
    for r in con.execute("SELECT * FROM finding ORDER BY id"):
        L.append("| %d | %s | %s | %s | %s | %s |" % (r["id"], r["severity"], r["stage_id"] or "", _cell(r["title"]),
                                                    r["status"], _cell(r["resolution"])))
    L += ["", "## Ellenőrzőpontok", "", "| Szakasz | Ítélet | Ágens | Idő | Összegzés |", "|---|---|---|---|---|"]
    for r in con.execute("SELECT * FROM checkpoint ORDER BY id"):
        L.append("| %s | %s | %s | %s | %s |" % (r["stage_id"], r["verdict"], r["agent"], r["ts"], _cell(r["summary"])))
    L += ["", "## GRADE", "", "| Kimenet | k | Bizonyosság | Indoklás |", "|---|---|---|---|"]
    for r in con.execute("SELECT * FROM grade ORDER BY id"):
        L.append("| %s | %s | %s | %s |" % (_cell(r["outcome"]), r["k"] or "", r["certainty"], _cell(r["rationale"])))
    L += ["", "## Futtatások", "", "| # | Idő | Parancs | Adat SHA-256 | Kimenet |", "|---|---|---|---|---|"]
    for r in con.execute("SELECT * FROM run ORDER BY id"):
        L.append("| %d | %s | `%s` | `%s` | %s |" % (r["id"], r["ts"], _cell(r["command"]), (r["data_sha256"] or "")[:16], r["outdir"] or ""))
    con.close()
    return "\n".join(L) + "\n"


def _cell(s):
    return (s or "").replace("|", "/").replace("\n", " ")
