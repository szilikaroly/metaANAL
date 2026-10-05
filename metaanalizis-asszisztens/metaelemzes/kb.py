# -*- coding: utf-8 -*-
"""Tudásbázis (SQLite + FTS5): építés a seed JSON-okból, teljes szöveg betöltése helyi
PDF/DOCX/TXT/MD fájlokból, keresés és csak-olvasó SQL az ágensek döntéseihez.

Elérési utak:
  tudasbazis/schema.sql         — séma
  tudasbazis/seed/*.json         — verziókövetett, saját szavas tudás
  tudasbazis/tudasbazis.sqlite   — a felépített adatbázis (gitignore; `kb build` újraépíti); pluginként
                                   telepítve a plugin megmaradó adatmappájában (<plugins>/data/<plugin>-<marketplace>/),
                                   hogy frissítéskor a betöltött teljes szöveg ne vesszen el; METAELEMZES_KB felülírja
  tudasbazis/forrasok/           — a helyi forrásfájlok (gitignore; szerzői jog)
"""
import collections
import datetime
import glob
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import time
import zipfile
from xml.etree import ElementTree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KB_DIR = os.path.join(ROOT, "tudasbazis")
SCHEMA = os.path.join(KB_DIR, "schema.sql")
SEED_DIR = os.path.join(KB_DIR, "seed")


def _plugin_data_dir(env=os.environ, root=ROOT):
    """A Claude Code-plugin megmaradó adatmappája, ha a motor pluginként fut (különben None). A
    CLAUDE_PLUGIN_DATA csak a hook-/MCP-folyamatok környezetében van meg, a Bash-eszközzel futtatott
    parancsokéban nincs: ezért a plugin-gyorsítótár elrendezéséből is levezetjük."""
    data = env.get("CLAUDE_PLUGIN_DATA")
    if data:
        proot = env.get("CLAUDE_PLUGIN_ROOT")
        if not proot or os.path.normcase(os.path.realpath(proot)) == os.path.normcase(os.path.realpath(root)):
            return data
    parts = os.path.normpath(root).split(os.sep)
    if len(parts) >= 5 and parts[-4] == "cache":   # <plugins>/cache/<marketplace>/<plugin>/<verzió>
        plugins = os.sep.join(parts[:-4]) or os.sep
        if parts[-6:-4] == [".claude", "plugins"] or os.path.isfile(os.path.join(plugins, "installed_plugins.json")):
            pid = re.sub(r"[^A-Za-z0-9_-]", "-", "%s@%s" % (parts[-2], parts[-3]))
            return os.path.join(plugins, "data", pid)
    return None


def _default_db(env=os.environ, root=ROOT):
    """Az alapértelmezett adatbázis: METAELEMZES_KB > a plugin adatmappája > tudasbazis/tudasbazis.sqlite."""
    if env.get("METAELEMZES_KB"):
        return env["METAELEMZES_KB"]
    data = _plugin_data_dir(env, root)
    return os.path.join(data, "tudasbazis.sqlite") if data else os.path.join(KB_DIR, "tudasbazis.sqlite")


DEFAULT_DB = _default_db()

SEED_TABLES = [
    # (fájlminta, tábla, oszlopok)
    ("sources*.json", "source", ["source_id", "citation", "short", "year", "doi", "kind", "license_note", "file_hint", "notes"]),
    ("stages*.json", "stage", ["stage_id", "ord", "name_hu", "name_en", "description"]),
    ("knowledge*.json", "knowledge", ["k_id", "stage_id", "kind", "title", "body", "source_id", "locator", "tags"]),
    ("formulas*.json", "formula", ["formula_id", "name", "expression", "variables", "engine_ref", "stage_id", "source_id", "locator", "notes"]),
    ("rules*.json", "decision_rule", ["rule_id", "stage_id", "applies_to", "condition", "recommendation", "rationale", "strength", "machine_check", "source_ids", "locator"]),
    ("checklists*.json", "checklist_item", ["item_id", "checklist", "section", "ord", "text", "how_to_verify", "stage_id", "source_ids"]),
    ("tools*.json", "tool", ["tool_id", "name", "category", "purpose", "access", "url", "claude_integration", "notes", "source_ids"]),
    ("examples*.json", "worked_example", ["example_id", "source_id", "title", "location", "effect_measure", "input_type", "method_settings", "studies", "reported_results", "verification", "usable_as_test_oracle"]),
]

# a helyben betöltött teljes szöveg táblái: sémaváltáskor átmentjük (ha kompatibilis), a seed-építés nem törli
FULLTEXT_TABLES = ("source", "chunk", "ingest_file")

# az azonosítók feloldási sorrendje (kb show / a projektnapló --kb ellenőrzése)
ID_TABLES = (("decision_rule", "rule_id"), ("knowledge", "k_id"), ("formula", "formula_id"),
             ("checklist_item", "item_id"), ("tool", "tool_id"), ("worked_example", "example_id"),
             ("source", "source_id"), ("stage", "stage_id"))

# a seed ellenőrzőlistái (checklist_item.checklist; a `kb checklist` súgója is ezt sorolja)
CHECKLISTS = ("PRISMA2020", "PRISMA_P", "PRISMA_S", "PREFLIGHT", "REVIEWER", "EVALUATOR", "AMSTAR2", "GRADE")

SUPPORTED_EXT = (".pdf", ".docx", ".txt", ".md")


class KBError(RuntimeError):
    pass


def _readonly_uri(path, path_cls=pathlib.Path):
    """Csak-olvasó SQLite URI. A pathlib százalékosan kódolja a '#', '?', '%' és szóköz
    karaktereket, és Windows-meghajtóbetűre 'file:///C:/…' alakot ad. UNC-útvonalnál
    (\\\\szerver\\megosztás) az SQLite csak üres authority-t fogad el, ezért a
    'file:////szerver/megosztás/…' alakot használjuk. A relatív útvonal (pl. --db kb.sqlite)
    az aktuális mappához képest értendő."""
    p = path_cls(path)
    if not p.is_absolute():
        p = p.absolute()
    uri = p.as_uri()
    if uri.startswith("file://") and not uri.startswith("file:///"):
        uri = "file:////" + uri[len("file://"):]
    return uri + "?mode=ro"


