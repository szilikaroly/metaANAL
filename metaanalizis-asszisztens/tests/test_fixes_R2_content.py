# -*- coding: utf-8 -*-
"""Regressziós tesztek a 2. review-kör tartalmi javításaihoz (tudásbázis-seedek, sablonok, README,
ágens- és skill-promptok, .gitignore).

Minden teszt neve a megállapítás azonosítóját viseli (pl. KB_R2_02 = kb_content:KB-R2-02,
AG_13 = agents_e2e:AG-13, R2_NF_04 = stats_new:R2-NF-04). A tudásbázis ideiglenes fájlban épül.
"""
import csv
import glob
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _helpers import ROOT, assert_close
from metaelemzes import cli, kb, models, moderators, projekt, validate
from metaelemzes import conversions as C

REPO = os.path.dirname(ROOT)
SEED = os.path.join(ROOT, "tudasbazis", "seed")
SABLON = os.path.join(ROOT, "tudasbazis", "sablonok")
AGENTS = os.path.join(REPO, ".claude", "agents")
SKILL = os.path.join(REPO, ".claude", "skills", "metaanalizis", "SKILL.md")
README = os.path.join(ROOT, "README.md")
ESZKOZOK = os.path.join(ROOT, "ESZKOZOK_ES_HOZZAFERESEK.md")
ID_KEYS = ("k_id", "formula_id", "rule_id", "item_id", "tool_id", "example_id", "source_id")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _seed_items():
    out = {}
    for f in sorted(glob.glob(os.path.join(SEED, "*.json"))):
        if os.path.basename(f).startswith("examples"):
            continue
        for it in json.loads(_read(f)):
            for k in ID_KEYS:
                if k in it:
                    out[it[k]] = it
                    break
    return out


ITEMS = _seed_items()


def item(iid):
    return ITEMS[iid]


def text_of(it):
    return " ".join(v for v in it.values() if isinstance(v, str))


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        try:
            code = cli.main([str(a) for a in args])
        except SystemExit as exc:
            code = exc.code
    return code, buf.getvalue(), err.getvalue()


def agent_docs():
    return {os.path.basename(f): _read(f) for f in glob.glob(os.path.join(AGENTS, "*.md"))}


class _KbTmp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "kb.sqlite")
        kb.build(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)


# ======================================================== motor-képesség a tudásbázisban
NEGATION = re.compile(r"not implemented|does not implement|implements neither|engine has no|"
                      r"has no [\w-]+ function|not in the engine|"
                      r"does not output|uses only|only LS|engine_ref is null|take (?:those|these) results from",
                      re.I)
# a motor által MÁR tudott képességek kulcsszavai (ha egy mondat ezek hiányát állítja, az elavult)
IMPLEMENTED = re.compile(r"power|LFK|Glass|2\(N ?[-−] ?(?:2|3\.94)\)|MetaXL variances|HC1|robust \(HC|"
                         r"find\.outliers|modified H|H2 = Q/df|equal-variance|unequal-variance|unpooled|"
                         r"weight share|subgroup's reported SE", re.I)
# valóban hiányzó módszerek: a mondatban ezek egyike szerepel → a tagadás helytálló
GENUINELY_MISSING = re.compile(r"\bQE\b|quality-effects|CR2|cluster|permutation|single-mean|n-denominator|"
                               r"pretest|composite|prediction function|Hozo|multivariate|three-level|RVE|"
                               r"dose|GLST|REMR|Morris|Hedges' \(1981\)|uncorrected d|Var\(g\) = c\^2|"
                               r"rescaling", re.I)


def _sentences(s):
    return re.split(r"(?<=[.;])\s+|\[note:", s)


