# -*- coding: utf-8 -*-
"""Elemzési specek (terv 2.4, 2.6, 3.4, 3.5.6, 4.4): ``05_elemzes/specs/<név>.json`` (szk.ma.analysis-spec/v1).

- ``GET /api/specs`` → ``{specs: [{name, path, etag, outcome, purpose, parent, prespecified, problems}]}``
- ``GET /api/specs/<név>`` → a spec maga (+ ETag); nincs ilyen: 404.
- ``PUT /api/specs/<név>`` ← a spec (If-Match: a betöltött ETag; nélküle csak új spec írható, vagy a
  meglévővel tartalmilag azonos — így a gyermek-spec újbóli létrehozása nem hiba). Ellenőrzés a motorral
  (``api.validate_spec``; hiba → 422 a motor üzenetlistájával), a ``data.path`` a projekten belüli
  adattábla legyen; PHI-szkenner és írás-tartás (``_common.phi_doc_guard``); mentés a motor kanonikus
  alakjában, atomikusan (``api.save_spec`` a tároló If-Match-ével); activity-sor ``spec.save``.

A spec-név a fájlnév is: ``^[a-z0-9][a-z0-9_-]{0,63}$`` (a motor mintája) — útvonal-bejárás kizárva."""
import re

from metaelemzes import api

from .. import store
from ..router import ApiError, Result
from . import _contracts
from ._common import dataset_rel, if_match, log_activity_or_warn, phi_doc_guard

SCHEMA = "szk.ma.analysis-spec/v1"
LIST_SCHEMA = "szk.ma.spec-list/v1"
SPEC_DIR = "05_elemzes/specs"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_SPECS = 500
MSG_NOT_FOUND = "Nincs ilyen elemzési spec (05_elemzes/specs/)."


def spec_rel(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ApiError("BAD_REQUEST", "Érvénytelen spec-név: kisbetű, szám, '_' vagy '-' (legfeljebb 64 karakter, "
                                      "betűvel vagy számmal kezdődik).")
    return "%s/%s.json" % (SPEC_DIR, name)


def load(app, name):
    """(spec, etag) vagy (None, None)."""
    doc, etag = app.store.load_json(spec_rel(name))
    if doc is not None and not isinstance(doc, dict):
        raise store.Invalid("A spec gyökere objektum legyen.", {"path": spec_rel(name)})
    return doc, etag


def _without_pin(spec):
    """A spec a data.sha256 nélkül (összevetéshez: a rögzítés a commit kérésében pinelhető)."""
    out = dict(spec)
    data = dict(out.get("data") or {})
    data.pop("sha256", None)
    out["data"] = data
    return out


def same_content(a, b):
    """Tartalmilag azonos-e a két spec (a motor kanonikus hash-e szerint, a data.sha256 nélkül)."""
    try:
        return api.spec_sha256(_without_pin(a)) == api.spec_sha256(_without_pin(b))
    except Exception:                                       # noqa: BLE001 — érvénytelen spec: nem azonos
        return False


def check_spec(req, spec):
    """A motor ellenőrzése (422 a hibalistával) + a data.path a projekten belüli adattábla (403/400)."""
    errors = api.validate_spec(spec)
    if errors:
        raise ApiError("VALIDATION", "Az elemzési spec érvénytelen: %s" % "; ".join(errors[:10]), {"errors": errors})
    dataset_rel(req, (spec.get("data") or {}).get("path"))
    return spec


def get_list(req):
    app = req.app
    app.require_open()
    d = app.store.path(SPEC_DIR) if (app.project_root / SPEC_DIR).is_dir() else None
    out = []
    if d is not None:
        for p in sorted(d.glob("*.json"))[:MAX_SPECS]:
            name = p.name[:-5]
            if not NAME_RE.match(name) or not p.is_file():
                continue
            try:
                doc, etag = load(app, name)
            except store.StoreError as exc:
                out.append({"name": name, "path": spec_rel(name), "etag": None, "problems": [exc.message]})
                continue
            problems = api.validate_spec(doc) if isinstance(doc, dict) else ["nem objektum"]
            doc = doc if isinstance(doc, dict) else {}
            out.append({"name": name, "path": spec_rel(name), "etag": etag, "outcome": doc.get("outcome"),
                        "purpose": doc.get("purpose"), "parent": doc.get("parent"),
                        "prespecified": doc.get("prespecified"), "problems": problems})
    return Result({"specs": out}, LIST_SCHEMA)


def get_spec(req):
    app = req.app
    app.require_open()
    doc, etag = load(app, req.params["name"])
    if doc is None:
        raise ApiError("NOT_FOUND", MSG_NOT_FOUND)
    problems = api.validate_spec(doc)
    warnings = ["A mentett spec a motor szerint hibás: %s" % "; ".join(problems[:10])] if problems else []
    return Result(doc, SCHEMA, warnings=warnings, etag=etag)


def put_spec(req):
    app = req.app
    app.require_open()
    name = req.params["name"]
    rel = spec_rel(name)
    spec = req.json_object()
    spec.pop("client_seq", None)
    if spec.get("name") != name:
        raise ApiError("BAD_REQUEST", "A spec name mezője (%s) és az útvonal neve (%s) eltér." % (spec.get("name"), name))
    check_spec(req, spec)
    phi_doc_guard(app, spec, rel, "elemzési spec")
    cond = if_match(req)
    if cond is None:
        cur, cur_etag = load(app, name)
        if cur is not None and same_content(cur, spec):
            return Result(cur, SCHEMA, etag=cur_etag)          # idempotens újraküldés (pl. gyermek-spec)
    sha = app.store.write_with(rel, lambda path: api.save_spec(path, spec), if_match=cond)
    warnings = []
    log_activity_or_warn(app, "spec.save", warnings, outputs=[(rel, sha)],
                         details={"name": name, "outcome": spec.get("outcome"), "purpose": spec.get("purpose"),
                                  "parent": spec.get("parent"), "prespecified": spec.get("prespecified")})
    saved, etag = load(app, name)
    return Result(saved, SCHEMA, warnings=warnings, etag=etag)


def register(router):
    router.add("GET", "/api/specs", get_list, schema=LIST_SCHEMA)
    router.add("GET", "/api/specs/<name>", get_spec, schema=SCHEMA, response_schema=_contracts.ref(SCHEMA))
    router.add("PUT", "/api/specs/<name>", put_spec, schema=SCHEMA, request_schema=_contracts.ref(SCHEMA),
               response_schema=_contracts.ref(SCHEMA))
