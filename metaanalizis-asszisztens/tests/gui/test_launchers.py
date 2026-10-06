# -*- coding: utf-8 -*-
"""DOC-2: a ma-munkapad.cmd (Windows) és ma-munkapad.command (macOS) indító (TERV 2.2).

A .command-ot valóban lefuttatjuk (bash, hamis PATH-szal): a Store-csonkot utánzó ``python`` (nem nulla kilépési kód)
kiesik, a következő jelölt (``python3``) indul; nem létező mappánál érthető magyar üzenet és 1-es kilépési kód. A
.cmd-et itt csak statikusan ellenőrizzük (CRLF, keresési sorrend, verziószonda, a ``ma.py gui --project`` hívás)."""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CMD = os.path.join(ROOT, "ma-munkapad.cmd")
COMMAND = os.path.join(ROOT, "ma-munkapad.command")
BASH = shutil.which("bash")


class WindowsLauncher(unittest.TestCase):
    def setUp(self):
        with open(CMD, "rb") as fh:
            self.raw = fh.read()
        self.text = self.raw.decode("utf-8")

    def test_crlf_and_utf8_codepage(self):
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""), "a .cmd minden sora CRLF")
        lines = self.text.split("\r\n")
        self.assertEqual(lines[0], "@echo off")
        chcp = next(i for i, ln in enumerate(lines) if ln.startswith("chcp 65001"))
        before = "\r\n".join(lines[:chcp])
        self.assertTrue(all(ord(c) < 128 for c in before), "a chcp 65001 előtti sorok ASCII-k")
        self.assertIn('set "PYTHONUTF8=1"', self.text)

    def test_search_order_and_store_stub_probe(self):
        order = [self.text.index(x) for x in ("py -3 -c", "python -c", "python3 -c", r"..\.claude\.venv\Scripts\python.exe")]
        self.assertEqual(order, sorted(order), "keresési sorrend: py -3, python, python3, .claude\\.venv")
        self.assertIn("sys.version_info[:2] >= (3, 9)", self.text)
        # minden jelölt a szondán át kerül be (a csonk nem 0-val lép ki → && nem állítja be)
        for line in self.text.split("\r\n"):
            if re.search(r'set "PYEXE=(?!")', line):
                self.assertIn('-c "%PROBE%" >nul 2>&1 &&', line, line)
        self.assertRegex(self.text, r'"%PYEXE%" %PYARG% "%HERE%ma\.py" gui --project "%PROJ%"')

    def test_beginner_messages(self):
        self.assertIn("Projektmappa:", self.text)
        self.assertIn("TELEPITES.md, 1. pont", self.text)
        self.assertIn("pause", self.text, "hibánál az ablak nyitva marad, hogy olvasható legyen az üzenet")
        self.assertNotIn("-m ma_gui", self.text)


@unittest.skipIf(os.name == "nt" or not BASH, "bash kell (macOS/Linux)")
class MacLauncher(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_launch_"))
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        # Store-csonk / régi Python helyett: nem nulla kóddal kilépő „python”
        self._script("python", "#!/bin/sh\necho 'Python was not found; run without arguments to install from the "
                               "Microsoft Store' >&2\nexit 9009\n")
        self._script("python3", '#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
        self.env = {"PATH": self.bin + os.pathsep + "/usr/bin" + os.pathsep + "/bin", "HOME": self.tmp,
                    "LANG": "C.UTF-8"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _script(self, name, body):
        p = os.path.join(self.bin, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(p, 0o755)

    def run_launcher(self, *args, stdin=""):
        return subprocess.run([BASH, COMMAND] + list(args), input=stdin, capture_output=True, text=True,
                              encoding="utf-8", env=self.env, timeout=120, cwd=self.tmp)

    def test_executable_lf_and_syntax(self):
        with open(COMMAND, "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.startswith(b"#!/bin/bash\n"))
        self.assertNotIn(b"\r", raw)
        self.assertTrue(os.stat(COMMAND).st_mode & stat.S_IXUSR, "futtatható (dupla kattintás a Finderben)")
        self.assertEqual(subprocess.run([BASH, "-n", COMMAND]).returncode, 0)

    def test_store_stub_filtered_next_candidate_runs(self):
        proj = os.path.join(self.tmp, "Saját projekt")
        os.makedirs(proj)
        r = self.run_launcher(proj, "--help")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("A munkapad indul (python3)", r.stdout, "a nem működő python kiesett, a python3 indult")
        self.assertIn("usage: ma.py gui", r.stdout)

    def test_dragged_path_from_prompt(self):
        """Dupla kattintás: a Terminálba húzott út („\\ ” a szóközök előtt, záró szóköz) is működik."""
        proj = os.path.join(self.tmp, "Saját projekt")
        os.makedirs(proj)
        self._script("python3", '#!/bin/sh\necho "ARGS:$*"\n[ "$1" = "-c" ] && exec "%s" "$@"\nexit 0\n'
                     % sys.executable)
        r = self.run_launcher(stdin=proj.replace(" ", "\\ ") + " \n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Add meg a projektmappát", r.stdout)       # a read -p prompt csak terminálon látszik
        self.assertIn("ARGS:%s gui --project %s" % (os.path.join(ROOT, "ma.py"), proj), r.stdout)

    def test_missing_folder_and_no_python(self):
        r = self.run_launcher(os.path.join(self.tmp, "nincs"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("Nincs ilyen mappa", r.stdout)
        self.assertIn("project init", r.stdout)
        os.remove(os.path.join(self.bin, "python3"))
        tools = os.path.join(self.tmp, "tools")             # csak a szkript segédprogramjai, Python nélkül
        os.makedirs(tools)
        os.symlink(shutil.which("dirname"), os.path.join(tools, "dirname"))
        self.env["PATH"] = self.bin + os.pathsep + tools
        r = self.run_launcher(self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Nem találtam használható Python 3.9+", r.stdout)


if __name__ == "__main__":
    unittest.main()
