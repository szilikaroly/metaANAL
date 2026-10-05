# -*- coding: utf-8 -*-
"""A munkapad HTTP-végpontjai (terv 3.4) modulonként; ``register_all(router)`` mindet felveszi.

Most: session, a statikus felület, engine (homlokzat nélküli minimál), capabilities, project,
privacy, table + import + provenance, documents + fileurl + /f/, log, kb, changes. A motor-
homlokzatra (``metaelemzes.api``, E1) épülők (validate, convert, compare, analyze, runs, …) később."""
from . import caps, changes, documents, engine, kb, log, privacy, project, session, static, table

MODULES = (session, static, engine, caps, project, privacy, table, documents, log, kb, changes)


def register_all(router):
    for mod in MODULES:
        mod.register(router)
    return router
