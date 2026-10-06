# -*- coding: utf-8 -*-
"""Motor ↔ validator 2.0.0 egyezés (2026-10): a teljes válaszkombináció-felsorolás eltérés-osztályai, és melyik oldal
követte a publikált kritériumokat.

A felsorolás (doménenként minden válaszkombináció — a ROBINS-I 1. doménje 1 119 744 —, a validatort generált
Markdown-rekordokon a saját --rollup útján, a motort az appraisal.check-kel futtatva) ezeket az eltéréseket találta;
a motor oldalán javítottak itt regressziós tesztet kapnak:

- RoB 2 1. domén: rejtett szekvencia + nem véletlen szekvencia + problémára utaló kiinduló különbség — a 2019-es
  kritériumtábla (újraközölve: PMC8191126, 2. táblázat) szerint „némi aggály”; a motor eddig a szigorúbb „magas”-t adta
  („ellenőrizhetetlen ág”);
- RoB 2 betartási (per-protocol) változat: a motorból hiányzott; most az „adherence” hatókör (2a.1–2a.6, a sablon
  2.1–2.6-a), a kritériumtábla szerinti 2. doménnel és az „ha alkalmazható” N/A-val;
- a nem kérdezett tétel kóbor válasza szabályt indított (ROBINS-I 1.7 NI mellett az 1.8 „Nem” → „súlyos”);
- ROBINS-I 1.2 / 1.3 „Nincs információ”: a 2016-os Table A csak az I/VI és N/VN választ irányítja tovább;
- ROBINS-I 3.2 „Nem”: a Table B szerint legalább mérsékelt, önmagában nem súlyos;
- ROBINS-E 1.4 (irányító) „Nincs információ”: nem hagyhatja „alacsony”-an a domént;
- QUIPS 3f / 5e: „Nem alkalmazható” (nem volt imputálás) — a validator elfogadta, a motor nem;
- AMSTAR 2 'weakness' konvenció: a 8. tétel „részben igen”-je is gyengeség (KB AMSTAR2-00, validator 2.0.0);
- a routing_conflicts minden kérdezett N/A-t megnevez, akkor is, ha egy korábbi szabály döntött.

Dokumentált konvenció-eltérések (nem javítás, a megjegyzésük a hídban): C1 — „Nem alkalmazható” kérdezett tételen
(a motor NI-ként számol, a validator INCOMPLETE); C2 — ROBINS-I/-E köztes/felső határeset (a motor a szigorúbbat, a
validator a „legalább” szintet adja); AMSTAR 2 'meets' (projektkonvenció) vs. a validator 'weakness'-e.

Ha a javított validator (≥ 2.0.0) elérhető (SZK_VALIDATOR_DIR, különben a szokásos helyek), egy rögzített magú minta
a két oldalt közvetlenül is összeveti (a validatort csak olvassuk, nem módosítjuk)."""
import importlib.util
import itertools
import json
import os
import random
import re
import sys
import tempfile
import unittest

from _helpers import ROOT  # noqa: F401  (sys.path)
from metaelemzes import appraisal as A, grade_help as G, instruments as I

Y, PY, PN, N, NI, NA = "yes", "probably_yes", "probably_no", "no", "no_information", "not_applicable"
YES, NO = (Y, PY), (N, PN)
VALIDATOR_DIRS = [os.environ.get("SZK_VALIDATOR_DIR") or "", "/home/user/szk-plugins/plugins/validator",
                  "/home/user/szilikaroly/szk-plugins/plugins/validator"]


def doc(tool, answers, scope=None):
    return {"schema": "szk.appraisal/v1", "tool": tool, "scope": scope, "target": {"unit": "S1", "study_id": "S1"},
            "assessor": "SzK", "second_assessor": None, "status": "draft", "origin": "human", "approved_by": None,
            "approved_at": None, "answers": {k: {"value": v} for k, v in answers.items()}, "domain_judgements": [],
            "applicability": [], "overall": None}


def dom(res, did):
    return next(x for x in res["domains"] if x["domain"] == did)


def implied(tool, answers, did, scope=None):
    return dom(A.check(doc(tool, answers, scope)), did)["implied"]


