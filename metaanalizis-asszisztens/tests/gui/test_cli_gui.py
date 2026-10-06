# -*- coding: utf-8 -*-
"""A `ma.py gui` parancssori útja végponttól végpontig (terv 2.5, 3.1, 9.2) — valódi alfolyamatban.

- `python ma.py gui --project P --no-browser --port 0`: a kiírt címből (csak 127.0.0.1) az indítókód → token
  (`POST /api/session`), `GET /api/engine` (a motor-homlokzat verziója), a kód második használata 403, a `GET /`
  token és projektadat nélkül; Ctrl-C (SIGINT) → tiszta leállás 0-s kóddal, a port bezárul, a példányzár
  felszabadul (az újraindítás új címet ad, nem a futó példánytól kér kódot).
- `ma.py gui snapshot …` / `ma.py gui audit-export …` (és a magyar álnevek): a ma_gui saját belépési pontjai a motor
  CLI-jén át; hibás kérés (táblák C osztályú projektből) 2-es kód; hiányzó ma_gui → érthető HIBA, 2-es kód.

Csak stdlib (subprocess, http.client)."""
import contextlib
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import test_routes_harness as H  # noqa: E402
from metaelemzes import api, cli  # noqa: E402

MA_PY = os.path.join(ROOT, "ma.py")
URL_RE = re.compile(r"Cím: (http://127\.0\.0\.1:(\d+)/#launch=([A-Za-z0-9_-]+))")
START_TIMEOUT = 60.0


def _env(tmp):
    env = dict(os.environ)
    env.update({"HOME": os.path.join(tmp, "home"), "USERPROFILE": os.path.join(tmp, "home"),
                "MA_GUI_RUNTIME_DIR": os.path.join(tmp, "rt"),
                "METAELEMZES_KB": os.path.join(tmp, "kb.sqlite"), "PYTHONIOENCODING": "utf-8"})
    env.pop("MA_ACTIVITY_LOG", None)
    os.makedirs(env["HOME"], exist_ok=True)
    return env


def _call(port, method, path, body=None, token=None, timeout=30):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        headers = {"Host": "127.0.0.1:%d" % port}
        data = None
        if token:
            headers["X-MA-Token"] = token
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, raw
    finally:
        conn.close()


