# -*- coding: utf-8 -*-
"""v1 javítások — A köteg (eszközök, értékelés, szerződések): a megerősített hibák regressziós tesztjei.

contracts:C1   a konszenzus-vázlat (feloldatlan tételekkel) nem írja felül az A értékelő saját fájlját
contracts:M1   a rob-szinkron csak a tábla kimenetére (vagy kimenet nélkül) szóló értékelést veszi
contracts:M4   GRADE-értékelés feloldatlan „gyanított” publikációs torzítással (és emberi bizonyosság nélkül) nem zárható
contracts:M5   kimenetenkénti értékelések target.key nélkül sem ütköznek; idegen fájl felülírása elutasítva
contracts:m2   a GRADE-blokk a szk.ma.grade/v1 kanonikus tokenjeit adja (very low, not serious …)
contracts:m3   séma-hibás értékre szerződés-hiba (nem TypeError)
contracts:m4   a fájlokból épített konszenzus rögzíti a forrásfájlok útját és hash-ét
methodology:M1 RoB 2: a 2019-es folyamatábra ágai (2.5 feltétel: „If Y/PY/NI to 2.4”), sosem enyhébb a hivatalosnál
methodology:M2 ROBINS-I 2016 tételkészlet (34; 30 / 32), útválasztás, NA
methodology:M3 ROBINS-E 2023: 3.3/5.3 feltételes, gyenge/erős nem, D1-felirat, eset-kontroll nem ROBINS-E
methodology:M4 egyezés: doménítéletek κ-ja elsődleges; kettős NA (útválasztás) kimarad
methodology:M7 eszköz-javaslat: „nem randomizált” nem RoB 2; klaszter / keresztezett figyelmeztetéssel
methodology:M10 QUIPS: a publikált prompting itemek (a–g), vágópont és modell-megfelelőség
methodology:M11 AMSTAR 2: 9. és 11. tétel RCT / NRSI résztétellel
methodology:M12 Newcastle–Ottawa: hivatalos tételszám (official_id) az eset-kontroll tételeknél"""
import copy
import hashlib
import itertools
import json
import os
import shutil
import tempfile
import unittest

from _helpers import ROOT  # noqa: F401  (sys.path)
from metaelemzes import api, appraisal as A, instruments as I, projekt

TS = "2026-10-05T10:00:00Z"
Y, PY, PN, N, NI, NA = "yes", "probably_yes", "probably_no", "no", "no_information", "not_applicable"
YS, NS = (Y, PY), (N, PN)
TIER = {"low": 0, "some_concerns": 1, "high": 2}


def contract(doc, name):
    return I.contract_errors(doc, name, 1)


def clean(tool, scope=None):
    inst = I.load(tool)
    out = {}
    for it in inst.slots(scope):
        allowed = inst.allowed(it)
        out[it["key"]] = N if it.get("polarity") == "reverse" else (Y if Y in allowed else allowed[0])
    return out


def rob2_answers(**ch):
    """Tiszta RoB 2 válaszok a hivatalos útválasztással (a nem kérdezett tétel 'Nem alkalmazható')."""
    ans = {"1.1": Y, "1.2": Y, "1.3": N, "2.1": Y, "2.2": Y, "2.3": N, "2.4": NA, "2.5": NA, "2.6": Y, "2.7": NA,
           "3.1": Y, "3.2": NA, "3.3": NA, "3.4": NA, "4.1": N, "4.2": N, "4.3": N, "4.4": NA, "4.5": NA,
           "5.1": Y, "5.2": N, "5.3": N}
    ans.update({k.replace("_", "."): v for k, v in ch.items()})
    return ans


def doc_of(tool, answers, unit="S1", assessor="SzK", status="draft", scope=None, **target):
    tg = {"unit": unit, "study_id": unit}
    tg.update(target)
    return {"schema": "szk.appraisal/v1", "tool": tool, "scope": scope, "target": tg, "assessor": assessor,
            "second_assessor": None, "status": status, "origin": "human", "approved_by": None, "approved_at": None,
            "answers": {k: {"value": v} for k, v in answers.items()}, "domain_judgements": [], "applicability": [],
            "overall": None}


def rob2_doc(unit="S1", assessor="SzK", status="draft", judged=True, **ch):
    d = doc_of("rob2", rob2_answers(**ch), unit=unit, assessor=assessor, status=status, scope="assignment")
    if judged:
        r = A.check(d)
        d["domain_judgements"] = [{"domain": x["domain"], "pass": None, "judgement": x["implied"]}
                                  for x in r["domains"]]
        d["overall"] = {"judgement": r["overall"]["implied"], "rationale": "indok"}
    return d


