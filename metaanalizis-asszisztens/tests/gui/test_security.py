# -*- coding: utf-8 -*-
"""ma_gui.security: indítókód → token, kérésszűrés (Host/Origin/Sec-Fetch-Site/metódus/token/
Content-Type/méret), válaszfejlécek, aláírt fájl-URL, útvonal-biztonság, korlátozott JSON,
boríték (terv 7.1 T1–T11, 3.4, 4.2, 8.4)."""
import ast
import base64
import hashlib
import hmac
import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.client import parse_headers
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ma_gui import security as sec  # noqa: E402
from ma_gui import schema_lite  # noqa: E402

PORT = 8790


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make_manager(clock=None):
    return sec.SecurityManager(port=PORT, clock=clock or FakeClock())


def session(mgr):
    return mgr.exchange_launch_code(mgr.new_launch_code())


def hdrs(**extra):
    h = {"Host": "127.0.0.1:%d" % PORT}
    for k, v in extra.items():
        h[k.replace("_", "-")] = v
    return h


# ---------------------------------------------------------------------------

class LaunchCodeTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr = make_manager(self.clock)

    def test_exchange_once(self):
        code = self.mgr.new_launch_code()
        token = self.mgr.exchange_launch_code(code)
        self.assertIsNotNone(token)
        self.assertTrue(self.mgr.check_token(token))
        self.assertTrue(self.mgr.has_session())

    def test_replay_rejected(self):
        code = self.mgr.new_launch_code()
        self.assertIsNotNone(self.mgr.exchange_launch_code(code))
        self.assertIsNone(self.mgr.exchange_launch_code(code))

    def test_expiry_60s(self):
        code = self.mgr.new_launch_code()
        self.clock.advance(59.9)
        self.assertIsNotNone(self.mgr.exchange_launch_code(code))
        code2 = self.mgr.new_launch_code()
        self.clock.advance(60.0)
        self.assertIsNone(self.mgr.exchange_launch_code(code2))
        self.assertEqual(sec.LAUNCH_CODE_TTL, 60)

    def test_expired_code_is_consumed(self):
        code = self.mgr.new_launch_code()
        self.clock.advance(61)
        self.assertIsNone(self.mgr.exchange_launch_code(code))
        self.clock.t -= 61       # visszaállított óra sem éleszti fel
        self.assertIsNone(self.mgr.exchange_launch_code(code))

    def test_forged_and_malformed_codes(self):
        code = self.mgr.new_launch_code()
        forged = code[:-1] + ("A" if code[-1] != "A" else "B")
        for bad in (forged, "", None, 123, b"x", code + "x", "é" * 10, "x" * 1000):
            self.assertIsNone(self.mgr.exchange_launch_code(bad), repr(bad)[:20])
        self.assertIsNotNone(self.mgr.exchange_launch_code(code))

    def test_code_shape(self):
        codes = {self.mgr.new_launch_code() for _ in range(5)}
        self.assertEqual(len(codes), 5)
        for c in codes:
            self.assertGreaterEqual(len(c), 32)
            self.assertRegex(c, r"^[A-Za-z0-9_-]+$")

    def test_pending_codes_are_bounded(self):
        codes = [self.mgr.new_launch_code() for _ in range(sec.MAX_PENDING_LAUNCH_CODES + 3)]
        self.assertEqual(len(self.mgr._launch_codes), sec.MAX_PENDING_LAUNCH_CODES)
        self.assertIsNotNone(self.mgr.exchange_launch_code(codes[-1]))

    def test_constant_time_compare_is_used(self):
        code = self.mgr.new_launch_code()
        with mock.patch.object(sec.hmac, "compare_digest", wraps=hmac.compare_digest) as cd:
            token = self.mgr.exchange_launch_code(code)
            self.assertGreaterEqual(cd.call_count, 1)
            cd.reset_mock()
            self.mgr.check_token(token)
            self.assertGreaterEqual(cd.call_count, 1)

    def test_tokens_unique_and_bounded(self):
        tokens = [session(self.mgr) for _ in range(sec.MAX_SESSION_TOKENS + 2)]
        self.assertEqual(len(set(tokens)), len(tokens))
        self.assertTrue(self.mgr.check_token(tokens[-1]))
        for t in tokens:
            self.assertGreaterEqual(len(t), 43)
            self.assertRegex(t, r"^[A-Za-z0-9_-]+$")

    def test_check_token_rejects(self):
        token = session(self.mgr)
        for bad in (None, "", token[:-1], token + "x", "ő" * 43, 42, token.upper() if token.upper() != token else "x"):
            self.assertFalse(self.mgr.check_token(bad))
        other = make_manager()
        self.assertFalse(other.check_token(token))

    def test_revoke(self):
        token = session(self.mgr)
        code = self.mgr.new_launch_code()
        self.mgr.revoke_tokens()
        self.assertFalse(self.mgr.check_token(token))
        self.assertIsNone(self.mgr.exchange_launch_code(code))

    def test_concurrent_exchange_single_winner(self):
        code = self.mgr.new_launch_code()
        results = []
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait()
            results.append(self.mgr.exchange_launch_code(code))

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_launch_url(self):
        url = self.mgr.launch_url()
        self.assertRegex(url, r"^http://127\.0\.0\.1:%d/#launch=[A-Za-z0-9_-]+$" % PORT)
        code = url.split("#launch=", 1)[1]
        self.assertIsNotNone(self.mgr.exchange_launch_code(code))
        self.assertEqual(self.mgr.launch_url(code="abc"), "http://127.0.0.1:%d/#launch=abc" % PORT)

    def test_port_required(self):
        mgr = sec.SecurityManager()
        with self.assertRaises(RuntimeError):
            mgr.launch_url()
        with self.assertRaises(RuntimeError):
            mgr.check_request("GET", "/", {"Host": "127.0.0.1:1"})
        mgr.port = 5555
        self.assertIsNone(mgr.check_request("GET", "/", {"Host": "127.0.0.1:5555"}))
        self.assertIsNone(mgr.check_request("GET", "/", {"Host": "127.0.0.1:6000"}, port=6000))


