# -*- coding: utf-8 -*-
"""Metaheadhunter-végpontok (ma_gui/routes/headhunter.py; TERV_metaheadhunter.md 17. fejezet) a VALÓDI munkapad-
szerverrel (port 0; a szerver a saját válaszait is sémán ellenőrzi).

- Szintetikus headhunter-projekt (``build_project``): három forrás-áttekintés (kettő kiválasztva), hat közlemény, hat
  vizsgálat, két függő javaslat. A PMID-ek (99…) és a címek SZINTETIKUSAK — nem valós közlemények; a fájlok a
  headhunter-sémáknak megfelelnek. Az állapotot (state.json) a valódi CLI ``init``-je hozza létre.
- A valódi CLI alfolyamatként (offline lépések: init, overlap, merge, prisma, decide, update --offline): a route
  csak az argv-t építi és a kimenetet adja tovább; a motor számait (CCA, PRISMA) nem számolja újra.
- Hamis CLI (``CLI_PREFIX``): lassú lépés folyamatnaplóval és együttműködő megszakítással (CANCEL), időkorlát,
  érthetetlen kimenet (502 HH_CLI_FAILED), egyszerre egy író futás (409 HH_RUN_ACTIVE), titok-szivárgás (H016).
- Döntések: If-Match kötelező (hiányzik → 400, elavult → 409), a szereplő a munkamenet felhasználója (a törzsben
  nem adható meg), PHI-gyanús indoklás → 403 (és nem kerül a decisions.jsonl-ba), a CLI magyar hibaüzenete 422-vel.
- 6.8: a route a motorból csak a ``metaelemzes.api``-t importálja; worker-célpontot nem használ."""
import ast
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_routes_harness as H  # noqa: E402
from ma_gui import schema_lite  # noqa: E402
from ma_gui.routes import headhunter as HH  # noqa: E402

ROOT = H.ROOT
AT = "2026-10-05T10:00:00Z"
RV_A, RV_B, RV_C = "rv-pmid-99000001", "rv-pmid-99000002", "rv-pmid-99000003"
QUESTION = "Csökkenti-e a szintetikus X-szer a halálozást Y-betegségben? (szintetikus példa)"
SECRET_KEY = "TESTKEY-SCOPUS-7f3a9c1e5b"
SECRET_MAIL = "kutato.titok@example.org"


# =============================================================================================
# szintetikus projekt (a fixture-generátor is ezt használja: test_headhunter_fixtures.py)
# =============================================================================================

def idv(value, source="pubmed", via="pubmed.esummary"):
    return {"value": value, "source": source, "via": via, "at": AT}


def _ev(rid, n, quote, kind="table_row", strategy="jats_table", label="Table 1", row=None, section=None, conf="high"):
    return {"evidence_id": "ev-%s-%04d" % (rid, n), "review_id": rid, "kind": kind, "strategy": strategy,
            "locator": {"container": "PMC99%s" % rid[-2:], "label": label, "row": row, "section": section},
            "quote": quote, "extracted_by": "tool:headhunter", "at": AT, "confidence": conf}


def _cand(cid, rec_id, text, label, evs, role="included", conf="high", status="confirmed", secondary=None, group=None,
          ids=None):
    first, year = label.split()[0], int(label.split()[1])
    return {"cand_id": cid, "cited_as": {"text": text, "first_author": first, "year": year},
            "study_label_in_review": label, "group_key": group, "ids": ids or {}, "rec_id": rec_id,
            "role_in_review": role, "evidence_ids": [e["evidence_id"] for e in evs], "confidence": conf,
            "status": status, "secondary_data": secondary or [], "decision_ids": []}


def _review(rid, status, title, author, year, cands, evidence, search_date=None, sd_ev=None, k=None, k_ev=None,
            cochrane=False, oa=True, score=0.5, comps=None, fallback=False):
    doc = {"schema": "szk.ma.headhunter.review/v1", "model": "szk.ma.headhunter/v1", "review_id": rid,
           "status": status, "ids": {"pmid": idv(rid.split("-")[-1], via="pubmed.esearch")},
           "bib": {"title": title, "first_author": author, "authors": [author], "year": year,
                   "journal": "Synthetic J Rev"},
           "is_cochrane": cochrane, "found_by": ["s-pubmed-20261005T090000Z"],
           "rank": {"score": score, "components": comps or {"relevance": 0.8, "recency": 0.5}},
           "signals": {"meta_analysis": True, "protocol_registered": cochrane, "open_fulltext": oa,
                       "retracted": False, "prisma_mentioned": True, "rob_assessed": True, "evidence_ids": []},
           "fulltext": {"available": oa, "route": "europepmc_oa" if oa else "none",
                        "license": "cc by" if oa else None, "checked_at": AT},
           "candidates": cands, "evidence": evidence, "decision_ids": []}
    if search_date or fallback:
        doc["search_date"] = {"value": search_date, "precision": "month" if search_date and len(search_date) == 7
                              else ("day" if search_date else "unknown"), "fallback": fallback, "evidence_id": sd_ev}
    if k is not None:
        doc["k_reported"] = {"value": k, "unit": "studies", "evidence_id": k_ev}
    return doc


