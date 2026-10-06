#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Segéd az extraction_dual.spec.js valódi-szerveres részéhez (kettős kinyerés a VALÓDI munkapad-szerverrel és a
termék-builddel).

    python3 tests/gui/ui/extraction_dual_server.py serve <ideiglenes mappa> [--stub] [--no-engine]
        Ideiglenes projekt (BCG → o1, Normand → o2; tests/gui/test_routes_harness.make_project), a szerver
        (ma_gui.server.App, port 0, a válaszok sémán ellenőrizve). Az o1 A tábláját a szerver API-ján át importálja
        (a B-t a felület importálja). --stub: ha a homlokzatban még nincs ``compare``, a motor v1 modulja
        (metaelemzes/kettos.py) kerül a helyére, ennek hiányában a TESZT-CSONK (tests/gui/_extraction_engine_stub.py);
        --no-engine: a motor-függvény biztosan hiányzik (a 424-es út). Az első stdout-sor JSON: {url, base, proj,
        b_csv (a B tábla szövege), engine: 'facade'|'module'|'stub'|'none'}. stdin: 'quit' (vagy EOF) → leállítás."""
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(GUI))
for p in (ROOT, GUI, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def serve(base, stub, no_engine):
    from unittest import mock
    import test_routes_harness as H
    import gen_extraction_fixtures as GEN
    from metaelemzes import api
    from ma_gui.routes import extraction_dual_common as C
    import contextlib
    names = set(n for k in C.ENGINE for n in (C.ENGINE[k][0],) + tuple(C.ENGINE[k][1]))
    stack = contextlib.ExitStack()
    if no_engine:
        for n in names:
            if hasattr(api, n):
                stack.enter_context(mock.patch.object(api, n, None))
        mode = "none"
    elif stub:
        import _extraction_engine_stub as STUB
        mode = GEN.engine_patches(stack, mock, C, STUB)     # facade | module (metaelemzes/kettos.py) | stub
    else:
        mode = GEN.engine_source(C) if C.engine_fn("compare") is not None else "none"
    proj, home = H.make_project(base)
    srv = H.Srv(proj, home, base, name="ui", validate_responses=True,
                log_stream=open(os.path.join(base, "server.log"), "w", encoding="utf-8"))
    a, b = GEN.o1_tables()
    stack.__enter__()
    srv.ok("POST", "/api/kettos/import", {"outcome": "o1", "side": "A", "rater": "SzK",
                                          "content_b64": base64.b64encode(GEN.csv_text(a).encode("utf-8")).decode("ascii")})
    url = srv.app.launch_url(srv.app.new_launch_code())
    print(json.dumps({"url": url, "base": url.split("/#")[0], "proj": proj, "b_csv": GEN.csv_text(b), "engine": mode,
                      "log": os.path.join(base, "server.log")}, ensure_ascii=False), flush=True)
    try:
        for line in sys.stdin:
            if line.strip() == "quit":
                break
    finally:
        srv.stop()


def main(argv):
    if len(argv) >= 2 and argv[0] == "serve":
        serve(argv[1], "--stub" in argv[2:], "--no-engine" in argv[2:])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
