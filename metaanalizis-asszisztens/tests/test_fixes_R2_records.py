# -*- coding: utf-8 -*-
"""Regressziós tesztek a 2. review-kör PRISMA-, projektnapló- és tudásbázis-javításaihoz.

Minden teszt neve a megállapítás azonosítóját viseli (pl. NF_01 = stats_new:R2-NF-01,
ROB_03 = robustness:R2-ROB-03, KBP_05 = kb_code:KBP-05, AG_10 = agents_e2e:AG-10).
A tudásbázisok ideiglenes fájlban épülnek; a repó tudasbazis.sqlite-ját a tesztek nem írják.
"""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _helpers import ROOT
from metaelemzes import cli, kb, projekt
from metaelemzes import prisma as P

TEMPLATE = os.path.join(ROOT, "tudasbazis", "sablonok", "prisma_folyamat_sablon.md")


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:      # argparse-hiba
            code = exc.code
    return code, buf.getvalue(), err.getvalue()


def codes(res, severity=None):
    return [f["code"] for f in res.findings if severity is None or f["severity"] == severity]


def fill_template(values, extra_rows=()):
    """A projekt prisma_folyamat_sablon.md-je kitöltve: {a Lépés-cella részlete: Szám}."""
    with open(TEMPLATE, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out = []
    for line in lines:
        if line.startswith("|"):
            parts = line.split("|")
            for key, val in values.items():
                if key in parts[1]:
                    parts[2] = " %s " % val
                    line = "|".join(parts)
                    break
        out.append(line)
        if line.startswith("| Egyéb forrásból"):
            out += ["| %s | %s | |" % row for row in extra_rows]
    return "\n".join(out) + "\n"


# konzisztens folyamat a sablon soraival (a KBP-01 / KBP-02 megállapítás számai)
BASE = {"(n = A1)": "1250", "(n = A2)": "40", "egyéb (D3)": "310 / 0 / 0", "(n = B)": "980", "(n = C)": "900",
        "(n = E)": "80", "(n = F)": "2", "(n = G)": "78", "(n = H": "60 (rossz populáció: 40; nincs kontroll: 20)",
        "(n = I)": "18 / 15"}


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, rel, text):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def md_check(self, text):
        return run_cli("prisma", "check", "--md", self.write("flow.md", text))


