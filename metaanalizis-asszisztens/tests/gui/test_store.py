# -*- coding: utf-8 -*-
"""ma_gui.store: CSV kerek-út (bájtazonosság), formátum-felismerés, row_uid, ETag-ütközés
cellaszintű diffel, atomikus írás, Excel-zárolás, útvédelem, oldalfájlok, változásfigyelő."""
import ast
import codecs
import glob
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ma_gui import store  # noqa: E402
from metaelemzes import tableio  # noqa: E402

PELDAK = sorted(glob.glob(os.path.join(ROOT, "peldak", "*.csv")))

HU_EXCEL = ("\ufeffSzerző;Év;n1;átlag1;sd1;n2;átlag2;sd2\r\n"
            "Kovács 2019;2019;40;12,5;3,1;38;11,9;2,8\r\n"
            "Tóth 2021;2021;55;13,1;2,9;51;12,0;3,4\r\n"
            "Szabó 2022;2022;61;12,2;3,3;60;12,8;3,0\r\n").encode("utf-8")

SYNTHETIC = {
    "hu_excel_bom_crlf_semicolon_comma.csv": HU_EXCEL,
    "quoted_newline.csv": ('study,note,e1,n1\r\n"Smith, J","line1\r\nline2",4,10\r\n'
                           'Doe,"he said ""hi""",5,12\r\n').encode("utf-8"),
    "quoted_lf_in_crlf_file.csv": 'study;megj;n\r\nA;"egy\nkettő";3\r\nB;x;4\r\n'.encode("utf-8"),
    "cp1250.csv": "Vizsgálat;Őrzött;n\r\nŐry Ödön;1,5;20\r\nÉva Ügy;2,25;30\r\n".encode("cp1250"),
    "utf16_tab_bom.txt.csv": ("\ufeffstudy\te1\tn1\te2\tn2\r\nA\t1\t20\t3\t21\r\nB\t2\t30\t4\t31\r\n"
                              ).encode("utf-16-le"),
    "no_final_newline.csv": b"study;n\nA;1\nB;2",
    "blank_lines.csv": b"\n\nstudy;n\nA;1\n\n;\nB;2\n\n\n",
    "quote_all.csv": b'"study","n1","e1"\n"A","10","2"\n"B","12","3"\n',
    "mixed_newlines.csv": b"a;b\r\nA;1\nB;2\r\nC;3\n",
    "ragged_short.csv": b"study;e1;n1\nA;1\nB;2;3;4\nC;5;6\n",
    "utf8_nobom_accents.csv": "vizsgálat,év,r,n\nÁrpád 2001a,2001a,0.31,40\nŐze 2003,2003,-0.12,55\n".encode("utf-8"),
    "trailing_delims.csv": b"study;n;\nA;1;\nB;2;\n",
}


def rows_of(table):
    return [{"row_uid": r["row_uid"], "cells": list(r["cells"])} for r in table.rows]


def engine_rows(path):
    rows, meta = tableio.read_table(str(path))
    return [{k: v for k, v in r.items() if k not in ("_line", "row_uid")} for r in rows], meta


class _ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ma_gui_store_")
        self.root = Path(self.tmp) / "projekt"
        (self.root / "03_adatok").mkdir(parents=True)
        self.ps = store.ProjectStore(self.root)
        self._delays = store._REPLACE_RETRY_DELAYS
        store._REPLACE_RETRY_DELAYS = (0, 0)

    def tearDown(self):
        store._REPLACE_RETRY_DELAYS = self._delays
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, data):
        p = self.root / "03_adatok" / name
        p.write_bytes(data)
        return "03_adatok/" + name

    def get(self, rel):
        return (self.root / rel).read_bytes()

    def leftovers(self):
        return [str(p) for p in self.root.rglob("*") if p.name.endswith(".tmp")]

    def resave(self, rel, write_uids=False, mutate=None):
        t = self.ps.load_table(rel)
        rows = rows_of(t)
        header = list(t.header)
        if mutate:
            header, rows = mutate(header, rows)
        return t, self.ps.save_table(rel, header, rows, t.etag, write_uids=write_uids)


