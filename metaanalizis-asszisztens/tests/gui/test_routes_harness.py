# -*- coding: utf-8 -*-
"""Közös segédek a végpont-tesztekhez (tests/gui/test_routes_*.py): ideiglenes projekt a BCG (o1, RR,
';' és tizedes nélküli egészek) és a Normand (o2, folytonos MD, ',' tagoló) példával, élő munkapad-
szerver (port 0) token-cserével, stdlib http.client-kérések. Ez a modul maga nem tartalmaz tesztet."""
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from metaelemzes import api  # noqa: E402
from ma_gui import caps as caps_mod  # noqa: E402
from ma_gui import privacy, schema_lite, security, server  # noqa: E402

BCG = os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv")
NORMAND = os.path.join(ROOT, "peldak", "normand1999_folytonos.csv")
O1 = "03_adatok/o1.csv"
O2 = "03_adatok/o2.csv"
SPEC_SCHEMA = "szk.ma.analysis-spec/v1"


def taj(first8="12345678"):
    """Érvényes CDV-jű (TAJ-gyanús) 9 jegyű szám — csak tesztadat."""
    return first8 + str(privacy.taj_check_digit(first8))


def tmpdir(prefix):
    return os.path.realpath(tempfile.mkdtemp(prefix=prefix))


def make_project(base, name="proj", data_class="A", vault=False, git=False, block=False, init=True):
    """(projektmappa, home): BCG → 03_adatok/o1.csv, Normand → 03_adatok/o2.csv, ma-projekt.json két
    kimenettel. vault=True: a projekt a (hamis) vault gyökere alatt van (~/Documents/claude)."""
    home = os.path.join(base, "home_" + name)
    os.makedirs(home)
    if vault:
        vroot = os.path.join(home, "Documents", "claude")
        os.makedirs(vroot)
        os.makedirs(os.path.join(home, ".claude", "vault"))
        with open(os.path.join(home, ".claude", "vault", "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"root": vroot, "max_depth": 2}, fh)
        proj = os.path.join(vroot, name)
    else:
        proj = os.path.join(base, name)
    os.makedirs(proj)
    if init:
        api.project_init(proj, "BCG és Normand — teszt")
    os.makedirs(os.path.join(proj, "03_adatok"), exist_ok=True)
    shutil.copy(BCG, os.path.join(proj, O1))
    shutil.copy(NORMAND, os.path.join(proj, O2))
    meta = {"schema": "szk.ma.project/v1", "title": "BCG és Normand — teszt", "data_class": data_class,
            "outcomes": [{"id": "o1", "name": {"hu": "TBC-incidencia", "en": "TB incidence"}, "data": O1,
                          "measure": "RR", "primary_spec": "05_elemzes/specs/o1_primary.json"},
                         {"id": "o2", "name": {"hu": "Kórházi napok", "en": "Hospital days"}, "data": O2,
                          "measure": "MD", "primary_spec": "05_elemzes/specs/o2_primary.json"}]}
    with open(os.path.join(proj, "ma-projekt.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    if git:
        subprocess.run(["git", "init", "-q", proj], check=True)
    if block:
        privacy.apply_gitignore(proj)
    return proj, home


def spec(name="o1_primary", outcome="o1", data=O1, measure="RR", **extra):
    doc = {"schema": SPEC_SCHEMA, "name": name, "outcome": outcome, "purpose": "primary", "prespecified": False,
           "protocol_ref": None, "parent": None, "data": {"path": data}, "options": {"measure": measure},
           "filters": {"include": [], "exclude": []}, "kb_refs": []}
    doc.update(extra)
    return doc


def http_call(port, method, path, body=None, headers=(), timeout=60):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        names = set()
        for k, v in headers:
            conn.putheader(k, v)
            names.add(k.lower())
        if body is not None and "content-length" not in names:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


class Srv(object):
    """Élő munkapad-szerver egy projekthez (port 0), token-csere után; a válaszokat a szerver a saját
    sémáival is ellenőrzi (validate_responses=True: eltérésnél 500)."""

    def __init__(self, proj, home, tmp, name="s", **kw):
        self.log = io.StringIO()
        opts = dict(kb_db=os.path.join(tmp, "kb_%s.sqlite" % name),
                    caps=caps_mod.Caps(runtime_dir=os.path.join(tmp, "caps_" + name), env={}, home=home),
                    privacy_home=home, privacy_env={}, selftest=False, kb_build=True, caps_refresh=False,
                    log_stream=self.log, idle_hours=0, watch_interval=0.2, validate_responses=True)
        opts.update(kw)
        self.proj = proj
        self.app = server.App(proj, **opts)
        self.app.start(port=0)
        self.thread = threading.Thread(target=self.app.serve_forever, name="test-serve", daemon=True)
        self.thread.start()
        self.port = self.app.port
        st, _h, env = self.call("POST", "/api/session", {"launch_code": self.app.new_launch_code()}, token=False)
        assert st == 200, env
        self.token = env["data"]["token"]

    def call(self, method, path, body=None, headers=(), token=True, raw=None, timeout=60):
        hdrs = [("Host", "127.0.0.1:%d" % self.port)]
        if token:
            hdrs.append(("X-MA-Token", self.token if token is True else token))
        payload = raw
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if payload is not None:
            hdrs.append(("Content-Type", "application/json"))
        hdrs.extend(headers)
        st, h, data = http_call(self.port, method, path, payload, hdrs, timeout=timeout)
        try:
            env = json.loads(data.decode("utf-8")) if data else None
        except ValueError:
            env = None
        return st, h, env

    def raw(self, method, path, headers=(), token=False):
        hdrs = [("Host", "127.0.0.1:%d" % self.port)]
        if token:
            hdrs.append(("X-MA-Token", self.token))
        hdrs.extend(headers)
        return http_call(self.port, method, path, None, hdrs)

    def ok(self, method, path, body=None, headers=()):
        st, h, env = self.call(method, path, body, headers)
        if st != 200 or not isinstance(env, dict) or env.get("ok") is not True:
            raise AssertionError("%s %s → %s %s" % (method, path, st, json.dumps(env, ensure_ascii=False)[:1500]))
        errs = schema_lite.validate(env, security.ENVELOPE_SCHEMA)
        if errs:
            raise AssertionError("boríték-séma: %s" % errs)
        env["_etag"] = h.get("etag")
        return env

    def err(self, method, path, body=None, headers=()):
        st, _h, env = self.call(method, path, body, headers)
        if not isinstance(env, dict) or env.get("ok") is not False:
            raise AssertionError("%s %s → %s %s (hibát vártunk)" % (method, path, st, env))
        errs = schema_lite.validate(env, security.ENVELOPE_SCHEMA)
        if errs:
            raise AssertionError("boríték-séma: %s" % errs)
        return st, env["error"]

    def job(self, job, timeout=60.0):
        """A feladat végállapotáig kérdez (GET /api/jobs/<id>)."""
        deadline = time.monotonic() + timeout
        while job.get("status") in ("queued", "running"):
            if time.monotonic() > deadline:
                raise AssertionError("a feladat nem fejeződött be: %s" % job)
            time.sleep(0.1)
            job = self.ok("GET", "/api/jobs/%s" % job["job_id"])["data"]
        return job

    def stop(self):
        self.app.shutdown()
        self.thread.join(15)


def wait_for(pred, timeout=10.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        val = pred()
        if val:
            return val
        time.sleep(step)
    return pred()


def contracts():
    """A motor szerződés-regisztere (a válaszok szerződés-ellenőrzéséhez)."""
    from ma_gui.routes import _contracts
    return _contracts.registry()


def check_contract(testcase, doc, name):
    from ma_gui.routes import _contracts
    errs = schema_lite.validate(doc, _contracts.ref(name), contracts())
    testcase.assertEqual(errs, [], "%s: %s" % (name, errs[:5]))
