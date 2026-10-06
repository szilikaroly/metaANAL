# -*- coding: utf-8 -*-
"""FID-1 (v1 harmadik átnézés): az ábra számhűség-ellenőrzése (6.7) minden számot hordozó szöveget a motor saját
szövegéhez köt — a SAJÁT sorában (két vizsgálat hatásának felcserélése eltérés), a heterogenitás-sort, a kumulatív
és a LOO-sorok k / I² / τ² oszlopait és a bubble-ábra együttható-sorát is; a motor szövegeiből nem magyarázható
szám (pl. átírt R²) szintén eltérés. A motor saját (érintetlen) ábrái mindkét nyelven, rétegekkel és anélkül zöldek."""
import contextlib
import io
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_routes_harness as H  # noqa: E402
from metaelemzes import api  # noqa: E402
from ma_gui.adapters import svgaudit as A  # noqa: E402

KINDS = ("forest", "funnel", "doi", "cumulative", "loo", "bubble")


def _series_doc():
    """A motor v2 ábra-dokumentuma kumulatív sorozattal és meta-regresszióval (BCG, szélesség) — a motor maga."""
    from metaelemzes import pipeline, tableio
    rows, meta = tableio.read_table(H.BCG)
    out, es = pipeline.run(rows, {"measure": "RR", "cumulative": "év", "moderators": ["szélesség"]}, meta)
    return pipeline.plot_document(out, es)


class NumberFidelityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from metaelemzes import pipeline
        cls.tmp = H.tmpdir("ma_r3_fig_")
        cls.proj, _home = H.make_project(cls.tmp)
        os.makedirs(os.path.join(cls.proj, "05_elemzes", "specs"), exist_ok=True)
        path = os.path.join(cls.proj, "05_elemzes", "specs", "o1_primary.json")
        api.save_spec(path, H.spec())
        with contextlib.redirect_stdout(io.StringIO()):
            view = api.analyze(path, mode="commit", project_root=cls.proj)
        run = view["run"]
        cls.run_dir = os.path.join(cls.proj, os.path.dirname(run["files"]["plot"]["path"]))
        with open(os.path.join(cls.proj, run["files"]["plot"]["path"]), encoding="utf-8") as fh:
            cls.plot = json.load(fh)
        cls.series = _series_doc()
        cls.svg = {}
        for lang in ("hu", "en"):
            for ann in (False, True):
                for kind in ("forest", "funnel", "doi"):
                    out = api.render_figure(cls.plot, kind, lang=lang, annotate=ann, run_dir=cls.run_dir,
                                            project_root=cls.proj)
                    cls.svg[(kind, lang, ann)] = out["svg"]
                for kind in ("cumulative", "loo", "bubble"):
                    cls.svg[(kind, lang, ann)] = pipeline.render_figure(cls.series, kind, lang, ann)["svg"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def doc(self, kind):
        return self.plot if kind in ("forest", "funnel", "doi") else self.series

    def check(self, svg, kind):
        return A.numbers_check_svg(svg, self.doc(kind), kind)

    def test_pristine_engine_figures_pass(self):
        self.assertEqual(len(self.svg), 24)
        for (kind, lang, ann), svg in sorted(self.svg.items()):
            res = self.check(svg, kind)
            self.assertTrue(res["ok"], (kind, lang, ann, res["mismatches"][:5]))
            self.assertEqual(res["unexplained"], 0)
            self.assertGreater(res["checked"], 3, kind)

    def test_heterogeneity_line_checked(self):
        het = self.plot["heterogeneity"]["text"]["en"]
        q = "Q = %s" % self.plot["heterogeneity"]["q_text"]["en"].split(" ")[0]
        for ann in (False, True):
            svg = self.svg[("forest", "en", ann)]
            self.assertIn(het.replace("<", "&lt;"), svg)
            bad = svg.replace(q, "Q = 1.23")
            self.assertNotEqual(bad, svg)
            res = self.check(bad, "forest")
            self.assertFalse(res["ok"])
            fields = [m["field"] for m in res["mismatches"]]
            self.assertIn("heterogeneity.text", fields)
            self.assertIn("svg.text", fields)

    def test_swapped_rows_caught(self):
        a = self.plot["studies"][0]["display_text"]["en"]
        b = next(s["display_text"]["en"] for s in self.plot["studies"][1:] if s["display_text"]["en"] != a)
        for ann in (False, True):
            svg = self.svg[("forest", "en", ann)]
            bad = svg.replace(">%s<" % a, ">@@<").replace(">%s<" % b, ">%s<" % a).replace(">@@<", ">%s<" % b)
            self.assertNotEqual(bad, svg)
            res = self.check(bad, "forest")
            self.assertFalse(res["ok"], ann)
            self.assertIn("studies[0].display_text", [m["field"] for m in res["mismatches"]])

    def test_cumulative_and_loo_columns(self):
        e = next(x for x in self.series["cumulative"]["entries"] if x["i2_text"]["en"] not in ("–", "-"))
        t2 = self.series["loo"][0]["tau2_text"]["en"]
        for ann in (False, True):
            svg = self.svg[("cumulative", "en", ann)]
            i2 = e["i2_text"]["en"]
            bad = svg.replace(">%s<" % i2, ">%s<" % ("1" + i2), 1)
            self.assertNotEqual(bad, svg)
            res = self.check(bad, "cumulative")
            self.assertFalse(res["ok"])
            self.assertTrue(any(m["field"].endswith(".i2_text") for m in res["mismatches"]))
            svg = self.svg[("loo", "en", ann)]
            bad = svg.replace(">%s<" % t2, ">9%s<" % t2, 1)
            self.assertNotEqual(bad, svg)
            res = self.check(bad, "loo")
            self.assertFalse(res["ok"])
            self.assertIn("loo[0].tau2_text", [m["field"] for m in res["mismatches"]])

    def test_bubble_coefficient_line(self):
        for ann in (False, True):
            svg = self.svg[("bubble", "en", ann)]
            line = next(t for t in A.text_elements(A.parse_svg(svg)) if "R²" in t)
            bad = svg.replace(line.replace("<", "&lt;"), line.replace("R² = ", "R² = 1").replace("<", "&lt;"))
            self.assertNotEqual(bad, svg)
            res = self.check(bad, "bubble")
            self.assertFalse(res["ok"])
            self.assertIn("bubble.coef_text", [m["field"] for m in res["mismatches"]])

    def test_unexplained_number_caught(self):
        svg = self.svg[("funnel", "en", True)]
        bad = svg.replace("</svg>", '<text x="1" y="1">p = 0.0001</text></svg>')
        res = self.check(bad, "funnel")
        self.assertFalse(res["ok"])
        self.assertEqual(res["unexplained"], 1)
        self.assertEqual(res["mismatches"][-1], {"field": "svg.text", "expected": None, "found": "p = 0.0001"})


class PolarityGuardTableTests(unittest.TestCase):
    """FID-5: a H12-tételek a motor definíciójából is (validator_differences), és a ROBINS-I 1.3 / 2.1 benne van."""

    def test_table(self):
        from ma_gui.adapters import validator as V
        for tool, want in (("robins-i", {"1.3", "2.1", "6.3"}), ("quadas2", {"1.2", "1.3"}),
                           ("robins-e", {"2.3", "5.2", "6.2"})):
            inst = api.instrument_get(tool)
            self.assertTrue(want <= set(V.polarity_differs(tool, inst)), tool)
        extra = {"validator_differences": [{"item": "9.9", "validator": "reverse", "here": "router"},
                                           {"item": "8.8", "validator": "If Y", "here": "If Y/NI"}]}
        got = V.polarity_differs("rob2", extra)
        self.assertIn("9.9", got)
        self.assertNotIn("8.8", got)


if __name__ == "__main__":
    unittest.main()
