# -*- coding: utf-8 -*-
"""Az elemzés végpontjai HTTP-szinten (stdlib http.client), élő szerveren, ideiglenes BCG + Normand projekttel
(terv 2.6, 3.4, 4.3–4.6, 6.3, 8.4):

- POST /api/validate (piszkozat, nem ment; acknowledged a „Nem hiba — indoklás” döntésből), POST /api/convert;
- GET/PUT /api/specs (motor-ellenőrzés, If-Match/409, idempotens újraküldés, kanonikus mentés);
- POST /api/analyze explore (a motorral azonos számok; „legutolsó nyer”: superseded) és commit (run-mappa,
  run.json, projektnapló actor-ral, EGY activity-sor, ép lánc, nincs „external” sor a saját kimenetre);
- 409 elavult spec (data.sha256 ≠ a tábla mostani hash-e) és eltérő mentett spec; GET /api/runs (stale/X001),
  /plot (szk.ma.plot/v2 szerződés), /results, /report, aláírt letöltés; GET /api/kb/rules; GET /api/audit/project;
- a munkapad és a CLI (MA_ACTIVITY_LOG=1) ugyanabba a hash-láncba ír."""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_routes_harness as H  # noqa: E402
from metaelemzes import api  # noqa: E402
from ma_gui import activity, store  # noqa: E402

VREQ = "szk.ma.validate-request/v1"
BCG_HEADER = ["row_uid", "vizsgálat", "esemény1", "n1", "esemény2", "n2"]
SECRET_CELLS = ("Aronson", "13598", "12867", "Rosenthal")


def _bcg_rows():
    """A BCG-tábla nyers cellái (row_uid-dal), a fájlból."""
    header, rows, _meta = api.read_table(H.BCG)
    idx = [header.index(c) for c in ("vizsgálat", "esemény1", "n1", "esemény2", "n2")]
    return [["r%05d" % (i + 1)] + [r[j] for j in idx] for i, r in enumerate(rows)]


class AnalysisRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_routes_an_")
        cls.proj, cls.home = H.make_project(cls.tmp)
        cls.srv = H.Srv(cls.proj, cls.home, cls.tmp, name="an")

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ok(self, *a, **kw):
        return self.srv.ok(*a, **kw)

    def err(self, *a, **kw):
        return self.srv.err(*a, **kw)

    def copy_data(self, name, src=H.BCG):
        shutil.copy(src, os.path.join(self.proj, "03_adatok", name))
        return "03_adatok/" + name

    def activity(self):
        return activity.read_records(os.path.join(self.proj, activity.LOG_RELPATH))

    # ------------------------------------------------------------------ motor
    def test_engine_route_uses_facade(self):
        d = self.ok("GET", "/api/engine")["data"]
        self.assertIs(d["facade"], True)
        want = api.engine_info(selftest=False)
        self.assertEqual(d["options"], want["options"])
        self.assertEqual(d["measures"], want["measures"])
        self.assertEqual([r["id"] for r in d["rules"]], [r["id"] for r in want["rules"]])

    # ------------------------------------------------------------------ validálás, átváltás
    def test_validate_draft_is_not_saved_and_matches_engine(self):
        before = store.sha256_file(os.path.join(self.proj, H.O1))
        rows = _bcg_rows()
        rows[1][2] = "999"                                # esemény1 > n1 → V006
        body = {"schema": VREQ, "measure": "RR", "options": {}, "dataset": H.O1, "client_seq": 7,
                "table": {"header": BCG_HEADER, "rows": rows, "decimal_mark": None}}
        doc = self.ok("POST", "/api/validate", body)["data"]
        H.check_contract(self, doc, "szk.ma.validation/v1")
        direct = api.validate_table(BCG_HEADER, rows, "RR", {})
        self.assertEqual([(f["code"], f["row"], f.get("row_uid")) for f in doc["findings"]],
                         [(f["code"], f["row"], f.get("row_uid")) for f in direct["findings"]])
        self.assertEqual(doc["k_analysable"], direct["k_analysable"])
        v6 = [f for f in doc["findings"] if f["code"] == "V006"]
        self.assertTrue(v6 and v6[0]["row_uid"] == "r00002")
        self.assertTrue(all("acknowledged" in f and f["acknowledged"] is None for f in doc["findings"]))
        self.assertEqual(store.sha256_file(os.path.join(self.proj, H.O1)), before)      # nem mentett
        self.assertFalse([r for r in self.activity() if r["action"].startswith("validate")])

    def test_table_carries_engine_column_map(self):
        d = self.ok("GET", "/api/table?dataset=" + H.O1)["data"]
        self.assertEqual(d["column_map"]["study"], "vizsgálat")
        self.assertEqual(d["column_map"]["e1"], "esemény1")
        header = d["header"]
        rows = [r["cells"] for r in d["rows"]]
        self.assertEqual({k: v for k, v in d["column_map"].items() if k in ("study", "e1", "n1", "e2", "n2")},
                         {k: v for k, v in api.validate_table(header, rows, "RR")["column_map"].items()
                          if k in ("study", "e1", "n1", "e2", "n2")})

    def test_acknowledged_from_logged_decision(self):
        rows = _bcg_rows()
        rows[2][2] = "4000"                               # V006 a 3. soron
        body = {"schema": VREQ, "measure": "RR", "dataset": "03_adatok/ack.csv",
                "table": {"header": BCG_HEADER, "rows": rows}}
        f = [x for x in self.ok("POST", "/api/validate", body)["data"]["findings"] if x["code"] == "V006"][0]
        self.assertIsNone(f["acknowledged"])
        dec = self.ok("POST", "/api/log/decision", {
            "agent": "user", "decision": "V006 nem hiba", "rationale": "a közlemény így adja meg", "kb_refs": ["V006"],
            "context": {"kind": "validation", "dataset": "03_adatok/ack.csv", "row_uid": f["row_uid"],
                        "code": "V006", "fields": f["fields"]}})["data"]
        doc = self.ok("POST", "/api/validate", body)["data"]
        f2 = [x for x in doc["findings"] if x["code"] == "V006"][0]
        self.assertEqual(f2["acknowledged"], dec["id"])
        rec = [r for r in self.activity() if r["action"] == "log.decision" and r["details"].get("id") == dec["id"]][0]
        self.assertEqual(rec["details"]["context"]["code"], "V006")              # csak azonosítók, érték nem
        self.assertNotIn("4000", json.dumps(rec))
        # másik tábla ugyanazzal a sorral: nem jelölt
        other = dict(body, dataset="03_adatok/masik.csv")
        f3 = [x for x in self.ok("POST", "/api/validate", other)["data"]["findings"] if x["code"] == "V006"][0]
        self.assertIsNone(f3["acknowledged"])
        # a döntés felülírása (supersedes) után a jelölés megszűnik
        self.ok("POST", "/api/log/decision", {"decision": "mégis hiba", "rationale": "újraellenőrizve",
                                              "supersedes": dec["id"]})
        f4 = [x for x in self.ok("POST", "/api/validate", body)["data"]["findings"] if x["code"] == "V006"][0]
        self.assertIsNone(f4["acknowledged"])
        # a kontextus csak azonosítókat tartalmazhat (cellaérték-kulcs → 400)
        st, e = self.err("POST", "/api/log/decision", {"decision": "x", "context": {"kind": "validation",
                                                                                   "value": "4000"}})
        self.assertEqual((st, e["code"]), (400, "BAD_REQUEST"))

    def test_validate_request_contract_errors(self):
        st, e = self.err("POST", "/api/validate", {"schema": VREQ, "table": {"header": [], "rows": []}})
        self.assertEqual((st, e["code"]), (400, "BAD_REQUEST"))                     # hiányzó measure
        st, e = self.err("POST", "/api/validate", {"schema": "x", "measure": "RR", "table": {"header": [], "rows": []}})
        self.assertEqual(st, 400)
        st, e = self.err("POST", "/api/validate", {"schema": VREQ, "measure": "NINCS",
                                                   "table": {"header": ["study"], "rows": [["A"]]}})
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))

    def test_convert(self):
        req = {"schema": "szk.ma.convert-request/v1", "kind": "median_to_mean_sd",
               "inputs": {"n": "40", "median": "12,5", "q1": "10", "q3": "15"}, "method": "luo",
               "target": {"dataset": H.O2, "row_uid": "rabcd1", "fields": {"mean": "m1", "sd": "sd1"},
                          "decimal_mark": ","}, "client_seq": 3}
        doc = self.ok("POST", "/api/convert", req)["data"]
        H.check_contract(self, doc, "szk.ma.convert-result/v1")
        want = api.convert({k: v for k, v in req.items() if k != "client_seq"})
        self.assertEqual(doc, json.loads(json.dumps(want)))
        self.assertIs(doc["estimated"], True)
        self.assertIn(",", doc["cell_text"]["mean"])
        st, e = self.err("POST", "/api/convert", dict(req, kind="nincs_ilyen"))
        self.assertEqual(st, 400)
        st, e = self.err("POST", "/api/convert", dict(req, inputs={"n": "negyven"}))
        self.assertEqual((st, e["code"]), (422, "VALIDATION"))

    # ------------------------------------------------------------------ specek
    def test_specs_get_put_conflict_and_validation(self):
        self.assertEqual(self.err("GET", "/api/specs/nincs_ilyen")[0], 404)
        sp = H.spec("s_crud")
        env = self.ok("PUT", "/api/specs/s_crud", sp)
        etag = env["_etag"]
        self.assertTrue(etag)
        H.check_contract(self, env["data"], "szk.ma.analysis-spec/v1")
        path = os.path.join(self.proj, "05_elemzes", "specs", "s_crud.json")
        self.assertEqual(api.load_spec(path), env["data"])
        self.assertEqual('"%s"' % store.sha256_file(path), etag)
        got = self.ok("GET", "/api/specs/s_crud")
        self.assertEqual(got["_etag"], etag)
        names = [s["name"] for s in self.ok("GET", "/api/specs")["data"]["specs"]]
        self.assertIn("s_crud", names)
        n_before = len(self.activity())
        self.ok("PUT", "/api/specs/s_crud", sp)                       # azonos tartalom If-Match nélkül: idempotens
        self.assertEqual(len(self.activity()), n_before)
        changed = H.spec("s_crud", options={"measure": "RR", "model": "fixed"})
        st, e = self.err("PUT", "/api/specs/s_crud", changed)          # eltérő tartalom If-Match nélkül
        self.assertEqual((st, e["code"]), (409, "CONFLICT"))
        env2 = self.ok("PUT", "/api/specs/s_crud", changed, headers=[("If-Match", etag)])
        self.assertNotEqual(env2["_etag"], etag)
        st, e = self.err("PUT", "/api/specs/s_crud", sp, headers=[("If-Match", etag)])     # elavult etag
        self.assertEqual(st, 409)
        recs = [r for r in self.activity() if r["action"] == "spec.save" and r["details"].get("name") == "s_crud"]
        self.assertEqual(len(recs), 2)
        # név, alak, motor-ellenőrzés
        self.assertEqual(self.err("PUT", "/api/specs/Rossz.Nev", H.spec("rossz"))[0], 400)
        self.assertEqual(self.err("PUT", "/api/specs/egyik", H.spec("masik"))[0], 400)
        self.assertEqual(self.err("PUT", "/api/specs/opt", H.spec("opt", options={"measure": "RR", "nincs": 1}))[0], 400)
        st, e = self.err("PUT", "/api/specs/badval", H.spec("badval", options={"measure": "RR", "model": "kocka"}))
        self.assertIn(st, (400, 422))
        st, e = self.err("PUT", "/api/specs/trav", H.spec("trav", data="../kivul.csv"))
        self.assertIn(st, (400, 403))
        st, e = self.err("PUT", "/api/specs/ext", H.spec("ext", data="03_adatok/x.json"))
        self.assertEqual(st, 403)

    # ------------------------------------------------------------------ explore
    def test_explore_returns_engine_view_model(self):
        sp = H.spec("o1_explore")
        job = self.srv.job(self.ok("POST", "/api/analyze", {"mode": "explore", "spec": sp, "client_seq": 1})["data"])
        self.assertEqual(job["status"], "done", job)
        res = job["result"]
        direct = api.analyze(sp, mode="explore", project_root=self.proj)
        self.assertEqual(res["plot"], json.loads(json.dumps(direct["plot"])))
        self.assertEqual(res["run"]["k"], direct["run"]["k"])
        self.assertEqual(res["run"]["primary"]["display_text"], direct["run"]["primary"]["display_text"])
        self.assertIsNone(res["run"]["run_id"])
        self.assertEqual(res["run"]["outcome_id"], "o1")
        H.check_contract(self, res["plot"], "szk.ma.plot/v2")
        H.check_contract(self, res["run"], "szk.ma.run/v1")
        self.assertFalse(os.path.isdir(os.path.join(self.proj, "05_elemzes", "o1_explore")))
        # mentetlen piszkozatból is (a táblát a kérés hozza)
        rows = _bcg_rows()
        job2 = self.srv.job(self.ok("POST", "/api/analyze", {
            "mode": "explore", "spec": sp, "table": {"header": BCG_HEADER, "rows": rows}, "lane": "draft"})["data"])
        self.assertEqual(job2["status"], "done", job2)
        self.assertEqual(job2["result"]["run"]["primary"]["display_text"], direct["run"]["primary"]["display_text"])

    def test_explore_supersede_last_wins(self):
        big = [["S%d" % i, str(3 + i % 7), str(100 + i % 13), str(9 + i % 5), str(110 + i % 11)] for i in range(400)]
        table = {"header": ["study", "e1", "n1", "e2", "n2"], "rows": big}
        sp = H.spec("o1_lw")
        first = self.ok("POST", "/api/analyze", {"mode": "explore", "spec": sp, "table": table, "client_seq": 10,
                                                 "lane": "lw"})["data"]
        self.assertIn(first["status"], ("queued", "running"), first)       # k = 400: több másodperc
        # egy régebbi (kisebb client_seq-ű) kérés, amíg az újabb fut: eleve superseded
        late = self.ok("POST", "/api/analyze", {"mode": "explore", "spec": sp, "client_seq": 9, "lane": "lw"})["data"]
        self.assertEqual(late["status"], "superseded")
        self.assertEqual(late["superseded_by"], first["job_id"])
        # az újabb kérés kiszorítja a futót (1 s türelmi idő után leállítja)
        newer = self.ok("POST", "/api/analyze", {"mode": "explore", "spec": sp, "client_seq": 11, "lane": "lw"})["data"]
        newer = self.srv.job(newer)
        self.assertEqual(newer["status"], "done", newer)
        old = self.srv.job(self.ok("GET", "/api/jobs/%s" % first["job_id"])["data"])
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["superseded_by"], newer["job_id"])
        self.assertNotIn("result", old)
        # más sáv (lane) nem szorítja ki
        self.assertEqual(self.srv.job(self.ok("POST", "/api/analyze", {"mode": "explore", "spec": sp,
                                                                       "client_seq": 1, "lane": "masik"})["data"])["status"],
                         "done")
        self.assertEqual(self.srv.app.get_jobs()._timeout, 30.0)
        self.assertEqual(self.err("GET", "/api/jobs/j_0000000000000000")[0], 404)

    # ------------------------------------------------------------------ commit
    def _commit(self, sp, seq=1):
        self.ok("PUT", "/api/specs/%s" % sp["name"], sp)
        job = self.srv.job(self.ok("POST", "/api/analyze", {"mode": "commit", "spec": sp, "client_seq": seq})["data"])
        self.assertEqual(job["status"], "done", job)
        return job

    def test_commit_artifacts_journal_activity_and_runs(self):
        ds = self.copy_data("c1.csv")
        sp = H.spec("c1_primary", outcome="c1", data=ds)
        n_before = len(self.activity())
        job = self._commit(sp, seq=5)
        run = job["result"]["run"]
        rid = run["run_id"]
        self.assertEqual((run["mode"], run["outcome_id"], run["client_seq"]), ("commit", "c1", 5))
        H.check_contract(self, run, "szk.ma.run/v1")
        outdir = os.path.join(self.proj, "05_elemzes", "c1", rid)
        for name in ("results.json", "plot_data.json", "report.md", "run.json", "forest.svg"):
            self.assertTrue(os.path.isfile(os.path.join(outdir, name)), name)
        with open(os.path.join(outdir, "run.json"), encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["run_id"], rid)
        self.assertEqual(on_disk["equivalent_argv"], ["ma.py", "analyze", "--spec", "05_elemzes/specs/c1_primary.json",
                                                      "--out", "05_elemzes/c1/%s" % rid, "--project", "."])
        self.assertEqual(on_disk["data"]["sha256"], store.sha256_file(os.path.join(self.proj, ds)))
        # a projektnapló futás-sora a munkapad szereplőjével
        runs = api.project_list(self.proj, "runs")
        self.assertTrue([r for r in runs if r["outdir"].endswith(rid) and r["actor"] == "user"])
        # EGY activity-sor: argv, bemenetek, kimenetek hash-sel; ép lánc
        recs = self.activity()[n_before:]
        commits = [r for r in recs if r["action"] == "analyze.commit"]
        self.assertEqual(len(commits), 1)
        rec = commits[0]
        self.assertEqual(rec["argv"], on_disk["equivalent_argv"])
        self.assertEqual(rec["actor"], "user")
        self.assertEqual(rec["inputs"][ds], on_disk["data"]["sha256"])
        self.assertIn("05_elemzes/specs/c1_primary.json", rec["inputs"])
        self.assertEqual(rec["outputs"]["05_elemzes/c1/%s/run.json" % rid],
                         store.sha256_file(os.path.join(outdir, "run.json")))
        self.assertEqual(rec["result"]["exit_code"], 0)
        self.assertIn(run["primary"]["display_text"]["hu"], rec["result"]["summary"])
        # bájtra a CLI szövege: a motor run_summary_text-je a projektnapló futás-összefoglalójából
        jrow = [r for r in runs if r["outdir"].endswith(rid)][0]
        self.assertEqual(rec["result"]["summary"], api.run_summary_text(json.loads(jrow["summary"])))
        self.assertTrue(rec["result"]["summary"].startswith("k=13, RR "), rec["result"]["summary"])
        self.assertEqual(rec["details"]["run_id"], rid)
        self.assertEqual(job["result"]["activity"]["seq"], rec["seq"])
        ok, bad, msg = activity.verify_chain(self.proj)
        self.assertTrue(ok, msg)
        with open(os.path.join(self.proj, activity.LOG_RELPATH), encoding="utf-8") as fh:
            text = fh.read()
        for secret in SECRET_CELLS:
            self.assertNotIn(secret, text)
        # a saját kimenet (run.json) nem „külső szerkesztés”
        time.sleep(1.0)
        self.assertFalse([r for r in self.activity() if r["action"] == "file.external_edit"
                          and any(k.startswith("05_elemzes/") for k in r["outputs"])])
        # futás-lista, részletek, ábra-, eredmény- és riport-végpont, aláírt letöltés
        listed = [r for r in self.ok("GET", "/api/runs?outcome=c1")["data"]["runs"]]
        self.assertEqual([r["run_id"] for r in listed], [rid])
        self.assertIs(listed[0]["stale"], False)
        self.assertEqual(listed[0]["measure"], "RR")
        detail = self.ok("GET", "/api/runs/%s" % rid)["data"]
        self.assertIn("forest", detail["downloads"])
        plot = self.ok("GET", "/api/runs/%s/plot" % rid)["data"]
        H.check_contract(self, plot, "szk.ma.plot/v2")
        with open(os.path.join(outdir, "plot_data.json"), encoding="utf-8") as fh:
            self.assertEqual(plot, json.load(fh))
        self.assertEqual(plot["meta"]["run_id"], rid)
        results = self.ok("GET", "/api/runs/%s/results" % rid)["data"]
        self.assertIn("options", results)
        rep = self.ok("GET", "/api/runs/%s/report" % rid)["data"]
        with open(os.path.join(outdir, "report.md"), encoding="utf-8") as fh:
            self.assertEqual(rep["markdown"], fh.read())
        st, h, data = self.srv.raw("GET", detail["downloads"]["forest"]["url"])
        self.assertEqual(st, 200)
        self.assertTrue(h["content-type"].startswith("image/svg+xml"))
        self.assertIn("sandbox", h["content-security-policy"])
        with open(os.path.join(outdir, "forest.svg"), "rb") as fh:
            self.assertEqual(data, fh.read())
        st, _h, _d = self.srv.raw("GET", rep["url"])
        self.assertEqual(st, 200)
        self.assertEqual(self.err("GET", "/api/runs/20200101T000000Z-abcdef")[0], 404)
        self.assertEqual(self.err("GET", "/api/runs/..%2F..%2Fetc")[0], 400)
        # elsődleges futás kimenetenként
        prim = [r for r in self.ok("GET", "/api/runs?primary=1")["data"]["runs"] if r["outcome_id"] == "c1"]
        self.assertEqual([r["run_id"] for r in prim], [rid])
        self.assertEqual(prim[0]["primary"]["i2_text"], plot["heterogeneity"]["i2_text"])    # a motor szövege
        self.assertNotIn("grade", prim[0])                  # még nincs GRADE-ítélet a kimenetre
        # a projektnapló legutóbbi GRADE-ítélete kimenetenként (az áttekintő GRADE-oszlopa)
        api.project_grade(self.proj, "c1", "low", risk_of_bias="-1", rationale="teszt")
        g2 = api.project_grade(self.proj, "c1", "moderate", risk_of_bias="0", rationale="újraértékelve")["id"]
        prim = [r for r in self.ok("GET", "/api/runs?primary=1")["data"]["runs"] if r["outcome_id"] == "c1"]
        self.assertEqual(prim[0]["grade"], {"certainty": "moderate", "id": g2})
        self.assertNotIn("grade", self.ok("GET", "/api/runs?outcome=c1")["data"]["runs"][0])   # csak primary=1

    def test_commit_normand_continuous(self):
        sp = H.spec("o2_primary", outcome="o2", data=H.O2, measure="MD")
        job = self._commit(sp)
        run = job["result"]["run"]
        direct = api.analyze(sp, mode="explore", project_root=self.proj)
        self.assertEqual(run["primary"]["display_text"], direct["run"]["primary"]["display_text"])
        self.assertEqual(run["k"], direct["run"]["k"])
        self.assertEqual(job["result"]["plot"]["studies"], json.loads(json.dumps(direct["plot"]["studies"])))

    def test_stale_spec_conflict_and_spec_differs(self):
        ds = self.copy_data("st.csv")
        sp = H.spec("st_primary", outcome="st", data=ds)
        self.ok("PUT", "/api/specs/st_primary", sp)
        old_sha = store.sha256_file(os.path.join(self.proj, ds))
        job = self._commit(sp)
        rid = job["result"]["run"]["run_id"]
        # az adattábla megváltozik (Excel/ágens): a futás ELAVULT, az audit X001-et ad
        with open(os.path.join(self.proj, ds), "ab") as fh:
            fh.write("Új vizsgálat 2001;5;100;9;100;30;2001;random\n".encode("utf-8"))
        run = [r for r in self.ok("GET", "/api/runs?outcome=st")["data"]["runs"] if r["run_id"] == rid][0]
        self.assertIs(run["stale"], True)
        audit = self.ok("GET", "/api/audit/project")["data"]
        H.check_contract(self, audit, "szk.ma.project-audit/v1")
        self.assertTrue([f for f in audit["findings"] if f["code"] == "X001" and f.get("outcome") == "st"], audit)
        # a záró (FINAL) kapu a munkapadon alapból audit-kapuval megy: az elavult futás (X001) blokkol
        st, e = self.err("POST", "/api/log/checkpoint", {"stage": "FINAL", "verdict": "PASS"})
        self.assertEqual((st, e["code"]), (409, "GATE_BLOCKED"))
        self.assertIn("X001", [f["code"] for f in e["details"]["audit_errors"]])
        ok_fail = self.ok("POST", "/api/log/checkpoint", {"stage": "FINAL", "verdict": "FAIL", "summary": "elavult"})
        self.assertEqual(ok_fail["data"]["kind"], "checkpoint")
        # a régi hash-re rögzített commit: 409 (elavult), semmi nem íródik
        pinned = dict(sp, data={"path": ds, "sha256": old_sha})
        n_runs = len(os.listdir(os.path.join(self.proj, "05_elemzes", "st")))
        st, e = self.err("POST", "/api/analyze", {"mode": "commit", "spec": pinned})
        self.assertEqual((st, e["code"]), (409, "CONFLICT"))
        self.assertEqual(e["details"]["reason"], "stale_data")
        self.assertEqual(e["details"]["expected_sha256"], old_sha)
        self.assertEqual(len(os.listdir(os.path.join(self.proj, "05_elemzes", "st"))), n_runs)
        # a mentettől eltérő spec rögzítése: 409 (előbb mentsd)
        other = dict(sp, options={"measure": "RR", "model": "fixed"})
        st, e = self.err("POST", "/api/analyze", {"mode": "commit", "spec": other})
        self.assertEqual((st, e["code"], e["details"]["reason"]), (409, "CONFLICT", "spec_differs"))
        # a mostani adattal a rögzítés megy, és a régi futás továbbra is elavult
        self._commit(sp)
        # commit piszkozat-táblával nem engedett
        st, e = self.err("POST", "/api/analyze", {"mode": "commit", "spec": sp,
                                                  "table": {"header": ["study"], "rows": [["A"]]}})
        self.assertEqual(st, 400)

    def test_commit_from_private_data_refused(self):
        os.makedirs(os.path.join(self.proj, "_privat"), exist_ok=True)
        shutil.copy(H.BCG, os.path.join(self.proj, "_privat", "kohorsz.csv"))
        sp = H.spec("priv", outcome="priv", data="_privat/kohorsz.csv")
        st, e = self.err("POST", "/api/analyze", {"mode": "commit", "spec": sp})
        self.assertEqual((st, e["code"]), (403, "FORBIDDEN"))
        self.assertFalse(os.path.exists(os.path.join(self.proj, "05_elemzes", "priv")))
        # a feltárás (semmit nem ír) megengedett
        job = self.srv.job(self.ok("POST", "/api/analyze", {"mode": "explore", "spec": sp})["data"])
        self.assertEqual(job["status"], "done")

    def test_missing_data_and_bad_bodies(self):
        st, e = self.err("POST", "/api/analyze", {"mode": "explore", "spec": H.spec("nd", data="03_adatok/nincs.csv")})
        self.assertEqual((st, e["code"]), (404, "NOT_FOUND"))
        self.assertEqual(self.err("POST", "/api/analyze", {"mode": "futtat", "spec": H.spec("x")})[0], 400)
        self.assertEqual(self.err("POST", "/api/analyze", {"mode": "explore"})[0], 400)
        self.assertEqual(self.err("POST", "/api/analyze", {"mode": "explore", "spec": H.spec("x"), "extra": 1})[0], 400)

    def test_gate_blocked_lists_engine_blockers_and_actor(self):
        f = self.ok("POST", "/api/log/finding", {"severity": "blocker", "title": "SE/SD csere gyanú", "stage": "S09"})
        st, e = self.err("POST", "/api/log/checkpoint", {"stage": "S09", "verdict": "PASS"})
        self.assertEqual((st, e["code"]), (409, "GATE_BLOCKED"))
        self.assertIn(f["data"]["id"], [b["id"] for b in e["details"]["blockers"]])
        self.assertIn("blocker", e["message"])                              # a motor üzenete szó szerint
        self.ok("POST", "/api/log/resolve", {"id": f["data"]["id"], "status": "fixed", "resolution": "javítva"})
        cp = self.ok("POST", "/api/log/checkpoint", {"stage": "S09", "verdict": "PASS"})["data"]
        item = api.project_show(self.proj, "checkpoint", cp["id"])
        self.assertEqual(item["actor"], "user")
        self.assertEqual(api.project_show(self.proj, "finding", f["data"]["id"])["actor"], "user")

    # ------------------------------------------------------------------ KB, audit
    def test_kb_rules_for_field(self):
        d = self.ok("GET", "/api/kb/rules?field=model")["data"]
        self.assertEqual(d["field"], "model")
        want = api.kb_rules_for_field("model", db=self.srv.app.kb_db)
        self.assertEqual([i["id"] for i in d["items"]], [i["id"] for i in want["items"]])
        self.assertTrue(d["items"])
        self.assertEqual(self.err("GET", "/api/kb/rules?field=" + quote("a b"))[0], 400)
        self.assertEqual(self.err("GET", "/api/kb/rules")[0], 400)

    def test_audit_project_matches_engine(self):
        d = self.ok("GET", "/api/audit/project")["data"]
        want = api.project_audit(self.proj)
        self.assertEqual([(f["code"], f.get("outcome")) for f in d["findings"]],
                         [(f["code"], f.get("outcome")) for f in want["findings"]])
        self.assertEqual(self.err("GET", "/api/audit/project?stage=" + quote("S0<1"))[0], 400)

    # ------------------------------------------------------------------ közös lánc a CLI-vel
    def test_gui_and_cli_share_one_activity_chain(self):
        self.ok("PUT", "/api/specs/chain_a", H.spec("chain_a"))
        env = dict(os.environ, MA_ACTIVITY_LOG="1")
        env.pop("MA_ACTOR", None)
        subprocess.run([sys.executable, os.path.join(H.ROOT, "ma.py"), "project", "log", self.proj, "--agent", "user",
                        "--decision", "CLI-döntés a lánc közepén"], check=True, env=env, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ok("POST", "/api/log/finding", {"severity": "minor", "title": "munkapad-megállapítás"})
        d = self.ok("GET", "/api/log/activity?limit=1000")["data"]
        self.assertTrue(d["verify"]["ok"], d["verify"])
        actions = [r["action"] for r in d["items"]]
        i = actions.index("project.log")
        self.assertIn("spec.save", actions[:i])
        self.assertEqual(actions[-1], "log.finding")
        self.assertEqual(d["head"], activity.ActivityLog(self.proj).head())


if __name__ == "__main__":
    unittest.main()
