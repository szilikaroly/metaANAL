# -*- coding: utf-8 -*-
"""Regressziós tesztek a D-köteg (tudásbázis és projektnapló) javításaihoz.

Minden teszt egy review-megállapításra hivatkozik (az azonosító a teszt docstringjében).
Minden tudásbázis ideiglenes fájlban épül (a repó tudasbazis.sqlite-ját a tesztek nem írják,
kivéve a projektnapló alapértelmezett --kb-ellenőrzését, amely csak olvas).
"""
import glob
import io
import json
import os
import pathlib
import re
import shutil
import sqlite3
import tempfile
import time
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _helpers import ROOT
from metaelemzes import cli, kb, projekt, validate

REPO = os.path.dirname(ROOT)


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:      # argparse-hiba
            code = exc.code
    return code, buf.getvalue(), err.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(path, body_xml):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", "<w:document %s><w:body>%s</w:body></w:document>" % (W, body_xml))
    return path


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "kb.sqlite")

    def write(self, rel, text, enc="utf-8"):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding=enc) as fh:
            fh.write(text)
        return p

    def write_bytes(self, rel, data):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def chunk_hits(self, q, db=None):
        return kb.search(q, db=db or self.db, scopes=("chunk",)).get("chunk", [])

    def fulltext(self, db=None):
        return kb.stats(db or self.db)["fulltext_by_source"]


