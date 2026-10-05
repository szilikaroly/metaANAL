# -*- coding: utf-8 -*-
"""CSV beolvasás magyar Excel-exportokhoz is: kódolás- és elválasztó-felismerés,
tizedesvessző, ezres tagolás, oszlopnév-szinonimák; sorszűrők (--exclude/--include).
"""
import codecs
import csv
import io
import math
import re
import unicodedata
from collections import Counter

# kanonikus oszlopnév -> elfogadott szinonimák. Az összevetés kis/nagybetű-, ékezet-,
# szóköz-, kötőjel- és aláhúzásjel-független (lásd _norm). Ha több fejléc is ugyanarra a
# kanonikus névre illeszkedne, a listában előrébb álló szinonima nyer (pl. a 'Szerző'
# az 'ID' előtt), a többi oszlop az eredeti nevén marad.
ALIASES = {
    "study": ["study", "vizsgalat", "vizsgálat", "tanulmany", "tanulmány", "author", "szerzo", "szerző", "slab", "label", "id"],
    "m1": ["m1", "m1i", "mean1", "atlag1", "átlag1", "mean_t", "mean_exp", "meant"],
    "sd1": ["sd1", "sd1i", "sdt", "sd_t", "sd_exp", "szoras1", "szórás1"],
    "n1": ["n1", "n1i", "nt", "n_t", "n_exp", "total1"],
    "m2": ["m2", "m2i", "mean2", "atlag2", "átlag2", "mean_c", "mean_ctrl", "meanc"],
    "sd2": ["sd2", "sd2i", "sdc", "sd_c", "sd_ctrl", "szoras2", "szórás2"],
    "n2": ["n2", "n2i", "nc", "n_c", "n_ctrl", "total2"],
    "e1": ["e1", "events1", "event1", "ev1", "esemeny1", "esemény1", "ai", "tpos"],
    "e2": ["e2", "events2", "event2", "ev2", "esemeny2", "esemény2", "cpos"],
    "x": ["x", "xi", "events", "esemeny", "esemény", "cases"],
    "n": ["n", "ni", "total", "osszes", "összes"],
    "r": ["r", "ri", "cor", "korrelacio", "korreláció"],
    # párosított (előtte–utána) elrendezés: MC / SMCC
    "sd_diff": ["sd_diff", "sddi", "sd_change", "sd_valtozas", "sd_változás", "sd_kulonbseg", "sd_különbség",
                "szoras_valtozas", "szórás_változás"],
    "mdiff": ["mdiff", "mean_change", "change_mean", "atlagos_valtozas", "átlagos_változás",
              "valtozas_atlag", "változás_átlag"],
    "sum_d": ["sum_d", "sumd", "d_osszeg", "d_összeg"],
    "sum_sq_dev_d": ["sum_sq_dev_d", "ss_d", "ssd"],
    "yi": ["yi", "es", "effect", "hatas", "hatás", "te"],
    "vi": ["vi", "var", "variance", "variancia"],
    # a 'hiba' (magyarul 'error') szándékosan NEM álnév: a szabad szöveges megjegyzés-
    # oszlopot korábban standard hibaként olvastuk be
    "sei": ["sei", "se", "sete"],
    "year": ["year", "ev", "év", "evszam", "évszám"],
    "subgroup": ["subgroup", "alcsoport", "group", "csoport"],
    "rob": ["rob", "risk_of_bias", "torzitas", "torzítás", "rob2"],
    "estimated": ["estimated", "imputed", "becsult", "becsült"],
    "study_id": ["study_id", "trial", "trial_id", "vizsgalat_id"],
    "control_split": ["control_split", "shared_control_split", "kontroll_felosztva", "kontroll_megosztva"],
}
# metafor-stílusú 2×2 fejléc (ai, n1i, ci, n2i): a 'ci' csak akkor e2, ha 'ai' is van
# (különben egy általános "CI" oszlopot sajátítanánk ki)
_CONDITIONAL_ALIASES = {"ci": ("e2", "ai")}

NUMERIC = {"m1", "sd1", "n1", "m2", "sd2", "n2", "e1", "e2", "x", "n", "r", "yi", "vi", "sei", "year",
           "sd_diff", "mdiff", "sum_d", "sum_sq_dev_d"}
