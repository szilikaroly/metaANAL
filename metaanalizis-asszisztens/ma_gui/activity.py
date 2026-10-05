# -*- coding: utf-8 -*-
"""Hash-láncolt tevékenységnapló (szk.ma.activity/v1, 07_ellenorzes/activity.jsonl; terv: 3.1, 4.16)
és újrafuttató szkriptek (rerun.sh, rerun.cmd).

A napló EGYETLEN megvalósítása a motoré (``metaelemzes.activity``, a ``metaelemzes.api``-n át): a
munkapad és a CLI (``MA_ACTIVITY_LOG=1 ma.py …``) ugyanazt a rekordformátumot, ugyanazt a zárfájlt
(07_ellenorzes/.activity.jsonl.lock) és ugyanazt a hash-láncot használja, így a két író felváltva is
ép láncot ad. Ez a modul vékony burok a motor köré (a régi ma_gui-hívások alakjával), plusz az
újrafuttató szkriptek előállítása.

Rekord (a motor kanonikus alakja): {schema, seq, ts, actor, action, argv | null,
inputs: {út: sha256 | null}, outputs: {út: sha256 | null}, result | null, details?, prev}. A prev az
előző sor kanonikus JSON-jának sha256-ja (record_hash), az elsőnél null; a fej {seq, hash}.

Adatvédelem (T10): cellaérték nem kerülhet bele — a value/values/cell/cells/rows/value_as_entered
nevű kulcsokat bármely mélységben a motor visszautasítja; az explore-futások nem naplózódnak (a
hívó dönt).
"""
import hashlib
import os
import re
import shlex
from pathlib import Path

from metaelemzes import api as _api

from . import store

SCHEMA = _api.ACTIVITY_SCHEMA
LOG_RELPATH = _api.LOG_RELPATH
FORBIDDEN_KEYS = _api.FORBIDDEN_KEYS
ActivityError = _api.ActivityError
canonical_json = _api.canonical_json
find_forbidden_key = _api.find_forbidden_key


def record_hash(record):
    """A rekord kanonikus JSON-jának sha256-ja — a következő sor ``prev``-je (a motoréval azonos)."""
    return hashlib.sha256(canonical_json(record)).hexdigest()


def _project_of(path):
    """A napló útja (``<projekt>/07_ellenorzes/activity.jsonl``) vagy maga a projektmappa →
    projektmappa. A motor csak a kanonikus helyen vezeti a naplót."""
    p = Path(os.fspath(path))
    if p.is_dir():
        return p
    parts = LOG_RELPATH.split("/")
    if len(p.parts) > len(parts) and list(p.parts[-len(parts):]) == parts:
        return p.parents[len(parts) - 1]
    raise ActivityError("A tevékenységnapló helye csak <projekt>/%s lehet." % LOG_RELPATH)


class ActivityLog(object):
    """Egy projekt tevékenységnaplója a motor ``ActivityLog``-ja fölött. clock: datetime-ot (vagy kész
    ISO-szöveget) adó függvény (tesztekhez); alapból UTC most. ``path``: a napló (Path)."""

    def __init__(self, project_root, clock=None, relpath=LOG_RELPATH):
        if relpath != LOG_RELPATH:
            raise ActivityError("A tevékenységnapló helye rögzített: %s." % LOG_RELPATH)
        self.root = Path(os.path.realpath(str(project_root)))
        self.relpath = LOG_RELPATH
        self._log = _api.ActivityLog(str(self.root), clock=clock)
        self.path = Path(self._log.path)
        self.clock = self._log.clock

    def build(self, action, actor, argv=None, inputs=(), outputs=(), result=None, details=None):
        """A rekord a lánc-mezők (seq, prev) nélkül; minden ellenőrzéssel, írás nélkül."""
        return self._log.build(action, actor, argv, inputs, outputs, result, details)

    def _guard(self):
        """A napló útja a projekten belül maradjon (T7: a 07_ellenorzes/ nem lehet kivezető symlink)."""
        store.resolve_under(self.root, LOG_RELPATH)

    def append(self, action, actor, argv=None, inputs=(), outputs=(), result=None, details=None):
        """Új bejegyzés a lánc végére → a teljes, kiírt rekord (seq, prev kitöltve)."""
        self._guard()
        return self._log.append(action, actor, argv=argv, inputs=inputs, outputs=outputs, result=result,
                                details=details)

    def external_edit(self, rel, sha256, actor="external"):
        """A változásfigyelő által észlelt külső írás (4.16): actor 'external', a fájl új hash-ével."""
        self._guard()
        return self._log.external_edit(rel, sha256, actor=actor)

    def read(self):
        return self._log.read()

    def head(self):
        """{seq, hash} — az utolsó rekord (az audit-csomag activity_head-je); üresnél None."""
        return self._log.head()

    def verify(self, anchor=None):
        return self._log.verify(anchor)