class TestEngineCapabilityNotes(unittest.TestCase):
    """stats_new:R2-NF-04, kb_content:KB-R2-01 — a KB nem állíthatja, hogy a motor nem tud olyat, amit tud."""

    LISTED_FORMULAS = {
        "F-KHN08-010": "effect_sizes.glass_delta", "F-KHN08-013": "effect_sizes.glass_delta",
        "F-KHN08-012": "effect_sizes.smd_variance_from_estimate",
        "F-KHN08-015": "effect_sizes.smd_variance_from_estimate",
        "F-KHN1113-012": "moderators._robust_hc1", "F-ZHA22-025": "power.power_analysis",
        "F-ZHA22-023": "sensitivity.outlier_screen", "F-KHN1113-011": "models.heterogeneity"}

    def test_R2_NF_04_listed_formulas_have_engine_ref_and_no_stale_note(self):
        for fid, ref in self.LISTED_FORMULAS.items():
            it = item(fid)
            self.assertEqual(it["engine_ref"], ref, fid)
            self.assertNotRegex(it["notes"], r"(?i)not implemented|does not implement|not in the engine", fid)
        self.assertIn("H2_M", item("F-KHN1113-011")["notes"])

    def test_R2_NF_04_every_engine_ref_resolves(self):
        bad = []
        for iid, it in ITEMS.items():
            ref = it.get("engine_ref")
            if not ref:
                continue
            mod, _, attr = ref.partition(".")
            try:
                ok = hasattr(importlib.import_module("metaelemzes." + mod), attr)
            except ImportError:
                ok = False
            if not ok:
                bad.append((iid, ref))
        self.assertEqual(bad, [])

    def test_R2_NF_04_knowledge_units_no_longer_deny_lfk_power_metan(self):
        self.assertNotIn("not implemented in the engine", item("K-KHN08-026")["body"])
        self.assertIn("METAN_HEDGES", item("K-KHN08-026")["body"])
        self.assertNotIn("does not implement the LFK", item("K-KHN0102-065")["body"])
        b = item("K-KHN0102-085")["body"]
        self.assertNotIn("QE and the LFK index are not implemented", b)
        self.assertIn("bias.doi_plot_data", b)
        self.assertIn("QE is not implemented", b)          # a QE valóban hiányzik
        self.assertIn("bias.doi_plot_data", item("K-KHN0102-003")["body"])

    def test_KB_R2_01_capability_lint(self):
        """Seed-lint: tudás- és képletegységben egy mondat sem tagadhat már implementált képességet."""
        bad = []
        for iid, it in ITEMS.items():
            if not (iid.startswith("K-") or iid.startswith("F-")):
                continue
            for field in ("body", "notes"):
                for s in _sentences(it.get(field) or ""):
                    if NEGATION.search(s) and IMPLEMENTED.search(s) and not GENUINELY_MISSING.search(s):
                        bad.append((iid, s.strip()[:120]))
        self.assertEqual(bad, [])

    def test_KB_R2_01_power_units(self):
        for kid in ("K-CHE16-010", "K-ZHA22-010"):
            body = item(kid)["body"]
            self.assertNotRegex(body, r"(?i)engine has no (meta-analytic )?power", kid)
            self.assertIn("ma.py power", body, kid)

    def test_KB_R2_01_subgroup_q_between_wald_regardless_of_ci(self):
        notes = item("F-MOR20-031")["notes"]
        self.assertNotIn("pass ci_method='z'", notes)
        self.assertIn("Wald", notes)
        # a KB állítása igaz: a Q_between nem függ a megjelenített CI-módszertől
        yi = [0.1, 0.3, 0.5, 0.2, 0.7, 0.4, 0.9, 0.35]
        vi = [0.04, 0.03, 0.05, 0.02, 0.06, 0.03, 0.05, 0.04]
        g = ["a", "a", "a", "a", "b", "b", "b", "b"]
        q = [moderators.subgroup_analysis(yi, vi, g, ci_method=m).Q_between for m in ("hksj", "z", "t")]
        assert_close(self, q[0], q[1], 1e-12)
        assert_close(self, q[0], q[2], 1e-12)

    def test_KB_R2_01_rule_no_longer_calls_glass_notes_outdated(self):
        self.assertNotIn("elavult", item("D-S07-006")["rationale"])


# ======================================================== AMSTAR 2: regisztráció hiánya
class TestRegistrationAmstar(unittest.TestCase):
    """kb_content:KB-R2-02"""

    def test_KB_R2_02_missing_registration_is_partial_yes_not_critical(self):
        bad = []
        pat_reg = re.compile(r"regisztráció hiánya|no ID is found|without registration|unregistered", re.I)
        pat_crit = re.compile(r"kritikus gyengeség|critical AMSTAR 2 weakness", re.I)
        for iid, it in ITEMS.items():
            for s in _sentences(text_of(it)):
                if pat_reg.search(s) and pat_crit.search(s):
                    bad.append(iid)
        self.assertEqual(bad, [])
        self.assertIn("részben igen", item("REVIEWER-08")["text"])
        self.assertIn("D-S02-019", item("REVIEWER-08")["text"])
        self.assertIn("partial yes", item("K-MOR20-007")["body"])
        self.assertNotIn("megfogalmazása a protokoll teljes hiányára", item("D-S02-019")["rationale"])


