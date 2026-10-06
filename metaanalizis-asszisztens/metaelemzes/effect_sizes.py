# -*- coding: utf-8 -*-
"""Hatásméretek és varianciáik vizsgálatonként (a metafor::escalc definícióit követve).

Mértékek (measure):
  Folytonos:  MD, SMD (Hedges g), COHEN_D (korrekció nélkül), ROM (log válaszhányados),
              SMD_GLASS (Glass-féle Δ: a kontrollcsoport SD-jével standardizálva)
  Párosított: MC (nyers átlagos változás), SMCC (standardizált átlagos változás, a változás
              SD-jével standardizálva) — egycsoportos előtte–utána / párosított elrendezés
  Bináris:    OR (log), RR (log), RD
  Arány:      PR, PLN (log), PLO (logit), PAS (arcsin), PFT (Freeman–Tukey, metafor-féle fél-összeg)
  Korreláció: COR, ZCOR (Fisher z)
  Generikus:  GEN (yi + vi, vagy yi + sei; SMD-nél yi + n1 + n2 is, gen_smd_vtype-pal)

Minden számítás a "log/transzformált" skálán történik; a visszatranszformálást a
`back_transform` végzi. Források: Borenstein et al. 2009, 4–6. fejezet;
Khan 2020, 3–10. fejezet; Viechtbauer 2010 (metafor); Stata metan (Harris et al. 2008).
"""
import math

CONTINUOUS = ("MD", "SMD", "COHEN_D", "ROM", "SMD_GLASS")
PAIRED = ("MC", "SMCC")
BINARY = ("OR", "RR", "RD")
PROPORTION = ("PR", "PLN", "PLO", "PAS", "PFT")
CORRELATION = ("COR", "ZCOR")
GENERIC = ("GEN",)
ALL_MEASURES = CONTINUOUS + PAIRED + BINARY + PROPORTION + CORRELATION + GENERIC

# A mértékhez szükséges oszlopok (a CSV-ben ezekkel a nevekkel). A párosított mértékeknél
# (MC, SMCC) csak az n kötelező mindig; az átlagos változás és a változás SD-je a
# PAIRED_INPUTS bármelyik teljes oszlopkészletéből jöhet.
REQUIRED_COLUMNS = {
    "MD": ("m1", "sd1", "n1", "m2", "sd2", "n2"),
    "SMD": ("m1", "sd1", "n1", "m2", "sd2", "n2"),
    "COHEN_D": ("m1", "sd1", "n1", "m2", "sd2", "n2"),
    "ROM": ("m1", "sd1", "n1", "m2", "sd2", "n2"),
    "SMD_GLASS": ("m1", "sd1", "n1", "m2", "sd2", "n2"),
    "MC": ("n",),
    "SMCC": ("n",),
    "OR": ("e1", "n1", "e2", "n2"),
    "RR": ("e1", "n1", "e2", "n2"),
    "RD": ("e1", "n1", "e2", "n2"),
    "PR": ("x", "n"),
    "PLN": ("x", "n"),
    "PLO": ("x", "n"),
    "PAS": ("x", "n"),
    "PFT": ("x", "n"),
    "COR": ("r", "n"),
    "ZCOR": ("r", "n"),
    "GEN": ("yi",),  # + vi vagy sei
}

# Párosított mértékek (MC, SMCC) bemenete: az átlagos változás ("mean") és a változás
# SD-je ("sd") egy-egy teljes oszlopkészletből, a felsorolás sorrendjében az első teljes nyer.
#   mean: m1 − m2 (előtte − utána, metafor-irány) | mdiff (közölt átlagos különbség) | sum_d / n
#   sd:   sd_diff | √(sd1² + sd2² − 2·r·sd1·sd2) (Cochrane 6.5.2.8) | √(sum_sq_dev_d / (n − 1))
PAIRED_INPUTS = {
    "mean": (("m1", "m2"), ("mdiff",), ("sum_d",)),
    "sd": (("sd_diff",), ("sd1", "sd2", "r"), ("sum_sq_dev_d",)),
}

# Varianciakonvenciók (a CLI/pipeline választási listáihoz)
SMD_VTYPES = ("LS", "LS2", "UB", "METAN_COHEN", "METAN_HEDGES")
SMCC_VTYPES = ("LS", "LS2")
GLASS_VTYPES = ("METAN", "LS", "LS2", "UB", "SMD1H")
MD_VTYPES = ("unequal", "pooled")
PFT_N_METHODS = ("harmonic", "variance")

# Mely mértékek ábrázolandók exponenciális (arány-) skálán
RATIO_MEASURES = ("OR", "RR", "ROM")

MEASURE_LABELS = {
    "MD": "Átlagkülönbség (MD)",
    "SMD": "Standardizált átlagkülönbség (Hedges g)",
    "COHEN_D": "Standardizált átlagkülönbség (Cohen d, korrekció nélkül)",
    "ROM": "Válaszhányados (ratio of means)",
    "SMD_GLASS": "Standardizált átlagkülönbség (Glass Δ, a kontroll SD-jével)",
    "MC": "Átlagos változás (párosított, nyers)",
    "SMCC": "Standardizált átlagos változás (a változás SD-jével)",
    "OR": "Esélyhányados (OR)",
    "RR": "Relatív kockázat (RR)",
    "RD": "Kockázatkülönbség (RD)",
    "PR": "Arány",
    "PLN": "Arány (log)",
    "PLO": "Arány (logit)",
    "PAS": "Arány (arcsin)",
    "PFT": "Arány (Freeman–Tukey)",
    "COR": "Korreláció (r)",
    "ZCOR": "Korreláció (Fisher z → r)",
    "GEN": "Generikus hatásméret",
}


