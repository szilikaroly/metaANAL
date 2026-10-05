# -*- coding: utf-8 -*-
"""ma_gui.activity: hash-lánc (szk.ma.activity/v1), manipuláció felismerése, cellaérték-tilalom,
konkurens hozzáfűzés (szálak, folyamatok), újrafuttató szkriptek idézése."""
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ma_gui import activity, store  # noqa: E402

TRICKY_ARGS = ["sima", "szóköz van benne", "", "idéző\"jel", "dollár $HOME `x`", "a'b", "50%", "x&y|z",
               "^caret", "(zárójel)", "C:\\út\\vég\\", "árvíztűrő tükörfúrógép", "--exclude", "rob=high", "a;b,c",
               "végén\\\\", "!bang!", "<be>", "tab\tbenne"]


class FixedClock(object):
    def __init__(self):
        self.t = datetime.datetime(2026, 10, 4, 21, 12, 0, tzinfo=datetime.timezone.utc)

    def __call__(self):
        self.t += datetime.timedelta(seconds=1)
        return self.t


class _LogCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ma_gui_activity_")
        self.root = Path(self.tmp) / "projekt"
        (self.root / "03_adatok").mkdir(parents=True)
        self.csv = self.root / "03_adatok" / "o1.csv"
        self.csv.write_bytes("study;e1;n1\nTitkosérték 1999;17;4242\n".encode("utf-8"))
        self.log = activity.ActivityLog(self.root, clock=FixedClock())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fill(self, n=4):
        for i in range(n):
            self.log.append("analyze.commit", "user:SzK",
                            argv=["ma.py", "analyze", "--data", "03_adatok/o1.csv", "--out", "05_elemzes/o1/run%d" % i],
                            inputs=["03_adatok/o1.csv"], outputs=[("05_elemzes/o1/run%d/results.json" % i, "a" * 64)],
                            result={"exit_code": 0, "summary": "k=13"})

    def lines(self):
        return self.log.path.read_bytes().split(b"\n")[:-1]

    def write_lines(self, lines):
        self.log.path.write_bytes(b"\n".join(lines) + b"\n")


