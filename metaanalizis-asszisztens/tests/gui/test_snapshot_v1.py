# -*- coding: utf-8 -*-
"""A pillanatkép és az audit-csomag a v1-képernyőkkel (terv 2.5, 3.5.17, 7.4, 7.7; v1 integráció).

Mit bizonyít:
- a v1-képernyők a pillanatképben is működnek: eszköz-definíciók, értékelés-lista eszközönként, egy-egy értékelés
  (a kliens pontos kulcsával), konszenzus-nézet, forgalmi lámpa, GRADE + tanács + SoF, Protokoll, kettős kinyerés
  jegyzéke (és A osztályban az összevetés), ábra-export, adapterek, Metaheadhunter állapot és források;
- kitakarás: az értékelések bizonyíték-idézete az idézet-kitakarással kimarad; B/C osztályban az értékelő monogram —
  a kulcsban (…?rater=) és az adatban egyformán —, a fenntartott 'consensus' azonosító nem változik; a kettős
  összevetés (cellaértékek) adattábla nélkül nem kerül bele; API-kulcs értéke soha;
- az audit-csomag a GRADE-ítéletet ('grade') és a Metaheadhunter állapotát ('headhunter') is viszi, a gyorsítótárat és
  a forrás-áttekintések szövegrészeit nem; a konszenzus-fájl neve B osztályban is 'consensus' marad."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from metaelemzes import api  # noqa: E402
from ma_gui import audit_export, snapshot  # noqa: E402
import test_snapshot as TS  # noqa: E402

EVIDENCE_MARKER = "BIZONYITEKIDEZET-5b1e"
KETTOS_MARKER = "98765"
KEY_MARKER = "SCOPUSKULCS-0f0f0f0f"
LONG_RATER = "Kovacs_Bela"           # B/C osztályban → KB


def _rob2_doc(rater, quote=False):
    inst = api.instrument_get("rob2")
    answers = {}
    for it in inst["items"]:
        if "assignment" in (it.get("scopes") or it.get("scope") or ["all"]) or "all" in (it.get("scopes") or []):
            answers[it["key"]] = {"value": "no" if it.get("polarity") == "reverse" else "yes"}
    first = sorted(answers)[0]
    if quote:
        answers[first]["evidence"] = {"text": EVIDENCE_MARKER, "page": 4, "locator": "Methods"}
    return {"schema": "szk.appraisal/v1", "tool": "rob2", "scope": "assignment",
            "target": {"unit": "S1", "study_id": "S1", "outcome": "o1", "key": "o1"}, "assessor": rater,
            "second_assessor": None, "status": "draft", "origin": "human", "answers": answers,
            "domain_judgements": [], "applicability": [], "overall": None}


def _grade_doc(run_id):
    why = {"rationale": "a domén indoklása"}
    return {"schema": "szk.ma.grade/v1", "run_id": run_id, "start": "high", "start_reason": "RCT",
            "domains": {"risk_of_bias": dict(why, rating="serious", step=-1),
                        "inconsistency": dict(why, rating="not serious", step=0),
                        "indirectness": dict(why, rating="not serious", step=0),
                        "imprecision": dict(why, rating="not serious", step=0),
                        "publication_bias": dict(why, rating="suspected", step=None)},
            "upgrades": {"large_effect": False, "dose_response": False, "opposing_confounding": False}}


def make_v1_project(base, data_class, name):
    root = TS.make_project(base, data_class, name)
    apdir = os.path.join(root, "04_torzitas_kockazat", "appraisals")
    os.makedirs(apdir, exist_ok=True)
    for rater, quote in (("SzK", True), (LONG_RATER, False)):
        with open(os.path.join(apdir, "S1.rob2.o1.%s.json" % rater), "w", encoding="utf-8") as fh:
            json.dump(_rob2_doc(rater, quote), fh, ensure_ascii=False, indent=1)
    cons = _rob2_doc("SzK")
    cons.update(status="draft", consensus_of=[{"assessor": "SzK"}, {"assessor": LONG_RATER}])
    with open(os.path.join(apdir, "S1.rob2.o1.consensus.json"), "w", encoding="utf-8") as fh:
        json.dump(cons, fh, ensure_ascii=False, indent=1)
    run_id = sorted(os.listdir(os.path.join(root, "05_elemzes", "o1")))[0]
    api.grade_put(root, "o1", _grade_doc(run_id), actor="user")
    kdir = os.path.join(root, "03_adatok", "kettos")
    os.makedirs(kdir)
    with open(os.path.join(root, "03_adatok", "o1.csv"), encoding="utf-8") as fh:
        text = fh.read()
    for side, body in (("A", text), ("B", text.replace(";%s;" % TS.CELL_MARKER, ";%s;" % KETTOS_MARKER, 1))):
        with open(os.path.join(kdir, "o1.%s.csv" % side), "w", encoding="utf-8", newline="") as fh:
            fh.write(body)
    env = api.headhunter_init(root, "BCG-oltás és tuberkulózis", actor="user:" + TS.ASSESSOR)
    assert env.get("ok"), env
    return root, run_id


class SnapshotV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ma_snap_v1_"))
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        cls.out = {}
        cls.roots = {}
        cls.run_ids = {}
        with mock.patch.dict(os.environ, {"MA_SCOPUS_APIKEY": KEY_MARKER}):
            for c in ("A", "B"):
                root, run_id = make_v1_project(cls.tmp, c, "v1" + c)
                cls.roots[c] = root
                cls.run_ids[c] = run_id
                html, info = TS.build(root, cls.home)
                cls.out[c] = (html.decode("utf-8"), TS.embedded(html))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_v1_screens_are_recorded(self):
        text, d = self.out["A"]
        r = d["routes"]
        for key in ("GET /api/instruments", "GET /api/instruments/rob2", "GET /api/appraisals",
                    "GET /api/appraisals?tool=rob2", "GET /api/appraisals/S1/rob2?rater=SzK&target=o1",
                    "GET /api/appraisals/S1/rob2?rater=consensus&target=o1",
                    "GET /api/appraisals/consensus/S1/rob2?target=o1", "GET /api/appraisals/rob-summary?tool=rob2",
                    "GET /api/appraisals/rob-summary?outcome=o1&tool=rob2", "GET /api/grade/o1",
                    "GET /api/grade/o1/advice", "GET /api/sof/o1", "GET /api/protocol", "GET /api/kettos",
                    "POST /api/compare?outcome=o1", "GET /api/figures", "GET /api/figures?run=" + self.run_ids["A"],
                    "GET /api/adapters", "GET /api/headhunter/status", "GET /api/headhunter/sources"):
            self.assertIn(key, r, key)
            self.assertTrue(r[key]["ok"], key)
        # a GRADE: feloldatlan publikációs torzítás → nincs bizonyosság (4. döntés), és ez a pillanatképben is látszik
        g = r["GET /api/grade/o1"]["data"]["grade"]
        self.assertIsNone(g["certainty"])
        self.assertEqual(g["domains"]["publication_bias"]["status"], "unresolved")
        # A osztály: a kettős összevetés (cellaértékekkel) benne van
        self.assertIn(KETTOS_MARKER, text)
        # a konszenzus-vázlat nem értékelő: az egyetlen nézetében is 'consensus'
        self.assertEqual(r["GET /api/appraisals/S1/rob2?rater=consensus&target=o1"]["data"]["rater"], "consensus")

    def test_evidence_quotes_redacted_by_default(self):
        for c, (text, d) in self.out.items():
            self.assertTrue(EVIDENCE_MARKER not in text, "nem szerepelhet: %r · %s" % (EVIDENCE_MARKER, c))
        html, _ = TS.build(self.roots["A"], self.home, redact={"quotes": False})
        self.assertTrue(EVIDENCE_MARKER in html.decode("utf-8"), "kérésre megmarad")

    def test_assessor_aliases_match_keys_and_data_in_class_b(self):
        text, d = self.out["B"]
        r = d["routes"]
        self.assertTrue(LONG_RATER not in text, "nem szerepelhet: %r" % LONG_RATER)
        key = "GET /api/appraisals/S1/rob2?rater=KB&target=o1"
        self.assertIn(key, r)
        view = r[key]["data"]
        self.assertEqual(view["rater"], "KB")
        self.assertEqual(view["doc"]["assessor"], "KB")
        self.assertTrue(view["path"].endswith("S1.rob2.o1.KB.json"), view["path"])
        raters = sorted(it["rater"] for it in r["GET /api/appraisals"]["data"]["items"])
        self.assertEqual(raters, ["KB", "SzK", "consensus"])
        # A osztályban a teljes azonosító marad
        self.assertTrue(LONG_RATER in self.out["A"][0], "hiányzik: %r" % LONG_RATER)

    def test_cell_values_and_keys_never_without_tables(self):
        text, d = self.out["B"]
        self.assertNotIn("POST /api/compare?outcome=o1", d["routes"])
        self.assertTrue(KETTOS_MARKER not in text, "B: a kettős összevetés cellaértéke nem kerülhet bele")
        for c, (t, _d) in self.out.items():
            self.assertTrue(KEY_MARKER not in t, "API-kulcs értéke soha · %s" % c)
        rows = d["routes"]["GET /api/headhunter/sources"]["data"]["rows"]
        scopus = [x for x in rows if x["source"] == "scopus"][0]
        self.assertIs(scopus["key_configured"], True)          # csak a jelenléte látszik

    def test_headhunter_commands_in_snapshot(self):
        _t, d = self.out["A"]
        cmds = [c for c in d["commands"] if c["path"].startswith("/api/headhunter/")]
        steps = {c["when"]["step"] for c in cmds if c.get("when", {}).get("step")}
        self.assertTrue({"init", "sources_check", "find_reviews", "merge", "prisma"} <= steps, steps)
        dec = [c for c in cmds if c.get("when", {}).get("kind") == "decide"][0]
        self.assertIn("user:<név>", dec["argv"])

    def test_audit_bundle_carries_grade_and_headhunter(self):
        app = TS.open_app(self.roots["B"], self.home)
        try:
            data, _info = audit_export.build(app=app)
        finally:
            app.close()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            manifest = json.loads(z.read([n for n in names if n.endswith("manifest.json")][0]).decode("utf-8"))
        roles = {f["path"]: f["role"] for f in manifest["files"]}
        self.assertEqual(roles.get("06_kezirat/grade/o1.grade.json"), "grade")
        self.assertEqual(roles.get("01_kereses/headhunter/state.json"), "headhunter")
        self.assertIn("04_torzitas_kockazat/appraisals/S1.rob2.o1.consensus.json", roles)
        self.assertIn("04_torzitas_kockazat/appraisals/S1.rob2.o1.KB.json", roles)
        excluded = [x["pattern"] for x in manifest["excluded"]]
        self.assertIn("01_kereses/headhunter/cache/**", excluded)
        self.assertIn("01_kereses/headhunter/reviews/**", excluded)
        self.assertFalse([n for n in names if "/headhunter/cache/" in n])


if __name__ == "__main__":
    unittest.main()
