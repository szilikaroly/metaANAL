# -*- coding: utf-8 -*-
"""GET /api/engine — a motor leírása a homlokzatból (``metaelemzes.api.engine_info``, E1, terv 3.2).

``data``: a motorverzió, a Python-verzió, a szerződések (név → főverzió), a mértékek (kötelező
oszlopokkal), az opció-metaadat (az argparse-ból és a ``pipeline.DEFAULTS``-ból generálva — a felület
űrlapja ebből épül) és a V/P/X-szabálylista; plusz a szerver saját állapota: az indításkor
alfolyamatban futó motor-önteszt jelvénye (``selftest``) és a tudásbázis-építés állapota (``kb``).
``facade: true`` — a felület innen tudja, hogy a homlokzat elérhető."""
import platform

from .. import __version__
from ..router import Result

SCHEMA = "szk.ma.engine-info/v1"


def get_engine(req):
    app = req.app
    info = app.engine_info()
    kb = {k: v for k, v in dict(info.get("kb") or {}).items() if k != "db"}   # helyi abszolút út nélkül
    kb.update(app.kb_info())
    data = dict(info)
    data.update({
        "schema": SCHEMA,
        "gui_version": __version__,
        "python": info.get("python") or platform.python_version(),
        "facade": True,
        "selftest": app.selftest_info(),
        "kb": kb,
        "lang": app.lang,
    })
    return Result(data, SCHEMA)


def register(router):
    router.add("GET", "/api/engine", get_engine, schema=SCHEMA)
