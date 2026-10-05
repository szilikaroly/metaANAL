# -*- coding: utf-8 -*-
"""Útválasztó és egységes boríték a munkapad HTTP API-jához (terv 3.1, 3.4, 4.2).

- Metódus + útminta → kezelő. Minta: ``/api/log/<kind>`` (egy szakasz) vagy ``/api/x/<rest:path>``
  (a maradék). A paraméterek URL-dekódolva érkeznek; vezérlőkarakter és érvénytelen UTF-8 → 400.
- JSON-törzs csak ``security.loads_limited``-del (méret, mélység, tiltott kulcsok, NaN).
- Opcionális kérés- és válaszséma útvonalanként (``schema_lite``). A válaszséma-ellenőrzés
  (``validate_responses``) fejlesztői/teszt-mód: eltérésnél 500 INTERNAL.
- Siker: ``{ok, schema, data, warnings, meta{engine, elapsed_ms, project_rev, request_id}}``;
  hiba: ``{ok: false, error{code, http, message, details?}}`` a 3.4 kódtáblával.
- Kivételek: ``code``/``http``/``message`` mezős hibák (security, store, privacy) a saját kódjukkal;
  a motor ``ValueError``-ja → 422 VALIDATION; zárolt fájl/adatbázis → 423 LOCKED; minden más → 500
  INTERNAL, a kliens felé részletek és traceback nélkül.

Napló (T10): csak metódus, útvonal-MINTA (nem a tényleges út, query nélkül), kód, idő és
kérésazonosító — cellaérték, keresőkifejezés, token és kivételszöveg soha.
"""
import json
import re
import sqlite3
import time
from urllib.parse import parse_qs, unquote

from . import schema_lite, security

_PARAM_RE = re.compile(r"<([a-z_][a-z0-9_]*)(?::(path))?>")
_INF = float("inf")
_MAX_MESSAGE = 2000
_MAX_SCHEMA_ERRORS = 20
_MISSING = object()

MSG_INTERNAL = "Belső hiba történt (kérésazonosító: %s). A részletek adatvédelmi okból nem kerültek a naplóba."
MSG_NOT_FOUND = "Nincs ilyen végpont."
MSG_METHOD = "A kérés metódusa ezen a végponton nem engedélyezett."
MSG_LOCKED_DB = ("A projektnapló (projekt.sqlite) foglalt — más program (például egy ágens) éppen írja. "
                 "Próbáld újra néhány másodperc múlva.")
MSG_LOCKED_FILE = "A fájlt más program zárolja (például az Excel). Zárd be, majd próbáld újra."
MSG_FILE_NOT_FOUND = "A kért fájl vagy a projektnapló nem található."
MSG_BAD_SHAPE = "A kérés szerkezete hibás."


class ApiError(Exception):
    """Kezelőből dobható hiba a 3.4 kódtábla szerint (``code`` → HTTP)."""

    def __init__(self, code, message, details=None, headers=None):
        if code not in security.ERROR_CODES:
            raise ValueError("ismeretlen hibakód: %s" % code)
        super().__init__(message)
        self.code = code
        self.http = security.ERROR_CODES[code]
        self.message = message
        self.details = details
        self.headers = list(headers or [])


class Result(object):
    """Sikeres JSON-válasz: data + schema (+ figyelmeztetések, ETag, extra fejlécek)."""

    __slots__ = ("data", "schema", "warnings", "etag", "status", "headers")

    def __init__(self, data=None, schema=None, warnings=None, etag=None, status=200, headers=None):
        self.data = data
        self.schema = schema
        self.warnings = list(warnings or [])
        self.etag = etag
        self.status = status
        self.headers = list(headers or [])


class Response(object):
    """Nem-boríték válasz (HTML-oldal, kiszolgált fájl). ``kind``: html | file | svg | api.

    ``body`` (bytes) vagy ``file_path`` (darabolva küldjük); ``nonce`` a 'html' fajtához kell."""

    __slots__ = ("status", "body", "file_path", "length", "content_type", "kind", "nonce", "headers")

    def __init__(self, body=b"", content_type="application/octet-stream", kind="file", status=200,
                 nonce=None, headers=None, file_path=None, length=None):
        self.status = status
        self.body = body
        self.file_path = file_path
        self.length = length
        self.content_type = content_type
        self.kind = kind
        self.nonce = nonce
        self.headers = list(headers or [])


