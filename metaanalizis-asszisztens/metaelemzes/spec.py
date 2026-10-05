# -*- coding: utf-8 -*-
"""Elemzési spec (szk.ma.analysis-spec/v1) ↔ pipeline.DEFAULTS ↔ argv leképezés és futás-leíró (szk.ma.run/v1).

A leképezés NEM kézzel karbantartott lista: a cli.build_parser() 'analyze' alparancsának introspekciójából épül.

- Az `options` kulcsai PONTOSAN a pipeline.DEFAULTS kulcsai (egy futás results.json.options-éből a spec
  visszaállítható). Az argparse dest ↔ DEFAULTS kulcs eltérést a DEST_TO_KEY tábla kezeli (--ht-centre → h_centre,
  --robust → metareg_robust); minden más eltérést a mapping_problems() jelez (teszt őrzi).
- Az érték-átalakítás az argparse-akció fajtájából következik: kapcsoló (store_true) ↔ bool; yes/no választás ↔
  true/false/null; lista alapértékű opció ↔ vesszős szöveg; minden más érték a kapcsoló saját `type`-ján és
  `choices`-án át (így a spec és a parancssor ugyanazt az opció-dictet adja).
- A DEFAULTS kapcsoló nélküli kulcsai (pl. label_col, bias_min_k) csak specben állíthatók.
- A `data.path` a projektgyökérhez képest relatív, '/' elválasztóval; a szűrők a --include/--exclude 'oszlop=érték'
  szövegei.

A cmd_analyze az opció-dictet az options_from_args()-szal építi; az `analyze --spec` a namespace-t az apply_spec()-kel
tölti ki, így a spec és a kapcsolók ugyanazon a kódúton futnak (azonos results.json).
"""
import argparse
import collections
import copy
import datetime
import difflib
import functools
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import unicodedata

from . import __version__
from .pipeline import DEFAULTS

SCHEMA = "szk.ma.analysis-spec/v1"
SCHEMA_ID = "urn:szk:contract:ma.analysis-spec:1"
RUN_SCHEMA = "szk.ma.run/v1"
RUN_SCHEMA_ID = "urn:szk:contract:ma.run:1"
SPEC_DIR = "05_elemzes/specs"
PURPOSES = ("primary", "sensitivity", "subgroup", "metaregression", "exploratory")
MODES = ("explore", "commit")

# argparse dest → DEFAULTS kulcs, ahol a kettő eltér (4.4)
DEST_TO_KEY = {"ht_centre": "h_centre", "robust": "metareg_robust"}
DATA_DEST = "data"
FILTER_DESTS = ("include", "exclude")
# futás-vezérlő kapcsolók: nem részei a specnek (a --spec mellett is megadhatók)
RUN_DESTS = ("out", "project", "date", "no_plots", "spec", "json_summary", "run_id", "actor")
# a parancssorban kötelező opció a specben is kötelező (a DEFAULTS 'SMD'-je nem csendes alapérték)
REQUIRED_OPTIONS = ("measure",)

NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
RELPATH_PATTERN = r"^(?!/)(?![A-Za-z]:)(?!.*\.\.)[^\\:]+$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
FILTER_PATTERN = r"^[^=]+=.*$"
RUN_ID_PATTERN = r"^\d{8}T\d{6}Z-[0-9a-f]{6}$"
_RE = {k: re.compile(v[1:-1], re.S) for k, v in (("name", NAME_PATTERN), ("relpath", RELPATH_PATTERN),
                                                 ("sha256", SHA256_PATTERN), ("filter", FILTER_PATTERN),
                                                 ("run_id", RUN_ID_PATTERN))}
_TOP_ORDER = ("schema", "name", "outcome", "purpose", "prespecified", "protocol_ref", "parent", "data", "options",
              "filters", "kb_refs")
# write_outputs fájlnév → run.json files-szerep
_FILE_ROLES = collections.OrderedDict((("results.json", "results"), ("plot_data.json", "plot"),
                                       ("report.md", "report"), ("effect_sizes.csv", "effect_sizes"),
                                       ("forest.svg", "forest"), ("funnel.svg", "funnel"), ("doi.svg", "doi")))


class SpecError(ValueError):
    """Érvénytelen spec vagy parancssor (a CLI 'HIBA: …' üzenettel, 1-es kóddal lép ki)."""


def _match(kind, s):
    return isinstance(s, str) and _RE[kind].fullmatch(s) is not None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ------------------------------------------------------------ introspekció
def _analyze_parser():
    """Friss 'analyze' alparser (a hibái SpecError-t dobnak, nem SystemExit-et) és a parancs nevei (álnevekkel)."""
    from .cli import build_parser
    top = build_parser()
    sub = next(a for a in top._actions if isinstance(a, argparse._SubParsersAction))
    an = sub.choices["analyze"]

    def error(message):
        raise SpecError("parancssor: %s" % message)
    an.error = error
    names = tuple(n for n, p in sub.choices.items() if p is an)
    return an, names


@functools.lru_cache(maxsize=1)
def _parser():
    """Gyorsítótárazott 'analyze' alparser csak parse-olásra (az argparse a parse során nem módosítja)."""
    return _analyze_parser()


def _flag(act):
    longs = [s for s in act.option_strings if s.startswith("--")]
    return (longs or act.option_strings)[0]


def _choices(act):
    if act.choices is not None:
        return list(act.choices)
    m = re.fullmatch(r"\{([^{}]+)\}", act.metavar) if isinstance(act.metavar, str) else None
    return [c.strip() for c in m.group(1).split(",")] if m else None


class _Opt(object):
    """Egy DEFAULTS-kulcs leképezése: kulcs, argparse-akció (vagy None: csak specben), fajta, választások."""
    __slots__ = ("key", "act", "dest", "flag", "kind", "choices", "default", "required", "json_type", "help")


