# -*- coding: utf-8 -*-
"""Stdlib SVG-audit és számhűség-újraellenőrzés (terv 3.5.9, 5.2 „Ábra-audit — önálló”, 5.3, 6.2, 6.7).

Két feladat, statisztika nélkül:

- ``audit_svg(svg_bytes)`` — a figure-forge ``audit``-jának stdlib-tartaléka a motor-SVG-n (a figure-forge
  hiányában vagy H5 miatt): ``<text>``-elemek száma, szöveggé nem alakított („outlined”) címkecsoportok,
  betűkészlet-láncok, elnevezett rétegek (``<g id="layer-…">`` vagy ``inkscape:label``), és a tipográfiai
  JAVASLATOK (kötőjel-mínusz szám előtt → U+2212; szám–szám tartomány → nagykötőjel). Semmit nem javít, csak
  jelent — a fájl a motoré (vagy a felhasználóé).
- ``numbers_check(texts, plot, kind)`` — a 6.7 számhűség-lánc szerveroldali láncszeme: a motor kész szövegei
  (``display_text``, ``weight_text``, ``pi_text``, a tengelyosztások ``ticks[].text``-je, az LFK-szöveg)
  SZÓ SZERINT szerepelnek-e az SVG ``<text>``-elemeiben (U+2212 és nagykötőjel → ``-`` normalizálva, mindkét
  nyelvi változatot elfogadva). Számot nem formáz, nem kerekít, nem értelmez.

Biztonság: DOCTYPE/ENTITY-t tartalmazó SVG-t nem parszolunk (entitás-robbantás ellen), a méret korlátozott.
A címkék (vizsgálatnevek) a válaszba kerülhetnek, naplóba soha (T10)."""
import re
import xml.etree.ElementTree as ET

MAX_SVG_BYTES = 32 * 1024 * 1024
MAX_SUGGESTIONS = 50
MAX_MISMATCHES = 100
SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_LABEL = "{http://www.inkscape.org/namespaces/inkscape}label"

MINUS = "−"
EN_DASH = "–"
_WS_RE = re.compile(r"\s+")
_DOCTYPE_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)
_FONT_STYLE_RE = re.compile(r"font-family\s*:\s*([^;]+)")
# kötőjel-mínusz egy szám előtt, ha nem azonosító része (IL-6, COVID-19 marad): előtte nem betű/szám/pont
_HYPHEN_MINUS_RE = re.compile(r"(?<![\w.\-])-(?=\d)")
# tartomány: számjegy-kötőjel-számjegy, betű nélkül a két oldalon (2019-2021 → 2019–2021); a mínusz-szabály után
_RANGE_RE = re.compile(r"(?<=\d)-(?=\d)")
_OUTLINED_ID_RE = re.compile(r"^text_\d+$")
_LAYER_ID_RE = re.compile(r"^layer-")

KINDS = ("forest", "funnel", "doi", "cumulative", "loo", "bubble", "rob_traffic", "rob_summary", "flowchart")


