# -*- coding: utf-8 -*-
"""Meleg worker-folyamat az elemzésekhez és alfolyamat-higiénia (terv: 2.2, 2.6, 3.1, 7.3).

JobManager — egyetlen, előre bemelegített worker-folyamat (multiprocessing 'spawn' kontextus +
Pipe; nem ProcessPoolExecutor, mert annak futó feladata nem szakítható meg). A worker induláskor
beimportálja a preload-modulokat (alapból metaelemzes.pipeline), majd ('modul:függvény', args,
kwargs) hívásokat hajt végre. Az args/kwargs és az eredmény JSON-képes; a csövön JSON-szöveg megy,
függvény vagy lambda soha.

- Állapotok: queued, running, done, error, superseded, timeout. Hiba: {code, message,
  traceback_tail} (a traceback vége, legfeljebb 2 KB UTF-8).
- Időkorlát (alapból 30 s): terminate() + új worker, az állapot 'timeout'.
- Legutolsó nyer: azonos kind-ú explore-kérések közül a nagyobb client_seq nyer. Az újabb kérés
  a régebbi várakozót azonnal kiszorítja; a futót 1 s-ig hagyja befejeződni (eredményét eldobja),
  utána leállítja és a workert újraindítja. Ha egy újabb, még élő kérés már van, a későn érkező
  régebbi kérés eleve 'superseded'. A kind ajánlottan a munkamenetet/lapot is azonosítja.
- commit: soha nem szorul ki és nem szorít ki; a kérések egy FIFO-sorban várnak.
- Csak engedélyezett modulok hívhatók (allowed_modules, alapból 'metaelemzes'); '_'-kezdetű
  függvény nem.
- Spawn-szabály: a fő szkriptnek `if __name__ == "__main__":` őre legyen (a ma.py-nak van); stdin-ről
  futtatott fő szkriptből a worker nem indul (WORKER_START_FAILED).

clean_env / run_subprocess — pluginadapterekhez: argv-lista, shell=False, Windows-on
CREATE_NO_WINDOW; PYTHONPATH/PYTHONHOME nem öröklődik, PYTHONUTF8=1, PYTHONIOENCODING=utf-8;
időkorlát és stdout-korlát (8 MB) túllépésekor a teljes folyamatfa leáll.

Statisztikát nem számol; a számok kizárólag a motorból jönnek. Argumentumot, eredményt vagy
cellaértéket nem naplóz (T10); a worker stdout/stderr kimenete eldobódik.
"""
import atexit
import collections
import importlib
import json
import multiprocessing
import multiprocessing.util  # noqa: F401  (atexit-sorrend: a mi leállítónk fusson előbb)
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import traceback
import weakref
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent        # a motor repója (metaelemzes/ szülője)

DEFAULT_PRELOAD = ("metaelemzes.pipeline",)
DEFAULT_ALLOWED_MODULES = ("metaelemzes",)
DEFAULT_TIMEOUT = 30.0
SUPERSEDE_GRACE = 1.0
START_TIMEOUT = 60.0                     # spawn + preload; lassú gépen (víruskereső) is elég
MAX_JOB_TIMEOUT = 24 * 3600.0
MAX_RESULT_BYTES = 32 * 1024 * 1024
TRACEBACK_TAIL_BYTES = 2048
MESSAGE_MAX_CHARS = 1000
MAX_QUEUED = 64
KEEP_FINISHED = 64
FINISHED_TTL = 15 * 60.0

MAX_STDOUT = 8 * 1024 * 1024
MAX_STDERR = 1024 * 1024
DROP_ENV = ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__")

MODES = ("explore", "commit")
STATUSES = ("queued", "running", "done", "error", "superseded", "timeout")
TERMINAL = frozenset(("done", "error", "superseded", "timeout"))

_TARGET_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*):([A-Za-z][A-Za-z0-9_]*)$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_POLL_SLICE = 0.05
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_MANAGERS = weakref.WeakSet()            # kilépéskor leállítandó kezelők


class JobQueueFull(RuntimeError):
    """Túl sok várakozó feladat (T11)."""


# ---------------------------------------------------------------- közös segédek

def _ms(seconds):
    return int(seconds * 1000) if seconds > 0 else 0


