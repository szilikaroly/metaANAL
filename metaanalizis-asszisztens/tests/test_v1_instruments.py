# -*- coding: utf-8 -*-
"""v1: natív értékelő eszközök (metaelemzes/instruments, szk.instrument/v1) és értékelések (metaelemzes/appraisal:
szk.appraisal/v1, szk.appraisal-result/v1, szk.rob-summary/v1, szk.ma.appraisal-agreement/v1,
szk.ma.rob-sync-proposal/v1; terv 4.11, 5.0 H1–H4, 5.4, 6.4–6.5, 9.3, 11. fejezet 4–6. döntés).

- a definíciók megfelelnek a sémának, belsőleg ellentmondásmentesek, a tételszámok a publikáltak (RoB 2 22,
  PROBAST+AI 16 + 18 = 34, TRIPOD+AI 52 D/E-címkével, AMSTAR 2 16 / 7 kritikus, GRADE 5 + 3 domén, NOS 8 + 8);
  a forrásmegjelölés és a licenc eszközönként kitöltött; a validator (ha elérhető) tételazonosítóival egyeznek;
- álnév-táblák eszközönként (H4: a 'PY' csak az AMSTAR 2-ben partial_yes);
- teljesség cellánként: PROBAST+AI csak fejlesztési kitöltéssel 16/34 (H2), üres TRIPOD+AI 0/52 (H1);
- implikált ítéletek 'algorithm' címkével (konzervatív = validátor-kompatibilis, nem hivatalos), AMSTAR 2 mindkét
  konvencióval, GRADE feloldatlan publikációs torzítással (4. döntés), NOS-csillagok küszöb nélkül;
- felülbírálás indoklással és naplódöntéssel (X017); AI-vázlat: tételindoklás, jóváhagyás, sosem értékelő (6. döntés);
- κ kézzel ellenőrzött értékkel; konszenzus; forgalmi lámpa súlyaránnyal; rob-oszlop szinkron és az X003-audit
  összhangja; atomi mentés."""
import copy
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import unittest
from fractions import Fraction

from _helpers import ROOT
from metaelemzes import appraisal as A
from metaelemzes import instruments as I
from metaelemzes import contracts as K
from metaelemzes import projekt

VALIDATOR_DIRS = [os.environ.get("SZK_VALIDATOR_DIR") or "",
                  os.path.join(os.path.dirname(ROOT), "..", "szilikaroly", "szk-plugins", "plugins", "validator"),
                  "/home/user/szilikaroly/szk-plugins/plugins/validator"]
VALIDATOR = next((os.path.abspath(d) for d in VALIDATOR_DIRS if d and os.path.isdir(
    os.path.join(d, "skills", "validator", "references"))), None)
EXPECTED_TOOLS = ["amstar2", "grade", "jbi", "nos", "probast-ai", "quadas2", "quips", "rob2", "robins-e", "robins-i",
                  "tripod-ai"]
TS = "2026-10-05T10:00:00Z"


def contract(doc, name):
    return I.contract_errors(doc, name, 1)


def base_doc(tool, answers=None, unit="S1", key=None, assessor="SzK", status="draft", origin="human", **kw):
    d = {"schema": "szk.appraisal/v1", "tool": tool, "scope": None,
         "target": {"unit": unit, "study_id": unit if unit not in ("review", "manuscript") else None, "key": key},
         "assessor": assessor, "second_assessor": None, "status": status, "origin": origin, "approved_by": None,
         "approved_at": None, "answers": {k: {"value": v} for k, v in (answers or {}).items()},
         "domain_judgements": [], "applicability": [], "overall": None}
    d.update(kw)
    return d


def clean_answers(tool, scope=None):
    """Minden hatókörbe eső tétel 'rendben' válasszal (normál: yes, fordított: no, irányító: yes)."""
    inst = I.load(tool)
    out = {}
    for it in inst.slots(scope):
        allowed = inst.allowed(it)
        if it.get("polarity") == "reverse":
            v = "no"
        elif "yes" in allowed:
            v = "yes"
        else:
            v = allowed[0]
        out[it["key"]] = v
    return out


def rob2(assessor="SzK", unit="S1", status="draft", judgement=None, **changes):
    ans = clean_answers("rob2")
    # tiszta vizsgálat a RoB 2 (2019) folyamatábrája szerint: nincs a vizsgálati helyzetből fakadó eltérés (2.3 N),
    # vakított kimenet-értékelők (4.3 N) — a 2.3–2.5 és 4.3–4.4 „Igen” útvonala már „némi aggály” (v1 javítás A)
    ans.update({"2.3": "no", "4.3": "no"})
    for k, v in changes.items():
        ans[k.replace("_", ".")] = v
    d = base_doc("rob2", ans, unit=unit, key="o1", assessor=assessor, status=status)
    d["target"]["outcome"] = "o1"
    d["scope"] = "assignment"
    if judgement:
        d["domain_judgements"] = [{"domain": str(i), "pass": None, "judgement": judgement[0] if len(judgement) == 1
                                   else judgement[i - 1]} for i in range(1, 6)]
        d["overall"] = {"judgement": judgement[-1] if len(judgement) > 1 else judgement[0], "rationale": "indok"}
    return d


def ai_answer(value, quote=True):
    a = {"value": value, "rationale": {"asks": "Mit kérdez a tétel.", "because": "Miért ez a javaslat.",
                                       "change": "Mi változtatná meg.", "uncertain": None}}
    if quote:
        a["evidence"] = {"text": "registry-based cohort", "doc": "pmid:1", "page": 4, "locator": None}
    else:
        a["rationale"]["uncertain"] = "A cikk nem közli."
    return a