class RoundTripTests(_ProjectCase):
    def test_peldak_byte_identical(self):
        self.assertTrue(PELDAK, "nincsenek példafájlok")
        for src in PELDAK:
            with self.subTest(src=os.path.basename(src)):
                raw = Path(src).read_bytes()
                rel = self.put(os.path.basename(src), raw)
                before, after = self.resave(rel)
                self.assertEqual(self.get(rel), raw)
                self.assertEqual(after.etag, before.etag)
                self.assertEqual(after.row_uids, before.row_uids)

    def test_synthetic_byte_identical(self):
        for name, raw in SYNTHETIC.items():
            with self.subTest(name=name):
                rel = self.put(name, raw)
                self.resave(rel)
                self.assertEqual(self.get(rel), raw)

    def test_uid_column_write_keeps_engine_view_and_is_idempotent(self):
        for name, raw in list(SYNTHETIC.items()) + [(os.path.basename(p), Path(p).read_bytes()) for p in PELDAK]:
            with self.subTest(name=name):
                rel = self.put(name, raw)
                want, meta0 = engine_rows(self.root / rel)
                t0, t1 = self.resave(rel, write_uids=True)
                self.assertTrue(t1.uid_column)
                self.assertEqual(t1.row_uids, t0.row_uids)        # a származtatott uid-ok rögzülnek
                self.assertEqual(t1.uid_issues, [])
                got, meta1 = engine_rows(self.root / rel)
                self.assertEqual(meta1["n_rows"], meta0["n_rows"])
                self.assertEqual(meta1["encoding"], meta0["encoding"])
                self.assertEqual(meta1["delimiter"], meta0["delimiter"])
                for a, b in zip(want, got):
                    self.assertEqual(set(a), set(b))
                    for k in a:
                        if a[k] == a[k]:                          # NaN kivételével
                            self.assertEqual(a[k], b[k], k)
                once = self.get(rel)
                self.resave(rel, write_uids=True)
                self.assertEqual(self.get(rel), once)              # második mentés: bájtra azonos

    def test_format_detection(self):
        rel = self.put("hu.csv", HU_EXCEL)
        f = self.ps.load_table(rel).fmt.to_json()
        self.assertEqual(f, {"encoding": "utf-8", "bom": True, "delimiter": ";", "decimal_mark": ",",
                             "newline": "crlf", "final_newline": True, "quoting": "minimal",
                             "header_quoting": "minimal"})
        cases = {"cp1250.csv": ("cp1250", False, ";"), "utf16_tab_bom.txt.csv": ("utf-16-le", True, "\t"),
                 "quote_all.csv": ("utf-8", False, ",")}
        for name, (enc, bom, delim) in cases.items():
            t = self.ps.load_table(self.put(name, SYNTHETIC[name]))
            self.assertEqual((t.fmt.encoding, t.fmt.bom, t.fmt.delimiter), (enc, bom, delim), name)
        qa = self.ps.load_table("03_adatok/quote_all.csv").fmt
        self.assertEqual((qa.quoting, qa.header_quoting), ("all", "all"))
        nf = self.ps.load_table(self.put("nf.csv", SYNTHETIC["no_final_newline.csv"])).fmt
        self.assertEqual((nf.final_newline, nf.newline), (False, "\n"))
        ml = self.ps.load_table(self.put("molloy.csv", Path(ROOT, "peldak", "molloy2014_korrelacio.csv").read_bytes()))
        self.assertEqual(ml.fmt.quoting, "strings")

    def test_engine_parity_on_peldak(self):
        for src in PELDAK:
            with self.subTest(src=os.path.basename(src)):
                t = self.ps.load_table(self.put(os.path.basename(src), Path(src).read_bytes()))
                _, meta = engine_rows(src)
                self.assertEqual(t.header, meta["columns"])
                self.assertEqual(len(t.rows), meta["n_rows"])
                self.assertEqual(t.fmt.delimiter, meta["delimiter"])
                self.assertEqual(t.fmt.decimal_mark, meta["decimal_mark"])
                self.assertEqual([r["line"] for r in t.rows], [r["_line"] for r in tableio.read_table(src)[0]])

    def test_quoted_newline_cells_are_raw_text(self):
        t = self.ps.load_table(self.put("q.csv", SYNTHETIC["quoted_newline.csv"]))
        self.assertEqual(t.header, ["study", "note", "e1", "n1"])
        self.assertEqual(t.rows[0]["cells"], ["Smith, J", "line1\r\nline2", "4", "10"])
        self.assertEqual(t.rows[1]["cells"], ["Doe", 'he said "hi"', "5", "12"])
        self.assertEqual([r["line"] for r in t.rows], [2, 4])

    def test_modified_cell_changes_only_that_line(self):
        rel = self.put("hu.csv", HU_EXCEL)

        def edit(header, rows):
            rows[0]["cells"][4] = "3,2"
            return header, rows

        self.resave(rel, mutate=edit)
        old, new = HU_EXCEL.split(b"\r\n"), self.get(rel).split(b"\r\n")
        self.assertTrue(self.get(rel).startswith(codecs.BOM_UTF8))
        self.assertEqual(len(old), len(new))
        self.assertEqual([i for i, (a, b) in enumerate(zip(old, new)) if a != b], [1])
        self.assertEqual(new[1].decode("utf-8"), "Kovács 2019;2019;40;12,5;3,2;38;11,9;2,8")

    def test_modified_row_uses_file_quoting_style(self):
        src = Path(ROOT, "peldak", "pritz1997_arany.csv").read_bytes()
        rel = self.put("pritz.csv", src)

        def edit(header, rows):
            rows[0]["cells"][1] = "15"
            rows.append({"row_uid": None, "cells": ["Új, szerző", "1", "2"]})
            return header, rows

        self.resave(rel, mutate=edit)
        lines = self.get(rel).decode("utf-8").split("\n")
        self.assertEqual(lines[1], '"Giannotta et al. ",15,17')
        self.assertEqual(lines[2], src.decode("utf-8").split("\n")[2])
        self.assertIn('"Új, szerző",1,2', lines)

    def test_new_row_uid_and_newline_glue(self):
        rel = self.put("nf.csv", SYNTHETIC["no_final_newline.csv"])
        t = self.ps.load_table(rel)
        rows = rows_of(t) + [{"row_uid": None, "cells": ["C", "3"]}]
        t2 = self.ps.save_table(rel, t.header, rows, t.etag)
        self.assertEqual(self.get(rel).decode("ascii").split("\n")[-1].split(";")[:2], ["C", "3"])
        self.assertFalse(self.get(rel).endswith(b"\n"))           # záró újsor nélküli stílus marad
        new_uid = t2.row_uids[-1]
        self.assertRegex(new_uid, r"^r[a-z2-7]{6}$")
        self.assertNotIn(new_uid, t.row_uids)
        self.assertNotEqual(new_uid, store.derive_row_uid("C", 2))
        self.assertEqual(t2.row_uids[:2], t.row_uids)

    def test_delete_and_reorder_keep_raw_records(self):
        rel = self.put("q.csv", SYNTHETIC["quoted_newline.csv"])

        def swap(header, rows):
            return header, [rows[1], rows[0]]

        self.resave(rel, mutate=swap)
        self.assertEqual(self.get(rel), b'study,note,e1,n1\r\nDoe,"he said ""hi""",5,12\r\n'
                                        b'"Smith, J","line1\r\nline2",4,10\r\n')

        def drop_first(header, rows):
            return header, rows[1:]

        self.resave(rel, mutate=drop_first)
        self.assertEqual(self.get(rel), b'study,note,e1,n1\r\n"Smith, J","line1\r\nline2",4,10\r\n')

    def test_rows_as_plain_lists_and_trailing_empty_padding(self):
        rel = self.put("short.csv", SYNTHETIC["ragged_short.csv"])
        t = self.ps.load_table(rel)
        padded = [r["cells"] + [""] * (len(t.header) - len(r["cells"])) for r in t.rows]
        rows = [{"row_uid": r["row_uid"], "cells": c} for r, c in zip(t.rows, padded)]
        self.ps.save_table(rel, t.header, rows, t.etag, write_uids=False)
        self.assertEqual(self.get(rel), SYNTHETIC["ragged_short.csv"])   # a kitöltés nem módosítás

    def test_encoding_error_names_column_not_value(self):
        rel = self.put("cp.csv", SYNTHETIC["cp1250.csv"])
        t = self.ps.load_table(rel)
        rows = rows_of(t)
        rows[0]["cells"][1] = "τ²=0,04"
        with self.assertRaises(store.Invalid) as cm:
            self.ps.save_table(rel, t.header, rows, t.etag)
        e = cm.exception
        self.assertEqual((e.code, e.http), ("VALIDATION", 422))
        self.assertEqual(e.details["column"], "Őrzött")
        self.assertEqual(e.details["row_uid"], t.rows[0]["row_uid"])
        self.assertNotIn("τ", e.message)
        self.assertEqual(self.get(rel), SYNTHETIC["cp1250.csv"])

    def test_non_string_cells_rejected(self):
        rel = self.put("hu.csv", HU_EXCEL)
        t = self.ps.load_table(rel)
        rows = rows_of(t)
        rows[0]["cells"][2] = 41
        with self.assertRaises(store.Invalid):
            self.ps.save_table(rel, t.header, rows, t.etag)
        dup = rows_of(t)
        dup[1]["row_uid"] = dup[0]["row_uid"]
        with self.assertRaises(store.Invalid):
            self.ps.save_table(rel, t.header, dup, t.etag)

    def test_unreadable_csv_is_validation_error(self):
        rel = self.put("cr.csv", b"a;b\rA;1\rB;2\r")
        with self.assertRaises(store.Invalid):
            self.ps.load_table(rel)
        with self.assertRaises(store.NotFound):
            self.ps.load_table("03_adatok/nincs.csv")


