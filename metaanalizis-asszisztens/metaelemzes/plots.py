# -*- coding: utf-8 -*-
"""Forest és funnel plot SVG-ben (csak standard könyvtár), valamint a rajzolási
adatok JSON-ban, hogy külső ábrakészítő (pl. figure-forge) is újrarajzolhassa.

Konvenciók (Cochrane/PRISMA): négyzetméret ∝ súly, gyémánt = összesített becslés és CI,
vízszintes vonal a gyémánt alatt = predikciós intervallum, arány-mértékeknél log-skála.
"""
import math
from xml.sax.saxutils import escape

from .effect_sizes import RATIO_MEASURES, PROPORTION, back_transform, pft_of_p

FONT = "Helvetica, Arial, sans-serif"
INK = "#1a1a1a"
MUTED = "#6b6b6b"
GRID = "#d9d9d9"
ACCENT = "#1f4e79"
PI_COLOR = "#b03a2e"

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


def fmt_triple(est, lo, hi):
    """'becslés [alsó; felső]' közös, nagyságrendhez igazított tizedesjeggyel."""
    nd = decimals((est, lo, hi))
    return "%s [%s; %s]" % (_fmt(est, nd), _fmt(lo, nd), _fmt(hi, nd))


def _nice_ticks(lo, hi, n=5):
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=10 * mag)
    start = math.ceil(lo / step) * step
    ticks = []
    t = start
    while t <= hi + 1e-12:
        ticks.append(round(t, 10))
        t += step
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


def axis_label(measure, label=None):
    """A tengely címe, a skála megnevezésével."""
    lab = label or "Hatásméret"
    if measure in RATIO_MEASURES:
        return "%s (log-skálán ábrázolva)" % lab
    return {"PLN": "%s (log-skálán ábrázolva)", "PLO": "%s (logit-skálán ábrázolva)",
            "PAS": "%s (arcsin-skálán ábrázolva)", "PFT": "%s (Freeman–Tukey skálán ábrázolva)",
            "ZCOR": "%s (Fisher z-skálán ábrázolva)"}.get(measure, "%s") % lab


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
        if self.ratio:
            pos, labels = _ratio_ticks(self.lo, self.hi)
            ticks = list(zip(pos, [("%g" % t) for t in labels]))
        elif self.measure in TRANSFORMED_MEASURES and not (self.measure == "PFT" and not self.n_harmonic):
            ticks = self._transformed_ticks()
        else:
            ticks = [(t, "%g" % t) for t in _nice_ticks(self.lo, self.hi)]
        # egymásra csúszó feliratok elhagyása
        out = []
        for pos, lab in ticks:
            if out and abs(self.x(pos) - self.x(out[-1][0])) < min_px:
                continue
            out.append((pos, lab))
        return out

    def _transformed_ticks(self):
        m, nh = self.measure, self.n_harmonic
        a, b = back_transform(m, self.lo, nh), back_transform(m, self.hi, nh)
        dom = (-1.0, 1.0) if m == "ZCOR" else (0.0, 1.0)
        a, b = max(min(a, b), dom[0]), min(max(a, b), dom[1])
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
    return {"measure": measure, "ratio_scale": measure in RATIO_MEASURES, "level": level,
            "axis_n": axis_n, "studies": studies, "summaries": summaries, "sections": sections}


def _single_n(n_info, i):
    if isinstance(n_info, (list, tuple)):
        return n_info[i]
    return n_info


def default_null(measure):
    """A nullhatás az ábrázolási skálán; arány-mértékeknél (egycsoportos arány) nincs."""
    if measure in PROPORTION:
        return None
    return 0.0          # log(1) = 0 (OR/RR/ROM), atanh(0) = 0 (ZCOR), 0 (MD/SMD/RD/COR/GEN)


