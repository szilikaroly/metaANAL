# -*- coding: utf-8 -*-
"""Metaheadhunter — HTTP-réteg (csak Python standard könyvtár: urllib).

Kezdőknek: ez a modul beszél a külső adatbázisokkal (PubMed, Europe PMC, OpenAlex, Scopus,
ClinicalTrials.gov, Crossref). Gondoskodik arról, hogy

* a proxy-beállítást (``HTTPS_PROXY``/``HTTP_PROXY``/``NO_PROXY``) és a rendszer CA-fájlját
  (``SSL_CERT_FILE``) kövessük,
* forrásonként/gépenként udvarias sebességgel kérdezzünk (sebességkorlát),
* átmeneti hibánál várjunk és újrapróbáljunk (exponenciális várakozás + véletlen „jitter",
  a ``Retry-After`` fejléc tisztelete),
* elérhetetlen forrásnál ne álljon le a munka: ``SourceUnavailable`` kivétel jön kezdőbarát magyar
  magyarázattal (``unreachable`` / ``rate_limited`` / ``unauthorized`` / ``forbidden`` / ``not_configured``),
* a kulcsok és az e-mail-cím **soha** ne kerüljenek naplóba, hibaüzenetbe, gyorsítótár-kulcsba vagy
  tesztkazettába (``redact``, ``redact_url``, ``find_secret_leaks``),
* a tesztek hálózat nélkül, rögzített válaszokkal („kazettákkal") fussanak:
  ``MA_HH_CASSETTE=record|replay|off`` és ``MA_HH_CASSETTE_FILE=<fájl vagy mappa>``.

Szerződés: TERV_metaheadhunter.md 3. és 20.5 fejezet (``Response``, ``SourceUnavailable``,
``HttpClient.get``). A kazetta-formátum: ``contracts/ma.headhunter.cassette.v1.schema.json``.
"""

from __future__ import absolute_import

import base64
import email.utils
import hashlib
import http.client
import json
import os
import random
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from . import __version__

# ---------------------------------------------------------------------------------------------
# Környezeti változók (kulcsok csak innen jöhetnek — parancssori kapcsoló kulcsra nincs, N6)
# ---------------------------------------------------------------------------------------------

ENV_CONTACT_EMAIL = "MA_CONTACT_EMAIL"
ENV_OPENALEX_APIKEY = "MA_OPENALEX_APIKEY"
ENV_SCOPUS_APIKEY = "MA_SCOPUS_APIKEY"
ENV_SCOPUS_INSTTOKEN = "MA_SCOPUS_INSTTOKEN"
ENV_NCBI_APIKEY = "MA_NCBI_APIKEY"
ENV_CACHE_DIR = "MA_HH_CACHE_DIR"
ENV_CASSETTE = "MA_HH_CASSETTE"
ENV_CASSETTE_FILE = "MA_HH_CASSETTE_FILE"

#: minden titkosnak tekintett környezeti változó (az e-mail-cím is védett adat, N9)
SECRET_ENV = (ENV_SCOPUS_APIKEY, ENV_SCOPUS_INSTTOKEN, ENV_OPENALEX_APIKEY, ENV_NCBI_APIKEY, ENV_CONTACT_EMAIL)

REDACTED = "«redacted»"  # «redacted»

#: URL-/űrlap-paraméterek, amelyeket a naplóból, hibából, gyorsítótár-kulcsból és kazettából elhagyunk
SECRET_PARAMS = frozenset(["api_key", "apikey", "insttoken", "mailto", "email", "tool", "access_token"])

#: tiltott fejlécek (soha nem kerülnek kazettába vagy naplóba)
SECRET_HEADERS = frozenset(["authorization", "proxy-authorization", "x-els-apikey", "x-els-insttoken",
                            "cookie", "set-cookie"])

#: kazettába menthető válaszfejlécek (minden más — Set-Cookie, report-to, nel … — kimarad)
CASSETTE_HEADER_PREFIXES = ("content-type", "retry-after", "x-ratelimit", "x-els-status")

#: a forráskulcsok (a ``retrieval.source`` enum értékei)
KNOWN_SOURCES = ("pubmed", "europepmc", "openalex", "scopus", "ctgov", "crossref", "pmc")

#: alapértelmezett sebességkorlát gépnév szerint (kérés/másodperc). A PubMed és a PMC efetch ugyanazt a
#: gépet használja, ezért gépenként korlátozunk (kulccsal az NCBI 10/s-ot enged — a kliens beállítja).
DEFAULT_HOST_RATES = {
    "eutils.ncbi.nlm.nih.gov": 3.0,
    "www.ncbi.nlm.nih.gov": 3.0,
    "pmc.ncbi.nlm.nih.gov": 3.0,
    "www.ebi.ac.uk": 5.0,
    "api.openalex.org": 5.0,
    "api.elsevier.com": 2.0,
    "clinicaltrials.gov": 3.0,
    "api.crossref.org": 3.0,
}
DEFAULT_RATE = 3.0

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
#: ennél rövidebb ``Retry-After`` esetén várunk és újrapróbálunk; hosszabbnál ``rate_limited``
DEFAULT_MAX_RETRY_AFTER = 60.0

SOURCE_NAMES = {
    "pubmed": ("A", "PubMed"),
    "europepmc": ("Az", "Europe PMC"),
    "openalex": ("Az", "OpenAlex"),
    "scopus": ("A", "Scopus"),
    "ctgov": ("A", "ClinicalTrials.gov"),
    "crossref": ("A", "Crossref"),
    "pmc": ("A", "PubMed Central"),
}


def source_name(source):
    """A forrás megjelenítendő neve (pl. ``"Europe PMC"``)."""
    return SOURCE_NAMES.get(source, ("A(z)", str(source)))[1]


def _hu_the(source, capital=True):
    art, name = SOURCE_NAMES.get(source, ("A(z)", str(source)))
    if not capital:
        art = art.lower()
    return "%s %s" % (art, name)


def get_env(name, env=None):
    """Környezeti változó (üres szöveg → ``None``); ``env`` tesztben felülírható leképezés."""
    src = os.environ if env is None else env
    val = src.get(name)
    if val is None:
        return None
    val = str(val).strip()
    return val or None


def secret_status(env=None):
    """Csak igen/nem: mely kulcsok vannak beállítva (az érték, hossz vagy ujjlenyomat SOHA nem)."""
    return {
        "scopus_key": get_env(ENV_SCOPUS_APIKEY, env) is not None,
        "scopus_insttoken": get_env(ENV_SCOPUS_INSTTOKEN, env) is not None,
        "openalex_key": get_env(ENV_OPENALEX_APIKEY, env) is not None,
        "ncbi_key": get_env(ENV_NCBI_APIKEY, env) is not None,
        "contact_email": get_env(ENV_CONTACT_EMAIL, env) is not None,
    }


def user_agent(env=None):
    """``metaelemzes-headhunter/<verzió> (python-urllib; mailto:<e-mail>)`` (e-mail nélkül ``(python-urllib)``)."""
    mail = get_env(ENV_CONTACT_EMAIL, env)
    if mail:
        return "metaelemzes-headhunter/%s (python-urllib; mailto:%s)" % (__version__, mail)
    return "metaelemzes-headhunter/%s (python-urllib)" % __version__


# ---------------------------------------------------------------------------------------------
# Redaktálás (N6, N9) — minden kimenő szöveg ezen megy át
# ---------------------------------------------------------------------------------------------

def secret_values(env=None, extra=()):
    """A nyilvántartott titkos értékek (és URL-kódolt alakjaik), hossz szerint csökkenő sorrendben."""
    vals = set()
    for name in SECRET_ENV:
        v = get_env(name, env)
        if v and len(v) >= 4:
            vals.add(v)
    for v in extra or ():
        if v and len(str(v)) >= 4:
            vals.add(str(v))
    out = set()
    for v in vals:
        out.add(v)
        out.add(urllib.parse.quote(v, safe=""))
        out.add(urllib.parse.quote_plus(v, safe=""))
    return sorted(out, key=lambda s: (-len(s), s))


