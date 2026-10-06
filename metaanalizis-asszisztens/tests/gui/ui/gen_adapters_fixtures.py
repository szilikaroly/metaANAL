#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Az adapter-képernyők (Ábra-export 3.5.9, Composer-forrás 3.5.14, Képességek 3.5.16, validator-keresztellenőrzés)
fejlesztői fixture-jeinek generátora → ``ma_gui/web/fixtures/adapters_{caps,figures,composer,validator}.json``.

Minden boríték a VALÓDI munkapad-szerver válasza (``ma_gui.server.App``, ``tests/gui/test_routes_harness.Srv``) egy
ideiglenes projekten, a BCG-példa VALÓDI commit-futásával (``api.analyze``) és a stub-pluginokkal
(``tests/gui/_adapters_stubs.py``; a validator legacy-kimenetei a rögzített 1.0.0-s golden-fájlok) — kézzel írt
szám nincs bennük (4.20/5). Három „világ”: ``legacy`` (validator 1.0.0, figure-forge 0.2.1, composer 1.4.1),
``h5`` (figure-forge matplotlib nélkül, validator és composer hiányzik) és ``ok`` (validator 1.1 json, figure-forge F2,
composer C1). A dev-háttér (``src/dev/adapters_backend.js``) a ``query.fx`` jelölő szerint választ.

Normalizálás (determinizmus): fix futás-azonosító, a végpontok órája (``now_iso``) rögzítve, egyéb időbélyegek,
ideiglenes utak, aláírt URL-ek, kérés-azonosítók, interpreter-utak helyőrzővel.

Használat:
  python3 tests/gui/ui/gen_adapters_fixtures.py           # írás
  python3 tests/gui/ui/gen_adapters_fixtures.py --check   # 1-es kilépés, ha a lemezen lévő eltér
