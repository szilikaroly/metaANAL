# -*- coding: utf-8 -*-
"""A Metaheadhunter fejlesztői fixture-jei (ma_gui/web/fixtures/headhunter_*.json): generátor + sodródás-őr.

A fixture-ök a VALÓDI munkapad-szerver headhunter-végpontjainak válaszai egy SZINTETIKUS projekten
(test_headhunter_routes.build_project: a PMID-ek 99…-kezdetűek, a címek „(synthetic)” jelölésűek — nem valós
közlemények), amelyen a valódi CLI offline lépései futottak (átfedés, egyesítés, PRISMA, frissítés --offline).

    python3 tests/gui/test_headhunter_fixtures.py --write     # újragenerálás
    python3 -m unittest tests/gui/test_headhunter_fixtures.py # sodródás-őr: a fixture-ök kulcsai = a mostani route-éi

Az ellenőrzés a boríték alakját (4.2), a `data` legfelső kulcsait végpontonként, és azt nézi, hogy a fixture-ökben
nincs abszolút út, kulcs vagy e-mail. A számok és időbélyegek futásonként változhatnak (a motor és a CLI adja őket)."""
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_headhunter_routes as R  # noqa: E402
import test_routes_harness as H  # noqa: E402

FIXTURES = os.path.join(H.ROOT, "ma_gui", "web", "fixtures")
META = {"engine": "fixture", "elapsed_ms": 0, "project_rev": None, "request_id": "fx-headhunter"}
NOTE = ("Szintetikus Metaheadhunter-projekt (a PMID-ek 99…-kezdetűek, a címek „(synthetic)” jelölésűek — NEM valós "
        "közlemények). Generálja: python3 tests/gui/test_headhunter_fixtures.py --write")
GETS = (
    ("headhunter_status.json", [("/api/headhunter/status", None), ("/api/headhunter/sources", {"lang": "hu"}),
                                ("/api/headhunter/sources", {"lang": "en"})]),
    ("headhunter_reviews.json", [("/api/headhunter/reviews", None), ("/api/headhunter/reviews", {"status": "selected"}),
                                 ("/api/headhunter/reviews/%s" % R.RV_A, None), ("/api/headhunter/reviews/%s" % R.RV_B, None),
                                 ("/api/headhunter/reviews/%s" % R.RV_C, None)]),
    ("headhunter_studies.json", [("/api/headhunter/proposals", None), ("/api/headhunter/proposals", {"status": "pending"}),
                                 ("/api/headhunter/studies", None)]),
    ("headhunter_overlap.json", [("/api/headhunter/overlap", None)]),
    ("headhunter_merge.json", [("/api/headhunter/merged", None), ("/api/headhunter/prisma", None),
                               ("/api/headhunter/update", None), ("/api/headhunter/decisions", None)]),
)


def _qs(query):
    return ("?" + "&".join("%s=%s" % kv for kv in sorted(query.items()))) if query else ""


def _route(srv, path, query, method="GET", body=None, headers=()):
    st, hdrs, env = srv.call(method, path + _qs(query), body, headers)
    assert isinstance(env, dict) and env.get("ok"), (path, st, env)
    env = dict(env, meta=META)
    out = {"method": method, "path": path, "status": st, "envelope": env}
    if query:
        out["query"] = query
    if hdrs.get("etag"):
        out["etag"] = hdrs["etag"].strip('"')
    return out


def _scrub(obj, roots):
    """Abszolút utak → <projekt> (a route maga is kitakarja; ez a második háló a tmp-mappa többi részére)."""
    text = json.dumps(obj, ensure_ascii=False)
    for r in roots:
        text = text.replace(r, "<projekt>")
    return json.loads(text)


