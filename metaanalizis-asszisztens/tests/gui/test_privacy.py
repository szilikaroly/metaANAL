# -*- coding: utf-8 -*-
"""ma_gui.privacy: PHI/TAJ-szkenner, vault-felismerés, kezelt .gitignore-blokk, Claude deny-szabály,
pre-commit őr, „már felment?”, felhőszinkron, írás-tartás és összesített állapot (terv 7.4–7.5, 8.5)."""
import ast
import csv
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ma_gui import privacy as pv  # noqa: E402

HAS_GIT = shutil.which("git") is not None
POSIX = os.name != "nt"
BLOCK_TEXT = "\n".join(pv.managed_block_lines()) + "\n"


def _cdv(first8):
    """Független CDV-számítás a tesztben: 1., 3., 5., 7. jegy ×3, 2., 4., 6., 8. jegy ×7, mod 10."""
    total = 0
    for pos, ch in enumerate(first8, start=1):
        total += int(ch) * (3 if pos % 2 == 1 else 7)
    return total % 10


class TempDirMixin(object):
    def make_tmp(self):
        d = Path(tempfile.mkdtemp(prefix="ma-privacy-")).resolve()
        self.addCleanup(shutil.rmtree, str(d), True)
        return d


class GitMixin(TempDirMixin):
    """Elszigetelt git-környezet: se globális, se rendszerszintű konfiguráció (pl. aláírás, hooksPath)."""

    def setup_git_env(self):
        tmp = self.make_tmp()
        empty = tmp / "empty.gitconfig"
        empty.write_text("", encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(empty),
            "GIT_AUTHOR_NAME": "Teszt", "GIT_AUTHOR_EMAIL": "teszt@example.invalid",
            "GIT_COMMITTER_NAME": "Teszt", "GIT_COMMITTER_EMAIL": "teszt@example.invalid",
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, cwd, *args, check=True):
        p = subprocess.run(["git"] + list(args), cwd=str(cwd), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        if check and p.returncode != 0:
            raise AssertionError("git %s: %s" % (" ".join(args), p.stderr.decode("utf-8", "replace")))
        return p

    def init_repo(self, path):
        path.mkdir(parents=True, exist_ok=True)
        self.git(path, "init", "-q")
        return path

    def commit_file(self, repo, rel, content="x\n", force=False, msg="c"):
        f = repo.joinpath(*rel.split("/"))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        self.git(repo, "add", *(["-f"] if force else []), "--", rel)
        self.git(repo, "commit", "-q", "--no-verify", "-m", msg)


# ---------------------------------------------------------------------------------------------
# TAJ-ellenőrzőjegy
# ---------------------------------------------------------------------------------------------

class TajChecksumTest(unittest.TestCase):
    def test_hand_computed_example(self):
        # 1·3 + 2·7 + 3·3 + 4·7 + 5·3 + 6·7 + 7·3 + 8·7 = 188 → ellenőrzőjegy 8
        self.assertEqual(pv.taj_check_digit("12345678"), 8)
        self.assertTrue(pv.is_valid_taj("123456788"))
        self.assertFalse(pv.is_valid_taj("123456789"))

    def test_algorithmic_valid_and_every_wrong_digit_invalid(self):
        bases = [str((i * 7919 + 13) % 10 ** 8).zfill(8) for i in range(1, 300)]
        for base in bases:
            good = base + str(_cdv(base))
            if good == "000000000":
                continue
            self.assertEqual(pv.taj_check_digit(base), _cdv(base), base)
            self.assertTrue(pv.is_valid_taj(good), good)
            for d in "0123456789":
                if d != good[8]:
                    self.assertFalse(pv.is_valid_taj(base + d), base + d)

    def test_formats_and_malformed(self):
        self.assertTrue(pv.is_valid_taj("123 456 788"))
        self.assertTrue(pv.is_valid_taj("123-456-788"))
        for bad in ("12345678", "1234567888", "12345678a", "", "000000000", "123 456 78 8x"):
            self.assertFalse(pv.is_valid_taj(bad), bad)
        with self.assertRaises(ValueError):
            pv.taj_check_digit("1234567")

    def test_value_scan_taj(self):
        good = "037492015"[:8]
        good = good + str(_cdv(good))
        bad = good[:8] + str((int(good[8]) + 1) % 10)
        self.assertEqual(pv.value_patterns("TAJ: %s %s %s" % (good[:3], good[3:6], good[6:])), ["taj_cdv"])
        self.assertEqual(pv.value_patterns(good), ["taj_cdv"])
        self.assertEqual(pv.value_patterns("%s-%s-%s" % (good[:3], good[3:6], good[6:])), ["taj_cdv"])
        self.assertEqual(pv.value_patterns(bad), [])
        # hosszabb számsor, tizedestört, azonosító-előtag: nem TAJ
        for text in ("1" + good, good + "0", "0," + good, "0." + good, "NCT" + good, good + ".5"):
            self.assertEqual(pv.value_patterns(text), [], text)

    def test_numeric_cells(self):
        good = "12345678" + str(_cdv("12345678"))
        f = pv.scan_table(["x"], [[int(good)], [float(good)], [True], [None], [1.5]])
        self.assertEqual([(x["row_index"], x["pattern"]) for x in f], [(0, "taj_cdv"), (1, "taj_cdv")])

        class NumText(float):           # a tableio.NumText alakja: float + eredeti szöveg
            pass
        n = NumText(1.0)
        n.text = good
        self.assertEqual(len(pv.scan_table(["x"], [[n]])), 1)


# ---------------------------------------------------------------------------------------------
# oszlopnév-minták
# ---------------------------------------------------------------------------------------------

class ColumnPatternTest(unittest.TestCase):
    POSITIVE = {
        "TAJ": "taj", "TAJ-szám": "taj", "taj_szam": "taj", "Tajszám": "taj", "SSN": "taj",
        "Név": "name", "NEV": "name", "name": "name", "Beteg neve": "name", "Vezetéknév": "name",
        "Patient Name": "name", "first_name": "name", "lastName": "name", "Anyja neve": "name",
        "Születési dátum": "birth", "SZULETESI_DATUM": "birth", "Szül. idő": "birth", "DOB": "birth",
        "date_of_birth": "birth", "BirthDate": "birth", "birthday": "birth", "születési hely": "birth",
        "Cím": "address", "Lakcím": "address", "address": "address", "Home Address": "address",
        "irányítószám": "address",
        "Telefon": "phone", "telefonszám": "phone", "Phone number": "phone", "tel": "phone",
        "E-mail": "email", "email_cim": "email", "e-mail cím": "email", "Email address": "email",
        "MRN": "mrn", "medical_record_number": "mrn", "kórlapszám": "mrn", "Törzsszám": "mrn",
        "patient_id": "patient_id", "PatientID": "patient_id", "beteg_azonosító": "patient_id",
        "Betegazonosító": "patient_id", "Beteg-azonosító": "patient_id", "subject id": "patient_id",
        "beteg kód": "patient_id",
    }
    NEGATIVE = [
        "vizsgálat", "study", "Study name", "study_id", "Szerző", "Szerző neve", "author name",
        "Gyógyszer neve", "Vizsgálat neve", "Trial name", "esemény1", "esemény2", "n1", "n2", "n",
        "szélesség", "év", "Year", "allokáció", "subgroup", "alcsoport", "rob", "row_uid", "mean_t",
        "sd_ctrl", "m1", "sd1", "x", "r", "yi", "vi", "sei", "korreláció", "nevező", "tájékoztatás",
        "címke", "Cikk címe", "Angol cím", "születési súly", "birth weight", "birthweight", "Births",
        "preterm birth", "születési hét", "No. of patients", "betegek száma", "patients (n)",
        "Beavatkozás", "Kontroll", "Ország", "Megjegyzés", "Kimenet", "Időtartam (hét)", "életkor",
        "Mean age", "Female (%)", "nők aránya", "BMI", "HbA1c", "Follow-up", "Setting", "Design",
        "", "   ", "#", "1",
    ]

    def test_positive(self):
        for name, pat in self.POSITIVE.items():
            self.assertIn(pat, pv.column_patterns(name), name)

    def test_negative(self):
        for name in self.NEGATIVE:
            self.assertEqual(pv.column_patterns(name), [], name)

    def test_accent_and_case_insensitive(self):
        for a, b in (("Születési dátum", "szuletesi datum"), ("Név", "nev"), ("Beteg-azonosító", "BETEG_AZONOSITO"),
                     ("Kórlapszám", "korlapszam"), ("Lakcím", "LAKCIM")):
            self.assertEqual(pv.column_patterns(a), pv.column_patterns(b), a)
            self.assertTrue(pv.column_patterns(a), a)

    def test_scan_reports_column_findings(self):
        f = pv.scan_table(["study", "Név", "TAJ"], [])
        self.assertEqual([(x["kind"], x["column"], x["column_index"], x["row_index"], x["pattern"]) for x in f],
                         [("column", "Név", 1, None, "name"), ("column", "TAJ", 2, None, "taj")])


# ---------------------------------------------------------------------------------------------
# értékminták: dátum, e-mail; értékmentes találatok
# ---------------------------------------------------------------------------------------------

class ValuePatternTest(unittest.TestCase):
    DATES = ["1985-03-12", "1985.03.12.", "1985. 03. 12.", "1985/3/2", "12.03.1985", "12. 03. 1985.",
             "03/12/1985", "12/31/1985", "1985. március 12.", "1985. márc. 12.", "12 March 1985",
             "March 12, 1985", "Mar 12th 1985", "12-Mar-1985", "született: 1972.05.04", "2.1.1990",
             "1985-03-12T10:00", "29.02.2000"]
    NOT_DATES = ["1985", "2001-2003", "1985-13-12", "1985-02-30", "29.02.1999", "0.12-0.45", "12.5",
                 "1.234.567", "13598", "Hart & Sutherland 1977", "Rosenthal et al 1960", "2019;12:345",
                 "Lancet 2019;393(10170):123-130", "95% CI 0,12–0,98", "1800-01-01", "10.1016/j.x.2019.12.003",
                 "Dec 2019", "2019 december", "már 12 beteg", "1.2.3", "12/31/85", "v2.10.2019.1"]

    def test_full_dates(self):
        for t in self.DATES:
            self.assertIn("full_date", pv.value_patterns(t), t)

    def test_not_dates(self):
        for t in self.NOT_DATES:
            self.assertNotIn("full_date", pv.value_patterns(t), t)

    def test_date_column_context(self):
        rows = [["1985-03-12", "1985-03-12", "1985-03-12"]]
        f = pv.scan_table(["Publikáció dátuma", "Felvétel dátuma", "Search date"], rows)
        self.assertEqual([(x["column"], x["pattern"]) for x in f], [("Felvétel dátuma", "full_date")])

    def test_email(self):
        for t in ("kovacs.janos@example.hu", "Levelezés: Dr. X <x_y+z@med.uni-szeged.hu>", "ÁRVÍZTŰRŐ@PÉLDA.HU"):
            self.assertIn("email", pv.value_patterns(t), t)
        for t in ("a@b", "@twitter", "x@y.z", "user at example dot com", "p<0,05 @ 95%"):
            self.assertNotIn("email", pv.value_patterns(t), t)

    def test_findings_never_contain_values(self):
        taj = "12345678" + str(_cdv("12345678"))
        secrets_ = ["Titkos Teréz", taj, "1971-08-09", "titkos.terez@korhaz.example.hu"]
        rows = [{"Megjegyzés": "Beteg: %s, TAJ %s, szül. %s, %s" % tuple(secrets_), "n1": "12"}]
        f = pv.scan_table(["Megjegyzés", "n1"], rows)
        self.assertEqual(sorted(x["pattern"] for x in f), ["email", "full_date", "taj_cdv"])
        dump = json.dumps(f, ensure_ascii=False) + pv.describe_findings(f)
        for s in secrets_ + ["12345678", "1971", "titkos"]:
            self.assertNotIn(s, dump)
        for x in f:
            self.assertEqual(set(x), {"kind", "column", "column_index", "row_index", "pattern"})

    def test_rows_as_lists_extra_cells_and_limit(self):
        rows = [["a", "x@example.hu", "y@example.hu"]] * 5
        f = pv.scan_table(["study", "email"], rows)
        self.assertIn({"kind": "value", "column": None, "column_index": 2, "row_index": 0, "pattern": "email"}, f)
        self.assertEqual(len(pv.scan_table(["study", "email"], rows, max_findings=3)), 3)
        self.assertEqual(pv.scan_table([], []), [])
        self.assertEqual(pv.scan_table(None, None), [])

    def test_describe_findings(self):
        self.assertEqual(pv.describe_findings([]), "")
        f = pv.scan_table(["TAJ", "x"], [["", "1985-03-12"], ["", "1986-04-13"]])
        msg = pv.describe_findings(f)
        self.assertIn("_privat/", msg)
        self.assertIn("„TAJ”", msg)
        self.assertIn("×2", msg)
        self.assertNotIn("1985", msg)


# ---------------------------------------------------------------------------------------------
# nincs hamis riasztás tipikus kinyerési táblákon
# ---------------------------------------------------------------------------------------------

def _read_csv(path):
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1250")
    delim = csv.Sniffer().sniff(text[:4000], delimiters=",;\t").delimiter
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    return rows[0], rows[1:]


class NoFalsePositiveTest(unittest.TestCase):
    def test_example_csvs_scan_clean(self):
        files = sorted(glob.glob(os.path.join(ROOT, "peldak", "*.csv")))
        self.assertGreaterEqual(len(files), 4)
        for f in files:
            header, rows = _read_csv(f)
            self.assertTrue(rows, f)
            self.assertEqual(pv.scan_table(header, rows), [], os.path.basename(f))

    def test_example_csvs_via_engine_reader_scan_clean(self):
        from metaelemzes import tableio
        for f in sorted(glob.glob(os.path.join(ROOT, "peldak", "*.csv"))):
            rows, meta = tableio.read_table(f)
            header = [meta["mapping"][c] for c in meta["columns"]]
            self.assertEqual(pv.scan_table(header, rows), [], os.path.basename(f))

    def test_typical_hungarian_extraction_table(self):
        header = ["row_uid", "Szerző", "Év", "Ország", "Vizsgálat típusa", "Beavatkozás", "Kontroll",
                  "esemény1", "n1", "esemény2", "n2", "Átlag (SD)", "HR (95% CI)", "Követés (hét)",
                  "Regisztráció", "Forrás", "Megjegyzés", "RoB", "Publikáció dátuma"]
        rows = [
            ["r7k2m4q", "Kovács et al.", "2019", "Magyarország", "RCT", "GLP-1 RA", "placebo", "12", "123",
             "18", "1 234", "5,2 (1,3)", "0,82 (0,71–0,95)", "52", "NCT01234567",
             "doi:10.1016/S0140-6736(19)31234-5", "PMID 31234567; táblázat 2, 45. o.", "alacsony", "2019-03-12"],
            ["r1a2b3c", "Smith & Jones", "2008", "USA", "kohorsz", "metformin", "standard ellátás", "4", "98",
             "7", "101", "61.3 (12.4)", "1.12 [0.88; 1.43]", "26", "ISRCTN12345678",
             "Lancet 2008;371(9612):1123-1130", "p<0,001; n=13 598; 2003-2005 között toborozva", "magas",
             "12 March 2008"],
            ["r9x8y7z", "Nagy, Tóth", "2021", "Németország", "RCT", "SGLT2-gátló", "placebo", "0", "45", "3",
             "44", "–", "0.45 (0.12-0.98)", "12", "EudraCT 2015-001234-56", "Diabetologia 64:1234", "", "", ""],
        ]
        self.assertEqual(pv.scan_table(header, rows), [])

    def test_reference_case_tables_scan_clean(self):
        files = sorted(glob.glob(os.path.join(ROOT, "tests", "reference", "source_examples", "*.json")))
        if not files:
            self.skipTest("nincsenek referencia-esetek")
        n = 0
        for f in files:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            cases = data if isinstance(data, list) else data.get("cases", [])
            for case in cases:
                rows = case.get("rows") or []
                if not rows or not isinstance(rows[0], dict):
                    continue
                header = []
                for r in rows:
                    header.extend(k for k in r if k not in header)
                n += 1
                self.assertEqual(pv.scan_table(header, rows), [], case.get("case_id"))
        self.assertGreater(n, 10)


# ---------------------------------------------------------------------------------------------
# L1: vault-felismerés
# ---------------------------------------------------------------------------------------------

class VaultDetectionTest(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.root = self.tmp / "vaultroot"
        self.env = {}

    def write_config(self, cfg, path=None):
        path = path or self.home / ".claude" / "vault" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg) if not isinstance(cfg, str) else cfg, encoding="utf-8")
        return path

    def vs(self, project, **kw):
        kw.setdefault("platform", "linux")
        return pv.vault_status(project, home=self.home, env=self.env, **kw)

    def test_not_installed(self):
        v = self.vs(self.home / "Documents" / "claude" / "p")
        self.assertFalse(v["installed"])
        self.assertFalse(v["tracked"])
        self.assertIsNone(v["root"])
        self.assertTrue(v["reason"])

    def test_vault_dir_without_config_uses_defaults(self):
        (self.home / ".claude" / "vault").mkdir(parents=True)
        default_root = self.home / "Documents" / "claude"
        v = self.vs(default_root / "apps" / "x")
        self.assertTrue(v["installed"])
        self.assertEqual(Path(v["root"]), default_root)
        self.assertEqual(v["max_depth"], 2)
        self.assertTrue(v["tracked"])
        self.assertEqual(v["depth"], 2)

    def test_matrix(self):
        self.write_config({"root": str(self.root), "max_depth": 2, "exclude": [], "paused": False})
        cases = [
            (self.root / "a", True, True),
            (self.root / "a" / "b", True, True),
            (self.root / "a" / "b" / "c", False, True),
            (self.tmp / "other" / "a", False, False),
            (self.root, True, True),
        ]
        for project, tracked, under in cases:
            v = self.vs(project)
            self.assertEqual(v["tracked"], tracked, str(project))
            self.assertEqual(v["under_root"], under, str(project))
            self.assertTrue(v["reason"])

    def test_deeper_project_inside_git_ancestor(self):
        self.write_config({"root": str(self.root), "max_depth": 2})
        (self.root / "apps" / "repo" / ".git").mkdir(parents=True)
        v = self.vs(self.root / "apps" / "repo" / "ma" / "projekt")
        self.assertTrue(v["tracked"])
        self.assertEqual(v["via"], "apps/repo")
        self.assertEqual(v["depth"], 4)

    def test_exclude_by_name_and_path(self):
        self.write_config({"root": str(self.root), "exclude": ["titkos"]})
        v = self.vs(self.root / "titkos" / "p")
        self.assertFalse(v["tracked"])
        self.assertTrue(v["excluded"])
        self.assertEqual(v["exclude_match"], "titkos")
        self.assertTrue(self.vs(self.root / "nyilt" / "p")["tracked"])
        self.write_config({"root": str(self.root), "exclude": ["apps/b"]})
        self.assertFalse(self.vs(self.root / "apps" / "b")["tracked"])
        self.assertTrue(self.vs(self.root / "apps" / "c")["tracked"])

    def test_paused(self):
        self.write_config({"root": str(self.root), "paused": True})
        v = self.vs(self.root / "a")
        self.assertFalse(v["tracked"])
        self.assertTrue(v["paused"])
        self.assertTrue(v["under_root"])

    def test_max_depth(self):
        self.write_config({"root": str(self.root), "max_depth": 1})
        self.assertTrue(self.vs(self.root / "a")["tracked"])
        self.assertFalse(self.vs(self.root / "a" / "b")["tracked"])
        self.write_config({"root": str(self.root), "max_depth": "sok"})     # érvénytelen → alapérték
        self.assertEqual(self.vs(self.root / "a" / "b")["max_depth"], 2)

    def test_vault_home_env_takes_precedence(self):
        self.write_config({"root": str(self.tmp / "masik")})
        vh = self.tmp / "vh"
        self.write_config({"root": str(self.root)}, vh / "config.json")
        self.env = {"VAULT_HOME": str(vh)}
        self.assertTrue(self.vs(self.root / "a")["tracked"])
        self.assertFalse(self.vs(self.tmp / "masik" / "a")["tracked"])
        self.assertEqual(Path(self.vs(self.root / "a")["config"]), vh / "config.json")
        # a $VAULT_HOME maga is lehet a .json fájl
        self.env = {"VAULT_HOME": str(vh / "config.json")}
        self.assertTrue(self.vs(self.root / "a")["tracked"])

    def test_tilde_and_relative_root(self):
        self.write_config({"root": "~/kutatas"})
        self.assertTrue(self.vs(self.home / "kutatas" / "p")["tracked"])
        self.write_config({"root": "Munka/claude"})
        self.assertTrue(self.vs(self.home / "Munka" / "claude" / "p")["tracked"])

    def test_invalid_config_is_conservative(self):
        self.write_config("{nem json")
        v = self.vs(self.home / "Documents" / "claude" / "p")
        self.assertTrue(v["installed"])
        self.assertTrue(v["error"])
        self.assertTrue(v["tracked"])

    def test_case_insensitive_paths_on_windows_and_mac(self):
        self.write_config({"root": str(self.tmp / "VaultRoot")})
        project = self.tmp / "vaultroot" / "a"
        self.assertTrue(self.vs(project, platform="win32")["tracked"])
        self.assertTrue(self.vs(project, platform="darwin")["tracked"])
        self.assertFalse(self.vs(project, platform="linux")["tracked"])

    def test_symlinked_project_path(self):
        if not POSIX:
            self.skipTest("symlink csak POSIX-on")
        self.write_config({"root": str(self.root)})
        (self.root / "a").mkdir(parents=True)
        link = self.tmp / "link"
        os.symlink(str(self.root / "a"), str(link))
        self.assertTrue(self.vs(link)["tracked"])

    def test_never_modifies_vault_config(self):
        cfg = self.write_config({"root": str(self.root), "max_depth": 2, "exclude": ["x"], "paused": False})
        project = self.root / "p"
        project.mkdir(parents=True)
        before = (cfg.read_bytes(), cfg.stat().st_mtime_ns, sorted(os.listdir(str(cfg.parent))))
        pv.status(project, "C", home=self.home, env=self.env, platform="linux")
        pv.can_write(project, "_privat/x.csv", "C", home=self.home, env=self.env, platform="linux")
        pv.apply_gitignore(project)
        pv.apply_claude_deny(project)
        after = (cfg.read_bytes(), cfg.stat().st_mtime_ns, sorted(os.listdir(str(cfg.parent))))
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------------------------
# L2: kezelt .gitignore-blokk
# ---------------------------------------------------------------------------------------------

