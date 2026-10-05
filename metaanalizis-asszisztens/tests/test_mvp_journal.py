# -*- coding: utf-8 -*-
"""E7: a projektnapló JSON-kimenetei és szereplője (actor), a szk.ma.journal-export/v1, a csak olvasó
rev-számláló, a ma-projekt.json (szk.ma.project/v1), valamint a hash-láncolt tevékenységnapló
(szk.ma.activity/v1, metaelemzes.activity) és a CLI-horog (MA_ACTIVITY_LOG=1).
"""
import datetime
import hashlib
import inspect
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from _helpers import ROOT
from metaelemzes import activity as A
from metaelemzes import cli, projekt

BCG = os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv")
TS = "2026-10-04T21:12:00Z"


def fixed_clock():
    return datetime.datetime(2026, 10, 4, 21, 12, 0)


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:
            code = exc.code
    return code, buf.getvalue(), err.getvalue()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, sort_keys=True)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = os.path.join(self.tmp, "proj")

    def init(self):
        projekt.init(self.p, "Teszt", "PICO?")
        return self.p

    def log_lines(self):
        with open(A.log_path(self.p), "rb") as fh:
            return fh.read().split(b"\n")[:-1]

    def write_lines(self, lines):
        with open(A.log_path(self.p), "wb") as fh:
            fh.write(b"".join(ln + b"\n" for ln in lines))


# ============================================================================ projektnapló
class ActorColumnTest(_Tmp):
    def test_new_journal_has_actor_columns_default_null(self):
        self.init()
        con = sqlite3.connect(projekt.db_path(self.p))
        for table in ("decision", "finding", "checkpoint", "grade", "run"):
            cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]
            self.assertIn("actor", cols, table)
        self.assertIn("resolved_actor", [r[1] for r in con.execute("PRAGMA table_info(finding)")])
        con.close()
        did = projekt.log_decision(self.p, "planner", "RR", check_kb=False)
        self.assertIsNone(projekt.get_item(self.p, "decision", did)["actor"])

    def test_actor_stored_by_every_writer(self):
        self.init()
        did = projekt.log_decision(self.p, "user", "RR", check_kb=False, actor="user:SzK")
        fid = projekt.add_finding(self.p, "reviewer", "minor", "apróság", stage="S08", check_kb=False,
                                  actor="agent:ma-ellenorzo")
        cid = projekt.checkpoint(self.p, "S01-S02", "reviewer", "PASS", actor="user:SzK")
        gid = projekt.add_grade(self.p, "o1", "low", check_kb=False, actor="user:SzK", risk_of_bias="-1",
                                inconsistency="-1")
        projekt.log_run(self.p, "ma.py analyze", BCG, self.tmp, "0.1.0", {"k": 13}, actor="cli")
        self.assertEqual(projekt.get_item(self.p, "decision", did)["actor"], "user:SzK")
        self.assertEqual(projekt.get_item(self.p, "finding", fid)["actor"], "agent:ma-ellenorzo")
        self.assertEqual([c["actor"] for c in projekt.list_items(self.p, "checkpoints")], ["user:SzK", "user:SzK"])
        self.assertEqual(projekt.get_item(self.p, "checkpoint", cid)["stage_id"], "S02")
        self.assertEqual(projekt.get_item(self.p, "grade", gid)["actor"], "user:SzK")
        self.assertEqual(projekt.list_items(self.p, "runs")[0]["actor"], "cli")

    def test_resolve_records_resolved_actor_and_keeps_creator(self):
        self.init()
        fid = projekt.add_finding(self.p, "reviewer", "blocker", "SE/SD", stage="S08", check_kb=False,
                                  actor="agent:ma-ellenorzo")
        projekt.resolve_finding(self.p, fid, "fixed", "javítva", actor="user:SzK")
        f = projekt.get_item(self.p, "finding", fid)
        self.assertEqual((f["actor"], f["resolved_actor"], f["status"]), ("agent:ma-ellenorzo", "user:SzK", "fixed"))
        projekt.resolve_finding(self.p, fid, "open", "mégsem", actor="user:XY")
        f = projekt.get_item(self.p, "finding", fid)
        self.assertEqual((f["resolved_actor"], f["status"], f["resolved_ts"]), ("user:XY", "open", None))
        projekt.resolve_finding(self.p, fid, "fixed", "most már igen")      # actor nélkül: NULL
        self.assertIsNone(projekt.get_item(self.p, "finding", fid)["resolved_actor"])

    def test_invalid_actor_rejected_before_any_write(self):
        self.init()
        for bad in ("", "   ", "a\nb", "x" * 201, 5, "tab\there"):
            with self.assertRaises(ValueError, msg=repr(bad)) as cm:
                projekt.log_decision(self.p, "planner", "RR", check_kb=False, actor=bad)
            self.assertIn("szereplő", str(cm.exception))
            with self.assertRaises(ValueError):
                projekt.checkpoint(self.p, "S01", "planner", "PASS", actor=bad)
            with self.assertRaises(ValueError):
                projekt.add_grade(self.p, "o1", "low", check_kb=False, actor=bad)
            with self.assertRaises(ValueError):
                projekt.log_run(self.p, "c", None, None, "0", actor=bad)
        for kind in ("decisions", "checkpoints", "grades", "runs"):
            self.assertEqual(projekt.list_items(self.p, kind), [], kind)
        self.assertEqual(projekt.check_actor("  user:SzK "), "user:SzK")

    def test_old_journal_upgraded_in_place(self):
        os.makedirs(self.p)
        con = sqlite3.connect(projekt.db_path(self.p))
        con.executescript("""
            CREATE TABLE project (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE decision (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, agent TEXT NOT NULL, stage_id TEXT,
                decision TEXT NOT NULL, rationale TEXT, alternatives TEXT, kb_refs TEXT,
                status TEXT NOT NULL DEFAULT 'active', supersedes INTEGER);
            CREATE TABLE finding (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, agent TEXT NOT NULL, stage_id TEXT,
                severity TEXT NOT NULL, title TEXT NOT NULL, detail TEXT, evidence TEXT, kb_refs TEXT,
                status TEXT NOT NULL DEFAULT 'open', resolution TEXT, resolved_ts TEXT);
            CREATE TABLE checkpoint (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, stage_id TEXT NOT NULL,
                agent TEXT NOT NULL, verdict TEXT NOT NULL, summary TEXT);
            CREATE TABLE grade (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, outcome TEXT NOT NULL, k INTEGER,
                participants INTEGER, effect TEXT, risk_of_bias TEXT, inconsistency TEXT, indirectness TEXT,
                imprecision TEXT, publication_bias TEXT, upgrades TEXT, certainty TEXT NOT NULL, rationale TEXT,
                kb_refs TEXT);
            CREATE TABLE run (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, command TEXT, data_path TEXT,
                data_sha256 TEXT, outdir TEXT, engine_version TEXT, summary TEXT);
            INSERT INTO decision (ts, agent, decision) VALUES ('2020-01-01T00:00:00', 'planner', 'régi');
            INSERT INTO finding (ts, agent, severity, title) VALUES ('2020-01-01T00:00:00', 'reviewer', 'minor', 'r');
        """)
        con.commit()
        con.close()
        d = projekt.get_item(self.p, "decision", 1)
        self.assertEqual((d["decision"], d["actor"], d["kb_unverified"]), ("régi", None, None))
        fid = projekt.add_finding(self.p, "reviewer", "minor", "új", check_kb=False, actor="user:SzK")
        projekt.resolve_finding(self.p, 1, "fixed", "kész", actor="user:SzK")
        self.assertEqual(projekt.get_item(self.p, "finding", fid)["actor"], "user:SzK")
        self.assertEqual(projekt.get_item(self.p, "finding", 1)["resolved_actor"], "user:SzK")
        projekt.connect(self.p).close()        # ismételt bővítés: nincs „duplicate column” hiba