class DeriveKeyTests(unittest.TestCase):
    def test_deterministic_and_separated(self):
        a, b = make_manager(), make_manager()
        k1 = a.derive_key("file-url")
        self.assertEqual(len(k1), 32)
        self.assertEqual(k1, a.derive_key("file-url"))
        self.assertNotEqual(k1, a.derive_key("other"))
        self.assertNotEqual(k1, b.derive_key("file-url"))

    def test_bad_purpose(self):
        mgr = make_manager()
        for bad in ("", None, b"x"):
            with self.assertRaises(ValueError):
                mgr.derive_key(bad)


class FileUrlTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr = make_manager(self.clock)
        self.docs = {"pmid:31234567": "06_kezirat/a.pdf", "doi:10.1000/x|y z": "docs/b c.pdf"}

    def lookup(self, doc):
        return self.docs.get(doc)

    def test_roundtrip(self):
        for doc, rel in self.docs.items():
            url = self.mgr.sign_file_url(doc, rel)
            self.assertTrue(url.startswith("/f/"))
            self.assertEqual(url.count("/"), 4, url)     # a doc kódolt, nincs benne '/'
            self.assertEqual(self.mgr.verify_file_url(url, self.lookup), (doc, rel))
            self.assertEqual(self.mgr.verify_file_url(url + "?x=1", self.lookup), (doc, rel))

    def test_signature_construction(self):
        url = self.mgr.sign_file_url("pmid:1", "a/b.pdf", ttl=600)
        _, _, doc_q, exp, sig = url.split("/")
        self.assertEqual(doc_q, "pmid%3A1")
        self.assertEqual(int(exp), int(self.clock()) + 600)
        key = self.mgr.derive_key("file-url")
        mac = hmac.new(key, ("pmid%%3A1|%s|a/b.pdf" % exp).encode(), hashlib.sha256).digest()
        self.assertEqual(sig, base64.urlsafe_b64encode(mac).rstrip(b"=").decode())

    def test_expired(self):
        url = self.mgr.sign_file_url("pmid:31234567", "06_kezirat/a.pdf")
        self.clock.advance(600)
        self.assertIsNotNone(self.mgr.verify_file_url(url, self.lookup))
        self.clock.advance(1)
        self.assertIsNone(self.mgr.verify_file_url(url, self.lookup))

    def test_forged_signature(self):
        url = self.mgr.sign_file_url("pmid:31234567", "06_kezirat/a.pdf")
        head, sig = url.rsplit("/", 1)
        flipped = head + "/" + ("A" if sig[0] != "A" else "B") + sig[1:]
        self.assertIsNone(self.mgr.verify_file_url(flipped, self.lookup))

    def test_tampered_exp_and_doc(self):
        url = self.mgr.sign_file_url("pmid:31234567", "06_kezirat/a.pdf")
        _, f, doc_q, exp, sig = url.split("/")
        longer = "/".join(["", f, doc_q, str(int(exp) + 100), sig])
        self.assertIsNone(self.mgr.verify_file_url(longer, self.lookup))
        other_doc = "/".join(["", f, "doi%3A10.1000%2Fx%7Cy%20z", exp, sig])
        self.assertIsNone(self.mgr.verify_file_url(other_doc, self.lookup))

    def test_path_bound(self):
        url = self.mgr.sign_file_url("pmid:31234567", "06_kezirat/a.pdf")
        self.docs["pmid:31234567"] = "06_kezirat/masik.pdf"     # a jegyzék közben változott
        self.assertIsNone(self.mgr.verify_file_url(url, self.lookup))

    def test_unknown_doc_and_other_process(self):
        url = self.mgr.sign_file_url("pmid:999", "x.pdf")
        self.assertIsNone(self.mgr.verify_file_url(url, self.lookup))
        url2 = self.mgr.sign_file_url("pmid:31234567", "06_kezirat/a.pdf")
        other = make_manager(self.clock)
        self.assertIsNone(other.verify_file_url(url2, self.lookup))

    def test_parse_malformed(self):
        good = self.mgr.sign_file_url("pmid:1", "a.pdf")
        _, f, doc_q, exp, sig = good.split("/")
        bad_urls = [
            "/f/", "/f/%s/%s" % (doc_q, exp), "/f/%s/%s/%s/x" % (doc_q, exp, sig),
            "/f/%s/abc/%s" % (doc_q, sig), "/f/%s/-5/%s" % (doc_q, sig),
            "/f/%s/%s/%s" % (doc_q, exp, sig[:-1]), "/f/%s/%s/%s=" % (doc_q, exp, sig[:-1]),
            "/f/pmid:1/%s/%s" % (exp, sig),           # nem kanonikus kódolás
            "/f/pmid%%3a1/%s/%s" % (exp, sig),        # kisbetűs hexa: nem kanonikus
            "/f/%%FF/%s/%s" % (exp, sig),             # nem UTF-8
            "/f/%%0A/%s/%s" % (exp, sig),             # vezérlőkarakter
            "/g/%s/%s/%s" % (doc_q, exp, sig), None, 42,
        ]
        for u in bad_urls:
            self.assertIsNone(sec.SecurityManager.parse_file_url(u), repr(u))
        self.assertEqual(sec.SecurityManager.parse_file_url(good), ("pmid:1", int(exp), sig))

    def test_verify_signature_type_guards(self):
        url = self.mgr.sign_file_url("pmid:1", "a.pdf")
        doc, exp, sig = sec.SecurityManager.parse_file_url(url)
        self.assertTrue(self.mgr.verify_file_signature(doc, exp, sig, "a.pdf"))
        self.assertFalse(self.mgr.verify_file_signature(doc, str(exp), sig, "a.pdf"))
        self.assertFalse(self.mgr.verify_file_signature(doc, True, sig, "a.pdf"))
        self.assertFalse(self.mgr.verify_file_signature(doc, exp, sig, ""))
        self.assertFalse(self.mgr.verify_file_signature(doc, exp, "é" * 43, "a.pdf"))
        self.assertFalse(self.mgr.verify_file_signature(doc, exp + 10 ** 6, sig, "a.pdf"))

    def test_lookup_returning_garbage(self):
        url = self.mgr.sign_file_url("pmid:1", "a.pdf")
        for ret in (None, "", 5, ["a.pdf"]):
            self.assertIsNone(self.mgr.verify_file_url(url, lambda d, r=ret: r))

    def test_sign_arg_validation(self):
        for doc, rel, ttl in (("", "a", 10), ("d", "", 10), ("d", "a", 0), ("d", "a", -1),
                              ("d", "a", sec.FILE_URL_MAX_TTL + 1), ("d", "a", True), ("d", "a", 1.5),
                              (None, "a", 10)):
            with self.assertRaises(ValueError):
                self.mgr.sign_file_url(doc, rel, ttl)
        self.assertEqual(sec.FILE_URL_TTL, 600)