_PARAM_RE = re.compile(r"(?i)\b(api_key|apikey|insttoken|mailto|email|access_token)(=|%3D)([^&\s\"'<>\\]+)")
_HEADER_RE = re.compile(r"(?i)\b(authorization|x-els-apikey|x-els-insttoken|cookie|set-cookie)(\s*[:=]\s*)"
                        r"([^\r\n,;\"'\\]+)")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_MAILTO_RE = re.compile(r"(?i)mailto:[^\s);,\"'<>\\]+")


def redact(text, env=None, extra=()):
    """A titkos értékeket, tiltott paramétereket és fejléceket ``«redacted»``-re cseréli.

    Minden napló-, hiba-, progress- és kazetta-szöveg ezen megy át (N6). Nem szöveg bemenetet
    ``str()``-ré alakít."""
    if text is None:
        return None
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    s = str(text)
    for v in secret_values(env, extra):
        if v in s:
            s = s.replace(v, REDACTED)
    s = _PARAM_RE.sub(lambda m: m.group(1) + m.group(2) + REDACTED, s)
    s = _HEADER_RE.sub(lambda m: m.group(1) + m.group(2) + REDACTED, s)
    s = _BEARER_RE.sub("Bearer " + REDACTED, s)
    s = _MAILTO_RE.sub("mailto:" + REDACTED, s)
    return s


def find_secret_leaks(data, env=None, extra=()):
    """Bájtszintű keresés: mely titkos környezeti változók értéke szerepel ``data``-ban (H016-őr).

    Visszaad: a szivárgó változók NEVEI (az érték soha)."""
    if data is None:
        return []
    if isinstance(data, str):
        blob = data.encode("utf-8", "replace")
    else:
        blob = bytes(data)
    leaks = []
    for name in SECRET_ENV:
        v = get_env(name, env)
        if not v or len(v) < 4:
            continue
        forms = {v, urllib.parse.quote(v, safe=""), urllib.parse.quote_plus(v, safe="")}
        if any(f.encode("utf-8") in blob for f in forms):
            leaks.append(name)
    for i, v in enumerate(extra or ()):
        if v and len(str(v)) >= 4 and str(v).encode("utf-8") in blob:
            leaks.append("extra[%d]" % i)
    return leaks


class SecretLeakError(RuntimeError):
    """Titok került volna kimenetbe (kazetta, napló) — az írás elmaradt."""


def _split_query(query):
    return urllib.parse.parse_qsl(query or "", keep_blank_values=True)


def canonical_params(params, drop_secrets=True):
    """Paraméterlista rendezve, a titkos paraméterek nélkül (gyorsítótár-kulcs, kazetta-illesztés)."""
    items = []
    for k, v in params:
        if drop_secrets and str(k).lower() in SECRET_PARAMS:
            continue
        items.append((str(k), "" if v is None else str(v)))
    items.sort()
    return items


def redact_url(url, env=None, sort=False):
    """A titkos URL-paramétereket ELHAGYJA (a szerződés szerint „paraméter nélkül"), a maradékban a
    nyilvántartott titkos értékeket redaktálja. ``sort=True``: rendezett (kanonikus) paramétersorrend."""
    if url is None:
        return None
    parts = urllib.parse.urlsplit(str(url))
    params = _split_query(parts.query)
    kept = canonical_params(params) if sort else [(k, v) for k, v in params if str(k).lower() not in SECRET_PARAMS]
    query = urllib.parse.urlencode(kept)
    out = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    return redact(out, env)


def canonical_url(url, env=None):
    """Kanonikus, redaktált URL: rendezett paraméterek, titkos paraméterek nélkül."""
    return redact_url(url, env, sort=True)


def canonical_body(body, env=None):
    """POST-törzs kanonikus alakja (űrlap: rendezett, titkos paraméterek nélkül)."""
    if body is None:
        return None
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    if "=" in body and "\n" not in body and "{" not in body[:1]:
        try:
            return redact(urllib.parse.urlencode(canonical_params(_split_query(body))), env)
        except Exception:  # pragma: no cover - védő ág
            pass
    return redact(body, env)


def redact_headers(headers, env=None):
    """Fejlécek naplózható/kazettázható másolata: a tiltott fejlécek nélkül, értékek redaktálva."""
    out = {}
    for k, v in (headers or {}).items():
        if str(k).lower() in SECRET_HEADERS:
            continue
        out[str(k)] = redact(v, env)
    return out


# ---------------------------------------------------------------------------------------------
# Idő és azonosító-segédek
# ---------------------------------------------------------------------------------------------

def utc_ts(epoch=None):
    """UTC időbélyeg ``YYYY-MM-DDTHH:MM:SSZ`` (a sémák ``ts`` mintája)."""
    if epoch is None:
        epoch = time.time()
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_ts(epoch=None):
    """``YYYYMMDDTHHMMSSZ`` (keresés- és futásazonosítókhoz)."""
    if epoch is None:
        epoch = time.time()
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def norm_pmid(value):
    """PMID: csak számjegy (``None``, ha nem értelmezhető)."""
    if value is None:
        return None
    s = str(value).strip()
    s = re.sub(r"(?i)^(pmid:?\s*|https?://(www\.)?(pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov/pubmed)/)", "", s)
    s = s.strip().strip("/")
    return s if re.match(r"^\d{1,9}$", s) and s.strip("0") else None


def norm_pmcid(value):
    """PMCID: ``PMC`` + számjegy."""
    if value is None:
        return None
    s = str(value).strip().upper()
    s = re.sub(r"^HTTPS?://\S*/(PMC\d+)/?$", r"\1", s)
    m = re.match(r"^(?:PMCID:?\s*)?(?:PMC)?(\d{1,9})$", s)
    return "PMC" + m.group(1) if m else None


def norm_doi(value):
    """DOI: kisbetűs, ``https://doi.org/`` / ``doi:`` előtag és záró írásjel nélkül."""
    if value is None:
        return None
    s = urllib.parse.unquote(str(value).strip())
    s = re.sub(r"(?i)^(https?://(dx\.)?doi\.org/|doi:\s*|doi\s+)", "", s).strip()
    s = s.rstrip(".,;:)]}>\"' ")
    s = s.lower()
    return s if re.match(r"^10\.\d{4,9}/\S+$", s) else None


def norm_nct(value):
    """NCT-szám: ``NCT`` + 8 számjegy."""
    if value is None:
        return None
    m = re.search(r"(?i)\bNCT\s?(\d{8})\b", str(value))
    return "NCT" + m.group(1) if m else None


def norm_eid(value):
    """Scopus EID: ``2-s2.0-<számjegyek>`` (``SCOPUS_ID:…`` és puszta számjegy is elfogadott)."""
    if value is None:
        return None
    s = str(value).strip()
    m = re.match(r"(?i)^(?:2-s2\.0-|scopus_id:\s*)?(\d{5,15})$", s)
    return "2-s2.0-" + m.group(1) if m else None


def norm_openalex(value):
    """OpenAlex munka-azonosító: ``W`` + számjegy."""
    if value is None:
        return None
    m = re.search(r"(?i)(?:^|/)(W\d{2,12})$", str(value).strip())
    return m.group(1).upper() if m else None


def sha1_10(text):
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------------------------------------
# Állapot-magyarázatok (3.2 fejezet táblázata) — a sources.py és a CLI is ezt használja
# ---------------------------------------------------------------------------------------------

SOURCE_STATUSES = ("ok", "not_configured", "unreachable", "rate_limited", "unauthorized", "forbidden",
                   "disabled", "unknown")


