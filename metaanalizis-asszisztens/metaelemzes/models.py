# -*- coding: utf-8 -*-
"""Összesítő modellek: fix (közös) hatás, véletlen hatás, Mantel–Haenszel, Peto.

τ²-becslők: DL, REML, ML, PM, HE, SJ.  CI: z (Wald), t, HKSJ, HKSJ ad hoc (max(1, q)).
Heterogenitás: Q, I² (Higgins–Thompson), H², τ², τ, CI-k (Q-profile, Higgins–Thompson).
Predikciós intervallum: t(k-2) (Higgins 2009 / Borenstein 17. fejezet), z vagy t(k-1).

Források: Borenstein et al. 2009 (11–17. fejezet); Viechtbauer 2005, 2007;
Hartung & Knapp 2001; Sidik & Jonkman 2002; IntHout et al. 2014; Higgins & Thompson 2002;
Cochrane Handbook v6 10. fejezet; Khan 2020 (4–5. fejezet).
"""
import math

from . import distributions as dist

TAU2_METHODS = ("DL", "REML", "ML", "PM", "HE", "SJ")
CI_METHODS = ("z", "t", "hksj", "hksj_adhoc")
PI_METHODS = ("t_k-2", "t_k-1", "z")


class ModelError(ValueError):
    pass


