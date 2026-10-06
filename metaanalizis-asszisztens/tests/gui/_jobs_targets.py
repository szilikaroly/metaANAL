# -*- coding: utf-8 -*-
"""Csak teszthez: a ma_gui.jobs worker-folyamata által importálható célfüggvények."""
import os
import sys
import time
import warnings

MARKER = "TITKOS-CELLAERTEK-7f3a"


def echo(*args, **kwargs):
    return {"args": list(args), "kwargs": kwargs}


def sleep_then_return(seconds, value=None):
    time.sleep(seconds)
    return value


def raise_error(message="szándékos hiba"):
    raise ValueError(message)


def deep_raise(depth):
    if depth <= 0:
        raise RuntimeError("mély hiba vége")
    return deep_raise(depth - 1)


def return_big(nbytes):
    return "x" * nbytes


def return_unjsonable():
    return {1, 2, 3}


def call_exit(code=2):
    sys.exit(code)


def crash_hard(code=3):
    os._exit(code)


def get_pid():
    return os.getpid()


def noisy():
    print(MARKER)
    sys.stderr.write(MARKER + "\n")
    warnings.warn(MARKER)
    os.write(1, (MARKER + "\n").encode("ascii"))
    return "ok"


def _private():
    return "nem hívható"
