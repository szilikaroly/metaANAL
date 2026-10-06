# -*- coding: utf-8 -*-
"""Metaheadhunter — regressziós tesztek a független felülvizsgálat (2026-10-05) hibáira (R-számok a jelentésben).

Minden teszt OFFLINE, szintetikus adattal (a PMID-ek 99xxxxxx tartományúak, nem valós cikkek).

- R1  feloldatlan/kétértelmű rekord API-ból jött (de a cím-ellenőrzésen ELBUKOTT) azonosítója alapján az L1
      automatikusan összevonta a rekordot egy MÁSIK közleménnyel (hamis összevonás + hamis proveniencia);
- R2  közös azonosító, de teljesen eltérő cím és szerző (gyűjtő-DOI, hibásan kapcsolt DOI) → automatikus összevonás;
- R3  névelők (van, der, dos, Abdel) „egyező első szerzőnek" számítottak;
- R4  a PRISMA-leképezés elavult/hiányzó frissítő keresésnél kiigazította (kitalálta) az azonosított számot, így a
      motor-ellenőrzés hamisan átment, és a lezárás is;
- R5  a lezárás (EP5) átment visszavont közleménnyel a bevont halmazban (H013, error szintű szabály);
- R6  verify-secondary „verified" jelölést fogadott el eltérő elsődleges értékkel → az elemzési export az áttekintés
      (eltérő) számát írta be „ellenőrzött" címkével;
- R7  az EP6-döntés érvénytelenítette a lezárást (H017), így az export --for-analysis lehetetlen volt; az export a
      tárolt (elavult) merged.json-ból dolgozott;
- R8  a szűrő nélküli tömeges „mindet elfogad" feloldási és azonosító-ütközési javaslatokat is „elfogadott";
- R9  meg nem erősített (áttekintés-eredetű / feloldatlan) azonosító került a RIS- és a kimenet-exportba;
- R10 emberi id_confirm eltérő című közleményre figyelmeztetés nélkül; a jelölt automatikus feloldását a CLI-ből
      nem lehetett javítani (decide --target rv-…#c… --value pmid:…); az eltérő évű automatikus elfogadás rejtve
      maradt;
- R11 a projekt-gyorsítótár teljesszöveg-őre csak az első 400 kB-ot nézte;
- R12 a szűrési CSV importja nem kért --actor-t (az importot végző ember nem volt rögzítve);
- R13 „decide --target rec-…|st-… --value pmid:…" SZŰRÉSI bevonásként (EP4) rögzült; option:N jelöltre
      jelölt-megerősítés lett;
- R14 a tömeges jelölt-megerősítés (CLI --all-candidates, munkapad „Az összes javasolt megerősítése") az
      irodalomjegyzék ismeretlen szerepű tételeit is „bevont vizsgálattá" tette.
"""
import io
import json
import os
import shutil
import tempfile
import unittest

from _helpers import ROOT  # noqa: F401  (a sys.path beállítása)
from metaelemzes.headhunter import checks, dedup, merge as M, net, prisma_map as PM, resolve, state as S
from metaelemzes.headhunter.__main__ import main as cli_main

import test_headhunter_link as TL
import test_headhunter_merge as TM

AT = TL.AT


def run_cli(*args):
    out = io.StringIO()
    rc = cli_main(list(args) + ["--json"], stdout=out)
    return rc, json.loads(out.getvalue())


def ready_for_signoff(proj):
    """A ``TM.build_project`` projektje lezárható állapotba: javaslatok elfogadva, a függő EP2-jelölt megerősítve,
    minden függő vizsgálat bevonva."""
    TM.accept_all_proposals(proj)
    rc, env = run_cli("confirm", proj, "--target", "%s#c0003" % TM.RV_A, "--actor", "user:SzK")
    assert rc == 0, env
    M.run_merge(proj, now=TM.NOW)
    merged = S.read_json(S.path(proj, "merged.json"))
    pend = [s["study_id"] for s in merged["studies"] if s["status"] == "pending"]
    rc, env = run_cli("confirm", proj, "--target", ",".join(pend), "--actor", "user:SzK")
    assert rc == 0, env


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-review-")
        self.env_backup = dict(os.environ)
        for k in ("MA_SCOPUS_APIKEY", "MA_OPENALEX_APIKEY", "MA_NCBI_APIKEY", "MA_CONTACT_EMAIL",
                  "MA_SCOPUS_INSTTOKEN", "MA_HH_CASSETTE", "MA_HH_CASSETTE_FILE"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)
        shutil.rmtree(self.tmp, ignore_errors=True)


