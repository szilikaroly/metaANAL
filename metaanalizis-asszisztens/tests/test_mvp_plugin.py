# -*- coding: utf-8 -*-
"""MVP: a repó Claude Code-plugin-marketplace is (terv 11. fejezet, 1. döntés; E12).

- a tools/build_plugin.py --check tiszta: a plugin agents/ és skills/ fája, a plugin.json és a marketplace.json
  pontosan az, amit a generátor a .claude/ változatból előállít (egyetlen igazságforrás);
- a két manifeszt érvényes JSON a kötelező mezőkkel, a nevek a Claude Code szabályai szerintiek, a verzió a
  metaelemzes.__version__, a marketplace-bejegyzés forrása a plugin mappája; plugin-szintű settings.json nincs;
- a generált ágensek frontmattere szigorú YAML-ként is egyértelmű, a plugin-ágensnél figyelmen kívül hagyott mezők
  nélkül; az alágens- és skillhivatkozások névtérben vannak; minden hivatkozott út létezik;
- az átalakító szabályai (útvonal, névtér, idézés, kihagyott mezők) egységtesztekkel;
- a generátor a forrás változását, a fölösleges és a hiányzó fájlt jelzi (--check: 1), az újragenerálás rendbe teszi;
- E12: a skill a munkapadot és az Artifact-tilalmat, a ma-ellenorzo a project auditot és az --audit-gate-et tartalmazza;
- a TELEPITES.md és a README telepítési parancsai a manifesztek neveivel egyeznek.

Plugin-telepítésből futtatva (ma.py selftest a plugin-gyorsítótárban) a repó gyökere (.claude/, marketplace.json)
nincs jelen: az azt igénylő tesztek ilyenkor kimaradnak, a plugin saját fájljait vizsgálók lefutnak.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from _helpers import ROOT
from metaelemzes import __version__

REPO = os.path.dirname(ROOT)
BUILD = os.path.join(ROOT, "tools", "build_plugin.py")
PLUGIN_JSON = os.path.join(ROOT, ".claude-plugin", "plugin.json")
MARKETPLACE_JSON = os.path.join(REPO, ".claude-plugin", "marketplace.json")
HAS_REPO = os.path.isdir(os.path.join(REPO, ".claude", "agents")) and os.path.isfile(MARKETPLACE_JSON)
NEED_REPO = unittest.skipUnless(HAS_REPO, "plugin-telepítésből fut: a repó .claude/ forrása és a marketplace.json "
                                          "nincs jelen")

_spec = importlib.util.spec_from_file_location("build_plugin", BUILD)
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)

# a Claude Code névszabályai (marketplace-reference, manifest-reference)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_RESERVED_MARKETPLACES = {
    "claude-code-marketplace", "claude-code-plugins", "claude-plugins-official", "anthropic-marketplace",
    "anthropic-plugins", "agent-skills", "anthropic-agent-skills", "life-sciences", "knowledge-work-plugins",
    "claude-for-legal", "claude-for-financial-services", "financial-services-plugins", "first-party-plugins",
    "claude-tag-plugins", "claude-community", "claude-plugins-community", "healthcare",
    "anthropic-plugin-directory", "claude-plugin-directory", "inline", "builtin", "skills-dir", "synced",
    "claude-plugin-test", "npm", "pip", "uv", "cargo", "github", "gh"}
_AGENT_KEYS = {"name", "description", "model", "effort", "maxTurns", "tools", "disallowedTools", "skills", "memory",
               "background", "omitClaudeMd", "isolation", "color", "experimental"}
_SKILL_KEYS = {"name", "description", "argument-hint", "allowed-tools", "model", "disable-model-invocation",
               "user-invocable", "context", "agent", "effort", "hooks", "paths", "shell", "when_to_use", "version",
               "license"}


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _generated_md():
    out = []
    for tree in ("agents", "skills"):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, tree)):
            out.extend(os.path.join(dirpath, f) for f in sorted(files) if f.endswith(".md"))
    return sorted(out)


def _frontmatter(text):
    """{kulcs: nyers érték} és a törzs — a felső szintű `kulcs: érték` sorok (folytatósor nincs a fájljainkban)."""
    assert text.startswith("---\n"), "a fájl nem frontmatterrel kezdődik"
    end = text.index("\n---\n", 3)
    fm = {}
    for ln in text[4:end].split("\n"):
        m = re.match(r"^([A-Za-z_][\w-]*):(?:\s(.*))?$", ln)
        assert m, "értelmezhetetlen frontmatter-sor: %r" % ln
        fm[m.group(1)] = m.group(2) or ""
    return fm, text[end + 5:]


def _scalar(raw):
    return json.loads(raw) if raw.startswith('"') else raw


def _main(argv):
    """bp.main csendben (a kimenetét a teszt nem írja a konzolra)."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return bp.main(argv)


