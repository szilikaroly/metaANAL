# -*- coding: utf-8 -*-
"""Stub-pluginok az adapter-tesztekhez (terv 8.3): figure-forge, validator, composer minden állapotban.

``make_plugins(base, figure_forge=…, validator=…, composer=…)`` egy ``base/<plugin>/`` fát ír (plugin.json,
``scripts/<szkript>``, ``stub.json``) — ``None`` = a plugin hiányzik (absent). A szkriptek közös magja
(``_adpstub.py``) a ``stub.json`` szerint viselkedik:

- **figure-forge** módok: ``h5`` (modulszintű ModuleNotFoundError: matplotlib — mint a 0.2.1 ff.py:28),
  ``legacy`` (nincs ``--capabilities``; az ``audit`` a fájl MELLÉ írja a ``.editability.json``-t, mint a 0.2.1;
  ``audit_h5: true`` → az audit futás közben hal el ModuleNotFoundError-ral; ``audit_fail: true`` → 1-es kód),
  ``f1`` (kézfogás + ``audit --json``), ``f2`` (+ ``meta --request … --json``: SVG a plot_data.json motorszövegeivel,
  és PDF/PNG/TIFF/PPTX helyes fejléccel; ``drop_number: true`` → egy szám hiányzik az SVG-ből), ``unusable``.
- **validator**: ``legacy`` (1.0.0: a ``--skeleton/--verify/--rollup`` a RÖGZÍTETT golden kimeneteket adja —
  ``tests/gui/adapters_golden/validator-1.0.0/``), ``json`` (V1: a ``--rollup/--verify … --json`` a ``stub.json``
  ``result``-ját adja; a kapott bemenetet a ``capture`` útra írja — adatvédelmi teszthez), ``unusable``.
- **composer**: ``legacy`` (1.4.1: ``--outdir D --project P export --format flow-json --out O`` / ``status``; az
  állapot ``D/prisma/P.json``: a ``flow`` kulcsa a flow-json, a ``status_text`` a status szövege; ``truncated_times``
  → az első N futás JSONDecodeError-ral hal el, mint egy félig írt állapotnál — H7), ``json`` (C1), ``unusable``.
  A szkript shebangja abszolút és NEM létező út, és a fájl nem futtatható: csak explicit interpreterrel indul (H7).
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "adapters_golden", "validator-1.0.0")
BAD_SHEBANG = "#!/Users/szili/anaconda3/bin/python3\n"

SCRIPTS = {"figure-forge": ("ff.py",), "validator": ("appraise.py", "checklist.py"), "composer": ("prisma",)}
VERSIONS = {"figure-forge": "0.2.1", "validator": "1.0.0", "composer": "1.4.1"}

SHIM = BAD_SHEBANG + '''# -*- coding: utf-8 -*-
"""Adapter-stub (tesztekhez)."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _adpstub  # noqa: E402
sys.exit(_adpstub.main(__file__))
'''

CORE = r'''# -*- coding: utf-8 -*-
import json
import os
import re
import sys
from pathlib import Path

