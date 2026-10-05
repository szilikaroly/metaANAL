# -*- coding: utf-8 -*-
"""A MA-munkapad felületének statikus tesztjei — böngésző nélkül (terv 6.8, 7.1 T4/T6/T9, 7.6, 8.6).

A termék-buildet (ma_gui/web/dist/index.html — ezt szolgálja ki a szerver a GET /-re; a ma_gui/static/index.html
csak tartalék oldal, ha a build hiányzik) és a
forrásokat (ma_gui/web/src/**) ellenőrzi:

  • a dist naprakész (a forrásokból újraépítve bájtra azonos), ≤ 900 KB (build_gui.MAX_BYTES), determinisztikus;
  • nincs benne fixture / fejlesztői kód / fixture-token;
  • nincs külső URL (az SVG-névtér az egyetlen kivétel), külső szkript, stíluslap vagy url();
  • nincs innerHTML / outerHTML / insertAdjacentHTML / document.write / eval / new Function / string-időzítő;
  • nincs számformázás (toFixed / toPrecision / Intl.NumberFormat / toLocaleString), Math.* csak a geom.js-ben;
  • böngészőtároló: localStorage csak 'mag.pref.*' (MA.prefs, store.js), sessionStorage csak a token
    (api.js); nincs IndexedDB / Cache API / süti;
  • minden <script> és <style> a {{CSP_NONCE}} helyőrzőt viszi, nincs inline eseménykezelő / style-attribútum;
  • minden használt i18n-kulcs megvan mindkét nyelven, a beágyazott szótár = a forrás-szótár;
  • ESLint (ma_gui/web/eslint.config.js) tiszta — ha van node és ESLint.

    python3 tests/gui/test_ui_static.py
"""
import importlib.util
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "ma_gui" / "web"
SRC = WEB / "src"
DIST = WEB / "dist" / "index.html"
NONCE = "{{CSP_NONCE}}"