class Request(object):
    """Egy beérkezett kérés a kezelő számára (a törzs már beolvasva, korlátok között)."""

    def __init__(self, app, method, target, headers, body=b"", request_id=None):
        self.app = app
        self.method = method
        self.target = target
        self.path = target.split("#", 1)[0].split("?", 1)[0]
        qs = target.split("#", 1)[0].partition("?")[2]
        try:
            parsed = parse_qs(qs, keep_blank_values=True, strict_parsing=False, errors="strict",
                              max_num_fields=100)
        except (ValueError, UnicodeDecodeError):
            raise ApiError("BAD_REQUEST", "Érvénytelen lekérdezési paraméterek.") from None
        self.query = {k: v[-1] for k, v in parsed.items()}
        self.headers = headers
        self.body = body or b""
        self.params = {}
        self.route = None
        self.request_id = request_id or security.new_request_id()
        self.started = time.monotonic()
        self._json = _MISSING

    def header(self, name, default=None):
        """Fejlécérték (kis/nagybetű-független); több azonos fejlécnél az utolsó."""
        get_all = getattr(self.headers, "get_all", None)
        if callable(get_all):
            vals = [str(v) for v in (get_all(name) or [])]
        else:
            vals = [str(v) for k, v in (self.headers or {}).items() if str(k).lower() == name.lower()]
        return vals[-1].strip() if vals else default

    def arg(self, name, default=None, max_len=1024):
        """Lekérdezési paraméter (szöveg); túl hosszú vagy vezérlőkarakteres értékre 400."""
        v = self.query.get(name)
        if v is None:
            return default
        if len(v) > max_len or any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):
            raise ApiError("BAD_REQUEST", "Érvénytelen lekérdezési paraméter: %s." % name)
        return v

    def json(self, default=_MISSING):
        """A törzs JSON-ként; üres törzsnél ``default`` (ha nincs megadva: 400)."""
        if self._json is not _MISSING:
            return self._json
        if not self.body.strip():
            if default is _MISSING:
                raise ApiError("BAD_REQUEST", "Hiányzó kéréstörzs (JSON-objektum kell).")
            return default
        self._json = security.loads_limited(self.body, max_bytes=security.body_limit(self.path))
        return self._json

    def json_object(self, default=_MISSING):
        body = self.json({} if default is _MISSING else default)
        if not isinstance(body, dict):
            raise ApiError("BAD_REQUEST", "A kéréstörzs JSON-objektum legyen.")
        return body


class Route(object):
    __slots__ = ("method", "pattern", "regex", "handler", "schema", "request_schema", "response_schema",
                 "name", "activity")

    def __init__(self, method, pattern, handler, schema=None, request_schema=None, response_schema=None,
                 activity=True):
        self.method = method
        self.pattern = pattern
        self.regex = _compile(pattern)
        self.handler = handler
        self.schema = schema
        self.request_schema = request_schema
        self.response_schema = response_schema
        self.name = "%s %s" % (method, pattern)
        self.activity = activity            # False: nem számít felhasználói aktivitásnak (long-poll)


def _compile(pattern):
    if not pattern.startswith("/"):
        raise ValueError("Az útminta '/'-rel kezdődjön.")
    out, pos = [], 0
    for m in _PARAM_RE.finditer(pattern):
        out.append(re.escape(pattern[pos:m.start()]))
        if m.group(2) == "path":
            out.append("(?P<%s>[^?#]+)" % m.group(1))
        else:
            out.append("(?P<%s>[^/?#]+)" % m.group(1))
        pos = m.end()
    out.append(re.escape(pattern[pos:]))
    return re.compile("^" + "".join(out) + "$")


