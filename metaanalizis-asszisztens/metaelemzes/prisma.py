# -*- coding: utf-8 -*-
"""PRISMA folyamatábra-számok konzisztencia-ellenőrzése (PRISMA 2020, és a régi 2009-es sablon).

A PRISMA 2020 folyamatábra dobozai (Page et al. 2021, BMJ 372:n71, 1. ábra; a projekt
`02_szures/prisma_folyamat.md` sablonjának betűjelei):

  A1 identified_databases     adatbázisokból azonosított rekordok
  A2 identified_registers     regiszterekből azonosított rekordok
     identified_other         egyéb forrásból azonosított, a fő ágba OLVASZTOTT rekordok (2009-es
                              sablon „additional records”; composer identified_other)
  D1 duplicates_removed       szűrés előtt eltávolított duplikátumok
  D2 automation_removed       automatizált eszközzel alkalmatlannak jelölt rekordok
  D3 other_removed            egyéb okból eltávolított rekordok
  B  screened                 szűrt rekordok (cím/absztrakt)       B = A1 + A2 (+ egyéb) − D1 − D2 − D3
  C  excluded_screening       kizárt rekordok (cím/absztrakt)
  E  sought                   teljes szövegre keresett jelentések  E = B − C
  F  not_retrieved            nem elérhető jelentések
  G  assessed                 teljes szövegben értékelt jelentések G = E − F
  H  excluded_eligibility     kizárt jelentések (okokkal: excluded_eligibility_reasons {ok: n}; Σ = H)
  J  included_reports         bevont jelentések                    J = G − H (+ egyéb módszerek ága)
  I  included_studies         bevont vizsgálatok                   I ≤ J
     included_meta            ebből metaanalízisben (2009: „quantitative synthesis”) ≤ I

Egyéb módszerek külön ága (weboldalak, szervezetek, hivatkozáskövetés): other_methods_identified,
other_methods_sought, other_methods_not_retrieved, other_methods_assessed (= keresett − nem
elérhető), other_methods_excluded (+ other_methods_excluded_reasons); az ág bevont jelentései
(értékelt − kizárt) a J-be számítanak. Frissített áttekintésnél: previous_studies,
previous_reports, total_studies (= previous + I), total_reports (= previous + J).

A 2009-es sablonban nincs E/F és regiszter-doboz; a „records after duplicates removed”
(after_duplicates) és a „studies included in qualitative synthesis” (included_studies) van; a
hiányzó duplikátum-doboz 0-nak számít (levezetve: azonosított − duplikátum után).

Belépési pontok: check_flow(flow, template=None) → FlowCheck; from_composer(prisma-flow.json
szótár); parse_markdown_table(02_szures/prisma_folyamat.md szövege); load(path).
A tételkódok (P001…) a validate.RULES formátumát követik (súlyosság, cím, teendő, forrás).
"""
import json
import re

