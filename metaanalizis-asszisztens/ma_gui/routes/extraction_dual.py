# -*- coding: utf-8 -*-
"""Kettős kinyerés és egyeztetés (terv 3.4, 3.5.5, 4.8, 4.9, 6.4 X009, 7.4; 11. fejezet 5. döntés).

- ``GET /api/kettos`` → ``{outcomes[{id, name, measure, data, dir, a, b, consensus, csv}], inbox[], engine{…}}``:
  kimenetenként a két kinyerő táblája, a konszenzus-fájl és a konszenzus-CSV állapota (út, etag, méret), valamint
  a ``kettos/beerkezett/`` mappa CSV-fájljai (5. döntés: a második kinyerő fájlja ide is másolható).
- ``POST /api/compare`` ← ``{outcome, key?, tolerance?, tables?}`` → ``szk.ma.dual-view/v1``: a motor
  ``szk.ma.compare-result/v1``-e (eltérések, súgók, hatás, κ) + a két tábla nyers cellái (a cella-diffhez) + a
  döntések hozzárendelése (tételek, haladás, X009-előjelzés) + ETag (a konszenzus-fájlé).
- ``POST /api/reconcile`` ← ``{outcome, decisions[{key, field, chosen, value?, reason}], clear?[{key, field}],
  write?: null|'kettos'|'outcome', outcome_if_match?, actor?, dry_run?}`` + If-Match (a konszenzus-fájl etagje; új
  fájlnál nincs): a döntések a ``<kimenet>.consensus.json``-ba; ``write`` mellett a konszenzus-CSV (+ eredet-
  oldalfájl ``reconciled`` módszerrel) a ``kettos/<kimenet>.consensus.csv``-be, vagy (``outcome``) a kimenet
  adattáblájába — EGY atomikus lépésben (minden ETag a tároló zárja alatt, minden tmp kész, majd os.replace). Indoklás
  nélküli döntés 422; feloldatlan eltéréssel a CSV nem írható (409 GATE_BLOCKED, X009). Activity-sor értékek, kulcsok
  és indoklás nélkül; a döntések egy bejegyzésben a projektnaplóba (agent=user, S05; a szövegben mezőnév + választás +
  indoklás, cellaérték nélkül).
- ``POST /api/kettos/import`` ← ``{outcome, side: A|B, rater?, content_b64 | path, replace?}`` — a kinyerő CSV-je
  (a böngészőből base64-ben, vagy a beérkezett mappából): formátumtartó, bájtra azonos mentés, PHI-szkenner (értékek
  nélkül), meglévő eltérő fájlt csak ``replace`` ír felül.
- ``POST /api/kettos/export`` ← ``{outcome, side: A|B|consensus, template?}`` → ``{filename, content_b64, sha256}``
  (a fájlcseréhez; ``template``: üres sablon a második kinyerőnek — fejléc + kulcsoszlopok).

Számot a szerver nem számol és cellát nem értelmez: az összevetés a motoré (``extraction_dual_common.ENGINE``)."""
import json

from metaelemzes import api

from .. import privacy, security, store
from ..router import ApiError, Result
from . import _contracts
from . import extraction_dual_common as C
from ._common import body_bool, has_journal, if_match, log_activity_or_warn, metadata_phi, now_iso, phi_text_guard

VIEW_SCHEMA = "szk.ma.dual-view/v1"
LIST_SCHEMA = "szk.ma.dual-list/v1"
IMPORT_SCHEMA = "szk.ma.dual-import/v1"
EXPORT_SCHEMA = "szk.ma.dual-export/v1"
STAGE = "S05"
MAX_RATIONALE = 4000
MAX_TABLE_ROWS_IN_VIEW = security.MAX_ROWS

_KEY = {"type": ["array", "null"], "items": {"type": "string", "minLength": 1, "maxLength": 200}, "maxItems": 10}
_OUTCOME = {"type": "string", "minLength": 1, "maxLength": 64}
_COMPARE_REQ = {"type": "object", "required": ["outcome"],
                "properties": {"outcome": _OUTCOME, "key": _KEY, "tolerance": {"type": ["number", "null"], "minimum": 0},
                               "tables": {"type": "boolean"}, "client_seq": {"type": "integer", "minimum": 0}},
                "additionalProperties": False}
_DECISION = {"type": "object", "required": ["key", "field", "chosen", "reason"],
             "properties": {"key": {"type": "string", "maxLength": 2000}, "field": {"type": "string", "minLength": 1,
                                                                                     "maxLength": 200},
                            "chosen": {"enum": list(C.CHOICES)},
                            "value": {"type": ["string", "null"], "maxLength": C.MAX_VALUE},
                            "reason": {"type": "string", "maxLength": C.MAX_REASON}},
             "additionalProperties": False}
_CLEAR = {"type": "object", "required": ["key", "field"],
          "properties": {"key": {"type": "string", "maxLength": 2000}, "field": {"type": "string", "maxLength": 200}},
          "additionalProperties": False}
