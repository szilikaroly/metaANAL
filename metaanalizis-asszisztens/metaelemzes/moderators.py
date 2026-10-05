# -*- coding: utf-8 -*-
"""Alcsoport-elemzés és (vegyes hatású) meta-regresszió.

Meta-regresszió: y = Xβ + u + e, u ~ N(0, τ²), e ~ N(0, v_i).
τ²-becslők: REML, ML (Fisher-scoring + τ² = 0 határ és profil-likelihood rács ellenőrzése),
DL, HE, SJ, PM (a metafor rma.uni képletei moderátorokkal), FE (τ² = 0).
Tesztek: z (Wald) vagy Knapp–Hartung (t / F); kiegészítésként HC1 robusztus (szendvics) SE-k
(Stata regress [aw=1/v], robust; metafor robust(..., adjust=TRUE)).
Alcsoportok: csoportonkénti összesítés, Q_between, a csoport részesedése a teljes súlyból.
Források: Borenstein et al. 2009 (19–20. fejezet); Khan 2020 (11. fejezet);
Viechtbauer 2010 (metafor rma.uni); Knapp & Hartung 2003.
"""
import math

from . import distributions as dist
from . import linalg as la
from .models import (ModelError, MetaResult, meta_analysis, estimate_tau2, optimize_tau2,
                     variance_scale, tau2_info_warnings, ratio_stat, polish_tau2)


