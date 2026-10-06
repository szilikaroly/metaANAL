# -*- coding: utf-8 -*-
"""A kettős kinyerés és a v1-ábrák fixture-jeinek (ma_gui/web/fixtures/extraction_{dual,plots}.json) sodródás-őre
(terv 4.20/5): a fájlok a tests/gui/ui/gen_extraction_fixtures.py kimenetei a munkapad VALÓDI szerveréből és a VALÓDI
motorból; ha a szerver, a motor vagy a generátor változott, futtasd: python3 tests/gui/ui/gen_extraction_fixtures.py.
Az alakokat a szerződések (szk.ma.compare-result/v1, szk.ma.plot/v2) és a szerver nézet-alakja (szk.ma.dual-view/v1)
szerint is ellenőrzi."""
import importlib.util
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(GUI))
for p in (ROOT, GUI, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
FX = os.path.join(ROOT, "ma_gui", "web", "fixtures")


def _gen():
    spec = importlib.util.spec_from_file_location("gen_extraction_fixtures", os.path.join(HERE, "gen_extraction_fixtures.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load(name):
    with open(os.path.join(FX, name), encoding="utf-8") as fh:
        return json.load(fh)


class ExtractionFixtureTests(unittest.TestCase):
    def test_generated_and_current(self):
        self.assertEqual(_gen().main(["--check"]), 0,
                         "az extraction_*.json elsodródott — futtasd: python3 tests/gui/ui/gen_extraction_fixtures.py")

    def test_dual_shapes_follow_contracts(self):
        import test_routes_harness as H
        doc = _load("extraction_dual.json")
        views = [r for r in doc["routes"] if r["path"] == "/api/compare" and r.get("status", 200) == 200]
        self.assertEqual(sorted(r["body"]["outcome"] for r in views), ["o1", "o2", "o3"])
        kinds = set()
        for r in views:
            d = r["envelope"]["data"]
            H.check_contract(self, d["compare"], "szk.ma.compare-result/v1")
            for k in ("outcome", "compare", "items", "progress", "gate", "consensus", "a", "b", "columns", "pairs"):
                self.assertIn(k, d)
            kinds |= {it["kind"] for it in d["items"]}
            self.assertTrue(r["etag"].startswith('"'))
        self.assertTrue({"value", "format_only", "category", "missing_b", "only_a", "only_b"} <= kinds, kinds)
        miss = [r for r in doc["routes"] if r.get("status") == 424][0]["envelope"]
        self.assertEqual(miss["error"]["code"], "CAPABILITY_MISSING")
        self.assertIn("metaelemzes.api.compare", miss["error"]["message"])
        lst = [r for r in doc["routes"] if r["path"] == "/api/kettos"][0]["envelope"]["data"]
        o3 = [o for o in lst["outcomes"] if o["id"] == "o3"][0]
        self.assertTrue(o3["a"]["exists"] and not o3["b"]["exists"])
        self.assertEqual([f["name"] for f in lst["inbox"]], ["o3.B.csv"])

    def test_plots_follow_plot_v2(self):
        import test_routes_harness as H
        doc = _load("extraction_plots.json")
        for r in doc["routes"]:
            p = r["envelope"]["data"]
            H.check_contract(self, p, "szk.ma.plot/v2")
            b = p["bubble"]
            for k in ("moderator", "points", "line", "band", "x_axis", "y_axis"):
                self.assertIn(k, b)
            if b["moderator"]["type"] == "continuous":
                self.assertTrue(p["cumulative"]["entries"], "a kumulatív blokk a motoré")
                self.assertEqual(len(b["line"]), len(b["band"]))
                for (x, y), (x2, lo, hi) in zip(b["line"], b["band"]):
                    self.assertEqual(x, x2)
                    self.assertLess(lo, y)
                    self.assertLess(y, hi)
            else:
                self.assertEqual(b["line"], [])
                self.assertEqual(len(b["groups"]), len(b["x_axis"]["ticks"]))


if __name__ == "__main__":
    unittest.main()
