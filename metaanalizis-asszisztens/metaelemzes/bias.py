# -*- coding: utf-8 -*-
"""Kis-vizsgálat hatások és publikációs torzítás.

- Egger-féle regressziós teszt (klasszikus: y/se ~ 1/se, OLS; Egger et al. 1997); a
  tengelymetszet CI-je t(k−2) vagy (dmetar::eggers.test) normális kvantilissel
- Begg–Mazumdar rangkorreláció (Kendall τ; Begg & Mazumdar 1994): pontos eloszlás (metafor) vagy
  normális közelítés folytonossági korrekcióval / nélküle (Stata metabias)
- Trim-and-fill (Duval & Tweedie 2000; L0 és R0; a metafor::trimfill algoritmusát követi; a
  vágás külön modellel is végezhető, pl. közös hatással, mint a meta::trimfill alapértelmezése)
- Rosenthal-féle fail-safe N (csak tájékoztató; nem ajánlott önálló bizonyítékként)

Fontos (Sterne et al. 2011, BMJ; Cochrane Handbook 13. fejezet): a tesztek ereje k < 10
esetén kicsi, és az aszimmetria nem egyenlő a publikációs torzítással (heterogenitás,
kis vizsgálatok eltérő minősége, véletlen is okozhatja).
"""
import functools
import math

from . import distributions as dist
from .models import meta_analysis, ModelError, MetaResult, ratio_stat
from .moderators import meta_regression, MR_TAU2_METHODS


EGGER_CI_DISTS = ("t", "norm")
BEGG_METHODS = ("auto", "exact", "normal")


def egger_test(yi, vi, ci_dist="t", level=0.95):
    """Egger-féle regressziós teszt: y_i/se_i = a + b·(1/se_i) + ε (OLS); H0: a = 0, t(k−2).

    ci_dist: a tengelymetszet CI-jének kritikus értéke — 't' (alapértelmezés; t(k−2), a
    teszttel konzisztens, = metafor regtest / R lm confint) vagy 'norm' (z-kvantilis, mint a
    dmetar::eggers.test nyomtatott CI-je). A t-statisztika, a df és a p mindkét esetben t(k−2)."""
    if ci_dist not in EGGER_CI_DISTS:
        raise ValueError("egger_test: ci_dist 't' vagy 'norm' lehet (kapott: %r)" % (ci_dist,))
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
    rss = sum(r * r for r in resid)
    warn = []
    # tökéletes illeszkedés (pl. minden y_i azonos → z_i = c·prec_i): a reziduális variancia
    # csak kerekítési zaj, a t = 0/0 nem értelmezhető (metafor regtest: NA)
    perfect = rss <= 1e-20 * max(sum(z * z for z in zs), 1e-300)
    if perfect:
        rss = 0.0
    s2 = rss / df if df > 0 else float("nan")
    se_int = math.sqrt(s2 * (1.0 / k + xm * xm / sxx))
    se_slope = math.sqrt(s2 / sxx)
    if perfect and abs(intercept) <= 1e-10 * (abs(ym) + abs(slope * xm) + 1e-300):
        intercept = 0.0
    t = ratio_stat(intercept, se_int)
    if math.isnan(t):
        warn.append("Egger-teszt: nulla reziduális variancia (azonos hatásméretek / tökéletes "
                    "illeszkedés) — a teszt nem értelmezhető.")
    p = dist.t_two_sided_p(t, df)
    crit = dist.t_ppf(0.5 + level / 2.0, df) if ci_dist == "t" else dist.norm_ppf(0.5 + level / 2.0)
    if k < 10:
        warn.append("k = %d < 10: az Egger-teszt ereje kicsi; ne értelmezd önmagában "
                    "(Sterne et al. 2011)." % k)
    return MetaResult(kind="egger", k=k, intercept=intercept, se_intercept=se_int, t=t, df=df, p=p,
                      ci_lower=intercept - crit * se_int, ci_upper=intercept + crit * se_int,
                      ci_dist=ci_dist, ci_crit=crit, level=level,
                      slope=slope, se_slope=se_slope, warnings=warn)


