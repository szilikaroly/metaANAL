# -*- coding: utf-8 -*-
"""Alcsoport-elemzés és (vegyes hatású) meta-regresszió.

Meta-regresszió: y = Xβ + u + e, u ~ N(0, τ²), e ~ N(0, v_i).
τ²-becslők: REML, ML (Fisher-scoring), DL (momentum-módszer), FE (τ² = 0).
Tesztek: z (Wald) vagy Knapp–Hartung (t / F).
Források: Borenstein et al. 2009 (19–20. fejezet); Khan 2020 (11. fejezet);
Viechtbauer 2010 (metafor rma.uni); Knapp & Hartung 2003.
"""
import math

from . import distributions as dist
from . import linalg as la
from .models import ModelError, MetaResult, meta_analysis, estimate_tau2


# ------------------------------------------------------------- alcsoportok
def subgroup_analysis(yi, vi, groups, labels=None, model="random", tau2_method="REML",
                      ci_method=None, level=0.95, common_tau2=False, pi_method="t_k-2"):
    """Alcsoportonkénti összesítés + alcsoport-különbség teszt (Q_between).

    common_tau2=True: közös τ² a csoportokon belül (vegyes hatású modell faktor
    moderátorral, a meta-regresszió reziduális τ²-ével), különben csoportonként
    külön τ² (Borenstein 19. fejezet mindkét változatot tárgyalja).
    Q_between = Σ (μ_g - μ̄)² / se_g², df = G - 1 (RevMan-féle teszt).
    """
    if len(groups) != len(yi):
        raise ModelError("a csoportváltozó hossza eltér")
    labels = list(labels) if labels else ["#%d" % (i + 1) for i in range(len(yi))]
    order = []
    for g in groups:
        if g not in order:
            order.append(g)
    tau2_common = None
    if common_tau2 and model == "random":
        x = [[1.0] + [1.0 if g == lev else 0.0 for lev in order[1:]] for g in groups]
        mr = meta_regression(yi, vi, x, ["intercept"] + ["g=%s" % lev for lev in order[1:]],
                             tau2_method=tau2_method if tau2_method in ("REML", "ML", "DL") else "REML")
        tau2_common = mr.tau2
    out_groups = []
    for g in order:
        idx = [i for i, gg in enumerate(groups) if gg == g]
        y = [yi[i] for i in idx]
        v = [vi[i] for i in idx]
        labs = [labels[i] for i in idx]
        if len(y) == 1:
            r = meta_analysis(y, v, "fixed", ci_method="z", level=level, labels=labs)
            r.note = "egyetlen vizsgálat — nincs összesítés"
        else:
            r = meta_analysis(y, v, model, tau2_method, ci_method, level, pi_method, labs,
                              tau2_fixed=tau2_common)
        r.group = g
        out_groups.append(r)
    mus = [r.estimate for r in out_groups]
    ses = [r.se for r in out_groups]
    w = [1.0 / s ** 2 for s in ses]
    mbar = sum(a * b for a, b in zip(w, mus)) / sum(w)
    q_between = sum(wi * (m - mbar) ** 2 for wi, m in zip(w, mus))
    df = len(out_groups) - 1
    overall = meta_analysis(yi, vi, model, tau2_method, ci_method, level, pi_method, labels)
    warnings = []
    if any(r.k < 3 for r in out_groups):
        warnings.append("Van < 3 vizsgálatot tartalmazó alcsoport: az alcsoport-becslések és a "
                        "különbség-teszt erősen bizonytalanok.")
    if len(yi) < 10:
        warnings.append("k < 10: az alcsoport-elemzés ereje kicsi; csak előre tervezett, "
                        "kevés alcsoportot vizsgálj (Cochrane Handbook 10.11).")
    return MetaResult(
        kind="subgroup", model=model, groups=out_groups, overall=overall,
        Q_between=q_between, df_between=df,
        p_between=dist.chi2_sf(q_between, df) if df > 0 else None,
        common_tau2=tau2_common, warnings=warnings,
    )