def connect(path=None, readonly=False):
    path = path or DEFAULT_DB
    if readonly:
        if not os.path.exists(path):
            raise KBError("A tudásbázis még nincs felépítve: futtasd a `kb build` parancsot.")
        con = sqlite3.connect(_readonly_uri(path), uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _seed_files(pattern):
    return sorted(glob.glob(os.path.join(SEED_DIR, pattern)))


def _read_schema():
    with open(SCHEMA, encoding="utf-8") as fh:
        return fh.read()


def _schema_hash(schema=None):
    return hashlib.sha256((schema if schema is not None else _read_schema()).encode("utf-8")).hexdigest()


# a motor szabálykészletei: (modul, szakasz) → a kb decision_rule táblájába kerülnek; szakasz None: a modul
# RULE_STAGES-e adja szabályonként (audit: X-szabályok)
ENGINE_RULESETS = (("validate", "S05"), ("prisma", "S04"), ("audit", None))
ENGINE_RULE_KINDS = {"validate": "Adatvalidálási", "prisma": "PRISMA-számellenőrzési",
                     "audit": "Projekt-audit (kereszt-artefaktum)"}


def _engine_rules():
    """A motor összes szabálya egy szótárban: kód → (súlyosság, cím, teendő, forrás)."""
    out = {}
    for mod, _stage in ENGINE_RULESETS:
        out.update(__import__("metaelemzes." + mod, fromlist=["RULES"]).RULES)
    return out


def _engine_rule_meta():
    """kód → (szakasz, modul) — melyik szakaszhoz és melyik motormodulhoz tartozik a szabály."""
    meta = {}
    for mod, stage in ENGINE_RULESETS:
        m = __import__("metaelemzes." + mod, fromlist=["RULES"])
        per_rule = getattr(m, "RULE_STAGES", {})
        for code in m.RULES:
            meta[code] = (per_rule.get(code, stage), mod)
    return meta


def _seed_fingerprint():
    """A seed-ek, a séma ÉS a motor validálási szabályai (validate.RULES) együttes ujjlenyomata:
    bármelyik változása automatikus újraépítést vált ki."""
    h = hashlib.sha256()
    for f in sorted(glob.glob(os.path.join(SEED_DIR, "*.json"))) + [SCHEMA]:
        with open(f, "rb") as fh:
            h.update(os.path.basename(f).encode("utf-8"))
            h.update(fh.read())
    h.update(b"\0engine.RULES\0")
    h.update(json.dumps(_engine_rules(), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
    return h.hexdigest()


def _jsonify(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def _split_sql(script):
    """SQL-szkript → utasítások (az sqlite3.complete_statement szerint; a megjegyzéseket és a
    sztringekben lévő ';'-t helyesen kezeli). A PRAGMA-kat kihagyjuk (tranzakcióban hatástalanok)."""
    out, buf = [], ""
    for line in script.splitlines(True):
        buf += line
        if sqlite3.complete_statement(buf):
            s = buf.strip()
            if s and not re.match(r"(?is)^(\s*--[^\n]*\n)*\s*pragma\b", s):
                out.append(s)
            buf = ""
    rest = "\n".join(l for l in buf.splitlines() if not l.strip().startswith("--")).strip()
    if rest:
        out.append(rest)   # befejezetlen utasítás: a végrehajtás jelzi a hibát
    return out


_CREATE_NAME = re.compile(r'(?is)create\s+(?:virtual\s+|unique\s+|temp\s+|temporary\s+)?(table|view|index|trigger)\s+'
                          r'(?:if\s+not\s+exists\s+)?["`\[]?(\w+)')


def _schema_objects(statements):
    """A sémában létrehozott objektumok (típus, név) listája."""
    out = []
    for s in statements:
        m = _CREATE_NAME.search(re.sub(r"(?m)--[^\n]*$", "", s))
        if m:
            out.append((m.group(1).lower(), m.group(2)))
    return out


def _meta_get(con, key):
    try:
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _user_objects(con):
    return {r[0]: r[1] for r in con.execute(
        "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}


def _migrate_schema(con, statements):
    """Sémaváltás (a schema.sql megváltozott): a tudásbázis kezelt objektumait eldobja és az aktuális
    sémából újra létrehozza. A helyben betöltött teljes szöveg (source, chunk, ingest_file) sorait
    átmenti a közös oszlopokon, ha az új séma kompatibilis; különben eldobja, és az újrabetöltésre
    figyelmeztet. Tranzakción belül fut (a hívó kezeli)."""
    notes = []
    existing = _user_objects(con)
    new_objects = _schema_objects(statements)
    managed = {name for _, name in new_objects}
    old_list = _meta_get(con, "managed_objects")
    if old_list:
        managed |= set(json.loads(old_list))
    # nézetek, triggerek, indexek (átnevezéskor a táblával vándorolnának, és az új séma
    # IF NOT EXISTS-e miatt nem jönnének létre), majd a virtuális (FTS) táblák — az árnyéktábláik
    # velük együtt tűnnek el
    for name, typ in existing.items():
        if name in managed and typ in ("view", "trigger", "index"):
            con.execute('DROP %s IF EXISTS "%s"' % (typ.upper(), name))
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND "
                               "sql LIKE 'CREATE VIRTUAL TABLE%'").fetchall():
        if name in managed:
            con.execute('DROP TABLE IF EXISTS "%s"' % name)
    kept = []
    for t in FULLTEXT_TABLES:
        if existing.get(t) == "table":
            con.execute('DROP TABLE IF EXISTS "_old_%s"' % t)
            con.execute('ALTER TABLE "%s" RENAME TO "_old_%s"' % (t, t))
            kept.append(t)
    for name, typ in _user_objects(con).items():
        if name in managed and typ == "table":
            con.execute('DROP TABLE IF EXISTS "%s"' % name)
    for s in statements:
        con.execute(s)
    # kompatibilitás: az új tábla minden kötelező (NOT NULL, alapérték nélküli, nem kulcs) oszlopa megvan-e
    plan, compatible = {}, True
    for t in kept:
        old_cols = [r[1] for r in con.execute('PRAGMA table_info("_old_%s")' % t)]
        info = con.execute('PRAGMA table_info("%s")' % t).fetchall()
        n_old = con.execute('SELECT COUNT(*) FROM "_old_%s"' % t).fetchone()[0]
        if not info:                    # az új sémából kikerült tábla: nincs hová átmenteni
            plan[t] = ([], 0)
            continue
        common = [r[1] for r in info if r[1] in old_cols]
        required = {r[1] for r in info if r[3] and r[4] is None and not r[5]}
        if n_old and (not common or required - set(common)):
            compatible = False
        plan[t] = (common, n_old)
    for t in kept:
        common, n_old = plan[t]
        if compatible and n_old and common:
            cols = ",".join('"%s"' % c for c in common)
            con.execute('INSERT INTO "%s" (%s) SELECT %s FROM "_old_%s"' % (t, cols, cols, t))
        con.execute('DROP TABLE "_old_%s"' % t)
    if not compatible:
        # a seed-források a seed-ből visszatöltődnek; a teljes szöveget újra be kell tölteni
        lost = ["%s: %d sor" % (t, plan[t][1]) for t in kept if plan[t][1]]
        notes.append("A séma inkompatibilis módon változott, a helyben betöltött teljes szöveg (%s) nem "
                     "menthető át. Töltsd be újra: ma.py kb ingest tudasbazis/forrasok (és a saját fájljaidat "
                     "a korábbi --source-id/--citation értékekkel)." % ", ".join(lost))
    elif kept:
        con.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')")
        n_chunk = plan.get("chunk", ([], 0))[1]
        notes.append("Sémaváltás: a tudásbázis táblái újra létrehozva; a teljes szöveg átmentve (%d szövegrész)." % n_chunk)
    return notes


def _fk_report(con, bad):
    """PRAGMA foreign_key_check sorai → olvasható leírás."""
    out = []
    for table, rowid, parent, fkid in bad:
        fk = [r for r in con.execute('PRAGMA foreign_key_list("%s")' % table) if r[0] == fkid]
        col = fk[0][3] if fk else "?"
        key = dict(ID_TABLES + (("chunk", "chunk_id"),)).get(table)
        try:
            row = con.execute('SELECT %s, "%s" FROM "%s" WHERE rowid=?' % (
                ('"%s"' % key) if key else "rowid", col, table), (rowid,)).fetchone()
            ident, val = (row[0], row[1]) if row else (rowid, "?")
        except sqlite3.Error:
            ident, val = rowid, "?"
        out.append("%s %s: %s=%s → nincs ilyen %s" % (table, ident, col, val, parent))
    return out


def build(path=None, keep_fulltext=True):
    """(Újra)építi a strukturált táblákat a seed-ekből, egyetlen tranzakcióban.
    A teljes szöveg (source/chunk/ingest_file) alapból megmarad; keep_fulltext=False esetén törlődik.
    Sémaváltáskor a kezelt táblák eldobva és újra létrehozva (a teljes szöveg átmentve, ha lehet).
    Hibát jelez (KBError) duplikált seed-azonosítóra, motorszabály-ütközésre és a seed-táblák
    hivatkozási (FK) hibáira — ekkor az adatbázis változatlan marad."""
    path = path or DEFAULT_DB
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    schema = _read_schema()
    statements = _split_sql(schema)
    con = connect(path)
    con.isolation_level = None   # explicit tranzakció (a DDL is benne van)
    notes = []
    try:
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute("BEGIN IMMEDIATE")
        existing = _user_objects(con)
        if existing and "meta" not in existing:
            raise KBError("A(z) %s nem tudásbázis-adatbázis (nincs 'meta' táblája) — nem írom felül. "
                          "Adj meg másik --db útvonalat." % path)
        if _meta_get(con, "schema_hash") != _schema_hash(schema):
            if existing:
                notes += _migrate_schema(con, statements)
            else:
                for s in statements:
                    con.execute(s)
        else:
            for s in statements:          # IF NOT EXISTS: hiányzó objektumok pótlása
                con.execute(s)
        cur = con.cursor()
        for table in ("knowledge_fts", "rule_fts"):
            cur.execute("DELETE FROM %s" % table)
        if not keep_fulltext:
            cur.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('delete-all')")
            cur.execute("DELETE FROM chunk")
            cur.execute("DELETE FROM ingest_file")
        for _, table, _ in reversed(SEED_TABLES):
            if table == "source" and keep_fulltext:
                continue
            cur.execute("DELETE FROM %s" % table)
        seed_ids = {}
        for pattern, table, cols in SEED_TABLES:
            ids = seed_ids.setdefault(table, {})
            for f in _seed_files(pattern):
                fname = os.path.basename(f)
                with open(f, encoding="utf-8") as fh:
                    try:
                        items = json.load(fh)
                    except ValueError as exc:
                        raise KBError("Hibás JSON: %s (%s)" % (f, exc))
                if not isinstance(items, list):
                    raise KBError("%s: a seed-fájlnak JSON-tömbnek kell lennie" % fname)
                for it in items:
                    key = it.get(cols[0]) if isinstance(it, dict) else None
                    if not key:
                        raise KBError("%s: hiányzó azonosító (%s) egy tételnél" % (fname, cols[0]))
                    if key in ids:
                        raise KBError("Duplikált azonosító a seed-ekben: %s.%s = %s (%s és %s) — az egyik tétel "
                                      "csendben felülírná a másikat; nevezd át az egyiket." % (
                                          table, cols[0], key, ids[key], fname))
                    ids[key] = fname
                    vals = [_jsonify(it.get(c)) for c in cols]
                    # a source-ban a megtartott (helyben betöltött) sorokat a seed felülírhatja
                    verb = "INSERT OR REPLACE" if table == "source" else "INSERT"
                    cur.execute("%s INTO %s (%s) VALUES (%s)" % (verb, table, ",".join(cols), ",".join("?" * len(cols))), vals)
        # a motor adatvalidálási szabályai is szabályok (egy igazságforrás)
        rules = _engine_rules()
        clash = sorted(set(rules) & set(seed_ids.get("decision_rule", {})))
        if clash:
            raise KBError("A seed-szabály azonosítója ütközik a motor szabályával: %s (%s) — a V-, P- és "
                          "X-kódok a metaelemzes/validate.py, prisma.py és audit.py RULES-ából jönnek." % (
                              ", ".join(clash), ", ".join(seed_ids["decision_rule"][c] for c in clash)))
        rmeta = _engine_rule_meta()
        for code, (sev, title, advice, src) in rules.items():
            stage, mod = rmeta[code]
            kind = ENGINE_RULE_KINDS.get(mod, mod)
            cur.execute("INSERT INTO decision_rule (rule_id, stage_id, applies_to, condition, recommendation, "
                        "rationale, strength, machine_check, source_ids, locator) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (code, stage, "engine", title, advice, "%s szabály (%s)" % (kind, sev),
                         "must" if sev == "error" else ("should" if sev == "warning" else "consider"),
                         "metaelemzes.%s:%s" % (mod, code), "engine", src))
        if keep_fulltext:
            # a seed-ből törölt/átnevezett, teljes szöveg nélküli (nem felhasználói) források eltávolítása
            keep = list(seed_ids.get("source", {}))
            cur.execute("DELETE FROM source WHERE COALESCE(kind,'') <> 'user' AND source_id NOT IN (%s) "
                        "AND source_id NOT IN (SELECT source_id FROM chunk) "
                        "AND source_id NOT IN (SELECT source_id FROM ingest_file)" % (",".join("?" * len(keep)) or "''"), keep)
        cur.execute("INSERT INTO knowledge_fts (k_id, title, body, tags) SELECT k_id, title, body, COALESCE(tags,'') FROM knowledge")
        cur.execute("INSERT INTO rule_fts (rule_id, condition, recommendation, rationale) "
                    "SELECT rule_id, condition, recommendation, COALESCE(rationale,'') FROM decision_rule")
        bad = cur.execute("PRAGMA foreign_key_check").fetchall()
        seed_bad = [b for b in bad if b[0] not in FULLTEXT_TABLES]
        if seed_bad:
            raise KBError("Hivatkozási hiba a seed-ekben (%d): %s" % (len(seed_bad), "; ".join(_fk_report(con, seed_bad[:10]))))
        if bad:
            notes.append("A teljes szöveg %d sora nem létező forrásra hivatkozik (%s)." % (
                len(bad), "; ".join(_fk_report(con, bad[:5]))))
        cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seed_fingerprint', ?)", (_seed_fingerprint(),))
        cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_hash', ?)", (_schema_hash(schema),))
        cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('managed_objects', ?)",
                    (json.dumps(sorted({n for _, n in _schema_objects(statements)})),))
        counts = {table: cur.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0] for _, table, _ in SEED_TABLES}
        counts["engine_rules"] = len(rules)
        counts["foreign_key_problems"] = len(bad)
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        try:
            con.execute("PRAGMA foreign_keys = ON")
        finally:
            con.close()
    if notes:
        counts["figyelmeztetesek"] = notes
    return counts


def ensure_built(path=None):
    path = path or DEFAULT_DB
    if not os.path.exists(path):
        res = build(path)
        _print_notes(res)
        return True
    try:
        con = connect(path, readonly=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key='seed_fingerprint'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        row = None
    if row is None or row[0] != _seed_fingerprint():
        res = build(path)
        _print_notes(res)
        return True
    return False


def _print_notes(res):
    for n in (res or {}).get("figyelmeztetesek", []):
        print("FIGYELEM (tudásbázis): %s" % n, file=sys.stderr)


# --------------------------------------------------------------- betöltés
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pdf_pages(path):
    """PDF → oldalak szövege. Sorrend: pypdf (ha telepítve), különben pdftotext (poppler)."""
    try:
        import pypdf
    except ImportError:
        pypdf = None
    if pypdf is not None:
        try:
            reader = pypdf.PdfReader(path)
            return [(p.extract_text() or "") for p in reader.pages]
        except Exception as exc:   # sérült / titkosított PDF
            raise KBError("%s: a PDF nem olvasható (pypdf: %s)" % (path, exc))
    try:
        res = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True)
    except OSError:
        raise KBError("PDF-hez telepítsd a pypdf csomagot (pip install pypdf) vagy a pdftotext-et (poppler).")
    if res.returncode != 0:
        msg = "; ".join(l.strip() for l in res.stderr.decode("utf-8", "replace").splitlines() if l.strip())
        raise KBError("%s: a pdftotext nem tudta feldolgozni (sérült vagy titkosított PDF?): %s" % (
            path, msg[:300] or "kilépési kód %d" % res.returncode))
    return res.stdout.decode("utf-8", "replace").split("\f")


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
# tartalmat hordozó "burkoló" elemek (tartalomvezérlők, egyéni XML, követett beszúrás)
_DOCX_WRAPPERS = {_W + t for t in ("sdt", "sdtContent", "customXml", "smartTag", "ins", "moveTo", "hyperlink",
                                    "fldSimple")}
_DOCX_SKIP = {_W + t for t in ("pPr", "rPr", "sdtPr", "sdtEndPr", "del", "moveFrom", "tblPr", "tblGrid", "trPr", "tcPr")}


def _docx_textboxes(node):
    """A futáson belüli szövegdobozok (w:txbxContent); mc:AlternateContent-nél csak az első ág."""
    if node.tag == _W + "txbxContent":
        return [node]
    if node.tag == _MC + "AlternateContent":
        kids = list(node)
        return _docx_textboxes(kids[0]) if kids else []
    out = []
    for c in node:
        out += _docx_textboxes(c)
    return out


def _docx_run(r, out):
    for e in r:
        tag = e.tag
        if tag == _W + "t":
            out.append(e.text or "")
        elif tag == _W + "tab":
            out.append("\t")
        elif tag in (_W + "br", _W + "cr"):
            out.append("\n")
        elif tag == _W + "noBreakHyphen":
            out.append("-")
        elif tag in _DOCX_SKIP:
            continue
        else:
            for box in _docx_textboxes(e):
                txt = " ".join(t for t in (_docx_par_text(p) for p in box.iter(_W + "p")) if t.strip())
                if txt:
                    out.append(" %s " % txt)


def _docx_par_text(p):
    """Egy bekezdés szövege dokumentum-sorrendben: a futások (w:r) w:t-szövege elválasztó nélkül,
    w:tab → tabulátor, w:br/w:cr → sortörés; hivatkozások, tartalomvezérlők és beszúrások belseje
    is, a törölt szöveg nem."""
    out = []

    def walk(node):
        for e in node:
            tag = e.tag
            if tag == _W + "r":
                _docx_run(e, out)
            elif tag in _DOCX_SKIP:
                continue
            elif tag == _MC + "AlternateContent":
                kids = list(e)
                if kids:
                    walk(kids[0])
            else:
                walk(e)
    walk(p)
    return "".join(out)


def _docx_children(node, tag):
    """Az adott címkéjű közvetlen gyermekek, a burkoló elemeken (w:sdt stb.) át is."""
    for c in node:
        if c.tag == tag:
            yield c
        elif c.tag in _DOCX_WRAPPERS:
            for x in _docx_children(c, tag):
                yield x


def _docx_cell_text(tc):
    parts = []

    def walk(node):
        for c in node:
            if c.tag == _W + "p":
                parts.append(_docx_par_text(c))
            elif c.tag == _W + "tbl":            # beágyazott táblázat: cellánként, sorrendben
                for tr in _docx_children(c, _W + "tr"):
                    for inner in _docx_children(tr, _W + "tc"):
                        walk(inner)
            elif c.tag in _DOCX_WRAPPERS:
                walk(c)
    walk(tc)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def docx_paragraphs(path):
    """DOCX → bekezdések (táblázatok sorai ' | ' elválasztással), csak standard könyvtárral.
    A futások szövegét elválasztó nélkül fűzi (a szavak egyben maradnak a cellákban is),
    a w:tab tabulátor, a w:br/w:cr sortörés; a tartalomvezérlők (w:sdt) tartalma is bekerül."""
    try:
        with zipfile.ZipFile(path) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError) as exc:
        raise KBError("%s: nem érvényes DOCX (%s)" % (path, exc))
    except ElementTree.ParseError as exc:
        raise KBError("%s: hibás DOCX XML (%s)" % (path, exc))
    body = root.find(_W + "body")
    out = []
    if body is None:
        return out

    def walk(node):
        for child in node:
            if child.tag == _W + "p":
                out.append(_docx_par_text(child))
            elif child.tag == _W + "tbl":
                out.append("[TÁBLÁZAT]")
                for tr in _docx_children(child, _W + "tr"):
                    out.append(" | ".join(_docx_cell_text(tc) for tc in _docx_children(tr, _W + "tc")))
                out.append("[/TÁBLÁZAT]")
            elif child.tag in _DOCX_WRAPPERS:
                walk(child)
            elif child.tag == _MC + "AlternateContent":
                kids = list(child)
                if kids:
                    walk(kids[0])
    walk(body)
    return out


def _read_text(path):
    """TXT/MD beolvasása: UTF-8 (BOM-mal is), UTF-16 BOM-mal, végül cp1250; bináris tartalom elutasítva."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise KBError("%s: hibás UTF-16 szöveg (%s)" % (path, exc))
    if b"\x00" in data:
        raise KBError("%s: bináris tartalom (nem szövegfájl) — a .txt/.md csak egyszerű szöveg lehet" % path)
    for enc in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise KBError("%s: a szöveg kódolása nem ismerhető fel (UTF-8 szükséges)" % path)


_heading = re.compile(r"^(chapter|fejezet|part|appendix|\d+(\.\d+)*\s+[A-ZÁÉÍÓÖŐÚÜŰ])", re.I)


def _group_paragraphs(paras, target=1400):
    chunks, buf, size, loc = [], [], 0, None
    current = None
    for p in paras:
        s = p.strip()
        if not s:
            continue
        if len(s) < 90 and _heading.match(s):
            current = re.sub(r"\s+", " ", s)[:80]
        if size + len(s) > target and buf:
            chunks.append((loc or current or "", "\n".join(buf)))
            buf, size, loc = [], 0, None
        if loc is None:
            loc = current
        buf.append(s)
        size += len(s) + 1
    if buf:
        chunks.append((loc or current or "", "\n".join(buf)))
    return chunks


def _extract(path):
    """Fájl → [(lokátor, szöveg)] a betöltéshez (üres darabok nélkül)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        pieces = [("p. %d" % (i + 1), t) for i, t in enumerate(pdf_pages(path)) if t.strip()]
    elif ext == ".docx":
        pieces = _group_paragraphs(docx_paragraphs(path))
    elif ext in (".txt", ".md"):
        pieces = _group_paragraphs(_read_text(path).replace("\r\n", "\n").replace("\r", "\n").split("\n\n"))
    else:
        raise KBError(_unsupported_msg(path))
    out = []
    for loc, text in pieces:
        text = re.sub(r"[ \t]{3,}", "  ", text).strip()
        if text:
            out.append((loc, text))
    if not out:
        raise KBError("%s: nem nyerhető ki szöveg (szkennelt PDF esetén előbb OCR szükséges)" % path)
    return out


def _unsupported_msg(path):
    return ("Nem támogatott formátum: %s (támogatott: PDF, DOCX, TXT, MD; .doc/.xls(x)/.ppt(x)/.rtf/.odt "
            "esetén előbb mentsd PDF-be vagy DOCX-be)." % path)


def _hint_matches(hint, base):
    """file_hint egyezés: csak elég specifikus minta (legalább 6 karakter, vagy elválasztót tartalmaz,
    pl. '978-981', '_khan_'), és csak szó/token-határon (a 'khan' nem illeszkedik a 'Khanna'-ra)."""
    h = (hint or "").strip().lower()
    if not h:
        return False
    if len(h) < 6 and not re.search(r"[^a-z0-9]", h):
        return False
    pre = r"(?<![a-z0-9])" if h[0].isalnum() else ""
    post = r"(?![a-z0-9])" if h[-1].isalnum() else ""
    return re.search(pre + re.escape(h) + post, base) is not None


def _match_source(con, path):
    base = os.path.basename(path).lower()
    for row in con.execute("SELECT source_id, file_hint FROM source WHERE file_hint IS NOT NULL ORDER BY source_id"):
        for hint in (row["file_hint"] or "").split("|"):
            if _hint_matches(hint, base):
                return row["source_id"]
    return None


def _slug(path):
    return re.sub(r"[^a-z0-9]+", "_", os.path.splitext(os.path.basename(path))[0].lower()).strip("_")[:40] or "forras"


def _same_document_text(con, sid, pieces):
    """Régi (nyilvántartás nélküli) teljes szövegnél: ugyanaz a dokumentum-e? A szavak multihalmazának
    legalább 90%-os átfedése (a kinyerési javítások — pl. tabulátor, szövegdoboz — miatti apró
    eltérések megengedettek, egy másik dokumentum nem)."""
    old = collections.Counter(re.findall(r"\w+", " ".join(
        r[0] for r in con.execute("SELECT text FROM chunk WHERE source_id=?", (sid,))).lower()))
    new = collections.Counter(re.findall(r"\w+", " ".join(t for _, t in pieces).lower()))
    if not old or not new:
        return False
    return sum((old & new).values()) >= 0.9 * max(sum(old.values()), sum(new.values()))


def _same_path(stored, path):
    return bool(stored) and os.path.normcase(stored) == os.path.normcase(os.path.abspath(path))


def _can_take(con, sid, path, digest, pieces, claimed):
    """Kaphatja-e a fájl a sid forrást úgy, hogy közben más dokumentum szövege ne vesszen el?
    Ugyanaz a dokumentum: azonos tartalom-hash, azonos útvonal (a fájl új változata), vagy egyfájlos /
    nyilvántartás nélküli régi forrásnál legalább 90%-ban azonos szöveg. Az azonos fájlnév nem elég
    (fulltext.pdf, download.pdf más mappából más dokumentum)."""
    if sid in claimed:
        return False, "a(z) '%s' forrást ebben a futásban már egy másik fájl kapta (%s)" % (
            sid, os.path.basename(claimed[sid]))
    n = con.execute("SELECT COUNT(*) FROM chunk WHERE source_id=?", (sid,)).fetchone()[0]
    if not n:
        return True, None
    recs = [(r[0], r[1]) for r in con.execute("SELECT sha256, path FROM ingest_file WHERE source_id=?", (sid,))]
    if not recs:   # régebbi betöltés: a felhasználói forrás sorában van a hash
        row = con.execute("SELECT notes, kind FROM source WHERE source_id=?", (sid,)).fetchone()
        if row is not None and row["kind"] == "user" and (row["notes"] or "").startswith("sha256="):
            recs = [((row["notes"] or "")[7:].strip(), None)]
    if any(r[0] == digest or _same_path(r[1], path) for r in recs):
        return True, None          # ugyanaz a fájl (vagy annak új változata) → újratöltés
    if len(recs) <= 1 and _same_document_text(con, sid, pieces):
        return True, None
    return False, "a(z) '%s' forrás már egy másik dokumentum teljes szövegét tartalmazza" % sid


def _resolve_source(con, path, digest, pieces, claimed, use_hint=True):
    """Automatikus forrás-azonosító: file_hint (token-határon), különben a fájlnévből képzett
    azonosító; ha az már más dokumentumé, saját, hash-utótagos azonosító (és figyelmeztetés)."""
    tried = []
    # ugyanez a fájl (tartalom-hash), vagy ugyanazon az útvonalon korábban betöltött fájl új változata →
    # ugyanoda (átnevezett fájl sem duplikálódik; többfájlos forrásban csak ennek a fájlnak a szövege cserélődik)
    row = con.execute("SELECT source_id FROM ingest_file WHERE sha256=? ORDER BY source_id", (digest,)).fetchone()
    if row:
        return row[0], None
    for sid, stored in con.execute("SELECT source_id, path FROM ingest_file WHERE path IS NOT NULL "
                                   "ORDER BY source_id").fetchall():
        if _same_path(stored, path):
            return sid, None
    hint_sid = _match_source(con, path) if use_hint else None
    for sid in ([hint_sid] if hint_sid else []) + [_slug(path)]:
        ok, why = _can_take(con, sid, path, digest, pieces, claimed)
        if ok:
            note = None
            if tried:
                note = "%s; ezért saját forrásként: %s" % (tried[0], sid)
            return sid, note
        tried.append(why)
    sid = "%s_%s" % (_slug(path)[:31], digest[:8])
    return sid, "%s; ezért saját forrásként: %s (ha ugyanaz a dokumentum, add meg a --source-id-t)" % (tried[0], sid)


def _delete_chunks(con, sid, shas=None):
    """A forrás szövegrészeinek és nyilvántartásának törlése; shas esetén csak az ezekből a fájlokból származóké."""
    cond, args = "", [sid]
    if shas is not None:
        cond = " IN (%s)" % ",".join("?" * len(shas))
        args += list(shas)
    cwhere = "source_id=?" + (" AND file_sha256" + cond if cond else "")
    for row in con.execute("SELECT chunk_id, text, locator FROM chunk WHERE " + cwhere, args).fetchall():
        con.execute("INSERT INTO chunk_fts(chunk_fts, rowid, text, locator) VALUES('delete', ?, ?, ?)",
                    (row["chunk_id"], row["text"], row["locator"] or ""))
    con.execute("DELETE FROM chunk WHERE " + cwhere, args)
    con.execute("DELETE FROM ingest_file WHERE source_id=?" + (" AND sha256" + cond if cond else ""), args)


def _stored_pieces(con, sid, shas=None):
    sql, args = "SELECT locator, text FROM chunk WHERE source_id=?", [sid]
    if shas is not None:
        sql += " AND file_sha256 IN (%s)" % ",".join("?" * len(shas))
        args += list(shas)
    return [(r[0] or "", r[1]) for r in con.execute(sql + " ORDER BY seq", args)]


def _replace_file(con, sid, f, digest, pieces):
    """Automatikus (nem --source-id) betöltés: a fájl korábbi szövegének cseréje a sid forrásban.
    Egyfájlos forrásnál a forrás teljes szövege cserélődik; többfájlos forrásnál (pl. egy mappa
    --source-id-vel) csak ennek a fájlnak a szövegrészei. Változatlan szövegnél semmi nem törlődik
    (a szövegrészek és azonosítóik megmaradnak). Visszaad: (beszúrandó-e, megjegyzés)."""
    recs = con.execute("SELECT sha256, filename, path FROM ingest_file WHERE source_id=?", (sid,)).fetchall()
    mine = [r for r in recs if r["sha256"] == digest or _same_path(r["path"], f)]
    others = [r for r in recs if r not in mine]
    new = [(loc or "", text) for loc, text in pieces]
    multi = bool(mine and others)
    shas = [r["sha256"] for r in mine] if multi else None
    if multi and con.execute("SELECT COUNT(*) FROM chunk WHERE source_id=? AND file_sha256 IS NULL",
                             (sid,)).fetchone()[0]:
        # régi betöltés: nem tudni, melyik szövegrész melyik fájlé — csak a változatlan fájl fogadható el
        if digest in shas:
            return False, None
        dirs = sorted({os.path.dirname(r["path"]) for r in recs if r["path"]})
        raise KBError("a(z) '%s' forrás több fájlból áll (%s), és a korábbi betöltés nem fájlonkénti — a "
                      "módosult fájlt a teljes mappával töltsd be újra: kb ingest %s --source-id %s" % (
                          sid, ", ".join(r["filename"] for r in recs), dirs[0] if len(dirs) == 1 else "<mappa>", sid))
    if _stored_pieces(con, sid, shas) == new:
        cond = (" IN (%s)" % ",".join("?" * len(shas))) if multi else ""
        con.execute("UPDATE chunk SET file_sha256=? WHERE source_id=?" + (" AND file_sha256" + cond if multi else ""),
                    [digest, sid] + (shas or []))
        con.execute("DELETE FROM ingest_file WHERE source_id=?" + (" AND sha256" + cond if multi else ""),
                    [sid] + (shas or []))
        return False, None
    old = [r for r in (mine if multi else recs) if r["sha256"] != digest]
    note = ("korábbi változat felülírva: %s" % ", ".join(
        "%s (sha %s)" % (r["filename"], r["sha256"][:8]) for r in old)) if old else None
    _delete_chunks(con, sid, shas)
    if note and not multi:
        con.execute("UPDATE source SET short=?, notes=? WHERE source_id=? AND kind='user'",
                    (os.path.basename(f), "sha256=%s" % digest, sid))
    return True, note


def _list_files(path):
    if os.path.isdir(path):
        files, skipped = [], []
        for root, dirs, names in os.walk(path):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for n in sorted(names):
                if n.startswith(".") or n.startswith("~$"):
                    continue
                full = os.path.join(root, n)
                (files if os.path.splitext(n)[1].lower() in SUPPORTED_EXT else skipped).append(full)
        return sorted(files), sorted(skipped)
    if not os.path.isfile(path):
        raise KBError("Nincs ilyen fájl vagy mappa: %s" % path)
    if os.path.splitext(path)[1].lower() not in SUPPORTED_EXT:
        raise KBError(_unsupported_msg(path))
    return [path], []


def ingest(path, source_id=None, citation=None, db=None, replace=True, report=None):
    """Egy fájl vagy mappa teljes szövegének betöltése. Visszaad: [(source_id, chunk-szám, fájl)];
    kihagyott vagy hibás fájlnál a source_id None, és a fájlnév mögött zárójelben az ok.

    - Forrás-azonosító: --source-id esetén mind ide kerül (a forrás meglévő szövege a futás elején
      egyszer törlődik, a további fájlok hozzáfűződnek). Különben a korábban betöltött fájl (azonos
      tartalom vagy azonos útvonal) a régi forrásába kerül, egyébként fájlonként saját forrás: file_hint
      (token-határon) vagy a fájlnévből képzett azonosító — más dokumentum szövegét sosem írja felül
      csendben (az azonos fájlnév nem elég); ütközéskor hash-utótagos saját azonosítót kap, és ezt jelzi.
    - Újratöltés (--source-id nélkül): változatlan szövegnél semmi nem változik (a stabil
      '<forrás>#<sorszám>' hivatkozások is megmaradnak); többfájlos forrásban csak az adott fájl
      szövege cserélődik; az azonos útvonalon lévő új változat felülírja a régit, és ezt jelzi.
    - Egy hibás fájl nem állítja le a többit: fájlonként külön tranzakció.
    - Csak PDF/DOCX/TXT/MD (a kiterjesztés kis-/nagybetűtől függetlenül); más formátum elutasítva.
    - report: ha lista, fájlonként {"file","source_id","chunks","status","message"} kerül bele
      (status: ok | duplicate | skipped | error)."""
    db = db or DEFAULT_DB
    ensure_built(db)
    files, skipped = _list_files(path)
    rep = report if report is not None else []
    results = []
    for f in skipped:
        msg = "nem támogatott formátum, kihagyva"
        results.append((None, 0, "%s (%s)" % (f, msg)))
        rep.append({"file": f, "source_id": None, "chunks": 0, "status": "skipped", "message": msg})
    con = connect(db)
    con.isolation_level = None
    seen_hash, claimed = {}, {}
    explicit = bool(source_id)
    try:
        for f in files:
            con.execute("SAVEPOINT ingest_one")
            try:
                digest = _sha256(f)
                if digest in seen_hash:
                    msg = "duplikátum: azonos tartalom, mint %s — kihagyva" % os.path.basename(seen_hash[digest])
                    con.execute("RELEASE ingest_one")
                    results.append((None, 0, "%s (%s)" % (f, msg)))
                    rep.append({"file": f, "source_id": None, "chunks": 0, "status": "duplicate", "message": msg})
                    continue
                pieces = _extract(f)   # előbb a kinyerés: hiba esetén semmi nem törlődik
                note = None
                if explicit:
                    sid = source_id
                    do_replace = replace and sid not in claimed
                else:
                    # --citation: a felhasználó saját dokumentumként jelöli → nincs file_hint-egyezés
                    sid, note = _resolve_source(con, f, digest, pieces, claimed, use_hint=not citation)
                    do_replace = False
                base = os.path.basename(f)
                row = con.execute("SELECT kind FROM source WHERE source_id=?", (sid,)).fetchone()
                if row is None:
                    con.execute("INSERT INTO source (source_id, citation, short, kind, license_note, notes) VALUES (?,?,?,?,?,?)",
                                (sid, citation or base, base, "user", "helyben betöltött teljes szöveg", "sha256=%s" % digest))
                elif citation and row["kind"] == "user":
                    con.execute("UPDATE source SET citation=? WHERE source_id=?", (citation, sid))
                elif citation:
                    note = ((note + "; ") if note else "") + \
                        "a(z) '%s' a seed-ből származó forrás, a --citation nem írja felül" % sid
                insert = True
                if do_replace:
                    _delete_chunks(con, sid)
                elif replace and not explicit:
                    insert, replaced = _replace_file(con, sid, f, digest, pieces)
                    if replaced:
                        note = ((note + "; ") if note else "") + replaced
                seq0 = con.execute("SELECT COALESCE(MAX(seq) + 1, 0) FROM chunk WHERE source_id=?", (sid,)).fetchone()[0]
                for i, (loc, text) in enumerate(pieces if insert else []):
                    cur = con.execute("INSERT INTO chunk (source_id, seq, locator, text, file_sha256) VALUES (?,?,?,?,?)",
                                      (sid, seq0 + i, loc, text, digest))
                    con.execute("INSERT INTO chunk_fts(rowid, text, locator) VALUES (?,?,?)", (cur.lastrowid, text, loc or ""))
                con.execute("INSERT OR REPLACE INTO ingest_file (source_id, sha256, filename, path, chunks, ts) "
                            "VALUES (?,?,?,?,?,?)", (sid, digest, base, os.path.abspath(f), len(pieces),
                                                     datetime.datetime.now().replace(microsecond=0).isoformat()))
                con.execute("RELEASE ingest_one")   # fájlonként véglegesítve
                claimed[sid] = f
                seen_hash[digest] = f
                results.append((sid, len(pieces), f + (" (FIGYELEM: %s)" % note if note else "")))
                rep.append({"file": f, "source_id": sid, "chunks": len(pieces), "status": "ok", "message": note})
            except (KBError, OSError, sqlite3.Error, ValueError) as exc:
                con.execute("ROLLBACK TO ingest_one")
                con.execute("RELEASE ingest_one")
                msg = str(exc)
                results.append((None, 0, "%s (HIBA: %s)" % (f, msg)))
                rep.append({"file": f, "source_id": None, "chunks": 0, "status": "error", "message": msg})
    finally:
        con.close()
    return results


# --------------------------------------------------------------- lekérdezés
def _fts_query(q):
    """Felhasználói keresőkifejezés → biztonságos FTS5 lekérdezés (szavak ÉS-kapcsolata,
    idézőjeles kifejezések megtartva, előtag-keresés a *-gal)."""
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]+"', " ", q)
    terms = []
    for p in phrases:
        terms.append('"%s"' % p.replace('"', ""))
    for w in re.findall(r"[\wáéíóöőúüűÁÉÍÓÖŐÚÜŰ²τ*-]+", rest):
        w = w.strip("-")
        if not w or w.upper() in ("AND", "OR", "NOT", "NEAR"):
            continue
        star = w.endswith("*")
        w = w.rstrip("*")
        if not w:
            continue
        terms.append('"%s"%s' % (w, "*" if star else ""))
    return " ".join(terms)


