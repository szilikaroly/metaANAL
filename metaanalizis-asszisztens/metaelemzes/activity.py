# -*- coding: utf-8 -*-
"""Hash-láncolt tevékenységnapló — a szk.ma.activity/v1 kanonikus megvalósítása (terv 4.16, E7).

Fájl: <projekt>/07_ellenorzes/activity.jsonl, soronként egy rekord, ebben a mezősorrendben:

    {"schema": "szk.ma.activity/v1", "seq": 412, "ts": "2026-10-04T21:12:00Z", "actor": "user:SzK",
     "action": "analyze.commit", "argv": ["ma.py", "analyze", ...] | null,
     "inputs": {"03_adatok/o1.csv": "<sha256>" | null, ...}, "outputs": {...},
     "result": {"exit_code": 0, "summary": "k=13, RR 0.49 [0.33; 0.73]"} | null,
     "prev": "<sha256>" | null}

Bővítés: opcionális "details" objektum (a munkapad pl. {"kind": "decision", "id": 5}); a fogyasztó
figyelmen kívül hagyhatja. Más mezőt az append nem fogad el.

Lánc: a seq 1-től folytonos; a prev az előző sor kanonikus JSON-jának sha256-ja (canonical_json:
sort_keys, (',', ':') elválasztók, ensure_ascii=False, UTF-8, NaN/Infinity nélkül), az első soré null.
Így a sor formázása (szóköz, kulcssorrend) nem számít, a tartalma igen. A fej ({seq, hash}) hash-e a
következő sor prev-je lesz.

Utak: az inputs/outputs kulcsa projekt-relatív, '/'-elválasztós út; projekten kívüli fájlnál abszolút
(POSIX-alakú) út. Az érték a fájl sha256-ja, vagy null (nincs ilyen fájl / nem olvasható).

Adatvédelem (T10): cellaérték nem kerülhet a naplóba — a value / values / cell / cells / rows /
value_as_entered nevű kulcsot bármely mélységben visszautasítjuk (a verify is jelzi). Az explore-futások
nem naplózódnak (a hívó dönt).

Konkurencia: folyamaton belül szálzár, folyamatok között zárfájl (07_ellenorzes/.activity.jsonl.lock;
fcntl / msvcrt, ha elérhető) a farok olvasása és a hozzáfűzés köré; a sor egyetlen írással, 'a'
(O_APPEND) módban kerül a fájl végére, opcionális fsync-kel.

API (a ma_gui/activity.py ezt burkolja):
    append(project_dir, record, clock=None, fsync=True) → a kiírt, teljes rekord
        record: {action, actor, [argv], [inputs], [outputs], [result], [details], [ts]}; a schema,
        seq és prev mezőt a napló tölti ki. inputs/outputs: {út: sha256 | None} (None → a fájlból
        számolva), vagy lista: út | (út, sha256) | {"path": út, "sha256": sha256}; a relatív út a
        projektgyökérhez képest értendő.
    verify(project_dir, anchor=None) → (ok, első hibás seq | None, üzenet)
    read(project_dir) → a rekordok listája (ellenőrzés nélkül);  head(project_dir) → {seq, hash} | None
    cli_record(project_dir, argv, inputs=(), outputs=(), result=None, ...) → rekord | None
        a motor CLI-jének horga: csak MA_ACTIVITY_LOG=1 mellett ír; hibánál figyelmeztet, nem bukik el.
    ActivityLog(project_dir, clock=None): append(action, actor, argv=None, inputs=(), outputs=(),
        result=None, details=None), read(), head(), verify(anchor=None), external_edit(rel, sha256).
"""
import datetime
import hashlib
import json
import os
import posixpath
import re
import sys
import threading
import time