R2 = {"1.1": Y, "1.2": Y, "1.3": N, "3.1": Y, "3.2": NA, "3.3": NA, "3.4": NA, "4.1": N, "4.2": N, "4.3": N,
      "4.4": NA, "4.5": NA, "5.1": Y, "5.2": N, "5.3": N}
R2_ADH = dict(R2, **{"2a.1": N, "2a.2": N, "2a.3": NA, "2a.4": N, "2a.5": N, "2a.6": NA})


def ref_adhering_d2(v):
    """A betartási 2. domén a 2019-es kritériumtábla szerint (PMC8191126, 2. táblázat) és a sablon útválasztásával:
    probléma = (tudtak a besorolásról ÉS a protokollon kívüli beavatkozások nem / nem ismerten kiegyensúlyozottak) VAGY
    végrehajtási hiba VAGY nem-adherencia (I/VI/NI); probléma nélkül alacsony; probléma mellett a megfelelő elemzés
    (2.6 I/VI) „némi aggály”, különben (N/VN/NI) „magas”. A „ha alkalmazható” 2.3–2.5 N/A-ja nem probléma."""
    aware = v["2a.1"] not in NO or v["2a.2"] not in NO
    problem = (aware and v["2a.3"] in NO + (NI,)) or v["2a.4"] in YES + (NI,) or v["2a.5"] in YES + (NI,)
    if not problem:
        return "low"
    return "some_concerns" if v["2a.6"] in YES else "high"


class TestRob2Domain1CriteriaTable(unittest.TestCase):
    def test_concealed_nonrandom_with_baseline_problem_is_some_concerns(self):
        for r, b in itertools.product((N, PN), (Y, PY)):
            with self.subTest(**{"1.1": r, "1.3": b}):
                self.assertEqual(implied("rob2", dict(R2, **{"1.1": r, "1.3": b}), "1", "assignment"),
                                 "some_concerns")
        # a magas ág változatlan: nem rejtett, vagy nincs információ a rejtésről + problémára utaló különbség
        self.assertEqual(implied("rob2", dict(R2, **{"1.2": PN}), "1", "assignment"), "high")
        self.assertEqual(implied("rob2", dict(R2, **{"1.2": NI, "1.3": Y}), "1", "assignment"), "high")
        self.assertNotIn("1-d", [r["id"] for r in I.load("rob2").rules("1", "assignment")])