class GitignoreBlockTest(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.p = self.make_tmp()
        self.gi = self.p / ".gitignore"

    def test_plan_does_not_write_and_shows_diff(self):
        plan = pv.plan_gitignore(self.p)
        self.assertFalse(self.gi.exists())
        self.assertTrue(plan["changed"])
        self.assertFalse(plan["exists"])
        self.assertIsNone(plan["base_sha256"])
        self.assertIn("--- a/.gitignore", plan["diff"])
        self.assertIn("+" + pv.BLOCK_BEGIN, plan["diff"])
        self.assertIn("+_privat/", plan["diff"])
        self.assertIn("+07_ellenorzes/audit/**/data/", plan["diff"])

    def test_create_new(self):
        res = pv.apply_gitignore(self.p)
        self.assertTrue(res["changed"])
        self.assertEqual(self.gi.read_text(encoding="utf-8"), BLOCK_TEXT)
        for pat in pv.MANAGED_PATTERNS:
            self.assertIn(pat + "\n", BLOCK_TEXT)
        self.assertTrue(pv.gitignore_block_present(self.p))

    def test_user_lines_preserved_and_idempotent(self):
        orig = "node_modules/\n# saját megjegyzés\n*.log\n!fontos.log"      # záró sorvég nélkül
        self.gi.write_bytes(orig.encode("utf-8"))
        pv.apply_gitignore(self.p)
        text = self.gi.read_text(encoding="utf-8")
        self.assertEqual(text, orig + "\n\n" + BLOCK_TEXT)
        data = self.gi.read_bytes()
        again = pv.apply_gitignore(self.p)
        self.assertFalse(again["changed"])
        self.assertEqual(self.gi.read_bytes(), data)
        plan = pv.plan_gitignore(self.p)
        self.assertFalse(plan["changed"])
        self.assertEqual(plan["diff"], "")
        self.assertTrue(plan["block_present"])

    def test_outdated_block_replaced_in_place(self):
        before = "elso\n\n"
        after = "\nutolso # megjegyzés\n  behuzott\n"
        old_block = pv.BLOCK_BEGIN + "\nregi-minta\n_privat/\n" + pv.BLOCK_END + "\n"
        self.gi.write_text(before + old_block + after, encoding="utf-8")
        self.assertFalse(pv.gitignore_block_present(self.p))
        plan = pv.plan_gitignore(self.p)
        self.assertIn("-regi-minta", plan["diff"])
        pv.apply_gitignore(self.p)
        self.assertEqual(self.gi.read_text(encoding="utf-8"), before + BLOCK_TEXT + after)

    def test_duplicate_blocks_collapse(self):
        blk = pv.BLOCK_BEGIN + "\nx\n" + pv.BLOCK_END + "\n"
        self.gi.write_text("a\n" + blk + "b\n" + blk + "c\n", encoding="utf-8")
        pv.apply_gitignore(self.p)
        self.assertEqual(self.gi.read_text(encoding="utf-8"), "a\n" + BLOCK_TEXT + "b\nc\n")

    def test_crlf_and_bom_preserved(self):
        orig = "\ufeffnode_modules/\r\n*.tmp\r\n"
        self.gi.write_bytes(orig.encode("utf-8"))
        pv.apply_gitignore(self.p)
        raw = self.gi.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbfnode_modules/\r\n*.tmp\r\n\r\n"))
        self.assertIn(pv.BLOCK_BEGIN.encode("utf-8") + b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertFalse(pv.apply_gitignore(self.p)["changed"])

    def test_unbalanced_markers_refused(self):
        for bad in (pv.BLOCK_BEGIN + "\n_privat/\n", "x\n" + pv.BLOCK_END + "\n",
                    pv.BLOCK_BEGIN + "\n" + pv.BLOCK_BEGIN + "\n" + pv.BLOCK_END + "\n"):
            self.gi.write_text(bad, encoding="utf-8")
            with self.assertRaises(pv.PrivacyError) as cm:
                pv.apply_gitignore(self.p)
            self.assertEqual(cm.exception.code, "VALIDATION")
            self.assertEqual(self.gi.read_text(encoding="utf-8"), bad)
            self.assertFalse(pv.gitignore_block_present(self.p))

    def test_stale_preview_conflict(self):
        self.gi.write_text("a\n", encoding="utf-8")
        plan = pv.plan_gitignore(self.p)
        self.gi.write_text("a\nb\n", encoding="utf-8")
        with self.assertRaises(pv.PrivacyError) as cm:
            pv.apply_gitignore(self.p, base_sha256=plan["base_sha256"])
        self.assertEqual(cm.exception.code, "CONFLICT")
        self.assertEqual(cm.exception.http, 409)
        self.assertEqual(self.gi.read_text(encoding="utf-8"), "a\nb\n")
        # az előnézetkor nem létező fájl is ütközés, ha közben létrejött
        self.gi.unlink()
        plan = pv.plan_gitignore(self.p)
        self.gi.write_text("c\n", encoding="utf-8")
        with self.assertRaises(pv.PrivacyError):
            pv.apply_gitignore(self.p, base_sha256=plan["base_sha256"])
        plan = pv.plan_gitignore(self.p)
        self.assertTrue(pv.apply_gitignore(self.p, base_sha256=plan["base_sha256"])["changed"])

    def test_non_utf8_refused(self):
        self.gi.write_bytes(b"\xff\xfe\x00a")
        with self.assertRaises(pv.PrivacyError):
            pv.apply_gitignore(self.p)

    def test_missing_project(self):
        with self.assertRaises(pv.PrivacyError) as cm:
            pv.plan_gitignore(self.p / "nincs")
        self.assertEqual(cm.exception.code, "NOT_FOUND")

    @unittest.skipUnless(POSIX, "symlink csak POSIX-on")
    def test_symlink_refused(self):
        target = self.p / "masik"
        target.write_text("x\n", encoding="utf-8")
        os.symlink(str(target), str(self.gi))
        with self.assertRaises(pv.PrivacyError):
            pv.apply_gitignore(self.p)
        self.assertEqual(target.read_text(encoding="utf-8"), "x\n")

    @unittest.skipUnless(POSIX, "jogosultság csak POSIX-on")
    def test_mode_preserved(self):
        self.gi.write_text("a\n", encoding="utf-8")
        os.chmod(str(self.gi), 0o640)
        pv.apply_gitignore(self.p)
        self.assertEqual(self.gi.stat().st_mode & 0o777, 0o640)
        self.assertEqual([n for n in os.listdir(str(self.p)) if n.endswith(".tmp")], [])

    def test_pattern_matcher_semantics(self):
        yes = ["_privat/x.csv", "a/_privat/b/c.csv", "03_adatok/beteg_PHI.csv", "x_PHI/adat.csv",
               "03_adatok/k.phi.csv", "07_ellenorzes/audit/2026-10-04/data/t.csv",
               "07_ellenorzes/audit/data/t.csv", "07_ellenorzes/audit/a/b/data/t.csv", "pillanat.snapshot.html",
               "projekt.sqlite-wal", "sub/projekt.sqlite-shm"]
        no = ["_privat", "privat/x.csv", "03_adatok/x.csv", "x/07_ellenorzes/audit/2026/data/t.csv",
              "07_ellenorzes/audit/2026/data", "07_ellenorzes/activity.jsonl", "projekt.sqlite",
              "snapshot.html", "phi.csv", "03_adatok/PHI.csv"]
        for p in yes:
            self.assertTrue(pv.is_sensitive_path(p, ignore_case=False), p)
        for p in no:
            self.assertFalse(pv.is_sensitive_path(p, ignore_case=False), p)
        self.assertFalse(pv.is_sensitive_path("_PRIVAT/x.csv", ignore_case=False))
        self.assertTrue(pv.is_sensitive_path("_PRIVAT/x.csv", ignore_case=True))


@unittest.skipUnless(HAS_GIT, "git nem érhető el")
class GitignoreWithGitTest(GitMixin, unittest.TestCase):
    def setUp(self):
        self.setup_git_env()
        self.repo = self.init_repo(self.make_tmp() / "repo")

    def test_git_agrees_with_block(self):
        proj = self.repo / "sub" / "proj"
        proj.mkdir(parents=True)
        self.assertEqual(pv.is_ignored(proj, "_privat/x.csv"), (False, "git"))
        pv.apply_gitignore(proj)
        for rel in ("_privat/x.csv", "03_adatok/a_PHI.csv", "07_ellenorzes/audit/2026/data/t.csv",
                    "projekt.sqlite-wal", "export.snapshot.html"):
            self.assertEqual(pv.is_ignored(proj, rel), (True, "git"), rel)
            self.assertTrue(pv.is_sensitive_path(rel), rel)
        self.assertEqual(pv.is_ignored(proj, "03_adatok/a.csv"), (False, "git"))
        # 'git add -A' a blokk mellett nem veszi fel az érzékeny fájlt
        f = proj / "_privat" / "kohorsz.csv"
        f.parent.mkdir()
        f.write_text("x\n", encoding="utf-8")
        (proj / "03_adatok").mkdir()
        (proj / "03_adatok" / "a.csv").write_text("study\n", encoding="utf-8")
        self.git(self.repo, "add", "-A")
        staged = self.git(self.repo, "diff", "--cached", "--name-only").stdout.decode()
        self.assertIn("sub/proj/03_adatok/a.csv", staged)
        self.assertNotIn("_privat", staged)


# ---------------------------------------------------------------------------------------------
# Claude deny-szabály
# ---------------------------------------------------------------------------------------------

class ClaudeDenyTest(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.p = self.make_tmp()
        self.f = self.p / ".claude" / "settings.json"

    def test_create_new(self):
        plan = pv.plan_claude_deny(self.p)
        self.assertFalse(self.f.exists())
        self.assertTrue(plan["changed"])
        self.assertEqual(plan["missing_rules"], list(pv.DENY_RULES))
        self.assertIn('+    "deny": [', plan["diff"])
        self.assertFalse(pv.deny_rule_present(self.p))
        res = pv.apply_claude_deny(self.p)
        self.assertTrue(res["changed"])
        self.assertEqual(json.loads(self.f.read_text(encoding="utf-8")),
                         {"permissions": {"deny": ["Read(./_privat/**)", "Read(**/*_PHI*)"]}})
        self.assertTrue(pv.deny_rule_present(self.p))

    def test_merge_preserves_other_keys_order_and_indent(self):
        existing = {
            "env": {"FOO": "1"},
            "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(./titok/**)"], "ask": []},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo é"}]}]},
            "model": "x",
        }
        self.f.parent.mkdir()
        self.f.write_text(json.dumps(existing, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        pv.apply_claude_deny(self.p)
        text = self.f.read_text(encoding="utf-8")
        got = json.loads(text)
        self.assertEqual(list(got), ["env", "permissions", "hooks", "model"])
        self.assertEqual(list(got["permissions"]), ["allow", "deny", "ask"])
        self.assertEqual(got["permissions"]["deny"], ["Read(./titok/**)"] + list(pv.DENY_RULES))
        for k in ("env", "hooks", "model"):
            self.assertEqual(got[k], existing[k])
        self.assertEqual(got["permissions"]["allow"], ["Bash(ls)"])
        self.assertIn('\n    "env"', text)          # a 4 szóközös behúzás megmaradt
        self.assertIn("echo é", text)
        data = self.f.read_bytes()
        self.assertFalse(pv.apply_claude_deny(self.p)["changed"])
        self.assertEqual(self.f.read_bytes(), data)
        self.assertEqual(pv.plan_claude_deny(self.p)["diff"], "")

    def test_partial_rules_and_no_permissions_key(self):
        self.f.parent.mkdir()
        self.f.write_text('{"permissions": {"deny": ["Read(**/*_PHI*)"]}}', encoding="utf-8")
        self.assertEqual(pv.plan_claude_deny(self.p)["missing_rules"], ["Read(./_privat/**)"])
        pv.apply_claude_deny(self.p)
        self.assertEqual(json.loads(self.f.read_text(encoding="utf-8"))["permissions"]["deny"],
                         ["Read(**/*_PHI*)", "Read(./_privat/**)"])
        self.f.write_text('{"theme": "dark"}', encoding="utf-8")
        pv.apply_claude_deny(self.p)
        got = json.loads(self.f.read_text(encoding="utf-8"))
        self.assertEqual(list(got), ["theme", "permissions"])

    def test_invalid_existing_files_untouched(self):
        self.f.parent.mkdir()
        for bad in ('{"permissions": ', '{"a": 1, "a": 2}', '[1, 2]', '{"permissions": []}',
                    '{"permissions": {"deny": "Read(x)"}}'):
            self.f.write_text(bad, encoding="utf-8")
            with self.assertRaises(pv.PrivacyError) as cm:
                pv.apply_claude_deny(self.p)
            self.assertEqual(cm.exception.code, "VALIDATION", bad)
            self.assertEqual(self.f.read_text(encoding="utf-8"), bad)
            self.assertFalse(pv.deny_rule_present(self.p))

    def test_rules_split_across_local_settings(self):
        self.f.parent.mkdir()
        self.f.write_text('{"permissions": {"deny": ["Read(./_privat/**)"]}}', encoding="utf-8")
        self.assertFalse(pv.deny_rule_present(self.p))
        (self.p / ".claude" / "settings.local.json").write_text(
            '{"permissions": {"deny": ["Read(**/*_PHI*)"]}}', encoding="utf-8")
        self.assertTrue(pv.deny_rule_present(self.p))

    def test_stale_preview_conflict(self):
        plan = pv.plan_claude_deny(self.p)
        self.f.parent.mkdir()
        self.f.write_text("{}", encoding="utf-8")
        with self.assertRaises(pv.PrivacyError) as cm:
            pv.apply_claude_deny(self.p, base_sha256=plan["base_sha256"])
        self.assertEqual(cm.exception.code, "CONFLICT")
        self.assertEqual(self.f.read_text(encoding="utf-8"), "{}")


# ---------------------------------------------------------------------------------------------
# írás-tartás (can_write)
# ---------------------------------------------------------------------------------------------

class CanWriteTest(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.home = self.tmp / "home"
        self.root = self.tmp / "vaultroot"
        cfg = self.home / ".claude" / "vault" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"root": str(self.root), "max_depth": 2}), encoding="utf-8")
        self.cfg = cfg
        self.inside = self.root / "reviews" / "glp1"
        self.outside = self.tmp / "helyi" / "glp1"
        self.inside.mkdir(parents=True)
        self.outside.mkdir(parents=True)

    def cw(self, project, rel, dc, **kw):
        return pv.can_write(project, rel, dc, home=self.home, env={}, platform="linux", **kw)

    def test_matrix_without_git(self):
        # (projekt, cél, osztály, blokk?, várt)
        cases = [
            (self.inside, "03_adatok/a.csv", "A", False, True),
            (self.inside, "_privat/a.csv", "A", False, True),
            (self.outside, "03_adatok/a.csv", "B", False, True),
            (self.outside, "_privat/a.csv", "B", False, True),
            (self.inside, "03_adatok/a.csv", "B", False, False),
            (self.inside, "_privat/a.csv", "B", False, False),
            (self.inside, "_privat/a.csv", "B", True, True),
            (self.inside, "03_adatok/a.csv", "B", True, False),
            (self.inside, "03_adatok/a_PHI.csv", "B", True, True),
            (self.outside, "03_adatok/a.csv", "C", False, False),
            (self.outside, "_privat/a.csv", "C", False, True),
            (self.inside, "_privat/a.csv", "C", False, False),
            (self.inside, "_privat/kohorsz/a.csv", "C", True, True),
            (self.inside, "03_adatok/a_PHI.csv", "C", True, False),
        ]
        for project, rel, dc, block, want in cases:
            gi = project / ".gitignore"
            if block:
                pv.apply_gitignore(project)
            elif gi.exists():
                gi.unlink()
            ok, reason = self.cw(project, rel, dc)
            self.assertEqual(ok, want, (str(project), rel, dc, block, reason))
            self.assertIsInstance(reason, str)
            self.assertTrue(reason)

    def test_refusal_offers_two_options(self):
        ok, reason = self.cw(self.inside, "_privat/a.csv", "B")
        self.assertFalse(ok)
        self.assertIn(".gitignore-blokk", reason)
        self.assertIn("áthelyezés", reason)

    def test_consent_only_for_b(self):
        self.assertTrue(self.cw(self.inside, "03_adatok/a.csv", "B", consent=True)[0])
        self.assertFalse(self.cw(self.inside, "_privat/a.csv", "C", consent=True)[0])

    def test_phi_detected_forces_private(self):
        pv.apply_gitignore(self.inside)
        self.assertFalse(self.cw(self.outside, "03_adatok/a.csv", "A", phi_detected=True)[0])
        self.assertTrue(self.cw(self.outside, "_privat/a.csv", "A", phi_detected=True)[0])
        self.assertFalse(self.cw(self.inside, "03_adatok/a.csv", "B", consent=True, phi_detected=True)[0])
        self.assertTrue(self.cw(self.inside, "_privat/a.csv", "B", phi_detected=True)[0])

    def test_paused_or_excluded_vault_releases_hold(self):
        self.cfg.write_text(json.dumps({"root": str(self.root), "paused": True}), encoding="utf-8")
        self.assertTrue(self.cw(self.inside, "03_adatok/a.csv", "B")[0])
        self.cfg.write_text(json.dumps({"root": str(self.root), "exclude": ["glp1"]}), encoding="utf-8")
        self.assertTrue(self.cw(self.inside, "03_adatok/a.csv", "B")[0])
        self.assertFalse(self.cw(self.inside, "03_adatok/a.csv", "C")[0])     # C: csak _privat/

    def test_negation_line_is_conservative_without_git(self):
        pv.apply_gitignore(self.inside)
        gi = self.inside / ".gitignore"
        gi.write_text(gi.read_text(encoding="utf-8") + "!_privat/megosztott.csv\n", encoding="utf-8")
        self.assertFalse(self.cw(self.inside, "_privat/a.csv", "B")[0])

    def test_invalid_paths_and_class(self):
        for rel in ("../a.csv", "/etc/x", "a\\b.csv", "", "C:/x.csv", "_privat/../a.csv", "./_privat/a.csv",
                    None, "_privat", ".git/hooks/pre-commit", "_privat/.GIT/x"):
            self.assertFalse(self.cw(self.outside, rel, "C")[0], rel)
        with self.assertRaises(pv.PrivacyError):
            self.cw(self.outside, "a.csv", "D")
        with self.assertRaises(pv.PrivacyError):
            self.cw(self.outside, "a.csv", "")
        self.assertTrue(self.cw(self.outside, "03_adatok/a.csv", "a")[0])

    def test_private_dir_case_on_windows(self):
        self.assertTrue(pv.can_write(self.outside, "_PRIVAT/a.csv", "C", home=self.home, env={},
                                     platform="win32")[0])
        self.assertFalse(self.cw(self.outside, "_PRIVAT/a.csv", "C")[0])


@unittest.skipUnless(HAS_GIT, "git nem érhető el")
class CanWriteWithGitTest(GitMixin, unittest.TestCase):
    def setUp(self):
        self.setup_git_env()
        tmp = self.make_tmp()
        self.home = tmp / "home"
        self.root = tmp / "vaultroot"
        cfg = self.home / ".claude" / "vault" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"root": str(self.root)}), encoding="utf-8")
        self.repo = self.init_repo(self.root / "apps" / "repo")
        self.proj = self.repo / "projekt"
        self.proj.mkdir()

    def cw(self, rel, dc):
        return pv.can_write(self.proj, rel, dc, home=self.home, env={}, platform="linux")

    def test_check_ignore_and_tracked_file(self):
        self.assertFalse(self.cw("_privat/a.csv", "C")[0])
        pv.apply_gitignore(self.proj)
        self.assertTrue(self.cw("_privat/a.csv", "C")[0])
        self.assertTrue(self.cw("_privat/a.csv", "B")[0])
        self.assertFalse(self.cw("03_adatok/a.csv", "B")[0])
        # a már követett fájl nem ignorált, akkor sem, ha illik a mintára
        self.commit_file(self.repo, "projekt/_privat/regi.csv", force=True)
        self.assertFalse(self.cw("_privat/regi.csv", "C")[0])
        self.assertEqual(pv.is_ignored(self.proj, "_privat/regi.csv"), (False, "git"))
        self.assertTrue(self.cw("_privat/uj.csv", "C")[0])