def status_explain(source, status, reset_at=None, detail=None):
    """Kezdőbarát ``{hu, en}`` magyarázat: mit jelent az állapot és mit tegyél."""
    the_hu = _hu_the(source)
    name = source_name(source)
    reset_hu = reset_at or "ismeretlen időpont"
    reset_en = reset_at or "unknown time"
    if status == "ok":
        hu = "%s elérhető." % the_hu
        en = "%s is reachable." % name
    elif status == "not_configured":
        if source == "scopus":
            hu = ("A Scopus nincs beállítva: hiányzik az MA_SCOPUS_APIKEY környezeti változó. Kulcsot a "
                  "dev.elsevier.com oldalon kérhetsz (intézményi hálózatról; Magyarországon az intézményi "
                  "Scopus-hozzáférés jellemzően az EISZ-en át érhető el); lásd TELEPITES.md.")
            en = ("Scopus is not configured: the MA_SCOPUS_APIKEY environment variable is missing. Request a key "
                  "at dev.elsevier.com (from your institution's network); see TELEPITES.md.")
        else:
            hu = "%s nincs beállítva." % the_hu
            en = "%s is not configured." % name
    elif status == "unauthorized":
        hu = ("%s elutasította a kulcsot (401). Ellenőrizd, hogy a kulcs helyes-e és nem járt-e le "
              "(a kulcs csak környezeti változóban lehet; lásd TELEPITES.md)." % the_hu)
        en = ("%s rejected the key (401). Check that the key is correct and has not expired "
              "(keys live only in environment variables; see TELEPITES.md)." % name)
    elif status == "forbidden":
        hu = ("A kulcs érvényes, de ehhez az adathoz nincs jogosultságod (403).")
        en = ("The key is valid but you are not entitled to this data (403).")
        if source == "scopus":
            hu += (" Scopusnál ez általában azt jelenti, hogy nem az intézményi hálózatról futtatod, vagy "
                   "intézményi token (MA_SCOPUS_INSTTOKEN) kell.")
            en += (" For Scopus this usually means you are not on your institution's network, or an "
                   "institutional token (MA_SCOPUS_INSTTOKEN) is required.")
    elif status == "rate_limited":
        hu = ("Elfogyott a lekérdezési keret (%s); visszaáll: %s. Addig ezt a forrást kihagyjuk." % (name, reset_hu))
        en = ("The request quota is used up (%s); it resets at %s. Until then this source is skipped." % (name, reset_en))
        if source == "openalex":
            hu += " OpenAlexnél ingyenes kulccsal (MA_OPENALEX_APIKEY) saját keretet kapsz."
            en += " With a free OpenAlex key (MA_OPENALEX_APIKEY) you get your own quota."
        elif source == "scopus":
            hu += " A Scopus heti kvótája a kulcshoz tartozik; várj a visszaállásig, vagy kérj nagyobb kvótát az Elseviertől."
            en += " The Scopus weekly quota belongs to the key; wait for the reset or ask Elsevier for a larger quota."
    elif status == "unreachable":
        hu = ("%s nem érhető el innen (hálózat, proxy vagy tűzfal). A többi forrással folytatjuk; később: "
              "python -m metaelemzes.headhunter sources --check." % the_hu)
        en = ("%s cannot be reached from here (network, proxy or firewall). We continue with the other sources; "
              "later run: python -m metaelemzes.headhunter sources --check." % name)
    elif status == "disabled":
        hu = "%s ki van kapcsolva ebben a projektben (bekapcsolás: sources set <projekt> --enable %s)." % (the_hu, source)
        en = "%s is disabled in this project (enable: sources set <project> --enable %s)." % (name, source)
    else:
        hu = "%s állapota még nem ismert (futtasd: sources --check)." % the_hu
        en = "The status of %s is not known yet (run: sources --check)." % name
    if detail:
        if isinstance(detail, dict):
            hu += " " + detail.get("hu", "")
            en += " " + detail.get("en", "")
        else:
            hu += " " + str(detail)
            en += " " + str(detail)
    return {"hu": redact(hu.strip()), "en": redact(en.strip())}


# ---------------------------------------------------------------------------------------------
# Kivételek
# ---------------------------------------------------------------------------------------------

class SourceUnavailable(Exception):
    """A forrás most nem használható; a lépés a többi forrással részlegesen folytatódik (H014).

    Attribútumok: ``source``, ``status`` (``unreachable`` | ``rate_limited`` | ``unauthorized`` |
    ``forbidden`` | ``not_configured``), ``reset_at`` (UTC ts vagy ``None``), ``explain`` (``{hu, en}``),
    ``http_status``, ``endpoint`` (redaktált), ``body_excerpt`` (redaktált, ≤ 300 karakter), ``headers``
    (redaktált válaszfejlécek)."""

    def __init__(self, source, status, explain=None, reset_at=None, http_status=None, endpoint=None,
                 body_excerpt=None, headers=None, detail=None):
        self.source = source
        self.status = status
        self.reset_at = reset_at
        self.http_status = http_status
        self.endpoint = redact(endpoint) if endpoint else None
        self.body_excerpt = redact(body_excerpt)[:300] if body_excerpt else None
        self.headers = redact_headers(headers or {})
        self.explain = explain or status_explain(source, status, reset_at, detail)
        Exception.__init__(self, self.explain["hu"])

    def to_dict(self):
        return {"source": self.source, "status": self.status, "reset_at": self.reset_at,
                "http_status": self.http_status, "endpoint": self.endpoint, "message": dict(self.explain)}

    def copy(self):
        return SourceUnavailable(self.source, self.status, explain=dict(self.explain), reset_at=self.reset_at,
                                 http_status=self.http_status, endpoint=self.endpoint,
                                 body_excerpt=self.body_excerpt, headers=self.headers)


class HttpError(Exception):
    """Nem átmeneti, nem elérhetőségi HTTP-hiba (pl. 400 hibás lekérdezés). A szöveg redaktált."""

    def __init__(self, source, status, endpoint=None, body_excerpt=None):
        self.source = source
        self.status = status
        self.endpoint = redact(endpoint) if endpoint else None
        self.body_excerpt = redact(body_excerpt)[:300] if body_excerpt else None
        self.explain = {
            "hu": "%s HTTP %s hibát adott (%s). Részlet: %s" % (_hu_the(source), status, self.endpoint or "?",
                                                               self.body_excerpt or "—"),
            "en": "%s returned HTTP %s (%s). Excerpt: %s" % (source_name(source), status, self.endpoint or "?",
                                                         self.body_excerpt or "—"),
        }
        Exception.__init__(self, self.explain["hu"])


class ParseError(ValueError):
    """A válasz nem értelmezhető JSON/XML-ként (pl. proxy HTML-hibaoldala)."""


class CassetteMiss(RuntimeError):
    """Lejátszás közben ismeretlen kérés — a teszt hibát ad, nem megy ki a hálózatra."""


# ---------------------------------------------------------------------------------------------
# Válasz
# ---------------------------------------------------------------------------------------------

class Response(object):
    """HTTP-válasz. ``status`` (int), ``headers`` (kisbetűs kulcsú dict), ``text`` (str), ``json()``,
    ``xml()``, ``from_cache`` (bool), ``retrieval`` (a ``common.v1#/$defs/retrieval`` alakú dict)."""

    def __init__(self, status, headers, body, url, source, from_cache=False, cache_key=None, at=None):
        self.status = int(status)
        self.headers = dict((str(k).lower(), str(v)) for k, v in (headers or {}).items())
        self.body = body if isinstance(body, bytes) else (body or "").encode("utf-8")
        self.url = url  # redaktált, kanonikus
        self.source = source
        self.from_cache = bool(from_cache)
        self.cache_key = cache_key
        self.at = at or utc_ts()

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def text(self):
        charset = "utf-8"
        m = re.search(r"charset=([\w.-]+)", self.headers.get("content-type", ""), re.I)
        if m:
            charset = m.group(1)
        try:
            return self.body.decode(charset, "replace")
        except LookupError:
            return self.body.decode("utf-8", "replace")

    def json(self):
        return parse_json(self.text, source=self.source, endpoint=self.url)

    def xml(self):
        return parse_xml(self.text, source=self.source, endpoint=self.url)

    @property
    def retrieval(self):
        return {"source": self.source,
                "endpoint": (self.url or "")[:500], "at": self.at, "http_status": self.status,
                "cache_key": self.cache_key}

    def __repr__(self):  # pragma: no cover - csak hibakereséshez
        return "<Response %s %s %s%s>" % (self.source, self.status, self.url, " (cache)" if self.from_cache else "")


# ---------------------------------------------------------------------------------------------
# JSON/XML segédek
# ---------------------------------------------------------------------------------------------

def parse_json(text, source=None, endpoint=None):
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ParseError(redact("%s: a válasz nem JSON (%s): %s" % (source or "?", endpoint or "?", str(exc)[:120])))


