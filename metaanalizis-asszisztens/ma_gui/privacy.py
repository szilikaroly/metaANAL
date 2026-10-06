# -*- coding: utf-8 -*-
"""Adatvédelem a munkapadhoz (terv 7.4–7.5; 3.5.2, 3.5.16, 8.5).

- Adatosztályok (A–D) és írás-tartás (``can_write``): B/C osztálynál, ha a vault követi a projektet,
  adat csak .gitignore-olt célba írható; C osztályú adat csak a ``_privat/`` alá kerülhet.
- PHI/TAJ-szkenner (``scan_table``): oszlopnév-minták (magyar és angol, ékezetfüggetlen) és
  értékminták (9 jegyű TAJ-szám CDV-ellenőrzőjeggyel — számexport-alakban is: '123456788.0',
  '1.23456788E+08', azonosító-oszlopban a vezető nullát vesztett 8 jegyű alak —, teljes dátum,
  e-mail). ``scan_doc``: JSON-dokumentum (pl. eredet-oldalfájl) minden szöveges levele;
  ``text_patterns``: szabad szöveg (napló, indoklás). A találat csak helyet és mintanevet
  tartalmaz, értéket soha.
- L1 vault-felismerés (``$VAULT_HOME`` vagy ``~/.claude/vault/config.json``: root, max_depth,
  exclude, paused) és a már követett érzékeny fájlok (``git ls-files``).
- L2 kezelt .gitignore-blokk a jelölők között: idempotens, a blokkon kívüli sorokhoz nem nyúl;
  előbb diff-előnézet (``plan_gitignore``), írás csak kifejezett hívásra (``apply_gitignore``).
- L3 opt-in pre-commit őr (stdlib Python-szkript; a meglévő hookot láncolja).
- L6 „Már felment?”: ``git log --all`` az érzékeny útvonalakra.
- Felhőszinkron-figyelmeztetés: OneDrive (Windows: winreg ``User Shell Folders\\Personal`` és
  ``%OneDrive%``), iCloud Desktop & Documents (macOS).
- Claude deny-szabály a projekt ``.claude/settings.json``-jába: összefésülés a többi kulcs
  megtartásával, diff-előnézettel, írás csak kifejezett hívásra.

A vault konfigurációját soha nem módosítja (L5). Csak stdlib, statisztika nincs. Cellaértéket nem
naplóz, és sem a találatokba, sem hibaüzenetbe nem tesz (T10). Git csak argv-listával
(``shell=False``), időkorláttal fut; ha nincs git, a függvények ezt jelzik, nem dobnak.
"""
import datetime
import difflib
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path

DATA_CLASSES = ("A", "B", "C")
DATA_CLASS_LABELS = {
    "A": "publikált aggregált",
    "B": "nem publikált aggregált",
    "C": "betegszintű / azonosítható",
    "D": "jogvédett teljes szöveg",
}
PRIVATE_DIR = "_privat"
# az atomikus írás ideiglenes fájljainak előtagja (azonos a security.TMP_PREFIX-szel; a modul
# szándékosan önálló, csak stdlib)
TMP_PREFIX = ".ma-tmp-"

# L2: kezelt .gitignore-blokk (7.5)
GITIGNORE_REL = ".gitignore"
BLOCK_BEGIN = "# >>> ma-munkapad (kezelt blokk) >>>"
BLOCK_END = "# <<< ma-munkapad <<<"
BLOCK_NOTE = "# A MA-munkapad kezeli: a jelölők közti sorok felülíródnak; saját sort a blokkon kívülre írj."
MANAGED_PATTERNS = (
    "_privat/",
    "*_PHI*",
    "*.phi.*",
    "07_ellenorzes/audit/**/data/",
    "*.snapshot.html",
    "projekt.sqlite-wal",
    "projekt.sqlite-shm",
    TMP_PREFIX + "*",                 # atomikus írás árva ideiglenes fájljai (összeomlás után)
)

# Claude-hozzáférés (7.5)
CLAUDE_SETTINGS_REL = ".claude/settings.json"
CLAUDE_LOCAL_SETTINGS_REL = ".claude/settings.local.json"
DENY_RULES = ("Read(./_privat/**)", "Read(**/*_PHI*)")

# L3: pre-commit őr
HOOK_MARKER = "ma-munkapad pre-commit őr"
HOOK_VERSION = 1
CHAINED_HOOK_NAME = "pre-commit.ma-munkapad-elozo"

# L1: vault alapértékei (H11)
VAULT_DEFAULT_ROOT = ("Documents", "claude")
VAULT_DEFAULT_MAX_DEPTH = 2

GIT_TIMEOUT_S = 10
MAX_LISTED_PATHS = 200
YEAR_MIN, YEAR_MAX = 1880, 2099

_NO_CHECK = object()                  # apply_*: alapból nincs előnézet-ellenőrzés

_HTTP = {"BAD_REQUEST": 400, "FORBIDDEN": 403, "NOT_FOUND": 404, "CONFLICT": 409,
         "VALIDATION": 422, "LOCKED": 423, "INTERNAL": 500}


