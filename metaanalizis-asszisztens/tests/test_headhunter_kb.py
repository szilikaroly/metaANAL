# -*- coding: utf-8 -*-
"""Metaheadhunter: tudásbázis-seedek (rules_HH, knowledge_HH, sources_HH) és a ma-metaheadhunter ágens.

TERV_metaheadhunter.md 16., 18. és 19.2 fejezet. Ellenőrzi:
- a három seed-fájl JSON-tömb, a tételek oszlopai pontosan a kb.SEED_TABLES oszlopai;
- az azonosítók alakja és tartománya (D-Sxx-101–149, K-HH-nnn, kisbetűs forrás-ID), nincs ütközés semmilyen más
  seed-azonosítóval vagy motor-szabállyal; a hivatkozott forrás, szakasz és KB-azonosító létezik;
- a forrás-bibliográfia egyezik a PubMedben ellenőrzött adatokkal (PMID, DOI, év, első szerző) — élő
  ellenőrzés: MA_LIVE_TESTS=1;
- a tudásegységek számszerű állításai a PubMed-absztraktokból/nyílt teljes szövegből ellenőrzött számok (új
  százalékérték csak az engedélylista bővítésével, azaz ellenőrzés után kerülhet be);
- a machine_check H-kódjai a terv 15. fejezetének KB-hozzárendelésével oda-vissza egyeznek, és ha a
  metaelemzes.headhunter.checks már létezik, a RULES-ában is megvannak;
- a kb.build ideiglenes adatbázisba sikeres (Python-API-n és `ma.py kb build`-en át, METAELEMZES_KB-vel), a
  szabályok/tudásegységek kereshetők, és a `project log --kb … --strict` elfogadja az új azonosítókat;
- az ágensfájl frontmattere, eszközlistája, parancsai (a terv 14. fejezetének CLI-je), KB-hivatkozásai és a plugin-
  generátoron átvitt alakja.
A repó tudasbazis.sqlite-ját a teszt nem érinti.
"""
import glob
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from _helpers import ROOT
from metaelemzes import kb

REPO = os.path.dirname(ROOT)
SEED = kb.SEED_DIR
HH_FILES = {"source": "sources_HH.json", "decision_rule": "rules_HH.json", "knowledge": "knowledge_HH.json"}
AGENT = os.path.join(REPO, ".claude", "agents", "ma-metaheadhunter.md")
BUILD_PLUGIN = os.path.join(ROOT, "tools", "build_plugin.py")
LIVE = os.environ.get("MA_LIVE_TESTS") == "1"

ID_KEYS = ("rule_id", "k_id", "formula_id", "item_id", "tool_id", "example_id", "source_id", "stage_id")
SEED_COLS = {table: cols for _pattern, table, cols in kb.SEED_TABLES}