def dom(res, did):
    return next(x for x in res["domains"] if x["domain"] == did)


class _Proj(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="szk_fixA_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "p")
        projekt.init(self.proj, "A köteg")

    def full(self, rel):
        return os.path.join(self.proj, *rel.split("/"))

    def read(self, rel):
        with open(self.full(rel), "rb") as fh:
            return fh.read()


# ================================================================== contracts:C1
class TestC1DraftConsensusPath(_Proj):
    def test_draft_consensus_never_overwrites_rater_file(self):
        a = rob2_doc(assessor="SzK", status="complete")
        b = rob2_doc(assessor="KP", status="complete", **{"1_2": PY, "2_1": N})
        pa = A.save(self.proj, a, now=TS)["path"]
        pb = A.save(self.proj, b, now=TS)["path"]
        before = self.read(pa)
        res = api.appraisal_build_consensus(self.full(pa), self.full(pb), project_dir=self.proj, now=TS)
        self.assertIn("1.2", res["unresolved"])
        self.assertEqual(res["doc"]["status"], "draft")
        self.assertTrue(res["saved"]["path"].endswith("S1.rob2.consensus.json"), res["saved"])
        self.assertEqual(self.read(pa), before, "az A értékelő független értékelése érintetlen")
        self.assertEqual(json.loads(self.read(pa))["status"], "complete")
        self.assertNotIn("consensus_of", json.loads(self.read(pa)))

    def test_draft_consensus_is_not_a_rater(self):
        a, b = rob2_doc(assessor="SzK"), rob2_doc(assessor="KP", **{"1_2": PY})
        draft, unresolved = A.build_consensus(a, b, now=TS)
        self.assertEqual((draft["status"], unresolved[:1]), ("draft", ["1.2"]))
        self.assertTrue(A.is_consensus_doc(draft))
        self.assertEqual(A.rater_of(draft), "consensus")
        self.assertFalse(A.is_rater(draft))
        with self.assertRaises(A.AppraisalError):
            A.appraisal_consensus(draft, b)
        bad = dict(draft, status="complete")
        self.assertTrue(any("konszenzus-dokumentum" in e for e in A.validate(bad)))


# ================================================================== contracts:M1
class TestM1RobSyncOutcome(_Proj):
    def setUp(self):
        _Proj.setUp(self)
        projekt.save_project_meta(self.proj, {"title": "A köteg", "data_class": "A", "review_type": "intervention",
                                              "appraisal_tools": ["rob2"],
                                              "outcomes": [{"id": "o1", "name": "Halálozás",
                                                            "data": "03_adatok/o1.csv", "measure": "RR"}]})
        os.makedirs(os.path.join(self.proj, "03_adatok"), exist_ok=True)
        with open(os.path.join(self.proj, "03_adatok", "o1.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write("study_id;study;e1;n1;e2;n2;rob\nS3;Rosenthal 1960;3;231;11;220;low\n")

    def test_other_outcome_appraisal_is_ignored(self):
        d = rob2_doc(unit="S3", status="complete", **{"1_2": N})
        d["target"].update(outcome="o2", key="o2")
        d["overall"]["judgement"] = "high"
        A.save(self.proj, d, now=TS)
        prop = A.project_rob_sync(self.proj, "o1")
        self.assertEqual(prop["changes"], [], "az o2-re szóló értékelés nem írhat az o1 táblájába")
        self.assertEqual(prop["outcome"], "o1")
        self.assertEqual(contract({k: v for k, v in prop.items() if k not in ("table", "table_sha256")},
                                  "ma.rob-sync-proposal"), [])
        header = ["study_id", "study", "rob"]
        rows = [["S3", "Rosenthal 1960", "low"]]
        self.assertEqual(A.rob_sync_proposal([d], header, rows, "rob2", outcome="o1")["changes"], [])
        self.assertEqual([c["after"] for c in A.rob_sync_proposal([d], header, rows, "rob2")["changes"]], ["high"],
                         "kimenet nélkül (kimenet-független hívás) a régi viselkedés marad")
        self.assertEqual([c["after"] for c in A.rob_sync_proposal([d], header, rows, "rob2", outcome="o2")["changes"]],
                         ["high"])


# ================================================================== contracts:M4 (GRADE)
def grade_doc(status="complete", pb="suspected", resolution=None, judgement="high", **ch):
    ans = {"0.1": "high", "1.1": "not_serious", "2.1": "not_serious", "3.1": "not_serious", "4.1": "not_serious",
           "5.1": pb, "6.1": "no", "7.1": "no", "8.1": "no"}
    ans.update({k.replace("_", "."): v for k, v in ch.items()})
    d = doc_of("grade", ans, unit="o1", status=status, scope="all", outcome="o1")
    d["target"].pop("study_id")
    if resolution is not None:
        d["answers"]["5.1"]["resolution"] = resolution
    d["overall"] = {"judgement": judgement, "rationale": "x"} if judgement else None
    return d


class TestM4GradeFinal(_Proj):
    def test_unresolved_suspected_cannot_be_final(self):
        d = grade_doc()
        p = api.appraisal_problems(d)
        self.assertFalse(p["ok"])
        self.assertTrue(any("FELOLDATLAN" in e for e in p["errors"]), p["errors"])
        with self.assertRaises(A.AppraisalError) as cm:
            A.save(self.proj, d, now=TS)
        self.assertTrue(any("FELOLDATLAN" in e for e in cm.exception.problems))
        draft = grade_doc(status="draft")
        self.assertEqual(A.validate(draft), [], "vázlatként menthető")
        A.save(self.proj, draft, now=TS)

    def test_resolved_and_human_certainty_can_be_final(self):
        ok = grade_doc(resolution={"step": -1, "rationale": "kis ipari vizsgálatok"}, judgement="moderate")
        self.assertEqual(A.check(ok)["grade"]["certainty"], "moderate")
        A.save(self.proj, ok, now=TS)
        undecided = grade_doc(pb="undetected", judgement=None)
        errs = A.validate(undecided)
        self.assertTrue(any("embernek kell rögzítenie" in e for e in errs), errs)
        res = A.check(undecided)
        self.assertEqual(res["grade"]["certainty"], "high")
        self.assertTrue(res["grade"]["computed"])
        self.assertTrue(any("csak javaslat" in x["hu"] for x in res["grade"]["lines"]))

    def test_ai_draft_approval_does_not_finalise_unresolved(self):
        d = grade_doc(status="draft", judgement=None)
        d["origin"], d["assessor"] = "ai_draft", "ai"
        for a in d["answers"].values():
            a["rationale"] = {"asks": "Mit kérdez.", "because": "Miért.", "change": "Mi változtatná meg.",
                              "uncertain": "A cikk nem közli."}
        out, res = A.approve_ai_draft(d, "SzK", now=TS)
        self.assertTrue(res["complete"])
        self.assertEqual(out["status"], "draft", "feloldatlan torzítás és emberi bizonyosság nélkül nem lezárt")


# ================================================================== contracts:M5
class TestM5PerOutcomeFiles(_Proj):
    def test_outcome_without_key_gets_its_own_file(self):
        d1 = rob2_doc(unit="S5", status="complete")
        d1["target"] = {"study_id": "S5", "outcome": "o1"}
        d2 = rob2_doc(unit="S5", status="complete", **{"1_1": N})
        d2["target"] = {"study_id": "S5", "outcome": "o2"}
        r1, r2 = A.save(self.proj, d1, now=TS), A.save(self.proj, d2, now=TS)
        self.assertEqual((r1["path"].rsplit("/", 1)[1], r2["path"].rsplit("/", 1)[1]),
                         ("S5.rob2.o1.SzK.json", "S5.rob2.o2.SzK.json"))
        self.assertEqual((r1["doc"]["target"]["key"], r2["doc"]["target"]["key"]), ("o1", "o2"))
        self.assertEqual(json.loads(self.read(r1["path"]))["target"]["outcome"], "o1", "az o1 értékelés megmaradt")
        self.assertEqual(A.target_key({"tool": "rob2", "target": {"outcome": "o 1/x"}}), "o_1_x")
        self.assertIsNone(A.target_key({"tool": "amstar2", "target": {"unit": "review"}}))

    def test_foreign_file_is_not_overwritten_silently(self):
        d1 = rob2_doc(unit="S6", status="complete")
        d1["target"].update(key="primary", outcome="o1")
        r1 = A.save(self.proj, d1, now=TS)
        d2 = copy.deepcopy(d1)
        d2["target"]["outcome"] = "o2"
        with self.assertRaises(A.AppraisalError) as cm:
            A.save(self.proj, d2, now=TS)
        self.assertIn("target.outcome", cm.exception.problems[0])
        A.save(self.proj, d2, now=TS, expect_sha256=r1["sha256"])        # tudatos csere: hash-sel engedett
        d3 = copy.deepcopy(d2)
        d3["answers"]["1.1"]["value"] = PY
        A.save(self.proj, d3, now=TS)                                    # ugyanaz az értékelés: frissíthető


# ================================================================== contracts:m2
class TestM2GradeVocabulary(unittest.TestCase):
    def test_grade_block_uses_ma_grade_tokens(self):
        d = grade_doc(status="draft", pb="strongly_suspected", judgement=None, **{"0_1": "low", "1_1": "serious"})
        g = A.check(d)["grade"]
        self.assertEqual((g["certainty"], g["certainty_value"], g["vocabulary"]), ("very low", "very_low",
                                                                                 "szk.ma.grade/v1"))
        self.assertEqual(g["domains"]["inconsistency"]["rating"], "not serious")
        self.assertEqual(g["domains"]["publication_bias"]["rating"], "strongly suspected")
        self.assertEqual(g["domains"]["publication_bias"]["value"], "strongly_suspected")
        schema = I.contract_errors.__globals__["_registry"]()["urn:szk:contract:ma.grade:1"]
        self.assertIn(g["certainty"], schema["properties"]["certainty"]["enum"])
        self.assertEqual(A.check(d)["overall"]["implied"], "very_low", "az összítélet az eszköz ítéletskáláján")
        self.assertEqual(contract(A.check(d), "appraisal-result"), [])

    def test_sof_accepts_appraisal_certainty(self):
        import test_v1_facade as T
        tmp = tempfile.mkdtemp(prefix="szk_fixA_sof_")
        self.addCleanup(shutil.rmtree, tmp, True)
        _view, run_dir = T.make_project(os.path.join(tmp, "p"))
        d = grade_doc(status="draft", pb="undetected", judgement=None, **{"0_1": "low", "1_1": "serious"})
        cert = api.appraisal_check(d)["grade"]["certainty"]
        sof = api.sof(run_dir, certainty=cert)
        self.assertEqual(sof["schema"], "szk.ma.sof/v1")


# ================================================================== contracts:m3
class TestM3TypeRobustness(unittest.TestCase):
    def mutations(self):
        base = rob2_doc()
        m1 = copy.deepcopy(base)
        m1["answers"]["1.1"] = {"value": ["yes"]}
        m2 = copy.deepcopy(base)
        m2["domain_judgements"] = [{"domain": "1", "pass": ["x"], "judgement": "low"}]
        m3 = copy.deepcopy(base)
        m3["domain_judgements"] = [{"domain": "1", "judgement": ["low"]}]
        m4 = copy.deepcopy(base)
        m4["answers"] = [1, 2]
        return {"answer_value_list": m1, "dj_pass_list": m2, "dj_judgement_list": m3, "answers_list": m4}

    def test_problems_report_contract_errors(self):
        for name, d in self.mutations().items():
            with self.subTest(name):
                p = api.appraisal_problems(d)
                self.assertFalse(p["ok"])
                self.assertTrue(p["errors"])

    def test_engine_functions_raise_appraisal_error(self):
        tmp = tempfile.mkdtemp(prefix="szk_fixA_m3_")
        self.addCleanup(shutil.rmtree, tmp, True)
        other = rob2_doc(assessor="KP")
        calls = {"check": lambda d: A.check(d), "save": lambda d: A.save(tmp, d, now=TS),
                 "agreement": lambda d: A.appraisal_consensus(d, other),
                 "rob_summary": lambda d: A.rob_summary([d], "rob2"),
                 "set_judgement": lambda d: A.set_judgement(d, "1", "high", reason="x"),
                 "consensus": lambda d: A.build_consensus(d, other)}
        for name, d in self.mutations().items():
            for fn, call in calls.items():
                with self.subTest(mutation=name, fn=fn):
                    try:
                        call(copy.deepcopy(d))
                    except A.AppraisalError:
                        pass


# ================================================================== contracts:m4
class TestM4ConsensusSources(_Proj):
    def test_consensus_records_source_paths_and_hashes(self):
        a, b = rob2_doc(assessor="SzK", status="complete"), rob2_doc(assessor="KP", status="complete", **{"1_2": PY})
        pa, pb = A.save(self.proj, a, now=TS)["path"], A.save(self.proj, b, now=TS)["path"]
        res = api.appraisal_build_consensus(self.full(pa), self.full(pb), project_dir=self.proj, now=TS,
                                            resolutions={"1.2": {"value": "Y", "reason": "egyeztetve"}})
        self.assertEqual(res["unresolved"], [])
        cof = {c["assessor"]: c for c in res["doc"]["consensus_of"]}
        self.assertEqual((cof["SzK"]["path"], cof["KP"]["path"]), (pa, pb))
        self.assertEqual(cof["SzK"]["sha256"], hashlib.sha256(self.read(pa)).hexdigest())
        self.assertEqual(contract(res["doc"], "appraisal"), [])


# ================================================================== methodology:M1 (RoB 2)
def ref_d1(v):
    """A RoB 2 (2019) 1. domén hivatalos ágai; az ellenőrizhetetlen ág (1.2 I + 1.1 N + 1.3 I) itt 'némi aggály'."""
    if v["1.2"] in NS:
        return "high"
    if v["1.2"] == NI:
        return "high" if v["1.3"] in YS else "some_concerns"
    if v["1.1"] in NS:
        return "some_concerns"
    return "some_concerns" if v["1.3"] in YS else "low"


def ref_d2(v):
    if v["2.1"] in NS and v["2.2"] in NS:
        p1 = "low"
    elif v["2.3"] in NS:
        p1 = "low"
    elif v["2.3"] == NI:
        p1 = "some_concerns"
    elif v["2.4"] in NS:
        p1 = "some_concerns"
    else:
        p1 = "some_concerns" if v["2.5"] in YS else "high"
    if v["2.6"] in YS:
        p2 = "low"
    else:
        p2 = "some_concerns" if v["2.7"] in NS else "high"
    return max(p1, p2, key=TIER.get)


def ref_d3(v):
    if v["3.1"] in YS or v["3.2"] in YS or v["3.3"] in NS:
        return "low"
    return "some_concerns" if v["3.4"] in NS else "high"


def ref_d4(v):
    if v["4.1"] in YS or v["4.2"] in YS:
        return "high"
    if v["4.3"] in NS or v["4.4"] in NS:
        return "low" if v["4.2"] in NS else "some_concerns"
    return "some_concerns" if v["4.5"] in NS else "high"


def ref_d5(v):
    if v["5.2"] in YS or v["5.3"] in YS:
        return "high"
    if NI in (v["5.2"], v["5.3"]):
        return "some_concerns"
    return "low" if v["5.1"] in YS else "some_concerns"


ALL5 = (Y, PY, PN, N, NI)


class TestM1Rob2Flowchart(unittest.TestCase):
    def implied(self, ans, did):
        return dom(A.check(doc_of("rob2", ans, scope="assignment")), did)["implied"]

    def test_verifier_cases(self):
        cases = [  # (változások, domén, hivatalos ítélet) — a megerősítő jelentés (a)–(g) esetei
            ({"2_3": Y, "2_4": Y, "2_5": Y}, "2", "some_concerns"),
            ({"2_3": NI}, "2", "some_concerns"),
            ({"2_3": Y, "2_4": N}, "2", "some_concerns"),
            ({"2_3": Y, "2_4": NI, "2_5": NI}, "2", "high"),
            ({"4_3": Y, "4_4": Y, "4_5": N}, "4", "some_concerns"),
            ({"4_3": Y, "4_4": Y, "4_5": NI}, "4", "high"),
            ({"2_3": Y, "2_4": NI, "2_5": NA}, "2", "high"),       # útválasztási ellentmondás: NA → NI
            ({"3_1": N, "3_2": Y}, "3", "low"),                     # túlminősítés javítva
            ({"5_1": N}, "5", "some_concerns"),
        ]
        for ch, did, want in cases:
            with self.subTest(ch=ch):
                self.assertEqual(self.implied(rob2_answers(**ch), did), want)
        r = A.check(doc_of("rob2", rob2_answers(**{"2_3": Y, "2_4": NI, "2_5": NA}), scope="assignment"))
        self.assertEqual(dom(r, "2")["routing_conflicts"], ["2.5"])

    def test_definition_and_label(self):
        inst = I.load("rob2")
        self.assertEqual(inst.item("2.5")["condition"]["en"], "If Y/PY/NI to 2.4")
        self.assertNotIn(NI, inst.allowed(inst.item("3.2")), "a hivatalos sablonban a 3.2-nek nincs NI válasza")
        self.assertEqual((inst.algorithm, inst.rollup["official"]), ("conservative", False))
        self.assertIn("NEM a hivatalos RoB 2", inst.rollup["label"]["hu"])

    def test_never_more_lenient_than_the_official_flowchart(self):
        def run(domain_items, fill, ref, did, exact=True):
            for combo in itertools.product(*[opts for _k, opts in domain_items]):
                v = dict(zip([k for k, _o in domain_items], combo))
                ans = rob2_answers(**{k.replace(".", "_"): fill(k, v) for k in v})
                got = self.implied(ans, did)
                want = ref({k: fill(k, v) for k in v})
                if exact:
                    self.assertEqual(got, want, (did, v))
                else:
                    self.assertGreaterEqual(TIER[got], TIER[want], (did, v))
        # 1. domén: legalább olyan szigorú; az egyetlen eltérés az ellenőrizhetetlen ág
        run([("1.1", ALL5), ("1.2", ALL5), ("1.3", ALL5)], lambda k, v: v[k], ref_d1, "1", exact=False)
        self.assertEqual(self.implied(rob2_answers(**{"1_1": N, "1_3": Y}), "1"), "high")

        def fill2(k, v):
            asked = {"2.3": v["2.1"] not in NS or v["2.2"] not in NS}
            asked["2.4"] = asked["2.3"] and v["2.3"] in YS
            asked["2.5"] = asked["2.4"] and v["2.4"] in YS + (NI,)
            asked["2.7"] = v["2.6"] not in YS
            return v[k] if asked.get(k, True) else NA
        run([("2.1", (Y, N, NI)), ("2.2", (Y, N, NI)), ("2.3", ALL5), ("2.4", (Y, N, NI)), ("2.5", (Y, N, NI)),
             ("2.6", (Y, N, NI)), ("2.7", (Y, N, NI))], fill2, ref_d2, "2")

        def fill3(k, v):
            asked = {"3.2": v["3.1"] not in YS}
            asked["3.3"] = asked["3.2"] and v["3.2"] in NS
            asked["3.4"] = asked["3.3"] and v["3.3"] not in NS
            return v[k] if asked.get(k, True) else NA
        run([("3.1", ALL5), ("3.2", (Y, PY, PN, N)), ("3.3", ALL5), ("3.4", ALL5)], fill3, ref_d3, "3")

        def fill4(k, v):
            asked = {"4.3": v["4.1"] not in YS and v["4.2"] not in YS}
            asked["4.4"] = asked["4.3"] and v["4.3"] not in NS
            asked["4.5"] = asked["4.4"] and v["4.4"] not in NS
            return v[k] if asked.get(k, True) else NA
        run([("4.1", (Y, N, NI)), ("4.2", (Y, N, NI)), ("4.3", (Y, N, NI)), ("4.4", (Y, N, NI)),
             ("4.5", ALL5)], fill4, ref_d4, "4")
        run([("5.1", ALL5), ("5.2", ALL5), ("5.3", ALL5)], lambda k, v: v[k], ref_d5, "5")


# ================================================================== methodology:M2 (ROBINS-I 2016)
class TestM2RobinsI2016(unittest.TestCase):
    def check(self, scope, **ch):
        ans = clean("robins-i", scope)
        ans.update({k.replace("_", "."): v for k, v in ch.items()})
        return A.check(doc_of("robins-i", ans, scope=scope))

    def test_item_set_numbering_and_scopes(self):
        inst = I.load("robins-i")
        ids = [it["id"] for it in inst.items]
        self.assertEqual(ids, ["1.%d" % i for i in range(1, 9)] + ["2.%d" % i for i in range(1, 6)] +
                         ["3.1", "3.2", "3.3"] + ["4.%d" % i for i in range(1, 7)] + ["5.%d" % i for i in range(1, 6)]
                         + ["6.1", "6.2", "6.3", "6.4"] + ["7.1", "7.2", "7.3"])
        self.assertEqual((len(inst.slots("assignment")), len(inst.slots("adherence"))), (30, 32))
        self.assertEqual(inst.doc["counts"]["published"], 34)
        self.assertEqual(inst.item("4.1")["scopes"], ["assignment"])
        self.assertEqual(inst.item("4.6")["scopes"], ["adherence"])
        self.assertEqual(inst.item("4.6")["condition"]["en"], "If N/PN to 4.3, 4.4 or 4.5")
        self.assertEqual(inst.item("1.3")["polarity"], "router")
        self.assertEqual(inst.item("1.7")["condition"]["en"], "If Y/PY to 1.3")
        self.assertEqual(inst.item("2.5")["condition"]["en"], "If Y/PY to 2.2 and 2.3, or N/PN to 2.4")
        for k in ("1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"):
            self.assertIn(NA, inst.allowed(inst.item(k)), k)
        self.assertNotIn(NI, inst.allowed(inst.item("1.1")), "az 1.1-nek nincs NI válasza")
        self.assertEqual(len(inst.doc["validator_ids"]), 31)

    def test_no_spurious_serious(self):
        self.assertNotIn("4.3", [it["key"] for it in I.load("robins-i").slots("assignment")])
        r = self.check("assignment", **{"1_1": Y, "1_2": Y, "1_3": Y, "1_7": Y, "1_8": Y})
        self.assertEqual(dom(r, "1")["implied"], "moderate", "jól kezelt idővel változó zavarás: mérsékelt")
        self.assertEqual(dom(self.check("assignment", **{"2_4": N, "2_5": Y}), "2")["implied"], "moderate")
        self.assertEqual(dom(self.check("assignment", **{"1_1": N, "1_2": NA, "1_6": NA}), "1")["implied"], "low")
        self.assertEqual(dom(self.check("adherence", **{"4_5": N, "4_6": Y}), "4")["implied"], "moderate")
        self.assertEqual(dom(self.check("adherence", **{"4_5": N, "4_6": N}), "4")["implied"], "serious")
        self.assertEqual(dom(self.check("assignment", **{"6_1": Y, "6_2": N}), "6")["implied"], "low")
        self.assertEqual(dom(self.check("assignment", **{"6_4": Y}), "6")["implied"], "serious")


# ================================================================== methodology:M3 (ROBINS-E 2023)
class TestM3RobinsE(unittest.TestCase):
    def check(self, **ch):
        ans = clean("robins-e")
        ans.update({k.replace("_", "."): v for k, v in ch.items()})
        return A.check(doc_of("robins-e", ans))

    def test_conditional_correction_questions(self):
        inst = I.load("robins-e")
        for k in ("3.3", "5.3"):
            self.assertIn(NA, inst.allowed(inst.item(k)))
        self.assertEqual(dom(self.check(**{"3_1": N, "3_2": Y, "3_3": N}), "3")["implied"], "low")
        self.assertEqual(dom(self.check(**{"5_1": Y, "5_2": N, "5_3": N}), "5")["implied"], "low")
        self.assertEqual(dom(self.check(**{"3_1": Y, "3_3": N}), "3")["implied"], "high")
        self.assertEqual(dom(self.check(**{"5_2": Y, "5_3": Y}), "5")["implied"], "low")

    def test_weak_and_strong_no_and_d1_label(self):
        inst = I.load("robins-e")
        self.assertEqual(inst.allowed(inst.item("1.1")), [Y, PY, "weak_no", "strong_no", NI])
        self.assertEqual(dom(self.check(**{"1_1": "WN"}), "1")["implied"], "some_concerns")
        self.assertEqual(dom(self.check(**{"1_1": "strong no"}), "1")["implied"], "high")
        lab = dom(self.check(), "1")["label"]["en"]
        self.assertEqual(lab, "Low risk of bias (except for concerns about uncontrolled confounding)")

    def test_case_control_not_routed_to_robins_e(self):
        tools = [h["tool"] for h in api.instrument_route("case-control study of exposure")]
        self.assertNotIn("robins-e", tools)
        self.assertIn("robins-e", [h["tool"] for h in api.instrument_route("cohort study of occupational exposure")])


# ================================================================== methodology:M4 (κ)
class TestM4DomainKappa(unittest.TestCase):
    def pair(self, unit, ja, jb, **chb):
        a = rob2_doc(unit=unit, assessor="AA", judged=False)
        b = rob2_doc(unit=unit, assessor="BB", judged=False, **chb)
        for d, js in ((a, ja), (b, jb)):
            d["domain_judgements"] = [{"domain": str(i + 1), "pass": None, "judgement": j} for i, j in enumerate(js)]
            d["overall"] = {"judgement": js[-1]}
        return a, b

    def test_domain_disagreement_is_visible(self):
        a, b = self.pair("S9", ["low", "low", "low", "low", "high"],
                         ["some_concerns", "high", "some_concerns", "high", "low"], **{"1_1": NI, "5_1": NI})
        ag = A.appraisal_consensus(a, b)
        self.assertEqual(contract(ag, "ma.appraisal-agreement"), [])
        self.assertEqual((ag["primary"], ag["kappa_level"]), ("domain", "item"))
        self.assertEqual(ag["judgement_kappa"]["agree"], 0)
        self.assertLess(ag["judgement_kappa"]["kappa"], 0.0, "egyetlen doménítélet sem egyezik")
        self.assertTrue(all(d["kappa"] is None for d in ag["domain_kappa"]), "egy párnál nem számolható")
        na_items = sum(1 for v in a["answers"].values() if v["value"] == NA)
        self.assertEqual((len(ag["excluded"]), ag["items_compared"]), (na_items, 22 - na_items))

    def test_pooled_domain_kappa_across_studies(self):
        pairs = [self.pair("S%d" % i, ["low"] * 5, (["low"] * 5) if i < 3 else ["high"] * 5) for i in range(1, 5)]
        ag = A.agreement(pairs)
        d1 = next(d for d in ag["domain_kappa"] if d["domain"] == "1")
        self.assertEqual((d1["n"], d1["agree"]), (4, 2))
        self.assertIsNotNone(d1["kappa"])
        self.assertEqual(ag["overall_kappa"]["n"], 4)


# ================================================================== methodology:M7 (útválasztás)
class TestM7Routing(unittest.TestCase):
    def tools(self, text):
        return [h["tool"] for h in api.instrument_route(text)]

    def test_non_randomised_is_not_rob2(self):
        for text in ("non-randomised study of intervention", "nem randomizált beavatkozásos vizsgálat",
                     "Non-randomized controlled trial", "quasi-randomised trial"):
            with self.subTest(text):
                self.assertEqual(self.tools(text), ["robins-i"])
        self.assertEqual(self.tools("randomised controlled trial"), ["rob2"])

    def test_cluster_and_crossover_warn(self):
        for text in ("cluster randomised trial", "crossover randomised trial", "rct_cluster", "rct_crossover"):
            with self.subTest(text):
                hits = api.instrument_route(text)
                self.assertEqual(hits[0]["tool"], "rob2")
                self.assertIn("változat", hits[0]["warning"]["hu"])
        self.assertNotIn("warning", api.instrument_route("RCT")[0])


# ================================================================== methodology:M10 (QUIPS)
class TestM10Quips(unittest.TestCase):
    def test_published_prompting_items(self):
        inst = I.load("quips")
        ids = [it["id"] for it in inst.items]
        self.assertEqual(len(ids), 31)
        self.assertEqual({d: sum(1 for i in ids if i[0] == d) for d in "123456"},
                         {"1": 6, "2": 5, "3": 6, "4": 3, "5": 7, "6": 4})
        self.assertIn("cut-point", inst.item("3c")["text"]["en"])
        self.assertIn("statistical model is adequate", inst.item("6c")["text"]["en"])
        texts = " ".join(it["text"]["en"].lower() for it in inst.items)
        for gone in ("after the outcome had begun", "assessors aware", "statistical significance",
                     "events per candidate"):
            self.assertNotIn(gone, texts)
        self.assertTrue(all(it.get("polarity") == "normal" for it in inst.items))
        r = A.check(doc_of("quips", dict(clean("quips"), **{"3c": "no"})))
        self.assertEqual(dom(r, "3")["implied"], "high", "adatvezérelt vágópont")


# ================================================================== methodology:M11 (AMSTAR 2)
class TestM11Amstar2Parts(unittest.TestCase):
    def doc(self, **parts):
        ans = {str(i): {"value": Y} for i in range(1, 17)}
        for k, pv in parts.items():
            ans[k] = {"parts": pv}
        d = doc_of("amstar2", {}, unit="review")
        d["target"] = {"unit": "review"}
        d["answers"] = ans
        return d

    def test_mixed_review_one_design_fails(self):
        d = self.doc(**{"9": {"RCT": Y, "NRSI": N}, "11": {"RCT": Y, "NRSI": "no meta-analysis"}})
        self.assertEqual(A.validate(d), [])
        r = A.check(d)
        self.assertEqual(r["amstar2"]["critical_flaws"], ["9"])
        self.assertEqual(r["amstar2"]["rating"], "low")
        self.assertEqual(r["amstar2"]["parts"]["9"], {"RCT": Y, "NRSI": N})
        self.assertEqual(A.normalize(d)[0]["answers"]["9"]["value"], N)
        self.assertEqual(contract(r, "appraisal-result"), [])
        only_rct = self.doc(**{"9": {"RCT": "partial yes", "NRSI": "na"}})
        self.assertEqual(A.check(only_rct)["amstar2"]["partial_yes_critical"], ["9"])
        self.assertEqual(api.amstar2_rating({"9": {"parts": {"RCT": Y, "NRSI": N}}})["critical_flaws"], ["9"])

    def test_inconsistent_value_and_missing_parts(self):
        bad = self.doc(**{"9": {"RCT": Y, "NRSI": N}})
        bad["answers"]["9"]["value"] = Y
        self.assertTrue(any("ellentmond" in e for e in A.validate(bad)))
        self.assertEqual(A.check(bad)["invalid"][0]["key"], "9")
        legacy = self.doc()
        r = A.check(legacy)
        self.assertEqual(r["amstar2"]["parts_missing"], ["9", "11"])
        self.assertTrue(any("KÜLÖN" in w["hu"] for w in r["warnings"]))


# ================================================================== methodology:M12 (Newcastle–Ottawa)
class TestM12NosOfficialIds(unittest.TestCase):
    def test_case_control_official_numbering(self):
        inst = I.load("nos")
        cc = [(it["id"], inst.display_id(it)) for it in inst.slots("case-control")]
        self.assertEqual([o for _i, o in cc], ["S1", "S2", "S3", "S4", "C1", "E1", "E2", "E3"])
        self.assertEqual([inst.display_id(it) for it in inst.slots("cohort")],
                         ["S1", "S2", "S3", "S4", "C1", "O1", "O2", "O3"])
        d = doc_of("nos", {}, scope="case-control")
        miss = {m["item"]: m["official_id"] for m in A.check(d)["missing"]}
        self.assertEqual((miss["S5"], miss["C2"]), ("S1", "C1"))


if __name__ == "__main__":
    unittest.main()