def _strip_header(text):
    return "\n".join(ln for ln in text.split("\n") if bp.GENERATED_MARK not in ln)


class BuildCheckTest(unittest.TestCase):
    @NEED_REPO
    def test_check_mode_is_clean(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, BUILD, "--check"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, "a plugin-fájlok elavultak — futtasd: python tools/build_plugin.py\n"
                         + r.stdout + r.stderr)
        self.assertIn("naprakészek", r.stdout)

    @NEED_REPO
    def test_check_detects_drift_and_build_repairs(self):
        tmp = tempfile.mkdtemp(prefix="ma_plugin_")
        try:
            shutil.copytree(os.path.join(REPO, ".claude"), os.path.join(tmp, ".claude"))
            shutil.copytree(os.path.join(REPO, ".claude-plugin"), os.path.join(tmp, ".claude-plugin"))
            pdir = os.path.join(tmp, bp.PLUGIN_DIRNAME)
            for sub in ("agents", "skills", ".claude-plugin"):
                shutil.copytree(os.path.join(ROOT, sub), os.path.join(pdir, sub))
            os.makedirs(os.path.join(pdir, "metaelemzes"))
            shutil.copy(os.path.join(ROOT, "metaelemzes", "__init__.py"), os.path.join(pdir, "metaelemzes"))
            self.assertEqual(bp.check(tmp), [])

            src = os.path.join(tmp, ".claude", "agents", "ma-tervezo.md")
            with open(src, "a", encoding="utf-8") as fh:
                fh.write("\nÚj sor: `python metaanalizis-asszisztens/ma.py selftest`.\n")
            stale = os.path.join(pdir, "agents", "regi-agens.md")
            with open(stale, "w", encoding="utf-8") as fh:
                fh.write("---\nname: regi-agens\n---\n")
            os.remove(os.path.join(pdir, ".claude-plugin", "plugin.json"))
            got = {(s, r) for s, r in bp.check(tmp)}
            self.assertEqual(got, {("ELTÉR", bp.PLUGIN_DIRNAME + "/agents/ma-tervezo.md"),
                                   ("FÖLÖSLEGES", bp.PLUGIN_DIRNAME + "/agents/regi-agens.md"),
                                   ("HIÁNYZIK", bp.PLUGIN_DIRNAME + "/.claude-plugin/plugin.json")})
            self.assertEqual(_main(["--check", "--repo-root", tmp]), 1)

            self.assertEqual(_main(["--repo-root", tmp]), 0)
            self.assertEqual(bp.check(tmp), [])
            self.assertFalse(os.path.exists(stale))
            self.assertIn('`python "${CLAUDE_PLUGIN_ROOT}/ma.py" selftest`',
                          _text(os.path.join(pdir, "agents", "ma-tervezo.md")))

            # a Windows-os (CRLF) munkapéldány nem számít eltérésnek
            gen = os.path.join(pdir, "agents", "ma-ellenorzo.md")
            with open(gen, "rb") as fh:
                data = fh.read()
            with open(gen, "wb") as fh:
                fh.write(data.replace(b"\n", b"\r\n"))
            self.assertEqual(bp.check(tmp), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @NEED_REPO
    def test_missing_replacement_anchor_is_an_error(self):
        tmp = tempfile.mkdtemp(prefix="ma_plugin_")
        try:
            shutil.copytree(os.path.join(REPO, ".claude"), os.path.join(tmp, ".claude"))
            os.makedirs(os.path.join(tmp, bp.PLUGIN_DIRNAME, "metaelemzes"))
            shutil.copy(os.path.join(ROOT, "metaelemzes", "__init__.py"),
                        os.path.join(tmp, bp.PLUGIN_DIRNAME, "metaelemzes"))
            skill = os.path.join(tmp, ".claude", "skills", "metaanalizis", "SKILL.md")
            text = _text(skill)
            for old, _new in bp.REPLACEMENTS[".claude/skills/metaanalizis/SKILL.md"]:
                text = text.replace(old, "## Átnevezett címsor")
            with open(skill, "w", encoding="utf-8") as fh:
                fh.write(text)
            with self.assertRaises(bp.BuildError):
                bp.generate(tmp)
            self.assertEqual(_main(["--check", "--repo-root", tmp]), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ManifestTest(unittest.TestCase):
    def test_plugin_manifest(self):
        pj = _load(PLUGIN_JSON)
        self.assertEqual(pj["name"], bp.PLUGIN_NAME)
        self.assertRegex(pj["name"], _KEBAB_RE)
        self.assertEqual(pj["version"], __version__)
        for key in ("description", "displayName", "repository"):
            self.assertIsInstance(pj.get(key), str)
            self.assertTrue(pj[key].strip(), key)
        self.assertEqual(pj["author"], {"name": "Szili Károly"})
        self.assertTrue(pj["keywords"] and all(isinstance(k, str) and k for k in pj["keywords"]))
        # alapértelmezett helyek: nincs komponens-kulcs, és plugin-szintű settings.json sincs (engedélyt nem adhat)
        for key in ("agents", "skills", "commands", "hooks", "mcpServers", "settings"):
            self.assertNotIn(key, pj)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "settings.json")))
        # a plugin gyökerében nincs olyan alapértelmezett komponenshely, amelyet nem a generátor ír
        for name in ("commands", "hooks", "output-styles", "workflows", "themes", "monitors", "bin", ".mcp.json",
                     ".lsp.json", "CLAUDE.md"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, name)), name)

    @NEED_REPO
    def test_marketplace_manifest(self):
        mp = _load(MARKETPLACE_JSON)
        self.assertRegex(mp["name"], _ID_RE)
        self.assertNotIn("..", mp["name"])
        self.assertNotIn(mp["name"].lower(), _RESERVED_MARKETPLACES)
        self.assertFalse(mp["name"].lower().startswith("claudeai-"))
        self.assertTrue(mp["owner"]["name"].strip())
        self.assertTrue(mp.get("description"))
        self.assertEqual(len(mp["plugins"]), 1)
        entry = mp["plugins"][0]
        for key in ("name", "source", "description", "version"):
            self.assertIsInstance(entry.get(key), str, key)
            self.assertTrue(entry[key].strip(), key)
        self.assertRegex(entry["name"], _ID_RE)
        self.assertTrue(entry["source"].startswith("./"))
        self.assertNotIn("..", entry["source"])
        self.assertNotIn("\\", entry["source"])
        src = os.path.normpath(os.path.join(REPO, entry["source"]))
        self.assertEqual(os.path.realpath(src), os.path.realpath(ROOT))
        pj = _load(os.path.join(src, ".claude-plugin", "plugin.json"))
        self.assertEqual(entry["name"], pj["name"])        # telepítési azonosító = manifeszt-név
        self.assertEqual(entry["version"], pj["version"])  # ha mindkettő megadja, egyezzen
        self.assertEqual(entry["version"], __version__)


