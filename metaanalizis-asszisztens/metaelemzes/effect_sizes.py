# -*- coding: utf-8 -*-
"""Hatásméretek és varianciáik vizsgálatonként (a metafor::escalc definícióit követve).

Mértékek (measure):
  Folytonos:  MD, SMD (Hedges g), COHEN_D (korrekció nélkül), ROM (log válaszhányados)
  Bináris:    OR (log), RR (log), RD
  Arány:      PR, PLN (log), PLO (logit), PAS (arcsin), PFT (Freeman–Tukey, metafor-féle fél-összeg)
  Korreláció: COR, ZCOR (Fisher z)
  Generikus:  GEN (yi + vi, vagy yi + sei)

Minden számítás a "log/transzformált" skálán történik; a visszatranszformálást a
`back_transform` végzi. Források: Borenstein et al. 2009, 4–6. fejezet;
Khan 2020, 3–10. fejezet; Viechtbauer 2010 (metafor).
"""
import math

CONTINUOUS = ("MD", "SMD", "COHEN_D", "ROM")
BINARY = ("OR", "RR", "RD")
PROPORTION = ("PR", "PLN", "PLO", "PAS", "PFT")
CORRELATION = ("COR", "ZCOR")
GENERIC = ("GEN",)
ALL_MEASURES = CONTINUOUS + BINARY + PROPORTION + CORRELATION + GENERIC

# A mértékhez szükséges oszlopok (a CSV-ben ezekkel a nevekkel)
REQUIRED_COLUMNS = {
    "MD": ("m1", "sd1", "n1", "m2", "sd2", "n2"),
    "SMD": ("m1", "sd1", "n1", "m2", "sd2", "n2"),
    "COHEN_D": ("m1", "sd1", "n1", "m2", "sd2", "n2"),
    "ROM": ("m1", "sd1", "n1", "m2", "sd2", "n2"),
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

# Mely mértékek ábrázolandók exponenciális (arány-) skálán
RATIO_MEASURES = ("OR", "RR", "ROM")

MEASURE_LABELS = {
    "MD": "Átlagkülönbség (MD)",
    "SMD": "Standardizált átlagkülönbség (Hedges g)",
    "COHEN_D": "Standardizált átlagkülönbség (Cohen d, korrekció nélkül)",
    "ROM": "Válaszhányados (ratio of means)",
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
        if vi is None or not (vi > 0) or math.isinf(vi) or math.isnan(vi) or math.isnan(yi):
            self.excluded.append((label, "nem pozitív vagy hiányzó variancia (vi=%r)" % (vi,)))
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
def md(m1, sd1, n1, m2, sd2, n2):
    """Nyers átlagkülönbség; variancia: sd1²/n1 + sd2²/n2 (Borenstein 4.5 / metafor)."""
    return m1 - m2, sd1 ** 2 / n1 + sd2 ** 2 / n2


def smd(m1, sd1, n1, m2, sd2, n2, correct=True, j_method="exact", vtype="LS"):
    """Standardizált átlagkülönbség.

    correct=True → Hedges g = J·d; különben Cohen d.
    vtype:
      LS  — metafor alapértelmezés: 1/n1 + 1/n2 + y²/(2(n1+n2))
      LS2 — Borenstein et al. 2009 (4.20, 4.24): J²·[(n1+n2)/(n1·n2) + d²/(2(n1+n2))]
      UB  — torzítatlan becslő: 1/n1 + 1/n2 + (1 - (m-2)/(m·J²))·y²
    """
    m = n1 + n2 - 2.0
    sp = pooled_sd(sd1, n1, sd2, n2)
    if not sp > 0:
        raise EffectSizeError("SMD: az összevont SD nem pozitív")
    d = (m1 - m2) / sp
    j = hedges_j(m, j_method) if correct else 1.0
    y = j * d
    n = n1 + n2
    if vtype == "LS":
        v = 1.0 / n1 + 1.0 / n2 + y ** 2 / (2.0 * n)
    elif vtype == "LS2":
        v = j ** 2 * ((n1 + n2) / (n1 * n2) + d ** 2 / (2.0 * n))
    elif vtype == "UB":
        jj = hedges_j(m, j_method)
        v = 1.0 / n1 + 1.0 / n2 + (1.0 - (m - 2.0) / (m * jj ** 2)) * y ** 2
    else:
        raise EffectSizeError("ismeretlen SMD vtype: %r" % vtype)
    return y, v


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


def back_transform(measure, value, n_harmonic=None):
    """A transzformált skáláról az értelmezési skálára (OR/RR/ROM → exp, PLO → expit ...)."""
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
        if n_harmonic is None:
            raise EffectSizeError("PFT visszatranszformáláshoz n (harmonikus átlag) kell")
        return pft_inverse(value, n_harmonic)
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
            cc=0.5, cc_to="only0", drop00=None, ci_level=0.95, skip_labels=None):
    """Sorok (dict-ek listája, már számmá alakítva) → EffectSizes.

    A hibás sorokat nem dobja el csendben: az `excluded` listába kerülnek indoklással.
    skip_labels: azoknak a vizsgálatoknak a címkéi, amelyek 'error' súlyosságú validálási
    tételt kaptak (validate.blocking_labels); ezek "validálási hiba" indokkal kimaradnak.
    Lehet szótár is (címke → a hibakódok szövege), ekkor az indoklás a kódokat is tartalmazza.
    Az es.row_index minden bevont vizsgálat eredeti sorindexét adja (a `rows` listában).
    """
    if measure not in ALL_MEASURES:
        raise EffectSizeError("ismeretlen mérték: %r (lehetséges: %s)" % (measure, ", ".join(ALL_MEASURES)))
    es = EffectSizes(measure)
    skip = skip_labels or ()
    for i, row in enumerate(rows):
        label = row_label(row, i, label_col)
        if label in skip:
            detail = skip.get(label) if isinstance(skip, dict) else None
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
                    y, v = md(m1, sd1, n1, m2, sd2, n2)
                elif measure == "SMD":
                    y, v = smd(m1, sd1, n1, m2, sd2, n2, True, j_method, smd_vtype)
                elif measure == "COHEN_D":
                    y, v = smd(m1, sd1, n1, m2, sd2, n2, False, j_method,
                               "LS2" if smd_vtype == "LS2" else "LS")
                else:
                    y, v = rom(m1, sd1, n1, m2, sd2, n2)
                es.add(label, y, v, n1 + n2, row, index=i)
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
                if v is None and row.get("sei") is not None:
                    v = row["sei"] ** 2
                if y is None or v is None:
                    raise EffectSizeError("GEN: yi és vi (vagy sei) kell")
                es.add(label, y, v, row.get("n"), row, index=i)
        except KeyError as exc:
            es.excluded.append((label, "hiányzó oszlop: %s" % exc))
        except (EffectSizeError, ZeroDivisionError, ValueError, TypeError) as exc:
            es.excluded.append((label, "%s: %s" % (type(exc).__name__, exc)))
    if es.excluded:
        es.warnings.append("%d sor kimaradt a hatásméret-számításból (lásd: excluded)." % len(es.excluded))
    return es


def study_ci(yi, vi, level=0.95):
    from .distributions import norm_ppf
    z = norm_ppf(0.5 + level / 2.0)
    se = math.sqrt(vi)
    return yi - z * se, yi + z * se