def _record(pmid_or_id, title, author, year, origins, nct=None, ids=None, resolution=None):
    rec_id = pmid_or_id if pmid_or_id.startswith("rec-") else "rec-pmid-%s" % pmid_or_id
    rec = {"rec_id": rec_id, "ids": ids if ids is not None else {"pmid": idv(pmid_or_id)},
           "bib": {"title": title, "first_author": author, "authors": [author, "Other B"], "year": year,
                   "journal": "Synthetic Trials J"},
           "flags": {}, "related": [],
           "origins": [{"route": "review_extraction", "review_id": r, "cand_id": c, "search_id": None}
                       for r, c in origins],
           "retrievals": [], "resolution": resolution or {"status": "resolved", "method": "pub-id", "score": 1.0},
           "status": "active", "merged_into": None}
    if nct:
        rec["ids"]["nct"] = idv(nct, via="pubmed.efetch.databank")
    return rec


def _study(sid, label, rec_id, reviews, role="primary", registry=None):
    return {"study_id": sid, "label": label, "registry_ids": [idv(registry, via="pubmed.efetch.databank")]
            if registry else [], "reports": [{"rec_id": rec_id, "role": role, "role_source": "rule"}],
            "reviews": reviews, "decision_ids": []}


def run_cli(*args):
    r = subprocess.run([sys.executable, "-m", "metaelemzes.headhunter"] + list(args) + ["--json"], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    try:
        env = json.loads(r.stdout)
    except ValueError:
        raise AssertionError("a CLI nem adott JSON-t: %s %s" % (r.stdout[-500:], r.stderr[-1500:]))
    return r.returncode, env


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def build_project(proj, actor="user:SzK"):
    """A headhunter-mappa egy MEGLÉVŐ projektmappában; a state.json-t a valódi CLI init-je írja."""
    code, env = run_cli("init", proj, "--question", QUESTION, "--population", "Y disease", "--intervention",
                        "drug X", "--actor", actor)
    assert code == 0, env
    hh = os.path.join(proj, "01_kereses", "headhunter")
    sec = "References to studies included in this review"
    ev_a = [_ev(RV_A, 1, "Smith 2015 {published data only}", "reference_section", "jats_cochrane_included", None,
                section=sec),
            _ev(RV_A, 2, "Jones 2016 {published data only}", "reference_section", "jats_cochrane_included", None,
                section=sec),
            _ev(RV_A, 3, "Kim 2017 | 40 | RCT | 12 weeks", row=3, conf="medium"),
            _ev(RV_A, 4, "We searched the databases up to June 2020.", "text", "jats_text", None, section="Methods"),
            _ev(RV_A, 5, "Three trials were included.", "text", "jats_text", None, section="Results")]
    rev_a = _review(RV_A, "selected", "Drug X for Y disease (synthetic Cochrane-style review)", "Author A", 2021, [
        _cand("c0001", "rec-pmid-99100101", "Smith A, et al. Drug X in Y. Synthetic Trials J 2015;1:1-9.",
              "Smith 2015", [ev_a[0]]),
        _cand("c0002", "rec-pmid-99100102", "Jones B. X therapy trial. Synthetic Trials J 2016;2:10-19.",
              "Jones 2016", [ev_a[1]]),
        _cand("c0003", "rec-pmid-99100105", "Kim C. X for Y. 2017.", "Kim 2017", [ev_a[2]], conf="medium",
              status="proposed"),
    ], ev_a, search_date="2020-06", sd_ev=ev_a[3]["evidence_id"], k=3, k_ev=ev_a[4]["evidence_id"], cochrane=True,
        score=0.82, comps={"relevance": 0.9, "recency": 0.6, "size": 0.4, "cochrane": 1.0, "open_fulltext": 1.0})
    ev_b = [_ev(RV_B, 1, "Smith 2015 | 52 | 48 | RCT", row=1),
            _ev(RV_B, 2, "Brown 2018 | 120 | 118 | pragmatic RCT", row=2, conf="medium"),
            _ev(RV_B, 3, "Brown 2019 (follow-up of Brown 2018)", row=3, conf="low"),
            _ev(RV_B, 4, "The last search was run on 15 March 2021.", "text", "jats_text", None, section="Methods"),
            _ev(RV_B, 5, "Four randomised trials were included.", "text", "jats_text", None, section="Results")]
    rev_b = _review(RV_B, "selected", "Effect of drug X on mortality in Y: a systematic review and meta-analysis "
                                      "(synthetic)", "Author B", 2022, [
        _cand("c0001", "rec-pmid-99100101", "Smith A et al. 2015", "Smith 2015", [ev_b[0]],
              secondary=[{"field": "n1", "value": 52, "evidence_id": ev_b[0]["evidence_id"], "status": "unverified",
                          "outcome": "mortality", "unit": None, "arm": None}]),
        _cand("c0002", "rec-pmid-99100103", "Brown D. Large trial of X. 2018.", "Brown 2018", [ev_b[1]],
              conf="medium", group="g-brown"),
        _cand("c0003", "rec-pmid-99100104", "Brown D. Long-term follow-up. 2019.", "Brown 2019", [ev_b[2]],
              role="included_companion", conf="low", status="proposed", group="g-brown"),
    ], ev_b, search_date="2021-03-15", sd_ev=ev_b[3]["evidence_id"], k=4, k_ev=ev_b[4]["evidence_id"], score=0.71)
    ev_c = [_ev(RV_C, 1, "Old 2001 | 30", row=1)]
    rev_c = _review(RV_C, "candidate", "Narrative overview of X (synthetic)", "Author C", 2012, [
        _cand("c0001", "rec-pmid-99100106", "Old E. 2001.", "Old 2001", [ev_c[0]], conf="medium", status="proposed")],
        ev_c, fallback=True, oa=False, score=0.35, comps={"relevance": 0.5, "recency": 0.1})
    for doc in (rev_a, rev_b, rev_c):
        write_json(os.path.join(hh, "reviews", doc["review_id"] + ".json"), doc)
    recs = [
        _record("99100101", "Drug X reduces mortality in adults with Y: a randomised trial (synthetic)", "Smith A",
                2015, [(RV_A, "c0001"), (RV_B, "c0001")]),
        _record("99100102", "X therapy versus placebo in Y (synthetic)", "Jones B", 2016, [(RV_A, "c0002")]),
        _record("99100103", "A large pragmatic trial of drug X (synthetic)", "Brown D", 2018, [(RV_B, "c0002")],
                nct="NCT09000001"),
        _record("99100104", "Long-term follow-up of the drug X pragmatic trial (synthetic)", "Brown D", 2019,
                [(RV_B, "c0003")], nct="NCT09000001"),
        _record("99100105", "Effect of X on Y outcomes (synthetic)", "Kim C", 2017, [(RV_A, "c0003")]),
        _record("rec-x-0a1b2c3d4e", "X-therapy versus placebo in Y (synthetic) [conference abstract]", "Jones B", 2016,
                [(RV_A, "c0002")], ids={}, resolution={"status": "unresolved", "method": None, "score": None}),
    ]
    studies = [_study("st-0001", "Smith 2015", "rec-pmid-99100101", [RV_A, RV_B]),
               _study("st-0002", "Jones 2016", "rec-pmid-99100102", [RV_A]),
               _study("st-0003", "Brown 2018", "rec-pmid-99100103", [RV_B], registry="NCT09000001"),
               _study("st-0004", "Brown 2019", "rec-pmid-99100104", [RV_B], role="unknown", registry="NCT09000001"),
               _study("st-0005", "Kim 2017", "rec-pmid-99100105", [RV_A]),
               _study("st-0006", "Jones 2016a", "rec-x-0a1b2c3d4e", [RV_A], role="abstract")]
    props = [
        {"proposal_id": "p-l2-0001", "kind": "same_report", "items": ["rec-pmid-99100102", "rec-x-0a1b2c3d4e"],
         "certainty": "probable", "score": 0.93, "rule": "L2: title >= 0.90, first author, year diff <= 1",
         "features": {"title_sim": 0.93, "first_author_match": True, "year_diff": 0, "journal_match": True,
                      "shared_ids": [], "shared_registry": [], "conflicting_ids": []},
         "status": "pending", "decision_id": None,
         "explanation": {"hu": "Valószínűleg ugyanaz a közlemény: a cím nagyon hasonló, az első szerző és az év "
                               "egyezik.",
                         "en": "Probably the same report: very similar title, same first author and year."}},
        {"proposal_id": "p-l3-0001", "kind": "same_study", "items": ["st-0003", "st-0004"], "certainty": "probable",
         "score": None, "rule": "L3: shared strong registry link",
         "features": {"title_sim": 0.61, "first_author_match": True, "year_diff": 1, "journal_match": True,
                      "shared_ids": [], "shared_registry": ["NCT09000001"], "conflicting_ids": []},
         "status": "pending", "decision_id": None,
         "explanation": {"hu": "Ugyanaz a vizsgálat lehet: mindkét közlemény ugyanazt a regiszterszámot nevezi meg.",
                         "en": "Possibly the same study: both reports name the same registration."}},
    ]
    write_json(os.path.join(hh, "studies.json"), {"schema": "szk.ma.headhunter.studies/v1",
                                                  "model": "szk.ma.headhunter/v1", "generated": AT,
                                                  "records": recs, "studies": studies, "proposals": props})
    return hh


# =============================================================================================
# hamis CLI (lassú lépés, megszakítás, időkorlát, hibás kimenet, titok)
# =============================================================================================

FAKE_CLI = r'''
import json, os, sys, time
ctl = json.load(open(%(ctl)r, encoding="utf-8"))
args = sys.argv[1:]
cmd, proj = args[0], args[1]
mode = ctl.get("mode", "ok")
with open(%(argv_log)r, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")
if mode == "garbage":
    sys.stderr.write("Traceback: boom api_key=%(key)s mailto=%(mail)s\n")
    print("ez nem JSON")
    sys.exit(1)
if mode == "slow":
    step = {"find-reviews": "find_reviews", "update-search": "update_search", "extract": "extract",
            "resolve": "resolve"}.get(cmd, cmd)
    rid = time.strftime("%%Y%%m%%dT%%H%%M%%SZ", time.gmtime()) + "-abc123"
    d = os.path.join(proj, "01_kereses", "headhunter", "runs", rid)
    os.makedirs(d, exist_ok=True)
    json.dump({"run_id": rid, "step": step, "status": "running"}, open(os.path.join(d, "run.json"), "w"))
    for i in range(int(ctl.get("ticks", 200))):
        with open(os.path.join(d, "progress.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "2026-10-05T10:00:00Z", "step": step, "phase": "search", "done": i,
                                 "total": 200, "source": "pubmed",
                                 "message": {"hu": "keresés %%d" %% i, "en": "search %%d" %% i}}) + "\n")
        if os.path.exists(os.path.join(d, "CANCEL")) and not ctl.get("ignore_cancel"):
            print(json.dumps({"ok": True, "data": {"cancelled": True}, "warnings": [], "errors": [], "pending": [],
                              "next": None, "exit_code": 0, "command": cmd}))
            sys.exit(0)
        time.sleep(0.05)
env = {"ok": True, "data": {"echo": cmd, "note": "kulcs: %(key)s, e-mail: %(mail)s, út: " + proj},
       "warnings": [{"code": "W", "hu": "figyelmeztetés %(mail)s", "en": "warning"}], "errors": [], "pending": [],
       "next": "status " + proj, "exit_code": int(ctl.get("exit", 0)), "command": cmd}
sys.stderr.write("stderr api_key=%(key)s\n")
print(json.dumps(env))
sys.exit(env["exit_code"])
'''


# =============================================================================================
# szerver
# =============================================================================================

class HHSrv(H.Srv):
    """Élő szerver a headhunter-végpontokkal (amíg a routes/__init__.py MODULES-ba nincs bekötve, itt vesszük fel)."""

    def __init__(self, proj, home, tmp, name="hh", **kw):
        kw.setdefault("kb_build", False)
        H.Srv.__init__(self, proj, home, tmp, name=name, **kw)
        if self.app.router.find("GET", "/api/headhunter/status") is None:
            HH.register(self.app.router)

    def wait_job(self, job, timeout=60.0):
        deadline = time.monotonic() + timeout
        while job.get("status") in ("queued", "running"):
            if time.monotonic() > deadline:
                raise AssertionError("a feladat nem fejeződött be: %s" % job)
            job = self.ok("GET", "/api/headhunter/runs/%s?wait=2" % job["job_id"])["data"]
        return job

    def run(self, step, options=None, timeout=60.0):
        st, _h, env = self.call("POST", "/api/headhunter/run", {"step": step, "options": options or {}})
        assert st == 202 and env and env.get("ok"), (st, env)
        return self.wait_job(env["data"], timeout)


def read_decisions(proj):
    path = os.path.join(proj, "01_kereses", "headhunter", "decisions.jsonl")
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def all_bytes(proj):
    out = []
    for dirpath, _d, files in os.walk(proj):
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as fh:
                out.append(fh.read())
    return b"\n".join(out)


class _Base(unittest.TestCase):
    BUILD = True

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_hh_routes_")
        cls.proj, cls.home = H.make_project(cls.tmp)
        if cls.BUILD:
            build_project(cls.proj)
        cls.srv = HHSrv(cls.proj, cls.home, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def status(self, cli=True):
        return self.srv.ok("GET", "/api/headhunter/status" + ("" if cli else "?cli=0"))


# =============================================================================================
# tesztek
# =============================================================================================

class NotInitialized(_Base):
    BUILD = False

    def test_a_status_and_conflicts_before_init(self):
        env = self.status()
        self.assertFalse(env["data"]["initialized"])
        self.assertEqual(env["data"]["steps"], [])
        st, err = self.srv.err("GET", "/api/headhunter/reviews")
        self.assertEqual((st, err["code"], err["details"]["hh_code"]), (409, "CONFLICT", "HH_NOT_INITIALIZED"))
        st, err = self.srv.err("POST", "/api/headhunter/decide", {"kind": "signoff"})
        self.assertEqual(err["details"]["hh_code"], "HH_NOT_INITIALIZED")
        st, err = self.srv.err("POST", "/api/headhunter/run", {"step": "dedupe"})
        self.assertEqual(err["details"]["hh_code"], "HH_NOT_INITIALIZED")
        src = self.srv.ok("GET", "/api/headhunter/sources")["data"]          # indulás előtt is: alapértékek a CLI-ből
        self.assertFalse(src["initialized"])
        self.assertEqual({r["source"] for r in src["rows"]} >= {"pubmed", "europepmc", "openalex", "scopus", "ctgov"},
                         True)
        self.assertIsNone(src["state_etag"])
        self.assertEqual(sorted(env["data"]["help"]), sorted(["intro", "sources", "pico", "reviews", "extract", "dedupe",
                                                              "overlap", "screen", "update", "merge"]))

    def test_b_init_via_real_cli_records_session_actor(self):
        st, err = self.srv.err("POST", "/api/headhunter/run", {"step": "init", "options": {}})
        self.assertEqual(st, 400)
        st, err = self.srv.err("POST", "/api/headhunter/run", {"step": "init", "options": {
            "question": "Kérdés — beteg TAJ: %s" % H.taj()}})
        self.assertEqual(st, 403)
        self.assertNotIn(H.taj(), json.dumps(err, ensure_ascii=False))
        job = self.srv.run("init", {"question": QUESTION, "population": "Y disease", "intervention": "drug X"})
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["exit_code"], 0)
        env = self.status()
        self.assertTrue(env["data"]["initialized"])
        self.assertEqual([s["id"] for s in env["data"]["steps"]], list(HH.STEP_ORDER))
        self.assertEqual({s["key"] for s in env["data"]["sources"]}, set(HH.SOURCE_KEYS))
        dec = read_decisions(self.proj)
        self.assertEqual(dec[-1]["kind"], "criteria_set")
        self.assertEqual(dec[-1]["actor"], "user:user")         # a munkamenet szereplője (App.actor = "user")
        st, err = self.srv.err("POST", "/api/headhunter/run", {"step": "init", "options": {"question": "Más"}})
        self.assertEqual((st, err["details"]["hh_code"]), (409, "HH_ALREADY_INITIALIZED"))
        acts = read_text(os.path.join(self.proj, "07_ellenorzes", "activity.jsonl"))
        self.assertIn("headhunter.run", acts)
        self.assertNotIn("szintetikus X-szer", acts)            # szabad szöveg nem kerül az activity-naplóba


class Views(_Base):
    def test_status_counts_files_and_cli(self):
        env = self.status()
        d = env["data"]
        self.assertTrue(d["initialized"])
        self.assertEqual(d["counts"]["reviews"], {"selected": 2, "candidate": 1, "total": 3})
        self.assertEqual(d["counts"]["candidates"], {"confirmed": 4, "proposed": 2})
        self.assertEqual((d["counts"]["records"], d["counts"]["studies"], d["counts"]["proposals_pending"]), (6, 6, 2))
        self.assertEqual(d["problems"], [])
        self.assertTrue(d["cli"]["available"], d["cli"])
        self.assertEqual(d["cli"]["checkpoints"]["EP2"], 2)
        self.assertEqual(d["cli"]["checkpoints"]["EP3"], 2)
        self.assertNotIn(self.proj, json.dumps(d, ensure_ascii=False))      # abszolút út helyett <projekt>
        self.assertTrue(env["_etag"])
        again = self.status()
        self.assertEqual(again["_etag"], env["_etag"])

    def test_sources_rows_and_decide_validation(self):
        src = self.srv.ok("GET", "/api/headhunter/sources?lang=en")["data"]
        self.assertTrue(src["initialized"])
        self.assertEqual(src["lang"], "en")
        self.assertEqual(src["state_etag"], self.status(cli=False)["data"]["files"]["state"]["etag"])
        scopus = [r for r in src["rows"] if r["source"] == "scopus"][0]
        self.assertFalse(scopus["enabled"])
        etag = src["state_etag"]
        for body in ({"kind": "decide", "target": "rv-pmid-1", "value": "include"},          # ismeretlen cél-fájl
                     {"kind": "decide", "target": "x-1", "value": "include"},
                     {"kind": "decide", "target": RV_A, "value": "drop table"},
                     {"kind": "batch", "value": "exclude", "batch": {"type": "all_proposals"}},
                     {"kind": "verify_secondary", "options": {"study": "st-0001", "field": "n1", "status": "verified"}},
                     {"kind": "sources", "options": {"enable": ["scopus"], "disable": ["scopus"]}},
                     {"kind": "sources", "options": {"enable": ["webofscience"]}}):
            st, err = self.srv.err("POST", "/api/headhunter/decide", body, headers=[("If-Match", etag)])
            self.assertIn(st, (400, 404), (body, err))

    def test_reviews_ranked_with_engine_scores(self):
        d = self.srv.ok("GET", "/api/headhunter/reviews")["data"]
        self.assertEqual([x["review_id"] for x in d["items"]], [RV_A, RV_B, RV_C])
        a = d["items"][0]
        self.assertEqual(a["rank"]["score_text"], "0.82")
        self.assertEqual(a["label"], "Author 2021")
        self.assertTrue(a["is_cochrane"])
        self.assertEqual(a["candidates"], {"proposed": 1, "confirmed": 2, "rejected": 0})
        self.assertTrue(a["ids"]["pmid"]["api"])
        sel = self.srv.ok("GET", "/api/headhunter/reviews?status=selected")["data"]
        self.assertEqual(len(sel["items"]), 2)

    def test_review_detail_has_evidence_and_locators(self):
        env = self.srv.ok("GET", "/api/headhunter/reviews/%s" % RV_B)
        d = env["data"]
        self.assertEqual(env["_etag"], d["summary"]["etag"].join('""'))
        c3 = [c for c in d["candidates"] if c["cand_id"] == "c0003"][0]
        self.assertEqual(c3["evidence"][0]["quote"], "Brown 2019 (follow-up of Brown 2018)")
        self.assertEqual(c3["evidence_missing"], [])
        self.assertEqual(d["search_date_evidence"]["locator"]["section"], "Methods")
        self.assertEqual(d["k_evidence"]["quote"], "Four randomised trials were included.")
        st, _err = self.srv.err("GET", "/api/headhunter/reviews/rv-pmid-12345678")
        self.assertEqual(st, 404)
        st, _err = self.srv.err("GET", "/api/headhunter/reviews/..%2Fstate")
        self.assertEqual(st, 400)

    def test_proposals_side_by_side(self):
        d = self.srv.ok("GET", "/api/headhunter/proposals")["data"]
        self.assertEqual([p["proposal_id"] for p in d["items"]], ["p-l2-0001", "p-l3-0001"])
        p = d["items"][0]
        self.assertEqual([r["rec_id"] for r in p["records"]], ["rec-pmid-99100102", "rec-x-0a1b2c3d4e"])
        self.assertEqual(p["score_text"], "0.93")
        self.assertEqual(p["records"][1]["resolution"]["status"], "unresolved")
        s = d["items"][1]
        self.assertEqual([r["type"] for r in s["records"]], ["study", "study"])
        self.assertEqual(s["records"][0]["reports"][0]["ids"]["nct"]["value"], "NCT09000001")
        st = self.srv.ok("GET", "/api/headhunter/studies?section=records")["data"]
        self.assertEqual(st["counts"]["records"], 6)
        self.assertNotIn("studies", st)


class RealCliSteps(_Base):
    def test_overlap_merge_prisma_and_views(self):
        job = self.srv.run("overlap", {"level": "study", "csv": True})
        self.assertEqual(job["status"], "done", job)
        ov = self.srv.ok("GET", "/api/headhunter/overlap")["data"]
        self.assertTrue(ov["exists"])
        self.assertEqual((ov["N"], ov["r"], ov["c"]), (6, 5, 2))
        self.assertEqual(ov["cca_text"], "%.1f" % ov["cca_pct"])        # a motor értéke, csak szövegként
        self.assertEqual(ov["pairs"][0]["cca_text"], "%.1f" % ov["pairs"][0]["cca_pct"])
        self.assertEqual(ov["review_meta"][RV_A]["label"], "Author 2021")
        job = self.srv.run("merge")
        self.assertIn(job["status"], ("done",), job)
        self.assertTrue(job["needs_human"])                                  # nyitott EP-k → 4-es kód, nem hiba
        m = self.srv.ok("GET", "/api/headhunter/merged")["data"]
        self.assertTrue(m["exists"])
        self.assertFalse(m["final"])
        self.assertEqual(sum(m["status_counts"].values()), len(m["studies"]))
        self.assertEqual([r["code"] for r in m["exclusion_reasons"]][:2], ["X1", "X2"])
        job = self.srv.run("prisma")
        self.assertEqual(job["status"], "done", job)
        pr = self.srv.ok("GET", "/api/headhunter/prisma")["data"]
        self.assertTrue(pr["exists"])
        self.assertIn("other_methods_identified", pr["flow"])
        self.assertNotIn("hh", pr["flow"])
        self.assertEqual(pr["check"]["counts"]["other_methods_identified"], pr["flow"]["other_methods_identified"])
        job = self.srv.run("update_search", {"dry_run": True})
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["data"]["plan"]["window"]["latest_source_search"], "2021-03-15")
        self.assertFalse(self.srv.ok("GET", "/api/headhunter/update")["data"]["exists"])
        job = self.srv.run("update_search", {"offline": True})
        self.assertEqual((job["status"], job["partial"], job["hh_code"]), ("done", True, "HH_SOURCE_UNAVAILABLE"))
        up = self.srv.ok("GET", "/api/headhunter/update")["data"]
        self.assertTrue(up["exists"])
        self.assertTrue(up["window"]["decision_id"])                         # az ablak jóváhagyása: emberi döntés
        self.assertEqual(read_decisions(self.proj)[-1]["actor"], "user:user")
        runs = self.srv.ok("GET", "/api/headhunter/runs")["data"]
        self.assertGreaterEqual(len(runs["jobs"]), 5)
        self.assertIsNone(runs["active"])

    def test_decisions_if_match_actor_phi_and_cli_errors(self):
        rel_b = "01_kereses/headhunter/reviews/%s.json" % RV_B
        etag = self.srv.ok("GET", "/api/headhunter/reviews/%s" % RV_B)["_etag"]
        body = {"kind": "decide", "target": RV_B + "#c0003", "value": "include"}
        st, err = self.srv.err("POST", "/api/headhunter/decide", body)
        self.assertEqual((st, err["details"]["hh_code"]), (400, "PRECONDITION_REQUIRED"))
        st, err = self.srv.err("POST", "/api/headhunter/decide", body, headers=[("If-Match", '"%s"' % ("0" * 64))])
        self.assertEqual((st, err["details"]["hh_code"], err["details"]["path"]), (409, "PRECONDITION_FAILED", rel_b))
        st, err = self.srv.err("POST", "/api/headhunter/decide", dict(body, actor="user:Mallory"),
                               headers=[("If-Match", etag)])
        self.assertEqual(st, 400)                                            # a szereplő nem jöhet a törzsből
        n0 = len(read_decisions(self.proj))
        st, err = self.srv.err("POST", "/api/headhunter/decide",
                               {"kind": "decide", "target": RV_B + "#c0003", "value": "reject",
                                "reason": "a beteg TAJ-száma %s" % H.taj()}, headers=[("If-Match", etag)])
        self.assertEqual(st, 403)
        self.assertEqual(len(read_decisions(self.proj)), n0)
        st, err = self.srv.err("POST", "/api/headhunter/decide",
                               {"kind": "decide", "target": RV_B + "#c0003", "value": "reject"},
                               headers=[("If-Match", etag)])
        self.assertEqual((st, err["code"]), (422, "VALIDATION"))             # a CLI magyar üzenete: ok kell
        self.assertIn("okát", err["message"])
        env = self.srv.ok("POST", "/api/headhunter/decide", body, headers=[("If-Match", etag)])
        self.assertEqual(env["data"]["status"], "done")
        self.assertEqual(env["data"]["data"]["decisions"][0]["kind"], "candidate_confirm")
        self.assertNotEqual(env["data"]["etag"], etag)
        last = read_decisions(self.proj)[-1]
        self.assertEqual((last["actor"], last["kind"], last["target"]["id"]), ("user:user", "candidate_confirm",
                                                                                RV_B + "#c0003"))
        rv = self.srv.ok("GET", "/api/headhunter/reviews/%s" % RV_B)["data"]
        self.assertEqual([c["status"] for c in rv["candidates"] if c["cand_id"] == "c0003"], ["confirmed"])
        # javaslat (EP3) és szűrés (EP4) a studies.json ETag-jével
        s_etag = self.srv.ok("GET", "/api/headhunter/proposals")["_etag"]
        env = self.srv.ok("POST", "/api/headhunter/decide", {"kind": "decide", "target": "p-l2-0001", "value": "accept",
                                                             "reason": "azonos közlemény"},
                          headers=[("If-Match", s_etag)])
        self.assertEqual(env["data"]["data"]["decisions"][0]["kind"], "duplicate_accept")
        s_etag = env["data"]["etag"]
        env = self.srv.ok("POST", "/api/headhunter/decide", {"kind": "decide", "target": "st-0005", "value": "exclude",
                                                             "level": "full_text", "reason_code": "X4",
                                                             "reason": "nem közöl halálozást"},
                          headers=[("If-Match", s_etag)])
        kinds = [(d["level"], d["value"]) for d in env["data"]["data"]["decisions"]]
        self.assertIn(("full_text", "exclude"), kinds)
        st, err = self.srv.err("POST", "/api/headhunter/decide", {"kind": "decide", "target": "st-0005",
                                                                  "value": "exclude", "reason_code": "ZZ"},
                               headers=[("If-Match", env["data"]["etag"])])
        self.assertEqual(st, 422)
        # tömeges jelölt-megerősítés egy áttekintésre (a szűrő a döntésben)
        a_etag = self.srv.ok("GET", "/api/headhunter/reviews/%s" % RV_A)["_etag"]
        env = self.srv.ok("POST", "/api/headhunter/decide", {"kind": "batch", "value": "include",
                                                             "batch": {"type": "all_candidates", "review": RV_A}},
                          headers=[("If-Match", a_etag)])
        self.assertEqual(env["data"]["data"]["n"], 1)
        self.assertTrue(read_decisions(self.proj)[-1]["batch"])
        # forrásválasztás (source_config döntés) a state.json ETag-jével
        st_etag = self.status(cli=False)["data"]["files"]["state"]["etag"]
        env = self.srv.ok("POST", "/api/headhunter/decide", {"kind": "sources", "options": {"disable": ["openalex"]}},
                          headers=[("If-Match", st_etag)])
        srcs = {s["key"]: s for s in self.status(cli=False)["data"]["sources"]}
        self.assertFalse(srcs["openalex"]["enabled"])
        self.assertEqual(read_decisions(self.proj)[-1]["kind"], "source_config")
        # lezárás nyitott ellenőrzőponttal: a CLI megtagadja
        self.srv.run("merge")
        m_etag = self.srv.ok("GET", "/api/headhunter/merged")["_etag"]
        st, err = self.srv.err("POST", "/api/headhunter/decide", {"kind": "signoff"}, headers=[("If-Match", m_etag)])
        self.assertEqual((st, err["code"], err["details"]["hh_code"]), (409, "GATE_BLOCKED", "HH_CHECKPOINT_PENDING"))
        self.assertNotIn(self.proj, json.dumps(err, ensure_ascii=False))
        acts = read_text(os.path.join(self.proj, "07_ellenorzes", "activity.jsonl"))
        self.assertIn("headhunter.decide", acts)
        self.assertNotIn("nem közöl halálozást", acts)