class GeneratedFilesTest(unittest.TestCase):
    def test_generated_tree(self):
        agents = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "agents")) if f.endswith(".md"))
        core = ["ma-ellenorzo", "ma-ertekelo", "ma-tervezo", "metaanalizis-asszisztens"]
        self.assertTrue(set(core) <= set(agents), agents)
        # a plugin ágensei pontosan a .claude/agents forrásai (v1: + ma-metaheadhunter)
        src = os.path.join(REPO, ".claude", "agents")
        if os.path.isdir(src):
            self.assertEqual(agents, sorted(f[:-3] for f in os.listdir(src) if f.endswith(".md")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "skills", "metaanalizis", "SKILL.md")))

    def test_frontmatter_and_header(self):
        skills = {"%s:%s" % (bp.PLUGIN_NAME, d) for d in os.listdir(os.path.join(ROOT, "skills"))}
        for path in _generated_md():
            rel = os.path.relpath(path, ROOT)
            with self.subTest(rel=rel):
                text = _text(path)
                fm, body = _frontmatter(text)
                self.assertTrue(body.startswith("<!-- " + bp.GENERATED_MARK), "hiányzik a generált-fájl fejléc")
                is_agent = rel.split(os.sep)[0] == "agents"
                self.assertLessEqual(set(fm), _AGENT_KEYS if is_agent else _SKILL_KEYS)
                for key in bp.IGNORED_AGENT_KEYS:
                    if is_agent:
                        self.assertNotIn(key, fm)
                name = _scalar(fm["name"])
                self.assertNotIn(":", name)
                if is_agent:
                    self.assertEqual(name, os.path.basename(path)[:-3])
                else:
                    self.assertEqual(name, os.path.basename(os.path.dirname(path)))
                for key, raw in fm.items():
                    # szigorú YAML: a sima skalár nem tartalmaz „: ”-t és „ #”-t; különben JSON-idézett
                    if raw.startswith('"'):
                        json.loads(raw)
                    else:
                        self.assertNotIn(": ", raw, key)
                        self.assertNotIn(" #", raw, key)
                self.assertTrue(_scalar(fm["description"]).strip())
                if "skills" in fm:
                    for item in _scalar(fm["skills"]).split(","):
                        self.assertIn(item.strip(), skills)
        orch = _frontmatter(_text(os.path.join(ROOT, "agents", "metaanalizis-asszisztens.md")))[0]
        self.assertEqual(orch["skills"], "metaanalizis:metaanalizis")

    def test_yaml_if_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML nincs telepítve")
        for path in _generated_md():
            text = _text(path)
            fm = yaml.safe_load(text[4:text.index("\n---\n", 3)])
            self.assertIsInstance(fm, dict, path)
            self.assertIn("description", fm)

    def test_references_are_rewritten_and_exist(self):
        names = [f[:-3] for f in os.listdir(os.path.join(ROOT, "agents")) if f.endswith(".md")]
        bare = re.compile(r"(?<![\w:/.\-])(%s)(?![\w/:\-]|\.\w)" % "|".join(map(re.escape, names)))
        for path in _generated_md():
            rel = os.path.relpath(path, ROOT)
            fm, body = _frontmatter(_text(path))
            body = _strip_header(body)
            with self.subTest(rel=rel):
                self.assertNotRegex(body, r"(?<![\w/.\-])%s/" % re.escape(bp.PLUGIN_DIRNAME))
                self.assertNotRegex(body, r"(?<![\w/.\-])\.claude/(agents|skills)/")
                self.assertIsNone(bare.search(body + "\n" + _scalar(fm["description"])),
                                  "névtér nélküli ágensnév")
                for v in re.findall(r'subagent_type\s*=\s*"([^"]+)"', body):
                    self.assertTrue(v.startswith(bp.PLUGIN_NAME + ":"), v)
                    self.assertIn(v.split(":", 1)[1], names)
                for v in re.findall(r"claude --agent ([\w:.-]+)", body + _scalar(fm["description"])):
                    self.assertTrue(v.startswith(bp.PLUGIN_NAME + ":"), v)
                self.assertNotIn('""${CLAUDE_PLUGIN_ROOT}', body)
                for ref in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)", body):
                    ref = ref.rstrip(".")
                    self.assertTrue(os.path.exists(os.path.join(ROOT, *ref.split("/"))), ref)

    @NEED_REPO
    def test_source_references_exist(self):
        """A .claude/ eredetiben hivatkozott repóbeli utak is léteznek (a generált változat ezekből készül)."""
        for sub in ("agents", "skills"):
            for dirpath, _dirs, files in os.walk(os.path.join(REPO, ".claude", sub)):
                for f in files:
                    if not f.endswith(".md"):
                        continue
                    text = _text(os.path.join(dirpath, f))
                    refs = re.findall(r"(?<![\w/.\-])(%s/[\w./-]+|\.claude/[\w./-]+)" % re.escape(bp.PLUGIN_DIRNAME),
                                      text)
                    for ref in refs:
                        ref = ref.rstrip(".")
                        with self.subTest(file=f, ref=ref):
                            self.assertTrue(os.path.exists(os.path.join(REPO, *ref.split("/"))), ref)


