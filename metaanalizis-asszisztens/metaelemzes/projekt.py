# -*- coding: utf-8 -*-
"""Projektnapló (SQLite): döntések, ellenőrzési megállapítások, ellenőrzőpontok, GRADE-értékelések
és futtatások — az ágensek ide írnak, így minden döntés visszakereshető és a tudásbázis
szabályaira hivatkozik (kb_refs).

Kapu (gate): PASS / PASS_WITH_FIXES nem rögzíthető, amíg az adott szakaszra (vagy szakasz nélkül)
nyitott 'blocker' megállapítás van; a záró, FINAL ellenőrzőpontnál bármely szakasz nyitott blockere
elég az elutasításhoz. A szakaszkód csak S00–S14, tartomány (pl. S01-S02 → S01, S02) vagy FINAL lehet.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3

from .kb import KB_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS project (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS decision (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, agent TEXT NOT NULL, stage_id TEXT,
    decision TEXT NOT NULL, rationale TEXT, alternatives TEXT, kb_refs TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded','reverted')),
    supersedes INTEGER REFERENCES decision(id), kb_unverified TEXT
);
CREATE TABLE IF NOT EXISTS finding (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, agent TEXT NOT NULL, stage_id TEXT,
    severity TEXT NOT NULL CHECK (severity IN ('blocker','major','minor','info')),
    title TEXT NOT NULL, detail TEXT, evidence TEXT, kb_refs TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','fixed','wontfix','invalid')),
    resolution TEXT, resolved_ts TEXT, kb_unverified TEXT
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
    rationale TEXT, kb_refs TEXT, kb_unverified TEXT
);
CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, command TEXT, data_path TEXT, data_sha256 TEXT,
    outdir TEXT, engine_version TEXT, summary TEXT
);
"""

# régebbi projektnaplók bővítése (ALTER TABLE ADD COLUMN)
_ADDED_COLUMNS = {"decision": ["kb_unverified TEXT"], "finding": ["kb_unverified TEXT"],
                  "grade": ["kb_unverified TEXT"]}

FOLDERS = ["00_protokoll", "01_kereses", "02_szures", "03_adatok", "04_torzitas_kockazat",
           "05_elemzes", "06_kezirat", "07_ellenorzes"]

SEVERITIES = ("blocker", "major", "minor", "info")
VERDICTS = ("PASS", "PASS_WITH_FIXES", "FAIL")
STAGES = tuple("S%02d" % i for i in range(15))
FINAL = "FINAL"
KNOWN_AGENTS = ("planner", "reviewer", "evaluator", "orchestrator", "engine", "user")


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def db_path(project_dir):
    return os.path.join(project_dir, "projekt.sqlite")


def _upgrade(con):
    for table, cols in _ADDED_COLUMNS.items():
        have = {r[1] for r in con.execute("PRAGMA table_info(%s)" % table)}
        if not have:
            continue
        for c in cols:
            if c.split()[0] not in have:
                con.execute("ALTER TABLE %s ADD COLUMN %s" % (table, c))
    con.commit()


def connect(project_dir):
    p = db_path(project_dir)
    if not os.path.exists(p):
        raise FileNotFoundError("Nincs projektnapló: %s (futtasd: project init)" % p)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    _upgrade(con)
    return con


def init(project_dir, title, question=None):
    os.makedirs(project_dir, exist_ok=True)
    for f in FOLDERS:
        os.makedirs(os.path.join(project_dir, f), exist_ok=True)
    con = sqlite3.connect(db_path(project_dir))
    con.executescript(SCHEMA)
    _upgrade(con)
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


# ------------------------------------------------------------------ szakaszkódok
def _one_stage(tok, orig):
    m = re.fullmatch(r"S?(\d{1,2})", tok)
    if not m or not 0 <= int(m.group(1)) <= 14:
        raise ValueError("Érvénytelen szakaszkód: %r — S00–S14 (pl. S05), tartomány (pl. S01-S02) vagy FINAL "
                         "adható meg." % orig)
    return int(m.group(1))


