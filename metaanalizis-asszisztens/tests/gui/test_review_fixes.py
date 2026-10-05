# -*- coding: utf-8 -*-
"""Regressziós tesztek a 2026-10-05-i bírálat (websec / privacy / fidelity) szerveroldali javításaihoz.

Minden teszt neve és docstringje a bírálati azonosítót (WS-n, PRIV-n, FID-n) nevezi meg. A pillanatkép és az
audit-csomag folyamaton belül épül (``snapshot.build(app=…)`` — ugyanaz a rögzítő, mint a ``POST /api/export/*``
végpontoké); a végpont-tesztek az élő szervert hívják (``test_routes_harness.Srv``)."""
import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from metaelemzes import api as engine_api  # noqa: E402
from metaelemzes import spec as S  # noqa: E402
from ma_gui import audit_export, privacy, snapshot  # noqa: E402
from ma_gui.routes import analyze as analyze_route  # noqa: E402

import test_routes_harness as H  # noqa: E402
import test_snapshot as TS  # noqa: E402

SECRET = "TITKOS_"


def _write_private_copy(root, rel="_privat/titkos.csv"):
    """A BCG-tábla másolata a _privat/ alá, minden vizsgálatcímke TITKOS_-előtaggal (egyedi jelölő)."""
    with open(TS.BCG, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out = [lines[0]] + [SECRET + ln for ln in lines[1:] if ln.strip()]
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(out) + "\n")
    return path


def _cli_private_commit(root, name="o1_priv", data="_privat/titkos.csv", outdir="_privat/05_elemzes/o1_priv"):
    """Az a parancssori commit, amit a munkapad MSG_PRIVATE-je javasol (python ma.py analyze --spec … --out
    _privat/05_elemzes/… --project .) — a motor homlokzatán át, ugyanazzal a hatással (napló run-sora is)."""
    spec = {"schema": "szk.ma.analysis-spec/v1", "name": name, "outcome": "o1", "purpose": "sensitivity",
            "parent": "o1_primary", "data": {"path": data}, "options": {"measure": "RR"}}
    sp = os.path.join(root, "05_elemzes", "specs", name + ".json")
    S.save_spec(sp, spec)
    with contextlib.redirect_stdout(io.StringIO()):
        res = engine_api.analyze(sp, mode="commit", project_root=root,
                                 outdir=os.path.join(root, *outdir.split("/")))
    return res["run"]


