# -*- coding: utf-8 -*-
"""MVP E2: a validálás szerződéses dokumentuma (szk.ma.validation/v1, terv 4.3), a lokátorok
(row/rows/line/fields/row_uid/blocking/kb_id), a nyers cellaszöveges bemenet (szk.ma.validate-request/v1),
a determinisztikus row_uid (4.6) és a V025 (képletnek látszó szöveges cella).
"""
import base64
import csv
import glob
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _helpers import ROOT
from metaelemzes import cli, kb, pipeline, tableio
from metaelemzes import effect_sizes as E
from metaelemzes import validate as V

PELDAK = os.path.join(ROOT, "peldak")
EXAMPLES = (("bcg_oltas_RR.csv", "RR"), ("molloy2014_korrelacio.csv", "ZCOR"),
            ("normand1999_folytonos.csv", "SMD"), ("pritz1997_arany.csv", "PLO"))
LEGACY_KEYS = {"code", "severity", "title", "study", "detail", "advice", "source", "row"}
FINDING_REQUIRED = ("code", "severity", "title", "study", "row", "fields", "detail", "advice", "source", "blocking")
CONTRACT_EXAMPLE = {
    "schema": "szk.ma.validate-request/v1", "measure": "RR", "options": {"cc": 0.5, "cc_to": "only0", "drop00": None},
    "table": {"header": ["row_uid", "study", "e1", "n1", "e2", "n2", "rob", "estimated", "forras_oldal"],
              "rows": [["r7f3a2", "Aronson 1948", "4", "123", "11", "139", "low", "nem", "3"]], "decimal_mark": None},
    "base_sha256": "9f3a" + "0" * 60}

BAD_BINARY = ("study,e1,n1,e2,n2,year,rob,estimated\n"
              "A,4,123,11,139,2001a,low,nem\n"
              "B,0,50,0,48,2003,high,igen\n"
              "C,12,10,3,40,in press,whatever,maybe\n"
              "C,5,100,\"2,000\",n,2005,some concerns,no\n"
              "\n"
              "D,3,30,2,40,2006,low,yes\n"
              ",3,40,4,41,2007,,\n")


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:
            code = exc.code
    return code, buf.getvalue(), err.getvalue()


def check_contract(tc, doc):
    """A 4.3 séma kötelező mezői és típusai (a ma_gui schema_lite nélkül, szándékosan függetlenül)."""
    for k in ("schema", "engine_version", "measure", "summary", "findings", "k_analysable"):
        tc.assertIn(k, doc)
    tc.assertEqual(doc["schema"], "szk.ma.validation/v1")
    tc.assertIsInstance(doc["engine_version"], str)
    tc.assertEqual(set(doc["summary"]), {"error", "warning", "info"})
    tc.assertIsInstance(doc["k_analysable"], int)
    tc.assertIn(doc["decimal_mark"], (",", ".", None))
    tc.assertTrue(all(isinstance(k, str) and isinstance(v, str) for k, v in doc["column_map"].items()))
    if "input_sha256" in doc:
        tc.assertRegex(doc["input_sha256"], r"^[0-9a-f]{64}$")
    for f in doc["findings"]:
        for k in FINDING_REQUIRED:
            tc.assertIn(k, f)
        tc.assertRegex(f["code"], r"^V\d{3}$")
        tc.assertIn(f["severity"], ("error", "warning", "info"))
        tc.assertRegex(f["kb_id"], r"^([VPX]\d{3}|[A-Z][A-Z0-9_]*-[A-Z0-9-]+)$")
        tc.assertTrue(f["row"] is None or (isinstance(f["row"], int) and f["row"] >= 0))
        tc.assertTrue(all(isinstance(i, int) and i >= 0 for i in f["rows"]))
        tc.assertTrue(f["row_uid"] is None or re.match(r"^r[0-9a-z]{4,12}$", f["row_uid"]))
        tc.assertTrue(f["line"] is None or (isinstance(f["line"], int) and f["line"] >= 1))
        tc.assertTrue(all(isinstance(c, str) for c in f["fields"]))
        tc.assertIsInstance(f["blocking"], bool)
        for k in ("title", "detail", "advice", "source"):
            tc.assertIsInstance(f[k], str)
        tc.assertTrue(f["study"] is None or isinstance(f["study"], str))
        if f["row"] is None:
            tc.assertIsNone(f["row_uid"])
            tc.assertFalse(f["blocking"])
    for x in doc["excluded"]:
        for k in ("study", "row", "reason"):
            tc.assertIn(k, x)
    json.dumps(doc, allow_nan=False)      # NaN / Infinity soha
    tc.assertEqual(V.summarize(doc["findings"]), doc["summary"])