# ------------------------------------------------------------- alcsoportok
def subgroup_analysis(yi, vi, groups, labels=None, model="random", tau2_method=None,
                      ci_method=None, level=0.95, common_tau2=False, pi_method="t_k-2",
                      h_centre="truncated"):
    """Alcsoportonkénti összesítés + alcsoport-különbség teszt (Q_between).

    common_tau2=True: közös τ² a csoportokon belül (vegyes hatású modell faktor
    moderátorral, a meta-regresszió reziduális τ²-ével — a metafor rma(yi, vi, mods=~g));
    ekkor az egyvizsgálatos csoport varianciája is v_i + τ²_közös. Ha a közös τ² nem
    becsülhető (k <= csoportok száma, szinguláris modell), figyelmeztetéssel csoportonként
    külön τ²-re vált. Különben csoportonként külön τ² (Borenstein 19. fejezet mindkét
    változatot tárgyalja); az egyvizsgálatos csoport ekkor fix hatású (RevMan-gyakorlat).
    IVhet modellnél is használható: a közös τ² ekkor alapértelmezésben a faktor-modell DL
    becslése (mint az IVhet saját τ²-e), és a csoportok IVhet-varianciája ezzel számol.
    Fix hatású modellnél a common_tau2 nem értelmezett (figyelmeztetés).

    Q_between = Σ (μ_g - μ̄)² / se_g², df = G - 1, ahol se_g a csoportbecslés Wald-féle
    (NEM HKSJ-korrigált) standard hibája, √(1/Σw). A χ²(G-1) teszt így független a
    megjelenített CI-módszertől (--ci): ez a Borenstein 19. fejezet / RevMan 5 tesztje és a
    metafor rma(est, sei, mods=~g, method="FE") a különálló τ²-es csoportbecslésekre; közös τ²
    esetén pontosan a metafor rma(yi, vi, mods=~g) QM-je (Wald).
    IVhet modellnél se_g a csoportbecslés IVhet-standardhibája (√Σ(w_i/Σw)²(v_i + τ²_DL), a
    megjelenített z-CI alapja), NEM a √(1/Σw) inverz-variancia SE (az a csoporton belüli
    heterogenitást figyelmen kívül hagyná, és túl liberális tesztet adna).

    tau2_method: None = a modell alapértelmezése (random → REML, ivhet → DL).
    h_centre: a Higgins–Thompson H/I² CI középpontja minden (csoport- és teljes) modellben
    (lásd models.heterogeneity), hogy a teljes modell blokkja egyezzen az elsődleges modellével.
    Minden csoport-eredmény kap egy weight_share_pct mezőt: a csoport vizsgálatainak összsúlya
    a TELJES (alcsoportok nélküli) modellben, a teljes súly %-ában (MetaXL / RevMan
    'Subtotal % weight'; véletlen hatásnál a teljes adatsor τ²-ével számolt 1/(v_i + τ²) súlyok,
    IVhet-nél és fix hatásnál 1/v_i). Ugyanez kerül a weight_share_raw mezőbe nyers súlyösszegként.
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
    method = None
    if common_tau2 and model not in ("random", "ivhet"):
        warnings.append("A közös τ² (--common-tau2) csak véletlen hatású és IVhet modellnél értelmezett; "
                        "a(z) %s modellnél figyelmen kívül hagytam." % model)
    elif common_tau2:
        if len(yi) <= len(order):
            warnings.append("Közös τ² nem becsülhető (k = %d <= alcsoportok száma = %d); "
                            "csoportonként külön τ²-t használtam." % (len(yi), len(order)))
        else:
            x = [[1.0] + [1.0 if g == lev else 0.0 for lev in order[1:]] for g in groups]
            method = (tau2_method or ("DL" if model == "ivhet" else "REML")).upper()
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
                r = meta_analysis(y, v, "ivhet" if model == "ivhet" else "random", tau2_method, "z",
                                  level, pi_method, labs, tau2_fixed=tau2_common, h_centre=h_centre)
                r.warnings = []
            else:
                r = meta_analysis(y, v, "fixed", ci_method="z", level=level, labels=labs, h_centre=h_centre)
            r.note = "egyetlen vizsgálat — nincs összesítés"
        else:
            r = meta_analysis(y, v, model, tau2_method, ci_method, level, pi_method, labs,
                              tau2_fixed=tau2_common, h_centre=h_centre)
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
    overall = meta_analysis(yi, vi, model, tau2_method, ci_method, level, pi_method, labels,
                            h_centre=h_centre)
    # alcsoport-részesedés a teljes modell súlyából (MetaXL/RevMan 'Subtotal' % weight)
    w_all = overall.weights_raw
    for g, r in zip(order, out_groups):
        idx = [i for i, gg in enumerate(groups) if gg == g]
        r.weight_share_raw = sum(w_all[i] for i in idx)
        r.weight_share_pct = 100.0 * r.weight_share_raw / overall.sum_weights
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
                         "heterogenitással inflált), χ²(G−1) eloszlással"
                         + ("; közös τ² (%s, faktor-modellből) az alcsoportokban" % method
                            if tau2_common is not None else "")) if model == "ivhet" else
                        "Q_between a csoportbecslések Wald-standardhibáiból (nem HKSJ), "
                        "χ²(G−1) eloszlással"
                        + ("; közös τ² mellett = metafor rma(mods=~alcsoport) QM" if tau2_common is not None
                           else "")),
        common_tau2=tau2_common, common_tau2_info=tau2_common_info, warnings=warnings,
    )


# ------------------------------------------------------------ meta-regresszió
MR_TAU2_METHODS = ("REML", "ML", "DL", "PM", "HE", "SJ", "FE")


def _wls_fit(x, y, w):
    """Súlyozott LS a W^½X Householder-QR-jével (linalg.WeightedQR): (b, M = (XᵀWX)⁻¹, e, fit).
    A normálegyenletek explicit inverzénél pontosabb rosszul kondicionált (közel-kollineáris
    moderátorok, nagyon eltérő v_i) terveknél; a fit a nyomokat és a log det-et is adja."""
    fit = la.WeightedQR(x, w)
    b, e, _ = fit.solve(y)
    return b, fit.cov(), e, fit


def _wls(x, y, w):
    b, m, e, _ = _wls_fit(x, y, w)
    return b, m, e


def _traces(x, w, m=None, fit=None):
    """tr(P), tr(PP) a P = W - WX M XᵀW projekcióhoz, O(k p²), kiejtés nélkül: a naiv
    Σw - tr(M XᵀW²X) alak közel-kollineáris tervnél vagy domináns súlynál jegyeket veszít
    (akár negatív tr(PP)); itt a QR-ből tr(P) = Σ w_i (1 - h_ii) (linalg.WeightedQR.traces)."""
    if fit is None:
        fit = la.WeightedQR(x, w)
    return fit.traces()


def _qe(x, y, v, tau2):
    """Általánosított Q_E(τ²) = Σ w_i e_i² (WLS-reziduumok, w = 1/(v + τ²))."""
    w = [1.0 / (vi + tau2) for vi in v]
    _, _, e = _wls(x, y, w)
    return sum(wi * ei * ei for wi, ei in zip(w, e))


def mr_loglik(x, y, v, tau2, reml=True):
    """(RE)ML log-likelihood (konstansok nélkül) a vegyes hatású meta-regresszióhoz:
    ML = -½[Σ log(v_i+τ²) + Σ w_i e_i²]; REML = ML - ½ log det(XᵀWX)."""
    w = [1.0 / (vi + tau2) for vi in v]
    b, m, e, fit = _wls_fit(x, y, w)
    ll = -0.5 * (sum(math.log(vi + tau2) for vi in v) + sum(wi * ei * ei for wi, ei in zip(w, e)))
    if reml:
        ll -= 0.5 * fit.logdet()
    return ll


def _tau2_he_mr(x, y, v):
    """Hedges (HE) momentum-becslő moderátorokkal (metafor): (RSS_OLS - tr(P_OLS V)) / (k - p)."""
    k, p = len(y), len(x[0])
    ones = [1.0] * k
    b, m, e, fit = _wls_fit(x, y, ones)
    rss = sum(ei * ei for ei in e)
    _, one_minus_h = fit.leverages()
    tr_pv = sum(vi * mh for vi, mh in zip(v, one_minus_h))
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


def _fs_mr_adj(x, y, v, method, tau2):
    """A REML/ML Fisher-scoring lépés moderátorokkal τ²-nél, vagy None (nem véges / nem pozitív
    információ)."""
    w = [1.0 / (vi + tau2) for vi in v]
    b, m, e, fit = _wls_fit(x, y, w)
    r2w2 = sum((wi * ei) ** 2 for wi, ei in zip(w, e))
    if method == "REML":
        tr_p, tr_pp = fit.traces()
        if not (tr_pp > 0) or not math.isfinite(tr_pp):
            return None
        adj = (r2w2 - tr_p) / tr_pp
    else:  # ML
        adj = (r2w2 - sum(w)) / sum(a * a for a in w)
    return adj if math.isfinite(adj) else None


def _fs_mr(x, y, v, method, start, scale, tol=1e-10, maxiter=1000, step=1.0):
    """REML/ML Fisher-scoring moderátorokkal (metafor rma.uni), lépésfelezéssel; step < 1:
    csillapított lépések (metafor stepadj). Konvergencia után szelő-lépéses finomítás
    (models.polish_tau2: a lineáris konvergencia miatti ~1e-7 relatív maradékhiba ellen)."""
    tau2 = max(0.0, start)
    it = -1
    for it in range(maxiter):
        raw = _fs_mr_adj(x, y, v, method, tau2)
        if raw is None:
            return tau2, False, it + 1
        adj = raw * step
        while tau2 + adj < 0:
            adj /= 2.0
            if abs(adj) < 1e-300:
                adj = -tau2
                break
        t_old = tau2
        tau2 += adj
        if abs(adj) <= tol * max(tau2, scale):
            if tau2 > 0 and adj != 0:
                tau2 = polish_tau2(lambda t: _fs_mr_adj(x, y, v, method, t), t_old, raw, tau2, scale)
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
        b0, m0, e0, fit0 = _wls_fit(x, y, w0)
        qe = sum(wi * ei * ei for wi, ei in zip(w0, e0))
        tr_p0, _ = fit0.traces()
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
                         lambda s, step=1.0: _fs_mr(x, y, v, method, s, scale, step=step),
                         _tau2_he_mr(x, y, v), scale, hi)


def _robust_hc1(x, y, w, b, m, e, names, level, idx, has_int):
    """HC1 (Huber–White, k/(k−p) korrekció) szendvics-kovariancia a súlyozott LS-illesztéshez.

    V = k/(k−p) · M (Σ w_i² e_i² x_i x_iᵀ) M, M = (XᵀWX)⁻¹; t(k−p) próbák és CI-k, robusztus
    Wald F(q, k−p) a tengelymetszeten kívüli együtthatókra. Fix hatású súlyokkal (w = 1/v_i) ez a
    Stata 'regress y x [aweight = 1/v], vce(robust)' (Khan 2020 11. fejezet), véletlen hatású
    súlyokkal (w = 1/(v_i + τ²)) a metafor robust(fit, cluster = 1:k, adjust = TRUE).
    R² (súlyozott, centrált; csak tengelymetszetes modellben), korrigált R², root MSE: a Stata
    aweight-konvenciója (a súlyok k-ra normálva: root MSE = √(k/Σw · Σ w e² / (k − p)))."""
    k, p = len(y), len(x[0])
    df = k - p
    meat = la.xtwx(x, [wi * wi * ei * ei for wi, ei in zip(w, e)])
    vb = la.matmul(la.matmul(m, meat), m)
    adj = k / float(df)
    vb = [[adj * c for c in row] for row in vb]
    crit = dist.t_ppf(0.5 + level / 2.0, df)
    coefs = []
    for j in range(p):
        se = math.sqrt(max(vb[j][j], 0.0))
        stat = ratio_stat(b[j], se)
        try:
            se_expb = math.exp(b[j]) * se
        except OverflowError:
            se_expb = math.inf
        coefs.append({"name": names[j], "estimate": b[j], "se": se, "se_expb": se_expb, "t": stat,
                      "p": dist.t_two_sided_p(stat, df),
                      "ci_lower": b[j] - crit * se, "ci_upper": b[j] + crit * se})
    f = f_p = None
    warn = []
    if idx:
        bsub = [b[i] for i in idx]
        vsub = [[vb[i][j] for j in idx] for i in idx]
        try:
            vinv = la.inverse(vsub)
        except la.SingularMatrixError:
            # pl. egyvizsgálatos moderátor-szint: a reziduuma 0, így a robusztus blokk szinguláris
            # (metafor robust(): 'Could not obtain the cluster-robust omnibus Wald test', QM = NA)
            vinv = None
            warn.append("A robusztus (HC1) omnibusz F-próba nem számolható: a robusztus kovariancia-blokk "
                        "szinguláris (pl. egyetlen vizsgálatot tartalmazó moderátor-szint); a modell-alapú "
                        "moderátor-teszt (QM) érvényes.")
        if vinv is not None:
            q = len(idx)
            f = sum(bsub[i] * sum(vinv[i][j] * bsub[j] for j in range(q)) for i in range(q)) / q
            f_p = dist.f_sf(f, q, df)
    # a tökéletesen illesztett (hat = 1) vizsgálat reziduuma 0, így a HC1 'hús' kihagyja: az ehhez
    # kötött együttható robusztus SE-je alulbecsült (a vizsgálat saját varianciáját sem tartalmazza)
    hat = [wi * sum(row[i] * sum(m[i][j] * row[j] for j in range(p)) for i in range(p))
           for row, wi in zip(x, w)]
    n_lev1 = sum(1 for h in hat if h > 1.0 - 1e-8)
    if n_lev1:
        warn.append("Robusztus (HC1) SE: %d vizsgálatot a modell tökéletesen illeszt (hat = 1, pl. egyvizsgálatos "
                    "moderátor-szint); a hozzájuk tartozó együtthatók robusztus SE-je alulbecsült, ezeknél a "
                    "modell-alapú SE az irányadó." % n_lev1)
    sw = sum(w)
    sse = sum(wi * ei * ei for wi, ei in zip(w, e))
    r2 = r2_adj = None
    if has_int:
        ybar = sum(wi * yi for wi, yi in zip(w, y)) / sw
        sst = sum(wi * (yi - ybar) ** 2 for wi, yi in zip(w, y))
        if sst > 0:
            r2 = max(0.0, 1.0 - sse / sst)
            r2_adj = 1.0 - (1.0 - r2) * (k - 1) / float(df)
    root_mse = math.sqrt(k / sw * sse / df)
    return {
        "type": "HC1", "df": df, "coefficients": coefs, "vcov": vb,
        "F": f, "F_df1": len(idx) if idx else 0, "F_df2": df, "F_p": f_p,
        "r2": r2, "r2_adj": r2_adj, "root_mse": root_mse, "sum_w": sw,
        # Khan (2020, 248. o.) nem standard 'I²_model' = (F − df_r)/F a robusztus F-ből (0-nál csonkolva);
        # csak a könyv reprodukálásához — heterogenitási mérőszámként nem ajánlott
        "I2_model_pct": (100.0 * max(0.0, (f - df) / f)) if (f is not None and f > 0) else None,
        "warnings": warn,
        "note": ("HC1 szendvics-SE (k/(k−p) korrekció), t(k−p) próbák; a súlyok a modell súlyai "
                 "(τ² = 0 esetén 1/v_i: Stata regress [aw=1/v], robust)."),
    }


# Tökéletes illeszkedés (minden reziduum csak kerekítési zaj). Két, egymást kiegészítő feltétel:
#  1) relatív: rss <= PERFECT_FIT_RTOL · (a hatások centrált súlyozott négyzetösszege) — vagyis
#     R² = 1 legalább 26 jegyig; eltolás-független (y + c ugyanazt adja);
#  2) ábrázolási padló: rss <= PERFECT_FIT_FLOOR · Σ w y² — a reziduumok nem nagyobbak a bemenő
#     hatásméretek saját kerekítésénél (néhány tucat ulp; pl. y_i = μ + b·x_i kerekítve, nagyon
#     kicsi b-vel, ahol a centrált variáció is alig nagyobb a kerekítésnél).
# Küszöbök a differenciális fuzz alapján (tests/fuzz, 2×3000 adatsor, minden valódi tökéletes
# illeszkedés): pontos (racionális) rss/Σwy² <= 5.1e-33, a motor számolt értéke <= 9.1e-32,
# centráltan <= 7.9e-28; a legkisebb nem tökéletes arány 3.6e-8. A régi, Σwy²-hez mért 1e-20
# küszöb a ~10 jegyig egyező hatásokat (pl. 1000 + 1e-8 különbségek, arány 5.6e-24) tévesen
# tökéletes illeszkedésnek vette.
PERFECT_FIT_RTOL = 1e-26
PERFECT_FIT_FLOOR = 1e-28


def _variation(yc, y, w, centred):
    """(a centrált súlyozott négyzetösszeg, Σ w y²): az előbbi tengelymetszet nélkül Σ w yc²."""
    if centred:
        sw = sum(w)
        m = sum(wi * a for wi, a in zip(w, yc)) / sw
        tss = sum(wi * (a - m) ** 2 for wi, a in zip(w, yc))
    else:
        tss = sum(wi * a * a for wi, a in zip(w, yc))
    return tss, sum(wi * a * a for wi, a in zip(w, y))


def is_perfect_fit(rss, tss, swy2):
    """Tökéletes illeszkedés-e (a reziduális négyzetösszeg csak kerekítési zaj); lásd fent."""
    return rss <= PERFECT_FIT_RTOL * tss or rss <= PERFECT_FIT_FLOOR * swy2


def rounding_rss(tss, swy2):
    """A még kerekítési zajnak tekintett legnagyobb súlyozott reziduális négyzetösszeg (az
    is_perfect_fit két küszöbének összege). Tökéletes illeszkedésnél egy b_j együttható akkor
    'nulla a kerekítésen belül', ha |b_j| <= sqrt(M_jj · rounding_rss), M = (XᵀWX)⁻¹: ekkora
    (kerekítési szintű) reziduum ennyit mozdíthat rajta (Cauchy–Schwarz a W-normában); a közel-
    kollineáris tervek felnagyított zaja így automatikusan benne van (M_jj nagy)."""
    return PERFECT_FIT_RTOL * tss + PERFECT_FIT_FLOOR * swy2


def meta_regression(yi, vi, x, names, tau2_method="REML", test="z", level=0.95, robust=False):
    """Vegyes hatású meta-regresszió.

    x: a modellmátrix sorai (az első oszlop általában a tengelymetszet: 1.0).
    test: 'z' (Wald, χ² omnibusz) vagy 'knha' (Knapp–Hartung: t és F); 'robust_hc1' a
          test='z', robust=True rövidítése.
    robust: True esetén az eredmény 'robust' mezője HC1 szendvics-SE-ket, t(k−p) próbákat és
          CI-ket, robusztus F-et, súlyozott R²-et / korrigált R²-et / root MSE-t és Σw-t tartalmaz
          (_robust_hc1; tau2_method='FE' mellett = Stata 'regress [aw=1/v], robust', Khan 2020
          11. fejezet). A fő ('coefficients') blokk változatlanul a test szerinti modell-alapú SE.
    """
    if test == "robust_hc1":
        test, robust = "z", True
    if test not in ("z", "knha"):
        raise ModelError("meta-regresszió teszt: 'z', 'knha' vagy 'robust_hc1' (kapott: %r)" % (test,))
    k = len(yi)
    p = len(x[0])
    if k <= p:
        raise ModelError("meta-regresszió: k (%d) <= paraméterek száma (%d)" % (k, p))
    method = (tau2_method or "REML").upper()
    if method not in MR_TAU2_METHODS:
        raise ModelError("meta-regresszió τ²-becslő: %s" % ", ".join(MR_TAU2_METHODS))
    has_int = all(row[0] == 1.0 for row in x)
    # Eltolás-független számolás: tengelymetszetes modellben a y_i - y_ref (y_ref = a középső
    # adatérték) eltolt hatásokkal illesztünk; a meredekségek, a reziduumok és a τ² matematikailag
    # változatlanok, csak a tengelymetszethez adódik vissza y_ref. Így a kerekítési hiba a hatások
    # SZÓRÁSÁHOZ mérten kicsi, nem a nagyságukhoz (pl. 1000 + 1e-8-os különbségeknél is pontos).
    y_ref = sorted(yi)[k // 2] if has_int else 0.0
    yc = [y - y_ref for y in yi]
    tau2, tau2_info = _tau2_mr(x, yc, vi, method)
    warnings = tau2_info_warnings(method, tau2_info)
    w = [1.0 / (v + tau2) for v in vi]
    b, m, e = _wls(x, yc, w)
    if has_int:
        b[0] += y_ref
    vb = [row[:] for row in m]
    df_res = k - p
    idx = list(range(1, p)) if has_int else list(range(p))
    perfect = False
    q = 1.0
    if test == "knha":
        rss = sum(wi * ei * ei for wi, ei in zip(w, e))
        # tökéletes illeszkedés (y = Xb, pl. azonos y_i-k): a reziduumok csak kerekítési zaj, így a
        # KH-skála s² = 0 (metafor: se = 0, z = ±Inf, QM = NA) — nem a moderátorok kollinearitása
        tss, swy2 = _variation(yc, yi, w, has_int)
        perfect = is_perfect_fit(rss, tss, swy2)
        if perfect:
            q = 0.0
            noise = rounding_rss(tss, swy2)
            for j in range(p):      # a kerekítési zajból adódó együttható (pl. 1e-17) pontosan 0
                if abs(b[j]) <= math.sqrt(max(m[j][j], 0.0) * noise):
                    b[j] = 0.0
            warnings.append("Knapp–Hartung: a modell tökéletesen illeszkedik (a súlyozott reziduális "
                            "négyzetösszeg csak kerekítési zaj), így a KH-skálázás s² = 0: az SE-k 0-k, a t-próbák "
                            "és az omnibusz F-próba nem értelmezhetők (F = –). Ez nem a moderátorok "
                            "kollinearitása; a z (Wald) teszt használható.")
        else:
            q = rss / df_res
        vb = [[q * c for c in row] for row in vb]
        crit = dist.t_ppf(0.5 + level / 2, df_res)
    else:
        crit = dist.norm_ppf(0.5 + level / 2)
    coefs = []
    for j in range(p):
        se = math.sqrt(max(vb[j][j], 0.0))
        stat = ratio_stat(b[j], se)
        pval = dist.t_two_sided_p(stat, df_res) if test == "knha" else dist.z_two_sided_p(stat)
        coefs.append({"name": names[j], "estimate": b[j], "se": se, "stat": stat, "p": pval,
                      "ci_lower": b[j] - crit * se, "ci_upper": b[j] + crit * se})
    # omnibusz moderátor-teszt (a tengelymetszet nélkül, ha van): QM = b_Sᵀ (M_SS)⁻¹ b_S a Schur-
    # komplementerrel, (M_SS)⁻¹ = X_Sᵀ W (I - H_N) X_S — a moderátor-oszlopok súlyozott reziduumai a
    # többi oszlopra (tengelymetszetnél: súlyozott centrálás); nincs explicit inverz, közel-kollineáris
    # tervnél is pontos
    qm = qm_p = None
    if idx and not perfect:
        nidx = [j for j in range(p) if j not in idx]
        if nidx:
            fit_n = la.WeightedQR([[row[j] for j in nidx] for row in x], w)
            res_s = [fit_n.solve([row[a] for row in x])[1] for a in idx]
        else:
            res_s = [[row[a] for row in x] for a in idx]
        qm = sum(wi * sum(r[i] * b[a] for r, a in zip(res_s, idx)) ** 2 for i, wi in enumerate(w)) / q
        if test == "knha":
            fstat = qm / len(idx)
            qm_p = dist.f_sf(fstat, len(idx), df_res)
            qm = fstat
        else:
            qm_p = dist.chi2_sf(qm, len(idx))
    # reziduális heterogenitás (FE-súlyokkal)
    w0 = [1.0 / v for v in vi]
    b0, m0, e0, fit0 = _wls_fit(x, yc, w0)
    qe = sum(wi * ei * ei for wi, ei in zip(w0, e0))
    tr_p0, _ = fit0.traces()
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
            _tau2_mr([[1.0] for _ in yi], yc, vi, "DL")[0]
        if tau2_0 > 0:
            r2 = max(0.0, 100.0 * (tau2_0 - tau2) / tau2_0)
        else:
            r2 = 0.0
    if k < 10 * max(1, len(idx)):
        warnings.append("Kevés vizsgálat moderátoronként (ökölszabály: >= 10 vizsgálat / "
                        "moderátor; Cochrane Handbook 10.11.4).")
    rob = None
    if robust:
        try:
            # yc: a súlyozott R² eltolás-független, az eltolt hatásokkal pontosabban számolható
            rob = _robust_hc1(x, yc, w, b, m, e, names, level, idx, has_int)
        except (ArithmeticError, ValueError) as exc:
            # a kiegészítő robusztus blokk hibája nem viheti magával a modell-alapú eredményt
            warnings.append("A robusztus (HC1) SE nem számolható (%s); a modell-alapú eredmények érvényesek." % exc)
        else:
            warnings += rob.pop("warnings")
        if rob is not None and k < 20:
            warnings.append("Robusztus (HC1) SE kevés vizsgálatnál (k = %d) alulbecsülheti a "
                            "bizonytalanságot; kis mintás korrekció (pl. CR2, clubSandwich) "
                            "megbízhatóbb." % k)
    # _vcov: a próba szerinti együttható-kovariancia (KH-nál s²-tel skálázva) — a predict() sávjaihoz; az
    # aláhúzásos mező nem kerül a results.json-ba (MetaResult.to_dict)
    return MetaResult(
        kind="meta_regression", tau2=tau2, tau2_method=method, tau2_info=tau2_info, test=test, k=k, p=p,
        coefficients=coefs, QM=qm, QM_df=len(idx), QM_p=qm_p, QM_type="F" if test == "knha" else "chi2",
        QE=qe, QE_df=df_res, QE_p=dist.chi2_sf(qe, df_res), I2_res=i2_res, R2=r2,
        robust=rob, warnings=warnings, level=level, _vcov=vb,
    )


def prediction_crit(mr, level=None):
    """A predict() sávjainak kritikus értéke: t(k − p) a 'knha' próbánál, különben z (level: alapból a
    meta-regresszióé, ennek hiányában 0.95)."""
    lv = mr.get("level") if level is None else level
    lv = 0.95 if lv is None else lv
    if mr.test == "knha":
        return dist.t_ppf(0.5 + lv / 2.0, mr.k - mr.p)
    return dist.norm_ppf(0.5 + lv / 2.0)


def predict(mr, xnew, level=None):
    """Az illesztett meta-regresszió előrejelzése új moderátor-értékeknél — a metafor
    predict(rma(yi, vi, mods = …), newmods = …) megfelelője.

    xnew: a modellmátrix új sorai (tengelymetszetes modellnél az első elem 1.0), pl. [[1.0, x], …].
    Soronként: pred = x0ᵀb; se = √(x0ᵀ V x0), ahol V a próba szerinti együttható-kovariancia (Knapp–Hartung
    esetén s²-tel skálázva); CI = pred ± krit·se; PI (predikciós sáv: ahol egy új vizsgálat valódi hatása
    várható) = pred ± krit·√(se² + τ²). krit: t(k − p) a 'knha' próbánál, különben z (mint a metaforban).
    level: alapból a meta-regresszióé. → [{pred, se, ci_lower, ci_upper, pi_lower, pi_upper}]"""
    vb = mr.get("_vcov")
    if vb is None:
        raise ModelError("a meta-regresszió eredményéből hiányzik az együttható-kovariancia (régi eredmény?)")
    b = [c["estimate"] for c in mr.coefficients]
    p = len(b)
    crit = prediction_crit(mr, level)
    tau2 = mr.tau2 or 0.0
    out = []
    for x0 in xnew:
        if len(x0) != p:
            raise ModelError("predict: az új sor hossza (%d) eltér az együtthatók számától (%d)" % (len(x0), p))
        pred = sum(a * c for a, c in zip(x0, b))
        var = sum(x0[i] * sum(vb[i][j] * x0[j] for j in range(p)) for i in range(p))
        se = math.sqrt(max(var, 0.0))
        pse = math.sqrt(max(var, 0.0) + tau2)
        out.append({"pred": pred, "se": se, "ci_lower": pred - crit * se, "ci_upper": pred + crit * se,
                    "pi_lower": pred - crit * pse, "pi_upper": pred + crit * pse})
    return out


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