class JsonOutputsTest(_Tmp):
    def _poison(self):
        """Nem JSON-képes SQLite-értékek: végtelen REAL és BLOB."""
        con = sqlite3.connect(projekt.db_path(self.p))
        con.execute("INSERT INTO grade (ts, outcome, k, participants, certainty, effect) VALUES (?,?,?,?,?,?)",
                    ("2026-01-01T00:00:00", "o1", float("inf"), float("-inf"), "low", b"\xff\xfeblob"))
        con.execute("INSERT INTO run (ts, command, summary) VALUES (?,?,?)",
                    ("2026-01-01T00:00:00", b"bin", 1e309))
        con.execute("INSERT OR REPLACE INTO project (key, value) VALUES ('weird', ?)", (float("inf"),))
        con.commit()
        con.close()

    def test_every_query_is_strict_json(self):
        self.init()
        projekt.add_finding(self.p, "reviewer", "blocker", "SE/SD", stage="S08", check_kb=False)
        self._poison()
        for obj in (projekt.status(self.p), projekt.list_items(self.p, "grades"), projekt.list_items(self.p, "runs"),
                    projekt.get_item(self.p, "grade", 1), projekt.get_item(self.p, "run", 1),
                    projekt.export_json(self.p)):
            strict_dumps(obj)
        g = projekt.get_item(self.p, "grade", 1)
        self.assertEqual((g["k"], g["participants"]), (None, None))
        self.assertIsInstance(g["effect"], str)
        self.assertEqual(projekt.status(self.p)["project"]["weird"], "Inf")     # TEXT-affinitás: szövegként tárolva
        r = projekt.get_item(self.p, "run", 1)
        self.assertEqual((r["command"], r["summary"]), ("bin", "Inf"))      # BLOB → szöveg

    def test_status_keys_and_cli_outputs_unchanged(self):
        self.init()
        projekt.log_decision(self.p, "planner", "RR", check_kb=False, actor="user:SzK")
        st = projekt.status(self.p)
        self.assertEqual(set(st), {"project", "open_findings", "findings_by_status", "checkpoints", "decisions",
                                   "runs", "grade", "warnings"})
        code, out, _ = run_cli("project", "status", self.p)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), json.loads(json.dumps(st)))
        self.assertNotIn("actor", out)
        md = projekt.export_markdown(self.p)
        self.assertNotIn("user:SzK", md)        # a Markdown-export változatlan (actor nélkül)


