# -*- coding: utf-8 -*-
"""Indító: ``python -m ma_gui --project DIR [--port N] [--no-browser] [--lang hu|en] [--idle-hours H]``.

A motor CLI ``gui`` alparancsát (``python ma.py gui``) az integrátor köti be; az a
``ma_gui.server.cli_main(argv)``-ot hívja. A worker-folyamat (spawn) miatt a ``__main__``-őr kötelező."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ma_gui.server import cli_main  # noqa: E402

if __name__ == "__main__":
    sys.exit(cli_main())