# ================================================================== definíciók
class TestDefinitions(unittest.TestCase):
    def test_all_instruments_present_and_valid(self):
        self.assertEqual(I.available(), EXPECTED_TOOLS)
        for k in I.available():
            with self.subTest(tool=k):
                doc = json.loads(I.raw(k).decode("utf-8"))
                self.assertEqual(contract(doc, "instrument"), [])
                self.assertEqual(I.definition_problems(doc), [])
                self.assertEqual(doc["key"], k)
                self.assertEqual(I.load(k).alias_conflicts, [])

    def test_canonical_bytes_and_stable_sha(self):
        for k in I.available():
            data = I.raw(k)
            with self.subTest(tool=k):
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                self.assertNotIn(b"\r", data)
                self.assertEqual(data.decode("utf-8"),
                                 json.dumps(json.loads(data.decode("utf-8")), indent=2, ensure_ascii=False) + "\n")
                self.assertEqual(I.sha256(k), hashlib.sha256(data).hexdigest())
                self.assertEqual(I.definition(k)["sha256"], I.sha256(k))

    def test_published_counts(self):
        c = {k: I.load(k) for k in I.available()}
        self.assertEqual(len(c["rob2"].items), 22)
        p = c["probast-ai"]
        self.assertEqual(len(p.items), 34)
        self.assertEqual(len(p.slots("development")), 16)
        self.assertEqual(len(p.slots("evaluation")), 18)
        self.assertEqual(len(p.slots("both")), 34)
        self.assertEqual(p.doc["counts"]["per_pass"], {"development": 16, "evaluation": 18})
        self.assertEqual(len({it["key"] for it in p.items}), 34, "a menettel minősített kulcsok egyediek")
        t = c["tripod-ai"]
        self.assertEqual(len(t.items), 52)
        self.assertEqual(sorted(it["id"] for it in t.items if it["applies_to"] == "D"),
                         sorted(["9a", "12a", "12b", "12c", "15", "22", "27a", "27b"]))
        self.assertEqual(sorted(it["id"] for it in t.items if it["applies_to"] == "E"),
                         sorted(["12f", "12g", "20c", "24"]))
        self.assertEqual((len(t.slots("development")), len(t.slots("evaluation"))), (48, 44))
        a = c["amstar2"]
        self.assertEqual(len(a.items), 16)
        self.assertEqual(sorted((it["id"] for it in a.items if it["critical"]), key=int),
                         ["2", "4", "7", "9", "11", "13", "15"])
        g = c["grade"]
        roles = [d.get("role") for d in g.domains]
        self.assertEqual((roles.count("start"), roles.count("downgrade"), roles.count("upgrade")), (1, 5, 3))
        self.assertEqual([d["grade_key"] for d in g.domains if d.get("role") != "start"],
                         ["risk_of_bias", "inconsistency", "indirectness", "imprecision", "publication_bias",
                          "large_effect", "dose_response", "opposing_confounding"])
        n = c["nos"]
        for variant in ("cohort", "case-control"):
            its = n.slots(variant)
            self.assertEqual(len(its), 8)
            self.assertEqual(sum(it["stars"] for it in its), 9)
        q = c["quadas2"]
        self.assertEqual(len(q.items), 11)
        self.assertEqual([d["id"] for d in q.domains if d["applicability"]], ["1", "2", "3"])
        j = c["jbi"]
        self.assertEqual({s: len(j.slots(s)) for s in j.scope_ids},
                         {"cohort": 11, "case-control": 10, "cross-sectional": 8, "case-series": 10,
                          "case-report": 8, "prevalence": 9, "qualitative": 10})
        # ROBINS-I 2016 (Sterne 2016, Table A): 34 tétel; a besorolás hatására 30, a betartásra 32 (v1 javítás A)
        self.assertEqual((len(c["robins-i"].items), len(c["robins-i"].slots("assignment")),
                          len(c["robins-i"].slots("adherence"))), (34, 30, 32))

    def test_units_rollup_labels_and_families(self):
        units = {k: I.load(k).unit for k in I.available()}
        self.assertEqual(units["rob2"], "result")
        self.assertEqual(units["quadas2"], "index_test")
        self.assertEqual(units["probast-ai"], "model")
        self.assertEqual(units["tripod-ai"], "study")
        self.assertEqual(units["amstar2"], "review")
        self.assertEqual(units["grade"], "outcome")
        algs = {k: I.load(k).algorithm for k in I.available()}
        self.assertEqual({k for k, v in algs.items() if v == "published"}, {"amstar2", "grade"})
        self.assertEqual({k for k, v in algs.items() if v == "count"}, {"nos"})
        self.assertEqual({k for k, v in algs.items() if v == "conservative"},
                         {"rob2", "robins-i", "robins-e", "quadas2", "quips"})
        self.assertEqual({k for k, v in algs.items() if v == "none"}, {"probast-ai", "tripod-ai", "jbi"})
        # a szabály alapja eszközönként: a RoB 2 / ROBINS-I / ROBINS-E gépi szabályai a publikált folyamatábrát /
        # kritériumokat követik (konzervatívan), a többi a validátor-kompatibilis polaritás-szabály (v1 javítás A)
        basis = {"rob2": "rob2-2019-flowchart-conservative", "robins-i": "robins-i-2016-criteria-conservative",
                 "robins-e": "robins-e-2023-criteria-conservative", "quadas2": "validator-compatible",
                 "quips": "validator-compatible"}
        for k, v in algs.items():
            r = I.load(k).rollup
            with self.subTest(tool=k):
                if v == "conservative":
                    self.assertIs(r["official"], False)
                    self.assertEqual(r["basis"], basis[k])
                    self.assertIn("NEM a hivatalos", r["label"]["hu"])
                    self.assertIn("NOT the official", r["label"]["en"])
                if v == "published":
                    self.assertIs(r["official"], True)

    def test_attribution_licence_and_hungarian_texts(self):
        for k in I.available():
            d = I.load(k).doc
            with self.subTest(tool=k):
                self.assertTrue(d["source"]["attribution"].startswith("forrás: szk-plugins validator "))
                self.assertEqual(d["source"]["licence"], "MIT")
                self.assertRegex(d["reference_sha256"], r"^[0-9a-f]{64}$")
                lic = d["licence"]
                self.assertTrue(lic["instrument_licence"])
                self.assertIn(lic["verbatim_allowed"], (True, False, None))
                self.assertEqual(lic["text_kind"], "paraphrase")
                self.assertTrue(lic["note"]["hu"] and lic["note"]["en"])
                self.assertTrue(d["citation"])
                self.assertTrue(d["intro"]["hu"])
                for it in d["items"]:
                    self.assertTrue(it["text"]["hu"].strip(), it["key"])
                    self.assertTrue(it["text"]["en"].strip(), it["key"])
                for dm in d["domains"]:
                    self.assertTrue(dm["title"]["hu"].strip())
                for a in d["answers"]:
                    self.assertTrue(a["text"]["hu"].strip())
        self.assertEqual(I.load("tripod-ai").doc["licence"]["instrument_licence"].split()[0:3], ["CC", "BY", "4.0"])

    def test_polarity_and_validator_differences(self):
        rob = I.load("rob2")
        pol = {it["id"]: it["polarity"] for it in rob.items}
        self.assertEqual(sorted(k for k, v in pol.items() if v == "reverse"),
                         ["1.3", "2.7", "3.4", "4.1", "4.2", "4.5", "5.2", "5.3"])
        self.assertEqual(sorted(k for k, v in pol.items() if v == "router"),
                         ["2.1", "2.2", "2.3", "2.4", "3.3", "4.3", "4.4"])
        q = {it["id"]: it["polarity"] for it in I.load("quadas2").items}
        self.assertEqual((q["1.2"], q["1.3"]), ("normal", "normal"))
        ri = {it["id"]: it for it in I.load("robins-i").items}
        self.assertEqual((ri["4.4"]["polarity"], ri["5.2"]["polarity"], ri["6.3"]["polarity"]),
                         ("normal", "reverse", "normal"))
        self.assertEqual(ri["1.1"].get("severity"), "some")
        re_ = {it["id"]: it["polarity"] for it in I.load("robins-e").items}
        self.assertEqual((re_["2.3"], re_["5.2"], re_["6.2"]), ("normal", "reverse", "normal"))
        for k in ("quadas2", "robins-i", "robins-e"):
            self.assertTrue(I.load(k).doc["validator_differences"], k)

    def test_conditional_items_allow_not_applicable(self):
        rob = I.load("rob2")
        self.assertIn("not_applicable", rob.allowed(rob.item("2.7")))
        self.assertNotIn("not_applicable", rob.allowed(rob.item("1.1")))
        self.assertEqual(rob.item("2.3")["condition"]["en"], "If Y/PY/NI to 2.1 or 2.2")
        am = I.load("amstar2")
        self.assertEqual(sorted((it["id"] for it in am.items if "partial_yes" in am.allowed(it)), key=int),
                         ["2", "4", "7", "8", "9"])
        self.assertEqual(sorted((it["id"] for it in am.items if "not_applicable" in am.allowed(it)), key=int),
                         ["11", "12", "15"])
        nos = I.load("nos")
        self.assertEqual(sorted(it["id"] for it in nos.items if "partial_yes" in nos.allowed(it)), ["C1", "C2"])

    def test_index_summary(self):
        rows = I.index()
        self.assertEqual([r["key"] for r in rows], EXPECTED_TOOLS)
        p = next(r for r in rows if r["key"] == "probast-ai")
        self.assertEqual(p["passes"], ["development", "evaluation"])
        self.assertEqual(p["default_scope"], "both")
        self.assertEqual(p["rollup"]["algorithm"], "none")
        self.assertEqual(A.instruments_list(), rows)
        g = A.instrument_get("jbi")
        self.assertEqual(g["items"][0]["scope"], g["items"][0]["scopes"])
        self.assertEqual(contract(g, "instrument"), [])
        with self.assertRaises(KeyError):
            A.instrument_get("nincs-ilyen")


@unittest.skipUnless(VALIDATOR, "a szk-plugins validator nincs meg (SZK_VALIDATOR_DIR)")
class TestValidatorDrift(unittest.TestCase):
    """A definíciók tételazonosítói a validator referenciafájljaiból (csak olvasás) — sodródás-őr."""
    ITEM_RE = re.compile(r"^\*\*(?P<id>(?=[\w.\-]*\d)[A-Za-z0-9][\w.\-]*)\s*(?:\((?P<scope>[^)]*)\))?\s*(?:\*\*)?\s*"
                         r"[—–-]\s*(?P<text>.+?)\s*$")

    def ref(self, name):
        with open(os.path.join(VALIDATOR, "skills", "validator", "references", name), encoding="utf-8") as fh:
            return fh.read()

    def test_generic_ids_match(self):
        # ahol a definíció a publikált eszközt követi a validator helyett (ROBINS-I 2016, QUIPS a–g; v1 javítás A),
        # a validator akkori azonosítói a 'validator_ids' mezőben vannak: a sodródás-őr azzal vet össze
        for tool in ("rob2", "robins-i", "robins-e", "quadas2", "nos", "quips", "jbi", "amstar2", "grade"):
            ids = [m.group("id") for m in map(self.ITEM_RE.match, self.ref(tool + ".md").splitlines()) if m]
            doc = I.load(tool).doc
            with self.subTest(tool=tool):
                self.assertEqual(doc.get("validator_ids") or [it["id"] for it in doc["items"]], ids)

    def test_probast_and_tripod_ids_match(self):
        text = self.ref("probast-ai.md")
        shared, dev, ev, bucket = [], [], [], None
        bucket = shared
        for line in text.splitlines():
            h = re.match(r"^###\s+Domain 4\s+—\s+(Development|Evaluation)", line, re.I)
            if h:
                bucket = dev if h.group(1).lower() == "development" else ev
                continue
            m = re.match(r"^\*\*([1-4]\.\d{1,2})\s*[—–-]", line)
            if m:
                bucket.append(m.group(1))
        want = ["development/" + i for i in shared + dev] + ["evaluation/" + i for i in shared + ev]
        self.assertEqual([it["key"] for it in I.load("probast-ai").items], want)
        tri = [(m.group(1), m.group(2)) for m in (re.match(r"^\*\*(\d{1,2}[a-z]?)\s*\((D;E|D|E)\)\*\*", ln)
                                                  for ln in self.ref("tripod-ai.md").splitlines()) if m]
        self.assertEqual([(it["id"], it["applies_to"]) for it in I.load("tripod-ai").items], tri)

    def test_reference_hash_recorded(self):
        for tool in EXPECTED_TOOLS:
            with open(os.path.join(VALIDATOR, "skills", "validator", "references", tool + ".md"), "rb") as fh:
                sha = hashlib.sha256(fh.read()).hexdigest()
            if os.environ.get("SZK_VALIDATOR_STRICT") == "1":
                self.assertEqual(I.load(tool).doc["reference_sha256"], sha, tool)
            else:
                self.assertRegex(I.load(tool).doc["reference_sha256"], r"^[0-9a-f]{64}$")


