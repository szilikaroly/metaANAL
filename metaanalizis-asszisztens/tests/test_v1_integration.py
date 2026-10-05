# -*- coding: utf-8 -*-
"""v1 integráció: a Metaheadhunter bekötése (CLI, homlokzat, képesség, munkapad-regiszter, skill, ágens-másolat,
dokumentáció) és a futás-ábrák újrarajzolásának parancssori útja.

Mit bizonyít:
- `ma.py headhunter …` (és a `metaheadhunter` álnév) a Metaheadhunter saját parancssorára ágazik, a súgó a
  `ma.py headhunter` nevet mutatja, a kilépési kódok változatlanok; a `sources` hálózat nélkül fut;
- API-kulcs értéke soha nem kerül a kimenetbe (csak a jelenléte);
- az `api.headhunter_*` homlokzat ugyanazt a borítékot adja, mint a CLI `--json`-ja; a képesség-leírásban szerepel;
- a munkapad regisztrálja a Metaheadhunter végpontjait (routes.MODULES);
- a skill S03-szakasza (mikor, EP1–EP6, `sources --check`), az orkesztrátor és a plugin-másolatok, valamint a
  TELEPITES / ESZKOZOK / README leírja a forrásokat és a környezeti változókat (Scopus EISZ-en át);
- `ma.py figure --kind forest` futásmappa nélkül érthető hibát ad (a futás-ábra csak a futás adataiból rajzolható)."""
import io
import json
import os
import sys
import tempfile
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from _helpers import ROOT
from metaelemzes import api, cli

REPO = os.path.dirname(ROOT)
KEY_MARKER = "SCOPUSKULCS-1a2b3c4d5e"


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


class HeadhunterCliTests(unittest.TestCase):
    def test_help_forwards_with_ma_py_prog(self):
        for name in ("headhunter", "metaheadhunter"):
            rc, out, _err = run_cli(name, "-h")
            self.assertEqual(rc, 0, name)
            self.assertIn("usage: ma.py %s" % name, out)
            self.assertIn("find-reviews", out)
        rc, out, _err = run_cli("-h")
        self.assertIn("headhunter", out, "a főmenü is listázza")

    def test_sources_without_network_and_no_key_leak(self):
        env = {"MA_SCOPUS_APIKEY": KEY_MARKER, "MA_CONTACT_EMAIL": "teszt@example.org"}
        with mock.patch.dict(os.environ, env):
            rc, out, err = run_cli("headhunter", "sources", "--json")
            via_api = api.headhunter_sources()
        self.assertEqual(rc, 0, err)
        self.assertNotIn(KEY_MARKER, out + err)
        self.assertNotIn(KEY_MARKER, json.dumps(via_api, ensure_ascii=False))
        doc = json.loads(out)
        self.assertEqual((doc["ok"], doc["command"], doc["exit_code"]), (True, "sources", 0))
        scopus = [r for r in doc["data"]["rows"] if r["source"] == "scopus"][0]
        self.assertIs(scopus["key_configured"], True)
        self.assertEqual([r["source"] for r in via_api["data"]["rows"]], [r["source"] for r in doc["data"]["rows"]])
        # a „ma.py headhunter” parancsnév a következő-lépés tippben is
        rc, out, _err = run_cli("headhunter", "sources")
        self.assertEqual(rc, 0)
        self.assertIn("ma.py headhunter init", out)

    def test_usage_error_exit_code_passes_through(self):
        rc, _out, _err = run_cli("headhunter", "nincs-ilyen-parancs")
        self.assertEqual(rc, 2)

    def test_init_and_status_through_facade_and_cli(self):
        tmp = tempfile.mkdtemp(prefix="ma_hh_int_")
        self.addCleanup(shutil.rmtree, tmp, True)
        proj = os.path.join(tmp, "proj")
        os.makedirs(proj)
        env = api.headhunter_init(proj, "BCG-oltás és tuberkulózis", actor="user:SzK")
        self.assertTrue(env["ok"], env)
        self.assertTrue(os.path.isfile(os.path.join(proj, "01_kereses", "headhunter", "state.json")))
        rc, out, err = run_cli("headhunter", "status", proj, "--json")
        self.assertEqual(rc, int(json.loads(out)["exit_code"]), err)
        self.assertEqual(json.loads(out)["data"], api.headhunter_status(proj)["data"])
        ver = api.headhunter_verify(proj)
        self.assertEqual(sorted(ver), sorted(json.loads(out)))         # ugyanaz a boríték-alak

    def test_facade_and_capabilities(self):
        for n in ("headhunter_init", "headhunter_status", "headhunter_sources", "headhunter_run_step",
                  "headhunter_decide", "headhunter_verify"):
            self.assertTrue(callable(getattr(api, n, None)), n)
        cmds = {c["name"]: c for c in api.capabilities()["commands"]}
        self.assertEqual(cmds["headhunter"]["argv"], ["ma.py", "headhunter"])
        self.assertIs(cmds["headhunter"]["available"], True)
        with self.assertRaises(Exception):
            api.headhunter_run_step(tempfile.gettempdir(), "nincs_ilyen_lepes")

    def test_gui_registers_headhunter_routes(self):
        from ma_gui import routes
        from ma_gui.routes import headhunter
        self.assertIn(headhunter, routes.MODULES)