# kód: (súlyosság, rövid cím, magyarázat/teendő, forrás)
RULES = {
    "P001": ("error", "Érvénytelen PRISMA-szám",
             "Minden dobozérték nemnegatív egész szám (rekordok, jelentések, vizsgálatok darabszáma).",
             "PRISMA 2020 (Page et al. 2021) 1. ábra"),
    "P002": ("error", "A szűrt rekordok száma nem egyezik",
             "B = A1 + A2 (+ egyéb forrás) − D1 − D2 − D3: a szűrés előtt eltávolított (duplikátum, "
             "automatikus, egyéb) rekordokat mind fel kell tüntetni; a keresési naplóval vesd össze.",
             "PRISMA 2020 1. ábra; PRISMA-S"),
    "P003": ("error", "A teljes szövegre keresett jelentések száma nem egyezik",
             "E = B − C: a cím/absztrakt alapján ki nem zárt rekordok mindegyikét teljes szövegre kell "
             "keresni.", "PRISMA 2020 1. ábra"),
    "P004": ("error", "Az értékelt jelentések száma nem egyezik",
             "G = E − F: a keresett, de el nem ért teljes szövegeket a „nem elérhető” dobozban kell "
             "jelezni.", "PRISMA 2020 1. ábra"),
    "P005": ("error", "A bevont jelentések száma nem egyezik",
             "J = G − H (+ az egyéb módszerek ágának bevont jelentései): minden értékelt jelentés vagy "
             "kizárt (okkal), vagy bevont.", "PRISMA 2020 1. ábra"),
    "P006": ("error", "Több bevont vizsgálat, mint bevont jelentés",
             "I ≤ J: egy vizsgálatnak több jelentése (közleménye) lehet, fordítva nem. A vizsgálatokat és "
             "a jelentéseket külön számold.", "PRISMA 2020 1. ábra; Cochrane Handbook 4.6.2"),
    "P007": ("error", "A kizárási okok összege nem egyezik",
             "A teljes szöveg szintű kizárások okonkénti számainak összege = a kizárt jelentések száma "
             "(H). Minden kizárt jelentéshez egy fő ok tartozzon.", "PRISMA 2020 16a tétel"),
    "P008": ("warning", "Hiányzó kizárási okok",
             "A teljes szöveg szintű kizárásokat okonként kell közölni (folyamatábra vagy melléklet).",
             "PRISMA 2020 16a tétel"),
    "P009": ("error", "Egyéb módszerek ága: az értékelt jelentések száma nem egyezik",
             "Az egyéb módszerek (weboldal, szervezet, hivatkozáskövetés) ágában: értékelt = keresett − "
             "nem elérhető.", "PRISMA 2020 1. ábra"),
    "P010": ("error", "Több vizsgálat a metaanalízisben, mint a bevontak között",
             "A metaanalízisbe (kvantitatív szintézisbe) vont vizsgálatok a bevont vizsgálatok "
             "részhalmaza.", "PRISMA 2009 / 2020 1. ábra"),
    "P011": ("info", "Levezetett (nem közölt) doboz-érték",
             "Az érték a többi dobozból adódik (pl. hiányzó duplikátum-doboz = 0); a folyamatábrán "
             "érdemes kifejezetten feltüntetni.", "PRISMA 2020 1. ábra"),
    "P012": ("info", "Nem ellenőrizhető összefüggés",
             "Egy doboz hiányzik, ezért az összefüggés nem ellenőrizhető; töltsd ki a hiányzó dobozt.",
             "PRISMA 2020 1. ábra"),
    "P013": ("warning", "A szűrés nem lezárt",
             "Vannak még elbírálásra váró rekordok/jelentések; a végleges folyamatábra előtt döntsd el őket "
             "(vagy tüntesd fel „folyamatban lévő” dobozként).", "PRISMA 2020 1. ábra"),
    "P014": ("warning", "Kevesebb bevont vizsgálat, mint értékelt − kizárt (2009-es sablon)",
             "A 2009-es sablon a cikkeket és a vizsgálatokat nem választja szét: ha több cikk tartozik egy "
             "vizsgálathoz, ez magyarázza; PRISMA 2020-ban a jelentéseket (J) és a vizsgálatokat (I) "
             "külön dobozban add meg.", "PRISMA 2020 (Page et al. 2021) magyarázó cikk"),
    "P015": ("error", "Frissített áttekintés: az összesítés nem egyezik",
             "Összes bevont = korábbi változatban bevont + új (vizsgálatokra és jelentésekre külön).",
             "PRISMA 2020 1. ábra (frissített áttekintés)"),
    "P016": ("warning", "Egyéb módszerek ága: több keresett, mint azonosított",
             "Az egyéb módszerekkel azonosított rekordoknál több jelentést kerestél teljes szövegre; "
             "ellenőrizd a számokat.", "PRISMA 2020 1. ábra"),
}

# kanonikus mezők (PRISMA 2020 betűjelekkel; lásd a modul docstringjét)
COUNT_FIELDS = (
    "identified_databases", "identified_registers", "identified_other",
    "duplicates_removed", "automation_removed", "other_removed", "after_duplicates",
    "screened", "excluded_screening", "sought", "not_retrieved", "assessed",
    "excluded_eligibility", "included_reports", "included_studies", "included_meta", "awaiting",
    "other_methods_identified", "other_methods_sought", "other_methods_not_retrieved",
    "other_methods_assessed", "other_methods_excluded",
    "previous_studies", "previous_reports", "total_studies", "total_reports", "not_imported",
)
REASON_FIELDS = ("excluded_eligibility_reasons", "other_methods_excluded_reasons")

LETTERS = {"identified_databases": "A1", "identified_registers": "A2", "duplicates_removed": "D1",
           "automation_removed": "D2", "other_removed": "D3", "screened": "B", "excluded_screening": "C",
           "sought": "E", "not_retrieved": "F", "assessed": "G", "excluded_eligibility": "H",
           "included_reports": "J", "included_studies": "I"}

