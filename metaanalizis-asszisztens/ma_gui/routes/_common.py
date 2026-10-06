# -*- coding: utf-8 -*-
"""Közös segédek a végpontokhoz: törzsmezők és lekérdezési paraméterek ellenőrzése, If-Match,
adattábla-út, szabad szöveg PHI-őre. A hibaüzenetek mezőnevet mondanak, értéket soha (T10)."""
from .. import privacy, security
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
    try:
        # a teljes T7-szabálykészlet is (COM0, LPT¹, CONIN$, rejtett név, ':' …), ne csak a tárolóé
        security.check_relpath(rel)
    except security.UnsafePath as exc:
        raise ApiError("FORBIDDEN", exc.message) from None
    req.app.store.path(rel)          # symlinkkel kivezető út → 403
    return rel


def phi_text_guard(app, fields):
    """Szabad szöveg (naplóbejegyzés, indoklás) PHI-őre (7.4, T10): a projekt.sqlite a vault
    mentésével GitHubra kerülhet. fields: [(mezőnév, szöveg|None)].

    TAJ-gyanú (vagy születési dátum) esetén B/C osztályú, vault által követett projektben (ha a
    projekt.sqlite nincs .gitignore-ban) 403 a mezők nevével (érték nélkül); egyébként a
    figyelmeztetések listája (üres, ha nincs találat)."""
    hits = []
    for name, text in fields:
        pats = privacy.text_patterns(text) if isinstance(text, str) else []
        if pats:
            hits.append({"field": name, "patterns": pats})
    if not hits:
        return []
    names = ", ".join(h["field"] for h in hits)
    if app.text_hold():
        raise ApiError("FORBIDDEN", "PHI-gyanús szöveg (mező: %s): a projektnapló (projekt.sqlite) a vault mentésével "
                                    "GitHubra kerülne. Írd le azonosító nélkül (például: „a _privat/… tábla 14. "
                                    "sora”), az adatot pedig tartsd a _privat/ alatt." % names,
                       {"phi_fields": hits})
    return ["PHI-gyanú a szövegben (mező: %s): a projektnaplóba azonosító (TAJ-szám, születési dátum) ne "
            "kerüljön; hivatkozz a _privat/ alatti adatra." % names]


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


# ---------------------------------------------------------------------------- közös segédek az új végpontokhoz
MSG_NO_LOG = ("Nincs projektnapló (projekt.sqlite) ebben a projektmappában. Hozd létre: Projekt → Új projekt "
              "(POST /api/project {action: 'init'}) vagy: python ma.py project init <mappa> --title …")


def journal_root(app):
    """A projektmappa (szöveg), ha van projektnapló (projekt.sqlite); különben 404 útmutatással."""
    if not (app.project_root / "projekt.sqlite").is_file():
        raise ApiError("NOT_FOUND", MSG_NO_LOG)
    return str(app.project_root)


def has_journal(app):
    return (app.project_root / "projekt.sqlite").is_file()


def accepts(fn, name):
    """A (motor-)függvény elfogad-e ilyen nevű kulcsszó-argumentumot (előre kompatibilis hívásokhoz)."""
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind == p.VAR_KEYWORD for p in params.values())


def client_seq(req, body=None):
    """A kérés client_seq-je: a törzs client_seq mezője, ennek hiányában az X-MA-Client-Seq fejléc; 0, ha
    egyik sincs. Nemnegatív egész (legfeljebb 15 jegy), különben 400."""
    v = (body or {}).get("client_seq") if isinstance(body, dict) else None
    if v is None:
        raw = req.header("X-MA-Client-Seq")
        if raw is None or raw == "":
            return 0
        if not raw.isascii() or not raw.isdigit() or len(raw) > 15:
            raise ApiError("BAD_REQUEST", "Az X-MA-Client-Seq fejléc nemnegatív egész szám legyen.")
        return int(raw)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > 10 ** 15:
        raise ApiError("BAD_REQUEST", "A client_seq nemnegatív egész szám legyen.")
    return v


MAX_META_NODES = 100000


def _meta_patterns(text):
    pats = list(privacy.text_patterns(text))
    if "email" in privacy.value_patterns(text, dates=False):
        pats.append("email")
    return pats


def metadata_phi(doc, limit=200):
    """Szabad szövegű projekt-metaadat (spec-címek, protokoll-hivatkozás, kizárási okok, vizsgálatcímkék) PHI-
    mintái — a szótárKULCSOKAT is nézve: TAJ-gyanús szám (CDV-vel), születési kontextusú teljes dátum,
    e-mail. A sima dátum (pl. „PROSPERO, 2026.10.04.”) itt nem gyanús (``privacy.text_patterns``).
    → [{path, pattern}] — értéket soha nem ad vissza; a gyanús kulcs helyett az útban csak a sorszáma áll."""
    found = []
    stack = [(doc, "")]
    nodes = 0
    while stack and len(found) < limit and nodes < MAX_META_NODES:
        node, path = stack.pop()
        nodes += 1
        if isinstance(node, dict):
            for i, (k, v) in enumerate(node.items()):
                key = str(k)
                kp = _meta_patterns(key) if key.strip() else []
                seg = key if not kp else "{%d. kulcs}" % (i + 1)
                here = "%s.%s" % (path, seg) if path else seg
                for pat in kp:
                    found.append({"kind": "key", "path": here, "column": here, "pattern": pat})
                stack.append((v, here))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append((v, "%s[%d]" % (path, i)))
        elif isinstance(node, str) and node.strip():
            for pat in _meta_patterns(node):
                found.append({"kind": "value", "path": path, "column": path, "pattern": pat})
    return found[:limit]


_PHI_LABELS = {"taj_cdv": "TAJ-számnak látszó szám", "full_date": "születési dátum", "email": "e-mail-cím"}


def phi_doc_guard(app, doc, rel, what):
    """Projekt-metaadat (spec, PRISMA, vizsgálat-térkép) PHI-szkennelése írás előtt (7.4): TAJ-gyanús
    szám, születési dátum vagy e-mail egy szöveges mezőben (vagy kulcsban) → 403 a mezők útjával, ÉRTÉK
    NÉLKÜL (T10); majd az írás-tartás (``app.can_write_meta``) → 403 az okkal."""
    findings = metadata_phi(doc)
    if findings:
        kinds = sorted({_PHI_LABELS.get(f["pattern"], f["pattern"]) for f in findings})
        where = sorted({f["path"] for f in findings})
        raise ApiError("FORBIDDEN", "Azonosítónak látszó adat a(z) %s szövegében (%s; mező: %s). Ide ne kerüljön "
                                    "beteg-azonosító (TAJ-szám, születési dátum, e-mail): írd át azonosító nélkül, az "
                                    "adatot pedig tartsd a _privat/ alatt." % (what, ", ".join(kinds),
                                                                                ", ".join(where[:10])),
                       {"phi": findings, "path": rel})
    ok, reason = app.can_write_meta(rel)
    if not ok:
        raise ApiError("FORBIDDEN", reason, {"path": rel})


def now_iso():
    """UTC időbélyeg 'YYYY-MM-DDTHH:MM:SSZ' alakban."""
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log_activity_or_warn(app, action, warnings, **kw):
    """Activity-sor; ha nem sikerült, figyelmeztetés a válaszba (a művelet megtörtént)."""
    rec = app.log_activity(action, **kw)
    if rec is None:
        warnings.append(app.ACTIVITY_WARNING)
    return rec