class EffectSizeError(ValueError):
    pass


class EffectSizes(object):
    """Vizsgálatonkénti hatásméretek és a kihagyott sorok naplója."""

    def __init__(self, measure):
        self.measure = measure
        self.labels = []
        self.yi = []
        self.vi = []
        self.ni = []          # teljes mintanagyság (ha értelmezhető), a PFT-hez és a riporthoz
        self.rows = []        # az eredeti sor (dict), a moderátorokhoz
        self.row_index = []   # a bevont vizsgálat eredeti sorindexe (a compute-nak átadott listában)
        self.notes = []       # vizsgálatonkénti megjegyzések (pl. folytonossági korrekció)
        self.excluded = []    # (címke, ok)
        self.warnings = []    # globális figyelmeztetések

    def __len__(self):
        return len(self.yi)

    def add(self, label, yi, vi, ni=None, row=None, note="", index=None):
        if vi is None or not (vi > 0) or math.isinf(vi) or math.isnan(vi):
            self.excluded.append((label, "nem pozitív vagy hiányzó variancia (vi=%r)" % (vi,)))
            return
        if not math.isfinite(yi):
            self.excluded.append((label, "nem véges hatásméret (yi=%r)" % (yi,)))
            return
        self.row_index.append(index if index is not None else len(self.labels))
        self.labels.append(label)
        self.yi.append(yi)
        self.vi.append(vi)
        self.ni.append(ni)
        self.rows.append(row if row is not None else {})
        self.notes.append(note)


# ------------------------------------------------------------ segédfüggvények
def hedges_j(df, method="exact"):
    """Kis minta korrekciós tényező J(df). exact: gamma-függvényes; approx: 1 - 3/(4df - 1)."""
    if df <= 1:
        raise EffectSizeError("Hedges J: df > 1 szükséges (df=%r)" % df)
    if method == "approx":
        return 1.0 - 3.0 / (4.0 * df - 1.0)
    return math.exp(math.lgamma(df / 2.0) - 0.5 * math.log(df / 2.0) - math.lgamma((df - 1.0) / 2.0))


def pooled_sd(sd1, n1, sd2, n2):
    return math.sqrt(((n1 - 1.0) * sd1 ** 2 + (n2 - 1.0) * sd2 ** 2) / (n1 + n2 - 2.0))


# --------------------------------------------------------- egyedi mértékek
_MD_VTYPE_ALIASES = {"unequal": "unequal", "ls": "unequal", "ub": "unequal",
                     "pooled": "pooled", "ho": "pooled", "equal": "pooled"}


def md_vtype_name(vtype):
    """'unequal' | 'pooled' (a metafor-nevek is: LS/UB → unequal, HO → pooled; kis/nagybetű mindegy)."""
    key = str(vtype if vtype is not None else "unequal").strip().lower()
    if key not in _MD_VTYPE_ALIASES:
        raise EffectSizeError("ismeretlen MD vtype: %r (lehetséges: unequal/LS, pooled/HO)" % (vtype,))
    return _MD_VTYPE_ALIASES[key]


def md(m1, sd1, n1, m2, sd2, n2, vtype="unequal"):
    """Nyers átlagkülönbség m1 − m2.

    vtype:
      unequal (alapértelmezés; metafor vtype='LS'/'UB') — nem egyenlő varianciák:
              sd1²/n1 + sd2²/n2 (Borenstein 4.5; Khan 2020 Ex. 8.1)
      pooled  (metafor vtype='HO') — egyenlő varianciák, összevont SD-vel:
              sp²·(1/n1 + 1/n2), sp² = ((n1−1)sd1² + (n2−1)sd2²)/(n1+n2−2) (Borenstein 4.1–4.3)
    """
    if md_vtype_name(vtype) == "pooled":
        return m1 - m2, pooled_sd(sd1, n1, sd2, n2) ** 2 * (1.0 / n1 + 1.0 / n2)
    return m1 - m2, sd1 ** 2 / n1 + sd2 ** 2 / n2