class ChainTests(_LogCase):
    def test_record_shape_and_chain(self):
        self.fill(3)
        recs = self.log.read()
        self.assertEqual([r["seq"] for r in recs], [1, 2, 3])
        r1 = recs[0]
        self.assertEqual(list(r1)[:5], ["schema", "seq", "ts", "actor", "action"])
        self.assertEqual(r1["schema"], "szk.ma.activity/v1")
        self.assertEqual(r1["ts"], "2026-10-04T21:12:01Z")
        self.assertIsNone(r1["prev_hash"])
        self.assertEqual(r1["inputs"], [{"path": "03_adatok/o1.csv",
                                         "sha256": hashlib.sha256(self.csv.read_bytes()).hexdigest()}])
        self.assertEqual(r1["outputs"], [{"path": "05_elemzes/o1/run0/results.json", "sha256": "a" * 64}])
        for prev, rec in zip(recs, recs[1:]):
            self.assertEqual(rec["prev_hash"], prev["hash"])
        body = {k: v for k, v in recs[1].items() if k != "hash"}
        want = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode("utf-8")).hexdigest()
        self.assertEqual(recs[1]["hash"], want)
        self.assertEqual(self.log.verify(), (True, None, "A lánc ép: 3 bejegyzés."))
        self.assertEqual(self.log.head(), {"seq": 3, "hash": recs[2]["hash"]})
        self.assertEqual(os.path.realpath(str(self.log.path)),
                         os.path.realpath(str(self.root / "07_ellenorzes" / "activity.jsonl")))

    def test_empty_and_missing(self):
        ok, bad, _ = activity.verify_chain(self.log.path)
        self.assertEqual((ok, bad), (True, None))
        self.assertIsNone(self.log.head())
        self.assertFalse(activity.verify_chain(self.log.path, anchor={"seq": 1, "hash": "a" * 64})[0])

    def test_absolute_paths_relativized(self):
        rec = self.log.append("table.save", "user:SzK", outputs=[str(self.csv)], inputs=[{"path": "nincs.csv"}])
        self.assertEqual(rec["outputs"][0]["path"], "03_adatok/o1.csv")
        self.assertIsNotNone(rec["outputs"][0]["sha256"])
        self.assertEqual(rec["inputs"], [{"path": "nincs.csv", "sha256": None}])

    def test_tamper_edit(self):
        self.fill(4)
        lines = self.lines()
        rec = json.loads(lines[1])
        rec["actor"] = "user:valaki"
        lines[1] = json.dumps(rec, ensure_ascii=False).encode("utf-8")
        self.write_lines(lines)
        ok, bad, msg = self.log.verify()
        self.assertEqual((ok, bad), (False, 2))
        self.assertIn("hash", msg)

    def test_tamper_edit_with_rehash(self):
        self.fill(4)
        lines = self.lines()
        rec = json.loads(lines[1])
        rec["result"]["summary"] = "k=12"
        rec["hash"] = activity.record_hash(rec)
        lines[1] = json.dumps(rec, ensure_ascii=False).encode("utf-8")
        self.write_lines(lines)
        self.assertEqual(self.log.verify()[:2], (False, 3))

    def test_tamper_delete_middle_and_first(self):
        self.fill(4)
        lines = self.lines()
        self.write_lines(lines[:1] + lines[2:])
        self.assertEqual(self.log.verify()[:2], (False, 2))
        self.write_lines(lines[1:])
        self.assertEqual(self.log.verify()[:2], (False, 1))

    def test_tamper_reorder(self):
        self.fill(4)
        lines = self.lines()
        lines[1], lines[2] = lines[2], lines[1]
        self.write_lines(lines)
        ok, bad, msg = self.log.verify()
        self.assertEqual((ok, bad), (False, 2))

    def test_tail_truncation_needs_anchor(self):
        self.fill(4)
        head = self.log.head()
        self.write_lines(self.lines()[:3])
        self.assertTrue(self.log.verify()[0])                   # a lánc maga ép…
        ok, bad, _ = self.log.verify(anchor=head)               # …de a rögzített fej hiányzik
        self.assertEqual((ok, bad), (False, 4))

    def test_partial_line_then_append(self):
        self.fill(2)
        with open(str(self.log.path), "ab") as fh:
            fh.write(b'{"schema":"szk.ma.activity/v1","seq":3,"ts":')
        ok, bad, msg = self.log.verify()
        self.assertEqual((ok, bad), (False, 3))
        self.assertIn("csonka", msg)
        rec = self.log.append("table.save", "user:SzK")
        self.assertEqual(rec["seq"], 3)
        self.assertEqual(rec["prev_hash"], self.log.read()[1]["hash"])
        self.assertEqual(len(self.lines()), 4)
        self.assertEqual(self.log.verify()[:2], (False, 3))     # a csonka sor nyoma megmarad
        self.assertEqual([r["seq"] for r in self.log.read()], [1, 2, 3])

    def test_external_edit_record(self):
        rec = self.log.external_edit("03_adatok/o1.csv", "b" * 64)
        self.assertEqual((rec["actor"], rec["action"]), ("external", "file.external_edit"))
        self.assertEqual(rec["outputs"], [{"path": "03_adatok/o1.csv", "sha256": "b" * 64}])