_RECONCILE_REQ = {"type": "object", "required": ["outcome"],
                  "properties": {"outcome": _OUTCOME, "key": _KEY,
                                 "decisions": {"type": "array", "items": _DECISION, "maxItems": C.MAX_DECISIONS},
                                 "clear": {"type": "array", "items": _CLEAR, "maxItems": C.MAX_DECISIONS},
                                 "write": {"enum": [None, "kettos", "outcome"]},
                                 "outcome_if_match": {"type": ["string", "null"], "maxLength": 200},
                                 "actor": {"type": ["string", "null"], "maxLength": 32},
                                 "dry_run": {"type": "boolean"}, "client_seq": {"type": "integer", "minimum": 0}},
                  "additionalProperties": False}
_IMPORT_REQ = {"type": "object", "required": ["outcome", "side"],
               "properties": {"outcome": _OUTCOME, "side": {"enum": ["A", "B", "a", "b"]},
                              "rater": {"type": ["string", "null"], "maxLength": 32},
                              "content_b64": {"type": "string"}, "path": {"type": "string", "maxLength": 1024},
                              "filename": {"type": ["string", "null"], "maxLength": 255},
                              "replace": {"type": "boolean"}},
               "additionalProperties": False}
_EXPORT_REQ = {"type": "object", "required": ["outcome", "side"],
               "properties": {"outcome": _OUTCOME, "side": {"enum": ["A", "B", "a", "b", "consensus"]},
                              "template": {"type": "boolean"}},
               "additionalProperties": False}
_VIEW_RESP = {"type": "object", "required": ["outcome", "compare", "items", "progress", "gate", "consensus"],
              "properties": {"compare": _contracts.ref(C.COMPARE_SCHEMA)}}


# ---------------------------------------------------------------------------- segédek
def _clone(doc):
    """Mély másolat (a konszenzus-dokumentum JSON-ból jön, így a JSON oda-vissza pontos)."""
    return json.loads(json.dumps(doc, ensure_ascii=False))


def _norm_etag(tag):
    if tag is None:
        return None
    t = str(tag).strip()
    if t.startswith("W/"):
        t = t[2:]
    return t.strip('"') or None


def _precheck(rel, im, cur_etag):
    """If-Match a konszenzus-fájlra: meglévő fájlnál az etagje kell, új fájlnál semmi (különben 409)."""
    im = _norm_etag(im)
    if (cur_etag is None and im is None) or (cur_etag is not None and im == cur_etag):
        return
    raise ApiError("CONFLICT", "A konszenzus-fájlt (%s) közben más is módosította (például a másik ablakban vagy egy "
                               "ágens). Töltsd újra az összevetést, és döntsd el újra a módosított tételeket." % rel,
                   {"path": rel, "etag": cur_etag})


def _compare(app, info, ta, tb, key, tolerance):
    fn = C.need("compare")
    abs_a, abs_b = app.store.path(info["a"]), app.store.path(info["b"])
    try:
        res = C.call(fn, C.table_arg(ta, info["a"], abs_a, fn), C.table_arg(tb, info["b"], abs_b, fn),
                     key=list(key) if key else None, tolerance=tolerance, measure=info.get("measure"))
    except (KeyError, ValueError) as exc:
        raise ApiError("VALIDATION", "A motor nem tudta összevetni a két táblát: %s" % exc) from None
    if not isinstance(res, dict):
        raise ApiError("INTERNAL", "A motor összevetés-eredménye nem objektum (integrációs hiba).")
    return C.check_contract(res, C.COMPARE_SCHEMA, "összevetés-eredménye")


def _agreement(result):
    """Az egyetértési táblázat Methods-szövege: a motor ``agreement_report``-ja, ennek hiányában az eredmény
    ``agreement_text``/``methods_text`` mezője ({hu, en}); különben None (a felület elrejti a gombot)."""
    fn = C.engine_fn("agreement_report")
    if fn is not None:
        try:
            out = C.call(fn, result)
        except (KeyError, ValueError, TypeError):
            out = None
        if isinstance(out, dict) and isinstance(out.get("text"), dict):
            return out["text"]
        if isinstance(out, dict) and "hu" in out:
            return out
    for k in ("agreement_text", "methods_text"):
        v = result.get(k)
        if isinstance(v, dict) and "hu" in v:
            return v
    return None


def _columns(ta, tb):
    fa, fb = C.canon_fields(ta.header), C.canon_fields(tb.header)
    out = []
    for i, f in enumerate(fa):
        j = fb.index(f) if f in fb else None
        out.append({"field": f, "a": ta.header[i], "b": tb.header[j] if j is not None else None})
    for j, f in enumerate(fb):
        if f not in fa:
            out.append({"field": f, "a": None, "b": tb.header[j]})
    return out


def _table_json(table, limit=MAX_TABLE_ROWS_IN_VIEW):
    return {"header": list(table.header), "rows": [{"row_uid": r["row_uid"], "cells": list(r["cells"])}
                                                   for r in table.rows[:limit]],
            "n_rows": len(table.rows), "etag": table.etag, "format": table.fmt.to_json()}


