# -*- coding: utf-8 -*-
"""Motor ↔ felület összehangolás (MVP): szerződés-, konvenció- és fixture-őrök.

- Számkonvenció (terv 4.0, döntés 2026-10-05): a motor minden kész szövege a magyar változatban is tizedespontot
  használ (mint a report.md és az SVG); hu és en csak a mínuszjelben tér el. A cellába írt szám (cell_text) a cél
  tábla tizedesjelével készül.
- Az átváltás eredménye (szk.ma.convert-result/v1): assumptions / warnings {hu, en} objektumok.
- A plot/v2 minden olyan szöveget és tengelyt hordoz, amit az ábra- és eredmény-képernyők olvasnak (additív, v2).
- A futás-leíró (szk.ma.run/v1) az áttekintő mezőit is hordozza (participants_text, rob_high, expanded_argv).
- GET /api/table column_map-je: api.column_map / api.read_table = a validálási dokumentum column_map-je.
- „Nem hiba — indoklás”: a döntés JSON-kontextusa a projektnaplóban (projekt context oszlop); a validate_table
  (project_dir) ebből jelöli az acknowledged-et (code, row_uid, fields).
- A felület fixture-jei a valódi motorból készülnek (tests/gui/ui/gen_fixtures_from_engine.py), és a sémáknak
  megfelelnek; a generátor --check-je zöld (nincs sodródás).
"""
import contextlib
import csv
import importlib.util
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest

from _helpers import ROOT
from metaelemzes import api, pipeline, projekt, report, spec as S, tableio
from metaelemzes.plots import MINUS

import test_mvp_contracts as C

PELDAK = os.path.join(ROOT, "peldak")
FX = os.path.join(ROOT, "ma_gui", "web", "fixtures")
GEN = os.path.join(ROOT, "tests", "gui", "ui", "gen_fixtures_from_engine.py")
WEB_SRC = os.path.join(ROOT, "ma_gui", "web", "src")
DECIMAL_COMMA = re.compile(r"\d,\d")

CASES = [
    ("bcg_oltas_RR.csv", {"measure": "RR", "subgroup": "allokáció", "cumulative": "év", "outliers": True}),
    ("bcg_oltas_RR.csv", {"measure": "OR", "model": "fixed"}),
    ("bcg_oltas_RR.csv", {"measure": "RD"}),
    ("normand1999_folytonos.csv", {"measure": "MD"}),
    ("normand1999_folytonos.csv", {"measure": "SMD", "model": "ivhet"}),
    ("molloy2014_korrelacio.csv", {"measure": "ZCOR"}),
    ("pritz1997_arany.csv", {"measure": "PFT"}),
    ("pritz1997_arany.csv", {"measure": "PLO"}),
]


def run_case(fn, opts, rows_slice=None):
    rows, meta = tableio.read_table(os.path.join(PELDAK, fn))
    use = rows if rows_slice is None else rows[rows_slice]
    with contextlib.redirect_stdout(io.StringIO()):
        out, es = pipeline.run(use, dict(opts), meta, table_rows=rows)
    return out, es


def plot_doc(out, es, provenance=None):
    return json.loads(json.dumps(pipeline.to_jsonable(pipeline.plot_document(out, es, provenance=provenance))))


def i18n_objects(obj, path="$"):
    """(út, {hu, en}) párok egy dokumentumban."""
    if isinstance(obj, dict):
        if set(obj) == {"hu", "en"} and all(isinstance(v, str) for v in obj.values()):
            yield path, obj
            return
        for k, v in obj.items():
            for x in i18n_objects(v, "%s.%s" % (path, k)):
                yield x
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            for x in i18n_objects(v, "%s[%d]" % (path, i)):
                yield x


# számszövegek: ezeknél a hu és az en csak a mínuszjelben térhet el
NUMERIC_KEYS = re.compile(r"(display_text|pi_text|p_text|i2_text|tau2_text|q_text|weight_text|_text_i18n|text_i18n|"
                          r"rstudent_text|dffits_text|cook_d_text|cov_ratio_text|hat_text|dfbetas_text|band_text|"
                          r"outputs_text\.[a-z_0-9]+|participants_text)$")


def load_fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as fh:
        return json.load(fh)


def ok_routes(name):
    return [r for r in load_fixture(name)["routes"] if (r.get("envelope") or {}).get("ok") is True]