def parse_xml(text, source=None, endpoint=None):
    """XML-elemzés (ElementTree; külső entitást nem old fel). A DOCTYPE-ot eldobja."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    cleaned = re.sub(r"<!DOCTYPE[^>\[]*(\[[^\]]*\])?\s*>", "", text or "", count=1)
    cleaned = re.sub(r"^\s*<\?xml[^>]*\?>", "", cleaned, count=1)
    try:
        return ET.fromstring(cleaned)
    except ET.ParseError as exc:
        raise ParseError(redact("%s: a válasz nem XML (%s): %s" % (source or "?", endpoint or "?", str(exc)[:120])))


def local_name(tag):
    """Névtér nélküli elemnév (``{ns}title`` → ``title``)."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def iter_local(el, name):
    """Az összes ``name`` nevű leszármazott (névtértől függetlenül)."""
    for sub in el.iter():
        if local_name(sub.tag) == name:
            yield sub


def find_local(el, name):
    for sub in iter_local(el, name):
        return sub
    return None


def xml_text(el):
    """Egy elem összefűzött szövege, szóközre normalizálva (``None`` → ``""``)."""
    if el is None:
        return ""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def get_path(obj, *keys, **kw):
    """Biztonságos beágyazott elérés: ``get_path(d, "a", 0, "b", default=None)``."""
    default = kw.get("default")
    cur = obj
    for k in keys:
        try:
            if isinstance(cur, dict):
                cur = cur.get(k)
            elif isinstance(cur, (list, tuple)) and isinstance(k, int):
                cur = cur[k] if -len(cur) <= k < len(cur) else None
            else:
                return default
        except Exception:
            return default
        if cur is None:
            return default
    return cur


def as_list(x):
    if x is None:
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


def to_int(x, default=None):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------------------------
# Lapozó iterátor (keresések: találatszám, letöltött szám, teljesség — PRISMA-S, H012)
# ---------------------------------------------------------------------------------------------

class Paged(object):
    """Lapozó iterátor. ``fetch(state) -> (items, total, next_state|None)``; az első hívásnál ``state=None``.

    Iterálás után: ``total`` (a forrás szerinti találatszám), ``retrieved`` (letöltött), ``complete``
    (minden találat megjött-e), ``stopped`` (``"end"``/``"cap"``/``"error"``), ``error``
    (``SourceUnavailable``, ha a lapozás közben szakadt meg), ``pages``.

    Az ELSŐ lap hibája kivételként jön (semmi sem jött le → a forrás elérhetetlen, H014); a későbbi lap
    hibája nem dob kivételt: a már letöltött tételek megmaradnak, ``complete = False`` (H012)."""

    def __init__(self, source, fetch, max_results=None, query=None, raise_partial=False):
        self.source = source
        self._fetch = fetch
        self.max_results = max_results
        self.query = query
        self.raise_partial = raise_partial
        self.total = None
        self.retrieved = 0
        self.complete = None
        self.stopped = None
        self.error = None
        self.pages = 0

    def __iter__(self):
        self.retrieved = 0
        self.pages = 0
        self.error = None
        state = None
        while True:
            try:
                items, total, next_state = self._fetch(state)
            except SourceUnavailable as exc:
                if self.pages == 0 or self.raise_partial:
                    raise
                self.error = exc
                self.complete = False
                self.stopped = "error"
                return
            self.pages += 1
            if total is not None:
                self.total = total
            items = list(items or [])
            for it in items:
                if self.max_results is not None and self.retrieved >= self.max_results:
                    self.stopped = "cap"
                    self.complete = self.total is not None and self.retrieved >= self.total
                    return
                self.retrieved += 1
                yield it
            if self.max_results is not None and self.retrieved >= self.max_results:
                self.stopped = "cap"
                self.complete = self.total is not None and self.retrieved >= self.total
                return
            if not items or next_state is None or (self.total is not None and self.retrieved >= self.total):
                self.stopped = "end"
                self.complete = True if self.total is None else self.retrieved >= self.total
                return
            state = next_state

    def all(self):
        return list(self)

    def summary(self):
        """A keresési napló számára (``count_total``, ``count_retrieved``, ``complete``)."""
        return {"count_total": self.total, "count_retrieved": self.retrieved,
                "complete": bool(self.complete), "stopped": self.stopped,
                "error": self.error.to_dict() if self.error else None}


def safe_call(fn, *args, **kwargs):
    """``(eredmény, None)`` vagy ``(None, SourceUnavailable)`` — kíméletes leromlás egy hívásra."""
    try:
        return fn(*args, **kwargs), None
    except SourceUnavailable as exc:
        return None, exc


# ---------------------------------------------------------------------------------------------
# Sebességkorlát (gépenként)
# ---------------------------------------------------------------------------------------------

class RateLimiter(object):
    """Egyszerű, szálbiztos intervallum-korlát kulcsonként (gépnév)."""

    def __init__(self, clock=None, sleep=None):
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self._next = {}
        self._lock = threading.Lock()

    def wait(self, key, rate):
        if not rate or rate <= 0:
            return 0.0
        interval = 1.0 / float(rate)
        with self._lock:
            now = self._clock()
            slot = max(now, self._next.get(key, 0.0))
            self._next[key] = slot + interval
        delay = slot - now
        if delay > 0:
            self._sleep(delay)
        return max(delay, 0.0)


# ---------------------------------------------------------------------------------------------
# Kazetták (rögzítés redaktálással és lejátszás)
# ---------------------------------------------------------------------------------------------

CASSETTE_SCHEMA = "szk.ma.headhunter.cassette/v1"

#: JSON-kulcsok, amelyek absztraktot/teljes szöveget tartalmaznak — kazettába nem kerülnek (N4)
JSON_REDACT_KEYS = frozenset(["abstractText", "abstract", "abstract_inverted_index", "dc:description",
                              "detailedDescription", "fullText", "full_text"])

_XML_REDACT = (
    ("AbstractText", re.compile(r"(<AbstractText\b[^>]*>)(.*?)(</AbstractText>)", re.S)),
    ("OtherAbstract", re.compile(r"(<OtherAbstract\b[^>]*>)(.*?)(</OtherAbstract>)", re.S)),
    ("abstract", re.compile(r"(<abstract\b[^>]*>)(.*?)(</abstract>)", re.S)),
    ("trans-abstract", re.compile(r"(<trans-abstract\b[^>]*>)(.*?)(</trans-abstract>)", re.S)),
    ("body", re.compile(r"(<body\b[^>]*>)(.*?)(</body>)", re.S)),
)


def redact_xml_text(text):
    """Absztrakt és JATS-törzs tartalmának kivágása. Visszaad: (szöveg, redactions-lista)."""
    labels = []
    for label, rx in _XML_REDACT:
        if rx.search(text):
            text = rx.sub(lambda m: m.group(1) + REDACTED + m.group(3), text)
            labels.append(label)
    return text, labels


#: ``slim=True`` rögzítésnél elhagyható, a kód által nem használt terjedelmes részek (címkéjük a ``redactions``-ben)
SLIM_JSON_KEYS = frozenset(["authorAffiliationDetailsList", "affiliation", "authorIdList", "fullTextUrlList",
                            "grantsList"])
_SLIM_XML = (
    ("slim:ReferenceList", re.compile(r"<ReferenceList\b[^>]*>.*?</ReferenceList>", re.S), "<ReferenceList/>"),
    ("slim:AffiliationInfo", re.compile(r"<AffiliationInfo\b[^>]*>.*?</AffiliationInfo>", re.S), ""),
)


def slim_json_obj(obj, labels=None):
    """A ``SLIM_JSON_KEYS`` kulcsok elhagyása (rekurzív). Visszaad: (obj, címkék)."""
    if labels is None:
        labels = []
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in SLIM_JSON_KEYS:
                if "slim:" + k not in labels:
                    labels.append("slim:" + k)
                continue
            out[k] = slim_json_obj(v, labels)[0]
        return out, labels
    if isinstance(obj, list):
        return [slim_json_obj(v, labels)[0] for v in obj], labels
    return obj, labels


def slim_xml_text(text):
    labels = []
    for label, rx, repl in _SLIM_XML:
        if rx.search(text):
            text = rx.sub(repl, text)
            labels.append(label)
    return text, labels