# ================================================================ PRISMA: lehetetlen levezetett dobozok
class TestNegativeDerivedBoxes(_Tmp):
    """stats_new:R2-NF-01, kb_code:KBP-04 — a negatívra adódó levezetett doboz hiba, nem P011."""

    MAIN = {"identified_databases": 100, "duplicates_removed": 10, "screened": 90, "excluded_screening": 60,
            "sought": 30, "not_retrieved": 2, "assessed": 28}

    def assert_no_negative(self, res):
        self.assertFalse([k for k, v in res.derived.items() if isinstance(v, int) and v < 0], res.derived)

    def test_NF_01_excluded_more_than_screened(self):
        res = P.check_flow({"identified_databases": 100, "duplicates_removed": 10, "screened": 90,
                            "excluded_screening": 95})
        self.assertFalse(res.ok)
        self.assertIn("P003", codes(res, "error"))
        self.assertNotIn("sought", res.derived)
        self.assert_no_negative(res)
        code, so, se = run_cli("prisma", "check", "--A1", "100", "--D1", "10", "--B", "90", "--C", "95")
        self.assertEqual(code, 1, so + se)
        self.assertIn("Kizárt rekordok (C) = 95 > Szűrt rekordok (B) = 90", so)

    def test_NF_01_excluded_reports_more_than_assessed_without_J(self):
        res = P.check_flow(dict(self.MAIN, excluded_eligibility=30,
                                excluded_eligibility_reasons={"wrong population": 18, "wrong outcome": 12}))
        self.assertFalse(res.ok)
        self.assertEqual(codes(res, "error"), ["P005"])
        self.assertNotIn("included_reports", res.derived)
        self.assert_no_negative(res)

    def test_NF_01_not_retrieved_more_than_sought_without_G(self):
        res = P.check_flow({"identified_databases": 100, "duplicates_removed": 10, "screened": 90,
                            "excluded_screening": 60, "sought": 30, "not_retrieved": 40, "excluded_eligibility": 0})
        self.assertFalse(res.ok)
        self.assertIn("P004", codes(res, "error"))
        self.assert_no_negative(res)

    def test_NF_01_other_branch_cannot_cancel_main_reports_even_when_filled(self):
        flow = dict(self.MAIN, excluded_eligibility=20, excluded_eligibility_reasons={"x": 20},
                    other_methods_identified=6, other_methods_sought=5, other_methods_assessed=5,
                    other_methods_excluded=9, other_methods_excluded_reasons={"y": 9},
                    included_reports=4, included_studies=4)
        res = P.check_flow(flow)
        self.assertFalse(res.ok)
        self.assertIn("P005", codes(res, "error"))
        self.assertNotIn("other_methods_included_reports", res.derived)

    def test_NF_01_prisma2009_excluded_more_than_screened(self):
        res = P.check_flow({"identified_databases": 100, "duplicates_removed": 10, "screened": 90,
                            "excluded_screening": 95}, "PRISMA2009")
        self.assertFalse(res.ok)
        self.assertIn("P003", codes(res, "error"))
        self.assert_no_negative(res)

    def test_KBP_04_cli_H_greater_than_G_exit_1(self):
        code, so, se = run_cli("prisma", "check", "--A1", "100", "--D1", "10", "--B", "90", "--C", "80", "--E", "10",
                               "--F", "0", "--G", "10", "--H", "12", "--reasons", "a: 12")
        self.assertEqual(code, 1, so + se)
        self.assertIn("HIBÁS", so)
        self.assertIn("Kizárt jelentések (H) = 12 > Értékelt jelentések (G) = 10", so)

    def test_KBP_04_prisma2009_duplicates_more_than_identified(self):
        res = P.check_flow({"records_identified_database_searching": 50, "duplicates_removed": 60}, "PRISMA2009")
        self.assertFalse(res.ok)
        self.assertEqual(codes(res, "error"), ["P002"])
        self.assertNotIn("after_duplicates", res.derived)
        self.assertNotIn("screened", res.derived)

    def test_KBP_04_valid_derivations_unchanged(self):
        res = P.check_flow(dict(self.MAIN, excluded_eligibility=20, excluded_eligibility_reasons={"x": 20}))
        self.assertTrue(res.ok)
        self.assertEqual(res.derived["included_reports"], 8)


