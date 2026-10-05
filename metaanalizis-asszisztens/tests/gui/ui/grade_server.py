#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Segéd a grade.spec.js valódi-szerveres részéhez (GRADE / SoF / Protokoll a VALÓDI munkapad-szerverrel).

    python3 tests/gui/ui/grade_server.py serve <ideiglenes mappa> [--stub | --no-engine]
        Ideiglenes projekt (BCG → o1, Normand → o2; tests/gui/test_routes_harness.make_project), a szerver
        (ma_gui.server.App, port 0, a válaszok sémán ellenőrizve), és egy commit-futás az o1-re a szerver saját
        API-ján át. --stub: a motor v1 GRADE/SoF/AMSTAR 2 függvényei helyén a TESZT-CSONK
        (tests/gui/_grade_engine_stub.py) — a szerver-logika és a felület így végigvihető; nélküle a motor-tár
        hiánya (424) a --no-engine-nel látszik (alapból a valódi motor fut). Az első stdout-sor JSON: {url, base, proj, run_id}. stdin: 'code' → új indítókód;
        'quit' (vagy EOF) → leállítás."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(GUI))
for p in (ROOT, GUI):
    if p not in sys.path:
        sys.path.insert(0, p)


def serve(base, stub, no_engine=False):
    import test_routes_harness as H
    if stub or no_engine:
        # a motor MÁR tartalmazza a GRADE/SoF/AMSTAR 2 függvényeket: a „motor nélkül” forgatókönyvhöz mind kikapcsolva;
        # a csonk-módban a csonkban nem szereplők is (különben a valódi grade_record / sof_csv a csonk dokumentumain
        # futna) — a lista a test_v1_grade_routes.ALL_ENGINE-é
        import _grade_engine_stub as STUB
        from test_v1_grade_routes import ALL_ENGINE
        names = list(ALL_ENGINE) if no_engine else [n for n in ALL_ENGINE if n not in STUB.FUNCS]
        for p in STUB.absent(names):
            p.start()
    if stub:
        for p in STUB.patch():
            p.start()
    proj, home = H.make_project(base)
    srv = H.Srv(proj, home, base, name="ui", validate_responses=True,
                log_stream=open(os.path.join(base, "server.log"), "w", encoding="utf-8"))
    sp = H.spec("o1_primary", outcome="o1")
    srv.ok("PUT", "/api/specs/o1_primary", sp)
    job = srv.job(srv.ok("POST", "/api/analyze", {"mode": "commit", "spec": sp, "client_seq": 1})["data"])
    run_id = job["result"]["run"]["run_id"]
    url = srv.app.launch_url(srv.app.new_launch_code())
    print(json.dumps({"url": url, "base": url.split("/#")[0], "proj": proj, "run_id": run_id,
                      "log": os.path.join(base, "server.log")}), flush=True)
    try:
        for line in sys.stdin:
            cmd = line.strip()
            if cmd == "code":
                print(json.dumps({"code": srv.app.new_launch_code()}), flush=True)
            elif cmd == "quit":
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