def redact_json_obj(obj, labels=None):
    """Az absztrakt-kulcsok értékének kivágása (rekurzív). Visszaad: (obj, redactions-lista)."""
    if labels is None:
        labels = []
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in JSON_REDACT_KEYS and v not in (None, "", [], {}):
                out[k] = REDACTED
                if k not in labels:
                    labels.append(k)
            else:
                out[k] = redact_json_obj(v, labels)[0]
        return out, labels
    if isinstance(obj, list):
        return [redact_json_obj(v, labels)[0] for v in obj], labels
    return obj, labels


def redact_strings(obj, env=None):
    """A ``redact()`` alkalmazása egy JSON-szerkezet minden szöveg-értékére (a szerkezet ép marad)."""
    if isinstance(obj, dict):
        return dict((k, redact_strings(v, env)) for k, v in obj.items())
    if isinstance(obj, list):
        return [redact_strings(v, env) for v in obj]
    if isinstance(obj, str):
        return redact(obj, env)
    return obj


def _cassette_headers(headers):
    out = {}
    for k, v in (headers or {}).items():
        lk = str(k).lower()
        if lk in SECRET_HEADERS:
            continue
        if any(lk.startswith(p) for p in CASSETTE_HEADER_PREFIXES):
            out[lk] = str(v)
    return dict(sorted(out.items()))


def _safe_name(text):
    s = re.sub(r"[^a-z0-9_.-]+", "-", str(text).lower()).strip("-.")
    if len(s) < 3:
        s = (s + "-cassette")[:20]
    if not re.match(r"^[a-z0-9]", s):
        s = "c" + s
    return s[:81]