def _kind(act, default):
    if isinstance(act, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        return "flag"
    if isinstance(act, argparse._AppendAction) and act.nargs is None:
        return "multi"
    if type(act) is argparse._StoreAction and act.nargs is None:
        if act.choices is not None and set(act.choices) == {"yes", "no"} and act.default is None:
            return "tristate"
        if isinstance(default, list):
            return "csv"
        return "value"
    return None


def _arg_to_option(o, raw):
    """argparse-érték → opció-érték (a cmd_analyze korábbi kézi szabályai, fajta szerint)."""
    if o.kind == "flag":
        return bool(raw)
    if o.kind == "tristate":
        return None if raw is None else raw == "yes"
    if o.kind == "csv":
        return [m.strip() for m in raw.split(",")] if raw else []
    if o.kind == "multi":
        return list(raw) if raw else []
    return raw


def _json_type(o):
    if o.choices is not None:
        return "enum"
    d = DEFAULTS[o.key]
    if o.act is None:
        return {bool: "boolean", int: "integer", float: "number", str: "string", list: "array",
                dict: "object"}.get(type(d), "any")
    if o.kind in ("flag", "tristate"):
        return "boolean"
    if o.kind in ("csv", "multi"):
        return "array"
    if _num(d):
        return "number"
    if isinstance(d, str) or o.act.type in (None, str, str.upper, str.lower):
        return "string"
    try:
        return "number" if _num(o.act.type("1")) else "string"
    except Exception:  # noqa: BLE001 — próba: a konverter nem fogad számot
        return "string"


_Meta = collections.namedtuple("_Meta", "opts data filters names problems")


@functools.lru_cache(maxsize=1)
def _meta():
    an, names = _analyze_parser()
    by_key, data, filters, problems = {}, None, {}, []
    for act in an._actions:
        if isinstance(act, argparse._HelpAction):
            continue
        dest = act.dest
        if dest == DATA_DEST:
            data = act
            continue
        if dest in FILTER_DESTS:
            filters[dest] = act
            continue
        if dest in RUN_DESTS:
            continue
        key = DEST_TO_KEY.get(dest, dest)
        if key not in DEFAULTS:
            close = difflib.get_close_matches(dest, list(DEFAULTS), n=1)
            problems.append("a(z) %s kapcsoló (dest=%s) nem képezhető pipeline.DEFAULTS-kulcsra%s — vedd fel a "
                            "spec.DEST_TO_KEY-be vagy a DEFAULTS-ba" % (_flag(act), dest,
                                                                      (" (talán: %s)" % close[0]) if close else ""))
            continue
        if key in by_key:
            problems.append("két kapcsoló ugyanarra a kulcsra: %s és %s → %s" % (_flag(by_key[key]), _flag(act), key))
            continue
        by_key[key] = act
    base = an.parse_args(["--data", "_", "--measure", _choices(by_key["measure"])[0]]) if "measure" in by_key \
        else None
    opts = collections.OrderedDict()
    for key, dflt in DEFAULTS.items():
        o = _Opt()
        act = by_key.get(key)
        o.key, o.act = key, act
        o.dest = act.dest if act is not None else None
        o.flag = _flag(act) if act is not None else None
        o.kind = _kind(act, dflt) if act is not None else "spec_only"
        o.choices = _choices(act) if act is not None and o.kind == "value" else None
        o.required = key in REQUIRED_OPTIONS or bool(act is not None and act.required)
        o.help = (act.help if act is not None and act.help != argparse.SUPPRESS else None)
        if act is None:
            o.default = copy.deepcopy(dflt)
        elif o.kind is None:
            problems.append("a(z) %s kapcsoló argparse-akciója nem támogatott (%s)" % (o.flag, type(act).__name__))
            o.default = copy.deepcopy(dflt)
        else:
            o.default = None if o.required else _arg_to_option(o, getattr(base, act.dest))
            if not o.required and o.default != dflt:
                problems.append("alapérték-eltérés: %s = %r a parancssorban, %r a pipeline.DEFAULTS-ban"
                                % (key, o.default, dflt))
        o.json_type = _json_type(o)
        opts[key] = o
    for need, act in ((DATA_DEST, data),) + tuple((d, filters.get(d)) for d in FILTER_DESTS):
        if act is None:
            problems.append("hiányzó analyze-kapcsoló: --%s" % need)
    return _Meta(opts, data, filters, names, tuple(problems))


def mapping_problems():
    """A kapcsolók ↔ DEFAULTS leképezés introspekcióval talált hibái (üres lista = rendben; teszt őrzi)."""
    return list(_meta().problems)


def spec_only_keys():
    """A DEFAULTS azon kulcsai, amelyeknek nincs parancssori kapcsolója (csak specben állíthatók)."""
    return [k for k, o in _meta().opts.items() if o.act is None]


def option_table():
    """Opció-metaadat (űrlap, engine_info): kulcs, kapcsoló (és álnevei), dest, fajta, JSON-típus, választások,
    alapérték, súgó."""
    res = []
    for key, o in _meta().opts.items():
        res.append({"key": key, "flag": o.flag, "flags": list(o.act.option_strings) if o.act is not None else [],
                    "dest": o.dest, "kind": o.kind, "type": o.json_type,
                    "choices": list(o.choices) if o.choices is not None else None,
                    "nullable": o.default is None and not o.required,
                    "default": copy.deepcopy(o.default), "required": o.required, "help": o.help,
                    "cli": o.act is not None})
    return res


# ------------------------------------------------------------ args → opciók
def options_from_args(a):
    """argparse-namespace (analyze) → opció-dict PONTOSAN a pipeline.DEFAULTS kulcsaival, DEFAULTS-sorrendben.
    A cmd_analyze ezzel építi az opcióit (a 'filter_report'-ot külön teszi hozzá)."""
    opts = {}
    for key, o in _meta().opts.items():
        if o.act is None:
            opts[key] = copy.deepcopy(getattr(a, key, o.default))
        else:
            opts[key] = _arg_to_option(o, getattr(a, o.dest, o.act.default))
    return opts


# ------------------------------------------------------------ validálás
def _token(v):
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _pair(flag, s):
    # '-'-sal kezdődő értéket az argparse kapcsolónak nézne: --kapcsoló=érték alak
    return [flag + "=" + s] if s.startswith("-") else [flag, s]


def _check_value(o, v, where):
    """Egy opció-érték ellenőrzése → (hibák, kanonikus érték)."""
    if o.act is None:
        d = DEFAULTS[o.key]
        want = {bool: (bool,), int: (int,), float: (int, float), str: (str,), list: (list,), dict: (dict,)}.get(type(d))
        if v is None and d is None:
            return [], v
        if want is None:
            return [], v
        if want == (int,) and isinstance(v, float) and math.isfinite(v) and v.is_integer():
            v = int(v)                      # a JSON-ban a 2.0 és a 2 ugyanaz a szám (a séma 'integer'-nek veszi)
        ok = isinstance(v, want) and (bool in want or not isinstance(v, bool))
        if ok and _num(v) and not math.isfinite(v):
            ok = False
        if not ok:
            return ["%s: %s szükséges (mint az alapérték: %r), kapott: %r" % (
                where, {bool: "logikai érték", int: "egész szám", float: "véges szám", str: "szöveg",
                        list: "lista", dict: "objektum"}[type(d)], d, v)], v
        return [], v
    if o.kind == "flag":
        if not isinstance(v, bool):
            return ["%s: logikai érték (true/false) szükséges, kapott: %r" % (where, v)], v
        return [], v
    if o.kind == "tristate":
        if v is not None and not isinstance(v, bool):
            return ["%s: true, false vagy null szükséges, kapott: %r" % (where, v)], v
        return [], v
    if o.kind in ("csv", "multi"):
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            return ["%s: szöveglista szükséges, kapott: %r" % (where, v)], v
        errs = []
        if o.kind == "csv":
            for i, x in enumerate(v):
                if not x or "," in x or x != x.strip():
                    errs.append("%s[%d]: nem üres, vessző és szélső szóköz nélküli név szükséges (a parancssorban "
                                "vesszővel elválasztott lista), kapott: %r" % (where, i, x))
        return errs, v
    if v is None:
        if o.default is not None or o.required:
            return ["%s: nem lehet null%s — az alapértékhez hagyd el a kulcsot" % (
                where, "" if o.required else " (alapérték: %r)" % (o.default,))], v
        return [], v
    if o.json_type == "number" or (o.json_type == "enum" and _num(o.default)):
        if not _num(v) or not math.isfinite(v):
            return ["%s: véges szám szükséges, kapott: %r" % (where, v)], v
    elif not isinstance(v, str):
        return ["%s: szöveg szükséges, kapott: %r" % (where, v)], v
    tok = _token(v)
    try:
        conv = o.act.type(tok) if o.act.type is not None else tok
    except (argparse.ArgumentTypeError, TypeError, ValueError) as exc:
        return ["%s: %s" % (where, exc)], v
    if o.choices is not None and conv not in o.choices:
        return ["%s: érvénytelen érték %r (lehetséges: %s)" % (where, v, ", ".join(map(str, o.choices)))], v
    if conv != v:
        return ["%s: nem kanonikus érték %r — a parancssor %r-ként értelmezi; a specben ez szerepeljen"
                % (where, v, conv)], v
    return [], conv


def validate_spec(spec):
    """szk.ma.analysis-spec/v1 ellenőrzése → magyar hibaüzenetek listája (üres = érvényes).
    Az options ismeretlen kulcsa hiba (a szerződésben is additionalProperties: false); a felső szint és a data
    ismeretlen mezőit a fogyasztó figyelmen kívül hagyja (4.0). Amit a szerződés (terv 4.4) mintája nem fejez ki, de
    a motor elutasít: a filters ismeretlen kulcsa, a nem névalakú parent, a '/'-re végződő data.path, a nem 64 jegyű
    data.sha256 (null sem: rögzítés nélkül a kulcs elmarad) és a szűrő üres oszlopneve."""
    if not isinstance(spec, dict):
        return ["a spec nem JSON-objektum (kapott: %s)" % type(spec).__name__]
    errs = []
    for req in ("schema", "name", "outcome", "data", "options"):
        if req not in spec:
            errs.append("hiányzó kötelező mező: %s" % req)
    if "schema" in spec and spec["schema"] != SCHEMA:
        errs.append("schema: %r helyett %r szükséges" % (spec["schema"], SCHEMA))
    if "name" in spec and not _match("name", spec["name"]):
        errs.append("name: érvénytelen név %r (kisbetű, számjegy, '_' vagy '-'; betűvel vagy számmal kezdődik; "
                    "legfeljebb 64 karakter)" % (spec["name"],))
    if "outcome" in spec and not isinstance(spec["outcome"], str):
        errs.append("outcome: szöveg szükséges, kapott: %r" % (spec["outcome"],))
    if "purpose" in spec and spec["purpose"] not in PURPOSES:
        errs.append("purpose: érvénytelen érték %r (lehetséges: %s)" % (spec["purpose"], ", ".join(PURPOSES)))
    if "prespecified" in spec and not isinstance(spec["prespecified"], bool):
        errs.append("prespecified: logikai érték (true/false) szükséges, kapott: %r" % (spec["prespecified"],))
    if "protocol_ref" in spec and not (spec["protocol_ref"] is None or isinstance(spec["protocol_ref"], str)):
        errs.append("protocol_ref: szöveg vagy null szükséges, kapott: %r" % (spec["protocol_ref"],))
    if "parent" in spec and not (spec["parent"] is None or _match("name", spec["parent"])):
        errs.append("parent: az elsődleges spec neve (mint a name) vagy null szükséges, kapott: %r" % (spec["parent"],))
    if "data" in spec:
        d = spec["data"]
        if not isinstance(d, dict):
            errs.append("data: objektum szükséges, kapott: %r" % (d,))
        else:
            if "path" not in d:
                errs.append("hiányzó kötelező mező: data.path")
            elif not _match("relpath", d["path"]) or d["path"].endswith("/") or "\x00" in d["path"]:
                errs.append("data.path: a projektgyökérhez képest relatív, '/' elválasztós fájlút szükséges ('..', "
                            "meghajtójel, '\\' és ':' nélkül), kapott: %r" % (d["path"],))
            if "sha256" in d and not _match("sha256", d["sha256"]):
                errs.append("data.sha256: 64 jegyű kisbetűs hexadecimális hash szükséges (rögzítés nélkül hagyd el a "
                            "kulcsot), kapott: %r" % (d["sha256"],))
    if "options" in spec:
        errs += _options_errors(spec["options"])
    if "filters" in spec:
        f = spec["filters"]
        if not isinstance(f, dict):
            errs.append("filters: objektum szükséges, kapott: %r" % (f,))
        else:
            for k, v in f.items():
                if k not in FILTER_DESTS:
                    errs.append("filters.%s: ismeretlen szűrő (lehetséges: %s)" % (k, ", ".join(FILTER_DESTS)))
                elif not isinstance(v, list):
                    errs.append("filters.%s: 'oszlop=érték' szövegek listája szükséges, kapott: %r" % (k, v))
                else:
                    for i, x in enumerate(v):
                        if not _match("filter", x) or not x.partition("=")[0].strip():
                            errs.append("filters.%s[%d]: 'oszlop=érték' alakú szöveg szükséges, kapott: %r" % (k, i, x))
    if "kb_refs" in spec and not (isinstance(spec["kb_refs"], list)
                                  and all(isinstance(x, str) for x in spec["kb_refs"])):
        errs.append("kb_refs: szöveglista szükséges, kapott: %r" % (spec["kb_refs"],))
    return errs


def _options_errors(options):
    if not isinstance(options, dict):
        return ["options: objektum szükséges, kapott: %r" % (options,)]
    meta = _meta()
    errs = []
    for key in options:
        if key not in meta.opts:
            hint = DEST_TO_KEY.get(key)
            if hint is None:
                close = difflib.get_close_matches(str(key), list(meta.opts), n=1)
                hint = close[0] if close else None
            errs.append("options.%s: ismeretlen opció (a spec pontosan a pipeline.DEFAULTS kulcsait használja)%s"
                        % (key, (" — talán: %s" % hint) if hint else ""))
    for key, o in meta.opts.items():
        if key not in options:
            if o.required:
                errs.append("options.%s: kötelező (a parancssorban is: %s)" % (key, o.flag or key))
            continue
        errs += _check_value(o, options[key], "options.%s" % key)[0]
    return errs


def _require_valid(spec):
    errs = validate_spec(spec)
    if errs:
        raise SpecError("érvénytelen elemzési spec: " + "; ".join(errs))


# ------------------------------------------------------------ spec → argv / opciók
def _option_tokens(options, all_options=False, spec_only_ok=False):
    """Opció-dict → kapcsolók (alapértéktől eltérők; all_options=True: minden kifejezhető). A kapcsoló nélküli
    kulcs alapértéktől eltérő értéke SpecError (csak --spec-kel futtatható), hacsak spec_only_ok."""
    meta = _meta()
    toks = []
    for key, o in meta.opts.items():
        if key not in options:
            continue
        v = _check_value(o, options[key], key)[1]
        if o.act is None:
            if v != o.default and not spec_only_ok:
                raise SpecError("options.%s = %r: ennek az opciónak nincs parancssori kapcsolója; csak "
                                "'analyze --spec'-kel futtatható" % (key, v))
            continue
        if not (all_options or o.required) and v == o.default:
            continue
        if o.kind == "flag":
            if v == o.act.const:
                toks.append(o.flag)
        elif o.kind == "tristate":
            if v is not None:
                toks += [o.flag, "yes" if v else "no"]
        elif o.kind == "csv":
            if v:
                toks += _pair(o.flag, ",".join(v))
        elif o.kind == "multi":
            for x in v:
                toks += _pair(o.flag, x)
        elif v is not None:
            toks += _pair(o.flag, _token(v))
    return toks


def _filter_tokens(filters):
    toks = []
    for dest in FILTER_DESTS:
        for x in (filters or {}).get(dest) or []:
            toks += _pair("--" + dest, x)
    return toks


def _disp_path(path, root):
    """Útvonal a run.json-ba és az egyenértékű parancssorba: a gyökérhez képest relatív ('/'), ha azon belül van;
    különben abszolút."""
    ap = os.path.abspath(path)
    if root is not None:
        try:
            rel = os.path.relpath(ap, os.path.abspath(root))
        except ValueError:      # Windows: másik meghajtó
            rel = None
        if rel is not None and rel != os.pardir and not rel.startswith(os.pardir + os.sep) and not os.path.isabs(rel):
            return rel.replace(os.sep, "/")
    return ap.replace(os.sep, "/") if os.sep != "/" else ap


def data_path(spec, project_root=None):
    """A spec adatfájljának útja a futtatáshoz: a projektgyökérhez (alap: a munkakönyvtár, '.') illesztve."""
    root = project_root if project_root is not None else "."
    return os.path.normpath(os.path.join(root, *spec["data"]["path"].split("/")))


def argv_from_spec(spec, project_root=None, out_dir=None, log_to_project=False, absolute=False, all_options=False):
    """Spec → egyenértékű parancssor: ['analyze', '--data', …, '--measure', …, …].
    Az utak a projektgyökérből futtatva érvényesek (relatívak, ha a gyökéren belül vannak); absolute=True: abszolút
    utak (bárhonnan futtatható). Csak az alapértéktől eltérő opciók szerepelnek (all_options=True: mind)."""
    _require_valid(spec)
    root = project_root
    data = data_path(spec, root) if absolute else spec["data"]["path"]
    if absolute:
        data = os.path.abspath(data)
    argv = ["analyze"] + _pair("--data", data) + _option_tokens(spec["options"], all_options) + \
        _filter_tokens(spec.get("filters"))
    if out_dir is not None:
        argv += _pair("--out", os.path.abspath(out_dir) if absolute or root is None else _disp_path(out_dir, root))
    if log_to_project:
        argv += ["--project", os.path.abspath(root if root is not None else ".") if absolute else "."]
    return argv


def namespace_from_spec(spec, project_root=None):
    """Spec → az 'analyze' argparse-namespace-e, mintha a kapcsolókat adták volna meg (a kapcsoló nélküli
    kulcsok attribútumként); a cmd_analyze ezzel változatlanul futtatható."""
    _require_valid(spec)
    an, _ = _parser()
    toks = _pair("--data", data_path(spec, project_root)) + \
        _option_tokens(spec["options"], all_options=True, spec_only_ok=True) + _filter_tokens(spec.get("filters"))
    ns = an.parse_args(toks)
    for key, o in _meta().opts.items():
        if o.act is None:
            v = spec["options"].get(key, o.default)
            setattr(ns, key, copy.deepcopy(_check_value(o, v, key)[1] if key in spec["options"] else v))
    return ns


def options_from_spec(spec):
    """Spec → teljes opció-dict (DEFAULTS-kulcsok); azonos az ugyanezt jelentő kapcsolókból kapott
    options_from_args()-szal (a kapcsolók type/choices szabályain át)."""
    return options_from_args(namespace_from_spec(spec))


def filters_from_spec(spec):
    """(exclude, include) — mint az argparse: üres helyett None (results.json input.filters)."""
    f = spec.get("filters") or {}
    return (list(f.get("exclude") or []) or None, list(f.get("include") or []) or None)


# ------------------------------------------------------------ argv → spec
def _slug(text):
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9_-]+", "_", s).lstrip("_-")[:64].rstrip("_-")
    return s or "elemzes"