_KENDALL_EXACT_MAX_N = 170   # e fölött az R pKendall túlcsordul (metafor: NaN) → normális közelítés


@functools.lru_cache(maxsize=8)
def _kendall_inv_dist(n):
    """Az inverziószám eloszlása n elem véletlen permutációjánál (valószínűségek; túlcsordulás-
    mentes, O(n³) csúszóablakos DP)."""
    probs = [1.0]
    for m in range(2, n + 1):
        size = len(probs) + m - 1
        new = [0.0] * size
        acc = 0.0
        for i in range(size):
            if i < len(probs):
                acc += probs[i]
            if i - m >= 0:
                acc -= probs[i - m]
            new[i] = acc / m
        probs = new
    return tuple(probs)


def _kendall_exact_sf(s_abs, n):
    """P(|S| >= s_abs) pontosan, kötések nélkül (R cor.test(..., exact=TRUE) pKendall)."""
    maxinv = n * (n - 1) // 2
    probs = _kendall_inv_dist(n)
    # S = (konkordáns - diszkordáns) = maxinv - 2*inv
    p = sum(pr for inv, pr in enumerate(probs) if abs(maxinv - 2 * inv) >= s_abs - 1e-9)
    return min(1.0, max(0.0, p))


def _kendall_var_s(x, y):
    """S varianciája H0 alatt, kötésekkel (R cor.test kendall, exact=FALSE ág)."""
    from collections import Counter
    n = len(x)
    tx = [c for c in Counter(x).values() if c > 1]
    ty = [c for c in Counter(y).values() if c > 1]
    v0 = n * (n - 1) * (2 * n + 5)
    vt = sum(c * (c - 1) * (2 * c + 5) for c in tx)
    vu = sum(c * (c - 1) * (2 * c + 5) for c in ty)
    v1 = sum(c * (c - 1) for c in tx) * sum(c * (c - 1) for c in ty)
    v2 = sum(c * (c - 1) * (c - 2) for c in tx) * sum(c * (c - 1) * (c - 2) for c in ty)
    return ((v0 - vt - vu) / 18.0 + v1 / (2.0 * n * (n - 1))
            + (v2 / (9.0 * n * (n - 1) * (n - 2)) if n > 2 else 0.0))


def begg_test(yi, vi, method="auto", continuity=False):
    """Begg–Mazumdar rangkorreláció: Kendall-τ a standardizált hatások (t*_i) és a v_i között.

    method: 'auto' (alapértelmezés = metafor ranktest: pontos Kendall-eloszlás, ha nincs kötés és
    k <= 170, különben normális közelítés a kötés-korrigált var(S)-sel); 'exact' (pontos eloszlás;
    kötés vagy k > 170 esetén figyelmeztetéssel normális közelítésre vált); 'normal' (mindig
    z = S/sd(S) — Stata metabias, metafor ranktest(exact=FALSE)).
    continuity: csak a normális közelítésnél; True esetén z = (|S| − 1)/sd(S) (Stata metabias
    'continuity corrected' sora). Az eredmény z és sd_S mezője a normális közelítés értéke
    (pontos módszernél is tájékoztatásul)."""
    if method not in BEGG_METHODS:
        raise ValueError("begg_test: method 'auto', 'exact' vagy 'normal' lehet (kapott: %r)" % (method,))
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
    # metafor ranktest: cor.test(..., method="kendall", exact=TRUE) → kötések nélkül pontos
    # eloszlás bármely k-ra (k > 170-nél az R is NaN-t ad, ott normális közelítés);
    # kötésekkel normális közelítés a teljes, kötés-korrigált varianciával, folytonossági
    # korrekció nélkül.
    warn = []
    exact_ok = not has_ties and k <= _KENDALL_EXACT_MAX_N
    if method == "exact" and not exact_ok:
        warn.append("Begg-teszt: a pontos Kendall-eloszlás nem használható (%s); normális közelítést "
                    "alkalmaztam." % ("kötések vannak az adatokban" if has_ties else "k > %d" % _KENDALL_EXACT_MAX_N))
    use_exact = exact_ok and method in ("auto", "exact")
    var_s = _kendall_var_s(tstar, vi)
    sd_s = math.sqrt(var_s) if var_s > 0 else 0.0
    if continuity:
        z = math.copysign(max(0.0, abs(s) - 1.0), s) / sd_s if sd_s > 0 else 0.0
    else:
        z = s / sd_s if sd_s > 0 else 0.0
    if use_exact:
        p = _kendall_exact_sf(abs(s), k)
        method_used = "pontos"
        if continuity:
            warn.append("Begg-teszt: a folytonossági korrekció csak a normális közelítésre vonatkozik; "
                        "a pontos p-értéket nem módosítja.")
    else:
        p = dist.z_two_sided_p(z)
        method_used = "normális közelítés" + (" (folytonossági korrekcióval)" if continuity else "")
    if k < 10:
        warn.append("k < 10: a Begg-teszt ereje nagyon kicsi.")
    return MetaResult(kind="begg", k=k, kendall_tau=tau, S=s, p=min(1.0, p), method=method_used,
                      method_requested=method, continuity=bool(continuity), z=z, sd_S=sd_s,
                      warnings=warn)


