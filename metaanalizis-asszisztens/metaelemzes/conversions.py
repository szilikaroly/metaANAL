# -*- coding: utf-8 -*-
"""Adatkinyerési konverziók: hiányzó átlag/SD becslése, hatásméret-átváltások.

Minden becsült (nem közvetlenül közölt) értéket a riportban és az ellenőrző
ágensnek jelezni kell; érzékenységi elemzés javasolt a becsült értékek nélkül.

Források: Wan et al. 2014 (BMC Med Res Methodol 14:135); Luo et al. 2018 (Stat Methods
Med Res 27:1785); Hozo et al. 2005; Cochrane Handbook v6 6.5.2 és 6.5.2.10 (Table 6.5.a);
Borenstein et al. 2009, 7. fejezet (hatásméretek közti átváltás).
"""
import math

from . import distributions as dist


class ConversionError(ValueError):
    pass


def _check_level(level):
    if level is None or not 0 < level < 1:
        raise ConversionError("0 < level < 1 szükséges (pl. 0.95), kapott: %r" % (level,))


def _check_n(n, minimum=2):
    if n is None:
        raise ConversionError("n (mintanagyság) kötelező")
    if n < minimum:
        raise ConversionError("n >= %d szükséges, kapott: %g" % (minimum, n))


def _check_interval(lower, upper):
    if lower is None or upper is None:
        raise ConversionError("az alsó és a felső határ is kötelező")
    if lower > upper:
        raise ConversionError("az alsó határ (%g) nagyobb, mint a felső (%g) — felcserélt határok?" % (lower, upper))


def _check_fivenum(n, median=None, q1=None, q3=None, minimum=None, maximum=None, need_n=True):
    """min <= Q1 <= medián <= Q3 <= max (a megadott értékekre), és n >= 2 — mint a
    metafor::conv.fivenum. Felcserélt kvartilisek negatív SD-t adnának."""
    if need_n:
        _check_n(n, 2)
    seq = [(name, v) for name, v in (("min", minimum), ("Q1", q1), ("medián", median), ("Q3", q3),
                                     ("max", maximum)) if v is not None]
    for (na, a), (nb, b) in zip(seq, seq[1:]):
        if a > b:
            raise ConversionError("min <= Q1 <= medián <= Q3 <= max nem teljesül: %s = %g > %s = %g" % (na, a, nb, b))


# --------------------------------------------------- SD / SE / CI átváltás
def sd_from_se(se, n):
    """Egy csoport átlagának SE-jéből SD = SE·√n."""
    _check_n(n, 1)
    if se is None or se < 0:
        raise ConversionError("SE >= 0 szükséges, kapott: %r" % (se,))
    return se * math.sqrt(n)


def sd_from_ci(lower, upper, n, level=0.95, use_t=True):
    """Egy csoport átlagának CI-jéből SD (Cochrane Handbook 6.5.2.2).

    Kis mintánál (n < 60) a t-eloszlás kvantilise használandó; use_t=True ezt
    mindig alkalmazza (n >= 60-nál a különbség elhanyagolható).
    """
    _check_level(level)
    _check_n(n, 2)
    _check_interval(lower, upper)
    q = 0.5 + level / 2.0
    crit = dist.t_ppf(q, n - 1) if use_t else dist.norm_ppf(q)
    return math.sqrt(n) * (upper - lower) / (2.0 * crit)


def se_from_ci(lower, upper, level=0.95, log_scale=False):
    """Hatásméret SE-je a CI-ből (arányoknál log_scale=True: ln(U), ln(L))."""
    _check_level(level)
    _check_interval(lower, upper)
    if log_scale:
        if lower <= 0 or upper <= 0:
            raise ConversionError("log-skálához pozitív határok kellenek")
        lower, upper = math.log(lower), math.log(upper)
    crit = dist.norm_ppf(0.5 + level / 2.0)
    return (upper - lower) / (2.0 * crit)


def sd_diff_from_t(t, n1, n2, md):
    """Két csoport különbségének t-statisztikájából az összevont SD (Cochrane 6.5.2.3)."""
    se = abs(md / t)
    return se / math.sqrt(1.0 / n1 + 1.0 / n2)


def se_from_p(estimate, p, log_scale=False, df=None):
    """Kétoldali p-értékből SE (Cochrane 6.3.2); kis mintánál df-fel t-eloszlás."""
    if log_scale:
        estimate = math.log(estimate)
    crit = dist.t_ppf(1 - p / 2.0, df) if df else dist.norm_ppf(1 - p / 2.0)
    return abs(estimate) / crit