def _decode_param(raw):
    try:
        val = unquote(raw, errors="strict")
    except UnicodeDecodeError:
        raise ApiError("BAD_REQUEST", "Érvénytelen URL-kódolás az útvonalban.") from None
    if not val or any(ord(c) < 0x20 or ord(c) == 0x7F for c in val):
        raise ApiError("BAD_REQUEST", "Érvénytelen útvonal-paraméter.")
    return val


# ---------------------------------------------------------------------------- JSON-kimenet
def sanitize(obj):
    """JSON-képes alak: NaN/±végtelen → None (4.0: soha NaN), tuple/set → lista, bytes → None,
    egyéb ismeretlen típus → szöveg."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (_INF, -_INF):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return [sanitize(v) for v in sorted(obj, key=str)]
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return None
    if hasattr(obj, "__fspath__"):
        return str(obj)
    return str(obj)


def dumps(obj):
    return json.dumps(sanitize(obj), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


# ---------------------------------------------------------------------------- hibák
def _clip(msg):
    msg = str(msg)
    return msg if len(msg) <= _MAX_MESSAGE else msg[:_MAX_MESSAGE] + "…"


def classify_exception(exc, request_id):
    """Kivétel → (kód, üzenet, részletek, napló-megjegyzés). A napló-megjegyzés csak típusnév."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in security.ERROR_CODES and not isinstance(exc, (OSError, sqlite3.Error)):
        message = getattr(exc, "message", None) or str(exc)
        details = getattr(exc, "details", None)
        if details is not None and not isinstance(details, dict):
            details = {"items": details}
        if details == {}:
            details = None
        if code == "INTERNAL":
            return code, MSG_INTERNAL % request_id, None, type(exc).__name__
        return code, _clip(message), details, None
    if isinstance(exc, sqlite3.OperationalError):
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            return "LOCKED", MSG_LOCKED_DB, None, None
        return "INTERNAL", MSG_INTERNAL % request_id, None, type(exc).__name__
    if isinstance(exc, FileNotFoundError):
        return "NOT_FOUND", MSG_FILE_NOT_FOUND, None, None
    if isinstance(exc, PermissionError):
        return "LOCKED", MSG_LOCKED_FILE, None, None
    if isinstance(exc, ValueError) and type(exc).__module__.startswith("metaelemzes"):
        return "VALIDATION", _clip(exc), None, None
    if type(exc) is ValueError:
        # a motor függvényei sima ValueError-t dobnak (3.4: VALIDATION)
        return "VALIDATION", _clip(exc), None, None
    return "INTERNAL", MSG_INTERNAL % request_id, None, type(exc).__name__