# bemeneti szinonimák → kanonikus mező (2009-es esetfájlok, composer prisma-flow.json, rövid nevek)
INPUT_ALIASES = {
    # PRISMA 2009 / esetfájlok
    "records_identified_database_searching": "identified_databases",
    "records_identified_databases": "identified_databases",
    "records_identified_registers": "identified_registers",
    "records_identified_other_sources": "identified_other",
    "records_after_duplicates_removed": "after_duplicates",
    "records_screened": "screened",
    "records_excluded_at_screening": "excluded_screening",
    "records_excluded": "excluded_screening",
    "reports_sought_for_retrieval": "sought",
    "reports_not_retrieved": "not_retrieved",
    "fulltext_assessed_for_eligibility": "assessed",
    "reports_assessed_for_eligibility": "assessed",
    "fulltext_excluded_with_reasons": "excluded_eligibility",
    "reports_excluded": "excluded_eligibility",
    "fulltext_excluded_reasons": "excluded_eligibility_reasons",
    "reports_excluded_reasons": "excluded_eligibility_reasons",
    "studies_included_qualitative_synthesis": "included_studies",
    "studies_included_in_review": "included_studies",
    "studies_included_quantitative_synthesis": "included_meta",
    "studies_included_meta_analysis": "included_meta",
    "reports_of_included_studies": "included_reports",
    # composer prisma-flow.json
    "dedup_removed": "duplicates_removed",
    "removed_before_screening_n": "other_removed",
    "sought_for_retrieval": "sought",
    "assessed_eligibility": "assessed",
    "included": "included_reports",
    "undecided": "awaiting",
    "retrieval_gap": "not_imported",
    # rövid alakok
    "excluded_reasons": "excluded_eligibility_reasons",
    "reasons": "excluded_eligibility_reasons",
}

# eredmény-útvonal szinonimák (a 2009-es esetfájlok 'expected.path' nevei) → érték-kulcs
RESULT_ALIASES = {
    "records_identified_total": "identified_total",
    "records_after_duplicates_removed": "after_duplicates",
    "records_screened": "screened",
    "fulltext_assessed": "assessed",
    "fulltext_excluded_sum_of_reasons": "excluded_eligibility_reasons_sum",
    "studies_included_qualitative": "included_studies",
    "studies_included_quantitative": "included_meta",
    "qualitative_only_not_meta_analysed": "included_not_meta",
}

_2009_KEYS = {"records_identified_other_sources", "records_after_duplicates_removed",
              "studies_included_qualitative_synthesis", "studies_included_quantitative_synthesis",
              "fulltext_assessed_for_eligibility", "after_duplicates"}
_2020_KEYS = {"identified_registers", "sought", "not_retrieved", "included_reports",
              "other_methods_identified", "sought_for_retrieval", "reports_sought_for_retrieval"}

TEMPLATES = ("PRISMA2020", "PRISMA2009")


class PrismaError(ValueError):
    pass


def _finding(code, detail="", fields=()):
    sev, title, advice, source = RULES[code]
    return {"code": code, "severity": sev, "title": title, "study": None, "detail": detail,
            "advice": advice, "source": source, "fields": list(fields)}


def _label(field):
    letter = LETTERS.get(field)
    return "%s (%s)" % (field, letter) if letter else field


class FlowCheck(object):
    """A check_flow eredménye.

    template: 'PRISMA2020' | 'PRISMA2009'; counts: a (kanonikus nevű) bemeneti értékek;
    reasons: kizárási okok mezőnként; derived: levezetett értékek (identified_total,
    removed_before_screening_total, a hiányzó dobozok levezetett értékei, okösszegek ...);
    findings: P-kódos tételek; ok: nincs 'error' tétel.
    Az értékek attribútumként vagy value(név)-vel is elérhetők (kanonikus név, bemeneti
    szinonima vagy a 2009-es esetfájlok útvonal-neve), pl. res.records_screened."""

    def __init__(self, template, counts, reasons, derived, findings):
        self.template = template
        self.counts = counts
        self.reasons = reasons
        self.derived = derived
        self.findings = findings

    @property
    def ok(self):
        return not any(f["severity"] == "error" for f in self.findings)

    def summary(self):
        out = {"error": 0, "warning": 0, "info": 0}
        for f in self.findings:
            out[f["severity"]] = out.get(f["severity"], 0) + 1
        return out

    def value(self, name):
        key = RESULT_ALIASES.get(name, INPUT_ALIASES.get(name, name))
        if self.counts.get(key) is not None:
            return self.counts[key]
        if self.derived.get(key) is not None:
            return self.derived[key]
        if key in self.reasons:
            return self.reasons[key]
        return None

    def __getattr__(self, name):
        if name.startswith("_") or name in ("template", "counts", "reasons", "derived", "findings"):
            raise AttributeError(name)
        v = self.value(name)
        if v is None:
            raise AttributeError("nincs ilyen (vagy kitöltetlen) PRISMA-érték: %s" % name)
        return v

    def to_dict(self):
        return {"template": self.template, "ok": self.ok, "summary": self.summary(),
                "counts": dict(self.counts), "reasons": {k: dict(v) for k, v in self.reasons.items()},
                "derived": dict(self.derived), "findings": list(self.findings)}