COUNT_COLUMNS = {"n1", "n2", "n", "e1", "e2", "x"}
DECIMAL_ONLY = {"r"}          # |r| <= 1: ezres tagolás nem értelmezhető
# azonosító/kategória oszlopok: mindig szövegként, pontosan úgy, ahogy a CSV-ben állnak
TEXT_COLUMNS = {"study", "study_id", "subgroup"}
NA_TOKENS = {"", "na", "n/a", "nan", "-", "—", "nr", "hiányzik", "hianyzik", "null", "none"}

_num_dot = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_num_comma = re.compile(r"^[+-]?\d+,\d+([eE][+-]?\d+)?$")
# ezres tagolás: az első csoport nem kezdődhet 0-val ("0.187" mindig tizedestört)
_grp_comma = re.compile(r"^[+-]?[1-9]\d{0,2}(,\d{3})+$")
_grp_dot = re.compile(r"^[+-]?[1-9]\d{0,2}(\.\d{3})+$")
_dec_dot = re.compile(r"^[+-]?\d*\.(\d+)$")
_dec_comma = re.compile(r"^[+-]?\d*,(\d+)$")
_year_suffix = re.compile(r"^(\d{4})\s*[a-z]$")


class NumText(float):
    """Számként értelmezett cella, amely megőrzi az eredeti szöveget.

    Számolni float-ként lehet vele (a moderátorok, a rendezés, a szűrők numerikusan
    kezelik), de str() az eredeti alakot adja ('2005', nem '2005.0'; '01' és '1' két
    különböző alcsoport-szint marad)."""
    __slots__ = ("text",)

    def __new__(cls, value, text=None):
        obj = float.__new__(cls, value)
        obj.text = str(text).strip() if text is not None else repr(float(value))
        return obj

    def __getnewargs__(self):
        return (float(self), self.text)

    def __str__(self):
        return self.text