def _zip_texts(data):
    zf = zipfile.ZipFile(io.BytesIO(data))
    return {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_rev_"))
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snap(self, root, **kw):
        app = TS.open_app(root, self.home)
        try:
            return snapshot.build(app=app, home=self.home, **kw)
        finally:
            app.close()

    def audit(self, root, **kw):
        app = TS.open_app(root, self.home)
        try:
            return audit_export.build(app=app, **kw)
        finally:
            app.close()


# ---------------------------------------------------------------------------------------------- WS-1 / PRIV-1
class PrivateRunsNeverExported(_Tmp):
    def test_ws1_class_a_private_commit_run_not_in_snapshot(self):
        """WS-1: a _privat/ adatú, _privat/05_elemzes alá rögzített (CLI-) futás címkéi, becslései és cellái nem
        kerülnek a pillanatképbe (a manifeszt '_privat/** — soha' ígérete); a nem privát futás marad."""
        root = TS.make_project(self.tmp, "A")
        _write_private_copy(root)
        prun = _cli_private_commit(root)
        html, info = self.snap(root)
        text = html.decode("utf-8")
        self.assertNotIn(SECRET, text)
        self.assertNotIn(prun["run_id"], text)
        self.assertNotIn("_privat/05_elemzes", text)
        d = TS.embedded(html)
        runs = d["routes"]["GET /api/runs?outcome=o1"]["data"]["runs"]
        self.assertTrue(runs, "a nem privát commit-futás megmarad")
        self.assertFalse([r for r in runs if r["run_id"] == prun["run_id"]])
        self.assertFalse([k for k in d["routes"] if prun["run_id"] in k])
        prim = d["routes"]["GET /api/runs?primary=1"]["data"]["runs"]
        self.assertEqual([r["outcome_id"] for r in prim], ["o1"], "a legutóbbi NEM privát elsődleges futás")
        self.assertNotIn(prun["run_id"], [r["run_id"] for r in d["manifest"]["runs"]])
        # a projektnapló futás-sorai és az állapot-összegzés sem hivatkozik rá
        for key in ("GET /api/log/run", "GET /api/log/status"):
            self.assertNotIn("_privat", json.dumps(d["routes"][key], ensure_ascii=False), key)

    def test_ws1_cli_snapshot_same(self):
        """WS-1: a `ma.py gui snapshot` parancssori út ugyanígy szűr (ugyanaz a rögzítő)."""
        root = TS.make_project(self.tmp, "A")
        _write_private_copy(root)
        _cli_private_commit(root)
        target = os.path.join(self.tmp, "cli.snapshot.html")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            rc = snapshot.main(["--project", root, "--out", target], out=out)
        self.assertEqual(rc, 0, out.getvalue())
        with open(target, "rb") as fh:
            self.assertNotIn(SECRET.encode(), fh.read())

    def test_priv1_class_c_private_data_run(self):
        """PRIV-1: C osztályú projekt, az adattábla a _privat/ alatt, a commit (CLI) a _privat/05_elemzes alá →
        a pillanatképben se címke, se becslés, se a privát futás útja/ujjlenyomata; az audit-manifeszt runs[]
        listájában sincs."""
        root = TS.make_project(self.tmp, "C")
        _write_private_copy(root, "_privat/o1.csv")
        meta_p = os.path.join(root, "ma-projekt.json")
        with open(meta_p, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta["outcomes"][0]["data"] = "_privat/o1.csv"
        with open(meta_p, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False)
        prun = _cli_private_commit(root, name="o1_c", data="_privat/o1.csv", outdir="_privat/05_elemzes/o1")
        html, info = self.snap(root)
        text = html.decode("utf-8")
        self.assertNotIn(SECRET, text)
        self.assertNotIn("_privat/05_elemzes", text)
        self.assertNotIn(prun["run_id"], text)
        self.assertNotIn(prun["data"]["sha256"], text, "a privát adatfájl ujjlenyomata sem")
        self.assertTrue(any(x["pattern"] == "_privat/**" for x in info["excluded"]))
        data, ainfo = self.audit(root)
        self.assertNotIn(prun["run_id"], [r["run_id"] for r in ainfo["manifest"]["runs"]])
        for name, body in _zip_texts(data).items():
            self.assertNotIn(SECRET, body, name)
        self.assertTrue(any(x["pattern"] == "_privat/05_elemzes/**" for x in ainfo["excluded"]))

    def test_priv1_live_runs_listing_and_private_filter(self):
        """A helyi felület továbbra is látja a saját privát futását; a ?private=0 (a pillanatkép kérése) nem."""
        root = TS.make_project(self.tmp, "A")
        _write_private_copy(root)
        prun = _cli_private_commit(root)
        srv = H.Srv(root, self.home, self.tmp)
        try:
            ids = [r["run_id"] for r in srv.ok("GET", "/api/runs?outcome=o1")["data"]["runs"]]
            self.assertIn(prun["run_id"], ids)
            ids0 = [r["run_id"] for r in srv.ok("GET", "/api/runs?outcome=o1&private=0")["data"]["runs"]]
            self.assertNotIn(prun["run_id"], ids0)
            self.assertEqual(len(ids0), len(ids) - 1)
            env = srv.ok("POST", "/api/export/snapshot", {"ack": True})
            with open(os.path.join(root, *env["data"]["path"].split("/")), "rb") as fh:
                self.assertNotIn(SECRET.encode(), fh.read())
        finally:
            srv.stop()


# ---------------------------------------------------------------------------------------------- WS-6
class SymlinkIntoPrivate(_Tmp):
    def test_ws6_symlinked_table_into_private_never_exported(self):
        """WS-6: a 03_adatok/o4.csv → ../_privat/titkos.csv link sem az audit-ZIP-be, sem a pillanatképbe nem
        viszi a privát cellákat (a fájl VALÓDI helye számít), és a manifeszt jelzi a kizárást."""
        root = TS.make_project(self.tmp, "A")
        _write_private_copy(root)
        link = os.path.join(root, "03_adatok", "o4.csv")
        try:
            os.symlink(os.path.join("..", "_privat", "titkos.csv"), link)
        except (OSError, NotImplementedError):
            self.skipTest("a platform nem tud szimbolikus linket létrehozni")
        self.assertTrue(privacy.is_private_location(root, "03_adatok/o4.csv"))
        self.assertFalse(privacy.is_private_location(root, "03_adatok/o1.csv"))
        data, ainfo = self.audit(root)
        texts = _zip_texts(data)
        self.assertNotIn("03_adatok/o4.csv", texts)
        for name, body in texts.items():
            self.assertNotIn(SECRET, body, name)
        self.assertTrue(any(x["pattern"] == "03_adatok/o4.csv" for x in ainfo["excluded"]))
        html, _info = self.snap(root)
        self.assertNotIn(SECRET.encode(), html)
        self.assertNotIn("03_adatok%2Fo4.csv", html.decode("utf-8"))


# ---------------------------------------------------------------------------------------------- WS-7
class AppraisalBadName(_Tmp):
    def test_ws7_bad_appraisal_name_skipped_not_fatal(self):
        """WS-7: egy ':'-ot tartalmazó értékelés-fájlnév (Linuxon/macOS-en érvényes) csak azt a fájlt hagyja ki,
        az audit-export (szerver és CLI) elkészül, a kizárás a manifesztben."""
        root = TS.make_project(self.tmp, "A")
        d = os.path.join(root, "04_torzitas_kockazat", "appraisals")
        os.makedirs(d, exist_ok=True)
        good = "Smith 2019.rob2.SzK.json"
        bad = "Smith 2019: pilot.rob2.SzK.json"
        for n in (good, bad):
            with open(os.path.join(d, n), "w", encoding="utf-8") as fh:
                json.dump({"tool": "rob2", "study": "Smith 2019"}, fh)
        data, info = self.audit(root)
        names = _zip_texts(data)
        self.assertIn("04_torzitas_kockazat/appraisals/" + good, names)
        self.assertNotIn("04_torzitas_kockazat/appraisals/" + bad, names)
        self.assertTrue(any(x["pattern"] == "04_torzitas_kockazat/appraisals/" + bad for x in info["excluded"]))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            rc = audit_export.main(["--project", root, "--out", os.path.join(self.tmp, "a.zip")], out=out)
        self.assertEqual(rc, 0, out.getvalue())


# ---------------------------------------------------------------------------------------------- WS-8
class CliWriteRules(_Tmp):
    def test_ws8_class_c_cli_exports_go_to_private_export(self):
        """WS-8: C osztályú projektben a `gui snapshot` és a `gui audit-export` --out nélkül ugyanoda ír, ahová a
        szerver (_privat/export/), nem a 07_ellenorzes/ alá; figyelmeztet az okkal."""
        root = TS.make_project(self.tmp, "C")
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            out = io.StringIO()
            self.assertEqual(snapshot.main(["--project", root, "--json"], out=out), 0, out.getvalue())
            info = json.loads(out.getvalue())
            out2 = io.StringIO()
            self.assertEqual(audit_export.main(["--project", root, "--json"], out=out2), 0, out2.getvalue())
            ainfo = json.loads(out2.getvalue())
        for i in (info, ainfo):
            rel = os.path.relpath(i["path"], root).replace(os.sep, "/")
            self.assertTrue(rel.startswith("_privat/export/"), rel)
            self.assertTrue(os.path.isfile(i["path"]))
            self.assertTrue(i["warnings"] and "_privat/export/" in i["warnings"][0])
        self.assertFalse(os.path.exists(os.path.join(root, "07_ellenorzes", "pillanatkep")))
        self.assertFalse(os.path.exists(os.path.join(root, "07_ellenorzes", "audit")))
        # a projekten belüli --out is ugyanazzal a szabállyal (C osztályban csak a _privat/ alá)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            rc = snapshot.main(["--project", root, "--out", os.path.join(root, "07_ellenorzes", "x.snapshot.html")],
                               out=out)
        self.assertEqual(rc, 2, out.getvalue())
        self.assertIn("nem írható", out.getvalue())

    def test_ws8_class_b_vault_tracked_same_decision_as_server(self):
        """WS-8: vault által követett B osztályú projekt .gitignore-blokk nélkül: a CLI ugyanazt dönti, mint a
        szerver export-útja (snapshot.export_target) — itt egyik hely sem írható, ezért 2-es kilépés az okkal, és
        a projektbe semmi nem kerül."""
        proj, home = H.make_project(self.tmp, "vb", data_class="B", vault=True)
        app = TS.open_app(proj, home)
        try:
            with self.assertRaises(snapshot.SnapshotError):
                snapshot.export_target(app, snapshot.OUT_DIR_REL + "/x" + snapshot.SNAPSHOT_SUFFIX)
        finally:
            app.close()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": home}):
            rc = snapshot.main(["--project", proj], out=out)
        self.assertEqual(rc, 2, out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(proj, "07_ellenorzes", "pillanatkep")))
        self.assertFalse(os.path.exists(os.path.join(proj, "_privat", "export")))
        # a .gitignore-blokk után már a szokásos helyre ír
        privacy.apply_gitignore(proj)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": home}):
            rc = snapshot.main(["--project", proj, "--json"], out=out)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("07_ellenorzes/pillanatkep/", json.loads(out.getvalue())["path"].replace(os.sep, "/"))


