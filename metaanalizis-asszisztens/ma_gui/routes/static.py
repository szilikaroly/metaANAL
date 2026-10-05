# -*- coding: utf-8 -*-
"""GET / — a statikus felület (``ma_gui/static/index.html``) válaszonként új CSP-nonce-szal.

A HTML-ben a ``{{CSP_NONCE}}`` helyeket a szerver cseréli; ugyanaz a nonce kerül a CSP-fejlécbe.
Az oldal adatot és tokent nem tartalmaz (T4): azt a lap tokennel, az API-n kéri le."""
from .. import security
from ..router import ApiError, Response

NONCE_PLACEHOLDER = b"{{CSP_NONCE}}"


def get_index(req):
    html = req.app.index_html()
    if html is None:
        raise ApiError("NOT_FOUND", "A felület fájlja (ma_gui/static/index.html) hiányzik.")
    nonce = security.csp_nonce()
    body = html.replace(NONCE_PLACEHOLDER, nonce.encode("ascii"))
    return Response(body, "text/html; charset=utf-8", kind="html", nonce=nonce)


def register(router):
    router.add("GET", "/", get_index)
