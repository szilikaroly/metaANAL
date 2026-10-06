# -*- coding: utf-8 -*-
"""Tudásbázis-kereső (3.4, 3.5.7, 3.5.15, 7.7) — csak olvas, a motor-homlokzaton át (``api.kb_*``).

- ``GET /api/kb/search?q=&limit=&scope=rule,knowledge,chunk&source=`` (``api.kb_search``)
- ``GET /api/kb/item/<id>`` (``api.kb_show``; a szövegrész-azonosító ``#``-ét ``%23``-ként kell küldeni)
- ``GET /api/kb/rules?field=<mező>[&model=&k=&measure=]`` (``api.kb_rules_for_field``): a mezőre
  (results.json-kulcs, elemzési opció vagy V/P/X-kód) hivatkozó döntési szabályok — a felület ⓚ-jelvényei; a
  futás kontextusával (modell, k, hatásméret) csak a futásra illők (UX-06).

A teljes szöveg (``chunk``) helyi forrás: a felület „helyi forrás — nem exportálható” címkével
mutatja; pillanatképbe és auditba nem kerül. A keresőkifejezés nem kerül a naplóba."""
import re

from metaelemzes import api

from ..router import ApiError, Result
from ._common import int_arg

SEARCH_SCHEMA = "szk.ma.kb-search/v1"
ITEM_SCHEMA = "szk.ma.kb-item/v1"
RULES_SCHEMA = "szk.ma.kb-rules/v1"
SCOPES = ("rule", "knowledge", "chunk")
LOCAL_NOTE = {"hu": "helyi forrás — nem exportálható", "en": "local source — not for export"}
FIELD_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9_]{1,20}$")
RULES_RESPONSE = {
    "type": "object", "required": ["field", "items"],
    "properties": {"field": {"type": "string"},
                   "items": {"type": "array", "items": {"type": "object", "required": ["id"]}}},
}


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
    res = api.kb_search(q, limit, scopes, source, db=app.kb_db)
    return Result({"query": q, "scopes": list(scopes), "results": res, "local_only": True, "note": LOCAL_NOTE},
                  SEARCH_SCHEMA)


def get_item(req):
    app = req.app
    item_id = req.params["item_id"]
    if len(item_id) > 300:
        raise ApiError("BAD_REQUEST", "Túl hosszú azonosító.")
    app.kb_ready()
    item = api.kb_show(item_id, db=app.kb_db)
    if item is None:
        raise ApiError("NOT_FOUND", "Nincs ilyen azonosító a tudásbázisban.")
    item = dict(item)
    table = item.pop("_table", None)
    return Result({"id": item_id, "table": table, "item": item, "local_only": table == "chunk", "note": LOCAL_NOTE},
                  ITEM_SCHEMA)


def get_rules(req):
    app = req.app
    field = req.arg("field", max_len=80)
    if not field or not FIELD_RE.match(field):
        raise ApiError("BAD_REQUEST", "Hiányzó vagy érvénytelen mezőnév (field: betű, szám, '_', '.', '-'; "
                                      "legfeljebb 80 karakter).")
    context = {}
    model = req.arg("model", max_len=20)
    if model:
        if not MODEL_RE.match(model):
            raise ApiError("BAD_REQUEST", "Érvénytelen modellnév (model).")
        context["model"] = model
    measure = req.arg("measure", max_len=20)
    if measure:
        if not MODEL_RE.match(measure):
            raise ApiError("BAD_REQUEST", "Érvénytelen hatásméret-név (measure).")
        context["measure"] = measure
    if req.arg("k"):
        context["k"] = int_arg(req, "k", 0, 0, 10 ** 6)
    app.kb_ready()
    res = api.kb_rules_for_field(field, db=app.kb_db, context=context or None)
    return Result({"field": res.get("field", field), "items": list(res.get("items") or [])}, RULES_SCHEMA)


def register(router):
    router.add("GET", "/api/kb/search", get_search, schema=SEARCH_SCHEMA)
    router.add("GET", "/api/kb/item/<item_id>", get_item, schema=ITEM_SCHEMA)
    router.add("GET", "/api/kb/rules", get_rules, schema=RULES_SCHEMA, response_schema=RULES_RESPONSE)
