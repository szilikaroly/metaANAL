# -*- coding: utf-8 -*-
"""Összesítő modellek: fix (közös) hatás, véletlen hatás, Mantel–Haenszel, Peto.

τ²-becslők: DL, REML, ML, PM, HE, SJ.  CI: z (Wald), t, HKSJ, HKSJ ad hoc (max(1, q)).
Heterogenitás: Q, I² (Higgins–Thompson), H, H² (Q/df), módosított H² (Stata admetan), τ², τ,
CI-k (Q-profile, Higgins–Thompson a meta:::calcH csonkolt középpontjával, Borenstein τ²-CI).
IVhet: τ² alapértelmezésben DL (Doi et al. 2015), rögzített vagy más becslő is választható.
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
def _check(yi, vi, labels=None):
    if len(yi) != len(vi):
        raise ModelError("yi és vi hossza eltér")
    if len(yi) < 1:
        raise ModelError("legalább 1 vizsgálat kell")
    for v in vi:
        if not (v > 0) or math.isinf(v):
            raise ModelError("minden vi-nek pozitív, véges számnak kell lennie")
    # a REML-nyomok (Σ w_i S₋ᵢ)² tagjai ~ k²·w⁴: ennél kisebb vi-nél (pl. 1e-300) túlcsordulnának
    k, vmin = len(vi), min(vi)
    w = 1.0 / vmin
    if not math.isfinite(k * k * w * w * w * w):
        i = list(vi).index(vmin)
        raise ModelError("a(z) %s vizsgálat varianciája (vi = %g) numerikusan kezelhetetlenül kicsi (a súlyok "
                         "túlcsordulnának); valószínű adatkinyerési hiba — ellenőrizd a vi/SE értékét" % (
                             labels[i] if labels and i < len(labels) else "%d." % (i + 1), vmin))


def ratio_stat(est, se):
    """Teszt-statisztika est/se; se = 0 esetén ±inf (est ≠ 0), illetve NaN a 0/0 esetben
    (a metafor ilyenkor NA-t ad) — sosem 'végtelen' egy pontosan 0 becslésre."""
    if se > 0:
        return est / se
    if est == 0 or math.isnan(est) or math.isnan(se):
        return float("nan")
    return math.copysign(math.inf, est)


def _wmean(yi, w):
    sw = sum(w)
    return sum(a * b for a, b in zip(w, yi)) / sw, sw


def cochran_q(yi, vi):
    w = [1.0 / v for v in vi]
    mu, sw = _wmean(yi, w)
    return sum(wi * (y - mu) ** 2 for wi, y in zip(w, yi))


def typical_within_variance(vi):
    """s̃² = (k-1)Σw / ((Σw)² - Σw²) = (k-1)/C  (Higgins & Thompson 2002; metafor)."""
    k = len(vi)
    if k < 2:
        return None
    c = _dl_c([1.0 / v for v in vi])
    if not c > 0:
        return None
    return (k - 1) / c


def _dl_c(w):
    """C = Σw − Σw²/Σw kiejtéses hiba nélkül (= tr(P) τ² = 0-nál, lásd _reml_traces): egy domináns
    súlynál (pl. elírt, 1e-10 nagyságú SE) a naiv alak 0-ra vagy kerekítési zajra kerekedik."""
    return _reml_traces(w)[0]


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
    c = _dl_c([1.0 / v for v in vi])
    if not c > 0:
        return 0.0
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
    scale = variance_scale(vi)
    lo, hi = 0.0, max(tau2_dl(yi, vi), scale, 1e-4)
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
        # skálához viszonyított tolerancia (mint a _tau2_pm_mr-ben): az eredmény ne függjön a
        # hatásméret mértékegységétől (kis varianciáknál az abszolút 1e-12 durva volna)
        if hi - lo < tol * max(hi, scale):
            break
    return 0.5 * (lo + hi)


def variance_scale(vi):
    """A mintavételi varianciák tipikus nagysága (medián) — a τ²-iterációk relatív
    toleranciáihoz, hogy az eredmény ne függjön a hatásméret mértékegységétől."""
    s = sorted(vi)
    m = s[len(s) // 2]
    return m if m > 0 else 1.0


def _reml_traces(w):
    """tr(P) és tr(PP) a P = W - w wᵀ/Σw projekcióhoz kiejtéses hiba nélkül.

    A naiv Σw - Σw²/Σw alak egy domináns súly (pl. v_i ~ 1e-10) esetén katasztrofálisan
    kiejt; itt S₋ᵢ = Σ_{j≠i} w_j és T₋ᵢ = Σ_{j≠i} w_j² prefix/suffix összegekből:
    tr(P) = Σ w_i S₋ᵢ / Σw,  tr(PP) = Σ [(w_i S₋ᵢ)² + w_i² T₋ᵢ] / (Σw)² (csak pozitív tagok).
    """
    k = len(w)
    w2 = [x * x for x in w]
    pre, pre2 = [0.0] * (k + 1), [0.0] * (k + 1)
    for i in range(k):
        pre[i + 1] = pre[i] + w[i]
        pre2[i + 1] = pre2[i] + w2[i]
    suf, suf2 = [0.0] * (k + 1), [0.0] * (k + 1)
    for i in range(k - 1, -1, -1):
        suf[i] = suf[i + 1] + w[i]
        suf2[i] = suf2[i + 1] + w2[i]
    sw = pre[k]
    tr_p = tr_pp = 0.0
    for i in range(k):
        s_mi = pre[i] + suf[i + 1]
        t_mi = pre2[i] + suf2[i + 1]
        tr_p += w[i] * s_mi
        tr_pp += (w[i] * s_mi) ** 2 + w2[i] * t_mi
    return tr_p / sw, tr_pp / (sw * sw)


def _fs_adj(yi, vi, kind, tau2):
    """A Fisher-scoring lépés (pontszám / várt információ) τ²-nél, vagy None (nem véges vagy nem
    pozitív információ)."""
    w = [1.0 / (v + tau2) for v in vi]
    mu, sw = _wmean(yi, w)
    r2w2 = sum((wi * (y - mu)) ** 2 for wi, y in zip(w, yi))
    if kind == "REML":
        tr_p, tr_pp = _reml_traces(w)
        if not (tr_pp > 0) or not math.isfinite(tr_pp):
            return None
        adj = (r2w2 - tr_p) / tr_pp
    else:  # ML
        sw2 = sum(x * x for x in w)
        adj = (r2w2 - sw) / sw2
    return adj if math.isfinite(adj) else None


def polish_tau2(adj_at, t_prev, a_prev, tau2, scale, tol=1e-14, maxsteps=8):
    """A konvergált Fisher-scoring τ² finomítása szelő-lépésekkel a lépésfüggvény (pontszám/info)
    gyökére.

    Miért: a Fisher-scoring a (RE)ML-nél csak lineárisan konvergál (lépésarány ρ, pl. 0.9 egy
    lapos likelihoodnál), ezért a |Δτ²| <= 1e-10·max(τ², skála) leállás után még kb.
    |Δτ²|·ρ/(1 − ρ) hiba marad — 1e-7 relatív nagyságrend, ami a 0 körüli származtatott
    értékekben (pl. predikciós intervallum alsó határa ≈ 0, R² két közeli τ² arányából) láthatóvá
    válik. A szelő-lépés (a két utolsó pontból; lineáris konvergenciánál ez az Aitken-féle Δ²
    extrapoláció) szuperlineárisan konvergál, így néhány kiértékeléssel a kerekítési szintig jut.
    Biztonsági korlátok: csak csökkenő lépésfüggvénynél (lokális maximum), nemnegatív τ²-re,
    legfeljebb 1e-6·max(τ², skála) elmozdulással, és csak akkor fogadunk el pontot, ha ott a lépés
    abszolút értéke kisebb; különben a Fisher-scoring eredménye marad.
    adj_at: τ² -> lépés vagy None; (t_prev, a_prev): az utolsó előtti kiértékelt pont."""
    f_b = adj_at(tau2)
    if f_b is None:
        return tau2
    t_a, f_a, t_b = t_prev, a_prev, tau2
    best_t, best_f = t_b, f_b
    for _ in range(maxsteps):
        if abs(f_b) <= tol * max(t_b, scale) or t_b == t_a:
            break
        slope = (f_b - f_a) / (t_b - t_a)
        if not (slope < 0) or not math.isfinite(slope):
            break
        t_n = t_b - f_b / slope
        if not math.isfinite(t_n) or t_n < 0 or abs(t_n - tau2) > 1e-6 * max(tau2, scale):
            break
        f_n = adj_at(t_n)
        if f_n is None:
            break
        t_a, f_a, t_b, f_b = t_b, f_b, t_n, f_n
        if abs(f_b) >= abs(best_f):
            break
        best_t, best_f = t_b, f_b
    return best_t


def _fisher_scoring(yi, vi, kind, start, tol=1e-10, maxiter=1000, scale=None, step=1.0):
    """REML/ML Fisher-scoring lépésfelezéssel (Viechtbauer 2005).

    Konvergencia: |Δτ²| <= tol · max(τ², skála), ahol a skála a v_i mediánja (mértékegység-
    független); utána szelő-lépéses finomítás (polish_tau2), mert a lineáris konvergencia miatt
    a leállási feltétel önmagában ~1e-7 relatív hibát hagyhat. Nem véges vagy nem pozitív
    információ esetén converged=False-szal kilép, és a hívó a profil-likelihood kereséssel
    folytatja. step < 1: csillapított lépések (a metafor control = list(stepadj = ...)
    megfelelője)."""
    if scale is None:
        scale = variance_scale(vi)
    tau2 = max(0.0, start)
    converged = False
    it = -1
    for it in range(maxiter):
        raw = _fs_adj(yi, vi, kind, tau2)
        if raw is None:
            break
        adj = raw * step
        while tau2 + adj < 0:
            adj /= 2.0
            if abs(adj) < 1e-300:
                adj = -tau2
                break
        t_old = tau2
        tau2 += adj
        if abs(adj) <= tol * max(tau2, scale):
            converged = True
            if tau2 > 0 and adj != 0:
                tau2 = polish_tau2(lambda t: _fs_adj(yi, vi, kind, t), t_old, raw, tau2, scale)
            break
    return tau2, converged, it + 1


def _safe_ll(ll, t):
    try:
        val = ll(t)
    except (ArithmeticError, ValueError):
        return -math.inf
    return val if math.isfinite(val) else -math.inf


def _golden_max(f, a, b, tol=1e-13, maxiter=300):
    """Arany-metszéses maximumkeresés [a, b]-n; (argmax, max) párt ad."""
    g = (math.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(maxiter):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
        if b - a < tol * max(1.0, abs(a)):
            break
    x = 0.5 * (a + b)
    return x, f(x)


def optimize_tau2(ll, fs, start, scale, hi, ngrid=30):
    """(RE)ML τ²: Fisher-scoring + globális ellenőrzés a profil-likelihoodon.

    ll: τ² -> (RE)ML log-likelihood; fs: (kezdőérték, lépésszorzó=1) -> (τ², converged, iterációk).
    1) Fisher-scoring a kezdőértékből (HE, mint a metafor-ban); ha oszcillál és nem konvergál,
       újra fél lépésekkel (metafor: stepadj = 0.5) — info['step_adj'].
    2) A τ² = 0 határ összehasonlítása: ha ll(0) nagyobb, mint ll(τ²_FS), akkor τ² = 0
       (a metafor 'Fisher scoring algorithm may have gotten stuck at a local maximum.
       Setting tau^2 = 0' esete) — info['boundary_reset'].
    3) Durva rács az u = log(1 + τ²/skála) tengelyen [0, felső korlát]; ha egy rácspont
       láthatóan nagyobb likelihoodot ad, a környezetében arany-metszés + Fisher-scoring
       finomítás (info['global_search']). Ez a nem konvergált Fisher-scoring tartaléka is.
       Ha a legjobb rácspont a τ² = 0 határ, de a határ csak jelölt (a Fisher-scoring nem
       konvergált, vagy ll(0) miatt állítottuk 0-ra), a [0, első rácspont] szakaszon is
       keresünk: a belső maximum az első rácspont alatt is lehet.
    """
    def run_fs(s0):
        t, c, n = fs(s0)
        if c:
            return t, c, n, 1.0
        t2, c2, n2 = fs(s0, 0.5)
        if c2:
            return t2, c2, n + n2, 0.5
        return t, c, n + n2, 1.0

    t_fs, conv, it, step = run_fs(start)
    info = {"converged": conv, "iterations": it}
    if conv and step != 1.0:
        info["step_adj"] = step
    ll0 = _safe_ll(ll, 0.0)
    if conv and math.isfinite(t_fs) and t_fs >= 0:
        ll_fs = _safe_ll(ll, t_fs)
    else:
        t_fs, ll_fs = None, -math.inf

    def tol_for(a):
        return 1e-9 * (1.0 + abs(a)) if math.isfinite(a) else 0.0

    # 1) Fisher-scoring vs. τ² = 0
    if t_fs is not None and not (ll0 > ll_fs + tol_for(ll_fs)):
        best_t, best_ll, source = t_fs, ll_fs, "fs"
    else:
        best_t, best_ll, source = 0.0, ll0, "zero"
    # 2) durva rács a log(1 + τ²/skála) tengelyen
    umax = math.log1p(max(hi, scale) / scale)
    us = [umax * j / ngrid for j in range(ngrid + 1)]
    vals = [ll0] + [_safe_ll(ll, scale * math.expm1(u)) for u in us[1:]]
    j = max(range(len(us)), key=lambda i: vals[i])
    if (j > 0 and vals[j] > best_ll + tol_for(best_ll)) or (j == 0 and source == "zero"):
        a, b = us[max(j - 1, 0)], us[min(j + 1, ngrid)]
        if j == ngrid:   # a rács szélén: tágítsuk a keresést
            b = us[ngrid] + 2.0
        u_best, _ = _golden_max(lambda u: _safe_ll(ll, scale * math.expm1(u)), a, b)
        t_g = scale * math.expm1(u_best)
        ll_g = _safe_ll(ll, t_g)
        t2, conv2, _, _ = run_fs(t_g)     # finomítás Fisher-scoringgal
        if conv2 and math.isfinite(t2) and t2 >= 0:
            ll2 = _safe_ll(ll, t2)
            if ll2 >= ll_g - tol_for(ll_g):
                t_g, ll_g = t2, ll2
        if ll_g > best_ll + tol_for(best_ll):
            best_t, best_ll, source = t_g, ll_g, "grid"
    if best_t < 1e-10 * scale:
        best_t = 0.0
    if t_fs is not None and source != "fs":
        info["tau2_fisher_scoring"] = t_fs
        if source == "zero":
            info["boundary_reset"] = True
        else:
            info["global_search"] = True
    if t_fs is None:
        info["fallback"] = "profil-likelihood rács + arany-metszés"
    return best_t, info


def _tau2_upper(yi, vi):
    """Felső korlát a τ²-keresési rácshoz: e fölött a (RE)ML score biztosan negatív."""
    k = len(yi)
    ybar = sum(yi) / k
    ss = sum((y - ybar) ** 2 for y in yi)
    return 2.0 * ss + 2.0 * max(vi)


def _tau2_uni(yi, vi, kind):
    if len(yi) < 2:
        return 0.0, {"converged": True, "iterations": 0}
    scale = variance_scale(vi)
    ll = (lambda x: reml_loglik(yi, vi, x)) if kind == "REML" else (lambda x: ml_loglik(yi, vi, x))
    fs = lambda s, step=1.0: _fisher_scoring(yi, vi, kind, s, scale=scale, step=step)
    return optimize_tau2(ll, fs, tau2_he(yi, vi), scale, _tau2_upper(yi, vi))


def tau2_reml(yi, vi):
    return _tau2_uni(yi, vi, "REML")


def tau2_ml(yi, vi):
    return _tau2_uni(yi, vi, "ML")


def tau2_info_warnings(method, info):
    """A τ²-becslés info-szótárából felhasználónak szóló figyelmeztetések."""
    out = []
    if not info:
        return out
    if info.get("converged") is False:
        out.append("A(z) %s τ²-becslés Fisher-scoringja nem konvergált; a τ² a profil-likelihood "
                   "rács- és arany-metszéses keresésével (tartalék) adódott." % method)
    if info.get("boundary_reset"):
        out.append("A(z) %s Fisher-scoring lokális maximumon akadt el (τ² = %.4g); a log-likelihood "
                   "τ² = 0-nál nagyobb, ezért τ² = 0 (a metafor is így jár el)."
                   % (method, info.get("tau2_fisher_scoring", float("nan"))))
    if info.get("global_search"):
        out.append("A(z) %s Fisher-scoring nem a globális maximumhoz konvergált (τ² = %.4g); a "
                   "profil-likelihood rácskeresése nagyobb likelihoodú τ²-t talált, ezt használtam "
                   "(a metafor alapbeállítással a Fisher-scoring értékét adná). Ellenőrizd a "
                   "profil-likelihoodot (metafor: profile())." % (method, info.get("tau2_fisher_scoring", float("nan"))))
    return out


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

    scale = variance_scale(vi)

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
            if hi - lo < 1e-12 * max(hi, scale):
                break
        return 0.5 * (lo + hi)

    return solve(crit_hi), solve(crit_lo)


H_CENTRES = ("truncated", "untruncated")
BHHR_SE_FORMS = ("borenstein", "higgins_thompson")


def _check_h_centre(h_centre):
    if h_centre not in H_CENTRES:
        raise ModelError("ismeretlen h_centre: %r (lehetséges: %s)" % (h_centre, ", ".join(H_CENTRES)))


def _ln_h_centre(q, df, h_centre):
    """A Higgins–Thompson H-intervallum középpontja (ln H).

    'truncated' (meta:::calcH; Higgins & Thompson 2002: H < 1 → 1): ln max(1, √(Q/df)) — Q < df
    esetén (Q = 0-t is beleértve) a középpont ln 1 = 0, így az intervallum [1, exp(z·SE)] és nem
    függ Q-tól. 'untruncated' (a motor korábbi viselkedése; Borenstein et al. 2009 16. fejezet
    L/U-képlete betű szerint): ½·ln(Q/df), Q = 0 esetén -inf."""
    if h_centre == "truncated":
        return math.log(max(1.0, math.sqrt(q / df))) if q > 0 else 0.0
    return 0.5 * math.log(q / df) if q > 0 else -math.inf


def i2_ci_higgins_thompson(q, k, level=0.95, h_centre="truncated"):
    """I² és H CI a Higgins–Thompson (2002) teszt-alapú módszerrel (Borenstein 16. fejezet).

    SE(ln H): Q > k esetén ½(ln Q − ln(k−1)) / (√(2Q) − √(2k−3)), különben
    √(1/(2(k−2)) · (1 − 1/(3(k−2)²))) (Higgins & Thompson 2002; = meta:::calcH; ez a
    homogenitás melletti pontos ¼·ψ′(df/2) variancia sorfejtése).
    h_centre: 'truncated' (alapértelmezés; meta::metagen / meta:::calcH konvenció: a középpont
    ln max(1, H)) vagy 'untruncated' (½·ln(Q/df), a korábbi viselkedés). Csak Q < df esetén tér
    el a kettő. A H-határok mindkét esetben 1-nél, az I²-határok 0-nál csonkoltak.
    Visszaad: (I²_alsó, I²_felső, H_alsó, H_felső); k < 3 esetén csupa None.
    """
    _check_h_centre(h_centre)
    if k < 3:
        return None, None, None, None
    df = k - 1
    z = dist.norm_ppf(0.5 + level / 2.0)
    if q > k:
        b = 0.5 * (math.log(q) - math.log(df)) / (math.sqrt(2 * q) - math.sqrt(2 * k - 3))
    else:
        b = math.sqrt(1.0 / (2 * (k - 2)) * (1 - 1.0 / (3 * (k - 2) ** 2)))
    lnh = _ln_h_centre(q, df, h_centre)
    finite = math.isfinite(lnh)
    h_lo = math.exp(lnh - z * b) if finite else 0.0
    h_hi = math.exp(lnh + z * b) if finite else 0.0
    i2_lo = max(0.0, (h_lo ** 2 - 1) / h_lo ** 2) * 100 if h_lo > 0 else 0.0
    i2_hi = max(0.0, (h_hi ** 2 - 1) / h_hi ** 2) * 100 if h_hi > 0 else 0.0
    return i2_lo, i2_hi, max(1.0, h_lo), max(1.0, h_hi)


def tau2_ci_bhhr(q, k, c, level=0.95, h_centre="truncated", se_form="borenstein"):
    """τ² CI Borenstein et al. (2009, 16. fejezet) szerint (a Higgins–Thompson H-intervallumból):
    L, U = exp(ln H ∓ z·B), τ²-határok = df(L² − 1)/C és df(U² − 1)/C, 0-nál csonkolva.

    B: Q > k esetén ½(ln Q − ln df) / (√(2Q) − √(2df − 1)) (16.14). Q <= k esetén
    se_form='borenstein' (alapértelmezés, a korábbi viselkedés): a könyv 16.15 egyenlete
    nyomtatott alakjában, B = √(1 / (2(df−1)(1 − 1/(3(df−1)²)))) — itt az (1 − 1/(3(df−1)²))
    tényező a NEVEZŐBEN áll; se_form='higgins_thompson': B = √(1/(2(df−1)) · (1 − 1/(3(df−1)²)))
    (Higgins & Thompson 2002; = i2_ci_higgins_thompson / meta:::calcH; ez a pontos
    ¼·ψ′(df/2) variancia sorfejtése, k = 3-nál 0.577 vs. a könyvi alak 0.866). k >= 10 esetén a
    különbség < 0,6%.
    h_centre: ugyanaz a középpont-konvenció, mint az I²/H CI-nél (heterogeneity() ugyanazt adja
    át, így a τ², I² és H intervallumok egymásba átszámolhatók). 'untruncated' = a könyv L/U-képlete
    betű szerint (½·ln(Q/df)); 'truncated' (alapértelmezés) = ln max(1, √(Q/df)).
    Csak Q < df esetén tér el: ekkor a csonkolt változat felső határa df(exp(2zB) − 1)/C.
    """
    _check_h_centre(h_centre)
    if se_form not in BHHR_SE_FORMS:
        raise ModelError("ismeretlen se_form: %r (lehetséges: %s)" % (se_form, ", ".join(BHHR_SE_FORMS)))
    if k < 3 or c <= 0:
        return None, None
    df = k - 1
    z = dist.norm_ppf(0.5 + level / 2.0)
    if q > df + 1:
        b = 0.5 * (math.log(q) - math.log(df)) / (math.sqrt(2 * q) - math.sqrt(2 * df - 1))
    elif se_form == "borenstein":
        b = math.sqrt(1.0 / (2 * (df - 1) * (1 - 1.0 / (3 * (df - 1) ** 2))))
    else:
        b = math.sqrt(1.0 / (2 * (df - 1)) * (1 - 1.0 / (3 * (df - 1) ** 2)))
    lnh = _ln_h_centre(q, df, h_centre)
    if not math.isfinite(lnh):
        return 0.0, 0.0
    lo = math.exp(lnh - z * b)
    hi = math.exp(lnh + z * b)
    return max(0.0, df * (lo ** 2 - 1) / c), max(0.0, df * (hi ** 2 - 1) / c)


def heterogeneity(yi, vi, tau2=None, level=0.95, h_centre="truncated"):
    """Heterogenitási statisztikák (a modelltől független blokk).

    H = max(1, √(Q/df)) (meta:::calcH; Higgins & Thompson 2002), H2 = Q/df (metafor EE/FE
    konvenció, nem csonkolt — visszafelé kompatibilis), H2_M = max(0, (Q − df)/df) (Stata
    admetan 'modified H²' / Mittlböck & Heinzl 2006). h_centre: a Higgins–Thompson H/I²
    intervallum és a Borenstein-féle τ²-intervallum középpontja (lásd i2_ci_higgins_thompson)."""
    _check_h_centre(h_centre)
    k = len(yi)
    df = k - 1
    c = _dl_c([1.0 / v for v in vi]) if k > 1 else 0.0
    q = cochran_q(yi, vi)
    out = {
        "k": k, "Q": q, "df": df,
        "p_Q": dist.chi2_sf(q, df) if df > 0 else None,
        "I2": (max(0.0, (q - df) / q) * 100.0) if (df > 0 and q > 0) else 0.0,
        "H2": (q / df) if df > 0 else None,
        "H": max(1.0, math.sqrt(q / df)) if df > 0 else None,
        "H2_M": max(0.0, (q - df) / df) if df > 0 else None,
        "C": c,
        "tau2_DL": tau2_dl(yi, vi),
        "s2_typical": typical_within_variance(vi),
        "H_ci_centre": h_centre,
    }
    lo, hi, hlo, hhi = i2_ci_higgins_thompson(q, k, level, h_centre)
    out["I2_ci_HT"] = [lo, hi] if lo is not None else None
    out["H_ci_HT"] = [hlo, hhi] if hlo is not None else None
    t_lo, t_hi = tau2_ci_bhhr(q, k, c, level, h_centre)
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
def meta_analysis(yi, vi, model="random", tau2_method=None, ci_method=None, level=0.95,
                  pi_method="t_k-2", labels=None, tau2_fixed=None, h_centre="truncated"):
    """Inverz-variancia súlyozott összesítés.

    model: 'fixed' (közös hatás), 'random', vagy 'ivhet' (Doi et al. 2015 inverz-variancia
           heterogenitás modell: FE pontbecslés, Var = Σ (w_i/Σw)²·(v_i + τ²); Khan 2020 4. fejezet).
    tau2_method: τ²-becslő; None = a modell alapértelmezése (random → 'REML', ivhet → 'DL' a
           publikált IVhet szerint; ivhet-nél más becslő is választható, figyelmeztetéssel).
    ci_method: 'z' | 't' | 'hksj' | 'hksj_adhoc'; alapértelmezés: fixed→z, random→hksj.
           IVhet-nél a CI mindig z-alapú; z-től eltérő kérés figyelmeztetést ad.
    tau2_fixed: ha megadott, ezt a τ²-t használja (pl. közös τ² alcsoportokhoz) — random és ivhet.
    h_centre: a Higgins–Thompson H/I² (és a Borenstein-féle τ²) intervallum középpontja:
           'truncated' (meta:::calcH, alapértelmezés) vagy 'untruncated' (lásd heterogeneity()).
    Az eredményben weights_raw a nyers inverz-variancia súlyok (1/v_i, ill. 1/(v_i + τ²)),
    sum_weights ezek összege; weights_pct = 100·weights_raw/sum_weights.
    """
    _check(yi, vi, labels)
    _check_h_centre(h_centre)
    yi = [float(y) for y in yi]
    vi = [float(v) for v in vi]
    k = len(yi)
    model = model.lower()
    if model in ("fe", "fixed", "common", "ce"):
        model = "fixed"
    elif model in ("re", "random"):
        model = "random"
    elif model == "ivhet":
        return _ivhet(yi, vi, level, labels, tau2_method, tau2_fixed, ci_method, h_centre)
    else:
        raise ModelError("ismeretlen modell: %r" % model)
    if tau2_method is None:
        tau2_method = "REML"
    if ci_method is None:
        ci_method = "z" if model == "fixed" else "hksj"
    if ci_method not in CI_METHODS:
        raise ModelError("ismeretlen CI-módszer: %r" % ci_method)
    if pi_method not in PI_METHODS:
        raise ModelError("ismeretlen PI-módszer: %r" % pi_method)
    warnings = []
    tau2_info = {}
    vratio = max(vi) / min(vi)
    if vratio >= 1e7:
        warnings.append("A legnagyobb és a legkisebb mintavételi variancia aránya rendkívül nagy "
                        "(%.3g ≥ 10⁷); az eredmények numerikusan instabilak lehetnek — ellenőrizd az "
                        "adatokat (pl. SE helyett p-érték vagy elírt tizedesjegy)." % vratio)
    if model == "fixed":
        tau2 = 0.0
        tau2_method_used = None
    else:
        if tau2_fixed is not None:
            tau2, tau2_method_used = float(tau2_fixed), "rögzített"
        else:
            tau2, tau2_info = estimate_tau2(yi, vi, tau2_method)
            tau2_method_used = tau2_method.upper()
            warnings += tau2_info_warnings(tau2_method_used, tau2_info)
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
        crit = dist.t_ppf(1 - alpha / 2, df_t)
        if ci_method == "hksj" and q_hk < 1:
            # q < 1 önmagában nem elég: a HKSJ-CI csak akkor szűkebb, ha t(k−1)·√q < z
            # (q < (z/t)², pl. k = 4-nél 0,38); a tényleges félszélességeket hasonlítjuk össze
            z_half = dist.norm_ppf(1 - alpha / 2) * se_wald
            if crit * se < z_half:
                warnings.append("HKSJ: q = %.3f < 1, és a HKSJ-CI szűkebb a Wald-CI-nál (félszélesség %.4g vs. "
                                "%.4g); érzékenységi elemzésként fontold meg a hksj_adhoc módszert."
                                % (q_hk, crit * se, z_half))
        stat = ratio_stat(mu, se)
        if math.isnan(stat):
            # HKSJ: q = 0 (minden y_i azonos) és a becslés pontosan 0 → 0/0 (metafor: NA)
            warnings.append("HKSJ: q = 0 (azonos hatásméretek) és a becslés 0, így a teszt 0/0 — "
                            "nem értelmezhető; érzékenységi elemzésként használd a hksj_adhoc vagy a "
                            "z (Wald) CI-t.")
        p = dist.t_two_sided_p(stat, df_t)
        test = "t"
    het = heterogeneity(yi, vi, tau2 if model == "random" else None, level, h_centre)
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
        heterogeneity=het, weights_pct=weights_pct, weights_raw=list(w), sum_weights=sw,
        yi=yi, vi=vi, labels=list(labels) if labels else ["#%d" % (i + 1) for i in range(k)],
        warnings=warnings,
    )
    return res


def _ivhet(yi, vi, level=0.95, labels=None, tau2_method=None, tau2_fixed=None, ci_method=None,
           h_centre="truncated"):
    """IVhet (Doi et al. 2015): FE (1/v_i) súlyok és pontbecslés, Var = Σ (w_i/Σw)²·(v_i + τ²), z-CI.

    τ²: tau2_fixed, ha megadott; különben tau2_method (None → 'DL', mint a publikált IVhet-ben és a
    MetaXL/admetan-ban; más becslő figyelmeztetéssel). ci_method: csak None/'z' érvényes
    a modellben — más ismert érték figyelmeztetéssel figyelmen kívül marad (a CI mindig z)."""
    k = len(yi)
    warnings = [] if k >= 2 else ["IVhet: k < 2."]
    if ci_method is not None and ci_method not in CI_METHODS:
        raise ModelError("ismeretlen CI-módszer: %r" % ci_method)
    if ci_method not in (None, "z"):
        warnings.append("IVhet: a kért '%s' CI-módszert figyelmen kívül hagytam — az IVhet-modell "
                        "konfidenciaintervalluma mindig z-alapú (Doi et al. 2015)." % ci_method)
    tau2_info = {}
    if tau2_fixed is not None:
        tau2 = float(tau2_fixed)
        if not (tau2 >= 0) or math.isinf(tau2):
            raise ModelError("tau2_fixed: nemnegatív, véges szám kell (kapott: %r)" % tau2_fixed)
        tau2_method_used = "rögzített"
    else:
        method = (tau2_method or "DL").upper()
        tau2, tau2_info = estimate_tau2(yi, vi, method)
        tau2_method_used = method
        warnings += tau2_info_warnings(method, tau2_info)
        if method != "DL":
            warnings.append("IVhet: a(z) %s τ²-becslő eltér a publikált IVhet-modelltől (Doi et al. 2015: "
                            "DerSimonian–Laird); az eredmény nem vethető össze közvetlenül a MetaXL / "
                            "Stata admetan IVhet-kimenetével." % method)
    w = [1.0 / v for v in vi]
    mu, sw = _wmean(yi, w)
    var = sum((wi / sw) ** 2 * (v + tau2) for wi, v in zip(w, vi))
    se = math.sqrt(var)
    crit = dist.norm_ppf(0.5 + level / 2.0)
    stat = ratio_stat(mu, se)
    het = heterogeneity(yi, vi, tau2, level, h_centre)
    return MetaResult(
        model="ivhet", k=k, estimate=mu, se=se, se_wald=math.sqrt(1.0 / sw),
        ci_lower=mu - crit * se, ci_upper=mu + crit * se, level=level, test="z", stat=stat, df=None,
        p=dist.z_two_sided_p(stat), ci_method="z", tau2=tau2, tau=math.sqrt(tau2),
        tau2_method=tau2_method_used,
        tau2_info=tau2_info, q_hksj=None, pi_lower=None, pi_upper=None, pi_method=None, pi_df=None,
        Q=het["Q"], Q_df=het["df"], p_Q=het["p_Q"], I2=het["I2"], H2=het["H2"], heterogeneity=het,
        weights_pct=[100.0 * wi / sw for wi in w], weights_raw=list(w), sum_weights=sw,
        yi=list(yi), vi=list(vi), labels=list(labels) if labels else ["#%d" % (i + 1) for i in range(k)],
        warnings=warnings,
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


def mantel_haenszel(e1, n1, e2, n2, measure="OR", level=0.95, labels=None, cc=0.5, rd_var="sato",
                    h_centre="truncated"):
    """Mantel–Haenszel fix hatású összesítés (OR, RR, RD).

    Varianciák: OR — Robins–Breslow–Greenland (1986); RR — Greenland & Robins (1985);
    RD — rd_var='sato': Sato, Greenland & Robins (1989), kettősen konzisztens (metafor
    alapértelmezés); rd_var='gr': Greenland & Robins (1985) (RevMan-képlet).  A pontbecslés folytonossági korrekció nélküli
    (metafor rma.mh, drop00 = c(TRUE, FALSE)): a kettős-nulla táblák OR/RR-nél nem járulnak
    hozzá, a kettős-100% táblák OR-nél nem, RR-nél viszont igen (m1·m2/n a számlálóhoz és a
    nevezőhöz) — ezért RR-nél bent maradnak; üres karú (n = 0) tábla kimarad. A heterogenitási
    Q a vizsgálatonkénti inverz-variancia becslésekből számolódik az MH-becslés körül
    (a kettős-nulla / kettős-100% táblák nélkül, mint a metafor-ban). Nulla összesített
    variancia (degenerált táblák) esetén se = 0, a z-statisztika ±inf vagy 0/0 → NaN.
    Súlyok: weights_raw / weights_pct / weights_labels pozíció szerint (a becslésbe bevont
    táblák sorrendjében, ismétlődő címkével is); weights_by_label és weights_raw_by_label
    címkénként összegez (ismétlődő címkéjű sorok — több kar — súlya összeadódik).
    h_centre: a heterogenitási blokk H/I² CI-középpontja (lásd heterogeneity()).
    """
    _check_h_centre(h_centre)
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
    w_raw = []
    for lab, (a, m1, c, m2) in zip(labels, rows):
        a, c, m1, m2 = float(a), float(c), float(m1), float(m2)
        b, d = m1 - a, m2 - c
        n = m1 + m2
        if m1 <= 0 or m2 <= 0:
            excluded.append((lab, "üres kar (n1 vagy n2 = 0) — nincs információ"))
            continue
        double_zero = a == 0 and c == 0
        double_full = b == 0 and d == 0
        # metafor rma.mh (drop00[2] = FALSE): a kettős-nulla tábla OR-nél és RR-nél is 0-t ad
        # minden összeghez; a kettős-100% tábla OR-nél 0-t ad (R = S = 0), RR-nél viszont a
        # számlálóhoz és a nevezőhöz is m1·m2/n-t ad (1 felé húz) — ezért RR-nél bent marad.
        if measure == "OR" and (double_zero or double_full):
            excluded.append((lab, "kettős nulla esemény — az MH-becsléshez nem járul hozzá" if double_zero
                             else "kettős 100% esemény — OR-nél nem járul hozzá"))
            continue
        if measure == "RR" and double_zero:
            excluded.append((lab, "kettős nulla esemény — az MH-becsléshez nem járul hozzá"))
            continue
        used.append(lab)
        w_raw.append((b * c / n) if measure == "OR" else ((c * m1 / n) if measure == "RR" else m1 * m2 / n))
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
        if rd_den <= 0:
            raise ModelError("MH RD nem becsülhető")
        est = rd_num / rd_den
        if rd_var == "gr":
            var = rd_gr / rd_den ** 2
        else:
            var = (est * rd_p + rd_q) / rd_den ** 2
    # analitikusan nemnegatív; pl. 0% vs 100%-os tábláknál kerekítés miatt lehet -1e-17
    var = max(var, 0.0)
    se = math.sqrt(var)
    z = dist.norm_ppf(0.5 + level / 2)
    mh_warn = []
    if se == 0:
        mh_warn.append("MH: az összesített variancia 0 (degenerált táblák, pl. minden vizsgálat "
                       "kettős nulla vagy 0% vs 100%); a z-teszt nem értelmezhető.")
    # heterogenitás: IV-becslések az MH körül (metafor rma.mh konvenció: a kettős-nulla
    # táblák kimaradnak, a nulla cellás táblák minden cellájához +cc kerül — RD-nél is).
    # Az üres karú (n = 0) tábla itt is kimarad: nincs becsülhető hatása (a metafor ilyenkor
    # a +cc-vel kapott, értelmetlen 0,5/1 arányt is beszámítaná a QE-be).
    yi, vi, labs = [], [], []
    for lab, (a, m1, c, m2) in zip(labels, rows):
        a, c, m1, m2 = float(a), float(c), float(m1), float(m2)
        b, d = m1 - a, m2 - c
        if m1 <= 0 or m2 <= 0:
            continue
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
    # metafor rma.mh: k.yi <= 1 esetén QE = 0
    q = sum((y - est) ** 2 / v for y, v in zip(yi, vi)) if len(yi) > 1 else 0.0
    df = max(0, len(yi) - 1)
    tot = sum(w_raw)
    w_pct = [100.0 * w / tot for w in w_raw] if tot > 0 else None
    het = heterogeneity(yi, vi, None, level, h_centre) if len(yi) >= 1 else {}
    stat = ratio_stat(est, se)
    return MetaResult(
        model="MH", measure=measure, k=len(rows), k_estimable=len(used), estimate=est, se=se, se_wald=se,
        ci_lower=est - z * se, ci_upper=est + z * se, level=level, test="z", stat=stat,
        df=None, p=dist.z_two_sided_p(stat), ci_method="z", tau2=0.0, tau=0.0,
        tau2_method=None, pi_lower=None, pi_upper=None, pi_method=None,
        Q=q, Q_df=df, p_Q=dist.chi2_sf(q, df) if df > 0 else None,
        I2=(max(0.0, (q - df) / q) * 100 if df > 0 and q > 0 else 0.0),
        H2=(q / df if df > 0 else None), heterogeneity=het,
        weights_pct=w_pct, weights_raw=w_raw, weights_labels=list(used), sum_weights=tot,
        weights_by_label=_sum_by_label(used, w_pct) if w_pct is not None else {},
        weights_raw_by_label=_sum_by_label(used, w_raw),
        yi=yi, vi=vi, labels=labs, excluded=excluded,
        warnings=mh_warn,
    )


def _sum_by_label(labels, values):
    """Címke → összeg (ismétlődő címkéjű sorok értéke összeadódik, nem íródik felül)."""
    out = {}
    for lab, v in zip(labels, values):
        out[lab] = out.get(lab, 0.0) + v
    return out


def peto(e1, n1, e2, n2, level=0.95, labels=None, h_centre="truncated"):
    """Peto-féle egylépéses OR (ritka események, kiegyensúlyozott karok esetén).

    weights_pct / weights_raw / weights_labels pozíció szerint; weights_by_label címkénként
    összegez (ismétlődő címkék). h_centre: a heterogenitási blokk H/I² CI-középpontja."""
    _check_h_centre(h_centre)
    rows = list(zip(e1, n1, e2, n2))
    labels = list(labels) if labels else ["#%d" % (i + 1) for i in range(len(rows))]
    labels, rows, excluded = _drop_incomplete(labels, rows)
    o_e, var_list, yi, vi, labs = [], [], [], [], []
    for lab, (a, m1, c, m2) in zip(labels, rows):
        a, c, m1, m2 = float(a), float(c), float(m1), float(m2)
        n = m1 + m2
        events = a + c
        nonevents = n - events
        if m1 <= 0 or m2 <= 0:
            excluded.append((lab, "üres kar (n1 vagy n2 = 0) — nincs információ"))
            continue
        if n <= 1 or events == 0 or nonevents == 0:
            excluded.append((lab, "nincs információ (0 vagy 100% esemény összesen)"))
            continue
        e = m1 * events / n
        v = m1 * m2 * events * nonevents / (n * n * (n - 1))
        if not (v > 0):
            excluded.append((lab, "nincs információ (nulla hipergeometrikus variancia)"))
            continue
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
        H2=(q / df if df > 0 else None), heterogeneity=heterogeneity(yi, vi, None, level, h_centre),
        weights_pct=[100 * v / sv for v in var_list], weights_raw=list(var_list), sum_weights=sv,
        weights_labels=list(labs), weights_by_label=_sum_by_label(labs, [100 * v / sv for v in var_list]),
        yi=yi, vi=vi, labels=labs,
        excluded=excluded, warnings=[],
    )
