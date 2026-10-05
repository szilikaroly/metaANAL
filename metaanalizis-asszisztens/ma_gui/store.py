# -*- coding: utf-8 -*-
"""Projektfájlok tárolója a munkapadhoz (terv: 2.2, 2.4, 3.1, 4.8, 4.10, 7.3).

- Projekt-CSV nyers, szöveges cellákkal: a formátumot (kódolás és BOM, elválasztó, tizedesjel,
  sorvég, idézés) a motor tableio-jával azonos módon ismeri fel, és visszaíráskor megőrzi; a nem
  módosított sorok bájtra azonosan íródnak vissza (változatlan mentés = azonos fájl).
- row_uid: a CSV `row_uid` oszlopából; ha nincs, determinisztikus 'r' + 6 base32 karakter a
  sha1(címke|sorindex)-ből (4.6). A fájlba csak mentéskor kerül; új sor véletlen uid-ot kap.
- ETag = a fájl bájtjainak sha256-ja; mentés If-Match-csel, eltérésnél Conflict (409) cellaszintű
  diffel. Írás csak a projektgyökér alá, atomikusan (tmp + os.replace); Excel-zárolásnál Locked (423).
- Oldalfájlok: <tábla>.prov.json (szk.ma.provenance/v1), documents.json, studies.json — minimális
  alakellenőrzéssel, atomikus írással.
- ChangeWatcher: sha256/mtime és a projekt.sqlite PRAGMA data_version-je alapján rev-számláló,
  long-pollhoz (wait). Figyeli a mintákat (a _privat/ és a 03_adatok/ almappáit is) és minden
  fájlt, amelyet a tároló megnyitott vagy írt (watch).
- T11: a teljes értelmezés (fejléc-kanonizálás) előtt a fejléc szélessége legfeljebb MAX_COLUMNS
  (különben TooLarge, 413). Ideiglenes fájl: '.ma-tmp-<név>-….tmp' (a kezelt .gitignore-blokk fedi).

Számot nem értelmez és nem számol (a cellák szövegek; a számparszolás a motoré). Cellaértéket
soha nem naplóz és hibaüzenetbe sem tesz (T10): az értékek csak a hívónak visszaadott adatban
(tábla, diff) szerepelnek.
"""
import base64
import codecs
import collections
import csv
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import stat
import tempfile
import threading
import time
from pathlib import Path

from metaelemzes import api as engine_api
from metaelemzes import tableio

from . import security

UID_COLUMN = "row_uid"
UID_RE = re.compile(r"^r[0-9a-z]{4,12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

DOCUMENTS_REL = "03_adatok/documents.json"
PRIVATE_DOCUMENTS_REL = "_privat/documents.json"     # C osztályú projekt dokumentum-jegyzéke
STUDIES_REL = "03_adatok/studies.json"
PROV_SUFFIX = ".prov.json"
DB_NAME = "projekt.sqlite"          # metaelemzes.projekt.db_path
DB_KEY = DB_NAME

PROV_SCHEMA = "szk.ma.provenance/v1"
DOCS_SCHEMA = "szk.ma.documents/v1"
STUDIES_SCHEMA = "szk.ma.studies/v1"
PROV_METHODS = ("reported", "calculated", "estimated", "digitized", "imputed", "author_contact",
                "reconciled", "external_edit")
ESTIMATED_METHODS = ("estimated", "digitized", "imputed")
DOC_ROOTS = ("project", "composer_outdir")

LOCKED_MESSAGE = "Zárd be a fájlt az Excelben, majd próbáld újra."


class _Any(object):
    def __repr__(self):
        return "ANY"


ANY = _Any()                         # if_match: feltétel nélküli írás (új fájlra is)

NEWLINE_NAMES = {"\r\n": "crlf", "\n": "lf"}
QUOTING_STYLES = ("minimal", "strings", "all")
_BOMS = {"utf-8": codecs.BOM_UTF8, "utf-16-le": codecs.BOM_UTF16_LE, "utf-16-be": codecs.BOM_UTF16_BE}
_ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "cp1250", "latin-1")

# os.replace újrapróbálása (Windows: víruskereső / OneDrive rövid zárolása); a tesztek felülírhatják
_REPLACE_RETRY_DELAYS = (0.05, 0.15, 0.4)
_VERSION_CACHE = 32                 # (tábla, etag) → tartalom, a háromutas diffhez
_DIFF_LIMIT = 5000
_EXPECT_TTL = 120.0                 # saját írás várt hash-e ennyi ideig érvényes (s)
_OWN_GRACE = 5.0                    # a saját (worker-) írások előtag-jelölése ennyivel túléli a feladatot (s)
MAX_COLUMNS = security.MAX_COLUMNS  # T11: a fejléc legfeljebb ennyi oszlopos (a kanonizálás előtt)
TMP_PREFIX = security.TMP_PREFIX
TMP_SWEEP_AGE = 300.0               # s; ennél régebbi árva ideiglenes fájl indításkor törölhető
_MAX_WATCHED = 2000                 # a tárolón át megnyitott, külön figyelt fájlok felső korlátja
# egy forrásból a security.check_relpath-szal (COM0, LPT¹, CONIN$ … is)
_WIN_RESERVED = frozenset(n.lower() for n in security.WINDOWS_RESERVED_NAMES)
_BAD_PATH_CHARS = set('<>:"|?*\\') | {chr(i) for i in range(32)}