def _strip_accents(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _norm(name):
    """Oszlopnév-összevetéshez: kisbetű, ékezet, szóköz, kötőjel és aláhúzásjel nélkül
    ('RoB 2', 'RoB-2', 'rob_2' → 'rob2'; 'Év' → 'ev')."""
    return re.sub(r"[\s\-_]+", "", _strip_accents(str(name).strip().lower()))


def _alias_lookup():
    lookup = {}
    for canon, alist in ALIASES.items():
        for prio, a in enumerate(alist):
            lookup.setdefault(_norm(a), (canon, prio))
    return lookup


def canonical_columns(header):
    """Eredeti fejléc → kanonikus név (az ismeretleneket változatlanul hagyja).

    Ha több oszlop illeszkedik ugyanarra a kanonikus névre, az ALIASES-listában előrébb
    álló szinonima nyer (azonos prioritásnál a fejlécben előbb álló oszlop)."""
    lookup = _alias_lookup()
    normed = [_norm(c) for c in header]
    cand = {}
    for pos, (col, nc) in enumerate(zip(header, normed)):
        hit = lookup.get(nc)
        if hit is None and nc in _CONDITIONAL_ALIASES:
            canon, needs = _CONDITIONAL_ALIASES[nc]
            if needs in normed:
                hit = (canon, len(ALIASES[canon]))
        if hit is not None:
            canon, prio = hit
            best = cand.get(canon)
            if best is None or (prio, pos) < best[0]:
                cand[canon] = ((prio, pos), col)
    winners = {col: canon for canon, (_, col) in cand.items()}
    # a vesztes oszlop kulcsa nem ütközhet a nyertessel (különben a sor-dictben felülírná):
    # 'M1,m1' → a második 'm1.1' kulcsot kap
    taken = set(winners.values())
    reserved = {c.strip() for c in header}
    mapping = {}
    for col in header:
        key = winners.get(col)
        if key is None:
            key = col.strip()
            if key and key in taken:
                n = 1
                while "%s.%d" % (key, n) in taken or "%s.%d" % (key, n) in reserved:
                    n += 1
                key = "%s.%d" % (key, n)
            taken.add(key)
        mapping[col] = key
    return mapping


def _unique_header(header):
    """Azonos fejlécnevek: a későbbiek '.1', '.2' utótagot kapnak (mint a pandas), hogy minden
    oszlop külön kulcs legyen, és az első nyerjen."""
    reserved = {h.strip() for h in header}
    seen = Counter()
    out = []
    for h in header:
        s = h.strip()
        if s and seen[s]:
            n = seen[s]
            while "%s.%d" % (s, n) in reserved:
                n += 1
            seen[s] = n + 1
            h = "%s.%d" % (s, n)
            reserved.add(h)
        else:
            seen[s] += 1
        out.append(h)
    return out


def _duplicate_columns(orig, header, mapping):
    """Az ugyanarra a kanonikus névre illeszkedő, de figyelmen kívül hagyott oszlopok (számoszlopnál,
    vagy ha a név azonos): [{column, position, key, canonical, used, used_position}]."""
    lookup = _alias_lookup()
    pos_of = {}
    for pos, col in enumerate(header):
        if mapping[col] in ALIASES:
            pos_of.setdefault(mapping[col], pos)
    out = []
    for pos, (o, col) in enumerate(zip(orig, header)):
        hit = lookup.get(_norm(o))
        canon = hit[0] if hit else None
        wpos = pos_of.get(canon)
        if wpos is None or wpos == pos:
            continue
        if canon in NUMERIC or _norm(o) == _norm(orig[wpos]):
            out.append({"column": o.strip(), "position": pos + 1, "key": mapping[col], "canonical": canon,
                        "used": orig[wpos].strip(), "used_position": wpos + 1})
    return out


class _NotFinite(ValueError):
    pass


def parse_number(text):
    """Szám értelmezése pont vagy vessző tizedesjellel; NA → None; egyébként ValueError."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    t = str(text).strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if t.lower() in NA_TOKENS:
        return None
    if _num_dot.match(t):
        v = float(t)
    elif _num_comma.match(t):
        v = float(t.replace(",", "."))
    else:
        raise ValueError("nem szám: %r" % text)
    if not math.isfinite(v):
        # '1e400' → inf: ugyanúgy hiba, mint a szó szerinti 'inf'
        raise _NotFinite("nem véges szám (túlcsordul)")
    return v


def _clean(text):
    return str(text).strip().replace(" ", "").replace(" ", "").replace(" ", "")


def _decimal_mark(cells):
    """A fájl tizedesjele a nem 3 tizedesjegyű tört számok alapján ('.', ',' vagy None)."""
    dot = comma = 0
    for c in cells:
        t = _clean(c)
        m = _dec_dot.match(t)
        if m and len(m.group(1)) != 3:
            dot += 1
        m = _dec_comma.match(t)
        if m and len(m.group(1)) != 3:
            comma += 1
    if dot and not comma:
        return "."
    if comma and not dot:
        return ","
    return None


class _Ambiguous(ValueError):
    pass


def _parse_grouped(t, key, mark, delim):
    """Ezres tagolásnak is olvasható szám ('2,000', '13.598') értelmezése.

    Visszaad: (érték, megjegyzés vagy None); kétértelmű esetben _Ambiguous."""
    sep = "," if _grp_comma.match(t) else "."
    thousands = float(t.replace(sep, ""))
    if t.count(sep) >= 2:
        return thousands, None                     # 1,234,567: csak ezres tagolás lehet
    decimal = float(t.replace(",", "."))
    if mark == sep:
        return decimal, None                       # a fájl tizedesjele ez
    note = "ezres tagolásként értelmezve: %s → %g" % (t, thousands)
    if mark is not None:
        return thousands, note                     # a fájl a másik jelet használja tizedesjelnek
    if key in COUNT_COLUMNS:
        return thousands, note                     # darabszám nem lehet 2,000 = 2.0 / 1,523
    if (sep == "," and delim == ";") or (sep == "." and delim != ";"):
        return decimal, None                       # a tagolóhoz illő szokásos tizedesjel
    raise _Ambiguous("kétértelmű szám (ezres tagoló vagy tizedesjel?): %s" % t)


def _parse_numeric_cell(val, key, mark, delim):
    """NUMERIC oszlop cellája → (érték, megjegyzés). Hibánál ValueError."""
    t = _clean(val)
    if t.lower() in NA_TOKENS:
        return None, None
    if key not in DECIMAL_ONLY and (_grp_comma.match(t) or _grp_dot.match(t)):
        return _parse_grouped(t, key, mark, delim)
    if key == "year":
        m = _year_suffix.match(t.lower())
        if m:   # '2001a' / '2001b' (szerző–év megkülönböztetés): az évszám 2001
            return NumText(float(m.group(1)), val), None
        v = parse_number(t)
        return (NumText(v, val) if v is not None else None), None
    return parse_number(t), None


def _decode(raw):
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16"), "utf-16"          # Excel „Unicode szöveg” export
    head = raw[:4096]
    if b"\x00" in head:
        # BOM nélküli UTF-16: a NUL bájtok helyéből a bájtsorrend
        odd, even = head[1::2].count(0), head[0::2].count(0)
        enc = "utf-16-le" if odd >= even else "utf-16-be"
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            raise ValueError("a fájl NUL bájtokat tartalmaz és nem értelmezhető UTF-16-ként; "
                             "mentsd „CSV UTF-8” formátumban")
    for enc in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError("ismeretlen karakterkódolás")


_C0_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def read_table(path):
    """CSV/TSV → (sorok listája dict-ként, metaadat).

    - A számoszlopok float-ok; a nem értelmezhető számok a 'parse_errors' listába kerülnek
      (nem dobjuk el csendben). A sorszám a fájl fizikai sora (üres sorok és több soros
      idézőjeles mezők után is).
    - A fejlécnél több (nem üres) cellát tartalmazó sor 'parse_errors'-be kerül
      ("kind": "ragged"), és a számértékei None-ok lesznek: elcsúszott értékeket nem
      összesítünk (tipikus ok: idézőjel nélküli tizedesvessző vesszővel tagolt fájlban).
    - Az ezres tagolásként olvasott számok a 'ambiguous' listába kerülnek.
    - Az azonosító oszlopok (study, study_id, subgroup) szövegek maradnak; a többi nem
      kanonikus oszlop számnak látszó cellái NumText-ek (float, eredeti szöveggel).
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    text, enc = _decode(raw)
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delim = dialect.delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in (";", "\t", ",")}
        delim = max(counts, key=counts.get)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    records = []
    prev = 0
    for r in reader:
        start, prev = prev + 1, reader.line_num   # a rekord első fizikai sora
        if any(cell.strip() for cell in r):
            records.append((start, r))
    if not records:
        raise ValueError("üres fájl: %s" % path)
    orig_header = records[0][1]
    header = _unique_header(orig_header)
    mapping = canonical_columns(header)
    numeric_cells = [val for _, r in records[1:] for col, val in zip(header, r) if mapping[col] in NUMERIC]
    mark = _decimal_mark(numeric_cells)
    rows = []
    parse_errors = []
    ambiguous = []
    for line_no, r in records[1:]:
        row = {}
        extra = r[len(header):]
        ragged = any(c.strip() for c in extra)
        if ragged:
            parse_errors.append({
                "line": line_no, "column": None, "kind": "ragged", "value": delim.join(r),
                "note": "%d cella, a fejlécben %d — elcsúszott sor (idézőjel nélküli tizedesvessző "
                        "vesszővel tagolt fájlban?)" % (len(r), len(header))})
        for col, val in zip(header, r + [""] * (len(header) - len(r))):
            key = mapping[col]
            if key in NUMERIC:
                try:
                    value, note = _parse_numeric_cell(val, key, mark, delim)
                    row[key] = value
                    if note:
                        ambiguous.append({"line": line_no, "column": col, "value": val, "note": note})
                except ValueError as exc:
                    row[key] = None
                    pe = {"line": line_no, "column": col, "value": val}
                    if isinstance(exc, (_Ambiguous, _NotFinite)):
                        pe["note"] = str(exc)
                    parse_errors.append(pe)
            else:
                # XML-ben tiltott C0 vezérlőkarakterek (pl. PDF-ből másolt címkében): szóköz, mint az SVG-ben
                v = _C0_CONTROL.sub(" ", val).strip()
                if v.lower() in NA_TOKENS:
                    row[key] = None
                elif key in TEXT_COLUMNS:
                    row[key] = v
                else:
                    try:
                        num = parse_number(v)
                        row[key] = NumText(num, v) if num is not None else None
                    except ValueError:
                        row[key] = v
        if ragged:
            for key in list(row):
                if key in NUMERIC:
                    row[key] = None
        row["_line"] = line_no
        rows.append(row)
    meta = {"path": path, "encoding": enc, "delimiter": delim, "decimal_mark": mark, "columns": header,
            "mapping": mapping, "parse_errors": parse_errors, "ambiguous": ambiguous, "n_rows": len(rows),
            "duplicate_columns": _duplicate_columns(orig_header, header, mapping)}
    keys = set(mapping.values())
    if "study" not in keys and "study_id" in keys:
        _labels_from_study_id(rows)
        meta["label_column"] = next(c.strip() for c in header if mapping[c] == "study_id")
    return rows, meta


