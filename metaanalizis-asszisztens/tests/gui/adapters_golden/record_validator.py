# -*- coding: utf-8 -*-
"""A validator bridge-golden kimenetei (``tests/gui/adapters_golden/validator-<verzió>/``): rögzítés és összevetés.

    python3 tests/gui/adapters_golden/record_validator.py <szk-plugins/plugins> [név …]

A valódi plugin ``--skeleton`` / ``--verify`` / ``--rollup`` kimenete a ``docs.json`` dokumentumaira, PONTOSAN úgy,
ahogy a bridge-adapter hívja: a telepített verzió szótárával (``validator.answer_tokens``), a hídon átmenő értékekkel
(``validator.bridge_values``) és — a javított kiadástól — a váz számozás-jelölőjével. A
``test_v1_adapters_real`` ugyanezzel a függvénnyel ellenőrzi, hogy a rögzített kimenetek nem sodródtak; a stub-plugin
(``_adapters_stubs``) ezeket adja vissza.

A ``NAMES`` verziónként rögzíti, mely dokumentumoknak van goldenje (az 1.0.0-é az eredeti H1–H4 reprodukció; a
2.0.0-é ugyanez a javított pluginnal, plusz a javított kiadás új kimenetei: publikált ROBINS-I- és QUIPS-számozás,
QUIPS „Partly”, QUADAS-2 1.2/1.3, NOS részleges csillag és INVALID, GRADE UNRESOLVED / feloldás / −2 / „Very large”,
ROBIS „Phase 3”).
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ma_gui.adapters import validator as V  # noqa: E402

DOCS_PATH = os.path.join(HERE, "docs.json")
NAMES = {
    "1.0.0": ("amstar2_py", "grade_strong", "probast_dev", "rob2", "tripod_empty"),
    "2.0.0": ("amstar2_py", "grade_pb2", "grade_resolved", "grade_strong", "grade_suspected", "grade_very_large",
              "nos_partial", "probast_dev", "quadas2_yes", "quips_partly", "rob2", "robins_i_2016", "robis_phase3",
              "tripod_empty"),
}


def golden_dir(version):
    return os.path.join(HERE, "validator-%s" % version)


def load_docs():
    with open(DOCS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def plugin_version(vdir):
    """A plugin.json verziója (``vdir`` a validator plugin mappája), vagy None."""
    try:
        with open(os.path.join(vdir, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            v = json.load(fh).get("version")
    except (OSError, ValueError):
        return None
    return v if isinstance(v, str) else None


def outputs(scripts, name, doc, version, cwd):
    """{'skeleton': …, 'verify': …[, 'rollup': …]} — a valódi plugin kimenete, ahogy a bridge kapja."""
    tool = doc["tool"]
    script, vtool, _f = V.TOOLS[tool]
    scope = V._scope(doc, tool, None)

    def run(args):
        return subprocess.run([sys.executable, os.path.join(scripts, script)] + args, capture_output=True,
                              text=True, cwd=cwd, env=dict(os.environ, PYTHONUTF8="1")).stdout

    out = {"skeleton": run(["--skeleton", vtool, "--scope", scope])}
    fixed = V.fixed_release({"version": version})
    if name == "tripod_empty":
        md = out["skeleton"]                        # H1: maga az üres sablon
    else:
        slots = V.parse_skeleton(out["skeleton"], checklist=(script == "checklist.py"))
        md = V.bridge_markdown(tool, slots, V.bridge_values(doc, tool, fixed), fixed,
                               V.skeleton_numbering(out["skeleton"]), V.applicability_of(doc, tool))[0]
    path = os.path.join(cwd, name + ".md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    out["verify"] = run(["--verify", path, "--tool", vtool, "--scope", scope])
    if script == "appraise.py":
        out["rollup"] = run(["--rollup", path, "--tool", vtool, "--scope", scope])
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    base = argv.pop(0)
    vdir = os.path.join(base, "validator") if os.path.isdir(os.path.join(base, "validator")) else base
    version = plugin_version(vdir)
    if version not in NAMES:
        print("ismeretlen validator-verzió: %s (van: %s)" % (version, ", ".join(sorted(NAMES))), file=sys.stderr)
        return 2
    docs = load_docs()
    names = argv or NAMES[version]
    gdir = golden_dir(version)
    os.makedirs(gdir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            for kind, text in sorted(outputs(os.path.join(vdir, "scripts"), name, docs[name], version, tmp).items()):
                with open(os.path.join(gdir, "%s.%s.txt" % (name, kind)), "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
            print("%s: %s" % (version, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
