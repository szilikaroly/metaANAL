# -*- coding: utf-8 -*-
"""Az adapterek a VALÓDI szk-plugins pluginokkal (terv 5.0, 8.3, 8.7 8. lépés: valódi plugin-integráció).

Futtatás: ``MA_GUI_PLUGIN_DIRS=<szk-plugins>/plugins python3 -m unittest tests/gui/test_v1_adapters_real.py``.
Ha a változó nincs beállítva vagy a plugin hiányzik, a tesztek tiszta üzenettel kimaradnak.

- validator 1.0.0 (stdlib): a golden-kimenetek nem sodródtak (a rögzített H1–H4 reprodukció ma is így fut), és a
  bridge-mód az őrökkel helyes eredményt ad a hibás plugin mellett is; a H12 (polaritás) és a H13 (számozás) hibája
  ma is reprodukálható, és az őr megjelöli / kiszűri.
- validator 2.0.0 (a javított kiadás, szk-plugins#5): a ``validator-2.0.0`` golden-kimenetek nem sodródtak; egyik
  5.0-s őr sem kapcsol be; a ROBINS-I és a QUIPS publikált azonosítói mind átmennek, és a validator teljessége és
  ítélete a motoréval egyezik (a ROBINS-I 2.1 tudatos szabálybeli eltérése megjegyzésként).
  A golden-kimeneteket a ``adapters_golden/record_validator.py`` rögzíti (ugyanazzal a függvénnyel, amellyel ez a
  teszt összeveti őket).
- figure-forge 0.2.1: matplotlib nélküli interpreterrel ``unusable`` + pontos H5-teendő; matplotlibes
  interpreterrel (``MA_GUI_TEST_FF_PYTHON`` vagy ``FIGURE_FORGE_PYTHON``; ennek hiányában a PATH ``python3``-ja, ha
  van benne matplotlib) ``legacy``, és az ``ff.py audit`` a motor valódi SVG-jén fut — a tmp-ben, a projektbe nem ír.
  ``MA_GUI_TEST_FF_INSTALL=1`` mellett a teszt maga készít venv-et (pip install matplotlib numpy pandas); ha a pip nem
  elérhető, kimarad.
- composer 1.4.1 (stdlib): a valódi ``prisma export --format flow-json`` egy kitalált állapoton, explicit
  interpreterrel (a szkript shebangja ``/Users/szili/anaconda3/…``); csonka állapotnál a H7-hiba 3 próbálkozás után."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_routes_harness as H  # noqa: E402

from metaelemzes import api  # noqa: E402
from ma_gui import caps as caps_mod  # noqa: E402
from ma_gui.adapters import composer as C  # noqa: E402
from ma_gui.adapters import figureforge as F  # noqa: E402
from ma_gui.adapters import validator as V  # noqa: E402

GOLDEN = os.path.join(HERE, "adapters_golden", "validator-1.0.0")
sys.path.insert(0, os.path.join(HERE, "adapters_golden"))
import record_validator as REC  # noqa: E402
SKIP_DIRS = "MA_GUI_PLUGIN_DIRS nincs beállítva (a valódi szk-plugins plugins/ mappája) — a valódi-plugin teszt kimarad"


def _plugin_base(name):
    raw = os.environ.get("MA_GUI_PLUGIN_DIRS", "")
    for d in raw.split(os.pathsep):
        d = d.strip()
        for cand in (os.path.join(d, name), os.path.join(d, "plugins", name)):
            if d and os.path.isdir(cand):
                return d
    return None


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _has_mpl(python):
    try:
        r = subprocess.run([python, "-c", "import matplotlib, numpy, pandas"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


_FF_PY = {}


def ff_python():
    """Matplotlibes interpreter a figure-forge-hoz, vagy (None, ok)."""
    if "py" in _FF_PY:
        return _FF_PY["py"], _FF_PY["why"]
    py, why = None, None
    for cand in (os.environ.get("MA_GUI_TEST_FF_PYTHON"), os.environ.get("FIGURE_FORGE_PYTHON"),
                 shutil.which("python3")):
        if cand and _has_mpl(cand):
            py = cand
            break
    if py is None and os.environ.get("MA_GUI_TEST_FF_INSTALL") == "1":
        venv = os.path.join(tempfile.gettempdir(), "ma_gui_ff_venv")
        vpy = os.path.join(venv, "bin", "python")
        try:
            if not os.path.isfile(vpy):
                subprocess.run([sys.executable, "-m", "venv", venv], check=True, timeout=300)
            subprocess.run([vpy, "-m", "pip", "install", "-q", "matplotlib", "numpy", "pandas", "Pillow"],
                           check=True, timeout=900)
            py = vpy if _has_mpl(vpy) else None
        except (OSError, subprocess.SubprocessError):
            py = None
        if py is None:
            why = "a pip-telepítés nem sikerült (nincs hálózat vagy pip)"
    if py is None and why is None:
        why = ("nincs matplotlibes Python (állítsd: MA_GUI_TEST_FF_PYTHON=<python>, vagy MA_GUI_TEST_FF_INSTALL=1 a "
               "venv-telepítéshez)")
    _FF_PY.update(py=py, why=why)
    return py, why


def _caps(tmp, base, name, **env):
    e = {"MA_GUI_PLUGIN_DIRS": base, "PATH": "/usr/bin:/bin"}
    e.update(env)
    return caps_mod.Caps(runtime_dir=os.path.join(tmp, "caps_" + name), env=e, home=os.path.join(tmp, "nohome"))


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = H.tmpdir("ma_adp_real_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def _validator_scripts(base):
    return os.path.join(base, "validator", "scripts") if os.path.isdir(os.path.join(base, "validator")) \
        else os.path.join(base, "plugins", "validator", "scripts")


class _RealValidatorBase(_Tmp):
    VERSION = None                                  # None: bármely rögzített verzió (a golden-teszthez)

    def setUp(self):
        super().setUp()
        self.base = _plugin_base("validator")
        if self.base is None:
            self.skipTest(SKIP_DIRS)
        self.scripts = _validator_scripts(self.base)
        self.ad = V.ValidatorAdapter(_caps(self.tmp, self.base, "v"))
        cap = self.ad.detect()
        self.version = cap.get("version")
        if cap.get("state") != "legacy":
            self.skipTest("a validator nem legacy (bridge) állapotú (%s)" % cap.get("state"))
        if self.VERSION == "fixed" and not V.fixed_release(cap):
            self.skipTest("nem a javított validator (≥ %s), hanem %s" % (V.FIXED_VERSION, self.version))
        if self.VERSION not in (None, "fixed") and self.version != self.VERSION:
            self.skipTest("nem a validator %s (legacy) — a teszt erre a verzióra szól (telepítve: %s)"
                          % (self.VERSION, self.version))


class RealValidatorGoldens(_RealValidatorBase):
    def test_goldens_have_not_drifted(self):
        """A rögzített kimenetek (validator-<verzió>/) a telepített pluginnal ma is így jönnek — 1.0.0-n és 2.0.0-n."""
        if self.version not in REC.NAMES:
            self.skipTest("a validator %s kimenetei nincsenek rögzítve (van: %s)" % (self.version, ", ".join(REC.NAMES)))
        gdir = REC.golden_dir(self.version)
        docs = REC.load_docs()
        for name in REC.NAMES[self.version]:
            got = REC.outputs(self.scripts, name, docs[name], self.version, self.tmp)
            for kind, text in sorted(got.items()):
                self.assertEqual(text, _read(os.path.join(gdir, "%s.%s.txt" % (name, kind))), "%s.%s" % (name, kind))
        # minden rögzített fájl a listában szereplő dokumentumé (nem maradt elárvult golden)
        names = {f.split(".")[0] for f in os.listdir(gdir) if f.endswith(".skeleton.txt")}
        self.assertEqual(names, set(REC.NAMES[self.version]))


class RealValidator(_RealValidatorBase):
    VERSION = "1.0.0"

    def test_bridge_with_guards_on_real_plugin(self):
        docs = json.loads(_read(os.path.join(HERE, "adapters_golden", "docs.json")))
        d = self.ad.check(docs["probast_dev"])["data"]
        self.assertEqual((d["answered"], d["validator_reported"]["answered"]), (16, 32))       # H2
        d = self.ad.check(docs["grade_strong"])["data"]
        self.assertIsNone(d["grade"]["certainty"])                                           # H3
        d = self.ad.check(docs["amstar2_py"])["data"]
        self.assertEqual((d["amstar2"]["rating"], d["amstar2"]["convention"]), ("moderate", "weakness"))  # H4
        d = self.ad.check(docs["tripod_empty"])["data"]
        self.assertEqual(d["answered"], 0)                                                    # H1
        self.assertIn("H1", [g["id"] for g in d["guards"]])

    def test_h12_h13_reproduced_and_guarded(self):
        """H12: a QUADAS-2 1.2/1.3 „yes” (= jó) a validator 1.0.0-ban magas kockázatot kényszerít (fordított
        polaritás) — az őr megjelöli; H13: a ROBINS-I régi számozású tételei nem mennek át."""
        def instrument(tool):
            return json.loads(_read(os.path.join(H.ROOT, "metaelemzes", "instruments", tool + ".json")))
        doc = {"schema": "szk.appraisal/v1", "tool": "quadas2",
               "answers": {k: {"value": "yes"} for k in ("1.1", "1.2", "1.3")}}
        d = self.ad.check(doc, instrument=instrument("quadas2"))["data"]
        dom1 = [x for x in d["domains"] if x["domain"] == "1"][0]
        self.assertEqual((dom1["implied"], sorted(dom1["forced_by"])), ("high", ["1.2", "1.3"]))   # a hiba ma is él
        self.assertEqual([g["id"] for g in d["guards"]], ["H12"])
        self.assertEqual(d["unreliable_domains"], ["1"])
        self.assertFalse(d["overall"]["reliable"])
        doc = {"schema": "szk.appraisal/v1", "tool": "robins-i", "scope": "assignment",
               "answers": {k: {"value": "no"} for k in ("4.1", "4.3", "5.1", "5.2")}}
        d = self.ad.check(doc, instrument=instrument("robins-i"))["data"]
        self.assertEqual([(g["id"], g["items"]) for g in d["guards"]], [("H13", ["4.3", "5.2"])])
        self.assertFalse(d["comparable"])
        self.assertEqual(d["validator_reported"]["answered"], 2)                                   # 4.1, 5.1

    def test_h12_robins_i_router_items(self):
        """FID-5: a ROBINS-I 2.1 (és 1.3) a publikált eszközben elágazó kérdés, a validator 1.0.0-ban „reverse”: a
        2.1 = igen a validatornál súlyos kockázatot kényszerít — az őr a domént és az összítéletet nem megbízhatónak
        jelöli (nem a H13 számozásnak tulajdonítja)."""
        inst = json.loads(_read(os.path.join(H.ROOT, "metaelemzes", "instruments", "robins-i.json")))
        doc = {"schema": "szk.appraisal/v1", "tool": "robins-i", "scope": "assignment",
               "answers": {"2.1": {"value": "yes"}, "2.2": {"value": "no"}, "2.3": {"value": "no"}}}
        d = self.ad.check(doc, instrument=inst)["data"]
        h12 = [g for g in d["guards"] if g["id"] == "H12"]
        self.assertTrue(h12, d["guards"])
        self.assertIn("2.1", h12[0]["items"])
        self.assertIn("2", d["unreliable_domains"])
        if isinstance(d.get("overall"), dict):
            self.assertFalse(d["overall"]["reliable"])


class RealValidatorFixed(_RealValidatorBase):
    """A javított validator (≥ 2.0.0): az 1.0.0 H1–H4, H12, H13 hibái eltűntek, az őrök nem kapcsolnak be, és a
    bridge eredménye a motoréval összevethető."""
    VERSION = "fixed"

    @staticmethod
    def instrument(tool):
        return json.loads(_read(os.path.join(H.ROOT, "metaelemzes", "instruments", tool + ".json")))

    def test_no_guards_and_bugs_gone(self):
        cap = self.ad.detect()
        self.assertEqual(cap["guards"], [])
        self.assertNotIn("bridge_untested_version", [p["code"] for p in cap["problems"]])
        docs = REC.load_docs()
        d = self.ad.check(docs["probast_dev"])["data"]
        self.assertEqual((d["answered"], d["validator_reported"]["answered"], d["validator_reported"]["trusted"]),
                         (16, 16, True))                                                     # H2 javítva
        self.assertEqual(d["guards"], [])
        d = self.ad.check(docs["tripod_empty"])["data"]
        self.assertEqual((d["answered"], d["validator_reported"]["answered"], d["guards"]), (0, 0, []))   # H1
        d = self.ad.check(docs["grade_strong"])["data"]
        self.assertEqual((d["grade"]["certainty"], d["guards"]), ("moderate", []))           # H3: −1
        d = self.ad.check(docs["amstar2_py"])["data"]
        self.assertEqual((d["amstar2"]["rating"], d["amstar2"]["convention"], d["guards"]),
                         ("moderate", "weakness", []))                                       # H4: nincs PY-csapda
        self.assertTrue(d["complete"] and d["validator_reported"]["agrees"])                # N/A a 11-en: válasz

    def test_polarity_and_numbering_fixed(self):
        """Az 1.0.0-n H12-t és H13-at kiváltó esetek: itt nincs őr, minden válasz átmegy."""
        doc = {"schema": "szk.appraisal/v1", "tool": "quadas2",
               "answers": {k: {"value": "yes"} for k in ("1.1", "1.2", "1.3")}}
        d = self.ad.check(doc, instrument=self.instrument("quadas2"))["data"]
        dom1 = [x for x in d["domains"] if x["domain"] == "1"][0]
        self.assertEqual((dom1["level"], dom1["forced_by"], d["guards"]), ("low", [], []))
        self.assertNotIn("unreliable_domains", d)
        doc = {"schema": "szk.appraisal/v1", "tool": "robins-i", "scope": "assignment",
               "answers": {k: {"value": "no"} for k in ("4.1", "5.1", "5.2", "5.4", "6.4")}}
        d = self.ad.check(doc, instrument=self.instrument("robins-i"))["data"]
        self.assertEqual((d["guards"], d["validator_reported"]["answered"], d["answered"]), ([], 5, 5))
        self.assertNotIn("comparable", d)

    def test_engine_and_validator_agree(self):
        """Teljes ROBINS-I- és QUIPS-értékelés a publikált azonosítókkal: teljesség és implikált összítélet = motor."""
        docs = REC.load_docs()
        for name, tool in (("robins_i_2016", "robins-i"), ("quips_partly", "quips"), ("quadas2_yes", "quadas2")):
            inst = self.instrument(tool)
            d = self.ad.check(docs[name], instrument=inst)["data"]
            eng = api.appraisal_check(docs[name], instrument=api.instrument_get(tool))
            self.assertEqual((d["answered"], d["expected"]), (eng["answered"], eng["expected"]), name)
            self.assertEqual(d["validator_reported"]["answered"], eng["answered"], name)
            self.assertEqual(d["overall"]["implied"], eng["overall"]["implied"], name)
            self.assertEqual(d["guards"], [], name)
        # a 2.1 mindkét oldalon irányító kérdés (szk-plugins#5, 84363b0): a korábbi 2.1-megjegyzés megszűnt
        d = self.ad.check(docs["robins_i_2016"], instrument=self.instrument("robins-i"))["data"]
        self.assertNotIn("rule_differences", d)

    def test_rob2_adherence_through_bridge(self):
        """RoB 2 betartási változat: a motor 2a.1–2a.6 kulcsai a validator 2.1–2.6-ján mennek át, és az ítélet = motor."""
        inst = self.instrument("rob2")
        if not any(it.get("validator_id") for it in inst["items"]):
            self.skipTest("a motor-definícióban nincs betartási változat")
        ans = {"1.1": "yes", "1.2": "yes", "1.3": "no", "2a.1": "yes", "2a.2": "no", "2a.3": "yes",
               "2a.4": "yes", "2a.5": "no", "2a.6": "yes", "3.1": "yes", "3.2": "not_applicable",
               "3.3": "not_applicable", "3.4": "not_applicable", "4.1": "no", "4.2": "no", "4.3": "no",
               "4.4": "not_applicable", "4.5": "not_applicable", "5.1": "yes", "5.2": "no", "5.3": "no",
               "2.3": "no_information"}                       # kóbor besorolási válasz: nem mehet át
        doc = {"schema": "szk.appraisal/v1", "tool": "rob2", "scope": "adherence",
               "target": {"unit": "S1", "study_id": "S1", "key": "o1"}, "assessor": "SzK",
               "answers": {k: {"value": v} for k, v in ans.items()}}
        d = self.ad.check(doc, instrument=inst)["data"]
        eng = api.appraisal_check(doc, instrument=api.instrument_get("rob2"))
        self.assertEqual((d["validator_reported"]["answered"], d["validator_reported"]["expected"]), (21, 21))
        self.assertEqual({x["domain"]: x["implied"] for x in d["domains"]}["2"], "some_concerns")
        self.assertEqual(d["overall"]["implied"], eng["overall"]["implied"])
        self.assertEqual(eng["overall"]["implied"], "some_concerns")

    def test_grade_resolution_through_bridge(self):
        docs = REC.load_docs()
        d = self.ad.check(docs["grade_suspected"])["data"]
        self.assertEqual((d["grade"]["certainty"], d["grade"]["unresolved"], d["grade"]["range"]),
                         (None, ["publication_bias"], ["high", "moderate"]))
        d = self.ad.check(docs["grade_resolved"])["data"]
        self.assertEqual((d["grade"]["certainty"], d["grade"]["unresolved"]), ("moderate", []))
        eng = api.appraisal_check(docs["grade_resolved"], instrument=api.instrument_get("grade"))
        self.assertEqual(d["grade"]["certainty"], eng["grade"]["certainty"])
        d = self.ad.check(docs["grade_pb2"])["data"]
        self.assertEqual(([g["id"] for g in d["guards"]], d["grade"]["certainty"]), (["PB2"], None))


class RealFigureForge(_Tmp):
    def setUp(self):
        super().setUp()
        self.base = _plugin_base("figure-forge")
        if self.base is None:
            self.skipTest(SKIP_DIRS)

    def test_without_matplotlib_h5(self):
        bare = None
        for cand in (sys.executable, "/usr/bin/python3"):
            if os.path.isfile(cand) and not _has_mpl(cand):
                bare = cand
                break
        if bare is None:
            self.skipTest("minden elérhető Pythonban van matplotlib — a H5-állapot itt nem állítható elő")
        ad = F.FigureForgeAdapter(_caps(self.tmp, self.base, "bare", FIGURE_FORGE_PYTHON=bare,
                                        PATH=os.path.dirname(bare)))
        st = ad.status()
        if st["state"] != "unusable":
            self.skipTest("a figure-forge ebben a környezetben más állapotú (%s)" % st["state"])
        self.assertEqual(st["audit"]["guard"], "H5")
        self.assertIn("pip install matplotlib", st["audit"]["remedy"]["hu"])
        self.assertIn("FIGURE_FORGE_PYTHON", st["audit"]["remedy"]["hu"])
        res = ad.audit(b'<svg xmlns="http://www.w3.org/2000/svg"><text>x</text></svg>')
        self.assertEqual(res["error"]["details"]["guard"], "H5")

    def test_audit_on_engine_svg(self):
        py, why = ff_python()
        if py is None:
            self.skipTest(why)
        proj, _home = H.make_project(self.tmp)
        os.makedirs(os.path.join(proj, "05_elemzes", "specs"))
        sp = os.path.join(proj, "05_elemzes", "specs", "o1_primary.json")
        api.save_spec(sp, H.spec())
        with contextlib.redirect_stdout(io.StringIO()):
            run = api.analyze(sp, mode="commit", project_root=proj)["run"]
        svg_path = os.path.join(proj, run["files"]["doi"]["path"])
        with open(svg_path, "rb") as fh:
            svg = fh.read()
        ad = F.FigureForgeAdapter(_caps(self.tmp, self.base, "mpl", FIGURE_FORGE_PYTHON=py))
        st = ad.status()
        self.assertIn(st["state"], ("legacy", "ok"), st)
        self.assertIsNone(st["export"]["mode"] if st["version"] == "0.2.1" else None)        # H6: nincs meta
        before = sorted(os.listdir(os.path.dirname(svg_path)))
        res = ad.audit(svg, "doi.svg")
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["data"]["editable"])
        self.assertGreater(res["data"]["text_elements"], 5)
        self.assertEqual(sorted(os.listdir(os.path.dirname(svg_path))), before)              # a projektbe nem ír


class RealComposer(_Tmp):
    def setUp(self):
        super().setUp()
        self.base = _plugin_base("composer")
        if self.base is None:
            self.skipTest(SKIP_DIRS)
        self.ad = C.ComposerAdapter(_caps(self.tmp, self.base, "c"))
        if self.ad.mode() is None:
            self.skipTest("a composer nem használható ebben a környezetben")
        self.ad.sleep = lambda s: None
        self.outdir = os.path.join(self.tmp, "PubMed_Downloads")
        script = self.ad.detect()["scripts"]["prisma"]
        run = lambda *a: subprocess.run([sys.executable, script, "--outdir", self.outdir, "--project", "glp1"]  # noqa
                                        + list(a), capture_output=True, text=True, check=True)
        run("init", "--title", "teszt")
        run("add-source", "--source", "PubMed", "--count", "120", "--retrieved", "100")
        state_path = os.path.join(self.outdir, "prisma", "glp1.json")
        state = json.loads(_read(state_path))
        recs = {}
        for i in range(20):
            pm = str(1000 + i)
            r = {"rec_id": pm, "pmid": pm, "cim": "t", "folyoirat": "", "doi": "", "publikacio_datuma": "",
                 "pubmed_url": "", "source": "PubMed", "forras_url": "", "decision": "", "reason": "", "phase": "",
                 "fulltext": "retrieved", "validation": ""}
            if i < 8:
                r.update(decision="exclude", phase="screen", reason="off-topic")
            elif i < 11:
                r.update(decision="exclude", phase="eligibility", reason="wrong population")
            else:
                r.update(decision="include")
            recs[pm] = r
        state["records"] = recs
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        self.state_path = state_path

    def test_export_and_status(self):
        with open(self.state_path, "rb") as fh:
            before = fh.read()
        res = self.ad.export_flow(self.outdir, "glp1")
        self.assertTrue(res["ok"], res)
        flow = res["data"]["flow"]
        self.assertEqual((flow["screened"], flow["included_reports"], flow["excluded_eligibility"]), (20, 9, 3))
        self.assertEqual(flow["excluded_eligibility_reasons"], {"wrong population": 3})
        warns = self.ad.status_warnings(self.outdir, "glp1")["data"]["warnings"]
        self.assertEqual([w["code"] for w in warns], ["retmax"])
        with open(self.state_path, "rb") as fh:
            self.assertEqual(fh.read(), before)                                  # a composer-állapot érintetlen

    def test_truncated_state_h7(self):
        with open(self.state_path, "r+", encoding="utf-8") as fh:
            text = fh.read()
            fh.seek(0)
            fh.write(text[: len(text) // 2])
            fh.truncate()
        res = self.ad.export_flow(self.outdir, "glp1")
        self.assertEqual(res["error"]["code"], "PLUGIN_FAILED")
        self.assertEqual(res["error"]["details"], {"plugin": "composer", "guard": "H7", "attempts": 3})


class RealRoutes(_Tmp):
    """Végponttól végpontig a valódi pluginokkal: ábra-export a figure-forge 0.2.1 auditjával a QC-ben."""

    def test_figure_export_with_real_figure_forge_audit(self):
        base = _plugin_base("figure-forge")
        if base is None:
            self.skipTest(SKIP_DIRS)
        py, why = ff_python()
        if py is None:
            self.skipTest(why)
        proj, home = H.make_project(self.tmp)
        os.makedirs(os.path.join(proj, "05_elemzes", "specs"))
        sp = os.path.join(proj, "05_elemzes", "specs", "o1_primary.json")
        api.save_spec(sp, H.spec())
        with contextlib.redirect_stdout(io.StringIO()):
            rid = api.analyze(sp, mode="commit", project_root=proj)["run"]["run_id"]
        srv = H.Srv(proj, home, self.tmp, caps=_caps(self.tmp, base, "srv", FIGURE_FORGE_PYTHON=py))
        try:
            st, _h, env = srv.call("POST", "/api/figures/export", {"run_id": rid, "kind": "forest", "formats": ["svg"]})
            self.assertEqual(st, 200, env)
            qc = env["data"]["qc"]
            self.assertEqual(qc["figure_forge"]["source"], "figure-forge")
            self.assertTrue(qc["figure_forge"]["editable"])
            self.assertTrue(qc["numbers"]["ok"], qc["numbers"])
            self.assertEqual(qc["badge"], "ok")
            self.assertEqual(env["data"]["renderer"]["used"], "engine")             # H6: 0.2.1-gyel a motor-SVG
        finally:
            srv.app.shutdown()
            srv.app.close()


if __name__ == "__main__":
    unittest.main()