def _labels_from_study_id(rows):
    """Nincs címkeoszlop, de van study_id ('Study ID', 'Trial'): a címke a study_id; ha több sor
    osztozik rajta (több karú vizsgálat), sorszám-utótaggal egyedivé téve ('Smith 2001 (2)')."""
    cnt = Counter(r.get("study_id") for r in rows if r.get("study_id"))
    seen = Counter()
    for r in rows:
        sid = r.get("study_id")
        if not sid:
            continue
        if cnt[sid] > 1:
            seen[sid] += 1
            r["study"] = "%s (%d)" % (sid, seen[sid])
        else:
            r["study"] = sid


def write_csv(path, header, rows, delimiter=","):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(header)
        for r in rows:
            w.writerow([_fmt(v) for v in r])


def _fmt(v):
    if isinstance(v, float):
        return repr(float(v))
    return "" if v is None else v


# ------------------------------------------------------------------ oszlopnevek
def _row_keys(meta, rows=None):
    if meta and meta.get("mapping") is not None:
        keys = list(dict.fromkeys(meta["mapping"].values()))
        if meta.get("label_column") and "study" not in keys:
            keys.append("study")         # a study_id-ből képzett címke (read_table) is szűrhető
        return keys
    keys = []
    for r in rows or []:
        for k in r:
            if not str(k).startswith("_") and k not in keys:
                keys.append(k)
    return keys


