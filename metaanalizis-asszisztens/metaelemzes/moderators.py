# -*- coding: utf-8 -*-
"""Alcsoport-elemzés és (vegyes hatású) meta-regresszió.

Meta-regresszió: y = Xβ + u + e, u ~ N(0, τ²), e ~ N(0, v_i).
τ²-becslők: REML, ML (Fisher-scoring + τ² = 0 határ és profil-likelihood rács ellenőrzése),
DL, HE, SJ, PM (a metafor rma.uni képletei moderátorokkal), FE (τ² = 0).
Tesztek: z (Wald) vagy Knapp–Hartung (t / F).
Források: Borenstein et al. 2009 (19–20. fejezet); Khan 2020 (11. fejezet);
Viechtbauer 2010 (metafor rma.uni); Knapp & Hartung 2003.
"""
import math

from . import distributions as dist
from . import linalg as la
from .models import (ModelError, MetaResult, meta_analysis, estimate_tau2, optimize_tau2,
                     variance_scale, tau2_info_warnings, ratio_stat)


# ------------------------------------------------------------- alcsoportok
def subgroup_analysis(yi, vi, groups, labels=None, model="random", tau2_method="REML",
                      ci_method=None, level=0.95, common_tau2=False, pi_method="t_k-2"):
    """Alcsoportonkénti összesítés + alcsoport-különbség teszt (Q_between).

    common_tau2=True: közös τ² a csoportokon belül (vegyes hatású modell faktor
    moderátorral, a meta-regresszió reziduális τ²-ével — a metafor rma(yi, vi, mods=~g));
    ekkor az egyvizsgálatos csoport varianciája is v_i + τ²_közös. Ha a közös τ² nem
    becsülhető (k <= csoportok száma, szinguláris modell), figyelmeztetéssel csoportonként
    külön τ²-re vált. Különben csoportonként külön τ² (Borenstein 19. fejezet mindkét
    változatot tárgyalja); az egyvizsgálatos csoport ekkor fix hatású (RevMan-gyakorlat).

    Q_between = Σ (μ_g - μ̄)² / se_g², df = G - 1, ahol se_g a csoportbecslés Wald-féle
    (NEM HKSJ-korrigált) standard hibája, √(1/Σw). A χ²(G-1) teszt így független a
    megjelenített CI-módszertől (--ci): ez a Borenstein 19. fejezet / RevMan 5 tesztje és a
    metafor rma(est, sei, mods=~g, method="FE") a különálló τ²-es csoportbecslésekre; közös τ²
    esetén pontosan a metafor rma(yi, vi, mods=~g) QM-je (Wald).
    IVhet modellnél se_g a csoportbecslés IVhet-standardhibája (√Σ(w_i/Σw)²(v_i + τ²_DL), a
    megjelenített z-CI alapja), NEM a √(1/Σw) inverz-variancia SE (az a csoporton belüli
    heterogenitást figyelmen kívül hagyná, és túl liberális tesztet adna).
    """
    if len(groups) != len(yi):
        raise ModelError("a csoportváltozó hossza eltér")
    labels = list(labels) if labels else ["#%d" % (i + 1) for i in range(len(yi))]
    warnings = []
    order = []
    for g in groups:
        if g not in order:
            order.append(g)
    tau2_common = None
    tau2_common_info = None
    if common_tau2 and model == "random":
        if len(yi) <= len(order):
            warnings.append("Közös τ² nem becsülhető (k = %d <= alcsoportok száma = %d); "
                            "csoportonként külön τ²-t használtam." % (len(yi), len(order)))
        else:
            x = [[1.0] + [1.0 if g == lev else 0.0 for lev in order[1:]] for g in groups]
            method = (tau2_method or "REML").upper()
            if method not in MR_TAU2_METHODS:
                method = "REML"
            try:
                tau2_common, tau2_common_info = _tau2_mr(x, yi, vi, method)
                warnings += ["Közös τ²: %s" % m for m in tau2_info_warnings(method, tau2_common_info)]
            except (ModelError, ArithmeticError) as exc:
                tau2_common = None
                warnings.append("Közös τ² nem becsülhető (%s); csoportonként külön τ²-t használtam." % exc)
    out_groups = []
    for g in order:
        idx = [i for i, gg in enumerate(groups) if gg == g]
        y = [yi[i] for i in idx]
        v = [vi[i] for i in idx]
        labs = [labels[i] for i in idx]
        if len(y) == 1:
            if tau2_common is not None:
                # közös τ²-es modellben az egyetlen vizsgálat varianciája v_i + τ²_közös
                r = meta_analysis(y, v, "random", tau2_method, "z", level, pi_method, labs,
                                  tau2_fixed=tau2_common)
                r.warnings = []
            else:
                r = meta_analysis(y, v, "fixed", ci_method="z", level=level, labels=labs)
            r.note = "egyetlen vizsgálat — nincs összesítés"
        else:
            r = meta_analysis(y, v, model, tau2_method, ci_method, level, pi_method, labs,
                              tau2_fixed=tau2_common)
        r.group = g
        out_groups.append(r)
    mus = [r.estimate for r in out_groups]
    # IVhet: a csoportbecslés saját (heterogenitással inflált, z-alapú) SE-je; az IVhet se_wald-ja
    # a √(1/Σw) inverz-variancia SE (a forrás-összevetésekhez), ami itt fix hatású tesztet adna
    ses = [r.se if r.model == "ivhet" else r.se_wald for r in out_groups]
    df = len(out_groups) - 1
    q_between = p_between = None
    if all(math.isfinite(s) and s > 0 for s in ses):
        w = [1.0 / s ** 2 for s in ses]
        mbar = sum(a * b for a, b in zip(w, mus)) / sum(w)
        q_between = sum(wi * (m - mbar) ** 2 for wi, m in zip(w, mus))
        p_between = dist.chi2_sf(q_between, df) if df > 0 else None
    else:
        warnings.append("Az alcsoport-különbség teszt nem számolható (nulla vagy nem véges "
                        "csoport-standardhiba).")
    overall = meta_analysis(yi, vi, model, tau2_method, ci_method, level, pi_method, labels)
    if any(r.k < 3 for r in out_groups):
        warnings.append("Van < 3 vizsgálatot tartalmazó alcsoport: az alcsoport-becslések és a "
                        "különbség-teszt erősen bizonytalanok.")
    if len(yi) < 10:
        warnings.append("k < 10: az alcsoport-elemzés ereje kicsi; csak előre tervezett, "
                        "kevés alcsoportot vizsgálj (Cochrane Handbook 10.11).")
    return MetaResult(
        kind="subgroup", model=model, groups=out_groups, overall=overall,
        Q_between=q_between, df_between=df, p_between=p_between,
        Q_between_se="ivhet" if model == "ivhet" else "wald",
        Q_between_note=(("Q_between a csoportbecslések IVhet-standardhibáiból (z-alapú, a csoporton belüli "
                         "heterogenitással inflált), χ²(G−1) eloszlással") if model == "ivhet" else
                        "Q_between a csoportbecslések Wald-standardhibáiból (nem HKSJ), "
                        "χ²(G−1) eloszlással"
                        + ("; közös τ² mellett = metafor rma(mods=~alcsoport) QM" if tau2_common is not None
                           else "")),
        common_tau2=tau2_common, common_tau2_info=tau2_common_info, warnings=warnings,
    )