def search(query, limit=8, db=None, scopes=("rule", "knowledge", "chunk"), source=None):
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    fq = _fts_query(query)
    out = {}
    if not fq:
        con.close()
        return out
    if "rule" in scopes:
        sql = ("SELECT r.rule_id AS id, r.stage_id, r.strength, r.applies_to, r.condition, r.recommendation, "
               "r.source_ids, r.locator, bm25(rule_fts) AS score FROM rule_fts JOIN decision_rule r "
               "ON r.rule_id = rule_fts.rule_id WHERE rule_fts MATCH ?")
        args = [fq]
        if source:
            # a szabály forrásai vesszős listában: csak a pontos elem illeszkedik (a --limit így a szűrt sorokat számolja)
            sql += " AND instr(',' || REPLACE(COALESCE(r.source_ids, ''), ' ', '') || ',', ',' || ? || ',') > 0"
            args.append(source)
        out["rule"] = [dict(r) for r in con.execute(sql + " ORDER BY score LIMIT ?", args + [limit])]
    if "knowledge" in scopes:
        sql = ("SELECT k.k_id AS id, k.stage_id, k.kind, k.title, snippet(knowledge_fts, 2, '[', ']', ' … ', 24) AS snippet, "
               "k.source_id, k.locator, bm25(knowledge_fts) AS score FROM knowledge_fts JOIN knowledge k "
               "ON k.k_id = knowledge_fts.k_id WHERE knowledge_fts MATCH ?")
        args = [fq]
        if source:
            sql += " AND k.source_id = ?"
            args.append(source)
        out["knowledge"] = [dict(r) for r in con.execute(sql + " ORDER BY score LIMIT ?", args + [limit])]
    if "chunk" in scopes:
        # ref: stabil hivatkozás (<forrás>#<sorszám>); az id (chunk_id) újratöltéskor változhat
        sql = ("SELECT c.chunk_id AS id, c.source_id || '#' || c.seq AS ref, c.source_id, c.seq, c.locator, "
               "snippet(chunk_fts, 0, '[', ']', ' … ', 30) AS snippet, "
               "bm25(chunk_fts) AS score FROM chunk_fts JOIN chunk c ON c.chunk_id = chunk_fts.rowid "
               "WHERE chunk_fts MATCH ?")
        args = [fq]
        if source:
            sql += " AND c.source_id = ?"
            args.append(source)
        out["chunk"] = [dict(r) for r in con.execute(sql + " ORDER BY score LIMIT ?", args + [limit])]
    con.close()
    return out