# ================================================================== álnevek (H4)
class TestAliases(unittest.TestCase):
    def test_py_is_partial_yes_only_in_amstar2(self):
        am, rob, pr, nos = I.load("amstar2"), I.load("rob2"), I.load("probast-ai"), I.load("nos")
        self.assertEqual(am.canonical("PY", am.item("2")), "partial_yes")
        self.assertEqual(am.canonical("py"), "partial_yes")
        self.assertIsNone(am.canonical("Probably yes"), "az AMSTAR 2-ben nincs 'valószínűleg igen'")
        self.assertEqual(rob.canonical("PY"), "probably_yes")
        self.assertEqual(rob.canonical("Valószínűleg igen"), "probably_yes")
        self.assertIsNone(rob.canonical("Partial yes"))
        self.assertEqual(pr.canonical("py"), "probably_yes")
        self.assertIsNone(nos.canonical("PY"), "a NOS-ban a 'PY' kétértelmű, nem fogadott")
        self.assertEqual(nos.canonical("részben igen", nos.item("C1")), "partial_yes")
        self.assertIsNone(nos.canonical("részben igen", nos.item("S1")), "1 csillagos tételen nincs részleges csillag")

    def test_other_aliases(self):
        tr = I.load("tripod-ai")
        self.assertEqual(tr.canonical("not reported"), "missing")
        self.assertEqual(tr.canonical("Részben közölt"), "partial")
        self.assertEqual(tr.canonical("N/A"), "not_applicable")
        am = I.load("amstar2")
        self.assertEqual(am.canonical("No meta-analysis conducted", am.item("11")), "not_applicable")
        self.assertIsNone(am.canonical("No meta-analysis conducted", am.item("1")))
        self.assertIsNone(am.canonical("PY", am.item("1")), "az 1. tételnél nincs 'részben igen'")
        g = I.load("grade")
        self.assertEqual(g.canonical("Not serious"), "not_serious")
        self.assertEqual(g.canonical("erősen gyanított"), "strongly_suspected")
        self.assertEqual(I.fold("Valószínűleg_NEM"), "valoszinuleg nem")

    def test_normalize_and_validate_store_only_canonical(self):
        d = base_doc("amstar2", {"2": "PY", "4": "Partial yes", "8": "py"}, unit="review")
        n, changes = A.normalize(d)
        self.assertEqual(sorted(c[2] for c in changes), ["partial_yes"] * 3)
        self.assertEqual(A.validate(n), [])
        errs = A.validate(d)
        self.assertTrue(any("írd így: partial_yes" in e for e in errs), errs)
        rb = base_doc("rob2", {"1.1": "PY"})
        self.assertEqual(A.normalize(rb)[0]["answers"]["1.1"]["value"], "probably_yes")
        r = A.check(d)
        self.assertEqual(r["answered"], 3)
        self.assertTrue(any("álnév" in w["hu"] for w in r["warnings"]))


# ================================================================== teljesség (H1, H2)
class TestCompleteness(unittest.TestCase):
    def test_probast_development_only_is_16_of_34(self):
        inst = I.load("probast-ai")
        d = base_doc("probast-ai", {it["key"]: "yes" for it in inst.items if it["pass"] == "development"},
                     unit="LEE2023", key="XGB")
        d["scope"] = "both"
        r = A.check(d)
        self.assertEqual((r["answered"], r["expected"]), (16, 34))
        self.assertEqual(r["completeness_text"], "16/34")
        self.assertEqual(r["per_pass"]["development"], {"expected": 16, "answered": 16, "complete": True,
                                                        "text": "16/16"})
        self.assertEqual(r["per_pass"]["evaluation"]["answered"], 0)
        self.assertFalse(r["complete"])
        self.assertEqual(len(r["missing"]), 18)
        self.assertTrue(all(m["pass"] == "evaluation" for m in r["missing"]))
        d["scope"] = "development"
        r = A.check(d)
        self.assertEqual((r["answered"], r["expected"], r["complete"]), (16, 16, True))
        self.assertEqual(contract(r, "appraisal-result"), [])

    def test_probast_unqualified_keys_do_not_count_h2(self):
        d = base_doc("probast-ai", {"1.1": "yes", "1.2": "no"}, unit="LEE2023", key="XGB")
        d["scope"] = "both"
        r = A.check(d)
        self.assertEqual(r["answered"], 0)
        self.assertEqual({x["key"] for x in r["invalid"]}, {"1.1", "1.2"})
        errs = A.validate(d)
        self.assertTrue(any("development/1.1" in e for e in errs), errs)

    def test_empty_tripod_is_0_of_52_h1(self):
        d = base_doc("tripod-ai", {}, unit="manuscript")
        d["scope"] = "both"
        r = A.check(d)
        self.assertEqual((r["answered"], r["expected"], r["completeness_text"]), (0, 52, "0/52"))
        self.assertEqual(r["tripod"]["counts"]["unanswered"], 52)
        self.assertIsNone(r["tripod"]["reported_pct"])
        self.assertEqual(len(r["tripod"]["unassessed"]), 52)
        self.assertIn("missing", I.load("tripod-ai").item("11")["text"]["en"].lower(),
                      "a 11. tétel szövegében szerepel a státuszszó — cellánként mégsem számít válasznak")
        for scope, n in (("development", 48), ("evaluation", 44)):
            d["scope"] = scope
            self.assertEqual(A.check(d)["completeness_text"], "0/%d" % n)
        self.assertEqual(contract(r, "appraisal-result"), [])

    def test_tripod_statuses_and_de_filter(self):
        d = base_doc("tripod-ai", {"1": "present", "2": "partial", "11": "missing", "3a": "not_applicable",
                                   "22": "present"}, unit="Lee2023")
        d["scope"] = "evaluation"
        t = A.tripod_check(d)
        self.assertEqual(t["counts"]["present"], 1, "a 22 (D) az értékelő hatókörön kívül esik")
        self.assertEqual((t["counts"]["partial"], t["counts"]["missing"], t["counts"]["not_applicable"]), (1, 1, 1))
        self.assertEqual(t["expected"], 44)
        self.assertAlmostEqual(t["reported_pct"], 100.0 / 3)
        self.assertEqual(t["reported_text"], "33.3%")
        self.assertAlmostEqual(t["reported_or_partial_pct"], 200.0 / 3)
        self.assertEqual([x["item"] for x in t["missing_items"]], ["11", "2"])
        self.assertEqual(t["applies_to"], ["E", "D;E"])
        r = A.check(d)
        self.assertTrue(any("hatókörön kívüli" in w["hu"] for w in r["warnings"]))
        self.assertEqual(r["overall"]["algorithm"], "none")
        self.assertIn("nem a módszertan", r["tripod"]["note"]["hu"])

    def test_invalid_value_and_unknown_scope(self):
        d = base_doc("rob2", {"1.1": "maybe"})
        r = A.check(d)
        self.assertEqual(r["invalid"][0]["key"], "1.1")
        self.assertFalse(r["complete"])
        d = base_doc("rob2", {})
        d["scope"] = "adherence"
        r = A.check(d)
        self.assertEqual(r["expected"], 0)
        self.assertFalse(r["complete"])
        self.assertTrue(any("scope" in e for e in A.validate(d)))


