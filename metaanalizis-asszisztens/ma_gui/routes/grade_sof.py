# -*- coding: utf-8 -*-
"""Summary of Findings kimenetenként (terv 2.4, 3.4, 3.5.12, 4.14, 7.6): a motor ``sof()``-ja számol, a munkapad
csak menti és exportálja (``06_kezirat/sof/<kimenet>.sof.json``; író: munkapad, motor-számokból).

- ``GET /api/sof/<kimenet>[?run=]`` → ``szk.ma.sof-view/v1`` + ETag (a mentett fájlé): ``{outcome, run, path,
  saved (szk.ma.sof/v1 | null), saved_matches_run, preview (a motor sof() kimenete a mentett — vagy az
  alapértelmezett: kontroll-pool — alapkockázatokkal), inputs{assumed_risks, footnotes}, certainty,
  certainty_source (grade | journal | null), engine{…}}``. A motor ``sof`` hiányában 424.
- ``PUT /api/sof/<kimenet>`` ← ``{assumed_risks: [{label?, source: control_pool | external, per_1000?: nyers szöveg,
  note?}], footnotes?: [{id?, text}], run_id?, dry_run?}`` + If-Match. A motor ``sof(run, assumed_risks, certainty,
  footnotes)`` adja a sorokat (relatív hatás = a futás display_text-je, résztvevők (vizsgálatok), abszolút hatás
  /1000 CI-vel alapkockázatonként, bizonyosság, lábjegyzetek a GRADE-indoklásokból, közérthető összefoglaló
  mondat); ``dry_run``: csak előnézet (nem ír). Mentés: PHI-szkenner + írás-tartás, atomikusan (If-Match),
  activity-sor ``sof.save`` (értékek nélkül).
- ``POST /api/sof/<kimenet>/export`` ← ``{format: csv | md, lang?: en | hu}`` → ``szk.ma.sof-export/v1``:
  ``{format, lang, path, sha256, bytes, url, expires_at, content?}`` — a MENTETT SoF-ból
  ``06_kezirat/sof/<kimenet>.sof.<lang>.<csv|md>``; az ``url`` aláírt, 10 perces letöltési cím. A CSV
  Excel-biztos (7.6): az ``= + - @``, tabulátorral vagy CR-rel kezdődő SZÖVEGES cella ``'`` előtagot kap
  (a SoF minden cellája a motor kész szövege); UTF-8 BOM, CRLF, a magyar változat ``;``, az angol ``,``
  elválasztóval. A Markdown változat szövege a válaszban is benne van (másoláshoz).
A szám mindig a motor szövege; a munkapad nem számol és nem formáz."""
import csv
import io

from .. import store
from ..router import ApiError, Result
from . import grade_engine as E
from ._common import accepts, log_activity_or_warn, phi_doc_guard
from .grade_common import outcome_of, require_run, run_dir_abs, run_summary, text_or_none

VIEW_SCHEMA = "szk.ma.sof-view/v1"
SOF_SCHEMA = "szk.ma.sof/v1"
EXPORT_SCHEMA = "szk.ma.sof-export/v1"
SOF_DIR = "06_kezirat/sof"
SOF_REL = SOF_DIR + "/%s.sof.json"
EXPORT_REL = SOF_DIR + "/%s.sof.%s.%s"
SOURCES = ("control_pool", "external")
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
MAX_RISKS = 6
DEFAULT_RISKS = [{"label": None, "source": "control_pool", "per_1000": None, "note": None}]

ASSUMED = {"type": "array", "maxItems": MAX_RISKS, "items": {
    "type": "object", "required": ["source"], "additionalProperties": False,
    "properties": {"label": {"type": ["string", "null"], "maxLength": 200},
                   "source": {"enum": list(SOURCES)},
                   "per_1000": {"type": ["string", "null"], "maxLength": 32},
                   "note": {"type": ["string", "null"], "maxLength": 500}}}}