# ------------------------------------------------------------------ hibák
class StoreError(Exception):
    """A tároló hibája; a router a code/http mezőkből építi a hiba-borítékot (4.2)."""
    code = "INTERNAL"
    http = 500

    def __init__(self, message, details=None, code=None, http=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code
        if http:
            self.http = http

    def to_error(self):
        return {"code": self.code, "http": self.http, "message": self.message, "details": self.details}


class BadRequest(StoreError):
    code, http = "BAD_REQUEST", 400


class Forbidden(StoreError):
    code, http = "FORBIDDEN", 403


class NotFound(StoreError):
    code, http = "NOT_FOUND", 404


class Invalid(StoreError):
    code, http = "VALIDATION", 422


class TooLarge(StoreError):
    code, http = "PAYLOAD_TOO_LARGE", 413


class Locked(StoreError):
    code, http = "LOCKED", 423

    def __init__(self, rel, details=None):
        d = {"path": rel}
        d.update(details or {})
        super().__init__(LOCKED_MESSAGE, d)


class Conflict(StoreError):
    """ETag-eltérés. details: dataset, etag (a mostani), diff (cellaszintű), base_known, truncated.

    A diff elemei: {row_uid, column, base, theirs, mine, conflict}; None = a sor vagy az oszlop
    abban a változatban nem létezik. Az üzenet csak darabszámot tartalmaz, értéket nem."""
    code, http = "CONFLICT", 409

    def __init__(self, rel, etag, diff=None, base_known=False, truncated=False):
        diff = diff or []
        n_conf = sum(1 for d in diff if d.get("conflict"))
        msg = ("A(z) %s időközben megváltozott (például Excelben vagy egy ágens írta át): %d eltérő "
               "cella, ebből %d ütközik a te módosításoddal. Töltsd be újra, vagy fésüld össze a "
               "változásokat." % (rel, len(diff), n_conf))
        super().__init__(msg, {"dataset": rel, "etag": etag, "diff": diff, "base_known": base_known,
                               "truncated": truncated})
        self.diff = diff
        self.etag = etag


def _provenance_conflict(prel, dataset, etag):
    exc = Conflict(prel, etag)
    exc.message = ("Az eredet-oldalfájl (%s) időközben megváltozott (például egy másik lapon vagy egy "
                   "ágens írta). Töltsd be újra az eredetet, majd mentsd újra." % prel)
    exc.args = (exc.message,)
    exc.details.update(kind="provenance", dataset=dataset, path=prel)
    return exc


# ------------------------------------------------------------------ utak
def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    """A fájl sha256-ja, vagy None, ha nem létezik."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except FileNotFoundError:
        return None
    return h.hexdigest()


def is_within(child, parent):
    """child a parent alatt van-e (vagy azonos vele) a szimbolikus linkek feloldása után;
    Windows-on kis/nagybetű-független."""
    c = os.path.normcase(os.path.realpath(str(child)))
    p = os.path.normcase(os.path.realpath(str(parent)))
    if c == p:
        return True
    return c.startswith(p.rstrip("\\/") + os.sep)


def relpath_parts(rel):
    """Projekt-relatív út ('/' elválasztó) részekre bontva; minden szabálytalan alakra Forbidden.

    Tilos: abszolút út, meghajtójel, '\\', '..', '.', üres rész, Windows-eszköznév (CON, NUL…),
    záró pont/szóköz, Windows-on érvénytelen karakter, a '.git' mappa."""
    if not isinstance(rel, str) or not rel or len(rel) > 1024:
        raise Forbidden("Érvénytelen projekt-relatív út.", {"path": None})
    shown = rel if len(rel) <= 200 else rel[:200] + "…"
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise Forbidden("Csak a projektmappán belüli, relatív út engedélyezett: %s" % shown, {"path": shown})
    parts = rel.split("/")
    for part in parts:
        bad = (part in ("", ".", "..") or part != part.rstrip(" .") or part == ".git"
               or any(ch in _BAD_PATH_CHARS for ch in part)
               or part.split(".")[0].strip().lower() in _WIN_RESERVED)
        if bad:
            raise Forbidden("Szabálytalan vagy a projektmappán kívülre mutató út: %s" % shown, {"path": shown})
    return parts


def resolve_under(root, rel):
    """root alatti abszolút út (szimbolikus linkek feloldásával); ha kivezetne, Forbidden."""
    parts = relpath_parts(rel)
    base = os.path.realpath(str(root))
    full = os.path.realpath(os.path.join(base, *parts))
    if not is_within(full, base) or os.path.normcase(full) == os.path.normcase(base):
        raise Forbidden("Az út a projektmappán kívülre mutat: %s" % "/".join(parts), {"path": "/".join(parts)})
    return Path(full)


def provenance_relpath(dataset):
    """03_adatok/o1.csv → 03_adatok/o1.prov.json"""
    head, _, name = dataset.rpartition("/")
    stem = name
    for ext in (".csv", ".tsv", ".txt"):
        if name.lower().endswith(ext):
            stem = name[:-len(ext)]
            break
    return (head + "/" if head else "") + stem + PROV_SUFFIX


def _norm_etag(tag):
    if tag is None:
        return None
    t = str(tag).strip()
    if t.startswith("W/"):
        t = t[2:]
    t = t.strip().strip('"').strip().lower()
    return t or None


def _read_bytes(path, rel):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        raise BadRequest("Ez egy mappa, nem fájl: %s" % rel, {"path": rel})
    except PermissionError:
        raise Locked(rel)


# ------------------------------------------------------------------ row_uid
def _b32(data):
    return base64.b32encode(data).decode("ascii").lower()


def derive_row_uid(label, row_index, taken=()):
    """Determinisztikus uid row_uid oszlop nélküli táblához (4.6) — a motor EGYETLEN uid-függvénye
    (``tableio.row_uid_for``): 'r' + a sha1('<címke>|<sorindex>') base32 alakjának első 6 karaktere,
    a címke a beolvasással azonos tisztításával (NA-jelölő → ''). A sorindex 0-tól számol, az üres
    sorok kihagyásával; ütközésnél '|1', '|2'… utótag. Így a felület, a validálás és a plot/v2
    ugyanazt a row_uid-ot látja."""
    return tableio.row_uid_for(label, row_index, taken)


def column_map(header):
    """Kanonikus oszlopnév → eredeti fejléc a motor felismerésével (``api.column_map`` = ``tableio.column_map``) —
    bájtra ugyanaz, mint a validálási dokumentum ``column_map``-je (ismétlődő fejlécnél is); a felület ezzel
    képezi a mezőket oszlopra a validálás előtt is."""
    return dict(engine_api.column_map(list(header)))


def new_row_uid(taken=()):
    """Új sor véletlen uid-ja: 'r' + 6 base32 karakter (secrets)."""
    while True:
        uid = "r" + _b32(secrets.token_bytes(5))[:6]
        if uid not in taken:
            return uid


# ------------------------------------------------------------------ CSV-formátum
class CsvFormat(object):
    """A fájl írásmódja. encoding: utf-8 | utf-16-le | utf-16-be | cp1250 | latin-1; bom;
    delimiter; decimal_mark ('.', ',' vagy None — a tableio döntése, csak tájékoztató);
    newline ('\\r\\n' | '\\n'); final_newline; quoting: minimal | strings | all (az adatsoroké;
    'strings' = a nem szám cellák idézőjelben, R/pandas-stílus); header_quoting: minimal | all."""
    __slots__ = ("encoding", "bom", "delimiter", "decimal_mark", "newline", "final_newline", "quoting",
                 "header_quoting")

    def __init__(self, encoding="utf-8", bom=True, delimiter=";", decimal_mark=",", newline="\r\n",
                 final_newline=True, quoting="minimal", header_quoting="minimal"):
        if encoding not in _ENCODINGS:
            raise Invalid("Nem támogatott kódolás: %s" % encoding)
        if delimiter not in (",", ";", "\t"):
            raise Invalid("Nem támogatott elválasztó.")
        if newline not in NEWLINE_NAMES:
            raise Invalid("Nem támogatott sorvég.")
        if quoting not in QUOTING_STYLES or header_quoting not in ("minimal", "all"):
            raise Invalid("Nem támogatott idézési mód.")
        self.encoding = encoding
        self.bom = bool(bom) and encoding in _BOMS
        self.delimiter = delimiter
        self.decimal_mark = decimal_mark
        self.newline = newline
        self.final_newline = bool(final_newline)
        self.quoting = quoting
        self.header_quoting = header_quoting

    def to_json(self):
        return {"encoding": self.encoding, "bom": self.bom, "delimiter": self.delimiter,
                "decimal_mark": self.decimal_mark, "newline": NEWLINE_NAMES[self.newline],
                "final_newline": self.final_newline, "quoting": self.quoting,
                "header_quoting": self.header_quoting}

    @classmethod
    def from_json(cls, d):
        if not isinstance(d, dict):
            raise Invalid("A formátum-leírás objektum legyen.")
        nl = {v: k for k, v in NEWLINE_NAMES.items()}.get(d.get("newline", "crlf"))
        if nl is None:
            raise Invalid("Nem támogatott sorvég.")
        kw = {k: d[k] for k in ("encoding", "bom", "delimiter", "decimal_mark", "final_newline", "quoting",
                                "header_quoting") if k in d}
        return cls(newline=nl, **kw)

    def copy(self):
        return CsvFormat(self.encoding, self.bom, self.delimiter, self.decimal_mark, self.newline,
                         self.final_newline, self.quoting, self.header_quoting)

    def __eq__(self, other):
        return isinstance(other, CsvFormat) and self.to_json() == other.to_json()

    def __repr__(self):
        return "CsvFormat(%r)" % (self.to_json(),)


def default_format():
    """Új tábla formátuma: magyar Excel-barát (UTF-8 BOM-mal, ';', CRLF, tizedesvessző)."""
    return CsvFormat()


def _numberish(cell):
    try:
        tableio.parse_number(cell)
    except ValueError:
        return False
    return True


def _quote(cell, style, delim):
    special = delim in cell or '"' in cell or "\r" in cell or "\n" in cell
    if not special and (style == "minimal" or (style == "strings" and _numberish(cell))):
        return cell
    return '"' + cell.replace('"', '""') + '"'


def _join(cells, style, delim):
    if len(cells) == 1 and cells[0] == "":
        return '""'                     # különben üres sor lenne (ahogy a csv.writer is írja)
    return delim.join(_quote(c, style, delim) for c in cells)


def _split_terminator(raw):
    if raw.endswith("\r\n"):
        return raw[:-2], "\r\n"
    if raw.endswith("\n"):
        return raw[:-1], "\n"
    return raw, ""


def _same_cells(a, b):
    """Egyezés a záró üres cellák figyelmen kívül hagyásával (a nyers sor ilyenkor változatlanul írható)."""
    a, b = list(a), list(b)
    while a and a[-1] == "":
        a.pop()
    while b and b[-1] == "":
        b.pop()
    return a == b


class _Rec(object):
    __slots__ = ("cells", "raw", "line", "trail")

    def __init__(self, cells, raw, line, trail=""):
        self.cells, self.raw, self.line, self.trail = cells, raw, line, trail


class _Parsed(object):
    __slots__ = ("fmt", "lead", "header", "rows", "tail", "uid_idx")

    def __init__(self, fmt, lead="", header=None, rows=None, tail="", uid_idx=None):
        self.fmt, self.lead, self.header, self.rows = fmt, lead, header, rows or []
        self.tail, self.uid_idx = tail, uid_idx


def _codec(raw, enc):
    if enc == "utf-8-sig":
        return "utf-8", raw.startswith(codecs.BOM_UTF8)
    if enc == "utf-16":
        return ("utf-16-be" if raw.startswith(codecs.BOM_UTF16_BE) else "utf-16-le"), True
    return enc, False


def _sniff_delimiter(text):
    # ugyanaz a döntés, mint a tableio.read_table-ben (a felület ugyanazt a cellarácsot lássa)
    sample = text[:20000]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in (";", "\t", ",")}
        return max(counts, key=counts.get)


def _read_records(text, delim):
    """csv.reader rekordjai a nyers szövegükkel: [(cellák, nyers, első fizikai sor)].

    A reader lustán kéri a sorokat, így a két rekord között elfogyasztott sorok pontosan a
    rekord nyers szövegét adják (idézőjeles többsoros mezővel együtt)."""
    buf = io.StringIO(text)
    consumed = []

    def feed():
        for ln in buf:
            consumed.append(ln)
            yield ln

    reader = csv.reader(feed(), delimiter=delim)
    out = []
    prev = 0
    while True:
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error:
            line = reader.line_num or prev + 1
            raise Invalid("A CSV a(z) %d. sor körül nem olvasható (önálló CR sorvég vagy hibás "
                          "idézőjel?). Mentsd újra Excelben „CSV UTF-8” formátumban." % line, {"line": line})
        out.append((cells, "".join(consumed), prev + 1))
        prev = reader.line_num
        del consumed[:]
    if consumed:
        out.append(([], "".join(consumed), prev + 1))
    return out


def parse_csv_bytes(raw, max_columns=MAX_COLUMNS):
    """CSV-bájtok → belső, formátumtartó szerkezet (nyers rekordokkal). Üres bájtsor: üres tábla.

    max_columns: a fejléc legfeljebb ennyi oszlopos lehet (különben TooLarge, 413) — a lineáris
    beolvasás után, a fejléc-kanonizálás (ismétlődő nevekre négyzetes) előtt; None: nincs korlát."""
    if not raw:
        return _Parsed(default_format())
    try:
        text, enc = tableio._decode(raw)
    except ValueError:
        raise Invalid("A fájl karakterkódolása nem ismerhető fel. Mentsd Excelben „CSV UTF-8” formátumban.")
    codec, bom = _codec(raw, enc)
    delim = _sniff_delimiter(text)
    recs = _read_records(text, delim)
    nonblank = [i for i, (cells, _, _) in enumerate(recs) if any(c.strip() for c in cells)]
    n_crlf = sum(1 for _, r, _ in recs if r.endswith("\r\n"))
    n_lf = sum(1 for _, r, _ in recs if r.endswith("\n") and not r.endswith("\r\n"))
    fmt = CsvFormat(encoding=codec, bom=bom, delimiter=delim, decimal_mark=None,
                    newline="\n" if n_lf > n_crlf else "\r\n", final_newline=text.endswith("\n"))
    if not nonblank:
        return _Parsed(fmt, lead="".join(r for _, r, _ in recs))
    first, last = nonblank[0], nonblank[-1]
    objs = []
    for k, i in enumerate(nonblank):
        nxt = nonblank[k + 1] if k + 1 < len(nonblank) else last + 1
        cells, raw_text, line = recs[i]
        objs.append(_Rec(cells, raw_text, line, "".join(r for _, r, _ in recs[i + 1:nxt])))
    header, rows = objs[0], objs[1:]
    if max_columns is not None and len(header.cells) > max_columns:
        raise TooLarge("Túl sok oszlop (legfeljebb %d)." % max_columns, {"max_columns": max_columns})
    # tizedesjel: a tableio döntése a szám-oszlopok celláiból
    mapping = tableio.canonical_columns(header.cells)
    numeric = [v for r in rows for col, v in zip(header.cells, r.cells) if mapping.get(col) in tableio.NUMERIC]
    fmt.decimal_mark = tableio._decimal_mark(numeric)
    # idézési stílus: amelyik a legtöbb adatsort bájtra visszaadja (holtversenyben a minimal)
    if rows:
        score = {s: sum(1 for r in rows if _split_terminator(r.raw)[0] == _join(r.cells, s, delim))
                 for s in QUOTING_STYLES}
        fmt.quoting = max(QUOTING_STYLES, key=lambda s: (score[s], -QUOTING_STYLES.index(s)))
    hbody = _split_terminator(header.raw)[0]
    if hbody == _join(header.cells, "all", delim) and hbody != _join(header.cells, "minimal", delim):
        fmt.header_quoting = "all"
    uid_idx = next((i for i, c in enumerate(header.cells) if c.strip().lower() == UID_COLUMN), None)
    return _Parsed(fmt, lead="".join(r for _, r, _ in recs[:first]), header=header, rows=rows,
                   tail="".join(r for _, r, _ in recs[last + 1:]), uid_idx=uid_idx)


def _drop(cells, idx):
    if idx is None or idx >= len(cells):
        return list(cells)
    return list(cells[:idx]) + list(cells[idx + 1:])


def _assign_uids(parsed):
    """(uid nélküli fejléc, [(uid, uid nélküli cellák, _Rec)], uid-problémák)."""
    if parsed is None or parsed.header is None:
        return [], [], []
    idx = parsed.uid_idx
    header = _drop(parsed.header.cells, idx)
    mapping = tableio.canonical_columns(header)
    label_idx = next((i for i, c in enumerate(header) if mapping.get(c) == "study"), None)
    raw_uids = []
    for r in parsed.rows:
        raw_uids.append(r.cells[idx].strip() if idx is not None and idx < len(r.cells) else "")
    reserved = {u for u in raw_uids if UID_RE.match(u)}
    taken = set()
    out, issues = [], []
    for i, r in enumerate(parsed.rows):
        cells = _drop(r.cells, idx)
        uid = raw_uids[i]
        if idx is not None:
            kind = None
            if not uid:
                kind = "missing"
            elif not UID_RE.match(uid):
                kind = "invalid"
            elif uid in taken:
                kind = "duplicate"          # pl. Excelben másolt sor
            if kind:
                issues.append({"row_index": i, "line": r.line, "kind": kind})
                uid = ""
        if not uid:
            label = cells[label_idx] if label_idx is not None and label_idx < len(cells) else ""
            uid = derive_row_uid(label, i, taken | reserved)
        taken.add(uid)
        out.append((uid, cells, r))
    return header, out, issues


class Table(object):
    """Betöltött adattábla. header: a row_uid oszlop nélkül; rows: [{row_uid, cells, line}]
    (cells: nyers szövegek, a fejléc szerinti sorrendben; a fájlbeli hosszukkal)."""

    def __init__(self, dataset, header, rows, fmt, etag, uid_column, uid_issues):
        self.dataset = dataset
        self.header = header
        self.rows = rows
        self.fmt = fmt
        self.etag = etag
        self.uid_column = uid_column
        self.uid_issues = uid_issues
        self.provenance_etag = None     # a mentéssel együtt írt .prov.json új etagje (ha volt)

    @property
    def row_uids(self):
        return [r["row_uid"] for r in self.rows]

    def to_json(self):
        return {"dataset": self.dataset, "etag": self.etag, "header": list(self.header),
                "rows": [{"row_uid": r["row_uid"], "cells": list(r["cells"]), "line": r["line"]} for r in self.rows],
                "format": self.fmt.to_json(),
                "uid": {"column": self.uid_column, "persisted": self.uid_column and not self.uid_issues,
                        "issues": [dict(x) for x in self.uid_issues]}}

    def _snapshot(self):
        return (tuple(self.header), tuple((r["row_uid"], tuple(r["cells"])) for r in self.rows))


def _to_table(rel, parsed, etag):
    header, rows, issues = _assign_uids(parsed)
    return Table(rel, header, [{"row_uid": u, "cells": c, "line": r.line} for u, c, r in rows],
                 parsed.fmt if parsed is not None else default_format(), etag,
                 parsed is not None and parsed.uid_idx is not None, issues)


def _render(orig, header, rows, fmt, write_uids, uid_pos):
    """A mentendő szöveg. orig: a mostani fájl (_Parsed) vagy None; header/rows uid nélkül.

    A változatlan sorok (és a köztük álló üres sorok) nyers szövege marad; a módosított és új
    sorok a fájl formátumában szerializálódnak."""
    delim = fmt.delimiter
    orig_header = orig.header.cells if orig is not None and orig.header is not None else None
    orig_uid_idx = orig.uid_idx if orig is not None else None
    has_uid = orig_uid_idx is not None or write_uids
    if uid_pos is None:
        uid_pos = orig_uid_idx if orig_uid_idx is not None else len(header)
    pos = min(uid_pos, len(header))
    full_header = list(header[:pos]) + [UID_COLUMN] + list(header[pos:]) if has_uid else list(header)
    # új row_uid oszlop a végén: a változatlan sorok nyers szövegéhez csak hozzáfűzünk
    appended = has_uid and orig_uid_idx is None and pos == len(header)
    orig_rows = {}
    if orig is not None:
        for uid, _, rec in _assign_uids(orig)[1]:
            orig_rows[uid] = rec
    parts = []

    def emit(s):
        if not s:
            return
        if parts and not parts[-1].endswith("\n"):
            parts.append(fmt.newline)
        parts.append(s)

    if orig is not None:
        emit(orig.lead)
    oh = orig.header if orig is not None else None
    if oh is not None and _same_cells(full_header, oh.cells):
        emit(oh.raw)
    elif oh is not None and appended and list(header) == list(oh.cells):
        body, term = _split_terminator(oh.raw)
        emit(body + delim + _quote(UID_COLUMN, fmt.header_quoting, delim) + (term or fmt.newline))
    elif full_header:
        emit(_join(full_header, fmt.header_quoting, delim) + fmt.newline)
    if oh is not None:
        emit(oh.trail)
    for uid, cells in rows:
        if has_uid:
            pad = [""] * (pos - len(cells)) if len(cells) < pos else []
            full = list(cells[:pos]) + pad + [uid] + list(cells[pos:])
        else:
            full = list(cells)
        rec = orig_rows.get(uid)
        if rec is not None and _same_cells(full, rec.cells):
            emit(rec.raw)
        elif (rec is not None and appended and len(rec.cells) == len(orig_header)
              and len(rec.cells) == pos and _same_cells(cells, rec.cells)):
            body, term = _split_terminator(rec.raw)
            emit(body + delim + _quote(uid, fmt.quoting, delim) + (term or fmt.newline))
        else:
            emit(_join(full, fmt.quoting, delim) + fmt.newline)
        if rec is not None:
            emit(rec.trail)
    if orig is not None:
        emit(orig.tail)
    text = "".join(parts)
    if not fmt.final_newline and (orig is None or not orig.tail):
        text = _split_terminator(text)[0]
    elif fmt.final_newline and text and not text.endswith("\n"):
        text += fmt.newline
    return text


def _encode(text, fmt, header, rows):
    try:
        body = text.encode(fmt.encoding)
    except UnicodeEncodeError:
        where = {"encoding": fmt.encoding, "row_uid": None, "column": None}
        for col in header:
            try:
                col.encode(fmt.encoding)
            except UnicodeEncodeError:
                where["column"] = col
                break
        else:
            for uid, cells in rows:
                bad = None
                for i, c in enumerate(cells):
                    try:
                        c.encode(fmt.encoding)
                    except UnicodeEncodeError:
                        bad = header[i] if i < len(header) else "#%d" % (i + 1)
                        break
                if bad is not None:
                    where.update(row_uid=uid, column=bad)
                    break
        where_txt = ("a(z) „%s” oszlop egyik cellájában" % where["column"]) if where["row_uid"] else "a fejlécben"
        raise Invalid("Mentés nem lehetséges: %s olyan karakter van, amelyet a fájl kódolása (%s) nem tud "
                      "tárolni. Javítsd a cellát, vagy mentsd a fájlt Excelben „CSV UTF-8” formátumban."
                      % (where_txt, fmt.encoding), where)
    return (_BOMS[fmt.encoding] if fmt.bom else b"") + body


def _reads_back(data, header, rows):
    """A mentendő bájtsor visszaolvasva ugyanazt a cellarácsot adja-e (fejléc, sorok, uid-ok)?
    Igen: a _Parsed; nem: None (pl. a Sniffer más elválasztót ismerne fel). Uid-oszlop nélkül a
    csupa üres sor eltűnik (a motor is kihagyja). Egyoszlopos táblánál az elválasztó közömbös."""
    p = parse_csv_bytes(data)
    got_header, got, _ = _assign_uids(p)
    if not _same_cells(got_header, header):
        return None
    with_uid = p.uid_idx is not None
    want = rows if with_uid else [r for r in rows if any(c.strip() for c in r[1])]
    if len(want) != len(got):
        return None
    for (uid, cells), (guid, gcells, _) in zip(want, got):
        if not _same_cells(cells, gcells) or (with_uid and uid != guid):
            return None
    return p


def _normalize_submission(header, rows):
    """(uid nélküli fejléc, a row_uid oszlop helye a beküldött fejlécben, [(uid|None, cellák)])."""
    if not isinstance(header, (list, tuple)) or not all(isinstance(h, str) for h in header):
        raise Invalid("A fejléc szövegek listája legyen.")
    if any("\x00" in h for h in header):
        raise Invalid("A fejléc nem tartalmazhat NUL karaktert.")
    uid_pos = None
    hdr = []
    for i, h in enumerate(header):
        if uid_pos is None and h.strip().lower() == UID_COLUMN:
            uid_pos = i
            continue
        hdr.append(h)
    if not isinstance(rows, (list, tuple)):
        raise Invalid("A sorok listája hiányzik.")
    out = []
    for k, r in enumerate(rows):
        if isinstance(r, dict):
            uid, cells = r.get("row_uid"), r.get("cells")
        elif isinstance(r, (list, tuple)):
            uid, cells = None, r
        else:
            raise Invalid("A(z) %d. sor alakja érvénytelen." % (k + 1), {"row": k})
        if not isinstance(cells, (list, tuple)):
            raise Invalid("A(z) %d. sor cellái hiányoznak." % (k + 1), {"row": k})
        cells = ["" if c is None else c for c in cells]
        if not all(isinstance(c, str) for c in cells):
            raise Invalid("A(z) %d. sor cellái szövegek legyenek (a számot a motor értelmezi)." % (k + 1), {"row": k})
        if any("\x00" in c for c in cells):
            raise Invalid("A(z) %d. sor NUL karaktert tartalmaz." % (k + 1), {"row": k})
        if uid_pos is not None and uid_pos < len(cells):
            cell_uid = cells[uid_pos].strip()
            cells = cells[:uid_pos] + cells[uid_pos + 1:]
            uid = uid or cell_uid or None
        if uid is not None and (not isinstance(uid, str) or not UID_RE.match(uid)):
            raise Invalid("A(z) %d. sor row_uid-ja érvénytelen (alak: r + 4–12 kisbetű/számjegy)." % (k + 1),
                          {"row": k})
        out.append((uid, cells))
    return hdr, uid_pos, out


# ------------------------------------------------------------------ diff
def _dedupe(header):
    seen = collections.Counter()
    out = []
    for h in header:
        seen[h] += 1
        out.append(h if seen[h] == 1 else "%s#%d" % (h, seen[h]))
    return out


def _as_map(version):
    if version is None:
        return [], [], {}
    header, rows = version
    cols = _dedupe(header)
    order, m = [], {}
    for uid, cells in rows:
        order.append(uid)
        m[uid] = {c: (cells[i] if i < len(cells) else "") for i, c in enumerate(cols)}
    return cols, order, m


def _union(*seqs):
    return list(collections.OrderedDict.fromkeys(x for s in seqs for x in s))


def cell_diff(base, theirs, mine, base_known=True, limit=_DIFF_LIMIT):
    """Cellaszintű háromutas diff. Minden változat (fejléc, [(uid, cellák)]) vagy None.

    base_known esetén a külső (theirs ≠ base) változások kerülnek bele, conflict = a saját
    változat is eltér mindkettőtől; ismeretlen base-nél a theirs ≠ mine cellák (conflict = True).
    Visszaad: (lista, csonkolt-e)."""
    bc, bo, bm = _as_map(base)
    tc, to, tm = _as_map(theirs)
    mc, mo, mm = _as_map(mine)
    out = []
    for uid in _union(to, mo, bo):
        for col in _union(tc, mc, bc):
            b = bm.get(uid, {}).get(col)
            t = tm.get(uid, {}).get(col)
            m = mm.get(uid, {}).get(col)
            if base_known:
                if t == b:
                    continue
                conflict = m != b and m != t
            else:
                if t == m:
                    continue
                conflict = True
            out.append({"row_uid": uid, "column": col, "base": b if base_known else None, "theirs": t,
                        "mine": m, "conflict": conflict})
            if len(out) >= limit:
                return out, True
    return out, False


# ------------------------------------------------------------------ oldalfájlok: alakellenőrzés
def _relpath_ok(rel):
    try:
        relpath_parts(rel)
    except Forbidden:
        return False
    return True


def _opt_str(d, key):
    return d.get(key) is None or isinstance(d.get(key), str)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def provenance_problems(doc):
    """szk.ma.provenance/v1 minimális alakellenőrzése → problémák listája (értékek nélkül)."""
    if not isinstance(doc, dict):
        return ["a dokumentum gyökere objektum legyen"]
    p = []
    if doc.get("schema") != PROV_SCHEMA:
        p.append("schema: '%s' kell" % PROV_SCHEMA)
    if not (isinstance(doc.get("table"), str) and _relpath_ok(doc["table"])):
        p.append("table: projekt-relatív út kell")
    if "table_sha256" not in doc:
        p.append("table_sha256: hiányzik")
    elif doc["table_sha256"] is not None and not (isinstance(doc["table_sha256"], str)
                                                  and SHA256_RE.match(doc["table_sha256"])):
        p.append("table_sha256: 64 jegyű hexadecimális sha256 kell")
    cells = doc.get("cells")
    if not isinstance(cells, list):
        return p + ["cells: lista kell"]
    seen = set()
    for i, c in enumerate(cells):
        pre = "cells[%d]" % i
        if not isinstance(c, dict):
            p.append(pre + ": objektum kell")
            continue
        uid, field, method = c.get("row_uid"), c.get("field"), c.get("method")
        if not (isinstance(uid, str) and UID_RE.match(uid)):
            p.append(pre + ".row_uid: érvénytelen")
        if not (isinstance(field, str) and field.strip()):
            p.append(pre + ".field: nem üres szöveg kell")
        if method not in PROV_METHODS:
            p.append(pre + ".method: ismeretlen módszer (%s)" % ", ".join(PROV_METHODS))
        if "estimated" in c:
            if not isinstance(c["estimated"], bool):
                p.append(pre + ".estimated: logikai érték kell")
            elif method in PROV_METHODS and c["estimated"] != (method in ESTIMATED_METHODS):
                p.append(pre + ".estimated: ellentmond a módszernek (becsült: %s)" % ", ".join(ESTIMATED_METHODS))
        for key in ("value_as_entered", "verified_by"):
            if not _opt_str(c, key):
                p.append(pre + ".%s: szöveg vagy null kell" % key)
        src = c.get("source")
        if src is not None:
            if not isinstance(src, dict):
                p.append(pre + ".source: objektum kell")
            else:
                if src.get("page") is not None and not _is_int(src.get("page")):
                    p.append(pre + ".source.page: egész szám vagy null kell")
                if not (_opt_str(src, "doc") and _opt_str(src, "locator")):
                    p.append(pre + ".source: a doc és a locator szöveg legyen")
                q = src.get("quote")
                if q is not None and (not isinstance(q, str) or len(q) > 500):
                    p.append(pre + ".source.quote: legfeljebb 500 karakteres szöveg")
        for key in ("conversion", "reconciliation"):
            if c.get(key) is not None and not isinstance(c.get(key), dict):
                p.append(pre + ".%s: objektum vagy null kell" % key)
        hist = c.get("history")
        if hist is not None and (not isinstance(hist, list) or len(hist) > 50):
            p.append(pre + ".history: legfeljebb 50 elemű lista")
        if isinstance(uid, str) and isinstance(field, str):
            if (uid, field) in seen:
                p.append(pre + ": ismétlődő (row_uid, field) pár")
            seen.add((uid, field))
    return p


def documents_problems(doc):
    """szk.ma.documents/v1 (documents.json) minimális alakellenőrzése."""
    if not isinstance(doc, dict):
        return ["a dokumentum gyökere objektum legyen"]
    p = []
    if doc.get("schema") != DOCS_SCHEMA:
        p.append("schema: '%s' kell" % DOCS_SCHEMA)
    docs = doc.get("docs")
    if not isinstance(docs, list):
        return p + ["docs: lista kell"]
    seen = set()
    for i, d in enumerate(docs):
        pre = "docs[%d]" % i
        if not isinstance(d, dict):
            p.append(pre + ": objektum kell")
            continue
        did = d.get("id")
        if not (isinstance(did, str) and re.match(r"^[a-z][a-z0-9_-]*:\S", did) and len(did) <= 300):
            p.append(pre + ".id: 'pmid:…', 'doi:…' vagy 'file:<út>' alakú azonosító kell")
        elif did in seen:
            p.append(pre + ".id: ismétlődő azonosító")
        else:
            seen.add(did)
        if d.get("root") not in DOC_ROOTS:
            p.append(pre + ".root: %s" % " | ".join(DOC_ROOTS))
        if not (isinstance(d.get("path"), str) and _relpath_ok(d["path"])):
            p.append(pre + ".path: a gyökérhez képest relatív, '/' elválasztós út kell")
        sha = d.get("sha256")
        if sha is not None and not (isinstance(sha, str) and SHA256_RE.match(sha)):
            p.append(pre + ".sha256: 64 jegyű hexadecimális sha256 vagy null")
        pages = d.get("pages")
        if pages is not None and not (_is_int(pages) and pages >= 0):
            p.append(pre + ".pages: nemnegatív egész vagy null")
    return p


def studies_problems(doc):
    """szk.ma.studies/v1 (studies.json) minimális alakellenőrzése."""
    if not isinstance(doc, dict):
        return ["a dokumentum gyökere objektum legyen"]
    p = []
    if doc.get("schema") != STUDIES_SCHEMA:
        p.append("schema: '%s' kell" % STUDIES_SCHEMA)
    studies = doc.get("studies")
    if not isinstance(studies, list):
        return p + ["studies: lista kell"]
    seen = set()
    for i, s in enumerate(studies):
        pre = "studies[%d]" % i
        if not isinstance(s, dict):
            p.append(pre + ": objektum kell")
            continue
        sid = s.get("study_id")
        if not (isinstance(sid, str) and sid.strip()):
            p.append(pre + ".study_id: nem üres szöveg kell")
        elif sid in seen:
            p.append(pre + ".study_id: ismétlődő azonosító")
        else:
            seen.add(sid)
        for key in ("label", "registration", "design"):
            if not _opt_str(s, key):
                p.append(pre + ".%s: szöveg vagy null kell" % key)
        outs = s.get("outcomes")
        if outs is not None and not (isinstance(outs, list) and all(isinstance(o, str) for o in outs)):
            p.append(pre + ".outcomes: szövegek listája kell")
        reps = s.get("reports")
        if reps is None:
            continue
        if not isinstance(reps, list):
            p.append(pre + ".reports: lista kell")
            continue
        for j, r in enumerate(reps):
            rp = "%s.reports[%d]" % (pre, j)
            if not isinstance(r, dict):
                p.append(rp + ": objektum kell")
                continue
            if not (isinstance(r.get("rec_id"), str) and r["rec_id"].strip()):
                p.append(rp + ".rec_id: nem üres szöveg kell")
            if not (_opt_str(r, "role") and _opt_str(r, "doc")):
                p.append(rp + ": a role és a doc szöveg vagy null legyen")
    return p


def _checker(problems_fn, label):
    def check(doc, rel):
        probs = problems_fn(doc)
        if probs:
            raise Invalid("A(z) %s nem felel meg a(z) %s szerződésnek: %s%s"
                          % (rel, label, "; ".join(probs[:5]), " …" if len(probs) > 5 else ""),
                          {"path": rel, "problems": probs})
    return check


check_provenance = _checker(provenance_problems, PROV_SCHEMA)
check_documents = _checker(documents_problems, DOCS_SCHEMA)
check_studies = _checker(studies_problems, STUDIES_SCHEMA)


def empty_provenance(dataset):
    return {"schema": PROV_SCHEMA, "table": dataset, "table_sha256": None, "cells": []}


def empty_documents():
    return {"schema": DOCS_SCHEMA, "docs": []}


def empty_studies():
    return {"schema": STUDIES_SCHEMA, "studies": []}


def json_bytes(doc):
    """Oldalfájl-szerializálás: UTF-8, 2 szóközös behúzás, LF, záró újsor (diffelhető); NaN tilos."""
    try:
        text = json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False)
    except ValueError:
        raise Invalid("A dokumentumban NaN vagy végtelen szám van; hiányzó érték helyett null kell (4.0).")
    except TypeError:
        raise Invalid("A dokumentum nem JSON-képes értéket tartalmaz.")
    return (text + "\n").encode("utf-8")


# ------------------------------------------------------------------ atomikus írás
def _write_tmp(path, data):
    # megkülönböztető előtag: a kezelt .gitignore-blokk '.ma-tmp-*' mintája fedi (egy összeomlás után
    # itt maradt másolatot a vault sem tolja fel), és az indításkori takarítás csak ilyet töröl
    fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX + path.name + "-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, stat.S_IMODE(os.stat(str(path)).st_mode))
        except OSError:
            pass                        # új fájl: a mkstemp szűk jogosultsága marad
    except BaseException:
        _unlink(tmp)
        raise
    return tmp


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def _replace(tmp, path):
    delays = tuple(_REPLACE_RETRY_DELAYS)
    for attempt in range(len(delays) + 1):
        try:
            os.replace(tmp, str(path))
            return
        except PermissionError:
            if attempt >= len(delays):
                raise
            time.sleep(delays[attempt])


def sweep_temp_files(root, max_age=TMP_SWEEP_AGE, limit=200000, now=None):
    """Árva ideiglenes fájlok ('.ma-tmp-….tmp', pl. összeomlás után) törlése a projektmappában →
    a törölt fájlok projekt-relatív útjai. Csak max_age másodpercnél régebbi, szabályos fájlt töröl
    (a futó írásokat nem zavarja); a .git mappába nem lép be; legfeljebb limit bejegyzést néz meg."""
    root = os.path.realpath(str(root))
    now = time.time() if now is None else now
    removed = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            seen += 1
            if seen > limit:
                return removed
            if not (name.startswith(TMP_PREFIX) and name.endswith(".tmp")):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or now - st.st_mtime < max_age:
                continue
            try:
                os.unlink(full)
            except OSError:
                continue
            removed.append(Path(os.path.relpath(full, root)).as_posix())
    return removed


def _fsync_dir(d):
    if os.name != "posix":
        return
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# ------------------------------------------------------------------ a tároló
class ProjectStore(object):
    """Egy projektmappa fájljai. Egy folyamaton belül a mentések sorba állnak (RLock);
    folyamatok (Excel, ágens) között az ETag véd."""

    def __init__(self, root, watcher=None):
        p = Path(os.path.realpath(str(root)))
        if not p.is_dir():
            raise NotFound("A projektmappa nem létezik vagy nem mappa.")
        self.root = p
        self.watcher = watcher
        self._lock = threading.RLock()
        self._versions = collections.OrderedDict()

    # --- utak
    def rel(self, rel):
        """Kanonikus projekt-relatív alak (ellenőrzéssel)."""
        return "/".join(relpath_parts(rel))

    def path(self, rel):
        """Projekt-relatív út → abszolút Path; ami kivezetne a projektből, Forbidden."""
        return resolve_under(self.root, rel)

    def relpath_of(self, path):
        """Abszolút út → projekt-relatív ('/'), ha a projekt alatt van; különben Forbidden."""
        full = os.path.realpath(str(path))
        if not is_within(full, str(self.root)) or os.path.normcase(full) == os.path.normcase(str(self.root)):
            raise Forbidden("Az út a projektmappán kívül van.")
        return Path(os.path.relpath(full, str(self.root))).as_posix()

    def etag(self, rel):
        """A fájl mostani ETag-je (sha256) vagy None."""
        path = self.path(rel)
        raw = _read_bytes(path, self.rel(rel))
        return None if raw is None else sha256_bytes(raw)

    # --- írás
    def write_bytes(self, rel, data, if_match=ANY):
        """Tetszőleges projektfájl atomikus írása If-Match-csel → új etag. if_match: etag; '*' =
        bármely meglévő változat; None = csak új fájl; ANY = feltétel nélkül."""
        rel = self.rel(rel)
        path = self.path(rel)
        with self._lock:
            cur = _read_bytes(path, rel)
            cur_etag = None if cur is None else sha256_bytes(cur)
            self._precondition(rel, if_match, cur_etag)
            if cur is not None and cur == data:
                return cur_etag
            self._write_many([(rel, path, data)], expect={rel: cur_etag})
        return sha256_bytes(data)

    def write_with(self, rel, writer, if_match=ANY):
        """Atomikus írás egy KÜLSŐ író függvénnyel (pl. a motor kanonikus spec-mentése) If-Match-csel →
        az új etag. writer(abszolút út) → a kiírt bájtok sha256-ja; neki kell atomikusan (tmp +
        os.replace) írnia. Az írás alatt a változásfigyelő nem szkennel, és a saját írásként jegyzi
        (nem 'external'). if_match: mint a write_bytes-nál."""
        rel = self.rel(rel)
        path = self.path(rel)
        with self._lock:
            cur = _read_bytes(path, rel)
            cur_etag = None if cur is None else sha256_bytes(cur)
            self._precondition(rel, if_match, cur_etag)
            path.parent.mkdir(parents=True, exist_ok=True)
            watcher = self.watcher
            lock = getattr(watcher, "_scan_lock", None) if watcher is not None else None
            if lock is not None:
                lock.acquire()
            try:
                try:
                    sha = writer(str(path))
                except PermissionError:
                    raise Locked(rel)
                if watcher is not None:
                    watcher.watch(rel)
                    watcher.note_write(rel, sha)
            finally:
                if lock is not None:
                    lock.release()
        if watcher is not None:
            try:
                watcher.scan()
            except Exception:
                pass
        return sha

    def _precondition(self, rel, if_match, cur_etag, diff_fn=None):
        if if_match is ANY:
            return
        im = _norm_etag(if_match)
        if im == "*":
            ok = cur_etag is not None
        elif im is None:
            ok = cur_etag is None
        else:
            ok = im == cur_etag
        if ok:
            return
        diff, base_known, truncated = [], False, False
        if diff_fn is not None:
            diff, base_known, truncated = diff_fn(im)
        raise Conflict(rel, cur_etag, diff, base_known, truncated)

    def _write_many(self, items, expect=None):
        """items: [(rel, Path, bytes)] — minden tmp elkészül, aztán os.replace sorrendben
        (a CSV előbb, az oldalfájl utána: félbeszakadásnál a table_sha256 eltérése jelzi, X022)."""
        prepared = []
        try:
            for rel, path, data in items:
                path.parent.mkdir(parents=True, exist_ok=True)
                prepared.append((rel, path, _write_tmp(path, data), sha256_bytes(data)))
        except PermissionError:
            for _, _, tmp, _ in prepared:
                _unlink(tmp)
            raise Forbidden("A projektmappa nem írható (jogosultság).", {"path": items[len(prepared)][0]})
        except BaseException:
            for _, _, tmp, _ in prepared:
                _unlink(tmp)
            raise
        # utolsó ellenőrzés közvetlenül a csere előtt: közben nem írta-e át más (Excel, ágens)?
        try:
            for rel, path, _, _ in prepared:
                if expect and rel in expect:
                    now = _read_bytes(path, rel)
                    now_etag = None if now is None else sha256_bytes(now)
                    if now_etag != expect[rel]:
                        raise Conflict(rel, now_etag)
        except BaseException:
            for _, _, tmp, _ in prepared:
                _unlink(tmp)
            raise
        done = []
        for i, (rel, path, tmp, sha) in enumerate(prepared):
            if self.watcher is not None:
                self.watcher.watch(rel)
                self.watcher.note_write(rel, sha)
            try:
                _replace(tmp, path)
            except BaseException as exc:
                for _, _, t, _ in prepared[i:]:
                    _unlink(t)
                if self.watcher is not None:
                    self.watcher.forget_write(rel)
                if isinstance(exc, PermissionError):
                    raise Locked(rel, {"written": done, "partial": bool(done)})
                raise
            done.append(rel)
        for d in sorted({str(p.parent) for _, p, _, _ in prepared}):
            _fsync_dir(d)
        if self.watcher is not None:
            try:
                self.watcher.scan()
            except Exception:
                pass                    # a figyelő hibája ne rontsa el a mentést

    # --- táblák
    def _remember(self, table):
        key = (table.dataset, table.etag)
        self._versions[key] = table._snapshot()
        self._versions.move_to_end(key)
        while len(self._versions) > _VERSION_CACHE:
            self._versions.popitem(last=False)

    def list_tables(self, folder="03_adatok"):
        """A mappa CSV/TSV táblái: [{dataset, etag}] (név szerint)."""
        d = self.path(folder)
        out = []
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in (".csv", ".tsv"):
                    try:
                        etag = sha256_file(p)
                    except OSError:
                        etag = None             # zárolt fájl: a betöltés jelzi
                    out.append({"dataset": self.rel(folder) + "/" + p.name, "etag": etag})
        return out

    def load_table(self, dataset):
        """A tábla nyers szöveges cellákkal (Table); nincs ilyen fájl: NotFound."""
        rel = self.rel(dataset)
        raw = _read_bytes(self.path(rel), rel)
        if raw is None:
            raise NotFound("Nincs ilyen adattábla: %s" % rel, {"dataset": rel})
        table = _to_table(rel, parse_csv_bytes(raw), sha256_bytes(raw))
        self._watch(rel, table.etag)
        with self._lock:
            self._remember(table)
        return table

    def _watch(self, rel, sha=None):
        """A megnyitott/írt fájl a változásfigyelőbe kerül (a látott hash az alapállapota), így a
        külső szerkesztése bárhol (_privat/, almappa, .txt) 'external' lesz (4.16)."""
        if self.watcher is not None:
            try:
                self.watcher.watch(rel, sha)
            except Exception:
                pass                    # a figyelő hibája ne rontsa el a betöltést

    def save_table(self, dataset, header, rows, if_match, provenance=None, write_uids=True, fmt=None,
                   provenance_if_match=ANY):
        """Mentés If-Match-csel → az új Table (a visszaadott uid-ok az érvényesek).

        header: oszlopnevek (a row_uid nélkül; ha benne van, a helyét megtartjuk); rows:
        [{"row_uid": uid|None, "cells": [szöveg…]}] (None uid = új sor, véletlen uid-ot kap).
        if_match: a betöltött ETag; None = csak új fájl; '*' = bármely meglévő; ANY = feltétel nélkül.
        Eltérésnél Conflict
        cellaszintű diffel. write_uids: a row_uid oszlop (ha még nincs) a fájlba kerül.
        provenance: ha adott, a .prov.json is íródik (table_sha256 = az új CSV hash-e), a CSV után.
        provenance_if_match: az oldalfájl betöltött etagje (a CSV-éhez hasonló feltétel: None = csak
        ha még nincs oldalfájl; ANY = feltétel nélkül); eltérésnél Conflict (details.kind: provenance).
        Részleges írásnál (a CSV kész, az oldalfájl zárolt) Locked: details {partial, written, etag}.
        fmt: új fájl formátuma (CsvFormat vagy dict); meglévő fájlnál a fájlé marad."""
        rel = self.rel(dataset)
        path = self.path(rel)
        hdr, uid_pos, subm = _normalize_submission(header, rows)
        given = [u for u, _ in subm if u]
        if len(given) != len(set(given)):
            raise Invalid("Ismétlődő row_uid a mentendő sorokban.")
        taken = set(given)
        sub_rows = []
        for uid, cells in subm:
            if uid is None:
                uid = new_row_uid(taken)
                taken.add(uid)
            sub_rows.append((uid, cells))
        if isinstance(fmt, dict):
            fmt = CsvFormat.from_json(fmt)
        with self._lock:
            cur = _read_bytes(path, rel)
            cur_etag = None if cur is None else sha256_bytes(cur)
            orig = parse_csv_bytes(cur) if cur is not None else None

            def diff_fn(im):
                theirs = _to_table(rel, orig, cur_etag)._snapshot() if orig is not None else None
                mine = (tuple(hdr), tuple((u, tuple(c)) for u, c in sub_rows))
                base = self._versions.get((rel, im)) if im not in (None, "*", ANY) else None
                known = base is not None or im is None
                d, trunc = cell_diff(base, theirs, mine, base_known=known)
                return d, known, trunc

            self._precondition(rel, if_match, cur_etag, diff_fn)
            prel = provenance_relpath(rel)
            prov_etag = None
            if provenance is not None:
                prov_raw = _read_bytes(self.path(prel), prel)
                prov_etag = None if prov_raw is None else sha256_bytes(prov_raw)
                try:
                    self._precondition(prel, provenance_if_match, prov_etag)
                except Conflict as exc:
                    raise _provenance_conflict(prel, rel, exc.etag) from None
            out_fmt = orig.fmt if orig is not None else (fmt or default_format())
            data = _encode(_render(orig, hdr, sub_rows, out_fmt, write_uids, uid_pos), out_fmt, hdr, sub_rows)
            parsed = _reads_back(data, hdr, sub_rows)
            if parsed is None:
                # az elválasztó-felismerés (Sniffer) megbillenne: az idézőjelek egyértelműsítik
                alt = out_fmt.copy()
                alt.quoting = alt.header_quoting = "all"
                data = _encode(_render(orig, hdr, sub_rows, alt, write_uids, uid_pos), alt, hdr, sub_rows)
                parsed = _reads_back(data, hdr, sub_rows)
            if parsed is None:
                raise Invalid("A tábla így mentve nem olvasható vissza egyértelműen (a motor más "
                              "elválasztót ismerne fel). Ellenőrizd az elválasztó karaktereket a cellákban.",
                              {"dataset": rel})
            new_etag = sha256_bytes(data)
            writes = []
            if cur is None or data != cur:
                writes.append((rel, path, data))
            prov_bytes = None
            if provenance is not None:
                doc = dict(provenance)
                if doc.get("table") not in (None, rel):
                    raise Invalid("Az eredet-oldalfájl másik táblához tartozik.", {"dataset": rel})
                doc["table"], doc["table_sha256"] = rel, new_etag
                check_provenance(doc, prel)
                prov_bytes = json_bytes(doc)
                writes.append((prel, self.path(prel), prov_bytes))
            table = _to_table(rel, parsed, new_etag)
            if writes:
                expect = {rel: cur_etag}
                if provenance is not None:
                    expect[prel] = prov_etag        # az oldalfájl sem íródhat felül vakon (a CSV-hez hasonlóan)
                try:
                    self._write_many(writes, expect=expect)
                except Locked as exc:
                    if exc.details.get("partial") and rel in (exc.details.get("written") or ()):
                        # a CSV már az új változat: a hívó ezzel az etaggel folytathatja
                        exc.details.update(dataset=rel, etag=new_etag)
                        self._remember(table)
                    raise
            if prov_bytes is not None:
                table.provenance_etag = sha256_bytes(prov_bytes)
            self._watch(rel, new_etag)
            self._remember(table)
        return table

    load = load_table
    save = save_table

    # --- JSON oldalfájlok
    def load_json(self, rel, check=None, default=None):
        """(dokumentum, etag); ha nincs fájl: (default(), None)."""
        rel = self.rel(rel)
        raw = _read_bytes(self.path(rel), rel)
        self._watch(rel, None if raw is None else sha256_bytes(raw))
        if raw is None:
            return (default() if callable(default) else default), None
        try:
            doc = json.loads(raw.decode("utf-8-sig"))
        except UnicodeDecodeError:
            raise Invalid("A(z) %s nem UTF-8 kódolású." % rel, {"path": rel})
        except ValueError as exc:
            line = getattr(exc, "lineno", None)
            raise Invalid("A(z) %s nem érvényes JSON%s." % (rel, " (%d. sor)" % line if line else ""),
                          {"path": rel, "line": line})
        if check is not None:
            check(doc, rel)
        return doc, sha256_bytes(raw)

    def save_json(self, rel, doc, if_match, check=None):
        """Oldalfájl atomikus írása If-Match-csel → új etag."""
        rel = self.rel(rel)
        if check is not None:
            check(doc, rel)
        return self.write_bytes(rel, json_bytes(doc), if_match=if_match)

    def load_provenance(self, dataset):
        """(dokumentum, etag, állapot). Állapot: {table_etag, in_sync} — in_sync False, ha a
        table_sha256 nem a mostani CSV-é (X022: félbeszakadt írás vagy külső szerkesztés)."""
        rel = self.rel(dataset)
        doc, etag = self.load_json(provenance_relpath(rel), check_provenance, lambda: empty_provenance(rel))
        table_etag = self.etag(rel)
        stated = doc.get("table_sha256")
        return doc, etag, {"table_etag": table_etag, "in_sync": None if stated is None else stated == table_etag}

    def save_provenance(self, dataset, doc, if_match, table_etag=None):
        """table_etag: a tábla azon változata, amelyhez az eredet tartozik (a felület által látott ETag)."""
        rel = self.rel(dataset)
        if not isinstance(doc, dict):
            raise Invalid("Az eredet-oldalfájl objektum legyen.")
        doc = dict(doc)
        if doc.get("table") not in (None, rel):
            raise Invalid("Az eredet-oldalfájl másik táblához tartozik.", {"dataset": rel})
        doc["table"] = rel
        if table_etag is not None:
            doc["table_sha256"] = _norm_etag(table_etag)
        doc.setdefault("table_sha256", None)
        return self.save_json(provenance_relpath(rel), doc, if_match, check_provenance)

    def load_documents(self, rel=DOCUMENTS_REL):
        """rel: a jegyzék helye (C osztályban PRIVATE_DOCUMENTS_REL — ezt a hívó dönti el)."""
        return self.load_json(rel, check_documents, empty_documents)

    def save_documents(self, doc, if_match, rel=DOCUMENTS_REL):
        return self.save_json(rel, doc, if_match, check_documents)

    def load_studies(self):
        return self.load_json(STUDIES_REL, check_studies, empty_studies)

    def save_studies(self, doc, if_match):
        return self.save_json(STUDIES_REL, doc, if_match, check_studies)

    def document_path(self, doc_id, composer_outdir=None, rel=DOCUMENTS_REL):
        """A documents.json-ban szereplő dokumentum abszolút útja (az aláírt fájl-URL allowlistje)."""
        doc, _ = self.load_documents(rel)
        for d in doc["docs"]:
            if d["id"] == doc_id:
                break
        else:
            raise NotFound("Nincs ilyen dokumentum a jegyzékben.", {"doc": doc_id})
        if d["root"] == "project":
            return self.path(d["path"])
        if not composer_outdir:
            raise NotFound("A composer kimeneti mappája nincs beállítva.", {"doc": doc_id})
        return resolve_under(composer_outdir, d["path"])


# ------------------------------------------------------------------ változásfigyelés
WATCH_PATTERNS = (
    "ma-projekt.json",
    "02_szures/*.json",
    # az adattáblák bármely almappában (a '**' nulla mappát is jelent): 03_adatok/ és a C osztály
    # egyetlen engedett helye, a _privat/ (4.16: a külső írás ott is 'external' sort kap)
    "03_adatok/**/*.csv", "03_adatok/**/*.tsv", "03_adatok/**/*.txt", "03_adatok/**/*.json",
    "_privat/**/*.csv", "_privat/**/*.tsv", "_privat/**/*.txt", "_privat/**/*.json",
    "04_torzitas_kockazat/appraisals/*.json",
    "05_elemzes/specs/*.json",
    "05_elemzes/*/*/run.json",
    "06_kezirat/sof/*.json",
    "07_ellenorzes/activity.jsonl",
)


def classify_key(key):
    """Változáskulcs → fajta: db | provenance | documents | studies | table | activity | file."""
    if key == DB_KEY:
        return "db"
    if key.endswith(PROV_SUFFIX):
        return "provenance"
    if key in (DOCUMENTS_REL, PRIVATE_DOCUMENTS_REL):
        return "documents"
    if key == STUDIES_REL:
        return "studies"
    if key.lower().endswith((".csv", ".tsv", ".txt")):
        return "table"
    if key.endswith("activity.jsonl"):
        return "activity"
    return "file"


class ChangeWatcher(object):
    """A projekt figyelt fájljainak (sha256, mtime) és a projekt.sqlite PRAGMA data_version-jének
    változásai, növekvő rev-számlálóval (long-poll: GET /api/changes?since=<rev>).

    A rev kezdőértéke a példány indulási ideje ezredmásodpercben, így egy korábbi szerverpéldány
    rev-je mindig „túl régi” → reset (teljes újratöltés). A saját írásokat a ProjectStore
    note_write-tal jelzi; minden más fájlváltozás 'external' (a szerver erre ír actor: external
    sort, 4.16) — kivéve a projekt.sqlite-ot és az activity.jsonl-t, amelyek csak 'changed'-ben
    szerepelnek. A mintákon túl a watch(rel)-lel felvett fájlokat is figyeli (amit a tároló
    megnyitott vagy írt)."""

    def __init__(self, project_root, patterns=WATCH_PATTERNS, extra_files=(), history=1000, interval=1.5):
        self.root = Path(os.path.realpath(str(project_root)))
        self._patterns = tuple(patterns)
        self._extra = [Path(p) for p in extra_files]
        self._extra_lock = threading.Lock()
        self._pending = {}              # kulcs → (Path, a tároló által látott sha vagy None)
        self._watched = {}              # kulcs → Path (csak a _scan_lock alatt változik)
        self.interval = float(interval)
        self._cond = threading.Condition()
        self._scan_lock = threading.Lock()
        self._rev = self._base_rev = int(time.time() * 1000)
        self._history = collections.deque(maxlen=max(1, int(history)))
        self._files = {}
        self._expected = {}
        self._own = {}                  # token → (előtag, lejárat | None): saját írások egy mappában
        self._db = None
        self._db_ident = None
        self._db_version = None
        self._thread = None
        self._stop = threading.Event()
        with self._scan_lock:
            self._scan_files()          # alapállapot: nem változás
            self._scan_db()

    @property
    def rev(self):
        with self._cond:
            return self._rev

    def _key(self, path):
        full = os.path.realpath(str(path))
        if is_within(full, str(self.root)):
            return Path(os.path.relpath(full, str(self.root))).as_posix()
        return "ext:" + Path(full).as_posix()

    def watch(self, rel, sha256=None):
        """Projekt-relatív fájl felvétele a figyeltek közé (a következő körtől). sha256: a hívó által
        látott tartalom hash-e — ez az alapállapot, így a felvétel maga nem változás; None: nincs
        alapállapot (új vagy épp írt fájl: a saját írást a note_write jelzi)."""
        if not isinstance(rel, str) or not rel:
            return
        parts = rel.split("/")
        if any(p in ("", ".", "..") for p in parts) or parts[-1].startswith(".") or parts[-1].endswith(".tmp"):
            return
        path = self.root.joinpath(*parts)
        key = self._key(path)
        if key.startswith("ext:"):
            return
        with self._extra_lock:
            if key in self._pending or len(self._watched) + len(self._pending) >= _MAX_WATCHED:
                return
            if key in self._watched and sha256 is None:
                return
            self._pending[key] = (path, sha256)

    def _take_pending(self):
        """A watch()-csal felvett fájlok átvétele (a _scan_lock alatt hívandó)."""
        with self._extra_lock:
            pending, self._pending = self._pending, {}
        for key, (path, sha) in pending.items():
            self._watched[key] = path
            if sha is not None and key not in self._files:
                self._files[key] = (None, sha)          # alapállapot: a tároló által látott tartalom

    def _iter_files(self):
        seen = set()
        for pat in self._patterns:
            for p in self.root.glob(pat):
                if p.name.startswith(".") or p.name.endswith(".tmp"):
                    continue
                key = self._key(p)
                if key not in seen:
                    seen.add(key)
                    yield key, p
        for p in self._extra:
            key = self._key(p)
            if key not in seen:
                seen.add(key)
                yield key, p
        for key, p in list(self._watched.items()):
            if key not in seen:
                seen.add(key)
                yield key, p

    def _scan_files(self):
        self._take_pending()
        changed = set()
        current = {}
        now = time.time()
        for key, path in self._iter_files():
            try:
                st = path.stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            sig = (st.st_size, st.st_mtime_ns)
            prev = self._files.get(key)
            # durva mtime-felbontású fájlrendszeren a friss fájlt újra hash-eljük
            if prev is not None and prev[0] == sig and now - st.st_mtime > 2.0:
                current[key] = prev
                continue
            try:
                sha = sha256_file(path)
            except OSError:
                sha = None              # zárolt (Excel): a következő körben újra
            if sha is None:
                if prev is not None:
                    current[key] = prev
                continue
            current[key] = (sig, sha)
            if prev is None or prev[1] != sha:
                changed.add(key)
        changed.update(k for k in self._files if k not in current)
        self._files = current
        return changed

    def _close_db(self):
        if self._db is not None:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
        self._db = None

    def _scan_db(self):
        """True, ha a projekt.sqlite tartalma más kapcsolatból változott (vagy a fájl cserélődött)."""
        db = self.root / DB_NAME
        try:
            st = db.stat()
        except OSError:
            had = self._db_ident is not None
            self._close_db()
            self._db_ident = self._db_version = None
            return had
        ident = (st.st_dev, st.st_ino)
        changed = False
        if self._db is None or ident != self._db_ident:
            changed = self._db_ident is not None
            self._close_db()
            try:
                self._db = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, check_same_thread=False,
                                           timeout=0.2)
            except sqlite3.Error:
                self._db = None
                return False
            self._db_ident = ident
            self._db_version = None
        try:
            ver = self._db.execute("PRAGMA data_version").fetchone()[0]
        except sqlite3.Error:
            return changed
        if self._db_version is not None and ver != self._db_version:
            changed = True
        self._db_version = ver
        return changed

    def scan(self):
        """Egy figyelési kör → (rev, változott kulcsok rendezve)."""
        with self._scan_lock:
            changed = self._scan_files()
            if self._scan_db():
                changed.add(DB_KEY)
            with self._cond:
                if not changed:
                    return self._rev, []
                external = set()
                now = time.monotonic()
                for key in changed:
                    # a projekt.sqlite íróját nem ismerjük; a hozzáfűzős naplóra nem írunk
                    # „külső szerkesztés” sort (különben a saját bejegyzés újabbat szülne)
                    if key == DB_KEY or classify_key(key) == "activity":
                        self._expected.pop(key, None)
                        continue
                    sha = self._files.get(key, (None, None))[1]
                    exp = self._expected.pop(key, None)
                    if self._owned_locked(key, now):
                        continue
                    if exp is None or exp[0] != sha or now - exp[1] > _EXPECT_TTL:
                        external.add(key)
                self._rev += 1
                self._history.append((self._rev, frozenset(changed), frozenset(external)))
                self._cond.notify_all()
                return self._rev, sorted(changed)

    def note_write(self, rel, sha256):
        """Saját (munkapad) írás várt hash-e: a megfelelő változás nem 'external'."""
        with self._cond:
            self._expected[rel] = (sha256, time.monotonic())

    def own_prefix(self, prefix):
        """Egy mappa (előtag) írásai saját írásnak számítanak, amíg a release_prefix nem jön (+ türelmi
        idő): a meleg workerben futó commit a 05_elemzes/ alá ír, a hash-eket előre nem ismerjük. → token"""
        tok = object()
        with self._cond:
            self._own[tok] = (str(prefix), None)
        return tok

    def release_prefix(self, token, grace=_OWN_GRACE):
        with self._cond:
            item = self._own.get(token)
            if item is not None:
                self._own[token] = (item[0], time.monotonic() + float(grace))

    def _owned_locked(self, key, now):
        hit = False
        for tok, (prefix, until) in list(self._own.items()):
            if until is not None and now > until:
                del self._own[tok]
                continue
            if key.startswith(prefix):
                hit = True
        return hit

    def forget_write(self, rel):
        with self._cond:
            self._expected.pop(rel, None)

    def _changes_locked(self, since):
        res = {"rev": self._rev, "changed": [], "external": [], "reset": False}
        try:
            since = int(since)
        except (TypeError, ValueError):
            res["reset"] = True
            return res
        oldest_known = self._rev - len(self._history)
        if since > self._rev or since < self._base_rev or since < oldest_known:
            res["reset"] = True
            return res
        keys, ext = set(), set()
        for rev, k, e in self._history:
            if rev > since:
                keys |= k
                ext |= e
        res["changed"], res["external"] = sorted(keys), sorted(ext)
        return res

    def changes_since(self, since):
        """{rev, changed, external, reset}; reset = a kliens állapota nem követhető (régi vagy
        ismeretlen rev, másik szerverpéldány) → teljes újratöltés."""
        with self._cond:
            return self._changes_locked(since)

    def poll(self, since=None):
        """Egy figyelési kör, majd a since óta történt változások."""
        self.scan()
        return self.changes_since(since)

    def wait(self, since, timeout=25.0):
        """Long-poll: vár, amíg since után változás lesz, vagy lejár az idő. Háttérszál nélkül
        maga figyel interval-onként."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._cond:
                res = self._changes_locked(since)
                remaining = deadline - time.monotonic()
                if res["changed"] or res["reset"] or remaining <= 0:
                    return res
                if self._running():
                    self._cond.wait(remaining)
                    continue
            time.sleep(min(self.interval, max(0.0, remaining)))
            self.scan()

    def _running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Háttérszál: interval-onként scan()."""
        if self._running():
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.scan()
                except Exception:
                    pass                # a figyelő nem állhat le egy hibás kör miatt

        self._thread = threading.Thread(target=loop, name="ma-gui-watcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=5)
        self._thread = None
        with self._scan_lock:
            self._close_db()

    close = stop
