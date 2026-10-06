# -*- coding: utf-8 -*-
"""A munkapad futásidejű segédei (terv 2.2, 2.4 vége, 7.2, 7.3).

- Futásidejű mappa projektenként: ``<alap>/ma-gui/<projekt-hash>/`` — Windows-on
  ``%LOCALAPPDATA%\\ma-gui\\…`` (nem roaming, a OneDrive nem szinkronizálja), macOS-en
  ``~/Library/Caches/ma-gui/…``, Linuxon ``$XDG_CACHE_HOME/ma-gui/…`` (vagy ``~/.cache/ma-gui/…``);
  ``MA_GUI_RUNTIME_DIR`` felülírja az alapot. A projektmappába ebből semmi nem kerül.
- Egy projektre egy szerver: kizárólagos zár (``server.lock``; fcntl / msvcrt — a folyamat halálakor
  az OS feloldja), ``server.json`` (pid, port, projekt; token NINCS benne) és ``admin.key``
  (0600, indításonként új). A második indítás ezzel a kulccsal kér új, egyszer használható
  indítókódot a futó példánytól (``POST /api/session`` ``{"relaunch_key": …}``).
- Portválasztás: 8790, majd 8791–8799, végül az OS által adott port (0).
- Motor-önteszt: a ``tests/source_cases.py`` forrás-esetei (≈ 192 eset, ≈ 3268 ellenőrzés,
  ≈ 0,2 s) külön alfolyamatban; ha nincs ilyen gyors út, az állapot 'unknown' (nem blokkol).

Csak stdlib, statisztika nincs; cellaértéket nem lát és nem naplóz (T10).
"""
import datetime
import hashlib
import http.client
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import __version__
from . import caps as _caps

ROOT = Path(__file__).resolve().parent.parent            # a motor repója
SERVER_INFO = "server.json"
ADMIN_KEY = "admin.key"
LOCK_NAME = "server.lock"
SERVER_SCHEMA = "szk.ma.gui-server/v1"
DEFAULT_PORT = 8790
PORT_RANGE = tuple(range(8790, 8800))
SELFTEST_TIMEOUT = 120.0
RELAUNCH_TIMEOUT = 5.0
# a munkapad dokumentált indító parancsa (TELEPITES 7., README, skill) — minden felhasználói üzenet ezt mondja
# (DOC-3, UX-17); a metaanalizis-asszisztens mappában futtatandó
LAUNCH_CMD = "python ma.py gui --project <mappa>"
LAUNCH_CMD_WIN = "py -3 ma.py gui --project <mappa>"
LAUNCH_HINT = "%s (Windowson: %s)" % (LAUNCH_CMD, LAUNCH_CMD_WIN)
_MAX_INFO_BYTES = 64 * 1024

# egy sorban (Windows-on is biztonságos argv); a forrás-esetek összesítését írja ki JSON-ban
_SELFTEST_CODE = (
    "import json,sys,time;t=time.time();sys.path.insert(0,sys.argv[1]);import source_cases as s;"
    "c=s.load_cases();a=[x for x in c if x.get('status','active')=='active'];"
    "r=[e[4] is True for x in a for e in s.check_case(x)];"
    "print(json.dumps({'cases':len(c),'active':len(a),'checks':len(r),'failed':r.count(False),"
    "'elapsed_ms':int((time.time()-t)*1000)}))"
)


# ---------------------------------------------------------------------------- mappák
def runtime_base(env=None, home=None):
    """A futásidejű alapmappa (``…/ma-gui``); nem hozza létre."""
    return _caps.default_runtime_dir(env, home)


def dir_identity(path):
    """A mappa fizikai azonosítója ('<st_dev>:<st_ino>') vagy None (nem érhető el / nincs inode).

    macOS-en (APFS/HFS+ alapból kis/nagybetű-független) a realpath nem javítja a betűméretet, és egy
    mappa bind mounttal is elérhető több úton: az út szövege ezért nem azonosít egyértelműen."""
    try:
        st = os.stat(os.fspath(path))
    except (OSError, ValueError):
        return None
    if not st.st_ino:
        return None
    return "%d:%d" % (st.st_dev, st.st_ino)


