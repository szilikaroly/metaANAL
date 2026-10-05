# -*- coding: utf-8 -*-
"""Plugin-képességek: felderítés, interpreter-választás, kézfogás és képesség-mátrix
(terv: 3.1 caps.py, 4.1, 4.20, 5.0–5.2, 7.3).

Pluginok és szkriptjeik: figure-forge (``ff.py``), validator (``appraise.py``, ``checklist.py``),
composer (``scripts/prisma``), presubmit (``pc.py``). Állapot pluginonként (4.1):
absent | unusable | legacy | ok.

- Felderítés (5.1) sorrendje: ``SZK_<PLUGIN>_HOME`` → caps.json ``plugins.<név>.home`` (a futásidejű
  mappában) → ma-projekt.json ``plugins.<név>`` → ``MA_GUI_PLUGIN_DIRS`` (os.pathsep-pel elválasztva;
  ``D/<név>`` vagy ``D/plugins/<név>``) és caps.json ``plugin_dirs`` → ``~/Documents/claude/szk-plugins/
  plugins/<név>`` → ``~/.claude/plugins/marketplaces/szk-plugins/plugins/<név>`` → ``~/.claude/plugins/
  cache/szk-plugins/<név>/<legújabb verzió>`` → ``installed_plugins.json`` (tartalék) → egyéb
  ``~/.claude/plugins/**/plugins/<név>`` (legfeljebb 3 szint mélyen). A szkriptet a plugin-mappa
  ``scripts/``, gyökér, ``bin/`` vagy ``skills/*/scripts/`` alatt keressük.
- Interpreter: caps.json ``python`` → (figure-forge: ``FIGURE_FORGE_PYTHON``, ``SZK_PYTHON``) →
  ``sys.executable`` → a plugin saját ``.venv``-je → (figure-forge: conda, ``~/.claude/.venv``, PATH).
  A figure-forge jelöltjeit a ténylegesen szükséges importtal szondázzuk; a Windows Store-csonk
  kiesik. A composer szkriptjeit is explicit interpreterrel hívjuk (H7: abszolút shebang).
- Kézfogás: ``<python> <szkript> --capabilities`` (10 s, argv-lista, shell=False, szűrt környezet).
  Ha a kapcsoló hiányzik (nem nulla kód vagy parszolhatatlan kimenet): ``--help``-szonda és
  plugin.json-verzió → legacy, a beépített H-őrökkel (5.0); újabb pluginnál a ``known_issues``
  alapján. Hiányzó modul (ModuleNotFoundError; vagy a kézfogásban ok=false / requires.missing)
  → unusable, magyar teendővel. Időtúllépés → unusable (Újraszondázás).
- Sodródás-őr (4.1, 4.20): a deklarált szerződés-sha256 ↔ a munkapad saját másolata
  (``ma_gui/contracts/``, ``metaelemzes/contracts/``). Eltérés → az érintett funkció legacy,
  „szerződés-eltérés”; ha nincs helyi másolat, „unknown” (nem eltérés).
- Gyorsítótár memóriában; a szkript, a plugin.json és a caps.json mtime-ja érvényteleníti,
  ``refresh()`` újraszondáz (párhuzamosan, pluginonként egy szálon; ``start_refresh()`` háttérben).

Statisztikát nem számol; táblát, cellaértéket nem lát és nem naplóz (T10).
"""
import copy
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from .adapters import base as _base
from .adapters.base import Capability

ROOT = Path(__file__).resolve().parent.parent            # a motor repója
ENGINE_CONTRACTS_DIR = ROOT / "metaelemzes" / "contracts"
GUI_CONTRACTS_DIR = ROOT / "ma_gui" / "contracts"
DEFAULT_CONTRACT_DIRS = (GUI_CONTRACTS_DIR, ENGINE_CONTRACTS_DIR)

CAPS_SCHEMA = "szk.capabilities/v1"
REPORT_SCHEMA = "szk.ma.capabilities-matrix/v1"
MARKETPLACE = "szk-plugins"
PLUGIN_ORDER = ("figure-forge", "validator", "composer", "presubmit")
STATES = _base.STATES

HANDSHAKE_TIMEOUT = 10.0
IMPORT_PROBE_TIMEOUT = 20.0
MAX_HANDSHAKE_OUT = 1024 * 1024
MAX_INTERPRETER_TRIES = 3
MAX_PROBE_CANDIDATES = 5
MAX_CONFIG_BYTES = 256 * 1024

PLUGINS = {
    "figure-forge": {
        "label": "figure-forge",
        "scripts": ("ff.py",),
        "probe_imports": ("matplotlib", "numpy", "pandas"),
        "optional_imports": (("pptx", {"hu": "PPTX-export", "en": "PPTX export"}),
                             ("PIL", {"hu": "TIFF-export", "en": "TIFF export"})),
        "python_env": ("FIGURE_FORGE_PYTHON", "SZK_PYTHON"),
        "extended_python": True,
        "python_hint": "FIGURE_FORGE_PYTHON",
    },
    "validator": {
        "label": "validator",
        "scripts": ("appraise.py", "checklist.py"),
        "probe_imports": (),
        "optional_imports": (),
        "python_env": (),
        "extended_python": False,
        "python_hint": None,
    },
    "composer": {
        "label": "composer",
        "scripts": ("prisma",),
        "probe_imports": (),
        "optional_imports": (),
        "python_env": (),
        "extended_python": False,
        "python_hint": None,
    },
    "presubmit": {
        "label": "presubmit",
        "scripts": ("pc.py",),
        "probe_imports": (),
        "optional_imports": (),
        "python_env": (),
        "extended_python": False,
        "python_hint": None,
    },
}

# import-név → pip-csomagnév a teendő szövegéhez
PIP_NAMES = {
    "PIL": "Pillow", "pptx": "python-pptx", "docx": "python-docx", "fitz": "PyMuPDF",
    "Bio": "biopython", "yaml": "PyYAML", "sklearn": "scikit-learn", "cv2": "opencv-python",
}

# 5.0: a tervezés közben igazolt hibák → legacy módban bekapcsolt őrök (fixed_in: None = nincs kiadott javítás)
BUILTIN_ISSUES = {
    "validator": (
        {"id": "H1", "fixed_in": None, "features": ("tripod",),
         "summary": "checklist.py --verify reports an empty TRIPOD+AI template as 1/52 answered",
         "guard": {"hu": "H1: a teljességet a munkapad számolja",
                   "en": "H1: completeness is computed by the workbench"}},
        {"id": "H2", "fixed_in": None, "features": ("probast",),
         "summary": "checklist.py --verify (PROBAST+AI): the two passes satisfy each other; "
                    "'no' in evidence text counts as an answer",
         "guard": {"hu": "H2: menettel minősített kulcs, cella-alapú olvasás",
                   "en": "H2: pass-qualified keys, cell-based reading"}},
        {"id": "H3", "fixed_in": None, "features": ("grade",),
         "summary": "appraise.py rollup_grade never downgrades publication bias 'Suspected/Strongly suspected'",
         "guard": {"hu": "H3: a „Suspected” publikációs torzítás feloldatlan, amíg ember nem dönt",
                   "en": "H3: publication bias 'Suspected' stays unresolved until a human decides"}},
        {"id": "H4", "fixed_in": None, "features": ("amstar2",),
         "summary": "appraise.py _norm reads 'py' as 'probably yes'; 'Partial yes' on a critical "
                    "AMSTAR 2 item becomes a non-critical weakness",
         "guard": {"hu": "H4: kanonikus partial_yes, a besorolás „weakness” konvencióként címkézve",
                   "en": "H4: canonical partial_yes, rating labelled as the 'weakness' convention"}},
    ),
    "figure-forge": (
        {"id": "H5", "fixed_in": None, "features": ("figure_audit",),
         "summary": "ff.py imports ff_style (matplotlib) at module level, so audit needs matplotlib too",
         "guard": {"hu": "H5: matplotlib nélkül az ábra-audit a motor-SVG stdlib-ellenőrzésével fut",
                   "en": "H5: without matplotlib the figure audit uses the stdlib check of the engine SVG"}},
        {"id": "H6", "fixed_in": None, "features": ("pub_figure",),
         "summary": "the forest subcommand draws no meta-analytic forest (no diamond, PI, subgroups); no funnel/Doi",
         "guard": {"hu": "H6: a meta-ábrák exportja az F2-ig a motor-SVG",
                   "en": "H6: meta-analytic figures are exported from the engine SVG until F2"}},
    ),
    "composer": (
        {"id": "H7", "fixed_in": None, "features": ("prisma",),
         "summary": "absolute shebang; non-atomic save_state; the 'Studies included' box shows the number of reports",
         "guard": {"hu": "H7: explicit interpreterrel hívva, csak olvasunk, csonka JSON-nál újrapróbálunk",
                   "en": "H7: called with an explicit interpreter, read-only, retried on truncated JSON"}},
    ),
    "presubmit": (),
}