CAPS = "szk.capabilities/v1"
COMMANDS = {
    "figure-forge": {
        "f1": [{"name": "audit", "argv": ["scripts/ff.py", "audit"], "out": []}],
        "f2": [{"name": "audit", "argv": ["scripts/ff.py", "audit"], "out": []},
               {"name": "meta", "argv": ["scripts/ff.py", "meta"], "in": [], "out": []},
               {"name": "flowchart", "argv": ["scripts/ff.py", "flowchart"], "in": []}],
    },
    "validator": {"json": [{"name": "appraise.rollup", "argv": ["scripts/appraise.py", "--rollup"]},
                           {"name": "appraise.verify", "argv": ["scripts/appraise.py", "--verify"]},
                           {"name": "checklist.verify", "argv": ["scripts/checklist.py", "--verify"]}]},
    "composer": {"json": [{"name": "prisma.export", "argv": ["scripts/prisma", "export"]},
                          {"name": "prisma.status", "argv": ["scripts/prisma", "status"]}]},
}


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _arg(args, flag, default=None):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def main(script_file):
    script = Path(script_file).resolve()
    pdir = script.parent.parent
    cfg = json.loads((pdir / "stub.json").read_text(encoding="utf-8"))
    man = json.loads((pdir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    name, version, mode = man["name"], cfg.get("version") or man["version"], cfg.get("mode", "legacy")
    args = sys.argv[1:]
    if mode == "h5":
        raise ModuleNotFoundError("No module named 'matplotlib'", name="matplotlib")
    if "--capabilities" in args:
        if mode == "legacy":
            sys.stderr.write("usage: %s [-h]\nerror: unrecognized arguments: --capabilities\n" % script.name)
            return 2
        doc = {"schema": CAPS, "plugin": name, "version": version, "python": "3", "ok": mode != "unusable",
               "contracts": {}, "commands": cfg.get("commands") or COMMANDS.get(name, {}).get(mode, []),
               "requires": {"modules": [], "missing": list(cfg.get("missing") or []) if mode == "unusable" else []},
               "known_issues": cfg.get("known_issues") or []}
        _emit(doc)
        return 0
    if "--help" in args or "-h" in args:
        sys.stdout.write("usage: %s [-h] ...\n" % script.name)
        return 0
    if name == "figure-forge":
        return ff(args, cfg, mode, version)
    if name == "validator":
        return validator(script.name, args, cfg, mode, version)
    if name == "composer":
        return composer(args, cfg, mode, pdir)
    return 2


# ------------------------------------------------------------------ figure-forge
def _texts(svg):
    return [re.sub(r"<[^>]+>", "", t) for t in re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S)]


def ff(args, cfg, mode, version):
    if not args:
        return 2
    if args[0] == "audit":
        path = Path(args[1])
        if cfg.get("audit_h5"):
            raise ModuleNotFoundError("No module named 'matplotlib'", name="matplotlib")
        if cfg.get("audit_fail"):
            sys.stderr.write("Traceback (most recent call last):\nRuntimeError: audit boom\n")
            return 1
        svg = path.read_text(encoding="utf-8")
        texts = _texts(svg)
        fams = sorted(set(re.findall(r'font-family="([^"]*)"', svg)))
        rep = {"file": str(path), "svg": {"text_elements": len(texts), "outlined_text_groups": 0,
                                          "font_families": fams, "font_fallback_stack": any("," in f for f in fams),
                                          "named_layers": svg.count('id="layer-'), "editable": bool(texts),
                                          "labels": texts},
               "typography_suggestions": [{"from": t, "to": t.replace("-", "−")} for t in texts
                                          if re.search(r"(?<![\w.])-(?=\d)", t)],
               "missing_glyphs": []}
        if "--json" in args:
            _emit(rep)
        else:
            path.with_suffix(".editability.json").write_text(json.dumps(rep), encoding="utf-8")
            sys.stdout.write("[figure-forge] editability audit\n  VERDICT: editable\n")
        return 0
    if args[0] == "meta" and mode == "f2":
        req = json.loads(Path(_arg(args, "--request")).read_text(encoding="utf-8"))
        plot = json.loads(Path(req["input"]["plot_path"]).read_text(encoding="utf-8"))
        loc = (req.get("style") or {}).get("locale") or "en"
        out = Path(req["out"]["dir"])
        stem = req["out"]["stem"]
        # egy forest-sor (címke, hatás, súly) egy alapvonalon, mint egy valódi rajzolónál; (szöveg, sor)
        texts = []
        for i, s in enumerate(plot.get("studies") or []):
            texts += [(s.get("label") or "", i), ((s.get("display_text") or {}).get(loc, ""), i),
                      ((s.get("weight_text") or {}).get(loc, ""), i)]
        row = len(texts)
        for s in plot.get("summaries") or []:
            row += 1
            texts.append(((s.get("display_text") or {}).get(loc, ""), row))
            if s.get("pi_text"):
                row += 1
                texts.append(("PI " + s["pi_text"].get(loc, ""), row))
        for t in (plot.get("axis") or {}).get("ticks") or []:
            row += 1
            texts.append((t.get("text") or "", row))
        het = (plot.get("heterogeneity") or {}).get("text")
        if isinstance(het, dict):
            row += 1
            texts.append((het.get(loc) or "", row))
        if cfg.get("drop_number") and len(texts) > 1:
            texts = texts[:1] + texts[2:]
        esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")
        body = "".join('<text x="1" y="%d" font-family="Arial, Helvetica, sans-serif">%s</text>' % (12 * (r + 1), esc(t))
                       for t, r in texts)
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><g id="layer-rows">%s</g></svg>' % body
        formats = {}
        heads = {"svg": svg.encode("utf-8"), "pdf": b"%PDF-1.4\n%stub\n", "png": b"\x89PNG\r\n\x1a\nstub",
                 "tiff": b"II*\x00stub", "pptx": b"PK\x03\x04stub"}
        for fmt in req["out"].get("formats") or ["svg"]:
            if fmt in heads:
                ext = {"tiff": ".tiff"}.get(fmt, "." + fmt)
                (out / (stem + ext)).write_bytes(heads[fmt])
                formats[fmt] = stem + ext
        if "svg" not in formats:
            (out / (stem + ".svg")).write_bytes(heads["svg"])
            formats["svg"] = stem + ".svg"
        if cfg.get("escape_path"):
            formats["png"] = "/etc/passwd"
        dropped = bool(cfg.get("drop_number"))
        _emit({"schema": "szk.figure-result/v1", "kind": req["kind"], "stem": stem, "ff_version": version,
               "formats": formats, "dpi": req["style"]["dpi"], "width_mm": 183, "height_mm": 120,
               "clean": not cfg.get("residual"), "labels_checked": len(texts),
               "residual_violations": [{"rule": "R1", "kind": "overlap", "label": "x", "message": "m"}] if cfg.get("residual") else [],
               "typography": {"enabled": True, "substitutions": [{"from": "-", "to": "−", "n": 2}]},
               "glyphs": {"ok": True, "missing": [], "font": "Arial"},
               "editability": {"svg": {"editable": True, "outlined_text_groups": 0, "font_fallback_stack": True,
                                       "named_layers": 1}},
               "numbers": {"ok": not dropped, "checked": len(texts), "mismatches": [{"text": "x"}] if dropped else []},
               "source": {"plot_sha256": req["input"].get("plot_sha256"), "run_id": req["input"].get("run_id")}})
        return 3 if cfg.get("residual") else 0
    sys.stderr.write("usage: ff.py {audit,...}\nerror: invalid choice\n")
    return 2


# ------------------------------------------------------------------ validator
def validator(script, args, cfg, mode, version):
    tool = _arg(args, "--tool") or _arg(args, "--skeleton")
    case = (cfg.get("golden") or {}).get(tool)
    gdir = Path(cfg.get("golden_dir") or ".")
    if "--json" in args and mode == "json":
        src = _arg(args, "--rollup") or _arg(args, "--verify")
        doc = json.loads(Path(src).read_text(encoding="utf-8"))
        if cfg.get("capture"):
            Path(cfg["capture"]).write_text(json.dumps(doc), encoding="utf-8")
        res = dict((cfg.get("results") or {}).get(tool) or {})
        res.setdefault("schema", "szk.appraisal-result/v1")
        res.setdefault("validator_version", version)
        _emit(res)
        return 0
    if "--skeleton" in args:
        sys.stdout.write((gdir / ("%s.skeleton.txt" % case)).read_text(encoding="utf-8"))
        return 0
    if "--verify" in args:
        text = (gdir / ("%s.verify.txt" % case)).read_text(encoding="utf-8")
        sys.stdout.write(text)
        return 1 if "UNANSWERED" in text else 0
    if "--rollup" in args:
        sys.stdout.write((gdir / ("%s.rollup.txt" % case)).read_text(encoding="utf-8"))
        return 0
    return 2


# ------------------------------------------------------------------ composer
def composer(args, cfg, mode, pdir):
    outdir = Path(_arg(args, "--outdir", "."))
    project = _arg(args, "--project", "review")
    rest = [a for i, a in enumerate(args) if a not in ("--outdir", "--project")
            and (i == 0 or args[i - 1] not in ("--outdir", "--project"))]
    cmd = rest[0] if rest else ""
    path = outdir / "prisma" / ("%s.json" % project)
    if not path.exists():
        sys.exit("Nincs PRISMA állapot: %s" % path)
    n_trunc = int(cfg.get("truncated_times") or 0)
    counter = pdir / "truncated.count"
    seen = int(counter.read_text()) if counter.exists() else 0
    if seen < n_trunc:
        counter.write_text(str(seen + 1))
        json.loads(path.read_text(encoding="utf-8")[:7])          # csonka: JSONDecodeError (H7)
    state = json.loads(path.read_text(encoding="utf-8"))
    if cmd == "export":
        out = Path(_arg(args, "--out"))
        out.mkdir(parents=True, exist_ok=True)
        (out / "prisma-flow.json").write_text(json.dumps(state.get("flow")), encoding="utf-8")
        sys.stdout.write("Exportálva:\n  %s\n" % (out / "prisma-flow.json"))
        return 0
    if cmd == "status":
        if "--json" in args and mode == "json":
            _emit({"warnings": state.get("warnings_json") or []})
            return 0
        sys.stdout.write(state.get("status_text") or "")
        return 0
    return 2
'''

STATUS_TEXT = (
    "PRISMA 2020 — projekt: glp1  (frissítve: 2026-10-04T20:12:00+00:00)\n"
    "================================================================\n"
    "  Azonosított összesen: 1246  (adatbázis 1234, regiszter 12, egyéb 0)\n"
    "  ! FIGYELEM: 12 azonosított rekord NEM került be a korpuszba (retmax-korlát, vagy szűrés előtt eltávolított "
    "duplikátum). A lenti szűrés a behozott 1234 rekordon történt.\n"
    "----------------------------------------------------------------\n"
    "  Függőben (nem ellenőrizhető):      3\n"
    "  ! A függőben lévők nem hivatkozhatók ellenőrzöttként. Vagy szerezd meg a teljes szöveget, vagy vedd ki őket.\n")

FLOW_JSON = {
    "databases": [{"source": "PubMed", "query": "glp-1 AND pregnancy", "count_total": 1234}], "registers": [],
    "other_sources": [], "identified_databases": 1234, "identified_registers": 12, "identified_other": 0,
    "identified_total": 1246, "retrieved_total": 1234, "retrieval_gap": 12, "dedup_removed": 300,
    "removed_before_screening": [], "removed_before_screening_n": 0, "screened": 946, "excluded_screening": 860,
    "sought_for_retrieval": 86, "not_retrieved": 4, "assessed_eligibility": 82, "excluded_eligibility": 57,
    "excluded_eligibility_reasons": {"wrong population": 30, "wrong outcome": 27}, "included": 25, "undecided": 0,
}


def _write_json(path, doc):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)


def make_plugin(base, name, cfg):
    pdir = os.path.join(base, name)
    if os.path.isdir(pdir):
        shutil.rmtree(pdir)
    os.makedirs(os.path.join(pdir, ".claude-plugin"))
    os.makedirs(os.path.join(pdir, "scripts"))
    _write_json(os.path.join(pdir, ".claude-plugin", "plugin.json"),
                {"name": name, "version": cfg.get("version") or VERSIONS[name], "description": "adapter-stub"})
    cfg = dict(cfg)
    if name == "validator":
        cfg.setdefault("golden_dir", GOLDEN)
        cfg.setdefault("golden", {"rob2": "rob2", "probast": "probast_dev", "tripod": "tripod_empty",
                                  "grade": "grade_strong", "amstar2": "amstar2_py"})
    _write_json(os.path.join(pdir, "stub.json"), cfg)
    with open(os.path.join(pdir, "scripts", "_adpstub.py"), "w", encoding="utf-8") as fh:
        fh.write(CORE)
    for script in SCRIPTS[name]:
        p = os.path.join(pdir, "scripts", script)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(SHIM)
        os.chmod(p, 0o644)                       # nem futtatható: csak explicit interpreterrel indul (H7)
    return pdir


def make_plugins(base, figure_forge=None, validator=None, composer=None):
    """A stub-pluginok (``None`` → hiányzik). Visszaad: ``base`` (a MA_GUI_PLUGIN_DIRS értéke)."""
    os.makedirs(base, exist_ok=True)
    for name, cfg in (("figure-forge", figure_forge), ("validator", validator), ("composer", composer)):
        pdir = os.path.join(base, name)
        if cfg is None:
            if os.path.isdir(pdir):
                shutil.rmtree(pdir)
            continue
        make_plugin(base, name, cfg)
    return base


def composer_state(outdir, project="glp1", flow=None, status_text=STATUS_TEXT, warnings_json=None, raw=None):
    """Composer-állapot a stubnak (``raw``: nyers fájltartalom, pl. csonka JSON)."""
    d = os.path.join(outdir, "prisma")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s.json" % project)
    if raw is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        return path
    _write_json(path, {"schema": "the-collector.prisma/v1", "project": project, "updated": "2026-10-04T20:12:00+00:00",
                       "flow": dict(FLOW_JSON) if flow is None else flow, "status_text": status_text,
                       "warnings_json": warnings_json or []})
    return path


def caps_specs():
    """A caps PLUGINS-másolata, a figure-forge import-szondája nélkül (a stub saját állapotot játszik el,
    függetlenül attól, hogy a tesztgépen van-e matplotlib)."""
    import copy
    from ma_gui import caps as caps_mod
    specs = copy.deepcopy(caps_mod.PLUGINS)
    specs["figure-forge"]["probe_imports"] = ()
    specs["figure-forge"]["optional_imports"] = ()
    specs["figure-forge"]["extended_python"] = False
    return specs