class RowUidTests(_ProjectCase):
    def test_derived_uids_deterministic(self):
        rel = self.put("hu.csv", HU_EXCEL)
        a, b = self.ps.load_table(rel), store.ProjectStore(self.root).load_table(rel)
        self.assertEqual(a.row_uids, b.row_uids)
        self.assertFalse(a.uid_column)
        self.assertEqual(a.row_uids[0], store.derive_row_uid("Kovács 2019", 0))
        self.assertEqual(a.row_uids[2], store.derive_row_uid("Szabó 2022", 2))
        for u in a.row_uids:
            self.assertRegex(u, r"^r[a-z2-7]{6}$")
        self.assertEqual(len(set(a.row_uids)), len(a.row_uids))
        self.assertNotIn("row_uid", self.get(rel).decode("utf-8-sig"))   # betöltés nem ír

    def test_derive_row_uid_spec(self):
        import base64
        import hashlib
        want = "r" + base64.b32encode(hashlib.sha1("Aronson 1948|0".encode("utf-8")).digest()).decode().lower()[:6]
        self.assertEqual(store.derive_row_uid("Aronson 1948", 0), want)
        self.assertNotEqual(store.derive_row_uid("x", 0, taken={store.derive_row_uid("x", 0)}),
                            store.derive_row_uid("x", 0))

    def test_uid_column_hidden_and_position_kept(self):
        raw = b"study;row_uid;n\nA;rabcdef;1\nB;rbcdefg;2\n"
        rel = self.put("u.csv", raw)
        t = self.ps.load_table(rel)
        self.assertEqual(t.header, ["study", "n"])
        self.assertEqual(t.row_uids, ["rabcdef", "rbcdefg"])
        self.assertEqual(t.rows[0]["cells"], ["A", "1"])
        self.assertTrue(t.to_json()["uid"]["persisted"])
        rows = rows_of(t)
        rows[1]["cells"][1] = "3"
        self.ps.save_table(rel, t.header, rows, t.etag)
        self.assertEqual(self.get(rel), b"study;row_uid;n\nA;rabcdef;1\nB;rbcdefg;3\n")

    def test_uid_issues_missing_invalid_duplicate(self):
        raw = b"study;n;row_uid\nA;1;rabcdef\nB;2;rabcdef\nC;3;\nD;4;XYZ\n"
        rel = self.put("u.csv", raw)
        t = self.ps.load_table(rel)
        kinds = [(i["row_index"], i["kind"]) for i in t.uid_issues]
        self.assertEqual(kinds, [(1, "duplicate"), (2, "missing"), (3, "invalid")])
        self.assertEqual(t.row_uids[0], "rabcdef")
        self.assertEqual(len(set(t.row_uids)), 4)
        self.assertFalse(t.to_json()["uid"]["persisted"])
        t2 = self.ps.save_table(rel, t.header, rows_of(t), t.etag)
        self.assertEqual(t2.row_uids, t.row_uids)
        self.assertEqual(t2.uid_issues, [])
        self.assertEqual(self.get(rel).split(b"\n")[1], b"A;1;rabcdef")

    def test_header_with_row_uid_in_submission(self):
        rel = self.put("hu.csv", HU_EXCEL)
        t = self.ps.load_table(rel)
        header = ["row_uid"] + t.header
        rows = [[r["row_uid"]] + r["cells"] for r in t.rows]
        t2 = self.ps.save_table(rel, header, rows, t.etag)
        self.assertEqual(t2.header, t.header)
        self.assertEqual(t2.row_uids, t.row_uids)
        self.assertTrue(self.get(rel).decode("utf-8-sig").startswith("row_uid;Szerző;"))