# ======================================================== Higgins–Thompson középpont
class TestHtCentreDescription(unittest.TestCase):
    """kb_content:KB-R2-04"""

    def test_KB_R2_04_units_describe_truncated_default(self):
        k = item("K-ZHA22-050")["body"]
        f = item("F-ZHA22-016")
        self.assertNotIn("the engine centres at ln H", k)
        self.assertNotIn("(centred at ln H) gives", f["notes"])
        for s in (k, f["notes"]):
            self.assertIn("truncated", s)
            self.assertIn("untruncated", s)
        self.assertIn("ln(max(H, 1))", f["expression"])

    def test_KB_R2_04_engine_matches_described_numbers(self):
        lo, hi, hlo, hhi = models.i2_ci_higgins_thompson(17.45, 19)
        self.assertEqual(round(hi, 1), 48.9)
        self.assertEqual(round(hhi, 2), 1.40)
        lo, hi, hlo, hhi = models.i2_ci_higgins_thompson(17.45, 19, h_centre="untruncated")
        self.assertEqual(round(hi, 1), 47.3)
        self.assertEqual(round(hhi, 2), 1.38)


# ======================================================== Doi-plot / LFK
class TestDoiLfkCaveats(unittest.TestCase):
    """kb_content:KB-R2-03"""

    LISTED = ("K-KHN0102-066", "K-KHN05-057", "K-KHN0304-080", "K-KHN08-069", "K-KHN08-066",
              "K-KHN05-078", "K-KHN0304-072", "K-KHN0304-079")
    CAVEAT = re.compile(r"pre-specified|D-S11-015|D-S11-001|authors' approach|book's approach|author group|"
                        r"if the protocol uses it|if a Doi plot is used|not Cochrane|not among the methods the "
                        r"Cochrane|developer|heuristic", re.I)
    # a publikált számok újraszámolása (nem ajánlás)
    ALLOW = {"K-KHN1113-086"}

    def test_KB_R2_03_listed_units_follow_s11_rules(self):
        for kid in self.LISTED:
            body = item(kid)["body"]
            self.assertRegex(body, r"D-S11-0(15|01)", kid)
        self.assertNotIn("funnel and/or Doi plot", item("K-KHN0102-066")["body"])
        self.assertNotIn("Report a quantitative index", item("K-KHN05-057")["body"])
        self.assertNotIn("Report both the funnel plot and the Doi plot", item("K-KHN08-069")["body"])
        self.assertNotIn("suggestive only", item("K-KHN0304-079")["body"])

    def test_KB_R2_03_normative_units_mentioning_doi_lfk_carry_caveat(self):
        bad = []
        for iid, it in ITEMS.items():
            if not iid.startswith("K-") or it.get("kind") not in ("criterion", "check", "guidance"):
                continue
            if it.get("stage_id") not in ("S11", "S13", "S14") or iid in self.ALLOW:
                continue
            body = re.sub(r"Doi (et al|&)", "", it["body"])
            if re.search(r"Doi[- ]plot|Doi/LFK|LFK", body) and not self.CAVEAT.search(body):
                bad.append(iid)
        self.assertEqual(bad, [])


# ======================================================== RoB-eszköz expozíciós vizsgálatra
class TestRobToolExposure(unittest.TestCase):
    """kb_content:KB-R2-05"""

    def test_KB_R2_05_s01_s02_rules_match_s06(self):
        for rid in ("D-S01-005", "D-S02-011"):
            rec = item(rid)["recommendation"]
            self.assertIn("ROBINS-E", rec, rid)
            self.assertIn("összpontszám nélkül", rec, rid)
            self.assertNotIn("NOS vagy más", rec, rid)
        self.assertIn("ROBINS-E", item("D-S06-001")["recommendation"])

    def test_KB_R2_05_template_and_skill(self):
        tpl = _read(os.path.join(SABLON, "protokoll_sablon.md"))
        line = [ln for ln in tpl.splitlines() if ln.startswith("- Eszköz (")][0]
        self.assertIn("ROBINS-E", line)
        self.assertNotIn("/ NOS /", line)
        skill = _read(SKILL)
        self.assertNotIn("NOS megfigyelésesre", skill)
        self.assertIn("ROBINS-E", skill)