def forest_svg(data, title=None, left_label=None, right_label=None, null_value=None,
               effect_label=None, footer=None, axis_title=None):
    """null_value: a referenciavonal az ábrázolási skálán; None → a mérték nullhatása
    (default_null); arány-mértékeknél nincs vonal (nincs értelmes nullhatás)."""
    studies = data["studies"]
    summaries = data.get("summaries") or []
    ratio = data["ratio_scale"]
    measure = data.get("measure")
    sections = data.get("sections")
    row_h = 22
    width = 940
    label_x, plot_x0, plot_x1, est_x, w_x = 12, 300, 640, 660, 925
    sec_sums = [sec["summary"] for sec in (sections or []) if sec.get("summary")]
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
    axis = _Axis(lo, hi, plot_x0, plot_x1, ratio, measure, data.get("axis_n"))
    top = 40 if title else 16
    header_y = top + 14
    body_top = header_y + 14
    out = []
    eff_lab = effect_label or ("%s [%d%% CI]" % ("Hatás", int(round(data["level"] * 100))))
    out.append('<text x="%d" y="%d" font-weight="bold" fill="%s">Vizsgálat</text>' % (label_x, header_y, INK))
    out.append('<text x="%d" y="%d" font-weight="bold" fill="%s">%s</text>' % (est_x, header_y, INK, escape(eff_lab)))
    out.append('<text x="%d" y="%d" font-weight="bold" fill="%s" text-anchor="end">Súly</text>' % (w_x, header_y, INK))
    out.append('<line x1="%d" x2="%d" y1="%d" y2="%d" stroke="%s"/>' % (label_x, w_x, header_y + 6, header_y + 6, INK))
    y = body_top + row_h * 0.6
    maxw = max([s["weight_pct"] or 0 for s in studies] + [1e-9])

    def study_row(s, y):
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
        parts.append('<text x="%d" y="%.1f" fill="%s">%s</text>' % (est_x, y + 4, INK, fmt_triple(d[0], d[1], d[2])))
        if s["weight_pct"] is not None:
            parts.append('<text x="%d" y="%.1f" fill="%s" text-anchor="end">%.1f%%</text>'
                         % (w_x, y + 4, MUTED, s["weight_pct"]))
        return parts

    def summary_row(sm, y, bold=True):
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
                     % (est_x, y + 4, "bold" if bold else "normal", INK, fmt_triple(d[0], d[1], d[2])))
        if sm.get("pi_lower") is not None:
            pd = sm.get("pi_display") or [sm["pi_lower"], sm["pi_upper"]]
            nd = decimals(pd)
            parts.append('<text x="%d" y="%.1f" fill="%s" font-size="11">PI [%s; %s]</text>'
                         % (est_x, y + 19, PI_COLOR, _fmt(pd[0], nd), _fmt(pd[1], nd)))
        if sm.get("note"):
            parts.append('<text x="%d" y="%.1f" fill="%s" font-size="11">%s</text>'
                         % (label_x, y + 19, MUTED, escape(sm["note"])))
        return parts

    body = []
    if sections:
        for sec in sections:
            body.append('<text x="%d" y="%.1f" font-weight="bold" font-style="italic" fill="%s">%s</text>'
                        % (label_x, y + 4, INK, escape(sec["title"])))
            y += row_h
            for i in sec["indices"]:
                body += study_row(studies[i], y)
                y += row_h
            if sec.get("summary"):
                body += summary_row(sec["summary"], y, bold=False)
                y += row_h * 1.6
    else:
        for s in studies:
            body += study_row(s, y)
            y += row_h
    y += row_h * 0.3
    for sm in summaries:
        body += summary_row(sm, y)
        y += row_h * 1.6
    plot_bottom = y
    # null-vonal (ha van értelmes nullhatás) és tengely
    xn = axis.x(null) if null is not None else (plot_x0 + plot_x1) / 2.0
    if null is not None:
        out.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="%s" stroke-dasharray="3,3"/>'
                   % (xn, xn, body_top, plot_bottom, MUTED))
    out += body
    ay = plot_bottom + 6
    out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s"/>' % (plot_x0, plot_x1, ay, ay, INK))
    for pos, lab in axis.ticks():
        xt = axis.x(pos)
        out.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="%s"/>' % (xt, xt, ay, ay + 5, INK))
        out.append('<text x="%.1f" y="%.1f" text-anchor="middle" fill="%s">%s</text>' % (xt, ay + 18, INK, lab))
    ly = ay + 34
    if axis_title:
        out.append('<text x="%.1f" y="%.1f" text-anchor="middle" fill="%s" font-size="11">%s</text>'
                   % ((plot_x0 + plot_x1) / 2.0, ly, INK, escape(axis_title)))
        ly += 16
    if left_label:
        out.append('<text x="%.1f" y="%.1f" text-anchor="end" fill="%s" font-size="11">← %s</text>'
                   % (xn - 6, ly, MUTED, escape(left_label)))
    if right_label:
        out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11">%s →</text>'
                   % (xn + 6, ly, MUTED, escape(right_label)))
    fy = ly + 22
    if footer:
        for line in footer if isinstance(footer, list) else [footer]:
            out.append('<text x="%d" y="%.1f" fill="%s" font-size="11">%s</text>' % (label_x, fy, MUTED, escape(line)))
            fy += 15
    total_h = int((fy if footer else ly + 6) + 16)
    head = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'font-family="%s" font-size="12" role="img">' % (width, total_h, width, total_h, FONT),
            '<rect width="100%" height="100%" fill="#ffffff"/>']
    if title:
        head.append('<text x="%d" y="22" font-size="15" font-weight="bold" fill="%s">%s</text>'
                    % (label_x, INK, escape(title)))
    out.append("</svg>")
    return "\n".join(head + out)


def _arrow(x, y, direction, color=INK):
    d = 6 * direction
    return '<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s"/>' % (x, y, x - d, y - 4, x - d, y + 4, color)