# ================================================================== implikált ítéletek
class TestImplied(unittest.TestCase):
    def dom(self, res, did, ps=None):
        return next(d for d in res["domains"] if d["domain"] == did and d.get("pass") == ps)

    def test_rob2_clean_trial_is_low_and_labelled_unofficial(self):
        r = A.check(rob2())
        self.assertTrue(r["complete"])
        self.assertEqual([d["implied"] for d in r["domains"]], ["low"] * 5)
        self.assertEqual(r["overall"]["implied"], "low")
        self.assertEqual(r["overall"]["algorithm"], "conservative")
        self.assertIs(r["overall"]["official"], False)
        self.assertEqual(r["overall"]["basis"], "rob2-2019-flowchart-conservative")
        self.assertIn("NEM a hivatalos RoB 2", r["overall"]["lines"][-1]["hu"])
        self.assertEqual(self.dom(r, "2")["routers"], ["2.1", "2.2", "2.3", "2.4"])

    def test_rob2_open_label_router_does_not_force(self):
        r = A.check(rob2(**{"2_1": "yes", "2_2": "yes"}))
        self.assertEqual(self.dom(r, "2")["implied"], "low")

    def test_rob2_design_example_2_6(self):
        r = A.check(rob2(**{"2_6": "no", "2_7": "probably_yes"}))
        d2 = self.dom(r, "2")
        self.assertEqual((d2["implied"], d2["forced_by"]), ("high", ["2.6", "2.7"]))
        self.assertEqual(r["overall"]["implied"], "high")

    def test_rob2_reverse_ni_and_missing(self):
        r = A.check(rob2(**{"1_3": "yes"}))
        # rejtett, véletlen szekvencia + problémára utaló kiinduló különbség: a hivatalos ág „némi aggály” (v1 javítás A)
        self.assertEqual((self.dom(r, "1")["implied"], self.dom(r, "1")["forced_by"]), ("some_concerns", ["1.3"]))
        r = A.check(rob2(**{"5_1": "no_information"}))
        self.assertEqual(self.dom(r, "5")["implied"], "some_concerns")
        self.assertEqual(self.dom(r, "5")["unknown_at"], ["5.1"])
        self.assertEqual(r["overall"]["implied"], "some_concerns")
        d = rob2()
        del d["answers"]["3.1"]
        r = A.check(d)
        self.assertIsNone(self.dom(r, "3")["implied"])
        self.assertEqual(self.dom(r, "3")["missing"], ["3.1"])
        self.assertIsNone(r["overall"]["implied"], "hiányos domén mellett nincs implikált összítélet")
        d["answers"]["4.1"] = {"value": "yes"}
        self.assertEqual(A.check(d)["overall"]["implied"], "high", "a kikényszerített 'magas' hiány mellett is áll")

    def test_robins_i_confounding_and_polarity_fixes(self):
        ans = clean_answers("robins-i", "assignment")
        ans["1.1"] = "yes"
        d = base_doc("robins-i", ans)
        d["scope"] = "assignment"
        r = A.check(d)
        self.assertEqual(self.dom(r, "1")["implied"], "moderate", "zavarás lehetősége → legalább mérsékelt")
        self.assertEqual(r["overall"]["implied"], "moderate")
        # 5.2 „Igen” (kizárás hiányzó adat miatt) probléma — „súlyos”, ha az arány/ok nem hasonló és az eredmény nem
        # robusztus (5.4, 5.5 N); a 2016-os Table C szerint hasonló arány/ok (5.4 I) mellett alacsony (v1 javítás A)
        ans["5.2"] = "yes"
        ans["5.4"] = ans["5.5"] = "no"
        d = base_doc("robins-i", ans)
        d["scope"] = "assignment"
        self.assertEqual(self.dom(A.check(d), "5")["implied"], "serious")
        ans2 = clean_answers("robins-i", "adherence")
        ans2["4.3"] = "yes"
        d = base_doc("robins-i", ans2)
        d["scope"] = "adherence"
        self.assertEqual(self.dom(A.check(d), "4")["implied"], "low", "kiegyensúlyozott ko-intervenció: rendben")

    def test_quadas2_case_control_avoided_is_good(self):
        ans = clean_answers("quadas2")
        self.assertEqual(ans["1.2"], "yes")
        r = A.check(base_doc("quadas2", ans))
        self.assertEqual(self.dom(r, "1")["implied"], "low")
        ans["1.2"] = "no"
        r = A.check(base_doc("quadas2", ans))
        self.assertEqual((self.dom(r, "1")["implied"], r["overall"]["implied"]), ("high", "high"))
        ans["1.2"] = "unclear"
        self.assertEqual(self.dom(A.check(base_doc("quadas2", ans)), "1")["implied"], "unclear")

    def test_quips_partly_is_moderate(self):
        ans = clean_answers("quips")
        # a QUIPS hivatalos prompting itemjei a–g betűjellel (Hayden 2013; v1 javítás A): 2d = a kiesettek leírása
        ans["2d"] = "partly"
        r = A.check(base_doc("quips", ans))
        self.assertEqual((self.dom(r, "2")["implied"], self.dom(r, "2")["partial_at"]), ("moderate", ["2d"]))

    def test_probast_and_jbi_have_no_implied_judgement(self):
        inst = I.load("probast-ai")
        ans = {it["key"]: "yes" for it in inst.items}
        ans["evaluation/4.7"] = "no"
        d = base_doc("probast-ai", ans, unit="LEE2023", key="XGB")
        d["domain_judgements"] = [{"domain": "4", "pass": "evaluation", "judgement": "low"}]
        r = A.check(d)
        self.assertTrue(r["complete"])
        self.assertTrue(all(x["implied"] is None for x in r["domains"]))
        self.assertEqual(self.dom(r, "4", "evaluation")["flags"], ["4.7"])
        self.assertIsNone(r["overall"]["implied"])
        self.assertEqual(r["overall"]["algorithm"], "none")
        self.assertEqual(r["overrides"], [], "holisztikus eszköznél nincs X017")
        j = base_doc("jbi", {"5.1": "no"})
        j["scope"] = "case-report"
        self.assertEqual(self.dom(A.check(j), "5")["flags"], ["5.1"])

    def test_nos_star_count_without_threshold(self):
        d = base_doc("nos", clean_answers("nos", "cohort"))
        d["scope"] = "cohort"
        r = A.check(d)
        self.assertEqual((r["nos"]["total"], r["nos"]["max"]), (9, 9))
        self.assertEqual(r["overall"]["algorithm"], "count")
        self.assertIsNone(r["overall"]["implied"])
        d["answers"]["C1"] = {"value": "partial_yes"}
        d["answers"]["O3"] = {"value": "no"}
        r = A.check(d)
        self.assertEqual(r["nos"]["total"], 7)
        self.assertEqual({x["domain"]: x["stars"] for x in r["nos"]["domains"]}, {"S": 4, "C": 1, "O": 2})
        self.assertIn("NINCS hivatalos küszöb", r["nos"]["note"]["hu"])

    def test_amstar2_published_rating_both_conventions(self):
        yes = {str(i): "yes" for i in range(1, 17)}
        r = A.check(base_doc("amstar2", yes, unit="review"))
        self.assertEqual((r["amstar2"]["rating"], r["overall"]["implied"]), ("high", "high"))
        self.assertEqual(r["conventions"], {"amstar2.partial_yes_critical": "meets"})
        ans = dict(yes, **{"2": "partial_yes", "4": "partial_yes"})
        a = A.check(base_doc("amstar2", ans, unit="review"))["amstar2"]
        self.assertEqual((a["rating"], a["alternative"]["rating"], a["differs"]), ("high", "moderate", True))
        self.assertEqual(a["partial_yes_critical"], ["2", "4"])
        self.assertIn("MÉRSÉKELT", a["text"]["hu"])
        w = A.check(base_doc("amstar2", ans, unit="review"),
                    conventions={"amstar2_partial_yes_critical": "weakness"})["amstar2"]
        self.assertEqual((w["convention"], w["rating"]), ("weakness", "moderate"))
        self.assertEqual(A.amstar2_rating({"2": "PY", "4": "PY"})["partial_yes_critical"], ["2", "4"])
        one = A.check(base_doc("amstar2", dict(yes, **{"7": "no"}), unit="review"))["amstar2"]
        self.assertEqual(one["rating"], "low")
        two = A.check(base_doc("amstar2", dict(yes, **{"7": "no", "13": "no"}), unit="review"))["amstar2"]
        self.assertEqual(two["rating"], "critically_low")
        weak = A.check(base_doc("amstar2", dict(yes, **{"5": "no", "6": "no"}), unit="review"))["amstar2"]
        self.assertEqual(weak["rating"], "moderate")
        na = A.check(base_doc("amstar2", dict(yes, **{"11": "not_applicable", "12": "not_applicable",
                                                       "15": "not_applicable"}), unit="review"))["amstar2"]
        self.assertEqual((na["rating"], na["not_applicable"]), ("high", ["11", "12", "15"]))
        partial = dict(yes)
        del partial["16"]
        pr = A.check(base_doc("amstar2", partial, unit="review"))
        self.assertTrue(pr["amstar2"]["provisional"])
        self.assertIn("IDEIGLENES", pr["amstar2"]["text"]["hu"])

    def grade(self, start="high", **kw):
        ans = {"0.1": start, "1.1": "not_serious", "2.1": "not_serious", "3.1": "not_serious",
               "4.1": "not_serious", "5.1": "undetected", "6.1": "no", "7.1": "no", "8.1": "no"}
        res = kw.pop("resolution", None)
        ans.update({k.replace("_", "."): v for k, v in kw.items()})
        d = base_doc("grade", ans, unit="review", key="o1")
        if res is not None:
            d["answers"]["5.1"]["resolution"] = res
        return d

    def test_grade_arithmetic_and_unresolved_publication_bias(self):
        g = A.check(self.grade())["grade"]
        self.assertEqual(g["certainty"], "high")
        self.assertEqual(A.check(self.grade(**{"1_1": "serious"}))["grade"]["certainty"], "moderate")
        # a GRADE-blokk a szk.ma.grade/v1 kanonikus tokenjeit adja ('very low'; v1 javítás A, contracts:m2)
        self.assertEqual(A.check(self.grade(**{"1_1": "very_serious", "4_1": "serious"}))["grade"]["certainty"],
                         "very low")
        r = A.check(self.grade(**{"5_1": "suspected"}))
        self.assertIsNone(r["grade"]["certainty"])
        self.assertEqual(r["grade"]["unresolved"], ["publication_bias"])
        self.assertEqual(r["grade"]["domains"]["publication_bias"]["status"], "unresolved")
        self.assertIsNone(r["overall"]["implied"])
        self.assertTrue(r["overall"]["provisional"])
        self.assertIn("FELOLDATLAN", r["grade"]["lines"][0]["hu"])
        r = A.check(self.grade(**{"5_1": "suspected"}, resolution={"step": -1, "rationale": "kis ipari vizsgálatok"}))
        self.assertEqual((r["grade"]["certainty"], r["grade"]["unresolved"]), ("moderate", []))
        r = A.check(self.grade(**{"5_1": "suspected"}, resolution={"step": 0, "rationale": "teljes regiszter-keresés"}))
        self.assertEqual(r["grade"]["certainty"], "high")
        bad = self.grade(**{"5_1": "suspected"}, resolution={"step": 0, "rationale": ""})
        self.assertIsNone(A.check(bad)["grade"]["certainty"])
        self.assertTrue(any("indoklás kötelező" in e for e in A.validate(bad)))
        self.assertTrue(any("step" in e for e in A.validate(
            self.grade(**{"5_1": "suspected"}, resolution={"step": -2, "rationale": "x"}))))
        self.assertEqual(A.check(self.grade(**{"5_1": "strongly_suspected"}))["grade"]["certainty"], "moderate")

    def test_grade_upgrades(self):
        self.assertEqual(A.check(self.grade("low", **{"6_1": "yes"}))["grade"]["certainty"], "moderate")
        self.assertEqual(A.check(self.grade("low", **{"6_1": "very_large"}))["grade"]["certainty"], "high")
        g = A.check(self.grade("low", **{"6_1": "yes", "2_1": "serious"}))["grade"]
        self.assertEqual((g["certainty"], g["upgrades_applied"]), ("very low", False))
        self.assertTrue(any("NEM alkalmazva" in x["hu"] for x in g["lines"]))
        d = self.grade()
        del d["answers"]["3.1"]
        g = A.check(d)["grade"]
        self.assertEqual((g["certainty"], g["missing"]), (None, ["indirectness"]))