# ======================================================== kinyerő sablon
class TestExtractionTemplate(unittest.TestCase):
    """kb_content:KB-R2-06"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_KB_R2_06_template_has_required_columns(self):
        header = _read(os.path.join(SABLON, "adatkinyero_sablon.csv")).splitlines()[0].split(";")
        for col in ("design", "rob", "estimated", "egyseg", "meroeszkoz", "idopont", "elemzesi_populacio",
                    "forras_oldal"):
            self.assertIn(col, header, col)
        self.assertIn("`forras_oldal`", item("D-S05-001")["recommendation"])
        self.assertNotIn("forrashely", item("D-S05-001")["recommendation"])
        self.assertIn("`forras_oldal`", item("D-S05-003")["recommendation"])

    def test_KB_R2_06_design_filter_works_on_project_template(self):
        p = os.path.join(self.tmp, "proj")
        projekt.init(p, "T")
        csv = os.path.join(p, "03_adatok", "adatkinyeres.csv")
        header = _read(csv).splitlines()[0]
        cols = header.split(";")
        rows = [dict(study="A 2020", design="parallel", m1=10, sd1=2, n1=20, m2=12, sd2=2, n2=20),
                dict(study="B 2021", design="crossover", m1=11, sd1=2.5, n1=25, m2=12, sd2=2.2, n2=24),
                dict(study="C 2022", design="parallel", m1=9, sd1=3, n1=30, m2=12, sd2=3, n2=31),
                dict(study="D 2023", design="cluster", m1=10, sd1=2, n1=40, m2=11, sd2=2, n2=40)]
        with open(csv, "w", encoding="utf-8") as fh:
            fh.write(header + "\n")
            for r in rows:
                r.update(rob="low", estimated="nem", forras_oldal="p. 3")
                fh.write(";".join(str(r.get(c, "")) for c in cols) + "\n")
        out = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", csv, "--measure", "MD", "--exclude", "design=crossover",
                               "--out", out)
        self.assertEqual(code, 0, se)
        res = json.loads(_read(os.path.join(out, "results.json")))
        self.assertEqual(res["primary"]["k"], 3)
        code, so, se = run_cli("analyze", "--data", csv, "--measure", "MD", "--subgroup", "design",
                               "--out", os.path.join(self.tmp, "o2"))
        self.assertEqual(code, 0, se)


# ======================================================== PRISMA-P / PRISMA-S
CHECKLIST_REF = re.compile(r"kb checklist ([A-Z0-9_]+)")


class TestPrismaPsDocumented(_KbTmp):
    """kb_content:KB-R2-07, agents_e2e:AG-15 (2)"""

    def test_KB_R2_07_readme_and_prompts_name_prisma_p_s(self):
        names = set(kb.checklist_names(self.db))
        self.assertTrue({"PRISMA_P", "PRISMA_S"} <= names)
        readme = _read(README)
        for n in names:
            self.assertIn(n, readme, n)
        docs = agent_docs()
        self.assertIn("kb checklist PRISMA_P", docs["ma-tervezo.md"])
        self.assertIn("kb checklist PRISMA_S", docs["ma-tervezo.md"])
        self.assertIn("kb checklist PRISMA_P", docs["ma-ellenorzo.md"])
        self.assertIn("kb checklist PRISMA_S", docs["ma-ellenorzo.md"])

    def test_KB_R2_07_documented_checklists_exist(self):
        names = set(kb.checklist_names(self.db))
        wanted = set()
        for t in list(agent_docs().values()) + [_read(SKILL), _read(README)]:
            wanted |= set(CHECKLIST_REF.findall(t))
        self.assertEqual(sorted(wanted - names), [])

    def test_KB_R2_07_cli_help_and_schema_list_all_checklists(self):
        code, so, se = run_cli("kb", "-h")
        for n in ("PRISMA_P", "PRISMA_S"):
            self.assertIn(n, so, "kb -h: %s" % n)
            self.assertIn(n, _read(kb.SCHEMA), "schema.sql: %s" % n)


# ======================================================== cheung file_hint
class TestCheungHint(unittest.TestCase):
    """kb_code:KBP-08"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "kb.sqlite")

    def test_KBP_08_no_bare_surname_hints(self):
        bad = []
        for iid, it in ITEMS.items():
            if "file_hint" not in it or not it.get("file_hint"):
                continue
            authors = (it.get("citation") or "").split(".")[0].lower()
            for h in it["file_hint"].split("|"):
                if re.fullmatch(r"[a-z]+", h.lower()) and re.search(r"\b%s\b" % re.escape(h.lower()), authors):
                    bad.append((iid, h))
        self.assertEqual(bad, [])
        hints = item("cheung2016")["file_hint"].split("|")
        self.assertTrue(any(kb._hint_matches(h, "209f8d7d-57cc7782-2c17-49bc-b4d4-6d6f1ddfa7f4.pdf") for h in hints))
        self.assertTrue(any(kb._hint_matches(h, "s11065-016-9319-z.pdf") for h in hints))

    def test_KBP_08_user_cheung_file_gets_own_source_on_fresh_kb(self):
        kb.build(self.db)
        p = os.path.join(self.tmp, "Cheung 2008 SEM meta-analysis.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("Cheung 2008 SEM paper wombatword")
        res = kb.ingest(p, db=self.db)
        self.assertEqual(res[0][0], "cheung_2008_sem_meta_analysis")
        hits = kb.search("wombatword", db=self.db, scopes=("chunk",)).get("chunk", [])
        self.assertEqual([h["source_id"] for h in hits], ["cheung_2008_sem_meta_analysis"])


# ======================================================== validálási szabályok száma
class TestReadmeRuleCount(unittest.TestCase):
    """kb_code:KBP-14, agents_e2e:AG-15 (1)"""

    def _check(self):
        m = re.search(r"Adatvalidálás \| (\d+) szabály \((V\d{3}(?:–V\d{3})?(?:, V\d{3}(?:–V\d{3})?)*)(?:;[^)]*)?\)",
                      _read(README))
        self.assertIsNotNone(m)
        codes = set()
        for part in m.group(2).split(", "):
            a, _, b = part.partition("–")
            codes |= {"V%03d" % i for i in range(int(a[1:]), int((b or a)[1:]) + 1)}
        self.assertEqual(sorted(codes), sorted(validate.RULES))
        self.assertEqual(int(m.group(1)), len(validate.RULES))

    def test_KBP_14_readme_rule_count(self):
        self._check()

    def test_AG_15_readme_rule_count(self):
        self._check()

    def test_KBP_14_kb_validation_lists_cover_every_rule(self):
        for iid, field in (("D-S05-031", "recommendation"), ("REVIEWER-24", "text")):
            codes = set()
            for a, b in re.findall(r"V(\d{3})(?:–V(\d{3}))?", item(iid)[field]):
                codes |= {"V%03d" % i for i in range(int(a), int(b or a) + 1)}
            self.assertEqual(sorted(set(validate.RULES) - codes), [], iid)


# ======================================================== CLI-konverziók
NEW_KINDS = ("d-from-t", "se-from-p", "sd-from-t", "corr-from-change", "split-control",
             "logor-to-d", "d-to-logor", "r-to-d", "d-to-r")


def _convert_choices():
    import argparse
    sub = [a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction)][0]
    conv = sub.choices["convert"]
    return [a for a in conv._actions if a.dest == "kind"][0].choices


