# -*- coding: utf-8 -*-
"""ma_gui.server + router + routes HTTP-szinten, stdlib http.client-tel (terv 8.4, 8.5):
indítókód → token (egyszeri, lejáró), token minden /api/* úton (GET is), Host / Origin /
Sec-Fetch-Site / OPTIONS / Content-Type szabályok, kötelező fejlécek minden válaszon, GET / nonce-os
CSP-vel és adat/token nélkül, tábla GET/PUT ETag-ütközéssel (409 + cellaszintű diff), útvonal-bejárás,
méret- és mélységkorlát, hiba-borítékok, kapu (409 GATE_BLOCKED), activity-lánc, long-poll,
aláírt fájl-URL, egy példány / újraindító kód, tétlenségi leállás, modul-higiénia."""
import ast
import http.client
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from metaelemzes import projekt  # noqa: E402
from ma_gui import activity, router, runtime, schema_lite, security, server, store  # noqa: E402
from ma_gui import caps as caps_mod  # noqa: E402

BCG = os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv")
DATASET = "03_adatok/bcg_oltas_RR.csv"
PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'


class _Clock(object):
    """Eltolható falióra a SecurityManagerhez (indítókód- és fájl-URL-lejárat)."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.time() + self.offset


def _http(port, method, path, body=None, headers=(), timeout=40):
    """Nyers kérés (a Host fejlécet is mi adjuk): (status, [(név, érték)], bájtok)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        names = set()
        for k, v in headers:
            conn.putheader(k, v)
            names.add(k.lower())
        if body is not None and "content-length" not in names:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, resp.getheaders(), data
    finally:
        conn.close()


def _hdict(headers):
    out = {}
    for k, v in headers:
        out.setdefault(k.lower(), v)
    return out


def _start_app(project, **kw):
    app = server.App(project, **kw)
    app.start(port=0)
    t = threading.Thread(target=app.serve_forever, name="test-serve", daemon=True)
    t.start()
    return app, t


def _stop_app(app, thread):
    app.shutdown()
    thread.join(10)


def _fake_caps(tmp, home):
    return caps_mod.Caps(runtime_dir=os.path.join(tmp, "caps"), env={}, home=home)


class ServerHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_gui_srv_"))
        cls.proj = os.path.join(cls.tmp, "proj")
        os.makedirs(cls.proj)
        projekt.init(cls.proj, "Teszt-projekt")
        shutil.copy(BCG, os.path.join(cls.proj, "03_adatok"))
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        cls.rt_base = os.path.join(cls.tmp, "rtbase")
        cls.rdir = runtime.project_runtime_dir(cls.proj, base=cls.rt_base)
        cls.kb_db = os.path.join(cls.tmp, "kb.sqlite")
        cls.clock = _Clock()
        cls.log = io.StringIO()
        cls.app, cls.thread = _start_app(
            cls.proj, kb_db=cls.kb_db, caps=_fake_caps(cls.tmp, cls.home), privacy_home=cls.home,
            privacy_env={}, selftest=True, validate_responses=True, watch_interval=0.2, log_stream=cls.log,
            clock=cls.clock, idle_hours=0, runtime_dir=cls.rdir, single_instance=True)
        cls.port = cls.app.port
        cls.token = cls._exchange(cls.app.new_launch_code())

    @classmethod
    def tearDownClass(cls):
        _stop_app(cls.app, cls.thread)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- segédek
    @classmethod
    def _exchange(cls, code):
        status, _h, data = _http(cls.port, "POST", "/api/session", json.dumps({"launch_code": code}).encode(),
                                 [("Host", "127.0.0.1:%d" % cls.port), ("Content-Type", "application/json")])
        assert status == 200, data
        return json.loads(data)["data"]["token"]

    def req(self, method, path, body=None, raw=None, headers=None, token=True, host=None, ctype=True):
        hdrs = [("Host", host or "127.0.0.1:%d" % self.port)]
        if token:
            hdrs.append(("X-MA-Token", self.token if token is True else token))
        payload = raw
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if payload is not None and ctype:
            hdrs.append(("Content-Type", "application/json"))
        hdrs.extend(headers or [])
        status, h, data = _http(self.port, method, path, payload, hdrs)
        try:
            env = json.loads(data.decode("utf-8")) if data else None
        except ValueError:
            env = None
        return status, _hdict(h), data, env

    def assertError(self, res, http_status, code):
        status, _h, _d, env = res
        self.assertEqual(status, http_status, env)
        self.assertIsInstance(env, dict)
        self.assertIs(env.get("ok"), False)
        self.assertEqual(env["error"]["code"], code, env)
        self.assertEqual(env["error"]["http"], http_status)
        self.assertEqual(schema_lite.validate(env, security.ENVELOPE_SCHEMA), [])
        return env

    def assertOk(self, res, status=200):
        st, _h, _d, env = res
        self.assertEqual(st, status, env)
        self.assertIs(env.get("ok"), True, env)
        self.assertEqual(schema_lite.validate(env, security.ENVELOPE_SCHEMA), [])
        return env

    def copy_dataset(self, name):
        dst = os.path.join(self.proj, "03_adatok", name)
        shutil.copy(BCG, dst)
        return "03_adatok/" + name

    def wait_for(self, pred, timeout=8.0, step=0.05):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            val = pred()
            if val:
                return val
            time.sleep(step)
        return pred()

    # ------------------------------------------------------------------ munkamenet
    def test_launch_code_single_use(self):
        code = self.app.new_launch_code()
        env = self.assertOk(self.req("POST", "/api/session", {"launch_code": code}, token=False))
        tok = env["data"]["token"]
        self.assertTrue(tok)
        self.assertOk(self.req("GET", "/api/engine", token=tok))
        self.assertError(self.req("POST", "/api/session", {"launch_code": code}, token=False), 403, "FORBIDDEN")

    def test_launch_code_expires(self):
        code = self.app.new_launch_code()
        self.clock.offset += security.LAUNCH_CODE_TTL + 1
        try:
            self.assertError(self.req("POST", "/api/session", {"launch_code": code}, token=False), 403, "FORBIDDEN")
        finally:
            self.clock.offset = 0.0

    def test_launch_code_malformed(self):
        for body in ({}, {"launch_code": 5}, {"launch_code": "x" * 300}, {"launch_code": "a", "relaunch_key": "b"},
                     {"foo": "bar"}):
            self.assertError(self.req("POST", "/api/session", body, token=False), 400, "BAD_REQUEST")
        self.assertError(self.req("POST", "/api/session", {"launch_code": "nincs-ilyen-kod"}, token=False),
                         403, "FORBIDDEN")

    def test_relaunch_key_and_server_json(self):
        info = runtime.read_server_info(self.rdir)
        self.assertEqual(info["port"], self.port)
        self.assertEqual(info["pid"], os.getpid())
        raw = (Path(self.rdir) / runtime.SERVER_INFO).read_text(encoding="utf-8")
        self.assertNotIn(self.token, raw)
        self.assertNotIn("token", raw.lower())
        url, _info = runtime.relaunch(self.rdir)
        code = url.split("#launch=", 1)[1]
        self.assertTrue(self._exchange(code))
        self.assertError(self.req("POST", "/api/session", {"relaunch_key": "rossz-kulcs-123456789"}, token=False),
                         403, "FORBIDDEN")

    def test_second_instance_relaunches(self):
        second = server.App(self.proj, runtime_dir=self.rdir, single_instance=True, selftest=False, kb_build=False,
                            caps_refresh=False, log_stream=None, privacy_home=self.home, privacy_env={})
        with self.assertRaises(server.AlreadyRunning):
            second.start(port=0)
        second.close()
        self.assertIsNotNone(runtime.read_server_info(self.rdir), "a második példány nem törölheti a server.json-t")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"MA_GUI_RUNTIME_DIR": self.rt_base}):
            rc = server.cli_main(["--project", self.proj, "--no-browser"], out=out)
        self.assertEqual(rc, 0, out.getvalue())
        m = re.search(r"http://127\.0\.0\.1:(\d+)/#launch=([A-Za-z0-9_-]+)", out.getvalue())
        self.assertIsNotNone(m, out.getvalue())
        self.assertEqual(int(m.group(1)), self.port)
        self.assertTrue(self._exchange(m.group(2)))

    # ------------------------------------------------------------------ token
    def _concrete(self, pattern):
        return re.sub(r"<[a-z_]+(?::path)?>", "x", pattern)

    def test_token_required_on_every_api_route(self):
        checked = 0
        for r in list(self.app.router.routes):
            if not r.pattern.startswith("/api/") or (r.method, r.pattern) == ("POST", "/api/session"):
                continue
            path = self._concrete(r.pattern)
            body = b"{}" if r.method in ("POST", "PUT") else None
            for tok in (False, "hamis-token"):
                res = self.req(r.method, path, raw=body, token=tok)
                self.assertError(res, 403, "FORBIDDEN")
            checked += 1
        self.assertGreaterEqual(checked, 20)
        self.assertError(self.req("GET", "/api/nincs-ilyen", token=False), 403, "FORBIDDEN")
        self.assertError(self.req("GET", "/api/nincs-ilyen"), 404, "NOT_FOUND")

    # ------------------------------------------------------------------ fejléc-szabályok
    def test_host_header_rules(self):
        for host in ("evil.com", "127.0.0.1.nip.io:%d" % self.port, "127.0.0.1:%d" % (self.port + 1),
                     "127.0.0.1", "[::1]:%d" % self.port):
            self.assertError(self.req("GET", "/api/engine", host=host), 403, "FORBIDDEN")
        self.assertOk(self.req("GET", "/api/engine", host="localhost:%d" % self.port))
        status, _h, _d = _http(self.port, "GET", "/api/engine", None, [("X-MA-Token", self.token)])
        self.assertEqual(status, 403)

    def test_origin_and_fetch_site(self):
        for origin in ("http://evil.com", "null", "http://localhost:%d" % (self.port + 1),
                       "https://127.0.0.1:%d" % self.port):
            self.assertError(self.req("GET", "/api/engine", headers=[("Origin", origin)]), 403, "FORBIDDEN")
        self.assertOk(self.req("GET", "/api/engine", headers=[("Origin", "http://127.0.0.1:%d" % self.port)]))
        self.assertError(self.req("GET", "/api/engine", headers=[("Sec-Fetch-Site", "cross-site")]), 403, "FORBIDDEN")
        self.assertError(self.req("GET", "/", token=False, headers=[("Sec-Fetch-Site", "cross-site")]),
                         403, "FORBIDDEN")
        self.assertOk(self.req("GET", "/api/engine", headers=[("Sec-Fetch-Site", "same-origin")]))

    def test_options_and_other_methods_405_without_cors(self):
        res = self.req("OPTIONS", "/api/engine", token=False,
                       headers=[("Origin", "http://evil.com"), ("Access-Control-Request-Method", "POST"),
                                ("Access-Control-Request-Headers", "x-ma-token")])
        self.assertError(res, 405, "METHOD_NOT_ALLOWED")
        self.assertFalse([k for k in res[1] if k.startswith("access-control-")])
        for method in ("DELETE", "PATCH", "TRACE", "FOO"):
            self.assertError(self.req(method, "/api/engine"), 405, "METHOD_NOT_ALLOWED")
        status, headers, data = _http(self.port, "HEAD", "/", None, [("Host", "127.0.0.1:%d" % self.port)])
        self.assertEqual(status, 405)
        self.assertEqual(data, b"")
        self.assertError(self.req("PUT", "/api/engine", raw=b"{}"), 405, "METHOD_NOT_ALLOWED")

    def test_content_type_rules(self):
        body = json.dumps({"severity": "info", "title": "ct"}).encode()
        for ct in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x",
                   "application/json; charset=latin-1"):
            res = self.req("POST", "/api/log/finding", raw=body, ctype=False, headers=[("Content-Type", ct)])
            self.assertError(res, 415, "UNSUPPORTED_MEDIA")
        self.assertError(self.req("POST", "/api/log/finding", raw=body, ctype=False), 415, "UNSUPPORTED_MEDIA")
        self.assertOk(self.req("POST", "/api/log/finding", raw=body, ctype=False,
                               headers=[("Content-Type", "application/json; charset=utf-8")]))

    # ------------------------------------------------------------------ válaszfejlécek
    def _check_security_headers(self, headers, kind):
        for name, value in security.MANDATORY_HEADERS:
            self.assertEqual(headers.get(name.lower()), value, (kind, name))
        csp = headers.get("content-security-policy")
        self.assertTrue(csp, kind)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertFalse([k for k in headers if k.startswith("access-control-")], kind)
        return csp

    def test_security_headers_on_all_responses(self):
        samples = {
            "html": self.req("GET", "/", token=False),
            "api": self.req("GET", "/api/engine"),
            "403": self.req("GET", "/api/engine", token=False),
            "404": self.req("GET", "/api/nincs"),
            "405": self.req("OPTIONS", "/api/engine"),
            "415": self.req("POST", "/api/log/finding", raw=b"{}", ctype=False, headers=[("Content-Type", "text/plain")]),
            "400": self.req("POST", "/api/log/finding", raw=b"{nem json"),
            "f403": self.req("GET", "/f/x/1/" + "A" * 43, token=False),
        }
        for kind, (status, headers, _d, _e) in samples.items():
            csp = self._check_security_headers(headers, kind)
            if kind == "html":
                self.assertEqual(status, 200)
                self.assertTrue(headers["content-type"].startswith("text/html"))
                self.assertIn("script-src 'nonce-", csp)
            else:
                self.assertTrue(headers["content-type"].startswith("application/json"), kind)

    def test_index_has_nonce_and_no_token_or_data(self):
        status1, h1, body1, _ = self.req("GET", "/", token=False)
        status2, h2, body2, _ = self.req("GET", "/", token=True)
        self.assertEqual((status1, status2), (200, 200))
        n1 = re.search(r"'nonce-([A-Za-z0-9_+/=-]+)'", h1["content-security-policy"]).group(1)
        n2 = re.search(r"'nonce-([A-Za-z0-9_+/=-]+)'", h2["content-security-policy"]).group(1)
        self.assertNotEqual(n1, n2)
        html = body1.decode("utf-8")
        self.assertNotIn("{{CSP_NONCE}}", html)
        tags = re.findall(r"<(script|style)\b([^>]*)>", html)
        self.assertTrue(tags)
        for _tag, attrs in tags:
            self.assertIn('nonce="%s"' % n1, attrs)
        for secret in (self.token, "Aronson", self.proj, "Teszt-projekt"):
            self.assertNotIn(secret, html)
            self.assertNotIn(secret, body2.decode("utf-8"))
        self.assertIsNone(re.search(r"#launch=[A-Za-z0-9_-]{16,}", html))      # nincs benne indítókód

    # ------------------------------------------------------------------ tábla
    def test_table_get_put_roundtrip(self):
        ds = self.copy_dataset("t_roundtrip.csv")
        st, h, _d, env = self.req("GET", "/api/table?dataset=" + quote(ds))
        self.assertEqual(st, 200, env)
        data = env["data"]
        self.assertEqual(h["etag"], '"%s"' % data["etag"])
        self.assertEqual(len(data["rows"]), 13)
        self.assertEqual(data["header"][0], "vizsgálat")
        rows = [{"row_uid": r["row_uid"], "cells": list(r["cells"])} for r in data["rows"]]
        rows[0]["cells"][1] = "7"
        env2 = self.assertOk(self.req("PUT", "/api/table", {"dataset": ds, "header": data["header"], "rows": rows},
                                      headers=[("If-Match", h["etag"])]))
        new = env2["data"]
        self.assertNotEqual(new["etag"], data["etag"])
        self.assertEqual(new["rows"][0]["cells"][1], "7")
        disk = Path(self.proj, ds).read_bytes()
        self.assertEqual(store.sha256_bytes(disk), new["etag"])
        self.assertIn(b"Aronson 1948;7;123", disk)
        env3 = self.assertOk(self.req("GET", "/api/table?dataset=" + quote(ds)))
        self.assertEqual(env3["data"]["etag"], new["etag"])
        lst = self.assertOk(self.req("GET", "/api/table"))
        self.assertIn(ds, [t["dataset"] for t in lst["data"]["tables"]])

    def test_table_conflict_409_with_cell_diff(self):
        ds = self.copy_dataset("t_conflict.csv")
        env = self.assertOk(self.req("GET", "/api/table?dataset=" + quote(ds)))
        data = env["data"]
        path = Path(self.proj, ds)
        raw = path.read_bytes()
        self.assertIn(b"Aronson 1948;4;123", raw)
        path.write_bytes(raw.replace(b"Aronson 1948;4;123", b"Aronson 1948;5;123"))   # „Excel” közben átírja
        rows = [{"row_uid": r["row_uid"], "cells": list(r["cells"])} for r in data["rows"]]
        rows[1]["cells"][2] = "999"
        body = {"dataset": ds, "header": data["header"], "rows": rows}
        err = self.assertError(self.req("PUT", "/api/table", body, headers=[("If-Match", '"%s"' % data["etag"])]),
                               409, "CONFLICT")
        det = err["error"]["details"]
        self.assertTrue(det["base_known"])
        self.assertEqual(det["etag"], store.sha256_bytes(path.read_bytes()))
        hits = [d for d in det["diff"] if d["row_uid"] == data["rows"][0]["row_uid"]]
        self.assertEqual(len(hits), 1, det["diff"])
        self.assertEqual((hits[0]["column"], hits[0]["base"], hits[0]["theirs"]), ("esemény1", "4", "5"))
        self.assertNotIn("Aronson", err["error"]["message"])
        self.assertError(self.req("PUT", "/api/table", body), 409, "CONFLICT")          # If-Match nélkül
        self.assertEqual(path.read_bytes(), raw.replace(b"Aronson 1948;4;123", b"Aronson 1948;5;123"))

    def test_table_locked_423(self):
        ds = self.copy_dataset("t_locked.csv")
        env = self.assertOk(self.req("GET", "/api/table?dataset=" + quote(ds)))
        data = env["data"]
        rows = [{"row_uid": r["row_uid"], "cells": list(r["cells"])} for r in data["rows"]]
        rows[0]["cells"][1] = "8"
        with mock.patch.object(store, "_replace", side_effect=PermissionError(13, "zárolt")):
            err = self.assertError(self.req("PUT", "/api/table", {"dataset": ds, "header": data["header"], "rows": rows},
                                            headers=[("If-Match", data["etag"])]), 423, "LOCKED")
        self.assertIn("Excel", err["error"]["message"])

    def test_dataset_path_traversal(self):
        for bad in ("../../etc/passwd", "/etc/passwd", "C:/x.csv", "03_adatok/CON.csv", "03_adatok/x.csv:ads",
                    "03_adatok\\..\\x.csv", "03_adatok/../../x.csv", "03_adatok/x.exe", "projekt.sqlite"):
            st, _h, _d, env = self.req("GET", "/api/table?dataset=" + quote(bad, safe=""))
            self.assertEqual(st, 403, (bad, env))
            self.assertEqual(env["error"]["code"], "FORBIDDEN")
        self.assertError(self.req("GET", "/api/table?dataset=" + quote("03_adatok/nincs.csv")), 404, "NOT_FOUND")
        self.assertError(self.req("GET", "/api/provenance?dataset=" + quote("../x.csv", safe="")), 403, "FORBIDDEN")
        # az URL-szintű bejárás mindig fut (a symlink-rész Windows-on jog híján kimaradhat, SRV-8)
        for path in ("/f/../../etc/passwd", "/f/%2e%2e/1/x", "/api/../api/engine"):
            st = self.req("GET", path, token=False)[0]
            self.assertIn(st, (400, 403, 404), path)
        outside = os.path.join(self.tmp, "outside.csv")
        shutil.copy(BCG, outside)
        link = os.path.join(self.proj, "03_adatok", "link.csv")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("nincs symlink-jog (Windows fejlesztői mód nélkül): csak a symlink-rész marad ki")
        try:
            self.assertError(self.req("GET", "/api/table?dataset=03_adatok/link.csv"), 403, "FORBIDDEN")
        finally:
            os.unlink(link)

    def test_path_traversal_url_checks_run_without_symlink_rights(self):
        """SRV-8: jog nélküli Windows-fiókon (os.symlink → 1314) is lefutnak az URL-ellenőrzések."""
        sent = []
        real = self.req

        def spy(method, path, *a, **kw):
            sent.append(path)
            return real(method, path, *a, **kw)

        with mock.patch.object(os, "symlink", side_effect=OSError(1314, "A required privilege is not held by the client")), \
                mock.patch.object(self, "req", side_effect=spy):
            with self.assertRaises(unittest.SkipTest):
                self.test_dataset_path_traversal()
        for path in ("/f/../../etc/passwd", "/f/%2e%2e/1/x", "/api/../api/engine"):
            self.assertIn(path, sent)

    def test_table_import_parses_without_saving(self):
        before = sorted(os.listdir(os.path.join(self.proj, "03_adatok")))
        env = self.assertOk(self.req("POST", "/api/table/import",
                                     {"text": "study\tn1\te1\nSmith 2001\t10\t2\nDoe 2003\t12\t3\n"}))
        d = env["data"]
        self.assertEqual(d["header"], ["study", "n1", "e1"])
        self.assertEqual(d["rows"], [["Smith 2001", "10", "2"], ["Doe 2003", "12", "3"]])
        self.assertEqual(d["delimiter"], "\t")
        self.assertEqual(d["phi"], [])
        env2 = self.assertOk(self.req("POST", "/api/table/import",
                                      {"text": "a;b\n1;2\n", "delimiter": ";", "has_header": False}))
        self.assertEqual(env2["data"]["rows"], [["a", "b"], ["1", "2"]])
        self.assertEqual(sorted(os.listdir(os.path.join(self.proj, "03_adatok"))), before)

    def test_phi_scan_blocks_save_until_logged_override(self):
        ds = "03_adatok/t_phi.csv"
        body = {"dataset": ds, "header": ["vizsgálat", "TAJ", "n1"], "rows": [["Kiss 2020", "x", "10"]]}
        err = self.assertError(self.req("PUT", "/api/table", body), 403, "FORBIDDEN")
        self.assertTrue(err["error"]["details"]["phi"])
        self.assertFalse(os.path.exists(os.path.join(self.proj, ds)))
        body["phi_override"] = "Teszt: a TAJ oszlop itt kódolt vizsgálati kar, nem személyazonosító."
        env = self.assertOk(self.req("PUT", "/api/table", body))
        self.assertTrue(any("PHI" in w for w in env["warnings"]))
        items = self.assertOk(self.req("GET", "/api/log/decision"))["data"]["items"]
        self.assertTrue(any(i["decision"].startswith("PHI-gyanú felülbírálva") for i in items))

    def test_provenance_roundtrip(self):
        ds = self.copy_dataset("t_prov.csv")
        t = self.assertOk(self.req("GET", "/api/table?dataset=" + quote(ds)))["data"]
        doc = {"schema": "szk.ma.provenance/v1", "table": ds, "table_sha256": t["etag"],
               "cells": [{"row_uid": t["rows"][0]["row_uid"], "field": "e1", "method": "reported",
                          "source": {"doc": "pmid:1", "page": 3, "locator": "Table 2"}}]}
        st, h, _d, env = self.req("PUT", "/api/provenance?dataset=" + quote(ds), doc)
        self.assertEqual(st, 200, env)
        self.assertTrue(h.get("etag"))
        env2 = self.assertOk(self.req("GET", "/api/provenance?dataset=" + quote(ds)))
        self.assertEqual(len(env2["data"]["provenance"]["cells"]), 1)
        self.assertTrue(env2["data"]["state"]["in_sync"])
        self.assertError(self.req("PUT", "/api/provenance?dataset=" + quote(ds), doc), 409, "CONFLICT")
        bad = dict(doc, cells=[{"row_uid": "nem uid", "field": "e1", "method": "kitalált"}])
        self.assertError(self.req("PUT", "/api/provenance?dataset=" + quote(ds), bad,
                                  headers=[("If-Match", h["etag"])]), 422, "VALIDATION")

    # ------------------------------------------------------------------ dokumentumok, aláírt URL
    def _register_documents(self):
        docs_dir = Path(self.proj, "03_adatok", "docs")
        docs_dir.mkdir(exist_ok=True)
        (docs_dir / "cikk.pdf").write_bytes(PDF_BYTES)
        (docs_dir / "rossz.html").write_bytes(b"<script>alert(1)</script>")
        art = Path(self.proj, "05_elemzes", "o1", "run1")
        art.mkdir(parents=True, exist_ok=True)
        (art / "forest.svg").write_bytes(SVG_BYTES)
        (art / "evil.html").write_bytes(b"<html></html>")
        doc = {"schema": "szk.ma.documents/v1", "docs": [
            {"id": "pmid:123", "root": "project", "path": "03_adatok/docs/cikk.pdf", "sha256": None, "pages": 1},
            {"id": "file:rossz", "root": "project", "path": "03_adatok/docs/rossz.html"},
            {"id": "doi:10.1/nincs", "root": "project", "path": "03_adatok/docs/nincs.pdf"}]}
        cur = self.req("GET", "/api/documents")
        etag = cur[1].get("etag")
        hdr = [("If-Match", etag)] if etag else []
        self.assertOk(self.req("PUT", "/api/documents", doc, headers=hdr))

    def test_fileurl_serves_pdf_and_sandboxed_svg(self):
        self._register_documents()
        env = self.assertOk(self.req("POST", "/api/fileurl", {"doc": "pmid:123"}))
        url = env["data"]["url"]
        self.assertTrue(url.startswith("/f/pmid%3A123/"))
        st, h, data, _ = self.req("GET", url, token=False, headers=[("Sec-Fetch-Site", "none")])
        self.assertEqual(st, 200)
        self.assertEqual(data, PDF_BYTES)
        self.assertEqual(h["content-type"], "application/pdf")
        self._check_security_headers(h, "pdf")
        env = self.assertOk(self.req("POST", "/api/fileurl", {"path": "05_elemzes/o1/run1/forest.svg"}))
        st, h, data, _ = self.req("GET", env["data"]["url"], token=False)
        self.assertEqual(st, 200)
        self.assertEqual(data, SVG_BYTES)
        self.assertEqual(h["content-type"], "image/svg+xml")
        csp = self._check_security_headers(h, "svg")
        self.assertTrue(csp.startswith("sandbox"))

    def test_fileurl_forged_expired_and_disallowed(self):
        self._register_documents()
        url = self.assertOk(self.req("POST", "/api/fileurl", {"doc": "pmid:123"}))["data"]["url"]
        prefix, sig = url.rsplit("/", 1)
        forged = prefix + "/" + ("B" if sig[0] != "B" else "C") + sig[1:]
        self.assertError(self.req("GET", forged, token=False), 403, "FORBIDDEN")
        parts = url.split("/")
        parts[3] = str(int(parts[3]) + 60)
        self.assertError(self.req("GET", "/".join(parts), token=False), 403, "FORBIDDEN")
        other = url.replace("pmid%3A123", "file%3Arossz")
        self.assertError(self.req("GET", other, token=False), 403, "FORBIDDEN")
        self.clock.offset += security.FILE_URL_TTL + 5
        try:
            self.assertError(self.req("GET", url, token=False), 403, "FORBIDDEN")
        finally:
            self.clock.offset = 0.0
        self.assertError(self.req("GET", url, token=False, headers=[("Sec-Fetch-Site", "cross-site")]),
                         403, "FORBIDDEN")
        self.assertError(self.req("POST", "/api/fileurl", {"doc": "pmid:999"}), 404, "NOT_FOUND")
        self.assertError(self.req("POST", "/api/fileurl", {"doc": "doi:10.1/nincs"}), 404, "NOT_FOUND")
        self.assertError(self.req("POST", "/api/fileurl", {"doc": "file:rossz"}), 403, "FORBIDDEN")
        for p in ("05_elemzes/o1/run1/evil.html", DATASET, "05_elemzes/../projekt.sqlite", "../x.svg"):
            self.assertError(self.req("POST", "/api/fileurl", {"path": p}), 403, "FORBIDDEN")
        self.assertError(self.req("POST", "/api/fileurl", {"doc": "pmid:123", "path": "x"}), 400, "BAD_REQUEST")
        self.assertError(self.req("POST", "/api/fileurl", {"doc": "_run:05_elemzes/o1/run1/forest.svg"}),
                         400, "BAD_REQUEST")

    def test_documents_reject_traversal(self):
        doc = {"schema": "szk.ma.documents/v1",
               "docs": [{"id": "file:x", "root": "project", "path": "../outside.pdf"}]}
        cur = self.req("GET", "/api/documents")
        etag = cur[1].get("etag")
        hdr = [("If-Match", etag)] if etag else []
        self.assertError(self.req("PUT", "/api/documents", doc, headers=hdr), 422, "VALIDATION")

    # ------------------------------------------------------------------ korlátok
    def test_body_size_limits_413(self):
        big = json.dumps({"severity": "info", "title": "x" * (security.MAX_BODY_BYTES + 100)}).encode()
        self.assertError(self.req("POST", "/api/log/finding", raw=big), 413, "PAYLOAD_TOO_LARGE")
        huge = b'{"dataset": "03_adatok/x.csv", "header": [], "rows": [], "pad": "' + \
            b"y" * security.MAX_TABLE_BODY_BYTES + b'"}'
        self.assertError(self.req("PUT", "/api/table", raw=huge), 413, "PAYLOAD_TOO_LARGE")
        header = ["c%d" % i for i in range(security.MAX_COLUMNS + 1)]
        res = self.req("PUT", "/api/table", {"dataset": "03_adatok/t_wide.csv", "header": header, "rows": []})
        self.assertError(res, 413, "PAYLOAD_TOO_LARGE")

    def test_json_depth_and_shape_400(self):
        deep = b"[" * (security.MAX_JSON_DEPTH + 8) + b"]" * (security.MAX_JSON_DEPTH + 8)
        for raw in (deep, b'{"__proto__": {"x": 1}}', b'{"severity": "info", "title": NaN}', b"{nem json",
                    b'{"severity": "info", "severity": "major", "title": "x"}', b"\xff\xfe"):
            self.assertError(self.req("POST", "/api/log/finding", raw=raw), 400, "BAD_REQUEST")
        env = self.assertError(self.req("POST", "/api/log/finding", {"severity": "nagyon", "title": 5}),
                               400, "BAD_REQUEST")
        self.assertTrue(env["error"]["details"]["errors"])
        bad_fmt = {"dataset": "03_adatok/t_fmt.csv", "header": ["a"], "rows": [], "format": {"newline": []}}
        self.assertError(self.req("PUT", "/api/table", bad_fmt), 400, "BAD_REQUEST")
        bad_fmt["format"] = {"encoding": "ebcdic"}
        self.assertError(self.req("PUT", "/api/table", bad_fmt), 422, "VALIDATION")

    # ------------------------------------------------------------------ hibák
    def test_error_envelopes_and_engine_valueerror(self):
        self.assertError(self.req("GET", "/api/nincs/ilyen"), 404, "NOT_FOUND")
        env = self.assertError(self.req("POST", "/api/log/finding",
                                        {"severity": "minor", "title": "rossz szakasz", "stage": "S99"}),
                               422, "VALIDATION")
        self.assertIn("szakasz", env["error"]["message"])
        self.assertError(self.req("GET", "/api/log/nincsilyen"), 404, "NOT_FOUND")
        self.assertError(self.req("GET", "/api/log/finding/abc"), 400, "BAD_REQUEST")
        self.assertError(self.req("GET", "/api/log/finding/987654"), 404, "NOT_FOUND")

    def test_internal_error_is_opaque(self):
        def boom(_req):
            raise RuntimeError("Aronson 1948;4;123 — titkos cellaérték")
        route = self.app.router.add("GET", "/api/test/boom", boom)
        try:
            env = self.assertError(self.req("GET", "/api/test/boom?q=Aronson"), 500, "INTERNAL")
        finally:
            self.app.router.routes.remove(route)
        self.assertNotIn("Aronson", json.dumps(env, ensure_ascii=False))
        self.assertNotIn("Traceback", json.dumps(env))
        self.assertNotIn("Aronson", self.log.getvalue())
        self.assertIn("GET /api/test/boom 500 INTERNAL", self.log.getvalue())

    # ------------------------------------------------------------------ napló és kapu
    def test_gate_blocked_409_with_blockers(self):
        f = self.assertOk(self.req("POST", "/api/log/finding",
                                   {"severity": "blocker", "title": "SE/SD csere gyanú", "stage": "S09",
                                    "agent": "reviewer"}))
        fid = f["data"]["id"]
        env = self.assertError(self.req("POST", "/api/log/checkpoint", {"stage": "S09", "verdict": "PASS"}),
                               409, "GATE_BLOCKED")
        blockers = env["error"]["details"]["blockers"]
        self.assertIn(fid, [b["id"] for b in blockers])
        self.assertIn("blocker", env["error"]["message"])
        self.assertOk(self.req("POST", "/api/log/checkpoint", {"stage": "S09", "verdict": "FAIL"}))
        self.assertOk(self.req("POST", "/api/log/resolve", {"id": fid, "status": "fixed", "resolution": "javítva"}))
        env = self.assertOk(self.req("POST", "/api/log/checkpoint", {"stage": "S09", "verdict": "PASS"}))
        self.assertEqual(env["data"]["kind"], "checkpoint")
        items = self.assertOk(self.req("GET", "/api/log/checkpoint?stage=S09"))["data"]["items"]
        self.assertEqual([i["verdict"] for i in items], ["FAIL", "PASS"])

    def test_log_lists_items_and_status(self):
        env = self.assertOk(self.req("POST", "/api/log/decision",
                                     {"agent": "user", "decision": "REML τ²", "rationale": "Cochrane 10.10.4",
                                      "kb_refs": ["NEMLETEZO-AZON-1"], "stage": "S10",
                                      "context": {"kind": "validation"}}))
        did = env["data"]["id"]
        self.assertTrue(env["warnings"])           # ismeretlen KB-azonosító → figyelmeztetés
        item = self.assertOk(self.req("GET", "/api/log/decision/%d" % did))["data"]["item"]
        self.assertEqual(item["decision"], "REML τ²")
        lst = self.assertOk(self.req("GET", "/api/log/decisions"))["data"]
        self.assertEqual(lst["kind"], "decision")
        self.assertIn(did, [i["id"] for i in lst["items"]])
        self.assertOk(self.req("GET", "/api/log/status"))
        self.assertError(self.req("POST", "/api/log/decision", {"agent": "hacker", "decision": "x"}),
                         400, "BAD_REQUEST")

    def test_activity_chain_valid_and_value_free(self):
        ds = self.copy_dataset("t_activity.csv")
        t = self.assertOk(self.req("GET", "/api/table?dataset=" + quote(ds)))["data"]
        rows = [{"row_uid": r["row_uid"], "cells": list(r["cells"])} for r in t["rows"]]
        rows[2]["cells"][0] = "ZZ-titkos-123"
        self.assertOk(self.req("PUT", "/api/table", {"dataset": ds, "header": t["header"], "rows": rows},
                               headers=[("If-Match", t["etag"])]))
        self.assertOk(self.req("POST", "/api/log/finding", {"severity": "info", "title": "lánc-teszt"}))
        log_path = Path(self.proj, activity.LOG_RELPATH)
        text = log_path.read_text(encoding="utf-8")
        ok, bad, msg = activity.verify_chain(log_path)
        self.assertTrue(ok, msg)
        self.assertNotIn("ZZ-titkos-123", text)
        self.assertNotIn("Aronson", text)
        self.assertNotIn("lánc-teszt", text)
        recs = activity.read_records(log_path)
        saves = [r for r in recs if r["action"] == "table.save" and r["outputs"][0]["path"] == ds]
        self.assertEqual(saves[-1]["outputs"][0]["sha256"], store.sha256_file(Path(self.proj, ds)))
        self.assertEqual(saves[-1]["actor"], "user")
        env = self.assertOk(self.req("GET", "/api/log/activity?limit=5"))
        self.assertTrue(env["data"]["verify"]["ok"])
        self.assertLessEqual(len(env["data"]["items"]), 5)
        self.assertNotIn("ZZ-titkos-123", self.log.getvalue())
        self.assertNotIn(self.token, self.log.getvalue())

    # ------------------------------------------------------------------ long-poll
    def test_long_poll_returns_on_external_change(self):
        rev = self.assertOk(self.req("GET", "/api/changes"))["data"]["rev"]
        target = Path(self.proj, "03_adatok", "t_watch.csv")

        def edit():
            time.sleep(0.4)
            target.write_bytes(Path(BCG).read_bytes())
        th = threading.Thread(target=edit)
        th.start()
        changed, external, since = set(), set(), rev
        deadline = time.monotonic() + 15
        while "03_adatok/t_watch.csv" not in changed and time.monotonic() < deadline:
            started = time.monotonic()
            data = self.assertOk(self.req("GET", "/api/changes?since=%d&wait=10" % since))["data"]
            self.assertLess(time.monotonic() - started, 9.0, "a long-poll nem tért vissza a változásra")
            self.assertFalse(data["reset"])
            self.assertGreater(data["rev"], since)
            changed.update(data["changed"])
            external.update(data["external"])
            since = data["rev"]
        th.join()
        self.assertIn("03_adatok/t_watch.csv", changed)
        self.assertIn("03_adatok/t_watch.csv", external)
        self.assertNotIn(activity.LOG_RELPATH, external)
        log_path = Path(self.proj, activity.LOG_RELPATH)
        found = self.wait_for(lambda: [r for r in activity.read_records(log_path)
                                       if r["action"] == "file.external_edit"
                                       and r["outputs"][0]["path"] == "03_adatok/t_watch.csv"])
        self.assertTrue(found)
        self.assertEqual(found[-1]["actor"], "external")
        started = time.monotonic()
        self.assertOk(self.req("GET", "/api/changes?since=%d&wait=0.3" % self.app.watcher.rev))
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertTrue(self.assertOk(self.req("GET", "/api/changes?since=xyz"))["data"]["reset"])

    # ------------------------------------------------------------------ egyéb végpontok
    def test_binds_loopback_ipv4_only(self):
        self.assertEqual(self.app.httpd.server_address[0], "127.0.0.1")
        self.assertEqual(self.app.httpd.socket.family, socket.AF_INET)

    def test_engine_route(self):
        env = self.assertOk(self.req("GET", "/api/engine"))
        d = env["data"]
        self.assertIs(d["facade"], False)
        rr = [m for m in d["measures"] if m["id"] == "RR"]
        self.assertTrue(rr and rr[0]["ratio"] and rr[0]["required_columns"] == ["e1", "n1", "e2", "n2"])
        st = self.wait_for(lambda: self.app.selftest_info()["state"] not in ("running", "pending"), timeout=60)
        self.assertTrue(st)
        info = self.assertOk(self.req("GET", "/api/engine"))["data"]["selftest"]
        self.assertIn(info["state"], ("pass", "fail", "error", "unknown"))
        if info["state"] == "pass":
            self.assertGreater(info["checks"], 1000)
        self.assertEqual(env["meta"]["engine"], d["engine_version"])

    def test_capabilities_route(self):
        env = self.assertOk(self.req("GET", "/api/capabilities"))
        comps = env["data"]["components"]
        self.assertEqual(comps[0]["id"], "metaelemzes")
        self.assertTrue(all(c["state"] in ("ok", "absent", "legacy", "unusable") for c in comps))
        self.assertOk(self.req("POST", "/api/capabilities/refresh", {"plugins": ["composer"]}))
        self.assertError(self.req("POST", "/api/capabilities/refresh", {"plugins": ["nincs"]}), 400, "BAD_REQUEST")

    def test_privacy_routes_need_explicit_confirm(self):
        env = self.assertOk(self.req("GET", "/api/privacy"))
        self.assertEqual(env["data"]["data_class"], "A")
        self.assertFalse(env["data"]["preview"])
        prev = self.assertOk(self.req("GET", "/api/privacy?data_class=C"))["data"]
        self.assertEqual((prev["data_class"], prev["preview"], prev["project_data_class"]), ("C", True, "A"))
        self.assertError(self.req("GET", "/api/privacy?data_class=X"), 422, "VALIDATION")
        gi = Path(self.proj, ".gitignore")
        plan = self.assertOk(self.req("POST", "/api/privacy/apply", {"action": "gitignore", "dry_run": True}))["data"]
        self.assertIn("_privat/", plan["diff"])
        self.assertFalse(gi.exists())
        self.assertError(self.req("POST", "/api/privacy/apply", {"action": "gitignore"}), 400, "BAD_REQUEST")
        self.assertError(self.req("POST", "/api/privacy/apply", {"action": "gitignore", "confirm": False}),
                         400, "BAD_REQUEST")
        self.assertError(self.req("POST", "/api/privacy/apply", {"action": "rm -rf", "confirm": True}),
                         400, "BAD_REQUEST")
        res = self.assertOk(self.req("POST", "/api/privacy/apply",
                                     {"action": "gitignore", "confirm": True, "base_sha256": plan["base_sha256"]}))
        self.assertTrue(res["data"]["applied"])
        self.assertIn("_privat/", gi.read_text(encoding="utf-8"))
        self.assertTrue(res["data"]["status"]["gitignore_block_present"])
        self.assertError(self.req("POST", "/api/privacy/apply",
                                  {"action": "gitignore", "confirm": True, "base_sha256": None}), 409, "CONFLICT")
        recs = activity.read_records(Path(self.proj, activity.LOG_RELPATH))
        self.assertTrue([r for r in recs if r["action"] == "privacy.apply"])

    def test_project_routes(self):
        env = self.assertOk(self.req("GET", "/api/project"))
        d = env["data"]
        self.assertTrue(d["initialized"])
        self.assertEqual(d["title"], "Teszt-projekt")
        self.assertEqual(d["path"], self.proj)
        self.assertIn(DATASET, [t["dataset"] for t in d["tables"]])
        self.assertOk(self.req("POST", "/api/project", {"action": "open", "path": self.proj}))
        self.assertError(self.req("POST", "/api/project", {"action": "open", "path": "/mashol/projekt"}),
                         400, "BAD_REQUEST")
        self.assertError(self.req("POST", "/api/project", {"action": "init", "path": "/mashol", "title": "x"}),
                         400, "BAD_REQUEST")
        self.assertError(self.req("POST", "/api/project", {"action": "torol"}), 400, "BAD_REQUEST")
        try:
            # (a megosztott projektben egy korábbi teszt beírhatta a kezelt blokkot: ma-projekt.json nélkül az
            # osztály ilyenkor 'unknown' → C, és minden csökkentés megerősítést kér)
            env = self.assertOk(self.req("POST", "/api/project", {"action": "data_class", "data_class": "b",
                                                                  "confirm": True}))
            self.assertEqual((env["data"]["data_class"], env["data"]["data_class_source"]), ("B", "ma-projekt.json"))
            meta = json.loads(Path(self.proj, "ma-projekt.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["data_class"], "B")
            self.assertError(self.req("POST", "/api/project", {"action": "data_class", "data_class": "D"}),
                             422, "VALIDATION")
            err = self.assertError(self.req("POST", "/api/project", {"action": "data_class", "data_class": "A"}),
                                   400, "BAD_REQUEST")
            self.assertTrue(err["error"]["details"]["needs_confirm"])
        finally:
            self.assertOk(self.req("POST", "/api/project", {"action": "data_class", "data_class": "A",
                                                            "confirm": True}))

    def test_kb_routes(self):
        env = self.assertOk(self.req("GET", "/api/kb/search?q=heterogeneity&limit=3"))
        res = env["data"]["results"]
        self.assertTrue(res.get("rule") or res.get("knowledge"))
        some = (res.get("rule") or res.get("knowledge"))[0]["id"]
        item = self.assertOk(self.req("GET", "/api/kb/item/" + quote(some, safe="")))
        self.assertEqual(item["data"]["id"], some)
        self.assertError(self.req("GET", "/api/kb/item/NINCS-ILYEN-999"), 404, "NOT_FOUND")
        self.assertError(self.req("GET", "/api/kb/search"), 400, "BAD_REQUEST")
        self.assertError(self.req("GET", "/api/kb/search?q=x&scope=sql"), 400, "BAD_REQUEST")
        self.assertNotIn("heterogeneity", self.log.getvalue())

    def test_c_class_project_under_vault_is_locked(self):
        home = os.path.join(self.tmp, "home_c")
        vault_root = os.path.join(home, "Documents", "claude")
        proj = os.path.join(vault_root, "projC")
        os.makedirs(os.path.join(proj, "03_adatok"))
        os.makedirs(os.path.join(home, ".claude", "vault"))
        with open(os.path.join(home, ".claude", "vault", "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"root": vault_root, "max_depth": 2}, fh)
        with open(os.path.join(proj, "ma-projekt.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": "szk.ma.project/v1", "title": "C", "data_class": "C"}, fh)
        shutil.copy(BCG, os.path.join(proj, "03_adatok"))
        app, th = _start_app(proj, selftest=False, kb_build=False, caps=_fake_caps(self.tmp, home),
                             caps_refresh=False, privacy_home=home, privacy_env={}, log_stream=None,
                             validate_responses=True, idle_hours=0)
        try:
            tok = None
            code = app.new_launch_code()
            st, _h, data = _http(app.port, "POST", "/api/session", json.dumps({"launch_code": code}).encode(),
                                 [("Host", "127.0.0.1:%d" % app.port), ("Content-Type", "application/json")])
            tok = json.loads(data)["data"]["token"]
            hdr = [("Host", "127.0.0.1:%d" % app.port), ("X-MA-Token", tok)]
            st, _h, data = _http(app.port, "GET", "/api/table?dataset=" + quote(DATASET), None, hdr)
            env = json.loads(data)
            self.assertEqual((st, env["error"]["code"]), (403, "FORBIDDEN"))
            self.assertTrue(env["error"]["details"]["reasons"])
            st, _h, data = _http(app.port, "GET", "/api/privacy", None, hdr)
            self.assertEqual(st, 200)
            self.assertTrue(json.loads(data)["data"]["open_blocked"]["blocked"])
        finally:
            _stop_app(app, th)

    # ------------------------------------------------------------------ hibás HTTP
    def _raw(self, payload):
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(payload)
            chunks = []
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)

    def test_malformed_requests_get_envelopes(self):
        data = self._raw(b"GET / HTTP/9.9\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port)
        self.assertTrue(data.startswith(b"HTTP/1.0 400"), data[:80])
        self.assertIn(b"X-Frame-Options: DENY", data)
        self.assertIn(b"Content-Security-Policy:", data)
        self.assertIn(b'"BAD_REQUEST"', data)
        garbage = self._raw(b"GARBAGE\r\n\r\n")
        self.assertTrue(garbage.startswith(b"HTTP/1.0 400"), garbage[:80])
        self.assertIn(b"X-Content-Type-Options: nosniff", garbage)
        self.assertIn(b'"BAD_REQUEST"', garbage)
        self.assertOk(self.req("GET", "/api/engine"))                              # a szerver él


class FreshProjectTests(unittest.TestCase):
    """Üres mappa → POST /api/project init; tétlenségi leállás."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_gui_srv2_"))
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _app(self, proj, **kw):
        opts = dict(selftest=False, kb_build=False, caps=_fake_caps(self.tmp, self.home), caps_refresh=False,
                    privacy_home=self.home, privacy_env={}, log_stream=None, validate_responses=True,
                    kb_db=os.path.join(self.tmp, "kb.sqlite"), idle_hours=0, watch_interval=0.1)
        opts.update(kw)
        return _start_app(proj, **opts)

    def _session(self, app):
        code = app.new_launch_code()
        _st, _h, data = _http(app.port, "POST", "/api/session", json.dumps({"launch_code": code}).encode(),
                              [("Host", "127.0.0.1:%d" % app.port), ("Content-Type", "application/json")])
        tok = json.loads(data)["data"]["token"]

        def call(method, path, body=None):
            hdr = [("Host", "127.0.0.1:%d" % app.port), ("X-MA-Token", tok)]
            raw = None
            if body is not None:
                raw = json.dumps(body).encode()
                hdr.append(("Content-Type", "application/json"))
            st, _h, d = _http(app.port, method, path, raw, hdr)
            return st, json.loads(d)
        return call

    def test_init_creates_log_and_project_json(self):
        proj = os.path.join(self.tmp, "uj")
        os.makedirs(proj)
        app, th = self._app(proj)
        try:
            call = self._session(app)
            st, env = call("GET", "/api/log/finding")
            self.assertEqual((st, env["error"]["code"]), (404, "NOT_FOUND"))
            st, env = call("GET", "/api/project")
            self.assertEqual(st, 200)
            self.assertFalse(env["data"]["initialized"])
            st, env = call("POST", "/api/project", {"action": "init", "title": "Új áttekintés", "path": proj,
                                                    "data_class": "B", "question": {"P": "felnőttek", "I": "x"}})
            self.assertEqual(st, 200, env)
            self.assertTrue(env["data"]["initialized"])
            self.assertEqual(env["data"]["data_class"], "B")
            self.assertTrue(os.path.isfile(os.path.join(proj, "projekt.sqlite")))
            meta = json.loads(Path(proj, "ma-projekt.json").read_text(encoding="utf-8"))
            self.assertEqual((meta["title"], meta["conventions"]["grade_suspected"]), ("Új áttekintés", "unresolved"))
            st, env = call("GET", "/api/log/finding")
            self.assertEqual(st, 200)
            st, env = call("POST", "/api/project", {"action": "init", "title": "Másik cím"})
            self.assertEqual(st, 200)
            self.assertTrue(env["warnings"])
            time.sleep(0.3)                     # a figyelő egy köre: a sablonmásolat nem „external”
            recs = activity.read_records(Path(proj, activity.LOG_RELPATH))
            self.assertEqual([r["action"] for r in recs][:1], ["project.init"])
            self.assertFalse([r for r in recs if r["action"] == "file.external_edit"])
            self.assertTrue(activity.verify_chain(Path(proj, activity.LOG_RELPATH))[0])
        finally:
            _stop_app(app, th)

    def test_idle_shutdown(self):
        proj = os.path.join(self.tmp, "idle")
        os.makedirs(proj)
        app, th = self._app(proj, idle_hours=0.5 / 3600.0)
        th.join(10)
        self.assertFalse(th.is_alive(), "a tétlen szerver nem állt le")
        with self.assertRaises(OSError):
            _http(app.port, "GET", "/", None, [("Host", "127.0.0.1:%d" % app.port)], timeout=2)

    def test_port_selection_prefers_8790_range(self):
        self.assertEqual(runtime.port_candidates(), list(range(8790, 8800)) + [0])
        self.assertEqual(runtime.port_candidates(0), [0])
        with self.assertRaises(ValueError):
            runtime.port_candidates(70000)
        busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            port = busy.getsockname()[1]
            proj = os.path.join(self.tmp, "port")
            os.makedirs(proj)
            app = server.App(proj, selftest=False, kb_build=False, caps_refresh=False, log_stream=None,
                             privacy_home=self.home, privacy_env={})
            with self.assertRaises(OSError):
                app.start(port=port)
            app.close()
        finally:
            busy.close()

    def test_instance_lock_is_exclusive(self):
        lock_path = os.path.join(self.tmp, "rt", runtime.LOCK_NAME)
        a, b = runtime.InstanceLock(lock_path), runtime.InstanceLock(lock_path)
        self.assertTrue(a.acquire())
        try:
            self.assertFalse(b.acquire())
        finally:
            a.release()
        self.assertTrue(b.acquire())
        b.release()

    def test_runtime_dir_is_outside_project_and_per_project(self):
        p1, p2 = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        os.makedirs(p1)
        os.makedirs(p2)
        base = os.path.join(self.tmp, "base")
        d1, d2 = runtime.project_runtime_dir(p1, base=base), runtime.project_runtime_dir(p2, base=base)
        self.assertNotEqual(d1, d2)
        self.assertEqual(d1, runtime.project_runtime_dir(p1 + os.sep, base=base))
        self.assertFalse(str(d1).startswith(p1))
        with mock.patch.dict(os.environ, {"MA_GUI_RUNTIME_DIR": base}):
            self.assertEqual(runtime.runtime_base(), Path(base))


class ModuleHygieneTests(unittest.TestCase):
    """AST: nincs math/cmath/statistics/random/decimal, csak stdlib + metaelemzes + ma_gui, nincs
    print, nincs shell=True a szerver saját moduljaiban."""

    FORBIDDEN = {"math", "cmath", "statistics", "random", "decimal"}
    ALLOWED = {"argparse", "contextlib", "csv", "datetime", "hashlib", "hmac", "http", "io", "json", "os", "platform", "re",
               "secrets", "signal", "socket", "socketserver", "sqlite3", "sys", "threading", "time", "urllib",
               "webbrowser", "pathlib", "fcntl", "msvcrt", "metaelemzes", "ma_gui"}

    def _files(self):
        base = Path(ROOT) / "ma_gui"
        files = [base / n for n in ("server.py", "router.py", "runtime.py", "__main__.py")]
        files += sorted((base / "routes").glob("*.py"))
        return files

    def test_imports_and_calls(self):
        files = self._files()
        self.assertGreaterEqual(len(files), 15)
        for path in files:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    mods = [node.module or ""]
                for m in mods:
                    top = m.split(".")[0]
                    self.assertNotIn(top, self.FORBIDDEN, (path.name, m))
                    self.assertIn(top, self.ALLOWED, (path.name, m))
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        self.assertNotEqual(node.func.id, "print", path.name)
                    for kw in node.keywords:
                        if kw.arg == "shell":
                            self.assertFalse(isinstance(kw.value, ast.Constant) and kw.value.value is True, path.name)
            self.assertTrue(ast.get_docstring(tree), path.name)


if __name__ == "__main__":
    unittest.main()