# ---------------------------------------------------------------------------- útválasztó
class Router(object):
    """Útvonal-tábla és diszpécser. ``meta_fn()`` → {engine, project_rev}; ``log_fn(sor)`` a
    szervernaplóba ír (csak minta, kód, idő)."""

    def __init__(self, meta_fn=None, log_fn=None, validate_responses=False, registry=None):
        self.routes = []
        self.meta_fn = meta_fn
        self.log_fn = log_fn
        self.validate_responses = validate_responses
        self.registry = registry

    def add(self, method, pattern, handler, schema=None, request_schema=None, response_schema=None,
            activity=True):
        method = method.upper()
        for r in self.routes:
            if r.method == method and r.pattern == pattern:
                raise ValueError("Az útvonal már regisztrálva: %s %s" % (method, pattern))
        if request_schema is not None:
            probs = schema_lite.check_schema(request_schema, self.registry)
            if probs:
                raise ValueError("Hibás kérésséma (%s %s): %s" % (method, pattern, "; ".join(probs[:3])))
        if response_schema is not None:
            probs = schema_lite.check_schema(response_schema, self.registry)
            if probs:
                raise ValueError("Hibás válaszséma (%s %s): %s" % (method, pattern, "; ".join(probs[:3])))
        route = Route(method, pattern, handler, schema, request_schema, response_schema, activity)
        self.routes.append(route)
        return route

    def route(self, method, pattern, **kw):
        def deco(fn):
            self.add(method, pattern, fn, **kw)
            return fn
        return deco

    def match(self, method, path):
        """(Route, nyers paraméterek); ismeretlen útnál NOT_FOUND, más metódusnál METHOD_NOT_ALLOWED."""
        allowed = []
        for r in self.routes:
            m = r.regex.match(path)
            if m:
                if r.method == method:
                    return r, m.groupdict()
                allowed.append(r.method)
        if allowed:
            raise ApiError("METHOD_NOT_ALLOWED", MSG_METHOD, headers=[("Allow", ", ".join(sorted(set(allowed))))])
        raise ApiError("NOT_FOUND", MSG_NOT_FOUND)

    def find(self, method, path):
        """Mint a match, de kivétel helyett None (pl. az aktivitás-számláláshoz)."""
        for r in self.routes:
            if r.method == method and r.regex.match(path):
                return r
        return None

    # -- boríték
    def _meta(self, req):
        meta = {"engine": None, "project_rev": None}
        if self.meta_fn is not None:
            try:
                meta.update(self.meta_fn() or {})
            except Exception:                   # noqa: BLE001 — a meta hibája ne vigye el a választ
                pass
        meta["elapsed_ms"] = int((time.monotonic() - req.started) * 1000)
        meta["request_id"] = req.request_id
        return meta

    def error_body(self, code, message, details=None):
        return dumps(security.error_envelope(code, message, details))

    def dispatch(self, req):
        """Kérés → (http, extra fejlécek, Response vagy a boríték bájtjai)."""
        route_name = "%s <ismeretlen>" % req.method
        extra_headers = []
        try:
            route, raw = self.match(req.method, req.path)
            route_name = route.name
            req.route = route
            req.params = {k: _decode_param(v) for k, v in raw.items()}
            if route.request_schema is not None:
                body = req.json()
                errs = schema_lite.validate(body, route.request_schema, self.registry, limit=_MAX_SCHEMA_ERRORS)
                if errs:
                    raise ApiError("BAD_REQUEST", MSG_BAD_SHAPE, {"errors": errs})
            out = route.handler(req)
            if isinstance(out, Response):
                self._log(req, route_name, out.status, None)
                return out.status, out.headers, out
            if not isinstance(out, Result):
                out = Result(out, route.schema)
            schema = out.schema if out.schema is not None else route.schema
            data = sanitize(out.data)
            if self.validate_responses and route.response_schema is not None:
                errs = schema_lite.validate(data, route.response_schema, self.registry, limit=_MAX_SCHEMA_ERRORS)
                if errs:
                    raise _ResponseShapeError("; ".join(errs[:5]))
            env = security.ok_envelope(schema, data, [_clip(w) for w in out.warnings], self._meta(req))
            if self.validate_responses:
                errs = schema_lite.validate(env, security.ENVELOPE_SCHEMA, limit=_MAX_SCHEMA_ERRORS)
                if errs:
                    raise _ResponseShapeError("; ".join(errs[:5]))
            headers = list(out.headers)
            if out.etag:
                headers.append(("ETag", '"%s"' % out.etag))
            self._log(req, route_name, out.status, None)
            return out.status, headers, dumps(env)
        except Exception as exc:                # noqa: BLE001 — minden hiba borítékot kap
            code, message, details, note = classify_exception(exc, req.request_id)
            if isinstance(exc, _ResponseShapeError):
                note = "válaszséma: %s" % exc
            if isinstance(exc, ApiError):
                extra_headers = exc.headers
            http = security.ERROR_CODES[code]
            self._log(req, route_name, http, code, note)
            try:
                body = dumps(security.error_envelope(code, message, details))
            except (TypeError, ValueError):
                body = self.error_body(code, message)
            return http, extra_headers, body

    def _log(self, req, route_name, status, code, note=None):
        if self.log_fn is None:
            return
        ms = int((time.monotonic() - req.started) * 1000)
        line = "%s %d%s %dms %s" % (route_name, status, (" " + code) if code else "", ms, req.request_id)
        if note:
            line += " (%s)" % note
        try:
            self.log_fn(line)
        except Exception:                       # noqa: BLE001 — a napló hibája ne vigye el a választ
            pass


class _ResponseShapeError(Exception):
    """A válasz nem felel meg a sémájának (fejlesztői mód) → 500 INTERNAL."""
