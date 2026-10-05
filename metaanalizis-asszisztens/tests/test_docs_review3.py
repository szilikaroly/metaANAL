# -*- coding: utf-8 -*-
"""Dokumentáció ↔ program — regressziós tesztek a v1 harmadik független átnézésének dokumentációs megállapításaihoz.

- DOC-1 az ágens L0 lépése (projekt NÉLKÜL: ``sources --check``) és minden dokumentált ``headhunter`` parancs a
        valódi CLI-elemzővel értelmezhető (a mappa a helyén, létező kapcsolók);
- DOC-2 a forrás be/ki kapcsolása ``sources <mappa> --enable … --actor user:<név>`` (nincs ``set`` alparancs) — a
        program saját tanácsai is ezt mondják;
- DOC-3 a plugin leírása és a TELEPÍTÉS annyi alágenst nevez, ahány a pluginban van;
- DOC-4 a ``kereses_naplo_headhunter.md`` fájlt semmi nem ígéri (a kód nem írja; a keresési napló a report.md része);
- DOC-5 a TELEPÍTÉS 7a táblázatának állapot-szavai a CLI magyar címkéi (és a zárójeles kód a valódi állapotkód);
- DOC-8 a futás közbeni tanácsok a repó gyökeréből is futtatható ``ma.py headhunter …`` alakot adják.
"""
import json
import os
import re
import shlex
import unittest

from _helpers import ROOT  # noqa: F401  (a sys.path beállítása)
from metaelemzes.headhunter import cli as HC
from metaelemzes.headhunter import net, sources as SRC

REPO = os.path.dirname(ROOT)
AGENT = os.path.join(REPO, ".claude", "agents", "ma-metaheadhunter.md")


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


class _NoExit(Exception):
    pass


def _parse(argv):
    """A valódi CLI-elemző (cli.build_parser) — hibánál kivétel, nem kilépés."""
    class P(HC.argparse.ArgumentParser):
        def error(self, message):
            raise _NoExit(message)
    return HC.build_parser(parser_class=P).parse_args(argv)


def _documented_commands(text):
    """`headhunter <parancs> …` kódrészletek a szövegből (a helyőrzők konkrét értékre cserélve)."""
    out = []
    for m in re.finditer(r"`(?:python3? (?:metaanalizis-asszisztens/)?ma\.py )?headhunter ([^`]+)`", text):
        cmd = m.group(1).strip()
        if "…" in cmd.split()[0] or cmd.startswith("<"):
            continue
        cmd = (cmd.replace("<mappa>", "proj").replace("<projekt>", "proj").replace("user:<név>", "user:SzK")
               .replace("<pico.json>", "pico.json").replace("\"…\"", "x").replace("…", "x"))
        out.append(re.sub(r"<[^>]+>", "X", cmd))                     # egyéb helyőrző (pl. <a fenti fájl>)
    return out


class AgentCommandTests(unittest.TestCase):
    """DOC-1, DOC-2."""

    def test_every_documented_headhunter_command_parses(self):
        cmds = _documented_commands(_read(AGENT))
        self.assertGreater(len(cmds), 8)
        bad = []
        for cmd in cmds:
            try:
                _parse(shlex.split(cmd))
            except (_NoExit, SystemExit, ValueError) as exc:
                bad.append("%s → %s" % (cmd, exc))
        self.assertEqual(bad, [])

    def test_l0_runs_before_init_without_a_project(self):
        agent = _read(AGENT)
        l0 = agent[agent.index("**L0"):agent.index("\n2.", agent.index("**L0"))]
        self.assertIn("`headhunter sources --check --json`", l0)
        self.assertNotRegex(l0, r"sources --check <mappa>", "a mappa a parancs után áll, és az init előtt nincs projekt")
        a = _parse(["sources", "--check", "--json"])
        self.assertIsNone(getattr(a, "project", None))
        with self.assertRaises(_NoExit):
            _parse(["sources", "set", "proj", "--enable", "openalex"])       # DOC-2: nincs „set” alparancs

    def test_runtime_enable_hint_is_valid(self):
        msg = net.status_explain("openalex", "disabled")
        hint = re.search(r"ma\.py headhunter (sources [^)]+)\)", msg["hu"]).group(1)
        a = _parse(shlex.split(hint.replace("<projekt>", "proj").replace("<név>", "SzK")))
        self.assertEqual(a.project, "proj")
        self.assertTrue(a.enable)


