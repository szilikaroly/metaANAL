# -*- coding: utf-8 -*-
"""Az adapter-fixture-ök (ma_gui/web/fixtures/adapters_*.json) sodródás-őre (terv 4.20/5): a fájlok a
tests/gui/ui/gen_adapters_fixtures.py kimenetei — a VALÓDI munkapad-szerver válaszai a BCG valódi commit-futásán és a
stub-pluginokon. Ha a szerver, a motor vagy a generátor változott, futtasd: python3 tests/gui/ui/gen_adapters_fixtures.py.
Ellenőrzi továbbá, hogy a fixture-ök hordozzák a tervezett állapotokat (H1–H7 őrök, 409/424, számhűség)."""
import importlib.util
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "ma_gui", "web", "fixtures")


def _gen():
    spec = importlib.util.spec_from_file_location("gen_adapters_fixtures", os.path.join(HERE, "gen_adapters_fixtures.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _routes(name):
    with open(os.path.join(FIX, "adapters_%s.json" % name), encoding="utf-8") as fh:
        return json.load(fh)["routes"]


def _env(name, path, fx, **query):
    for r in _routes(name):
        q = r.get("query") or {}
        if r["path"] == path and q.get("fx") == fx and all(q.get(k) == v for k, v in query.items()):
            return r["envelope"]
    raise AssertionError("nincs fixture: %s %s fx=%s" % (name, path, fx))


class AdapterFixtureTests(unittest.TestCase):
    def test_generated_from_server_and_current(self):
        self.assertEqual(_gen().main(["--check"]), 0,
                         "az adapter-fixture-ök elsodródtak — futtasd: python3 tests/gui/ui/gen_adapters_fixtures.py")

    def test_states_and_guards_present(self):
        feats = {f["id"]: f for f in _env("caps", "/api/adapters", "legacy")["data"]["features"]}
        self.assertEqual([g["id"] for g in feats["tripod"]["guards"]], ["H1"])
        self.assertEqual([g["id"] for g in feats["probast"]["guards"]], ["H2"])
        self.assertEqual([g["id"] for g in feats["grade"]["guards"]], ["H3"])
        self.assertEqual([g["id"] for g in feats["amstar2"]["guards"]], ["H4"])
        self.assertEqual([g["id"] for g in feats["prisma"]["guards"]], ["H7"])
        h5 = {f["id"]: f for f in _env("caps", "/api/adapters", "h5")["data"]["features"]}
        self.assertEqual(h5["figure_audit"]["state"], "unusable")
        self.assertIn("FIGURE_FORGE_PYTHON", h5["figure_audit"]["remedy"]["hu"])
        ok = {f["id"]: f["state"] for f in _env("caps", "/api/adapters", "ok")["data"]["features"]}
        self.assertTrue(all(v == "ok" for v in ok.values()), ok)

    def test_figures(self):
        d = _env("figures", "/api/figures/export", "legacy")["data"]
        self.assertTrue(d["qc"]["numbers"]["ok"])
        self.assertEqual(d["qc"]["badge"], "ok")
        self.assertEqual(_env("figures", "/api/figures/export", "conflict")["error"]["code"], "CONFLICT")
        self.assertEqual(_env("figures", "/api/figures/export", "issues")["data"]["qc"]["badge"], "issues")
        self.assertEqual(_env("figures", "/api/figures/export", "h5")["data"]["qc"]["figure_forge"]["error"]["guard"], "H5")

    def test_composer_and_validator(self):
        self.assertEqual(_env("composer", "/api/prisma/composer/refresh", "needs-confirm")["error"]["code"], "CONFLICT")
        view = _env("composer", "/api/prisma/composer", "composer")["data"]
        self.assertEqual(view["current"]["mode"], "composer")
        self.assertEqual([w["code"] for w in view["current"]["status_warnings"]], ["retmax", "pending_5d"])
        prob = _env("validator", "/api/validator/check", "legacy", tool="probast-ai")["data"]
        self.assertEqual((prob["answered"], prob["validator_reported"]["answered"]), (16, 32))
        self.assertEqual(_env("validator", "/api/validator/check", "absent")["error"]["code"], "CAPABILITY_MISSING")


if __name__ == "__main__":
    unittest.main()
