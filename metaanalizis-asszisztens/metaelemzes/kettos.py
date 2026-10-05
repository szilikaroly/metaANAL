# -*- coding: utf-8 -*-
"""Kettős (független) adatkinyerés összevetése és egyeztetése (terv 3.5.5, 4.9, 6.4 X009, 11. fejezet 5. döntés; E6).

Két kinyerő (A és B) ugyanannak a kimenetnek a tábláját tölti ki egymástól függetlenül
(``03_adatok/kettos/<kimenet>.A.csv`` és ``.B.csv``). A motor cellánként összeveti őket, megmondja, mi lehet az
eltérés oka (súgó), mennyit mozdítana az eltérés a hatásméreten (és spec mellett az összesített becslésen), és
egyezési statisztikát ad (egyezés %, Cohen-féle κ CI-vel, számmezőkre ICC(A,1)). A döntések
(``<kimenet>.consensus.json``, ``szk.ma.consensus/v1``) alapján elkészül a konszenzus-tábla, az A tábla
formátumában.

    compare(a, b, key=None, tolerance=None, measure=None, spec=None, …)   → szk.ma.compare-result/v1
    reconcile(compare_result, decisions, a=None, b=None, …)              → {consensus (szk.ma.consensus/v1), table}
    consensus_table(a, b, consensus, key=None)                           → {header, rows[{row_uid, cells}], reconciled}
    agreement_report(result, consensus=None)                             → {text {hu, en}, table [...]} (Methods)
    cohen_kappa(pairs), icc_a1(pairs)                                    → egyezési statisztikák
    outcome_paths / project_status / x009_findings / require_s08_gate    → fájlok és az X009 (S08 PASS előtt)
    cli_main(argv)                                                       → 'ma.py kettos …'

A tábla (a, b) lehet fájlút, a fájl bájtjai, ``{header, rows, row_uids?, decimal_mark?, delimiter?, format?}``
(a munkapad nyers cellái) vagy ``(header, rows)``. A számot a ``tableio`` értelmezi, táblánként a saját
tizedesjelével: ``12,3`` / ``12.3`` / ``12.30`` ugyanaz a szám → ``format_only`` (nem kell dönteni).

Adatvédelem (T10): a hibaüzenetek cellaértéket és kulcsértéket nem idéznek (csak mezőnevet és sorszámot).
"""
import collections
import datetime
import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata

from . import __version__
from . import tableio

SCHEMA = "szk.ma.compare-result/v1"
CONSENSUS_SCHEMA = "szk.ma.consensus/v1"
PROVENANCE_SCHEMA = "szk.ma.provenance/v1"

ROW_FIELD = "*"                     # sorszintű döntés (csak az egyik táblában szereplő sor)
KETTOS_DIR = "kettos"
DATA_DIR = "03_adatok"
DEFAULT_DIR = DATA_DIR + "/" + KETTOS_DIR
PRIVATE_DIR = "_privat/" + KETTOS_DIR
INBOX = "beerkezett"

STATUSES = ("equal", "format_only", "within_tolerance", "differs", "missing_a", "missing_b", "both_empty")
AGREE = ("equal", "format_only", "within_tolerance")
DISAGREE = ("differs", "missing_a", "missing_b")
KINDS = ("value", "missing_a", "missing_b", "format_only", "parse", "category")
AUTO_KINDS = ("format_only",)       # döntés nélkül: a konszenzus az A szövegét veszi át
CHOICES = ("a", "b", "other")

# a kategóriás mezők (κ); a rob-szerű oszlopokat a tableio.is_rob_column is felismeri
CATEGORICAL_FIELDS = ("rob", "estimated", "control_split", "subgroup")
YES_NO_FIELDS = ("estimated", "control_split")
# alapból kihagyott oszlopok: a sor-azonosító és a kinyerők saját megjegyzései
DEFAULT_IGNORE = ("row_uid", "megjegyzes", "megjegyzesek", "notes", "note", "comment", "comments", "kommentar")
KEY_CANDIDATES = (("study_id", "arm"), ("study_id", "kar"), ("study_id",), ("study",), ("row_uid",))
# a rob-szintek sorrendje (szomszédos kategória súgóhoz)
_ORDINAL = {"low": 0, "some concerns": 1, "moderate": 1, "unclear": 1, "high": 2, "serious": 2, "critical": 3}
# karpárok (felcserélt karok súgója)
ARM_PARTNER = {"m1": "m2", "m2": "m1", "sd1": "sd2", "sd2": "sd1", "n1": "n2", "n2": "n1", "e1": "e2", "e2": "e1"}
_EVENT_PAIRS = (("e1", "n1"), ("e2", "n2"), ("x", "n"))
_SD_N = {"sd1": "n1", "sd2": "n2", "sd_diff": "n"}
# nevezetes mértékegység-szorzók (2% tűréssel): (szorzó, hu, en)
_UNIT_FACTORS = (
    (18.0, "glükóz: mg/dL ↔ mmol/L", "glucose: mg/dL ↔ mmol/L"),
    (38.67, "koleszterin: mg/dL ↔ mmol/L", "cholesterol: mg/dL ↔ mmol/L"),
    (88.4, "kreatinin: mg/dL ↔ µmol/L", "creatinine: mg/dL ↔ µmol/L"),
    (2.2046, "testtömeg: font (lb) ↔ kg", "body weight: lb ↔ kg"),
    (2.54, "hossz: hüvelyk (inch) ↔ cm", "length: inch ↔ cm"),
)
# a súgók kódjai (hint_code; a munkapad ezekhez ad kezdőbarát magyarázatot — ma_gui extraction_dual.js HINT_WHY):
# a munkapaddal közös szótár elöl, a motor további kódjai utána
HINT_CODES = ("x10", "se_sd_swap", "arms_swapped", "format_only", "parse", "value", "category", "missing",
              "x100", "x1000", "x1000000", "unit_factor", "thousands_decimal", "events_gt_n", "percent_for_count",
              "sign", "variance_sd", "digit_transposition", "digit_typo", "rounding")
MATERIAL_SHIFT_SE = 0.05            # „hatásos” eltérés: a becslés legalább 0.05 SE-vel mozdul (vagy ki/bekerül)
MAX_POOLED_RERUNS = 500             # az összesített becslés újraszámolásainak felső korlátja egy összevetésben
Z95 = 1.959963984540054

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_OUTCOME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_SEP = "␟"                     # kulcs ␟ mező (belső szótárkulcs)


class KettosError(ValueError):
    """A kettős kinyerés hibája (magyar üzenet, cellaérték nélkül)."""


def _t(hu, en):
    return collections.OrderedDict((("hu", hu), ("en", en)))


def _en_minus(text):
    from .plots import MINUS
    return text.replace("-", MINUS)


def _num_i18n(text):
    """Számszöveg {hu, en}: a magyar kötőjel-mínusszal, az angol U+2212-vel (terv 4.0)."""
    return _t(text, _en_minus(text))


def _now_ts(now=None):
    if isinstance(now, str):
        return now
    d = now or datetime.datetime.now(datetime.timezone.utc)
    if d.tzinfo is not None:
        d = d.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return d.replace(microsecond=0).isoformat() + "Z"


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _num(v):
    """JSON-szám vagy None (NaN/Infinity soha)."""
    return float(v) if _finite(v) else None


def _norm_text(s):
    """Szövegösszevetéshez: NFC, kisbetű, összevont szóközök."""
    s = unicodedata.normalize("NFC", str(s or ""))
    return re.sub(r"\s+", " ", s).strip().casefold()


def _norm_name(s):
    return re.sub(r"[\s\-_]+", "", tableio._strip_accents(str(s).strip().lower()))


def _same_num(a, b):
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _pct_text(p):
    return None if p is None else _num_i18n("%.1f%%" % p)


# ------------------------------------------------------------------ táblák
class _Table(object):
    """Egy kinyerő táblája: nyers cellák (a row_uid oszloppal együtt, ha van), a tableio értelmezése, sor-uid-ok."""

    def __init__(self, side, header, rows, row_uids=None, decimal_mark=None, delimiter=None, fmt=None,
                 source=None, sha256=None):
        if not isinstance(header, (list, tuple)) or not any(str(h).strip() for h in header):
            raise KettosError("A(z) %s tábla fejléce üres (nincs oszlopnév)." % side)
        self.side = side
        self.header = ["" if h is None else str(h) for h in header]
        rows = [list(r.get("cells") or []) if isinstance(r, dict) else list(r or []) for r in rows or []]
        uids_in = list(row_uids) if isinstance(row_uids, (list, tuple)) and len(row_uids) == len(rows) else None
        keep = [i for i, r in enumerate(rows) if any(str(c if c is not None else "").strip() for c in r)]
        self.rows = [["" if c is None else str(c) for c in rows[i]] for i in keep]
        given = [uids_in[i] for i in keep] if uids_in is not None else None
        self.decimal_mark_given = decimal_mark if decimal_mark in (".", ",") else None
        self.delimiter = delimiter if delimiter in (",", ";", "\t") else None
        self.fmt = dict(fmt) if isinstance(fmt, dict) else None
        self.source = source
        self.sha256 = sha256
        try:
            self.prow, self.pmeta = tableio.parse_table(self.header, self.rows, self.decimal_mark_given, self.delimiter)
        except ValueError as exc:
            raise KettosError("A(z) %s tábla nem értelmezhető: %s" % (side, exc)) from None
        self.decimal_mark = self.pmeta.get("decimal_mark")
        self.columns = list(self.pmeta["columns"])
        mapping = self.pmeta["mapping"]
        self.fields = [mapping[c] for c in self.columns]
        self.col_of = {}
        for i, f in enumerate(self.fields):
            self.col_of.setdefault(f, i)
        self.uid_index = next((i for i, h in enumerate(self.header) if h.strip().lower() == tableio.UID_COLUMN), None)
        self.uids = self._uids(given)
        self.parse_err = {}
        for pe in self.pmeta.get("parse_errors") or []:
            i = pe.get("line", 0) - 2
            if pe.get("column") is None:
                for f in self.fields:
                    self.parse_err.setdefault((i, f), "ragged")
            else:
                self.parse_err[(i, mapping.get(pe["column"], pe["column"]))] = pe.get("note") or "nem szám"
        self.ambiguous = {}
        for am in self.pmeta.get("ambiguous") or []:
            self.ambiguous[(am.get("line", 0) - 2, mapping.get(am.get("column"), am.get("column")))] = am.get("note")

    def _uids(self, given):
        if given is None:
            return tableio.row_uids(self.prow, self.pmeta)
        reserved = {str(u) for u in given if isinstance(u, str) and tableio.UID_RE.match(u)}
        taken, out = set(), []
        for i, u in enumerate(given):
            u = u.strip() if isinstance(u, str) else ""
            if not tableio.UID_RE.match(u) or u in taken:
                u = tableio.row_uid_for(self.prow[i].get("study") if i < len(self.prow) else None, i, taken | reserved)
            taken.add(u)
            out.append(u)
        return out

    def __len__(self):
        return len(self.rows)

    def has(self, field):
        return field in self.col_of or (field == tableio.UID_COLUMN)

    def raw(self, i, field):
        """A cella nyers szövege (None: nincs ilyen oszlop); a row_uid mező a sor tényleges uid-ja."""
        if field == tableio.UID_COLUMN:
            return self.uids[i]
        c = self.col_of.get(field)
        if c is None:
            return None
        r = self.rows[i]
        return r[c] if c < len(r) else ""

    def text(self, i, field):
        r = self.raw(i, field)
        return "" if r is None else r.strip()

    def value(self, i, field):
        return self.prow[i].get(field)

    def rename(self, old, new):
        """Nem kanonikus mező átnevezése (a két tábla azonos oszlopa más írásmóddal, pl. 'Kar' ↔ 'kar')."""
        self.fields = [new if f == old else f for f in self.fields]
        self.col_of[new] = self.col_of.pop(old)
        for r in self.prow:
            if old in r:
                r[new] = r.pop(old)
        for d in (self.parse_err, self.ambiguous):
            for (i, f) in [k for k in d if k[1] == old]:
                d[(i, new)] = d.pop((i, f))

    def column_name(self, field):
        c = self.col_of.get(field)
        return self.header[c].strip() if c is not None else None

    def info(self):
        return {"rows": len(self.rows), "path": self.source if isinstance(self.source, str) else None,
                "sha256": self.sha256, "decimal_mark": self.decimal_mark, "delimiter": self.delimiter}


def _load(t, side):
    if isinstance(t, _Table):
        return t
    raw = None
    source = None
    if isinstance(t, (str, os.PathLike)):
        source = os.fspath(t)
        try:
            with open(source, "rb") as fh:
                raw = fh.read()
        except FileNotFoundError:
            raise KettosError("A(z) %s tábla nem található: %s" % (side, source)) from None
    elif isinstance(t, (bytes, bytearray)):
        raw = bytes(t)
    if raw is not None:
        try:
            header, rows, fmt = tableio.read_raw(raw=raw)
        except ValueError as exc:
            raise KettosError("A(z) %s tábla nem olvasható: %s" % (side, exc)) from None
        if not header:
            raise KettosError("A(z) %s tábla üres." % side)
        return _Table(side, header, rows, None, fmt.get("decimal_mark"), fmt.get("delimiter"), fmt, source,
                      fmt.get("sha256"))
    if isinstance(t, dict):
        rows = t.get("rows") or []
        uids = t.get("row_uids")
        if uids is None and rows and all(isinstance(r, dict) for r in rows):
            uids = [r.get("row_uid") for r in rows]
        fmt = t.get("format") if isinstance(t.get("format"), dict) else None
        return _Table(side, t.get("header") or [], rows, uids, t.get("decimal_mark"),
                      t.get("delimiter") or (fmt or {}).get("delimiter"), fmt, t.get("dataset") or t.get("path"),
                      t.get("sha256") or t.get("etag"))
    if isinstance(t, (list, tuple)) and len(t) == 2:
        return _Table(side, t[0], t[1])
    raise KettosError("A(z) %s tábla fájlút, bájtok, {header, rows} vagy (fejléc, sorok) lehet." % side)


def _align_fields(ta, tb):
    """A B nem kanonikus oszlopai az A azonos (kis/nagybetű-, ékezet-, szóköz- és aláhúzásjel-független) nevű
    oszlopának mezőnevét kapják, hogy a két tábla ugyanazon oszlopa egy mezőként legyen összevetve."""
    by_norm = {}
    for f in ta.fields:
        by_norm.setdefault(_norm_name(f), f)
    for f in list(tb.fields):
        if f in ta.col_of or f == tableio.UID_COLUMN:
            continue
        g = by_norm.get(_norm_name(f))
        if g is not None and g not in tb.col_of:
            tb.rename(f, g)


