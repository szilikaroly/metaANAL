# -*- coding: utf-8 -*-
"""A MA-munkapad helyi HTTP-szervere (terv 2.2, 3.1, 3.4, 7.1–7.3).

Indítás (2.2), sorrendben: motor-önteszt (háttérben, alfolyamatban) → ``kb.ensure_built``
(háttérszálon) → képesség-felderítés (háttérszálon) → adatvédelmi ellenőrzés → egyszer használható,
60 s-os indítókód; a böngésző a ``http://127.0.0.1:<port>/#launch=<kód>`` címet nyitja.

- Csak ``127.0.0.1``-en hallgat; port: 8790, 8791–8799, majd az OS-é. Windows-on
  ``SO_EXCLUSIVEADDRUSE`` (a port nem „lopható el”), máshol ``SO_REUSEADDR``.
- Egy projektre egy szerver: zárfájl + ``server.json`` (pid, port; token nélkül) a futásidejű
  mappában; a második indítás új indítókódot kér a futó példánytól (``runtime.relaunch``).
- Minden kérés a ``security.check_request`` szűrőn megy át (Host, Origin, Sec-Fetch-Site, metódus,
  token, Content-Type, méret), minden válaszon ott vannak a kötelező fejlécek és a CSP.
- Változásfigyelő (``store.ChangeWatcher``) háttérszálon; a külső írásokat ``actor: external``
  sorként naplózza (4.16). Tétlenségi leállás (alap 4 óra; a long-poll nem számít aktivitásnak),
  Ctrl-C-re tiszta leállás. A token nem kerül lemezre.

A szervernapló csak metódust, útvonal-mintát, kódot, időt és kérésazonosítót ír (T10). Statisztika
nincs; a számok a motorból jönnek.
"""
import argparse
import contextlib
import hmac
import os
import signal
import socket
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import metaelemzes

from . import __version__, activity, privacy, runtime, security, store
from . import router as _router
from .router import ApiError

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
PROJECT_JSON = "ma-projekt.json"
ACTOR = "user"
DEFAULT_IDLE_HOURS = 4.0
LONGPOLL_MAX = 25.0
PRIVACY_TTL = 30.0
KB_WAIT = 60.0
READ_CHUNK = 64 * 1024
SEND_CHUNK = 256 * 1024
DRAIN_LIMIT = 16 * 1024 * 1024
DRAIN_TIMEOUT = 5.0
_LOG_DEFAULT = object()


class AlreadyRunning(RuntimeError):
    """A projekthez már fut munkapad (a példányzár foglalt)."""

    def __init__(self, runtime_dir):
        super().__init__("A projekthez már fut egy munkapad-példány.")
        self.runtime_dir = runtime_dir


