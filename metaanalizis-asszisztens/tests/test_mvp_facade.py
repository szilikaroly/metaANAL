# -*- coding: utf-8 -*-
"""E1 motor-homlokzat (metaelemzes/api.py) és a CLI-bekötés (E2, E3, E7, E8, rules export, --capabilities, gui).

- Minden api-függvény ugyanazt adja, mint a megfelelő CLI-parancs JSON-kimenete (json.dumps(sort_keys) bájtra).
- api.analyze: explore semmit nem ír; commit a CLI `analyze --spec`-kel bájtra azonos kimeneteket, run.json-t és
  projektnapló-sort ír; a nézetmodell plot-ja a kiírt plot_data.json.
- api.convert: szk.ma.convert-result/v1 (szerződés szerint), a számok a conversions függvényeiéi.
- A CLI új kapcsolói és alparancsai (validate --request-json, project status --json, export --format json,
  project activity, --actor, gui/munkapad, rules export) és a MA_ACTIVITY_LOG-horog.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _helpers  # noqa: F401  (a csomag gyökerét a sys.path-ra teszi)
from metaelemzes import activity as A
from metaelemzes import api, cli, contracts as K, conversions as C, kb, pipeline, projekt, prisma, spec as S
from metaelemzes import tableio, validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PELDAK = os.path.join(ROOT, "peldak")
BCG = os.path.join(PELDAK, "bcg_oltas_RR.csv")
EXAMPLES = [("bcg_oltas_RR.csv", ("RR", "OR", "RD")), ("molloy2014_korrelacio.csv", ("ZCOR",)),
            ("normand1999_folytonos.csv", ("MD", "SMD")), ("pritz1997_arany.csv", ("PLO", "PFT"))]
DATE = "2026-10-05"
RID = "20261005T120000Z-abcdef"


def run_cli(*args, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        patch = mock.patch.object(sys, "stdin", io.StringIO(stdin)) if stdin is not None else contextlib.nullcontext()
        with patch:
            try:
                code = cli.main(list(args))
            except SystemExit as exc:
                code = exc.code
    return code, out.getvalue(), err.getvalue()


def canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False)


def contract_errors(doc, name, version):
    import test_mvp_contracts as TC
    return TC.errors_for(doc, name, version)


def snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def project(self, name="proj"):
        p = os.path.join(self.tmp, name)
        projekt.init(p, "Teszt")
        os.makedirs(os.path.join(p, "03_adatok"), exist_ok=True)
        shutil.copy(BCG, os.path.join(p, "03_adatok", "o1.csv"))
        return p

    def spec(self, p, name="o1_primary", flags=("--measure", "RR"), save=True, **kw):
        sp = S.spec_from_argv(["analyze", "--data", os.path.join(p, "03_adatok", "o1.csv")] + list(flags), p,
                              name=name, outcome="o1", **kw)
        path = os.path.join(p, S.spec_relpath(name))
        if save:
            S.save_spec(path, sp)
        return sp, path


# ------------------------------------------------------------------ CLI ↔ api
class TestCliApiParity(_Tmp):
    """A CLI JSON-kimenete és a homlokzat függvénye bájtra ugyanaz (rendezett kulcsokkal)."""

    @classmethod
    def setUpClass(cls):
        cls.kbdir = tempfile.mkdtemp()
        cls.db = os.path.join(cls.kbdir, "kb.sqlite")
        kb.build(cls.db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.kbdir, True)

    def assertParity(self, args, value, stdin=None, code=0):
        rc, out, err = run_cli(*args, stdin=stdin)
        self.assertEqual(rc, code, (args, err))
        self.assertEqual(canon(json.loads(out)), canon(value), args)

    def test_capabilities_and_rules_export(self):
        self.assertParity(["--capabilities"], api.capabilities())
        self.assertParity(["rules", "export", "--json"], api.rules_export())

    def test_validate_json_and_request(self):
        for fname, measures in EXAMPLES:
            path = os.path.join(PELDAK, fname)
            for m in measures:
                doc = api.validate_file(path, m, {"smd_vtype": "LS", "md_vtype": "unequal", "glass_vtype": "METAN",
                                                  "gen_smd_vtype": None})
                self.assertParity(["validate", "--data", path, "--measure", m, "--json"], doc,
                                  code=1 if doc["summary"]["error"] else 0)
        req = {"schema": "szk.ma.validate-request/v1", "measure": "RR", "options": {"cc": 0.5},
               "table": {"header": ["study", "e1", "n1", "e2", "n2"],
                         "rows": [["A", "4", "123", "11", "139"], ["B", "=1+2", "20", "3", "20"]]}}
        doc = api.validate_request(req)
        self.assertEqual(doc, api.validate_table(req["table"]["header"], req["table"]["rows"], "RR", {"cc": 0.5}))
        self.assertParity(["validate", "--request-json"], doc, stdin=json.dumps(req), code=1)

    def test_convert_cli(self):
        cases = [("median", {"n": 40.0, "median": 12.5, "q1": 10.0, "q3": 15.0}, ["convert", "median", "--n", "40",
                  "--median", "12.5", "--q1", "10", "--q3", "15"]),
                 ("se-from-ci", {"lower": 0.3, "upper": 0.9, "log": True},
                  ["convert", "se-from-ci", "--lower", "0.3", "--upper", "0.9", "--log"]),
                 ("d-from-t", {"t": 2.1, "n1": 20.0, "n2": 22.0, "hedges": True},
                  ["convert", "d-from-t", "--t", "2.1", "--n1", "20", "--n2", "22", "--hedges"])]
        for kind, params, argv in cases:
            self.assertParity(argv, api.convert_cli(kind, params))

    def test_prisma_check(self):
        flow = {"identified_databases": 100, "duplicates_removed": 10, "screened": 90, "excluded_screening": 70,
                "sought": 20, "not_retrieved": 2, "assessed": 18, "excluded_eligibility": 8,
                "excluded_eligibility_reasons": {"a": 5, "b": 3}, "included_studies": 11, "included_reports": 10}
        f = os.path.join(self.tmp, "flow.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(flow, fh)
        res = api.prisma_check(flow)
        self.assertParity(["prisma", "check", "--json", f, "--out-format", "json"], res,
                          code=0 if res["ok"] else 1)

    def test_prisma_check_raw_text_boxes_with_studies(self):
        """P017 szövegként érkező dobozokkal (a felület '15'-öt küld): a motor ugyanúgy értelmez, mint a P001-nél;
        korábban a részletszöveg '%d'-vel készült a nyers szövegből → TypeError."""
        studies = {"schema": "szk.ma.studies/v1", "studies": [{"study_id": "a", "reports": [{"rec_id": "r"}]}]}
        res = api.prisma_check({"dedup_removed": "1", "included_reports": "15", "included_studies": "13"},
                               studies=studies)
        p017 = sorted((f["fields"][0], f["detail"]) for f in res["findings"] if f["code"] == "P017")
        self.assertEqual(p017, [("included_reports", "Bevont jelentések (J): flow = 15, studies.json = 1"),
                                ("included_studies", "Bevont vizsgálatok (I): flow = 13, studies.json = 1")])
        # tizedesvesszős / szóközös szöveg ugyanaz a szám → nincs P017; érvénytelen → csak P001 (nem P017, nem hiba)
        same = api.prisma_check({"included_reports": " 1 ", "included_studies": "1,0"}, studies=studies)
        self.assertNotIn("P017", [f["code"] for f in same["findings"]])
        bad = api.prisma_check({"included_reports": "x", "included_studies": ""}, studies=studies)
        codes = [f["code"] for f in bad["findings"]]
        self.assertIn("P001", codes)
        self.assertNotIn("P017", codes)
        # üres doboz: a studies.json-ból töltődik (I = 1), mint a hiányzó
        self.assertEqual(bad["counts"]["included_studies"], 1)
        self.assertIsNone(bad["counts"]["included_reports"])

    def test_project_json_commands(self):
        p = self.project()
        projekt.log_decision(p, "planner", "REML", stage="S08", check_kb=False)
        fid = projekt.add_finding(p, "reviewer", "major", "SE/SD", stage="S05", check_kb=False)
        self.assertParity(["project", "status", p, "--json"], api.project_status(p))
        self.assertParity(["project", "list", p, "findings", "--json"], api.project_list(p, "findings"))
        self.assertParity(["project", "list", p, "decisions"], api.project_list(p, "decisions"))
        self.assertParity(["project", "show", p, "finding", str(fid), "--json"], api.project_show(p, "finding", fid))
        self.assertParity(["project", "activity", p, "--json"], api.project_activity(p))
        rc, out, err = run_cli("project", "export", p, "--format", "json")
        self.assertEqual(rc, 0, err)
        target = os.path.join(p, "07_ellenorzes", "dontesi_naplo.json")
        self.assertIn(target, out)
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(canon(json.load(fh)), canon(api.project_export_json(p)))
        rc, out, err = run_cli("project", "export", p)
        with open(os.path.join(p, "07_ellenorzes", "dontesi_naplo.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), api.project_export_markdown(p))
        fixed = "2026-10-05T12:00:00Z"
        with mock.patch("metaelemzes.audit._now_utc", return_value=fixed):
            self.assertParity(["project", "audit", p, "--json"], api.project_audit(p))
            self.assertParity(["project", "audit", p, "--json", "--stage", "S08"], api.project_audit(p, stage="S08"))

    def test_kb_commands(self):
        db = self.db
        self.assertParity(["kb", "--db", db, "search", "heterogeneity", "--json", "--limit", "3"],
                          api.kb_search("heterogeneity", 3, db=db))
        self.assertParity(["kb", "--db", db, "search", "V018", "--json", "--scope", "rule", "--source", "engine"],
                          api.kb_search("V018", 8, ("rule",), "engine", db=db))
        self.assertParity(["kb", "--db", db, "show", "X016"], api.kb_show("X016", db=db))
        self.assertParity(["kb", "--db", db, "rules", "--stage", "S12", "--json"], api.kb_rules("S12", db=db))
        self.assertParity(["kb", "--db", db, "rules", "--stage", "FINAL", "--agent", "reviewer", "--json"],
                          api.kb_rules("FINAL", "reviewer", db=db))
        self.assertParity(["kb", "--db", db, "checklist", "AMSTAR2", "--json"], api.kb_checklist("AMSTAR2", db=db))
        rc, _, err = run_cli("kb", "--db", db, "rules", "--stage", "S99")
        self.assertEqual(rc, 1)
        with self.assertRaises(api.UnknownStage) as cm:
            api.kb_rules("S99", db=db)
        self.assertEqual(err.strip(), str(cm.exception))
        self.assertIsNone(api.kb_show("NINCS-ILYEN", db=db))

    def test_x_rules_in_kb(self):
        rules = {r["rule_id"]: r for r in kb.rules(None, None, self.db)}
        from metaelemzes import audit
        for code in audit.RULES:
            self.assertIn(code, rules)
            self.assertEqual(rules[code]["stage_id"], audit.RULE_STAGES[code])
            self.assertEqual(rules[code]["machine_check"], "metaelemzes.audit:%s" % code)
        self.assertEqual({r["rule_id"] for r in kb.rules("S12", None, self.db)} & set(audit.RULES),
                         {"X005", "X006", "X016"})
        exported = {r["id"] for r in api.rules_export()}
        self.assertTrue(set(audit.RULES) | set(validate.RULES) | set(prisma.RULES) <= exported)
        self.assertEqual(kb.existing_ids(sorted(exported), self.db), exported)


# ------------------------------------------------------------------ engine_info, capabilities, rules
class TestEngineInfo(unittest.TestCase):
    def test_engine_info_shape(self):
        info = api.engine_info()
        self.assertEqual(info["schema"], api.ENGINE_INFO_SCHEMA)
        self.assertEqual(list(info["options"]), list(pipeline.DEFAULTS))
        an = S._parser()[0]
        by_dest = {a.dest: a for a in an._actions}
        for key, o in info["options"].items():
            if o["dest"] is None:
                self.assertIn(key, S.spec_only_keys())
                continue
            act = by_dest[o["dest"]]
            self.assertEqual(o["flags"], list(act.option_strings), key)
            self.assertEqual(o["help"], act.help, key)
            if o["type"] == "enum":
                self.assertEqual(o["choices"], list(act.choices or o["choices"]), key)
        self.assertEqual(info["options"]["h_centre"]["flags"], ["--ht-centre", "--ht-center"])
        self.assertEqual(info["options"]["plot_locale"]["cli"], "--lang")
        ms = {m["id"]: m for m in info["measures"]}
        self.assertEqual(len(ms), 18)
        self.assertEqual(ms["RR"]["required_columns"], ["e1", "n1", "e2", "n2"])
        self.assertEqual((ms["RR"]["scale"], ms["RR"]["ratio"], ms["ZCOR"]["scale"]), ("log", True, "atanh"))
        self.assertTrue(all(m["label"]["en"] != m["id"] for m in info["measures"]))
        st = info["selftest"]
        if st is not None:
            self.assertEqual((st["state"], st["failed"]), ("pass", 0), st)
            self.assertGreater(st["checks"], 3000)
        self.assertIsNone(api.engine_info(selftest=False)["selftest"])
        self.assertIn("ma.plot", info["contracts"])
        json.dumps(info, allow_nan=False)

    def test_rules_export(self):
        rows = api.rules_export()
        self.assertEqual(len(rows), len(validate.RULES) + len(prisma.RULES) + len(__import__(
            "metaelemzes.audit", fromlist=["RULES"]).RULES))
        self.assertEqual([r["id"][0] for r in rows], sorted((r["id"][0] for r in rows), key="VPX".index))
        for r in rows:
            self.assertNotEqual(r["title"]["en"], r["title"]["hu"], r["id"])
            self.assertIn(r["id"], api.RULE_TITLES_EN)
            self.assertRegex(r["stage"], r"^S\d{2}$")
        by = {r["id"]: r for r in rows}
        self.assertEqual(by["V025"]["title"]["en"], "Text cell that looks like a formula")
        self.assertEqual((by["X001"]["escalation"], by["X016"]["kb_refs"][:1]), ({"from": "S08", "before": "warning"},
                                                                               ["D-S12-006"]))
        self.assertFalse(set(api.RULE_TITLES_EN) - set(by), "fölösleges angol cím")

    def test_capabilities_contract(self):
        cap = api.capabilities()
        self.assertEqual(contract_errors(cap, "capabilities", 1), [])
        for row in K.index():
            self.assertEqual(cap["contracts"][row["schema"]], {"dir": row["dir"], "sha256": row["sha256"]})
        names = {c["name"] for c in cap["commands"]}
        self.assertTrue({"analyze", "validate", "project-audit", "rules-export", "gui"} <= names)
        for c in cap["commands"]:
            for d in c["in"] + c["out"]:
                self.assertIn(d, cap["contracts"], c["name"])

    def test_kb_rules_for_field(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        db = os.path.join(tmp, "kb.sqlite")
        kb.build(db)
        res = api.kb_rules_for_field("model", db=db)
        self.assertEqual(res["field"], "model")
        ids = [i["id"] for i in res["items"]]
        self.assertTrue(ids, res)
        self.assertTrue(all("model" in i["machine_check"] for i in res["items"]))
        self.assertIn("V018", [i["id"] for i in api.kb_rules_for_field("V018", db=db)["items"]] +
                      [i["machine_check"].split(":")[-1][:4] for i in api.kb_rules_for_field("V018", db=db)["items"]])
        hc = api.kb_rules_for_field("h_centre", db=db)
        self.assertEqual(hc["field"], "h_centre")
        with self.assertRaises(ValueError):
            api.kb_rules_for_field("x; DROP TABLE", db=db)


# ------------------------------------------------------------------ tábla
class TestTables(_Tmp):
    def test_read_write_roundtrip(self):
        for fname, _ in EXAMPLES:
            src = os.path.join(PELDAK, fname)
            header, rows, meta = api.read_table(src)
            dst = os.path.join(self.tmp, fname)
            digest = api.write_table(dst, header, rows, meta)
            with open(src, "rb") as a, open(dst, "rb") as b:
                self.assertEqual(a.read(), b.read(), fname)
            self.assertEqual(digest, meta["sha256"])
            # a nyers cellák motorbeli értelmezése = a fájlé
            parsed, _m = tableio.parse_table(header, rows, meta["decimal_mark"], meta["delimiter"], meta["lines"])
            file_rows, _ = tableio.read_table(src)
            self.assertEqual([{k: v for k, v in r.items() if k != "_line"} for r in parsed],
                             [{k: v for k, v in r.items() if k != "_line"} for r in file_rows], fname)

    def test_write_new_table_default_format(self):
        p = os.path.join(self.tmp, "uj.csv")
        api.write_table(p, ["study", "e1"], [["Á", "1,5"], [], ["B", 'x;"y"']])
        with open(p, "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw[3:].decode("utf-8"), 'study;e1\r\nÁ;1,5\r\n\r\nB;"x;""y"""\r\n')
        h, rows, meta = api.read_table(p)
        # az üres sor nem adatsor (a sorrács = a read_table / validate_file / plot row_index sorai), a meta őrzi
        self.assertEqual((h, rows, meta["delimiter"], meta["lines"]), (["study", "e1"], [["Á", "1,5"],
                                                                                         ["B", 'x;"y"']], ";",
                                                                        [2, 4]))
        self.assertEqual(api.write_table(p, h, rows, meta), meta["sha256"])
        with self.assertRaises(ValueError):
            api.write_table(p, ["a"], [["1"]], {"delimiter": "|"})