def project_key(project_dir):
    """A projekt azonosítója a futásidejű mappában: a mappa fizikai azonosítójának (st_dev, st_ino;
    ha nem érhető el: a feloldott, kis/nagybetű-normalizált abszolút útnak) sha256-ja, első 16 hexa
    jegy. Így egy mappára egy szerver fut, akárhogy (más betűmérettel, más úton) adják meg."""
    real = os.path.normcase(os.path.realpath(os.fspath(project_dir)))
    ident = dir_identity(real)
    material = ("dir:" + ident) if ident else real
    return hashlib.sha256(material.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _private_dir(path):
    if os.name != "nt":
        try:
            os.chmod(str(path), 0o700)
        except OSError:
            pass


def project_runtime_dir(project_dir, base=None, env=None, home=None, create=True):
    """``<alap>/<projekt-hash>``; ``create`` esetén létrehozza (POSIX-on 0700)."""
    root = Path(base) if base is not None else runtime_base(env, home)
    path = root / project_key(project_dir)
    if create:
        path.mkdir(parents=True, exist_ok=True)
        _private_dir(path)
    return path


def _atomic_write(path, data, mode=0o600):
    path = Path(path)
    tmp = path.with_name(".%s.%s.tmp" % (path.name, secrets.token_hex(6)))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------- egy példány
class InstanceLock(object):
    """Kizárólagos, nem blokkoló fájlzár (POSIX: fcntl.flock; Windows: msvcrt.locking).

    A zárat az OS a folyamat halálakor feloldja, így a „beragadt” server.json nem akadályoz."""

    def __init__(self, path):
        self.path = Path(path)
        self._fd = None

    @property
    def held(self):
        return self._fd is not None

    def acquire(self):
        """True, ha megszereztük; False, ha más (folyamat vagy leíró) tartja."""
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self):
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(fd)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_server_info(rdir, port, project_dir, pid=None):
    """``server.json``: {schema, pid, port, project, started, gui_version} — token nélkül (2.2)."""
    info = {"schema": SERVER_SCHEMA, "pid": int(pid if pid is not None else os.getpid()), "port": int(port),
            "project": os.path.realpath(os.fspath(project_dir)), "started": _now_iso(),
            "gui_version": __version__}
    _atomic_write(Path(rdir) / SERVER_INFO, (json.dumps(info, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return info


def read_server_info(rdir):
    """A ``server.json`` tartalma, vagy None (hiányzik, sérült, érvénytelen mezők)."""
    path = Path(rdir) / SERVER_INFO
    try:
        if path.stat().st_size > _MAX_INFO_BYTES:
            return None
        info = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict):
        return None
    pid, port = info.get("pid"), info.get("port")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        return None
    return info


def remove_server_info(rdir, pid=None):
    """A ``server.json`` és az ``admin.key`` törlése (csak ha a mienk: a pid egyezik)."""
    info = read_server_info(rdir)
    if pid is not None and info is not None and info.get("pid") != pid:
        return False
    for name in (SERVER_INFO, ADMIN_KEY):
        try:
            os.unlink(str(Path(rdir) / name))
        except OSError:
            pass
    return True


def new_admin_key(rdir):
    """Indításonként új, 0600-as jogú újraindító kulcs a futásidejű mappában."""
    key = secrets.token_urlsafe(32)
    _atomic_write(Path(rdir) / ADMIN_KEY, key.encode("ascii"), 0o600)
    return key


def read_admin_key(rdir):
    try:
        raw = (Path(rdir) / ADMIN_KEY).read_bytes()
    except OSError:
        return None
    try:
        key = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    return key if 16 <= len(key) <= 128 else None


def launch_url(port, code, lang=None):
    """``http://127.0.0.1:<port>/#launch=<kód>`` (+ ``&lang=en`` az angol felülethez)."""
    url = "http://127.0.0.1:%d/#launch=%s" % (int(port), code)
    if lang and lang != "hu":
        url += "&lang=%s" % lang
    return url


def request_launch_code(port, key, timeout=RELAUNCH_TIMEOUT):
    """Új indítókód a futó példánytól (loopback, admin-kulccsal). Hibára RuntimeError."""
    body = json.dumps({"relaunch_key": key}).encode("ascii")
    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
    try:
        conn.request("POST", "/api/session", body=body,
                     headers={"Host": "127.0.0.1:%d" % int(port), "Content-Type": "application/json",
                              "Accept": "application/json"})
        resp = conn.getresponse()
        raw = resp.read(_MAX_INFO_BYTES + 1)
        status = resp.status
    except (OSError, http.client.HTTPException):
        raise RuntimeError("A futó munkapad nem válaszol a(z) %d-es porton." % int(port)) from None
    finally:
        conn.close()
    try:
        env = json.loads(raw.decode("utf-8"))
    except ValueError:
        env = None
    data = env.get("data") if isinstance(env, dict) and env.get("ok") is True else None
    code = data.get("launch_code") if isinstance(data, dict) else None
    if status != 200 or not isinstance(code, str) or not code:
        raise RuntimeError("A futó munkapad (%d-es port) nem adott új indítókódot." % int(port))
    return code


def relaunch(rdir, lang=None, timeout=RELAUNCH_TIMEOUT):
    """Ha a projekthez már fut munkapad: (url új indítókóddal, server.json); különben None.
    A futó példánytól kapott kód hibájára RuntimeError."""
    info = read_server_info(rdir)
    key = read_admin_key(rdir)
    if info is None or key is None:
        return None
    code = request_launch_code(info["port"], key, timeout)
    return launch_url(info["port"], code, lang), info


def port_candidates(port=None):
    """Kipróbálandó portok: alapból 8790, 8791–8799, majd 0 (OS); megadott portnál csak az."""
    if port is None:
        return list(PORT_RANGE) + [0]
    port = int(port)
    if not 0 <= port < 65536:
        raise ValueError("A port 0 és 65535 közötti egész szám lehet.")
    return [port]


def open_browser(url):
    """A böngésző megnyitása háttérszálon (néhány platformon a webbrowser blokkolhat)."""
    def run():
        try:
            webbrowser.open(url, new=2)
        except Exception:                                 # noqa: BLE001 — a böngésző hiánya nem hiba
            pass
    t = threading.Thread(target=run, name="ma-gui-browser", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------- motor-önteszt
def run_engine_selftest(root=None, timeout=SELFTEST_TIMEOUT, python=None):
    """A motor forrás-eseteinek gyors önteszt-futása alfolyamatban → jelvény-állapot.

    Visszaad: {state: pass | fail | error | unknown, ok, cases, active, checks, passed, failed,
    elapsed_ms, source, reason?}. Ha nincs gyors út (hiányzó tests/source_cases.py, nincs
    interpreter), 'unknown' — az indítást nem blokkolja."""
    root = Path(root) if root is not None else ROOT
    tests = root / "tests"
    base = {"state": "unknown", "ok": None, "cases": None, "active": None, "checks": None, "passed": None,
            "failed": None, "elapsed_ms": None, "source": "tests/source_cases.py"}
    python = python or sys.executable
    if not (tests / "source_cases.py").is_file():
        base["reason"] = "Nincs gyors motor-önteszt (a tests/source_cases.py hiányzik)."
        return base
    if not python:
        base["reason"] = "A Python-értelmező útja nem ismert; az önteszt nem futtatható."
        return base
    from . import jobs
    started = time.monotonic()
    try:
        res = jobs.run_subprocess([python, "-c", _SELFTEST_CODE, str(tests)], timeout=timeout,
                                  cwd=str(tests), max_stdout=64 * 1024, max_stderr=8 * 1024)
    except (OSError, ValueError):
        base["reason"] = "Az önteszt nem indítható."
        return base
    base["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    if res.get("timed_out"):
        base["reason"] = "Az önteszt időtúllépés miatt leállt (%d s)." % int(timeout)
        return base
    if res.get("returncode") != 0:
        base["state"] = "error"
        base["ok"] = False
        base["reason"] = "Az önteszt hibával leállt (kilépési kód: %s)." % res.get("returncode")
        return base
    try:
        lines = [ln for ln in res.get("stdout", b"").decode("utf-8", "replace").splitlines() if ln.strip()]
        out = json.loads(lines[-1])
        checks, failed = int(out["checks"]), int(out["failed"])
    except (ValueError, KeyError, IndexError, TypeError):
        base["state"] = "error"
        base["ok"] = False
        base["reason"] = "Az önteszt kimenete nem értelmezhető."
        return base
    base.update({"cases": out.get("cases"), "active": out.get("active"), "checks": checks,
                 "passed": checks - failed, "failed": failed, "elapsed_ms": out.get("elapsed_ms")})
    if checks == 0:
        base["reason"] = "Nincs aktív forrás-eset."
        return base
    base["state"] = "pass" if failed == 0 else "fail"
    base["ok"] = failed == 0
    return base