class TestRob2AdheringVariant(unittest.TestCase):
    def test_definition(self):
        inst = I.load("rob2")
        self.assertEqual(inst.scope_ids, ["assignment", "adherence"])
        self.assertEqual(inst.default_scope, "assignment")
        adh = [it for it in inst.slots("adherence") if it["domain"] == "2"]
        self.assertEqual([(it["key"], inst.display_id(it), it["validator_id"]) for it in adh],
                         [("2a.%d" % i, "2.%d" % i, "2.%d" % i) for i in range(1, 7)])
        self.assertEqual([it["key"] for it in adh if it.get("if_applicable")], ["2a.3", "2a.4", "2a.5"])
        self.assertEqual(inst.doc["counts"]["per_scope"], {"assignment": 22, "adherence": 21})
        self.assertEqual(I.definition_problems(inst.doc), [])
        # a besorolási 2. domén szabályai az adherence hatókörben nem futnak, és fordítva
        self.assertTrue(all(r.get("scopes") == ["assignment"] for r in inst.rules("2", "assignment")))
        self.assertTrue(all(r.get("scopes") == ["adherence"] for r in inst.rules("2", "adherence")))

    def test_every_combination_matches_the_criteria_table(self):
        keys = ["2a.%d" % i for i in range(1, 7)]
        opts = {"2a.1": (Y, PN, NI), "2a.2": (Y, PN, NI), "2a.3": (PY, N, NI, NA), "2a.4": (Y, PN, NI, NA),
                "2a.5": (PY, N, NI, NA), "2a.6": (Y, PN, NI, NA)}
        bad = []
        for combo in itertools.product(*[opts[k] for k in keys]):
            v = dict(zip(keys, combo))
            r = A.check(doc("rob2", dict(R2_ADH, **v), "adherence"))
            d2 = dom(r, "2")
            want = ref_adhering_d2(v)
            asked6 = ref_adhering_d2(dict(v, **{"2a.6": Y})) != "low"
            if v["2a.6"] == NA and asked6:
                # C1: N/A a kérdezett 2.6-on — a motor NI-ként számol (→ magas) és megnevezi
                self.assertEqual((d2["implied"], d2["routing_conflicts"]), ("high", ["2a.6"]), v)
                continue
            if d2["implied"] != want or d2["routing_conflicts"]:
                bad.append((v, want, d2["implied"], d2["routing_conflicts"]))
        self.assertEqual(bad[:5], [], "%d eltérés a kritériumtáblától" % len(bad))

    def test_if_applicable_na_is_not_a_routing_conflict(self):
        r = A.check(doc("rob2", dict(R2_ADH, **{"2a.1": Y, "2a.3": NA}), "adherence"))
        self.assertEqual((dom(r, "2")["implied"], dom(r, "2")["routing_conflicts"]), ("low", []))
        self.assertTrue(r["complete"])
        self.assertEqual(r["expected"], 21)
        self.assertEqual(r["overall"]["implied"], "low")
        r = A.check(doc("rob2", dict(R2_ADH, **{"2a.4": Y, "2a.6": Y}), "adherence"))
        self.assertEqual(dom(r, "2")["implied"], "some_concerns")
        self.assertEqual(dom(r, "2")["rule_path"], ["2A-b"])
        r = A.check(doc("rob2", dict(R2_ADH, **{"2a.5": NI, "2a.6": NI}), "adherence"))
        self.assertEqual(dom(r, "2")["implied"], "high")

    def test_scopes_do_not_mix(self):
        # a betartási hatókörben a besorolási 2.x válasz a hatókörön kívül esik (és fordítva)
        r = A.check(doc("rob2", dict(R2_ADH, **{"2.6": N}), "adherence"))
        self.assertEqual(dom(r, "2")["implied"], "low")
        self.assertTrue(any("hatókörön kívüli" in w["hu"] for w in r["warnings"]))


class TestRoutingStrayAnswers(unittest.TestCase):
    RI = {"1.1": Y, "1.2": Y, "1.3": Y, "1.4": NA, "1.5": NA, "1.6": NA, "1.7": NI, "1.8": N}

    def test_answer_at_a_question_not_asked_does_not_count(self):
        # 1.7 NI → az 1.8-at nem kérdezik: a kóbor 1.8 „Nem” nem ad „súlyos”-at (validator: listázza, nem pontozza)
        self.assertEqual(implied("robins-i", self.RI, "1", "assignment"), "moderate")
        self.assertEqual(implied("robins-i", dict(self.RI, **{"1.7": Y}), "1", "assignment"), "serious")
        base = {"1.1": Y, "1.2": N, "1.3": NA, "1.4": NI, "1.5": N, "1.6": N, "1.7": NA, "1.8": NA}
        self.assertEqual(implied("robins-i", base, "1", "assignment"), "moderate")       # 1.5 csak 1.4 I/VI-nél

    def test_ni_at_robins_i_1_2_or_1_3_opens_no_branch(self):
        for over in ({"1.2": NI, "1.3": NA}, {"1.2": Y, "1.3": NI}):
            ans = dict({k: NA for k in ("1.4", "1.5", "1.6", "1.7", "1.8")}, **{"1.1": Y}, **over)
            r = A.check(doc("robins-i", ans, "assignment"))
            d1 = dom(r, "1")
            with self.subTest(**over):
                self.assertEqual((d1["implied"], d1["missing"], d1["routing_conflicts"]), ("moderate", [], []))
                self.assertEqual(d1["rule_path"], ["1-r"])
                self.assertLessEqual({"1.4", "1.5", "1.6", "1.7", "1.8"}, set(d1["not_asked"]))
        inst = I.load("robins-i")
        self.assertEqual(inst.item("1.4")["ask_if"]["all"][1],
                         {"any": [{"item": "1.2", "in": [N, PN]}, {"item": "1.3", "in": [N, PN]}]})

    def test_routing_conflicts_name_every_na_asked(self):
        ans = {"1.1": PY, "1.2": PN, "1.3": PY, "1.4": PN, "1.5": N, "1.6": NA, "1.7": N, "1.8": N}
        d1 = dom(A.check(doc("robins-i", ans, "assignment")), "1")
        self.assertEqual((d1["implied"], d1["routing_conflicts"]), ("serious", ["1.6"]))