def cells_of(path):
    """A fájl nyers cellái a tableio-val azonos dekódolással és tagolóval: (header, rows, lines, delim)."""
    with open(path, "rb") as fh:
        text, _ = tableio._decode(fh.read())
    delim = tableio._sniff_delimiter(text)
    rdr = csv.reader(io.StringIO(text), delimiter=delim)
    recs, prev = [], 0
    for r in rdr:
        recs.append((prev + 1, r))
        prev = rdr.line_num
    return recs[0][1], [r for _, r in recs[1:]], [ln for ln, _ in recs[1:]], delim


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, text, encoding="utf-8"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
        return p


# ================================================================ V025
class TestV025Rule(_Tmp):
    def test_rule_metadata(self):
        sev, title, advice, source = V.RULES["V025"]
        self.assertEqual(sev, "warning")
        self.assertIn("Képlet", title)
        self.assertIn("értékként", advice)
        self.assertIn("OWASP", source)
        self.assertIn("V025", kb._engine_rules())
        self.assertEqual([c for c in sorted(V.RULES) if c.startswith("V")],
                         ["V%03d" % i for i in range(1, 31)])

    def test_formula_like_truth_table(self):
        yes = ("=B2*2", "+A1", "@SUM(1)", "-lásd szöveg", "\t=1+1", "\r=1", "=1", "+-", "--1", "\tSmith")
        no = ("", "+", "-", "—", "=", "-5", "+3", "-1,5", "-2,000", "-1.234.567", "-1e400", " =1", "Smith",
              "2001a", "NR", "\t12", "\r", None, 5.0)
        for t in yes:
            self.assertTrue(tableio.formula_like(t), repr(t))
        for t in no:
            self.assertFalse(tableio.formula_like(t), repr(t))

    def test_v025_from_file_with_locators_and_cli_text(self):
        p = self.write("f.csv", "study,e1,n1,e2,n2,megjegyzes\n"
                                "\"=HYPERLINK(\"\"http://x\"\")\",4,123,11,139,\n"
                                "B,=B2*2,100,5,100,-5\n"
                                "C,3,100,5,100,@SUM(1)\n")
        rows, meta = tableio.read_table(p)
        self.assertEqual([(f["line"], f["column"]) for f in meta["formula_like"]],
                         [(2, "study"), (3, "e1"), (4, "megjegyzes")])
        f = V.validate(rows, "RR", meta)
        v25 = [x for x in f if x["code"] == "V025"]
        self.assertEqual([(x["study"], x["row"]) for x in v25],
                         [('=HYPERLINK("http://x")', 0), ("B", 1), ("C", 2)])
        # a számoszlopban a képlet V003-as hiba is (a sor kimarad), a V025 csak figyelmeztet
        self.assertIn(("V003", 1), [(x["code"], x.get("row")) for x in f])
        doc = V.validation_document_from_file(p, "RR")
        check_contract(self, doc)
        d25 = [x for x in doc["findings"] if x["code"] == "V025"]
        self.assertEqual([x["fields"] for x in d25], [["study"], ["e1"], ["megjegyzes"]])
        self.assertEqual([x["line"] for x in d25], [2, 3, 4])
        self.assertEqual([x["blocking"] for x in d25], [False, False, False])
        code, so, _ = run_cli("validate", "--data", p, "--measure", "RR")
        self.assertEqual(code, 1)
        self.assertIn("[V025] warning C — Képletnek látszó szöveges cella: 4. sor, megjegyzes oszlop: '@SUM(1)'", so)

    def test_no_formula_key_for_clean_files(self):
        # a meglévő kimenetek bájtra változatlanok: tiszta fájlnál nincs új meta-kulcs és nincs V025
        for name, measure in EXAMPLES:
            rows, meta = tableio.read_table(os.path.join(PELDAK, name))
            self.assertNotIn("formula_like", meta)
            self.assertNotIn("V025", [f["code"] for f in V.validate(rows, measure, meta)])

    def test_filtered_rows_drop_their_v025(self):
        p = self.write("f.csv", "study,e1,n1,e2,n2,csoport\nA,=1+1,10,2,10,x\nB,1,10,2,10,y\nC,1,10,2,10,y\n")
        rows, meta = tableio.read_table(p)
        kept = tableio.apply_filters(rows, exclude=["csoport=x"], meta=meta)
        self.assertNotIn("V025", [f["code"] for f in V.validate(kept, "RR", meta)])