# ------------------------------------------------------------ meta-regresszió
MR_TAU2_METHODS = ("REML", "ML", "DL", "PM", "HE", "SJ", "FE")


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


def _qe(x, y, v, tau2):
    """Általánosított Q_E(τ²) = Σ w_i e_i² (WLS-reziduumok, w = 1/(v + τ²))."""
    w = [1.0 / (vi + tau2) for vi in v]
    _, _, e = _wls(x, y, w)
    return sum(wi * ei * ei for wi, ei in zip(w, e))


def mr_loglik(x, y, v, tau2, reml=True):
    """(RE)ML log-likelihood (konstansok nélkül) a vegyes hatású meta-regresszióhoz:
    ML = -½[Σ log(v_i+τ²) + Σ w_i e_i²]; REML = ML - ½ log det(XᵀWX)."""
    w = [1.0 / (vi + tau2) for vi in v]
    b, m, e = _wls(x, y, w)
    ll = -0.5 * (sum(math.log(vi + tau2) for vi in v) + sum(wi * ei * ei for wi, ei in zip(w, e)))
    if reml:
        ll -= 0.5 * la.logdet_spd(la.xtwx(x, w))
    return ll


def _tau2_he_mr(x, y, v):
    """Hedges (HE) momentum-becslő moderátorokkal (metafor): (RSS_OLS - tr(P_OLS V)) / (k - p)."""
    k, p = len(y), len(x[0])
    ones = [1.0] * k
    b, m, e = _wls(x, y, ones)
    rss = sum(ei * ei for ei in e)
    hdiag = [sum(row[i] * sum(m[i][j] * row[j] for j in range(p)) for i in range(p)) for row in x]
    tr_pv = sum(vi * (1.0 - h) for vi, h in zip(v, hdiag))
    return max(0.0, (rss - tr_pv) / (k - p))


