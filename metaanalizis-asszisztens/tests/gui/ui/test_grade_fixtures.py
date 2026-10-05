# -*- coding: utf-8 -*-
"""A GRADE / SoF / Protokoll fixture-ök (ma_gui/web/fixtures/grade.json) sodródás-őre (terv 4.20/5): a fájl a
tests/gui/ui/gen_grade_fixtures.py kimenete a VALÓDI motorból (grade_help.advice / sof); ha a motor vagy a generátor
változott, futtasd: python3 tests/gui/ui/gen_grade_fixtures.py. A fixture-ök alakja a szerver végpontjaié
(szk.ma.grade-view/v1, szk.ma.grade-advice-view/v1, szk.ma.sof-view/v1, szk.ma.protocol/v1)."""
import importlib.util
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "ma_gui", "web", "fixtures", "grade.json")


def _gen():
    spec = importlib.util.spec_from_file_location("gen_grade_fixtures", os.path.join(HERE, "gen_grade_fixtures.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GradeFixtureTests(unittest.TestCase):
    def setUp(self):
        with open(FIX, encoding="utf-8") as fh:
            self.doc = json.load(fh)

    def data(self, path):
        return [r for r in self.doc["routes"] if r["path"] == path][0]["envelope"]["data"]

    def test_generated_from_engine_and_current(self):
        try:
            gen = _gen()
        except ImportError as exc:                          # pragma: no cover — motor-modul nélkül
            self.skipTest("a motor grade_help modulja nem tölthető: %s" % exc)
        self.assertEqual(gen.main(["--check"]), 0,
                         "a grade.json elsodródott — futtasd: python3 tests/gui/ui/gen_grade_fixtures.py")

    def test_shapes_follow_the_routes(self):
        from ma_gui.routes import grade as grade_route
        v = self.data("/api/grade/o1")
        self.assertEqual(v["vocab"], grade_route.vocab(), "a szókincs a szerveré")
        self.assertIsNone(v["grade"])
        adv = self.data("/api/grade/o1/advice")["advice"]
        self.assertEqual(adv["schema"], "szk.ma.grade/v1")
        for d in grade_route.DOMAINS:
            self.assertIsNone(adv["domains"][d]["rating"], "a motor-tanács soha nem végleges")
            sug = adv["domains"][d]["suggestion"]
            self.assertEqual(set(sug["summary"]), {"hu", "en"})
            self.assertTrue(sug["kb_refs"])
        sof = self.data("/api/sof/o1")["preview"]
        self.assertEqual(sof["schema"], "szk.ma.sof/v1")
        keys = [c["key"] for c in sof["columns"]]
        for k in keys:
            self.assertEqual(set(sof["rows"][0][k]), {"hu", "en"}, k)
        p = self.data("/api/protocol")
        self.assertEqual(set(p["question"]), {"P", "I", "C", "O"})


if __name__ == "__main__":
    unittest.main()
