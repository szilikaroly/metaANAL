#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A folyamat-képernyők fixture-jeinek sodródás-őre (terv 4.20 szellemében, 8.6).

Böngésző nélkül ellenőrzi, hogy a fejlesztői fixture-ök a VALÓDI motort és tudásbázist tükrözik:
  - prisma.json: minden előnézeti (dry_run) válasz check-je = metaelemzes.prisma.check_flow(flow);
    a kérés-flow kulcssorrendje = a felület toFlow()-ja (screens/prisma.js BOXES);
  - kb_process.json: a KB-tételek = metaelemzes.kb.show(id); a teljes szöveg (chunk) SZINTETIKUS (7.7);
  - log_write.json: a GATE_BLOCKED üzenet a motor (projekt.checkpoint) valódi szövege;
  - a folyamat-képernyők i18n-kulcsai (hu/en process.json) azonosak, és a doboz-címkék mind megvannak;
  - a képernyő-forrásokban nincs tiltott minta és nincs „fixture” szó (a termék-build tiltja).
Futtatás: python3 tests/gui/ui/test_process_fixtures.py
"""
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
WEB = ROOT / "ma_gui" / "web"
FIX = WEB / "fixtures"
SRC = WEB / "src"
MINE = ["components/proc.js", "components/why.js", "screens/overview.js", "screens/prisma.js", "screens/studies.js",
        "screens/log.js", "screens/capabilities.js", "screens/export.js"]

import gen_process_fixtures as gen  # noqa: E402
from metaelemzes import kb, prisma  # noqa: E402


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class PrismaFixtureTests(unittest.TestCase):
    def test_previews_match_engine(self):
        n = 0
        for r in load("prisma.json")["routes"]:
            flow = (r.get("body") or {}).get("flow")
            if r["method"] != "PUT" or not flow:
                continue
            got = r["envelope"]["data"]["check"]
            want = prisma.check_flow(flow).to_dict()
            for k in ("template", "ok", "summary", "findings", "derived"):
                self.assertEqual(got[k], want[k], "%s: %s eltér a motortól" % (r.get("note"), k))
            n += 1
        self.assertGreaterEqual(n, 4)

    def test_flow_key_order_matches_ui(self):
        src = (SRC / "screens" / "prisma.js").read_text(encoding="utf-8")
        block = src[src.index("var BOXES = ["):src.index("];", src.index("var BOXES = ["))]
        keys = re.findall(r"\{ k: '([a-z_]+)'", block)
        self.assertEqual(keys, gen.BOX_KEYS, "a fixture-flow kulcssorrendje ≠ a felület toFlow()-ja")
        for r in load("prisma.json")["routes"]:
            flow = (r.get("body") or {}).get("flow")
            if flow:
                self.assertEqual(list(flow), ["schema"] + gen.BOX_KEYS + ["excluded_eligibility_reasons"])

    def test_base_state_has_p007_on_box_h(self):
        get = [r for r in load("prisma.json")["routes"] if r["method"] == "GET" and not r.get("query")][0]
        f = get["envelope"]["data"]["check"]["findings"]
        self.assertEqual([x["code"] for x in f], ["P007"])
        self.assertIn("excluded_eligibility", f[0]["fields"])


class KbFixtureTests(unittest.TestCase):
    def test_items_match_kb(self):
        n = 0
        for r in load("kb_process.json")["routes"]:
            if not r["path"].startswith("/api/kb/item/") or not r["envelope"]["ok"]:
                continue
            d = r["envelope"]["data"]
            if d["table"] == "chunk":
                self.assertIn("SZINTETIKUS", d["item"]["text"], "jogvédett teljes szöveg nem kerülhet a repóba (7.7)")
                self.assertTrue(d["local_only"])
                continue
            want = dict(kb.show(d["id"]))
            want.pop("_table")
            self.assertEqual(d["item"], want, d["id"])
            n += 1
        self.assertGreaterEqual(n, 8)

    def test_search_chunks_are_synthetic(self):
        for r in load("kb_process.json")["routes"]:
            if r["path"] == "/api/kb/search":
                for c in r["envelope"]["data"]["results"].get("chunk", []):
                    self.assertIn("SZINTETIKUS", c["snippet"])


class LogFixtureTests(unittest.TestCase):
    def test_gate_message_is_engine_text(self):
        r = [x for x in load("log_write.json")["routes"] if x.get("status") == 409][0]
        self.assertEqual(r["envelope"]["error"]["message"], gen.gate_message())
        self.assertEqual(r["envelope"]["error"]["code"], "GATE_BLOCKED")

    def test_blocker_route_is_subset(self):
        routes = load("log_finding.json")["routes"]
        allf = routes[0]["envelope"]["data"]["items"]
        bl = routes[1]["envelope"]["data"]["items"]
        self.assertEqual([f["id"] for f in bl], [f["id"] for f in allf if f["severity"] == "blocker" and f["status"] == "open"])
        self.assertIn(15, [f["id"] for f in bl], "a keret tesztje a #15 blockert várja")


class SourceTests(unittest.TestCase):
    def test_i18n_fragments(self):
        hu = json.loads((SRC / "i18n" / "hu" / "process.json").read_text(encoding="utf-8"))
        en = json.loads((SRC / "i18n" / "en" / "process.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(hu), sorted(en))
        for L in ("A1", "A2", "D1", "D2", "D3", "B", "C", "E", "F", "G", "H", "I", "J"):
            self.assertIn("prisma.box." + L, hu)
        for k, v in list(hu.items()) + list(en.items()):
            self.assertNotIn("fixture", v.lower(), k)
        self.assertIn("Artifact", hu["export.snap.warn"])
        self.assertIn("Artifact", en["export.snap.warn"])

    def test_generator_check_passes(self):
        """DOC-4: a generátor és a lemezen lévő fixture-ök egyeznek (--check 0) — kézi szerkesztés nem sodródhat el,
        és az export-fixture letöltési mezői (kind, name, url, expires_at) a generátorból jönnek."""
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(gen.main(["--check"]), 0, err.getvalue())
        for r in load("export.json")["routes"]:
            d = r["envelope"]["data"]
            for k in ("kind", "name", "url", "expires_at"):
                self.assertIn(k, d, (r["path"], k))
            self.assertTrue(d["url"].startswith("/f/x/"))

    def test_sources_clean(self):
        bad = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|toFixed|toPrecision|Intl\.NumberFormat|"
                         r"toLocaleString|\bMath\.|localStorage|sessionStorage|indexedDB|\bfetch\s*\(|(?i:fixture)")
        for name in MINE:
            text = (SRC / name).read_text(encoding="utf-8")
            m = bad.search(text)
            self.assertIsNone(m, "%s: tiltott minta: %r" % (name, m.group(0) if m else None))


if __name__ == "__main__":
    unittest.main(verbosity=1)
