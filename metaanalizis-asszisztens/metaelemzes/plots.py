# -*- coding: utf-8 -*-
"""Forest és funnel plot SVG-ben (csak standard könyvtár), valamint a rajzolási
adatok JSON-ban, hogy külső ábrakészítő (pl. figure-forge) is újrarajzolhassa.

Konvenciók (Cochrane/PRISMA): négyzetméret ∝ súly, gyémánt = összesített becslés és CI,
vízszintes vonal a gyémánt alatt = predikciós intervallum, arány-mértékeknél log-skála.

Nyelv (E5): lang = 'hu' (alapértelmezés; a korábbi kimenettel bájtra azonos) vagy 'en'. Angol nyelven és
annotate=True esetén a számok mínuszjele U+2212. annotate=True: elnevezett rétegek (<g id="layer-…">) és
soronként <g id="study-<row_uid>" data-row data-y data-lo data-hi> (a figure-forge / a felület számára).
"""
import math
import re
from xml.sax.saxutils import escape as _escape

from .effect_sizes import RATIO_MEASURES, PROPORTION, back_transform, pft_of_p

FONT = "Helvetica, Arial, sans-serif"
INK = "#1a1a1a"
MUTED = "#6b6b6b"
GRID = "#d9d9d9"
ACCENT = "#1f4e79"
PI_COLOR = "#b03a2e"

MINUS = "\u2212"
LANGS = ("hu", "en")

# a kódba égetett feliratok nyelvenként (a 'hu' a korábbi, byte-azonos szöveg; a contour_legend és a
# doi_note már XML-escape-elt)
TEXTS = {
    "study": {"hu": "Vizsgálat", "en": "Study"},
    "weight": {"hu": "Súly", "en": "Weight"},
    "effect": {"hu": "Hatás", "en": "Effect"},
    "effect_size": {"hu": "Hatásméret", "en": "Effect size"},
    "se_axis": {"hu": "Standard hiba (elemzési skála)", "en": "Standard error (analysis scale)"},
    "contour_legend": {"hu": "Sávok (a nullhatás körül): p &gt; 0.10 sötét · 0.05–0.10 · 0.01–0.05 · p &lt; 0.01 fehér",
                       "en": "Bands (around the null effect): p &gt; 0.10 dark · 0.05–0.10 · 0.01–0.05 · "
                             "p &lt; 0.01 white"},
    "no_contour": {"hu": "Egycsoportos arány: nincs nullhatás, ezért nincsenek szignifikancia-kontúrok; az aszimmetria "
                         "arányoknál nehezen értelmezhető.",
                   "en": "Single-arm proportion: no null effect, hence no significance contours; asymmetry is hard "
                         "to interpret for proportions."},
    "doi_svg_title": {"hu": "Doi-plot, LFK-index = %s (%s)", "en": "Doi plot, LFK index = %s (%s)"},
    "abs_z": {"hu": "|Z-pontszám| (0 felül)", "en": "|Z-score| (0 at top)"},
    "lfk": {"hu": "LFK-index: %s (%s)", "en": "LFK index: %s (%s)"},
    "doi_note": {"hu": ("Heurisztikus mutató (|LFK| ≤ 1 nincs, 1–2 kisebb, &gt; 2 jelentős aszimmetria) — "
                        "érzékenységi jellegű,",
                        "nem szignifikancia-teszt; a funnel plottal és az Egger/Harbord/Peters-teszttel "
                        "együtt értelmezd."),
                 "en": ("Heuristic index (|LFK| ≤ 1 none, 1–2 minor, &gt; 2 major asymmetry) — a sensitivity "
                        "measure,",
                        "not a significance test; interpret it together with the funnel plot and the "
                        "Egger/Harbord/Peters tests.")},
}
# a bias.doi_plot_data kategóriái angolul
LFK_CATEGORY_EN = {"nincs aszimmetria": "no asymmetry", "kisebb aszimmetria": "minor asymmetry",
                   "jelentős aszimmetria": "major asymmetry"}
_SCALE_SUFFIX = {
    "hu": {"log": "%s (log-skálán ábrázolva)", "PLO": "%s (logit-skálán ábrázolva)", "PAS": "%s (arcsin-skálán ábrázolva)",
           "PFT": "%s (Freeman–Tukey skálán ábrázolva)", "ZCOR": "%s (Fisher z-skálán ábrázolva)"},
    "en": {"log": "%s (log scale)", "PLO": "%s (logit scale)", "PAS": "%s (arcsine scale)",
           "PFT": "%s (Freeman–Tukey scale)", "ZCOR": "%s (Fisher z scale)"},
}


def check_lang(lang):
    if lang not in LANGS:
        raise ValueError("lang: érvénytelen nyelv %r (lehetséges: %s)" % (lang, ", ".join(LANGS)))
    return lang


def tr(key, lang="hu"):
    """Kódba égetett felirat a kért nyelven."""
    return TEXTS[key][check_lang(lang)]


def lfk_category(category, lang="hu"):
    if lang == "en":
        return LFK_CATEGORY_EN.get(category, category)
    return category


def minus_for(lang="hu", annotate=False):
    """A számok mínuszjele: U+2212 angolul és annotált SVG-ben, egyébként a korábbi '-'."""
    return MINUS if (lang == "en" or annotate) else "-"


def num_text(s, minus="-"):
    """Formázott szám(ok) szövegében a kötőjel-mínusz cseréje (csak számszövegre hívható)."""
    return s if minus == "-" else s.replace("-", minus)


def attr_num(v):
    """Gépi olvasásra szánt data-* érték: a float teljes pontosságú alakja (mint a JSON-ban); nem véges → ''."""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return ""
    return repr(float(v))