def _cap_message(text):
    text = str(text)
    if len(text) > MESSAGE_MAX_CHARS:
        text = text[:MESSAGE_MAX_CHARS - 1] + "…"
    return text


def _tail(text, limit=TRACEBACK_TAIL_BYTES):
    """A szöveg vége legfeljebb `limit` UTF-8 bájton (csonka karakter nélkül)."""
    raw = (text or "").encode("utf-8", "replace")
    if len(raw) <= limit:
        return raw.decode("utf-8", "replace")
    return raw[-limit:].decode("utf-8", "ignore")


def _exc_message(exc):
    text = str(exc)
    return _cap_message("%s: %s" % (type(exc).__name__, text) if text else type(exc).__name__)


def _error(code, message, tb=""):
    return {"code": code, "message": _cap_message(message), "traceback_tail": _tail(tb)}


def _module_allowed(module, allowed):
    return any(module == p or module.startswith(p + ".") for p in allowed)


def _dump_json(value, what):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=True)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("A(z) %s nem JSON-képes." % what) from None


# ---------------------------------------------------------------- worker-oldal (spawn: modulszint)

def _silence_output():
    """A worker kimenete eldobódik (T10: a motor esetleges kiírása se kerüljön a konzolra)."""
    try:
        sink = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        return
    sys.stdout = sink
    sys.stderr = sink
    for fd in (1, 2):
        try:
            os.dup2(sink.fileno(), fd)
        except (OSError, ValueError):
            pass


def _watch_parent():
    """Ha a szerver-folyamat megszűnik, a worker se maradjon árván."""
    try:
        parent = multiprocessing.parent_process()
    except Exception:
        parent = None
    if parent is None:
        return

    def watch():
        try:
            parent.join()
        except Exception:
            return
        os._exit(0)

    threading.Thread(target=watch, name="ma-gui-parent-watch", daemon=True).start()


def _execute(token, target, args_json, kwargs_json, allowed, max_result_bytes):
    match = _TARGET_RE.match(target if isinstance(target, str) else "")
    if not match or not _module_allowed(match.group(1), allowed):
        return ("error", token, _error("BAD_TARGET", "Nem engedélyezett hívási cél."))
    mod_name, func_name = match.groups()
    try:
        module = importlib.import_module(mod_name)
    except (Exception, SystemExit) as exc:
        return ("error", token, _error("IMPORT_ERROR", "A cél-modul nem importálható (%s): %s"
                                       % (mod_name, _exc_message(exc)), traceback.format_exc()))
    func = getattr(module, func_name, None)
    if not callable(func):
        return ("error", token, _error("BAD_TARGET", "A cél-függvény nem található vagy nem hívható: %s"
                                       % target))
    try:
        args = json.loads(args_json)
        kwargs = json.loads(kwargs_json)
        result = func(*args, **kwargs)
    except (Exception, SystemExit) as exc:
        return ("error", token, _error("EXCEPTION", _exc_message(exc), traceback.format_exc()))
    try:
        payload = json.dumps(result, ensure_ascii=False, allow_nan=True).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        return ("error", token, _error("NOT_JSON", "Az eredmény nem JSON-képes: %s" % _exc_message(exc)))
    if len(payload) > max_result_bytes:
        return ("error", token, _error("RESULT_TOO_LARGE", "Az eredmény túl nagy (%d bájt; korlát: %d bájt)."
                                       % (len(payload), max_result_bytes)))
    return ("result", token, payload)


def _worker_main(conn, preload, allowed, extra_sys_path, max_result_bytes):
    """A worker-folyamat belépési pontja (modulszintű, hogy a spawn importálni tudja)."""
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)      # Ctrl-C-t a szerver kezeli
    except (ValueError, OSError, AttributeError):
        pass
    _silence_output()
    _watch_parent()
    for entry in reversed([str(p) for p in extra_sys_path]):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    preload_errors = []
    for name in preload:
        try:
            importlib.import_module(name)
        except (Exception, SystemExit) as exc:
            preload_errors.append({"module": name, "message": _exc_message(exc)})
    try:
        conn.send(("ready", {"pid": os.getpid(), "preload_errors": preload_errors}))
    except (OSError, EOFError):
        return
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        if not isinstance(msg, tuple) or not msg:
            continue
        if msg[0] == "stop":
            return
        if msg[0] != "call" or len(msg) != 5:
            continue
        _, token, target, args_json, kwargs_json = msg
        reply = _execute(token, target, args_json, kwargs_json, allowed, max_result_bytes)
        try:
            conn.send(reply)
        except (OSError, EOFError):
            return


