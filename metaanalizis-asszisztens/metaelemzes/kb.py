# -*- coding: utf-8 -*-
"""Tudásbázis (SQLite + FTS5): építés a seed JSON-okból, teljes szöveg betöltése helyi
PDF/DOCX/TXT/MD fájlokból, keresés és csak-olvasó SQL az ágensek döntéseihez.

Elérési utak:
  tudasbazis/schema.sql         — séma
  tudasbazis/seed/*.json         — verziókövetett, saját szavas tudás
  tudasbazis/tudasbazis.sqlite   — a felépített adatbázis (gitignore; `kb build` újraépíti)
  tudasbazis/forrasok/           — a helyi forrásfájlok (gitignore; szerzői jog)
"""
import glob
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import zipfile
from xml.etree import ElementTree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KB_DIR = os.path.join(ROOT, "tudasbazis")
SCHEMA = os.path.join(KB_DIR, "schema.sql")
SEED_DIR = os.path.join(KB_DIR, "seed")
DEFAULT_DB = os.environ.get("METAELEMZES_KB") or os.path.join(KB_DIR, "tudasbazis.sqlite")

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


class KBError(RuntimeError):
    pass


def connect(path=None, readonly=False):
    path = path or DEFAULT_DB
    if readonly:
        if not os.path.exists(path):
            raise KBError("A tudásbázis még nincs felépítve: futtasd a `kb build` parancsot.")
        uri = "file:%s?mode=ro" % path.replace("\\", "/")
        con = sqlite3.connect(uri, uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _seed_files(pattern):
    return sorted(glob.glob(os.path.join(SEED_DIR, pattern)))


def _seed_fingerprint():
    h = hashlib.sha256()
    for f in sorted(glob.glob(os.path.join(SEED_DIR, "*.json"))) + [SCHEMA]:
        with open(f, "rb") as fh:
            h.update(os.path.basename(f).encode("utf-8"))
            h.update(fh.read())
    return h.hexdigest()


def _jsonify(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def build(path=None, keep_fulltext=True):
    """(Újra)építi a strukturált táblákat a seed-ekből. A teljes szöveg (chunk) megmarad."""
    path = path or DEFAULT_DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = connect(path)
    with open(SCHEMA, encoding="utf-8") as fh:
        con.executescript(fh.read())
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys = OFF")
    for table in ("knowledge_fts", "rule_fts"):
        cur.execute("DELETE FROM %s" % table)
    for _, table, _ in reversed(SEED_TABLES):
        if table == "source" and keep_fulltext:
            continue
        cur.execute("DELETE FROM %s" % table)
    counts = {}
    for pattern, table, cols in SEED_TABLES:
        n = 0
        for f in _seed_files(pattern):
            with open(f, encoding="utf-8") as fh:
                try:
                    items = json.load(fh)
                except ValueError as exc:
                    raise KBError("Hibás JSON: %s (%s)" % (f, exc))
            for it in items:
                missing = [c for c in cols[:1] if not it.get(c)]
                if missing:
                    raise KBError("%s: hiányzó azonosító (%s) egy tételnél" % (os.path.basename(f), cols[0]))
                vals = [_jsonify(it.get(c)) for c in cols]
                verb = "INSERT OR REPLACE"
                cur.execute("%s INTO %s (%s) VALUES (%s)" % (verb, table, ",".join(cols), ",".join("?" * len(cols))), vals)
                n += 1
        counts[table] = n
    # a motor adatvalidálási szabályai is szabályok (egy igazságforrás)
    from .validate import RULES
    for code, (sev, title, advice, src) in RULES.items():
        cur.execute("INSERT OR REPLACE INTO decision_rule (rule_id, stage_id, applies_to, condition, recommendation, "
                    "rationale, strength, machine_check, source_ids, locator) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (code, "S05", "engine", title, advice, "Adatvalidálási szabály (%s)" % sev,
                     "must" if sev == "error" else ("should" if sev == "warning" else "consider"),
                     "metaelemzes.validate:%s" % code, "engine", src))
    cur.execute("INSERT INTO knowledge_fts (k_id, title, body, tags) SELECT k_id, title, body, COALESCE(tags,'') FROM knowledge")
    cur.execute("INSERT INTO rule_fts (rule_id, condition, recommendation, rationale) "
                "SELECT rule_id, condition, recommendation, COALESCE(rationale,'') FROM decision_rule")
    cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seed_fingerprint', ?)", (_seed_fingerprint(),))
    con.commit()
    bad = cur.execute("PRAGMA foreign_key_check").fetchall()
    cur.execute("PRAGMA foreign_keys = ON")
    con.close()
    counts["foreign_key_problems"] = len(bad)
    return counts


def ensure_built(path=None):
    path = path or DEFAULT_DB
    if not os.path.exists(path):
        build(path)
        return True
    try:
        con = connect(path, readonly=True)
        row = con.execute("SELECT value FROM meta WHERE key='seed_fingerprint'").fetchone()
        con.close()
    except sqlite3.Error:
        row = None
    if row is None or row[0] != _seed_fingerprint():
        build(path)
        return True
    return False


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
        import pypdf  # noqa: F401
        reader = pypdf.PdfReader(path)
        return [(p.extract_text() or "") for p in reader.pages]
    except ImportError:
        pass
    try:
        txt = subprocess.run(["pdftotext", "-layout", path, "-"], check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        raise KBError("PDF-hez telepítsd a pypdf csomagot (pip install pypdf) vagy a pdftotext-et (poppler).")
    return txt.decode("utf-8", "replace").split("\f")


def docx_paragraphs(path):
    """DOCX → bekezdések (táblázatok sorai ' | ' elválasztással), csak standard könyvtárral."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    w = "{%s}" % ns["w"]
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    body = root.find("w:body", ns)
    out = []
    for child in body:
        if child.tag == w + "p":
            out.append("".join(t.text or "" for t in child.iter(w + "t")))
        elif child.tag == w + "tbl":
            out.append("[TÁBLÁZAT]")
            for tr in child.iter(w + "tr"):
                cells = [" ".join((t.text or "") for t in tc.iter(w + "t")).strip() for tc in tr.iter(w + "tc")]
                out.append(" | ".join(cells))
            out.append("[/TÁBLÁZAT]")
    return out


_heading = re.compile(r"^(chapter|fejezet|part|appendix|\d+(\.\d+)*\s+[A-ZÁÉÍÓÖŐÚÜŰ])", re.I)


def _group_paragraphs(paras, target=1400):
    chunks, buf, size, loc = [], [], 0, None
    current = None
    for p in paras:
        s = p.strip()
        if not s:
            continue
        if len(s) < 90 and _heading.match(s):
            current = s[:80]
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


def _match_source(con, path):
    base = os.path.basename(path).lower()
    for row in con.execute("SELECT source_id, file_hint FROM source WHERE file_hint IS NOT NULL"):
        for hint in (row["file_hint"] or "").split("|"):
            if hint and hint.lower() in base:
                return row["source_id"]
    return None


def ingest(path, source_id=None, citation=None, db=None, replace=True):
    """Egy fájl vagy mappa teljes szövegének betöltése. Visszaad: [(source_id, chunk-szám, fájl)]."""
    db = db or DEFAULT_DB
    ensure_built(db)
    files = []
    if os.path.isdir(path):
        for ext in ("*.pdf", "*.docx", "*.txt", "*.md"):
            files += glob.glob(os.path.join(path, "**", ext), recursive=True)
    else:
        files = [path]
    con = connect(db)
    results = []
    seen_hash = set()
    for f in sorted(files):
        digest = _sha256(f)
        if digest in seen_hash:
            results.append((None, 0, f + " (duplikátum, kihagyva)"))
            continue
        seen_hash.add(digest)
        sid = source_id or _match_source(con, f)
        if sid is None:
            sid = re.sub(r"[^a-z0-9]+", "_", os.path.splitext(os.path.basename(f))[0].lower()).strip("_")[:40] or "forras"
        if con.execute("SELECT 1 FROM source WHERE source_id=?", (sid,)).fetchone() is None:
            con.execute("INSERT INTO source (source_id, citation, short, kind, license_note, notes) VALUES (?,?,?,?,?,?)",
                        (sid, citation or os.path.basename(f), os.path.basename(f), "user",
                         "helyben betöltött teljes szöveg", "sha256=%s" % digest))
        ext = os.path.splitext(f)[1].lower()
        if ext == ".pdf":
            pieces = [("p. %d" % (i + 1), t) for i, t in enumerate(pdf_pages(f)) if t.strip()]
        elif ext == ".docx":
            pieces = _group_paragraphs(docx_paragraphs(f))
        else:
            with open(f, encoding="utf-8", errors="replace") as fh:
                pieces = _group_paragraphs(fh.read().split("\n\n"))
        if replace:
            ids = [r[0] for r in con.execute("SELECT chunk_id FROM chunk WHERE source_id=?", (sid,))]
            for cid in ids:
                row = con.execute("SELECT text, locator FROM chunk WHERE chunk_id=?", (cid,)).fetchone()
                con.execute("INSERT INTO chunk_fts(chunk_fts, rowid, text, locator) VALUES('delete', ?, ?, ?)",
                            (cid, row["text"], row["locator"] or ""))
            con.execute("DELETE FROM chunk WHERE source_id=?", (sid,))
        for seq, (loc, text) in enumerate(pieces):
            text = re.sub(r"[ \t]{3,}", "  ", text).strip()
            if not text:
                continue
            cur = con.execute("INSERT INTO chunk (source_id, seq, locator, text) VALUES (?,?,?,?)", (sid, seq, loc, text))
            con.execute("INSERT INTO chunk_fts(rowid, text, locator) VALUES (?,?,?)", (cur.lastrowid, text, loc or ""))
        results.append((sid, len(pieces), f))
    con.commit()
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
        return out
    if "rule" in scopes:
        out["rule"] = [dict(r) for r in con.execute(
            "SELECT r.rule_id AS id, r.stage_id, r.strength, r.applies_to, r.condition, r.recommendation, "
            "r.source_ids, r.locator, bm25(rule_fts) AS score FROM rule_fts JOIN decision_rule r "
            "ON r.rule_id = rule_fts.rule_id WHERE rule_fts MATCH ? ORDER BY score LIMIT ?", (fq, limit))]
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
        sql = ("SELECT c.chunk_id AS id, c.source_id, c.locator, snippet(chunk_fts, 0, '[', ']', ' … ', 30) AS snippet, "
               "bm25(chunk_fts) AS score FROM chunk_fts JOIN chunk c ON c.chunk_id = chunk_fts.rowid "
               "WHERE chunk_fts MATCH ?")
        args = [fq]
        if source:
            sql += " AND c.source_id = ?"
            args.append(source)
        out["chunk"] = [dict(r) for r in con.execute(sql + " ORDER BY score LIMIT ?", args + [limit])]
    con.close()
    return out


def show(item_id, db=None):
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    for table, key in (("decision_rule", "rule_id"), ("knowledge", "k_id"), ("formula", "formula_id"),
                       ("checklist_item", "item_id"), ("tool", "tool_id"), ("worked_example", "example_id"),
                       ("source", "source_id"), ("stage", "stage_id")):
        row = con.execute("SELECT * FROM %s WHERE %s = ?" % (table, key), (item_id,)).fetchone()
        if row:
            d = dict(row)
            d["_table"] = table
            con.close()
            return d
    if item_id.isdigit():
        row = con.execute("SELECT * FROM chunk WHERE chunk_id = ?", (int(item_id),)).fetchone()
        if row:
            d = dict(row)
            d["_table"] = "chunk"
            con.close()
            return d
    con.close()
    return None


def rules(stage=None, applies_to=None, db=None):
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    sql = "SELECT * FROM v_rules WHERE 1=1"
    args = []
    if stage:
        sql += " AND stage_id = ?"
        args.append(stage)
    if applies_to:
        sql += " AND (applies_to = ? OR applies_to = 'all')"
        args.append(applies_to)
    rows = [dict(r) for r in con.execute(sql + " ORDER BY stage_id, rule_id", args)]
    con.close()
    return rows


def checklist(name, db=None):
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    rows = [dict(r) for r in con.execute("SELECT * FROM checklist_item WHERE checklist = ? ORDER BY ord, item_id", (name,))]
    con.close()
    return rows


_READONLY_SQL = re.compile(r"^\s*(select|with|pragma\s+table_info|explain)\b", re.I)


def query(sql, params=(), db=None, max_rows=500):
    """Csak-olvasó SQL (SELECT/WITH). Az adatbázis read-only módban nyílik meg."""
    if not _READONLY_SQL.match(sql):
        raise KBError("Csak SELECT / WITH lekérdezés engedélyezett.")
    db = db or DEFAULT_DB
    ensure_built(db)
    con = connect(db, readonly=True)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows)
    finally:
        con.close()
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