class FigureCliTests(unittest.TestCase):
    def test_run_figure_needs_the_run_folder(self):
        tmp = tempfile.mkdtemp(prefix="ma_fig_int_")
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "plot_data.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"schema": "szk.ma.plot/v2"}, fh)
        rc, _out, err = run_cli("figure", "--plot", p, "--kind", "forest")
        self.assertEqual(rc, 1)
        self.assertIn("futás mappája", err)


class SkillAgentDocsTests(unittest.TestCase):
    def test_skill_s03_section_and_orchestrator(self):
        for skill in (os.path.join(REPO, ".claude", "skills", "metaanalizis", "SKILL.md"),
                      os.path.join(ROOT, "skills", "metaanalizis", "SKILL.md")):
            text = _read(skill)
            self.assertIn("### S03: Meglévő metaanalízisek bányászata", text, skill)
            self.assertIn("**Mikor hívd:**", text)
            for ep in ("EP1", "EP2", "EP3", "EP4", "EP5", "EP6"):
                self.assertIn("**%s**" % ep, text, (skill, ep))
            self.assertIn('ma.py" headhunter sources --check' if "PLUGIN_ROOT" in text else
                          "ma.py headhunter sources --check", text)
            self.assertIn("ma-metaheadhunter", text)
        orch = _read(REPO, ".claude", "agents", "metaanalizis-asszisztens.md")
        self.assertIn("ma-metaheadhunter", orch)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "agents", "ma-metaheadhunter.md")), "plugin-másolat")

    def test_docs_describe_sources_and_env_keys(self):
        tel = _read(ROOT, "TELEPITES.md")
        esz = _read(ROOT, "ESZKOZOK_ES_HOZZAFERESEK.md")
        readme = _read(ROOT, "README.md")
        for var in ("MA_CONTACT_EMAIL", "MA_OPENALEX_APIKEY", "MA_SCOPUS_APIKEY", "MA_SCOPUS_INSTTOKEN"):
            for name, text in (("TELEPITES", tel), ("ESZKOZOK", esz), ("README", readme)):
                self.assertIn(var, text, (name, var))
        for name, text in (("TELEPITES", tel), ("ESZKOZOK", esz), ("README", readme)):
            self.assertIn("headhunter sources --check", text, name)
            self.assertIn("EISZ", text, name)
        self.assertIn("setx MA_SCOPUS_APIKEY", tel)
        self.assertIn("export MA_SCOPUS_APIKEY", tel)


if __name__ == "__main__":
    unittest.main()