SCHEMA = "szk.ma.activity/v1"
LOG_RELPATH = "07_ellenorzes/activity.jsonl"
ENV_FLAG = "MA_ACTIVITY_LOG"
ENV_ACTOR = "MA_ACTOR"
FORBIDDEN_KEYS = frozenset({"value", "values", "cell", "cells", "rows", "value_as_entered"})
FIELDS = ("schema", "seq", "ts", "actor", "action", "argv", "inputs", "outputs", "result", "details", "prev")
_INPUT_KEYS = frozenset({"action", "actor", "argv", "inputs", "outputs", "result", "details", "ts", "schema"})
_ACTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_ACTOR = 200
_LOCK_TIMEOUT = 10.0
_TRUE = ("1", "true", "yes", "on", "igen")

_locks_guard = threading.Lock()
_locks = {}


class ActivityError(ValueError):
    """Érvénytelen naplóbejegyzés, vagy a napló nem írható."""


# ------------------------------------------------------------------ alapok
def log_path(project_dir):
    return os.path.join(os.path.abspath(project_dir), *LOG_RELPATH.split("/"))


def canonical_json(obj):
    """Kanonikus JSON-bájtsor (a hash alapja); NaN/Infinity → ValueError."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def record_hash(record):
    """A rekord kanonikus JSON-jának sha256-ja — a következő sor prev-je."""
    return hashlib.sha256(canonical_json(record)).hexdigest()


def file_sha256(path):
    """Szabályos fájl sha256-ja; None, ha nincs ilyen fájl vagy nem olvasható."""
    try:
        if not os.path.isfile(path):
            return None
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 16), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def find_forbidden_key(obj, path=""):
    """Az első tiltott (cellaérték-) kulcs útja (pl. 'details.rows[0].cell'), vagy None."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = "%s.%s" % (path, k) if path else str(k)
            if str(k).strip().lower() in FORBIDDEN_KEYS:
                return here
            hit = find_forbidden_key(v, here)
            if hit:
                return hit
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hit = find_forbidden_key(v, "%s[%d]" % (path, i))
            if hit:
                return hit
    return None


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def format_ts(value):
    """datetime (naiv: UTC) → '2026-10-04T21:12:00Z'; kész szöveget ellenőrizve ad vissza."""
    if isinstance(value, str):
        if not _TS_RE.match(value):
            raise ActivityError("Érvénytelen időbélyeg (UTC, pl. 2026-10-04T21:12:00Z): %r" % value)
        return value
    if not isinstance(value, datetime.datetime):
        raise ActivityError("Az óra datetime-ot vagy ISO-szöveget adjon.")
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


def valid_actor(actor):
    """Szereplő: nem üres, legfeljebb 200 karakteres, vezérlőkarakter nélküli szöveg
    (pl. 'user:SzK', 'agent:reviewer', 'cli', 'external')."""
    return (isinstance(actor, str) and bool(actor.strip()) and len(actor) <= _MAX_ACTOR
            and not any(ord(c) < 32 or ord(c) == 127 for c in actor))


def _json_safe(obj, where):
    try:
        canonical_json(obj)
    except (TypeError, ValueError):
        raise ActivityError("A(z) %s mező nem JSON-képes (vagy NaN/végtelen számot tartalmaz)." % where)


# ------------------------------------------------------------------ rekord összeállítása
def _rel_key(path, root):
    """Fájlút → (napló-kulcs, abszolút út a hash-hez). Relatív út: a projektgyökérhez képest."""
    if not isinstance(path, (str, os.PathLike)):
        raise ActivityError("Érvénytelen fájlút a naplóbejegyzésben: %r" % (path,))
    raw = os.fspath(path)
    if not raw or "\x00" in raw:
        raise ActivityError("Üres vagy NUL karaktert tartalmazó fájlút a naplóbejegyzésben.")
    s = raw.replace("\\", "/")
    if os.path.isabs(raw) or s.startswith("/") or re.match(r"^[A-Za-z]:", s):
        full = os.path.realpath(raw)
        rel = os.path.relpath(full, root) if _within(full, root) else None
        if rel is None:
            return full.replace("\\", "/"), full
        return ("." if rel == "." else rel.replace("\\", "/")), full
    norm = posixpath.normpath(s)
    if norm == ".." or norm.startswith("../"):
        raise ActivityError("A relatív út nem mutathat a projekten kívülre: %r" % raw)
    return norm, os.path.join(root, *norm.split("/"))


