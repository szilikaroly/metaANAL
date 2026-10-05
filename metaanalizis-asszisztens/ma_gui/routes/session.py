# -*- coding: utf-8 -*-
"""POST /api/session — indítókód → munkamenet-token (T4), illetve a második indítás új indítókódja.

Törzs: ``{"launch_code": "<kód>"}`` → ``data.token`` (egyszer használható, 60 s-os kód), vagy
``{"relaunch_key": "<kulcs>"}`` → ``data.launch_code`` (a futásidejű mappa 0600-as admin.key-jével;
csak a projekt második indítása használja, 2.2). A token nem kerül lemezre és naplóba."""
from .. import runtime, security
from ..router import ApiError, Result

SCHEMA = "szk.ma.session/v1"
REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "launch_code": {"type": "string", "maxLength": 256},
        "relaunch_key": {"type": "string", "maxLength": 256},
    },
    "additionalProperties": False,
}
MSG_CODE = ("Az indítókód lejárt vagy már felhasználták. Új kódot a munkapad indító parancsának újbóli "
            "futtatása ad (python -m ma_gui --project <mappa>).")
MSG_KEY = "Érvénytelen újraindító kulcs."


def post_session(req):
    app = req.app
    body = req.json_object()
    code, key = body.get("launch_code"), body.get("relaunch_key")
    if (code is None) == (key is None):
        raise ApiError("BAD_REQUEST", "Pontosan egy mező adható meg: launch_code vagy relaunch_key.")
    if code is not None:
        token = app.security.exchange_launch_code(code)
        if token is None:
            raise ApiError("FORBIDDEN", MSG_CODE)
        return Result({"token": token, "header": security.TOKEN_HEADER, "expires_in_s": None, "user": None}, SCHEMA)
    if not app.check_admin_key(key):
        raise ApiError("FORBIDDEN", MSG_KEY)
    new_code = app.security.new_launch_code()
    return Result({"launch_code": new_code, "url": runtime.launch_url(app.port, new_code, app.lang),
                   "expires_in_s": security.LAUNCH_CODE_TTL}, SCHEMA)


def register(router):
    router.add("POST", "/api/session", post_session, schema=SCHEMA, request_schema=REQUEST_SCHEMA)
