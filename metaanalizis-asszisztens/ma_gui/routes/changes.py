# -*- coding: utf-8 -*-
"""GET /api/changes?since=<rev>[&wait=<s>] — long-poll változásjelzés (2.2).

A válasz ``{rev, changed, external, reset}`` (``store.ChangeWatcher``): vár, amíg ``since`` után
változás lesz, legfeljebb 25 s-ig (``wait`` ennél kisebbre veheti). ``since`` nélkül azonnal a
mostani rev-et adja (``reset: true``). Nem számít felhasználói aktivitásnak (tétlenségi leállás)."""
from ..router import Result
from ._common import num_arg

SCHEMA = "szk.ma.changes/v1"


def get_changes(req):
    app = req.app
    since = req.arg("since", max_len=20)
    wait = num_arg(req, "wait", app.longpoll_max, 0.0, app.longpoll_max)
    if since is None or since == "":
        return Result(app.watcher.changes_since(None), SCHEMA)
    if not since.isdigit():
        return Result(app.watcher.changes_since(None), SCHEMA)
    res = app.wait_changes(int(since), wait)
    return Result(res, SCHEMA)


def register(router):
    router.add("GET", "/api/changes", get_changes, schema=SCHEMA, activity=False)