_CHUNK_REF = re.compile(r"(.+)#(\d+)")


def _lookup(con, item_id):
    """Azonosító → a tétel sora (és '_table'). Szövegrész: a stabil '<forrás>#<sorszám>' alak, vagy a
    kb search '#<chunk_id>'-ja (ez újratöltéskor változhat); szövegrésznél a 'ref' a stabil alak."""
    for table, key in ID_TABLES:
        row = con.execute("SELECT * FROM %s WHERE %s = ?" % (table, key), (item_id,)).fetchone()
        if row:
            d = dict(row)
            d["_table"] = table
            return d
    m = _CHUNK_REF.fullmatch(item_id)
    if m:
        row = con.execute("SELECT * FROM chunk WHERE source_id = ? AND seq = ?", (m.group(1), int(m.group(2)))).fetchone()
    else:
        cid = item_id.lstrip("#")
        row = con.execute("SELECT * FROM chunk WHERE chunk_id = ?", (int(cid),)).fetchone() if cid.isdigit() else None
    if row:
        d = dict(row)
        d["_table"] = "chunk"
        d["ref"] = "%s#%d" % (d["source_id"], d["seq"])
        return d
    return None


def show(item_id, db=None):
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    try:
        return _lookup(con, item_id)
    finally:
        con.close()