class TestRobinsIClassification(unittest.TestCase):
    def test_3_2_no_is_moderate_not_serious(self):
        self.assertEqual(implied("robins-i", {"3.1": Y, "3.2": N, "3.3": N}, "3", "assignment"), "moderate")
        self.assertEqual(implied("robins-i", {"3.1": Y, "3.2": PN, "3.3": Y}, "3", "assignment"), "serious")
        self.assertEqual(implied("robins-i", {"3.1": N, "3.2": Y, "3.3": N}, "3", "assignment"), "serious")
        self.assertEqual(I.load("robins-i").item("3.2")["severity"], "some")


class TestRobinsEGateway(unittest.TestCase):
    def test_ni_at_router_1_4_is_not_low(self):
        ans = {"1.1": Y, "1.2": Y, "1.3": N, "1.4": NI, "1.5": NA}
        d1 = dom(A.check(doc("robins-e", ans)), "1")
        self.assertEqual((d1["implied"], d1["unknown_at"]), ("some_concerns", ["1.4"]))
        d1 = dom(A.check(doc("robins-e", dict(ans, **{"1.4": N}))), "1")
        self.assertEqual(d1["implied"], "low")

    def test_gateway_ni_settled_by_the_question_it_opens(self):
        # 5.1 NI, 5.2 Igen → az 5.3 kérdezett; 5.3 Igen (bizonyíték, hogy a hiány nem torzított) → alacsony
        self.assertEqual(implied("robins-e", {"5.1": NI, "5.2": Y, "5.3": Y}, "5"), "low")
        self.assertEqual(implied("robins-e", {"5.1": NI, "5.2": N, "5.3": NA}, "5"), "some_concerns")


class TestQuipsNotApplicable(unittest.TestCase):
    def test_3f_and_5e_accept_na(self):
        inst = I.load("quips")
        yes = {it["key"]: Y for it in inst.items}
        r = A.check(doc("quips", dict(yes, **{"3f": NA, "5e": NA})))
        self.assertEqual((r["invalid"], r["complete"], r["overall"]["implied"]), ([], True, "low"))
        r = A.check(doc("quips", dict(yes, **{"3a": NA})))
        self.assertEqual([x["item"] for x in r["invalid"]], ["3a"])
        self.assertEqual(sorted(it["id"] for it in inst.items if NA in inst.allowed(it)), ["3f", "5e"])


class TestAmstar2WeaknessConvention(unittest.TestCase):
    def test_item_8_partial_yes_counts_under_weakness(self):
        ans = {str(i): "yes" for i in range(1, 17)}
        ans.update({"8": "partial_yes", "10": "no"})
        blk = A.amstar2_rating(ans)
        self.assertEqual((blk["rating"], blk["alternative"]["rating"]), ("high", "moderate"))
        self.assertEqual(blk["alternative"]["weaknesses"], ["8", "10"])
        g = G.amstar2_consistency(ans)
        self.assertEqual(g["by_convention"]["weakness"], {"rating": "moderate", "critical_flaws": [],
                                                           "weaknesses": ["8", "10"]})
        self.assertEqual(g["by_convention"]["meets"]["rating"], "high")