# a bridge-adapterek ezekhez a verziókhoz készültek (4.19)
BRIDGE_TESTED = {"validator": "1.0.0", "figure-forge": "0.2.1", "composer": "1.4.1"}


def _i18n(hu, en):
    return {"hu": hu, "en": en}


# 5.2 képesség-mátrix: funkció × önálló × pluginnal. A "keywords" a kézfogás parancsneveire illeszt.
FEATURES = (
    {"id": "table",
     "label": _i18n("Tábla, élő V-validáció, átváltó, eredet, kettős kinyerés",
                    "Table, live V-validation, converter, provenance, dual extraction"),
     "standalone": True, "standalone_note": _i18n("teljes", "full"),
     "plugin": None, "keywords": (), "plugin_note": None},
    {"id": "plots",
     "label": _i18n("Forest / funnel / Doi / LOO / befolyás / kumulatív / buborék, lefúrás",
                    "Forest / funnel / Doi / LOO / influence / cumulative / bubble, drill-down"),
     "standalone": True, "standalone_note": _i18n("teljes (interaktív SVG)", "full (interactive SVG)"),
     "plugin": None, "keywords": (), "plugin_note": None},
    {"id": "pub_figure",
     "label": _i18n("Publikációs ábra", "Publication figure"),
     "standalone": True,
     "standalone_note": _i18n("motor-SVG (EN felirat, rétegek, U+2212) + böngészős PNG "
                              "„ELŐNÉZET — QC NÉLKÜL” vízjellel",
                              "engine SVG (EN labels, layers, U+2212) + browser PNG with a "
                              "'PREVIEW — NO QC' watermark"),
     "plugin": "figure-forge", "keywords": ("meta",),
     "plugin_note": _i18n("SVG/PDF/TIFF/PNG/PPTX 600 dpi, címke-QC, szerkeszthetőség, tipográfia, numbers",
                          "SVG/PDF/TIFF/PNG/PPTX at 600 dpi, label QC, editability, typography, numbers")},
    {"id": "figure_audit",
     "label": _i18n("Ábra-audit", "Figure audit"),
     "standalone": True,
     "standalone_note": _i18n("stdlib SVG-ellenőrzés a motor-SVG-n", "stdlib SVG check of the engine SVG"),
     "plugin": "figure-forge", "keywords": ("audit",),
     "plugin_note": _i18n("ff.py audit (F1 után matplotlib nélkül is)", "ff.py audit (without matplotlib after F1)")},
    {"id": "rob",
     "label": _i18n("RoB 2 / ROBINS-I/E / QUADAS-2 / NOS / QUIPS / JBI", "RoB 2 / ROBINS-I/E / QUADAS-2 / NOS / QUIPS / JBI"),
     "standalone": True,
     "standalone_note": _i18n("doménszintű ítélettábla, forgalmi lámpa, a rob oszlop karbantartása",
                              "domain-level judgement table, traffic light, upkeep of the rob column"),
     "plugin": "validator",
     "keywords": ("appraise", "rob", "rob2", "robins", "quadas", "nos", "quips", "jbi"),
     "plugin_note": _i18n("jelző-kérdéses űrlapok, teljesség, implikált ítélet, NOS-csillagok",
                          "signalling-question forms, completeness, implied judgement, NOS stars")},
    {"id": "probast",
     "label": _i18n("PROBAST+AI", "PROBAST+AI"),
     "standalone": True,
     "standalone_note": _i18n("doménszintű ítélet (4 domén × Low/High/Unclear, két menet) + alkalmazhatóság",
                              "domain-level judgement (4 domains × Low/High/Unclear, two passes) + applicability"),
     "plugin": "validator", "keywords": ("checklist", "probast"),
     "plugin_note": _i18n("16 + 18 jelző-kérdés, menettel minősített teljesség",
                          "16 + 18 signalling questions, pass-qualified completeness")},
    {"id": "tripod",
     "label": _i18n("TRIPOD+AI", "TRIPOD+AI"),
     "standalone": False,
     "standalone_note": _i18n("nem elérhető (a tétellista a validator referenciájából jön)",
                              "not available (the item list comes from the validator reference)"),
     "plugin": "validator", "keywords": ("checklist", "tripod"),
     "plugin_note": _i18n("52 altétel D/E szűréssel, hőtérkép, hiánylista, saját kézirat",
                          "52 sub-items with D/E filter, heat map, gap list, own manuscript")},
    {"id": "grade",
     "label": _i18n("GRADE + SoF", "GRADE + SoF"),
     "standalone": True,
     "standalone_note": _i18n("motor-tanácsadó (grade_help), grade_consistency, SoF a motorból",
                              "engine advisor (grade_help), grade_consistency, SoF from the engine"),
     "plugin": "validator", "keywords": ("appraise", "grade"),
     "plugin_note": _i18n("+ validator rollup_grade (V2 után megbízható)", "+ validator rollup_grade (reliable after V2)")},
    {"id": "amstar2",
     "label": _i18n("AMSTAR 2", "AMSTAR 2"),
     "standalone": True,
     "standalone_note": _i18n("KB AMSTAR2-lista + project audit bizonyíték-javaslatok + kézi besorolás + "
                              "amstar2_consistency",
                              "KB AMSTAR2 list + project audit evidence hints + manual rating + amstar2_consistency"),
     "plugin": "validator", "keywords": ("appraise", "amstar2", "amstar"),
     "plugin_note": _i18n("validator rollup_amstar2 (V4 után konvencióval)",
                          "validator rollup_amstar2 (with explicit convention after V4)")},
    {"id": "prisma",
     "label": _i18n("PRISMA", "PRISMA"),
     "standalone": True,
     "standalone_note": _i18n("kézi űrlap + élő P001–P016 + X014/X015", "manual form + live P001–P016 + X014/X015"),
     "plugin": "composer", "keywords": ("prisma", "export", "flow"),
     "plugin_note": _i18n("a composer-állapot élő olvasása (flow-json)", "live read of the composer state (flow-json)")},
    {"id": "prisma_figure",
     "label": _i18n("PRISMA-folyamatábra", "PRISMA flow diagram"),
     "standalone": False,
     "standalone_note": _i18n("önállóan csak a számok és az ellenőrzésük; a rajzot a figure-forge készíti",
                              "standalone: numbers and checks only; the drawing needs figure-forge"),
     "plugin": "figure-forge", "keywords": ("flowchart",),
     "plugin_note": _i18n("figure-forge folyamatábra (a motor specjével)", "figure-forge flowchart (from the engine spec)")},
    {"id": "manuscript",
     "label": _i18n("Kézirat-számok", "Manuscript numbers"),
     "standalone": False, "standalone_note": _i18n("—", "—"),
     "plugin": "presubmit", "keywords": ("check", "facts"),
     "plugin_note": _i18n("presubmit --facts (v2)", "presubmit --facts (v2)")},
)

