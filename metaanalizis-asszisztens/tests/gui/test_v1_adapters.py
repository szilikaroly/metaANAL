# -*- coding: utf-8 -*-
"""Plugin-adapterek (terv 4.11–4.13, 5.0 H1–H5/H7, 5.1–5.5, 6.5, 6.7, 8.3): stub-pluginokkal, minden állapotban.

- ``svgaudit``: stdlib-audit és a szerver számhűség-újraellenőrzése a motor valódi SVG-jén (BCG commit-futás).
- validator: a bridge-mód golden-kimenetekből (1.0.0) — H1 (üres TRIPOD → 1/52), H2 (csak fejlesztési menet →
  32/34), H3 („Strongly suspected” → HIGH), H4 („PY” vs „Partial yes”) reprodukciója, és hogy az őrök kijavítják;
  json-mód (V1) utólagos őrökkel, a pluginnak átadott bemenet szöveg nélkül; absent/unusable → 424.
- figure-forge: H5 (modulszintű és futásközbeni ModuleNotFoundError → unusable, pontos teendő), legacy audit (a fájl
  mellé írt .editability.json a tmp-ben), F1 json-audit, F2 meta-export (fejléc- és út-ellenőrzés).
- composer: explicit interpreter (nem futtatható, abszolút shebangú szkript — H7), csonka állapot → újrapróbálás,
  majd érthető hiba; leképezés szk.prisma-flow/v1-re; status-figyelmeztetések; az állapotot SOHA nem írja.
- végpontok: /api/adapters (állapot × funkció, magyar teendők), /api/figures(/export, /audit), /api/validator/check,
  /api/prisma/composer(/config, /refresh) — a válaszokat a szerver a saját sémáival is ellenőrzi; naplóban érték nincs."""
import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _adapters_stubs as STUBS  # noqa: E402
import test_routes_harness as H  # noqa: E402

from ma_gui import caps as caps_mod  # noqa: E402
from ma_gui.adapters import composer as C  # noqa: E402
from ma_gui.adapters import figureforge as F  # noqa: E402
from ma_gui.adapters import svgaudit as A  # noqa: E402
from ma_gui.adapters import validator as V  # noqa: E402
from ma_gui.routes import adapters_figures as RF  # noqa: E402
from metaelemzes import api  # noqa: E402

GOLDEN = STUBS.GOLDEN
INSTR = os.path.join(H.ROOT, "metaelemzes", "instruments")


def _rb(path):
    with open(path, "rb") as fh:
        return fh.read()


