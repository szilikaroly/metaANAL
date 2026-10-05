# -*- coding: utf-8 -*-
"""A 3 Kinyerés / átváltó / projekt képernyők fixture-jeinek szerződés-ellenőrzése (terv 4.3, 4.7, 4.8, 3.5.2).

A fixture-ök a felület fejlesztői módjának (?fixtures=1) adatai; itt azt ellenőrizzük, hogy a szerver
saját alakellenőrzői (ma_gui/store.py) és a 4. fejezet kötelező mezői szerint is érvényesek, és egymással
konzisztensek (row_uid ↔ tábla, column_map ↔ fields, summary ↔ findings). Böngésző nélkül fut.

Futtatás: python3 -m unittest tests.gui.ui.test_extraction_fixtures   vagy   python3 tests/gui/ui/test_extraction_fixtures.py
"""
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
WEB = ROOT / "ma_gui" / "web"
FX = WEB / "fixtures"

from ma_gui import store  # noqa: E402

UID_RE = re.compile(r"^r[0-9a-z]{4,12}$")
FINDING_KEYS = ("code", "severity", "title", "study", "row", "fields", "detail", "advice", "source", "blocking")


def routes(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))["routes"]


def ok_data(route):
    env = route["envelope"]
    assert env["ok"] is True, route
    return env["data"]


class TableFixtureTests(unittest.TestCase):
    def test_tables(self):
        gets = [r for r in routes("table.json") if r["method"] == "GET"]
        self.assertEqual({r["query"]["dataset"] for r in gets}, {"03_adatok/o1.csv", "03_adatok/o2.csv", "03_adatok/o3.csv"})
        for r in gets:
            d = ok_data(r)
            self.assertEqual(d["dataset"], r["query"]["dataset"])
            self.assertTrue(all(isinstance(h, str) for h in d["header"]))
            self.assertNotIn("row_uid", d["header"])
            uids = [x["row_uid"] for x in d["rows"]]
            self.assertEqual(len(uids), len(set(uids)))
            for x in d["rows"]:
                self.assertRegex(x["row_uid"], UID_RE)
                self.assertEqual(len(x["cells"]), len(d["header"]))
                self.assertTrue(all(isinstance(c, str) for c in x["cells"]), "a cellák szövegek")
            self.assertEqual(store.CsvFormat.from_json(d["format"]).to_json(), d["format"])
            self.assertRegex(d["etag"], r"^[0-9a-f]{64}$")
            self.assertEqual(r["etag"].strip('"'), d["etag"])

    def test_locked_and_put(self):
        rs = routes("table.json")
        locked = [r for r in rs if r.get("status") == 423]
        self.assertEqual(len(locked), 1)
        self.assertEqual(locked[0]["envelope"]["error"]["code"], "LOCKED")
        self.assertEqual(locked[0]["envelope"]["error"]["message"], store.LOCKED_MESSAGE)

    def test_dom_safety_labels_present(self):
        o2 = [ok_data(r) for r in routes("table.json") if r["method"] == "GET" and r["query"]["dataset"].endswith("o2.csv")][0]
        labels = [x["cells"][0] for x in o2["rows"]]
        self.assertIn("<img src=x onerror=alert(1)>", labels)


class ValidationFixtureTests(unittest.TestCase):
    def test_contract_and_consistency(self):
        tables = {ok_data(r)["dataset"]: ok_data(r) for r in routes("table.json") if r["method"] == "GET"}
        for r in routes("validate.json"):
            v = ok_data(r)
            for k in ("schema", "engine_version", "measure", "summary", "findings", "k_analysable"):
                self.assertIn(k, v)
            self.assertEqual(v["schema"], "szk.ma.validation/v1")
            t = tables[r["body"]["dataset"]]
            counts = {"error": 0, "warning": 0, "info": 0}
            blocked = set()
            for f in v["findings"]:
                for k in FINDING_KEYS:
                    self.assertIn(k, f, (f["code"], k))
                self.assertRegex(f["code"], r"^V\d{3}$")
                self.assertIn(f["severity"], counts)
                counts[f["severity"]] += 1
                if f["row"] is not None:
                    self.assertEqual(t["rows"][f["row"]]["row_uid"], f["row_uid"], "row ↔ row_uid")
                    self.assertEqual(t["rows"][f["row"]]["cells"][0], f["study"])
                for fld in f["fields"]:
                    self.assertIn(fld, v["column_map"], "a fields kanonikus nevei a column_map-ben")
                    self.assertIn(v["column_map"][fld], t["header"])
                if f["blocking"]:
                    blocked.add(f["row"])
            self.assertEqual(counts, {k: v["summary"][k] for k in counts})
            self.assertEqual(v["k_analysable"], len(t["rows"]) - len(blocked))
            self.assertEqual(sorted(x["row"] for x in v["excluded"]), sorted(blocked))


