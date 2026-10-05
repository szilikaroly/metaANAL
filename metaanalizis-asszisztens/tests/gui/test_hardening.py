# -*- coding: utf-8 -*-
"""A 3. ellenőrzési kör megerősített hibáinak regressziós tesztjei (ma_gui), hibaazonosítónként:

- websec SEC-1…SEC-6: oszlopkorlát a drága értelmezés előtt, kérés-határidő és kapcsolatkorlát,
  nem ASCII Content-Length, HTTP/0.9, a nyers metódus a naplóban, Windows-eszköznevek.
- privacy_data P1…P9: az eredet PHI-szkennelése, az oldalfájl feltételes írása, a _privat/ figyelése,
  szabad szöveg a projekt.sqlite-ba, részleges írás naplózása, ideiglenes fájlok, TAJ-számexport,
  ismeretlen adatosztály, C osztályú dokumentum-jegyzék.
- robust_portability SRV-1…SRV-7: egy mappa = egy szerver, újraindítás a zár felszabadulásakor,
  KB-újrapróbálás, CLI-hibák, AlreadyRunning-szivárgás, hiányzó tesztek (portváltás, long-poll,
  server.json-tulajdon, NaN, önteszt).

Minden teszt ideiglenes mappában dolgozik; cellaértéket a naplóban nem keres, csak tilt (T10)."""
import argparse
import functools
import http.client
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
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

from metaelemzes import kb as kb_mod  # noqa: E402
from metaelemzes import projekt  # noqa: E402
from ma_gui import activity, privacy, router, runtime, security, server, store  # noqa: E402
from ma_gui import caps as caps_mod  # noqa: E402

HAS_GIT = shutil.which("git") is not None
PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _taj(first8="12345678"):
    return first8 + str(privacy.taj_check_digit(first8))