def _load_build():
    spec = importlib.util.spec_from_file_location("ma_gui_build_gui_static", str(WEB / "build_gui.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bg = _load_build()

BLOCK_RE = re.compile(r"<(script|style)\b([^>]*)>(.*?)</\1\s*>", re.S | re.I)
MARKER_RE = re.compile(r"/\* --- ([^ ]+) --- \*/\n")
BANNED_DOM = r"innerHTML|outerHTML|insertAdjacentHTML|createContextualFragment|document\.write|DOMParser"
BANNED_EXEC = r"\beval\s*\(|\bnew\s+Function\s*\(|\bFunction\s*\(\s*['\"]|set(?:Timeout|Interval)\s*\(\s*['\"`]"
BANNED_NUMFMT = r"toFixed|toPrecision|Intl\.NumberFormat|toLocaleString"
I18N_KEY_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+$")


def js_modules(script):
    """A termék-szkript modulokra bontva a build fejlécjelölői (/* --- név --- */) mentén: {név: szöveg}."""
    parts = MARKER_RE.split(script)
    mods = {}
    for k in range(1, len(parts) - 1, 2):
        mods[parts[k]] = parts[k + 1]
    return mods


def product_sources():
    """(relatív név, forrásszöveg dev-blokkok nélkül) a termék-build moduljaira."""
    return [(name, bg.strip_dev(bg.read_text(SRC / name), name)) for name in bg.js_order(SRC, dev=False)]


class DistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DIST.is_file():
            raise unittest.SkipTest("nincs ma_gui/web/dist/index.html — futtasd: python3 ma_gui/web/build_gui.py")
        cls.html = DIST.read_text(encoding="utf-8")
        cls.blocks = [(m.group(1).lower(), m.group(2), m.group(3)) for m in BLOCK_RE.finditer(cls.html)]
        scripts = [b for b in cls.blocks if b[0] == "script" and "type=" not in b[1]]
        assert len(scripts) == 1, "pontosan egy végrehajtható <script> várható"
        cls.script = scripts[0][2]
        cls.css = "".join(b[2] for b in cls.blocks if b[0] == "style")
        cls.mods = js_modules(cls.script)
        cls.markup = BLOCK_RE.sub("", cls.html)

    def test_dist_is_current_and_deterministic(self):
        fresh = bg.build(dev=False)
        self.assertEqual(fresh, bg.build(dev=False), "két egymás utáni build bájtra azonos")
        self.assertEqual(self.html, fresh, "a dist/index.html elavult — futtasd: python3 ma_gui/web/build_gui.py")

    def test_size_budget(self):
        self.assertLessEqual(len(self.html.encode("utf-8")), 900 * 1024)
        self.assertEqual(bg.MAX_BYTES, 900 * 1024, "a keret a terv 2.3 szerinti 900 KB")

    def test_no_fixtures_or_dev_code(self):
        low = self.html.lower()
        for bad in ("fixture", "ma-fixtures", "ma.dev", "__devsettransport", "/*<dev>", "fx-token", "selftest_fixture"):
            self.assertNotIn(bad, low)
        self.assertNotIn("dev/", " ".join(self.mods), "dev/*.js modul nem kerülhet a termékbe")
        self.assertTrue(set(self.mods) >= {"dom.js", "geom.js", "i18n.js", "store.js", "api.js", "app.js", "boot.js"})

    def test_no_external_urls_or_resources(self):
        urls = set(re.findall(r"(?i)\b(?:https?|ftp|wss?):\/\/[^\s\"'<>)]*", self.html))
        self.assertEqual(urls, {bg.SVG_NS})
        self.assertIsNone(re.search(r"(?i)<script\b[^>]*\bsrc\s*=", self.html))
        links = re.findall(r"(?i)<link\b[^>]*>", self.markup)
        self.assertEqual(links, ['<link rel="icon" href="data:,">'])
        self.assertIsNone(re.search(r"(?i)url\s*\(|@import", self.css))
        self.assertIsNone(re.search(r"(?i)<(?:iframe|object|embed|base|form)\b", self.markup))

    def test_every_script_and_style_carries_the_nonce(self):
        self.assertEqual(len(self.blocks), 3)                  # <style>, i18n-adat, <script>
        for tag, attrs, _ in self.blocks:
            self.assertIn('nonce="%s"' % NONCE, attrs, "<%s%s>" % (tag, attrs))
        self.assertEqual(self.html.count(NONCE), 3)
        self.assertIsNone(re.search(r"(?i)<(?:script|style)\b", self.markup), "lezáratlan <script>/<style>")
        self.assertEqual(set(re.findall(r"\{\{([A-Z_]+)\}\}", self.html)), {"CSP_NONCE"})

    def test_markup_has_no_inline_handlers_or_style_attributes(self):
        self.assertIsNone(re.search(r"(?i)<[^>]+\son[a-z]+\s*=", self.markup))
        self.assertIsNone(re.search(r"(?i)<[^>]+\sstyle\s*=", self.markup))

    def test_no_html_string_injection_or_eval(self):
        self.assertIsNone(re.search(BANNED_DOM, self.script))
        self.assertIsNone(re.search(BANNED_EXEC, self.script))
        self.assertIsNone(re.search(r"(?i)javascript:", self.html))

    def test_no_number_formatting_and_math_only_in_geom(self):
        self.assertIsNone(re.search(BANNED_NUMFMT, self.script))
        for name, text in self.mods.items():
            if name != "geom.js":
                self.assertIsNone(re.search(r"\bMath\.", text), name)
        self.assertIn("Math.", self.mods["geom.js"])

    def test_browser_storage_rules(self):
        for name, text in self.mods.items():
            if re.search(r"\blocalStorage\b", text):
                self.assertIn(name, ("store.js", "selftest.js"), name)
            if re.search(r"\bsessionStorage\b", text):
                self.assertIn(name, ("api.js", "selftest.js"), name)
            self.assertIsNone(re.search(r"indexedDB|openDatabase|caches\.|serviceWorker|document\.cookie\s*=(?!=)", text), name)
            for m in re.finditer(r"\.(setItem|getItem|removeItem)\(([^,)]*)", text):
                arg = m.group(2).strip()
                if name == "store.js":
                    self.assertTrue(arg.startswith("PREF_PREFIX+"), "store.js: %s(%s)" % (m.group(1), arg))
                elif name == "api.js":
                    self.assertEqual(arg, "TOKEN_KEY", "api.js: %s(%s)" % (m.group(1), arg))
                else:
                    self.fail("%s: böngészőtároló-hívás a store.js / api.js-en kívül: %s" % (name, m.group(0)))
        self.assertIn("PREF_PREFIX='mag.pref.'", self.mods["store.js"].replace(" ", ""))
        self.assertIn("TOKEN_KEY='mag.token'", self.mods["api.js"].replace(" ", ""))

    def test_embedded_i18n_equals_sources(self):
        m = re.search(r'<script type="text/plain" id="ma-i18n" data-enc="lz1" nonce="\{\{CSP_NONCE\}\}">(.*?)</script>',
                      self.html, re.S)
        self.assertIsNotNone(m)
        data = bg.unpack_i18n(m.group(1))
        self.assertEqual(data, bg.load_i18n(SRC))
        self.assertEqual(set(data["hu"]), set(data["en"]))


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.i18n = bg.load_i18n(SRC)
        cls.sources = product_sources()

    def test_banned_apis_absent_in_sources(self):
        for name, text in self.sources:
            for pat in (BANNED_DOM, BANNED_EXEC, BANNED_NUMFMT):
                self.assertIsNone(re.search(pat, text), "%s: %s" % (name, pat))

    def test_math_only_in_geom(self):
        for name, text in self.sources:
            toks = bg._js_tokens(text)
            uses = [k for k in range(len(toks) - 1) if toks[k][:2] == ("word", "Math") and toks[k + 1][1] == "."]
            if name == "geom.js":
                self.assertTrue(uses)
            else:
                self.assertEqual(uses, [], name)

    def test_i18n_parity(self):
        hu, en = self.i18n["hu"], self.i18n["en"]
        self.assertEqual(set(hu), set(en))
        for k in hu:
            self.assertEqual(set(re.findall(r"\{(\w+)\}", hu[k])), set(re.findall(r"\{(\w+)\}", en[k])), k)
            self.assertTrue(hu[k].strip() and en[k].strip(), "üres szöveg: %s" % k)

    # UX-5: a magyar felület egységesen tegező („Add meg”, „döntsd el”, „a te ítéleted”) — a magázó alakok tilosak
    FORMAL_HU = re.compile(r"(?<![\wÁÉÍÓÖŐÚÜŰáéíóöőúüű])Ön(?:nek|t|é|ök|nél|re|ről|höz|től|hez)?(?![\wÁÉÍÓÖŐÚÜŰáéíóöőúüű])"
                           r"|erősítse meg|írja át|[Kk]attintson|[Kk]érjük|[Tt]öltse ki|[Vv]álassza ki|[Ee]llenőrizze"
                           r"|[Dd]öntse el|[Ii]ndokolja meg|[Nn]yissa meg|[Ff]uttassa|[Hh]asználja a|[Aa]dja meg a")

    def test_hungarian_register_is_informal(self):
        bad = ["%s: %s" % (k, v) for k, v in sorted(self.i18n["hu"].items()) if self.FORMAL_HU.search(v)]
        self.assertEqual(bad, [], "magázó alak a magyar felületen (UX-5) — írd át tegezőre")
        for probe in ("Az Ön ítélete", "erősítse meg vagy írja át", "Kérjük, kattintson"):
            self.assertIsNotNone(self.FORMAL_HU.search(probe), probe)
        for ok in ("A te ítéleted", "erősítsd meg vagy írd át", "önálló", "Önállóan", "a GRADE adja meg"):
            self.assertIsNone(self.FORMAL_HU.search(ok), ok)

    def test_all_used_i18n_keys_exist_in_both_languages(self):
        """Minden kulcs-alakú sztringliterál, amely i18n-névtérrel kezdődik és nem állapot-/preferencia-út
        (MA.store / MA.prefs első argumentuma), mindkét szótárban megvan; a t(…)-ben álló dinamikus előtag
        (t('err.' + kód)) legalább egy kulcsot fed; a t(…)/i18n:/…_key: helyeken álló kulcs bármely
        névtérből megvan."""
        hu, en = self.i18n["hu"], self.i18n["en"]
        namespaces = {k.split(".")[0] for k in hu}
        store_paths = set()
        literals = []                 # (név, kulcs, előző tokenek)
        for name, text in self.sources:
            toks = bg._js_tokens(text)
            for k, (kind, val, _) in enumerate(toks):
                if kind != "str":
                    continue
                s = val[1:-1]
                before = [t[1] for t in toks[max(0, k - 4):k]]
                if len(before) >= 4 and before[-1] == "(" and before[-3] == "." and before[-4] in ("store", "prefs"):
                    store_paths.add(s)
                    continue
                literals.append((name, s, before))
        missing, prefixes = [], []
        contextual = ("t", "has", "emptyState", "spinner", "i18n", "title_key", "label_key")
        for name, s, before in literals:
            if s in store_paths:
                continue
            in_ctx = len(before) >= 2 and ((before[-1] == "(" and before[-2] in contextual) or
                                           (before[-1] == ":" and before[-2] in contextual))
            if s.endswith("."):
                if in_ctx:
                    prefixes.append((name, s))         # t('err.' + kód) — dinamikus előtag
                continue
            if (in_ctx and I18N_KEY_SHAPE.match(s)) or (I18N_KEY_SHAPE.match(s) and s.split(".")[0] in namespaces):
                if s not in hu or s not in en:
                    missing.append("%s: %s" % (name, s))
        self.assertEqual(missing, [])
        for name, p in prefixes:
            self.assertTrue(any(k.startswith(p) for k in hu), "%s: a(z) %r előtag egyetlen kulcsot sem fed" % (name, p))

    @unittest.skipIf(shutil.which("node") is None, "node nincs telepítve")
    def test_eslint_clean(self):
        cands = [shutil.which("eslint"), "/opt/node22/lib/node_modules/eslint/bin/eslint.js"]
        eslint = next((c for c in cands if c and Path(c).exists()), None)
        if eslint is None:
            self.skipTest("ESLint nem található")
        cmd = (["node", eslint] if eslint.endswith(".js") else [eslint]) + ["-c", "eslint.config.js", "src"]
        r = subprocess.run(cmd, cwd=str(WEB), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class DevBuildTests(unittest.TestCase):
    def test_dev_build_has_fixture_mode_prod_does_not(self):
        dev = bg.build(dev=True)
        self.assertIn('id="ma-fixtures"', dev)
        self.assertIn("MA.__devSetTransport", dev)
        self.assertEqual(dev, bg.build(dev=True), "a dev-build is determinisztikus")
        self.assertEqual(dev.count('nonce="%s"' % NONCE), 4)


if __name__ == "__main__":
    unittest.main(verbosity=1)