def parse_stage(stage, allow_final=True):
    """Szakaszkód(ok) ellenőrzése és normalizálása → a lefedett szakaszok rendezett listája.
    'S05', 's5', 'S05 ' → ['S05']; 'S01–S02', 'S07-S12' → kibontva (S07, S08, …, S12);
    vesszős lista ('S01,S03') is; 'FINAL' → ['FINAL']; None/üres → []. Más érték: ValueError."""
    if stage is None:
        return []
    s = str(stage).strip().upper()
    if not s:
        return []
    if s == FINAL:
        if not allow_final:
            raise ValueError("A FINAL itt nem adható meg.")
        return [FINAL]
    out = set()
    for part in s.split(","):
        part = part.strip()
        ends = [x.strip() for x in re.split(r"\s*(?:-|–|—|\.\.)\s*", part)]
        if len(ends) == 1:
            out.add(_one_stage(ends[0], stage))
        elif len(ends) == 2:
            a, b = _one_stage(ends[0], stage), _one_stage(ends[1], stage)
            if a > b:
                raise ValueError("Érvénytelen szakasz-tartomány: %r (a kezdet nagyobb a végnél)" % stage)
            out.update(range(a, b + 1))
        else:
            raise ValueError("Érvénytelen szakaszkód: %r" % stage)
    return ["S%02d" % i for i in sorted(out)]


def _stage_value(stage):
    """Tárolási alak: egy szakasz 'S05'; tartomány kibontva 'S07,S08,…'; FINAL; None."""
    st = parse_stage(stage)
    return ",".join(st) if st else None


def _stored_stages(value):
    """Tárolt szakaszérték → lefedett szakaszok; None: szakasz nélküli (mindenhol érvényes);
    érvénytelen régi címke → None (óvatosan: mindenhol blokkol)."""
    if value is None or not str(value).strip():
        return None
    try:
        return parse_stage(value)
    except ValueError:
        return None


# ------------------------------------------------------------------ ellenőrzések
def _check_agent(agent, warnings):
    if agent not in KNOWN_AGENTS and warnings is not None:
        warnings.append("Ismeretlen ágensnév: %r (ismert: %s) — a napló így rögzíti." % (agent, ", ".join(KNOWN_AGENTS)))


def check_kb_refs(kb_refs, kb_db=None):
    """KB-hivatkozások ellenőrzése a tudásbázisban → (normalizált szöveg, ismeretlen azonosítók, hiba).
    Elválasztó: vessző, pontosvessző, '|' vagy szóköz. Ha a tudásbázis nem érhető el, minden
    azonosító ellenőrizetlen, és a harmadik elem a hiba leírása."""
    if kb_refs is None:
        return None, [], None
    ids = []
    for x in re.split(r"[,;|\s]+", str(kb_refs)):
        if x.strip() and x.strip() not in ids:
            ids.append(x.strip())
    if not ids:
        return None, [], None
    try:
        from . import kb
        known = kb.existing_ids(ids, kb_db)
    except Exception as exc:   # a napló a tudásbázis hibája esetén is használható maradjon
        return ",".join(ids), ids, "a tudásbázis nem érhető el (%s: %s)" % (type(exc).__name__, exc)
    return ",".join(ids), [i for i in ids if i not in known], None


def _prepare(agent, stage, kb_refs, kb_db, strict, warnings, check_kb=True):
    stage_value = _stage_value(stage)
    if agent is not None:
        _check_agent(agent, warnings)
    refs, unknown, err = (kb_refs, [], None)
    if check_kb:
        refs, unknown, err = check_kb_refs(kb_refs, kb_db)
    if unknown:
        msg = ("Ismeretlen tudásbázis-azonosító(k): %s — ellenőrizd: kb show <ID> / kb search; kitalált "
               "azonosítót ne adj meg." % ", ".join(unknown)) if err is None else \
              ("A KB-hivatkozások nem ellenőrizhetők (%s): %s" % (err, ", ".join(unknown)))
        if strict:
            raise ValueError(msg)
        if warnings is not None:
            warnings.append(msg + " (ellenőrizetlenként rögzítve)")
    return stage_value, refs, (",".join(unknown) or None)