class FakeCli(_Base):
    def setUp(self):
        self.ctl = os.path.join(self.tmp, "ctl.json")
        self.argv_log = os.path.join(self.tmp, "argv.log")
        script = os.path.join(self.tmp, "fake_cli.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(FAKE_CLI % {"ctl": self.ctl, "argv_log": self.argv_log, "key": SECRET_KEY, "mail": SECRET_MAIL})
        self.control(mode="ok")
        self.patches = [mock.patch.object(HH, "CLI_PREFIX", [sys.executable, script]),
                        mock.patch.dict(os.environ, {"MA_SCOPUS_APIKEY": SECRET_KEY, "MA_CONTACT_EMAIL": SECRET_MAIL})]
        for p in self.patches:
            p.start()
        HH.runner(self.srv.app).status_cache = (None, None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        HH.runner(self.srv.app).status_cache = (None, None)

    def control(self, **kw):
        with open(self.ctl, "w", encoding="utf-8") as fh:
            json.dump(kw, fh)

    def last_argv(self):
        with open(self.argv_log, encoding="utf-8") as fh:
            return json.loads(fh.read().splitlines()[-1])

    def test_argv_is_validated_and_options_use_equals_form(self):
        job = self.srv.run("find_reviews", {"query": "-rm -rf; \"drug X\"", "since": "2015", "max": 50,
                                            "sources": ["pubmed", "europepmc"], "offline": True})
        self.assertEqual(job["status"], "done")
        argv = self.last_argv()
        self.assertEqual(argv[:4], ["find-reviews", os.path.realpath(self.proj), "--json", "--lang"])
        self.assertIn("--query=-rm -rf; \"drug X\"", argv)
        self.assertIn("--sources=pubmed,europepmc", argv)
        self.assertIn("--max=50", argv)
        self.assertIn("--offline", argv)
        st, _err = self.srv.err("POST", "/api/headhunter/run", {"step": "find_reviews", "options": {"since": "tegnap"}})
        self.assertEqual(st, 400)
        st, _err = self.srv.err("POST", "/api/headhunter/run", {"step": "rm", "options": {}})
        self.assertEqual(st, 400)
        st, _err = self.srv.err("POST", "/api/headhunter/run", {"step": "extract", "options": {"pdf": "/etc/passwd"}})
        self.assertEqual(st, 400)
        job = self.srv.run("update_search", {"anchor": "manual", "start": "2021-01-01", "overlap_months": 3,
                                             "cite": "forward"})
        argv = self.last_argv()
        self.assertIn("--actor=user:user", argv)
        self.assertIn("--cite=forward", argv)
        st, _err = self.srv.err("POST", "/api/headhunter/run", {"step": "update_search",
                                                                "options": {"anchor": "manual"}})
        self.assertEqual(st, 400)

    def test_secrets_never_leave_the_server(self):
        job = self.srv.run("dedupe")
        text = json.dumps(job, ensure_ascii=False)
        self.assertNotIn(SECRET_KEY, text)
        self.assertNotIn(SECRET_MAIL, text)
        self.assertIn("«redacted»", text)
        self.assertNotIn(os.path.realpath(self.proj), text)
        self.assertIn("<projekt>", text)
        self.control(mode="garbage")
        job = self.srv.run("merge")
        self.assertEqual((job["status"], job["error"]["code"], job["error"]["hh_code"]),
                         ("error", "PLUGIN_FAILED", "HH_CLI_FAILED"))
        self.assertIn("api_key=«redacted»", job["error"]["stderr_tail"])
        self.assertNotIn(SECRET_KEY, json.dumps(job))
        self.assertNotIn(SECRET_MAIL, json.dumps(job))
        etag = self.srv.ok("GET", "/api/headhunter/reviews/%s" % RV_A)["_etag"]
        st, err = self.srv.err("POST", "/api/headhunter/decide", {"kind": "decide", "target": RV_A + "#c0003",
                                                                  "value": "include"}, headers=[("If-Match", etag)])
        self.assertEqual((st, err["code"], err["details"]["hh_code"]), (502, "PLUGIN_FAILED", "HH_CLI_FAILED"))
        self.assertNotIn(SECRET_KEY, json.dumps(err))
        self.control(mode="ok")
        env = self.status()
        self.assertNotIn(SECRET_KEY, json.dumps(env))
        log = self.srv.log.getvalue()
        self.assertNotIn(SECRET_KEY, log)
        self.assertNotIn(SECRET_KEY.encode(), all_bytes(self.proj))

    def test_one_writer_progress_and_cooperative_cancel(self):
        self.control(mode="slow", ticks=400)
        st, _h, env = self.srv.call("POST", "/api/headhunter/run", {"step": "find_reviews", "options": {}})
        self.assertEqual(st, 202)
        job = env["data"]
        self.assertEqual(job["status"], "running") if job["status"] != "queued" else None
        got = H.wait_for(lambda: self.srv.ok("GET", "/api/headhunter/runs/%s" % job["job_id"])["data"]["progress"],
                         timeout=15)
        self.assertTrue(got)
        self.assertEqual(got[-1]["step"], "find_reviews")
        st, err = self.srv.err("POST", "/api/headhunter/run", {"step": "dedupe"})
        self.assertEqual((st, err["details"]["hh_code"]), (409, "HH_RUN_ACTIVE"))
        etag = self.srv.ok("GET", "/api/headhunter/reviews/%s" % RV_A)["_etag"]
        st, err = self.srv.err("POST", "/api/headhunter/decide", {"kind": "decide", "target": RV_A + "#c0003",
                                                                  "value": "include"}, headers=[("If-Match", etag)])
        self.assertEqual(err["details"]["hh_code"], "HH_RUN_ACTIVE")
        stat = self.status(cli=False)["data"]
        self.assertEqual(stat["jobs"]["active"]["job_id"], job["job_id"])
        snap = self.srv.ok("POST", "/api/headhunter/runs/%s/cancel" % job["job_id"], {})["data"]
        self.assertTrue(snap["cancel_written"])
        self.assertTrue(snap["run_id"].endswith("-abc123"))
        done = self.srv.wait_job(snap, timeout=20)
        self.assertEqual(done["status"], "cancelled")
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "01_kereses", "headhunter", "runs", snap["run_id"],
                                                    "CANCEL")))
        st, _err = self.srv.err("POST", "/api/headhunter/runs/hh-000000000000/cancel", {})
        self.assertEqual(st, 404)
        st, _err = self.srv.err("GET", "/api/headhunter/runs/nem-ilyen")
        self.assertEqual(st, 400)

    def test_timeout_kills_the_step(self):
        self.control(mode="slow", ticks=2000, ignore_cancel=True)
        with mock.patch.dict(HH.STEPS, {"find_reviews": ("find-reviews", 1.0, "find_reviews", True)}):
            job = self.srv.run("find_reviews", timeout=30)
        self.assertEqual((job["status"], job["error"]["code"], job["error"]["hh_code"]), ("timeout", "TIMEOUT",
                                                                                         "HH_TIMEOUT"))
        self.assertIsNone(self.srv.ok("GET", "/api/headhunter/runs")["data"]["active"])

    def test_exit_codes_map_to_job_states(self):
        for code, status, partial, human in ((3, "done", True, False), (4, "done", False, True)):
            self.control(mode="ok", exit=code)
            job = self.srv.run("resolve")
            self.assertEqual((job["status"], job["partial"], job["needs_human"]), (status, partial, human))


class Lint(unittest.TestCase):
    def test_route_imports_only_the_facade(self):
        path = os.path.join(ROOT, "ma_gui", "routes", "headhunter.py")
        tree = ast.parse(read_text(path))
        mods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                mods.append(node.module or "")
                if node.module == "metaelemzes":
                    mods += ["metaelemzes.%s" % a.name for a in node.names]
        engine = sorted(m for m in mods if m.startswith("metaelemzes."))
        self.assertEqual(engine, ["metaelemzes.api"])
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("metaelemzes."):
                self.assertNotIn(":", node.value, "worker-célpont nem lehet a headhunter-route-ban")

    def test_request_schemas_are_schema_lite_clean(self):
        for sch in (HH.RUN_SCHEMA, HH.DECIDE_SCHEMA, HH.JOB_RESPONSE):
            self.assertEqual(schema_lite.check_schema(sch), [])

    def test_contract_registry_loads(self):
        reg = HH.registry()
        self.assertIn("urn:szk:contract:ma.headhunter.review:1", reg)
        self.assertEqual(HH.problems_of({"schema": "x"}, "overlap")[:1] != [], True)


if __name__ == "__main__":
    unittest.main()