# az XML 1.0-ban tiltott vezérlőkarakterek (pl. Word-sortörés \x0b, PDF-ből másolt \x0c)
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def escape(s):
    """XML-escape (&, <, >) + a tiltott vezérlőkarakterek szóközre cserélése: az SVG mindig jól formált."""
    return _escape(_XML_ILLEGAL.sub(" ", str(s)))


# az ábrázolási (elemzési) skála ≠ az értelmezési skála: a tengelyfeliratok visszatranszformáltak
TRANSFORMED_MEASURES = ("PLN", "PLO", "PAS", "PFT", "ZCOR")


def decimals(values, minimum=2, maximum=6):
    """Közös tizedesjegy-szám egy becslés/CI hármashoz: a legnagyobb abszolút érték
    nagyságrendjéhez igazítva legalább 2 értékes jegy (RD 0.0033 → 4 tizedes), de
    legalább `minimum` (0.1 fölött 2 tizedes, mint eddig)."""
    vals = [abs(v) for v in values if isinstance(v, (int, float)) and v == v and not math.isinf(v) and v != 0]
    if not vals:
        return minimum
    m = max(vals)
    return int(min(maximum, max(minimum, 1 - math.floor(math.log10(m)))))


def _fmt(v, nd=2):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "–"
    s = ("%." + str(nd) + "f") % v
    if s.startswith("-") and float(s) == 0:      # nincs "-0.00"
        s = s[1:]
    return s


def fmt_triple(est, lo, hi, minus="-"):
    """'becslés [alsó; felső]' közös, nagyságrendhez igazított tizedesjeggyel (minus: '-' vagy U+2212)."""
    nd = decimals((est, lo, hi))
    return num_text("%s [%s; %s]" % (_fmt(est, nd), _fmt(lo, nd), _fmt(hi, nd)), minus)


def fmt_pair(lo, hi, minus="-"):
    """'[alsó; felső]' közös tizedesjeggyel (a PI szövege az ábrán: 'PI ' + ez)."""
    nd = decimals((lo, hi))
    return num_text("[%s; %s]" % (_fmt(lo, nd), _fmt(hi, nd)), minus)


def display_text(est, lo, hi):
    """A motor kész szövege mindkét nyelven: {'hu': az ábra és a riport mai szövege, 'en': U+2212 mínusszal}."""
    return {"hu": fmt_triple(est, lo, hi), "en": fmt_triple(est, lo, hi, MINUS)}