class ConflictTests(_ProjectCase):
    def _external_edit(self, rel):
        text = self.get(rel).decode("utf-8-sig")
        text = text.replace("Kovács 2019;2019;40;12,5;3,1", "Kovács 2019;2019;40;12,5;9,9")
        text += "Kiss 2023;2023;20;11,0;2,0;22;10,5;2,2\r\n"
        (self.root / rel).write_bytes(codecs.BOM_UTF8 + text.encode("utf-8"))

    def test_conflict_with_cell_diff(self):
        rel = self.put("hu.csv", HU_EXCEL)
        t = self.ps.load_table(rel)
        self._external_edit(rel)
        external = self.get(rel)
        mine = rows_of(t)
        mine[0]["cells"][4] = "3,2"            # ütközik
        mine[1]["cells"][2] = "56"             # csak a saját módosítás
        with self.assertRaises(store.Conflict) as cm:
            self.ps.save_table(rel, t.header, mine, t.etag)
        e = cm.exception
        self.assertEqual((e.code, e.http), ("CONFLICT", 409))
        self.assertEqual(e.details["etag"], store.sha256_bytes(external))
        self.assertTrue(e.details["base_known"])
        uid0 = t.row_uids[0]
        self.assertIn({"row_uid": uid0, "column": "sd1", "base": "3,1", "theirs": "9,9", "mine": "3,2",
                       "conflict": True}, e.diff)
        added = [d for d in e.diff if d["row_uid"] not in t.row_uids]
        self.assertEqual(len(added), len(t.header))
        self.assertTrue(all(d["base"] is None and d["mine"] is None and not d["conflict"] for d in added))
        self.assertIn({"row_uid": added[0]["row_uid"], "column": "Szerző", "base": None, "theirs": "Kiss 2023",
                       "mine": None, "conflict": False}, e.diff)
        self.assertFalse(any(d["column"] == "n1" and d["row_uid"] == t.row_uids[1] for d in e.diff))
        for secret in ("9,9", "3,2", "Kiss"):
            self.assertNotIn(secret, str(e))
            self.assertNotIn(secret, e.message)
        self.assertEqual(self.get(rel), external)            # semmi nem íródott
        self.assertEqual(self.leftovers(), [])

    def test_conflict_without_cached_base(self):
        rel = self.put("hu.csv", HU_EXCEL)
        t = self.ps.load_table(rel)
        self._external_edit(rel)
        other = store.ProjectStore(self.root)                  # új szerverpéldány: nincs base
        mine = rows_of(t)
        with self.assertRaises(store.Conflict) as cm:
            other.save_table(rel, t.header, mine, t.etag)
        e = cm.exception
        self.assertFalse(e.details["base_known"])
        self.assertIn({"row_uid": t.row_uids[0], "column": "sd1", "base": None, "theirs": "9,9", "mine": "3,1",
                       "conflict": True}, e.diff)

    def test_if_match_variants(self):
        rel = self.put("hu.csv", HU_EXCEL)
        t = self.ps.load_table(rel)
        with self.assertRaises(store.Conflict):
            self.ps.save_table(rel, t.header, rows_of(t), None)            # létező fájl, If-None-Match
        t2 = self.ps.save_table(rel, t.header, rows_of(t), 'W/"%s"' % t.etag.upper())
        t3 = self.ps.save_table(rel, t.header, rows_of(t), "*")
        self.assertEqual(t2.etag, t3.etag)
        with self.assertRaises(store.Conflict) as cm:
            self.ps.save_table("03_adatok/nincs.csv", ["a"], [["1"]], "*")
        self.assertIsNone(cm.exception.details["etag"])

    def test_create_new_table_default_format(self):
        rel = "03_adatok/o2.csv"
        t = self.ps.save_table(rel, ["study", "e1", "n1"], [{"cells": ["Kovács, A", "3", "40"]}, ["Tóth", "1", "2"]],
                               None)
        raw = self.get(rel)
        self.assertTrue(raw.startswith(codecs.BOM_UTF8))
        self.assertEqual(raw[3:].decode("utf-8"),
                         'study;e1;n1;row_uid\r\nKovács, A;3;40;%s\r\nTóth;1;2;%s\r\n' % tuple(t.row_uids))
        self.assertEqual(self.ps.load_table(rel).row_uids, t.row_uids)
        rows, meta = tableio.read_table(str(self.root / rel))
        self.assertEqual((meta["n_rows"], rows[0]["study"], rows[0]["n1"]), (2, "Kovács, A", 40.0))
        fmt = {"encoding": "utf-8", "bom": False, "delimiter": ",", "newline": "lf"}
        self.ps.save_table("03_adatok/o3.csv", ["study"], [["A"]], None, write_uids=False, fmt=fmt)
        self.assertEqual(self.get("03_adatok/o3.csv"), b"study\nA\n")

    def test_sniffer_flip_is_prevented_by_quoting(self):
        # minimal idézéssel a Sniffer ','-t ismerne fel; a mentés visszaolvasással ellenőriz
        rel = "03_adatok/vesszos.csv"
        header = ["Átlag, kezelt", "SD, kezelt"]
        for write_uids in (False, True):
            with self.subTest(write_uids=write_uids):
                t = self.ps.save_table(rel, header, [["12,5", "3,1"]], "*" if write_uids else None,
                                       write_uids=write_uids)
                self.assertIn(b'"\xc3\x81tlag, kezelt"', self.get(rel))
                rows, meta = tableio.read_table(str(self.root / rel))
                self.assertEqual(meta["delimiter"], ";")
                self.assertEqual(meta["columns"][:2], header)
                self.assertEqual(self.ps.load_table(rel).rows[0]["cells"], ["12,5", "3,1"])
                self.assertEqual(t.header, header)

    def test_noop_save_does_not_touch_file(self):
        rel = self.put("hu.csv", HU_EXCEL)
        os.utime(str(self.root / rel), (1000000000, 1000000000))
        self.resave(rel)
        self.assertEqual(os.stat(str(self.root / rel)).st_mtime, 1000000000)