def _strip_command(argv, names):
    argv = list(argv)
    if argv and argv[0] in names:
        return argv[1:]
    return argv


def spec_from_namespace(a, project_root=None, name=None, outcome=None, pin_data=False, **fields):
    """Parse-olt 'analyze' namespace → spec. A data.path a projektgyökérhez (alap: a --project, különben a
    munkakönyvtár) relatív; gyökéren kívüli adatfájl SpecError. pin_data=True: data.sha256 is (commit)."""
    if getattr(a, "spec", None):
        raise SpecError("--spec-et tartalmazó parancssorból nem képezhető spec (a spec maga a fájl)")
    if not getattr(a, DATA_DEST, None):
        raise SpecError("hiányzik a --data")
    root = project_root if project_root is not None else (getattr(a, "project", None) or ".")
    rel = _disp_path(a.data, root)
    if os.path.isabs(rel) or not _match("relpath", rel):
        raise SpecError("az adatfájl (%s) nincs a projektgyökéren (%s) belül, vagy az útja nem írható le relatívan; "
                        "a spec csak projekten belüli adatfájlra hivatkozhat" % (a.data, os.path.abspath(root)))
    stem = os.path.splitext(os.path.basename(a.data))[0]
    spec = {"schema": SCHEMA, "name": name or _slug(stem), "outcome": outcome if outcome is not None else stem}
    for k in ("purpose", "prespecified", "protocol_ref", "parent", "kb_refs"):
        if fields.get(k) is not None:
            spec[k] = fields[k]
    unknown = set(fields) - {"purpose", "prespecified", "protocol_ref", "parent", "kb_refs"}
    if unknown:
        raise TypeError("ismeretlen spec-mező: %s" % ", ".join(sorted(unknown)))
    spec["data"] = {"path": rel}
    if pin_data:
        spec["data"]["sha256"] = sha256_file(a.data)
    spec["options"] = options_from_args(a)
    spec["filters"] = {d: list(getattr(a, d, None) or []) for d in FILTER_DESTS}
    _require_valid(spec)
    return canonical_spec(spec)


