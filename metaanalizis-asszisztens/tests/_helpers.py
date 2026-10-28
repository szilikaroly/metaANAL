# -*- coding: utf-8 -*-
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REF_PATH = os.path.join(HERE, "reference", "metafor_reference.json")

# A repó .claude/ forrása (ágensek, skill) csak a repóból futtatva van jelen; a telepített pluginban
# (<plugins>/cache/<marketplace>/<plugin>/<verzió>/) nincs — az ezeket olvasó dokumentációs tesztek ott kimaradnak.
REPO = os.path.dirname(ROOT)
HAS_REPO_SOURCES = os.path.isdir(os.path.join(REPO, ".claude", "agents"))


def split_cmdline(cmd):
    """A naplózott (újrafuttatható) parancssor szétbontása: POSIX-on shlex.split, Windows-on a cmd/CRT
    szabályai szerint (CommandLineToArgvW) — a cli._replay_command ott subprocess.list2cmdline-nal idéz."""
    if os.name != "nt":
        import shlex
        return shlex.split(cmd)
    import ctypes
    from ctypes import wintypes
    fn = ctypes.windll.shell32.CommandLineToArgvW
    fn.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    fn.restype = ctypes.POINTER(wintypes.LPWSTR)
    n = ctypes.c_int()
    argv = fn(cmd, ctypes.byref(n))
    try:
        return [argv[i] for i in range(n.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


def need_repo(obj):
    """skipUnless-dekorátor a repó .claude/ forrását olvasó tesztekhez (osztályra és metódusra is)."""
    import unittest
    return unittest.skipUnless(HAS_REPO_SOURCES, "plugin-telepítésből fut: a repó .claude/ forrása nincs jelen")(obj)


def load_reference():
    with open(REF_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def assert_close(tc, got, want, tol=1e-6, msg="", rel=False):
    """Abszolút VAGY relatív tolerancia (a nagyobbik).

    rel=True: csak relatív tolerancia, abszolút alsó korlát nélkül (|kapott − várt| <= tol·|várt|;
    várt = 0-nál pontos egyezés) — p-értékekhez: az 1e-26 nagyságú farok-valószínűséget az abszolút
    tolerancia nem ellenőrizné (a 0-ra alulcsorduló érték is átmenne)."""
    if want is None:
        tc.assertTrue(got is None or (isinstance(got, float) and math.isnan(got)), "%s: várt None, kapott %r" % (msg, got))
        return
    tc.assertIsNotNone(got, "%s: kapott None, várt %r" % (msg, want))
    if isinstance(want, float) and math.isinf(want):
        tc.assertTrue(math.isinf(got), msg)
        return
    diff = abs(got - want)
    bound = tol * abs(want) if rel else max(tol, tol * abs(want))
    tc.assertLessEqual(diff, bound, "%s: kapott %.10g, várt %.10g (eltérés %.3g, %s tolerancia %.3g)" % (
        msg, got, want, diff, "relatív" if rel else "abszolút/relatív", tol))