class MetaResult(object):
    """Egy összesítés eredménye; a to_dict() JSON-barát kimenetet ad."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def get(self, key, default=None):
        return self.__dict__.get(key, default)

    def to_dict(self):
        out = {}
        for k, v in self.__dict__.items():
            if k.startswith("_"):
                continue
            out[k] = v
        return out


# ------------------------------------------------------------- alapelemek
def _check(yi, vi):
    if len(yi) != len(vi):
        raise ModelError("yi és vi hossza eltér")
    if len(yi) < 1:
        raise ModelError("legalább 1 vizsgálat kell")
    for v in vi:
        if not (v > 0) or math.isinf(v):
            raise ModelError("minden vi-nek pozitív, véges számnak kell lennie")


def _wmean(yi, w):
    sw = sum(w)
    return sum(a * b for a, b in zip(w, yi)) / sw, sw


def cochran_q(yi, vi):
    w = [1.0 / v for v in vi]
    mu, sw = _wmean(yi, w)
    return sum(wi * (y - mu) ** 2 for wi, y in zip(w, yi))


def typical_within_variance(vi):
    """s̃² = (k-1)Σw / ((Σw)² - Σw²)  (Higgins & Thompson 2002; metafor)."""
    w = [1.0 / v for v in vi]
    sw = sum(w)
    sw2 = sum(x * x for x in w)
    k = len(vi)
    den = sw * sw - sw2
    if k < 2 or den <= 0:
        return None
    return (k - 1) * sw / den


def generalized_q(yi, vi, tau2):
    w = [1.0 / (v + tau2) for v in vi]
    mu, _ = _wmean(yi, w)
    return sum(wi * (y - mu) ** 2 for wi, y in zip(w, yi))


def reml_loglik(yi, vi, tau2):
    w = [1.0 / (v + tau2) for v in vi]
    mu, sw = _wmean(yi, w)
    return -0.5 * (sum(math.log(v + tau2) for v in vi) + math.log(sw)
                   + sum(wi * (y - mu) ** 2 for wi, y in zip(w, yi)))


def ml_loglik(yi, vi, tau2):
    w = [1.0 / (v + tau2) for v in vi]
    mu, sw = _wmean(yi, w)
    return -0.5 * (sum(math.log(v + tau2) for v in vi)
                   + sum(wi * (y - mu) ** 2 for wi, y in zip(w, yi)))


# ------------------------------------------------------------- τ²-becslők
def tau2_dl(yi, vi):
    k = len(yi)
    if k < 2:
        return 0.0
    w = [1.0 / v for v in vi]
    sw = sum(w)
    c = sw - sum(x * x for x in w) / sw
    q = cochran_q(yi, vi)
    return max(0.0, (q - (k - 1)) / c)


def tau2_he(yi, vi):
    k = len(yi)
    if k < 2:
        return 0.0
    ybar = sum(yi) / k
    return max(0.0, sum((y - ybar) ** 2 for y in yi) / (k - 1) - sum(vi) / k)


def tau2_sj(yi, vi):
    k = len(yi)
    if k < 2:
        return 0.0
    ybar = sum(yi) / k
    t0 = sum((y - ybar) ** 2 for y in yi) / k
    if t0 <= 0:
        return 0.0
    w = [1.0 / (v + t0) for v in vi]
    mu, _ = _wmean(yi, w)
    return t0 * sum(wi * (y - mu) ** 2 for wi, y in zip(w, yi)) / (k - 1)


def tau2_pm(yi, vi, tol=1e-12, maxiter=1000):
    """Paule–Mandel: Q_gen(τ²) = k-1 megoldása (Q_gen monoton csökkenő)."""
    k = len(yi)
    if k < 2:
        return 0.0
    target = k - 1.0
    if generalized_q(yi, vi, 0.0) <= target:
        return 0.0
    lo, hi = 0.0, max(tau2_dl(yi, vi), 1e-4)
    while generalized_q(yi, vi, hi) > target:
        hi *= 2.0
        if hi > 1e12:
            raise ModelError("PM: nem találok felső korlátot")
    for _ in range(maxiter):
        mid = 0.5 * (lo + hi)
        if generalized_q(yi, vi, mid) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def _fisher_scoring(yi, vi, kind, start, tol=1e-10, maxiter=1000):
    """REML/ML Fisher-scoring lépésfelezéssel (Viechtbauer 2005)."""
    tau2 = max(0.0, start)
    converged = False
    for it in range(maxiter):
        w = [1.0 / (v + tau2) for v in vi]
        mu, sw = _wmean(yi, w)
        sw2 = sum(x * x for x in w)
        r2w2 = sum((wi * (y - mu)) ** 2 for wi, y in zip(w, yi))
        if kind == "REML":
            sw3 = sum(x ** 3 for x in w)
            tr_p = sw - sw2 / sw
            tr_pp = sw2 - 2.0 * sw3 / sw + (sw2 / sw) ** 2
            adj = (r2w2 - tr_p) / tr_pp
        else:  # ML
            adj = (r2w2 - sw) / sw2
        while tau2 + adj < 0:
            adj /= 2.0
            if abs(adj) < 1e-300:
                adj = -tau2
                break
        tau2 += adj
        if abs(adj) <= tol * max(1.0, tau2):
            converged = True
            break
    return tau2, converged, it + 1


def _maximize_1d(fn, hi_guess):
    """Tartalék: arany-metszés a log(1+τ²) skálán [0, felső korlát]."""
    hi = max(hi_guess, 1.0)
    while fn(hi * 2) > fn(hi) and hi < 1e10:
        hi *= 2
    hi *= 2
    a, b = 0.0, math.log1p(hi)
    g = (math.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    f = lambda u: fn(math.expm1(u))
    fc, fd = f(c), f(d)
    for _ in range(300):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
        if b - a < 1e-13:
            break
    best = math.expm1(0.5 * (a + b))
    return 0.0 if fn(0.0) >= fn(best) else best


def tau2_reml(yi, vi):
    if len(yi) < 2:
        return 0.0, {"converged": True, "iterations": 0}
    t, conv, it = _fisher_scoring(yi, vi, "REML", tau2_he(yi, vi))
    if not conv:
        t = _maximize_1d(lambda x: reml_loglik(yi, vi, x), tau2_dl(yi, vi) + 1.0)
    if t < 1e-10:
        t = 0.0
    return t, {"converged": conv, "iterations": it}


def tau2_ml(yi, vi):
    if len(yi) < 2:
        return 0.0, {"converged": True, "iterations": 0}
    t, conv, it = _fisher_scoring(yi, vi, "ML", tau2_he(yi, vi))
    if not conv:
        t = _maximize_1d(lambda x: ml_loglik(yi, vi, x), tau2_dl(yi, vi) + 1.0)
    if t < 1e-10:
        t = 0.0
    return t, {"converged": conv, "iterations": it}


def estimate_tau2(yi, vi, method="REML"):
    method = method.upper()
    if method == "DL":
        return tau2_dl(yi, vi), {}
    if method == "HE":
        return tau2_he(yi, vi), {}
    if method == "SJ":
        return tau2_sj(yi, vi), {}
    if method == "PM":
        return tau2_pm(yi, vi), {}
    if method == "REML":
        return tau2_reml(yi, vi)
    if method == "ML":
        return tau2_ml(yi, vi)
    raise ModelError("ismeretlen τ²-becslő: %r (lehetséges: %s)" % (method, ", ".join(TAU2_METHODS)))


# ----------------------------------------------------- heterogenitás CI-k
def tau2_ci_qprofile(yi, vi, level=0.95):
    """τ² konfidenciaintervallum Q-profile módszerrel (Viechtbauer 2007)."""
    k = len(yi)
    if k < 2:
        return None, None
    df = k - 1
    alpha = 1.0 - level
    crit_hi = dist.chi2_ppf(1 - alpha / 2.0, df)   # alsó határhoz
    crit_lo = dist.chi2_ppf(alpha / 2.0, df)       # felső határhoz

    def solve(target):
        if generalized_q(yi, vi, 0.0) <= target:
            return 0.0
        lo, hi = 0.0, max(1e-4, tau2_dl(yi, vi) * 2 + 1e-4)
        while generalized_q(yi, vi, hi) > target:
            hi *= 2.0
            if hi > 1e12:
                return math.inf
        for _ in range(400):
            mid = 0.5 * (lo + hi)
            if generalized_q(yi, vi, mid) > target:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-12 * max(1.0, hi):
                break
        return 0.5 * (lo + hi)

    return solve(crit_hi), solve(crit_lo)


def i2_ci_higgins_thompson(q, k, level=0.95):
    """I² és H CI a Higgins–Thompson (2002) teszt-alapú módszerrel (Borenstein 16. fejezet)."""
    if k < 3:
        return None, None, None, None
    df = k - 1
    z = dist.norm_ppf(0.5 + level / 2.0)
    if q > k:
        b = 0.5 * (math.log(q) - math.log(df)) / (math.sqrt(2 * q) - math.sqrt(2 * k - 3))
    else:
        b = math.sqrt(1.0 / (2 * (k - 2)) * (1 - 1.0 / (3 * (k - 2) ** 2)))
    lnh = 0.5 * math.log(q / df) if q > 0 else -math.inf
    h_lo = math.exp(lnh - z * b) if q > 0 else 0.0
    h_hi = math.exp(lnh + z * b) if q > 0 else 0.0
    i2_lo = max(0.0, (h_lo ** 2 - 1) / h_lo ** 2) * 100 if h_lo > 0 else 0.0
    i2_hi = max(0.0, (h_hi ** 2 - 1) / h_hi ** 2) * 100 if h_hi > 0 else 0.0
    return i2_lo, i2_hi, max(1.0, h_lo), max(1.0, h_hi)


def tau2_ci_bhhr(q, k, c, level=0.95):
    """τ² CI Borenstein et al. (2009, 16.13–16.16) szerint (a Higgins–Thompson H-intervallumból)."""
    if k < 3 or c <= 0:
        return None, None
    df = k - 1
    z = dist.norm_ppf(0.5 + level / 2.0)
    if q > df + 1:
        b = 0.5 * (math.log(q) - math.log(df)) / (math.sqrt(2 * q) - math.sqrt(2 * df - 1))
    else:
        b = math.sqrt(1.0 / (2 * (df - 1) * (1 - 1.0 / (3 * (df - 1) ** 2))))
    if q <= 0:
        return 0.0, 0.0
    lnh = 0.5 * math.log(q / df)
    lo = math.exp(lnh - z * b)
    hi = math.exp(lnh + z * b)
    return max(0.0, df * (lo ** 2 - 1) / c), max(0.0, df * (hi ** 2 - 1) / c)


def heterogeneity(yi, vi, tau2=None, level=0.95):
    """Heterogenitási statisztikák (a modelltől független blokk)."""
    k = len(yi)
    df = k - 1
    w = [1.0 / v for v in vi]
    sw = sum(w)
    c = sw - sum(x * x for x in w) / sw if k > 1 else 0.0
    q = cochran_q(yi, vi)
    out = {
        "k": k, "Q": q, "df": df,
        "p_Q": dist.chi2_sf(q, df) if df > 0 else None,
        "I2": (max(0.0, (q - df) / q) * 100.0) if (df > 0 and q > 0) else 0.0,
        "H2": (q / df) if df > 0 else None,
        "C": c,
        "tau2_DL": tau2_dl(yi, vi),
        "s2_typical": typical_within_variance(vi),
    }
    lo, hi, hlo, hhi = i2_ci_higgins_thompson(q, k, level)
    out["I2_ci_HT"] = [lo, hi] if lo is not None else None
    out["H_ci_HT"] = [hlo, hhi] if hlo is not None else None
    t_lo, t_hi = tau2_ci_bhhr(q, k, c, level)
    out["tau2_ci_BHHR"] = [t_lo, t_hi] if t_lo is not None else None
    if k >= 2:
        qlo, qhi = tau2_ci_qprofile(yi, vi, level)
        out["tau2_ci_QP"] = [qlo, qhi]
        s2 = out["s2_typical"]
        if s2:
            out["I2_ci_QP"] = [100 * qlo / (qlo + s2), 100 * qhi / (qhi + s2) if not math.isinf(qhi) else 100.0]
            out["H2_ci_QP"] = [(qlo + s2) / s2, (qhi + s2) / s2 if not math.isinf(qhi) else math.inf]
        if tau2 is not None and s2:
            out["I2_from_tau2"] = 100 * tau2 / (tau2 + s2)
            out["H2_from_tau2"] = (tau2 + s2) / s2
    return out


# ------------------------------------------------------------------ fő modell
def meta_analysis(yi, vi, model="random", tau2_method="REML", ci_method=None, level=0.95,
                  pi_method="t_k-2", labels=None, tau2_fixed=None):
    """Inverz-variancia súlyozott összesítés.

    model: 'fixed' (közös hatás), 'random', vagy 'ivhet' (Doi et al. 2015 inverz-variancia
           heterogenitás modell: FE pontbecslés, Var = Σ (w_i/Σw)²·(v_i + τ²_DL); Khan 2020 4. fejezet).
    ci_method: 'z' | 't' | 'hksj' | 'hksj_adhoc'; alapértelmezés: fixed→z, random→hksj.
    tau2_fixed: ha megadott, ezt a τ²-t használja (pl. közös τ² alcsoportokhoz).
    """
    _check(yi, vi)
    yi = [float(y) for y in yi]
    vi = [float(v) for v in vi]
    k = len(yi)
    model = model.lower()
    if model in ("fe", "fixed", "common", "ce"):
        model = "fixed"
    elif model in ("re", "random"):
        model = "random"
    elif model == "ivhet":
        return _ivhet(yi, vi, level, labels)
    else:
        raise ModelError("ismeretlen modell: %r" % model)
    if ci_method is None:
        ci_method = "z" if model == "fixed" else "hksj"
    if ci_method not in CI_METHODS:
        raise ModelError("ismeretlen CI-módszer: %r" % ci_method)
    if pi_method not in PI_METHODS:
        raise ModelError("ismeretlen PI-módszer: %r" % pi_method)
    warnings = []
    tau2_info = {}
    if model == "fixed":
        tau2 = 0.0
        tau2_method_used = None
    else:
        if tau2_fixed is not None:
            tau2, tau2_method_used = float(tau2_fixed), "rögzített"
        else:
            tau2, tau2_info = estimate_tau2(yi, vi, tau2_method)
            tau2_method_used = tau2_method.upper()
            if tau2_info.get("converged") is False:
                warnings.append("A(z) %s τ²-becslés Fisher-scoringja nem konvergált; "
                                "tartalék 1D maximalizálás futott." % tau2_method_used)
    w = [1.0 / (v + tau2) for v in vi]
    mu, sw = _wmean(yi, w)
    se_wald = math.sqrt(1.0 / sw)
    alpha = 1.0 - level
    df_t = None
    q_hk = None
    if ci_method == "z" or k < 2:
        if ci_method != "z":
            warnings.append("k < 2: a(z) %s helyett z-alapú CI." % ci_method)
            ci_method = "z"
        se = se_wald
        crit = dist.norm_ppf(1 - alpha / 2)
        stat = mu / se
        p = dist.z_two_sided_p(stat)
        test = "z"
    else:
        df_t = k - 1
        if ci_method == "t":
            se = se_wald
        else:
            q_hk = sum(wi * (y - mu) ** 2 for wi, y in zip(w, yi)) / (k - 1)
            qq = max(1.0, q_hk) if ci_method == "hksj_adhoc" else q_hk
            se = math.sqrt(qq / sw)
            if ci_method == "hksj" and q_hk < 1:
                warnings.append("HKSJ: q = %.3f < 1, ezért a HKSJ-CI szűkebb lehet a Wald-CI-nál; "
                                "érzékenységi elemzésként fontold meg a hksj_adhoc módszert." % q_hk)
        crit = dist.t_ppf(1 - alpha / 2, df_t)
        stat = mu / se if se > 0 else math.inf
        p = dist.t_two_sided_p(stat, df_t)
        test = "t"
    het = heterogeneity(yi, vi, tau2 if model == "random" else None, level)
    # predikciós intervallum (csak véletlen hatás)
    pi_lo = pi_hi = None
    pi_df = None
    if model == "random":
        if pi_method == "t_k-2":
            if k >= 3:
                pi_df = k - 2
                pcrit = dist.t_ppf(1 - alpha / 2, pi_df)
            else:
                pcrit = None
                warnings.append("Predikciós intervallum t(k-2)-vel k >= 3 esetén számolható.")
        elif pi_method == "t_k-1":
            pi_df = k - 1 if k >= 2 else None
            pcrit = dist.t_ppf(1 - alpha / 2, pi_df) if pi_df else None
        else:
            pcrit = dist.norm_ppf(1 - alpha / 2)
        if pcrit is not None:
            half = pcrit * math.sqrt(tau2 + se ** 2)
            pi_lo, pi_hi = mu - half, mu + half
    tot_w = sw
    weights_pct = [100.0 * wi / tot_w for wi in w]
    if k < 5 and model == "random":
        warnings.append("k = %d < 5: a τ² becslése nagyon bizonytalan; a CI-t és a PI-t "
                        "óvatosan értelmezd." % k)
    res = MetaResult(
        model=model, k=k, estimate=mu, se=se, se_wald=se_wald,
        ci_lower=mu - crit * se, ci_upper=mu + crit * se, level=level,
        test=test, stat=stat, df=df_t, p=p, ci_method=ci_method,
        tau2=tau2 if model == "random" else 0.0,
        tau=math.sqrt(tau2) if model == "random" else 0.0,
        tau2_method=tau2_method_used, tau2_info=tau2_info, q_hksj=q_hk,
        pi_lower=pi_lo, pi_upper=pi_hi, pi_method=pi_method if model == "random" else None,
        pi_df=pi_df,
        Q=het["Q"], Q_df=het["df"], p_Q=het["p_Q"], I2=het["I2"], H2=het["H2"],
        heterogeneity=het, weights_pct=weights_pct,
        yi=yi, vi=vi, labels=list(labels) if labels else ["#%d" % (i + 1) for i in range(k)],
        warnings=warnings,
    )
    return res


def _ivhet(yi, vi, level=0.95, labels=None):
    k = len(yi)
    w = [1.0 / v for v in vi]
    mu, sw = _wmean(yi, w)
    tau2 = tau2_dl(yi, vi)
    var = sum((wi / sw) ** 2 * (v + tau2) for wi, v in zip(w, vi))
    se = math.sqrt(var)
    crit = dist.norm_ppf(0.5 + level / 2.0)
    stat = mu / se
    het = heterogeneity(yi, vi, tau2, level)
    return MetaResult(
        model="ivhet", k=k, estimate=mu, se=se, se_wald=math.sqrt(1.0 / sw),
        ci_lower=mu - crit * se, ci_upper=mu + crit * se, level=level, test="z", stat=stat, df=None,
        p=dist.z_two_sided_p(stat), ci_method="z", tau2=tau2, tau=math.sqrt(tau2), tau2_method="DL",
        tau2_info={}, q_hksj=None, pi_lower=None, pi_upper=None, pi_method=None, pi_df=None,
        Q=het["Q"], Q_df=het["df"], p_Q=het["p_Q"], I2=het["I2"], H2=het["H2"], heterogeneity=het,
        weights_pct=[100.0 * wi / sw for wi in w],
        yi=list(yi), vi=list(vi), labels=list(labels) if labels else ["#%d" % (i + 1) for i in range(k)],
        warnings=[] if k >= 2 else ["IVhet: k < 2."],
    )


# ------------------------------------------------------------ Mantel–Haenszel
def _drop_incomplete(labels, rows):
    keep_l, keep_r, excluded = [], [], []
    for lab, row in zip(labels, rows):
        if any(v is None for v in row):
            excluded.append((lab, "hiányzó cellaérték"))
            continue
        keep_l.append(lab)
        keep_r.append(row)
    return keep_l, keep_r, excluded


def mantel_haenszel(e1, n1, e2, n2, measure="OR", level=0.95, labels=None, cc=0.5, rd_var="sato"):
    """Mantel–Haenszel fix hatású összesítés (OR, RR, RD).

    Varianciák: OR — Robins–Breslow–Greenland (1986); RR — Greenland & Robins (1985);
    RD — rd_var='sato': Sato, Greenland & Robins (1989), kettősen konzisztens (metafor
    alapértelmezés); rd_var='gr': Greenland & Robins (1985) (RevMan-képlet).  A pontbecslés folytonossági korrekció nélküli
    (a kettős-nulla vizsgálatok OR/RR-nél nem járulnak hozzá). A heterogenitási Q a
    vizsgálatonkénti inverz-variancia becslésekből számolódik az MH-becslés körül
    (RevMan-gyakorlat).
    """
    measure = measure.upper()
    if measure not in ("OR", "RR", "RD"):
        raise ModelError("MH: OR, RR vagy RD")
    rows = list(zip(e1, n1, e2, n2))
    labels = list(labels) if labels else ["#%d" % (i + 1) for i in range(len(rows))]
    labels, rows, excluded = _drop_incomplete(labels, rows)
    sum_r = sum_s = 0.0
    sum_pr = sum_ps_qr = sum_qs = 0.0
    rr_num = rr_den = rr_var = 0.0
    rd_num = rd_den = rd_gr = 0.0
    rd_p = rd_q = 0.0
    used = []
    mh_w = {}
    for lab, (a, m1, c, m2) in zip(labels, rows):
        a, c, m1, m2 = float(a), float(c), float(m1), float(m2)
        b, d = m1 - a, m2 - c
        n = m1 + m2
        if measure in ("OR", "RR") and ((a == 0 and c == 0) or (b == 0 and d == 0)):
            excluded.append((lab, "kettős nulla / kettős 100% — nem járul hozzá"))
            continue
        used.append(lab)
        mh_w[lab] = (b * c / n) if measure == "OR" else ((c * m1 / n) if measure == "RR" else m1 * m2 / n)
        if measure == "OR":
            r, s = a * d / n, b * c / n
            p_, q_ = (a + d) / n, (b + c) / n
            sum_r += r
            sum_s += s
            sum_pr += p_ * r
            sum_ps_qr += p_ * s + q_ * r
            sum_qs += q_ * s
        elif measure == "RR":
            rr_num += a * m2 / n
            rr_den += c * m1 / n
            rr_var += (m1 * m2 * (a + c) / n - a * c) / n
        else:
            rd_num += (a * m2 - c * m1) / n
            rd_den += m1 * m2 / n
            rd_gr += (a * b * m2 ** 3 + c * d * m1 ** 3) / (m1 * m2 * n * n)
            rd_p += c * (m1 / n) ** 2 - a * (m2 / n) ** 2 + (m1 / n) * (m2 / n) * (m2 - m1) / 2.0
            rd_q += (a * d / n + c * b / n) / 2.0
    if not used:
        raise ModelError("MH: nincs felhasználható vizsgálat")
    if measure == "OR":
        if sum_r <= 0 or sum_s <= 0:
            raise ModelError("MH OR nem becsülhető (Σ R vagy Σ S = 0)")
        est = math.log(sum_r / sum_s)
        var = sum_pr / (2 * sum_r ** 2) + sum_ps_qr / (2 * sum_r * sum_s) + sum_qs / (2 * sum_s ** 2)
    elif measure == "RR":
        if rr_num <= 0 or rr_den <= 0:
            raise ModelError("MH RR nem becsülhető")
        est = math.log(rr_num / rr_den)
        var = rr_var / (rr_num * rr_den)
    else:
        est = rd_num / rd_den
        if rd_var == "gr":
            var = rd_gr / rd_den ** 2
        else:
            var = (est * rd_p + rd_q) / rd_den ** 2
    se = math.sqrt(var)
    z = dist.norm_ppf(0.5 + level / 2)
    # heterogenitás: IV-becslések az MH körül (metafor rma.mh konvenció: a kettős-nulla
    # táblák kimaradnak, a nulla cellás táblák minden cellájához +cc kerül — RD-nél is)
    yi, vi, labs = [], [], []
    for lab, (a, m1, c, m2) in zip(labels, rows):
        a, c, m1, m2 = float(a), float(c), float(m1), float(m2)
        b, d = m1 - a, m2 - c
        if (a == 0 and c == 0) or (b == 0 and d == 0):
            continue
        if min(a, b, c, d) == 0:
            a, b, c, d = a + cc, b + cc, c + cc, d + cc
        if measure == "OR":
            y, v = math.log(a * d / (b * c)), 1 / a + 1 / b + 1 / c + 1 / d
        elif measure == "RR":
            y, v = math.log((a / (a + b)) / (c / (c + d))), 1 / a - 1 / (a + b) + 1 / c - 1 / (c + d)
        else:
            p1, p2 = a / (a + b), c / (c + d)
            y, v = p1 - p2, p1 * (1 - p1) / (a + b) + p2 * (1 - p2) / (c + d)
        yi.append(y)
        vi.append(v)
        labs.append(lab)
    q = sum((y - est) ** 2 / v for y, v in zip(yi, vi))
    df = len(yi) - 1
    tot = sum(mh_w.values())
    w_mh = {lab: 100.0 * w / tot for lab, w in mh_w.items()} if tot > 0 else {}
    het = heterogeneity(yi, vi) if len(yi) >= 1 else {}
    stat = est / se
    return MetaResult(
        model="MH", measure=measure, k=len(rows), k_estimable=len(used), estimate=est, se=se, se_wald=se,
        ci_lower=est - z * se, ci_upper=est + z * se, level=level, test="z", stat=stat,
        df=None, p=dist.z_two_sided_p(stat), ci_method="z", tau2=0.0, tau=0.0,
        tau2_method=None, pi_lower=None, pi_upper=None, pi_method=None,
        Q=q, Q_df=df, p_Q=dist.chi2_sf(q, df) if df > 0 else None,
        I2=(max(0.0, (q - df) / q) * 100 if df > 0 and q > 0 else 0.0),
        H2=(q / df if df > 0 else None), heterogeneity=het,
        weights_pct=None, weights_by_label=w_mh, yi=yi, vi=vi, labels=labs, excluded=excluded,
        warnings=[],
    )


def peto(e1, n1, e2, n2, level=0.95, labels=None):
    """Peto-féle egylépéses OR (ritka események, kiegyensúlyozott karok esetén)."""
    rows = list(zip(e1, n1, e2, n2))
    labels = list(labels) if labels else ["#%d" % (i + 1) for i in range(len(rows))]
    labels, rows, excluded = _drop_incomplete(labels, rows)
    o_e, var_list, yi, vi, labs = [], [], [], [], []
    for lab, (a, m1, c, m2) in zip(labels, rows):
        a, c, m1, m2 = float(a), float(c), float(m1), float(m2)
        n = m1 + m2
        events = a + c
        nonevents = n - events
        if n <= 1 or events == 0 or nonevents == 0:
            excluded.append((lab, "nincs információ (0 vagy 100% esemény összesen)"))
            continue
        e = m1 * events / n
        v = m1 * m2 * events * nonevents / (n * n * (n - 1))
        o_e.append(a - e)
        var_list.append(v)
        yi.append((a - e) / v)
        vi.append(1.0 / v)
        labs.append(lab)
    if not var_list:
        raise ModelError("Peto: nincs felhasználható vizsgálat")
    sv = sum(var_list)
    est = sum(o_e) / sv
    se = math.sqrt(1.0 / sv)
    q = sum(x * x / v for x, v in zip(o_e, var_list)) - sum(o_e) ** 2 / sv
    df = len(var_list) - 1
    z = dist.norm_ppf(0.5 + level / 2)
    stat = est / se
    return MetaResult(
        model="PETO", measure="OR", k=len(rows), k_estimable=len(var_list), estimate=est, se=se, se_wald=se,
        ci_lower=est - z * se, ci_upper=est + z * se, level=level, test="z", stat=stat,
        df=None, p=dist.z_two_sided_p(stat), ci_method="z", tau2=0.0, tau=0.0,
        tau2_method=None, pi_lower=None, pi_upper=None, pi_method=None,
        Q=q, Q_df=df, p_Q=dist.chi2_sf(q, df) if df > 0 else None,
        I2=(max(0.0, (q - df) / q) * 100 if df > 0 and q > 0 else 0.0),
        H2=(q / df if df > 0 else None), heterogeneity=heterogeneity(yi, vi),
        weights_pct=[100 * v / sv for v in var_list],
        weights_by_label={lab: 100 * v / sv for lab, v in zip(labs, var_list)}, yi=yi, vi=vi, labels=labs,
        excluded=excluded, warnings=[],
    )