# ---------------------------------------------------------------------------------------------
# L3: pre-commit őr
# ---------------------------------------------------------------------------------------------

class HookScriptTest(unittest.TestCase):
    def load_hook(self):
        ns = {"__name__": "ma_hook_teszt", "__file__": "pre-commit"}
        exec(compile(pv.precommit_hook_script(), "pre-commit", "exec"), ns)
        return ns

    def test_script_is_stdlib_python_with_marker(self):
        script = pv.precommit_hook_script()
        self.assertTrue(script.startswith("#!"))
        self.assertIn(pv.HOOK_MARKER, script[:600])
        tree = ast.parse(script)
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertLessEqual(mods, {"fnmatch", "os", "shutil", "subprocess", "sys"})
        self.assertTrue(pv.precommit_hook_script("/opt/py/bin/python3").startswith("#!/opt/py/bin/python3\n"))

    def test_hook_matcher_agrees_with_module(self):
        is_sensitive = self.load_hook()["is_sensitive"]
        paths = ["_privat/x.csv", "a/_privat/b.csv", "03_adatok/x_PHI.csv", "x.phi.json", "projekt.sqlite-wal",
                 "07_ellenorzes/audit/d/data/t.csv", "pillanat.snapshot.html", "_privat", "03_adatok/x.csv",
                 "README.md", "07_ellenorzes/activity.jsonl", "privat/x.csv"]
        for p in paths:
            self.assertEqual(is_sensitive(p), pv.is_sensitive_path(p, ignore_case=sys.platform in ("win32", "darwin")), p)
        # a hook a repó gyökeréhez relatív utat kap: a projekt bármely mélységben lehet
        self.assertTrue(is_sensitive("apps/proj/07_ellenorzes/audit/2026/data/t.csv"))
        self.assertTrue(is_sensitive("apps/proj/_privat/kohorsz.csv"))
        self.assertFalse(is_sensitive("apps/proj/03_adatok/a.csv"))