def _within(path, root):
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:      # más meghajtó (Windows)
        return False


def _check_sha(sha, key):
    if sha is not None and not (isinstance(sha, str) and _SHA_RE.match(sha)):
        raise ActivityError("Érvénytelen sha256 a naplóbejegyzésben (%s)." % key)
    return sha


def _file_map(items, root, field):
    """inputs/outputs bármely elfogadott alakja → {kulcs: sha256 | None}, kulcs szerint rendezve."""
    if items is None:
        return {}
    pairs = []
    if isinstance(items, dict):
        for p, sha in items.items():
            pairs.append((p, sha, sha is not None))
    elif isinstance(items, (list, tuple, set, frozenset)):
        for it in items:
            if isinstance(it, dict):
                if "path" not in it or set(it) - {"path", "sha256"}:
                    raise ActivityError("A(z) %s elem alakja {path, sha256} legyen." % field)
                pairs.append((it["path"], it.get("sha256"), "sha256" in it))
            elif isinstance(it, (tuple, list)) and len(it) == 2:
                pairs.append((it[0], it[1], True))
            else:
                pairs.append((it, None, False))
    else:
        raise ActivityError("A(z) %s mező útvonal→sha256 objektum vagy lista legyen." % field)
    out = {}
    for p, sha, given in pairs:
        key, full = _rel_key(p, root)
        out[key] = _check_sha(sha, key) if given else file_sha256(full)
    return {k: out[k] for k in sorted(out)}


def _result(result):
    if result is None:
        return None
    if isinstance(result, bool) or not isinstance(result, (int, dict)):
        raise ActivityError("A result mező objektum ({exit_code, summary}) vagy kilépési kód legyen.")
    if isinstance(result, int):
        return {"exit_code": result, "summary": None}
    res = dict(result)
    code = res.get("exit_code")
    if code is not None and (isinstance(code, bool) or not isinstance(code, int)):
        raise ActivityError("A result.exit_code egész szám (vagy null) legyen.")
    if res.get("summary") is not None and not isinstance(res.get("summary"), str):
        raise ActivityError("A result.summary szöveg (vagy null) legyen.")
    out = {"exit_code": code, "summary": res.get("summary")}
    out.update((k, v) for k, v in res.items() if k not in out)
    return out


def prepare(project_dir, record, clock=None):
    """A bejegyzés ellenőrzött, normalizált alakja a lánc-mezők (seq, prev) nélkül.
    Fájlt csak a hiányzó sha256-ok kiszámításához olvas."""
    if not isinstance(record, dict):
        raise ActivityError("A naplóbejegyzés objektum legyen.")
    extra = set(record) - _INPUT_KEYS
    if extra:
        raise ActivityError("Ismeretlen vagy a napló által kitöltött mező a bejegyzésben: %s (a seq és a "
                            "prev mezőt a napló számolja)." % ", ".join(sorted(map(str, extra))))
    if record.get("schema") not in (None, SCHEMA):
        raise ActivityError("A bejegyzés sémája csak %s lehet." % SCHEMA)
    hit = find_forbidden_key(record)
    if hit:
        raise ActivityError("A naplóba nem kerülhet cellaérték: tiltott kulcs (%s)." % hit)
    action, actor = record.get("action"), record.get("actor")
    if not isinstance(action, str) or not _ACTION_RE.match(action):
        raise ActivityError("Érvénytelen művelet-azonosító (betűvel kezdődik; betű, szám, '_', '.', ':', '-'): %r"
                            % (action,))
    if not valid_actor(actor):
        raise ActivityError("Érvénytelen szereplő (actor): nem üres, legfeljebb %d karakteres, vezérlőkarakter "
                            "nélküli szöveg kell (pl. 'user:SzK', 'agent:reviewer')." % _MAX_ACTOR)
    argv = record.get("argv")
    if argv is not None:
        if not isinstance(argv, (list, tuple)) or not all(isinstance(a, str) for a in argv):
            raise ActivityError("Az argv szövegek listája legyen.")
        if any("\x00" in a for a in argv):
            raise ActivityError("Az argv nem tartalmazhat NUL karaktert.")
        argv = list(argv)
    details = record.get("details")
    if details is not None and not isinstance(details, dict):
        raise ActivityError("A details mező objektum legyen.")
    root = os.path.realpath(os.path.abspath(project_dir))
    ts = record.get("ts")
    rec = {"schema": SCHEMA, "seq": None,
           "ts": format_ts(ts if ts is not None else (clock or utc_now)()),
           "actor": actor.strip(), "action": action, "argv": argv,
           "inputs": _file_map(record.get("inputs"), root, "inputs"),
           "outputs": _file_map(record.get("outputs"), root, "outputs"),
           "result": _result(record.get("result"))}
    if details is not None:
        rec["details"] = dict(details)
    rec["prev"] = None
    hit = find_forbidden_key(rec)
    if hit:
        raise ActivityError("A naplóba nem kerülhet cellaérték: tiltott kulcs (%s)." % hit)
    _json_safe(rec, "bejegyzés")
    return rec


