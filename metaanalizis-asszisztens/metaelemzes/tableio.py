# -*- coding: utf-8 -*-
"""CSV beolvasás magyar Excel-exportokhoz is: kódolás- és elválasztó-felismerés,
tizedesvessző, oszlopnév-szinonimák.
"""
import csv
import io
import re

# kanonikus oszlopnév -> elfogadott szinonimák (kisbetűs, szóköz/kötőjel nélkül)
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
    "yi": ["yi", "es", "effect", "hatas", "hatás", "te"],
    "vi": ["vi", "var", "variance", "variancia"],
    "sei": ["sei", "se", "sete", "hiba"],
    "year": ["year", "ev", "év", "evszam", "évszám"],
    "subgroup": ["subgroup", "alcsoport", "group", "csoport"],
    "rob": ["rob", "risk_of_bias", "torzitas", "torzítás", "rob2"],
    "estimated": ["estimated", "imputed", "becsult", "becsült"],
    "study_id": ["study_id", "trial", "trial_id", "vizsgalat_id"],
}

NUMERIC = {"m1", "sd1", "n1", "m2", "sd2", "n2", "e1", "e2", "x", "n", "r", "yi", "vi", "sei", "year"}
NA_TOKENS = {"", "na", "n/a", "nan", "-", "—", "nr", "hiányzik", "hianyzik", "null", "none"}

_num_dot = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_num_comma = re.compile(r"^[+-]?\d+,\d+([eE][+-]?\d+)?$")


def _norm(name):
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def canonical_columns(header):
    """Eredeti fejléc → kanonikus név (az ismeretleneket változatlanul hagyja)."""
    lookup = {}
    for canon, alist in ALIASES.items():
        for a in alist:
            lookup[_norm(a)] = canon
    mapping = {}
    used = set()
    for col in header:
        c = lookup.get(_norm(col))
        if c and c not in used:
            mapping[col] = c
            used.add(c)
        else:
            mapping[col] = col.strip()
    return mapping


def parse_number(text):
    """Szám értelmezése pont vagy vessző tizedesjellel; NA → None; egyébként ValueError."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    t = str(text).strip().replace(" ", "").replace(" ", "")
    if t.lower() in NA_TOKENS:
        return None
    if _num_dot.match(t):
        return float(t)
    if _num_comma.match(t):
        return float(t.replace(",", "."))
    raise ValueError("nem szám: %r" % text)


def _decode(raw):
    for enc in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError("ismeretlen karakterkódolás")


def read_table(path):
    """CSV/TSV → (sorok listája dict-ként, metaadat). A számoszlopok float-ok,
    a nem értelmezhető számok a 'parse_errors' listába kerülnek (nem dobjuk el csendben)."""
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
    rows_raw = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows_raw:
        raise ValueError("üres fájl: %s" % path)
    header = rows_raw[0]
    mapping = canonical_columns(header)
    rows = []
    parse_errors = []
    for line_no, r in enumerate(rows_raw[1:], start=2):
        row = {}
        for col, val in zip(header, r + [""] * (len(header) - len(r))):
            key = mapping[col]
            if key in NUMERIC:
                try:
                    row[key] = parse_number(val)
                except ValueError:
                    row[key] = None
                    parse_errors.append({"line": line_no, "column": col, "value": val})
            else:
                v = val.strip()
                if v.lower() in NA_TOKENS:
                    row[key] = None
                else:
                    try:
                        row[key] = parse_number(v)
                    except ValueError:
                        row[key] = v
        row["_line"] = line_no
        rows.append(row)
    meta = {"path": path, "encoding": enc, "delimiter": delim, "columns": header,
            "mapping": mapping, "parse_errors": parse_errors, "n_rows": len(rows)}
    return rows, meta


def write_csv(path, header, rows, delimiter=","):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(header)
        for r in rows:
            w.writerow([_fmt(v) for v in r])


def _fmt(v):
    if isinstance(v, float):
        return repr(v)
    return "" if v is None else v


def apply_filters(rows, exclude=None, include=None):
    """--exclude 'oszlop=érték' / --include 'oszlop=érték' szűrők (kis/nagybetű-független)."""
    def match(row, cond):
        col, _, val = cond.partition("=")
        rv = row.get(col.strip())
        return rv is not None and str(rv).strip().lower() == val.strip().lower()
    out = rows
    for cond in include or []:
        out = [r for r in out if match(r, cond)]
    for cond in exclude or []:
        out = [r for r in out if not match(r, cond)]
    return out