# PubMed esummary-vel ellenőrizve (2026-10-05): source_id → (PMID, DOI, év, az első szerző vezetékneve)
VERIFIED_SOURCES = {
    "pieper2014_cca": ("24581293", "10.1016/j.jclinepi.2013.11.007", 2014, "Pieper"),
    "hennessy2020_cca": ("31823513", "10.1002/jrsm.1390", 2020, "Hennessy"),
    "ying2025_wcca": ("41626914", "10.1017/rsm.2025.19", 2025, "Ying"),
    "gotzsche2007_extraction": ("17652297", "10.1001/jama.298.4.430", 2007, "Gøtzsche"),
    "jones2005_extraction": ("15939227", "10.1016/j.jclinepi.2004.11.024", 2005, "Jones"),
    "mathes2017_extraction": ("29179685", "10.1186/s12874-017-0431-4", 2017, "Mathes"),
    "garner2016_update": ("27443385", "10.1136/bmj.i3507", 2016, "Garner"),
    "shojania2007_outdated": ("17638714", "10.7326/0003-4819-147-4-200708210-00179", 2007, "Shojania"),
    "aromataris2015_umbrella": ("26360830", "10.1097/XEB.0000000000000055", 2015, "Aromataris"),
    "prior2022": ("35944924", "10.1136/bmj-2022-070849", 2022, "Gates"),
    "ballard2017_overviews": ("28074553", "10.1002/jrsm.1229", 2017, "Ballard"),
    "pollock2019_overview_tool": ("30670086", "10.1186/s13643-018-0768-8", 2019, "Pollock"),
    "hirt2023_citation_tracking": ("37042216", "10.1002/jrsm.1635", 2023, "Hirt"),
    "tarcis2024": ("38724089", "10.1136/bmj-2023-078384", 2024, "Hirt"),
    "rethlefsen2022_tracking": ("35440907", "10.5195/jmla.2022.1449", 2022, "Rethlefsen"),
    "bramer2016_dedup": ("27366130", "10.3163/1536-5050.104.3.014", 2016, "Bramer"),
    "mckeown2021_dedup": ("33485394", "10.1186/s13643-021-01583-y", 2021, "McKeown"),
    "hair2023_asysd": ("37674179", "10.1186/s12915-023-01686-z", 2023, "Hair"),
    "tramer1997_duplicate": ("9310564", "10.1136/bmj.315.7109.635", 1997, "Tramèr"),
    "vonelm2004_duplicate": ("14982913", "10.1001/jama.291.8.974", 2004, "von Elm"),
    "colditz1994_bcg": ("8309034", None, 1994, "Colditz"),
}
# a meglévő (nem HH) források, amelyekre a HH-seedek hivatkoznak — újrahasznosítva, nem másolva (terv 18.)
REUSED_SOURCES = {"engine", "cochrane_handbook", "prisma2020", "prisma_s", "amstar2", "kharbach2026_tools"}

# a terv 15. fejezete: H-kód → a KB-szabály, amelyre hivatkozik (H001, H015, H017: nincs)
SPEC_H_KB = {
    "H002": "D-S03-102", "H003": "D-S03-103", "H004": "D-S03-102", "H005": "D-S03-103", "H006": "D-S03-102",
    "H007": "D-S04-101", "H008": "D-S03-104", "H009": "D-S04-104", "H010": "D-S05-101", "H011": "D-S14-101",
    "H012": "D-S03-105", "H013": "D-S04-103", "H014": "D-S03-106", "H016": "D-S00-101", "H018": "D-S05-102",
    "H019": "D-S03-107", "H020": "D-S04-102",
}
SPEC_H_CODES = {"H%03d" % i for i in range(1, 21)}

# a terv 14. fejezetének parancsai (+ a fejlesztői `cassette`, 19.1)
SPEC_COMMANDS = {"sources", "init", "find-reviews", "reviews", "select-reviews", "extract", "show-text",
                 "agent-classify", "resolve", "dedupe", "proposals", "decide", "overlap", "screen", "update-search",
                 "cite-search", "merge", "prisma", "signoff", "verify-secondary", "export", "status", "verify",
                 "rebuild", "report", "cassette"}

