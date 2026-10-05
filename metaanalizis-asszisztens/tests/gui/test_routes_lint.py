# -*- coding: utf-8 -*-
"""A 6.8 tilalmi lista a szerveroldalon (AST, futtatás nélkül) és az egyetlen row_uid-függvény.

- A ``ma_gui`` a motorhoz CSAK a homlokzaton (``metaelemzes.api``) át fér hozzá: ``from metaelemzes import api``,
  ``import metaelemzes.api``, ``from metaelemzes.api import …``. A belső modulok (pipeline, tableio, projekt, kb,
  spec …) közvetlen importja tilos — kivéve az alábbi, egyenként indokolt KIVÉTELEKET; ezek is csak a felsorolt
  nevekre (attribútumokra) szólnak. Elavult kivétel (amire már nincs szükség) is hiba, hogy a lista ne nőjön.
- A meleg worker hívási célpontja csak ``metaelemzes.api:…`` vagy a munkapad commit-burka lehet; dinamikus
  import (``importlib.import_module`` / ``__import__``) motor-modulra nincs.
- A ``ma_gui`` egyetlen modulja sem importál ``math``/``cmath``/``statistics``/``random``/``decimal``-t (3.1, 6.8).
- row_uid: a munkapad tárolója és a motor ugyanazt a függvényt használja (``tableio.row_uid_for``): a táblák
  uid-jai a felületen, a validálásban és a plot/v2-ben azonosak."""
import ast
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from metaelemzes import api, tableio  # noqa: E402
from ma_gui import server, store  # noqa: E402

PKG = Path(ROOT) / "ma_gui"
BANNED = {"math", "cmath", "statistics", "random", "decimal"}

# (fájl a repó gyökeréhez képest, motor-modul) → (engedett nevek/attribútumok, indoklás)
EXCEPTIONS = {
    ("ma_gui/store.py", "metaelemzes.tableio"): (
        {"row_uid_for", "canonical_columns", "NUMERIC", "_decimal_mark", "_decode", "parse_number"},
        "a formátumtartó CSV-tároló a motorral AZONOS döntéseket hoz (kódolás, tizedesjel, oszlop-felismerés, "
        "row_uid): a homlokzat read_table/write_table-je nem ad bájtszintű ETag/diff/visszaírás-kezelést; "
        "statisztikát nem számol"),
}
# átmeneti kivételek (elavulás-ellenőrzés nélkül) — jelenleg nincs: a pillanatkép és az audit-export is a
# homlokzatot használja (api.__version__, api.kb_status()['db'], api.project_run_files)
TRANSITIONAL = {}
API_MODULE = "metaelemzes.api"
WORKER_TARGET_PREFIXES = ("metaelemzes.api:", "ma_gui.routes._worker:")


