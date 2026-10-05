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
    # a P017-et a `prisma check` több fájlforrás összevetése adja (cli.cmd_prisma), nem a check_flow
    "P017": ("error", "A források számai eltérnek",
             "Ugyanaz a doboz (vagy a kizárási okok okonkénti bontása) a megadott fájlokban (composer "
             "prisma-flow.json, --json, prisma_folyamat.md) más "
             "értékű. A számok egyetlen forrása a composer export (ha van), a prisma_folyamat.md csak ellenőrzés: "
             "javítsd az eltérő fájlt, és a kéziratban is az egyező számokat közöld.", "PRISMA 2020 1. ábra; D-S04-009"),
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
    return {"code": code, "severity": sev, "title": title, "study": None, "detail": humanize_boxes(detail),
            "advice": advice, "source": source, "fields": list(fields)}


# a dobozok neve úgy, ahogy az űrlapon és a folyamatábrán áll (UX-18: az üzenet a felhasználó szavaival nevezi meg a
# hiányzó/hibás dobozt, nem a belső JSON-kulccsal; a gépi kulcs a megállapítás 'fields' listájában marad)
BOX_NAMES = {"A1": "Azonosított rekordok — adatbázisok", "A2": "Azonosított rekordok — regiszterek",
             "D1": "Eltávolított duplikátumok", "D2": "Automatizált eszközzel eltávolítva",
             "D3": "Egyéb okból eltávolítva", "B": "Szűrt rekordok", "C": "Kizárt rekordok",
             "E": "Keresett jelentések", "F": "Nem elérhető jelentések", "G": "Értékelt jelentések",
             "H": "Kizárt jelentések", "J": "Bevont jelentések", "I": "Bevont vizsgálatok"}


def _label(field):
    letter = LETTERS.get(field)
    return "%s (%s)" % (BOX_NAMES.get(letter, field), letter) if letter else field


box_label = _label

# „<belső kulcs> (<betű>)” → „<doboz neve> (<betű>)” a megállapítások szövegében (a régi, kulcsos alakok is)
_BOX_ALIASES = dict(LETTERS, **{"included studies": "I", "fulltext assessed": "G"})
_BOX_RE = re.compile(r"(?<![A-Za-z_])(%s) \((%s)\)" % (
    "|".join(re.escape(k) for k in sorted(_BOX_ALIASES, key=len, reverse=True)),
    "|".join(re.escape(v) for v in sorted(set(LETTERS.values()), key=len, reverse=True))))


def humanize_boxes(text):
    """A belső mezőnevek helyett a dobozok űrlapon látható neve (UX-18); a betű marad."""
    if not isinstance(text, str) or not text:
        return text

    def sub(m):
        if _BOX_ALIASES.get(m.group(1)) != m.group(2):
            return m.group(0)
        return "%s (%s)" % (BOX_NAMES.get(m.group(2), m.group(1)), m.group(2))
    return _BOX_RE.sub(sub, text)


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
        t = re.sub("[ \u00a0\u2009\u202f]", "", v.strip())
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


# ok nélküli kizárás címkéje (a composer is ezt írja) és a vele egyenértékű helykitöltők
NO_REASON = "ok nélkül"
_NO_REASON_LABELS = {NO_REASON, "nincs ok", "nincs megadva", "ismeretlen", "ismeretlen ok", "no reason",
                     "not reported", "not specified", "unspecified", "unknown", "none", "n/a", "-", "–", "—"}


def _is_no_reason(label):
    t = re.sub(r"\s+", " ", str(label or "")).strip().lower().rstrip(".")
    return not t or t in _NO_REASON_LABELS


