# -*- coding: utf-8 -*-
"""v1 integráció: a motor-homlokzat (metaelemzes/api.py) új függvényei és a CLI új parancsai.

- Minden v1 homlokzat-függvény ugyanazt adja, mint a megfelelő `ma.py … --json` parancs (JSON-tartalomra azonos):
  appraisal instruments | schema | route | check | validate | list | agreement | rob-summary | sync-rob, grade advice |
  sof | show | amstar2, kettos compare | report | status, prisma check --studies, figure; a kimenetek a szerződéseknek
  megfelelnek (szk.instrument/v1, szk.appraisal-result/v1, szk.ma.appraisal-agreement/v1, szk.rob-summary/v1,
  szk.ma.rob-sync-proposal/v1, szk.ma.grade/v1, szk.ma.sof/v1, szk.ma.compare-result/v1, szk.ma.consensus/v1,
  szk.ff.flowchart/v1).
- Az író parancsok (appraisal save | approve | consensus | sync-rob --apply, grade save | record, sof --save,
  kettos reconcile) a helyes fájlt írják, és a döntési szabályokat betartják (X017, 4. és 6. döntés, X009).
- A munkapad (ma_gui) által név szerint keresett függvények megvannak, és az aláírásuk illik a hívásaihoz.
- Kereszt-modul javítások: ROBINS-E 'very high' RoB-kategória; jóvá nem hagyott AI-vázlat nem számít az X003-ban;
  az S08 PASS-t az X009 kapuja elutasítja; a riport megemlíti a cumulative.svg / bubble.svg ábrát.
"""
import copy
import inspect
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _helpers import ROOT
from metaelemzes import api, audit, cli, instruments as I, projekt as P, spec as S, tableio
from metaelemzes import appraisal as A

BCG = os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv")
ALLOC_ROB = {"random": "low", "alternate": "some", "systematic": "high"}
TS = "2026-10-05T10:00:00Z"


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def plain(obj):
    """A JSON-tartalom (a CLI ezt írja ki)."""
    return json.loads(json.dumps(obj, ensure_ascii=False, allow_nan=False))


def contract(doc, name, version=1):
    return I.contract_errors(doc, name, version)


def bcg():
    with open(BCG, encoding="utf-8") as fh:
        lines = [ln.rstrip("\r\n") for ln in fh if ln.strip()]
    return lines[0].split(";"), [ln.split(";") for ln in lines[1:]]


def write_table(path, rows_override=None):
    header, rows = bcg()
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(";".join(["study_id"] + header + ["rob"]) + "\n")
        for i, r in enumerate(rows, 1):
            cells = ["S%02d" % i] + r + [ALLOC_ROB[r[7]]]
            if rows_override and i in rows_override:
                cells = rows_override[i](cells)
            fh.write(";".join(cells) + "\n")


def clean_answers(tool, scope=None):
    inst = I.load(tool)
    out = {}
    for it in inst.slots(scope):
        allowed = inst.allowed(it)
        out[it["key"]] = "no" if it.get("polarity") == "reverse" else ("yes" if "yes" in allowed else allowed[0])
    return out


def rob2(unit, assessor="SzK", status="complete", overall=None, origin="human", ai=False, judged=True, **changes):
    """RoB 2 értékelés: a doménítéletek az implikáltak; az összítélet az implikált, vagy a megadott (overall)."""
    ans = clean_answers("rob2")
    for k, v in changes.items():
        ans[k.replace("_", ".")] = v
    answers = {}
    for k, v in ans.items():
        a = {"value": v}
        if ai:
            a["rationale"] = {"asks": "Mit kérdez a tétel (egyszerű nyelven).", "because": "Miért ez a javaslat.",
                              "change": "Mi változtatná meg a javaslatot.", "uncertain": None}
            a["evidence"] = {"text": "computer-generated random sequence", "doc": "pmid:1", "page": 3, "locator": None}
        answers[k] = a
    doc = {"schema": "szk.appraisal/v1", "tool": "rob2", "scope": "assignment",
           "target": {"unit": unit, "study_id": unit, "outcome": "o1", "key": "o1"},
           "assessor": "ai" if ai else assessor, "second_assessor": None, "status": status, "origin": origin,
           "approved_by": None, "approved_at": None, "answers": answers, "domain_judgements": [],
           "applicability": [], "overall": None}
    if judged:
        res = A.check(doc)
        doc["domain_judgements"] = [{"domain": d["domain"], "pass": None, "judgement": d["implied"]}
                                    for d in res["domains"]]
        doc["overall"] = {"judgement": overall or res["overall"]["implied"], "rationale": "indok"}
    return doc


