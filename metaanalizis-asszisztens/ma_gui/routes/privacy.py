# -*- coding: utf-8 -*-
"""GET /api/privacy · POST /api/privacy/apply — adatvédelmi állapot és védelmek (7.4, 7.5, 3.5.2).

- ``GET /api/privacy[?data_class=X]``: ``privacy.status()``; az X „mi lenne, ha” előnézet (nem ír).
- ``POST /api/privacy/apply``: ``{action: gitignore | deny | precommit, dry_run: true}`` → terv
  (diff, base_sha256); ``{action, confirm: true, base_sha256?}`` → beírás. Írás CSAK kifejezett
  ``confirm: true``-val (a felhasználó kattintására, diff-előnézet után); a vault konfigurációjához
  a munkapad nem nyúl."""
from .. import privacy
from ..router import ApiError, Result
from ._common import body_bool

SCHEMA = "szk.ma.privacy/v1"
PLAN_SCHEMA = "szk.ma.privacy-plan/v1"
ACTIONS = ("gitignore", "deny", "precommit")
REQUEST_SCHEMA = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {"enum": list(ACTIONS)},
        "dry_run": {"type": "boolean"},
        "confirm": {"type": "boolean"},
        "base_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
    },
    "additionalProperties": False,
}


def get_privacy(req):
    app = req.app
    preview = req.arg("data_class", max_len=4)
    if preview:
        dc = privacy.normalize_data_class(preview)
        data = dict(app.compute_privacy(dc))
        data["preview"] = True
    else:
        data = dict(app.refresh_privacy())
        data["preview"] = False
    data["project_data_class"] = app.data_class()
    return Result(data, SCHEMA)


def post_apply(req):
    app = req.app
    body = req.json_object()
    action = body["action"]
    root = str(app.project_root)
    if body_bool(body, "dry_run"):
        if action == "gitignore":
            plan = privacy.plan_gitignore(root)
        elif action == "deny":
            plan = privacy.plan_claude_deny(root)
        else:
            plan = privacy.plan_precommit_guard(root)
        plan = dict(plan)
        plan.update({"action": action, "dry_run": True})
        return Result(plan, PLAN_SCHEMA)
    if body.get("confirm") is not True:
        raise ApiError("BAD_REQUEST", "Az adatvédelmi védelem beírásához kifejezett megerősítés kell "
                                      "(confirm: true) a diff-előnézet után.")
    kw = {"base_sha256": body["base_sha256"]} if "base_sha256" in body else {}
    outputs = []
    if action == "gitignore":
        res = privacy.apply_gitignore(root, **kw)
        outputs.append({"path": privacy.GITIGNORE_REL, "sha256": res.get("sha256")})
    elif action == "deny":
        res = privacy.apply_claude_deny(root, **kw)
        outputs.append({"path": privacy.CLAUDE_SETTINGS_REL, "sha256": res.get("sha256")})
    else:
        res = privacy.install_precommit_guard(root)
    res = dict(res)
    res.update({"action": action, "dry_run": False})
    warnings = []
    rec = app.log_activity("privacy.apply", outputs=outputs,
                           details={"action": action, "changed": bool(res.get("changed", True))})
    if rec is None:
        warnings.append(app.ACTIVITY_WARNING)
    res["status"] = app.refresh_privacy()
    return Result(res, PLAN_SCHEMA, warnings=warnings)


def register(router):
    router.add("GET", "/api/privacy", get_privacy, schema=SCHEMA)
    router.add("POST", "/api/privacy/apply", post_apply, schema=PLAN_SCHEMA, request_schema=REQUEST_SCHEMA)