class PrivacyTests(_LogCase):
    def test_forbidden_keys_rejected(self):
        cases = [dict(result={"value": 1}), dict(details={"x": [{"Cells": []}]}), dict(details={"rows": 3}),
                 dict(result={"a": {"b": {"cell": "x"}}}), dict(details={"value_as_entered": "4"}),
                 dict(details={"VALUES": [1]})]
        for kw in cases:
            with self.subTest(kw=kw):
                with self.assertRaises(activity.ActivityError) as cm:
                    self.log.append("table.save", "user:SzK", **kw)
                self.assertIn("cellaérték", str(cm.exception))
        self.assertFalse(self.log.path.exists())

    def test_invalid_fields(self):
        for kw in (dict(action="rossz akció", actor="u"), dict(action="ok", actor=""),
                   dict(action="ok", actor="a\nb"), dict(action="ok", actor="u", argv="ma.py analyze"),
                   dict(action="ok", actor="u", argv=["a", 1]), dict(action="ok", actor="u", result=[1]),
                   dict(action="ok", actor="u", result={"x": float("nan")}),
                   dict(action="ok", actor="u", outputs=[("a.json", "nem-hash")])):
            with self.subTest(kw=kw):
                with self.assertRaises(activity.ActivityError):
                    self.log.append(**kw)

    def test_no_cell_values_in_log(self):
        self.fill(2)
        text = self.log.path.read_text(encoding="utf-8")
        for secret in ("Titkosérték", "4242", "17;"):
            self.assertNotIn(secret, text)

    def test_tampered_forbidden_key_detected(self):
        self.fill(2)
        lines = self.lines()
        rec = json.loads(lines[1])
        rec["details"] = {"cells": ["4242"]}
        rec["hash"] = activity.record_hash(rec)
        lines[1] = json.dumps(rec).encode("utf-8")
        self.write_lines(lines)
        ok, bad, msg = self.log.verify()
        self.assertEqual((ok, bad), (False, 2))
        self.assertIn("tiltott", msg)


_CHILD = r"""
import sys
sys.path.insert(0, sys.argv[1])
from ma_gui import activity
log = activity.ActivityLog(sys.argv[2])
for i in range(int(sys.argv[3])):
    log.append("proc.test", "agent:p%s" % sys.argv[4], argv=["ma.py", "x", str(i)])
"""


class ConcurrencyTests(_LogCase):
    def test_threads(self):
        log = activity.ActivityLog(self.root)
        errors = []

        def worker(k):
            try:
                for i in range(20):
                    log.append("thread.test", "agent:t%d" % k, argv=["ma.py", str(i)])
            except Exception as exc:          # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(log.verify(), (True, None, "A lánc ép: 160 bejegyzés."))

    def test_processes(self):
        procs = [subprocess.Popen([sys.executable, "-c", _CHILD, ROOT, str(self.root), "15", str(k)])
                 for k in range(3)]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        ok, bad, msg = activity.verify_chain(self.root / "07_ellenorzes" / "activity.jsonl")
        self.assertTrue(ok, msg)
        self.assertEqual(msg, "A lánc ép: 45 bejegyzés.")


def _cmd_unescape(line):
    """cmd.exe 2. fázisa egy batch-sorra (egyszerűsítve): %% → %, idézőjelen kívül ^x → x."""
    line = line.replace("%%", "%")
    out, inq, i = [], False, 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            inq = not inq
        elif ch == "^" and not inq and i + 1 < len(line):
            i += 1
            ch = line[i]
        elif not inq and ch in "&|<>":
            raise AssertionError("idézőjelen kívüli, nem escape-elt cmd-metakarakter")
        out.append(ch)
        i += 1
    return "".join(out)


def _ms_argv(cmdline):
    """CommandLineToArgvW-szabályok (2008 utáni CRT, a munkapad által előállított alakokra)."""
    args, cur, inq, have, i = [], [], False, False, 0
    while i < len(cmdline):
        ch = cmdline[i]
        if ch == "\\":
            j = i
            while j < len(cmdline) and cmdline[j] == "\\":
                j += 1
            n = j - i
            if j < len(cmdline) and cmdline[j] == '"':
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    i = j + 1
                else:
                    i = j
                have = True
                continue
            cur.append("\\" * n)
            i = j
            have = True
            continue
        if ch == '"':
            inq = not inq
            have = True
        elif ch in " \t" and not inq:
            if have:
                args.append("".join(cur))
                cur, have = [], False
        else:
            cur.append(ch)
            have = True
        i += 1
    if have:
        args.append("".join(cur))
    return args