# ================================================================ régi kimenetek
class TestLegacyShape(unittest.TestCase):
    def test_validate_and_check_effect_sizes_keep_legacy_keys(self):
        p = os.path.join(PELDAK, "normand1999_folytonos.csv")
        rows, meta = tableio.read_table(p)
        for measure in ("SMD", "MD", "ROM"):
            f = V.validate(rows, measure, meta)
            es = E.compute(rows, measure, skip_rows=V.blocking_rows(f))
            f += V.check_effect_sizes(es)
            for x in f:
                self.assertLessEqual(set(x), LEGACY_KEYS, x)
                self.assertEqual(list(x)[:7], ["code", "severity", "title", "study", "detail", "advice", "source"])

    def test_cross_study_findings_have_no_legacy_row(self):
        # V007/V011/V022 eddig nem kapott 'row' kulcsot (results.json változatlan); a dokumentumban igen
        rows = [dict(study="A", m1=10, sd1=2, n1=20, m2=9, sd2=2.2, n2=21),
                dict(study="A", m1=11, sd1=0.4, n1=25, m2=9.1, sd2=2.0, n2=21),
                dict(study="C", m1=100, sd1=20, n1=30, m2=95, sd2=19, n2=31)]
        f = V.validate(rows, "MD")
        for x in f:
            if x["code"] in ("V007", "V011", "V015", "V016"):
                self.assertNotIn("row", x)

    def test_results_json_findings_unchanged_shape(self):
        rows, meta = tableio.read_table(os.path.join(PELDAK, "bcg_oltas_RR.csv"))
        out, _ = pipeline.run(rows, {"measure": "RR"}, meta)
        for x in out["validation"]["findings"]:
            self.assertLessEqual(set(x), LEGACY_KEYS)
        self.assertNotIn("formula_like", out["input"])
        self.assertNotIn("row_positions", out["input"])

    def test_read_table_bytes_equals_read_table(self):
        for name, _ in EXAMPLES:
            p = os.path.join(PELDAK, name)
            with open(p, "rb") as fh:
                raw = fh.read()
            a, b = tableio.read_table(p), tableio.read_table_bytes(raw, p)
            self.assertEqual(a[1], b[1])
            self.assertEqual([sorted(r.items()) for r in a[0]], [sorted(r.items()) for r in b[0]])
            self.assertEqual(list(a[1]), ["path", "encoding", "delimiter", "decimal_mark", "columns", "mapping",
                                          "parse_errors", "ambiguous", "n_rows", "duplicate_columns"])