# a motor parancsai (engine_capabilities): név → argv a repó gyökeréhez képest
ENGINE_COMMANDS = (
    ("analyze", ("ma.py", "analyze")),
    ("validate", ("ma.py", "validate")),
    ("convert", ("ma.py", "convert")),
    ("prisma", ("ma.py", "prisma", "check")),
    ("project", ("ma.py", "project")),
    ("kb", ("ma.py", "kb")),
)
_ENGINE_CONTRACT_IN = ("szk.ma.analysis-spec/v1", "szk.ma.convert-request/v1", "szk.prisma-flow/v1")

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_URN_RE = re.compile(r"^urn:szk:contract:([a-z0-9][a-z0-9.\-]*):(\d+)$")
_FILE_KEY_RE = re.compile(r"^(szk\.[a-z0-9.\-]+?)\.v(\d+)$")
_MAJOR_RE = re.compile(r"/v(\d+)$")
_MISSING_RE = re.compile(r"(?:ModuleNotFoundError|ImportError): No module named '([A-Za-z0-9_.]+)'")
_DLL_RE = re.compile(r"DLL load failed while importing ([A-Za-z0-9_]+)")
_TOKEN_SPLIT_RE = re.compile(r"[^0-9a-z]+")

_PROBE_CODE = (
    "import json, sys\n"
    "missing = []\n"
    "for name in sys.argv[1:]:\n"
    "    try:\n"
    "        __import__(name)\n"
    "    except Exception:\n"
    "        missing.append(name)\n"
    "print(json.dumps({'missing': missing, 'version': '%d.%d.%d' % tuple(sys.version_info[:3])}))\n"
)


# ---------------------------------------------------------------- segédek

def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _version_tuple(value):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def _issue_active(issue, version):
    """Az ismert hiba érvényes-e ennél a verziónál (ismeretlen verzió vagy javítás → igen)."""
    fixed = _version_tuple(issue.get("fixed_in"))
    current = _version_tuple(version)
    if fixed is None or current is None:
        return True
    return current < fixed


def _problem(code, level, hu, en):
    return {"code": code, "level": level, "hu": hu, "en": en}


def _quote_argv(argv):
    return " ".join('"%s"' % a if (" " in a or not a) else a for a in argv)


def _abspath(path):
    return Path(os.path.abspath(os.fspath(path)))


def is_windows_store_stub(path, nt=None):
    """A Windows Store-os python.exe-csonk (…\\Microsoft\\WindowsApps\\…) kiszűrése."""
    nt = (os.name == "nt") if nt is None else nt
    if not nt:
        return False
    parts = [p.lower() for p in re.split(r"[\\/]+", str(path)) if p]
    return "windowsapps" in parts


def default_runtime_dir(env=None, home=None):
    """A futásidejű mappa (2.4): Windows ``%LOCALAPPDATA%\\ma-gui``, macOS ``~/Library/Caches/ma-gui``,
    Linux ``$XDG_CACHE_HOME/ma-gui``; ``MA_GUI_RUNTIME_DIR`` felülírja. Nem hozza létre."""
    env = os.environ if env is None else env
    home = Path(home) if home is not None else Path.home()
    override = env.get("MA_GUI_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = env.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / "ma-gui"
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "ma-gui"
    xdg = env.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else home / ".cache") / "ma-gui"


def contract_key(name):
    """Szerződésnév normalizálása: ``szk.instrument/v1``, ``urn:szk:contract:instrument:1`` és
    ``szk.instrument.v1.schema.json`` → ``szk.instrument/v1``."""
    low = str(name).strip().lower()
    m = _URN_RE.match(low)
    if m:
        return "szk.%s/v%s" % m.groups()
    for suffix in (".schema.json", ".json"):
        if low.endswith(suffix):
            low = low[:-len(suffix)]
            break
    m = _FILE_KEY_RE.match(low)
    if m:
        return "%s/v%s" % m.groups()
    return low


def _contract_major(key):
    m = _MAJOR_RE.search(key)
    return int(m.group(1)) if m else None


def contract_files(dirs):
    """A helyi séma-másolatok: [(kulcs, sha256, aliasok)] — fájlonként egy, a bájtok hash-ével."""
    out = []
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.json")):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            digest = hashlib.sha256(data).hexdigest()
            aliases = [contract_key(path.name)]
            try:
                doc = json.loads(data.decode("utf-8-sig"))
            except ValueError:
                doc = None
            if isinstance(doc, dict):
                props = doc.get("properties")
                schema_prop = props.get("schema") if isinstance(props, dict) else None
                const = schema_prop.get("const") if isinstance(schema_prop, dict) else None
                if isinstance(const, str):
                    aliases.insert(0, contract_key(const))
                if isinstance(doc.get("$id"), str):
                    aliases.append(contract_key(doc["$id"]))
            uniq = []
            for a in aliases:
                if a not in uniq:
                    uniq.append(a)
            out.append((uniq[0], digest, tuple(uniq)))
    return out


def local_contract_index(dirs):
    """{normalizált szerződésnév: {sha256, …}} a helyi másolatokból."""
    index = {}
    for _key, digest, aliases in contract_files(dirs):
        for alias in aliases:
            index.setdefault(alias, set()).add(digest)
    return index


def validate_capabilities_doc(doc, plugin=None):
    """A ``szk.capabilities/v1`` minimális alakjának ellenőrzése (4.1). Visszaad: hibák listája
    (magyarul; üres lista = rendben). Ismeretlen mezőt nem kifogásol (4.0)."""
    if not isinstance(doc, dict):
        return ["a kézfogás-dokumentum nem objektum"]
    errs = ["hiányzó mező: %s" % k for k in ("schema", "plugin", "version", "ok", "contracts", "commands")
            if k not in doc]
    if errs:
        return errs
    if doc["schema"] != CAPS_SCHEMA:
        errs.append("schema: nem %s (ismeretlen főverzió vagy szerződés)" % CAPS_SCHEMA)
    if not isinstance(doc["plugin"], str) or not doc["plugin"]:
        errs.append("plugin: nem szöveg")
    elif plugin is not None and doc["plugin"] != plugin:
        errs.append("plugin: nem a várt plugin neve")
    if not isinstance(doc["version"], str) or not _VERSION_RE.match(doc["version"]):
        errs.append("version: nem X.Y.Z alakú")
    if not isinstance(doc["ok"], bool):
        errs.append("ok: nem logikai érték")
    if "python" in doc and not isinstance(doc["python"], str):
        errs.append("python: nem szöveg")
    contracts = doc["contracts"]
    if not isinstance(contracts, dict):
        errs.append("contracts: nem objektum")
    else:
        for name, info in contracts.items():
            if not isinstance(info, dict):
                errs.append("contracts[%s]: nem objektum" % name)
                continue
            if not isinstance(info.get("sha256"), str) or not _SHA256_RE.match(info["sha256"]):
                errs.append("contracts[%s].sha256: nem 64 jegyű kisbetűs hex" % name)
            dirs = info.get("dir")
            if not isinstance(dirs, list) or any(d not in ("in", "out") for d in dirs):
                errs.append("contracts[%s].dir: csak 'in'/'out' lehet" % name)
    commands = doc["commands"]
    if not isinstance(commands, list):
        errs.append("commands: nem tömb")
    else:
        for i, cmd in enumerate(commands):
            if not isinstance(cmd, dict):
                errs.append("commands[%d]: nem objektum" % i)
                continue
            if not isinstance(cmd.get("name"), str) or not cmd["name"]:
                errs.append("commands[%d].name: nem szöveg" % i)
            argv = cmd.get("argv")
            if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
                errs.append("commands[%d].argv: nem szövegtömb" % i)
            for key in ("in", "out"):
                if key in cmd and not isinstance(cmd[key], list):
                    errs.append("commands[%d].%s: nem tömb" % (i, key))
            if "needs" in cmd and (not isinstance(cmd["needs"], list)
                                   or not all(isinstance(n, str) for n in cmd["needs"])):
                errs.append("commands[%d].needs: nem szövegtömb" % i)
            if "available" in cmd and not isinstance(cmd["available"], bool):
                errs.append("commands[%d].available: nem logikai érték" % i)
    req = doc.get("requires")
    if req is not None:
        if not isinstance(req, dict):
            errs.append("requires: nem objektum")
        else:
            for key in ("modules", "missing"):
                if key in req and (not isinstance(req[key], list)
                                   or not all(isinstance(m, str) for m in req[key])):
                    errs.append("requires.%s: nem szövegtömb" % key)
    issues = doc.get("known_issues")
    if issues is not None:
        if not isinstance(issues, list):
            errs.append("known_issues: nem tömb")
        else:
            for i, issue in enumerate(issues):
                if (not isinstance(issue, dict) or not isinstance(issue.get("id"), str)
                        or not isinstance(issue.get("summary"), str)
                        or ("fixed_in" in issue and not isinstance(issue["fixed_in"], (str, type(None))))):
                    errs.append("known_issues[%d]: id/summary szöveg kell" % i)
    return errs


