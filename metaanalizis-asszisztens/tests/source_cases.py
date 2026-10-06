# -*- coding: utf-8 -*-
"""A forrásokból (tankönyvek, cikkek) származó kidolgozott példák futtatója.

Egy eset (JSON) szerkezete:
{
  "case_id": "khan2020__heartburn_ivhet",
  "example_id": "khan_ch03_04__heartburn_ivhet", "source_id": "khan2020",
  "location": "Ch4 Table 4.9-4.10, pp. 75-77", "description": "...",
  "measure": "RR", "options": {"cc": 0.5, "smd_vtype": "LS2", "j_method": "approx"},
  "rows": [{"study": "...", "e1": 5, "n1": 54, "e2": 5, "n2": 53}, ...],
  "call": "meta_analysis", "call_args": {"model": "ivhet"},
  "expected": [{"path": "estimate", "value": 0.56366, "tol": 5e-6, "transform": null}],
  "status": "active" | "known_gap", "gap_reason": "..."
}
call: effect_sizes | meta_analysis | mantel_haenszel | peto | subgroup | meta_regression | egger |
      begg | trimfill | lfk | doi | harbord | peters | failsafe | leave_one_out | influence |
      cumulative | outlier_screen | power | studies_needed | prisma_check (= prisma_flow) | conversion
      - prisma_check: a flow-szótár a case["flow"] vagy a case["rows"][0]; call_args: template,
        composer (True → prisma.from_composer előfeldolgozás)
      - power / studies_needed: nincs sor; a call_args a power.power_analysis argumentumai
input_storage: {"oszlop": "float32"} — a bemeneti oszlop a forrásszoftver (pl. Stata 'float')
      egyszeres pontosságú tárolásával (a nyomtatott értékek ezzel készültek).
path: pontokkal elválasztott út az eredményben (pl. "estimate", "yi.3", "heterogeneity.tau2_ci_QP.0",
      "groups.early.estimate", "coefficients.1.estimate", "adjusted.estimate", "0.estimate").
expr: (path helyett vagy mellett) származtatott mennyiség biztonságos kifejezéssel, pl.
      "100*(QE-QE_df)/QE" vagy "p('weights_pct.3')/p('weights_pct.2')". A név egy útvonal (vagy a
      "vars" szótár kulcsa: {"név": "útvonal"}); p('útvonal') tetszőleges útvonalat old fel.
      Engedélyezett: számok, + - * / ** %, egyoperandusú ±, és sqrt, log, log10, exp, abs, min,
      max, sum, len, tanh, expit, pi, e. Ha van expr, a "path" csak felirat.
value: szám (abs. tolerancia: |kapott − várt| <= tol), vagy szöveg / logikai érték — ez PONTOS
      egyezés (pl. az LFK-kategória 'kisebb aszimmetria'); a tol ekkor nem számít.
transform: null | exp | expit | tanh | sin2 | pct | sqrt | square | pft_hm | pft_iv
      pft_hm: Freeman–Tukey visszatranszformálás (Miller 1978); az n alapból a sorok n-jeinek
        harmonikus átlaga; az elvárt érték "pft_n": "variance" mezőjével a MetaXL-konvenció
        (Barendregt 2013): m = 1/(4·se²), ahol se az útvonal SZÜLŐ-objektumának se mezője
        (pl. 'groups.Low.estimate' → 'groups.Low.se'); pft_iv = pft_hm + "pft_n": "variance".
"""
import ast
import json
import math
import os
import struct

import _helpers  # noqa: F401  (a csomag gyökerét a sys.path-ra teszi)
from metaelemzes import bias as B
from metaelemzes import conversions as C
from metaelemzes import effect_sizes as E
from metaelemzes import models as M
from metaelemzes import moderators as MO
from metaelemzes import power as PW
from metaelemzes import prisma as PR
from metaelemzes import sensitivity as SE

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.join(HERE, "reference", "source_examples")


def _float32(x):
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def _rows(case):
    """A sorok, a forrásszoftver tárolási pontosságának emulálásával, ha az eset kéri:
    "input_storage": {"oszlop": "float32"} — pl. a Stata alapértelmezett 'float' (egyszeres
    pontosságú) változótípusa, amellyel a nyomtatott eredmény készült."""
    rows = case["rows"]
    storage = case.get("input_storage") or {}
    if not storage:
        return rows
    out = []
    for r in rows:
        r = dict(r)
        for col, kind in storage.items():
            if kind != "float32":
                raise ValueError("input_storage: csak 'float32' támogatott (kapott: %r)" % (kind,))
            if isinstance(r.get(col), (int, float)) and not isinstance(r.get(col), bool):
                r[col] = _float32(r[col])
        out.append(r)
    return out


def _es(case):
    return E.compute(_rows(case), case.get("measure", "GEN"), **(case.get("options") or {}))


def _cols(case, names=("e1", "n1", "e2", "n2")):
    return [[r.get(c) for r in _rows(case)] for c in names]


