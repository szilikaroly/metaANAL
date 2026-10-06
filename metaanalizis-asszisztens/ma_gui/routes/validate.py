# -*- coding: utf-8 -*-
"""Élő validálás és átváltás (terv 3.4, 3.5.3, 3.5.4, 4.3, 4.7, 6.3) — in-process, a motor-homlokzaton át.

- ``POST /api/validate`` ← ``szk.ma.validate-request/v1`` (a tábla mentetlen piszkozata: fejléc és NYERS
  cellaszövegek; ``dataset``, ``client_seq`` kiegészítő mezők) → ``szk.ma.validation/v1``
  (``api.validate_table``). Nem ment, nem naplóz. Minden megállapítás ``acknowledged`` mezőt kap: a
  „Nem hiba — indoklás” döntés azonosítója (projekt.sqlite), ha van ilyen aktív döntés ugyanarra a
  szabályra, sorra (row_uid) és mezőkre — különben null. A döntés gépi ``context``-je a
  ``POST /api/log/decision``-ből jön (az activity-láncban és, ha a motor tárolja, a projektnaplóban).
- ``POST /api/convert`` ← ``szk.ma.convert-request/v1`` → ``szk.ma.convert-result/v1`` (``api.convert``):
  a bemenetek nyers szövegek, a motor parszol és számol; a ``cell_text`` a cél tábla tizedesjelével.

A számokat és a szövegeket kizárólag a motor állítja elő; a szerver csak továbbít és jelöl."""
import json
import os

from metaelemzes import api

from .. import security
from ..router import ApiError, Result
from . import _contracts
from ._common import has_journal

VALIDATION_SCHEMA = "szk.ma.validation/v1"
VALIDATE_REQUEST = "szk.ma.validate-request/v1"
CONVERT_SCHEMA = "szk.ma.convert-result/v1"
CONVERT_REQUEST = "szk.ma.convert-request/v1"
ACK_KIND = "validation"


