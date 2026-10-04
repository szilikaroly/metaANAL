# -*- coding: utf-8 -*-
"""Adatvalidálás az elemzés előtt. Minden szabály kódja egyben tudásbázis-azonosító
(a `kb build` a RULES szótárt a decision_rule táblába tölti), így az ágensek a
döntéseiknél ugyanarra a szabályra hivatkoznak, amit a kód ellenőriz.
"""
import math
from collections import Counter

from .effect_sizes import REQUIRED_COLUMNS, CONTINUOUS, BINARY, PROPORTION, CORRELATION
from .tableio import values_equal

# kód: (súlyosság, rövid cím, magyarázat/teendő, forrás)
RULES = {
    "V001": ("error", "Hiányzó kötelező oszlop",
             "A választott hatásmérethez szükséges oszlop hiányzik a táblából.", "engine"),
    "V002": ("error", "Hiányzó kötelező érték",
             "A vizsgálat kimarad az elemzésből; pótold a közleményből, a szerzőtől, vagy dokumentált "
             "konverzióval (medián/IQR, SE, CI → SD).", "Cochrane Handbook 6.5.2"),
    "V003": ("error", "Nem szám érték",
             "A hatásmérethez szükséges cella nem értelmezhető számként; a vizsgálat kimarad az "
             "elemzésből, amíg nem javítod.", "engine"),
    "V004": ("error", "Érvénytelen mintanagyság",
             "Nem egész n, vagy túl kicsi: folytonos adatnál n < 2 (karonként), bináris és arány-adatnál "
             "n < 1, korrelációnál n < 4. A vizsgálat kimarad az elemzésből.", "engine"),
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
             "vagy vond össze a kísérleti karokat. Ha a felosztás megtörtént, jelöld a sorokat "
             "control_split=igen oszloppal (ekkor a figyelmeztetés eltűnik).", "Cochrane Handbook 23.3.4"),
    "V018": ("info", "Becsült (imputált) értékek",
             "Becsült/imputált adatot tartalmazó vizsgálatok: érzékenységi elemzés javasolt nélkülük.",
             "Cochrane Handbook 6.5.2.10"),
    "V019": ("info", "Magas torzítási kockázatú vizsgálatok",
             "Érzékenységi elemzés javasolt a magas RoB-ú vizsgálatok kizárásával.", "Cochrane Handbook 7–8"),
    "V020": ("warning", "Nagyon kis mintájú vizsgálat",
             "n < 10 karonként: a normális közelítés és a variancia-becslés bizonytalan.", "Borenstein 2009 8. fejezet"),
    "V021": ("warning", "Nem szám érték nem kötelező oszlopban",
             "A cella nem értelmezhető számként (pl. év '2001a' helyett 'in press'). A választott "
             "hatásméretet nem érinti; ha az oszlopot moderátorként, alcsoportként vagy kumulatív "
             "rendezőkulcsként használod, javítsd.", "engine"),
    "V022": ("warning", "Kizárt sor a hatásméret-számításból",
             "A sor nem kerül be az elemzésbe (pl. ROM nem pozitív átlaggal, korreláció n <= 3, "
             "nulla cella korrekció nélkül); ellenőrizd az okát és a forrást.", "engine"),
    "V023": ("warning", "Kétértelmű számformátum",
             "A számot ezres tagolásként olvastuk (pl. '2,000' → 2000), mert tizedesjelként "
             "értelmetlen lenne vagy ellentmond a fájl tizedesjelének. Ellenőrizd a forrással.", "engine"),
    "V024": ("error", "Elcsúszott sor",
             "A sorban több cella van, mint a fejlécben (tipikusan idézőjel nélküli tizedesvessző "
             "vesszővel tagolt fájlban): az értékek elcsúsztak, a sor kimarad az elemzésből. Tedd "
             "idézőjelbe a tizedesvesszős számokat, vagy használj pontosvesszős tagolást.", "engine"),
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


_COMPUTE_OPTIONS = ("label_col", "smd_vtype", "j_method", "cc", "cc_to", "drop00")


def _yes(v):
    """igen/yes/y/i/true/1 — ugyanaz a normalizálás, mint az --exclude/--include szűrőknél."""
    return values_equal(v, "igen", "estimated")