# ================================================================ dokumentum fájlból
class TestDocumentFromFile(_Tmp):
    def test_examples_match_legacy_validate(self):
        for name, measure in EXAMPLES:
            p = os.path.join(PELDAK, name)
            doc = V.validation_document_from_file(p, measure)
            check_contract(self, doc)
            rows, meta = tableio.read_table(p)
            f = V.validate(rows, measure, meta, {})
            es = E.compute(rows, measure, skip_rows=V.blocking_rows(f))
            f += V.check_effect_sizes(es)
            self.assertEqual(len(doc["findings"]), len(f))
            for a, b in zip(f, doc["findings"]):
                self.assertEqual({k: b[k] for k in a}, a)
            self.assertEqual(doc["k_analysable"], len(es))
            with open(p, "rb") as fh:
                self.assertEqual(doc["input_sha256"], hashlib.sha256(fh.read()).hexdigest())
            self.assertEqual(doc["measure"], measure)

    def test_locators_rows_lines_fields_blocking(self):
        p = self.write("b.csv", BAD_BINARY)
        doc = V.validation_document_from_file(p, "RR")
        check_contract(self, doc)
        by = {}
        for f in doc["findings"]:
            by.setdefault(f["code"], []).append(f)
        # V003: 'n' az n2-ben, 4. adatsor (0-alapú 3), fájlsor 5; blokkoló
        v003 = by["V003"][0]
        self.assertEqual((v003["row"], v003["line"], v003["fields"], v003["blocking"]), (3, 5, ["n2"], True))
        # az üres fájlsor (6.) után: D a 7. fájlsor, de a 4. adatsor (0-alapú 4)
        v027 = [f for f in by["V027"] if f["study"] == "C"]
        self.assertEqual({tuple(f["fields"]) for f in v027}, {("rob",), ("estimated",)})
        d_rows = [f for f in doc["findings"] if f["study"] == "D"]
        self.assertTrue(d_rows and all(f["row"] == 4 and f["line"] == 7 for f in d_rows))
        # V007: többsoros, az első sor a 'row'
        v007 = by["V007"][0]
        self.assertEqual((v007["row"], v007["rows"], v007["fields"], v007["blocking"]), (2, [2, 3], ["study"], False))
        # V008 kettős nulla RR-nél kimarad → blokkoló; V029 táblaszintű, de megnevezi a sort
        self.assertEqual((by["V008"][0]["row"], by["V008"][0]["blocking"]), (1, True))
        v029 = by["V029"][0]
        self.assertEqual((v029["row"], v029["rows"], v029["fields"], v029["blocking"]), (None, [5], ["study"], False))
        # V015/V016: táblaszintű
        for code in ("V015", "V016"):
            self.assertEqual((by[code][0]["row"], by[code][0]["rows"], by[code][0]["fields"]), (None, [], []))
        # a hibás sorok uid-ja és sora az 'excluded'-ben is
        ex = {x["row"]: x for x in doc["excluded"]}
        self.assertEqual(sorted(ex), [1, 2, 3])
        self.assertEqual(ex[3]["line"], 5)
        uids = tableio.row_uids(*tableio.read_table(p))
        self.assertEqual(ex[3]["row_uid"], uids[3])
        self.assertEqual(v003["row_uid"], uids[3])
        self.assertEqual(doc["k_analysable"], 3)
        self.assertEqual(doc["n_rows"], 6)

    def test_v022_and_kept_double_zero_not_blocking(self):
        p = self.write("b.csv", "study,e1,n1,e2,n2\nA,0,10,0,12\nB,3,10,4,12\nC,2,10,1,12\n")
        doc = V.validation_document_from_file(p, "RD")      # RD: bent marad
        v008 = [f for f in doc["findings"] if f["code"] == "V008"][0]
        self.assertFalse(v008["blocking"])
        self.assertEqual(doc["k_analysable"], 3)
        doc = V.validation_document_from_file(p, "OR", {"drop00": False, "cc": 0})   # nem számolható → V022 nélkül
        v008 = [f for f in doc["findings"] if f["code"] == "V008"][0]
        self.assertTrue(v008["blocking"])
        self.assertEqual(doc["excluded"][0]["row"], 0)
        p = self.write("r.csv", "study,m1,sd1,n1,m2,sd2,n2\nA,-1,1,10,2,1,10\nB,1,1,10,2,1,10\nC,3,1,10,2,1,10\n")
        doc = V.validation_document_from_file(p, "ROM")
        v022 = [f for f in doc["findings"] if f["code"] == "V022"]
        self.assertEqual([(f["row"], f["blocking"], f["row_uid"] is not None) for f in v022], [(0, True, True)])

    def test_continuous_multi_row_and_cross_study_locators(self):
        p = self.write("c.csv", "study;m1;sd1;n1;m2;sd2;n2;study_id\n"
                                "A;10,5;2,1;20;9,0;2,2;21;S1\n"
                                "B;11,2;0,4;25;9,0;2,2;21;S1\n"
                                "C;100;20;30;95;19;31;S2\n")
        doc = V.validation_document_from_file(p, "MD")
        check_contract(self, doc)
        v017 = [f for f in doc["findings"] if f["code"] == "V017"][0]
        self.assertEqual((v017["row"], v017["rows"]), (0, [0, 1]))
        self.assertEqual(v017["fields"], ["study_id", "m2", "sd2", "n2"])
        v011 = [f for f in doc["findings"] if f["code"] == "V011"][0]
        self.assertEqual((v011["row"], v011["fields"], v011["line"]), (1, ["sd1"], 3))
        self.assertEqual(doc["decimal_mark"], ",")

    def test_v014_row_from_effect_sizes(self):
        p = self.write("x.csv", "study,e1,n1,e2,n2\nA,1,100,90,100\nB,3,10,4,12\n")
        doc = V.validation_document_from_file(p, "OR")
        v014 = [f for f in doc["findings"] if f["code"] == "V014"][0]
        self.assertEqual((v014["row"], v014["fields"], v014["blocking"]), (0, ["e1", "n1", "e2", "n2"], False))

    def test_table_level_and_column_map(self):
        p = self.write("m.csv", "Szerző;M1;m1;sd1;n1;m2;sd2;n2\nA;1;2;1;10;2;1;10\n")
        doc = V.validation_document_from_file(p, "MD")
        self.assertEqual(doc["column_map"]["study"], "Szerző")
        self.assertEqual(doc["column_index"]["m1"], 1)
        v028 = [f for f in doc["findings"] if f["code"] == "V028"][0]
        self.assertIsNone(v028["row"])
        self.assertEqual(v028["fields"], ["m1.1", "m1"])
        self.assertEqual(doc["column_map"]["m1.1"], "m1")
        p = self.write("v1.csv", "study,e1,n1\nA,1,10\n")
        doc = V.validation_document_from_file(p, "OR")
        v001 = [f for f in doc["findings"] if f["code"] == "V001"]
        self.assertEqual([f["fields"] for f in v001], [["e2"], ["n2"]])
        self.assertEqual(doc["k_analysable"], 0)
        self.assertEqual([f["code"] for f in doc["findings"]], ["V001", "V001"])     # mint a `validate`
        check_contract(self, doc)

    def test_all_measures_on_examples_are_contract_clean(self):
        for name, _ in EXAMPLES:
            for measure in E.ALL_MEASURES:
                check_contract(self, V.validation_document_from_file(os.path.join(PELDAK, name), measure))