class TransformTest(unittest.TestCase):
    AGENTS = ["ma-ellenorzo", "ma-ertekelo", "ma-tervezo", "metaanalizis-asszisztens"]

    def t(self, text):
        return bp.transform_text(text, self.AGENTS)

    def test_paths(self):
        self.assertEqual(self.t("`python metaanalizis-asszisztens/ma.py kb stats`"),
                         '`python "${CLAUDE_PLUGIN_ROOT}/ma.py" kb stats`')
        self.assertEqual(self.t("`metaanalizis-asszisztens/ESZKOZOK_ES_HOZZAFERESEK.md`"),
                         "`${CLAUDE_PLUGIN_ROOT}/ESZKOZOK_ES_HOZZAFERESEK.md`")
        self.assertEqual(self.t("a `.claude/skills/metaanalizis/SKILL.md` protokoll"),
                         "a `${CLAUDE_PLUGIN_ROOT}/skills/metaanalizis/SKILL.md` protokoll")
        # más út része, home-mappa, projektút: változatlan
        # csak maga a ma.py kap idézőjelet
        self.assertEqual(self.t("metaanalizis-asszisztens/ma.pyc"), "${CLAUDE_PLUGIN_ROOT}/ma.pyc")
        # a mappanév az orkesztrátor nevével azonos: mappaként perjellel írjuk, így út marad
        self.assertEqual(self.t("a `metaanalizis-asszisztens/` mappa"), "a `${CLAUDE_PLUGIN_ROOT}/` mappa")
        for keep in ("~/.claude/skills/x/SKILL.md", "x/metaanalizis-asszisztens/ma.py", "ma.py kb stats",
                     "<projekt>/00_protokoll/protokoll.md"):
            self.assertEqual(self.t(keep), keep)

    def test_agent_names(self):
        self.assertEqual(self.t('Agent(subagent_type="ma-ellenorzo", prompt="…")'),
                         'Agent(subagent_type="metaanalizis:ma-ellenorzo", prompt="…")')
        self.assertEqual(self.t("kérd a `ma-ellenorzo`-t."), "kérd a `metaanalizis:ma-ellenorzo`-t.")
        self.assertEqual(self.t("claude --agent metaanalizis-asszisztens"),
                         "claude --agent metaanalizis:metaanalizis-asszisztens")
        self.assertEqual(self.t("a ma-tervezo írja."), "a metaanalizis:ma-tervezo írja.")
        for keep in ("ma-ellenorzo.md", "metaanalizis:ma-ellenorzo", "agents/ma-tervezo.md", "ma-ertekelo-v2",
                     "--agent reviewer", "a metaanalízis-asszisztens orkesztrátora", "xma-tervezo"):
            self.assertEqual(self.t(keep), keep)
        # idempotens
        once = self.t("`ma-tervezo` és metaanalizis-asszisztens/ma.py")
        self.assertEqual(self.t(once), once)

    def test_yaml_scalar(self):
        self.assertEqual(bp.yaml_scalar("egyszerű leírás, (zárójel)"), "egyszerű leírás, (zárójel)")
        self.assertEqual(bp.yaml_scalar("metaanalizis:metaanalizis"), "metaanalizis:metaanalizis")
        for raw in ("változik): PICO", "a # megjegyzés", "- lista", '"idézet"', "vég:", "`kód`"):
            out = bp.yaml_scalar(raw)
            self.assertTrue(out.startswith('"'), raw)
            self.assertEqual(json.loads(out), raw)

    def test_frontmatter(self):
        lines = ["name: ma-tervezo", 'description: "Tervező (ma-ellenorzo előtt): x"', "permissionMode: plan",
                 "hooks:", "  PreToolUse:", "    - matcher: Bash", "skills: metaanalizis, mas-skill",
                 "tools: Read, Bash", "mcpServers:", "  - pubmed", "model: inherit"]
        out = bp.transform_frontmatter(lines, self.AGENTS, ["metaanalizis"], "teszt")
        self.assertEqual(out, ["name: ma-tervezo",
                               'description: "Tervező (metaanalizis:ma-ellenorzo előtt): x"',
                               "skills: metaanalizis:metaanalizis, mas-skill",
                               "tools: Read, Bash", "model: inherit"])
        block = bp.transform_frontmatter(["name: a", "description: b", "skills:", "  - metaanalizis", "  - mas"],
                                         self.AGENTS, ["metaanalizis"], "teszt")
        self.assertEqual(block, ["name: a", "description: b", "skills:", "  - metaanalizis:metaanalizis", "  - mas"])
        flow = bp.transform_frontmatter(["name: a", "description: >", "  A ma-tervezo után.", "skills: [metaanalizis]"],
                                        self.AGENTS, ["metaanalizis"], "teszt")
        self.assertEqual(flow, ["name: a", "description: >", "  A metaanalizis:ma-tervezo után.",
                                "skills: [metaanalizis:metaanalizis]"])
        with self.assertRaises(bp.BuildError):
            bp.transform_frontmatter(["name: plugin:nev"], self.AGENTS, [], "teszt")
        with self.assertRaises(bp.BuildError):
            bp.transform_frontmatter(["  árva folytatósor"], self.AGENTS, [], "teszt")

    def test_markdown_header_and_note(self):
        src = "---\nname: metaanalizis\ndescription: d\n---\n\n# Cím\n\nSzöveg `ma-tervezo`.\n"
        out = bp.transform_markdown(src, ".claude/skills/x/SKILL.md", self.AGENTS, ["metaanalizis"], is_skill=True)
        fm, body = _frontmatter(out)
        self.assertTrue(body.startswith("<!-- %s" % bp.GENERATED_MARK))
        self.assertIn("# Cím\n\n> **Plugin-telepítés.**", body)
        self.assertIn("${CLAUDE_PLUGIN_DATA}", body)
        self.assertIn("Szöveg `metaanalizis:ma-tervezo`.", body)
        with self.assertRaises(bp.BuildError):
            bp.transform_markdown("nincs frontmatter, nincs címsor\n", "x.md", self.AGENTS, [], is_skill=True)