FOOTNOTES = {"type": "array", "maxItems": 20, "items": {
    "type": "object", "required": ["text"], "additionalProperties": False,
    "properties": {"id": {"type": ["string", "null"], "maxLength": 8},
                   "text": {"type": "string", "maxLength": 2000}}}}
PUT_REQUEST = {
    "type": "object",
    "properties": {"assumed_risks": ASSUMED, "footnotes": FOOTNOTES,
                   "run_id": {"type": ["string", "null"], "pattern": r"^\d{8}T\d{6}Z-[0-9a-f]{6}$"},
                   "dry_run": {"type": "boolean"}, "client_seq": {"type": ["integer", "null"]}},
    "additionalProperties": False,
}
EXPORT_REQUEST = {
    "type": "object", "required": ["format"],
    "properties": {"format": {"enum": ["csv", "md"]}, "lang": {"enum": ["en", "hu", None]}},
    "additionalProperties": False,
}
# az export alapértelmezett oszlopfejei (ha a motor SoF-ja nem ad 'columns'-t)
COLUMNS = (
    ("outcome", {"hu": "Kimenet", "en": "Outcome"}),
    ("participants_text", {"hu": "Résztvevők (vizsgálatok)", "en": "No. of participants (studies)"}),
    ("relative_text", {"hu": "Relatív hatás (95% CI)", "en": "Relative effect (95% CI)"}),
    ("assumed_risk", {"hu": "Alapkockázat", "en": "Assumed risk"}),
    ("absolute_text", {"hu": "Abszolút hatás (95% CI)", "en": "Absolute effect (95% CI)"}),
    ("certainty_text", {"hu": "A bizonyíték bizonyossága (GRADE)", "en": "Certainty of the evidence (GRADE)"}),
    ("footnotes", {"hu": "Lábjegyzet", "en": "Footnotes"}),
)
LABELS = {"footnotes": {"hu": "Lábjegyzetek", "en": "Footnotes"},
          "statement": {"hu": "Összefoglaló", "en": "Summary statement"}}


def excel_safe(cell):
    """Excel-képletinjekció elleni őr (7.6): a képletnek látszó SZÖVEGES cella ' előtagot kap."""
    s = "" if cell is None else str(cell)
    return "'" + s if s.startswith(FORMULA_START) else s


def _pick(v, lang):
    if isinstance(v, dict):
        if isinstance(v.get(lang), str):
            return v[lang]
        for k in ("text", "label"):
            if k in v:
                return _pick(v[k], lang)
        other = v.get("en" if lang == "hu" else "hu")
        return other if isinstance(other, str) else ""
    if isinstance(v, list):
        return ", ".join(_pick(x, lang) for x in v if x is not None)
    return "" if v is None else str(v)


def _cell(row, key, lang):
    if key == "assumed_risk":
        ar = row.get("assumed_risk")
        if isinstance(ar, dict):
            text = _pick(ar.get("text"), lang)
            label = _pick(ar.get("label"), lang)
            return ("%s: %s" % (label, text)) if label and text else (text or label)
        return _pick(row.get("assumed_risk_text") or ar, lang)
    if key == "certainty_text" and row.get(key) is None:
        return _pick(row.get("certainty"), lang)
    if key == "footnotes" and row.get(key) is None:
        return _pick(row.get("footnote_refs"), lang)
    return _pick(row.get(key), lang)


def _columns(doc, lang):
    cols = doc.get("columns")
    if isinstance(cols, list) and cols and all(isinstance(c, dict) and isinstance(c.get("key"), str) for c in cols):
        return [(c["key"], _pick(c.get("label") or c["key"], lang)) for c in cols]
    return [(k, lbl[lang]) for k, lbl in COLUMNS]


def table_rows(doc, lang):
    """(fejléc, sorok, lábjegyzetek, összefoglaló) a SoF-dokumentumból, a kért nyelven (a motor szövegei)."""
    cols = _columns(doc, lang)
    rows = [[_cell(r, k, lang) for k, _lbl in cols] for r in doc.get("rows") or [] if isinstance(r, dict)]
    notes = []
    for f in doc.get("footnotes") or []:
        if isinstance(f, dict):
            notes.append(("%s " % f["id"] if f.get("id") else "") + _pick(f.get("text"), lang))
        elif f is not None:
            notes.append(_pick(f, lang))
    return [lbl for _k, lbl in cols], rows, notes, _pick(doc.get("statement"), lang)