class _SeedTmp(_Tmp):
    """Saját seed-mappa és séma-másolat (a repó seed-jei nem változnak)."""

    def setUp(self):
        super().setUp()
        self.seed = os.path.join(self.tmp, "seed")
        # csak a minimális alap (források, szakaszok) — a tesztek így függetlenek a repó
        # mindenkori tudás-, szabály- és ellenőrzőlista-seedjeitől
        os.makedirs(self.seed)
        for f in glob.glob(os.path.join(kb.SEED_DIR, "sources*.json")) + \
                glob.glob(os.path.join(kb.SEED_DIR, "stages*.json")):
            shutil.copy(f, self.seed)
        self.schema = os.path.join(self.tmp, "schema.sql")
        shutil.copyfile(kb.SCHEMA, self.schema)
        for target, value in (("SEED_DIR", self.seed), ("SCHEMA", self.schema)):
            p = mock.patch.object(kb, target, value)
            p.start()
            self.addCleanup(p.stop)

    def seed_file(self, name, items):
        with open(os.path.join(self.seed, name), "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False)


# ================================================================ projektnapló: kapu
class TestCheckpointGate(_Tmp):
    """checkpoint-gate-bypass (major + minor), gate-enforcement-bypassable"""

    def setUp(self):
        super().setUp()
        self.p = os.path.join(self.tmp, "p")
        projekt.init(self.p, "T")
        kb.build(self.db)

    def blocker(self, stage, title="x"):
        return projekt.add_finding(self.p, "reviewer", "blocker", title, stage=stage, kb_db=self.db)

    def test_variant_labels_cannot_bypass_open_blocker(self):
        self.blocker("S02", "PICO hiányos")
        for st in ("S02", "s02", " S02", "S2", "S01–S02", "S01-S02", "S01—S02", "FINAL", "final"):
            with self.assertRaises(ValueError, msg=st):
                projekt.checkpoint(self.p, st, "reviewer", "PASS")
            with self.assertRaises(ValueError, msg=st):
                projekt.checkpoint(self.p, st, "reviewer", "PASS_WITH_FIXES")
        # más szakasz PASS-a megengedett (szakaszonkénti kapu), FAIL mindig rögzíthető
        projekt.checkpoint(self.p, "S03", "reviewer", "PASS")
        projekt.checkpoint(self.p, "FINAL", "reviewer", "FAIL")
        cps = projekt.list_items(self.p, "checkpoints")
        self.assertEqual([(c["stage_id"], c["verdict"]) for c in cps], [("S03", "PASS"), ("FINAL", "FAIL")])

    def test_final_requires_no_open_blocker_anywhere(self):
        fid = self.blocker("S05", "kitalált adat")
        projekt.checkpoint(self.p, "S14", "reviewer", "PASS")          # más szakasz: megengedett
        with self.assertRaisesRegex(ValueError, "FINAL"):
            projekt.checkpoint(self.p, "FINAL", "reviewer", "PASS")
        projekt.resolve_finding(self.p, fid, "fixed", "javítva")
        projekt.checkpoint(self.p, "FINAL", "reviewer", "PASS")

    def test_range_blocker_blocks_each_covered_stage(self):
        """A dokumentáció 'S07–S12' tartományára rögzített blocker minden lefedett szakaszt blokkol."""
        self.blocker("S07–S12", "rossz modell")
        f = projekt.get_item(self.p, "finding", 1)
        self.assertEqual(f["stage_id"], "S07,S08,S09,S10,S11,S12")
        for st in ("S08", "S12", "S07-S08"):
            with self.assertRaises(ValueError):
                projekt.checkpoint(self.p, st, "reviewer", "PASS")
        projekt.checkpoint(self.p, "S13", "reviewer", "PASS")

    def test_range_checkpoint_expands_to_one_row_per_stage(self):
        projekt.checkpoint(self.p, "S01–S02", "reviewer", "PASS", "protokoll rendben")
        st = projekt.status(self.p)
        self.assertEqual([c["stage_id"] for c in st["checkpoints"]], ["S01", "S02"])

    def test_invalid_stage_ids_rejected_everywhere(self):
        for bad in ("S15", "S1x", "X01", "S05-S03", "S01-S02-S03", "előkészítés"):
            with self.assertRaises(ValueError, msg=bad):
                projekt.checkpoint(self.p, bad, "reviewer", "FAIL")
            with self.assertRaises(ValueError, msg=bad):
                projekt.add_finding(self.p, "reviewer", "minor", "t", stage=bad, check_kb=False)
            with self.assertRaises(ValueError, msg=bad):
                projekt.log_decision(self.p, "planner", "d", stage=bad, check_kb=False)
        self.assertEqual(projekt.parse_stage("s5"), ["S05"])
        self.assertEqual(projekt.parse_stage("S01,S03"), ["S01", "S03"])
        self.assertEqual(projekt.parse_stage(None), [])

    def test_legacy_invalid_stage_label_blocks_conservatively(self):
        """Régi (javítás előtti) naplóban maradt érvénytelen címke: mindenhol blokkol, a status jelzi."""
        con = sqlite3.connect(projekt.db_path(self.p))
        con.execute("INSERT INTO finding (ts, agent, stage_id, severity, title) VALUES ('t','reviewer','S5 elemzés','blocker','x')")
        con.execute("INSERT INTO checkpoint (ts, stage_id, agent, verdict) VALUES ('t','S01–S02','reviewer','PASS')")
        con.execute("INSERT INTO checkpoint (ts, stage_id, agent, verdict) VALUES ('t','protokoll','reviewer','PASS')")
        con.commit()
        con.close()
        with self.assertRaises(ValueError):
            projekt.checkpoint(self.p, "S09", "reviewer", "PASS")
        warns = "\n".join(projekt.status(self.p)["warnings"])
        self.assertIn("finding #1: 'S5 elemzés'", warns)
        self.assertIn("checkpoint #2: 'protokoll'", warns)
        # a régi, kapu-megkerüléssel rögzített PASS láthatóvá válik
        self.assertIn("S01–S02: az utolsó ellenőrzőpont PASS, de nyitott blocker van: #1", warns)

    def test_cli_rejects_and_reports(self):
        run_cli("project", "finding", self.p, "--agent", "reviewer", "--severity", "blocker", "--stage", "S02",
                "--title", "PICO hiányos", "--kb-db", self.db)
        for st in ("S02", "S01–S02", "s02", "FINAL"):
            code, so, se = run_cli("project", "checkpoint", self.p, "--stage", st, "--agent", "reviewer", "--verdict", "PASS")
            self.assertEqual(code, 1, st)
            self.assertIn("nyitott 'blocker'", se)
        code, so, se = run_cli("project", "checkpoint", self.p, "--stage", "S03-S04", "--agent", "reviewer", "--verdict", "PASS")
        self.assertEqual(code, 0, se)
        self.assertIn("S03, S04", so)

    def test_unknown_agent_recorded_as_given_but_warned(self):
        warns = []
        projekt.log_decision(self.p, "planer", "REML", stage="S08", kb_db=self.db, warnings=warns)
        self.assertTrue(any("planer" in w for w in warns))
        self.assertEqual(projekt.get_item(self.p, "decision", 1)["agent"], "planer")
        st = projekt.status(self.p)
        self.assertTrue(any("Ismeretlen ágensnév" in w and "planer" in w for w in st["warnings"]))
        code, so, se = run_cli("project", "status", self.p)
        self.assertEqual(code, 0)
        self.assertIn("planer", se)


# ================================================================ projektnapló: KB-hivatkozások
class TestKbRefsValidated(_Tmp):
    """kb-refs-not-validated-fabrication"""

    def setUp(self):
        super().setUp()
        self.p = os.path.join(self.tmp, "p")
        projekt.init(self.p, "T")
        kb.build(self.db)

    def test_unknown_ids_warned_and_flagged(self):
        warns = []
        did = projekt.log_decision(self.p, "planner", "REML", stage="S08", kb_refs="D-SYN-003, V015|D-FAKE-999",
                                   kb_db=self.db, warnings=warns)
        d = projekt.get_item(self.p, "decision", did)
        self.assertEqual(d["kb_refs"], "D-SYN-003,V015,D-FAKE-999")
        self.assertEqual(d["kb_unverified"], "D-SYN-003,D-FAKE-999")
        self.assertTrue(any("D-SYN-003" in w and "D-FAKE-999" in w for w in warns))
        md = projekt.export_markdown(self.p)
        self.assertIn("ellenőrizetlen: D-SYN-003,D-FAKE-999", md)
        self.assertTrue(any("D-FAKE-999" in w for w in projekt.status(self.p)["warnings"]))

    def test_known_ids_pass_silently(self):
        warns = []
        fid = projekt.add_finding(self.p, "reviewer", "major", "SE/SD", stage="S05", kb_refs="V011,S05,khan2020",
                                  kb_db=self.db, warnings=warns)
        gid = projekt.add_grade(self.p, "HbA1c", "moderate", kb_db=self.db, warnings=warns, kb_refs="V015", k=3)
        self.assertEqual(warns, [])
        self.assertIsNone(projekt.get_item(self.p, "finding", fid)["kb_unverified"])
        self.assertIsNone(projekt.get_item(self.p, "grade", gid)["kb_unverified"])

    def test_strict_rejects(self):
        with self.assertRaisesRegex(ValueError, "G-FAKE-7"):
            projekt.add_grade(self.p, "HbA1c", "low", kb_db=self.db, strict=True, kb_refs="G-FAKE-7")
        self.assertEqual(projekt.list_items(self.p, "grades"), [])
        code, so, se = run_cli("project", "log", self.p, "--agent", "planner", "--stage", "S08", "--decision", "REML",
                               "--kb", "D-SYN-003", "--strict", "--kb-db", self.db)
        self.assertEqual(code, 1)
        self.assertIn("D-SYN-003", se)
        code, so, se = run_cli("project", "finding", self.p, "--agent", "reviewer", "--severity", "minor", "--title", "t",
                               "--kb", "K-FAKE-1", "--kb-db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("FIGYELEM", se)
        self.assertIn("K-FAKE-1", se)

    def test_kb_unavailable_does_not_break_logging(self):
        warns = []
        bad_db = os.path.join(self.tmp, "nem_kb.sqlite")
        con = sqlite3.connect(bad_db)
        con.execute("CREATE TABLE idegen (x)")
        con.commit()
        con.close()
        did = projekt.log_decision(self.p, "planner", "REML", kb_refs="V015", kb_db=bad_db, warnings=warns)
        self.assertEqual(projekt.get_item(self.p, "decision", did)["kb_unverified"], "V015")
        self.assertTrue(any("nem ellenőrizhetők" in w for w in warns))

    def test_old_project_log_is_upgraded(self):
        """Régi projektnapló (kb_unverified oszlop nélkül) továbbra is használható."""
        old = os.path.join(self.tmp, "old")
        os.makedirs(old)
        con = sqlite3.connect(projekt.db_path(old))
        con.executescript(projekt.SCHEMA.replace(", kb_unverified TEXT", ""))
        self.assertNotIn("kb_unverified", [r[1] for r in con.execute("PRAGMA table_info(decision)")])
        con.close()
        projekt.log_decision(old, "planner", "x", kb_refs="V015", kb_db=self.db)
        self.assertIn("kb_unverified", projekt.get_item(old, "decision", 1))


# ================================================================ projektnapló: show/list, export
class TestFindingVisibility(_Tmp):
    """finding-resolution-loop-unspecified (kódoldal: show/list, export oszlopai)"""

    def setUp(self):
        super().setUp()
        self.p = os.path.join(self.tmp, "p")
        projekt.init(self.p, "T")
        kb.build(self.db)
        projekt.add_finding(self.p, "reviewer", "blocker", "PICO hiányos", detail="részletek", stage="S02",
                            evidence="protokoll.md:12", kb_db=self.db)
        projekt.add_finding(self.p, "reviewer", "major", "Egger k<10", detail="d2", stage="S10",
                            evidence="eredmeny.json", kb_db=self.db)
        projekt.resolve_finding(self.p, 1, "invalid", "nem releváns")

    def test_show_and_list(self):
        code, so, se = run_cli("project", "show", self.p, "finding", "1")
        self.assertEqual(code, 0, se)
        f = json.loads(so)
        self.assertEqual((f["detail"], f["evidence"], f["status"], f["resolution"]),
                         ("részletek", "protokoll.md:12", "invalid", "nem releváns"))
        code, so, se = run_cli("project", "list", self.p, "findings", "--status", "open")
        self.assertEqual([x["id"] for x in json.loads(so)], [2])
        code, so, se = run_cli("project", "list", self.p, "findings", "--status", "resolved")
        self.assertEqual([x["id"] for x in json.loads(so)], [1])
        self.assertEqual(json.loads(so)[0]["evidence"], "protokoll.md:12")
        code, so, se = run_cli("project", "list", self.p, "findings")
        self.assertEqual(len(json.loads(so)), 2)
        code, so, se = run_cli("project", "show", self.p, "finding", "99")
        self.assertEqual(code, 1)
        self.assertEqual(projekt.status(self.p)["findings_by_status"], {"invalid": 1, "open": 1})

    def test_export_has_detail_evidence_and_grade_domains(self):
        projekt.add_grade(self.p, "HbA1c", "low", kb_db=self.db, k=5, participants=420, risk_of_bias="-1",
                          imprecision="-1", effect="MD -0.4")
        md = projekt.export_markdown(self.p)
        self.assertIn("| Részletek | Bizonyíték | KB |", md)
        self.assertIn("protokoll.md:12", md)
        self.assertIn("nem releváns", md)
        self.assertIn("| HbA1c | 5 | 420 | MD -0.4 | -1 |", md)


class TestExportEscaping(_Tmp):
    """export-unescaped-cells"""

    @staticmethod
    def cells(line):
        return len(re.split(r"(?<!\\)\|", line.strip())) - 2

    def test_cells_escaped_single_line_and_k0(self):
        p = os.path.join(self.tmp, "p")
        projekt.init(p, "SR\n## injected", "kérdés\r\nmásodik | sor")
        projekt.log_decision(p, "planner|x", "REML\r\nHKSJ", stage="S08", kb_refs="D-SYN-001|V015", check_kb=False)
        projekt.add_grade(p, "HbA1c", "moderate", k=0, check_kb=False)
        projekt.add_finding(p, "reviewer", "minor", "cím | cső", detail="a\rb", check_kb=False)
        projekt.log_run(p, "analyze data.csv --label 'a`b' --out out|dir", None, "out|dir", "1.0")
        md = projekt.export_markdown(p)
        self.assertNotIn("\r", md)
        lines = md.split("\n")
        self.assertTrue(lines[0].startswith("# ") and "## injected" in lines[0])
        self.assertFalse(any(l.startswith("## injected") for l in lines))
        # minden táblázatsor cellaszáma egyezik a fejlécével
        header = None
        for l in lines:
            if l.startswith("|") and not l.startswith("|---"):
                if header is None:
                    header = self.cells(l)
                else:
                    self.assertEqual(self.cells(l), header, l)
            elif not l.startswith("|"):
                header = None
        self.assertIn("| HbA1c | 0 |", md)
        self.assertIn("``analyze data.csv --label 'a`b' --out out\\|dir``", md)
        self.assertNotIn("| `` |", md)      # üres hash: nincs üres kódspan


# ================================================================ tudásbázis: csak-olvasó URI, --db
class TestReadonlyUri(_Tmp):
    """kb-ro-uri-unescaped, kb-readonly-uri-special-chars"""

    def test_special_characters_in_path(self):
        for d in ("kb#1", "kb%41b", "q?x", "sp ace", "árvíztűrő"):
            if os.name == "nt" and "?" in d:
                continue
            db = os.path.join(self.tmp, d, "kb.sqlite")
            os.makedirs(os.path.dirname(db))
            self.assertGreaterEqual(kb.stats(db)["stage"], 15, d)
            self.assertFalse(kb.ensure_built(db), d)           # nincs folytonos újraépítés
            con = kb.connect(db, readonly=True)
            with self.assertRaises(sqlite3.OperationalError):  # valóban csak olvasható
                con.execute("CREATE TABLE zz (x)")
            con.close()
        # nem keletkezik kósza fájl a csonkolt útvonalakon
        self.assertEqual(sorted(n for n in os.listdir(self.tmp) if os.path.isfile(os.path.join(self.tmp, n))), [])

    def test_uri_forms(self):
        self.assertTrue(kb._readonly_uri("/x/kb#1/a%41?.sqlite").startswith("file:///x/kb%231/a%2541%3F.sqlite?mode=ro"))
        win = pathlib.PureWindowsPath
        self.assertEqual(kb._readonly_uri(r"C:\SR #2\kb.sqlite", path_cls=win), "file:///C:/SR%20%232/kb.sqlite?mode=ro")
        self.assertEqual(kb._readonly_uri(r"\\server\share\kb.sqlite", path_cls=win), "file:////server/share/kb.sqlite?mode=ro")

    def test_bare_relative_db_name(self):
        """kb-db-bare-filename-crash"""
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            code, so, se = run_cli("kb", "--db", "kb.sqlite", "build")
            self.assertEqual(code, 0, se)
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "kb.sqlite")))
            code, so, se = run_cli("kb", "--db", "kb2.sqlite", "stats")
            self.assertEqual(code, 0, se)
            self.assertEqual(json.loads(so)["stage"], 15)
        finally:
            os.chdir(cwd)


