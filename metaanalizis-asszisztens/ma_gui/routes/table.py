# -*- coding: utf-8 -*-
"""Adattáblák és cellaszintű eredet (3.4, 4.8, 7.4).

- ``GET /api/table?dataset=<relút>`` → ``{dataset, etag, header, rows[{row_uid, cells, line}], format,
  uid}`` + ``ETag`` fejléc; ``dataset`` nélkül a 03_adatok táblái.
- ``PUT /api/table`` (If-Match) ← ``{dataset, header, rows[{row_uid, cells}], provenance?, format?,
  phi_override?, consent?}``: PHI/TAJ-szkenner → ``privacy.can_write`` → ``store.save_table``
  (409 cellaszintű diffel, 423 Excel-zárnál) → activity-sor (cellaérték nélkül).
- ``POST /api/table/import`` ← ``{text, delimiter?, has_header?}``: TSV/CSV-beillesztés → sorok
  (NEM ment), PHI-találatokkal (érték nélkül).
- ``GET/PUT /api/provenance?dataset=`` ↔ ``szk.ma.provenance/v1`` (If-Match).

A cellák szövegek; számot itt senki nem értelmez (a motoré)."""
import csv
import io

from metaelemzes import projekt

from .. import privacy, security, store
from ..router import ApiError, Result
from ._common import body_bool, body_str, dataset_rel, if_match

SCHEMA = "szk.ma.table/v1"
LIST_SCHEMA = "szk.ma.table-list/v1"
IMPORT_SCHEMA = "szk.ma.table-import/v1"
PROV_SCHEMA = "szk.ma.provenance/v1"
MAX_PHI_FINDINGS = 200
DELIMITERS = {"auto": None, "tab": "\t", "\t": "\t", ";": ";", ",": ",", "|": "|"}

