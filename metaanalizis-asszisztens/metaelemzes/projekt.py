# -*- coding: utf-8 -*-
"""Projektnapló (SQLite): döntések, ellenőrzési megállapítások, ellenőrzőpontok, GRADE-értékelések
és futtatások — az ágensek ide írnak, így minden döntés visszakereshető és a tudásbázis
szabályaira hivatkozik (kb_refs).

Kapu (gate): PASS / PASS_WITH_FIXES nem rögzíthető, amíg az adott szakaszra (vagy szakasz nélkül)
nyitott 'blocker' megállapítás van; a záró, FINAL ellenőrzőpontnál bármely szakasz nyitott blockere
elég az elutasításhoz. Blocker csak fixed vagy indokolt invalid státusszal zárható (a régi naplóban
wontfix-szel lezárt blocker továbbra is blokkol); a megállapítás újranyitható (open).
A szakaszkód csak S00–S14, tartomány (pl. S01-S02 → S01, S02) vagy FINAL lehet.

Szereplő (E7): minden író függvény opcionális actor kulcsszót kap (pl. 'user:SzK', 'agent:reviewer');
az 'actor' oszlopba kerül (alapból NULL), a megállapítás lezárójáé/újranyitójáé a 'resolved_actor'-ba.
A lekérdezők (status, get_item, list_items, export_json) JSON-képes értékeket adnak (NaN/végtelen → None).
A ma-projekt.json (szk.ma.project/v1) a load_project_meta / save_project_meta párral olvasható és írható.

Döntés-kontextus: a döntés gépi kontextusa (context) JSON-objektumként a 'context' oszlopba kerül (pl. a „Nem
hiba — indoklás” döntésnél {kind: 'validation', dataset, row_uid, code, fields}); csak azonosítók, kódok és
mezőnevek — cellaérték soha (check_context). A lekérdezők objektumként adják vissza.
"""
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from pathlib import Path

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

# régebbi projektnaplók bővítése (ALTER TABLE ADD COLUMN); az új napló is így kapja meg
_ADDED_COLUMNS = {"decision": ["kb_unverified TEXT", "actor TEXT", "context TEXT"],
                  "finding": ["kb_unverified TEXT", "actor TEXT", "resolved_actor TEXT"],
                  "checkpoint": ["actor TEXT"],
                  "grade": ["kb_unverified TEXT", "actor TEXT"],
                  "run": ["actor TEXT"]}

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
                try:
                    con.execute("ALTER TABLE %s ADD COLUMN %s" % (table, c))
                except sqlite3.OperationalError as exc:
                    msg = str(exc).lower()
                    if "duplicate column" in msg:       # egy másik folyamat épp most bővítette
                        continue
                    if "readonly" in msg or "read-only" in msg:
                        con.rollback()                  # csak olvasható napló: bővítés nélkül olvasható
                        return
                    raise
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
_MAX_ACTOR = 200


def check_actor(actor):
    """A szereplő (actor) ellenőrzése → a normalizált szöveg vagy None. Érvényes: nem üres, legfeljebb
    200 karakteres, vezérlőkarakter nélküli szöveg (pl. 'user:SzK', 'agent:reviewer', 'cli')."""
    if actor is None:
        return None
    if not isinstance(actor, str) or not actor.strip() or len(actor) > _MAX_ACTOR or \
            any(ord(c) < 32 or ord(c) == 127 for c in actor):
        raise ValueError("Érvénytelen szereplő (actor): nem üres, legfeljebb %d karakteres, vezérlőkarakter nélküli "
                         "szöveg kell (pl. 'user:SzK', 'agent:reviewer')." % _MAX_ACTOR)
    return actor.strip()


# a döntés gépi kontextusa (a munkapad POST /api/log/decision context-sémájával azonos kulcsok)
CONTEXT_KINDS = ("validation", "analysis", "prisma", "other")
_CONTEXT_KEYS = ("kind", "dataset", "row_uid", "code", "fields", "run_id", "spec", "outcome", "changes")
# cellaérték nem kerülhet a naplóba (terv 7.4; ugyanaz a tiltólista, mint az activity-láncé)
_CONTEXT_FORBIDDEN = frozenset({"value", "values", "cell", "cells", "rows", "value_as_entered"})
_CONTEXT_MAX = 8192
_CONTEXT_ROW_UID = re.compile(r"^r[0-9a-z]{4,12}$")
_CONTEXT_CODE = re.compile(r"^[VPX][0-9]{3}$")


def _context_forbidden(obj, path="context"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).strip().lower() in _CONTEXT_FORBIDDEN:
                return "%s.%s" % (path, k)
            hit = _context_forbidden(v, "%s.%s" % (path, k))
            if hit:
                return hit
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hit = _context_forbidden(v, "%s[%d]" % (path, i))
            if hit:
                return hit
    return None


def _short_text(v, where, limit=1024):
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > limit or any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise ValueError("Érvénytelen döntés-kontextus: a(z) %s legfeljebb %d karakteres, vezérlőkarakter nélküli "
                         "szöveg legyen." % (where, limit))
    return v


