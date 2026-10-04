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
      begg | trimfill | lfk | harbord | peters | failsafe | leave_one_out | influence | conversion
path: pontokkal elválasztott út az eredményben (pl. "estimate", "yi.3", "heterogeneity.tau2_ci_QP.0",
      "groups.early.estimate", "coefficients.1.estimate", "adjusted.estimate", "0.estimate").
transform: null | exp | expit | tanh | sin2 | pct | sqrt | square | pft_hm (Freeman–Tukey visszatr.)
"""
import json
import math
import os

import _helpers  # noqa: F401  (a csomag gyökerét a sys.path-ra teszi)
from metaelemzes import bias as B
from metaelemzes import conversions as C
from metaelemzes import effect_sizes as E
from metaelemzes import models as M
from metaelemzes import moderators as MO
from metaelemzes import sensitivity as SE

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.join(HERE, "reference", "source_examples")


def _es(case):
    return E.compute(case["rows"], case.get("measure", "GEN"), **(case.get("options") or {}))


def _cols(case, names=("e1", "n1", "e2", "n2")):
    return [[r.get(c) for r in case["rows"]] for c in names]


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
    if call in ("egger", "begg", "lfk", "failsafe"):
        es = _es(case)
        fn = {"egger": B.egger_test, "begg": B.begg_test, "lfk": B.doi_plot_data,
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


def transform(value, how, case=None):
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
    if how == "pft_hm":
        nh = E.harmonic_mean([r["n"] for r in case["rows"]])
        return E.pft_inverse(value, nh)
    raise ValueError("ismeretlen transform: %r" % how)


def check_case(case):
    """Visszaad: [(path, várt, kapott, tol, ok, hibaüzenet)]."""
    out = []
    try:
        res = execute(case)
    except Exception as exc:  # noqa: BLE001
        return [("*", None, None, None, False, "%s: %s" % (type(exc).__name__, exc))]
    for ex in case["expected"]:
        try:
            got = transform(float(resolve(res, ex["path"])), ex.get("transform"), case)
            ok = abs(got - ex["value"]) <= ex["tol"]
            out.append((ex["path"], ex["value"], got, ex["tol"], ok, ""))
        except Exception as exc:  # noqa: BLE001
            out.append((ex["path"], ex.get("value"), None, ex.get("tol"), False, "%s: %s" % (type(exc).__name__, exc)))
    return out


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
    n_ok = n_fail = 0
    for c in load_cases(target):
        if c.get("status") == "known_gap":
            print("GAP   %s — %s" % (c["case_id"], c.get("gap_reason", "")))
            continue
        for path, want, got, tol, ok, msg in check_case(c):
            tag = "PASS" if ok else "FAIL"
            n_ok += ok
            n_fail += (not ok)
            print("%s  %s  %s: várt %s, kapott %s (tol %s) %s" % (tag, c["case_id"], path, want, got, tol, msg))
    print("összesen: %d PASS, %d FAIL" % (n_ok, n_fail))
    sys.exit(1 if n_fail else 0)