def _high_rob(v):
    """high/magas/high risk/serious/súlyos — ugyanaz, mint a `--exclude rob=high` szűrőnél."""
    return values_equal(v, "high", "rob")


def _is_int(x):
    return x == int(x)


def blocking_labels(findings):
    """Azoknak a vizsgálatoknak a címkéi, amelyek vizsgálat-szintű 'error' tételt kaptak.
    Ezeket az effect_sizes.compute(skip_labels=...) kihagyja ("validálási hiba")."""
    return {f["study"] for f in findings if f.get("severity") == "error" and f.get("study") is not None}


def blocking_reasons(findings):
    """Mint a blocking_labels, de címke → a hibakódok szövege (pl. 'V004, V006')."""
    out = {}
    for f in findings:
        if f.get("severity") == "error" and f.get("study") is not None:
            codes = out.setdefault(f["study"], [])
            if f["code"] not in codes:
                codes.append(f["code"])
    return {lab: ", ".join(codes) for lab, codes in out.items()}


def validate(rows, measure, meta=None, options=None):
    """Visszaad: findings lista (dict).

    Az 'error' súlyosságú, vizsgálathoz kötött tételek blokkolják az adott vizsgálatot:
    a hívó a blocking_labels(findings) halmazt adja át az effect_sizes.compute(skip_labels=...)
    paraméterének (a pipeline és a CLI így tesz). A globális (study=None) hibák (pl. V001)
    az egész elemzést érintik. A k-szabályok (V015/V016) az elemezhető k-ra vonatkoznak:
    a validálás a hatásméret-számítás kizárásait is figyelembe veszi (options: a compute
    beállításai, pl. cc, cc_to, drop00; alapértelmezés a motor alapbeállítása)."""
    from .effect_sizes import compute, row_label, EffectSizeError
    opts = {k: v for k, v in (options or {}).items() if k in _COMPUTE_OPTIONS and v is not None}
    corrects = opts.get("cc", 0.5) > 0 and opts.get("cc_to", "only0") != "none"
    out = []
    req = REQUIRED_COLUMNS.get(measure, ())
    if meta and meta.get("mapping") is not None:
        present = set(meta["mapping"].values())          # a fejlécből (0 sor esetén is helyes)
    else:
        present = set()
        for r in rows:
            present.update(k for k in r.keys())
    missing_cols = [c for c in req if c not in present]
    for col in missing_cols:
        out.append(_finding("V001", None, "hiányzó oszlop: %s" % col))
    gen_missing = measure == "GEN" and "vi" not in present and "sei" not in present
    if gen_missing:
        out.append(_finding("V001", None, "GEN-hez vi vagy sei oszlop kell"))
    labels = [row_label(r, i, opts.get("label_col", "study")) for i, r in enumerate(rows)]
    by_line = {r.get("_line"): lab for lab, r in zip(labels, rows) if r.get("_line") is not None}
    needed = set(req) | ({"vi", "sei"} if measure == "GEN" else set())
    ragged = set()
    # a szűrővel (--exclude/--include) eltávolított sorok beolvasási hibái már nem érintik az elemzést
    filtered = meta is not None and len(rows) < (meta.get("n_rows") or 0)
    if meta:
        mapping = meta.get("mapping") or {}
        for pe in meta.get("parse_errors", []):
            if filtered and pe.get("line") not in by_line:
                continue
            lab = by_line.get(pe.get("line"))
            if pe.get("kind") == "ragged":
                out.append(_finding("V024", lab, "%d. sor: %s" % (pe["line"], pe.get("note", ""))))
                if lab is not None:
                    ragged.add(lab)
                continue
            canon = mapping.get(pe["column"], pe["column"])
            detail = "%d. sor, %s oszlop: %r" % (pe["line"], pe["column"], pe["value"])
            if pe.get("note"):
                detail += " (%s)" % pe["note"]
            if canon in needed:
                out.append(_finding("V003", lab, detail))
            else:
                out.append(_finding("V021", lab, detail))
        for am in meta.get("ambiguous", []):
            if filtered and am.get("line") not in by_line:
                continue
            out.append(_finding("V023", by_line.get(am.get("line")),
                                "%d. sor, %s oszlop: %s" % (am["line"], am["column"], am["note"])))
    if missing_cols or gen_missing:
        return out       # a soronkénti ellenőrzésekhez hiányzik egy kötelező oszlop
    for lab, cnt in Counter(labels).items():
        if cnt > 1:
            out.append(_finding("V007", lab, "%d sor ugyanazzal a címkével" % cnt))
    for lab, r in zip(labels, rows):
        if lab in ragged:
            continue
        missing = [c for c in req if r.get(c) is None]
        if measure == "GEN" and r.get("vi") is None and r.get("sei") is None:
            missing.append("vi/sei")
        if missing:
            out.append(_finding("V002", lab, "hiányzik: " + ", ".join(missing)))
        if any(r.get(c) is None for c in req):
            continue
        if measure in CONTINUOUS:
            for nc in ("n1", "n2"):
                if r[nc] < 2 or not _is_int(r[nc]):
                    out.append(_finding("V004", lab, "%s = %g (folytonos adatnál egész n >= 2 kell)" % (nc, r[nc])))
            for sc in ("sd1", "sd2"):
                if r[sc] <= 0:
                    out.append(_finding("V005", lab, "%s = %g" % (sc, r[sc])))
            for mc, sc in (("m1", "sd1"), ("m2", "sd2")):
                if r[mc] >= 0 and r[sc] > 0 and r[mc] < 2 * r[sc]:
                    out.append(_finding("V013", lab, "%s=%g < 2·%s=%g" % (mc, r[mc], sc, 2 * r[sc])))
            if min(r["n1"], r["n2"]) < 10:
                out.append(_finding("V020", lab, "n1=%g, n2=%g" % (r["n1"], r["n2"])))
        elif measure in BINARY:
            ok = True
            for ec, nc in (("e1", "n1"), ("e2", "n2")):
                if r[nc] < 1 or not _is_int(r[nc]):
                    out.append(_finding("V004", lab, "%s = %g (bináris adatnál egész n >= 1 kell)" % (nc, r[nc])))
                    ok = False
                if r[ec] < 0 or r[ec] > r[nc] or not _is_int(r[ec]):
                    out.append(_finding("V006", lab, "%s = %g, %s = %g" % (ec, r[ec], nc, r[nc])))
                    ok = False
            if ok:
                cells = (r["e1"], r["n1"] - r["e1"], r["e2"], r["n2"] - r["e2"])
                if (r["e1"] == 0 and r["e2"] == 0) or (cells[1] == 0 and cells[3] == 0):
                    out.append(_finding("V008", lab))
                elif min(cells) == 0 and corrects and (measure != "RD" or opts.get("cc_to") == "all"):
                    # RD-nél alapértelmezésben nincs korrekció (effect_sizes.two_by_two)
                    out.append(_finding("V009", lab))
        elif measure in PROPORTION:
            if r["n"] < 1 or not _is_int(r["n"]):
                out.append(_finding("V004", lab, "n = %g (arány-adatnál egész n >= 1 kell)" % r["n"]))
            elif r["x"] < 0 or r["x"] > r["n"] or not _is_int(r["x"]):
                out.append(_finding("V006", lab, "x = %g, n = %g" % (r["x"], r["n"])))
            elif r["x"] in (0, r["n"]) and measure in ("PR", "PLN", "PLO") and corrects:
                out.append(_finding("V009", lab, "x = %g / n = %g" % (r["x"], r["n"])))
        elif measure in CORRELATION:
            if not -1 < r["r"] < 1:
                out.append(_finding("V010", lab, "r = %g" % r["r"]))
            if r["n"] < 4 or not _is_int(r["n"]):
                out.append(_finding("V004", lab, "n = %g (korrelációnál egész n >= 4 kell)" % r["n"]))
        if _yes(r.get("estimated")):
            out.append(_finding("V018", lab))
        if _high_rob(r.get("rob")):
            out.append(_finding("V019", lab))
    # vizsgálatok közötti mintázatok (folytonos)
    if measure in CONTINUOUS:
        good = [(lab, r) for lab, r in zip(labels, rows) if lab not in ragged
                and all(r.get(c) is not None for c in req) and r["sd1"] > 0 and r["sd2"] > 0]
        sds = [r[c] for _, r in good for c in ("sd1", "sd2")]
        means = [abs(r[c]) for _, r in good for c in ("m1", "m2") if r[c] != 0]
        msd = _median(sds)
        mmean = _median(means)
        for lab, r in good:
            # V011: az SD feltűnően kicsi, SD·√n a tipikus SD körül van, ÉS a vizsgálaton belüli
            # mintázat is SE-re utal: vagy a másik kar SD-je is kicsi (mindkét karnál SE), vagy a
            # másik kar SD-je kb. √n-szerese (egyik karnál SE, a másiknál SD).
            for sc, nc, oc in (("sd1", "n1", "sd2"), ("sd2", "n2", "sd1")):
                if msd and r[sc] < 0.3 * msd and 0.5 * msd <= r[sc] * math.sqrt(r[nc]) <= 2.0 * msd:
                    other_small = r[oc] < 0.3 * msd
                    other_ratio = r[oc] / r[sc] >= math.sqrt(r[nc]) / 2.0
                    if other_small or other_ratio:
                        out.append(_finding("V011", lab, "%s=%g, %s·√n=%.3g, medián SD=%.3g, %s=%g" %
                                            (sc, r[sc], sc, r[sc] * math.sqrt(r[nc]), msd, oc, r[oc])))
            # V012: mértékegység-váltás az átlagot ÉS az SD-t is ugyanabba az irányba skálázza;
            # a nullához közeli (pl. változási pontszám) átlag önmagában nem mértékegység-jel.
            for mc, sc in (("m1", "sd1"), ("m2", "sd2")):
                if mmean and msd and r[mc] != 0 and len(good) >= 3:
                    ratio = abs(r[mc]) / mmean
                    sdr = r[sc] / msd
                    if (ratio >= 10 and sdr >= 3) or (ratio <= 0.1 and sdr <= 1.0 / 3):
                        out.append(_finding("V012", lab, "%s=%g, medián |átlag|=%.3g; %s/medián SD=%.2g" %
                                            (mc, r[mc], mmean, sc, sdr)))
        _shared_control(out, good, lambda r: (r["m2"], r["sd2"]), "n2")
    if measure in BINARY:
        good_b = [(lab, r) for lab, r in zip(labels, rows) if lab not in ragged
                  and all(r.get(c) is not None for c in req) and r["n2"] > 0]
        _shared_control(out, good_b, lambda r: (round(r["e2"] / r["n2"], 4),), "n2")
    # elemezhető k: a motor ugyanazokkal a kizárásokkal számol, mint az elemzés
    blocked = blocking_reasons(out)
    try:
        es = compute(rows, measure, skip_labels=blocked, **opts)
    except EffectSizeError:
        es = None
    k = len(es) if es is not None else 0
    if es is not None:
        explained = {f["study"] for f in out if f["code"] == "V008"}
        for lab, why in es.excluded:
            if why.startswith("validálási hiba") or lab in explained:
                continue
            out.append(_finding("V022", lab, why))
    n_rows = len(rows)
    if k < 5:
        out.append(_finding("V015", None, "k = %d elemezhető vizsgálat (%d sorból)" % (k, n_rows)))
    if k < 10:
        out.append(_finding("V016", None, "k = %d" % k))
    return out


def _shared_control(out, pairs, key_fn, n_col):
    """V017: azonos study_id és azonos kontrollkar-adatok (folytonosnál átlag+SD, binárisnál
    eseményarány; az n szándékosan nem része a kulcsnak, mert a felosztás megváltoztatja).
    A control_split=igen jelölésű sorok kimaradnak (a kontroll n-je már fel van osztva)."""
    groups = {}
    for lab, r in pairs:
        sid = r.get("study_id")
        if sid is None or str(sid).strip() == "":
            continue
        if _yes(r.get("control_split")):
            continue
        groups.setdefault((str(sid).strip(),) + tuple(key_fn(r)), []).append(r)
    for key, rs in groups.items():
        if len(rs) > 1:
            ns = [r.get(n_col) for r in rs]
            out.append(_finding("V017", key[0], "%d sor ugyanazzal a kontrollkarral (%s: %s = %g); ha a "
                                "kontroll n-jét már felosztottad, jelöld control_split=igen oszloppal" % (
                                    len(rs), n_col, " + ".join("%g" % n for n in ns), sum(ns))))


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