# ----------------------------------------------------------------- bemenet
def _as_count(v):
    """Szám → int; None/üres → None; érvénytelen → ValueError (az ok szövegével)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        raise ValueError("logikai érték, nem szám")
    if isinstance(v, str):
        t = v.strip().replace(" ", "").replace(" ", "")
        try:
            v = float(t.replace(",", ".")) if re.match(r"^[+-]?\d+([.,]\d+)?$", t) else float(t)
        except ValueError:
            raise ValueError("nem szám: %r" % v)
    if not isinstance(v, (int, float)):
        raise ValueError("nem szám: %r" % (v,))
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError("nem véges szám")
    if v < 0:
        raise ValueError("negatív: %g" % v)
    if v != int(v):
        raise ValueError("nem egész: %g" % v)
    return int(v)


def _reasons_dict(v):
    """{ok: n} | [{"reason": .., "count": ..}] | [(ok, n)] → {ok: n (nyers)}."""
    if v is None:
        return None
    if isinstance(v, dict):
        return dict(v)
    out = {}
    for item in v:
        if isinstance(item, dict):
            key = item.get("reason") or item.get("ok") or item.get("label") or "ok nélkül"
            out[key] = out.get(key, 0) + (item.get("count", item.get("n")) or 0)
        else:
            key, n = item
            out[key] = out.get(key, 0) + n
    return out


def normalize(flow):
    """Bemeneti szótár → (kanonikus értékek, okok, a felismert eredeti kulcsok halmaza).

    Ismeretlen kulcsokat figyelmen kívül hagy (pl. composer 'databases', 'identified_total')."""
    vals, reasons, seen = {}, {}, set()
    for key, v in (flow or {}).items():
        canon = INPUT_ALIASES.get(key, key)
        if canon in REASON_FIELDS:
            reasons[canon] = _reasons_dict(v)
            seen.add(key)
        elif canon in COUNT_FIELDS:
            if canon in vals and vals[canon] is not None and v is None:
                continue
            vals[canon] = v
            seen.add(key)
        elif key == "removed_before_screening" and isinstance(v, list):
            # composer: [{"reason", "count"}]; csak ha a removed_before_screening_n hiányzik
            vals.setdefault("other_removed", sum((x.get("count") or 0) for x in v if isinstance(x, dict)))
            seen.add(key)
    return vals, reasons, seen


def detect_template(flow):
    keys = set(flow or {})
    if keys & _2020_KEYS:
        return "PRISMA2020"
    if keys & _2009_KEYS:
        return "PRISMA2009"
    return "PRISMA2020"


# --------------------------------------------------------------- ellenőrzés
def check_flow(flow, template=None):
    """PRISMA folyamatábra-számok ellenőrzése → FlowCheck.

    flow: szótár kanonikus vagy szinonim mezőnevekkel (lásd INPUT_ALIASES; composer
    prisma-flow.json is jó közvetlenül). template: 'PRISMA2020' | 'PRISMA2009' | None (felismerés).
    Ellenőrzések: nemnegatív egészek (P001); B = A1 + A2 (+ egyéb) − D1 − D2 − D3 (P002);
    E = B − C (P003); G = E − F (P004); J = G − H (+ egyéb ág) (P005); I ≤ J (P006);
    Σ okok = H (P007, P008); egyéb ág (P009, P016); metaanalízis ≤ I (P010); frissített
    áttekintés (P015); a hiányzó dobozok levezetése (P011) vagy jelzése (P012)."""
    tpl = (template or detect_template(flow)).upper().replace(" ", "").replace("_", "")
    if tpl not in TEMPLATES:
        raise PrismaError("ismeretlen sablon: %r (lehetséges: %s)" % (template, ", ".join(TEMPLATES)))
    raw, raw_reasons, _ = normalize(flow)
    findings = []
    counts = {}
    for key in COUNT_FIELDS:
        if key not in raw:
            continue
        try:
            counts[key] = _as_count(raw[key])
        except ValueError as exc:
            counts[key] = None
            findings.append(_finding("P001", "%s: %s" % (_label(key), exc), [key]))
    reasons = {}
    for rf, rv in raw_reasons.items():
        if rv is None:
            continue
        clean = {}
        for k, v in rv.items():
            try:
                cnt = _as_count(v)
            except ValueError as exc:
                findings.append(_finding("P001", "%s[%s]: %s" % (rf, k, exc), [rf]))
                continue
            if cnt is not None:
                clean[str(k)] = cnt
        reasons[rf] = clean
    c = counts.get
    derived = {}
    unverifiable = []

    def need(fields, what):
        miss = [f for f in fields if c(f) is None and derived.get(f) is None]
        if miss:
            unverifiable.append("%s (hiányzik: %s)" % (what, ", ".join(_label(f) for f in miss)))
            return False
        return True

    # --- azonosítás és szűrés előtti eltávolítás
    ident_parts = [c(k) for k in ("identified_databases", "identified_registers", "identified_other")]
    if any(v is not None for v in ident_parts):
        derived["identified_total"] = sum(v for v in ident_parts if v is not None)
    ident = derived.get("identified_total")
    removed_keys = ("duplicates_removed", "automation_removed", "other_removed")
    if tpl == "PRISMA2009":
        # duplikátum után = azonosított − duplikátum; a hiányzó duplikátum-doboz levezethető
        dup, after = c("duplicates_removed"), c("after_duplicates")
        if dup is None and ident is not None and after is not None:
            derived["duplicates_removed"] = ident - after
            if ident - after >= 0:
                findings.append(_finding("P011", "duplicates_removed (D1) = %d − %d = %d (nincs duplikátum-doboz)"
                                         % (ident, after, ident - after), ["duplicates_removed"]))
            else:
                findings.append(_finding("P002", "a duplikátumszűrés után több rekord (%d), mint azonosított (%d)"
                                         % (after, ident), ["after_duplicates", "identified_total"]))
        elif dup is not None and ident is not None and after is not None and ident - dup != after:
            findings.append(_finding("P002", "after_duplicates = %d, de azonosított − duplikátum = %d − %d = %d"
                                     % (after, ident, dup, ident - dup), ["after_duplicates", "duplicates_removed"]))
        elif after is None and ident is not None and dup is not None:
            derived["after_duplicates"] = ident - dup
        base = after if after is not None else derived.get("after_duplicates")
        other_rm = sum(c(k) or 0 for k in ("automation_removed", "other_removed"))
        if base is not None and c("screened") is not None:
            if base - other_rm != c("screened"):
                findings.append(_finding("P002", "screened (B) = %d, de duplikátum után − egyéb eltávolítás = %d − %d = %d"
                                         % (c("screened"), base, other_rm, base - other_rm),
                                         ["screened", "after_duplicates"]))
        elif base is not None and c("screened") is None:
            derived["screened"] = base - other_rm
            findings.append(_finding("P011", "screened (B) = %d (a duplikátumszűrés utáni számból)"
                                     % derived["screened"], ["screened"]))
        else:
            need(("screened",) if base is not None else ("after_duplicates",), "B = duplikátum után − egyéb eltávolítás")
    else:
        removed = [c(k) for k in removed_keys]
        derived["removed_before_screening_total"] = sum(v for v in removed if v is not None)
        no_removal_box = all(c(k) is None for k in removed_keys)
        if ident is not None and c("screened") is not None and no_removal_box and ident >= c("screened"):
            # nincs eltávolítás-doboz: a különbség csak levezethető, nem ellenőrizhető
            derived["removed_before_screening_total"] = ident - c("screened")
            derived["screened_expected"] = c("screened")
            if ident > c("screened"):
                findings.append(_finding("P011", "D1 + D2 + D3 = azonosított − B = %d − %d = %d (a szűrés előtti "
                                         "eltávolítás dobozai hiányoznak)" % (ident, c("screened"),
                                                                             ident - c("screened")),
                                         list(removed_keys)))
            else:
                findings.append(_finding("P011", "D1 + D2 + D3 = 0 (nincs eltávolítás-doboz)", list(removed_keys)))
        elif ident is not None and c("screened") is not None:
            exp_b = ident - derived["removed_before_screening_total"]
            derived["screened_expected"] = exp_b
            if exp_b != c("screened"):
                parts = " + ".join("%s %d" % (LETTERS.get(k, k), c(k)) for k in
                                   ("identified_databases", "identified_registers", "identified_other")
                                   if c(k) is not None)
                rm = " − ".join("%s %d" % (LETTERS[k], c(k)) for k in removed_keys if c(k) is not None)
                detail = "screened (B) = %d, de %s%s = %d" % (c("screened"), parts, (" − " + rm) if rm else "", exp_b)
                if c("not_imported"):
                    detail += "; %d azonosított rekord nem került a szűrt halmazba (retrieval_gap: " \
                              "retmax-korlát vagy be nem hozott rekordok?)" % c("not_imported")
                findings.append(_finding("P002", detail, ["screened"] + list(removed_keys)))
        else:
            need(("identified_databases", "screened") if ident is None else ("screened",),
                 "B = A1 + A2 − D1 − D2 − D3")
    b = c("screened") if c("screened") is not None else derived.get("screened")

    # --- szűrés → teljes szöveg
    if tpl == "PRISMA2020" or c("sought") is not None:
        if b is not None and c("excluded_screening") is not None and c("sought") is not None:
            if b - c("excluded_screening") != c("sought"):
                findings.append(_finding("P003", "sought (E) = %d, de B − C = %d − %d = %d" % (
                    c("sought"), b, c("excluded_screening"), b - c("excluded_screening")),
                    ["sought", "screened", "excluded_screening"]))
        elif b is not None and c("excluded_screening") is not None and c("sought") is None:
            derived["sought"] = b - c("excluded_screening")
            findings.append(_finding("P011", "sought (E) = B − C = %d" % derived["sought"], ["sought"]))
        else:
            need(("screened", "excluded_screening", "sought"), "E = B − C")
        e = c("sought") if c("sought") is not None else derived.get("sought")
        f_ = c("not_retrieved")
        if e is not None and c("assessed") is not None:
            if f_ is None:
                derived["not_retrieved"] = e - c("assessed")
                if e - c("assessed") < 0:
                    findings.append(_finding("P004", "assessed (G) = %d > sought (E) = %d" % (c("assessed"), e),
                                             ["assessed", "sought"]))
                else:
                    findings.append(_finding("P011", "not_retrieved (F) = E − G = %d" % derived["not_retrieved"],
                                             ["not_retrieved"]))
            elif e - f_ != c("assessed"):
                findings.append(_finding("P004", "assessed (G) = %d, de E − F = %d − %d = %d" % (
                    c("assessed"), e, f_, e - f_), ["assessed", "sought", "not_retrieved"]))
        elif e is not None and c("assessed") is None and f_ is not None:
            derived["assessed"] = e - f_
            findings.append(_finding("P011", "assessed (G) = E − F = %d" % derived["assessed"], ["assessed"]))
        else:
            need(("sought", "not_retrieved", "assessed"), "G = E − F")
    else:
        # 2009: nincs E/F doboz — G = B − C
        if b is not None and c("excluded_screening") is not None and c("assessed") is not None:
            if b - c("excluded_screening") - (c("not_retrieved") or 0) != c("assessed"):
                findings.append(_finding("P003", "fulltext assessed (G) = %d, de B − C = %d − %d = %d" % (
                    c("assessed"), b, c("excluded_screening"), b - c("excluded_screening")),
                    ["assessed", "screened", "excluded_screening"]))
        elif b is not None and c("excluded_screening") is not None and c("assessed") is None:
            derived["assessed"] = b - c("excluded_screening")
            findings.append(_finding("P011", "assessed (G) = B − C = %d" % derived["assessed"], ["assessed"]))
        else:
            need(("screened", "excluded_screening", "assessed"), "G = B − C")
    g = c("assessed") if c("assessed") is not None else derived.get("assessed")

    # --- kizárási okok
    h = c("excluded_eligibility")
    for rf, hf in (("excluded_eligibility_reasons", "excluded_eligibility"),
                   ("other_methods_excluded_reasons", "other_methods_excluded")):
        rs = reasons.get(rf)
        total = c(hf)
        if rs:
            s = sum(rs.values())
            derived[rf + "_sum"] = s
            if total is not None and s != total:
                findings.append(_finding("P007", "%s = %d, de az okok összege %s = %d" % (
                    _label(hf), total, " + ".join("%s %d" % (k, v) for k, v in rs.items()), s), [hf, rf]))
            elif total is None:
                derived[hf] = s
                findings.append(_finding("P011", "%s = Σ okok = %d" % (_label(hf), s), [hf]))
        elif total:
            findings.append(_finding("P008", "%s = %d, okonkénti bontás nélkül" % (_label(hf), total), [hf, rf]))
    if h is None:
        h = derived.get("excluded_eligibility")

    # --- egyéb módszerek ága
    om = {k: c("other_methods_" + k) for k in ("identified", "sought", "not_retrieved", "assessed", "excluded")}
    other_j = None
    if any(v is not None for v in om.values()):
        if om["identified"] is not None and om["sought"] is not None and om["sought"] > om["identified"]:
            findings.append(_finding("P016", "other_methods_sought = %d > other_methods_identified = %d" % (
                om["sought"], om["identified"]), ["other_methods_sought", "other_methods_identified"]))
        if om["sought"] is not None and om["assessed"] is not None:
            nr = om["not_retrieved"] or 0
            if om["sought"] - nr != om["assessed"]:
                findings.append(_finding("P009", "other_methods_assessed = %d, de keresett − nem elérhető = %d − %d = %d"
                                         % (om["assessed"], om["sought"], nr, om["sought"] - nr),
                                         ["other_methods_assessed", "other_methods_sought",
                                          "other_methods_not_retrieved"]))
        om_excl = om["excluded"] if om["excluded"] is not None else derived.get("other_methods_excluded")
        if om["assessed"] is not None and om_excl is not None:
            other_j = om["assessed"] - om_excl
            derived["other_methods_included_reports"] = other_j
        else:
            need(("other_methods_assessed", "other_methods_excluded"), "egyéb ág: bevont = értékelt − kizárt")

    # --- bevonás
    i_ = c("included_studies")
    j_ = c("included_reports")
    if g is not None and h is not None:
        main_j = g - h
        derived["included_reports_expected"] = main_j + (other_j or 0)
        awaiting = c("awaiting") or 0
        if tpl == "PRISMA2009" and j_ is None:
            # a 2009-es sablon a bevont vizsgálatokat adja (cikk ≠ vizsgálat)
            if i_ is not None:
                exp = derived["included_reports_expected"] - awaiting
                if i_ > exp:
                    findings.append(_finding("P005", "included studies (I) = %d > G − H = %d − %d = %d" % (
                        i_, g, h, exp), ["included_studies", "assessed", "excluded_eligibility"]))
                elif i_ < exp:
                    findings.append(_finding("P014", "included studies (I) = %d < G − H = %d − %d = %d" % (
                        i_, g, h, exp), ["included_studies", "assessed", "excluded_eligibility"]))
            else:
                need(("included_studies",), "I ≤ G − H")
        elif j_ is not None:
            if j_ + awaiting != derived["included_reports_expected"]:
                txt = "included_reports (J) = %d%s, de G − H = %d − %d = %d" % (
                    j_, (" + elbírálásra vár %d" % awaiting) if awaiting else "", g, h, main_j)
                if other_j is not None:
                    txt += " (+ egyéb ág %d = %d)" % (other_j, derived["included_reports_expected"])
                findings.append(_finding("P005", txt, ["included_reports", "assessed", "excluded_eligibility"]))
        else:
            derived["included_reports"] = derived["included_reports_expected"] - awaiting
            if i_ is None or i_ <= derived["included_reports"]:
                findings.append(_finding("P011", "included_reports (J) = G − H%s = %d" % (
                    " + egyéb ág" if other_j is not None else "", derived["included_reports"]),
                    ["included_reports"]))
    else:
        need(("assessed", "excluded_eligibility"), "J = G − H")
    j_eff = j_ if j_ is not None else (derived.get("included_reports") if tpl == "PRISMA2020" else None)
    if i_ is not None and j_eff is not None and i_ > j_eff:
        findings.append(_finding("P006", "included_studies (I) = %d > included_reports (J) = %d" % (i_, j_eff),
                                 ["included_studies", "included_reports"]))
    if c("awaiting"):
        findings.append(_finding("P013", "%d elbírálásra vár" % c("awaiting"), ["awaiting"]))
    if c("included_meta") is not None:
        if i_ is not None:
            derived["included_not_meta"] = i_ - c("included_meta")
            if c("included_meta") > i_:
                findings.append(_finding("P010", "metaanalízisben %d > bevont vizsgálat %d" % (c("included_meta"), i_),
                                         ["included_meta", "included_studies"]))
        else:
            need(("included_studies",), "metaanalízisben ≤ I")

    # --- frissített áttekintés
    for tot, prev, new, name in (("total_studies", "previous_studies", i_, "vizsgálat"),
                                 ("total_reports", "previous_reports", j_eff, "jelentés")):
        if c(tot) is not None and c(prev) is not None and new is not None and c(prev) + new != c(tot):
            findings.append(_finding("P015", "%s = %d, de korábbi + új = %d + %d = %d (%s)" % (
                tot, c(tot), c(prev), new, c(prev) + new, name), [tot, prev]))
    if unverifiable:
        findings.append(_finding("P012", "; ".join(unverifiable)))
    return FlowCheck(tpl, counts, reasons, derived, findings)


# ------------------------------------------------------------- betöltők
def from_composer(data):
    """A composer plugin `prisma export --format flow-json` kimenete (prisma-flow.json) → flow szótár.

    Mezők: identified_databases, identified_registers, identified_other (a composer a fő ágba
    olvasztja), dedup_removed (D1), removed_before_screening_n (D3, egyéb ok), screened,
    excluded_screening, sought_for_retrieval, not_retrieved, assessed_eligibility,
    excluded_eligibility, excluded_eligibility_reasons, included (bevont REKORD = jelentés, J),
    undecided (elbírálásra vár), retrieval_gap (azonosított, de be nem hozott)."""
    flow, _, _ = normalize(data)
    reasons = (data or {}).get("excluded_eligibility_reasons")
    if reasons is not None:
        flow["excluded_eligibility_reasons"] = _reasons_dict(reasons)
    return flow


_MD_LETTER = re.compile(r"\(\s*n\s*=\s*([A-Z]\d?)")
_INT = re.compile(r"(?<![\w.,])\d+(?![\w.,]?\d)")
_LETTER_FIELD = {"A1": "identified_databases", "A2": "identified_registers", "B": "screened",
                 "C": "excluded_screening", "E": "sought", "F": "not_retrieved", "G": "assessed",
                 "H": "excluded_eligibility", "J": "included_reports", "I": "included_studies"}


def _ints(text):
    return [int(x) for x in _INT.findall(text or "")]


def parse_markdown_table(text):
    """A projekt `02_szures/prisma_folyamat.md` táblázata (| Lépés | Szám | Ellenőrzés |) → flow szótár.

    A sorokat a Lépés-cella „(n = A1)” stb. betűjele azonosítja; a Szám-cella első egész száma
    az érték. Különleges sorok: a D1/D2/D3 sor (Szám: „12 / 0 / 3” vagy „D1=12; D2=0; D3=3”),
    a bevont sor (J és I: „8 / 6” vagy „J=8, I=6”), a kizárt sor okai („9 (ok A: 5; ok B: 4)”),
    az „Ebből metaanalízisben” sor és az „Egyéb forrásból” sor (other_methods_identified).
    Az üres Szám-cellák kimaradnak (a check_flow P012-vel jelzi őket)."""
    flow = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [x.strip() for x in line.strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        step, num = cells[0], cells[1]
        low = step.lower()
        if not num or low.startswith("lépés"):
            continue
        if "d1" in low and ("d2" in low or "d3" in low):
            tagged = dict((k.upper(), int(v)) for k, v in re.findall(r"\b(D[123])\s*[=:]\s*(\d+)", num, re.I))
            vals = _ints(re.sub(r"\bD[123]\s*[=:]", " ", num, flags=re.I)) if not tagged else []
            for i, k in enumerate(("D1", "D2", "D3")):
                v = tagged.get(k, vals[i] if i < len(vals) else None)
                if v is not None:
                    flow[{"D1": "duplicates_removed", "D2": "automation_removed", "D3": "other_removed"}[k]] = v
            continue
        letters = _MD_LETTER.findall(step)
        if "J" in letters and "I" in letters:
            tagged = dict((k.upper(), int(v)) for k, v in re.findall(r"\b([JI])\s*[=:]\s*(\d+)", num))
            vals = _ints(num) if not tagged else []
            j = tagged.get("J", vals[0] if vals else None)
            i = tagged.get("I", vals[1] if len(vals) > 1 else None)
            if j is not None:
                flow["included_reports"] = j
            if i is not None:
                flow["included_studies"] = i
            continue
        if letters and letters[0] in _LETTER_FIELD:
            vals = _ints(num)
            if not vals:
                continue
            field = _LETTER_FIELD[letters[0]]
            flow[field] = vals[0]
            if field == "excluded_eligibility":
                rs = re.findall(r"([^;:()=,]+?)\s*[:=]\s*(\d+)", num)
                rs = [(k.strip(), int(v)) for k, v in rs if k.strip().lower() not in ("h", "n")]
                if rs:
                    flow["excluded_eligibility_reasons"] = dict(rs)
            continue
        if "metaanal" in low:
            vals = _ints(num)
            if vals:
                flow["included_meta"] = vals[0]
        elif "egyéb forrás" in low or "egyeb forras" in low:
            vals = _ints(num)
            if vals:
                flow["other_methods_identified"] = vals[0]
    return flow


def load(path, template=None):
    """prisma-flow.json (composer) vagy prisma_folyamat.md → FlowCheck."""
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    if path.lower().endswith(".json"):
        data = json.loads(text)
        return check_flow(from_composer(data) if "dedup_removed" in data else data, template)
    return check_flow(parse_markdown_table(text), template)