def render_csv(doc, lang):
    head, rows, notes, statement = table_rows(doc, lang)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";" if lang == "hu" else ",", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow([excel_safe(c) for c in head])
    for r in rows:
        w.writerow([excel_safe(c) for c in r])
    if notes or statement:
        w.writerow([])
    if notes:
        w.writerow([excel_safe(LABELS["footnotes"][lang])])
        for n in notes:
            w.writerow([excel_safe(n)])
    if statement:
        w.writerow([excel_safe(LABELS["statement"][lang]), excel_safe(statement)])
    return "\ufeff" + buf.getvalue()


def _md(s):
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_md(doc, lang):
    head, rows, notes, statement = table_rows(doc, lang)
    title = _pick(doc.get("title"), lang) or ("Summary of findings" if lang == "en" else "Az eredmények összefoglalása")
    out = ["## %s" % _md(title), "", "| %s |" % " | ".join(_md(c) for c in head),
           "|%s|" % "|".join("---" for _ in head)]
    out += ["| %s |" % " | ".join(_md(c) for c in r) for r in rows]
    if notes:
        out += ["", "**%s**" % LABELS["footnotes"][lang], ""] + ["- %s" % _md(n) for n in notes]
    if statement:
        out += ["", "**%s:** %s" % (LABELS["statement"][lang], _md(statement))]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------- bemenetek
def _risks(raw):
    out = []
    for i, r in enumerate(raw if raw is not None else DEFAULT_RISKS):
        label = text_or_none(r.get("label"), 200, "assumed_risks[%d].label" % i)
        per = text_or_none(r.get("per_1000"), 32, "assumed_risks[%d].per_1000" % i)
        note = text_or_none(r.get("note"), 500, "assumed_risks[%d].note" % i)
        src = r.get("source")
        if src == "external" and per is None:
            raise ApiError("VALIDATION", "A külső alapkockázathoz add meg az értékét (esemény / 1000 fő), és nevezd "
                                         "meg a forrását (%d. alapkockázat)." % (i + 1), {"field": "per_1000", "index": i})
        out.append({"label": label, "source": src, "per_1000": per if src == "external" else None, "note": note})
    if not out:
        raise ApiError("VALIDATION", "Legalább egy alapkockázat kell (alapértelmezés: a kontrollkarok összesített "
                                     "kockázata).")
    return out


def _user_notes(raw):
    out = []
    for i, f in enumerate(raw or []):
        text = text_or_none(f.get("text"), 2000, "footnotes[%d].text" % i)
        if text:
            out.append({"id": text_or_none(f.get("id"), 8, "footnotes[%d].id" % i), "text": text, "source": "user"})
    return out


def _grade_inputs(app, oid):
    """(GRADE-dokumentum | None, bizonyosság, forrás, GRADE-lábjegyzetek) — a mentett GRADE-ítéletből (ha a motor
    tára elérhető), különben a projektnapló legutóbbi sorából."""
    from . import grade as _grade
    doc = None
    if E.has("grade_get"):
        try:
            doc = E.call("grade_get", project_dir=str(app.project_root), outcome=oid)
        except (ApiError, ValueError):
            doc = None
    notes = []
    if isinstance(doc, dict):
        for d in _grade.DOMAINS:
            dom = (doc.get("domains") or {}).get(d) or {}
            if dom.get("rationale") and (dom.get("step") not in (0, None) or dom.get("rating") in ("suspected",
                                                                                                    "strongly suspected")):
                notes.append({"id": None, "source": "grade:%s" % d, "domain": d, "rating": dom.get("rating"),
                              "step": dom.get("step"), "text": dom.get("rationale")})
        if doc.get("certainty"):
            return doc, doc["certainty"], "grade", notes
    else:
        doc = None
    j = _grade._latest_journal(app, oid)
    if j and j.get("certainty"):
        return doc, j["certainty"], "journal", notes
    return doc, None, None, notes