# ================================================================ tudásbázis: build
class TestBuild(_SeedTmp):
    def test_fingerprint_includes_engine_rules(self):
        """fingerprint-ignores-engine-rules"""
        kb.build(self.db)
        self.assertFalse(kb.ensure_built(self.db))
        with mock.patch.dict(validate.RULES, {"V999": ("error", "Új teszt-szabály", "tanács", "engine")}):
            self.assertTrue(kb.ensure_built(self.db))
            self.assertEqual(kb.show("V999", db=self.db)["condition"], "Új teszt-szabály")
        self.assertTrue(kb.ensure_built(self.db))
        self.assertIsNone(kb.show("V999", db=self.db))

    def test_duplicate_ids_across_seed_files_fail(self):
        """build-duplicate-ids-misleading-counts"""
        self.seed_file("rules_a.json", [{"rule_id": "D-SYN-001", "stage_id": "S08", "applies_to": "planner",
                                         "condition": "k<5", "recommendation": "HKSJ", "strength": "should"}])
        self.seed_file("rules_b.json", [{"rule_id": "D-SYN-001", "stage_id": "S09", "applies_to": "reviewer",
                                         "condition": "I2>75", "recommendation": "explore", "strength": "must"}])
        with self.assertRaisesRegex(kb.KBError, r"Duplikált.*D-SYN-001.*rules_a\.json.*rules_b\.json"):
            kb.build(self.db)
        code, so, se = run_cli("kb", "--db", self.db, "rules", "--stage", "S08")
        self.assertEqual(code, 1)
        self.assertIn("D-SYN-001", se)

    def test_engine_rule_clash_and_fk_problems_fail(self):
        self.seed_file("rules_a.json", [{"rule_id": "V011", "stage_id": "S08", "applies_to": "planner",
                                         "condition": "c", "recommendation": "r", "strength": "should"}])
        with self.assertRaisesRegex(kb.KBError, "V011"):
            kb.build(self.db)
        self.seed_file("rules_a.json", [{"rule_id": "D-PUB-001", "stage_id": "S99", "applies_to": "reviewer",
                                         "condition": "k<10", "recommendation": "no Egger", "strength": "avoid"}])
        with self.assertRaisesRegex(kb.KBError, r"D-PUB-001.*S99"):
            kb.build(self.db)

    def test_true_counts(self):
        self.seed_file("rules_a.json", [{"rule_id": "D-SYN-001", "stage_id": "S08", "applies_to": "planner",
                                         "condition": "k<5", "recommendation": "HKSJ", "strength": "should"}])
        counts = kb.build(self.db)
        self.assertEqual(counts["decision_rule"], 1 + len(kb._engine_rules()))
        self.assertEqual(counts["engine_rules"], len(kb._engine_rules()))
        self.assertTrue(set(validate.RULES) <= set(kb._engine_rules()))
        self.assertEqual(counts["decision_rule"], kb.stats(self.db)["decision_rule"])

    def test_failed_build_leaves_db_unchanged(self):
        kb.build(self.db)
        txt = self.write("jegyzet.txt", "heterogenitás jegyzet")
        kb.ingest(txt, db=self.db)
        self.seed_file("rules_a.json", [{"rule_id": "X", "stage_id": "S99", "applies_to": "all",
                                         "condition": "c", "recommendation": "r", "strength": "must"}])
        with self.assertRaises(kb.KBError):
            kb.build(self.db)
        os.remove(os.path.join(self.seed, "rules_a.json"))
        self.assertEqual(len(self.chunk_hits("heterogenitás")), 1)

    def test_keep_fulltext_false_leaves_no_orphans(self):
        """keep-fulltext-false-orphans"""
        kb.build(self.db)
        kb.ingest(self.write("jegyzet.txt", "heterogenitas jegyzet"), db=self.db)
        res = kb.build(self.db, keep_fulltext=False)
        self.assertEqual(res["foreign_key_problems"], 0)
        self.assertEqual(kb.stats(self.db)["chunk"], 0)
        self.assertEqual(self.chunk_hits("heterogenitas"), [])
        self.assertIsNone(kb.show("jegyzet", db=self.db))

    def test_removed_seed_source_disappears(self):
        with open(os.path.join(self.seed, "sources.json"), encoding="utf-8") as fh:
            src = json.load(fh)
        self.seed_file("sources.json", src + [{"source_id": "zz_tmp", "citation": "Tmp", "kind": "article"}])
        kb.build(self.db)
        self.assertIsNotNone(kb.show("zz_tmp", db=self.db))
        self.seed_file("sources.json", src)
        kb.build(self.db)
        self.assertIsNone(kb.show("zz_tmp", db=self.db))

    def test_refuses_to_overwrite_non_kb_database(self):
        """(finding-resolution-loop mellékmegfigyelés) a kb --db <projekt.sqlite> nem írhatja felül a naplót."""
        p = os.path.join(self.tmp, "p")
        projekt.init(p, "T")
        before = sorted(r[0] for r in sqlite3.connect(projekt.db_path(p)).execute("SELECT name FROM sqlite_master"))
        code, so, se = run_cli("kb", "--db", projekt.db_path(p), "sql", "SELECT 1")
        self.assertEqual(code, 1)
        self.assertIn("nem tudásbázis", se)
        after = sorted(r[0] for r in sqlite3.connect(projekt.db_path(p)).execute("SELECT name FROM sqlite_master"))
        self.assertEqual(before, after)