def _missing_from_stderr(data):
    text = _base.stderr_tail(data, 16384)
    names = []
    for regex in (_MISSING_RE, _DLL_RE):
        for m in regex.finditer(text):
            name = m.group(1).split(".")[0]
            if name not in names:
                names.append(name)
    return names


def _plugin_json_version(home):
    for rel in (Path(".claude-plugin") / "plugin.json", Path("plugin.json")):
        try:
            doc = json.loads((home / rel).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("version"), str) and doc["version"].strip():
            return doc["version"].strip()[:64]
    return None


def _latest_version_dir(directory):
    try:
        subs = [p for p in directory.iterdir() if p.is_dir()]
    except OSError:
        return None
    if not subs:
        return None

    def key(p):
        t = _version_tuple(p.name)
        return (1, t, p.name) if t else (0, (), p.name)
    return max(subs, key=key)


def _find_script(home, script):
    for sub in ("scripts", "", "bin"):
        path = home / sub / script if sub else home / script
        if path.is_file():
            return path
    for path in sorted(home.glob("skills/*/scripts/" + script)):
        if path.is_file():
            return path
    return None


def _installed_plugin_paths(path, name):
    """Best effort: ``installed_plugins.json`` (nem nyilvános formátum) installPath-jai."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    plugins = doc.get("plugins") if isinstance(doc, dict) else None
    if not isinstance(plugins, dict):
        return []
    found = []
    for key, val in plugins.items():
        if not isinstance(key, str) or key.split("@", 1)[0] != name:
            continue
        entries = val if isinstance(val, list) else [val]
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("installPath"), str) and entry["installPath"]:
                found.append((0 if key.endswith("@" + MARKETPLACE) else 1, Path(entry["installPath"]).expanduser()))
    return [p for _rank, p in sorted(found, key=lambda t: t[0])]


# ---------------------------------------------------------------- motor

def engine_capabilities():
    """A motor (metaelemzes) ``szk.capabilities/v1`` leírása — tiszta függvény, alfolyamat nélkül."""
    try:
        import metaelemzes
        version = str(getattr(metaelemzes, "__version__", "") or "")
        ok = True
    except Exception:                                        # noqa: BLE001 — a leírás akkor is készüljön el
        version, ok = "", False
    if not _VERSION_RE.match(version):
        version = "0.0.0"
    contracts = {}
    for key, digest, _aliases in contract_files([ENGINE_CONTRACTS_DIR]):
        contracts[key] = {"dir": ["in"] if key in _ENGINE_CONTRACT_IN else ["out"], "sha256": digest}
    return {
        "schema": CAPS_SCHEMA,
        "plugin": "metaelemzes",
        "version": version,
        "python": platform.python_version(),
        "ok": ok,
        "contracts": contracts,
        "commands": [{"name": name, "argv": list(argv), "needs": [], "available": ok}
                     for name, argv in ENGINE_COMMANDS],
        "requires": {"modules": [], "missing": []},
        "known_issues": [],
    }


def engine_component():
    """A motor sora a képesség-képernyőn (3.5.16), a pluginokkal azonos rekordalakban."""
    doc = engine_capabilities()
    commands = [{"name": c["name"], "available": c["available"], "mode": "json", "needs": [],
                 "in": [], "out": [], "drift": []} for c in doc["commands"]]
    contracts = [{"name": k, "version": _contract_major(k), "dir": list(v["dir"]), "sha256": v["sha256"],
                  "local": "match", "ok": True} for k, v in sorted(doc["contracts"].items())]
    return Capability(
        "metaelemzes", label=_i18n("motor", "engine"), state="ok" if doc["ok"] else "unusable",
        mode="api" if (ROOT / "metaelemzes" / "api.py").is_file() else "cli",
        version=doc["version"], handshake="ok", home=str(ROOT), source="builtin",
        script=str(ROOT / "ma.py"), scripts={"ma.py": str(ROOT / "ma.py")},
        python=[sys.executable] if sys.executable else None, python_source="sys.executable",
        python_version=doc["python"], capabilities=doc, commands=commands,
        features=[c["name"] for c in commands if c["available"]], contracts=contracts)


# ---------------------------------------------------------------- mátrix

def _command_tokens(name):
    low = str(name).lower()
    return {low} | {t for t in _TOKEN_SPLIT_RE.split(low) if t}


def _matching_commands(feature, commands):
    keys = set(feature["keywords"])
    return [c for c in commands or [] if _command_tokens(c.get("name", "")) & keys]


def feature_state(feature, cap):
    """Egy funkció állapota a plugin rekordja alapján: (állapot, okok-dict)."""
    state = cap.get("state") or "absent"
    if state in ("absent", "unusable"):
        return state, {}
    if cap.get("handshake") != "ok":
        return "legacy", {}
    cmds = _matching_commands(feature, cap.get("commands"))
    if not cmds:
        return "legacy", {"undeclared": list(feature["keywords"])}
    if any(c.get("available") and c.get("mode") == "json" for c in cmds):
        return "ok", {}
    if any(c.get("available") for c in cmds):
        drift = sorted({d for c in cmds for d in c.get("drift") or []})
        return "legacy", {"drift": drift}
    needs = sorted({n for c in cmds for n in c.get("needs") or []})
    return "unusable", {"needs": needs}


def _guard_texts(plugin):
    return {issue["id"]: issue for issue in BUILTIN_ISSUES.get(plugin, ())}


def _feature_row(feature, cap):
    row = {"label": feature["label"], "standalone": feature["standalone"],
           "standalone_note": feature["standalone_note"], "plugin": feature["plugin"],
           "with_plugin": None, "note": None, "guards": []}
    if not feature["plugin"]:
        return row
    state, why = feature_state(feature, cap)
    row["with_plugin"] = state
    known = _guard_texts(feature["plugin"])
    guards = [g for g in cap.get("guards") or [] if feature["id"] in known.get(g, {}).get("features", ())]
    row["guards"] = guards
    hu, en = [], []
    if state == "absent":
        hu.append("nincs telepítve")
        en.append("not installed")
        if cap.get("todo"):
            hu.append(cap["todo"]["hu"])
            en.append(cap["todo"]["en"])
    elif state == "unusable":
        if why.get("needs"):
            hu.append("ebben a környezetben nem elérhető (kell: %s)" % ", ".join(why["needs"]))
            en.append("not available in this environment (needs: %s)" % ", ".join(why["needs"]))
        else:
            errs = [p for p in cap.get("problems") or [] if p.get("level") == "error"]
            if errs:
                hu.append(errs[0]["hu"])
                en.append(errs[0]["en"])
            if cap.get("todo"):
                hu.append(cap["todo"]["hu"])
                en.append(cap["todo"]["en"])
    else:
        hu.append(feature["plugin_note"]["hu"])
        en.append(feature["plugin_note"]["en"])
        if state == "legacy":
            if why.get("drift"):
                hu.append("szerződés-eltérés: %s — legacy (bridge) mód" % ", ".join(why["drift"]))
                en.append("contract mismatch: %s — legacy (bridge) mode" % ", ".join(why["drift"]))
            elif why.get("undeclared"):
                hu.append("a kézfogás nem deklarál ilyen parancsot — legacy (bridge) mód")
                en.append("no such command in the handshake — legacy (bridge) mode")
            else:
                hu.append("régi verzió (bridge)")
                en.append("old version (bridge)")
        for g in guards:
            text = known[g].get("guard")
            if text:
                hu.append(text["hu"])
                en.append(text["en"])
    row["note"] = _i18n("; ".join(hu), "; ".join(en))
    return row


def build_matrix(plugins):
    """{funkció: {label, standalone, standalone_note, plugin, with_plugin, note, guards}} (5.2)."""
    matrix = {}
    for feature in FEATURES:
        plugin = feature["plugin"]
        cap = plugins.get(plugin) if plugin else None
        if plugin and cap is None:
            cap = Capability(plugin)
        matrix[feature["id"]] = _feature_row(feature, cap or {})
    return matrix


# ---------------------------------------------------------------- nyilvántartás

class Caps:
    """Plugin-képesség nyilvántartás, memóriában gyorsítótárazva; szálbiztos.

    Paraméterek (mind elhagyható): ``runtime_dir`` (a caps.json és a tmp helye), ``env`` (a
    felderítés környezeti változói; alapból os.environ), ``home`` (a ``~``), ``python`` (alapból
    sys.executable), ``project_plugins`` és ``project_root`` (a ma-projekt.json ``plugins`` kulcsa:
    csak plugin-mappa adható meg, interpreter nem), ``contracts_dirs`` (a saját séma-másolatok),
    ``timeout`` (kézfogás, s), ``specs`` (a PLUGINS felülírása, tesztekhez)."""

    def __init__(self, runtime_dir=None, *, env=None, home=None, python=None, project_plugins=None,
                 project_root=None, contracts_dirs=None, timeout=None, specs=None, config_path=None):
        self._env = None if env is None else dict(env)
        self.home = Path(home) if home is not None else Path.home()
        self.runtime_dir = (Path(runtime_dir) if runtime_dir is not None
                            else default_runtime_dir(self.env, self.home))
        self.config_path = Path(config_path) if config_path is not None else self.runtime_dir / "caps.json"
        self.python = str(python if python is not None else (sys.executable or ""))
        self.project_plugins = dict(project_plugins or {})
        self.project_root = Path(project_root) if project_root is not None else None
        self.contracts_dirs = [Path(d) for d in (contracts_dirs if contracts_dirs is not None
                                                 else DEFAULT_CONTRACT_DIRS)]
        self.specs = copy.deepcopy(specs if specs is not None else PLUGINS)
        if timeout is not None:
            _base._check_timeout(timeout)
        self._timeout = timeout
        self._lock = threading.RLock()
        self._plugin_locks = {name: threading.Lock() for name in self.specs}
        self._records = {}
        self._fingerprints = {}
        self._refresh_thread = None

    # -- alapadatok

    @property
    def env(self):
        return dict(os.environ) if self._env is None else self._env

    @property
    def plugin_names(self):
        names = [n for n in PLUGIN_ORDER if n in self.specs]
        return names + sorted(n for n in self.specs if n not in names)

    def _check_name(self, name):
        if name not in self.specs:
            raise KeyError("Ismeretlen plugin: %s" % name)

    def tmp_dir(self):
        """Az alfolyamatok munkakönyvtára (a projektmappán kívül, 2.4/7.3)."""
        path = self.runtime_dir / "tmp"
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            return Path(tempfile.gettempdir())

    def _load_config(self):
        try:
            if self.config_path.stat().st_size > MAX_CONFIG_BYTES:
                raise ValueError("túl nagy")
            raw = self.config_path.read_bytes()
        except FileNotFoundError:
            return {}, []
        except (OSError, ValueError):
            return {}, [_problem("config_unreadable", "warning",
                                 "A caps.json nem olvasható; az alapbeállítások érvényesek.",
                                 "caps.json is unreadable; defaults apply.")]
        try:
            cfg = json.loads(raw.decode("utf-8-sig"))
        except ValueError:
            cfg = None
        if not isinstance(cfg, dict):
            return {}, [_problem("config_invalid", "warning",
                                 "A caps.json nem érvényes JSON-objektum; az alapbeállítások érvényesek.",
                                 "caps.json is not a valid JSON object; defaults apply.")]
        return cfg, []

    def _config_mtime(self):
        try:
            return self.config_path.stat().st_mtime_ns
        except OSError:
            return None

    @staticmethod
    def _plugin_cfg(cfg, name):
        plugins = cfg.get("plugins")
        val = plugins.get(name) if isinstance(plugins, dict) else None
        if isinstance(val, str):
            return {"home": val}
        return val if isinstance(val, dict) else {}

    def _handshake_timeout(self, cfg):
        if self._timeout is not None:
            return float(self._timeout)
        val = cfg.get("timeout")
        if isinstance(val, (int, float)) and not isinstance(val, bool) and 1 <= val <= 120:
            return float(val)
        return HANDSHAKE_TIMEOUT

    # -- felderítés

    def _candidate_homes(self, name, cfg, pcfg):
        env = self.env
        upper = name.upper().replace("-", "_")
        out = []
        val = env.get("SZK_%s_HOME" % upper)
        if val:
            out.append((Path(val).expanduser(), "env:SZK_%s_HOME" % upper, True))
        if isinstance(pcfg.get("home"), str) and pcfg["home"]:
            out.append((Path(pcfg["home"]).expanduser(), "caps.json", True))
        proj = self.project_plugins.get(name)
        proj_home = proj.get("home") if isinstance(proj, dict) else proj
        if isinstance(proj_home, str) and proj_home:
            path = Path(proj_home).expanduser()
            if not path.is_absolute() and self.project_root is not None:
                path = self.project_root / path
            out.append((path, "ma-projekt.json", True))
        dirs = [("MA_GUI_PLUGIN_DIRS", d) for d in env.get("MA_GUI_PLUGIN_DIRS", "").split(os.pathsep)]
        extra = cfg.get("plugin_dirs")
        if isinstance(extra, list):
            dirs += [("caps.json:plugin_dirs", d) for d in extra if isinstance(d, str)]
        for source, d in dirs:
            if d.strip():
                base = Path(d.strip()).expanduser()
                out += [(base / name, source, False), (base / "plugins" / name, source, False)]
        plugins_root = self.home / ".claude" / "plugins"
        out.append((self.home / "Documents" / "claude" / MARKETPLACE / "plugins" / name, "git", False))
        out.append((plugins_root / "marketplaces" / MARKETPLACE / "plugins" / name, "marketplace", False))
        latest = _latest_version_dir(plugins_root / "cache" / MARKETPLACE / name)
        if latest is not None:
            out.append((latest, "cache", False))
        for path in _installed_plugin_paths(plugins_root / "installed_plugins.json", name):
            out.append((path, "installed_plugins.json", False))
        if plugins_root.is_dir():
            for pattern in ("marketplaces/*/plugins/%s", "*/plugins/%s", "*/*/plugins/%s", "*/*/*/plugins/%s"):
                for path in sorted(plugins_root.glob(pattern % name)):
                    out.append((path, "glob", False))
            for market in sorted(p for p in (plugins_root / "cache").glob("*") if p.is_dir()):
                latest = _latest_version_dir(market / name)
                if latest is not None:
                    out.append((latest, "cache", False))
        return out

    def discover(self, name, cfg=None, pcfg=None):
        """A plugin-mappa és a szkriptek feloldása (alfolyamat nélkül).
        Visszaad: ({home, scripts, source, version} vagy None, figyelmeztetések)."""
        self._check_name(name)
        if cfg is None:
            cfg, _ = self._load_config()
        if pcfg is None:
            pcfg = self._plugin_cfg(cfg, name)
        spec = self.specs[name]
        main = spec["scripts"][0]
        problems = []
        seen = set()
        for home, source, explicit in self._candidate_homes(name, cfg, pcfg):
            home = _abspath(home)
            key = os.path.normcase(str(home))
            if key in seen:
                continue
            seen.add(key)
            scripts = {}
            if home.is_dir():
                for script in spec["scripts"]:
                    path = _find_script(home, script)
                    if path is not None:
                        scripts[script] = _abspath(path)
            if main not in scripts:
                if explicit:
                    problems.append(_problem(
                        "configured_home_invalid", "warning",
                        "A megadott plugin-mappa (%s) nem tartalmazza a(z) %s szkriptet; tovább keresem."
                        % (source, main),
                        "The configured plugin folder (%s) has no %s script; searching further." % (source, main)))
                continue
            version = _plugin_json_version(home)
            if version is None and source == "cache" and _version_tuple(home.name):
                version = home.name
            return {"home": home, "scripts": scripts, "source": source, "version": version}, problems
        return None, problems

    # -- interpreter

    def _which(self, cmd):
        return shutil.which(cmd, path=self.env.get("PATH", ""))

    def _interp(self, argv, source):
        """Jelölt ellenőrzése: létező fájl (vagy PATH-on), nem Store-csonk."""
        argv = [str(a) for a in argv]
        exe = argv[0]
        if os.path.isabs(exe) or os.sep in exe or (os.altsep and os.altsep in exe):
            exe = os.path.abspath(os.path.expanduser(exe))
            if not os.path.isfile(exe):
                return None
        else:
            exe = self._which(exe)
            if not exe:
                return None
        if is_windows_store_stub(exe):
            return None
        return {"argv": [exe] + argv[1:], "source": source}

    def _interpreters(self, name, spec, home, pcfg):
        raw = []
        py = pcfg.get("python")
        if isinstance(py, str) and py:
            raw.append(([py], "caps.json"))
        elif isinstance(py, list) and py and all(isinstance(x, str) and x for x in py):
            raw.append((list(py), "caps.json"))
        env = self.env
        for var in spec.get("python_env", ()):
            if env.get(var):
                raw.append(([env[var]], "env:" + var))
        if self.python:
            raw.append(([self.python], "sys.executable"))
        for rel in (Path("bin") / "python", Path("bin") / "python3", Path("Scripts") / "python.exe"):
            if (home / ".venv" / rel).is_file():
                raw.append(([str(home / ".venv" / rel)], "plugin .venv"))
                break
        if spec.get("extended_python"):
            raw += self._extended_pythons()
        out, seen = [], set()
        for argv, source in raw:
            cand = self._interp(argv, source)
            if cand is None:
                continue
            key = tuple([os.path.normcase(cand["argv"][0])] + cand["argv"][1:])
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
        return out

    def _extended_pythons(self):
        """figure-forge: conda → ~/.claude/.venv → PATH (5.1)."""
        env, home, nt = self.env, self.home, os.name == "nt"
        raw = []
        conda_dirs = []
        if env.get("CONDA_PREFIX"):
            conda_dirs.append(Path(env["CONDA_PREFIX"]))
        for sub in ("anaconda3", "miniconda3", "miniforge3", "mambaforge"):
            conda_dirs += [home / sub, home / "opt" / sub]
        if nt:
            for var in ("LOCALAPPDATA", "ProgramData"):
                if env.get(var):
                    conda_dirs += [Path(env[var]) / "anaconda3", Path(env[var]) / "miniconda3"]
        else:
            conda_dirs += [Path("/opt/anaconda3"), Path("/opt/miniconda3"),
                           Path("/opt/homebrew/anaconda3"), Path("/usr/local/anaconda3")]
        for d in conda_dirs:
            raw.append(([str(d / ("python.exe" if nt else "bin/python"))], "conda"))
        venv = home / ".claude" / ".venv"
        raw.append(([str(venv / ("Scripts/python.exe" if nt else "bin/python"))], "~/.claude/.venv"))
        for cmd in ("python3", "python"):
            raw.append(([cmd], "PATH"))
        if nt:
            raw.append((["py", "-3"], "PATH"))
        return raw

    def _probe_imports(self, interp, modules, timeout):
        argv = list(interp["argv"]) + ["-c", _PROBE_CODE] + list(modules)
        try:
            res = _base._run(argv, timeout, 65536, cwd=self.tmp_dir())
        except (OSError, ValueError):
            return None
        if res["timed_out"] or res["returncode"] != 0:
            return None
        try:
            doc = _base.parse_json_output(res["stdout"])
        except ValueError:
            return None
        if not isinstance(doc, dict) or not isinstance(doc.get("missing"), list):
            return None
        return {"missing": [m for m in doc["missing"] if isinstance(m, str)],
                "version": doc.get("version") if isinstance(doc.get("version"), str) else None}

    def _rank_interpreters(self, spec, interps):
        """A szükséges importtal szondázott első teljes jelölt kerül előre (5.1)."""
        required = list(spec.get("probe_imports") or ())
        if not required or not interps:
            return interps, {}
        optional = [m for m, _label in spec.get("optional_imports") or ()]
        probes = {}
        chosen = None
        for i, interp in enumerate(interps[:MAX_PROBE_CANDIDATES]):
            res = self._probe_imports(interp, required + optional, IMPORT_PROBE_TIMEOUT)
            probes[i] = res
            if res is not None and not [m for m in res["missing"] if m in required]:
                chosen = i
                break
        order = list(range(len(interps)))
        if chosen is not None:
            order.remove(chosen)
            order.insert(0, chosen)
        ranked = [interps[i] for i in order]
        return ranked, {id(interps[i]): probes[i] for i in probes}

    # -- kézfogás

    def _handshake(self, name, interp, script, timeout):
        argv = list(interp["argv"]) + [str(script), "--capabilities"]
        try:
            res = _base._run(argv, timeout, MAX_HANDSHAKE_OUT, cwd=self.tmp_dir())
        except (OSError, ValueError) as exc:
            return {"kind": "spawn_error", "reason": type(exc).__name__}
        if res["timed_out"]:
            return {"kind": "timeout", "timeout": timeout}
        doc = None
        if not res["truncated"]:
            try:
                doc = _base.parse_json_output(res["stdout"])
            except ValueError:
                doc = None
        if isinstance(doc, dict) and str(doc.get("schema", "")).startswith("szk.capabilities/"):
            errs = validate_capabilities_doc(doc, plugin=name)
            if errs:
                return {"kind": "legacy", "warn": "handshake_invalid", "errors": errs[:5]}
            req = doc.get("requires") or {}
            missing = [m for m in req.get("missing") or []]
            if not doc["ok"] or missing:
                return {"kind": "unusable_doc", "doc": doc, "missing": missing}
            return {"kind": "ok", "doc": doc}
        if res["returncode"] != 0:
            missing = _missing_from_stderr(res["stderr"])
            if missing:
                return {"kind": "missing_modules", "missing": missing}
        return self._legacy_probe(interp, script, timeout,
                                  "nonzero" if res["returncode"] != 0 else "unparsable")

    def _legacy_probe(self, interp, script, timeout, why):
        """Nincs --capabilities: --help-szonda (5.1)."""
        argv = list(interp["argv"]) + [str(script), "--help"]
        try:
            res = _base._run(argv, timeout, MAX_HANDSHAKE_OUT, cwd=self.tmp_dir())
        except (OSError, ValueError) as exc:
            return {"kind": "spawn_error", "reason": type(exc).__name__}
        if res["timed_out"]:
            return {"kind": "legacy", "why": why, "warn": "help_timeout"}
        if res["returncode"] != 0:
            missing = _missing_from_stderr(res["stderr"])
            if missing:
                return {"kind": "missing_modules", "missing": missing}
            return {"kind": "legacy", "why": why, "warn": "help_failed", "returncode": res["returncode"]}
        return {"kind": "legacy", "why": why}

    # -- szondázás

    def _missing_todo(self, name, spec, interp, missing):
        pips = " ".join(PIP_NAMES.get(m, m) for m in missing)
        py = _quote_argv(interp["argv"]) if interp else "python"
        hu = "Telepítsd: %s -m pip install %s" % (py, pips)
        en = "Install: %s -m pip install %s" % (py, pips)
        hint = spec.get("python_hint")
        if hint:
            hu += " — vagy állítsd a %s környezeti változót egy olyan Pythonra, amelyben megvan." % hint
            en += " — or set %s to a Python that has it." % hint
        else:
            hu += " — vagy add meg egy megfelelő Python útját a caps.json plugins.%s.python kulcsában." % name
            en += " — or give a suitable Python in caps.json plugins.%s.python." % name
        return _i18n(hu, en)

    def _python_todo(self, name, spec):
        hint = spec.get("python_hint")
        hu = "Add meg egy működő Python útját a caps.json plugins.%s.python kulcsában" % name
        en = "Give a working Python in caps.json plugins.%s.python" % name
        if hint:
            hu += " vagy a %s környezeti változóban" % hint
            en += " or in %s" % hint
        return _i18n(hu + ".", en + ".")

    def _fingerprint(self, cap):
        parts = [("caps.json", self._config_mtime())]
        if cap.get("script"):
            paths = [cap["script"]]
            if cap.get("home"):
                paths.append(os.path.join(cap["home"], ".claude-plugin", "plugin.json"))
            for path in paths:
                try:
                    st = os.stat(path)
                    parts.append((path, st.st_mtime_ns, st.st_size))
                except OSError:
                    parts.append((path, None, None))
        return tuple(parts)

    def probe(self, name):
        """Egy plugin teljes szondázása gyorsítótár nélkül. Visszaad: Capability."""
        self._check_name(name)
        started = time.monotonic()
        try:
            cap = self._probe(name)
        except Exception as exc:                            # noqa: BLE001 — a nyilvántartás ne álljon le
            cap = Capability(name, label=self.specs[name].get("label", name), state="unusable",
                             handshake="failed")
            cap["problems"].append(_problem(
                "internal", "error", "Belső hiba a szondázás közben (%s)." % type(exc).__name__,
                "Internal error while probing (%s)." % type(exc).__name__))
        cap["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return cap

    def _probe(self, name):
        spec = self.specs[name]
        cfg, cfg_problems = self._load_config()
        pcfg = self._plugin_cfg(cfg, name)
        timeout = self._handshake_timeout(cfg)
        cap = Capability(name, label=spec.get("label", name))
        cap["problems"].extend(cfg_problems)
        if pcfg.get("disabled") is True:
            cap["problems"].append(_problem("disabled", "info", "A caps.json kikapcsolta.",
                                            "Disabled in caps.json."))
            cap["todo"] = _i18n("Bekapcsolás: töröld a caps.json plugins.%s.disabled kulcsát." % name,
                                "To enable: remove plugins.%s.disabled from caps.json." % name)
            return cap
        found, problems = self.discover(name, cfg, pcfg)
        cap["problems"].extend(problems)
        if found is None:
            upper = name.upper().replace("-", "_")
            cap["todo"] = _i18n(
                "Telepítés: claude plugin install %s@%s — vagy add meg a plugin mappáját az SZK_%s_HOME "
                "környezeti változóban." % (name, MARKETPLACE, upper),
                "Install: claude plugin install %s@%s — or set SZK_%s_HOME to the plugin folder."
                % (name, MARKETPLACE, upper))
            return cap
        home = found["home"]
        script = found["scripts"][spec["scripts"][0]]
        cap.update(home=str(home), source=found["source"], script=str(script), version=found["version"],
                   scripts={k: str(v) for k, v in found["scripts"].items()})
        interps = self._interpreters(name, spec, home, pcfg)
        if not interps:
            cap.update(state="unusable", handshake="failed", todo=self._python_todo(name, spec))
            cap["problems"].append(_problem("no_interpreter", "error", "Nem találtam futtatható Pythont.",
                                            "No runnable Python found."))
            return cap
        ranked, probes = self._rank_interpreters(spec, interps)
        attempts = []
        for interp in ranked[:MAX_INTERPRETER_TRIES]:
            res = self._handshake(name, interp, script, timeout)
            attempts.append((interp, res))
            if res["kind"] in ("ok", "legacy"):
                break
            retry = res["kind"] in ("missing_modules", "spawn_error") or (
                res["kind"] == "unusable_doc" and res.get("missing"))
            if not retry:
                break
        interp, res = self._choose(attempts)
        cap.update(python=list(interp["argv"]), python_source=interp["source"])
        probe = probes.get(id(interp))
        if probe and probe.get("version"):
            cap["python_version"] = probe["version"]
        if len(attempts) > 1:
            cap["interpreters_tried"] = len(attempts)
        self._apply(cap, name, spec, interp, res, probe, timeout)
        cap["features"] = [f["id"] for f in FEATURES if f["plugin"] == name
                           and feature_state(f, cap)[0] in _base.USABLE_STATES]
        return cap

    @staticmethod
    def _choose(attempts):
        for interp, res in attempts:
            if res["kind"] in ("ok", "legacy"):
                return interp, res
        for interp, res in attempts:
            if res["kind"] != "spawn_error":
                return interp, res
        return attempts[0]

    def _apply(self, cap, name, spec, interp, res, probe, timeout):
        kind = res["kind"]
        builtin = [dict((k, v) for k, v in issue.items() if k in ("id", "summary", "fixed_in"))
                   for issue in BUILTIN_ISSUES.get(name, ())]
        if kind in ("ok", "unusable_doc"):
            doc = res["doc"]
            cap.update(handshake="ok", capabilities=doc, version=doc["version"])
            if isinstance(doc.get("python"), str):
                cap["python_version"] = doc["python"]
            issues = [dict(i) for i in doc.get("known_issues") or []]
            cap["known_issues"] = issues
            cap["guards"] = sorted({i["id"] for i in issues if _issue_active(i, doc["version"])})
            self._apply_contracts(cap, doc)
            if kind == "unusable_doc":
                cap["state"] = "unusable"
                missing = res.get("missing") or []
                cap["missing_modules"] = list(missing)
                if missing:
                    cap["problems"].append(_problem("missing_modules", "error", "%s hiányzik" % ", ".join(missing),
                                                    "%s missing" % ", ".join(missing)))
                    cap["todo"] = self._missing_todo(name, spec, interp, missing)
                else:
                    cap["problems"].append(_problem(
                        "plugin_not_ok", "error",
                        "A plugin jelzése szerint ebben az interpreterben nem futtatható.",
                        "The plugin reports that it cannot run in this interpreter."))
                    cap["todo"] = self._python_todo(name, spec)
                return
            if cap["drift"]:
                cap.update(state="legacy", mode="bridge")
                cap["problems"].append(_problem(
                    "contract_drift", "warning",
                    "szerződés-eltérés: %s — az érintett funkciók legacy (bridge) módban futnak"
                    % ", ".join(cap["drift"]),
                    "contract mismatch: %s — affected features fall back to legacy (bridge) mode"
                    % ", ".join(cap["drift"])))
                cap["todo"] = _i18n("Frissítsd a plugint vagy a munkapadot, hogy a szerződések bájtra egyezzenek.",
                                    "Update the plugin or the workbench so that the contracts match byte for byte.")
            else:
                cap.update(state="ok", mode="json")
            return
        if kind == "missing_modules":
            missing = res["missing"]
            cap.update(state="unusable", handshake="failed", missing_modules=list(missing), known_issues=builtin)
            cap["problems"].append(_problem("missing_modules", "error", "%s hiányzik" % ", ".join(missing),
                                            "%s missing" % ", ".join(missing)))
            cap["todo"] = self._missing_todo(name, spec, interp, missing)
            return
        if kind == "timeout":
            cap.update(state="unusable", handshake="timeout", known_issues=builtin)
            cap["problems"].append(_problem(
                "handshake_timeout", "error",
                "A plugin %g s alatt nem válaszolt a --capabilities kérésre." % timeout,
                "The plugin did not answer --capabilities within %g s." % timeout))
            cap["todo"] = _i18n("Próbáld újra (Újraszondázás); ha tartósan lassú, ellenőrizd a Python-telepítést "
                                "vagy növeld a caps.json timeout értékét.",
                                "Retry (Re-probe); if it stays slow, check the Python installation or raise "
                                "the timeout in caps.json.")
            return
        if kind == "spawn_error":
            cap.update(state="unusable", handshake="failed", known_issues=builtin)
            cap["problems"].append(_problem("interpreter_failed", "error",
                                            "A Python-interpreter nem indítható (%s)." % res.get("reason", "?"),
                                            "The Python interpreter cannot be started (%s)." % res.get("reason", "?")))
            cap["todo"] = self._python_todo(name, spec)
            return
        # legacy: kézfogás nélkül nem tudjuk, mi fut a szükséges modulok nélkül (H5) → unusable (8.3)
        required = list(spec.get("probe_imports") or ())
        req_missing = [m for m in (probe or {}).get("missing", []) if m in required]
        if req_missing:
            cap.update(state="unusable", handshake="missing", missing_modules=req_missing, known_issues=builtin)
            cap["problems"].append(_problem("missing_modules", "error", "%s hiányzik" % ", ".join(req_missing),
                                            "%s missing" % ", ".join(req_missing)))
            cap["todo"] = self._missing_todo(name, spec, interp, req_missing)
            return
        version = cap.get("version")
        cap.update(state="legacy", mode="bridge", known_issues=builtin,
                   handshake="invalid" if res.get("warn") == "handshake_invalid" else "missing",
                   note=_i18n("a --capabilities hiányzik; verzióhoz rögzített bridge-adapter fut",
                              "--capabilities is missing; a version-pinned bridge adapter runs"))
        cap["guards"] = [i["id"] for i in BUILTIN_ISSUES.get(name, ()) if _issue_active(i, version)]
        warn = res.get("warn")
        if warn == "handshake_invalid":
            cap["problems"].append(_problem(
                "handshake_invalid", "warning",
                "Érvénytelen kézfogás-dokumentum (%s); legacy módban fut." % "; ".join(res.get("errors") or []),
                "Invalid handshake document; running in legacy mode."))
        elif warn == "help_failed":
            cap["problems"].append(_problem(
                "help_probe_failed", "warning",
                "A --help-szonda hibával lépett ki (kód: %s); a bridge-adapter hibázhat." % res.get("returncode"),
                "The --help probe exited with an error (code %s); the bridge adapter may fail."
                % res.get("returncode")))
        elif warn == "help_timeout":
            cap["problems"].append(_problem("help_probe_timeout", "warning",
                                            "A --help-szonda nem válaszolt időben.",
                                            "The --help probe did not answer in time."))
        tested = BRIDGE_TESTED.get(name)
        if tested and version and version != tested:
            cap["problems"].append(_problem(
                "bridge_untested_version", "info",
                "A bridge-adapter a %s verzióhoz készült; a telepített %s ezzel nem tesztelt." % (tested, version),
                "The bridge adapter targets %s; installed %s is untested." % (tested, version)))
        if probe is not None:
            for module, label in spec.get("optional_imports") or ():
                if module in probe["missing"]:
                    pip = PIP_NAMES.get(module, module)
                    cap["problems"].append(_problem(
                        "optional_missing", "info", "%s: %s hiányzik" % (label["hu"], pip),
                        "%s: %s missing" % (label["en"], pip)))

    def _apply_contracts(self, cap, doc):
        index = local_contract_index(self.contracts_dirs)
        contracts, drift = [], []
        for cname, info in sorted(doc["contracts"].items()):
            key = contract_key(cname)
            hashes = index.get(key)
            if not hashes:
                status = "unknown"
            elif info["sha256"] in hashes:
                status = "match"
            else:
                status = "drift"
                drift.append(cname)
            contracts.append({"name": cname, "version": _contract_major(key), "dir": list(info.get("dir") or []),
                              "sha256": info["sha256"], "local": status, "ok": status != "drift"})
        drift_keys = {contract_key(n): n for n in drift}
        commands = []
        for cmd in doc["commands"]:
            used = [x for x in list(cmd.get("in") or []) + list(cmd.get("out") or []) if isinstance(x, str)]
            hit = sorted({drift_keys[contract_key(u)] for u in used if contract_key(u) in drift_keys})
            commands.append({"name": cmd["name"], "available": cmd.get("available", True) is not False,
                             "mode": "legacy" if hit else "json", "needs": list(cmd.get("needs") or []),
                             "in": list(cmd.get("in") or []), "out": list(cmd.get("out") or []), "drift": hit})
        cap.update(contracts=contracts, drift=drift, commands=commands)

    # -- gyorsítótár

    def _store(self, name, cap):
        with self._lock:
            self._records[name] = cap
            self._fingerprints[name] = self._fingerprint(cap)

    def _stale(self, name, cap):
        with self._lock:
            fp = self._fingerprints.get(name)
        return fp is None or self._fingerprint(cap) != fp

    def get(self, name):
        """A plugin rekordja (másolat); ha nincs a gyorsítótárban vagy elavult, szondáz."""
        self._check_name(name)
        with self._lock:
            cap = self._records.get(name)
        if cap is not None and not self._stale(name, cap):
            return copy.deepcopy(cap)
        with self._plugin_locks[name]:
            with self._lock:
                current = self._records.get(name)
            if current is not None and current is not cap and not self._stale(name, current):
                return copy.deepcopy(current)                # közben egy másik szál frissítette
            fresh = self.probe(name)
            self._store(name, fresh)
            return copy.deepcopy(fresh)

    def plugins(self):
        """{név: Capability} minden pluginra (a hiányzókat szondázza)."""
        names = self.plugin_names
        with self._lock:
            missing = [n for n in names if n not in self._records]
        if missing:
            self.refresh(missing)
        return {n: self.get(n) for n in names}

    def refresh(self, names=None):
        """Újraszondázás (párhuzamosan, pluginonként egy szálon). Visszaad: {név: Capability}."""
        names = self.plugin_names if names is None else list(names)
        for name in names:
            self._check_name(name)

        def work(name):
            with self._plugin_locks[name]:
                self._store(name, self.probe(name))

        threads = [threading.Thread(target=work, args=(n,), name="ma-gui-caps-%s" % n, daemon=True)
                   for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with self._lock:
            return {n: copy.deepcopy(self._records[n]) for n in names if n in self._records}

    def start_refresh(self, names=None):
        """Háttérszálas újraszondázás (2.2: indításkor). Ha már fut, a futó szálat adja vissza."""
        with self._lock:
            thread = self._refresh_thread
            if thread is not None and thread.is_alive():
                return thread
            thread = threading.Thread(target=self._refresh_quiet, args=(names,), name="ma-gui-caps",
                                      daemon=True)
            self._refresh_thread = thread
            thread.start()
            return thread

    def _refresh_quiet(self, names):
        try:
            self.refresh(names)
        except Exception:                                    # noqa: BLE001 — a következő get() újraszondáz
            pass

    @property
    def probing(self):
        thread = self._refresh_thread
        return bool(thread is not None and thread.is_alive())

    def wait(self, timeout=None):
        """Megvárja a háttérszondázást; True, ha befejeződött."""
        thread = self._refresh_thread
        if thread is not None:
            thread.join(timeout)
        return not self.probing

    # -- kimenetek

    def capability_matrix(self):
        """Az 5.2 képesség-mátrix: {funkció: {label, standalone, standalone_note, plugin,
        with_plugin, note, guards}} — JSON-képes."""
        return build_matrix(self.plugins())

    def report(self):
        """A GET /api/capabilities adata: {schema, generated, probing, components[], matrix, problems}."""
        plugins = self.plugins()
        _cfg, cfg_problems = self._load_config()
        components = [dict(engine_component())] + [dict(plugins[n]) for n in self.plugin_names]
        return {"schema": REPORT_SCHEMA, "generated": _now_iso(), "probing": self.probing,
                "components": components, "matrix": build_matrix(plugins), "problems": cfg_problems}


# ---------------------------------------------------------------- modulszintű alappéldány

_DEFAULT = None
_DEFAULT_LOCK = threading.Lock()


def default_caps():
    """A folyamat közös Caps-példánya (alapbeállításokkal)."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = Caps()
        return _DEFAULT


def set_default_caps(caps):
    """A szerver indításkor a saját (pl. projektfüggő futásidejű mappájú) példányát állítja be."""
    global _DEFAULT
    if caps is not None and not isinstance(caps, Caps):
        raise TypeError("Caps-példány kell.")
    with _DEFAULT_LOCK:
        _DEFAULT = caps


def get(name):
    return default_caps().get(name)


def refresh(names=None):
    return default_caps().refresh(names)


def capability_matrix():
    return default_caps().capability_matrix()


def report():
    return default_caps().report()
