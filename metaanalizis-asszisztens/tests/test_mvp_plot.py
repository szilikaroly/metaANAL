# -*- coding: utf-8 -*-
"""MVP E4a/E4b/E5: a szk.ma.plot/v2 dokumentum (terv 4.6) és az SVG-k nyelve, rétegei, sor-azonosítói.

- a plot_data.json alapból v2, a plot_schema='v1' a korábbi kulcsokat adja (bájtra azonosan a make_plots v1-ével);
- szerkezeti invariánsok: egyedi row_uid, a tickek a tartományon belül és azonosak az SVG tickjeivel, zárt
  kontúr-poligonok, a display = a y/lo/hi visszatranszformáltja, nincs NaN/Infinity;
- a display_text szó szerint az SVG-ben (hu: az alapértelmezett, en: az angol ábra);
- lang='en' feliratok és U+2212; annotate=True: rétegek és <g id="study-<row_uid>" data-row data-y data-lo data-hi>,
  az annotálatlan alapértelmezés változatlan;
- a contracts/ma.plot.v2.schema.json szerinti validálás, ha a séma (és a stdlib-validátor) már megvan.
"""
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import tempfile
import unittest
import xml.dom.minidom

from _helpers import ROOT, assert_close
from metaelemzes import pipeline, tableio
from metaelemzes import effect_sizes as E
from metaelemzes import plots as P
from metaelemzes import validate as V

PELDAK = os.path.join(ROOT, "peldak")
BCG = os.path.join(PELDAK, "bcg_oltas_RR.csv")
NORMAND = os.path.join(PELDAK, "normand1999_folytonos.csv")
MOLLOY = os.path.join(PELDAK, "molloy2014_korrelacio.csv")
PRITZ = os.path.join(PELDAK, "pritz1997_arany.csv")
CASES = [(BCG, {"measure": "RR"}), (BCG, {"measure": "OR", "subgroup": "allokáció", "cumulative": "év"}),
         (BCG, {"measure": "RR", "model": "fixed", "subgroup": "allokáció"}), (BCG, {"measure": "RD"}),
         (BCG, {"measure": "RR", "model": "ivhet", "outliers": True}),
         (NORMAND, {"measure": "MD"}), (NORMAND, {"measure": "SMD"}), (NORMAND, {"measure": "ROM"}),
         (MOLLOY, {"measure": "ZCOR"}), (MOLLOY, {"measure": "COR"}),
         (PRITZ, {"measure": "PFT"}), (PRITZ, {"measure": "PFT", "pft_backtransform": "variance"}),
         (PRITZ, {"measure": "PLO"}), (PRITZ, {"measure": "PAS"}), (PRITZ, {"measure": "PLN"}),
         (PRITZ, {"measure": "PR"})]
V1_KEYS = {"measure", "ratio_scale", "level", "axis_n", "studies", "summaries", "sections", "axis_title",
           "null_value", "heterogeneity", "funnel"}
V2_REQUIRED = ("schema", "meta", "measure", "scale", "level", "axis", "studies", "summaries")
STUDY_REQUIRED = ("row_uid", "row_index", "label", "y", "lo", "hi", "weight_pct", "display", "display_text")
SUMMARY_REQUIRED = ("id", "kind", "label", "estimate", "ci_lower", "ci_upper", "display", "display_text")
UID_RE = re.compile(r"^r[0-9a-z]{4,12}$")
SCHEMA_PATH = os.path.join(ROOT, "metaelemzes", "contracts", "ma.plot.v2.schema.json")
COMMON_PATH = os.path.join(ROOT, "metaelemzes", "contracts", "common.schema.json")


def analyze(path, table_rows=False, filters=None, **opts):
    rows, meta = tableio.read_table(path)
    full = rows
    if filters:
        rows = tableio.apply_filters(rows, meta=meta, **filters)
    out, es = pipeline.run(rows, opts, meta, table_rows=full if table_rows else None)
    return out, es


def strict_json(text):
    def bad(c):
        raise ValueError("nem JSON-szám: %s" % c)
    return json.loads(text, parse_constant=bad)