# ---------------------------------------------------------------------------

class CheckRequestTests(unittest.TestCase):
    def setUp(self):
        self.mgr = make_manager()
        self.token = session(self.mgr)

    def check(self, method, path, **h):
        return self.mgr.check_request(method, path, hdrs(**h))

    def api(self, method="GET", path="/api/engine", **h):
        h.setdefault("X_MA_Token", self.token)
        if method != "GET":
            h.setdefault("Content_Type", "application/json")
        return self.check(method, path, **h)

    def assertRejected(self, result, status, code=None):
        self.assertIsNotNone(result, "elfogadva, holott %s várt" % status)
        self.assertEqual(result[0], status, result)
        if code:
            self.assertEqual(result[1], code)
        self.assertIsInstance(result[2], str)

    # -- T2 Host --
    def test_host_matrix(self):
        ok = ["127.0.0.1:%d" % PORT, "localhost:%d" % PORT]
        bad = [None, "", "evil.com", "evil.com:%d" % PORT, "127.0.0.1.nip.io:%d" % PORT,
               "127.0.0.1:%d" % (PORT + 1), "127.0.0.1", "localhost", "[::1]:%d" % PORT,
               "0.0.0.0:%d" % PORT, "127.0.0.2:%d" % PORT, "localhost.:%d" % PORT,
               "127.0.0.1:%d@evil.com" % PORT, " 127.0.0.1:%d.evil" % PORT]
        for host in ok:
            self.assertIsNone(self.mgr.check_request("GET", "/", {"Host": host}), host)
        for host in bad:
            h = {} if host is None else {"Host": host}
            self.assertRejected(self.mgr.check_request("GET", "/", h), 403, "FORBIDDEN")

    def test_duplicate_host_header(self):
        raw = b"Host: 127.0.0.1:8790\r\nHost: evil.com\r\n\r\n"
        msg = parse_headers(io.BytesIO(raw))
        self.assertRejected(self.mgr.check_request("GET", "/", msg), 400, "BAD_REQUEST")
        self.assertRejected(self.mgr.check_request("GET", "/", {"Host": ["127.0.0.1:8790", "x"]}), 400)

    def test_http_message_headers(self):
        raw = ("Host: 127.0.0.1:8790\r\nx-ma-token: %s\r\ncontent-type: application/json\r\n"
               "Content-Length: 2\r\n\r\n" % self.token).encode()
        msg = parse_headers(io.BytesIO(raw))
        self.assertIsNone(self.mgr.check_request("POST", "/api/validate", msg))

    def test_dict_headers_case_insensitive(self):
        h = {"host": "127.0.0.1:%d" % PORT, "x-ma-token": self.token}
        self.assertIsNone(self.mgr.check_request("GET", "/api/engine", h))

    def test_messages_do_not_echo_values(self):
        r = self.mgr.check_request("GET", "/", {"Host": "titkos-gep.example:1"})
        self.assertNotIn("titkos", r[2])
        r = self.check("GET", "/", Origin="http://titkos.example")
        self.assertNotIn("titkos", r[2])

    # -- T1 Origin --
    def test_origin_matrix(self):
        self.assertIsNone(self.check("GET", "/"))
        self.assertIsNone(self.check("GET", "/", Origin="http://127.0.0.1:%d" % PORT))
        self.assertIsNone(self.mgr.check_request(
            "GET", "/", {"Host": "localhost:%d" % PORT, "Origin": "http://localhost:%d" % PORT}))
        bad = ["http://localhost:%d" % PORT, "null", "https://127.0.0.1:%d" % PORT,
               "http://127.0.0.1:%d/" % PORT, "http://127.0.0.1:%d" % (PORT + 1), "http://evil.com",
               "http://127.0.0.1", ""]
        for origin in bad:
            self.assertRejected(self.check("GET", "/", Origin=origin), 403, "FORBIDDEN")
        self.assertRejected(self.api("POST", "/api/validate", Origin="http://evil.com"), 403)
        self.assertRejected(self.mgr.check_request(
            "GET", "/", {"Host": "localhost:%d" % PORT, "Origin": "http://127.0.0.1:%d" % PORT}), 403)

    # -- T1 Sec-Fetch-Site --
    def test_sec_fetch_site_matrix(self):
        url = self.mgr.sign_file_url("pmid:1", "a.pdf")
        cases = [
            ("/", "cross-site", 403), ("/", "same-site", None), ("/", "none", None), ("/", "same-origin", None),
            (url, "cross-site", 403), (url, "same-site", 403), (url, "none", None), (url, "same-origin", None),
            (url, None, None), (url, "bogus", 403),
            ("/api/engine", "cross-site", 403), ("/api/engine", "same-site", 403),
            ("/api/engine", "none", 403), ("/api/engine", "same-origin", None), ("/api/engine", None, None),
        ]
        for path, site, want in cases:
            h = {"X_MA_Token": self.token}
            if site is not None:
                h["Sec_Fetch_Site"] = site
            res = self.check("GET", path, **h)
            if want is None:
                self.assertIsNone(res, (path, site))
            else:
                self.assertRejected(res, want, "FORBIDDEN")

    # -- T1 metódusok --
    def test_methods(self):
        for path in ("/", "/api/engine", "/api/validate", "/f/x/1/y"):
            for method in ("OPTIONS", "HEAD", "DELETE", "TRACE", "PATCH", "CONNECT", "get", "PROPFIND"):
                self.assertRejected(self.api(method, path), 405, "METHOD_NOT_ALLOWED")
        for method in ("POST", "PUT"):
            self.assertRejected(self.check(method, "/", Content_Type="application/json"), 405)
            self.assertRejected(self.check(method, "/f/x/1/y", Content_Type="application/json"), 405)

    def test_options_preflight_from_evil_origin(self):
        res = self.check("OPTIONS", "/api/validate", Origin="http://evil.com",
                         Access_Control_Request_Method="POST", Access_Control_Request_Headers="x-ma-token")
        self.assertRejected(res, 405)

    # -- T4 token --
    def test_api_requires_token_even_for_get(self):
        for method, path in (("GET", "/api/engine"), ("GET", "/api/table?dataset=x"), ("POST", "/api/validate"),
                             ("PUT", "/api/table"), ("POST", "/api/table/import"), ("GET", "/api"),
                             ("GET", "/api/session"), ("PUT", "/api/session")):
            self.assertRejected(self.check(method, path, Content_Type="application/json"), 403, "FORBIDDEN")
            self.assertRejected(self.check(method, path, Content_Type="application/json",
                                           X_MA_Token="rossz"), 403)
            self.assertRejected(self.check(method, path, Content_Type="application/json",
                                           X_MA_Token=self.token + "x"), 403)
        self.assertIsNone(self.api("GET", "/api/engine"))
        self.assertIsNone(self.api("POST", "/api/validate"))
        self.assertIsNone(self.api("PUT", "/api/table"))

    def test_session_route_without_token(self):
        self.assertIsNone(self.check("POST", "/api/session", Content_Type="application/json"))
        self.assertRejected(self.check("POST", "/api/session", Content_Type="text/plain"), 415)
        self.assertRejected(self.check("POST", "/api/session?x", Content_Type="text/plain"), 415)

    def test_page_and_file_without_token(self):
        self.assertIsNone(self.check("GET", "/"))
        self.assertIsNone(self.check("GET", self.mgr.sign_file_url("pmid:1", "a.pdf")))

    # -- T1 Content-Type --
    def test_content_type(self):
        ok = ["application/json", "application/json; charset=utf-8", "Application/JSON",
              "application/json;charset=UTF-8", 'application/json; charset="utf-8"']
        bad = [None, "", "text/plain", "text/plain; charset=utf-8", "application/x-www-form-urlencoded",
               "multipart/form-data; boundary=x", "application/json; charset=latin-1",
               "application/json-patch+json", "application/jsonx", "application/json; foo=bar"]
        for ct in ok:
            self.assertIsNone(self.check("POST", "/api/validate", X_MA_Token=self.token, Content_Type=ct), ct)
        for ct in bad:
            h = {"X_MA_Token": self.token}
            if ct is not None:
                h["Content_Type"] = ct
            self.assertRejected(self.check("PUT", "/api/table", **h), 415, "UNSUPPORTED_MEDIA")
        self.assertIsNone(self.check("GET", "/api/engine", X_MA_Token=self.token))

    def test_token_checked_before_media_type(self):
        self.assertRejected(self.check("POST", "/api/validate", Content_Type="text/plain"), 403)

    # -- T11 méret --
    def test_body_size_limits(self):
        small, big = sec.MAX_BODY_BYTES, sec.MAX_TABLE_BODY_BYTES
        self.assertEqual(small, 64 * 1024)
        self.assertEqual(big, 4 * 1024 * 1024)
        self.assertIsNone(self.api("POST", "/api/log/decision", Content_Length=str(small)))
        self.assertRejected(self.api("POST", "/api/log/decision", Content_Length=str(small + 1)), 413,
                            "PAYLOAD_TOO_LARGE")
        for route in ("/api/table", "/api/table/import", "/api/validate", "/api/compare", "/api/analyze"):
            method = "PUT" if route == "/api/table" else "POST"
            self.assertIsNone(self.api(method, route, Content_Length=str(big)), route)
            self.assertIsNone(self.api(method, route + "?dataset=x", Content_Length=str(big)), route)
            self.assertRejected(self.api(method, route, Content_Length=str(big + 1)), 413)
        self.assertEqual(sec.body_limit("/api/table?dataset=a"), big)
        self.assertEqual(sec.body_limit("/api/table/x"), small)
        # értékelés- és GRADE-dokumentum: 1 MB (TRIPOD+AI AI-vázlat indoklásokkal); a többi 64 KB marad
        doc = sec.MAX_DOC_BODY_BYTES
        self.assertEqual(doc, 1024 * 1024)
        for route in ("/api/appraisals/S1/tripod-ai?rater=ai", "/api/appraisals/S1/rob2/check",
                      "/api/appraisals/import", "/api/grade/o1"):
            self.assertEqual(sec.body_limit(route), doc, route)
            self.assertIsNone(self.api("PUT", route, Content_Length=str(doc)), route)
            self.assertRejected(self.api("PUT", route, Content_Length=str(doc + 1)), 413, "PAYLOAD_TOO_LARGE")
        for route in ("/api/appraisals", "/api/gradex", "/api/sof/o1", "/api/log/decision"):
            self.assertEqual(sec.body_limit(route), small, route)

    def test_bad_content_length_and_chunked(self):
        for cl in ("abc", "-1", "1.5", " ", "0x10", "\u0661\u0662", "9" * 20):
            self.assertRejected(self.api("POST", "/api/validate", Content_Length=cl), 400, "BAD_REQUEST")
        self.assertRejected(self.api("POST", "/api/validate", Transfer_Encoding="chunked"), 400)
        self.assertRejected(self.check("GET", "/", Transfer_Encoding="chunked"), 400)

    # -- útvonal-épség --
    def test_path_sanity(self):
        bad = ["http://evil/api/engine", "*", "", "api/engine", "//api/engine", "/api//engine",
               "/%61pi/engine", "/api/../f/x", "/api/./engine", "/a\\b", "/api/%2e%2e/x",
               "/api/engine\x00", "/api/\u00e9", "/api/ engine", "/f/%2E%2E/1/x"]
        for path in bad:
            self.assertRejected(self.api("GET", path), 400, "BAD_REQUEST")
        self.assertIsNone(self.api("GET", "/api/engine?q=../../x"))
        self.assertIsNone(self.api("GET", "/api/runs/"))

    def test_route_class(self):
        self.assertEqual(sec.route_class("/api/x"), "api")
        self.assertEqual(sec.route_class("/api"), "api")
        self.assertEqual(sec.route_class("/apix"), "page")
        self.assertEqual(sec.route_class("/f/a/1/b"), "file")
        self.assertEqual(sec.route_class("/"), "page")