# ------------------------------------------------------------------ kulcs és párosítás
def _resolve_field(name, t):
    n = str(name).strip()
    if n == tableio.UID_COLUMN or n in t.col_of:
        return n
    try:
        k = tableio.resolve_column(n, t.pmeta, t.prow)
    except ValueError:
        return None
    return k if k in t.col_of else None


def _key_fields(key, ta, tb):
    if isinstance(key, str):
        key = [k for k in key.split(",") if k.strip()]
    out = []
    for name in key:
        fa, fb = _resolve_field(name, ta), _resolve_field(name, tb)
        if fa is None or fb is None or fa != fb:
            where = {(True, False): "az A", (False, True): "a B"}.get((fa is None, fb is None), "mindkét")
            raise KettosError("A kulcsoszlop (%s) hiányzik %s táblából. Az A oszlopai: %s; a B oszlopai: %s."
                              % (str(name).strip(), where, ", ".join(h.strip() for h in ta.header),
                                 ", ".join(h.strip() for h in tb.header)))
        if fa not in out:
            out.append(fa)
    if not out:
        raise KettosError("Üres kulcs: adj meg legalább egy kulcsoszlopot (pl. study_id).")
    return out


_EMPTY_KEY = "\x00"


def _row_keys(t, key):
    """[(megjelenített kulcs, normalizált kulcs, ismétlődés-e)] soronként; az ismétlődő kulcs '#2', '#3' utótagot
    kap (előfordulási sorrendben párosul), az üres kulcs '#<sor>'-t (nem párosul). A normalizált kulcs kis/nagybetű-
    és szóközfüggetlen."""
    seen = collections.Counter()
    out = []
    for i in range(len(t)):
        parts = [t.text(i, f) for f in key]
        if not any(parts):
            out.append(("#%d" % (i + 1), "%s%s%d" % (_EMPTY_KEY, t.side, i), False))
            continue
        disp = "|".join(parts)
        norm = "|".join(_norm_text(p) for p in parts)
        seen[norm] += 1
        dup = seen[norm] > 1
        if dup:
            disp += "#%d" % seen[norm]
            norm += "%s#%d" % (_EMPTY_KEY, seen[norm])
        out.append((disp, norm, dup))
    return out


def _key_quality(t, key):
    keys = _row_keys(t, key)
    return sum(1 for _, n, _d in keys if n.startswith(_EMPTY_KEY)), sum(1 for _, _n, d in keys if d)


def _choose_key(ta, tb):
    present = []
    for cand in KEY_CANDIDATES:
        fs = [_resolve_field(f, ta) for f in cand]
        if all(f is not None and _resolve_field(f, tb) == f for f in fs) and fs not in present:
            present.append(fs)
    for c in present:
        if c == [tableio.UID_COLUMN] and (tableio.UID_COLUMN not in ta.col_of or tableio.UID_COLUMN not in tb.col_of):
            continue        # származtatott uid-ok: a két kinyerőnél nem egyeznek
        if _key_quality(ta, c) == (0, 0) and _key_quality(tb, c) == (0, 0):
            return c, "auto"
    for c in present:
        if c != [tableio.UID_COLUMN]:
            return c, "auto"
    raise KettosError("Nincs közös kulcsoszlop a két táblában (study_id, study vagy row_uid). Add meg a kulcsot "
                      "(pl. key=['study_id', 'arm']), és ellenőrizd, hogy mindkét táblában szerepel.")


# ------------------------------------------------------------------ mezők, típusok, tűrés
def _rob_level(v):
    """RoB-cella → szint ('low', 'some concerns', 'high', 'serious' …) vagy None (fel nem ismert)."""
    t = tableio._token(v)
    rt = tableio._ROB_SUFFIX.sub("", re.sub(r"[\s_\-]+", " ", t).strip())
    return next((k for k, grp in tableio._ROB_LEVEL.items() if t in grp or rt in grp), None)


def _numberish(text):
    t = tableio._clean(text)
    if not t:
        return False
    try:
        tableio.parse_number(t)
        return True
    except ValueError:
        return bool(tableio._grp_comma.match(t) or tableio._grp_dot.match(t))


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _na(text):
    t = (text or "").strip()
    return t == "" or t.lower() in tableio.NA_TOKENS