def _clip(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def funnel_svg(yi, vi, center, measure, title=None, filled_yi=None, filled_vi=None,
               contour=None, labels=None, n_harmonic=None, null_value=None, axis_title=None):
    """Funnel plot: x = hatás az elemzési skálán (a tengelyfeliratok az értelmezési skálán),
    y = SE (fent 0).

    contour: kontúr-javított funnel (p < 0,10 / 0,05 / 0,01 sávok a nullhatás körül;
    Peters et al. 2008). None → automatikus: arány-mértékeknél (egycsoportos arány; nincs
    értelmes nullhatás) nincs kontúr, egyébként van. null_value: a kontúrok középpontja az
    elemzési skálán (alap: default_null). A pszeudo-95% háromszög a `center` (FE-becslés) körül.
    """
    null = default_null(measure) if null_value is None else null_value
    if contour is None:
        contour = null is not None
    if null is None:
        contour = False
    width, height = 640, 486
    x0, x1, y0, y1 = 70, 610, 40 if title else 20, 410
    ses = [math.sqrt(v) for v in vi] + ([math.sqrt(v) for v in filled_vi] if filled_vi else [])
    maxse = max(ses) * 1.08 if ses else 1.0
    allx = list(yi) + (list(filled_yi) if filled_yi else []) + [center + 1.96 * maxse, center - 1.96 * maxse]
    if contour:
        allx += [null + 2.576 * maxse, null - 2.576 * maxse]
    axis = _Axis(min(allx), max(allx), x0, x1, measure in RATIO_MEASURES, measure, n_harmonic)

    def ypx(se):
        return y0 + se / maxse * (y1 - y0)

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
           'font-family="%s" font-size="12" role="img">' % (width, height, width, height, FONT),
           '<rect width="100%" height="100%" fill="#ffffff"/>']
    if title:
        out.append('<text x="%d" y="22" font-size="15" font-weight="bold" fill="%s">%s</text>' % (x0, INK, escape(title)))
    out.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="%s"/>' % (x0, y0, x1 - x0, y1 - y0, GRID))
    if contour:
        # sávok kívülről befelé: p<0.01 fehér marad; 0.01–0.05 világos; 0.05–0.10 közép; >0.10 sötét
        bands = ((2.576, "#e8e8e8"), (1.96, "#cfcfcf"), (1.645, "#b0b0b0"))
        for zc, shade in bands:
            pts = "%.1f,%.1f %.1f,%.1f %.1f,%.1f" % (axis.x(null), y0, axis.x(null - zc * maxse), y1,
                                                    axis.x(null + zc * maxse), y1)
            out.append('<polygon points="%s" fill="%s" opacity="0.9"/>' % (pts, shade))
    # pszeudo-95% háromszög
    out.append('<polyline points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="none" stroke="%s" stroke-dasharray="4,3"/>'
               % (axis.x(center - 1.96 * maxse), y1, axis.x(center), y0, axis.x(center + 1.96 * maxse), y1, INK))
    out.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s"/>' % (axis.x(center), axis.x(center), y0, y1, INK))
    for y, v in zip(yi, vi):
        out.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s"/>' % (axis.x(y), ypx(math.sqrt(v)), ACCENT))
    if filled_yi:
        for y, v in zip(filled_yi, filled_vi):
            out.append('<circle cx="%.1f" cy="%.1f" r="4" fill="#ffffff" stroke="%s" stroke-width="1.4"/>'
                       % (axis.x(y), ypx(math.sqrt(v)), PI_COLOR))
    # tengelyek
    for pos, lab in axis.ticks():
        xt = axis.x(pos)
        out.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s"/>' % (xt, xt, y1, y1 + 5, INK))
        out.append('<text x="%.1f" y="%d" text-anchor="middle" fill="%s">%s</text>' % (xt, y1 + 19, INK, lab))
    for t in _nice_ticks(0, maxse, 4):
        yt = ypx(t)
        out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s"/>' % (x0 - 5, x0, yt, yt, INK))
        out.append('<text x="%d" y="%.1f" text-anchor="end" fill="%s">%g</text>' % (x0 - 8, yt + 4, INK, t))
    xlab = axis_title or axis_label(measure)
    out.append('<text x="%.1f" y="%d" text-anchor="middle" fill="%s">%s</text>' % ((x0 + x1) / 2, y1 + 38, INK, escape(xlab)))
    out.append('<text x="18" y="%.1f" transform="rotate(-90 18 %.1f)" text-anchor="middle" fill="%s">Standard hiba '
               '(elemzési skála)</text>' % ((y0 + y1) / 2, (y0 + y1) / 2, INK))
    if contour:
        out.append('<text x="%d" y="%d" fill="%s" font-size="10">Sávok (a nullhatás körül): p &gt; 0.10 sötét · 0.05–0.10 · 0.01–0.05 · p &lt; 0.01 fehér</text>'
                   % (x0, y1 + 52, MUTED))
    elif null is None:
        out.append('<text x="%d" y="%d" fill="%s" font-size="10">Egycsoportos arány: nincs nullhatás, ezért nincsenek '
                   'szignifikancia-kontúrok; az aszimmetria arányoknál nehezen értelmezhető.</text>' % (x0, y1 + 52, MUTED))
    out.append("</svg>")
    return "\n".join(out)