class TestSchemaMigration(_SeedTmp):
    """schema-change-not-migrated"""

    def setUp(self):
        super().setUp()
        kb.build(self.db)
        kb.ingest(self.write("jegyzet.txt", "Heterogenitás és predikciós intervallum."), db=self.db,
                  citation="Saját jegyzet 2026")

    def edit_schema(self, old, new):
        with open(self.schema, encoding="utf-8") as fh:
            s = fh.read()
        self.assertIn(old, s)
        with open(self.schema, "w", encoding="utf-8") as fh:
            fh.write(s.replace(old, new))

    def test_check_constraint_change_rebuilds_and_keeps_fulltext(self):
        self.edit_schema("'definition','check','criterion')", "'definition','check','criterion','table')")
        self.seed_file("knowledge_x.json", [{"k_id": "K-X-001", "stage_id": "S08", "kind": "table", "title": "t",
                                             "body": "tau táblázat", "source_id": "khan2020"}])
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertTrue(kb.ensure_built(self.db))
        self.assertIn("Sémaváltás", err.getvalue())
        self.assertEqual(kb.show("K-X-001", db=self.db)["kind"], "table")
        self.assertEqual(len(self.chunk_hits("predikciós")), 1)
        self.assertEqual(kb.show("jegyzet", db=self.db)["citation"], "Saját jegyzet 2026")
        self.assertFalse(kb.ensure_built(self.db))
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertTrue(con.execute("SELECT 1 FROM sqlite_master WHERE name='idx_chunk_source'").fetchone())

    def test_new_column_added(self):
        self.edit_schema("    tags      TEXT\n);", "    tags      TEXT,\n    evidence_level TEXT\n);")
        res = kb.build(self.db)
        cols = [r[1] for r in sqlite3.connect(self.db).execute("PRAGMA table_info(knowledge)")]
        self.assertIn("evidence_level", cols)
        self.assertEqual(len(self.chunk_hits("predikciós")), 1)
        self.assertIn("figyelmeztetesek", res)

    def test_incompatible_change_instructs_reingest(self):
        self.edit_schema("    text      TEXT NOT NULL\n);", "    text      TEXT NOT NULL,\n    page INTEGER NOT NULL\n);")
        res = kb.build(self.db)
        self.assertTrue(any("kb ingest" in n for n in res["figyelmeztetesek"]))
        self.assertEqual(kb.stats(self.db)["chunk"], 0)
        self.assertEqual(res["foreign_key_problems"], 0)