class TestConvertKinds(unittest.TestCase):
    """agents_e2e:AG-06 — minden konverzió, amelyre a KB/README küld, `ma.py convert <fajta>`-ként elérhető."""

    def _convert(self, *args):
        code, so, se = run_cli("convert", *args)
        self.assertEqual(code, 0, "convert %s: %s" % (" ".join(map(str, args)), se))
        return json.loads(so)

    def test_AG_06_kb_does_not_send_agents_to_python_functions(self):
        bad = [iid for iid, it in ITEMS.items()
               if re.match(r"(D-|REVIEWER|PREFLIGHT|EVALUATOR|GRADE|AMSTAR2|PRISMA)", iid)
               and re.search(r"`(?:metaelemzes\.)?conversions\.\w+`", " ".join(
                   it.get(f) or "" for f in ("recommendation", "text", "how_to_verify")))]
        self.assertEqual(bad, [])

    def test_AG_06_every_referenced_convert_kind_exists(self):
        texts = [text_of(it) for it in ITEMS.values()] + list(agent_docs().values()) + [_read(SKILL), _read(README)]
        ref = set()
        for t in texts:
            for m in re.finditer(r"ma\.py convert ((?:[a-z][a-z-]*\|?)+)", t):
                ref |= set(k for k in m.group(1).split("|") if k)
            for m in re.finditer(r"`convert ([a-z][a-z-]*)`", t):
                ref.add(m.group(1))
        choices = set(_convert_choices())
        self.assertTrue(set(NEW_KINDS) <= ref, sorted(set(NEW_KINDS) - ref))
        self.assertEqual(sorted(ref - choices), [])

    def test_AG_06_new_convert_kinds_match_engine_functions(self):
        r = self._convert("d-from-t", "--t", 2.5, "--n1", 20, "--n2", 22)
        assert_close(self, r["d"], C.d_from_t(2.5, 20, 22), 1e-12)
        assert_close(self, r["vi"], C.smd_variance(r["d"], 20, 22, "LS"), 1e-12)
        self.assertIn("figyelem", r)
        r = self._convert("se-from-p", "--estimate", 0.8, "--p", 0.03, "--log")
        assert_close(self, r["se"], C.se_from_p(0.8, 0.03, log_scale=True), 1e-12)
        r = self._convert("se-from-p", "--estimate", 1.5, "--p", 0.04, "--df", 18)
        assert_close(self, r["se"], C.se_from_p(1.5, 0.04, df=18), 1e-12)
        r = self._convert("sd-from-t", "--t", 2.1, "--n1", 15, "--n2", 16, "--md", 3.2)
        assert_close(self, r["sd"], C.sd_diff_from_t(2.1, 15, 16, 3.2), 1e-12)
        r = self._convert("corr-from-change", "--sd-baseline", 10, "--sd-final", 12, "--sd-change", 8)
        assert_close(self, r["corr"], C.corr_from_change(10, 12, 8), 1e-12)
        r = self._convert("split-control", "--n", 41, "--arms", 2, "--events", 9)
        self.assertEqual(r["n"], [21, 20])
        self.assertEqual(r["events"], [5, 4])
        for kind, fn, extra in (("logor-to-d", C.logor_to_d, ()), ("d-to-logor", C.d_to_logor, ()),
                                ("r-to-d", C.r_to_d, ())):
            r = self._convert(kind, "--y", 0.3, "--v", 0.01)
            y, v = fn(0.3, 0.01)
            assert_close(self, r["y"], y, 1e-12, kind)
            assert_close(self, r["v"], v, 1e-12, kind)
        r = self._convert("d-to-r", "--y", 0.5, "--v", 0.04, "--n1", 20, "--n2", 22)
        y, v = C.d_to_r(0.5, 0.04, 20, 22)
        assert_close(self, r["y"], y, 1e-12)
        assert_close(self, r["v"], v, 1e-12)