def smd_variance_from_estimate(y, n1, n2, vtype="LS", j=None, j_method="exact", d=None):
    """Egy standardizált átlagkülönbség (y = Hedges g vagy Cohen d) mintavételi varianciája a
    karlétszámokból. N = n1 + n2, J = Hedges-féle korrekciós tényező (df = N − 2).

      LS           — metafor alapértelmezés: 1/n1 + 1/n2 + y²/(2N)
      LS2          — Borenstein 2009 (4.20, 4.24): J²·[N/(n1·n2) + d²/(2N)], d = y/J
      UB           — torzítatlan: 1/n1 + 1/n2 + (1 − (N−4)/((N−2)·J²))·y²
      METAN_COHEN  — Stata metan 'cohen' / MetaXL / Hedges 1985 (Khan 2020 Ex. 8.3):
                     N/(n1·n2) + y²/(2(N−2))
      METAN_HEDGES — Stata metan 'hedges' / MetaXL (Khan 2020 Ex. 8.12): N/(n1·n2) + y²/(2(N−3.94))

    j: a becslésnél ténylegesen használt J (LS2-nél d = y/J; Cohen d-nél 1.0); ha None, a
    j_method szerint számoljuk (LS2, UB). d: a korrekció előtti d, ha ismert (LS2)."""
    n = float(n1 + n2)
    if vtype == "LS":
        return 1.0 / n1 + 1.0 / n2 + y ** 2 / (2.0 * n)
    if vtype == "METAN_COHEN":
        if n <= 2:
            raise EffectSizeError("METAN_COHEN: n1 + n2 > 2 szükséges")
        return n / (n1 * n2) + y ** 2 / (2.0 * (n - 2.0))
    if vtype == "METAN_HEDGES":
        if n <= 3.94:
            raise EffectSizeError("METAN_HEDGES: n1 + n2 > 3,94 szükséges")
        return n / (n1 * n2) + y ** 2 / (2.0 * (n - 3.94))
    if vtype in ("LS2", "UB"):
        m = n - 2.0
        if j is None:
            j = hedges_j(m, j_method)
        if vtype == "LS2":
            if d is None:
                d = y / j
            return j ** 2 * (n / (n1 * n2) + d ** 2 / (2.0 * n))
        jj = hedges_j(m, j_method)
        return 1.0 / n1 + 1.0 / n2 + (1.0 - (m - 2.0) / (m * jj ** 2)) * y ** 2
    raise EffectSizeError("ismeretlen SMD vtype: %r (lehetséges: %s)" % (vtype, ", ".join(SMD_VTYPES)))


def smd(m1, sd1, n1, m2, sd2, n2, correct=True, j_method="exact", vtype="LS"):
    """Standardizált átlagkülönbség, az összevont (n−1) SD-vel: d = (m1 − m2)/sp.

    correct=True → Hedges g = J·d; különben Cohen d.
    vtype (lásd smd_variance_from_estimate):
      LS  — metafor alapértelmezés: 1/n1 + 1/n2 + y²/(2(n1+n2))
      LS2 — Borenstein et al. 2009 (4.20, 4.24): J²·[(n1+n2)/(n1·n2) + d²/(2(n1+n2))]
      UB  — torzítatlan becslő: 1/n1 + 1/n2 + (1 - (m-2)/(m·J²))·y²
      METAN_COHEN  — Stata metan / MetaXL Cohen: N/(n1·n2) + y²/(2(N−2))
      METAN_HEDGES — Stata metan / MetaXL Hedges: N/(n1·n2) + y²/(2(N−3.94)); itt a J MINDIG
                     a metan közelítő tényezője, 1 − 3/(4N − 9) (= j_method='approx'), a
                     j_method-tól függetlenül, hogy a konvenció teljes egészében egyezzen.
    """
    m = n1 + n2 - 2.0
    sp = pooled_sd(sd1, n1, sd2, n2)
    if not sp > 0:
        raise EffectSizeError("SMD: az összevont SD nem pozitív")
    d = (m1 - m2) / sp
    if correct:
        j = hedges_j(m, "approx" if vtype == "METAN_HEDGES" else j_method)
    else:
        j = 1.0
    y = j * d
    return y, smd_variance_from_estimate(y, n1, n2, vtype, j=j, j_method=j_method, d=d)


def glass_delta(m1, sd1, n1, m2, sd2, n2, vtype="METAN", correct=None, j_method="exact"):
    """Glass-féle Δ: (m1 − m2)/sd2, a 2. (kontroll-) csoport SD-jével standardizálva — akkor
    hasznos, ha a beavatkozás a szórást is megváltoztatja (Glass 1976; Borenstein 2009 4. fej.).

    vtype (a mintavételi variancia konvenciója):
      METAN (alapértelmezés) — Stata metan 'glass' / MetaXL / Khan 2020 Ex. 8.12 (Glass, McGaw &
              Smith 1981): korrekció nélküli Δ, v = N/(n1·n2) + Δ²/(2(n2 − 1)).
      LS    — metafor escalc(measure="SMD1"): y = J(n2−1)·Δ, v = 1/n1 + 1/n2 + y²/(2·n2)
              (egyenlő populációs SD-t feltételez).
      LS2   — metafor SMD1, vtype="LS2": J²·(1/n1 + 1/n2 + Δ²/(2·n2)).
      UB    — metafor SMD1, vtype="UB": 1/n1 + 1/n2 + (1 − (m−2)/(m·c²))·y², m = n2 − 1.
      SMD1H — metafor escalc(measure="SMD1H"), eltérő SD-k: v = (sd1²/sd2²)/n1 + 1/n2 + y²/(2·n2).
    correct: Hedges-féle kis minta korrekció J(n2 − 1); None → METAN-nál nincs (mint a metan),
    a metafor-konvencióknál van (mint az escalc alapértelmezése). A METAN és a metafor LS a
    variancia nevezőjében (2(n2 − 1) vs 2·n2) és a korrekcióban tér el; a Khan 2020 Fig. 8.4c–8.6c
    nyomtatott értékeit csak a METAN adja vissza. A metafor sd1-et csak az SMD1H-nál használja.
    """
    if vtype not in GLASS_VTYPES:
        raise EffectSizeError("ismeretlen Glass vtype: %r (lehetséges: %s)" % (vtype, ", ".join(GLASS_VTYPES)))
    if not sd2 > 0:
        raise EffectSizeError("Glass Δ: a kontrollcsoport SD-je (sd2) nem pozitív")
    if correct is None:
        correct = vtype != "METAN"
    mi = n2 - 1.0
    delta = (m1 - m2) / sd2
    c = hedges_j(mi, j_method) if correct else 1.0
    y = c * delta
    n = float(n1 + n2)
    if vtype == "METAN":
        if mi <= 0:
            raise EffectSizeError("Glass Δ (METAN): n2 > 1 szükséges")
        v = n / (n1 * n2) + y ** 2 / (2.0 * mi)
    elif vtype == "LS":
        v = 1.0 / n1 + 1.0 / n2 + y ** 2 / (2.0 * n2)
    elif vtype == "LS2":
        v = c ** 2 * (1.0 / n1 + 1.0 / n2 + delta ** 2 / (2.0 * n2))
    elif vtype == "UB":
        v = 1.0 / n1 + 1.0 / n2 + (1.0 - (mi - 2.0) / (mi * c ** 2)) * y ** 2
    else:  # SMD1H
        v = (sd1 ** 2 / sd2 ** 2) / n1 + 1.0 / n2 + y ** 2 / (2.0 * n2)
    return y, v