@unittest.skipUnless(HAS_GIT and POSIX, "git + POSIX hookfuttatás kell")
class PrecommitGuardTest(GitMixin, unittest.TestCase):
    def setUp(self):
        self.setup_git_env()
        self.repo = self.init_repo(self.make_tmp() / "repo")
        self.commit_file(self.repo, "README.md", "x\n")
        self.hooks = self.repo / ".git" / "hooks"

    def write_foreign_hook(self, exit_code=0):
        marker = self.repo.parent / "regi-hook-futott"
        hook = self.hooks / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!%s\nimport pathlib, sys\npathlib.Path(%r).write_text('ok')\nsys.exit(%d)\n"
                        % (sys.executable, str(marker), exit_code), encoding="utf-8")
        os.chmod(str(hook), 0o755)
        return hook, marker

    def try_commit(self, rel, force=False):
        f = self.repo.joinpath(*rel.split("/"))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("adat\n", encoding="utf-8")
        self.git(self.repo, "add", *(["-f"] if force else []), "--", rel)
        return self.git(self.repo, "commit", "-q", "-m", "teszt", check=False)

    def test_plan_does_not_write(self):
        plan = pv.plan_precommit_guard(self.repo)
        self.assertFalse(plan["installed"])
        self.assertFalse(plan["will_chain"])
        self.assertFalse((self.hooks / "pre-commit").exists())
        self.assertIn(pv.HOOK_MARKER, plan["script"])
        self.assertIn("tartalék", plan["note"])
        self.assertFalse(pv.precommit_guard_installed(self.repo))

    def test_blocks_only_forced_sensitive_files(self):
        pv.apply_gitignore(self.repo)
        self.git(self.repo, "add", ".gitignore")
        self.git(self.repo, "commit", "-q", "-m", "gi")
        res = pv.install_precommit_guard(self.repo)
        self.assertTrue(res["installed"])
        self.assertTrue(pv.precommit_guard_installed(self.repo))
        self.assertTrue(os.access(str(self.hooks / "pre-commit"), os.X_OK))
        self.assertEqual(self.try_commit("03_adatok/a.csv").returncode, 0)
        p = self.try_commit("_privat/kohorsz.csv", force=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("ma-munkapad", p.stderr.decode("utf-8", "replace"))
        self.assertIn("_privat/kohorsz.csv", p.stderr.decode("utf-8", "replace"))
        self.git(self.repo, "rm", "-q", "--cached", "--", "_privat/kohorsz.csv")
        self.assertEqual(self.try_commit("03_adatok/b.csv").returncode, 0)
        # korábban követett érzékeny fájl módosítása is megáll
        self.git(self.repo, "commit", "-q", "--allow-empty", "-m", "x")
        self.commit_file(self.repo, "03_adatok/regi_PHI.csv", force=True)       # --no-verify: az őr kikerülve
        (self.repo / "03_adatok" / "regi_PHI.csv").write_text("uj\n", encoding="utf-8")
        self.git(self.repo, "add", "-u")
        self.assertNotEqual(self.git(self.repo, "commit", "-q", "-m", "m", check=False).returncode, 0)
        # törlése viszont engedett
        self.git(self.repo, "reset", "-q")
        self.git(self.repo, "rm", "-q", "--cached", "--", "03_adatok/regi_PHI.csv")
        self.assertEqual(self.git(self.repo, "commit", "-q", "-m", "torles", check=False).returncode, 0)

    def test_chains_existing_hook_and_reinstall_is_idempotent(self):
        hook, marker = self.write_foreign_hook(0)
        original = hook.read_bytes()
        self.assertTrue(pv.plan_precommit_guard(self.repo)["will_chain"])
        res = pv.install_precommit_guard(self.repo)
        chained = self.hooks / pv.CHAINED_HOOK_NAME
        self.assertEqual(Path(res["chained_path"]), chained)
        self.assertEqual(chained.read_bytes(), original)
        self.assertEqual(self.try_commit("a.txt").returncode, 0)
        self.assertTrue(marker.exists())
        res2 = pv.install_precommit_guard(self.repo)
        self.assertTrue(res2["updated"])
        self.assertEqual(chained.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in self.hooks.iterdir()
                                if p.name.startswith("pre-commit") and not p.name.endswith(".sample")),
                         ["pre-commit", pv.CHAINED_HOOK_NAME])
        un = pv.uninstall_precommit_guard(self.repo)
        self.assertEqual(un, {"removed": True, "restored": True})
        self.assertEqual(hook.read_bytes(), original)
        self.assertFalse(chained.exists())

    def test_chained_failure_propagates(self):
        self.write_foreign_hook(1)
        pv.install_precommit_guard(self.repo)
        self.assertNotEqual(self.try_commit("a.txt").returncode, 0)

    def test_uninstall_without_chain(self):
        pv.install_precommit_guard(self.repo)
        self.assertEqual(pv.uninstall_precommit_guard(self.repo), {"removed": True, "restored": False})
        self.assertFalse((self.hooks / "pre-commit").exists())
        self.assertEqual(pv.uninstall_precommit_guard(self.repo), {"removed": False, "restored": False})

    def test_shared_hooks_path_refused(self):
        shared = self.make_tmp() / "kozos-hookok"
        self.git(self.repo, "config", "core.hooksPath", str(shared))
        self.assertTrue(pv.plan_precommit_guard(self.repo)["shared_hooks_dir"])
        with self.assertRaises(pv.PrivacyError) as cm:
            pv.install_precommit_guard(self.repo)
        self.assertEqual(cm.exception.code, "CONFLICT")
        self.assertFalse(shared.exists())

    def test_project_subdirectory_uses_repo_hooks(self):
        proj = self.repo / "apps" / "proj"
        proj.mkdir(parents=True)
        pv.install_precommit_guard(proj)
        self.assertTrue((self.hooks / "pre-commit").exists())
        self.assertTrue(pv.precommit_guard_installed(proj))
        self.assertNotEqual(self.try_commit("apps/proj/_privat/x.csv", force=True).returncode, 0)

    def test_not_a_repo(self):
        with self.assertRaises(pv.PrivacyError):
            pv.install_precommit_guard(self.make_tmp())
        self.assertFalse(pv.precommit_guard_installed(self.make_tmp()))