def _validator():
    for d in VALIDATOR_DIRS:
        path = os.path.join(d, "scripts", "appraise.py") if d else ""
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(os.path.join(d, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
                ver = tuple(int(x) for x in re.findall(r"\d+", json.load(fh).get("version") or "0")[:3])
        except (OSError, ValueError):
            continue
        if ver < (2, 0, 0):
            continue
        spec = importlib.util.spec_from_file_location("szk_validator_parity_ro", path)
        mod = importlib.util.module_from_spec(spec)
        old = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(mod)
            mod.load_all()
        except Exception:                                                   # noqa: BLE001
            continue
        finally:
            sys.dont_write_bytecode = old
        if "settled_by" in mod.rollup_signalling.__code__.co_varnames:     # a 2026-10-es egyeztetés utáni kiadás
            return mod
    return None


VMOD = _validator()
SPELL = {Y: "Yes", PY: "Probably yes", PN: "Probably no", N: "No", NI: "No information", NA: "N/A",
         "weak_no": "Weak no", "strong_no": "Strong no", "unclear": "Unclear", "partly": "Partly"}
TIERS = {"LOW": "low", "SOME CONCERNS / UNCLEAR": "some", "HIGH / SERIOUS": "high", "INCOMPLETE": "incomplete"}


@unittest.skipUnless(VMOD, "nincs javított (≥ 2.0.0, settled_by) validator — a közvetlen összevetés kimarad")
class TestSeededParityWithValidator(unittest.TestCase):
    """Rögzített magú minta doménenként: a validator --rollup-ja generált Markdownon vs. a motor; minden eltérés C1 vagy
    C2 kell legyen (a dokumentált konvenciók)."""
    PLAN = (("rob2", "assignment"), ("rob2", "adherence"), ("robins-i", "assignment"), ("robins-i", "adherence"),
            ("robins-e", "all"), ("quadas2", "all"), ("quips", "all"))
    C2 = {("robins-i", "2"): ("2.5",), ("robins-i", "4"): ("4.6",), ("robins-i", "5"): ("5.4", "5.5"),
          ("robins-e", "3"): ("3.3",), ("robins-e", "4"): ("4.2",), ("robins-e", "5"): ("5.3",)}

    def test_seeded_sample(self):
        tools = VMOD.load_all()
        rnd = random.Random(20261006)
        tmp = tempfile.mkdtemp(prefix="szk_parity_")
        bad = []
        for tool, scope in self.PLAN:
            vinst, einst = tools[tool], I.load(tool)
            escope = scope if scope != "all" else None
            emap = {(it.get("validator_id") or it["id"]): it for it in einst.slots(escope)}
            vitems = vinst.scoped(scope)
            for d in sorted({it["domain"] for it in vitems}):
                its = [it for it in vitems if it["domain"] == d]
                vocs = []
                for it in its:
                    vv = vinst.vocab_of(it)
                    vv = {VMOD._canonical(a) for a in vinst.answers} | {"n/a"} if vv is None else vv
                    vocs.append(sorted({"not_applicable" if x == "n/a" else x.replace(" ", "_") for x in vv}
                                       | set(einst.allowed(emap[it["id"]]))))
                for _ in range(150):
                    ans = {it["id"]: rnd.choice(v) for it, v in zip(its, vocs)}
                    rows = ["| # | Q | Answer | Evidence |", "|---|---|---|---|"]
                    rows += ["| %s | q | %s |  |" % (i, SPELL[a]) for i, a in ans.items()]
                    marker = "<!-- numbering: %s %s -->\n" % (tool, vinst.meta["numbering"]) \
                        if vinst.meta.get("numbering") else ""
                    path = os.path.join(tmp, "r.md")
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(marker + "\n".join(rows) + "\n")
                    lines = VMOD.rollup_signalling(vinst, VMOD.read_answers(VMOD.Path(path), vinst, scope),
                                                   [it for it in vitems if it["domain"] == d])
                    m = re.search(r"\): (LOW|SOME CONCERNS / UNCLEAR|HIGH / SERIOUS|INCOMPLETE)", "\n".join(lines))
                    vt = TIERS[m.group(1)]
                    res = A.check(doc(tool, {emap[i]["key"]: a for i, a in ans.items()}, escope))
                    ed = dom(res, d)
                    inv = {v: k for k, v in einst.rollup["tiers"].items()}
                    et = inv.get(ed["implied"], "incomplete") if ed["implied"] else "incomplete"
                    if vt == et:
                        continue
                    if vt == "incomplete" and ed["routing_conflicts"]:
                        continue                                                       # C1
                    c2 = self.C2.get((tool, d), ())
                    if (vt, et) == ("some", "high") and c2 and (
                            any(ans.get(i) == NI for i in c2) or all(ans.get(i) in NO for i in c2)):
                        continue                                                       # C2
                    bad.append((tool, scope, d, ans, vt, et))
        self.assertEqual(bad[:3], [], "%d nem dokumentált eltérés" % len(bad))


if __name__ == "__main__":
    unittest.main()