# ======================================================== értékelő: motoron kívüli képletek, naplózott futás
class TestEvaluatorCalculations(unittest.TestCase):
    """agents_e2e:AG-07"""

    def test_AG_07_documented_exception_for_sof_formulas(self):
        t = item("EVALUATOR-00")["text"]
        for ref in ("GRADE-10a", "EVALUATOR-03a", "D-S13-007", "--project"):
            self.assertIn(ref, t, ref)
        skill = _read(SKILL)
        rule2 = skill[skill.index("2. **Minden számítást"):skill.index("3. **Minden módszertani")]
        self.assertIn("GRADE-10a", rule2)
        self.assertIn("EVALUATOR-00", rule2)

    def test_AG_07_evaluator_analyze_runs_are_logged(self):
        doc = agent_docs()["ma-ertekelo.md"]
        lines = [ln for ln in doc.splitlines() if "ma.py analyze" in ln]
        self.assertTrue(lines)
        for ln in lines:
            self.assertIn("--project", ln)


# ======================================================== ágensek kb rules lefedettsége
def _covered_stages(text, role):
    stages, generic = set(), False
    for line in text.splitlines():
        if "kb rules" not in line or ("--agent %s" % role) not in line:
            continue
        for tok in re.findall(r"--stage\s+([^\s`,;)]+)", line):
            m = re.fullmatch(r"S(\d\d)(?:-S(\d\d))?", tok)
            if m:
                a, b = int(m.group(1)), int(m.group(2) or m.group(1))
                stages |= {"S%02d" % i for i in range(a, b + 1)}
            elif tok.startswith("<"):
                generic = True
    return stages, generic


