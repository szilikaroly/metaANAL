# -*- coding: utf-8 -*-
"""GET /api/capabilities · POST /api/capabilities/refresh — a képesség-mátrix (5.2, 3.5.16).

Az adat a ``ma_gui.caps.Caps.report()`` kimenete (``szk.ma.capabilities-matrix/v1``). A refresh
újraszondázza a pluginokat (``plugins``: opcionális névlista)."""
from ..router import ApiError, Result

SCHEMA = "szk.ma.capabilities-matrix/v1"
REFRESH_SCHEMA = {
    "type": "object",
    "properties": {"plugins": {"type": "array", "items": {"type": "string", "maxLength": 64}}},
    "additionalProperties": False,
}


def get_capabilities(req):
    return Result(req.app.caps.report(), SCHEMA)


def post_refresh(req):
    caps = req.app.caps
    body = req.json_object({})
    names = body.get("plugins")
    if names is not None:
        unknown = [n for n in names if n not in caps.plugin_names]
        if unknown or len(names) > 16:
            raise ApiError("BAD_REQUEST", "Ismeretlen plugin a listában (ismert: %s)." % ", ".join(caps.plugin_names))
    caps.refresh(names)
    return Result(caps.report(), SCHEMA)


def register(router):
    router.add("GET", "/api/capabilities", get_capabilities, schema=SCHEMA)
    router.add("POST", "/api/capabilities/refresh", post_refresh, schema=SCHEMA, request_schema=REFRESH_SCHEMA)