def read_records(path):
    """A napló rekordjai (ellenőrzés nélkül). path: a napló útja vagy a projektmappa."""
    return _api.activity_read(str(_project_of(path)))


def verify_chain(path, anchor=None):
    """(ok, első hibás seq, üzenet) — a motor lánc-ellenőrzése. path: a napló útja vagy a projektmappa;
    anchor = {seq, hash}: a lánc tartalmazza-e ezt a rekordot (a záró bejegyzések törlése ellen)."""
    return _api.activity_verify(str(_project_of(path)), anchor)


def head(path):
    """{seq, hash} vagy None. path: a napló útja vagy a projektmappa."""
    return _api.activity_head(str(_project_of(path)))


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


def _crt_arg(arg):
    if any(c in arg for c in "\r\n\x00"):
        raise ValueError("sortörés nem adható át cmd-parancssorban")
    return _msvc_quote(arg) if (arg == "" or any(c in _CMD_SPECIAL for c in arg)) else arg


def _cmd_escape(line):
    """A cmd.exe rétege: a '%' kettőzve (batch-fájlban idézőjelben is kell), a metakarakterek
    '^'-pal ott, ahol a cmd szerint idézőjelen kívül vagyunk. A cmd minden '"'-nél vált (a CRT
    '\\"'-escape-jét nem ismeri), ezért az állapotot az egész soron át kell követni."""
    out, inq = [], False
    for ch in line:
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


def cmd_line(args):
    """Argumentumlista → rerun.cmd-sor (CRT-idézés + cmd-escape); sortörésnél ValueError."""
    return _cmd_escape(" ".join(_crt_arg(a) for a in args))


def cmd_quote(arg):
    """Egyetlen argumentum rerun.cmd-be (önálló sorként értelmezve)."""
    return cmd_line([arg])


def _safe_comment(text):
    return re.sub(r"[^A-Za-z0-9_.:\- ]", "_", text)


def _record_label(rec):
    return "seq %s - %s - %s" % (_safe_comment(str(rec.get("seq"))), _safe_comment(str(rec.get("action", ""))),
                                 _safe_comment(str(rec.get("ts", ""))))


def _commands(records):
    for rec in records:
        argv = rec.get("argv") if isinstance(rec, dict) else None
        if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv):
            yield rec, argv


def render_rerun_sh(records, cd_rel="."):
    lines = ["#!/bin/sh",
             "# MA-munkapad - ujrafuttato szkript (%s alapjan, a naplo sorrendjeben)" % SCHEMA,
             "# Hasznalat: sh rerun.sh   (felulirhato: MA_PYTHON=..., MA_PY=/ut/a/ma.py)",
             "set -eu",
             'cd "$(dirname "$0")/%s"' % cd_rel.replace('"', ""),
             'MA_PYTHON="${MA_PYTHON:-python3}"',
             'MA_PY="${MA_PY:-ma.py}"', ""]
    n = 0
    for rec, argv in _commands(records):
        kind, rest, prog = _split_program(argv)
        head = {"ma": '"$MA_PYTHON" "$MA_PY"', "py": '"$MA_PYTHON" ' + shlex.quote(prog),
                "exe": shlex.quote(prog)}[kind]
        lines.append("# " + _record_label(rec))
        lines.append(" ".join([head] + [shlex.quote(a) for a in rest]))
        n += 1
    if not n:
        lines.append("# nincs ujrafuttathato parancs a naploban")
    return "\n".join(lines) + "\n"


def render_rerun_cmd(records, cd_rel="."):
    lines = ["@echo off",
             "rem MA-munkapad - ujrafuttato szkript (%s alapjan, a naplo sorrendjeben)" % SCHEMA,
             "rem Hasznalat: rerun.cmd   (felulirhato: set MA_PYTHON=..., set MA_PY=C:\\ut\\ma.py)",
             "chcp 65001 >nul",
             "setlocal",
             'cd /d "%%~dp0%s"' % cd_rel.replace("/", "\\").replace('"', "").replace("%", "%%"),
             'if not defined MA_PYTHON set "MA_PYTHON=py -3"',
             'if not defined MA_PY set "MA_PY=ma.py"', ""]
    n = 0
    for rec, argv in _commands(records):
        lines.append("rem " + _record_label(rec))
        kind, rest, prog = _split_program(argv)
        try:
            if kind == "ma":
                line = '%MA_PYTHON% "%MA_PY%"' + (" " + cmd_line(rest) if rest else "")
            elif kind == "py":
                line = "%MA_PYTHON% " + cmd_line([prog] + rest)
            else:
                line = cmd_line([prog] + rest)
            lines.append(line)
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
