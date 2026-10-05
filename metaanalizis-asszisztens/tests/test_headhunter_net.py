# -*- coding: utf-8 -*-
"""Metaheadhunter — hálózati réteg, titokkezelés, kazetták, forráskliensek, forrás-regiszter és
áttekintés-felkutatás (TERV_metaheadhunter.md 3., 5., 19. és 20.5 fejezet).

Minden teszt OFFLINE fut: rögzített kazettákkal (``tests/reference/headhunter/cassettes/``) vagy egy
hamis ``opener``-rel, amely a ``urllib`` válaszait/hibáit utánozza. A hálózatra semmi nem megy ki (a lejátszó
ismeretlen kérésnél ``CassetteMiss``-t dob).

Lefedett szerződés-pontok:

- N5: újrapróbálás exponenciális várakozással és jitterrel, ``Retry-After`` (rövid: vár; hosszú:
  ``rate_limited`` + ``reset_at``), időtúllépés (30 s), gépenkénti sebességkorlát, proxy a környezetből
  (``ProxyHandler``), offline mód, gyorsítótár (teljes szöveg SOHA a projektben), User-Agent;
- N6/N9/H016: kulcs csak fejlécben (Scopus, OpenAlex), az NCBI ``api_key`` URL-ben, de minden naplóból,
  hibából, gyorsítótár-kulcsból és kazettából redaktálva; hamis kulcsokkal futtatott ellenőrzés után semmilyen
  kimenetben nincs kulcs vagy e-mail-cím (még akkor sem, ha a szerver visszhangozza);
- kazetta: rögzítő redaktálás (fejlécek, paraméterek, absztrakt, JATS-törzs), titok esetén nem ír; lejátszó
  illesztése (paraméter-sorrend és titkos paraméterek függetlenül), sorozatok, ``CassetteMiss``; a repóban
  lévő minden kazetta sémahelyes és megfelel a redaktálási szabályoknak;
- forrásonként: PubMed (esearch/esummary/efetch/elink/ecitmatch/idconv-tartalék), Europe PMC (``EXT_ID``,
  ``fullTextXML`` 500 → nincs nyílt szöveg, hivatkozások, idézők, annotációk), OpenAlex (egyedi vs listás
  lekérés, valós 429, cursor-lapozás, ``Authorization: Bearer``), Scopus (nincs kulcs → nincs kérés;
  401 két hibaalakja; 403 → ``search_only``; 429 kvóta; cursor; ``view=REF``), CT.gov (``BACKGROUND`` gyenge
  jel — regresszió), Crossref (blokkolt → ``unreachable``, a többi forrás fut tovább);
- forrás-regiszter: alapbeállítás, választás, állapot-táblázat, ``sources --check`` adat és CLI;
- ``finder.find_reviews``: lekérdezés-építés, összevonás azonosító szerint, Cochrane-változatok, visszavont
  áttekintés, keresési dátum / k / jelzések idézettel, H012/H014, sémahelyes ``reviews/<id>.json``.
"""
import calendar
import email.message
import io
import json
import os
import random
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

from _helpers import ROOT
from ma_gui import schema_lite
from metaelemzes.headhunter import net, pubmed, europepmc, openalex, scopus, ctgov, crossref, sources, finder

CDIR = os.path.join(ROOT, "tests", "reference", "headhunter", "cassettes")
CONTRACTS = os.path.join(ROOT, "metaelemzes", "headhunter", "contracts")
REG = schema_lite.load_schema_dir(CONTRACTS)
SCHEMA = {
    "cassette": REG["urn:szk:contract:ma.headhunter.cassette:1"],
    "review": REG["urn:szk:contract:ma.headhunter.review:1"],
    "state": REG["urn:szk:contract:ma.headhunter.state:1"],
}
#: a kazetták rögzített órája: 2026-10-05T12:00:00Z
FIXED = calendar.timegm((2026, 10, 5, 12, 0, 0, 0, 0, 0))

FAKE_ENV = {
    "MA_SCOPUS_APIKEY": "TESTKEY-SCOPUS-9f8e7d6c5b4a3210",
    "MA_SCOPUS_INSTTOKEN": "TESTTOKEN-INST-1a2b3c4d5e6f",
    "MA_OPENALEX_APIKEY": "TESTKEY-OPENALEX-77aa88bb99cc",
    "MA_NCBI_APIKEY": "TESTKEY-NCBI-0011223344556677",
    "MA_CONTACT_EMAIL": "kutato.teszt@example.org",
}

BCG_BLOCKS = [{"concept": "P", "terms": ["tuberculosis"], "mesh": []},
              {"concept": "I", "terms": ["BCG vaccine", "BCG vaccination"], "mesh": []}]
SOY_BLOCKS = [{"concept": "P", "terms": ["inflammation", "C-reactive protein", "inflammatory markers"], "mesh": []},
              {"concept": "I", "terms": ["soy", "isoflavones", "soy isoflavone"], "mesh": []}]


def validate(doc, name):
    return schema_lite.validate(doc, SCHEMA[name], registry=REG)


def validate_sub(doc, schema):
    return schema_lite.validate(doc, schema, registry=REG)


# ---------------------------------------------------------------------------------------------
# Tesztsegédek: hamis óra, hamis opener (urllib-utánzat), kazettás kliens
# ---------------------------------------------------------------------------------------------

class FakeClock(object):
    """Injektálható óra: az ``alvás`` az időt előre tolja (a teszt nem vár valóban)."""

    def __init__(self, t=FIXED):
        self.t = float(t)
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += max(0.0, float(seconds))


def _msg(headers):
    m = email.message.Message()
    for k, v in (headers or {}).items():
        m[k] = v
    return m


class FakeResp(object):
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = _msg(headers)
        self.body = body

    def getcode(self):
        return self.status

    def read(self):
        return self.body

    def close(self):
        pass