def execute(case):
    call = case["call"]
    a = dict(case.get("call_args") or {})
    if call == "effect_sizes":
        return _es(case)
    if call == "meta_analysis":
        es = _es(case)
        return M.meta_analysis(es.yi, es.vi, labels=es.labels, **a)
    if call == "mantel_haenszel":
        return M.mantel_haenszel(*_cols(case), measure=case["measure"], **a)
    if call == "peto":
        return M.peto(*_cols(case), **a)
    if call == "subgroup":
        es = _es(case)
        col = a.pop("group_col", "subgroup")
        res = MO.subgroup_analysis(es.yi, es.vi, [str(r.get(col)) for r in es.rows], es.labels, **a)
        res.groups = {g.group: g for g in res.groups}
        return res
    if call == "meta_regression":
        es = _es(case)
        x, names = MO.design_matrix(es.rows, a.pop("moderators"))
        return MO.meta_regression(es.yi, es.vi, x, names, **a)
    if call in ("egger", "begg", "lfk", "doi", "failsafe"):
        es = _es(case)
        fn = {"egger": B.egger_test, "begg": B.begg_test, "lfk": B.doi_plot_data, "doi": B.doi_plot_data,
              "failsafe": B.failsafe_n_rosenthal}[call]
        return fn(es.yi, es.vi, **a)
    if call == "trimfill":
        es = _es(case)
        return B.trim_and_fill(es.yi, es.vi, es.labels, **a)
    if call in ("harbord", "peters"):
        return (B.harbord_test if call == "harbord" else B.peters_test)(*_cols(case), **a)
    if call == "leave_one_out":
        es = _es(case)
        return SE.leave_one_out(es.yi, es.vi, es.labels, **a)
    if call == "influence":
        es = _es(case)
        return SE.influence(es.yi, es.vi, es.labels, **a)
    if call == "cumulative":
        es = _es(case)
        col = a.pop("order_col", "year")
        return SE.cumulative(es.yi, es.vi, es.labels, [r.get(col) for r in es.rows], **a)
    if call in ("outlier_screen", "outliers"):
        es = _es(case)
        return SE.outlier_screen(es.yi, es.vi, es.labels, **a)
    if call in ("power", "power_analysis"):
        return PW.power_analysis(**a)
    if call == "studies_needed":
        return PW.studies_needed(**a)
    if call in ("prisma_check", "prisma_flow"):
        flow = case.get("flow")
        if flow is None:
            flow = (case.get("rows") or [{}])[0]
        flow = {k: v for k, v in flow.items() if k != "study"}
        if a.pop("composer", False):
            flow = PR.from_composer(flow)
        return PR.check_flow(flow, **a)
    if call == "conversion":
        fn = getattr(C, a.pop("function"))
        return fn(**a)
    raise ValueError("ismeretlen call: %r" % call)


def resolve(obj, path):
    cur = obj
    for part in str(path).split("."):
        if part == "":
            continue
        if isinstance(cur, M.MetaResult):
            cur = cur.__dict__
        elif isinstance(cur, E.EffectSizes):
            cur = cur.__dict__
        if isinstance(cur, dict):
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            cur = cur[int(part)]
        else:
            cur = getattr(cur, part)
    return cur


def _parent(path):
    parts = [p for p in str(path).split(".") if p != ""]
    return ".".join(parts[:-1])


# ------------------------------------------------- származtatott mennyiségek (expr)
def _expit(x):
    return 1.0 / (1.0 + math.exp(-x))


_EXPR_FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp, "abs": abs,
               "min": min, "max": max, "sum": sum, "len": len, "tanh": math.tanh, "expit": _expit}
_EXPR_CONSTS = {"pi": math.pi, "e": math.e}
_EXPR_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
                ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b}


def _num_or_list(v):
    if isinstance(v, (list, tuple)):
        return [float(x) for x in v]
    if isinstance(v, bool):
        return float(v)
    return float(v)


def eval_expr(expr, res, names=None):
    """Biztonságos aritmetikai kifejezés az eredmény útvonalain (nincs eval(): csak az
    engedélyezett AST-csomópontok). names: {"név": "útvonal"}; egyéb név = maga az útvonal."""
    names = names or {}
    tree = ast.parse(str(expr), mode="eval")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _EXPR_BINOPS:
            return _EXPR_BINOPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Name):
            if node.id in names:
                return _num_or_list(resolve(res, names[node.id]))
            if node.id in _EXPR_CONSTS:
                return _EXPR_CONSTS[node.id]
            return _num_or_list(resolve(res, node.id))
        if isinstance(node, (ast.List, ast.Tuple)):
            return [ev(x) for x in node.elts]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            fname = node.func.id
            if fname == "p":
                if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant) \
                        or not isinstance(node.args[0].value, str):
                    raise ValueError("expr: p() egyetlen szöveges útvonalat vár")
                return _num_or_list(resolve(res, node.args[0].value))
            if fname in _EXPR_FUNCS:
                return _EXPR_FUNCS[fname](*[ev(x) for x in node.args])
        raise ValueError("expr: nem engedélyezett elem: %s" % ast.dump(node)[:80])

    return ev(tree)


