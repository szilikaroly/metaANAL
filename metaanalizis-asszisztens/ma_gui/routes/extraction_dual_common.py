# -*- coding: utf-8 -*-
"""Közös rész a kettős kinyerés végpontjaihoz (terv 2.4, 3.4, 3.5.5, 4.8, 4.9, 6.4 X009; 11. fejezet 5. döntés).

**Motor-kötés (feature detection).** Az összevetés (egyezés, eltérés-fajta, súgó, hatás a hatásméretre és az
összesített becslésre, κ CI-vel) a MOTOR dolga (``metaelemzes.api``; a v1 motor-munkafolyamat ``kettos.py``-ja). A
munkapad egyetlen számot sem számol és egyetlen cellát sem értelmez számként: ha a homlokzat-függvény hiányzik, a
végpont 424 CAPABILITY_MISSING-et ad magyar üzenettel. Az elvárt nevek és az álnevek az ``ENGINE`` táblában; a
végleges összekötést az integrátor itt, egy helyen igazítja.

**Fájlok (2.4).** A kimenet adattáblája mellett, a ``kettos/`` almappában (``03_adatok/o1.csv`` →
``03_adatok/kettos/``; ``_privat/o1.csv`` → ``_privat/kettos/``, így a B/C osztályú adat a _privat/ alatt marad):
``<kimenet>.A.csv``, ``<kimenet>.B.csv`` (a két kinyerő táblája, a motor CSV-formátumában), ``<kimenet>.consensus.json``
(``szk.ma.consensus/v1`` + additív mezők: outcome, raters, sources, csv), ``<kimenet>.consensus.csv`` és az eredet-
oldalfájlja (``<kimenet>.consensus.prov.json``, ``reconciled`` módszerrel). Beérkezett fájlok (5. döntés: a második
kinyerő a saját gépén dolgozik, fájlcserével): ``kettos/beerkezett/*.csv``.

**Döntések.** ``{key, field, chosen: a|b|other, value, reason, actor, ts}`` (4.9). A csak az egyik táblában szereplő
sor (``only_a``/``only_b``) sorszintű döntés: ``field = "*"``, ``chosen = a`` → az A változata (only_a: a sor bekerül,
only_b: kimarad), ``b`` → a B változata; ``value`` null. A döntéskori A/B szöveget a döntés megőrzi (``a``, ``b``):
ha a tábla azóta változott, a döntés ELAVULT, és újra kell dönteni (a feloldatlanok közé számít; X009).

Adatvédelem (T10): naplóba és activity-be cellaérték, kulcsérték és indoklás-szöveg nem kerül — csak út, sha256,
kimenet, mezőnevek és darabszámok."""
import base64
import posixpath
import re

from metaelemzes import api

from .. import schema_lite, security, store
from ..router import ApiError
from . import _contracts
from ._common import accepts

COMPARE_SCHEMA = "szk.ma.compare-result/v1"
CONSENSUS_SCHEMA = "szk.ma.consensus/v1"
PROV_SCHEMA = "szk.ma.provenance/v1"
KETTOS = "kettos"
INBOX = "beerkezett"
DEFAULT_DIR = "03_adatok/" + KETTOS
SIDES = ("A", "B")
ROW_FIELD = "*"
CHOICES = ("a", "b", "other")
ROW_CHOICES = ("a", "b")
KINDS_AUTO = ("format_only",)
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_REASON = 2000
MAX_VALUE = 2000
MAX_DECISIONS = 5000

OUTCOME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RATER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


# ---------------------------------------------------------------------------- motor-kötés
# kulcs → (elvárt név, álnevek, magyar leírás). A munkapad CSAK ezeken át hívja a motort (6.8: metaelemzes.api).
ENGINE = {
    "compare": ("compare", ("kettos_compare", "compare_tables", "dual_compare"),
                "a kettős kinyerés összevetése (eltérések, súgók, hatás a hatásméretre, κ)"),
    "consensus_table": ("consensus_table", ("kettos_consensus", "reconcile_table", "build_consensus"),
                        "a konszenzus-tábla összeállítása a döntésekből"),
    "agreement_report": ("agreement_report", ("kettos_agreement", "agreement_table"),
                         "az egyetértési táblázat (Methods-szöveg)"),
}


