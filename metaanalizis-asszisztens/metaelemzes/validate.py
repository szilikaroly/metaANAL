# -*- coding: utf-8 -*-
"""Adatvalidálás az elemzés előtt. Minden szabály kódja egyben tudásbázis-azonosító
(a `kb build` a RULES szótárt a decision_rule táblába tölti), így az ágensek a
döntéseiknél ugyanarra a szabályra hivatkoznak, amit a kód ellenőriz.
"""
import math
from collections import Counter

from .effect_sizes import (REQUIRED_COLUMNS, CONTINUOUS, BINARY, PROPORTION, CORRELATION, PAIRED,
                           PAIRED_INPUTS)
from .tableio import values_equal, rob_category, yes_no

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
             "n < 1, korrelációnál n < 4, GEN-nél (ha megadod) n < 1. A vizsgálat kimarad az elemzésből.",
             "engine"),
    "V005": ("error", "Nem pozitív SD", "Az SD <= 0 (GEN-nél: negatív SE); valószínű adatkinyerési hiba.",
             "engine"),
    "V006": ("error", "Érvénytelen eseményszám", "Az eseményszám negatív vagy nagyobb, mint n.", "engine"),
    "V007": ("warning", "Ismétlődő vizsgálat-azonosító",
             "Ugyanaz a címke többször: többkarú vizsgálat vagy kettős közlés? Az egységelemzési "
             "hibát kerüld (Cochrane 23.3.4).", "Cochrane Handbook 23.3"),
    "V008": ("warning", "Kettős nulla esemény",
             "Mindkét karban 0 (vagy 100%) esemény: alapértelmezésben OR/RR-nél kimarad, RD-nél bent marad "
             "(--drop00; a tényleges kezelést a részletek mutatják). Ritka eseménynél fontold meg a Peto- "
             "vagy MH-módszert, és érzékenységi elemzést.",
             "Cochrane Handbook 10.4.4"),
    "V009": ("info", "Nulla cella", "Folytonossági korrekció kerül alkalmazásra a vizsgálat "
             "hatásméreténél (mértéke: --cc, alapértelmezés 0,5; lásd a részleteket).", "Cochrane Handbook 10.4.4.1"),
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
    # (V025: a munkapad-tervben foglalt kód)
    "V026": ("error", "Nincs elemezhető vizsgálat",
             "k = 0: minden sor kimaradt (validálási hiba, kettős nulla, nem számolható hatásméret) vagy "
             "a tábla üres; az elemzés nem futtatható. Lásd a kizárt sorokat és az okukat.", "engine"),
    "V027": ("warning", "Fel nem ismert kategóriaérték",
             "A rob / estimated cella értéke egyik elfogadott kategóriának sem felel meg, ezért a sor nem "
             "számít magas RoB-únak (V019, --exclude rob=high), illetve becsültnek (V018, --exclude "
             "estimated=igen). rob: low / some concerns / high (RoB 2), low / moderate / serious / critical "
             "/ no information (ROBINS-I), alacsony / közepes / magas ('… risk of bias' / '… kockázat' "
             "alakban is); estimated: igen / nem.", "Cochrane Handbook 8 (RoB 2), 25 (ROBINS-I)"),
    "V028": ("warning", "Ismétlődő oszlop",
             "Több fejléc illeszkedik ugyanarra az oszlopra (pl. kétszer 'm1', vagy 'mean1' és 'm1'): az "
             "elemzés az elsőt (a szinonimák közül az elsőbbségit) használja, a többi külön oszlopként "
             "marad. Töröld vagy nevezd át a fölösleges oszlopot.", "engine"),
    "V030": ("error", "Numerikusan kezelhetetlen variancia",
             "Egy vizsgálat mintavételi varianciája olyan kicsi (pl. vi = 1e-300), hogy a súlyok túlcsordulnának: "
             "az elemzés nem futtatható. Valószínű adatkinyerési hiba — ellenőrizd a vi/SE (vagy az SD) értékét.",
             "engine"),
    "V029": ("warning", "Hiányzó vizsgálat-címke",
             "A sornak nincs vizsgálat-címkéje (study / Szerző / author / label / ID oszlop vagy study_id), "
             "ezért sorszámot kap (#1, #2, …): a forest plot, a riport és a szűrők nem nevezik meg a "
             "vizsgálatot, és a sorszám a szűréstől függ. Adj címkeoszlopot (pl. 'Szerző, év').", "engine"),
}


