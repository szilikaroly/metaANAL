# -*- coding: utf-8 -*-
"""Lánc-visszajátszás (terv 8.2): a forrásokból vett kidolgozott példák (tests/source_cases.py, az aktív esetek)
a munkapad TELJES útján — saját táblaíró → motor-homlokzat → nézetmodell —, őszinte lefedettségi jelentéssel.

Esetenként:

1. **Bemenet.** Az eset ``rows``-ából a munkapad SAJÁT táblaírója (``ma_gui.store.ProjectStore.save_table``, ugyanaz,
   mint a ``PUT /api/table``) ír CSV-t egy ideiglenes projektbe, szándékosan magyar konvencióval (``;`` tagoló,
   tizedesvessző, row_uid oszlop), így a motor parszolása is próbára kerül. A számokat a teszt szövegként adja át
   (``repr`` → a pont helyett vessző): a lebegőpontos érték bitre visszaolvasható. A ``input_storage: float32`` esetek
   a forrásszoftver tárolt (egyszeres pontosságú) értékeit kapják — ugyanazt, amit a közvetlen motorteszt.
2. **Spec.** ``szk.ma.analysis-spec/v1`` a ``measure``-ből, az ``options``-ból és a ``call_args``-ból, hívástípusonkénti
   leképezéssel (``PLANS``). Minden paraméter, amelyet a közvetlen hívás használ, a motor-függvény saját
   alapértelmezésével kerül a specbe, ha az eset nem adja meg (a pipeline alapértelmezései néhol mások: pl. a
   meta-regresszió próbája knha, a közvetlen hívásé z) — így a két út ugyanazt a modellt számolja.
3. **Futtatás.** ``api.analyze(spec, mode='explore', project_root=…)`` — a ``POST /api/analyze`` explore-útja: a motor
   a mentett CSV-t olvassa, a nézetmodell (``plot`` = szk.ma.plot/v2, ``results``) a felületé.
4. **Értékelés.** Az ``expected`` útvonalakat fordítótábla képezi a nézetmodellre: ahol a szám a ``plot/v2``-ben is
   ott van (összesítő becslés és CI, PI, heterogenitás, vizsgálatonkénti y és súly, alcsoport-összesítők, LFK és a
   Doi-pontok), onnan; különben a ``results``-ból (a ``results.json`` tartalma). Két ellenőrzés fut minden várt
   értékre: (a) a ``source_cases.check_expectation`` UGYANAZZAL a tűréssel, mint a közvetlen motorteszt; (b) a
   nézetmodell száma egyenlő a közvetlen motorhívás (``source_cases.execute``) számával (relatív 1e-12) — a lánc
   egyetlen számot sem változtat.
5. **Lefedettség.** ``tests/gui/chain_replay_coverage.md``: esetenként és hívástípusonként a leképezett és a nem
   leképezhető ellenőrzések, okkal. A jelentést ez a modul állítja elő (``--write-report``); a teszt ellenőrzi, hogy a
   verziókövetett jelentés naprakész.

A PRISMA-esetek a ``PUT /api/prisma/manual`` motor-hívásán (``api.prisma_check``; a dobozok nyers szövegként, mint a
felületről), az átváltás az ``api.convert``-en (``POST /api/convert``) fut — ezek nem elemzés-futások, a jelentés
külön sorolja őket.

Futtatás: ``python3 tests/gui/test_chain_replay.py`` (unittest) · ``python3 tests/gui/test_chain_replay.py
--write-report`` (a jelentés újraírása)."""
import collections
import copy
import math
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTS)
for _p in (ROOT, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import source_cases as SC  # noqa: E402
from metaelemzes import api  # noqa: E402
from metaelemzes import prisma as PR  # noqa: E402  (csak a PRISMA-útvonalnevek feloldásához)
from ma_gui import store  # noqa: E402

REPORT = os.path.join(HERE, "chain_replay_coverage.md")
SPEC_SCHEMA = "szk.ma.analysis-spec/v1"
REL_TOL = 1e-12
ABS_TOL = 1e-15

# --------------------------------------------------------------------------- leképezés
# hatásméret-számítás (E.compute) paraméterei, amelyeket a pipeline átad: eset-opció → spec-opció (azonos név) és
# a közvetlen hívás alapértéke (E.compute szignatúrája)
ES_DEFAULTS = collections.OrderedDict((
    ("smd_vtype", "LS"), ("j_method", "exact"), ("cc", 0.5), ("cc_to", "only0"), ("drop00", None),
    ("md_vtype", "unequal"), ("glass_vtype", "METAN"), ("gen_smd_vtype", None)))
ES_UNMAPPED = {"ci_level": "E.compute ci_level — a pipeline nem adja át (a CI-szint a modellé)",
               "skip_labels": "E.compute skip_labels — a pipeline a validálás alapján dönt"}

# hívástípus → (call_args-név → (spec-opció, értékfordító | None), a közvetlen hívás alapértékei a specbe,
#               a pipeline-ban nem leképezhető call_args → ok)
PLANS = {
    "effect_sizes": ({}, {}, {}),
    "meta_analysis": (
        {"model": ("model", None), "tau2_method": ("tau2", None), "ci_method": ("ci", None), "level": ("level", None),
         "pi_method": ("pi", None), "h_centre": ("h_centre", None)},
        {"model": "random", "tau2": None, "ci": None, "level": 0.95, "pi": "t_k-2", "h_centre": "truncated"},
        {"tau2_fixed": "tau2_fixed — rögzített τ²-re nincs pipeline-opció (motor-PR-jelölt)"}),
    "subgroup": (
        {"group_col": ("subgroup", None), "model": ("model", None), "tau2_method": ("tau2", None),
         "ci_method": ("ci", None), "level": ("level", None), "common_tau2": ("common_tau2", None),
         "pi_method": ("pi", None), "h_centre": ("h_centre", None)},
        {"subgroup": "subgroup", "model": "random", "tau2": None, "ci": None, "level": 0.95, "common_tau2": False,
         "pi": "t_k-2", "h_centre": "truncated"},
        {}),
    "meta_regression": (
        {"moderators": ("moderators", None), "tau2_method": ("metareg_tau2", None), "test": ("metareg_test", None),
         "level": ("level", None), "robust": ("metareg_robust", None)},
        {"metareg_tau2": "REML", "metareg_test": "z", "level": 0.95, "metareg_robust": False},
        {}),
    "egger": ({"ci_dist": ("egger_ci_dist", None), "level": ("level", None)},
              {"egger_ci_dist": "t", "level": 0.95}, {}),
    "begg": ({"method": ("begg_method", None), "continuity": ("begg_continuity", None)},
             {"begg_method": "auto", "begg_continuity": False}, {}),
    "trimfill": (
        {"model": ("model", None), "tau2_method": ("tau2", None), "estimator": ("trimfill_estimator", None),
         "ci_method": ("ci", None), "level": ("level", None), "trim_model": ("trimfill_trim_model", None),
         "h_centre": ("h_centre", None)},
        # a pipeline a korrigált modellt az elsődleges modell CI-módszerével illeszti: a közvetlen hívás 'z'
        # alapértéke ezért a spec ci-jébe kerül
        {"model": "random", "tau2": None, "trimfill_estimator": "L0", "ci": "z", "level": 0.95,
         "trimfill_trim_model": None, "h_centre": "truncated"},
        {"side": "trimfill side — az oldalt a pipeline mindig maga választja; nincs pipeline-opció (motor-PR-jelölt)",
         "maxiter": "trimfill maxiter — nincs pipeline-opció"}),
    "lfk": ({}, {}, {}),
    "doi": ({}, {}, {}),
    "harbord": ({}, {}, {}),
    "peters": ({"cc": ("cc", None)}, {"cc": 0.5}, {}),
}
BIAS_KEY = {"egger": "egger", "begg": "begg", "trimfill": "trimfill", "lfk": "lfk", "doi": "lfk",
            "harbord": "harbord", "peters": "peters"}
FACADE_CALLS = {"prisma_check": "prisma", "prisma_flow": "prisma", "conversion": "convert"}
# a conversions-függvény → az api.convert fajtája (POST /api/convert) és a bemenet-nevek fordítása
CONVERT_KINDS = {"se_from_p": ("p_to_se", {"estimate": "estimate", "p": "p", "log_scale": "log", "df": "df"}, "se")}


VALIDATION_GATE = "validálási kapu"
# a mért, ismert okok (a jelentés okonként sorolja; új ok = a teszt elbukik, hogy a lefedettség ne csökkenhessen
# észrevétlenül)
KNOWN_UNMAPPED = ("tau2_fixed —", "trimfill side —", "meta-regresszió moderátor nélkül", VALIDATION_GATE + ":")
MIN_MAPPED_SHARE = 0.90          # mért: 92,1% (2026-10-05); a terv 8.2 felső korlátja 96,8% volt — lásd a jelentést
_RULES = {}


def rule_title(code):
    if not _RULES:
        for r in api.rules_export():
            _RULES[r["id"]] = (r.get("title") or {}).get("hu") or ""
    return _RULES.get(code, "")


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def plan(case):
    """→ (út: 'analyze' | 'prisma' | 'convert' | None, spec-opciók | facade-kérés | None, nem leképezhető ok | None)."""
    call = case["call"]
    if call in FACADE_CALLS:
        if FACADE_CALLS[call] == "convert":
            a = dict(case.get("call_args") or {})
            kind = CONVERT_KINDS.get(a.pop("function", None))
            if kind is None:
                return None, None, "conversion: a függvénynek nincs api.convert-fajtája"
            names = kind[1]
            bad = [k for k in a if k not in names]
            if bad:
                return None, None, "conversion: nem leképezhető bemenet: %s" % ", ".join(bad)
            return "convert", (kind[0], {names[k]: v for k, v in a.items()}, kind[2]), None
        extra = [k for k in (case.get("call_args") or {}) if k not in ("template", "composer")]
        if extra:
            return None, None, "prisma: nem leképezhető call_args: %s" % ", ".join(extra)
        return "prisma", None, None
    if call not in PLANS:
        return None, None, "%s: nincs elemzés-futás (a munkapad nem hívja)" % call
    amap, defaults, unmapped = PLANS[call]
    opts = {"measure": case.get("measure") or "GEN"}
    for k, dv in ES_DEFAULTS.items():
        opts[k] = dv
    for k, v in (case.get("options") or {}).items():
        if k in ES_UNMAPPED:
            return None, None, ES_UNMAPPED[k]
        if k not in ES_DEFAULTS:
            return None, None, "hatásméret-opció %s — nincs pipeline-opció" % k
        opts[k] = v
    opts.update(copy.deepcopy(defaults))
    for k, v in (case.get("call_args") or {}).items():
        if k in unmapped:
            return None, None, unmapped[k]
        if k not in amap:
            return None, None, "%s: nem leképezhető call_args: %s" % (call, k)
        name, conv = amap[k]
        opts[name] = conv(v) if conv else v
    if call == "meta_regression":
        if opts.get("metareg_test") == "robust_hc1":         # moderators.meta_regression: = test='z', robust=True
            opts["metareg_test"], opts["metareg_robust"] = "z", True
        if not opts.get("moderators"):
            return None, None, ("meta-regresszió moderátor nélkül (csak tengelymetszet) — a pipeline moderátor "
                                "nélkül nem illeszt meta-regressziót")
    if call == "meta_analysis" and opts["model"] == "ivhet" and opts.get("ci") not in (None, "z"):
        return None, None, "ivhet: a pipeline nem adja át a ci_method-ot (az IVhet CI-je mindig z)"
    if call == "meta_analysis" and opts["model"] == "ivhet" and opts.get("pi") != "t_k-2":
        return None, None, "ivhet: a pipeline nem adja át a pi_method-ot"
    if call in BIAS_KEY and len(case.get("rows") or ()) < 3:
        return None, None, "k < 3: a pipeline torzítás-tesztet nem futtat"
    if call == "subgroup":
        col = opts["subgroup"]
        if any(r.get(col) in (None, "") for r in case["rows"]):
            return None, None, "alcsoport: hiányzó csoportérték (a pipeline ilyenkor kihagyja az alcsoport-elemzést)"
    return "analyze", opts, None


# --------------------------------------------------------------------------- bemenet: a munkapad táblaírója
def cell_text(v):
    """Eset-érték → cellaszöveg, magyar konvencióval (tizedesvessző); a float bitre visszaolvasható (repr)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "igen" if v else "nem"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v).replace(".", ",")
    return str(v)


def write_case_table(st, rel, rows):
    header = []
    for r in rows:
        for k in r:
            if k not in header:
                header.append(k)
    if "study" in header:
        header.remove("study")
        header.insert(0, "study")
    body = [{"row_uid": None, "cells": [cell_text(r.get(h)) for h in header]} for r in rows]
    st.save_table(rel, header, body, if_match=None, fmt=store.CsvFormat(delimiter=";", decimal_mark=","))
    return header


def make_spec(name, rel, opts):
    return {"schema": SPEC_SCHEMA, "name": name, "outcome": "o1", "purpose": "primary", "prespecified": False,
            "protocol_ref": None, "parent": None, "data": {"path": rel}, "options": opts,
            "filters": {"include": [], "exclude": []}, "kb_refs": []}


# --------------------------------------------------------------------------- nézetmodell → az eredmény tükre
def _primary_summary(plot):
    return [s for s in plot["summaries"] if s.get("primary")][0]


def _plot_studies(view):
    """A plot/v2 vizsgálatai az elemzett sorrendben (results.effect_sizes.row_index szerint)."""
    plot = view["plot"]
    by_ix = {s["row_index"]: s for s in plot["studies"]}
    order = view["results"]["effect_sizes"].get("row_index") or []
    if len(order) == len(plot["studies"]) and all(i in by_ix for i in order):
        return [by_ix[i] for i in order]
    return list(plot["studies"])


def mirror(call, view):
    """A közvetlen motor-eredmény alakja (az expected útvonalakhoz) a nézetmodellből: ahol a szám a plot/v2-ben
    is ott van, onnan."""
    res = view["results"]
    plot = view["plot"]
    if call == "effect_sizes":
        prim = res["primary"]
        studies = _plot_studies(view)
        return {"yi": [s["y"] for s in studies], "vi": list(prim["vi"]), "_se_plot": [s["se"] for s in studies],
                "labels": [s["label"] for s in studies]}
    if call == "meta_analysis":
        m = copy.deepcopy(res["primary"])
        ps = _primary_summary(plot)
        for k in ("estimate", "ci_lower", "ci_upper", "pi_lower", "pi_upper"):
            m[k] = ps[k]
        m["k"] = ps["k"]
        het = plot["heterogeneity"]
        m["Q"], m["Q_df"], m["p_Q"], m["I2"], m["tau2"] = het["Q"], het["df"], het["p"], het["I2"], het["tau2"]
        m["heterogeneity"] = dict(m.get("heterogeneity") or {}, I2_from_tau2=het["I2_from_tau2"])
        studies = _plot_studies(view)
        m["weights_pct"] = [s["weight_pct"] for s in studies]
        m["yi"] = [s["y"] for s in studies]
        return m
    if call == "subgroup":
        sg = copy.deepcopy(res["subgroups"])
        groups = collections.OrderedDict((g["group"], g) for g in sg["groups"])
        for sec in plot.get("sections") or []:
            g = groups.get(sec["title"])
            if g is not None:
                for k in ("estimate", "ci_lower", "ci_upper"):
                    g[k] = sec["summary"][k]
                g["k"] = sec["k"]
        sg["groups"] = groups
        if plot.get("subgroup_test"):
            st = plot["subgroup_test"]
            sg["Q_between"], sg["df_between"], sg["p_between"] = st["Q"], st["df"], st["p"]
        return sg
    if call == "meta_regression":
        return res["metaregression"]
    if call in ("lfk", "doi"):
        lfk = copy.deepcopy(res["bias"]["lfk"])
        doi = plot.get("doi") or {}
        if doi.get("lfk") is not None:
            lfk["lfk"] = doi["lfk"]
        pts = doi.get("points") or []
        if len(pts) == len(lfk["points"]):
            for p_res, p_plot in zip(lfk["points"], pts):
                p_res["y"], p_res["abs_z"] = p_plot["x"], p_plot["abs_z"]
        return lfk
    return res["bias"][BIAS_KEY[call]]


# --------------------------------------------------------------------------- összevetés
def _close(a, b):
    if isinstance(a, list) or isinstance(b, list):
        return isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and all(
            _close(x, y) for x, y in zip(a, b))
    if isinstance(a, (str, bool)) or isinstance(b, (str, bool)) or a is None or b is None:
        return a == b
    a, b = float(a), float(b)
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _value(res, ex):
    if ex.get("expr") is not None:
        return SC.eval_expr(ex["expr"], res, ex.get("vars"))
    v = SC.resolve(res, ex["path"])
    return v if isinstance(v, (str, bool, list)) or v is None else float(v)


def evaluate(case, view_res, direct):
    """→ [(címke, ok, üzenet)] — (a) a várt érték a nézetmodellből ugyanazzal a tűréssel; (b) = a közvetlen hívás."""
    out = []
    for ex in case["expected"]:
        label, want, got, tol, ok, msg = SC.check_expectation(view_res, ex, case)
        if not ok:
            out.append((label, False, "várt %r (tol %r), a nézetmodellben %r %s" % (want, tol, got, msg)))
            continue
        try:
            mine, theirs = _value(view_res, ex), _value(direct, ex)
        except Exception as exc:                            # noqa: BLE001
            out.append((label, False, "összevetés: %s: %s" % (type(exc).__name__, exc)))
            continue
        if not _close(mine, theirs):
            out.append((label, False, "a nézetmodell %r ≠ a közvetlen motorhívás %r" % (mine, theirs)))
            continue
        out.append((label, True, ""))
    return out


def run_prisma(case):
    """A PRISMA-eset a felület útján: a dobozok nyers szövegként (ahogy a PRISMA-képernyő küldi) →
    api.prisma_check (a PUT /api/prisma/manual dry_run motor-hívása); az útvonalneveket a motor FlowCheck-
    aliasai oldják fel a facade kimenetéből."""
    flow = case.get("flow")
    if flow is None:
        flow = (case.get("rows") or [{}])[0]
    a = dict(case.get("call_args") or {})

    def raw(v):
        if isinstance(v, dict):
            return {k: raw(x) for k, x in v.items()}
        return cell_text(v) if _is_num(v) else v
    flow = {k: raw(v) for k, v in flow.items() if k != "study"}
    if a.get("composer"):
        flow = PR.from_composer(flow)
    d = api.prisma_check(flow, template=a.get("template"))
    return PR.FlowCheck(d["template"], d["counts"], d["reasons"], d["derived"], d["findings"])


def run_convert(req):
    kind, inputs, out_name = req
    body = {"schema": "szk.ma.convert-request/v1", "kind": kind,
            "inputs": {k: (("igen" if v else "nem") if isinstance(v, bool) else cell_text(v)) for k, v in
                       inputs.items()}}
    res = api.convert(body)
    return res["outputs"][out_name]


class Replay(object):
    """Az összes aktív eset visszajátszása egyetlen ideiglenes projektben → esetenkénti eredmények."""

    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_chain_"))
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, "03_adatok"))
        self.store = store.ProjectStore(self.proj)
        self.rows = []

    def close(self):
        shutil.rmtree(self.tmp, True)

    def run(self, cases):
        for i, case in enumerate(cases):
            self.rows.append(self.one(i, case))
        return self.rows

    def one(self, i, case):
        route, opts, reason = plan(case)
        rec = {"case": case, "route": route, "reason": reason, "checks": len(case["expected"]), "results": []}
        if route is None:
            return rec
        try:
            direct = SC.execute(case)
            if route == "prisma":
                view_res = run_prisma(case)
            elif route == "convert":
                view_res = run_convert(opts)
            else:
                rel = "03_adatok/c%03d.csv" % i
                write_case_table(self.store, rel, SC._rows(case))
                view = api.analyze(make_spec("c%03d" % i, rel, opts), mode="explore", project_root=self.proj)
                blocked = (view["results"].get("validation") or {}).get("blocked_studies") or []
                if blocked:
                    # a munkapad validálása 'error' szintűnek ítél sorokat, és a pipeline ezeket kizárja; a közvetlen
                    # motorhívás nem validál — ugyanaz a modell nem számolható (mért, nem feltételezett ok)
                    codes = sorted({f["code"] for f in view["results"]["validation"]["findings"]
                                    if f.get("severity") == "error"})
                    rec["route"] = None
                    rec["reason"] = ("%s: a munkapad validálása hibásnak ítéli és kizárja a sort (%s) — a közvetlen "
                                     "motorhívás nem validál" % (VALIDATION_GATE, "; ".join(
                                         "%s %s" % (c, rule_title(c)) for c in codes)))
                    return rec
                if view.get("plot") is None:
                    raise AssertionError("nincs plot/v2 (k = 0?): %s" % view.get("warnings"))
                view_res = mirror(case["call"], view)
                rec["view"] = view
                if case["call"] == "effect_sizes":
                    rec["extra"] = [("plot.studies[].se² = vi", _close([s * s for s in view_res["_se_plot"]],
                                                                       view_res["vi"]), "")]
            rec["results"] = evaluate(case, view_res, direct)
        except Exception as exc:                            # noqa: BLE001 — a jelentés és a teszt mutatja
            rec["results"] = [("*", False, "%s: %s" % (type(exc).__name__, exc))] * max(1, rec["checks"])
        return rec


# --------------------------------------------------------------------------- lefedettségi jelentés
def active_cases():
    return [c for c in SC.load_cases() if SC.is_active(c)]


def coverage(rows):
    by_call = collections.OrderedDict()
    reasons = collections.OrderedDict()
    tot = collections.Counter()
    for r in rows:
        c = r["case"]
        key = c["call"]
        b = by_call.setdefault(key, collections.Counter())
        b["cases"] += 1
        b["checks"] += r["checks"]
        tot["cases"] += 1
        tot["checks"] += r["checks"]
        if r["route"] is None:
            b["unmapped_cases"] += 1
            b["unmapped_checks"] += r["checks"]
            tot["unmapped_cases"] += 1
            tot["unmapped_checks"] += r["checks"]
            rr = reasons.setdefault(r["reason"], collections.Counter())
            rr["cases"] += 1
            rr["checks"] += r["checks"]
            continue
        green = sum(1 for _l, ok, _m in r["results"] if ok)
        b["mapped_cases"] += 1
        b["mapped_checks"] += r["checks"]
        b["green"] += green
        tot["mapped_cases"] += 1
        tot["mapped_checks"] += r["checks"]
        tot["green"] += green
    return by_call, reasons, tot


def _pct(a, b):
    return "%.1f%%" % (100.0 * a / b) if b else "—"


def report_text(rows):
    by_call, reasons, tot = coverage(rows)
    route_of = collections.Counter(r["route"] for r in rows if r["route"])
    lines = [
        "# Lánc-visszajátszás — lefedettségi jelentés (terv 8.2)",
        "",
        "Generálja: `python3 tests/gui/test_chain_replay.py --write-report` (a `test_chain_replay` teszt ellenőrzi, "
        "hogy naprakész). A forrás: `tests/source_cases.py` aktív esetei (`tests/reference/source_examples/`).",
        "",
        "Út: az eset sorai → a munkapad táblaírója (`ProjectStore.save_table`, `;` + tizedesvessző) → "
        "`api.analyze(explore)` → nézetmodell (`plot/v2` + `results`). Minden leképezett ellenőrzés kétszer: a várt "
        "érték ugyanazzal a tűréssel, mint a közvetlen motorteszt, és a nézetmodell száma = a közvetlen motorhívásé "
        "(relatív 1e-12). A PRISMA-esetek a `PUT /api/prisma/manual` motor-hívásán (`api.prisma_check`, a dobozok "
        "nyers szövegként), az átváltás az `api.convert`-en fut.",
        "",
        "## Összesítés",
        "",
        "| | Eset | Ellenőrzés |",
        "|---|---:|---:|",
        "| aktív (összes) | %d | %d |" % (tot["cases"], tot["checks"]),
        "| leképezett | %d | %d (%s) |" % (tot["mapped_cases"], tot["mapped_checks"],
                                         _pct(tot["mapped_checks"], tot["checks"])),
        "| — ebből zöld | | %d (%s a leképezettekből) |" % (tot["green"], _pct(tot["green"], tot["mapped_checks"])),
        "| nem leképezhető | %d | %d (%s) |" % (tot["unmapped_cases"], tot["unmapped_checks"],
                                              _pct(tot["unmapped_checks"], tot["checks"])),
        "",
        "Utak: elemzés-futás (`api.analyze` explore) %d eset; PRISMA (`api.prisma_check`) %d; átváltás "
        "(`api.convert`) %d." % (route_of["analyze"], route_of["prisma"], route_of["convert"]),
        "",
        "## Hívástípusonként",
        "",
        "| Hívás | Eset | Ellenőrzés | Leképezett eset | Leképezett ellenőrzés | Zöld | Nem leképezhető ellenőrzés |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for call, b in sorted(by_call.items()):
        lines.append("| `%s` | %d | %d | %d | %d | %d | %d |" % (
            call, b["cases"], b["checks"], b["mapped_cases"], b["mapped_checks"], b["green"], b["unmapped_checks"]))
    lines += ["", "## Nem leképezhető ellenőrzések okonként", "",
              "Ezek a közvetlen motortesztben (`tests/source_cases.py`) maradnak; a pipeline-opció hiánya "
              "motor-PR-jelölt. A terv 8.2 előzetes becslése 106 nem leképezhető ellenőrzés (96,8%-os felső korlát) "
              "volt; a mérés további okot talált: a **validálási kapu** (a pipeline az `error` szintű V-találattal "
              "jelölt sort kizárja, a közvetlen motorhívás nem validál). Ide tartoznak a Khan 2020 6–7. fejezetének "
              "MetaXL-esetei, ahol a forrás tört eseményszámmal (x = prevalencia × n) számol, a motor V006-szabálya "
              "pedig a nem egész eseményszámot hibának veszi. Ez a motor szándékos döntése, nem a lánc hibája; ha a "
              "tört eseményszámot engedni kell (pl. figyelmeztetéssel), az motor-PR.", "",
              "| Ok | Eset | Ellenőrzés |", "|---|---:|---:|"]
    for why, c in sorted(reasons.items(), key=lambda kv: (-kv[1]["checks"], kv[0])):
        lines.append("| %s | %d | %d |" % (why, c["cases"], c["checks"]))
    lines += ["", "## Esetek", "",
              "| Eset | Hívás | Mérték | Ellenőrzés | Út | Eredmény / ok |", "|---|---|---|---:|---|---|"]
    for r in sorted(rows, key=lambda r: r["case"]["case_id"]):
        c = r["case"]
        if r["route"] is None:
            res = "nem leképezhető: " + r["reason"]
        else:
            green = sum(1 for _l, ok, _m in r["results"] if ok)
            res = "%d/%d zöld" % (green, r["checks"])
        lines.append("| `%s` | %s | %s | %d | %s | %s |" % (c["case_id"], c["call"], c.get("measure") or "—",
                                                            r["checks"], r["route"] or "—", res))
    lines.append("")
    return "\n".join(lines)


_CACHE = {}


def replay_all():
    if "rows" not in _CACHE:
        rp = Replay()
        try:
            _CACHE["rows"] = rp.run(active_cases())
        finally:
            rp.close()
    return _CACHE["rows"]


class ChainReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = replay_all()

    def test_all_mappable_checks_green(self):
        """A leképezhető ellenőrzések 100%-a zöld (a tűrés és a közvetlen motorhívás szerint is)."""
        bad = []
        for r in self.rows:
            if r["route"] is None:
                continue
            for label, ok, msg in list(r["results"]) + list(r.get("extra") or []):
                if not ok:
                    bad.append("%s :: %s — %s" % (r["case"]["case_id"], label, msg))
        self.assertEqual(bad, [], "\n".join(bad[:40]))

    def test_every_mapped_check_was_evaluated(self):
        for r in self.rows:
            if r["route"] is not None:
                self.assertEqual(len(r["results"]), r["checks"], r["case"]["case_id"])

    def test_coverage_is_measured_and_high(self):
        _by, reasons, tot = coverage(self.rows)
        self.assertEqual(tot["cases"], len(active_cases()))
        self.assertGreater(tot["mapped_checks"], 0)
        self.assertGreaterEqual(tot["mapped_checks"], MIN_MAPPED_SHARE * tot["checks"], sorted(reasons))
        for why in reasons:
            self.assertTrue(why.startswith(KNOWN_UNMAPPED), "új, nem leképezhető ok (indokold és vedd fel a "
                                                              "KNOWN_UNMAPPED-be, vagy képezd le): %s" % why)

    def test_view_model_is_the_plot_contract(self):
        """A visszajátszott nézetmodellek plot-ja a motor szk.ma.plot/v2-szerződése szerint érvényes (mintavétel)."""
        from ma_gui import schema_lite
        from ma_gui.routes import _contracts
        reg = _contracts.registry()
        seen = 0
        for r in self.rows:
            view = r.get("view")
            if view is None or seen >= 25:
                continue
            errs = schema_lite.validate(view["plot"], _contracts.ref("szk.ma.plot/v2"), reg)
            self.assertEqual(errs, [], "%s: %s" % (r["case"]["case_id"], errs[:3]))
            seen += 1
        self.assertGreater(seen, 0)

    def test_coverage_report_up_to_date(self):
        want = report_text(self.rows)
        try:
            with open(REPORT, encoding="utf-8") as fh:
                have = fh.read()
        except OSError:
            have = None
        self.assertEqual(have, want, "a lefedettségi jelentés elavult: python3 tests/gui/test_chain_replay.py "
                                     "--write-report")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--write-report" in argv:
        text = report_text(replay_all())
        with open(REPORT, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        _by, _r, tot = coverage(replay_all())
        print("%s: %d/%d ellenőrzés leképezett, ebből %d zöld" % (REPORT, tot["mapped_checks"], tot["checks"],
                                                                   tot["green"]))
        return 0 if tot["green"] == tot["mapped_checks"] else 1
    return unittest.main(argv=[sys.argv[0]] + argv)


if __name__ == "__main__":
    sys.exit(main())