def log_decision(project_dir, agent, decision, rationale=None, stage=None, kb_refs=None,
                 alternatives=None, supersedes=None, kb_db=None, strict=False, warnings=None, check_kb=True):
    """Döntés naplózása. strict=True: ismeretlen KB-azonosító → ValueError; különben figyelmeztetés
    (warnings listába) és a kb_unverified oszlopban jelölve."""
    con = connect(project_dir)
    try:
        stage_value, refs, unverified = _prepare(agent, stage, kb_refs, kb_db, strict, warnings, check_kb)
        cur = con.execute("INSERT INTO decision (ts, agent, stage_id, decision, rationale, alternatives, kb_refs, "
                          "supersedes, kb_unverified) VALUES (?,?,?,?,?,?,?,?,?)",
                          (_now(), agent, stage_value, decision, rationale, alternatives, refs, supersedes, unverified))
        if supersedes:
            con.execute("UPDATE decision SET status='superseded' WHERE id=?", (supersedes,))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def add_finding(project_dir, agent, severity, title, detail=None, stage=None, evidence=None, kb_refs=None,
                kb_db=None, strict=False, warnings=None, check_kb=True):
    if severity not in SEVERITIES:
        raise ValueError("súlyosság: %s" % ", ".join(SEVERITIES))
    con = connect(project_dir)
    try:
        stage_value, refs, unverified = _prepare(agent, stage, kb_refs, kb_db, strict, warnings, check_kb)
        cur = con.execute("INSERT INTO finding (ts, agent, stage_id, severity, title, detail, evidence, kb_refs, "
                          "kb_unverified) VALUES (?,?,?,?,?,?,?,?,?)",
                          (_now(), agent, stage_value, severity, title, detail, evidence, refs, unverified))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def resolve_finding(project_dir, finding_id, status, resolution):
    con = connect(project_dir)
    n = con.execute("UPDATE finding SET status=?, resolution=?, resolved_ts=? WHERE id=?",
                    (status, resolution, _now(), finding_id)).rowcount
    con.commit()
    con.close()
    if not n:
        raise ValueError("nincs ilyen megállapítás: %s" % finding_id)


def open_blockers(con, stages):
    """A megadott szakaszok PASS-át akadályozó nyitott blockerek. FINAL: bármely szakasz blockere;
    egyébként a szakasz nélküliek, az érintett szakaszra (vagy azt lefedő tartományra) rögzítettek,
    és a régi, érvénytelen szakaszcímkéjűek."""
    rows = con.execute("SELECT id, stage_id, title FROM finding WHERE status='open' AND severity='blocker' "
                       "ORDER BY id").fetchall()
    if FINAL in stages:
        return rows
    want = set(stages)
    return [r for r in rows if _stored_stages(r["stage_id"]) is None or want & set(_stored_stages(r["stage_id"]))]


