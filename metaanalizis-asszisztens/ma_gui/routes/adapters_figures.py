# -*- coding: utf-8 -*-
"""Ábra-export és ábra-audit (terv 3.4, 3.5.9, 4.12, 5.2–5.3, 6.2, 6.7; 2.6: csak commit-futásra).

- ``GET /api/figures[?run=<run_id>]`` → ``szk.ma.figure-options/v1``: a commit-futások (választó), a kért futás
  ábrafajtái (forest / funnel / Doi a futás SVG-jéből; a motor rajzoló-függvényével a többi is), a megjelenítők
  (figure-forge állapota és teendője, motor-SVG, böngészős előnézet), a formátumok elérhetősége okkal (PNG/PDF:
  helyi SVG-átalakító vagy figure-forge; TIFF/PPTX: csak figure-forge F2), és a már exportált ábrák
  (``06_kezirat/abrak/<stem>.result.json``) — ELAVULT-jelöléssel, ha a futás plot_data.json-ja azóta más (X002).
- ``POST /api/figures/export`` ← ``{run_id, kind, formats[], renderer?: auto|figure-forge|engine, lang?: en|hu,
  dpi?, width?, stem?, overwrite?, style?{palette, typography}, select?{columns, show_pi, order}}`` →
  ``szk.ma.figure-export/v1``. Megjelenítő: a figure-forge ``meta`` (F2, json), ha deklarált; különben a MOTOR SVG-je
  (a motor ``render_figure``-ével angol felirattal és rétegekkel, ha a homlokzat már tudja; addig a futás saját
  SVG-je, figyelmeztetéssel). A motor-SVG-ből PNG/PDF csak működő helyi átalakítóval; TIFF/PPTX csak figure-forge-
  dzsal — ami nem készülhet, az a ``skipped``-ben, magyar okkal. Minőség: figure-forge audit (ha használható; H5-nél
  a teendővel), stdlib-audit, és a SZERVER számhűség-újraellenőrzése (a motor display_text-jei szó szerint az SVG-
  ben). Zöld jelvény csak hiánytalan számhűségnél. Kimenet: ``06_kezirat/abrak/<stem>.{svg,…}`` +
  ``<stem>.result.json``; meglévő fájlt csak ``overwrite: true`` ír felül (különben 409). Activity-sor szám nélkül.
- ``POST /api/figures/audit`` ← ``{path}`` (``06_kezirat/abrak/`` vagy ``05_elemzes/`` alatti SVG/PDF) vagy
  ``{run_id, kind}`` → audit (figure-forge + stdlib) és számhűség (ha a futás ismert).

Statisztikát nem számol, számot nem formáz; minden szám a motor kész szövege."""
import json
import os
import re

from .. import security, store
from ..adapters import figureforge as ff_ad
from ..adapters import svgaudit
from ..router import ApiError, Result
from . import runs
from ._common import accepts, body_bool, body_int, body_str, now_iso
from .adapters import engine_fn, raise_for