def make_project(root):
    """BCG-projekt (13 vizsgálat, study_id S01–S13, rob oszlop), egy commit-futás kumulatív elemzéssel és egy
    folytonos moderátorral (szélesség) → (nézetmodell, futásmappa)."""
    P.init(root, "v1 homlokzat próba")
    write_table(os.path.join(root, "03_adatok", "o1.csv"))
    P.add_outcome(root, {"id": "o1", "name": {"hu": "Tbc", "en": "Tuberculosis"}, "data": "03_adatok/o1.csv",
                         "measure": "RR", "critical": True, "grade_start": "high"})
    meta = P.load_project_meta(root)
    meta["appraisal_tools"] = ["rob2"]
    P.save_project_meta(root, meta)
    os.makedirs(os.path.join(root, "05_elemzes", "specs"), exist_ok=True)
    sp = S.spec_from_argv(["analyze", "--data", os.path.join(root, "03_adatok", "o1.csv"), "--measure", "RR",
                           "--cumulative", "ev", "--moderators", "szelesseg"],
                          project_root=root, name="o1_primary", outcome="o1")
    spec_path = os.path.join(root, "05_elemzes", "specs", "o1_primary.json")
    api.save_spec(spec_path, sp)
    view = api.analyze(spec_path, mode="commit", project_root=root)
    run_dir = os.path.join(root, os.path.dirname(view["run"]["files"]["results"]["path"]))
    return view, run_dir


