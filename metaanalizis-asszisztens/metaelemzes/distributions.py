# -*- coding: utf-8 -*-
"""Eloszlásfüggvények csak a standard könyvtárral (normális, t, khí-négyzet, F).

A metaanalízishez szükséges eloszlásfüggvényeket implementálja numpy/scipy
nélkül, hogy a motor telepítés nélkül fusson. Pontosság: ~1e-12 relatív a
tipikus tartományban (az ellenőrzést a tests/test_distributions.py végzi).
"""
import math

_EPS = 1e-15
_FPMIN = 1e-300


# ------------------------------------------------------------------ normális
def norm_cdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_sf(x):
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def norm_ppf(p):
    """A standard normális eloszlás kvantilisfüggvénye.

    Acklam-féle racionális közelítés, majd két Halley-lépés az erfc-vel
    számolt pontos eloszlásfüggvényen (gépi pontosság közelében).
    """
    if not 0.0 < p < 1.0:
        if p == 0.0:
            return -math.inf
        if p == 1.0:
            return math.inf
        raise ValueError("norm_ppf: p a (0, 1) intervallumon kívül: %r" % p)
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p > 1 - plow:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    else:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    # Halley-finomítás
    for _ in range(2):
        e = norm_cdf(x) - p
        u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2.0)
        x = x - u / (1 + x * u / 2.0)
    return x


def norm_isf(p):
    return -norm_ppf(p)


# ----------------------------------------------------- inkomplett béta / gamma
def _betacf(a, b, x):
    """Lentz-féle lánctört az inkomplett béta-függvényhez (Numerical Recipes)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, 10001):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 3e-16:
            return h
    raise ArithmeticError("_betacf nem konvergált (a=%r, b=%r, x=%r)" % (a, b, x))


def betainc_reg(a, b, x):
    """Regularizált inkomplett béta-függvény I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _gser(a, x):
    """P(a, x) sorfejtéssel (x < a + 1)."""
    ap = a
    s = 1.0 / a
    dl = s
    for _ in range(100000):
        ap += 1.0
        dl *= x / ap
        s += dl
        if abs(dl) < abs(s) * 1e-16:
            break
    return s * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a, x):
    """Q(a, x) lánctörttel (x >= a + 1)."""
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, 100000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-16:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gammainc_lower_reg(a, x):
    """P(a, x) regularizált alsó inkomplett gamma."""
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def gammainc_upper_reg(a, x):
    """Q(a, x) = 1 - P(a, x), farokban is pontosan."""
    if x <= 0.0:
        return 1.0
    if x < a + 1.0:
        return 1.0 - _gser(a, x)
    return _gcf(a, x)


# ---------------------------------------------------------------- khí-négyzet
def chi2_cdf(x, df):
    if df <= 0:
        raise ValueError("chi2_cdf: df > 0 kell")
    return gammainc_lower_reg(df / 2.0, x / 2.0)


def chi2_sf(x, df):
    if df <= 0:
        raise ValueError("chi2_sf: df > 0 kell")
    return gammainc_upper_reg(df / 2.0, x / 2.0)


def chi2_ppf(p, df):
    """Khí-négyzet kvantilis (alsó farok p).

    Bisekció a log(x) skálán; p < 0,5 esetén az alsó, egyébként a felső
    farok-függvényt invertálja, így mindkét szélen relatív pontosságú.
    """
    if not 0.0 < p < 1.0:
        if p == 0.0:
            return 0.0
        if p == 1.0:
            return math.inf
        raise ValueError("chi2_ppf: p a (0, 1) intervallumon kívül")
    if p < 0.5:
        fn = lambda lx: chi2_cdf(math.exp(lx), df) - p
    else:
        q = 1.0 - p
        fn = lambda lx: q - chi2_sf(math.exp(lx), df)
    lo, hi = -10.0, math.log(df + 10.0 * math.sqrt(2.0 * df) + 10.0)
    while fn(lo) > 0:
        lo *= 2.0
    while fn(hi) < 0:
        hi += 2.0
    return math.exp(_bisect(fn, lo, hi, tol=1e-15))


# ------------------------------------------------------------------------ t
def t_cdf(t, df):
    if df <= 0:
        raise ValueError("t_cdf: df > 0 kell")
    if math.isinf(df):
        return norm_cdf(t)
    x = df / (df + t * t)
    tail = 0.5 * betainc_reg(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_sf(t, df):
    return t_cdf(-t, df)


def t_ppf(p, df):
    if not 0.0 < p < 1.0:
        if p == 0.0:
            return -math.inf
        if p == 1.0:
            return math.inf
        raise ValueError("t_ppf: p a (0, 1) intervallumon kívül")
    if math.isinf(df):
        return norm_ppf(p)
    if p == 0.5:
        return 0.0
    if p > 0.5:
        return -t_ppf(1.0 - p, df)
    # alsó farok: t_cdf(v) = p, v < 0; bisekció a log(-v) skálán
    fn = lambda lv: p - t_cdf(-math.exp(lv), df)
    lo, hi = -40.0, max(3.0, math.log(abs(norm_ppf(p)) * 4 + 1))
    while fn(hi) < 0:
        hi += 2.0
    while fn(lo) > 0:
        lo *= 2.0
    return -math.exp(_bisect(fn, lo, hi, tol=1e-15))


def t_two_sided_p(t, df):
    if t != t:          # NaN-statisztika (pl. 0/0) → NaN p (nem 1 és nem 0)
        return float("nan")
    return min(1.0, 2.0 * t_sf(abs(t), df))


# ------------------------------------------------------------------------ F
def f_sf(f, df1, df2):
    if f <= 0:
        return 1.0
    x = df2 / (df2 + df1 * f)
    return betainc_reg(df2 / 2.0, df1 / 2.0, x)


def f_cdf(f, df1, df2):
    return 1.0 - f_sf(f, df1, df2)


# ------------------------------------------------------------------ segédek
def _bisect(fn, lo, hi, tol=1e-14, maxiter=500):
    flo = fn(lo)
    fhi = fn(hi)
    if flo == 0:
        return lo
    if fhi == 0:
        return hi
    if (flo > 0) == (fhi > 0):
        raise ArithmeticError("_bisect: a gyök nincs bekeretezve")
    for _ in range(maxiter):
        mid = 0.5 * (lo + hi)
        fm = fn(mid)
        if fm == 0 or (hi - lo) < tol * max(1.0, abs(mid)):
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def z_two_sided_p(z):
    if z != z:          # NaN-statisztika (pl. 0/0) → NaN p
        return float("nan")
    return min(1.0, 2.0 * norm_sf(abs(z)))
