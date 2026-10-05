# -*- coding: utf-8 -*-
"""Tudásbázis-kereső (3.4, 3.5.15, 7.7) — csak olvas, a ``metaelemzes.kb`` függvényein át.

- ``GET /api/kb/search?q=&limit=&scope=rule,knowledge,chunk&source=``
- ``GET /api/kb/item/<id>`` (a szövegrész-azonosító ``#``-ét ``%23``-ként kell küldeni)

A teljes szöveg (``chunk``) helyi forrás: a felület „helyi forrás — nem exportálható” címkével
mutatja; pillanatképbe és auditba nem kerül. A keresőkifejezés nem kerül a naplóba."""
from metaelemzes import kb

from ..router import ApiError, Result
from ._common import int_arg

SEARCH_SCHEMA = "szk.ma.kb-search/v1"
ITEM_SCHEMA = "szk.ma.kb-item/v1"
SCOPES = ("rule", "knowledge", "chunk")
LOCAL_NOTE = {"hu": "helyi forrás — nem exportálható", "en": "local source — not for export"}


def get_search(req):
    app = req.app
    q = req.arg("q", max_len=500)
    if q is None or not q.strip():
        raise ApiError("BAD_REQUEST", "Hiányzó keresőkifejezés (q).")
    limit = int_arg(req, "limit", 8, 1, 50)
    raw_scope = req.arg("scope", max_len=100)
    scopes = SCOPES
    if raw_scope:
        scopes = tuple(s.strip() for s in raw_scope.split(",") if s.strip())
        if not scopes or any(s not in SCOPES for s in scopes):
            raise ApiError("BAD_REQUEST", "Ismeretlen keresési kör (rule, knowledge, chunk).")
    source = req.arg("source", max_len=200) or None
    app.kb_ready()
    res = kb.search(q, limit, app.kb_db, scopes, source)
    if source and res.get("rule"):
        res["rule"] = [r for r in res["rule"]
                       if source in [x.strip() for x in (r.get("source_ids") or "").split(",")]]
    return Result({"query": q, "scopes": list(scopes), "results": res, "local_only": True, "note": LOCAL_NOTE},
                  SEARCH_SCHEMA)


def get_item(req):
    app = req.app
    item_id = req.params["item_id"]
    if len(item_id) > 300:
        raise ApiError("BAD_REQUEST", "Túl hosszú azonosító.")
    app.kb_ready()
    item = kb.show(item_id, app.kb_db)
    if item is None:
        raise ApiError("NOT_FOUND", "Nincs ilyen azonosító a tudásbázisban.")
    item = dict(item)
    table = item.pop("_table", None)
    return Result({"id": item_id, "table": table, "item": item, "local_only": table == "chunk", "note": LOCAL_NOTE},
                  ITEM_SCHEMA)


def register(router):
    router.add("GET", "/api/kb/search", get_search, schema=SEARCH_SCHEMA)
    router.add("GET", "/api/kb/item/<item_id>", get_item, schema=ITEM_SCHEMA)