def _tau2_sj_mr(x, y, v):
    """Sidik–Jonkman moderátorokkal (metafor): τ²₀ = Σ(y - ȳ)²/k, τ² = τ²₀ · Q_E(τ²₀)/(k - p)."""
    k, p = len(y), len(x[0])
    ybar = sum(y) / k
    t0 = sum((a - ybar) ** 2 for a in y) / k
    if t0 <= 0:
        return 0.0
    return t0 * _qe(x, y, v, t0) / (k - p)


def _tau2_pm_mr(x, y, v, tol=1e-12, maxiter=1000):
    """Paule–Mandel moderátorokkal: Q_E(τ²) = k - p gyöke (Q_E monoton csökkenő)."""
    k, p = len(y), len(x[0])
    target = float(k - p)
    if _qe(x, y, v, 0.0) <= target:
        return 0.0
    lo, hi = 0.0, max(_tau2_he_mr(x, y, v), variance_scale(v), 1e-4)
    while _qe(x, y, v, hi) > target:
        hi *= 2.0
        if hi > 1e12:
            raise ModelError("PM: nem találok felső korlátot")
    for _ in range(maxiter):
        mid = 0.5 * (lo + hi)
        if _qe(x, y, v, mid) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol * max(hi, variance_scale(v)):
            break
    return 0.5 * (lo + hi)


def _fs_mr(x, y, v, method, start, scale, tol=1e-10, maxiter=1000):
    """REML/ML Fisher-scoring moderátorokkal (metafor rma.uni), lépésfelezéssel."""
    tau2 = max(0.0, start)
    it = -1
    for it in range(maxiter):
        w = [1.0 / (vi + tau2) for vi in v]
        b, m, e = _wls(x, y, w)
        r2w2 = sum((wi * ei) ** 2 for wi, ei in zip(w, e))
        if method == "REML":
            tr_p, tr_pp = _traces(x, w, m)
            if not (tr_pp > 0) or not math.isfinite(tr_pp):
                return tau2, False, it + 1
            adj = (r2w2 - tr_p) / tr_pp
        else:  # ML
            adj = (r2w2 - sum(w)) / sum(a * a for a in w)
        if not math.isfinite(adj):
            return tau2, False, it + 1
        while tau2 + adj < 0:
            adj /= 2.0
            if abs(adj) < 1e-300:
                adj = -tau2
                break
        tau2 += adj
        if abs(adj) <= tol * max(tau2, scale):
            return tau2, True, it + 1
    return tau2, False, it + 1


def _tau2_mr(x, y, v, method):
    """Reziduális τ² a meta-regresszióhoz; (τ², info) párt ad.

    REML/ML: Fisher-scoring a HE-becslésből indulva (mint a metafor), utána a τ² = 0 határ
    és egy durva profil-likelihood rács ellenőrzése (models.optimize_tau2), így a
    lokális maximumon elakadt iteráció nem ad csendben rossz τ²-t."""
    k, p = len(y), len(x[0])
    if method == "FE":
        return 0.0, {}
    if method == "DL":
        w0 = [1.0 / vi for vi in v]
        b0, m0, e0 = _wls(x, y, w0)
        qe = sum(wi * ei * ei for wi, ei in zip(w0, e0))
        tr_p0, _ = _traces(x, w0, m0)
        return (max(0.0, (qe - (k - p)) / tr_p0) if tr_p0 > 0 else 0.0), {}
    if method == "HE":
        return _tau2_he_mr(x, y, v), {}
    if method == "SJ":
        return _tau2_sj_mr(x, y, v), {}
    if method == "PM":
        return _tau2_pm_mr(x, y, v), {}
    if method not in ("REML", "ML"):
        raise ModelError("meta-regresszió τ²-becslő: %s" % ", ".join(MR_TAU2_METHODS))
    scale = variance_scale(v)
    reml = method == "REML"
    ybar = sum(y) / k
    ss = sum((a - ybar) ** 2 for a in y)
    _, _, e_ols = _wls(x, y, [1.0] * k)
    ss = max(ss, sum(a * a for a in e_ols))
    hi = 2.0 * ss + 2.0 * max(v)
    return optimize_tau2(lambda t: mr_loglik(x, y, v, t, reml),
                         lambda s: _fs_mr(x, y, v, method, s, scale),
                         _tau2_he_mr(x, y, v), scale, hi)


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
    if method not in MR_TAU2_METHODS:
        raise ModelError("meta-regresszió τ²-becslő: %s" % ", ".join(MR_TAU2_METHODS))
    tau2, tau2_info = _tau2_mr(x, yi, vi, method)
    warnings = tau2_info_warnings(method, tau2_info)
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
        stat = ratio_stat(b[j], se)
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
        kind="meta_regression", tau2=tau2, tau2_method=method, tau2_info=tau2_info, test=test, k=k, p=p,
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