"""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
GUI = ROOT / "tests" / "gui"
for p in (str(ROOT), str(GUI)):
    if p not in sys.path:
        sys.path.insert(0, p)
FIX = ROOT / "ma_gui" / "web" / "fixtures"

import _adapters_stubs as STUBS  # noqa: E402
import test_routes_harness as H  # noqa: E402

from metaelemzes import api  # noqa: E402
from ma_gui import caps as caps_mod  # noqa: E402
from ma_gui.adapters import composer as composer_ad  # noqa: E402
from ma_gui.routes import adapters as r_adapters  # noqa: E402
from ma_gui.routes import adapters_composer as r_composer  # noqa: E402
from ma_gui.routes import adapters_figures as r_figures  # noqa: E402
from ma_gui.routes import prisma as r_prisma  # noqa: E402

RUN_ID = "20261004T211200Z-a1f3c2"
NOW = "2026-10-05T10:00:00Z"
FAKE_HOME = "/home/szk/Documents"
EXPIRES = 1791200000
INSTR = ROOT / "metaelemzes" / "instruments"
DOCS = json.loads((GUI / "adapters_golden" / "docs.json").read_text(encoding="utf-8"))
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
URL_RE = re.compile(r"(/f/[^/\"]+)/\d+/[A-Za-z0-9_\-]+")


def _instrument(tool):
    p = INSTR / ("%s.json" % tool)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


class World(object):
    """Egy ideiglenes projekt + stub-pluginok + élő szerver; a válaszok normalizálva gyűlnek."""

    def __init__(self, tmp, name, ff, val, comp):
        self.tmp = tmp
        self.plugins = STUBS.make_plugins(os.path.join(tmp, "plugins_" + name), figure_forge=ff, validator=val,
                                          composer=comp)
        env = {"MA_GUI_PLUGIN_DIRS": self.plugins, "PATH": ""}
        caps = caps_mod.Caps(runtime_dir=os.path.join(tmp, "caps_" + name), env=env,
                             home=os.path.join(tmp, "nohome"), specs=STUBS.caps_specs())
        self.srv = H.Srv(PROJ[0], PROJ[1], tmp, name=name, caps=caps)

    def call(self, method, path, body=None, headers=()):
        st, h, env = self.srv.call(method, path, body, headers=headers)
        return st, h.get("etag"), env

    def close(self):
        self.srv.app.shutdown()
        self.srv.app.close()


PROJ = [None, None]


def norm(obj, tmp):
    text = json.dumps(obj, ensure_ascii=False, sort_keys=False)
    for src, dst in ((os.path.join(tmp, "PubMed_Downloads"), FAKE_HOME + "/PubMed_Downloads"), (tmp, FAKE_HOME),
                     (sys.executable, "python3")):
        text = text.replace(json.dumps(src)[1:-1], dst)
    text = ISO_RE.sub(NOW, text)
    text = URL_RE.sub(lambda m: "%s/%d/SIG" % (m.group(1), EXPIRES), text)
    text = re.sub(r'"expires_at": \d+', '"expires_at": %d' % EXPIRES, text)
    doc = json.loads(text)
    if isinstance(doc, dict) and isinstance(doc.get("meta"), dict):
        doc["meta"].update(elapsed_ms=1, project_rev=61)
    return doc


_RID = [0]


def route(method, path, status, etag, env, fx=None, tmp=None, query=None):
    _RID[0] += 1
    env = norm(env, tmp)
    if env.get("ok") and isinstance(env.get("meta"), dict):
        env["meta"]["request_id"] = "q_adp%03d" % _RID[0]
    if isinstance(env.get("data"), dict):
        _strip_elapsed(env["data"])
    r = {"method": method, "path": path}
    q = dict(query or {})
    if fx:
        q["fx"] = fx
    if q:
        r["query"] = q
    r["status"] = status
    if etag:
        r["etag"] = etag
    r["envelope"] = env
    return r


def _strip_elapsed(d):
    if isinstance(d, dict):
        for k in list(d):
            if k == "elapsed_ms":
                d[k] = 1
            else:
                _strip_elapsed(d[k])
    elif isinstance(d, list):
        for x in d:
            _strip_elapsed(x)


def generate():
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_adpfx_"))
    out = {"caps": [], "figures": [], "composer": [], "validator": []}
    try:
        proj, home = H.make_project(tmp)
        PROJ[0], PROJ[1] = proj, home
        os.makedirs(os.path.join(proj, "05_elemzes", "specs"))
        sp = os.path.join(proj, "05_elemzes", "specs", "o1_primary.json")
        api.save_spec(sp, H.spec())
        with contextlib.redirect_stdout(io.StringIO()):
            api.analyze(sp, mode="commit", project_root=proj, run_id=RUN_ID, date="2026-10-04")
        STUBS.composer_state(os.path.join(tmp, "PubMed_Downloads"))
        patches = [mock.patch.object(m, "now_iso", lambda: NOW) for m in (r_adapters, r_composer, r_figures, r_prisma)]
        patches.append(mock.patch.object(r_adapters, "_instrument", _instrument))
        patches.append(mock.patch.object(r_figures, "_render_fn", lambda: None))
        patches.append(mock.patch.object(composer_ad.ComposerAdapter, "sleep", staticmethod(lambda s: None)))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            _legacy(tmp, out)
            _h5(tmp, out)
            _ok(tmp, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _add(out, key, method, path, res, fx, tmp, query=None):
    st, etag, env = res
    out[key].append(route(method, path, st, etag, env, fx, tmp, query))


def _legacy(tmp, out):
    w = World(tmp, "legacy", {"mode": "legacy"}, {"mode": "legacy"}, {"mode": "legacy"})
    try:
        _add(out, "caps", "GET", "/api/adapters", w.call("GET", "/api/adapters"), "legacy", tmp)
        _add(out, "figures", "GET", "/api/figures", w.call("GET", "/api/figures"), "legacy-norun", tmp)
        _add(out, "figures", "GET", "/api/figures", w.call("GET", "/api/figures?run=" + RUN_ID), "legacy", tmp,
             {"run": RUN_ID})
        body = {"run_id": RUN_ID, "kind": "forest", "formats": ["svg", "pdf", "png", "tiff"], "lang": "en"}
        _add(out, "figures", "POST", "/api/figures/export", w.call("POST", "/api/figures/export", body), "legacy", tmp)
        _add(out, "figures", "POST", "/api/figures/export", w.call("POST", "/api/figures/export", body), "conflict",
             tmp)
        _add(out, "figures", "POST", "/api/figures/audit",
             w.call("POST", "/api/figures/audit", {"path": "06_kezirat/abrak/fig_forest_o1.svg"}), "legacy", tmp)
        _add(out, "figures", "GET", "/api/figures", w.call("GET", "/api/figures?run=" + RUN_ID), "legacy-after", tmp,
             {"run": RUN_ID})
        # composer: beállítatlan → beállítva → előnézet → mentés → utána
        _add(out, "composer", "GET", "/api/prisma/composer", w.call("GET", "/api/prisma/composer"), "unconfigured",
             tmp)
        cfg = {"outdir": os.path.join(tmp, "PubMed_Downloads"), "project": "glp1"}
        _add(out, "composer", "PUT", "/api/prisma/composer/config",
             w.call("PUT", "/api/prisma/composer/config", cfg), "configured", tmp)
        _add(out, "composer", "POST", "/api/prisma/composer/refresh",
             w.call("POST", "/api/prisma/composer/refresh", {"dry_run": True}), "preview", tmp)
        flow = {"identified_databases": "1234", "screened": "946", "excluded_screening": "860"}
        st, etag, _env = w.call("PUT", "/api/prisma/manual", {"flow": flow})
        _add(out, "composer", "POST", "/api/prisma/composer/refresh",
             w.call("POST", "/api/prisma/composer/refresh", {}, headers=[("If-Match", etag)]), "needs-confirm", tmp)
        _add(out, "composer", "GET", "/api/prisma/composer", w.call("GET", "/api/prisma/composer"), "manual", tmp)
        _add(out, "composer", "POST", "/api/prisma/composer/refresh",
             w.call("POST", "/api/prisma/composer/refresh", {"confirm_replace_manual": True},
                    headers=[("If-Match", etag)]), "written", tmp)
        _add(out, "composer", "GET", "/api/prisma/composer", w.call("GET", "/api/prisma/composer"), "composer", tmp)
        # validator (bridge, őrökkel)
        for name in ("rob2", "probast_dev", "tripod_empty", "grade_strong", "amstar2_py"):
            _add(out, "validator", "POST", "/api/validator/check",
                 w.call("POST", "/api/validator/check", {"doc": DOCS[name]}), "legacy", tmp, {"tool": DOCS[name]["tool"]})
    finally:
        w.close()


def _h5(tmp, out):
    w = World(tmp, "h5", {"mode": "h5"}, None, None)
    try:
        _add(out, "caps", "GET", "/api/adapters", w.call("GET", "/api/adapters"), "h5", tmp)
        _add(out, "figures", "GET", "/api/figures", w.call("GET", "/api/figures?run=" + RUN_ID), "h5", tmp,
             {"run": RUN_ID})
        body = {"run_id": RUN_ID, "kind": "doi", "formats": ["svg"], "lang": "hu"}
        _add(out, "figures", "POST", "/api/figures/export", w.call("POST", "/api/figures/export", body), "h5", tmp)
        _add(out, "composer", "GET", "/api/prisma/composer", w.call("GET", "/api/prisma/composer"), "absent", tmp)
        _add(out, "validator", "POST", "/api/validator/check",
             w.call("POST", "/api/validator/check", {"doc": DOCS["rob2"]}), "absent", tmp)
    finally:
        w.close()


def _ok(tmp, out):
    w = World(tmp, "ok", {"mode": "f2", "version": "0.3.0"}, {"mode": "json", "version": "1.1.0",
                                                               "results": {"rob2": _json_result()}},
              {"mode": "json", "version": "1.5.0"})
    try:
        _add(out, "caps", "GET", "/api/adapters", w.call("GET", "/api/adapters"), "ok", tmp)
        _add(out, "figures", "GET", "/api/figures", w.call("GET", "/api/figures?run=" + RUN_ID), "ok", tmp,
             {"run": RUN_ID})
        body = {"run_id": RUN_ID, "kind": "forest", "formats": ["svg", "pdf", "tiff", "pptx"], "lang": "hu",
                "renderer": "figure-forge", "stem": "fig2_forest", "overwrite": True}
        _add(out, "figures", "POST", "/api/figures/export", w.call("POST", "/api/figures/export", body), "ff", tmp)
        STUBS.make_plugin(w.plugins, "figure-forge", {"mode": "f2", "version": "0.3.0", "drop_number": True})
        w.call("POST", "/api/capabilities/refresh", {})
        body = dict(body, stem="fig2_forest_bad")
        _add(out, "figures", "POST", "/api/figures/export", w.call("POST", "/api/figures/export", body), "issues",
             tmp)
        _add(out, "validator", "POST", "/api/validator/check",
             w.call("POST", "/api/validator/check", {"doc": DOCS["rob2"]}), "json", tmp)
    finally:
        w.close()


def _json_result():
    """A V1-es validator (stub) válasza — a legacy rob2-eredmény alakja (a mezők a 4.11 szerződésből)."""
    return {"tool": "rob2", "legacy": False, "complete": False, "expected": 22, "answered": 21,
            "completeness_text": "21/22", "missing": [{"item": "5.3", "pass": None, "key": "5.3"}], "invalid": [],
            "domains": [{"domain": "2", "implied": "high", "level": "high", "algorithm": "conservative",
                         "forced_by": ["2.6"], "unknown_at": [], "routers": ["2.1"]}],
            "overall": {"implied": "high", "level": "high", "algorithm": "conservative", "official": False},
            "guards": [], "notes": [{"hu": "Ez NEM a hivatalos RoB 2 folyamatábra.",
                                     "en": "This is NOT the published RoB 2 flowchart."}]}


DESCRIPTIONS = {
    "caps": "GET /api/adapters — az adapter-állapot funkciónként (absent/unusable/legacy/ok, mód, tartalék, magyar teendő, "
            "H-őrök). fx: legacy (validator 1.0.0 bridge, figure-forge 0.2.1, composer 1.4.1 beállítatlan) · h5 "
            "(figure-forge matplotlib nélkül, a többi hiányzik) · ok (V1/F2/C1). A valódi szerver válaszai stub-pluginokkal "
            "(tests/gui/ui/gen_adapters_fixtures.py).",
    "figures": "GET /api/figures, POST /api/figures/export, POST /api/figures/audit — a valódi szerver válaszai a BCG "
               "VALÓDI commit-futásán (a számok és a számhűség-ellenőrzés a motor szövegeiből). fx: legacy (motor-SVG, "
               "PNG/PDF átalakító nélkül, TIFF figure-forge nélkül kimarad), conflict (409), h5 (a figure-forge audit "
               "H5 miatt nem fut — a QC a munkapad saját ellenőrzése), ff (figure-forge F2), issues (egy szám hiányzik "
               "az ábráról → nem zöld).",
    "composer": "GET /api/prisma/composer, PUT …/config, POST …/refresh — a valódi szerver válaszai a composer stub-"
                "pluginnal (1.4.1 bridge: explicit interpreter, H7). fx: unconfigured, configured, preview, "
                "needs-confirm (409), manual, written, composer, absent.",
    "validator": "POST /api/validator/check — a validator keresztellenőrzése (szk.appraisal-result/v1). fx: legacy "
                 "(1.0.0 golden-kimenetek: H1 üres TRIPOD 1/52, H2 PROBAST 32/34, H3 GRADE, H4 AMSTAR 2 — az őrök "
                 "javításával; query.tool szerint), json (V1), absent (424).",
}


def render(key, routes):
    return json.dumps({"description": DESCRIPTIONS[key], "routes": routes}, ensure_ascii=False, indent=1) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    out = generate()
    bad = []
    for key, routes in out.items():
        path = FIX / ("adapters_%s.json" % key)
        text = render(key, routes)
        if a.check:
            cur = path.read_text(encoding="utf-8") if path.is_file() else None
            if cur != text:
                bad.append(path.name)
        else:
            path.write_text(text, encoding="utf-8")
    if bad:
        print("elsodródott fixture: %s — futtasd: python3 tests/gui/ui/gen_adapters_fixtures.py" % ", ".join(bad))
        return 1
    if not a.check:
        print("írva: %s" % ", ".join("adapters_%s.json" % k for k in out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