# ================================================================== felülbírálás (X017)
class TestOverride(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "p")
        projekt.init(self.proj, "Teszt")

    def test_override_without_reason_blocks_final_save(self):
        d = rob2(status="complete", judgement=["low"], **{"2_6": "no", "2_7": "probably_yes"})
        r = A.check(d)
        ov = {o["domain"]: o for o in r["overrides"]}
        self.assertEqual(set(ov), {"2", "overall"})
        self.assertTrue(ov["2"]["reason_missing"])
        self.assertEqual(ov["2"]["implied"], "high")
        self.assertTrue(any("X017" in w["hu"] for w in r["warnings"]))
        with self.assertRaises(A.AppraisalError) as cm:
            A.save(self.proj, d, now=TS)
        self.assertTrue(any("X017" in p for p in cm.exception.problems), cm.exception.problems)
        draft = copy.deepcopy(d)
        draft["status"] = "draft"
        self.assertTrue(A.save(self.proj, draft, now=TS)["path"].endswith("S1.rob2.o1.SzK.json"))

    def test_set_judgement_logs_decision(self):
        d = rob2(**{"2_6": "no", "2_7": "probably_yes"})
        with self.assertRaises(A.AppraisalError):
            A.set_judgement(d, "2", "low")
        d2, dec = A.set_judgement(d, "2", "low", reason="az ITT-eltérés 2 főt érint 400-ból", project_dir=self.proj,
                                  actor="SzK")
        self.assertIsInstance(dec, int)
        dj = d2["domain_judgements"][0]
        self.assertEqual((dj["judgement"], dj["implied"], dj["decision_id"]), ("low", "high", dec))
        item = projekt.get_item(self.proj, "decision", dec)
        self.assertIn("felülbírálás", item["decision"])
        ctx = item["context"] if isinstance(item["context"], dict) else json.loads(item["context"])
        self.assertEqual(ctx["code"], "X017")
        ov = [o for o in A.check(d2)["overrides"] if o["domain"] == "2"][0]
        self.assertEqual((ov["reason_missing"], ov["decision_missing"]), (False, False))
        d3, dec3 = A.set_judgement(d2, "2", "high")
        self.assertIsNone(dec3)
        self.assertIsNone(d3["domain_judgements"][0]["override_reason"])
        d4, _ = A.set_judgement(d3, "overall", "low", reason="egyetlen kis torzítás", project_dir=self.proj)
        self.assertEqual(d4["overall"]["implied"], "high")
        with self.assertRaises(A.AppraisalError):
            A.set_judgement(d, "9", "low")
        with self.assertRaises(A.AppraisalError):
            A.set_judgement(d, "2", "serious")


# ================================================================== AI-vázlat (6. döntés)
class TestAIDraft(unittest.TestCase):
    def doc(self, **kw):
        d = base_doc("rob2", {}, unit="S1", key="o1", assessor="ai", origin="ai_draft", **kw)
        d["scope"] = "assignment"
        d["answers"] = {k: ai_answer(v) for k, v in clean_answers("rob2").items()}
        return d

    def test_item_rationale_and_evidence_required(self):
        d = self.doc()
        self.assertEqual(A.validate(d), [])
        d["answers"]["1.1"] = {"value": "yes"}
        errs = A.validate(d)
        self.assertTrue(any("rationale" in e and "1.1" in e for e in errs), errs)
        self.assertTrue(any("idézet" in e for e in errs), errs)
        d["answers"]["1.1"] = ai_answer("no_information", quote=False)
        self.assertEqual(A.validate(d), [], "bizonytalanság-jelöléssel idézet nélkül is elfogadható")

    def test_status_rules_and_approval(self):
        d = self.doc(status="complete")
        self.assertTrue(any("jóváhagyás" in e for e in A.validate(d)))
        c = self.doc(status="consensus")
        c["second_assessor"] = "KP"
        self.assertTrue(any("konszenzus" in e for e in A.validate(c)))
        ok, res = A.approve_ai_draft(self.doc(), "SzK", now=TS)
        self.assertEqual((ok["status"], ok["approved_by"], ok["approved_at"]), ("complete", "SzK", TS))
        self.assertTrue(res["complete"])
        self.assertEqual(A.validate(ok), [])
        with self.assertRaises(A.AppraisalError):
            A.approve_ai_draft(self.doc(), "ai")
        with self.assertRaises(A.AppraisalError):
            A.approve_ai_draft(rob2(), "SzK")
        bad = self.doc()
        bad["answers"]["1.1"] = {"value": "yes"}
        with self.assertRaises(A.AppraisalError) as cm:
            A.approve_ai_draft(bad, "SzK")
        self.assertTrue(cm.exception.problems)

    def test_ai_draft_never_a_rater(self):
        human = rob2("KP")
        ai = self.doc()
        with self.assertRaises(A.AppraisalError) as cm:
            A.appraisal_consensus(ai, human)
        self.assertIn("AI-vázlat", str(cm.exception))
        approved, _ = A.approve_ai_draft(self.doc(), "SzK", now=TS)
        with self.assertRaises(A.AppraisalError):
            A.agreement([(human, approved)])
        with self.assertRaises(A.AppraisalError):
            A.build_consensus(approved, human)
        self.assertFalse(A.is_rater(approved))
        self.assertTrue(A.is_rater(human))

    def test_c_class_project_refuses_ai_draft(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "c")
        projekt.init(p, "C")
        projekt.save_project_meta(p, {"title": "C", "data_class": "C"})
        with self.assertRaises(A.AppraisalError) as cm:
            A.save(p, self.doc(), now=TS)
        self.assertTrue(any("C osztályú" in x for x in cm.exception.problems))
        rel = A.save(os.path.join(tmp, "a"), self.doc(), now=TS)["path"]
        self.assertTrue(rel.endswith("/S1.rob2.o1.ai.json"))


