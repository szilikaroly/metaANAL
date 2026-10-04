# -*- coding: utf-8 -*-
"""Kis-vizsgálat hatások és publikációs torzítás.

- Egger-féle regressziós teszt (klasszikus: y/se ~ 1/se, OLS; Egger et al. 1997)
- Begg–Mazumdar rangkorreláció (Kendall τ; Begg & Mazumdar 1994)
- Trim-and-fill (Duval & Tweedie 2000; L0 és R0; a metafor::trimfill algoritmusát követi)
- Rosenthal-féle fail-safe N (csak tájékoztató; nem ajánlott önálló bizonyítékként)

Fontos (Sterne et al. 2011, BMJ; Cochrane Handbook 13. fejezet): a tesztek ereje k < 10
esetén kicsi, és az aszimmetria nem egyenlő a publikációs torzítással (heterogenitás,
kis vizsgálatok eltérő minősége, véletlen is okozhatja).
"""
import math

from . import distributions as dist
from .models import meta_analysis, ModelError, MetaResult
from .moderators import meta_regression


def egger_test(yi, vi):
    k = len(yi)
    if k < 3:
        raise ModelError("Egger-teszt: k >= 3 szükséges")
    se = [math.sqrt(v) for v in vi]
    zs = [y / s for y, s in zip(yi, se)]
    prec = [1.0 / s for s in se]
    xm = sum(prec) / k
    ym = sum(zs) / k
    sxx = sum((x - xm) ** 2 for x in prec)
    if sxx <= 0:
        raise ModelError("Egger-teszt: a pontosságok azonosak")
    sxy = sum((x - xm) * (y - ym) for x, y in zip(prec, zs))
    slope = sxy / sxx
    intercept = ym - slope * xm
    resid = [y - intercept - slope * x for x, y in zip(prec, zs)]
    df = k - 2
    s2 = sum(r * r for r in resid) / df if df > 0 else float("nan")
    se_int = math.sqrt(s2 * (1.0 / k + xm * xm / sxx))
    se_slope = math.sqrt(s2 / sxx)
    t = intercept / se_int if se_int > 0 else math.inf
    p = dist.t_two_sided_p(t, df)
    crit = dist.t_ppf(0.975, df)
    warn = []
    if k < 10:
        warn.append("k = %d < 10: az Egger-teszt ereje kicsi; ne értelmezd önmagában "
                    "(Sterne et al. 2011)." % k)
    return MetaResult(kind="egger", k=k, intercept=intercept, se_intercept=se_int, t=t, df=df, p=p,
                      ci_lower=intercept - crit * se_int, ci_upper=intercept + crit * se_int,
                      slope=slope, se_slope=se_slope, warnings=warn)


def _kendall_exact_sf(s_abs, n):
    """P(|S| >= s_abs) pontosan, kötések nélkül (inverziószám-eloszlás DP)."""
    maxinv = n * (n - 1) // 2
    counts = [1]
    for m in range(2, n + 1):
        new = [0] * (len(counts) + m - 1)
        for i, c in enumerate(counts):
            if c:
                for j in range(m):
                    new[i + j] += c
        counts = new
    total = float(sum(counts))
    # S = (konkordáns - diszkordáns) = maxinv - 2*inv
    p = 0.0
    for inv, c in enumerate(counts):
        s = maxinv - 2 * inv
        if abs(s) >= s_abs - 1e-9:
            p += c
    return p / total


def begg_test(yi, vi):
    k = len(yi)
    if k < 3:
        raise ModelError("Begg-teszt: k >= 3 szükséges")
    w = [1.0 / v for v in vi]
    sw = sum(w)
    mu = sum(a * b for a, b in zip(w, yi)) / sw
    vstar = [v - 1.0 / sw for v in vi]
    tstar = [(y - mu) / math.sqrt(vs) if vs > 0 else 0.0 for y, vs in zip(yi, vstar)]
    conc = disc = 0
    ties_x = ties_y = 0
    for i in range(k):
        for j in range(i + 1, k):
            dx = tstar[i] - tstar[j]
            dy = vi[i] - vi[j]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                ties_x += 1
            elif dy == 0:
                ties_y += 1
            elif dx * dy > 0:
                conc += 1
            else:
                disc += 1
    s = conc - disc
    n0 = k * (k - 1) / 2.0
    denom = math.sqrt((n0 - _tie_pairs(tstar)) * (n0 - _tie_pairs(vi)))
    tau = s / denom if denom > 0 else 0.0
    has_ties = _tie_pairs(tstar) > 0 or _tie_pairs(vi) > 0
    if not has_ties and k <= 50:
        p = _kendall_exact_sf(abs(s), k)
        method = "pontos"
    else:
        var_s = (k * (k - 1) * (2 * k + 5) - _tie_term(tstar) - _tie_term(vi)) / 18.0
        z = s / math.sqrt(var_s) if var_s > 0 else 0.0
        p = dist.z_two_sided_p(z)
        method = "normális közelítés"
    warn = []
    if k < 10:
        warn.append("k < 10: a Begg-teszt ereje nagyon kicsi.")
    return MetaResult(kind="begg", k=k, kendall_tau=tau, S=s, p=min(1.0, p), method=method,
                      warnings=warn)