class AtomicWriteTests(_ProjectCase):
    def test_no_tmp_files_after_writes(self):
        rel = self.put("hu.csv", HU_EXCEL)
        for i in range(3):
            def edit(header, rows, i=i):
                rows[0]["cells"][2] = str(40 + i + 1)
                return header, rows
            self.resave(rel, write_uids=True, mutate=edit)
        self.ps.save_studies(store.empty_studies(), None)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(sorted(p.name for p in (self.root / "03_adatok").iterdir()), ["hu.csv", "studies.json"])

    def test_excel_lock_maps_to_423(self):
        rel = self.put("hu.csv", HU_EXCEL)
        t = self.ps.load_table(rel)
        rows = rows_of(t)
        rows[0]["cells"][2] = "41"
        with mock.patch.object(store.os, "replace", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(store.Locked) as cm:
                self.ps.save_table(rel, t.header, rows, t.etag)
            with self.assertRaises(store.Locked):
                self.ps.save_studies(store.empty_studies(), None)
        e = cm.exception
        self.assertEqual((e.code, e.http), ("LOCKED", 423))
        self.assertEqual(e.message, "Zárd be a fájlt az Excelben, majd próbáld újra.")
        self.assertEqual(e.to_error()["details"]["path"], rel)
        self.assertEqual(self.get(rel), HU_EXCEL)
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.root / store.STUDIES_REL).exists())
        # a zár feloldása után ugyanazzal az ETag-gel menthető
        self.ps.save_table(rel, t.header, rows, t.etag)

    def test_lock_retry_then_success(self):
        rel = self.put("hu.csv", HU_EXCEL)
        real = os.replace
        calls = []

        def flaky(a, b):
            calls.append(1)
            if len(calls) < 2:
                raise PermissionError(32, "sharing violation")
            return real(a, b)

        t = self.ps.load_table(rel)
        rows = rows_of(t)
        rows[0]["cells"][2] = "41"
        with mock.patch.object(store.os, "replace", side_effect=flaky):
            self.ps.save_table(rel, t.header, rows, t.etag)
        self.assertEqual(len(calls), 2)
        self.assertIn(b";41;", self.get(rel))

    def test_external_write_during_save_is_conflict(self):
        rel = self.put("hu.csv", HU_EXCEL)
        t = self.ps.load_table(rel)
        rows = rows_of(t)
        rows[0]["cells"][2] = "41"
        real = store._write_tmp
        external = HU_EXCEL + "Kiss;2023;1;1;1;1;1;1\r\n".encode("utf-8")

        def racing(path, data):
            tmp = real(path, data)
            (self.root / rel).write_bytes(external)       # Excel ment a két lépés között
            return tmp

        with mock.patch.object(store, "_write_tmp", side_effect=racing):
            with self.assertRaises(store.Conflict) as cm:
                self.ps.save_table(rel, t.header, rows, t.etag)
        self.assertEqual(cm.exception.details["etag"], store.sha256_bytes(external))
        self.assertEqual(self.get(rel), external)
        self.assertEqual(self.leftovers(), [])

    def test_table_json_shape(self):
        rel = self.put("hu.csv", HU_EXCEL)
        j = self.ps.load_table(rel).to_json()
        self.assertEqual(sorted(j), ["dataset", "etag", "format", "header", "rows", "uid"])
        self.assertEqual(sorted(j["rows"][0]), ["cells", "line", "row_uid"])
        self.assertEqual(j["uid"], {"column": False, "persisted": False, "issues": []})
        json.dumps(j)
        self.assertEqual(store.CsvFormat.from_json(j["format"]), self.ps.load_table(rel).fmt)
        with self.assertRaises(store.Invalid):
            store.CsvFormat.from_json({"encoding": "ebcdic"})
        self.assertEqual([x["dataset"] for x in self.ps.list_tables()], [rel])

    def test_partial_pair_write_is_detectable(self):
        rel = self.put("hu.csv", HU_EXCEL)
        t = self.ps.load_table(rel)
        uid = t.row_uids[0]
        cell = {"row_uid": uid, "field": "sd1", "method": "reported", "estimated": False,
                "source": {"doc": "pmid:1", "page": 3}}
        prov = {"schema": store.PROV_SCHEMA, "cells": [cell]}
        t1 = self.ps.save_table(rel, t.header, rows_of(t), t.etag, provenance=prov)
        doc, _, st = self.ps.load_provenance(rel)
        self.assertEqual(doc["table_sha256"], t1.etag)
        self.assertTrue(st["in_sync"])
        real = os.replace
        calls = []

        def second_fails(a, b):
            calls.append(b)
            if b.endswith(store.PROV_SUFFIX):
                raise PermissionError(13, "locked")
            return real(a, b)

        rows = rows_of(t1)
        rows[0]["cells"][4] = "3,4"
        with mock.patch.object(store.os, "replace", side_effect=second_fails):
            with self.assertRaises(store.Locked) as cm:
                self.ps.save_table(rel, t1.header, rows, t1.etag, provenance=doc)
        self.assertEqual(cm.exception.details["written"], [rel])
        self.assertTrue(cm.exception.details["partial"])
        self.assertTrue(calls[0].endswith("hu.csv"))           # előbb a CSV
        _, _, st = self.ps.load_provenance(rel)
        self.assertFalse(st["in_sync"])                         # X022 ezt jelzi
        self.assertEqual(self.leftovers(), [])