# PubMed-absztraktból (efetch) vagy nyílt teljes szövegből (Ying 2025: PMC12527530; Hennessy 2020: PMC8555740)
# ellenőrzött állítások — a tudásegység szövegének ezeket kell tartalmaznia
VERIFIED_FACTS = {
    "K-HH-001": ["(N − r) / (r × c − r)", "30%", "index publication"],
    "K-HH-002": ["0–5% slight", "6–10% moderate", "11–15% high", "above 15% very high",
                 "guidelines rather than strict rules"],
    "K-HH-003": ["five steps", "create the citation matrix", "not necessarily a bias"],
    "K-HH-004": ["square root of its sample size", "n = 836", "5.3%", "6.6%", "3.2%", "11.5%"],
    "K-HH-005": ["27 meta-analyses", "10 of the 27 (37%)", "0.6 or more", "17 meta-analyses (63%)", "7 of the 10"],
    "K-HH-006": ["20 of 34", "(2 reviews)", "(7 reviews)", "none affected the review conclusions"],
    "K-HH-007": ["six studies", "up to 50%"],
    "K-HH-008": ["84 trials", "3,335", "17%", "28%", "6.4", "4.9", "23% overestimation"],
    "K-HH-009": ["56 systematic reviews", "six duplication patterns", "5.3% (65/1234)", "64%",
                 "authorship is an unreliable criterion"],
    "K-HH-010": ["Ovid, Covidence and Rayyan were the most accurate", "Rayyan the highest sensitivity"],
    "K-HH-011": ["1845 to 79,880", "0.95 to 0.99", "above 0.99", "less than 1 hour"],
    "K-HH-012": ["47 studies", "1985 and 2021", "96%"],
    "K-HH-015": ["100 quantitative systematic reviews", "57% (95% CI 47% to 67%)", "5.5 years (CI 4.6 to 7.6 years)",
                 "within 2 years for 23%", "within 1 year for 15%", "in 7%"],
    "K-HH-016": ["10 questions"],
    "K-HH-017": ["24 papers", "do not substantially overlap", "up to date"],
    "K-HH-018": ["Pollock M, Fernandes RM, Becker LA, Pieper D, Hartling L",
                 "the unit of searching, inclusion and data analysis is the systematic review"],
    "K-HH-021": ["35 overviews", "avoid double-counting outcome data"],
    "K-HH-022": ["32 of 60", "median CCA was 4.0"],
    "K-HH-023": ["27 main items with 19 sub-items"],
}
VERIFIED_RULE_FACTS = {
    "D-S03-101": ["96%"],
    "D-S03-104": ["5,5 év", "23%", "15%"],
    "D-S04-102": ["17%", "28%", "23%"],
    "D-S05-101": ["10-ben (37%)", "34 Cochrane-áttekintésből 20-ban", "akár 50%"],
    "D-S13-101": ["60 overview-ból csak 32", "4,0"],
}
# a százalékértékek engedélylistája elemenként (csak ellenőrzött szám; bővítés = új ellenőrzés)
PERCENT_ALLOW = {
    "K-HH-001": {"30%"}, "K-HH-002": {"5%", "10%", "15%"}, "K-HH-004": {"5.3%", "6.6%", "3.2%", "11.5%"},
    "K-HH-005": {"37%", "63%"}, "K-HH-007": {"50%"}, "K-HH-008": {"17%", "28%", "23%"},
    "K-HH-009": {"5.3%", "64%"}, "K-HH-012": {"96%"},
    "K-HH-015": {"57%", "95%", "47%", "67%", "23%", "15%", "7%"},
    "D-S03-101": {"96%"}, "D-S03-104": {"23%", "15%"}, "D-S03-108": {"5%", "10%", "15%"},
    "D-S04-102": {"17%", "28%", "23%"}, "D-S04-105": {"23%"}, "D-S05-101": {"37%", "50%"},
    "D-S05-102": {"37%"}, "D-S13-101": {"5%", "10%", "15%"},
}
PERCENT_RE = re.compile(r"\d+(?:[.,]\d+)?%")
KB_REF_RE = re.compile(r"(?<![\w-])(D-S\d{2}-\d{3}|K-[A-Z0-9]+-\d{3}|[VPX]\d{3})(?![\w-])")

AGENT_TOOLS_REQUIRED = {
    "Read", "Grep", "Glob", "Bash", "Write", "WebFetch",
    "mcp__PubMed__search_articles", "mcp__PubMed__get_article_metadata", "mcp__PubMed__lookup_article_by_citation",
    "mcp__PubMed__convert_article_ids", "mcp__PubMed__get_full_text_article", "mcp__Clinical_Trials__get_trial_details",
    "mcp__claude_ai_PubMed", "mcp__claude_ai_Clinical_Trials",
}
AGENT_KEYS = {"name", "description", "model", "effort", "maxTurns", "tools", "disallowedTools", "skills", "memory",
              "background", "omitClaudeMd", "isolation", "color", "experimental"}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _load(name):
    with open(os.path.join(SEED, name), encoding="utf-8") as fh:
        return json.load(fh)


def _hh(table):
    return _load(HH_FILES[table])


def _all_seed_ids(exclude_hh=False):
    """{azonosító: (fájl, tábla)} minden seed-fájlból (a példák is; a HH-fájlok kihagyhatók)."""
    hh = set(HH_FILES.values())
    out = {}
    for pattern, table, cols in kb.SEED_TABLES:
        for f in sorted(glob.glob(os.path.join(SEED, pattern))):
            name = os.path.basename(f)
            if exclude_hh and name in hh:
                continue
            for it in _load(name):
                out.setdefault(it[cols[0]], []).append((name, table))
    return out