def check_context(context):
    """A döntés gépi kontextusának ellenőrzése → kanonikus JSON-szöveg (rendezett kulcsok) vagy None.

    Elfogadott kulcsok: kind (validation | analysis | prisma | other), dataset, row_uid, code (V/P/X + 3 jegy),
    fields (mezőnevek), run_id, spec, outcome, changes ([{key, before, after}] — elemzési opcióértékek). A null
    értékű kulcs kimarad. Cellaérték (value, values, cell, cells, rows, value_as_entered) sehol nem lehet benne
    (adatvédelem, terv 7.4); legfeljebb 8 KB. Hibás kontextus: ValueError (magyar üzenettel)."""
    if context is None:
        return None
    if not isinstance(context, dict):
        raise ValueError("Érvénytelen döntés-kontextus: JSON-objektum kell (pl. {\"kind\": \"validation\", "
                         "\"code\": \"V013\", \"row_uid\": \"r7f3a2\", \"fields\": [\"m1\"]}).")
    hit = _context_forbidden(context)
    if hit:
        raise ValueError("A döntés-kontextusba nem kerülhet cellaérték (%s): csak azonosítók, kódok és mezőnevek "
                         "(adatvédelem)." % hit)
    ctx = {k: v for k, v in context.items() if v is not None}
    unknown = sorted(set(ctx) - set(_CONTEXT_KEYS))
    if unknown:
        raise ValueError("Ismeretlen kulcs a döntés-kontextusban: %s (elfogadott: %s)."
                         % (", ".join(map(str, unknown)), ", ".join(_CONTEXT_KEYS)))
    if "kind" in ctx and ctx["kind"] not in CONTEXT_KINDS:
        raise ValueError("Érvénytelen döntés-kontextus: kind = %r (lehetséges: %s)."
                         % (ctx["kind"], ", ".join(CONTEXT_KINDS)))
    if "row_uid" in ctx and not (isinstance(ctx["row_uid"], str) and _CONTEXT_ROW_UID.match(ctx["row_uid"])):
        raise ValueError("Érvénytelen döntés-kontextus: a row_uid alakja 'r' + 4–12 kisbetű/számjegy "
                         "(pl. r7f3a2), kapott: %r." % (ctx["row_uid"],))
    if "code" in ctx and not (isinstance(ctx["code"], str) and _CONTEXT_CODE.match(ctx["code"])):
        raise ValueError("Érvénytelen döntés-kontextus: a code egy szabálykód (pl. V013, X001), kapott: %r."
                         % (ctx["code"],))
    for key in ("dataset", "run_id", "spec", "outcome"):
        if key in ctx:
            _short_text(ctx[key], key)
    if "fields" in ctx:
        f = ctx["fields"]
        if not isinstance(f, list) or len(f) > 50 or not all(isinstance(x, str) and 0 < len(x) <= 100 for x in f):
            raise ValueError("Érvénytelen döntés-kontextus: a fields mezőnevek listája (legfeljebb 50, egyenként "
                             "legfeljebb 100 karakter).")
    if "changes" in ctx:
        ch = ctx["changes"]
        if not isinstance(ch, list) or len(ch) > 100 or not all(
                isinstance(c, dict) and set(c) <= {"key", "before", "after"} and isinstance(c.get("key"), str)
                for c in ch):
            raise ValueError("Érvénytelen döntés-kontextus: a changes [{key, before, after}] lista (legfeljebb 100).")
    try:
        text = json.dumps(ctx, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("Érvénytelen döntés-kontextus: csak JSON-értékek (szöveg, véges szám, logikai, lista, "
                         "objektum) lehetnek benne.")
    if len(text.encode("utf-8")) > _CONTEXT_MAX:
        raise ValueError("A döntés-kontextus túl nagy (legfeljebb %d bájt)." % _CONTEXT_MAX)
    return text


def _check_agent(agent, warnings):
    if agent not in KNOWN_AGENTS and warnings is not None:
        warnings.append("Ismeretlen ágensnév: %r (ismert: %s) — a napló így rögzíti." % (agent, ", ".join(KNOWN_AGENTS)))


def check_kb_refs(kb_refs, kb_db=None, notes=None):
    """KB-hivatkozások ellenőrzése a tudásbázisban → (normalizált szöveg, ismeretlen azonosítók, hiba).
    Elválasztó: vessző, pontosvessző, '|' vagy szóköz. A szövegrész-azonosító (kb search '#<chunk_id>')
    a stabil '<forrás>#<sorszám>' alakban kerül a naplóba (notes listába: a csere leírása). Ha a
    tudásbázis nem érhető el, minden azonosító ellenőrizetlen, és a harmadik elem a hiba leírása."""
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
        canon = kb.canonical_ids(ids, kb_db)
    except Exception as exc:   # a napló a tudásbázis hibája esetén is használható maradjon
        return ",".join(ids), ids, "a tudásbázis nem érhető el (%s: %s)" % (type(exc).__name__, exc)
    refs = []
    for i in ids:
        r = canon.get(i) or i
        if r != i and notes is not None:
            notes.append("Szövegrész-hivatkozás stabil alakban rögzítve: %s → %s (a sorszám-azonosító a kb ingest "
                         "után mást jelölhet)" % (i, r))
        if r not in refs:
            refs.append(r)
    return ",".join(refs), [i for i in ids if canon.get(i) is None], None


def _prepare(agent, stage, kb_refs, kb_db, strict, warnings, check_kb=True):
    stage_value = _stage_value(stage)
    if agent is not None:
        _check_agent(agent, warnings)
    refs, unknown, err = (kb_refs, [], None)
    if check_kb:
        refs, unknown, err = check_kb_refs(kb_refs, kb_db, warnings)
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
                 alternatives=None, supersedes=None, kb_db=None, strict=False, warnings=None, check_kb=True,
                 actor=None, context=None):
    """Döntés naplózása. strict=True: ismeretlen KB-azonosító → ValueError; különben figyelmeztetés
    (warnings listába) és a kb_unverified oszlopban jelölve. context: a döntés gépi kontextusa (dict; lásd
    check_context) — a 'context' oszlopba JSON-szövegként kerül."""
    actor = check_actor(actor)
    ctx_text = check_context(context)
    con = connect(project_dir)
    try:
        stage_value, refs, unverified = _prepare(agent, stage, kb_refs, kb_db, strict, warnings, check_kb)
        cur = con.execute("INSERT INTO decision (ts, agent, stage_id, decision, rationale, alternatives, kb_refs, "
                          "supersedes, kb_unverified, actor, context) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (_now(), agent, stage_value, decision, rationale, alternatives, refs, supersedes, unverified,
                           actor, ctx_text))
        if supersedes:
            con.execute("UPDATE decision SET status='superseded' WHERE id=?", (supersedes,))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def add_finding(project_dir, agent, severity, title, detail=None, stage=None, evidence=None, kb_refs=None,
                kb_db=None, strict=False, warnings=None, check_kb=True, actor=None):
    if severity not in SEVERITIES:
        raise ValueError("súlyosság: %s" % ", ".join(SEVERITIES))
    actor = check_actor(actor)
    con = connect(project_dir)
    try:
        stage_value, refs, unverified = _prepare(agent, stage, kb_refs, kb_db, strict, warnings, check_kb)
        cur = con.execute("INSERT INTO finding (ts, agent, stage_id, severity, title, detail, evidence, kb_refs, "
                          "kb_unverified, actor) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (_now(), agent, stage_value, severity, title, detail, evidence, refs, unverified, actor))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


RESOLVE_STATUSES = ("fixed", "wontfix", "invalid", "open")


def resolve_finding(project_dir, finding_id, status, resolution, actor=None):
    """Megállapítás lezárása (fixed | wontfix | invalid) vagy újranyitása (open). Blocker csak fixed vagy
    indokolt invalid státusszal zárható. Újranyitáskor a korábbi lezárás a megoldás szövegében megmarad.
    actor: a lezáró / újranyitó szereplő (resolved_actor oszlop)."""
    if status not in RESOLVE_STATUSES:
        raise ValueError("státusz: %s" % ", ".join(RESOLVE_STATUSES))
    actor = check_actor(actor)
    con = connect(project_dir)
    try:
        row = con.execute("SELECT severity, status, resolution FROM finding WHERE id=?", (finding_id,)).fetchone()
        if row is None:
            raise ValueError("nincs ilyen megállapítás: %s" % finding_id)
        if row["severity"] == "blocker" and status == "wontfix":
            raise ValueError("A #%s blocker: csak fixed vagy indokolt invalid státusszal zárható, wontfix-szel nem — "
                             "javítatlan blocker mellett a szakasz nem kaphat PASS-t." % finding_id)
        if row["severity"] == "blocker" and status == "invalid" and not (resolution or "").strip():
            raise ValueError("A #%s blocker invalid státuszú lezárásához indoklás kell." % finding_id)
        if status == "open":
            if row["status"] == "open":
                raise ValueError("a #%s megállapítás már nyitott" % finding_id)
            con.execute("UPDATE finding SET status='open', resolution=?, resolved_ts=NULL, resolved_actor=? WHERE id=?",
                        ("újranyitva: %s (korábban %s: %s)" % (resolution, row["status"], row["resolution"] or "–"),
                         actor, finding_id))
        else:
            con.execute("UPDATE finding SET status=?, resolution=?, resolved_ts=?, resolved_actor=? WHERE id=?",
                        (status, resolution, _now(), actor, finding_id))
        con.commit()
    finally:
        con.close()


def open_blockers(con, stages):
    """A megadott szakaszok PASS-át akadályozó blockerek: a nyitottak és a (régi naplóban) wontfix-szel
    lezártak. FINAL: bármely szakasz blockere; egyébként a szakasz nélküliek, az érintett szakaszra (vagy
    azt lefedő tartományra) rögzítettek, és a régi, érvénytelen szakaszcímkéjűek."""
    rows = con.execute("SELECT id, stage_id, title, status FROM finding WHERE status IN ('open','wontfix') "
                       "AND severity='blocker' ORDER BY id").fetchall()
    if FINAL in stages:
        return rows
    want = set(stages)
    return [r for r in rows if _stored_stages(r["stage_id"]) is None or want & set(_stored_stages(r["stage_id"]))]


def checkpoint(project_dir, stage, agent, verdict, summary=None, warnings=None, actor=None, audit_gate=False):
    """Ellenőrzőpont rögzítése. Tartomány (pl. S01-S02) szakaszonként külön sort kap.
    audit_gate=True (csak FINAL): az error szintű X-szabály találatok (project audit) is kizárják a PASS-t.
    Visszaad: az (utolsó) beszúrt sor azonosítója."""
    stages = parse_stage(stage)
    if not stages:
        raise ValueError("Az ellenőrzőponthoz szakaszkód kell (S00–S14, tartomány vagy FINAL).")
    if verdict not in VERDICTS:
        raise ValueError("ítélet: %s" % ", ".join(VERDICTS))
    actor = check_actor(actor)
    if audit_gate and FINAL not in stages:
        raise ValueError("Az audit-kapu (--audit-gate) csak a záró, FINAL ellenőrzőponthoz adható meg.")
    _check_agent(agent, warnings)
    con = connect(project_dir)
    try:
        if verdict in ("PASS", "PASS_WITH_FIXES"):
            if audit_gate:
                from . import audit
                audit.require_gate(project_dir, warnings)
            blockers = open_blockers(con, stages)
            if blockers:
                where = ("a záró (FINAL) ellenőrzőponthoz egyetlen szakaszban sem lehet nyitott blocker"
                         if FINAL in stages else "ennél a szakasznál (%s)" % ", ".join(stages))
                raise ValueError("%d nyitott 'blocker' megállapítás van — %s; %s nem adható. Nyitott: %s" % (
                    len(blockers), where, verdict, "; ".join(
                        "#%d [%s] %s%s" % (r["id"], r["stage_id"] or "–", r["title"],
                                           " (wontfix — blocker így nem zárható)" if r["status"] == "wontfix" else "")
                        for r in blockers)))
            if not audit_gate:
                # szakaszkapu (6.4): pl. X009 — lezáratlan kettős kinyerés mellett az S08 (és későbbi) PASS tilos
                from . import audit
                audit.require_stage_gate(project_dir, stages)
        if FINAL in stages and verdict != "FAIL" and warnings is not None:
            unverified = ["%s #%d: %s" % (t, r["id"], r["kb_unverified"]) for t, cond in (
                ("decision", " AND status='active'"), ("finding", ""), ("grade", "")) for r in con.execute(
                "SELECT id, kb_unverified FROM %s WHERE kb_unverified IS NOT NULL%s ORDER BY id" % (t, cond))]
            if unverified:
                warnings.append("A záró ellenőrzőpontnál ellenőrizetlen KB-hivatkozás maradt (a ma-ellenorzo szerint "
                                "blocker; javítás: új döntés --supersedes-szel, --kb … --strict): %s" % "; ".join(unverified))
        rid = None
        for st in stages:
            rid = con.execute("INSERT INTO checkpoint (ts, stage_id, agent, verdict, summary, actor) VALUES (?,?,?,?,?,?)",
                              (_now(), st, agent, verdict, summary, actor)).lastrowid
        con.commit()
        return rid
    finally:
        con.close()


_GRADE_LEVELS = ("very low", "low", "moderate", "high")
_GRADE_DOWN = ("risk_of_bias", "inconsistency", "indirectness", "imprecision", "publication_bias")
_SIGNED = re.compile(r"^\s*([+\-\u2212\u2013])\s*([0-3])(?![0-9.,])")
_ZERO = re.compile(r"^\s*0(?![0-9.,])")
# „nincs” jelentésű szöveg ('nincs', 'nincs felminősítés', 'none', '–', 'n/a'): 0 lépés
_NONE = re.compile(r"^\s*(?:[-\u2013\u2014\u2212]+|n/?a|none|no(?: upgrades?)?|not applicable|nem alkalmazható|"
                   r"nincs(?:en)?(?: (?:fel|le)minősítés)?)\s*(?:$|[.,;:(])", re.I)


def _grade_step(text):
    """A domén-szöveg elején álló előjeles lépés (pl. '−1 súlyos' → -1, '+1 nagy hatás' → +1, '0' → 0,
    'nincs' / 'none' / '–' → 0); None, ha nincs ilyen (szabad szöveg: nem találgatunk)."""
    if text is None:
        return None
    m = _SIGNED.match(str(text))
    if m:
        return (1 if m.group(1) == "+" else -1) * int(m.group(2))
    return 0 if (_ZERO.match(str(text)) or _NONE.match(str(text))) else None


def grade_consistency(certainty, **domains):
    """A GRADE-bizonyosság és a domén-lépések összhangja. A kiindulás magas (RCT, ROBINS-I) vagy
    alacsony (megfigyeléses); L: a leminősítések összege, F: a felminősítéseké ('very low'–'high' közé
    vágva). Ha mind az öt leminősítési domén előjeles lépés (és a felminősítés is az, vagy nincs megadva),
    csak a két kiindulásból elérhető szint fogadható el: magas − L + F vagy alacsony − L + F. Egyébként
    a [alacsony − L + F, magas − L + F] tartomány, ahol a szabad szövegű (nem pontozott) domén lefelé, a
    szabad szövegű felminősítés felfelé nyitja a tartományt (nem találgatunk); a meg nem adott és a „nincs”
    jelentésű ('nincs', 'none', '–', 'n/a') domén 0.
    Ellentmondásnál figyelmeztető szöveg, különben None."""
    if certainty not in _GRADE_LEVELS:
        return None
    steps = {d: _grade_step(domains.get(d)) for d in _GRADE_DOWN + ("upgrades",)}
    if all(v is None for v in steps.values()):
        return None
    if (steps["upgrades"] or 0) < 0:
        return ("A felminősítés nem lehet negatív (%r): a leminősítést a megfelelő doménnél add meg."
                % domains.get("upgrades"))

    def given(d):
        return domains.get(d) is not None and str(domains.get(d)).strip() != ""

    unscored = [d for d in _GRADE_DOWN if given(d) and steps[d] is None]
    up_unknown = given("upgrades") and steps["upgrades"] is None
    down = sum(abs(steps[d]) for d in _GRADE_DOWN if steps[d] is not None)
    up = steps["upgrades"] or 0
    c = _GRADE_LEVELS.index(certainty) + 1
    rct, obs = max(1, min(4, 4 - down + up)), max(1, min(4, 2 - down + up))
    if all(steps[d] is not None for d in _GRADE_DOWN) and not up_unknown:
        if c in (rct, obs):
            return None
        rng = ("csak '%s'" % _GRADE_LEVELS[rct - 1]) if rct == obs else \
            ("'%s' (RCT/ROBINS-I kiindulás) vagy '%s' (megfigyeléses kiindulás)" % (
                _GRADE_LEVELS[rct - 1], _GRADE_LEVELS[obs - 1]))
    else:
        hi = 4 if up_unknown else rct
        lo = 1 if unscored else obs
        if lo <= c <= hi:
            return None
        rng = ("csak '%s'" % _GRADE_LEVELS[lo - 1]) if lo == hi else \
            ("'%s'–'%s'" % (_GRADE_LEVELS[lo - 1], _GRADE_LEVELS[hi - 1]))
    return ("A bizonyosság ('%s') nem egyeztethető össze a megadott lépésekkel (leminősítés összesen %s, "
            "felminősítés összesen %s): a kiindulástól (RCT: magas; megfigyeléses: alacsony) függően %s lehet. "
            "Ellenőrizd a domének értékét vagy a végső ítéletet."
            % (certainty, ("−%d" % down) if down else "0", ("+%d" % up) if up else "0", rng))


# publikációs torzítás (11. döntés, 4. pont): a „suspected” (gyanított) előjeles lépés nélkül FELOLDATLAN — a rögzítés
# tiltott, amíg ember 0-t vagy −1-et nem választ indoklással; a „strongly suspected” −1 (kézzel, indoklással −2)
_PB_STRONG = re.compile(r"^\s*(?:strongly\s+suspected|er[őo]sen\s+gyan[ií]t(?:ott|hat[óo]))", re.I)
_PB_SUSPECTED = re.compile(r"^\s*(?:suspected|gyan[ií]tott|gyan[ií]that[óo])", re.I)
_PB_WORDS = re.compile(r"(?i)\b(?:strongly|suspected|er[őo]sen|gyan[ií]tott|gyan[ií]that[óo]|resolved|feloldva|"
                       r"feloldott)\b")


def publication_bias_check(text):
    """A publikációs torzítás domén-szövegének ellenőrzése a 11/4. döntés szerint → (hiba, figyelmeztetés);
    mindkettő szöveg vagy None. Hiba: a „suspected” / „gyanított” előjeles lépés nélkül (feloldatlan), vagy
    előjeles lépéssel, de indoklás nélkül. Figyelmeztetés: „strongly suspected” lépés nélkül (−1 az alapérték)."""
    if text is None or not str(text).strip():
        return None, None
    t = str(text)
    step = _grade_step(t)
    if step is None:
        if _PB_STRONG.match(t):
            return None, ("Publikációs torzítás: az „erősen gyanított” (strongly suspected) ítélet −1 lépés; írd elé "
                          "az előjeles lépést (pl. \"−1 erősen gyanított: …\"), különben a bizonyosság-ellenőrzés nem "
                          "tudja beszámítani.")
        if _PB_SUSPECTED.match(t):
            return ("Publikációs torzítás: a „gyanított” (suspected) ítélet feloldatlan — a rögzítés tiltott, amíg nem "
                    "választasz 0-t vagy −1-et indoklással (11. döntés, 4. pont; X019). Példa: "
                    "--publication-bias \"0 gyanított (feloldva): <miért nem minősítesz le>\" vagy "
                    "\"−1 gyanított: <a konkrét jelek>\"."), None
        return None, None
    m = _SIGNED.match(t) or _ZERO.match(t)
    rest = t[m.end():] if m else t
    label = rest.lstrip(" :;,.-\u2013\u2014(")
    strong = _PB_STRONG.match(label)
    if (strong and step == -2) or (not strong and _PB_SUSPECTED.match(label)):
        if len(re.findall(r"\w", _PB_WORDS.sub(" ", rest))) < 3:
            return ("Publikációs torzítás: %s indoklás kell (pl. \"%s %s: <a jelek és a döntés oka>\")." % (
                "a −2 lépéshez" if strong else "a „gyanított” ítélet feloldásához",
                t[:m.end()].strip() if m else "0", "erősen gyanított" if strong else "gyanított")), None
    return None, None


def add_grade(project_dir, outcome, certainty, kb_db=None, strict=False, warnings=None, check_kb=True, actor=None,
              **kw):
    cols = ["k", "participants", "effect", "risk_of_bias", "inconsistency", "indirectness", "imprecision",
            "publication_bias", "upgrades", "rationale", "kb_refs", "kb_unverified", "actor"]
    kw["actor"] = check_actor(actor)
    pb_error, pb_warning = publication_bias_check(kw.get("publication_bias"))
    if pb_error:
        raise ValueError(pb_error)
    if pb_warning and warnings is not None:
        warnings.append(pb_warning)
    if warnings is not None:
        msg = grade_consistency(certainty, **{d: kw.get(d) for d in _GRADE_DOWN + ("upgrades",)})
        if msg:
            warnings.append(msg)
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


# ------------------------------------------------------------------ GRADE-tár (szk.ma.grade/v1; terv 4.14, E10)
# Kimenetenként egy JSON (06_kezirat/grade/<kimenet>.grade.json): az emberi ítélet doménenként (rating, step,
# rationale) a motor tanácsa (advisory, suggestion — grade_help.advice) mellett. A naplóbejegyzés (add_grade) marad
# a rögzítés: a record_grade_doc előjeles lépés-szövegekkel hívja, így a grade_consistency változatlanul ellenőriz.
GRADE_SCHEMA = "szk.ma.grade/v1"
GRADE_DIR = "06_kezirat/grade"
GRADE_DOMAINS = _GRADE_DOWN
GRADE_LEVELS = _GRADE_LEVELS
GRADE_RATINGS = {"not serious": 0, "serious": -1, "very serious": -2}
PUBLICATION_BIAS_RATINGS = ("undetected", "suspected", "strongly suspected")
PUBLICATION_BIAS_STATUSES = ("open", "unresolved", "resolved")
GRADE_IMPORTANCE = ("critical", "important", "limited", "not important")
GRADE_UPGRADES = ("large_effect", "dose_response", "opposing_confounding")
GRADE_UPGRADE_STEPS = {"large_effect": (1, 2), "dose_response": (1,), "opposing_confounding": (1,)}
GRADE_CERTAINTY_SOURCES = ("computed", "human")
GRADE_DOC_STATUSES = ("draft", "recorded")
GRADE_ORIGINS = ("human", "ai_draft")
GRADE_DOMAIN_LABELS = {"risk_of_bias": ("Torzítási kockázat", "Risk of bias"),
                       "inconsistency": ("Inkonzisztencia", "Inconsistency"),
                       "indirectness": ("Indirektség", "Indirectness"),
                       "imprecision": ("Pontatlanság", "Imprecision"),
                       "publication_bias": ("Publikációs torzítás", "Publication bias")}
GRADE_RATING_LABELS = {"not serious": ("nem súlyos", "not serious"), "serious": ("súlyos", "serious"),
                       "very serious": ("nagyon súlyos", "very serious"), "undetected": ("nem észlelt", "undetected"),
                       "suspected": ("gyanított", "suspected"),
                       "strongly suspected": ("erősen gyanított", "strongly suspected")}
_GRADE_RUN_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
_GRADE_DEFAULT_KB = "D-S13-002"


def _grade_utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grade_blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _grade_is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def grade_doc_path(project_dir, outcome_id):
    """A kimenet GRADE-fájlja: <projekt>/06_kezirat/grade/<kimenet>.grade.json."""
    if not isinstance(outcome_id, str) or not _OUTCOME_ID.match(outcome_id):
        raise ValueError("érvénytelen kimenet-azonosító: %r (betű, szám, '_', '.', '-'; legfeljebb 64 karakter)"
                         % (outcome_id,))
    return os.path.join(project_dir, *GRADE_DIR.split("/"), "%s.grade.json" % outcome_id)


def _check_main_domain(name, dom, errors):
    rating, step = dom.get("rating"), dom.get("step")
    if rating is not None and rating not in GRADE_RATINGS:
        errors.append("domains.%s.rating: %s vagy null lehet (kapott: %r)" % (name, " | ".join(GRADE_RATINGS), rating))
        return
    if step is not None and not (_grade_is_int(step) and step in (0, -1, -2)):
        errors.append("domains.%s.step: 0, -1, -2 vagy null lehet (kapott: %r)" % (name, step))
        return
    if rating is None:
        if step is not None:
            errors.append("domains.%s: lépés ítélet nélkül (rating: null, step: %r)" % (name, step))
        dom["step"] = None
        return
    want = GRADE_RATINGS[rating]
    if step is None:
        dom["step"] = want
    elif step != want:
        errors.append("domains.%s: a(z) „%s” ítélethez %s lépés tartozik (kapott: %s)" % (
            name, rating, _signed_step(want), _signed_step(step)))


def _check_publication_bias(dom, errors):
    rating, step, status = dom.get("rating"), dom.get("step"), dom.get("status")
    where = "domains.publication_bias"
    if rating is not None and rating not in PUBLICATION_BIAS_RATINGS:
        errors.append("%s.rating: %s vagy null lehet (kapott: %r)" % (
            where, " | ".join(PUBLICATION_BIAS_RATINGS), rating))
        return
    if step is not None and not (_grade_is_int(step) and step in (0, -1, -2)):
        errors.append("%s.step: 0, -1, -2 vagy null lehet (kapott: %r)" % (where, step))
        return
    if status is not None and status not in PUBLICATION_BIAS_STATUSES:
        errors.append("%s.status: %s lehet (kapott: %r)" % (where, " | ".join(PUBLICATION_BIAS_STATUSES), status))
        return
    rationale = not _grade_blank(dom.get("rationale"))
    if rating is None:
        want_status = "open"
        if step is not None:
            errors.append("%s: lépés ítélet nélkül (rating: null, step: %r)" % (where, step))
    elif rating == "undetected":
        want_status = "resolved"
        if step is None:
            dom["step"] = step = 0
        if step != 0:
            errors.append("%s: a „nem észlelt” (undetected) ítélethez 0 lépés tartozik (kapott: %d)" % (where, step))
    elif rating == "strongly suspected":
        want_status = "resolved"
        if step is None:
            dom["step"] = step = -1
        if step == 0:
            errors.append("%s: az „erősen gyanított” (strongly suspected) ítélet −1 lépés (indoklással −2)" % where)
        elif step == -2 and not rationale:
            errors.append("%s: a −2 lépéshez indoklás kell (rationale)" % where)
    else:                                   # suspected: 11. döntés, 4. pont
        if step is None:
            want_status = "unresolved"
        elif step == -2:
            want_status = "resolved"
            errors.append("%s: a „gyanított” (suspected) ítélet feloldása 0 vagy −1 lehet (kapott: −2; az erősebb "
                          "jelhez válaszd az „erősen gyanított” ítéletet)" % where)
        else:
            want_status = "resolved"
            if not rationale:
                errors.append("%s: a „gyanított” (suspected) ítélet feloldásához (0 vagy −1) indoklás kell — "
                              "indoklás nélkül feloldatlan marad (step: null, status: unresolved)" % where)
    if status == "resolved" and want_status == "unresolved":
        errors.append("%s.status: a „gyanított” (suspected) ítélet lépés (0 / −1) és indoklás nélkül nem lehet "
                      "„resolved” — feloldatlan (11. döntés, 4. pont)" % where)
    dom["status"] = want_status             # a státusz származtatott: az ítéletből és a lépésből


def validate_grade_doc(doc):
    """A szk.ma.grade/v1 dokumentum ellenőrzése és normalizálása → (normalizált másolat, hibák).
    Kötelező: outcome_id; a hiányzó run_id / start / certainty null, a hiányzó domén {rating: null, step: null}, a
    hiányzó felminősítés false. A rating és a step összeillik (not serious 0, serious −1, very serious −2; a
    hiányzó step a ratingből jön). Publikációs torzítás: undetected 0; strongly suspected −1 (−2 csak indoklással);
    suspected lépés nélkül → status 'unresolved' (feloldatlan), 0 / −1 csak indoklással ('resolved'); ítélet
    nélkül 'open'. Az ismeretlen kulcsok megmaradnak."""
    if not isinstance(doc, dict):
        return doc, ["a gyökér objektum legyen"]
    try:
        d = json.loads(json.dumps(doc, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        return doc, ["a tartalom nem JSON-képes (vagy NaN/végtelen számot tartalmaz)"]
    errors = []
    if d.get("schema", GRADE_SCHEMA) != GRADE_SCHEMA:
        errors.append("schema: csak %s lehet (kapott: %r)" % (GRADE_SCHEMA, d.get("schema")))
    d["schema"] = GRADE_SCHEMA
    oid = d.get("outcome_id")
    if not isinstance(oid, str) or not _OUTCOME_ID.match(oid):
        errors.append("outcome_id: kötelező; betű, szám, '_', '.', '-' (legfeljebb 64 karakter): %r" % (oid,))
    for key in ("run_id", "start", "certainty"):
        d.setdefault(key, None)
    if d["run_id"] is not None and not (isinstance(d["run_id"], str) and _GRADE_RUN_ID.match(d["run_id"])):
        errors.append("run_id: commit-futás azonosítója (20261004T211200Z-a1f3c2) vagy null: %r" % (d["run_id"],))
    if d["start"] not in ("high", "low", None):
        errors.append("start: high, low vagy null lehet (kapott: %r)" % (d["start"],))
    if d.get("importance") not in GRADE_IMPORTANCE + (None,):
        errors.append("importance: %s vagy null lehet (kapott: %r)" % (
            " | ".join(GRADE_IMPORTANCE), d.get("importance")))
    if d["certainty"] not in _GRADE_LEVELS + (None,):
        errors.append("certainty: %s vagy null lehet (kapott: %r)" % (" | ".join(_GRADE_LEVELS), d["certainty"]))
    d.setdefault("status", "draft")
    if d["status"] not in GRADE_DOC_STATUSES:
        errors.append("status: %s lehet (kapott: %r)" % (" | ".join(GRADE_DOC_STATUSES), d["status"]))
    d.setdefault("origin", "human")
    if d["origin"] not in GRADE_ORIGINS:
        errors.append("origin: %s lehet (kapott: %r)" % (" | ".join(GRADE_ORIGINS), d["origin"]))
    for key in ("start_reason", "rationale", "consistency_warning", "upgrades_rationale", "actor", "approved_by",
                "updated"):
        if d.get(key) is not None and not isinstance(d[key], str):
            errors.append("%s: szöveg (vagy null) legyen" % key)
    jid = d.get("journal_id")
    if jid is not None and not (_grade_is_int(jid) and jid >= 1):
        errors.append("journal_id: pozitív egész vagy null legyen")
    refs = d.get("kb_refs")
    if refs is not None and not (isinstance(refs, list) and all(isinstance(x, str) and x.strip() for x in refs)):
        errors.append("kb_refs: nem üres szövegek listája legyen")
    doms = d.get("domains")
    if doms is None:
        doms = d["domains"] = {}
    if not isinstance(doms, dict):
        errors.append("domains: objektum legyen")
    else:
        for name in GRADE_DOMAINS:
            dom = doms.get(name)
            if dom is None:
                dom = doms[name] = {"rating": None, "step": None}
            if not isinstance(dom, dict):
                errors.append("domains.%s: objektum legyen" % name)
                continue
            dom.setdefault("rating", None)
            dom.setdefault("step", None)
            if dom.get("rationale") is not None and not isinstance(dom["rationale"], str):
                errors.append("domains.%s.rationale: szöveg (vagy null) legyen" % name)
                continue
            if name == "publication_bias":
                _check_publication_bias(dom, errors)
            else:
                _check_main_domain(name, dom, errors)
    ups = d.get("upgrades")
    if ups is None:
        ups = d["upgrades"] = {}
    if not isinstance(ups, dict):
        errors.append("upgrades: objektum legyen")
    else:
        for k in GRADE_UPGRADES:
            ups.setdefault(k, False)
        for k, v in ups.items():
            if not (isinstance(v, bool) or (_grade_is_int(v) and 0 <= v <= max(GRADE_UPGRADE_STEPS.get(k, (2,))))):
                errors.append("upgrades.%s: true / false vagy 0–%d közötti egész legyen (kapott: %r)"
                              % (k, max(GRADE_UPGRADE_STEPS.get(k, (2,))), v))
    det = d.get("upgrade_details")
    if det is not None:
        if not isinstance(det, dict):
            errors.append("upgrade_details: objektum (vagy null) legyen")
        else:
            for k, info in det.items():
                if info is None:
                    continue
                if not isinstance(info, dict):
                    errors.append("upgrade_details.%s: objektum (vagy null) legyen" % k)
                    continue
                st = info.get("step")
                if st is not None and not (_grade_is_int(st) and st in GRADE_UPGRADE_STEPS.get(k, (1, 2))):
                    errors.append("upgrade_details.%s.step: %s lehet (kapott: %r)" % (
                        k, " vagy ".join("+%d" % x for x in GRADE_UPGRADE_STEPS.get(k, (1, 2))), st))
                if info.get("rationale") is not None and not isinstance(info["rationale"], str):
                    errors.append("upgrade_details.%s.rationale: szöveg (vagy null) legyen" % k)
    if d.get("certainty_source") not in GRADE_CERTAINTY_SOURCES + (None,):
        errors.append("certainty_source: %s vagy null lehet" % " | ".join(GRADE_CERTAINTY_SOURCES))
    if d.get("mid_text") is not None and not isinstance(d["mid_text"], str):
        errors.append("mid_text: szöveg (vagy null) legyen")
    return d, errors


def grade_upgrade_steps(doc):
    """{felminősítés: lépés} a bekapcsolt felminősítésekre: true → az upgrade_details lépése (alapból +1), egész →
    maga az érték (0 = nincs)."""
    ups = doc.get("upgrades") if isinstance(doc.get("upgrades"), dict) else {}
    det = doc.get("upgrade_details") if isinstance(doc.get("upgrade_details"), dict) else {}
    out = {}
    for k, v in ups.items():
        if v is True:
            st = (det.get(k) or {}).get("step") if isinstance(det.get(k), dict) else None
            out[k] = st if _grade_is_int(st) and st > 0 else 1
        elif _grade_is_int(v) and v > 0:
            out[k] = v
    return out


def _upgrade_total(doc):
    return sum(grade_upgrade_steps(doc).values())


def grade_doc_certainty(doc):
    """A kiindulásból és az előjeles lépésekből adódó szint (kiindulás − Σ|lépés| + felminősítés, 'very low'–'high'
    közé vágva); None, ha a kiindulás vagy bármely domén lépése hiányzik (pl. feloldatlan publikációs torzítás).
    Csak tájékoztató előtöltés: a végső bizonyosság emberi ítélet (GRADE-09)."""
    if doc.get("start") not in ("high", "low"):
        return None
    doms = doc.get("domains") or {}
    steps = [(doms.get(d) or {}).get("step") for d in GRADE_DOMAINS]
    if any(s is None for s in steps):
        return None
    idx = (3 if doc["start"] == "high" else 1) - sum(abs(s) for s in steps) + _upgrade_total(doc)
    return _GRADE_LEVELS[max(0, min(3, idx))]


def grade_doc_open(doc):
    """{'undecided': [ítélet nélküli domének], 'unresolved': ['publication_bias'] ha a „suspected” feloldatlan}."""
    doms = doc.get("domains") or {}
    undecided = [d for d in GRADE_DOMAINS if (doms.get(d) or {}).get("rating") is None]
    pb = doms.get("publication_bias") or {}
    unresolved = ["publication_bias"] if pb.get("rating") == "suspected" and pb.get("step") is None else []
    return {"undecided": undecided, "unresolved": unresolved}


def _signed_step(step):
    return "0" if step == 0 else "%s%d" % ("−" if step < 0 else "+", abs(step))


def grade_doc_texts(doc):
    """Előjeles lépés-szövegek a projekt.add_grade-hez: {domén: '−1 serious: <indoklás>', …, 'upgrades': '+1
    large_effect' | '0'}; ítélet nélküli doménnél None. A feloldott „suspected”: '0 suspected (resolved): …'."""
    doms = doc.get("domains") or {}
    out = {}
    for name in GRADE_DOMAINS:
        dom = doms.get(name) or {}
        if dom.get("step") is None:
            out[name] = None
            continue
        label = dom.get("rating") or ""
        if name == "publication_bias" and label == "suspected":
            label = "suspected (resolved)"
        txt = "%s %s" % (_signed_step(dom["step"]), label)
        if not _grade_blank(dom.get("rationale")):
            txt += ": " + dom["rationale"].strip()
        out[name] = txt
    steps = grade_upgrade_steps(doc)
    det = doc.get("upgrade_details") if isinstance(doc.get("upgrade_details"), dict) else {}
    parts = []
    for k, st in steps.items():
        why = (det.get(k) or {}).get("rationale") if isinstance(det.get(k), dict) else None
        parts.append("%s (+%d)%s" % (k, st, (": " + why.strip()) if not _grade_blank(why) else ""))
    total = sum(steps.values())
    up = "+%d %s" % (total, "; ".join(parts)) if total else "0"
    if total and not _grade_blank(doc.get("upgrades_rationale")):
        up += " — " + doc["upgrades_rationale"].strip()
    out["upgrades"] = up
    return out


def _grade_flag(code, hu, en, domain=None):
    return {"code": code, "domain": domain, "text": {"hu": hu, "en": en}}


def grade_doc_warnings(doc):
    """Figyelmeztetések (nem tiltások): indoklás nélküli le- vagy felminősítés; a motor-tanácsnál enyhébb ítélet
    indoklás nélkül (3.5.12: „a tanács leminősítést jelez; a »not serious« ítélethez indoklás kell”); felminősítés
    leminősítés mellett (D-S13-009). → [{code, domain, text {hu, en}}]"""
    out = []
    doms = doc.get("domains") or {}
    for name in GRADE_DOMAINS:
        dom = doms.get(name) or {}
        step, rating = dom.get("step"), dom.get("rating")
        sug = dom.get("suggestion") if isinstance(dom.get("suggestion"), dict) else {}
        has_rationale = not _grade_blank(dom.get("rationale"))
        hu_d, en_d = GRADE_DOMAIN_LABELS[name]
        if step is not None and step != 0 and not has_rationale:
            st = _signed_step(step)
            out.append(_grade_flag("rationale_missing",
                                   "%s: a %s lépést lábjegyzetben indokolni kell (GRADE-00)." % (hu_d, st),
                                   "%s: the %s step needs a footnote rationale (GRADE-00)." % (en_d, st), name))
        sstep = sug.get("step")
        if step is not None and _grade_is_int(sstep) and step > sstep and not has_rationale:
            hu_r, en_r = GRADE_RATING_LABELS.get(rating, (rating, rating))
            st = _signed_step(sstep)
            out.append(_grade_flag(
                "advice_downgrade_unexplained",
                "%s: a motor-tanács leminősítést jelez (%s); a „%s” ítélethez indoklás kell." % (hu_d, st, hu_r),
                "%s: the engine advice suggests rating down (%s); the '%s' judgement needs a rationale."
                % (en_d, st, en_r), name))
    det = doc.get("upgrade_details") if isinstance(doc.get("upgrade_details"), dict) else {}
    for k, st in grade_upgrade_steps(doc).items():
        why = (det.get(k) or {}).get("rationale") if isinstance(det.get(k), dict) else None
        if _grade_blank(why) and _grade_blank(doc.get("upgrades_rationale")):
            out.append(_grade_flag("rationale_missing", "Felminősítés (%s, +%d): indoklás kell (GRADE-08)." % (k, st),
                                   "Rating up (%s, +%d): a rationale is needed (GRADE-08)." % (k, st), None))
    steps = [(doms.get(d) or {}).get("step") for d in GRADE_DOMAINS]
    if _upgrade_total(doc) and any(isinstance(x, int) and x < 0 for x in steps):
        out.append(_grade_flag("upgrade_with_downgrade",
                               "Felminősítés leminősítés mellett: általában csak akkor indokolt, ha nincs érdemi "
                               "leminősítési ok (D-S13-009) — indokold.",
                               "Rating up despite rating down: usually justified only when there is no important "
                               "reason to rate down (D-S13-009) — explain.", None))
    return out


def grade_doc_consistency(doc):
    """A megadott bizonyosság és a lépések összhangja → figyelmeztető szöveg vagy None. Ha a kiindulás és minden
    lépés ismert, a pontosan adódó szinttel vet össze; különben a grade_consistency (kiindulás-független)
    ellenőrzése az előjeles lépés-szövegeken."""
    cert = doc.get("certainty")
    if cert is None:
        return None
    want = grade_doc_certainty(doc)
    if want is not None:
        if want == cert:
            return None
        return ("A bizonyosság ('%s') nem egyezik a kiindulásból (%s) és a lépésekből adódó szinttel ('%s'): "
                "ellenőrizd a domének értékét, vagy indokold a globális ítéletet (GRADE-09)."
                % (cert, "magas" if doc.get("start") == "high" else "alacsony", want))
    texts = grade_doc_texts(doc)
    return grade_consistency(cert, **{d: texts.get(d) for d in GRADE_DOMAINS + ("upgrades",)})


def _grade_write_json(path, obj):
    text = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".grade.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _grade_doc_error(errors):
    return ValueError("Érvénytelen GRADE-dokumentum (%s): %s." % (GRADE_SCHEMA, "; ".join(errors)))


def _human_certainty(doc):
    """Az ember adta-e meg a bizonyosságot (nem a korábbi mentés számolta)."""
    return isinstance(doc, dict) and doc.get("certainty") is not None and doc.get("certainty_source") != "computed"


def _settle_certainty(norm, given):
    """A bizonyosság a mentéskor: feloldatlan / hiányzó domén vagy kiindulás mellett null (4.14: „a certainty addig
    null”); különben a megadott (emberi, given=True) érték marad, vagy — ha nincs megadva — a lépésekből adódó szint
    (certainty_source: 'computed'). Az eltérést a grade_doc_consistency jelzi."""
    computed = grade_doc_certainty(norm)
    if computed is None:
        norm["certainty"] = None
        norm["certainty_source"] = None
    elif norm.get("certainty") is None or not given:
        norm["certainty"] = computed
        norm["certainty_source"] = "computed"
    else:
        norm["certainty_source"] = "human"


def save_grade_doc(project_dir, doc, actor=None):
    """A kimenet GRADE-dokumentumának mentése piszkozatként (status 'draft'; feloldatlan publikációs torzítással is
    menthető — csak a rögzítés tiltott). A bizonyosság: feloldatlan / hiányzó domén mellett null, különben a megadott
    (emberi) érték, vagy ha nincs megadva, a kiindulásból és a lépésekből adódó szint (_settle_certainty). Kiszámolja a
    consistency_warning és az override_warnings mezőt, beírja az actor-t és az 'updated' időbélyeget.
    → a mentett (normalizált) dokumentum. Érvénytelen tartalom → ValueError."""
    norm, errors = validate_grade_doc(doc)
    if errors:
        raise _grade_doc_error(errors)
    actor = check_actor(actor)
    norm["status"] = "draft"
    _settle_certainty(norm, _human_certainty(doc))
    norm["consistency_warning"] = grade_doc_consistency(norm)
    norm["override_warnings"] = grade_doc_warnings(norm)
    if actor is not None:
        norm["actor"] = actor
    norm["updated"] = _grade_utc_now()
    _grade_write_json(grade_doc_path(project_dir, norm["outcome_id"]), norm)
    return norm


def load_grade_doc(project_dir, outcome_id):
    """A kimenet GRADE-dokumentuma (normalizálva) vagy None, ha nincs. Hibás tartalom → ValueError."""
    p = grade_doc_path(project_dir, outcome_id)
    try:
        with open(p, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except ValueError as exc:
        raise ValueError("Érvénytelen %s: nem érvényes JSON (%s)." % (p, exc)) from None
    norm, errors = validate_grade_doc(doc)
    if errors:
        raise _grade_doc_error(errors)
    return norm


def list_grade_docs(project_dir):
    """A projekt GRADE-dokumentumai: [{outcome_id, path (projekt-relatív), doc (normalizált; hibánál a nyers vagy
    None), errors}] fájlnév szerint rendezve."""
    base = os.path.join(project_dir, *GRADE_DIR.split("/"))
    out = []
    if not os.path.isdir(base):
        return out
    for fn in sorted(os.listdir(base)):
        if not fn.endswith(".grade.json") or fn.startswith("."):
            continue
        rel = "%s/%s" % (GRADE_DIR, fn)
        oid = fn[:-len(".grade.json")]
        try:
            with open(os.path.join(base, fn), "rb") as fh:
                doc = json.loads(fh.read().decode("utf-8-sig"))
        except (OSError, ValueError) as exc:
            out.append({"outcome_id": oid, "path": rel, "doc": None, "errors": ["nem olvasható JSON: %s" % exc]})
            continue
        norm, errors = validate_grade_doc(doc)
        if not errors and norm.get("outcome_id") != oid:
            errors = ["a fájlnév (%s) és az outcome_id (%s) eltér" % (oid, norm.get("outcome_id"))]
        out.append({"outcome_id": oid, "path": rel, "doc": doc if errors else norm, "errors": errors})
    return out


def record_grade_doc(project_dir, doc, actor=None, kb_db=None, strict=False, check_kb=True):
    """A GRADE-ítélet rögzítése: a projektnaplóba (add_grade, előjeles lépés-szövegekkel, k / résztvevők / hatás a
    dokumentum run_summary-jéből) és a 06_kezirat/grade/<kimenet>.grade.json-ba (status 'recorded', journal_id).
    Tiltott (ValueError, minden okkal): érvénytelen dokumentum; commit-futás (run_id) nélkül; kiindulás nélkül;
    ítélet nélküli domén; feloldatlan publikációs torzítás („suspected” 0 / −1 döntés nélkül; 11. döntés, 4. pont);
    bizonyosság nélkül; jóvá nem hagyott AI-vázlat (origin 'ai_draft', approved_by nélkül; 11. döntés, 6. pont).
    → {id, doc, warnings, path}"""
    norm, errors = validate_grade_doc(doc)
    if errors:
        raise _grade_doc_error(errors)
    actor = check_actor(actor)
    _settle_certainty(norm, _human_certainty(doc))
    problems = []
    if norm.get("run_id") is None:
        problems.append("nincs commit-futás (run_id): GRADE csak rögzített futásra hivatkozhat")
    if norm.get("start") is None:
        problems.append("hiányzik a kiindulás (start: high — RCT; low — megfigyeléses)")
    state = grade_doc_open(norm)
    if state["undecided"]:
        problems.append("ítélet nélküli domén: %s" % ", ".join(state["undecided"]))
    if state["unresolved"]:
        problems.append("a publikációs torzítás „gyanított” (suspected) ítélete feloldatlan: válassz 0-t vagy −1-et "
                        "indoklással (11. döntés, 4. pont; X019)")
    if norm.get("certainty") is None:
        problems.append("hiányzik a bizonyosság (certainty)")
    if norm.get("origin") == "ai_draft" and _grade_blank(norm.get("approved_by")):
        problems.append("AI-vázlat emberi jóváhagyás nélkül (approved_by): az AI-vázlat nem rögzíthető "
                        "(11. döntés, 6. pont)")
    if problems:
        raise ValueError("A GRADE-ítélet nem rögzíthető: %s." % "; ".join(problems))
    texts = grade_doc_texts(norm)
    summ = norm.get("run_summary") if isinstance(norm.get("run_summary"), dict) else {}
    part = summ.get("participants")
    if isinstance(part, float) and part.is_integer():
        part = int(part)
    effect = summ.get("effect_text")
    effect = effect.get("hu") if isinstance(effect, dict) else effect
    reason = "" if _grade_blank(norm.get("start_reason")) else " (%s)" % norm["start_reason"].strip()
    lead = "Kiindulás: %s%s." % ("magas" if norm["start"] == "high" else "alacsony", reason)
    rationale = lead + ((" " + norm["rationale"].strip()) if not _grade_blank(norm.get("rationale")) else "")
    refs = norm.get("kb_refs") or [_GRADE_DEFAULT_KB]
    warns = []
    rid = add_grade(project_dir, norm["outcome_id"], norm["certainty"], kb_db=kb_db, strict=strict, warnings=warns,
                    check_kb=check_kb, actor=actor, k=summ.get("k"), participants=part, effect=effect,
                    risk_of_bias=texts["risk_of_bias"], inconsistency=texts["inconsistency"],
                    indirectness=texts["indirectness"], imprecision=texts["imprecision"],
                    publication_bias=texts["publication_bias"], upgrades=texts["upgrades"], rationale=rationale,
                    kb_refs=",".join(refs))
    norm["status"] = "recorded"
    norm["journal_id"] = rid
    norm["consistency_warning"] = grade_doc_consistency(norm)
    norm["override_warnings"] = grade_doc_warnings(norm)
    if actor is not None:
        norm["actor"] = actor
    norm["updated"] = _grade_utc_now()
    path = grade_doc_path(project_dir, norm["outcome_id"])
    _grade_write_json(path, norm)
    if norm["consistency_warning"] and norm["consistency_warning"] not in warns:
        warns.append(norm["consistency_warning"])
    warns += [w["text"]["hu"] for w in norm["override_warnings"]]
    return {"id": rid, "doc": norm, "warnings": warns, "path": "%s/%s.grade.json" % (GRADE_DIR, norm["outcome_id"])}


def log_run(project_dir, command, data_path, outdir, engine_version, summary=None, actor=None):
    actor = check_actor(actor)
    digest = None
    if data_path and os.path.exists(data_path):
        with open(data_path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
    con = connect(project_dir)
    try:
        con.execute("INSERT INTO run (ts, command, data_path, data_sha256, outdir, engine_version, summary, actor) "
                    "VALUES (?,?,?,?,?,?,?,?)", (_now(), command, data_path, digest, outdir, engine_version,
                                                 json.dumps(summary, ensure_ascii=False) if summary is not None else None,
                                                 actor))
        con.commit()
    finally:
        con.close()
    return digest


# ------------------------------------------------------------------ lekérdezés
def _json_value(v):
    """SQLite-érték → JSON-képes érték: nem véges szám → None, BLOB → UTF-8 szöveg."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).decode("utf-8", "replace")
    return v


def _json_context(v):
    """A 'context' oszlop JSON-szövege → objektum (érvénytelen / nem objektum → None)."""
    if v is None:
        return None
    try:
        obj = json.loads(v)
    except (TypeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _json_row(row):
    return {k: (_json_context(_json_value(row[k])) if k == "context" else _json_value(row[k])) for k in row.keys()}


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
    return _json_row(row)


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
        rows = [_json_row(r) for r in con.execute(sql + " ORDER BY id", args)]
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
        for r in con.execute("SELECT id, kb_refs FROM %s WHERE kb_refs IS NOT NULL ORDER BY id" % table):
            rowids = [x for x in re.split(r"[,;|\s]+", r["kb_refs"]) if re.fullmatch(r"#?\d+", x)]
            if rowids:
                out.append("Instabil szövegrész-hivatkozás (a sorszám-azonosító a kb ingest után mást jelölhet): "
                           "%s #%d: %s — add meg '<forrás>#<sorszám>' alakban (kb search)" % (
                               table, r["id"], ", ".join(rowids)))
    for r in con.execute("SELECT id, stage_id, title FROM finding WHERE severity='blocker' AND status='wontfix' "
                         "ORDER BY id"):
        out.append("Wontfix-szel lezárt blocker: finding #%d [%s] %s — blocker csak fixed vagy indokolt invalid "
                   "státusszal zárható; a kapukat továbbra is blokkolja" % (r["id"], r["stage_id"] or "–", r["title"]))
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
    try:
        return _status(con)
    finally:
        con.close()


def _status(con):
    info = {r["key"]: _json_value(r["value"]) for r in con.execute("SELECT key, value FROM project")}
    return {
        "project": info,
        "open_findings": [_json_row(r) for r in con.execute(
            "SELECT id, severity, stage_id, title, agent, ts FROM finding WHERE status='open' "
            "ORDER BY CASE severity WHEN 'blocker' THEN 0 WHEN 'major' THEN 1 WHEN 'minor' THEN 2 ELSE 3 END, id")],
        "findings_by_status": {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM finding GROUP BY status ORDER BY status")},
        "checkpoints": [_json_row(r) for r in con.execute(
            "SELECT stage_id, verdict, agent, ts FROM checkpoint c WHERE id = "
            "(SELECT MAX(id) FROM checkpoint WHERE stage_id = c.stage_id) "
            "ORDER BY CASE stage_id WHEN 'FINAL' THEN 1 ELSE 0 END, stage_id")],
        "decisions": con.execute("SELECT COUNT(*) FROM decision WHERE status='active'").fetchone()[0],
        "runs": [_json_row(r) for r in con.execute(
            "SELECT id, ts, command, data_sha256, outdir FROM run ORDER BY id DESC LIMIT 5")],
        "grade": [_json_row(r) for r in con.execute("SELECT outcome, certainty, ts FROM grade ORDER BY id")],
        "warnings": _log_warnings(con),
    }


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


EXPORT_SCHEMA = "szk.ma.journal-export/v1"
_EXPORT_TABLES = (("decisions", "decision"), ("findings", "finding"), ("checkpoints", "checkpoint"),
                  ("grades", "grade"), ("runs", "run"))


def export_json(project_dir):
    """A teljes napló JSON-képes szótárként (szk.ma.journal-export/v1; E7, `project export --format json`):
    {schema, project: {title, question, created, …}, decisions, findings, checkpoints, grades, runs,
    warnings}. A tételek minden oszlopukat tartalmazzák (mint a get_item; a run.summary a naplóban tárolt
    JSON-szöveg), azonosító szerint rendezve; nem véges szám helyett null. Időbélyeget nem tesz bele:
    ugyanaz a naplóállapot ugyanazt a dokumentumot adja (determinisztikus audit-csomag)."""
    con = connect(project_dir)
    try:
        out = {"schema": EXPORT_SCHEMA,
               "project": {r["key"]: _json_value(r["value"]) for r in con.execute(
                   "SELECT key, value FROM project ORDER BY key")}}
        for key, table in _EXPORT_TABLES:
            out[key] = [_json_row(r) for r in con.execute("SELECT * FROM %s ORDER BY id" % table)]
        out["warnings"] = _log_warnings(con)
    finally:
        con.close()
    return out


# ------------------------------------------------------------------ változásfigyelés (csak olvasás)
_REV_TABLES = ("project", "decision", "finding", "checkpoint", "grade", "run")
_UNSET = object()


def _ro_connect(project_dir, timeout=0.2):
    """Csak olvasó kapcsolat (nem bővíti a sémát, nem ír)."""
    p = os.path.abspath(db_path(project_dir))
    if not os.path.isfile(p):
        raise FileNotFoundError("Nincs projektnapló: %s (futtasd: project init)" % p)
    uri = Path(p).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=timeout, check_same_thread=False)


def _table_marks(con):
    """{tábla: [sorok száma, legnagyobb rowid]} — a hiányzó tábla [0, 0]."""
    out = {}
    for t in _REV_TABLES:
        try:
            n, mx = con.execute("SELECT COUNT(*), MAX(rowid) FROM %s" % t).fetchall()[0]   # végig: nincs nyitva hagyott olvasás
        except sqlite3.OperationalError:
            n, mx = 0, None
        out[t] = [n, mx or 0]
    return out


def journal_marks(project_dir):
    """Folyamatok között összevethető, csak olvasó állapotjel: {"tables": {tábla: [db, max rowid]},
    "change_counter": az SQLite-fejléc fájlváltozás-számlálója (rollback-journal módban minden
    véglegesített írás növeli; UPDATE-et is jelez) vagy None, "wal": [méret, mtime_ns] | None}.
    Két hívás eredményének egyezése: nincs (észlelhető) változás köztük."""
    p = db_path(project_dir)
    con = _ro_connect(project_dir)
    try:
        tables = _table_marks(con)
    finally:
        con.close()
    counter = None
    try:
        with open(p, "rb") as fh:
            header = fh.read(100)
        if len(header) >= 28 and header[:16] == b"SQLite format 3\x00":
            counter = int.from_bytes(header[24:28], "big")
    except OSError:
        pass
    try:
        st = os.stat(p + "-wal")
        wal = [st.st_size, st.st_mtime_ns]
    except OSError:
        wal = None
    return {"tables": tables, "change_counter": counter, "wal": wal}


class JournalRev(object):
    """Csak olvasó változásszámláló a projektnaplóhoz (a munkapad változásfigyelőjéhez, long-poll).

    poll() → (rev, változott-e). A rev egész szám, amely minden észlelt változásnál eggyel nő (0-ról
    indul; csak ugyanazon példány értékei vethetők össze). Észlelés: a nyitva tartott, csak olvasó
    kapcsolat PRAGMA data_version-je (más kapcsolat véglegesített írása, UPDATE is), a táblák
    sorszáma és legnagyobb rowid-je, valamint a fájl cseréje vagy törlése (st_dev, st_ino). Írni
    nem ír, a sémát nem bővíti. Szálbiztos; close()-zal (vagy with-blokkal) zárható."""

    def __init__(self, project_dir):
        self.project_dir = os.path.abspath(project_dir)
        self._con = None
        self._ident = None
        self._last = _UNSET
        self._rev = 0
        self._lock = threading.Lock()

    def _close(self):
        if self._con is not None:
            try:
                self._con.close()
            except sqlite3.Error:
                pass
        self._con = None
        self._ident = None

    def _snapshot(self):
        try:
            st = os.stat(db_path(self.project_dir))
        except OSError:
            self._close()
            return None
        ident = (st.st_dev, st.st_ino)
        if self._con is None or ident != self._ident:
            self._close()
            self._con = _ro_connect(self.project_dir)
            self._ident = ident
        version = self._con.execute("PRAGMA data_version").fetchall()[0][0]
        return ident, version, _table_marks(self._con)

    def poll(self):
        with self._lock:
            try:
                snap = self._snapshot()
            except sqlite3.Error:           # foglalt vagy épp helyreálló napló: a következő körben újra
                self._close()
                return self._rev, False
            if self._last is _UNSET:
                self._last = snap
                return self._rev, False
            if snap != self._last:
                self._last = snap
                self._rev += 1
                return self._rev, True
            return self._rev, False

    def rev(self):
        return self.poll()[0]

    def close(self):
        with self._lock:
            self._close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ------------------------------------------------------------------ ma-projekt.json (szk.ma.project/v1)
PROJECT_META = "ma-projekt.json"
PROJECT_SCHEMA = "szk.ma.project/v1"
REVIEW_TYPES = ("intervention", "exposure", "diagnostic", "prognostic_factor", "prediction_model")
DATA_CLASSES = ("A", "B", "C")
LOCALES = ("hu", "en")
GRADE_STARTS = ("high", "low")
CONVENTIONS = {"amstar2_partial_yes_critical": ("meets", "weakness"), "grade_suspected": ("unresolved",)}
DEFAULT_CONVENTIONS = {"amstar2_partial_yes_critical": "meets", "grade_suspected": "unresolved"}
_META_ORDER = ("schema", "title", "question", "review_type", "data_class", "outcomes", "appraisal_tools",
               "composer", "doc_roots", "locale", "conventions")
_OUTCOME_ORDER = ("id", "name", "data", "measure", "critical", "primary_spec", "grade_start")
_OUTCOME_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_RELPATH = re.compile(r"^(?!/)(?![A-Za-z]:)(?!.*\.\.)[^\\:]+$")


def project_meta_path(project_dir):
    return os.path.join(project_dir, PROJECT_META)


def _opt_str(errors, where, v):
    if v is not None and not isinstance(v, str):
        errors.append("%s: szöveg (vagy null) legyen" % where)


def _relpath_ok(errors, where, v):
    if v is None:
        return
    if not isinstance(v, str) or not v or not _RELPATH.match(v):
        errors.append("%s: projekt-relatív, '/'-elválasztós út legyen ('..', meghajtó és '\\' nélkül): %r"
                      % (where, v))


def _i18n(errors, where, v):
    if isinstance(v, str):
        return {"hu": v, "en": v}
    if isinstance(v, dict) and isinstance(v.get("hu"), str) and isinstance(v.get("en"), str):
        return dict(v)
    errors.append("%s: {hu, en} szövegpár (vagy egyetlen szöveg) legyen" % where)
    return v


def _str_list(errors, where, v, unique=True):
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() and "\x00" not in x for x in v):
        errors.append("%s: nem üres szövegek listája legyen" % where)
        return v
    if unique and len(set(v)) != len(v):
        errors.append("%s: ismétlődő elem" % where)
    return list(v)


def _ordered(d, order):
    out = {k: d[k] for k in order if k in d}
    out.update((k, v) for k, v in d.items() if k not in out)
    return out


def validate_project_meta(meta):
    """A ma-projekt.json (szk.ma.project/v1) ellenőrzése és normalizálása → (normalizált szótár, hibák).
    Kötelező: title, data_class (A | B | C; kisbetű is). Ellenőrzött, ha megvan: review_type
    (intervention | exposure | diagnostic | prognostic_factor | prediction_model), question {P, I, C, O},
    outcomes[{id (egyedi), name (i18n; szövegből {hu, en} lesz), data, measure, critical, primary_spec,
    grade_start: high | low}], appraisal_tools[], composer {project, outdir}, doc_roots[], locale (hu | en),
    conventions {amstar2_partial_yes_critical: meets | weakness, grade_suspected: unresolved}, plugins {}.
    Alapértékek: schema, locale 'hu', conventions (meets, unresolved), üres listák. Az ismeretlen kulcsok
    megmaradnak (a fogyasztó figyelmen kívül hagyja őket)."""
    errors = []
    if not isinstance(meta, dict):
        return meta, ["a gyökér objektum legyen"]
    m = dict(meta)
    if m.get("schema", PROJECT_SCHEMA) != PROJECT_SCHEMA:
        errors.append("schema: csak %s lehet (kapott: %r)" % (PROJECT_SCHEMA, m.get("schema")))
    m["schema"] = PROJECT_SCHEMA
    if not isinstance(m.get("title"), str) or not m["title"].strip():
        errors.append("title: kötelező, nem üres szöveg")
    dc = m.get("data_class")
    if isinstance(dc, str) and dc.strip().upper() in DATA_CLASSES:
        m["data_class"] = dc.strip().upper()
    else:
        errors.append("data_class: kötelező; A, B vagy C lehet (kapott: %r)" % (dc,))
    rt = m.get("review_type")
    if rt is not None and rt not in REVIEW_TYPES:
        errors.append("review_type: %s lehet (kapott: %r)" % (" | ".join(REVIEW_TYPES), rt))
    q = m.get("question")
    if q is not None:
        if not isinstance(q, dict):
            errors.append("question: {P, I, C, O} objektum legyen")
        else:
            for k, v in q.items():
                _opt_str(errors, "question.%s" % k, v)
    outcomes = m.get("outcomes")
    if outcomes is None:
        m["outcomes"] = []
    elif not isinstance(outcomes, list):
        errors.append("outcomes: lista legyen")
    else:
        seen, norm = set(), []
        for i, o in enumerate(outcomes):
            where = "outcomes[%d]" % i
            if not isinstance(o, dict):
                errors.append("%s: objektum legyen" % where)
                norm.append(o)
                continue
            o = dict(o)
            oid = o.get("id")
            if not isinstance(oid, str) or not _OUTCOME_ID.match(oid):
                errors.append("%s.id: kötelező; betű, szám, '_', '.', '-' (legfeljebb 64 karakter): %r" % (where, oid))
            elif oid in seen:
                errors.append("%s.id: ismétlődő kimenet-azonosító: %s" % (where, oid))
            else:
                seen.add(oid)
            if o.get("name") is not None:
                o["name"] = _i18n(errors, where + ".name", o["name"])
            _relpath_ok(errors, where + ".data", o.get("data"))
            _relpath_ok(errors, where + ".primary_spec", o.get("primary_spec"))
            _opt_str(errors, where + ".measure", o.get("measure"))
            if not isinstance(o.get("critical", False), bool):
                errors.append("%s.critical: true vagy false legyen" % where)
            if o.get("grade_start") is not None and o["grade_start"] not in GRADE_STARTS:
                errors.append("%s.grade_start: high vagy low lehet (kapott: %r)" % (where, o["grade_start"]))
            norm.append(_ordered(o, _OUTCOME_ORDER))
        m["outcomes"] = norm
    m["appraisal_tools"] = _str_list(errors, "appraisal_tools", m.get("appraisal_tools"))
    m["doc_roots"] = _str_list(errors, "doc_roots", m.get("doc_roots"))
    comp = m.get("composer")
    if comp is not None:
        if not isinstance(comp, dict):
            errors.append("composer: {project, outdir} objektum legyen")
        else:
            for k in ("project", "outdir"):
                _opt_str(errors, "composer.%s" % k, comp.get(k))
    plugins = m.get("plugins")
    if plugins is not None and not (isinstance(plugins, dict) and all(isinstance(v, str) for v in plugins.values())):
        errors.append("plugins: {név: út} objektum legyen")
    loc = m.get("locale", "hu")
    if loc not in LOCALES:
        errors.append("locale: hu vagy en lehet (kapott: %r)" % (loc,))
    m["locale"] = loc
    conv = m.get("conventions")
    if conv is None:
        conv = {}
    if not isinstance(conv, dict):
        errors.append("conventions: objektum legyen")
    else:
        conv = dict(DEFAULT_CONVENTIONS, **conv)
        for k, allowed in CONVENTIONS.items():
            if conv.get(k) not in allowed:
                errors.append("conventions.%s: %s lehet (kapott: %r)" % (k, " | ".join(allowed), conv.get(k)))
        m["conventions"] = _ordered(conv, tuple(CONVENTIONS))
    try:
        json.dumps(m, allow_nan=False)
    except (TypeError, ValueError):
        errors.append("a tartalom nem JSON-képes (vagy NaN/végtelen számot tartalmaz)")
    return _ordered(m, _META_ORDER), errors


def _meta_error(errors):
    return ValueError("Érvénytelen %s: %s." % (PROJECT_META, "; ".join(errors)))


def load_project_meta(project_dir):
    """A ma-projekt.json ellenőrzött, alapértékekkel kiegészített tartalma; None, ha nincs ilyen fájl.
    Hibás JSON vagy séma → ValueError (magyar üzenet, minden hibával)."""
    p = project_meta_path(project_dir)
    try:
        with open(p, encoding="utf-8-sig") as fh:
            text = fh.read()
    except FileNotFoundError:
        return None

    def bad_constant(name):
        raise ValueError("nem véges szám: %s" % name)
    try:
        meta = json.loads(text, parse_constant=bad_constant)
    except ValueError as exc:
        raise ValueError("Érvénytelen %s: nem érvényes JSON (%s)." % (PROJECT_META, exc)) from None
    norm, errors = validate_project_meta(meta)
    if errors:
        raise _meta_error(errors)
    return norm


def _journal_title(project_dir):
    """A projektnapló (projekt.sqlite) címe, ha van; különben None."""
    if not os.path.isfile(db_path(project_dir)):
        return None
    try:
        con = connect(project_dir)
        try:
            row = con.execute("SELECT value FROM project WHERE key='title'").fetchone()
        finally:
            con.close()
    except Exception:                                   # noqa: BLE001 — a cím hiánya nem hiba
        return None
    return row[0] if row and isinstance(row[0], str) and row[0].strip() else None


def outcome_meta_doc(project_dir, meta, outcome, data_class=None, replace=False):
    """A ma-projekt.json új tartalma egy kimenet felvételével (vagy cseréjével, replace=True) — írás nélkül.
    meta: a mostani (load_project_meta) tartalom vagy None (még nincs fájl: létrejön; cím a projektnapló címe vagy
    a mappa neve, adatosztály data_class, alapból A). Az outcome alapértelmezett primary_spec-je
    05_elemzes/specs/<id>_primary.json. → (normalizált dokumentum, a felvett kimenet). Hibánál ValueError."""
    if not isinstance(outcome, dict):
        raise ValueError("a kimenet objektum legyen ({id, name, data, measure})")
    o = {k: v for k, v in outcome.items() if v is not None}
    oid = o.get("id")
    if not isinstance(oid, str) or not _OUTCOME_ID.match(oid):
        raise ValueError("a kimenet azonosítója kötelező: betű, szám, '_', '.', '-' (legfeljebb 64 karakter)")
    o.setdefault("primary_spec", "05_elemzes/specs/%s_primary.json" % oid)
    if meta is None:
        title = _journal_title(project_dir) or os.path.basename(os.path.abspath(project_dir)) or "projekt"
        doc = {"schema": PROJECT_SCHEMA, "title": title, "data_class": data_class or "A", "outcomes": []}
    else:
        doc = dict(meta)
    outs = [dict(x) for x in doc.get("outcomes") or [] if isinstance(x, dict)]
    idx = next((i for i, x in enumerate(outs) if x.get("id") == oid), None)
    if idx is not None and not replace:
        raise ValueError("már van %s azonosítójú kimenet (módosításhoz: replace / --replace)" % oid)
    if idx is None:
        outs.append(o)
    else:
        merged = dict(outs[idx])
        merged.update(o)
        outs[idx] = merged
    doc["outcomes"] = outs
    norm, errors = validate_project_meta(doc)
    if errors:
        raise _meta_error(errors)
    return norm, next(x for x in norm["outcomes"] if x.get("id") == oid)


def add_outcome(project_dir, outcome, data_class=None, replace=False):
    """Kimenet felvétele a ma-projekt.json-ba (ha nincs, létrehozza) → (normalizált dokumentum, kimenet)."""
    doc, o = outcome_meta_doc(project_dir, load_project_meta(project_dir), outcome, data_class, replace)
    return save_project_meta(project_dir, doc), o


def save_project_meta(project_dir, meta):
    """A ma-projekt.json ellenőrzött kiírása (atomikus csere, UTF-8, 2 szóközös behúzás, záró újsor)
    → a normalizált szótár. Érvénytelen tartalom → ValueError, és a fájl nem változik."""
    norm, errors = validate_project_meta(meta)
    if errors:
        raise _meta_error(errors)
    text = json.dumps(norm, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    os.makedirs(project_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ma-projekt.", suffix=".tmp", dir=project_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, project_meta_path(project_dir))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return norm