def spec_from_argv(argv, project_root=None, name=None, outcome=None, pin_data=False, **fields):
    """Parancssor (['analyze', '--data', …] vagy a parancsnév nélkül) → szk.ma.analysis-spec/v1."""
    an, names = _parser()
    ns = an.parse_args(_strip_command(argv, names))
    return spec_from_namespace(ns, project_root, name=name, outcome=outcome, pin_data=pin_data, **fields)


def spec_conflicts(argv):
    """A --spec mellett tiltott (a specet felülíró) kapcsolók a megadás sorrendjében; megengedett: RUN_DESTS."""
    an, names = _analyze_parser()
    for act in an._actions:
        if not isinstance(act, argparse._HelpAction):
            act.default = argparse.SUPPRESS
            act.required = False
    ns = an.parse_args(_strip_command(argv, names))
    return [_flag(act) for act in an._actions
            if not isinstance(act, argparse._HelpAction) and act.dest not in RUN_DESTS and hasattr(ns, act.dest)]


def _default_conflicts(a):
    """Tartalék, ha a nyers argv nem ismert: az alapértéktől eltérő elemzési kapcsolók."""
    an, _ = _parser()
    res = []
    for act in an._actions:
        if isinstance(act, argparse._HelpAction) or act.dest in RUN_DESTS:
            continue
        dflt = act.default
        if isinstance(dflt, str) and act.type is not None:
            dflt = act.type(dflt)
        if getattr(a, act.dest, dflt) != dflt:
            res.append(_flag(act))
    return res


