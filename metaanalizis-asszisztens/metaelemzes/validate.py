# -*- coding: utf-8 -*-
"""Adatvalidálás az elemzés előtt. Minden szabály kódja egyben tudásbázis-azonosító
(a `kb build` a RULES szótárt a decision_rule táblába tölti), így az ágensek a
döntéseiknél ugyanarra a szabályra hivatkoznak, amit a kód ellenőriz.
"""
import math
from collections import Counter

from .effect_sizes import REQUIRED_COLUMNS, CONTINUOUS, BINARY, PROPORTION, CORRELATION

# kód: (súlyosság, rövid cím, magyarázat/teendő, forrás)
RULES = {
    "V001": ("error", "Hiányzó kötelező oszlop",
             "A választott hatásmérethez szükséges oszlop hiányzik a táblából.", "engine"),
    "V002": ("error", "Hiányzó kötelező érték",
             "A vizsgálat kimarad az elemzésből; pótold a közleményből, a szerzőtől, vagy dokumentált "
             "konverzióval (medián/IQR, SE, CI → SD).", "Cochrane Handbook 6.5.2"),
    "V003": ("error", "Nem szám érték", "A cella nem értelmezhető számként.", "engine"),
    "V004": ("error", "Érvénytelen mintanagyság", "n < 2 vagy nem egész.", "engine"),
    "V005": ("error", "Nem pozitív SD", "Az SD <= 0; valószínű adatkinyerési hiba.", "engine"),
    "V006": ("error", "Érvénytelen eseményszám", "Az eseményszám negatív vagy nagyobb, mint n.", "engine"),
    "V007": ("warning", "Ismétlődő vizsgálat-azonosító",
             "Ugyanaz a címke többször: többkarú vizsgálat vagy kettős közlés? Az egységelemzési "
             "hibát kerüld (Cochrane 23.3.4).", "Cochrane Handbook 23.3"),
    "V008": ("warning", "Kettős nulla esemény",
             "Mindkét karban 0 (vagy 100%) esemény: OR/RR-nél kimarad, RD-nél bent marad. Ritka "
             "eseménynél fontold meg a Peto- vagy MH-módszert, és érzékenységi elemzést.",
             "Cochrane Handbook 10.4.4"),
    "V009": ("info", "Nulla cella", "Folytonossági korrekció (0,5) kerül alkalmazásra a vizsgálat "
             "hatásméreténél.", "Cochrane Handbook 10.4.4.1"),
    "V010": ("error", "Érvénytelen korreláció", "r a (-1, 1) intervallumon kívül.", "engine"),
    "V011": ("warning", "SD helyett SE gyanúja",
             "Az SD feltűnően kicsi, de SD·√n közel esik a többi vizsgálat SD-jéhez: valószínűleg "
             "SE-t közöltek. Ellenőrizd a forrást (táblázat lábjegyzete!).", "Cochrane Handbook 6.5.2.2"),
    "V012": ("warning", "Mértékegység-eltérés gyanúja",
             "Az átlag nagyságrendileg (>= 10x) eltér a többi vizsgálattól (pl. mg/dL vs mmol/L).",
             "Cochrane Handbook 6.5.2"),
    "V013": ("info", "Ferde eloszlás gyanúja",
             "Nemnegatív változónál átlag < 2·SD: ferde eloszlásra utal; a medián/IQR-ből becsült "
             "értékek és az átlagkülönbség óvatosan kezelendők.", "Cochrane Handbook 10.5.3"),
    "V014": ("warning", "Szélsőséges hatásméret",
             "|SMD| > 3 vagy |log OR/RR| > ln(20): gyakran adatkinyerési hiba (SE/SD csere, előjel, "
             "mértékegység).", "Borenstein 2009; gyakorlati szabály"),
    "V015": ("warning", "Kevés vizsgálat",
             "k < 5: a τ² becslése megbízhatatlan; HKSJ-CI és predikciós intervallum ajánlott, a "
             "véletlen hatású eredményt óvatosan értelmezd.", "Cochrane Handbook 10.10.4"),
    "V016": ("info", "Publikációs torzítás tesztje alulerőzött",
             "k < 10: funnel-aszimmetria tesztek (Egger, Begg) nem ajánlottak.", "Sterne et al. 2011 BMJ"),
    "V017": ("warning", "Közös kontrollkar többszöri használata",
             "Azonos study_id és azonos kontrollkar-adatok több sorban: a kontroll n-jét osztsd fel, "
             "vagy vond össze a kísérleti karokat.", "Cochrane Handbook 23.3.4"),
    "V018": ("info", "Becsült (imputált) értékek",
             "Becsült/imputált adatot tartalmazó vizsgálatok: érzékenységi elemzés javasolt nélkülük.",
             "Cochrane Handbook 6.5.2.10"),
    "V019": ("info", "Magas torzítási kockázatú vizsgálatok",
             "Érzékenységi elemzés javasolt a magas RoB-ú vizsgálatok kizárásával.", "Cochrane Handbook 7–8"),
    "V020": ("warning", "Nagyon kis mintájú vizsgálat",
             "n < 10 karonként: a normális közelítés és a variancia-becslés bizonytalan.", "Borenstein 2009 8. fejezet"),
}