# ---------------------------------------------------------------------------------------------- PRIV-2
class ProvenanceDeepStrip(_Tmp):
    def test_priv2_converter_shaped_provenance_values_removed_without_tables(self):
        """PRIV-2: adattábla nélkül (B osztály) az eredet-adat átváltó-bemenetei/kimenetei, az egyeztetés A/B
        értéke és a korábbi bejegyzések értékei sem kerülnek a pillanatképbe vagy az audit-ZIP-be."""
        root = TS.make_project(self.tmp, "B")
        uids = TS.store.ProjectStore(root).load_table("03_adatok/o1.csv").row_uids
        p = os.path.join(root, "03_adatok", "o1.prov.json")
        with open(p, encoding="utf-8") as fh:
            prov = json.load(fh)
        prov["cells"].append({
            "row_uid": uids[0], "field": "n1", "value_as_entered": "777001", "method": "estimated", "estimated": True,
            "source": {"doc": "file:" + TS.PDF_NAME, "page": 4},
            "conversion": {"kind": "median_to_mean_sd",
                           "request": {"schema": "szk.ma.convert-request/v1", "kind": "median_to_mean_sd",
                                       "inputs": {"median": "777002", "q1": "777003", "q3": "777004", "n": "777005"}},
                           "outputs": {"mean": 777006.5}, "outputs_text": {"mean": {"hu": "777006,5", "en": "777006.5"}},
                           "cell_text": "777006,5", "engine_version": "0.2.0"},
            "reconciliation": {"key": "n1", "a": "777007", "b": "777008", "chosen": "a", "reason": "x"},
            "history": [{"value_as_entered": "777009", "method": "reported", "source": None}]})
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(prov, fh, ensure_ascii=False)
        markers = ["7770%02d" % i for i in range(1, 10)]
        html, _ = self.snap(root)
        data, _ = self.audit(root)
        pj = _zip_texts(data)["03_adatok/o1.prov.json"]
        for m in markers:
            self.assertNotIn(m.encode(), html, m)
            self.assertNotIn(m, pj, m)
        cell = json.loads(pj)["cells"][-1]
        self.assertEqual(cell["conversion"]["kind"], "median_to_mean_sd", "a módszer és az átváltás fajtája marad")
        self.assertEqual(cell["source"]["page"], 4)
        # adattáblával (A osztály, kérésre) az értékek maradnak
        html_a, _ = self.snap(root, include={"data_tables": True})
        self.assertIn(b"777002", html_a)