def _tie_pairs(vals):
    from collections import Counter
    return sum(c * (c - 1) / 2.0 for c in Counter(vals).values() if c > 1)


def _tie_term(vals):
    from collections import Counter
    return sum(c * (c - 1) * (2 * c + 5) for c in Counter(vals).values() if c > 1)


def _rank_first(values):
    """R rank(ties.method='first') megfelelője (1-től)."""
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    ranks = [0] * len(values)
    for r, i in enumerate(order, start=1):
        ranks[i] = r
    return ranks


def trim_and_fill(yi, vi, labels=None, model="random", tau2_method="REML", estimator="L0",
                  side=None, ci_method="z", level=0.95, maxiter=100):
    """Duval–Tweedie trim-and-fill (a metafor::trimfill logikája szerint).

    side: 'left' / 'right' / None (automatikus: y ~ se meta-regresszió meredekségének
    előjele; pozitív → a hiányzó vizsgálatok bal oldalon).
    """
    k = len(yi)
    if k < 3:
        raise ModelError("trim-and-fill: k >= 3 szükséges")
    labels = list(labels) if labels else ["#%d" % (i + 1) for i in range(k)]
    mr_method = tau2_method if (model == "random" and tau2_method in ("REML", "ML", "DL")) else \
        ("FE" if model == "fixed" else "REML")
    if side is None:
        x = [[1.0, math.sqrt(v)] for v in vi]
        mr = meta_regression(yi, vi, x, ["intercept", "sei"], mr_method)
        side = "right" if mr.coefficients[1]["estimate"] < 0 else "left"
    sign = -1.0 if side == "right" else 1.0
    y = [sign * a for a in yi]
    order = sorted(range(k), key=lambda i: (y[i], i))
    ys = [y[i] for i in order]
    vs = [vi[i] for i in order]
    k0, k0_prev, it = 0, -1, 0
    beta = None
    se_k0 = None
    while k0 != k0_prev:
        k0_prev = k0
        it += 1
        if it > maxiter:
            raise ModelError("trim-and-fill nem konvergált")
        fit = meta_analysis(ys[:k - k0], vs[:k - k0], model, tau2_method, "z", level)
        beta = fit.estimate
        yc = [a - beta for a in ys]
        r = _rank_first([abs(a) for a in yc])
        rs = [(1 if a > 0 else (-1 if a < 0 else 0)) * rr for a, rr in zip(yc, r)]
        if estimator == "R0":
            negs = [-a for a in rs if a < 0]
            k0f = (k - max(negs)) - 1 if negs else k - 1
            se_k0 = math.sqrt(2 * max(0, k0f) + 2)
        else:
            sr = sum(a for a in rs if a > 0)
            k0f = (4 * sr - k * (k + 1)) / (2 * k - 1)
            var_sr = (k * (k + 1) * (2 * k + 1) + 10 * k0f ** 3 + 27 * k0f ** 2 + 17 * k0f
                      - 18 * k * k0f ** 2 - 18 * k * k0f + 6 * k * k * k0f) / 24.0
            se_k0 = 4 * math.sqrt(max(0.0, var_sr)) / (2 * k - 1)
        k0 = max(0, int(round(k0f)))   # Python round() = R round() (bankári kerekítés)
    filled_y, filled_v, filled_labels = [], [], []
    if k0 > 0:
        for i in range(k - k0, k):
            filled_y.append(sign * (2 * beta - ys[i]))
            filled_v.append(vs[i])
            filled_labels.append("kitöltött: " + labels[order[i]])
    all_y = list(yi) + filled_y
    all_v = list(vi) + filled_v
    adjusted = meta_analysis(all_y, all_v, model, tau2_method, ci_method, level,
                             labels=list(labels) + filled_labels)
    return MetaResult(kind="trimfill", side=side, estimator=estimator, k0=k0, se_k0=se_k0,
                      iterations=it, filled_yi=filled_y, filled_vi=filled_v,
                      filled_labels=filled_labels, adjusted=adjusted,
                      warnings=["A trim-and-fill korrigált becslése érzékenységi elemzés, nem "
                                "'valódi' hatás; heterogenitás mellett félrevezető lehet."])


def failsafe_n_rosenthal(yi, vi, alpha=0.05):
    zs = [y / math.sqrt(v) for y, v in zip(yi, vi)]
    za = dist.norm_ppf(1 - alpha)  # egyoldali
    k = len(zs)
    n = (sum(zs) ** 2) / (za ** 2) - k
    return MetaResult(kind="failsafe_rosenthal", N=max(0.0, n), k=k,
                      warnings=["A fail-safe N elavult, félrevezető mutató; csak kiegészítő "
                                "információként jelentsd (Cochrane Handbook 13.3.5.6)."])