OPTIONS_SCHEMA = "szk.ma.figure-options/v1"
EXPORT_SCHEMA = "szk.ma.figure-export/v1"
AUDIT_SCHEMA = "szk.ma.figure-audit-view/v1"
RESULT_SCHEMA = "szk.figure-result/v1"
REQUEST_SCHEMA_NAME = "szk.figure-request/v1"
FIG_DIR = "06_kezirat/abrak"
RUN_KINDS = ("forest", "funnel", "doi")
RENDER_KINDS = ("forest", "funnel", "doi", "cumulative", "loo", "bubble")
RENDERERS = ("auto", "figure-forge", "engine")
DPIS = (300, 600, 1200)
WIDTHS = ("single", "1.5", "double")
LANGS = ("en", "hu")
PALETTES = ("okabe-ito",)
ORDERS = ("input", "effect", "weight")
COLUMNS = ("events", "weight", "pi")
RENDER_FNS = ("render_figure", "figure_svg", "plot_svg")
STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_EXISTING = 200
MAX_AUDIT_BYTES = svgaudit.MAX_SVG_BYTES
AUDIT_ROOTS = (FIG_DIR + "/", "05_elemzes/")
EXPORT_REQUEST = {
    "type": "object", "required": ["run_id", "kind"],
    "properties": {
        "run_id": {"type": "string", "maxLength": 40},
        "kind": {"enum": list(RENDER_KINDS)},
        "formats": {"type": "array", "items": {"enum": list(ff_ad.FORMATS)}},
        "renderer": {"enum": list(RENDERERS)},
        "lang": {"enum": list(LANGS)},
        "dpi": {"enum": list(DPIS)},
        "width": {"enum": list(WIDTHS)},
        "stem": {"type": ["string", "null"], "maxLength": 64},
        "overwrite": {"type": "boolean"},
        "style": {"type": "object", "properties": {"palette": {"enum": list(PALETTES)},
                                                   "typography": {"type": "boolean"}},
                  "additionalProperties": False},
        "select": {"type": "object", "properties": {
            "columns": {"type": "array", "items": {"enum": list(COLUMNS)}},
            "show_pi": {"type": "boolean"}, "order": {"enum": list(ORDERS)}}, "additionalProperties": False},
        "client_seq": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}
AUDIT_REQUEST = {
    "type": "object",
    "properties": {"path": {"type": "string", "maxLength": 1024}, "run_id": {"type": "string", "maxLength": 40},
                   "kind": {"enum": list(RENDER_KINDS)}},
    "additionalProperties": False,
}


def _i18n(hu, en):
    return {"hu": hu, "en": en}


# ---------------------------------------------------------------------------- futás-segédek
def _run_file_rel(run, rel_dir, key, default_name=None):
    f = (run.get("files") or {}).get(key)
    rel = f.get("path") if isinstance(f, dict) else None
    if isinstance(rel, str) and rel.startswith(rel_dir + "/"):
        return rel
    return rel_dir + "/" + default_name if default_name else None


def _read_rel(app, rel, limit=MAX_AUDIT_BYTES):
    try:
        path = app.store.path(rel)
        if not path.is_file() or path.stat().st_size > limit:
            return None
        return path.read_bytes()
    except (OSError, store.StoreError):
        return None


def _plot(app, run, rel_dir):
    """(plot_data.json dokumentum, sha256, rel) — a futás szk.ma.plot/v2-je; hiányában 404."""
    rel = _run_file_rel(run, rel_dir, "plot", "plot_data.json")
    raw = _read_rel(app, rel, 64 * 1024 * 1024)
    if raw is None:
        raise ApiError("NOT_FOUND", "A futás ábra-adata (%s) hiányzik; az ábra nem exportálható." % rel)
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        raise ApiError("VALIDATION", "A futás ábra-adata (%s) nem érvényes JSON." % rel) from None
    if not isinstance(doc, dict):
        raise ApiError("VALIDATION", "A futás ábra-adata (%s) nem objektum." % rel)
    return doc, store.sha256_bytes(raw), rel


def _render_fn():
    return engine_fn(RENDER_FNS)


def _kinds(run, rel_dir, plot):
    """A futás exportálható ábrafajtái: a futás SVG-je (forest/funnel/doi) vagy a motor rajzoló-függvénye."""
    fn = _render_fn()
    out = []
    for kind in RENDER_KINDS:
        rel = _run_file_rel(run, rel_dir, kind) if kind in RUN_KINDS else None
        has_file = bool(rel and rel.endswith(".svg"))
        has_data = kind in RUN_KINDS or bool((plot or {}).get(kind))
        if fn is not None and has_data:
            out.append({"kind": kind, "available": True, "source": "engine_render"})
        elif has_file:
            out.append({"kind": kind, "available": True, "source": "run_file"})
        elif kind in RUN_KINDS or has_data:
            out.append({"kind": kind, "available": False, "source": None, "reason": _i18n(
                "Ehhez az ábrához a motor rajzoló-függvénye (metaelemzes.api.render_figure) kell — frissítsd a "
                "motort (git pull), majd indítsd újra a munkapadot.",
                "This figure needs the engine drawing function (metaelemzes.api.render_figure) — update the engine "
                "(git pull), then restart the workbench.")})
    return out


def _engine_svg(app, rel_dir, run, plot, kind, lang, warnings):
    """(svg-bájtok, felirat-nyelv, forrás) — a motor rajzoló-függvénye (EN, rétegek, U+2212), különben a futás
    saját SVG-je. Hiányzó függvény és fájl → 424 magyar teendővel."""
    fn = _render_fn()
    if fn is not None:
        kw = {"plot": plot, "kind": kind, "lang": lang, "annotate": True,
              "run_dir": str(app.store.path(rel_dir)), "project_root": str(app.project_root),
              "run_id": run.get("run_id")}
        use = {k: v for k, v in kw.items() if accepts(fn, k)}
        engine_reason = None
        try:
            out = fn(**use)
        except (ValueError, KeyError, TypeError) as exc:
            if kind not in RUN_KINDS:
                raise ApiError("VALIDATION", "A motor nem tudta megrajzolni az ábrát (%s): %s" % (kind, exc)) from None
            # forest / funnel / Doi: a motor csak a futás változatlan adataiból rajzol újra (fidelitás-őr) —
            # ha nem tud, a futás saját SVG-je készül, a motor okával
            out, engine_reason = None, str(exc)
        svg = out if isinstance(out, str) else (out.get("svg") if isinstance(out, dict) else None)
        if isinstance(svg, str) and svg.strip():
            used = out.get("lang") if isinstance(out, dict) and out.get("lang") in LANGS else lang
            return svg.encode("utf-8"), used, "engine_render"
        if engine_reason:
            warnings.append("A motor nem rajzolta újra az ábrát (%s), ezért a futás saját SVG-je készült." % engine_reason)
    if kind not in RUN_KINDS:
        raise ApiError("CAPABILITY_MISSING", "A(z) %s ábra exportjához a motor rajzoló-függvénye kell "
                                             "(metaelemzes.api.render_figure), de ez a motorváltozat még nem "
                                             "tartalmazza. Frissítsd a motort (git pull), majd indítsd újra a "
                                             "munkapadot." % kind,
                       {"engine_functions": ["render_figure"], "engine": "metaelemzes.api"})
    rel = _run_file_rel(run, rel_dir, kind)
    raw = _read_rel(app, rel) if rel and rel.endswith(".svg") else None
    if raw is None:
        raise ApiError("CAPABILITY_MISSING", "A futásnak nincs %s-SVG-je, és a motor rajzoló-függvénye "
                                             "(metaelemzes.api.render_figure) még nincs meg. Futtasd újra az elemzést "
                                             "(Rögzítés), vagy frissítsd a motort." % kind,
                       {"engine_functions": ["render_figure"], "engine": "metaelemzes.api"})
    run_lang = plot.get("display_locale") if plot.get("display_locale") in LANGS else "hu"
    if run_lang != lang and fn is None:
        warnings.append("A futás saját SVG-je %s feliratú; a kért (%s) nyelvű, rétegzett motor-SVG-hez a motor "
                        "render_figure függvénye kell (frissítsd a motort). Most a futás SVG-je készült."
                        % ("magyar" if run_lang == "hu" else "angol", lang))
    elif run_lang != lang:
        warnings.append("A futás saját SVG-je %s feliratú, nem a kért (%s) nyelvű: az újrarajzoláshoz futtasd újra "
                        "az elemzést (Rögzítés)." % ("magyar" if run_lang == "hu" else "angol", lang))
    return raw, run_lang, "run_file"


# ---------------------------------------------------------------------------- opciók
def _formats(ff, ff_status, convs):
    conv_ok = any(c["ok"] for c in convs)
    ff_export = ff_status["export"]["available"]
    out = [{"id": "svg", "available": True, "via": ["engine"] + (["figure-forge"] if ff_export else [])}]
    for fmt in ("pdf", "png"):
        via = (["converter"] if conv_ok else []) + (["figure-forge"] if ff_export else [])
        out.append({"id": fmt, "available": bool(via), "via": via,
                    "reason": None if via else ff_ad.NO_CONVERTER})
    for fmt in ff_ad.FF_ONLY_FORMATS:
        out.append({"id": fmt, "available": ff_export, "via": ["figure-forge"] if ff_export else [],
                    "reason": None if ff_export else ff_ad.FF_ONLY})
    return out


def _existing(app, run_plot_sha):
    """A 06_kezirat/abrak/*.result.json összesítői (legfeljebb 200), X002-jelöléssel."""
    try:
        base = app.store.path(FIG_DIR)
        names = sorted(n for n in os.listdir(str(base)) if n.endswith(".result.json"))[:MAX_EXISTING]
    except (OSError, store.StoreError):
        return []
    out = []
    for name in names:
        raw = _read_rel(app, FIG_DIR + "/" + name, 4 * 1024 * 1024)
        try:
            doc = json.loads(raw.decode("utf-8-sig")) if raw else None
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            continue
        src = doc.get("source") if isinstance(doc.get("source"), dict) else {}
        rid = src.get("run_id")
        cur = run_plot_sha(rid) if isinstance(rid, str) else None
        out.append({"stem": doc.get("stem"), "kind": doc.get("kind"), "run_id": rid, "path": FIG_DIR + "/" + name,
                    "formats": sorted((doc.get("formats") or {}).keys()), "renderer": doc.get("renderer"),
                    "generated": doc.get("generated"), "badge": (doc.get("qc") or {}).get("badge"),
                    "stale": (cur is not None and cur != src.get("plot_sha256")) if cur is not None else None})
    return out


def _plot_sha_lookup(app):
    index = runs.run_index(app)
    cache = {}

    def lookup(run_id):
        if run_id in cache:
            return cache[run_id]
        hit = index.get(run_id)
        sha = None
        if hit is not None:
            rel_dir, run = hit
            raw = _read_rel(app, _run_file_rel(run, rel_dir, "plot", "plot_data.json"), 64 * 1024 * 1024)
            sha = store.sha256_bytes(raw) if raw is not None else None
        cache[run_id] = sha
        return sha
    return lookup


def get_figures(req):
    app = req.app
    app.require_open()
    ff = ff_ad.FigureForgeAdapter(app.caps)
    fst = ff.status()
    convs = ff_ad.find_converters(app.caps.env, app.caps.tmp_dir())
    rid = req.arg("run", max_len=40)
    run_view = None
    if rid:
        rel_dir, run = runs.find_run(app, rid)
        plot, _sha, _rel = _plot(app, run, rel_dir)
        dec = runs.decorate(app, rel_dir, run)
        run_view = {"run_id": run.get("run_id"), "outcome_id": dec.get("outcome_id"), "stale": dec.get("stale"),
                    "measure": dec.get("measure"), "purpose": (dec.get("spec") or {}).get("purpose"),
                    "display_locale": plot.get("display_locale"), "kinds": _kinds(run, rel_dir, plot)}
    run_list = [{"run_id": r.get("run_id"), "outcome_id": r.get("outcome_id"), "stale": r.get("stale"),
                 "purpose": (r.get("spec") or {}).get("purpose"), "started": r.get("started"),
                 "k": r.get("k")} for r in runs.list_runs(app)][:200]
    fn = _render_fn()
    renderers = [
        {"id": "figure-forge", "available": fst["export"]["available"], "state": fst["state"],
         "version": fst["version"], "mode": fst["export"]["mode"], "remedy": fst["export"]["remedy"]},
        {"id": "engine", "available": True, "render_fn": getattr(fn, "__name__", None) if fn else None,
         "remedy": None if fn else _i18n(
             "A motor rajzoló-függvénye (metaelemzes.api.render_figure) még nincs meg: a futás saját SVG-je készül "
             "(a futás nyelvén, rétegek nélkül). Frissítsd a motort az angol feliratú, rétegzett SVG-hez.",
             "The engine drawing function (metaelemzes.api.render_figure) is missing: the run's own SVG is used (in "
             "the run's language, without layers). Update the engine for the English, layered SVG.")},
        {"id": "browser_png", "available": True, "preview_only": True},
    ]
    data = {"schema": OPTIONS_SCHEMA, "run": run_view, "runs": run_list, "renderers": renderers,
            "formats": _formats(ff, fst, convs),
            "converters": [{"name": c["name"], "ok": c["ok"], "formats": c["formats"]} for c in convs],
            "dpis": list(DPIS), "widths": list(WIDTHS), "langs": list(LANGS), "palettes": list(PALETTES),
            "audit": {"figure_forge": fst["audit"], "stdlib": True},
            "figure_forge": {"state": fst["state"], "version": fst["version"], "guards": fst["guards"]},
            "existing": _existing(app, _plot_sha_lookup(app))}
    return Result(data, OPTIONS_SCHEMA)


# ---------------------------------------------------------------------------- minőség
def _ff_audit(ff, data, name, st=None):
    """A figure-forge audit eredménye, vagy ``{error: {code, message, guard}}``, ha a plugin telepítve van, de nem
    használható (H5: a pontos teendővel) vagy az audit elbukott; hiányzó pluginnál None (a stdlib-audit fut)."""
    st = ff.status() if st is None else st
    if not st["audit"]["available"]:
        if st["state"] != "unusable":
            return None
        remedy = st["audit"].get("remedy") or {}
        return {"error": {"code": "CAPABILITY_MISSING", "message": remedy.get("hu") or "A figure-forge nem használható.",
                          "message_i18n": remedy or None, "guard": st["audit"].get("guard")}}
    res = ff.audit(data, name)
    if res.get("ok"):
        return res["data"]
    det = res["error"].get("details") or {}
    return {"error": {"code": res["error"]["code"], "message": res["error"]["message"],
                      "message_i18n": det.get("todo") if isinstance(det.get("todo"), dict) else None,
                      "guard": det.get("guard")}}


def _qc(app, ff, svg, plot, kind, stem, ff_doc=None):
    """{badge, stdlib, figure_forge, numbers (szerver), ff_numbers, clean, labels_checked} — a QC-panel adata."""
    stdlib = None
    numbers = None
    if svg is not None:
        try:
            stdlib = svgaudit.audit_svg(svg)
            numbers = svgaudit.numbers_check_svg(svg, plot, kind) if plot is not None else None
        except svgaudit.SvgError as exc:
            stdlib = {"source": "stdlib", "error": str(exc)}
    ffa = None
    if svg is not None:
        ffa = _ff_audit(ff, svg, (stem or "figure") + ".svg")
    ff_numbers = ff_doc.get("numbers") if isinstance(ff_doc, dict) and isinstance(ff_doc.get("numbers"), dict) else None
    clean = ff_doc.get("clean") if isinstance(ff_doc, dict) and isinstance(ff_doc.get("clean"), bool) else None
    editable = None
    for src in (ffa, stdlib):
        if isinstance(src, dict) and isinstance(src.get("editable"), bool):
            editable = src["editable"]
            break
    ok = (numbers is not None and numbers.get("ok") is True and (ff_numbers is None or ff_numbers.get("ok") is True)
          and editable is not False and clean is not False)
    return {"badge": "ok" if ok else "issues", "stdlib": stdlib, "figure_forge": ffa, "numbers": numbers,
            "ff_numbers": ff_numbers, "clean": clean, "editable": editable,
            "labels_checked": ff_doc.get("labels_checked") if isinstance(ff_doc, dict) else None,
            "residual_violations": (ff_doc.get("residual_violations") or [])[:50] if isinstance(ff_doc, dict) else [],
            "typography": (ff_doc.get("typography") if isinstance(ff_doc, dict) else None),
            "glyphs": (ff_doc.get("glyphs") if isinstance(ff_doc, dict) else None)}


# ---------------------------------------------------------------------------- export
def _figure_request(run, plot_rel, plot_sha, kind, body, stem, formats):
    sel = body.get("select") or {}
    style = body.get("style") or {}
    return {"schema": REQUEST_SCHEMA_NAME, "kind": kind,
            "input": {"plot": plot_rel, "plot_sha256": plot_sha, "run_id": run.get("run_id")},
            "select": {"sections": ["random", "alternate"], "columns": list(sel.get("columns") or ["events", "weight"]),
                       "show_pi": bool(sel.get("show_pi", True)), "order": sel.get("order") or "input"},
            "style": {"profile": "nature", "width": body.get("width") or "double", "dpi": body.get("dpi") or 600,
                      "palette": style.get("palette") or "okabe-ito", "locale": body.get("lang") or "en",
                      "typography": bool(style.get("typography", True))},
            "out": {"dir": FIG_DIR, "stem": stem, "formats": list(formats)},
            "qc": {"max_iter": 8, "require_clean": True}}


def _default_stem(kind, outcome):
    base = "fig_%s_%s" % (kind, outcome or "x")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", base)[:64]


def post_export(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    rel_dir, run = runs.find_run(app, body_str(body, "run_id", required=True, max_len=40))
    kind = body.get("kind")
    formats = []
    for f in body.get("formats") or ["svg"]:
        if f not in formats:
            formats.append(f)
    renderer = body.get("renderer") or "auto"
    lang = body.get("lang") or "en"
    dpi = body_int(body, "dpi") or 600
    overwrite = body_bool(body, "overwrite")
    dec = runs.decorate(app, rel_dir, run)
    stem = body_str(body, "stem", max_len=64) or _default_stem(kind, dec.get("outcome_id"))
    if not STEM_RE.match(stem):
        raise ApiError("BAD_REQUEST", "Az ábra fájlneve (stem) betűvel vagy számmal kezdődjön, és csak betűt, számot, "
                                      "kötőjelet, aláhúzást tartalmazzon (legfeljebb 64 karakter).")
    plot, plot_sha, plot_rel = _plot(app, run, rel_dir)
    warnings = []
    if dec.get("stale"):
        warnings.append("ELAVULT futás: az adattábla a futás óta megváltozott (X001). Az ábra a futás számait "
                        "mutatja; futtasd újra az elemzést, mielőtt beküldöd.")
    ff = ff_ad.FigureForgeAdapter(app.caps)
    use_ff = renderer in ("auto", "figure-forge") and ff.export_mode() == "json"
    if renderer == "figure-forge" and not use_ff:
        st = ff.status()
        remedy = st["export"]["remedy"] or {}
        raise ApiError("CAPABILITY_MISSING", remedy.get("hu") or "A figure-forge export nem érhető el.",
                       {"plugin": "figure-forge", "state": st["state"], "todo": remedy})
    targets = {f: "%s/%s%s" % (FIG_DIR, stem, ff_ad.EXT[f]) for f in ff_ad.FORMATS}
    result_rel = "%s/%s.result.json" % (FIG_DIR, stem)
    for rel in [targets[f] for f in formats] + [targets["svg"], result_rel]:
        ok, reason = app.can_write_meta(rel)
        if not ok:
            raise ApiError("FORBIDDEN", reason, {"path": rel})
    if not overwrite:
        exists = [rel for rel in sorted(set([targets[f] for f in formats] + [targets["svg"], result_rel]))
                  if _exists(app, rel)]
        if exists:
            raise ApiError("CONFLICT", "Ilyen nevű ábra már van (%s). Adj meg másik nevet, vagy kérd a felülírást."
                           % ", ".join(exists[:4]), {"exists": exists, "needs_overwrite": True})
    files, skipped, conv_used = {}, [], {}
    ff_doc, renderer_used, lang_used, svg_source = None, None, lang, None
    if use_ff:
        res = ff.export(_figure_request(run, plot_rel, plot_sha, kind, body, stem, formats),
                        str(app.store.path(plot_rel)), app.project_root)
        raise_for(res)
        ff_doc = res["data"]
        renderer_used = "figure-forge"
        svg_source = "figure-forge"
        for f, blob in res["files"].items():
            if f in formats or f == "svg":
                files[f] = blob
        for f in formats:
            if f not in files:
                skipped.append({"format": f, "reason": _i18n("A figure-forge ezt a formátumot nem írta ki.",
                                                             "figure-forge did not write this format.")})
    else:
        svg, lang_used, svg_source = _engine_svg(app, rel_dir, run, plot, kind, lang, warnings)
        files["svg"] = svg
        renderer_used = "engine"
        for f in formats:
            if f == "svg":
                continue
            if f in ff_ad.FF_ONLY_FORMATS:
                skipped.append({"format": f, "reason": ff_ad.FF_ONLY})
                continue
            data, conv = ff_ad.convert_svg(svg, f, dpi, app.caps.env, app.caps.tmp_dir())
            if data is None:
                skipped.append({"format": f, "reason": ff_ad.NO_CONVERTER})
            else:
                files[f] = data
                conv_used[f] = conv
        if "svg" not in formats:
            warnings.append("Az SVG-mesterpéldány mindig elkészül (szerkeszthető, ebből készül minden más formátum).")
    qc = _qc(app, ff, files.get("svg"), plot, kind, stem, ff_doc)
    written = {}
    for f in ff_ad.FORMATS:
        if f in files:
            sha = app.store.write_bytes(targets[f], files[f])
            written[f] = {"path": targets[f], "sha256": sha, "bytes": len(files[f]), "via": conv_used.get(f) or
                          renderer_used}
    result = {"schema": RESULT_SCHEMA, "kind": kind, "stem": stem, "generated": now_iso(),
              "renderer": renderer_used, "svg_source": svg_source, "lang": lang_used,
              "ff_version": ff_doc.get("ff_version") if isinstance(ff_doc, dict) else None,
              "formats": {f: w["path"] for f, w in written.items()}, "dpi": dpi, "width": body.get("width") or "double",
              "clean": qc["clean"], "labels_checked": qc["labels_checked"],
              "residual_violations": qc["residual_violations"], "typography": qc["typography"], "glyphs": qc["glyphs"],
              "editability": {"svg": qc["figure_forge"] if isinstance(qc["figure_forge"], dict) and
                              "error" not in qc["figure_forge"] else qc["stdlib"]},
              "numbers": qc["ff_numbers"], "server_check": qc["numbers"],
              "qc": {"badge": qc["badge"]},
              "source": {"plot": plot_rel, "plot_sha256": plot_sha, "run_id": run.get("run_id")}}
    rsha = app.store.write_bytes(result_rel, store.json_bytes(result))
    outputs = [(w["path"], w["sha256"]) for w in written.values()] + [(result_rel, rsha)]
    if app.log_activity("figures.export", inputs=[(plot_rel, plot_sha)], outputs=outputs,
                        details={"kind": kind, "run_id": run.get("run_id"), "renderer": renderer_used,
                                 "formats": sorted(written), "badge": qc["badge"],
                                 "numbers_ok": (qc["numbers"] or {}).get("ok")}) is None:
        warnings.append(app.ACTIVITY_WARNING)
    downloads = {}
    for f, w in written.items():
        if security.check_extension(w["path"], security.RUN_ARTIFACT_EXTENSIONS):
            url = app.security.sign_file_url(runs.ARTIFACT_PREFIX + w["path"], w["path"])
            parsed = app.security.parse_file_url(url)
            downloads[f] = {"url": url, "expires_at": parsed[1] if parsed else None}
    data = {"schema": EXPORT_SCHEMA, "run_id": run.get("run_id"), "outcome_id": dec.get("outcome_id"),
            "run_stale": bool(dec.get("stale")), "kind": kind, "stem": stem, "dir": FIG_DIR,
            "renderer": {"requested": renderer, "used": renderer_used, "svg_source": svg_source,
                         "ff_version": result["ff_version"]},
            "lang": {"requested": lang, "used": lang_used}, "formats": written, "downloads": downloads,
            "skipped": skipped, "result_path": result_rel, "qc": qc}
    return Result(data, EXPORT_SCHEMA, warnings=warnings)


def _exists(app, rel):
    try:
        return app.store.path(rel).exists()
    except store.StoreError:
        return False


# ---------------------------------------------------------------------------- audit
def _audit_rel(value):
    if not isinstance(value, str) or not value.startswith(AUDIT_ROOTS):
        raise ApiError("FORBIDDEN", "Csak a 06_kezirat/abrak/ vagy a 05_elemzes/ alatti SVG/PDF auditálható.")
    try:
        security.check_relpath(value)
    except security.UnsafePath as exc:
        raise ApiError("FORBIDDEN", exc.message) from None
    if not value.lower().endswith((".svg", ".pdf")):
        raise ApiError("FORBIDDEN", "Csak SVG vagy PDF auditálható (a raszterképben nincs szöveg).")
    return value


def post_audit(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    path = body.get("path")
    plot, kind, run_id = None, body.get("kind"), body.get("run_id")
    if path:
        rel = _audit_rel(path)
        stem_rel = re.sub(r"\.(svg|pdf)$", "", rel, flags=re.I)
        meta_raw = _read_rel(app, stem_rel + ".result.json", 4 * 1024 * 1024) if rel.startswith(FIG_DIR) else None
        try:
            meta = json.loads(meta_raw.decode("utf-8-sig")) if meta_raw else None
        except ValueError:
            meta = None
        if isinstance(meta, dict):
            kind = kind or meta.get("kind")
            run_id = run_id or (meta.get("source") or {}).get("run_id")
    elif run_id and kind in RUN_KINDS:
        rel_dir, run = runs.find_run(app, run_id)
        rel = _run_file_rel(run, rel_dir, kind)
        if not rel or not rel.endswith(".svg"):
            raise ApiError("NOT_FOUND", "A futásnak nincs ilyen SVG-je (%s)." % kind)
    else:
        raise ApiError("BAD_REQUEST", "Adj meg egy fájlt (path) vagy egy futást és ábrafajtát (run_id, kind).")
    raw = _read_rel(app, rel)
    if raw is None:
        raise ApiError("NOT_FOUND", "A fájl nem található vagy túl nagy (%s)." % rel)
    if isinstance(run_id, str) and kind in RENDER_KINDS:
        try:
            rel_dir, run = runs.find_run(app, run_id)
            plot, _sha, _prel = _plot(app, run, rel_dir)
        except ApiError:
            plot = None
    ff = ff_ad.FigureForgeAdapter(app.caps)
    is_svg = rel.lower().endswith(".svg")
    stdlib, numbers, ffa = None, None, None
    if is_svg:
        try:
            stdlib = svgaudit.audit_svg(raw)
            numbers = svgaudit.numbers_check_svg(raw, plot, kind) if plot is not None and kind else None
        except svgaudit.SvgError as exc:
            stdlib = {"source": "stdlib", "error": str(exc)}
    st = ff.status()
    ffa = _ff_audit(ff, raw, os.path.basename(rel), st)
    data = {"schema": AUDIT_SCHEMA, "path": rel, "kind": kind, "run_id": run_id if plot is not None else None,
            "figure_forge": ffa, "figure_forge_status": st["audit"], "stdlib": stdlib, "numbers": numbers}
    return Result(data, AUDIT_SCHEMA)


def register(router):
    router.add("GET", "/api/figures", get_figures, schema=OPTIONS_SCHEMA)
    router.add("POST", "/api/figures/export", post_export, schema=EXPORT_SCHEMA, request_schema=EXPORT_REQUEST)
    router.add("POST", "/api/figures/audit", post_audit, schema=AUDIT_SCHEMA, request_schema=AUDIT_REQUEST)