# ------------------------------------------------------------------ analyze
class TestAnalyze(_Tmp):
    def test_explore_writes_nothing(self):
        p = self.project()
        sp, path = self.spec(p, flags=("--measure", "RR", "--subgroup", "allokáció"))
        before = snapshot(p)
        view = api.analyze(sp, project_root=p, client_seq=7)
        self.assertEqual(snapshot(p), before)
        run = view["run"]
        self.assertEqual((view["schema"], run["mode"], run["run_id"], run["client_seq"]),
                         (api.ANALYSIS_RESULT_SCHEMA, "explore", None, 7))
        self.assertNotIn("files", run)
        self.assertEqual(run["data"]["path"], "03_adatok/o1.csv")
        self.assertEqual(contract_errors(run, "ma.run", 1), [])
        self.assertEqual(contract_errors(view["plot"], "ma.plot", 2), [])
        self.assertEqual(view["plot"]["meta"]["run_id"], None)
        self.assertEqual(view["results"]["input"]["path"], "03_adatok/o1.csv")
        self.assertEqual(view["results"]["effect_sizes"]["k"], 13)
        self.assertEqual(view["command"][:4], ["ma.py", "analyze", "--data", "03_adatok/o1.csv"])
        uids = tableio.row_uids(tableio.read_table(os.path.join(p, "03_adatok", "o1.csv"))[0])
        self.assertEqual([s["row_uid"] for s in view["plot"]["studies"] if s.get("row_uid")][:13], uids)
        json.dumps(view, allow_nan=False)
        # spec-fájlból is (a spec-út a parancsban és a hash a fájlé)
        v2 = api.analyze(path, project_root=p)
        self.assertEqual(v2["run"]["spec"]["path"], "05_elemzes/specs/o1_primary.json")
        self.assertEqual(v2["run"]["spec"]["sha256"], S.sha256_file(path))
        self.assertEqual(v2["command"], ["ma.py", "analyze", "--spec", "05_elemzes/specs/o1_primary.json",
                                         "--project", "."])
        self.assertEqual(canon(v2["results"]), canon(view["results"]))

    def test_commit_equals_cli_spec_run(self):
        p = self.project()
        flags = ("--measure", "RR", "--exclude", "allokáció=random", "--moderators", "szélesség")
        sp, path = self.spec(p, flags=flags, purpose="primary", prespecified=True, pin_data=True)
        a_dir = os.path.join(p, "05_elemzes", "o1", "cli")
        rc, out, err = run_cli("analyze", "--spec", path, "--out", a_dir, "--project", p, "--date", DATE,
                               "--run-id", RID)
        self.assertEqual(rc, 0, err)
        b_dir = os.path.join(p, "05_elemzes", "o1", "api")
        view = api.analyze(path, mode="commit", outdir=b_dir, project_root=p, run_id=RID, date=DATE, actor="user:SzK")
        fa = sorted(os.listdir(a_dir))
        self.assertEqual(fa, sorted(os.listdir(b_dir)))
        for f in fa:
            if f == "run.json":
                continue
            with open(os.path.join(a_dir, f), "rb") as x, open(os.path.join(b_dir, f), "rb") as y:
                self.assertEqual(x.read(), y.read(), f)
        with open(os.path.join(b_dir, "run.json"), encoding="utf-8") as fh:
            run = json.load(fh)
        self.assertEqual(run, view["run"])
        self.assertEqual((run["mode"], run["run_id"], run["spec"]["path"]),
                         ("commit", RID, "05_elemzes/specs/o1_primary.json"))
        self.assertEqual(run["equivalent_argv"], ["ma.py", "analyze", "--spec", "05_elemzes/specs/o1_primary.json",
                                                  "--out", "05_elemzes/o1/api", "--project", "."])
        self.assertEqual(contract_errors(run, "ma.run", 1), [])
        for role, f in run["files"].items():
            self.assertEqual(S.sha256_file(os.path.join(p, f["path"])), f["sha256"], role)
        with open(os.path.join(b_dir, "plot_data.json"), encoding="utf-8") as fh:
            self.assertEqual(canon(json.load(fh)), canon(view["plot"]))
        runs = projekt.list_items(p, "runs")
        self.assertEqual(len(runs), 2)
        self.assertEqual((runs[-1]["actor"], runs[-1]["outdir"]), ("user:SzK", os.path.abspath(b_dir)))
        self.assertIn("--spec", runs[-1]["command"])
        # az audit a commit-futást látja (X001 nincs: a tábla nem változott)
        self.assertFalse([f for f in api.project_audit(p)["findings"] if f["code"] == "X001"])

    def test_commit_default_outdir_and_guards(self):
        p = self.project()
        sp, path = self.spec(p, pin_data=True)
        view = api.analyze(sp, mode="commit", project_root=p, date=DATE)
        rid = view["run"]["run_id"]
        self.assertRegex(rid, S.RUN_ID_PATTERN)
        self.assertTrue(os.path.isfile(os.path.join(p, "05_elemzes", "o1", rid, "run.json")))
        with self.assertRaises(ValueError):
            api.analyze(sp, table={"header": ["study"], "rows": []}, mode="commit", project_root=p)
        with self.assertRaises(ValueError):
            api.analyze(sp, mode="dry", project_root=p)
        with self.assertRaises(S.SpecError):
            api.analyze(sp, mode="commit", project_root=p, run_id="rossz")
        with open(os.path.join(p, "03_adatok", "o1.csv"), "a", encoding="utf-8") as fh:
            fh.write("Új;1;10;2;10;40;2000;random\n")
        with self.assertRaises(S.SpecError):
            api.analyze(sp, mode="commit", project_root=p)
        v = api.analyze(sp, project_root=p)          # explore: figyelmeztetés, nem hiba
        self.assertTrue(any("data.sha256" in w for w in v["warnings"]))
        bad = dict(sp, options=dict(sp["options"], robust=True))
        with self.assertRaises(S.SpecError):
            api.analyze(bad, project_root=p)
        noproj = os.path.join(self.tmp, "nincs_naplo")
        os.makedirs(os.path.join(noproj, "03_adatok"))
        shutil.copy(BCG, os.path.join(noproj, "03_adatok", "o1.csv"))
        with self.assertRaises(Exception):
            api.analyze(sp, mode="commit", project_root=noproj)
        self.assertFalse(os.path.exists(os.path.join(noproj, "05_elemzes")))

    def test_draft_table_explore(self):
        p = self.project()
        sp, _ = self.spec(p, flags=("--measure", "RR"))
        header, rows, meta = api.read_table(os.path.join(p, "03_adatok", "o1.csv"))
        file_view = api.analyze(sp, project_root=p)
        draft = api.analyze(sp, table={"header": header, "rows": rows, "delimiter": meta["delimiter"],
                                       "lines": meta["lines"]}, project_root=p)
        self.assertEqual(draft["run"]["data"]["sha256"], None)
        self.assertEqual(draft["run"]["data"]["path"], "03_adatok/o1.csv")
        fp, dp = dict(file_view["plot"]), dict(draft["plot"])
        fp.pop("meta"), dp.pop("meta")
        self.assertEqual(canon(fp), canon(dp))
        # egy cella átírása (mentés nélkül) az eredményt megváltoztatja
        rows2 = [list(r) for r in rows]
        rows2[0][3] = "40"
        changed = api.analyze(sp, table={"header": header, "rows": rows2}, project_root=p)
        self.assertNotEqual(changed["results"]["primary"]["estimate"], file_view["results"]["primary"]["estimate"])

    def test_excluded_rows_have_uids(self):
        p = self.project()
        path = os.path.join(p, "03_adatok", "o1.csv")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("Hibás;x;10;2;10;40;2000;random\n")
        sp, _ = self.spec(p)
        view = api.analyze(sp, project_root=p)
        uids = tableio.row_uids(tableio.read_table(path)[0])
        self.assertEqual([(e["row"], e["row_uid"]) for e in view["excluded"]], [(13, uids[13])])
        self.assertGreaterEqual(view["validation_summary"]["error"], 1)
        self.assertTrue(any(f["code"] == "V003" for f in view["findings"]))


