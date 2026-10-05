# -*- coding: utf-8 -*-
"""ma_gui.caps és ma_gui.adapters.base: felderítés, interpreter-választás, kézfogás
(absent / unusable / legacy / ok, mind a 16 kombináció), sodródás-őr, időkorlát és folyamatfa,
argv-only hívás (shell nélkül), gyorsítótár, képesség-mátrix (5.2), motor-leírás, Adapter.run
hibaborítékai, opcióérték-validálás, stdlib-only / statisztika-tilalom (AST)."""
import ast
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ma_gui import caps  # noqa: E402
from ma_gui.adapters import base  # noqa: E402

STUBS = Path(HERE) / "stub_plugins"
PLUGINS = ("figure-forge", "validator", "composer", "presubmit")
HOME_VAR = {p: "SZK_%s_HOME" % p.upper().replace("-", "_") for p in PLUGINS}
FEATURES_OF = {p: [f["id"] for f in caps.FEATURES if f["plugin"] == p] for p in PLUGINS}
BANNED = {"math", "cmath", "statistics", "random", "decimal"}
MODULE_FILES = [Path(ROOT) / "ma_gui" / "caps.py", Path(ROOT) / "ma_gui" / "adapters" / "__init__.py",
                Path(ROOT) / "ma_gui" / "adapters" / "base.py"]


def quiet_specs():
    """A PLUGINS import-szonda és bővített interpreter-keresés nélkül (hermetikus tesztekhez)."""
    specs = copy.deepcopy(caps.PLUGINS)
    for spec in specs.values():
        spec["probe_imports"] = ()
        spec["optional_imports"] = ()
        spec["extended_python"] = False
        spec["python_env"] = ()
    return specs