def svg_text(svg):
    """Az SVG <text> elemeinek szövege (XML-entitások feloldva)."""
    doc = xml.dom.minidom.parseString(svg.encode("utf-8"))
    out = []
    for t in doc.getElementsByTagName("text"):
        out.append("".join(n.data for n in t.childNodes if n.nodeType == n.TEXT_NODE))
    return out


def unwrap(svg):
    """Az annotált SVG sorai a <g>-burkolók nélkül, U+2212 → '-' (az alapértelmezett SVG sorainak multihalmaza)."""
    lines = []
    for ln in svg.split("\n"):
        if ln.startswith("<g id=") and ln.endswith(">") and "</g>" not in ln:
            continue
        if ln == "</g>":
            continue
        m = re.match(r'^<g id="[^"]+"[^>]*>(.*)</g>$', ln)
        lines.append((m.group(1) if m else ln).replace(P.MINUS, "-"))
    return sorted(lines)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return p


# ------------------------------------------------------------------ kompatibilitás
class TestBackwardCompatibility(_Tmp):
    def test_defaults_and_options(self):
        self.assertEqual((pipeline.DEFAULTS["plot_schema"], pipeline.DEFAULTS["plot_locale"],
                          pipeline.DEFAULTS["svg_annotate"]), ("v2", "hu", False))
        for bad in ({"plot_schema": "v3"}, {"plot_locale": "de"}, {"svg_annotate": "yes"}):
            with self.assertRaises(ValueError):
                analyze(BCG, measure="RR", **bad)

    def test_make_plots_still_returns_v1(self):
        out, es = analyze(BCG, measure="RR", subgroup="allokáció")
        f_svg, fu_svg, data = pipeline.make_plots(out, es)
        self.assertTrue(V1_KEYS <= set(data), set(data))
        self.assertNotIn("schema", data)
        self.assertEqual([s["title"] for s in data["sections"]], ["random", "alternate", "systematic"])
        self.assertIsInstance(data["summaries"][0]["display"], list)
        # az alapértelmezés = kifejezett hu, annotálatlan
        self.assertEqual(pipeline.make_plots(out, es, lang="hu", annotate=False)[:2], (f_svg, fu_svg))
        for svg in (f_svg, fu_svg, pipeline.make_doi_plot(out, es)):
            self.assertNotIn(P.MINUS, svg)
            self.assertNotIn("<g ", svg)
            self.assertNotIn("data-row", svg)
        self.assertIn(">Vizsgálat<", f_svg)
        self.assertIn(">Súly<", f_svg)
        self.assertIn(">Alcsoport összesen (k = 7)<", f_svg)
        self.assertIn(">Heterogenitás: Q = ", f_svg)

    def test_plot_schema_v1_file_equals_make_plots_v1(self):
        for path, opts in CASES[:3] + CASES[10:12]:
            out, es = analyze(path, plot_schema="v1", **opts)
            od = os.path.join(self.tmp, "v1_%s_%s" % (opts["measure"], len(os.listdir(self.tmp))))
            pipeline.write_outputs(out, es, od)
            with open(os.path.join(od, "plot_data.json"), encoding="utf-8") as fh:
                got = fh.read()
            want = json.dumps(pipeline.to_jsonable(pipeline.make_plots(out, es)[2]), ensure_ascii=False, indent=1)
            self.assertEqual(got, want, opts)
            self.assertNotIn('"schema"', got)

    def test_default_file_is_v2_and_svgs_unchanged(self):
        out, es = analyze(BCG, measure="RR")
        od = os.path.join(self.tmp, "o")
        paths = pipeline.write_outputs(out, es, od)
        with open(paths["plot_data.json"], encoding="utf-8") as fh:
            doc = strict_json(fh.read())
        self.assertEqual(doc["schema"], "szk.ma.plot/v2")
        f_svg, fu_svg, _ = pipeline.make_plots(out, es)
        for name, svg in (("forest.svg", f_svg), ("funnel.svg", fu_svg), ("doi.svg", pipeline.make_doi_plot(out, es))):
            with open(paths[name], encoding="utf-8") as fh:
                self.assertEqual(fh.read(), svg, name)

    def test_annotated_svg_is_default_plus_wrappers(self):
        for path, opts in CASES:
            out, es = analyze(path, **opts)
            plain = pipeline.make_plots(out, es)[:2] + (pipeline.make_doi_plot(out, es),)
            ann = pipeline.make_plots(out, es, annotate=True)[:2] + (pipeline.make_doi_plot(out, es, annotate=True),)
            for a, b in zip(plain, ann):
                if a is None:
                    self.assertIsNone(b)
                    continue
                xml.dom.minidom.parseString(b.encode("utf-8"))
                self.assertEqual(unwrap(b), sorted(a.split("\n")), opts)