# ---------------------------------------------------------------- szerver-oldal

class _Worker:
    """Egy worker-folyamat és a csöve; csak a diszpécser-szál használja."""

    def __init__(self, ctx, preload, allowed, sys_path, max_result_bytes):
        self.conn, child_conn = ctx.Pipe(duplex=True)
        try:
            self.proc = ctx.Process(target=_worker_main, name="ma-gui-worker", daemon=True,
                                    args=(child_conn, list(preload), list(allowed), list(sys_path),
                                          int(max_result_bytes)))
            self.proc.start()
        except BaseException:
            self.conn.close()
            raise
        finally:
            child_conn.close()
        self.pid = self.proc.pid
        self.ready = False
        self.closed = False
        self.preload_errors = []

    def _take_ready(self, msg):
        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "ready":
            self.ready = True
            info = msg[1] if isinstance(msg[1], dict) else {}
            self.preload_errors = list(info.get("preload_errors") or [])

    def poll_ready(self):
        """Nem blokkoló kísérlet a 'ready' üzenet beolvasására; False, ha a worker halott."""
        if self.ready:
            return True
        try:
            if self.conn.poll(0):
                self._take_ready(self.conn.recv())
        except (EOFError, OSError):
            return False
        return True

    def wait_ready(self, timeout, abort):
        deadline = time.monotonic() + timeout
        while not self.ready:
            if abort():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                if self.conn.poll(min(0.2, remaining)):
                    self._take_ready(self.conn.recv())
                elif self.proc.exitcode is not None:
                    return False
            except (EOFError, OSError):
                return False
        return True

    def exitcode(self):
        if self.closed:
            return None
        self.proc.join(0.2)
        return self.proc.exitcode

    def kill(self):
        if self.closed:
            return
        try:
            if self.proc.exitcode is None:
                self.proc.terminate()
            self.proc.join(2.0)
            if self.proc.exitcode is None:
                self.proc.kill()
                self.proc.join(2.0)
        except (OSError, ValueError):
            pass
        self._close()

    def stop(self, timeout=1.0):
        """Kíméletes leállítás ('stop' üzenet), szükség esetén terminate()."""
        if self.closed:
            return
        try:
            self.conn.send(("stop",))
            self.proc.join(timeout)
        except (OSError, EOFError, ValueError):
            pass
        self.kill()

    def _close(self):
        self.closed = True
        try:
            self.conn.close()
        except OSError:
            pass
        try:
            self.proc.close()
        except (ValueError, AttributeError):
            pass


class _Job:
    __slots__ = ("id", "kind", "mode", "target", "args_json", "kwargs_json", "client_seq",
                 "timeout", "status", "submitted", "started", "finished", "result", "error",
                 "superseded_at", "superseded_by")

    def __init__(self, kind, mode, target, args_json, kwargs_json, client_seq, timeout):
        self.id = "j_" + secrets.token_hex(8)
        self.kind = kind
        self.mode = mode
        self.target = target
        self.args_json = args_json
        self.kwargs_json = kwargs_json
        self.client_seq = client_seq
        self.timeout = timeout
        self.status = "queued"
        self.submitted = time.monotonic()
        self.started = None
        self.finished = None
        self.result = None
        self.error = None
        self.superseded_at = None
        self.superseded_by = None