def engine_fn(key):
    """A motor homlokzat-függvénye (vagy None): az elvárt név, majd az álnevek közül az első hívható."""
    name, aliases, _what = ENGINE[key]
    for n in (name,) + tuple(aliases):
        fn = getattr(api, n, None)
        if callable(fn) and not isinstance(fn, type):
            return fn
    return None


def engine_missing(keys):
    names = ", ".join("metaelemzes.api.%s" % ENGINE[k][0] for k in keys)
    whats = "; ".join(ENGINE[k][2] for k in keys)
    return ApiError("CAPABILITY_MISSING",
                    "Ehhez a lépéshez a metaanalízis-motor kettős kinyerés funkciója kell (%s), de ez a motorváltozat "
                    "még nem tartalmazza (%s). Frissítsd a motort (git pull a metaanalizis-asszisztens mappában), "
                    "majd indítsd újra a munkapadot. A két tábla addig is importálható és exportálható."
                    % (whats, names),
                    {"engine_functions": [ENGINE[k][0] for k in keys], "engine": "metaelemzes.api"})


def need(key):
    fn = engine_fn(key)
    if fn is None:
        raise engine_missing([key])
    return fn


def engine_status():
    """{available, functions{kulcs: név|None}, missing[], engine_version} — a felület sávjához."""
    funcs = {}
    for key in ENGINE:
        fn = engine_fn(key)
        funcs[key] = getattr(fn, "__name__", None) if fn is not None else None
    missing = [ENGINE["compare"][0]] if funcs["compare"] is None else []
    return {"available": not missing, "functions": funcs, "missing": missing,
            "engine_version": str(getattr(api, "__version__", "")) or None}


def call(fn, *args, **kw):
    """Hívás csak az elfogadott kulcsszavakkal (a motor szignatúrája még alakulhat)."""
    use = {k: v for k, v in kw.items() if v is not None and accepts(fn, k)}
    return fn(*args, **use)