# ---------------------------------------------------------------------------------------------
# L1 követett érzékeny fájlok és L6 „már felment?”
# ---------------------------------------------------------------------------------------------

@unittest.skipUnless(HAS_GIT, "git nem érhető el")
class HistoryTest(GitMixin, unittest.TestCase):
    def setUp(self):
        self.setup_git_env()
        self.repo = self.init_repo(self.make_tmp() / "repo")

    def test_empty_and_clean_repo(self):
        r = pv.already_pushed(self.repo)
        self.assertTrue(r["checked"])
        self.assertFalse(r["in_history"])
        self.commit_file(self.repo, "03_adatok/a.csv")
        r = pv.already_pushed(self.repo)
        self.assertTrue(r["checked"])
        self.assertTrue(r["git"] and r["repo"])
        self.assertFalse(r["in_history"])
        self.assertEqual(r["commits"], 0)
        self.assertEqual(pv.tracked_sensitive_files(self.repo), [])

    def test_sensitive_paths_found_even_after_removal(self):
        self.commit_file(self.repo, "README.md")
        self.commit_file(self.repo, "_privat/kohorsz.csv", force=True)
        self.commit_file(self.repo, "03_adatok/x_PHI/adat.csv", force=True)
        self.assertEqual(sorted(pv.tracked_sensitive_files(self.repo)),
                         ["03_adatok/x_PHI/adat.csv", "_privat/kohorsz.csv"])
        self.git(self.repo, "rm", "-q", "--cached", "--", "_privat/kohorsz.csv")
        self.git(self.repo, "commit", "-q", "-m", "ki")
        self.assertEqual(pv.tracked_sensitive_files(self.repo), ["03_adatok/x_PHI/adat.csv"])
        r = pv.already_pushed(self.repo)
        self.assertTrue(r["in_history"])
        self.assertEqual(r["commits"], 3)
        self.assertIn("_privat/kohorsz.csv", r["paths"])
        self.assertIn("03_adatok/x_PHI/adat.csv", r["paths"])
        self.assertNotIn("README.md", r["paths"])
        self.assertIs(r["in_remote"], False)

    def test_explicit_paths_and_remote(self):
        self.commit_file(self.repo, "03_adatok/a.csv")
        r = pv.already_pushed(self.repo, paths=["03_adatok/a.csv"])
        self.assertTrue(r["in_history"])
        self.assertFalse(pv.already_pushed(self.repo, paths=["03_adatok/b.csv"])["in_history"])
        with self.assertRaises(pv.PrivacyError):
            pv.already_pushed(self.repo, paths=["../x"])
        remote = self.make_tmp() / "remote.git"
        self.git(remote.parent, "init", "-q", "--bare", str(remote))
        self.git(self.repo, "remote", "add", "origin", str(remote))
        self.git(self.repo, "push", "-q", "origin", "HEAD:refs/heads/main")
        self.git(self.repo, "fetch", "-q", "origin")
        self.assertIs(pv.already_pushed(self.repo, paths=["03_adatok/a.csv"])["in_remote"], True)

    def test_project_subdirectory_scope(self):
        self.commit_file(self.repo, "masik/_privat/x.csv", force=True)
        proj = self.repo / "proj"
        proj.mkdir()
        self.assertFalse(pv.already_pushed(proj)["in_history"])
        self.assertEqual(pv.tracked_sensitive_files(proj), [])
        self.commit_file(self.repo, "proj/_privat/y.csv", force=True)
        r = pv.already_pushed(proj)
        self.assertTrue(r["in_history"])
        self.assertEqual(r["paths"], ["_privat/y.csv"])
        self.assertEqual(pv.tracked_sensitive_files(proj), ["_privat/y.csv"])

    def test_not_a_repo(self):
        r = pv.already_pushed(self.make_tmp())
        self.assertTrue(r["git"])
        self.assertFalse(r["repo"])
        self.assertFalse(r["in_history"])