def _finding(code, study=None, detail=""):
    sev, title, advice, source = RULES[code]
    return {"code": code, "severity": sev, "title": title, "study": study,
            "detail": detail, "advice": advice, "source": source}


def _median(vals):
    v = sorted(vals)
    n = len(v)
    if n == 0:
        return None
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def validate(rows, measure, meta=None):
    """Visszaad: findings lista (dict). Az 'error' súlyosságú tételek blokkolják az elemzést
    az adott vizsgálatra (az effect_sizes.compute kizárja), a többi figyelmeztetés."""
    out = []
    req = REQUIRED_COLUMNS.get(measure, ())
    present = set()
    for r in rows:
        present.update(k for k in r.keys())
    for col in req:
        if col not in present:
            out.append(_finding("V001", None, "hiányzó oszlop: %s" % col))
    if measure == "GEN" and "vi" not in present and "sei" not in present:
        out.append(_finding("V001", None, "GEN-hez vi vagy sei oszlop kell"))
    if meta:
        for pe in meta.get("parse_errors", []):
            out.append(_finding("V003", None, "%d. sor, %s oszlop: %r" % (pe["line"], pe["column"], pe["value"])))
    labels = [str(r.get("study") or "#%d" % i) for i, r in enumerate(rows, start=1)]
    for lab, cnt in Counter(labels).items():
        if cnt > 1:
            out.append(_finding("V007", lab, "%d sor ugyanazzal a címkével" % cnt))
    for lab, r in zip(labels, rows):
        missing = [c for c in req if c in present and r.get(c) is None]
        if missing:
            out.append(_finding("V002", lab, "hiányzik: " + ", ".join(missing)))
            continue
        if measure in CONTINUOUS:
            for nc in ("n1", "n2"):
                if r[nc] < 2 or r[nc] != int(r[nc]):
                    out.append(_finding("V004", lab, "%s = %r" % (nc, r[nc])))
            for sc in ("sd1", "sd2"):
                if r[sc] <= 0:
                    out.append(_finding("V005", lab, "%s = %r" % (sc, r[sc])))
            for mc, sc in (("m1", "sd1"), ("m2", "sd2")):
                if r[mc] >= 0 and r[sc] > 0 and r[mc] < 2 * r[sc]:
                    out.append(_finding("V013", lab, "%s=%g < 2·%s=%g" % (mc, r[mc], sc, 2 * r[sc])))
            if min(r["n1"], r["n2"]) < 10:
                out.append(_finding("V020", lab, "n1=%g, n2=%g" % (r["n1"], r["n2"])))
        elif measure in BINARY:
            ok = True
            for ec, nc in (("e1", "n1"), ("e2", "n2")):
                if r[nc] < 1 or r[nc] != int(r[nc]):
                    out.append(_finding("V004", lab, "%s = %r" % (nc, r[nc])))
                    ok = False
                if r[ec] < 0 or r[ec] > r[nc] or r[ec] != int(r[ec]):
                    out.append(_finding("V006", lab, "%s = %r, %s = %r" % (ec, r[ec], nc, r[nc])))
                    ok = False
            if ok:
                cells = (r["e1"], r["n1"] - r["e1"], r["e2"], r["n2"] - r["e2"])
                if (r["e1"] == 0 and r["e2"] == 0) or (cells[1] == 0 and cells[3] == 0):
                    out.append(_finding("V008", lab))
                elif min(cells) == 0:
                    out.append(_finding("V009", lab))
        elif measure in PROPORTION:
            if r["n"] < 1 or r["n"] != int(r["n"]):
                out.append(_finding("V004", lab, "n = %r" % r["n"]))
            elif r["x"] < 0 or r["x"] > r["n"]:
                out.append(_finding("V006", lab, "x = %r, n = %r" % (r["x"], r["n"])))
            elif r["x"] in (0, r["n"]):
                out.append(_finding("V009", lab, "x = %g / n = %g" % (r["x"], r["n"])))
        elif measure in CORRELATION:
            if not -1 < r["r"] < 1:
                out.append(_finding("V010", lab, "r = %r" % r["r"]))
            if r["n"] < 4:
                out.append(_finding("V004", lab, "n = %r" % r["n"]))
        if str(r.get("estimated") or "").strip().lower() in ("1", "1.0", "yes", "igen", "true", "y", "i"):
            out.append(_finding("V018", lab))
        if str(r.get("rob") or "").strip().lower() in ("high", "magas", "high risk"):
            out.append(_finding("V019", lab))
    # vizsgálatok közötti mintázatok (folytonos)
    if measure in CONTINUOUS:
        good = [(lab, r) for lab, r in zip(labels, rows)
                if all(r.get(c) is not None for c in req) and r["sd1"] > 0 and r["sd2"] > 0]
        sds = [r[c] for _, r in good for c in ("sd1", "sd2")]
        means = [abs(r[c]) for _, r in good for c in ("m1", "m2") if r[c] != 0]
        msd = _median(sds)
        mmean = _median(means)
        for lab, r in good:
            for sc, nc in (("sd1", "n1"), ("sd2", "n2")):
                if msd and r[sc] < 0.3 * msd and 0.5 * msd <= r[sc] * math.sqrt(r[nc]) <= 2.0 * msd:
                    out.append(_finding("V011", lab, "%s=%g, %s·√n=%.3g, medián SD=%.3g" %
                                        (sc, r[sc], sc, r[sc] * math.sqrt(r[nc]), msd)))
            for mc in ("m1", "m2"):
                if mmean and r[mc] != 0 and len(good) >= 3:
                    ratio = abs(r[mc]) / mmean
                    if ratio >= 10 or ratio <= 0.1:
                        out.append(_finding("V012", lab, "%s=%g, medián |átlag|=%.3g" % (mc, r[mc], mmean)))
        # közös kontroll
        if any(r.get("study_id") for _, r in good):
            seen = Counter((r.get("study_id"), r["m2"], r["sd2"], r["n2"]) for _, r in good if r.get("study_id"))
            for key, cnt in seen.items():
                if cnt > 1:
                    out.append(_finding("V017", str(key[0]), "%d sor ugyanazzal a kontrollkarral" % cnt))
    if measure in BINARY and any(r.get("study_id") for r in rows):
        seen = Counter((r.get("study_id"), r.get("e2"), r.get("n2")) for r in rows if r.get("study_id"))
        for key, cnt in seen.items():
            if cnt > 1:
                out.append(_finding("V017", str(key[0]), "%d sor ugyanazzal a kontrollkarral" % cnt))
    k_ok = len([1 for lab, r in zip(labels, rows) if all(r.get(c) is not None for c in req)])
    if 0 < k_ok < 5:
        out.append(_finding("V015", None, "k = %d" % k_ok))
    if 0 < k_ok < 10:
        out.append(_finding("V016", None, "k = %d" % k_ok))
    return out


def check_effect_sizes(es):
    """Hatásméret-szintű ellenőrzés (V014) a kiszámolt yi-k alapján."""
    out = []
    for lab, y in zip(es.labels, es.yi):
        if es.measure in ("SMD", "COHEN_D") and abs(y) > 3:
            out.append(_finding("V014", lab, "%s = %.3f" % (es.measure, y)))
        if es.measure in ("OR", "RR") and abs(y) > math.log(20):
            out.append(_finding("V014", lab, "log %s = %.3f" % (es.measure, y)))
    return out


def summarize(findings):
    c = Counter(f["severity"] for f in findings)
    return {"error": c.get("error", 0), "warning": c.get("warning", 0), "info": c.get("info", 0)}
