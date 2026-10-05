#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Az elemzés-képernyők statikus tesztjei (böngésző nélkül): a termék-build tömörítése (minify_js)
biztonságos, és az elemzés-fixture-ök a szerződés (szk.ma.plot/v2, szk.ma.run/v1, szk.ma.analysis-spec/v1)
szerinti, egymással összhangban lévő borítékok.

    python3 tests/gui/ui/test_analysis_static.py
"""
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
FX = WEB / "fixtures"
sys.path.insert(0, str(WEB))
import build_gui as bg  # noqa: E402

ROW_UID = re.compile(r"^r[0-9a-z]{4,12}$")
I18N_KEYS = {"hu", "en"}


def envelopes(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))["routes"]


def is_i18n(v):
    return isinstance(v, dict) and set(v) >= I18N_KEYS and all(isinstance(v[k], str) for k in I18N_KEYS)


class CompactTests(unittest.TestCase):
    """A termék-build tömörítése (build_gui.minify_js) — a részletes tesztek: tests/gui/ui/test_minify.py."""

    def test_minify_keeps_code_drops_comments(self):
        src = "/* fej\n * több sor */\n(function () {\n  'use strict';\n  // megjegyzés\n  var a = 1; // sorvégi\n  /* egysoros */ var b = '/* nem megjegyzés */';\n\n  return a\n    * b;\n})();\n"
        out = bg.minify_js(src)
        self.assertNotIn("fej", out)
        self.assertNotIn("megjegyzés\n", out)
        self.assertNotIn("sorvégi", out)
        self.assertIn("var b='/* nem megjegyzés */';", out)
        self.assertIn("return a*b;", out)                    # sor eleji szorzás nem megjegyzés

    @unittest.skipIf(shutil.which("node") is None, "node nincs telepítve")
    def test_compacted_product_script_parses(self):
        html = bg.build(dev=False)
        scripts = re.findall(r'<script nonce="\{\{CSP_NONCE\}\}">(.*?)</script>', html, re.S)
        self.assertTrue(scripts)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(scripts[-1])
        r = subprocess.run(["node", "--check", fh.name], capture_output=True, text=True)
        Path(fh.name).unlink()
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("plots/forest.js", "screens/plan.js", "screens/results.js"):
            self.assertIn("/* --- %s --- */" % name, html)


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plots = {r["path"]: r["envelope"]["data"] for r in envelopes("analysis_plots.json")}
        cls.engine = json.loads((FX / "engine.json").read_text(encoding="utf-8"))["routes"][0]["envelope"]["data"]

    def test_plot_v2_contract(self):
        self.assertGreaterEqual(len(self.plots), 7)
        for path, p in self.plots.items():
            with self.subTest(path=path):
                self.assertEqual(p["schema"], "szk.ma.plot/v2")
                for key in ("meta", "measure", "scale", "level", "axis", "studies", "summaries"):
                    self.assertIn(key, p)
                d0, d1 = p["axis"]["domain"]
                self.assertTrue(all(d0 <= t["at"] <= d1 and isinstance(t["text"], str) for t in p["axis"]["ticks"]))
                for s in p["studies"]:
                    self.assertRegex(s["row_uid"], ROW_UID)
                    self.assertTrue(is_i18n(s["display_text"]) and s["lo"] <= s["y"] <= s["hi"])
                    self.assertEqual(s["clip"], {"left": s["lo"] < d0, "right": s["hi"] > d1})
                uids = {s["row_uid"] for s in p["studies"]}
                self.assertEqual(len(uids), len(p["studies"]), "row_uid egyedi")
                for sec in p.get("sections") or []:
                    self.assertTrue(set(sec["row_uids"]) <= uids)
                self.assertTrue(all(is_i18n(sm["display_text"]) for sm in p["summaries"]))
                fu = p["funnel"]
                self.assertEqual(sorted(c["p"] for c in fu["contours"]), [0.01, 0.05, 0.1])
                self.assertTrue({x["row_uid"] for x in fu["points"]} == uids)
                self.assertTrue({x["omitted_row_uid"] for x in p["loo"]} == uids)
                self.assertTrue({x["row_uid"] for x in p["influence"]} == uids)
                self.assertTrue(all(is_i18n(e["rstudent_text"]) for e in p["influence"]))

    def test_primary_numbers(self):
        p = self.plots["/api/runs/20261004T211200Z-a1f3c2/plot"]
        prim = [s for s in p["summaries"] if s.get("primary")][0]
        # a motor kész szövegei (4.0: tizedespont mindkét nyelven, mint a report.md és az SVG; en: U+2212)
        self.assertEqual(prim["display_text"], {"hu": "0.49 [0.33; 0.73]", "en": "0.49 [0.33; 0.73]"})
        self.assertEqual(prim["pi_text"]["hu"], "[0.13; 1.79]")
        self.assertEqual(len(p["studies"]), 13)
        self.assertEqual(p["subgroup_test"]["text"]["hu"], "Alcsoport-különbség: Q_b = 1.86 (df = 2, p = 0.394)")
        self.assertIn("−", p["doi"]["lfk_text"]["en"])
        self.assertEqual(p["doi"]["lfk_text"]["hu"], p["doi"]["lfk_text"]["en"].replace("−", "-").replace(
            "LFK index", "LFK-index").replace("major asymmetry", "jelentős aszimmetria"))

    def test_runs_reference_existing_plots_and_specs(self):
        runs = [r for r in envelopes("analysis_runs.json") if r.get("query") == {"outcome": "o1"}][0]["envelope"]["data"]["runs"]
        self.assertTrue(any(r["stale"] for r in runs) and any(not r["stale"] for r in runs))
        for r in runs:
            self.assertEqual(r["schema"], "szk.ma.run/v1")
            self.assertIn("/api/runs/%s/plot" % r["run_id"], self.plots)
            self.assertEqual(r["equivalent_argv"][:3], ["ma.py", "analyze", "--spec"])
        specs = {r["path"]: r["envelope"] for r in envelopes("analysis_specs.json")}
        spec = specs["/api/specs/o1_primary"]["data"]
        self.assertEqual(spec["schema"], "szk.ma.analysis-spec/v1")
        self.assertEqual(set(spec["options"]), set(self.engine["options"]), "options = a motor DEFAULTS-kulcsai")
        for k, v in spec["options"].items():
            m = self.engine["options"][k]
            if m["type"] == "enum" and v is not None:
                self.assertIn(v, m["choices"], k)


if __name__ == "__main__":
    unittest.main(verbosity=1)