class _Fields(object):
    """Az összevetett mezők, típusuk (numeric | categorical | text) és a kategóriák kanonikus alakja."""

    def __init__(self, ta, tb, key, fields=None, ignore=None, categorical=None):
        ign = set(DEFAULT_IGNORE if ignore is None else [_norm_name(x) for x in ignore])
        ign.add(tableio.UID_COLUMN)
        forced = set()
        for c in categorical or ():
            f = _resolve_field(c, ta) or _resolve_field(c, tb) or str(c).strip()
            forced.add(f)
        want = None
        if fields:
            want = set()
            for c in fields:
                f = _resolve_field(c, ta) or _resolve_field(c, tb)
                if f is None:
                    raise KettosError("Ismeretlen oszlop az összevetendő mezők között: %s" % str(c).strip())
                want.add(f)
        order = []
        for f in ta.fields + tb.fields:
            if f in order or f in key or _norm_name(f) in ign or f == tableio.UID_COLUMN:
                continue
            if want is not None and f not in want:
                continue
            order.append(f)
        self.order = order
        self.only_a = [f for f in order if f in ta.col_of and f not in tb.col_of]
        self.only_b = [f for f in order if f in tb.col_of and f not in ta.col_of]
        self.type = {}
        for f in order:
            self.type[f] = self._type(f, ta, tb, forced)

    @staticmethod
    def _type(f, ta, tb, forced):
        if f in forced or f in CATEGORICAL_FIELDS or tableio.is_rob_column(f):
            return "categorical"
        if f in tableio.NUMERIC:
            return "numeric"
        texts, numeric = [], True
        for t in (ta, tb):
            if f not in t.col_of:
                continue
            for i in range(len(t)):
                s = t.text(i, f)
                if _na(s):
                    continue
                texts.append(s)
                if not (_is_number(t.value(i, f)) and _numberish(s)):
                    numeric = False
        if not texts:
            return "text"
        if numeric:
            return "numeric"
        distinct = {_norm_text(s) for s in texts}
        if len(distinct) <= 10 and len(distinct) <= max(2, len(texts) // 2):
            return "categorical"
        return "text"


def category(field, text):
    """Kategória-cella kanonikus alakja (κ és a csak-írásmód felismerése): rob-szint, igen/nem, egyébként a
    normalizált szöveg. Üres / NA → None."""
    if _na(text):
        return None
    if field == "rob" or tableio.is_rob_column(field):
        lv = _rob_level(text)
        if lv is not None:
            return lv
    if field in YES_NO_FIELDS:
        yn = tableio.yes_no(text)
        if yn:
            return yn
    return _norm_text(text)


def _tolerance(tolerance, ta):
    """tolerance: None | szám (abszolút, minden számmezőre) | {mező: szám | {abs, rel}} ('*' = alapérték)."""
    out = {}
    default = (0.0, 0.0)

    def one(v, where):
        if isinstance(v, dict):
            a, r = v.get("abs", 0) or 0, v.get("rel", 0) or 0
        else:
            a, r = v, 0
        if not (_finite(a) and _finite(r)) or a < 0 or r < 0:
            raise KettosError("Érvénytelen tűrés (%s): nemnegatív szám kell." % where)
        return float(a), float(r)

    if tolerance is None:
        return out, default
    if isinstance(tolerance, dict):
        for k, v in tolerance.items():
            if k == "*":
                default = one(v, "*")
                continue
            f = _resolve_field(k, ta) or str(k).strip()
            out[f] = one(v, str(k))
        return out, default
    return out, one(tolerance, "tolerance")


# ------------------------------------------------------------------ súgók
def _h(code, hu, en, kb=None):
    return {"code": code, "text": _t(hu, en), "kb": kb}


def _digits(text):
    """Számszöveg → (előjel nélküli számjegyek, tizedes-pozíció a végéről) az írásmódtól függetlenül."""
    t = tableio._clean(text).lstrip("+-")
    if re.search(r"[eE]", t):
        return None
    m = re.match(r"^(\d*)(?:[.,](\d+))?$", t.replace(",", ".") if t.count(",") + t.count(".") <= 1 else "")
    if not m:
        return None
    return (m.group(1) or "") + (m.group(2) or ""), len(m.group(2) or "")


def _numeric_hints(f, va, vb, sa, sb, ta, ia, tb, ib, amb):
    hints = []
    row_a = ta.prow[ia] if ia is not None else {}
    row_b = tb.prow[ib] if ib is not None else {}

    def val(row, k):
        v = row.get(k)
        return float(v) if _is_number(v) else None

    # eseményszám > létszám (V006)
    for e, n in _EVENT_PAIRS:
        if f not in (e, n):
            continue
        for side, row in (("A", row_a), ("B", row_b)):
            ev, nv = val(row, e), val(row, n)
            if ev is not None and nv is not None and ev > nv:
                hints.append(_h("events_gt_n", "A(z) %s táblában az eseményszám nagyobb, mint a létszám (%s > %s): "
                                "elírás vagy felcserélt oszlop? (V006)" % (side, e, n),
                                "In table %s the event count exceeds the sample size (%s > %s): typo or swapped "
                                "columns? (V006)" % (side, e, n), "V006"))
                break
    # felcserélt karok
    p = ARM_PARTNER.get(f)
    if p is not None:
        pa, pb = val(row_a, p), val(row_b, p)
        if pa is not None and pb is not None and _same_num(va, pb) and _same_num(pa, vb) and not _same_num(va, vb):
            hints.append(_h("arms_swapped", "Felcserélt karok? Az A %s értéke a B %s értéke, és fordítva (kezelt ↔ "
                            "kontroll): a hatás iránya megfordul." % (f, p),
                            "Swapped arms? A's %s equals B's %s and vice versa (treatment ↔ control): the direction "
                            "of the effect flips." % (f, p)))
    # SE/SD csere (V011) és variancia
    if f in _SD_N and va > 0 and vb > 0:
        nk = _SD_N[f]
        n = val(row_a, nk) or val(row_b, nk)
        lo, hi = sorted((va, vb))
        if n and n >= 2 and abs(hi / lo / math.sqrt(n) - 1.0) <= 0.05:
            small = "A" if va < vb else "B"
            hints.append(_h("se_sd_swap", "SE/SD csere? A nagyobb érték ≈ a kisebb·√n (n = %g): a(z) %s kinyerő "
                            "valószínűleg standard hibát (SE) írt be szórás (SD) helyett. Nézd meg a táblázat "
                            "lábjegyzetét (V011)." % (n, small),
                            "SE/SD swap? The larger value ≈ the smaller·√n (n = %g): extractor %s probably entered "
                            "the standard error (SE) instead of the SD. Check the table footnote (V011)." % (n, small),
                            "V011"))
        elif hi > 1 and abs(hi / (lo * lo) - 1.0) <= 0.02:
            hints.append(_h("variance_sd", "Variancia ↔ SD? Az egyik érték ≈ a másik négyzete: az egyik kinyerő "
                            "varianciát írt be szórás helyett.",
                            "Variance ↔ SD? One value ≈ the square of the other: one extractor entered the variance "
                            "instead of the SD."))
    # százalék a darabszám helyén
    for e, n in _EVENT_PAIRS:
        if f != e:
            continue
        nv = val(row_a, n) or val(row_b, n)
        if nv and nv > 0:
            for cnt, other in ((va, vb), (vb, va)):
                if abs(cnt / nv * 100.0 - other) <= 0.6 and not _same_num(cnt, other):
                    hints.append(_h("percent_for_count", "Százalék a darabszám helyén? Az egyik érték a másik "
                                    "eseményszám százaléka (%s/%g ≈ %.1f%%)." % (e, nv, cnt / nv * 100.0),
                                    "Percentage instead of a count? One value equals the other event count as a "
                                    "percentage (%s/%g ≈ %.1f%%)." % (e, nv, cnt / nv * 100.0)))
                    break
    # ellentétes előjel
    if va != 0 and _same_num(va, -vb):
        hints.append(_h("sign", "Ellentétes előjel: a különbség iránya (kezelt − kontroll vagy fordítva) vagy a "
                        "változás iránya (előtte − utána) eltér.",
                        "Opposite sign: the direction of the difference (treatment − control or vice versa) or of "
                        "the change (before − after) differs."))
    # ezres tagolás / tizedesjel (V023), 10^k-szoros és nevezetes mértékegység-szorzó (V012)
    if va != 0 and vb != 0 and (va > 0) == (vb > 0):
        lo, hi = sorted((abs(va), abs(vb)))
        ratio = hi / lo
        if amb or ((tableio._grp_comma.match(tableio._clean(sa)) or tableio._grp_dot.match(tableio._clean(sa)) or
                    tableio._grp_comma.match(tableio._clean(sb)) or tableio._grp_dot.match(tableio._clean(sb)))
                   and abs(ratio - 1000.0) <= 1000.0 * 0.001):
            hints.append(_h("thousands_decimal", "Ezres tagolás vagy tizedesjel? Az egyik cella (pl. '1,204') ezres "
                            "tagolásként és tizedestörtként is olvasható, és a két tábla másként értelmezte (V023). "
                            "Írd tagolás nélkül (1204), vagy a fájl tizedesjelével.",
                            "Thousands separator or decimal mark? One cell (e.g. '1,204') reads both as a grouped "
                            "integer and as a decimal, and the two tables read it differently (V023). Write it "
                            "without grouping (1204) or with the file's decimal mark.", "V023"))
        for k in (1, 2, 3, 6):
            if abs(ratio / 10.0 ** k - 1.0) <= 0.02:
                hints.append(_h("x%d" % 10 ** k,
                                "%d× eltérés: tizedesjel-hiba (pl. 0.42 ↔ 4.2) vagy mértékegység (pl. mm ↔ cm, "
                                "mg ↔ g)? (V012 jellegű)" % 10 ** k,
                                "%d× difference: misplaced decimal point (e.g. 0.42 ↔ 4.2) or unit (e.g. mm ↔ cm, "
                                "mg ↔ g)? (V012-like)" % 10 ** k, "V012"))
                break
        for fac, hu, en in _UNIT_FACTORS:
            if abs(ratio / fac - 1.0) <= 0.02:
                hints.append(_h("unit_factor", "≈%g× eltérés: mértékegység (%s)? (V012 jellegű)" % (fac, hu),
                                "≈%g× difference: unit (%s)? (V012-like)" % (fac, en), "V012"))
                break
    # számjegyhibák
    da, db = _digits(sa), _digits(sb)
    if da and db and da[1] == db[1] and len(da[0]) == len(db[0]) and da[0] != db[0]:
        diff = [i for i, (x, y) in enumerate(zip(da[0], db[0])) if x != y]
        if len(diff) == 2 and diff[1] == diff[0] + 1 and da[0][diff[0]] == db[0][diff[1]] and \
                da[0][diff[1]] == db[0][diff[0]]:
            hints.append(_h("digit_transposition", "Szomszédos számjegyek felcserélése? (pl. 12.43 ↔ 12.34) — "
                            "gépelési hiba.", "Transposed adjacent digits? (e.g. 12.43 ↔ 12.34) — typing error."))
        elif len(diff) == 1:
            hints.append(_h("digit_typo", "Egyetlen számjegy tér el — gépelési hiba?",
                            "A single digit differs — typing error?"))
    # kerekítés
    if da and db and da[1] != db[1]:
        coarse, fine, d = (va, vb, da[1]) if da[1] < db[1] else (vb, va, db[1])
        if abs(fine - coarse) <= 0.5 * 10.0 ** (-d) * (1 + 1e-9) and not _same_num(fine, coarse):
            hints.append(_h("rounding", "Kerekítési eltérés: az egyik érték több tizedesjeggyel szerepel (pl. a "
                            "szövegben és a táblázatban eltérő pontossággal). Általában a pontosabbat érdemes "
                            "megtartani.",
                            "Rounding difference: one value has more decimals (e.g. text vs. table). Usually keep "
                            "the more precise one."))
    if not hints:
        hints.append(_h("value", "Eltérő érték: nézd meg a forrást (oldal, táblázat), és írd le, melyik a helyes és "
                        "miért.", "Different value: check the source (page, table) and record which is correct and "
                        "why."))
    return hints


def _hints_for(kind, f, ftype, va, vb, ca, cb, sa, sb, ta, ia, tb, ib, amb):
    if kind == "missing_a":
        return [_h("missing", "Az A kinyerőnél hiányzik az érték (a B-nél megvan): a forrásból pótold, vagy vedd át "
                   "a B értékét.", "Missing for extractor A (B has a value): fill it in from the source or take B's.")]
    if kind == "missing_b":
        return [_h("missing", "A B kinyerőnél hiányzik az érték (az A-nál megvan): a forrásból pótold, vagy vedd át "
                   "az A értékét.", "Missing for extractor B (A has a value): fill it in from the source or take A's.")]
    if kind == "format_only":
        if ftype == "numeric":
            return [_h("format_only", "Csak írásmódban tér el (ugyanaz a szám, pl. tizedesvessző vagy ezres tagolás; "
                       "V023): nem kell dönteni, a konszenzus az A szövegét veszi át.",
                       "Formatting only (same number, e.g. decimal comma or grouping; V023): no decision needed, the "
                       "consensus keeps A's text.", "V023")]
        return [_h("format_only", "Ugyanaz a kategória, csak az írásmód más (pl. 'High' ↔ 'high risk of bias'): nem "
                   "kell dönteni, a konszenzus az A szövegét veszi át.",
                   "Same category, different spelling (e.g. 'High' ↔ 'high risk of bias'): no decision needed, the "
                   "consensus keeps A's text.")]
    if kind == "parse":
        return [_h("parse", "Az egyik cella nem értelmezhető számként (pl. megjegyzés vagy mértékegység a szám "
                   "mellett): a helyes számot a forrásból írd be, a megjegyzést külön oszlopba (V003).",
                   "One cell cannot be read as a number (e.g. a note or unit next to it): enter the correct number "
                   "from the source and move the note to a separate column (V003).", "V003")]
    if kind == "category":
        oa, ob = _ORDINAL.get(ca), _ORDINAL.get(cb)
        if oa is not None and ob is not None and abs(oa - ob) == 1:
            return [_h("category", "Szomszédos kategóriák (határeset): vessétek össze az indoklást és a "
                       "doménítéleteket, majd döntsetek.",
                       "Adjacent categories (borderline): compare the rationale and the domain judgements, then "
                       "decide.")]
        return [_h("category", "Eltérő kategória: egyeztessétek az indoklást a forrás alapján.",
                   "Different category: reconcile the rationale against the source.")]
    if ftype == "numeric" and _is_number(va) and _is_number(vb):
        return _numeric_hints(f, float(va), float(vb), sa, sb, ta, ia, tb, ib, amb)
    return [_h("value", "Eltérő érték: nézd meg a forrást (oldal, táblázat), és írd le, melyik a helyes és miért.",
               "Different value: check the source (page, table) and record which is correct and why.")]


# ------------------------------------------------------------------ szövegátvitel az A formátumába
def _target_mark(ta):
    if ta.decimal_mark in (".", ","):
        return ta.decimal_mark
    return "," if ta.delimiter == ";" else "."


def _reads_as(text, field, mark, delim):
    try:
        if field in tableio.NUMERIC:
            v, _ = tableio._parse_numeric_cell(text, field, mark, delim or ",")
        else:
            v = tableio.parse_number(text)
    except ValueError:
        return None
    return float(v) if _is_number(v) else None


def to_a_format(text, value, field, ta):
    """A B (vagy kézi) szám szövege az A tábla írásmódjával (tizedesjel, ezres tagolás nélkül) úgy, hogy az A
    formátumában visszaolvasva ugyanazt a számot adja. Nem szám (vagy nem visszaalakítható) szöveg változatlan."""
    s = "" if text is None else str(text).strip()
    if not s or not _is_number(value) or not _numberish(s):
        return s
    mark, delim = _target_mark(ta), ta.delimiter
    back = _reads_as(s, field, mark, delim)
    # idegen jel: a másik tizedesjel vagy ezres tagolás (pl. '4.2' egy tizedesvesszős táblában)
    foreign = ("," in s) if mark == "." else ("." in s and not re.search(r"[eE]", s))
    if back is not None and _same_num(back, value) and not foreign:
        return s
    t = tableio._clean(s)
    cand = None
    for dec in (".", ","):
        other = "," if dec == "." else "."
        c = t.replace(other, "").replace(dec, ".")
        try:
            if _same_num(float(c), value):
                cand = c
                break
        except ValueError:
            continue
    if cand is None:
        cand = "%.15g" % value
    out = cand.replace(".", ",") if mark == "," else cand
    back = _reads_as(out, field, mark, delim)
    if back is None or not _same_num(back, value):
        out = ("%.15g" % value).replace(".", ",") if mark == "," else "%.15g" % value
    return out


# ------------------------------------------------------------------ κ és ICC
def cohen_kappa(pairs, level=0.95):
    """Cohen-féle κ névleges kategóriákra; aszimptotikus SE (Fleiss, Cohen & Everitt 1969) és normál CI.
    pairs: [(a, b)] → {n, categories, table, po, pe, kappa, se, ci, level} (κ None: n = 0 vagy pe = 1)."""
    n = len(pairs)
    cats = sorted({a for a, _ in pairs} | {b for _, b in pairs})
    idx = {c: i for i, c in enumerate(cats)}
    table = [[0] * len(cats) for _ in cats]
    for a, b in pairs:
        table[idx[a]][idx[b]] += 1
    out = {"n": n, "categories": cats, "table": table, "po": None, "pe": None, "kappa": None, "se": None,
           "ci": None, "level": level}
    if n == 0:
        return out
    p = [[c / float(n) for c in row] for row in table]
    row = [sum(r) for r in p]
    col = [sum(p[i][j] for i in range(len(cats))) for j in range(len(cats))]
    po = sum(p[i][i] for i in range(len(cats)))
    pe = sum(row[i] * col[i] for i in range(len(cats)))
    out.update(po=po, pe=pe)
    if pe >= 1.0 - 1e-12:
        return out
    k = (po - pe) / (1.0 - pe)
    a_term = sum(p[i][i] * (1.0 - (row[i] + col[i]) * (1.0 - k)) ** 2 for i in range(len(cats)))
    b_term = (1.0 - k) ** 2 * sum(p[i][j] * (col[i] + row[j]) ** 2 for i in range(len(cats))
                                  for j in range(len(cats)) if i != j)
    c_term = (k - pe * (1.0 - k)) ** 2
    se = math.sqrt(max((a_term + b_term - c_term) / (n * (1.0 - pe) ** 2), 0.0))
    z = _z(level)
    out.update(kappa=k, se=se, ci=[max(-1.0, k - z * se), min(1.0, k + z * se)])
    return out


def _z(level):
    if abs(level - 0.95) < 1e-12:
        return Z95
    from .distributions import norm_ppf
    return norm_ppf(0.5 + level / 2.0)


def _f_ppf(p, d1, d2):
    """Az F-eloszlás p-kvantilise (bisectio a distributions.f_cdf-en; nem egész szabadságfokkal is)."""
    from .distributions import f_cdf
    lo, hi = 0.0, 1.0
    while f_cdf(hi, d1, d2) < p:
        hi *= 2.0
        if hi > 1e12:
            return math.inf
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if f_cdf(mid, d1, d2) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-13 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def icc_a1(pairs, level=0.95):
    """ICC(A,1): kétutas véletlen modell, abszolút egyezés, egyetlen mérés (McGraw & Wong 1996; Shrout & Fleiss
    1979 ICC(2,1)), két kinyerővel; CI a Shrout–Fleiss (Satterthwaite) képlettel, mint a psych::ICC.
    pairs: [(a, b)] számpárok → {n, icc, ci, ms_rows, ms_raters, ms_error, level} (icc None, ha n < 2 vagy nincs
    szórás; CI None, ha n < 3 vagy a hiba-négyzetösszeg 0 — ilyenkor az egyezés tökéletes, icc = 1)."""
    pairs = [(float(a), float(b)) for a, b in pairs if _is_number(a) and _is_number(b)]
    n, k = len(pairs), 2
    out = {"n": n, "icc": None, "ci": None, "ms_rows": None, "ms_raters": None, "ms_error": None, "level": level}
    if n < 2:
        return out
    grand = sum(a + b for a, b in pairs) / (n * k)
    rows = [(a + b) / 2.0 for a, b in pairs]
    ca = sum(a for a, _ in pairs) / n
    cb = sum(b for _, b in pairs) / n
    ssr = k * sum((r - grand) ** 2 for r in rows)
    ssc = n * ((ca - grand) ** 2 + (cb - grand) ** 2)
    sst = sum((a - grand) ** 2 + (b - grand) ** 2 for a, b in pairs)
    sse = max(sst - ssr - ssc, 0.0)
    msr, msc, mse = ssr / (n - 1), ssc / (k - 1), sse / ((n - 1) * (k - 1))
    out.update(ms_rows=msr, ms_raters=msc, ms_error=mse)
    scale = max(1.0, abs(grand)) ** 2
    if sst <= 1e-24 * scale:
        return out
    if sse <= 1e-24 * scale and ssc <= 1e-24 * scale:
        out["icc"] = 1.0
        return out
    icc = (msr - mse) / (msr + (k - 1) * mse + k * (msc - mse) / n)
    out["icc"] = icc
    if n < 3 or mse <= 0:
        return out
    alpha = 1.0 - level
    fj = msc / mse
    vn = (k - 1) * (n - 1) * (k * icc * fj + n * (1 + (k - 1) * icc) - k * icc) ** 2
    vd = (n - 1) * k ** 2 * icc ** 2 * fj ** 2 + (n * (1 + (k - 1) * icc) - k * icc) ** 2
    if vd <= 0:
        return out
    v = vn / vd
    f_u = _f_ppf(1 - alpha / 2.0, n - 1, v)
    f_l = _f_ppf(1 - alpha / 2.0, v, n - 1)
    lower = n * (msr - f_u * mse) / (f_u * (k * msc + (k * n - k - n) * mse) + n * msr)
    upper = n * (f_l * msr - mse) / (k * msc + (k * n - k - n) * mse + n * f_l * msr)
    out["ci"] = [max(-1.0, lower), min(1.0, upper)]
    return out


def _kappa_label(k):
    """Landis & Koch (1977) értelmezése."""
    if k is None:
        return None
    if k < 0:
        return _t("gyenge (a véletlennél rosszabb)", "poor")
    for lim, hu, en in ((0.20, "csekély", "slight"), (0.40, "elfogadható", "fair"), (0.60, "közepes", "moderate"),
                        (0.80, "jelentős", "substantial")):
        if k <= lim + 1e-12:
            return _t(hu, en)
    return _t("majdnem tökéletes", "almost perfect")


def _icc_label(v):
    """Koo & Li (2016) értelmezése."""
    if v is None:
        return None
    for lim, hu, en in ((0.5, "gyenge", "poor"), (0.75, "közepes", "moderate"), (0.9, "jó", "good")):
        if v < lim:
            return _t(hu, en)
    return _t("kiváló", "excellent")


def _triple_text(est, ci):
    from .plots import fmt_triple
    if ci is None:
        return _num_i18n("%.2f" % est)
    return _num_i18n(fmt_triple(est, ci[0], ci[1]))


def _ci_text(name, est, ci):
    from .plots import fmt_triple
    if est is None:
        return None
    if ci is None:
        return _num_i18n("%s = %s" % (name, "%.2f" % est))
    return _num_i18n("%s = %s" % (name, fmt_triple(est, ci[0], ci[1])))


# ------------------------------------------------------------------ hatás (impact)
def _measure_fields(measure, opts):
    from . import effect_sizes as E
    if measure in E.CONTINUOUS:
        return set(E.REQUIRED_COLUMNS[measure])
    if measure in E.PAIRED:
        s = {"n"}
        for grp in E.PAIRED_INPUTS.values():
            for cols in grp:
                s.update(cols)
        return s
    if measure in E.BINARY or measure in E.PROPORTION or measure in E.CORRELATION:
        return set(E.REQUIRED_COLUMNS[measure])
    s = {"yi", "vi", "sei", "n"}
    if opts.get("gen_smd_vtype"):
        s.update(("n1", "n2"))
    return s


class _Impact(object):
    """A hatás újraszámolása a motor útján: a vizsgálat hatásmérete (validálás + effect_sizes, mint a pipeline-ban)
    és — spec mellett — az összesített becslés (a pipeline 1–3. lépése: validálás, hatásméretek, elsődleges modell)."""

    def __init__(self, ta, measure=None, spec=None, options=None):
        from . import pipeline
        self.ta = ta
        self.spec = spec
        opts = {}
        self.exclude = self.include = None
        if spec is not None:
            from . import spec as S
            errs = S.validate_spec(spec)
            if errs:
                raise KettosError("Érvénytelen elemzési spec: %s" % "; ".join(errs))
            opts = S.options_from_spec(spec)
            self.exclude, self.include = S.filters_from_spec(spec)
        opts.update({k: v for k, v in (options or {}).items() if v is not None})
        if measure:
            opts["measure"] = measure
        self.enabled = bool(opts.get("measure"))
        self.pooled_enabled = spec is not None or bool((options or {}).get("pooled"))
        opts.pop("pooled", None)
        full = dict(pipeline.DEFAULTS)
        full.update({k: v for k, v in opts.items() if v is not None})
        if self.enabled:
            full["measure"] = str(full["measure"]).upper()
            full["level"] = pipeline.normalize_level(full["level"])
            try:
                pipeline.check_options(full)
            except ValueError as exc:
                raise KettosError("Érvénytelen elemzési opció: %s" % exc) from None
            from . import effect_sizes as E
            if full["measure"] not in E.ALL_MEASURES:
                raise KettosError("Ismeretlen hatásmérték: %s (lehetséges: %s)" % (full["measure"],
                                                                                 ", ".join(E.ALL_MEASURES)))
        self.opts = full
        self.measure = full["measure"] if self.enabled else None
        self.fields = _measure_fields(self.measure, full) if self.enabled else set()
        self.filter_fields = set()
        for cond in (self.exclude or []) + (self.include or []):
            col = str(cond).partition("=")[0]
            try:
                self.filter_fields.add(tableio.resolve_column(col, ta.pmeta, ta.prow))
            except ValueError:
                pass
        self._base_rows = None
        self._base_pool = None
        self.reruns = 0
        self.skipped = 0

    def relevant(self, field):
        return self.enabled and (field in self.fields or (self.pooled_enabled and field in self.filter_fields))

    # -- a motor lépései
    def _es_kwargs(self):
        o = self.opts
        return dict(label_col=o["label_col"], smd_vtype=o["smd_vtype"], j_method=o["j_method"], cc=o["cc"],
                    cc_to=o["cc_to"], drop00=o["drop00"], md_vtype=o["md_vtype"], glass_vtype=o["glass_vtype"],
                    gen_smd_vtype=o["gen_smd_vtype"])

    def study(self, row, meta):
        """Egy sor hatásmérete úgy, ahogy a pipeline számolná: (yi, vi, ni, ok). A soron belüli 'error' szintű
        validálási tétel (pl. V004, V005, V006) kizárja, mint az elemzésben."""
        from . import effect_sizes as E, validate as V
        line = row.get("_line")
        m = dict(meta or {})
        for k in ("parse_errors", "ambiguous", "formula_like"):
            if k in m:
                m[k] = [x for x in m[k] or [] if x.get("line") == line]
        try:
            findings = V.validate([row], self.measure, m, self.opts)
        except Exception:                              # noqa: BLE001 — a hatás csak tájékoztató
            findings = []
        blocking = V.blocking_rows(findings)
        es = E.compute([row], self.measure, skip_rows=blocking, **self._es_kwargs())
        if len(es) == 1:
            return es.yi[0], es.vi[0], es.ni[0], None
        why = es.excluded[0][1] if es.excluded else "nem számolható"
        if blocking:
            codes = sorted({f["code"] for f in findings if f.get("severity") == "error"})
            why = "validálási hiba (%s)" % ", ".join(codes)
        return None, None, None, why

    def pool(self, rows, meta):
        """Az elsődleges összesített becslés (a pipeline.run 1–3. lépése, ugyanazokkal a hívásokkal):
        {estimate, ci_lower, ci_upper, se, k, display, model} vagy None (k = 0)."""
        from . import effect_sizes as E, models as M, pipeline, validate as V
        o = self.opts
        rows = tableio.apply_filters(rows, self.exclude, self.include, meta=meta)
        findings = V.validate(rows, self.measure, meta, o)
        blocking = V.blocking_rows(findings)
        es = E.compute(rows, self.measure, skip_rows=blocking, **self._es_kwargs())
        k = len(es)
        if k == 0:
            return {"k": 0, "estimate": None, "ci_lower": None, "ci_upper": None, "se": None, "display": None,
                    "model": None}
        hc = o["h_centre"]
        req = o["model"]
        # csak az elsődleges modell (a pipeline.run ugyanezekkel az argumentumokkal illeszti; a többi modell a
        # becslést nem befolyásolja)
        try:
            if req == "random" and k >= 2:
                primary = M.meta_analysis(es.yi, es.vi, "random", o["tau2"], o["ci"] or "hksj", o["level"], o["pi"],
                                          es.labels, h_centre=hc)
                model = "random"
            elif req == "ivhet" and k >= 2:
                primary = M.meta_analysis(es.yi, es.vi, "ivhet", o["tau2"], level=o["level"], labels=es.labels,
                                          h_centre=hc)
                model = "ivhet"
            else:
                fixed_ci = (o["ci"] or "z") if req == "fixed" else "z"
                primary = M.meta_analysis(es.yi, es.vi, "fixed", ci_method=fixed_ci, level=o["level"],
                                          labels=es.labels, h_centre=hc)
                model = "fixed"
        except (M.ModelError, ArithmeticError, ValueError):
            return None
        nh = E.harmonic_mean(es.ni) if self.measure == "PFT" else None
        try:
            disp = pipeline._display(self.measure, primary.estimate, primary.ci_lower, primary.ci_upper, nh,
                                     o["pft_backtransform"], primary.se)
        except (ArithmeticError, ValueError, E.EffectSizeError):
            disp = None
        return {"k": k, "estimate": _num(primary.estimate), "ci_lower": _num(primary.ci_lower),
                "ci_upper": _num(primary.ci_upper), "se": _num(primary.se), "model": model,
                "display": [_num(x) for x in disp] if disp else None}

    def base_pool(self):
        if self._base_pool is None:
            self._base_pool = self.pool(self.ta.prow, self.ta.pmeta) or {}
        return self._base_pool or None

    # -- szövegek
    def display(self, yi, ni):
        from . import effect_sizes as E
        if yi is None:
            return None
        try:
            if self.measure == "PFT":
                return _num(E.back_transform(self.measure, yi, n_harmonic=ni))
            return _num(E.back_transform(self.measure, yi))
        except (ArithmeticError, ValueError, E.EffectSizeError):
            return None

    def label(self):
        return self.measure

    def study_text(self, da, db, why_a, why_b):
        """'SMD 0.31 → 0.03'; hiányzó cellánál '—', nem számolható hatásméretnél 'nem számolható'. A why_* None:
        a cella üres (a „—” jelzi)."""
        from .plots import _fmt, decimals
        nd = decimals([v for v in (da, db) if v is not None])

        def one(v, why, lang):
            if v is not None:
                return _fmt(v, nd)
            if why:
                return "nem számolható" if lang == "hu" else "not computable"
            return "—"
        hu = "%s %s → %s" % (self.label(), one(da, why_a, "hu"), one(db, why_b, "hu"))
        en = "%s %s → %s" % (self.label(), one(da, why_a, "en"), one(db, why_b, "en"))
        return _t(hu, _en_minus(en))

    def pooled_text(self, pa, pb):
        from .plots import fmt_triple, MINUS

        def one(p, minus):
            if not p or p.get("display") is None or None in p["display"]:
                return "—"
            return fmt_triple(p["display"][0], p["display"][1], p["display"][2], minus)
        ks = ""
        if pa and pb and pa.get("k") != pb.get("k"):
            ks = " (k = %s → %s)" % (pa.get("k"), pb.get("k"))
        return _t("összesített %s: %s → %s%s" % (self.label(), one(pa, "-"), one(pb, "-"), ks),
                  "pooled %s: %s → %s%s" % (self.label(), one(pa, MINUS), one(pb, MINUS), ks))


def _variant(ta, i, field, text, column_name=None):
    """Az A tábla nyers cellái, az i. sor field cellájában a megadott szöveggel (új oszlop, ha az A-ban nincs)."""
    header = list(ta.header)
    rows = [list(r) for r in ta.rows]
    c = ta.col_of.get(field)
    if c is None:
        header.append(column_name or field)
        c = len(header) - 1
        for r in rows:
            r.extend([""] * (c + 1 - len(r)))
    r = rows[i]
    if c >= len(r):
        r.extend([""] * (c + 1 - len(r)))
    r[c] = text
    return header, rows


def variant_table(compare_result_or_tables, key, field, side_value=None):
    """Teszt- és felület-segéd: az A tábla, a (kulcs, mező) cellában a B értékével (az A formátumára hozva) —
    pontosan az a tábla, amelyből az impact 'b' oldala számol. → {header, rows, decimal_mark, delimiter}."""
    res = compare_result_or_tables
    tables = getattr(res, "tables", None)
    if tables is None:
        raise KettosError("A variant_table a compare() közvetlen eredményét várja.")
    ta, tb, pairs = tables["a"], tables["b"], tables["pairs"]
    ia, ib = pairs[key]
    text = side_value if side_value is not None else to_a_format(tb.text(ib, field), tb.value(ib, field), field, ta)
    header, rows = _variant(ta, ia, field, text, tb.column_name(field))
    return {"header": header, "rows": rows, "decimal_mark": ta.decimal_mark_given, "delimiter": ta.delimiter}


def _impact(imp, ta, ia, tb, ib, field):
    """A (kulcs, mező) eltérés hatása: az A sora az A értékével, illetve a B értékével (a többi cella változatlan)."""
    if not imp.relevant(field):
        return None
    btext = to_a_format(tb.text(ib, field), tb.value(ib, field), field, ta) if ib is not None else ""
    header, rows = _variant(ta, ia, field, btext, tb.column_name(field) if ib is not None else None)
    try:
        vrows, vmeta = tableio.parse_table(header, rows, ta.decimal_mark_given, ta.delimiter)
    except ValueError:
        return None
    out = collections.OrderedDict()
    out["measure"] = imp.measure
    yi_a = vi_a = ni_a = why_a = yi_b = vi_b = ni_b = why_b = None
    if field in imp.fields:
        yi_a, vi_a, ni_a, why_a = imp.study(ta.prow[ia], ta.pmeta)
        yi_b, vi_b, ni_b, why_b = imp.study(vrows[ia], vmeta)
    out["yi_a"] = _num(yi_a)
    out["yi_b"] = _num(yi_b)
    out["vi_a"] = _num(vi_a)
    out["vi_b"] = _num(vi_b)
    out["delta"] = _num(yi_b - yi_a) if yi_a is not None and yi_b is not None else None
    out["display_a"] = imp.display(yi_a, ni_a)
    out["display_b"] = imp.display(yi_b, ni_b)
    out["reason_a"] = why_a
    out["reason_b"] = why_b
    shift = None
    if out["delta"] is not None and vi_a and vi_a > 0:
        shift = abs(out["delta"]) / math.sqrt(vi_a)
    changes = (yi_a is None) != (yi_b is None) and field in imp.fields
    out["shift_se"] = _num(shift)
    out["changes_inclusion"] = bool(changes)
    empty_a = _na(ta.text(ia, field))
    empty_b = ib is None or _na(tb.text(ib, field))
    out["text"] = imp.study_text(out["display_a"], out["display_b"], None if empty_a else why_a,
                                 None if empty_b else why_b) if field in imp.fields else None
    rank = shift
    if imp.pooled_enabled:
        if imp.reruns >= MAX_POOLED_RERUNS:
            imp.skipped += 1
        else:
            imp.reruns += 1
            pa = imp.base_pool()
            pb = imp.pool(vrows, vmeta)
            out["pooled_a"] = pa
            out["pooled_b"] = pb
            if pa and pb and pa.get("estimate") is not None and pb.get("estimate") is not None:
                out["pooled_delta"] = _num(pb["estimate"] - pa["estimate"])
                out["pooled_shift_se"] = _num(abs(out["pooled_delta"]) / pa["se"]) if pa.get("se") else None
                rank = out["pooled_shift_se"] if out["pooled_shift_se"] is not None else rank
            if pa and pb and pa.get("k") != pb.get("k"):
                out["changes_inclusion"] = True
            out["pooled_text"] = imp.pooled_text(pa, pb)
            if out["text"] is None:
                out["text"] = out["pooled_text"]
    out["rank"] = _num(rank)
    out["material"] = bool(out["changes_inclusion"] or (rank is not None and rank >= MATERIAL_SHIFT_SE))
    if out["text"] is None:
        return None
    return out


def _row_impact(imp, ta, tb, ib):
    """Csak a B-ben szereplő sor hatása: az összesített becslés a sor nélkül (A) és vele (B, az A formátumában)."""
    if not (imp.enabled and imp.pooled_enabled) or imp.reruns >= MAX_POOLED_RERUNS:
        return None
    header = list(ta.header)
    rows = [list(r) for r in ta.rows]
    extra = []
    for f in tb.fields:
        if f not in ta.col_of and f != tableio.UID_COLUMN and f in imp.fields:
            extra.append(f)
            header.append(tb.column_name(f) or f)
    new = []
    for c, f in enumerate(ta.fields):
        if f == tableio.UID_COLUMN:
            new.append("")
        elif tb.has(f) and f in tb.col_of:
            new.append(to_a_format(tb.text(ib, f), tb.value(ib, f), f, ta))
        else:
            new.append("")
    for r in rows:
        r.extend([""] * len(extra))
    new.extend(to_a_format(tb.text(ib, f), tb.value(ib, f), f, ta) for f in extra)
    rows.append(new)
    try:
        vrows, vmeta = tableio.parse_table(header, rows, ta.decimal_mark_given, ta.delimiter)
    except ValueError:
        return None
    imp.reruns += 1
    pa, pb = imp.base_pool(), imp.pool(vrows, vmeta)
    yi, vi, ni, why = imp.study(vrows[-1], vmeta)
    out = collections.OrderedDict((("measure", imp.measure), ("yi_a", None), ("yi_b", _num(yi)),
                                   ("display_b", imp.display(yi, ni)), ("reason_b", why),
                                   ("pooled_a", pa), ("pooled_b", pb)))
    if pa and pb and pa.get("estimate") is not None and pb.get("estimate") is not None:
        out["pooled_delta"] = _num(pb["estimate"] - pa["estimate"])
    out["text"] = imp.study_text(None, out["display_b"], None, why)
    out["pooled_text"] = imp.pooled_text(pa, pb)
    return out


def _row_impact_a(imp, ta, ia):
    """Csak az A-ban szereplő sor hatása: az összesített becslés vele (A) és nélküle (ha B szerint kimarad)."""
    if not (imp.enabled and imp.pooled_enabled) or imp.reruns >= MAX_POOLED_RERUNS:
        return None
    rows = [r for j, r in enumerate(ta.rows) if j != ia]
    try:
        vrows, vmeta = tableio.parse_table(ta.header, rows, ta.decimal_mark_given, ta.delimiter)
    except ValueError:
        return None
    imp.reruns += 1
    pa, pb = imp.base_pool(), imp.pool(vrows, vmeta)
    yi, vi, ni, why = imp.study(ta.prow[ia], ta.pmeta)
    out = collections.OrderedDict((("measure", imp.measure), ("yi_a", _num(yi)), ("yi_b", None),
                                   ("display_a", imp.display(yi, ni)), ("reason_a", why),
                                   ("pooled_a", pa), ("pooled_b", pb)))
    if pa and pb and pa.get("estimate") is not None and pb.get("estimate") is not None:
        out["pooled_delta"] = _num(pb["estimate"] - pa["estimate"])
    out["text"] = imp.study_text(out["display_a"], None, why, None)
    out["pooled_text"] = imp.pooled_text(pa, pb)
    return out


# ------------------------------------------------------------------ összevetés
class CompareResult(dict):
    """A compare() eredménye (szk.ma.compare-result/v1 dict); a ``tables`` attribútum a beolvasott táblákat tartja
    (a reconcile() így a táblák újraküldése nélkül is dolgozhat). JSON-ba írva sima dict."""
    tables = None


def _cell_status(ftype, sa, sb, va, vb, err_a, err_b, ca, cb, tol):
    """(státusz, eltérés-fajta vagy None)."""
    ea, eb = _na(sa), _na(sb)
    if ea and eb:
        return "both_empty", None
    if ea:
        return "missing_a", "missing_a"
    if eb:
        return "missing_b", "missing_b"
    if sa == sb:
        return "equal", None
    if ftype == "numeric":
        if err_a or err_b or not (_is_number(va) and _is_number(vb)):
            return "differs", "parse"
        va, vb = float(va), float(vb)
        if _same_num(va, vb):
            if _numberish(sa) and _numberish(sb):
                return "format_only", "format_only"
            return "differs", "value"
        a_tol, r_tol = tol
        if abs(va - vb) <= max(a_tol, r_tol * max(abs(va), abs(vb))) + 1e-12 * max(1.0, abs(va), abs(vb)) and \
                (a_tol or r_tol):
            return "within_tolerance", None
        return "differs", "value"
    if ftype == "categorical":
        if ca == cb:
            return "format_only", "format_only"
        return "differs", "category"
    if _norm_text(sa) == _norm_text(sb):
        return "format_only", "format_only"
    return "differs", "value"


def compare(a, b, key=None, tolerance=None, measure=None, spec=None, options=None, fields=None, ignore=None,
            categorical=None, level=0.95, include_cells=True):
    """A két kinyerő táblájának összevetése → szk.ma.compare-result/v1 (CompareResult).

    key: kulcsoszlop(ok) (pl. 'study', ['study_id', 'arm'], 'row_uid'); None: study_id (+ arm), study, row_uid
    közül az első, amely mindkét táblában megvan és egyedi. tolerance: abszolút tűrés minden számmezőre, vagy
    {mező: szám | {abs, rel}, '*': alapérték}; a tűrésen belüli eltérés egyezésnek számít (within_tolerance).
    measure: a hatásmérték (pl. SMD, RR) — megadva az eltérések hatása (impact) is elkészül a vizsgálat
    hatásméretére; spec (szk.ma.analysis-spec/v1) mellett az összesített becslésre is (a spec opcióival és
    szűrőivel, a pipeline-éval azonos számítással). options: további elemzési opciók (pl. {'pooled': True}).
    fields / ignore / categorical: az összevetett mezők szűkítése, a kihagyott oszlopok (alap: row_uid és a
    megjegyzés-oszlopok), a κ-hoz kategóriásnak tekintett mezők."""
    ta, tb = _load(a, "A"), _load(b, "B")
    _align_fields(ta, tb)
    if key:
        kf, key_source = _key_fields(key, ta, tb), "given"
    else:
        kf, key_source = _choose_key(ta, tb)
    flds = _Fields(ta, tb, kf, fields, ignore, categorical)
    tol_map, tol_default = _tolerance(tolerance, ta)
    imp = _Impact(ta, measure, spec, options)
    keys_a, keys_b = _row_keys(ta, kf), _row_keys(tb, kf)
    idx_b = {n: j for j, (_, n, _d) in enumerate(keys_b)}
    idx_a = {n: i for i, (_, n, _d) in enumerate(keys_a)}
    pairs, pair_index = [], {}
    for i, (disp, norm, _d) in enumerate(keys_a):
        j = idx_b.get(norm)
        if j is not None:
            pairs.append((i, j, disp))
            pair_index[disp] = (i, j)
    only_a = [(i, disp) for i, (disp, norm, _d) in enumerate(keys_a) if norm not in idx_b]
    only_b = [(j, disp) for j, (disp, norm, _d) in enumerate(keys_b) if norm not in idx_a]
    warnings = []
    for side, t, keys in (("A", ta, keys_a), ("B", tb, keys_b)):
        empty, dup = _key_quality(t, kf)
        if dup:
            warnings.append(_t("A(z) %s táblában %d sor kulcsa ismétlődik (%s): ezeket előfordulási sorrendben "
                               "párosítottuk ('#2', '#3' utótag). Bővítsd a kulcsot (pl. study_id + arm)."
                               % (side, dup, " + ".join(kf)),
                               "%d rows of table %s have a repeated key (%s): they were paired in order of "
                               "occurrence ('#2', '#3' suffix). Extend the key (e.g. study_id + arm)."
                               % (dup, side, " + ".join(kf))))
        if empty:
            warnings.append(_t("A(z) %s táblában %d sor kulcsa üres: ezek nem párosíthatók." % (side, empty),
                               "%d rows of table %s have an empty key: they cannot be paired." % (empty, side)))
    for f in flds.only_a:
        warnings.append(_t("A(z) %s oszlop csak az A táblában van: a B-ben minden cellája hiányzónak számít."
                           % f, "Column %s exists only in table A: all its cells count as missing in B." % f))
    for f in flds.only_b:
        warnings.append(_t("A(z) %s oszlop csak a B táblában van: az A-ban minden cellája hiányzónak számít."
                           % f, "Column %s exists only in table B: all its cells count as missing in A." % f))

    by_field = collections.OrderedDict()
    for f in flds.order:
        by_field[f] = collections.Counter()
    cat_pairs = collections.defaultdict(list)
    num_pairs = collections.defaultdict(list)
    disagreements, pair_docs = [], []
    for i, j, disp in pairs:
        cells = collections.OrderedDict()
        for f in flds.order:
            ftype = flds.type[f]
            sa, sb = ta.text(i, f), tb.text(j, f)
            va, vb = ta.value(i, f), tb.value(j, f)
            ca = category(f, sa) if ftype == "categorical" else None
            cb = category(f, sb) if ftype == "categorical" else None
            st, kind = _cell_status(ftype, sa, sb, va, vb, (i, f) in ta.parse_err, (j, f) in tb.parse_err, ca, cb,
                                    tol_map.get(f, tol_default))
            cells[f] = st
            by_field[f][st] += 1
            if ftype == "categorical" and ca is not None and cb is not None:
                cat_pairs[f].append((ca, cb))
            if ftype == "numeric" and _is_number(va) and _is_number(vb) and not _na(sa) and not _na(sb):
                num_pairs[f].append((float(va), float(vb)))
            if kind is None:
                continue
            d = collections.OrderedDict()
            d["key"] = disp
            d["row_uid_a"] = ta.uids[i]
            d["row_uid_b"] = tb.uids[j]
            d["row_a"] = i
            d["row_b"] = j
            d["field"] = f
            d["column_a"] = ta.column_name(f)
            d["column_b"] = tb.column_name(f)
            ra, rb = ta.raw(i, f), tb.raw(j, f)
            d["a"] = ra if ra is not None and ra.strip() else None
            d["b"] = rb if rb is not None and rb.strip() else None
            d["a_value"] = _num(va) if ftype == "numeric" else None
            d["b_value"] = _num(vb) if ftype == "numeric" else None
            if ftype == "categorical":
                d["a_category"] = ca
                d["b_category"] = cb
            d["kind"] = kind
            d["status"] = st
            d["type"] = ftype
            amb = (i, f) in ta.ambiguous or (j, f) in tb.ambiguous
            hints = _hints_for(kind, f, ftype, va, vb, ca, cb, sa, sb, ta, i, tb, j, amb)
            d["hint"] = hints[0]["text"] if hints else None
            d["hint_code"] = hints[0]["code"] if hints else None
            d["kb"] = next((h["kb"] for h in hints if h.get("kb")), None)
            d["hints"] = hints
            d["auto"] = kind in AUTO_KINDS
            d["needs_decision"] = kind not in AUTO_KINDS
            d["impact"] = _impact(imp, ta, i, tb, j, f) if kind not in AUTO_KINDS else None
            disagreements.append(d)
        pd = collections.OrderedDict((("key", disp), ("row_uid_a", ta.uids[i]), ("row_uid_b", tb.uids[j]),
                                      ("row_a", i), ("row_b", j)))
        if include_cells:
            pd["cells"] = cells
        pair_docs.append(pd)

    # sorrend: ki/bekerülést okozó, majd a hatás nagysága szerint, utána a hatás nélküliek, végül a csak-írásmód
    def order(item):
        pos, d = item
        imp_d = d.get("impact") or {}
        if d["kind"] in AUTO_KINDS:
            return (3, 0.0, pos)
        if imp_d.get("changes_inclusion"):
            return (0, 0.0, pos)
        if imp_d.get("rank") is not None:
            return (1, -imp_d["rank"], pos)
        return (2, 0.0, pos)
    disagreements = [d for _, d in sorted(enumerate(disagreements), key=order)]

    row_impacts = []
    if imp.enabled and imp.pooled_enabled:
        for i, disp in only_a:
            ri = _row_impact_a(imp, ta, i)
            if ri is not None:
                row_impacts.append({"key": disp, "side": "a", "impact": ri})
        for j, disp in only_b:
            ri = _row_impact(imp, ta, tb, j)
            if ri is not None:
                row_impacts.append({"key": disp, "side": "b", "impact": ri})
    if imp.skipped:
        warnings.append(_t("Az összesített becslés újraszámolása %d eltérésnél kimaradt (felső korlát: %d)."
                           % (imp.skipped, MAX_POOLED_RERUNS),
                           "The pooled estimate was not recomputed for %d disagreements (limit: %d)."
                           % (imp.skipped, MAX_POOLED_RERUNS)))

    summary = _summary(ta, tb, pairs, by_field, flds, cat_pairs, num_pairs, level)
    res = CompareResult()
    res["schema"] = SCHEMA
    res["engine_version"] = __version__
    res["key"] = list(kf)
    res["key_source"] = key_source
    res["summary"] = summary
    res["disagreements"] = disagreements
    res["only_a"] = [disp for _, disp in only_a]
    res["only_b"] = [disp for _, disp in only_b]
    res["only_a_rows"] = [{"key": disp, "row_uid": ta.uids[i], "row": i} for i, disp in only_a]
    res["only_b_rows"] = [{"key": disp, "row_uid": tb.uids[j], "row": j} for j, disp in only_b]
    res["pairs"] = pair_docs
    res["row_impacts"] = row_impacts
    res["columns"] = [{"field": f, "a": ta.column_name(f), "b": tb.column_name(f), "type": flds.type[f]}
                      for f in flds.order]
    res["impact_info"] = _impact_info(imp)
    res["sources"] = {"a": ta.info(), "b": tb.info()}
    res["params"] = {"tolerance": tolerance if isinstance(tolerance, (int, float, dict)) else None,
                     "measure": imp.measure, "fields": list(fields) if fields else None,
                     "ignore": list(ignore) if ignore is not None else None,
                     "categorical": list(categorical) if categorical else None, "level": level}
    res["warnings"] = warnings
    res["notes"] = _notes()
    res["agreement_text"] = agreement_report(res)["text"]
    res.tables = {"a": ta, "b": tb, "pairs": pair_index, "only_a": dict((d, i) for i, d in only_a),
                  "only_b": dict((d, j) for j, d in only_b)}
    return res


def _impact_info(imp):
    if not imp.enabled:
        return {"computed": False, "measure": None, "pooled": False,
                "reason": _t("Nincs megadva hatásmérték (measure) vagy elemzési spec: a hatás nem számolható.",
                             "No effect measure or analysis spec given: impact not computed.")}
    out = {"computed": True, "measure": imp.measure, "pooled": imp.pooled_enabled,
           "spec": (imp.spec or {}).get("name") if isinstance(imp.spec, dict) else None,
           "fields": sorted(imp.fields), "reruns": imp.reruns}
    if imp.pooled_enabled:
        out["baseline"] = imp.base_pool()
    return out


def _notes():
    return [
        _t("Egyezés (%): az összevetett cellák aránya, ahol a két kinyerő ugyanazt írta; a csak írásmódban eltérő "
           "(pl. 12,3 ↔ 12.30) és a tűrésen belüli cellák egyezőnek számítanak, a mindkét táblában üres cellák nem "
           "számítanak bele.",
           "Agreement (%): share of compared cells where both extractors entered the same value; cells differing "
           "only in formatting (e.g. 12,3 ↔ 12.30) or within the tolerance count as agreement, cells empty in both "
           "tables are not counted."),
        _t("Cohen-féle κ (kappa): a kategóriás mezők (pl. rob) egyezése a véletlen egyezéshez mérve — 0 = csak a "
           "véletlen, 1 = tökéletes. Landis & Koch (1977) szerint 0.61–0.80 jelentős, 0.80 felett majdnem tökéletes.",
           "Cohen's κ (kappa): agreement on categorical fields (e.g. rob) beyond chance — 0 = chance only, 1 = "
           "perfect. Landis & Koch (1977): 0.61–0.80 substantial, above 0.80 almost perfect."),
        _t("ICC(A,1): a számmezők abszolút egyezése (kétutas véletlen modell; Shrout & Fleiss ICC(2,1)) — 1 = a két "
           "kinyerő számai azonosak. Koo & Li (2016): 0.90 felett kiváló. Kevés vizsgálatnál a CI széles.",
           "ICC(A,1): absolute agreement on numeric fields (two-way random model; Shrout & Fleiss ICC(2,1)) — 1 = "
           "identical numbers. Koo & Li (2016): above 0.90 excellent. With few studies the CI is wide."),
        _t("Hatás: mennyit mozdulna a vizsgálat hatásmérete (spec mellett az összesített becslés is), ha az A értéke "
           "helyett a B értékét használnánk — minden más cella az A szerinti. Előre kerülnek a legnagyobb hatású "
           "eltérések.",
           "Impact: how much the study's effect size (and, with a spec, the pooled estimate) would move if B's value "
           "were used instead of A's — all other cells as in A. The most influential disagreements come first."),
    ]


def _summary(ta, tb, pairs, by_field, flds, cat_pairs, num_pairs, level):
    tot = collections.Counter()
    bf_out = collections.OrderedDict()
    for f, c in by_field.items():
        tot.update(c)
        compared = sum(v for s, v in c.items() if s != "both_empty")
        agree = sum(c[s] for s in AGREE)
        dis = sum(c[s] for s in DISAGREE)
        e = collections.OrderedDict()
        e["compared"] = compared
        e["disagree"] = dis
        e["agree"] = agree
        for s in STATUSES:
            e[s] = c[s]
        e["agreement_pct"] = 100.0 * agree / compared if compared else None
        e["agreement_pct_text"] = _pct_text(e["agreement_pct"])
        e["type"] = flds.type[f]
        if flds.type[f] == "categorical":
            k = cohen_kappa(cat_pairs.get(f, []), level)
            e["kappa"] = _num(k["kappa"])
            e["kappa_ci"] = [_num(x) for x in k["ci"]] if k["ci"] else None
            e["kappa_se"] = _num(k["se"])
            e["kappa_n"] = k["n"]
            e["kappa_po"] = _num(k["po"])
            e["kappa_pe"] = _num(k["pe"])
            e["categories"] = list(k["categories"])
            e["kappa_text"] = _ci_text("κ", k["kappa"], k["ci"]) if k["kappa"] is not None else (
                _t("κ nem számolható (%s)" % ("nincs összevethető cella" if not k["n"] else "egyetlen kategória"),
                   "κ not estimable (%s)" % ("no comparable cell" if not k["n"] else "single category")))
            e["kappa_label"] = _kappa_label(k["kappa"])
        elif flds.type[f] == "numeric":
            ps = num_pairs.get(f, [])
            ic = icc_a1(ps, level)
            e["icc"] = _num(ic["icc"])
            e["icc_ci"] = [_num(x) for x in ic["ci"]] if ic["ci"] else None
            e["icc_n"] = ic["n"]
            e["icc_text"] = _ci_text("ICC", ic["icc"], ic["ci"])
            e["icc_label"] = _icc_label(ic["icc"])
            diffs = [x - y for x, y in ps]
            if diffs:
                md = sum(diffs) / len(diffs)
                sd = math.sqrt(sum((d - md) ** 2 for d in diffs) / (len(diffs) - 1)) if len(diffs) > 1 else None
                e["mean_diff"] = _num(md)
                e["sd_diff"] = _num(sd)
                e["loa"] = [_num(md - Z95 * sd), _num(md + Z95 * sd)] if sd is not None else None
                e["max_abs_diff"] = _num(max(abs(d) for d in diffs))
            else:
                e["mean_diff"] = e["sd_diff"] = e["loa"] = e["max_abs_diff"] = None
        bf_out[f] = e
    compared = sum(v for s, v in tot.items() if s != "both_empty")
    agree = sum(tot[s] for s in AGREE)
    s = collections.OrderedDict()
    s["rows_a"] = len(ta)
    s["rows_b"] = len(tb)
    s["matched"] = len(pairs)
    s["cells_compared"] = compared
    s["agree"] = agree
    s["disagree"] = sum(tot[x] for x in DISAGREE)
    s["agreement_pct"] = 100.0 * agree / compared if compared else None
    s["agreement_pct_text"] = _pct_text(s["agreement_pct"])
    for st in STATUSES:
        s[st] = tot[st]
    s["fields_compared"] = list(flds.order)
    s["fields_only_a"] = list(flds.only_a)
    s["fields_only_b"] = list(flds.only_b)
    s["by_field"] = bf_out
    return s


# ------------------------------------------------------------------ egyetértési táblázat (Methods)
def agreement_report(result, consensus=None):
    """Az egyetértési táblázat és a Methods-szöveg {hu, en} a compare() eredményéből (a felület 'Egyetértési
    táblázat' gombja). consensus: a szk.ma.consensus/v1 dokumentum (a feloldás módjának leírásához)."""
    s = result.get("summary") or {}
    key = " + ".join(result.get("key") or [])
    rows = []
    kap_hu, kap_en, icc_hu, icc_en = [], [], [], []
    for f, e in (s.get("by_field") or {}).items():
        rows.append({"field": f, "type": e.get("type"), "compared": e.get("compared"), "agree": e.get("agree"),
                     "disagree": e.get("disagree"), "agreement_pct": e.get("agreement_pct"),
                     "kappa": e.get("kappa"), "kappa_ci": e.get("kappa_ci"), "icc": e.get("icc"),
                     "icc_ci": e.get("icc_ci")})
        if e.get("kappa") is not None:
            t = _triple_text(e["kappa"], e.get("kappa_ci"))
            kap_hu.append("%s %s" % (f, t["hu"]))
            kap_en.append("%s %s" % (f, t["en"]))
        if e.get("icc") is not None:
            t = _triple_text(e["icc"], e.get("icc_ci"))
            icc_hu.append("%s %s" % (f, t["hu"]))
            icc_en.append("%s %s" % (f, t["en"]))
    pct = s.get("agreement_pct")
    pt = _pct_text(pct) or _t("–", "–")
    hu = ("Az adatokat két kinyerő egymástól függetlenül gyűjtötte ki; a két táblát soronként (kulcs: %s) vetettük "
          "össze (%d párosított sor). Az összevetett %d cellából %d egyezett (%s; a csak írásmódban eltérő cellák "
          "egyezőnek számítanak)." % (key, s.get("matched", 0), s.get("cells_compared", 0), s.get("agree", 0),
                                      pt["hu"]))
    en = ("Data were extracted independently by two reviewers; the two tables were compared row by row (key: %s; "
          "%d matched rows). Of %d compared cells, %d agreed (%s; cells differing only in formatting were counted "
          "as agreement)." % (key, s.get("matched", 0), s.get("cells_compared", 0), s.get("agree", 0), pt["en"]))
    if kap_hu:
        hu += " Cohen-féle κ (95%% CI): %s." % "; ".join(kap_hu)
        en += " Cohen's κ (95%% CI): %s." % "; ".join(kap_en)
    if icc_hu:
        hu += " Abszolút egyezésű ICC(A,1) (95%% CI): %s." % "; ".join(icc_hu)
        en += " Absolute-agreement ICC(A,1) (95%% CI): %s." % "; ".join(icc_en)
    n_only = len(result.get("only_a") or []) + len(result.get("only_b") or [])
    if n_only:
        hu += " %d sor csak az egyik táblában szerepelt." % n_only
        en += " %d rows appeared in only one table." % n_only
    dis = s.get("disagree", 0)
    unresolved = None
    if isinstance(consensus, dict):
        unresolved = len(consensus.get("unresolved") or [])
    if dis or n_only:
        if unresolved:
            hu += " Az eltérések egyeztetése folyamatban (%d feloldatlan)." % unresolved
            en += " Reconciliation in progress (%d unresolved)." % unresolved
        else:
            hu += " Az eltéréseket (%d) a forrás újbóli ellenőrzésével, egyeztetéssel oldottuk fel; a döntéseket " \
                  "indoklással dokumentáltuk." % (dis + n_only)
            en += " Disagreements (n = %d) were resolved by consensus after re-checking the source; each decision " \
                  "was documented with its rationale." % (dis + n_only)
    return {"text": _t(hu, en), "table": rows}


# ------------------------------------------------------------------ egyeztetés
def _dkey(key, field):
    return "%s%s%s" % (key, _SEP, field)


def _norm_decision(d, n):
    if not isinstance(d, dict):
        raise KettosError("A(z) %d. döntés nem objektum." % (n + 1))
    cell = d.get("cell")
    key, field = d.get("key"), d.get("field")
    if isinstance(cell, dict):
        key, field = cell.get("key", key), cell.get("field", field)
    elif isinstance(cell, (list, tuple)) and len(cell) == 2:
        key, field = cell
    elif isinstance(cell, str) and _SEP in cell:
        key, field = cell.split(_SEP, 1)
    if not isinstance(key, str) or not isinstance(field, str) or not field:
        raise KettosError("A(z) %d. döntésből hiányzik a cella (key + field)." % (n + 1))
    chosen = d.get("chosen", d.get("choice"))
    if chosen == "value":
        chosen = "other"
    if chosen not in CHOICES:
        raise KettosError("A(z) %d. döntés (mező: %s) választása a, b vagy other (saját érték) lehet." % (n + 1, field))
    reason = d.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise KettosError("Indoklás nélkül nem rögzíthető döntés: %d. döntés (mező: %s). Írd le röviden, miért ez a "
                          "helyes érték (pl. „Table 2 lábjegyzete: SD”)." % (n + 1, field))
    value = d.get("value")
    if chosen == "other" and field != ROW_FIELD and not isinstance(value, str):
        raise KettosError("Saját érték választásához add meg az értéket (szövegként): %d. döntés (mező: %s)."
                          % (n + 1, field))
    out = {"key": key, "field": field, "chosen": chosen, "value": value if isinstance(value, str) else None,
           "reason": reason.strip(), "actor": d.get("actor"), "ts": d.get("ts")}
    for k in ("a", "b"):
        if k in d:
            out["_" + k] = d.get(k)
    return out


def _items(res):
    """A döntést igénylő tételek: {(kulcs, mező): {key, field, kind, a, b, level, …}}."""
    items = collections.OrderedDict()
    for d in res.get("disagreements") or []:
        if d.get("kind") in AUTO_KINDS:
            continue
        items[_dkey(d["key"], d["field"])] = {"key": d["key"], "field": d["field"], "kind": d["kind"],
                                              "a": d.get("a"), "b": d.get("b"), "level": "cell",
                                              "row_uid_a": d.get("row_uid_a"), "row_uid_b": d.get("row_uid_b")}
    for side in ("a", "b"):
        for k in res.get("only_" + side) or []:
            items[_dkey(k, ROW_FIELD)] = {"key": k, "field": ROW_FIELD, "kind": "only_" + side, "a": None, "b": None,
                                          "level": "row"}
    return items


def reconcile(compare_result, decisions, a=None, b=None, actor=None, now=None, allow_unresolved=False,
              outcome=None):
    """Döntések → konszenzus. compare_result: a compare() eredménye (CompareResult: a táblákkal); sima dict (pl.
    fájlból) mellett az a és b tábla is kell — ekkor a motor ugyanazzal a kulccsal és paraméterekkel újra
    összevet, így a döntések a mostani táblákra vonatkoznak.

    decisions: [{key, field, chosen: a|b|other, value?, reason, actor?, ts?}] (a 'cell': {key, field} és a
    'choice' alak is jó; 'value' = other). Sorszintű döntés (csak az egyik táblában szereplő sor): field '*',
    a → az A változata (only_a: a sor bekerül, only_b: kimarad), b → a B változata. Ha a döntés tartalmazza a
    döntéskori A/B szöveget ('a', 'b'), és a tábla azóta változott, a döntés ELAVULT: feloldatlannak számít.

    → {consensus: szk.ma.consensus/v1 (döntések indoklással, unresolved, orphans, sources, summary),
       table: {header, rows[{row_uid, cells}], reconciled[…], uid_column, format, decimal_mark} vagy None
       (feloldatlan eltérésnél, hacsak allow_unresolved nem igaz — X009)}."""
    if a is not None and b is not None:
        p = compare_result.get("params") or {}
        res = compare(a, b, key=compare_result.get("key"), tolerance=p.get("tolerance"), fields=p.get("fields"),
                      ignore=p.get("ignore"), categorical=p.get("categorical"))
    elif getattr(compare_result, "tables", None) is not None:
        res = compare_result
    else:
        raise KettosError("A konszenzushoz a két tábla is kell (a, b), vagy a compare() közvetlen eredménye.")
    ta, tb = res.tables["a"], res.tables["b"]
    items = _items(res)
    ts_now = _now_ts(now)
    applied, orphans, by = [], [], {}
    for n, raw in enumerate(decisions or []):
        d = _norm_decision(raw, n)
        dk = _dkey(d["key"], d["field"])
        it = items.get(dk)
        if it is None:
            orphans.append({k: d.get(k) for k in ("key", "field", "chosen", "value", "reason", "actor", "ts")})
            continue
        if it["level"] == "row" and d["chosen"] not in ("a", "b"):
            raise KettosError("A(z) %d. döntés egy egész sorra vonatkozik: csak az A vagy a B változata választható."
                              % (n + 1))
        by[dk] = d
    unresolved = []
    decided = collections.OrderedDict()
    for dk, it in items.items():
        d = by.get(dk)
        stale = False
        if d is not None and it["level"] == "cell" and ("_a" in d or "_b" in d):
            stale = d.get("_a", it["a"]) != it["a"] or d.get("_b", it["b"]) != it["b"]
        if d is None or stale:
            u = {"key": it["key"], "field": it["field"], "kind": it["kind"], "a": it["a"], "b": it["b"]}
            if stale:
                u["stale"] = True
            unresolved.append(u)
            continue
        decided[dk] = (it, d)
    table = None
    if not unresolved or allow_unresolved:
        table = _build_table(res, ta, tb, decided)
    cell_text = {}
    if table is not None:
        for r in table["reconciled"]:
            if r.get("level") == "cell":
                cell_text[_dkey(r["key"], r["field"])] = r["value"]
    for dk, (it, d) in decided.items():
        if it["level"] == "row":
            value = None
        elif dk in cell_text:
            value = cell_text[dk]
        else:
            value = _decision_text(res, ta, tb, it, d)
        act = d.get("actor") or actor or "user"
        ts = d["ts"] if isinstance(d.get("ts"), str) and _TS_RE.match(d["ts"]) else ts_now
        entry = collections.OrderedDict((("key", it["key"]), ("field", it["field"]), ("chosen", d["chosen"]),
                                         ("value", value), ("reason", d["reason"]), ("actor", str(act)),
                                         ("ts", ts), ("a", it["a"]), ("b", it["b"]), ("kind", it["kind"])))
        applied.append(entry)
    auto = sum(1 for d in res.get("disagreements") or [] if d.get("kind") in AUTO_KINDS)
    doc = collections.OrderedDict()
    doc["schema"] = CONSENSUS_SCHEMA
    if outcome:
        doc["outcome"] = outcome
    doc["key"] = list(res["key"])
    doc["decisions"] = applied
    doc["unresolved"] = unresolved
    doc["orphans"] = orphans
    doc["complete"] = not unresolved
    doc["summary"] = {"total": len(items), "decided": len(decided), "unresolved": len(unresolved),
                      "stale": sum(1 for u in unresolved if u.get("stale")), "auto": auto, "orphans": len(orphans)}
    doc["sources"] = {"a": ta.info(), "b": tb.info()}
    doc["engine_version"] = __version__
    return {"consensus": doc, "table": table}


def _decision_text(res, ta, tb, it, d):
    i, j = res.tables["pairs"].get(it["key"], (None, None))
    if d["chosen"] == "a":
        return ta.raw(i, it["field"]) if i is not None and ta.raw(i, it["field"]) is not None else ""
    if d["chosen"] == "b":
        return to_a_format(tb.text(j, it["field"]), tb.value(j, it["field"]), it["field"], ta) if j is not None else ""
    return d.get("value") or ""


def _build_table(res, ta, tb, decided):
    """A konszenzus-tábla: az A sorrendje és írásmódja; a döntött cellák a döntés szerint (a B szövege az A
    formátumában), az egyező és csak-írásmódban eltérő cellák az A szövegével; csak-B sorok a 'b' döntéssel."""
    uid_col = ta.uid_index
    cols = [c for c in range(len(ta.header)) if c != uid_col]
    header = [ta.header[c] for c in cols]
    fields = [ta.fields[c] for c in cols]
    compared = set((res.get("summary") or {}).get("fields_compared") or [])
    extra = [f for f in tb.fields if f not in ta.col_of and f in compared]
    header += [tb.column_name(f) for f in extra]
    fields += extra
    pos = {f: k for k, f in enumerate(fields)}
    only_a_at = {i: k for k, i in res.tables["only_a"].items()}
    out, reconciled, taken = [], [], set()
    pairs = res.tables["pairs"]
    by_a = {i: k for k, (i, j) in pairs.items()}
    cell_dec = collections.defaultdict(list)
    for dk, (it, d) in decided.items():
        if it["level"] == "cell":
            cell_dec[it["key"]].append((it, d))
    intended = {}
    for i in range(len(ta)):
        k = by_a.get(i)
        if k is None:
            k = only_a_at.get(i)
            if k is not None:
                dec = decided.get(_dkey(k, ROW_FIELD))
                if dec is not None and dec[1]["chosen"] == "b":
                    continue
        r = ta.rows[i]
        cells = [r[c] if c < len(r) else "" for c in cols] + [""] * len(extra)
        uid = ta.uids[i]
        for f in fields:
            if f in ta.col_of and _is_number(ta.value(i, f)):
                intended[(uid, f)] = float(ta.value(i, f))
        j = pairs.get(k, (None, None))[1] if k in pairs else None
        for it, d in cell_dec.get(k, []):
            f = it["field"]
            if d["chosen"] == "a":
                text = ta.raw(i, f) or ""
                val = ta.value(i, f)
            elif d["chosen"] == "b":
                text = to_a_format(tb.text(j, f), tb.value(j, f), f, ta)
                val = tb.value(j, f)
            else:
                text = d.get("value") or ""
                val = None
            cells[pos[f]] = text
            intended.pop((uid, f), None)
            if _is_number(val) and d["chosen"] in ("a", "b") and not _na(text):
                intended[(uid, f)] = float(val)
            reconciled.append({"row_uid": uid, "field": f, "key": k, "chosen": d["chosen"], "value": text,
                               "a": it["a"], "b": it["b"], "reason": d["reason"], "level": "cell"})
        taken.add(uid)
        out.append({"row_uid": uid, "cells": cells})
    for k, j in res.tables["only_b"].items():
        dec = decided.get(_dkey(k, ROW_FIELD))
        if dec is None or dec[1]["chosen"] != "b":
            continue
        uid = tb.uids[j]
        if uid in taken:
            uid = tableio.row_uid_for(tb.prow[j].get("study"), len(out), taken)
        taken.add(uid)
        cells = []
        for f in fields:
            if f in tb.col_of:
                text = to_a_format(tb.text(j, f), tb.value(j, f), f, ta)
                if _is_number(tb.value(j, f)) and not _na(text):
                    intended[(uid, f)] = float(tb.value(j, f))
            else:
                text = ""
            cells.append(text)
            if text.strip():
                reconciled.append({"row_uid": uid, "field": f, "key": k, "chosen": "b", "value": text, "a": None,
                                   "b": tb.raw(j, f), "reason": dec[1]["reason"], "level": "row"})
        out.append({"row_uid": uid, "cells": cells})
    _verify_roundtrip(header, fields, out, intended, ta)
    fmt = dict(ta.fmt) if ta.fmt else None
    return {"header": header, "rows": out, "reconciled": reconciled, "uid_column": uid_col, "fields": fields,
            "format": fmt, "decimal_mark": _target_mark(ta), "delimiter": ta.delimiter}


def _verify_roundtrip(header, fields, rows, intended, ta):
    """A konszenzus-tábla visszaolvasva (tizedesjel-felismeréssel, mint egy fájlnál) ugyanazokat a számokat adja-e."""
    try:
        prow, pmeta = tableio.parse_table(header, [r["cells"] for r in rows], None, ta.delimiter)
    except ValueError as exc:
        raise KettosError("A konszenzus-tábla nem olvasható vissza: %s" % exc) from None
    mapping = pmeta["mapping"]
    canon = set(mapping[c] for c in pmeta["columns"])
    parsed_at = {pos: n for n, pos in enumerate(pmeta.get("row_positions") or [])}
    bad = collections.Counter()
    for pos, r in enumerate(rows):
        n = parsed_at.get(pos)
        for f in canon:
            want = intended.get((r["row_uid"], f))
            if want is None:
                continue
            got = prow[n].get(f) if n is not None else None
            if not (_is_number(got) and _same_num(float(got), want)):
                bad[f] += 1
    if bad:
        raise KettosError("A konszenzus-tábla visszaolvasva más számot adna (%s): a két tábla tizedesjele / ezres "
                          "tagolása ütközik. Egységesítsd az érintett cellák írásmódját (pl. tagolás nélkül), majd "
                          "próbáld újra." % ", ".join("%s: %d cella" % kv for kv in sorted(bad.items())))


def consensus_table(a, b, consensus, key=None, tolerance=None):
    """A munkapad konszenzus-építője (ma_gui extraction_dual_common.ENGINE['consensus_table']):
    (a, b, szk.ma.consensus/v1, key) → {header, rows[{row_uid, cells}], reconciled[{row_uid, field, key, chosen,
    value}], uid_column, format, consensus}. Feloldatlan eltérésnél KettosError (X009)."""
    if not isinstance(consensus, dict):
        raise KettosError("A konszenzus-dokumentum objektum legyen (szk.ma.consensus/v1).")
    key = key or consensus.get("key") or None
    res = compare(a, b, key=key, tolerance=tolerance)
    out = reconcile(res, consensus.get("decisions") or [])
    if out["table"] is None:
        n = len(out["consensus"]["unresolved"])
        raise KettosError("%d eltérés még feloldatlan (vagy a döntése elavult): a konszenzus-tábla csak akkor "
                          "készíthető el, ha minden eltérésről döntöttél (X009)." % n)
    t = out["table"]
    return {"header": t["header"], "rows": t["rows"], "reconciled": t["reconciled"], "uid_column": t["uid_column"],
            "format": t["format"], "decimal_mark": t["decimal_mark"], "consensus": out["consensus"]}


# ------------------------------------------------------------------ kiírás
def render_table(table, include_uid=True):
    """A konszenzus-tábla bájtjai az A tábla formátumában (tableio.render_raw); a row_uid oszlop az A-beli helyén
    (ha az A-ban nem volt, a végén)."""
    header, rows = list(table["header"]), [list(r["cells"]) for r in table["rows"]]
    if include_uid:
        pos = table.get("uid_column")
        pos = len(header) if pos is None or pos > len(header) else pos
        header.insert(pos, tableio.UID_COLUMN)
        for r, src in zip(rows, table["rows"]):
            r.insert(pos, src["row_uid"])
    fmt = dict(table.get("format") or {})
    fmt.pop("gaps", None)
    fmt.pop("lead_records", None)
    fmt["lead_blank"] = 0
    if table.get("delimiter") and not fmt.get("delimiter"):
        fmt["delimiter"] = table["delimiter"]
    return tableio.render_raw(header, rows, fmt)


def _atomic_write(path, data):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return hashlib.sha256(data).hexdigest()


def json_bytes(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")


def provenance_doc(table, table_rel, table_sha256, consensus_rel):
    """A konszenzus-CSV eredet-oldalfájlja (szk.ma.provenance/v1): az egyeztetett cellák 'reconciled' módszerrel."""
    cells = []
    for r in table["reconciled"]:
        cells.append(collections.OrderedDict((
            ("row_uid", r["row_uid"]), ("field", r["field"]), ("value_as_entered", r["value"]),
            ("method", "reconciled"), ("estimated", False),
            ("reconciliation", {"key": r["key"], "a": r.get("a"), "b": r.get("b"), "chosen": r["chosen"],
                                "reason": r.get("reason"), "consensus": consensus_rel}))))
    return collections.OrderedDict((("schema", PROVENANCE_SCHEMA), ("table", table_rel),
                                    ("table_sha256", table_sha256), ("cells", cells)))


# ------------------------------------------------------------------ projektfájlok és X009
def outcome_paths(project_dir, outcome, data=None):
    """A kimenet kettős kinyerés-fájljai (projekt-relatív utak, terv 2.4): {dir, a, b, json, csv, prov, inbox}.
    A mappa az adattábla mellett van ('03_adatok/o1.csv' → '03_adatok/kettos'); adattábla nélkül 03_adatok/kettos."""
    if not isinstance(outcome, str) or not _OUTCOME_RE.match(outcome):
        raise KettosError("Érvénytelen kimenet-azonosító (betű, szám, _ . -; pl. o1).")
    d = DEFAULT_DIR
    if isinstance(data, str) and data.strip():
        head = data.replace("\\", "/").rpartition("/")[0]
        d = (head + "/" if head else "") + KETTOS_DIR
    base = "%s/%s" % (d, outcome)
    return {"dir": d, "a": base + ".A.csv", "b": base + ".B.csv", "json": base + ".consensus.json",
            "csv": base + ".consensus.csv", "prov": base + ".consensus.prov.json", "inbox": d + "/" + INBOX}


def _abs(root, rel):
    return os.path.join(root, *rel.split("/"))


def _project_outcomes(project_dir):
    """[(kimenet, adattábla-út)] a ma-projekt.json-ból és a kettos/ mappák *.A.csv / *.B.csv fájljaiból."""
    found = collections.OrderedDict()
    try:
        from . import projekt
        meta = projekt.load_project_meta(project_dir)
    except (ValueError, OSError):
        meta = None
    for o in (meta or {}).get("outcomes") or []:
        if isinstance(o, dict) and isinstance(o.get("id"), str) and _OUTCOME_RE.match(o["id"]):
            found.setdefault(o["id"], o.get("data") if isinstance(o.get("data"), str) else None)
    dirs = {DEFAULT_DIR, PRIVATE_DIR}
    dirs.update(outcome_paths(project_dir, oid, data)["dir"] for oid, data in found.items())
    for d in sorted(dirs):
        p = _abs(project_dir, d)
        if not os.path.isdir(p):
            continue
        for name in sorted(os.listdir(p)):
            m = re.match(r"^(.+)\.(A|B)\.csv$", name)
            if m and _OUTCOME_RE.match(m.group(1)) and m.group(1) not in found:
                found[m.group(1)] = None if d == DEFAULT_DIR else d.rpartition("/")[0] + "/" + m.group(1) + ".csv"
    return list(found.items())


def load_consensus(path):
    """A konszenzus-fájl (szk.ma.consensus/v1) vagy None (nincs fájl). Hibás fájl → KettosError."""
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8-sig"), parse_constant=lambda n: None)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise KettosError("A konszenzus-fájl nem olvasható: %s" % type(exc).__name__) from None
    if not isinstance(doc, dict) or doc.get("schema") != CONSENSUS_SCHEMA or not isinstance(doc.get("decisions"),
                                                                                             list):
        raise KettosError("A konszenzus-fájl nem szk.ma.consensus/v1 dokumentum.")
    return doc


def outcome_status(project_dir, outcome, data=None, tolerance=None):
    """Egy kimenet kettős kinyerésének állapota: {outcome, paths, has_a, has_b, has_consensus, total, decided,
    unresolved, stale, auto, complete, error}. Az összevetés kulcsa a konszenzus-fájl kulcsa (ha van)."""
    paths = outcome_paths(project_dir, outcome, data)
    st = collections.OrderedDict((("outcome", outcome), ("paths", paths),
                                  ("has_a", os.path.isfile(_abs(project_dir, paths["a"]))),
                                  ("has_b", os.path.isfile(_abs(project_dir, paths["b"]))),
                                  ("has_consensus", os.path.isfile(_abs(project_dir, paths["json"]))),
                                  ("total", None), ("decided", None), ("unresolved", None), ("stale", None),
                                  ("auto", None), ("complete", None), ("error", None)))
    if not (st["has_a"] and st["has_b"]):
        return st
    try:
        doc = load_consensus(_abs(project_dir, paths["json"]))
        key = (doc or {}).get("key") or None
        try:
            res = compare(_abs(project_dir, paths["a"]), _abs(project_dir, paths["b"]), key=key, tolerance=tolerance)
        except KettosError:
            if not key:
                raise
            res = compare(_abs(project_dir, paths["a"]), _abs(project_dir, paths["b"]), tolerance=tolerance)
        decisions = [d for d in (doc or {}).get("decisions") or [] if isinstance(d, dict)]
        valid = []
        for n, d in enumerate(decisions):
            try:
                _norm_decision(d, n)
                valid.append(d)
            except KettosError:
                continue
        out = reconcile(res, valid)
    except KettosError as exc:
        st["error"] = str(exc)
        return st
    sm = out["consensus"]["summary"]
    st.update(total=sm["total"], decided=sm["decided"], unresolved=sm["unresolved"], stale=sm["stale"],
              auto=sm["auto"], complete=out["consensus"]["complete"])
    return st


def project_status(project_dir, tolerance=None):
    """A projekt összes kettős kinyerésének állapota: {outcomes: [outcome_status…], unresolved_total}."""
    outs = [outcome_status(project_dir, oid, data, tolerance) for oid, data in _project_outcomes(project_dir)]
    outs = [o for o in outs if o["has_a"] or o["has_b"]]
    return {"outcomes": outs, "unresolved_total": sum(o["unresolved"] or 0 for o in outs)}


# X009 metaadata az audit.py-nak (a validate.RULES / audit.RULES formátuma)
X009_RULE = ("error", "Kettős kinyerés: feloldatlan eltérés",
             "A két kinyerő táblája között van olyan eltérés (vagy csak az egyik táblában szereplő sor), amelyről még "
             "nincs (vagy elavult) indokolt döntés a konszenzus-fájlban. Amíg ez így van, az S08 (szintézis) PASS nem "
             "adható, és a konszenzus-tábla nem írható ki. Nyisd meg a Kettős kinyerés képernyőt (vagy: ma.py kettos "
             "compare --project <mappa> --outcome <kimenet>), és minden eltérésnél válaszd ki a helyes értéket "
             "indoklással.", "Cochrane Handbook 5.5.2; AMSTAR 2 6. tétel")
X009_STAGE = "S05"
X009_ESCALATION = "S08"
X009_KB_REFS = ("D-S05-001",)


def x009_findings(project_dir, tolerance=None):
    """(találatok, nem ellenőrizhetők) az X009-hez. Találat: {outcome, detail, artifacts, suggested_command,
    unresolved, stale, total}; nem ellenőrizhető: {outcome, reason}. Az üzenetek cellaértéket nem tartalmaznak."""
    found, skipped = [], []
    for st in project_status(project_dir, tolerance)["outcomes"]:
        p = st["paths"]
        if st["error"]:
            skipped.append({"outcome": st["outcome"], "reason": "kettős kinyerés (%s, %s): %s"
                            % (p["a"], p["b"], st["error"])})
            continue
        if not (st["has_a"] and st["has_b"]):
            skipped.append({"outcome": st["outcome"], "reason": "a kettős kinyerés egyik táblája hiányzik (%s)"
                            % (p["b"] if st["has_a"] else p["a"])})
            continue
        if st["unresolved"]:
            detail = "%d feloldatlan eltérés a két kinyerő táblája között (%d döntést igénylő tételből%s)" % (
                st["unresolved"], st["total"], ("; ebből %d döntés elavult, mert a tábla azóta változott"
                                                 % st["stale"]) if st["stale"] else "")
            found.append({"outcome": st["outcome"], "detail": detail,
                          "artifacts": [p["a"], p["b"]] + ([p["json"]] if st["has_consensus"] else []),
                          "suggested_command": ["ma.py", "kettos", "compare", "--project", ".", "--outcome",
                                                st["outcome"]],
                          "unresolved": st["unresolved"], "stale": st["stale"], "total": st["total"]})
    return found, skipped


def require_s08_gate(project_dir):
    """S08 PASS előtt (X009): ValueError, ha bármely kimenet kettős kinyerésében feloldatlan eltérés van."""
    found, _ = x009_findings(project_dir)
    if found:
        raise ValueError("Kettős kinyerés: feloldatlan eltérés van (X009), ezért az S08 PASS nem adható: %s. "
                         "Egyeztesd az eltéréseket (Kettős kinyerés képernyő vagy ma.py kettos compare --project "
                         "<mappa> --outcome <kimenet>)." % "; ".join("%s: %d" % (f["outcome"], f["unresolved"])
                                                                     for f in found))


def compare_project(project_dir, outcome, data=None, key=None, tolerance=None, measure=None, spec=None):
    """A kimenet A/B táblájának összevetése a projektmappában (a kulcs alapból a konszenzus-fájlé)."""
    paths = outcome_paths(project_dir, outcome, data)
    doc = load_consensus(_abs(project_dir, paths["json"]))
    key = key or (doc or {}).get("key") or None
    return compare(_abs(project_dir, paths["a"]), _abs(project_dir, paths["b"]), key=key, tolerance=tolerance,
                   measure=measure, spec=spec)


def reconcile_project(project_dir, outcome, decisions, actor, data=None, key=None, write_csv=False, now=None):
    """Döntések rögzítése a projektben: a meglévő konszenzus-fájl döntéseivel összefésülve (ugyanarra a cellára
    az új döntés nyer) → <kimenet>.consensus.json; write_csv mellett — csak ha nincs feloldatlan eltérés — a
    konszenzus-CSV (az A formátumában, row_uid oszloppal) és az eredet-oldalfájl (reconciled) is. → reconcile()."""
    paths = outcome_paths(project_dir, outcome, data)
    doc = load_consensus(_abs(project_dir, paths["json"]))
    key = key or (doc or {}).get("key") or None
    res = compare(_abs(project_dir, paths["a"]), _abs(project_dir, paths["b"]), key=key)
    merged = collections.OrderedDict()
    for d in list((doc or {}).get("decisions") or []) + list(decisions or []):
        if isinstance(d, dict):
            nd = dict(d)
            if "cell" in nd and ("key" not in nd or "field" not in nd):
                c = _norm_decision(nd, 0)
                nd["key"], nd["field"] = c["key"], c["field"]
            merged[_dkey(nd.get("key"), nd.get("field"))] = nd
    out = reconcile(res, list(merged.values()), actor=actor, now=now, outcome=outcome)
    cons = out["consensus"]
    if write_csv:
        if out["table"] is None:
            raise KettosError("%d eltérés még feloldatlan: a konszenzus-CSV nem írható ki (X009)."
                              % len(cons["unresolved"]))
        data_bytes = render_table(out["table"])
        sha = _atomic_write(_abs(project_dir, paths["csv"]), data_bytes)
        prov = provenance_doc(out["table"], paths["csv"], sha, paths["json"])
        _atomic_write(_abs(project_dir, paths["prov"]), json_bytes(prov))
        cons["csv"] = {"path": paths["csv"], "sha256": sha, "prov": paths["prov"], "rows": len(out["table"]["rows"]),
                       "reconciled": len(out["table"]["reconciled"]), "written_at": _now_ts(now), "by": actor}
    for side in ("a", "b"):
        cons["sources"][side]["path"] = paths[side]
    _atomic_write(_abs(project_dir, paths["json"]), json_bytes(cons))
    return out


# ------------------------------------------------------------------ parancssor
def _format_text(res):
    s = res["summary"]
    lines = ["Kettős kinyerés — kulcs: %s · %d/%d sor párosítva · egyezés %s (%d/%d cella) · eltér %d · csak A: %d · "
             "csak B: %d" % (" + ".join(res["key"]), s["matched"], max(s["rows_a"], s["rows_b"]),
                             (s["agreement_pct_text"] or {}).get("hu", "–"), s["agree"], s["cells_compared"],
                             s["disagree"], len(res["only_a"]), len(res["only_b"]))]
    for f, e in s["by_field"].items():
        extra = ""
        if e.get("kappa_text"):
            extra = " · " + e["kappa_text"]["hu"]
        elif e.get("icc_text") and e.get("disagree"):
            extra = " · " + e["icc_text"]["hu"]
        lines.append("  %-14s %3d/%-3d eltér%s" % (f, e["disagree"], e["compared"], extra))
    for d in res["disagreements"]:
        imp = d.get("impact") or {}
        lines.append("[%s] %s · %s: A=%s B=%s — %s%s" % (
            d["kind"], d["key"], d["field"], d["a"] if d["a"] is not None else "—",
            d["b"] if d["b"] is not None else "—", (d.get("hint") or {}).get("hu", ""),
            (" · hatás: %s" % imp["text"]["hu"]) if imp.get("text") else ""))
        if imp.get("pooled_text"):
            lines.append("    %s" % imp["pooled_text"]["hu"])
    for side in ("a", "b"):
        for k in res["only_" + side]:
            lines.append("[csak %s] %s" % (side.upper(), k))
    for w in res.get("warnings") or []:
        lines.append("Figyelem: %s" % w["hu"])
    return "\n".join(lines)


def _parse_tolerance(values):
    if not values:
        return None
    out = {}
    for v in values:
        name, sep, num = v.partition("=")
        try:
            if sep:
                out[name.strip()] = float(num.replace(",", "."))
            else:
                out["*"] = float(name.replace(",", "."))
        except ValueError:
            raise KettosError("Érvénytelen --tolerance: %s (alak: 0.01 vagy sd1=0.01)" % v) from None
    return out


def build_parser(prog="ma.py kettos"):
    import argparse
    ap = argparse.ArgumentParser(prog=prog, description="Kettős (független) adatkinyerés: összevetés, egyeztetés, "
                                                        "konszenzus-tábla, X009-állapot (terv 4.9).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("a", nargs="?", help="az A kinyerő CSV-je (vagy --project + --outcome)")
        p.add_argument("b", nargs="?", help="a B kinyerő CSV-je")
        p.add_argument("--project", help="projektmappa (a fájlok: 03_adatok/kettos/<kimenet>.A.csv / .B.csv)")
        p.add_argument("--outcome", help="kimenet-azonosító (pl. o1)")
        p.add_argument("--key", help="kulcsoszlop(ok) vesszővel (pl. study_id,arm); alap: study_id (+ arm) / study")
        p.add_argument("--json", action="store_true", help="JSON-kimenet")

    p = sub.add_parser("compare", help="a két tábla összevetése (szk.ma.compare-result/v1)")
    common(p)
    p.add_argument("--tolerance", action="append", help="tűrés: 0.01 (minden számmező) vagy sd1=0.01 (ismételhető)")
    p.add_argument("--measure", help="hatásmérték a hatás (impact) számításához (pl. SMD, RR)")
    p.add_argument("--spec", help="elemzési spec (szk.ma.analysis-spec/v1): az összesített becslésre gyakorolt hatás")
    p.add_argument("--categorical", help="kategóriásnak tekintett mezők vesszővel (κ)")
    p.add_argument("--ignore", help="kihagyott oszlopok vesszővel (alap: row_uid és a megjegyzés-oszlopok)")
    p = sub.add_parser("reconcile", help="döntések → konszenzus-fájl (és -CSV)")
    common(p)
    p.add_argument("--decisions", required=True, help="döntések JSON-fájlja: [{key, field, chosen, value?, reason}]")
    p.add_argument("--actor", default="user", help="ki döntött (monogram)")
    p.add_argument("--write-csv", action="store_true", help="a konszenzus-CSV kiírása (csak teljes egyeztetésnél)")
    p.add_argument("--out", help="(fájl-mód) a konszenzus-CSV útja")
    p.add_argument("--consensus-json", help="(fájl-mód) a konszenzus-fájl útja")
    p = sub.add_parser("report", help="egyetértési táblázat és Methods-szöveg")
    common(p)
    p.add_argument("--lang", choices=("hu", "en"), default="hu")
    p = sub.add_parser("status", help="a projekt kettős kinyeréseinek állapota (X009)")
    p.add_argument("project", help="projektmappa")
    p.add_argument("--json", action="store_true")
    return ap


def _tables_from_args(args):
    if args.project or args.outcome:
        if not (args.project and args.outcome):
            raise KettosError("A --project és az --outcome együtt adandó meg.")
        return None
    if not (args.a and args.b):
        raise KettosError("Add meg a két táblát (A.csv B.csv), vagy a --project és --outcome kapcsolót.")
    return args.a, args.b


def cli_main(argv=None, out=None):
    """'ma.py kettos …' → kilépési kód (0 rendben; 1: feloldatlan eltérés / hiba; 2: használati hiba)."""
    import sys
    out = out or sys.stdout
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help(out)
        return 2
    try:
        if args.cmd == "status":
            st = project_status(args.project)
            if args.json:
                out.write(json.dumps(st, ensure_ascii=False, indent=1) + "\n")
            else:
                if not st["outcomes"]:
                    out.write("Nincs kettős kinyerés a projektben (%s/<kimenet>.A.csv, .B.csv).\n" % DEFAULT_DIR)
                for o in st["outcomes"]:
                    if o["error"] or o["unresolved"] is None:
                        out.write("%s: %s\n" % (o["outcome"], o["error"] or "az egyik tábla hiányzik"))
                    else:
                        out.write("%s: %d/%d eldöntve, %d feloldatlan%s\n" % (
                            o["outcome"], o["decided"], o["total"], o["unresolved"],
                            (" (%d elavult)" % o["stale"]) if o["stale"] else ""))
            return 1 if st["unresolved_total"] else 0
        tables = _tables_from_args(args)
        key = [k for k in (args.key or "").split(",") if k.strip()] or None
        if args.cmd == "compare":
            spec = None
            if args.spec:
                from . import spec as S
                spec = S.load_spec(args.spec)
            kw = dict(key=key, tolerance=_parse_tolerance(args.tolerance), measure=args.measure, spec=spec)
            if tables is None:
                res = compare_project(args.project, args.outcome, **kw)
            else:
                res = compare(tables[0], tables[1], categorical=[c for c in (args.categorical or "").split(",")
                                                                 if c.strip()] or None,
                              ignore=[c for c in args.ignore.split(",") if c.strip()] if args.ignore else None, **kw)
            out.write((json.dumps(res, ensure_ascii=False, indent=1, allow_nan=False) if args.json
                       else _format_text(res)) + "\n")
            return 0
        if args.cmd == "report":
            res = compare_project(args.project, args.outcome, key=key) if tables is None else \
                compare(tables[0], tables[1], key=key)
            rep = agreement_report(res)
            out.write((json.dumps(rep, ensure_ascii=False, indent=1) if args.json else rep["text"][args.lang]) + "\n")
            return 0
        with open(args.decisions, encoding="utf-8-sig") as fh:
            decisions = json.load(fh)
        if isinstance(decisions, dict):
            decisions = decisions.get("decisions") or []
        if tables is None:
            res = reconcile_project(args.project, args.outcome, decisions, args.actor, key=key,
                                    write_csv=args.write_csv)
        else:
            res = reconcile(compare(tables[0], tables[1], key=key), decisions, actor=args.actor,
                            allow_unresolved=False)
            if args.consensus_json:
                _atomic_write(args.consensus_json, json_bytes(res["consensus"]))
            if args.out:
                if res["table"] is None:
                    raise KettosError("%d eltérés még feloldatlan: a konszenzus-CSV nem írható ki (X009)."
                                      % len(res["consensus"]["unresolved"]))
                _atomic_write(args.out, render_table(res["table"]))
        cons = res["consensus"]
        if args.json:
            out.write(json.dumps(cons, ensure_ascii=False, indent=1) + "\n")
        else:
            out.write("Döntések: %d · feloldatlan: %d · elavult: %d · árva (már nem eltérés): %d\n" % (
                cons["summary"]["decided"], cons["summary"]["unresolved"], cons["summary"]["stale"],
                cons["summary"]["orphans"]))
        return 1 if cons["unresolved"] else 0
    except (KettosError, ValueError, OSError) as exc:
        import sys as _s
        _s.stderr.write("Hiba: %s\n" % exc)
        return 1