# ------------------------------------------------------------------ zárolás
def _thread_lock(path):
    key = os.path.normcase(os.path.realpath(path))
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class _ProcessLock(object):
    """Folyamatok közötti kizárás zárfájllal (POSIX: fcntl.flock, Windows: msvcrt.locking); ha egyik
    sem érhető el (vagy a fájlrendszer nem támogatja), csak a szálzár és az O_APPEND véd."""

    def __init__(self, path, timeout=_LOCK_TIMEOUT):
        self.path, self.timeout, self.fd, self.kind = path, timeout, None, None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        deadline = time.monotonic() + self.timeout
        try:
            import fcntl
        except ImportError:
            fcntl = None
        if fcntl is not None:
            while True:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.kind = "fcntl"
                    return self
                except (BlockingIOError, PermissionError):
                    self._wait(deadline)
                except OSError:
                    return self
        try:
            import msvcrt
        except ImportError:
            return self
        while True:
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                self.kind = "msvcrt"
                return self
            except OSError:
                self._wait(deadline)

    def _wait(self, deadline):
        if time.monotonic() > deadline:
            self._close()
            raise ActivityError("A tevékenységnaplót egy másik folyamat zárolja; próbáld újra.")
        time.sleep(0.01)

    def __exit__(self, *exc):
        try:
            if self.kind == "fcntl":
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            elif self.kind == "msvcrt":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            self._close()
        return False

    def _close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


# ------------------------------------------------------------------ olvasás
def _loads(raw):
    """Egy sor → objektum; NaN/Infinity és érvénytelen UTF-8/JSON → ValueError."""
    def bad_constant(name):
        raise ValueError("nem véges szám: %s" % name)
    return json.loads(raw.decode("utf-8"), parse_constant=bad_constant)