def _az(n):
    """Névelő szám elé: 'az' (1, 5, 50–59, 500–599, 1000–1999 …: magánhangzóval ejtett), különben 'a'."""
    t = str(int(n))
    return "az" if t[0] == "5" or (t[0] == "1" and len(t) in (1, 4, 7)) else "a"


def _finding(code, study=None, detail=""):
    sev, title, advice, source = RULES[code]
    return {"code": code, "severity": sev, "title": title, "study": study,
            "detail": detail, "advice": advice, "source": source}


def _double_zero_detail(r, measure, opts, corrects):
    """A V008 részlete: mi történik TÉNYLEG a kettős nulla vizsgálattal ezekkel a beállításokkal
    (az effect_sizes.two_by_two logikája szerint: drop00, cc, cc_to)."""
    head = "e1 = %g/%g, e2 = %g/%g" % (r["e1"], r["n1"], r["e2"], r["n2"])
    drop = opts.get("drop00")
    if drop is None:
        drop = measure in ("OR", "RR")
    cc, cc_to = opts.get("cc", 0.5), opts.get("cc_to", "only0")
    if drop:
        return head + " — ebben az elemzésben kimarad (drop00)"
    if measure == "RD":
        if cc_to == "all" and cc > 0:
            how = "+%g korrekcióval minden cellához" % cc
        elif cc > 0 and cc_to != "none":
            how = "RD = 0, a 0 variancia %g-es korrekcióval számolva" % cc
        else:
            return head + " — 0 variancia miatt korrekció nélkül kimarad"
        return head + " — ebben az elemzésben bent marad (%s)" % how
    if corrects:
        return head + " — ebben az elemzésben bent marad (+%g korrekció minden cellához)" % cc
    return head + " — korrekció nélkül az %s nem számolható, ezért kimarad" % measure


def _median(vals):
    v = sorted(vals)
    n = len(v)
    if n == 0:
        return None
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


_COMPUTE_OPTIONS = ("label_col", "smd_vtype", "j_method", "cc", "cc_to", "drop00", "md_vtype",
                    "glass_vtype", "gen_smd_vtype")


def _alt_text(alts):
    return " | ".join("+".join(a) for a in alts)


def _first_complete(r, alts):
    """A párosított bemenet első teljes oszlopkészlete a sorban (vagy None)."""
    for cols in alts:
        if all(r.get(c) is not None for c in cols):
            return cols
    return None


def _paired_row_checks(out, lab, r, measure):
    """Párosított (MC, SMCC) sor: V002 hiányzó készlet, V004 n, V005 SD, V010 r, V020 kis n."""
    missing = []
    for part, name in (("mean", "átlagos változás"), ("sd", "a változás SD-je")):
        if _first_complete(r, PAIRED_INPUTS[part]) is None:
            missing.append("%s (%s)" % (name, _alt_text(PAIRED_INPUTS[part])))
    if missing:
        out.append(_finding("V002", lab, "hiányzik: " + "; ".join(missing)))
    n = r["n"]
    n_min = 3 if measure == "SMCC" else 2
    if n < n_min or not _is_int(n):
        out.append(_finding("V004", lab, "n = %g (párosított adatnál egész n >= %d kell)" % (n, n_min)))
    sd_cols = _first_complete(r, PAIRED_INPUTS["sd"])
    if sd_cols == ("sd_diff",) and r["sd_diff"] <= 0:
        out.append(_finding("V005", lab, "sd_diff = %g" % r["sd_diff"]))
    elif sd_cols == ("sd1", "sd2", "r"):
        for sc in ("sd1", "sd2"):
            if r[sc] <= 0:
                out.append(_finding("V005", lab, "%s = %g" % (sc, r[sc])))
        if not -1 <= r["r"] <= 1:
            out.append(_finding("V010", lab, "r = %g (a két mérés korrelációja: -1 <= r <= 1)" % r["r"]))
    elif sd_cols == ("sum_sq_dev_d",) and r["sum_sq_dev_d"] <= 0:
        out.append(_finding("V005", lab, "sum_sq_dev_d = %g (a változás SD-je nem pozitív)" % r["sum_sq_dev_d"]))
    if n < 10:
        out.append(_finding("V020", lab, "n=%g" % n))