# ================================================================== κ és konszenzus
class TestKappa(unittest.TestCase):
    # kézzel ellenőrzött 3×3 tábla (A sorok, B oszlopok; n = 20): Y: 8 1 1 · N: 1 5 0 · NI: 0 1 3
    PAIRS = ([("yes", "yes")] * 8 + [("yes", "no"), ("yes", "no_information"), ("no", "yes")] + [("no", "no")] * 5
             + [("no_information", "no")] + [("no_information", "no_information")] * 3)

    def test_hand_checked_kappa(self):
        k = A.cohen_kappa(self.PAIRS)
        # po = 16/20 = 0.8; pe = (10·9 + 6·7 + 4·4)/400 = 148/400 = 0.37; κ = 0.43/0.63 = 43/63
        self.assertEqual(Fraction(k["kappa"]).limit_denominator(1000), Fraction(43, 63))
        self.assertAlmostEqual(k["po"], 0.8, places=12)
        self.assertAlmostEqual(k["pe"], 0.37, places=12)
        # Fleiss–Cohen–Everitt (1969) kézzel, törtekkel: p_ii, sorösszeg r, oszlopösszeg c
        n = 20
        r = {"yes": Fraction(10, n), "no": Fraction(6, n), "no_information": Fraction(4, n)}
        c = {"yes": Fraction(9, n), "no": Fraction(7, n), "no_information": Fraction(4, n)}
        cell = {}
        for a, b in self.PAIRS:
            cell[(a, b)] = cell.get((a, b), 0) + 1
        kap = Fraction(43, 63)
        pe = Fraction(148, 400)
        a_term = sum(Fraction(cell.get((i, i), 0), n) * (1 - (r[i] + c[i]) * (1 - kap)) ** 2 for i in r)
        b_term = (1 - kap) ** 2 * sum(Fraction(cell.get((i, j), 0), n) * (c[i] + r[j]) ** 2
                                      for i in r for j in r if i != j)
        c_term = (kap - pe * (1 - kap)) ** 2
        se = math.sqrt((a_term + b_term - c_term) / (n * (1 - pe) ** 2))
        self.assertAlmostEqual(k["se"], se, places=12)
        self.assertAlmostEqual(k["se"], 0.1408390428, places=9)
        self.assertAlmostEqual(k["ci"][0], 43 / 63 - 1.959963984540054 * se, places=12)
        self.assertAlmostEqual(k["ci"][1], 43 / 63 + 1.959963984540054 * se, places=12)

    def test_degenerate_cases(self):
        self.assertIsNone(A.cohen_kappa([])["kappa"])
        self.assertIsNone(A.cohen_kappa([("yes", "yes")] * 5)["kappa"], "egyetlen kategória: κ nem értelmezett")
        k = A.cohen_kappa([("yes", "yes")] * 3 + [("no", "no")] * 2)
        self.assertEqual(k["kappa"], 1.0)
        self.assertEqual(k["ci"][1], 1.0)

    def test_pair_agreement_and_disagreements(self):
        a = rob2("SzK", judgement=["low"])
        b = rob2("KP", judgement=["low"], **{"1_1": "no_information", "5_1": "probably_no"})
        ag = A.appraisal_consensus(a, b)
        self.assertEqual(contract(ag, "ma.appraisal-agreement"), [])
        self.assertEqual((ag["a"], ag["b"], ag["items_compared"], ag["disagree"]), ("SzK", "KP", 22, 2))
        self.assertEqual([x["key"] for x in ag["disagreements"]], ["1.1", "5.1"])
        self.assertTrue(ag["overall"]["agree"])
        self.assertRegex(ag["kappa_text"]["hu"], r"^κ = 0\.\d\d \[")
        with self.assertRaises(A.AppraisalError):
            A.appraisal_consensus(a, rob2("SzK"))
        other = rob2("KP", unit="S2")
        with self.assertRaises(A.AppraisalError):
            A.appraisal_consensus(a, other)

    def test_pooled_agreement(self):
        pairs = [(rob2("SzK", unit="S%d" % i), rob2("KP", unit="S%d" % i, **({"1_1": "no"} if i == 1 else {})))
                 for i in range(1, 4)]
        ag = A.agreement(pairs)
        self.assertEqual((ag["pairs"], ag["items_compared"], ag["disagree"]), (3, 66, 1))
        self.assertIsNone(ag["overall"])

    def test_build_consensus(self):
        a = rob2("SzK", judgement=["low"])
        b = rob2("KP", judgement=["some_concerns", "low", "low", "low", "low", "low"], **{"1_1": "no_information"})
        doc, unresolved = A.build_consensus(a, b, now=TS)
        self.assertEqual(doc["status"], "draft")
        self.assertIn("1.1", unresolved)
        self.assertIn("domain_judgements:1", unresolved)
        doc, unresolved = A.build_consensus(
            a, b, resolutions={"1.1": {"value": "Y", "reason": "a módszertani függelék szerint számítógépes"}},
            judgements={"domain_judgements": [{"domain": str(i), "pass": None, "judgement": "low"}
                                              for i in range(1, 6)]}, now=TS, paths=["x/a.json", "x/b.json"])
        self.assertEqual((doc["status"], unresolved), ("consensus", []))
        self.assertEqual(doc["answers"]["1.1"]["value"], "yes")
        self.assertEqual(doc["resolutions"][0]["a"], "yes")
        self.assertEqual((doc["assessor"], doc["second_assessor"]), ("SzK", "KP"))
        self.assertEqual(A.validate(doc), [])
        self.assertEqual(A.rater_of(doc), "consensus")
        self.assertEqual(contract(doc, "appraisal"), [])


# ================================================================== mentés és betöltés
class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "p")

    def test_save_load_roundtrip(self):
        d = rob2(status="complete", judgement=["low"])
        d["answers"]["1.1"] = {"value": "PY"}
        out = A.save(self.proj, d, now=TS)
        self.assertEqual(out["path"], "04_torzitas_kockazat/appraisals/S1.rob2.o1.SzK.json")
        path = os.path.join(self.proj, *out["path"].split("/"))
        with open(path, "rb") as fh:
            data = fh.read()
        self.assertEqual(hashlib.sha256(data).hexdigest(), out["sha256"])
        doc = A.load(path)
        self.assertEqual(doc, out["doc"])
        self.assertEqual(doc["answers"]["1.1"]["value"], "probably_yes")
        self.assertEqual(doc["instrument_sha256"], I.sha256("rob2"))
        self.assertEqual((doc["created"], doc["updated"]), (TS, TS))
        self.assertEqual(doc["domain_judgements"][0]["implied"], "low")
        self.assertEqual(contract(doc, "appraisal"), [])
        self.assertEqual([x for x in os.listdir(os.path.dirname(path)) if x.endswith(".tmp")], [])
        lst = A.list_appraisals(self.proj)
        self.assertEqual([(e["unit"], e["tool"], e["target"], e["rater"]) for e in lst], [("S1", "rob2", "o1", "SzK")])
        later = A.save(self.proj, doc, now="2026-10-06T08:00:00Z", expect_sha256=out["sha256"])
        self.assertEqual((later["doc"]["created"], later["doc"]["updated"]), (TS, "2026-10-06T08:00:00Z"))
        with self.assertRaises(A.AppraisalError):
            A.save(self.proj, doc, expect_sha256=out["sha256"])
        with self.assertRaises(A.AppraisalError):
            A.save(self.proj, rob2(unit="S9"), expect_sha256="0" * 64)

    def test_invalid_doc_is_not_written(self):
        d = rob2()
        d["answers"]["9.9"] = {"value": "yes"}
        with self.assertRaises(A.AppraisalError) as cm:
            A.save(self.proj, d)
        self.assertTrue(any("9.9" in p for p in cm.exception.problems))
        self.assertFalse(os.path.exists(os.path.join(self.proj, "04_torzitas_kockazat")))
        inc = rob2(status="complete")
        del inc["answers"]["3.1"]
        with self.assertRaises(A.AppraisalError) as cm:
            A.save(self.proj, inc)
        self.assertTrue(any("minden hatókörbe eső tétel" in p for p in cm.exception.problems))

    def test_file_names_match_workbench(self):
        self.assertEqual(A.slug("Smith 2010 (b)"), "Smith_2010_b")
        self.assertEqual(A.relpath("Smith 2010", "rob2", "o1", "SzK"),
                         "04_torzitas_kockazat/appraisals/Smith_2010.rob2.o1.SzK.json")
        self.assertEqual(A.relpath("review", "amstar2", None, "consensus"),
                         "04_torzitas_kockazat/appraisals/review.amstar2.consensus.json")
        self.assertEqual(A.parse_name("Smith_2010.rob2.o1.SzK.json"), ("Smith_2010", "rob2", "o1", "SzK"))
        self.assertEqual(A.parse_name("review.amstar2.SzK.json"), ("review", "amstar2", None, "SzK"))
        self.assertIsNone(A.parse_name("x.json"))
        with self.assertRaises(A.AppraisalError):
            A.relpath("S1", "rob2", "o.1", "SzK")
        try:
            from ma_gui.routes import appraisal_common as C
        except Exception:                                           # noqa: BLE001 — a motor tesztje nem függ tőle
            return
        for args in (("Smith 2010", "rob2", "o1", "SzK"), ("review", "amstar2", None, "ai"),
                     ("NCT0456", "probast-ai", "XGB-PE", "consensus")):
            self.assertEqual(A.relpath(*args), C.relpath(*args))
        self.assertEqual(A.parse_name("a.rob2.o1.SzK.json"), C.parse_name("a.rob2.o1.SzK.json"))