class GitUnavailableTest(TempDirMixin, unittest.TestCase):
    def test_missing_git_is_graceful(self):
        p = self.make_tmp()
        with mock.patch.object(pv.subprocess, "run", side_effect=FileNotFoundError("git")):
            r = pv.already_pushed(p)
            self.assertFalse(r["git"])
            self.assertFalse(r["checked"])
            self.assertTrue(r["reason"])
            self.assertEqual(pv.git_info(p)["available"], False)
            self.assertEqual(pv.tracked_sensitive_files(p), [])
            self.assertEqual(pv.is_ignored(p, "_privat/x.csv"), (False, "pattern"))
            pv.apply_gitignore(p)
            self.assertEqual(pv.is_ignored(p, "_privat/x.csv"), (True, "pattern"))
            self.assertFalse(pv.precommit_guard_installed(p))
            st = pv.status(p, "B", home=p / "home", env={}, platform="linux")
            self.assertFalse(st["git"]["available"])
            json.dumps(st)

    def test_timeout_is_graceful(self):
        p = self.make_tmp()
        with mock.patch.object(pv.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 1)):
            r = pv.already_pushed(p)
            self.assertFalse(r["checked"])

    def test_git_called_without_shell_and_with_timeout(self):
        p = self.make_tmp()
        calls = []

        def fake(argv, **kw):
            calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, 128, b"", b"")
        with mock.patch.object(pv.subprocess, "run", side_effect=fake):
            pv.already_pushed(p)
            pv.is_ignored(p, "_privat/x.csv")
        self.assertTrue(calls)
        for argv, kw in calls:
            self.assertIsInstance(argv, list)
            self.assertEqual(argv[0], "git")
            self.assertFalse(kw.get("shell"))
            self.assertTrue(kw.get("timeout"))
            self.assertIn("GIT_OPTIONAL_LOCKS", kw.get("env"))