class RerunTests(_LogCase):
    def records(self):
        self.log.append("analyze.commit", "user:SzK", argv=["ma.py", "analyze", "--spec", "05_elemzes/specs/o1.json"])
        self.log.append("table.save", "user:SzK")                     # argv nélkül: kimarad
        self.log.append("tricky", "user:SzK", argv=["/abs/út/ma.py"] + TRICKY_ARGS)
        self.log.append("plugin", "user:SzK", argv=["python3", "ma.py", "es", "--data", "a b.csv"])
        return self.log.read()

    def test_cmd_quote_cases(self):
        q = activity.cmd_quote
        self.assertEqual(q("sima"), "sima")
        self.assertEqual(q("a b"), '"a b"')
        self.assertEqual(q(""), '""')
        self.assertEqual(q("50%"), '"50%%"')
        self.assertEqual(q("x&y"), '"x&y"')
        self.assertEqual(q('a"&b'), '"a\\"^&b"')
        self.assertEqual(q("C:\\út vég\\"), '"C:\\út vég\\\\"')
        with self.assertRaises(ValueError):
            q("sor\ntörés")
        for arg in TRICKY_ARGS:
            with self.subTest(arg=arg):
                self.assertEqual(_ms_argv(_cmd_unescape(q(arg))), [arg])

    def test_cmd_script(self):
        recs = self.records()
        text = activity.render_rerun_cmd(recs, "..")
        self.assertTrue(text.endswith("\r\n"))
        self.assertNotIn("\n", text.replace("\r\n", ""))
        self.assertEqual(text, activity.render_rerun_cmd(recs, ".."))   # determinisztikus
        cmds = [ln for ln in text.split("\r\n") if ln.startswith("%MA_PYTHON%")]
        self.assertEqual(len(cmds), 3)
        self.assertTrue(cmds[0].startswith('%MA_PYTHON% "%MA_PY%" analyze --spec'))
        tail = cmds[1][len('%MA_PYTHON% "%MA_PY%" '):]
        self.assertEqual(_ms_argv(_cmd_unescape(tail)), TRICKY_ARGS)
        self.assertIn('cd /d "%~dp0.."', text)
        self.assertEqual(text.count("if errorlevel 1 exit /b 1"), 3)
        bad = activity.render_rerun_cmd([{"seq": 9, "action": "x", "argv": ["ma.py", "a\nb"]}])
        self.assertIn("exit /b 1", bad)

    def test_sh_script_roundtrip(self):
        if os.name != "posix" or not shutil.which("sh"):
            self.skipTest("POSIX sh kell")
        recs = self.records()
        sh_path, cmd_path = activity.write_rerun_scripts(self.root, recs)
        self.assertEqual(os.path.realpath(str(sh_path)), os.path.realpath(str(self.root / "07_ellenorzes" / "rerun.sh")))
        self.assertTrue(cmd_path.exists())
        self.assertTrue(os.access(str(sh_path), os.X_OK))
        stub = Path(self.tmp) / "stub_ma.py"
        out = Path(self.tmp) / "argv.jsonl"
        stub.write_text("import json, os, sys\nwith open(%r, 'a', encoding='utf-8') as fh:\n"
                        "    fh.write(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:]}) + '\\n')\n" % str(out),
                        encoding="utf-8")
        env = dict(os.environ, MA_PYTHON=sys.executable, MA_PY=str(stub))
        subprocess.run(["sh", str(sh_path)], check=True, env=env, cwd=self.tmp, timeout=60)
        got = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([g["argv"] for g in got], [["analyze", "--spec", "05_elemzes/specs/o1.json"], TRICKY_ARGS,
                                                    ["es", "--data", "a b.csv"]])
        self.assertTrue(all(os.path.realpath(g["cwd"]) == os.path.realpath(str(self.root)) for g in got))
        again = activity.write_rerun_scripts(self.root, recs)
        self.assertEqual(again[0].read_bytes(), sh_path.read_bytes())

    def test_out_dir_must_be_inside_project(self):
        with self.assertRaises(store.Forbidden):
            activity.write_rerun_scripts(self.root, [], out_dir=Path(self.tmp))
        sh, cmd = activity.write_rerun_scripts(self.root, [], out_dir=self.root / "07_ellenorzes" / "audit" / "2026-10-04")
        self.assertIn('cd "$(dirname "$0")/../../.."', sh.read_text(encoding="utf-8"))
        self.assertIn('cd /d "%~dp0..\\..\\.."', cmd.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
