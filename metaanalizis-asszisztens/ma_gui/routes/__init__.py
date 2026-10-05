# -*- coding: utf-8 -*-
"""A munkapad HTTP-végpontjai (terv 3.4) modulonként; ``register_all(router)`` mindet felveszi.

session, a statikus felület, engine (``api.engine_info``), capabilities, project, privacy, table + import
+ provenance, documents + fileurl + /f/, validate + convert, specs, analyze + jobs, runs, prisma + studies,
audit, log, kb, changes. A motorhoz minden végpont a homlokzaton (``metaelemzes.api``) át fér hozzá (6.8).
Az export-végpontok (``routes/export.py``: audit-csomag, pillanatkép) külön modulban élnek; ha a modul
jelen van, ugyanígy felvesszük."""
import os

from . import (analyze, audit, caps, changes, documents, engine, kb, log, prisma, privacy, project, runs, session,
               specs, static, table, validate)

if os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "export.py")):
    from . import export                    # az export-végpontok külön modulban (ha jelen vannak)
else:                                       # pragma: no cover — a modul hiánya nem hiba
    export = None

MODULES = (session, static, engine, caps, project, privacy, table, documents, validate, specs, analyze, runs,
           prisma, audit, log, kb, changes)


def register_all(router):
    mods = MODULES + ((export,) if export is not None and hasattr(export, "register") else ())
    for mod in mods:
        mod.register(router)
    return router