# =============================================================================================
# R1–R3: duplikátum-logika (hamis összevonás)
# =============================================================================================

def _bare_rec(rid, ids, title, fa, year, status="resolved"):
    return {"rec_id": rid, "ids": ids, "bib": {"title": title, "first_author": fa, "year": year}, "flags": {},
            "related": [], "origins": [], "retrievals": [], "resolution": {"status": status}, "status": "active",
            "merged_into": None}


class TestFalseMerges(_Base):

    def test_r1_failed_api_id_of_unresolved_record_is_not_merged(self):
        """A „Soy … CRP" hivatkozás irodalomjegyzékből jött (Europe PMC által kapcsolt) PMID-je egy D-vitamin-cikké;
        a feloldás cím-ellenőrzése elutasítja → a rekord NEM olvad be a D-vitamin-cikkbe, és a B áttekintés
        proveniencia-sora sem kerül át."""
        ca = TL.mk_cand("c0001", "Smith J. Soy isoflavones and CRP in postmenopausal women. Nutr J. 2010",
                        title="Soy isoflavones and CRP in postmenopausal women", first_author="Smith J", year=2010,
                        ids={"pmid": TL.idv("99000777", source="europepmc", via="europepmc.references")})
        cb = TL.mk_cand("c0001", "Jones K. Vitamin D and fracture risk. Bone. 2011",
                        title="Vitamin D and fracture risk in elderly men", first_author="Jones K", year=2011,
                        ids={"pmid": TL.idv("99000777")})
        ra, rb = TL.mk_review("rv-pmid-90000201", 2020, [ca]), TL.mk_review("rv-pmid-90000202", 2021, [cb])
        pm = TL.FakePubMed(records=[TL.summary("99000777", "Vitamin D and fracture risk in elderly men", "Jones K",
                                               2011)])
        res = resolve.Resolver(clients={"pubmed": pm, "europepmc": TL.FakeEPMC(), "ctgov": TL.FakeCtgov()},
                               now=AT).run([ra, rb], None, [])
        unres = [r for r in res["records"] if r["rec_id"].startswith("rec-x-")]
        self.assertEqual(1, len(unres))
        self.assertIn(unres[0]["resolution"]["status"], dedup.UNVERIFIED_RESOLUTION)
        self.assertEqual({}, dedup.trusted_ids(unres[0]), "feloldatlan rekord azonosítója nem L1-kulcs")
        # a meg nem erősített azonosító H003-figyelmeztetést kap (akkor is, ha API-forrású volt)
        self.assertTrue(any(w["code"] == "H003" and unres[0]["rec_id"] in w["hu"] for w in res["warnings"]))
        resolve.apply_links_to_reviews([ra, rb], res["links"], res["confirmations"])
        doc = dedup.link(res["records"], [ra, rb], [], now=AT)
        self.assertFalse([p for p in doc["proposals"] if p["kind"] == "same_report" and p["status"] == "auto_applied"])
        self.assertTrue(all(r["status"] == "active" for r in doc["records"]))
        self.assertEqual(2, len(doc["studies"]))

    def test_r2_shared_doi_with_contradicting_bib_needs_human(self):
        a = _bare_rec("rec-doi-a", {"doi": TL.api("10.5555/supp.1", "crossref", "crossref.works")},
                      "Effect of soy on C-reactive protein in women", "Smith A", 2015)
        b = _bare_rec("rec-x-bbbbbbbbbb", {"doi": TL.api("10.5555/supp.1", "europepmc", "europepmc.search")},
                      "Vitamin D supplementation and fracture risk in elderly men", "Jones B", 2015)
        doc = dedup.link([a, b], [], [], now=AT)
        p = [x for x in doc["proposals"] if x["rule"] == "L1-bib-mismatch"]
        self.assertEqual(1, len(p))
        self.assertEqual(("id_conflict", "pending"), (p[0]["kind"], p[0]["status"]))
        self.assertIn("NEM vontuk össze", p[0]["explanation"]["hu"])
        self.assertTrue(all(r["status"] == "active" for r in doc["records"]))
        # emberi elfogadás után összevonódik (a döntés a szokásos id_conflict úton hat)
        d = dedup.make_decision("duplicate_accept", "proposal", p[0]["proposal_id"], "accept", "user:SzK", now=AT)
        d["_order"] = 0
        doc2 = dedup.link([a, b], [], [d], prior=doc, now=AT)
        self.assertEqual("merged_into", [r for r in doc2["records"] if r["rec_id"] == "rec-x-bbbbbbbbbb"][0]["status"])
        # lefordított cím (azonos első szerző és év) → továbbra is automatikus L1
        c = _bare_rec("rec-doi-c", {"doi": TL.api("10.5555/x.2")}, "Wirkung von Soja auf das CRP", "Müller K", 2012)
        e = _bare_rec("rec-x-eeeeeeeeee", {"doi": TL.api("10.5555/x.2", "europepmc", "europepmc.search")},
                      "[Effect of soy on CRP]", "Muller K", 2012)
        doc3 = dedup.link([c, e], [], [], now=AT)
        self.assertEqual([("same_report", "auto_applied")], [(x["kind"], x["status"]) for x in doc3["proposals"]])
        # ugyanaz a közlemény, kissé eltérő cím → automatikus L1 marad
        f = _bare_rec("rec-pmid-99000801", {"pmid": TL.api("99000801")},
                      "Soy protein and inflammatory markers: a randomized trial", "Kim A", 2014)
        g = _bare_rec("rec-x-gggggggggg", {"pmid": TL.api("99000801", "openalex", "openalex.works")},
                      "Soy protein and inflammatory markers", "Kim A", 2014)
        doc4 = dedup.link([f, g], [], [], now=AT)
        self.assertEqual([("same_report", "auto_applied")], [(x["kind"], x["status"]) for x in doc4["proposals"]])

    def test_r3_name_particles_are_not_surname_keys(self):
        for a, b in (("van der Berg A", "van Dyk B"), ("Abdel Rahman M", "Abdel Aziz K"),
                     ("dos Santos A", "dos Reis B"), ("de Souza M", "de Lima M")):
            self.assertFalse(dedup.first_author_match(a, b), (a, b))
        for a, b in (("van der Berg A", "van der Berg AB"), ("Ferguson Rg", "FERGUSON RG"),
                     ("Cano Pérez G", "Pérez"), ("A. Mac DOWELL", "MacDOWELL A Jr"), ("de Souza M", "Souza M")):
            self.assertTrue(dedup.first_author_match(a, b), (a, b))
        # a feloldás nem fogad el eltérő (csak névelőben egyező) első szerzőt
        it = resolve._Item({"review_id": "rv-pmid-90000301"},
                           TL.mk_cand("c0001", "x", title="Soy and CRP in postmenopausal women: a randomised trial",
                                      first_author="van der Berg A", year=2010))
        ok, _ts, f = resolve._accept(it, {"title": "Soy and CRP in postmenopausal women: a randomised trial",
                                          "first_author": "van Dyk B", "year": 2010}, resolve.ACCEPT_SEARCH)
        self.assertFalse(f["first_author_match"])
        self.assertFalse(ok)