def _yes(v):
    """igen/yes/y/i/true/1 — ugyanaz a normalizálás, mint az --exclude/--include szűrőknél."""
    return values_equal(v, "igen", "estimated")


def _high_rob(v):
    """high/magas/high risk/serious/súlyos — ugyanaz, mint a `--exclude rob=high` szűrőnél."""
    return values_equal(v, "high", "rob")


def _is_int(x):
    return math.isfinite(x) and x == int(x)


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


def blocking_rows(findings):
    """Sorindex (a validált `rows` listában) → a hibakódok szövege, a sorhoz kötött 'error' tételekből.
    Ezt kapja az effect_sizes.compute(skip_rows=...): ismétlődő címkéknél (több karú vizsgálat)
    csak a hibás sor marad ki, az azonos címkéjű érvényes sor nem."""
    out = {}
    for f in findings:
        if f.get("severity") == "error" and f.get("row") is not None:
            codes = out.setdefault(f["row"], [])
            if f["code"] not in codes:
                codes.append(f["code"])
    return {i: ", ".join(codes) for i, codes in out.items()}


def validate(rows, measure, meta=None, options=None):
    """Visszaad: findings lista (dict).

    Az 'error' súlyosságú, sorhoz kötött tételek (f['row'] = sorindex) blokkolják az adott sort:
    a hívó a blocking_rows(findings) szótárt adja át az effect_sizes.compute(skip_rows=...)
    paraméterének (a pipeline és a CLI így tesz). A globális (study=None) hibák (pl. V001)
    az egész elemzést érintik. A k-szabályok (V015/V016) az elemezhető k-ra vonatkoznak:
    a validálás a hatásméret-számítás kizárásait is figyelembe veszi (options: a compute
    beállításai, pl. cc, cc_to, drop00; alapértelmezés a motor alapbeállítása)."""
    from .effect_sizes import compute, row_label, EffectSizeError
    from .models import _check as _check_variances, ModelError
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
    gen_n = bool(opts.get("gen_smd_vtype")) and "n1" in present and "n2" in present
    gen_missing = measure == "GEN" and "vi" not in present and "sei" not in present and not gen_n
    if gen_missing:
        out.append(_finding("V001", None, "GEN-hez vi vagy sei oszlop kell" +
                            (" (vagy n1 és n2)" if opts.get("gen_smd_vtype") else "")))
    if measure in PAIRED and not missing_cols:
        for part, name in (("mean", "az átlagos változáshoz"), ("sd", "a változás SD-jéhez")):
            if not any(all(c in present for c in cols) for cols in PAIRED_INPUTS[part]):
                missing_cols.append(part)
                out.append(_finding("V001", None, "%s oszlop(ok) kell(enek): %s" % (
                    name, _alt_text(PAIRED_INPUTS[part]))))
    label_col = opts.get("label_col", "study")
    labels = [row_label(r, i, label_col) for i, r in enumerate(rows)]
    by_line = {r.get("_line"): lab for lab, r in zip(labels, rows) if r.get("_line") is not None}
    row_of_line = {r.get("_line"): i for i, r in enumerate(rows) if r.get("_line") is not None}
    for d in (meta or {}).get("duplicate_columns") or []:
        out.append(_finding("V028", None, "'%s' (%d. oszlop) ugyanarra illeszkedik (%s), mint '%s' (%d. oszlop): "
                            "%s %d. oszlop számít, %s %d. oszlop '%s' néven külön marad" % (
                                d["column"], d["position"], d["canonical"], d["used"], d["used_position"],
                                _az(d["used_position"]), d["used_position"], _az(d["position"]), d["position"],
                                d["key"])))
    unlabelled = [lab for lab, r in zip(labels, rows)
                  if r.get(label_col) is None or str(r.get(label_col)).strip() == ""]
    if unlabelled:
        out.append(_finding("V029", None, "%d sor címke nélkül: %s" % (
            len(unlabelled), ", ".join(unlabelled[:10]) + (", …" if len(unlabelled) > 10 else ""))))
    needed = set(req) | ({"vi", "sei"} if measure == "GEN" else set())
    if measure == "GEN" and opts.get("gen_smd_vtype"):
        needed |= {"n1", "n2"}
    if measure in PAIRED:
        needed |= {c for part in PAIRED_INPUTS.values() for cols in part for c in cols}
    ragged = set()
    # a szűrővel (--exclude/--include) eltávolított sorok beolvasási hibái már nem érintik az elemzést
    filtered = meta is not None and len(rows) < (meta.get("n_rows") or 0)
    if meta:
        mapping = meta.get("mapping") or {}
        for pe in meta.get("parse_errors", []):
            if filtered and pe.get("line") not in by_line:
                continue
            lab = by_line.get(pe.get("line"))
            ri = row_of_line.get(pe.get("line"))
            if pe.get("kind") == "ragged":
                out.append(_finding("V024", lab, "%d. sor: %s" % (pe["line"], pe.get("note", ""))))
                if ri is not None:
                    out[-1]["row"] = ri
                    ragged.add(ri)
                continue
            canon = mapping.get(pe["column"], pe["column"])
            detail = "%d. sor, %s oszlop: %r" % (pe["line"], pe["column"], pe["value"])
            if pe.get("note"):
                detail += " (%s)" % pe["note"]
            out.append(_finding("V003" if canon in needed else "V021", lab, detail))
            if ri is not None:
                out[-1]["row"] = ri
        for am in meta.get("ambiguous", []):
            if filtered and am.get("line") not in by_line:
                continue
            out.append(_finding("V023", by_line.get(am.get("line")),
                                "%d. sor, %s oszlop: %s" % (am["line"], am["column"], am["note"])))
            if row_of_line.get(am.get("line")) is not None:
                out[-1]["row"] = row_of_line[am["line"]]
    if missing_cols or gen_missing:
        return out       # a soronkénti ellenőrzésekhez hiányzik egy kötelező oszlop
    for lab, cnt in Counter(labels).items():
        if cnt > 1:
            out.append(_finding("V007", lab, "%d sor ugyanazzal a címkével" % cnt))
    if (meta or {}).get("label_column") and label_col == "study":
        # a címke a study_id-ből készült (tableio.read_table): a közös azonosító sorszám-utótagot kapott
        sids = Counter(str(r.get("study_id")).strip() for r in rows
                       if r.get("study_id") is not None and str(r.get("study_id")).strip())
        for sid, cnt in sids.items():
            if cnt > 1:
                out.append(_finding("V007", sid, "%d sor ugyanazzal a(z) %s azonosítóval (címkéjük: %s (1) … (%d))"
                                    % (cnt, meta["label_column"], sid, cnt)))
    for i, (lab, r) in enumerate(zip(labels, rows)):
        if i in ragged:
            continue
        start = len(out)
        _row_checks(out, lab, r, measure, req, opts, corrects)
        for f in out[start:]:
            f["row"] = i
    # vizsgálatok közötti mintázatok (folytonos)
    if measure in CONTINUOUS:
        good = [(lab, r) for i, (lab, r) in enumerate(zip(labels, rows)) if i not in ragged
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
        good_b = [(lab, r) for i, (lab, r) in enumerate(zip(labels, rows)) if i not in ragged
                  and all(r.get(c) is not None for c in req) and r["n2"] > 0]
        _shared_control(out, good_b, lambda r: (round(r["e2"] / r["n2"], 4),), "n2")
    # elemezhető k: a motor ugyanazokkal a kizárásokkal számol, mint az elemzés
    try:
        es = compute(rows, measure, skip_rows=blocking_rows(out), **opts)
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
    if k == 0:
        # k = 0-nál a kevés vizsgálatra vonatkozó tanács (V015/V016) értelmetlen: a V026 a hiba
        out.append(_finding("V026", None, "k = 0 elemezhető vizsgálat (%d sorból)" % n_rows))
        return out
    try:
        _check_variances(es.yi, es.vi, es.labels)
    except ModelError as exc:
        out.append(_finding("V030", None, str(exc)))
    if k < 5:
        out.append(_finding("V015", None, "k = %d elemezhető vizsgálat (%d sorból)" % (k, n_rows)))
    if k < 10:
        out.append(_finding("V016", None, "k = %d" % k))
    return out


def _row_checks(out, lab, r, measure, req, opts, corrects):
    """Egy sor ellenőrzései (V002–V010, V013, V018–V020, V027); a hívó a tételekhez a sorindexet is felírja."""
    missing = [c for c in req if r.get(c) is None]
    if measure == "GEN" and r.get("vi") is None and r.get("sei") is None and not (
            opts.get("gen_smd_vtype") and r.get("n1") is not None and r.get("n2") is not None):
        missing.append("vi/sei" + (" (vagy n1 és n2)" if opts.get("gen_smd_vtype") else ""))
    if missing:
        out.append(_finding("V002", lab, "hiányzik: " + ", ".join(missing)))
    if any(r.get(c) is None for c in req):
        return
    if measure in PAIRED:
        _paired_row_checks(out, lab, r, measure)
    elif measure in CONTINUOUS:
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
                out.append(_finding("V008", lab, _double_zero_detail(r, measure, opts, corrects)))
            elif min(cells) == 0 and corrects and (measure != "RD" or opts.get("cc_to") == "all"):
                # RD-nél alapértelmezésben nincs korrekció (effect_sizes.two_by_two)
                out.append(_finding("V009", lab, "+%g minden cellához" % opts.get("cc", 0.5)))
    elif measure in PROPORTION:
        if r["n"] < 1 or not _is_int(r["n"]):
            out.append(_finding("V004", lab, "n = %g (arány-adatnál egész n >= 1 kell)" % r["n"]))
        elif r["x"] < 0 or r["x"] > r["n"] or not _is_int(r["x"]):
            out.append(_finding("V006", lab, "x = %g, n = %g" % (r["x"], r["n"])))
        elif r["x"] in (0, r["n"]) and measure in ("PR", "PLN", "PLO") and corrects:
            out.append(_finding("V009", lab, "x = %g / n = %g; +%g korrekció" % (r["x"], r["n"],
                                                                               opts.get("cc", 0.5))))
    elif measure in CORRELATION:
        if not -1 < r["r"] < 1:
            out.append(_finding("V010", lab, "r = %g" % r["r"]))
        if r["n"] < 4 or not _is_int(r["n"]):
            out.append(_finding("V004", lab, "n = %g (korrelációnál egész n >= 4 kell)" % r["n"]))
    elif measure == "GEN":
        # ugyanazok a kizárások, mint az effect_sizes.compute GEN-ágában (ott csak V022 lenne belőlük)
        if r.get("vi") is None and r.get("sei") is not None and r["sei"] < 0:
            out.append(_finding("V005", lab, "sei = %g (negatív SE; valószínű adatkinyerési hiba)" % r["sei"]))
        if r.get("n") is not None and (r["n"] < 1 or not _is_int(r["n"])):
            out.append(_finding("V004", lab, "n = %g (GEN-nél egész n >= 1 kell)" % r["n"]))
        if opts.get("gen_smd_vtype") and r.get("vi") is None and r.get("sei") is None:
            for nc in ("n1", "n2"):
                if r.get(nc) is not None and (r[nc] < 1 or not _is_int(r[nc])):
                    out.append(_finding("V004", lab, "%s = %g (a --gen-smd-vtype varianciájához egész n >= 1 kell)"
                                        % (nc, r[nc])))
    if _yes(r.get("estimated")):
        out.append(_finding("V018", lab))
    if _high_rob(r.get("rob")):
        out.append(_finding("V019", lab))
    if rob_category(r.get("rob")) == "":
        out.append(_finding("V027", lab, "rob = %r" % str(r["rob"]).strip()))
    if yes_no(r.get("estimated")) == "":
        out.append(_finding("V027", lab, "estimated = %r" % str(r["estimated"]).strip()))


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
        if es.measure in ("SMD", "COHEN_D", "SMD_GLASS", "SMCC") and abs(y) > 3:
            out.append(_finding("V014", lab, "%s = %.3f" % (es.measure, y)))
        if es.measure in ("OR", "RR") and abs(y) > math.log(20):
            out.append(_finding("V014", lab, "log %s = %.3f" % (es.measure, y)))
    return out


def summarize(findings):
    c = Counter(f["severity"] for f in findings)
    return {"error": c.get("error", 0), "warning": c.get("warning", 0), "info": c.get("info", 0)}