def _texts(item, fields):
    return " ".join(str(item.get(f) or "") for f in fields)


def _frontmatter(text):
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


def _run(args, env=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.update(env or {})
    return subprocess.run([sys.executable, os.path.join(ROOT, "ma.py")] + list(args), cwd=ROOT, env=e,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                          timeout=300)


def _checks_module():
    try:
        return importlib.import_module("metaelemzes.headhunter.checks")
    except Exception:          # a HH-B még építi (párhuzamos munka): a hozzá kötött ellenőrzés kimarad
        return None


# =========================================================================== seed-fájlok
class TestSeedFiles(unittest.TestCase):

    def test_files_exist_match_kb_patterns_and_columns(self):
        for table, name in HH_FILES.items():
            pattern = [p for p, t, _c in kb.SEED_TABLES if t == table][0]
            self.assertIn(os.path.join(SEED, name), glob.glob(os.path.join(SEED, pattern)), name)
            items = _load(name)
            self.assertIsInstance(items, list, name)
            self.assertTrue(items, name)
            for it in items:
                with self.subTest(file=name, id=it.get(SEED_COLS[table][0])):
                    self.assertEqual(set(it), set(SEED_COLS[table]))
                    for col in SEED_COLS[table]:
                        v = it[col]
                        self.assertTrue(v is None or isinstance(v, (str, int)), col)
                        if isinstance(v, str):
                            self.assertEqual(v, v.strip(), col)
                            self.assertTrue(v, col)

    def test_id_formats_and_reserved_range(self):
        for r in _hh("decision_rule"):
            m = re.fullmatch(r"D-S(\d{2})-(\d{3})", r["rule_id"])
            self.assertIsNotNone(m, r["rule_id"])
            self.assertTrue(101 <= int(m.group(2)) <= 149, "a 101–149 tartomány a Metaheadhunteré: %s" % r["rule_id"])
            self.assertEqual(r["stage_id"], "S" + m.group(1), r["rule_id"])
        ks = [k["k_id"] for k in _hh("knowledge")]
        for kid in ks:
            self.assertRegex(kid, r"^K-HH-\d{3}$")
        self.assertEqual(ks, sorted(ks))
        for s in _hh("source"):
            self.assertRegex(s["source_id"], r"^[a-z][a-z0-9_]*$")

    def test_no_id_collisions(self):
        hh_ids = []
        for table in HH_FILES:
            hh_ids += [it[SEED_COLS[table][0]] for it in _hh(table)]
        self.assertEqual(len(hh_ids), len(set(hh_ids)), "duplikált azonosító a HH-seedekben")
        others = _all_seed_ids(exclude_hh=True)
        self.assertEqual(sorted(set(hh_ids) & set(others)), [], "ütközés más seed-azonosítóval")
        engine = set(kb._engine_rules())
        self.assertEqual(sorted(set(hh_ids) & engine), [], "ütközés motor-szabállyal")

    def test_enums_and_stages(self):
        stages = {s["stage_id"] for s in _load("stages.json")}
        schema = _read(kb.SCHEMA)
        kinds = set(re.findall(r"'(\w+)'", re.search(r"kind\s+TEXT NOT NULL CHECK \(kind IN \(([^)]*)\)", schema).group(1)))
        for r in _hh("decision_rule"):
            with self.subTest(rule=r["rule_id"]):
                self.assertIn(r["stage_id"], stages)
                self.assertIn(r["applies_to"], {"planner", "reviewer", "evaluator", "orchestrator", "all"})
                self.assertIn(r["strength"], {"must", "should", "consider", "avoid"})
                self.assertRegex(r["rationale"], r"Kulcsszavak \(EN\): ")
        for k in _hh("knowledge"):
            with self.subTest(k=k["k_id"]):
                self.assertIn(k["stage_id"], stages)
                self.assertIn(k["kind"], kinds)
                self.assertTrue(k["locator"])
                self.assertTrue(k["tags"])

    def test_source_references_resolve(self):
        sources = {s["source_id"] for f in glob.glob(os.path.join(SEED, "sources*.json"))
                   for s in _load(os.path.basename(f))}
        hh_sources = {s["source_id"] for s in _hh("source")}
        used = set()
        for r in _hh("decision_rule"):
            ids = [x.strip() for x in r["source_ids"].split(",")]
            self.assertTrue(ids and all(ids), r["rule_id"])
            for sid in ids:
                self.assertIn(sid, sources, "%s → %s" % (r["rule_id"], sid))
            used |= set(ids)
        for k in _hh("knowledge"):
            self.assertIn(k["source_id"], sources, k["k_id"])
            used.add(k["source_id"])
        self.assertEqual(sorted(hh_sources - used), [], "használatlan HH-forrás")
        self.assertEqual(sorted(used - hh_sources - REUSED_SOURCES), [], "nem várt meglévő forrás")
        self.assertEqual(sorted(REUSED_SOURCES - used), [], "a felsorolt meglévő forrás nincs használva")

    def test_cross_references_exist(self):
        known = set(_all_seed_ids()) | set(kb._engine_rules())
        bad = []
        for r in _hh("decision_rule"):
            for ref in KB_REF_RE.findall(_texts(r, ("condition", "recommendation", "rationale", "locator"))):
                if ref not in known:
                    bad.append((r["rule_id"], ref))
        for k in _hh("knowledge"):
            for ref in KB_REF_RE.findall(_texts(k, ("title", "body", "locator"))):
                if ref not in known:
                    bad.append((k["k_id"], ref))
        self.assertEqual(bad, [])

    def test_sources_match_verified_bibliography(self):
        items = {s["source_id"]: s for s in _hh("source")}
        self.assertEqual(sorted(items), sorted(VERIFIED_SOURCES), "csak ellenőrzött forrás kerülhet a seedbe")
        for sid, (pmid, doi, year, surname) in VERIFIED_SOURCES.items():
            s = items[sid]
            with self.subTest(source=sid):
                self.assertEqual(s["doi"], doi)
                self.assertEqual(s["year"], year)
                self.assertTrue(s["citation"].startswith(surname + " "), s["citation"])
                self.assertIn("%d;" % year, s["citation"])
                self.assertRegex(s["notes"], r"PMID: %s;" % pmid)
                self.assertIn("ellenőrizve PubMed", s["notes"])
                self.assertEqual(s["license_note"], "csak hivatkozás (teljes szöveg nincs a tudásbázisban)")
                self.assertIsNone(s["file_hint"])
                self.assertIn(s["kind"], {"article", "guide", "standard", "exemplar", "method"})
                if doi:
                    self.assertRegex(doi, r"^10\.\d{4,9}/\S+$")

    def test_numeric_claims_are_the_verified_ones(self):
        k = {x["k_id"]: x for x in _hh("knowledge")}
        r = {x["rule_id"]: x for x in _hh("decision_rule")}
        for kid, facts in VERIFIED_FACTS.items():
            for fact in facts:
                self.assertIn(fact, k[kid]["body"], kid)
        for rid, facts in VERIFIED_RULE_FACTS.items():
            for fact in facts:
                self.assertIn(fact, r[rid]["rationale"], rid)
        bad = []
        for kid, it in k.items():
            for tok in set(PERCENT_RE.findall(it["body"] + " " + it["title"])):
                if tok not in PERCENT_ALLOW.get(kid, set()):
                    bad.append((kid, tok))
        for rid, it in r.items():
            for tok in set(PERCENT_RE.findall(_texts(it, ("condition", "recommendation", "rationale")))):
                if tok not in PERCENT_ALLOW.get(rid, set()):
                    bad.append((rid, tok))
        self.assertEqual(bad, [], "ellenőrizetlen százalékérték (előbb ellenőrizd a forrásban, majd bővítsd a "
                                  "PERCENT_ALLOW-t)")

    def test_title_level_sources_make_no_content_claims(self):
        """A PubMedben absztrakt nélküli forrásokra (TARCiS, Bramer) a KB csak cím szinten hivatkozik."""
        k = {x["k_id"]: x for x in _hh("knowledge")}
        for kid in ("K-HH-013", "K-HH-025"):
            self.assertIn("title level only", k[kid]["body"], kid)
            self.assertFalse(PERCENT_RE.search(k[kid]["body"]), kid)

    def test_machine_checks_match_spec_both_ways(self):
        got = {}
        for r in _hh("decision_rule"):
            mc = r["machine_check"]
            if mc is None:
                continue
            self.assertTrue(mc.startswith("metaelemzes.headhunter.checks:"), r["rule_id"])
            codes = re.findall(r"\bH\d{3}\b", mc)
            self.assertTrue(codes, r["rule_id"])
            self.assertEqual(mc, "metaelemzes.headhunter.checks:" + ", ".join(codes), r["rule_id"])
            for c in codes:
                self.assertIn(c, SPEC_H_CODES, r["rule_id"])
                self.assertNotIn(c, got, "%s két szabályban" % c)
                got[c] = r["rule_id"]
        self.assertEqual(got, SPEC_H_KB)

    def test_machine_checks_exist_in_engine_checks(self):
        checks = _checks_module()
        if checks is None or not hasattr(checks, "RULES"):
            self.skipTest("a metaelemzes.headhunter.checks még nem érhető el (HH-B)")
        codes = {c for r in _hh("decision_rule") for c in re.findall(r"\bH\d{3}\b", r["machine_check"] or "")}
        self.assertEqual(sorted(codes - set(checks.RULES)), [])
        refs = getattr(checks, "KB_REFS", None)
        if isinstance(refs, dict):
            known = set(_all_seed_ids()) | set(kb._engine_rules())
            missing = sorted({x for v in refs.values() for x in (v if isinstance(v, (list, tuple)) else [v])}
                             - known)
            self.assertEqual(missing, [], "a checks.KB_REFS nem létező KB-azonosítóra mutat")

    def test_must_stages_are_queried_by_existing_agents(self):
        """A tervező/értékelő ágens a saját must-szabályainak minden szakaszát lekérdezi (test_fixes_R2_content
        AG-08) — az új HH-szabályok ezt nem törhetik el."""
        agents = os.path.join(REPO, ".claude", "agents")
        if not os.path.isdir(agents):
            self.skipTest("plugin-telepítésből fut")
        for role, doc in (("planner", "ma-tervezo.md"), ("evaluator", "ma-ertekelo.md")):
            text = _read(os.path.join(agents, doc))
            covered = set()
            for line in text.splitlines():
                if "kb rules" in line and ("--agent %s" % role) in line:
                    for a, b in re.findall(r"--stage\s+S(\d\d)(?:-S(\d\d))?", line):
                        covered |= {"S%02d" % i for i in range(int(a), int(b or a) + 1)}
            must = {r["stage_id"] for r in _hh("decision_rule") if r["applies_to"] == role and r["strength"] == "must"}
            self.assertEqual(sorted(must - covered), [], doc)


# =========================================================================== tudásbázis-építés
class TestKbBuild(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hh_kb_")
        cls.db = os.path.join(cls.tmp, "kb.sqlite")
        cls.counts = kb.build(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _ids(self, sql):
        _cols, rows = kb.query(sql, db=self.db)
        return {r[0] for r in rows}

    def test_build_loads_all_hh_items(self):
        self.assertEqual(self.counts["foreign_key_problems"], 0)
        self.assertNotIn("figyelmeztetesek", self.counts)
        rules = self._ids("SELECT rule_id FROM decision_rule")
        know = self._ids("SELECT k_id FROM knowledge")
        src = self._ids("SELECT source_id FROM source")
        self.assertLessEqual({r["rule_id"] for r in _hh("decision_rule")}, rules)
        self.assertLessEqual({k["k_id"] for k in _hh("knowledge")}, know)
        self.assertLessEqual({s["source_id"] for s in _hh("source")}, src)

    def test_rules_and_search(self):
        ids = {r["rule_id"] for r in kb.rules(stage="S03", applies_to="planner", db=self.db)}
        self.assertLessEqual({"D-S03-101", "D-S03-102", "D-S03-104", "D-S03-108", "D-S03-109"}, ids)
        ids = {r["rule_id"] for r in kb.rules(stage="S04", applies_to="reviewer", db=self.db)}
        self.assertLessEqual({"D-S04-101", "D-S04-102", "D-S04-103", "D-S04-105"}, ids)
        hits = kb.search("corrected covered area", db=self.db, scopes=("rule", "knowledge"))
        self.assertIn("K-HH-001", [h["id"] for h in hits["knowledge"]])
        self.assertTrue({"D-S03-108", "D-S13-101"} & {h["id"] for h in hits["rule"]})
        hits = kb.search("secondary data verify", db=self.db, scopes=("rule",))
        self.assertIn("D-S05-101", [h["id"] for h in hits["rule"]])
        shown = kb.show("K-HH-019", db=self.db)
        self.assertEqual(shown["_table"], "knowledge")
        self.assertEqual(kb.show("pieper2014_cca", db=self.db)["doi"], "10.1016/j.jclinepi.2013.11.007")

    def test_cli_build_with_env_and_strict_project_log(self):
        db = os.path.join(self.tmp, "cli_kb.sqlite")
        env = {"METAELEMZES_KB": db}
        p = _run(["kb", "build"], env)
        self.assertEqual(p.returncode, 0, p.stderr)
        counts = json.loads(p.stdout)
        self.assertEqual(counts["foreign_key_problems"], 0)
        p = _run(["kb", "show", "D-S04-105"], env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("D-S04-105", p.stdout)
        proj = os.path.join(self.tmp, "proj")
        p = _run(["project", "init", proj, "--title", "HH KB próba", "--question", "BCG és tuberkulózis"], env)
        self.assertEqual(p.returncode, 0, p.stderr)
        p = _run(["project", "log", proj, "--agent", "planner", "--stage", "S03",
                  "--decision", "Metaheadhunter: indulás (teszt)", "--kb", "D-S00-101,D-S03-101,K-HH-019",
                  "--strict", "--kb-db", db], env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


# =========================================================================== ágensfájl
@unittest.skipUnless(os.path.isfile(AGENT), "plugin-telepítésből fut: a .claude/agents forrás nincs jelen")
class TestAgentDefinition(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = _read(AGENT)
        cls.fm, cls.body = _frontmatter(cls.text)

    def test_frontmatter(self):
        fm = self.fm
        self.assertLessEqual(set(fm), AGENT_KEYS)
        self.assertEqual(_scalar(fm["name"]), "ma-metaheadhunter")
        self.assertEqual(fm["model"], "inherit")
        self.assertTrue(fm["description"].startswith('"'))
        desc = _scalar(fm["description"])
        for word in ("Metaheadhunter", "Használd", "bizonyíték", "EP1–EP6"):
            self.assertIn(word, desc)
        for key, raw in fm.items():
            if not raw.startswith('"'):
                self.assertNotIn(": ", raw, key)
                self.assertNotIn(" #", raw, key)
        tools = {t.strip() for t in fm["tools"].split(",")}
        self.assertLessEqual(AGENT_TOOLS_REQUIRED, tools)
        self.assertNotIn("Edit", tools)

    def test_pipeline_checkpoints_and_exit_codes(self):
        body = self.body
        for ep in ("EP1", "EP2", "EP3", "EP4", "EP5", "EP6"):
            self.assertIn(ep, body)
        for code in range(5):
            self.assertRegex(body, r"\| %d \|" % code)
        for must in ("agent-classify import", "show-text", "--dry-run", "update_window", "agent_classification/",
                     "verify-secondary", "sources --check", "## Tilos", "csak memóriában", "300 karakter"):
            self.assertIn(must, body)

    def test_commands_follow_the_cli_contract(self):
        cmds = set(re.findall(r"(?<![\w.])headhunter\s+([a-z][a-z-]+)", self.body))
        cmds |= set(re.findall(r"metaelemzes\.headhunter\s+([a-z][a-z-]+)", self.body))
        self.assertTrue(cmds)
        self.assertEqual(sorted(cmds - SPEC_COMMANDS), [])
        try:
            cli = importlib.import_module("metaelemzes.headhunter.cli")
            parser = cli.build_parser()
        except Exception:
            self.skipTest("a metaelemzes.headhunter.cli build_parser még nem érhető el (párhuzamos munka)")
        import argparse
        subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        self.assertTrue(subs)
        self.assertEqual(sorted(cmds - set(subs[0].choices)), [])

    def test_decisions_are_never_recorded_as_agent(self):
        self.assertNotRegex(self.body, r"--actor\s+agent:")
        for cmd in re.findall(r"`([^`]*\b(?:decide|select-reviews|signoff|verify-secondary)\b[^`]*)`", self.body):
            if "--actor" in cmd:
                self.assertIn("--actor user:", cmd, cmd)

    def test_kb_references_exist(self):
        known = set(_all_seed_ids()) | set(kb._engine_rules())
        refs = set(KB_REF_RE.findall(self.text))
        self.assertTrue(refs)
        self.assertEqual(sorted(refs - known), [])
        for cl in re.findall(r"kb checklist ([A-Z0-9_]+)", self.text):
            self.assertIn(cl, kb.CHECKLISTS)
        for cmd in re.findall(r"`([^`]*\bproject log\b[^`]*)`", self.text):
            if re.search(r"--kb\s+[^\s-]", cmd):
                self.assertIn("--strict", cmd)

    def test_repo_paths_exist(self):
        refs = re.findall(r"(?<![\w/.\-])(metaanalizis-asszisztens/[\w./-]+|\.claude/[\w./-]+)", self.text)
        for ref in refs:
            ref = ref.rstrip(".")
            self.assertTrue(os.path.exists(os.path.join(REPO, *ref.split("/"))), ref)

    def test_plugin_transform(self):
        if not os.path.isfile(BUILD_PLUGIN):
            self.skipTest("nincs plugin-generátor")
        spec = importlib.util.spec_from_file_location("build_plugin_hh", BUILD_PLUGIN)
        bp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bp)
        agents = bp.agent_names(REPO)
        self.assertIn("ma-metaheadhunter", agents)
        out = bp.transform_markdown(self.text, ".claude/agents/ma-metaheadhunter.md", agents, bp.skill_names(REPO))
        fm, body = _frontmatter(out)
        self.assertEqual(_scalar(fm["name"]), "ma-metaheadhunter")
        json.loads(fm["description"])
        bare = re.compile(r"(?<![\w:/.\-])(%s)(?![\w/:\-]|\.\w)" % "|".join(map(re.escape, agents)))
        self.assertIsNone(bare.search(body), "névtér nélküli ágensnév a plugin-változatban")
        self.assertNotRegex(body, r"(?<![\w/.\-])metaanalizis-asszisztens/")
        for ref in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)", body):
            ref = ref.rstrip(".")
            self.assertTrue(os.path.exists(os.path.join(ROOT, *ref.split("/"))), ref)


# =========================================================================== élő bibliográfia-ellenőrzés
@unittest.skipUnless(LIVE, "élő teszt: MA_LIVE_TESTS=1")
class TestLiveCitations(unittest.TestCase):
    """A HH-források PMID-jeit a PubMed esummary-vel ellenőrzi (DOI, év, első szerző)."""

    def test_pubmed_esummary(self):
        import urllib.parse
        import urllib.request
        ids = ",".join(v[0] for v in VERIFIED_SOURCES.values())
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode(
            {"db": "pubmed", "id": ids, "retmode": "json", "tool": "metaelemzes-headhunter-kbtest"})
        req = urllib.request.Request(url, headers={"User-Agent": "metaelemzes-headhunter/kb-test (python-urllib)"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))["result"]
        for sid, (pmid, doi, year, surname) in VERIFIED_SOURCES.items():
            with self.subTest(source=sid, pmid=pmid):
                rec = data[pmid]
                dois = [a["value"].lower() for a in rec.get("articleids", []) if a.get("idtype") == "doi"]
                if doi:
                    self.assertIn(doi.lower(), dois)
                else:
                    self.assertEqual(dois, [])
                self.assertTrue(rec["pubdate"].startswith(str(year)), rec["pubdate"])
                self.assertTrue(rec["authors"][0]["name"].startswith(surname + " "), rec["authors"][0]["name"])


if __name__ == "__main__":
    unittest.main()