def _http(port, method, path, body=None, headers=(), timeout=40):
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
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def _raw(port, payload, timeout=10):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall(payload)
        chunks = []
        while True:
            try:
                chunk = s.recv(65536)
            except (ConnectionResetError, socket.timeout):
                break
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def _make_project(base, name="proj", data_class="A", vault=False, git=False, block=False, guard=False,
                  init=True):
    """(projektmappa, home). vault: a projekt a vault gyökere alatt (~/Documents/claude)."""
    home = os.path.join(base, "home_" + name)
    os.makedirs(home)
    if vault:
        vroot = os.path.join(home, "Documents", "claude")
        os.makedirs(vroot)
        os.makedirs(os.path.join(home, ".claude", "vault"))
        with open(os.path.join(home, ".claude", "vault", "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"root": vroot, "max_depth": 2}, fh)
        proj = os.path.join(vroot, name)
    else:
        proj = os.path.join(base, name)
    os.makedirs(proj)
    if init:
        projekt.init(proj, "Teszt")
    if data_class:
        with open(os.path.join(proj, "ma-projekt.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": "szk.ma.project/v1", "title": "T", "data_class": data_class}, fh)
    if git:
        subprocess.run(["git", "init", "-q", proj], check=True)
    if block:
        privacy.apply_gitignore(proj)
    if guard:
        privacy.install_precommit_guard(proj)
    return proj, home


class Srv(object):
    """Élő munkapad-szerver egy projekthez, token-csere után."""

    def __init__(self, proj, home, tmp, name="s", **kw):
        self.log = io.StringIO()
        opts = dict(kb_db=os.path.join(tmp, "kb_%s.sqlite" % name),
                    caps=caps_mod.Caps(runtime_dir=os.path.join(tmp, "caps_" + name), env={}, home=home),
                    privacy_home=home, privacy_env={}, selftest=False, kb_build=False, caps_refresh=False,
                    log_stream=self.log, idle_hours=0, watch_interval=0.2, validate_responses=True)
        opts.update(kw)
        self.proj = proj
        self.app = server.App(proj, **opts)
        self.app.start(port=0)
        self.thread = threading.Thread(target=self.app.serve_forever, name="test-serve", daemon=True)
        self.thread.start()
        self.port = self.app.port
        st, _h, env = self.call("POST", "/api/session", {"launch_code": self.app.new_launch_code()}, token=False)
        assert st == 200, env
        self.token = env["data"]["token"]

    def call(self, method, path, body=None, headers=(), token=True, raw=None, timeout=40):
        hdrs = [("Host", "127.0.0.1:%d" % self.port)]
        if token:
            hdrs.append(("X-MA-Token", self.token if token is True else token))
        payload = raw
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if payload is not None:
            hdrs.append(("Content-Type", "application/json"))
        hdrs.extend(headers)
        st, h, data = _http(self.port, method, path, payload, hdrs, timeout=timeout)
        try:
            env = json.loads(data.decode("utf-8")) if data else None
        except ValueError:
            env = None
        return st, h, env

    def stop(self):
        self.app.shutdown()
        self.thread.join(10)


def _wait_for(pred, timeout=8.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        val = pred()
        if val:
            return val
        time.sleep(step)
    return pred()


def _tmpdir(prefix):
    return os.path.realpath(tempfile.mkdtemp(prefix=prefix))


# ======================================================================================== közös A-szerver
class HttpHardeningTests(unittest.TestCase):
    """Egy A osztályú projekt élő szerverrel (SEC-1, SEC-3…SEC-6, P1, P2, P3 részben, P5)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = _tmpdir("ma_gui_hard_")
        cls.proj, cls.home = _make_project(cls.tmp, "proj", data_class="A")
        cls.srv = Srv(cls.proj, cls.home, cls.tmp, name="a")
        cls.port = cls.srv.port

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, *a, **kw):
        return self.srv.call(*a, **kw)

    def ok(self, res):
        st, _h, env = res
        self.assertEqual(st, 200, env)
        self.assertIs(env["ok"], True)
        return env

    def err(self, res, status, code):
        st, _h, env = res
        self.assertEqual(st, status, env)
        self.assertEqual(env["error"]["code"], code, env)
        return env

    def table(self, ds, header, rows, **extra):
        body = {"dataset": ds, "header": header, "rows": rows}
        hdr = extra.pop("headers", ())
        body.update(extra)
        return self.call("PUT", "/api/table", body, headers=hdr)

    # ---------------------------------------------------------------- SEC-1
    def test_sec1_import_wide_duplicate_header_is_rejected_fast(self):
        t0 = time.monotonic()
        self.err(self.call("POST", "/api/table/import", {"text": "NA," * 12000}), 413, "PAYLOAD_TOO_LARGE")
        self.assertLess(time.monotonic() - t0, 3.0, "az oszlopkorlát nem a drága értelmezés előtt fut")
        t0 = time.monotonic()
        self.err(self.call("POST", "/api/table/import", {"text": "NA\t" * 12000 + "\n1\t2\n", "delimiter": "tab"}),
                 413, "PAYLOAD_TOO_LARGE")
        self.assertLess(time.monotonic() - t0, 3.0)

    def test_sec1_import_text_has_own_cap(self):
        from ma_gui.routes import table as table_routes
        big = "a;b\n" + "1;2\n" * (table_routes.MAX_IMPORT_CHARS // 4 + 10)
        self.assertGreater(len(big), table_routes.MAX_IMPORT_CHARS)
        self.err(self.call("POST", "/api/table/import", {"text": big}), 413, "PAYLOAD_TOO_LARGE")
        env = self.ok(self.call("POST", "/api/table/import", {"text": "study;n1\nA;10\n"}))
        self.assertEqual(env["data"]["header"], ["study", "n1"])

    def test_sec1_wide_csv_on_disk_rejected_before_parse(self):
        path = Path(self.proj, "03_adatok", "szeles.csv")
        path.write_bytes(("NA," * 12000 + "\n" + "1," * 12000 + "\n").encode("utf-8"))
        t0 = time.monotonic()
        self.err(self.call("GET", "/api/table?dataset=" + quote("03_adatok/szeles.csv")), 413, "PAYLOAD_TOO_LARGE")
        self.assertLess(time.monotonic() - t0, 3.0)
        with self.assertRaises(store.TooLarge):
            store.parse_csv_bytes(b"NA," * 5000)
        self.assertEqual(len(store.parse_csv_bytes(b"NA," * 5000, max_columns=None).header.cells), 5001)

    # ---------------------------------------------------------------- SEC-3
    def test_sec3_non_ascii_content_length_gets_enveloped_reply(self):
        for clen in (b"\xb2", b"\xb2\xb3", b"\xb9\xb2"):
            data = _raw(self.port, b"POST /api/engine HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\n"
                                   b"Content-Length: %s\r\n\r\n{}" % (self.port, clen))
            self.assertTrue(data.startswith(b"HTTP/1.0 403"), data[:80])
            self.assertIn(b"Content-Security-Policy:", data)
            self.assertIn(b'"FORBIDDEN"', data)
        data = _raw(self.port, b"POST /api/log/decision HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-MA-Token: %s\r\n"
                               b"Content-Type: application/json\r\nContent-Length: \xb2\r\n\r\n{}"
                    % (self.port, self.srv.token.encode()))
        self.assertTrue(data.startswith(b"HTTP/1.0 400"), data[:80])
        self.assertIn(b"X-Content-Type-Options: nosniff", data)
        self.assertNotIn("kapcsolati hiba (ValueError)", self.srv.log.getvalue())

    # ---------------------------------------------------------------- SEC-4
    def test_sec4_http09_is_refused_with_headers(self):
        for payload in (b"GET /\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port, b"GET /api/engine\r\n\r\n",
                        b"GET /api/engine\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port):
            data = _raw(self.port, payload)
            self.assertTrue(data.startswith(b"HTTP/1.0 400"), data[:80])
            self.assertIn(b"Content-Security-Policy:", data)
            self.assertIn(b"X-Frame-Options: DENY", data)
            self.assertNotIn(b"<!doctype html>", data.lower())
        st, _h, _env = self.call("GET", "/api/engine")
        self.assertEqual(st, 200)

    # ---------------------------------------------------------------- SEC-5 / SRV-5
    def test_sec5_unknown_method_is_not_echoed_to_log(self):
        data = _raw(self.port, b"\x1b]52;c;ZWNobyBwd25lZA==\x07\x1b[2J / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertTrue(data.startswith(b"HTTP/1.0 405"), data[:80])
        data = _raw(self.port, b"Aronson_1948;4;123\x1b[2J /x HTTP/1.0\r\n\r\n")
        self.assertTrue(data.startswith(b"HTTP/1.0 405"), data[:80])
        data = _raw(self.port, b"M" + b"X" * 60000 + b" /x HTTP/1.0\r\n\r\n")
        self.assertTrue(data.startswith(b"HTTP/1.0 405"), data[:80])
        log = self.srv.log.getvalue()
        self.assertNotIn("\x1b", log)
        self.assertNotIn("\x07", log)
        self.assertNotIn("Aronson", log)
        self.assertNotIn("XXXXXXXXXX", log)
        self.assertLess(max(len(ln) for ln in log.splitlines()), 300)
        self.assertIn("<?> <hibás kérés> 405 METHOD_NOT_ALLOWED", log)

    # ---------------------------------------------------------------- SEC-6
    def test_sec6_windows_device_names_refused(self):
        for name in ("03_adatok/COM¹.csv", "03_adatok/LPT0.csv", "03_adatok/CONIN$.csv", "03_adatok/conout$.tsv",
                     "03_adatok/COM0.csv", "03_adatok/lpt³.csv", "03_adatok/CON.csv"):
            env = self.err(self.table(name, ["study"], [["x"]]), 403, "FORBIDDEN")
            self.assertNotIn("¹", env["error"]["message"].replace(name, ""))
            fname = name.split("/", 1)[1]
            self.assertFalse(os.path.exists(os.path.join(self.proj, "03_adatok", fname)), name)
            self.err(self.call("GET", "/api/table?dataset=" + quote(name)), 403, "FORBIDDEN")
        for rel in ("x/COM¹.csv", "x/conin$.json", "x/LPT0"):
            with self.assertRaises(store.Forbidden):
                store.relpath_parts(rel)
        self.assertEqual({n.lower() for n in security.WINDOWS_RESERVED_NAMES}, set(store._WIN_RESERVED))

    # ---------------------------------------------------------------- P1
    def _prov(self, ds, uid, **cell):
        c = {"row_uid": uid, "field": "n1", "method": "reported"}
        c.update(cell)
        return {"schema": store.PROV_SCHEMA, "table": ds, "table_sha256": None, "cells": [c]}

    def test_p1_provenance_text_is_phi_scanned_on_table_save(self):
        taj = _taj()
        ds = "03_adatok/p1.csv"
        rows = [{"row_uid": "rabcdef", "cells": ["A", "10"]}]
        prov = self._prov(ds, "rabcdef", value_as_entered=taj,
                          source={"doc": "pmid:1", "page": 2, "quote": "Beteg TAJ: %s, szül. 1961.03.12." % taj})
        env = self.err(self.table(ds, ["study", "n1"], rows, provenance=prov), 403, "FORBIDDEN")
        paths = {f.get("path") for f in env["error"]["details"]["phi"]}
        self.assertIn("cells[0].value_as_entered", paths)
        self.assertIn("cells[0].source.quote", paths)
        self.assertNotIn(taj, json.dumps(env))
        self.assertFalse(os.path.exists(os.path.join(self.proj, ds)))
        self.assertFalse(os.path.exists(os.path.join(self.proj, "03_adatok", "p1.prov.json")))
        # indokolt felülbírálással menthető (döntésként naplózva)
        env = self.ok(self.table(ds, ["study", "n1"], rows, provenance=prov,
                                 phi_override="Teszt: a szám regisztrációs azonosító, nem TAJ."))
        self.assertTrue(any("PHI" in w for w in env["warnings"]))
        items = self.ok(self.call("GET", "/api/log/decision"))["data"]["items"]
        self.assertTrue(any(i["decision"] == "PHI-gyanú felülbírálva: %s" % ds for i in items))

    def test_p1_put_provenance_is_phi_scanned_and_override_not_stored(self):
        taj = _taj()
        spaced = "%s %s %s" % (taj[:3], taj[3:6], taj[6:])
        ds = "03_adatok/p1b.csv"
        self.ok(self.table(ds, ["study", "n1"], [{"row_uid": "rabcdef", "cells": ["A", "10"]}]))
        cur = self.ok(self.call("GET", "/api/provenance?dataset=" + quote(ds)))
        doc = self._prov(ds, "rabcdef", value_as_entered=spaced)
        hdr = [("If-Match", cur["data"]["etag"])] if cur["data"]["etag"] else []
        env = self.err(self.call("PUT", "/api/provenance?dataset=" + quote(ds), doc, headers=hdr), 403, "FORBIDDEN")
        self.assertTrue(env["error"]["details"]["phi"])
        self.assertFalse(os.path.exists(os.path.join(self.proj, "03_adatok", "p1b.prov.json")))
        doc["phi_override"] = "Teszt: oldalszám-tartomány, nem azonosító."
        env = self.ok(self.call("PUT", "/api/provenance?dataset=" + quote(ds), doc, headers=hdr))
        saved = json.loads(Path(self.proj, "03_adatok", "p1b.prov.json").read_text(encoding="utf-8"))
        self.assertNotIn("phi_override", saved)
        self.assertNotIn("phi_override", env["data"]["provenance"])

    def test_p1_timestamps_in_provenance_are_not_false_positives(self):
        ds = "03_adatok/p1c.csv"
        prov = self._prov(ds, "rabcdef", value_as_entered="10", extracted_by="user",
                          extracted_at="2026-10-05T10:00:00Z", verified_by="user", verified_at="2026-10-05T11:00:00Z",
                          source={"doc": "pmid:31234567", "page": 3, "locator": "Table 2", "quote": "n = 10"},
                          history=[{"value_as_entered": "9", "method": "reported", "extracted_at": "2026-10-04T09:00:00Z"}])
        env = self.ok(self.table(ds, ["study", "n1"], [{"row_uid": "rabcdef", "cells": ["A", "10"]}], provenance=prov))
        self.assertFalse([w for w in env["warnings"] if "PHI" in w])

    # ---------------------------------------------------------------- P2
    def test_p2_table_put_does_not_clobber_newer_provenance(self):
        ds = "03_adatok/p2.csv"
        rows = [{"row_uid": "rabcdef", "cells": ["A", "10"]}]
        tab1_prov = self._prov(ds, "rabcdef", method="estimated", estimated=True)
        env = self.ok(self.table(ds, ["study", "n1"], rows, provenance=tab1_prov))
        t_etag, p_etag = env["data"]["etag"], env["data"]["provenance_etag"]
        self.assertTrue(p_etag)
        self.assertEqual(p_etag, store.sha256_file(Path(self.proj, "03_adatok", "p2.prov.json")))
        # 2. lap: új eredet-bejegyzés a friss etaggel
        tab2 = self._prov(ds, "rabcdef")
        tab2["cells"].append({"row_uid": "rbbbbbb", "field": "n1", "method": "reported"})
        env2 = self.ok(self.call("PUT", "/api/provenance?dataset=" + quote(ds), tab2,
                                 headers=[("If-Match", '"%s"' % p_etag)]))
        p_etag2 = env2["data"]["etag"]
        # 1. lap: elavult eredet a még érvényes tábla-etaggel → 409, nem felülírás
        stale = self.table(ds, ["study", "n1"], rows, provenance=tab1_prov, provenance_if_match=p_etag,
                           headers=[("If-Match", '"%s"' % t_etag)])
        env = self.err(stale, 409, "CONFLICT")
        self.assertEqual(env["error"]["details"]["kind"], "provenance")
        self.assertEqual(env["error"]["details"]["etag"], p_etag2)
        # etag nélkül sem írhatja felül a meglévő oldalfájlt
        self.err(self.table(ds, ["study", "n1"], rows, provenance=tab1_prov, headers=[("If-Match", t_etag)]),
                 409, "CONFLICT")
        cur = json.loads(Path(self.proj, "03_adatok", "p2.prov.json").read_text(encoding="utf-8"))
        self.assertIn("rbbbbbb", [c["row_uid"] for c in cur["cells"]])
        # a friss etaggel mehet
        env = self.ok(self.table(ds, ["study", "n1"], rows, provenance=tab1_prov, provenance_if_match=p_etag2,
                                 headers=[("If-Match", t_etag)]))
        self.assertNotEqual(env["data"]["provenance_etag"], p_etag2)

    def test_p2_store_expects_provenance_etag(self):
        root = Path(_tmpdir("ma_gui_p2s_"))
        try:
            ps = store.ProjectStore(root)
            ds = "03_adatok/x.csv"
            prov = {"schema": store.PROV_SCHEMA, "table": ds, "table_sha256": None, "cells": []}
            t = ps.save_table(ds, ["study"], [{"row_uid": "rabcdef", "cells": ["A"]}], None, provenance=prov,
                              provenance_if_match=None)
            with self.assertRaises(store.Conflict) as cm:
                ps.save_table(ds, ["study"], [{"row_uid": "rabcdef", "cells": ["B"]}], t.etag, provenance=prov,
                              provenance_if_match=None)
            self.assertEqual(cm.exception.details["kind"], "provenance")
            self.assertEqual(ps.etag(ds), t.etag)          # a CSV sem íródott
            t2 = ps.save_table(ds, ["study"], [{"row_uid": "rabcdef", "cells": ["B"]}], t.etag, provenance=prov,
                               provenance_if_match=t.provenance_etag)
            self.assertEqual(t2.provenance_etag, ps.etag(store.provenance_relpath(ds)))
        finally:
            shutil.rmtree(str(root), ignore_errors=True)

    # ---------------------------------------------------------------- P3 (A osztály: almappa, .txt)
    def _external_edit_seen(self, rel, write):
        rev = self.ok(self.call("GET", "/api/changes"))["data"]["rev"]
        time.sleep(0.5)
        write()
        changed, external, since = set(), set(), rev
        deadline = time.monotonic() + 10
        while rel not in changed and time.monotonic() < deadline:
            data = self.ok(self.call("GET", "/api/changes?since=%d&wait=3" % since))["data"]
            changed.update(data["changed"])
            external.update(data["external"])
            since = data["rev"]
        self.assertIn(rel, changed)
        self.assertIn(rel, external)
        log_path = Path(self.proj, activity.LOG_RELPATH)
        found = _wait_for(lambda: [r for r in activity.read_records(log_path) if r["action"] == "file.external_edit"
                                   and r["outputs"][0]["path"] == rel])
        self.assertTrue(found, rel)
        self.assertEqual(found[-1]["actor"], "external")

    def test_p3_subfolder_and_txt_tables_are_watched(self):
        for ds in ("03_adatok/al/agg.csv", "03_adatok/agg.txt", "tablak/kulso.csv"):
            self.ok(self.table(ds, ["study", "n1"], [{"row_uid": "rabcdef", "cells": ["A", "10"]}]))
            p = Path(self.proj, *ds.split("/"))
            time.sleep(0.5)
            self._external_edit_seen(ds, lambda p=p: p.write_bytes(p.read_bytes().replace(b";10;", b";99;")))

    # ---------------------------------------------------------------- P5
    def test_p5_partial_pair_write_is_logged_and_reports_new_etag(self):
        ds = "03_adatok/p5.csv"
        rows = [{"row_uid": "rabcdef", "cells": ["A", "10"]}]
        env = self.ok(self.table(ds, ["study", "n1"], rows))
        etag = env["data"]["etag"]
        real_replace = store._replace

        def flaky(tmp, path):
            if str(path).endswith(store.PROV_SUFFIX):
                raise PermissionError(13, "zárolt")
            return real_replace(tmp, path)

        log_path = Path(self.proj, activity.LOG_RELPATH)
        before = len(activity.read_records(log_path))
        rows2 = [{"row_uid": "rabcdef", "cells": ["A", "11"]}]
        prov = self._prov(ds, "rabcdef")
        with mock.patch.object(store, "_replace", side_effect=flaky), \
                mock.patch.object(store, "_REPLACE_RETRY_DELAYS", ()):
            err = self.err(self.table(ds, ["study", "n1"], rows2, provenance=prov, headers=[("If-Match", etag)]),
                           423, "LOCKED")
        det = err["error"]["details"]
        self.assertTrue(det["partial"])
        self.assertEqual(det["written"], [ds])
        disk = store.sha256_file(Path(self.proj, ds))
        self.assertEqual(det["etag"], disk)
        self.assertIn(b";11;", Path(self.proj, ds).read_bytes())
        recs = activity.read_records(log_path)[before:]
        saves = [r for r in recs if r["action"] == "table.save"]
        self.assertTrue(saves, "a részleges írás nem került az activity-láncba")
        self.assertEqual(saves[-1]["outputs"][0]["sha256"], disk)
        self.assertTrue(saves[-1]["details"]["partial"])
        self.assertTrue(activity.verify_chain(log_path)[0])
        # a kapott etaggel az újramentés sikerül (most már az oldalfájllal együtt)
        env = self.ok(self.table(ds, ["study", "n1"], rows2, provenance=prov, headers=[("If-Match", det["etag"])]))
        self.assertTrue(env["data"]["provenance_etag"])
        self.assertTrue(Path(self.proj, "03_adatok", "p5.prov.json").is_file())

    # ---------------------------------------------------------------- P4 (A osztály: csak figyelmeztetés)
    def test_p4_log_text_phi_warns_in_class_a(self):
        env = self.ok(self.call("POST", "/api/log/finding", {"severity": "info", "title": "teszt",
                                                              "evidence": "TAJ %s" % _taj()}))
        self.assertTrue(any("PHI" in w for w in env["warnings"]))
        env = self.ok(self.call("POST", "/api/log/decision", {"decision": "A 2024.05.12-i megbeszélés alapján"}))
        self.assertFalse([w for w in env["warnings"] if "PHI" in w])

    # ---------------------------------------------------------------- SRV-7 (d)
    def test_srv7_sanitize_and_dumps_nan(self):
        nan, inf = float("nan"), float("inf")
        self.assertEqual(router.sanitize({"x": nan, "y": [inf, -inf, 1.5]}), {"x": None, "y": [None, None, 1.5]})
        self.assertEqual(router.dumps({"x": nan, "y": inf}), b'{"x":null,"y":null}')


# ======================================================================================== SEC-2
class ConnectionLimitTests(unittest.TestCase):
    """SEC-2: teljes kérés-határidő (a csepegtetés nem nyújtja) és korlátos kapcsolatszám."""

    def setUp(self):
        self.tmp = _tmpdir("ma_gui_conn_")
        self.proj, self.home = _make_project(self.tmp, "proj", data_class="A", init=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_trickled_headers_hit_total_deadline(self):
        srv = Srv(self.proj, self.home, self.tmp)
        srv.app.httpd.request_deadline = 1.0
        try:
            s = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
            try:
                s.sendall(b"GET /api/engine HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Foo: " % srv.port)
                t0 = time.monotonic()
                closed = False
                while time.monotonic() - t0 < 6.0:
                    time.sleep(0.3)
                    try:
                        s.sendall(b"a")
                        s.settimeout(0.05)
                        if s.recv(1024) == b"":
                            closed = True
                            break
                    except socket.timeout:
                        continue
                    except OSError:
                        closed = True
                        break
                elapsed = time.monotonic() - t0
            finally:
                s.close()
            self.assertTrue(closed, "a csepegtetett kérés kapcsolata nyitva maradt")
            self.assertLess(elapsed, 4.0)
            st, _h, _env = srv.call("GET", "/api/engine")
            self.assertEqual(st, 200)
        finally:
            srv.stop()

    def test_slow_body_drain_is_bounded_in_total(self):
        srv = Srv(self.proj, self.home, self.tmp)
        srv.app.httpd.request_deadline = 1.0
        try:
            s = socket.create_connection(("127.0.0.1", srv.port), timeout=10)
            try:
                # token nélkül: 403, a törzs „lecsorgatása” is a határidőhöz kötött
                s.sendall(b"POST /api/log/finding HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\n"
                          b"Content-Length: 100000\r\n\r\n{" % srv.port)
                t0 = time.monotonic()
                data = b""
                while time.monotonic() - t0 < 6.0:
                    time.sleep(0.3)
                    try:
                        s.sendall(b" ")
                    except OSError:
                        break
                    s.settimeout(0.05)
                    try:
                        chunk = s.recv(65536)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    data += chunk
                elapsed = time.monotonic() - t0
            finally:
                s.close()
            self.assertLess(elapsed, 4.0)
            self.assertTrue(data.startswith(b"HTTP/1.0 403"), data[:60])
        finally:
            srv.stop()

    def test_connection_cap_evicts_slow_readers(self):
        with mock.patch.object(server.MunkapadHTTPServer, "max_connections", 4):
            srv = Srv(self.proj, self.home, self.tmp)
        socks = []
        try:
            self.assertEqual(srv.app.httpd.max_connections, 4)
            srv.app.httpd.accept_wait = 0.3
            for _ in range(10):
                s = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
                s.sendall(b"GET / HTTP/1.1\r\nHost: x")
                socks.append(s)
            time.sleep(0.6)
            t0 = time.monotonic()
            st, _h, _env = srv.call("GET", "/api/engine", timeout=8)
            self.assertEqual(st, 200)
            self.assertLess(time.monotonic() - t0, 5.0)
            st, _h, _env = srv.call("GET", "/api/engine", timeout=8)
            self.assertEqual(st, 200)
            with srv.app.httpd._reading_lock:
                self.assertLessEqual(len(srv.app.httpd._reading), 4)
        finally:
            for s in socks:
                s.close()
            srv.stop()


# ======================================================================================== adatosztályok
@unittest.skipUnless(HAS_GIT, "git nélkül a vault-esetek nem állíthatók elő")
class PrivacyClassTests(unittest.TestCase):
    """P3 (C: _privat/ figyelése), P4 (szabad szöveg B/C-ben), P8 (ismeretlen osztály), P9 (C jegyzék)."""

    def setUp(self):
        self.tmp = _tmpdir("ma_gui_cls_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_p3_class_c_private_table_external_edit_is_logged(self):
        proj, home = _make_project(self.tmp, "projC", data_class="C", vault=True, git=True, block=True, guard=True)
        srv = Srv(proj, home, self.tmp)
        try:
            ds = "_privat/kohorsz.csv"
            st, _h, env = srv.call("PUT", "/api/table", {"dataset": ds, "header": ["study", "n"],
                                                          "rows": [{"row_uid": "rabcdef", "cells": ["A", "10"]}]})
            self.assertEqual(st, 200, env)
            time.sleep(0.6)
            rev = srv.call("GET", "/api/changes")[2]["data"]["rev"]
            p = Path(proj, "_privat", "kohorsz.csv")
            p.write_bytes(p.read_bytes().replace(b";10;", b";99;"))
            changed, external, since = set(), set(), rev
            deadline = time.monotonic() + 10
            while ds not in changed and time.monotonic() < deadline:
                data = srv.call("GET", "/api/changes?since=%d&wait=3" % since)[2]["data"]
                changed.update(data["changed"])
                external.update(data["external"])
                since = data["rev"]
            self.assertIn(ds, changed)
            self.assertIn(ds, external)
            log_path = Path(proj, activity.LOG_RELPATH)
            found = _wait_for(lambda: [r for r in activity.read_records(log_path)
                                       if r["action"] == "file.external_edit" and r["outputs"][0]["path"] == ds])
            self.assertTrue(found)
            self.assertEqual(found[-1]["outputs"][0]["sha256"], store.sha256_file(p))
        finally:
            srv.stop()

    def _sqlite_text(self, proj):
        con = sqlite3.connect(os.path.join(proj, "projekt.sqlite"))
        try:
            return "\n".join(con.iterdump())
        finally:
            con.close()

    def test_p4_free_text_phi_refused_in_tracked_class_b(self):
        proj, home = _make_project(self.tmp, "projB", data_class="B", vault=True, git=True, block=True)
        srv = Srv(proj, home, self.tmp)
        taj = _taj()
        spaced = "%s %s %s" % (taj[:3], taj[3:6], taj[6:])
        try:
            st, _h, env = srv.call("POST", "/api/log/finding",
                                   {"severity": "minor", "title": "adat", "evidence": "Kovács Jánosné, TAJ %s, "
                                                                                    "szül. 1961.03.12." % spaced})
            self.assertEqual((st, env["error"]["code"]), (403, "FORBIDDEN"), env)
            self.assertEqual([f["field"] for f in env["error"]["details"]["phi_fields"]], ["evidence"])
            self.assertNotIn(taj[:6], json.dumps(env))
            st, _h, env = srv.call("POST", "/api/log/decision", {"decision": "kizárás", "rationale": "TAJ " + taj})
            self.assertEqual(st, 403, env)
            # azonosító nélkül mehet
            st, _h, env = srv.call("POST", "/api/log/finding",
                                   {"severity": "minor", "title": "adat", "evidence": "a _privat/kohorsz.csv 14. sora"})
            self.assertEqual(st, 200, env)
            # a felülbírálás indoklása sem kerülhet TAJ-jal a projektnaplóba
            st, _h, env = srv.call("PUT", "/api/table", {"dataset": "03_adatok/k.csv", "header": ["study", "id"],
                                                          "rows": [["A", taj]], "consent": True,
                                                          "phi_override": "A %s nem TAJ" % taj})
            self.assertEqual((st, env["error"]["code"]), (403, "FORBIDDEN"), env)
            self.assertFalse(os.path.exists(os.path.join(proj, "03_adatok", "k.csv")))
            self.assertNotIn(taj, self._sqlite_text(proj))
            self.assertNotIn(spaced, self._sqlite_text(proj))
            # _privat/ alá PHI felülbírálás nélkül is mehet; a fölösleges felülbírálás nem lesz döntés
            n_dec = len(srv.call("GET", "/api/log/decision")[2]["data"]["items"])
            st, _h, env = srv.call("PUT", "/api/table", {"dataset": "_privat/k.csv", "header": ["study", "id"],
                                                          "rows": [["A", taj]], "phi_override": "regisztrációs szám"})
            self.assertEqual(st, 200, env)
            self.assertTrue(any("nem volt szükség" in w for w in env["warnings"]))
            self.assertEqual(len(srv.call("GET", "/api/log/decision")[2]["data"]["items"]), n_dec)
        finally:
            srv.stop()

    def test_p8_missing_project_json_fails_closed(self):
        proj, home = _make_project(self.tmp, "projU", data_class="C")
        os.makedirs(os.path.join(proj, "_privat"))
        Path(proj, "_privat", "kohorsz.csv").write_text("id;kor\n17;64\n", encoding="utf-8")
        srv = Srv(proj, home, self.tmp)
        try:
            body = {"dataset": "03_adatok/betegek.csv", "header": ["id", "kor", "nem", "kimenet"],
                    "rows": [["17", "64", "F", "1"]]}
            st, _h, env = srv.call("PUT", "/api/table", body)
            self.assertEqual(st, 403, env)
            os.unlink(os.path.join(proj, "ma-projekt.json"))
            d = srv.call("GET", "/api/project")[2]["data"]
            self.assertEqual((d["data_class"], d["data_class_source"]), ("C", "unknown"))
            st, _h, env = srv.call("PUT", "/api/table", body)
            self.assertEqual((st, env["error"]["code"]), (403, "FORBIDDEN"), env)
            self.assertIn("ismeretlen", env["error"]["message"])
            self.assertFalse(os.path.exists(os.path.join(proj, "03_adatok", "betegek.csv")))
            st, _h, env = srv.call("PUT", "/api/table", dict(body, dataset="_privat/betegek.csv"))
            self.assertEqual(st, 200, env)
            # az osztály csökkentése csak megerősítéssel, és döntésként naplózva
            st, _h, env = srv.call("POST", "/api/project", {"action": "data_class", "data_class": "A"})
            self.assertEqual((st, env["error"]["details"]["needs_confirm"]), (400, True), env)
            st, _h, env = srv.call("POST", "/api/project", {"action": "data_class", "data_class": "A", "confirm": True,
                                                             "reason": "publikált aggregált adatok"})
            self.assertEqual(st, 200, env)
            self.assertEqual((env["data"]["data_class"], env["data"]["data_class_source"]), ("A", "ma-projekt.json"))
            items = srv.call("GET", "/api/log/decision")[2]["data"]["items"]
            self.assertTrue(any(i["decision"] == "Adatosztály csökkentve: C → A" for i in items))
            recs = activity.read_records(Path(proj, activity.LOG_RELPATH))
            dc = [r for r in recs if r["action"] == "project.data_class"]
            self.assertTrue(dc[-1]["details"]["downgrade"])
            # emelés megerősítés nélkül is mehet
            st, _h, env = srv.call("POST", "/api/project", {"action": "data_class", "data_class": "B"})
            self.assertEqual(st, 200, env)
        finally:
            srv.stop()

    def test_p8_no_traces_defaults_to_class_a(self):
        proj, home = _make_project(self.tmp, "projD", data_class=None)
        srv = Srv(proj, home, self.tmp)
        try:
            d = srv.call("GET", "/api/project")[2]["data"]
            self.assertEqual((d["data_class"], d["data_class_source"]), ("A", "default"))
            st, _h, env = srv.call("PUT", "/api/table", {"dataset": "03_adatok/a.csv", "header": ["study"],
                                                          "rows": [["A"]]})
            self.assertEqual(st, 200, env)
        finally:
            srv.stop()

    def test_p9_class_c_document_register_lives_in_private(self):
        proj, home = _make_project(self.tmp, "projC9", data_class="C", vault=True, git=True, block=True, guard=True)
        Path(proj, "00_protokoll", "a.pdf").write_bytes(PDF_BYTES)
        srv = Srv(proj, home, self.tmp)
        try:
            doc = {"schema": "szk.ma.documents/v1",
                   "docs": [{"id": "pmid:123", "root": "project", "path": "00_protokoll/a.pdf"}]}
            st, h, env = srv.call("PUT", "/api/documents", doc)
            self.assertEqual(st, 200, env)
            self.assertTrue(Path(proj, "_privat", "documents.json").is_file())
            self.assertFalse(Path(proj, "03_adatok", "documents.json").exists())
            env = srv.call("GET", "/api/documents")[2]
            self.assertEqual([d["id"] for d in env["data"]["docs"]], ["pmid:123"])
            st, _h, env = srv.call("POST", "/api/fileurl", {"doc": "pmid:123"})
            self.assertEqual(st, 200, env)
            st, h, data = _http(srv.port, "GET", env["data"]["url"], None, [("Host", "127.0.0.1:%d" % srv.port)])
            self.assertEqual((st, data), (200, PDF_BYTES))
            self.assertTrue(privacy.is_ignored(proj, store.PRIVATE_DOCUMENTS_REL)[0])
        finally:
            srv.stop()


# ======================================================================================== P6, P7
class TempFileAndScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = _tmpdir("ma_gui_tmpf_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_p6_prefix_is_shared_and_gitignored(self):
        self.assertEqual(store.TMP_PREFIX, security.TMP_PREFIX)
        self.assertEqual(privacy.TMP_PREFIX, security.TMP_PREFIX)
        self.assertIn(security.TMP_PREFIX + "*", privacy.MANAGED_PATTERNS)
        self.assertIn(security.TMP_PREFIX + "*", privacy.managed_block_lines())
        self.assertTrue(privacy.is_sensitive_path("03_adatok/.ma-tmp-szerzoi_adat.csv-abc123.tmp"))

    def test_p6_crash_leftover_is_ignored_and_swept(self):
        root = Path(self.tmp, "proj")
        (root / "03_adatok").mkdir(parents=True)
        ps = store.ProjectStore(root)
        with mock.patch.object(store, "_replace", side_effect=SystemExit("kill -9")), \
                mock.patch.object(store, "_unlink", lambda p: None):
            with self.assertRaises(SystemExit):
                ps.save_table("03_adatok/szerzoi_adat.csv", ["study"], [["A"]], None)
        left = [p for p in (root / "03_adatok").iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(len(left), 1)
        self.assertTrue(left[0].name.startswith(".ma-tmp-szerzoi_adat.csv-"), left[0].name)
        rel = "03_adatok/" + left[0].name
        self.assertTrue(privacy.is_sensitive_path(rel))
        if HAS_GIT:
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            privacy.apply_gitignore(str(root))
            self.assertTrue(privacy.is_ignored(str(root), rel)[0])
        # friss: marad (futó írás lehet); régi: a takarítás törli
        self.assertEqual(store.sweep_temp_files(root), [])
        old = time.time() - store.TMP_SWEEP_AGE - 60
        os.utime(str(left[0]), (old, old))
        fresh = root / "03_adatok" / ".ma-tmp-uj.csv-xyz.tmp"
        fresh.write_bytes(b"x")
        other = root / "03_adatok" / ".hu.csv.abc.tmp"           # nem a mi előtagunk: nem nyúlunk hozzá
        other.write_bytes(b"x")
        os.utime(str(other), (old, old))
        self.assertEqual(store.sweep_temp_files(root), [rel])
        self.assertFalse(left[0].exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(other.exists())

    def test_p6_app_start_sweeps_stale_tmp(self):
        proj, home = _make_project(self.tmp, "proj", data_class="A", init=False)
        os.makedirs(os.path.join(proj, "_privat"))
        stale = Path(proj, "_privat", ".ma-tmp-k.csv-abc.tmp")
        stale.write_bytes(b"adat")
        old = time.time() - store.TMP_SWEEP_AGE - 60
        os.utime(str(stale), (old, old))
        srv = Srv(proj, home, self.tmp)
        try:
            self.assertTrue(_wait_for(lambda: not stale.exists(), timeout=5))
            self.assertIn("árva ideiglenes fájl törölve: 1 db", srv.log.getvalue())
        finally:
            srv.stop()

    def test_p6_privacy_atomic_write_uses_prefix(self):
        seen = []
        real = os.replace

        def spy(src, dst):
            seen.append(os.path.basename(src))
            return real(src, dst)

        proj = os.path.join(self.tmp, "p")
        os.makedirs(proj)
        with mock.patch.object(privacy.os, "replace", side_effect=spy):
            privacy.apply_gitignore(proj)
        self.assertTrue(seen and seen[0].startswith(".ma-tmp-.gitignore-"), seen)

    def test_p7_numeric_export_forms_of_taj(self):
        taj = _taj()
        for text in (taj + ".0", taj + ",0", "1.%sE+08" % taj[1:], "TAJ" + taj, "taj-szám: " + taj,
                     "%s  %s  %s" % (taj[:3], taj[3:6], taj[6:])):
            self.assertEqual(privacy.value_patterns(text), ["taj_cdv"], text)
            self.assertTrue(privacy.scan_table(["id"], [[text]]), text)
        zero = "0" + "1234567"
        zero += str(privacy.taj_check_digit(zero))
        self.assertTrue(privacy.is_valid_taj(zero))
        short = zero[1:]
        self.assertTrue(privacy.scan_table(["id"], [[short]]))
        self.assertTrue(privacy.scan_table(["azonosító"], [[short + ".0"]]))
        for col in ("pmid", "PMID", "év", "n1", "esemény1", "total"):
            self.assertEqual(privacy.scan_table([col], [[short]]), [], col)
        self.assertEqual(privacy.value_patterns(short), [])         # oszlop-kontextus nélkül nem

    def test_p7_no_false_positives_on_extraction_cells(self):
        for text in ("12.5", "1,25 (0,98-1,60)", "12/120", "NCT01234567", "10.1056/NEJMoa1234567",
                     "PMID 12345678", "120 (45%)", "123.456.789", "0.123456789", "123456789.5", "1.5E+08"):
            self.assertEqual(privacy.value_patterns(text), [], text)
        peldak = Path(ROOT, "peldak")
        for p in sorted(peldak.glob("*.csv")):
            t = store.parse_csv_bytes(p.read_bytes(), max_columns=None)
            if t.header is None:
                continue
            hits = privacy.scan_table(t.header.cells, [r.cells for r in t.rows])
            self.assertFalse([h for h in hits if h["kind"] == "value"], p.name)


# ======================================================================================== SRV
class RuntimeCliTests(unittest.TestCase):
    """SRV-1, SRV-2, SRV-4, SRV-6, SRV-7 (a, b, c), a selftest-jelvény logikája."""

    def setUp(self):
        self.tmp = _tmpdir("ma_gui_cli_")
        self.base = os.path.join(self.tmp, "rtbase")
        self.proj = os.path.join(self.tmp, "Proj")
        os.makedirs(self.proj)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patched_app(self):
        return functools.partial(server.App, selftest=False, kb_build=False, caps_refresh=False, log_stream=None,
                                 privacy_home=self.tmp, privacy_env={},
                                 caps=caps_mod.Caps(runtime_dir=os.path.join(self.tmp, "caps"), env={}, home=self.tmp))

    # -- SRV-1
    def test_srv1_project_key_uses_physical_identity(self):
        with mock.patch.object(runtime, "dir_identity", return_value="42:4242"):
            self.assertEqual(runtime.project_key("/Users/x/Proj"), runtime.project_key("/Users/x/proj"))
        self.assertEqual(runtime.project_key(self.proj), runtime.project_key(self.proj + os.sep))
        other = os.path.join(self.tmp, "masik")
        os.makedirs(other)
        self.assertNotEqual(runtime.project_key(self.proj), runtime.project_key(other))
        alias = os.path.join(self.tmp, "proj")
        if os.path.isdir(alias) and os.path.samefile(alias, self.proj):      # kis/nagybetű-független FS
            self.assertEqual(runtime.project_key(alias), runtime.project_key(self.proj))

    @unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0
                         and shutil.which("mount"), "bind mount csak rootként, Linuxon")
    def test_srv1_one_server_per_physical_folder(self):
        alias = os.path.join(self.tmp, "alias")
        os.makedirs(alias)
        if subprocess.run(["mount", "--bind", self.proj, alias], capture_output=True).returncode != 0:
            self.skipTest("a bind mount nem engedélyezett")
        try:
            self.assertTrue(os.path.samefile(alias, self.proj))
            self.assertNotEqual(os.path.realpath(alias), os.path.realpath(self.proj))
            with mock.patch.dict(os.environ, {"MA_GUI_RUNTIME_DIR": self.base}):
                mk = self._patched_app()
                first = mk(self.proj, single_instance=True, idle_hours=0)
                first.start(port=0)
                try:
                    second = mk(alias, single_instance=True, idle_hours=0)
                    with self.assertRaises(server.AlreadyRunning):
                        second.start(port=0)
                    second.close()
                    from ma_gui.routes import project as project_routes
                    self.assertTrue(project_routes._same_project(first, alias))
                finally:
                    first.close()
        finally:
            subprocess.run(["umount", alias], capture_output=True)

    # -- SRV-2
    def _holder(self, rdir, release_after, port):
        ready = threading.Event()

        def run():
            lock = runtime.InstanceLock(Path(rdir) / runtime.LOCK_NAME)
            assert lock.acquire()
            runtime.write_server_info(rdir, port, self.proj)
            runtime.new_admin_key(rdir)
            ready.set()
            time.sleep(release_after)
            runtime.remove_server_info(rdir)
            lock.release()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        ready.wait(5)
        return t

    def _closed_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def test_srv2_relaunch_returns_when_lock_is_freed(self):
        rdir = runtime.project_runtime_dir(self.proj, base=self.base)
        t = self._holder(rdir, 1.0, self._closed_port())
        out = io.StringIO()
        t0 = time.monotonic()
        rc = server._relaunch(rdir, argparse.Namespace(lang="hu", no_browser=True), out, time.monotonic() + 8)
        self.assertIsNone(rc, out.getvalue())
        self.assertLess(time.monotonic() - t0, 5.0)
        t.join(5)

    def test_srv2_cli_starts_after_shutdown_overlap(self):
        rdir = runtime.project_runtime_dir(self.proj, base=self.base)
        t = self._holder(rdir, 1.0, self._closed_port())
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"MA_GUI_RUNTIME_DIR": self.base}), \
                mock.patch.object(server, "App", self._patched_app()), \
                mock.patch.object(server.signal, "signal"):
            rc = server.cli_main(["--project", self.proj, "--no-browser", "--port", "0", "--idle-hours", "0.0003"],
                                 out=out)
        t.join(5)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("MA-munkapad", out.getvalue())
        self.assertIn(" fut ", out.getvalue())
        self.assertNotIn("HIBA", out.getvalue())

    # -- SRV-6
    def test_srv6_held_lock_builds_no_app_and_start_closes(self):
        rdir = runtime.project_runtime_dir(self.proj, base=self.base)
        lock = runtime.InstanceLock(Path(rdir) / runtime.LOCK_NAME)
        self.assertTrue(lock.acquire())
        try:
            built = []

            def fake_app(*a, **kw):
                built.append(a)
                raise AssertionError("App nem épülhet, ha a zár foglalt")

            out = io.StringIO()
            with mock.patch.dict(os.environ, {"MA_GUI_RUNTIME_DIR": self.base}), \
                    mock.patch.object(server, "App", fake_app), mock.patch.object(server, "RELAUNCH_WAIT", 0.5):
                rc = server.cli_main(["--project", self.proj, "--no-browser"], out=out)
            self.assertEqual(rc, 1, out.getvalue())
            self.assertIn("HIBA", out.getvalue())
            self.assertEqual(built, [])
            second = self._patched_app()(self.proj, runtime_dir=rdir, single_instance=True)
            with self.assertRaises(server.AlreadyRunning):
                second.start(port=0)
            self.assertTrue(second._closed)
            self.assertIsNone(second.watcher._db)
        finally:
            lock.release()

    # -- SRV-4
    def test_srv4_cli_errors_are_hungarian_lines(self):
        for port in ("70000", "-5"):
            out = io.StringIO()
            rc = server.cli_main(["--project", self.proj, "--port", port, "--no-browser"], out=out)
            self.assertEqual(rc, 2, out.getvalue())
            self.assertTrue(out.getvalue().startswith("HIBA:"), out.getvalue())
        notdir = os.path.join(self.tmp, "notadir")
        Path(notdir).write_text("x", encoding="utf-8")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"MA_GUI_RUNTIME_DIR": notdir}):
            rc = server.cli_main(["--project", self.proj, "--no-browser"], out=out)
        self.assertEqual(rc, 1, out.getvalue())
        self.assertTrue(out.getvalue().startswith("HIBA:"), out.getvalue())
        self.assertNotIn("Traceback", out.getvalue())

    # -- SRV-7 (a): portváltás
    def test_srv7_port_fallback_binds_next_candidate(self):
        busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        free = self._closed_port()
        try:
            with mock.patch.object(runtime, "PORT_RANGE", (busy.getsockname()[1], free)):
                app = self._patched_app()(self.proj, idle_hours=0)
                try:
                    app.start()
                    self.assertEqual(app.port, free)
                finally:
                    app.close()
        finally:
            busy.close()

    # -- SRV-7 (b): a long-poll nem aktivitás
    def test_srv7_longpoll_does_not_keep_server_alive(self):
        proj, home = _make_project(self.tmp, "idle", data_class="A", init=False)
        srv = Srv(proj, home, self.tmp, idle_hours=1.0 / 3600.0)
        try:
            rev = srv.call("GET", "/api/changes")[2]["data"]["rev"]
            t0 = time.monotonic()
            while srv.thread.is_alive() and time.monotonic() - t0 < 6.0:
                try:
                    srv.call("GET", "/api/changes?since=%d&wait=0" % rev, timeout=3)
                except OSError:
                    break
                time.sleep(0.15)
            srv.thread.join(3)
            self.assertFalse(srv.thread.is_alive(), "a long-poll aktivitásnak számított")
        finally:
            srv.stop()

    # -- SRV-7 (c): server.json tulajdon
    def test_srv7_remove_server_info_respects_pid(self):
        rdir = runtime.project_runtime_dir(self.proj, base=self.base)
        runtime.write_server_info(rdir, 8790, self.proj, pid=os.getpid() + 1)
        runtime.new_admin_key(rdir)
        self.assertFalse(runtime.remove_server_info(rdir, pid=os.getpid()))
        self.assertIsNotNone(runtime.read_server_info(rdir))
        self.assertIsNotNone(runtime.read_admin_key(rdir))
        self.assertTrue(runtime.remove_server_info(rdir, pid=os.getpid() + 1))
        self.assertIsNone(runtime.read_server_info(rdir))

    # -- SRV-7: az önteszt-jelvény a forrás-esetek eredményét tükrözi (nem mindig 'error')
    def _fake_cases(self, body):
        root = Path(self.tmp, "eng")
        (root / "tests").mkdir(parents=True, exist_ok=True)
        (root / "tests" / "source_cases.py").write_text(body, encoding="utf-8")
        return root

    def test_srv7_selftest_states(self):
        ok = self._fake_cases("def load_cases():\n    return [{'id': 1}, {'id': 2, 'status': 'retired'}]\n"
                              "def check_case(c):\n    return [(0, 0, 0, 0, True), (0, 0, 0, 0, True)]\n")
        res = runtime.run_engine_selftest(root=ok, timeout=60)
        self.assertEqual((res["state"], res["checks"], res["failed"], res["active"]), ("pass", 2, 0, 1), res)
        bad = self._fake_cases("def load_cases():\n    return [{'id': 1}]\n"
                               "def check_case(c):\n    return [(0, 0, 0, 0, True), (0, 0, 0, 0, False)]\n")
        res = runtime.run_engine_selftest(root=bad, timeout=60)
        self.assertEqual((res["state"], res["failed"]), ("fail", 1), res)
        boom = self._fake_cases("raise RuntimeError('x')\n")
        self.assertEqual(runtime.run_engine_selftest(root=boom, timeout=60)["state"], "error")
        self.assertEqual(runtime.run_engine_selftest(root=Path(self.tmp, "nincs"))["state"], "unknown")


# ======================================================================================== SRV-3
class KbRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = _tmpdir("ma_gui_kb_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_srv3_failed_startup_kb_build_recovers(self):
        proj, home = _make_project(self.tmp, "proj", data_class="A", init=False)
        real = kb_mod.ensure_built
        calls = []

        def flaky(db=None):
            calls.append(db)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            return real(db)

        with mock.patch.object(kb_mod, "ensure_built", side_effect=flaky):
            srv = Srv(proj, home, self.tmp, kb_build=True, kb_retry_s=3600.0)
            try:
                srv.app._kb_thread.join(10)
                self.assertEqual(srv.app.kb_info()["state"], "error")
                st, _h, env = srv.call("GET", "/api/kb/search?q=heterogeneity")
                self.assertEqual((st, env["error"]["code"]), (424, "CAPABILITY_MISSING"))     # még a várakozási időn belül
                srv.app.kb_retry_s = 0.0
                st, _h, env = srv.call("GET", "/api/kb/search?q=heterogeneity")
                self.assertEqual(st, 200, env)
                self.assertEqual(srv.call("GET", "/api/engine")[2]["data"]["kb"]["state"], "ok")
                self.assertGreaterEqual(len(calls), 2)          # (a kb.search maga is hívhatja)
            finally:
                srv.stop()


if __name__ == "__main__":
    unittest.main()