# ------------------------------------------------------------------ v2 szerkezet
class TestPlotV2Structure(_Tmp):
    def doc(self, path, **opts):
        out, es = analyze(path, **opts)
        return out, es, pipeline.plot_document(out, es)

    def test_required_fields_and_json_safety(self):
        for path, opts in CASES:
            out, es, doc = self.doc(path, **opts)
            for key in V2_REQUIRED:
                self.assertIn(key, doc, (key, opts))
            self.assertEqual(doc["schema"], "szk.ma.plot/v2")
            self.assertEqual(doc["meta"]["engine_version"], out["engine"]["version"])
            for st in doc["studies"]:
                for key in STUDY_REQUIRED:
                    self.assertIn(key, st, key)
            for sm in doc["summaries"]:
                for key in SUMMARY_REQUIRED:
                    self.assertIn(key, sm, key)
                self.assertIn(sm["kind"], ("overall", "subgroup", "sensitivity"))
                self.assertIn(sm["model"], ("random", "fixed", "ivhet", "mh", "peto"))
                self.assertEqual(set(sm["label"]), {"hu", "en"})
            self.assertIsInstance(doc["sections"], list)
            self.assertIsNone(doc["bubble"])
            # nincs NaN / Infinity a fájlban
            text = json.dumps(pipeline.to_jsonable(doc), ensure_ascii=False, allow_nan=False)
            strict_json(text)

    def test_row_uids_unique_and_consistent(self):
        for path, opts in CASES:
            out, es, doc = self.doc(path, **opts)
            uids = [s["row_uid"] for s in doc["studies"]]
            self.assertEqual(len(uids), len(set(uids)), opts)
            self.assertTrue(all(UID_RE.match(u) for u in uids), uids)
            self.assertEqual(len(uids), out["effect_sizes"]["k"])
            self.assertEqual([s["row_index"] for s in doc["studies"]], out["effect_sizes"]["row_index"])
            self.assertEqual([p["row_uid"] for p in doc["funnel"]["points"]], uids)
            if doc["loo"]:
                self.assertEqual([e["omitted_row_uid"] for e in doc["loo"]], uids)
            if doc["influence"]:
                self.assertEqual([e["row_uid"] for e in doc["influence"]], uids)
            if "doi" in doc:
                self.assertEqual(sorted(p["row_uid"] for p in doc["doi"]["points"]), sorted(uids))
            for sec in doc["sections"]:
                self.assertTrue(set(sec["row_uids"]) <= set(uids))
                self.assertEqual(sec["row_uids"], [uids[i] for i in sec["indices"]])
                for u in sec["row_uids"]:
                    self.assertEqual(doc["studies"][uids.index(u)]["section"], sec["id"])
            if doc["sections"]:
                self.assertEqual(sorted(u for sec in doc["sections"] for u in sec["row_uids"]), sorted(uids))

    def test_row_uid_matches_validation_document(self):
        doc_v = V.validation_document_from_file(BCG, "RR")
        out, es, doc = self.doc(BCG, measure="RR")
        self.assertEqual([s["row_uid"] for s in doc["studies"]],
                         tableio.row_uids(*tableio.read_table(BCG)))
        uid_of_row = {f["row"]: f["row_uid"] for f in doc_v["findings"] if f["row"] is not None}
        for st in doc["studies"]:
            if st["row_index"] in uid_of_row:
                self.assertEqual(st["row_uid"], uid_of_row[st["row_index"]])

    def test_filtered_rows_keep_full_table_identity(self):
        full = tableio.row_uids(*tableio.read_table(BCG))
        flt = {"exclude": ["allokáció=random"]}
        out, es = analyze(BCG, table_rows=True, filters=flt, measure="RR")
        doc = pipeline.plot_document(out, es)
        for st in doc["studies"]:
            self.assertEqual(st["row_uid"], full[st["row_index"]])
        self.assertNotEqual([s["row_index"] for s in doc["studies"]], list(range(len(doc["studies"]))))
        # table_rows nélkül a szűrt lista számít teljes táblának (a CLI-nek át kell adnia)
        out2, es2 = analyze(BCG, filters=flt, measure="RR")
        self.assertEqual([s["row_index"] for s in pipeline.plot_document(out2, es2)["studies"]],
                         list(range(len(es2))))

    def test_row_uid_column_is_used(self):
        p = self.write("u.csv", "row_uid,study,e1,n1,e2,n2\nrabc123,A,4,123,11,139\n,B,6,306,29,303\n"
                                "rzz99,C,3,231,11,220\nrabc123,D,62,13598,248,12867\n")
        out, es = analyze(p, measure="RR")
        uids = [s["row_uid"] for s in pipeline.plot_document(out, es)["studies"]]
        self.assertEqual(uids[0], "rabc123")
        self.assertEqual(uids[2], "rzz99")
        self.assertEqual(uids[1], tableio.row_uid_for("B", 1, {"rabc123", "rzz99"}))
        self.assertNotEqual(uids[3], "rabc123")             # ismétlődő uid: determinisztikus pótlás
        self.assertEqual(len(set(uids)), 4)

    def test_axis_ticks_match_svg_and_lie_in_domain(self):
        for path, opts in CASES:
            out, es, doc = self.doc(path, **opts)
            f_svg, fu_svg, data = pipeline.make_plots(out, es)
            for ax, svg in ((doc["axis"], f_svg), (doc["funnel"]["axis"], fu_svg)):
                lo, hi = ax["domain"]
                self.assertLess(lo, hi)
                eps = 1e-9 * (hi - lo)
                ats = [t["at"] for t in ax["ticks"]]
                self.assertEqual(ats, sorted(ats))
                self.assertTrue(all(lo - eps <= a <= hi + eps for a in ats), (opts, ats, lo, hi))
                texts = svg_text(svg)
                for t in ax["ticks"]:
                    self.assertIn(t["text"], texts)
                    self.assertEqual(t["text_i18n"]["hu"], t["text"])
                    self.assertEqual(t["text_i18n"]["en"], t["text"].replace("-", P.MINUS))
            self.assertEqual(set(doc["axis"]["title"]), {"hu", "en"})
            self.assertIn(doc["axis"]["title"]["hu"], svg_text(f_svg))
            # a tengely ugyanaz, mint amivel az SVG rajzol
            axis, _ = P.forest_axis(data)
            self.assertEqual(doc["axis"]["domain"], [axis.lo, axis.hi])

    def test_display_is_back_transform_of_y(self):
        for path, opts in CASES:
            out, es, doc = self.doc(path, **opts)
            m = doc["measure"]
            n_axis = doc["scale"].get("axis_n")
            if m == "PFT":
                self.assertIsNotNone(n_axis)

            def bt(v):
                return E.back_transform(m, v, n_axis)

            for st in doc["studies"]:
                for key, dk in (("y", "est"), ("lo", "lo"), ("hi", "hi")):
                    assert_close(self, bt(st[key]), st["display"][dk], 1e-9, "%s %s %s" % (m, st["label"], key))
                self.assertEqual(st["display_text"], P.display_text(st["display"]["est"], st["display"]["lo"],
                                                                    st["display"]["hi"]))
            for sm in doc["summaries"]:
                for key, dk in (("estimate", "est"), ("ci_lower", "lo"), ("ci_upper", "hi")):
                    assert_close(self, bt(sm[key]), sm["display"][dk], 1e-9, "%s %s" % (m, sm["id"]))
            for e in doc["loo"] + ((doc["cumulative"] or {}).get("entries") or []):
                assert_close(self, bt(e["estimate"]), e["display"]["est"], 1e-9, m)
            # null a megjelenítési skálán
            if doc["scale"]["null_analysis"] is not None:
                self.assertEqual(doc["scale"]["null_display"], E.back_transform(m, doc["scale"]["null_analysis"]))

    def test_scale(self):
        want = {"RR": ("log", True, 0.0, 1.0), "OR": ("log", True, 0.0, 1.0), "RD": ("identity", False, 0.0, 0.0),
                "MD": ("identity", False, 0.0, 0.0), "ROM": ("log", True, 0.0, 1.0),
                "ZCOR": ("atanh", False, 0.0, 0.0), "COR": ("identity", False, 0.0, 0.0),
                "PFT": ("pft", False, None, None), "PLO": ("logit", False, None, None),
                "PAS": ("asin_sqrt", False, None, None), "PLN": ("log", False, None, None),
                "PR": ("identity", False, None, None), "SMD": ("identity", False, 0.0, 0.0)}
        for path, opts in CASES:
            out, es, doc = self.doc(path, **opts)
            sc = doc["scale"]
            self.assertEqual((sc["analysis"], sc["ratio"], sc["null_analysis"], sc["null_display"]),
                             want[doc["measure"]], doc["measure"])

    def test_funnel_polygons(self):
        out, es, doc = self.doc(BCG, measure="RR")
        fu = doc["funnel"]
        self.assertEqual([c["p"] for c in fu["contours"]], [0.10, 0.05, 0.01])
        for c in fu["contours"]:
            poly = c["polygon"]
            self.assertEqual(poly[0], poly[-1])                      # zárt
            self.assertEqual(poly[0], [0.0, 0.0])                    # csúcs a nullhatáson, SE = 0
            self.assertEqual(len(poly), 4)
            for x, se in poly[1:3]:
                assert_close(self, se, fu["se_max"], 1e-15)
                assert_close(self, abs(x), c["z"] * fu["se_max"], 1e-12)
            self.assertEqual(set(c["band_text"]), {"hu", "en"})
        # pszeudo-CI a közös hatású becslés körül; se_max = max SE · 1.08 (az SVG-vel azonos)
        ses = [math.sqrt(v) for v in es.vi] + [math.sqrt(v) for v in out["bias"]["trimfill"].filled_vi]
        assert_close(self, fu["se_max"], max(ses) * 1.08, 1e-15)
        (x0, s0), (xc, sc), (x1, s1) = fu["pseudo_ci"]
        self.assertEqual((xc, sc), (out["fixed"].estimate, 0.0))
        assert_close(self, xc - x0, 1.96 * fu["se_max"], 1e-12)
        assert_close(self, x1 - xc, 1.96 * fu["se_max"], 1e-12)
        self.assertEqual(len(fu["filled"]), out["bias"]["trimfill"].k0)
        for f in fu["filled"]:
            self.assertIn(f["mirror_of"], [s["row_uid"] for s in doc["studies"]])
        self.assertIn("Harbord p", fu["tests_text"]["hu"])
        self.assertIn("jobb oldal", fu["tests_text"]["hu"])
        self.assertIn("right side", fu["tests_text"]["en"])
        # egycsoportos arány: nincs kontúr
        out, es, doc = self.doc(PRITZ, measure="PLO")
        self.assertEqual(doc["funnel"]["contours"], [])
        self.assertIsNone(doc["funnel"]["contour_center"])

    def test_display_text_verbatim_in_svgs(self):
        """Számhűség (terv 6.7): minden display_text szó szerint az adott nyelvű motor-SVG-ben."""
        for path, opts in CASES:
            out, es, doc = self.doc(path, **opts)
            for lang in ("hu", "en"):
                texts = svg_text(pipeline.make_plots(out, es, lang=lang)[0])
                for st in doc["studies"]:
                    self.assertIn(st["display_text"][lang], texts, (lang, st["label"]))
                for sm in doc["summaries"]:
                    self.assertIn(sm["display_text"][lang], texts, (lang, sm["id"]))
                    if sm.get("pi_text"):
                        self.assertIn("PI " + sm["pi_text"][lang], texts)
                    if sm["kind"] == "subgroup" and sm.get("het_text"):
                        self.assertIn(sm["het_text"][lang], texts)
                    self.assertIn(sm["label"][lang], texts)
                self.assertIn(doc["heterogeneity"]["text"][lang], texts)
                if doc["subgroup_test"]:
                    self.assertIn(doc["subgroup_test"]["text"][lang], texts)
            if "doi" in doc:
                for lang in ("hu", "en"):
                    self.assertIn(doc["doi"]["lfk_text"][lang], svg_text(pipeline.make_doi_plot(out, es, lang=lang)))

    def test_hu_text_is_todays_report_format(self):
        out, es, doc = self.doc(NORMAND, measure="MD")
        st = doc["studies"][0]
        self.assertEqual(st["display_text"]["hu"], P.fmt_triple(-20.0, st["display"]["lo"], st["display"]["hi"]))
        self.assertTrue(st["display_text"]["hu"].startswith("-20.00 ["))      # tizedespont, kötőjel-mínusz
        self.assertTrue(st["display_text"]["en"].startswith("−20.00 ["))
        self.assertEqual(st["display_text"]["en"].replace(P.MINUS, "-"), st["display_text"]["hu"])

    def test_flags_cells_and_source(self):
        p = self.write("f.csv", "study,e1,n1,e2,n2,rob,estimated,forrás oldal,Táblázat,PDF,study_id\n"
                                "A,4,123,11,139,low,nem,3,Table 1,a.pdf,s-a\n"
                                "B,0,50,1,48,Some concerns,igen,7,Fig 2,,s-b\n"
                                "C,12,100,3,40,Critical risk of bias,,,,,\n"
                                "D,5,100,9,100,magas,no,12.0,T3,d.pdf,s-d\n")
        out, es = analyze(p, measure="RR")
        doc = pipeline.plot_document(out, es)
        st = {s["label"]: s for s in doc["studies"]}
        self.assertEqual([st[k]["flags"]["rob"] for k in "ABCD"], ["low", "some", "critical", "high"])
        self.assertEqual([st[k]["flags"]["estimated"] for k in "ABCD"], [False, True, False, False])
        self.assertEqual([st[k]["flags"]["zero_cell_corrected"] for k in "ABCD"], [False, True, False, False])
        self.assertEqual(st["A"]["cells"], {"events1": "4/123", "events2": "11/139"})
        self.assertEqual([c["id"] for c in doc["columns"]], ["events1", "events2"])
        self.assertEqual(st["A"]["source"], {"doc": "a.pdf", "page": 3, "locator": "Table 1"})
        self.assertEqual(st["C"]["source"], {"doc": None, "page": None, "locator": None})
        self.assertEqual(st["D"]["source"]["page"], 12)
        self.assertEqual(st["A"]["study_id"], "s-a")
        self.assertIsNone(st["C"]["study_id"])
        out, es = analyze(BCG, measure="RR")
        self.assertNotIn("source", pipeline.plot_document(out, es)["studies"][0])
        out, es = analyze(NORMAND, measure="MD")
        self.assertEqual(pipeline.plot_document(out, es)["studies"][0]["cells"],
                         {"mean_sd1": "55 (47)", "n1": "155", "mean_sd2": "75 (64)", "n2": "156"})

    def test_influence_outlier_flags(self):
        out, es, doc = self.doc(BCG, measure="RR", outliers=True)
        infl = out["sensitivity"]["influence"]
        flagged = set(out["sensitivity"]["outliers"].flagged_index)
        for i, st in enumerate(doc["studies"]):
            self.assertEqual(st["flags"]["influential"], infl[i]["influential"])
            self.assertEqual(st["flags"]["outlier"], infl[i]["outlier_flag"] or i in flagged)
            self.assertEqual(doc["influence"][i]["rstudent"], infl[i]["rstudent"])
        self.assertTrue(any(st["flags"]["outlier"] for st in doc["studies"]))
        self.assertIn("rstudent", doc["influence_axes"])
        # k < 3: nincs LOO, befolyás, Doi
        p = self.write("k2.csv", "study,yi,vi\nA,0.2,0.04\nB,0.5,0.05\n")
        out, es, doc = self.doc(p, measure="GEN")
        self.assertEqual((doc["loo"], doc["influence"]), ([], []))
        self.assertNotIn("doi", doc)
        self.assertIsNone(doc["influence_axes"])

    def test_cumulative_only_when_computed(self):
        out, es, doc = self.doc(BCG, measure="RR")
        self.assertIsNone(doc["cumulative"])
        out, es, doc = self.doc(BCG, measure="RR", cumulative="év")
        cum = doc["cumulative"]
        self.assertEqual(cum["key_label"], {"hu": "év", "en": "év"})
        self.assertEqual([e["k"] for e in cum["entries"]], list(range(1, len(es) + 1)))
        self.assertEqual(sorted(e["added_row_uid"] for e in cum["entries"]),
                         sorted(s["row_uid"] for s in doc["studies"]))
        by_uid = {s["row_uid"]: s["label"] for s in doc["studies"]}
        for e in cum["entries"]:
            self.assertEqual(by_uid[e["added_row_uid"]], e["label"])
        self.assertEqual(cum["entries"][0]["key_text"], "1948")
        self.assertEqual(cum["entries"][0]["display_text"], doc["studies"][0]["display_text"])

    def test_meta_and_run_info(self):
        out, es, doc = self.doc(BCG, measure="RR")
        with open(BCG, "rb") as fh:
            self.assertEqual(doc["meta"]["data_sha256"], hashlib.sha256(fh.read()).hexdigest())
        self.assertIsNone(doc["meta"]["run_id"])
        info = {"run_id": "20261004T211200Z-a1f3c2", "spec_sha256": "ab" * 32, "data_sha256": "cd" * 32}
        doc = pipeline.plot_document(out, es, run_info=info)
        self.assertEqual(doc["meta"], dict(info, engine_version=out["engine"]["version"]))
        od = os.path.join(self.tmp, "o")
        paths = pipeline.write_outputs(out, es, od, run_info=info)
        with open(paths["plot_data.json"], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["meta"]["run_id"], info["run_id"])

    def test_locale_preference(self):
        out, es = analyze(NORMAND, measure="MD", plot_locale="en")
        doc = pipeline.plot_document(out, es)
        self.assertEqual(doc["display_locale"], "en")
        neg = [t for t in doc["axis"]["ticks"] if t["at"] < 0]
        self.assertTrue(neg and all(t["text"].startswith(P.MINUS) for t in neg))
        f_svg = pipeline.make_plots(out, es)[0]          # az opció az SVG nyelvét is adja
        self.assertIn(">Study<", f_svg)
        self.assertIn(">Heterogeneity: Q = ", f_svg)