def _available(meta, rows=None):
    if meta and meta.get("mapping") is not None:
        parts = []
        for orig, key in meta["mapping"].items():
            parts.append(orig.strip() if orig.strip() == key else "%s (= %s)" % (orig.strip(), key))
        if meta.get("label_column") and "study" not in meta["mapping"].values():
            parts.append("study (= a(z) %s oszlopból képzett címke)" % meta["label_column"])
        return ", ".join(parts)
    return ", ".join(_row_keys(meta, rows))


def resolve_column(name, meta, rows=None):
    """A felhasználó által megadott oszlopnév (eredeti CSV-fejléc, kanonikus név vagy
    szinonima; kis/nagybetű- és ékezetfüggetlen) → a sor-dict kulcsa.

    Ismeretlen oszlopnál ValueError, az elérhető oszlopok felsorolásával. meta nélkül
    (és sorok nélkül) csak a szinonimákat oldja fel, létezést nem ellenőriz."""
    if name is None:
        raise ValueError("hiányzó oszlopnév")
    n = str(name).strip()
    keys = _row_keys(meta, rows)
    mapping = (meta or {}).get("mapping") or {}
    if n in keys:
        return n
    for orig, key in mapping.items():
        if orig.strip() == n:
            return key
    nn = _norm(n)
    for orig, key in mapping.items():
        if _norm(orig) == nn:
            return key
    for key in keys:
        if _norm(key) == nn:
            return key
    hit = _alias_lookup().get(nn)
    canon = hit[0] if hit else None
    if not keys and not mapping:
        return canon or n
    if canon in keys:
        return canon
    raise ValueError("ismeretlen oszlop: %r (elérhető oszlopok: %s)" % (n, _available(meta, rows)))