# ------------------------------------------------------------ fájlok
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_spec(spec):
    """Kulcssorrend: a szerződés mezői, az options a DEFAULTS sorrendjében; az ismeretlen felső szintű mezők a
    végén változatlanul (a fogyasztó figyelmen kívül hagyja, de mentéskor nem vész el)."""
    out = collections.OrderedDict()
    for k in _TOP_ORDER:
        if k in spec:
            out[k] = copy.deepcopy(spec[k])
    for k in spec:
        if k not in out:
            out[k] = copy.deepcopy(spec[k])
    if isinstance(out.get("data"), dict):
        d = out["data"]
        out["data"] = collections.OrderedDict([(k, d[k]) for k in ("path", "sha256") if k in d] +
                                              [(k, v) for k, v in d.items() if k not in ("path", "sha256")])
    if isinstance(out.get("options"), dict):
        o = out["options"]
        out["options"] = collections.OrderedDict([(k, o[k]) for k in DEFAULTS if k in o] +
                                                 [(k, v) for k, v in o.items() if k not in DEFAULTS])
    if isinstance(out.get("filters"), dict):
        f = out["filters"]
        out["filters"] = collections.OrderedDict([(k, f[k]) for k in FILTER_DESTS if k in f] +
                                                 [(k, v) for k, v in f.items() if k not in FILTER_DESTS])
    return dict(out)


