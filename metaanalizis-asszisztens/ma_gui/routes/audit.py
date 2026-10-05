# -*- coding: utf-8 -*-
"""GET /api/audit/project[?stage=S08] — a motor X-szabályai (terv 3.4, 3.5.1, 4.15, 6.4):
``api.project_audit`` → ``szk.ma.project-audit/v1``. A „következő lépések” forrása: minden találat a
motor szövegével, a javasolt paranccsal (``suggested_command``) és KB-hivatkozással; a szerver nem
értékel, csak továbbít. A FINAL audit-kapu (``POST /api/log/checkpoint`` ``audit_gate: true``) ugyanezt
a motor-ellenőrzést használja."""
from metaelemzes import api

from ..router import ApiError, Result
from . import _contracts

SCHEMA = "szk.ma.project-audit/v1"
STAGE_MAX = 20


def get_audit(req):
    app = req.app
    app.require_open()
    stage = req.arg("stage", max_len=STAGE_MAX) or None
    if stage is not None and not all(c.isalnum() or c in "-,–" for c in stage):
        raise ApiError("BAD_REQUEST", "Érvénytelen szakaszkód (S00–S14, tartomány vagy FINAL).")
    return Result(api.project_audit(str(app.project_root), stage=stage), SCHEMA)


def register(router):
    router.add("GET", "/api/audit/project", get_audit, schema=SCHEMA, response_schema=_contracts.ref(SCHEMA))