# ---------------------------------------------------------------------------------------------- PRIV-3
class ValidationDetailsWithoutTables(_Tmp):
    def _project(self, data_class):
        root = TS.make_project(self.tmp, data_class, "p" + data_class)
        p = os.path.join(root, "03_adatok", "o1.csv")
        with open(p, encoding="utf-8") as fh:
            csv = fh.read()
        self.assertIn("Aronson 1948;4;123;", csv)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(csv.replace("Aronson 1948;4;123;", "Aronson 1948;98765;123;"))
        sp = os.path.join(root, "05_elemzes", "specs", "o1_primary.json")
        with contextlib.redirect_stdout(io.StringIO()):
            engine_api.analyze(sp, mode="commit", project_root=root)
        return root

    def test_priv3_class_b_audit_has_no_validation_cell_values(self):
        """PRIV-3: B osztály, adattábla nélkül: a futás results.json-jában és report.md-jében a V006 részlete
        („e1 = 98765, n1 = 123”) nem kerül az audit-ZIP-be; a kód, a súlyosság és a vizsgálat marad."""
        root = self._project("B")
        data, info = self.audit(root)
        texts = _zip_texts(data)
        for name, body in texts.items():
            self.assertNotIn("98765", body, name)
        res = [n for n in texts if n.endswith("/results.json") and "98765" not in n]
        rep = [n for n in texts if n.endswith("/report.md")]
        self.assertTrue(res and rep)
        newest = sorted(res)[-1]
        f = [x for x in json.loads(texts[newest])["validation"]["findings"] if x["code"] == "V006"]
        self.assertTrue(f and f[0]["detail"] is None and f[0]["study"] == "Aronson 1948")
        self.assertIn("(kitakarva)", texts[sorted(rep)[-1]])
        self.assertIn("V006", texts[sorted(rep)[-1]])

    def test_priv3_class_a_keeps_details(self):
        root = self._project("A")
        data, _info = self.audit(root)
        self.assertTrue(any("98765" in b for n, b in _zip_texts(data).items() if n.endswith("/results.json")))


# ---------------------------------------------------------------------------------------------- PRIV-4
class DecisionContextPhi(unittest.TestCase):
    def test_priv4_taj_in_decision_context_refused_when_held(self):
        """PRIV-4: írás-tartásos (vault által követett, .gitignore nélküli B) projektben a döntés gépi
        kontextusában (changes[].after, fields) álló TAJ-gyanús szám is 403 — nem kerül a projekt.sqlite-ba és a
        hash-láncolt activity-naplóba."""
        tmp = H.tmpdir("ma_rev_p4_")
        try:
            proj, home = H.make_project(tmp, "p4", data_class="B", vault=True)
            secret = H.taj()
            srv = H.Srv(proj, home, tmp)
            try:
                self.assertTrue(srv.app.text_hold())
                st, e = srv.err("POST", "/api/log/decision", {"decision": "ok", "rationale": "TAJ " + secret})
                self.assertEqual(st, 403)
                body = {"decision": "Protokoll-eltérés", "rationale": "ok",
                        "context": {"kind": "analysis", "changes": [{"key": "title", "before": None,
                                                                     "after": "Beteg TAJ " + secret}],
                                    "fields": ["TAJ " + secret]}}
                st, e = srv.err("POST", "/api/log/decision", body)
                self.assertEqual(st, 403, e)
                self.assertNotIn(secret, json.dumps(e, ensure_ascii=False))
                self.assertIn("context", e["message"])
                # tiszta kontextus átmegy
                body["context"] = {"kind": "analysis", "changes": [{"key": "title", "before": None, "after": "BCG"}]}
                srv.ok("POST", "/api/log/decision", body)
            finally:
                srv.stop()
            for rel in ("07_ellenorzes/activity.jsonl",):
                with open(os.path.join(proj, *rel.split("/")), "rb") as fh:
                    self.assertNotIn(secret.encode(), fh.read())
            con = sqlite3.connect(os.path.join(proj, "projekt.sqlite"))
            try:
                dump = "\n".join(con.iterdump())
            finally:
                con.close()
            self.assertNotIn(secret, dump)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------------------------- WS-3 / WS-4
