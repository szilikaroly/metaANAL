# -*- coding: utf-8 -*-
"""Stub-pluginok közös magja a caps- és adapter-tesztekhez (terv 8.3: stub-pluginok).

Minden stub-szkript (``<plugin>/scripts/*``) ugyanaz a rövid alátét: megkeresi felfelé ezt a
fájlt, és a ``main(__file__)``-t hívja. A plugin nevét és verzióját a ``.claude-plugin/plugin.json``,
a viselkedést a plugin-mappa opcionális ``stub.json``-ja adja (a tesztek írják egy ideiglenes
másolatba):

    {"mode": "ok", "version": "1.1.0", "commands": [...], "known_issues": [...],
     "contracts": {...}, "missing": ["matplotlib"], "module": "x", "sleep": 60}

Módok (``--capabilities``-re):
  ok         érvényes szk.capabilities/v1 (a contracts/ mappa sémáinak sha256-jával)
  legacy     argparse-szerű hiba (kód 2) — nincs kapcsoló; a --help működik
  unusable   kézfogás ok=false, requires.missing = stub.json "missing"
  notok      kézfogás ok=false, hiányzó modul nélkül
  badshape   szk.capabilities/v1, de hiányos mezőkkel
  v2         ismeretlen főverzió (szk.capabilities/v2)
  wrongname  más plugin nevével
  missingmod modulszintű import egy nem létező modulra (mint a H5) — minden hívás elhasal
  crash      RuntimeError minden hívásra
  garbage    nem JSON kimenet, kód 0
  slow       alszik (az időkorlát-teszthez)

ok módban futásidejű parancsok is vannak (az Adapter.run tesztjeihez): echo, argv, env, fail,
sleep, garbage, big, rc, text, spawn.
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

CAPS = "szk.capabilities/v1"

DEFAULT_COMMANDS = {
    "validator": [
        {"name": "appraise.schema", "argv": ["scripts/appraise.py", "--schema"], "out": ["szk.instrument/v1"]},
        {"name": "appraise.verify", "argv": ["scripts/appraise.py", "--verify"],
         "in": ["szk.appraisal/v1"], "out": ["szk.appraisal-result/v1"]},
        {"name": "appraise.rollup", "argv": ["scripts/appraise.py", "--rollup"],
         "in": ["szk.appraisal/v1"], "out": ["szk.appraisal-result/v1"]},
        {"name": "checklist.schema", "argv": ["scripts/checklist.py", "--schema"], "out": ["szk.instrument/v1"]},
        {"name": "checklist.verify", "argv": ["scripts/checklist.py", "--verify"],
         "in": ["szk.appraisal/v1"], "out": ["szk.appraisal-result/v1"]},
    ],
    "figure-forge": [
        {"name": "audit", "argv": ["scripts/ff.py", "audit"], "out": ["szk.figure-result/v1"]},
        {"name": "meta", "argv": ["scripts/ff.py", "meta"], "in": ["szk.figure-request/v1"],
         "out": ["szk.figure-result/v1"], "needs": ["matplotlib"]},
        {"name": "flowchart", "argv": ["scripts/ff.py", "flowchart"], "in": ["szk.ff.flowchart/v1"],
         "needs": ["matplotlib"]},
    ],
    "composer": [
        {"name": "prisma.export", "argv": ["scripts/prisma", "export", "--format", "flow-json"],
         "out": ["szk.prisma-flow/v1"]},
        {"name": "prisma.status", "argv": ["scripts/prisma", "status"]},
    ],
    "presubmit": [
        {"name": "check", "argv": ["scripts/pc.py", "check"], "in": ["szk.facts/v1"]},
    ],
}


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _contracts(plugin_dir):
    out = {}
    for path in sorted((plugin_dir / "contracts").glob("*.schema.json")):
        data = path.read_bytes()
        key = json.loads(data.decode("utf-8"))["properties"]["schema"]["const"]
        out[key] = {"dir": ["out"], "sha256": hashlib.sha256(data).hexdigest()}
    return out


def build_doc(name, version, plugin_dir, cfg):
    contracts = _contracts(plugin_dir)
    contracts.update(cfg.get("contracts") or {})
    return {
        "schema": CAPS,
        "plugin": name,
        "version": version,
        "python": platform.python_version(),
        "ok": True,
        "contracts": contracts,
        "commands": cfg.get("commands") or DEFAULT_COMMANDS.get(name, []),
        "requires": {"modules": [], "missing": []},
        "known_issues": cfg.get("known_issues") or [],
    }


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_command(args, script):
    cmd = args[0] if args else ""
    rest = args[1:]
    if cmd == "echo":
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        _emit({"echo": json.loads(raw) if raw.strip() else None, "args": rest})
        return 0
    if cmd == "argv":
        _emit({"script": os.path.basename(script), "args": rest})
        return 0
    if cmd == "env":
        keys = ("PYTHONPATH", "PYTHONHOME", "PYTHONUTF8", "PYTHONIOENCODING")
        _emit({"env": {k: os.environ.get(k) for k in keys}, "cwd": os.getcwd()})
        return 0
    if cmd == "fail":
        size = int(rest[0]) if rest else 100
        sys.stderr.write("x" * size + "\nVEGE-MARKER\n")
        sys.stderr.flush()
        return int(rest[1]) if len(rest) > 1 else 3
    if cmd == "sleep":
        time.sleep(float(rest[0]) if rest else 60)
        return 0
    if cmd == "garbage":
        sys.stdout.write("ez nem JSON <<<\n")
        return 0
    if cmd == "big":
        sys.stdout.write("x" * int(rest[0]))
        sys.stdout.flush()
        return 0
    if cmd == "rc":
        code = int(rest[0])
        _emit({"rc": code})
        return code
    if cmd == "text":
        sys.stdout.write("1. sor\n2. sor: árvíztűrő\n")
        return 0
    if cmd == "spawn":
        # unoka-folyamat, amely örökli a stdout-csövet: a folyamatfa-leállítás tesztjéhez
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        Path(rest[0]).write_text(str(child.pid), encoding="utf-8")
        time.sleep(60)
        return 0
    sys.stderr.write("stub: ismeretlen parancs\n")
    return 2


def main(script_file):
    script = Path(script_file).resolve()
    plugin_dir = script.parent.parent if script.parent.name == "scripts" else script.parent
    manifest = _read_json(plugin_dir / ".claude-plugin" / "plugin.json", {})
    cfg = _read_json(plugin_dir / "stub.json", {})
    name = manifest.get("name", plugin_dir.name)
    version = cfg.get("version") or manifest.get("version") or "0.0.0"
    mode = cfg.get("mode", "ok")
    args = sys.argv[1:]

    if mode == "missingmod":
        __import__(cfg.get("module", "ma_gui_stub_hianyzo_modul"))
    if mode == "crash":
        raise RuntimeError("stub: szándékos összeomlás")
    if mode == "slow":
        time.sleep(float(cfg.get("sleep", 60)))
        return 0
    if mode == "garbage":
        sys.stdout.write("ez nem JSON <<<\n")
        return 0

    if "--capabilities" in args:
        if mode == "legacy":
            sys.stderr.write("usage: %s [-h] {run}\n%s: error: unrecognized arguments: --capabilities\n"
                             % (script.name, script.name))
            return 2
        if mode == "badshape":
            _emit({"schema": CAPS, "plugin": name})
            return 0
        doc = build_doc(name, version, plugin_dir, cfg)
        if mode == "v2":
            doc["schema"] = "szk.capabilities/v2"
        elif mode == "wrongname":
            doc["plugin"] = "masik-plugin"
        elif mode == "unusable":
            missing = cfg.get("missing") or ["matplotlib"]
            doc["ok"] = False
            doc["requires"] = {"modules": list(missing), "missing": list(missing)}
        elif mode == "notok":
            doc["ok"] = False
        _emit(doc)
        return 0
    if "--help" in args or "-h" in args:
        sys.stdout.write("usage: %s [-h] ...\n" % script.name)
        return 0
    if mode != "ok":
        sys.stderr.write("stub: ebben a módban nincs futásidejű parancs\n")
        return 2
    return run_command(args, str(script))