def checkpoint(project_dir, stage, agent, verdict, summary=None, warnings=None):
    """Ellenőrzőpont rögzítése. Tartomány (pl. S01-S02) szakaszonként külön sort kap.
    Visszaad: az (utolsó) beszúrt sor azonosítója."""
    stages = parse_stage(stage)
    if not stages:
        raise ValueError("Az ellenőrzőponthoz szakaszkód kell (S00–S14, tartomány vagy FINAL).")
    if verdict not in VERDICTS:
        raise ValueError("ítélet: %s" % ", ".join(VERDICTS))
    _check_agent(agent, warnings)
    con = connect(project_dir)
    try:
        if verdict in ("PASS", "PASS_WITH_FIXES"):
            blockers = open_blockers(con, stages)
            if blockers:
                where = ("a záró (FINAL) ellenőrzőponthoz egyetlen szakaszban sem lehet nyitott blocker"
                         if FINAL in stages else "ennél a szakasznál (%s)" % ", ".join(stages))
                raise ValueError("%d nyitott 'blocker' megállapítás van — %s; %s nem adható. Nyitott: %s" % (
                    len(blockers), where, verdict, "; ".join(
                        "#%d [%s] %s" % (r["id"], r["stage_id"] or "–", r["title"]) for r in blockers)))
        rid = None
        for st in stages:
            rid = con.execute("INSERT INTO checkpoint (ts, stage_id, agent, verdict, summary) VALUES (?,?,?,?,?)",
                              (_now(), st, agent, verdict, summary)).lastrowid
        con.commit()
        return rid
    finally:
        con.close()


def add_grade(project_dir, outcome, certainty, kb_db=None, strict=False, warnings=None, check_kb=True, **kw):
    cols = ["k", "participants", "effect", "risk_of_bias", "inconsistency", "indirectness", "imprecision",
            "publication_bias", "upgrades", "rationale", "kb_refs", "kb_unverified"]
    con = connect(project_dir)
    try:
        _, refs, unverified = _prepare(None, None, kw.get("kb_refs"), kb_db, strict, warnings, check_kb)
        kw = dict(kw, kb_refs=refs, kb_unverified=unverified)
        cur = con.execute("INSERT INTO grade (ts, outcome, certainty, %s) VALUES (?,?,?,%s)" % (
            ",".join(cols), ",".join("?" * len(cols))), [_now(), outcome, certainty] + [kw.get(c) for c in cols])
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


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


# ------------------------------------------------------------------ lekérdezés
_ITEM_TABLES = {"finding": "finding", "decision": "decision", "checkpoint": "checkpoint", "grade": "grade",
                "run": "run"}
_LIST_KINDS = {"findings": "finding", "decisions": "decision", "checkpoints": "checkpoint", "grades": "grade",
               "runs": "run"}