# ================================================================== forgalmi lámpa és rob-szinkron
class TestRobSummaryAndSync(unittest.TestCase):
    PLOT = {"schema": "szk.ma.plot/v2", "meta": {"run_id": "20261004T211200Z-a1f3c2"},
            "studies": [{"row_uid": "rabc001", "study_id": "S1", "label": "Aronson 1948", "weight_pct": 50.0},
                        {"row_uid": "rabc002", "study_id": "S2", "label": "Ferguson 1949", "weight_pct": 30.0},
                        {"row_uid": "rabc003", "study_id": None, "label": "Kovács 2019", "weight_pct": 20.0}]}
    STUDIES = {"schema": "szk.ma.studies/v1", "studies": [{"study_id": "S3", "label": "Kovács 2019"}]}

    def docs(self):
        s1a = rob2("SzK", unit="S1", status="complete", judgement=["low"])
        s1b = rob2("KP", unit="S1", status="complete", judgement=["low"], **{"1_1": "no_information"})
        s1b["domain_judgements"][0]["judgement"] = "some_concerns"
        s1c, _ = A.build_consensus(s1a, s1b, resolutions={"1.1": {"value": "yes", "reason": "egyeztetve"}},
                                   judgements={"domain_judgements": [{"domain": str(i), "pass": None,
                                                                      "judgement": "low"} for i in range(1, 6)]})
        s2 = rob2("SzK", unit="S2", status="complete", judgement=["high"], **{"2_6": "no"})
        s3 = rob2("SzK", unit="S3", status="draft", **{"5_1": "no_information"})
        return [s1a, s1b, s1c, s2, s3]

    def test_summary_weights_and_sources(self):
        s = A.rob_summary(self.docs(), "rob2", outcome="o1", plot=self.PLOT, studies=self.STUDIES)
        self.assertEqual(contract(s, "rob-summary"), [])
        by = {x["study_id"]: x for x in s["studies"]}
        self.assertEqual((by["S1"]["source"], by["S2"]["source"], by["S3"]["source"]),
                         ("consensus", "single", "draft"))
        self.assertEqual((by["S1"]["weight_pct"], by["S2"]["weight_pct"], by["S3"]["weight_pct"]), (50.0, 30.0, 20.0))
        self.assertEqual(by["S3"]["label"], "Kovács 2019")
        self.assertEqual(by["S2"]["level"], "high")
        self.assertEqual(by["S2"]["rob_value"], "high")
        self.assertEqual(by["S3"]["domains"][4], {"domain": "5", "pass": None, "judgement": None,
                                                  "implied": "some_concerns", "level": "some", "from": "implied"})
        self.assertEqual(len(s["domains"]), 5)
        w = {x["level"]: x for x in s["weighted"]}
        self.assertEqual((w["low"]["pct"], w["high"]["pct"], w["some"]["n"]), (50.0, 30.0, 0))
        self.assertEqual(s["high_weight_pct"], 30.0)
        self.assertEqual(s["weights_source"]["run_id"], "20261004T211200Z-a1f3c2")
        nw = A.rob_summary(self.docs(), "rob2", outcome="o1")
        self.assertIsNone(nw["high_weight_pct"])
        self.assertIsNone(nw["studies"][0]["weight_pct"])

    def test_conflict_without_consensus(self):
        a = rob2("SzK", unit="S1", status="complete", judgement=["low"])
        b = rob2("KP", unit="S1", status="complete", judgement=["high"], **{"2_6": "no"})
        s = A.rob_summary([a, b], "rob2")
        self.assertEqual(s["studies"][0]["source"], "conflict")
        p = A.rob_sync_proposal([a, b], ["study_id", "rob"], [["S1", ""]], "rob2")
        self.assertEqual((p["changes"], len(p["conflicts"])), ([], 1))
        same = rob2("KP", unit="S1", status="complete", judgement=["low"])
        self.assertEqual(A.rob_summary([a, same], "rob2")["studies"][0]["source"], "agreeing")

    def test_sync_proposal_mapping_and_matching(self):
        header = ["row_uid", "study_id", "study", "e1", "n1", "rob"]
        rows = [["rabc001", "S1", "Aronson 1948", "4", "123", "High risk of bias"],
                ["rabc002", "S2", "Ferguson 1949", "6", "306", "High risk of bias"],
                ["rabc003", "", "Kovács 2019", "3", "231", ""],
                ["rabc004", "S9", "Nincs 2001", "1", "10", "low"]]
        p = A.rob_sync_proposal(self.docs(), header, rows, "rob2", studies=self.STUDIES,
                                paths=["a", "b", "c.consensus", "d", "e"])
        self.assertEqual(contract(p, "ma.rob-sync-proposal"), [])
        self.assertEqual([(c["row_uid"], c["before"], c["after"], c["basis"]) for c in p["changes"]],
                         [("rabc001", "High risk of bias", "low", "consensus")])
        self.assertEqual(p["changes"][0]["source"], "c.consensus")
        self.assertEqual(p["changes"][0]["provenance"]["method"], "calculated")
        self.assertEqual(p["unchanged"], 1, "a 'High risk of bias' és a 'high' egy kategória")
        self.assertEqual([x["row_uid"] for x in p["skipped"]], ["rabc003"], "S3 csak vázlat")
        self.assertEqual([x["row_uid"] for x in p["unmatched"]], ["rabc004"])
        nocol = A.rob_sync_proposal(self.docs(), ["study_id", "e1"], [["S2", "1"]], "rob2", column="RoB")
        self.assertEqual((nocol["column"], nocol["column_exists"], nocol["changes"][0]["after"]),
                         ("RoB", False, "high"))
        self.assertTrue(nocol["warnings"])
        am = A.rob_sync_proposal([], header, rows, "amstar2")
        self.assertEqual(am["changes"], [])
        self.assertTrue(am["warnings"])

    def test_rob_column_mapping_per_tool(self):
        self.assertEqual(I.load("rob2").rob_column, {"low": "low", "some_concerns": "some", "high": "high"})
        ri = I.load("robins-i")
        self.assertEqual([ri.rob_value(v) for v in ("low", "moderate", "serious", "critical", "no_information")],
                         ["low", "some", "high", "critical", "no information"])
        self.assertEqual(I.load("robins-e").rob_value("very_high"), "critical")
        self.assertEqual(I.load("quadas2").rob_value("unclear"), "some")
        self.assertEqual(I.load("probast-ai").rob_value("high"), "high")
        for k in ("amstar2", "grade", "tripod-ai", "nos", "jbi"):
            self.assertIsNone(I.load(k).rob_column, k)
        from metaelemzes import tableio
        for k in I.available():
            inst = I.load(k)
            for verdict, cell in (inst.rob_column or {}).items():
                with self.subTest(tool=k, verdict=verdict):
                    self.assertNotEqual(tableio.rob_category(cell), "", "a tábla-ellenőrző (V027) felismeri")
                    if inst.level(verdict) in ("low", "some", "high"):
                        self.assertEqual(tableio.rob_category(cell), inst.level(verdict))


