# -*- coding: utf-8 -*-
"""Előzetes (prospektív) erőelemzés fix és véletlen hatású metaanalízishez.

Hedges & Pigott (2001, Psychological Methods 6:203–217) közelítése: k azonos méretű vizsgálat,
vizsgálatonkénti mintavételi variancia v; az összesített becslés varianciája
  fix hatás:       v_m = v / k
  véletlen hatás:  v_m = (v + τ²) / k
λ = |θ| / √v_m; kétoldali erő = 1 − Φ(z_{1−α/2} − λ) + Φ(−z_{1−α/2} − λ),
egyoldali (a feltételezett hatás irányában) = 1 − Φ(z_{1−α} − λ).

A heterogenitás megadható közvetlenül (tau2 vagy i2), vagy a dmetar::power.analysis
konvenciója szerint szóban: 'fixed' / 'low' / 'moderate' / 'high' → v_m = f · v/k, ahol
f = 1 / 1,33 / 1,67 / 2 (dmetar; Harrer et al. 2021). Ez Hedges & Pigott (2001) kis / közepes /
nagy heterogenitásának (τ² = v/3, 2v/3, v; azaz I² = 25%, 40%, 50%) kerekített alakja;
factors='hedges_pigott' a pontos 4/3, 5/3, 2 tényezőket adja.

SMD (Cohen d / Hedges g) esetén v = (n1 + n2)/(n1·n2) + d²/(2(n1 + n2)) (dmetar; Borenstein et al.
2009 4.20 közelítés); OR megadásakor d = ln(OR)·√3/π (Chinn 2000, mint a dmetar); nyers
átlagkülönbségnél (MD) v = sd²·(n1 + n2)/(n1·n2); bármely más mértéknél v közvetlenül adható.

Fontos: a prospektív erő a TERVEZÉSHEZ való (hány vizsgálat / milyen méret kell); egy elkészült
metaanalízis utólagos ('post hoc') ereje a megfigyelt hatásból nem informatív (Hoenig & Heisey 2001).
"""
import math

from . import distributions as dist
from .models import MetaResult, ModelError

HETEROGENEITY_FACTORS = {
    "dmetar": {"fixed": 1.0, "low": 1.33, "moderate": 1.67, "high": 2.0},
    "hedges_pigott": {"fixed": 1.0, "low": 4.0 / 3.0, "moderate": 5.0 / 3.0, "high": 2.0},
}
POWER_MEASURES = ("SMD", "MD", "GEN")


def smd_variance(d, n1, n2):
    """Egy kétkaros vizsgálat SMD-jének közelítő mintavételi varianciája (dmetar::power.analysis)."""
    return (n1 + n2) / float(n1 * n2) + d * d / (2.0 * (n1 + n2))


def _power_from_lambda(lam, alpha, tails):
    if tails == 2:
        z = dist.norm_ppf(1.0 - alpha / 2.0)
        return 1.0 - dist.norm_cdf(z - lam) + dist.norm_cdf(-z - lam), z
    z = dist.norm_ppf(1.0 - alpha)
    return 1.0 - dist.norm_cdf(z - lam), z