class ExportJsonTest(_Tmp):
    def test_schema_content_and_determinism(self):
        self.init()
        projekt.log_decision(self.p, "planner", "RR | modell", rationale="sor1\nsor2", stage="S05", check_kb=False,
                             actor="user:SzK")
        fid = projekt.add_finding(self.p, "reviewer", "blocker", "SE/SD", stage="S08", check_kb=False)
        projekt.add_finding(self.p, "nobody", "minor", "ismeretlen ágens", check_kb=False)
        projekt.resolve_finding(self.p, fid, "fixed", "javítva", actor="user:SzK")
        projekt.checkpoint(self.p, "S08", "reviewer", "PASS")
        projekt.add_grade(self.p, "mortalitás", "low", check_kb=False, risk_of_bias="-1", inconsistency="-1",
                          indirectness="0", imprecision="0", publication_bias="0", k=13)
        projekt.log_run(self.p, "ma.py analyze", BCG, self.tmp, "0.1.0", {"k": 13, "estimate": -0.71})
        ex = projekt.export_json(self.p)
        self.assertEqual(list(ex), ["schema", "project", "decisions", "findings", "checkpoints", "grades", "runs",
                                    "warnings"])
        self.assertEqual(ex["schema"], "szk.ma.journal-export/v1")
        self.assertEqual(ex["project"]["title"], "Teszt")
        self.assertEqual(ex["decisions"][0]["rationale"], "sor1\nsor2")
        self.assertEqual(ex["decisions"][0]["actor"], "user:SzK")
        self.assertEqual([f["id"] for f in ex["findings"]], [1, 2])
        self.assertEqual(ex["findings"][0]["resolved_actor"], "user:SzK")
        self.assertEqual(ex["checkpoints"][0]["verdict"], "PASS")
        self.assertEqual(ex["grades"][0]["k"], 13)
        self.assertEqual(json.loads(ex["runs"][0]["summary"]), {"k": 13, "estimate": -0.71})
        self.assertTrue(any("nobody" in w for w in ex["warnings"]))
        self.assertEqual(ex["findings"][0], projekt.get_item(self.p, "finding", fid))
        self.assertEqual(strict_dumps(ex), strict_dumps(projekt.export_json(self.p)))

    def test_empty_journal(self):
        self.init()
        ex = projekt.export_json(self.p)
        for k in ("decisions", "findings", "checkpoints", "grades", "runs", "warnings"):
            self.assertEqual(ex[k], [], k)

    def test_missing_journal(self):
        with self.assertRaises(FileNotFoundError):
            projekt.export_json(self.p)


