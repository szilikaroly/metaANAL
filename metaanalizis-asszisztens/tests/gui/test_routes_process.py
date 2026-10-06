# -*- coding: utf-8 -*-
"""PRISMA, vizsgálat↔jelentés térkép és az írási adatvédelmi kapuk HTTP-szinten (terv 3.4, 3.5.14, 4.10,
4.13, 7.4, 7.5, 8.5):

- GET /api/prisma, PUT /api/prisma/manual (dry_run előnézet nem ír; mentés If-Match-csel; a fájlba a motor
  által értelmezett egészek; P001 → 422; composer-mód csak naplózott indoklással írható felül);
- GET/PUT /api/studies (I és J a motortól; P017 a PRISMA-ellenőrzésben; 409, 422);
- PHI-gyanú a spec/PRISMA/térkép szövegében → 403 érték nélkül; B osztály vault alatt → írás-tartás;
  C osztály: a metaadat (spec) írható, az adattábla csak a _privat/ alá; activity-ban nincs cellaérték."""
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_routes_harness as H  # noqa: E402
from metaelemzes import api  # noqa: E402
from ma_gui import activity, store  # noqa: E402

FLOW = {"schema": "szk.prisma-flow/v1", "identified_databases": "412", "identified_registers": "9",
        "dedup_removed": "96", "automation_removed": None, "removed_before_screening_n": None, "screened": "325",
        "excluded_screening": "271", "sought_for_retrieval": "54", "not_retrieved": "3",
        "assessed_eligibility": "51", "excluded_eligibility": "36", "included_reports": "15",
        "included_studies": "13", "excluded_eligibility_reasons": {"nem RCT": "17", "nincs TBC-kimenet": "11",
                                                                  "nem BCG-oltás": "6"}}
PRISMA = "02_szures/prisma_flow.json"


def _studies(n=13, extra_reports=0):
    out = [{"study_id": "S%02d" % i, "label": "Vizsgálat %d" % i, "registration": None, "design": "rct_parallel",
            "outcomes": ["o1"], "reports": [{"rec_id": "pmid:%d" % (1000 + i), "role": "primary", "doc": None}]}
           for i in range(n)]
    for j in range(extra_reports):
        out[j]["reports"].append({"rec_id": "pmid:%d" % (5000 + j), "role": "secondary", "doc": None})
    return {"schema": "szk.ma.studies/v1", "studies": out}


class ProcessRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_routes_pr_")
        cls.proj, cls.home = H.make_project(cls.tmp)
        cls.srv = H.Srv(cls.proj, cls.home, cls.tmp, name="pr")

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ok(self, *a, **kw):
        return self.srv.ok(*a, **kw)

    def err(self, *a, **kw):
        return self.srv.err(*a, **kw)

    def path(self, rel):
        return os.path.join(self.proj, *rel.split("/"))

    def setUp(self):
        for rel in (PRISMA, store.STUDIES_REL):
            if os.path.exists(self.path(rel)):
                os.remove(self.path(rel))

    def test_prisma_preview_save_and_conflict(self):
        n_before = len([r for r in activity.read_records(self.proj) if r["action"] == "prisma.save"])
        empty = self.ok("GET", "/api/prisma")
        self.assertIsNone(empty["_etag"])
        self.assertEqual((empty["data"]["mode"], empty["data"]["path"]), ("manual", PRISMA))
        # élő előnézet: a motor ellenőrzése, nem ír
        dry = self.ok("PUT", "/api/prisma/manual", {"dry_run": True, "flow": FLOW, "client_seq": 4})["data"]
        self.assertIs(dry["saved"], False)
        self.assertFalse(os.path.exists(self.path(PRISMA)))
        self.assertEqual(dry["check"], api.prisma_check(FLOW))
        self.assertEqual([f["code"] for f in dry["check"]["findings"]], ["P007"])
        self.assertEqual(dry["check"]["derived"]["excluded_eligibility_reasons_sum"], 34)
        # mentés (új fájl, If-Match nélkül): a fájlban a motor által értelmezett egész számok
        saved = self.ok("PUT", "/api/prisma/manual", {"flow": FLOW})
        self.assertIs(saved["data"]["saved"], True)
        etag = saved["_etag"]
        with open(self.path(PRISMA), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["schema"], "szk.prisma-flow/v1")
        self.assertEqual((doc["identified_databases"], doc["included_studies"]), (412, 13))
        self.assertEqual(doc["excluded_eligibility_reasons"], {"nem RCT": 17, "nincs TBC-kimenet": 11,
                                                                "nem BCG-oltás": 6})
        self.assertEqual(doc["source"]["kind"], "manual")
        self.assertEqual(api.prisma_check(doc)["counts"], api.prisma_check(FLOW)["counts"])    # oda-vissza
        self.assertEqual('"%s"' % store.sha256_file(self.path(PRISMA)), etag)
        got = self.ok("GET", "/api/prisma")
        self.assertEqual(got["_etag"], etag)
        self.assertEqual(got["data"]["flow"]["screened"], 325)
        # ütközés: If-Match nélkül (a fájl már létezik) vagy elavult etaggel → 409
        self.assertEqual(self.err("PUT", "/api/prisma/manual", {"flow": FLOW})[0], 409)
        fixed = dict(FLOW, excluded_eligibility_reasons={"nem RCT": "19", "nincs TBC-kimenet": "11",
                                                        "nem BCG-oltás": "6"})
        env = self.ok("PUT", "/api/prisma/manual", {"flow": fixed}, headers=[("If-Match", etag)])
        self.assertEqual(env["data"]["check"]["summary"]["error"], 0)
        self.assertEqual(self.err("PUT", "/api/prisma/manual", {"flow": FLOW}, headers=[("If-Match", etag)])[0], 409)
        recs = [r for r in activity.read_records(self.proj) if r["action"] == "prisma.save"]
        self.assertEqual(len(recs) - n_before, 2)
        self.assertIn(PRISMA, recs[-1]["outputs"])

    def test_prisma_unparsable_box_is_refused(self):
        st, e = self.err("PUT", "/api/prisma/manual", {"flow": dict(FLOW, screened="sok")})
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))
        self.assertEqual([f["code"] for f in e["details"]["findings"]], ["P001"])
        self.assertFalse(os.path.exists(self.path(PRISMA)))
        # az előnézet ugyanezt megmutatja, hibakód nélkül
        dry = self.ok("PUT", "/api/prisma/manual", {"dry_run": True, "flow": dict(FLOW, screened="sok")})["data"]
        self.assertIn("P001", [f["code"] for f in dry["check"]["findings"]])
        self.assertEqual(self.err("PUT", "/api/prisma/manual", {"flow": dict(FLOW, ismeretlen="1")})[0], 400)

    def test_prisma_composer_mode_needs_logged_override(self):
        comp = {"schema": "szk.prisma-flow/v1", "project": "bcg", "generated": "2026-10-04T20:12:00Z",
                "composer_version": "1.5.0", "identified_databases": 400, "dedup_removed": 90, "screened": 310,
                "excluded_screening": 260, "sought_for_retrieval": 50, "not_retrieved": 2,
                "assessed_eligibility": 48, "excluded_eligibility": 35, "included": 13, "included_reports": 13,
                "included_studies": 13}
        os.makedirs(os.path.dirname(self.path(PRISMA)), exist_ok=True)
        with open(self.path(PRISMA), "w", encoding="utf-8") as fh:
            json.dump(comp, fh)
        got = self.ok("GET", "/api/prisma")
        self.assertEqual(got["data"]["mode"], "composer")
        self.assertEqual(got["data"]["source"]["composer_version"], "1.5.0")
        st, e = self.err("PUT", "/api/prisma/manual", {"flow": FLOW}, headers=[("If-Match", got["_etag"])])
        self.assertEqual((st, e["details"]["needs_override"]), (400, True))
        env = self.ok("PUT", "/api/prisma/manual", {"flow": FLOW, "override_reason": "a composer még nem frissült"},
                      headers=[("If-Match", got["_etag"])])["data"]
        self.assertEqual(env["mode"], "manual")
        did = env["override"]["decision_id"]
        item = api.project_show(self.proj, "decision", did)
        self.assertEqual(item["rationale"], "a composer még nem frissült")
        self.assertEqual(item["actor"], "user")

    def test_studies_roundtrip_counts_and_p017(self):
        empty = self.ok("GET", "/api/studies")["data"]
        self.assertEqual(empty["studies"], [])
        self.assertEqual(self.ok("GET", "/api/prisma")["data"]["studies"], {"path": store.STUDIES_REL})  # I, J: ismeretlen
        env = self.ok("PUT", "/api/studies", _studies(13, extra_reports=2))
        d = env["data"]
        H.check_contract(self, d, "szk.ma.studies/v1")
        self.assertEqual(d["summary"], {"studies": 13, "reports": 15})          # I és J a motortól
        self.assertEqual(d["problems"], [])
        with open(self.path(store.STUDIES_REL), encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(set(on_disk), {"schema", "studies"})                  # a nézet mezői nem kerülnek a fájlba
        etag = env["_etag"]
        self.assertEqual(self.err("PUT", "/api/studies", _studies(12))[0], 409)
        self.assertEqual(self.ok("PUT", "/api/studies", _studies(12), headers=[("If-Match", etag)])
                         ["data"]["summary"]["studies"], 12)
        bad = _studies(2)
        bad["studies"][1]["study_id"] = bad["studies"][0]["study_id"]
        st, e = self.err("PUT", "/api/studies", bad, headers=[("If-Match", "*")])
        self.assertEqual(st, 422)
        # a PRISMA-ellenőrzés összeveti a studies.json-nal (I = 12 ≠ 13 a dobozban → P017)
        pr = self.ok("GET", "/api/prisma")["data"]
        self.assertEqual(pr["studies"]["studies"], 12)
        dry = self.ok("PUT", "/api/prisma/manual", {"dry_run": True, "flow": dict(FLOW, included_studies=13)})["data"]
        self.assertIn("P017", [f["code"] for f in dry["check"]["findings"]])

    def test_prisma_meta_lists_primary_runs(self):
        self.ok("PUT", "/api/specs/o1_primary", H.spec())
        job = self.srv.job(self.ok("POST", "/api/analyze", {"mode": "commit", "spec": H.spec()})["data"])
        self.assertEqual(job["status"], "done", job)
        meta = self.ok("GET", "/api/prisma")["data"]["meta"]
        hit = [m for m in meta if m["outcome_id"] == "o1"]
        self.assertTrue(hit)
        self.assertEqual(hit[0]["k"], job["result"]["run"]["k"])
        self.assertEqual(hit[0]["name"], {"hu": "TBC-incidencia", "en": "TB incidence"})


class PrivacyGateTests(unittest.TestCase):
    """Az írási kapuk az új végpontokon: PHI-szkenner (érték nélküli hiba), vault-írástartás, C osztály."""

    def setUp(self):
        self.tmp = H.tmpdir("ma_routes_priv_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_phi_in_metadata_is_refused_without_echoing_values(self):
        proj, home = H.make_project(self.tmp, "a")
        srv = H.Srv(proj, home, self.tmp, name="a", kb_build=False)
        secret = H.taj()
        try:
            st, e = srv.err("PUT", "/api/specs/phi", H.spec("phi", options={"measure": "RR",
                                                                             "title": "Beteg TAJ %s" % secret}))
            self.assertEqual((st, e["code"]), (403, "FORBIDDEN"))
            self.assertNotIn(secret, json.dumps(e))
            self.assertEqual(e["details"]["path"], "05_elemzes/specs/phi.json")
            self.assertFalse(os.path.exists(os.path.join(proj, "05_elemzes", "specs", "phi.json")))
            st, e = srv.err("PUT", "/api/prisma/manual", {"flow": dict(FLOW, excluded_eligibility_reasons={
                "beteg %s kizárva" % secret: "1"})})
            self.assertEqual(st, 403)
            self.assertNotIn(secret, json.dumps(e))
            stud = _studies(1)
            stud["studies"][0]["label"] = "Kovács, szül. 1961.03.12., TAJ %s" % secret
            st, e = srv.err("PUT", "/api/studies", stud)
            self.assertEqual(st, 403)
            self.assertNotIn(secret, json.dumps(e))
            # PRIV-5: a felülírás indoklása a PRISMA-fájlba is bekerülne → ugyanaz a kemény őr, mint a fájl többi
            # szövegére (A osztályban is 403; korábban csak figyelmeztetés volt, és a TAJ a prisma_flow.json-ba került)
            st, e = srv.err("PUT", "/api/prisma/manual", {"flow": FLOW, "override_reason": "TAJ " + secret})
            self.assertEqual(st, 403, e)
            self.assertNotIn(secret, json.dumps(e))
            self.assertFalse(os.path.exists(os.path.join(proj, *PRISMA.split("/"))))
            st, _h, env = srv.call("PUT", "/api/prisma/manual", {"flow": FLOW, "override_reason": "a composer-export "
                                                                                                  "elavult"})
            self.assertEqual(st, 200, env)
            os.remove(os.path.join(proj, *PRISMA.split("/")))
            for rel in ("07_ellenorzes/activity.jsonl",):
                p = os.path.join(proj, rel)
                if os.path.exists(p):
                    with open(p, "rb") as fh:
                        self.assertNotIn(secret.encode(), fh.read())
            self.assertNotIn(secret, srv.log.getvalue())
            # a sima dátum (protokoll-hivatkozás) nem PHI; a születési kontextusú igen
            srv.ok("PUT", "/api/specs/datum", H.spec("datum", protocol_ref="PROSPERO CRD42026, 2026.10.04."))
            st, e = srv.err("PUT", "/api/specs/szul", H.spec("szul", options={"measure": "RR",
                                                                               "title": "szül. 1961.03.12."}))
            self.assertEqual(st, 403)
            self.assertNotIn("1961", json.dumps(e))
        finally:
            srv.stop()

    def test_class_b_vault_write_hold_on_metadata(self):
        proj, home = H.make_project(self.tmp, "b", data_class="B", vault=True, git=True, block=True)
        srv = H.Srv(proj, home, self.tmp, name="b", kb_build=False)
        try:
            st, e = srv.err("PUT", "/api/specs/o1_primary", H.spec())
            self.assertEqual((st, e["code"]), (403, "FORBIDDEN"))
            self.assertIn("vault", e["message"])
            st, e = srv.err("PUT", "/api/studies", _studies(2))
            self.assertEqual(st, 403)
            # a feltárás (nem ír) itt is megy
            job = srv.job(srv.ok("POST", "/api/analyze", {"mode": "explore", "spec": H.spec()})["data"])
            self.assertEqual(job["status"], "done")
        finally:
            srv.stop()

    def test_class_c_metadata_allowed_tables_only_in_private(self):
        proj, home = H.make_project(self.tmp, "c", data_class="C")
        srv = H.Srv(proj, home, self.tmp, name="c", kb_build=False)
        try:
            srv.ok("PUT", "/api/specs/o1_primary", H.spec())               # metaadat: nem betegszintű adat
            st, e = srv.err("PUT", "/api/table", {"dataset": H.O1, "header": ["study", "e1"],
                                                  "rows": [{"row_uid": "rabcde", "cells": ["A", "1"]}]})
            self.assertEqual((st, e["code"]), (403, "FORBIDDEN"))
        finally:
            srv.stop()


if __name__ == "__main__":
    unittest.main()
