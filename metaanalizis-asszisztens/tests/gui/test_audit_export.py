# -*- coding: utf-8 -*-
"""ma_gui.audit_export — a determinisztikus audit-csomag (terv 3.5.17, 4.17, 7.4, 7.7, 8.6 „audit-ZIP kétszer").

Mit bizonyít:
- determinizmus: ugyanaz a projektállapot ugyanazt a ZIP-bájtsort adja (ugyanabban és külön folyamatban);
  rendezett bejegyzések, 1980-01-01-es időbélyeg, ZIP_DEFLATED, rögzített attribútumok;
- a manifest.json a §4.17 szerinti sémának megfelel; a files[] sha256/bytes értékei a ZIP tartalmára igazak;
- a tevékenységnapló változatlan (a hash-lánc a kicsomagolt példányon is ép), a feje = activity_head;
- a rerun.cmd / rerun.sh a napló argv-jeiből készül;
- KB-pillanatkép: csak azonosítók + az adatbázis sha256-ja;
- adatosztály-mátrix: A — tábla benne; B — alapból ki; C — soha (kérésre sem); _privat/, PDF és a KB teljes
  szövege soha; az idézetek és az abszolút utak alapból ki."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from ma_gui import activity, audit_export, schema_lite, snapshot  # noqa: E402
from test_snapshot import (ASSESSOR, CELL_MARKER, has_cell_marker, PDF_MARKER, PRIVATE_MARKER, QUOTE_MARKER, make_project,  # noqa: E402
                           open_app)


def build(root, home, **kw):
    app = open_app(root, home)
    try:
        return audit_export.build(app=app, **kw)
    finally:
        app.close()


def zip_text(data):
    """A ZIP összes bejegyzésének szövege egyben (a kizárási keresésekhez)."""
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for n in zf.namelist():
            out.append(n)
            out.append(zf.read(n).decode("utf-8", "replace"))
    return "\n".join(out)


class AuditBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_audit_"))
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        cls.root = make_project(cls.tmp, "A")
        cls.data, cls.info = build(cls.root, cls.home)
        cls.zf = zipfile.ZipFile(io.BytesIO(cls.data))
        cls.manifest = json.loads(cls.zf.read("manifest.json").decode("utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.zf.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_deterministic_bytes(self):
        time.sleep(1.1)
        data2, info2 = build(self.root, self.home)
        self.assertEqual(self.data, data2)
        self.assertEqual(self.info["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(self.info["name"], info2["name"])

    def test_deterministic_separate_process(self):
        out = os.path.join(self.tmp, "kulso.zip")
        env = dict(os.environ, HOME=self.home)
        env.pop("SOURCE_DATE_EPOCH", None)
        r = subprocess.run([sys.executable, "-m", "ma_gui.audit_export", "--project", self.root, "--out", out],
                           cwd=ROOT, capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(out, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), self.info["sha256"])

    def test_zip_structure(self):
        infos = self.zf.infolist()
        names = [i.filename for i in infos]
        self.assertEqual(names, sorted(names), "rendezett bejegyzések")
        self.assertEqual(len(names), len(set(names)))
        for i in infos:
            self.assertEqual(i.date_time, (1980, 1, 1, 0, 0, 0), i.filename)
            self.assertEqual(i.compress_type, zipfile.ZIP_DEFLATED, i.filename)
            self.assertEqual(i.create_system, 3, i.filename)
            self.assertEqual(i.external_attr >> 16, 0o100644, i.filename)
            self.assertFalse(i.filename.startswith("/") or ".." in i.filename.split("/"), i.filename)
        self.assertIsNone(self.zf.testzip())
        for must in ("manifest.json", "rerun.cmd", "rerun.sh", "07_ellenorzes/activity.jsonl", "projekt/naplo.json",
                     "projekt/naplo.md", "ma-projekt.json", "05_elemzes/specs/o1_primary.json",
                     "audit/project_audit.json"):
            self.assertIn(must, names)

    def test_manifest_schema_and_file_hashes(self):
        m = self.manifest
        self.assertEqual(schema_lite.validate(m, audit_export.MANIFEST_SCHEMA), [])
        self.assertEqual(m["schema"], "szk.ma.audit-bundle/v1")
        self.assertEqual(m["data_class"], "A")
        listed = {f["path"]: f for f in m["files"]}
        names = set(self.zf.namelist()) - {"manifest.json"}
        self.assertEqual(set(listed), names, "minden bejegyzés (a manifeszten kívül) szerepel a files[]-ben")
        for path, f in listed.items():
            raw = self.zf.read(path)
            self.assertEqual(f["sha256"], hashlib.sha256(raw).hexdigest(), path)
            self.assertEqual(f["bytes"], len(raw), path)
        roles = {f["path"]: f["role"] for f in m["files"]}
        self.assertEqual(roles["07_ellenorzes/activity.jsonl"], "activity")
        self.assertEqual(roles["rerun.sh"], "rerun")
        self.assertEqual(roles["05_elemzes/specs/o1_primary.json"], "spec")
        self.assertIn("data", roles.values())
        self.assertEqual(m["rerun_scripts"], ["rerun.cmd", "rerun.sh"])
        self.assertEqual(len(m["runs"]), 1)
        self.assertEqual(m["runs"][0]["outcome_id"], "o1")
        self.assertEqual(m["versions"]["selftest"]["fail"], 0)
        self.assertEqual(self.info["manifest"], m)

    def test_activity_verbatim_and_chain_intact(self):
        with open(os.path.join(self.root, "07_ellenorzes", "activity.jsonl"), "rb") as fh:
            original = fh.read()
        self.assertEqual(self.zf.read("07_ellenorzes/activity.jsonl"), original)
        x = os.path.join(self.tmp, "kicsomagolt")
        self.zf.extractall(x)
        ok, bad, msg = activity.verify_chain(os.path.join(x, "07_ellenorzes", "activity.jsonl"))
        self.assertTrue(ok, msg)
        head = activity.ActivityLog(self.root).head()
        self.assertEqual(self.manifest["activity_head"], head["hash"])
        self.assertEqual(self.manifest["activity_seq"], head["seq"])
        self.assertEqual(self.manifest["activity_records"], head["seq"])

    def test_rerun_scripts_from_activity(self):
        sh = self.zf.read("rerun.sh").decode("utf-8")
        cmd = self.zf.read("rerun.cmd").decode("utf-8")
        self.assertTrue(sh.startswith("#!/bin/sh"))
        self.assertIn("analyze --spec 05_elemzes/specs/o1_primary.json", sh)
        self.assertIn("analyze --spec 05_elemzes/specs/o1_primary.json", cmd)
        self.assertIn("\r\n", cmd)

    def test_kb_snapshot_ids_and_db_hash_only(self):
        kb = self.manifest["kb_snapshot"]
        self.assertIn("D-S09-007", kb["ids"])
        from metaelemzes import kb as kbmod
        if os.path.isfile(kbmod.DEFAULT_DB):
            with open(kbmod.DEFAULT_DB, "rb") as fh:
                self.assertEqual(kb["db_sha256"], hashlib.sha256(fh.read()).hexdigest())
        names = self.zf.namelist()
        self.assertFalse([n for n in names if n.endswith(".sqlite") or "tudasbazis" in n])
        self.assertTrue(any(x["pattern"].startswith("tudasbazis") for x in self.manifest["excluded"]))

    def test_never_private_pdf_quotes_paths(self):
        text = zip_text(self.data)
        self.assertTrue(PRIVATE_MARKER not in text, "nem szerepelhet: %r" % (PRIVATE_MARKER,))
        self.assertNotIn("_privat/", "\n".join(self.zf.namelist()))
        self.assertTrue(PDF_MARKER.decode() not in text, "nem szerepelhet: %r" % (PDF_MARKER.decode(),))
        self.assertFalse([n for n in self.zf.namelist() if n.lower().endswith(".pdf")])
        self.assertTrue(QUOTE_MARKER not in text, "nem szerepelhet: %r" % (QUOTE_MARKER,))
        for n in self.zf.namelist():
            if n != "07_ellenorzes/activity.jsonl":      # a napló változatlan (hash-lánc) — lásd a notes-ot
                self.assertNotIn(self.tmp, self.zf.read(n).decode("utf-8", "replace"), n)
        pats = {x["pattern"] for x in self.manifest["excluded"]}
        self.assertTrue({"_privat/**", "**/*.pdf", "07_ellenorzes/audit/**"} <= pats)
        docs = json.loads(self.zf.read("03_adatok/documents.json").decode("utf-8"))
        self.assertEqual(sorted(docs["docs"][0]), ["id", "pages", "sha256"])

    def test_class_a_tables_included(self):
        self.assertIn("03_adatok/o1.csv", self.zf.namelist())
        self.assertIn(CELL_MARKER, self.zf.read("03_adatok/o1.csv").decode("utf-8"))


class AuditClassMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_audit_m_"))
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        cls.roots = {c: make_project(cls.tmp, c, "proj" + c) for c in ("B", "C")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _check_no_cells(self, data, manifest):
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertFalse([n for n in names if n.startswith("03_adatok/") and n.endswith(".csv")])
        text = zip_text(data)
        self.assertFalse(has_cell_marker(text), "nem szerepelhet: %r · %s" % (CELL_MARKER, "cellaérték: sem tábla, sem eredet-érték, sem ábra-oszlop"))
        self.assertTrue(PRIVATE_MARKER not in text, "nem szerepelhet: %r" % (PRIVATE_MARKER,))
        self.assertTrue(any(x["pattern"] == "03_adatok/**/*.csv" for x in manifest["excluded"]))
        self.assertEqual(schema_lite.validate(manifest, audit_export.MANIFEST_SCHEMA), [])
        zf = zipfile.ZipFile(io.BytesIO(data))
        plot = [n for n in names if n.endswith("plot_data.json")]
        self.assertTrue(plot)
        p = json.loads(zf.read(plot[0]).decode("utf-8"))
        self.assertTrue(all(not s.get("cells") for s in p["studies"]))

    def test_class_b_default_no_tables_and_initials(self):
        data, info = build(self.roots["B"], self.home)
        self._check_no_cells(data, info["manifest"])
        self.assertEqual(info["data_class"], "B")
        self.assertIn("assessors", [r["key"] for r in info["manifest"]["redactions"]])
        naplo = zipfile.ZipFile(io.BytesIO(data)).read("projekt/naplo.json").decode("utf-8")
        self.assertNotIn(ASSESSOR, naplo)

    def test_class_b_tables_on_request(self):
        data, info = build(self.roots["B"], self.home, include={"data_tables": True})
        self.assertIn("03_adatok/o1.csv", zipfile.ZipFile(io.BytesIO(data)).namelist())

    def test_class_c_never_tables(self):
        data, info = build(self.roots["C"], self.home)
        self._check_no_cells(data, info["manifest"])
        with self.assertRaises(snapshot.SnapshotError) as cm:
            build(self.roots["C"], self.home, include={"data_tables": True})
        self.assertEqual(cm.exception.code, "FORBIDDEN")

    def test_switching_off_groups(self):
        data, info = build(self.roots["B"], self.home, include={"activity": False, "rerun": False, "decisions": False})
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertNotIn("07_ellenorzes/activity.jsonl", names)
        self.assertNotIn("rerun.sh", names)
        self.assertNotIn("projekt/naplo.json", names)
        self.assertEqual(info["manifest"]["rerun_scripts"], [])

    def test_cli(self):
        out = io.StringIO()
        rc = audit_export.main(["--project", self.roots["B"], "--json"], out=out)
        self.assertEqual(rc, 0, out.getvalue())
        info = json.loads(out.getvalue())
        self.assertTrue(info["path"].endswith(audit_export.default_rel(info)))
        self.assertTrue(os.path.isfile(info["path"]))
        out = io.StringIO()
        self.assertEqual(audit_export.main(["--project", self.roots["C"], "--keep", "tables"], out=out), 2)
        self.assertIn("C osztály", out.getvalue())
        self.assertEqual(audit_export.main(["--project", os.path.join(self.tmp, "nincs")], out=io.StringIO()), 2)


class ZipUnitTests(unittest.TestCase):
    def test_zip_bytes_order_independent(self):
        a = audit_export.zip_bytes([("b.txt", b"2"), ("a.txt", b"1")])
        b = audit_export.zip_bytes([("a.txt", b"1"), ("b.txt", b"2")])
        self.assertEqual(a, b)
        with zipfile.ZipFile(io.BytesIO(a)) as zf:
            self.assertEqual(zf.namelist(), ["a.txt", "b.txt"])

    def test_appraisal_rel_initials(self):
        self.assertEqual(audit_export._appraisal_rel("x/smith2010.rob2.o1.Kovács Béla.json", True),
                         "x/smith2010.rob2.o1.KB.json")
        self.assertEqual(audit_export._appraisal_rel("x/smith2010.rob2.SzK.json", True), "x/smith2010.rob2.SzK.json")
        self.assertEqual(audit_export._appraisal_rel("x/a.b.Kovács Béla.json", False), "x/a.b.Kovács Béla.json")


if __name__ == "__main__":
    unittest.main()