# ------------------------------------------------ párosított (egycsoportos) mértékek
def mean_change(mdiff, sd_diff, n):
    """Nyers átlagos változás (metafor escalc "MC"): y = átlagos különbség, v = sd_diff²/n
    (Khan 2020 Ex. 8.2: Var(D̄) = S_D²/n). Az irány a metaforé: m1 − m2 (pl. előtte − utána,
    így a pozitív érték csökkenést jelent); mdiff-nél a közölt előjel marad."""
    if n is None or n < 2:
        raise EffectSizeError("MC: n >= 2 szükséges")
    if sd_diff is None or not sd_diff > 0:
        raise EffectSizeError("MC: a változás SD-je nem pozitív")
    return float(mdiff), sd_diff ** 2 / float(n)


def smcc(mdiff, sd_diff, n, correct=True, j_method="exact", vtype="LS"):
    """Standardizált átlagos változás, a változás SD-jével standardizálva (metafor escalc
    "SMCC"; Gibbons et al. 1993; Becker 1988): d = mdiff/sd_diff, y = J(n−1)·d.
      LS  (alapértelmezés) — v = 1/n + y²/(2n)
      LS2                  — v = J²·(1/n + d²/(2n))
    A nyers pontszám SD-jével standardizált változatot (SMCR) a motor nem kínálja."""
    if vtype not in SMCC_VTYPES:
        raise EffectSizeError("SMCC: a vtype csak %s lehet (kapott: %r)" % (" vagy ".join(SMCC_VTYPES), vtype))
    if n is None or n < 2:
        raise EffectSizeError("SMCC: n >= 2 szükséges")
    if sd_diff is None or not sd_diff > 0:
        raise EffectSizeError("SMCC: a változás SD-je nem pozitív")
    n = float(n)
    d = float(mdiff) / sd_diff
    j = hedges_j(n - 1.0, j_method) if correct else 1.0
    y = j * d
    if vtype == "LS":
        return y, 1.0 / n + y ** 2 / (2.0 * n)
    return y, j ** 2 * (1.0 / n + d ** 2 / (2.0 * n))


def _present(row, cols):
    return all(row.get(c) is not None for c in cols)


def paired_inputs(row):
    """Egy párosított sor → (átlagos változás, a változás SD-je, n, forrás-megjegyzés).

    A PAIRED_INPUTS sorrendjében az első teljes oszlopkészlet nyer; ha nincs teljes készlet,
    EffectSizeError (a hiányzó oszlopok felsorolásával)."""
    n = row.get("n")
    if n is None:
        raise EffectSizeError("hiányzó érték: n")
    mean = sd = None
    how = []
    for cols in PAIRED_INPUTS["mean"]:
        if _present(row, cols):
            if cols == ("m1", "m2"):
                mean = row["m1"] - row["m2"]
            elif cols == ("mdiff",):
                mean = row["mdiff"]
            else:
                mean = row["sum_d"] / float(n)
                how.append("átlag = sum_d/n")
            break
    for cols in PAIRED_INPUTS["sd"]:
        if _present(row, cols):
            if cols == ("sd_diff",):
                sd = row["sd_diff"]
            elif cols == ("sd1", "sd2", "r"):
                sd1, sd2, r = row["sd1"], row["sd2"], row["r"]
                if not -1.0 <= r <= 1.0:
                    raise EffectSizeError("párosított: -1 <= r <= 1 szükséges (r=%r)" % r)
                if sd1 < 0 or sd2 < 0:
                    raise EffectSizeError("párosított: negatív SD")
                var = sd1 ** 2 + sd2 ** 2 - 2.0 * r * sd1 * sd2
                if not var > 0:
                    raise EffectSizeError("párosított: a változás varianciája nem pozitív (sd1, sd2, r)")
                sd = math.sqrt(var)
                how.append("változás SD-je sd1, sd2 és r = %g alapján" % r)
            else:
                if n < 2:
                    raise EffectSizeError("párosított: n >= 2 szükséges")
                if row["sum_sq_dev_d"] < 0:
                    raise EffectSizeError("párosított: negatív eltérés-négyzetösszeg")
                sd = math.sqrt(row["sum_sq_dev_d"] / (n - 1.0))
                how.append("változás SD-je sum_sq_dev_d/(n−1) alapján")
            break
    missing = []
    if mean is None:
        missing.append("átlagos változás (m1+m2, mdiff vagy sum_d)")
    if sd is None:
        missing.append("a változás SD-je (sd_diff, sd1+sd2+r vagy sum_sq_dev_d)")
    if missing:
        raise EffectSizeError("hiányzó érték: " + "; ".join(missing))
    return mean, sd, n, "; ".join(how)


