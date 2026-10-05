# -*- coding: utf-8 -*-
"""Plugin-adapterek állapota és a validator keresztellenőrzése (terv 3.4, 3.5.16, 4.11, 5.0–5.5, 6.5).

- ``GET /api/adapters`` → ``szk.ma.adapters/v1``: pluginonként az adapter állapota (validator, figure-forge,
  composer), és FUNKCIÓNKÉNT az állapot (absent | unusable | legacy | ok), a mód (json | bridge), a tartalék
  (mi működik plugin nélkül) és a magyar teendő; a bekapcsolt H-őrök a szövegükkel; a helyi SVG-átalakítók
  (PNG/PDF a motor-SVG-ből). A képesség-képernyő „Funkciók a pluginokkal” táblája ebből készül.
- ``POST /api/validator/check`` ← ``{doc: szk.appraisal/v1}`` → ``szk.appraisal-result/v1`` a validator
  véleményével (``mode``, ``legacy``, ``guards``, ``validator_reported``). Az ítélet és a teljesség elsődleges
  forrása a motor (``/api/appraisals/…/check``); ez a validator keresztellenőrzése, a H1–H4 őrökkel. Plugin
  nélkül 424 CAPABILITY_MISSING a teendővel. Az értékelés szövegei (idézet, indoklás) nem mennek át a pluginnak.

Statisztikát nem számol; értékelés-szöveget, argv-t nem naplóz (T10)."""
from metaelemzes import api

from .. import caps as caps_mod
from .. import security
from ..adapters import composer as composer_ad
from ..adapters import figureforge as ff_ad
from ..adapters import validator as val_ad
from ..router import ApiError, Result
from . import _contracts
from ._common import now_iso

SCHEMA = "szk.ma.adapters/v1"
RESULT_SCHEMA = "szk.appraisal-result/v1"
CHECK_REQUEST = {
    "type": "object", "required": ["doc"],
    "properties": {"doc": {"type": "object"}, "client_seq": {"type": "integer", "minimum": 0}},
    "additionalProperties": False,
}
INSTRUMENT_FNS = ("instrument_get", "get_instrument", "load_instrument")
FEATURE_ORDER = ("rob", "probast", "tripod", "grade", "amstar2", "figure_audit", "pub_figure", "prisma_figure",
                 "prisma")
VALIDATOR_REP_TOOL = {"rob": "rob2", "probast": "probast-ai", "tripod": "tripod-ai", "grade": "grade",
                      "amstar2": "amstar2"}


def _i18n(hu, en):
    return {"hu": hu, "en": en}


def raise_for(res):
    """Adapter-boríték → az adat, vagy ApiError a 3.4 kódtábla szerint (ismeretlen kód: PLUGIN_FAILED)."""
    if res.get("ok"):
        return res
    err = res.get("error") or {}
    code = err.get("code") if err.get("code") in security.ERROR_CODES else "PLUGIN_FAILED"
    raise ApiError(code, err.get("message") or "A plugin hibát jelzett.", err.get("details"))


def engine_fn(names):
    """Az első hívható ``metaelemzes.api`` függvény a nevek közül (feature detection), vagy None."""
    for n in names:
        fn = getattr(api, n, None)
        if callable(fn) and not isinstance(fn, type):
            return fn
    return None


def _feature_meta():
    return {f["id"]: f for f in caps_mod.FEATURES}


def _guard_list(plugin, ids):
    texts = {i["id"]: i for i in caps_mod.BUILTIN_ISSUES.get(plugin, ())}
    out = []
    for gid in ids:
        g = texts.get(gid, {})
        out.append({"id": gid, "text": g.get("guard") or _i18n(gid, gid)})
    return out


def _feature(fid, plugin, state, mode, remedy, guards, fallback=None, extra=None):
    meta = _feature_meta().get(fid, {})
    row = {"id": fid, "plugin": plugin, "label": meta.get("label") or _i18n(fid, fid), "state": state,
           "mode": mode, "remedy": remedy, "guards": guards,
           "standalone": bool(meta.get("standalone")), "fallback": fallback if fallback is not None
           else meta.get("standalone_note")}
    if extra:
        row.update(extra)
    return row


