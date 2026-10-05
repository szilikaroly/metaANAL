# -*- coding: utf-8 -*-
"""A munkapad biztonsági rétege (terv 7.1, T1–T11; 2.2; 3.4; 4.2).

- Egyszer használható, 60 s-os indítókód → munkamenet-token (``secrets``, ``hmac.compare_digest``).
- Kérésszűrés: Host (DNS-rebinding), Origin, ``Sec-Fetch-Site``, metódus (``OPTIONS`` → 405),
  ``X-MA-Token`` minden ``/api/*`` kérésen, ``Content-Type: application/json``, törzsméret.
- CSP-nonce és a kötelező válaszfejlécek (HTML / API / fájl / SVG); CORS-fejléc soha.
- Aláírt, rövid életű fájl-URL: ``/f/<doc>/<lejárat>/<hmac>``, HMAC-SHA256 a ``doc|lejárat|út``
  hármasra, a folyamatonkénti titokból származtatott kulccsal.
- Útvonal-biztonság Windows-higiéniával (abszolút út, ``..``, meghajtóbetű, ADS, fenntartott
  nevek, gyökéren kívülre mutató symlink) és kiterjesztés-allowlist.
- Korlátozott JSON-beolvasás (méret, mélység, ``__proto__``/``constructor``/``prototype`` kulcs,
  NaN/Infinity, ismétlődő kulcs) és táblaméret-korlátok; egységes hiba-boríték.

Csak stdlib, statisztika nincs. Hibaüzenetben soha nincs a kérésből származó érték (T10):
se cellaérték, se fejlécérték, se útvonal. A modul nem naplóz.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path, PurePath
from urllib.parse import quote, unquote

# --- korlátok (T4, T5, T11) ---
LAUNCH_CODE_TTL = 60                  # s; az indítókód élettartama
FILE_URL_TTL = 600                    # s; aláírt fájl-URL alapértelmezett élettartama
FILE_URL_MAX_TTL = 3600
MAX_PENDING_LAUNCH_CODES = 8
MAX_SESSION_TOKENS = 16
MAX_TABLE_BODY_BYTES = 4 * 1024 * 1024
MAX_DOC_BODY_BYTES = 1024 * 1024      # értékelés / GRADE-dokumentum (TRIPOD+AI AI-vázlat 52 tétel indoklással)
MAX_BODY_BYTES = 64 * 1024
MAX_JSON_DEPTH = 32
MAX_ROWS = 5000
MAX_COLUMNS = 200
MAX_NUMBER_LITERAL = 64               # karakter; a JSON-számliterál hossza (óriás int = CPU-kimerítés)
MAX_REL_PATH_LEN = 1024
MAX_SEGMENT_LEN = 255
ANALYSIS_TIMEOUT_S = 30
IDLE_SHUTDOWN_S = 4 * 3600

TOKEN_HEADER = "X-MA-Token"
SESSION_ROUTE = ("POST", "/api/session")
API_METHODS = ("GET", "POST", "PUT")
# a teljes táblát vivő útvonalak 4 MB-ot kapnak, minden más 64 KB-ot
TABLE_BODY_ROUTES = frozenset([
    "/api/table", "/api/table/import", "/api/validate", "/api/compare", "/api/reconcile",
    "/api/analyze", "/api/provenance",
])
# a teljes értékelés- és GRADE-dokumentumot vivő útvonalak 1 MB-ot kapnak (v1): egy TRIPOD+AI AI-vázlat mind az 52
# tételhez egyszerű nyelvű indoklást és idézetet hordoz (6. döntés), ami a 64 KB-ot meghaladhatja
DOC_BODY_PREFIXES = ("/api/appraisals/", "/api/grade/")

# 3.4 hibakód-táblázat (+ METHOD_NOT_ALLOWED a T1 OPTIONS → 405 szabályhoz)
ERROR_CODES = {
    "BAD_REQUEST": 400,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "CONFLICT": 409,
    "GATE_BLOCKED": 409,
    "PAYLOAD_TOO_LARGE": 413,
    "UNSUPPORTED_MEDIA": 415,
    "VALIDATION": 422,
    "LOCKED": 423,
    "CAPABILITY_MISSING": 424,
    "INTERNAL": 500,
    "PLUGIN_FAILED": 502,
    "TIMEOUT": 504,
}

# --- kötelező válaszfejlécek (7.1) ---
MANDATORY_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    ("X-Frame-Options", "DENY"),
)
HTML_CSP_TEMPLATE = ("default-src 'none'; script-src 'nonce-{n}'; style-src 'nonce-{n}'; "
                     "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; "
                     "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
API_CSP = "default-src 'none'; frame-ancestors 'none'; sandbox"
# PDF/kép új lapon: a 'sandbox' és a default-src 'none' a böngészők PDF-nézőjét letiltaná
FILE_CSP = "frame-ancestors 'none'"
SVG_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'"
HEADER_KINDS = ("api", "html", "file", "svg")
_NONCE_RE = re.compile(r"^[A-Za-z0-9+/_-]{16,128}={0,2}$")

# --- fájlok (T5, T7) ---
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json",
}
DOC_EXTENSIONS = frozenset([".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff", ".txt"])
RUN_ARTIFACT_EXTENSIONS = frozenset([".svg", ".png", ".pdf", ".tif", ".tiff", ".json", ".md", ".csv", ".txt"])
# saját originen futtatható tartalom: allowlistben sem szolgálható ki
NEVER_SERVE_EXTENSIONS = frozenset([".html", ".htm", ".xhtml", ".xht", ".xml", ".xsl", ".js", ".mjs",
                                    ".svgz", ".swf", ".exe", ".bat", ".cmd", ".com", ".ps1", ".sh"])
WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + ["COM" + c for c in "0123456789¹²³"]
    + ["LPT" + c for c in "0123456789¹²³"])
_FORBIDDEN_PATH_CHARS = frozenset('<>:"|?*\\')
# az atomikus írás ideiglenes fájljainak előtagja a projektmappában (a kezelt .gitignore-blokk
# '.ma-tmp-*' mintája fedi; az indításkori takarítás csak ilyen nevet töröl)
TMP_PREFIX = ".ma-tmp-"

FORBIDDEN_JSON_KEYS = frozenset(["__proto__", "constructor", "prototype"])

_FILE_URL_RE = re.compile(r"^/f/([A-Za-z0-9_.~%-]{1,512})/([0-9]{1,12})/([A-Za-z0-9_-]{43})$")
_INF = float("inf")

_MSG_HOST = ("Érvénytelen Host fejléc: a munkapad csak a 127.0.0.1 vagy a localhost címen, "
             "a saját portján érhető el.")
_MSG_ORIGIN = "Idegen eredetű (Origin) kérés elutasítva."
_MSG_FETCH_SITE = "Más webhelyről indított kérés elutasítva (Sec-Fetch-Site)."
_MSG_METHOD = "A kérés metódusa itt nem engedélyezett."
_MSG_TOKEN = "Hiányzó vagy érvénytelen munkamenet-token."
_MSG_MEDIA = "A kérés törzse csak application/json (UTF-8) lehet."
_MSG_PATH = "Érvénytelen kérés-útvonal."
_MSG_DUP = "Ismétlődő biztonsági fejléc."
_MSG_LENGTH = "Érvénytelen Content-Length fejléc."
_MSG_CHUNKED = "A darabolt (chunked) átvitel nem támogatott."


class SecurityError(ValueError):
    """Elutasított kérés vagy bemenet; ``http``/``code`` a 3.4 táblázat szerint."""

    def __init__(self, code, message, details=None):
        if code not in ERROR_CODES:
            raise ValueError("ismeretlen hibakód: %s" % code)
        super().__init__(message)
        self.code = code
        self.http = ERROR_CODES[code]
        self.message = message
        self.details = details

    def as_tuple(self):
        return (self.http, self.code, self.message)

    def envelope(self):
        return error_envelope(self.code, self.message, self.details)


class UnsafePath(SecurityError):
    """Útvonal-bejárás, tiltott név vagy kiterjesztés (T7)."""

    def __init__(self, message, code="FORBIDDEN"):
        super().__init__(code, message)


def _reject(code, message):
    return (ERROR_CODES[code], code, message)


def _ascii_bytes(value, max_len):
    if not isinstance(value, str) or not value or len(value) > max_len:
        return None
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        return None


class _DuplicateHeader(Exception):
    pass


def _header_values(headers, name):
    """Fejlécértékek kis/nagybetű-függetlenül: http.client.HTTPMessage és sima dict is."""
    if headers is None:
        return []
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        return [str(v) for v in (get_all(name) or [])]
    lname = name.lower()
    out = []
    for k, v in headers.items():
        if isinstance(k, str) and k.lower() == lname:
            if isinstance(v, (list, tuple)):
                out.extend(str(x) for x in v)
            else:
                out.append(str(v))
    return out


def _single_header(headers, name):
    vals = _header_values(headers, name)
    if len(vals) > 1:
        raise _DuplicateHeader(name)
    return vals[0].strip() if vals else None


def _split_target(raw):
    """Kérés-cél → útvonal (query és fragmentum nélkül), vagy None, ha gyanús."""
    if not isinstance(raw, str) or not raw.startswith("/") or not raw.isascii():
        return None
    path = raw.split("#", 1)[0].split("?", 1)[0]
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in path) or "\\" in path:
        return None
    segs = path.split("/")[1:]
    if any(s == "" for s in segs[:-1]):
        return None
    for s in segs:
        if s in (".", "..") or unquote(s) in (".", ".."):
            return None
    # az útvonal-osztályt (api / f / oldal) eldöntő első szakasz nem lehet kódolt
    if "%" in segs[0]:
        return None
    return path


def route_class(path):
    """'api' (/api/*), 'file' (/f/*) vagy 'page' (minden más, pl. GET /)."""
    p = path.split("?", 1)[0]
    if p == "/api" or p.startswith("/api/"):
        return "api"
    if p == "/f" or p.startswith("/f/"):
        return "file"
    return "page"


def body_limit(path):
    """A törzs megengedett legnagyobb mérete bájtban (T11)."""
    p = path.split("?", 1)[0]
    if p in TABLE_BODY_ROUTES:
        return MAX_TABLE_BODY_BYTES
    if p.startswith(DOC_BODY_PREFIXES):
        return MAX_DOC_BODY_BYTES
    return MAX_BODY_BYTES


_FETCH_SITE_ALLOWED = {
    "api": frozenset([None, "same-origin"]),
    "file": frozenset([None, "none", "same-origin"]),
    "page": frozenset([None, "none", "same-origin", "same-site"]),
}


def _media_type_ok(value):
    if not value:
        return False
    parts = [p.strip() for p in value.split(";")]
    if parts[0].lower() != "application/json":
        return False
    for param in parts[1:]:
        if not param:
            continue
        key, _, val = param.partition("=")
        if key.strip().lower() != "charset" or val.strip().strip('"').lower() not in ("utf-8", "utf8"):
            return False
    return True


# ---------------------------------------------------------------------------
# munkamenet: indítókód, token, kulcsszármaztatás, aláírt fájl-URL
# ---------------------------------------------------------------------------

class SecurityManager:
    """Folyamatonkénti biztonsági állapot: titok, függő indítókódok, munkamenet-tokenek.

    A ``port`` a kötés után is beállítható (``--port 0``). A ``clock`` a tesztekhez cserélhető
    (másodpercben mért falióra). Szálbiztos: a ThreadingHTTPServer szálai közösen használják.
    """

    def __init__(self, port=None, clock=None):
        self.port = port
        self._clock = clock or time.time
        self._secret = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._launch_codes = {}       # kód -> lejárat
        self._tokens = {}             # token -> kiadás ideje

    # -- port --
    def _port_for(self, port):
        port = self.port if port is None else port
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            raise RuntimeError("A szerver portja nincs beállítva.")
        return port

    # -- indítókód → token (T4) --
    def new_launch_code(self):
        """Új, egyszer használható, LAUNCH_CODE_TTL másodpercig érvényes indítókód."""
        code = secrets.token_urlsafe(24)
        now = self._clock()
        with self._lock:
            self._purge_codes(now)
            while len(self._launch_codes) >= MAX_PENDING_LAUNCH_CODES:
                oldest = min(self._launch_codes, key=self._launch_codes.get)
                del self._launch_codes[oldest]
            self._launch_codes[code] = now + LAUNCH_CODE_TTL
        return code

    def launch_url(self, code=None, port=None):
        """A böngészőnek átadott cím: ``http://127.0.0.1:<port>/#launch=<kód>``."""
        port = self._port_for(port)
        if code is None:
            code = self.new_launch_code()
        return "http://127.0.0.1:%d/#launch=%s" % (port, code)

    def _purge_codes(self, now):
        for c in [c for c, exp in self._launch_codes.items() if now >= exp]:
            del self._launch_codes[c]

    def exchange_launch_code(self, code):
        """Indítókód → új munkamenet-token; lejárt, ismeretlen vagy már felhasznált kódra None."""
        cand = _ascii_bytes(code, 128)
        if cand is None:
            return None
        now = self._clock()
        with self._lock:
            hit = None
            # minden függő kódot összevetünk (nincs korai kilépés)
            for stored in list(self._launch_codes):
                if hmac.compare_digest(stored.encode("ascii"), cand):
                    hit = stored
            if hit is None:
                return None
            expiry = self._launch_codes.pop(hit)
            if now >= expiry:
                return None
            token = secrets.token_urlsafe(32)
            while len(self._tokens) >= MAX_SESSION_TOKENS:
                oldest = min(self._tokens, key=self._tokens.get)
                del self._tokens[oldest]
            self._tokens[token] = now
            return token

    def check_token(self, value):
        """Konstans idejű tokenellenőrzés (minden kiadott tokennel összevet)."""
        cand = _ascii_bytes(value, 256)
        if cand is None:
            return False
        ok = False
        with self._lock:
            for tok in self._tokens:
                if hmac.compare_digest(tok.encode("ascii"), cand):
                    ok = True
        return ok

    def has_session(self):
        with self._lock:
            return bool(self._tokens)

    def revoke_tokens(self):
        with self._lock:
            self._tokens.clear()
            self._launch_codes.clear()

    # -- kulcsszármaztatás --
    def derive_key(self, purpose):
        """Célhoz kötött 32 bájtos kulcs a folyamatonkénti titokból (HMAC-SHA256)."""
        if not isinstance(purpose, str) or not purpose:
            raise ValueError("A kulcs céljának nem üres szövegnek kell lennie.")
        return hmac.new(self._secret, b"ma-gui/v1|" + purpose.encode("utf-8"), hashlib.sha256).digest()

    # -- aláírt fájl-URL (T5) --
    def _file_sig(self, doc_q, exp, rel_path):
        msg = ("%s|%d|%s" % (doc_q, exp, rel_path)).encode("utf-8")
        mac = hmac.new(self.derive_key("file-url"), msg, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")

    def sign_file_url(self, doc_id, rel_path, ttl=FILE_URL_TTL):
        """``/f/<doc>/<lejárat>/<aláírás>``; az aláírás a ``doc|lejárat|út`` hármasé.

        A ``doc`` az URL-kódolt (``quote(doc, safe='')``) alak, így a ``|`` elválasztó
        egyértelmű; az út az aláírásba kerül, az URL-be nem."""
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError("Üres dokumentum-azonosító.")
        if not isinstance(rel_path, str) or not rel_path:
            raise ValueError("Üres fájlút.")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 0 < ttl <= FILE_URL_MAX_TTL:
            raise ValueError("Az élettartam 1 és %d másodperc között lehet." % FILE_URL_MAX_TTL)
        exp = int(self._clock()) + ttl
        doc_q = quote(doc_id, safe="")
        return "/f/%s/%d/%s" % (doc_q, exp, self._file_sig(doc_q, exp, rel_path))

    @staticmethod
    def parse_file_url(url_path):
        """``/f/<doc>/<exp>/<sig>`` → (doc_id, exp, sig); formai hibára None."""
        if not isinstance(url_path, str):
            return None
        m = _FILE_URL_RE.match(url_path.split("?", 1)[0])
        if not m:
            return None
        doc_q, exp, sig = m.group(1), int(m.group(2)), m.group(3)
        try:
            doc = unquote(doc_q, errors="strict")
        except UnicodeDecodeError:
            return None
        if not doc or quote(doc, safe="") != doc_q or any(ord(c) < 0x20 or ord(c) == 0x7F for c in doc):
            return None
        return doc, exp, sig

    def verify_file_signature(self, doc_id, exp, sig, rel_path):
        """Érvényes-e (nem lejárt, nem hamisított) az aláírás az adott útra."""
        if (not isinstance(doc_id, str) or not doc_id or not isinstance(rel_path, str) or not rel_path
                or isinstance(exp, bool) or not isinstance(exp, int)):
            return False
        sig_b = _ascii_bytes(sig, 64)
        if sig_b is None:
            return False
        now = self._clock()
        if now > exp or exp > now + FILE_URL_MAX_TTL + 1:
            return False
        expected = self._file_sig(quote(doc_id, safe=""), exp, rel_path).encode("ascii")
        return hmac.compare_digest(expected, sig_b)

    def verify_file_url(self, url_path, lookup):
        """Aláírt URL ellenőrzése; ``lookup(doc_id)`` a jegyzékből (allowlist) adja az utat.

        Siker: (doc_id, rel_path); minden más esetben None (→ 403)."""
        parsed = self.parse_file_url(url_path)
        if parsed is None:
            return None
        doc, exp, sig = parsed
        rel = lookup(doc)
        if not isinstance(rel, str) or not rel:
            return None
        if not self.verify_file_signature(doc, exp, sig, rel):
            return None
        return doc, rel

    # -- kérésszűrés (T1, T2, T4, T11) --
    def check_request(self, method, path, headers, port=None):
        """Kérés előszűrése a kezelő előtt. None = mehet; különben (http, kód, üzenet).

        Sorrend: ismétlődő biztonsági fejléc (400) → Host (403) → útvonal (400) → metódus (405)
        → Origin (403) → Sec-Fetch-Site (403) → ``/api/*``: token (403), Content-Type (415)
        → Transfer-Encoding / Content-Length (400 / 413)."""
        port = self._port_for(port)
        try:
            host = _single_header(headers, "Host")
            origin = _single_header(headers, "Origin")
            site = _single_header(headers, "Sec-Fetch-Site")
            token = _single_header(headers, TOKEN_HEADER)
            ctype = _single_header(headers, "Content-Type")
            clen = _single_header(headers, "Content-Length")
            tenc = _single_header(headers, "Transfer-Encoding")
        except _DuplicateHeader:
            return _reject("BAD_REQUEST", _MSG_DUP)

        if host not in ("127.0.0.1:%d" % port, "localhost:%d" % port):
            return _reject("FORBIDDEN", _MSG_HOST)
        p = _split_target(path)
        if p is None:
            return _reject("BAD_REQUEST", _MSG_PATH)
        kind = route_class(p)
        allowed_methods = API_METHODS if kind == "api" else ("GET",)
        if method not in allowed_methods:
            return _reject("METHOD_NOT_ALLOWED", _MSG_METHOD)
        if origin is not None and origin != "http://" + host:
            return _reject("FORBIDDEN", _MSG_ORIGIN)
        if (site.lower() if site is not None else None) not in _FETCH_SITE_ALLOWED[kind]:
            return _reject("FORBIDDEN", _MSG_FETCH_SITE)

        if kind == "api":
            if (method, p) != SESSION_ROUTE and not self.check_token(token):
                return _reject("FORBIDDEN", _MSG_TOKEN)
            if method != "GET" and not _media_type_ok(ctype):
                return _reject("UNSUPPORTED_MEDIA", _MSG_MEDIA)
        if tenc is not None:
            return _reject("BAD_REQUEST", _MSG_CHUNKED)
        if clen is not None:
            if not clen.isdigit() or not clen.isascii() or len(clen) > 12:
                return _reject("BAD_REQUEST", _MSG_LENGTH)
            limit = body_limit(p)
            if int(clen) > limit:
                return _reject("PAYLOAD_TOO_LARGE", "A kérés törzse túl nagy (legfeljebb %d bájt)." % limit)
        return None


# ---------------------------------------------------------------------------
# válaszfejlécek (T3, T6, 7.1)
# ---------------------------------------------------------------------------

def csp_nonce():
    """Válaszonként új CSP-nonce (144 bit, base64url)."""
    return secrets.token_urlsafe(18)


def html_csp(nonce):
    if not isinstance(nonce, str) or not _NONCE_RE.match(nonce):
        raise ValueError("Érvénytelen CSP-nonce.")
    return HTML_CSP_TEMPLATE.format(n=nonce)


def base_headers(nonce=None, kind="api"):
    """A válasz biztonsági fejlécei (név, érték) párok listájaként; Content-Type nélkül.

    kind: 'html' (nonce kötelező, szigorú CSP), 'api' (JSON), 'file' (PDF/kép új lapon),
    'svg' (``Content-Security-Policy: sandbox …``). CORS-fejlécet soha nem ad."""
    if kind not in HEADER_KINDS:
        raise ValueError("Ismeretlen fejléc-típus: %s" % kind)
    if kind == "html":
        csp = html_csp(nonce)
    elif kind == "svg":
        csp = SVG_CSP
    elif kind == "file":
        csp = FILE_CSP
    else:
        csp = API_CSP
    return list(MANDATORY_HEADERS) + [("Content-Security-Policy", csp)]


# ---------------------------------------------------------------------------
# útvonal-biztonság (T7)
# ---------------------------------------------------------------------------

def _suffix(name):
    if isinstance(name, PurePath):
        name = name.name
    name = str(name).rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def check_extension(name, allowed):
    """Az utolsó kiterjesztés (kisbetűsítve) az allowlistben van-e; a végrehajtható/HTML-szerű
    kiterjesztés (NEVER_SERVE_EXTENSIONS) sosem engedett."""
    suffix = _suffix(name)
    if not suffix or suffix in NEVER_SERVE_EXTENSIONS:
        return False
    norm = {("." + e.lstrip(".")).lower() for e in allowed}
    return suffix in norm


def content_type_for(name):
    return CONTENT_TYPES.get(_suffix(name), "application/octet-stream")


def file_kind(name):
    """base_headers()-hez: 'svg' vagy 'file'."""
    return "svg" if _suffix(name) == ".svg" else "file"


def check_relpath(rel, allow_hidden=False):
    """Relatív, '/'-elválasztós projektút formai ellenőrzése → szakaszok listája.

    Tilt: abszolút utat, meghajtóbetűt és ADS-t (':'), visszaperjelet, '..'-t (szövegrészként
    is, a common:1 relpath mintájával egyezően), üres és '.' szakaszt, vezérlőkaraktert, a
    Windows-on tiltott karaktereket, pontra/szóközre végződő nevet, a fenntartott
    eszközneveket (CON, PRN, AUX, NUL, COM0–9, LPT0–9, felső indexes változatok, CONIN$,
    CONOUT$ — bármilyen kiterjesztéssel, kis/nagybetűtől függetlenül), alapból a rejtett
    ('.'-tal kezdődő) szakaszt is."""
    if isinstance(rel, os.PathLike):
        rel = os.fspath(rel)
    if not isinstance(rel, str):
        raise UnsafePath("Az útvonal csak szöveg lehet.")
    if not rel:
        raise UnsafePath("Üres útvonal.")
    if len(rel) > MAX_REL_PATH_LEN:
        raise UnsafePath("Túl hosszú útvonal.")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in rel):
        raise UnsafePath("Vezérlőkarakter az útvonalban.")
    if rel.startswith("/"):
        raise UnsafePath("Abszolút útvonal nem engedett.")
    if ":" in rel:
        raise UnsafePath("Meghajtóbetű vagy alternatív adatfolyam (':') nem engedett.")
    if "\\" in rel:
        raise UnsafePath("Visszaperjel nem engedett; az elválasztó '/'.")
    if any(c in _FORBIDDEN_PATH_CHARS for c in rel):
        raise UnsafePath("Tiltott karakter az útvonalban.")
    if ".." in rel:
        raise UnsafePath("A '..' nem engedett.")
    segs = rel.split("/")
    for seg in segs:
        if seg in ("", "."):
            raise UnsafePath("Üres vagy '.' útvonal-szakasz.")
        if len(seg) > MAX_SEGMENT_LEN:
            raise UnsafePath("Túl hosszú fájlnév.")
        if seg[-1] in ". ":
            raise UnsafePath("Ponttal vagy szóközzel végződő név nem engedett.")
        if seg.split(".", 1)[0].rstrip(" ").upper() in WINDOWS_RESERVED_NAMES:
            raise UnsafePath("Windows-on fenntartott eszköznév nem engedett.")
        if seg.startswith(".") and not allow_hidden:
            raise UnsafePath("Rejtett fájl vagy mappa nem engedett.")
    return segs


def is_within(path, root):
    """``path`` a ``root`` alatt van-e (mindkettő feloldott út legyen)."""
    try:
        Path(path).relative_to(Path(root))
    except ValueError:
        return False
    return True


def safe_resolve(root, rel, allowed_ext=None, must_exist=False, allow_hidden=False):
    """Relatív út biztonságos feloldása a ``root`` alá → abszolút Path.

    A symlinkeket feloldja, és elutasítja, ha az eredmény a gyökéren kívülre esik. Ha
    ``allowed_ext`` adott, a kért név és a feloldott cél kiterjesztése is az allowlistben
    kell legyen. ``must_exist``: hiányzó fájlra NOT_FOUND."""
    segs = check_relpath(rel, allow_hidden=allow_hidden)
    # os.path.realpath: a lógó symlinket is követi (Windows-on a 3.9-es Path.resolve nem)
    try:
        root_r = Path(os.path.realpath(os.fspath(root)))
        root_ok = root_r.is_dir()
        resolved = Path(os.path.realpath(str(root_r.joinpath(*segs))))
    except (OSError, ValueError, TypeError):
        raise UnsafePath("Az útvonal nem oldható fel.") from None
    if not root_ok:
        raise UnsafePath("A gyökérmappa nem érhető el.", code="NOT_FOUND")
    if not is_within(resolved, root_r):
        raise UnsafePath("Az útvonal a megengedett gyökéren kívülre mutat.")
    if must_exist and not resolved.exists():
        raise UnsafePath("A fájl nem található.", code="NOT_FOUND")
    if allowed_ext is not None:
        if not check_extension(segs[-1], allowed_ext) or not check_extension(resolved.name, allowed_ext):
            raise UnsafePath("Ez a fájltípus nem engedett.")
    return resolved


# ---------------------------------------------------------------------------
# JSON-beolvasás és méretkorlátok (T6, T11)
# ---------------------------------------------------------------------------

class _JsonReject(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _pairs_hook(pairs):
    obj = {}
    for k, v in pairs:
        if k in FORBIDDEN_JSON_KEYS:
            raise _JsonReject("Tiltott kulcs a JSON-ban (__proto__ / constructor / prototype).")
        if k in obj:
            raise _JsonReject("Ismétlődő kulcs a JSON-ban.")
        obj[k] = v
    return obj


def _no_constant(_name):
    raise _JsonReject("NaN és Infinity nem engedett; a hiányzó érték null.")


def _parse_int(text):
    if len(text) > MAX_NUMBER_LITERAL:
        raise _JsonReject("Túl hosszú számliterál a JSON-ban.")
    return int(text)


def _parse_float(text):
    if len(text) > MAX_NUMBER_LITERAL:
        raise _JsonReject("Túl hosszú számliterál a JSON-ban.")
    v = float(text)
    if v != v or v in (_INF, -_INF):
        raise _JsonReject("Nem véges szám a JSON-ban; a hiányzó érték null.")
    return v


def _depth_exceeds(obj, limit):
    stack = [(obj, 0)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            children = node.values()
        elif isinstance(node, list):
            children = node
        else:
            continue
        depth += 1
        if depth > limit:
            return True
        for child in children:
            if isinstance(child, (dict, list)):
                stack.append((child, depth))
    return False


def loads_limited(raw, max_bytes=MAX_BODY_BYTES, max_depth=MAX_JSON_DEPTH):
    """Kéréstörzs → Python-objektum, szigorú korlátokkal; hibára SecurityError.

    413: ``max_bytes``-nál nagyobb törzs. 400: üres, nem UTF-8, érvénytelen JSON, mélység >
    ``max_depth`` (a ``[]`` mélysége 1), tiltott vagy ismétlődő kulcs, NaN/Infinity (a túlcsorduló
    ``1e400`` is), ``MAX_NUMBER_LITERAL``-nál hosszabb számliterál."""
    if isinstance(raw, str):
        data = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        data = bytes(raw)
    else:
        raise TypeError("A törzs bytes vagy str lehet.")
    if len(data) > max_bytes:
        raise SecurityError("PAYLOAD_TOO_LARGE", "A kérés törzse túl nagy (legfeljebb %d bájt)." % max_bytes)
    if not data.strip():
        raise SecurityError("BAD_REQUEST", "Üres kéréstörzs.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SecurityError("BAD_REQUEST", "A kéréstörzs nem érvényes UTF-8.") from None
    depth_msg = "Túl mély JSON-szerkezet (legfeljebb %d szint)." % max_depth
    try:
        obj = json.loads(text, object_pairs_hook=_pairs_hook, parse_constant=_no_constant,
                         parse_int=_parse_int, parse_float=_parse_float)
    except _JsonReject as e:
        raise SecurityError("BAD_REQUEST", e.message) from None
    except json.JSONDecodeError as e:
        raise SecurityError("BAD_REQUEST", "Érvénytelen JSON (%d. sor, %d. oszlop)." % (e.lineno, e.colno)) from None
    except RecursionError:
        raise SecurityError("BAD_REQUEST", depth_msg) from None
    except ValueError:
        raise SecurityError("BAD_REQUEST", "Érvénytelen szám a JSON-ban.") from None
    if _depth_exceeds(obj, max_depth):
        raise SecurityError("BAD_REQUEST", depth_msg)
    return obj


def check_table_limits(header, rows):
    """≤ MAX_ROWS sor és ≤ MAX_COLUMNS oszlop (soronként is); túllépésre 413."""
    if len(header) > MAX_COLUMNS:
        raise SecurityError("PAYLOAD_TOO_LARGE", "Túl sok oszlop (legfeljebb %d)." % MAX_COLUMNS)
    if len(rows) > MAX_ROWS:
        raise SecurityError("PAYLOAD_TOO_LARGE", "Túl sok sor (legfeljebb %d)." % MAX_ROWS)
    for row in rows:
        if len(row) > MAX_COLUMNS:
            raise SecurityError("PAYLOAD_TOO_LARGE", "Túl sok oszlop egy sorban (legfeljebb %d)." % MAX_COLUMNS)


# ---------------------------------------------------------------------------
# boríték (4.2)
# ---------------------------------------------------------------------------

def new_request_id():
    return "q_" + secrets.token_hex(4)


def ok_envelope(schema, data, warnings=None, meta=None):
    """Sikeres válasz: ``{ok, schema, data, warnings, meta}``."""
    return {"ok": True, "schema": schema, "data": data, "warnings": list(warnings or []),
            "meta": dict(meta or {})}


def error_envelope(code, message, details=None):
    """Hibaválasz: ``{ok: false, error: {code, http, message[, details]}}`` (3.4 kódokkal)."""
    if code not in ERROR_CODES:
        raise ValueError("ismeretlen hibakód: %s" % code)
    err = {"code": code, "http": ERROR_CODES[code], "message": message}
    if details is not None:
        err["details"] = details
    return {"ok": False, "error": err}


ENVELOPE_SCHEMA = {
    "$id": "urn:szk:contract:ma.envelope:1",
    "oneOf": [{"$ref": "#/$defs/ok"}, {"$ref": "#/$defs/error"}],
    "$defs": {
        "ok": {
            "type": "object",
            "required": ["ok", "schema", "data", "warnings", "meta"],
            "properties": {
                "ok": {"const": True},
                "schema": {"type": ["string", "null"]},
                "warnings": {"type": "array"},
                "meta": {
                    "type": "object",
                    "properties": {
                        "engine": {"type": ["string", "null"]},
                        "elapsed_ms": {"type": "number", "minimum": 0},
                        "project_rev": {"type": ["integer", "null"]},
                        "request_id": {"type": "string"},
                    },
                },
            },
        },
        "error": {
            "type": "object",
            "required": ["ok", "error"],
            "properties": {
                "ok": {"const": False},
                "error": {
                    "type": "object",
                    "required": ["code", "http", "message"],
                    "properties": {
                        "code": {"enum": sorted(ERROR_CODES)},
                        "http": {"type": "integer", "minimum": 400, "maximum": 599},
                        "message": {"type": "string"},
                        "details": {"type": "object"},
                    },
                },
            },
        },
    },
}