# ================================================================ tudásbázis: ingest
class TestIngestAttribution(_Tmp):
    """ingest-hint-substring-misattribution, ingest-same-sid-overwrite"""

    def setUp(self):
        super().setUp()
        kb.build(self.db)
        # a seed-forrásoknak (khan2020, cheung2016) már van teljes szövege
        kb.ingest(self.write("konyv/f4c4c879-978-981-15-5032-4.txt", "Khan book full text about meta analysis."),
                  db=self.db)
        kb.ingest(self.write("konyv/209f8d7d-57cc7782-guide.txt", "Cheung guide full text."), db=self.db)

    def test_seed_hints_assigned_on_token_boundary(self):
        ft = self.fulltext()
        self.assertEqual(ft.get("khan2020"), 1)
        self.assertEqual(ft.get("cheung2016"), 1)
        self.assertTrue(kb._hint_matches("978-981-15-5032-4", "f4c4c879-978-981-15-5032-4.docx"))
        self.assertTrue(kb._hint_matches("cheung", "cheung_2016.pdf"))
        self.assertFalse(kb._hint_matches("khan", "khan_2020.pdf"))          # túl rövid, nem specifikus
        self.assertFalse(kb._hint_matches("cheung", "cheunger2016.pdf"))     # nincs token-határ
        self.assertTrue(kb._hint_matches("_khan_", "x_khan_2020.pdf"))       # explicit elválasztó

    def test_unrelated_user_files_never_overwrite_seed_fulltext(self):
        out = io.StringIO()
        code, so, se = run_cli("kb", "--db", self.db, "ingest", self.write("Khanna_2019_statins.txt", "Statin trials pooled with rosuvastatin."),
                               "--citation", "Khanna 2019")
        self.assertEqual(code, 0, se)
        res = kb.ingest(self.write("Cheung_2014_metaSEM.txt", "metaSEM structural equation."), db=self.db)
        ft = self.fulltext()
        self.assertEqual(ft["khan2020"], 1)
        self.assertEqual(ft["cheung2016"], 1)
        self.assertEqual(kb.show("khanna_2019_statins", db=self.db)["citation"], "Khanna 2019")
        self.assertEqual(res[0][0], "cheung_2014_metasem")
        self.assertIn("cheung2016", res[0][2])                    # a figyelmeztetés megnevezi
        hit = self.chunk_hits("rosuvastatin")
        self.assertEqual([h["source_id"] for h in hit], ["khanna_2019_statins"])

    def test_reingest_same_file_replaces(self):
        p = os.path.join(self.tmp, "konyv", "f4c4c879-978-981-15-5032-4.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("Khan book full text, second edition.")
        res = kb.ingest(p, db=self.db)
        self.assertEqual(res[0][0], "khan2020")
        self.assertEqual(self.fulltext()["khan2020"], 1)
        self.assertEqual(len(self.chunk_hits("edition")), 1)

    def test_directory_with_explicit_source_id_keeps_every_file(self):
        for n, t in (("a.txt", "Alpha text"), ("b.txt", "Bravo text"), ("c.md", "Charlie text")):
            self.write("src/" + n, t)
        kb.ingest(os.path.join(self.tmp, "src"), source_id="sajat", db=self.db)
        for q in ("Alpha", "Bravo", "Charlie"):
            self.assertEqual([h["source_id"] for h in self.chunk_hits(q)], ["sajat"], q)
        seqs = [r[0] for r in kb.query("SELECT seq FROM chunk WHERE source_id='sajat' ORDER BY seq", db=self.db)[1]]
        self.assertEqual(seqs, [0, 1, 2])
        # újrafuttatás: idempotens (nem duplikál, nem veszít)
        kb.ingest(os.path.join(self.tmp, "src"), source_id="sajat", db=self.db)
        self.assertEqual(self.fulltext()["sajat"], 3)

    def test_colliding_auto_ids_get_own_sources(self):
        d = "col/"
        self.write(d + "Cochrane_Handbook_for_Systematic_Reviews_chapter_10.txt", "Delta")
        self.write(d + "Cochrane_Handbook_for_Systematic_Reviews_chapter_11.txt", "Echo")
        self.write(d + "Μεταανάλυση.txt", "Foxtrot")
        self.write(d + "荟萃分析.txt", "Golf")
        res = kb.ingest(os.path.join(self.tmp, "col"), db=self.db)
        self.assertEqual(len({r[0] for r in res}), 4)
        for q in ("Delta", "Echo", "Foxtrot", "Golf"):
            self.assertEqual(len(self.chunk_hits(q)), 1, q)
        before = self.fulltext()
        kb.ingest(os.path.join(self.tmp, "col"), db=self.db)       # újrafuttatás
        self.assertEqual(self.fulltext(), before)

    def test_renamed_copy_not_duplicated_across_runs(self):
        a = self.write("x/jegyzet.txt", "Hotel unique content")
        kb.ingest(a, db=self.db)
        b = os.path.join(self.tmp, "x", "renamed_copy.txt")
        shutil.copyfile(a, b)
        res = kb.ingest(b, db=self.db)
        self.assertEqual(res[0][0], "jegyzet")
        self.assertEqual(len(self.chunk_hits("Hotel")), 1)

    def test_legacy_fulltext_without_record(self):
        """Régi adatbázis (nincs ingest_file sor): ugyanaz a dokumentum felismerve, más nem írja felül."""
        con = sqlite3.connect(self.db)
        con.execute("DELETE FROM ingest_file")
        con.commit()
        con.close()
        res = kb.ingest(os.path.join(self.tmp, "konyv", "f4c4c879-978-981-15-5032-4.txt"), db=self.db)
        self.assertEqual(res[0][0], "khan2020")
        res = kb.ingest(self.write("other/cheung_notes.txt", "Completely different words here."), db=self.db)
        self.assertNotEqual(res[0][0], "cheung2016")
        self.assertEqual(len(self.chunk_hits("guide")), 1)


class TestIngestRobustness(_Tmp):
    """ingest-one-bad-pdf-aborts-all, ingest-binary-and-uppercase-ext"""

    def setUp(self):
        super().setUp()
        kb.build(self.db)

    def test_bad_file_does_not_abort_others(self):
        self.write("src/notes.txt", "notes about kappa")
        self.write_bytes("src/zz_broken.pdf", b"%PDF-1.4\ngarbage")
        self.write_bytes("src/rossz.docx", b"PK nem zip")
        report = []
        res = kb.ingest(os.path.join(self.tmp, "src"), db=self.db, report=report)
        self.assertEqual([r["status"] for r in report if r["file"].endswith("notes.txt")], ["ok"])
        errs = [r for r in report if r["status"] == "error"]
        self.assertEqual(len(errs), 2)
        self.assertTrue(any("zz_broken.pdf" in r["message"] for r in errs))
        if shutil.which("pdftotext"):
            self.assertNotIn("telepítsd", [r for r in errs if "zz_broken" in r["file"]][0]["message"])
        self.assertEqual(len(self.chunk_hits("kappa")), 1)              # a jó fájl véglegesítve
        code, so, se = run_cli("kb", "--db", self.db, "ingest", os.path.join(self.tmp, "src"))
        self.assertEqual(code, 1)
        self.assertIn("zz_broken.pdf", se)
        self.assertIn("notes", so)

    def test_uppercase_extension_and_binary_refused(self):
        self.write("d/UPPER.TXT", "uppercase extension content")
        self.write("d/Konyv.MD", "markdown upper")
        self.write_bytes("d/regi.doc", b"\xd0\xcf\x11\xe0" + bytes(range(256)) * 4)
        self.write_bytes("d/tabla.xlsx", b"PK\x03\x04" + bytes(range(256)))
        report = []
        kb.ingest(os.path.join(self.tmp, "d"), db=self.db, report=report)
        st = {os.path.basename(r["file"]): r["status"] for r in report}
        self.assertEqual(st, {"UPPER.TXT": "ok", "Konyv.MD": "ok", "regi.doc": "skipped", "tabla.xlsx": "skipped"})
        with self.assertRaisesRegex(kb.KBError, "Nem támogatott"):
            kb.ingest(os.path.join(self.tmp, "d", "regi.doc"), db=self.db)
        code, so, se = run_cli("kb", "--db", self.db, "ingest", os.path.join(self.tmp, "d", "tabla.xlsx"))
        self.assertEqual(code, 1)
        self.assertNotIn("PK", "".join(r[0] for r in kb.query("SELECT text FROM chunk", db=self.db)[1]))

    def test_binary_disguised_as_txt_does_not_wipe_existing(self):
        p = self.write("lower.txt", "real content")
        kb.ingest(p, source_id="lower", db=self.db)
        self.write_bytes("fake.txt", b"\x00\x01\x02binary")
        res = kb.ingest(os.path.join(self.tmp, "fake.txt"), source_id="lower", db=self.db)
        self.assertIsNone(res[0][0])
        self.assertIn("bináris", res[0][2])
        self.assertEqual(len(self.chunk_hits("real")), 1)

    def test_text_encodings(self):
        self.write("cp.txt", "árvíztűrő tükörfúrógép", enc="cp1250")
        self.write("u16.txt", "kappa-utf16", enc="utf-16")
        kb.ingest(os.path.join(self.tmp, "cp.txt"), db=self.db)
        kb.ingest(os.path.join(self.tmp, "u16.txt"), db=self.db)
        self.assertEqual(len(self.chunk_hits("tükörfúrógép")), 1)
        self.assertEqual(len(self.chunk_hits("utf16")), 1)


class TestDocxExtraction(_Tmp):
    """docx-tab-br-table-runs"""

    def test_runs_tabs_breaks_tables_sdt(self):
        p = make_docx(os.path.join(self.tmp, "r.docx"),
                      '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Hed</w:t></w:r><w:r><w:t>ges</w:t></w:r></w:p></w:tc>'
                      '<w:tc><w:p><w:r><w:t>−</w:t></w:r><w:r><w:t>0.3116</w:t></w:r></w:p>'
                      '<w:p><w:r><w:t>(SE)</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                      '<w:sdt><w:sdtPr><w:alias w:val="TOC"/></w:sdtPr><w:sdtContent><w:p><w:r><w:t>Zygomaticword</w:t></w:r></w:p>'
                      '</w:sdtContent></w:sdt>'
                      '<w:p><w:r><w:t>Heterogeneity</w:t></w:r><w:r><w:br/></w:r><w:r><w:t>Tausquared</w:t></w:r></w:p>'
                      '<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>'
                      '<w:r><w:t>186</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>8 Meta-Analysis of SMD</w:t></w:r></w:p>'
                      '<w:p><w:r><w:t>kept</w:t></w:r><w:del><w:r><w:delText>deleted</w:delText><w:tab/></w:r></w:del>'
                      '<w:hyperlink><w:r><w:t>Linked</w:t></w:r></w:hyperlink><w:r><w:t>Text</w:t></w:r></w:p>')
        paras = kb.docx_paragraphs(p)
        self.assertEqual(paras, ["[TÁBLÁZAT]", "Hedges | −0.3116 (SE)", "[/TÁBLÁZAT]", "Zygomaticword",
                                 "Heterogeneity\nTausquared", "186\t8 Meta-Analysis of SMD", "keptLinkedText"])
        kb.ingest(p, db=self.db)
        for q in ("Hedges", "Zygomaticword", "Tausquared", "Heterogeneity", "0.3116"):
            self.assertEqual(len(self.chunk_hits(q)), 1, q)
        # a lap+fejezet futófej nem lesz lokátor ('1868 Meta-Analysis…')
        locs = [r[0] for r in kb.query("SELECT locator FROM chunk", db=self.db)[1]]
        self.assertFalse(any(l and l.startswith("1868") for l in locs), locs)

    def test_textbox_text_once(self):
        p = make_docx(os.path.join(self.tmp, "tb.docx"),
                      '<w:p><w:r><w:t>Host</w:t></w:r><w:r><mc:AlternateContent '
                      'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
                      '<mc:Choice><w:drawing><w:txbxContent><w:p><w:r><w:t>Boxed</w:t></w:r></w:p></w:txbxContent>'
                      '</w:drawing></mc:Choice><mc:Fallback><w:pict><w:txbxContent><w:p><w:r><w:t>Boxed</w:t></w:r>'
                      '</w:p></w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>')
        self.assertEqual(kb.docx_paragraphs(p), ["Host Boxed "])

    def test_invalid_docx_is_kberror(self):
        p = os.path.join(self.tmp, "x.docx")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("other.xml", "<a/>")
        with self.assertRaises(kb.KBError):
            kb.docx_paragraphs(p)


# ================================================================ tudásbázis: lekérdezések
class TestKbQueries(_Tmp):
    """kb-sql-silent-truncation, kb-sql-no-timeout, silent-empty-checklist-rules, kb-docs-empty-and-silent"""

    def setUp(self):
        super().setUp()
        kb.build(self.db)

    def test_sql_truncation_reported(self):
        n = kb.stats(self.db)["decision_rule"]
        info = {}
        cols, rows = kb.query("SELECT rule_id FROM decision_rule", db=self.db, max_rows=3, info=info)
        self.assertEqual((len(rows), info["truncated"]), (3, True))
        cols, rows = kb.query("SELECT rule_id FROM decision_rule", db=self.db, max_rows=0, info=info)
        self.assertEqual((len(rows), info["truncated"]), (n, False))
        cols, rows = kb.query("SELECT rule_id FROM decision_rule", db=self.db, max_rows=n, info=info)
        self.assertFalse(info["truncated"])
        code, so, se = run_cli("kb", "--db", self.db, "sql", "SELECT rule_id FROM decision_rule", "--max-rows", "2", "--json")
        self.assertEqual(len(json.loads(so)), 2)
        self.assertIn("csonkolva", se)
        code, so, se = run_cli("kb", "--db", self.db, "sql", "SELECT rule_id FROM decision_rule", "--max-rows", "0")
        self.assertEqual(len(so.strip().split("\n")), n + 1)
        self.assertEqual(se, "")

    def test_sql_time_limit(self):
        t = time.monotonic()
        with self.assertRaisesRegex(kb.KBError, "időkorlát"):
            kb.query("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT max(x) FROM c",
                     db=self.db, timeout=0.5)
        self.assertLess(time.monotonic() - t, 5)
        self.assertEqual(kb.query("SELECT COUNT(*) FROM stage", db=self.db, timeout=0.5)[1], [(15,)])
        code, so, se = run_cli("kb", "--db", self.db, "sql",
                               "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT max(x) FROM c",
                               "--timeout", "0.3")
        self.assertEqual(code, 1)
        self.assertIn("időkorlát", se)

    def test_unknown_checklist_and_stage_exit_nonzero_with_names(self):
        code, so, se = run_cli("kb", "--db", self.db, "checklist", "PRIZMA")
        self.assertEqual(code, 1)
        self.assertIn("Elérhető", se)
        code, so, se = run_cli("kb", "--db", self.db, "rules", "--stage", "S99")
        self.assertEqual(code, 1)
        self.assertIn("S00", se)
        self.assertIn("S14", se)
        code, so, se = run_cli("kb", "--db", self.db, "rules", "--stage", "S08", "--agent", "planner")
        self.assertEqual(code, 0)
        if not so.strip():
            self.assertIn("Nincs szabály", se)

    def test_reviewer_sees_engine_rules(self):
        ids = {r["rule_id"] for r in kb.rules(stage="s05", applies_to="reviewer", db=self.db)}
        self.assertTrue({"V011", "V012", "V017", "V018"} <= ids)
        self.assertFalse(any(r["applies_to"] == "engine" for r in kb.rules(stage="S05", applies_to="planner", db=self.db)))
        code, so, se = run_cli("kb", "--db", self.db, "rules", "--stage", "S05", "--agent", "reviewer")
        self.assertIn("[V011]", so)


class TestChecklistNames(_SeedTmp):
    def test_case_insensitive_and_available_names(self):
        self.seed_file("checklists_t.json", [
            {"item_id": "PR-1", "checklist": "PRISMA2020", "ord": 1, "text": "Cím"},
            {"item_id": "RV-1", "checklist": "REVIEWER", "ord": 1, "text": "Adatok"}])
        kb.build(self.db)
        self.assertEqual([r["item_id"] for r in kb.checklist("prisma2020", db=self.db)], ["PR-1"])
        code, so, se = run_cli("kb", "--db", self.db, "checklist", "NINCS")
        self.assertEqual(code, 1)
        self.assertIn("PRISMA2020, REVIEWER", se)


class TestAgentDocsMatchKb(_Tmp):
    """kb-docs-empty-and-silent: az ágensdokumentumokban hivatkozott ellenőrzőlisták léteznek
    (kihagyva, amíg a checklists*.json seed nincs megírva)."""

    def test_documented_checklists_exist(self):
        kb.build(self.db)
        names = set(kb.checklist_names(self.db))
        if not names:
            self.skipTest("a checklists*.json seed még nincs megírva")
        docs = glob.glob(os.path.join(REPO, ".claude", "agents", "*.md")) + \
            glob.glob(os.path.join(REPO, ".claude", "skills", "*", "SKILL.md"))
        wanted = set()
        for f in docs:
            with open(f, encoding="utf-8") as fh:
                wanted |= set(re.findall(r"kb checklist ([A-Z0-9]+)", fh.read()))
        self.assertEqual(sorted(wanted - names), [])


if __name__ == "__main__":
    unittest.main()