class E12ContentTest(unittest.TestCase):
    """A skill és az ágensek E12-tartalma — a plugin-változaton (mindig jelen van) és az eredetin."""

    def _variants(self, plugin_rel, repo_rel):
        out = [_text(os.path.join(ROOT, *plugin_rel.split("/")))]
        if HAS_REPO:
            out.append(_text(os.path.join(REPO, *repo_rel.split("/"))))
        return out

    def test_skill_munkapad_and_artifact_ban(self):
        for text in self._variants("skills/metaanalizis/SKILL.md", ".claude/skills/metaanalizis/SKILL.md"):
            self.assertRegex(text, r"ma\.py\"? gui --project <mappa>")
            self.assertIn("## MA-munkapad", text)
            self.assertIn("Soha ne publikáld Artifactként", text)
            self.assertIn("project audit <mappa> --json", text)
            self.assertIn("--audit-gate", text)

    def test_reviewer_audit(self):
        for text in self._variants("agents/ma-ellenorzo.md", ".claude/agents/ma-ellenorzo.md"):
            self.assertRegex(text, r"ma\.py\"? project audit <mappa> --json")
            self.assertIn("S12-től", text)
            self.assertRegex(text, r"--stage FINAL --agent reviewer --verdict … --audit-gate")

    def test_evaluator_keeps_ai_draft(self):
        for text in self._variants("agents/ma-ertekelo.md", ".claude/agents/ma-ertekelo.md"):
            self.assertIn("## AI-vázlat értékelésekhez", text)
            self.assertIn("kezdő kutató számára is érthetően", text)
            self.assertIn("Artifactként", text)