# ---------------------------------------------------------------------------

class HeaderTests(unittest.TestCase):
    MANDATORY = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "X-Frame-Options": "DENY",
    }

    def as_dict(self, headers):
        names = [n.lower() for n, _ in headers]
        self.assertEqual(len(names), len(set(names)), "ismétlődő fejléc")
        return dict(headers)

    def test_all_kinds_have_mandatory_headers_and_no_cors(self):
        for kind in sec.HEADER_KINDS:
            nonce = sec.csp_nonce() if kind == "html" else None
            h = self.as_dict(sec.base_headers(nonce=nonce, kind=kind))
            for name, value in self.MANDATORY.items():
                self.assertEqual(h.get(name), value, (kind, name))
            self.assertIn("Content-Security-Policy", h)
            self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])
            self.assertFalse([n for n in h if n.lower().startswith("access-control-")], kind)

    def test_html_csp_exact(self):
        nonce = sec.csp_nonce()
        h = self.as_dict(sec.base_headers(nonce=nonce, kind="html"))
        want = ("default-src 'none'; script-src 'nonce-{n}'; style-src 'nonce-{n}'; img-src 'self' data: blob:; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'").format(n=nonce)
        self.assertEqual(h["Content-Security-Policy"], want)
        self.assertNotIn("unsafe-inline", want)

    def test_html_requires_valid_nonce(self):
        for bad in (None, "", "short", "abc def ghijklmnop", "a" * 20 + "'; script-src *",
                    "a" * 20 + "\r\nSet-Cookie: x=1", "a" * 200):
            with self.assertRaises(ValueError):
                sec.base_headers(nonce=bad, kind="html")

    def test_svg_sandbox(self):
        h = self.as_dict(sec.base_headers(kind="svg"))
        csp = h["Content-Security-Policy"]
        self.assertEqual(csp.split(";")[0].strip(), "sandbox")
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("allow-scripts", csp)

    def test_api_and_file_csp(self):
        api = self.as_dict(sec.base_headers())["Content-Security-Policy"]
        self.assertIn("default-src 'none'", api)
        self.assertIn("sandbox", api)
        f = self.as_dict(sec.base_headers(kind="file"))["Content-Security-Policy"]
        self.assertNotIn("sandbox", f)       # a böngésző PDF-nézője sandboxban nem fut

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            sec.base_headers(kind="json")

    def test_nonce_unique_and_well_formed(self):
        nonces = {sec.csp_nonce() for _ in range(200)}
        self.assertEqual(len(nonces), 200)
        for n in nonces:
            self.assertRegex(n, r"^[A-Za-z0-9_-]{22,}$")
            sec.html_csp(n)

    def test_file_kind_and_content_type(self):
        self.assertEqual(sec.file_kind("a/b.SVG"), "svg")
        self.assertEqual(sec.file_kind("a/b.pdf"), "file")
        self.assertEqual(sec.content_type_for("x.pdf"), "application/pdf")
        self.assertEqual(sec.content_type_for(Path("x") / "y.PNG"), "image/png")
        self.assertEqual(sec.content_type_for("x.svg"), "image/svg+xml")
        self.assertEqual(sec.content_type_for("x.bin"), "application/octet-stream")
        self.assertEqual(sec.content_type_for("noext"), "application/octet-stream")