# ================================================================ PRISMA: ezres tagolás a Markdown-táblázatban
class TestThousandsSeparators(_Tmp):
    """robustness:R2-ROB-03, kb_code:KBP-01, agents_e2e:AG-01"""

    SEPARATORS = (" ", " ", " ", " ", ".", ",")

    def test_ROB_03_every_separator_parses(self):
        for sep in self.SEPARATORS:
            md = ("| Azonosított rekordok — adatbázisok (n = A1) | 12%s345 | |\n"
                  "| Szűrés előtt eltávolítva: duplikátumok (D1), automatikusan kizárt (D2), egyéb (D3) | 1%s345 / 0 / 0 | |\n"
                  "| Szűrt rekordok (n = B) | 11%s000 | |\n| Kizárt rekordok (cím/absztrakt) (n = C) | 10%s900 | |\n"
                  % (sep, sep, sep, sep))
            flow = P.parse_markdown_table(md)
            self.assertEqual(flow, {"identified_databases": 12345, "duplicates_removed": 1345, "automation_removed": 0,
                                    "other_removed": 0, "screened": 11000, "excluded_screening": 10900}, repr(sep))

    def test_ROB_03_grouped_flow_passes_and_wrong_flow_fails(self):
        for sep in self.SEPARATORS:
            vals = dict(BASE, **{"(n = A1)": "2%s345" % sep, "(n = A2)": "0", "egyéb (D3)": "345 / 0 / 0",
                                 "(n = B)": "2%s000" % sep,
                                 "(n = C)": "1%s900" % sep, "(n = E)": "100", "(n = F)": "0", "(n = G)": "100",
                                 "(n = H": "90 (a: 50; b: 40)", "(n = I)": "10 / 8"})
            code, so, se = self.md_check(fill_template(vals))
            self.assertEqual(code, 0, repr(sep) + so + se)
            self.assertIn("identified_total = 2345", so)
        # AG-01: a vesszős tagolású, de hibás B (9,999 ≠ 1 430) már nem tűnik el csendben
        vals = dict(BASE, **{"(n = A1)": "1,500", "egyéb (D3)": "100 / 0 / 10", "(n = B)": "9,999",
                             "(n = C)": "1,380", "(n = E)": "50", "(n = G)": "48",
                             "(n = H": "35 (wrong design: 20; wrong outcome: 15)", "(n = I)": "13 / 13"})
        code, so, se = self.md_check(fill_template(vals))
        self.assertEqual(code, 1, so + se)
        self.assertIn("Szűrt rekordok (B) = 9999", so)

    def test_KBP_01_grouped_D_H_and_JI_rows(self):
        md = ("| Szűrés előtt eltávolítva: duplikátumok (D1), automatikusan kizárt (D2), egyéb (D3) | 1 010 / 0 / 0 | |\n"
              "| Kizárt teljes szöveg okokkal (n = H = H1 + H2 + …) | 1 060 (rossz populáció: 1 040; nincs kontroll: 20) | |\n"
              "| Bevont közlemények (n = J) és vizsgálatok (n = I) | 1 018 / 1 015 | |\n")
        self.assertEqual(P.parse_markdown_table(md), {
            "duplicates_removed": 1010, "automation_removed": 0, "other_removed": 0, "excluded_eligibility": 1060,
            "excluded_eligibility_reasons": {"rossz populáció": 1040, "nincs kontroll": 20},
            "included_reports": 1018, "included_studies": 1015})
        code, so, se = self.md_check(fill_template(dict(BASE, **{"(n = A1)": "1 250"})))
        self.assertEqual(code, 0, so + se)

    def test_AG_01_unreadable_filled_box_is_error_not_skipped(self):
        code, so, se = self.md_check(fill_template(dict(BASE, **{"(n = B)": "2.5"})))
        self.assertEqual(code, 1, so + se)
        self.assertIn("[P001]", so)
        self.assertIn("Szűrt rekordok (B)", so)
        # a gondolatjel / n/a kitöltetlennek számít
        flow = P.parse_markdown_table("| Azonosított rekordok — regiszterek (n = A2) | – |  |\n"
                                      "| Szűrt rekordok (n = B) | n/a | |\n")
        self.assertEqual(flow, {})


# ================================================================ PRISMA: egyéb módszerek ága a sablonban
class TestOtherMethodsBranch(_Tmp):
    """kb_code:KBP-02"""

    def test_KBP_02_template_rule_J_includes_other_source_reports(self):
        vals = dict(BASE, **{"(n = I)": "21 / 17", "metaanalízisben": "15", "külön ág": "5"})
        code, so, se = self.md_check(fill_template(vals))
        self.assertEqual(code, 0, so + se)
        self.assertNotIn("[P005]", so)
        self.assertIn("J többlete (21 − 18 = 3)", so)

    def test_KBP_02_branch_rows_verify_J(self):
        rows = (("Egyéb ág: teljes szövegre keresett", "4"), ("Egyéb ág: nem elérhető teljes szöveg", "0"),
                ("Egyéb ág: teljes szövegben értékelt", "4"), ("Egyéb ág: kizárt okokkal", "1 (nem releváns: 1)"))
        text = fill_template(dict(BASE, **{"(n = I)": "21 / 17", "külön ág": "5"}), rows)
        flow = P.parse_markdown_table(text)
        self.assertEqual((flow["other_methods_identified"], flow["other_methods_sought"],
                          flow["other_methods_not_retrieved"], flow["other_methods_assessed"],
                          flow["other_methods_excluded"], flow["other_methods_excluded_reasons"]),
                         (5, 4, 0, 4, 1, {"nem releváns": 1}))
        res = P.check_flow(flow)
        self.assertTrue(res.ok, res.findings)
        self.assertEqual(res.derived["other_methods_included_reports"], 3)
        self.assertNotIn("P012", codes(res))
        # a kitöltött ág ellenőrzi is a J-t
        bad = P.check_flow(dict(flow, included_reports=22))
        self.assertIn("P005", codes(bad, "error"))

    def test_KBP_02_excess_larger_than_branch_is_still_error(self):
        vals = dict(BASE, **{"(n = I)": "25 / 17", "külön ág": "5"})
        code, so, se = self.md_check(fill_template(vals))
        self.assertEqual(code, 1, so + se)
        self.assertIn("[P005]", so)