# ---------------------------------------------------------------------------- HTTP-réteg
class MunkapadHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer csak IPv4-loopbackre, gyors (DNS-mentes) kötéssel."""

    daemon_threads = True
    allow_reuse_address = os.name != "nt"
    request_queue_size = 64

    def __init__(self, address, handler, app):
        self.app = app
        super().__init__(address, handler)

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # a HTTPServer.server_bind getfqdn-je fordított DNS-t kérdezne (lassú lehet): kihagyjuk
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def handle_error(self, request, client_address):
        # nincs traceback a konzolon (T10): csak a kivétel típusa
        exc = sys.exc_info()[0]
        self.app.log("kapcsolati hiba (%s)" % (exc.__name__ if exc else "?"))


class RequestHandler(BaseHTTPRequestHandler):
    """Minden metódus egy útra fut; a szűrés, az útválasztás és a válaszfejlécek egy helyen."""

    server_version = "ma-munkapad"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout = 30

    def version_string(self):
        return "ma-munkapad"

    # a BaseHTTPRequestHandler saját naplója a nyers kéréssort (query-vel) írná: kikapcsolva
    def log_message(self, format, *args):        # noqa: A002 — a szülőosztály paraméterneve
        pass

    def log_request(self, code="-", size="-"):
        pass

    def log_error(self, format, *args):           # noqa: A002
        pass

    def send_error(self, code, message=None, explain=None):
        """A szülőosztály hibái (szintaktikailag hibás kérés, túl hosszú URI, ismeretlen metódus)
        is borítékot és biztonsági fejléceket kapnak."""
        if code == 501:
            err, msg = "METHOD_NOT_ALLOWED", "A kérés metódusa nem engedélyezett."
        elif code >= 500 and code != 505:
            err, msg = "INTERNAL", "Belső hiba."
        else:
            # 400, 408, 414, 431, 505 …: a boríték kódtáblája szerint 400
            err, msg = "BAD_REQUEST", "Hibás HTTP-kérés."
        http = security.ERROR_CODES[err]
        if getattr(self, "request_version", "HTTP/0.9") == "HTTP/0.9":
            # a kéréssor nem értelmezhető: a stdlib fejléc nélkül (HTTP/0.9-ként) válaszolna — a
            # biztonsági fejlécek így is menjenek ki
            self.request_version = "HTTP/1.0"
        self.close_connection = True
        body = _router.dumps(security.error_envelope(err, msg))
        try:
            self._send_bytes(http, [], body)
        except OSError:
            pass
        self.server.app.log("%s <hibás kérés> %d %s" % (self.command or "-", http, err))

    def _handle(self):
        app = self.server.app
        method = self.command
        target = self.path
        rej = app.security.check_request(method, target, self.headers)
        if rej is not None:
            http, code, msg = rej
            self._drain_body()
            self._send_bytes(http, [], _router.dumps(security.error_envelope(code, msg)))
            app.log("%s %s %d %s" % (method, _target_class(target), http, code))
            return
        try:
            body = self._read_body()
            req = _router.Request(app, method, target, self.headers, body)
        except ApiError as exc:
            self._send_bytes(exc.http, exc.headers, _router.dumps(security.error_envelope(exc.code, exc.message)))
            app.log("%s %s %d %s" % (method, _target_class(target), exc.http, exc.code))
            return
        route = app.router.find(method, req.path)
        if route is None or route.activity:
            app.touch()
        status, headers, out = app.router.dispatch(req)
        try:
            if isinstance(out, _router.Response):
                self._send_response(out)
            else:
                self._send_bytes(status, headers, out)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            pass

    do_GET = do_POST = do_PUT = do_HEAD = do_OPTIONS = do_DELETE = do_PATCH = do_TRACE = do_CONNECT = _handle

    # -- törzs
    def _content_length(self):
        vals = self.headers.get_all("Content-Length") or []
        if len(vals) != 1:
            return 0
        v = vals[0].strip()
        return int(v) if v.isdigit() and len(v) <= 12 else 0

    def _read_body(self):
        n = self._content_length()
        if n <= 0:
            return b""
        chunks, remaining = [], n
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, READ_CHUNK))
            if not chunk:
                raise ApiError("BAD_REQUEST", "A kéréstörzs csonka (a Content-Length-nél rövidebb).")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _drain_body(self):
        """Elutasított kérés törzsének elolvasása (különben a kliens a választ nem kapná meg)."""
        self.close_connection = True
        n = self._content_length()
        if n <= 0 or n > DRAIN_LIMIT:
            return
        try:
            self.connection.settimeout(DRAIN_TIMEOUT)
            remaining = n
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, READ_CHUNK))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass

    # -- válasz
    def _headers(self, status, kind, content_type, length, extra, nonce=None):
        self.send_response(status)
        for k, v in security.base_headers(nonce, kind):
            self.send_header(k, v)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        for k, v in extra or ():
            self.send_header(k, v)
        self.end_headers()

    def _send_bytes(self, status, extra, body, kind="api", content_type="application/json; charset=utf-8",
                    nonce=None):
        self._headers(status, kind, content_type, len(body), extra, nonce)
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_response(self, resp):
        if resp.file_path is None:
            self._send_bytes(resp.status, resp.headers, resp.body or b"", resp.kind, resp.content_type, resp.nonce)
            return
        try:
            fh = open(str(resp.file_path), "rb")
        except OSError as exc:
            # a feloldás és a megnyitás között törölték vagy zárolták: még nem ment ki fejléc
            code = "NOT_FOUND" if isinstance(exc, FileNotFoundError) else "LOCKED"
            msg = "A fájl nem található." if code == "NOT_FOUND" else _router.MSG_LOCKED_FILE
            self._send_bytes(security.ERROR_CODES[code], [], _router.dumps(security.error_envelope(code, msg)))
            return
        with fh:
            size = os.fstat(fh.fileno()).st_size
            self._headers(resp.status, resp.kind, resp.content_type, size, resp.headers, resp.nonce)
            if self.command == "HEAD":
                return
            remaining = size
            while remaining > 0:
                chunk = fh.read(min(SEND_CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def _target_class(target):
    """Naplóhoz: az útvonal osztálya (a tényleges út és a query nélkül)."""
    try:
        return "<%s>" % security.route_class(str(target).split("?", 1)[0])
    except Exception:                                    # noqa: BLE001
        return "<?>"


# ---------------------------------------------------------------------------- alkalmazás
class App(object):
    """Egy projektmappához kötött munkapad-szerver állapota és életciklusa.

    Tesztben: ``App(mappa, …).start(port=0)``, majd ``serve_forever()`` külön szálon és
    ``shutdown()``. A CLI a ``cli_main``-en át ``single_instance=True``-val indítja."""

    ACTIVITY_WARNING = "A tevékenységnaplót (07_ellenorzes/activity.jsonl) nem sikerült írni; a művelet megtörtént."

    def __init__(self, project_dir, *, lang="hu", idle_hours=DEFAULT_IDLE_HOURS, kb_db=None, caps=None,
                 privacy_home=None, privacy_env=None, privacy_platform=None, selftest=True, kb_build=True,
                 caps_refresh=True, runtime_dir=None, single_instance=False, log_stream=_LOG_DEFAULT,
                 validate_responses=False, watch_interval=1.5, longpoll_max=LONGPOLL_MAX, clock=None,
                 actor=ACTOR):
        root = Path(os.path.realpath(os.fspath(project_dir)))
        if not root.is_dir():
            raise ValueError("A projektmappa nem létezik vagy nem mappa.")
        if lang not in ("hu", "en"):
            raise ValueError("A nyelv hu vagy en lehet.")
        self.project_root = root
        self.lang = lang
        self.idle_s = float(idle_hours) * 3600.0 if idle_hours else 0.0
        self.kb_db = kb_db
        self.actor = actor
        self.privacy_kw = {"home": privacy_home, "env": privacy_env, "platform": privacy_platform}
        self.longpoll_max = float(longpoll_max)
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else None
        self.single_instance = single_instance
        self._log_stream = sys.stderr if log_stream is _LOG_DEFAULT else log_stream
        self._log_lock = threading.Lock()
        self._lock = threading.RLock()
        self.security = security.SecurityManager(clock=clock)
        self.watcher = store.ChangeWatcher(root, interval=watch_interval)
        self.store = store.ProjectStore(root, watcher=self.watcher)
        self.activity = activity.ActivityLog(root)
        self.router = _router.Router(meta_fn=self._meta, log_fn=self.log, validate_responses=validate_responses)
        from .routes import register_all
        register_all(self.router)
        self._caps = caps
        self._caps_refresh = caps_refresh
        self._selftest_enabled = selftest
        self._kb_build = kb_build
        self.selftest_state = {"state": "disabled" if not selftest else "pending", "ok": None}
        self.kb_state = {"state": "pending" if kb_build else "lazy", "ok": None}
        self._kb_thread = None
        self._privacy = None
        self._privacy_at = 0.0
        self._privacy_lock = threading.Lock()
        self._jobs = None
        self.httpd = None
        self.port = None
        self.admin_key = None
        self._instance_lock = None
        self._serving = False
        self._closed = False
        self._stopping = threading.Event()
        self._last_activity = time.monotonic()
        self._threads = []
        self._index_cache = (None, None)
        self._caps_registered = False

    # -- napló
    def log(self, line):
        """Szervernapló (csak minta, kód, idő — T10)."""
        stream = self._log_stream
        if stream is None:
            return
        with self._log_lock:
            try:
                stream.write("[ma-gui %s] %s\n" % (time.strftime("%H:%M:%S"), line))
                stream.flush()
            except (OSError, ValueError):
                pass

    def touch(self):
        self._last_activity = time.monotonic()

    def _meta(self):
        return {"engine": str(getattr(metaelemzes, "__version__", "")) or None, "project_rev": self.project_rev()}

    def project_rev(self):
        try:
            return self.watcher.rev
        except Exception:                                  # noqa: BLE001
            return None

    # -- képességek, KB, önteszt
    @property
    def caps(self):
        with self._lock:
            if self._caps is None:
                from . import caps as caps_mod
                meta, _ = self.project_meta_safe()
                plugins = meta.get("plugins") if isinstance(meta, dict) else None
                self._caps = caps_mod.Caps(project_plugins=plugins if isinstance(plugins, dict) else None,
                                           project_root=self.project_root)
            return self._caps

    def selftest_info(self):
        return dict(self.selftest_state)

    def kb_info(self):
        return dict(self.kb_state)

    def _run_selftest(self):
        self.selftest_state = {"state": "running", "ok": None}
        try:
            res = runtime.run_engine_selftest()
        except Exception as exc:                           # noqa: BLE001 — a jelvény legyen 'unknown'
            res = {"state": "unknown", "ok": None, "reason": "Az önteszt nem futott (%s)." % type(exc).__name__}
        self.selftest_state = res
        self.log("motor-önteszt: %s%s" % (res.get("state"), (" (%s/%s)" % (res.get("passed"), res.get("checks")))
                                           if res.get("checks") else ""))

    def _build_kb(self):
        from metaelemzes import kb
        self.kb_state = {"state": "building", "ok": None}
        started = time.monotonic()
        try:
            rebuilt = kb.ensure_built(self.kb_db)
            self.kb_state = {"state": "ok", "ok": True, "rebuilt": bool(rebuilt),
                             "elapsed_ms": int((time.monotonic() - started) * 1000)}
        except Exception as exc:                           # noqa: BLE001
            self.kb_state = {"state": "error", "ok": False,
                             "reason": "A tudásbázis nem építhető fel (%s)." % type(exc).__name__}
            self.log("tudásbázis-hiba (%s)" % type(exc).__name__)

    def kb_ready(self, timeout=KB_WAIT):
        """Megvárja az indításkori KB-építést; hibánál 424 CAPABILITY_MISSING."""
        t = self._kb_thread
        if t is not None:
            t.join(timeout)
            if t.is_alive():
                raise ApiError("TIMEOUT", "A tudásbázis még épül; próbáld újra néhány másodperc múlva.")
        if self.kb_state.get("state") == "error":
            raise ApiError("CAPABILITY_MISSING", self.kb_state.get("reason") or "A tudásbázis nem érhető el.")

    def get_jobs(self):
        """A meleg worker-folyamat kezelője — lustán, első használatkor indul (elemzésekhez)."""
        with self._lock:
            if self._jobs is None:
                from . import jobs
                self._jobs = jobs.JobManager()
            return self._jobs

    # -- projekt-metaadat és adatvédelem
    def project_meta(self):
        """(ma-projekt.json tartalma vagy None, etag). Sérült JSON → 422 (store.Invalid)."""
        doc, etag = self.store.load_json(PROJECT_JSON)
        if doc is not None and not isinstance(doc, dict):
            raise store.Invalid("A ma-projekt.json gyökere objektum legyen.", {"path": PROJECT_JSON})
        return doc, etag

    def project_meta_safe(self):
        try:
            return self.project_meta()
        except store.StoreError:
            return None, None

    def data_class_with_source(self):
        meta, _ = self.project_meta()
        if meta is None or meta.get("data_class") in (None, ""):
            return "A", "default"
        return privacy.normalize_data_class(meta.get("data_class")), PROJECT_JSON

    def data_class(self):
        return self.data_class_with_source()[0]

    def compute_privacy(self, data_class=None):
        dc = data_class or self.data_class()
        return privacy.status(str(self.project_root), dc, **self.privacy_kw)

    def refresh_privacy(self):
        with self._privacy_lock:
            st = self.compute_privacy()
            self._privacy, self._privacy_at = st, time.monotonic()
            return st

    def privacy_status(self):
        with self._privacy_lock:
            if self._privacy is not None and time.monotonic() - self._privacy_at < PRIVACY_TTL:
                return self._privacy
        return self.refresh_privacy()

    def require_open(self):
        """C osztályú projekt a vault gyökere alatt csak védelemmel nyitható meg (7.5)."""
        st = self.privacy_status()
        ob = st.get("open_blocked") or {}
        if ob.get("blocked"):
            raise ApiError("FORBIDDEN", "C osztályú projekt a vault gyökere alatt: az adatok csak a védelmek "
                                        "beírása után nyithatók meg (%s)." % "; ".join(ob.get("reasons") or []),
                           {"reasons": list(ob.get("reasons") or []), "actions": list(st.get("actions") or [])})

    def can_write(self, rel, phi_detected=False, consent=False):
        """privacy.can_write a projekt adatosztályával → (ok, indoklás)."""
        return privacy.can_write(str(self.project_root), rel, self.data_class(), consent=consent,
                                 phi_detected=phi_detected, **self.privacy_kw)

    def log_activity(self, action, argv=None, inputs=(), outputs=(), result=None, details=None):
        """Activity-sor (cellaérték nélkül); hibánál None és szervernapló-sor, a kérés nem bukik el."""
        try:
            return self.activity.append(action, self.actor, argv=argv, inputs=inputs, outputs=outputs,
                                        result=result, details=details)
        except Exception as exc:                           # noqa: BLE001
            self.log("activity-napló hiba (%s)" % type(exc).__name__)
            return None

    def check_admin_key(self, key):
        if not self.admin_key or not isinstance(key, str):
            return False
        try:
            return hmac.compare_digest(self.admin_key.encode("ascii"), key.encode("ascii"))
        except UnicodeEncodeError:
            return False

    def index_html(self):
        """A statikus felület bájtjai (mtime szerint gyorsítótárazva) vagy None."""
        try:
            st = INDEX_HTML.stat()
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        cached_key, data = self._index_cache
        if cached_key != key:
            try:
                data = INDEX_HTML.read_bytes()
            except OSError:
                return None
            self._index_cache = (key, data)
        return data

    # -- változások
    @contextlib.contextmanager
    def own_writes(self):
        """A motor közvetlen (store-t megkerülő) írásai alatt a változásfigyelő nem szkennel; a
        ``note(rel)``-lel jelölt fájl a saját írásunk lesz, nem „external” (pl. projekt.init sablonjai)."""
        lock = getattr(self.watcher, "_scan_lock", None)       # a ChangeWatcher-nek nincs nyilvános szünete
        noted = []

        def note(rel):
            noted.append(rel)

        with (lock if lock is not None else contextlib.nullcontext()):
            yield note
            for rel in noted:
                try:
                    sha = store.sha256_file(self.store.path(rel))
                except (OSError, store.StoreError):
                    continue
                if sha is not None:
                    self.watcher.note_write(rel, sha)

    def wait_changes(self, since, timeout):
        """Long-poll szeletekben (a leállást észrevesszük); {rev, changed, external, reset}."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            res = self.watcher.wait(since, timeout=max(0.0, min(1.0, remaining)))
            if res["changed"] or res["reset"] or remaining <= 0 or self._stopping.is_set():
                return res

    def _external_loop(self):
        since = self.watcher.rev
        while not self._stopping.is_set():
            try:
                res = self.watcher.wait(since, timeout=1.0)
            except Exception:                              # noqa: BLE001
                self._stopping.wait(1.0)
                continue
            if res["reset"]:
                since = res["rev"]
                continue
            for key in res["external"]:
                if key.startswith("ext:"):
                    continue
                try:
                    sha = store.sha256_file(self.project_root / key)
                except OSError:
                    sha = None
                try:
                    self.activity.external_edit(key, sha)
                except Exception as exc:                   # noqa: BLE001
                    self.log("activity-napló hiba (%s)" % type(exc).__name__)
            since = res["rev"]

    def _idle_loop(self):
        step = max(0.05, min(30.0, self.idle_s / 20.0))
        while not self._stopping.wait(step):
            if time.monotonic() - self._last_activity > self.idle_s:
                self.log("tétlenség miatti leállás (%.4g óra)" % (self.idle_s / 3600.0))
                self.shutdown()
                return

    def _thread(self, target, name):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)
        return t

    # -- életciklus
    def ensure_runtime_dir(self):
        if self.runtime_dir is None:
            self.runtime_dir = runtime.project_runtime_dir(self.project_root)
        else:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
        return self.runtime_dir

    def _bind(self, port):
        last = None
        for p in runtime.port_candidates(port):
            try:
                return MunkapadHTTPServer(("127.0.0.1", p), RequestHandler, self)
            except OSError as exc:
                last = exc
        raise OSError("Nem sikerült portot nyitni a 127.0.0.1 címen (%s)." % (type(last).__name__ if last else "?"))

    def start(self, port=None):
        """Indítás a 2.2 sorrendjében, a kiszolgálás nélkül (azt a serve_forever végzi)."""
        if self.single_instance:
            rdir = self.ensure_runtime_dir()
            lock = runtime.InstanceLock(rdir / runtime.LOCK_NAME)
            if not lock.acquire():
                raise AlreadyRunning(rdir)
            self._instance_lock = lock
            runtime.remove_server_info(rdir)        # a zárat mi tartjuk: a régi server.json elavult
        try:
            if self._selftest_enabled:
                self.selftest_state = {"state": "running", "ok": None}
                self._thread(self._run_selftest, "ma-gui-selftest")
            if self._kb_build:
                self._kb_thread = self._thread(self._build_kb, "ma-gui-kb")
            if self._caps_refresh:
                from . import caps as caps_mod
                caps = self.caps
                caps_mod.set_default_caps(caps)
                self._caps_registered = True
                caps.start_refresh()
            try:
                st = self.refresh_privacy()
                if (st.get("open_blocked") or {}).get("blocked"):
                    self.log("adatvédelem: C osztályú projekt védelem nélkül — az adatok zárolva")
            except Exception as exc:                       # noqa: BLE001 — a /api/privacy jelzi
                self.log("adatvédelmi ellenőrzés hiba (%s)" % type(exc).__name__)
            self.httpd = self._bind(port)
            self.port = self.httpd.server_address[1]
            self.security.port = self.port
            if self.single_instance:
                self.admin_key = runtime.new_admin_key(self.runtime_dir)
                runtime.write_server_info(self.runtime_dir, self.port, self.project_root)
            self.watcher.start()
            self._thread(self._external_loop, "ma-gui-external")
            if self.idle_s > 0:
                self._thread(self._idle_loop, "ma-gui-idle")
            self.touch()
        except BaseException:
            self.close()
            raise
        return self

    def new_launch_code(self):
        return self.security.new_launch_code()

    def launch_url(self, code=None):
        return runtime.launch_url(self.port, code or self.new_launch_code(), self.lang)

    def serve_forever(self, poll_interval=0.25):
        if self.httpd is None:
            raise RuntimeError("A szerver nincs elindítva (start).")
        self._serving = True
        try:
            self.httpd.serve_forever(poll_interval=poll_interval)
        finally:
            self._serving = False
            self.close()

    def shutdown(self):
        """Leállítás bármely szálból (a serve_forever-t futtatón kívül)."""
        self._stopping.set()
        httpd = self.httpd
        if httpd is not None and self._serving:
            threading.Thread(target=httpd.shutdown, name="ma-gui-shutdown", daemon=True).start()
        elif not self._serving:
            self.close()

    def close(self):
        """Erőforrások felszabadítása (idempotens): szerver, figyelő, worker, server.json, zár."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stopping.set()
        if self.httpd is not None:
            try:
                self.httpd.server_close()
            except OSError:
                pass
        try:
            self.watcher.stop()
        except Exception:                                  # noqa: BLE001
            pass
        if self._jobs is not None:
            try:
                self._jobs.shutdown()
            except Exception:                              # noqa: BLE001
                pass
        self.security.revoke_tokens()
        if self._caps_registered:
            from . import caps as caps_mod
            if caps_mod.default_caps() is self._caps:
                caps_mod.set_default_caps(None)
        if self.single_instance and self.runtime_dir is not None and self._instance_lock is not None:
            runtime.remove_server_info(self.runtime_dir, pid=os.getpid())
        if self._instance_lock is not None:
            self._instance_lock.release()
            self._instance_lock = None


# ---------------------------------------------------------------------------- parancssor
def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m ma_gui",
        description="MA-munkapad — helyi, böngészős validáló és grafikus felület a metaanalízis-motorhoz.")
    p.add_argument("--project", default=".", help="a projektmappa (alap: az aktuális mappa)")
    p.add_argument("--port", type=int, default=None,
                   help="port (alap: 8790; ha foglalt, 8791–8799, majd az OS választ; 0 = az OS választ)")
    p.add_argument("--no-browser", action="store_true", help="ne nyissa meg a böngészőt, csak írja ki a címet")
    p.add_argument("--lang", choices=("hu", "en"), default="hu", help="a felület nyelve (alap: hu)")
    p.add_argument("--idle-hours", type=float, default=DEFAULT_IDLE_HOURS,
                   help="tétlenségi leállás órában (alap: 4; 0 = soha)")
    return p


def _say(out, text):
    try:
        out.write(text + "\n")
        out.flush()
    except (OSError, ValueError):
        pass


def _relaunch(rdir, a, out, wait_s=10.0):
    """Már futó példány: új indítókód kérése (a zárat tartó példány még indulhat: várunk rá)."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            res = runtime.relaunch(rdir, a.lang)
        except RuntimeError as exc:
            if time.monotonic() < deadline:             # a másik példány még indulhat
                time.sleep(0.25)
                continue
            info = runtime.read_server_info(rdir) or {}
            _say(out, "HIBA: %s A projekthez már fut munkapad%s; állítsd le (Ctrl-C a futó ablakban), vagy "
                      "nyisd meg a meglévő böngészőlapot." % (exc, (" (port: %d)" % info["port"]) if info.get("port")
                                                                    else ""))
            return 1
        if res is not None:
            url, info = res
            _say(out, "A projekthez már fut munkapad (port: %d, pid: %d). Új, egyszer használható indítókód "
                      "(60 s-ig érvényes):" % (info["port"], info["pid"]))
            _say(out, "  " + url)
            if not a.no_browser:
                runtime.open_browser(url)
            return 0
        if time.monotonic() > deadline:
            _say(out, "HIBA: a projekthez tartozó munkapad-zár foglalt, de a futó példány adatai nem olvashatók "
                      "(%s). Várj néhány másodpercet, vagy állítsd le a másik példányt." % rdir)
            return 1
        time.sleep(0.25)