class PathGuardTests(_ProjectCase):
    def test_escapes_rejected(self):
        bad = ["../x.csv", "/etc/passwd", "C:/Windows/x.csv", "c:x.csv", "03_adatok\\..\\x.csv", "03_adatok/../../x.csv",
               "a//b.csv", "./a.csv", "03_adatok/CON.csv", "03_adatok/nul", "x.csv.", ".git/config", "a/<b>.csv", "",
               "03_adatok/a?.csv", None]
        for rel in bad:
            with self.subTest(rel=rel):
                with self.assertRaises(store.Forbidden) as cm:
                    self.ps.path(rel)
                self.assertEqual(cm.exception.http, 403)
        with self.assertRaises(store.Forbidden):
            self.ps.save_table("../kint.csv", ["a"], [["1"]], None)
        with self.assertRaises(store.Forbidden):
            self.ps.load_table("../../etc/passwd")
        self.assertFalse((Path(self.tmp) / "kint.csv").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "nincs szimbolikus link")
    def test_symlink_escape_rejected(self):
        outside = Path(self.tmp) / "kint"
        outside.mkdir()
        try:
            os.symlink(str(outside), str(self.root / "03_adatok" / "link"))
        except (OSError, NotImplementedError):
            self.skipTest("a szimbolikus link nem hozható létre")
        with self.assertRaises(store.Forbidden):
            self.ps.save_table("03_adatok/link/x.csv", ["a"], [["1"]], None)
        self.assertEqual(list(outside.iterdir()), [])

    def test_valid_paths(self):
        p = self.ps.path("03_adatok/kettos/o1.A.csv")
        self.assertTrue(store.is_within(p, self.root))
        self.assertEqual(self.ps.relpath_of(p), "03_adatok/kettos/o1.A.csv")
        self.assertEqual(store.provenance_relpath("03_adatok/o1.csv"), "03_adatok/o1.prov.json")
        with self.assertRaises(store.Forbidden):
            self.ps.relpath_of(Path(self.tmp) / "x.csv")