# ================================================================ PRISMA: ok nélküli kizárások
class TestNoReasonExclusions(_Tmp):
    """kb_code:KBP-09"""

    COMPOSER = {"identified_databases": 100, "identified_registers": 0, "identified_other": 0, "dedup_removed": 10,
                "removed_before_screening_n": 0, "screened": 90, "excluded_screening": 70,
                "sought_for_retrieval": 20, "not_retrieved": 0, "assessed_eligibility": 20,
                "excluded_eligibility": 15, "included": 5, "undecided": 0, "retrieval_gap": 0}

    def p008(self, reasons):
        res = P.check_flow(P.from_composer(dict(self.COMPOSER, excluded_eligibility_reasons=reasons)))
        return [f["detail"] for f in res.findings if f["code"] == "P008"], res

    def test_KBP_09_placeholder_reasons_raise_P008(self):
        for reasons, missing in (({"ok nélkül": 15}, 15), ({"ok nélkül": 10, "wrong population": 5}, 10),
                                 ([{"count": 15}], 15), ({"unspecified": 15}, 15)):
            details, res = self.p008(reasons)
            self.assertTrue(res.ok)
            self.assertEqual(details, ["Kizárt jelentések (H) = 15, ebből %d kizárás ok nélkül" % missing],
                             reasons)
        details, _ = self.p008({"wrong population": 10, "wrong outcome": 5})
        self.assertEqual(details, [])

    def test_KBP_09_cli_composer(self):
        p = self.write("prisma-flow.json", json.dumps(dict(self.COMPOSER, excluded_eligibility_reasons={"ok nélkül": 15}),
                                                      ensure_ascii=False))
        code, so, se = run_cli("prisma", "check", "--composer", p)
        self.assertEqual(code, 0, so + se)
        self.assertIn("[P008] warning", so)
        self.assertIn("ebből 15 kizárás ok nélkül", so)


# ================================================================ PRISMA: kisbetűs J/I, „H = H1 + H2” jelölés
class TestMarkdownTags(_Tmp):
    """kb_code:KBP-10"""

    def test_KBP_10_lowercase_tags(self):
        row = "| Bevont közlemények (n = J) és vizsgálatok (n = I) | %s | |"
        for cell in ("i=15; j=18", "J=18; i=15", "I: 15, J: 18", "J=18; 15"):
            self.assertEqual(P.parse_markdown_table(row % cell), {"included_reports": 18, "included_studies": 15},
                             cell)
        code, so, se = self.md_check(fill_template(dict(BASE, **{"(n = I)": "i=15; j=18"})))
        self.assertEqual(code, 0, so + se)

    def test_KBP_10_sum_notation_is_not_a_reason(self):
        flow = P.parse_markdown_table("| Kizárt teljes szöveg okokkal (n = H = H1 + H2 + …) | 60 = 40 + 20 | |")
        self.assertEqual(flow, {"excluded_eligibility": 60})
        code, so, se = self.md_check(fill_template(dict(BASE, **{"(n = H": "60 = 40 + 20"})))
        self.assertEqual(code, 0, so + se)
        self.assertNotIn("[P007]", so)
        self.assertIn("[P008]", so)