class DocsTest(unittest.TestCase):
    @NEED_REPO
    def test_install_commands_match_manifests(self):
        mp = _load(MARKETPLACE_JSON)
        install = "/plugin install %s@%s" % (mp["plugins"][0]["name"], mp["name"])
        add = "/plugin marketplace add szilikaroly/metaANAL"
        orch = "claude --agent %s:metaanalizis-asszisztens" % mp["plugins"][0]["name"]
        telepites = _text(os.path.join(ROOT, "TELEPITES.md"))
        readme = _text(os.path.join(ROOT, "README.md"))
        for doc, text in (("TELEPITES.md", telepites), ("README.md", readme)):
            for needle in (add, install, orch):
                self.assertTrue(needle in text, "%s: hiányzik: %s" % (doc, needle))
        self.assertTrue("(TELEPITES.md)" in readme, "a README nem hivatkozik a TELEPITES.md-re")
        for needle in ("python ma.py selftest", "kb build", "kb stats", "kb ingest", "gui --project",
                       "py -3", "_privat/", "Python 3.9", "METAELEMZES_KB", "pypdf"):
            self.assertTrue(needle in telepites, "TELEPITES.md: hiányzik: %s" % needle)

    @NEED_REPO
    def test_permission_block_is_valid_and_matches_plugin_path(self):
        """A TELEPITES.md engedélylistája beilleszthető JSON, és a plugin gyorsítótár-útjára illeszkedik."""
        mp = _load(MARKETPLACE_JSON)
        blocks = re.findall(r"```json\n(.*?)```", _text(os.path.join(ROOT, "TELEPITES.md")), re.S)
        perms = [json.loads(b) for b in blocks if '"permissions"' in b]
        self.assertEqual(len(perms), 1)
        allow, deny = perms[0]["permissions"]["allow"], perms[0]["permissions"]["deny"]
        cache = "/.claude/plugins/cache/%s/%s/*/ma.py" % (mp["name"], mp["plugins"][0]["name"])
        self.assertIn('Bash(python "*%s" *)' % cache, allow)
        self.assertIn("Bash(python metaanalizis-asszisztens/ma.py *)", allow)
        self.assertTrue(any("_privat" in d for d in deny))
        # a parancs, amelyet a generált skill a telepítés után kiad, illeszkedik a szabályra
        rule = re.escape('python "*%s" *' % cache).replace(r"\*", ".*")
        cmd = 'python "C:/Users/kolléga/.claude/plugins/cache/%s/%s/%s/ma.py" kb stats' % (
            mp["name"], mp["plugins"][0]["name"], __version__)
        self.assertRegex(cmd, "^" + rule + "$")
        # a repó saját beállításai a motor parancsát engedik
        repo_allow = _load(os.path.join(REPO, ".claude", "settings.json"))["permissions"]["allow"]
        self.assertTrue(any("metaanalizis-asszisztens/ma.py" in r for r in repo_allow))


if __name__ == "__main__":
    unittest.main()