def canonical_ids(ids, db=None):
    """azonosító → tárolható alakja: a szövegrész stabil '<forrás>#<sorszám>' alakban (a chunk_id
    újratöltéskor változik), a többi változatlanul; ismeretlen azonosítónál None."""
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    try:
        out = {}
        for i in ids:
            item = _lookup(con, i)
            out[i] = None if item is None else (item["ref"] if item["_table"] == "chunk" else i)
        return out
    finally:
        con.close()


def existing_ids(ids, db=None):
    """A megadott azonosítók közül azok halmaza, amelyek léteznek a tudásbázisban
    (szabály, tudás, képlet, ellenőrzőlista-tétel, eszköz, példa, forrás, szakasz, szövegrész)."""
    return {i for i, c in canonical_ids(ids, db).items() if c is not None}


def normalize_stage(stage):
    """'s5' / 'S05' / '5' → 'S05'; egyéb érték változatlanul (nagybetűsítve)."""
    s = str(stage).strip().upper()
    m = re.fullmatch(r"S?(\d{1,2})", s)
    return "S%02d" % int(m.group(1)) if m else s


def stage_ids(db=None):
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    try:
        return [r[0] for r in con.execute("SELECT stage_id FROM stage ORDER BY ord, stage_id")]
    finally:
        con.close()