# ================================================================ nyers cellák
class TestDocumentFromCells(_Tmp):
    def test_parity_with_file_for_examples(self):
        for name, measure in EXAMPLES:
            p = os.path.join(PELDAK, name)
            header, rows, lines, delim = cells_of(p)
            with open(p, "rb") as fh:
                raw = fh.read()
            a = V.validation_document_from_file(p, measure)
            b = V.validation_document(header, rows, measure, delimiter=delim, lines=lines, raw_bytes=raw)
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True), name)
            c = V.validation_document(header, rows, measure)
            self.assertNotIn("input_sha256", c)
            self.assertTrue(all(f["line"] is None for f in c["findings"]))
            self.assertEqual([(f["code"], f["row"], f["row_uid"]) for f in a["findings"]],
                             [(f["code"], f["row"], f["row_uid"]) for f in c["findings"]])

    def test_raw_text_cells_are_parsed_like_files(self):
        hdr = ["study", "m1", "sd1", "n1", "m2", "sd2", "n2"]
        rows = [["A", "12,3", "2,1", "20", "11,0", "2,0", "21"],
                ["B", "11", "2", "NR", "10", "2", "22"],
                ["C", "2,000", "1", "30", "1", "1", "30"],
                ["D", "10", "2", "2,000", "9", "2", "40"]]
        doc = V.validation_document(hdr, rows, "MD")
        check_contract(self, doc)
        codes = [(f["code"], f["row"], tuple(f["fields"])) for f in doc["findings"]]
        self.assertIn(("V002", 1, ("n1",)), codes)          # 'NR' = hiányzó érték
        self.assertEqual(doc["decimal_mark"], ",")          # a 12,3-ból felismerve: a '2,000' = 2,0
        self.assertIn(("V020", 3, ("n1",)), codes)          # n1 = 2,0 (tizedesvesszős tábla)
        self.assertFalse([c for c in codes if c[0] in ("V003", "V023")])
        # pont tizedesjelű táblában a '2,000' ezres tagolás (2000), jelezve
        doc = V.validation_document(hdr, [rows[2], ["E", "1.5", "1", "30", "1", "1", "30"]], "MD")
        self.assertIn(("V023", 0, ("m1",)), [(f["code"], f["row"], tuple(f["fields"])) for f in doc["findings"]])
        # felismerhető tizedesjel nélkül kétértelmű (vesszős tagolás szabálya) → V003, blokkoló
        doc = V.validation_document(hdr, [rows[2]], "MD")
        v003 = [f for f in doc["findings"] if f["code"] == "V003"]
        self.assertEqual([(f["row"], f["fields"], f["blocking"]) for f in v003], [(0, ["m1"], True)])
        self.assertIn("kétértelmű", v003[0]["detail"])
        # a forrásfájl tagolója és tizedesjele megadható
        doc = V.validation_document(hdr, [rows[2]], "MD", delimiter=";")
        self.assertFalse([f for f in doc["findings"] if f["code"] in ("V003", "V023")])
        doc = V.validation_document(hdr, [rows[2]], "MD", decimal_mark=".")
        self.assertIn("V023", [f["code"] for f in doc["findings"]])
        self.assertEqual(doc["decimal_mark"], ".")

    def test_blank_rows_keep_client_positions(self):
        hdr = ["study", "e1", "n1", "e2", "n2"]
        rows = [["A", "1", "10", "2", "10"], ["", "", "", "", ""], ["B", "x", "10", "2", "10"], [None] * 5,
                ["C", "1", "10", "2", "10"]]
        doc = V.validation_document(hdr, rows, "OR", lines=[2, None, 4, 5, 6])
        v003 = [f for f in doc["findings"] if f["code"] == "V003"][0]
        self.assertEqual((v003["row"], v003["line"], v003["study"]), (2, 4, "B"))
        self.assertEqual(doc["n_rows"], 3)
        self.assertEqual(doc["excluded"][0]["row"], 2)
        # a származtatott uid a fájlbeli (üres sorok nélküli) sorindexből készül, mint a tárolóban
        self.assertEqual(v003["row_uid"], tableio.row_uid_for("B", 1))

    def test_contract_request_example(self):
        doc = V.validation_from_request(json.loads(json.dumps(CONTRACT_EXAMPLE)))
        check_contract(self, doc)
        self.assertEqual(doc["base_sha256"], CONTRACT_EXAMPLE["base_sha256"])
        self.assertEqual(doc["k_analysable"], 1)
        self.assertEqual(doc["column_map"]["row_uid"], "row_uid")
        f15 = [f for f in doc["findings"] if f["code"] == "V015"][0]
        self.assertIsNone(f15["row"])
        doc = V.validation_from_request(dict(CONTRACT_EXAMPLE, table=dict(
            CONTRACT_EXAMPLE["table"], rows=[["r7f3a2", "Aronson 1948", "x", "123", "11", "139", "low", "nem", "3"]])))
        v003 = [f for f in doc["findings"] if f["code"] == "V003"][0]
        self.assertEqual((v003["row"], v003["row_uid"], v003["blocking"]), (0, "r7f3a2", True))
        self.assertEqual(doc["excluded"][0]["row_uid"], "r7f3a2")
        text = json.dumps(CONTRACT_EXAMPLE)
        self.assertEqual(V.validation_from_request_text(text), V.validation_from_request(CONTRACT_EXAMPLE))

    def test_numbers_as_cells_are_tolerated(self):
        req = dict(CONTRACT_EXAMPLE, table={"header": ["study", "e1", "n1", "e2", "n2"],
                                            "rows": [["A", 4, 123, 11, 139.0], ["B", 3, 100, None, 90]]})
        doc = V.validation_from_request(req)
        self.assertEqual(doc["k_analysable"], 1)
        self.assertIn(("V002", 1), [(f["code"], f["row"]) for f in doc["findings"]])

    def test_bad_requests_are_rejected_in_hungarian(self):
        base = json.loads(json.dumps(CONTRACT_EXAMPLE))
        bad = [
            ([], "JSON-objektum"),
            (dict(base, schema="szk.ma.validate-request/v2"), "kérés-séma"),
            (dict(base, measure="XX"), "ismeretlen mérték"),
            (dict(base, measure=None), "ismeretlen mérték"),
            (dict(base, table=None), "'table'"),
            (dict(base, table=dict(base["table"], header="study")), "table.header"),
            (dict(base, table=dict(base["table"], rows=[["A", True]])), r"table.rows\[0\]"),
            (dict(base, table=dict(base["table"], rows=[["A", float("nan")]])), r"table.rows\[0\]"),
            (dict(base, table=dict(base["table"], rows="x")), "table.rows"),
            (dict(base, table=dict(base["table"], lines=[0])), "table.lines"),
            (dict(base, table=dict(base["table"], lines=[2, 3])), "'lines' hossza"),
            (dict(base, table=dict(base["table"], decimal_mark=";")), "tizedesjel"),
            (dict(base, table=dict(base["table"], delimiter="|")), "tagoló"),
            (dict(base, table=dict(base["table"], header=["", " "])), "üres fejléc"),
            (dict(base, base_sha256="9f3a…"), "base_sha256"),
            (dict(base, options=[]), "'options'"),
            (dict(base, options={"cc": -1}), "cc:"),
            (dict(base, options={"cc": True}), "cc:"),
            (dict(base, options={"cc_to": "some"}), "cc_to"),
            (dict(base, options={"drop00": "talán"}), "drop00"),
            (dict(base, options={"smd_vtype": "XX"}), "smd_vtype"),
            (dict(base, options={"md_vtype": "XX"}), "md_vtype"),
            (dict(base, options={"j_method": "XX"}), "j_method"),
            (dict(base, options={"label_col": ""}), "label_col"),
        ]
        for req, msg in bad:
            with self.assertRaisesRegex(ValueError, msg):
                V.validation_from_request(req)
        with self.assertRaisesRegex(ValueError, "nem érvényes JSON"):
            V.validation_from_request_text("{")

    def test_compute_options_normalisation_and_effect(self):
        o = V.compute_options({"smd_vtype": "ls2", "drop00": "yes", "md_vtype": "HO", "glass_vtype": "ub",
                               "gen_smd_vtype": "ls", "cc": 0, "model": "fixed"})
        self.assertEqual((o["smd_vtype"], o["drop00"], o["md_vtype"], o["glass_vtype"], o["gen_smd_vtype"], o["cc"]),
                         ("LS2", True, "pooled", "UB", "LS", 0.0))
        self.assertNotIn("model", o)
        self.assertEqual(V.compute_options(None), V.COMPUTE_DEFAULTS)
        hdr, rows = ["study", "e1", "n1", "e2", "n2"], [["A", "0", "10", "0", "12"], ["B", "3", "10", "4", "12"]]
        self.assertEqual(V.validation_document(hdr, rows, "or")["k_analysable"], 1)
        self.assertEqual(V.validation_document(hdr, rows, "OR", {"drop00": False})["k_analysable"], 2)