# ------------------------------------------------------------------- szűrők
_YES = {"igen", "yes", "y", "i", "true", "1"}
_NO = {"nem", "no", "n", "false", "0"}
_ROB = {
    "high": {"high", "magas", "high risk", "magas kockazat", "serious", "sulyos", "critical", "kritikus"},
    "low": {"low", "alacsony", "low risk", "alacsony kockazat"},
    "some": {"some", "some concerns", "unclear", "unclear risk", "kozepes", "moderate", "nehany aggaly",
             "nem egyertelmu", "bizonytalan"},
    "no_info": {"no information", "no info", "nincs informacio", "nincs adat"},
}
# a RoB 2 / ROBINS-I hivatalos alakja: 'High risk of bias', 'Serious risk of bias', 'Magas torzítási kockázat'
_ROB_SUFFIX = re.compile(r" (risk( of bias)?|(torzitasi )?kockazat(u)?)$")
_ROB_COLUMN_HINTS = ("bias", "torzitas", "kockazat")


def _token(v):
    s = _strip_accents(str(v).strip().lower())
    return re.sub(r"\s+", " ", s)


def _is_rob_column(key):
    nk = _norm(key)
    return nk.startswith("rob") or any(h in nk for h in _ROB_COLUMN_HINTS)


def _canon_value(v, rob):
    """Összevetési alak: igen/nem-szinonimák → 'yes'/'no', RoB-szinonimák → high/low/some."""
    t = _token(v)
    try:
        num = parse_number(t)
    except ValueError:
        num = None
    if num is not None:
        t = "1" if num == 1 else ("0" if num == 0 else t)
    if rob:
        rt = _ROB_SUFFIX.sub("", re.sub(r"[\s_\-]+", " ", t).strip())
        for canon, group in _ROB.items():
            if t in group or rt in group:
                return canon
    if t in _YES:
        return "yes"
    if t in _NO:
        return "no"
    return t


# az alcsoport-szintekhez: csak ugyanannak a kategóriának az írásmódjai esnek egybe (a szűrők high/some
# csoportjai itt nem: a ROBINS-I 'serious' és 'critical', a RoB 1 'unclear' külön szint marad)
_ROB_LEVEL = {
    "low": {"low", "alacsony", "low risk", "alacsony kockazat"},
    "some concerns": {"some", "some concerns", "nehany aggaly"},
    "unclear": {"unclear", "unclear risk", "nem egyertelmu", "bizonytalan"},
    "moderate": {"moderate", "kozepes"},
    "high": {"high", "magas", "high risk", "magas kockazat"},
    "serious": {"serious", "sulyos"},
    "critical": {"critical", "kritikus"},
    "no information": {"no information", "no info", "nincs informacio", "nincs adat"},
}


def rob_levels(values):
    """RoB-oszlop alcsoport-szintjei: ugyanannak a kategóriának eltérő írásmódjai ('high', 'High risk of
    bias', 'Magas torzítási kockázat') egy szint, az elsőként előforduló alakkal; a fel nem ismert érték
    változatlan. Visszaad: (szintek, {írásmód: a szint alakja} az összevont írásmódokra)."""
    first, merged, out = {}, {}, []
    for v in values:
        t = _token(v)
        rt = _ROB_SUFFIX.sub("", re.sub(r"[\s_\-]+", " ", t).strip())
        lv = next((k for k, grp in _ROB_LEVEL.items() if t in grp or rt in grp), None)
        if lv is None:
            out.append(v)
            continue
        lab = first.setdefault(lv, v)
        if lab != v:
            merged[v] = lab
        out.append(lab)
    return out, merged


def is_rob_column(key):
    return _is_rob_column(key)


def rob_category(v):
    """RoB-cella → 'high' | 'low' | 'some' | 'no_info'; üres → None; fel nem ismert → ''."""
    if v is None or not str(v).strip():
        return None
    c = _canon_value(v, True)
    return c if c in _ROB else ""


