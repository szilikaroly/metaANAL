# -*- coding: utf-8 -*-
"""A felület-build (ma_gui/web/build_gui.py) tesztjei — böngésző nélkül (terv 6.8, 7.1 T6/T9, 7.6, 8.6).

Futtatás: python3 -m pytest tests/gui/ui/test_build_gui.py   vagy   python3 tests/gui/ui/test_build_gui.py
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "ma_gui" / "web"
SRC = WEB / "src"


def _load_build():
    spec = importlib.util.spec_from_file_location("ma_gui_build_gui", str(WEB / "build_gui.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bg = _load_build()


class BuildOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prod = bg.build(dev=False)
        cls.dev = bg.build(dev=True)

    def test_deterministic(self):
        self.assertEqual(self.prod, bg.build(dev=False))
        self.assertEqual(self.dev, bg.build(dev=True))

    def test_size_limit(self):
        self.assertLessEqual(len(self.prod.encode("utf-8")), bg.MAX_BYTES)

    def test_every_script_and_style_has_nonce_placeholder(self):
        n_blocks = len(re.findall(r"<(script|style)\b([^>]*)>(.*?)</\1\s*>", self.prod, re.S))
        self.assertEqual(self.prod.count('nonce="{{CSP_NONCE}}"'), n_blocks)
        self.assertEqual(n_blocks, 3)      # <style>, i18n-JSON, <script>
        self.assertEqual(self.dev.count('nonce="{{CSP_NONCE}}"'), 4)  # + fixture-JSON

    def test_only_nonce_placeholder_left(self):
        self.assertEqual(set(re.findall(r"\{\{([A-Z_]+)\}\}", self.prod)), {"CSP_NONCE"})

    def test_no_external_urls(self):
        urls = set(re.findall(r"https?://[^\s\"'<>)]*", self.prod + self.dev))
        self.assertEqual(urls, {bg.SVG_NS})

    def test_prod_has_no_fixture_code(self):
        low = self.prod.lower()
        for bad in ("fixture", "ma.dev", "/*<dev>", "__devsettransport", 'id="ma-fixtures"'):
            self.assertNotIn(bad, low)
        self.assertIn('id="ma-fixtures"', self.dev)
        self.assertIn("MA.__devSetTransport", self.dev)

    def test_banned_apis_absent(self):
        for pat in (r"innerHTML", r"outerHTML", r"insertAdjacentHTML", r"document\.write", r"toFixed",
                    r"toPrecision", r"Intl\.NumberFormat", r"\beval\s*\(", r"indexedDB", r"<link rel=\"stylesheet\""):
            self.assertIsNone(re.search(pat, self.prod), pat)

    def test_math_only_in_geom(self):
        for part in re.split(r"/\* --- ", self.prod):
            name = part.split(" --- */", 1)[0]
            if name.endswith(".js") and name != "geom.js":
                self.assertNotIn("Math.", part, name)

    def test_i18n_embedded_and_parity(self):
        for html in (self.prod, self.dev):
            m = re.search(r'<script type="text/plain" id="ma-i18n" data-enc="lz1"[^>]*>(.*?)</script>', html, re.S)
            data = bg.unpack_i18n(m.group(1))
            self.assertEqual(set(data["hu"]), set(data["en"]))
            self.assertGreater(len(data["hu"]), 100)
            self.assertEqual(data, bg.load_i18n(SRC))


class StaticSourceTests(unittest.TestCase):
    def test_i18n_keys_used_in_js_exist(self):
        i18n = bg.load_i18n(SRC)
        used = set()
        pats = [r"\bt\('([A-Za-z][\w.-]*)'", r"\bi18n: '([A-Za-z][\w.-]*)'", r"_key: '([A-Za-z][\w.-]*)'",
                r"emptyState\('([A-Za-z][\w.-]*)'", r"spinner\('([A-Za-z][\w.-]*)'", r"MA\.i18n\.t\('([A-Za-z][\w.-]*)'"]
        for f in SRC.rglob("*.js"):
            text = f.read_text(encoding="utf-8")
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)          # kommentek (példakódok) nélkül
            text = re.sub(r"(?m)^\s*//.*$", "", text)
            for p in pats:
                used.update(re.findall(p, text))
        # a '.'-ra végződő kulcs dinamikus előtag ('caps.state.' + állapot) — azt a selftest ellenőrzi
        missing = sorted(k for k in used if not k.endswith(".") and k not in i18n["hu"])
        self.assertEqual(missing, [])

    def test_order_lists_all_modules(self):
        order = bg.js_order(SRC, dev=False)
        self.assertEqual(order[:5], ["dom.js", "geom.js", "i18n.js", "store.js", "api.js"])
        self.assertEqual(order[-1], "boot.js")
        self.assertNotIn("dev/fixture_backend.js", order)
        self.assertIn("dev/fixture_backend.js", bg.js_order(SRC, dev=True))

    def test_fixtures_are_valid_envelopes(self):
        fx = bg.load_fixtures(WEB / "fixtures")
        for name in ("session.json", "engine.json", "project.json", "privacy.json", "capabilities.json", "changes.json"):
            self.assertIn(name, fx)

    @unittest.skipIf(shutil.which("node") is None, "node nincs telepítve")
    def test_es2019_parse(self):
        """Minden forrás ES2019-ként (classic script) értelmezhető — ha van ESLint."""
        cands = [shutil.which("eslint"), "/opt/node22/lib/node_modules/eslint/bin/eslint.js"]
        eslint = next((c for c in cands if c and Path(c).exists()), None)
        if eslint is None:
            self.skipTest("ESLint nem található")
        cmd = (["node", eslint] if eslint.endswith(".js") else [eslint]) + [
            "--no-config-lookup", "--parser-options", "ecmaVersion:2019", "--parser-options", "sourceType:script"]
        files = [str(f) for f in sorted(SRC.rglob("*.js"))]
        r = subprocess.run(cmd + files, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @unittest.skipIf(shutil.which("node") is None, "node nincs telepítve")
    def test_js_syntax(self):
        for f in sorted(SRC.rglob("*.js")):
            r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (f, r.stderr))


class LintRejectionTests(unittest.TestCase):
    """A build-lint a tiltott mintákat elutasítja (egy ideiglenes forrásmásolaton)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ma_gui_build_"))
        self.src = self.tmp / "src"
        shutil.copytree(str(SRC), str(self.src))
        for d in ("screens", "plots", "components", "dev", "i18n/hu", "i18n/en"):
            (self.src / d).mkdir(parents=True, exist_ok=True)   # git nem követi az üres mappákat

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def _screen(self, body):
        (self.src / "screens" / "zz_test.js").write_text(
            "(function () {\n  'use strict';\n  var MA = window.MA;\n  %s\n})();\n" % body, encoding="utf-8")

    def _assert_rejected(self, body, needle):
        self._screen(body)
        with self.assertRaises(bg.BuildError) as cm:
            bg.build(dev=False, src=self.src, fixtures=WEB / "fixtures")
        self.assertIn(needle, str(cm.exception))

    def test_clean_copy_builds(self):
        self._screen("var x = 1;")
        bg.build(dev=False, src=self.src, fixtures=WEB / "fixtures")

    def test_rejects_html_string(self):
        self._assert_rejected("document.body.inner" + "HTML = '<b>x</b>';", "HTML-sztring")

    def test_rejects_number_formatting(self):
        self._assert_rejected("var s = (0.125).to" + "Fixed(2);", "számformázás")

    def test_rejects_math_outside_geom(self):
        self._assert_rejected("var r = Ma" + "th.round(1.5);", "geom.js")

    def test_rejects_storage(self):
        self._assert_rejected("window.local" + "Storage.getItem('x');", "localStorage")
        self._assert_rejected("window.indexed" + "DB.open('x');", "IndexedDB")

    def test_rejects_external_url(self):
        self._assert_rejected("var u = 'https:" + "//cdn.example.org/x.js';", "külső URL")

    def test_rejects_es2020(self):
        self._assert_rejected("var a = MA?" + ".app;", "ES2020")
        self._assert_rejected("var b = MA.x ?" + "? 1;", "ES2020")

    def test_rejects_fetch_outside_api(self):
        self._assert_rejected("fe" + "tch('/api/x');", "api.js")

    def test_rejects_style_attribute(self):
        self._assert_rejected("document.body.setAttribute('sty" + "le', 'color:red');", "CSP")

    def test_rejects_missing_use_strict(self):
        (self.src / "screens" / "zz_test.js").write_text("(function () { var a = 1; })();\n", encoding="utf-8")
        with self.assertRaises(bg.BuildError) as cm:
            bg.build(dev=False, src=self.src, fixtures=WEB / "fixtures")
        self.assertIn("use strict", str(cm.exception))

    def test_rejects_stray_js(self):
        (self.src / "misc.js").write_text("(function () { 'use strict'; })();\n", encoding="utf-8")
        with self.assertRaises(bg.BuildError) as cm:
            bg.build(dev=False, src=self.src, fixtures=WEB / "fixtures")
        self.assertIn("ismeretlen helyen", str(cm.exception))

    def test_rejects_i18n_mismatch(self):
        (self.src / "i18n" / "hu" / "zz.json").write_text('{"zz.only.hu": "csak magyar"}\n', encoding="utf-8")
        (self.src / "i18n" / "en" / "zz.json").write_text('{}\n', encoding="utf-8")
        with self.assertRaises(bg.BuildError) as cm:
            bg.build(dev=False, src=self.src, fixtures=WEB / "fixtures")
        self.assertIn("kulcskészlet", str(cm.exception))

    def test_rejects_duplicate_i18n_key(self):
        (self.src / "i18n" / "hu" / "zz.json").write_text('{"app.name": "x"}\n', encoding="utf-8")
        (self.src / "i18n" / "en" / "zz.json").write_text('{"app.name": "x"}\n', encoding="utf-8")
        with self.assertRaises(bg.BuildError):
            bg.build(dev=False, src=self.src, fixtures=WEB / "fixtures")

    def test_rejects_template_without_nonce(self):
        t = (self.src / "index.template.html").read_text(encoding="utf-8")
        (self.src / "index.template.html").write_text(t.replace('<script nonce="{{CSP_NONCE}}">', "<script>"), encoding="utf-8")
        with self.assertRaises(bg.BuildError) as cm:
            bg.build(dev=False, src=self.src, fixtures=WEB / "fixtures")
        self.assertIn("nonce", str(cm.exception))

    def test_dev_block_stripped_in_prod(self):
        self._screen("/*<dev>*/ var devOnly = 'DEVMARKER'; /*</dev>*/ var y = 2;")
        prod = bg.build(dev=False, src=self.src, fixtures=WEB / "fixtures")
        self.assertNotIn("DEVMARKER", prod)
        dev = bg.build(dev=True, src=self.src, fixtures=WEB / "fixtures")
        self.assertIn("DEVMARKER", dev)


class DistUpToDateTests(unittest.TestCase):
    def test_dist_is_current(self):
        """A dist/index.html a forrásokból épített termék-build (build_gui.py --check)."""
        if not bg.PROD_OUT.is_file():
            self.skipTest("nincs dist/index.html — futtasd: python3 ma_gui/web/build_gui.py")
        self.assertEqual(bg.main(["--check"]), 0)


if __name__ == "__main__":
    sys.exit(unittest.main())