def generate():
    """{fájlnév: fixture-dokumentum} a szintetikus projektből (nem ír lemezre)."""
    tmp = H.tmpdir("ma_hh_fx_")
    try:
        proj, home = H.make_project(tmp)
        R.build_project(proj)
        srv = R.HHSrv(proj, home, tmp, name="fx")
        try:
            for step, opts in (("overlap", {"level": "study", "csv": True}), ("merge", {}), ("prisma", {}),
                               ("update_search", {"offline": True})):
                job = srv.run(step, opts)
                assert job["status"] == "done", job
            out = {}
            for name, reqs in GETS:
                out[name] = {"description": NOTE, "routes": [_route(srv, p, q) for p, q in reqs]}
            # feladat-életciklus: a valódi kész pillanatkép + egy „fut” változata folyamatnaplóval
            done = srv.run("prisma")
            running = dict(done, status="running", exit_code=None, ok=None, cancellable=True, message=None,
                           warnings=[], errors=[], pending=[], next=None, data=None,
                           progress=[{"ts": "2026-10-05T10:00:00Z", "step": "find_reviews", "phase": "search",
                                      "done": 40, "total": 120, "source": "pubmed",
                                      "message": {"hu": "Keresés a PubMedben…", "en": "Searching PubMed…"}}])
            env = lambda data: {"ok": True, "schema": "szk.ma.hh-job/v1", "data": data, "warnings": [],  # noqa: E731
                                "meta": META}
            etag = srv.ok("GET", "/api/headhunter/reviews/%s" % R.RV_A)["_etag"]
            dec = _route(srv, "/api/headhunter/decide", None, "POST",
                         {"kind": "decide", "target": R.RV_A + "#c0003", "value": "include"}, [("If-Match", etag)])
            out["headhunter_actions.json"] = {"description": NOTE, "routes": [
                {"method": "POST", "path": "/api/headhunter/run", "status": 202, "envelope": env(running)},
                {"method": "GET", "path": "/api/headhunter/runs/*", "sequence": [
                    {"status": 200, "envelope": env(running)}, {"status": 200, "envelope": env(done)}]},
                {"method": "POST", "path": "/api/headhunter/runs/*/cancel", "status": 200,
                 "envelope": env(dict(running, cancel_requested=True, cancel_written=True))},
                {"method": "POST", "path": "/api/headhunter/decide", "status": 200, "envelope": dec["envelope"]},
            ]}
            roots = sorted({proj, os.path.realpath(proj), tmp, home}, key=len, reverse=True)
            return {k: _scrub(v, roots) for k, v in out.items()}
        finally:
            srv.stop()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def write(docs):
    for name, doc in sorted(docs.items()):
        with open(os.path.join(FIXTURES, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")


def _keys(route):
    if "sequence" in route:
        return [sorted((s["envelope"].get("data") or {}).keys()) for s in route["sequence"]]
    return sorted((route["envelope"].get("data") or {}).keys())


class FixtureDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fresh = generate()

    def test_files_exist_and_envelopes_are_valid(self):
        for name in self.fresh:
            path = os.path.join(FIXTURES, name)
            self.assertTrue(os.path.isfile(path), "hiányzik: %s — futtasd: python3 %s --write" % (name, __file__))
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            for r in doc["routes"]:
                steps = r.get("sequence") or [r]
                for s in steps:
                    env = s["envelope"]
                    self.assertTrue(env["ok"])
                    self.assertTrue(all(k in env for k in ("schema", "data", "warnings", "meta")), name)

    def test_data_keys_match_the_route(self):
        for name, fresh in self.fresh.items():
            with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual([(r["method"], r["path"], r.get("query")) for r in doc["routes"]],
                             [(r["method"], r["path"], r.get("query")) for r in fresh["routes"]], name)
            for old, new in zip(doc["routes"], fresh["routes"]):
                self.assertEqual(_keys(old), _keys(new), "%s %s: a route alakja változott — generáld újra (--write)"
                                 % (name, old["path"]))

    def test_no_paths_keys_or_emails(self):
        for name in self.fresh:
            with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("/tmp/", text)
            self.assertNotIn("/home/", text)
            self.assertNotIn("@", text.replace("user:", ""), name)
            self.assertNotIn(R.SECRET_KEY, text)


if __name__ == "__main__":
    if "--write" in sys.argv:
        write(generate())
        print("fixture-ök frissítve: %s" % ", ".join(n for n, _ in GETS) + ", headhunter_actions.json")
    else:
        unittest.main()