# ------------------------------------------------------------------ convert
class TestConvert(unittest.TestCase):
    def req(self, kind, inputs, **kw):
        d = {"schema": "szk.ma.convert-request/v1", "kind": kind, "inputs": inputs}
        d.update(kw)
        return d

    def test_every_kind_is_contract_valid(self):
        samples = {
            "median_to_mean_sd": {"n": "40", "median": "12,5", "q1": "10", "q3": "16"},
            "se_to_sd": {"se": "0,42", "n": 52}, "ci_to_sd": {"lower": "10,1", "upper": "14,3", "n": "48"},
            "ci_to_se": {"lower": "0.33", "upper": "0.73", "log": "igen"}, "p_to_se": {"estimate": "0.49", "p": "0.001",
                                                                                     "log": "igen"},
            "smd_variance": {"g": "0.5", "n1": "20", "n2": "22"},
            "combine_groups": {"n1": "10", "m1": "5", "sd1": "1", "n2": "12", "m2": "6", "sd2": "1.5"},
            "change_sd": {"sd_baseline": "2", "sd_final": "2.5", "corr": "0.6"},
            "corr_from_change": {"sd_baseline": "2", "sd_final": "2.5", "sd_change": "2"},
            "split_shared_control": {"n": "60", "arms": "2", "events": "9"},
            "paired_sums": {"n": "10", "sum_d": "25", "sum_sq_dev": "40"},
            "t_to_d": {"t": "2.1", "n1": "20", "n2": "22", "hedges": "igen"},
            "logor_to_d": {"y": "0.8", "v": "0.04"}, "d_to_logor": {"y": "0.5", "v": "0.02"},
            "r_to_d": {"y": "0.3", "v": "0.01"}, "d_to_r": {"y": "0.5", "v": "0.04", "n1": "20", "n2": "20"},
        }
        self.assertEqual(set(samples), set(api.CONVERT_KINDS))
        kinds = K.load("ma.convert-request", 1)["properties"]["kind"]["enum"]
        self.assertTrue(set(api.CONVERT_KINDS) <= set(kinds))
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        db = os.path.join(tmp, "kb.sqlite")
        kb.build(db)
        for kind, inputs in samples.items():
            req = self.req(kind, inputs, target={"dataset": "03_adatok/o1.csv", "row_uid": "r7f3a2",
                                                 "fields": {"x": "m1"}})
            self.assertEqual(contract_errors(req, "ma.convert-request", 1), [], kind)
            res = api.convert(req)
            self.assertEqual(contract_errors(res, "ma.convert-result", 1), [], kind)
            self.assertTrue(res["outputs"], kind)
            self.assertEqual(set(res["outputs_text"]), set(res["outputs"]))
            self.assertEqual(res["target"], req["target"])
            refs = res["method"]["kb_refs"]
            self.assertEqual(kb.existing_ids(refs, db), set(refs), kind)
            self.assertEqual("D-S05-023" in refs, res["estimated"], kind)
            json.dumps(res, allow_nan=False)

    def test_numbers_come_from_conversions(self):
        r = api.convert(self.req("median_to_mean_sd", {"n": "40", "median": "12,5", "q1": "10", "q3": "15"}))
        self.assertEqual(r["outputs"]["mean"], C.mean_from_median(40, 12.5, 10, 15, None, None, "luo"))
        self.assertEqual(r["outputs"]["sd"], C.sd_from_median(40, 10, 15, None, None, median=12.5))
        self.assertTrue(r["estimated"])
        self.assertEqual(r["method"]["id"], "luo2018+wan2014")
        h = api.convert(self.req("median_to_mean_sd", {"n": 40, "median": 12.5, "min": 4, "max": 30}, method="hozo"))
        self.assertEqual(h["method"]["id"], "hozo2005+wan2014")
        self.assertEqual(h["outputs"]["mean"], C.mean_from_median(40, 12.5, None, None, 4, 30, "hozo"))
        self.assertEqual(api.convert(self.req("p_to_se", {"estimate": "0.49", "p": "0.001", "log": True}))["outputs"],
                         api.convert(self.req("p_to_se", {"estimate": "0.49", "p": "0.001", "log": "igen"}))["outputs"])
        s = api.convert(self.req("se_to_sd", {"se": "0,42", "n": "52"}))
        self.assertEqual((s["outputs"], s["estimated"]), ({"sd": C.sd_from_se(0.42, 52)}, False))
        # kijelzési szöveg: a display_text konvenciója (tizedespont); a cellába írandó szöveg a cell_text
        self.assertEqual(s["outputs_text"]["sd"], {"hu": "3.029", "en": "3.029"})
        self.assertEqual(s["cell_text"]["sd"], "3,029")
        c = api.convert(self.req("ci_to_sd", {"lower": "10,1", "upper": "14,3", "n": "48", "level": "95"}))
        self.assertEqual(c["outputs"]["sd"], C.sd_from_ci(10.1, 14.3, 48, 0.95))
        skew = api.convert(self.req("median_to_mean_sd", {"n": "30", "median": "2", "q1": "1", "q3": "9"}))
        # assumptions / warnings: kétnyelvű szövegek ({hu, en}; terv 4.7)
        self.assertTrue(any("V013" in w["hu"] and "V013" in w["en"] for w in skew["warnings"]))

    def test_invalid_requests(self):
        bad = [None, {}, self.req("nincs", {}), {"schema": "x", "kind": "se_to_sd", "inputs": {}},
               self.req("se_to_sd", "0.42"), self.req("se_to_sd", {"se": "abc", "n": "10"}),
               self.req("se_to_sd", {"se": "0.4", "n": "10", "zz": "1"}), self.req("se_to_sd", {"se": "0.4"}),
               self.req("se_to_sd", {"se": "0.4", "n": "10"}, method="luo"),
               self.req("median_to_mean_sd", {"n": "40", "median": "12"}, method="x"),
               self.req("ci_to_se", {"lower": "1", "upper": "2", "log": "talán"}),
               self.req("se_to_sd", {"se": True, "n": "10"})]
        for req in bad:
            with self.assertRaises(ValueError, msg=repr(req)):
                api.convert(req)