FORMAT_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "encoding": {"type": "string", "maxLength": 20},
        "bom": {"type": "boolean"},
        "delimiter": {"type": "string", "maxLength": 1},
        "decimal_mark": {"type": ["string", "null"], "maxLength": 1},
        "newline": {"enum": ["crlf", "lf"]},
        "final_newline": {"type": "boolean"},
        "quoting": {"type": "string", "maxLength": 10},
        "header_quoting": {"type": "string", "maxLength": 10},
    },
    "additionalProperties": False,
}
PUT_SCHEMA = {
    "type": "object",
    "required": ["dataset", "header", "rows"],
    "properties": {
        "dataset": {"type": "string", "maxLength": 1024},
        "header": {"type": "array", "items": {"type": "string", "maxLength": 2000}},
        "rows": {"type": "array"},
        "provenance": {"type": ["object", "null"]},
        "format": FORMAT_SCHEMA,
        "phi_override": {"type": ["string", "null"], "maxLength": 4000},
        "consent": {"type": "boolean"},
    },
    "additionalProperties": False,
}
IMPORT_REQUEST = {
    "type": "object",
    "required": ["text"],
    "properties": {
        "text": {"type": "string"},
        "delimiter": {"enum": sorted(DELIMITERS)},
        "has_header": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def _cells_of(rows):
    """A beküldött sorok cellalistái a méretkorláthoz és a szkennerhez (alakhiba: 400)."""
    out = []
    for i, r in enumerate(rows):
        cells = r.get("cells") if isinstance(r, dict) else r
        if not isinstance(cells, list):
            raise ApiError("BAD_REQUEST", "A(z) %d. sor alakja érvénytelen ({row_uid, cells} vagy lista)." % (i + 1))
        out.append(cells)
    return out


def _phi_details(findings):
    return {"phi": findings[:MAX_PHI_FINDINGS], "summary": privacy.describe_findings(findings)}


# ---------------------------------------------------------------------------- tábla
def get_table(req):
    app = req.app
    raw = req.arg("dataset")
    if raw is None:
        return Result({"tables": app.store.list_tables()}, LIST_SCHEMA)
    app.require_open()
    rel = dataset_rel(req, raw)
    table = app.store.load_table(rel)
    data = table.to_json()
    return Result(data, SCHEMA, etag=table.etag)


def put_table(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    rel = dataset_rel(req, body["dataset"])
    header, rows = body["header"], body["rows"]
    cells = _cells_of(rows)
    security.check_table_limits(header, cells)
    override = body_str(body, "phi_override", max_len=4000)
    consent = body_bool(body, "consent")
    findings = privacy.scan_table(header, cells, max_findings=MAX_PHI_FINDINGS)
    phi = bool(findings) and override is None
    prov = body.get("provenance")
    targets = [rel] + ([store.provenance_relpath(rel)] if prov is not None else [])
    for target in targets:
        ok, reason = app.can_write(target, phi_detected=phi, consent=consent)
        if not ok:
            raise ApiError("FORBIDDEN", reason, _phi_details(findings) if phi else {"path": target})
    table = app.store.save_table(rel, header, rows, if_match(req), provenance=prov, fmt=body.get("format"))
    warnings = []
    decision_id = None
    if findings and override is not None:
        decision_id = _log_phi_override(app, rel, override, warnings)
        warnings.append("PHI-gyanú felülbírálva (indokolt, naplózott döntés): %s" % privacy.describe_findings(findings))
    outputs = [{"path": rel, "sha256": table.etag}]
    if prov is not None:
        prel = store.provenance_relpath(rel)
        outputs.append({"path": prel})
    details = {"n_rows": len(table.rows), "n_columns": len(table.header), "phi_findings": len(findings),
               "phi_override": bool(findings and override is not None), "consent": consent}
    if decision_id is not None:
        details["decision_id"] = decision_id
    if app.log_activity("table.save", outputs=outputs, details=details) is None:
        warnings.append(app.ACTIVITY_WARNING)
    return Result(table.to_json(), SCHEMA, warnings=warnings, etag=table.etag)


def _log_phi_override(app, rel, reason, warnings):
    """A PHI-felülbírálás döntésként a projektnaplóba (ha van); az indoklás csak oda kerül."""
    if not (app.project_root / "projekt.sqlite").is_file():
        warnings.append("Nincs projektnapló (projekt.sqlite): a PHI-felülbírálás csak az activity-naplóba került.")
        return None
    try:
        return projekt.log_decision(str(app.project_root), "user", "PHI-gyanú felülbírálva: %s" % rel,
                                    rationale=reason, stage=None, kb_db=app.kb_db, check_kb=False)
    except Exception:                                      # noqa: BLE001 — a mentés már megtörtént
        warnings.append("A PHI-felülbírálás döntését nem sikerült a projektnaplóba írni.")
        return None


def post_import(req):
    """TSV/CSV-beillesztés értelmezése (a store CSV-felismerésével); nem ment."""
    body = req.json_object()
    text = body["text"]
    if "\x00" in text:
        raise ApiError("BAD_REQUEST", "A beillesztett szöveg NUL karaktert tartalmaz.")
    delim = DELIMITERS.get(body.get("delimiter") or "auto")
    has_header = body_bool(body, "has_header", True)
    if delim is None:
        parsed = store.parse_csv_bytes(text.encode("utf-8"))
        delim = parsed.fmt.delimiter
        recs = ([parsed.header.cells] if parsed.header is not None else []) + [r.cells for r in parsed.rows]
        decimal_mark = parsed.fmt.decimal_mark
    else:
        try:
            recs = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
        except csv.Error:
            raise ApiError("VALIDATION", "A beillesztett szöveg nem olvasható (hibás idézőjel?).") from None
        decimal_mark = None
    if has_header:
        header, rows = (recs[0] if recs else []), recs[1:]
    else:
        width = max([len(r) for r in recs] or [0])
        header, rows = ["oszlop%d" % (i + 1) for i in range(width)], recs
    security.check_table_limits(header, rows)
    findings = privacy.scan_table(header, rows, max_findings=MAX_PHI_FINDINGS)
    warnings = [privacy.describe_findings(findings)] if findings else []
    data = {"header": header, "rows": rows, "delimiter": delim, "decimal_mark": decimal_mark,
            "n_rows": len(rows), "n_columns": len(header), "phi": findings[:MAX_PHI_FINDINGS]}
    return Result(data, IMPORT_SCHEMA, warnings=warnings)


# ---------------------------------------------------------------------------- eredet
def get_provenance(req):
    app = req.app
    app.require_open()
    rel = dataset_rel(req, req.arg("dataset"))
    doc, etag, state = app.store.load_provenance(rel)
    return Result({"provenance": doc, "etag": etag, "state": state}, PROV_SCHEMA, etag=etag)


def put_provenance(req):
    app = req.app
    app.require_open()
    rel = dataset_rel(req, req.arg("dataset"))
    doc = req.json_object()
    prel = store.provenance_relpath(rel)
    ok, reason = app.can_write(prel)
    if not ok:
        raise ApiError("FORBIDDEN", reason, {"path": prel})
    new_etag = app.store.save_provenance(rel, doc, if_match(req))
    warnings = []
    cells = doc.get("cells")
    if app.log_activity("provenance.save", outputs=[{"path": prel, "sha256": new_etag}],
                        details={"dataset": rel, "n_entries": len(cells) if isinstance(cells, list) else 0}) is None:
        warnings.append(app.ACTIVITY_WARNING)
    saved, etag, state = app.store.load_provenance(rel)
    return Result({"provenance": saved, "etag": etag, "state": state}, PROV_SCHEMA, warnings=warnings, etag=etag)


def register(router):
    router.add("GET", "/api/table", get_table, schema=SCHEMA)
    router.add("PUT", "/api/table", put_table, schema=SCHEMA, request_schema=PUT_SCHEMA)
    router.add("POST", "/api/table/import", post_import, schema=IMPORT_SCHEMA, request_schema=IMPORT_REQUEST)
    router.add("GET", "/api/provenance", get_provenance, schema=PROV_SCHEMA)
    router.add("PUT", "/api/provenance", put_provenance, schema=PROV_SCHEMA)