# =============================================================================================
# R4: PRISMA — nincs kitalált szám
# =============================================================================================

class TestPrismaNoInflation(_Base):

    def _with_update_records(self, proj, n=3):
        doc = S.read_json(S.path(proj, "studies.json"))
        recs = doc["records"]
        for i in range(n):
            pmid = "9930%04d" % i
            recs.append({"rec_id": "rec-pmid-%s" % pmid, "ids": {"pmid": TL.api(pmid)},
                         "bib": {"title": "Update trial number %d of drug X" % i, "first_author": "Upd%d A" % i,
                                 "year": 2024},
                         "flags": {}, "related": [], "retrievals": [], "status": "active", "merged_into": None,
                         "origins": [{"route": "update_search", "review_id": None, "cand_id": None,
                                      "search_id": "s-pubmed-20261005T100000Z"}],
                         "resolution": {"status": "resolved", "method": "update-search:pubmed", "score": None}})
        doc = dedup.link(recs, S.load_reviews(proj), S.read_decisions(proj), prior=doc, now=TM.NOW)
        S.write_json_atomic(S.path(proj, "studies.json"), doc)

    def test_r4_stale_update_doc_is_not_inflated(self):
        proj = TM.build_project(self.tmp)
        self._with_update_records(proj, 3)
        upd = {"results": {"databases_retrieved": 1, "registers_retrieved": 0, "unusable": 0, "by_source": {}},
               "queries": [], "window": {}}
        flow, warns = PM.build_flow(S.load_state(proj), S.load_reviews(proj), S.read_json(S.path(proj, "studies.json")),
                                    S.read_decisions(proj), upd)
        self.assertEqual(1, flow["identified_databases"], "a letöltött számot nem emeljük meg")
        self.assertEqual(3, flow["screened"])
        self.assertTrue(any(w["code"] == "H011" for w in warns))
        chk = PM.check(flow)
        self.assertFalse(chk["ok"])
        self.assertIn("P002", [f["code"] for f in chk["findings"]])

    def test_r4_missing_update_doc_is_not_invented_and_blocks_signoff(self):
        proj = TM.build_project(self.tmp)
        self._with_update_records(proj, 2)
        flow, warns = PM.build_flow(S.load_state(proj), S.load_reviews(proj), S.read_json(S.path(proj, "studies.json")),
                                    S.read_decisions(proj), None)
        self.assertEqual(0, flow["identified_databases"])
        self.assertEqual(2, flow["screened"])
        self.assertFalse(flow["hh"]["no_database_branch"])
        self.assertFalse(PM.check(flow)["ok"])
        # a lezárás nem mehet át a hibás PRISMA-számokkal
        ready_for_signoff(proj)
        res = M.signoff(proj, "user:SzK", now=TM.NOW)
        self.assertFalse(res["ok"])
        self.assertTrue(any("H011" in e["hu"] for e in res["errors"]))