class PrivacyError(ValueError):
    """Elutasított adatvédelmi művelet; a router a code/http mezőkből építi a hiba-borítékot (4.2)."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code if code in _HTTP else "INTERNAL"
        self.http = _HTTP[self.code]
        self.message = message
        self.details = details or {}

    def to_error(self):
        return {"code": self.code, "http": self.http, "message": self.message, "details": self.details}


def normalize_data_class(value):
    """'a'/'B'/… → 'A'/'B'/'C'; másra PrivacyError (a D fájltípus-osztály, projektosztály nem lehet)."""
    v = str(value or "").strip().upper()
    if v not in DATA_CLASSES:
        raise PrivacyError("VALIDATION", "Ismeretlen adatosztály: a projekt osztálya A, B vagy C lehet.")
    return v


# ---------------------------------------------------------------------------------------------
# PHI/TAJ-szkenner (7.4)
# ---------------------------------------------------------------------------------------------

PATTERN_LABELS = {
    "taj": "TAJ-szám / társadalombiztosítási azonosító",
    "name": "személynév",
    "birth": "születési adat",
    "address": "lakcím",
    "phone": "telefonszám",
    "email": "e-mail-cím",
    "mrn": "kórlap- / törzsszám (MRN)",
    "patient_id": "beteg-azonosító",
    "taj_cdv": "9 jegyű TAJ-szám érvényes CDV-ellenőrzőjeggyel",
    "full_date": "teljes dátum (lehetséges születési dátum)",
}


def _fold(text):
    """Kisbetűs, ékezet nélküli alak (NFKD, kombináló jelek nélkül)."""
    t = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def _tokens(name):
    # betű- és számsorozatok külön tokenek: 'esemény1' → ['esemeny', '1']
    return re.findall(r"[a-z]+|[0-9]+", _fold(name))


_NAME_WORDS = {"nev", "neve", "nevek", "nevei", "name", "names"}
_NAME_COMPOUNDS = {
    "vezeteknev", "keresztnev", "csaladnev", "utonev", "betegnev", "szuletesinev", "leanykorinev",
    "teljesnev", "firstname", "lastname", "fullname", "surname", "forename", "givenname",
    "familyname", "patientname", "middlename", "maidenname",
}
# ha ezek közül bármelyik szerepel, a 'név' nem személynév (vizsgálat, szerző, gyógyszer neve …)
_NAME_NOT_PERSON = {
    "study", "studies", "trial", "vizsgalat", "vizsgalatok", "tanulmany", "szerzo", "szerzok",
    "author", "authors", "journal", "folyoirat", "drug", "gyogyszer", "hatoanyag", "intervention",
    "intervencio", "beavatkozas", "outcome", "kimenet", "kimenetel", "file", "fajl", "variable",
    "valtozo", "column", "oszlop", "group", "csoport", "alcsoport", "subgroup", "arm", "kar",
    "instrument", "eszkoz", "scale", "skala", "measure", "meres", "mutato", "center", "centre",
    "kozpont", "korhaz", "hospital", "intezmeny", "institution", "site", "helyszin", "country",
    "orszag", "test", "teszt", "product", "termek", "database", "adatbazis", "domain", "item",
    "tetel", "model", "modell", "sponsor", "szponzor", "registry", "regiszter", "register", "tool",
    "method", "modszer", "comparator", "kontroll", "control", "condition", "betegseg", "disease",
    "diagnozis", "diagnosis", "exposure", "expozicio", "category", "kategoria", "label", "cimke",
    "short", "rovid", "reference", "hivatkozas", "source", "forras", "project", "projekt", "unit",
    "egyseg", "field", "mezo", "brand", "marka", "generic", "generikus",
}
_ADDRESS_WORDS = {"cim", "cime", "cimek", "lakcim", "lakcime", "address", "addr", "iranyitoszam",
                  "irsz", "zip", "zipcode", "postcode", "utca", "street"}
# 'cím' = cím (lakcím) vagy cím (közlemény címe); e-mail- és webcím sem lakcím
_ADDRESS_NOT = {"email", "mail", "web", "weboldal", "url", "honlap", "ip", "cikk", "cikkek",
                "kozlemeny", "article", "paper", "publikacio", "publication", "folyoirat", "journal",
                "title", "angol", "eredeti", "magyar", "english", "original", "konyv", "book",
                "fejezet", "chapter", "rovid", "short"}
_BIRTH_WORDS = {"dob", "szul"}
_BIRTH_PLURALS = {"births", "szuletesek", "szuletesszam"}
# születési súly, -hét, élveszületés stb. aggregált kimenetek, nem személyes adat
_BIRTH_NOT = {
    "weight", "suly", "sulya", "tomeg", "tomege", "length", "hossz", "hossza", "height", "rate",
    "arany", "aranya", "order", "sorrend", "defect", "defects", "rendellenesseg", "outcome",
    "outcomes", "kimenetel", "kimenet", "asphyxia", "trauma", "cohort", "kohorsz", "control",
    "kontroll", "preterm", "live", "elve", "spacing", "interval", "plurality", "mode", "mod", "modja",
    "het", "hete", "week", "weeks", "gestational", "gestacios", "birthweight", "birthrate",
    "birthorder", "birthlength", "count", "szama", "number", "n",
}
_PHONE_WORDS = {"tel", "fax", "phone", "telephone"}
_PHONE_PREFIXES = ("telefon", "phone", "telephone", "mobilszam", "mobiltel", "faxszam")
_PATIENT_SUBJECT = {"patient", "patients", "beteg", "betege", "betegek", "paciens", "pacient",
                    "subject", "participant", "resztvevo", "alany"}
_ID_WORDS = {"id", "azonosito", "azonositoja", "azon", "identifier", "kod", "kodja", "code",
             "sorszam", "sorszama"}
_PATIENT_COMPOUNDS = {"patientid", "betegid", "betegazonosito", "betegkod", "pacienskod", "subjectid",
                      "participantid", "patientcode", "patientidentifier", "betegsorszam"}


def column_patterns(name):
    """Az oszlopnévre illő PHI-minták azonosítói (lista, sorrendtartó, ismétlés nélkül)."""
    toks = _tokens(name)
    if not toks:
        return []
    ts = set(toks)
    found = []

    def add(p):
        if p not in found:
            found.append(p)

    if ("taj" in ts or "ssn" in ts or any(t.startswith(("tajszam", "tarsadalombiztositasi", "tbszam"))
                                           for t in toks)
            or "socialsecurity" in "".join(toks)):
        add("taj")
    if ((ts & _NAME_WORDS and not ts & _NAME_NOT_PERSON) or ts & _NAME_COMPOUNDS):
        add("name")
    birthish = (ts & _BIRTH_WORDS or "dateofbirth" in "".join(toks)
                or any(t.startswith(("szulet", "birth")) and t not in _BIRTH_PLURALS for t in toks))
    if birthish and not ts & _BIRTH_NOT:
        add("birth")
    if ts & _ADDRESS_WORDS and not ts & _ADDRESS_NOT:
        add("address")
    if ts & _PHONE_WORDS or any(t.startswith(_PHONE_PREFIXES) for t in toks):
        add("phone")
    if (any(t.startswith("email") for t in toks) or "mail" in ts
            or any(a == "e" and b.startswith("mail") for a, b in zip(toks, toks[1:]))):
        add("email")
    if ("mrn" in ts or "medicalrecord" in "".join(toks)
            or any(t.startswith(("korlapszam", "torzsszam", "betegtorzsszam")) for t in toks)
            or ("korlap" in ts and ts & {"szam", "szama"})):
        add("mrn")
    if (ts & _PATIENT_SUBJECT and ts & _ID_WORDS) or ts & _PATIENT_COMPOUNDS:
        add("patient_id")
    return found


def taj_check_digit(first8):
    """A TAJ CDV-ellenőrzőjegye az első 8 jegyből: páratlan helyen ×3, páros helyen ×7, összeg mod 10."""
    s = str(first8)
    if not re.fullmatch(r"[0-9]{8}", s):
        raise ValueError("8 számjegy kell")
    return sum(int(c) * (3 if i % 2 == 0 else 7) for i, c in enumerate(s)) % 10


def is_valid_taj(text):
    """9 jegyű (szóközzel vagy kötőjellel tagolt) TAJ-szám érvényes CDV-vel; a csupa nulla nem az."""
    s = re.sub(r"[ \-]", "", str(text))
    if not re.fullmatch(r"[0-9]{9}", s) or s == "000000000":
        return False
    return taj_check_digit(s[:8]) == int(s[8])


_TAJ_RE = re.compile(r"(?<![\w.,/])([0-9]{3})[ \-]{0,2}([0-9]{3})[ \-]{0,2}([0-9]{3})(?!\w)(?![.,/][0-9])")
# 'TAJ123456788', 'TAJ-szám: 123 456 788' (a betű közvetlenül a szám előtt áll)
_TAJ_PREFIX_RE = re.compile(r"taj(?:[ \-]?szam[a]?)?\s*[:#.\-]?\s*([0-9]{3})[ \-]{0,2}([0-9]{3})[ \-]{0,2}"
                            r"([0-9]{3})(?![0-9])")
_EMAIL_RE = re.compile(r"(?<![a-z0-9._%+\-])[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,24}"
                       r"(?![a-z0-9\-])")

_MONTHS = {
    "januar": 1, "january": 1, "jan": 1,
    "februar": 2, "february": 2, "febr": 2, "feb": 2,
    "marcius": 3, "march": 3, "marc": 3, "mar": 3,
    "aprilis": 4, "april": 4, "apr": 4,
    "majus": 5, "may": 5, "maj": 5,
    "junius": 6, "june": 6, "jun": 6,
    "julius": 7, "july": 7, "jul": 7,
    "augusztus": 8, "august": 8, "aug": 8,
    "szeptember": 9, "september": 9, "szept": 9, "sept": 9, "sep": 9,
    "oktober": 10, "october": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MON = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")(?![a-z])"
_DSEP = r"[\s.,\-/]*"
_DATE_YMD = re.compile(r"(?<![0-9.,/])([0-9]{4})\s*([-./])\s*([0-9]{1,2})\s*\2\s*([0-9]{1,2})(?![0-9])"
                       r"(?![.,/\-][0-9])")
_DATE_DMY = re.compile(r"(?<![0-9.,/])([0-9]{1,2})\s*([-./])\s*([0-9]{1,2})\s*\2\s*([0-9]{4})(?![0-9])"
                       r"(?![.,/\-][0-9])")
_DATE_Y_MON_D = re.compile(r"(?<![0-9.,/])([0-9]{4})" + _DSEP + _MON + _DSEP + r"([0-9]{1,2})(?![0-9])")
_DATE_D_MON_Y = re.compile(r"(?<![0-9.,/])([0-9]{1,2})" + _DSEP + _MON + _DSEP + r"([0-9]{4})(?![0-9])")
_DATE_MON_D_Y = re.compile(r"(?<![a-z])" + _MON + _DSEP + r"([0-9]{1,2})(?:st|nd|rd|th)?" + _DSEP
                           + r"([0-9]{4})(?![0-9])")

# oszlopnév-előtagok, amelyeknél a teljes dátum nem a betegé (megjelenés, keresés, kinyerés …)
_DATE_CONTEXT_NOT_PERSON = (
    "publ", "megjelen", "kozles", "kozzetet", "search", "keres", "letolt", "download", "access",
    "hozzafer", "update", "frissit", "modosit", "modified", "creat", "letrehoz", "extract", "kinyer",
    "submit", "benyujt", "accept", "elfogad", "receiv", "beerkez", "epub", "online", "review",
    "atteken", "ellenor", "ertekel", "registr", "regisztr", "version", "verzio", "export", "import",
)


def _valid_date(y, m, d):
    if not YEAR_MIN <= y <= YEAR_MAX:
        return False
    try:
        datetime.date(y, m, d)
    except ValueError:
        return False
    return True


def _has_full_date(t):
    for m in _DATE_YMD.finditer(t):
        if _valid_date(int(m.group(1)), int(m.group(3)), int(m.group(4))):
            return True
    for m in _DATE_DMY.finditer(t):
        a, b, y = int(m.group(1)), int(m.group(3)), int(m.group(4))
        if _valid_date(y, b, a) or _valid_date(y, a, b):     # nap.hó.év vagy hó/nap/év
            return True
    for m in _DATE_Y_MON_D.finditer(t):
        if _valid_date(int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))):
            return True
    for m in _DATE_D_MON_Y.finditer(t):
        if _valid_date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))):
            return True
    for m in _DATE_MON_D_Y.finditer(t):
        if _valid_date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2))):
            return True
    return False


def _date_context_excluded(column_name):
    return any(t.startswith(_DATE_CONTEXT_NOT_PERSON) for t in _tokens(column_name or ""))


_PLAIN_NUMBER = re.compile(r"\s*[+\-]?[0-9]*(?:[.,][0-9]*)?\s*\Z")
# egész szám, esetleg '.0'-s lebegőpontos exportalakban (pandas/R: NA-t is tartalmazó egész oszlop)
_INT_EXPORT = re.compile(r"\s*[+\-]?([0-9]+)(?:[.,]0+)?\s*\Z")
# Excel/R tudományos alak: 1.23456788E+08
_SCI_NUMBER = re.compile(r"\s*[+\-]?([0-9])[.,]([0-9]{1,8})[eE]\+?0*([0-9]{1,2})\s*\Z")
# ezekben az oszlopokban a 8 jegyű szám nem nullát vesztett TAJ: bibliográfiai azonosító (PMID …),
# évszám, létszám, a motor számoszlopai (n1, e1, m1, sd1, yi, vi, sei …; tokenekre bontva)
_SHORT_TAJ_NOT = {
    "pmid", "pmcid", "pubmed", "doi", "isbn", "issn", "nct", "ev", "eve", "evszam", "year", "volume",
    "kotet", "issue", "page", "pages", "oldal", "n", "nt", "nc", "ni", "count", "total", "osszes",
    "letszam", "population", "nepesseg", "lakossag", "size", "meret", "events", "event", "esemeny",
    "esetszam", "e", "x", "xi", "cases", "m", "mean", "atlag", "sd", "sdt", "sdc", "szoras", "r", "ri",
    "cor", "korrelacio", "yi", "es", "effect", "hatas", "te", "vi", "var", "variance", "variancia", "sei",
    "se", "sete", "mdiff", "sum", "ssd", "tpos", "cpos", "ai", "ci",
}


def _number_digits(text):
    """Számként írt egész szám jegyei ('123456788', '123456788.0', '1.23456788E+08') vagy None."""
    m = _INT_EXPORT.match(text)
    if m:
        return m.group(1)
    m = _SCI_NUMBER.match(text)
    if m:
        mant, exp = m.group(1) + m.group(2), int(m.group(3))
        if len(mant) - 1 <= exp:
            return mant + "0" * (exp - (len(mant) - 1))
    return None


def _numeric_taj(text, short_taj):
    digits = _number_digits(text)
    if digits is None:
        return False
    if len(digits) == 9:
        return is_valid_taj(digits)
    # Excelben számként tárolt TAJ: a vezető 0 elvész (012345678 → 12345678)
    return short_taj and len(digits) == 8 and is_valid_taj("0" + digits)


def value_patterns(text, dates=True, short_taj=False):
    """A cellaszövegre illő értékminták azonosítói ('taj_cdv', 'full_date', 'email'); értéket nem ad vissza.

    short_taj: a 8 jegyű számot a vezető nullát vesztett TAJ-ként is ellenőrzi (azonosító-jellegű
    oszlopban; a hívó dönti el)."""
    # a legrövidebb illeszkedő alak is ≥ 6 karakter (a@b.hu, 1.2.1990, 8–9 jegyű TAJ): a tipikus
    # számcella gyorsan kiesik; a sima szám csak TAJ lehet
    if not text or len(text) < 6:
        return []
    if _PLAIN_NUMBER.match(text) or _SCI_NUMBER.match(text):
        return ["taj_cdv"] if _numeric_taj(text, short_taj) else []
    t = text.lower() if text.isascii() else _fold(text)
    found = []
    if (any(is_valid_taj(m.group(1) + m.group(2) + m.group(3)) for m in _TAJ_RE.finditer(t))
            or ("taj" in t and any(is_valid_taj(m.group(1) + m.group(2) + m.group(3))
                                   for m in _TAJ_PREFIX_RE.finditer(t)))):
        found.append("taj_cdv")
    if dates and _has_full_date(t):
        found.append("full_date")
    if "@" in t and _EMAIL_RE.search(t):
        found.append("email")
    return found


_BIRTH_CONTEXT_RE = re.compile(r"(?<![a-z])(?:szul|dob(?![a-z])|birth|date of birth)")


def text_patterns(text):
    """Szabad szöveg (naplóbejegyzés, indoklás) mintái: 'taj_cdv'; 'full_date' csak születési
    kontextusban (szül., született, DOB, birth) — a sima dátum a naplóban szokásos. Értéket nem ad."""
    if not isinstance(text, str) or len(text) < 6:
        return []
    found = [p for p in value_patterns(text, dates=False) if p == "taj_cdv"]
    t = text.lower() if text.isascii() else _fold(text)
    if _BIRTH_CONTEXT_RE.search(t) and _has_full_date(t):
        found.append("full_date")
    return found


def _short_taj_column(name):
    """A 8 jegyű (nullát vesztett) TAJ-ellenőrzés kell-e ebben az oszlopban: nem a motor
    számoszlopa (n1, e1, year …) és nem bibliográfiai/létszám-jellegű (PMID, évszám …)."""
    return not set(_tokens(name)) & _SHORT_TAJ_NOT


def _cell_text(v):
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, str):
        return v
    text = getattr(v, "text", None)          # tableio.NumText: az eredeti szöveg
    if isinstance(text, str):
        return text
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        # Excelből számként érkező azonosító (pl. 123456788.0)
        return str(int(v)) if v.is_integer() and -1e15 < v < 1e15 else ""
    return ""


def scan_table(header, rows, max_findings=None):
    """PHI/TAJ-szkenner. header: oszlopnevek; rows: listák vagy (fejléc szerinti) dict-ek.

    Visszaad: [{kind: 'column'|'value', column, column_index, row_index, pattern}] — a 'column'
    találatnál row_index None. Az illeszkedő értéket soha nem adja vissza (T10)."""
    names = ["" if h is None else str(h) for h in (header or [])]
    findings = []

    def full():
        return max_findings is not None and len(findings) >= max_findings

    for ci, name in enumerate(names):
        for pat in column_patterns(name):
            findings.append({"kind": "column", "column": name, "column_index": ci, "row_index": None,
                             "pattern": pat})
            if full():
                return findings
    no_date = [_date_context_excluded(n) for n in names]
    short = [_short_taj_column(n) for n in names]
    for ri, row in enumerate(rows or []):
        if isinstance(row, dict):
            cells = [row.get(n) for n in names]
        else:
            cells = list(row or [])
        for ci, cell in enumerate(cells):
            text = _cell_text(cell)
            if not text or not text.strip():
                continue
            dates = not (ci < len(no_date) and no_date[ci])
            for pat in value_patterns(text, dates=dates, short_taj=ci < len(short) and short[ci]):
                findings.append({"kind": "value", "column": names[ci] if ci < len(names) else None,
                                 "column_index": ci, "row_index": ri, "pattern": pat})
                if full():
                    return findings
    return findings


# eredet-oldalfájl: szerkezeti kulcsok (nem szabad szöveg) és időbélyeg-kulcsok (dátum nem PHI-jel)
_DOC_SKIP_KEYS = frozenset(["schema", "table", "table_sha256", "row_uid", "method", "sha256", "doc",
                            "estimated", "field"])
_DOC_TIME_KEYS = frozenset(["at", "date", "time", "timestamp", "ts", "created", "updated", "modified",
                            "engine_version", "version"])


def _time_key(key):
    if not key:
        return False
    k = str(key).lower()
    return k.endswith("_at") or k in _DOC_TIME_KEYS or _date_context_excluded(k)


def scan_doc(obj, max_findings=None, max_nodes=200000):
    """JSON-dokumentum (pl. szk.ma.provenance/v1) minden szöveges levelének PHI/TAJ-szkennelése:
    value_as_entered, source.quote/locator, history[*], conversion, reconciliation, verified_by …

    Visszaad: [{kind: 'value', column: <út>, path: <út>, column_index: None, row_index: None,
    pattern}] — az út pl. 'cells[3].source.quote'; az illeszkedő értéket soha nem adja vissza."""
    findings = []
    stack = [(obj, "", None)]
    nodes = 0
    while stack:
        node, path, key = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            break
        if isinstance(node, dict):
            items = [(str(k), v) for k, v in node.items() if str(k) not in _DOC_SKIP_KEYS]
            for k, v in reversed(items):
                stack.append((v, "%s.%s" % (path, k) if path else k, k))
        elif isinstance(node, list):
            for i in range(len(node) - 1, -1, -1):
                stack.append((node[i], "%s[%d]" % (path, i), key))
        elif isinstance(node, str) and node.strip():
            for pat in value_patterns(node, dates=not _time_key(key)):
                findings.append({"kind": "value", "column": path, "path": path, "column_index": None,
                                 "row_index": None, "pattern": pat})
                if max_findings is not None and len(findings) >= max_findings:
                    return findings
    return findings


def describe_findings(findings):
    """Magyar összefoglaló a találatokról (darabszámok és oszlopnevek, értékek nélkül); üresre ''."""
    if not findings:
        return ""
    cols = []
    by_pat = {}
    for f in findings:
        if f.get("kind") == "column":
            if f.get("column") not in cols:
                cols.append(f.get("column"))
        else:
            by_pat[f.get("pattern")] = by_pat.get(f.get("pattern"), 0) + 1
    parts = []
    if cols:
        parts.append("gyanús oszlopnév: %s" % ", ".join("„%s”" % c for c in cols[:10]))
    if by_pat:
        parts.append("gyanús cella: %s" % ", ".join(
            "%s ×%d" % (PATTERN_LABELS.get(p, p), n) for p, n in sorted(by_pat.items())))
    return ("PHI-gyanú (%s). A mentés csak a _privat/ alá engedett; hamis riasztásnál indokolt, "
            "naplózott felülbírálással menthető." % "; ".join(parts))


# ---------------------------------------------------------------------------------------------
# útvonalak és gitignore-minták
# ---------------------------------------------------------------------------------------------

def _home(home):
    return Path(home) if home is not None else Path.home()


def _env(env):
    return os.environ if env is None else env


def _env_get(env, key, ignore_case=False):
    v = env.get(key)
    if v is None and ignore_case:
        for k, val in env.items():
            if k.upper() == key.upper():
                return val
    return v


def _expand(text, home, env, ignore_case=False):
    """'~', %VAR% és $VAR / ${VAR} kibontása a megadott (injektálható) környezettel."""
    s = str(text)
    if s == "~" or s.startswith(("~/", "~\\")):
        s = str(_home(home)) + s[1:]

    def sub(m):
        key = m.group(1) or m.group(2) or m.group(3)
        val = _env_get(env, key, ignore_case)
        if val is None and key.upper() in ("HOME", "USERPROFILE"):
            val = str(_home(home))
        return val if val is not None else m.group(0)

    return re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
                  sub, s)


def _ignore_case(platform=None):
    p = platform or sys.platform
    return p.startswith("win") or p == "darwin"


def _parts_under(child_parts, parent_parts, ic):
    if len(child_parts) < len(parent_parts):
        return None
    for a, b in zip(child_parts, parent_parts):
        if (a.casefold() != b.casefold()) if ic else (a != b):
            return None
    return tuple(child_parts[len(parent_parts):])


def _relative_to(child, parent, ic=False):
    """child részei parent alatt (tuple; azonosnál üres), vagy None. A szimbolikus linkek feloldott és
    nem feloldott alakját is összeveti (macOS: /var → /private/var)."""
    for f in (os.path.abspath, os.path.realpath):
        r = _parts_under(Path(f(str(child))).parts, Path(f(str(parent))).parts, ic)
        if r is not None:
            return r
    return None


def _fm(name, pat, ic):
    if ic:
        return fnmatch.fnmatchcase(name.casefold(), pat.casefold())
    return fnmatch.fnmatchcase(name, pat)


def _match_parts(pparts, parts, ic):
    # glob-komponensek illesztése; '**' nulla vagy több komponens
    if not pparts:
        return not parts
    if pparts[0] == "**":
        return any(_match_parts(pparts[1:], parts[i:], ic) for i in range(len(parts) + 1))
    return bool(parts) and _fm(parts[0], pparts[0], ic) and _match_parts(pparts[1:], parts[1:], ic)


def gitignore_pattern_matches(pattern, rel_parts, ignore_case=False, is_dir=False):
    """Egy gitignore-minta (negáció nélkül) illeszkedik-e a .gitignore mappájához relatív útra.

    Záró '/' → csak könyvtár; '/' a mintában → horgonyzott; különben bármely komponens neve."""
    parts = [p for p in rel_parts if p]
    dir_only = pattern.endswith("/")
    pat = pattern.rstrip("/")
    anchored = "/" in pat
    pparts = pat.lstrip("/").split("/")
    n = len(parts)
    for k in range(1, n + 1):
        if k == n and dir_only and not is_dir:
            continue
        prefix = parts[:k]
        if anchored:
            if _match_parts(pparts, prefix, ignore_case):
                return True
        elif _fm(prefix[-1], pparts[0], ignore_case):
            return True
    return False


def is_sensitive_path(rel_path, ignore_case=None):
    """A projekt-relatív út illeszkedik-e a kezelt blokk valamelyik mintájára."""
    ic = _ignore_case() if ignore_case is None else ignore_case
    parts = str(rel_path).replace("\\", "/").split("/")
    return any(gitignore_pattern_matches(p, parts, ic) for p in MANAGED_PATTERNS)


def _clean_rel(rel_path):
    """Projekt-relatív, '/'-elválasztós út részei, vagy None, ha szabálytalan / kifelé mutat."""
    if not isinstance(rel_path, str) or not rel_path or len(rel_path) > 1024:
        return None
    if "\\" in rel_path or "\0" in rel_path or rel_path.startswith("/") or re.match(r"^[A-Za-z]:", rel_path):
        return None
    parts = rel_path.split("/")
    if any(p in ("", ".", "..") or p.casefold() == ".git" for p in parts):
        return None
    return parts


def is_private_path(rel_path, platform=None):
    """A projekt-relatív út a _privat/ mappán belül van-e (Windows-on és macOS-en kis/nagybetű-független)."""
    parts = _clean_rel(rel_path)
    if parts is None or len(parts) < 2:
        return False
    if _ignore_case(platform):
        return parts[0].casefold() == PRIVATE_DIR.casefold()
    return parts[0] == PRIVATE_DIR


def _first_part_private(rel):
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".")]
    return bool(parts) and parts[0].casefold() == PRIVATE_DIR.casefold()


def is_private_location(project_dir, path):
    """Az út (projekt-relatív vagy abszolút) a _privat/ alatt van-e — az írt alakja szerint ÉS a szimbolikus
    linkek feloldása után is (7.4: a „_privat/** — soha” a fájl VALÓDI helyére vonatkozik; egy
    ``03_adatok/x.csv → ../_privat/titkos.csv`` link is privát). Kis/nagybetű-független (óvatos irány).
    Projekten kívüli valódi hely → False (azt a hívó külön kezeli)."""
    if not isinstance(path, str) or not path.strip() or "\0" in path:
        return False
    if not os.path.isabs(path) and _first_part_private(path):
        return True
    try:
        root = os.path.realpath(str(project_dir))
        if os.path.isabs(path):
            full = path
        else:
            full = os.path.join(root, *[p for p in path.replace("\\", "/").split("/") if p])
        real = os.path.realpath(full)
    except (OSError, ValueError):
        return False
    rc, fc = os.path.normcase(root), os.path.normcase(real)
    if fc == rc or not fc.startswith(rc.rstrip("\\/") + os.sep):
        return False
    return _first_part_private(os.path.relpath(real, root))


def _project(project_dir):
    p = Path(project_dir)
    if not p.is_dir():
        raise PrivacyError("NOT_FOUND", "A projektmappa nem létezik.")
    return p


def _sha256(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _read_bytes(path):
    try:
        with open(str(path), "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        raise PrivacyError("VALIDATION", "A várt fájl helyén mappa van: %s" % path.name)


def _atomic_write(path, data, mode=None):
    """tmp + os.replace ugyanabban a mappában. mode: kötelező jogosultság (pl. hooknál 0o755).
    A tmp-név '.ma-tmp-…' (a kezelt blokk fedi, az indításkori takarítás törli)."""
    path = Path(path)
    tmp = path.with_name("%s%s-%s.tmp" % (TMP_PREFIX, path.name, secrets.token_hex(6)))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(tmp), flags, 0o666 if mode is None else mode)
    except OSError:
        raise PrivacyError("FORBIDDEN", "A mappa nem írható: %s" % path.parent.name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            if mode is not None:
                os.chmod(str(tmp), mode)
            elif path.exists():
                os.chmod(str(tmp), stat.S_IMODE(path.stat().st_mode))
        os.replace(str(tmp), str(path))
    except PermissionError:
        _unlink_quiet(tmp)
        raise PrivacyError("LOCKED", "A fájl zárolva van vagy nem írható (%s); zárd be, majd próbáld újra."
                           % path.name)
    except BaseException:
        _unlink_quiet(tmp)
        raise


def _unlink_quiet(p):
    try:
        os.unlink(str(p))
    except OSError:
        pass


def _diff(old_text, new_text, rel):
    def lines(t):
        return [ln.rstrip("\r\n") + "\n" for ln in t.splitlines(True)]
    return "".join(difflib.unified_diff(lines(old_text), lines(new_text), fromfile="a/" + rel,
                                        tofile="b/" + rel))


def _decode_text(raw, rel):
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        return raw.decode("utf-8-sig"), bom
    except UnicodeDecodeError:
        raise PrivacyError("VALIDATION", "A(z) %s nem UTF-8 kódolású; a munkapad nem írja át." % rel)


# ---------------------------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------------------------

def _git_env():
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR",
              "GIT_OBJECT_DIRECTORY"):
        env.pop(k, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"          # ne vegyen zárat a vault mentése elől
    return env


def _run_git(cwd, args, timeout=GIT_TIMEOUT_S):
    """(returncode, stdout bájtok); None, ha nincs git, a mappa nem érhető el, vagy időtúllépés volt."""
    argv = ["git", "-c", "core.quotepath=false"] + list(args)
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(argv, cwd=None if cwd is None else str(cwd), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout,
                           shell=False, env=_git_env(), **kw)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return p.returncode, p.stdout


def _out_lines(data, sep="\n"):
    return [x for x in data.decode("utf-8", "replace").split(sep) if x]


def git_info(project_dir, timeout=GIT_TIMEOUT_S):
    """{available, repo, top, prefix} — prefix: a projekt útja a repó gyökeréhez képest ('/'-rel)."""
    info = {"available": False, "repo": False, "top": None, "prefix": None}
    if _run_git(None, ["--version"], timeout) is None:
        return info
    info["available"] = True
    if not Path(project_dir).is_dir():
        return info
    r = _run_git(project_dir, ["rev-parse", "--show-toplevel", "--show-prefix"], timeout)
    if r is None or r[0] != 0:
        return info
    lines = r[1].decode("utf-8", "replace").splitlines()
    info["repo"] = True
    info["top"] = lines[0] if lines else None
    info["prefix"] = lines[1] if len(lines) > 1 else ""
    return info


def tracked_sensitive_files(project_dir, timeout=GIT_TIMEOUT_S):
    """L1: a git-indexben már követett, a kezelt mintákra illő fájlok (projekt-relatív utak)."""
    r = _run_git(project_dir, ["ls-files", "-z", "--cached", "--", "."], timeout)
    if r is None or r[0] != 0:
        return []
    hits = [p for p in _out_lines(r[1], "\0") if is_sensitive_path(p)]
    return hits[:MAX_LISTED_PATHS]


def is_ignored(project_dir, rel_path, timeout=GIT_TIMEOUT_S):
    """(ignored, módszer). Git-repóban 'git check-ignore' (a követett fájl nem számít ignoráltnak);
    különben a kezelt blokk mintái, ha a blokk ép és a .gitignore-ban nincs negáló ('!') sor."""
    parts = _clean_rel(rel_path)
    if parts is None:
        return False, "invalid"
    rel = "/".join(parts)
    r = _run_git(project_dir, ["check-ignore", "-q", "--", rel], timeout)
    if r is not None and r[0] in (0, 1):
        return r[0] == 0, "git"
    raw = _read_bytes(Path(project_dir) / GITIGNORE_REL)
    if raw is None:
        return False, "pattern"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False, "pattern"
    try:
        spans = _block_spans(_split_lines(text))
    except PrivacyError:
        return False, "pattern"
    if not spans or any(ln.strip().startswith("!") for ln in text.splitlines()):
        return False, "pattern"
    return is_sensitive_path(rel), "pattern"


# ---------------------------------------------------------------------------------------------
# L1: vault-felismerés
# ---------------------------------------------------------------------------------------------

def vault_config_path(home=None, env=None):
    """A vault konfigurációs fájlja: $VAULT_HOME/config.json (vagy maga a $VAULT_HOME, ha .json),
    különben ~/.claude/vault/config.json."""
    env = _env(env)
    vh = env.get("VAULT_HOME")
    if vh:
        p = Path(_expand(vh, home, env))
        if p.suffix.lower() == ".json" and not p.is_dir():
            return p
        return p / "config.json"
    return _home(home) / ".claude" / "vault" / "config.json"


def load_vault_config(home=None, env=None):
    """{installed, config, root, max_depth, exclude, paused, error} — csak olvas, soha nem ír."""
    env = _env(env)
    path = vault_config_path(home, env)
    info = {"installed": False, "config": str(path), "root": str(_home(home).joinpath(*VAULT_DEFAULT_ROOT)),
            "max_depth": VAULT_DEFAULT_MAX_DEPTH, "exclude": [], "paused": False, "error": None}
    if not path.is_file():
        info["installed"] = path.parent.is_dir()      # mappa van, konfiguráció nincs → alapértékek
        return info
    info["installed"] = True
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raw = None
    if not isinstance(raw, dict):
        info["error"] = "A vault konfigurációja nem olvasható; alapértékekkel számolok (óvatos becslés)."
        return info
    root = raw.get("root")
    if isinstance(root, str) and root.strip():
        rp = Path(_expand(root.strip(), home, env))
        info["root"] = str(rp if rp.is_absolute() else _home(home) / rp)
    md = raw.get("max_depth")
    if isinstance(md, int) and not isinstance(md, bool) and 0 <= md <= 64:
        info["max_depth"] = md
    ex = raw.get("exclude")
    if isinstance(ex, str):
        ex = [ex]
    if isinstance(ex, list):
        info["exclude"] = [x.strip() for x in ex if isinstance(x, str) and x.strip()]
    paused = raw.get("paused")
    info["paused"] = paused is True or (isinstance(paused, str)
                                        and paused.strip().lower() in ("true", "1", "yes", "igen"))
    return info


def _excluded(rel_parts, entries, project, root, ic):
    for e in entries:
        if "/" in e or "\\" in e:
            ep = Path(e.replace("\\", "/"))
            base = ep if ep.is_absolute() else Path(root) / ep
            if _relative_to(project, base, ic) is not None:
                return e
        elif any(_fm(part, e, ic) for part in rel_parts):
            return e
    return None


def vault_status(project_dir, home=None, env=None, platform=None):
    """L1: követi-e a vault a projektet (gyökér alatt, mélység ≤ max_depth, nincs kizárva, nem szünetel).

    A max_depth-nél mélyebb projektet is követettnek veszi, ha egy max_depth-en belüli őse
    git-munkafa (annak 'git add -A'-ja a projektet is viszi)."""
    cfg = load_vault_config(home, env)
    ic = _ignore_case(platform)
    out = {"tracked": False, "root": cfg["root"] if cfg["installed"] else None, "reason": "",
           "installed": cfg["installed"], "config": cfg["config"], "max_depth": cfg["max_depth"],
           "depth": None, "under_root": False, "paused": cfg["paused"], "excluded": False,
           "exclude_match": None, "via": None, "error": cfg["error"]}
    if not cfg["installed"]:
        out["reason"] = "Nem találtam vault-konfigurációt: a vault nincs telepítve (vagy a $VAULT_HOME máshová mutat)."
        return out
    project = Path(project_dir)
    rel = _relative_to(project, cfg["root"], ic)
    if rel is None:
        out["reason"] = "A projekt nincs a vault gyökere (%s) alatt." % cfg["root"]
        return out
    out["under_root"] = True
    out["depth"] = len(rel)
    hit = _excluded(rel, cfg["exclude"], project, cfg["root"], ic)
    if hit is not None:
        out["excluded"] = True
        out["exclude_match"] = hit
    if cfg["paused"]:
        out["reason"] = ("A vault szünetel (paused): most nem tol fel semmit; a szünet feloldása után "
                         "újra követi a projektet.")
        return out
    if hit is not None:
        out["reason"] = "A projekt ki van zárva a vault mentéséből (exclude: %s)." % hit
        return out
    if len(rel) <= cfg["max_depth"]:
        out["tracked"] = True
        out["reason"] = ("A projekt a vault gyökere (%s) alatt van (mélység %d ≤ %d): a munkamenet végén "
                         "a vault git add -A + commit + push-t futtat egy privát GitHub-repóba."
                         % (cfg["root"], len(rel), cfg["max_depth"]))
        return out
    root = Path(cfg["root"])
    for k in range(1, cfg["max_depth"] + 1):
        anc = root.joinpath(*rel[:k])
        if (anc / ".git").exists():
            out["tracked"] = True
            out["via"] = "/".join(rel[:k])
            out["reason"] = ("A projekt a(z) %s mappa része, amelyet a vault ment (mélység ≤ %d): a "
                             "munkamenet végén git add -A + push." % (out["via"], cfg["max_depth"]))
            return out
    out["reason"] = ("A projekt mélyebben van (%d), mint a vault max_depth értéke (%d), és nincs ezen "
                     "belüli git-munkafa őse." % (len(rel), cfg["max_depth"]))
    return out


# ---------------------------------------------------------------------------------------------
# felhőszinkron (7.5)
# ---------------------------------------------------------------------------------------------

def _read_shell_folders():
    """Windows: HKCU\\…\\Explorer\\User Shell Folders (Personal, Desktop); máshol {}."""
    try:
        import winreg
    except ImportError:
        return {}
    out = {}
    key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            for name in ("Personal", "Desktop"):
                try:
                    out[name] = winreg.QueryValueEx(k, name)[0]
                except OSError:
                    pass
    except OSError:
        return {}
    return out


def detect_cloud_sync(project_dir, home=None, env=None, platform=None, registry=None):
    """{kind: 'onedrive'|'icloud', path, …} ha a projektet felhőszolgáltatás szinkronizálja, különben None.

    registry: a 'User Shell Folders' értékei (dict) tesztekhez; None → winreg (csak Windows-on)."""
    plat = platform or sys.platform
    env = _env(env)
    project = Path(project_dir)
    if plat.startswith("win"):
        return _detect_onedrive(project, home, env, registry)
    if plat == "darwin":
        return _detect_icloud(project, home)
    return None


def _detect_onedrive(project, home, env, registry):
    roots = []
    for key in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        v = _env_get(env, key, True)
        if v and v.strip():
            roots.append(Path(_expand(v.strip(), home, env, True)))
    if registry is None:
        registry = _read_shell_folders() if sys.platform.startswith("win") else {}
    kfm = False
    personal = registry.get("Personal") if isinstance(registry, dict) else None
    if isinstance(personal, str) and personal.strip():
        pp = Path(_expand(personal.strip(), home, env, True))
        if any(_relative_to(pp, r, True) is not None for r in roots):
            kfm = True
        else:
            for i, part in enumerate(pp.parts):
                if part.casefold().startswith("onedrive"):
                    roots.append(Path(*pp.parts[:i + 1]))
                    kfm = True
                    break
    for r in roots:
        if _relative_to(project, r, True) is not None:
            return {"kind": "onedrive", "path": str(r), "known_folder_move": kfm}
    return None


def _detect_icloud(project, home):
    h = _home(home)
    drive = h / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    dd = (drive / "Documents").exists()
    if _relative_to(project, drive, True) is not None:
        return {"kind": "icloud", "path": str(drive), "desktop_documents": dd}
    if dd:
        for name in ("Documents", "Desktop"):
            base = h / name
            if _relative_to(project, base, True) is not None:
                return {"kind": "icloud", "path": str(base), "desktop_documents": True}
    return None


# ---------------------------------------------------------------------------------------------
# L2: kezelt .gitignore-blokk
# ---------------------------------------------------------------------------------------------

def managed_block_lines():
    return [BLOCK_BEGIN, BLOCK_NOTE] + list(MANAGED_PATTERNS) + [BLOCK_END]


def _split_lines(text):
    # sorvégekkel együtt; az összefűzés bájtra visszaadja az eredetit
    return re.findall(r"[^\n]*\n|[^\n]+\Z", text)


def _block_spans(lines):
    """[(kezdő, záró)] sorindexek; párosítatlan jelölőnél PrivacyError."""
    spans = []
    start = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == BLOCK_BEGIN:
            if start is not None:
                break
            start = i
        elif s == BLOCK_END:
            if start is None:
                break
            spans.append((start, i))
            start = None
    else:
        if start is None:
            return spans
    raise PrivacyError("VALIDATION", "A .gitignore kezelt blokkja sérült (párosítatlan jelölő). Javítsd "
                                     "kézzel a „%s” és a „%s” sort, majd próbáld újra." % (BLOCK_BEGIN, BLOCK_END))


def _gitignore_new_text(old):
    nl = "\r\n" if "\r\n" in old else "\n"
    block = nl.join(managed_block_lines()) + nl
    lines = _split_lines(old)
    spans = _block_spans(lines)
    if spans:
        out = []
        prev = 0
        for n, (a, b) in enumerate(spans):
            out.extend(lines[prev:a])
            if n == 0:
                out.append(block)
            prev = b + 1
        out.extend(lines[prev:])
        return "".join(out)
    prefix = old
    if prefix and not prefix.endswith("\n"):
        prefix += nl
    if prefix.strip() and not prefix.endswith(nl + nl) and not prefix.endswith("\n\n"):
        prefix += nl
    return prefix + block


def gitignore_block_present(project_dir):
    """Van-e ép, naprakész kezelt blokk a projekt .gitignore-jában."""
    raw = _read_bytes(Path(project_dir) / GITIGNORE_REL)
    if raw is None:
        return False
    try:
        text = raw.decode("utf-8-sig")
        spans = _block_spans(_split_lines(text))
    except (UnicodeDecodeError, PrivacyError):
        return False
    if not spans:
        return False
    lines = _split_lines(text)
    a, b = spans[0]
    have = {ln.strip() for ln in lines[a + 1:b]}
    return all(p in have for p in MANAGED_PATTERNS)


def plan_gitignore(project_dir):
    """Diff-előnézet a kezelt blokkhoz (nem ír). {path, exists, changed, diff, base_sha256, block_present}."""
    project = _project(project_dir)
    path = project / GITIGNORE_REL
    raw = _read_bytes(path)
    old, _bom = ("", False) if raw is None else _decode_text(raw, GITIGNORE_REL)
    new = _gitignore_new_text(old)
    return {"path": GITIGNORE_REL, "exists": raw is not None, "changed": new != old,
            "diff": _diff(old, new, GITIGNORE_REL) if new != old else "",
            "base_sha256": _sha256(raw), "block_present": gitignore_block_present(project)}


def apply_gitignore(project_dir, base_sha256=_NO_CHECK):
    """A kezelt blokk beírása (csak a felhasználó kattintására). base_sha256: az előnézet alapja
    (None = a fájl akkor nem létezett) — ha azóta változott, CONFLICT. A blokkon kívüli sorok
    bájtra változatlanok."""
    project = _project(project_dir)
    path = project / GITIGNORE_REL
    if path.is_symlink():
        raise PrivacyError("FORBIDDEN", "A .gitignore szimbolikus link; a munkapad nem írja felül.")
    raw = _read_bytes(path)
    if base_sha256 is not _NO_CHECK and base_sha256 != _sha256(raw):
        raise PrivacyError("CONFLICT", "A .gitignore az előnézet óta megváltozott; nézd meg újra a diffet.")
    old, bom = ("", False) if raw is None else _decode_text(raw, GITIGNORE_REL)
    new = _gitignore_new_text(old)
    result = {"path": GITIGNORE_REL, "exists": raw is not None, "changed": new != old,
              "diff": _diff(old, new, GITIGNORE_REL) if new != old else "", "base_sha256": _sha256(raw)}
    if new != old:
        data = (b"\xef\xbb\xbf" if bom else b"") + new.encode("utf-8")
        _atomic_write(path, data)
        result["sha256"] = _sha256(data)
    else:
        result["sha256"] = _sha256(raw)
    result["applied"] = True
    return result


# ---------------------------------------------------------------------------------------------
# Claude deny-szabály
# ---------------------------------------------------------------------------------------------

def _pairs_no_dup(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise PrivacyError("VALIDATION", "A .claude/settings.json ismétlődő kulcsot tartalmaz; javítsd kézzel, "
                                             "a munkapad nem írja felül.")
        seen[k] = v
    return seen


def _load_settings(raw, rel):
    if raw is None:
        return {}, "", False
    text, bom = _decode_text(raw, rel)
    if not text.strip():
        return {}, text, bom
    try:
        obj = json.loads(text, object_pairs_hook=_pairs_no_dup)
    except ValueError as exc:
        if isinstance(exc, PrivacyError):
            raise
        raise PrivacyError("VALIDATION", "A(z) %s nem érvényes JSON; javítsd kézzel, a munkapad nem írja felül."
                           % rel)
    if not isinstance(obj, dict):
        raise PrivacyError("VALIDATION", "A(z) %s gyökere nem objektum; a munkapad nem írja felül." % rel)
    return obj, text, bom


def _deny_list(obj):
    perms = obj.get("permissions")
    if perms is None:
        return None, []
    if not isinstance(perms, dict):
        raise PrivacyError("VALIDATION", "A settings.json 'permissions' kulcsa nem objektum; javítsd kézzel.")
    deny = perms.get("deny")
    if deny is None:
        return perms, []
    if not isinstance(deny, list):
        raise PrivacyError("VALIDATION", "A settings.json 'permissions.deny' kulcsa nem lista; javítsd kézzel.")
    return perms, deny


def _settings_new_text(raw):
    obj, old, bom = _load_settings(raw, CLAUDE_SETTINGS_REL)
    perms, deny = _deny_list(obj)
    missing = [r for r in DENY_RULES if r not in deny]
    if not missing:
        return old, old, bom, missing
    if perms is None:
        perms = {}
        obj["permissions"] = perms
    perms["deny"] = list(deny) + missing
    m = re.search(r"\n([ \t]+)\S", old)
    indent = m.group(1) if m else 2
    new = json.dumps(obj, indent=indent, ensure_ascii=False) + "\n"
    if "\r\n" in old:
        new = new.replace("\n", "\r\n")
    return old, new, bom, missing


def plan_claude_deny(project_dir):
    """Diff-előnézet a deny-szabályokhoz (nem ír). {path, exists, changed, diff, base_sha256, missing_rules}."""
    project = _project(project_dir)
    raw = _read_bytes(project / ".claude" / "settings.json")
    old, new, _bom, missing = _settings_new_text(raw)
    return {"path": CLAUDE_SETTINGS_REL, "exists": raw is not None, "changed": new != old,
            "diff": _diff(old, new, CLAUDE_SETTINGS_REL) if new != old else "",
            "base_sha256": _sha256(raw), "missing_rules": missing}


def apply_claude_deny(project_dir, base_sha256=_NO_CHECK):
    """A deny-szabályok összefésülése a .claude/settings.json-ba (a többi kulcs és a meglévő
    szabályok megmaradnak). Csak kifejezett hívásra; base_sha256 eltérésnél CONFLICT."""
    project = _project(project_dir)
    path = project / ".claude" / "settings.json"
    if path.is_symlink():
        raise PrivacyError("FORBIDDEN", "A .claude/settings.json szimbolikus link; a munkapad nem írja felül.")
    raw = _read_bytes(path)
    if base_sha256 is not _NO_CHECK and base_sha256 != _sha256(raw):
        raise PrivacyError("CONFLICT", "A .claude/settings.json az előnézet óta megváltozott; nézd meg újra a diffet.")
    old, new, bom, missing = _settings_new_text(raw)
    result = {"path": CLAUDE_SETTINGS_REL, "exists": raw is not None, "changed": new != old,
              "diff": _diff(old, new, CLAUDE_SETTINGS_REL) if new != old else "",
              "base_sha256": _sha256(raw), "missing_rules": missing, "applied": True}
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (b"\xef\xbb\xbf" if bom else b"") + new.encode("utf-8")
        _atomic_write(path, data)
        result["sha256"] = _sha256(data)
    else:
        result["sha256"] = _sha256(raw)
    return result


def deny_rule_present(project_dir):
    """A .claude/settings.json és a settings.local.json deny-listái együtt tartalmazzák-e a szabályokat."""
    have = set()
    for rel in (CLAUDE_SETTINGS_REL, CLAUDE_LOCAL_SETTINGS_REL):
        try:
            raw = _read_bytes(Path(project_dir).joinpath(*rel.split("/")))
            obj, _t, _b = _load_settings(raw, rel)
            _p, deny = _deny_list(obj)
        except PrivacyError:
            continue
        have.update(x for x in deny if isinstance(x, str))
    return all(r in have for r in DENY_RULES)


# ---------------------------------------------------------------------------------------------
# L3: pre-commit őr (opt-in)
# ---------------------------------------------------------------------------------------------

_HOOK_TEMPLATE = r'''#!@PYTHON@
# -*- coding: utf-8 -*-
# @MARKER@ v@VERSION@ — a MA-munkapad generálta (csak stdlib); kézzel ne szerkeszd, a munkapad
# felülírja. Csak akkor utasítja el a commitot, ha érzékeny fájl (_privat/, *_PHI* …) MÉGIS az
# indexbe került (kényszerített add vagy korábban követett fájl). A korábbi hookot láncolja.
import fnmatch
import os
import shutil
import subprocess
import sys

PATTERNS = @PATTERNS@
CHAINED = @CHAINED@
IGNORE_CASE = sys.platform.startswith("win") or sys.platform == "darwin"


def _fm(name, pat):
    if IGNORE_CASE:
        return fnmatch.fnmatchcase(name.casefold(), pat.casefold())
    return fnmatch.fnmatchcase(name, pat)


def _match_parts(pparts, parts):
    if not pparts:
        return not parts
    if pparts[0] == "**":
        return any(_match_parts(pparts[1:], parts[i:]) for i in range(len(parts) + 1))
    return bool(parts) and _fm(parts[0], pparts[0]) and _match_parts(pparts[1:], parts[1:])


def is_sensitive(path):
    """A repó-relatív út illeszkedik-e (bármely mélységben) a kezelt mintákra."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    for pattern in PATTERNS:
        dir_only = pattern.endswith("/")
        pparts = pattern.strip("/").split("/")
        for k in range(1, len(parts) + 1):
            if k == len(parts) and dir_only:
                continue
            prefix = parts[:k]
            if len(pparts) == 1:
                if _fm(prefix[-1], pparts[0]):
                    return True
            elif any(_match_parts(pparts, prefix[s:]) for s in range(len(prefix))):
                return True
    return False


def _say(text):
    data = text.encode(getattr(sys.stderr, "encoding", None) or "utf-8", "replace")
    try:
        sys.stderr.buffer.write(data)
    except AttributeError:
        sys.stderr.write(data.decode("ascii", "replace"))
    sys.stderr.flush()


def staged_paths():
    try:
        p = subprocess.run(["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only", "-z",
                            "--diff-filter=ACMRT"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    if p.returncode != 0:
        return None
    return [x for x in p.stdout.decode("utf-8", "replace").split("\0") if x]


def _chained_argv(path):
    if os.name != "nt":
        return [path] if os.access(path, os.X_OK) else None
    with open(path, "rb") as fh:
        first = fh.readline(300).decode("utf-8", "replace").strip()
    if first.startswith("#!"):
        parts = first[2:].split()
        if parts and os.path.basename(parts[0]).lower() in ("env", "env.exe"):
            parts = parts[1:]
        if parts:
            exe = shutil.which(parts[0]) or shutil.which(os.path.basename(parts[0]))
            if exe:
                return [exe] + parts[1:] + [path]
    sh = shutil.which("sh")
    return [sh, path] if sh else None


def run_chained():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CHAINED)
    if not os.path.isfile(path):
        return 0
    argv = _chained_argv(path)
    if argv is None:
        return 0
    try:
        return subprocess.call(argv + sys.argv[1:])
    except OSError:
        _say("ma-munkapad őr: a láncolt hook (%s) nem indítható.\n" % CHAINED)
        return 1


def main():
    paths = staged_paths()
    if paths is None:
        _say("ma-munkapad őr: az index nem kérdezhető le, ezért a commit leállt (biztonsági alapállás).\n")
        return 1
    bad = [p for p in paths if is_sensitive(p)]
    if bad:
        lines = ["ma-munkapad őr: érzékeny fájl került az indexbe, a commit leállt:"]
        lines += ["  " + p for p in bad[:20]]
        if len(bad) > 20:
            lines.append("  … és még %d" % (len(bad) - 20))
        lines.append("Vedd ki az indexből (a fájl a lemezen marad):  git rm --cached -- <út>")
        lines.append("A vault mentése addig elmarad; a kezelt .gitignore-blokk a szokásos 'git add -A'-t "
                     "már kiszűri.")
        _say("\n".join(lines) + "\n")
        return 1
    return run_chained()


if __name__ == "__main__":
    sys.exit(main())
'''

PRECOMMIT_NOTE = ("A pre-commit őr csak tartalék: akkor állítja meg a commitot (a vault mentését is), ha "
                  "érzékeny fájl mégis az indexbe került — kényszerített 'git add -f' vagy korábban "
                  "követett fájl miatt. A szokásos 'git add -A'-t a kezelt .gitignore-blokk már kiszűri.")


def _hook_python(python=None):
    if python:
        return str(python)
    # nem a futó (esetleg venv-beli) értelmező: ha az később eltűnik, minden commit — a vault
    # mentése is — elhasalna; a hook csak stdlib-et használ, bármely Python 3 megfelel
    return "/usr/bin/env python" if os.name == "nt" else "/usr/bin/env python3"


def precommit_hook_script(python=None):
    """A pre-commit őr szkriptje (szöveg). python: az értelmező a shebangben (alapból
    '/usr/bin/env python3'; Windows-on '/usr/bin/env python', a Git for Windows sh-ja ezt kezeli)."""
    return (_HOOK_TEMPLATE.replace("@PYTHON@", _hook_python(python))
            .replace("@MARKER@", HOOK_MARKER).replace("@VERSION@", str(HOOK_VERSION))
            .replace("@PATTERNS@", repr(list(MANAGED_PATTERNS))).replace("@CHAINED@", repr(CHAINED_HOOK_NAME)))


def _is_our_hook(path):
    raw = _read_bytes(path) if path.is_file() else None
    return raw is not None and HOOK_MARKER.encode("utf-8") in raw[:600]


def _hooks_dir(project_dir, timeout=GIT_TIMEOUT_S):
    """(hooks mappa, közös-e más repókkal) vagy PrivacyError, ha nincs git / nem repó."""
    info = git_info(project_dir, timeout)
    if not info["available"]:
        raise PrivacyError("VALIDATION", "A git nem érhető el; a pre-commit őr nem telepíthető.")
    if not info["repo"]:
        raise PrivacyError("VALIDATION", "A projekt nincs git-repóban; a pre-commit őr csak git-repóban értelmes.")
    r = _run_git(project_dir, ["rev-parse", "--git-path", "hooks", "--git-common-dir"], timeout)
    if r is None or r[0] != 0:
        raise PrivacyError("INTERNAL", "A git hook-mappája nem kérdezhető le.")
    lines = r[1].decode("utf-8", "replace").splitlines()
    hooks = Path(project_dir) / lines[0]
    common = Path(project_dir) / lines[1] if len(lines) > 1 else hooks.parent
    inside = (_relative_to(hooks, common) is not None
              or (info["top"] and _relative_to(hooks, info["top"]) is not None))
    return Path(os.path.abspath(str(hooks))), not inside


def plan_precommit_guard(project_dir, python=None):
    """Előnézet (nem ír): {hook_path, installed, will_chain, chained_path, shared_hooks_dir, script, note}."""
    project = _project(project_dir)
    hooks, shared = _hooks_dir(project)
    hook = hooks / "pre-commit"
    ours = _is_our_hook(hook)
    return {"hook_path": str(hook), "installed": ours, "will_chain": hook.exists() and not ours,
            "chained_path": str(hooks / CHAINED_HOOK_NAME), "shared_hooks_dir": shared,
            "script": precommit_hook_script(python), "note": PRECOMMIT_NOTE}


def install_precommit_guard(project_dir, python=None):
    """Az őr telepítése (csak kifejezett hívásra). A meglévő idegen hook átnevezve láncolódik; a saját
    hook frissül (nincs dupla láncolás). Közös (repón kívüli) core.hooksPath esetén CONFLICT."""
    project = _project(project_dir)
    hooks, shared = _hooks_dir(project)
    if shared:
        raise PrivacyError("CONFLICT", "A repó core.hooksPath beállítása a repón kívüli, közös hook-mappára "
                                       "mutat; az őrt ott kézzel kell láncolni.")
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    chained = hooks / CHAINED_HOOK_NAME
    ours = _is_our_hook(hook)
    foreign = (hook.exists() or hook.is_symlink()) and not ours
    if foreign and (chained.exists() or chained.is_symlink()):
        raise PrivacyError("CONFLICT", "Már van láncolt hook (%s) és egy másik pre-commit is; kézi rendezés "
                                       "kell." % CHAINED_HOOK_NAME)
    data = precommit_hook_script(python).encode("utf-8")
    tmp = hooks / (".pre-commit.%s.tmp" % secrets.token_hex(6))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o755)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if os.name != "nt":
            os.chmod(str(tmp), 0o755)
        if foreign:
            os.replace(str(hook), str(chained))
        try:
            os.replace(str(tmp), str(hook))
        except OSError:
            if foreign:
                os.replace(str(chained), str(hook))      # visszaállítás
            raise
    except BaseException:
        _unlink_quiet(tmp)
        raise
    return {"installed": True, "hook_path": str(hook), "updated": ours,
            "chained_path": str(chained) if (chained.exists() or chained.is_symlink()) else None,
            "note": PRECOMMIT_NOTE}


def uninstall_precommit_guard(project_dir):
    """Az őr eltávolítása; a láncolt korábbi hook visszakerül a helyére."""
    project = _project(project_dir)
    hooks, _shared = _hooks_dir(project)
    hook = hooks / "pre-commit"
    chained = hooks / CHAINED_HOOK_NAME
    if not _is_our_hook(hook):
        return {"removed": False, "restored": False}
    if chained.exists() or chained.is_symlink():
        os.replace(str(chained), str(hook))
        return {"removed": True, "restored": True}
    os.unlink(str(hook))
    return {"removed": True, "restored": False}


def precommit_guard_installed(project_dir, timeout=GIT_TIMEOUT_S):
    try:
        hooks, _shared = _hooks_dir(project_dir, timeout)
    except PrivacyError:
        return False
    return _is_our_hook(hooks / "pre-commit")


# ---------------------------------------------------------------------------------------------
# L6: „Már felment?”
# ---------------------------------------------------------------------------------------------

def sensitive_pathspecs():
    """A kezelt minták git-pathspecként (a projektmappához, azaz a cwd-hez relatívan)."""
    out = []
    for p in MANAGED_PATTERNS:
        dir_only = p.endswith("/")
        pat = p.rstrip("/")
        if "/" in pat:
            specs = [pat + "/**"] if dir_only else [pat, pat + "/**"]
        else:
            specs = ["**/" + pat + "/**"] if dir_only else ["**/" + pat, "**/" + pat + "/**"]
        out.extend(":(glob)" + s for s in specs)
    return out


def already_pushed(project_dir, paths=None, timeout=GIT_TIMEOUT_S):
    """L6: szerepel-e érzékeny út a git-történetben ('git log --all --format=%H -- <út>').

    paths: projekt-relatív utak (szó szerint); None → a kezelt minták. Visszaad: {checked, git, repo,
    in_history, in_remote, commits, paths, reason}. Git hiánya / időtúllépés nem hiba: checked=False."""
    out = {"checked": False, "git": False, "repo": False, "in_history": False, "in_remote": None,
           "commits": 0, "paths": [], "reason": ""}
    info = git_info(project_dir, timeout)
    out["git"] = info["available"]
    if not info["available"]:
        out["reason"] = "A git nem érhető el; a történet nem ellenőrizhető."
        return out
    out["repo"] = info["repo"]
    if not info["repo"]:
        out["reason"] = "A projekt nincs git-repóban; nincs mit ellenőrizni."
        out["checked"] = True
        return out
    if paths is None:
        specs = sensitive_pathspecs()
    else:
        specs = []
        for p in paths:
            parts = _clean_rel(p)
            if parts is None:
                raise PrivacyError("VALIDATION", "Érvénytelen projekt-relatív út.")
            specs.append(":(literal)" + "/".join(parts))
        if not specs:
            out["checked"] = True
            return out
    r = _run_git(project_dir, ["log", "--all", "--format=%H", "--"] + specs, timeout)
    if r is None or r[0] != 0:
        out["reason"] = "A git-történet nem kérdezhető le (időtúllépés vagy hiba)."
        return out
    commits = _out_lines(r[1])
    out["checked"] = True
    out["commits"] = len(commits)
    out["in_history"] = bool(commits)
    if not commits:
        out["reason"] = "Érzékeny útvonal nem került a git-történetbe."
        return out
    names = _run_git(project_dir, ["log", "--all", "--format=", "--name-only", "--relative", "--"] + specs,
                     timeout)
    if names is not None and names[0] == 0:
        seen = []
        for n in _out_lines(names[1]):
            if n not in seen:
                seen.append(n)
        out["paths"] = seen[:MAX_LISTED_PATHS]
    rem = _run_git(project_dir, ["log", "--remotes", "--format=%H", "-n", "1", "--"] + specs, timeout)
    if rem is not None and rem[0] == 0:
        out["in_remote"] = bool(_out_lines(rem[1]))
    out["reason"] = ("Érzékeny útvonal már szerepel a git-történetben (%d commit)%s."
                     % (len(commits), ", és távoli ágon is" if out["in_remote"] else ""))
    return out


HISTORY_ACTIONS = ("Érzékeny útvonal már szerepel a git-történetben. Teendők: történet-átírás "
                   "(git filter-repo), force push, a GitHub-támogatás megkeresése a gyorsítótárazott "
                   "nézetek törléséhez; betegszintű adatnál az adatvédelmi tisztviselő értesítése (a GDPR "
                   "33. cikke szerinti mérlegelés).")


# ---------------------------------------------------------------------------------------------
# írás-tartás és összesített állapot
# ---------------------------------------------------------------------------------------------

def can_write(project_dir, rel_path, data_class, *, home=None, env=None, platform=None, consent=False,
              phi_detected=False):
    """Írás-tartás (7.4): (ok, indoklás). Adatot tartalmazó fájl írása előtt hívandó.

    - C osztály: csak a _privat/ alá.
    - PHI-találat (phi_detected): csak a _privat/ alá (a hamis riasztás felülbírálása a hívóé).
    - B/C osztály és vault által követett projekt: a cél legyen .gitignore-olt (git check-ignore;
      git nélkül a kezelt blokk mintái). B osztálynál kifejezett, naplózott hozzájárulás (consent)
      feloldja."""
    dc = normalize_data_class(data_class)
    parts = _clean_rel(rel_path)
    if parts is None:
        return False, "Érvénytelen vagy a projektmappán kívülre mutató út."
    in_private = is_private_path(rel_path, platform)
    if dc == "C" and not in_private:
        return False, "C osztályú (betegszintű) adat csak a _privat/ mappába írható."
    if phi_detected and not in_private:
        return False, ("PHI-gyanús tartalom: a mentés csak a _privat/ alá engedett; hamis riasztásnál "
                       "indokolt, naplózott felülbírálással menthető.")
    if dc == "A":
        return True, "A osztály (publikált aggregált adat): nincs írás-tartás."
    vault = vault_status(project_dir, home=home, env=env, platform=platform)
    if not vault["tracked"]:
        return True, "Nincs írás-tartás: %s" % vault["reason"]
    if dc == "B" and consent:
        return True, ("B osztály kifejezett, naplózott hozzájárulással: a cél a vault privát repójába "
                      "is felkerülhet.")
    ignored, _how = is_ignored(project_dir, "/".join(parts))
    if ignored:
        return True, "A cél .gitignore-ban van: a vault nem tolja fel."
    return False, ("Írás-tartás: %s osztály, a projektet a vault követi (munkamenet végén GitHubra kerül), "
                   "és a cél nincs a .gitignore-ban. Lehetőségek: a kezelt .gitignore-blokk beírása "
                   "(és mentés a _privat/ alá), vagy a projekt áthelyezése a vault gyökerén kívülre." % dc)


def status(project_dir, data_class, *, home=None, env=None, platform=None, registry=None,
           check_history=True, timeout=GIT_TIMEOUT_S):
    """Az adatvédelmi ellenőrzés összesítése (3.5.2, 3.5.16); JSON-képes dict.

    Kulcsok: data_class, data_class_label, vault {tracked, root, reason, …}, cloud_sync ({kind, path}
    vagy None), git, gitignore_block_present, deny_rule_present, precommit_guard_installed,
    tracked_sensitive_files, history (L6), write_hold {active, reason}, open_blocked {blocked,
    reasons}, recommendations (magyar szövegek), actions (a felajánlható kattintások)."""
    dc = normalize_data_class(data_class)
    project = _project(project_dir)
    vault = vault_status(project, home=home, env=env, platform=platform)
    cloud = detect_cloud_sync(project, home=home, env=env, platform=platform, registry=registry)
    git = git_info(project, timeout)
    block = gitignore_block_present(project)
    deny = deny_rule_present(project)
    tracked = tracked_sensitive_files(project, timeout) if git["repo"] else []
    guard = precommit_guard_installed(project, timeout) if git["repo"] else False
    if check_history:
        history = already_pushed(project, timeout=timeout)
    else:
        history = {"checked": False, "git": git["available"], "repo": git["repo"], "in_history": False,
                   "in_remote": None, "commits": 0, "paths": [], "reason": "Nem ellenőrizve."}
    sensitive = dc in ("B", "C")
    private_ignored = is_ignored(project, PRIVATE_DIR + "/ma-proba.csv", timeout)[0]

    hold = {"active": False, "reason": None}
    if sensitive and vault["tracked"] and not private_ignored:
        hold = {"active": True,
                "reason": ("%s osztály: a projektet a vault követi, és a _privat/ nincs a .gitignore-ban — "
                           "adat nem írható, amíg a kezelt .gitignore-blokk nincs beírva (vagy a projekt "
                           "nincs áthelyezve)." % dc)}
    open_reasons = []
    if dc == "C" and vault["tracked"]:
        if not block:
            open_reasons.append("hiányzik a kezelt .gitignore-blokk (L2)")
        if not guard:
            open_reasons.append("nincs telepítve a pre-commit őr (L3)")
        if tracked:
            open_reasons.append("érzékeny fájl már követett a git-indexben (L1)")

    recs = []
    actions = []
    if vault.get("error"):
        recs.append(vault["error"])
    if vault["tracked"]:
        recs.append(vault["reason"])
        if not block:
            recs.append("Írd be a kezelt .gitignore-blokkot: a _privat/, a *_PHI* és a többi érzékeny "
                        "minta kimarad a vault mentéséből.")
        if sensitive:
            name = Path(os.path.abspath(str(project))).name
            recs.append("Ha a projektet egészen ki akarod hagyni a vault mentéséből: 'vault pause', vagy "
                        "vedd fel a mappa nevét (%s) a vault exclude listájába (%s). A munkapad a vault "
                        "konfigurációját nem módosítja." % (name, vault["config"]))
        if dc == "C" and not guard:
            recs.append("C osztálynál a vault gyökere alatt a pre-commit őr is kell. " + PRECOMMIT_NOTE)
    elif vault["under_root"] and (vault["paused"] or vault["excluded"]):
        recs.append(vault["reason"])
    if not block and (vault["tracked"] or sensitive):
        actions.append({"id": "gitignore", "label": "Kezelt .gitignore-blokk beírása (diff-előnézettel)"})
    if tracked:
        recs.append("%d érzékeny fájl már követett a git-indexben (pl. %s). Vedd ki az indexből: "
                    "git rm --cached -- <út> (a fájl a lemezen marad)." % (len(tracked), tracked[0]))
    if history.get("in_history"):
        recs.append(HISTORY_ACTIONS)
    if cloud:
        svc = "OneDrive" if cloud["kind"] == "onedrive" else "iCloud"
        if sensitive:
            recs.append("A projekt mappáját a(z) %s szinkronizálja (%s): helyezd a _privat/ mappát "
                        "szinkronizálatlan helyre, és hivatkozd át a doc_roots-ban." % (svc, cloud["path"]))
        else:
            recs.append("A projekt mappáját a(z) %s szinkronizálja (%s); A osztálynál ez csak figyelmeztetés."
                        % (svc, cloud["path"]))
    if sensitive and not deny:
        recs.append("Javasolt Claude deny-szabály a .claude/settings.json-ban (%s): a Claude-munkamenetben "
                    "olvasott fájl tartalma a modell-szolgáltatóhoz kerül." % ", ".join(DENY_RULES))
        actions.append({"id": "deny", "label": ".claude/settings.json deny-javaslat (diff-előnézettel)"})
    if git["repo"] and not guard and (dc == "C" or (sensitive and vault["tracked"])):
        actions.append({"id": "precommit", "label": "Pre-commit őr telepítése (opt-in)"})
    if open_reasons:
        recs.append("C osztály: a vault gyökere alatt a projekt csak védelemmel nyitható meg — " +
                    "; ".join(open_reasons) + ". (Vagy szüneteltesd a vaultot, illetve zárd ki a projektet.)")

    return {
        "data_class": dc,
        "data_class_label": DATA_CLASS_LABELS[dc],
        "vault": vault,
        "cloud_sync": cloud,
        "git": git,
        "gitignore_block_present": block,
        "deny_rule_present": deny,
        "precommit_guard_installed": guard,
        "tracked_sensitive_files": tracked,
        "history": history,
        "write_hold": hold,
        "open_blocked": {"blocked": bool(open_reasons), "reasons": open_reasons},
        "recommendations": recs,
        "actions": actions,
    }