def _compute(app, oid, run, risks, user_notes):
    grade_doc, certainty, source, gnotes = _grade_inputs(app, oid)
    f = E.need("sof")
    values = {"run": run_dir_abs(app, run), "assumed_risks": risks, "project_dir": str(app.project_root),
              "outcome": oid, "run_id": run.get("run_id")}
    if grade_doc is not None and accepts(f, "grade") and source == "grade":
        # a motor a GRADE-dokumentumból maga veszi a bizonyosságot és a domén-indoklásos lábjegyzeteket
        values.update(grade=grade_doc, footnotes=user_notes)
    else:
        values.update(certainty=certainty, footnotes=gnotes + user_notes)
    doc = f(**E.bind("sof", f, values))
    if not isinstance(doc, dict):
        raise ApiError("INTERNAL", "A motor SoF-függvénye nem objektumot adott.")
    doc = dict(doc)
    doc.setdefault("schema", SOF_SCHEMA)
    doc.setdefault("outcome_id", oid)
    doc.setdefault("run_id", run.get("run_id"))
    doc["inputs"] = {"assumed_risks": risks, "footnotes": user_notes}
    return doc, certainty, source


def _load(app, oid):
    doc, etag = app.store.load_json(SOF_REL % oid)
    if doc is not None and not isinstance(doc, dict):
        raise store.Invalid("A SoF-fájl gyökere objektum legyen.", {"path": SOF_REL % oid})
    return doc, etag


def _view(app, outcome, run, saved, preview, certainty, source):
    inputs = (saved or {}).get("inputs") if isinstance((saved or {}).get("inputs"), dict) else None
    return {"schema": VIEW_SCHEMA,
            "outcome": {"id": outcome["id"], "name": outcome.get("name"), "measure": outcome.get("measure"),
                        "critical": bool(outcome.get("critical"))},
            "run": run_summary(run), "path": SOF_REL % outcome["id"], "saved": saved,
            "saved_matches_run": None if saved is None else saved.get("run_id") == run.get("run_id"),
            "preview": preview,
            "inputs": inputs or {"assumed_risks": DEFAULT_RISKS, "footnotes": []},
            "certainty": certainty, "certainty_source": source, "engine": E.available()}


def get_sof(req):
    app = req.app
    app.require_open()
    outcome, _meta = outcome_of(app, req.params["outcome"])
    oid = outcome["id"]
    E.need("sof")
    run = require_run(app, oid, req.arg("run", max_len=40) or None)
    saved, etag = _load(app, oid)
    inputs = saved.get("inputs") if isinstance(saved, dict) and isinstance(saved.get("inputs"), dict) else {}
    preview, certainty, source = _compute(app, oid, run, _risks(inputs.get("assumed_risks")),
                                          _user_notes(inputs.get("footnotes")))
    return Result(_view(app, outcome, run, saved, preview, certainty, source), VIEW_SCHEMA, etag=etag)


def put_sof(req):
    app = req.app
    app.require_open()
    outcome, _meta = outcome_of(app, req.params["outcome"])
    oid = outcome["id"]
    body = req.json_object()
    E.need("sof")
    run = require_run(app, oid, body.get("run_id"))
    risks = _risks(body.get("assumed_risks"))
    notes = _user_notes(body.get("footnotes"))
    rel = SOF_REL % oid
    phi_doc_guard(app, {"assumed_risks": risks, "footnotes": notes}, rel, "SoF-tábla")
    doc, certainty, source = _compute(app, oid, run, risks, notes)
    saved, etag = _load(app, oid)
    warnings = []
    if body.get("dry_run") is True:
        return Result(_view(app, outcome, run, saved, doc, certainty, source), VIEW_SCHEMA, etag=etag)
    if certainty is None:
        warnings.append("A SoF bizonyosság-oszlopa üres: a kimenet GRADE-ítélete még nincs kész (GRADE fül).")
    want = req.header("If-Match") or None
    sha = app.store.write_bytes(rel, store.json_bytes(doc), if_match=want)
    log_activity_or_warn(app, "sof.save", warnings, outputs=[{"path": rel, "sha256": sha}],
                         details={"outcome": oid, "run_id": run.get("run_id"), "assumed_risks": len(risks),
                                  "sources": sorted({r["source"] for r in risks}), "certainty": certainty})
    saved, etag = _load(app, oid)
    return Result(_view(app, outcome, run, saved, doc, certainty, source), VIEW_SCHEMA, warnings=warnings, etag=etag)