def adapters_report(app):
    """A ``GET /api/adapters`` adata (a felület és a tesztek közös forrása)."""
    caps = app.caps
    val = val_ad.ValidatorAdapter(caps)
    ff = ff_ad.FigureForgeAdapter(caps)
    comp = composer_ad.ComposerAdapter(caps)
    meta, _etag = app.project_meta_safe()
    loc = composer_ad.location(meta or {}, app.project_root, caps.env)
    vst, fst, cst = val.status(), ff.status(), comp.status(loc)
    vcap = val.detect()
    features = []
    for fid in ("rob", "probast", "tripod", "grade", "amstar2"):
        feat = _feature_meta().get(fid)
        state = caps_mod.feature_state(feat, vcap)[0] if feat else vst["state"]
        tool = VALIDATOR_REP_TOOL[fid]
        ids = vst["tools"].get(tool, {}).get("guards") or []
        remedy = vst["remedy"] if state != "ok" else None
        features.append(_feature(fid, "validator", state, val.mode(tool, vcap), remedy, _guard_list("validator", ids)))
    # figure-forge: audit (H5), publikációs ábra (H6), PRISMA-folyamatábra
    a = fst["audit"]
    a_state = fst["state"] if a["available"] else ("absent" if fst["state"] == "absent" else "unusable")
    features.append(_feature("figure_audit", "figure-forge", a_state, a["mode"], a["remedy"],
                             _guard_list("figure-forge", ["H5"] if a.get("guard") == "H5" else []),
                             extra={"fallback_used": not a["available"]}))
    e = fst["export"]
    e_state = "ok" if e["available"] else (fst["state"] if fst["state"] in ("absent", "unusable") else "legacy")
    features.append(_feature("pub_figure", "figure-forge", e_state, e["mode"], e["remedy"],
                             _guard_list("figure-forge", ["H6"] if not e["available"] and "H6" in fst["guards"]
                                         else []),
                             extra={"fallback_used": not e["available"]}))
    fl = fst["flowchart"]
    features.append(_feature("prisma_figure", "figure-forge",
                             (fst["state"] if fl["available"] else ("absent" if fst["state"] == "absent" else
                                                                    "unusable")),
                             fl["mode"], fl["remedy"], []))
    c_state = cst["state"]
    features.append(_feature("prisma", "composer", c_state, cst["mode"], cst["remedy"],
                             _guard_list("composer", [g for g in cst["guards"] if g == "H7"]),
                             extra={"configured": loc["configured"], "can_refresh": cst["can_refresh"]}))
    convs = ff_ad.find_converters(caps.env, caps.tmp_dir())
    return {"schema": SCHEMA, "generated": now_iso(), "probing": bool(getattr(caps, "probing", False)),
            "plugins": {"validator": vst, "figure-forge": fst, "composer": cst},
            "features": features,
            "converters": [{"name": c["name"], "ok": c["ok"], "formats": c["formats"]} for c in convs],
            "converter_remedy": None if any(c["ok"] for c in convs) else ff_ad.NO_CONVERTER,
            "composer_location": {"configured": loc["configured"], "project": loc["project"],
                                  "source": loc["source"], "state_exists": loc["state_exists"]}}


def get_adapters(req):
    return Result(adapters_report(req.app), SCHEMA)


def _instrument(tool):
    fn = engine_fn(INSTRUMENT_FNS)
    if fn is None:
        return None
    try:
        inst = fn(tool)
    except (KeyError, LookupError, ValueError):
        return None
    return inst if isinstance(inst, dict) else None


def post_validator_check(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    doc = body.get("doc")
    try:
        tool = val_ad.check_doc(doc)
    except ValueError as exc:
        raise ApiError("BAD_REQUEST", str(exc)) from None
    adapter = val_ad.ValidatorAdapter(app.caps)
    try:
        res = adapter.check(doc, instrument=_instrument(tool))
    except ValueError as exc:
        raise ApiError("BAD_REQUEST", str(exc)) from None
    raise_for(res)
    warnings = []
    data = res["data"]
    if data.get("legacy"):
        warnings.append("A validator régi (bridge) módban fut; az ismert hibáit az őrök (5.0: H1–H4, H12, H13) "
                        "kijavítják, vagy megjelölik, ahol az eredménye nem megbízható.")
    return Result(data, RESULT_SCHEMA, warnings=warnings)


def register(router):
    router.add("GET", "/api/adapters", get_adapters, schema=SCHEMA)
    router.add("POST", "/api/validator/check", post_validator_check, schema=RESULT_SCHEMA,
               request_schema=CHECK_REQUEST,
               response_schema=_contracts.ref(RESULT_SCHEMA) if _contracts.available(RESULT_SCHEMA) else None)