# ------------------------------------------------------------ meta-regresszió
def _wls(x, y, w):
    xtwx = la.xtwx(x, w)
    m = la.inverse(xtwx)
    b = la.matvec(m, la.xtwy(x, w, y))
    e = [yi - sum(a * c for a, c in zip(row, b)) for row, yi in zip(x, y)]
    return b, m, e


def _traces(x, w, m):
    """tr(P), tr(PP) a P = W - WX M XᵀW projekcióhoz, O(k p²)."""
    w2 = [a * a for a in w]
    w3 = [a ** 3 for a in w]
    a2 = la.matmul(m, la.xtwx(x, w2))     # M XᵀW²X
    a3 = la.matmul(m, la.xtwx(x, w3))     # M XᵀW³X
    tr_p = sum(w) - la.trace(a2)
    tr_pp = sum(w2) - 2.0 * la.trace(a3) + la.trace(la.matmul(a2, a2))
    return tr_p, tr_pp


def _tau2_mr(x, y, v, method, tol=1e-10, maxiter=1000):
    k, p = len(y), len(x[0])
    w0 = [1.0 / vi for vi in v]
    b0, m0, e0 = _wls(x, y, w0)
    qe = sum(wi * ei * ei for wi, ei in zip(w0, e0))
    tr_p0, _ = _traces(x, w0, m0)
    tau2_mm = max(0.0, (qe - (k - p)) / tr_p0) if tr_p0 > 0 else 0.0
    if method == "FE":
        return 0.0, True
    if method == "DL":
        return tau2_mm, True
    tau2 = tau2_mm
    for _ in range(maxiter):
        w = [1.0 / (vi + tau2) for vi in v]
        b, m, e = _wls(x, y, w)
        r2w2 = sum((wi * ei) ** 2 for wi, ei in zip(w, e))
        if method == "REML":
            tr_p, tr_pp = _traces(x, w, m)
            adj = (r2w2 - tr_p) / tr_pp
        else:  # ML
            adj = (r2w2 - sum(w)) / sum(a * a for a in w)
        while tau2 + adj < 0:
            adj /= 2.0
            if abs(adj) < 1e-300:
                adj = -tau2
                break
        tau2 += adj
        if abs(adj) <= tol * max(1.0, tau2):
            return (0.0 if tau2 < 1e-10 else tau2), True
    return tau2, False


