# -*- coding: utf-8 -*-
"""A motor szerződései (``metaelemzes/contracts/*.schema.json``) a kérés- és válaszséma-ellenőrzéshez
(terv 3.1, 4.0): a ``schema_lite`` regiszterébe töltve, ``$id``-jük (``urn:szk:contract:…``) szerint.

A sémafájlokat csak OLVASSUK (JSON); a motor kódját ez a modul nem importálja (6.8). ``ref(név)`` egy
``{"$ref": "urn:szk:contract:ma.validation:1"}`` alakú hivatkozást ad a szerződés nevéből
(``szk.ma.validation/v1``)."""
import re
import threading
from pathlib import Path

from .. import schema_lite

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "metaelemzes" / "contracts"
_NAME_RE = re.compile(r"^szk\.([a-z0-9][a-z0-9.\-]*)/v(\d+)$")
_lock = threading.Lock()
_registry = None


def registry():
    """{$id: séma} — a motor összes szerződése (egyszer betöltve). Hiányzó mappa: üres regiszter."""
    global _registry
    with _lock:
        if _registry is None:
            reg = {}
            if CONTRACTS_DIR.is_dir():
                schema_lite.load_schema_dir(CONTRACTS_DIR, reg)
            _registry = reg
        return _registry


def urn(name):
    """'szk.ma.validation/v1' → 'urn:szk:contract:ma.validation:1'."""
    m = _NAME_RE.match(name)
    if not m:
        raise ValueError("ismeretlen szerződésnév: %s" % name)
    return "urn:szk:contract:%s:%s" % m.groups()


def ref(name, nullable=False):
    """Hivatkozás a szerződésre (``nullable``: a null is elfogadott)."""
    target = {"$ref": urn(name)}
    if nullable:
        return {"oneOf": [{"type": "null"}, target]}
    return target


def available(name):
    """Megvan-e a szerződés a motor contracts/ mappájában."""
    try:
        return urn(name) in registry()
    except ValueError:
        return False
