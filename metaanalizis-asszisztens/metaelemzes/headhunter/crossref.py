# -*- coding: utf-8 -*-
"""Crossref REST kliens (opcionális, automatikus tartalék) — DOI-lekérés és bibliográfiai keresés.

Kezdőknek: a Crossref a DOI-k nyilvántartója; kulcs nem kell, az udvarias azonosítás ``mailto``
(``MA_CONTACT_EMAIL``; naplóból/kazettából kivágva). Ebből a fejlesztői környezetből a Crossref BLOKKOLT —
ilyenkor minden hívás ``SourceUnavailable("crossref", "unreachable")``-t ad, és a feloldás (L4) a többi
forrással folytatódik (H014). A felhasználó gépén általában működik.

Kényelmi változatok: ``try_work`` / ``try_search`` sosem dobnak elérhetetlenség miatt — ``(eredmény, hiba)``
párt adnak, így a hívó egyszerűen kihagyhatja a Crossrefet.
"""

from __future__ import absolute_import

import urllib.parse

from . import net
from .net import BaseClient, SourceUnavailable, get_env, get_path, as_list, to_int, norm_doi

SOURCE = "crossref"
PLATFORM = "Crossref REST API"
BASE = "https://api.crossref.org/"
PROBE_DOI = "10.1136/bmj.n71"  # PRISMA 2020


def work_record(m):
    """Egy Crossref-munka normalizált alakja (bibliográfiai tények; az absztraktot eldobjuk)."""
    authors = []
    for a in as_list(m.get("author")):
        fam = a.get("family")
        if fam:
            authors.append(("%s %s" % (fam, "".join(p[0] for p in (a.get("given") or "").split() if p))).strip())
        elif a.get("name"):
            authors.append(a["name"])
    year = None
    for key in ("published-print", "published-online", "issued", "published"):
        parts = get_path(m, key, "date-parts", 0)
        if parts and parts[0]:
            year = to_int(parts[0])
            break
    updates = [u for u in as_list(m.get("update-to")) if isinstance(u, dict)]
    relation = m.get("relation") or {}
    return {
        "doi": norm_doi(m.get("DOI")),
        "title": (as_list(m.get("title")) or [None])[0],
        "authors": authors[:50],
        "authors_truncated": len(authors) > 50,
        "first_author": authors[0] if authors else None,
        "year": year,
        "journal": (as_list(m.get("short-container-title")) or as_list(m.get("container-title")) or [None])[0],
        "volume": m.get("volume"),
        "issue": m.get("issue"),
        "pages": m.get("page"),
        "type": m.get("type"),
        "issn": as_list(m.get("ISSN")),
        "updates": [{"type": u.get("type"), "doi": norm_doi(u.get("DOI"))} for u in updates],
        "is_retraction_notice": any((u.get("type") or "").lower() == "retraction" for u in updates),
        "has_preprint": bool(relation.get("has-preprint")),
        "score": m.get("score"),
    }


class Client(BaseClient):
    """Crossref kliens (a 20.5 szerződés szerinti ``work`` metódussal)."""

    SOURCE = SOURCE
    PLATFORM = PLATFORM

    def _params(self, params=()):
        params = list(params)
        mail = get_env(net.ENV_CONTACT_EMAIL, self.env)
        if mail:
            params.append(("mailto", mail))
        return params

    def work(self, doi):
        """Egy munka (normalizált dict) vagy ``None`` (nincs ilyen DOI). Elérhetetlenségnél
        ``SourceUnavailable`` — a ``try_work`` ezt elnyeli."""
        d = norm_doi(doi)
        if not d:
            return None
        url = BASE + "works/" + urllib.parse.quote(d, safe="/:;()-._")
        resp = self.http.get(SOURCE, url, params=self._params(), accept="json", allow_status=(400,))
        if resp.status != 200:
            return None
        msg = resp.json().get("message") or {}
        rec = work_record(msg)
        return rec if rec["doi"] == d else None

    def search_bibliographic(self, text, rows=5):
        """Bibliográfiai keresés (``query.bibliographic``) — pontszámmal; csak JAVASLAT, a feloldást a hívó
        cím-hasonlósággal ellenőrzi (TERV 7. fejezet, 7. lépés)."""
        params = self._params([("query.bibliographic", text), ("rows", int(rows)),
                               ("select", "DOI,title,author,issued,container-title,short-container-title,volume,"
                                          "issue,page,type,ISSN,score,update-to,published-print,published-online")])
        data = self.http.get(SOURCE, BASE + "works", params=params, accept="json").json()
        return [work_record(m) for m in as_list(get_path(data, "message", "items"))]

    def try_work(self, doi):
        """``(rekord|None, SourceUnavailable|None)`` — sosem dob elérhetetlenség miatt."""
        return net.safe_call(self.work, doi)

    def try_search(self, text, rows=5):
        return net.safe_call(self.search_bibliographic, text, rows=rows)

    def _probe(self):
        rec = self.work(PROBE_DOI)
        if rec:
            return self._result("ok", http_status=200)
        return self._result("unreachable", detail_text={"hu": "A próbalekérés nem adott találatot.",
                                                        "en": "The probe lookup returned nothing."})


__all__ = ["Client", "work_record", "SourceUnavailable"]