class FakeOpener(object):
    """A ``urllib`` openerének utánzata. ``script``: válaszok sorban — ``(status, headers, body)`` vagy
    kivétel; az utolsó ismétlődik. 4xx/5xx-nél ``HTTPError``-t dob (mint a urllib)."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def open(self, req, timeout=None):
        hdrs = dict((k.lower(), v) for k, v in req.header_items())
        self.requests.append({"url": req.full_url, "headers": hdrs, "data": req.data, "method": req.get_method(),
                              "timeout": timeout})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if callable(item) and not isinstance(item, BaseException):
            item = item(req)
        if isinstance(item, BaseException):
            raise item
        status, headers, body = item
        if isinstance(body, str):
            body = body.encode("utf-8")
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "error", _msg(headers), io.BytesIO(body))
        return FakeResp(status, headers, body)


JSON_H = {"Content-Type": "application/json"}


def fake_client(script, env=None, clock=None, **kw):
    clock = clock or FakeClock()
    op = FakeOpener(script)
    h = net.HttpClient(opener=op, env=env if env is not None else {}, clock=clock, sleep=clock.sleep,
                       use_env_cassette=False, rng=random.Random(7), **kw)
    return h, op, clock


def cassette_client(*names, **kw):
    env = kw.pop("env", {})
    paths = [os.path.join(CDIR, n + ".json") for n in names]
    player = net.CassettePlayer(paths, env=env)
    clock = FakeClock()
    h = net.HttpClient(player=player, env=env, clock=clock, sleep=clock.sleep, use_env_cassette=False, **kw)
    return h, player


def all_cassette_files():
    out = []
    for root, _dirs, files in os.walk(CDIR):
        for fn in sorted(files):
            if fn.endswith(".json"):
                out.append(os.path.join(root, fn))
    return sorted(out)


# =============================================================================================
# 1. Titkok és redaktálás (N6, N9, H016)
# =============================================================================================

class TestSecretsAndRedaction(unittest.TestCase):

    def test_redact_values_params_headers(self):
        env = dict(FAKE_ENV)
        text = ("GET https://eutils.ncbi.nlm.nih.gov/x?term=a&api_key=%s&email=%s&tool=t "
                "X-ELS-APIKey: %s; Authorization: Bearer %s mailto:%s token %s" % (
                    env["MA_NCBI_APIKEY"], env["MA_CONTACT_EMAIL"].replace("@", "%40"), env["MA_SCOPUS_APIKEY"],
                    env["MA_OPENALEX_APIKEY"], env["MA_CONTACT_EMAIL"], env["MA_SCOPUS_INSTTOKEN"]))
        out = net.redact(text, env)
        for v in env.values():
            self.assertNotIn(v, out)
        self.assertNotIn("kutato.teszt", out)
        self.assertIn(net.REDACTED, out)
        self.assertIn("term=a", out)
        # bejegyzett érték nélkül is: paraméter, fejléc, Bearer, mailto
        out2 = net.redact("u?apiKey=abc123456&insttoken=zz99zz99 Authorization: Bearer qwerty12345 mailto:a@b.org", {})
        self.assertNotIn("abc123456", out2)
        self.assertNotIn("zz99zz99", out2)
        self.assertNotIn("qwerty12345", out2)
        self.assertNotIn("a@b.org", out2)

    def test_redact_proxy_credentials(self):
        out = net.redact("Tunnel to http://felhasznalo:titkosjelszo@proxy.example:3128 failed", {})
        self.assertNotIn("titkosjelszo", out)
        self.assertNotIn("felhasznalo", out)
        self.assertIn("proxy.example:3128", out)

    def test_redact_url_drops_secret_params_and_canonical_sorts(self):
        env = dict(FAKE_ENV)
        url = "https://x.org/p?z=1&api_key=%s&a=2&mailto=%s&tool=me&email=e%%40x.org" % (
            env["MA_NCBI_APIKEY"], env["MA_CONTACT_EMAIL"])
        self.assertEqual(net.redact_url(url, env), "https://x.org/p?z=1&a=2")
        self.assertEqual(net.canonical_url(url, env), "https://x.org/p?a=2&z=1")

    def test_find_secret_leaks_names_only(self):
        env = dict(FAKE_ENV)
        blob = ("x" + env["MA_SCOPUS_APIKEY"] + "y").encode("utf-8")
        leaks = net.find_secret_leaks(blob, env)
        self.assertEqual(leaks, ["MA_SCOPUS_APIKEY"])
        self.assertEqual(net.find_secret_leaks("tiszta szöveg", env), [])
        enc = "a=" + env["MA_CONTACT_EMAIL"].replace("@", "%40")
        self.assertEqual(net.find_secret_leaks(enc, env), ["MA_CONTACT_EMAIL"])

    def test_secret_registry(self):
        reg = net.SecretRegistry(env=dict(FAKE_ENV), extra=["EXTRA-SECRET-42"])
        self.assertNotIn("EXTRA-SECRET-42", reg.redact("abc EXTRA-SECRET-42 def"))
        self.assertEqual(reg.leaks("EXTRA-SECRET-42"), ["extra[0]"])
        with self.assertRaises(net.SecretLeakError):
            reg.assert_clean("..." + FAKE_ENV["MA_OPENALEX_APIKEY"], "teszt")
        st = reg.status()
        self.assertEqual(set(st.values()), {True})
        self.assertTrue(all(isinstance(v, bool) for v in st.values()))

    def test_secret_status_only_booleans(self):
        st = net.secret_status(dict(FAKE_ENV))
        self.assertEqual(st, {"scopus_key": True, "scopus_insttoken": True, "openalex_key": True, "ncbi_key": True,
                              "contact_email": True})
        st = net.secret_status({"MA_SCOPUS_APIKEY": "   "})
        self.assertFalse(st["scopus_key"])

    def test_user_agent(self):
        self.assertEqual(net.user_agent({}), "metaelemzes-headhunter/1.0.0 (python-urllib)")
        self.assertEqual(net.user_agent({"MA_CONTACT_EMAIL": "a@b.org"}),
                         "metaelemzes-headhunter/1.0.0 (python-urllib; mailto:a@b.org)")

    def test_status_explain_beginner_messages(self):
        for st in net.SOURCE_STATUSES:
            for src in ("pubmed", "europepmc", "openalex", "scopus", "ctgov", "crossref"):
                msg = net.status_explain(src, st, reset_at="2026-10-06T00:00:00Z")
                self.assertTrue(msg["hu"] and msg["en"], (src, st))
        sc = net.status_explain("scopus", "not_configured")["hu"]
        self.assertIn("MA_SCOPUS_APIKEY", sc)
        self.assertIn("dev.elsevier.com", sc)
        self.assertIn("EISZ", sc)
        self.assertIn("MA_SCOPUS_INSTTOKEN", net.status_explain("scopus", "forbidden")["hu"])
        rl = net.status_explain("openalex", "rate_limited", reset_at="2026-10-06T00:51:34Z")["hu"]
        self.assertIn("MA_OPENALEX_APIKEY", rl)
        self.assertIn("2026-10-06T00:51:34Z", rl)
        self.assertIn("sources --check", net.status_explain("crossref", "unreachable")["hu"])


class TestNormalizers(unittest.TestCase):

    def test_ids(self):
        self.assertEqual(net.norm_pmid("PMID: 8309034"), "8309034")
        self.assertEqual(net.norm_pmid("https://pubmed.ncbi.nlm.nih.gov/8309034/"), "8309034")
        self.assertIsNone(net.norm_pmid("0000"))
        self.assertIsNone(net.norm_pmid("12a"))
        self.assertEqual(net.norm_pmcid("pmc6488980"), "PMC6488980")
        self.assertEqual(net.norm_pmcid("6488980"), "PMC6488980")
        self.assertEqual(net.norm_doi("https://doi.org/10.1002/14651858.CD012915.pub2."), "10.1002/14651858.cd012915.pub2")
        self.assertEqual(net.norm_doi("doi: 10.1136/BMJ.n71"), "10.1136/bmj.n71")
        self.assertIsNone(net.norm_doi("11.1/x"))
        self.assertEqual(net.norm_nct("registered as NCT 00953927."), "NCT00953927")
        self.assertEqual(net.norm_eid("SCOPUS_ID:84900000001"), "2-s2.0-84900000001")
        self.assertEqual(net.norm_eid("2-s2.0-84900000001"), "2-s2.0-84900000001")
        self.assertEqual(net.norm_openalex("https://openalex.org/W2075477269"), "W2075477269")
        self.assertIsNone(net.norm_openalex("X123"))


# =============================================================================================
# 2. HTTP-kliens (N5)
# =============================================================================================

URL = "https://api.example.org/v1/items"
NO_RATE = {"api.example.org": 0}


class TestHttpClient(unittest.TestCase):

    def test_success_json_user_agent_accept_timeout(self):
        h, op, _c = fake_client([(200, JSON_H, '{"a": 1}')], env={"MA_CONTACT_EMAIL": "a@b.org"}, rates=NO_RATE)
        r = h.get("pubmed", URL, params=[("q", "x y")])
        self.assertEqual(r.json(), {"a": 1})
        self.assertTrue(r.ok)
        self.assertFalse(r.from_cache)
        req = op.requests[0]
        self.assertEqual(req["headers"]["user-agent"], "metaelemzes-headhunter/1.0.0 (python-urllib; mailto:a@b.org)")
        self.assertEqual(req["headers"]["accept"], "application/json")
        self.assertEqual(req["timeout"], 30.0)
        self.assertEqual(r.retrieval["source"], "pubmed")
        self.assertEqual(r.retrieval["http_status"], 200)
        self.assertTrue(r.retrieval["endpoint"].startswith("https://api.example.org/v1/items?q="))

    def test_retry_on_503_then_ok(self):
        h, op, clock = fake_client([(503, {}, "busy"), (200, JSON_H, "{}")], rates=NO_RATE)
        h.get("europepmc", URL)
        self.assertEqual(len(op.requests), 2)
        self.assertEqual(len(clock.sleeps), 1)
        self.assertTrue(1.0 <= clock.sleeps[0] <= 1.5, clock.sleeps)  # backoff 1 s + jitter
        self.assertEqual(h.stats["europepmc"]["retries"], 1)

    def test_exponential_backoff_then_unreachable(self):
        h, op, clock = fake_client([(500, {}, "oops")], rates=NO_RATE, max_retries=3)
        with self.assertRaises(net.SourceUnavailable) as cm:
            h.get("ctgov", URL)
        self.assertEqual(cm.exception.status, "unreachable")
        self.assertEqual(cm.exception.http_status, 500)
        self.assertEqual(len(op.requests), 4)
        self.assertEqual(len(clock.sleeps), 3)
        for i, s in enumerate(clock.sleeps):
            self.assertTrue(2 ** i <= s <= 2 ** i + 0.5, clock.sleeps)
        self.assertIn("nem érhető el", cm.exception.explain["hu"])
        self.assertEqual(h.source_state["ctgov"]["status"], "unreachable")

    def test_retry_after_short_waits(self):
        h, op, clock = fake_client([(429, {"Retry-After": "5"}, "{}"), (200, JSON_H, "{}")], rates=NO_RATE)
        h.get("openalex", URL)
        self.assertEqual(clock.sleeps, [5.0])
        self.assertEqual(len(op.requests), 2)

    def test_retry_after_http_date(self):
        when = net.email.utils.formatdate(FIXED + 10, usegmt=True)
        h, op, clock = fake_client([(503, {"Retry-After": when}, ""), (200, JSON_H, "{}")], rates=NO_RATE)
        h.get("pubmed", URL)
        self.assertEqual(len(clock.sleeps), 1)
        self.assertAlmostEqual(clock.sleeps[0], 10.0, places=3)

    def test_retry_after_long_rate_limited_and_blocked(self):
        body = '{"error": "Rate limit exceeded"}'
        h, op, clock = fake_client([(429, {"Retry-After": "49853", "Content-Type": "application/json"}, body),
                                    (200, JSON_H, "{}")], rates=NO_RATE)
        with self.assertRaises(net.SourceUnavailable) as cm:
            h.get("openalex", URL, bucket="openalex:list")
        exc = cm.exception
        self.assertEqual(exc.status, "rate_limited")
        self.assertEqual(exc.reset_at, net.utc_ts(FIXED + 49853))
        self.assertIn(exc.reset_at, exc.explain["hu"])
        self.assertEqual(clock.sleeps, [])  # nem vár ~14 órát
        self.assertEqual(h.source_state["openalex"]["status"], "rate_limited")
        # a vödör le van tiltva: nincs újabb hálózati kérés
        with self.assertRaises(net.SourceUnavailable):
            h.get("openalex", URL + "/2", bucket="openalex:list")
        self.assertEqual(len(op.requests), 1)
        # más vödör (egyedi lekérés) mehet
        h.get("openalex", URL + "/3")
        self.assertEqual(len(op.requests), 2)
        # a visszaállás után a tiltás megszűnik
        clock.t += 49854
        self.assertIsNone(h.blocked("openalex:list"))

    def test_429_without_retry_after_backs_off(self):
        h, op, clock = fake_client([(429, {}, "{}"), (200, JSON_H, "{}")], rates=NO_RATE)
        h.get("europepmc", URL)
        self.assertEqual(len(op.requests), 2)
        self.assertTrue(1.0 <= clock.sleeps[0] <= 1.5)

    def test_401_unauthorized_blocks_403_forbidden_does_not(self):
        h, op, _c = fake_client([(401, JSON_H, '{"error-response": {"error-code": "APIKEY_INVALID"}}')],
                                rates=NO_RATE)
        with self.assertRaises(net.SourceUnavailable) as cm:
            h.get("scopus", URL)
        self.assertEqual(cm.exception.status, "unauthorized")
        with self.assertRaises(net.SourceUnavailable):
            h.get("scopus", URL)
        self.assertEqual(len(op.requests), 1)
        h2, op2, _c = fake_client([(403, JSON_H, "{}"), (200, JSON_H, "{}")], rates=NO_RATE)
        with self.assertRaises(net.SourceUnavailable) as cm2:
            h2.get("scopus", URL)
        self.assertEqual(cm2.exception.status, "forbidden")
        h2.get("scopus", URL)
        self.assertEqual(len(op2.requests), 2)

    def test_404_and_allowed_status_return_response(self):
        h, _op, _c = fake_client([(404, JSON_H, '{"error": "nf"}')], rates=NO_RATE)
        self.assertEqual(h.get("openalex", URL).status, 404)
        h, _op, _c = fake_client([(500, JSON_H, '{}')], rates=NO_RATE)
        r = h.get("europepmc", URL, allow_status=(500,))
        self.assertEqual(r.status, 500)

    def test_400_http_error_is_redacted(self):
        env = dict(FAKE_ENV)
        h, op, _c = fake_client([(400, {}, "bad request for api_key=%s" % env["MA_NCBI_APIKEY"])], env=env,
                                rates=NO_RATE)
        with self.assertRaises(net.HttpError) as cm:
            h.get("pubmed", URL, params=[("term", "x"), ("api_key", env["MA_NCBI_APIKEY"]),
                                         ("email", env["MA_CONTACT_EMAIL"])])
        text = str(cm.exception) + json.dumps(cm.exception.explain)
        for v in env.values():
            self.assertNotIn(v, text)
        # az NCBI a kulcsot URL-ben várja: a kérésben benne van, a hibában nem
        self.assertIn("api_key=" + env["MA_NCBI_APIKEY"], op.requests[0]["url"])

    def test_tunnel_failure_not_retried(self):
        err = urllib.error.URLError("Tunnel connection failed: 403 Forbidden")
        h, op, clock = fake_client([err], rates=NO_RATE)
        with self.assertRaises(net.SourceUnavailable) as cm:
            h.get("crossref", URL)
        self.assertEqual(cm.exception.status, "unreachable")
        self.assertEqual(len(op.requests), 1)
        self.assertIn("Tunnel connection failed", cm.exception.explain["hu"])

    def test_timeout_retried_then_unreachable(self):
        h, op, _c = fake_client([socket.timeout("timed out")], rates=NO_RATE, max_retries=2)
        with self.assertRaises(net.SourceUnavailable) as cm:
            h.get("pubmed", URL)
        self.assertEqual(len(op.requests), 3)
        self.assertEqual(cm.exception.status, "unreachable")

    def test_tls_error_gives_hint_without_retry(self):
        err = urllib.error.URLError(ssl.SSLError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))
        h, op, _c = fake_client([err], rates=NO_RATE)
        with self.assertRaises(net.SourceUnavailable) as cm:
            h.get("europepmc", URL)
        self.assertEqual(len(op.requests), 1)
        self.assertIn("SSL_CERT_FILE", cm.exception.explain["hu"])

    def test_rate_limiter_per_host(self):
        clock = FakeClock()
        rl = net.RateLimiter(clock, clock.sleep)
        delays = [rl.wait("a", 2.0) for _ in range(3)]
        self.assertEqual([round(d, 6) for d in delays], [0.0, 0.5, 0.5])
        self.assertEqual(rl.wait("b", 2.0), 0.0)
        self.assertEqual(rl.wait("c", 0), 0.0)
        # HttpClient: PubMed gép 3 kérés/s
        h, op, clock = fake_client([(200, JSON_H, "{}")])
        for _ in range(3):
            h.get("pubmed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi")
        self.assertEqual([round(s, 4) for s in clock.sleeps], [0.3333, 0.3333])

    def test_ncbi_key_raises_rate_to_10(self):
        h, _op, _c = fake_client([(200, JSON_H, "{}")], env={"MA_NCBI_APIKEY": "TESTKEY-NCBI-1"})
        pubmed.Client(h)
        self.assertEqual(h.rates[pubmed.HOST], 10.0)
        h2, _op, _c = fake_client([(200, JSON_H, "{}")])
        pubmed.Client(h2)
        self.assertEqual(h2.rates[pubmed.HOST], 3.0)

    def test_proxy_from_environment(self):
        env = {"HTTPS_PROXY": "http://upper:1", "https_proxy": "http://lower:2", "HTTP_PROXY": "http://h:3",
               "NO_PROXY": "localhost"}
        self.assertEqual(net.proxies_from_env(env), {"https": "http://lower:2", "http": "http://h:3"})
        self.assertNotIn("http", net.proxies_from_env({"HTTP_PROXY": "http://h:3", "REQUEST_METHOD": "GET"}))
        h = net.HttpClient(env={"HTTPS_PROXY": "http://proxy.example:3128"}, use_env_cassette=False)
        handlers = [x for x in h.opener.handlers if isinstance(x, urllib.request.ProxyHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0].proxies, {"https": "http://proxy.example:3128"})

    def test_offline_and_project_cache(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        cache = os.path.join(tmp, "cache")
        off, op_off, _c = fake_client([(200, JSON_H, '{"x": 1}')], cache_dir=cache, offline=True, rates=NO_RATE)
        with self.assertRaises(net.SourceUnavailable) as cm:
            off.get("pubmed", URL)
        self.assertEqual(cm.exception.status, "unreachable")
        self.assertIn("Offline", cm.exception.explain["hu"])
        self.assertEqual(op_off.requests, [])
        on, op_on, _c = fake_client([(200, JSON_H, '{"x": 1}')], cache_dir=cache, rates=NO_RATE)
        self.assertFalse(on.get("pubmed", URL).from_cache)
        r2 = on.get("pubmed", URL)
        self.assertTrue(r2.from_cache)
        self.assertEqual(len(op_on.requests), 1)
        r3 = off.get("pubmed", URL)
        self.assertTrue(r3.from_cache)
        self.assertEqual(r3.json(), {"x": 1})
        self.assertEqual(op_off.requests, [])
        self.assertRegex(r3.retrieval["cache_key"], r"^[0-9a-f]{64}$")

    def test_cache_key_independent_of_secrets_and_no_secret_in_cache(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        cache = os.path.join(tmp, "cache")
        env = dict(FAKE_ENV)
        h1, _op, _c = fake_client([(200, JSON_H, '{"ok": true}')], env=env, cache_dir=cache, rates=NO_RATE)
        r1 = h1.get("pubmed", URL, params=[("term", "a"), ("api_key", env["MA_NCBI_APIKEY"]),
                                           ("email", env["MA_CONTACT_EMAIL"])])
        h2, op2, _c = fake_client([(200, JSON_H, "{}")], env={}, cache_dir=cache, rates=NO_RATE)
        r2 = h2.get("pubmed", URL, params=[("term", "a")])
        self.assertTrue(r2.from_cache)
        self.assertEqual(r1.cache_key, r2.cache_key)
        self.assertEqual(op2.requests, [])
        for root, _d, files in os.walk(cache):
            for fn in files:
                with open(os.path.join(root, fn), "rb") as fh:
                    self.assertEqual(net.find_secret_leaks(fh.read(), env), [])

    def test_cache_ttl(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        h, op, clock = fake_client([(200, JSON_H, "{}")], cache_dir=tmp, rates=NO_RATE)
        h.get("pubmed", URL)
        clock.t += 29 * 86400
        self.assertTrue(h.get("pubmed", URL).from_cache)
        clock.t += 2 * 86400
        self.assertFalse(h.get("pubmed", URL).from_cache)
        self.assertEqual(len(op.requests), 2)

    def test_fulltext_never_in_project_cache(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        proj, user = os.path.join(tmp, "proj"), os.path.join(tmp, "user")
        jats = "<article><front><article-meta/></front><body><sec><p>teljes szöveg</p></sec></body></article>"
        h, _op, _c = fake_client([(200, {"Content-Type": "application/xml"}, jats)], cache_dir=proj,
                                 fulltext_cache_dir=user, rates=NO_RATE)
        h.get("europepmc", URL, accept="xml")  # cache=True, de teljes szöveg → nem tárolja
        self.assertFalse(os.path.exists(proj))
        h.get("europepmc", URL + "/ft", accept="xml", cache="fulltext")  # a projekten KÍVÜLI gyorsítótárba
        self.assertFalse(os.path.exists(proj))
        self.assertTrue(os.path.isdir(user))
        # MA_HH_CACHE_DIR nélkül nincs teljesszöveg-gyorsítótár
        self.assertIsNone(net.user_fulltext_cache_dir({}))
        self.assertEqual(net.user_fulltext_cache_dir({"MA_HH_CACHE_DIR": "/x"}), os.path.join("/x", "fulltext"))
        self.assertTrue(net.default_fulltext_cache_dir({"XDG_CACHE_HOME": "/c"}).endswith(
            os.path.join("metaelemzes", "headhunter", "fulltext")))

    def test_log_events_redacted_and_response_url_without_key(self):
        env = dict(FAKE_ENV)
        events = []
        h, op, _c = fake_client([(200, JSON_H, '{"esearchresult": {"count": "0", "idlist": []}}')], env=env,
                                log=events.append)
        res = pubmed.Client(h).esearch("x")
        self.assertIn("api_key=" + env["MA_NCBI_APIKEY"], op.requests[0]["url"])
        blob = json.dumps(events) + json.dumps(res["retrieval"])
        for v in env.values():
            self.assertNotIn(v, blob)
        self.assertNotIn("tool=", res["retrieval"]["endpoint"])

    def test_long_pubmed_query_goes_by_post(self):
        h, op, _c = fake_client([(200, JSON_H, '{"esearchresult": {"count": "1", "idlist": ["1"]}}')])
        term = " OR ".join("term%d[tiab]" % i for i in range(200))
        res = pubmed.Client(h).esearch(term)
        self.assertEqual(res["ids"], ["1"])
        req = op.requests[0]
        self.assertEqual(req["method"], "POST")
        self.assertNotIn("term", req["url"])
        self.assertIn(b"term199", req["data"])

    def test_bad_url_is_http_error_without_secret(self):
        env = dict(FAKE_ENV)
        h, _op, _c = fake_client([(200, JSON_H, "{}")], env=env)
        with self.assertRaises(net.HttpError) as cm:
            h.get("pubmed", "eutils.ncbi.nlm.nih.gov/esearch.fcgi?api_key=" + env["MA_NCBI_APIKEY"])
        self.assertNotIn(env["MA_NCBI_APIKEY"], str(cm.exception))


class TestPaged(unittest.TestCase):

    def test_pages_total_complete(self):
        pages = {None: ([1, 2], 5, "b"), "b": ([3, 4], 5, "c"), "c": ([5], 5, None)}
        p = net.Paged("x", lambda st: pages[st])
        self.assertEqual(p.all(), [1, 2, 3, 4, 5])
        self.assertEqual((p.total, p.retrieved, p.complete, p.stopped, p.pages), (5, 5, True, "end", 3))
        p = net.Paged("x", lambda st: pages[st], max_results=3)
        self.assertEqual(p.all(), [1, 2, 3])
        self.assertEqual((p.complete, p.stopped), (False, "cap"))
        self.assertEqual(p.summary()["count_total"], 5)

    def test_first_page_error_raises_later_page_partial(self):
        def first(st):
            raise net.SourceUnavailable("openalex", "rate_limited", reset_at="2026-10-06T00:00:00Z")
        with self.assertRaises(net.SourceUnavailable):
            net.Paged("openalex", first).all()

        def later(st):
            if st is None:
                return [1, 2], 10, "n"
            raise net.SourceUnavailable("openalex", "rate_limited")
        p = net.Paged("openalex", later)
        self.assertEqual(p.all(), [1, 2])
        self.assertEqual((p.complete, p.stopped), (False, "error"))
        self.assertEqual(p.summary()["error"]["status"], "rate_limited")

    def test_safe_call(self):
        def boom():
            raise net.SourceUnavailable("crossref", "unreachable")
        res, err = net.safe_call(boom)
        self.assertIsNone(res)
        self.assertEqual(err.status, "unreachable")
        self.assertEqual(net.safe_call(lambda: 5), (5, None))


# =============================================================================================
# 3. Kazetták (rögzítés, lejátszás, a repó kazettáinak szabályai)
# =============================================================================================

class TestCassettes(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        net.reset_env_cassettes()
        self.addCleanup(net.reset_env_cassettes)

    def test_recorder_redacts_everything_and_validates(self):
        env = dict(FAKE_ENV)
        path = os.path.join(self.tmp, "rec.json")
        rec = net.CassetteRecorder(path, name="test-recorder", env=env, notes="teszt")
        abstract_json = json.dumps({"resultList": {"result": [{"id": "1", "abstractText": "TITKOS ABSZTRAKT SZÖVEG",
                                                               "title": "T " + env["MA_CONTACT_EMAIL"]}]}})
        efetch_xml = ("<PubmedArticleSet><PubmedArticle><Abstract><AbstractText Label='X'>ABSZTRAKT-XML</AbstractText>"
                      "</Abstract></PubmedArticle></PubmedArticleSet>")
        jats = "<article><body><sec><p>TELJES SZÖVEG</p></sec></body><back><ref-list/></back></article>"
        script = [(200, {"Content-Type": "application/json", "Set-Cookie": "sess=abc", "X-RateLimit-Limit": "3",
                         "report-to": "{}"}, abstract_json),
                  (200, {"Content-Type": "text/xml"}, efetch_xml),
                  (200, {"Content-Type": "application/xml"}, jats)]
        clock = FakeClock()
        h = net.HttpClient(opener=FakeOpener(script), recorder=rec, env=env, clock=clock, sleep=clock.sleep,
                           use_env_cassette=False)
        cl = pubmed.Client(h)
        cl.esearch("x")
        h.get("pubmed", pubmed.BASE + "efetch.fcgi", params=[("id", "1"), ("api_key", env["MA_NCBI_APIKEY"])],
              accept="xml")
        h.get("europepmc", europepmc.BASE + "PMC1/fullTextXML", accept="xml", cache=False)
        self.assertEqual(h.close(), path)
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertEqual(net.find_secret_leaks(raw, env), [])
        text = raw.decode("utf-8")
        for bad in ("TITKOS ABSZTRAKT", "ABSZTRAKT-XML", "TELJES SZÖVEG", "sess=abc", "report-to", "api_key",
                    "tool=", "email=", "mailto"):
            self.assertNotIn(bad, text)
        doc = json.loads(text)
        self.assertEqual(validate(doc, "cassette"), [])
        its = doc["interactions"]
        self.assertEqual(its[0]["response"]["headers"], {"content-type": "application/json", "x-ratelimit-limit": "3"})
        self.assertEqual(its[0]["response"]["redactions"], ["abstractText"])
        self.assertEqual(its[1]["response"]["redactions"], ["AbstractText"])
        self.assertEqual(its[2]["response"]["redactions"], ["body"])
        self.assertIn("<ref-list/>", its[2]["response"]["body_text"])  # bibliográfiai rész marad
        # a rögzített kazetta azonnal lejátszható (kulcs nélküli környezetben is)
        player = net.CassettePlayer(path, env={})
        h2 = net.HttpClient(player=player, env={}, use_env_cassette=False)
        self.assertEqual(pubmed.Client(h2).esearch("x")["count"], 0)

    def test_recorder_refuses_to_write_secret(self):
        env = dict(FAKE_ENV)
        path = os.path.join(self.tmp, "leak.json")
        rec = net.CassetteRecorder(path, name="leak-test", env=env, notes="kulcs: " + env["MA_SCOPUS_APIKEY"])
        rec.record("GET", "https://x.org/a", None, 200, {"Content-Type": "application/json"}, b"{}")
        with self.assertRaises(net.SecretLeakError) as cm:
            rec.save()
        self.assertFalse(os.path.exists(path))
        self.assertNotIn(env["MA_SCOPUS_APIKEY"], str(cm.exception))
        self.assertIsNone(net.CassetteRecorder(os.path.join(self.tmp, "empty.json")).save())

    def test_player_matching_sequence_and_miss(self):
        doc = {"schema": net.CASSETTE_SCHEMA, "name": "seq-test", "recorded_at": "2026-10-05T12:00:00Z",
               "origin": "hand_made", "interactions": [
                   {"request": {"method": "GET", "url": "https://api.example.org/x?b=2&a=1"},
                    "response": {"status": 500, "headers": {}, "body_text": "err"}},
                   {"request": {"method": "GET", "url": "https://api.example.org/x?a=1&b=2"},
                    "response": {"status": 200, "headers": {"content-type": "application/json"},
                                 "body_json": {"ok": True}}}]}
        self.assertEqual(validate(doc, "cassette"), [])
        player = net.CassettePlayer.from_documents([doc], env={})
        clock = FakeClock()
        h = net.HttpClient(player=player, env={}, clock=clock, sleep=clock.sleep, use_env_cassette=False)
        r = h.get("pubmed", "https://api.example.org/x", params=[("b", 2), ("api_key", "K-123456"),
                                                                 ("email", "e@x.org"), ("a", 1)])
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual([p["status"] for p in player.played], [500, 200])
        with self.assertRaises(net.CassetteMiss):
            h.get("pubmed", "https://api.example.org/unknown")
        self.assertEqual(player.misses[-1]["url"], "https://api.example.org/unknown")

    def test_cassette_from_env(self):
        self.assertEqual(net.cassette_from_env({}), (None, None))
        self.assertEqual(net.cassette_from_env({"MA_HH_CASSETTE": "off"}), (None, None))
        with self.assertRaises(ValueError):
            net.cassette_from_env({"MA_HH_CASSETTE": "replay"})
        with self.assertRaises(ValueError):
            net.cassette_from_env({"MA_HH_CASSETTE": "bogus", "MA_HH_CASSETTE_FILE": self.tmp})
        env = {"MA_HH_CASSETTE": "replay", "MA_HH_CASSETTE_FILE": os.path.join(CDIR, "ctgov")}
        p1, r1 = net.cassette_from_env(env)
        p2, _r2 = net.cassette_from_env(env)
        self.assertIsNone(r1)
        self.assertIs(p1, p2)  # folyamatonként közös lejátszó
        self.assertGreaterEqual(len(p1.files), 5)
        h = net.HttpClient(env=env)  # a környezeti változó dönt
        self.assertEqual(ctgov.Client(h, env=env).check()["status"], "ok")
        renv = {"MA_HH_CASSETTE": "record", "MA_HH_CASSETTE_FILE": self.tmp}
        _p, rec = net.cassette_from_env(renv)
        self.assertTrue(rec.path.startswith(self.tmp))
        self.assertRegex(os.path.basename(rec.path), r"^recorded-\d{8}T\d{6}Z\.json$")

    def test_repository_cassettes_follow_policy(self):
        files = all_cassette_files()
        self.assertGreaterEqual(len(files), 35)
        names = set()
        for fn in files:
            rel = os.path.relpath(fn, CDIR)
            with open(fn, "rb") as fh:
                raw = fh.read()
            self.assertLess(len(raw), 250000, rel)
            doc = json.loads(raw.decode("utf-8"))
            self.assertEqual(validate(doc, "cassette"), [], rel)
            self.assertNotIn(doc["name"], names, rel)
            names.add(doc["name"])
            self.assertEqual(raw.decode("utf-8"), net.dump_json(doc), "kanonikus formázás: " + rel)
            text = raw.decode("utf-8")
            for it in doc["interactions"]:
                req, resp = it["request"], it["response"]
                for k in (req.get("headers") or {}):
                    self.assertNotIn(k.lower(), net.SECRET_HEADERS, rel)
                for k in (resp.get("headers") or {}):
                    self.assertNotIn(k.lower(), net.SECRET_HEADERS, rel)
                    self.assertTrue(k.startswith(net.CASSETTE_HEADER_PREFIXES), (rel, k))
                q = req["url"].split("?", 1)[1] if "?" in req["url"] else ""
                for k, _v in net._split_query(q):
                    self.assertNotIn(k.lower(), net.SECRET_PARAMS, rel)
                body = resp.get("body_text") or ""
                for tag in ("AbstractText", "OtherAbstract", "abstract", "trans-abstract"):
                    self.assertIsNone(re.search(r"<%s\b[^>]*>(?!%s)" % (tag, net.REDACTED), body), (rel, tag))
                if doc["origin"] == "recorded":
                    self.assertIsNone(re.search(r"<body\b[^>]*>(?!%s)" % net.REDACTED, body), rel)
                else:  # kézi, szintetikus JATS: csak apró, jelölt fixture lehet
                    if "<body" in body:
                        self.assertLess(len(body), 2000, rel)
                        self.assertIn("szintetikus", doc.get("license_note") or "", rel)
                self._check_json_redacted(resp.get("body_json"), rel)
            if rel.startswith("scopus" + os.sep):
                if doc["origin"] == "hand_made":
                    self.assertTrue(doc.get("unverified_live"), rel)
                else:
                    self.assertIn("401", doc["notes"], rel)  # élőben csak a 401 volt megfigyelhető
            self.assertNotIn("TESTKEY-SCOPUS", text, rel)

    def _check_json_redacted(self, obj, rel):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in net.JSON_REDACT_KEYS and v not in (None, "", [], {}):
                    self.assertEqual(v, net.REDACTED, (rel, k))
                else:
                    self._check_json_redacted(v, rel)
        elif isinstance(obj, list):
            for v in obj:
                self._check_json_redacted(v, rel)


# =============================================================================================
# 4. Forráskliensek kazettákkal
# =============================================================================================

class TestPubMed(unittest.TestCase):

    def test_check(self):
        h, _p = cassette_client("pubmed/check")
        res = pubmed.Client(h).check()
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["source"], "pubmed")
        self.assertFalse(res["key_configured"])

    def test_esearch_review_filter(self):
        h, _p = cassette_client("pubmed/esearch_bcg_reviews")
        q = finder.build_queries(BCG_BLOCKS)["pubmed"]["term"]
        res = pubmed.Client(h).esearch(q, retmax=5)
        self.assertEqual(res["count"], 90)
        self.assertEqual(len(res["ids"]), 5)
        self.assertIn('"systematic"[Filter]', res["querytranslation"])

    def test_esummary_records(self):
        h, _p = cassette_client("pubmed/esummary_kashangura_tameris")
        recs = pubmed.Client(h).esummary(["31038197", "23391465"])
        self.assertEqual([r["pmid"] for r in recs], ["31038197", "23391465"])
        k, t = recs
        self.assertEqual(k["first_author"], "Kashangura R")
        self.assertEqual(k["pmcid"], "PMC6488980")
        self.assertEqual(k["doi"], "10.1002/14651858.cd012915.pub2")
        self.assertEqual(k["year"], 2019)
        self.assertEqual(t["first_author"], "Tameris MD")
        self.assertEqual(t["year"], 2013)
        self.assertNotIn("abstract", k)

    def test_efetch_registry_and_references(self):
        h, _p = cassette_client("pubmed/efetch_tameris")
        rec = pubmed.Client(h).efetch_records(["23391465"])[0]
        self.assertEqual(rec["pmid"], "23391465")
        self.assertEqual(rec["registry_ids"], ["NCT00953927"])  # a közlemény saját regisztrációs nyilatkozata
        self.assertEqual(rec["databanks"][0]["name"], "ClinicalTrials.gov")
        self.assertIn(net.REDACTED, rec["abstract"])  # a kazettában nincs absztrakt
        self.assertTrue(rec["references"])
        self.assertTrue(rec["title"].startswith("Safety and efficacy of MVA85A"))
        self.assertFalse(rec["retracted"])

    def test_parse_efetch_synthetic_retraction_and_registry(self):
        xml = ("<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>"
               "<Journal><Title>Fixture J</Title><ISOAbbreviation>Fix J</ISOAbbreviation><JournalIssue><Volume>3"
               "</Volume><PubDate><Year>2015</Year></PubDate></JournalIssue></Journal>"
               "<ArticleTitle>Synthetic trial</ArticleTitle><Pagination><MedlinePgn>1-9</MedlinePgn></Pagination>"
               "<ELocationID EIdType='doi'>10.5555/FIX.1</ELocationID><Abstract><AbstractText>Registered as "
               "ISRCTN12345678.</AbstractText></Abstract><AuthorList><Author><LastName>Alpha</LastName>"
               "<Initials>A</Initials></Author></AuthorList><PublicationTypeList><PublicationType>"
               "Randomized Controlled Trial</PublicationType></PublicationTypeList><DataBankList><DataBank>"
               "<DataBankName>ClinicalTrials.gov</DataBankName><AccessionNumberList><AccessionNumber>NCT01234567"
               "</AccessionNumber></AccessionNumberList></DataBank></DataBankList></Article>"
               "<CommentsCorrectionsList><CommentsCorrections RefType='RetractionIn'><RefSource>Fix J 2016</RefSource>"
               "<PMID>456</PMID></CommentsCorrections></CommentsCorrectionsList></MedlineCitation>"
               "<PubmedData><ArticleIdList><ArticleId IdType='pmc'>PMC999</ArticleId></ArticleIdList></PubmedData>"
               "</PubmedArticle></PubmedArticleSet>")
        rec = pubmed.parse_efetch(xml)[0]
        self.assertEqual((rec["pmid"], rec["doi"], rec["pmcid"], rec["year"]), ("123", "10.5555/fix.1", "PMC999", 2015))
        self.assertTrue(rec["retracted"])
        self.assertEqual(rec["registry_ids"], ["NCT01234567"])
        self.assertEqual(rec["registry_ids_from_abstract"], ["ISRCTN12345678"])
        self.assertEqual(rec["comments_corrections"][0], {"type": "RetractionIn", "pmid": "456", "source": "Fix J 2016"})

    def test_ecitmatch(self):
        h, _p = cassette_client("pubmed/ecitmatch_colditz_tameris")
        res = pubmed.Client(h).ecitmatch([("jama", 1994, 271, 698, "colditz ga", "ref1"),
                                          ("lancet", 2013, 381, 1021, "tameris md", "ref2"),
                                          ("foo", 1999, 1, 1, "bar", "ref3")])
        self.assertEqual(res, {"ref1": "8309034", "ref2": "23391465", "ref3": None})

    def test_elink_references(self):
        h, _p = cassette_client("pubmed/elink_roy2014_refs")
        refs = pubmed.Client(h).elink("25097193", "pubmed_pubmed_refs")
        self.assertGreater(len(refs), 20)
        self.assertTrue(all(re.match(r"^\d+$", x) for x in refs))

    def test_idconv_falls_back_when_converter_blocked(self):
        h, _p = cassette_client("pubmed/idconv_unreachable_handmade", "pubmed/idconv_fallback")
        res = pubmed.Client(h).idconv(["10.1002/14651858.CD012915.pub2"])
        self.assertEqual(res["10.1002/14651858.CD012915.pub2"],
                         {"pmid": "31038197", "pmcid": "PMC6488980", "doi": "10.1002/14651858.cd012915.pub2",
                          "via": "pubmed.esearch"})

    def test_replay_with_ncbi_key_set(self):
        """A kulcs az URL-ben megy, de a kazetta kulcs nélküli: a lejátszás ugyanúgy illeszt."""
        env = {"MA_NCBI_APIKEY": FAKE_ENV["MA_NCBI_APIKEY"], "MA_CONTACT_EMAIL": FAKE_ENV["MA_CONTACT_EMAIL"]}
        h, player = cassette_client("pubmed/check", env=env)
        res = pubmed.Client(h, env=env).check()
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["key_configured"])
        self.assertNotIn(env["MA_NCBI_APIKEY"], json.dumps(player.played))


class TestEuropePMC(unittest.TestCase):

    def test_queries_use_ext_id(self):
        self.assertEqual(europepmc.query_pmid("PMID:8309034"), "EXT_ID:8309034 AND SRC:MED")
        self.assertEqual(europepmc.date_range("CREATION_DATE", "2023-01-01"), "CREATION_DATE:[2023-01-01 TO 3000-12-31]")
        with self.assertRaises(ValueError):
            europepmc.date_range("PUB_YEAR", "2020")
        self.assertEqual(europepmc.query_doi("https://doi.org/10.1136/BMJ.N71"), 'DOI:"10.1136/bmj.n71"')

    def test_check(self):
        h, _p = cassette_client("europepmc/check")
        self.assertEqual(europepmc.Client(h).check()["status"], "ok")

    def test_lookup_open_access(self):
        h, _p = cassette_client("europepmc/lookup_kashangura")
        raw = europepmc.Client(h).lookup_pmid("31038197")
        rec = europepmc.record(raw)
        self.assertEqual(rec["pmid"], "31038197")
        self.assertEqual(rec["pmcid"], "PMC6488980")
        self.assertTrue(rec["is_open_access"])
        self.assertEqual(rec["license"], "cc by-nc")
        self.assertEqual(rec["abstract"], net.REDACTED)

    def test_fulltext_500_means_no_open_fulltext_without_retry(self):
        h, player = cassette_client("europepmc/fulltext_not_oa_500")
        self.assertIsNone(europepmc.Client(h).fulltext_xml("PMC8555740"))
        self.assertEqual(len(player.played), 1)
        self.assertEqual(h.stats["europepmc"]["retries"], 0)

    def test_fulltext_oa_in_memory_not_in_project_cache(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        h, _p = cassette_client("europepmc/fulltext_oa_synthetic_handmade", cache_dir=tmp)
        xml = europepmc.Client(h).fulltext_xml("PMC0000001")
        self.assertIn("<article", xml)
        self.assertEqual(os.listdir(tmp), [])

    def test_references_and_citations(self):
        h, _p = cassette_client("europepmc/references_roy2014")
        pager = europepmc.Client(h).references("MED", "25097193", normalize=True)
        refs = pager.all()
        self.assertEqual(len(refs), pager.total)
        self.assertTrue(pager.complete)
        self.assertTrue(any(r["pmid"] for r in refs))
        h, _p = cassette_client("europepmc/citations_colditz_p1")
        pager = europepmc.Client(h).citations("MED", "8309034", page_size=25, max_results=25)
        self.assertEqual(len(pager.all()), 25)
        self.assertGreater(pager.total, 1000)
        self.assertFalse(pager.complete)
        self.assertEqual(pager.stopped, "cap")

    def test_accession_numbers(self):
        h, _p = cassette_client("europepmc/annotations_tameris")
        self.assertEqual(europepmc.Client(h).accession_numbers("MED", "23391465", subtype="NCT"), ["NCT00953927"])

    def test_search_paging(self):
        h, _p = cassette_client("europepmc/search_bcg_reviews")
        q = finder.build_queries(BCG_BLOCKS)["europepmc"]["query"]
        pager = europepmc.Client(h).search(q, result_type="core", page_size=5, max_results=5, normalize=True)
        items = pager.all()
        self.assertEqual(len(items), 5)
        self.assertGreater(pager.total, 5)
        self.assertFalse(pager.complete)


class TestOpenAlex(unittest.TestCase):

    def test_check_single_lookup_ok_but_list_budget_exhausted(self):
        h, _p = cassette_client("openalex/check")
        res = openalex.Client(h).check()
        self.assertEqual(res["status"], "rate_limited")
        self.assertEqual(res["details"]["single_lookup"], "ok")
        self.assertEqual(res["reset_at"], net.utc_ts(FIXED + 46294))
        self.assertIn("MA_OPENALEX_APIKEY", res["message"]["hu"])
        self.assertIn("egyedi lekérések", res["message"]["hu"])
        h, _p = cassette_client("openalex/check", env={"MA_OPENALEX_APIKEY": FAKE_ENV["MA_OPENALEX_APIKEY"]})
        res = openalex.Client(h).check()
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["key_configured"])

    def test_work_and_referenced_works(self):
        h, _p = cassette_client("openalex/work_pieper2014")
        w = openalex.work_record(openalex.Client(h).work("pmid:24581293", select=openalex.DEFAULT_SELECT))
        self.assertEqual(w["pmid"], "24581293")
        self.assertEqual(w["doi"], "10.1016/j.jclinepi.2013.11.007")
        self.assertEqual(w["year"], 2014)
        self.assertTrue(w["title"].startswith("Systematic review finds overlapping reviews"))
        h, _p = cassette_client("openalex/referenced_works_pieper2014")
        refs = openalex.Client(h).referenced_works("10.1016/j.jclinepi.2013.11.007")
        self.assertGreater(len(refs), 10)
        self.assertTrue(all(re.match(r"^W\d+$", r) for r in refs))
        h, _p = cassette_client("openalex/work_404")
        self.assertIsNone(openalex.Client(h).work("doi:10.9999/does-not-exist-xyz"))

    def test_real_429_list_budget(self):
        h, _p = cassette_client("openalex/list_429")
        with self.assertRaises(net.SourceUnavailable) as cm:
            openalex.Client(h).works(filter={"type": "review", "cites": "W2075477269"}, per_page=5,
                                     max_results=5).all()
        self.assertEqual(cm.exception.status, "rate_limited")
        self.assertEqual(cm.exception.reset_at, "2026-10-06T00:51:34Z")
        self.assertIsNotNone(h.blocked(openalex.LIST_BUCKET))
        self.assertIsNone(h.blocked("openalex"))  # az ingyenes egyedi lekérések mennek tovább

    def test_list_cursor_paging(self):
        h, _p = cassette_client("openalex/list_cursor_handmade")
        pager = openalex.Client(h).works(filter="type:review,from_publication_date:2015-01-01",
                                         search='(tuberculosis) AND ("BCG vaccine")', per_page=2)
        works = [openalex.work_record(w) for w in pager]
        self.assertEqual(len(works), 3)
        self.assertEqual((pager.total, pager.complete), (3, True))
        self.assertTrue(works[2]["is_retracted"])

    def test_key_only_in_authorization_header(self):
        env = {"MA_OPENALEX_APIKEY": FAKE_ENV["MA_OPENALEX_APIKEY"], "MA_CONTACT_EMAIL": FAKE_ENV["MA_CONTACT_EMAIL"]}
        h, op, _c = fake_client([(200, JSON_H, '{"id": "https://openalex.org/W12345"}')], env=env)
        self.assertEqual(openalex.Client(h).work("W12345", select="id"), {"id": "https://openalex.org/W12345"})
        req = op.requests[0]
        self.assertEqual(req["headers"]["authorization"], "Bearer " + env["MA_OPENALEX_APIKEY"])
        self.assertNotIn(env["MA_OPENALEX_APIKEY"], req["url"])
        self.assertIn("mailto=", req["url"])  # udvarias azonosítás (a naplóból/kazettából kivágva)

    def test_review_search_filters_and_title_search(self):
        body = json.dumps({"meta": {"count": 1, "next_cursor": None}, "results": [
            {"id": "https://openalex.org/W77", "display_name": "A meta-analysis", "type": "review"}]})
        h, op, _c = fake_client([(200, JSON_H, body)])
        cl = openalex.Client(h)
        works = cl.search_reviews("bcg tuberculosis", from_date="2020-01-01",
                                  concepts=["https://openalex.org/C123", "C456"], topics="T9").all()
        self.assertEqual(len(works), 1)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(op.requests[0]["url"]).query)
        self.assertEqual(q["filter"], ["type:review,from_publication_date:2020-01-01,concepts.id:C123|C456,"
                                       "topics.id:T9"])
        self.assertEqual(q["search"], ["bcg tuberculosis"])
        self.assertEqual(q["cursor"], ["*"])
        res = cl.search_title("Efficacy of BCG: a meta-analysis, part 1", year=1994)
        self.assertEqual(len(res), 1)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(op.requests[1]["url"]).query)
        self.assertEqual(q["filter"], ["title.search:Efficacy of BCG a meta-analysis part 1,publication_year:1994"])
        self.assertEqual(cl.search_title("  "), [])

    def test_cited_by_uses_cites_filter(self):
        body = json.dumps({"meta": {"count": 0, "next_cursor": None}, "results": []})
        h, op, _c = fake_client([(200, JSON_H, body)])
        self.assertEqual(openalex.Client(h).cited_by("W2075477269", from_date="2024-01-01").all(), [])
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(op.requests[0]["url"]).query)
        self.assertEqual(q["filter"], ["cites:W2075477269,from_publication_date:2024-01-01"])

    def test_helpers(self):
        self.assertEqual(openalex.work_path("W123"), "works/W123")
        self.assertEqual(openalex.work_path("https://openalex.org/W123"), "works/W123")
        self.assertEqual(openalex.work_path("pmid:24581293"), "works/pmid:24581293")
        self.assertEqual(openalex.work_path("24581293"), "works/pmid:24581293")
        self.assertEqual(openalex.work_path("10.1016/J.X.1"), "works/doi:10.1016/j.x.1")
        self.assertEqual(openalex.work_path("pmcid:pmc12"), "works/pmcid:PMC12")
        self.assertIsNone(openalex.work_path("valami"))
        self.assertEqual(openalex.filter_string([("type", "review"), ("openalex", ["W1", "W2"]), ("x", None)]),
                         "type:review,openalex:W1|W2")


class TestScopus(unittest.TestCase):
    KEY_ENV = {"MA_SCOPUS_APIKEY": FAKE_ENV["MA_SCOPUS_APIKEY"]}

    def test_without_key_no_request(self):
        h, op, _c = fake_client([(200, JSON_H, "{}")])
        cl = scopus.Client(h)
        res = cl.check()
        self.assertEqual(res["status"], "not_configured")
        self.assertEqual(res["entitlement"], "none")
        self.assertFalse(res["key_configured"])
        self.assertIn("MA_SCOPUS_APIKEY", res["message"]["hu"])
        with self.assertRaises(net.SourceUnavailable) as cm:
            cl.search("TITLE(x)").all()
        self.assertEqual(cm.exception.status, "not_configured")
        self.assertEqual(op.requests, [])

    def test_key_and_insttoken_only_in_headers(self):
        env = {"MA_SCOPUS_APIKEY": FAKE_ENV["MA_SCOPUS_APIKEY"], "MA_SCOPUS_INSTTOKEN": FAKE_ENV["MA_SCOPUS_INSTTOKEN"]}
        body = json.dumps({"search-results": {"opensearch:totalResults": "0", "entry": [{"error": "Result set was empty"}]}})
        h, op, _c = fake_client([(200, JSON_H, body)], env=env)
        self.assertEqual(scopus.Client(h).search("TITLE(x)").all(), [])
        req = op.requests[0]
        self.assertEqual(req["headers"]["x-els-apikey"], env["MA_SCOPUS_APIKEY"])
        self.assertEqual(req["headers"]["x-els-insttoken"], env["MA_SCOPUS_INSTTOKEN"])
        self.assertEqual(req["headers"]["accept"], "application/json")
        for v in env.values():
            self.assertNotIn(v, req["url"])
        self.assertIn("field=", req["url"])
        self.assertNotIn("dc%3Adescription", req["url"])  # absztrakt nincs a kért mezők között

    def test_search_cursor_handmade(self):
        h, _p = cassette_client("scopus/search_cursor_handmade", env=self.KEY_ENV)
        pager = scopus.Client(h).search(scopus.build_review_query(["tuberculosis"], ["BCG vaccine"]), count=2,
                                        normalize=True)
        recs = pager.all()
        self.assertEqual([r["eid"] for r in recs], ["2-s2.0-0000000001", "2-s2.0-0000000002", "2-s2.0-0000000003"])
        self.assertEqual((pager.total, pager.complete), (3, True))
        self.assertIsNone(recs[1]["pmid"])
        self.assertTrue(all(r["unverified_live"] for r in recs))
        self.assertEqual(recs[0]["doctype"], "re")

    def test_references_ref_view(self):
        h, _p = cassette_client("scopus/references_ref_view_handmade", env=self.KEY_ENV)
        pager = scopus.Client(h).references("2-s2.0-0000000001", refcount=2, normalize=True)
        refs = pager.all()
        self.assertEqual([r["doi"] for r in refs], ["10.5555/fixture.ref.1", "10.5555/fixture.ref.2",
                                                    "10.5555/fixture.ref.3"])
        self.assertEqual(refs[0]["first_author"], "Alpha1 A.")
        self.assertEqual(refs[0]["eid"], "2-s2.0-0000000201")

    def test_check_entitlements(self):
        h, _p = cassette_client("scopus/check_entitled_handmade", env=self.KEY_ENV)
        res = scopus.Client(h).check()
        self.assertEqual((res["status"], res["entitlement"]), ("ok", "search_and_ref"))
        self.assertEqual(res["details"]["quota"]["limit"], 20000)
        self.assertTrue(res["unverified_live"])
        h, _p = cassette_client("scopus/check_search_only_403_handmade", env=self.KEY_ENV)
        res = scopus.Client(h).check()
        self.assertEqual((res["status"], res["entitlement"]), ("ok", "search_only"))
        self.assertIn("MA_SCOPUS_INSTTOKEN", res["message"]["hu"])

    def test_quota_429(self):
        h, _p = cassette_client("scopus/quota_429_handmade", env=self.KEY_ENV)
        res = scopus.Client(h).check()
        self.assertEqual(res["status"], "rate_limited")
        self.assertEqual(res["reset_at"], "2026-10-08T12:00:00Z")
        self.assertIn("QUOTA_EXCEEDED", res["message"]["hu"])

    def test_real_401_both_error_shapes(self):
        for name, code in (("unauthorized_no_key_401", "AUTHENTICATION_ERROR"),
                           ("unauthorized_invalid_key_401", "APIKEY_INVALID")):
            h, _p = cassette_client("scopus/" + name, env=self.KEY_ENV)
            res = scopus.Client(h).check()
            self.assertEqual(res["status"], "unauthorized", name)
            self.assertIn(code, res["message"]["hu"], name)
            self.assertEqual(res["entitlement"], "none")
            self.assertNotIn(self.KEY_ENV["MA_SCOPUS_APIKEY"], json.dumps(res))

    def test_error_code_parsing_and_queries(self):
        self.assertEqual(scopus.error_code('{"service-error":{"status":{"statusCode":"AUTHORIZATION_ERROR"}}}'),
                         "AUTHORIZATION_ERROR")
        self.assertEqual(scopus.error_code('{"error-response":{"error-code":"APIKEY_INVALID"}}'), "APIKEY_INVALID")
        self.assertEqual(scopus.error_code('xx "statusCode": "QUOTA_EXCEEDED" (csonka'), "QUOTA_EXCEEDED")
        self.assertIsNone(scopus.error_code(""))
        self.assertEqual(scopus.build_review_query(["tuberculosis"], ["BCG vaccine"], since_year=2015),
                         'TITLE-ABS-KEY(tuberculosis) AND TITLE-ABS-KEY("BCG vaccine") AND '
                         '(TITLE-ABS-KEY("meta-analysis") OR TITLE-ABS-KEY("systematic review")) AND DOCTYPE(re) '
                         'AND PUBYEAR > 2014')
        self.assertEqual(scopus.build_update_query("TITLE-ABS-KEY(x)", "2023-06-15"),
                         ("(TITLE-ABS-KEY(x)) AND PUBYEAR > 2022 AND ORIG-LOAD-DATE AFT 20230615", True))
        self.assertEqual(scopus.Client(net.HttpClient(env={}, use_env_cassette=False)).citing_query("84900000001"),
                         "REFEID(2-s2.0-84900000001)")

    def test_title_query_and_citing_works(self):
        self.assertEqual(scopus.title_query('Efficacy of "BCG" vaccine', "Colditz", 1994),
                         'TITLE("Efficacy of BCG vaccine") AND AUTHLASTNAME(Colditz) AND PUBYEAR = 1994')
        self.assertEqual(scopus.title_query("A trial", "van der Berg"), 'TITLE("A trial") AND AUTHLASTNAME("van der Berg")')
        self.assertIsNone(scopus.title_query(" "))
        body = json.dumps({"search-results": {"opensearch:totalResults": "1", "cursor": {"@next": None},
                                              "entry": [{"eid": "2-s2.0-85000000077", "dc:title": "Citing work"}]}})
        h, op, _c = fake_client([(200, JSON_H, body)], env=self.KEY_ENV)
        recs = scopus.Client(h).citing_works("2-s2.0-84900000001", since_year=2023).all()
        self.assertEqual([r["eid"] for r in recs], ["2-s2.0-85000000077"])
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(op.requests[0]["url"]).query)
        self.assertEqual(q["query"], ["REFEID(2-s2.0-84900000001) AND PUBYEAR > 2022"])

    def test_reference_record_ref_info_shape(self):
        ref = {"@id": "4", "ref-info": {"ref-title": {"ref-titletext": "A trial"}, "ref-publicationyear": {"@first": "2001"},
                                         "ref-authors": {"author": [{"ce:surname": "Beta", "ce:initials": "B."}]},
                                         "refd-itemidlist": {"itemid": [{"@idtype": "DOI", "$": "10.5555/R4"}]},
                                         "volisspag": {"voliss": {"@volume": "7"}, "pagerange": {"@first": "10",
                                                                                                  "@last": "19"}}}}
        rec = scopus.reference_record(ref)
        self.assertEqual((rec["title"], rec["year"], rec["doi"], rec["first_author"], rec["volume"], rec["pages"]),
                         ("A trial", 2001, "10.5555/r4", "Beta B.", "7", "10-19"))


class TestClinicalTrials(unittest.TestCase):

    def test_check_and_404(self):
        h, _p = cassette_client("ctgov/check")
        res = ctgov.Client(h).check()
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["details"]["api_version"])
        h, _p = cassette_client("ctgov/study_404")
        self.assertIsNone(ctgov.Client(h).study("NCT99999999"))
        self.assertIsNone(ctgov.Client(h).study("nem-nct"))

    def test_background_reference_is_weak_regression(self):
        """NCT00953927 a saját fő eredményközlését (PMID 23391465) BACKGROUND-ként sorolja; egy idegen
        vizsgálat (NCT04975178) szintén — a CT.gov hivatkozás önmagában nem kapcsol (TERV 3.1, 7.)."""
        h, _p = cassette_client("ctgov/study_nct00953927")
        rec = ctgov.study_record(ctgov.Client(h).study("NCT00953927", fields="protocolSection"))
        self.assertEqual(rec["nct"], "NCT00953927")
        own = [r for r in rec["references"] if r["pmid"] == "23391465"]
        self.assertEqual(own[0]["type"], "BACKGROUND")
        self.assertEqual(own[0]["strength"], "weak")
        h, _p = cassette_client("ctgov/referencepmid_23391465")
        links = ctgov.Client(h).studies_citing_pmid("23391465")
        self.assertEqual(sorted(x["nct"] for x in links), ["NCT00953927", "NCT04975178"])
        self.assertEqual(set(x["strength"] for x in links), {"weak"})
        self.assertEqual(ctgov.link_strength("RESULT"), "confirming")
        self.assertEqual(ctgov.link_strength("derived"), "confirming")
        self.assertEqual(ctgov.link_strength(None), "weak")
        self.assertEqual(ctgov.date_filter("2023-01-01"), "AREA[StudyFirstPostDate]RANGE[2023-01-01,MAX]")
        self.assertEqual(ctgov.date_filter("2023-01-01", "2024-12-31", field="ResultsFirstPostDate"),
                         "AREA[ResultsFirstPostDate]RANGE[2023-01-01,2024-12-31]")
        with self.assertRaises(ValueError):
            ctgov.date_filter("2023", field="Bogus")

    def test_update_window_paging(self):
        h, _p = cassette_client("ctgov/studies_update_window")
        pager = ctgov.Client(h).studies(term="BCG tuberculosis vaccine",
                                        advanced="AREA[StudyFirstPostDate]RANGE[2025-01-01,MAX]",
                                        fields="protocolSection.identificationModule.nctId", page_size=3,
                                        max_results=5)
        self.assertEqual(len(pager.all()), 5)
        self.assertGreater(pager.total, 5)
        self.assertFalse(pager.complete)


class TestCrossref(unittest.TestCase):

    def test_blocked_is_unreachable_and_skippable(self):
        h, op, _c = fake_client([urllib.error.URLError("Tunnel connection failed: 403 Forbidden")])
        cl = crossref.Client(h)
        res = cl.check()
        self.assertEqual(res["status"], "unreachable")
        self.assertIn("A Crossref nem érhető el", res["message"]["hu"])
        rec, err = cl.try_work("10.1136/bmj.n71")
        self.assertIsNone(rec)
        self.assertEqual(err.status, "unreachable")

    def test_work_handmade(self):
        h, _p = cassette_client("crossref/work_handmade")
        rec = crossref.Client(h).work("10.5555/FIXTURE.CROSSREF.1")
        self.assertEqual((rec["doi"], rec["year"], rec["first_author"], rec["journal"]),
                         ("10.5555/fixture.crossref.1", 2017, "Fixture AB", "Fixture Med J"))
        self.assertNotIn("abstract", rec)


# =============================================================================================
# 5. Forrás-regiszter és ``sources --check``
# =============================================================================================

CHECK_CASSETTES = ("pubmed/check", "europepmc/check", "openalex/check", "ctgov/check")


class TestSourcesRegistry(unittest.TestCase):

    def setUp(self):
        self.src_schema = SCHEMA["state"]["properties"]["sources"]

    def test_default_config_and_schema(self):
        cfg = sources.default_config({})
        self.assertEqual(sorted(cfg), sorted(sources.ALL_SOURCES))
        self.assertEqual(validate_sub(cfg, self.src_schema), [])
        self.assertEqual([s for s in sources.SELECTABLE if cfg[s]["enabled"]], ["pubmed", "europepmc", "openalex", "ctgov"])
        self.assertEqual(cfg["scopus"]["status"], "not_configured")
        self.assertEqual(cfg["scopus"]["entitlement"], "none")
        cfg2 = sources.default_config(dict(FAKE_ENV))
        self.assertEqual(validate_sub(cfg2, self.src_schema), [])
        self.assertTrue(cfg2["scopus"]["enabled"])
        self.assertEqual(cfg2["scopus"]["status"], "unknown")
        self.assertTrue(cfg2["scopus"]["insttoken_configured"])
        self.assertNotIn(FAKE_ENV["MA_SCOPUS_APIKEY"], json.dumps(cfg2))

    def test_normalize_keeps_choices_and_tracks_env(self):
        stored = {"pubmed": {"enabled": False, "status": "ok"}, "scopus": {"enabled": True, "status": "ok"}}
        cfg = sources.normalize_config(stored, {})
        self.assertFalse(cfg["pubmed"]["enabled"])
        self.assertEqual(cfg["scopus"]["status"], "not_configured")  # kulcs nélkül mindig
        self.assertEqual(sources.effective_status("pubmed", cfg["pubmed"]), "disabled")
        self.assertEqual(validate_sub(cfg, self.src_schema), [])

    def test_select_sources(self):
        sel = sources.select_sources(env={})
        self.assertEqual(sel["use"], ["pubmed", "europepmc", "openalex", "ctgov"])
        self.assertEqual(sel["skipped"], [])
        sel = sources.select_sources(override="pubmed,scopus", env={})
        self.assertEqual(sel["use"], ["pubmed"])
        self.assertEqual(sel["skipped"][0]["status"], "not_configured")
        self.assertIn("MA_SCOPUS_APIKEY", sel["skipped"][0]["message"]["hu"])
        sel = sources.select_sources(override=["scopus"], env=dict(FAKE_ENV))
        self.assertEqual(sel["use"], ["scopus"])
        state = {"sources": {"openalex": {"enabled": True, "status": "rate_limited", "reset_at": "2999-01-01T00:00:00Z"}}}
        sel = sources.select_sources(state=state, env={})
        self.assertNotIn("openalex", sel["use"])
        state["sources"]["openalex"]["reset_at"] = "2000-01-01T00:00:00Z"
        self.assertIn("openalex", sources.select_sources(state=state, env={})["use"])
        with self.assertRaises(ValueError) as cm:
            sources.parse_source_list("pubmed,webofscience")
        self.assertIn("Ismeretlen forrás", str(cm.exception))

    def test_set_enabled(self):
        cfg, changes, warnings = sources.set_enabled(sources.default_config({}), enable=["scopus"],
                                                     disable=["openalex"], env={})
        self.assertTrue(cfg["scopus"]["enabled"])
        self.assertFalse(cfg["openalex"]["enabled"])
        self.assertEqual(changes, [{"source": "scopus", "enabled": True}, {"source": "openalex", "enabled": False}])
        self.assertEqual(warnings[0]["code"], "H014")
        with self.assertRaises(ValueError):
            sources.set_enabled({}, enable=["pubmed"], disable=["pubmed"])
        with self.assertRaises(ValueError):
            sources.set_enabled({}, disable=["crossref"])

    def test_check_with_cassettes(self):
        h, player = cassette_client(*CHECK_CASSETTES)
        res = sources.sources_status(check=True, http=h, env={}, sources="pubmed,europepmc,openalex,ctgov,scopus")
        st = dict((s, e["status"]) for s, e in res["sources"].items())
        self.assertEqual(st["pubmed"], "ok")
        self.assertEqual(st["europepmc"], "ok")
        self.assertEqual(st["ctgov"], "ok")
        self.assertEqual(st["openalex"], "rate_limited")
        self.assertEqual(st["scopus"], "not_configured")
        self.assertEqual(st["crossref"], "unknown")  # nem ellenőriztük
        self.assertEqual(res["exit_code"], 3)  # egy bekapcsolt forrás (OpenAlex) részleges
        self.assertEqual([w["source"] for w in res["warnings"]], ["openalex"])
        self.assertEqual(res["warnings"][0]["code"], "H014")
        self.assertEqual(validate_sub(res["sources"], self.src_schema), [])
        self.assertEqual(player.misses, [])
        table = sources.format_table(res["rows"])
        self.assertIn("rendben", table)
        self.assertIn("keret elfogyott", table)
        self.assertIn("nincs beállítva", table)
        self.assertIn("Scopus", table)
        self.assertIn("Forrás", sources.format_table(res["rows"], lang="hu"))
        self.assertIn("Source", sources.format_table(sources.table_rows(res["sources"], lang="en"), lang="en"))

    def test_crossref_unreachable_does_not_fail_check(self):
        h, _op, _c = fake_client([urllib.error.URLError("Tunnel connection failed: 403 Forbidden")])
        res = sources.sources_status(check=True, http=h, env={}, sources="crossref")
        self.assertEqual(res["sources"]["crossref"]["status"], "unreachable")
        self.assertEqual(res["exit_code"], 0)  # automatikus tartalék: figyelmeztetés, de nem részleges lépés
        self.assertEqual(res["warnings"][0]["source"], "crossref")

    def test_cli_main_json_and_text(self):
        h, _p = cassette_client(*CHECK_CASSETTES)
        out = io.StringIO()
        code = sources.main(["--check", "--json", "--sources", "pubmed,europepmc,openalex,ctgov"], stdout=out,
                            env={}, http=h)
        self.assertEqual(code, 3)
        doc = json.loads(out.getvalue())
        self.assertEqual(sorted(doc), ["data", "errors", "next", "ok", "pending", "warnings"])
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["warnings"][0]["code"], "H014")
        self.assertEqual(validate_sub(doc["data"]["sources"], self.src_schema), [])
        self.assertEqual(doc["data"]["secrets"]["scopus_key"], False)
        # --check nélkül nincs hálózat
        h2, op2, _c = fake_client([(200, JSON_H, "{}")])
        out = io.StringIO()
        self.assertEqual(sources.main([], stdout=out, env={}, http=h2), 0)
        self.assertIn("Metaheadhunter — források", out.getvalue())
        self.assertIn("sources --check", out.getvalue())
        self.assertEqual(op2.requests, [])
        out = io.StringIO()
        self.assertEqual(sources.main(["--sources", "nincs", "--json"], stdout=out, env={}, http=h2), 2)
        self.assertEqual(json.loads(out.getvalue())["errors"][0]["code"], "USAGE")

    def test_module_entry_point_subprocess(self):
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith("MA_"))
        env["PYTHONPATH"] = ROOT
        proc = subprocess.run([sys.executable, "-m", "metaelemzes.headhunter.sources", "--json"], cwd=ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        doc = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(doc["ok"])
        self.assertEqual(len(doc["data"]["rows"]), 6)

    def test_aliases_and_factory(self):
        import importlib
        mod = importlib.import_module("metaelemzes.headhunter.sources.pubmed")
        self.assertIs(mod, pubmed)
        self.assertIs(sources.scopus, scopus)
        self.assertIsInstance(sources.make_client("ctgov", http=net.HttpClient(env={}, use_env_cassette=False)),
                              ctgov.Client)
        with self.assertRaises(ValueError):
            sources.client_module("webofscience")


# =============================================================================================
# 6. H016: hamis kulcsokkal semmilyen kimenetben nincs titok (akkor sem, ha a szerver visszhangozza)
# =============================================================================================

class EchoOpener(object):
    """Rosszindulatú/hibás szerver: a kérés URL-jét és fejléceit visszhangozza a válasz törzsében."""

    def __init__(self, status_for=None):
        self.status_for = status_for or {}
        self.n = 0

    def open(self, req, timeout=None):
        self.n += 1
        body = json.dumps({"echo_url": req.full_url, "echo_headers": dict(req.header_items()),
                           "message": "your key is %s" % dict(req.header_items()).get("X-els-apikey")}).encode()
        host = urllib.parse.urlsplit(req.full_url).hostname
        status = self.status_for.get(host, 200)
        hdrs = {"Content-Type": "application/json", "Set-Cookie": "s=1"}
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", _msg(hdrs), io.BytesIO(body))
        return FakeResp(status, hdrs, body)



class TestNoSecretLeaks(unittest.TestCase):

    def test_full_check_run_with_fake_keys(self):
        env = dict(FAKE_ENV)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        events = []
        rec = net.CassetteRecorder(os.path.join(tmp, "cassette.json"), name="h016-test", env=env)
        clock = FakeClock()
        h = net.HttpClient(cache_dir=os.path.join(tmp, "cache"), opener=EchoOpener({"api.elsevier.com": 401}),
                           env=env, recorder=rec, log=events.append, clock=clock, sleep=clock.sleep,
                           use_env_cassette=False)
        out = io.StringIO()
        code = sources.main(["--check", "--json"], stdout=out, env=env, http=h)
        self.assertIn(code, (0, 3))
        texts = [out.getvalue(), json.dumps(events, ensure_ascii=False)]
        # közvetlen hívások is (hibák szövegével)
        for fn in (lambda: pubmed.Client(h).esearch("x"), lambda: openalex.Client(h).works(filter="type:review").all(),
                   lambda: scopus.Client(h).search("TITLE(x)").all(), lambda: europepmc.Client(h).lookup_pmid("1")):
            try:
                texts.append(json.dumps(fn(), ensure_ascii=False, default=str))
            except net.SourceUnavailable as exc:
                texts.append(str(exc) + json.dumps(exc.to_dict(), ensure_ascii=False) + (exc.body_excerpt or ""))
            except (net.HttpError, net.ParseError) as exc:
                texts.append(str(exc))
        h.close()
        out2 = io.StringIO()
        sources.main([], stdout=out2, env=env, http=h)
        texts.append(out2.getvalue())
        for root, _d, files in os.walk(tmp):
            for fn in files:
                with open(os.path.join(root, fn), "rb") as fh:
                    texts.append(fh.read().decode("utf-8", "replace"))
        blob = "\n".join(texts)
        for name, value in env.items():
            self.assertNotIn(value, blob, name)
        self.assertNotIn("kutato.teszt", blob)
        self.assertEqual(net.find_secret_leaks(blob, env), [])
        self.assertTrue(os.path.exists(os.path.join(tmp, "cassette.json")))


# =============================================================================================
# 7. Áttekintés-felkutatás (L1): finder
# =============================================================================================

class TestFinderText(unittest.TestCase):

    def test_build_queries(self):
        q = finder.build_queries(BCG_BLOCKS, since="2015", until="2024-06")
        self.assertEqual(q["pubmed"]["term"],
                         '((tuberculosis[tiab]) AND ("BCG vaccine"[tiab] OR "BCG vaccination"[tiab])) AND '
                         '(systematic[sb] OR meta-analysis[pt] OR "meta-analysis"[ti] OR "systematic review"[ti])')
        self.assertEqual((q["pubmed"]["datetype"], q["pubmed"]["mindate"], q["pubmed"]["maxdate"]),
                         ("pdat", "2015/01/01", "2024/06/30"))
        # Europe PMC: a P/I kifejezések cím/absztrakt/kulcsszó mezőre szűkítve (élő próba: mező nélkül a teljes
        # szövegben is keresett — BCG: 892 találat a 78 helyett)
        self.assertEqual(q["europepmc"]["query"],
                         '((TITLE_ABS:tuberculosis OR KW:tuberculosis) AND (TITLE_ABS:"BCG vaccine" OR '
                         'TITLE_ABS:"BCG vaccination" OR KW:"BCG vaccine" OR KW:"BCG vaccination")) AND '
                         '(PUB_TYPE:"systematic-review" OR PUB_TYPE:"meta-analysis" OR TITLE:"meta-analysis" OR '
                         'TITLE:"systematic review") AND FIRST_PDATE:[2015-01-01 TO 2024-06-30]')
        self.assertEqual(q["openalex"]["filter"], [("type", "review"), ("from_publication_date", "2015-01-01"),
                                                   ("to_publication_date", "2024-06-30")])
        self.assertEqual(q["scopus"]["query"],
                         'TITLE-ABS-KEY(tuberculosis) AND TITLE-ABS-KEY("BCG vaccine" OR "BCG vaccination") AND '
                         '(TITLE-ABS-KEY("meta-analysis") OR TITLE-ABS-KEY("systematic review")) AND DOCTYPE(re) '
                         'AND PUBYEAR > 2014 AND PUBYEAR < 2025')
        mesh = finder.build_queries({"P": {"terms": ["asthma"], "mesh": ["Asthma"]}, "I": ["montelukast"]})
        self.assertIn('"Asthma"[mh]', mesh["pubmed"]["term"])
        self.assertIn('MESH:"Asthma"', mesh["europepmc"]["query"])
        plain = finder.build_queries("BCG vaccine[tiab] AND tuberculosis")
        self.assertEqual(plain["openalex"]["search"], "BCG vaccine AND tuberculosis")
        with self.assertRaises(ValueError):
            finder.build_queries(BCG_BLOCKS, since="2015-13-45x")
        with self.assertRaises(ValueError):
            finder.build_queries([])

    def test_search_date_patterns(self):
        cases = [
            ("We searched MEDLINE, Embase and CENTRAL from inception to 15 March 2020.", "2020-03-15", "day"),
            ("Databases were searched up to June 30, 2025.", "2025-06-30", "day"),
            ("The search was conducted in October 2025.", "2025-10", "month"),
            ("Date of search: 2019-04-23.", "2019-04-23", "day"),
            ("Searches were run until 2018.", "2018", "year"),
            ("The last search was performed on 1st Feb 2021; trials published between 2000 and 2019 were eligible.",
             "2021-02-01", "day"),
        ]
        for text, value, prec in cases:
            sd = finder.extract_search_date(text, container="pubmed:1")
            self.assertIsNotNone(sd, text)
            self.assertEqual((sd["value"], sd["precision"]), (value, prec), text)
            self.assertIn(sd["quote"], text)
            self.assertEqual(sd["locator"], {"container": "pubmed:1", "section": "Abstract"})
        for text in ("Studies published between 2000 and 2019 were included.",
                     "PubMed and Embase were searched for studies published from January 2021 to February 2026.",
                     "We searched PubMed for articles published up to March 2020.",
                     "The trial ran from 2010 to 2015."):
            self.assertIsNone(finder.extract_search_date(text), text)
        long_text = "We searched " + ", ".join("database%d" % i for i in range(80)) + " up to 3 May 2019."
        sd = finder.extract_search_date(long_text)
        self.assertEqual(sd["value"], "2019-05-03")
        self.assertLessEqual(len(sd["quote"]), finder.QUOTE_MAX)
        self.assertIn("3 May 2019", sd["quote"])

    def test_k_reported_patterns(self):
        k = finder.extract_k("We included 24 trials in 26 reports involving 3000 participants.")
        self.assertEqual((k["value"], k["unit"]), (24, "trials"))
        self.assertEqual(k["also"], [{"value": 26, "unit": "reports"}])
        k = finder.extract_k("RESULTS: Twenty studies comprising 3,056 participants were included.")
        self.assertEqual((k["value"], k["unit"]), (20, "studies"))
        k = finder.extract_k("Twelve randomised controlled trials met the inclusion criteria.")
        self.assertEqual((k["value"], k["unit"]), (12, "trials"))
        k = finder.extract_k("Out of 5202 records screened, six RCTs including 3485 patients were eligible.")
        self.assertEqual((k["value"], k["unit"]), (6, "trials"))
        self.assertIsNone(finder.extract_k("We screened 5202 records."))

    def test_signals(self):
        sig, quotes = finder.extract_signals(
            "We searched MEDLINE, Embase, CENTRAL and Web of Science. Risk of bias was assessed with RoB 2. "
            "Registration: PROSPERO CRD42019123456. Reported per PRISMA 2020.")
        self.assertEqual(sig, {"protocol_registered": True, "registration_id": "CRD42019123456",
                               "databases_searched_n": 4, "rob_assessed": True, "prisma_mentioned": True})
        self.assertEqual(sorted(q["about"] for q in quotes),
                         ["signal:prisma_mentioned", "signal:protocol_registered", "signal:rob_assessed"])
        sig, quotes = finder.extract_signals("A narrative overview.")
        self.assertEqual(set(sig.values()), {None})  # a hiány nem bizonyíték a hiányra
        self.assertEqual(quotes, [])

    def test_review_ids(self):
        self.assertEqual(finder.review_id_for({"pmid": {"value": "31038197"}, "doi": {"value": "10.1/x"}}),
                         "rv-pmid-31038197")
        self.assertRegex(finder.review_id_for({"doi": "10.1002/14651858.cd012915.pub2"}), r"^rv-doi-[0-9a-f]{10}$")
        self.assertEqual(finder.review_id_for({"eid": "2-s2.0-85000000001"}), "rv-eid-85000000001")
        self.assertEqual(finder.review_id_for({"openalex": "W123"}), "rv-oa-w123")
        self.assertEqual(finder.review_id_for({"pmcid": "PMC77"}), "rv-pmc-77")
        self.assertIsNone(finder.review_id_for({}))
        self.assertRegex(finder.review_id_for({}, fallback="PPR:PPR1"), r"^rv-x-[0-9a-f]{10}$")


def _esummary_doc(pmid, title, year, journal, doi=None, pmc=None, pubtype=("Journal Article",), month="Mar"):
    ids = [{"idtype": "pubmed", "value": pmid}]
    if doi:
        ids.append({"idtype": "doi", "value": doi})
    if pmc:
        ids.append({"idtype": "pmc", "value": pmc})
    return {"uid": pmid, "title": title, "pubdate": "%d %s" % (year, month), "sortpubdate": "%d/03/01 00:00" % year,
            "source": journal, "authors": [{"name": "Alpha A", "authtype": "Author"}], "lastauthor": "Omega O",
            "articleids": ids, "pubtype": list(pubtype), "volume": "1", "issue": "2", "pages": "1-10",
            "lang": ["eng"]}


def _efetch_xml(pmid, abstract, retracted_in=None, pubtypes=()):
    cc = ""
    if retracted_in:
        cc = ("<CommentsCorrectionsList><CommentsCorrections RefType='RetractionIn'><RefSource>x</RefSource>"
              "<PMID>%s</PMID></CommentsCorrections></CommentsCorrectionsList>" % retracted_in)
    pts = "".join("<PublicationType>%s</PublicationType>" % p for p in pubtypes)
    return ("<PubmedArticle><MedlineCitation><PMID>%s</PMID><Article><ArticleTitle>t</ArticleTitle>"
            "<Abstract><AbstractText>%s</AbstractText></Abstract><PublicationTypeList>%s</PublicationTypeList>"
            "</Article>%s</MedlineCitation></PubmedArticle>" % (pmid, abstract, pts, cc))


SECRET_SENTENCE = "UNIQUEABSTRACTMARKER sentence that must never be stored"


class StubPubmed(object):
    def __init__(self):
        docs = [
            _esummary_doc("111", "BCG vaccine for preventing tuberculosis: a systematic review and meta-analysis", 2020,
                          "Cochrane Database Syst Rev", doi="10.1002/14651858.CD000001.pub2", pmc="PMC111",
                          pubtype=("Journal Article", "Systematic Review")),
            _esummary_doc("222", "BCG vaccine for preventing tuberculosis", 2015, "Cochrane Database Syst Rev",
                          doi="10.1002/14651858.CD000001", pubtype=("Journal Article", "Review")),
            _esummary_doc("333", "Retracted meta-analysis of BCG vaccine in tuberculosis", 2018, "Fixture J",
                          doi="10.5555/retracted.1", pubtype=("Journal Article", "Meta-Analysis",
                                                              "Retracted Publication")),
        ]
        self.summ = dict((d["uid"], pubmed.summary_record(d)) for d in docs)
        abstract = ("We searched MEDLINE and Embase up to 15 March 2019. %s. Twelve randomised controlled trials "
                    "were included. The protocol was registered in PROSPERO (CRD42019123456). We followed PRISMA."
                    % SECRET_SENTENCE)
        xml = "<PubmedArticleSet>%s%s%s</PubmedArticleSet>" % (
            _efetch_xml("111", abstract), _efetch_xml("222", "No date here."),
            _efetch_xml("333", "x", retracted_in="999", pubtypes=("Retracted Publication",)))
        self.fetched = dict((r["pmid"], r) for r in pubmed.parse_efetch(xml))
        self.calls = []

    def esearch_all(self, term, cap=5000, **kw):
        self.calls.append(("esearch_all", term, cap))
        return {"count": 3, "ids": ["111", "222", "333"], "querytranslation": None, "retrieved": 3, "complete": True,
                "error": None, "warnings": []}

    def esummary(self, pmids):
        return [dict(self.summ[p]) for p in pmids if p in self.summ]

    def efetch_records(self, pmids):
        return [self.fetched[p] for p in pmids if p in self.fetched]


class StubEPMC(object):
    def __init__(self):
        self.items = [
            {"id": "111", "source": "MED", "pmid": "111", "pmcid": "PMC111", "doi": "10.1002/14651858.CD000001.pub2",
             "title": "BCG vaccine for preventing tuberculosis: a systematic review and meta-analysis",
             "pubYear": "2020", "isOpenAccess": "Y", "inEPMC": "Y", "license": "cc by", "abstractText": "x"},
            {"id": "PPR9", "source": "PPR", "doi": "10.5555/NARR.1", "title": "BCG and tuberculosis: an overview",
             "pubYear": "2021", "firstPublicationDate": "2021-05-01", "isOpenAccess": "N"},
        ]
        self.lookups = []

    def search(self, query, result_type="core", page_size=100, max_results=None):
        items = list(self.items)
        return net.Paged("europepmc", lambda st: (items, len(items), None), max_results=max_results)

    def lookup_many_pmids(self, pmids, result_type="lite", chunk=50):
        self.lookups.append(list(pmids))
        return {}


class StubOpenAlex(object):
    def works(self, filter=None, search=None, select=None, per_page=200, max_results=None):
        w = {"id": "https://openalex.org/W42", "doi": "https://doi.org/10.5555/narr.1", "display_name":
             "BCG and tuberculosis: an overview", "publication_year": 2021, "publication_date": "2021-05-01",
             "type": "review", "authorships": [{"author": {"display_name": "Gamma G"}}]}
        return net.Paged("openalex", lambda st: ([w], 1, None), max_results=max_results)


class StubScopus(object):
    def __init__(self):
        self.queries = []

    def search(self, query, max_results=None, normalize=True):
        self.queries.append(query)
        e = scopus.entry_record({"eid": "2-s2.0-85000000009", "dc:title": "Scopus-only meta-analysis of BCG vaccine "
                                 "and tuberculosis", "prism:coverDate": "2022-02-01", "prism:doi": "10.5555/sc.9",
                                 "subtype": "re", "subtypeDescription": "Review", "dc:creator": "Delta D."})
        return net.Paged("scopus", lambda st: ([e], 1, None), max_results=max_results)


class TestFindReviews(unittest.TestCase):

    def _run(self, sources_arg=None, env=None, clients=None, **filters):
        clock = FakeClock()
        h = net.HttpClient(env=env or {}, clock=clock, sleep=clock.sleep, use_env_cassette=False,
                           opener=FakeOpener([AssertionError("nem mehet ki hálózatra")]))
        clients = clients or {"pubmed": StubPubmed(), "europepmc": StubEPMC(), "openalex": StubOpenAlex()}
        events = []
        res = finder.find_reviews(BCG_BLOCKS, dict({"max_per_source": 50}, **filters), sources=sources_arg, http=h,
                                  env=env or {}, clients=clients, progress=events.append)
        return res, events, clients

    def test_merge_rank_cochrane_versions_retraction(self):
        res, events, clients = self._run()
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(res["warnings"], [])
        self.assertEqual(sorted(res["sources"]), ["europepmc", "openalex", "pubmed"])  # Scopus kulcs nélkül: csendben kimarad
        by_id = dict((c["review_id"], c) for c in res["candidates"])
        doi_rid = finder.review_id_for({"doi": "10.5555/narr.1"})
        self.assertEqual(sorted(by_id), sorted(["rv-pmid-111", "rv-pmid-222", "rv-pmid-333", doi_rid]))
        top = by_id["rv-pmid-111"]
        self.assertEqual(res["candidates"][0]["review_id"], "rv-pmid-111")
        self.assertEqual(top["found_in"], ["europepmc", "pubmed"])
        self.assertEqual(top["ids"]["pmid"]["source"], "pubmed")
        self.assertEqual(top["ids"]["pmcid"]["value"], "PMC111")
        self.assertEqual(top["fulltext"], {"available": True, "route": "europepmc_oa", "license": "cc by",
                                           "checked_at": "2026-10-05T12:00:00Z"})
        self.assertTrue(top["is_cochrane"])
        self.assertEqual(top["cochrane"], {"cd_number": "CD000001", "version": 2})
        self.assertEqual(top["search_date"], {"value": "2019-03-15", "precision": "day", "fallback": False,
                                              "evidence_id": None})
        self.assertEqual(top["k_reported"]["value"], 12)
        self.assertEqual(top["signals"]["registration_id"], "CRD42019123456")
        self.assertTrue(top["signals"]["prisma_mentioned"])
        self.assertEqual(top["rank"]["components"]["cochrane"], 1.0)
        # régebbi Cochrane-változat
        old = by_id["rv-pmid-222"]
        self.assertEqual((old["status"], old["superseded_by"]), ("superseded", "rv-pmid-111"))
        self.assertTrue(old["search_date"]["fallback"])  # nem közölt → megjelenés − 12 hónap (H008)
        self.assertEqual(old["search_date"]["value"], "2014-03")
        # visszavont: a lista végén, kizárási javaslattal, 0 pontszámmal
        ret = by_id["rv-pmid-333"]
        self.assertEqual(res["candidates"][-1]["review_id"], "rv-pmid-333")
        self.assertTrue(ret["flags"]["retracted"])
        self.assertEqual(ret["proposal"]["action"], "exclude")
        self.assertEqual(ret["rank"]["score"], 0.0)
        self.assertIn({"type": "RetractionIn", "pmid": "999"}, ret["flags"]["updates"])
        # DOI szerint összevont Europe PMC + OpenAlex rekord, narratív gyanú
        narr = by_id[doi_rid]
        self.assertEqual(narr["found_in"], ["europepmc", "openalex"])
        self.assertEqual(narr["ids"]["doi"]["source"], "europepmc")
        self.assertEqual(narr["ids"]["openalex"], {"value": "W42", "source": "openalex", "via": "openalex.works",
                                                   "at": "2026-10-05T12:00:00Z"})
        self.assertTrue(narr["flags"]["narrative_suspect"])
        self.assertEqual(narr["proposal"]["action"], "check")
        # a hiányzó Europe PMC-adatot kötegelten kérte (csak a PubMed-ben talált PMID-ekre)
        self.assertEqual(clients["europepmc"].lookups, [["222", "333"]])
        self.assertEqual([e["phase"] for e in events][-1], "done")
        self.assertIn("merge", [e["phase"] for e in events])

    def test_outputs_schema_valid_with_evidence_and_no_abstract(self):
        res, _e, _c = self._run()
        for c in res["candidates"]:
            doc = finder.to_review_doc(c)
            self.assertEqual(validate(doc, "review"), [], c["review_id"])
            for v in doc["ids"].values():
                self.assertIn(v["source"], ("pubmed", "europepmc", "openalex", "scopus"))  # csak API-ból
            for ev in doc["evidence"]:
                self.assertLessEqual(len(ev["quote"]), 300)
        top = finder.to_review_doc(res["candidates"][0])
        ev = dict((e["evidence_id"], e) for e in top["evidence"])
        self.assertIn("15 March 2019", ev[top["search_date"]["evidence_id"]]["quote"])
        self.assertIn("Twelve randomised", ev[top["k_reported"]["evidence_id"]]["quote"])
        self.assertTrue(set(top["signals"]["evidence_ids"]) <= set(ev))
        self.assertEqual(ev[top["search_date"]["evidence_id"]]["locator"], {"container": "pubmed:111",
                                                                            "section": "Abstract"})
        blob = json.dumps(res, ensure_ascii=False)
        self.assertNotIn("UNIQUEABSTRACTMARKER", blob)  # az absztrakt csak memóriában (N4)
        self.assertNotIn('"abstract"', blob)
        state_search = SCHEMA["state"]["properties"]["searches"]
        self.assertEqual(validate_sub(res["searches"], state_search), [])
        self.assertEqual([s["purpose"] for s in res["searches"]], ["review_discovery"] * 3)

    def test_scopus_requested_without_key_warns(self):
        res, _e, _c = self._run(sources_arg="pubmed,scopus")
        self.assertEqual(res["exit_code"], 3)
        self.assertEqual([(w["code"], w["source"]) for w in res["warnings"]], [("H014", "scopus")])
        self.assertFalse(res["sources"]["scopus"]["searched"])
        self.assertEqual(res["sources"]["scopus"]["status"], "not_configured")

    def test_scopus_with_key_merges_and_is_flagged_unverified(self):
        clients = {"pubmed": StubPubmed(), "scopus": StubScopus()}
        res, _e, clients = self._run(sources_arg="pubmed,scopus", env=dict(FAKE_ENV), clients=clients)
        self.assertEqual(res["exit_code"], 0)
        sc = [c for c in res["candidates"] if c["found_in"] == ["scopus"]][0]
        self.assertEqual(sc["review_id"], "rv-doi-" + finder.review_id_for({"doi": "10.5555/sc.9"})[7:])
        self.assertEqual(sc["ids"]["eid"]["source"], "scopus")
        self.assertTrue(sc["flags"]["unverified_live"])
        self.assertIn("DOCTYPE(re)", clients["scopus"].queries[0])
        self.assertEqual(validate(finder.to_review_doc(sc), "review"), [])
        self.assertNotIn(FAKE_ENV["MA_SCOPUS_APIKEY"], json.dumps(res))

    def test_unavailable_source_is_partial_not_fatal(self):
        class DownOpenAlex(object):
            def works(self, **kw):
                def fetch(st):
                    raise net.SourceUnavailable("openalex", "rate_limited", reset_at="2026-10-06T00:51:34Z")
                return net.Paged("openalex", fetch)
        clients = {"pubmed": StubPubmed(), "europepmc": StubEPMC(), "openalex": DownOpenAlex()}
        res, _e, _c = self._run(clients=clients)
        self.assertEqual(res["exit_code"], 3)
        self.assertEqual(res["sources"]["openalex"]["status"], "rate_limited")
        self.assertEqual(res["sources"]["openalex"]["reset_at"], "2026-10-06T00:51:34Z")
        self.assertEqual([w["code"] for w in res["warnings"]], ["H014"])
        self.assertEqual(len(res["candidates"]), 4)  # a DOI-s rekord az Europe PMC-ből megvan

    def test_empty_query_rejected(self):
        with self.assertRaises(ValueError):
            finder.find_reviews(None, http=net.HttpClient(env={}, use_env_cassette=False), env={})


class TestFindReviewsCassettes(unittest.TestCase):
    """A valós L1-folyamat rögzített válaszokkal (PubMed, Europe PMC, OpenAlex valós 429-cel)."""

    def _replay(self, name, blocks, srcs, max_n):
        h, player = cassette_client("finder/" + name)
        res = finder.find_reviews(blocks, {"max_per_source": max_n}, sources=srcs, http=h, env={})
        self.assertEqual(player.misses, [])
        return res

    def test_bcg_partial_with_openalex_quota(self):
        res = self._replay("bcg_reviews", BCG_BLOCKS, "pubmed,europepmc,openalex", 6)
        self.assertEqual(res["exit_code"], 3)
        codes = sorted((w["code"], w["source"]) for w in res["warnings"])
        self.assertEqual(codes, [("H012", "europepmc"), ("H012", "pubmed"), ("H014", "openalex")])
        self.assertEqual(res["sources"]["openalex"]["status"], "rate_limited")
        self.assertEqual(len(res["candidates"]), 10)
        scores = [c["rank"]["score"] for c in res["candidates"] if c["status"] == "candidate"
                  and not c["flags"]["retracted"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for c in res["candidates"]:
            self.assertRegex(c["review_id"], r"^rv-pmid-\d+$")
            self.assertEqual(validate(finder.to_review_doc(c), "review"), [], c["review_id"])
        pm = [s for s in res["searches"] if s["source"] == "pubmed"][0]
        self.assertEqual((pm["count_total"], pm["count_retrieved"], pm["complete"]), (90, 6, False))
        self.assertEqual(pm["search_id"], "s-pubmed-20261005T120000Z")
        self.assertEqual(validate_sub(res["searches"], SCHEMA["state"]["properties"]["searches"]), [])

    def test_soy(self):
        res = self._replay("soy_reviews", SOY_BLOCKS, "pubmed,europepmc", 4)
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(sorted(set(w["code"] for w in res["warnings"])), ["H012"])
        self.assertEqual(len(res["candidates"]), 7)  # újrarögzítve a TITLE_ABS-szűkítéssel (2026-10-05)
        oa = [c for c in res["candidates"] if c["fulltext"]["route"] == "europepmc_oa"]
        self.assertTrue(oa)
        for c in oa:
            self.assertTrue(c["ids"]["pmcid"]["value"].startswith("PMC"))


if __name__ == "__main__":
    unittest.main()
