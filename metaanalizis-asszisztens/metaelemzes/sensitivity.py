# -*- coding: utf-8 -*-
"""Érzékenységi és befolyás-elemzések.

- Leave-one-out (egyenként kihagyott vizsgálatok)
- Befolyás-diagnosztika (Viechtbauer & Cheung 2010): externálisan studentizált reziduum,
  DFFITS, Cook-távolság, kovariancia-arány, hat-érték, DFBETAS; jelölés a metafor
  influence.rma.uni szabályai szerint
- Kumulatív metaanalízis (pl. publikációs év szerint)
- Kiugró vizsgálatok szűrése (dmetar::find.outliers szabály: a vizsgálat CI-je teljesen az
  összesített CI-n kívül esik) és újraillesztés nélkülük
"""
import math

from . import distributions as dist
from .models import meta_analysis, MetaResult, ModelError


def leave_one_out(yi, vi, labels, model="random", tau2_method=None, ci_method=None,
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


def influence(yi, vi, labels, model="random", tau2_method=None, level=0.95):
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


def _natural_key(value):
    """Típusbiztos természetes rendezési kulcs vegyes szám/szöveg értékekhez: az egész
    számértékű float egészként íródik ('2019'), a számjegy-sorozatok számként hasonlítanak
    (így '2018-12' < 2019 < '2019-05' < '2019a' < 2020)."""
    import re
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    parts = re.split(r"(\d+)", str(value).strip().lower())
    return tuple(int(p) if i % 2 else p for i, p in enumerate(parts))


def cumulative_order(order_key):
    """A kumulatív elemzés sorrendje: a hiányzó (None/üres) kulcsú vizsgálatok a végére
    kerülnek (mint a metafor cumul()-ban az NA); ha minden kulcs szám, numerikus rendezés,
    vegyes szám/szöveg kulcsoknál típusbiztos természetes rendezés (nincs TypeError)."""
    n = len(order_key)
    missing = [i for i in range(n) if order_key[i] is None or
               (isinstance(order_key[i], str) and not order_key[i].strip()) or
               (isinstance(order_key[i], float) and math.isnan(order_key[i]))]
    miss = set(missing)
    present = [i for i in range(n) if i not in miss]
    if all(isinstance(order_key[i], (int, float)) and not isinstance(order_key[i], bool)
           for i in present):
        present.sort(key=lambda i: (order_key[i], i))
    else:
        present.sort(key=lambda i: (_natural_key(order_key[i]), i))
    return present + missing


def cumulative(yi, vi, labels, order_key, model="random", tau2_method=None, ci_method=None,
               level=0.95):
    """Kumulatív metaanalízis order_key szerint (hiányzó kulcs: a sor végére, mint a metafor
    cumul()-ban; vegyes típusú kulcsok: természetes rendezés)."""
    idx = cumulative_order(order_key)
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


def outlier_screen(yi, vi, labels=None, model="random", tau2_method=None, level=0.95, ci_method=None,
                   h_centre="truncated"):
    """Kiugró vizsgálatok szűrése a dmetar::find.outliers szabálya szerint, újraillesztéssel.

    Egy vizsgálat kiugró, ha a saját (1−α) CI-je (y_i ± z·√v_i) TELJESEN az összesített modell
    (1−α) CI-jén kívül esik: felső határa < az összesített alsó határ, vagy alsó határa > az
    összesített felső határ (Harrer et al. 2021, Doing Meta-Analysis with R, 5.4.1). A szűrés
    egylépéses (nem iterált), és a kiugró vizsgálatok nélkül ugyanazzal a modellel (model,
    tau2_method, ci_method, level) újraillesztünk.

    Megjegyzés: a dmetar a meta-objektumon a vizsgálati CI-ket a level szerinti z-kvantilissel,
    a metafor-objektumon fix 1,96-tal számolja; itt mindig a level szerinti pontos z-kvantilis
    (95%-nál 1,959964). Az összesített CI a megadott ci_method szerint (None → a meta_analysis
    alapértelmezése: random → HKSJ); a meta::metagen alapértelmezésének (klasszikus z-CI)
    megfelelő eredményhez ci_method='z' kell.

    Visszaad: MetaResult(kind='outliers', flagged (címkék), flagged_index, k, k_removed,
    full (teljes modell), refit (újraillesztett modell vagy None, ha nincs kiugró / nem maradt
    vizsgálat), study_ci (vizsgálatonkénti [alsó, felső]), rule, warnings).
    A kiugró vizsgálat nem feltétlenül befolyásos (influence()), és automatikus kizárása nem
    ajánlott — érzékenységi elemzésként jelentsd mindkét eredményt.
    """
    k = len(yi)
    labels = list(labels) if labels else ["#%d" % (i + 1) for i in range(k)]
    if len(labels) != k:
        raise ModelError("a címkék száma eltér a vizsgálatok számától")
    full = meta_analysis(yi, vi, model, tau2_method, ci_method, level, labels=labels, h_centre=h_centre)
    z = dist.norm_ppf(0.5 + level / 2.0)
    study_ci = [[y - z * math.sqrt(v), y + z * math.sqrt(v)] for y, v in zip(yi, vi)]
    flagged_index = [i for i, (lo, hi) in enumerate(study_ci)
                     if hi < full.ci_lower or lo > full.ci_upper]
    warnings = []
    refit = None
    if flagged_index:
        keep = [i for i in range(k) if i not in set(flagged_index)]
        if keep:
            refit = meta_analysis([yi[i] for i in keep], [vi[i] for i in keep], model, tau2_method,
                                  ci_method, level, labels=[labels[i] for i in keep], h_centre=h_centre)
        else:
            warnings.append("Minden vizsgálat kiugrónak minősült; újraillesztés nem lehetséges.")
    if k < 10:
        warnings.append("k = %d: kevés vizsgálatnál a kiugró-szűrés bizonytalan; a kiugró vizsgálat "
                        "nem feltétlenül befolyásos — vesd össze a befolyás-diagnosztikával." % k)
    return MetaResult(kind="outliers", rule="dmetar::find.outliers (vizsgálati CI teljesen az összesített "
                      "CI-n kívül)", k=k, k_removed=len(flagged_index),
                      flagged=[labels[i] for i in flagged_index], flagged_index=flagged_index,
                      study_ci=study_ci, full=full, refit=refit, model=full.model, level=level,
                      warnings=warnings)