def load_generator():
    spec = importlib.util.spec_from_file_location("gen_fixtures_from_engine", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------------------------- számkonvenció
class TestDecimalConvention(unittest.TestCase):
    """Egy számszöveg-konvenció a motor minden i18n-kimenetében (terv 4.0): tizedespont mindkét nyelven."""

    def assert_texts(self, doc, what, require=True):
        n = 0
        for path, obj in i18n_objects(doc):
            n += 1
            with self.subTest(what=what, at=path):
                self.assertIsNone(DECIMAL_COMMA.search(obj["hu"]), "tizedesvessző a hu szövegben: %r" % obj["hu"])
                self.assertIsNone(DECIMAL_COMMA.search(obj["en"]), "tizedesvessző az en szövegben: %r" % obj["en"])
                if NUMERIC_KEYS.search(path):
                    self.assertNotIn(MINUS, obj["hu"], "U+2212 a hu számszövegben (a hu kötőjel-mínuszt használ)")
                    self.assertEqual(obj["hu"], obj["en"].replace(MINUS, "-"),
                                     "a hu és az en számszöveg csak a mínuszjelben térhet el")
        if require:
            self.assertGreater(n, 0, what)
        return n

    def test_plot_documents(self):
        for fn, opts in CASES:
            out, es = run_case(fn, opts)
            self.assert_texts(plot_doc(out, es), "%s %s" % (fn, opts))

    def test_tick_text_follows_display_locale(self):
        out, es = run_case("normand1999_folytonos.csv", {"measure": "MD"})
        doc = plot_doc(out, es)
        self.assertEqual(doc["display_locale"], "hu")
        negatives = [t for t in doc["axis"]["ticks"] if t["at"] < 0]
        self.assertTrue(negatives, "az MD-tengelynek van negatív tickje")
        for t in doc["axis"]["ticks"]:
            self.assertEqual(t["text"], t["text_i18n"]["hu"])
            self.assertEqual(t["text_i18n"]["en"], t["text_i18n"]["hu"].replace("-", MINUS))

    def test_display_text_equals_svg_and_report_number(self):
        """A számhűség-lánc (6.7): a plot/v2 hu display_text-je szó szerint szerepel a forest SVG-ben és a riportban."""
        out, es = run_case("bcg_oltas_RR.csv", {"measure": "RR"})
        doc = plot_doc(out, es)
        prim = [s for s in doc["summaries"] if s.get("primary")][0]
        f_svg, _fu, _data = pipeline.make_plots(out, es)
        md = report.build_report(out, None, "2026-10-04")
        self.assertIn(prim["display_text"]["hu"], f_svg)
        self.assertIn(prim["display_text"]["hu"], md)
        self.assertEqual(prim["display_text"]["hu"], "0.49 [0.33; 0.73]")

    def test_run_descriptor_and_convert(self):
        out, es = run_case("bcg_oltas_RR.csv", {"measure": "RR"})
        self.assert_texts(S.run_descriptor(out, es=es), "run")
        res = api.convert({"schema": "szk.ma.convert-request/v1", "kind": "logor_to_d",
                           "inputs": {"y": "-0,5", "v": "0,04"}, "target": {"decimal_mark": ","}})
        self.assert_texts(res, "convert")
        self.assertEqual(res["outputs_text"]["y"]["hu"].replace("-", MINUS), res["outputs_text"]["y"]["en"])
        self.assertTrue(res["cell_text"]["y"].startswith("-0,"), "a cellába a tábla tizedesjelével")

    def test_fixtures_follow_the_convention(self):
        for name in ("analysis_plots.json", "analysis_runs.json", "runs.json", "analysis_jobs.json", "convert.json"):
            n = sum(self.assert_texts(r["envelope"]["data"], name, require=False) for r in ok_routes(name))
            self.assertGreater(n, 0, name)


# ------------------------------------------------------------------------------------------- átváltás
class TestConvertShape(unittest.TestCase):
    SAMPLES = {
        "median_to_mean_sd": {"n": "30", "median": "2", "q1": "1", "q3": "9"},
        "se_to_sd": {"se": "0,42", "n": "52"},
        "ci_to_sd": {"lower": "10,1", "upper": "14,3", "n": "48"},
        "ci_to_se": {"lower": "0,2", "upper": "0,8", "log": "igen"},
        "p_to_se": {"estimate": "0,49", "p": "0,001", "log": "igen"},
        "change_sd": {"sd_baseline": "5", "sd_final": "4", "corr": "0,5"},
        "split_shared_control": {"n": "100", "arms": "2", "events": "10"},
        "t_to_d": {"t": "2", "n1": "10", "n2": "12", "vtype": "LS2"},
        "d_to_r": {"y": "0,5", "v": "0,04", "n1": "20", "n2": "20"},
    }

    def test_assumptions_and_warnings_are_i18n(self):
        for kind, inputs in self.SAMPLES.items():
            res = api.convert({"schema": "szk.ma.convert-request/v1", "kind": kind, "inputs": inputs})
            with self.subTest(kind=kind):
                self.assertEqual(C.errors_for(res, "ma.convert-result", 1), [])
                for item in res["assumptions"] + res["warnings"]:
                    self.assertEqual(set(item), {"hu", "en"})
                    self.assertTrue(item["hu"].strip() and item["en"].strip())
        skew = api.convert({"schema": "szk.ma.convert-request/v1", "kind": "median_to_mean_sd",
                            "inputs": self.SAMPLES["median_to_mean_sd"]})
        self.assertTrue(any("V013" in w["hu"] and "V013" in w["en"] for w in skew["warnings"]))
        note = api.convert({"schema": "szk.ma.convert-request/v1", "kind": "t_to_d", "inputs": self.SAMPLES["t_to_d"]})
        self.assertTrue(any("Hedges" in w["en"] and w["en"] != w["hu"] for w in note["warnings"]),
                        "a CLI megjegyzése angolul is")

    def test_string_assumptions_rejected_by_contract(self):
        bad = {"schema": "szk.ma.convert-result/v1", "kind": "se_to_sd", "engine_version": "0.1.0",
               "outputs": {"sd": 2.1}, "estimated": False, "method": {"id": "sd_from_se"},
               "assumptions": ["közel normális eloszlás"]}
        self.assertTrue(C.errors_for(bad, "ma.convert-result", 1))


# ------------------------------------------------------------------------------------------- plot/v2 ↔ felület
# a felület ábra- és eredmény-képernyőinek olvasott mezői (ma_gui/web/src/plots/*.js, screens/results.js,
# screens/analysis_shared.js); (út, a JS-ben keresett kulcs)
UI_PLOT_FIELDS = [
    ("studies[].weight_text", "weight_text"), ("studies[].display_text", "display_text"), ("studies[].cells", "cells"),
    ("studies[].clip", "clip"), ("studies[].flags", "flags"), ("studies[].source", "source"),
    ("summaries[].primary", "primary"), ("summaries[].p_text", "p_text"), ("summaries[].pi_text", "pi_text"),
    ("summaries[].pi_label", "pi_label"), ("summaries[].het_text", "het_text"), ("summaries[].label", "label"),
    ("sections[].row_uids", "row_uids"), ("sections[].summary", "summary"), ("sections[].title", "title"),
    ("subgroup_test.text", "subgroup_test"), ("heterogeneity.text", "heterogeneity"),
    ("heterogeneity.i2_text", "i2_text"), ("heterogeneity.tau2_text", "tau2_text"),
    ("labels.study", "labels"), ("labels.effect", "effect"), ("labels.weight", "weight"),
    ("axis.title", "title"), ("axis.ticks[].text", "ticks"), ("scale.null_analysis", "null_analysis"),
    ("funnel.axis", "axis"), ("funnel.y_axis", "y_axis"), ("funnel.contours[].band_text", "band_text"),
    ("funnel.contours[].polygon", "polygon"), ("funnel.pseudo_ci", "pseudo_ci"),
    ("funnel.contour_center", "contour_center"), ("funnel.center_label", "center_label"),
    ("funnel.outside_text", "outside_text"), ("funnel.tests_text", "tests_text"),
    ("funnel.trimfill_text", "trimfill_text"), ("funnel.filled[].mirror_of", "mirror_of"),
    ("funnel.filled[].label", "label"),
    ("doi.points[].abs_z", "abs_z"), ("doi.lfk_text", "lfk_text"), ("doi.note", "note"), ("doi.axis", "axis"),
    ("doi.y_axis", "y_axis"),
    ("loo[].omitted_row_uid", "omitted_row_uid"), ("loo[].display_text", "display_text"), ("loo[].i2_text", "i2_text"),
    ("loo[].label", "label"), ("loo_axis", "loo_axis"),
    ("cumulative.entries[].added_row_uid", "added_row_uid"), ("cumulative.entries[].key_text", "key_text"),
    ("cumulative.entries[].i2_text", "i2_text"), ("cumulative.axis", "axis"), ("cumulative.key_label", "key_label"),
    ("influence[].rstudent_text", "_text"), ("influence[].cook_d_text", "_text"), ("influence[].hat_text", "_text"),
    ("influence[].influential", "influential"), ("influence[].outlier", "outlier"),
    ("influence_axes.rstudent.refs", "refs"), ("influence_text", "influence_text"),
    ("influence_note", "influence_note"), ("notes", "notes"),
]


def lookup(doc, path):
    """Az út értékei a dokumentumban ('a[].b' → minden elem b-je)."""
    cur = [doc]
    for part in path.split("."):
        nxt = []
        many = part.endswith("[]")
        key = part[:-2] if many else part
        for c in cur:
            if not isinstance(c, dict) or key not in c:
                continue
            v = c[key]
            if many:
                nxt.extend(v if isinstance(v, list) else [])
            else:
                nxt.append(v)
        cur = nxt
    return cur


class TestPlotCarriesUiTexts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out, es = run_case("bcg_oltas_RR.csv", {"measure": "RR", "subgroup": "allokáció", "cumulative": "év",
                                               "outliers": True})
        prov = {"schema": "szk.ma.provenance/v1", "table": "03_adatok/o1.csv", "table_sha256": None, "cells": []}
        cls.doc = plot_doc(out, es, provenance=prov)
        sources = []
        for root, _dirs, files in os.walk(WEB_SRC):
            for f in files:
                if f.endswith(".js") and ("plots" in root or f in ("results.js", "analysis_shared.js", "plan.js")):
                    with open(os.path.join(root, f), encoding="utf-8") as fh:
                        sources.append(fh.read())
        cls.js = "\n".join(sources)

    def test_every_ui_field_is_produced(self):
        for path, js_key in UI_PLOT_FIELDS:
            with self.subTest(path=path):
                vals = lookup(self.doc, path)
                self.assertTrue(vals, "a motor nem adja: %s" % path)
                self.assertTrue(any(v is not None for v in vals), "csak null: %s" % path)
                self.assertIn(js_key, self.js, "a felület már nem olvassa (%s) — frissítsd a listát" % js_key)

    def test_text_fields_are_i18n(self):
        for path, _k in UI_PLOT_FIELDS:
            if not path.split(".")[-1].endswith(("_text", "_label", "label", "note")) or path.startswith("loo[].label") \
                    or path.endswith("cumulative.entries[].key_text") or path == "influence[].label":
                continue
            for v in lookup(self.doc, path):
                if v is None or isinstance(v, str) and path.endswith("key_text"):
                    continue
                with self.subTest(path=path):
                    self.assertEqual(set(v), {"hu", "en"}, path)

    def test_documents_conform_to_extended_schema(self):
        for fn, opts in CASES:
            for sl in (None, slice(0, 2), slice(0, 1)):
                out, es = run_case(fn, opts, sl)
                if out.get("primary") is None:
                    continue
                with self.subTest(fn=fn, opts=opts, rows=sl):
                    self.assertEqual(C.errors_for(plot_doc(out, es), "ma.plot", 2), [])

    def test_ui_reads_are_declared_in_schema(self):
        s = C.REGISTRY[C.K.urn("ma.plot", 2)]
        props = s["properties"]
        for key in ("k", "display_locale", "loo_axis", "influence_axes", "influence_text", "influence_note"):
            self.assertIn(key, props)
        self.assertIn("weight_text", props["studies"]["items"]["properties"])
        for key in ("primary", "p_text", "pi_label"):
            self.assertIn(key, props["summaries"]["items"]["properties"])
        for key in ("i2_text", "tau2_text"):
            self.assertIn(key, props["heterogeneity"]["properties"])


class TestRunDescriptorTexts(unittest.TestCase):
    def test_run_conforms_and_overview_fields(self):
        for fn, opts in CASES:
            out, es = run_case(fn, opts)
            d = S.run_descriptor(out, es=es)
            doc = plot_doc(out, es)
            prim = [s for s in doc["summaries"] if s.get("primary")][0]
            with self.subTest(fn=fn, opts=opts):
                self.assertEqual(C.errors_for(d, "ma.run", 1), [])
                self.assertEqual(d["primary"]["display_text"], prim["display_text"], "run.json = plot_data.json szövege")
                flags = [s["flags"]["rob"] for s in doc["studies"]]
                assessed = [f for f in flags if f in ("low", "some", "high", "critical")]
                # értékelés nélkül None (nem 0 — UX-02); különben a magas/kritikus sorok száma
                self.assertEqual(d["rob_high"], sum(1 for f in assessed if f in ("high", "critical")) if assessed else None)
                self.assertEqual(d["rob_missing"], len(flags) - len(assessed))
                self.assertEqual(d["measure"], opts["measure"], "a futás saját hatásmérete (FID-2)")
                self.assertIsNotNone(doc["heterogeneity"]["i2_text"], "az áttekintő I²-szövege a plotból jön")

    def test_rob_high_none_without_assessment_and_counted_with_it(self):
        """UX-02: RoB-értékelés nélkül a rob_high None (az áttekintő „nincs értékelés”-t mutat, nem 0-t); részleges
        értékelésnél a magas kockázatúak száma és a hiányzók száma (rob_missing)."""
        out, es = run_case("bcg_oltas_RR.csv", {"measure": "RR"})
        d = S.run_descriptor(out, es=es)
        self.assertIsNone(d["rob_high"])
        self.assertEqual(d["rob_missing"], d["k"])
        self.assertEqual(C.errors_for(d, "ma.run", 1), [])
        rows, meta = tableio.read_table(os.path.join(PELDAK, "bcg_oltas_RR.csv"))
        for i, r in enumerate(rows):
            r["rob"] = {0: "high", 1: "low", 2: "some"}.get(i)
        with contextlib.redirect_stdout(io.StringIO()):
            out, es = pipeline.run(rows, {"measure": "RR"}, meta, table_rows=rows)
        d = S.run_descriptor(out, es=es)
        self.assertEqual(d["rob_high"], 1)
        self.assertEqual(d["rob_missing"], d["k"] - 3)
        self.assertEqual(C.errors_for(d, "ma.run", 1), [])

    def test_participants_text_matches_report(self):
        out, es = run_case("bcg_oltas_RR.csv", {"measure": "RR"})
        d = S.run_descriptor(out, es=es)
        self.assertEqual(d["participants_text"], {"hu": "357347", "en": "357347"})
        self.assertIn("| Résztvevők (N, elemzett vizsgálatok) | 357347 |", report.build_report(out, None, "2026-10-04"))

    def test_expanded_argv_round_trips(self):
        sp = {"schema": "szk.ma.analysis-spec/v1", "name": "o1_primary", "outcome": "o1",
              "data": {"path": "03_adatok/o1.csv"}, "options": {"measure": "RR", "tau2": "DL"},
              "filters": {"exclude": ["estimated=igen"]}}
        out, es = run_case("bcg_oltas_RR.csv", {"measure": "RR"})
        d = S.run_descriptor(out, spec=sp, es=es)
        self.assertEqual(d["expanded_argv"][:2], ["ma.py", "analyze"])
        back = S.spec_from_argv(d["expanded_argv"][1:])
        self.assertEqual(S.options_from_spec(back), S.options_from_spec(sp))
        self.assertNotIn("expanded_argv", S.run_descriptor(out, spec={"name": "x", "parent": None}, es=es))


# ------------------------------------------------------------------------------------------- tábla, column_map
class TestColumnMap(unittest.TestCase):
    HEADERS = [["study", "e1", "n1", "e2", "n2"], ["Vizsgálat", "esemény1", "N1", "esemény2", "n2", "év", "allokáció"],
               ["row_uid", "szerző", "M1", "SD1", "N1", "M2", "SD2", "N2"], ["study", "m1", "m1", "sd1"],
               ["Study ", " e1", "n1", "e2", "n2", "rob"]]

    def test_column_map_equals_validation_document(self):
        for h in self.HEADERS:
            measure = "MD" if any(x.strip().lower() == "m1" for x in h) else "RR"
            doc = api.validate_table(h, [["A"] + ["1"] * (len(h) - 1)], measure)
            with self.subTest(header=h):
                self.assertEqual(api.column_map(h), doc["column_map"])

    def test_read_table_exposes_column_map_and_round_trips(self):
        tmp = tempfile.mkdtemp()
        try:
            for fn in os.listdir(PELDAK):
                if not fn.endswith(".csv"):
                    continue
                p = os.path.join(PELDAK, fn)
                header, rows, meta = api.read_table(p)
                doc = api.validate_file(p, "RR" if "bcg" in fn else "MD" if "normand" in fn else
                                        "ZCOR" if "molloy" in fn else "PFT")
                with self.subTest(fn=fn):
                    self.assertEqual(meta["column_map"], doc["column_map"])
                    q = os.path.join(tmp, fn)
                    api.write_table(q, header, rows, meta)
                    with open(p, "rb") as a, open(q, "rb") as b:
                        self.assertEqual(a.read(), b.read(), "a column_map nem kerül a fájlba")
        finally:
            shutil.rmtree(tmp)


# ------------------------------------------------------------------------------------------- döntés-kontextus
class TestDecisionContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        api.project_init(self.tmp, "teszt")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_context_stored_as_json_object(self):
        ctx = {"kind": "validation", "dataset": "03_adatok/o1.csv", "row_uid": "rbcg09", "code": "V018",
               "fields": ["estimated"], "spec": None}
        r = api.project_log(self.tmp, "user", "Nem hiba", rationale="dokumentált becslés", context=ctx)
        item = api.project_show(self.tmp, "decision", r["id"])
        self.assertEqual(item["context"], {"kind": "validation", "dataset": "03_adatok/o1.csv", "row_uid": "rbcg09",
                                           "code": "V018", "fields": ["estimated"]})
        self.assertEqual(api.project_list(self.tmp, "decisions")[0]["context"], item["context"])
        self.assertEqual(api.project_export_json(self.tmp)["decisions"][0]["context"], item["context"])
        con = sqlite3.connect(projekt.db_path(self.tmp))
        raw = con.execute("SELECT context FROM decision WHERE id=?", (r["id"],)).fetchone()[0]
        con.close()
        self.assertEqual(json.loads(raw), item["context"])
        plain = api.project_log(self.tmp, "user", "döntés kontextus nélkül")
        self.assertIsNone(api.project_show(self.tmp, "decision", plain["id"])["context"])
        self.assertNotIn("rbcg09", api.project_export_markdown(self.tmp), "a Markdown-napló változatlan")

    def test_context_refuses_cell_values_and_unknown_keys(self):
        bad = [{"kind": "validation", "value": "12,3"}, {"kind": "validation", "changes": [{"key": "x", "cells": 1}]},
               {"kind": "validation", "foo": 1}, {"kind": "nem-ismert"}, {"row_uid": "R1"}, {"code": "V1"},
               {"fields": "e1"}, {"dataset": "a\nb"}, "nem objektum", {"kind": "validation", "fields": [float("nan")]}]
        for ctx in bad:
            with self.subTest(ctx=ctx):
                with self.assertRaises(ValueError):
                    api.project_log(self.tmp, "user", "x", context=ctx)
        self.assertEqual(api.project_list(self.tmp, "decisions"), [], "hibás kontextus: semmi nem íródott")
        with self.assertRaises(ValueError):
            api.project_log(self.tmp, "user", "x", context={"kind": "other", "outcome": "o" * 5000})

    def test_old_journal_gets_context_column(self):
        con = sqlite3.connect(projekt.db_path(self.tmp))
        con.executescript("DROP TABLE decision; CREATE TABLE decision (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, "
                          "agent TEXT NOT NULL, stage_id TEXT, decision TEXT NOT NULL, rationale TEXT, alternatives TEXT, "
                          "kb_refs TEXT, status TEXT NOT NULL DEFAULT 'active', supersedes INTEGER, kb_unverified TEXT);")
        con.execute("INSERT INTO decision (ts, agent, decision) VALUES ('2026-01-01T00:00:00', 'user', 'régi')")
        con.commit()
        con.close()
        self.assertIsNone(api.project_list(self.tmp, "decisions")[0]["context"])
        r = api.project_log(self.tmp, "user", "új", context={"kind": "analysis", "spec": "o1_primary"})
        self.assertEqual(api.project_show(self.tmp, "decision", r["id"])["context"],
                         {"kind": "analysis", "spec": "o1_primary"})


class TestAcknowledged(unittest.TestCase):
    HEADER = ["row_uid", "study", "e1", "n1", "e2", "n2", "estimated", "rob"]
    ROWS = [["rabc01", "A", "4", "123", "11", "139", "igen", "low"],
            ["rabc02", "B", "0", "10", "3", "12", "nem", "high"],
            ["rabc03", "C", "5", "50", "8", "52", "nem", "low"]]

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        api.project_init(self.tmp, "teszt")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def validate(self, dataset="03_adatok/o1.csv", rows=None):
        return api.validate_table(self.HEADER, rows or self.ROWS, "RR", project_dir=self.tmp, dataset=dataset)

    def find(self, doc, code, uid=None):
        return [f for f in doc["findings"] if f["code"] == code and (uid is None or f["row_uid"] == uid)][0]

    def ack(self, f, **over):
        ctx = {"kind": "validation", "dataset": "03_adatok/o1.csv", "row_uid": f["row_uid"], "code": f["code"],
               "fields": f["fields"]}
        ctx.update(over)
        return api.project_log(self.tmp, "user", "Nem hiba — indoklás", rationale="ellenőrizve a forrással",
                               kb_refs=f["code"], context=ctx)["id"]

    def test_every_finding_gets_the_key(self):
        doc = self.validate()
        self.assertTrue(doc["findings"])
        self.assertTrue(all("acknowledged" in f and f["acknowledged"] is None for f in doc["findings"]))
        self.assertEqual(C.errors_for(doc, "ma.validation", 1), [])
        self.assertNotIn("acknowledged", api.validate_table(self.HEADER, self.ROWS, "RR")["findings"][0],
                         "project_dir nélkül a szerver jelöl")
        self.assertNotIn("_row_uids", doc)

    def test_match_by_code_row_uid_and_fields(self):
        f = self.find(self.validate(), "V018", "rabc01")
        did = self.ack(f)
        doc = self.validate()
        self.assertEqual(self.find(doc, "V018", "rabc01")["acknowledged"], did)
        self.assertIsNone(self.find(doc, "V009", "rabc02")["acknowledged"], "más szabály")
        self.assertIsNone(self.find(doc, "V019", "rabc02")["acknowledged"], "más sor")
        other = self.validate(dataset="03_adatok/o2.csv")
        self.assertIsNone(self.find(other, "V018", "rabc01")["acknowledged"], "más tábla")
        later = self.ack(f)
        self.assertEqual(self.find(self.validate(), "V018", "rabc01")["acknowledged"], later, "a legutóbbi döntés")

    def test_fields_and_dataset_rules(self):
        f = self.find(self.validate(), "V009", "rabc02")
        self.ack(f, fields=["e1"])
        self.assertIsNone(self.find(self.validate(), "V009", "rabc02")["acknowledged"], "eltérő mezők")
        did = self.ack(f, fields=None, dataset=None)
        self.assertEqual(self.find(self.validate(), "V009", "rabc02")["acknowledged"], did,
                         "mezők és tábla nélküli döntés: kód + sor elég")

    def test_table_level_and_superseded(self):
        doc = self.validate()
        f = self.find(doc, "V015")
        self.assertIsNone(f["row_uid"])
        did = self.ack(f)
        self.assertEqual(self.find(self.validate(), "V015")["acknowledged"], did)
        api.project_log(self.tmp, "user", "visszavonva", supersedes=did)
        self.assertIsNone(self.find(self.validate(), "V015")["acknowledged"], "felülírt döntés nem számít")

    def test_multi_row_findings_use_row_uids(self):
        rows = [["rdup01", "Same", "4", "40", "6", "41", "nem", "low"],
                ["rdup02", "Same", "4", "40", "6", "41", "nem", "low"],
                ["rdup03", "Other", "5", "50", "8", "52", "nem", "low"]]
        doc = self.validate(rows=rows)
        multi = [f for f in doc["findings"] if len(f.get("rows") or []) > 1]
        self.assertTrue(multi, "van többsoros megállapítás (duplikátum)")
        f = multi[0]
        did = self.ack(f, row_uid="rdup02")
        hit = [g for g in self.validate(rows=rows)["findings"] if g["code"] == f["code"] and g["rows"] == f["rows"]]
        self.assertEqual(hit[0]["acknowledged"], did)

    def test_validate_file_with_project(self):
        p = os.path.join(self.tmp, "03_adatok", "o1.csv")
        with open(p, "w", encoding="utf-8", newline="") as fh:
            csv.writer(fh, delimiter=";").writerows([self.HEADER] + self.ROWS)
        doc = api.validate_file(p, "RR", project_dir=self.tmp, dataset="03_adatok/o1.csv")
        f = self.find(doc, "V018", "rabc01")
        did = self.ack(f)
        self.assertEqual(self.find(api.validate_file(p, "RR", project_dir=self.tmp, dataset="03_adatok/o1.csv"),
                                   "V018", "rabc01")["acknowledged"], did)


# ------------------------------------------------------------------------------------------- forrás a provenance-ből
class TestProvenanceSource(unittest.TestCase):
    def test_source_from_sidecar_and_column_precedence(self):
        tmp = tempfile.mkdtemp()
        try:
            header = ["row_uid", "study", "e1", "n1", "e2", "n2", "oldal"]
            rows = [["rsrc01", "A", "4", "123", "11", "139", "7"], ["rsrc02", "B", "6", "306", "29", "303", ""],
                    ["rsrc03", "C", "3", "231", "11", "220", ""]]
            data = os.path.join(tmp, "o1.csv")
            with open(data, "w", encoding="utf-8", newline="") as fh:
                csv.writer(fh, delimiter=";").writerows([header] + rows)
            prov = {"schema": "szk.ma.provenance/v1", "table": "03_adatok/o1.csv", "table_sha256": None, "cells": [
                {"row_uid": "rsrc01", "field": "n1", "method": "reported", "source": {"doc": "file:a.pdf", "page": 3}},
                {"row_uid": "rsrc02", "field": "rob", "method": "reported", "source": {"doc": "file:x.pdf", "page": 9}},
                {"row_uid": "rsrc02", "field": "e1", "method": "reported",
                 "source": {"doc": "file:b.pdf", "page": 5, "locator": "Table 2", "quote": "6 of 306"},
                 "value_as_entered": "6"}]}
            with open(pipeline.provenance_path(data), "w", encoding="utf-8") as fh:
                json.dump(prov, fh)
            loaded = pipeline.load_provenance(data)
            self.assertEqual(loaded["cells"][0]["row_uid"], "rsrc01")
            r, meta = tableio.read_table(data)
            with contextlib.redirect_stdout(io.StringIO()):
                out, es = pipeline.run(r, {"measure": "RR"}, meta, table_rows=r)
            doc = plot_doc(out, es, provenance=loaded)
            src = {s["row_uid"]: s["source"] for s in doc["studies"]}
            self.assertEqual(src["rsrc01"], {"doc": "file:a.pdf", "page": 7, "locator": None}, "az oszlop elsőbbsége")
            self.assertEqual(src["rsrc02"], {"doc": "file:b.pdf", "page": 5, "locator": "Table 2"},
                             "a mérték kötelező mezőjének forrása")
            self.assertEqual(src["rsrc03"], {"doc": None, "page": None, "locator": None})
            self.assertNotIn("6 of 306", json.dumps(doc), "idézet és cellaérték nem kerül a plotba")
            with open(pipeline.provenance_path(data), "w", encoding="utf-8") as fh:
                fh.write("{nem json")
            self.assertIsNone(pipeline.load_provenance(data))
            paths = pipeline.write_outputs(out, es, os.path.join(tmp, "out"))
            with open(paths["plot_data.json"], encoding="utf-8") as fh:
                written = json.load(fh)
            self.assertEqual(written["studies"][0]["source"]["page"], 7, "hibás oldalfájl: csak az oszlop")
        finally:
            shutil.rmtree(tmp)

    def test_api_analyze_reads_sidecar(self):
        tmp = tempfile.mkdtemp()
        try:
            api.project_init(tmp, "teszt")
            header = ["row_uid", "study", "e1", "n1", "e2", "n2"]
            rows = [["rsrc01", "A", "4", "123", "11", "139"], ["rsrc02", "B", "6", "306", "29", "303"]]
            with open(os.path.join(tmp, "03_adatok", "o1.csv"), "w", encoding="utf-8", newline="") as fh:
                csv.writer(fh, delimiter=";").writerows([header] + rows)
            prov = {"schema": "szk.ma.provenance/v1", "table": "03_adatok/o1.csv", "table_sha256": None, "cells": [
                {"row_uid": "rsrc02", "field": "e1", "method": "reported", "source": {"doc": "file:b.pdf", "page": 5}}]}
            with open(os.path.join(tmp, "03_adatok", "o1.prov.json"), "w", encoding="utf-8") as fh:
                json.dump(prov, fh)
            sp = {"schema": "szk.ma.analysis-spec/v1", "name": "o1_primary", "outcome": "o1",
                  "data": {"path": "03_adatok/o1.csv"}, "options": {"measure": "RR"}}
            view = api.analyze(sp, project_root=tmp)
            self.assertEqual({s["row_uid"]: s["source"]["page"] for s in view["plot"]["studies"]},
                             {"rsrc01": None, "rsrc02": 5})
            commit = api.analyze(sp, mode="commit", project_root=tmp)
            with open(os.path.join(tmp, commit["run"]["files"]["plot"]["path"]), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["studies"], view["plot"]["studies"])
        finally:
            shutil.rmtree(tmp)


# ------------------------------------------------------------------------------------------- fixture-ök
class TestFixtures(unittest.TestCase):
    """A felület fixture-jei a motorból készülnek és a szerződéseknek megfelelnek."""

    def test_generator_has_no_drift(self):
        gen = load_generator()
        files = gen.generate()
        for name, obj in files.items():
            with self.subTest(fixture=name):
                with open(os.path.join(FX, name), encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), gen.render(name, obj),
                                     "elsodródott — futtasd: python3 tests/gui/ui/gen_fixtures_from_engine.py")

    def test_plot_fixtures_conform(self):
        routes = ok_routes("analysis_plots.json")
        self.assertGreaterEqual(len(routes), 9)
        for r in routes:
            with self.subTest(path=r["path"]):
                self.assertEqual(C.errors_for(r["envelope"]["data"], "ma.plot", 2), [])

    def test_run_fixtures_conform(self):
        runs = []
        for name in ("analysis_runs.json", "runs.json"):
            for r in ok_routes(name):
                d = r["envelope"]["data"]
                runs.extend(d["runs"] if "runs" in d else [d])
        self.assertGreaterEqual(len(runs), 10)
        for run in runs:
            with self.subTest(run=run["run_id"]):
                self.assertEqual(C.errors_for(run, "ma.run", 1), [])
                self.assertIn("participants_text", run)
                self.assertIn("rob_high", run)
        for r in ok_routes("analysis_jobs.json"):
            res = (r["envelope"]["data"] or {}).get("result")
            if res:
                self.assertEqual(C.errors_for(res["run"], "ma.run", 1), [])
                if res.get("plot"):
                    self.assertEqual(C.errors_for(res["plot"], "ma.plot", 2), [])
        for r in load_fixture("analysis_jobs.json")["routes"]:
            for step in r.get("sequence") or ():
                res = step["envelope"]["data"].get("result")
                if res:
                    self.assertEqual(C.errors_for(res["run"], "ma.run", 1), [])

    def test_spec_fixtures_are_engine_valid(self):
        for r in ok_routes("analysis_specs.json"):
            sp = r["envelope"]["data"]
            with self.subTest(spec=sp["name"]):
                self.assertEqual(C.errors_for(sp, "ma.analysis-spec", 1), [])
                self.assertEqual(api.validate_spec(sp), [])
                self.assertEqual(list(sp["options"]), list(pipeline.DEFAULTS))

    def test_validation_convert_and_table_fixtures(self):
        for r in ok_routes("validate.json"):
            with self.subTest(dataset=r["body"]["dataset"]):
                doc = r["envelope"]["data"]
                self.assertEqual(C.errors_for(doc, "ma.validation", 1), [])
                self.assertTrue(all("acknowledged" in f for f in doc["findings"]))
        for r in ok_routes("convert.json"):
            with self.subTest(convert=r["body"]):
                self.assertEqual(C.errors_for(r["envelope"]["data"], "ma.convert-result", 1), [])
        tables = {r["query"]["dataset"]: r["envelope"]["data"] for r in ok_routes("table.json") if r["method"] == "GET"}
        validations = {r["body"]["dataset"]: r["envelope"]["data"] for r in ok_routes("validate.json")}
        for ds, t in tables.items():
            with self.subTest(table=ds):
                self.assertEqual(t["column_map"], api.column_map(t["header"]))
                vmap = dict(validations[ds]["column_map"])
                vmap.pop("row_uid", None)
                self.assertEqual(t["column_map"], vmap, "a tábla és a validálás ugyanúgy képez oszlopot")

    def test_engine_fixture_matches_engine_metadata(self):
        data = ok_routes("engine.json")[0]["envelope"]["data"]
        live = api.engine_info(selftest=False)
        for key in ("engine_version", "measures", "options", "rules", "contracts"):
            with self.subTest(key=key):
                self.assertEqual(data[key], json.loads(json.dumps(live[key])))


if __name__ == "__main__":
    unittest.main()