def meta_regression(yi, vi, x, names, tau2_method="REML", test="z", level=0.95):
    """Vegyes hatású meta-regresszió.

    x: a modellmátrix sorai (az első oszlop általában a tengelymetszet: 1.0).
    test: 'z' (Wald, χ² omnibusz) vagy 'knha' (Knapp–Hartung: t és F).
    """
    k = len(yi)
    p = len(x[0])
    if k <= p:
        raise ModelError("meta-regresszió: k (%d) <= paraméterek száma (%d)" % (k, p))
    method = tau2_method.upper()
    if method not in ("REML", "ML", "DL", "FE"):
        raise ModelError("meta-regresszió τ²-becslő: REML, ML, DL vagy FE")
    tau2, conv = _tau2_mr(x, yi, vi, method)
    warnings = []
    if not conv:
        warnings.append("A meta-regresszió τ²-iterációja nem konvergált.")
    w = [1.0 / (v + tau2) for v in vi]
    b, m, e = _wls(x, yi, w)
    vb = [row[:] for row in m]
    df_res = k - p
    if test == "knha":
        q = sum(wi * ei * ei for wi, ei in zip(w, e)) / df_res
        vb = [[q * c for c in row] for row in vb]
        crit = dist.t_ppf(0.5 + level / 2, df_res)
    else:
        crit = dist.norm_ppf(0.5 + level / 2)
    coefs = []
    for j in range(p):
        se = math.sqrt(vb[j][j])
        stat = b[j] / se if se > 0 else math.inf
        pval = dist.t_two_sided_p(stat, df_res) if test == "knha" else dist.z_two_sided_p(stat)
        coefs.append({"name": names[j], "estimate": b[j], "se": se, "stat": stat, "p": pval,
                      "ci_lower": b[j] - crit * se, "ci_upper": b[j] + crit * se})
    # omnibusz moderátor-teszt (a tengelymetszet nélkül, ha van)
    has_int = all(row[0] == 1.0 for row in x)
    idx = list(range(1, p)) if has_int else list(range(p))
    qm = qm_p = None
    if idx:
        bsub = [b[i] for i in idx]
        vsub = [[vb[i][j] for j in idx] for i in idx]
        vinv = la.inverse(vsub)
        qm = sum(bsub[i] * sum(vinv[i][j] * bsub[j] for j in range(len(idx))) for i in range(len(idx)))
        if test == "knha":
            fstat = qm / len(idx)
            qm_p = dist.f_sf(fstat, len(idx), df_res)
            qm = fstat
        else:
            qm_p = dist.chi2_sf(qm, len(idx))
    # reziduális heterogenitás (FE-súlyokkal)
    w0 = [1.0 / v for v in vi]
    b0, m0, e0 = _wls(x, yi, w0)
    qe = sum(wi * ei * ei for wi, ei in zip(w0, e0))
    tr_p0, _ = _traces(x, w0, m0)
    s2 = df_res / tr_p0 if tr_p0 > 0 else None
    if method == "FE":
        # metafor: FE-modellnél az I² a QE-ből számolódik
        i2_res = 100.0 * max(0.0, (qe - df_res) / qe) if qe > 0 else 0.0
    else:
        i2_res = 100.0 * tau2 / (tau2 + s2) if s2 else None
    # R² analóg: a τ² arányos csökkenése az üres modellhez képest
    r2 = None
    if has_int and method != "FE":
        tau2_0 = estimate_tau2(yi, vi, method)[0] if method != "DL" else \
            _tau2_mr([[1.0] for _ in yi], yi, vi, "DL")[0]
        if tau2_0 > 0:
            r2 = max(0.0, 100.0 * (tau2_0 - tau2) / tau2_0)
        else:
            r2 = 0.0
    if k < 10 * max(1, len(idx)):
        warnings.append("Kevés vizsgálat moderátoronként (ökölszabály: >= 10 vizsgálat / "
                        "moderátor; Cochrane Handbook 10.11.4).")
    return MetaResult(
        kind="meta_regression", tau2=tau2, tau2_method=method, test=test, k=k, p=p,
        coefficients=coefs, QM=qm, QM_df=len(idx), QM_p=qm_p, QM_type="F" if test == "knha" else "chi2",
        QE=qe, QE_df=df_res, QE_p=dist.chi2_sf(qe, df_res), I2_res=i2_res, R2=r2,
        warnings=warnings, level=level,
    )


def design_matrix(rows, moderators, intercept=True):
    """Moderátor-oszlopokból modellmátrix; a nem numerikus oszlopok dummy-kódolást kapnak
    (referencia: az első előforduló szint)."""
    x = [[1.0] if intercept else [] for _ in rows]
    names = ["intercept"] if intercept else []
    for mod in moderators:
        vals = [r.get(mod) for r in rows]
        if any(v is None or v == "" for v in vals):
            raise ModelError("hiányzó moderátor-érték: %s" % mod)
        numeric = all(isinstance(v, (int, float)) for v in vals)
        if numeric:
            for row, v in zip(x, vals):
                row.append(float(v))
            names.append(mod)
        else:
            levels = []
            for v in vals:
                if str(v) not in levels:
                    levels.append(str(v))
            for lev in levels[1:]:
                for row, v in zip(x, vals):
                    row.append(1.0 if str(v) == lev else 0.0)
                names.append("%s=%s" % (mod, lev))
    return x, names