# ================================================================ row_uid
class TestRowUid(_Tmp):
    def test_spec_and_collisions(self):
        want = "r" + base64.b32encode(hashlib.sha1("Aronson 1948|0".encode("utf-8")).digest()).decode().lower()[:6]
        self.assertEqual(tableio.row_uid_for("Aronson 1948", 0), want)
        self.assertEqual(tableio.row_uid_for("  Aronson 1948 ", 0), want)
        self.assertRegex(want, r"^r[a-z2-7]{6}$")
        first = tableio.row_uid_for("x", 0)
        second = tableio.row_uid_for("x", 0, taken={first})
        self.assertNotEqual(first, second)
        self.assertEqual(second, "r" + base64.b32encode(hashlib.sha1(b"x|0|1").digest()).decode().lower()[:6])
        # a beolvasott (tisztított) címke és a nyers cella ugyanazt adja
        self.assertEqual(tableio.row_uid_for("NA", 3), tableio.row_uid_for(None, 3))
        self.assertEqual(tableio.row_uid_for("A\x01B", 1), tableio.row_uid_for("A B", 1))

    def test_row_uids_from_column_and_derived(self):
        p = self.write("u.csv", "row_uid;study;n\nrabcdef;A;1\n;B;2\nrabcdef;C;3\nXYZ;D;4\nrbcdefg;E;5\n")
        rows, meta = tableio.read_table(p)
        uids = tableio.row_uids(rows, meta)
        self.assertEqual(uids[0], "rabcdef")
        self.assertEqual(uids[4], "rbcdefg")
        self.assertEqual(uids[1], tableio.row_uid_for("B", 1, {"rabcdef", "rbcdefg"}))
        self.assertNotIn(uids[2], ("rabcdef",))            # ismétlődő uid → származtatott
        self.assertEqual(len(set(uids)), 5)
        doc = V.validation_document_from_file(p, "PR")
        self.assertTrue(all(f["row_uid"] == uids[f["row"]] for f in doc["findings"] if f["row"] is not None))

    def test_study_id_label_is_not_used(self):
        p = self.write("s.csv", "Study ID,r,n\nX,0.5,10\nX,0.2,30\n")
        rows, meta = tableio.read_table(p)
        self.assertEqual(rows[0]["study"], "X (1)")
        self.assertEqual(tableio.row_uids(rows, meta), [tableio.row_uid_for("", 0), tableio.row_uid_for("", 1)])

    def test_unique_across_examples(self):
        for name, _ in EXAMPLES:
            rows, meta = tableio.read_table(os.path.join(PELDAK, name))
            uids = tableio.row_uids(rows, meta)
            self.assertEqual(len(set(uids)), len(rows))
            self.assertEqual(uids, tableio.row_uids(*tableio.read_table(os.path.join(PELDAK, name))))


# ================================================================ parse_table
class TestParseTable(unittest.TestCase):
    def test_meta_shape_and_positions(self):
        rows, meta = tableio.parse_table(["study", "x", "n"], [["A", "1", "10"], [], ["B", "2", "20", "9"]])
        self.assertEqual(meta["row_positions"], [0, 2])
        self.assertIsNone(meta["path"])
        self.assertIsNone(meta["delimiter"])
        self.assertEqual([r["_line"] for r in rows], [2, 4])
        self.assertEqual(meta["parse_errors"][0]["kind"], "ragged")
        self.assertIsNone(rows[1]["x"])

    def test_same_rows_as_read_table(self):
        for name, _ in EXAMPLES:
            p = os.path.join(PELDAK, name)
            header, rows_text, lines, delim = cells_of(p)
            a, ma = tableio.read_table(p)
            b, mb = tableio.parse_table(header, rows_text, delimiter=delim, lines=lines)
            self.assertEqual([sorted(r.items()) for r in a], [sorted(r.items()) for r in b])
            for k in ("decimal_mark", "columns", "mapping", "parse_errors", "ambiguous", "n_rows", "duplicate_columns"):
                self.assertEqual(ma[k], mb[k], k)


if __name__ == "__main__":
    unittest.main()