def rom(m1, sd1, n1, m2, sd2, n2):
    if m1 <= 0 or m2 <= 0:
        raise EffectSizeError("ROM: az átlagoknak pozitívnak kell lenniük")
    return math.log(m1 / m2), sd1 ** 2 / (n1 * m1 ** 2) + sd2 ** 2 / (n2 * m2 ** 2)


def _cells(e1, n1, e2, n2):
    a, b, c, d = float(e1), float(n1 - e1), float(e2), float(n2 - e2)
    if min(a, b, c, d) < 0:
        raise EffectSizeError("negatív cellaérték (események > n?)")
    return a, b, c, d


def two_by_two(measure, e1, n1, e2, n2, cc=0.5, cc_to="only0", drop00=None):
    """OR / RR / RD egy 2×2 táblából. Visszaad: (yi, vi, megjegyzés) vagy None (kizárás).

    cc_to: 'only0' (csak ha van 0 cella), 'all', 'none'.
    drop00: kettős-nulla (és kettős-teljes) vizsgálatok kizárása; alapértelmezés OR/RR-nél
    igen (Cochrane-gyakorlat), RD-nél nem.
    """
    a, b, c, d = _cells(e1, n1, e2, n2)
    if drop00 is None:
        drop00 = measure in ("OR", "RR")
    double_zero = (a == 0 and c == 0) or (b == 0 and d == 0)
    if double_zero and drop00:
        return None
    note = ""
    has_zero = min(a, b, c, d) == 0
    add = 0.0
    if cc_to == "all" or (cc_to == "only0" and has_zero):
        add = cc
    if measure == "RD":
        # RD-nél a nulla cella nem akadálya a számításnak, ezért alapértelmezésben (only0) NEM
        # korrigálunk (Cochrane Handbook 10.4.4.1); a korrekció csak akkor kell, ha a variancia
        # egyébként 0 lenne (mindkét karban 0% vagy 100%). Kifejezett cc_to="all" kérésre a
        # metafor::escalc(to="all") szerint minden vizsgálat minden cellájához hozzáadjuk.
        if cc_to == "all" and cc > 0:
            a, b, c, d = a + cc, b + cc, c + cc, d + cc
            note = "folytonossági korrekció +%g minden cellához" % cc
        p1, p2 = a / (a + b), c / (c + d)
        y = p1 - p2
        v = p1 * (1 - p1) / (a + b) + p2 * (1 - p2) / (c + d)
        if v <= 0 and cc > 0 and cc_to != "none":
            aa, bb, cc_, dd = a + cc, b + cc, c + cc, d + cc
            q1, q2 = aa / (aa + bb), cc_ / (cc_ + dd)
            v = q1 * (1 - q1) / (aa + bb) + q2 * (1 - q2) / (cc_ + dd)
            note = "RD variancia %.2g folytonossági korrekcióval (0 variancia)" % cc
        return y, v, note
    if add > 0:
        a, b, c, d = a + add, b + add, c + add, d + add
        note = "folytonossági korrekció +%g minden cellához" % add
    if measure == "OR":
        if min(a, b, c, d) <= 0:
            raise EffectSizeError("OR: nulla cella korrekció nélkül")
        return math.log(a * d / (b * c)), 1 / a + 1 / b + 1 / c + 1 / d, note
    if measure == "RR":
        if a <= 0 or c <= 0:
            raise EffectSizeError("RR: nulla eseményszám korrekció nélkül")
        n1c, n2c = a + b, c + d
        return math.log((a / n1c) / (c / n2c)), 1 / a - 1 / n1c + 1 / c - 1 / n2c, note
    raise EffectSizeError("ismeretlen bináris mérték: %r" % measure)


def proportion(measure, x, n, cc=0.5, cc_to="only0"):
    x, n = float(x), float(n)
    if x < 0 or x > n or n <= 0:
        raise EffectSizeError("arány: 0 <= x <= n és n > 0 szükséges")
    note = ""
    if measure in ("PR", "PLN", "PLO"):
        if cc_to == "all" or (cc_to == "only0" and (x == 0 or x == n)):
            if cc > 0:
                x, n = x + cc, n + 2 * cc
                note = "folytonossági korrekció +%g" % cc
        p = x / n
        if measure == "PR":
            return p, p * (1 - p) / n, note
        if measure == "PLN":
            if x <= 0:
                raise EffectSizeError("PLN: x = 0 korrekció nélkül")
            return math.log(p), 1 / x - 1 / n, note
        if x <= 0 or x >= n:
            raise EffectSizeError("PLO: x = 0 vagy x = n korrekció nélkül")
        return math.log(p / (1 - p)), 1 / x + 1 / (n - x), note
    if measure == "PAS":
        return math.asin(math.sqrt(x / n)), 1 / (4 * n), note
    if measure == "PFT":
        y = 0.5 * (math.asin(math.sqrt(x / (n + 1))) + math.asin(math.sqrt((x + 1) / (n + 1))))
        return y, 1 / (4 * n + 2), note
    raise EffectSizeError("ismeretlen arány-mérték: %r" % measure)