class StubCase(unittest.TestCase):
    """Ideiglenes munkaterület: a stub-pluginok másolata + _stubcore.py a gyökérben."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ma-gui-caps-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        shutil.copy2(str(STUBS / "_stubcore.py"), str(self.tmp / "_stubcore.py"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.runtime = self.tmp / "runtime"
        self.plugdir = self.tmp / "plugins"
        self.plugdir.mkdir()
        self.contracts = self.tmp / "contracts"
        self.contracts.mkdir()

    def install(self, plugin, mode="ok", root=None, name=None, **cfg):
        root = Path(root) if root is not None else self.plugdir
        dest = root / (name or plugin)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(STUBS / plugin), str(dest), ignore=shutil.ignore_patterns("__pycache__", "stub.json"))
        cfg["mode"] = mode
        (dest / "stub.json").write_text(json.dumps(cfg), encoding="utf-8")
        return dest

    def make_caps(self, env_extra=None, **kw):
        env = {"MA_GUI_PLUGIN_DIRS": str(self.plugdir)}
        env.update(env_extra or {})
        opts = dict(runtime_dir=self.runtime, env=env, home=self.home, python=sys.executable,
                    contracts_dirs=[self.contracts], timeout=8.0, specs=quiet_specs())
        opts.update(kw)
        return caps.Caps(**opts)

    def codes(self, cap, level=None):
        return [p["code"] for p in cap["problems"] if level is None or p["level"] == level]


# ---------------------------------------------------------------- állapotok

class TestStates(StubCase):

    def test_all_ok(self):
        for p in PLUGINS:
            self.install(p, "ok")
        recs = self.make_caps().refresh()
        self.assertEqual(set(recs), set(PLUGINS))
        for p in PLUGINS:
            cap = recs[p]
            self.assertEqual(cap["state"], "ok", p)
            self.assertEqual(cap["handshake"], "ok")
            self.assertEqual(cap["mode"], "json")
            self.assertEqual(cap["python"], [sys.executable])
            self.assertEqual(cap["python_source"], "sys.executable")
            self.assertEqual(cap["source"], "MA_GUI_PLUGIN_DIRS")
            self.assertEqual(cap["capabilities"]["plugin"], p)
            self.assertEqual(self.codes(cap, "error"), [])
            self.assertTrue(cap["features"])
            self.assertIsInstance(cap, base.Capability)
            self.assertEqual(cap.state, "ok")
            self.assertTrue(cap.usable)
            json.dumps(cap)
        self.assertEqual(recs["validator"]["version"], "1.0.0")
        self.assertEqual(sorted(recs["validator"]["scripts"]), ["appraise.py", "checklist.py"])
        self.assertTrue(recs["validator"]["script"].endswith("appraise.py"))

    def test_all_legacy(self):
        for p in PLUGINS:
            self.install(p, "legacy")
        recs = self.make_caps().refresh()
        for p in PLUGINS:
            self.assertEqual(recs[p]["state"], "legacy", p)
            self.assertEqual(recs[p]["mode"], "bridge")
            self.assertEqual(recs[p]["handshake"], "missing")
            self.assertIsNone(recs[p]["capabilities"])
            self.assertIn("bridge", recs[p]["note"]["hu"])
        self.assertEqual(recs["validator"]["guards"], ["H1", "H2", "H3", "H4"])
        self.assertEqual(recs["figure-forge"]["guards"], ["H5", "H6"])
        self.assertEqual(recs["composer"]["guards"], ["H7"])
        self.assertEqual(recs["presubmit"]["guards"], [])
        # a verzió a plugin.json-ból jön; a bridge-hez rögzített verziónál nincs figyelmeztetés
        self.assertEqual(recs["validator"]["version"], "1.0.0")
        self.assertNotIn("bridge_untested_version", self.codes(recs["validator"]))
        self.assertEqual([i["id"] for i in recs["validator"]["known_issues"]], ["H1", "H2", "H3", "H4"])

    def test_legacy_untested_version_is_info(self):
        dest = self.install("validator", "legacy")
        manifest = dest / ".claude-plugin" / "plugin.json"
        manifest.write_text(json.dumps({"name": "validator", "version": "1.0.3"}), encoding="utf-8")
        cap = self.make_caps().get("validator")
        self.assertEqual(cap["state"], "legacy")
        self.assertEqual(cap["version"], "1.0.3")
        self.assertIn("bridge_untested_version", self.codes(cap, "info"))

    def test_all_unusable(self):
        for p in PLUGINS:
            self.install(p, "unusable", missing=["matplotlib", "PIL"])
        recs = self.make_caps().refresh()
        for p in PLUGINS:
            cap = recs[p]
            self.assertEqual(cap["state"], "unusable", p)
            self.assertEqual(cap["handshake"], "ok")
            self.assertEqual(cap["missing_modules"], ["matplotlib", "PIL"])
            prob = [x for x in cap["problems"] if x["code"] == "missing_modules"][0]
            self.assertEqual(prob["level"], "error")
            self.assertIn("matplotlib", prob["hu"])
            self.assertIn("hiányzik", prob["hu"])
            self.assertIn("pip install matplotlib Pillow", cap["todo"]["hu"])
            self.assertEqual(cap["features"], [])
        self.assertIn("FIGURE_FORGE_PYTHON", recs["figure-forge"]["todo"]["hu"])
        self.assertIn("caps.json plugins.validator.python", recs["validator"]["todo"]["hu"])

    def test_all_absent(self):
        recs = self.make_caps().refresh()
        for p in PLUGINS:
            cap = recs[p]
            self.assertEqual(cap["state"], "absent", p)
            self.assertIsNone(cap["home"])
            self.assertIsNone(cap["python"])
            self.assertIn("claude plugin install %s@szk-plugins" % p, cap["todo"]["hu"])
            self.assertIn(HOME_VAR[p], cap["todo"]["hu"])
            self.assertFalse(cap.usable)

    def test_sixteen_combinations(self):
        """absent / unusable / legacy / ok × 4 plugin — négy forgatással mind a 16 pár előáll (8.3)."""
        states = ("absent", "unusable", "legacy", "ok")
        trees = {}
        for st in states[1:]:
            root = self.tmp / ("tree-" + st)
            for p in PLUGINS:
                self.install(p, st, root=root, missing=["ma_gui_stub_dep"])
            trees[st] = root
        seen = set()
        for shift in range(4):
            env = {"MA_GUI_PLUGIN_DIRS": ""}
            want = {}
            for i, p in enumerate(PLUGINS):
                st = states[(i + shift) % 4]
                want[p] = st
                if st != "absent":
                    env[HOME_VAR[p]] = str(trees[st] / p)
            c = self.make_caps(env_extra=env)
            recs = c.refresh()
            matrix = c.capability_matrix()
            for p in PLUGINS:
                self.assertEqual(recs[p]["state"], want[p], (shift, p))
                if want[p] != "absent":
                    self.assertEqual(recs[p]["source"], "env:" + HOME_VAR[p])
                for fid in FEATURES_OF[p]:
                    self.assertEqual(matrix[fid]["with_plugin"], want[p], (shift, p, fid))
                seen.add((p, want[p]))
        self.assertEqual(len(seen), 16)

    def test_missing_module_traceback_is_unusable(self):
        """H5-minta: a szkript modulszintű importja elhasal → unusable, a modul megnevezve."""
        self.install("figure-forge", "missingmod", module="ma_gui_stub_matplotlib_nincs")
        cap = self.make_caps().get("figure-forge")
        self.assertEqual(cap["state"], "unusable")
        self.assertEqual(cap["handshake"], "failed")
        self.assertEqual(cap["missing_modules"], ["ma_gui_stub_matplotlib_nincs"])
        self.assertIn("ma_gui_stub_matplotlib_nincs hiányzik", cap["problems"][0]["hu"])
        self.assertIn("FIGURE_FORGE_PYTHON", cap["todo"]["hu"])

    def test_garbage_output_is_legacy(self):
        self.install("validator", "garbage")
        cap = self.make_caps().get("validator")
        self.assertEqual(cap["state"], "legacy")
        self.assertEqual(cap["handshake"], "missing")
        self.assertEqual(self.codes(cap, "error"), [])

    def test_crash_is_legacy_with_warning(self):
        self.install("presubmit", "crash")
        cap = self.make_caps().get("presubmit")
        self.assertEqual(cap["state"], "legacy")
        self.assertIn("help_probe_failed", self.codes(cap, "warning"))

    def test_badshape_is_legacy_invalid(self):
        self.install("validator", "badshape")
        cap = self.make_caps().get("validator")
        self.assertEqual(cap["state"], "legacy")
        self.assertEqual(cap["handshake"], "invalid")
        prob = [p for p in cap["problems"] if p["code"] == "handshake_invalid"][0]
        self.assertIn("hiányzó mező", prob["hu"])

    def test_unknown_major_version_is_legacy(self):
        self.install("validator", "v2")
        cap = self.make_caps().get("validator")
        self.assertEqual(cap["state"], "legacy")
        self.assertEqual(cap["handshake"], "invalid")

    def test_wrong_plugin_name_is_legacy(self):
        self.install("composer", "wrongname")
        cap = self.make_caps().get("composer")
        self.assertEqual(cap["state"], "legacy")
        self.assertIn("handshake_invalid", self.codes(cap))

    def test_not_ok_without_missing_is_unusable(self):
        self.install("presubmit", "notok")
        cap = self.make_caps().get("presubmit")
        self.assertEqual(cap["state"], "unusable")
        self.assertEqual(self.codes(cap, "error"), ["plugin_not_ok"])
        self.assertIn("caps.json", cap["todo"]["hu"])

    def test_timeout_is_unusable_and_fast(self):
        self.install("validator", "slow", sleep=60)
        c = self.make_caps(timeout=1.0)
        t0 = time.monotonic()
        cap = c.get("validator")
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 15.0)
        self.assertEqual(cap["state"], "unusable")
        self.assertEqual(cap["handshake"], "timeout")
        self.assertEqual(self.codes(cap, "error"), ["handshake_timeout"])
        self.assertIn("Újraszondázás", cap["todo"]["hu"])


# ---------------------------------------------------------------- sodródás-őr

class TestContractDrift(StubCase):

    def fixture(self):
        return STUBS / "validator" / "contracts" / "szk.instrument.v1.schema.json"

    def test_match(self):
        self.install("validator", "ok")
        shutil.copy2(str(self.fixture()), str(self.contracts / "szk.instrument.v1.schema.json"))
        cap = self.make_caps().get("validator")
        self.assertEqual(cap["state"], "ok")
        self.assertEqual(cap["drift"], [])
        entry = cap["contracts"][0]
        self.assertEqual(entry["name"], "szk.instrument/v1")
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["local"], "match")
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["sha256"], hashlib.sha256(self.fixture().read_bytes()).hexdigest())

    def test_missing_local_copy_is_unknown_not_drift(self):
        self.install("validator", "ok")
        for dirs in ([self.contracts], [self.tmp / "nincs-ilyen-mappa"]):
            cap = self.make_caps(contracts_dirs=dirs).get("validator")
            self.assertEqual(cap["state"], "ok")
            self.assertEqual(cap["contracts"][0]["local"], "unknown")
            self.assertNotIn("contract_drift", self.codes(cap))

    def test_drift_makes_affected_features_legacy(self):
        commands = [
            {"name": "appraise.verify", "argv": [], "in": ["szk.appraisal/v1"], "out": ["szk.appraisal-result/v1"]},
            {"name": "checklist.verify", "argv": [], "out": ["szk.instrument/v1"]},
        ]
        self.install("validator", "ok", commands=commands)
        local = json.loads(self.fixture().read_text(encoding="utf-8"))
        local["title"] = "helyi, eltérő másolat"
        (self.contracts / "masolat.schema.json").write_text(json.dumps(local), encoding="utf-8")
        c = self.make_caps()
        cap = c.get("validator")
        self.assertEqual(cap["state"], "legacy")
        self.assertEqual(cap["handshake"], "ok")
        self.assertEqual(cap["drift"], ["szk.instrument/v1"])
        self.assertEqual(cap["contracts"][0]["local"], "drift")
        self.assertFalse(cap["contracts"][0]["ok"])
        prob = [p for p in cap["problems"] if p["code"] == "contract_drift"][0]
        self.assertIn("szerződés-eltérés", prob["hu"])
        modes = {cmd["name"]: cmd["mode"] for cmd in cap["commands"]}
        self.assertEqual(modes, {"appraise.verify": "json", "checklist.verify": "legacy"})
        matrix = c.capability_matrix()
        for fid in ("probast", "tripod"):
            self.assertEqual(matrix[fid]["with_plugin"], "legacy")
            self.assertIn("szerződés-eltérés", matrix[fid]["note"]["hu"])
        for fid in ("rob", "grade", "amstar2"):
            self.assertEqual(matrix[fid]["with_plugin"], "ok")

    def test_contract_key_normalization(self):
        for name in ("szk.instrument/v1", "urn:szk:contract:instrument:1", "szk.instrument.v1.schema.json",
                     "SZK.Instrument/V1"):
            self.assertEqual(caps.contract_key(name), "szk.instrument/v1", name)
        self.assertEqual(caps.contract_key("urn:szk:contract:ma.plot:2"), "szk.ma.plot/v2")
        index = caps.local_contract_index([STUBS / "validator" / "contracts"])
        self.assertIn("szk.instrument/v1", index)
        self.assertEqual(len(index["szk.instrument/v1"]), 1)


# ---------------------------------------------------------------- felderítés

class TestDiscovery(StubCase):

    def test_env_home_beats_plugin_dirs(self):
        self.install("validator", "ok")
        other = self.install("validator", "legacy", root=self.tmp / "masik")
        cap = self.make_caps(env_extra={"SZK_VALIDATOR_HOME": str(other)}).get("validator")
        self.assertEqual(cap["state"], "legacy")
        self.assertEqual(cap["source"], "env:SZK_VALIDATOR_HOME")
        self.assertEqual(os.path.normcase(cap["home"]), os.path.normcase(str(other)))

    def test_invalid_explicit_home_warns_and_falls_back(self):
        self.install("validator", "ok")
        cap = self.make_caps(env_extra={"SZK_VALIDATOR_HOME": str(self.tmp / "ures")}).get("validator")
        self.assertEqual(cap["state"], "ok")
        self.assertEqual(cap["source"], "MA_GUI_PLUGIN_DIRS")
        self.assertIn("configured_home_invalid", self.codes(cap, "warning"))

    def test_caps_json_home_python_and_timeout(self):
        other = self.install("presubmit", "ok", root=self.tmp / "cfg")
        self.runtime.mkdir()
        cfg = {"timeout": 9, "plugins": {"presubmit": {"home": str(other), "python": sys.executable}}}
        (self.runtime / "caps.json").write_text(json.dumps(cfg), encoding="utf-8")
        c = self.make_caps(env_extra={"MA_GUI_PLUGIN_DIRS": ""}, timeout=None)
        cap = c.get("presubmit")
        self.assertEqual(cap["state"], "ok")
        self.assertEqual(cap["source"], "caps.json")
        self.assertEqual(cap["python_source"], "caps.json")
        self.assertEqual(c._handshake_timeout(cfg), 9.0)

    def test_caps_json_disabled(self):
        self.install("validator", "ok")
        self.runtime.mkdir()
        (self.runtime / "caps.json").write_text(json.dumps({"plugins": {"validator": {"disabled": True}}}),
                                                encoding="utf-8")
        cap = self.make_caps().get("validator")
        self.assertEqual(cap["state"], "absent")
        self.assertIn("disabled", self.codes(cap))

    def test_caps_json_malformed_is_warning(self):
        self.install("validator", "ok")
        self.runtime.mkdir()
        (self.runtime / "caps.json").write_text("{ez nem json", encoding="utf-8")
        c = self.make_caps()
        cap = c.get("validator")
        self.assertEqual(cap["state"], "ok")
        self.assertIn("config_invalid", self.codes(cap, "warning"))
        self.assertIn("config_invalid", [p["code"] for p in c.report()["problems"]])

    def test_plugin_dirs_plugins_subfolder_and_config_dirs(self):
        self.install("composer", "ok", root=self.tmp / "repo" / "plugins")
        self.runtime.mkdir()
        (self.runtime / "caps.json").write_text(json.dumps({"plugin_dirs": [str(self.tmp / "repo")]}),
                                                encoding="utf-8")
        cap = self.make_caps(env_extra={"MA_GUI_PLUGIN_DIRS": ""}).get("composer")
        self.assertEqual(cap["state"], "ok")
        self.assertEqual(cap["source"], "caps.json:plugin_dirs")

    def test_git_checkout_layout(self):
        self.install("validator", "ok", root=self.home / "Documents" / "claude" / "szk-plugins" / "plugins")
        cap = self.make_caps(env_extra={"MA_GUI_PLUGIN_DIRS": ""}).get("validator")
        self.assertEqual((cap["state"], cap["source"]), ("ok", "git"))

    def test_marketplace_layout(self):
        root = self.home / ".claude" / "plugins" / "marketplaces" / "szk-plugins" / "plugins"
        self.install("figure-forge", "ok", root=root)
        cap = self.make_caps(env_extra={"MA_GUI_PLUGIN_DIRS": ""}).get("figure-forge")
        self.assertEqual((cap["state"], cap["source"]), ("ok", "marketplace"))

    def test_cache_layout_picks_latest_version(self):
        base_dir = self.home / ".claude" / "plugins" / "cache" / "szk-plugins" / "validator"
        for ver in ("1.9.0", "1.10.0", "0.9.9"):
            dest = self.install("validator", "legacy", root=base_dir, name=ver)
            (dest / ".claude-plugin" / "plugin.json").unlink()
        cap = self.make_caps(env_extra={"MA_GUI_PLUGIN_DIRS": ""}).get("validator")
        self.assertEqual(cap["source"], "cache")
        self.assertTrue(cap["home"].endswith("1.10.0"))
        self.assertEqual(cap["version"], "1.10.0")
        self.assertEqual(cap["state"], "legacy")

    def test_installed_plugins_json_fallback(self):
        dest = self.install("presubmit", "ok", root=self.tmp / "valahol")
        reg = self.home / ".claude" / "plugins"
        reg.mkdir(parents=True)
        doc = {"version": 2, "plugins": {"presubmit@szk-plugins": [{"scope": "user", "installPath": str(dest)}],
                                         "validator@masik": [{"installPath": str(self.tmp / "nincs")}]}}
        (reg / "installed_plugins.json").write_text(json.dumps(doc), encoding="utf-8")
        cap = self.make_caps(env_extra={"MA_GUI_PLUGIN_DIRS": ""}).get("presubmit")
        self.assertEqual((cap["state"], cap["source"]), ("ok", "installed_plugins.json"))

    def test_generic_marketplace_glob(self):
        self.install("presubmit", "ok", root=self.home / ".claude" / "plugins" / "marketplaces" / "mas-piac" / "plugins")
        cap = self.make_caps(env_extra={"MA_GUI_PLUGIN_DIRS": ""}).get("presubmit")
        self.assertEqual((cap["state"], cap["source"]), ("ok", "glob"))

    def test_skills_scripts_layout(self):
        dest = self.install("presubmit", "ok")
        target = dest / "skills" / "presubmit" / "scripts"
        target.mkdir(parents=True)
        shutil.move(str(dest / "scripts" / "pc.py"), str(target / "pc.py"))
        shutil.rmtree(str(dest / "scripts"))
        cap = self.make_caps().get("presubmit")
        self.assertEqual(cap["state"], "ok")
        self.assertIn("skills", cap["script"])

    def test_project_plugins_relative_home(self):
        project = self.tmp / "projekt"
        self.install("composer", "legacy", root=project / "eszkozok")
        c = self.make_caps(env_extra={"MA_GUI_PLUGIN_DIRS": ""},
                           project_plugins={"composer": {"home": "eszkozok/composer", "python": "/rossz/python"}},
                           project_root=project)
        cap = c.get("composer")
        self.assertEqual((cap["state"], cap["source"]), ("legacy", "ma-projekt.json"))
        # a projektfájl interpretert nem adhat meg
        self.assertEqual(cap["python"], [sys.executable])

    def test_unknown_plugin_name(self):
        with self.assertRaises(KeyError):
            self.make_caps().get("nincs-ilyen")

    def test_default_runtime_dir(self):
        env = {"MA_GUI_RUNTIME_DIR": str(self.tmp / "rt")}
        self.assertEqual(caps.default_runtime_dir(env, self.home), self.tmp / "rt")
        rt = caps.default_runtime_dir({"XDG_CACHE_HOME": str(self.tmp / "xdg"),
                                       "LOCALAPPDATA": str(self.tmp / "lad")}, self.home)
        if os.name == "nt":
            self.assertEqual(rt, self.tmp / "lad" / "ma-gui")
        elif sys.platform == "darwin":
            self.assertEqual(rt, self.home / "Library" / "Caches" / "ma-gui")
        else:
            self.assertEqual(rt, self.tmp / "xdg" / "ma-gui")


# ---------------------------------------------------------------- interpreter

class TestInterpreter(StubCase):

    def test_store_stub_filter(self):
        stub = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"
        self.assertTrue(caps.is_windows_store_stub(stub, nt=True))
        self.assertFalse(caps.is_windows_store_stub(r"C:\Python311\python.exe", nt=True))
        self.assertFalse(caps.is_windows_store_stub(stub, nt=False))

    def test_broken_override_falls_back_to_sys_executable(self):
        self.install("validator", "ok")
        bad = self.tmp / "nem-python.txt"
        bad.write_text("nem futtatható", encoding="utf-8")
        self.runtime.mkdir()
        (self.runtime / "caps.json").write_text(json.dumps({"plugins": {"validator": {"python": str(bad)}}}),
                                                encoding="utf-8")
        cap = self.make_caps().get("validator")
        self.assertEqual(cap["state"], "ok")
        self.assertEqual(cap["python"], [sys.executable])
        self.assertEqual(cap["interpreters_tried"], 2)

    def test_only_broken_interpreter_is_unusable(self):
        self.install("validator", "ok")
        bad = self.tmp / "nem-python.txt"
        bad.write_text("nem futtatható", encoding="utf-8")
        cap = self.make_caps(python=str(bad)).get("validator")
        self.assertEqual(cap["state"], "unusable")
        self.assertEqual(self.codes(cap, "error"), ["interpreter_failed"])

    def test_no_interpreter_at_all(self):
        self.install("validator", "ok")
        cap = self.make_caps(python=str(self.tmp / "nincs" / "python")).get("validator")
        self.assertEqual(cap["state"], "unusable")
        self.assertEqual(self.codes(cap, "error"), ["no_interpreter"])

    def test_legacy_figure_forge_without_required_modules_is_unusable(self):
        """8.3: figure-forge matplotlib nélkül unusable, érthető problems mezővel."""
        self.install("figure-forge", "legacy")
        specs = quiet_specs()
        specs["figure-forge"]["probe_imports"] = ("ma_gui_stub_matplotlib_nincs", "json")
        specs["figure-forge"]["optional_imports"] = (("ma_gui_stub_pptx_nincs", {"hu": "PPTX-export", "en": "PPTX"}),)
        cap = self.make_caps(specs=specs).get("figure-forge")
        self.assertEqual(cap["state"], "unusable")
        self.assertEqual(cap["missing_modules"], ["ma_gui_stub_matplotlib_nincs"])
        self.assertIn("FIGURE_FORGE_PYTHON", cap["todo"]["hu"])
        self.assertRegex(cap["python_version"] or "", r"^\d+\.\d+\.\d+$")

    def test_legacy_figure_forge_optional_module_note(self):
        self.install("figure-forge", "legacy")
        specs = quiet_specs()
        specs["figure-forge"]["probe_imports"] = ("json",)
        specs["figure-forge"]["optional_imports"] = (("ma_gui_stub_pptx_nincs", {"hu": "PPTX-export", "en": "PPTX"}),)
        cap = self.make_caps(specs=specs).get("figure-forge")
        self.assertEqual(cap["state"], "legacy")
        prob = [p for p in cap["problems"] if p["code"] == "optional_missing"][0]
        self.assertEqual(prob["level"], "info")
        self.assertIn("PPTX-export", prob["hu"])

    def test_handshake_overrides_import_probe(self):
        """ok-kézfogásnál a plugin saját leírása dönt (F1 után az audit matplotlib nélkül is fut)."""
        commands = [{"name": "audit", "argv": []},
                    {"name": "meta", "argv": [], "needs": ["matplotlib"], "available": False}]
        self.install("figure-forge", "ok", commands=commands)
        specs = quiet_specs()
        specs["figure-forge"]["probe_imports"] = ("ma_gui_stub_matplotlib_nincs",)
        c = self.make_caps(specs=specs)
        cap = c.get("figure-forge")
        self.assertEqual(cap["state"], "ok")
        matrix = c.capability_matrix()
        self.assertEqual(matrix["figure_audit"]["with_plugin"], "ok")
        self.assertEqual(matrix["pub_figure"]["with_plugin"], "unusable")
        self.assertIn("matplotlib", matrix["pub_figure"]["note"]["hu"])
        self.assertIn("figure_audit", cap["features"])
        self.assertNotIn("pub_figure", cap["features"])

    def _make_venv_with_module(self, venv_dir, module):
        try:
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv_dir)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return None
        py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not py.is_file():
            return None
        out = subprocess.run([str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        purelib = Path(out.stdout.decode().strip())
        purelib.mkdir(parents=True, exist_ok=True)
        (purelib / (module + ".py")).write_text("X = 1\n", encoding="utf-8")
        return py

    def test_plugin_venv_chosen_by_import_probe(self):
        """sys.executable az első jelölt, de a szükséges import csak a plugin .venv-jében van meg."""
        dest = self.install("figure-forge", "ok")
        module = "ma_gui_stub_ffdep_%d" % os.getpid()
        py = self._make_venv_with_module(dest / ".venv", module)
        if py is None:
            self.skipTest("venv nem hozható létre ezen a gépen")
        specs = quiet_specs()
        specs["figure-forge"]["probe_imports"] = (module,)
        cap = self.make_caps(specs=specs).get("figure-forge")
        self.assertEqual(cap["state"], "ok")
        self.assertEqual(cap["python_source"], "plugin .venv")
        self.assertEqual(os.path.normcase(cap["python"][0]), os.path.normcase(os.path.abspath(str(py))))
        # a stdlib-pluginnál a sys.executable marad az első
        cap_v = self.make_caps().get("figure-forge")
        self.assertEqual(cap_v["python_source"], "sys.executable")


# ---------------------------------------------------------------- hívás: argv, shell, környezet

class TestInvocation(StubCase):

    def test_handshake_is_argv_only_without_shell(self):
        self.install("composer", "ok")
        calls = []
        real_popen = subprocess.Popen

        def spy(argv, **kw):
            calls.append((argv, kw))
            return real_popen(argv, **kw)

        with mock.patch.dict(os.environ, {"PYTHONPATH": "/gonosz", "PYTHONHOME": "/gonosz"}), \
                mock.patch.object(base.subprocess, "Popen", side_effect=spy):
            cap = self.make_caps().get("composer")
        self.assertEqual(cap["state"], "ok")
        self.assertEqual(len(calls), 1)
        argv, kw = calls[0]
        self.assertIsInstance(argv, list)
        self.assertTrue(all(isinstance(a, str) for a in argv))
        self.assertEqual(argv[0], sys.executable)               # H7: a shebang megkerülve
        self.assertTrue(argv[1].endswith(os.path.join("scripts", "prisma")))
        self.assertEqual(argv[2:], ["--capabilities"])
        self.assertIs(kw["shell"], False)
        self.assertNotIn("PYTHONPATH", kw["env"])
        self.assertNotIn("PYTHONHOME", kw["env"])
        self.assertEqual(kw["env"]["PYTHONUTF8"], "1")
        self.assertEqual(kw["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(Path(kw["cwd"]), self.runtime / "tmp")
        if os.name == "nt":
            self.assertTrue(kw["creationflags"] & 0x08000000)
        else:
            self.assertTrue(kw["start_new_session"])

    def test_legacy_probe_runs_help(self):
        self.install("validator", "legacy")
        seen = []
        real_run = base._run

        def spy(argv, *a, **kw):
            seen.append(list(argv))
            return real_run(argv, *a, **kw)

        with mock.patch.object(base, "_run", side_effect=spy):
            cap = self.make_caps().get("validator")
        self.assertEqual(cap["state"], "legacy")
        self.assertEqual([a[-1] for a in seen], ["--capabilities", "--help"])

    def test_run_helper_rejects_non_list(self):
        with self.assertRaises(TypeError):
            base._run("python --version", 5)
        with self.assertRaises(TypeError):
            base._run([sys.executable, 3], 5)
        with self.assertRaises(ValueError):
            base._run([sys.executable, "a\x00b"], 5)
        with self.assertRaises(ValueError):
            base._run([sys.executable, "-c", "pass"], 0)


# ---------------------------------------------------------------- gyorsítótár

class TestCache(StubCase):

    def counting(self):
        counter = {"n": 0}
        real_run = base._run

        def spy(*a, **kw):
            counter["n"] += 1
            return real_run(*a, **kw)
        return counter, mock.patch.object(base, "_run", side_effect=spy)

    def test_get_is_cached_until_refresh(self):
        self.install("validator", "ok")
        c = self.make_caps()
        counter, patch = self.counting()
        with patch:
            c.get("validator")
            c.get("validator")
            c.capability_matrix()
            self.assertEqual(counter["n"], 1 + 0)               # a többi plugin absent: nincs alfolyamat
            c.refresh(["validator"])
            self.assertEqual(counter["n"], 2)

    def test_script_mtime_invalidates(self):
        dest = self.install("validator", "ok")
        c = self.make_caps()
        self.assertEqual(c.get("validator")["state"], "ok")
        (dest / "stub.json").write_text(json.dumps({"mode": "legacy"}), encoding="utf-8")
        self.assertEqual(c.get("validator")["state"], "ok")     # a stub.json nem része a kulcsnak
        script = dest / "scripts" / "appraise.py"
        st = script.stat()
        os.utime(str(script), ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10 ** 9))
        self.assertEqual(c.get("validator")["state"], "legacy")

    def test_caps_json_change_invalidates(self):
        self.install("validator", "ok")
        c = self.make_caps()
        self.assertEqual(c.get("validator")["state"], "ok")
        self.runtime.mkdir(exist_ok=True)
        (self.runtime / "caps.json").write_text(json.dumps({"plugins": {"validator": {"disabled": True}}}),
                                                encoding="utf-8")
        self.assertEqual(c.get("validator")["state"], "absent")

    def test_returns_copies(self):
        self.install("validator", "ok")
        c = self.make_caps()
        cap = c.get("validator")
        cap["state"] = "absent"
        cap["problems"].append({"code": "x"})
        again = c.get("validator")
        self.assertEqual(again["state"], "ok")
        self.assertNotIn("x", self.codes(again))

    def test_concurrent_get_probes_once(self):
        self.install("validator", "ok")
        c = self.make_caps()
        counter, patch = self.counting()
        results = []
        with patch:
            threads = [threading.Thread(target=lambda: results.append(c.get("validator")["state"]))
                       for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(results, ["ok"] * 6)
        self.assertEqual(counter["n"], 1)

    def test_background_refresh(self):
        for p in PLUGINS:
            self.install(p, "ok")
        c = self.make_caps()
        thread = c.start_refresh()
        self.assertIs(c.start_refresh(), thread)              # már fut: ugyanaz a szál
        self.assertTrue(c.wait(30))
        self.assertFalse(c.probing)
        counter, patch = self.counting()
        with patch:
            self.assertEqual({n: r["state"] for n, r in c.plugins().items()}, {p: "ok" for p in PLUGINS})
        self.assertEqual(counter["n"], 0)

    def test_module_level_default(self):
        self.install("presubmit", "ok")
        c = self.make_caps()
        old = caps._DEFAULT
        self.addCleanup(caps.set_default_caps, old)
        caps.set_default_caps(c)
        self.assertIs(caps.default_caps(), c)
        self.assertEqual(caps.get("presubmit")["state"], "ok")
        self.assertIn("manuscript", caps.capability_matrix())
        self.assertEqual(caps.refresh(["presubmit"])["presubmit"]["state"], "ok")
        self.assertEqual(caps.report()["schema"], caps.REPORT_SCHEMA)
        with self.assertRaises(TypeError):
            caps.set_default_caps(object())


# ---------------------------------------------------------------- mátrix és jelentés

class TestMatrix(StubCase):

    def test_shape_and_standalone_column(self):
        matrix = self.make_caps().capability_matrix()
        self.assertEqual(list(matrix), [f["id"] for f in caps.FEATURES])
        json.dumps(matrix)
        for fid, row in matrix.items():
            self.assertEqual(set(row), {"label", "standalone", "standalone_note", "plugin", "with_plugin",
                                        "note", "guards"}, fid)
            self.assertIsInstance(row["standalone"], bool)
            self.assertEqual(set(row["label"]), {"hu", "en"})
        for fid in ("table", "plots"):
            self.assertIsNone(matrix[fid]["plugin"])
            self.assertIsNone(matrix[fid]["with_plugin"])
        not_standalone = sorted(fid for fid, row in matrix.items() if not row["standalone"])
        self.assertEqual(not_standalone, ["manuscript", "prisma_figure", "tripod"])
        self.assertEqual(matrix["pub_figure"]["plugin"], "figure-forge")
        self.assertEqual(matrix["rob"]["plugin"], "validator")
        self.assertEqual(matrix["prisma"]["plugin"], "composer")
        self.assertEqual(matrix["manuscript"]["plugin"], "presubmit")
        self.assertEqual(matrix["tripod"]["with_plugin"], "absent")
        self.assertIn("claude plugin install validator@szk-plugins", matrix["tripod"]["note"]["hu"])

    def test_legacy_guards_reach_features(self):
        self.install("validator", "legacy")
        self.install("figure-forge", "legacy")
        self.install("composer", "legacy")
        matrix = self.make_caps().capability_matrix()
        self.assertEqual(matrix["tripod"]["guards"], ["H1"])
        self.assertEqual(matrix["probast"]["guards"], ["H2"])
        self.assertEqual(matrix["grade"]["guards"], ["H3"])
        self.assertIn("feloldatlan", matrix["grade"]["note"]["hu"])
        self.assertEqual(matrix["amstar2"]["guards"], ["H4"])
        self.assertEqual(matrix["rob"]["guards"], [])
        self.assertEqual(matrix["pub_figure"]["guards"], ["H6"])
        self.assertIn("motor-SVG", matrix["pub_figure"]["note"]["hu"])
        self.assertEqual(matrix["figure_audit"]["guards"], ["H5"])
        self.assertEqual(matrix["prisma"]["guards"], ["H7"])

    def test_known_issues_drive_guards_when_ok(self):
        issues = [{"id": "H3", "summary": "publication bias suspected unresolved", "fixed_in": "1.2.0"},
                  {"id": "H1", "summary": "tripod verify", "fixed_in": "1.0.1"},
                  {"id": "X9", "summary": "egyéb"}]
        self.install("validator", "ok", version="1.1.0", known_issues=issues)
        c = self.make_caps()
        cap = c.get("validator")
        self.assertEqual(cap["version"], "1.1.0")
        self.assertEqual(cap["guards"], ["H3", "X9"])
        self.assertEqual(len(cap["known_issues"]), 3)
        matrix = c.capability_matrix()
        self.assertEqual(matrix["grade"]["guards"], ["H3"])
        self.assertEqual(matrix["tripod"]["guards"], [])
        self.assertEqual(matrix["grade"]["with_plugin"], "ok")

    def test_undeclared_command_is_legacy(self):
        self.install("figure-forge", "ok", commands=[{"name": "audit", "argv": []}])
        matrix = self.make_caps().capability_matrix()
        self.assertEqual(matrix["figure_audit"]["with_plugin"], "ok")
        self.assertEqual(matrix["pub_figure"]["with_plugin"], "legacy")
        self.assertIn("nem deklarál", matrix["pub_figure"]["note"]["hu"])

    def test_report_components(self):
        self.install("validator", "legacy")
        self.install("figure-forge", "unusable")
        self.install("composer", "ok")
        rep = self.make_caps().report()
        json.dumps(rep)
        self.assertEqual(rep["schema"], "szk.ma.capabilities-matrix/v1")
        self.assertRegex(rep["generated"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertFalse(rep["probing"])
        ids = [c["id"] for c in rep["components"]]
        self.assertEqual(ids, ["metaelemzes", "figure-forge", "validator", "composer", "presubmit"])
        states = {c["id"]: c["state"] for c in rep["components"]}
        self.assertEqual(states, {"metaelemzes": "ok", "figure-forge": "unusable", "validator": "legacy",
                                  "composer": "ok", "presubmit": "absent"})
        for comp in rep["components"]:
            for key in ("id", "label", "version", "state", "mode", "problems", "features", "contracts", "guards"):
                self.assertIn(key, comp)
            for prob in comp["problems"]:
                self.assertEqual({"code", "level", "hu", "en"}, set(prob))
        engine = rep["components"][0]
        self.assertEqual(engine["label"], {"hu": "motor", "en": "engine"})
        self.assertEqual(set(engine["features"]), {"analyze", "validate", "convert", "prisma", "project", "kb"})
        self.assertIn("matrix", rep)


# ---------------------------------------------------------------- motor és séma

class TestEngineCapabilities(unittest.TestCase):

    def test_shape_and_values(self):
        import metaelemzes
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("alfolyamat tilos")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("alfolyamat tilos")):
            doc = caps.engine_capabilities()
            comp = caps.engine_component()
        self.assertEqual(caps.validate_capabilities_doc(doc, plugin="metaelemzes"), [])
        self.assertEqual(doc["schema"], "szk.capabilities/v1")
        self.assertEqual(doc["plugin"], "metaelemzes")
        self.assertEqual(doc["version"], metaelemzes.__version__)
        self.assertIs(doc["ok"], True)
        self.assertEqual([c["name"] for c in doc["commands"]],
                         ["analyze", "validate", "convert", "prisma", "project", "kb"])
        for cmd in doc["commands"]:
            self.assertEqual(cmd["argv"][0], "ma.py")
            self.assertIs(cmd["available"], True)
        self.assertEqual(doc["requires"]["missing"], [])
        self.assertEqual(comp["state"], "ok")
        self.assertIn(comp["mode"], ("api", "cli"))
        json.dumps(doc)
        self.assertEqual(caps.engine_capabilities(), doc)       # determinisztikus

    def test_validate_doc_negatives(self):
        good = caps.engine_capabilities()
        self.assertEqual(caps.validate_capabilities_doc(good), [])
        cases = {
            "nem objektum": [],
            "hiányzó mező": {k: v for k, v in good.items() if k != "commands"},
            "schema": dict(good, schema="szk.capabilities/v2"),
            "version": dict(good, version="egy"),
            "ok": dict(good, ok="igen"),
            "contracts[x].sha256": dict(good, contracts={"x": {"dir": ["in"], "sha256": "ABC"}}),
            "contracts[x].dir": dict(good, contracts={"x": {"dir": ["fel"], "sha256": "a" * 64}}),
            "commands": dict(good, commands={"name": "x"}),
            "commands[0].argv": dict(good, commands=[{"name": "x", "argv": "ma.py x"}]),
            "commands[0].available": dict(good, commands=[{"name": "x", "argv": [], "available": "igen"}]),
            "requires.missing": dict(good, requires={"missing": "matplotlib"}),
            "known_issues[0]": dict(good, known_issues=[{"id": "H1"}]),
        }
        for needle, doc in cases.items():
            errs = caps.validate_capabilities_doc(doc)
            self.assertTrue(errs, needle)
            self.assertTrue(any(needle in e for e in errs), (needle, errs))
        self.assertTrue(caps.validate_capabilities_doc(good, plugin="validator"))


# ---------------------------------------------------------------- Adapter.run

class StubValidator(base.Adapter):
    plugin = "validator"
    default_timeout = 20.0


class TestAdapterRun(StubCase):

    def setUp(self):
        super().setUp()
        self.install("validator", "ok")
        self.caps = self.make_caps()
        self.adapter = StubValidator(self.caps)

    def test_detect(self):
        cap = self.adapter.detect()
        self.assertIsInstance(cap, base.Capability)
        self.assertEqual(cap.state, "ok")
        self.assertTrue(self.adapter.available())
        self.assertTrue(self.adapter.command_available("appraise.verify"))
        self.assertFalse(self.adapter.command_available("nincs"))

    def test_json_roundtrip_with_stdin(self):
        payload = {"szöveg": "árvíztűrő tükörfúrógép", "n": [1, 2.5, None]}
        res = self.adapter.run(["echo", "--x", "1"], stdin_json=payload)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["data"], {"echo": payload, "args": ["--x", "1"]})
        self.assertEqual(res["returncode"], 0)

    def test_argv_values_are_literal(self):
        evil = ["$(touch PWNED)", "a;b|c&d", "`id`", "'\"", "%PATH%", "* ?"]
        res = self.adapter.run(["argv"] + evil)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["data"]["args"], evil)
        self.assertFalse((self.runtime / "tmp" / "PWNED").exists())
        self.assertFalse(Path("PWNED").exists())

    def test_script_selection(self):
        res = self.adapter.run(["argv"], script="checklist.py")
        self.assertEqual(res["data"]["script"], "checklist.py")
        with self.assertRaises(ValueError):
            self.adapter.run(["argv"], script="../../evil.py")

    def test_nonzero_exit_is_plugin_failed_with_stderr_tail(self):
        res = self.adapter.run(["fail", "5000", "3"])
        self.assertFalse(res["ok"])
        err = res["error"]
        self.assertEqual((err["code"], err["http"]), ("PLUGIN_FAILED", 502))
        tail = err["details"]["stderr_tail"]
        self.assertLessEqual(len(tail.encode("utf-8")), 2048)
        self.assertTrue(tail.rstrip().endswith("VEGE-MARKER"))
        self.assertEqual(err["details"]["returncode"], 3)
        self.assertIn("validator", err["message"])

    def test_accepted_returncodes(self):
        class Lenient(StubValidator):
            accepted_returncodes = (0, 3)
        res = Lenient(self.caps).run(["rc", "3"])
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["data"], res["returncode"]), ({"rc": 3}, 3))

    def test_timeout(self):
        t0 = time.monotonic()
        res = self.adapter.run(["sleep", "30"], timeout=1)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertEqual((res["error"]["code"], res["error"]["http"]), ("TIMEOUT", 504))
        self.assertEqual(res["error"]["details"]["timeout_s"], 1.0)

    @unittest.skipIf(os.name == "nt", "POSIX folyamatcsoport-teszt")
    def test_timeout_kills_process_tree(self):
        pidfile = self.tmp / "unoka.pid"
        res = self.adapter.run(["spawn", str(pidfile)], timeout=2)
        self.assertEqual(res["error"]["code"], "TIMEOUT")
        pid = int(pidfile.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        alive = True
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                alive = False
                break
            time.sleep(0.05)
        self.assertFalse(alive, "az unoka-folyamat életben maradt")

    def test_unparsable_output(self):
        res = self.adapter.run(["garbage"])
        self.assertEqual(res["error"]["code"], "PLUGIN_FAILED")
        self.assertIn("JSON", res["error"]["message"])

    def test_output_cap(self):
        class Small(StubValidator):
            max_stdout = 1000
        res = Small(self.caps).run(["big", "500000"])
        self.assertEqual(res["error"]["code"], "PLUGIN_FAILED")
        self.assertIn("túl nagy", res["error"]["message"])

    def test_text_mode(self):
        res = self.adapter.run(["text"], parse="text")
        self.assertTrue(res["ok"])
        self.assertIn("árvíztűrő", res["data"])
        with self.assertRaises(ValueError):
            self.adapter.run(["text"], parse="xml")

    def test_env_is_clean_and_cwd_is_runtime_tmp(self):
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/gonosz", "PYTHONHOME": "/gonosz2",
                                          "PYTHONIOENCODING": "cp1250"}):
            res = self.adapter.run(["env"])
        self.assertTrue(res["ok"], res)
        env = res["data"]["env"]
        self.assertIsNone(env["PYTHONPATH"])
        self.assertIsNone(env["PYTHONHOME"])
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(os.path.realpath(res["data"]["cwd"]), os.path.realpath(str(self.runtime / "tmp")))

    def test_capability_missing(self):
        class Absent(base.Adapter):
            plugin = "presubmit"
        res = Absent(self.caps).run(["check"])
        self.assertEqual((res["error"]["code"], res["error"]["http"]), ("CAPABILITY_MISSING", 424))
        self.assertEqual(res["error"]["details"]["state"], "absent")
        self.assertIn("claude plugin install", res["error"]["details"]["todo"]["hu"])

    def test_bad_inputs(self):
        with self.assertRaises(TypeError):
            self.adapter.run("echo; rm -rf /")
        with self.assertRaises(TypeError):
            self.adapter.run(["echo", 5])
        with self.assertRaises(ValueError):
            self.adapter.run(["echo", "a\x00"])
        with self.assertRaises(ValueError):
            self.adapter.run(["echo"], stdin_json={"x": float("nan")})
        with self.assertRaises(ValueError):
            self.adapter.run(["echo"], timeout=-1)
        with self.assertRaises(NotImplementedError):
            base.Adapter(self.caps).detect()

    def test_build_argv(self):
        argv = self.adapter.build_argv(["--verify", "x.md"])
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("appraise.py"))
        self.assertEqual(argv[2:], ["--verify", "x.md"])


# ---------------------------------------------------------------- opcióértékek

class TestOptionValues(unittest.TestCase):

    def test_accepts(self):
        v = base.validate_option_value
        self.assertEqual(v("rob2", enum=["rob2", "robins-i"]), "rob2")
        self.assertEqual(v("LEE2023", regex=r"[A-Za-z0-9_\-]+"), "LEE2023")
        self.assertEqual(v(5), "5")
        self.assertEqual(v("-0.5"), "-0.5")
        self.assertEqual(v("árvíztűrő tükör"), "árvíztűrő tükör")
        self.assertEqual(base.option("--tool", "rob2", enum=("rob2",)), ["--tool", "rob2"])

    def test_rejects_without_echo(self):
        v = base.validate_option_value
        secret = "TITKOS-ERTEK-123"
        bad = [
            (dict(value="--evil"), "kötőjel"),
            (dict(value="-x"), "kötőjel"),
            (dict(value=secret + "\n"), "vezérlő"),
            (dict(value=secret + "\x00"), "vezérlő"),
            (dict(value=""), "üres"),
            (dict(value=True), "szöveg"),
            (dict(value=1.5), "szöveg"),
            (dict(value=None), "szöveg"),
            (dict(value=secret, enum=["a", "b"]), "megengedett"),
            (dict(value=secret, regex=r"[a-z]+"), "formátum"),
            (dict(value="a" * 2000), "hosszú"),
        ]
        for kwargs, needle in bad:
            with self.assertRaises(ValueError) as ctx:
                v(**kwargs)
            self.assertIn(needle, str(ctx.exception))
            self.assertNotIn(secret, str(ctx.exception))
        with self.assertRaises(ValueError):
            base.option("tool; rm", "x")
        self.assertEqual(v("", allow_empty=True), "")


# ---------------------------------------------------------------- stdlib-only, statisztika-tilalom

class TestModuleHygiene(unittest.TestCase):

    def imports(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [(a.name.split(".")[0], 0) for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(((node.module or "").split(".")[0], node.level))
        return tree, names

    def test_no_statistics_no_third_party_no_print(self):
        stdlib = getattr(sys, "stdlib_module_names", None)
        for path in MODULE_FILES:
            tree, names = self.imports(path)
            for name, level in names:
                self.assertNotIn(name, BANNED, path)
                if level == 0 and stdlib is not None:
                    self.assertTrue(name in stdlib or name in ("ma_gui", "metaelemzes"), (path.name, name))
            self.assertNotIn("logging", [n for n, _ in names], path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotEqual(node.func.id, "print", path)
                    self.assertNotEqual(node.func.id, "eval", path)
                    self.assertNotEqual(node.func.id, "exec", path)
                if isinstance(node, ast.keyword) and node.arg == "shell":
                    self.assertIs(getattr(node.value, "value", None), False, path)

    def test_no_os_system_or_fork(self):
        for path in MODULE_FILES:
            text = path.read_text(encoding="utf-8")
            for needle in ("os.system", "os.popen", "os.fork", "shell=True"):
                self.assertNotIn(needle, text, (path.name, needle))

    def test_stub_plugins_are_stdlib(self):
        for path in sorted(STUBS.rglob("*")):
            if path.is_file() and (path.suffix == ".py" or path.name == "prisma"):
                _tree, names = self.imports(path)
                for name, level in names:
                    self.assertNotIn(name, BANNED, path)


if __name__ == "__main__":
    unittest.main()
