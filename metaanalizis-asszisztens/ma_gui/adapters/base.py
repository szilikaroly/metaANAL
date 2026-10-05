# -*- coding: utf-8 -*-
"""Plugin-adapterek közös alapja (terv: 3.1 adapters/*, 3.4 hibakódok, 5.0, 7.3).

Egységes protokoll: ``Adapter.detect() → Capability`` (a caps-nyilvántartásból) és
``Adapter.run(argv, stdin_json, timeout) → dict`` (boríték: ``{ok: true, data, …}`` vagy
``{ok: false, error: {code, http, message, details}}``).

- Csak argv-lista, ``shell=False``; a szkript útja rögzített (a felderítés adja), a felhasználói
  érték csak ``validate_option_value``-n átment opcióértékként kerülhet az argv-be.
- ``_run``: minimális alfolyamat-futtató a 7.3 szerint — szűrt környezet (PYTHONPATH/PYTHONHOME
  nem öröklődik, PYTHONUTF8=1, PYTHONIOENCODING=utf-8), Windows-on CREATE_NO_WINDOW, POSIX-on
  saját folyamatcsoport; időkorlát vagy kimeneti túlcsordulás esetén a folyamatfa leáll.
- Nem nulla kilépési kód vagy parszolhatatlan kimenet → PLUGIN_FAILED (502) a stderr utolsó
  2 KB-jával; időtúllépés → TIMEOUT (504); hiányzó/nem használható plugin → CAPABILITY_MISSING (424).

Ítéletet, rajzot, számítást nem végez. Argv-t, stdin-t, kimenetet nem naplóz, és hibaüzenetbe
sem tesz felhasználói értéket (T10).
"""
import copy
import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

MAX_STDOUT = 8 * 1024 * 1024            # 7.3: stdout ≤ 8 MB
MAX_STDERR = 64 * 1024
STDERR_TAIL = 2048                      # 3.4: a stderr utolsó 2 KB-ja a részletekben
DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 3600.0
MAX_OPTION_LEN = 1024
DROP_ENV = ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__")

STATES = ("absent", "unusable", "legacy", "ok")
USABLE_STATES = ("ok", "legacy")

ERROR_HTTP = {
    "BAD_REQUEST": 400,
    "CAPABILITY_MISSING": 424,
    "PLUGIN_FAILED": 502,
    "TIMEOUT": 504,
}

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_NEG_NUMBER_RE = re.compile(r"-[0-9][0-9.,eE+\-]*")
_FLAG_RE = re.compile(r"--?[A-Za-z][A-Za-z0-9_\-]*")


# ---------------------------------------------------------------- képesség-rekord

_CAPABILITY_DEFAULTS = {
    "id": None,               # = plugin (a felület components[].id-ja)
    "plugin": None,
    "label": None,
    "state": "absent",        # absent | unusable | legacy | ok
    "mode": None,             # ok: "json"; legacy: "bridge"
    "version": None,
    "handshake": "not_run",   # ok | missing | invalid | failed | timeout | not_run
    "home": None,             # abszolút út (csak a képesség-leírásban, 4.0)
    "source": None,           # honnan jött a plugin-mappa (env:…, caps.json, cache …)
    "script": None,           # a kézfogás szkriptje
    "scripts": {},            # szkriptnév → abszolút út
    "python": None,           # interpreter argv-előtag, pl. ["/usr/bin/python3"] vagy ["py", "-3"]
    "python_source": None,
    "python_version": None,
    "capabilities": None,     # a plugin szk.capabilities/v1 dokumentuma (ha volt)
    "commands": [],           # [{name, available, mode, needs, in, out}]
    "features": [],           # az 5.2 funkciói, amelyek ezzel a pluginnal elérhetők
    "contracts": [],          # [{name, version, dir, sha256, local, ok}]
    "drift": [],              # eltérő szerződésnevek
    "guards": [],             # bekapcsolt H-őrök (5.0)
    "known_issues": [],
    "missing_modules": [],
    "problems": [],           # [{code, level, hu, en}]
    "todo": None,             # {hu, en} — a teendő
    "note": None,             # {hu, en}
    "elapsed_ms": 0,
}


class Capability(dict):
    """Egy plugin képesség-rekordja: JSON-képes dict, attribútum-eléréssel (cap.state)."""

    def __init__(self, plugin=None, **fields):
        super().__init__(copy.deepcopy(_CAPABILITY_DEFAULTS))
        if plugin is not None:
            self["plugin"] = plugin
            self["id"] = plugin
        self.update(fields)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    @property
    def usable(self):
        return self.get("state") in USABLE_STATES