# ------------------------------------------------------------------ SVG: nyelv és annotáció
class TestSvgLanguageAndLayers(_Tmp):
    def test_english_labels_and_minus(self):
        out, es = analyze(BCG, measure="RD", subgroup="allokáció")
        f_svg, fu_svg, data = pipeline.make_plots(out, es, lang="en")
        doi = pipeline.make_doi_plot(out, es, lang="en")
        for svg in (f_svg, fu_svg, doi):
            xml.dom.minidom.parseString(svg.encode("utf-8"))
            for hu in ("Vizsgálat", "Súly", "Alcsoport", "Heterogenitás", "ábrázolva", "Standard hiba", "Sávok",
                       "pontszám", "LFK-index", "Véletlen hatású"):
                self.assertNotIn(hu, svg)
        texts = svg_text(f_svg)
        for en in ("Study", "Weight", "Subtotal (k = 7)", "Test for subgroup differences: Q_b = "):
            self.assertTrue(any(t.startswith(en) for t in texts), en)
        self.assertTrue(any(t.startswith("Random-effects model (REML, HKSJ)") for t in texts))
        nums = [t for t in texts if re.match(r"^[−\-]?\d", t)]
        self.assertTrue(any(P.MINUS in t for t in nums))
        self.assertFalse(any(re.search(r"(^|[\s\[;])-\d", t) for t in texts), "kötőjel-mínusz az angol ábrán")
        self.assertIn("Standard error (analysis scale)", fu_svg)
        self.assertIn("LFK index: −", doi)
        self.assertIn("major asymmetry", doi)
        with self.assertRaises(ValueError):
            pipeline.make_plots(out, es, lang="de")
        with self.assertRaises(ValueError):
            P.forest_svg(data, lang="xx")

    def test_annotated_forest_rows(self):
        out, es = analyze(BCG, measure="RR", subgroup="allokáció")
        doc = pipeline.plot_document(out, es)
        f_svg = pipeline.make_plots(out, es, annotate=True)[0]
        dom = xml.dom.minidom.parseString(f_svg.encode("utf-8"))
        groups = {g.getAttribute("id"): g for g in dom.getElementsByTagName("g")}
        for layer in ("header", "null", "studies", "subgroups", "summaries", "axis", "footer"):
            self.assertIn("layer-forest-" + layer, groups)
        for st in doc["studies"]:
            g = groups["study-" + st["row_uid"]]
            self.assertEqual(int(g.getAttribute("data-row")), st["row_index"])
            for key in ("y", "lo", "hi"):
                self.assertEqual(float(g.getAttribute("data-" + key)), st[key])
            self.assertEqual(g.parentNode.getAttribute("id"), "layer-forest-studies")
            texts = ["".join(n.data for n in t.childNodes) for t in g.getElementsByTagName("text")]
            self.assertEqual(texts[0], st["label"])
            self.assertIn(st["display_text"]["en"], texts)          # annotált: U+2212
        fu_svg = pipeline.make_plots(out, es, annotate=True)[1]
        dom = xml.dom.minidom.parseString(fu_svg.encode("utf-8"))
        ids = [g.getAttribute("id") for g in dom.getElementsByTagName("g")]
        self.assertIn("layer-funnel-contours", ids)
        self.assertEqual([i for i in ids if i.startswith("funnel-study-")],
                         ["funnel-study-" + s["row_uid"] for s in doc["studies"]])
        doi = pipeline.make_doi_plot(out, es, annotate=True)
        self.assertEqual(sorted(re.findall(r'id="doi-study-(r[0-9a-z]+)"', doi)),
                         sorted(s["row_uid"] for s in doc["studies"]))

    def test_option_drives_written_svgs(self):
        out, es = analyze(BCG, measure="RR", svg_annotate=True, plot_locale="en")
        paths = pipeline.write_outputs(out, es, os.path.join(self.tmp, "o"))
        with open(paths["forest.svg"], encoding="utf-8") as fh:
            svg = fh.read()
        self.assertIn('<g id="layer-forest-studies">', svg)
        self.assertIn(">Study<", svg)
        with open(paths["plot_data.json"], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["display_locale"], "en")