def _tail(path):
    """(az utolsó, egész seq-et hordozó rekord vagy None, véget ér-e a fájl újsorral)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, True
    if size == 0:
        return None, True
    with open(path, "rb") as fh:
        fh.seek(size - 1)
        ends_nl = fh.read(1) == b"\n"
        pos, buf, block = size, b"", 1 << 16
        while True:
            start = max(0, pos - block)
            fh.seek(start)
            buf = fh.read(pos - start) + buf
            pos = start
            lines = buf.split(b"\n")
            for ln in reversed(lines if pos == 0 else lines[1:]):
                if not ln.strip():
                    continue
                try:
                    rec = _loads(ln)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("seq"), int) and not isinstance(rec.get("seq"), bool):
                    return rec, ends_nl
            if pos == 0:
                return None, ends_nl
            buf = lines[0]


def read(project_dir):
    """A napló rekordjai a fájl sorrendjében (ellenőrzés nélkül; a nem értelmezhető sort kihagyja)."""
    out = []
    try:
        with open(log_path(project_dir), "rb") as fh:
            for ln in fh:
                if not ln.strip():
                    continue
                try:
                    rec = _loads(ln.rstrip(b"\r\n"))
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except FileNotFoundError:
        pass
    return out


def head(project_dir):
    """{seq, hash} — az utolsó rekord (az audit-csomag activity_head-je; hash = a következő prev); üresnél None."""
    last, _ = _tail(log_path(project_dir))
    return None if last is None else {"seq": last["seq"], "hash": record_hash(last)}


# ------------------------------------------------------------------ írás
def append(project_dir, record, clock=None, fsync=True):
    """Új bejegyzés a lánc végére → a kiírt, teljes rekord (seq és prev kitöltve).
    ActivityError: érvénytelen bejegyzés (pl. cellaérték-kulcs) vagy foglalt napló."""
    rec = prepare(project_dir, record, clock)
    path = log_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".lock")
    with _thread_lock(path), _ProcessLock(lock_path):
        last, ends_nl = _tail(path)
        rec["seq"] = last["seq"] + 1 if last else 1
        rec["prev"] = record_hash(last) if last else None
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        # félbeszakadt utolsó sor után új sorban folytatjuk; a verify jelzi a csonkát
        data = (b"" if ends_nl else b"\n") + line.encode("utf-8")
        with open(path, "ab") as fh:
            fh.write(data)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
    return rec


# ------------------------------------------------------------------ ellenőrzés
def _field_problem(rec):
    """A v1-es mezők típushibája szövegként, vagy None."""
    if not isinstance(rec.get("ts"), str) or not _TS_RE.match(rec["ts"]):
        return "az időbélyeg (ts) hiányzik vagy érvénytelen"
    if not valid_actor(rec.get("actor")):
        return "a szereplő (actor) hiányzik vagy érvénytelen"
    if not isinstance(rec.get("action"), str) or not _ACTION_RE.match(rec["action"]):
        return "a művelet (action) hiányzik vagy érvénytelen"
    argv = rec.get("argv")
    if argv is not None and not (isinstance(argv, list) and all(isinstance(a, str) for a in argv)):
        return "az argv nem szövegek listája"
    for f in ("inputs", "outputs"):
        m = rec.get(f)
        if not isinstance(m, dict) or any(v is not None and not (isinstance(v, str) and _SHA_RE.match(v))
                                          for v in m.values()):
            return "a(z) %s nem útvonal→sha256 objektum" % f
    res = rec.get("result")
    if res is not None:
        if not isinstance(res, dict):
            return "a result nem objektum"
        code = res.get("exit_code")
        if code is not None and (isinstance(code, bool) or not isinstance(code, int)):
            return "a result.exit_code nem egész szám"
    if rec.get("details") is not None and not isinstance(rec.get("details"), dict):
        return "a details nem objektum"
    return None


def verify(project_dir, anchor=None):
    """A lánc ellenőrzése → (ok, első hibás seq vagy None, üzenet). Ellenőrzi: érvényes JSON (NaN/
    Infinity nélkül), schema, folytonos seq 1-től, prev = az előző sor kanonikus hash-e, a mezők típusa,
    tiltott (cellaérték-) kulcs, csonka utolsó sor. anchor = {seq, hash} (pl. az audit-csomag
    activity_head-je): a láncban szerepel-e ez a rekord — így a záró bejegyzések törlése is kiderül."""
    path = log_path(project_dir)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        if anchor:
            return False, _anchor_seq(anchor, 1), "A tevékenységnapló hiányzik, pedig rögzített fej tartozik hozzá."
        return True, None, "Nincs tevékenységnapló (üres lánc)."
    lines = data.split(b"\n")
    partial = lines[-1] != b""
    if not partial:
        lines.pop()
    prev, expected, hashes = None, 1, {}
    for i, raw in enumerate(lines):
        lineno = i + 1
        if partial and i == len(lines) - 1:
            return False, expected, "A(z) %d. sor csonka (félbeszakadt írás)." % lineno
        try:
            rec = _loads(raw[:-1] if raw.endswith(b"\r") else raw)
        except ValueError:
            return False, expected, "A(z) %d. sor nem érvényes JSON-rekord." % lineno
        if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
            return False, expected, "A(z) %d. sor nem %s rekord." % (lineno, SCHEMA)
        seq = rec.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq != expected:
            return False, expected, ("A(z) %d. sorban a sorszám %r, de %d várható (törölt, beszúrt vagy átrendezett "
                                     "bejegyzés)." % (lineno, seq, expected))
        if rec.get("prev", False) != prev:
            return False, seq, ("A(z) %d. sor (seq %d) nem az előző bejegyzés hash-ére hivatkozik (az előző sor "
                                "utólag megváltozott, vagy a lánc megszakadt)." % (lineno, seq))
        problem = _field_problem(rec)
        if problem:
            return False, seq, "A(z) %d. sor (seq %d): %s." % (lineno, seq, problem)
        hit = find_forbidden_key(rec)
        if hit:
            return False, seq, "A(z) %d. sor (seq %d) tiltott (cellaérték-) kulcsot tartalmaz: %s." % (lineno, seq, hit)
        prev = record_hash(rec)
        hashes[seq] = prev
        expected += 1
    n = expected - 1
    if anchor:
        aseq = anchor.get("seq") if isinstance(anchor, dict) else None
        if hashes.get(aseq) != (anchor.get("hash") if isinstance(anchor, dict) else None):
            return False, _anchor_seq(anchor, n + 1, n), ("A rögzített fej (seq %s) nem található a láncban: törölt "
                                                          "vagy átírt bejegyzések." % aseq)
    return True, None, "A lánc ép: %d bejegyzés." % n


def _anchor_seq(anchor, default, n=None):
    aseq = anchor.get("seq") if isinstance(anchor, dict) else None
    if isinstance(aseq, int) and not isinstance(aseq, bool) and aseq >= 1 and (n is None or aseq <= n):
        return aseq
    return default


# ------------------------------------------------------------------ objektumos burok (munkapad)
class ActivityLog(object):
    """Egy projekt tevékenységnaplója (a ma_gui ezt használja). clock: datetime-ot (naiv = UTC) vagy kész
    ISO-szöveget adó függvény — tesztekhez; alapból UTC most."""

    def __init__(self, project_dir, clock=None, fsync=True):
        self.root = os.path.realpath(os.path.abspath(os.fspath(project_dir)))
        self.path = log_path(self.root)
        self.relpath = LOG_RELPATH
        self.clock = clock or utc_now
        self.fsync = fsync

    def build(self, action, actor, argv=None, inputs=(), outputs=(), result=None, details=None):
        """A rekord seq és prev nélkül (minden ellenőrzéssel, írás nélkül)."""
        return prepare(self.root, _record(action, actor, argv, inputs, outputs, result, details), self.clock)

    def append(self, action, actor, argv=None, inputs=(), outputs=(), result=None, details=None):
        return append(self.root, _record(action, actor, argv, inputs, outputs, result, details), self.clock,
                      self.fsync)

    def external_edit(self, rel, sha256, actor="external"):
        """A változásfigyelő által észlelt külső írás (4.16): actor 'external', a fájl új hash-ével."""
        return self.append("file.external_edit", actor, outputs={rel: sha256} if sha256 else [rel])

    def read(self):
        return read(self.root)

    def head(self):
        return head(self.root)

    def verify(self, anchor=None):
        return verify(self.root, anchor)


def _record(action, actor, argv, inputs, outputs, result, details):
    rec = {"action": action, "actor": actor, "argv": argv, "inputs": inputs, "outputs": outputs, "result": result}
    if details is not None:
        rec["details"] = details
    return rec


# ------------------------------------------------------------------ CLI-horog (E7, MA_ACTIVITY_LOG)
_CMD_ALIASES = {"elemez": "analyze", "projekt": "project", "validal": "validate", "hatasmeret": "es",
                "konvertal": "convert", "tudasbazis": "kb", "ero": "power", "onteszt": "selftest"}
PATH_OPTIONS = ("--data", "--out", "--project", "--spec", "--kb-db")
_PY_NAMES = ("python", "python3", "python.exe", "python3.exe", "py", "py.exe")
_PROJECT_FLAGS = ("--strict", "-h", "--help", "--json")


def enabled(environ=None):
    """Be van-e kapcsolva a CLI-hozzáfűzés (MA_ACTIVITY_LOG=1)."""
    env = os.environ if environ is None else environ
    return str(env.get(ENV_FLAG, "")).strip().lower() in _TRUE


def cli_actor(agent=None, environ=None):
    """A CLI-bejegyzés szereplője: MA_ACTOR (ha érvényes); különben 'user' (--agent user),
    'agent:<név>' (--agent <név>) vagy 'cli'."""
    env = os.environ if environ is None else environ
    who = env.get(ENV_ACTOR)
    if valid_actor(who):
        return who.strip()
    if agent == "user":
        return "user"
    if isinstance(agent, str) and agent.strip():
        cand = "agent:%s" % agent.strip()
        if valid_actor(cand):
            return cand
    return "cli"


def _program_split(argv):
    """argv → (a program-előtag, a parancs argumentumai). Előtag nélküli argv (sys.argv[1:]) elé
    'ma.py' kerül; 'python3 ma.py …', 'ma.py …' és 'python3 -m metaelemzes …' megmarad."""
    args = [str(a) for a in argv]
    if args and os.path.basename(args[0].replace("\\", "/")).lower() in _PY_NAMES:
        if len(args) > 2 and args[1] == "-m":
            return args[:3], args[3:]
        if len(args) > 1 and args[1].lower().endswith(".py"):
            return args[:2], args[2:]
        return args[:1], args[1:]
    if args and args[0].lower().endswith(".py"):
        return args[:1], args[1:]
    return ["ma.py"], args


def action_from_argv(argv):
    """['analyze', …] → 'analyze'; ['project', 'log', …] → 'project.log' (magyar álnevek is)."""
    _, args = _program_split(argv)
    cmd = _CMD_ALIASES.get(args[0], args[0]) if args else "cli"
    if cmd == "project" and len(args) > 1 and not args[1].startswith("-"):
        cmd = "project.%s" % args[1]
    return cmd if _ACTION_RE.match(cmd) else "cli"


def _portable(value, root, cwd):
    full = os.path.realpath(os.path.join(cwd, value))
    if _within(full, root):
        rel = os.path.relpath(full, root).replace("\\", "/")
        return rel
    return full


def portable_argv(argv, project_dir, cwd=None):
    """Újrafuttatható argv a projektgyökérből (a rerun.sh/.cmd onnan indul): a program elé 'ma.py' kerül
    (ha nincs); a PATH_OPTIONS kapcsolók értéke és a project-alparancsok mappa-argumentuma projekt-relatív
    ('.' = a gyökér), a projekten kívül abszolút út. A rövidített kapcsolónevet szó szerint hagyja."""
    cwd = os.path.abspath(cwd or os.getcwd())
    root = os.path.realpath(os.path.abspath(project_dir))
    prog, args = _program_split(argv)
    out, i, positional_done = [], 0, True
    cmd = _CMD_ALIASES.get(args[0], args[0]) if args else None
    if cmd == "project" and len(args) > 1 and not args[1].startswith("-"):
        out, i, positional_done = args[:2], 2, False
    while i < len(args):
        t = args[i]
        opt, eq, val = t.partition("=")
        if t in PATH_OPTIONS and i + 1 < len(args):
            out += [t, _portable(args[i + 1], root, cwd)]
            i += 2
            continue
        if eq and opt in PATH_OPTIONS and t.startswith("--"):
            out.append("%s=%s" % (opt, _portable(val, root, cwd)))
        elif t.startswith("-") and t != "-":
            out.append(t)
            if not eq and t not in _PROJECT_FLAGS and not positional_done and i + 1 < len(args):
                out.append(args[i + 1])     # project-alparancs: a kapcsolók (a --strict kivételével) értéket kapnak
                i += 1
        elif not positional_done:
            out.append(_portable(t, root, cwd))
            positional_done = True
        else:
            out.append(t)
        i += 1
    return prog + out


def _abs_items(items, cwd, expand_dirs=False):
    """CLI-oldali utak (a munkakönyvtárhoz képest) → abszolút utak; mappa → a benne lévő fájlok."""
    if items is None:
        return []
    if isinstance(items, dict):
        items = list(items.items())
    out = []
    for it in items:
        if isinstance(it, dict) and "path" in it:
            it = (it["path"], it.get("sha256"))
        p, rest = (it[0], tuple(it[1:])) if isinstance(it, (tuple, list)) else (it, ())
        if rest == (None,):
            rest = ()                       # nincs megadott hash: a fájlból számoljuk
        full = os.path.abspath(os.path.join(cwd, os.fspath(p)))
        if expand_dirs and not rest and os.path.isdir(full):
            for base, dirs, files in os.walk(full):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                out += [os.path.join(base, f) for f in sorted(files) if not f.startswith(".")]
            continue
        out.append((full,) + rest if rest else full)
    return out


def cli_record(project_dir, argv, inputs=(), outputs=(), result=None, action=None, actor=None, details=None,
               environ=None, clock=None, cwd=None):
    """A motor CLI-jének horga (E7): MA_ACTIVITY_LOG=1 mellett egy bejegyzés a projekt láncába.
    project_dir: a projektmappa; argv: a parancs argumentumai (sys.argv[1:] / a._argv) vagy a teljes
    parancssor — portable_argv teszi újrafuttathatóvá; inputs/outputs: a munkakönyvtárhoz képest
    értett utak (mappa → a benne lévő fájlok) vagy (út, sha256) párok; result: {exit_code, summary}
    vagy kilépési kód (summary-ben cellaérték nem lehet; ajánlott: run_summary_text). Csak sikeres
    (0 kódú) parancsot naplózz: a rerun-szkript a napló sorrendjében, hibánál megállva futtat.
    Kikapcsolt állapotban semmit nem ír és None-t ad; íráshiba esetén figyelmeztet a stderr-en, és
    None-t ad (a parancs eredménye nem vész el)."""
    env = os.environ if environ is None else environ
    if not enabled(env):
        return None
    try:
        root = os.path.abspath(os.fspath(project_dir))
        if not os.path.isdir(root):
            raise ActivityError("nincs ilyen projektmappa: %s" % root)
        here = os.path.abspath(cwd or os.getcwd())
        rec = {"action": action or action_from_argv(argv), "actor": actor or cli_actor(None, env),
               "argv": portable_argv(argv, root, here), "inputs": _abs_items(inputs, here),
               "outputs": _abs_items(outputs, here, expand_dirs=True), "result": result}
        if details is not None:
            rec["details"] = details
        return append(root, rec, clock)
    except Exception as exc:                # noqa: BLE001 — a napló hibája nem buktatja el a parancsot
        print("FIGYELEM: a tevékenységnaplóba (%s) nem sikerült írni: %s" % (LOG_RELPATH, exc), file=sys.stderr)
        return None


def run_summary_text(summary):
    """A projektnapló futás-összefoglalójából (cli._run_summary) rövid szöveg a result.summary-hez,
    a motor formázójával: 'k=13, RR 0.49 [0.33; 0.73]' (a megjelenítési skálán, ha van ilyen)."""
    from .plots import fmt_triple
    if not isinstance(summary, dict):
        return None
    parts = ["k=%s" % summary.get("k")] if summary.get("k") is not None else []
    measure = summary.get("measure")
    est, ci = summary.get("estimate_display"), summary.get("ci_display")
    if est is None or not isinstance(ci, (list, tuple)) or len(ci) != 2 or None in ci:
        est, ci = summary.get("estimate"), summary.get("ci")
    if est is not None and isinstance(ci, (list, tuple)) and len(ci) == 2:
        parts.append(("%s %s" % (measure or "", fmt_triple(est, ci[0], ci[1]))).strip())
    elif measure:
        parts.append(str(measure))
    return ", ".join(parts) or None
