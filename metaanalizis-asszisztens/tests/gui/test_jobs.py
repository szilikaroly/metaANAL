# -*- coding: utf-8 -*-
"""ma_gui.jobs: meleg worker (spawn + Pipe), eredmény-kerekút, hibaboríték, időkorlát és
újraindítás, 'legutolsó nyer' client_seq-kel, commit-sor, szálbiztosság, leállítás; alfolyamat-
higiénia (clean_env, run_subprocess: időkorlát, folyamatfa, kimenetkorlát)."""
import ast
import json
import multiprocessing
import os
import pickle
import sys
import textwrap
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ma_gui import jobs  # noqa: E402
import _jobs_targets  # noqa: E402

T = "_jobs_targets:"
BANNED = {"math", "cmath", "statistics", "random", "decimal"}


def make_manager(**kw):
    opts = dict(preload=["_jobs_targets"], allowed_modules=["_jobs_targets", "_nincs_ilyen_modul"],
                extra_sys_path=[HERE], timeout=10.0)
    opts.update(kw)
    return jobs.JobManager(**opts)


def wait_until(pred, timeout=10.0, step=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step)
    return False


def pid_alive(pid):
    """POSIX: él-e a folyamat (a zombit halottnak vesszük); Windows-on None (nem vizsgáljuk)."""
    if os.name == "nt":
        return None
    if os.path.isdir("/proc/self"):
        try:
            with open("/proc/%d/stat" % pid, encoding="ascii", errors="replace") as fh:
                state = fh.read().rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            return False
        return state not in ("Z", "X")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class StaticTests(unittest.TestCase):
    def test_only_stdlib_and_no_statistics_imports(self):
        path = os.path.join(ROOT, "ma_gui", "jobs.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split(".")[0])
        self.assertFalse(names & BANNED, names & BANNED)
        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib is not None:
            self.assertEqual(set(), names - set(stdlib))

    def test_worker_entry_is_picklable_module_level_function(self):
        self.assertEqual("_worker_main", jobs._worker_main.__qualname__)
        self.assertIs(pickle.loads(pickle.dumps(jobs._worker_main)), jobs._worker_main)

    def test_traceback_tail_is_byte_capped_and_utf8_clean(self):
        text = "eleje\n" + "ő" * 5000 + "\nVÉGE"
        tail = jobs._tail(text)
        self.assertLessEqual(len(tail.encode("utf-8")), jobs.TRACEBACK_TAIL_BYTES)
        self.assertTrue(tail.endswith("VÉGE"))
        self.assertEqual("rövid", jobs._tail("rövid"))


class ManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = make_manager(max_result_bytes=256 * 1024)

    @classmethod
    def tearDownClass(cls):
        cls.m.shutdown()

    def run_job(self, target, args=(), kwargs=None, kind=None, seq=0, mode="explore"):
        jid = self.m.submit(kind or ("k-" + target + str(time.monotonic())), T + target, args, kwargs,
                            client_seq=seq, mode=mode)
        snap = self.m.wait(jid, 20)
        self.assertIn(snap["status"], jobs.TERMINAL, snap)
        return snap

    def test_result_round_trip(self):
        snap = self.run_job("echo", [1, 2.5, "árvíztűrő", None, [True, {"a": "b"}]], {"kulcs": "érték"})
        self.assertEqual("done", snap["status"])
        self.assertEqual({"args": [1, 2.5, "árvíztűrő", None, [True, {"a": "b"}]], "kwargs": {"kulcs": "érték"}},
                         snap["result"])
        self.assertNotIn("error", snap)
        self.assertRegex(snap["job_id"], r"^j_[0-9a-f]{16}$")
        self.assertIsInstance(snap["elapsed_ms"], int)
        self.assertGreaterEqual(snap["elapsed_ms"], snap["run_ms"])
        self.assertEqual(snap, self.m.get(snap["job_id"]))

    def test_tuple_args_and_default_kwargs(self):
        snap = self.run_job("echo", (1, 2))
        self.assertEqual({"args": [1, 2], "kwargs": {}}, snap["result"])

    def test_error_capture(self):
        snap = self.run_job("raise_error", ["hibás bemenet a 3. sorban"])
        self.assertEqual("error", snap["status"])
        err = snap["error"]
        self.assertEqual({"code", "message", "traceback_tail"}, set(err))
        self.assertEqual("EXCEPTION", err["code"])
        self.assertEqual("ValueError: hibás bemenet a 3. sorban", err["message"])
        self.assertIn("raise_error", err["traceback_tail"])
        self.assertNotIn("result", snap)

    def test_long_traceback_is_tail_capped(self):
        snap = self.run_job("deep_raise", [300])
        err = snap["error"]
        self.assertEqual("EXCEPTION", err["code"])
        self.assertLessEqual(len(err["traceback_tail"].encode("utf-8")), 2048)
        self.assertTrue(err["traceback_tail"].rstrip().endswith("RuntimeError: mély hiba vége"))

    def test_long_message_is_capped(self):
        snap = self.run_job("raise_error", ["x" * 5000])
        self.assertLessEqual(len(snap["error"]["message"]), jobs.MESSAGE_MAX_CHARS)

    def test_system_exit_is_error_and_worker_survives(self):
        pid = self.run_job("get_pid")["result"]
        snap = self.run_job("call_exit", [2])
        self.assertEqual("error", snap["status"])
        self.assertEqual("EXCEPTION", snap["error"]["code"])
        self.assertIn("SystemExit", snap["error"]["message"])
        self.assertEqual(pid, self.run_job("get_pid")["result"])

    def test_import_error_and_missing_function(self):
        snap = self.m.wait(self.m.submit("imp", "_nincs_ilyen_modul:f"), 20)
        self.assertEqual("error", snap["status"])
        self.assertEqual("IMPORT_ERROR", snap["error"]["code"])
        snap = self.run_job("nincs_ilyen_fuggveny")
        self.assertEqual("BAD_TARGET", snap["error"]["code"])

    def test_non_json_result(self):
        snap = self.run_job("return_unjsonable")
        self.assertEqual("error", snap["status"])
        self.assertEqual("NOT_JSON", snap["error"]["code"])

    def test_result_size_cap(self):
        snap = self.run_job("return_big", [200 * 1024])
        self.assertEqual("done", snap["status"])
        self.assertEqual(200 * 1024, len(snap["result"]))
        snap = self.run_job("return_big", [300 * 1024])
        self.assertEqual("error", snap["status"])
        self.assertEqual("RESULT_TOO_LARGE", snap["error"]["code"])

    def test_submit_validation(self):
        bad = [
            dict(kind="k", target="os:getcwd"),                       # nem engedélyezett modul
            dict(kind="k", target="os.path:join"),
            dict(kind="k", target=T + "_private"),                    # privát függvény
            dict(kind="k", target="_jobs_targets.echo"),              # rossz alak
            dict(kind="k", target=T + "echo; rm"),
            dict(kind="k", target=T + "echo", mode="futtat"),
            dict(kind="k", target=T + "echo", client_seq=-1),
            dict(kind="k", target=T + "echo", client_seq=True),
            dict(kind="k", target=T + "echo", client_seq="3"),
            dict(kind="k", target=T + "echo", args=[object()]),
            dict(kind="k", target=T + "echo", args="nem lista"),
            dict(kind="k", target=T + "echo", kwargs={1: "a"}),
            dict(kind="k", target=T + "echo", timeout=0),
            dict(kind="", target=T + "echo"),
            dict(kind="a\nb", target=T + "echo"),
            dict(kind=5, target=T + "echo"),
        ]
        for kw in bad:
            with self.subTest(kw=kw):
                with self.assertRaises(ValueError):
                    self.m.submit(**kw)

    def test_queue_limit(self):
        with make_manager(max_queued=2) as m:
            blocker = m.submit("q", T + "sleep_then_return", [0.5], mode="commit")
            self.assertTrue(wait_until(lambda: m.get(blocker)["status"] == "running", 10))
            m.submit("q1", T + "echo", mode="commit")
            m.submit("q2", T + "echo", client_seq=1)
            with self.assertRaises(jobs.JobQueueFull):
                m.submit("q3", T + "echo", mode="commit")
            # az azonos kind-ú újabb explore a régit váltja: nem nő a sor
            newer = m.submit("q2", T + "echo", ["új"], client_seq=2)
            self.assertEqual("done", m.wait(newer, 20)["status"])

    def test_unknown_job(self):
        self.assertIsNone(self.m.get("j_0000000000000000"))
        self.assertIsNone(self.m.wait("j_0000000000000000", 0.1))

    def test_wait_timeout_returns_current_state(self):
        jid = self.m.submit("wt", T + "sleep_then_return", [0.5, "kész"], mode="commit")
        snap = self.m.wait(jid, 0.05)
        self.assertIn(snap["status"], ("queued", "running"))
        self.assertNotIn("result", snap)
        self.assertEqual("kész", self.m.wait(jid, 20)["result"])

    def test_info(self):
        self.run_job("echo")
        info = self.m.info()
        self.assertIsInstance(info["worker_pid"], int)
        self.assertTrue(info["worker_ready"])
        self.assertEqual([], info["preload_errors"])
        self.assertEqual(["_jobs_targets"], info["preload"])
        self.assertFalse(info["closed"])

    def test_concurrent_submissions_from_threads(self):
        n_threads, per_thread = 8, 6
        barrier = threading.Barrier(n_threads)
        submitted, errors = [], []
        lock = threading.Lock()

        def client(t):
            try:
                barrier.wait(5)
                for i in range(per_thread):
                    mode = "commit" if i % 2 else "explore"
                    payload = "t%d-i%d-ő" % (t, i)
                    jid = self.m.submit("cc-%d-%d" % (t, i), T + "echo", [payload], client_seq=i, mode=mode)
                    with lock:
                        submitted.append((jid, payload))
            except Exception as exc:  # pragma: no cover - a hibát a fő szál jelzi
                errors.append(exc)

        threads = [threading.Thread(target=client, args=(t,)) for t in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(30)
        self.assertEqual([], errors)
        self.assertEqual(n_threads * per_thread, len(submitted))
        self.assertEqual(len(submitted), len({jid for jid, _ in submitted}))
        for jid, payload in submitted:
            snap = self.m.wait(jid, 30)
            self.assertEqual("done", snap["status"], snap)
            self.assertEqual([payload], snap["result"]["args"])

    def test_concurrent_same_kind_last_wins(self):
        counter = iter(range(1, 10000))
        lock = threading.Lock()
        submitted = []

        def client():
            for _ in range(5):
                with lock:
                    seq = next(counter)
                    jid = self.m.submit("same-kind", T + "sleep_then_return", [0.02, seq], client_seq=seq)
                    submitted.append((seq, jid))
                time.sleep(0.005)

        threads = [threading.Thread(target=client) for _ in range(6)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(30)
        snaps = {seq: self.m.wait(jid, 30) for seq, jid in submitted}
        top = max(snaps)
        self.assertEqual("done", snaps[top]["status"])
        self.assertEqual(top, snaps[top]["result"])
        for seq, snap in snaps.items():
            self.assertIn(snap["status"], ("done", "superseded"), snap)
            if snap["status"] == "superseded":
                self.assertNotIn("result", snap)
                self.assertIn("superseded_by", snap)


class TimeoutAndCrashTests(unittest.TestCase):
    def test_timeout_terminates_and_restarts(self):
        with make_manager(timeout=0.8) as m:
            pid1 = m.wait(m.submit("t", T + "get_pid"), 20)["result"]
            t0 = time.monotonic()
            snap = m.wait(m.submit("t", T + "sleep_then_return", [30], client_seq=1), 20)
            took = time.monotonic() - t0
            self.assertEqual("timeout", snap["status"])
            self.assertEqual("TIMEOUT", snap["error"]["code"])
            self.assertNotIn("result", snap)
            self.assertLess(took, 6.0)
            self.assertGreaterEqual(snap["run_ms"], 750)
            self.assertTrue(wait_until(lambda: pid_alive(pid1) is not True, 5))
            pid2 = m.wait(m.submit("t", T + "get_pid", client_seq=2), 20)["result"]
            self.assertNotEqual(pid1, pid2)
            self.assertEqual(1, m.info()["restarts"])
            # feladatonkénti időkorlát
            snap = m.wait(m.submit("t", T + "sleep_then_return", [30], client_seq=3, timeout=0.3), 20)
            self.assertEqual("timeout", snap["status"])
            self.assertEqual("done", m.wait(m.submit("t", T + "echo", client_seq=4), 20)["status"])

    def test_worker_crash_is_reported_and_restarted(self):
        with make_manager() as m:
            pid1 = m.wait(m.submit("c", T + "get_pid"), 20)["result"]
            snap = m.wait(m.submit("c", T + "crash_hard", [3]), 20)
            self.assertEqual("error", snap["status"])
            self.assertEqual("WORKER_DIED", snap["error"]["code"])
            self.assertIn("3", snap["error"]["message"])
            pid2 = m.wait(m.submit("c", T + "get_pid"), 20)["result"]
            self.assertNotEqual(pid1, pid2)

    def test_worker_killed_while_idle_is_replaced_transparently(self):
        with make_manager() as m:
            pid1 = m.wait(m.submit("c", T + "get_pid"), 20)["result"]
            if os.name == "nt":
                self.skipTest("POSIX-jelzés")
            import signal
            os.kill(pid1, signal.SIGKILL)
            # a tétlen diszpécser magától pótolja a meghalt meleg workert
            self.assertTrue(wait_until(lambda: m.info()["worker_pid"] not in (None, pid1)
                                       and m.info()["worker_ready"], 10))
            self.assertEqual(1, m.info()["restarts"])
            snap = m.wait(m.submit("c", T + "get_pid"), 20)
            self.assertEqual("done", snap["status"])
            self.assertNotEqual(pid1, snap["result"])


class SupersedeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = make_manager(timeout=15.0)

    @classmethod
    def tearDownClass(cls):
        cls.m.shutdown()

    def wait_running(self, jid):
        self.assertTrue(wait_until(lambda: self.m.get(jid)["status"] == "running", 10))

    def test_older_running_finishes_within_grace_result_discarded(self):
        m = self.m
        pid = m.wait(m.submit("g0", T + "get_pid"), 20)["result"]
        restarts = m.info()["restarts"]
        old = m.submit("g1", T + "sleep_then_return", [0.4, "régi"], client_seq=1)
        self.wait_running(old)
        new = m.submit("g1", T + "echo", ["új"], client_seq=2)
        snap_new = m.wait(new, 20)
        snap_old = m.get(old)
        self.assertEqual("done", snap_new["status"])
        self.assertEqual(["új"], snap_new["result"]["args"])
        self.assertEqual("superseded", snap_old["status"])
        self.assertNotIn("result", snap_old)
        self.assertEqual(new, snap_old["superseded_by"])
        self.assertGreaterEqual(snap_old["run_ms"], 350)       # hagytuk befejeződni
        self.assertEqual(restarts, m.info()["restarts"])       # nem kellett újraindítani
        self.assertEqual(pid, m.wait(m.submit("g0", T + "get_pid"), 20)["result"])

    def test_older_running_killed_after_grace(self):
        m = self.m
        restarts = m.info()["restarts"]
        old = m.submit("g2", T + "sleep_then_return", [30, "régi"], client_seq=10)
        self.wait_running(old)
        t0 = time.monotonic()
        new = m.submit("g2", T + "sleep_then_return", [0, "új"], client_seq=11)
        snap_old = m.wait(old, 20)
        killed_after = time.monotonic() - t0
        snap_new = m.wait(new, 20)
        self.assertEqual("superseded", snap_old["status"])
        self.assertNotIn("result", snap_old)
        self.assertGreaterEqual(killed_after, 0.9)
        self.assertLess(killed_after, 5.0)
        self.assertEqual("done", snap_new["status"])
        self.assertEqual("új", snap_new["result"])
        self.assertEqual(restarts + 1, m.info()["restarts"])

    def test_queued_older_superseded_without_running(self):
        m = self.m
        blocker = m.submit("blk", T + "sleep_then_return", [0.4], mode="commit")
        self.wait_running(blocker)
        a = m.submit("g3", T + "echo", ["a"], client_seq=1)
        b = m.submit("g3", T + "echo", ["b"], client_seq=2)
        snap_a = m.get(a)
        self.assertEqual("superseded", snap_a["status"])
        self.assertEqual(b, snap_a["superseded_by"])
        self.assertEqual(0, snap_a["run_ms"])
        self.assertEqual("done", m.wait(b, 20)["status"])
        self.assertEqual("done", m.wait(blocker, 20)["status"])

    def test_out_of_order_arrival_is_superseded(self):
        m = self.m
        blocker = m.submit("blk", T + "sleep_then_return", [0.3], mode="commit")
        self.wait_running(blocker)
        newer = m.submit("g4", T + "echo", ["5"], client_seq=5)
        late = m.submit("g4", T + "echo", ["3"], client_seq=3)
        snap_late = m.get(late)
        self.assertEqual("superseded", snap_late["status"])
        self.assertEqual(newer, snap_late["superseded_by"])
        self.assertEqual("done", m.wait(newer, 20)["status"])
        # ha az újabb már lezárult, a régi client_seq-ű kérés is lefut (pl. lap újratöltése után)
        again = m.submit("g4", T + "echo", ["1"], client_seq=1)
        self.assertEqual("done", m.wait(again, 20)["status"])

    def test_equal_seq_and_other_kind_not_superseded(self):
        m = self.m
        a = m.submit("g5", T + "sleep_then_return", [0.2, "a"], client_seq=7)
        b = m.submit("g5", T + "echo", ["b"], client_seq=7)
        c = m.submit("g6", T + "echo", ["c"], client_seq=8)
        for jid in (a, b, c):
            self.assertEqual("done", m.wait(jid, 20)["status"])

    def test_commit_never_superseded_and_explores_queue_behind(self):
        m = self.m
        commit = m.submit("g7", T + "sleep_then_return", [1.3, "rögzítve"], client_seq=1, mode="commit")
        self.wait_running(commit)
        explore = m.submit("g7", T + "echo", ["felfedez"], client_seq=2)
        self.assertEqual("queued", m.get(explore)["status"])
        snap_commit = m.wait(commit, 20)
        self.assertEqual("done", snap_commit["status"])
        self.assertEqual("rögzítve", snap_commit["result"])
        self.assertNotIn("superseded_by", snap_commit)
        self.assertEqual("done", m.wait(explore, 20)["status"])

    def test_queued_commit_survives_newer_explore(self):
        m = self.m
        running = m.submit("g8", T + "sleep_then_return", [0.3, "e1"], client_seq=1)
        self.wait_running(running)
        commit = m.submit("g8", T + "echo", ["c"], client_seq=0, mode="commit")
        newer = m.submit("g8", T + "echo", ["e2"], client_seq=2)
        self.assertEqual("superseded", m.wait(running, 20)["status"])
        self.assertEqual("done", m.wait(commit, 20)["status"])
        self.assertEqual("done", m.wait(newer, 20)["status"])
        # a commit nem szorítja ki a régebbi explore-t
        e = m.submit("g9", T + "sleep_then_return", [0.2, "e"], client_seq=1)
        c2 = m.submit("g9", T + "echo", ["c2"], client_seq=5, mode="commit")
        self.assertEqual("done", m.wait(e, 20)["status"])
        self.assertEqual("done", m.wait(c2, 20)["status"])


class LifecycleTests(unittest.TestCase):
    def test_shutdown_leaves_no_child_processes(self):
        m = make_manager()
        pid = m.wait(m.submit("s", T + "get_pid"), 20)["result"]
        running = m.submit("s", T + "sleep_then_return", [30], mode="commit")
        queued = m.submit("s2", T + "echo", ["x"], mode="commit")
        self.assertTrue(wait_until(lambda: m.get(running)["status"] == "running", 10))
        waited = {}
        waiter = threading.Thread(target=lambda: waited.update(m.wait(running)))
        waiter.start()
        t0 = time.monotonic()
        m.shutdown()
        self.assertLess(time.monotonic() - t0, 6.0)
        waiter.join(5)
        self.assertFalse(waiter.is_alive())
        self.assertEqual("error", waited["status"])
        for jid in (running, queued):
            snap = m.get(jid)
            self.assertEqual("error", snap["status"])
            self.assertEqual("SHUTDOWN", snap["error"]["code"])
        self.assertNotIn(pid, [p.pid for p in multiprocessing.active_children()])
        self.assertFalse([p for p in multiprocessing.active_children() if p.name == "ma-gui-worker"])
        self.assertTrue(wait_until(lambda: pid_alive(pid) is not True, 5))
        info = m.info()
        self.assertTrue(info["closed"])
        self.assertIsNone(info["worker_pid"])
        self.assertFalse(m._thread.is_alive())
        with self.assertRaises(RuntimeError):
            m.submit("s", T + "echo")
        m.shutdown()                                       # idempotens

    def test_lazy_start(self):
        m = make_manager()
        m.shutdown()
        m = jobs.JobManager(False, preload=["_jobs_targets"], allowed_modules=["_jobs_targets"],
                            extra_sys_path=[HERE])
        try:
            self.assertIsNone(m.info()["worker_pid"])
            self.assertIsNone(m._thread)
            snap = m.wait(m.submit("l", T + "echo", ["x"]), 20)
            self.assertEqual("done", snap["status"])
        finally:
            m.shutdown()
        self.assertFalse([p for p in multiprocessing.active_children() if p.name == "ma-gui-worker"])

    def test_default_preload_engine_imports_in_worker(self):
        with jobs.JobManager() as m:
            self.assertEqual(["metaelemzes.pipeline"], m.info()["preload"])
            self.assertTrue(wait_until(lambda: m.info()["worker_ready"], 30))
            self.assertEqual([], m.info()["preload_errors"])
            with self.assertRaises(ValueError):
                m.submit("x", T + "echo")                  # alapból csak a motor hívható

    def test_exit_without_shutdown_leaves_no_worker(self):
        code = textwrap.dedent("""
            import sys, time
            sys.path.insert(0, %r); sys.path.insert(0, %r)
            from ma_gui import jobs
            m = jobs.JobManager(preload=["_jobs_targets"], allowed_modules=["_jobs_targets"],
                                extra_sys_path=[%r])
            print(m.wait(m.submit("a", "_jobs_targets:get_pid"), 30)["result"], flush=True)
            m.submit("a", "_jobs_targets:sleep_then_return", [60], mode="commit")
            time.sleep(0.3)
        """) % (ROOT, HERE, HERE)
        t0 = time.monotonic()
        res = jobs.run_subprocess([sys.executable, "-c", code], timeout=60)
        self.assertLess(time.monotonic() - t0, 15.0)
        self.assertEqual(0, res["returncode"], res["stderr"][-2000:])
        worker_pid = int(res["stdout"].split()[0])
        if os.name != "nt":
            self.assertTrue(wait_until(lambda: not pid_alive(worker_pid), 5))

    def test_worker_output_is_discarded(self):
        code = textwrap.dedent("""
            import sys
            sys.path.insert(0, %r); sys.path.insert(0, %r)
            from ma_gui import jobs
            m = jobs.JobManager(preload=["_jobs_targets"], allowed_modules=["_jobs_targets"],
                                extra_sys_path=[%r])
            snap = m.wait(m.submit("n", "_jobs_targets:noisy"), 30)
            m.shutdown()
            sys.stdout.write("STATUS=" + snap["status"] + "\\n")
        """) % (ROOT, HERE, HERE)
        res = jobs.run_subprocess([sys.executable, "-c", code], timeout=60)
        self.assertEqual(0, res["returncode"], res["stderr"][-2000:])
        self.assertIn(b"STATUS=done", res["stdout"])
        marker = _jobs_targets.MARKER.encode("ascii")
        self.assertNotIn(marker, res["stdout"])
        self.assertNotIn(marker, res["stderr"])


class SubprocessTests(unittest.TestCase):
    def test_clean_env(self):
        base = {"PythonPath": "/x", "PYTHONHOME": "/y", "__PYVENV_LAUNCHER__": "/z", "PATH": "/bin",
                "pythonutf8": "0", "PYTHONIOENCODING": "latin-1", "EGYEB": "érték"}
        env = jobs.clean_env(base, extra={"SZAM": 3})
        self.assertEqual({"PATH": "/bin", "EGYEB": "érték", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                          "SZAM": "3"}, env)
        self.assertEqual("/x", base["PythonPath"])          # a bemenet nem változik
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/nincs"}):
            self.assertNotIn("PYTHONPATH", jobs.clean_env())

    def test_round_trip_and_env_hygiene(self):
        code = ("import json, os, sys; data = sys.stdin.buffer.read();"
                "sys.stdout.buffer.write(json.dumps({'in': data.decode('utf-8'),"
                "'pp': os.environ.get('PYTHONPATH'), 'ph': os.environ.get('PYTHONHOME'),"
                "'u8': os.environ.get('PYTHONUTF8'), 'enc': os.environ.get('PYTHONIOENCODING'),"
                "'x': os.environ.get('MA_TESZT')}).encode('utf-8'))")
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/nincs/ilyen"}):
            res = jobs.run_subprocess([sys.executable, "-c", code], "ő bemenet".encode("utf-8"), timeout=30,
                                      extra_env={"MA_TESZT": "1"})
        self.assertEqual(0, res["returncode"], res["stderr"])
        self.assertEqual({"in": "ő bemenet", "pp": None, "ph": None, "u8": "1", "enc": "utf-8", "x": "1"},
                         json.loads(res["stdout"].decode("utf-8")))
        self.assertFalse(res["timed_out"] or res["killed"] or res["stdout_truncated"])
        self.assertIsInstance(res["elapsed_ms"], int)

    def test_nonzero_exit_and_stderr(self):
        res = jobs.run_subprocess([sys.executable, "-c", "import sys; sys.stderr.write('baj'); sys.exit(3)"],
                                  timeout=30)
        self.assertEqual(3, res["returncode"])
        self.assertEqual(b"baj", res["stderr"])
        self.assertEqual(b"", res["stdout"])

    def test_timeout_kills_process_tree(self):
        code = ("import subprocess, sys, time\n"
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "print(p.pid, flush=True)\n"
                "time.sleep(60)\n")
        t0 = time.monotonic()
        res = jobs.run_subprocess([sys.executable, "-c", code], timeout=1.5)
        took = time.monotonic() - t0
        self.assertTrue(res["timed_out"])
        self.assertTrue(res["killed"])
        self.assertNotEqual(0, res["returncode"])
        self.assertLess(took, 8.0)
        grandchild = int(res["stdout"].split()[0])
        if os.name != "nt":
            self.assertTrue(wait_until(lambda: not pid_alive(grandchild), 5), "az unoka-folyamat életben maradt")

    def test_stdout_cap_kills_and_truncates(self):
        code = "import sys\nwhile True:\n    sys.stdout.buffer.write(b'x' * 65536)\n"
        t0 = time.monotonic()
        res = jobs.run_subprocess([sys.executable, "-c", code], timeout=20, max_stdout=100000)
        self.assertLess(time.monotonic() - t0, 8.0)
        self.assertTrue(res["stdout_truncated"])
        self.assertTrue(res["killed"])
        self.assertFalse(res["timed_out"])
        self.assertEqual(100000, len(res["stdout"]))

    def test_stderr_keeps_tail(self):
        code = "import sys\nsys.stderr.write('a' * 300000)\nsys.stderr.write('VEGE')\n"
        res = jobs.run_subprocess([sys.executable, "-c", code], timeout=30, max_stderr=10000)
        self.assertEqual(0, res["returncode"])
        self.assertTrue(res["stderr_truncated"])
        self.assertEqual(10000, len(res["stderr"]))
        self.assertTrue(res["stderr"].endswith(b"VEGE"))

    def test_argument_validation(self):
        with self.assertRaises(TypeError):
            jobs.run_subprocess("python -c 1")
        with self.assertRaises(ValueError):
            jobs.run_subprocess([])
        with self.assertRaises(TypeError):
            jobs.run_subprocess([sys.executable, 1])
        with self.assertRaises(TypeError):
            jobs.run_subprocess([sys.executable, "-c", "pass"], stdin_bytes="szöveg")
        with self.assertRaises(ValueError):
            jobs.run_subprocess([sys.executable, "-c", "pass"], timeout=-1)
        with self.assertRaises(OSError):
            jobs.run_subprocess([os.path.join(HERE, "nincs_ilyen_program.exe")])


if __name__ == "__main__":
    unittest.main()