# ---------------------------------------------------------------------------

class PathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ma_gui_sec_"))
        self.root = self.tmp / "projekt"
        (self.root / "03_adatok").mkdir(parents=True)
        (self.root / "03_adatok" / "bcg.csv").write_text("a\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "cikk.pdf").write_bytes(b"%PDF-1.4")
        (self.root / "docs" / "titok.key").write_bytes(b"k")
        self.outside = self.tmp / "kivul"
        self.outside.mkdir()
        (self.outside / "secret.pdf").write_bytes(b"%PDF")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def symlink(self, link, target, is_dir=False):
        try:
            os.symlink(str(target), str(link), target_is_directory=is_dir)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink itt nem hozható létre")

    def assertUnsafe(self, rel, code="FORBIDDEN", **kw):
        with self.assertRaises(sec.UnsafePath) as cm:
            sec.safe_resolve(self.root, rel, **kw)
        self.assertEqual(cm.exception.code, code, repr(rel))
        self.assertEqual(cm.exception.http, sec.ERROR_CODES[code])
        return cm.exception

    def test_ok_paths(self):
        p = sec.safe_resolve(self.root, "03_adatok/bcg.csv")
        self.assertEqual(p, (self.root / "03_adatok" / "bcg.csv").resolve())
        self.assertTrue(p.is_absolute())
        p2 = sec.safe_resolve(str(self.root), "03_adatok/uj.prov.json")
        self.assertEqual(p2.name, "uj.prov.json")
        for name in ("CONSOLE.pdf", "COM10.pdf", "LPT.txt", "connect.pdf", "nullable.csv", "auxiliary.md",
                     "a/CONx/b.pdf", "árvíztűrő tükörfúrógép.pdf", "v1.2.pdf", "01-a_b.c.csv"):
            sec.safe_resolve(self.root, name)

    def test_absolute_and_drive(self):
        for rel in ("/etc/passwd", str(self.outside / "secret.pdf"), "C:/Windows/win.ini", "c:x", "C:\\x",
                    "\\\\server\\share\\x", "\\x", "//server/share"):
            self.assertUnsafe(rel)

    def test_dotdot(self):
        for rel in ("..", "../x", "a/../../x", "a/..", "03_adatok/../../kivul/secret.pdf", "a..b.pdf",
                    "..\\x", "a/.../b"):
            self.assertUnsafe(rel)

    def test_ads(self):
        for rel in ("docs/cikk.pdf:Zone.Identifier", "docs/cikk.pdf::$DATA", "docs:stream/x"):
            self.assertUnsafe(rel)

    def test_reserved_names(self):
        for name in ("CON", "con", "Con.txt", "nul.tar.gz", "NUL", "AUX", "aux.pdf", "PRN", "prn.csv",
                     "COM1", "com9.pdf", "COM0", "LPT1", "lpt9.txt", "LPT5.x.y", "COM\u00b9", "lpt\u00b2.pdf",
                     "CONIN$", "conout$.txt", "CON .txt", "a/CON/b.pdf", "a/b/NUL"):
            self.assertUnsafe(name)

    def test_trailing_dot_space_and_bad_chars(self):
        for rel in ("x.pdf.", "x.pdf ", "dir./x", "a/b /c", "a<b", "a>b", 'a"b', "a|b", "a?b", "a*b",
                    "a\tb", "a\nb", "a\x00b", "a\x7fb"):
            self.assertUnsafe(rel)

    def test_empty_and_dot_segments(self):
        for rel in ("", ".", "./a", "a/./b", "a//b", "a/", "/"):
            self.assertUnsafe(rel)

    def test_hidden(self):
        (self.root / ".claude").mkdir()
        self.assertUnsafe(".git/config")
        self.assertUnsafe("a/.env")
        p = sec.safe_resolve(self.root, ".claude/settings.json", allow_hidden=True)
        self.assertEqual(p.name, "settings.json")

    def test_types_and_length(self):
        for rel in (None, 5, b"a.pdf"):
            self.assertUnsafe(rel)
        self.assertUnsafe("a" * (sec.MAX_REL_PATH_LEN + 1))
        self.assertUnsafe("a/" + "b" * (sec.MAX_SEGMENT_LEN + 1))
        from pathlib import PurePosixPath
        self.assertEqual(sec.safe_resolve(self.root, PurePosixPath("03_adatok/bcg.csv")).name, "bcg.csv")

    def test_symlink_file_escape(self):
        self.symlink(self.root / "docs" / "link.pdf", self.outside / "secret.pdf")
        self.assertUnsafe("docs/link.pdf")
        self.assertUnsafe("docs/link.pdf", must_exist=True)

    def test_symlink_dir_escape(self):
        self.symlink(self.root / "kulso", self.outside, is_dir=True)
        self.assertUnsafe("kulso/secret.pdf")
        self.assertUnsafe("kulso/nincs.pdf")           # nem létező cél sem csúszhat ki

    def test_dangling_symlink_escape(self):
        self.symlink(self.root / "dang.pdf", self.outside / "nincs-ilyen.pdf")
        self.assertUnsafe("dang.pdf")
        self.assertUnsafe("dang.pdf", must_exist=True)        # kívülre mutat: 403, nem 404

    def test_symlink_loop(self):
        self.symlink(self.root / "loop1", self.root / "loop2", is_dir=True)
        self.symlink(self.root / "loop2", self.root / "loop1", is_dir=True)
        with self.assertRaises(sec.UnsafePath):
            sec.safe_resolve(self.root, "loop1/x.pdf", must_exist=True)

    def test_symlink_inside_root_ok(self):
        self.symlink(self.root / "docs" / "alias.pdf", self.root / "docs" / "cikk.pdf")
        p = sec.safe_resolve(self.root, "docs/alias.pdf", allowed_ext=sec.DOC_EXTENSIONS, must_exist=True)
        self.assertEqual(p.name, "cikk.pdf")

    def test_symlink_extension_laundering(self):
        self.symlink(self.root / "docs" / "kulcs.pdf", self.root / "docs" / "titok.key")
        self.assertUnsafe("docs/kulcs.pdf", allowed_ext=sec.DOC_EXTENSIONS)

    def test_symlinked_root_is_fine(self):
        link_root = self.tmp / "root_link"
        self.symlink(link_root, self.root, is_dir=True)
        p = sec.safe_resolve(link_root, "docs/cikk.pdf", must_exist=True)
        self.assertEqual(p, (self.root / "docs" / "cikk.pdf").resolve())

    def test_must_exist_and_root(self):
        self.assertUnsafe("docs/nincs.pdf", code="NOT_FOUND", must_exist=True)
        self.assertEqual(sec.safe_resolve(self.root, "docs/nincs.pdf").name, "nincs.pdf")
        with self.assertRaises(sec.UnsafePath) as cm:
            sec.safe_resolve(self.tmp / "nincs-gyoker", "a.pdf")
        self.assertEqual(cm.exception.code, "NOT_FOUND")
        with self.assertRaises(sec.UnsafePath):
            sec.safe_resolve(self.root / "docs" / "cikk.pdf", "a.pdf")

    def test_extension_allowlist(self):
        sec.safe_resolve(self.root, "docs/cikk.pdf", allowed_ext=sec.DOC_EXTENSIONS)
        sec.safe_resolve(self.root, "docs/CIKK.PDF", allowed_ext=sec.DOC_EXTENSIONS)
        for rel in ("docs/titok.key", "docs/x.pdf.exe", "docs/x.html", "docs/noext", "docs/x.js"):
            self.assertUnsafe(rel, allowed_ext=sec.DOC_EXTENSIONS)
        self.assertTrue(sec.check_extension("a.PDF", ["pdf"]))
        self.assertTrue(sec.check_extension("a.svg", {".svg"}))
        self.assertFalse(sec.check_extension("a.html", {".html"}))     # sosem szolgálható ki
        self.assertFalse(sec.check_extension(".pdf", {".pdf"}))
        self.assertFalse(sec.check_extension("a.pdf.exe", {".pdf"}))
        self.assertTrue(sec.check_extension(Path("x") / "y.tiff", sec.RUN_ARTIFACT_EXTENSIONS))
        self.assertTrue(sec.DOC_EXTENSIONS.isdisjoint(sec.NEVER_SERVE_EXTENSIONS))
        self.assertTrue(sec.RUN_ARTIFACT_EXTENSIONS.isdisjoint(sec.NEVER_SERVE_EXTENSIONS))

    def test_messages_do_not_echo_path(self):
        e = self.assertUnsafe("titkos-beteg-nev/../x")
        self.assertNotIn("titkos", e.message)

    def test_is_within(self):
        r = self.root.resolve()
        self.assertTrue(sec.is_within(r / "a" / "b", r))
        self.assertTrue(sec.is_within(r, r))
        self.assertFalse(sec.is_within(self.outside.resolve(), r))
        self.assertFalse(sec.is_within(Path(str(r) + "_masik") / "x", r))


# ---------------------------------------------------------------------------

class JsonLimitTests(unittest.TestCase):
    def assertBad(self, raw, code="BAD_REQUEST", **kw):
        with self.assertRaises(sec.SecurityError) as cm:
            sec.loads_limited(raw, **kw)
        self.assertEqual(cm.exception.code, code)
        self.assertEqual(cm.exception.http, sec.ERROR_CODES[code])
        return cm.exception

    def test_valid(self):
        self.assertEqual(sec.loads_limited(b'{"a": [1, 2.5, null, true, "x"]}'),
                         {"a": [1, 2.5, None, True, "x"]})
        self.assertEqual(sec.loads_limited('{"név": "ő"}'), {"név": "ő"})
        self.assertEqual(sec.loads_limited(bytearray(b"[]")), [])
        self.assertEqual(sec.loads_limited(memoryview(b"3")), 3)
        self.assertEqual(sec.loads_limited(b' {"x": 1} \n'), {"x": 1})
        with self.assertRaises(TypeError):
            sec.loads_limited({"a": 1})

    def test_size(self):
        body = b'{"a": "' + b"x" * 100 + b'"}'
        self.assertEqual(sec.loads_limited(body, max_bytes=len(body))["a"], "x" * 100)
        self.assertBad(body, code="PAYLOAD_TOO_LARGE", max_bytes=len(body) - 1)
        self.assertBad(b"[" + b"1," * 40000 + b"1]", code="PAYLOAD_TOO_LARGE")
        big = b"[" + b"1," * 40000 + b"1]"
        self.assertEqual(len(sec.loads_limited(big, max_bytes=sec.MAX_TABLE_BODY_BYTES)), 40001)
        # a méret UTF-8 bájtban számít
        self.assertBad('"' + "ő" * 40 + '"', code="PAYLOAD_TOO_LARGE", max_bytes=60)

    def test_depth(self):
        ok = "[" * 32 + "]" * 32
        sec.loads_limited(ok)
        self.assertBad("[" * 33 + "]" * 33)
        mixed_ok = '{"a":' * 16 + "[" * 16 + "1" + "]" * 16 + "}" * 16
        sec.loads_limited(mixed_ok)
        self.assertBad('{"a":' * 17 + "[" * 16 + "1" + "]" * 16 + "}" * 17)
        sec.loads_limited("[" * 5 + "]" * 5, max_depth=5)
        self.assertBad("[" * 6 + "]" * 6, max_depth=5)
        self.assertEqual(sec.loads_limited("1", max_depth=0), 1)

    def test_extreme_depth_fast(self):
        big = sec.MAX_TABLE_BODY_BYTES
        t0 = time.perf_counter()
        self.assertBad("[" * 500000 + "]" * 500000, max_bytes=big)
        self.assertBad('{"a":' * 200000 + "1" + "}" * 200000, max_bytes=big)
        self.assertBad("[" * 600000, max_bytes=big)
        self.assertLess(time.perf_counter() - t0, 2.0)

    def test_proto_keys(self):
        for raw in ('{"__proto__": {}}', '{"a": [{"__proto__": 1}]}', '{"constructor": {"prototype": 1}}',
                    '{"x": {"prototype": 1}}', '[{"ok": 1}, {"constructor": 2}]'):
            self.assertBad(raw)
        self.assertEqual(sec.loads_limited('{"__proto": 1, "Constructor": 2, "proto": 3}'),
                         {"__proto": 1, "Constructor": 2, "proto": 3})
        self.assertEqual(sec.loads_limited('["__proto__"]'), ["__proto__"])   # értékként nem kulcs

    def test_nan_infinity(self):
        for raw in ("NaN", "[Infinity]", '{"a": -Infinity}', "[1e400]", "[-1e400]", '{"a": 1E999}'):
            self.assertBad(raw)
        self.assertEqual(sec.loads_limited("[1e300, -1e-300, 0.0]")[0], 1e300)

    def test_duplicate_keys(self):
        self.assertBad('{"a": 1, "a": 2}')
        self.assertBad('{"x": {"b": 1, "b": 1}}')

    def test_numbers(self):
        self.assertBad("[" + "9" * 100 + "]")
        self.assertBad("[1." + "0" * 100 + "]")
        self.assertEqual(sec.loads_limited("[" + "9" * 30 + "]")[0], int("9" * 30))

    def test_encoding_and_syntax(self):
        for raw in (b"", b"   ", b"\xff\xfe[]", b'["\xc3"]', "\ufeff[]".encode("utf-8"), b"[1,]",
                    b"{'a': 1}", b"[1] x", b"[1] [2]", b'{"a" 1}', b"undefined", b'"\\ud800"x'):
            self.assertBad(raw)

    def test_messages_do_not_echo_values(self):
        e = self.assertBad('{"study": "TITKOS-BETEG", "x": NaN}')
        self.assertNotIn("TITKOS", e.message)
        e = self.assertBad('{"study": "TITKOS-BETEG" "x"}')
        self.assertNotIn("TITKOS", e.message)
        e = self.assertBad('{"TITKOS": 1, "TITKOS": 2}')
        self.assertNotIn("TITKOS", e.message)

    def test_table_limits(self):
        sec.check_table_limits(["c"] * 200, [["x"] * 200] * 5)
        sec.check_table_limits(["c"], [["x"]] * 5000)
        for header, rows in ((["c"] * 201, []), (["c"], [["x"]] * 5001), (["c"], [["x"] * 201])):
            with self.assertRaises(sec.SecurityError) as cm:
                sec.check_table_limits(header, rows)
            self.assertEqual(cm.exception.http, 413)
        self.assertEqual((sec.MAX_ROWS, sec.MAX_COLUMNS, sec.MAX_JSON_DEPTH), (5000, 200, 32))


# ---------------------------------------------------------------------------

class EnvelopeTests(unittest.TestCase):
    SPEC_TABLE = {
        "BAD_REQUEST": 400, "FORBIDDEN": 403, "NOT_FOUND": 404, "CONFLICT": 409, "GATE_BLOCKED": 409,
        "PAYLOAD_TOO_LARGE": 413, "UNSUPPORTED_MEDIA": 415, "VALIDATION": 422, "LOCKED": 423,
        "CAPABILITY_MISSING": 424, "PLUGIN_FAILED": 502, "TIMEOUT": 504, "INTERNAL": 500,
    }

    def test_error_code_table(self):
        for code, http in self.SPEC_TABLE.items():
            self.assertEqual(sec.ERROR_CODES[code], http)
        self.assertEqual(set(sec.ERROR_CODES) - set(self.SPEC_TABLE), {"METHOD_NOT_ALLOWED"})
        self.assertEqual(sec.ERROR_CODES["METHOD_NOT_ALLOWED"], 405)

    def test_error_envelope(self):
        env = sec.error_envelope("GATE_BLOCKED", "2 nyitott blocker.",
                                 {"blockers": [{"id": 3, "stage": "S09", "title": "x"}]})
        self.assertEqual(env, {"ok": False, "error": {
            "code": "GATE_BLOCKED", "http": 409, "message": "2 nyitott blocker.",
            "details": {"blockers": [{"id": 3, "stage": "S09", "title": "x"}]}}})
        self.assertNotIn("details", sec.error_envelope("NOT_FOUND", "nincs")["error"])
        with self.assertRaises(ValueError):
            sec.error_envelope("NOPE", "x")
        self.assertEqual(schema_lite.validate(env, sec.ENVELOPE_SCHEMA), [])

    def test_ok_envelope(self):
        env = sec.ok_envelope("szk.ma.validation/v1", {"a": 1},
                              meta={"engine": "0.2.0", "elapsed_ms": 1, "project_rev": 41,
                                    "request_id": sec.new_request_id()})
        self.assertEqual(env["warnings"], [])
        self.assertTrue(env["ok"])
        self.assertEqual(schema_lite.validate(env, sec.ENVELOPE_SCHEMA), [])
        self.assertRegex(env["meta"]["request_id"], r"^q_[0-9a-f]{8}$")

    def test_envelope_schema_rejects(self):
        bad = [
            {"ok": True, "schema": "x", "data": {}, "warnings": []},
            {"ok": False, "error": {"code": "WHATEVER", "http": 400, "message": "x"}},
            {"ok": False, "error": {"code": "FORBIDDEN", "http": True, "message": "x"}},
            {"ok": "true", "schema": None, "data": {}, "warnings": [], "meta": {}},
            {"ok": True, "schema": None, "data": {}, "warnings": [], "meta": {"elapsed_ms": -1}},
        ]
        for env in bad:
            self.assertNotEqual(schema_lite.validate(env, sec.ENVELOPE_SCHEMA), [], env)
        self.assertEqual(schema_lite.check_schema(sec.ENVELOPE_SCHEMA), [])

    def test_security_error(self):
        e = sec.SecurityError("LOCKED", "Zárd be a fájlt az Excelben.")
        self.assertEqual(e.as_tuple(), (423, "LOCKED", "Zárd be a fájlt az Excelben."))
        self.assertEqual(e.envelope()["error"]["http"], 423)
        self.assertIsInstance(e, ValueError)
        with self.assertRaises(ValueError):
            sec.SecurityError("X", "y")
        self.assertIsInstance(sec.UnsafePath("x"), sec.SecurityError)


# ---------------------------------------------------------------------------

STDLIB_OK = {
    "base64", "hashlib", "hmac", "json", "os", "re", "secrets", "threading", "time", "pathlib",
    "urllib", "urllib.parse", "collections", "__future__",
}
FORBIDDEN = {"math", "cmath", "statistics", "random", "decimal"}


class ImportHygieneTests(unittest.TestCase):
    def test_stdlib_only_and_no_statistics(self):
        for name in ("security.py", "schema_lite.py"):
            tree = ast.parse((Path(ROOT) / "ma_gui" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    mods = [node.module or ""]
                for m in mods:
                    self.assertNotIn(m.split(".")[0], FORBIDDEN, (name, m))
                    self.assertIn(m, STDLIB_OK, (name, m))

    def test_no_print_or_logging(self):
        for name in ("security.py", "schema_lite.py"):
            src = (Path(ROOT) / "ma_gui" / name).read_text(encoding="utf-8")
            tree = ast.parse(src)
            calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            self.assertNotIn("print", calls, name)
            self.assertNotIn("import logging", src)


if __name__ == "__main__":
    unittest.main()