def _tie_pairs(vals):
    from collections import Counter
    return sum(c * (c - 1) / 2.0 for c in Counter(vals).values() if c > 1)


def _rank_first(values):
    """R rank(ties.method='first') megfelelője (1-től)."""
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    ranks = [0] * len(values)
    for r, i in enumerate(order, start=1):
        ranks[i] = r
    return ranks


_TRIM_MODELS = {"fixed": "fixed", "fe": "fixed", "common": "fixed", "ce": "fixed",
                "random": "random", "re": "random"}


def trim_and_fill(yi, vi, labels=None, model="random", tau2_method=None, estimator="L0",
                  side=None, ci_method="z", level=0.95, maxiter=100, trim_model=None):
    """Duval–Tweedie trim-and-fill (a metafor::trimfill / meta::trimfill logikája szerint).

    model / tau2_method / ci_method / level: a KORRIGÁLT (kitöltött) adatokra illesztett,
    jelentett modell (tau2_method None → a modell alapértelmezése).
    trim_model: a vágási (L0/R0) iterációkban használt modell.
      None     — ugyanaz, mint a jelentett modell (metafor::trimfill egy rma-illesztésen);
      'fixed'  — a vágás a közös (fix) hatású átlaggal történik, a kitöltött adatokat pedig a
                 jelentett modellel összesítjük (meta::trimfill alapértelmezése, ma.common = TRUE:
                 'common-random' eljárás, Duval & Tweedie 2000 ajánlása szerint jobban viselkedik);
      'random' — véletlen hatású átlag a megadott τ²-becslővel (meta: ma.common = FALSE).
    side: 'left' / 'right' / None (automatikus). Automatikus választásnál a vágási modell
    y_i ~ √v_i meta-regressziójának meredeksége dönt (pozitív → a hiányzó vizsgálatok bal
    oldalon): trim_model=None esetén a jelentett modell τ²-becslőjével (metafor), trim_model='fixed'
    esetén 1/v_i súlyokkal — ez a meredekség pontosan a klasszikus Egger-tengelymetszet, vagyis a
    meta::trimfill szabálya (left = Egger-tengelymetszet > 0). A meta a vágási modelltől
    függetlenül mindig az Egger-előjelet használja; trim_model='random' mellett a pontos egyezéshez
    add meg kifejezetten a side-ot.

    A korrigált (kitöltött) modell a megadott ci_method-dal és level-lel illeszkedik (a
    pipeline az elsődleges modellét adja át). k0 = 0 esetén ez pontosan az elsődleges
    eredmény (mint a metaforban). Eltérés a metafor 4.4-től k0 > 0 esetén: a metafor a
    kitöltött adatokat test='z'-vel és 95%-os szinttel illeszti újra (a test/level nem
    öröklődik); ugyanezt itt ci_method='z', level=0.95 adja.
    """
    k = len(yi)
    if k < 3:
        raise ModelError("trim-and-fill: k >= 3 szükséges")
    if trim_model is not None:
        tkey = str(trim_model).lower()
        if tkey not in _TRIM_MODELS:
            raise ModelError("trim-and-fill: trim_model None, 'fixed' vagy 'random' lehet (kapott: %r)"
                             % (trim_model,))
        trim_m = _TRIM_MODELS[tkey]
    else:
        trim_m = model
    labels = list(labels) if labels else ["#%d" % (i + 1) for i in range(k)]
    # metafor::trimfill: az oldal-regresszió (y ~ √v) a modell saját τ²-becslőjével fut
    # (method = x$method), PM/HE/SJ esetén is; itt a VÁGÁSI modellé
    tm = (tau2_method or "REML").upper()
    if trim_m in ("fixed", "fe", "common", "ce"):
        mr_method = "FE"
    elif trim_m in ("random", "re") and tm in MR_TAU2_METHODS:
        mr_method = tm
    else:
        mr_method = "REML"
    side_rule = None
    if side is None:
        x = [[1.0, math.sqrt(v)] for v in vi]
        mr = meta_regression(yi, vi, x, ["intercept", "sei"], mr_method)
        side = "right" if mr.coefficients[1]["estimate"] < 0 else "left"
        side_rule = ("Egger-tengelymetszet előjele (y ~ √v, 1/v súlyokkal; meta::trimfill)" if mr_method == "FE"
                     else "y ~ √v meta-regresszió meredekségének előjele (%s; metafor::trimfill)" % mr_method)
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
        fit = meta_analysis(ys[:k - k0], vs[:k - k0], trim_m, tau2_method, "z", level)
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
    return MetaResult(kind="trimfill", side=side, side_rule=side_rule, estimator=estimator, k0=k0,
                      se_k0=se_k0, trim_model=trim_m, trim_estimate=sign * beta,
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


# ------------------------------------------------- Doi-plot / LFK-index
def doi_plot_data(yi, vi):
    """Doi-plot pontjai és az LFK-index (Furuya-Kanamori, Barendregt & Doi 2018;
    a metasens::lfkindex algoritmusa szerint; Khan 2020, 12. fejezet).

    Lépések: rendezés hatás szerint; N_j = 100·max(v)/v_j; középrangok
    MR_1 = N_1/2, MR_j = MR_{j-1} + (N_{j-1} + N_j)/2; pct_j = (MR_j - 0,5)/ΣN;
    Z_j = Φ⁻¹(pct_j); a csúcs (m) a legkisebb |Z|-jű vizsgálat;
    LFK = 5/(2k)·Σ_j [Z_j + (max Z - min Z)/(max(θ-θ_m) - min(θ-θ_m))·(θ_j - θ_m)].
    Értelmezés: |LFK| ≤ 1 szimmetrikus, 1–2 kisebb, > 2 jelentős aszimmetria.
    """
    k = len(yi)
    if k < 3:
        raise ModelError("LFK: k >= 3 szükséges")
    order = sorted(range(k), key=lambda i: (yi[i], i))
    y = [yi[i] for i in order]
    v = [vi[i] for i in order]
    vmax = max(v)
    n = [100.0 * vmax / x for x in v]
    mr = [n[0] / 2.0]
    for j in range(1, k):
        mr.append(mr[-1] + (n[j - 1] + n[j]) / 2.0)
    tot = sum(n)
    z = [dist.norm_ppf((m - 0.5) / tot) for m in mr]
    m_idx = min(range(k), key=lambda j: (abs(z[j]), j))
    d = [a - y[m_idx] for a in y]
    rng_d = max(d) - min(d)
    if rng_d <= 0:
        raise ModelError("LFK: a hatásméretek azonosak")
    slope = (max(z) - min(z)) / rng_d
    lfk = 5.0 / (2.0 * k) * sum(zj + slope * dj for zj, dj in zip(z, d))
    a = abs(lfk)
    category = "nincs aszimmetria" if a <= 1 else ("kisebb aszimmetria" if a <= 2 else "jelentős aszimmetria")
    return MetaResult(kind="lfk", k=k, lfk=lfk, category=category,
                      points=[{"study_index": order[j], "y": y[j], "abs_z": abs(z[j])} for j in range(k)],
                      warnings=["Az LFK-index a Doi-csoport módszere; a küszöbök heurisztikusak, és a Cochrane "
                                "Handbook nem ajánlja önálló tesztként — a funnel-plot és az Egger-teszt mellett, "
                                "érzékenységi jelleggel értelmezd."])


def harbord_test(e1, n1, e2, n2):
    """Harbord-teszt (bináris kimenet, log OR): Z/√V regressziója √V-re, a tengelymetszet
    t(k−2)-tesztje (Harbord et al. 2006; Cochrane Handbook 13.3.5.4). Z = a − (a+c)n1/N,
    V = n1·n2·(a+c)(b+d) / (N²(N−1))."""
    xs, ys = [], []
    for a, m1, c, m2 in zip(e1, n1, e2, n2):
        a, c, m1, m2 = float(a), float(c), float(m1), float(m2)
        n = m1 + m2
        ev = a + c
        nev = n - ev
        if n <= 1 or ev == 0 or nev == 0:
            continue
        zs = a - ev * m1 / n
        v = m1 * m2 * ev * nev / (n * n * (n - 1))
        xs.append(math.sqrt(v))
        ys.append(zs / math.sqrt(v))
    return _ols_intercept_test(xs, ys, "harbord")


def peters_test(e1, n1, e2, n2, cc=0.5):
    """Peters-teszt (bináris kimenet): ln OR súlyozott regressziója 1/N-re, súly
    1/(1/(a+c) + 1/(b+d)) a korrekció nélküli összegekből; a meredekség t(k−2)-tesztje
    (Peters et al. 2006; Stata metabias). Nulla cellánál az ln OR-hez +cc kerül."""
    xs, ys, ws = [], [], []
    for a, m1, c, m2 in zip(e1, n1, e2, n2):
        a, c, m1, m2 = float(a), float(c), float(m1), float(m2)
        b, d = m1 - a, m2 - c
        if (a == 0 and c == 0) or (b == 0 and d == 0):
            continue
        # a súly a korrekció nélküli esemény/nem-esemény összegekből (Stata metabias)
        ws.append(1.0 / (1.0 / (a + c) + 1.0 / (b + d)))
        if min(a, b, c, d) == 0:
            a, b, c, d = a + cc, b + cc, c + cc, d + cc
        ys.append(math.log(a * d / (b * c)))
        xs.append(1.0 / (m1 + m2))
    k = len(xs)
    if k < 3:
        raise ModelError("Peters-teszt: k >= 3 szükséges")
    sw = sum(ws)
    xm = sum(w * x for w, x in zip(ws, xs)) / sw
    ym = sum(w * y for w, y in zip(ws, ys)) / sw
    sxx = sum(w * (x - xm) ** 2 for w, x in zip(ws, xs))
    slope = sum(w * (x - xm) * (y - ym) for w, x, y in zip(ws, xs, ys)) / sxx
    inter = ym - slope * xm
    rss = sum(w * (y - inter - slope * x) ** 2 for w, x, y in zip(ws, xs, ys))
    df = k - 2
    se = math.sqrt(rss / df / sxx)
    t = slope / se
    return MetaResult(kind="peters", k=k, slope=slope, se_slope=se, t=t, df=df,
                      p=dist.t_two_sided_p(t, df), warnings=[] if k >= 10 else
                      ["k < 10: a teszt ereje kicsi (Sterne et al. 2011)."])


def _ols_intercept_test(xs, ys, kind):
    k = len(xs)
    if k < 3:
        raise ModelError("%s: k >= 3 szükséges" % kind)
    xm = sum(xs) / k
    ym = sum(ys) / k
    sxx = sum((x - xm) ** 2 for x in xs)
    slope = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / sxx
    inter = ym - slope * xm
    df = k - 2
    s2 = sum((y - inter - slope * x) ** 2 for x, y in zip(xs, ys)) / df
    se = math.sqrt(s2 * (1.0 / k + xm * xm / sxx))
    t = inter / se
    return MetaResult(kind=kind, k=k, intercept=inter, se_intercept=se, t=t, df=df,
                      p=dist.t_two_sided_p(t, df), slope=slope,
                      warnings=[] if k >= 10 else ["k < 10: a teszt ereje kicsi (Sterne et al. 2011)."])