# ================================================================ projektnapló: blocker lezárása
class TestBlockerClosure(_Tmp):
    """kb_code:KBP-05, agents_e2e:AG-02"""

    def setUp(self):
        super().setUp()
        self.p = os.path.join(self.tmp, "proj")
        projekt.init(self.p, "T")
        self.fid = projekt.add_finding(self.p, "reviewer", "blocker", "Hatásirány fordított", stage="S05",
                                       check_kb=False)

    def test_KBP_05_blocker_cannot_be_closed_as_wontfix(self):
        with self.assertRaises(ValueError) as cm:
            projekt.resolve_finding(self.p, self.fid, "wontfix", "nem érdekes")
        self.assertIn("fixed vagy indokolt invalid", str(cm.exception))
        self.assertEqual(projekt.get_item(self.p, "finding", self.fid)["status"], "open")
        with self.assertRaises(ValueError):
            projekt.checkpoint(self.p, "S05", "reviewer", "PASS")
        code, so, se = run_cli("project", "resolve", self.p, str(self.fid), "--status", "wontfix",
                               "--resolution", "nincs idő")
        self.assertEqual(code, 1, so + se)
        # major továbbra is lezárható wontfix-szel; blocker invalidként csak indoklással
        mid = projekt.add_finding(self.p, "reviewer", "major", "Egger k<10", stage="S11", check_kb=False)
        projekt.resolve_finding(self.p, mid, "wontfix", "k = 6, a teszt nem értelmezhető")
        with self.assertRaises(ValueError):
            projekt.resolve_finding(self.p, self.fid, "invalid", "  ")

    def test_KBP_05_legacy_wontfix_blocker_still_gates_and_warns(self):
        con = sqlite3.connect(projekt.db_path(self.p))
        con.execute("UPDATE finding SET status='wontfix', resolution='régi napló' WHERE id=?", (self.fid,))
        con.commit()
        con.close()
        for stage in ("S05", "FINAL"):
            with self.assertRaises(ValueError) as cm:
                projekt.checkpoint(self.p, stage, "reviewer", "PASS")
            self.assertIn("wontfix", str(cm.exception))
        self.assertTrue(any("Wontfix-szel lezárt blocker" in w for w in projekt.status(self.p)["warnings"]))
        projekt.resolve_finding(self.p, self.fid, "fixed", "javítva, forrás ellenőrizve")
        projekt.checkpoint(self.p, "FINAL", "reviewer", "PASS")

    def test_AG_02_finding_can_be_reopened(self):
        projekt.resolve_finding(self.p, self.fid, "fixed", "előjel javítva")
        projekt.checkpoint(self.p, "S05", "reviewer", "PASS")
        projekt.resolve_finding(self.p, self.fid, "open", "a javítás nem teljes (2. vizsgálat)")
        f = projekt.get_item(self.p, "finding", self.fid)
        self.assertEqual(f["status"], "open")
        self.assertIsNone(f["resolved_ts"])
        self.assertIn("előjel javítva", f["resolution"])
        self.assertIn("a javítás nem teljes", f["resolution"])
        with self.assertRaises(ValueError):
            projekt.checkpoint(self.p, "S05", "reviewer", "PASS")
        with self.assertRaises(ValueError):
            projekt.resolve_finding(self.p, self.fid, "open", "már nyitott")
        with self.assertRaises(ValueError):
            projekt.resolve_finding(self.p, self.fid, "closed", "?")


