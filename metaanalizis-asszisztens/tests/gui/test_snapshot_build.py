# -*- coding: utf-8 -*-
"""A felület-build pillanatkép-változata (build_gui.py --snapshot → web/dist/snapshot.html; terv 2.5, 7.6).

- determinisztikus, naprakész (a --check a dist/snapshot.html-t is ellenőrzi);
- nonce nincs benne; a CSP-meta és az adat helyőrzője pontosan egyszer; a snapshot-kliens benne van;
- a termék-build (dist/index.html) NEM tartalmazza a pillanatkép-klienst (/*<snapshot>*/ blokkok, src/snapshot/);
- a snapshot.py a tényleges sablonnal érvényes, hash-alapú CSP-t ad."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
WEB = os.path.join(ROOT, "ma_gui", "web")
for p in (ROOT, WEB):
    if p not in sys.path:
        sys.path.insert(0, p)

import build_gui as bg  # noqa: E402
from ma_gui import snapshot  # noqa: E402


class SnapshotBuildTests(unittest.TestCase):
    def _absent(self, needle, hay):
        self.assertTrue(needle not in hay, "nem szerepelhet: %r" % needle)

    def _present(self, needle, hay):
        self.assertTrue(needle in hay, "hiányzik: %r" % needle)

    @classmethod
    def setUpClass(cls):
        cls.snap = bg.build(variant="snapshot")
        cls.prod = bg.build(dev=False)

    def test_deterministic_and_current(self):
        self.assertEqual(self.snap, bg.build(variant="snapshot"))
        self.assertTrue(bg.SNAP_OUT.is_file(), "a dist/snapshot.html hiányzik — futtasd: python3 ma_gui/web/build_gui.py")
        self.assertEqual(bg.SNAP_OUT.read_text(encoding="utf-8"), self.snap,
                         "a dist/snapshot.html elavult — futtasd: python3 ma_gui/web/build_gui.py")

    def test_check_flag_covers_snapshot(self):
        r = subprocess.run([sys.executable, os.path.join(WEB, "build_gui.py"), "--check"], capture_output=True,
                           text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("snapshot.html", r.stdout)

    def test_template_shape(self):
        s = self.snap
        self.assertIsNone(re.search(r"<(?:script|style)\b[^>]*\bnonce=", s), "nonce-attribútum a pillanatkép-sablonban")
        self._absent("{{CSP_NONCE}}", s)
        self.assertEqual(s.count("{{SNAPSHOT_CSP}}"), 1)
        self.assertEqual(s.count("{{SNAPSHOT_DATA}}"), 1)
        self.assertRegex(s, r'<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" '
                            r'content="\{\{SNAPSHOT_CSP\}\}">')
        self._present('<script type="application/json" id="ma-snapshot">{{SNAPSHOT_DATA}}</script>', s)
        self._present("__snapSetTransport", s)
        self._present("szk.ma.snapshot/v1", s)
        self._absent("__devSetTransport", s)
        self._absent("ma-fixtures", s)
        self.assertLessEqual(len(s.encode("utf-8")), bg.SNAPSHOT_MAX_BYTES)
        self._present("mode: 'snapshot'", s)
        # a data-blokk a futó szkript előtt van (a kliens betöltéskor olvassa)
        self.assertLess(s.index('id="ma-snapshot"'), s.index("window.MA = {"))

    def test_prod_has_no_snapshot_client(self):
        self._absent("__snapSetTransport", self.prod)
        self._absent("szk.ma.snapshot/v1", self.prod)
        self._absent('id="ma-snapshot"', self.prod)
        self._absent("/*<snapshot>", self.prod)
        self._absent("snapshot/provider.js", self.prod)
        self._present("snapshot/provider.js", self.snap)
        dev = bg.build(dev=True)
        self._absent("__snapSetTransport", dev)

    def test_module_order(self):
        order = bg.js_order(bg.SRC, snapshot=True)
        self.assertEqual(order[:5], ["dom.js", "geom.js", "i18n.js", "store.js", "api.js"])
        self.assertEqual(order[5], "snapshot/provider.js")
        self.assertNotIn("snapshot/provider.js", bg.js_order(bg.SRC))
        self.assertNotIn("snapshot/provider.js", bg.js_order(bg.SRC, dev=True))

    def test_snapshot_marker_balance_is_checked(self):
        with self.assertRaises(bg.BuildError):
            bg.strip_snapshot("a /*<snapshot>*/ b", "x.js")
        self.assertEqual(bg.strip_snapshot("a/*<snapshot>*/b/*</snapshot>*/c", "x.js"), "ac")

    def test_render_with_real_template(self):
        html = snapshot.render(self.snap, {"schema": snapshot.SCHEMA, "routes": {}})
        self.assertTrue("{{" not in html, "kitöltetlen helyőrző")
        csp = re.search(r'content="(default-src[^"]+)"', html).group(1)
        scripts, styles = snapshot.inline_hashes(html)
        self.assertEqual(len(scripts), 1)
        self.assertEqual(len(styles), 1)
        self.assertIn("script-src %s;" % scripts[0], csp)
        self.assertIn("style-src %s;" % styles[0], csp)
        self.assertIn("connect-src 'none'", csp)


class SnapshotBuildLintTests(unittest.TestCase):
    """A lint a src/snapshot/ modulokra is fut (egy ideiglenes forrásmásolaton)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ma_gui_snapbuild_"))
        self.src = self.tmp / "src"
        shutil.copytree(str(bg.SRC), str(self.src))

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_snapshot_module_is_linted(self):
        (self.src / "snapshot" / "zz.js").write_text(
            "(function () {\n  'use strict';\n  window.fetch('/x');\n})();\n", encoding="utf-8")
        with self.assertRaises(bg.BuildError) as cm:
            bg.build(variant="snapshot", src=self.src)
        self.assertIn("hálózat", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