def _start(proj, env):
    """`python ma.py gui …` alfolyamatban → (folyamat, a cím-sor illesztése, a kiírt sorok)."""
    proc = subprocess.Popen([sys.executable, MA_PY, "gui", "--project", proj, "--no-browser", "--port", "0",
                             "--idle-hours", "0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=ROOT, text=True, encoding="utf-8")
    lines, match = [], None
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line)
        match = URL_RE.search(line)
        if match:
            break
    return proc, match, lines


def _stop(proc, timeout=20):
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise AssertionError("a munkapad nem állt le %d s alatt (SIGINT)" % timeout)
    return proc.returncode, out, err


@unittest.skipIf(sys.platform == "win32", "a Ctrl-C (SIGINT) jelzés küldése más folyamatnak POSIX-specifikus")
class MaPyGuiEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = H.tmpdir("ma_cli_gui_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj, _home = H.make_project(self.tmp)
        self.env = _env(self.tmp)

    def test_start_session_engine_and_clean_shutdown(self):
        proc, m, lines = _start(self.proj, self.env)
        try:
            self.assertIsNotNone(m, "nincs cím a kimenetben: %r" % lines)
            self.assertTrue(any(l.startswith("MA-munkapad ") and " fut — projekt: " in l for l in lines), lines)
            url, port, code = m.group(1), int(m.group(2)), m.group(3)
            self.assertTrue(url.startswith("http://127.0.0.1:"), url)
            self.assertGreater(port, 0)
            # a GET / adat és token nélkül: a lefordított felület (dist), a kód nincs benne
            st, h, page = _call(port, "GET", "/")
            self.assertEqual(st, 200)
            self.assertTrue(h["content-type"].startswith("text/html"))
            self.assertIn("default-src 'none'", h["content-security-policy"])
            self.assertNotIn(code.encode("ascii"), page)
            # token nélkül minden /api/* 403
            self.assertEqual(_call(port, "GET", "/api/engine")[0], 403)
            # indítókód → token (egyszer)
            st, _h, raw = _call(port, "POST", "/api/session", {"launch_code": code})
            self.assertEqual(st, 200, raw)
            token = json.loads(raw.decode("utf-8"))["data"]["token"]
            self.assertEqual(_call(port, "POST", "/api/session", {"launch_code": code})[0], 403)
            st, _h, raw = _call(port, "GET", "/api/engine", token=token)
            self.assertEqual(st, 200, raw)
            env = json.loads(raw.decode("utf-8"))
            self.assertIs(env["ok"], True)
            self.assertEqual(env["data"]["engine_version"], api.__version__)
            self.assertIs(env["data"]["facade"], True)
            self.assertEqual(env["meta"]["engine"], api.__version__)
            st, _h, raw = _call(port, "GET", "/api/project", token=token)
            self.assertEqual(st, 200, raw)
            self.assertEqual(json.loads(raw.decode("utf-8"))["data"]["title"], "BCG és Normand — teszt")
        finally:
            rc, out, err = _stop(proc)
        self.assertEqual(rc, 0, (out, err))
        self.assertIn("Leállítás", out)
        self.assertNotIn("Traceback", out + err)
        # a szervernapló (stderr) nem tartalmaz tokent / indítókódot / cellaértéket
        self.assertNotIn(code, err)
        self.assertNotIn(token, err)
        self.assertNotIn("Aronson", err)
        # a port bezárult
        with self.assertRaises(OSError):
            _call(port, "GET", "/", timeout=3)
        # a példányzár felszabadult: az újraindítás saját szervert indít (új port, új kód)
        proc2, m2, lines2 = _start(self.proj, self.env)
        try:
            self.assertIsNotNone(m2, lines2)
            self.assertNotEqual(m2.group(3), code)
            self.assertFalse(any("már fut" in l for l in lines2), lines2)
            st, _h, raw = _call(int(m2.group(2)), "POST", "/api/session", {"launch_code": m2.group(3)})
            self.assertEqual(st, 200, raw)
        finally:
            rc2, out2, err2 = _stop(proc2)
        self.assertEqual(rc2, 0, (out2, err2))

    def test_bad_project_is_an_error_without_traceback(self):
        r = subprocess.run([sys.executable, MA_PY, "gui", "--project", os.path.join(self.tmp, "nincs"),
                            "--no-browser", "--port", "0"], capture_output=True, text=True, encoding="utf-8",
                           env=self.env, cwd=ROOT, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("HIBA:", r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)


class GuiSubcommandsViaEngineCli(unittest.TestCase):
    """`ma.py gui snapshot` / `ma.py gui audit-export` a motor CLI-jén át (a snapshot-munkafolyam cross_requestje)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_cli_sub_")
        cls.proj, _home = H.make_project(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def run_ma(self, *args):
        r = subprocess.run([sys.executable, MA_PY] + list(args), capture_output=True, text=True, encoding="utf-8",
                           env=_env(self.tmp), cwd=ROOT, timeout=300)
        return r.returncode, r.stdout, r.stderr

    def test_snapshot_subcommand_and_alias(self):
        out1 = os.path.join(self.tmp, "a.snapshot.html")
        rc, so, se = self.run_ma("gui", "snapshot", "--project", self.proj, "--out", out1, "--json")
        self.assertEqual(rc, 0, so + se)
        info = json.loads(so)
        self.assertEqual(info["path"], out1)
        with open(out1, "rb") as fh:
            data = fh.read()
        self.assertTrue(data.lstrip().lower().startswith(b"<!doctype html"), data[:80])
        self.assertIn(b"connect-src 'none'", data)
        self.assertEqual(info["bytes"], len(data))
        # magyar álnév: ugyanaz a parancs; ugyanabból az állapotból bájtra azonos
        out2 = os.path.join(self.tmp, "b.snapshot.html")
        rc, so, se = self.run_ma("munkapad", "pillanatkep", "--project", self.proj, "--out", out2)
        self.assertEqual(rc, 0, so + se)
        self.assertIn("Pillanatkép kész", so)
        self.assertIn("Claude Artifactként sem", so)
        with open(out2, "rb") as fh:
            self.assertEqual(fh.read(), data)

    def test_audit_export_subcommand_and_alias(self):
        out1 = os.path.join(self.tmp, "a.zip")
        rc, so, se = self.run_ma("gui", "audit-export", "--project", self.proj, "--out", out1, "--json")
        self.assertEqual(rc, 0, so + se)
        info = json.loads(so)
        with zipfile.ZipFile(out1) as z:
            self.assertIn("manifest.json", z.namelist())
        out2 = os.path.join(self.tmp, "b.zip")
        rc, so, se = self.run_ma("gui", "audit-csomag", "--project", self.proj, "--out", out2)
        self.assertEqual(rc, 0, so + se)
        self.assertIn("Audit-csomag kész", so)
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            zbytes = f1.read()
            self.assertEqual(zbytes, f2.read())                 # determinisztikus
        self.assertEqual(info["sha256"], hashlib.sha256(zbytes).hexdigest())

    def test_class_c_tables_refused_with_exit_2(self):
        tmp = H.tmpdir("ma_cli_c_")
        self.addCleanup(shutil.rmtree, tmp, True)
        proj, _home = H.make_project(tmp, name="c", data_class="C")
        r = subprocess.run([sys.executable, MA_PY, "gui", "snapshot", "--project", proj, "--keep", "tables",
                            "--out", os.path.join(tmp, "x.html")], capture_output=True, text=True,
                           encoding="utf-8", env=_env(tmp), cwd=ROOT, timeout=300)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("HIBA:", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(tmp, "x.html")))

    def test_help_mentions_subcommands_and_publish_warning(self):
        rc, so, _se = self.run_ma("gui", "-h")
        self.assertEqual(rc, 0)
        flat = " ".join(so.split())
        self.assertIn("ma.py gui snapshot", flat)
        self.assertIn("ma.py gui audit-export", flat)
        self.assertIn("Claude Artifactként sem", flat)
        rc, so, _se = self.run_ma("gui", "snapshot", "-h")
        self.assertEqual(rc, 0)
        self.assertIn("--redact", so)

    def test_missing_ma_gui_is_a_clear_error(self):
        real = __import__("importlib").import_module

        def broken(name, *a, **kw):
            if name.startswith("ma_gui"):
                raise ImportError("szándékosan hiányzik")
            return real(name, *a, **kw)
        err = io.StringIO()
        with mock.patch("importlib.import_module", side_effect=broken), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["gui", "snapshot", "--project", self.proj])
            rc2 = cli.main(["munkapad", "audit-export", "--project", self.proj])
        self.assertEqual((rc, rc2), (2, 2))
        self.assertIn("HIBA: a munkapad (ma_gui) nem érhető el: ImportError", err.getvalue())

    def test_other_gui_flags_still_reach_the_launcher(self):
        """A `gui` indító kapcsolói változatlanul az argparse-on mennek (a snapshot-ág csak a 2. szónál ágazik)."""
        self.assertIsNone(cli._gui_subcommand(["gui", "--project", "x"]))
        self.assertIsNone(cli._gui_subcommand(["analyze", "snapshot"]))
        self.assertIsNone(cli._gui_subcommand(["gui"]))


if __name__ == "__main__":
    unittest.main()