def spec_bytes(spec):
    """A mentett spec bájtjai (UTF-8, 2 szóközös behúzás, záró újsor; NaN/Infinity tiltott)."""
    return (json.dumps(canonical_spec(spec), ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def spec_sha256(spec):
    """A spec azonosító hash-e: a save_spec által írt bájtoké (így egy mentett spec fájl-hash-ével egyezik)."""
    return hashlib.sha256(spec_bytes(spec)).hexdigest()


def _atomic_write(path, data):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _no_dupes(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise SpecError("ismétlődő kulcs a JSON-ban: %r" % (k,))
        seen[k] = v
    return seen


def _no_constant(name):
    raise SpecError("a JSON nem tartalmazhat %s értéket (hiányzó szám: null)" % name)


def _read_spec(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        spec = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_no_dupes, parse_constant=_no_constant)
    except UnicodeDecodeError as exc:
        raise SpecError("a spec nem UTF-8 kódolású (%s): %s" % (path, exc))
    except json.JSONDecodeError as exc:
        raise SpecError("a spec nem érvényes JSON (%s): %s" % (path, exc))
    errs = validate_spec(spec)
    if errs:
        raise SpecError("érvénytelen elemzési spec (%s): %s" % (path, "; ".join(errs)))
    return spec, hashlib.sha256(raw).hexdigest()


def load_spec(path):
    """Spec beolvasása és ellenőrzése (SpecError a magyar hibalistával)."""
    return _read_spec(path)[0]


def save_spec(path, spec):
    """Ellenőrzés után atomi írás (ideiglenes fájl + os.replace) kanonikus alakban; → a fájl sha256-ja."""
    _require_valid(spec)
    data = spec_bytes(spec)
    _atomic_write(path, data)
    return hashlib.sha256(data).hexdigest()


def spec_relpath(name):
    """A projektbeli spec-fájl helye: 05_elemzes/specs/<név>.json."""
    if not _match("name", name):
        raise SpecError("érvénytelen spec-név: %r" % (name,))
    return "%s/%s.json" % (SPEC_DIR, name)


def check_data_sha256(spec, path):
    """Ha a spec rögzíti az adat hash-ét, a fájlnak egyeznie kell (különben elavult a spec: X001)."""
    want = (spec.get("data") or {}).get("sha256")
    if want:
        got = sha256_file(path)
        if got != want:
            raise SpecError("az adatfájl (%s) tartalma megváltozott a spec rögzítése óta (sha256 %s… ≠ a specben "
                            "%s…); ellenőrizd az adatot, majd frissítsd a spec data.sha256 mezőjét" % (
                                path, got[:12], want[:12]))


# ------------------------------------------------------------ CLI: --spec
def apply_spec(a, argv=None):
    """'analyze --spec FILE': a namespace kitöltése a specből (data, szűrők, minden opció), hogy a cmd_analyze
    további része változatlanul fusson. Elemzési kapcsoló a --spec mellett hiba (csak RUN_DESTS). A data.path a
    --project-hez, ennek hiányában a munkakönyvtárhoz relatív. → a spec (a.spec_doc, a.spec_sha256 is beáll)."""
    argv = argv if argv is not None else getattr(a, "_argv", None)
    bad = spec_conflicts(argv) if argv is not None else _default_conflicts(a)
    if bad:
        raise SpecError("a --spec mellett elemzési kapcsoló nem adható meg (a spec határozza meg): %s; megengedett: "
                        "%s" % (", ".join(bad), ", ".join("--" + d.replace("_", "-") for d in RUN_DESTS
                                                          if d != "spec")))
    spec, digest = _read_spec(a.spec)
    ns = namespace_from_spec(spec, getattr(a, "project", None))
    if not os.path.isfile(ns.data):
        raise SpecError("a spec adatfájlja nem található: %s (a data.path a --project mappához, ennek hiányában a "
                        "munkakönyvtárhoz relatív)" % ns.data)
    check_data_sha256(spec, ns.data)
    for k, v in vars(ns).items():
        if k not in RUN_DESTS and k != "func":
            setattr(a, k, v)
    a.spec_doc, a.spec_sha256 = spec, digest
    return spec


# ------------------------------------------------------------ futás-leíró (szk.ma.run/v1)
def _utc(t):
    if t is None:
        return None
    if isinstance(t, str):
        return t
    if t.tzinfo is not None:
        t = t.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_id(now_utc, data_sha256):
    """'20261004T211200Z-a1f3c2': UTC időbélyeg másodpercre + az adat-sha256 első 6 jegye."""
    h = str(data_sha256 or "").lower()
    if not re.fullmatch(r"[0-9a-f]{6,64}", h):
        raise ValueError("run_id: érvénytelen adat-sha256: %r" % (data_sha256,))
    if now_utc.tzinfo is not None:
        now_utc = now_utc.astimezone(datetime.timezone.utc)
    return now_utc.strftime("%Y%m%dT%H%M%SZ") + "-" + h[:6]


ANALYSIS_DIR = "05_elemzes"


def _relocated(path, root):
    """A projektnapló abszolút útja (outdir) a mostani projektgyökérben: ha az út nem létezik (a projektet
    áthelyezték), az utolsó projektmappa-névtől (03_adatok, 05_elemzes …) kezdődő része a gyökérhez illesztve."""
    from .projekt import FOLDERS
    if os.path.exists(path) or root is None:
        return path
    parts = re.split(r"[\\/]+", str(path))
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] in FOLDERS:
            cand = os.path.join(root, *parts[i:])
            return cand if os.path.exists(cand) else path
    return path


def outcome_dir(outcome):
    """A kimenet commit-futásainak mappaneve (05_elemzes/<ez>/<run_id>): a kimenet-azonosító, ha névalakú, különben
    a slugja."""
    return outcome if _match("name", outcome or "") else _slug(outcome)


def journal_run_dirs(project_root, with_data=False):
    """A projektnapló (projekt.sqlite, csak olvasva) futásainak kimeneti mappái, naplósorrendben (áthelyezett
    projektnél a gyökérhez igazítva); with_data=True: [(mappa, a futás adatfájlja, az adat sha256-ja)]. Napló nélkül
    vagy olvashatatlan naplónál üres lista."""
    import sqlite3
    from pathlib import Path
    p = os.path.join(project_root, "projekt.sqlite")
    if not os.path.isfile(p):
        return []
    try:
        con = sqlite3.connect(Path(os.path.abspath(p)).as_uri() + "?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute("SELECT outdir, data_path, data_sha256 FROM run WHERE outdir IS NOT NULL "
                           "ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = [(_relocated(d, project_root), _relocated(f, project_root) if isinstance(f, str) and f else None,
            h if isinstance(h, str) and h else None) for d, f, h in rows if isinstance(d, str) and d]
    return out if with_data else [d for d, _, _ in out]


def project_run_files(project_root):
    """A projekt futás-leírói (run.json): 05_elemzes/<kimenet>/<futás>/run.json ('nested', a terv elrendezése),
    05_elemzes/<kimenet>/run.json ('flat') és a projektnapló futásainak mappái ('journal'; pl. a --project melletti
    alapértelmezett <adatmappa>/eredmeny). → [(abszolút út, elrendezés, kimenet-mappa vagy None, a napló ide írt
    futásainak [(adatfájl, adat-sha256)] listája)] ismétlés nélkül. A naplóbeli adatokkal a hívó ellenőrizheti, hogy
    a projekten kívüli mappában a projekt futása van-e (egy másik projekt futása felülírhatta)."""
    out, seen = [], {}

    def add(path, layout, folder, logged=None):
        key = os.path.normcase(os.path.realpath(path))
        if key in seen:
            if logged:
                out[seen[key]][3].append(logged)
        elif os.path.isfile(path):
            seen[key] = len(out)
            out.append((os.path.abspath(path), layout, folder, [logged] if logged else []))
    base = os.path.join(project_root, ANALYSIS_DIR)
    if os.path.isdir(base):
        for oid in sorted(os.listdir(base)):
            odir = os.path.join(base, oid)
            if oid == "specs" or not os.path.isdir(odir):
                continue
            add(os.path.join(odir, "run.json"), "flat", oid)
            for sub in sorted(os.listdir(odir)):
                if os.path.isdir(os.path.join(odir, sub)):
                    add(os.path.join(odir, sub, "run.json"), "nested", oid)
    for d, data, sha in journal_run_dirs(project_root, with_data=True):
        add(os.path.join(d, "run.json"), "journal", None, (data, sha))
    return out


def project_run_ids(project_root):
    """A projekt futás-leíróiban már szereplő run_id-k halmaza (project_run_files)."""
    ids = set()
    for path, _, _, _ in project_run_files(project_root):
        try:
            with open(path, "rb") as fh:
                doc = json.loads(fh.read().decode("utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("run_id"), str):
            ids.add(doc["run_id"])
    return ids


def unique_run_id(started, data_sha256, project_root=None, base_dir=None, taken=None):
    """Generált, a projektben még nem használt run_id: ha a run_id(started) foglalt (egy futás-leíró már viseli,
    vagy a <base_dir>/<run_id> mappa létezik), az időbélyeg másodpercenként előrelép (az alak marad).
    base_dir: a mappa atomi létrehozása (os.mkdir) le is foglalja az azonosítót, így két egyidejű commit sem kapja
    ugyanazt. → (run_id, a lefoglalt mappa vagy None)."""
    taken = set(taken or ()) | (project_run_ids(project_root) if project_root is not None else set())
    t = started
    for _ in range(86400):
        rid = run_id(t, data_sha256)
        if rid not in taken:
            if base_dir is None:
                return rid, None
            os.makedirs(base_dir, exist_ok=True)
            d = os.path.join(base_dir, rid)
            try:
                os.mkdir(d)
                return rid, d
            except FileExistsError:
                pass
        t = t + datetime.timedelta(seconds=1)
    raise SpecError("nem található szabad run_id (%s)" % run_id(started, data_sha256))


def _clean(obj):
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def display_text(est, lo, hi):
    """A plot_data v2 summaries[].display_text-jével azonos szöveg (plots.display_text): 'hu' = az ábra és a
    riport szövege ('0.49 [0.33; 0.73]'), 'en' = U+2212 mínusszal — így a run.json, a plot_data.json és az SVG
    ugyanazt a számot mutatja (számhűség-lánc, terv 6.7)."""
    from .plots import display_text as _display_text
    return _display_text(est, lo, hi)


def _primary(out):
    pr = out.get("primary")
    if pr is None:
        return None
    bt = (out.get("back_transformed") or {}).get("estimate_ci") or [None, None, None]
    return {"model": out.get("primary_model"), "display_text": display_text(*bt[:3])}


def run_descriptor(out, mode="explore", run_id=None, spec=None, spec_path=None, spec_hash=None,
                   equivalent_argv=None, data_file=None, data_sha256=None, data_rows=None, files=None,
                   project_root=None, started=None, finished=None, elapsed_ms=None, client_seq=None):
    """szk.ma.run/v1 a pipeline.run kimenetéből. mode: 'commit' (run_id kötelező) vagy 'explore' (run_id null).
    files: a write_outputs {fájlnév: út} dict-je (szerep + relatív út + sha256). Az utak a projektgyökérhez
    relatívak, ha azon belül vannak, különben abszolútak. A spec-hash (spec_hash hiányában): spec_path esetén a
    fájlé, különben a spec kanonikus bájtjaié (spec_sha256()). data_file: a ténylegesen olvasott adatfájl."""
    if mode not in MODES:
        raise ValueError("mode: %r (lehetséges: %s)" % (mode, ", ".join(MODES)))
    if mode == "commit" and not _match("run_id", run_id):
        raise ValueError("commit-futáshoz érvényes run_id kell (pl. 20261004T211200Z-a1f3c2), kapott: %r" % (run_id,))
    if mode == "explore" and run_id is not None:
        raise ValueError("explore-futásnak nincs run_id-je")
    d = collections.OrderedDict()
    d["schema"] = RUN_SCHEMA
    d["run_id"] = run_id
    d["mode"] = mode
    sp = collections.OrderedDict()
    sp["path"] = _disp_path(spec_path, project_root) if spec_path else None
    if spec_hash is None:
        if spec_path and os.path.isfile(spec_path):
            spec_hash = sha256_file(spec_path)
        elif spec is not None and not validate_spec(spec):
            spec_hash = spec_sha256(spec)
    sp["sha256"] = spec_hash
    sp["name"] = (spec or {}).get("name")
    sp["parent"] = (spec or {}).get("parent")
    d["spec"] = sp
    d["equivalent_argv"] = list(equivalent_argv) if equivalent_argv is not None else None
    d["engine_version"] = __version__
    dd = collections.OrderedDict()
    dd["path"] = _disp_path(data_file, project_root) if data_file else ((spec or {}).get("data") or {}).get("path")
    if data_sha256 is None and data_file and os.path.isfile(data_file):
        data_sha256 = sha256_file(data_file)
    dd["sha256"] = data_sha256
    dd["rows"] = data_rows if data_rows is not None else (out.get("input") or {}).get("n_rows")
    d["data"] = dd
    if files:
        fs = collections.OrderedDict()
        names = [n for n in _FILE_ROLES if n in files] + sorted(n for n in files if n not in _FILE_ROLES)
        for n in names:
            role = _FILE_ROLES.get(n) or re.sub(r"[^a-z0-9_]+", "_", os.path.splitext(n)[0].lower())
            fs[role] = collections.OrderedDict((("path", _disp_path(files[n], project_root)),
                                                ("sha256", sha256_file(files[n]))))
        d["files"] = fs
    d["k"] = (out.get("effect_sizes") or {}).get("k")
    d["primary"] = _primary(out)
    d["validation_summary"] = dict((out.get("validation") or {}).get("summary") or {})
    if client_seq is not None:
        d["client_seq"] = client_seq
    if elapsed_ms is not None:
        d["elapsed_ms"] = elapsed_ms
    if started is not None:
        d["started"] = _utc(started)
    if finished is not None:
        d["finished"] = _utc(finished)
    return _clean(d)


def cli_run_id(a, started):
    """A CLI-futás run_id-je: --run-id, vagy commit-futásnál (--project) generált; különben None (explore)."""
    rid = getattr(a, "run_id", None)
    if rid:
        if not _match("run_id", rid):
            raise SpecError("--run-id: érvénytelen azonosító %r (alak: 20261004T211200Z-a1f3c2)" % (rid,))
        return rid
    if getattr(a, "project", None):
        return unique_run_id(started, sha256_file(a.data), a.project)[0]
    return None


def cli_equivalent_argv(a, outdir, prog="ma.py"):
    """Egyenértékű parancssor a run.json-ba. --project-tel a projektgyökérből futtatható (relatív utak,
    '--project .'), anélkül abszolút utakkal; --spec-futásnál a --spec alak."""
    root = getattr(a, "project", None)
    if getattr(a, "spec", None):
        argv = [prog, "analyze", "--spec", _disp_path(a.spec, root) if root else os.path.abspath(a.spec)]
        argv += ["--out", _disp_path(outdir, root) if root else os.path.abspath(outdir)]
        return argv + (["--project", "."] if root else [])
    opts = options_from_args(a)
    try:
        flags = _option_tokens(opts)
    except SpecError:
        flags = None
    if root:
        rel = _disp_path(a.data, root)
        if flags is not None and not os.path.isabs(rel):
            return [prog, "analyze"] + _pair("--data", rel) + flags + \
                _filter_tokens({d: getattr(a, d, None) for d in FILTER_DESTS}) + \
                _pair("--out", _disp_path(outdir, root)) + ["--project", "."]
    argv = [prog, "analyze"] + _pair("--data", os.path.abspath(a.data)) + (flags or []) + \
        _filter_tokens({d: getattr(a, d, None) for d in FILTER_DESTS}) + _pair("--out", os.path.abspath(outdir))
    return argv + (["--project", os.path.abspath(root)] if root else [])


def describe_cli_run(a, out, paths, outdir, started, finished, rid=None):
    """A cmd_analyze futás-leírója (run.json / --json-summary). commit, ha van run_id (--run-id vagy --project),
    különben explore; a files a ténylegesen kiírt kimenetek — csak commit-futásnál (4.5: explore-futásnak nincs
    files mezője; a kimenetek helyét a --out és a parancs kimenete adja)."""
    root = getattr(a, "project", None)
    rid = rid if rid is not None else cli_run_id(a, started)
    spec = getattr(a, "spec_doc", None)
    digest = getattr(a, "spec_sha256", None)
    if spec is None:
        try:
            spec = spec_from_namespace(a, root) if root else None
        except SpecError:
            spec = None
        if spec is None:
            spec = {"name": _slug(os.path.splitext(os.path.basename(a.data))[0]), "parent": None}
    elapsed = int(round((finished - started).total_seconds() * 1000)) if started and finished else None
    return run_descriptor(out, mode="commit" if rid else "explore", run_id=rid, spec=spec,
                          spec_path=getattr(a, "spec", None), spec_hash=digest,
                          equivalent_argv=cli_equivalent_argv(a, outdir), data_file=a.data,
                          files=paths if rid else None,
                          project_root=root, started=started, finished=finished, elapsed_ms=elapsed)


def run_json_bytes(desc):
    return (json.dumps(_clean(desc), ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")


def write_run_json(outdir, desc):
    """run.json atomi írása a results.json mellé; → az út."""
    p = os.path.join(outdir, "run.json")
    _atomic_write(p, run_json_bytes(desc))
    return p


# ------------------------------------------------------------ generált sémák
def analysis_spec_schema():
    """A szk.ma.analysis-spec/v1 JSON Schema (2020-12), az options a DEFAULTS-ból és az argparse-ból generálva."""
    props = collections.OrderedDict()
    for key, o in _meta().opts.items():
        t = o.json_type
        if t == "enum":
            s = {"enum": list(o.choices) + ([None] if o.default is None and not o.required else [])}
        elif t == "array":
            item = {"type": "string"}
            if o.kind == "csv":
                item.update({"minLength": 1, "pattern": r"^[^,\s](?:[^,]*[^,\s])?$"})
            s = {"type": "array", "items": item}
        elif t == "any":
            s = {}
        else:
            s = {"type": [t, "null"] if o.default is None and not o.required else t}
        if key == "level":
            s.update({"exclusiveMinimum": 0, "exclusiveMaximum": 1})
        minimum = getattr(o.act.type, "minimum", None) if o.act is not None else None
        if minimum is not None and t in ("number", "integer"):
            s["minimum"] = minimum
        if o.default is not None or not o.required:
            s["default"] = copy.deepcopy(o.default)
        if o.help:
            s["description"] = ("%s — %s" % (o.flag, o.help))
        elif o.act is None:
            s["description"] = "csak specben (nincs parancssori kapcsolója)"
        props[key] = s
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": SCHEMA_ID,
        "title": "Elemzési spec (felület → motor; analyze --spec)", "type": "object",
        "required": ["schema", "name", "outcome", "data", "options"],
        "properties": {
            "schema": {"const": SCHEMA},
            "name": {"type": "string", "pattern": NAME_PATTERN},
            "outcome": {"type": "string"},
            "purpose": {"enum": list(PURPOSES)},
            "prespecified": {"type": "boolean"},
            "protocol_ref": {"type": ["string", "null"]},
            "parent": {"type": ["string", "null"], "pattern": NAME_PATTERN,
                       "description": "gyermek-futásnál az elsődleges spec neve"},
            "data": {"type": "object", "required": ["path"], "properties": {
                "path": {"type": "string", "pattern": RELPATH_PATTERN},
                "sha256": {"type": ["string", "null"], "pattern": SHA256_PATTERN,
                           "description": "commitnál kötelező; eltérésnél a motor elavultnak jelzi (409)"}}},
            "options": {"type": "object", "additionalProperties": False, "required": list(REQUIRED_OPTIONS),
                        "description": "PONTOSAN a pipeline.DEFAULTS kulcsai (generált)", "properties": props},
            "filters": {"type": "object", "additionalProperties": False, "properties": {
                d: {"type": "array", "items": {"type": "string", "pattern": FILTER_PATTERN}} for d in FILTER_DESTS}},
            "kb_refs": {"type": "array", "items": {"type": "string"}},
        }}


def run_schema():
    """A szk.ma.run/v1 JSON Schema (2020-12) — a metaelemzes/contracts/ma.run.v1.schema.json (egy igazságforrás)."""
    from . import contracts
    return contracts.load("ma.run", 1)


def _main(argv=None):
    """Fejlesztői segéd: python -m metaelemzes.spec schema|run-schema|problems."""
    cmd = (argv if argv is not None else sys.argv[1:] or ["schema"])[0]
    obj = {"schema": analysis_spec_schema, "run-schema": run_schema, "problems": mapping_problems}[cmd]()
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
