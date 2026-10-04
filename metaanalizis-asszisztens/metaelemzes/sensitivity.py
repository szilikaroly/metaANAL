# -*- coding: utf-8 -*-
"""Érzékenységi és befolyás-elemzések.

- Leave-one-out (egyenként kihagyott vizsgálatok)
- Befolyás-diagnosztika (Viechtbauer & Cheung 2010): externálisan studentizált reziduum,
  DFFITS, Cook-távolság, kovariancia-arány, hat-érték, DFBETAS; jelölés a metafor
  influence.rma.uni szabályai szerint
- Kumulatív metaanalízis (pl. publikációs év szerint)
"""
import math

from . import distributions as dist
from .models import meta_analysis, MetaResult


def leave_one_out(yi, vi, labels, model="random", tau2_method="REML", ci_method=None,
                  level=0.95):
    out = []
    k = len(yi)
    if k < 3:
        return out
    for i in range(k):
        y = yi[:i] + yi[i + 1:]
        v = vi[:i] + vi[i + 1:]
        r = meta_analysis(y, v, model, tau2_method, ci_method, level)
        out.append({"omitted": labels[i], "estimate": r.estimate, "se": r.se,
                    "ci_lower": r.ci_lower, "ci_upper": r.ci_upper, "p": r.p,
                    "tau2": r.tau2, "I2": r.I2, "Q": r.Q})
    return out


def influence(yi, vi, labels, model="random", tau2_method="REML", level=0.95):
    k = len(yi)
    if k < 3:
        return []
    full = meta_analysis(yi, vi, model, tau2_method, "z", level)
    tau2 = full.tau2
    w = [1.0 / (v + tau2) for v in vi]
    sw = sum(w)
    p = 1
    rows = []
    for i in range(k):
        y = yi[:i] + yi[i + 1:]
        v = vi[:i] + vi[i + 1:]
        r = meta_analysis(y, v, model, tau2_method, "z", level)
        hat = w[i] / sw
        resid = yi[i] - r.estimate
        var_r = vi[i] + r.tau2 + r.se ** 2
        rstudent = resid / math.sqrt(var_r)
        dffits = (full.estimate - r.estimate) / math.sqrt(hat * (r.tau2 + vi[i]))
        cook = (full.estimate - r.estimate) ** 2 / full.se ** 2
        covratio = r.se ** 2 / full.se ** 2
        # metafor: a nevező a TELJES adatsor varianciája a kihagyásos τ²-tel
        vb_del = 1.0 / sum(1.0 / (v_ + r.tau2) for v_ in vi)
        dfbetas = (full.estimate - r.estimate) / math.sqrt(vb_del)
        infl = (abs(dffits) > 3 * math.sqrt(p / float(k - p))
                or cook > dist.chi2_ppf(0.5, p)
                or hat > 3.0 * p / k
                or abs(dfbetas) > 1)
        rows.append({"study": labels[i], "rstudent": rstudent, "dffits": dffits,
                     "cook_d": cook, "cov_ratio": covratio, "tau2_del": r.tau2,
                     "Q_del": r.Q, "hat": hat, "weight_pct": 100 * hat, "dfbetas": dfbetas,
                     "influential": infl, "outlier_flag": abs(rstudent) > 1.96})
    return rows


def cumulative(yi, vi, labels, order_key, model="random", tau2_method="REML", ci_method=None,
               level=0.95):
    idx = sorted(range(len(yi)), key=lambda i: (order_key[i], i))
    out = []
    for n in range(1, len(idx) + 1):
        sel = idx[:n]
        y = [yi[i] for i in sel]
        v = [vi[i] for i in sel]
        if n == 1:
            r = meta_analysis(y, v, "fixed", ci_method="z", level=level)
        else:
            r = meta_analysis(y, v, model, tau2_method, ci_method, level)
        out.append({"added": labels[idx[n - 1]], "key": order_key[idx[n - 1]], "k": n,
                    "estimate": r.estimate, "ci_lower": r.ci_lower, "ci_upper": r.ci_upper,
                    "tau2": r.tau2, "I2": r.I2})
    return out
