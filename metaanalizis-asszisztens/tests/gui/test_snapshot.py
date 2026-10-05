# -*- coding: utf-8 -*-
"""ma_gui.snapshot — a kitakaró, csak olvasható HTML-pillanatkép (terv 2.5, 3.5.17, 7.4, 7.6, 7.7, 8.5).

Mit bizonyít:
- determinizmus: ugyanaz a projektállapot ugyanazt a bájtsort adja (két építés ugyanabban és külön folyamatban);
- CSP: <meta http-equiv> sha256-hash-ek minden végrehajtható inline <script>/<style>-ra, connect-src 'none',
  nonce és külső erőforrás nincs; a hash-ek a kész tartalomra érvényesek;
- kitakarási mátrix adatosztályonként: A — tábla benne, B — tábla alapból ki, C — tábla soha (kérésre sem);
  a _privat/ soha; PDF soha (csak doc-id + sha256); a KB teljes szövege soha; idézetek és abszolút utak alapból
  ki; értékelők B/C-ben monogrammal; tábla nélkül a nyers cellaértékek az ábra-adatból és az eredetből is kimaradnak;
- a manifeszt rögzíti a kitakarásokat;
- az író gombok parancs-sablonjai a motor (és a ma_gui) argparse-ával értelmezhetők;
- a parancssori belépési pont (main) kilépési kódjai és kimenete.

A Playwright-os böngészőteszt (nulla hálózati kérés, nulla CSP-sértés): tests/gui/ui/snapshot.spec.js."""
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from metaelemzes import api as engine_api  # noqa: E402
from metaelemzes import cli as engine_cli  # noqa: E402
from metaelemzes import projekt  # noqa: E402
from metaelemzes import spec as S  # noqa: E402
from ma_gui import activity, server, snapshot, store  # noqa: E402

BCG = os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv")
CELL_MARKER = "54321"                    # egy n1-cella (Hart & Sutherland 1977) egyedi értéke
# a hiány-ellenőrzés hexa- és számkörnyezetet kizáró mintával, hogy egy sha256-ban vagy hosszabb számban
# véletlenül előforduló „54321” ne adjon hamis találatot
CELL_MARKER_RE = re.compile(r"(?<![0-9A-Za-z])%s(?![0-9A-Za-z])" % CELL_MARKER)


def has_cell_marker(text):
    return CELL_MARKER_RE.search(text) is not None
QUOTE_MARKER = "IDEZETMARKER-7f3a"
PRIVATE_MARKER = "PRIVATMARKER-c0ffee"
PDF_MARKER = b"PDFTARTALOMMARKER-91"
ASSESSOR = "Kovács Béla"
PDF_NAME = "forrasok/smith2010.pdf"