def _view(app, info, ta, tb, doc, etag, result, tables=True):
    items, progress = C.build_items(result, doc, ta, tb)
    prs, src = C.pairs(result, ta, tb)
    cols = _columns(ta, tb)
    warnings = []
    sources = (doc or {}).get("sources") or {}
    for side, t in (("a", ta), ("b", tb)):
        s = sources.get(side) if isinstance(sources, dict) else None
        if isinstance(s, dict) and s.get("sha256") and s.get("sha256") != t.etag:
            warnings.append("A(z) %s tábla a legutóbbi döntések óta megváltozott: az érintett döntések elavultak, ezeket "
                            "újra el kell dönteni." % side.upper())
    if progress["orphans"]:
        warnings.append("%d korábbi döntés már nem tartozik eltéréshez (a táblák azóta egyeznek); a konszenzus-fájlban "
                        "megmarad, de nem használjuk." % progress["orphans"])
    csv_state = C.file_state(app, info["csv"])
    data = {"outcome": {k: info.get(k) for k in ("id", "name", "measure", "data")},
            "dir": info["dir"], "key": list(result.get("key") or []),
            "key_candidates": [c["field"] for c in cols if c["a"] is not None and c["b"] is not None],
            "columns": cols,
            "files": {"a": {"path": info["a"], "etag": ta.etag, "n_rows": len(ta.rows)},
                      "b": {"path": info["b"], "etag": tb.etag, "n_rows": len(tb.rows)},
                      "consensus": {"path": info["json"], "etag": etag, "exists": doc is not None},
                      "csv": csv_state},
            "raters": dict((doc or {}).get("raters") or {}),
            "compare": result, "pairs": prs, "pairs_source": src,
            "items": items, "progress": progress, "gate": C.gate(progress),
            "agreement_text": _agreement(result),
            "consensus": {"exists": doc is not None, "etag": etag, "csv": (doc or {}).get("csv"),
                          "decisions": len((doc or {}).get("decisions") or [])},
            "engine": C.engine_status()}
    if tables:
        data["a"] = _table_json(ta)
        data["b"] = _table_json(tb)
    return data, warnings


def _load_both(app, info):
    return C.load_table(app, info["a"], "A"), C.load_table(app, info["b"], "B")


# ---------------------------------------------------------------------------- GET /api/kettos
def get_list(req):
    app = req.app
    app.require_open()
    out, dirs = [], []
    for o in C.outcomes(app):
        try:
            info = C.outcome_info(app, o["id"])
        except ApiError:
            continue
        doc, etag, n_dec = None, None, 0
        try:
            doc, etag = C.load_consensus(app, info)
            n_dec = len((doc or {}).get("decisions") or [])
        except ApiError:
            doc = None
        out.append({"id": o["id"], "name": o["name"], "measure": o["measure"], "data": o["data"], "dir": info["dir"],
                    "a": C.file_state(app, info["a"]), "b": C.file_state(app, info["b"]),
                    "consensus": {"path": info["json"], "exists": doc is not None, "etag": etag, "decisions": n_dec,
                                  "raters": dict((doc or {}).get("raters") or {}), "csv": (doc or {}).get("csv")},
                    "csv": C.file_state(app, info["csv"])})
        if info["inbox"] not in dirs:
            dirs.append(info["inbox"])
    inbox = []
    for d in dirs or [C.DEFAULT_DIR + "/" + C.INBOX]:
        p = app.store.path(d)
        if not p.is_dir():
            continue
        for f in sorted(p.iterdir()):
            if not f.is_file() or f.name.startswith(".") or not f.name.lower().endswith((".csv", ".tsv", ".txt")):
                continue
            rel = "%s/%s" % (d, f.name)
            name = f.name
            guess = None
            for s in C.SIDES:
                if ".%s." % s in name or name[:-4].endswith(("_" + s, "-" + s, "." + s)):
                    guess = s
            outcome_guess = next((o["id"] for o in out if name.startswith(o["id"] + ".") or
                                  name.startswith(o["id"] + "_")), None)
            inbox.append({"path": rel, "name": name, "bytes": f.stat().st_size, "side_guess": guess,
                          "outcome_guess": outcome_guess})
    return Result({"outcomes": out, "inbox": inbox, "engine": C.engine_status(), "default_dir": C.DEFAULT_DIR},
                  LIST_SCHEMA)