class SidecarTests(_ProjectCase):
    def test_provenance_roundtrip_and_validation(self):
        rel = self.put("hu.csv", HU_EXCEL)
        t = self.ps.load_table(rel)
        doc, etag, st = self.ps.load_provenance(rel)
        self.assertIsNone(etag)
        self.assertEqual(doc, store.empty_provenance(rel))
        self.assertIsNone(st["in_sync"])
        doc["cells"].append({"row_uid": t.row_uids[0], "field": "sd1", "value_as_entered": "3,1",
                             "method": "digitized", "estimated": True,
                             "source": {"doc": "pmid:31234567", "page": 7, "locator": "Fig. 2", "quote": "x" * 500},
                             "history": []})
        e1 = self.ps.save_provenance(rel, doc, None, table_etag='"%s"' % t.etag)
        got, e2, st = self.ps.load_provenance(rel)
        self.assertEqual(e1, e2)
        self.assertTrue(st["in_sync"])
        self.assertEqual(got["cells"][0]["source"]["page"], 7)
        raw = self.get(store.provenance_relpath(rel))
        self.assertTrue(raw.endswith(b"}\n"))
        self.assertIn("Fig. 2".encode("utf-8"), raw)
        bad_cases = [
            lambda d: d["cells"][0].update(method="guessed"),
            lambda d: d["cells"][0].update(estimated=False),
            lambda d: d["cells"][0].update(row_uid="12"),
            lambda d: d["cells"][0]["source"].update(page="7"),
            lambda d: d["cells"][0]["source"].update(quote="x" * 501),
            lambda d: d["cells"].append(dict(d["cells"][0])),
            lambda d: d["cells"][0].update(history=[{}] * 51),
            lambda d: d.update(schema="szk.ma.provenance/v0"),
            lambda d: d.update(table_sha256="abc"),
        ]
        for i, mutate in enumerate(bad_cases):
            with self.subTest(case=i):
                d = json.loads(json.dumps(got))
                mutate(d)
                with self.assertRaises(store.Invalid) as cm:
                    self.ps.save_provenance(rel, d, e2)
                self.assertTrue(cm.exception.details["problems"])
                self.assertNotIn("3,1", cm.exception.message)
        self.assertEqual(self.get(store.provenance_relpath(rel)), raw)
        # külső CSV-módosítás → az oldalfájl elavult (X022)
        (self.root / rel).write_bytes(HU_EXCEL.replace(b"3,1", b"3,0"))
        self.assertFalse(self.ps.load_provenance(rel)[2]["in_sync"])

    def test_invalid_provenance_blocks_table_write(self):
        rel = self.put("hu.csv", HU_EXCEL)
        t = self.ps.load_table(rel)
        rows = rows_of(t)
        rows[0]["cells"][2] = "41"
        bad = {"schema": store.PROV_SCHEMA, "cells": [{"row_uid": t.row_uids[0], "field": "n1", "method": "?"}]}
        with self.assertRaises(store.Invalid):
            self.ps.save_table(rel, t.header, rows, t.etag, provenance=bad)
        self.assertEqual(self.get(rel), HU_EXCEL)

    def test_documents(self):
        docs = {"schema": store.DOCS_SCHEMA, "docs": [
            {"id": "pmid:31234567", "root": "project", "path": "_privat/pdf/smith2010.pdf", "sha256": None, "pages": 12},
            {"id": "doi:10.1000/xyz", "root": "composer_outdir", "path": "fulltext/a.pdf", "sha256": "a" * 64,
             "pages": None}]}
        etag = self.ps.save_documents(docs, None)
        got, etag2 = self.ps.load_documents()
        self.assertEqual((got, etag2), (docs, etag))
        p = self.ps.document_path("pmid:31234567")
        self.assertEqual(self.ps.relpath_of(p), "_privat/pdf/smith2010.pdf")
        outdir = Path(self.tmp) / "composer_out"
        self.assertEqual(self.ps.document_path("doi:10.1000/xyz", outdir),
                         Path(os.path.realpath(str(outdir))) / "fulltext" / "a.pdf")
        with self.assertRaises(store.NotFound):
            self.ps.document_path("doi:10.1000/xyz")
        with self.assertRaises(store.NotFound):
            self.ps.document_path("pmid:1")
        for mutate in (lambda d: d["docs"][0].update(path="../../etc/passwd"),
                       lambda d: d["docs"][0].update(root="home"),
                       lambda d: d["docs"][1].update(id="pmid:31234567"),
                       lambda d: d["docs"][0].update(id="smith"),
                       lambda d: d["docs"][0].update(pages=-1),
                       lambda d: d["docs"][0].update(sha256="xyz")):
            d = json.loads(json.dumps(docs))
            mutate(d)
            with self.assertRaises(store.Invalid):
                self.ps.save_documents(d, etag)
        with self.assertRaises(store.Conflict):
            self.ps.save_documents(docs, "0" * 64)

    def test_studies(self):
        doc, etag = self.ps.load_studies()
        self.assertEqual((doc, etag), (store.empty_studies(), None))
        doc["studies"].append({"study_id": "NCT0456", "label": "Smith 2010", "registration": "NCT0456",
                               "design": "rct_parallel", "outcomes": ["o1", "o2"],
                               "reports": [{"rec_id": "pmid:20123456", "role": "primary", "doc": "pmid:20123456"},
                                           {"rec_id": "pmid:21987654", "role": "secondary"}]})
        e1 = self.ps.save_studies(doc, None)
        self.assertEqual(self.ps.load_studies(), (doc, e1))
        dup = json.loads(json.dumps(doc))
        dup["studies"].append(dict(dup["studies"][0]))
        with self.assertRaises(store.Invalid):
            self.ps.save_studies(dup, e1)
        bad = json.loads(json.dumps(doc))
        bad["studies"][0]["reports"][0]["rec_id"] = ""
        with self.assertRaises(store.Invalid):
            self.ps.save_studies(bad, e1)
        with self.assertRaises(store.Conflict):
            self.ps.save_studies(doc, None)
        nan = json.loads(json.dumps(doc))
        nan["studies"][0]["x"] = float("nan")
        with self.assertRaises(store.Invalid):
            self.ps.save_studies(nan, e1)
        (self.root / store.STUDIES_REL).write_text("{nem json", encoding="utf-8")
        with self.assertRaises(store.Invalid):
            self.ps.load_studies()