# ================================================================ projektnapló: GRADE-összhang
class TestGradeConsistency(_Tmp):
    """kb_code:KBP-12"""

    ZERO = dict(risk_of_bias="0", inconsistency="0", indirectness="0", imprecision="0", publication_bias="0")

    def test_KBP_12_all_signed_only_two_start_levels(self):
        g = projekt.grade_consistency
        msg = g("moderate", upgrades="0", **self.ZERO)
        self.assertIn("'high' (RCT/ROBINS-I kiindulás) vagy 'low' (megfigyeléses kiindulás)", msg)
        self.assertIn("'moderate' (RCT/ROBINS-I kiindulás) vagy 'very low'", g("low", **dict(self.ZERO, risk_of_bias="-1")))
        for ok in ("high", "low"):
            self.assertIsNone(g(ok, **self.ZERO))
        self.assertIsNone(g("moderate", **dict(self.ZERO, risk_of_bias="-1")))
        self.assertIsNone(g("moderate", **dict(self.ZERO, upgrades="+1")))      # megfigyeléses + 1
        p = os.path.join(self.tmp, "proj")
        projekt.init(p, "T")
        code, so, se = run_cli("project", "grade", p, "--outcome", "O", "--certainty", "moderate", "--rob", "0",
                               "--inconsistency", "0", "--indirectness", "0", "--imprecision", "0",
                               "--publication-bias", "0")
        self.assertEqual(code, 0, so + se)
        self.assertIn("nem egyeztethető össze", se)

    def test_KBP_12_free_text_domains_do_not_bound_from_below(self):
        g = projekt.grade_consistency
        self.assertIsNone(g("very low", risk_of_bias="0", inconsistency="súlyos", imprecision="nagyon súlyos"))
        self.assertIsNone(g("moderate", risk_of_bias="-1", inconsistency="-1", upgrades="nagy hatás"))
        # a felső korlát továbbra is érvényes
        self.assertIn("csak 'very low'", g("high", risk_of_bias="-2", imprecision="-1", inconsistency="súlyos"))

    def test_KBP_12_negative_upgrade_is_not_abs(self):
        msg = projekt.grade_consistency("low", upgrades="-1")
        self.assertIn("nem lehet negatív", msg)
        self.assertNotIn("+1", msg)


# ================================================================ tudásbázis: betöltés
class _KbTmp(_Tmp):
    def setUp(self):
        super().setUp()
        self.db = os.path.join(self.tmp, "kb.sqlite")
        kb.build(self.db)

    def hits(self, q):
        return [h["source_id"] for h in kb.search(q, db=self.db, scopes=("chunk",)).get("chunk", [])]

    def chunk_rows(self, sid):
        return kb.query("SELECT chunk_id, seq, text FROM chunk WHERE source_id=? ORDER BY seq", (sid,), db=self.db)[1]


class TestIngestSameBasename(_KbTmp):
    """kb_code:KBP-06"""

    def test_KBP_06_same_basename_other_folder_gets_own_source(self):
        kb.ingest(self.write("a/fulltext.txt", "Alpha zebraword text.\n"), db=self.db)
        res = kb.ingest(self.write("b/fulltext.txt", "Beta giraffeword text.\n"), db=self.db)
        self.assertNotEqual(res[0][0], "fulltext")
        self.assertIn("FIGYELEM", res[0][2])
        self.assertEqual(self.hits("zebraword"), ["fulltext"])
        self.assertEqual(self.hits("giraffeword"), [res[0][0]])

    def test_KBP_06_reused_path_replacement_is_reported(self):
        p = self.write("dl/download.txt", "Okapi first document\n")
        kb.ingest(p, db=self.db)
        res = kb.ingest(self.write("dl/download.txt", "Entirely different giraffe paper\n"), db=self.db)
        self.assertEqual(res[0][0], "download")
        self.assertIn("korábbi változat felülírva: download.txt", res[0][2])
        self.assertEqual(kb.show("download", db=self.db)["notes"], "sha256=%s" % kb._sha256(p))


class TestIngestMultiFileSource(_KbTmp):
    """kb_code:KBP-07"""

    def setUp(self):
        super().setUp()
        self.write("d/book/part1.txt", "Part one kangarooword\n")
        self.write("d/book/part2.txt", "Part two platypusword\n")
        kb.ingest(os.path.join(self.tmp, "d", "book"), source_id="mybook", citation="My Book 2020", db=self.db)
        self.before = self.chunk_rows("mybook")

    def test_KBP_07_plain_reingest_of_parent_keeps_source(self):
        res = kb.ingest(os.path.join(self.tmp, "d"), db=self.db)
        self.assertEqual([r[0] for r in res], ["mybook", "mybook"])
        self.assertEqual(self.hits("platypusword"), ["mybook"])
        self.assertEqual(self.chunk_rows("mybook"), self.before)          # változatlan: azonosítók is
        self.assertEqual(kb.show("mybook", db=self.db)["citation"], "My Book 2020")
        self.assertIsNone(kb.show("part2", db=self.db))

    def test_KBP_07_single_file_reingest_keeps_other_parts(self):
        kb.ingest(os.path.join(self.tmp, "d", "book", "part1.txt"), db=self.db)
        self.assertEqual(self.hits("platypusword"), ["mybook"])
        self.assertEqual(self.chunk_rows("mybook"), self.before)

    def test_KBP_07_changed_part_replaces_only_itself(self):
        self.write("d/book/part1.txt", "Part one revised wombatword\n")
        res = kb.ingest(os.path.join(self.tmp, "d"), db=self.db)
        self.assertEqual([r[0] for r in res], ["mybook", "mybook"])
        self.assertEqual(self.hits("wombatword"), ["mybook"])
        self.assertEqual(self.hits("kangarooword"), [])
        self.assertEqual(self.hits("platypusword"), ["mybook"])
        self.assertEqual(len(self.chunk_rows("mybook")), 2)