def yes_no(v):
    """igen/nem-cella → 'yes' | 'no'; üres → None; fel nem ismert → ''."""
    if v is None or not str(v).strip():
        return None
    c = _canon_value(v, False)
    return c if c in ("yes", "no") else ""


def _number(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return parse_number(v)
    except ValueError:
        return None


def values_equal(cell, value, column=None):
    """Szűrő-összevetés normalizálás után: számok numerikusan ('1' = 1.0, '2005' = 2005.0),
    igen/yes/y/i/true/1 egymással, nem/no/n/false/0 egymással; RoB-oszlopban high/magas/
    'high risk'/serious/súlyos/critical = high, low/alacsony = low, 'some concerns'/unclear/közepes/
    moderate = some, 'no information' = no_info; a ' risk of bias' / ' (torzítási) kockázat' utótag
    nem számít ('High risk of bias' = high). Egyébként kis/nagybetű- és ékezetfüggetlen szövegegyezés."""
    if cell is None:
        return False
    a, b = _number(cell), _number(value)
    if a is not None and b is not None:
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    rob = column is not None and _is_rob_column(column)
    return _canon_value(cell, rob) == _canon_value(value, rob)


def _level_text(v):
    """Cella szintként (a pipeline alcsoport-szintjével azonosan): NumText → eredeti szöveg, egész float → '2'."""
    if isinstance(v, NumText):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _code_level(rows, key, val):
    """Ha az oszlopban a szűrőértékkel számként egyenlő cellák szövege eltér ('01' és '1', '1.0' és '1'),
    és a szűrőérték szó szerint az egyik: ez a szint (szövegként); egyébként None (numerikus összevetés)."""
    num = _number(val)
    if num is None:
        return None
    texts = set()
    for v in (r.get(key) for r in rows):
        x = _number(v) if v is not None else None
        if x is not None and abs(x - num) <= 1e-9 * max(1.0, abs(num), abs(x)):
            texts.add(_level_text(v))
    return val if len(texts) > 1 and val in texts else None


def _row_label(row, i, label_col="study"):
    v = row.get(label_col)
    if v is None or str(v).strip() == "":
        return "#%d" % (i + 1)
    return str(v).strip()


def apply_filters(rows, exclude=None, include=None, meta=None, report=None):
    """--exclude 'oszlop=érték' / --include 'oszlop=érték' szűrők.

    Az oszlopnév a resolve_column-nal oldódik fel (eredeti fejléc, kanonikus név vagy
    szinonima); ismeretlen oszlop → ValueError. Az értékek összevetése: values_equal.
    Ha az oszlop számként egyenlő, de szövegként eltérő kódokat tartalmaz ('01' és '1'), és az érték szó
    szerint az egyik, csak az a szint egyezik (mint az alcsoport-elemzésben, ahol ezek külön szintek).
    Ha `report` lista, szűrőnként hozzáfűzi: {"filter", "mode", "removed", "removed_labels"}.
    Sorrend: előbb az include-ok, aztán az exclude-ok."""
    labels = {id(r): _row_label(r, i) for i, r in enumerate(rows)}
    out = list(rows)
    for mode, conds in (("include", include or []), ("exclude", exclude or [])):
        for cond in conds:
            col, sep, val = str(cond).partition("=")
            if not sep or not col.strip():
                raise ValueError("érvénytelen szűrő: %r (alak: oszlop=érték)" % cond)
            key = resolve_column(col, meta, rows)
            val = val.strip()
            code = _code_level(rows, key, val)
            if code is not None:
                # számként egyenlő, de szövegként eltérő szintek ('01' és '1'): mint az alcsoport-elemzésben,
                # a szó szerint egyező szint számít
                hit = [r.get(key) is not None and _level_text(r.get(key)) == code for r in out]
            else:
                hit = [values_equal(r.get(key), val, key) for r in out]
            keep = [r for r, h in zip(out, hit) if (h if mode == "include" else not h)]
            removed = [r for r, h in zip(out, hit) if not (h if mode == "include" else not h)]
            if isinstance(report, list):
                report.append({"filter": "%s=%s" % (col.strip(), val), "column": key, "mode": mode,
                               "removed": len(removed), "removed_labels": [labels[id(r)] for r in removed]})
            out = keep
    return out