def error(code, message, details=None):
    """Hibaboríték (3.4/4.2): ``{ok: false, error: {code, http, message[, details]}}``."""
    err = {"code": code, "http": ERROR_HTTP.get(code, 500), "message": message}
    if details is not None:
        err["details"] = details
    return {"ok": False, "error": err}


# ---------------------------------------------------------------- opcióértékek

def validate_option_value(value, enum=None, regex=None, *, max_len=MAX_OPTION_LEN, allow_empty=False):
    """Felhasználói érték opcióértékként: str (vagy int), vezérlőkarakter nélkül, legfeljebb
    max_len karakter, nem kezdődhet '-'-vel (kivéve negatív számot) — így nem lehet belőle
    kapcsoló. Ha ``enum`` adott, pontosan annak egyik eleme; ha ``regex``, arra teljesen illeszkedik.
    Visszaadja a (sztring) értéket; hibánál ValueError — az üzenet az értéket nem idézi (T10)."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Az opció értéke szöveg vagy egész szám legyen.")
    text = value if isinstance(value, str) else str(value)
    if not text and not allow_empty:
        raise ValueError("Az opció értéke nem lehet üres.")
    if len(text) > max_len:
        raise ValueError("Az opció értéke túl hosszú (legfeljebb %d karakter)." % max_len)
    if _CONTROL_RE.search(text):
        raise ValueError("Az opció értéke vezérlőkaraktert tartalmaz.")
    if text.startswith("-") and not _NEG_NUMBER_RE.fullmatch(text):
        raise ValueError("Az opció értéke nem kezdődhet kötőjellel.")
    if enum is not None:
        allowed = [str(e) for e in enum]
        if text not in allowed:
            raise ValueError("Az opció értéke nem a megengedett értékek egyike.")
    if regex is not None:
        pattern = regex if hasattr(regex, "fullmatch") else re.compile(regex)
        if not pattern.fullmatch(text):
            raise ValueError("Az opció értéke nem megfelelő formátumú.")
    return text


def option(flag, value, enum=None, regex=None, **kw):
    """``[flag, érték]`` argv-darab; a kapcsolónév rögzített minta, az érték validált."""
    if not isinstance(flag, str) or not _FLAG_RE.fullmatch(flag):
        raise ValueError("Érvénytelen kapcsolónév.")
    return [flag, validate_option_value(value, enum=enum, regex=regex, **kw)]


# ---------------------------------------------------------------- alfolyamat (7.3)

def clean_env(extra=None, base=None):
    """Szűrt környezet: PYTHONPATH, PYTHONHOME, __PYVENV_LAUNCHER__ nem öröklődik;
    PYTHONUTF8=1, PYTHONIOENCODING=utf-8 (a cp1250 ellen)."""
    src = os.environ if base is None else base
    env = {}
    for key, value in src.items():
        upper = str(key).upper()
        if upper in DROP_ENV or upper in ("PYTHONUTF8", "PYTHONIOENCODING"):
            continue
        env[str(key)] = str(value)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    return env


def check_argv(argv):
    """argv: nem üres lista/tuple, csupa str, NUL nélkül. Visszaad: list."""
    if not isinstance(argv, (list, tuple)):
        raise TypeError("Az argv lista legyen (shell nem használható).")
    if not argv:
        raise ValueError("Üres argv.")
    out = []
    for item in argv:
        if isinstance(item, Path):
            item = str(item)
        if not isinstance(item, str):
            raise TypeError("Az argv minden eleme szöveg legyen.")
        if "\x00" in item:
            raise ValueError("Az argv NUL karaktert tartalmaz.")
        out.append(item)
    return out


def _arg_list(args):
    """Plugin-argumentumok: lista/tuple (üres is lehet), csupa str — sztring soha (shell-szerű hívás)."""
    if args is None:
        return []
    if not isinstance(args, (list, tuple)):
        raise TypeError("Az argv lista legyen (shell nem használható).")
    return check_argv(list(args)) if args else []


def _check_timeout(timeout):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("Érvénytelen időkorlát.")
    if not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError("Az időkorlát 0 és %d s közé essen." % int(MAX_TIMEOUT))
    return float(timeout)


def _kill_tree(proc):
    """A folyamat és leszármazottai leállítása (POSIX: folyamatcsoport; Windows: taskkill /T)."""
    if proc.poll() is not None:
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)   # a csoport maradék tagjai (unokák)
            except (OSError, AttributeError):
                pass
        return                                      # Windows: lezárt PID-re taskkill nem megy (PID-újrahasznosítás)
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
            os.killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except (OSError, AttributeError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


class _Pipe:
    """Csőolvasó szál: az elejét (stdout) vagy a végét (stderr) tartja meg, korláttal."""

    def __init__(self, stream, limit, keep_tail=False, on_overflow=None):
        self.stream = stream
        self.limit = limit
        self.keep_tail = keep_tail
        self.on_overflow = on_overflow
        self.buf = bytearray()
        self.truncated = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._read, name="ma-gui-adapter-pipe", daemon=True)

    def _read(self):
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

    def value(self):
        with self.lock:
            return bytes(self.buf)


def _feed(stream, data):
    try:
        stream.write(data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _run(argv, timeout=DEFAULT_TIMEOUT, max_out=MAX_STDOUT, *, stdin_bytes=None, cwd=None,
         extra_env=None, max_err=MAX_STDERR):
    """Alfolyamat a 7.3 szerint (argv-lista, shell=False, szűrt környezet, időkorlát,
    kimenetkorlát). Visszaad: {returncode, stdout, stderr, timed_out, truncated, elapsed_ms};
    ha a program nem indítható, OSError."""
    argv = check_argv(argv)
    timeout = _check_timeout(timeout)
    if stdin_bytes is not None and not isinstance(stdin_bytes, (bytes, bytearray)):
        raise TypeError("A stdin bájtsorozat legyen.")
    popen_kw = {
        "stdin": subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": clean_env(extra=extra_env),
        "shell": False,
        "close_fds": True,
        "cwd": os.fspath(cwd) if cwd is not None else None,
    }
    if os.name == "nt":
        popen_kw["creationflags"] = _CREATE_NO_WINDOW
    else:
        popen_kw["start_new_session"] = True        # saját folyamatcsoport: a fa együtt leállítható
    started = time.monotonic()
    proc = subprocess.Popen(argv, **popen_kw)
    killed = {"flag": False}
    kill_lock = threading.Lock()

    def kill():
        with kill_lock:
            killed["flag"] = True
            _kill_tree(proc)

    out = _Pipe(proc.stdout, max_out, on_overflow=kill)
    err = _Pipe(proc.stderr, max_err, keep_tail=True)
    out.thread.start()
    err.thread.start()
    if stdin_bytes is not None:
        threading.Thread(target=_feed, args=(proc.stdin, bytes(stdin_bytes)),
                         name="ma-gui-adapter-stdin", daemon=True).start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    for pipe in (out, err):
        pipe.thread.join(2.0)
    if out.thread.is_alive() or err.thread.is_alive():
        kill()                                      # egy unoka még nyitva tartja a csöveket
        for pipe in (out, err):
            pipe.thread.join(2.0)
    elif os.name != "nt" and not killed["flag"]:
        try:
            os.killpg(proc.pid, 0)                  # maradt-e élő tag a csoportban
        except OSError:
            pass
        else:
            _kill_tree(proc)
    return {
        "returncode": proc.returncode,
        "stdout": out.value(),
        "stderr": err.value(),
        "timed_out": timed_out,
        "truncated": out.truncated,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def stderr_tail(data, limit=STDERR_TAIL):
    """A stderr utolsó ``limit`` bájtja szövegként (UTF-8, hibás bájt cserélve)."""
    if not data:
        return ""
    return bytes(data[-limit:]).decode("utf-8", errors="replace")


def parse_json_output(data):
    """Stdout → JSON (BOM és szóköz levágva). Hibánál ValueError."""
    text = bytes(data).decode("utf-8-sig", errors="strict").strip()
    if not text:
        raise ValueError("üres kimenet")
    return json.loads(text)


# ---------------------------------------------------------------- adapter

class Adapter:
    """Plugin-adapter alaposztály. Az alosztály megadja: ``plugin`` (pl. "validator"),
    ``default_timeout`` (validator 30 s, figure-forge 300 s, presubmit 120 s — 5.1),
    ``accepted_returncodes`` (pl. figure-forge: 0 tiszta, 3 maradék QC-sértés)."""

    plugin = None
    default_timeout = DEFAULT_TIMEOUT
    accepted_returncodes = (0,)
    max_stdout = MAX_STDOUT

    def __init__(self, caps=None):
        self._caps = caps

    @property
    def caps(self):
        if self._caps is None:
            from .. import caps as _caps_mod     # késleltetett import (körkörösség ellen)
            self._caps = _caps_mod.default_caps()
        return self._caps

    def detect(self):
        """A plugin képesség-rekordja (Capability) a caps-gyorsítótárból."""
        if not self.plugin:
            raise NotImplementedError("Az adapter plugin-neve nincs megadva.")
        return self.caps.get(self.plugin)

    def available(self):
        return self.detect().get("state") in USABLE_STATES

    def command_available(self, name):
        """ok-állapotban: a kézfogás deklarálja-e (és elérhető-e) a parancsot."""
        cap = self.detect()
        if cap.get("state") != "ok":
            return False
        return any(c.get("name") == name and c.get("available") and c.get("mode") == "json"
                   for c in cap.get("commands") or [])

    def build_argv(self, args, script=None, cap=None):
        """Interpreter + rögzített szkript-út + argumentumok. A ``script`` csak a felderítésben
        talált szkriptnevek egyike lehet."""
        cap = cap if cap is not None else self.detect()
        args = _arg_list(args)
        scripts = cap.get("scripts") or {}
        path = scripts.get(script) if script else cap.get("script")
        if script and path is None:
            raise ValueError("Ismeretlen plugin-szkript.")
        python = cap.get("python")
        if not path or not python:
            raise ValueError("A plugin nem futtatható (nincs szkript vagy interpreter).")
        return check_argv(list(python) + [path] + args)

    def _missing(self, cap):
        state = cap.get("state") or "absent"
        details = {"plugin": self.plugin, "state": state, "problems": cap.get("problems") or [],
                   "todo": cap.get("todo")}
        return error("CAPABILITY_MISSING",
                     "A(z) %s plugin nem érhető el (állapot: %s)." % (self.plugin, state), details)

    def run(self, argv, stdin_json=None, timeout=None, *, script=None, parse="json", cwd=None,
            extra_env=None):
        """A plugin futtatása: interpreter + szkript + ``argv`` (lista!). ``stdin_json``: JSON-képes
        objektum a stdin-re (NaN/Infinity tilos, 4.0). ``parse``: "json" (alap) vagy "text".

        Siker: ``{ok: True, data, returncode, elapsed_ms, stderr_tail}``; hiba: hibaboríték
        (CAPABILITY_MISSING / PLUGIN_FAILED / TIMEOUT)."""
        if parse not in ("json", "text"):
            raise ValueError("A parse értéke 'json' vagy 'text'.")
        argv = _arg_list(argv)
        timeout = _check_timeout(self.default_timeout if timeout is None else timeout)
        stdin_bytes = None
        if stdin_json is not None:
            stdin_bytes = json.dumps(stdin_json, ensure_ascii=False, allow_nan=False).encode("utf-8")
        cap = self.detect()
        if cap.get("state") not in USABLE_STATES:
            return self._missing(cap)
        full = self.build_argv(argv, script=script, cap=cap)
        if cwd is None:
            tmp = getattr(self.caps, "tmp_dir", None)
            cwd = tmp() if callable(tmp) else None
        name = self.plugin
        try:
            res = _run(full, timeout, self.max_stdout, stdin_bytes=stdin_bytes, cwd=cwd, extra_env=extra_env)
        except OSError as exc:
            return error("PLUGIN_FAILED", "A(z) %s plugin nem indítható." % name,
                         {"plugin": name, "reason": type(exc).__name__})
        tail = stderr_tail(res["stderr"])
        base = {"plugin": name, "returncode": res["returncode"], "elapsed_ms": res["elapsed_ms"]}
        if res["timed_out"]:
            return error("TIMEOUT", "A(z) %s plugin %g s alatt nem futott le; a folyamat leállítva."
                         % (name, timeout), dict(base, timeout_s=timeout, stderr_tail=tail))
        if res["truncated"]:
            return error("PLUGIN_FAILED", "A(z) %s plugin kimenete túl nagy (> %d MB); a folyamat leállítva."
                         % (name, self.max_stdout // (1024 * 1024) or 1), dict(base, stderr_tail=tail))
        if res["returncode"] not in self.accepted_returncodes:
            return error("PLUGIN_FAILED", "A(z) %s plugin hibával lépett ki (kilépési kód: %s)."
                         % (name, res["returncode"]), dict(base, stderr_tail=tail))
        if parse == "text":
            data = res["stdout"].decode("utf-8", errors="replace")
        else:
            try:
                data = parse_json_output(res["stdout"])
            except ValueError:
                return error("PLUGIN_FAILED", "A(z) %s plugin kimenete nem értelmezhető JSON." % name,
                             dict(base, stderr_tail=tail))
        return {"ok": True, "data": data, "returncode": res["returncode"],
                "elapsed_ms": res["elapsed_ms"], "stderr_tail": tail}
