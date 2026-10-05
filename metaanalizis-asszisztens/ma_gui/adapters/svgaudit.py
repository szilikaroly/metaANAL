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
            items.append(("%s.ticks[%d].text" % (path, i), val, "exact"))


def expected_texts(plot, kind):
    """[(mező-út, motorszöveg, 'contains'|'exact')] — a motor kész szövegei, amelyeknek az ábrán szó szerint
    látszaniuk kell. A tengelyosztás pontos egyezést kér (egy „1” részszövegként bárhol előfordulna)."""
    items = []
    if not isinstance(plot, dict):
        return items
    if kind == "forest":
        for i, s in enumerate(plot.get("studies") or []):
            if isinstance(s, dict):
                items.append(("studies[%d].display_text" % i, s.get("display_text"), "contains"))
                if s.get("weight_text") is not None:
                    items.append(("studies[%d].weight_text" % i, s.get("weight_text"), "exact"))
        for i, s in enumerate(plot.get("summaries") or []):
            if isinstance(s, dict):
                items.append(("summaries[%d].display_text" % i, s.get("display_text"), "contains"))
                if s.get("pi_text") is not None:
                    items.append(("summaries[%d].pi_text" % i, s.get("pi_text"), "contains"))
        for i, sec in enumerate(plot.get("sections") or []):
            summ = sec.get("summary") if isinstance(sec, dict) else None
            if isinstance(summ, dict) and summ.get("display_text") is not None:
                items.append(("sections[%d].summary.display_text" % i, summ.get("display_text"), "contains"))
        _ticks(plot.get("axis"), "axis", items)
    elif kind in ("funnel", "doi"):
        part = plot.get(kind) if isinstance(plot.get(kind), dict) else {}
        _ticks(part.get("axis"), kind + ".axis", items)
        _ticks(part.get("y_axis"), kind + ".y_axis", items)
        if kind == "doi" and part.get("lfk_text") is not None:
            items.append(("doi.lfk_text", part.get("lfk_text"), "contains"))
    elif kind == "cumulative":
        cum = plot.get("cumulative") if isinstance(plot.get("cumulative"), dict) else {}
        for i, e in enumerate(cum.get("entries") or []):
            if isinstance(e, dict):
                items.append(("cumulative.entries[%d].display_text" % i, e.get("display_text"), "contains"))
        _ticks(cum.get("axis"), "cumulative.axis", items)
    elif kind == "loo":
        for i, e in enumerate(plot.get("loo") or []):
            if isinstance(e, dict):
                items.append(("loo[%d].display_text" % i, e.get("display_text"), "contains"))
        _ticks(plot.get("loo_axis"), "loo_axis", items)
    elif kind == "bubble":
        bub = plot.get("bubble") if isinstance(plot.get("bubble"), dict) else {}
        _ticks(bub.get("axis") or bub.get("x_axis"), "bubble.x_axis", items)
        _ticks(bub.get("y_axis"), "bubble.y_axis", items)
    return [it for it in items if _variants(it[1])]


def numbers_check(texts, plot, kind):
    """{ok, checked, matched, mismatches[{field, expected}], kind} — a motorszövegek szó szerinti jelenléte az
    SVG szövegei között. ``ok`` None, ha a fajtához nincs ellenőrizhető motorszöveg (nem zöld, nem piros)."""
    elems = [norm(t) for t in texts or [] if isinstance(t, str)]
    elem_set = set(elems)
    items = expected_texts(plot, kind)
    mismatches = []
    matched = 0
    for field, value, mode in items:
        cands = _variants(value)
        if mode == "exact":
            hit = any(c in elem_set for c in cands)
        else:
            hit = any(c in e for c in cands for e in elems)
        if hit:
            matched += 1
        elif len(mismatches) < MAX_MISMATCHES:
            mismatches.append({"field": field, "expected": cands[0]})
    checked = len(items)
    return {"kind": kind, "checked": checked, "matched": matched, "mismatches": mismatches,
            "ok": (matched == checked) if checked else None}


def numbers_check_svg(data, plot, kind):
    """Az SVG bájtjaiból: numbers_check a <text>-elemeken. Hibánál SvgError."""
    return numbers_check(text_elements(parse_svg(data)), plot, kind)