class PluginAgentCountTests(unittest.TestCase):
    """DOC-3."""

    WORDS = {3: "három", 4: "négy", 5: "öt", 6: "hat"}

    def test_descriptions_name_the_real_number_of_subagents(self):
        agents = sorted(f for f in os.listdir(os.path.join(ROOT, "agents")) if f.endswith(".md"))
        self.assertIn("metaanalizis-asszisztens.md", agents)
        n_sub = len(agents) - 1                                   # az orkesztrátoron kívül
        plugin = json.loads(_read(ROOT, ".claude-plugin", "plugin.json"))
        self.assertIn("%s alágens" % self.WORDS[n_sub], plugin["description"])
        self.assertIn("Metaheadhunter", plugin["description"])
        tel = " ".join(_read(ROOT, "TELEPITES.md").split())
        self.assertNotIn("három alágens", tel)
        self.assertIn("%s alágens" % self.WORDS[n_sub], tel)


class SearchLogFileTests(unittest.TestCase):
    """DOC-4."""

    def test_nothing_promises_a_file_the_code_never_writes(self):
        name = "kereses_naplo_headhunter"
        written = False
        for base, _dirs, files in os.walk(os.path.join(ROOT, "metaelemzes")):
            for f in files:
                if f.endswith(".py") and name in _read(base, f):
                    written = True
        if written:
            self.skipTest("a kód írja a fájlt")
        for path in (AGENT, os.path.join(ROOT, "agents", "ma-metaheadhunter.md"),
                     os.path.join(ROOT, "tudasbazis", "seed", "rules_HH.json")):
            self.assertNotIn(name, _read(path), path)
        self.assertIn("Keresések (PRISMA-S)", _read(AGENT))


class TroubleshootingTableTests(unittest.TestCase):
    """DOC-5."""

    def test_status_words_are_the_cli_labels(self):
        tel = _read(ROOT, "TELEPITES.md")
        sec = tel[tel.index("## 7a."):tel.index("## 8.")]
        rows = re.findall(r"^\| [^|]*„([^”]+)”[^|]*\(`([a-z_]+)`", sec, re.M)
        self.assertGreaterEqual(len(rows), 4)
        for word, code in rows:
            self.assertIn(code, SRC._STATUS_HU, code)
            self.assertTrue(word.startswith(SRC._STATUS_HU[code]), "%s ≠ a CLI címkéje (%s)" % (word, SRC._STATUS_HU[code]))


class RuntimeHintTests(unittest.TestCase):
    """DOC-8: a tanácsok a repó gyökeréből is futnak (ma.py headhunter …), nem a csomag-modul alakot adják."""

    def test_hints_use_ma_py(self):
        texts = []
        for status in ("unknown", "unreachable", "disabled"):
            for src in ("pubmed", "openalex", "scopus"):
                m = net.status_explain(src, status)
                texts += [m["hu"], m["en"]]
        from metaelemzes.headhunter import state as S
        try:
            S.load_state("/nonexistent-ma-docs-test")
        except Exception as exc:                                      # noqa: BLE001 — a hibaüzenet szövege kell
            texts.append(str(getattr(exc, "hu", "")) + " " + str(exc))
        for t in texts:
            self.assertNotIn("python -m metaelemzes.headhunter", t)
        self.assertTrue(any("ma.py headhunter sources --check" in t for t in texts))
        self.assertTrue(any("ma.py headhunter init" in t for t in texts), texts[-1])


if __name__ == "__main__":
    unittest.main()
