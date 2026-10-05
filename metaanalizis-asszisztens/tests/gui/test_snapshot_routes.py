# -*- coding: utf-8 -*-
"""Az export-végpontok HTTP-szinten (terv 3.4, 3.5.17, 7.1 T5, 7.6, 8.4, 8.6):

- ``POST /api/export/audit`` → a ZIP a projektben (07_ellenorzes/audit/<nap>/), aláírt letöltési cím; kétszer
  kérve ugyanaz a sha256 (az export nem ír a tevékenységnaplóba, így nem változtatja a projektállapotot);
- ``POST /api/export/snapshot`` → tudomásulvétel (``ack``) nélkül 400; vele a HTML a projektben, aláírt címmel;
- ``GET /f/x/…``: token nélkül, csak érvényes, le nem járt aláírással; mindig csatolmány, ``nosniff``, sandbox
  CSP; hamisított / lejárt / másik fájlra átírt aláírás → 403; a /f/ dokumentum-útvonallal nem keverhető;
- C osztály: adattábla kérésre is 403; az export a _privat/export/ alá kerül (írás-tartás).
"""
import hashlib
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from ma_gui import schema_lite, security, server, snapshot  # noqa: E402
from ma_gui.routes import export as export_routes  # noqa: E402
from test_snapshot import CELL_MARKER, PRIVATE_MARKER, embedded, make_project  # noqa: E402


class _Clock(object):
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.time() + self.offset


def _http(port, method, path, body=None, headers=(), timeout=60):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in headers:
            conn.putheader(k, v)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


class _ServerCase(unittest.TestCase):
    CLASS = "A"

    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_snap_http_"))
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        cls.root = make_project(cls.tmp, cls.CLASS)
        cls.clock = _Clock()
        cls.app = server.App(cls.root, selftest=False, kb_build=False, caps_refresh=False, log_stream=None,
                             idle_hours=0, privacy_home=cls.home, privacy_env={}, validate_responses=True,
                             clock=cls.clock, watch_interval=0.2)
        if cls.app.router.find("POST", "/api/export/audit") is None:     # a regisztrációt a routes/__init__ végzi
            export_routes.register(cls.app.router)
        cls.app.start(port=0)
        cls.thread = threading.Thread(target=cls.app.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.app.port
        st, _h, data = _http(cls.port, "POST", "/api/session",
                             json.dumps({"launch_code": cls.app.new_launch_code()}).encode(),
                             [("Host", "127.0.0.1:%d" % cls.port), ("Content-Type", "application/json")])
        assert st == 200, data
        cls.token = json.loads(data)["data"]["token"]

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown()
        cls.thread.join(10)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def post(self, path, body):
        st, h, data = _http(self.port, "POST", path, json.dumps(body).encode("utf-8"),
                            [("Host", "127.0.0.1:%d" % self.port), ("Content-Type", "application/json"),
                             ("X-MA-Token", self.token)])
        env = json.loads(data.decode("utf-8"))
        self.assertEqual(schema_lite.validate(env, security.ENVELOPE_SCHEMA), [])
        return st, env

    def get(self, path, headers=()):
        return _http(self.port, "GET", path, None, [("Host", "127.0.0.1:%d" % self.port)] + list(headers))


class ExportRouteTests(_ServerCase):
    def test_audit_export_deterministic_and_downloadable(self):
        n_before = len(self.app.activity.read())
        st, env = self.post("/api/export/audit", {"include": {"data_tables": True}, "redact": {"quotes": True}})
        self.assertEqual(st, 200, env)
        d = env["data"]
        self.assertEqual(env["schema"], "szk.ma.export-result/v1")
        self.assertEqual(d["kind"], "audit")
        self.assertTrue(d["deterministic"])
        self.assertTrue(d["path"].startswith("07_ellenorzes/audit/%s/" % d["created"][:10]), d["path"])
        with open(os.path.join(self.root, *d["path"].split("/")), "rb") as fh:
            raw = fh.read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), d["sha256"])
        self.assertEqual(len(raw), d["bytes"])
        self.assertTrue(d["url"].startswith("/f/x/"))
        # második kérés: ugyanaz a bájtsor (az export nem változtatja a projektállapotot)
        st2, env2 = self.post("/api/export/audit", {"include": {"data_tables": True}, "redact": {"quotes": True}})
        self.assertEqual(env2["data"]["sha256"], d["sha256"])
        self.assertEqual(len(self.app.activity.read()), n_before, "az export nem kerül a hash-láncba")
        # letöltés: token nélkül, csatolmányként, a fájl bájtjai
        status, h, body = self.get(d["url"], [("Sec-Fetch-Site", "same-origin")])
        self.assertEqual(status, 200)
        self.assertEqual(body, raw)
        self.assertEqual(h["content-type"], "application/zip")
        self.assertTrue(h["content-disposition"].startswith("attachment;"))
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertIn("sandbox", h["content-security-policy"])
        self.assertEqual(h["cache-control"], "no-store")
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            self.assertIn("manifest.json", zf.namelist())

    def test_snapshot_requires_ack_and_downloads_as_attachment(self):
        st, env = self.post("/api/export/snapshot", {"include": {}, "redact": {}})
        self.assertEqual(st, 400)
        self.assertEqual(env["error"]["code"], "BAD_REQUEST")
        self.assertIn("Artifact", env["error"]["message"])
        st, env = self.post("/api/export/snapshot", {"include": {}, "redact": {}, "ack": True})
        self.assertEqual(st, 200, env)
        d = env["data"]
        self.assertEqual(d["kind"], "snapshot")
        self.assertIs(d["network"], False)
        self.assertTrue(d["path"].startswith("07_ellenorzes/pillanatkep/") and d["path"].endswith(".snapshot.html"))
        status, h, body = self.get(d["url"], [("Sec-Fetch-Site", "same-origin")])
        self.assertEqual(status, 200)
        self.assertEqual(hashlib.sha256(body).hexdigest(), d["sha256"])
        self.assertEqual(h["content-type"], "application/octet-stream", "a HTML nem nyílhat meg a munkapad originjén")
        self.assertTrue(h["content-disposition"].startswith("attachment;"))
        self.assertIn("sandbox", h["content-security-policy"])
        data = embedded(body)
        self.assertEqual(data["manifest"]["data_class"], "A")
        self.assertNotIn(PRIVATE_MARKER, body.decode("utf-8"))
        # ugyanarra az állapotra a második kérés és a parancssori (önálló App) változat is ugyanazt a bájtsort adja
        st2, env2 = self.post("/api/export/snapshot", {"ack": True})
        self.assertEqual(env2["data"]["sha256"], d["sha256"])
        html, info = snapshot.build(project_dir=self.root)
        self.assertEqual(info["sha256"], d["sha256"], "a szerverből és a parancssorból készült pillanatkép azonos")

    def test_download_signature_checks(self):
        st, env = self.post("/api/export/audit", {})
        url = env["data"]["url"]
        parts = url.split("/")
        forged = "/".join(parts[:-1] + [parts[-1][:-2] + ("AA" if not parts[-1].endswith("AA") else "BB")])
        self.assertEqual(self.get(forged)[0], 403)
        # a /f/ (dokumentum-) útvonalon ugyanez az aláírás nem érvényes
        self.assertIn(self.get("/f/" + url[len("/f/x/"):])[0], (403, 404))
        # másik (nem export-) fájlra mutató, saját kulccsal aláírt azonosító
        other = "projekt.sqlite"
        signed = self.app.security.sign_file_url("_export:" + other, other)
        self.assertEqual(self.get("/f/x/" + signed[len("/f/"):])[0], 403)
        # idegen webhelyről indított letöltés
        self.assertEqual(self.get(url, [("Sec-Fetch-Site", "cross-site")])[0], 403)
        # lejárat után
        self.clock.offset = security.FILE_URL_MAX_TTL + 5
        try:
            self.assertEqual(self.get(url)[0], 403)
        finally:
            self.clock.offset = 0.0

    def test_bad_toggles(self):
        st, env = self.post("/api/export/audit", {"include": {"mindent": True}})
        self.assertEqual(st, 400)
        st, env = self.post("/api/export/audit", {"include": {"data_tables": "igen"}})
        self.assertEqual(st, 400)
        st, env = self.post("/api/export/snapshot", {"ack": True, "valami": 1})
        self.assertEqual(st, 400)

    def test_requires_token(self):
        st, h, data = _http(self.port, "POST", "/api/export/audit", b"{}",
                            [("Host", "127.0.0.1:%d" % self.port), ("Content-Type", "application/json")])
        self.assertEqual(st, 403)