def _nice_ticks(lo, hi, n=5):
    """Kerek beosztás [lo, hi]-ban: a legkisebb, legalább span/n szép lépés; ha így kevesebb mint
    min(4, n) tick jutna, a következő kisebb szép lépés. A tickek i·lépés alakúak (nincs halmozódó
    lebegőpontos hiba, nincs '-0')."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    steps = [m * mag for m in (1, 2, 2.5, 5, 10)]

    def make(step):
        out = []
        for i in range(int(math.ceil(lo / step - 1e-9)), int(math.floor(hi / step + 1e-9)) + 1):
            t = float("%.12g" % (i * step))
            out.append(0.0 if t == 0 else t)
        return out

    cand = [st for st in steps if st >= raw * (1 - 1e-9)]
    step = cand[0] if cand else 10 * mag
    ticks = make(step)
    smaller = [st for st in steps if st < step]
    while len(ticks) < min(4, n) and smaller:
        ticks = make(smaller.pop())
    return ticks


def _ratio_ticks(lo, hi):
    cands = [0.001, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 1000]
    ticks = [c for c in cands if lo <= math.log(c) <= hi]
    if len(ticks) > 7:
        ticks = [c for c in ticks if c in (0.01, 0.1, 0.5, 1, 2, 10, 100)]
    if len(ticks) < 3:
        # szűk tartomány (pl. RR 0.85–1.15): finomabb, visszatranszformált beosztás
        fine = [0.25, 0.33, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1, 1.05, 1.1, 1.2,
                1.25, 1.33, 1.5, 1.75, 2, 2.5, 3, 4]
        ft = [c for c in fine if lo <= math.log(c) <= hi]
        while len(ft) > 7:
            ft = ft[::2] if 1 in ft[::2] else ft[1::2]
        if len(ft) > len(ticks):
            ticks = ft
        if len(ticks) < 3:
            nice = [t for t in _nice_ticks(math.exp(lo), math.exp(hi), 4) if t > 0]
            if len(nice) > len(ticks):
                ticks = nice
    return [math.log(t) for t in ticks], ticks


def forward_transform(measure, v, n_harmonic=None):
    """Az értelmezési skáláról (arány, r, OR …) az ábrázolási skálára; None, ha nem értelmezett."""
    try:
        if measure in RATIO_MEASURES or measure == "PLN":
            return math.log(v) if v > 0 else None
        if measure == "PLO":
            return math.log(v / (1 - v)) if 0 < v < 1 else None
        if measure == "PAS":
            return math.asin(math.sqrt(v)) if 0 <= v <= 1 else None
        if measure == "PFT":
            return pft_of_p(v, n_harmonic) if (n_harmonic and 0 <= v <= 1) else None
        if measure == "ZCOR":
            return math.atanh(v) if -1 < v < 1 else None
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return v


def axis_label(measure, label=None, lang="hu"):
    """A tengely címe, a skála megnevezésével."""
    lab = label or tr("effect_size", lang)
    suffix = _SCALE_SUFFIX[check_lang(lang)]
    if measure in RATIO_MEASURES or measure == "PLN":
        return suffix["log"] % lab
    return suffix.get(measure, "%s") % lab


# a tartomány széléhez közeli tick-jelöltek (ZCOR: r; arány-mértékek: p), ha a kerek beosztás túl kevés tickhez vezet
_EDGE_TICKS_R = (-0.999, -0.99, -0.95, -0.9, -0.8, 0.8, 0.9, 0.95, 0.99, 0.999)
_EDGE_TICKS_P = (0.001, 0.01, 0.05, 0.1, 0.9, 0.95, 0.99, 0.999)


class _Axis(object):
    """Vízszintes tengely az elemzési skálán; a tickek felirata az értelmezési skálán
    (arány-mértékeknél exp, arányoknál/korrelációnál a visszatranszformált érték)."""

    def __init__(self, lo, hi, x0, x1, ratio=False, measure=None, n_harmonic=None):
        pad = (hi - lo) * 0.05 or 0.5
        self.lo, self.hi = lo - pad, hi + pad
        self.x0, self.x1 = x0, x1
        self.measure = measure
        self.n_harmonic = n_harmonic
        self.ratio = ratio or (measure in RATIO_MEASURES)

    def x(self, v):
        v = min(max(v, self.lo), self.hi)
        return self.x0 + (v - self.lo) / (self.hi - self.lo) * (self.x1 - self.x0)

    def ticks(self, min_px=30):
        transformed = self.measure in TRANSFORMED_MEASURES and not (self.measure == "PFT" and not self.n_harmonic)
        if self.ratio:
            pos, labels = _ratio_ticks(self.lo, self.hi)
            ticks = list(zip(pos, [("%g" % t) for t in labels]))
        elif transformed:
            ticks = self._transformed_ticks()
        else:
            ticks = [(t, "%g" % t) for t in _nice_ticks(self.lo, self.hi)]
        out = self._spaced(ticks, min_px)
        if transformed and len(out) < 3:
            # széles tartomány (pl. ZCOR, kis k-jú PI): a kerek r/arány-tickek a null mellé szorulnak — a
            # tartomány széléhez közeli értékek (0.9, 0.99 …) is jelöltek (a középhez közelebbiek előbb), ha
            # minden megtartott ticktől legalább min_px-re vannak
            mid = 0.0 if self.measure == "ZCOR" else 0.5
            for pos, lab in sorted(self._transformed_ticks(edges=True), key=lambda t: abs(float(t[1]) - mid)):
                if all(abs(self.x(pos) - self.x(q)) >= min_px for q, _ in out):
                    out.append((pos, lab))
            out = sorted(out)
        return out

    def _spaced(self, ticks, min_px):
        # egymásra csúszó feliratok elhagyása; a nullhatás tickje (pl. OR = 1) mindig megmarad, a
        # többi a tőle mért távolság sorrendjében, ha minden megtartottól legalább min_px-re van
        null = default_null(self.measure)
        anchor = [t for t in ticks if null is not None and abs(t[0] - null) < 1e-12]
        if not anchor:
            out = []
            for pos, lab in ticks:
                if out and abs(self.x(pos) - self.x(out[-1][0])) < min_px:
                    continue
                out.append((pos, lab))
            return out
        out = anchor[:1]
        for pos, lab in sorted((t for t in ticks if t is not anchor[0]), key=lambda t: abs(t[0] - null)):
            if all(abs(self.x(pos) - self.x(q)) >= min_px for q, _ in out):
                out.append((pos, lab))
        return sorted(out)

    def _transformed_ticks(self, edges=False):
        m, nh = self.measure, self.n_harmonic
        a, b = back_transform(m, self.lo, nh), back_transform(m, self.hi, nh)
        dom = (-1.0, 1.0) if m == "ZCOR" else (0.0, 1.0)
        a, b = max(min(a, b), dom[0]), min(max(a, b), dom[1])
        if edges:
            cand = _EDGE_TICKS_R if m == "ZCOR" else _EDGE_TICKS_P
            out = []
            for t in cand:
                pos = forward_transform(m, t, nh) if a <= t <= b else None
                if pos is not None and self.lo - 1e-12 <= pos <= self.hi + 1e-12:
                    out.append((pos, "%g" % t))
            return out
        out = []
        for n in (5, 6, 8):
            cand = []
            for t in _nice_ticks(a, b, n):
                pos = forward_transform(m, t, nh)
                if pos is not None and self.lo - 1e-12 <= pos <= self.hi + 1e-12:
                    cand.append((pos, "%g" % t))
            out = cand
            if len(out) >= 3:
                break
        return out


def forest_data(measure, labels, yi, vi, weights_pct, summaries, level=0.95, n_harmonic=None,
                sections=None):
    """A forest plot adatai (ábrázolási skálán + visszatranszformált értékekkel).

    summaries: [{"label", "estimate", "ci_lower", "ci_upper", "pi_lower", "pi_upper"}]
    sections: opcionális [{"title", "indices", "summary"}] alcsoportos ábrához; az
    "indices" a `studies` lista pozíciói (nem címke alapján keresve: ismétlődő címke is jó).
    n_harmonic: PFT-nél a vizsgálatonkénti n listája (a vizsgálat saját visszatranszformálásához)
    vagy egyetlen szám; a tengelyfeliratokhoz a harmonikus átlag kerül az "axis_n" mezőbe.
    """
    from .distributions import norm_ppf
    z = norm_ppf(0.5 + level / 2)
    studies = []
    for i, (lab, y, v) in enumerate(zip(labels, yi, vi)):
        se = math.sqrt(v)
        lo, hi = y - z * se, y + z * se
        studies.append({
            "label": lab, "y": y, "lo": lo, "hi": hi,
            "weight_pct": (weights_pct[i] if weights_pct else None),
            "display": [back_transform(measure, y, n_harmonic and _single_n(n_harmonic, i)),
                        back_transform(measure, lo, n_harmonic and _single_n(n_harmonic, i)),
                        back_transform(measure, hi, n_harmonic and _single_n(n_harmonic, i))],
        })
    axis_n = None
    if n_harmonic:
        if isinstance(n_harmonic, (list, tuple)):
            vals = [float(v) for v in n_harmonic if v]
            axis_n = len(vals) / sum(1.0 / v for v in vals) if vals else None
        else:
            axis_n = float(n_harmonic)
    if measure == "PFT" and axis_n:
        # egy n az egész ábrán: a jelölő, a CI-vonal és a gyémánt a saját feliratának (saját n-nel vagy
        # m = 1/Var(t)-vel visszatranszformált értékének) tengelyhelyén, a tengely n-jével (axis_n);
        # az eredeti FT-skálás értékek az 'analysis' mezőben
        for s in studies:
            _place_pft(s, ("y", "lo", "hi"), s["display"], axis_n)
        for sm in list(summaries or []) + [sec["summary"] for sec in sections or [] if sec.get("summary")]:
            if sm.get("display"):
                _place_pft(sm, ("estimate", "ci_lower", "ci_upper"), sm["display"], axis_n)
            if sm.get("pi_display") and sm.get("pi_lower") is not None:
                _place_pft(sm, ("pi_lower", "pi_upper"), sm["pi_display"], axis_n, "analysis_pi")
    return {"measure": measure, "ratio_scale": measure in RATIO_MEASURES, "level": level,
            "axis_n": axis_n, "studies": studies, "summaries": summaries, "sections": sections}


def _place_pft(d, keys, display, axis_n, store="analysis"):
    d[store] = [d.get(k) for k in keys]
    for k, v in zip(keys, display):
        pos = forward_transform("PFT", v, axis_n) if v is not None else None
        if pos is not None:
            d[k] = pos


def _single_n(n_info, i):
    if isinstance(n_info, (list, tuple)):
        return n_info[i]
    return n_info


def default_null(measure):
    """A nullhatás az ábrázolási skálán; arány-mértékeknél (egycsoportos arány) nincs."""
    if measure in PROPORTION:
        return None
    return 0.0          # log(1) = 0 (OR/RR/ROM), atanh(0) = 0 (ZCOR), 0 (MD/SMD/RD/COR/GEN)


# a forest plot vízszintes elrendezése (az SVG és a plot_data v2 tengelye ugyanebből számol)
FOREST_X0, FOREST_X1 = 300, 640
FOREST_LAYERS = ("title", "header", "null", "studies", "subgroups", "summaries", "axis", "labels", "footer")
FUNNEL_LAYERS = ("title", "frame", "contours", "pseudo-ci", "studies", "filled", "axis", "legend")
DOI_LAYERS = ("title", "frame", "curve", "studies", "axis", "legend")


def forest_axis(data, null_value=None):
    """A forest-tengely és a nullvonal helye az elemzési skálán: (tengely, null). Ugyanez rajzolja az SVG-t
    és adja a plot_data v2 axis-át (tartomány, tickek), így a kettő nem térhet el."""
    studies = data["studies"]
    summaries = data.get("summaries") or []
    measure = data.get("measure")
    sec_sums = [sec["summary"] for sec in (data.get("sections") or []) if sec.get("summary")]
    # tengelytartomány: CI-k és összesítések (az alcsoport-összesítéseké is)
    vals = []
    for s in studies:
        vals += [s["lo"], s["hi"]]
    for sm in summaries + sec_sums:
        vals += [v for v in (sm.get("ci_lower"), sm.get("ci_upper"), sm.get("pi_lower"), sm.get("pi_upper"))
                 if v is not None and math.isfinite(v)]
    null = default_null(measure) if null_value is None else null_value
    if null is not None:
        vals.append(null)
    lo, hi = min(vals), max(vals)
    # extrém CI-k ne nyomják össze az ábrát (a levágott vonalak végén nyíl jelzi a folytatást)
    core = sorted(vals)
    if len(core) > 6:
        q_lo, q_hi = core[1], core[-2]
        span = q_hi - q_lo
        lo, hi = max(lo, q_lo - span), min(hi, q_hi + span)
    # a nullvonal és minden összesített pontbecslés a tengelyen belül maradjon
    must = [sm["estimate"] for sm in summaries + sec_sums
            if sm.get("estimate") is not None and math.isfinite(sm["estimate"])]
    if null is not None:
        must.append(null)
    lo, hi = min([lo] + must), max([hi] + must)
    return _Axis(lo, hi, FOREST_X0, FOREST_X1, data["ratio_scale"], measure, data.get("axis_n")), null


def _layered(items, plot, order, annotate):
    """[(réteg, sor)] → SVG-sorok; annotate=True: rétegenként <g id="layer-<ábra>-<réteg>">, a rétegek
    rögzített sorrendjében (a rétegen belül a rajzolási sorrend marad)."""
    if not annotate:
        return [line for _, line in items]
    out = []
    for layer in order:
        lines = [line for ly, line in items if ly == layer]
        if lines:
            out += ['<g id="layer-%s-%s">' % (plot, layer)] + lines + ["</g>"]
    return out


def _row_id(row_ids, i):
    if row_ids is None or i is None or not 0 <= i < len(row_ids) or row_ids[i] is None:
        return None, None
    uid, rix = row_ids[i]
    return uid, rix


def forest_svg(data, title=None, left_label=None, right_label=None, null_value=None,
               effect_label=None, footer=None, axis_title=None, lang="hu", annotate=False, row_ids=None):
    """null_value: a referenciavonal az ábrázolási skálán; None → a mérték nullhatása
    (default_null); arány-mértékeknél nincs vonal (nincs értelmes nullhatás).

    lang: 'hu' | 'en' (a kódba égetett feliratok; a többi szöveget a hívó adja a kért nyelven).
    annotate: elnevezett rétegek és soronként <g id="study-<row_uid>" data-row data-y data-lo data-hi>;
    row_ids: a studies sorrendjében [(row_uid, row_index)] (annotate-hez)."""
    check_lang(lang)
    minus = minus_for(lang, annotate)
    studies = data["studies"]
    summaries = data.get("summaries") or []
    sections = data.get("sections")
    row_h = 22
    width = 940
    label_x, plot_x0, plot_x1, est_x, w_x = 12, FOREST_X0, FOREST_X1, 660, 925
    axis, null = forest_axis(data, null_value)
    top = 40 if title else 16
    header_y = top + 14
    body_top = header_y + 14
    items = []
    if title:
        items.append(("title", '<text x="%d" y="22" font-size="15" font-weight="bold" fill="%s">%s</text>'
                      % (label_x, INK, escape(title))))
    eff_lab = effect_label or ("%s [%d%% CI]" % (tr("effect", lang), int(round(data["level"] * 100))))
    items.append(("header", '<text x="%d" y="%d" font-weight="bold" fill="%s">%s</text>'
                  % (label_x, header_y, INK, escape(tr("study", lang)))))
    items.append(("header", '<text x="%d" y="%d" font-weight="bold" fill="%s">%s</text>'
                  % (est_x, header_y, INK, escape(eff_lab))))
    items.append(("header", '<text x="%d" y="%d" font-weight="bold" fill="%s" text-anchor="end">%s</text>'
                  % (w_x, header_y, INK, escape(tr("weight", lang)))))
    items.append(("header", '<line x1="%d" x2="%d" y1="%d" y2="%d" stroke="%s"/>'
                  % (label_x, w_x, header_y + 6, header_y + 6, INK)))
    y = body_top + row_h * 0.6
    maxw = max([s["weight_pct"] or 0 for s in studies] + [1e-9])

    def study_row(i, s, y):
        parts = []
        parts.append('<text x="%d" y="%.1f" fill="%s">%s</text>' % (label_x, y + 4, INK, escape(_clip(s["label"], 40))))
        x_lo, x_hi = axis.x(s["lo"]), axis.x(s["hi"])
        parts.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="%s" stroke-width="1.2"/>' % (x_lo, x_hi, y, y, INK))
        if s["lo"] < axis.lo:
            parts.append(_arrow(axis.x0, y, -1))
        if s["hi"] > axis.hi:
            parts.append(_arrow(axis.x1, y, 1))
        if axis.lo <= s["y"] <= axis.hi:
            wv = s["weight_pct"] if s["weight_pct"] else maxw * 0.3
            side = 4 + 12 * math.sqrt(wv / maxw)
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                         % (axis.x(s["y"]) - side / 2, y - side / 2, side, side, ACCENT))
        d = s["display"]
        parts.append('<text x="%d" y="%.1f" fill="%s">%s</text>'
                     % (est_x, y + 4, INK, fmt_triple(d[0], d[1], d[2], minus)))
        if s["weight_pct"] is not None:
            parts.append('<text x="%d" y="%.1f" fill="%s" text-anchor="end">%.1f%%</text>'
                         % (w_x, y + 4, MUTED, s["weight_pct"]))
        if annotate:
            uid, rix = _row_id(row_ids, i)
            parts = ['<g id="study-%s" data-row="%s" data-y="%s" data-lo="%s" data-hi="%s">' % (
                escape(uid if uid is not None else "i%d" % i), "" if rix is None else int(rix),
                attr_num(s["y"]), attr_num(s["lo"]), attr_num(s["hi"]))] + parts + ["</g>"]
        return [("studies", p) for p in parts]

    def summary_row(sm, y, bold=True):
        layer = "summaries" if bold else "subgroups"
        parts = []
        est, l, h = sm["estimate"], sm["ci_lower"], sm["ci_upper"]
        xl, xe, xh = axis.x(l), axis.x(est), axis.x(h)
        parts.append('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s" stroke="%s"/>'
                     % (xl, y, xe, y - 7, xh, y, xe, y + 7, INK if bold else "#ffffff", INK))
        # a tengelyen túlnyúló gyémánt / PI: nyíl a levágott végen (mint a vizsgálatoknál)
        if l < axis.lo:
            parts.append(_arrow(axis.x0, y, -1))
        if h > axis.hi:
            parts.append(_arrow(axis.x1, y, 1))
        if sm.get("pi_lower") is not None:
            yp = y + 11
            parts.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="%s" stroke-width="2"/>'
                         % (axis.x(sm["pi_lower"]), axis.x(sm["pi_upper"]), yp, yp, PI_COLOR))
            if sm["pi_lower"] < axis.lo:
                parts.append(_arrow(axis.x0, yp, -1, PI_COLOR))
            if sm["pi_upper"] > axis.hi:
                parts.append(_arrow(axis.x1, yp, 1, PI_COLOR))
        parts.append('<text x="%d" y="%.1f" font-weight="%s" fill="%s">%s</text>'
                     % (label_x, y + 4, "bold" if bold else "normal", INK, escape(sm["label"])))
        d = sm.get("display") or [est, l, h]
        parts.append('<text x="%d" y="%.1f" font-weight="%s" fill="%s">%s</text>'
                     % (est_x, y + 4, "bold" if bold else "normal", INK, fmt_triple(d[0], d[1], d[2], minus)))
        if sm.get("pi_lower") is not None:
            pd = sm.get("pi_display") or [sm["pi_lower"], sm["pi_upper"]]
            parts.append('<text x="%d" y="%.1f" fill="%s" font-size="11">PI %s</text>'
                         % (est_x, y + 19, PI_COLOR, fmt_pair(pd[0], pd[1], minus)))
        if sm.get("note"):
            parts.append('<text x="%d" y="%.1f" fill="%s" font-size="11">%s</text>'
                         % (label_x, y + 19, MUTED, escape(sm["note"])))
        return [(layer, p) for p in parts]

    body = []
    if sections:
        for sec in sections:
            body.append(("subgroups", '<text x="%d" y="%.1f" font-weight="bold" font-style="italic" fill="%s">%s</text>'
                         % (label_x, y + 4, INK, escape(sec["title"]))))
            y += row_h
            for i in sec["indices"]:
                body += study_row(i, studies[i], y)
                y += row_h
            if sec.get("summary"):
                body += summary_row(sec["summary"], y, bold=False)
                y += row_h * 1.6
    else:
        for i, s in enumerate(studies):
            body += study_row(i, s, y)
            y += row_h
    y += row_h * 0.3
    for sm in summaries:
        body += summary_row(sm, y)
        y += row_h * 1.6
    plot_bottom = y
    # null-vonal (ha van értelmes nullhatás) és tengely
    xn = axis.x(null) if null is not None else (plot_x0 + plot_x1) / 2.0
    if null is not None:
        items.append(("null", '<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="%s" stroke-dasharray="3,3"/>'
                      % (xn, xn, body_top, plot_bottom, MUTED)))
    items += body
    ay = plot_bottom + 6
    items.append(("axis", '<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s"/>' % (plot_x0, plot_x1, ay, ay, INK)))
    for pos, lab in axis.ticks():
        xt = axis.x(pos)
        items.append(("axis", '<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="%s"/>' % (xt, xt, ay, ay + 5, INK)))
        items.append(("axis", '<text x="%.1f" y="%.1f" text-anchor="middle" fill="%s">%s</text>'
                      % (xt, ay + 18, INK, num_text(lab, minus))))
    ly = ay + 34
    if axis_title:
        items.append(("labels", '<text x="%.1f" y="%.1f" text-anchor="middle" fill="%s" font-size="11">%s</text>'
                      % ((plot_x0 + plot_x1) / 2.0, ly, INK, escape(axis_title))))
        ly += 16
    if left_label:
        items.append(("labels", '<text x="%.1f" y="%.1f" text-anchor="end" fill="%s" font-size="11">← %s</text>'
                      % (xn - 6, ly, MUTED, escape(left_label))))
    if right_label:
        items.append(("labels", '<text x="%.1f" y="%.1f" fill="%s" font-size="11">%s →</text>'
                      % (xn + 6, ly, MUTED, escape(right_label))))
    fy = ly + 22
    if footer:
        for line in footer if isinstance(footer, list) else [footer]:
            items.append(("footer", '<text x="%d" y="%.1f" fill="%s" font-size="11">%s</text>'
                          % (label_x, fy, MUTED, escape(line))))
            fy += 15
    total_h = int((fy if footer else ly + 6) + 16)
    head = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'font-family="%s" font-size="12" role="img">' % (width, total_h, width, total_h, FONT),
            '<rect width="100%" height="100%" fill="#ffffff"/>']
    return "\n".join(head + _layered(items, "forest", FOREST_LAYERS, annotate) + ["</svg>"])


def _arrow(x, y, direction, color=INK):
    d = 6 * direction
    return '<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s"/>' % (x, y, x - d, y - 4, x - d, y + 4, color)


def _clip(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# a kontúr-javított funnel sávjai (p, z); az SVG és a plot_data v2 poligonjai ugyanezekkel számolnak
FUNNEL_CONTOURS = ((0.10, 1.645), (0.05, 1.96), (0.01, 2.576))
FUNNEL_PSEUDO_Z = 1.96


class FunnelGeometry(object):
    """A funnel plot geometriája az elemzési skálán (az SVG és a plot_data v2 közös forrása):
    se_max, a tengely, a kontúr-poligonok és a pszeudo-95% háromszög."""

    def __init__(self, yi, vi, center, measure, filled_yi=None, filled_vi=None, contour=None, n_harmonic=None,
                 null_value=None, x0=70, x1=610):
        null = default_null(measure) if null_value is None else null_value
        if contour is None:
            contour = null is not None
        if null is None:
            contour = False
        self.null, self.contour, self.center = null, contour, center
        ses = [math.sqrt(v) for v in vi] + ([math.sqrt(v) for v in filled_vi] if filled_vi else [])
        self.se_max = maxse = max(ses) * 1.08 if ses else 1.0
        allx = list(yi) + (list(filled_yi) if filled_yi else []) + [center + 1.96 * maxse, center - 1.96 * maxse]
        if contour:
            allx += [null + 2.576 * maxse, null - 2.576 * maxse]
        self.axis = _Axis(min(allx), max(allx), x0, x1, measure in RATIO_MEASURES, measure, n_harmonic)

    def contours(self):
        """[(p, z, zárt poligon [[x, se], …])] kívülről befelé haladó rajzoláshoz fordított sorrendben."""
        if not self.contour:
            return []
        n, m = self.null, self.se_max
        return [(p, z, [[n, 0.0], [n - z * m, m], [n + z * m, m], [n, 0.0]]) for p, z in FUNNEL_CONTOURS]

    def pseudo_ci(self):
        c, m = self.center, self.se_max
        return [[c - FUNNEL_PSEUDO_Z * m, m], [c, 0.0], [c + FUNNEL_PSEUDO_Z * m, m]]

    def se_ticks(self):
        return _nice_ticks(0, self.se_max, 4)


def funnel_svg(yi, vi, center, measure, title=None, filled_yi=None, filled_vi=None,
               contour=None, labels=None, n_harmonic=None, null_value=None, axis_title=None,
               lang="hu", annotate=False, row_ids=None):
    """Funnel plot: x = hatás az elemzési skálán (a tengelyfeliratok az értelmezési skálán),
    y = SE (fent 0).

    contour: kontúr-javított funnel (p < 0,10 / 0,05 / 0,01 sávok a nullhatás körül;
    Peters et al. 2008). None → automatikus: arány-mértékeknél (egycsoportos arány; nincs
    értelmes nullhatás) nincs kontúr, egyébként van. null_value: a kontúrok középpontja az
    elemzési skálán (alap: default_null). A pszeudo-95% háromszög a `center` (FE-becslés) körül.
    lang / annotate / row_ids: mint a forest_svg-nél (a pontok: <g id="funnel-study-<row_uid>" data-row
    data-x data-se>).
    """
    check_lang(lang)
    minus = minus_for(lang, annotate)
    geo = FunnelGeometry(yi, vi, center, measure, filled_yi, filled_vi, contour, n_harmonic, null_value)
    null, contour, maxse, axis = geo.null, geo.contour, geo.se_max, geo.axis
    width, height = 640, 486
    x0, x1, y0, y1 = 70, 610, 40 if title else 20, 410

    def ypx(se):
        return y0 + se / maxse * (y1 - y0)

    head = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'font-family="%s" font-size="12" role="img">' % (width, height, width, height, FONT),
            '<rect width="100%" height="100%" fill="#ffffff"/>']
    items = []
    if title:
        items.append(("title", '<text x="%d" y="22" font-size="15" font-weight="bold" fill="%s">%s</text>'
                      % (x0, INK, escape(title))))
    items.append(("frame", '<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="%s"/>'
                  % (x0, y0, x1 - x0, y1 - y0, GRID)))
    if contour:
        # sávok kívülről befelé: p<0.01 fehér marad; 0.01–0.05 világos; 0.05–0.10 közép; >0.10 sötét
        shades = {2.576: "#e8e8e8", 1.96: "#cfcfcf", 1.645: "#b0b0b0"}
        for _, zc, _poly in reversed(geo.contours()):
            pts = "%.1f,%.1f %.1f,%.1f %.1f,%.1f" % (axis.x(null), y0, axis.x(null - zc * maxse), y1,
                                                    axis.x(null + zc * maxse), y1)
            items.append(("contours", '<polygon points="%s" fill="%s" opacity="0.9"/>' % (pts, shades[zc])))
    # pszeudo-95% háromszög
    items.append(("pseudo-ci", '<polyline points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="none" stroke="%s" '
                  'stroke-dasharray="4,3"/>' % (axis.x(center - 1.96 * maxse), y1, axis.x(center), y0,
                                                axis.x(center + 1.96 * maxse), y1, INK)))
    items.append(("pseudo-ci", '<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s"/>'
                  % (axis.x(center), axis.x(center), y0, y1, INK)))
    for i, (y, v) in enumerate(zip(yi, vi)):
        dot = '<circle cx="%.1f" cy="%.1f" r="4" fill="%s"/>' % (axis.x(y), ypx(math.sqrt(v)), ACCENT)
        if annotate:
            uid, rix = _row_id(row_ids, i)
            dot = '<g id="funnel-study-%s" data-row="%s" data-x="%s" data-se="%s">%s</g>' % (
                escape(uid if uid is not None else "i%d" % i), "" if rix is None else int(rix), attr_num(y),
                attr_num(math.sqrt(v)), dot)
        items.append(("studies", dot))
    if filled_yi:
        for y, v in zip(filled_yi, filled_vi):
            items.append(("filled", '<circle cx="%.1f" cy="%.1f" r="4" fill="#ffffff" stroke="%s" stroke-width="1.4"/>'
                          % (axis.x(y), ypx(math.sqrt(v)), PI_COLOR)))
    # tengelyek
    for pos, lab in axis.ticks():
        xt = axis.x(pos)
        items.append(("axis", '<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s"/>' % (xt, xt, y1, y1 + 5, INK)))
        items.append(("axis", '<text x="%.1f" y="%d" text-anchor="middle" fill="%s">%s</text>'
                      % (xt, y1 + 19, INK, num_text(lab, minus))))
    for t in geo.se_ticks():
        yt = ypx(t)
        items.append(("axis", '<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s"/>' % (x0 - 5, x0, yt, yt, INK)))
        items.append(("axis", '<text x="%d" y="%.1f" text-anchor="end" fill="%s">%s</text>'
                      % (x0 - 8, yt + 4, INK, num_text("%g" % t, minus))))
    xlab = axis_title or axis_label(measure, lang=lang)
    items.append(("axis", '<text x="%.1f" y="%d" text-anchor="middle" fill="%s">%s</text>'
                  % ((x0 + x1) / 2, y1 + 38, INK, escape(xlab))))
    items.append(("axis", '<text x="18" y="%.1f" transform="rotate(-90 18 %.1f)" text-anchor="middle" fill="%s">%s</text>'
                  % ((y0 + y1) / 2, (y0 + y1) / 2, INK, escape(tr("se_axis", lang)))))
    if contour:
        items.append(("legend", '<text x="%d" y="%d" fill="%s" font-size="10">%s</text>'
                      % (x0, y1 + 52, MUTED, tr("contour_legend", lang))))
    elif null is None:
        items.append(("legend", '<text x="%d" y="%d" fill="%s" font-size="10">%s</text>'
                      % (x0, y1 + 52, MUTED, escape(tr("no_contour", lang)))))
    return "\n".join(head + _layered(items, "funnel", FUNNEL_LAYERS, annotate) + ["</svg>"])


def doi_axes(points, measure, n_harmonic=None, x0=70, x1=610):
    """A Doi-plot tengelyei: (x-tengely az elemzési skálán, |Z|-tengely maximuma) — SVG és plot_data v2."""
    ys = [p["y"] for p in points]
    zs = [p["abs_z"] for p in points]
    maxz = max(zs + [1.0]) * 1.08
    return _Axis(min(ys), max(ys), x0, x1, measure in RATIO_MEASURES, measure, n_harmonic), maxz


def doi_svg(points, lfk, category, measure, labels=None, title=None, n_harmonic=None, axis_title=None,
            lang="hu", annotate=False, row_ids=None):
    """Doi-plot (Furuya-Kanamori, Barendregt & Doi 2018): x = hatás az elemzési skálán (a
    tengelyfeliratok az értelmezési skálán), y = |Z| (a normális kvantilis a rangsorolt kumulatív
    súly-percentilisből; bias.doi_plot_data). A |Z| tengely fordított (0 felül), így szimmetrikus
    eloszlásnál a görbe egy szimmetrikus „hegy”, csúcsán a legkisebb |Z|-jű vizsgálattal; a pontok
    hatás szerinti sorrendben összekötve. Az LFK-index és a kategória az ábrán (heurisztika:
    |LFK| ≤ 1 nincs, 1–2 kisebb, > 2 jelentős aszimmetria).

    points: [{"y", "abs_z", "study_index"}] a bias.doi_plot_data szerint (hatás szerint rendezve);
    labels: a vizsgálatok címkéi (study_index szerint) — minden pont <title>-t kap (rámutatásra).
    lang / annotate / row_ids: mint a forest_svg-nél (row_ids a study_index szerint; a pontok:
    <g id="doi-study-<row_uid>" data-row data-x data-abs-z>)."""
    check_lang(lang)
    minus = minus_for(lang, annotate)
    category = lfk_category(category, lang)
    width, height = 640, 486
    x0, x1, y0, y1 = 70, 610, 40, 400      # a fejlécsorban a cím (balra) és az LFK-index (jobbra)
    axis, maxz = doi_axes(points, measure, n_harmonic, x0, x1)
    lfk_txt = num_text(_fmt(lfk, 2), minus)

    def ypx(z):
        return y0 + z / maxz * (y1 - y0)       # 0 felül

    head = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'font-family="%s" font-size="12" role="img">' % (width, height, width, height, FONT),
            '<title>%s</title>' % (tr("doi_svg_title", lang) % (lfk_txt, escape(category or ""))),
            '<rect width="100%" height="100%" fill="#ffffff"/>']
    items = []
    if title:
        items.append(("title", '<text x="%d" y="22" font-size="15" font-weight="bold" fill="%s">%s</text>'
                      % (x0, INK, escape(title))))
    items.append(("frame", '<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="%s"/>'
                  % (x0, y0, x1 - x0, y1 - y0, GRID)))
    for t in _nice_ticks(0, maxz, 4):
        yt = ypx(t)
        items.append(("frame", '<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s"/>' % (x0, x1, yt, yt, GRID)))
        items.append(("frame", '<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s"/>' % (x0 - 5, x0, yt, yt, INK)))
        items.append(("frame", '<text x="%d" y="%.1f" text-anchor="end" fill="%s">%s</text>'
                      % (x0 - 8, yt + 4, INK, num_text("%g" % t, minus))))
    pts = " ".join("%.1f,%.1f" % (axis.x(p["y"]), ypx(p["abs_z"])) for p in points)
    items.append(("curve", '<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6"/>' % (pts, ACCENT)))
    for p in points:
        i = p.get("study_index")
        lab = labels[i] if (labels is not None and i is not None and 0 <= i < len(labels)) else None
        z_txt = num_text(_fmt(p["abs_z"], 2), minus)
        dot = '<circle cx="%.1f" cy="%.1f" r="4" fill="%s"><title>%s</title></circle>' % (
            axis.x(p["y"]), ypx(p["abs_z"]), ACCENT,
            escape("%s: |Z| = %s" % (lab, z_txt) if lab else "|Z| = %s" % z_txt))
        if annotate:
            uid, rix = _row_id(row_ids, i)
            dot = '<g id="doi-study-%s" data-row="%s" data-x="%s" data-abs-z="%s">%s</g>' % (
                escape(uid if uid is not None else "i%s" % i), "" if rix is None else int(rix), attr_num(p["y"]),
                attr_num(p["abs_z"]), dot)
        items.append(("studies", dot))
    for pos, lab in axis.ticks():
        xt = axis.x(pos)
        items.append(("axis", '<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s"/>' % (xt, xt, y1, y1 + 5, INK)))
        items.append(("axis", '<text x="%.1f" y="%d" text-anchor="middle" fill="%s">%s</text>'
                      % (xt, y1 + 19, INK, num_text(lab, minus))))
    xlab = axis_title or axis_label(measure, lang=lang)
    items.append(("axis", '<text x="%.1f" y="%d" text-anchor="middle" fill="%s">%s</text>'
                  % ((x0 + x1) / 2, y1 + 38, INK, escape(xlab))))
    items.append(("axis", '<text x="18" y="%.1f" transform="rotate(-90 18 %.1f)" text-anchor="middle" fill="%s">%s</text>'
                  % ((y0 + y1) / 2, (y0 + y1) / 2, INK, escape(tr("abs_z", lang)))))
    items.append(("legend", '<text x="%d" y="22" text-anchor="end" font-size="13" font-weight="bold" fill="%s">%s</text>'
                  % (x1, INK, tr("lfk", lang) % (lfk_txt, escape(category or "")))))
    for i, line in enumerate(TEXTS["doi_note"][lang]):
        items.append(("legend", '<text x="%d" y="%d" fill="%s" font-size="10">%s</text>'
                      % (x0 - 50, y1 + 56 + 13 * i, MUTED, line)))
    return "\n".join(head + _layered(items, "doi", DOI_LAYERS, annotate) + ["</svg>"])