class SvgError(ValueError):
    """Az SVG nem auditálható (túl nagy, DOCTYPE/ENTITY, nem XML) — az üzenet nem idéz tartalmat."""


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_svg(data):
    """bytes/str → ElementTree gyökér. Hibánál SvgError (magyar üzenet, tartalom nélkül)."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data or b"")
    if not raw.strip():
        raise SvgError("Az SVG üres.")
    if len(raw) > MAX_SVG_BYTES:
        raise SvgError("Az SVG túl nagy az audithoz (legfeljebb %d MB)." % (MAX_SVG_BYTES // (1024 * 1024)))
    if _DOCTYPE_RE.search(raw):
        raise SvgError("Az SVG DOCTYPE- vagy ENTITY-deklarációt tartalmaz; biztonsági okból nem auditáljuk.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise SvgError("Az SVG nem érvényes XML.") from None
    if _local(root.tag) != "svg":
        raise SvgError("A fájl gyökéreleme nem <svg>.")
    return root


def norm(text):
    """Összevetési alak: U+2212 és nagykötőjel → '-', NBSP/keskeny szóköz → szóköz, szóközök összevonva."""
    s = str(text or "")
    s = s.replace(MINUS, "-").replace(EN_DASH, "-").replace(" ", " ").replace(" ", " ")
    s = s.replace(" ", " ")
    return _WS_RE.sub(" ", s).strip()


def text_elements(root):
    """A <text>-elemek teljes szövege (a <tspan>-okkal együtt), dokumentum-sorrendben."""
    out = []
    for el in root.iter():
        if _local(el.tag) == "text":
            out.append("".join(el.itertext()))
    return out


def _font_families(root):
    fams = set()
    for el in root.iter():
        fam = el.get("font-family")
        if fam:
            fams.add(fam.strip())
        style = el.get("style")
        if style:
            for m in _FONT_STYLE_RE.finditer(style):
                fams.add(m.group(1).strip())
    return sorted(f for f in fams if f)


def typography_suggestions(labels):
    """[(eredeti, javasolt)] — csak jelentés: kötőjel-mínusz szám előtt → U+2212, szám-szám → nagykötőjel."""
    out = []
    for lab in labels:
        want = _HYPHEN_MINUS_RE.sub(MINUS, lab)
        want = _RANGE_RE.sub(EN_DASH, want)
        if want != lab:
            out.append({"from": lab, "to": want})
    return out


def audit_svg(data):
    """A motor-SVG (vagy bármely SVG) stdlib-auditja → dict (lásd a modul leírását). Hibánál SvgError."""
    root = parse_svg(data)
    labels = [t.strip() for t in text_elements(root)]
    outlined = 0
    layers = 0
    for el in root.iter():
        if _local(el.tag) != "g":
            continue
        gid = el.get("id") or ""
        if _OUTLINED_ID_RE.match(gid) and not any(_local(c.tag) == "text" for c in el.iter()):
            outlined += 1
        if _LAYER_ID_RE.match(gid) or el.get(INKSCAPE_LABEL):
            layers += 1
    fams = _font_families(root)
    sugg = typography_suggestions([lab for lab in labels if lab])
    n_text = len(labels)
    return {
        "source": "stdlib",
        "text_elements": n_text,
        "outlined_text_groups": outlined,
        "editable": n_text > 0 and outlined == 0,
        "font_families": fams,
        "font_fallback_stack": any("," in f for f in fams),
        "named_layers": layers,
        "typography": {"suggestions_n": len(sugg), "suggestions": sugg[:MAX_SUGGESTIONS]},
        "glyphs": None,                     # betűkészlet nélkül nem ellenőrizhető (figure-forge kell)
    }


# ---------------------------------------------------------------------------- számhűség (6.7)
def _variants(value):
    """Egy motorszöveg összevetési alakjai: {hu,en}-objektum mindkét nyelve, vagy a sima szöveg."""
    if isinstance(value, dict):
        vals = [value.get(k) for k in ("hu", "en")]
    else:
        vals = [value]
    out = []
    for v in vals:
        if isinstance(v, str) and v.strip():
            n = norm(v)
            if n and n not in out:
                out.append(n)
    return out


def _ticks(axis, path, items):
    if not isinstance(axis, dict):
        return
    for i, t in enumerate(axis.get("ticks") or []):
        if isinstance(t, dict):
            val = t.get("text_i18n") if isinstance(t.get("text_i18n"), dict) else t.get("text")
            items.append(("%s.ticks[%d].text" % (path, i), val, "exact", None))


def _row(prefix, uid, label):
    """Egy ábrasor azonosítása: az annotált motor-SVG sorcsoportja (``<g id="<prefix><row_uid>" data-row>``), ennek
    hiányában a sor címkéje (ugyanabban a magasságban, y, álló szövegek)."""
    lab = label if isinstance(label, str) else None
    return {"group": (prefix + uid) if isinstance(uid, str) and uid else None, "label": norm(lab) if lab else None}


def expected_texts(plot, kind):
    """[(mező-út, motorszöveg, mód, sor)] — a motor kész szövegei, amelyeknek az ábrán szó szerint látszaniuk kell.
    mód: 'contains' (egy elemen belül), 'exact' (a tengelyosztás: egy „1” részszövegként bárhol előfordulna),
    'wrapped' (több sorba tördelt hosszú szöveg: az elemek egymás utáni szövegében). sor: None (bárhol), vagy a
    vizsgálat/lépés sora (_row) — a sorhoz kötött szám CSAK a saját sorában számít (két sor felcserélése hiba)."""
    items = []
    if not isinstance(plot, dict):
        return items
    if kind == "forest":
        for i, s in enumerate(plot.get("studies") or []):
            if isinstance(s, dict):
                row = _row("study-", s.get("row_uid"), s.get("label"))
                items.append(("studies[%d].display_text" % i, s.get("display_text"), "contains", row))
                if s.get("weight_text") is not None:
                    items.append(("studies[%d].weight_text" % i, s.get("weight_text"), "exact", row))
        for i, s in enumerate(plot.get("summaries") or []):
            if isinstance(s, dict):
                items.append(("summaries[%d].display_text" % i, s.get("display_text"), "contains", None))
                if s.get("pi_text") is not None:
                    items.append(("summaries[%d].pi_text" % i, s.get("pi_text"), "contains", None))
        for i, sec in enumerate(plot.get("sections") or []):
            summ = sec.get("summary") if isinstance(sec, dict) else None
            if isinstance(summ, dict) and summ.get("display_text") is not None:
                items.append(("sections[%d].summary.display_text" % i, summ.get("display_text"), "contains", None))
        het = plot.get("heterogeneity") if isinstance(plot.get("heterogeneity"), dict) else {}
        if het.get("text") is not None:
            items.append(("heterogeneity.text", het.get("text"), "wrapped", None))
        _ticks(plot.get("axis"), "axis", items)
    elif kind in ("funnel", "doi"):
        part = plot.get(kind) if isinstance(plot.get(kind), dict) else {}
        _ticks(part.get("axis"), kind + ".axis", items)
        _ticks(part.get("y_axis"), kind + ".y_axis", items)
        if kind == "doi" and part.get("lfk_text") is not None:
            items.append(("doi.lfk_text", part.get("lfk_text"), "contains", None))
    elif kind == "cumulative":
        cum = plot.get("cumulative") if isinstance(plot.get("cumulative"), dict) else {}
        for i, e in enumerate(cum.get("entries") or []):
            if isinstance(e, dict):
                row = _row("cumulative-step-", e.get("added_row_uid"), e.get("label"))
                items.append(("cumulative.entries[%d].display_text" % i, e.get("display_text"), "contains", row))
                for f in ("i2_text", "tau2_text", "key_text"):
                    if e.get(f) is not None:
                        items.append(("cumulative.entries[%d].%s" % (i, f), e.get(f), "exact", row))
                if isinstance(e.get("k"), int) and not isinstance(e.get("k"), bool):
                    items.append(("cumulative.entries[%d].k" % i, str(e["k"]), "exact", row))
        _ticks(cum.get("axis"), "cumulative.axis", items)
    elif kind == "loo":
        for i, e in enumerate(plot.get("loo") or []):
            if isinstance(e, dict):
                row = _row("loo-study-", e.get("omitted_row_uid"), e.get("label"))
                items.append(("loo[%d].display_text" % i, e.get("display_text"), "contains", row))
                for f in ("i2_text", "tau2_text"):
                    if e.get(f) is not None:
                        items.append(("loo[%d].%s" % (i, f), e.get(f), "exact", row))
        _ticks(plot.get("loo_axis"), "loo_axis", items)
    elif kind == "bubble":
        bub = plot.get("bubble") if isinstance(plot.get("bubble"), dict) else {}
        _ticks(bub.get("axis") or bub.get("x_axis"), "bubble.x_axis", items)
        _ticks(bub.get("y_axis"), "bubble.y_axis", items)
        if bub.get("coef_text") is not None:
            items.append(("bubble.coef_text", bub.get("coef_text"), "wrapped", None))
    return [it for it in items if _variants(it[1])]


def text_nodes(root):
    """[(szöveg, sorcsoport-id | None, y | None)] dokumentum-sorrendben: a legközelebbi ``data-row``-os ``<g>`` őse
    azonosítója és a ``<text>`` y-koordinátája (a sor felismeréséhez annotálatlan SVG-n)."""
    out = []

    def walk(el, group):
        tag = _local(el.tag)
        if tag == "g" and el.get("data-row") is not None and el.get("id"):
            group = el.get("id")
        if tag == "text":
            y = el.get("y")
            try:
                yv = round(float(y), 1) if y is not None else None
            except ValueError:
                yv = None
            out.append(("".join(el.itertext()), group, yv))
            return
        for c in el:
            walk(c, group)
    walk(root, None)
    return out


_STATIC = []


def _static_texts():
    """A motor rajzolójának kódba égetett feliratai (pl. a funnel sáv-jelmagyarázata) — a homlokzatról, egyszer."""
    if not _STATIC:
        try:
            from metaelemzes import api
            fn = getattr(api, "figure_static_texts", None)
            vals = fn() if callable(fn) else []
        except Exception:                               # noqa: BLE001 — a hiányuk csak szigorúbbá teszi az ellenőrzést
            vals = []
        _STATIC.append(set(norm(v) for v in vals if isinstance(v, str) and v.strip()))
    return _STATIC[0]


def _plot_strings(plot):
    """A motor dokumentumának összes szöveges értéke (normalizálva) — a „nem magyarázott szám” kereséshez."""
    out = set(_static_texts())

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            n = norm(o)
            if n:
                out.add(n)
    walk(plot)
    return out


_DIGIT_RE = re.compile(r"\d")
_LETTER_RE = re.compile(r"[^\W\d_]")


def numbers_check(texts, plot, kind):
    """{ok, checked, matched, mismatches[{field, expected, found?}], unexplained, kind} — a motorszövegek szó
    szerinti jelenléte az SVG szövegei között (6.7).

    - A sorhoz kötött szám (vizsgálat hatása és súlya; kumulatív lépés k / I² / τ²; LOO-sor) a SAJÁT sorában kell,
      hogy szerepeljen (az annotált SVG sorcsoportja, különben a címkével azonos magasságú szövegek) — két sor
      felcserélése eltérés.
    - Az ábra minden számot tartalmazó ``<text>``-elemének a motor valamely szövegéből kell származnia (vagy azzal
      egyeznie / azt tartalmaznia, vagy a motor dokumentumának egy szövegrésze lennie); a nem magyarázható szám is
      eltérés (pl. átírt heterogenitás-sor, R², együttható).
    ``texts``: szövegek listája VAGY text_nodes() hármasai. ``ok`` None, ha a fajtához nincs ellenőrizhető
    motorszöveg (nem zöld, nem piros)."""
    nodes = [t if isinstance(t, tuple) else (t, None, None) for t in texts or []]
    nodes = [(norm(t), g, y) for t, g, y in nodes if isinstance(t, str)]
    elems = [t for t, _g, _y in nodes]
    elem_set = set(elems)
    joined = " ".join(e for e in elems if e)
    by_group, by_y = {}, {}
    for t, g, y in nodes:
        if g:
            by_group.setdefault(g, []).append(t)
        if y is not None:
            by_y.setdefault(y, []).append(t)
    items = expected_texts(plot, kind)
    mismatches = []
    matched = 0

    def hit_in(cands, mode, pool):
        if mode == "exact":
            return any(c in pool for c in cands)
        return any(c in e for c in cands for e in pool)

    def row_pools(row):
        """A sor szövegei (listák listája: több azonos címkéjű sor esetén bármelyik); None, ha a sor nem található."""
        if not row:
            return None
        if row.get("group") and row["group"] in by_group:
            return [by_group[row["group"]]]
        lab = row.get("label")
        if lab:
            ys = sorted(set(y for t, _g, y in nodes if t == lab and y is not None))
            if ys:
                return [by_y[y] for y in ys]
        return None

    for field, value, mode, row in items:
        cands = _variants(value)
        pools = row_pools(row)
        if pools is not None:
            hit = any(hit_in(cands, mode, pool) for pool in pools)
        elif mode == "exact":
            hit = any(c in elem_set for c in cands)
        elif mode == "wrapped":
            hit = any(c in joined for c in cands)
        else:
            hit = any(c in e for c in cands for e in elems)
        if hit:
            matched += 1
        elif len(mismatches) < MAX_MISMATCHES:
            mismatches.append({"field": field, "expected": cands[0]})
    # nem magyarázott számok: minden számjegyet tartalmazó szöveg a motor szövegeiből
    checked = len(items)
    unexplained = 0
    if checked:
        exact_all = set(c for _f, v, _m, _r in items for c in _variants(v))
        long_all = [c for _f, v, m, _r in items if m != "exact" for c in _variants(v)]
        known = _plot_strings(plot)
        # a sablonba illesztett motor-kifejezések (pl. „95% confidence band” a jelmagyarázatban): betűt is tartalmazó
        # szövegek — a puszta számok (tengelyosztás „1”) nem „magyaráznak” meg egy átírt számot
        phrases = sorted(set(long_all) | set(k for k in known if _LETTER_RE.search(k) and _DIGIT_RE.search(k)),
                         key=len, reverse=True)
        for t in elems:
            if not t or not _DIGIT_RE.search(t) or t in exact_all:
                continue
            rest = t
            for c in phrases:
                if c in rest:
                    rest = rest.replace(c, " ")
            if not _DIGIT_RE.search(rest):
                continue
            # tördelt hosszú motorszöveg egy sora (pl. megjegyzés): a motor egy szövegének része
            if (len(t) >= 8 or _LETTER_RE.search(t)) and any(t in k for k in known):
                continue
            unexplained += 1
            if len(mismatches) < MAX_MISMATCHES:
                mismatches.append({"field": "svg.text", "expected": None, "found": t[:200]})
    return {"kind": kind, "checked": checked, "matched": matched, "mismatches": mismatches,
            "unexplained": unexplained, "ok": (matched == checked and not unexplained) if checked else None}


def numbers_check_svg(data, plot, kind):
    """Az SVG bájtjaiból: numbers_check a <text>-elemeken (sor-azonosítással). Hibánál SvgError."""
    return numbers_check(text_nodes(parse_svg(data)), plot, kind)