def make_project(base, data_class="A", name="proj"):
    """Kis, valószerű projekt: BCG (13 vizsgálat) egy commit-futással, eredet-adattal (idézettel), dokumentum-
    jegyzékkel (PDF-fel), _privat/ adattal, naplóbejegyzésekkel és egy névvel jelölt szereplőjű activity-sorral."""
    root = os.path.join(base, name)
    os.makedirs(root)
    projekt.init(root, "BCG-oltás próba")
    with open(BCG, "r", encoding="utf-8") as fh:
        csv = fh.read().replace(";13598;", ";%s;" % CELL_MARKER)
    with open(os.path.join(root, "03_adatok", "o1.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write(csv)
    meta = {"schema": "szk.ma.project/v1", "title": "BCG-oltás próba", "data_class": data_class,
            "review_type": "intervention",
            "outcomes": [{"id": "o1", "name": {"hu": "TBC", "en": "TB"}, "data": "03_adatok/o1.csv", "measure": "RR",
                          "critical": True, "primary_spec": "05_elemzes/specs/o1_primary.json"}]}
    with open(os.path.join(root, "ma-projekt.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    spec = {"schema": "szk.ma.analysis-spec/v1", "name": "o1_primary", "outcome": "o1", "purpose": "primary",
            "data": {"path": "03_adatok/o1.csv"}, "options": {"measure": "RR"}}
    os.makedirs(os.path.join(root, "05_elemzes", "specs"))
    sp = os.path.join(root, "05_elemzes", "specs", "o1_primary.json")
    S.save_spec(sp, spec)
    res = engine_api.analyze(sp, mode="commit", project_root=root)
    run = res["run"]
    uids = store.ProjectStore(root).load_table("03_adatok/o1.csv").row_uids
    prov = {"schema": "szk.ma.provenance/v1", "table": "03_adatok/o1.csv", "table_sha256": None,
            "cells": [{"row_uid": uids[3], "field": "n1", "method": "reported", "value_as_entered": CELL_MARKER,
                       "source": {"doc": "file:" + PDF_NAME, "page": 3, "locator": "2. táblázat",
                                  "quote": QUOTE_MARKER}}]}
    with open(os.path.join(root, "03_adatok", "o1.prov.json"), "w", encoding="utf-8") as fh:
        json.dump(prov, fh, ensure_ascii=False, indent=1)
    os.makedirs(os.path.join(root, "forrasok"))
    with open(os.path.join(root, PDF_NAME), "wb") as fh:
        fh.write(b"%PDF-1.4\n" + PDF_MARKER + b"\n%%EOF\n")
    docs = {"schema": "szk.ma.documents/v1",
            "docs": [{"id": "file:" + PDF_NAME, "root": "project", "path": PDF_NAME,
                      "sha256": hashlib.sha256(b"%PDF-1.4\n" + PDF_MARKER + b"\n%%EOF\n").hexdigest(), "pages": 9}]}
    with open(os.path.join(root, "03_adatok", "documents.json"), "w", encoding="utf-8") as fh:
        json.dump(docs, fh, ensure_ascii=False, indent=1)
    os.makedirs(os.path.join(root, "_privat"))
    with open(os.path.join(root, "_privat", "kohorsz.csv"), "w", encoding="utf-8") as fh:
        fh.write("id;ertek\n1;%s\n" % PRIVATE_MARKER)
    projekt.add_finding(root, "reviewer", "major", "SE/SD csere gyanú", detail="Smith 2010", stage="S09")
    projekt.log_decision(root, "user", "Véletlen hatású modell", rationale="klinikai heterogenitás", stage="S09",
                         kb_refs="D-S09-007")
    log = activity.ActivityLog(root)
    log.append("analyze.commit", "user:" + ASSESSOR, argv=run.get("equivalent_argv"),
               outputs=[{"path": run["files"]["results"]["path"], "sha256": run["files"]["results"]["sha256"]}],
               result={"exit_code": 0, "summary": "k=13"})
    return root


def open_app(root, home):
    return server.App(root, selftest=False, kb_build=False, caps_refresh=False, log_stream=None, idle_hours=0,
                      sweep_tmp=False, privacy_home=home, privacy_env={})


def embedded(html_bytes):
    """A beágyazott pillanatkép-adat (JSON) a HTML-ből."""
    html = html_bytes.decode("utf-8") if isinstance(html_bytes, bytes) else html_bytes
    m = re.search(r'<script type="application/json" id="ma-snapshot">(.*?)</script>', html, re.S)
    assert m, "nincs beágyazott pillanatkép-adat"
    return json.loads(m.group(1))


def build(root, home, **kw):
    app = open_app(root, home)
    try:
        return snapshot.build(app=app, home=home, **kw)
    finally:
        app.close()


class _ProjectCase(unittest.TestCase):
    CLASS = "A"

    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_snap_"))
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        cls.root = make_project(cls.tmp, cls.CLASS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TemplateAndCspTests(_ProjectCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html, cls.info = build(cls.root, cls.home)
        cls.text = cls.html.decode("utf-8")
        cls.data = embedded(cls.html)

    def test_deterministic_same_process(self):
        time.sleep(1.1)                           # a falióra nem kerülhet bele
        html2, info2 = build(self.root, self.home)
        self.assertEqual(self.html, html2)
        self.assertEqual(self.info["sha256"], info2["sha256"])
        self.assertEqual(self.info["sha256"], hashlib.sha256(self.html).hexdigest())

    def test_deterministic_separate_process(self):
        """Másik folyamatban (más watcher-rev, kérésazonosító, gyorsítótár) ugyanaz a bájtsor."""
        out = os.path.join(self.tmp, "kulso.snapshot.html")
        env = dict(os.environ, HOME=self.home, VAULT_HOME=os.path.join(self.home, "nincs-vault"))
        env.pop("SOURCE_DATE_EPOCH", None)
        r = subprocess.run([sys.executable, "-m", "ma_gui.snapshot", "--project", self.root, "--out", out],
                           cwd=ROOT, capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(out, "rb") as fh:
            other = fh.read()
        self.assertEqual(hashlib.sha256(other).hexdigest(), self.info["sha256"])

    def test_csp_meta_hashes_every_inline_script_and_style(self):
        m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', self.text)
        self.assertIsNotNone(m)
        csp = m.group(1)
        directives = dict((d.strip().split(" ", 1) + [""])[:2] for d in csp.split(";") if d.strip())
        self.assertEqual(directives["default-src"], "'none'")
        self.assertEqual(directives["connect-src"], "'none'")
        self.assertEqual(directives["img-src"], "data: blob:")
        self.assertEqual(directives["object-src"], "'none'")
        self.assertEqual(directives["base-uri"], "'none'")
        self.assertEqual(directives["form-action"], "'none'")
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("nonce-", csp)
        scripts, styles = [], []
        for mm in re.finditer(r"<(script|style)\b([^>]*)>(.*?)</\1\s*>", self.text, re.S | re.I):
            tag, attrs, body = mm.group(1).lower(), mm.group(2), mm.group(3)
            self.assertNotIn("nonce", attrs)
            self.assertNotIn("src=", attrs)
            digest = "'sha256-%s'" % base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
            if tag == "style":
                styles.append(digest)
            elif 'type="application/json"' in attrs or 'type="text/plain"' in attrs:
                continue                          # adatblokk: nem fut, nem kell hash
            else:
                scripts.append(digest)
        self.assertEqual(len(scripts), 1, "egyetlen végrehajtható inline szkript")
        self.assertEqual(sorted(directives["script-src"].split()), sorted(scripts))
        self.assertEqual(sorted(directives["style-src"].split()), sorted(styles))
        # a CSP-meta a fejben minden szkript/stílus előtt áll
        self.assertLess(self.text.index('http-equiv="Content-Security-Policy"'), self.text.index("<style"))

    def test_no_network_resources_in_markup(self):
        self.assertIsNone(re.search(r"<(?:iframe|object|embed|base)\b", self.text, re.I))
        for link in re.findall(r"<link\b[^>]*>", self.text, re.I):
            self.assertEqual(link, '<link rel="icon" href="data:,">')
        urls = set(re.findall(r"https?://[^\s\"'<>)\\]*", self.text))
        self.assertLessEqual(urls, {"http://www.w3.org/2000/svg"})
        self.assertTrue("{{" not in self.text, "nem szerepelhet: %r" % ("{{",))

    def test_embedded_data_shape(self):
        d = self.data
        self.assertEqual(d["schema"], snapshot.SCHEMA)
        self.assertRegex(d["state_id"], r"^[0-9a-f]{64}$")
        self.assertRegex(d["state_time"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        r = d["routes"]
        for key in ("GET /api/engine", "GET /api/project", "GET /api/privacy", "GET /api/capabilities",
                    "GET /api/log/finding", "GET /api/log/decision", "GET /api/log/activity",
                    "GET /api/runs?outcome=o1", "GET /api/runs?primary=1"):
            self.assertIn(key, r)
            self.assertTrue(r[key]["ok"], key)
            self.assertEqual(r[key]["meta"]["request_id"], "snapshot")
            self.assertEqual(r[key]["meta"]["elapsed_ms"], 0)
        runs = r["GET /api/runs?outcome=o1"]["data"]["runs"]
        self.assertEqual(len(runs), 1)
        plot = r["GET /api/runs/%s/plot" % runs[0]["run_id"]]
        self.assertTrue(plot["ok"])
        self.assertEqual(plot["data"]["schema"], "szk.ma.plot/v2")
        self.assertEqual(len(plot["data"]["studies"]), 13)
        # a motor szövegei változatlanok (a felület nem számol)
        with open(os.path.join(self.root, *runs[0]["files"]["plot"]["path"].split("/")), encoding="utf-8") as fh:
            res = json.load(fh)
        self.assertEqual(plot["data"]["studies"][0]["display_text"], res["studies"][0]["display_text"])
        m = d["manifest"]
        self.assertEqual(m["schema"], snapshot.MANIFEST_SCHEMA)
        self.assertEqual(m["data_class"], "A")
        self.assertEqual(m["created"], d["state_time"])
        self.assertIn("o1", m["outcomes"])
        self.assertEqual([x["run_id"] for x in m["runs"]], [runs[0]["run_id"]])
        self.assertEqual(sorted(m["routes"]), sorted(r))
        self.assertTrue(r["GET /api/capabilities"]["data"]["snapshot"])
        # a projektállapot ideje a legkésőbbi rögzített esemény, nem a falióra
        self.assertLessEqual(d["state_time"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def test_commands_parse_with_the_real_parsers(self):
        """Minden író parancs-sablon (mintaértékekkel kitöltve) a motor argparse-ával értelmezhető."""
        sample = {"agent": "reviewer", "decision": "Véletlen hatás; 'idézőjel' és \"macskaköröm\"", "rationale": "x",
                  "stage": "S09", "kb_refs": ["D-S09-007", "V011"], "alternatives": "fix", "supersedes": 2,
                  "severity": "major", "title": "Cím", "detail": "r", "evidence": "e", "id": 3, "status": "fixed",
                  "resolution": "javítva", "verdict": "PASS", "summary": "ok", "mode": "commit",
                  "spec": {"name": "o1_primary"}, "options": {"question": "BCG és TBC"}, "target": "rv-pmid-1",
                  "value": "include", "reason": "PICO"}
        parser = engine_cli.build_parser()
        seen = 0
        for c in self.data["commands"]:
            if c["kind"] != "write":
                continue
            argv = fill_template(c["argv"], sample, "/tmp/projekt mappa")
            self.assertEqual(argv[0], "ma.py")
            if argv[1:3] == ["gui", "snapshot"]:
                snapshot.build_parser().parse_args(argv[3:])
            elif argv[1:3] == ["gui", "audit-export"]:
                from ma_gui import audit_export
                audit_export.build_parser().parse_args(argv[3:])
            elif argv[1] == "headhunter":
                # a Metaheadhunter saját parancssora (ma.py headhunter … → metaelemzes.headhunter.cli)
                from metaelemzes.headhunter import cli as hh_cli
                with contextlib.redirect_stderr(io.StringIO()):
                    ns = hh_cli.build_parser().parse_args(argv[2:])
                self.assertTrue(hasattr(ns, "func"), argv)
            else:
                with contextlib.redirect_stderr(io.StringIO()):
                    ns = parser.parse_args(argv[1:])
                self.assertTrue(hasattr(ns, "func"), argv)
            seen += 1
        self.assertGreaterEqual(seen, 8)
        # opcionális csoport elmarad, a kötelező hiányzó mező láthatóan jelölt
        dec = next(c for c in self.data["commands"] if c["path"] == "/api/log/decision")
        self.assertEqual(fill_template(dec["argv"], {"decision": "d"}, "P"),
                         ["ma.py", "project", "log", "P", "--agent", "user", "--decision", "d"])
        self.assertIn("<decision>", fill_template(dec["argv"], {}, "P"))

    def test_project_dir_hint_redacted(self):
        self.assertEqual(self.data["project"]["dir_hint"], snapshot.PROJECT_DIR_HINT)
        self.assertTrue(self.root not in self.text, "nem szerepelhet: %r" % (self.root,))
        self.assertTrue(self.tmp not in self.text, "nem szerepelhet: %r" % (self.tmp,))


def fill_template(tpl, body, project):
    """A provider.js fill()-jének Python-tükre (a teszthez): {mező|alap}, opcionális csoport, <mező>."""
    ctx = dict(body, project=project)

    def text(v):
        if v is None:
            return ""
        if isinstance(v, list):
            return ",".join(str(x) for x in v if x not in (None, ""))
        if isinstance(v, dict):
            return ""
        return str(v)

    def lookup(name):
        cur = ctx
        for p in name.split("."):
            cur = cur.get(p) if isinstance(cur, dict) else None
        return cur

    def sub(part):
        m = re.match(r"^\{([A-Za-z0-9_.]+)(?:\|([^}]*))?\}$", part)
        if not m:
            return part
        v = text(lookup(m.group(1)))
        if v != "":
            return v
        return m.group(2)
    out = []
    for part in tpl:
        if isinstance(part, list):
            vals = [sub(p) for p in part]
            if all(v not in (None, "") for v in vals):
                out.extend(vals)
            continue
        v = sub(part)
        out.append(v if v not in (None, "") else "<%s>" % re.match(r"^\{([A-Za-z0-9_.]+)", part).group(1))
    return out


class RedactionMatrixTests(unittest.TestCase):
    """Adatosztály × kapcsoló → mi kerülhet a fájlba (7.4, 7.7)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_snap_m_"))
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        cls.roots = {c: make_project(cls.tmp, c, "proj" + c) for c in ("A", "B", "C")}
        cls.out = {}
        for c, root in cls.roots.items():
            html, info = build(root, cls.home)
            cls.out[c] = (html.decode("utf-8"), embedded(html), info)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _table_key(self):
        return "GET /api/table?dataset=03_adatok%2Fo1.csv"

    def test_class_a_includes_tables_by_default(self):
        text, d, info = self.out["A"]
        self.assertTrue(d["routes"][self._table_key()]["ok"])
        self.assertTrue(CELL_MARKER in text, "hiányzik: %r" % (CELL_MARKER,))
        self.assertTrue(d["manifest"]["policy"]["include"]["data_tables"])
        self.assertIn(snapshot.route_key("POST", "/api/validate", {"dataset": "03_adatok/o1.csv"}), d["routes"])

    def test_class_b_excludes_tables_by_default(self):
        text, d, info = self.out["B"]
        env = d["routes"][self._table_key()]
        self.assertFalse(env["ok"])
        self.assertIn("B osztály", env["error"]["message"])
        self.assertFalse(has_cell_marker(text), "B: cellaérték (tábla, eredet, ábra-oszlop) nem kerülhet bele")
        self.assertFalse(d["manifest"]["policy"]["include"]["data_tables"])
        keys = [r["key"] for r in d["manifest"]["redactions"]]
        self.assertIn("data_tables", keys)
        self.assertIn("assessors", keys)
        self.assertTrue(any(x["pattern"] == "03_adatok/**/*.csv" for x in d["manifest"]["excluded"]))
        # ábra: a becslések maradnak, a nyers cellaoszlopok nem
        runs = d["routes"]["GET /api/runs?outcome=o1"]["data"]["runs"]
        plot = d["routes"]["GET /api/runs/%s/plot" % runs[0]["run_id"]]["data"]
        self.assertEqual(len(plot["studies"]), 13)
        self.assertTrue(all(not s.get("cells") for s in plot["studies"]))
        self.assertTrue(all("display_text" in s for s in plot["studies"]))
        # validálás: csak darabszám és kód
        v = d["manifest"]["validation"]["03_adatok/o1.csv"]
        self.assertEqual(sorted(v), sorted(["schema", "measure", "n_rows", "k_analysable", "summary", "input_sha256",
                                            "codes"]))
        self.assertNotIn(snapshot.route_key("POST", "/api/validate", {"dataset": "03_adatok/o1.csv"}), d["routes"])

    def test_class_b_tables_can_be_included_on_request(self):
        html, info = build(self.roots["B"], self.home, include={"data_tables": True})
        self.assertTrue(CELL_MARKER in html.decode("utf-8"), "hiányzik: %r" % (CELL_MARKER,))
        self.assertTrue(embedded(html)["manifest"]["policy"]["include"]["data_tables"])

    def test_class_c_never_tables(self):
        text, d, info = self.out["C"]
        self.assertFalse(d["routes"][self._table_key()]["ok"])
        self.assertIn("C osztály", d["routes"][self._table_key()]["error"]["message"])
        self.assertFalse(has_cell_marker(text), "nem szerepelhet: %r" % (CELL_MARKER,))
        with self.assertRaises(snapshot.SnapshotError) as cm:
            build(self.roots["C"], self.home, include={"data_tables": True})
        self.assertEqual(cm.exception.code, "FORBIDDEN")

    def test_private_folder_never(self):
        for c, (text, d, info) in self.out.items():
            self.assertTrue(PRIVATE_MARKER not in text, "nem szerepelhet: %r · %s" % (PRIVATE_MARKER, c))
            self.assertTrue("_privat/kohorsz" not in text, "nem szerepelhet: %r · %s" % ("_privat/kohorsz", c))
            self.assertTrue(any(x["pattern"] == "_privat/**" for x in d["manifest"]["excluded"]), c)
        html, _ = build(self.roots["A"], self.home, include={"data_tables": True}, redact={"abs_paths": False})
        self.assertTrue(PRIVATE_MARKER not in html.decode("utf-8"), "nem szerepelhet: %r" % (PRIVATE_MARKER,))

    def test_pdf_never_only_id_page_sha(self):
        for c, (text, d, info) in self.out.items():
            self.assertTrue(PDF_MARKER.decode() not in text, "nem szerepelhet: %r · %s" % (PDF_MARKER.decode(), c))
            self.assertTrue(any(x["pattern"] == "**/*.pdf" for x in d["manifest"]["excluded"]), c)
            if c == "C":            # C osztályban a jegyzék helye a _privat/ — soha nem kerül bele
                self.assertNotIn("GET /api/documents", d["routes"])
                continue
            docs = d["routes"]["GET /api/documents"]["data"]["docs"]
            self.assertEqual(sorted(docs[0]), ["id", "pages", "sha256"], c)
            self.assertFalse([k for k in docs[0] if k in ("path", "root")], c)
        prov = self.out["A"][1]["routes"]["GET /api/provenance?dataset=03_adatok%2Fo1.csv"]["data"]["provenance"]
        self.assertEqual(prov["cells"][0]["source"]["page"], 3)

    def test_quotes_redacted_by_default_and_on_request_kept(self):
        for c, (text, d, info) in self.out.items():
            self.assertTrue(QUOTE_MARKER not in text, "nem szerepelhet: %r · %s" % (QUOTE_MARKER, c))
        html, _ = build(self.roots["A"], self.home, redact={"quotes": False})
        self.assertTrue(QUOTE_MARKER in html.decode("utf-8"), "hiányzik: %r" % (QUOTE_MARKER,))

    def test_provenance_values_only_with_tables(self):
        prov_b = self.out["B"][1]["routes"]["GET /api/provenance?dataset=03_adatok%2Fo1.csv"]["data"]["provenance"]
        self.assertNotIn("value_as_entered", prov_b["cells"][0])
        self.assertEqual(prov_b["cells"][0]["source"]["page"], 3)
        prov_a = self.out["A"][1]["routes"]["GET /api/provenance?dataset=03_adatok%2Fo1.csv"]["data"]["provenance"]
        self.assertEqual(prov_a["cells"][0]["value_as_entered"], CELL_MARKER)

    def test_assessors_to_initials_in_b_and_c(self):
        for c in ("B", "C"):
            text, d, info = self.out[c]
            self.assertTrue(ASSESSOR not in text, "nem szerepelhet: %r · %s" % (ASSESSOR, c))
            actors = [it["actor"] for it in d["routes"]["GET /api/log/activity"]["data"]["items"]]
            self.assertIn("user:KB", actors, c)
        self.assertTrue(ASSESSOR in self.out["A"][0], "hiányzik: %r" % (ASSESSOR,))

    def test_absolute_paths_removed_by_default(self):
        for c, (text, d, info) in self.out.items():
            self.assertTrue(self.tmp not in text, "nem szerepelhet: %r · %s" % (self.tmp, c))
            self.assertTrue(self.home not in text, "nem szerepelhet: %r · %s" % (self.home, c))
        html, _ = build(self.roots["A"], self.home, redact={"abs_paths": False})
        self.assertTrue(self.roots["A"] in html.decode("utf-8"), "hiányzik: %r" % (self.roots["A"],))

    def test_kb_full_text_never(self):
        """A KB teljes szövegét (chunk) a pillanatkép akkor sem veszi fel, ha egy hivatkozott azonosító oda mutat."""
        from metaelemzes import kb as kbmod
        real = kbmod.show

        def fake(item_id, db=None):
            item = real(item_id, db)
            if item_id == "D-S09-007":
                return {"_table": "chunk", "chunk_id": item_id, "text": "JOGVEDETT-TELJES-SZOVEG"}
            return item
        with mock.patch.object(kbmod, "show", side_effect=fake):
            html, _ = build(self.roots["A"], self.home)
        text = html.decode("utf-8")
        self.assertTrue("JOGVEDETT-TELJES-SZOVEG" not in text, "nem szerepelhet: %r" % ("JOGVEDETT-TELJES-SZOVEG",))
        self.assertNotIn("GET /api/kb/item/D-S09-007", embedded(html)["routes"])
        self.assertIn("GET /api/kb/item/V006", embedded(html)["routes"])

    def test_include_toggles_drop_route_groups(self):
        html, _ = build(self.roots["A"], self.home, include={"decisions": False, "activity": False,
                                                            "specs_runs": False})
        r = embedded(html)["routes"]
        self.assertFalse([k for k in r if k.startswith("GET /api/log/") or k.startswith("GET /api/runs")])
        self.assertIn("GET /api/engine", r)

    def test_unknown_toggle_rejected(self):
        with self.assertRaises(snapshot.SnapshotError):
            build(self.roots["A"], self.home, include={"mindent": True})
        with self.assertRaises(snapshot.SnapshotError):
            build(self.roots["A"], self.home, redact={"quotes": "igen"})


class PolicyUnitTests(unittest.TestCase):
    def test_defaults_by_class(self):
        a, b, c = (snapshot.policy_defaults(x) for x in "ABC")
        self.assertTrue(a["include"]["data_tables"])
        self.assertFalse(b["include"]["data_tables"])
        self.assertFalse(c["include"]["data_tables"])
        self.assertEqual((a["redact"]["assessors"], b["redact"]["assessors"], c["redact"]["assessors"]),
                         (False, True, True))
        for p in (a, b, c):
            self.assertTrue(p["redact"]["quotes"] and p["redact"]["abs_paths"])

    def test_cli_toggles(self):
        inc, red = snapshot.toggles_from_cli(["tables,quotes", "paths"], ["assessors"])
        self.assertEqual(inc, {"data_tables": False})
        self.assertEqual(red, {"quotes": True, "abs_paths": True, "assessors": False})
        inc, red = snapshot.toggles_from_cli(None, ["tables", "idezetek"])
        self.assertEqual(inc, {"data_tables": True})
        self.assertEqual(red, {"quotes": False})
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.toggles_from_cli(["ismeretlen"], None)

    def test_initials(self):
        self.assertEqual(snapshot.initials("Szili Károly"), "SzK")
        self.assertEqual(snapshot.initials("Kovács Béla"), "KB")
        self.assertEqual(snapshot.initials("Gyöngyi Zsófia"), "GyZs")
        self.assertEqual(snapshot.initials("SzK"), "SzK")
        self.assertEqual(snapshot._actor_initials("user:Kovács Béla"), "user:KB")
        self.assertEqual(snapshot._actor_initials("external"), "external")

    def test_query_string_matches_js_encoding(self):
        self.assertEqual(snapshot.query_string({"dataset": "03_adatok/o1.csv", "a": "x y!*'()~"}),
                         "a=x%20y%21%2A%27%28%29~&dataset=03_adatok%2Fo1.csv")
        self.assertEqual(snapshot.route_key("get", "/api/runs", {"outcome": "o1"}), "GET /api/runs?outcome=o1")

    def test_state_time(self):
        self.assertEqual(snapshot.state_time([{"ts": "2026-10-04T21:12:00Z"}, {"x": [{"started": "2026-10-05T01:00:00"}]}],
                                             environ={}), "2026-10-05T01:00:00Z")
        self.assertEqual(snapshot.state_time([], environ={}), snapshot.EPOCH_TS)
        self.assertEqual(snapshot.state_time([{"ts": "2026-10-04T21:12:00Z"}], environ={"SOURCE_DATE_EPOCH": "0"}),
                         "1970-01-01T00:00:00Z")

    def test_render_rejects_bad_template(self):
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.render("<html>{{SNAPSHOT_DATA}}</html>", {})
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.render('<meta content="{{SNAPSHOT_CSP}}"><script nonce="{{CSP_NONCE}}">x</script>'
                            '{{SNAPSHOT_DATA}}', {})

    def test_json_for_script_cannot_close_tag(self):
        s = snapshot.json_for_script({"x": "</script><script>alert(1)</script> "})
        self.assertNotIn("</script", s)
        self.assertNotIn(" ", s)
        self.assertEqual(json.loads(s)["x"], "</script><script>alert(1)</script> ")


class CliTests(_ProjectCase):
    CLASS = "B"

    def test_main_writes_default_location_and_reports(self):
        out = io.StringIO()
        rc = snapshot.main(["--project", self.root], out=out)
        self.assertEqual(rc, 0, out.getvalue())
        d = os.path.join(self.root, *snapshot.OUT_DIR_REL.split("/"))
        files = [f for f in os.listdir(d) if f.endswith(snapshot.SNAPSHOT_SUFFIX)]
        self.assertEqual(len(files), 1)
        self.assertIn("Pillanatkép kész", out.getvalue())
        self.assertIn("ne publikáld", out.getvalue())
        rc = snapshot.main(["--project", self.root], out=io.StringIO())
        self.assertEqual(rc, 0)
        self.assertEqual(len([f for f in os.listdir(d) if f.endswith(snapshot.SNAPSHOT_SUFFIX)]), 1,
                         "ugyanaz az állapot ugyanazt a fájlnevet adja")

    def test_main_json_and_redact_keep(self):
        out = io.StringIO()
        target = os.path.join(self.tmp, "x.html")
        rc = snapshot.main(["--project", self.root, "--out", target, "--keep", "tables", "--redact", "log", "--json"],
                           out=out)
        self.assertEqual(rc, 0, out.getvalue())
        info = json.loads(out.getvalue())
        self.assertEqual(info["path"], target)
        self.assertTrue(info["manifest"]["policy"]["include"]["data_tables"])
        self.assertFalse(info["manifest"]["policy"]["include"]["decisions"])
        with open(target, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), info["sha256"])

    def test_main_errors(self):
        out = io.StringIO()
        self.assertEqual(snapshot.main(["--project", os.path.join(self.tmp, "nincs")], out=out), 2)
        self.assertIn("HIBA", out.getvalue())
        out = io.StringIO()
        self.assertEqual(snapshot.main(["--project", self.root, "--redact", "valami"], out=out), 2)
        self.assertIn("Ismeretlen kapcsoló", out.getvalue())

    def test_class_c_keep_tables_refused(self):
        root = make_project(self.tmp, "C", "projC_cli")
        out = io.StringIO()
        self.assertEqual(snapshot.main(["--project", root, "--keep", "tables"], out=out), 2)
        self.assertIn("C osztály", out.getvalue())


if __name__ == "__main__":
    unittest.main()