class RevTest(_Tmp):
    def test_rev_counts_inserts_updates_and_replacement(self):
        self.init()
        projekt.connect(self.p).close()
        with projekt.JournalRev(self.p) as w:
            r0, changed = w.poll()
            self.assertFalse(changed)
            self.assertEqual(w.poll(), (r0, False))
            projekt.status(self.p)                       # olvasás: nem változás
            self.assertEqual(w.poll(), (r0, False))
            fid = projekt.add_finding(self.p, "reviewer", "minor", "x", check_kb=False)
            r1, changed = w.poll()
            self.assertTrue(changed)
            self.assertGreater(r1, r0)
            projekt.resolve_finding(self.p, fid, "fixed", "ok")   # UPDATE: a max rowid nem változik
            r2, changed = w.poll()
            self.assertTrue(changed and r2 > r1)
            self.assertEqual(w.rev(), r2)
            os.remove(projekt.db_path(self.p))
            r3, changed = w.poll()
            self.assertTrue(changed and r3 > r2)
            projekt.init(self.p, "Új")
            r4, changed = w.poll()
            self.assertTrue(changed and r4 > r3)

    def test_rev_is_read_only(self):
        """A számláló nem ír: régi napló sémáját sem bővíti, a fájl bájtra azonos marad."""
        os.makedirs(self.p)
        con = sqlite3.connect(projekt.db_path(self.p))
        con.execute("CREATE TABLE decision (id INTEGER PRIMARY KEY, ts TEXT, agent TEXT, decision TEXT)")
        con.commit()
        con.close()
        with open(projekt.db_path(self.p), "rb") as fh:
            before = fh.read()
        w = projekt.JournalRev(self.p)
        w.poll()
        w.poll()
        marks = projekt.journal_marks(self.p)
        w.close()
        with open(projekt.db_path(self.p), "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(marks["tables"]["decision"], [0, 0])
        self.assertEqual(marks["tables"]["finding"], [0, 0])

    def test_rev_does_not_block_writers(self):
        self.init()
        w = projekt.JournalRev(self.p)
        self.addCleanup(w.close)
        w.poll()
        con = sqlite3.connect(projekt.db_path(self.p), timeout=0.5)
        con.execute("BEGIN EXCLUSIVE")              # nyitva hagyott olvasás mellett ez „database is locked” lenne
        con.execute("INSERT INTO project (key, value) VALUES ('x', 'y')")
        con.commit()
        con.close()
        self.assertTrue(w.poll()[1])

    def test_journal_marks_cross_process_comparable(self):
        self.init()
        projekt.connect(self.p).close()
        m0 = projekt.journal_marks(self.p)
        self.assertEqual(m0, projekt.journal_marks(self.p))
        self.assertIsInstance(m0["change_counter"], int)
        fid = projekt.add_finding(self.p, "reviewer", "minor", "x", check_kb=False)
        m1 = projekt.journal_marks(self.p)
        self.assertEqual(m1["tables"]["finding"], [1, fid])
        projekt.resolve_finding(self.p, fid, "fixed", "ok")
        m2 = projekt.journal_marks(self.p)
        self.assertEqual(m2["tables"], m1["tables"])
        self.assertNotEqual(m2["change_counter"], m1["change_counter"])
        strict_dumps(m2)


class ProjectMetaTest(_Tmp):
    def meta(self, **kw):
        m = {"title": "GLP-1 és mortalitás", "data_class": "A", "review_type": "intervention",
             "question": {"P": "T2DM", "I": "GLP-1", "C": "placebo", "O": "mortalitás"},
             "outcomes": [{"id": "o1", "name": {"hu": "Mortalitás", "en": "Mortality"}, "data": "03_adatok/o1.csv",
                           "measure": "RR", "critical": True, "primary_spec": "05_elemzes/specs/o1_primary.json",
                           "grade_start": "high"}],
             "appraisal_tools": ["rob2"], "composer": {"project": "glp1", "outdir": "02_szures"},
             "doc_roots": ["_privat/pdf"], "locale": "hu",
             "conventions": {"amstar2_partial_yes_critical": "meets", "grade_suspected": "unresolved"}}
        m.update(kw)
        return m

    def test_roundtrip_defaults_and_order(self):
        os.makedirs(self.p)
        self.assertIsNone(projekt.load_project_meta(self.p))
        saved = projekt.save_project_meta(self.p, {"title": "T", "data_class": "b", "x_extra": {"keep": 1}})
        self.assertEqual(saved["schema"], "szk.ma.project/v1")
        self.assertEqual(saved["data_class"], "B")
        self.assertEqual(saved["conventions"], {"amstar2_partial_yes_critical": "meets",
                                                "grade_suspected": "unresolved"})
        self.assertEqual((saved["locale"], saved["outcomes"], saved["appraisal_tools"], saved["doc_roots"]),
                         ("hu", [], [], []))
        self.assertEqual(saved["x_extra"], {"keep": 1})
        self.assertEqual(list(saved)[:2], ["schema", "title"])
        with open(projekt.project_meta_path(self.p), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith('{\n  "schema": "szk.ma.project/v1"'))
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(projekt.load_project_meta(self.p), saved)
        full = projekt.save_project_meta(self.p, self.meta())
        self.assertEqual(projekt.load_project_meta(self.p), full)
        self.assertEqual(full["outcomes"][0]["name"], {"hu": "Mortalitás", "en": "Mortality"})
        self.assertEqual([f for f in os.listdir(self.p) if f.endswith(".tmp")], [])

    def test_name_string_becomes_i18n(self):
        os.makedirs(self.p)
        m = self.meta()
        m["outcomes"][0]["name"] = "Mortalitás"
        self.assertEqual(projekt.save_project_meta(self.p, m)["outcomes"][0]["name"],
                         {"hu": "Mortalitás", "en": "Mortalitás"})

    def test_validation_errors_leave_file_untouched(self):
        os.makedirs(self.p)
        projekt.save_project_meta(self.p, self.meta())
        with open(projekt.project_meta_path(self.p), "rb") as fh:
            before = fh.read()
        bad_cases = [
            ({"data_class": "D"}, "data_class"),
            ({"data_class": None}, "data_class"),
            ({"title": ""}, "title"),
            ({"review_type": "narrative"}, "review_type"),
            ({"schema": "szk.ma.project/v2"}, "schema"),
            ({"conventions": {"grade_suspected": "minus_one"}}, "grade_suspected"),
            ({"conventions": {"amstar2_partial_yes_critical": "yes"}}, "amstar2_partial_yes_critical"),
            ({"locale": "de"}, "locale"),
            ({"outcomes": [{"id": "o1"}, {"id": "o1"}]}, "ismétlődő"),
            ({"outcomes": [{"id": "o 1"}]}, "outcomes[0].id"),
            ({"outcomes": [{"id": "o1", "data": "../kint.csv"}]}, "outcomes[0].data"),
            ({"outcomes": [{"id": "o1", "data": "C:/x.csv"}]}, "outcomes[0].data"),
            ({"outcomes": [{"id": "o1", "grade_start": "moderate"}]}, "grade_start"),
            ({"outcomes": [{"id": "o1", "critical": "igen"}]}, "critical"),
            ({"outcomes": [{"id": "o1", "name": {"hu": "csak magyar"}}]}, "name"),
            ({"question": "PICO szöveg"}, "question"),
            ({"appraisal_tools": ["rob2", "rob2"]}, "appraisal_tools"),
            ({"doc_roots": "egy"}, "doc_roots"),
            ({"composer": {"project": 5}}, "composer.project"),
            ({"plugins": {"validator": 1}}, "plugins"),
            ({"x": float("nan")}, "JSON"),
        ]
        for patch, needle in bad_cases:
            with self.assertRaises(ValueError, msg=repr(patch)) as cm:
                projekt.save_project_meta(self.p, self.meta(**patch))
            self.assertIn(needle, str(cm.exception), patch)
            self.assertIn("ma-projekt.json", str(cm.exception))
        with open(projekt.project_meta_path(self.p), "rb") as fh:
            self.assertEqual(fh.read(), before)
        for rt in projekt.REVIEW_TYPES:
            self.assertEqual(projekt.save_project_meta(self.p, self.meta(review_type=rt))["review_type"], rt)
        self.assertIsNone(projekt.save_project_meta(self.p, self.meta(review_type=None))["review_type"])

    def test_load_rejects_bad_json_nan_and_accepts_bom(self):
        os.makedirs(self.p)
        path = projekt.project_meta_path(self.p)
        for raw, needle in ((b"{nem json", "JSON"), (b'{"title": "T", "data_class": "A", "x": NaN}', "JSON"),
                            (b"[]", "objektum"), (b'{"title": "T", "data_class": "Z"}', "data_class")):
            with open(path, "wb") as fh:
                fh.write(raw)
            with self.assertRaises(ValueError, msg=raw) as cm:
                projekt.load_project_meta(self.p)
            self.assertIn(needle, str(cm.exception))
        with open(path, "wb") as fh:
            fh.write(b'\xef\xbb\xbf{"title": "T", "data_class": "c"}')
        self.assertEqual(projekt.load_project_meta(self.p)["data_class"], "C")


# ============================================================================ tevékenységnapló
class ActivityChainTest(_Tmp):
    def setUp(self):
        super(ActivityChainTest, self).setUp()
        os.makedirs(os.path.join(self.p, "03_adatok"))
        with open(os.path.join(self.p, "03_adatok", "o1.csv"), "wb") as fh:
            fh.write(b"study,yi,vi\nA,0.1,0.01\n")
        self.data_sha = sha(b"study,yi,vi\nA,0.1,0.01\n")

    def rec(self, **kw):
        r = {"action": "analyze.commit", "actor": "user:SzK",
             "argv": ["ma.py", "analyze", "--spec", "05_elemzes/specs/o1_primary.json"],
             "inputs": ["03_adatok/o1.csv"], "outputs": {}, "result": {"exit_code": 0, "summary": "k=13"}}
        r.update(kw)
        return r

    def test_record_format_and_chain(self):
        r1 = A.append(self.p, self.rec(), clock=fixed_clock)
        r2 = A.append(self.p, self.rec(action="project.log", argv=None, inputs=None, result=0), clock=fixed_clock)
        lines = self.log_lines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(list(first), ["schema", "seq", "ts", "actor", "action", "argv", "inputs", "outputs",
                                       "result", "prev"])
        self.assertEqual(first, r1)
        self.assertEqual((r1["schema"], r1["seq"], r1["ts"], r1["prev"]), ("szk.ma.activity/v1", 1, TS, None))
        self.assertEqual(r1["inputs"], {"03_adatok/o1.csv": self.data_sha})
        self.assertEqual(r2["seq"], 2)
        canon = json.dumps(first, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertEqual(r2["prev"], sha(canon))
        self.assertEqual(r2["result"], {"exit_code": 0, "summary": None})
        self.assertEqual((r2["argv"], r2["inputs"], r2["outputs"]), (None, {}, {}))
        self.assertEqual(A.head(self.p), {"seq": 2, "hash": A.record_hash(r2)})
        self.assertEqual(A.verify(self.p), (True, None, "A lánc ép: 2 bejegyzés."))
        self.assertEqual(A.read(self.p), [r1, r2])

    def test_clock_and_ts(self):
        aware = datetime.datetime(2026, 10, 4, 23, 12, 0, 999, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
        self.assertEqual(A.append(self.p, self.rec(), clock=lambda: aware)["ts"], TS)
        self.assertEqual(A.append(self.p, self.rec(ts="2026-01-02T03:04:05Z"))["ts"], "2026-01-02T03:04:05Z")
        self.assertRegex(A.append(self.p, self.rec())["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        with self.assertRaises(A.ActivityError):
            A.append(self.p, self.rec(ts="2026-01-02 03:04"))
        self.assertTrue(A.verify(self.p)[0])

    def test_file_maps(self):
        outside = os.path.join(self.tmp, "kint.csv")
        with open(outside, "wb") as fh:
            fh.write(b"x")
        r = A.append(self.p, self.rec(
            inputs=[os.path.join(self.p, "03_adatok", "o1.csv"), outside, "nincs/ilyen.csv",
                    ("05_elemzes/a.json", "a" * 64), {"path": "06_kezirat\\b.md", "sha256": None}],
            outputs={"./03_adatok/o1.csv": None, "x/../y.json": "b" * 64}), clock=fixed_clock)
        self.assertEqual(r["inputs"], {
            "03_adatok/o1.csv": self.data_sha, os.path.realpath(outside).replace("\\", "/"): sha(b"x"),
            "05_elemzes/a.json": "a" * 64, "06_kezirat/b.md": None, "nincs/ilyen.csv": None})
        self.assertEqual(list(r["inputs"]), sorted(r["inputs"]))
        self.assertEqual(r["outputs"], {"03_adatok/o1.csv": self.data_sha, "y.json": "b" * 64})
        for bad in (["../kint.csv"], {"a.csv": "nem-hash"}, [("a.csv", "A" * 64)], [{"path": "a", "x": 1}], "a.csv",
                    [123], [""]):
            with self.assertRaises(A.ActivityError, msg=repr(bad)):
                A.append(self.p, self.rec(inputs=bad))
        self.assertEqual(len(self.log_lines()), 1)

    def test_forbidden_keys_refused_anywhere(self):
        for patch in ({"details": {"rows": [1]}}, {"details": {"x": [{"Cell": 1}]}},
                      {"result": {"exit_code": 0, "summary": "s", "cells": {}}}, {"details": {"value": 0.5}},
                      {"details": {"a": {"values": []}}}, {"details": {"value_as_entered": "1,5"}}):
            with self.assertRaises(A.ActivityError, msg=repr(patch)) as cm:
                A.append(self.p, self.rec(**patch))
            self.assertIn("cellaérték", str(cm.exception))
        self.assertFalse(os.path.exists(A.log_path(self.p)))
        r = A.append(self.p, self.rec(details={"kind": "decision", "id": 5}))
        self.assertEqual(r["details"], {"kind": "decision", "id": 5})
        self.assertEqual(list(r)[-2:], ["details", "prev"])

    def test_other_invalid_records(self):
        cases = [self.rec(seq=5), self.rec(prev=None), self.rec(hash="x"), self.rec(action="1bad"),
                 self.rec(action="a b"), self.rec(actor=""), self.rec(actor="a\x00"), self.rec(actor="x" * 201),
                 self.rec(argv="ma.py analyze"), self.rec(argv=["a", 1]), self.rec(argv=["a\x00"]),
                 self.rec(result="ok"), self.rec(result=True), self.rec(result={"exit_code": "0"}),
                 self.rec(result={"exit_code": 0, "summary": 5}), self.rec(result={"exit_code": 0, "x": float("nan")}),
                 self.rec(details=[1]), self.rec(details={"x": float("inf")}), self.rec(details={"x": object()}),
                 self.rec(schema="szk.ma.activity/v2"), "nem objektum"]
        for bad in cases:
            with self.assertRaises(A.ActivityError, msg=repr(bad)):
                A.append(self.p, bad)
        self.assertFalse(os.path.exists(A.log_path(self.p)))
        self.assertEqual(A.verify(self.p), (True, None, "Nincs tevékenységnapló (üres lánc)."))

    def _three(self):
        for i in range(3):
            A.append(self.p, self.rec(result={"exit_code": 0, "summary": "#%d" % i}), clock=fixed_clock)
        return self.log_lines()

    def test_tamper_detection(self):
        lines = self._three()
        anchor = A.head(self.p)
        # tartalom-módosítás egy középső sorban → a következő sor prev-je nem egyezik
        rec = json.loads(lines[1])
        rec["result"]["summary"] = "hamis"
        self.write_lines([lines[0], json.dumps(rec).encode(), lines[2]])
        ok, bad, msg = A.verify(self.p)
        self.assertEqual((ok, bad), (False, 3))
        self.assertIn("hash", msg)
        # törlés → sorszám-ugrás
        self.write_lines([lines[0], lines[2]])
        self.assertEqual(A.verify(self.p)[:2], (False, 2))
        # átrendezés
        self.write_lines([lines[0], lines[2], lines[1]])
        self.assertEqual(A.verify(self.p)[:2], (False, 2))
        # a záró sor törlése csak a rögzített fejjel derül ki
        self.write_lines(lines[:2])
        self.assertTrue(A.verify(self.p)[0])
        ok, bad, msg = A.verify(self.p, anchor)
        self.assertEqual((ok, bad), (False, 3))
        self.assertIn("rögzített fej", msg)
        # az utolsó sor átírása a fejjel
        rec = json.loads(lines[2])
        rec["actor"] = "user:XY"
        self.write_lines([lines[0], lines[1], json.dumps(rec).encode()])
        self.assertTrue(A.verify(self.p)[0])
        self.assertEqual(A.verify(self.p, anchor)[:2], (False, 3))
        self.write_lines(lines)
        self.assertEqual(A.verify(self.p, anchor), (True, None, "A lánc ép: 3 bejegyzés."))
        os.remove(A.log_path(self.p))
        self.assertEqual(A.verify(self.p, anchor)[:2], (False, 3))

    def test_reformatting_keeps_chain(self):
        lines = self._three()
        pretty = json.dumps(json.loads(lines[1]), sort_keys=True, ensure_ascii=True, separators=(", ", ": "))
        self.write_lines([lines[0], pretty.encode(), lines[2]])
        self.assertTrue(A.verify(self.p)[0])
        with open(A.log_path(self.p), "wb") as fh:            # CRLF (pl. git autocrlf)
            fh.write(b"".join(ln + b"\r\n" for ln in lines))
        self.assertTrue(A.verify(self.p)[0])

    def test_verify_rejects_bad_lines(self):
        lines = self._three()
        rec = json.loads(lines[1])
        nan_line = json.dumps(rec).replace('"#1"', "NaN").encode()
        forbidden = dict(json.loads(lines[0]), details={"rows": 3})
        badtype = dict(json.loads(lines[0]), inputs={"a": "nem-hash"})
        for variant, seq in (([lines[0], nan_line, lines[2]], 2), ([lines[0], b"", lines[1], lines[2]], 2),
                             ([json.dumps(forbidden).encode()], 1), ([json.dumps(badtype).encode()], 1),
                             ([json.dumps(dict(json.loads(lines[0]), schema="x")).encode()], 1),
                             ([json.dumps(dict(json.loads(lines[0]), prev="a" * 64)).encode()], 1),
                             ([json.dumps(dict(json.loads(lines[0]), ts="tegnap")).encode()], 1),
                             ([b"[1, 2]"], 1)):
            self.write_lines(variant)
            ok, bad, msg = A.verify(self.p)
            self.assertEqual((ok, bad), (False, seq), msg)

    def test_truncated_tail_is_reported_and_append_continues(self):
        lines = self._three()
        with open(A.log_path(self.p), "wb") as fh:
            fh.write(b"\n".join(lines[:2]) + b"\n" + lines[2][:20])
        ok, bad, msg = A.verify(self.p)
        self.assertEqual((ok, bad), (False, 3))
        self.assertIn("csonka", msg)
        r = A.append(self.p, self.rec(), clock=fixed_clock)
        self.assertEqual(r["seq"], 3)
        self.assertEqual(r["prev"], A.record_hash(json.loads(lines[1])))
        self.assertEqual(json.loads(self.log_lines()[-1]), r)
        self.assertEqual(A.verify(self.p)[:2], (False, 3))

    def test_threads_and_processes_append_safely(self):
        n_thr, per = 4, 15
        errors = []

        def worker(k):
            try:
                for i in range(per):
                    A.append(self.p, self.rec(actor="agent:t%d" % k, result={"exit_code": 0, "summary": str(i)}),
                             fsync=False)
            except Exception as exc:          # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=worker, args=(k,)) for k in range(n_thr)]
        for t in threads:
            t.start()
        script = ("import sys; sys.path.insert(0, %r)\n"
                  "from metaelemzes import activity as A\n"
                  "for i in range(%d):\n"
                  "    A.append(%r, {'action': 'test.proc', 'actor': 'agent:p' + sys.argv[1], 'result': 0}, fsync=False)\n"
                  % (ROOT, per, self.p))
        procs = [subprocess.Popen([sys.executable, "-c", script, str(k)]) for k in range(3)]
        for t in threads:
            t.join()
        self.assertEqual([p.wait(timeout=120) for p in procs], [0, 0, 0])
        self.assertEqual(errors, [])
        ok, bad, msg = A.verify(self.p)
        self.assertTrue(ok, msg)
        recs = A.read(self.p)
        self.assertEqual([r["seq"] for r in recs], list(range(1, (n_thr + 3) * per + 1)))

    def test_activity_log_object_api(self):
        log = A.ActivityLog(self.p, clock=fixed_clock)
        built = log.build("table.save", "user:SzK", outputs=[{"path": "03_adatok/o1.csv", "sha256": self.data_sha}],
                          details={"partial": False})
        self.assertIsNone(built["seq"])
        self.assertFalse(os.path.exists(log.path))
        r1 = log.append("table.save", "user:SzK", outputs=[{"path": "03_adatok/o1.csv", "sha256": self.data_sha}],
                        details={"partial": False})
        r2 = log.external_edit("03_adatok/o1.csv", "c" * 64)
        self.assertEqual((r2["actor"], r2["action"], r2["outputs"]), ("external", "file.external_edit",
                                                                      {"03_adatok/o1.csv": "c" * 64}))
        self.assertEqual(log.external_edit("03_adatok/o1.csv", None)["outputs"], {"03_adatok/o1.csv": self.data_sha})
        self.assertEqual(log.read()[:2], [r1, r2])
        self.assertEqual(log.head()["seq"], 3)
        self.assertEqual(log.verify(), (True, None, "A lánc ép: 3 bejegyzés."))
        self.assertEqual(log.relpath, "07_ellenorzes/activity.jsonl")


class ActivityCliHookTest(_Tmp):
    ENV = {"MA_ACTIVITY_LOG": "1"}

    def setUp(self):
        super(ActivityCliHookTest, self).setUp()
        self.init()

    def test_disabled_by_default(self):
        for env in ({}, {"MA_ACTIVITY_LOG": "0"}, {"MA_ACTIVITY_LOG": ""}):
            self.assertIsNone(A.cli_record(self.p, ["project", "log", self.p], environ=env))
        self.assertFalse(os.path.exists(A.log_path(self.p)))
        self.assertTrue(A.enabled({"MA_ACTIVITY_LOG": " igen "}))

    def test_project_command_record(self):
        argv = ["projekt", "log", self.p, "--agent", "reviewer", "--decision", "RR | --data x", "--stage=S05",
                "--strict", "--kb-db", os.path.join(self.tmp, "kb.sqlite")]
        r = A.cli_record(self.p, argv, outputs=[projekt.db_path(self.p)], result={"exit_code": 0, "summary": "döntés #1"},
                         environ=dict(self.ENV, MA_ACTOR="agent:ma-ellenorzo"), clock=fixed_clock, cwd=self.tmp)
        self.assertEqual(r["action"], "project.log")
        self.assertEqual(r["actor"], "agent:ma-ellenorzo")
        self.assertEqual(r["argv"], ["ma.py", "projekt", "log", ".", "--agent", "reviewer", "--decision",
                                     "RR | --data x", "--stage=S05", "--strict", "--kb-db",
                                     os.path.realpath(os.path.join(self.tmp, "kb.sqlite"))])
        with open(projekt.db_path(self.p), "rb") as fh:
            self.assertEqual(r["outputs"], {"projekt.sqlite": sha(fh.read())})
        self.assertEqual(r["result"], {"exit_code": 0, "summary": "döntés #1"})
        self.assertTrue(A.verify(self.p)[0])

    def test_analyze_record_relative_paths(self):
        out = os.path.join(self.p, "05_elemzes", "o1")
        os.makedirs(out)
        for name in ("results.json", "report.md", ".hidden"):
            with open(os.path.join(out, name), "w") as fh:
                fh.write(name)
        os.makedirs(os.path.join(self.p, "03_adatok"), exist_ok=True)
        shutil.copy(BCG, os.path.join(self.p, "03_adatok", "o1.csv"))
        argv = ["analyze", "--data", "proj/03_adatok/o1.csv", "--measure", "RR", "--out=proj/05_elemzes/o1",
                "--project", "proj", "--title", "proj/03_adatok/o1.csv"]
        r = A.cli_record(self.p, argv, inputs=["proj/03_adatok/o1.csv"], outputs=["proj/05_elemzes/o1"], result=0,
                         environ=self.ENV, cwd=self.tmp)
        self.assertEqual(r["action"], "analyze")
        self.assertEqual(r["actor"], "cli")
        self.assertEqual(r["argv"], ["ma.py", "analyze", "--data", "03_adatok/o1.csv", "--measure", "RR",
                                     "--out=05_elemzes/o1", "--project", ".", "--title", "proj/03_adatok/o1.csv"])
        with open(BCG, "rb") as fh:
            self.assertEqual(r["inputs"], {"03_adatok/o1.csv": sha(fh.read())})
        self.assertEqual(r["outputs"], {"05_elemzes/o1/report.md": sha(b"report.md"),
                                        "05_elemzes/o1/results.json": sha(b"results.json")})

    def test_program_prefix_and_actor_helpers(self):
        self.assertEqual(A.action_from_argv(["python3", "/x/ma.py", "elemez", "--data", "a"]), "analyze")
        self.assertEqual(A.action_from_argv(["python3", "-m", "metaelemzes", "project", "grade", "d"]), "project.grade")
        self.assertEqual(A.action_from_argv([]), "cli")
        self.assertEqual(A.portable_argv(["python3", "/x/ma.py", "project", "status", self.p], self.p)[:4],
                         ["python3", "/x/ma.py", "project", "status"])
        self.assertEqual(A.cli_actor("reviewer", {}), "agent:reviewer")
        self.assertEqual(A.cli_actor("user", {}), "user")
        self.assertEqual(A.cli_actor(None, {}), "cli")
        self.assertEqual(A.cli_actor("reviewer", {"MA_ACTOR": "user:SzK"}), "user:SzK")
        self.assertEqual(A.cli_actor("reviewer", {"MA_ACTOR": "rossz\nnév"}), "agent:reviewer")

    def test_failure_warns_and_returns_none(self):
        err = io.StringIO()
        with redirect_stderr(err):
            r = A.cli_record(self.p, ["project", "log", self.p], details={"rows": [1]}, environ=self.ENV)
            r2 = A.cli_record(os.path.join(self.tmp, "nincs"), ["analyze"], environ=self.ENV)
        self.assertIsNone(r)
        self.assertIsNone(r2)
        self.assertIn("FIGYELEM: a tevékenységnaplóba", err.getvalue())
        self.assertFalse(os.path.exists(A.log_path(self.p)))

    def test_run_summary_text(self):
        summ = {"k": 13, "measure": "RR", "estimate": -0.7145, "ci": [-1.1, -0.3], "estimate_display": 0.4894,
                "ci_display": [0.3301, 0.7257]}
        self.assertEqual(A.run_summary_text(summ), "k=13, RR 0.49 [0.33; 0.73]")
        self.assertEqual(A.run_summary_text({"k": 5, "measure": "SMD", "estimate": -0.5, "ci": [-0.9, -0.1],
                                             "estimate_display": None, "ci_display": [None, None]}),
                         "k=5, SMD -0.50 [-0.90; -0.10]")
        self.assertEqual(A.run_summary_text({"k": 2, "measure": "MD"}), "k=2, MD")
        self.assertIsNone(A.run_summary_text(None))


_CLI_SRC = inspect.getsource(cli)


@unittest.skipUnless("cli_record" in _CLI_SRC, "a CLI-horog (activity.cli_record) még nincs bekötve a cli.py-ba")
class ActivityCliWiredTest(_Tmp):
    """A bekötött CLI-horog: MA_ACTIVITY_LOG=1 mellett a projektbe író parancsok láncba kerülnek."""

    def test_project_commands_append_when_enabled(self):
        self.init()
        with mock.patch.dict(os.environ, {"MA_ACTIVITY_LOG": "1"}):
            self.assertEqual(run_cli("project", "log", self.p, "--agent", "reviewer", "--decision", "RR")[0], 0)
            self.assertEqual(run_cli("project", "finding", self.p, "--agent", "reviewer", "--severity", "minor",
                                     "--title", "x")[0], 0)
            self.assertEqual(run_cli("project", "resolve", self.p, "1", "--status", "fixed", "--resolution", "ok")[0], 0)
            self.assertEqual(run_cli("project", "checkpoint", self.p, "--stage", "S01", "--agent", "reviewer",
                                     "--verdict", "PASS")[0], 0)
            self.assertEqual(run_cli("project", "grade", self.p, "--outcome", "o1", "--certainty", "low")[0], 0)
            self.assertEqual(run_cli("analyze", "--data", BCG, "--measure", "RR", "--out",
                                     os.path.join(self.p, "05_elemzes", "o1"), "--project", self.p,
                                     "--date", "2026-10-05")[0], 0)
        recs = A.read(self.p)
        self.assertEqual([r["action"] for r in recs][:5], ["project.log", "project.finding", "project.resolve",
                                                           "project.checkpoint", "project.grade"])
        self.assertTrue(recs[5]["action"].startswith("analyze"))
        self.assertTrue(any(k.endswith("results.json") for k in recs[5]["outputs"]))
        self.assertTrue(all(r["result"]["exit_code"] == 0 for r in recs))
        self.assertTrue(A.verify(self.p)[0])
        with open(A.log_path(self.p), encoding="utf-8") as fh:
            self.assertNotIn("Aronson", fh.read())          # cellaérték (vizsgálatnév) nincs a naplóban

    def test_nothing_appended_when_disabled(self):
        self.init()
        with mock.patch.dict(os.environ, {"MA_ACTIVITY_LOG": "0"}):
            self.assertEqual(run_cli("project", "log", self.p, "--agent", "reviewer", "--decision", "RR")[0], 0)
        self.assertFalse(os.path.exists(A.log_path(self.p)))


if __name__ == "__main__":
    unittest.main()