class TestAgentRuleCoverage(_KbTmp):
    """agents_e2e:AG-08"""

    def must_stages(self, role):
        cols, rows = kb.query("SELECT DISTINCT stage_id FROM decision_rule WHERE applies_to = ? AND strength = 'must'",
                              (role,), db=self.db)
        return {r[0] for r in rows}

    def test_AG_08_planner_and_evaluator_query_every_must_stage(self):
        docs = agent_docs()
        for role, doc in (("planner", "ma-tervezo.md"), ("evaluator", "ma-ertekelo.md")):
            stages, generic = _covered_stages(docs[doc], role)
            self.assertFalse(generic, doc)
            self.assertEqual(sorted(self.must_stages(role) - stages), [], doc)

    def test_AG_08_orchestrator_startup(self):
        skill = _read(SKILL)
        stages, generic = _covered_stages(skill, "orchestrator")
        self.assertIn("S00", stages)
        self.assertTrue(generic or self.must_stages("orchestrator") <= stages)
        start = skill[skill.index("### 0. Indítás"):skill.index("### 1. Tervezés")]
        self.assertIn("ma.py selftest", start)
        self.assertIn("D-S00-001", start)
        self.assertNotIn("csak PASS után haladj", skill)
        self.assertIn("PASS_WITH_FIXES", skill[skill.index("### 1. Tervezés"):skill.index("### 2. Végrehajtás")])


# ======================================================== integritás-jelölés
class TestIntegrityFlag(unittest.TestCase):
    """agents_e2e:AG-11"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_AG_11_single_integrity_value(self):
        vals = set()
        for it in ITEMS.values():
            vals |= set(re.findall(r"integritas=(\w+)", text_of(it)))
        self.assertEqual(vals, {"igen"})

    def test_AG_11_marked_study_is_excluded(self):
        csv = os.path.join(self.tmp, "d.csv")
        with open(csv, "w", encoding="utf-8") as fh:
            fh.write("study;yi;vi;integritas\nA;0.1;0.04;nem\nB;0.5;0.05;igen\nC;0.2;0.03;nem\nD;0.3;0.06;nem\n")
        out = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", csv, "--measure", "GEN", "--exclude", "integritas=igen",
                               "--out", out)
        self.assertEqual(code, 0, se)
        self.assertEqual(json.loads(_read(os.path.join(out, "results.json")))["primary"]["k"], 3)


# ======================================================== SMD Egger
class TestSmdEgger(unittest.TestCase):
    """agents_e2e:AG-12"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_AG_12_prompts_flag_smd_egger(self):
        docs = agent_docs()
        for d in ("ma-tervezo.md", "ma-ertekelo.md"):
            self.assertNotIn("folytonos kimenet: Egger", docs[d], d)
            self.assertIn("SMD: a klasszikus Egger csak tájékoztató", docs[d], d)
        self.assertIn("SMD-nél a klasszikus Egger csak", docs["ma-ellenorzo.md"])
        self.assertIn("SMD-nél a klasszikus Egger csak", _read(SKILL))

    def test_AG_12_report_flags_smd_egger(self):
        src = os.path.join(ROOT, "peldak", "normand1999_folytonos.csv")
        lines = _read(src).splitlines()
        extra = []
        for i, ln in enumerate(lines[1:4]):
            cells = ln.split(";") if ";" in lines[0] else ln.split(",")
            cells[0] = "Dup %d" % i
            extra.append((";" if ";" in lines[0] else ",").join(cells))
        csv = os.path.join(self.tmp, "smd12.csv")
        with open(csv, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines + extra) + "\n")
        out = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", csv, "--measure", "SMD", "--out", out)
        self.assertEqual(code, 0, se)
        res = json.loads(_read(os.path.join(out, "results.json")))
        self.assertGreaterEqual(res["bias"]["k"], 10)
        note = res["bias"].get("smd_note")
        self.assertTrue(note)
        self.assertIn(note, _read(os.path.join(out, "report.md")))
        out2 = os.path.join(self.tmp, "o2")
        code, so, se = run_cli("analyze", "--data", csv, "--measure", "MD", "--out", out2)
        self.assertEqual(code, 0, se)
        self.assertFalse(json.loads(_read(os.path.join(out2, "results.json")))["bias"].get("smd_note"))