# ================================================================ stabil szövegrész-hivatkozás
class TestStableChunkRefs(_KbTmp):
    """agents_e2e:AG-10"""

    def test_AG_10_chunk_ref_stored_in_stable_form(self):
        kb.ingest(self.write("src/okapi.txt", "Okapi passage about prediction intervals.\n"), db=self.db)
        kb.ingest(self.write("src/zebra.txt", "Zebra passage.\n"), db=self.db)
        hit = kb.search("okapi", db=self.db, scopes=("chunk",))["chunk"][0]
        self.assertEqual(hit["ref"], "okapi#0")
        p = os.path.join(self.tmp, "proj")
        projekt.init(p, "T")
        warns = []
        did = projekt.log_decision(p, "planner", "PI közlése", stage="S08", kb_refs="#%d" % hit["id"],
                                   kb_db=self.db, strict=True, warnings=warns)
        self.assertEqual(projekt.get_item(p, "decision", did)["kb_refs"], "okapi#0")
        self.assertTrue(any("okapi#0" in w for w in warns))
        # a forrás változik és újratöltődik: a régi chunk_id mást jelöl, a stabil hivatkozás nem
        self.write("src/okapi.txt", "Okapi passage about prediction intervals, revised.\n")
        kb.ingest(os.path.join(self.tmp, "src"), db=self.db)
        item = kb.show("okapi#0", db=self.db)
        self.assertEqual((item["source_id"], item["seq"]), ("okapi", 0))
        self.assertIn("revised", item["text"])
        self.assertEqual(kb.existing_ids(["okapi#0", "okapi#7"], self.db), {"okapi#0"})

    def test_AG_10_legacy_numeric_ref_warned_in_status(self):
        p = os.path.join(self.tmp, "proj")
        projekt.init(p, "T")
        projekt.log_decision(p, "planner", "x", kb_refs="#1340", check_kb=False)
        self.assertTrue(any("Instabil szövegrész-hivatkozás" in w and "#1340" in w
                            for w in projekt.status(p)["warnings"]))


# ================================================================ ellenőrizetlen KB-azonosító a záráskor
class TestUnverifiedRefsAtFinal(_KbTmp):
    """agents_e2e:AG-14 (a kódoldali rész; az ágens-sablonok --strict-je más fájlokban)"""

    def test_AG_14_final_pass_warns_about_unverified_refs(self):
        p = os.path.join(self.tmp, "proj")
        projekt.init(p, "T")
        projekt.log_decision(p, "planner", "fake", stage="S08", kb_refs="D-S08-999", kb_db=self.db, warnings=[])
        warns = []
        projekt.checkpoint(p, "FINAL", "reviewer", "PASS", warnings=warns)
        self.assertTrue(any("ellenőrizetlen KB-hivatkozás" in w and "D-S08-999" in w for w in warns), warns)
        code, so, se = run_cli("project", "checkpoint", p, "--stage", "FINAL", "--agent", "reviewer",
                               "--verdict", "PASS")
        self.assertEqual(code, 0, so + se)
        self.assertIn("D-S08-999", se)


if __name__ == "__main__":
    unittest.main()