class _Base(unittest.TestCase):
    """Egy közös, csak olvasott projekt; az író tesztek saját másolaton dolgoznak (copy_project)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="szk_v1fac_")
        cls.root = os.path.join(cls.tmp, "proj")
        cls.view, cls.run_dir = make_project(cls.root)
        a = rob2("S01", "SzK")
        b = rob2("S01", "KP", **{"1_2": "probably_yes"})
        cls.file_a = os.path.join(cls.tmp, "S01.SzK.json")
        cls.file_b = os.path.join(cls.tmp, "S01.KP.json")
        for path, doc in ((cls.file_a, a), (cls.file_b, b)):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False)
        for doc in (a, rob2("S02", "SzK"), rob2("S10", "SzK", **{"1_1": "no"})):
            A.save(cls.root, doc, now=TS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy_project(self):
        d = tempfile.mkdtemp(prefix="szk_v1fac_w_")
        self.addCleanup(shutil.rmtree, d, True)
        dst = os.path.join(d, "proj")
        shutil.copytree(self.root, dst)
        return dst

    def parity(self, args, want, code=0):
        rc, out, err = run_cli(*args)
        self.assertEqual(rc, code, err)
        self.assertEqual(json.loads(out), plain(want), args)
        return json.loads(out)


# ================================================================== homlokzat-felszín
class TestSurface(unittest.TestCase):
    NAMES = ("instruments_list", "instrument_get", "instrument_route", "appraisal_load", "appraisal_list",
             "appraisal_check", "appraisal_completeness", "appraisal_implied", "appraisal_problems", "appraisal_save", "appraisal_set_judgement", "appraisal_approve_ai_draft",
             "appraisal_consensus", "appraisal_agreement_pooled", "appraisal_build_consensus", "tripod_check",
             "amstar2_rating", "rob_summary", "rob_sync_proposal", "project_rob_summary", "project_rob_sync",
             "apply_rob_sync", "project_rob_sync_apply", "grade_advice", "grade_get", "grade_put", "grade_record",
             "grade_list", "sof", "sof_save", "sof_load", "sof_markdown", "sof_csv", "sof_html",
             "amstar2_consistency", "compare", "reconcile", "consensus_table", "agreement_report", "kettos_status",
             "kettos_project_compare", "kettos_project_reconcile", "kettos_project_report", "prisma_check",
             "prisma_flowchart", "checkpoint_gate_errors", "render_figure")

    def test_all_v1_functions_exist(self):
        for n in self.NAMES:
            self.assertTrue(callable(getattr(api, n, None)), n)

    def test_workbench_lookup_names_resolve_to_the_engine(self):
        """A munkapad név szerint keres (routes/*_common.ENGINE, grade_engine.FUNCS, adapters_figures.RENDER_FNS):
        az elsődleges nevek mind megvannak, így álnévre sosem kerül sor."""
        try:
            from ma_gui.routes import appraisal_common, extraction_dual_common, grade_engine, adapters_figures
        except Exception as exc:                            # noqa: BLE001 — csak motor telepítve
            self.skipTest("nincs ma_gui: %s" % exc)
        for table in (appraisal_common.ENGINE, extraction_dual_common.ENGINE):
            for key, (name, _aliases, _what) in table.items():
                # completeness / implied: a check tartalék-útja; validate: a munkapad az appraisal_problems-t
                # veheti fel álnévként (lásd api.py) — addig a saját alak-ellenőrzését használja
                if key in ("completeness", "implied", "validate") or callable(getattr(api, name, None)):
                    continue
                self.fail("hiányzó homlokzat-függvény: %s (%s)" % (name, key))
        for key, (names, _label) in grade_engine.FUNCS.items():
            self.assertTrue(callable(getattr(api, names[0], None)), names[0])
        self.assertTrue(callable(getattr(api, adapters_figures.RENDER_FNS[0], None)))

    def test_signatures_match_workbench_calls(self):
        p = inspect.signature(api.render_figure).parameters
        self.assertFalse({"run_dir", "project_root", "run_id"} & set(p), "a felület csak plot/kind/lang/annotate-et ad")
        self.assertNotIn("path", list(inspect.signature(api.compare).parameters)[0])
        for kw in ("key", "tolerance", "measure", "spec"):
            self.assertIn(kw, inspect.signature(api.compare).parameters)
        for kw in ("doc", "actor", "kb_db"):
            self.assertIn(kw, inspect.signature(api.grade_record).parameters)
        for kw in ("grade", "footnotes", "certainty", "run_id", "outcome_id"):
            self.assertIn(kw, inspect.signature(api.sof).parameters)
        for kw in ("instrument", "project_dir"):
            self.assertIn(kw, inspect.signature(api.appraisal_check).parameters)
        for kw in ("paths", "studies", "column", "row_uids"):
            self.assertIn(kw, inspect.signature(api.rob_sync_proposal).parameters)
        # a munkapad álnevei közül egyik sem lehet más jelentésű homlokzat-függvény
        for n in ("build_consensus", "appraisal_agreement", "kettos_compare", "kettos_consensus", "instrument",
                  "instruments"):
            self.assertFalse(callable(getattr(api, n, None)), n)

    def test_capabilities_and_rule_titles(self):
        cap = api.capabilities()
        names = {c["name"]: c for c in cap["commands"]}
        for n in ("appraisal", "grade", "kettos", "figure", "prisma"):
            self.assertIn(n, names)
            for d in names[n]["in"] + names[n]["out"]:
                self.assertIn(d, cap["contracts"], (n, d))
        self.assertIn("szk.ff.flowchart/v1", names["prisma"]["out"])
        self.assertEqual(contract(cap, "capabilities"), [])
        for code in audit.RULES:
            self.assertIn(code, api.RULE_TITLES_EN)
        by = {r["id"]: r for r in api.rules_export()}
        self.assertEqual(by["X019"]["title"]["en"], audit.TITLES_EN["X019"])

    def test_help_lists_v1_commands(self):
        rc, out, _err = run_cli("-h")
        self.assertEqual(rc, 0)
        for c in ("appraisal", "grade", "kettos", "figure"):
            self.assertIn(c, out)
        rc, out, _err = run_cli("appraisal")
        self.assertEqual(rc, 0)
        self.assertIn("sync-rob", out)
        rc, out, _err = run_cli("grade")
        self.assertIn("amstar2", out)


# ================================================================== értékelés
class TestAppraisalCli(_Base):
    def test_instruments_schema_route(self):
        rows = self.parity(["appraisal", "instruments", "--json"], api.instruments_list())
        self.assertEqual(sorted(r["key"] for r in rows), I.available())
        doc = self.parity(["appraisal", "schema", "probast-ai", "--json"], api.instrument_get("probast-ai"))
        self.assertEqual(contract(doc, "instrument"), [])
        self.assertEqual(sum(1 for it in doc["items"]), 34)
        self.parity(["ertekeles", "route", "randomised controlled trial", "--json"],
                    api.instrument_route("randomised controlled trial"))
        rc, _out, err = run_cli("appraisal", "schema", "nincs-ilyen")
        self.assertEqual(rc, 1)
        self.assertIn("HIBA", err)

    def test_check_validate_and_text(self):
        res = self.parity(["appraisal", "check", self.file_a, "--project", self.root, "--json"],
                          api.appraisal_check(self.file_a, project_dir=self.root))
        self.assertEqual(contract(res, "appraisal-result"), [])
        self.assertTrue(res["complete"])
        self.assertEqual(res["overall"]["implied"], "low")
        comp = api.appraisal_completeness(self.file_a)
        self.assertEqual((comp["complete"], comp["completeness_text"]), (True, res["completeness_text"]))
        self.assertEqual(api.appraisal_implied(self.file_a)["overall"], res["overall"])
        prob = self.parity(["appraisal", "validate", self.file_a, "--json"], api.appraisal_problems(self.file_a))
        self.assertEqual((prob["ok"], prob["errors"]), (True, []))
        rc, out, _err = run_cli("appraisal", "check", self.file_a)
        self.assertEqual(rc, 0)
        self.assertIn("Teljesség: 22/22", out)
        self.assertIn("NEM a hivatalos", out)
        # hiányos értékelés: kilépési kód 1
        d = rob2("S03", status="draft")
        del d["answers"]["4.3"]
        f = os.path.join(self.tmp, "hianyos.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        rc, out, _err = run_cli("appraisal", "check", f)
        self.assertEqual(rc, 1)
        self.assertIn("Hiányzó válasz: 4.3", out)

    def test_list_agreement_summary_sync_parity(self):
        rows = self.parity(["appraisal", "list", self.root, "--json"], api.appraisal_list(self.root))
        self.assertEqual([r["unit"] for r in rows], ["S01", "S02", "S10"])
        self.assertTrue(all(r["complete"] for r in rows))
        ag = self.parity(["appraisal", "agreement", self.file_a, self.file_b, "--json"],
                         api.appraisal_consensus(self.file_a, self.file_b))
        self.assertEqual(contract(ag, "ma.appraisal-agreement"), [])
        self.assertEqual([x["key"] for x in ag["disagreements"]], ["1.2"])
        rs = self.parity(["appraisal", "rob-summary", self.root, "--outcome", "o1", "--plot", self.run_dir, "--json"],
                         api.project_rob_summary(self.root, "o1", plot=self.run_dir))
        self.assertEqual(contract(rs, "rob-summary"), [])
        self.assertEqual(sorted(s["study_id"] for s in rs["studies"]), ["S01", "S02", "S10"])
        self.assertIsNotNone(rs["high_weight_pct"])
        prop = self.parity(["appraisal", "sync-rob", self.root, "--outcome", "o1", "--json"],
                           api.project_rob_sync(self.root, "o1"))
        self.assertEqual(contract({k: v for k, v in prop.items() if k not in ("table", "table_sha256", "outcome")},
                                  "ma.rob-sync-proposal"), [])
        # S10 (systematic → high a táblában) ítélete high: változatlan; S01, S02 low = low
        self.assertEqual(prop["changes"], [])

    def test_save_approve_consensus_and_sync_apply(self):
        root = self.copy_project()
        # mentés (a fájlnév a tartalomból)
        rc, out, err = run_cli("appraisal", "save", self.file_b, "--project", root, "--json")
        self.assertEqual(rc, 0, err)
        saved = json.loads(out)
        self.assertEqual(saved["path"], "04_torzitas_kockazat/appraisals/S01.rob2.o1.KP.json")
        self.assertTrue(os.path.isfile(os.path.join(root, *saved["path"].split("/"))))
        # konszenzus: feloldatlan tétel → draft, kilépési kód 1; feloldással consensus
        rc, out, _err = run_cli("appraisal", "consensus", self.file_a, self.file_b, "--project", root, "--json")
        self.assertEqual(rc, 1)
        res = json.loads(out)
        self.assertEqual((res["doc"]["status"], res["unresolved"]), ("draft", ["1.2"]))
        rfile = os.path.join(root, "resolutions.json")
        with open(rfile, "w", encoding="utf-8") as fh:
            json.dump({"1.2": {"value": "yes", "reason": "egyeztetve a cikk 3. oldala alapján"}}, fh)
        rc, out, err = run_cli("appraisal", "consensus", self.file_a, self.file_b, "--resolutions", rfile,
                               "--project", root, "--json")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertEqual((res["doc"]["status"], res["unresolved"]), ("consensus", []))
        self.assertEqual(res["saved"]["path"], "04_torzitas_kockazat/appraisals/S01.rob2.o1.consensus.json")
        self.assertEqual(contract(res["doc"], "appraisal"), [])
        # AI-vázlat: jóváhagyás nélkül nem lehet kész; jóváhagyással igen (6. döntés)
        draft = rob2("S03", status="draft", origin="ai_draft", ai=True, judged=False)
        df = os.path.join(root, "ai.json")
        with open(df, "w", encoding="utf-8") as fh:
            json.dump(draft, fh, ensure_ascii=False)
        prob = api.appraisal_problems(df, project_dir=root)
        self.assertTrue(prob["ok"], prob)
        rc, out, err = run_cli("appraisal", "approve", df, "--approver", "SzK", "--project", root, "--json")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertEqual((res["doc"]["status"], res["doc"]["approved_by"]), ("complete", "SzK"))
        self.assertEqual(res["saved"]["path"], "04_torzitas_kockazat/appraisals/S03.rob2.o1.ai.json")
        # az AI-vázlat sosem értékelő (κ)
        rc, _out, err = run_cli("appraisal", "agreement", df, self.file_a)
        self.assertEqual(rc, 1)
        self.assertIn("HIBA", err)
        # rob-szinkron alkalmazása: S10 ítélete legyen low → a tábla high-ja változik, döntés a naplóba
        A.save(root, rob2("S10", "SzK"), now=TS)
        rc, out, err = run_cli("appraisal", "sync-rob", root, "--outcome", "o1", "--apply", "--actor", "SzK", "--json")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertEqual([(c["row_uid"] is not None, c["before"], c["after"]) for c in res["proposal"]["changes"]],
                         [(True, "high", "low")])
        self.assertEqual(res["result"]["applied"], 1)
        dec = api.project_show(root, "decision", res["result"]["decision_id"])
        self.assertIn("RoB-oszlop szinkron (rob2, 1 cella)", dec["decision"])
        self.assertEqual(dec["context"]["code"], "X003")
        self.assertEqual(api.project_rob_sync(root, "o1")["changes"], [])

    def test_appraisal_errors_are_hungarian(self):
        root = self.copy_project()
        d = rob2("S04", status="complete", overall="high")          # implikált low, ítélet high indoklás nélkül
        f = os.path.join(root, "x017.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        rc, _out, err = run_cli("appraisal", "save", f, "--project", root)
        self.assertEqual(rc, 1)
        self.assertIn("X017", err)
        self.assertIn("  - ", err)
        res = api.appraisal_set_judgement(d, "overall", "high", reason="A 2. domén valójában magas: a "
                                          "protokoll-eltérések nem elemzettek.", project_dir=root, actor="SzK")
        self.assertIsInstance(res["decision_id"], int)
        self.assertEqual(res["doc"]["overall"]["override_reason"][:10], "A 2. domén")


# ================================================================== GRADE / SoF / AMSTAR 2
class TestGradeCli(_Base):
    def test_advice_and_sof_parity(self):
        adv = self.parity(["grade", "advice", "--run", self.run_dir, "--project", self.root, "--json"],
                          api.grade_advice(self.run_dir, project_dir=self.root))
        self.assertEqual(contract(adv, "ma.grade"), [])
        self.assertTrue(all(d["rating"] is None for d in adv["domains"].values()), "csak piszkozat")
        rc, out, _err = run_cli("grade", "advice", "--run", self.run_dir, "--project", self.root)
        self.assertEqual(rc, 0)
        self.assertIn("Mit kérdez?", out)
        self.assertIn("feloldatlan", out)
        so = self.parity(["grade", "sof", "--run", self.run_dir, "--project", self.root, "--certainty", "moderate"],
                         api.sof(self.run_dir, project_dir=self.root, certainty="moderate"))
        self.assertEqual(contract(so, "ma.sof"), [])
        two = api.sof(self.run_dir, project_dir=self.root, assumed_risks=[{"source": "control_pool"},
                                                                          {"label": "alacsony", "per_1000": "12,5",
                                                                           "source": "Kovács 2020"}])
        self.parity(["grade", "sof", "--run", self.run_dir, "--project", self.root, "--assumed-risk", "pool",
                     "--assumed-risk", "alacsony=12,5@Kovács 2020"], two)
        self.assertEqual(len(two["rows"][0]["absolute"]), 2)
        rc, out, _err = run_cli("grade", "sof", "--run", self.run_dir, "--project", self.root, "--format", "md")
        self.assertEqual(out, api.sof_markdown(api.sof(self.run_dir, project_dir=self.root)) + "\n"
                         if not api.sof_markdown(api.sof(self.run_dir, project_dir=self.root)).endswith("\n")
                         else api.sof_markdown(api.sof(self.run_dir, project_dir=self.root)))
        csv_out = os.path.join(self.tmp, "sof.csv")
        rc, _out, _err = run_cli("grade", "sof", "--run", self.run_dir, "--project", self.root, "--format", "csv",
                                 "--out", csv_out)
        with open(csv_out, "rb") as fh:
            self.assertTrue(fh.read().startswith(b"\xef\xbb\xbf"), "Excel-biztos CSV: UTF-8 BOM")

    def test_save_record_flow_and_decision4(self):
        root = self.copy_project()
        d = api.grade_advice(self.run_dir, project_dir=root)
        for dom, rating, why in (("risk_of_bias", "not serious", "A magas RoB-ú vizsgálatok kizárása nem változtat."),
                                 ("inconsistency", "serious", "A predikciós intervallum átnyúlik az 1-en."),
                                 ("indirectness", "not serious", "A PICO egyezik."),
                                 ("imprecision", "not serious", "Az OIS teljesül.")):
            d["domains"][dom].update(rating=rating, rationale=why)
        d["domains"]["publication_bias"].update(rating="suspected")
        f = os.path.join(root, "grade.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        rc, out, err = run_cli("grade", "save", root, "--doc", f, "--json")
        self.assertEqual(rc, 0, err)
        saved = json.loads(out)
        self.assertIsNone(saved["certainty"], "4. döntés: a „gyanított” feloldatlan → nincs bizonyosság")
        self.assertEqual(api.grade_get(root, "o1"), saved)
        rc, out, _err = run_cli("grade", "show", root, "--outcome", "o1")
        self.assertEqual(json.loads(out), saved)
        rc, _out, err = run_cli("grade", "record", root, "--outcome", "o1")
        self.assertEqual(rc, 1)
        self.assertIn("X019", err)
        self.assertEqual(api.project_list(root, "grades"), [])
        saved["domains"]["publication_bias"].update(step=0, rationale="Harbord és Peters nem jelez; a regiszter-"
                                                                     "keresés teljes.")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(saved, fh, ensure_ascii=False)
        rc, out, err = run_cli("grade", "save", root, "--doc", f, "--json")
        self.assertEqual(json.loads(out)["certainty"], "moderate")
        rc, out, err = run_cli("grade", "record", root, "--outcome", "o1", "--actor", "user:SzK", "--json")
        self.assertEqual(rc, 0, err)
        rec = json.loads(out)
        self.assertEqual((rec["doc"]["status"], rec["doc"]["journal_id"]), ("recorded", rec["id"]))
        self.assertEqual(api.project_show(root, "grade", rec["id"])["certainty"], "moderate")
        self.assertEqual(contract(api.grade_get(root, "o1"), "ma.grade"), [])
        # SoF mentése a rögzített ítéletből (X008 ezt veti össze)
        rc, out, err = run_cli("grade", "sof", "--run", self.run_dir, "--project", root, "--save")
        self.assertEqual(rc, 0, err)
        self.assertEqual(api.sof_load(root, "o1")["rows"][0]["certainty"], "moderate")
        self.assertEqual([g["outcome_id"] for g in api.grade_list(root)], ["o1"])
        with self.assertRaises(ValueError):
            api.grade_put(root, "o2", saved)                # a dokumentum kimenete o1

    def test_amstar2(self):
        answers = {str(i): "yes" for i in range(1, 17)}
        answers.update({"2": "partial_yes", "9": "no"})
        f = os.path.join(self.tmp, "amstar.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(answers, fh)
        res = self.parity(["grade", "amstar2", "--answers", f, "--claimed", "high", "--json"],
                          api.amstar2_consistency(answers, claimed="high"))
        self.assertEqual(res["rating"], "low")
        self.assertTrue(res["consistency_warning"])
        rc, out, _err = run_cli("grade", "amstar2", "--answers", f)
        self.assertIn("AMSTAR 2", out)


# ================================================================== kettős kinyerés, S08-kapu
class TestKettos(_Base):
    def setUp(self):
        self.proj = self.copy_project()
        d = os.path.join(self.proj, "03_adatok", "kettos")
        os.makedirs(d)
        self.a = os.path.join(d, "o1.A.csv")
        self.b = os.path.join(d, "o1.B.csv")
        write_table(self.a)

        def typo(cells):
            cells[3] = "360"                             # S02 n1: 306 → 360 (számjegycsere)
            return cells
        write_table(self.b, {2: typo})

    def test_compare_report_status_parity(self):
        res = self.parity(["kettos", "compare", self.a, self.b, "--json"], api.compare(self.a, self.b))
        self.assertEqual(contract(res, "ma.compare-result"), [])
        self.assertEqual(len([x for x in res["disagreements"] if not x.get("auto")]), 1)
        self.parity(["kettos", "compare", "--project", self.proj, "--outcome", "o1", "--json"],
                    api.kettos_project_compare(self.proj, "o1"))
        self.parity(["kettős", "report", "--project", self.proj, "--outcome", "o1", "--json"],
                    api.kettos_project_report(self.proj, "o1"))
        st = self.parity(["kettos", "status", self.proj, "--json"], api.kettos_status(self.proj), code=1)
        self.assertEqual(st["unresolved_total"], 1)

    def test_s08_gate_and_reconcile(self):
        errs = api.checkpoint_gate_errors(self.proj, "S08")
        self.assertEqual([f["code"] for f in errs], ["X009"])
        self.assertEqual(api.checkpoint_gate_errors(self.proj, "S05"), [])
        with self.assertRaises(ValueError) as cm:
            P.checkpoint(self.proj, "S08", "ma-ellenorzo", "PASS")
        self.assertIn("X009", str(cm.exception))
        self.assertIn("S08 PASS", str(cm.exception))
        rc, _out, err = run_cli("project", "checkpoint", self.proj, "--stage", "S08", "--agent", "ma-ellenorzo",
                                "--verdict", "PASS")
        self.assertEqual(rc, 1)
        self.assertIn("X009", err)
        # a korábbi szakasz és a FAIL ítélet nem kapuzott
        P.checkpoint(self.proj, "S05", "ma-ellenorzo", "PASS")
        P.checkpoint(self.proj, "S08", "ma-ellenorzo", "FAIL")
        # egyeztetés → a kapu nyílik
        item = next(x for x in api.compare(self.a, self.b)["disagreements"] if not x.get("auto"))
        dec = os.path.join(self.proj, "d.json")
        with open(dec, "w", encoding="utf-8") as fh:
            json.dump([{"key": item["key"], "field": item["field"], "chosen": "a",
                        "reason": "A cikk 2. táblázata szerint 306."}], fh, ensure_ascii=False)
        rc, out, err = run_cli("kettos", "reconcile", "--project", self.proj, "--outcome", "o1", "--decisions", dec,
                               "--actor", "SzK", "--write-csv", "--json")
        self.assertEqual(rc, 0, err)
        cons = json.loads(out)
        self.assertEqual(contract(cons, "ma.consensus"), [])
        self.assertEqual(cons["unresolved"], [])
        self.assertEqual(api.checkpoint_gate_errors(self.proj, "S08"), [])
        P.checkpoint(self.proj, "S08", "ma-ellenorzo", "PASS")
        self.assertEqual(api.kettos_status(self.proj)["unresolved_total"], 0)


# ================================================================== PRISMA, ábra
FLOW = {"identified_databases": 120, "duplicates_removed": 20, "screened": 100, "excluded_screening": 80,
        "sought": 20, "not_retrieved": 2, "assessed": 18, "excluded_eligibility": 5,
        "excluded_eligibility_reasons": {"rossz populáció": 3, "nincs kimenet": 2}, "included_reports": 13}


class TestPrismaAndFigure(_Base):
    def setUp(self):
        self.flow = os.path.join(self.tmp, "flow.json")
        with open(self.flow, "w", encoding="utf-8") as fh:
            json.dump(FLOW, fh, ensure_ascii=False)
        self.studies = os.path.join(self.tmp, "studies.json")
        st = {"schema": "szk.ma.studies/v1", "studies": [
            {"study_id": "S%02d" % i, "reports": [{"rec_id": "r%02d" % i}]} for i in range(1, 14)]}
        with open(self.studies, "w", encoding="utf-8") as fh:
            json.dump(st, fh)

    def test_prisma_studies_and_flowchart(self):
        want = api.prisma_check(FLOW, studies=self.studies)
        res = self.parity(["prisma", "check", "--json", self.flow, "--studies", self.studies, "--out-format", "json"],
                          want, code=0 if want["ok"] else 1)
        self.assertEqual(res["counts"]["included_studies"], 13, "az I a vizsgálat-térképből")
        out = os.path.join(self.tmp, "flowchart.json")
        rc, stdout, err = run_cli("prisma", "check", "--json", self.flow, "--studies", self.studies, "--out-format",
                                  "json", "--emit-flowchart", out, "--flowchart-lang", "hu")
        self.assertEqual(json.loads(stdout), res, "a JSON-kimenet a folyamatábrával is változatlan")
        self.assertIn("ff.py flowchart", err)
        with open(out, encoding="utf-8") as fh:
            spec = json.load(fh)
        self.assertEqual(spec, plain(api.prisma_flowchart(FLOW, studies=self.studies, lang="hu")))
        self.assertEqual(contract(spec, "ff.flowchart"), [])
        # eltérő I → P017
        bad = dict(FLOW, included_studies=12)
        codes = [f["code"] for f in api.prisma_check(bad, studies=self.studies)["findings"]]
        self.assertIn("P017", codes)

    def test_figure_rerender_equals_run_svg(self):
        with open(os.path.join(self.run_dir, "plot_data.json"), encoding="utf-8") as fh:
            plot = json.load(fh)
        for kind in ("cumulative", "bubble"):
            res = self.parity(["figure", "--plot", self.run_dir, "--kind", kind, "--json"],
                              api.render_figure(plot, kind))
            with open(os.path.join(self.run_dir, "%s.svg" % kind), encoding="utf-8") as fh:
                self.assertEqual(res["svg"], fh.read(), kind)
        out = os.path.join(self.tmp, "loo_en.svg")
        rc, _o, err = run_cli("abra", "--plot", os.path.join(self.run_dir, "run.json"), "--kind", "loo", "--lang",
                              "en", "--annotate", "--out", out)
        self.assertEqual(rc, 0, err)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), api.render_figure(plot, "loo", "en", True)["svg"])
        self.assertIsNone(api.render_figure(plot, "forest"))
        # a riport megemlíti az E4c ábrákat
        with open(os.path.join(self.run_dir, "report.md"), encoding="utf-8") as fh:
            self.assertIn("`cumulative.svg`, `bubble.svg`", fh.read())


# ================================================================== további homlokzat-függvények
class TestMoreFacade(_Base):
    def test_pooled_agreement_tripod_amstar_and_ai_draft(self):
        a2, b2 = rob2("S02", "SzK"), rob2("S02", "KP", **{"2_1": "probably_yes"})
        pooled = api.appraisal_agreement_pooled([(self.file_a, self.file_b), (a2, b2)])
        self.assertEqual(contract(pooled, "ma.appraisal-agreement"), [])
        self.assertEqual((pooled["pairs"], pooled["items_compared"], pooled["disagree"]), (2, 44, 2))
        empty = {"schema": "szk.appraisal/v1", "tool": "tripod-ai", "scope": "both", "target": {"unit": "manuscript"},
                 "assessor": "SzK", "status": "draft", "origin": "human", "answers": {}}
        tr = api.tripod_check(empty)
        self.assertEqual((tr["counts"]["unanswered"], tr["completeness_text"]), (52, "0/52"), "H1: üres sablon 0/52")
        self.assertEqual(api.appraisal_completeness(empty)["completeness_text"], "0/52")
        rating = api.amstar2_rating({str(i): "yes" for i in range(1, 17)})
        self.assertEqual(rating["rating"], "high")
        draft = rob2("S03", status="draft", origin="ai_draft", ai=True, judged=False)
        res = api.appraisal_approve_ai_draft(draft, "SzK", now=TS)
        self.assertEqual((res["doc"]["approved_by"], res["doc"]["status"], res["saved"]), ("SzK", "complete", None))
        bad = copy.deepcopy(draft)
        bad["answers"]["1.1"] = {"value": "yes"}
        with self.assertRaises(A.AppraisalError) as cm:
            api.appraisal_approve_ai_draft(bad, "SzK")
        self.assertTrue(cm.exception.problems)
        self.assertFalse(api.appraisal_problems(bad)["ok"], "6. döntés: tételenkénti indoklás kell")

    def test_flowchart_out_and_project_reconcile_api(self):
        out = os.path.join(self.tmp, "fc_api.json")
        spec = api.prisma_flowchart(FLOW, out=out)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), plain(spec))
        root = self.copy_project()
        kd = os.path.join(root, "03_adatok", "kettos")
        os.makedirs(kd)
        write_table(os.path.join(kd, "o1.A.csv"))
        write_table(os.path.join(kd, "o1.B.csv"), {3: lambda c: c[:4] + ["2000"] + c[5:]})     # S03 n2
        res = api.kettos_project_compare(root, "o1")
        item = next(x for x in res["disagreements"] if not x.get("auto"))
        cons = api.kettos_project_reconcile(root, "o1", [{"key": item["key"], "field": item["field"], "chosen": "a",
                                                         "reason": "A forrás 1. táblázata."}], "SzK", write_csv=True)
        self.assertEqual((contract(cons, "ma.consensus"), cons["unresolved"]), ([], []))
        self.assertTrue(os.path.isfile(os.path.join(kd, "o1.consensus.csv")))
        rep = api.kettos_project_report(root, "o1")
        self.assertIn("κ", rep["text"]["hu"] + json.dumps(rep["table"], ensure_ascii=False))


# ================================================================== kereszt-modul javítások
class TestCrossModuleFixes(_Base):
    def test_robins_e_very_high_is_a_rob_category(self):
        for v in ("very high", "Very high risk of bias", "nagyon magas", "very_high"):
            self.assertEqual(tableio.rob_category(v), "high", v)
        levels, merged = tableio.rob_levels(["Very high", "very high", "critical"])
        self.assertEqual(levels, ["Very high", "Very high", "critical"], "külön szint, nem olvad a kritikusba")

    def test_unapproved_ai_draft_is_not_final_for_x003(self):
        root = self.copy_project()
        x003 = lambda: [f for f in audit.project_audit(root)["findings"] if f["code"] == "X003"]  # noqa: E731
        self.assertEqual(x003(), [])
        doc = rob2("S04", status="complete", origin="ai_draft", ai=True, overall="high")     # tábla: low
        path = os.path.join(root, *A.relpath("S04", "rob2", "o1", "ai").split("/"))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        self.assertEqual(x003(), [], "jóváhagyás nélküli AI-vázlat nem végső ítélet (6. döntés)")
        doc["approved_by"], doc["approved_at"] = "SzK", TS
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        self.assertEqual(len(x003()), 1, "jóváhagyva már végső")

    def test_gate_helpers(self):
        self.assertIsNone(audit.gated_stage(["S05"]))
        self.assertEqual(audit.gated_stage(["S07", "S08"]), "S08")
        self.assertEqual(audit.gated_stage(["FINAL"]), "S14")
        self.assertIn("a(z) S08 PASS", audit.gate_message([{"code": "X009", "outcome": "o1", "title": "t"}], "S08"))
        self.assertIn("a FINAL ellenőrzőpont", audit.gate_message([{"code": "X001", "outcome": None, "title": "t"}]))


if __name__ == "__main__":
    unittest.main()