def _engine_export(doc, fmt, lang):
    """A motor saját exportja (sof_csv / sof_markdown), ha elérhető; a CSV-t az Excel-őr után BOM-mal írjuk.
    → szöveg vagy None (nincs motor-export: a munkapad rendereli ugyanazokból a motor-szövegekből)."""
    key = "sof_csv" if fmt == "csv" else "sof_markdown"
    if not E.has(key):
        return None
    if fmt == "csv":
        text = E.call(key, doc=doc, lang=lang, delimiter=";" if lang == "hu" else ",")
        if not isinstance(text, str):
            return None
        # második őr: a motor kimenete is csak Excel-biztos cellákkal mehet ki (7.6)
        rows = list(csv.reader(io.StringIO(text), delimiter=";" if lang == "hu" else ","))
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";" if lang == "hu" else ",", lineterminator="\r\n")
        for r in rows:
            w.writerow([c if c.startswith("'") else excel_safe(c) for c in r])
        return "\ufeff" + buf.getvalue()
    text = E.call(key, doc=doc, lang=lang)
    return text if isinstance(text, str) else None


def post_export(req):
    app = req.app
    app.require_open()
    outcome, _meta = outcome_of(app, req.params["outcome"])
    oid = outcome["id"]
    body = req.json_object()
    fmt = body["format"]
    lang = body.get("lang") or "en"
    saved, _etag = _load(app, oid)
    if not isinstance(saved, dict) or not isinstance(saved.get("rows"), list):
        raise ApiError("NOT_FOUND", "Még nincs mentett SoF-tábla ehhez a kimenethez: előbb nézd meg az előnézetet, és "
                                    "mentsd el (GRADE/SoF fül → SoF → Mentés).")
    text = _engine_export(saved, fmt, lang)
    if text is None:
        text = render_csv(saved, lang) if fmt == "csv" else render_md(saved, lang)
    data = text.encode("utf-8")
    rel = EXPORT_REL % (oid, lang, fmt)
    ok, reason = app.can_write_meta(rel)
    if not ok:
        raise ApiError("FORBIDDEN", reason, {"path": rel})
    sha = app.store.write_bytes(rel, data)
    url = app.security.sign_file_url("_run:" + rel, rel)
    parsed = app.security.parse_file_url(url)
    warnings = []
    log_activity_or_warn(app, "sof.export", warnings, outputs=[{"path": rel, "sha256": sha}],
                         details={"outcome": oid, "format": fmt, "lang": lang, "run_id": saved.get("run_id")})
    out = {"schema": EXPORT_SCHEMA, "format": fmt, "lang": lang, "path": rel, "sha256": sha, "bytes": len(data),
           "url": url, "expires_at": parsed[1] if parsed else None, "run_id": saved.get("run_id")}
    if fmt == "md":
        out["content"] = text
    return Result(out, EXPORT_SCHEMA, warnings=warnings)


def register(router):
    router.add("GET", "/api/sof/<outcome>", get_sof, schema=VIEW_SCHEMA)
    router.add("PUT", "/api/sof/<outcome>", put_sof, schema=VIEW_SCHEMA, request_schema=PUT_REQUEST)
    router.add("POST", "/api/sof/<outcome>/export", post_export, schema=EXPORT_SCHEMA, request_schema=EXPORT_REQUEST)