# ------------------------------------------------------------------ szerződés-séma
class TestPlotV2Schema(unittest.TestCase):
    def test_conforms_to_contract_schema(self):
        if not os.path.isfile(SCHEMA_PATH):
            self.skipTest("a metaelemzes/contracts/ma.plot.v2.schema.json még nincs meg (a szerződés-ág írja); "
                          "a szerkezeti invariánsokat a többi teszt ellenőrzi")
        lite = os.path.join(ROOT, "ma_gui", "schema_lite.py")
        if not os.path.isfile(lite):
            self.skipTest("nincs stdlib JSON-Schema-validátor (ma_gui/schema_lite.py)")
        spec = importlib.util.spec_from_file_location("_schema_lite_for_plot_test", lite)
        sl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sl)
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            schema = json.load(fh)
        registry = sl.load_schema_dir(os.path.dirname(SCHEMA_PATH))
        try:
            sl.check_schema(schema, registry)
        except sl.SchemaError as exc:
            self.skipTest("a séma a stdlib-validátorral nem ellenőrizhető: %s" % exc)
        for path, opts in CASES:
            out, es = analyze(path, **opts)
            doc = strict_json(json.dumps(pipeline.to_jsonable(pipeline.plot_document(out, es)), ensure_ascii=False))
            errors = sl.validate(doc, schema, registry)
            self.assertEqual([str(e) for e in errors], [], opts)


if __name__ == "__main__":
    unittest.main()