# ======================================================== .gitignore PHI
class TestGitignorePhi(unittest.TestCase):
    """agents_e2e:AG-13"""

    def ignored(self, path):
        try:
            r = subprocess.run(["git", "-C", REPO, "check-ignore", "-q", "--no-index", path],
                               capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git nem érhető el")
        if r.returncode not in (0, 1):
            self.skipTest("git check-ignore nem futtatható: %s" % r.stderr.decode(errors="replace"))
        return r.returncode == 0

    def test_AG_13_ordinary_project_files_are_versioned(self):
        for p in ("reviews/morphine-pain/03_adatok/adatkinyeres.csv",
                  "reviews/delphi-consensus/00_protokoll/protokoll.md",
                  "reviews/x/06_kezirat/graphical_abstract.svg",
                  "reviews/glp1-terhesseg/03_adatok/demographics.csv",
                  "reviews/neutrophil-lymphocyte-sepsis/projekt.sqlite",
                  # „_phi” után betűvel folytatódó szó sem PHI-tag (a korábbi *_phi* minta egész mappákat zárt ki)
                  "reviews/dengue_philippines/03_adatok/adatkinyeres.csv",
                  "reviews/pediatric_phimosis/00_protokoll/protokoll.md",
                  "reviews/all_philadelphia-positive/projekt.sqlite",
                  "reviews/x/05_forrasok/smith_phillips_2020.pdf"):
            self.assertFalse(self.ignored(p), p)

    def test_AG_13_phi_marked_files_are_ignored(self):
        for p in ("reviews/x/03_adatok/betegek_PHI.csv", "reviews/x/03_adatok/lista_phi.csv",
                  "reviews/x/03_adatok/PHI_lista.csv", "reviews/x/03_adatok/phi_lista.csv",
                  "reviews/x/03_adatok/kohorsz.phi.csv", "reviews/x/03_adatok/beteg_adat.xlsx",
                  "reviews/x/03_adatok/kohorsz.PHI.csv", "reviews/x/03_adatok/sepsis_PHI_v2.csv",
                  "reviews/x/03_adatok/t_phi-v2.csv", "reviews/x/03_adatok/beteg_adat_2024.csv",
                  "reviews/x/03_adatok/x_PHI/adat.csv"):
            self.assertTrue(self.ignored(p), p)

    def test_AG_13_docs_list_the_actual_patterns(self):
        gi = _read(os.path.join(REPO, ".gitignore"))
        pats = [ln.strip() for ln in gi.splitlines()
                if ln.strip() and not ln.startswith("#") and re.search(r"(?i)phi|\[Pp\]\[Hh\]\[Ii\]|beteg_adat", ln)]
        self.assertGreaterEqual(len(pats), 5)
        self.assertNotIn("*phi*", pats)
        self.assertNotIn("*_phi*", pats)          # agents_e2e:AG-13 maradék: prefix-egyezés szó belsejében
        docs = {"PREFLIGHT-31": item("PREFLIGHT-31")["text"], "D-S00-013": item("D-S00-013")["recommendation"],
                "ESZKOZOK": _read(ESZKOZOK), "SKILL": _read(SKILL)}
        for name, t in docs.items():
            for p in pats:
                self.assertIn("`%s`" % p, t, "%s: %s" % (name, p))
        self.assertIn("protokoll.md", item("PREFLIGHT-31")["how_to_verify"])


# ======================================================== tau2_method állítás
class TestTau2MethodClaim(unittest.TestCase):
    """agents_e2e:AG-15 (3)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_AG_15_skill_claim_matches_results_json(self):
        skill = _read(SKILL)
        self.assertNotIn("minden blokkjában a ténylegesen használt", skill)
        out = os.path.join(self.tmp, "o")
        code, so, se = run_cli("analyze", "--data", os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv"),
                               "--measure", "RR", "--tau2", "PM", "--out", out)
        self.assertEqual(code, 0, se)
        res = json.loads(_read(os.path.join(out, "results.json")))
        for block in ("random", "primary"):
            self.assertEqual(res[block]["tau2_method"], "PM", block)
        self.assertEqual(res["bias"]["trimfill"]["adjusted"]["tau2_method"], "PM")
        # a leave-one-out ugyanazzal a becslővel fut (metafor PM, Aronson nélkül: τ² = 0.35576)
        with open(os.path.join(out, "effect_sizes.csv"), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        loo = res["sensitivity"]["leave_one_out"][0]
        self.assertEqual(loo["omitted"], rows[0]["study"])
        ref = models.meta_analysis([float(r["yi"]) for r in rows[1:]], [float(r["vi"]) for r in rows[1:]],
                                   "random", "PM")
        assert_close(self, loo["tau2"], ref.tau2, 1e-10)


if __name__ == "__main__":
    unittest.main()