def dump_json(doc):
    """Kanonikus formázás (a motor szerződés-konvenciója): indent=2, ensure_ascii=False, záró újsor."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _atomic_write(path, data):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = "%s.tmp-%d-%d" % (path, os.getpid(), threading.get_ident())
    mode = "wb" if isinstance(data, bytes) else "w"
    kw = {} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": "\n"}
    with open(tmp, mode, **kw) as fh:
        fh.write(data)
    os.replace(tmp, path)


class CassetteRecorder(object):
    """HTTP-válaszok rögzítése kazettába — csak fejlesztői/tesztcélra.

    Redaktál: tiltott fejlécek (``Authorization``, ``X-ELS-*``, ``Cookie``, ``Set-Cookie``, ``report-to``,
    ``nel`` …) — a válaszfejlécekből csak a ``content-type``, ``retry-after``, ``x-ratelimit-*``,
    ``x-els-status`` marad; URL-paraméterek ``api_key``, ``apiKey``, ``insttoken``, ``mailto``, ``email``,
    ``tool`` (elhagyva); absztrakt és JATS-törzs (``redactions[]``). Írás előtt bájtszinten ellenőrzi, hogy
    nyilvántartott titok nincs benne — ha van, ``SecretLeakError`` és nem ír."""

    def __init__(self, path, name=None, origin="recorded", notes=None, license_note=None, unverified_live=False,
                 env=None, clock=None, slim=False):
        self.path = path
        self.slim = bool(slim)
        self.name = _safe_name(name or os.path.splitext(os.path.basename(path))[0])
        self.origin = origin
        self.notes = notes
        self.license_note = license_note
        self.unverified_live = bool(unverified_live)
        self.env = env
        self._clock = clock or time.time
        self.interactions = []
        self._lock = threading.Lock()

    def record(self, method, url, body, status, headers, resp_body, accept=None):
        req = {"method": method, "url": canonical_url(url, self.env)}
        if accept:
            req["headers"] = {"accept": accept}
        if body is not None:
            req["body"] = canonical_body(body, self.env)
        hdrs = _cassette_headers(headers)
        text = resp_body.decode("utf-8", "replace") if isinstance(resp_body, bytes) else (resp_body or "")
        resp = {"status": int(status), "headers": hdrs}
        ctype = hdrs.get("content-type", "")
        redactions = []
        parsed = None
        if "json" in ctype or (text[:1] in ("{", "[") and "xml" not in ctype):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
        if parsed is not None:
            parsed, redactions = redact_json_obj(parsed)
            if self.slim:
                parsed, more = slim_json_obj(parsed)
                redactions = redactions + more
            resp["body_json"] = redact_strings(parsed, self.env)
        else:
            text, redactions = redact_xml_text(text)
            if self.slim:
                text, more = slim_xml_text(text)
                redactions = redactions + more
            resp["body_text"] = redact(text, self.env)
        if redactions:
            resp["redactions"] = redactions
        with self._lock:
            self.interactions.append({"request": req, "response": resp, "delay_ms": None})

    def document(self):
        doc = {"schema": CASSETTE_SCHEMA, "name": self.name, "recorded_at": utc_ts(self._clock()),
               "origin": self.origin, "unverified_live": self.unverified_live,
               "license_note": self.license_note, "notes": self.notes,
               "interactions": list(self.interactions)}
        return doc

    def save(self, path=None):
        """A kazetta kiírása (atomikusan). Üres kazettát nem ír (a séma ``minItems: 1``)."""
        if not self.interactions:
            return None
        text = dump_json(self.document())
        leaks = find_secret_leaks(text, self.env)
        if leaks:
            raise SecretLeakError("A kazetta titkot tartalmazna (%s) — nem írtam ki." % ", ".join(leaks))
        _atomic_write(path or self.path, text)
        return path or self.path


def _iter_cassette_files(spec):
    for part in str(spec).split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        if os.path.isdir(part):
            for root, _dirs, files in os.walk(part):
                for fn in sorted(files):
                    if fn.endswith(".json"):
                        yield os.path.join(root, fn)
        else:
            yield part


class CassettePlayer(object):
    """Rögzített válaszok lejátszása. Illesztés: metódus + kanonikus (redaktált, rendezett) URL + törzs.

    Ugyanarra a kérésre több rögzített válasz sorban jön (pl. 500, majd 200 — újrapróbálás teszteléséhez);
    a sor végén az utolsó ismétlődik. Ismeretlen kérés → ``CassetteMiss`` (és a ``misses`` listába kerül)."""

    def __init__(self, paths, env=None):
        self.env = env
        self._index = {}
        self._pos = {}
        self.misses = []
        self.played = []
        self.files = []
        self.documents = []
        if isinstance(paths, (list, tuple)):
            spec = os.pathsep.join(str(p) for p in paths)
        else:
            spec = paths
        for fn in _iter_cassette_files(spec):
            with open(fn, encoding="utf-8") as fh:
                doc = json.load(fh)
            if doc.get("schema") != CASSETTE_SCHEMA:
                continue
            self.files.append(fn)
            self.documents.append(doc)
            for it in doc.get("interactions", []):
                self.add_interaction(it, origin=doc.get("origin"), name=doc.get("name"))

    @classmethod
    def from_documents(cls, docs, env=None):
        p = cls([], env=env)
        for doc in docs:
            p.documents.append(doc)
            for it in doc.get("interactions", []):
                p.add_interaction(it, origin=doc.get("origin"), name=doc.get("name"))
        return p

    def _key(self, method, url, body):
        return (str(method or "GET").upper(), canonical_url(url, self.env),
                canonical_body(body, self.env) if body is not None else None)

    def add_interaction(self, it, origin=None, name=None):
        req = it.get("request", {})
        key = self._key(req.get("method", "GET"), req.get("url", ""), req.get("body"))
        entry = dict(it)
        entry["_origin"] = origin
        entry["_name"] = name
        self._index.setdefault(key, []).append(entry)

    def play(self, method, url, body=None):
        key = self._key(method, url, body)
        seq = self._index.get(key)
        if not seq:
            self.misses.append({"method": key[0], "url": key[1]})
            raise CassetteMiss("Ismeretlen kérés a kazettában (nem megy ki a hálózatra): %s %s" % (key[0], key[1]))
        i = self._pos.get(key, 0)
        it = seq[min(i, len(seq) - 1)]
        self._pos[key] = i + 1
        resp = it.get("response", {})
        if "body_json" in resp:
            body_bytes = json.dumps(resp["body_json"], ensure_ascii=False).encode("utf-8")
        else:
            body_bytes = (resp.get("body_text") or "").encode("utf-8")
        headers = dict(resp.get("headers") or {})
        self.played.append({"method": key[0], "url": key[1], "status": resp.get("status"),
                            "cassette": it.get("_name")})
        return int(resp.get("status", 200)), headers, body_bytes


def cassette_from_env(env=None):
    """``(player, recorder)`` a ``MA_HH_CASSETTE`` / ``MA_HH_CASSETTE_FILE`` alapján (``off``: ``(None, None)``)."""
    mode = (get_env(ENV_CASSETTE, env) or "off").lower()
    if mode in ("", "off", "0", "no", "none"):
        return None, None
    path = get_env(ENV_CASSETTE_FILE, env)
    if not path:
        raise ValueError("MA_HH_CASSETTE=%s mellé add meg a kazettát: MA_HH_CASSETTE_FILE=<fájl vagy mappa>." % mode)
    if mode == "replay":
        return CassettePlayer(path, env=env), None
    if mode == "record":
        return None, CassetteRecorder(path, env=env)
    raise ValueError("Ismeretlen MA_HH_CASSETTE érték: %r (record|replay|off)." % mode)


# ---------------------------------------------------------------------------------------------
# Proxy és TLS
# ---------------------------------------------------------------------------------------------

def proxies_from_env(env=None):
    """A ``urllib.request.getproxies_environment`` megfelelője tetszőleges leképezésre: a ``*_PROXY``
    változók (a kisbetűs alak elsőbbséget élvez; ``no_proxy``-t a urllib külön kezeli)."""
    src = os.environ if env is None else env
    proxies = {}
    for name, value in src.items():
        lname = str(name).lower()
        if value and lname.endswith("_proxy"):
            proxies[lname[:-6]] = value
    if "REQUEST_METHOD" in src:  # CVE-2016-1000110: CGI alatt a HTTP_PROXY nem megbízható
        proxies.pop("http", None)
    for name, value in src.items():
        if str(name).endswith("_proxy"):
            lname = str(name).lower()
            if value:
                proxies[lname[:-6]] = value
            else:
                proxies.pop(lname[:-6], None)
    proxies.pop("no", None)
    return proxies


def build_opener(env=None, ssl_context=None):
    """urllib-opener a környezet proxyjaival (``ProxyHandler``) és a rendszer CA-ival (``SSL_CERT_FILE``)."""
    ctx = ssl_context or ssl.create_default_context()
    handlers = [urllib.request.ProxyHandler(proxies_from_env(env)), urllib.request.HTTPSHandler(context=ctx)]
    return urllib.request.build_opener(*handlers)


def _tls_hint():
    return {"hu": ("TLS-tanúsítvány hiba. Proxy mögött állítsd be az SSL_CERT_FILE változót a proxy CA-fájljára; "
                   "macOS-en a python.org-os Pythonnál futtasd az 'Install Certificates.command'-ot."),
            "en": ("TLS certificate error. Behind a proxy set SSL_CERT_FILE to the proxy's CA bundle; on macOS with "
                   "the python.org Python run 'Install Certificates.command'.")}


def project_cache_dir(project_dir):
    """A projekt metaadat-gyorsítótára: ``<projekt>/01_kereses/headhunter/cache/http`` (gitignore; N4)."""
    return os.path.join(project_dir, "01_kereses", "headhunter", "cache", "http")


def user_fulltext_cache_dir(env=None):
    """A teljes szöveg opcionális gyorsítótára a PROJEKTEN KÍVÜL (N4): ``MA_HH_CACHE_DIR`` vagy az OS
    felhasználói cache-mappája. ``None``, ha a felhasználó nem kérte (``MA_HH_CACHE_DIR`` nincs beállítva)."""
    base = get_env(ENV_CACHE_DIR, env)
    if not base:
        return None
    return os.path.join(base, "fulltext")


def default_fulltext_cache_dir(env=None):
    """Az alapértelmezett (javasolt) teljesszöveg-gyorsítótár helye az OS szerint."""
    src = os.environ if env is None else env
    if os.name == "nt":
        root = src.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(root, "metaelemzes", "headhunter", "fulltext")
    root = src.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(root, "metaelemzes", "headhunter", "fulltext")


def _looks_like_fulltext(body):
    head = body[:400000] if isinstance(body, bytes) else str(body)[:400000].encode("utf-8", "replace")
    return b"<body" in head and (b"<sec" in head or b"<article-meta" in head)


def _parse_retry_after(value, now):
    """``Retry-After`` (másodperc vagy HTTP-dátum) → várakozás másodpercben."""
    if value is None:
        return None
    v = str(value).strip()
    if re.match(r"^\d+(\.\d+)?$", v):
        return float(v)
    try:
        dt = email.utils.parsedate_to_datetime(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, dt.timestamp() - now)
    except (TypeError, ValueError, IndexError):
        return None


def _parse_reset(value, now):
    """``X-RateLimit-Reset``: epoch (Scopus) vagy hátralévő másodperc (OpenAlex) → várakozás másodpercben."""
    f = None
    try:
        f = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if f > 1e9:
        return max(0.0, f - now)
    return max(0.0, f)


# ---------------------------------------------------------------------------------------------
# HTTP-kliens
# ---------------------------------------------------------------------------------------------

_RETRY_STATUSES = (500, 502, 503, 504)
_NONRETRY_NET = ("tunnel connection failed", "name or service not known", "nodename nor servname",
                 "getaddrinfo failed", "no address associated", "certificate verify failed",
                 "connection refused")


class HttpClient(object):
    """Udvarias HTTP-kliens a forráskliensek alá.

    Paraméterek (a 20.5 szerződés szerint, kiegészítve): ``cache_dir`` (a projekt ``cache/http`` mappája;
    ``None``: nincs gyorsítótár), ``offline`` (csak gyorsítótárból), ``player``/``recorder`` (kazetta; ha
    mindkettő ``None``, a ``MA_HH_CASSETTE`` környezeti változó dönt), ``clock``/``sleep`` (tesztben
    injektálható), ``timeout`` (30 s), ``max_retries``, ``max_retry_after`` (ennél hosszabb ``Retry-After``
    → ``rate_limited``), ``rates`` (gépnév → kérés/s), ``env`` (környezet-leképezés), ``opener``,
    ``log`` (függvény: redaktált eseménydict-et kap), ``rng`` (jitterhez), ``fulltext_cache_dir``.

    A ``source_state`` a forrásonként legutóbb megfigyelt állapotot tartja (``ok`` / ``rate_limited`` …);
    a ``stats`` a kérés-, gyorsítótár- és hibaszámlálókat (futásnaplóhoz)."""

    def __init__(self, cache_dir=None, offline=False, player=None, recorder=None, clock=None, sleep=None,
                 timeout=DEFAULT_TIMEOUT, max_retries=DEFAULT_MAX_RETRIES, max_retry_after=DEFAULT_MAX_RETRY_AFTER,
                 backoff_base=1.0, rates=None, env=None, opener=None, log=None, rng=None, fulltext_cache_dir=None,
                 use_env_cassette=True):
        self.env = env
        self.cache_dir = cache_dir
        self.offline = bool(offline)
        if player is None and recorder is None and use_env_cassette:
            player, recorder = cassette_from_env(env)
        self.player = player
        self.recorder = recorder
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.max_retry_after = float(max_retry_after)
        self.backoff_base = float(backoff_base)
        self.rates = dict(DEFAULT_HOST_RATES)
        if rates:
            self.rates.update(rates)
        self._opener = opener
        self._log = log
        self._rng = rng or random.Random()
        self.fulltext_cache_dir = fulltext_cache_dir if fulltext_cache_dir is not None else user_fulltext_cache_dir(env)
        self.limiter = RateLimiter(self._clock, self._sleep)
        self._blocked = {}
        self._lock = threading.Lock()
        self.source_state = {}
        self.stats = {}
        self.user_agent = user_agent(env)

    # -- segédek ------------------------------------------------------------------------------

    @property
    def opener(self):
        if self._opener is None:
            self._opener = build_opener(self.env)
        return self._opener

    def set_rate(self, host, rate):
        """Gépenkénti sebességkorlát módosítása (pl. NCBI-kulccsal 10 kérés/s)."""
        self.rates[host] = float(rate)

    def now(self):
        return self._clock()

    def _stat(self, source, key):
        with self._lock:
            st = self.stats.setdefault(source, {"requests": 0, "cache_hits": 0, "errors": 0, "retries": 0})
            st[key] = st.get(key, 0) + 1

    def _emit(self, **event):
        if self._log is None:
            return
        safe = {}
        for k, v in event.items():
            safe[k] = redact(v, self.env) if isinstance(v, str) else v
        try:
            self._log(safe)
        except Exception:  # pragma: no cover - a napló hibája nem állíthatja meg a lekérést
            pass

    def _set_state(self, source, status, http_status=None, reset_at=None):
        self.source_state[source] = {"status": status, "at": utc_ts(self._clock()), "http_status": http_status,
                                     "reset_at": reset_at}

    def blocked(self, bucket):
        """Ha a vödör (forrás vagy forrás:alosztály) le van tiltva (kvóta/kulcs), a tárolt kivétel; különben ``None``."""
        with self._lock:
            info = self._blocked.get(bucket)
            if not info:
                return None
            exc, until = info
            if until is not None and self._clock() >= until:
                del self._blocked[bucket]
                return None
            return exc

    def _block(self, bucket, exc, until=None):
        with self._lock:
            self._blocked[bucket] = (exc, until)

    def unblock(self, bucket=None):
        with self._lock:
            if bucket is None:
                self._blocked.clear()
            else:
                self._blocked.pop(bucket, None)

    def cache_key(self, method, canon, body=None):
        h = hashlib.sha256()
        h.update(("%s %s\n" % (method, canon)).encode("utf-8"))
        if body is not None:
            h.update(canonical_body(body, self.env).encode("utf-8"))
        return h.hexdigest()

    def _cache_path(self, base, key):
        return os.path.join(base, key[:2], key + ".json")

    def _cache_get(self, base, key, ttl_days):
        if not base:
            return None
        path = self._cache_path(base, key)
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return None
        stored = doc.get("stored_at", 0)
        if ttl_days is not None and self._clock() - float(stored) > float(ttl_days) * 86400.0:
            return None
        if "body_b64" in doc:
            body = base64.b64decode(doc["body_b64"])
        else:
            body = (doc.get("body_text") or "").encode("utf-8")
        return int(doc.get("status", 200)), doc.get("headers") or {}, body

    def _cache_put(self, base, key, canon, source, status, headers, body):
        if not base:
            return
        doc = {"url": canon, "source": source, "status": status, "headers": _cassette_headers(headers),
               "stored_at": self._clock()}
        try:
            doc["body_text"] = body.decode("utf-8")
        except UnicodeDecodeError:
            doc["body_b64"] = base64.b64encode(body).decode("ascii")
        text = json.dumps(doc, ensure_ascii=False)
        if find_secret_leaks(text, self.env):
            return  # titok nem kerülhet a gyorsítótárba (H016) — inkább nem tárolunk
        try:
            _atomic_write(self._cache_path(base, key), text)
        except OSError:
            pass

    def _build_url(self, url, params):
        if not params:
            return url
        if isinstance(params, dict):
            items = [(k, v) for k, v in params.items() if v is not None]
        else:
            items = [(k, v) for k, v in params if v is not None]
        q = urllib.parse.urlencode([(k, str(v)) for k, v in items])
        return url + ("&" if "?" in url else "?") + q

    def _accept(self, accept):
        return {"json": "application/json", "xml": "application/xml, text/xml;q=0.9, */*;q=0.1",
                "text": "text/plain, */*;q=0.1", None: "*/*"}.get(accept, accept)

    def _backoff(self, attempt):
        return self.backoff_base * (2 ** attempt) + self._rng.uniform(0, 0.5 * self.backoff_base)

    # -- nyilvános API -----------------------------------------------------------------------

    def get(self, source, url, params=None, headers=None, accept="json", cache=True, ttl_days=30,
            allow_status=(), bucket=None, timeout=None):
        """GET-kérés. Visszaad: ``Response`` (2xx, 404/410 és az ``allow_status`` kódjai).

        Kivételek: ``SourceUnavailable`` (401/403/429 hosszú várakozással, hálózati hiba, tartós 5xx,
        offline gyorsítótár-hiány), ``HttpError`` (egyéb 4xx), ``CassetteMiss`` (lejátszás, ismeretlen kérés).

        ``cache``: ``True`` (projekt-gyorsítótár; teljes szöveg sosem kerül bele), ``False`` vagy
        ``"fulltext"`` (a projekten KÍVÜLI teljesszöveg-gyorsítótár, ha be van állítva; 7 nap)."""
        return self.request("GET", source, url, params=params, headers=headers, accept=accept, cache=cache,
                            ttl_days=ttl_days, allow_status=allow_status, bucket=bucket, timeout=timeout)

    def post(self, source, url, data=None, params=None, headers=None, accept="json", cache=True, ttl_days=30,
             allow_status=(), bucket=None, timeout=None):
        """POST (űrlap-törzzel) — pl. hosszú E-utilities lekérdezésekhez."""
        return self.request("POST", source, url, params=params, data=data, headers=headers, accept=accept,
                            cache=cache, ttl_days=ttl_days, allow_status=allow_status, bucket=bucket,
                            timeout=timeout)

    def request(self, method, source, url, params=None, data=None, headers=None, accept="json", cache=True,
                ttl_days=30, allow_status=(), bucket=None, timeout=None):
        method = method.upper()
        bucket = bucket or source
        full_url = self._build_url(url, params)
        body = None
        if data is not None:
            if isinstance(data, dict):
                data = [(k, v) for k, v in data.items() if v is not None]
            if isinstance(data, (list, tuple)):
                body = urllib.parse.urlencode([(k, str(v)) for k, v in data])
            elif isinstance(data, bytes):
                body = data.decode("utf-8")
            else:
                body = str(data)
        canon = canonical_url(full_url, self.env)
        allow = tuple(allow_status or ())

        exc = self.blocked(bucket)
        if exc is not None:
            raise exc.copy()

        # gyorsítótár (a teljes szöveg sosem a projektben)
        if cache == "fulltext":
            cache_base, ttl = self.fulltext_cache_dir, 7
        elif cache:
            cache_base, ttl = self.cache_dir, ttl_days
        else:
            cache_base, ttl = None, ttl_days
        key = self.cache_key(method, canon, body) if (cache_base or self.offline) else None
        if cache_base and key:
            hit = self._cache_get(cache_base, key, ttl)
            if hit is not None:
                self._stat(source, "cache_hits")
                status, hdrs, raw = hit
                self._emit(event="http", source=source, method=method, url=canon, status=status, from_cache=True)
                return Response(status, hdrs, raw, canon, source, from_cache=True, cache_key=key,
                                at=utc_ts(self._clock()))
        if self.offline and self.player is None:
            raise SourceUnavailable(source, "unreachable", endpoint=canon, detail={
                "hu": "Offline mód: ez a kérés nincs a gyorsítótárban.",
                "en": "Offline mode: this request is not in the cache."})

        attempt = 0
        while True:
            status, hdrs, raw, net_exc = self._send(method, source, full_url, canon, body, headers, accept, timeout)
            now = self._clock()
            if net_exc is not None:
                msg = str(getattr(net_exc, "reason", net_exc)).lower()
                retryable = not any(s in msg for s in _NONRETRY_NET) and not isinstance(net_exc, ssl.SSLError)
                if retryable and attempt < self.max_retries:
                    self._stat(source, "retries")
                    self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                self._stat(source, "errors")
                detail = _tls_hint() if ("certificate" in msg or isinstance(net_exc, ssl.SSLError)) else {
                    "hu": "Részlet: %s" % redact(str(getattr(net_exc, "reason", net_exc)), self.env)[:200],
                    "en": "Detail: %s" % redact(str(getattr(net_exc, "reason", net_exc)), self.env)[:200]}
                err = SourceUnavailable(source, "unreachable", endpoint=canon, detail=detail)
                self._set_state(source, "unreachable")
                self._emit(event="http_error", source=source, method=method, url=canon, status=None,
                           error=str(err))
                raise err

            self._emit(event="http", source=source, method=method, url=canon, status=status, from_cache=False,
                       attempt=attempt)
            if 200 <= status < 300 or status in allow or status in (404, 410):
                if 200 <= status < 300:
                    self._set_state(source, "ok", http_status=status)
                    if cache_base and key and status == 200:
                        if cache == "fulltext" or not _looks_like_fulltext(raw):
                            self._cache_put(cache_base, key, canon, source, status, hdrs, raw)
                return Response(status, hdrs, raw, canon, source, from_cache=False, cache_key=key,
                                at=utc_ts(now))

            lower = dict((str(k).lower(), v) for k, v in (hdrs or {}).items())
            excerpt = raw[:600].decode("utf-8", "replace") if raw else None
            if status == 401:
                self._stat(source, "errors")
                err = SourceUnavailable(source, "unauthorized", http_status=401, endpoint=canon,
                                        body_excerpt=excerpt, headers=lower)
                self._set_state(source, "unauthorized", http_status=401)
                self._block(bucket, err)
                raise err
            if status == 403:
                self._stat(source, "errors")
                err = SourceUnavailable(source, "forbidden", http_status=403, endpoint=canon,
                                        body_excerpt=excerpt, headers=lower)
                self._set_state(source, "forbidden", http_status=403)
                raise err
            if status == 429:
                wait = _parse_retry_after(lower.get("retry-after"), now)
                quota = "quota" in (lower.get("x-els-status", "") + (excerpt or "")).lower()
                if wait is None and quota:
                    wait = _parse_reset(lower.get("x-ratelimit-reset"), now)
                if wait is None and not quota and attempt < self.max_retries:
                    self._stat(source, "retries")
                    self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                if wait is not None and wait <= self.max_retry_after and attempt < self.max_retries:
                    self._stat(source, "retries")
                    self._sleep(max(wait, 0.0))
                    attempt += 1
                    continue
                self._stat(source, "errors")
                reset_epoch = now + wait if wait is not None else None
                reset_at = utc_ts(reset_epoch) if reset_epoch is not None else None
                err = SourceUnavailable(source, "rate_limited", reset_at=reset_at, http_status=429, endpoint=canon,
                                        body_excerpt=excerpt, headers=lower)
                self._set_state(source, "rate_limited", http_status=429, reset_at=reset_at)
                self._block(bucket, err, until=reset_epoch if reset_epoch is not None else now + 60.0)
                raise err
            if status in _RETRY_STATUSES:
                if attempt < self.max_retries:
                    wait = _parse_retry_after(lower.get("retry-after"), now)
                    self._stat(source, "retries")
                    self._sleep(wait if (wait is not None and wait <= self.max_retry_after) else self._backoff(attempt))
                    attempt += 1
                    continue
                self._stat(source, "errors")
                err = SourceUnavailable(source, "unreachable", http_status=status, endpoint=canon,
                                        body_excerpt=excerpt, headers=lower, detail={
                                            "hu": "A szolgáltatás tartósan hibát jelez (HTTP %s)." % status,
                                            "en": "The service keeps returning errors (HTTP %s)." % status})
                self._set_state(source, "unreachable", http_status=status)
                raise err
            self._stat(source, "errors")
            raise HttpError(source, status, endpoint=canon, body_excerpt=excerpt)

    def _send(self, method, source, full_url, canon, body, headers, accept, timeout):
        """Egy kérés (kazettából vagy élőben). Visszaad: (status, headers, body, hálózati_kivétel|None)."""
        self._stat(source, "requests")
        if self.player is not None:
            status, hdrs, raw = self.player.play(method, full_url, body)
            return status, hdrs, raw, None
        host = urllib.parse.urlsplit(full_url).hostname or ""
        self.limiter.wait(host, self.rates.get(host, DEFAULT_RATE))
        req_headers = {"User-Agent": self.user_agent, "Accept": self._accept(accept)}
        for k, v in (headers or {}).items():
            if v is not None:
                req_headers[k] = v
        data = body.encode("utf-8") if body is not None else None
        if data is not None:
            req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req = urllib.request.Request(full_url, data=data, headers=req_headers, method=method)
        status, hdrs, raw = None, {}, b""
        try:
            resp = self.opener.open(req, timeout=timeout or self.timeout)
            try:
                status = resp.getcode()
                hdrs = dict(resp.headers.items())
                raw = resp.read()
            finally:
                resp.close()
        except urllib.error.HTTPError as exc:
            status = exc.code
            hdrs = dict(exc.headers.items()) if exc.headers is not None else {}
            try:
                raw = exc.read() or b""
            except Exception:
                raw = b""
        except (urllib.error.URLError, socket.timeout, ConnectionError, http.client.HTTPException, OSError) as exc:
            return None, {}, b"", exc
        if self.recorder is not None:
            self.recorder.record(method, full_url, body, status, hdrs, raw, accept=self._accept(accept))
        return status, hdrs, raw, None

    def close(self):
        """A rögzítő kazettájának kiírása (``record`` módban)."""
        if self.recorder is not None:
            return self.recorder.save()
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def recording(path, name=None, notes=None, origin="recorded", env=None, slim=False, **kw):
    """Kényelmi rögzítő: ``with recording(fájl, name=...) as http: ...`` — kilépéskor kiírja a kazettát.

    Csak fejlesztői használatra (élő hálózat kell). A titkokat a rögzítő redaktálja és ellenőrzi. ``slim=True``:
    a kód által nem használt terjedelmes részek (affiliációk, PubMed ReferenceList) is kimaradnak."""
    rec = CassetteRecorder(path, name=name, notes=notes, origin=origin, env=env, slim=slim)
    return HttpClient(recorder=rec, env=env, use_env_cassette=False, **kw)


# ---------------------------------------------------------------------------------------------
# Forráskliensek közös alapja (a pubmed/europepmc/openalex/scopus/ctgov/crossref modulok használják)
# ---------------------------------------------------------------------------------------------

class BaseClient(object):
    """Közös alap: ``Client(http, cfg)``; ``check() -> dict(status, message, key_configured, entitlement,
    reset_at, …)``. A ``cfg`` a ``state.json`` ``sources.<kulcs>`` objektuma (vagy ``None``)."""

    SOURCE = None
    PLATFORM = None
    PROBE = None

    def __init__(self, http=None, cfg=None, env=None):
        if http is None:
            http = HttpClient(env=env)
        self.http = http
        self.cfg = dict(cfg or {})
        self.env = env if env is not None else getattr(http, "env", None)

    @property
    def enabled(self):
        return self.cfg.get("enabled", True) is not False

    def now_ts(self):
        return utc_ts(self.http.now())

    def idval(self, value, via, source=None):
        """Azonosító-objektum (``common.v1#/$defs/idval``): az érték EBBŐL az API-válaszból jött."""
        return {"value": value, "source": source or self.SOURCE, "via": via, "at": self.now_ts()}

    def key_configured(self):
        return None

    def _result(self, status, message=None, reset_at=None, http_status=None, entitlement=None, details=None,
                detail_text=None):
        res = {
            "source": self.SOURCE,
            "status": status,
            "message": message or status_explain(self.SOURCE, status, reset_at, detail_text),
            "key_configured": self.key_configured(),
            "insttoken_configured": None,
            "entitlement": entitlement,
            "reset_at": reset_at,
            "checked_at": self.now_ts(),
            "http_status": http_status,
            "platform": self.PLATFORM,
        }
        if details:
            res["details"] = details
        return res

    def _result_from_exc(self, exc):
        if isinstance(exc, SourceUnavailable):
            return self._result(exc.status, message=exc.explain, reset_at=exc.reset_at, http_status=exc.http_status)
        if isinstance(exc, HttpError):
            return self._result("unreachable", http_status=exc.status, detail_text={
                "hu": "Váratlan válasz (HTTP %s)." % exc.status, "en": "Unexpected response (HTTP %s)." % exc.status})
        if isinstance(exc, ParseError):
            return self._result("unreachable", detail_text={
                "hu": "A válasz nem értelmezhető (proxy hibaoldal?).",
                "en": "The response could not be parsed (proxy error page?)."})
        raise exc

    def _probe(self):  # pragma: no cover - a leszármazott valósítja meg
        raise NotImplementedError

    def check(self):
        """Olcsó próbakérés; az eredmény a ``state.sources.<kulcs>`` mezőibe írható (``sources.apply_checks``)."""
        try:
            return self._probe()
        except (SourceUnavailable, HttpError, ParseError) as exc:
            return self._result_from_exc(exc)