def correlation(measure, r, n):
    if not -1 < r < 1:
        raise EffectSizeError("korreláció: -1 < r < 1 szükséges")
    if measure == "ZCOR":
        if n <= 3:
            raise EffectSizeError("ZCOR: n > 3 szükséges")
        return math.atanh(r), 1.0 / (n - 3.0), ""
    if n <= 3:
        # metafor::escalc: ni <= 3 esetén a mintavételi variancia nem becsülhető (NA)
        raise EffectSizeError("COR: n > 3 szükséges")
    return r, (1 - r * r) ** 2 / (n - 1.0), ""


# ------------------------------------------------------------ visszatranszf.
def pft_inverse(t, n):
    """Freeman–Tukey kettős arcsin visszatranszformálás (Miller 1978; metafor transf.ipft)."""
    if t <= pft_of_p(0.0, n):
        return 0.0
    if t >= pft_of_p(1.0, n):
        return 1.0
    s = math.sin(2 * t)
    if s == 0:
        return 0.0 if t < math.pi / 4 else 1.0
    inner = 1 - (s + (s - 1 / s) / n) ** 2
    if inner < 0:
        inner = 0.0
    c = math.cos(2 * t)
    sign = (c > 0) - (c < 0)
    return 0.5 * (1 - sign * math.sqrt(inner))


def pft_of_p(p, n):
    x = p * n
    return 0.5 * (math.asin(math.sqrt(x / (n + 1))) + math.asin(math.sqrt((x + 1) / (n + 1))))


def pft_backtransform_n(pft_n="harmonic", n_harmonic=None, se=None):
    """A Freeman–Tukey visszatranszformálás (Miller 1978) n-je (MetaXL-ben 'm').

    pft_n:
      'harmonic' (alapértelmezés) — a vizsgálati n-ek harmonikus átlaga (metafor transf.ipft.hm,
                  R meta); n_harmonic kell.
      'variance' — MetaXL / Barendregt et al. 2013 (J Epidemiol Community Health 67:974):
                  m = 1/Var(t) a MetaXL teljes-összeg skáláján (t = 2y, Var(t) = 4·se²), tehát a
                  motor fél-összeg skáláján m = 1/(4·se²); se a visszatranszformált összesített
                  becslés SE-je (a becslést és a CI két határát ugyanazzal az m-mel). Egyetlen
                  vizsgálatnál ez n + 0,5 (MetaXL-sor), az összesített közös hatású becslésnél
                  Σ(nᵢ + 0,5). Megjegyzés: a v = 1/(4n + 2) képlet invertálása, n = (1/se² − 2)/4,
                  ennél pontosan 0,5-del kisebb (egy vizsgálatnál nᵢ, mint a metafor
                  transf.ipft) — 2 tizedesjegyre a Khan 2020 Fig. 6.1–6.4 értékeit mindkettő
                  adja, a MetaXL-dokumentáció szerinti 1/Var(t) az irányadó.
      szám       — a megadott n (pl. egy vizsgálat saját n-je).
    """
    if isinstance(pft_n, (int, float)) and not isinstance(pft_n, bool):
        if not pft_n > 0:
            raise EffectSizeError("PFT visszatranszformálás: n > 0 szükséges (kapott: %r)" % (pft_n,))
        return float(pft_n)
    key = str(pft_n if pft_n is not None else "harmonic").strip().lower()
    if key in ("harmonic", "hm"):
        if n_harmonic is None:
            raise EffectSizeError("PFT visszatranszformáláshoz n (harmonikus átlag) kell")
        return n_harmonic
    if key in ("variance", "metaxl", "inverse_variance", "iv"):
        if se is None or not se > 0 or math.isinf(se):
            raise EffectSizeError("PFT visszatranszformálás (pft_n='variance'): a becslés pozitív SE-je kell")
        return 1.0 / (4.0 * se ** 2)
    raise EffectSizeError("ismeretlen pft_n: %r (lehetséges: harmonic, variance, vagy egy szám)" % (pft_n,))