def _reasons_dict(v):
    """{ok: n} | [{"reason": .., "count": ..}] | [(ok, n)] → {ok: n (nyers)}."""
    if v is None:
        return None
    if isinstance(v, dict):
        return dict(v)
    out = {}
    for item in v:
        if isinstance(item, dict):
            key = item.get("reason") or item.get("ok") or item.get("label") or NO_REASON
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
    áttekintés (P015); a hiányzó dobozok levezetése (P011) vagy jelzése (P012). A negatívra adódó
    levezetett doboz lehetetlen folyamat: a megfelelő hibakódot kapja (pl. C > B → P003, H > G → P005).
    Az ok nélküli kizárás (üres ok, „ok nélkül”) P008-at ad. Ha az egyéb módszerek ágának
    értékelt/kizárt dobozai hiányoznak, a J − (G − H) többlet (legfeljebb az ágban azonosított
    rekordok száma) nem ellenőrizhető (P012), nem P005."""
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
                key = NO_REASON if k is None or not str(k).strip() else str(k)
                clean[key] = clean.get(key, 0) + cnt
        reasons[rf] = clean
    c = counts.get
    derived = {}
    unverifiable = []
    # érvénytelen (P001) vagy lehetetlen (negatívra levezetett) doboz: a hibatétel már jelzi, a P012 nem sorolja
    invalid = {x["fields"][0] for x in findings if x["fields"]}

    def need(fields, what):
        miss = [f for f in fields if c(f) is None and derived.get(f) is None]
        report = [f for f in miss if f not in invalid]
        if report:
            unverifiable.append("%s (hiányzik: %s)" % (what, ", ".join(_label(f) for f in report)))
        return not miss

    def impossible(code, detail, fields, box):
        findings.append(_finding(code, detail, fields))
        invalid.add(box)

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
            if ident - after >= 0:
                derived["duplicates_removed"] = ident - after
                findings.append(_finding("P011", "duplicates_removed (D1) = %d − %d = %d (nincs duplikátum-doboz)"
                                         % (ident, after, ident - after), ["duplicates_removed"]))
            else:
                impossible("P002", "a duplikátumszűrés után több rekord (%d), mint azonosított (%d)"
                           % (after, ident), ["after_duplicates", "identified_total"], "duplicates_removed")
        elif dup is not None and ident is not None and after is not None and ident - dup != after:
            findings.append(_finding("P002", "after_duplicates = %d, de azonosított − duplikátum = %d − %d = %d"
                                     % (after, ident, dup, ident - dup), ["after_duplicates", "duplicates_removed"]))
        elif after is None and ident is not None and dup is not None:
            if ident - dup >= 0:
                derived["after_duplicates"] = ident - dup
            else:
                impossible("P002", "duplicates_removed (D1) = %d > azonosított = %d" % (dup, ident),
                           ["duplicates_removed", "identified_total"], "after_duplicates")
        base = after if after is not None else derived.get("after_duplicates")
        other_rm = sum(c(k) or 0 for k in ("automation_removed", "other_removed"))
        if base is not None and c("screened") is not None:
            if base - other_rm != c("screened"):
                findings.append(_finding("P002", "screened (B) = %d, de duplikátum után − egyéb eltávolítás = %d − %d = %d"
                                         % (c("screened"), base, other_rm, base - other_rm),
                                         ["screened", "after_duplicates"]))
        elif base is not None and c("screened") is None:
            if base - other_rm >= 0:
                derived["screened"] = base - other_rm
                findings.append(_finding("P011", "screened (B) = %d (a duplikátumszűrés utáni számból)"
                                         % derived["screened"], ["screened"]))
            else:
                impossible("P002", "egyéb eltávolítás = %d > duplikátum után = %d" % (other_rm, base),
                           ["automation_removed", "other_removed", "after_duplicates"], "screened")
        else:
            need(("screened",) if base is not None else ("after_duplicates",), "B = duplikátum után − egyéb eltávolítás")
    else:
        removed = [c(k) for k in removed_keys]
        derived["removed_before_screening_total"] = sum(v for v in removed if v is not None)
        no_removal_box = all(c(k) is None for k in removed_keys)
        if any(k in invalid for k in removed_keys):
            pass     # kitöltött, de érvénytelen eltávolítás-doboz (P001): B ettől nem ellenőrizhető, nem „hiányzik”
        elif ident is not None and c("screened") is not None and no_removal_box and ident >= c("screened"):
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
            if b - c("excluded_screening") >= 0:
                derived["sought"] = b - c("excluded_screening")
                findings.append(_finding("P011", "sought (E) = B − C = %d" % derived["sought"], ["sought"]))
            else:
                impossible("P003", "excluded_screening (C) = %d > screened (B) = %d" % (c("excluded_screening"), b),
                           ["excluded_screening", "screened"], "sought")
        else:
            need(("screened", "excluded_screening", "sought"), "E = B − C")
        e = c("sought") if c("sought") is not None else derived.get("sought")
        f_ = c("not_retrieved")
        if e is not None and c("assessed") is not None:
            if f_ is None:
                if e - c("assessed") < 0:
                    impossible("P004", "assessed (G) = %d > sought (E) = %d" % (c("assessed"), e),
                               ["assessed", "sought"], "not_retrieved")
                else:
                    derived["not_retrieved"] = e - c("assessed")
                    findings.append(_finding("P011", "not_retrieved (F) = E − G = %d" % derived["not_retrieved"],
                                             ["not_retrieved"]))
            elif e - f_ != c("assessed"):
                findings.append(_finding("P004", "assessed (G) = %d, de E − F = %d − %d = %d" % (
                    c("assessed"), e, f_, e - f_), ["assessed", "sought", "not_retrieved"]))
        elif e is not None and c("assessed") is None and f_ is not None:
            if e - f_ >= 0:
                derived["assessed"] = e - f_
                findings.append(_finding("P011", "assessed (G) = E − F = %d" % derived["assessed"], ["assessed"]))
            else:
                impossible("P004", "not_retrieved (F) = %d > sought (E) = %d" % (f_, e),
                           ["not_retrieved", "sought"], "assessed")
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
            if b - c("excluded_screening") >= 0:
                derived["assessed"] = b - c("excluded_screening")
                findings.append(_finding("P011", "assessed (G) = B − C = %d" % derived["assessed"], ["assessed"]))
            else:
                impossible("P003", "excluded_screening (C) = %d > screened (B) = %d" % (c("excluded_screening"), b),
                           ["excluded_screening", "screened"], "assessed")
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
            missing = sum(v for k, v in rs.items() if _is_no_reason(k))
            if missing:
                findings.append(_finding("P008", "%s = %d, ebből %d kizárás ok nélkül" % (
                    _label(hf), total if total is not None else s, missing), [hf, rf]))
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
            if om_excl > om["assessed"]:
                # nem olthatja ki a fő ág bevont jelentéseit (J = G − H + egyéb ág)
                impossible("P005", "egyéb ág: other_methods_excluded = %d > other_methods_assessed = %d" % (
                    om_excl, om["assessed"]), ["other_methods_excluded", "other_methods_assessed"],
                    "other_methods_included_reports")
            else:
                other_j = om["assessed"] - om_excl
                derived["other_methods_included_reports"] = other_j
        else:
            need(("other_methods_assessed", "other_methods_excluded"), "egyéb ág: bevont = értékelt − kizárt")

    # --- bevonás
    i_ = c("included_studies")
    j_ = c("included_reports")
    if g is not None and h is not None and h > g:
        impossible("P005", "excluded_eligibility (H) = %d > assessed (G) = %d" % (h, g),
                   ["excluded_eligibility", "assessed"], "included_reports")
    elif g is not None and h is not None and "other_methods_included_reports" in invalid:
        # lehetetlen egyéb ág (P005): a J csak a fő ághoz mérhető — az ág legfeljebb az értékelt jelentéseit adhatja
        main_j = g - h
        awaiting = c("awaiting") or 0
        if j_ is not None and not main_j <= j_ + awaiting <= main_j + om["assessed"]:
            findings.append(_finding("P005", "included_reports (J) = %d%s, de G − H = %d − %d = %d (az egyéb ág "
                                     "legfeljebb %d jelentést adhat)" % (
                                         j_, (" + elbírálásra vár %d" % awaiting) if awaiting else "", g, h,
                                         main_j, om["assessed"]),
                                     ["included_reports", "assessed", "excluded_eligibility"]))
    elif g is not None and h is not None:
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
            extra = j_ + awaiting - main_j
            if other_j is None and om["identified"] is not None and 0 < extra <= om["identified"]:
                # az egyéb ág dobozai hiányoznak: a többlet onnan származhat, de nem ellenőrizhető
                unverifiable.append("J = G − H + egyéb ág: a J többlete (%d − %d = %d) az egyéb módszerek ágából "
                                    "bevont jelentés lehet (az ág értékelt/kizárt dobozai nélkül nem ellenőrizhető)"
                                    % (j_ + awaiting, main_j, extra))
            elif j_ + awaiting != derived["included_reports_expected"]:
                txt = "included_reports (J) = %d%s, de G − H = %d − %d = %d" % (
                    j_, (" + elbírálásra vár %d" % awaiting) if awaiting else "", g, h, main_j)
                if other_j is not None:
                    txt += " (+ egyéb ág %d = %d)" % (other_j, derived["included_reports_expected"])
                findings.append(_finding("P005", txt, ["included_reports", "assessed", "excluded_eligibility"]))
        elif derived["included_reports_expected"] - awaiting < 0:
            impossible("P005", "elbírálásra vár %d > G − H%s = %d" % (
                awaiting, " + egyéb ág" if other_j is not None else "", derived["included_reports_expected"]),
                ["awaiting", "assessed", "excluded_eligibility"], "included_reports")
        else:
            derived["included_reports"] = derived["included_reports_expected"] - awaiting
            if i_ is None or i_ <= derived["included_reports"]:
                findings.append(_finding("P011", "included_reports (J) = G − H%s = %d" % (
                    " + egyéb ág" if other_j is not None else "", derived["included_reports"]),
                    ["included_reports"]))
    elif g is None or h is None:
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


_MD_LETTER = re.compile(r"[(,;]\s*n\s*=\s*([A-Z]\d?)")
_INT = re.compile(r"(?<![\w.,])\d+(?![\w.,]?\d)")
# ezres tagolás egy számon belül azonos elválasztóval: szóköz, NBSP, keskeny szóköz, pont vagy vessző
# („12 345”, „12.345”, „1,500”); a „/”-rel vagy „;”-vel elválasztott értékeket nem érinti
_GROUPED = re.compile(r"(?<![\w.,])\d{1,3}([ \u00a0\u2009\u202f.,])\d{3}(?:\1\d{3})*(?![\w.,]?\d)")
# kitöltetlennek számító Szám-cella (gondolatjel, n/a)
_MD_EMPTY = re.compile(r"^(?:[-–—−]+|n\.?\s*a\.?|n/a)$", re.I)
_LETTER_FIELD = {"A1": "identified_databases", "A2": "identified_registers", "B": "screened",
                 "C": "excluded_screening", "E": "sought", "F": "not_retrieved", "G": "assessed",
                 "H": "excluded_eligibility", "J": "included_reports", "I": "included_studies"}


def _ungroup(text):
    return _GROUPED.sub(lambda m: re.sub(r"\D", "", m.group(0)), text or "")


def _ints(text):
    return [int(x) for x in _INT.findall(_ungroup(text))]


_LIST_INT = re.compile(r"(?<![\w.])\d+(?![\w.]?\d)")


def _slot_values(raw, slots):
    """Többértékű Szám-cella (D1/D2/D3: 3, J/I: 2 doboz) értékei dobozonként (None = üres rész).

    „/” vagy „;” elválasztóval részenként, ezres tagolással is („1 010 / 0 / 0”). Elválasztó nélkül a
    szóközzel/vesszővel tagolt számsor lista, ha pontosan annyi szám, ahány doboz, és egyik sem kezdődik
    0-val („118 115” → 118, 115; „100 200 300”), különben ezres tagolásként olvasandó („1 018 1 015” →
    1018, 1015; „1 018” → 1018)."""
    parts = re.split(r"[/;]", raw)
    if len(parts) > 1:
        out = []
        for part in parts:
            v = _ints(part)
            out.append(v[0] if v else None)
        return out
    plain = _LIST_INT.findall(raw)
    if len(plain) == slots and not any(len(x) > 1 and x.startswith("0") for x in plain):
        return [int(x) for x in plain]
    return _ints(raw)


def _md_reasons(num):
    """„60 (ok A: 40; ok B: 20)” → [(ok, n)]; a csak számból álló címke (pl. „60 = 40 + 20”) nem ok."""
    rs = re.findall(r"([^;:()=,]+?)\s*[:=]\s*(\d+)", num)
    return [(k.strip(), int(v)) for k, v in rs
            if k.strip().lower() not in ("h", "n") and not re.fullmatch(r"[\d\s.,+]*", k.strip())]


def _other_branch_field(low):
    if "nem elérhető" in low or "nem elerheto" in low:
        return "other_methods_not_retrieved"
    if "keresett" in low:
        return "other_methods_sought"
    if "értékelt" in low or "ertekelt" in low:
        return "other_methods_assessed"
    if "kizárt" in low or "kizart" in low:
        return "other_methods_excluded"
    return "other_methods_identified"


def parse_markdown_table(text):
    """A projekt `02_szures/prisma_folyamat.md` táblázata (| Lépés | Szám | Ellenőrzés |) → flow szótár.

    A sorokat a Lépés-cella „(n = A1)” stb. betűjele azonosítja; a Szám-cella egész száma az érték
    (ezres tagolással is: „12 345”, „12.345”, „12,345”; a zárójeles megjegyzés nem számít, de a
    zárójelen kívüli több szám — pl. „1 200 + 180” — nem egyértelmű, P001). Különleges sorok: a D1/D2/D3 sor
    (Szám: „12 / 0 / 3” vagy „D1=12; D2=0; D3=3”), a bevont sor (J és I: „8 / 6” vagy „J=8, I=6”;
    elválasztó nélküli számsornál lásd _slot_values),
    a kizárt sor okai („9 (ok A: 5; ok B: 4)”), az „Ebből metaanalízisben” sor, és az egyéb módszerek
    ágának sorai („Egyéb forrásból / Egyéb ág …”: azonosított, keresett, nem elérhető, értékelt,
    kizárt okokkal). Az üres (vagy „–”, „n/a”) Szám-cellák kimaradnak (a check_flow P012-vel jelzi
    őket); a betűjeles doboz számként nem olvasható, kitöltött cellájának szövege kerül a mezőbe
    (a check_flow P001-gyel jelzi)."""
    flow = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [x.strip() for x in line.strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        step, raw = cells[0], cells[1]
        num = _ungroup(raw)
        low = step.lower()
        if not num or low.startswith("lépés") or _MD_EMPTY.match(num):
            continue
        if "d1" in low and ("d2" in low or "d3" in low):
            tagged = dict((k.upper(), int(v)) for k, v in re.findall(r"\b(D[123])\s*[=:]\s*(\d+)", num, re.I))
            vals = _slot_values(raw, 3) if not tagged else []
            if not tagged and all(v is None for v in vals):
                flow["duplicates_removed"] = raw
            for i, k in enumerate(("D1", "D2", "D3")):
                v = tagged.get(k, vals[i] if i < len(vals) else None)
                if v is not None:
                    flow[{"D1": "duplicates_removed", "D2": "automation_removed", "D3": "other_removed"}[k]] = v
            continue
        letters = _MD_LETTER.findall(step)
        if "J" in letters and "I" in letters:
            tagged = dict((k.upper(), int(v)) for k, v in re.findall(r"\b([JI])\s*[=:]\s*(\d+)", num, re.I))
            if tagged:
                rest = _ints(re.sub(r"\b[JI]\s*[=:]\s*\d+", " ", num, flags=re.I))
            else:
                rest = _slot_values(raw, 2)
            j = tagged["J"] if "J" in tagged else (rest.pop(0) if rest else None)
            i = tagged["I"] if "I" in tagged else (rest.pop(0) if rest else None)
            if j is None and i is None:
                flow["included_reports"] = raw
            if j is not None:
                flow["included_reports"] = j
            if i is not None:
                flow["included_studies"] = i
            continue
        lettered = bool(letters) and letters[0] in _LETTER_FIELD
        if lettered:
            field = _LETTER_FIELD[letters[0]]
        elif "metaanal" in low:
            field = "included_meta"
        elif re.search(r"egy[ée]b (forr[áa]s|m[óo]dszer|[áa]g)", low):
            field = _other_branch_field(low)
        else:
            continue
        vals = _ints(num)
        if lettered and field not in ("excluded_eligibility", "other_methods_excluded") and \
                len(_ints(re.sub(r"\([^()]*\)", " ", num))) > 1:
            flow[field] = raw        # több szám a zárójelen kívül (pl. „1 200 + 180”): nem választunk → P001
        elif vals:
            flow[field] = vals[0]
        elif lettered:
            flow[field] = raw        # kitöltött, de számként nem olvasható doboz → P001
        if vals and field in ("excluded_eligibility", "other_methods_excluded"):
            rs = _md_reasons(num)
            if rs:
                flow[field + "_reasons"] = dict(rs)
    return flow


def load(path, template=None):
    """prisma-flow.json (composer) vagy prisma_folyamat.md → FlowCheck."""
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    if path.lower().endswith(".json"):
        data = json.loads(text)
        return check_flow(from_composer(data) if "dedup_removed" in data else data, template)
    return check_flow(parse_markdown_table(text), template)