class ErrorEnvelopesCarryNoInternals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_rev_err_")
        cls.proj, cls.home = H.make_project(cls.tmp, "e")
        cls.srv = H.Srv(cls.proj, cls.home, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_ws3_job_error_has_no_traceback(self):
        """WS-3: a hibára futott feladat (POST /api/analyze és GET /api/jobs/<id>) csak kódot és magyar üzenetet
        ad — traceback, abszolút telepítési út és forrássor nélkül."""
        for extra in ({"table": {"header": [], "rows": []}},
                      {"spec_options": {"moderators": ["nincs"]}}):
            sp = H.spec()
            if "spec_options" in extra:
                sp["options"].update(extra["spec_options"])
            body = {"mode": "explore", "spec": sp, "lane": "ws3"}
            if "table" in extra:
                body["table"] = extra["table"]
            st, _h, env = self.srv.call("POST", "/api/analyze", body)
            if st != 200:
                text = json.dumps(env, ensure_ascii=False)
            else:
                job = self.srv.job(env["data"])
                self.assertEqual(job["status"], "error", job)
                self.assertEqual(sorted(job["error"]), ["code", "message"])
                text = json.dumps(job, ensure_ascii=False) + json.dumps(env, ensure_ascii=False)
            for bad in ("traceback_tail", "Traceback", ROOT, 'File "', ".py\", line"):
                self.assertNotIn(bad, text, (extra, bad))

    def test_ws4_convert_error_does_not_echo_input(self):
        """WS-4: az átváltó hibaüzenete a mezőt nevezi meg, a bevitt szöveget (pl. TAJ-szám és név) soha."""
        bad = "TAJ 123456788 Kovács"
        st, e = self.srv.err("POST", "/api/convert", {"schema": "szk.ma.convert-request/v1", "kind": "se_to_sd",
                                                      "inputs": {"se": bad, "n": "40"}})
        self.assertEqual(st, 422)
        self.assertIn("se", e["message"])
        self.assertNotIn("123456788", json.dumps(e, ensure_ascii=False))
        self.assertNotIn("Kovács", json.dumps(e, ensure_ascii=False))
        self.assertNotIn("123456788", self.srv.log.getvalue())

    def test_ws4_engine_messages_name_field_only(self):
        for inputs in ({"se": "TAJ 123456788 Kovács", "n": "40"}, {"se": True, "n": "40"}):
            with self.assertRaises(ValueError) as cm:
                engine_api.convert({"schema": "szk.ma.convert-request/v1", "kind": "se_to_sd", "inputs": inputs})
            self.assertNotIn("123456788", str(cm.exception))
            self.assertNotIn("True", str(cm.exception))
        from metaelemzes import tableio
        with self.assertRaises(ValueError) as cm:
            tableio.parse_number("TAJ 123456788")
        self.assertNotIn("123456788", str(cm.exception))


# ---------------------------------------------------------------------------------------------- WS-5
class _FakeJobs(object):
    """A feladatkezelő helyettese: a commit sokáig 'queued' (lassú explore-ok mögött), aztán 'done'."""

    def __init__(self, queued_polls):
        self.polls = queued_polls
        self.calls = []

    def wait(self, job_id, timeout=None):
        self.calls.append(timeout)
        if self.polls > 0:
            self.polls -= 1
            return {"job_id": job_id, "status": "queued"}
        return {"job_id": job_id, "status": "done"}

    def info(self):
        return {"closed": False}


class _FakeWatcher(object):
    def __init__(self):
        self.released = threading.Event()
        self.scans = 0

    def scan(self):
        self.scans += 1

    def release_prefix(self, token, grace=None):
        self.released.set()


class CommitFollowWaitsForTerminal(unittest.TestCase):
    def test_ws5_prefix_released_only_after_job_ends(self):
        """WS-5: a 05_elemzes/ saját-írás jelölése csak a commit TÉNYLEGES végén jár le — a sorban várakozás
        (lassú explore-ok mögött) bármeddig tarthat, nincs határidő a beküldéstől számítva."""
        jobs = _FakeJobs(queued_polls=6)
        watcher = _FakeWatcher()
        app = mock.Mock()
        app.get_jobs.return_value = jobs
        app.watcher = watcher
        with mock.patch.object(analyze_route, "FOLLOW_POLL", 0.01):
            analyze_route._follow(app, "j_0000000000000000", "tok")
            self.assertTrue(watcher.released.wait(5.0))
        self.assertEqual(len(jobs.calls), 7, "a 6 'queued' válasz után a 'done'-ig vár")
        self.assertTrue(all(t is not None and t <= 0.01 for t in jobs.calls))
        self.assertEqual(watcher.scans, 1)

    def test_ws5_no_overall_deadline_constant(self):
        self.assertFalse(hasattr(analyze_route, "FOLLOW_GRACE"), "a beküldéstől számított határidő megszűnt")


# ---------------------------------------------------------------------------------------------- FID-2 / FID-3 / FID-6
class RunFactsFromRunArtifacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = H.tmpdir("ma_rev_fid_")
        cls.proj, cls.home = H.make_project(cls.tmp, "f")
        cls.srv = H.Srv(cls.proj, cls.home, cls.tmp)
        s = cls.srv
        sp = H.spec()
        env = s.ok("PUT", "/api/specs/o1_primary", sp)
        cls.rr = s.job(s.ok("POST", "/api/analyze", {"mode": "commit", "spec": sp})["data"])["result"]["run"]
        sp2 = H.spec()
        sp2["options"]["measure"] = "OR"
        s.ok("PUT", "/api/specs/o1_primary", sp2, headers=[("If-Match", env["_etag"])])
        cls.orr = s.job(s.ok("POST", "/api/analyze", {"mode": "commit", "spec": sp2})["data"])["result"]["run"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _plot(self, run):
        with open(os.path.join(self.proj, run["dir"], "plot_data.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_fid2_each_run_keeps_its_own_measure(self):
        """FID-2: a spec RR → OR módosítása után a korábbi futás továbbra is RR (a run.json / plot_data.json
        szerint), nem a most mentett spec hatásmérete."""
        runs = {r["run_id"]: r for r in self.srv.ok("GET", "/api/runs?outcome=o1")["data"]["runs"]}
        self.assertEqual(runs[self.rr["run_id"]]["measure"], "RR")
        self.assertEqual(runs[self.orr["run_id"]]["measure"], "OR")
        for r in runs.values():
            self.assertEqual(r["measure"], self._plot(r)["measure"])
        one = self.srv.ok("GET", "/api/runs/%s" % self.rr["run_id"])["data"]
        self.assertEqual(one["measure"], "RR")
        prim = self.srv.ok("GET", "/api/runs?primary=1")["data"]["runs"]
        self.assertEqual([(r["run_id"], r["measure"]) for r in prim], [(self.orr["run_id"], "OR")])

    def test_fid2_old_run_json_without_measure_falls_back_to_plot(self):
        """Régi run.json (measure mező nélkül): a futás plot_data.json-ja dönt, nem a spec."""
        runs = {r["run_id"]: r for r in self.srv.ok("GET", "/api/runs?outcome=o1")["data"]["runs"]}
        rp = os.path.join(self.proj, runs[self.rr["run_id"]]["dir"], "run.json")
        with open(rp, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc.pop("measure", None)
        doc["spec"].pop("purpose", None)
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        r = self.srv.ok("GET", "/api/runs/%s" % self.rr["run_id"])["data"]
        self.assertEqual(r["measure"], "RR")

    def test_fid3_every_listed_run_has_engine_i2_text(self):
        """FID-3 / UX-14: a terv futás-táblája (GET /api/runs?outcome=) is kapja a motor I²- és PI-szövegét."""
        for r in self.srv.ok("GET", "/api/runs?outcome=o1")["data"]["runs"]:
            plot = self._plot(r)
            self.assertEqual(r["primary"]["i2_text"], plot["heterogeneity"]["i2_text"])
            pi = [x for x in plot["summaries"] if x.get("primary")][0].get("pi_text")
            self.assertEqual(r["primary"].get("pi_text"), pi)
            H.check_contract(self, r, "szk.ma.run/v1")


class StaleMatchesEngineAudit(unittest.TestCase):
    def test_fid6_absolute_outside_data_path(self):
        """FID-6: a projekten kívüli (abszolút data.path-ú) CLI-futás frissessége a motor X001-ével egyezik:
        változatlan fájl → nem elavult; törölt fájl → elavult (mint az X001); olvashatatlan → ismeretlen (null)."""
        tmp = H.tmpdir("ma_rev_f6_")
        try:
            proj, home = H.make_project(tmp, "f6")
            outside = os.path.join(tmp, "kint", "bcg.csv")
            os.makedirs(os.path.dirname(outside))
            shutil.copy(H.BCG, outside)
            rid = "20261005T070000Z-abcdef"
            out = os.path.join(proj, "05_elemzes", "o1", rid)
            from metaelemzes import cli
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = cli.main(["analyze", "--data", outside, "--measure", "RR", "--out", out, "--project", proj,
                               "--run-id", rid])
            self.assertIn(rc, (0, None))
            with open(os.path.join(out, "run.json"), encoding="utf-8") as fh:
                self.assertTrue(os.path.isabs(json.load(fh)["data"]["path"]))
            audit = engine_api.project_audit(proj)
            self.assertFalse([f for f in audit["findings"] if f["code"] == "X001"])
            srv = H.Srv(proj, home, tmp)
            try:
                r = [x for x in srv.ok("GET", "/api/runs?outcome=o1")["data"]["runs"] if x["run_id"] == rid][0]
                self.assertIs(r["stale"], False)
                self.assertIsNotNone(r["data_current_sha256"])
                os.remove(outside)
                r = [x for x in srv.ok("GET", "/api/runs?outcome=o1")["data"]["runs"] if x["run_id"] == rid][0]
                self.assertIs(r["stale"], True)
                self.assertTrue([f for f in engine_api.project_audit(proj)["findings"] if f["code"] == "X001"])
                with mock.patch("ma_gui.store.sha256_file", side_effect=PermissionError("zárolt")):
                    shutil.copy(H.BCG, outside)
                    r = [x for x in srv.ok("GET", "/api/runs?outcome=o1")["data"]["runs"] if x["run_id"] == rid][0]
                self.assertIsNone(r["stale"], "olvashatatlan adatfájl: ismeretlen, nem elavult")
            finally:
                srv.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)



# ---------------------------------------------------------------------------------------------- DOC-1 / DOC-3
def _ma(*args, cwd=None):
    import subprocess
    return subprocess.run([sys.executable, os.path.join(ROOT, "ma.py")] + list(args), cwd=cwd or ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)


class OutcomeFromInitOnlyProject(_Tmp):
    """DOC-1: a TELEPITES útja (csak `project init`) nem akad el — a kimenet parancssorból és a munkapadról is
    felvehető, a ma-projekt.json magától létrejön."""

    def _init(self):
        root = os.path.join(self.tmp, "P")
        r = _ma("project", "init", root, "--title", "T", "--question", "Q")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(root, "ma-projekt.json")), "init nem ír ma-projekt.json-t")
        os.makedirs(os.path.join(root, "03_adatok"), exist_ok=True)
        shutil.copy(H.BCG, os.path.join(root, "03_adatok", "bcg.csv"))
        return root

    def test_doc1_cli_project_outcome(self):
        root = self._init()
        r = _ma("project", "outcome", root, "--id", "o1", "--name", "TBC-incidencia", "--data", "03_adatok/bcg.csv",
                "--measure", "rr")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("kimenet felvéve: o1", r.stdout)
        with open(os.path.join(root, "ma-projekt.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        self.assertEqual(meta["title"], "T")
        o = meta["outcomes"][0]
        self.assertEqual((o["id"], o["data"], o["measure"], o["name"]["hu"]), ("o1", "03_adatok/bcg.csv", "RR", "TBC-incidencia"))
        self.assertEqual(o["primary_spec"], "05_elemzes/specs/o1_primary.json")
        dup = _ma("project", "outcome", root, "--id", "o1", "--measure", "OR")
        self.assertNotEqual(dup.returncode, 0)
        self.assertIn("--replace", dup.stderr + dup.stdout)
        bad = _ma("project", "outcome", root, "--id", "o2", "--measure", "XYZ")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("ismeretlen hatásméret", bad.stderr + bad.stdout)
        rep = _ma("project", "outcome", root, "--id", "o1", "--measure", "OR", "--replace")
        self.assertEqual(rep.returncode, 0, rep.stderr)
        with open(os.path.join(root, "ma-projekt.json"), encoding="utf-8") as fh:
            o = json.load(fh)["outcomes"][0]
        self.assertEqual((o["measure"], o["data"]), ("OR", "03_adatok/bcg.csv"), "a csere a többi mezőt megtartja")
        self.assertRegex(_ma("project", "--help").stdout, r"outcome\s+kimenet felvétele")

    def test_doc1_workbench_outcome_action(self):
        root = self._init()
        from ma_gui import activity
        srv = H.Srv(root, self.home, self.tmp)
        try:
            self.assertEqual(srv.ok("GET", "/api/project")["data"].get("outcomes") or [], [])
            env = srv.ok("POST", "/api/project", {"action": "outcome", "outcome": {
                "id": "o1", "name": "TBC-incidencia", "data": "03_adatok/bcg.csv", "measure": "RR"}})
            outs = env["data"]["outcomes"]
            self.assertEqual([(o["id"], o["data"], o["measure"]) for o in outs], [("o1", "03_adatok/bcg.csv", "RR")])
            st, _h, err = srv.call("POST", "/api/project", {"action": "outcome", "outcome": {"id": "o1", "measure": "RR"}})
            self.assertEqual((st, err["error"]["code"]), (422, "VALIDATION"))
            st, _h, err = srv.call("POST", "/api/project", {"action": "outcome", "outcome": {"id": "o 2"}})
            self.assertEqual(st, 422)
            recs = [r for r in activity.read_records(os.path.join(root, activity.LOG_RELPATH))
                    if r["action"] == "project.outcome"]
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["details"].get("id"), "o1")
            self.assertTrue(recs[0]["details"].get("created"))
            # a felvett kimenet a futás-listában is kérdezhető (az Eredmények képernyő ugyanezt hívja)
            self.assertEqual(srv.ok("GET", "/api/runs?outcome=o1")["data"]["runs"], [])
        finally:
            srv.stop()


class NoInternalLaunchCommand(unittest.TestCase):
    def test_doc3_no_user_facing_python_m_ma_gui(self):
        """DOC-3: a felhasználónak szóló szövegek a dokumentált `python ma.py gui --project <mappa>` parancsot adják,
        sehol sem a csak a kódmappában működő `python -m ma_gui`-t."""
        from ma_gui import runtime
        from ma_gui.routes import project as project_route, session as session_route
        self.assertIn(runtime.LAUNCH_CMD, session_route.MSG_CODE)
        self.assertEqual(runtime.LAUNCH_CMD, "python ma.py gui --project <mappa>")
        hits = []
        for base in (os.path.join(ROOT, "ma_gui"),):
            for dp, _dn, fns in os.walk(base):
                if "__pycache__" in dp or os.sep + "dist" in dp:
                    continue
                for fn in fns:
                    if not fn.endswith((".py", ".json", ".js", ".html")):
                        continue
                    with open(os.path.join(dp, fn), encoding="utf-8", errors="replace") as fh:
                        for i, line in enumerate(fh, 1):
                            s = line.strip()
                            # a fejlesztői docstringek/megjegyzések (a kódmappában valóban működik) kivételek
                            if re.search(r"-m ma_gui(?![.\w])", s) and not s.startswith(("#", "*", "/*", "//", '"""')):
                                hits.append("%s:%d: %s" % (os.path.relpath(os.path.join(dp, fn), ROOT), i, s[:120]))
        for doc in ("README.md", "TELEPITES.md"):
            with open(os.path.join(ROOT, doc), encoding="utf-8") as fh:
                if "python -m ma_gui" in fh.read():
                    hits.append(doc)
        self.assertEqual(hits, [], "belső indító parancs a felhasználói szövegben")
        import pathlib
        import types
        exc = project_route._other_project(types.SimpleNamespace(project_root=pathlib.Path("/x/P")))
        self.assertIn(runtime.LAUNCH_CMD, exc.message)
        self.assertNotIn("-m ma_gui", exc.message)


# ---------------------------------------------------------------------------------------------- DOC-1, DOC-5..8
def _read(rel):
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _section(md, title_prefix):
    m = re.search(r"^## %s.*$" % re.escape(title_prefix), md, re.M)
    end = re.search(r"^## ", md[m.end():], re.M)
    return md[m.end(): m.end() + end.start()] if end else md[m.end():]


class DocsMatchTheProduct(unittest.TestCase):
    def test_doc1_docs_explain_outcome_creation(self):
        """DOC-1: a TELEPITES 6–7. pontja, a README és a skill (forrás + plugin-másolat) leírja a kimenet felvételét."""
        tel = _read("TELEPITES.md")
        self.assertIn("project outcome", _section(tel, "6."))
        self.assertIn("Kimenet felvétele", _section(tel, "7."))
        self.assertIn("project outcome", _read("README.md"))
        for rel in ("../.claude/skills/metaanalizis/SKILL.md", "skills/metaanalizis/SKILL.md"):
            txt = _read(rel)
            sec = txt[txt.index("## MA-munkapad"):]
            self.assertIn("project outcome", sec, rel)
            self.assertIn("Kimenet felvétele", sec, rel)
        self.assertNotIn("ma-projekt.json → outcomes", _read("ma_gui/web/src/i18n/hu/analysis.json"))

    def test_doc2_launchers_exist_and_status_line(self):
        """DOC-2: a §9.2-ben vállalt .cmd/.command indító megvan, a státuszsor nem állít mást."""
        for fn in ("ma-munkapad.cmd", "ma-munkapad.command"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, fn)), fn)
        status = _read("TERV_validalo_grafikus_felulet.md").split("> **Állapot (2026-10-05):**", 1)[1].split("\n\n")[0]
        self.assertIn("ma-munkapad.cmd", status)
        self.assertIn("Hátravan", status)

    def test_doc5_selftest_scope_is_stated(self):
        """DOC-5: a README nem állítja, hogy a selftest „mindent” lefuttat, és megnevezi a munkapad tesztjeit."""
        readme, tel = _read("README.md"), _read("TELEPITES.md")
        self.assertNotIn("selftest` lefuttat mindent", readme)
        for txt in (readme, tel):
            self.assertIn("tests/run_parallel.py --gui", txt)

    def test_doc6_dist_is_served_directly(self):
        """DOC-6: a web-README és a static.py a valóságot írja: a szerver a dist/index.html-t szolgálja ki."""
        web = _read("ma_gui/web/README.md")
        self.assertNotIn("az integrátor másolja", web)
        self.assertRegex(web, r"dist/index\.html +termék-build; a szerver közvetlenül ezt szolgálja ki")
        from ma_gui.routes import static as static_route
        self.assertIn("web/dist/index.html", static_route.__doc__)
        app = type("A", (), {"index_html": lambda self: None})()
        req = type("R", (), {"app": app})()
        with self.assertRaises(Exception) as cm:
            static_route.get_index(req)
        self.assertIn("ma_gui/web/dist/index.html", cm.exception.message)
        self.assertIn("build_gui.py", cm.exception.message)

    def test_doc7_adapters_are_future_tense(self):
        """DOC-7: a README nem állítja, hogy a figure-forge/validator adapter már csatlakozik (az MVP csak felderíti)."""
        readme = _read("README.md")
        sec = readme[readme.index("## Validáló és grafikus felület"):]
        sec = sec[:sec.index("\n## ", 5)]
        flat = " ".join(sec.split())
        self.assertNotIn("opcionális adapterként, verziózott `szk.*` JSON-szerződéssel csatlakozik", flat)
        self.assertIn("a v1-ben csatlakozik majd", flat)
        self.assertIn("csak felderíti", flat)

    def test_doc8_every_command_block_names_its_folder(self):
        """DOC-8: a TELEPITES 6–7. pontjának minden `python ma.py` blokkja előtt ott a munkamappa (vagy előtaggal fut)."""
        tel = _read("TELEPITES.md")
        for title in ("6.", "7."):
            sec = _section(tel, title)
            for m in re.finditer(r"```bash\n(.*?)```", sec, re.S):
                if "python ma.py" not in m.group(1):
                    continue
                before = sec[:m.start()]
                self.assertIn("`metaanalizis-asszisztens` mappában", before, "%s pont: %s" % (title, m.group(1)[:60]))


if __name__ == "__main__":
    unittest.main()