def _py_files():
    out = []
    for p in sorted(PKG.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if "/web/" in rel or "__pycache__" in rel:
            continue
        out.append((rel, p))
    return out


def _engine_imports(tree):
    """[(motor-modul, alias-név a kódban | None, importált nevek | None, sor)] — minden metaelemzes-import."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "metaelemzes" or a.name.startswith("metaelemzes."):
                    out.append((a.name, a.asname or a.name, None, node.lineno))
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            mod = node.module
            if mod == "metaelemzes":
                for a in node.names:
                    out.append(("metaelemzes.%s" % a.name, a.asname or a.name, None, node.lineno))
            elif mod.startswith("metaelemzes."):
                out.append((mod, None, {a.name for a in node.names}, node.lineno))
    return out


def _attrs_used(tree, alias):
    """Az alias-néven elért attribútumok (pl. tableio.row_uid_for → {'row_uid_for'})."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == alias:
            names.add(node.attr)
    return names


class EngineImportLint(unittest.TestCase):
    def test_engine_only_through_facade(self):
        used = set()
        for rel, path in _py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for mod, alias, names, line in _engine_imports(tree):
                where = "%s:%d (%s)" % (rel, line, mod)
                if mod == API_MODULE or mod.startswith(API_MODULE + "."):
                    continue
                key = (rel, mod)
                if key in EXCEPTIONS:
                    allowed, reason = EXCEPTIONS[key]
                    self.assertTrue(reason)
                    used.add(key)
                elif key in TRANSITIONAL:
                    allowed = TRANSITIONAL[key]
                else:
                    self.fail("6.8: a ma_gui a motort csak a metaelemzes.api-n át érheti el — %s" % where)
                got = names if names is not None else _attrs_used(tree, alias.split(".")[-1])
                if mod == "metaelemzes" and names is None:
                    got = _attrs_used(tree, "metaelemzes")
                extra = set(got) - set(allowed)
                self.assertFalse(extra, "6.8: a kivétel csak ezekre szól: %s; %s ezt is használja: %s"
                                 % (sorted(allowed), where, sorted(extra)))
        stale = set(EXCEPTIONS) - used
        self.assertFalse(stale, "elavult 6.8-kivétel (már nincs rá szükség — töröld a listából): %s" % sorted(stale))

    def test_vocab_exception_is_constants_only(self):
        tree = ast.parse((PKG / "routes" / "log.py").read_text(encoding="utf-8"))
        for attr in _attrs_used(tree, "_vocab"):
            self.assertTrue(attr.isupper(), "a projekt-szókincsből csak konstans vehető át: %s" % attr)

    def test_worker_targets_and_dynamic_imports(self):
        for rel, path in _py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    s = node.value
                    if (s.startswith("metaelemzes.") or s.startswith("ma_gui.")) and ":" in s and " " not in s:
                        self.assertTrue(s.startswith(WORKER_TARGET_PREFIXES), "%s: worker-célpont: %s" % (rel, s))
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
                    if name in ("import_module", "__import__") and node.args:
                        arg = node.args[0]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            self.assertFalse(arg.value.startswith("metaelemzes") and arg.value != API_MODULE,
                                             "%s: dinamikus motor-import: %s" % (rel, arg.value))
        self.assertEqual(tuple(server.JOB_ALLOWED_MODULES), ("metaelemzes.api", "ma_gui.routes._worker"))

    def test_no_statistics_modules_anywhere_in_ma_gui(self):
        for rel, path in _py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    mods = [node.module or ""]
                for m in mods:
                    self.assertNotIn(m.split(".")[0], BANNED, "%s: %s" % (rel, m))


class RowUidSingleSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ma_rowuid_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_store_uses_engine_function(self):
        for label, i in (("Aronson 1948", 0), ("  NA ", 3), (None, 7), ("x\x07y", 2), ("", 0)):
            self.assertEqual(store.derive_row_uid(label, i), tableio.row_uid_for(label, i))
        taken = {tableio.row_uid_for("x", 0)}
        self.assertEqual(store.derive_row_uid("x", 0, taken), tableio.row_uid_for("x", 0, taken))

    def test_store_engine_and_plot_agree_on_uids(self):
        raw = ("study;e1;n1;e2;n2\nAronson 1948;4;123;11;139\nNA;6;306;29;303\n\n"
               "Rosenthal 1960;3;231;11;220\nAronson 1948;62;13598;248;12867\n").encode("utf-8")
        path = os.path.join(self.tmp, "t.csv")
        with open(path, "wb") as fh:
            fh.write(raw)
        uids_store = store._to_table("03_adatok/t.csv", store.parse_csv_bytes(raw), "x").row_uids
        rows, meta = tableio.read_table(path)
        self.assertEqual(uids_store, tableio.row_uids(rows, meta))
        spec = {"schema": "szk.ma.analysis-spec/v1", "name": "t", "outcome": "t", "data": {"path": "t.csv"},
                "options": {"measure": "RR"}}
        view = api.analyze(spec, mode="explore", project_root=self.tmp)
        self.assertEqual([s["row_uid"] for s in view["plot"]["studies"]], uids_store)


class ServedPageTests(unittest.TestCase):
    """GET / a build_gui.py termék-buildjét (web/dist/index.html) szolgálja ki; ha az hiányzik, a static/
    tartalék oldalát (útmutatással)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ma_index_")
        self.app = server.App(self.tmp, selftest=False, kb_build=False, caps_refresh=False, log_stream=None,
                              sweep_tmp=False)

    def tearDown(self):
        self.app.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prefers_dist_build(self):
        self.assertEqual(server.INDEX_CANDIDATES[0], PKG / "web" / "dist" / "index.html")
        if not server.WEB_DIST_HTML.is_file():
            self.skipTest("nincs ma_gui/web/dist/index.html")
        self.assertEqual(self.app.index_html(), server.WEB_DIST_HTML.read_bytes())

    def test_fallback_to_static_page(self):
        from unittest import mock
        with mock.patch.object(server, "INDEX_CANDIDATES", (Path(self.tmp) / "nincs.html", server.INDEX_HTML)):
            html = self.app.index_html()
        self.assertEqual(html, server.INDEX_HTML.read_bytes())
        self.assertIn("build_gui.py".encode(), html)
        with mock.patch.object(server, "INDEX_CANDIDATES", (Path(self.tmp) / "nincs.html",)):
            self.assertIsNone(self.app.index_html())


if __name__ == "__main__":
    unittest.main()