# =============================================================================================
# R5–R7, R9: lezárás, másodlagos adat, export
# =============================================================================================

class TestSignoffAndExports(_Base):

    def test_r5_retracted_included_study_blocks_signoff_until_excluded_or_kept(self):
        proj = TM.build_project(self.tmp)
        doc = S.read_json(S.path(proj, "studies.json"))
        for r in doc["records"]:
            if r["rec_id"] == "rec-pmid-99100102":
                r["flags"]["retracted"] = True
        S.write_json_atomic(S.path(proj, "studies.json"), doc)
        ready_for_signoff(proj)
        jones = TM.study_of(proj, "rec-pmid-99100102")
        res = M.signoff(proj, "user:SzK", now=TM.NOW)
        self.assertFalse(res["ok"])
        self.assertEqual(4, res["exit_code"])
        self.assertTrue(any("H013" in e["hu"] and jones in e["hu"] for e in res["errors"]))
        self.assertFalse(S.read_json(S.path(proj, "merged.json"))["final"])
        self.assertIn("H013", [f["code"] for f in checks.verify(proj)["findings"]])
        # megtartás indoklás nélkül: használati hiba; nem visszavont vizsgálatra: hiba
        rc, env = run_cli("decide", proj, "--target", jones, "--value", "keep_retracted", "--actor", "user:SzK")
        self.assertEqual(2, rc)
        smith = TM.study_of(proj, "rec-pmid-99100101")
        rc, env = run_cli("decide", proj, "--target", smith, "--value", "keep_retracted", "--reason", "x",
                          "--actor", "user:SzK")
        self.assertEqual(2, rc)
        rc, env = run_cli("decide", proj, "--target", jones, "--value", "keep_retracted", "--reason",
                          "csak érzékenységi elemzésben; a visszavonás oka szerzői vita", "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        self.assertEqual("final_inclusion", S.read_decisions(proj)[-1]["kind"])
        res = M.signoff(proj, "user:SzK", now=TM.NOW)
        self.assertTrue(res["ok"], res.get("errors"))
        merged = S.read_json(S.path(proj, "merged.json"))
        st = [s for s in merged["studies"] if s["study_id"] == jones][0]
        self.assertIn("retracted_retention_documented", st["flags"])
        self.assertNotIn("H013", [f["code"] for f in checks.verify(proj)["findings"]])

    def test_r6_verified_with_different_primary_value_is_refused(self):
        proj = TM.build_project(self.tmp)
        TM.accept_all_proposals(proj)
        TM.screen_all(proj, include=["rec-pmid-99100101"])
        M.run_merge(proj, now=TM.NOW)
        smith = TM.study_of(proj, "rec-pmid-99100101")
        with self.assertRaises(S.DecisionError) as cm:
            M.verify_secondary(proj, smith, "n1", "verified", "user:SzK", review_id=TM.RV_A,
                               primary_locator="rec-pmid-99100101 p. 3", primary_value=52)
        self.assertIn("discrepant", str(cm.exception))
        # azonos érték → elfogadva; eltérés → discrepant
        M.verify_secondary(proj, smith, "n1", "verified", "user:SzK", review_id=TM.RV_A,
                           primary_locator="rec-pmid-99100101 p. 3", primary_value=50)
        M.verify_secondary(proj, smith, "n1", "discrepant", "user:SzK", review_id=TM.RV_B,
                           primary_locator="rec-pmid-99100101 p. 3", primary_value=50)

    def test_r7_ep6_after_signoff_keeps_signoff_and_export_uses_fresh_state(self):
        proj = TM.build_project(self.tmp)
        ready_for_signoff(proj)
        res = M.signoff(proj, "user:SzK", now=TM.NOW)
        self.assertTrue(res["ok"], res.get("errors"))
        smith = TM.study_of(proj, "rec-pmid-99100101")
        M.verify_secondary(proj, smith, "n1", "verified", "user:SzK", review_id=TM.RV_A,
                           primary_locator="rec-pmid-99100101 p. 3, Table 1", primary_value=50)
        # merge NÉLKÜL is a friss döntésekből exportál
        ex = M.run_export(proj, outcome="mortality", for_analysis=True, now=TM.NOW)
        self.assertTrue(ex["ok"], ex.get("errors"))
        self.assertTrue(any(w["code"] == "H017" for w in ex["warnings"]))
        with open(S.path(proj, "exports", "mortality_sablon.csv"), encoding="utf-8") as fh:
            rows = list(__import__("csv").DictReader(fh, delimiter=";"))
        row = [r for r in rows if r["study_id"] == smith][0]
        self.assertEqual(("50", "masodlagos_ellenorzott", "igen"), (row["n1"], row["adat_forras"], row["ellenorizve"]))
        self.assertEqual("", row["e1"], "ellenőrizetlen e1 nem kerül be")
        # merge után a lezárás érvényes marad (az EP6 nem változtatja a bevont halmazt)
        M.run_merge(proj, now=TM.NOW)
        self.assertTrue(S.read_json(S.path(proj, "merged.json"))["final"])
        self.assertTrue(M.run_export(proj, outcome="mortality", for_analysis=True, now=TM.NOW)["ok"])
        # bevonási döntés a lezárás után → az export --for-analysis merge nélkül is megtagad (friss állapot)
        S.append_decision(proj, "screen", ("record", "rec-pmid-99100102"), "exclude", "user:SzK", level="full_text",
                          reason_code="X5", reason="nem RCT")
        ex = M.run_export(proj, outcome="mortality", for_analysis=True, now=TM.NOW)
        self.assertEqual(4, ex["exit_code"])

    def test_r9_unconfirmed_ids_are_not_exported(self):
        proj = TM.build_project(self.tmp)
        doc = S.read_json(S.path(proj, "studies.json"))
        for r in doc["records"]:
            if r["rec_id"] == "rec-pmid-99100102":
                r["resolution"] = {"status": "unresolved", "method": None, "score": None}
                r["ids"] = {"pmid": TL.idv("99100102", source="review", via="jats.pub-id"),
                            "doi": TL.api("10.5555/unconf.1", "europepmc", "europepmc.references")}
        S.write_json_atomic(S.path(proj, "studies.json"), doc)
        TM.accept_all_proposals(proj)
        TM.screen_all(proj, include=["rec-pmid-99100101", "rec-pmid-99100102"])
        M.run_merge(proj, now=TM.NOW)
        res = M.run_export(proj, outcome="mortality", now=TM.NOW)
        self.assertTrue(res["ok"], res)
        with open(S.path(proj, "exports", "records.ris"), encoding="utf-8") as fh:
            ris = fh.read()
        self.assertIn("AN  - PMID:99100101", ris)
        self.assertNotIn("PMID:99100102", ris)
        self.assertNotIn("10.5555/unconf.1", ris)
        with open(S.path(proj, "exports", "mortality_sablon.csv"), encoding="utf-8") as fh:
            rows = list(__import__("csv").DictReader(fh, delimiter=";"))
        jones = TM.study_of(proj, "rec-pmid-99100102")
        row = [r for r in rows if r["study_id"] == jones][0]
        self.assertEqual(("", ""), (row["pmid"], row["doi"]))


# =============================================================================================
# R8: tömeges döntés
# =============================================================================================

class TestBulkDecisions(_Base):

    def test_r8_bulk_accept_skips_resolution_and_id_conflict(self):
        proj = os.path.join(self.tmp, "p")
        os.makedirs(proj)
        S.init_state(proj, "Kérdés?", pico=S.pico_from_args("Kérdés?", population="adults", intervention="soy"),
                     actor="user:SzK", now=TM.NOW)
        recs = [
            _bare_rec("rec-doi-a", {"doi": TL.api("10.5555/supp.9", "crossref", "crossref.works")},
                      "Effect of soy on C-reactive protein in women", "Smith A", 2015),
            _bare_rec("rec-x-bbbbbbbbbb", {"doi": TL.api("10.5555/supp.9", "europepmc", "europepmc.search")},
                      "Vitamin D supplementation and fracture risk in elderly men", "Jones B", 2015),
            _bare_rec("rec-pmid-99000901", {"pmid": TL.api("99000901")},
                      "Isoflavone supplementation and interleukin-6 in obese adults", "Lee C", 2016),
            _bare_rec("rec-x-cccccccccc", {}, "Isoflavone supplementation and interleukin 6 in obese adults",
                      "Lee C", 2016, status="unresolved"),
        ]
        doc = dedup.link(recs, [], [], now=AT)
        doc["proposals"].append({"proposal_id": "p-res-0001", "kind": "resolution", "items": ["rv-pmid-1#c0001"],
                                 "status": "pending", "rule": "R-ambiguous", "decision_id": None,
                                 "options": [{"rec_id": "rec-pmid-99000901", "ids": {"pmid": "99000901"}}]})
        S.write_json_atomic(S.path(proj, "studies.json"), doc)
        kinds = sorted((p["kind"], p["rule"]) for p in doc["proposals"] if p["status"] == "pending")
        self.assertEqual([("id_conflict", "L1-bib-mismatch"), ("resolution", "R-ambiguous"),
                          ("same_report", "L2-title-author-year")], kinds)
        ds = dedup.decide_batch(proj, "accept", "user:SzK")
        self.assertEqual(["duplicate_accept"], [d["kind"] for d in ds], "csak az L2-javaslat")
        self.assertFalse(any(d["kind"] == "id_confirm" for d in S.read_decisions(proj)))
        ds = dedup.decide_batch(proj, "accept", "user:SzK", kind="id_conflict")
        self.assertEqual(1, len(ds))
        # feloldási javaslatot tömegesen elfogadni explicit szűrővel sem lehet; elutasítani igen
        self.assertEqual([], dedup.decide_batch(proj, "accept", "user:SzK", kind="resolution"))
        self.assertEqual(1, len(dedup.decide_batch(proj, "reject", "user:SzK", kind="resolution")))

    def test_r14_bulk_candidate_confirm_skips_reference_list_items(self):
        """Az „összes javasolt megerősítése" nem teheti az irodalomjegyzék ismeretlen szerepű tételeit „bevont
        vizsgálattá" (az áttekintésnek tulajdonított hamis bevonás-állítás)."""
        proj = TM.build_project(self.tmp)
        rv = S.load_review(proj, TM.RV_A)
        ev = dict(rv["evidence"][0], evidence_id="ev-%s-0009" % TM.RV_A, kind="reference_list",
                  strategy="reflist_api", quote="Background B. A narrative review of X. 2012")
        rv["evidence"].append(ev)
        rv["candidates"].append(TM._cand("c0009", None, "Background B. A narrative review of X. 2012",
                                         "Background 2012", [ev["evidence_id"]], role="unknown", confidence="low",
                                         status="proposed"))
        S.save_review(proj, rv)
        rc, env = run_cli("confirm", proj, "--all-candidates", "--review", TM.RV_A, "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        self.assertEqual(["%s#c0003" % TM.RV_A], [d["target"] for d in env["data"]["decisions"]])
        self.assertTrue(any(w["code"] == "W-UNKNOWN-ROLE" for w in env["warnings"]))
        rv = S.load_review(proj, TM.RV_A)
        c9 = [c for c in rv["candidates"] if c["cand_id"] == "c0009"][0]
        self.assertEqual(("unknown", "proposed"), (c9["role_in_review"], c9["status"]))
        # csak ismeretlen szerepű maradt → használati hiba, nincs döntés
        n = len(S.read_decisions(proj))
        rc, env = run_cli("confirm", proj, "--all-candidates", "--review", TM.RV_A, "--actor", "user:SzK")
        self.assertEqual(2, rc)
        self.assertEqual(n, len(S.read_decisions(proj)))
        # egyenként (kifejezett emberi döntéssel) megerősíthető — ekkor lesz bevont (role_origin rögzíti)
        rc, env = run_cli("confirm", proj, "--target", "%s#c0009" % TM.RV_A, "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        c9 = [c for c in S.load_review(proj, TM.RV_A)["candidates"] if c["cand_id"] == "c0009"][0]
        self.assertEqual("included", c9["role_in_review"])
        self.assertEqual("unknown", c9["role_origin"]["extracted"])


# =============================================================================================
# R10: emberi feloldási döntés
# =============================================================================================

class TestHumanResolution(_Base):

    def _clients(self):
        pm = TL.FakePubMed(records=[
            TL.summary("99000501", "Vitamin D and fracture risk in elderly men", "Jones K", 2011),
            TL.summary("99000502", "Soy isoflavones and CRP in postmenopausal women", "Smith J", 2010),
            TL.summary("99000503", "BCG vaccination in Chicago infants", "Rosenthal SR", 1945)])
        return {"pubmed": pm, "europepmc": TL.FakeEPMC(), "ctgov": TL.FakeCtgov()}

    def test_r10_human_choice_with_mismatching_record_warns_but_is_kept(self):
        c = TL.mk_cand("c0001", "Smith J. Soy isoflavones and CRP. 2010",
                       title="Soy isoflavones and CRP in postmenopausal women", first_author="Smith J", year=2010)
        rv = TL.mk_review("rv-pmid-90000401", 2020, [c])
        d = dedup.make_decision("id_confirm", "candidate", "rv-pmid-90000401#c0001", "pmid:99000501", "user:SzK",
                                now=AT)
        res = resolve.Resolver(clients=self._clients(), now=AT).run([rv], None, [d])
        rec = [r for r in res["records"] if r["rec_id"] == "rec-pmid-99000501"][0]
        self.assertEqual("human-choice", rec["resolution"]["method"])
        self.assertIn("human_choice_bib_mismatch", rec["resolution"]["notes"])
        self.assertTrue(any(w["code"] == "id_title_mismatch" for w in res["warnings"]))
        # helyes azonosító → nincs figyelmeztetés
        d2 = dedup.make_decision("id_confirm", "candidate", "rv-pmid-90000401#c0001", "pmid:99000502", "user:SzK",
                                 now=AT)
        res2 = resolve.Resolver(clients=self._clients(), now=AT).run([rv], None, [d2])
        self.assertFalse(any(w["code"] == "id_title_mismatch" for w in res2["warnings"]))

    def test_r10_year_differs_acceptance_is_surfaced(self):
        c = TL.mk_cand("c0001", "Rosenthal SR. BCG vaccination in Chicago infants. 1961",
                       title="BCG vaccination in Chicago infants", first_author="Rosenthal SR", year=1961,
                       ids={"pmid": TL.idv("99000503")})
        rv = TL.mk_review("rv-pmid-90000402", 2020, [c])
        res = resolve.Resolver(clients=self._clients(), now=AT).run([rv], None, [])
        self.assertIn("rec-pmid-99000503", [r["rec_id"] for r in res["records"]])
        w = [x for x in res["warnings"] if x["code"] == "resolution_year_differs"]
        self.assertEqual(1, len(w))
        self.assertIn("1961", w[0]["hu"])

    def test_r10_cli_candidate_level_id_confirm(self):
        proj = TM.build_project(self.tmp)
        rc, env = run_cli("decide", proj, "--target", "%s#c0002" % TM.RV_A, "--value", "pmcid:PMC9100102",
                          "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        d = S.read_decisions(proj)[-1]
        self.assertEqual(("id_confirm", "candidate", "%s#c0002" % TM.RV_A, "pmcid:PMC9100102"),
                         (d["kind"], d["target"]["type"], d["target"]["id"], d["value"]))
        self.assertIn("resolve", env["next"])
        # ágens nem dönthet
        rc, env = run_cli("decide", proj, "--target", "%s#c0002" % TM.RV_A, "--value", "pmid:99100102",
                          "--actor", "agent:ma-metaheadhunter")
        self.assertEqual(2, rc)
        # azonosító rekord-/vizsgálat-célra NEM lesz szűrési bevonás (korábban: screen include, EP4!)
        n = len(S.read_decisions(proj))
        for target in ("rec-pmid-99100102", TM.study_of(proj, "rec-pmid-99100102")):
            rc, env = run_cli("decide", proj, "--target", target, "--value", "pmid:99100102", "--actor", "user:SzK")
            self.assertEqual(2, rc, env)
        # option:N jelöltre értelmetlen (nem lehet belőle jelölt-megerősítés)
        rc, env = run_cli("decide", proj, "--target", "%s#c0002" % TM.RV_A, "--value", "option:1",
                          "--actor", "user:SzK")
        self.assertEqual(2, rc, env)
        self.assertEqual(n, len(S.read_decisions(proj)))

    def test_r10_gui_accepts_the_same_decision_values_as_the_cli(self):
        from ma_gui.routes import headhunter as H
        for v in ("keep_retracted", "no_identifier", "pmid:99100102", "doi:10.5555/x.1", "pmcid:PMC9100102",
                  "nct:NCT09000001", "eid:2-s2.0-85000000001", "openalex:W90000001", "option:2"):
            self.assertTrue(H.VALUE_RE.match(v), v)
        for v in ("drop table", "pmcid:123", "nct:123", "keep"):
            self.assertFalse(H.VALUE_RE.match(v), v)


# =============================================================================================
# R11–R12: adatvédelem / elszámoltathatóság
# =============================================================================================

class TestPrivacyAndAccountability(_Base):

    def test_r11_fulltext_guard_scans_whole_body(self):
        front = b"<article><front><article-meta>" + b"<contrib>Author</contrib>" * 30000 + b"</article-meta></front>"
        body = front + b"<body><sec><p>full text</p></sec></body></article>"
        self.assertGreater(len(front), 400000)
        self.assertTrue(net._looks_like_fulltext(body))
        self.assertFalse(net._looks_like_fulltext(b'{"resultList": {"result": []}}'))

    def test_r12_screen_import_requires_actor_and_fills_empty_cells(self):
        proj = TM.build_project(self.tmp)
        fp = os.path.join(self.tmp, "screen.csv")
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write("rec_id;level;decision;reason_code;actor\n"
                     "rec-pmid-99100101;title_abstract;include;;\n"
                     "rec-pmid-99100102;title_abstract;include;;user:Masodik\n")
        rc, env = run_cli("screen", proj, "import", fp)
        self.assertEqual(2, rc)
        rc, env = run_cli("screen", proj, "import", fp, "--actor", "agent:x")
        self.assertEqual(2, rc)
        rc, env = run_cli("screen", proj, "import", fp, "--actor", "user:SzK")
        self.assertEqual(0, rc, env)
        self.assertEqual(2, env["data"]["n"])
        actors = [d["actor"] for d in S.read_decisions(proj) if d["kind"] == "screen"]
        self.assertEqual(["user:SzK", "user:Masodik"], actors)


if __name__ == "__main__":
    unittest.main()
