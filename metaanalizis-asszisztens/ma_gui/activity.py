# -*- coding: utf-8 -*-
"""Hash-láncolt tevékenységnapló (szk.ma.activity/v1, 07_ellenorzes/activity.jsonl; terv: 3.1, 4.16)
és újrafuttató szkriptek (rerun.sh, rerun.cmd).

Soronként egy JSON-rekord: {schema, seq, ts, actor, action, argv?, inputs: [{path, sha256}],
outputs: [{path, sha256}], result?, details?, prev_hash, hash}. A hash a rekord 'hash' nélküli
kanonikus JSON-jának (sort_keys, (',', ':') elválasztók, ensure_ascii=False, UTF-8) sha256-ja; a
prev_hash az előző rekord hash-e (az elsőnél null). A napló csak hozzáfűzhető.

Adatvédelem (T10): cellaérték nem kerülhet bele — a value/values/cell/cells/rows/value_as_entered
nevű kulcsokat bármely mélységben visszautasítjuk; az explore-futások nem naplózódnak (a hívó dönt).
Konkurencia: folyamaton belül threading.Lock, folyamatok között zárfájl (fcntl / msvcrt, ha
elérhető) és egyetlen os.write O_APPEND-del.
"""
import datetime
import hashlib
import json
import os
import re
import shlex
import threading
import time
from pathlib import Path

from . import store

SCHEMA = "szk.ma.activity/v1"
LOG_RELPATH = "07_ellenorzes/activity.jsonl"
FORBIDDEN_KEYS = frozenset({"value", "values", "cell", "cells", "rows", "value_as_entered"})
_ACTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")
_LOCK_TIMEOUT = 10.0

_locks_guard = threading.Lock()
_locks = {}


class ActivityError(ValueError):
    """Érvénytelen naplóbejegyzés vagy a napló nem írható."""


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _fmt_ts(value):
    if isinstance(value, str):
        return value
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


def canonical_json(obj):
    """Kanonikus JSON-bájtsor (a hash alapja)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def record_hash(record):
    """A rekord 'hash' mező nélküli kanonikus JSON-jának sha256-ja."""
    return hashlib.sha256(canonical_json({k: v for k, v in record.items() if k != "hash"})).hexdigest()


def find_forbidden_key(obj, path=""):
    """Az első tiltott kulcs útja (pl. 'result.cells'), vagy None."""
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


def _thread_lock(path):
    key = os.path.normcase(os.path.realpath(str(path)))
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class _ProcessLock(object):
    """Folyamatok közötti kizárás egy zárfájllal (POSIX: fcntl.flock, Windows: msvcrt.locking).
    Ha a platform egyiket sem adja, csak a folyamaton belüli zár és az O_APPEND véd."""

    def __init__(self, path, timeout=_LOCK_TIMEOUT):
        self.path = str(path)
        self.timeout = timeout
        self.fd = None
        self.kind = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        deadline = time.monotonic() + self.timeout
        try:
            import fcntl
        except ImportError:
            fcntl = None
        try:
            import msvcrt
        except ImportError:
            msvcrt = None
        while fcntl is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.kind = "fcntl"
                return self
            except (BlockingIOError, PermissionError):
                self._wait(deadline)
            except OSError:
                return self             # a fájlrendszer nem támogatja: szálzár + O_APPEND marad
        while msvcrt is not None:
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                self.kind = "msvcrt"
                return self
            except OSError:
                self._wait(deadline)
        return self

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


def _tail_record(path):
    """Az utolsó érvényes (hash-sel bíró) rekord, és véget ér-e a fájl újsorral."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, True
    if size == 0:
        return None, True
    with open(path, "rb") as fh:
        fh.seek(size - 1)
        ends_nl = fh.read(1) == b"\n"
        block = 1 << 16
        pos, buf = size, b""
        while True:
            start = max(0, pos - block)
            fh.seek(start)
            buf = fh.read(pos - start) + buf
            pos = start
            lines = buf.split(b"\n")
            # az első darab csonka lehet, ha nem a fájl elejéről olvastunk
            candidates = lines if pos == 0 else lines[1:]
            for ln in reversed(candidates):
                if not ln.strip():
                    continue
                try:
                    rec = json.loads(ln.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("hash"), str) and isinstance(rec.get("seq"), int):
                    return rec, ends_nl
            if pos == 0:
                return None, ends_nl
            buf = lines[0]