class ExportClassCTests(_ServerCase):
    CLASS = "C"

    def test_class_c_tables_forbidden_and_private_location(self):
        st, env = self.post("/api/export/audit", {"include": {"data_tables": True}})
        self.assertEqual(st, 403)
        self.assertEqual(env["error"]["code"], "FORBIDDEN")
        st, env = self.post("/api/export/audit", {})
        self.assertEqual(st, 200, env)
        d = env["data"]
        self.assertTrue(d["path"].startswith("_privat/export/"), d["path"])
        self.assertTrue(env["warnings"])
        status, _h, body = self.get(d["url"])
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            names = zf.namelist()
            text = "".join(zf.read(n).decode("utf-8", "replace") for n in names)
        self.assertFalse([n for n in names if n.startswith("03_adatok/") and n.endswith(".csv")])
        self.assertNotIn(CELL_MARKER, text)
        self.assertNotIn(PRIVATE_MARKER, text)
        st, env = self.post("/api/export/snapshot", {"ack": True, "include": {"data_tables": True}})
        self.assertEqual(st, 403)
        st, env = self.post("/api/export/snapshot", {"ack": True})
        self.assertEqual(st, 200, env)
        self.assertTrue(env["data"]["path"].startswith("_privat/export/"))
        status, _h, body = self.get(env["data"]["url"])
        self.assertNotIn(CELL_MARKER, body.decode("utf-8"))


class ExportHelperTests(unittest.TestCase):
    def test_export_rel_allowlist(self):
        ok = export_routes._export_rel
        self.assertEqual(ok("07_ellenorzes/audit/2026-10-05/x-audit-1a2b3c4d.zip"),
                         "07_ellenorzes/audit/2026-10-05/x-audit-1a2b3c4d.zip")
        self.assertEqual(ok("07_ellenorzes/pillanatkep/x.snapshot.html"), "07_ellenorzes/pillanatkep/x.snapshot.html")
        self.assertEqual(ok("_privat/export/x.zip"), "_privat/export/x.zip")
        for bad in ("projekt.sqlite", "07_ellenorzes/audit/../../projekt.sqlite", "07_ellenorzes/audit/x.html",
                    "07_ellenorzes/pillanatkep/x.html", "03_adatok/o1.csv", "/etc/passwd", "_privat/kohorsz.csv",
                    "07_ellenorzes/audit/CON.zip", None):
            self.assertIsNone(ok(bad), bad)
        self.assertEqual(quote("_export:a/b", safe=""), "_export%3Aa%2Fb")


if __name__ == "__main__":
    unittest.main()
