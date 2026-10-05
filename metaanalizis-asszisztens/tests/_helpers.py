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