# -------------------------------------------- medián / IQR / tartomány
def mean_from_median(n, median, q1=None, q3=None, minimum=None, maximum=None, method="luo"):
    """Átlag becslése (Luo et al. 2018); method='hozo' a régi (a+2m+b)/4 képlet (S1)."""
    if median is None:
        raise ConversionError("a medián kötelező")
    _check_fivenum(n, median, q1, q3, minimum, maximum, need_n=(method != "hozo"))
    s1 = minimum is not None and maximum is not None
    s2 = q1 is not None and q3 is not None
    if method == "hozo":
        if not s1:
            raise ConversionError("Hozo-képlethez min és max kell")
        return (minimum + 2 * median + maximum) / 4.0
    if s1 and s2:
        w1 = 2.2 / (2.2 + n ** 0.75)
        w2 = 0.7 - 0.72 / n ** 0.55
        return w1 * (minimum + maximum) / 2.0 + w2 * (q1 + q3) / 2.0 + (1 - w1 - w2) * median
    if s1:
        w = 4.0 / (4.0 + n ** 0.75)
        return w * (minimum + maximum) / 2.0 + (1 - w) * median
    if s2:
        w = 0.7 + 0.39 / n
        return w * (q1 + q3) / 2.0 + (1 - w) * median
    raise ConversionError("min+max vagy Q1+Q3 kell")


def sd_from_median(n, q1=None, q3=None, minimum=None, maximum=None, median=None):
    """SD becslése (Wan et al. 2014, S1/S2/S3 forgatókönyv). A median (ha megadod) csak
    a sorrend-ellenőrzéshez kell (min <= Q1 <= medián <= Q3 <= max)."""
    _check_fivenum(n, median, q1, q3, minimum, maximum)
    s1 = minimum is not None and maximum is not None
    s2 = q1 is not None and q3 is not None
    xi = 2.0 * dist.norm_ppf((n - 0.375) / (n + 0.25))
    eta = 2.0 * dist.norm_ppf((0.75 * n - 0.125) / (n + 0.25))
    if s1 and s2:
        return (maximum - minimum) / (2.0 * xi) + (q3 - q1) / (2.0 * eta)
    if s1:
        return (maximum - minimum) / xi
    if s2:
        return (q3 - q1) / eta
    raise ConversionError("min+max vagy Q1+Q3 kell")


# ----------------------------------------------------- csoportok, változás
def combine_groups(n1, m1, sd1, n2, m2, sd2):
    """Két alcsoport összevonása egy csoporttá (Cochrane Handbook Table 6.5.a)."""
    n = n1 + n2
    m = (n1 * m1 + n2 * m2) / n
    var = ((n1 - 1) * sd1 ** 2 + (n2 - 1) * sd2 ** 2 + n1 * n2 / n * (m1 ** 2 + m2 ** 2 - 2 * m1 * m2)) / (n - 1)
    return n, m, math.sqrt(var)


def sd_change(sd_baseline, sd_final, corr):
    """A változás SD-je imputált korrelációval (Cochrane 6.5.2.8)."""
    if not -1 <= corr <= 1:
        raise ConversionError("a korreláció -1 és 1 közé esik")
    v = sd_baseline ** 2 + sd_final ** 2 - 2 * corr * sd_baseline * sd_final
    if v < 0:
        raise ConversionError("negatív variancia")
    return math.sqrt(v)


def corr_from_change(sd_baseline, sd_final, sd_change_):
    """A kiindulás–végpont korreláció visszaszámolása egy teljesen közölt vizsgálatból."""
    return (sd_baseline ** 2 + sd_final ** 2 - sd_change_ ** 2) / (2 * sd_baseline * sd_final)


def split_shared_control(n_control, k_arms):
    """Több-karú vizsgálat: a közös kontroll mintanagyságának felosztása (egységelemzési
    hiba elkerülése; Cochrane 23.3.4). Az átlag és SD változatlan marad."""
    base = n_control // k_arms
    rest = n_control - base * k_arms
    return [base + (1 if i < rest else 0) for i in range(k_arms)]


# ------------------------------------------------ hatásméret-átváltások
def logor_to_d(logor, v_logor):
    """Hasselblad–Hedges: d = lnOR·√3/π; V_d = V_lnOR·3/π² (Borenstein 7.1–7.2)."""
    return logor * math.sqrt(3) / math.pi, v_logor * 3.0 / math.pi ** 2


def d_to_logor(d, v_d):
    return d * math.pi / math.sqrt(3), v_d * math.pi ** 2 / 3.0


def r_to_d(r, v_r):
    """d = 2r/√(1-r²); V_d = 4V_r/(1-r²)³ (Borenstein 7.5–7.6)."""
    return 2 * r / math.sqrt(1 - r * r), 4 * v_r / (1 - r * r) ** 3


def d_to_r(d, v_d, n1, n2):
    """r = d/√(d²+a), a = (n1+n2)²/(n1·n2); V_r = a²V_d/(d²+a)³ (Borenstein 7.7–7.9)."""
    a = (n1 + n2) ** 2 / float(n1 * n2)
    return d / math.sqrt(d * d + a), a * a * v_d / (d * d + a) ** 3


def d_from_t(t, n1, n2):
    """Független mintás t-statisztikából d = t·√(1/n1 + 1/n2)."""
    return t * math.sqrt(1.0 / n1 + 1.0 / n2)