def cli_main(argv=None, out=None):
    """``python -m ma_gui --project DIR [--port N] [--no-browser] [--lang hu|en] [--idle-hours H]``.
    A motor CLI ``gui`` alparancsa ide delegálhat."""
    out = out or sys.stdout
    a = build_parser().parse_args(argv)
    project = Path(os.path.expanduser(a.project))
    if not project.is_dir():
        _say(out, "HIBA: a projektmappa nem létezik vagy nem mappa: %s" % project)
        return 2
    if a.idle_hours is not None and a.idle_hours < 0:
        _say(out, "HIBA: az --idle-hours nem lehet negatív.")
        return 2
    rdir = runtime.project_runtime_dir(project)
    app = App(project, lang=a.lang, idle_hours=a.idle_hours, runtime_dir=rdir, single_instance=True)
    try:
        app.start(a.port)
    except AlreadyRunning:
        return _relaunch(rdir, a, out)
    except OSError as exc:
        _say(out, "HIBA: %s" % exc)
        return 1
    url = app.launch_url()
    _say(out, "MA-munkapad %s fut — projekt: %s" % (__version__, app.project_root))
    _say(out, "  Cím: %s" % url)
    _say(out, "  Az indítókód egyszer használható és 60 másodpercig érvényes; új kódot a parancs újbóli "
              "futtatása ad.")
    _say(out, "  Leállítás: Ctrl-C (tétlenség esetén %s)." % (
        "%.4g óra után magától" % a.idle_hours if a.idle_hours else "nincs automatikus leállás"))
    if not a.no_browser:
        runtime.open_browser(url)

    def _term(_signum, _frame):
        raise KeyboardInterrupt()

    for sig, handler in ((signal.SIGINT, signal.default_int_handler), (signal.SIGTERM, _term)):
        try:
            # a háttérben indított folyamat SIG_IGN-t örökölhet: a Ctrl-C mindig tiszta leállást adjon
            signal.signal(sig, handler)
        except (ValueError, OSError, AttributeError):
            pass
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        _say(out, "Leállítás…")
    finally:
        app.close()
    return 0