def _check_timeout(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= MAX_JOB_TIMEOUT:
        raise ValueError("Érvénytelen %s: pozitív szám kell (másodperc)." % what)
    return float(value)


class JobManager:
    """Szálbiztos feladatkezelő egyetlen meleg worker-folyamattal (ThreadingHTTPServer-kezelőkből).

    submit() → job_id; get() / wait() → {job_id, kind, mode, client_seq, status, elapsed_ms, run_ms,
    result?, error?, superseded_by?}; shutdown() leállítja a workert és a diszpécser-szálat.
    """

    def __init__(self, start=True, *, preload=DEFAULT_PRELOAD, allowed_modules=DEFAULT_ALLOWED_MODULES,
                 timeout=DEFAULT_TIMEOUT, supersede_grace=SUPERSEDE_GRACE, start_timeout=START_TIMEOUT,
                 extra_sys_path=(), max_result_bytes=MAX_RESULT_BYTES, max_queued=MAX_QUEUED,
                 keep_finished=KEEP_FINISHED, finished_ttl=FINISHED_TTL):
        self._preload = tuple(str(m) for m in preload)
        self._allowed = tuple(str(m) for m in allowed_modules)
        for name in self._preload + self._allowed:
            if not _TARGET_RE.match(name + ":f"):
                raise ValueError("Érvénytelen modulnév.")
        self._timeout = _check_timeout(timeout, "időkorlát")
        self._grace = float(supersede_grace)
        self._start_timeout = float(start_timeout)
        paths = [str(p) for p in extra_sys_path] + [str(ROOT)]
        self._sys_path = list(collections.OrderedDict.fromkeys(paths))
        self._max_result_bytes = int(max_result_bytes)
        self._max_queued = int(max_queued)
        self._keep_finished = int(keep_finished)
        self._ttl = float(finished_ttl)
        self._ctx = multiprocessing.get_context("spawn")
        self._cond = threading.Condition()
        self._jobs = {}
        self._queue = collections.deque()
        self._running = None
        self._worker = None
        self._restarts = 0
        self._thread = None
        self._closed = False
        self._stopping = False
        if start:
            self.start()

    # ------------------------------------------------------------ nyilvános API

    def start(self):
        """Elindítja a diszpécser-szálat, amely azonnal bemelegíti a workert (idempotens)."""
        with self._cond:
            if self._closed:
                raise RuntimeError("A feladatkezelő le van állítva.")
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="ma-gui-jobs", daemon=True)
                self._thread.start()
                _MANAGERS.add(self)

    def submit(self, kind, target, args=(), kwargs=None, client_seq=0, mode="explore", timeout=None):
        """Feladat beküldése; visszaad egy job_id-t. Hibás bemenetnél ValueError, teli sornál
        JobQueueFull, leállított kezelőnél RuntimeError."""
        if not isinstance(kind, str) or not 0 < len(kind) <= 200 or _CONTROL_RE.search(kind):
            raise ValueError("Érvénytelen feladatfajta (kind).")
        match = _TARGET_RE.match(target) if isinstance(target, str) else None
        if not match:
            raise ValueError("Érvénytelen hívási cél: 'modul:függvény' alak kell ('_' nélküli függvénynévvel).")
        if not _module_allowed(match.group(1), self._allowed):
            raise ValueError("Nem engedélyezett modul a hívási célban.")
        if mode not in MODES:
            raise ValueError("Érvénytelen mód: 'explore' vagy 'commit' lehet.")
        if isinstance(client_seq, bool) or not isinstance(client_seq, int) or client_seq < 0:
            raise ValueError("Érvénytelen client_seq: nemnegatív egész kell.")
        if not isinstance(args, (list, tuple)):
            raise ValueError("Az args lista vagy tuple lehet.")
        if kwargs is None:
            kwargs = {}
        if not isinstance(kwargs, dict) or not all(isinstance(k, str) for k in kwargs):
            raise ValueError("A kwargs szöveg kulcsú szótár lehet.")
        job_timeout = self._timeout if timeout is None else _check_timeout(timeout, "időkorlát")
        args_json = _dump_json(list(args), "args")
        kwargs_json = _dump_json(kwargs, "kwargs")
        job = _Job(kind, mode, target, args_json, kwargs_json, client_seq, job_timeout)
        with self._cond:
            if self._closed:
                raise RuntimeError("A feladatkezelő le van állítva.")
            self._prune_locked(job.submitted)
            replaced = sum(1 for j in self._queue if mode == "explore" and j.mode == "explore"
                           and j.kind == kind and j.client_seq < client_seq)
            if len(self._queue) - replaced >= self._max_queued:
                raise JobQueueFull("Túl sok várakozó feladat; próbáld újra később.")
            self._jobs[job.id] = job
            if mode == "explore" and self._apply_last_wins_locked(job):
                self._cond.notify_all()
                return job.id
            self._queue.append(job)
            if self._thread is None:
                self.start()
            self._cond.notify_all()
        return job.id

    def get(self, job_id):
        """A feladat állapota (dict), vagy None, ha nincs ilyen (vagy már törlődött)."""
        with self._cond:
            job = self._jobs.get(job_id)
            return None if job is None else self._snapshot_locked(job)

    def wait(self, job_id, timeout=None):
        """Vár, amíg a feladat lezárul (vagy lejár a timeout); a get()-tel azonos dict-et adja."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            while job.status not in TERMINAL:
                if deadline is None:
                    self._cond.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._cond.wait(remaining)
            return self._snapshot_locked(job)

    def info(self):
        """Diagnosztika (gui doctor): worker-pid, készenlét, újraindítások, preload-hibák, sor."""
        with self._cond:
            worker = self._worker
            return {
                "worker_pid": worker.pid if worker is not None and not worker.closed else None,
                "worker_ready": bool(worker is not None and not worker.closed and worker.ready),
                "restarts": self._restarts,
                "preload": list(self._preload),
                "preload_errors": list(worker.preload_errors) if worker is not None else [],
                "queued": len(self._queue),
                "running": self._running.id if self._running is not None else None,
                "closed": self._closed,
            }

    def shutdown(self, timeout=5.0):
        """Leállítás: a várakozó és a futó feladat 'error' (SHUTDOWN) lesz, a worker megszűnik.
        Idempotens."""
        with self._cond:
            self._closed = True
            self._stopping = True
            while self._queue:
                self._finish_locked(self._queue.popleft(), "error",
                                    error=_error("SHUTDOWN", "A feladatkezelő leállt; a feladat nem futott le."))
            self._cond.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        with self._cond:
            worker, self._worker = self._worker, None
        if worker is not None:
            worker.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.shutdown()
        return False

    # ------------------------------------------------------------ belső: állapot (zár alatt)

    def _apply_last_wins_locked(self, job):
        """Explore-kérés: régebbiek kiszorítása; True, ha maga a kérés elavult (nem kerül sorba)."""
        newer = [j for j in self._queue if j.mode == "explore" and j.kind == job.kind
                 and j.client_seq > job.client_seq]
        running = self._running
        if (running is not None and running.mode == "explore" and running.kind == job.kind
                and running.client_seq > job.client_seq and running.superseded_at is None):
            newer.append(running)
        if newer:
            self._finish_locked(job, "superseded", superseded_by=max(newer, key=_seq_key).id)
            return True
        for other in [j for j in self._queue if j.mode == "explore" and j.kind == job.kind
                      and j.client_seq < job.client_seq]:
            self._queue.remove(other)
            self._finish_locked(other, "superseded", superseded_by=job.id)
        if (running is not None and running.mode == "explore" and running.kind == job.kind
                and running.client_seq < job.client_seq):
            if running.superseded_at is None:
                running.superseded_at = time.monotonic()
            running.superseded_by = job.id
        return False

    def _finish_locked(self, job, status, result=None, error=None, superseded_by=None):
        if job.status in TERMINAL:
            return
        job.status = status
        job.finished = time.monotonic()
        job.result = result if status == "done" else None
        job.error = error
        if superseded_by is not None:
            job.superseded_by = superseded_by
        job.args_json = job.kwargs_json = None          # a bemenet ne maradjon a memóriában
        if self._running is job:
            self._running = None
        self._cond.notify_all()

    def _snapshot_locked(self, job):
        now = time.monotonic()
        end = job.finished if job.finished is not None else now
        snap = {
            "job_id": job.id, "kind": job.kind, "mode": job.mode, "client_seq": job.client_seq,
            "status": job.status, "elapsed_ms": _ms(end - job.submitted),
            "run_ms": _ms(end - job.started) if job.started is not None else 0,
        }
        if job.status == "done":
            snap["result"] = job.result
        if job.error is not None:
            snap["error"] = dict(job.error)
        if job.superseded_by is not None:
            snap["superseded_by"] = job.superseded_by
        return snap

    def _prune_locked(self, now):
        finished = sorted((j for j in self._jobs.values() if j.status in TERMINAL),
                          key=_finished_key)
        excess = len(finished) - self._keep_finished
        for i, job in enumerate(finished):
            if i < excess or now - job.finished > self._ttl:
                del self._jobs[job.id]

    # ------------------------------------------------------------ belső: diszpécser-szál

    def _run(self):
        try:
            self._dispatch_loop()
        finally:
            with self._cond:
                self._closed = True                      # váratlan kilépés után se fogadjon kérést
                worker, self._worker = self._worker, None
                leftovers = list(self._queue)
                self._queue.clear()
                if self._running is not None:
                    leftovers.append(self._running)
                for job in leftovers:
                    self._finish_locked(job, "error", error=_error(
                        "SHUTDOWN", "A feladatkezelő leállt; a feladat nem fejeződött be."))
            if worker is not None:
                worker.stop()

    def _dispatch_loop(self):
        self._spawn_worker()                             # meleg indítás
        while True:
            with self._cond:
                if self._stopping:
                    return
                pending = bool(self._queue)
                if not pending:
                    worker = self._worker
                    if worker is None or worker.closed:
                        self._cond.wait()
                    else:
                        self._cond.wait(1.0 if worker.ready else 0.25)
            if not pending:
                self._idle_check()
                continue
            if not self._ensure_ready():
                continue
            with self._cond:
                if self._stopping or not self._queue:
                    continue
                job = self._queue.popleft()
                job.status = "running"
                job.started = time.monotonic()
                self._running = job
                call = ("call", job.id, job.target, job.args_json, job.kwargs_json)
                self._cond.notify_all()
            try:
                self._supervise(job, call)
            except Exception as exc:                     # programhiba: a sor ne akadjon el
                self._discard_worker(restart=True)
                with self._cond:
                    self._finish_locked(job, "error", error=_error(
                        "INTERNAL", "Belső hiba a feladatkezelőben (%s)." % type(exc).__name__))

    def _idle_check(self):
        """Tétlen állapotban: a 'ready' beolvasása; a meghalt worker pótlása (ha már egyszer
        bemelegedett), különben eldobása (a következő feladat indít újat)."""
        worker = self._worker
        if worker is None or worker.closed:
            return
        if worker.poll_ready() and worker.proc.exitcode is None:
            return
        self._discard_worker(restart=worker.ready)

    def _spawn_worker(self):
        """Új worker indítása (a diszpécser-szálból); None, ha nem sikerült vagy leállás van."""
        if self._stopping:
            return None
        try:
            worker = _Worker(self._ctx, self._preload, self._allowed, self._sys_path, self._max_result_bytes)
        except Exception:
            return None
        with self._cond:
            if not self._stopping:
                self._worker = worker
                return worker
        worker.kill()
        return None

    def _discard_worker(self, restart):
        """A jelenlegi worker leállítása (terminate/kill); restart=True esetén azonnal új indul."""
        with self._cond:
            old, self._worker = self._worker, None
            if old is not None and not self._stopping:
                self._restarts += 1
        if old is not None:
            old.kill()
        if restart:
            self._spawn_worker()

    def _ensure_ready(self):
        worker = self._worker
        if worker is not None and (worker.closed or worker.proc.exitcode is not None):
            self._discard_worker(restart=False)          # tétlenül halt meg
            worker = None
        if worker is None:
            worker = self._spawn_worker()
        ok = worker is not None and worker.wait_ready(self._start_timeout, self._is_stopping)
        if ok or self._stopping:
            return ok
        if worker is None:
            detail = "a folyamat nem indítható"
        else:
            code = worker.exitcode()
            detail = "időtúllépés" if code is None else "kilépési kód: %s" % code
        self._discard_worker(restart=False)
        with self._cond:
            if self._queue:
                self._finish_locked(self._queue.popleft(), "error", error=_error(
                    "WORKER_START_FAILED", "A worker-folyamat nem indult el (%s)." % detail))
        return False

    def _is_stopping(self):
        return self._stopping

    def _supervise(self, job, call):
        worker = self._worker
        try:
            worker.conn.send(call)
        except (OSError, EOFError, ValueError):
            self._on_worker_died(job, worker)
            return
        deadline = job.started + job.timeout
        while True:
            with self._cond:
                stopping = self._stopping
                superseded_at = job.superseded_at
            if stopping:
                self._discard_worker(restart=False)
                with self._cond:
                    self._finish_locked(job, "error", error=_error(
                        "SHUTDOWN", "A feladatkezelő leállt; a feladat nem fejeződött be."))
                return
            limit = deadline if superseded_at is None else min(deadline, superseded_at + self._grace)
            now = time.monotonic()
            if now >= limit:
                self._discard_worker(restart=True)
                with self._cond:
                    if job.superseded_at is not None:
                        self._finish_locked(job, "superseded")
                    else:
                        self._finish_locked(job, "timeout", error=_error(
                            "TIMEOUT", "A feladat túllépte az időkorlátot (%g s); a worker-folyamat újraindult."
                            % job.timeout))
                return
            try:
                if worker.conn.poll(min(_POLL_SLICE, limit - now)):
                    msg = worker.conn.recv()
                elif worker.proc.exitcode is not None:
                    raise EOFError
                else:
                    continue
            except (EOFError, OSError):
                self._on_worker_died(job, worker)
                return
            if not (isinstance(msg, tuple) and len(msg) == 3 and msg[1] == job.id):
                continue                                  # elavult vagy idegen üzenet
            self._on_reply(job, msg[0], msg[2])
            return

    def _on_reply(self, job, kind, payload):
        if kind == "result":
            try:
                result, error = json.loads(payload), None
            except ValueError:
                result, error = None, _error("INTERNAL", "Az eredmény nem olvasható vissza.")
        else:
            result = None
            error = payload if isinstance(payload, dict) else _error("INTERNAL", "Ismeretlen worker-válasz.")
        with self._cond:
            if job.superseded_at is not None:
                self._finish_locked(job, "superseded")
            elif error is None:
                self._finish_locked(job, "done", result=result)
            else:
                self._finish_locked(job, "error", error=error)

    def _on_worker_died(self, job, worker):
        code = worker.exitcode() if worker is not None else None
        self._discard_worker(restart=True)
        with self._cond:
            if job.superseded_at is not None:
                self._finish_locked(job, "superseded")
            else:
                self._finish_locked(job, "error", error=_error(
                    "WORKER_DIED", "A worker-folyamat váratlanul leállt (kilépési kód: %s); újraindítva." % code))


def _seq_key(job):
    return job.client_seq


@atexit.register
def _shutdown_all():
    # a multiprocessing saját kilépési kezelője előtt fut (LIFO): ne induljon új worker leállás közben
    for manager in list(_MANAGERS):
        try:
            manager.shutdown(timeout=2.0)
        except Exception:
            pass


def _finished_key(job):
    return job.finished


# ---------------------------------------------------------------- alfolyamatok (pluginadapterek)

def clean_env(base=None, extra=None):
    """Szűrt környezet alfolyamathoz (7.3): PYTHONPATH, PYTHONHOME (és a macOS-es
    __PYVENV_LAUNCHER__) nem öröklődik; PYTHONUTF8=1, PYTHONIOENCODING=utf-8. Az `extra` a
    végén, felülírással kerül bele."""
    src = os.environ if base is None else base
    env = {}
    for key, value in src.items():
        if str(key).upper() in DROP_ENV:
            continue
        env[str(key)] = str(value)
    for key in [k for k in env if k.upper() in ("PYTHONUTF8", "PYTHONIOENCODING")]:
        del env[key]
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    return env


class _Collector:
    """Csőolvasó szál: az elejét (stdout) vagy a végét (stderr) tartja meg, korláttal."""

    def __init__(self, stream, limit, keep_tail=False, on_overflow=None):
        self.stream = stream
        self.limit = limit
        self.keep_tail = keep_tail
        self.on_overflow = on_overflow
        self.buf = bytearray()
        self.truncated = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="ma-gui-pipe", daemon=True)

    def _run(self):
        read = getattr(self.stream, "read1", self.stream.read)
        try:
            while True:
                chunk = read(65536)
                if not chunk:
                    break
                overflow = False
                with self.lock:
                    if self.keep_tail:
                        self.buf += chunk
                        if len(self.buf) > self.limit:
                            del self.buf[:len(self.buf) - self.limit]
                            self.truncated = True
                    elif not self.truncated:
                        room = self.limit - len(self.buf)
                        if len(chunk) > room:
                            self.buf += chunk[:room]
                            self.truncated = overflow = True
                        else:
                            self.buf += chunk
                if overflow and self.on_overflow is not None:
                    self.on_overflow()
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.stream.close()
            except (OSError, ValueError):
                pass

    def data(self):
        with self.lock:
            return bytes(self.buf)


def _feed_stdin(stream, data):
    try:
        if data:
            stream.write(data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _kill_process_tree(proc):
    """A folyamat és leszármazottai leállítása (POSIX: saját folyamatcsoport; Windows: taskkill /T)."""
    if os.name == "nt":
        taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        try:
            subprocess.run([taskkill, "/F", "/T", "/PID", str(proc.pid)], shell=False,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=_CREATE_NO_WINDOW, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _check_argv(argv):
    if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)):
        raise TypeError("Az argv argumentumlista legyen (shell nélkül fut).")
    if not argv:
        raise ValueError("Üres argv.")
    out = []
    for item in argv:
        if isinstance(item, os.PathLike):
            item = os.fspath(item)
        if not isinstance(item, str):
            raise TypeError("Az argv elemei szövegek vagy útvonalak legyenek.")
        out.append(item)
    return out


def run_subprocess(argv, stdin_bytes=None, timeout=DEFAULT_TIMEOUT, max_stdout=MAX_STDOUT, *,
                   cwd=None, extra_env=None, max_stderr=MAX_STDERR):
    """Alfolyamat futtatása a 7.3 szerint: shell=False, szűrt környezet (clean_env + extra_env),
    Windows-on CREATE_NO_WINDOW. Időkorlát vagy stdout-túlcsordulás esetén a teljes folyamatfa
    leáll. A stderr végéből legfeljebb max_stderr bájt marad meg.

    Visszaad: {returncode, stdout, stderr, timed_out, stdout_truncated, stderr_truncated, killed,
    elapsed_ms}; ha a program nem indítható, OSError (pl. FileNotFoundError)."""
    argv = _check_argv(argv)
    if stdin_bytes is not None and not isinstance(stdin_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("A stdin_bytes bájtsorozat legyen.")
    timeout = _check_timeout(timeout, "időkorlát")
    if isinstance(max_stdout, bool) or not isinstance(max_stdout, int) or max_stdout <= 0:
        raise ValueError("Érvénytelen max_stdout.")
    if isinstance(max_stderr, bool) or not isinstance(max_stderr, int) or max_stderr <= 0:
        raise ValueError("Érvénytelen max_stderr.")
    popen_kw = {
        "stdin": subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
        "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "env": clean_env(extra=extra_env), "shell": False, "close_fds": True,
        "cwd": os.fspath(cwd) if cwd is not None else None,
    }
    if os.name == "nt":
        popen_kw["creationflags"] = _CREATE_NO_WINDOW
    else:
        popen_kw["start_new_session"] = True              # saját folyamatcsoport: a fa együtt állítható le
    started = time.monotonic()
    proc = subprocess.Popen(argv, **popen_kw)
    state = {"killed": False}
    kill_lock = threading.Lock()

    def kill_tree():
        with kill_lock:
            state["killed"] = True
            _kill_process_tree(proc)

    out = _Collector(proc.stdout, max_stdout, on_overflow=kill_tree)
    err = _Collector(proc.stderr, max_stderr, keep_tail=True)
    out.thread.start()
    err.thread.start()
    feeder = None
    if stdin_bytes is not None:
        feeder = threading.Thread(target=_feed_stdin, args=(proc.stdin, bytes(stdin_bytes)),
                                  name="ma-gui-stdin", daemon=True)
        feeder.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    for collector in (out, err):
        collector.thread.join(1.0)
    if out.thread.is_alive() or err.thread.is_alive():
        # egy unoka-folyamat még nyitva tartja a csöveket
        kill_tree()
        for collector in (out, err):
            collector.thread.join(1.0)
    if feeder is not None:
        feeder.join(1.0)
    return {
        "returncode": proc.returncode,
        "stdout": out.data(),
        "stderr": err.data(),
        "timed_out": timed_out,
        "stdout_truncated": out.truncated,
        "stderr_truncated": err.truncated,
        "killed": state["killed"],
        "elapsed_ms": _ms(time.monotonic() - started),
    }