def transform(value, how, case=None, res=None, path=None, pft_n=None):
    if how in (None, "", "none"):
        return value
    if how == "exp":
        return math.exp(value)
    if how == "expit":
        return 1.0 / (1.0 + math.exp(-value))
    if how == "tanh":
        return math.tanh(value)
    if how == "sin2":
        return math.sin(value) ** 2
    if how == "pct":
        return 100.0 * value
    if how == "sqrt":
        return math.sqrt(value)
    if how == "square":
        return value * value
    if how == "pft_iv" or (how == "pft_hm" and pft_n == "variance"):
        if res is None or path is None:
            raise ValueError("pft_iv: az eredmény és az útvonal kell (expr-rel nem használható)")
        par = _parent(path)
        se = float(resolve(res, (par + ".se") if par else "se"))
        return E.back_transform("PFT", value, pft_n="variance", se=se)
    if how == "pft_hm":
        if pft_n not in (None, "harmonic"):
            raise ValueError("pft_hm: ismeretlen pft_n: %r (harmonic | variance)" % (pft_n,))
        nh = E.harmonic_mean([r["n"] for r in case["rows"]])
        return E.pft_inverse(value, nh)
    raise ValueError("ismeretlen transform: %r" % how)


def _label(ex):
    return ex.get("path") or ex.get("expr") or "?"


def check_expectation(res, ex, case=None):
    """Egy elvárt érték ellenőrzése → (címke, várt, kapott, tol, ok, hibaüzenet)."""
    want = ex.get("value")
    try:
        if ex.get("expr") is not None:
            raw = eval_expr(ex["expr"], res, ex.get("vars"))
        else:
            raw = resolve(res, ex["path"])
        if isinstance(want, (str, bool)):
            # szöveg / logikai érték: pontos egyezés (pl. LFK-kategória); transform itt nem értelmezett
            if ex.get("transform") not in (None, "", "none"):
                raise ValueError("szöveges elvárt értékhez nem adható transform")
            got = raw if isinstance(want, bool) else str(raw)
            ok = (got is want) if isinstance(want, bool) else (got == want)
            return (_label(ex), want, got, None, ok, "")
        got = transform(float(raw), ex.get("transform"), case, res, ex.get("path"), ex.get("pft_n"))
        ok = abs(got - want) <= ex["tol"]
        return (_label(ex), want, got, ex["tol"], ok, "")
    except Exception as exc:  # noqa: BLE001
        return (_label(ex), want, None, ex.get("tol"), False, "%s: %s" % (type(exc).__name__, exc))


def check_case(case):
    """Visszaad: [(path, várt, kapott, tol, ok, hibaüzenet)]."""
    out = []
    try:
        res = execute(case)
    except Exception as exc:  # noqa: BLE001
        return [("*", None, None, None, False, "%s: %s" % (type(exc).__name__, exc))]
    for ex in case["expected"]:
        out.append(check_expectation(res, ex, case))
    return out


STATUSES = ("active", "known_gap")


def case_status(case):
    """Az eset állapota ('active' alapértelmezéssel); ismeretlen érték (pl. elírt 'Active') →
    ValueError, hogy a unittest és a parancssori futtató ugyanúgy utasítsa el."""
    st = case.get("status", "active")
    if st not in STATUSES:
        raise ValueError("%s: ismeretlen status: %r (lehetséges: %s)" % (
            case.get("case_id", "?"), st, ", ".join(STATUSES)))
    return st


def is_active(case):
    """A két futtató (test_source_examples és a __main__) közös szűrője."""
    return case_status(case) == "active"


def load_cases(path=None):
    files = []
    if path and os.path.isfile(path):
        files = [path]
    else:
        d = path or CASE_DIR
        if os.path.isdir(d):
            files = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))
    cases = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        for c in data if isinstance(data, list) else [data]:
            c["_file"] = os.path.basename(f)
            cases.append(c)
    return cases


if __name__ == "__main__":  # python3 tests/source_cases.py [fájl vagy mappa]
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else None
    n_ok = n_fail = n_active = n_gap = 0
    for c in load_cases(target):
        try:
            active = is_active(c)
        except ValueError as exc:
            n_fail += 1
            print("FAIL  %s" % exc)
            continue
        if not active:
            n_gap += 1
            print("GAP   %s — %s" % (c["case_id"], c.get("gap_reason", "")))
            continue
        n_active += 1
        for path, want, got, tol, ok, msg in check_case(c):
            tag = "PASS" if ok else "FAIL"
            n_ok += ok
            n_fail += (not ok)
            print("%s  %s  %s: várt %s, kapott %s (tol %s) %s" % (tag, c["case_id"], path, want, got, tol, msg))
    print("esetek: %d aktív, %d ismert hiány (known_gap)" % (n_active, n_gap))
    print("összesen: %d PASS, %d FAIL" % (n_ok, n_fail))
    sys.exit(1 if n_fail else 0)
