# -*- coding: utf-8 -*-
"""A munkapad HTTP-végpontjai (terv 3.4) modulonként; ``register_all(router)`` mindet felveszi.

session, a statikus felület, engine (``api.engine_info``), capabilities, project, privacy, table + import
+ provenance, documents + fileurl + /f/, validate + convert, specs, analyze + jobs, runs, prisma + studies,
audit, log, kb, changes; v1: értékelések, GRADE/SoF/Protokoll, kettős kinyerés, adapterek/ábra-export/composer,
Metaheadhunter. A motorhoz minden végpont a homlokzaton (``metaelemzes.api``) át fér hozzá (6.8); a Metaheadhunter a
saját parancssorát futtatja alfolyamatként (``python -m metaelemzes.headhunter … --json``).
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

from . import appraisal, appraisal_rob  # noqa: E402 — v1: értékelések (RoB-család, PROBAST+AI, TRIPOD+AI, AMSTAR 2)
MODULES += (appraisal, appraisal_rob)
from . import grade  # noqa: E402 — v1: GRADE, SoF, AMSTAR 2, Protokoll (grade_sof/_amstar2/_protocol is)
MODULES += (grade,)
from . import extraction_dual  # noqa: E402 — v1: kettős kinyerés (compare, reconcile, A/B import/export)
MODULES += (extraction_dual,)
from . import adapters, adapters_composer, adapters_figures  # noqa: E402 — v1: plugin-adapterek, ábra-export, composer
MODULES += (adapters, adapters_figures, adapters_composer)
from . import headhunter  # noqa: E402 — Metaheadhunter (meglévő metaanalízisek bányászata; a CLI-t futtatja)
MODULES += (headhunter,)


def register_all(router):
    mods = MODULES + ((export,) if export is not None and hasattr(export, "register") else ())
    for mod in mods:
        mod.register(router)
    return router
