# -*- coding: utf-8 -*-
"""Közös segédek a végpontokhoz: törzsmezők és lekérdezési paraméterek ellenőrzése, If-Match,
adattábla-út. A hibaüzenetek mezőnevet mondanak, értéket soha (T10)."""
from ..router import ApiError

TABLE_EXTENSIONS = (".csv", ".tsv", ".txt")
MAX_TEXT = 20000


def body_str(body, key, required=False, max_len=MAX_TEXT, allow_empty=False):
    """Szöveges törzsmező (vagy None). Üres szöveg: kötelezőnél 400, egyébként None."""
    v = body.get(key)
    if v is None:
        if required:
            raise ApiError("BAD_REQUEST", "Hiányzó mező: %s." % key)
        return None
    if not isinstance(v, str):
        raise ApiError("BAD_REQUEST", "A(z) %s mező szöveg legyen." % key)
    if len(v) > max_len:
        raise ApiError("BAD_REQUEST", "A(z) %s mező túl hosszú (legfeljebb %d karakter)." % (key, max_len))
    if "\x00" in v:
        raise ApiError("BAD_REQUEST", "A(z) %s mező NUL karaktert tartalmaz." % key)
    if not allow_empty and not v.strip():
        if required:
            raise ApiError("BAD_REQUEST", "A(z) %s mező nem lehet üres." % key)
        return None
    return v


def body_bool(body, key, default=False):
    v = body.get(key, default)
    if v is None:
        return default
    if not isinstance(v, bool):
        raise ApiError("BAD_REQUEST", "A(z) %s mező logikai érték (true/false) legyen." % key)
    return v


def body_int(body, key, required=False, minimum=None):
    v = body.get(key)
    if v is None:
        if required:
            raise ApiError("BAD_REQUEST", "Hiányzó mező: %s." % key)
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ApiError("BAD_REQUEST", "A(z) %s mező egész szám legyen." % key)
    if minimum is not None and v < minimum:
        raise ApiError("BAD_REQUEST", "A(z) %s mező legalább %d legyen." % (key, minimum))
    return v


def int_arg(req, name, default, lo, hi):
    """Egész lekérdezési paraméter a [lo, hi] tartományba vágva; nem szám → 400."""
    raw = req.arg(name)
    if raw is None or raw == "":
        return default
    if not raw.isdigit() or len(raw) > 9:
        raise ApiError("BAD_REQUEST", "A(z) %s paraméter nemnegatív egész szám legyen." % name)
    return max(lo, min(hi, int(raw)))


def num_arg(req, name, default, lo, hi):
    """Nemnegatív szám (pl. várakozási idő másodpercben) a [lo, hi] tartományba vágva."""
    raw = req.arg(name)
    if raw is None or raw == "":
        return default
    head, dot, tail = raw.partition(".")
    if not head.isdigit() or (dot and not tail.isdigit()) or len(raw) > 12:
        raise ApiError("BAD_REQUEST", "A(z) %s paraméter nemnegatív szám legyen." % name)
    return max(lo, min(hi, float(raw)))


def if_match(req):
    """Az If-Match fejléc (vagy None, ha nincs megadva)."""
    v = req.header("If-Match")
    return v if v else None


def dataset_rel(req, value):
    """Adattábla projekt-relatív útja kanonikus alakban; szabálytalan vagy kivezető út → 403,
    nem tábla-kiterjesztés → 403."""
    if value is None or value == "":
        raise ApiError("BAD_REQUEST", "Hiányzó paraméter: dataset (projekt-relatív CSV-út).")
    if not isinstance(value, str) or len(value) > 1024:
        raise ApiError("BAD_REQUEST", "A dataset projekt-relatív út legyen.")
    rel = req.app.store.rel(value)
    if not rel.lower().endswith(TABLE_EXTENSIONS):
        raise ApiError("FORBIDDEN", "Csak CSV/TSV adattábla nyitható meg (.csv, .tsv, .txt).")
    req.app.store.path(rel)          # symlinkkel kivezető út → 403
    return rel


def kb_refs_text(value):
    """KB-hivatkozások: lista vagy szöveg → vesszős szöveg (vagy None)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        if len(value) > 100:
            raise ApiError("BAD_REQUEST", "Túl sok KB-hivatkozás (legfeljebb 100).")
        parts = [x.strip() for x in value if x and x.strip()]
        return ",".join(parts) or None
    raise ApiError("BAD_REQUEST", "A kb_refs szöveg vagy szövegek listája legyen.")