def power_analysis(k, effect=None, n1=None, n2=None, v=None, sd=None, OR=None, measure="SMD",
                   heterogeneity="fixed", tau2=None, i2=None, alpha=0.05, tails=2, factors="dmetar"):
    """Prospektív erő egy tervezett metaanalízis összesített hatásának z-tesztjéhez.

    k: a várható vizsgálatszám; effect: a feltételezett hatás (SMD-nél d, MD-nél nyers különbség,
    GEN-nél tetszőleges skálán, pl. ln OR); OR: esélyhányados (d = ln(OR)·√3/π, measure = SMD);
    n1, n2: vizsgálatonkénti karlétszám; v: a vizsgálatonkénti mintavételi variancia a hatás
    skáláján (megadva felülírja a képletet); sd: közös SD a nyers MD-hez.
    OR mellett v és tau2 nem adható meg (a d-skálára váltás miatt egy ln OR-skálájú v / τ² hibás
    erőt adna): ln OR-skálájú varianciához measure='GEN', effect=ln(OR) használandó.
    Heterogenitás (elsőbbségi sorrend): tau2 (abszolút τ²) > i2 (I² %-ban: τ² = v·I²/(100 − I²)) >
    heterogeneity ('fixed' / 'low' / 'moderate' / 'high', factors szerinti tényezővel).
    alpha: szignifikanciaszint; tails: 2 (kétoldali, alapértelmezés) vagy 1.

    Visszaad: MetaResult(kind='power', power, lambda_, v_study, v_pooled, tau2, het_factor, ...).
    A dmetar::power.analysis(d, k, n1, n2, p, heterogeneity) eredményét pontosan reprodukálja
    (factors='dmetar', tails=2).
    """
    warnings = []
    measure = (measure or "SMD").upper()
    if measure not in POWER_MEASURES:
        raise ModelError("erőelemzés: measure %s lehet (kapott: %r)" % (" / ".join(POWER_MEASURES), measure))
    if tails not in (1, 2):
        raise ModelError("erőelemzés: tails 1 vagy 2 lehet")
    if not (0.0 < alpha < 1.0):
        raise ModelError("erőelemzés: alpha a (0, 1) intervallumba kell essen")
    try:
        k_ok = float(k) >= 1 and float(k) == int(k)
    except (TypeError, ValueError):
        k_ok = False
    if not k_ok:
        raise ModelError("erőelemzés: k pozitív egész kell legyen")
    k = int(k)
    if OR is not None:
        if effect is not None:
            raise ModelError("erőelemzés: vagy effect (d), vagy OR adható meg, a kettő együtt nem")
        if not (OR > 0):
            raise ModelError("erőelemzés: OR > 0 kell")
        if v is not None or tau2 is not None:
            raise ModelError("erőelemzés: OR mellett v és τ² nem adható meg (az OR a d-skálára vált, d = ln OR·√3/π, "
                             "így egy ln OR-skálájú v / τ² hibás erőt adna); ln OR-skálájú varianciához: "
                             "--measure GEN --effect <ln OR> --v ..., OR mellett pedig n1/n2 (és --heterogeneity "
                             "vagy --i2)")
        effect = math.log(OR) * math.sqrt(3.0) / math.pi
        measure = "SMD"
    if effect is None:
        raise ModelError("erőelemzés: a feltételezett hatás (effect vagy OR) kötelező")
    effect = float(effect)
    if v is None:
        if n1 is None or n2 is None or not (n1 > 0 and n2 > 0):
            raise ModelError("erőelemzés: n1 és n2 (pozitív) vagy a vizsgálatonkénti v megadása kötelező")
        if measure == "SMD":
            v = smd_variance(effect, n1, n2)
        elif measure == "MD":
            if sd is None or not (sd > 0):
                raise ModelError("erőelemzés: nyers MD-nél a közös sd (> 0) vagy v megadása kötelező")
            v = sd * sd * (n1 + n2) / float(n1 * n2)
        else:
            raise ModelError("erőelemzés: GEN mértéknél a vizsgálatonkénti v megadása kötelező")
    v = float(v)
    if not (v > 0) or math.isinf(v):
        raise ModelError("erőelemzés: v pozitív, véges szám kell")
    if factors not in HETEROGENEITY_FACTORS:
        raise ModelError("erőelemzés: factors 'dmetar' vagy 'hedges_pigott' lehet")
    if tau2 is not None:
        tau2 = float(tau2)
        if not (tau2 >= 0) or math.isinf(tau2):
            raise ModelError("erőelemzés: tau2 nemnegatív, véges szám kell")
        het_label = "τ² = %.4g" % tau2
        factor = (v + tau2) / v
    elif i2 is not None:
        i2 = float(i2)
        if not (0.0 <= i2 < 100.0):
            raise ModelError("erőelemzés: i2 a [0, 100) tartományban (%) kell legyen")
        tau2 = v * i2 / (100.0 - i2)
        het_label = "I² = %.4g%%" % i2
        factor = (v + tau2) / v
    else:
        key = str(heterogeneity).lower()
        table = HETEROGENEITY_FACTORS[factors]
        if key not in table:
            raise ModelError("erőelemzés: heterogeneity 'fixed', 'low', 'moderate' vagy 'high' lehet "
                             "(kapott: %r)" % (heterogeneity,))
        factor = table[key]
        tau2 = (factor - 1.0) * v
        het_label = key
    v_pooled = factor * v / k
    lam = abs(effect) / math.sqrt(v_pooled)
    power, zcrit = _power_from_lambda(lam, alpha, tails)
    model = "fixed" if tau2 == 0 else "random"
    if k < 5 and model == "random":
        warnings.append("k = %d < 5: véletlen hatású modellnél a τ² becslése nagyon bizonytalan; a "
                        "z-alapú erőszámítás optimista lehet (HKSJ-CI-vel az erő kisebb)." % k)
    return MetaResult(
        kind="power", model=model, measure=measure, effect=effect, k=k, n1=n1, n2=n2,
        v_study=v, v_pooled=v_pooled, se_pooled=math.sqrt(v_pooled), tau2=tau2, het_factor=factor,
        heterogeneity=het_label, factors=factors, lambda_=lam, z_crit=zcrit, alpha=alpha, tails=tails,
        power=power,
        method=("Hedges & Pigott (2001) z-teszt alapú erő; %s heterogenitás-tényezők"
                % ("dmetar" if factors == "dmetar" else "Hedges–Pigott")),
        warnings=warnings,
    )


def studies_needed(target_power=0.8, max_k=100000, **kwargs):
    """A legkisebb k, amelynél a power_analysis(k, **kwargs) ereje eléri a target_power-t.

    Visszaad: MetaResult(kind='power_k', k, power, target_power, result) — k = None, ha a célerő
    max_k vizsgálattal sem érhető el (pl. nulla feltételezett hatásnál az erő mindig α)."""
    if not (0.0 < target_power < 1.0):
        raise ModelError("erőelemzés: target_power a (0, 1) intervallumba kell essen")
    kwargs = dict(kwargs)
    kwargs.pop("k", None)
    # az erő k-ban monoton nő (v_m = f·v/k) → bináris keresés
    lo_res = power_analysis(1, **kwargs)
    if lo_res.power >= target_power:
        return MetaResult(kind="power_k", k=1, power=lo_res.power, target_power=target_power,
                          result=lo_res, warnings=[])
    hi_res = power_analysis(max_k, **kwargs)
    if hi_res.power < target_power:
        return MetaResult(kind="power_k", k=None, power=hi_res.power, target_power=target_power,
                          result=hi_res, warnings=["A célerő k <= %d vizsgálattal sem érhető el." % max_k])
    lo, hi = 1, max_k
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if power_analysis(mid, **kwargs).power >= target_power:
            hi = mid
        else:
            lo = mid
    res = power_analysis(hi, **kwargs)
    return MetaResult(kind="power_k", k=hi, power=res.power, target_power=target_power, result=res,
                      warnings=list(res.warnings))