class ActivityLog(object):
    """Egy projekt tevékenységnaplója. clock: datetime-ot (vagy kész ISO-szöveget) adó függvény
    (tesztekhez); alapból UTC most."""

    def __init__(self, project_root, clock=None, relpath=LOG_RELPATH):
        self.root = Path(os.path.realpath(str(project_root)))
        self.relpath = relpath
        self.path = store.resolve_under(self.root, relpath)
        self.clock = clock or utc_now

    def _entry(self, item):
        """inputs/outputs eleme (út, {path, sha256} vagy (út, sha256)) → {path, sha256}."""
        sha = None
        if isinstance(item, dict):
            p, sha, given = item.get("path"), item.get("sha256"), "sha256" in item
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            (p, sha), given = item, True
        else:
            p, given = item, False
        if not isinstance(p, (str, os.PathLike)) or not str(p):
            raise ActivityError("Érvénytelen fájlút a naplóbejegyzésben.")
        s = str(p).replace("\\", "/")
        if os.path.isabs(str(p)) or re.match(r"^[A-Za-z]:", s):
            full = Path(os.path.realpath(str(p)))
            if store.is_within(full, self.root):
                rel = Path(os.path.relpath(str(full), str(self.root))).as_posix()
            else:
                rel = full.as_posix()
        else:
            rel, full = s, None
            try:
                full = store.resolve_under(self.root, s)
            except store.StoreError:
                full = None
        if not given:
            sha = store.sha256_file(full) if full is not None else None
        if sha is not None and not (isinstance(sha, str) and store.SHA256_RE.match(sha)):
            raise ActivityError("Érvénytelen sha256 a naplóbejegyzésben.")
        return {"path": rel, "sha256": sha}

    def build(self, action, actor, argv=None, inputs=(), outputs=(), result=None, details=None):
        """A rekord a lánc-mezők (seq, prev_hash, hash) nélkül; minden ellenőrzéssel."""
        if not isinstance(action, str) or not _ACTION_RE.match(action):
            raise ActivityError("Érvénytelen művelet-azonosító (betű, szám, '_', '.', ':', '-').")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 200 or any(ord(c) < 32 for c in actor):
            raise ActivityError("Érvénytelen szereplő (actor).")
        rec = {"schema": SCHEMA, "seq": None, "ts": _fmt_ts(self.clock()), "actor": actor, "action": action}
        if argv is not None:
            if not isinstance(argv, (list, tuple)) or not all(isinstance(a, str) for a in argv):
                raise ActivityError("Az argv szövegek listája legyen.")
            if any("\x00" in a for a in argv):
                raise ActivityError("Az argv nem tartalmazhat NUL karaktert.")
            rec["argv"] = list(argv)
        rec["inputs"] = [self._entry(x) for x in inputs or ()]
        rec["outputs"] = [self._entry(x) for x in outputs or ()]
        for key, val in (("result", result), ("details", details)):
            if val is not None:
                if not isinstance(val, dict):
                    raise ActivityError("A(z) %s mező objektum legyen." % key)
                rec[key] = val
        hit = find_forbidden_key(rec)
        if hit:
            raise ActivityError("A naplóba nem kerülhet cellaérték: tiltott kulcs (%s)." % hit)
        try:
            canonical_json(rec)
        except (TypeError, ValueError):
            raise ActivityError("A naplóbejegyzés nem JSON-képes (vagy NaN/végtelen számot tartalmaz).")
        return rec

    def append(self, action, actor, argv=None, inputs=(), outputs=(), result=None, details=None):
        """Új bejegyzés a lánc végére → a teljes rekord (seq, prev_hash, hash kitöltve)."""
        rec = self.build(action, actor, argv, inputs, outputs, result, details)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name("." + self.path.name + ".lock")
        with _thread_lock(self.path), _ProcessLock(lock_path):
            last, ends_nl = _tail_record(str(self.path))
            rec["seq"] = (last["seq"] + 1) if last else 1
            rec["prev_hash"] = last["hash"] if last else None
            rec["hash"] = record_hash(rec)
            line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
            # csonka (félbeszakadt) utolsó sor után új sorban folytatjuk; a verify jelzi a csonkát
            data = (b"" if ends_nl else b"\n") + line.encode("utf-8")
            fd = os.open(str(self.path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
            try:
                view = memoryview(data)
                while view:
                    n = os.write(fd, view)
                    view = view[n:]
                os.fsync(fd)
            finally:
                os.close(fd)
        return rec

    def external_edit(self, rel, sha256, actor="external"):
        """A változásfigyelő által észlelt külső írás (4.16): actor 'external', a fájl új hash-ével."""
        return self.append("file.external_edit", actor, outputs=[{"path": rel, "sha256": sha256}])

    def read(self):
        return read_records(self.path)

    def head(self):
        """{seq, hash} — az utolsó érvényes rekord (az audit-csomag activity_head-je); üresnél None."""
        last, _ = _tail_record(str(self.path))
        return None if last is None else {"seq": last["seq"], "hash": last["hash"]}

    def verify(self, anchor=None):
        return verify_chain(self.path, anchor)


def read_records(path):
    """A napló rekordjai (ellenőrzés nélkül; a nem értelmezhető sorokat kihagyja)."""
    out = []
    try:
        with open(str(path), "rb") as fh:
            for ln in fh:
                if not ln.strip():
                    continue
                try:
                    rec = json.loads(ln.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except FileNotFoundError:
        pass
    return out


def verify_chain(path, anchor=None):
    """(ok, első hibás seq, üzenet). Ellenőrzi: JSON, schema, folytonos seq (1-től), prev_hash,
    hash, tiltott kulcsok, csonka sor. anchor = {seq, hash} (pl. az audit-csomagból): a lánc
    tartalmazza-e ezt a rekordot — így a záró bejegyzések törlése is kiderül."""
    try:
        with open(str(path), "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        if anchor:
            return False, int(anchor.get("seq") or 1), "A tevékenységnapló hiányzik, pedig rögzített fej tartozik hozzá."
        return True, None, "Nincs tevékenységnapló (üres lánc)."
    lines = data.split(b"\n")
    partial = lines[-1] != b""
    if not partial:
        lines = lines[:-1]
    prev, expected = None, 1
    by_seq = {}
    for i, ln in enumerate(lines):
        lineno = i + 1
        if partial and i == len(lines) - 1:
            return False, expected, "A(z) %d. sor csonka (félbeszakadt írás)." % lineno
        try:
            rec = json.loads(ln.decode("utf-8"))
        except ValueError:
            return False, expected, "A(z) %d. sor nem érvényes JSON." % lineno
        if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
            return False, expected, "A(z) %d. sor nem %s rekord." % (lineno, SCHEMA)
        seq = rec.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq != expected:
            return False, expected, ("A(z) %d. sorban a sorszám %r, de %d várható (törölt, beszúrt vagy "
                                     "átrendezett bejegyzés)." % (lineno, seq, expected))
        if rec.get("prev_hash") != prev:
            return False, expected, "A(z) %d. sor (seq %d) nem az előző bejegyzés hash-ére hivatkozik." % (lineno, seq)
        try:
            h = record_hash(rec)
        except (TypeError, ValueError):
            return False, expected, "A(z) %d. sor (seq %d) nem kanonizálható." % (lineno, seq)
        if rec.get("hash") != h:
            return False, expected, "A(z) %d. sor (seq %d) tartalma utólag megváltozott (hash-eltérés)." % (lineno, seq)
        if find_forbidden_key(rec):
            return False, expected, "A(z) %d. sor (seq %d) tiltott (cellaérték-) kulcsot tartalmaz." % (lineno, seq)
        by_seq[seq] = h
        prev, expected = h, expected + 1
    n = expected - 1
    if anchor:
        aseq, ahash = anchor.get("seq"), anchor.get("hash")
        if by_seq.get(aseq) != ahash:
            bad = aseq if isinstance(aseq, int) and aseq <= n else n + 1
            return False, bad, "A rögzített fej (seq %s) nem található a láncban: törölt vagy átírt bejegyzések." % aseq
    return True, None, "A lánc ép: %d bejegyzés." % n


# ------------------------------------------------------------------ újrafuttató szkriptek
_PY_NAMES = {"python", "python3", "python.exe", "python3.exe", "py", "py.exe"}
_CMD_SPECIAL = set(' \t"&|<>^()%!,;=')


def _split_program(argv):
    """argv → ('ma' | 'py' | 'exe', a program utáni argumentumok, a program)."""
    args = list(argv)
    if args and args[0].replace("\\", "/").rsplit("/", 1)[-1].lower() in _PY_NAMES and len(args) > 1:
        args = args[1:]
    prog = args[0]
    base = prog.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base == "ma.py":
        return "ma", args[1:], prog
    if base.endswith(".py"):
        return "py", args[1:], prog
    return "exe", args[1:], prog


def _msvc_quote(arg):
    """MS C-futtatókörnyezet szerinti idézés (CommandLineToArgvW-szabályok), mindig idézőjelben."""
    out, bs = ['"'], 0
    for ch in arg:
        if ch == "\\":
            bs += 1
        elif ch == '"':
            out.append("\\" * (2 * bs + 1) + '"')
            bs = 0
        else:
            out.append("\\" * bs + ch)
            bs = 0
    out.append("\\" * (2 * bs) + '"')
    return "".join(out)


def cmd_quote(arg):
    """Egy argumentum rerun.cmd-be: CRT-idézés, a cmd.exe metakarakterei idézőjelen kívül '^'-pal,
    a '%' kettőzve (batch-fájlban az idézőjel sem véd tőle)."""
    if any(c in arg for c in "\r\n\x00"):
        raise ValueError("sortörés nem adható át cmd-parancssorban")
    s = _msvc_quote(arg) if (arg == "" or any(c in _CMD_SPECIAL for c in arg)) else arg
    out, inq = [], False
    for ch in s:
        if ch == '"':
            inq = not inq
            out.append(ch)
        elif ch == "%":
            out.append("%%")
        elif not inq and ch in "&|<>^()":
            out.append("^" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _safe_comment(text):
    return re.sub(r"[^A-Za-z0-9_.:\- ]", "_", text)


def _commands(records):
    for rec in records:
        argv = rec.get("argv") if isinstance(rec, dict) else None
        if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv):
            yield rec, argv


def render_rerun_sh(records, cd_rel="."):
    lines = ["#!/bin/sh",
             "# MA-munkapad - ujrafuttato szkript (%s alapjan, a naplo sorrendjeben)" % SCHEMA,
             "# Hasznalat: sh rerun.sh   (felulirhato: PYTHON=..., MA_PY=/ut/a/ma.py)",
             "set -eu",
             'cd "$(dirname "$0")/%s"' % cd_rel.replace('"', ""),
             'PYTHON="${PYTHON:-python3}"',
             'MA_PY="${MA_PY:-ma.py}"', ""]
    n = 0
    for rec, argv in _commands(records):
        kind, rest, prog = _split_program(argv)
        head = {"ma": '"$PYTHON" "$MA_PY"', "py": '"$PYTHON" ' + shlex.quote(prog), "exe": shlex.quote(prog)}[kind]
        lines.append("# seq %s - %s - %s" % (rec.get("seq"), _safe_comment(str(rec.get("action", ""))),
                                              _safe_comment(str(rec.get("ts", "")))))
        lines.append(" ".join([head] + [shlex.quote(a) for a in rest]))
        n += 1
    if not n:
        lines.append("# nincs ujrafuttathato parancs a naploban")
    return "\n".join(lines) + "\n"


def render_rerun_cmd(records, cd_rel="."):
    lines = ["@echo off",
             "rem MA-munkapad - ujrafuttato szkript (%s alapjan, a naplo sorrendjeben)" % SCHEMA,
             "rem Hasznalat: rerun.cmd   (felulirhato: set PYTHON=..., set MA_PY=C:\\ut\\ma.py)",
             "chcp 65001 >nul",
             "setlocal",
             'cd /d "%%~dp0%s"' % cd_rel.replace("/", "\\").replace('"', "").replace("%", "%%"),
             'if not defined PYTHON set "PYTHON=py -3"',
             'if not defined MA_PY set "MA_PY=ma.py"', ""]
    n = 0
    for rec, argv in _commands(records):
        lines.append("rem seq %s - %s - %s" % (rec.get("seq"), _safe_comment(str(rec.get("action", ""))),
                                                _safe_comment(str(rec.get("ts", "")))))
        kind, rest, prog = _split_program(argv)
        try:
            head = {"ma": '%PYTHON% "%MA_PY%"', "py": "%PYTHON% " + cmd_quote(prog), "exe": cmd_quote(prog)}[kind]
            lines.append(" ".join([head] + [cmd_quote(a) for a in rest]))
        except ValueError:
            lines.append("echo HIBA: a seq %s parancsa sortorest tartalmaz, cmd-ben nem futtathato. 1>&2"
                         % rec.get("seq"))
            lines.append("exit /b 1")
            continue
        lines.append("if errorlevel 1 exit /b 1")
        n += 1
    if not n:
        lines.append("rem nincs ujrafuttathato parancs a naploban")
    lines.append("endlocal")
    return "\r\n".join(lines) + "\r\n"


def write_rerun_scripts(project, records, out_dir=None):
    """rerun.sh és rerun.cmd az argv-t tartalmazó bejegyzésekből (a napló sorrendjében) →
    (sh út, cmd út). out_dir alapból <projekt>/07_ellenorzes; a projekten belül kell lennie.
    A tartalom csak a rekordoktól függ (determinisztikus audit-csomaghoz)."""
    root = Path(os.path.realpath(str(project)))
    out = Path(os.path.realpath(str(out_dir))) if out_dir is not None else root / "07_ellenorzes"
    if not store.is_within(out, root):
        raise store.Forbidden("Az újrafuttató szkriptek csak a projektmappába írhatók.")
    records = list(records)
    cd_rel = Path(os.path.relpath(str(root), str(out))).as_posix()
    ps = store.ProjectStore(root)
    rel_dir = "" if os.path.normcase(str(out)) == os.path.normcase(str(root)) else ps.relpath_of(out) + "/"
    sh_rel, cmd_rel = rel_dir + "rerun.sh", rel_dir + "rerun.cmd"
    ps.write_bytes(sh_rel, render_rerun_sh(records, cd_rel).encode("utf-8"))
    ps.write_bytes(cmd_rel, render_rerun_cmd(records, cd_rel).encode("utf-8"))
    sh_path = ps.path(sh_rel)
    try:
        os.chmod(str(sh_path), 0o755)
    except OSError:
        pass
    return sh_path, ps.path(cmd_rel)