# ---------------------------------------------------------------------------------------------
# felhőszinkron
# ---------------------------------------------------------------------------------------------

class CloudSyncTest(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()

    def test_onedrive_env_and_known_folder_move(self):
        od = self.tmp / "OneDrive"
        env = {"OneDrive": str(od)}
        reg = {"Personal": "%OneDrive%/Documents"}
        r = pv.detect_cloud_sync(od / "Documents" / "claude" / "p", env=env, platform="win32", registry=reg)
        self.assertEqual(r["kind"], "onedrive")
        self.assertEqual(Path(r["path"]), od)
        self.assertTrue(r["known_folder_move"])
        self.assertIsNone(pv.detect_cloud_sync(self.tmp / "helyi" / "p", env=env, platform="win32",
                                               registry=reg))
        r = pv.detect_cloud_sync(od / "x", env={"ONEDRIVE": str(od)}, platform="win32", registry={})
        self.assertEqual(r["kind"], "onedrive")
        self.assertFalse(r["known_folder_move"])

    def test_onedrive_from_registry_only(self):
        docs = self.tmp / "Users" / "szili" / "OneDrive - Egyetem" / "Documents"
        r = pv.detect_cloud_sync(docs / "claude" / "p", env={}, platform="win32", registry={"Personal": str(docs)})
        self.assertEqual(r["kind"], "onedrive")
        self.assertEqual(Path(r["path"]), self.tmp / "Users" / "szili" / "OneDrive - Egyetem")
        prof = self.tmp / "u"
        r = pv.detect_cloud_sync(prof / "OneDrive" / "Documents" / "p", env={"USERPROFILE": str(prof)},
                                 platform="win32", registry={"Personal": "%USERPROFILE%/OneDrive/Documents"})
        self.assertEqual(r["kind"], "onedrive")
        # nincs átirányítás: a helyi Documents nem szinkronizált
        self.assertIsNone(pv.detect_cloud_sync(prof / "Documents" / "p", env={"USERPROFILE": str(prof)},
                                               platform="win32", registry={"Personal": "%USERPROFILE%/Documents"}))

    def test_winreg_is_lazy_and_optional(self):
        with mock.patch.dict(sys.modules, {"winreg": None}):
            self.assertEqual(pv._read_shell_folders(), {})
        src = Path(pv.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        top = [a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names]
        self.assertNotIn("winreg", top)

    def test_icloud_desktop_and_documents(self):
        home = self.tmp / "home"
        drive = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        self.assertIsNone(pv.detect_cloud_sync(home / "Documents" / "claude" / "p", home=home, platform="darwin"))
        r = pv.detect_cloud_sync(drive / "x" / "p", home=home, platform="darwin")
        self.assertEqual(r["kind"], "icloud")
        (drive / "Documents").mkdir(parents=True)
        for base in ("Documents", "Desktop"):
            r = pv.detect_cloud_sync(home / base / "claude" / "p", home=home, platform="darwin")
            self.assertEqual(r["kind"], "icloud", base)
            self.assertEqual(Path(r["path"]), home / base)
            self.assertTrue(r["desktop_documents"])
        self.assertIsNone(pv.detect_cloud_sync(home / "Projektek" / "p", home=home, platform="darwin"))

    def test_linux_none(self):
        self.assertIsNone(pv.detect_cloud_sync(self.tmp, home=self.tmp, env={"OneDrive": str(self.tmp)},
                                               platform="linux"))


# ---------------------------------------------------------------------------------------------
# összesített állapot
# ---------------------------------------------------------------------------------------------

class StatusTest(GitMixin, unittest.TestCase):
    def setUp(self):
        self.setup_git_env()
        self.tmp = self.make_tmp()
        self.home = self.tmp / "home"
        self.root = self.home / "Documents" / "claude"
        cfg = self.home / ".claude" / "vault" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"root": "~/Documents/claude", "max_depth": 2}), encoding="utf-8")
        self.proj = self.root / "reviews" / "glp1"
        self.proj.mkdir(parents=True)

    def st(self, project, dc, **kw):
        kw.setdefault("platform", "linux")
        return pv.status(project, dc, home=self.home, env={}, **kw)

    def snapshot(self, d):
        out = {}
        for p in sorted(Path(d).rglob("*")):
            if ".git" in p.parts:
                continue
            out[str(p)] = p.read_bytes() if p.is_file() else None
        return out

    def test_b_class_under_vault_without_protection(self):
        before = self.snapshot(self.proj)
        s = self.st(self.proj, "b")
        self.assertEqual(self.snapshot(self.proj), before)          # az állapot-lekérdezés nem ír
        json.loads(json.dumps(s, ensure_ascii=False))
        self.assertEqual(s["data_class"], "B")
        self.assertTrue(s["vault"]["tracked"])
        self.assertEqual(Path(s["vault"]["root"]), self.root)
        self.assertIsNone(s["cloud_sync"])
        self.assertFalse(s["gitignore_block_present"])
        self.assertFalse(s["deny_rule_present"])
        self.assertTrue(s["write_hold"]["active"])
        self.assertIn(".gitignore", s["write_hold"]["reason"])
        self.assertFalse(s["open_blocked"]["blocked"])
        ids = [a["id"] for a in s["actions"]]
        self.assertIn("gitignore", ids)
        self.assertIn("deny", ids)
        self.assertTrue(s["recommendations"])
        joined = " ".join(s["recommendations"])
        self.assertIn("vault pause", joined)
        self.assertIn("exclude", joined)
        self.assertIn("Read(./_privat/**)", joined)
        for key in ("data_class", "vault", "cloud_sync", "gitignore_block_present", "deny_rule_present",
                    "tracked_sensitive_files", "write_hold", "recommendations"):
            self.assertIn(key, s)
        for key in ("tracked", "root", "reason"):
            self.assertIn(key, s["vault"])

        pv.apply_gitignore(self.proj)
        pv.apply_claude_deny(self.proj)
        s = self.st(self.proj, "B")
        self.assertFalse(s["write_hold"]["active"])
        self.assertTrue(s["gitignore_block_present"])
        self.assertTrue(s["deny_rule_present"])
        self.assertEqual([a["id"] for a in s["actions"]], [])

    def test_a_class_outside_vault(self):
        p = self.tmp / "helyi" / "proj"
        p.mkdir(parents=True)
        s = self.st(p, "A")
        self.assertFalse(s["vault"]["tracked"])
        self.assertFalse(s["write_hold"]["active"])
        self.assertFalse(s["open_blocked"]["blocked"])
        self.assertEqual(s["recommendations"], [])
        self.assertEqual(s["actions"], [])

    @unittest.skipUnless(HAS_GIT, "git nem érhető el")
    def test_c_class_open_blocked_until_protected(self):
        repo = self.init_repo(self.proj)
        s = self.st(repo, "C")
        self.assertTrue(s["open_blocked"]["blocked"])
        self.assertEqual(len(s["open_blocked"]["reasons"]), 2)
        self.assertIn("precommit", [a["id"] for a in s["actions"]])
        pv.apply_gitignore(repo)
        pv.install_precommit_guard(repo)
        s = self.st(repo, "C")
        self.assertTrue(s["precommit_guard_installed"])
        self.assertFalse(s["open_blocked"]["blocked"], s["open_blocked"])
        self.assertFalse(s["write_hold"]["active"])
        # követett érzékeny fájl → újra blokkolt, és L6 piros riasztás
        self.commit_file(repo, "_privat/kohorsz.csv", force=True)
        s = self.st(repo, "C")
        self.assertTrue(s["open_blocked"]["blocked"])
        self.assertEqual(s["tracked_sensitive_files"], ["_privat/kohorsz.csv"])
        self.assertTrue(s["history"]["in_history"])
        joined = " ".join(s["recommendations"])
        self.assertIn("git filter-repo", joined)
        self.assertIn("GDPR", joined)
        self.assertIn("git rm --cached", joined)
        self.assertFalse(self.st(repo, "C", check_history=False)["history"]["checked"])

    def test_c_class_paused_vault_opens(self):
        cfg = self.home / ".claude" / "vault" / "config.json"
        cfg.write_text(json.dumps({"root": "~/Documents/claude", "paused": True}), encoding="utf-8")
        s = self.st(self.proj, "C")
        self.assertFalse(s["open_blocked"]["blocked"])
        self.assertFalse(s["write_hold"]["active"])
        self.assertTrue(any("szünetel" in r for r in s["recommendations"]))

    def test_cloud_sync_recommendation(self):
        (self.home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Documents").mkdir(parents=True)
        s = self.st(self.proj, "B", platform="darwin")
        self.assertEqual(s["cloud_sync"]["kind"], "icloud")
        self.assertTrue(any("iCloud" in r and "doc_roots" in r for r in s["recommendations"]))

    def test_invalid_class(self):
        with self.assertRaises(pv.PrivacyError):
            self.st(self.proj, "X")
        with self.assertRaises(pv.PrivacyError) as cm:
            self.st(self.proj / "nincs", "A")
        self.assertEqual(cm.exception.to_error()["http"], 404)


# ---------------------------------------------------------------------------------------------
# modul-higiénia
# ---------------------------------------------------------------------------------------------

class ModuleHygieneTest(unittest.TestCase):
    BANNED = {"math", "cmath", "statistics", "random", "decimal"}

    def tree(self):
        return ast.parse(Path(pv.__file__).read_text(encoding="utf-8"))

    def imported(self):
        mods = set()
        for n in ast.walk(self.tree()):
            if isinstance(n, ast.Import):
                mods.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                mods.add((n.module or "").split(".")[0])
        return mods

    def test_no_statistics_imports(self):
        self.assertFalse(self.imported() & self.BANNED)

    def test_stdlib_only(self):
        stdlib = getattr(sys, "stdlib_module_names", None) or {
            "datetime", "difflib", "fnmatch", "hashlib", "json", "os", "re", "secrets", "shutil", "stat",
            "subprocess", "sys", "unicodedata", "pathlib", "winreg"}
        self.assertLessEqual(self.imported(), set(stdlib))

    def test_no_logging_or_print(self):
        self.assertNotIn("logging", self.imported())
        calls = [n.func.id for n in ast.walk(self.tree())
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertNotIn("print", calls)


if __name__ == "__main__":
    unittest.main()
