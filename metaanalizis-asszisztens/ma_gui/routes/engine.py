# -*- coding: utf-8 -*-
"""GET /api/engine — motorverzió, önteszt-jelvény, tudásbázis-állapot és a mértékek listája.

Átmeneti, minimális változat a motor-homlokzat (``metaelemzes.api.engine_info``, E1) előtt:
``facade: false``; az opció- és szabály-metaadat (``options``, ``rules``) a homlokzattal jön."""
import platform
import threading

import metaelemzes

from .. import __version__
from ..router import Result

SCHEMA = "szk.ma.engine-info/v1"
_FAMILIES = (("CONTINUOUS", "continuous"), ("PAIRED", "paired"), ("BINARY", "binary"),
             ("PROPORTION", "proportion"), ("CORRELATION", "correlation"), ("GENERIC", "generic"))
_lock = threading.Lock()
_measures = None


def measures():
    """A motor mértékei (effect_sizes metaadatai), egyszer felépítve; hibánál üres lista."""
    global _measures
    with _lock:
        if _measures is not None:
            return _measures
        try:
            from metaelemzes import effect_sizes as es
        except Exception:                                  # noqa: BLE001 — a motor hibája ne vigye el
            return []
        family = {}
        for attr, name in _FAMILIES:
            for m in getattr(es, attr, ()) or ():
                family.setdefault(m, name)
        labels = getattr(es, "MEASURE_LABELS", {}) or {}
        required = getattr(es, "REQUIRED_COLUMNS", {}) or {}
        ratio = set(getattr(es, "RATIO_MEASURES", ()) or ())
        out = []
        for m in getattr(es, "ALL_MEASURES", ()) or ():
            out.append({"id": m, "label": labels.get(m, m), "family": family.get(m),
                        "required_columns": list(required.get(m, ())), "ratio": m in ratio})
        _measures = out
        return out


def get_engine(req):
    app = req.app
    data = {
        "schema": SCHEMA,
        "engine_version": str(getattr(metaelemzes, "__version__", "")),
        "gui_version": __version__,
        "python": platform.python_version(),
        "facade": False,
        "selftest": app.selftest_info(),
        "kb": app.kb_info(),
        "measures": measures(),
        "options": {},
        "rules": [],
        "lang": app.lang,
    }
    return Result(data, SCHEMA)


def register(router):
    router.add("GET", "/api/engine", get_engine, schema=SCHEMA)