def _rt(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _rj(path):
    return json.loads(_rt(path))


DOCS = _rj(os.path.join(HERE, "adapters_golden", "docs.json"))


def _instrument(tool):
    with open(os.path.join(INSTR, tool + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def _golden(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as fh:
        return fh.read()


def _commit(proj):
    os.makedirs(os.path.join(proj, "05_elemzes", "specs"), exist_ok=True)
    path = os.path.join(proj, "05_elemzes", "specs", "o1_primary.json")
    api.save_spec(path, H.spec())
    with contextlib.redirect_stdout(io.StringIO()):
        view = api.analyze(path, mode="commit", project_root=proj)
    return view["run"]


def _caps(tmp, plugin_base, name="c", **env):
    e = {"MA_GUI_PLUGIN_DIRS": plugin_base, "PATH": ""}
    e.update(env)
    return caps_mod.Caps(runtime_dir=os.path.join(tmp, "caps_" + name), env=e, home=os.path.join(tmp, "nohome"),
                         specs=STUBS.caps_specs())


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = H.tmpdir("ma_adp_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ============================================================================ svgaudit
class SvgAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_adp_svg_")
        proj, _home = H.make_project(cls.tmp)
        run = _commit(proj)
        files = run["files"]
        cls.svg = {k: _rb(os.path.join(proj, files[k]["path"])) for k in ("forest", "funnel", "doi")}
        cls.plot = _rj(os.path.join(proj, files["plot"]["path"]))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_engine_svgs_pass_number_fidelity(self):
        for kind in ("forest", "funnel", "doi"):
            res = A.numbers_check_svg(self.svg[kind], self.plot, kind)
            self.assertTrue(res["ok"], (kind, res["mismatches"]))
            self.assertGreater(res["checked"], 5, kind)
        forest = A.numbers_check_svg(self.svg["forest"], self.plot, "forest")
        # minden vizsgálat display_text-je + súlya, az összesítők, a PI és a tengelyosztások
        k = len(self.plot["studies"])
        self.assertGreaterEqual(forest["checked"], 2 * k + 2)

    def test_tampered_number_is_caught(self):
        st = self.plot["studies"][0]["display_text"]["hu"]
        bad = self.svg["forest"].replace(st.encode("utf-8"), b"0.42 [0.13; 1.26]")
        res = A.numbers_check_svg(bad, self.plot, "forest")
        self.assertFalse(res["ok"])
        self.assertEqual(res["mismatches"][0]["field"], "studies[0].display_text")

    def test_minus_sign_normalised_both_ways(self):
        plot = {"doi": {"lfk_text": {"hu": "LFK-index: -4.10", "en": "LFK index: −4.10"}}}
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>LFK index: −4.10</text></svg>'
        self.assertTrue(A.numbers_check_svg(svg, plot, "doi")["ok"])
        svg2 = '<svg xmlns="http://www.w3.org/2000/svg"><text>LFK-index: -4.10</text></svg>'
        self.assertTrue(A.numbers_check_svg(svg2, plot, "doi")["ok"])

    def test_ticks_need_exact_element(self):
        plot = {"funnel": {"axis": {"ticks": [{"text": "1"}]}}}
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>12</text></svg>'
        self.assertFalse(A.numbers_check_svg(svg, plot, "funnel")["ok"])

    def test_stdlib_audit(self):
        a = A.audit_svg(self.svg["doi"])
        self.assertTrue(a["editable"])
        self.assertEqual(a["outlined_text_groups"], 0)
        self.assertTrue(a["font_fallback_stack"])
        self.assertIsNone(a["glyphs"])
        sug = a["typography"]["suggestions"]
        self.assertTrue(any("−4.10" in s["to"] for s in sug), sug)       # kötőjel-mínusz → U+2212 javaslat
        out = A.audit_svg('<svg xmlns="http://www.w3.org/2000/svg"><g id="text_1"><path d="M0 0"/></g>'
                          '<g id="layer-rows"><text>IL-6 COVID-19 2019-2021</text></g></svg>')
        self.assertFalse(out["editable"])
        self.assertEqual(out["outlined_text_groups"], 1)
        self.assertEqual(out["named_layers"], 1)
        self.assertEqual(out["typography"]["suggestions"][0]["to"], "IL-6 COVID-19 2019–2021")

    def test_rejects_entities_and_non_svg(self):
        for bad in (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "x">]><svg/>', b"<html/>", b"", b"<svg"):
            with self.assertRaises(A.SvgError):
                A.audit_svg(bad)


# ============================================================================ validator (golden, bridge)
class ValidatorGoldenTests(unittest.TestCase):
    """A validator 1.0.0 rögzített kimenetei: a hibák (H1–H4) ott vannak, az adapter olvasata helyes."""

    def test_skeleton_slots(self):
        self.assertEqual(len(V.parse_skeleton(_golden("rob2.skeleton.txt"))), 22)
        prob = V.parse_skeleton(_golden("probast_dev.skeleton.txt"), checklist=True)
        self.assertEqual(len(prob), 34)
        self.assertEqual(sum(1 for s in prob if s["pass"] == "development"), 16)
        self.assertEqual(V.slot_key(prob[0]), "development/1.1")
        self.assertEqual(len(V.parse_skeleton(_golden("tripod_empty.skeleton.txt"), checklist=True)), 52)
        rob = V.parse_skeleton(_golden("rob2.skeleton.txt"))
        self.assertEqual({s["domain"] for s in rob}, {"1", "2", "3", "4", "5"})

    def test_h1_h2_reproduced_in_golden(self):
        self.assertEqual(V.parse_verify(_golden("tripod_empty.verify.txt"))["answered"], 1)      # H1: 1/52 üresen
        p = V.parse_verify(_golden("probast_dev.verify.txt"))
        self.assertEqual((p["answered"], p["expected"]), (32, 34))                              # H2: 32/34
        self.assertEqual(p["unanswered"], ["evaluation/4.6", "evaluation/4.7"])

    def test_h3_h4_reproduced_in_golden(self):
        self.assertEqual(V.parse_rollup_grade(_golden("grade_strong.rollup.txt"))["certainty"], "high")   # H3
        py = V.parse_rollup_amstar2(_golden("amstar2_PYtoken.rollup.txt"))
        partial = V.parse_rollup_amstar2(_golden("amstar2_py.rollup.txt"))
        self.assertEqual(py["rating"], "high")                     # „PY” → probably yes → nem gyengeség (H4)
        self.assertEqual(partial["rating"], "moderate")            # „Partial yes” → gyengeség (weakness konvenció)
        self.assertEqual(partial["weaknesses"], ["2", "4"])
        self.assertTrue(partial["provisional"])

    def test_signalling_rollup_parse(self):
        doms, overall, lines = V.parse_rollup_signalling(_golden("rob2.rollup.txt"))
        by = {d["domain"]: d for d in doms}
        self.assertEqual(by["2"]["level"], "high")
        self.assertEqual(by["2"]["forced_by"], ["2.6"])
        self.assertEqual(by["2"]["routers"], ["2.1", "2.2", "2.3", "2.4"])
        self.assertEqual(by["3"]["level"], "some")
        self.assertEqual(by["3"]["unknown_at"], ["3.1"])
        self.assertEqual(overall, "high")
        self.assertTrue(any("NOT the published flowchart" in ln for ln in lines))

    def test_markdown_never_leaks_vocabulary_or_py(self):
        doc = DOCS["amstar2_py"]
        slots = V.parse_skeleton(_golden("amstar2_py.skeleton.txt"))
        md, sent, invalid, skipped = V.bridge_markdown("amstar2", slots, V._values(doc))
        self.assertIn("| Partial yes |", md)
        self.assertNotIn("| PY |", md)
        self.assertEqual(skipped, ["11"])                         # N/A: az 1.0.0 hibának számolná → üresen
        for line in md.splitlines():
            if line.startswith("| ") and not line.startswith("| #"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                self.assertEqual(cells[1], "—")                   # kérdés-oszlop
                self.assertRegex(cells[3], r"^(\[E\d+\])?$")      # bizonyíték: csak hivatkozás
        tri = V.bridge_markdown("tripod-ai", V.parse_skeleton(_golden("tripod_empty.skeleton.txt"), True),
                                {"11": "missing", "1": "present"})[0]
        self.assertNotIn("missing data", tri.lower())             # H1: a tétel címe nem kerül a sorba

    def test_check_doc_and_minimal_doc(self):
        for bad in (None, {"tool": "x!"}, {"tool": "rob2"}, {"tool": "rob2", "answers": {"../x": {}}},
                    {"tool": "rob2", "answers": {"1.1": {"value": 3}}}, {"tool": "nincs", "answers": {}}):
            with self.assertRaises(ValueError):
                V.check_doc(bad)
        doc = {"tool": "rob2", "answers": {"1.1": {"value": "yes", "evidence": {"text": "TITOK 123456788"},
                                                   "comment": "titok", "rationale": {"because": "titok"}}},
               "overall": {"judgement": "low", "rationale": "titok"}}
        mini = json.dumps(V.minimal_json_doc(doc))
        self.assertNotIn("titok", mini.lower())
        self.assertIn('"yes"', mini)


# ============================================================================ validator (stub-futtatással)
class ValidatorAdapterTests(_Tmp):
    def adapter(self, cfg, name="v"):
        base = STUBS.make_plugins(os.path.join(self.tmp, "plugins_" + name), validator=cfg)
        return V.ValidatorAdapter(_caps(self.tmp, base, name))

    def test_legacy_bridge_guards(self):
        ad = self.adapter({"mode": "legacy"})
        st = ad.status()
        self.assertEqual((st["state"], st["mode"]), ("legacy", "bridge"))
        self.assertEqual(st["guards"], ["H1", "H2", "H3", "H4"])
        self.assertIn("V1", st["remedy"]["hu"])
        # RoB 2: implikált ítélet a validatortól, a motor szintjeire (tiers) képezve, NEM hivatalos
        res = ad.check(DOCS["rob2"], instrument=_instrument("rob2"))
        self.assertTrue(res["ok"], res)
        d = res["data"]
        self.assertEqual((d["mode"], d["legacy"]), ("bridge", True))
        self.assertEqual(d["overall"]["algorithm"], "conservative")
        self.assertFalse(d["overall"]["official"])
        self.assertEqual(d["overall"]["implied"], "high")
        self.assertEqual({x["domain"]: x["implied"] for x in d["domains"]}["3"], "some_concerns")
        self.assertEqual(d["completeness_text"], "21/22")
        self.assertTrue(d["validator_reported"]["agrees"])
        self.assertEqual({x["domain"]: x.get("missing") for x in d["domains"]}["5"], ["5.3"])
        # PROBAST+AI — H2: a munkapad 16/34-et számol (menettel minősítve), a validator 32/34-et
        d = ad.check(DOCS["probast_dev"])["data"]
        self.assertEqual((d["answered"], d["expected"]), (16, 34))
        self.assertEqual(d["per_pass"]["evaluation"]["text"], "0/18")
        self.assertFalse(d["validator_reported"]["trusted"])
        self.assertEqual(d["validator_reported"]["answered"], 32)
        self.assertEqual([g["id"] for g in d["guards"]], ["H2"])
        self.assertEqual(d["overall"]["algorithm"], "none")
        # TRIPOD+AI — H1: üresen a validator 1/52-t mond, a munkapad 0/52-t
        d = ad.check(DOCS["tripod_empty"])["data"]
        self.assertEqual(d["answered"], 0)
        self.assertEqual(d["validator_reported"]["answered"], 1)
        self.assertEqual([g["id"] for g in d["guards"]], ["H1"])
        # GRADE — H3: „Strongly suspected” mellett a validator HIGH-ja nem megbízható → certainty null
        d = ad.check(DOCS["grade_strong"])["data"]
        self.assertIsNone(d["grade"]["certainty"])
        self.assertEqual(d["grade"]["validator_certainty"], "high")
        self.assertFalse(d["grade"]["reliable"])
        self.assertEqual(d["grade"]["unresolved"], [])
        self.assertIn("H3", [g["id"] for g in d["guards"]])
        sus = copy.deepcopy(DOCS["grade_strong"])
        sus["answers"]["5.1"] = {"value": "suspected"}
        d = ad.check(sus)["data"]
        self.assertEqual(d["grade"]["unresolved"], ["publication_bias"])       # 4. döntés: feloldatlan
        sus["answers"]["5.1"]["resolution"] = {"step": -1, "rationale": "aszimmetrikus funnel"}
        d = ad.check(sus)["data"]
        self.assertEqual(d["grade"]["unresolved"], [])
        self.assertIsNone(d["grade"]["certainty"])                            # a motor számol, nem a validator
        # AMSTAR 2 — H4: weakness konvencióként címkézve, N/A üresen átadva (ideiglenes)
        d = ad.check(DOCS["amstar2_py"])["data"]
        self.assertEqual(d["amstar2"]["convention"], "weakness")
        self.assertEqual(d["amstar2"]["rating"], "moderate")
        self.assertTrue(d["amstar2"]["provisional"])
        self.assertEqual(d["conventions"]["amstar2.partial_yes_critical"], "weakness")
        self.assertIn("H4", [g["id"] for g in d["guards"]])
        self.assertTrue(d["complete"])                                         # a munkapad N/A-t válasznak vesz

    def test_json_mode_post_guards_and_privacy(self):
        capture = os.path.join(self.tmp, "captured.json")
        result = {"tool": "grade", "complete": True, "expected": 9, "answered": 9, "missing": [], "invalid": [],
                  "domains": [], "overall": {"implied": "high", "algorithm": "published"},
                  "grade": {"certainty": "high", "unresolved": []}}
        ad = self.adapter({"mode": "json", "version": "1.1.0", "capture": capture,
                           "known_issues": [{"id": "H3", "summary": "x", "fixed_in": "1.2.0"}],
                           "results": {"grade": result, "rob2": dict(result, tool="rob2", grade=None)}})
        self.assertEqual(ad.mode("grade"), "json")
        doc = copy.deepcopy(DOCS["grade_strong"])
        doc["answers"]["1.1"]["evidence"] = {"text": "SZÓ SZERINTI IDÉZET"}
        doc["answers"]["1.1"]["rationale"] = {"because": "TITKOS INDOKLÁS"}
        d = ad.check(doc)["data"]
        self.assertEqual(d["mode"], "json")
        self.assertIsNone(d["grade"]["certainty"])                             # H3 még él az 1.1.0-ban
        self.assertEqual(d["grade"]["validator_certainty"], "high")
        sent = _rt(capture)
        self.assertNotIn("IDÉZET", sent)
        self.assertNotIn("INDOKLÁS", sent)
        self.assertIn("strongly_suspected", sent)
        d = ad.check(DOCS["rob2"])["data"]
        self.assertEqual(d["guards"], [])

    def test_unavailable(self):
        for cfg in (None, {"mode": "unusable", "missing": ["yaml"]}):
            base = STUBS.make_plugins(os.path.join(self.tmp, "p_un"), validator=cfg)
            ad = V.ValidatorAdapter(_caps(self.tmp, base, "un%s" % bool(cfg)))
            res = ad.check(DOCS["rob2"])
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"]["code"], "CAPABILITY_MISSING")
            self.assertIsNotNone(ad.status()["remedy"])


# ============================================================================ figure-forge
class FigureForgeAdapterTests(_Tmp):
    SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">'
           b'<text>Aronson 1948</text><text>-0.41</text></svg>')

    def adapter(self, cfg, name="f"):
        base = STUBS.make_plugins(os.path.join(self.tmp, "plugins_" + name), figure_forge=cfg)
        return F.FigureForgeAdapter(_caps(self.tmp, base, name))

    def test_h5_module_level_import(self):
        ad = self.adapter({"mode": "h5"})
        st = ad.status()
        self.assertEqual(st["state"], "unusable")
        self.assertFalse(st["audit"]["available"])
        self.assertEqual(st["audit"]["guard"], "H5")
        remedy = st["audit"]["remedy"]["hu"]
        self.assertIn("matplotlib", remedy)
        self.assertIn("-m pip install matplotlib", remedy)
        self.assertIn("FIGURE_FORGE_PYTHON", remedy)
        self.assertIn(sys.executable, remedy)                               # a pontos interpreter
        res = ad.audit(self.SVG, "x.svg")
        self.assertEqual(res["error"]["code"], "CAPABILITY_MISSING")
        self.assertEqual(res["error"]["details"]["guard"], "H5")
        self.assertEqual(res["error"]["details"]["missing"], ["matplotlib"])

    def test_h5_at_runtime(self):
        ad = self.adapter({"mode": "legacy", "audit_h5": True})
        self.assertEqual(ad.status()["audit"]["mode"], "bridge")
        res = ad.audit(self.SVG, "x.svg")
        self.assertEqual(res["error"]["code"], "CAPABILITY_MISSING")
        self.assertEqual(res["error"]["details"]["guard"], "H5")
        self.assertIn("pip install matplotlib", res["error"]["message"])

    def test_legacy_audit_reads_sidecar_in_tmp(self):
        ad = self.adapter({"mode": "legacy"})
        st = ad.status()
        self.assertEqual((st["state"], st["audit"]["mode"], st["export"]["mode"]), ("legacy", "bridge", None))
        self.assertIn("H6", st["export"]["remedy"]["hu"])
        res = ad.audit(self.SVG, "../../etc/passwd.svg")
        self.assertTrue(res["ok"], res)
        d = res["data"]
        self.assertEqual((d["source"], d["mode"], d["editable"], d["text_elements"]), ("figure-forge", "bridge", True, 2))
        self.assertEqual(d["typography"]["suggestions_n"], 1)
        self.assertNotIn("labels", d)
        tmp_root = ad.caps.tmp_dir()
        self.assertEqual([n for n in os.listdir(str(tmp_root)) if n.startswith("ff-audit-")], [])   # takarítva

    def test_f1_json_audit_and_failure(self):
        ad = self.adapter({"mode": "f1", "version": "0.3.0"})
        self.assertEqual(ad.audit_mode(), "json")
        self.assertIsNone(ad.export_mode())
        self.assertTrue(ad.audit(self.SVG)["ok"])
        bad = self.adapter({"mode": "f1", "version": "0.3.0", "audit_fail": True}, "f1b")
        res = bad.audit(self.SVG)
        self.assertEqual(res["error"]["code"], "PLUGIN_FAILED")
        self.assertIn("audit boom", res["error"]["details"]["stderr_tail"])

    def test_absent(self):
        ad = self.adapter(None)
        st = ad.status()
        self.assertEqual(st["state"], "absent")
        self.assertIn("claude plugin install figure-forge", st["audit"]["remedy"]["hu"])
        self.assertEqual(ad.audit(self.SVG)["error"]["code"], "CAPABILITY_MISSING")

    def test_converter_probe_and_conversion(self):
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        good = os.path.join(bindir, "rsvg-convert")
        with open(good, "w") as fh:
            fh.write("#!%s\nimport sys\nout = sys.argv[sys.argv.index('-o') + 1]\nfmt = sys.argv[sys.argv.index('-f') + 1]\n"
                     "open(out, 'wb').write(b'\\x89PNG\\r\\n\\x1a\\nX' if fmt == 'png' else b'%%PDF-1.4 X')\n"
                     % sys.executable)
        os.chmod(good, 0o755)
        bad = os.path.join(bindir, "magick")
        with open(bad, "w") as fh:
            fh.write("#!%s\nimport sys\nsys.exit(1)\n" % sys.executable)
        os.chmod(bad, 0o755)
        convs = F.find_converters({"PATH": bindir}, self.tmp)
        self.assertEqual({c["name"]: c["ok"] for c in convs}, {"rsvg-convert": True, "magick": False})
        data, name = F.convert_svg(self.SVG, "png", 600, {"PATH": bindir}, self.tmp)
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertEqual(name, "rsvg-convert")
        self.assertEqual(F.convert_svg(self.SVG, "tiff", 600, {"PATH": bindir}, self.tmp), (None, None))
        self.assertEqual(F.find_converters({"PATH": ""}, self.tmp), [])


# ============================================================================ composer
class ComposerAdapterTests(_Tmp):
    def setUp(self):
        super().setUp()
        self.outdir = os.path.join(self.tmp, "PubMed_Downloads")
        self.state = STUBS.composer_state(self.outdir)

    def adapter(self, cfg, name="c"):
        base = STUBS.make_plugins(os.path.join(self.tmp, "plugins_" + name), composer=cfg)
        ad = C.ComposerAdapter(_caps(self.tmp, base, name))
        ad.sleep = lambda s: None
        return ad, base

    def test_explicit_interpreter_and_mapping(self):
        ad, base = self.adapter({"mode": "legacy"})
        script = os.path.join(base, "composer", "scripts", "prisma")
        self.assertFalse(os.access(script, os.X_OK))
        with open(script, encoding="utf-8") as fh:
            self.assertTrue(fh.readline().startswith("#!/Users/szili/anaconda3"))   # H7: a shebang nem él
        before = hashlib.sha256(_rb(self.state)).hexdigest()
        res = ad.export_flow(self.outdir, "glp1")
        self.assertTrue(res["ok"], res)
        flow = res["data"]["flow"]
        self.assertEqual(flow["schema"], "szk.prisma-flow/v1")
        self.assertEqual(flow["included_reports"], 25)                # included = J (jelentések), H7/C1
        self.assertIsNone(flow["included_studies"])
        self.assertEqual(flow["excluded_eligibility_reasons"], {"wrong population": 30, "wrong outcome": 27})
        self.assertNotIn("databases", flow)                          # keresőkifejezések nem kerülnek át
        self.assertEqual(flow["composer_version"], "1.4.1")
        self.assertEqual(res["data"]["mode"], "bridge")
        self.assertEqual(hashlib.sha256(_rb(self.state)).hexdigest(), before)   # csak olvas
        chk = api.prisma_check(flow)
        self.assertIn("findings", chk)
        warns = ad.status_warnings(self.outdir, "glp1")["data"]["warnings"]
        self.assertEqual([(w["code"], w.get("n")) for w in warns], [("retmax", 12), ("pending_5d", 3)])
        self.assertTrue(warns[0]["hu"].startswith("FIGYELEM: 12 azonosított rekord"))

    def test_truncated_state_retries_then_clear_error(self):
        ad, base = self.adapter({"mode": "legacy", "truncated_times": 2}, "t2")
        res = ad.export_flow(self.outdir, "glp1")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["data"]["attempts"], 3)
        ad, base = self.adapter({"mode": "legacy", "truncated_times": 9}, "t9")
        res = ad.export_flow(self.outdir, "glp1")
        self.assertEqual(res["error"]["code"], "PLUGIN_FAILED")
        self.assertEqual(res["error"]["details"]["guard"], "H7")
        self.assertIn("csonka", res["error"]["message"])

    def test_missing_state_and_bad_names(self):
        ad, _ = self.adapter({"mode": "legacy"})
        res = ad.export_flow(self.outdir, "nincs-ilyen")
        self.assertEqual((res["error"]["code"], res["error"]["http"]), ("NOT_FOUND", 404))
        for bad in ("-x", "../x", "a b", ""):
            with self.assertRaises(ValueError):
                ad.export_flow(self.outdir, bad)
        loc = C.location({"composer": {"outdir": self.outdir, "project": "nincs"}})
        self.assertFalse(loc["state_exists"])
        self.assertIn("prisma init --project nincs", loc["problems"][0]["hu"])

    def test_json_mode_and_unusable(self):
        STUBS.composer_state(self.outdir, warnings_json=[{"code": "retmax", "n": 12, "message": {"hu": "h", "en": "e"}}])
        ad, _ = self.adapter({"mode": "json", "version": "1.5.0"}, "j")
        self.assertEqual(ad.mode(), "json")
        self.assertEqual(ad.status_warnings(self.outdir, "glp1")["data"]["warnings"][0]["en"], "e")
        ad, _ = self.adapter({"mode": "unusable", "missing": ["requests"]}, "u")
        self.assertEqual(ad.export_flow(self.outdir, "glp1")["error"]["code"], "CAPABILITY_MISSING")

    def test_map_flow_validation(self):
        good = dict(STUBS.FLOW_JSON)
        self.assertEqual(C.map_flow(good)["included_reports"], 25)
        c1 = dict(good, schema="szk.prisma-flow/v1", included_reports=25, included_studies=18)
        self.assertEqual(C.map_flow(c1)["included_studies"], 18)
        for bad in (dict(good, screened=-1), dict(good, screened=True), dict(good, screened="946"),
                    dict(good, excluded_eligibility_reasons={"x": -2}), dict(good, excluded_eligibility_reasons=[1]),
                    {"foo": 1}, [], dict(good, schema="szk.prisma-flow/v9")):
            with self.assertRaises(ValueError):
                C.map_flow(bad)

    def test_parse_status_texts(self):
        warns = C.parse_status(STUBS.STATUS_TEXT + "  ! Valami más\n")
        self.assertEqual([w["code"] for w in warns], ["retmax", "pending_5d", "other"])


# ============================================================================ végpontok
class _RouteBase(_Tmp):
    """Élő szerver a stub-pluginokkal és egy BCG commit-futással."""
    FF = None
    VAL = None
    COMP = None
    ENV = None

    def setUp(self):
        super().setUp()
        self.proj, self.home = H.make_project(self.tmp)
        self.run = _commit(self.proj)
        self.rid = self.run["run_id"]
        self.plugins = STUBS.make_plugins(os.path.join(self.tmp, "plugins"), figure_forge=self.FF,
                                          validator=self.VAL, composer=self.COMP)
        self.outdir = os.path.join(self.tmp, "PubMed_Downloads")
        STUBS.composer_state(self.outdir)
        env = dict(self.ENV or {})
        self.srv = H.Srv(self.proj, self.home, self.tmp, caps=_caps(self.tmp, self.plugins, "srv", **env))

    def tearDown(self):
        self.srv.app.shutdown()
        self.srv.app.close()
        super().tearDown()

    def call(self, *a, **kw):
        return self.srv.call(*a, **kw)

    def assert_log_clean(self, *values):
        log = self.srv.log.getvalue()
        act = _rt(os.path.join(self.proj, "07_ellenorzes", "activity.jsonl")) \
            if os.path.isfile(os.path.join(self.proj, "07_ellenorzes", "activity.jsonl")) else ""
        for v in values:
            self.assertNotIn(v, log)
            self.assertNotIn(v, act)


class AdaptersMatrixRoutes(unittest.TestCase):
    """A 16 állapot-kombináció lényege: absent / unusable / legacy / ok minden pluginra — a funkciók állapota és a
    magyar teendő megjelenik."""

    def setUp(self):
        self.tmp = H.tmpdir("ma_adp_mx_")
        self.proj, self.home = H.make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def report(self, ff, val, comp, name):
        base = STUBS.make_plugins(os.path.join(self.tmp, "plugins_" + name), figure_forge=ff, validator=val,
                                  composer=comp)
        srv = H.Srv(self.proj, self.home, self.tmp, name=name, caps=_caps(self.tmp, base, name))
        try:
            st, _h, env = srv.call("GET", "/api/adapters")
            self.assertEqual(st, 200, env)
            return {f["id"]: f for f in env["data"]["features"]}, env["data"]
        finally:
            srv.app.shutdown()
            srv.app.close()

    def test_states(self):
        feats, data = self.report(None, None, None, "absent")
        for fid in ("rob", "probast", "tripod", "grade", "amstar2", "figure_audit", "pub_figure", "prisma"):
            self.assertEqual(feats[fid]["state"], "absent", fid)
            self.assertTrue(feats[fid]["remedy"]["hu"], fid)
        self.assertIn("claude plugin install", feats["rob"]["remedy"]["hu"])
        self.assertTrue(data["converter_remedy"]["hu"].startswith("PNG/PDF"))
        feats, _ = self.report({"mode": "h5"}, {"mode": "unusable", "missing": ["yaml"]},
                               {"mode": "unusable", "missing": ["requests"]}, "unusable")
        self.assertEqual(feats["figure_audit"]["state"], "unusable")
        self.assertEqual([g["id"] for g in feats["figure_audit"]["guards"]], ["H5"])
        self.assertIn("FIGURE_FORGE_PYTHON", feats["figure_audit"]["remedy"]["hu"])
        self.assertEqual(feats["rob"]["state"], "unusable")
        self.assertIn("pip install", feats["rob"]["remedy"]["hu"])
        self.assertEqual(feats["prisma"]["state"], "unusable")
        feats, _ = self.report({"mode": "legacy"}, {"mode": "legacy"}, {"mode": "legacy"}, "legacy")
        self.assertEqual((feats["rob"]["state"], feats["rob"]["mode"]), ("legacy", "bridge"))
        self.assertEqual([g["id"] for g in feats["tripod"]["guards"]], ["H1"])
        self.assertEqual([g["id"] for g in feats["grade"]["guards"]], ["H3"])
        self.assertEqual((feats["figure_audit"]["state"], feats["figure_audit"]["mode"]), ("legacy", "bridge"))
        self.assertEqual(feats["pub_figure"]["state"], "legacy")
        self.assertIn("H6", feats["pub_figure"]["remedy"]["hu"])
        self.assertEqual([g["id"] for g in feats["prisma"]["guards"]], ["H7"])
        self.assertIn("composer kimeneti mappáját", feats["prisma"]["remedy"]["hu"])
        feats, _ = self.report({"mode": "f2", "version": "0.3.0"}, {"mode": "json", "version": "1.1.0"},
                               {"mode": "json", "version": "1.5.0"}, "ok")
        for fid in ("rob", "probast", "tripod", "grade", "amstar2", "figure_audit", "pub_figure", "prisma"):
            self.assertEqual(feats[fid]["state"], "ok", fid)
        self.assertEqual(feats["rob"]["mode"], "json")
        self.assertIsNone(feats["pub_figure"]["remedy"])


class FigureRoutesEngine(_RouteBase):
    FF = {"mode": "legacy"}

    def test_options_and_engine_export(self):
        with mock.patch.object(RF, "_render_fn", lambda: None):
            st, _h, env = self.call("GET", "/api/figures?run=" + self.rid)
            self.assertEqual(st, 200, env)
            d = env["data"]
            kinds = {k["kind"]: k for k in d["run"]["kinds"]}
            self.assertEqual(kinds["forest"]["source"], "run_file")
            self.assertFalse(kinds["loo"]["available"])
            fmts = {f["id"]: f for f in d["formats"]}
            self.assertTrue(fmts["svg"]["available"])
            self.assertFalse(fmts["png"]["available"])
            self.assertFalse(fmts["tiff"]["available"])
            self.assertIn("figure-forge", fmts["tiff"]["reason"]["hu"])
            self.assertEqual(d["renderers"][0]["id"], "figure-forge")
            self.assertFalse(d["renderers"][0]["available"])
            st, _h, env = self.call("POST", "/api/figures/export",
                                    {"run_id": self.rid, "kind": "forest", "formats": ["svg", "png", "tiff"]})
            self.assertEqual(st, 200, env)
            d = env["data"]
            self.assertEqual(d["renderer"]["used"], "engine")
            self.assertEqual(d["renderer"]["svg_source"], "run_file")
            self.assertEqual(sorted(d["formats"]), ["svg"])
            self.assertEqual([s["format"] for s in d["skipped"]], ["png", "tiff"])
            self.assertTrue(d["qc"]["numbers"]["ok"])
            self.assertEqual(d["qc"]["badge"], "ok")
            self.assertEqual(d["qc"]["figure_forge"]["mode"], "bridge")
            self.assertTrue(d["downloads"]["svg"]["url"].startswith("/f/"))
            self.assertTrue(any("render_figure" in w for w in env["warnings"]))     # nyelv: a futás SVG-je (hu)
            path = os.path.join(self.proj, "06_kezirat", "abrak", "fig_forest_o1.svg")
            self.assertTrue(os.path.isfile(path))
            res = _rj(os.path.join(self.proj, "06_kezirat", "abrak", "fig_forest_o1.result.json"))
            self.assertEqual(res["source"]["run_id"], self.rid)
            self.assertTrue(res["server_check"]["ok"])
            # ugyanaz a név újra → 409; overwrite → rendben
            st, _h, env = self.call("POST", "/api/figures/export", {"run_id": self.rid, "kind": "forest"})
            self.assertEqual((st, env["error"]["code"]), (409, "CONFLICT"))
            self.assertTrue(env["error"]["details"]["needs_overwrite"])
            st, _h, env = self.call("POST", "/api/figures/export", {"run_id": self.rid, "kind": "forest",
                                                                    "overwrite": True})
            self.assertEqual(st, 200, env)
            # nem futás-SVG-s fajta motorfüggvény nélkül → 424 magyar teendővel
            st, _h, env = self.call("POST", "/api/figures/export", {"run_id": self.rid, "kind": "cumulative"})
            self.assertEqual((st, env["error"]["code"]), (424, "CAPABILITY_MISSING"))
            self.assertIn("render_figure", env["error"]["message"])
            # explicit figure-forge kérés legacy pluginnal → 424 a H6-teendővel
            st, _h, env = self.call("POST", "/api/figures/export", {"run_id": self.rid, "kind": "funnel",
                                                                    "renderer": "figure-forge"})
            self.assertEqual(st, 424)
            self.assertIn("H6", env["error"]["message"])
            st, _h, env = self.call("GET", "/api/figures?run=" + self.rid)
            self.assertEqual([e["stem"] for e in env["data"]["existing"]], ["fig_forest_o1"])
            self.assertFalse(env["data"]["existing"][0]["stale"])
        act = _rt(os.path.join(self.proj, "07_ellenorzes", "activity.jsonl"))
        self.assertIn('"figures.export"', act)
        disp = self.run["primary"]["display_text"]["hu"]
        self.assert_log_clean(disp, "Aronson")

    def test_engine_render_function_used_when_present(self):
        calls = []

        def fake_render(plot, kind, lang="en", annotate=True):
            calls.append((kind, lang, annotate))
            texts = "".join("<text>%s</text>" % s["display_text"]["en"] for s in plot["studies"])
            texts += "".join("<text>%s</text>" % s["weight_text"]["en"] for s in plot["studies"])
            texts += "".join("<text>%s</text><text>PI %s</text>" % (s["display_text"]["en"], s["pi_text"]["en"])
                             if s.get("pi_text") else "<text>%s</text>" % s["display_text"]["en"]
                             for s in plot["summaries"])
            texts += "".join("<text>%s</text>" % t["text"] for t in plot["axis"]["ticks"])
            return {"svg": '<svg xmlns="http://www.w3.org/2000/svg"><g id="layer-rows">%s</g></svg>' % texts,
                    "lang": lang}

        with mock.patch.object(RF, "_render_fn", lambda: fake_render):
            st, _h, env = self.call("POST", "/api/figures/export", {"run_id": self.rid, "kind": "forest",
                                                                    "lang": "en", "stem": "f_en"})
        self.assertEqual(st, 200, env)
        self.assertEqual(calls, [("forest", "en", True)])
        d = env["data"]
        self.assertEqual((d["renderer"]["svg_source"], d["lang"]["used"]), ("engine_render", "en"))
        self.assertTrue(d["qc"]["numbers"]["ok"], d["qc"]["numbers"])
        self.assertEqual(d["qc"]["stdlib"]["named_layers"], 1)
        self.assertEqual(env["warnings"], [])

    def test_converter_on_path(self):
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        p = os.path.join(bindir, "rsvg-convert")
        with open(p, "w") as fh:
            fh.write("#!%s\nimport sys\nout = sys.argv[sys.argv.index('-o') + 1]\nfmt = sys.argv[sys.argv.index('-f') + 1]\n"
                     "open(out, 'wb').write(b'\\x89PNG\\r\\n\\x1a\\nX' if fmt == 'png' else b'%%PDF-1.4 X')\n"
                     % sys.executable)
        os.chmod(p, 0o755)
        self.srv.app.caps._env["PATH"] = bindir
        with mock.patch.object(RF, "_render_fn", lambda: None):
            st, _h, env = self.call("GET", "/api/figures?run=" + self.rid)
            self.assertTrue({f["id"]: f for f in env["data"]["formats"]}["png"]["available"])
            st, _h, env = self.call("POST", "/api/figures/export", {"run_id": self.rid, "kind": "doi",
                                                                    "formats": ["svg", "png", "pdf"], "dpi": 300})
        self.assertEqual(st, 200, env)
        self.assertEqual(sorted(env["data"]["formats"]), ["pdf", "png", "svg"])
        self.assertEqual(env["data"]["formats"]["png"]["via"], "rsvg-convert")
        self.assertTrue(_rb(os.path.join(self.proj, "06_kezirat/abrak/fig_doi_o1.png"))
                        .startswith(b"\x89PNG"))

    def test_audit_route(self):
        st, _h, env = self.call("POST", "/api/figures/audit", {"run_id": self.rid, "kind": "doi"})
        self.assertEqual(st, 200, env)
        d = env["data"]
        self.assertTrue(d["numbers"]["ok"])
        self.assertEqual(d["figure_forge"]["mode"], "bridge")
        self.assertTrue(d["stdlib"]["editable"])
        for bad in ("../ma-projekt.json", "06_kezirat/abrak/../../x.svg", "03_adatok/o1.csv", "/etc/passwd",
                    "06_kezirat/abrak/x.png"):
            st, _h, env = self.call("POST", "/api/figures/audit", {"path": bad})
            self.assertIn(st, (400, 403), bad)
        st, _h, env = self.call("POST", "/api/figures/audit", {"path": "06_kezirat/abrak/nincs.svg"})
        self.assertEqual(st, 404)


class FigureRoutesF2(_RouteBase):
    FF = {"mode": "f2", "version": "0.3.0"}

    def test_figure_forge_export(self):
        st, _h, env = self.call("GET", "/api/figures?run=" + self.rid)
        fmts = {f["id"]: f for f in env["data"]["formats"]}
        self.assertTrue(fmts["tiff"]["available"] and fmts["pptx"]["available"])
        st, _h, env = self.call("POST", "/api/figures/export", {"run_id": self.rid, "kind": "forest", "lang": "hu",
                                                                "formats": ["svg", "pdf", "tiff", "pptx"]})
        self.assertEqual(st, 200, env)
        d = env["data"]
        self.assertEqual(d["renderer"]["used"], "figure-forge")
        self.assertEqual(sorted(d["formats"]), ["pdf", "pptx", "svg", "tiff"])
        self.assertTrue(d["qc"]["ff_numbers"]["ok"])
        self.assertTrue(d["qc"]["numbers"]["ok"], d["qc"]["numbers"])            # szerver-újraellenőrzés
        self.assertEqual(d["qc"]["badge"], "ok")
        self.assertNotIn("pptx", d["downloads"])                                  # nem kiszolgálható kiterjesztés

    def test_number_drop_and_escape(self):
        STUBS.make_plugin(self.plugins, "figure-forge", {"mode": "f2", "version": "0.3.0", "drop_number": True,
                                                         "escape_path": True})
        self.call("POST", "/api/capabilities/refresh", {})
        st, _h, env = self.call("POST", "/api/figures/export", {"run_id": self.rid, "kind": "forest", "lang": "hu",
                                                                "formats": ["svg", "png"], "stem": "bad"})
        self.assertEqual(st, 200, env)
        d = env["data"]
        self.assertFalse(d["qc"]["numbers"]["ok"])
        self.assertEqual(d["qc"]["badge"], "issues")
        self.assertEqual([s["format"] for s in d["skipped"]], ["png"])            # /etc/passwd → elutasítva
        self.assertFalse(os.path.exists(os.path.join(self.proj, "06_kezirat", "abrak", "bad.png")))
        # a motor projekt-auditja a <stem>.result.json-ból jelzi (X018); a forrás-hash a futásé (nincs X002)
        found = [f for f in api.project_audit(self.proj).get("findings") or [] if f.get("code") in ("X002", "X018")]
        self.assertTrue(any(f["code"] == "X018" and "bad" in (f.get("detail") or "") for f in found), found)
        self.assertFalse(any(f["code"] == "X002" for f in found), found)


class ValidatorRoutes(_RouteBase):
    VAL = {"mode": "legacy"}

    def test_check_route(self):
        doc = copy.deepcopy(DOCS["probast_dev"])
        doc["answers"]["development/1.1"]["evidence"] = {"text": "IDÉZET-XYZ", "page": 4}
        st, _h, env = self.call("POST", "/api/validator/check", {"doc": doc})
        self.assertEqual(st, 200, env)
        self.assertEqual(env["data"]["completeness_text"], "16/34")
        self.assertTrue(env["warnings"])
        st, _h, env = self.call("POST", "/api/validator/check", {"doc": {"tool": "rob2"}})
        self.assertEqual(st, 400)
        self.assert_log_clean("IDÉZET-XYZ")

    def test_missing_plugin(self):
        STUBS.make_plugins(self.plugins, validator=None)
        self.call("POST", "/api/capabilities/refresh", {})
        st, _h, env = self.call("POST", "/api/validator/check", {"doc": DOCS["rob2"]})
        self.assertEqual((st, env["error"]["code"]), (424, "CAPABILITY_MISSING"))


class ComposerRoutes(_RouteBase):
    COMP = {"mode": "legacy"}

    def test_flow(self):
        st, h, env = self.call("GET", "/api/prisma/composer")
        self.assertEqual(st, 200, env)
        self.assertFalse(env["data"]["can_refresh"])
        self.assertIn("kimeneti mappáját", env["data"]["reason"]["hu"])
        st, _h, env = self.call("POST", "/api/prisma/composer/refresh", {})
        self.assertEqual(st, 400)
        st, _h, env = self.call("PUT", "/api/prisma/composer/config", {"outdir": os.path.join(self.tmp, "nincs"),
                                                                       "project": "glp1"})
        self.assertEqual(st, 422)
        st, _h, env = self.call("PUT", "/api/prisma/composer/config", {"outdir": self.outdir, "project": "a b"})
        self.assertEqual(st, 400)
        st, _h, env = self.call("PUT", "/api/prisma/composer/config", {"outdir": "-rf", "project": "glp1"})
        self.assertEqual(st, 400)
        st, _h, env = self.call("PUT", "/api/prisma/composer/config", {"outdir": self.outdir, "project": "glp1"})
        self.assertEqual(st, 200, env)
        self.assertTrue(env["data"]["can_refresh"])
        meta = _rj(os.path.join(self.proj, "ma-projekt.json"))
        self.assertEqual(meta["composer"]["project"], "glp1")
        # előnézet: nem ír
        st, _h, env = self.call("POST", "/api/prisma/composer/refresh", {"dry_run": True})
        self.assertEqual(st, 200, env)
        self.assertFalse(env["data"]["written"])
        self.assertEqual(env["data"]["flow"]["included_reports"], 25)
        self.assertIn("findings", env["data"]["check"])
        self.assertFalse(os.path.exists(os.path.join(self.proj, "02_szures", "prisma_flow.json")))
        # írás (nincs még fájl → If-Match nélkül)
        st, h, env = self.call("POST", "/api/prisma/composer/refresh", {})
        self.assertEqual(st, 200, env)
        self.assertTrue(env["data"]["written"])
        etag = h.get("etag")
        st, _h, env = self.call("GET", "/api/prisma")
        self.assertEqual(env["data"]["mode"], "composer")
        self.assertEqual([w["code"] for w in env["data"]["source"]["status"]], ["retmax", "pending_5d"])
        self.assertEqual(env["data"]["flow"]["screened"], 946)
        # újra: If-Match nélkül 400, régi ETag-gel 409, jóval 200
        st, _h, env = self.call("POST", "/api/prisma/composer/refresh", {})
        self.assertEqual(st, 400)
        st, _h, env = self.call("POST", "/api/prisma/composer/refresh", {}, headers=[("If-Match", '"' + "0" * 64 + '"')])
        self.assertEqual(st, 409)
        st, _h, env = self.call("POST", "/api/prisma/composer/refresh", {}, headers=[("If-Match", etag)])
        self.assertEqual(st, 200, env)
        act = _rt(os.path.join(self.proj, "07_ellenorzes", "activity.jsonl"))
        self.assertIn('"prisma.composer_refresh"', act)
        self.assertIn('"composer_version":"1.4.1"', act.replace(" ", ""))
        self.assertNotIn("946", act)                                             # számérték nincs a naplóban
        self.assertNotIn("glp-1 AND pregnancy", act + self.srv.log.getvalue())

    def test_manual_numbers_need_confirmation(self):
        self.call("PUT", "/api/prisma/composer/config", {"outdir": self.outdir, "project": "glp1"})
        flow = {"identified_databases": "100", "screened": "80", "excluded_screening": "70"}
        st, h, env = self.call("PUT", "/api/prisma/manual", {"flow": flow})
        self.assertEqual(st, 200, env)
        etag = h.get("etag")
        st, _h, env = self.call("POST", "/api/prisma/composer/refresh", {}, headers=[("If-Match", etag)])
        self.assertEqual(st, 409)
        self.assertTrue(env["error"]["details"]["needs_confirm"])
        st, _h, env = self.call("POST", "/api/prisma/composer/refresh", {"confirm_replace_manual": True},
                                headers=[("If-Match", etag)])
        self.assertEqual(st, 200, env)
        # vissza kézire: a meglévő PRISMA-végpont indoklást kér composer-módban
        st, _h, env = self.call("PUT", "/api/prisma/manual", {"flow": flow},
                                headers=[("If-Match", _h.get("etag") or "")])
        self.assertEqual(st, 400)

    def test_truncated_state(self):
        STUBS.make_plugin(self.plugins, "composer", {"mode": "legacy", "truncated_times": 9})
        self.call("POST", "/api/capabilities/refresh", {})
        self.call("PUT", "/api/prisma/composer/config", {"outdir": self.outdir, "project": "glp1"})
        with mock.patch.object(C.ComposerAdapter, "sleep", staticmethod(lambda s: None)):
            st, _h, env = self.call("POST", "/api/prisma/composer/refresh", {"dry_run": True})
        self.assertEqual((st, env["error"]["code"]), (502, "PLUGIN_FAILED"))
        self.assertIn("H7", env["error"]["message"])


if __name__ == "__main__":
    unittest.main()