class WatcherTests(_ProjectCase):
    def setUp(self):
        super().setUp()
        self.rel = self.put("hu.csv", HU_EXCEL)
        db = sqlite3.connect(str(self.root / "projekt.sqlite"))
        db.execute("CREATE TABLE finding (id INTEGER PRIMARY KEY, title TEXT)")
        db.commit()
        db.close()
        self.w = store.ChangeWatcher(self.root, interval=0.05)
        self.watchers = [self.w]

    def tearDown(self):
        for w in self.watchers:             # Windows: a nyitott SQLite-kapcsolat a törlés előtt záródjon
            w.stop()
        super().tearDown()

    def test_baseline_and_external_change(self):
        r0 = self.w.rev
        self.assertEqual(self.w.poll(r0), {"rev": r0, "changed": [], "external": [], "reset": False})
        (self.root / self.rel).write_bytes(HU_EXCEL + b"Kiss;2023;1;1;1;1;1;1\r\n")
        res = self.w.poll(r0)
        self.assertEqual(res["rev"], r0 + 1)
        self.assertEqual(res["changed"], [self.rel])
        self.assertEqual(res["external"], [self.rel])
        self.assertEqual(self.w.poll(res["rev"])["changed"], [])
        self.assertEqual(self.w.changes_since(r0)["changed"], [self.rel])

    def test_own_write_not_external(self):
        ps = store.ProjectStore(self.root, watcher=self.w)
        r0 = self.w.rev
        t = ps.load_table(self.rel)
        rows = rows_of(t)
        rows[0]["cells"][2] = "41"
        ps.save_table(self.rel, t.header, rows, t.etag, provenance={"schema": store.PROV_SCHEMA, "cells": []})
        res = self.w.changes_since(r0)                      # a mentés maga is lefuttat egy kört
        self.assertEqual(res["changed"], ["03_adatok/hu.csv", "03_adatok/hu.prov.json"])
        self.assertEqual(res["external"], [])
        self.assertEqual(store.classify_key("03_adatok/hu.prov.json"), "provenance")
        self.assertEqual(store.classify_key(self.rel), "table")
        self.assertEqual(store.classify_key("projekt.sqlite"), "db")

    def test_sqlite_data_version(self):
        r0 = self.w.rev
        self.assertEqual(self.w.poll(r0)["changed"], [])
        con = sqlite3.connect(str(self.root / "projekt.sqlite"))
        con.execute("INSERT INTO finding (title) VALUES ('x')")
        con.commit()
        con.close()
        res = self.w.poll(r0)
        self.assertEqual(res["changed"], ["projekt.sqlite"])
        self.assertEqual(res["external"], [])

    def test_activity_log_change_is_never_external(self):
        r0 = self.w.rev
        log = self.root / "07_ellenorzes" / "activity.jsonl"
        log.parent.mkdir()
        log.write_bytes(b"{}\n")
        res = self.w.poll(r0)
        self.assertEqual(res["changed"], ["07_ellenorzes/activity.jsonl"])
        self.assertEqual(res["external"], [])

    def test_delete_new_and_tmp_ignored(self):
        r0 = self.w.rev
        (self.root / "03_adatok" / ".hu.csv.abc.tmp").write_bytes(b"x")
        (self.root / "03_adatok" / "studies.json").write_bytes(b"{}")
        os.unlink(str(self.root / self.rel))
        res = self.w.poll(r0)
        self.assertEqual(res["changed"], ["03_adatok/hu.csv", "03_adatok/studies.json"])

    def test_reset_cases(self):
        r = self.w.rev
        for since in (None, "abc", r + 5, 0, r - 1):
            with self.subTest(since=since):
                self.assertTrue(self.w.changes_since(since)["reset"])
        self.assertFalse(self.w.changes_since(r)["reset"])

    def test_history_overflow_resets(self):
        w = store.ChangeWatcher(self.root, history=2)
        self.watchers.append(w)
        r0 = w.rev
        for i in range(3):
            (self.root / self.rel).write_bytes(HU_EXCEL + (b"%d" % i))
            w.scan()
        self.assertTrue(w.changes_since(r0)["reset"])
        self.assertFalse(w.changes_since(r0 + 1)["reset"])

    def test_wait_with_background_thread(self):
        self.w.start()
        r0 = self.w.rev

        def later():
            time.sleep(0.2)
            (self.root / self.rel).write_bytes(HU_EXCEL + b"\r\n")

        th = threading.Thread(target=later)
        th.start()
        t0 = time.monotonic()
        res = self.w.wait(r0, timeout=10)
        th.join()
        self.assertEqual(res["changed"], [self.rel])
        self.assertLess(time.monotonic() - t0, 5)

    def test_wait_without_thread_and_timeout(self):
        r0 = self.w.rev
        t0 = time.monotonic()
        res = self.w.wait(r0, timeout=0.2)
        self.assertEqual(res["changed"], [])
        self.assertGreaterEqual(time.monotonic() - t0, 0.15)
        threading.Timer(0.1, lambda: (self.root / self.rel).write_bytes(b"x")).start()
        res = self.w.wait(r0, timeout=5)
        self.assertEqual(res["changed"], [self.rel])


class StaticRulesTests(unittest.TestCase):
    def test_no_statistics_imports(self):
        banned = {"math", "cmath", "statistics", "random", "decimal"}
        for mod in ("store.py", "activity.py"):
            tree = ast.parse(Path(ROOT, "ma_gui", mod).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                self.assertFalse(banned & set(names), "%s: tiltott import %s" % (mod, names))

    def test_no_logging_or_print(self):
        for mod in ("store.py", "activity.py"):
            src = Path(ROOT, "ma_gui", mod).read_text(encoding="utf-8")
            tree = ast.parse(src)
            calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            self.assertNotIn("print", calls, mod)
            self.assertNotIn("import logging", src, mod)


if __name__ == "__main__":
    unittest.main()