class TestProjectSyncAudit(unittest.TestCase):
    """A rob-oszlop szinkronja után az X003 (CSV rob ≠ végső összítélet) nem jelez — calculated eredettel."""
    HEADER = ["row_uid", "study_id", "study", "e1", "n1", "e2", "n2", "rob"]

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "proj")
        projekt.init(self.proj, "RoB-szinkron teszt")
        projekt.save_project_meta(self.proj, {"title": "RoB-szinkron teszt", "data_class": "A",
                                              "review_type": "intervention", "appraisal_tools": ["rob2"],
                                              "outcomes": [{"id": "o1", "name": "Halálozás",
                                                            "data": "03_adatok/o1.csv", "measure": "RR"}]})
        rows = [["rbcg01", "S1", "Aronson 1948", "4", "123", "11", "139", ""],
                ["rbcg02", "S2", "Ferguson 1949", "6", "306", "29", "303", "low"],
                ["rbcg03", "S3", "Rosenthal 1960", "3", "231", "11", "220", "low"]]
        os.makedirs(os.path.join(self.proj, "03_adatok"), exist_ok=True)
        self.csv = os.path.join(self.proj, "03_adatok", "o1.csv")
        with open(self.csv, "w", encoding="utf-8", newline="") as fh:
            fh.write(";".join(self.HEADER) + "\r\n" + "".join(";".join(r) + "\r\n" for r in rows))
        with open(self.csv, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        prov = {"schema": "szk.ma.provenance/v1", "table": "03_adatok/o1.csv", "table_sha256": sha,
                "cells": [{"row_uid": "rbcg02", "field": "rob", "value_as_entered": "low", "method": "reported",
                           "estimated": False, "source": {"doc": "pmid:1", "page": 2, "locator": "Table 1"}}]}
        with open(os.path.join(self.proj, "03_adatok", "o1.prov.json"), "w", encoding="utf-8") as fh:
            json.dump(prov, fh)
        high2 = ["low", "high", "low", "low", "low", "high"]
        # 2.6 N + 2.7 NI → „magas” a 2019-es folyamatábra szerint (2.6 N + 2.7 N csak „némi aggály”; v1 javítás A)
        a = rob2("SzK", unit="S1", status="complete", judgement=high2, **{"2_6": "no", "2_7": "no_information"})
        b = rob2("KP", unit="S1", status="complete", judgement=high2, **{"2_6": "no", "2_7": "probably_yes"})
        cons, unresolved = A.build_consensus(a, b, resolutions={"2.7": {"value": "PY", "reason": "egyeztetve"}})
        self.assertEqual(unresolved, [])
        for d in (a, b, cons):
            A.save(self.proj, d, now=TS)
        s2 = rob2("SzK", unit="S2", status="complete", judgement=["low", "low", "low", "low", "some_concerns",
                                                                  "some_concerns"], **{"5_1": "no_information"})
        A.save(self.proj, s2, now=TS)

    def x003(self):
        from metaelemzes import audit
        rep = audit.project_audit(self.proj)
        return [f for f in rep["findings"] if f["code"] == "X003"]

    def test_sync_resolves_x003(self):
        before = self.x003()
        self.assertEqual(len(before), 1)
        self.assertEqual(sorted(before[0]["row_uids"]), ["rbcg01", "rbcg02"])
        prop = A.project_rob_sync(self.proj, "o1")
        self.assertEqual(prop["tool"], "rob2")
        self.assertEqual(contract({k: v for k, v in prop.items() if k not in ("table", "table_sha256", "outcome")},
                                  "ma.rob-sync-proposal"), [])
        self.assertEqual([(c["row_uid"], c["after"]) for c in prop["changes"]],
                         [("rbcg01", "high"), ("rbcg02", "some")])
        self.assertEqual(prop["changes"][0]["basis"], "consensus")
        self.assertTrue(prop["changes"][0]["source"].endswith("S1.rob2.o1.consensus.json"))
        res = A.apply_rob_sync(self.proj, prop, actor="SzK", now=TS)
        self.assertEqual((res["applied"], res["provenance"]), (2, "03_adatok/o1.prov.json"))
        self.assertEqual(self.x003(), [])
        with open(self.csv, "rb") as fh:
            data = fh.read()
        self.assertTrue(data.endswith(b"\r\n"), "formátumtartó írás (CRLF)")
        self.assertIn(b"rbcg01;S1;Aronson 1948;4;123;11;139;high\r\n", data)
        with open(os.path.join(self.proj, "03_adatok", "o1.prov.json"), encoding="utf-8") as fh:
            prov = json.load(fh)
        self.assertEqual(prov["table_sha256"], hashlib.sha256(data).hexdigest())
        cells = {(c["row_uid"], c["field"]): c for c in prov["cells"]}
        self.assertEqual(cells[("rbcg01", "rob")]["method"], "calculated")
        self.assertEqual(cells[("rbcg02", "rob")]["history"][0]["method"], "reported")
        with self.assertRaises(A.AppraisalError):
            A.apply_rob_sync(self.proj, prop)
        again = A.project_rob_sync(self.proj, "o1")
        self.assertEqual((again["changes"], again["unchanged"]), ([], 2))

    def test_project_summary_with_run_plot(self):
        plot = {"schema": "szk.ma.plot/v2", "studies": [
            {"row_uid": "rbcg01", "study_id": "S1", "label": "Aronson 1948", "weight_pct": 70.0},
            {"row_uid": "rbcg02", "study_id": "S2", "label": "Ferguson 1949", "weight_pct": 30.0}]}
        os.makedirs(os.path.join(self.proj, "05_elemzes", "o1", "r1"))
        with open(os.path.join(self.proj, "05_elemzes", "o1", "r1", "plot_data.json"), "w", encoding="utf-8") as fh:
            json.dump(plot, fh)
        s = A.project_rob_summary(self.proj, "o1", plot="05_elemzes/o1/r1")
        self.assertEqual(s["high_weight_pct"], 70.0)
        self.assertEqual(contract(s, "rob-summary"), [])
        self.assertEqual(A.default_tool(self.proj), "rob2")
        with self.assertRaises(A.AppraisalError):
            A.project_rob_summary(self.proj, "o1", plot="nincs/plot_data.json")


# ================================================================== homlokzat-kompatibilitás és útválasztás
class TestFacadeShape(unittest.TestCase):
    def test_workbench_engine_names_and_keywords(self):
        """A munkapad (routes/appraisal_common.ENGINE) által várt nevek és kulcsszavak."""
        for name in ("instruments_list", "instrument_get", "appraisal_check", "appraisal_consensus", "rob_summary",
                     "rob_sync_proposal", "appraisal_validate", "tripod_check", "instrument_route"):
            self.assertTrue(callable(getattr(A, name)), name)
        inst = A.instrument_get("rob2")
        d = rob2()
        self.assertEqual(A.appraisal_check(d, instrument=inst)["overall"]["implied"], "low")
        self.assertEqual(A.appraisal_validate(d, instrument=inst), [])
        self.assertEqual(A.rob_summary([d], tool="rob2", outcome="o1", plot=None, studies=None,
                                       paths=["x.json"])["studies"][0]["files"], ["x.json"])
        p = A.rob_sync_proposal([d], ["study_id", "rob"], [["S1", ""]], tool="rob2", row_uids=["r00001"],
                                studies=None, column=None, paths=["x.json"])
        self.assertEqual(p["changes"], [], "vázlatból nincs szinkron")

    def test_route(self):
        self.assertEqual(A.instrument_route("randomised controlled trial")[0]["tool"], "rob2")
        self.assertEqual(A.instrument_route("rct_parallel")[0]["tool"], "rob2")
        tools = [h["tool"] for h in A.instrument_route("predikciós modell külső validálása, TRIPOD")]
        self.assertEqual(tools[:2], ["probast-ai", "tripod-ai"])
        self.assertEqual(A.instrument_route("diagnosztikai pontosság")[0]["tool"], "quadas2")
        self.assertEqual(A.instrument_route("valami más"), [])


# ================================================================== szerződés-példák
class TestContractExamples(unittest.TestCase):
    NAMES = ("instrument", "appraisal", "appraisal-result", "rob-summary", "ma.appraisal-agreement",
             "ma.rob-sync-proposal")

    def test_schemas_registered_and_canonical(self):
        for n in self.NAMES:
            with self.subTest(contract=n):
                s = K.load(n, 1)
                self.assertEqual(s["$id"], K.urn(n, 1))
                self.assertEqual(s["properties"]["schema"]["const"], K.document_id(n, 1))
                self.assertEqual(K.raw(n, 1).decode("utf-8"), K.canonical_text(s))
                self.assertIn((n, 1), K.CONTRACTS)

    def test_examples_validate_as_labelled(self):
        d = os.path.join(ROOT, "tests", "reference", "contract_examples")
        seen = {}
        for fn in sorted(os.listdir(d)):
            m = re.match(r"^(?P<name>[a-z0-9][a-z0-9.\-]*?)\.v1\.(?P<label>[a-z0-9_\-]+)\.json$", fn)
            if not m or m.group("name") not in self.NAMES:
                continue
            with open(os.path.join(d, fn), encoding="utf-8") as fh:
                doc = json.load(fh)
            errs = contract(doc, m.group("name"))
            bad = m.group("label").startswith("invalid")
            seen.setdefault((m.group("name"), bad), 0)
            seen[(m.group("name"), bad)] += 1
            with self.subTest(example=fn):
                self.assertEqual(bool(errs), bad, errs)
        for n in self.NAMES:
            self.assertGreaterEqual(seen.get((n, False), 0), 2, n)
            self.assertGreaterEqual(seen.get((n, True), 0), 2, n)


if __name__ == "__main__":
    unittest.main()