def _first_params(fn, n=2):
    import inspect
    try:
        ps = [p for p in inspect.signature(fn).parameters.values()
              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    except (TypeError, ValueError):
        return []
    return [p.name for p in ps[:n]]


def table_arg(table, rel, abs_path, fn):
    """A tábla a motor-függvénynek: ha az első paramétere út-jellegű (``*path*``), az abszolút fájlút; különben
    ``{header, rows, row_uids, dataset, decimal_mark}`` (nyers szöveges cellák, a motor értelmezi őket)."""
    first = _first_params(fn, 1)
    if first and "path" in first[0]:
        return str(abs_path)
    return {"header": list(table.header), "rows": [list(r["cells"]) for r in table.rows],
            "row_uids": [r["row_uid"] for r in table.rows], "dataset": rel,
            "decimal_mark": table.fmt.decimal_mark}


def check_contract(doc, name, what):
    """A motor kimenete megfelel-e a szerződésnek; eltérésnél 500 (integrációs hiba) — értékek nélkül."""
    errs = schema_lite.validate(doc, _contracts.ref(name), _contracts.registry())
    if errs:
        raise ApiError("INTERNAL", "A motor %s nem felel meg a(z) %s szerződésnek (%d eltérés). Ez a motor és a "
                                   "munkapad közti integrációs hiba: jelezd a fejlesztőnek." % (what, name, len(errs)),
                       {"contract": name, "problems": [e.split(":")[0] for e in errs[:10]]})
    return doc


# ---------------------------------------------------------------------------- kimenetek és utak
def check_outcome_id(value):
    if not isinstance(value, str) or not OUTCOME_RE.match(value):
        raise ApiError("BAD_REQUEST", "Érvénytelen kimenet-azonosító (betű, szám, _ és -; pl. o1).")
    return value


def check_rater(value, required=False):
    if value in (None, ""):
        if required:
            raise ApiError("BAD_REQUEST", "Add meg a kinyerő monogramját (pl. SzK).")
        return None
    if not isinstance(value, str) or not RATER_RE.match(value):
        raise ApiError("BAD_REQUEST", "A monogram betűvel kezdődjön, és csak betűt, számot, _ és - jelet tartalmazzon "
                                      "(legfeljebb 32 karakter).")
    return value


def check_side(value, allow=SIDES):
    v = value.upper() if isinstance(value, str) else value
    if v not in allow:
        raise ApiError("BAD_REQUEST", "Az oldal %s lehet." % " vagy ".join(allow))
    return v


def outcomes(app):
    """A projekt kimenetei a ma-projekt.json-ból: [{id, name, measure, data}] (érvénytelen bejegyzés kimarad)."""
    try:
        meta, _ = app.project_meta()
    except store.StoreError:
        meta = None
    out = []
    for o in (meta or {}).get("outcomes") or []:
        if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not OUTCOME_RE.match(o["id"]):
            continue
        data = o.get("data") if isinstance(o.get("data"), str) else None
        out.append({"id": o["id"], "name": o.get("name"), "measure": o.get("measure") if isinstance(o.get("measure"), str)
                    else None, "data": data})
    return out


def _dir_for(app, data):
    if data:
        try:
            rel = app.store.rel(data)
        except store.StoreError:
            rel = None
        if rel:
            head = posixpath.dirname(rel)
            return (head + "/" if head else "") + KETTOS
    return DEFAULT_DIR


def outcome_info(app, outcome_id):
    """{id, name, measure, data, dir, a, b, json, csv, prov, inbox} — a kimenet kettős kinyerés-fájljainak útja.
    Ismeretlen kimenet → 404 (a kimenetet a Projekt / Protokoll képernyőn lehet felvenni)."""
    check_outcome_id(outcome_id)
    for o in outcomes(app):
        if o["id"] == outcome_id:
            d = _dir_for(app, o["data"])
            base = "%s/%s" % (d, outcome_id)
            info = dict(o, dir=d, a=base + ".A.csv", b=base + ".B.csv", json=base + ".consensus.json",
                        csv=base + ".consensus.csv", inbox=d + "/" + INBOX)
            info["prov"] = store.provenance_relpath(info["csv"])
            for k in ("a", "b", "json", "csv", "prov"):
                try:
                    security.check_relpath(info[k])
                except security.UnsafePath as exc:
                    raise ApiError("FORBIDDEN", exc.message) from None
                app.store.path(info[k])           # kivezető (symlinkes) út → 403
            return info
    raise ApiError("NOT_FOUND", "Nincs ilyen kimenet a projektben: %s. A kimenetet a Projekt képernyőn lehet felvenni."
                   % outcome_id)


def side_rel(info, side):
    return info["a"] if side == "A" else info["b"]


def file_state(app, rel):
    """{path, exists, etag, bytes} — a fájl tartalmának olvasása nélkül is (csak hash)."""
    p = app.store.path(rel)
    try:
        size = p.stat().st_size if p.is_file() else None
    except OSError:
        size = None
    etag = None
    if size is not None:
        try:
            etag = app.store.etag(rel)
        except store.StoreError:
            etag = None
    return {"path": rel, "exists": size is not None, "etag": etag, "bytes": size}


def load_table(app, rel, label):
    """A kinyerő táblája (store.Table) vagy 404 útmutatással."""
    try:
        return app.store.load_table(rel)
    except store.NotFound:
        raise ApiError("NOT_FOUND", "Hiányzik a(z) %s tábla (%s). Importáld a kinyerő CSV-jét (Kettős kinyerés → "
                                    "Importálás), vagy másold a fájlt a kettos/beerkezett/ mappába." % (label, rel),
                       {"path": rel}) from None


def load_consensus(app, info):
    """(dokumentum vagy None, etag)."""
    try:
        doc, etag = app.store.load_json(info["json"])
    except store.Invalid as exc:
        raise ApiError("VALIDATION", "A konszenzus-fájl (%s) sérült: %s" % (info["json"], exc.message),
                       {"path": info["json"]}) from None
    if doc is not None:
        probs = consensus_problems(doc)
        if probs:
            raise ApiError("VALIDATION", "A konszenzus-fájl (%s) nem szk.ma.consensus/v1: %s"
                           % (info["json"], "; ".join(probs[:5])), {"path": info["json"], "problems": probs[:50]})
    return doc, etag


def consensus_problems(doc):
    """Alak-ellenőrzés a szerződés szerint (értékek nélkül)."""
    if not isinstance(doc, dict):
        return ["a dokumentum gyökere objektum legyen"]
    errs = schema_lite.validate(doc, _contracts.ref(CONSENSUS_SCHEMA), _contracts.registry()) \
        if _contracts.available(CONSENSUS_SCHEMA) else []
    out = [e.split(":")[0] + ": a szerződés szerint hibás" for e in errs[:20]]
    if not errs and not isinstance(doc.get("decisions"), list):
        out.append("decisions: lista kell")
    return out


def empty_consensus(outcome_id, key=None):
    return {"schema": CONSENSUS_SCHEMA, "outcome": outcome_id, "key": list(key) if key else None, "raters": {},
            "sources": {}, "decisions": [], "csv": None}


# ---------------------------------------------------------------------------- oszlopok és kulcsok
def canon_fields(header):
    """A fejléc oszlopainak kanonikus neve (a motor column_map-je szerint), pozíció szerint."""
    cmap = api.column_map(list(header))
    used = set()
    out = []
    for name in header:
        hit = None
        for canon, orig in cmap.items():
            if orig == name and canon not in used:
                hit = canon
                break
        if hit is None:
            hit = name
        used.add(hit)
        out.append(hit)
    return out


def key_text(fields_idx, cells, key):
    """A kulcsoszlopok nyers szövege '|'-lal összefűzve (szóköz-levágással) — a motor kulcsának szöveges párja; ha
    valamelyik kulcsoszlop hiányzik, None."""
    parts = []
    for f in key:
        i = fields_idx.get(f)
        if i is None:
            return None
        parts.append((cells[i] if i < len(cells) else "").strip())
    return "|".join(parts)


def index_rows(table, fields, key):
    idx = {f: i for i, f in enumerate(fields)}
    out = {}
    for r in table.rows:
        k = key_text(idx, r["cells"], key) if key else None
        if k is not None and k not in out:
            out[k] = r
    return out, idx


# ---------------------------------------------------------------------------- eltérések + döntések → tételek
def _dkey(key, field):
    return "%s␟%s" % (key, field)


def build_items(result, doc, ta=None, tb=None):
    """(tételek, haladás) a motor összevetéséből és a konszenzus-döntésekből.

    Tétel: {id, level: cell|row, key, field, kind, a, b, row_uid_a, row_uid_b, hint, hint_code, kb, impact,
    auto, needs_decision, decision, stale}. A számot és a súgót a motor adja; itt csak a döntések hozzárendelése
    (kulcs + mező) és a darabszámok (feloldott / feloldatlan / automatikus / elavult) készülnek."""
    decisions = {}
    for d in (doc or {}).get("decisions") or []:
        if isinstance(d, dict) and isinstance(d.get("key"), str) and isinstance(d.get("field"), str):
            decisions[_dkey(d["key"], d["field"])] = d
    key = list(result.get("key") or [])
    rows_a = rows_b = {}
    if ta is not None and tb is not None and key:
        rows_a, _ = index_rows(ta, canon_fields(ta.header), key)
        rows_b, _ = index_rows(tb, canon_fields(tb.header), key)
    items = []
    seen = set()
    for i, d in enumerate(result.get("disagreements") or []):
        kind = d.get("kind")
        auto = kind in KINDS_AUTO
        it = {"id": "c%d" % i, "level": "cell", "key": d.get("key"), "field": d.get("field"), "kind": kind,
              "a": d.get("a"), "b": d.get("b"), "row_uid_a": d.get("row_uid_a"), "row_uid_b": d.get("row_uid_b"),
              "hint": d.get("hint"), "hint_code": d.get("hint_code"), "kb": d.get("kb") or d.get("kb_refs"),
              "impact": d.get("impact"), "auto": auto, "needs_decision": not auto}
        if it["row_uid_a"] is None and it["key"] in rows_a:
            it["row_uid_a"] = rows_a[it["key"]]["row_uid"]
        if it["row_uid_b"] is None and it["key"] in rows_b:
            it["row_uid_b"] = rows_b[it["key"]]["row_uid"]
        items.append(it)
    for side, keys in (("a", result.get("only_a") or []), ("b", result.get("only_b") or [])):
        for j, k in enumerate(keys):
            src = rows_a.get(k) if side == "a" else rows_b.get(k)
            items.append({"id": "r%s%d" % (side, j), "level": "row", "key": k, "field": ROW_FIELD,
                          "kind": "only_" + side, "a": None, "b": None,
                          "row_uid_a": src["row_uid"] if side == "a" and src else None,
                          "row_uid_b": src["row_uid"] if side == "b" and src else None,
                          "hint": None, "hint_code": None, "kb": None, "impact": None, "auto": False,
                          "needs_decision": True})
    total = decided = auto = stale = 0
    for it in items:
        dk = _dkey(it["key"], it["field"])
        d = decisions.get(dk)
        seen.add(dk)
        it["decision"] = None
        it["stale"] = False
        if it["auto"]:
            auto += 1
        if d is not None:
            it["decision"] = {k: d.get(k) for k in ("chosen", "value", "reason", "actor", "ts")}
            it["stale"] = (d.get("a") != it["a"] or d.get("b") != it["b"]) if it["level"] == "cell" else False
            if it["stale"]:
                stale += 1
        if it["needs_decision"]:
            total += 1
            if d is not None and not it["stale"]:
                decided += 1
    orphans = len([k for k in decisions if k not in seen])
    progress = {"total": total, "decided": decided, "unresolved": total - decided, "auto": auto, "stale": stale,
                "orphans": orphans}
    return items, progress


def gate(progress):
    """X009-előjelzés (a motor project audit-ja a döntő): feloldatlan eltérésnél az S08 PASS nem rögzíthető."""
    n = progress["unresolved"]
    return {"code": "X009", "blocked": n > 0,
            "message": ("%d feloldatlan eltérés: amíg van ilyen, az S08 PASS nem rögzíthető (X009), és a konszenzus-CSV "
                        "nem írható ki." % n) if n else "Nincs feloldatlan eltérés (X009 rendben)."}


def pairs(result, ta, tb):
    """[{key, row_uid_a, row_uid_b}] — a motor párosítása (``result.pairs``), ennek hiányában a kulcsoszlopok
    szövegének egyezése (szóköz-levágással). (párok, forrás: engine|server)"""
    if isinstance(result.get("pairs"), list):
        return [p for p in result["pairs"] if isinstance(p, dict)], "engine"
    key = list(result.get("key") or [])
    if not key:
        return [], "server"
    ra, _ = index_rows(ta, canon_fields(ta.header), key)
    rb, _ = index_rows(tb, canon_fields(tb.header), key)
    out = []
    for k, r in ra.items():
        if k in rb:
            out.append({"key": k, "row_uid_a": r["row_uid"], "row_uid_b": rb[k]["row_uid"]})
    return out, "server"


# ---------------------------------------------------------------------------- konszenzus-tábla
class Unresolved(Exception):
    def __init__(self, items):
        super().__init__("unresolved")
        self.items = items


def consensus_rows(ta, tb, result, items, decisions, taken_uids=None):
    """A konszenzus-tábla a döntésekből (a motor ``consensus_table``-je hiányában): (fejléc, sorok, egyeztetett
    cellák). A sorrend az A-é; a cella szövege a döntésé (a: az A szövege, b: a B-é, other: a megadott érték), az
    egyező és a csak formátumban eltérő cella az A szövege. Számot nem értelmez és nem számol (szövegválasztás).
    Feloldatlan eltérésnél Unresolved."""
    key = list(result.get("key") or [])
    fa, fb = canon_fields(ta.header), canon_fields(tb.header)
    ia = {f: i for i, f in enumerate(fa)}
    ib = {f: i for i, f in enumerate(fb)}
    extra = [(f, tb.header[i]) for i, f in enumerate(fb) if f not in ia]
    header = list(ta.header) + [name for _f, name in extra]
    fields = list(fa) + [f for f, _n in extra]
    dec = {_dkey(d["key"], d["field"]): d for d in decisions}
    need = {_dkey(it["key"], it["field"]): it for it in items if it["needs_decision"]}
    rows_b, _ = index_rows(tb, fb, key) if key else ({}, None)
    only_a = set(result.get("only_a") or [])
    only_b = list(result.get("only_b") or [])
    missing = [it for dk, it in need.items() if dk not in dec or it.get("stale")]
    if missing:
        raise Unresolved(missing)
    taken = set(taken_uids or ())
    out, reconciled = [], []

    def cell_b(rb, f):
        i = ib.get(f)
        return (rb["cells"][i] if rb is not None and i is not None and i < len(rb["cells"]) else "")

    for r in ta.rows:
        k = key_text(ia, r["cells"], key) if key else None
        if k in only_a:
            d = dec.get(_dkey(k, ROW_FIELD))
            if d is not None and d.get("chosen") == "b":
                continue
        rb = rows_b.get(k) if k is not None else None
        cells = []
        for col, f in enumerate(fields):
            if col < len(ta.header):
                text = r["cells"][col] if col < len(r["cells"]) else ""
            else:
                text = cell_b(rb, f)
            d = dec.get(_dkey(k, f)) if k is not None else None
            if d is not None and _dkey(k, f) in need:
                text = "" if d.get("value") is None else d["value"]
                reconciled.append({"row_uid": r["row_uid"], "field": f, "decision": d})
            cells.append(text)
        taken.add(r["row_uid"])
        out.append({"row_uid": r["row_uid"], "cells": cells})
    for k in only_b:
        d = dec.get(_dkey(k, ROW_FIELD))
        rb = rows_b.get(k)
        if d is None or d.get("chosen") != "b" or rb is None:
            continue
        uid = rb["row_uid"]
        if uid in taken:
            uid = store.new_row_uid(taken)
        taken.add(uid)
        cells = []
        for col, f in enumerate(fields):
            cells.append(cell_b(rb, f))
        out.append({"row_uid": uid, "cells": cells})
    return header, out, reconciled


def engine_consensus(fn, ta, tb, rel_a, rel_b, abs_a, abs_b, doc, key):
    """A motor konszenzus-táblája: {header, rows[{row_uid?, cells}], reconciled?[{row_uid, field}]} alakra hozva."""
    out = call(fn, table_arg(ta, rel_a, abs_a, fn), table_arg(tb, rel_b, abs_b, fn), doc, key=key)
    if isinstance(out, (list, tuple)) and len(out) == 2:
        out = {"header": out[0], "rows": out[1]}
    if not isinstance(out, dict) or not isinstance(out.get("header"), list) or not isinstance(out.get("rows"), list):
        raise ApiError("INTERNAL", "A motor konszenzus-táblája nem {header, rows} alakú (integrációs hiba).")
    rows = []
    for r in out["rows"]:
        if isinstance(r, dict):
            rows.append({"row_uid": r.get("row_uid"), "cells": ["" if c is None else str(c) for c in r.get("cells")
                                                                  or []]})
        else:
            rows.append({"row_uid": None, "cells": ["" if c is None else str(c) for c in r]})
    return [str(h) for h in out["header"]], rows, list(out.get("reconciled") or [])


# ---------------------------------------------------------------------------- CSV-bájtok és atomikus írás
def csv_bytes(app, rel, header, rows, fmt=None):
    """A tábla bájtjai a tároló formátumtartó CSV-írójával (a meglévő fájl formátuma — a változatlan sorok bájtra
    azonosak —, különben ``fmt``), a visszaolvashatóság ellenőrzésével. ``rel`` None: új fájl (pl. letöltendő
    sablon). → (bájtok, a visszaolvasott Table)."""
    cur = None
    if rel is not None:
        path = app.store.path(rel)
        cur = path.read_bytes() if path.is_file() else None
    orig = store.parse_csv_bytes(cur) if cur is not None else None
    hdr, uid_pos, subm = store._normalize_submission(header, rows)
    taken = {u for u, _ in subm if u}
    sub = []
    for uid, cells in subm:
        if uid is None:
            uid = store.new_row_uid(taken)
            taken.add(uid)
        sub.append((uid, cells))
    out_fmt = orig.fmt if orig is not None else (fmt.copy() if fmt is not None else store.default_format())
    data = store._encode(store._render(orig, hdr, sub, out_fmt, True, uid_pos), out_fmt, hdr, sub)
    parsed = store._reads_back(data, hdr, sub)
    if parsed is None:
        alt = out_fmt.copy()
        alt.quoting = alt.header_quoting = "all"
        data = store._encode(store._render(orig, hdr, sub, alt, True, uid_pos), alt, hdr, sub)
        parsed = store._reads_back(data, hdr, sub)
    if parsed is None:
        raise ApiError("VALIDATION", "A tábla így nem olvasható vissza egyértelműen (elválasztó karakter a cellákban?). "
                                     "Ellenőrizd a cellákat.", {"path": rel})
    return data, store._to_table(rel or "sablon.csv", parsed, store.sha256_bytes(data))


def atomic_write(app, items):
    """Több fájl EGY lépésben (2.4, 4.8): items = [(rel, bájtok | None, if_match)] — minden feltétel (ETag) a
    tároló zárja alatt ellenőrizve (None bájt: csak-ellenőrzés, pl. a forrástábla nem változhatott), minden tmp-fájl
    elkészül, majd os.replace sorrendben; eltérésnél 409 (semmi sem íródik), Excel-zárnál 423. A tároló belső
    írója (``ProjectStore._write_many``) a projekt egyetlen több-fájlos atomikus primitívje (a tábla + eredet
    mentése is ezt használja). → {rel: új etag} (a csak-ellenőrzött fájloké a mostani)."""
    st = app.store
    prepared, expect, out = [], {}, {}
    with st._lock:
        for rel, data, if_match in items:
            rel = st.rel(rel)
            path = st.path(rel)
            cur = path.read_bytes() if path.is_file() else None
            cur_etag = None if cur is None else store.sha256_bytes(cur)
            st._precondition(rel, if_match, cur_etag)
            expect[rel] = cur_etag
            if data is None:
                out[rel] = cur_etag
                continue
            out[rel] = store.sha256_bytes(data)
            if cur is None or cur != data:
                prepared.append((rel, path, data))
        if prepared:
            st._write_many(prepared, expect={rel: expect[rel] for rel, _p, _d in prepared})
    return out


def b64decode(text, limit=MAX_FILE_BYTES):
    if not isinstance(text, str):
        raise ApiError("BAD_REQUEST", "A content_b64 base64-szöveg legyen.")
    if len(text) > (limit * 4) // 3 + 8:
        raise ApiError("PAYLOAD_TOO_LARGE", "A fájl túl nagy (legfeljebb %d MB)." % (limit // (1024 * 1024)))
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        raise ApiError("BAD_REQUEST", "A content_b64 nem érvényes base64.") from None


def b64encode(data):
    return base64.b64encode(data).decode("ascii")