def checklist_names(db=None):
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    try:
        return [r[0] for r in con.execute("SELECT DISTINCT checklist FROM checklist_item ORDER BY 1")]
    finally:
        con.close()


def rules(stage=None, applies_to=None, db=None):
    """Döntési szabályok szakasz és/vagy szerep szerint. A reviewer a motor validálási
    szabályait (V-kódok, applies_to='engine') is látja, mert ezeket ellenőrzi."""
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    sql = "SELECT * FROM v_rules WHERE 1=1"
    args = []
    if stage:
        sql += " AND stage_id = ?"
        args.append(normalize_stage(stage))
    if applies_to:
        roles = [applies_to, "all"] + (["engine"] if applies_to == "reviewer" else [])
        sql += " AND applies_to IN (%s)" % ",".join("?" * len(roles))
        args += roles
    rows = [dict(r) for r in con.execute(sql + " ORDER BY stage_id, rule_id", args)]
    con.close()
    return rows


def checklist(name, db=None):
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM checklist_item WHERE checklist = ? COLLATE NOCASE ORDER BY ord, item_id", (name.strip(),))]
    con.close()
    return rows


_READONLY_SQL = re.compile(r"^\s*(select|with|pragma\s+table_info|explain)\b", re.I)


def query(sql, params=(), db=None, max_rows=500, timeout=10.0, info=None):
    """Csak-olvasó SQL (SELECT/WITH). Az adatbázis read-only módban nyílik meg.
    max_rows: legfeljebb ennyi sor (0/None = korlát nélkül; negatív: KBError); timeout: másodperc
    (0/None/negatív = nincs), utána a lekérdezés megszakad (KBError). info: ha dict, ide kerül
    {"truncated": bool, "max_rows": n}."""
    if not _READONLY_SQL.match(sql):
        raise KBError("Csak SELECT / WITH lekérdezés engedélyezett.")
    if max_rows is not None and max_rows < 0:
        raise KBError("max_rows >= 0 szükséges (0 = korlát nélkül), kapott: %r" % (max_rows,))
    if timeout is not None and not timeout > 0:
        timeout = None
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    truncated = False
    if timeout:
        deadline = time.monotonic() + float(timeout)
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        if max_rows:
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
        else:
            rows = cur.fetchall()
    except sqlite3.OperationalError as exc:
        if timeout and "interrupt" in str(exc).lower():
            raise KBError("A lekérdezés túllépte az időkorlátot (%g s), megszakítva — szűkítsd (WHERE/LIMIT)." % float(timeout))
        raise
    finally:
        con.close()
    if info is not None:
        info["truncated"] = truncated
        info["max_rows"] = max_rows
    return cols, [tuple(r) for r in rows]


def stats(db=None):
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    out = {}
    for t in ("source", "stage", "knowledge", "formula", "decision_rule", "checklist_item", "tool",
              "worked_example", "chunk"):
        out[t] = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    out["fulltext_by_source"] = {r[0]: r[1] for r in con.execute(
        "SELECT source_id, COUNT(*) FROM chunk GROUP BY source_id ORDER BY source_id")}
    con.close()
    return out