# ------------------------------------------------------------------ CLI-bekötés
class TestCliWiring(_Tmp):
    def test_status_json_keeps_stderr_clean(self):
        p = self.project()
        projekt.log_decision(p, "nincsilyen", "x", check_kb=False)       # ismeretlen ágens: figyelmeztetés
        rc, out, err = run_cli("project", "status", p, "--json")
        self.assertEqual((rc, err), (0, ""))
        st = json.loads(out)
        rc2, out2, err2 = run_cli("project", "status", p)
        self.assertEqual(out2, out)
        self.assertEqual(err2, "".join("FIGYELEM: %s\n" % w for w in st.get("warnings", [])))

    def test_actor_flags(self):
        p = self.project()
        rc, _, err = run_cli("project", "log", p, "--agent", "planner", "--decision", "REML", "--actor", "user:SzK")
        self.assertEqual(rc, 0, err)
        self.assertEqual(projekt.get_item(p, "decision", 1)["actor"], "user:SzK")
        with mock.patch.dict(os.environ, {"MA_ACTOR": "agent:ma-ellenorzo"}):
            run_cli("project", "finding", p, "--agent", "reviewer", "--severity", "minor", "--title", "x")
            run_cli("project", "resolve", p, "1", "--status", "fixed", "--resolution", "ok")
        f = projekt.get_item(p, "finding", 1)
        self.assertEqual((f["actor"], f["resolved_actor"]), ("agent:ma-ellenorzo", "agent:ma-ellenorzo"))
        rc, _, err = run_cli("analyze", "--data", BCG, "--measure", "RR", "--out", os.path.join(self.tmp, "o"),
                             "--project", p, "--actor", "\x01rossz")
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "o")))
        rc, _, err = run_cli("analyze", "--data", BCG, "--measure", "RR", "--out", os.path.join(self.tmp, "o"),
                             "--project", p, "--actor", "user:SzK", "--date", DATE)
        self.assertEqual(rc, 0, err)
        self.assertEqual(projekt.list_items(p, "runs")[-1]["actor"], "user:SzK")

    def test_activity_hook_init_export_and_verify(self):
        p = os.path.join(self.tmp, "akt")
        with mock.patch.dict(os.environ, {"MA_ACTIVITY_LOG": "1", "MA_ACTOR": "user:SzK"}):
            self.assertEqual(run_cli("project", "init", p, "--title", "T")[0], 0)
            self.assertEqual(run_cli("project", "export", p, "--format", "json")[0], 0)
            self.assertEqual(run_cli("project", "checkpoint", p, "--stage", "S01", "--agent", "reviewer",
                                     "--verdict", "FAIL")[0], 0)
        recs = A.read(p)
        self.assertEqual([r["action"] for r in recs], ["project.init", "project.export", "project.checkpoint"])
        self.assertTrue(all(r["actor"] == "user:SzK" for r in recs))
        self.assertIn("07_ellenorzes/dontesi_naplo.json", recs[1]["outputs"])
        self.assertTrue(any(k.endswith("protokoll.md") for k in recs[0]["outputs"]))
        rc, out, _ = run_cli("project", "activity", p)
        self.assertEqual(rc, 0)
        self.assertIn("3 bejegyzés", out)
        with open(A.log_path(p), "r+", encoding="utf-8") as fh:
            lines = fh.read().splitlines(True)
            fh.seek(0)
            fh.truncate()
            fh.writelines([lines[0], lines[2]])
        rc, out, _ = run_cli("project", "activity", p, "--json")
        rep = json.loads(out)
        self.assertEqual((rc, rep["ok"], rep["first_bad_seq"]), (1, False, 2))

    def test_audit_gate_and_project_audit(self):
        p = self.project()
        rc, out, err = run_cli("project", "audit", p, "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["schema"], "szk.ma.project-audit/v1")
        rc, _, err = run_cli("project", "checkpoint", p, "--stage", "S08", "--agent", "reviewer", "--verdict",
                             "PASS", "--audit-gate")
        self.assertEqual(rc, 1)
        self.assertIn("FINAL", err)
        rc, out, err = run_cli("project", "checkpoint", p, "--stage", "FINAL", "--agent", "reviewer", "--verdict",
                               "PASS", "--audit-gate")
        self.assertEqual(rc, 0, err)

    def test_validate_and_analyze_required_flags(self):
        for args in (["validate", "--data", BCG], ["validate", "--measure", "RR"], ["analyze", "--measure", "RR"]):
            rc, _, err = run_cli(*args)
            self.assertEqual(rc, 2, args)
            self.assertIn("the following arguments are required", err)
        rc, out, err = run_cli("validate", "--request-json", stdin="{nem json")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("HIBA: a kérés nem érvényes JSON", err)
        rc, out, err = run_cli("validate", "--request-json", stdin=json.dumps({"schema": "x"}))
        self.assertEqual(rc, 2)
        self.assertIn("HIBA:", err)

    def test_analyze_new_flags_and_json_summary(self):
        o = os.path.join(self.tmp, "o")
        rc, out, err = run_cli("analyze", "--data", BCG, "--measure", "RR", "--out", o, "--lang", "en",
                               "--svg-annotate", "--plot-schema", "v1", "--json-summary", "--date", DATE)
        self.assertEqual(rc, 0, err)
        self.assertIn("Kész:", err)
        desc = json.loads(out)
        with open(os.path.join(o, "run.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), desc)
        with open(os.path.join(o, "forest.svg"), encoding="utf-8") as fh:
            svg = fh.read()
        self.assertIn('id="layer-forest', svg)
        self.assertIn("Study", svg)
        with open(os.path.join(o, "plot_data.json"), encoding="utf-8") as fh:
            self.assertNotIn("schema", json.load(fh))                 # v1: a korábbi kulcsok
        with open(os.path.join(o, "results.json"), encoding="utf-8") as fh:
            opts = json.load(fh)["options"]
        self.assertEqual((opts["plot_locale"], opts["svg_annotate"], opts["plot_schema"]), ("en", True, "v1"))
        rc, out, err = run_cli("analyze", "--data", BCG, "--measure", "RR", "--out", o, "--plot-locale", "hu",
                               "--date", DATE)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(o, "plot_data.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["schema"], "szk.ma.plot/v2")

    def test_filtered_run_row_uids_refer_to_full_table(self):
        o = os.path.join(self.tmp, "f")
        rc, _, err = run_cli("analyze", "--data", BCG, "--measure", "RR", "--out", o, "--exclude",
                             "allokáció=random", "--date", DATE)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(o, "plot_data.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        rows, meta = tableio.read_table(BCG)
        uids = tableio.row_uids(rows, meta)
        for s in doc["studies"]:
            self.assertEqual(uids[s["row_index"]], s["row_uid"])
        kept = {id(r) for r in tableio.apply_filters(rows, ["allokáció=random"], None, meta=meta)}
        self.assertEqual(sorted(s["row_index"] for s in doc["studies"]),
                         [i for i, r in enumerate(rows) if id(r) in kept])
        self.assertLess(len(kept), len(rows))

    def test_gui_forwarding_and_missing(self):
        import importlib
        real = importlib.import_module

        def broken(name, *a, **k):
            if name.startswith("ma_gui"):
                raise ImportError("No module named 'ma_gui'")
            return real(name, *a, **k)

        with mock.patch("importlib.import_module", side_effect=broken):
            rc, out, err = run_cli("gui", "--project", self.tmp)
        self.assertEqual(rc, 2)
        self.assertIn("HIBA: a munkapad (ma_gui) nem érhető el:", err)
        mod = importlib.import_module("ma_gui.__main__")
        name = "main" if hasattr(mod, "main") else "cli_main"
        with mock.patch.object(mod, name, return_value=0) as entry:
            rc, _, err = run_cli("munkapad", "--project", self.tmp, "--port", "0", "--no-browser", "--lang", "en",
                                 "--idle-hours", "0.5")
        self.assertEqual(rc, 0, err)
        entry.assert_called_once_with(["--project", self.tmp, "--port", "0", "--no-browser", "--lang", "en",
                                       "--idle-hours", "0.5"])
        rc, out, err = run_cli("gui", "--project", os.path.join(self.tmp, "nincs"), "--no-browser")
        self.assertEqual(rc, 2)
        self.assertIn("HIBA", out + err)

    def test_contracts_alias(self):
        rc, out, _ = run_cli("contracts", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), K.index())
        self.assertEqual(run_cli("contracts", "check")[0], 0)


class TestKbDefaultDb(unittest.TestCase):
    """A tudásbázis alapértelmezett helye: METAELEMZES_KB > a plugin adatmappája > tudasbazis/tudasbazis.sqlite."""

    def test_precedence(self):
        repo = kb.ROOT
        self.assertEqual(kb._default_db({}, repo), os.path.join(kb.KB_DIR, "tudasbazis.sqlite"))
        self.assertEqual(kb._default_db({"CLAUDE_PLUGIN_DATA": "/x"}, repo), os.path.join("/x", "tudasbazis.sqlite"))
        self.assertEqual(kb._default_db({"CLAUDE_PLUGIN_DATA": "/x", "METAELEMZES_KB": "/k.sqlite"}, repo),
                         "/k.sqlite")
        # másik plugin hook-környezete (CLAUDE_PLUGIN_ROOT máshová mutat): nem a mi adatmappánk
        self.assertEqual(kb._default_db({"CLAUDE_PLUGIN_DATA": "/x", "CLAUDE_PLUGIN_ROOT": "/mas/plugin"}, repo),
                         os.path.join(kb.KB_DIR, "tudasbazis.sqlite"))
        cache = os.path.join(os.sep, "h", ".claude", "plugins", "cache", "metaanal", "metaanalizis",
                             "0.1.0")
        self.assertEqual(kb._default_db({}, cache), os.path.join(
            os.sep, "h", ".claude", "plugins", "data", "metaanalizis-metaanal", "tudasbazis.sqlite"))


if __name__ == "__main__":
    unittest.main()