# ---------------------------------------------------------------------------- „Nem hiba” döntések
def _stat_key(app):
    out = []
    for path in (app.activity.path, app.project_root / "projekt.sqlite", app.project_root / "projekt.sqlite-wal"):
        try:
            st = os.stat(str(path))
            out.append((st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


def _context_of(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) and value.get("kind") == ACK_KIND else None


def ack_entries(app):
    """[(döntés-id, context)] — az aktív „Nem hiba — indoklás” döntések (a fájlok állapota szerint
    gyorsítótárazva). Forrás: az activity-lánc log.decision sorai és a projektnapló context-oszlopa."""
    key = _stat_key(app)
    cached = getattr(app, "_ack_cache", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    entries = []
    if has_journal(app):
        try:
            decisions = api.project_list(str(app.project_root), "decisions")
        except Exception:                                   # noqa: BLE001 — zárolt napló: nincs jelölés
            decisions = None
        if decisions is not None:
            active = {d.get("id") for d in decisions if (d.get("status") or "active") == "active"}
            seen = set()
            for d in decisions:
                ctx = _context_of(d.get("context"))
                if ctx is not None:
                    entries.append((d["id"], ctx))
                    seen.add(d["id"])
            try:
                records = app.activity.read()
            except Exception:                               # noqa: BLE001
                records = []
            for rec in records:
                if rec.get("action") != "log.decision":
                    continue
                det = rec.get("details") or {}
                did = det.get("id")
                ctx = _context_of(det.get("context"))
                if ctx is not None and isinstance(did, int) and not isinstance(did, bool) and did not in seen:
                    entries.append((did, ctx))
                    seen.add(did)
            entries = [(i, c) for i, c in entries if i in active]
    app._ack_cache = (key, entries)
    return entries


def _finding_uids(f, row_uids):
    if f.get("row_uid"):
        return [f["row_uid"]]
    out = []
    for r in f.get("rows") or ():
        if isinstance(r, int) and 0 <= r < len(row_uids) and row_uids[r]:
            out.append(row_uids[r])
    if not out and isinstance(f.get("row"), int) and 0 <= f["row"] < len(row_uids) and row_uids[f["row"]]:
        out.append(row_uids[f["row"]])
    return out


def mark_acknowledged(doc, entries, dataset, row_uids):
    """A validálási dokumentum megállapításaira az ``acknowledged`` (döntés-id vagy null) — a
    legutóbbi illeszkedő aktív döntésé. Illeszkedés: azonos kód; azonos tábla (ha mindkettő ismert);
    a döntés row_uid-ja a megállapítás sorai közt van (táblaszintű megállapításnál mindkettő üres);
    a döntés mezői (ha megadta) = a megállapítás mezői."""
    for f in doc.get("findings") or ():
        if f.get("acknowledged") is not None:
            continue                        # a motor már jelölte (a projektnaplóból)
        uids = _finding_uids(f, row_uids)
        hit = None
        for did, ctx in entries:
            if ctx.get("code") != f.get("code"):
                continue
            if dataset and ctx.get("dataset") and ctx.get("dataset") != dataset:
                continue
            cu = ctx.get("row_uid")
            if (cu or None) is None:
                if uids:
                    continue
            elif cu not in uids:
                continue
            fields = ctx.get("fields")
            if fields and sorted(fields) != sorted(f.get("fields") or []):
                continue
            hit = did if hit is None else max(hit, did)
        f["acknowledged"] = hit
    return doc


# ---------------------------------------------------------------------------- végpontok
def post_validate(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    table = body["table"]
    header, rows = table["header"], table["rows"]
    security.check_table_limits(header, rows)
    dataset = body.get("dataset") if isinstance(body.get("dataset"), str) else None
    lines = table.get("lines")
    if lines is not None and len(lines) != len(rows):
        raise ApiError("BAD_REQUEST", "A table.lines hossza egyezzen a sorok számával.")
    # a motor jelöli az 'acknowledged'-et a projektnapló context-oszlopából (code + row_uid — többsoros szabálynál
    # az összes sor — + fields + dataset); a mark_acknowledged csak a csak az activity-láncban rögzített
    # döntésekre tartalék (a motor által már jelölt megállapításokat kihagyja)
    doc = api.validate_table(header, [["" if c is None else c for c in r] for r in rows], body["measure"],
                             body.get("options") or {}, decimal_mark=table.get("decimal_mark"),
                             delimiter=table.get("delimiter"), lines=lines,
                             project_dir=str(app.project_root) if has_journal(app) else None, dataset=dataset)
    uid_col = next((i for i, c in enumerate(header) if str(c).strip().lower() == "row_uid"), None)
    row_uids = [(r[uid_col] if uid_col is not None and uid_col < len(r) else None) for r in rows]
    mark_acknowledged(doc, ack_entries(app), dataset, row_uids)
    return Result(doc, VALIDATION_SCHEMA)


def post_convert(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    request = {k: v for k, v in body.items() if k != "client_seq"}
    if len(request.get("inputs") or {}) > 20:
        raise ApiError("BAD_REQUEST", "Túl sok bemenet (legfeljebb 20).")
    try:
        return Result(api.convert(request), CONVERT_SCHEMA)
    except ValueError as exc:
        raise ApiError("VALIDATION", _value_free(str(exc), request.get("inputs"))) from None


def _value_free(message, inputs):
    """A motor hibaüzenete, ha nem idézi a bevitt értékek egyikét sem; különben általános üzenet (T10: a
    bevitt cellaszöveg — pl. egy TAJ-szám — hibaüzenetbe, toastba nem kerülhet; WS-4)."""
    vals = [str(v).strip() for v in (inputs or {}).values()
            if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip()]
    if any(len(v) >= 2 and v in message for v in vals):
        return "Az átváltás egyik bemenete nem értelmezhető (ellenőrizd a mezőket)."
    return message


def register(router):
    router.add("POST", "/api/validate", post_validate, schema=VALIDATION_SCHEMA,
               request_schema=_contracts.ref(VALIDATE_REQUEST), response_schema=_contracts.ref(VALIDATION_SCHEMA))
    router.add("POST", "/api/convert", post_convert, schema=CONVERT_SCHEMA,
               request_schema=_contracts.ref(CONVERT_REQUEST), response_schema=_contracts.ref(CONVERT_SCHEMA))