def get_item(project_dir, kind, item_id):
    """Egy napló-tétel minden mezője (pl. megállapítás részletei, bizonyítéka és megoldása)."""
    table = _ITEM_TABLES.get(kind)
    if table is None:
        raise ValueError("ismeretlen típus: %s (%s)" % (kind, ", ".join(_ITEM_TABLES)))
    con = connect(project_dir)
    try:
        row = con.execute("SELECT * FROM %s WHERE id=?" % table, (item_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError("nincs ilyen %s: #%s" % (kind, item_id))
    return dict(row)


def list_items(project_dir, kind, status=None, severity=None, stage=None):
    """Napló-tételek listája szűrőkkel. findings: status = open | fixed | wontfix | invalid | resolved
    (= nem nyitott); severity; stage (a tartományra rögzítetteket is mutatja)."""
    table = _LIST_KINDS.get(kind, _ITEM_TABLES.get(kind))
    if table is None:
        raise ValueError("ismeretlen típus: %s (%s)" % (kind, ", ".join(_LIST_KINDS)))
    sql, args = "SELECT * FROM %s WHERE 1=1" % table, []
    if status:
        if table not in ("finding", "decision"):
            raise ValueError("--status csak findings / decisions listánál adható meg")
        if status == "resolved" and table == "finding":
            sql += " AND status <> 'open'"
        else:
            sql += " AND status = ?"
            args.append(status)
    if severity:
        if table != "finding":
            raise ValueError("--severity csak findings listánál adható meg")
        sql += " AND severity = ?"
        args.append(severity)
    con = connect(project_dir)
    try:
        rows = [dict(r) for r in con.execute(sql + " ORDER BY id", args)]
    finally:
        con.close()
    if stage and table in ("finding", "decision", "checkpoint"):
        want = set(parse_stage(stage))
        rows = [r for r in rows if _stored_stages(r.get("stage_id")) is None
                or want & set(_stored_stages(r.get("stage_id")))]
    return rows


def _log_warnings(con):
    """Állapotjelentéshez: ismeretlen ágensnevek, ellenőrizetlen KB-hivatkozások, érvénytelen régi
    szakaszcímkék és nyitott blocker mellett rögzített (régi) PASS."""
    out = []
    agents = {}
    for table in ("decision", "finding", "checkpoint"):
        for r in con.execute("SELECT id, agent FROM %s ORDER BY id" % table):
            if r["agent"] not in KNOWN_AGENTS:
                agents.setdefault(r["agent"], []).append("%s #%d" % (table, r["id"]))
    for a, where in agents.items():
        out.append("Ismeretlen ágensnév a naplóban: %r (%s); ismert: %s" % (a, ", ".join(where[:5]), ", ".join(KNOWN_AGENTS)))
    for table in ("decision", "finding", "grade"):
        for r in con.execute("SELECT id, kb_unverified FROM %s WHERE kb_unverified IS NOT NULL ORDER BY id" % table):
            out.append("Ellenőrizetlen KB-hivatkozás: %s #%d: %s" % (table, r["id"], r["kb_unverified"]))
    for table in ("decision", "finding", "checkpoint"):
        for r in con.execute("SELECT id, stage_id FROM %s WHERE stage_id IS NOT NULL ORDER BY id" % table):
            if _stored_stages(r["stage_id"]) is None:
                out.append("Érvénytelen szakaszkód a naplóban: %s #%d: %r" % (table, r["id"], r["stage_id"]))
    for r in con.execute("SELECT stage_id, verdict, id FROM checkpoint c WHERE id = (SELECT MAX(id) FROM checkpoint "
                         "WHERE stage_id = c.stage_id) AND verdict <> 'FAIL' ORDER BY stage_id"):
        st = _stored_stages(r["stage_id"])
        if st is None:
            continue
        bl = open_blockers(con, st)
        if bl:
            out.append("%s: az utolsó ellenőrzőpont %s, de nyitott blocker van: %s" % (
                r["stage_id"], r["verdict"], ", ".join("#%d" % b["id"] for b in bl)))
    return out


def status(project_dir):
    con = connect(project_dir)
    info = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM project")}
    out = {
        "project": info,
        "open_findings": [dict(r) for r in con.execute(
            "SELECT id, severity, stage_id, title, agent, ts FROM finding WHERE status='open' "
            "ORDER BY CASE severity WHEN 'blocker' THEN 0 WHEN 'major' THEN 1 WHEN 'minor' THEN 2 ELSE 3 END, id")],
        "findings_by_status": {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM finding GROUP BY status ORDER BY status")},
        "checkpoints": [dict(r) for r in con.execute(
            "SELECT stage_id, verdict, agent, ts FROM checkpoint c WHERE id = "
            "(SELECT MAX(id) FROM checkpoint WHERE stage_id = c.stage_id) "
            "ORDER BY CASE stage_id WHEN 'FINAL' THEN 1 ELSE 0 END, stage_id")],
        "decisions": con.execute("SELECT COUNT(*) FROM decision WHERE status='active'").fetchone()[0],
        "runs": [dict(r) for r in con.execute("SELECT id, ts, command, data_sha256, outdir FROM run ORDER BY id DESC LIMIT 5")],
        "grade": [dict(r) for r in con.execute("SELECT outcome, certainty, ts FROM grade ORDER BY id")],
        "warnings": _log_warnings(con),
    }
    con.close()
    return out


# ------------------------------------------------------------------ export
def _cell(s):
    """Markdown-táblázatcella: egy sor (CR/LF → szóköz), a '|' escape-elve."""
    s = "" if s is None else str(s)
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return s.replace("|", "\\|")


def _code(s):
    """Inline kód cellában: a kerítés hosszabb a benne lévő leghosszabb backtick-sorozatnál."""
    s = _cell(s)
    if not s:
        return ""
    fence = "`" * (max([len(m) for m in re.findall("`+", s)] or [0]) + 1)
    pad = " " if s.startswith("`") or s.endswith("`") else ""
    return "%s%s%s%s%s" % (fence, pad, s, pad, fence)


def _kb_cell(refs, unverified):
    out = _cell(refs)
    if unverified:
        out += " (ellenőrizetlen: %s)" % _cell(unverified)
    return out


def export_markdown(project_dir):
    """Döntési napló és ellenőrzési nyomvonal Markdownban (kiegészítő anyaghoz)."""
    con = connect(project_dir)
    info = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM project")}
    L = ["# Döntési és ellenőrzési napló — %s" % _cell(info.get("title", "")), ""]
    if info.get("question"):
        L += ["**Kérdés:** %s" % _cell(info["question"]), ""]
    L += ["## Döntések", "", "| # | Idő | Ágens | Szakasz | Döntés | Indoklás | KB | Állapot |", "|---|---|---|---|---|---|---|---|"]
    for r in con.execute("SELECT * FROM decision ORDER BY id"):
        L.append("| %d | %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], _cell(r["ts"]), _cell(r["agent"]), _cell(r["stage_id"]), _cell(r["decision"]),
            _cell(r["rationale"]), _kb_cell(r["kb_refs"], r["kb_unverified"]), _cell(r["status"])))
    L += ["", "## Ellenőrzési megállapítások", "",
          "| # | Súlyosság | Szakasz | Cím | Részletek | Bizonyíték | KB | Állapot | Megoldás |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in con.execute("SELECT * FROM finding ORDER BY id"):
        L.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], _cell(r["severity"]), _cell(r["stage_id"]), _cell(r["title"]), _cell(r["detail"]),
            _cell(r["evidence"]), _kb_cell(r["kb_refs"], r["kb_unverified"]), _cell(r["status"]),
            _cell(r["resolution"])))
    L += ["", "## Ellenőrzőpontok", "", "| Szakasz | Ítélet | Ágens | Idő | Összegzés |", "|---|---|---|---|---|"]
    for r in con.execute("SELECT * FROM checkpoint ORDER BY id"):
        L.append("| %s | %s | %s | %s | %s |" % (_cell(r["stage_id"]), _cell(r["verdict"]), _cell(r["agent"]),
                                                 _cell(r["ts"]), _cell(r["summary"])))
    L += ["", "## GRADE", "",
          "| Kimenet | k | Résztvevők | Hatás | Torzítás | Inkonzisztencia | Indirektség | Pontatlanság | "
          "Publikációs torzítás | Felminősítés | Bizonyosság | Indoklás | KB |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in con.execute("SELECT * FROM grade ORDER BY id"):
        L.append("| %s |" % " | ".join(_cell(x) for x in (
            r["outcome"], r["k"], r["participants"], r["effect"], r["risk_of_bias"], r["inconsistency"],
            r["indirectness"], r["imprecision"], r["publication_bias"], r["upgrades"], r["certainty"],
            r["rationale"])) + " %s |" % _kb_cell(r["kb_refs"], r["kb_unverified"]))
    L += ["", "## Futtatások", "", "| # | Idő | Parancs | Adat SHA-256 | Kimenet |", "|---|---|---|---|---|"]
    for r in con.execute("SELECT * FROM run ORDER BY id"):
        h = (r["data_sha256"] or "")[:16]
        L.append("| %d | %s | %s | %s | %s |" % (r["id"], _cell(r["ts"]), _code(r["command"]),
                                                 ("`%s`" % h) if h else "", _cell(r["outdir"])))
    con.close()
    return "\n".join(L) + "\n"