# ---------------------------------------------------------------------------- POST /api/compare
def post_compare(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    info = C.outcome_info(app, body["outcome"])
    C.need("compare")
    ta, tb = _load_both(app, info)
    doc, etag = C.load_consensus(app, info)
    key = body.get("key") or (doc or {}).get("key") or None
    result = _compare(app, info, ta, tb, key, body.get("tolerance"))
    data, warnings = _view(app, info, ta, tb, doc, etag, result, tables=body_bool(body, "tables", True))
    return Result(data, VIEW_SCHEMA, warnings=warnings, etag=etag)


# ---------------------------------------------------------------------------- POST /api/reconcile
def _apply_decisions(body, doc, items, actor):
    """(új dokumentum, megváltozott döntések, törölt darabszám). Ismeretlen / formátum-eltérés / indoklás nélküli
    döntés: 422 — az üzenet a mezőt és a sorszámot nevezi meg, cellaértéket és kulcsot nem."""
    by = {C._dkey(it["key"], it["field"]): it for it in items}
    new = _clone(doc) if doc is not None else None
    decisions = [d for d in ((new or {}).get("decisions") or []) if isinstance(d, dict)]
    index = {C._dkey(d.get("key"), d.get("field")): i for i, d in enumerate(decisions)}
    cleared = 0
    drop = set()
    for c in body.get("clear") or []:
        dk = C._dkey(c["key"], c["field"])
        if dk in index:
            drop.add(index[dk])
            cleared += 1
    changed = []
    seen = set()
    for n, d in enumerate(body.get("decisions") or []):
        where = "%d. döntés (mező: %s)" % (n + 1, d["field"])
        dk = C._dkey(d["key"], d["field"])
        if dk in seen:
            raise ApiError("VALIDATION", "Ugyanarra az eltérésre két döntés érkezett: %s." % where, {"index": n})
        seen.add(dk)
        it = by.get(dk)
        if it is None:
            raise ApiError("VALIDATION", "A %s már nem tartozik eltéréshez (a táblák közben változhattak). Töltsd újra "
                                         "az összevetést." % where, {"index": n, "field": d["field"]})
        if not it["needs_decision"]:
            raise ApiError("VALIDATION", "A %s csak írásmódban tér el (%s): nem kell dönteni, a konszenzus az A szövegét "
                                         "veszi át." % (where, it["kind"]), {"index": n, "field": d["field"]})
        chosen = d["chosen"]
        if it["level"] == "row" and chosen not in C.ROW_CHOICES:
            raise ApiError("VALIDATION", "A %s egy egész sorra vonatkozik: csak az A vagy a B változata választható."
                           % where, {"index": n})
        reason = d["reason"].strip()
        if not reason:
            raise ApiError("VALIDATION", "Indoklás nélkül nem rögzíthető döntés: %s. Írd le röviden, miért ez a helyes "
                                         "érték (pl. „Table 2 lábjegyzete: SD”)." % where,
                           {"index": n, "field": d["field"], "missing": "reason"})
        if it["level"] == "row":
            value = None
        elif chosen == "a":
            value = it["a"]
        elif chosen == "b":
            value = it["b"]
        else:
            value = d.get("value")
            if value is None:
                raise ApiError("VALIDATION", "Saját érték választásához add meg az értéket: %s." % where,
                               {"index": n, "field": d["field"], "missing": "value"})
        for text in (reason, value or ""):
            if "\x00" in text:
                raise ApiError("BAD_REQUEST", "A %s NUL karaktert tartalmaz." % where)
        nd = {"key": d["key"], "field": d["field"], "chosen": chosen, "value": value, "reason": reason,
              "actor": actor, "ts": now_iso(), "a": it["a"], "b": it["b"], "kind": it["kind"]}
        i = index.get(dk)
        prev = decisions[i] if i is not None else None
        if prev is not None and i not in drop and all(prev.get(k) == nd[k] for k in ("chosen", "value", "reason", "a",
                                                                                     "b")):
            continue
        if i is not None:
            decisions[i] = nd
            drop.discard(i)
        else:
            index[dk] = len(decisions)
            decisions.append(nd)
        changed.append(nd)
    decisions = [d for i, d in enumerate(decisions) if i not in drop]
    return decisions, changed, cleared


def _prov_entry(uid, field, d, json_rel):
    rec = {"key": d.get("key"), "a": d.get("a"), "b": d.get("b"), "chosen": d.get("chosen"), "reason": d.get("reason"),
           "decided_by": d.get("actor"), "decided_at": d.get("ts"), "consensus": json_rel}
    return {"row_uid": uid, "field": field, "value_as_entered": d.get("value"), "method": "reconciled",
            "estimated": False, "reconciliation": rec, "extracted_by": d.get("actor"), "extracted_at": d.get("ts")}


def _build_table(app, info, ta, tb, result, items, decisions, target):
    """(fejléc, sorok, egyeztetett cellák [(row_uid, mező, döntés)]) — a motor konszenzus-táblájával, ennek
    hiányában a szöveg-választó tartalékkal. Feloldatlan eltérésnél 409 GATE_BLOCKED."""
    fn = C.engine_fn("consensus_table")
    dec = {C._dkey(d["key"], d["field"]): d for d in decisions}
    if fn is not None:
        doc = {"schema": C.CONSENSUS_SCHEMA, "key": list(result.get("key") or []),
               "decisions": [{k: d.get(k) for k in ("key", "field", "chosen", "value", "reason", "actor", "ts")}
                             for d in decisions]}
        try:
            header, rows, rec = C.engine_consensus(fn, ta, tb, info["a"], info["b"], app.store.path(info["a"]),
                                                   app.store.path(info["b"]), doc, list(result.get("key") or []))
        except (KeyError, ValueError) as exc:          # a motor magyar üzenete (cellaérték nélkül)
            raise ApiError("VALIDATION", "A motor nem tudta összeállítani a konszenzus-táblát: %s" % exc) from None
        reconciled = []
        for r in rec:
            if isinstance(r, dict) and r.get("row_uid") and r.get("field"):
                d = dec.get(C._dkey(r.get("key"), r["field"])) if r.get("key") is not None else None
                reconciled.append((r["row_uid"], r["field"], d or {"chosen": r.get("chosen"), "value": r.get("value")}))
        return header, rows, reconciled, "engine"
    try:
        header, rows, rec = C.consensus_rows(ta, tb, result, items, decisions)
    except C.Unresolved as exc:
        raise _gate_error(len(exc.items), exc.items) from None
    return header, rows, [(r["row_uid"], r["field"], r["decision"]) for r in rec], "server"


def _gate_error(n, items):
    fields = sorted({it["field"] for it in items if it.get("field")})[:50]
    return ApiError("GATE_BLOCKED", "%d eltérés még feloldatlan (vagy a döntése elavult): a konszenzus-CSV csak akkor "
                                    "írható ki, ha minden eltérésről döntöttél (X009)." % n,
                    {"code": "X009", "unresolved": n, "fields": fields,
                     "blockers": [{"code": "X009", "title": "Kettős kinyerés: feloldatlan eltérés (%d)" % n}]})


def _merged_prov(app, target, csv_sha, reconciled, json_rel, uids):
    """A kimenet adattáblájának eredet-oldalfájlja: a nem egyeztetett cellák eddigi eredete megmarad (ha a sor még
    létezik), az egyeztetett cellák ``reconciled`` bejegyzést kapnak (az előző bejegyzés a history végére)."""
    try:
        old, old_etag, _st = app.store.load_provenance(target)
    except store.Invalid as exc:
        raise ApiError("VALIDATION", "A kimenet eredet-oldalfájlja sérült: %s" % exc.message) from None
    rset = {(u, f) for u, f, _d in reconciled}
    prev = {(c.get("row_uid"), c.get("field")): c for c in old.get("cells") or [] if isinstance(c, dict)}
    cells = [c for c in old.get("cells") or [] if isinstance(c, dict) and (c.get("row_uid"), c.get("field")) not in rset
             and c.get("row_uid") in uids]
    for u, f, d in reconciled:
        e = _prov_entry(u, f, d, json_rel)
        p = prev.get((u, f))
        if p is not None:
            hist = list(p.get("history") or [])
            hist.append({k: p.get(k) for k in ("value_as_entered", "method", "extracted_by", "extracted_at")})
            e["history"] = hist[-50:]
        cells.append(e)
    return {"schema": C.PROV_SCHEMA, "table": target, "table_sha256": csv_sha, "cells": cells}, old_etag


def post_reconcile(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    info = C.outcome_info(app, body["outcome"])
    write = body.get("write")
    dry = body_bool(body, "dry_run")
    actor = C.check_rater(body.get("actor")) or app.actor
    C.need("compare")
    ta, tb = _load_both(app, info)
    doc, etag = C.load_consensus(app, info)
    if not dry:
        _precheck(info["json"], if_match(req), etag)
    key = body.get("key") or (doc or {}).get("key") or None
    result = _compare(app, info, ta, tb, key, None)
    items, _p0 = C.build_items(result, doc, ta, tb)
    decisions, changed, cleared = _apply_decisions(body, doc, items, actor)
    new_doc = _clone(doc) if doc is not None else C.empty_consensus(info["id"])
    new_doc.update({"schema": C.CONSENSUS_SCHEMA, "outcome": info["id"], "key": list(result.get("key") or []),
                    "decisions": decisions,
                    "sources": {"a": {"path": info["a"], "sha256": ta.etag}, "b": {"path": info["b"], "sha256": tb.etag}}})
    new_doc.setdefault("raters", {})
    items2, progress = C.build_items(result, new_doc, ta, tb)
    warnings = []
    # adatvédelem (7.4): a döntések szövege (saját érték, indoklás) PHI-mintákon; találatnál csak a _privat/ alá
    texts = [{"value": d.get("value"), "reason": d.get("reason")} for d in changed]
    phi = metadata_phi({"decisions": texts})
    if phi:
        ok, reason = app.can_write(info["json"], phi_detected=True)
        if not ok:
            raise ApiError("FORBIDDEN", "Azonosítónak látszó adat (TAJ-szám, születési dátum vagy e-mail) egy döntés "
                                        "szövegében: írd át azonosító nélkül. (%s)" % reason,
                           {"phi": [{"path": f["path"], "pattern": f["pattern"]} for f in phi[:50]]})
    rationale = _rationale(changed, cleared)
    if rationale and has_journal(app):
        warnings += phi_text_guard(app, [("reason", rationale)])
    writes = []
    target = None
    table_info = None
    if write:
        if progress["unresolved"]:
            unresolved = [it for it in items2 if it["needs_decision"] and (it["decision"] is None or it["stale"])]
            raise _gate_error(progress["unresolved"], unresolved)
        header, rows, reconciled, builder = _build_table(app, info, ta, tb, result, items2, decisions, write)
        if write == "outcome":
            if not info.get("data"):
                raise ApiError("BAD_REQUEST", "A kimenetnek nincs adattáblája a ma-projekt.json-ban (data); előbb add meg.")
            target = app.store.rel(info["data"])
            try:
                security.check_relpath(target)
            except security.UnsafePath as exc:
                raise ApiError("FORBIDDEN", exc.message) from None
        else:
            target = info["csv"]
        data, table = C.csv_bytes(app, target, header, rows, fmt=ta.fmt)
        uids = {r["row_uid"] for r in table.rows}
        prel = store.provenance_relpath(target)
        if write == "outcome":
            prov, prov_etag = _merged_prov(app, target, table.etag, reconciled, info["json"], uids)
            cur = C.file_state(app, target)
            target_im = body.get("outcome_if_match")
            if cur["exists"] and _norm_etag(target_im) != cur["etag"]:
                raise ApiError("CONFLICT", "A kimenet adattáblája (%s) már létezik: a felülírásához a betöltött "
                                           "változat etagje kell (outcome_if_match). Nézd meg a táblát, majd erősítsd "
                                           "meg a felülírást." % target, {"path": target, "etag": cur["etag"]})
        else:
            prov = {"schema": C.PROV_SCHEMA, "table": target, "table_sha256": table.etag,
                    "cells": [_prov_entry(u, f, d, info["json"]) for u, f, d in reconciled]}
            prov_etag = C.file_state(app, prel)["etag"]
            target_im = C.file_state(app, target)["etag"]
        try:
            store.check_provenance(prov, prel)
        except store.StoreError as exc:
            raise ApiError("VALIDATION", "Az eredet-oldalfájl nem állítható össze: %s" % exc.message) from None
        findings = privacy.scan_table(header, [r["cells"] for r in rows], max_findings=200)
        for rel in (target, prel, info["json"]):
            ok, why = app.can_write(rel, phi_detected=bool(findings))
            if not ok:
                raise ApiError("FORBIDDEN", why, {"path": rel, "phi_findings": len(findings),
                                                  "summary": privacy.describe_findings(findings) if findings else None})
        new_doc["csv"] = {"path": target, "sha256": table.etag, "prov": prel, "target": write, "builder": builder,
                          "rows": len(table.rows), "reconciled": len(reconciled), "written_at": now_iso(), "by": actor}
        writes = [(target, data, target_im), (prel, store.json_bytes(prov), prov_etag)]
        table_info = {"path": target, "sha256": table.etag, "prov": prel, "rows": len(table.rows),
                      "reconciled": len(reconciled), "builder": builder,
                      "preview": {"header": header, "rows": [r["cells"] for r in rows[:200]]} if dry else None}
    else:
        ok, why = app.can_write(info["json"])
        if not ok:
            raise ApiError("FORBIDDEN", why, {"path": info["json"]})
    if dry:
        data, w2 = _view(app, info, ta, tb, new_doc, etag, result, tables=False)
        data["dry_run"] = True
        data["written"] = {"csv": table_info, "changed": len(changed), "cleared": cleared}
        return Result(data, VIEW_SCHEMA, warnings=warnings + w2, etag=etag)
    json_data = store.json_bytes(new_doc)
    # A és B nem változhatott közben (a konszenzus azokra épül): csak-ellenőrzés tétel (adat nélkül)
    checks = [(info["a"], None, ta.etag), (info["b"], None, tb.etag)]
    new_etags = C.atomic_write(app, checks + writes + [(info["json"], json_data, etag)])
    new_etag = new_etags[info["json"]]
    outputs = [{"path": rel, "sha256": new_etags[app.store.rel(rel)]} for rel, _d, _im in writes]
    outputs.append({"path": info["json"], "sha256": new_etag})
    log_activity_or_warn(app, "kettos.reconcile", warnings,
                         inputs=[{"path": info["a"], "sha256": ta.etag}, {"path": info["b"], "sha256": tb.etag}],
                         outputs=outputs,
                         details={"outcome": info["id"], "changed": len(changed), "cleared": cleared,
                                  "decided": progress["decided"], "unresolved": progress["unresolved"],
                                  "write": write, "fields": sorted({d["field"] for d in changed})[:50]})
    decision_id = None
    if (rationale or write) and has_journal(app):
        decision_id = _journal(app, info, changed, cleared, rationale, table_info, warnings)
    elif rationale or write:
        warnings.append("Nincs projektnapló (projekt.sqlite): a döntések csak a konszenzus-fájlba és az activity-naplóba "
                        "kerültek.")
    data, w2 = _view(app, info, ta, tb, new_doc, new_etag, result, tables=False)
    data["written"] = {"csv": table_info, "changed": len(changed), "cleared": cleared, "decision_id": decision_id}
    return Result(data, VIEW_SCHEMA, warnings=warnings + w2, etag=new_etag)


_CHOICE_HU = {"a": "az A változata", "b": "a B változata", "other": "saját érték"}


def _rationale(changed, cleared):
    """A projektnapló szövege: mezőnév + választás + indoklás döntésenként — cellaérték és kulcs NÉLKÜL."""
    lines = ["%s — %s: %s" % ("egész sor" if d["field"] == C.ROW_FIELD else d["field"], _CHOICE_HU[d["chosen"]],
                              d["reason"]) for d in changed]
    if cleared:
        lines.append("%d korábbi döntés visszavonva." % cleared)
    text = "\n".join(lines)
    return text[:MAX_RATIONALE - 1] + "…" if len(text) > MAX_RATIONALE else text


def _journal(app, info, changed, cleared, rationale, table_info, warnings):
    what = "Kettős kinyerés egyeztetése (%s): %d döntés" % (info["id"], len(changed))
    if cleared:
        what += ", %d visszavonva" % cleared
    if table_info:
        what += "; konszenzus-tábla kiírva (%s)" % table_info["path"]
    fields = sorted({d["field"] for d in changed if d["field"] != C.ROW_FIELD})[:50]
    ctx = {"kind": "other", "code": "X009", "outcome": info["id"], "dataset": info["json"], "fields": fields or None}
    try:
        out = api.project_log(str(app.project_root), "user", what, rationale=rationale or None, stage=STAGE,
                              kb_db=app.kb_db, actor=app.actor, context=ctx)
        warnings.extend(out.get("warnings") or [])
        return out.get("id")
    except Exception:                                      # noqa: BLE001 — a fájlok már kiírva
        warnings.append("A döntéseket nem sikerült a projektnaplóba írni (a konszenzus-fájlban megvannak).")
        return None


# ---------------------------------------------------------------------------- import / export (5. döntés)
def _read_inbox(app, info, raw_path):
    rel = app.store.rel(raw_path)
    try:
        security.check_relpath(rel)
    except security.UnsafePath as exc:
        raise ApiError("FORBIDDEN", exc.message) from None
    if not (rel.startswith(info["inbox"] + "/") and rel.lower().endswith((".csv", ".tsv", ".txt"))):
        raise ApiError("FORBIDDEN", "Importálni csak a %s/ mappából lehet (CSV-fájl)." % info["inbox"])
    p = app.store.path(rel)
    try:
        size = p.stat().st_size
    except FileNotFoundError:
        raise ApiError("NOT_FOUND", "Nincs ilyen fájl: %s" % rel) from None
    if size > C.MAX_FILE_BYTES:
        raise ApiError("PAYLOAD_TOO_LARGE", "A fájl túl nagy (legfeljebb %d MB)." % (C.MAX_FILE_BYTES // (1024 * 1024)))
    return rel, p.read_bytes()


def post_import(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    info = C.outcome_info(app, body["outcome"])
    side = C.check_side(body["side"])
    rater = C.check_rater(body.get("rater"))
    replace = body_bool(body, "replace")
    src = None
    if body.get("path") is not None:
        src_rel, raw = _read_inbox(app, info, body["path"])
        src = {"path": src_rel, "sha256": store.sha256_bytes(raw)}
    elif body.get("content_b64") is not None:
        raw = C.b64decode(body["content_b64"])
    else:
        raise ApiError("BAD_REQUEST", "Az import törzse {content_b64} (a fájl tartalma base64-ben) vagy {path} (a "
                                      "beérkezett mappában).")
    if not raw.strip():
        raise ApiError("VALIDATION", "Az importált fájl üres.")
    parsed = store.parse_csv_bytes(raw, max_columns=security.MAX_COLUMNS)
    if parsed.header is None:
        raise ApiError("VALIDATION", "Az importált fájlban nincs fejléc (oszlopnevek az első sorban).")
    side_rel = C.side_rel(info, side)
    table = store._to_table(side_rel, parsed, store.sha256_bytes(raw))
    security.check_table_limits(table.header, [r["cells"] for r in table.rows])
    findings = privacy.scan_table(table.header, [r["cells"] for r in table.rows], max_findings=200)
    ok, why = app.can_write(side_rel, phi_detected=bool(findings))
    if not ok:
        raise ApiError("FORBIDDEN", why, {"path": side_rel, "phi": findings[:200],
                                          "summary": privacy.describe_findings(findings) if findings else None})
    warnings = []
    if findings:
        warnings.append(privacy.describe_findings(findings))
    cur = C.file_state(app, side_rel)
    state = "new"
    if cur["exists"]:
        if cur["etag"] == table.etag:
            state = "same"
        elif not replace:
            raise ApiError("CONFLICT", "A(z) %s tábla már létezik, más tartalommal. Ha a most importált változat a helyes, "
                                       "importáld újra „felülírás” jelöléssel (a korábbi döntések elavulhatnak)."
                           % side, {"path": side_rel, "etag": cur["etag"]})
        else:
            state = "replaced"
    other = C.side_rel(info, "B" if side == "A" else "A")
    if C.file_state(app, other)["exists"]:
        try:
            ot = app.store.load_table(other)
            fa, fo = set(C.canon_fields(table.header)), set(C.canon_fields(ot.header))
            if fa != fo:
                warnings.append("A két tábla oszlopai eltérnek (csak az egyikben: %s). Az összevetés a közös oszlopokat "
                                "veti össze." % ", ".join(sorted(fa ^ fo)[:20]))
        except store.StoreError:
            pass
    doc, doc_etag = C.load_consensus(app, info)
    items = []
    if state != "same":
        items.append((side_rel, raw, cur["etag"]))
    new_doc = None
    if rater and (doc is None or (doc.get("raters") or {}).get(side.lower()) != rater):
        new_doc = _clone(doc) if doc is not None else C.empty_consensus(info["id"])
        new_doc.setdefault("raters", {})[side.lower()] = rater
        items.append((info["json"], store.json_bytes(new_doc), doc_etag))
    if doc is not None and (doc.get("decisions") or []) and state == "replaced":
        warnings.append("A korábbi döntések erre a táblára épültek: az érintett tételeket az összevetés elavultként "
                        "jelöli, ezeket újra el kell dönteni.")
    etags = C.atomic_write(app, items) if items else {}
    outputs = [{"path": rel, "sha256": etags[app.store.rel(rel)]} for rel, _d, _im in items]
    log_activity_or_warn(app, "kettos.import", warnings, inputs=[src] if src else (), outputs=outputs,
                         details={"outcome": info["id"], "side": side, "state": state, "n_rows": len(table.rows),
                                  "n_columns": len(table.header), "phi_findings": len(findings),
                                  "from_inbox": src is not None, "replace": replace})
    return Result({"outcome": info["id"], "side": side, "path": side_rel, "state": state, "etag": table.etag,
                   "rows": len(table.rows), "columns": len(table.header), "header": list(table.header),
                   "format": table.fmt.to_json(), "rater": rater, "source": src["path"] if src else None},
                  IMPORT_SCHEMA, warnings=warnings)


def post_export(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    info = C.outcome_info(app, body["outcome"])
    side = C.check_side(body["side"], allow=("A", "B", "CONSENSUS"))
    template = body_bool(body, "template")
    if side == "CONSENSUS":
        rel = info["csv"]
        label = "konszenzus"
    else:
        rel = C.side_rel(info, side)
        label = side
    st = C.file_state(app, rel)
    if not st["exists"]:
        raise ApiError("NOT_FOUND", "Nincs exportálható %s tábla (%s)." % (label, rel), {"path": rel})
    raw = app.store.path(rel).read_bytes()
    name = rel.rsplit("/", 1)[-1]
    rows_n = None
    if template:
        t = app.store.load_table(rel)
        doc, _e = C.load_consensus(app, info)
        key = [k for k in ((doc or {}).get("key") or []) if isinstance(k, str)]
        fields = C.canon_fields(t.header)
        if not key:
            key = [f for f in ("study_id", "study", "arm") if f in fields]
        keep = {i for i, f in enumerate(fields) if f in key}
        rows = [{"row_uid": None, "cells": [c if i in keep else "" for i, c in enumerate(r["cells"])]} for r in t.rows]
        raw, tt = C.csv_bytes(app, None, t.header, rows, t.fmt, write_uids=False)
        rows_n = len(tt.rows)
        name = "%s.%s.sablon.csv" % (info["id"], "B" if side == "A" else "A")
    warnings = []
    log_activity_or_warn(app, "kettos.export", warnings, inputs=[{"path": rel, "sha256": st["etag"]}],
                         details={"outcome": info["id"], "side": side, "template": template})
    return Result({"outcome": info["id"], "side": side, "path": rel, "filename": name, "template": template,
                   "content_b64": C.b64encode(raw), "sha256": store.sha256_bytes(raw), "bytes": len(raw),
                   "rows": rows_n, "media_type": "text/csv"}, EXPORT_SCHEMA, warnings=warnings)


def register(router):
    router.add("GET", "/api/kettos", get_list, schema=LIST_SCHEMA)
    router.add("POST", "/api/kettos/import", post_import, schema=IMPORT_SCHEMA, request_schema=_IMPORT_REQ)
    router.add("POST", "/api/kettos/export", post_export, schema=EXPORT_SCHEMA, request_schema=_EXPORT_REQ)
    router.add("POST", "/api/compare", post_compare, schema=VIEW_SCHEMA, request_schema=_COMPARE_REQ,
               response_schema=_VIEW_RESP)
    router.add("POST", "/api/reconcile", post_reconcile, schema=VIEW_SCHEMA, request_schema=_RECONCILE_REQ,
               response_schema=_VIEW_RESP)