def back_transform(measure, value, n_harmonic=None, pft_n="harmonic", se=None):
    """A transzformált skáláról az értelmezési skálára (OR/RR/ROM → exp, PLO → expit ...).

    PFT-nél az n a pft_n szerint (lásd pft_backtransform_n): 'harmonic' (alapértelmezés,
    n_harmonic kell), 'variance' (MetaXL: m = 1/(4·se²), se kell) vagy egy szám."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return value
    if measure in RATIO_MEASURES or measure == "PLN":
        try:
            return math.exp(value)
        except OverflowError:
            return math.inf
    if measure == "PLO":
        if value >= 0:
            return 1.0 / (1.0 + math.exp(-value))
        e = math.exp(value)
        return e / (1.0 + e)
    if measure == "PAS":
        v = min(max(value, 0.0), math.pi / 2)
        return math.sin(v) ** 2
    if measure == "PFT":
        return pft_inverse(value, pft_backtransform_n(pft_n, n_harmonic, se))
    if measure == "ZCOR":
        return math.tanh(value)
    return value


def harmonic_mean(values):
    vals = [float(v) for v in values if v]
    if not vals:
        return None
    return len(vals) / sum(1.0 / v for v in vals)


# --------------------------------------------------------------- tömeges
def row_label(row, i, label_col="study"):
    """A vizsgálat címkéje pontosan úgy, ahogy a CSV-ben áll ('0', '2005', '001' is);
    '#n' (1-től számozva) csak akkor, ha a cella tényleg üres."""
    v = row.get(label_col)
    if v is None or str(v).strip() == "":
        return "#%d" % (i + 1)
    return str(v).strip()


def compute(rows, measure, label_col="study", smd_vtype="LS", j_method="exact",
            cc=0.5, cc_to="only0", drop00=None, ci_level=0.95, skip_labels=None,
            md_vtype="unequal", glass_vtype="METAN", gen_smd_vtype=None, skip_rows=None):
    """Sorok (dict-ek listája, már számmá alakítva) → EffectSizes.

    A hibás sorokat nem dobja el csendben: az `excluded` listába kerülnek indoklással.
    skip_labels: azoknak a vizsgálatoknak a címkéi, amelyek 'error' súlyosságú validálási
    tételt kaptak (validate.blocking_labels); ezek "validálási hiba" indokkal kimaradnak.
    Lehet szótár is (címke → a hibakódok szövege), ekkor az indoklás a kódokat is tartalmazza.
    skip_rows: mint a skip_labels, de SORINDEX szerint (a `rows` listában; halmaz vagy
    sorindex → kódok szótár). Ismétlődő címkéknél (több karú vizsgálat) ez a helyes: csak a
    hibás sor marad ki, az azonos címkéjű érvényes sor nem.
    Az es.row_index minden bevont vizsgálat eredeti sorindexét adja (a `rows` listában).

    Varianciakonvenciók:
      smd_vtype     — SMD / COHEN_D (SMD_VTYPES) és SMCC (LS, LS2); COHEN_D-nél az UB és
                      SMCC-nél a nem támogatott érték LS-re vált, figyelmeztetéssel
      md_vtype      — MD: 'unequal' (alapértelmezés, metafor LS) | 'pooled' (metafor HO)
      glass_vtype   — SMD_GLASS: GLASS_VTYPES, alapértelmezés 'METAN' (Stata metan / MetaXL)
      gen_smd_vtype — GEN: ha egy sorban nincs vi/sei, de van n1 és n2, a yi-t közölt SMD-nek
                      tekintjük, és a varianciáját ezzel a konvencióval számoljuk (SMD_VTYPES;
                      alapértelmezés None = nincs ilyen pótlás)
    """
    if measure not in ALL_MEASURES:
        raise EffectSizeError("ismeretlen mérték: %r (lehetséges: %s)" % (measure, ", ".join(ALL_MEASURES)))
    es = EffectSizes(measure)
    md_vt = md_vtype_name(md_vtype)
    if measure == "SMD_GLASS" and glass_vtype not in GLASS_VTYPES:
        raise EffectSizeError("ismeretlen Glass vtype: %r (lehetséges: %s)" % (glass_vtype, ", ".join(GLASS_VTYPES)))
    if gen_smd_vtype is not None and gen_smd_vtype not in SMD_VTYPES:
        raise EffectSizeError("ismeretlen gen_smd_vtype: %r (lehetséges: %s)" % (gen_smd_vtype, ", ".join(SMD_VTYPES)))
    cohen_vt = smd_vtype
    if measure == "COHEN_D" and smd_vtype == "UB":
        # az UB a Hedges-korrekcióhoz tartozik; a korábbi viselkedés (LS) marad, de nem csendben
        cohen_vt = "LS"
        es.warnings.append("COHEN_D: az UB variancia a korrigált (Hedges g) becslőhöz tartozik; "
                           "LS-sel számoltunk.")
    if measure == "SMD" and smd_vtype == "METAN_COHEN":
        es.warnings.append("SMD: a METAN_COHEN variancia a korrekció nélküli Cohen d-hez tartozik; Hedges g-re "
                           "alkalmazva egyik Stata metan / MetaXL konvencióval sem egyezik (metan-egyezéshez: SMD + "
                           "METAN_HEDGES vagy COHEN_D + METAN_COHEN).")
    elif measure == "COHEN_D" and smd_vtype == "METAN_HEDGES":
        es.warnings.append("COHEN_D: a METAN_HEDGES variancia a Hedges g-hez tartozik; a korrekció nélküli Cohen "
                           "d-re alkalmazva egyik Stata metan / MetaXL konvencióval sem egyezik (metan-egyezéshez: "
                           "COHEN_D + METAN_COHEN vagy SMD + METAN_HEDGES).")
    smcc_vt = smd_vtype
    if measure == "SMCC" and smd_vtype not in SMCC_VTYPES:
        smcc_vt = "LS"
        es.warnings.append("SMCC: a(z) %s variancia nem értelmezett (csak LS vagy LS2); LS-sel számoltunk."
                           % smd_vtype)
    skip = skip_labels or ()
    skip_r = skip_rows or ()
    for i, row in enumerate(rows):
        label = row_label(row, i, label_col)
        if i in skip_r or label in skip:
            src, key = (skip_r, i) if i in skip_r else (skip, label)
            detail = src.get(key) if isinstance(src, dict) else None
            es.excluded.append((label, "validálási hiba: %s" % detail if detail else
                                "validálási hiba (lásd a validálási tételeket)"))
            continue
        try:
            if measure in CONTINUOUS:
                args = [row[c] for c in REQUIRED_COLUMNS[measure]]
                if any(a is None for a in args):
                    raise EffectSizeError("hiányzó érték")
                m1, sd1, n1, m2, sd2, n2 = args
                if n1 < 2 or n2 < 2:
                    raise EffectSizeError("n < 2 valamelyik karban")
                if sd1 <= 0 or sd2 <= 0:
                    raise EffectSizeError("SD <= 0")
                if measure == "MD":
                    y, v = md(m1, sd1, n1, m2, sd2, n2, md_vt)
                elif measure == "SMD":
                    y, v = smd(m1, sd1, n1, m2, sd2, n2, True, j_method, smd_vtype)
                elif measure == "COHEN_D":
                    y, v = smd(m1, sd1, n1, m2, sd2, n2, False, j_method, cohen_vt)
                elif measure == "SMD_GLASS":
                    y, v = glass_delta(m1, sd1, n1, m2, sd2, n2, glass_vtype, None, j_method)
                else:
                    y, v = rom(m1, sd1, n1, m2, sd2, n2)
                es.add(label, y, v, n1 + n2, row, index=i)
            elif measure in PAIRED:
                mean, sdd, n, how = paired_inputs(row)
                if measure == "MC":
                    y, v = mean_change(mean, sdd, n)
                else:
                    y, v = smcc(mean, sdd, n, True, j_method, smcc_vt)
                es.add(label, y, v, n, row, how, index=i)
            elif measure in BINARY:
                e1, n1, e2, n2 = [row[c] for c in REQUIRED_COLUMNS[measure]]
                if None in (e1, n1, e2, n2):
                    raise EffectSizeError("hiányzó érték")
                if e1 > n1 or e2 > n2:
                    raise EffectSizeError("eseményszám > n")
                res = two_by_two(measure, e1, n1, e2, n2, cc, cc_to, drop00)
                if res is None:
                    es.excluded.append((label, "kettős nulla (vagy kettős 100%%) esemény — %s-nél kizárva" % measure))
                    continue
                y, v, note = res
                es.add(label, y, v, n1 + n2, row, note, index=i)
            elif measure in PROPORTION:
                x, n = row["x"], row["n"]
                if x is None or n is None:
                    raise EffectSizeError("hiányzó érték")
                y, v, note = proportion(measure, x, n, cc, cc_to)
                es.add(label, y, v, n, row, note, index=i)
            elif measure in CORRELATION:
                r, n = row["r"], row["n"]
                if r is None or n is None:
                    raise EffectSizeError("hiányzó érték")
                y, v, note = correlation(measure, r, n)
                es.add(label, y, v, n, row, note, index=i)
            else:  # GEN
                y = row.get("yi")
                v = row.get("vi")
                note = ""
                ni = row.get("n")
                if v is None and row.get("sei") is not None:
                    if row["sei"] < 0:
                        # a négyzetre emelés elfedné (valószínű adatkinyerési hiba, pl. CI-határ)
                        raise EffectSizeError("GEN: sei = %g < 0 (negatív SE)" % row["sei"])
                    v = row["sei"] ** 2
                if ni is not None and (ni < 1 or ni != int(ni)):
                    # a résztvevőszám (GRADE) összegébe kerülne
                    raise EffectSizeError("GEN: n = %g (egész n >= 1 kell)" % ni)
                if (v is None and y is not None and gen_smd_vtype is not None
                        and row.get("n1") is not None and row.get("n2") is not None):
                    n1, n2 = row["n1"], row["n2"]
                    if n1 < 1 or n2 < 1:
                        raise EffectSizeError("GEN: n1, n2 >= 1 szükséges")
                    v = smd_variance_from_estimate(y, n1, n2, gen_smd_vtype, j_method=j_method)
                    note = "variancia a közölt SMD-ből és a karlétszámokból (%s)" % gen_smd_vtype
                    if ni is None:
                        ni = n1 + n2
                if y is None or v is None:
                    raise EffectSizeError("GEN: yi és vi (vagy sei) kell" +
                                          ("" if gen_smd_vtype is None else "; vagy n1 és n2"))
                es.add(label, y, v, ni, row, note, index=i)
        except KeyError as exc:
            es.excluded.append((label, "hiányzó oszlop: %s" % exc))
        except (EffectSizeError, ArithmeticError, ValueError, TypeError) as exc:
            # ArithmeticError: ZeroDivisionError és OverflowError (pl. sd = 1e200) — csak ez a sor marad ki
            es.excluded.append((label, "%s: %s" % (type(exc).__name__, exc)))
    if es.excluded:
        es.warnings.append("%d sor kimaradt a hatásméret-számításból (lásd: excluded)." % len(es.excluded))
    return es


def study_ci(yi, vi, level=0.95):
    from .distributions import norm_ppf
    z = norm_ppf(0.5 + level / 2.0)
    se = math.sqrt(vi)
    return yi - z * se, yi + z * se