class ProvenanceDocumentsTests(unittest.TestCase):
    def test_provenance_contract(self):
        tables = {ok_data(r)["dataset"]: ok_data(r) for r in routes("table.json") if r["method"] == "GET"}
        for r in routes("provenance.json"):
            if r["method"] != "GET":
                continue
            data = ok_data(r)                      # ma_gui/routes/table.py: {provenance, etag, state}
            doc, state = data["provenance"], data["state"]
            self.assertEqual(r["etag"].strip('"'), data["etag"])
            self.assertEqual(store.provenance_problems(doc), [])
            t = tables[doc["table"]]
            self.assertEqual(doc["table_sha256"], t["etag"])
            self.assertTrue(state["in_sync"])
            uids = {x["row_uid"] for x in t["rows"]}
            for c in doc["cells"]:
                self.assertIn(c["row_uid"], uids)

    def test_documents_contract_and_sources(self):
        docs = ok_data(routes("documents.json")[0])
        self.assertEqual(store.documents_problems(docs), [])
        ids = {d["id"] for d in docs["docs"]}
        for r in routes("provenance.json"):
            if r["method"] == "GET":
                for c in ok_data(r)["provenance"]["cells"]:
                    if c["source"]["doc"]:
                        self.assertIn(c["source"]["doc"], ids, "az eredet csak a jegyzékben lévő dokumentumra mutat")

    def test_fileurl(self):
        url = ok_data(routes("fileurl.json")[0])["url"]
        self.assertTrue(url.startswith("/f/"))


class ConvertFixtureTests(unittest.TestCase):
    def test_results(self):
        kinds = set()
        for r in routes("convert.json"):
            env = r["envelope"]
            if not env["ok"]:
                self.assertEqual(env["error"]["code"], "VALIDATION")
                continue
            d = env["data"]
            kinds.add(d["kind"])
            self.assertEqual(d["schema"], "szk.ma.convert-result/v1")
            self.assertIsInstance(d["estimated"], bool)
            for k in ("id", "citation", "function", "kb_refs"):
                self.assertIn(k, d["method"])
            for out, val in d["outputs"].items():
                self.assertIsInstance(val, (int, float))
                txt = d["outputs_text"][out]
                self.assertEqual(set(txt), {"hu", "en"}, "minden kimenethez a motor {hu, en} szövege")
        self.assertEqual(kinds, {"median_to_mean_sd", "se_to_sd", "ci_to_sd", "combine_groups", "change_sd"})
        est = {ok_data(r)["kind"]: ok_data(r)["estimated"] for r in routes("convert.json") if r["envelope"]["ok"]}
        self.assertTrue(est["median_to_mean_sd"])
        self.assertFalse(est["se_to_sd"])


class ProjectPrivacyFixtureTests(unittest.TestCase):
    def test_project_outcomes_point_to_tables(self):
        tables = {r["query"]["dataset"] for r in routes("table.json") if r["method"] == "GET"}
        p = ok_data([r for r in routes("project_actions.json") if r["method"] == "GET"][0])
        self.assertTrue(p["recent"])
        for o in p["outcomes"]:
            self.assertIn(o["data"], tables)

    def test_privacy_plans(self):
        for r in routes("privacy_apply.json"):
            env = r["envelope"]
            if r["method"] == "GET":
                d = env["data"]
                self.assertIn(d["data_class"], ("B", "C"))
                self.assertEqual(r["query"]["data_class"], d["data_class"])
                continue
            if not env["ok"]:
                continue
            d = env["data"]
            if r["body"].get("dry_run"):
                self.assertTrue(d.get("diff") or d.get("script"), "a terv diff-et vagy hook-szöveget ad")
            else:
                self.assertTrue(r["body"].get("confirm"), "írás csak kifejezett jóváhagyással")


class I18nParityTests(unittest.TestCase):
    def test_fragments_parity(self):
        for name in ("extraction", "convert", "project"):
            hu = json.loads((WEB / "src" / "i18n" / "hu" / ("%s.json" % name)).read_text(encoding="utf-8"))
            en = json.loads((WEB / "src" / "i18n" / "en" / ("%s.json" % name)).read_text(encoding="utf-8"))
            self.assertEqual(set(hu), set(en), name)
            for k in hu:
                self.assertEqual(set(re.findall(r"\{(\w+)\}", hu[k])), set(re.findall(r"\{(\w+)\}", en[k])), k)
                self.assertNotIn("fixture", (hu[k] + en[k]).lower(), "a termék-buildben tiltott szó")


if __name__ == "__main__":
    unittest.main()
