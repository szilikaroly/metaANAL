# -*- coding: utf-8 -*-
"""validator-adapter — RoB-család, PROBAST+AI, TRIPOD+AI, GRADE, AMSTAR 2 (terv 4.11, 5.0 H1–H4, H12, H13, 5.4, 6.5).

Egy ``szk.appraisal/v1`` értékelést a validator pluginnal KERESZTELLENŐRIZ, és ``szk.appraisal-result/v1``-et ad.
Az ítéletek és a teljesség elsődleges forrása a motor (``metaelemzes.api``); ez az adapter a validator saját
véleményét hozza, a hibái elleni őrökkel, felcímkézve.

**Módok** (a caps-rekordból, eszközönként):

- ``json`` (validator ≥ 1.1, PR-V1): ``appraise.py --rollup <f.json> --tool <eszköz> --json`` (a ``checklist.py``
  eszközeinél ``--verify``), a stdout ``szk.appraisal-result/v1``. A bemenetbe csak a válaszértékek és az ítéletek
  kerülnek — idézet, indoklás, megjegyzés nem (adatvédelem, C osztály).
- ``bridge`` (1.0.0, legacy): (1) a plugin ``--skeleton``-jából a hely-lista (tétel-azonosítók, menetek, domének);
  (2) a validator sablonjával egyező Markdown-tábla a futásidejű tmp-be (a bizonyíték-oszlopban csak ``[E1]``-
  hivatkozás, soha válaszszótárbeli szó; a kérdés-oszlopban „—”, hogy a soron belüli keresés ne tévedjen);
  (3) ``--verify``, majd (``appraise.py``-nál) ``--rollup``; (4) a stdout rögzített mintákkal olvasva.

**Őrök** (5.0; aktívak, ha a hiba a telepített verzióban él — legacy módban a beépített tábla
``caps.BUILTIN_ISSUES`` ``fixed_in`` verziója, újabb pluginnál a kézfogás ``known_issues``-a szerint; a validator
2.0.0 — szk-plugins#5 — mind a hatot javítja, így ott egyik sem kapcsol be, az 1.0.x-en mind él):

- **H1** (TRIPOD+AI) és **H2** (PROBAST+AI): a teljességet a munkapad SAJÁT JSON-jából számolja, menettel
  minősített kulcsokkal (``development/1.1``); a plugin számlálása ``validator_reported``-ként, ``trusted: false``
  jelöléssel kerül mellé. A státuszt soha nem soron belüli kereséssel olvassuk, csak a státusz-cellából.
- **H3** (GRADE): a publikációs torzítás „suspected”/„strongly suspected” értékénél a validator rollupja NEM
  megbízható (sosem minősít le). A motor konvenciója él (11. fejezet, 4. döntés): a „suspected” FELOLDATLAN, amíg
  ember nem dönt 0 vagy −1 között indoklással (``grade.unresolved``); a bizonyosság ilyenkor ``null``.
- **H4** (AMSTAR 2): eszközönkénti álnév-tábla a globális ``_norm`` helyett — a ``partial_yes`` mindig
  „Partial yes” szöveggel megy (soha „PY”, amit a validator „probably yes”-nek olvasna); az 1.0.0 besorolása a
  ``weakness`` konvenciónak felel meg (a 8. tétel „részben igen”-jét kivéve), és így is címkézzük (a projekt
  konvenciója ``meets``, KB AMSTAR2-00). A 2.0.0 besorolása pontosan a motor ``weakness`` konvenciója.
- **H12** (polaritás): a validator 1.0.0 referenciafájljában néhány tétel polaritás-címkéje eltér a publikált
  eszköztől (QUADAS-2 1.2/1.3, ROBINS-E 2.3/6.2 és ROBINS-I 6.3 „reverse”, a ROBINS-E 5.2 nem) — a motor
  definíciója a publikált változatot követi. Ha ilyen tétel válaszolt, az érintett domének és az összítélet
  validator-ítélete nem megbízható (``reliable: false``, ``unreliable_domains``); a motor ítélete számít.
- **H13** (számozás): a motor a publikált számozást használja (ROBINS-I 2016: ``numbering_changed``; QUIPS a–g:
  ``validator_ids``), a validator 1.0.0 a régit — ugyanaz az azonosító MÁS kérdést jelöl. Ezeket a tételeket a híd
  NEM küldi át (különben a validator rossz kérdésre adott választ értékelne); a validator teljessége és ítélete így
  nem vethető össze a motoréval (``comparable: false``).

**A javított kiadás** (``FIXED_VERSION``, 2.0.0; ``fixed_release``): ugyanaz a bridge, a javított plugin
szótárával és kimenetével —

- a ROBINS-I és a QUIPS váza a publikált azonosítókat adja (2016 Table A; QUIPS 1a–6d), egyezik a motorral: minden
  válasz átmegy, az eredmény összevethető; a Markdown a váz ``numbering:`` jelölőjét viszi (különben a plugin régi,
  1.x számozású fájlnak nézhetné, és nem pontozná);
- AMSTAR 2: a „Nem alkalmazható” (11/12/15) ``N/A``-ként megy; GRADE: a „nagyon nagy hatás” ``Very large`` (+2), a
  gyanított publikációs torzítás ember által rögzített feloldása a plugin szótárában megy (0 → ``Undetected``,
  −1 → ``Strongly suspected``; a −2-t a plugin nem fejezi ki, ott a bizonyossága nem összevethető — ``PB2``);
- a kimenet új szavai: ``INCOMPLETE`` domén és összítélet (nincs ítélet, amíg hely üres), ``UNRESOLVED`` és
  ``INCOMPLETE`` GRADE-bizonyosság, fordított polaritású kiváltó tételek, középső szint („rules out low”), QUIPS
  „Partly”, ``Phase 3`` (ROBIS), ``INVALID`` (a tételnél nem választható válasz), ``exit 1: … not final``;
- QUIPS: „Nem alkalmazható” a 3f/5e-n ``N/A``-ként; ROBINS-E 1.1 „gyenge / erős nem” ``Weak no`` / ``Strong no``;
  RoB 2 betartási változat: a motor 2a.1–2a.6 kulcsai a validator 2.1–2.6-ján (``validator_id``, csak a hatókör
  tételei mennek át);
- a teljes válaszkombináció-felsorolás után (2026-10) csak dokumentált konvenció-eltérések maradtak, ezeket
  megjegyzés mondja ki (``rule_differences``; nem őr, nem a plugin hibája): C1 — „Nem alkalmazható” kérdezett
  tételen (a motor NI-ként számol, a validator INCOMPLETE); C2 — ROBINS-I/-E, ahol a válaszok a köztes és a felső
  szint között nem döntenek (a motor a szigorúbbat adja, a validator a „legalább” szintet). A ROBINS-I 2.1 mindkettőben
  irányító kérdés (a korábbi 2.1-megjegyzés megszűnt).

Az implikált ítélet a validator ``algorithm`` címkéjével jön (``conservative`` = NEM a hivatalos folyamatábra) —
hivatalos eredményként soha nem jeleníthető meg (6.5). Statisztikát nem számol; a teljesség csak darabszám."""
import json
import os
import re
import shutil
import tempfile
import threading

from .base import USABLE_STATES, Adapter, error

PLUGIN = "validator"
RESULT_SCHEMA = "szk.appraisal-result/v1"
APPRAISAL_SCHEMA = "szk.appraisal/v1"
INSTRUMENT_SCHEMA = "szk.instrument/v1"
TIMEOUT = 30.0
MAX_ANSWERS = 1000
PASSES = ("development", "evaluation")
TOOL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
KEY_RE = re.compile(r"^((development|evaluation)/)?[0-9A-Za-z.\-]+$")
SCOPE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

# eszköz → (szkript, a validator eszköz-neve, az 5.2 funkció)
TOOLS = {
    "rob2": ("appraise.py", "rob2", "rob"), "robins-i": ("appraise.py", "robins-i", "rob"),
    "robins-e": ("appraise.py", "robins-e", "rob"), "quadas2": ("appraise.py", "quadas2", "rob"),
    "quips": ("appraise.py", "quips", "rob"), "nos": ("appraise.py", "nos", "rob"),
    "jbi": ("appraise.py", "jbi", "rob"), "robis": ("appraise.py", "robis", "rob"),
    "amstar2": ("appraise.py", "amstar2", "amstar2"), "grade": ("appraise.py", "grade", "grade"),
    "probast-ai": ("checklist.py", "probast", "probast"), "tripod-ai": ("checklist.py", "tripod", "tripod"),
}
DEFAULT_SCOPE = {"rob2": "assignment", "robins-i": "assignment", "jbi": "cohort", "nos": "cohort",
                 "probast-ai": "both", "tripod-ai": "both"}
CHECKLIST_SCOPES = ("development", "evaluation", "both")
# a validator rollup-algoritmusa eszközönként (4.11 rollup.algorithm)
ALGORITHM = {"rob2": "conservative", "robins-i": "conservative", "robins-e": "conservative",
             "quadas2": "conservative", "quips": "conservative", "robis": "conservative", "jbi": "none",
             "nos": "count", "amstar2": "published", "grade": "published", "probast-ai": "none",
             "tripod-ai": "none"}

_SIGNALLING = {"yes": "Yes", "probably_yes": "Probably yes", "probably_no": "Probably no", "no": "No",
               "no_information": "No information", "not_applicable": "N/A"}
# H4: eszközönkénti álnév-tábla — kanonikus érték → a validator 1.0.0 válasz-tokenje (szó szerint)
ANSWER_TOKENS = {
    "rob2": _SIGNALLING, "robins-i": _SIGNALLING, "robins-e": _SIGNALLING, "robis": _SIGNALLING,
    "probast-ai": _SIGNALLING,
    "quadas2": {"yes": "Yes", "no": "No", "unclear": "Unclear", "not_applicable": "N/A"},
    "quips": {"yes": "Yes", "partly": "Partly", "no": "No", "unclear": "Unclear"},
    "jbi": {"yes": "Yes", "no": "No", "unclear": "Unclear", "not_applicable": "Not applicable"},
    "nos": {"yes": "Yes", "partial_yes": "Partial yes", "no": "No", "unclear": "Unclear"},
    # „Partial yes”, SOHA „PY” (H4); N/A-t az 1.0.0 AMSTAR 2-rollupja hibának számolná → üresen marad
    "amstar2": {"yes": "Yes", "partial_yes": "Partial yes", "no": "No"},
    "grade": {"high": "High", "low": "Low", "not_serious": "Not serious", "serious": "Serious",
              "very_serious": "Very serious", "undetected": "Undetected", "suspected": "Suspected",
              "strongly_suspected": "Strongly suspected", "no": "No", "yes": "Yes", "very_large": "Yes"},
    "tripod-ai": {"present": "Present", "partial": "Partial", "missing": "Missing", "not_applicable": "N/A"},
}
# kanonikus értékek, amelyeket a validator 1.0.0 nem tud kifejezni (válasznak számítanak, de üresen mennek át)
UNEXPRESSIBLE = {"amstar2": ("not_applicable",), "quips": ("not_applicable",), "robins-e": ("weak_no", "strong_no")}
PUB_BIAS_DOMAIN = "5"

# a validator első javított kiadása (szk-plugins#5): tételenkénti szótár, publikált ROBINS-I/QUIPS-számozás, helyes
# polaritás, INCOMPLETE/UNRESOLVED a csendes LOW helyett. Az 5.0 őreinek fixed_in-je is ez (caps.BUILTIN_ISSUES).
FIXED_VERSION = "2.0.0"
# a javított kiadás szótár-többlete (a tételenkénti listája szerint: N/A csak az AMSTAR 2 11/12/15-ön, „Very large”
# csak a GRADE 6.1-en — ugyanott, ahol a motor is megengedi)
TOKENS_FIXED = {"amstar2": {"not_applicable": "N/A"}, "grade": {"very_large": "Very large"},
                "quips": {"not_applicable": "N/A"}, "robins-e": {"weak_no": "Weak no", "strong_no": "Strong no"}}
# GRADE: az ember által rögzített feloldás (resolution.step) a javított kiadás szótárában (a „Suspected” maga
# feloldatlan — UNRESOLVED —, a döntést a plugin szavaival kell kimondani)
PB_RESOLVED = {("suspected", 0): "undetected", ("suspected", -1): "strongly_suspected"}


def _version_tuple(value):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def fixed_release(cap):
    """A telepített validator a javított kiadás (≥ ``FIXED_VERSION``)? Ismeretlen verziónál nem: ott a régi,
    óvatos viselkedés marad (mint a caps ``_issue_active``-jénél)."""
    cur = _version_tuple((cap or {}).get("version"))
    return cur is not None and cur >= _version_tuple(FIXED_VERSION)


def answer_tokens(tool, fixed=False):
    """kanonikus érték → a telepített validator válasz-tokenje (H4: eszközönként, szó szerint)."""
    tokens = dict(ANSWER_TOKENS[tool])
    if fixed:
        tokens.update(TOKENS_FIXED.get(tool, {}))
    return tokens


def unexpressible(tool, fixed=False):
    return () if fixed else UNEXPRESSIBLE.get(tool, ())


def _resolved_step(answer, allowed):
    """Az ember rögzített, indokolt feloldása (resolution.step), ha az ``allowed`` lépések egyike; különben None."""
    res = (answer or {}).get("resolution")
    if (isinstance(res, dict) and res.get("step") in allowed and isinstance(res.get("rationale"), str)
            and res["rationale"].strip()):
        return res["step"]
    return None


def bridge_values(doc, tool, fixed=False):
    """A hídon átmenő kanonikus értékek. A javított kiadásnál a GRADE gyanított publikációs torzításának ember által
    rögzített feloldása a plugin szótárában megy (0 → undetected, −1 → strongly_suspected): a „Suspected” a 2.0.0-ban
    UNRESOLVED, és a plugin maga is így kéri a döntést. Az 1.0.0-nál változatlan (ott a H3 őr dönt)."""
    values = _values(doc)
    if fixed and tool == "grade":
        answers = doc.get("answers") or {}
        for k, v in list(values.items()):
            if k.split(".")[0] != PUB_BIAS_DOMAIN or v != "suspected":
                continue
            step = _resolved_step(answers.get(k), (0, -1))
            if step is not None:
                values[k] = PB_RESOLVED[(v, step)]
    return values

GUARD_TEXT = {
    "H1": ({"hu": "H1: a validator 1.0.0 TRIPOD+AI-számlálása nem megbízható (a tétel címében álló „Missing” szót is "
                  "válasznak veszi); a teljességet a munkapad számolja a saját JSON-jából.",
            "en": "H1: validator 1.0.0 miscounts TRIPOD+AI completeness (it reads 'Missing' in an item title as an "
                  "answer); completeness is counted by the workbench from its own JSON."}, "completeness_recomputed"),
    "H2": ({"hu": "H2: a validator 1.0.0 PROBAST+AI-számlálásában a két menet egymást teljesíti, és a bizonyíték-"
                  "szövegben álló „no” is válasznak számít; a teljességet a munkapad számolja, menetenként.",
            "en": "H2: in validator 1.0.0 the two PROBAST+AI passes satisfy each other and a 'no' in evidence text "
                  "counts as an answer; completeness is counted by the workbench, per pass."}, "completeness_recomputed"),
    "H3": ({"hu": "H3: a validator 1.0.0 GRADE-rollupja a publikációs torzítás „Suspected/Strongly suspected” "
                  "értékénél sosem minősít le — a bizonyossága itt nem megbízható. A „Suspected” feloldatlan, amíg "
                  "nem döntesz 0 vagy −1 között indoklással; a bizonyosságot a motor számolja.",
            "en": "H3: the validator 1.0.0 GRADE roll-up never downgrades publication bias 'Suspected/Strongly "
                  "suspected' — its certainty is unreliable here. 'Suspected' stays unresolved until you choose 0 or "
                  "−1 with a reason; certainty comes from the engine."}, "rollup_unreliable"),
    "H4": ({"hu": "H4: a „Részben igen” kanonikusan ment át („Partial yes”, nem „PY”); a validator 1.0.0 besorolása a "
                  "„weakness” konvenció szerinti, kivéve, hogy a 8. tétel „Részben igen”-jét teljesültnek veszi (a "
                  "projekt konvenciója „meets”, KB AMSTAR2-00).",
            "en": "H4: 'Partial yes' was sent canonically ('Partial yes', never 'PY'); the validator 1.0.0 rating "
                  "follows the 'weakness' convention, except that it counts a 'Partial yes' on item 8 as met (the "
                  "project convention is 'meets', KB AMSTAR2-00)."},
           "convention_weakness"),
}
GUARD_TEXT.update({
    "H12": ({"hu": "H12: a validator 1.0.0 polaritás-címkéje ezeknél a tételeknél eltér a publikált eszköztől (és a "
                   "motor definíciójától): %s — ezért az érintett domének (%s) és az összítélet validator-ítélete "
                   "nem megbízható; a motor ítélete számít.",
             "en": "H12: validator 1.0.0 tags these items with a polarity that differs from the published tool (and "
                   "the engine definition): %s — so its verdict for the affected domains (%s) and overall is "
                   "unreliable; the engine verdict counts."}, "polarity_differs"),
    "H13": ({"hu": "H13: a validator 1.0.0 régi tételszámozást használ, a motor a publikáltat: ugyanez az azonosító "
                   "ott MÁS kérdés (%s). Ezek a válaszok nem mentek át, így a validator teljessége és ítélete nem "
                   "vethető össze a motoréval.",
             "en": "H13: validator 1.0.0 uses an old item numbering, the engine the published one: the same id is a "
                   "DIFFERENT question there (%s). These answers were not sent, so the validator's completeness and "
                   "verdict are not comparable with the engine's."}, "numbering_differs"),
})
# H12: a validator 1.0.0 polaritás-címkéi, amelyek eltérnek a publikált eszköztől (a motor definíciója szerint);
# a router↔reverse eltérés is az (ROBINS-I 1.3 és 2.1: a validatornál „reverse”, a publikált eszközben elágazó kérdés)
POLARITY_DIFFERS = {"quadas2": ("1.2", "1.3"), "robins-e": ("2.3", "5.2", "6.2"), "robins-i": ("1.3", "2.1", "6.3")}
_POLARITY_WORDS = ("normal", "reverse", "router", "none")


def polarity_differs(tool, instrument=None):
    """A H12-tételek: a fenti tábla ÉS a motor eszköz-definíciójának validator_differences-bejegyzései, ahol a
    validator és a motor polaritása eltér (így a definíció bővülése is érvényesül)."""
    out = list(POLARITY_DIFFERS.get(tool, ()))
    for d in (instrument or {}).get("validator_differences") or ():
        if not isinstance(d, dict):
            continue
        a, b = d.get("validator"), d.get("here")
        if a in _POLARITY_WORDS and b in _POLARITY_WORDS and a != b and isinstance(d.get("item"), str) \
                and d["item"] not in out:
            out.append(d["item"])
    return tuple(out)
# melyik őr melyik eszközt érinti (status().tools[*].guards)
GUARD_TOOLS = (("H1", ("tripod-ai",)), ("H2", ("probast-ai",)), ("H3", ("grade",)), ("H4", ("amstar2",)),
               ("H12", tuple(sorted(POLARITY_DIFFERS))), ("H13", ("quips", "robins-i")))


def guard_order(ids):
    """Az őr-azonosítók természetes sorrendben (H1, H2, …, H12, H13 — nem betűrendben)."""
    def key(gid):
        num = gid[1:] if gid[:1] == "H" else ""
        return (0, int(num), gid) if num.isdigit() else (1, 0, gid)
    return sorted(ids, key=key)
VERY_LARGE_GUARD = ({"hu": "A validator 1.0.0 a „nagyon nagy hatást” csak +1-nek számolja (a GRADE szerint +2 is "
                           "lehet); a bizonyossága itt nem megbízható, a motor számol.",
                     "en": "validator 1.0.0 counts a 'very large effect' as +1 only (GRADE allows +2); its certainty "
                           "is unreliable here, the engine computes it."}, "rollup_unreliable")
PB2_GUARD = ({"hu": "PB2: a publikációs torzítás −2-es leminősítése (indokolt emberi döntés) a validator %s szótárában "
                    "nem fejezhető ki — a plugin −1-gyel számol, a bizonyossága itt nem vethető össze a motoréval; a "
                    "bizonyosságot a motor számolja." % FIXED_VERSION,
              "en": "PB2: the −2 publication-bias downgrade (a reasoned human decision) cannot be expressed in the "
                    "validator %s vocabulary — the plugin counts −1, so its certainty is not comparable with the "
                    "engine's here; certainty comes from the engine." % FIXED_VERSION}, "rollup_unreliable")
NOS_NOTE = {"hu": "NOS: a validator 1.0.0 a „Részben igen”-t minden tételen 1 csillagnak veszi; a csillagszámot a motor "
                  "is számolja. Hivatalos küszöb nincs.",
            "en": "NOS: validator 1.0.0 scores 'Partial yes' as 1 star on every item; the engine counts stars too. "
                  "There is no official threshold."}
NOS_NOTE_FIXED = {"hu": "NOS: a „Részben igen” csak a kétcsillagos összehasonlíthatósági tételen (C1/C2) ér 1 csillagot "
                        "— a validatornál és a motornál is; a csillagszámot a motor is számolja. Hivatalos küszöb nincs.",
                  "en": "NOS: 'Partial yes' earns 1 star only on the two-star comparability item (C1/C2) — in the "
                        "validator and in the engine alike; the engine counts stars too. There is no official "
                        "threshold."}
# a javított kiadás és a motor TUDATOS szabálybeli eltérései (nem a plugin hibái — dokumentált konvenciók): a teljes
# válaszkombináció-felsorolásnál (2026-10, szk-plugins#5 utáni egyeztetés) csak ezek maradtak.
# C2 (ROBINS-I/-E): ahol a válaszok a köztes és a felső szint között nem döntenek (a korrekciós kérdés „Nincs
# információ”; ROBINS-I 5.4 és 5.5 egyaránt N/VN), a motor konzervatív szabálya a szigorúbb szintet adja, a validator
# a válaszok által kikényszerített minimumot („legalább”). eszköz → (domén, a kiváltó tételek, feltétel)
_NI = ("no_information",)
_NPN = ("no", "probably_no")
RULE_DIFFERENCES_FIXED = {
    "robins-i": (("2", ("2.5",), "ni"), ("4", ("4.6",), "ni"), ("5", ("5.4", "5.5"), "all_no")),
    "robins-e": (("3", ("3.3",), "ni"), ("4", ("4.2",), "ni"), ("5", ("5.3",), "ni")),
}
RULE_DIFFERENCE_NOTE = {
    "hu": "%s %s (%s. domén): a válaszok itt nem döntenek a köztes és a felső szint között — a motor konzervatív "
          "szabálya a szigorúbb szintet adja, a validator %s a válaszok által kikényszerített minimumot („legalább”). "
          "Dokumentált konvenció-eltérés, nem hiba; a doménítélet emberi döntés.",
    "en": "%s %s (domain %s): the answers do not decide between the middle and the top tier here — the engine's "
          "conservative rule takes the stricter tier, validator %s reports the tier the answers force ('at least'). A "
          "documented convention difference, not a bug; the domain judgement is a human decision."}
# C1 (RoB 2, ROBINS-I/-E): „Nem alkalmazható” olyan tételen, amelyet az útválasztás kérdez — a motor „Nincs
# információ”-ként számolja és ítéletet javasol (routing_conflicts), a validator a domént INCOMPLETE-nek jelzi
NA_ASKED_NOTE = {
    "hu": "%s: „Nem alkalmazható” az útválasztás szerint kérdezett %s tételen — a motor „Nincs információ”-ként "
          "számolja (konzervatív; routing_conflicts), a validator %s a domént hiányosnak (INCOMPLETE) jelzi. "
          "Dokumentált konvenció-eltérés; válaszold meg a tételt.",
    "en": "%s: 'Not applicable' at %s although the routing asks it — the engine counts it as 'No information' "
          "(conservative; routing_conflicts), validator %s marks the domain INCOMPLETE. A documented convention "
          "difference; answer the question."}
NA_ASKED_TOOLS = ("rob2", "robins-i", "robins-e")


def _evaluate(cond, value_of):
    """Az eszköz-definíció ask_if-feltételének háromértékű kiértékelése (= metaelemzes.instruments.evaluate)."""
    if cond is None or cond == {}:
        return True
    if not isinstance(cond, dict):
        return None
    if "always" in cond:
        return bool(cond["always"])
    if "item" in cond:
        v = value_of(cond["item"])
        return None if v is None else v in (cond.get("in") or ())
    if "all" in cond:
        res = [_evaluate(c, value_of) for c in cond.get("all") or ()]
        return False if False in res else (None if None in res else True)
    if "any" in cond:
        res = [_evaluate(c, value_of) for c in cond.get("any") or ()]
        return True if True in res else (None if None in res else False)
    if "not" in cond:
        r = _evaluate(cond["not"], value_of)
        return None if r is None else not r
    return None


class _Route(object):
    """Az eszköz-definíció útválasztása egy értékelésre (a motor _Routing-jának megfelelője): kérdezik-e a tételt
    (ask_if), és a nem kérdezett tétel értéke 'not_applicable'."""

    def __init__(self, values, instrument, scope):
        self.values = values
        self.items = {it.get("key") or it.get("id"): it for it in (instrument or {}).get("items") or ()
                      if isinstance(it, dict) and (not it.get("scopes") or scope in it["scopes"])}
        self._asked = {}

    def asked(self, k):
        if k not in self.items:
            return None
        if k not in self._asked:
            self._asked[k] = None
            self._asked[k] = _evaluate(self.items[k].get("ask_if"), self.value)
        return self._asked[k]

    def value(self, k):
        if k not in self.items:
            return None
        return "not_applicable" if self.asked(k) is False else self.values.get(k)


def na_where_asked(values, instrument, scope):
    """A hatókör azon tételei, amelyekre „Nem alkalmazható” a válasz, bár az útválasztás kérdezi őket (és nem „ha
    alkalmazható” tételek). A nem kérdezett tétel értéke 'not_applicable' (mint a motorban)."""
    r = _Route(values, instrument, scope)
    return [k for k, it in r.items.items() if values.get(k) == "not_applicable" and not it.get("if_applicable")
            and r.asked(k) is True]


def scoped_values(values, instrument, scope):
    """A validatornak szánt értékek: ha a motor kulcsa eltér a validator azonosítójától (validator_id — RoB 2
    betartási 2a.1–2a.6 → 2.1–2.6), csak a hatókör tételei, a validator azonosítóján; különben változatlanul."""
    items = [it for it in (instrument or {}).get("items") or () if isinstance(it, dict)]
    if not any(it.get("validator_id") for it in items):
        return values
    out = {}
    for it in items:
        if it.get("scopes") and scope not in it["scopes"]:
            continue
        k = it.get("key") or it.get("id")
        if k in values:
            out[it.get("validator_id") or it.get("id")] = values[k]
    return out
AMSTAR_NA_NOTE = {"hu": "A „Nem alkalmazható” AMSTAR 2-választ a validator 1.0.0 nem ismeri (hibának számolná), ezért "
                        "üresen ment át: a validator besorolása ideiglenes.",
                  "en": "validator 1.0.0 has no 'Not applicable' answer for AMSTAR 2 (it would count it as a flaw), so "
                        "it was left blank: the validator rating is provisional."}
NOT_OFFICIAL = {"hu": "Ez a validator implikált ítélete (konzervatív szabály) — NEM a hivatalos folyamatábra eredménye; "
                      "az ítélet a Tiéd.",
                "en": "This is the validator's implied judgement (conservative rule) — NOT the official flowchart "
                      "result; the judgement is yours."}

_VERIFY_RE = re.compile(r"^\s*(\d+)/(\d+) answered", re.M)
_UNANSWERED_RE = re.compile(r"UNANSWERED \((\d+)\):\s*(.*)$", re.M)
# 2.0.0: a tételnél nem választható (INVALID) és a szótáron kívüli (UNRECOGNISED) válaszok; az elemek „; ”-vel
# elválasztva, mindegyik „<id> '<válasz>'” alakkal kezdődik
_INVALID_RE = re.compile(r"(?:INVALID|UNRECOGNISED) \((\d+)\):\s*(.*)$", re.M)
_INVALID_ID_RE = re.compile(r"(?:^|; )([0-9A-Za-z][0-9A-Za-z./\-]*) '")
_LEGACY_RE = re.compile(r"LEGACY NUMBERING")
# a domén-sor: „Domain 2 (…): …”, 2.0.0-tól „Phase 3 (…): …” is (ROBIS) és INCOMPLETE ítélet
_DOMAIN_RE = re.compile(r"^\s*(Domain|Phase|Section|Item group) (\S+) \((.*)\): "
                        r"(HIGH / SERIOUS|SOME CONCERNS / UNCLEAR|LOW|INCOMPLETE)\s+—\s+(.*)$")
_ROUTERS_RE = re.compile(r"routing questions answered, not scored:\s*(.*)$")
_OVERALL_RE = re.compile(r"Implied overall:\s*(LOW|SOME CONCERNS|HIGH / SERIOUS|INCOMPLETE)")
_AT_LEAST_HIGH_RE = re.compile(r"Already at least HIGH / SERIOUS")
_NOT_FINAL_RE = re.compile(r"\(exit 1: this verdict is not final")
# az indoklás részei (a sorrend és az elválasztó a validator-verziótól függ, ezért mintánként keresünk)
_IDLIST = r"([0-9A-Za-z][0-9A-Za-z.]*(?:, [0-9A-Za-z][0-9A-Za-z.]*)*)"
_WHY_REVERSE_RE = re.compile(r"'Yes' or 'Probably yes' at " + _IDLIST + r" \(reverse-worded\)")
_WHY_NORMAL_RE = re.compile(r"'No' or 'Probably no' at " + _IDLIST)
_WHY_UNKNOWN_RE = re.compile(r"no information at " + _IDLIST)
_WHY_PARTLY_RE = re.compile(r"'Partly' at " + _IDLIST)
_WHY_MISSING_RE = re.compile(r"unanswered: " + _IDLIST)
_WHY_INVALID_RE = re.compile(r"answer not offered by the item at " + _IDLIST)
_AM_CRIT_RE = re.compile(r"Critical flaws \((\d+)\):\s*(.*)$", re.M)
_AM_WEAK_RE = re.compile(r"Non-critical weaknesses \((\d+)\):\s*(.*)$", re.M)
_AM_NA_RE = re.compile(r"No meta-analysis conducted \(N/A[^)]*\):\s*(.*)$", re.M)
_AM_RATING_RE = re.compile(r"OVERALL CONFIDENCE IN THE RESULTS:\s*(HIGH|MODERATE|CRITICALLY LOW|LOW)")
_AM_UNANS_RE = re.compile(r"(\d+) item\(s\) unanswered")
_NOT_SCORED_RE = re.compile(r"^\s*Not scored", re.M)
_GR_START_RE = re.compile(r"Start:\s*(HIGH|LOW)")
_GR_CERT_RE = re.compile(r"CERTAINTY:\s*(HIGH|MODERATE|VERY LOW|LOW|INCOMPLETE|UNRESOLVED)")
_GR_RANGE_RE = re.compile(r"CERTAINTY: UNRESOLVED — (HIGH|MODERATE|VERY LOW|LOW) without a publication-bias downgrade, "
                          r"(HIGH|MODERATE|VERY LOW|LOW) with one")
_NOS_TOTAL_RE = re.compile(r"TOTAL:\s*(\d+)/(\d+) stars")
_NOS_DOM_RE = re.compile(r"Stars by domain:\s*(.*)$", re.M)
_SKEL_HEAD_RE = re.compile(r"^##\s+(?:.*?\b(Domain|Checklist|domain|Phase|Section|Item group))\s+(\S+)\s+[—–-]")
_SKEL_PASS_RE = re.compile(r"^###\s+.*\((development|evaluation)\)", re.I)
_SKEL_ROW_RE = re.compile(r"^\|\s*([0-9A-Za-z][0-9A-Za-z.\-]*)\s*\|")
_SKEL_HEADER_CELLS = ("#", "sq", "item")
# 2.0.0: a váz záró megjegyzése a számozást is megnevezi („numbering: robins-i 2016 Table A”) — a jelölő nélküli
# fájlt a plugin régi (1.x) számozásúnak nézheti, és nem pontozza
_SKEL_NUMBERING_RE = re.compile(r"numbering:\s*([a-z0-9][a-z0-9-]*\b[^;>\n]*?)\s*(?:;|-->|$)", re.M)
LEVEL = {"HIGH / SERIOUS": "high", "SOME CONCERNS / UNCLEAR": "some", "SOME CONCERNS": "some", "LOW": "low"}
# a fő-csoport szava → kulcs: a „Domain N” a szám maga; a 2.0.0 ROBIS „Phase 3”-ja külön csoport (nem a 3. domén)
_GROUP_KEY = {"Domain": "", "domain": "", "Checklist": "", "Phase": "P", "Section": "S", "Item group": "I"}


def group_key(word, num):
    return "%s%s" % (_GROUP_KEY.get(word, ""), num)


def _i18n(hu, en):
    return {"hu": hu, "en": en}


def _ids(text):
    return [x.strip() for x in str(text or "").split(",") if x.strip() and x.strip().lower() != "none"]


def check_doc(doc):
    """A bemenő értékelés formai ellenőrzése (az adapter csak ennyit vár el; a teljes séma a motoré).
    Hibánál ValueError — az üzenet mezőt nevez, értéket nem idéz (T10)."""
    if not isinstance(doc, dict):
        raise ValueError("Az értékelés JSON-objektum legyen (szk.appraisal/v1).")
    if doc.get("schema") not in (None, APPRAISAL_SCHEMA):
        raise ValueError("Az értékelés sémája szk.appraisal/v1 legyen.")
    tool = doc.get("tool")
    if not isinstance(tool, str) or not TOOL_RE.match(tool):
        raise ValueError("Hiányzó vagy érvénytelen eszköz (tool).")
    if tool not in TOOLS:
        raise ValueError("Ezt az eszközt a validator nem ismeri: %s." % tool)
    answers = doc.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("Az answers mező objektum legyen.")
    if len(answers) > MAX_ANSWERS:
        raise ValueError("Túl sok válasz (legfeljebb %d)." % MAX_ANSWERS)
    for key, ans in answers.items():
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise ValueError("Érvénytelen válaszkulcs az answers mezőben.")
        if ans is not None and not isinstance(ans, dict):
            raise ValueError("Az answers értékei objektumok legyenek ({value, …}).")
        val = (ans or {}).get("value")
        if val is not None and not isinstance(val, str):
            raise ValueError("A válaszérték (value) szöveg vagy null legyen.")
    scope = doc.get("scope")
    if scope is not None and (not isinstance(scope, str) or not SCOPE_RE.match(scope)):
        raise ValueError("Érvénytelen hatókör (scope).")
    return tool


def _values(doc):
    return {k: (v or {}).get("value") for k, v in (doc.get("answers") or {}).items()}


def _scope(doc, tool, instrument):
    sc = doc.get("scope")
    if isinstance(sc, str) and sc:
        return sc
    if isinstance(instrument, dict):
        for s in instrument.get("scopes") or []:
            if isinstance(s, dict) and s.get("default") and isinstance(s.get("id"), str):
                return s["id"]
    return DEFAULT_SCOPE.get(tool, "all")


def _tiers(instrument):
    roll = instrument.get("rollup") if isinstance(instrument, dict) else None
    tiers = roll.get("tiers") if isinstance(roll, dict) else None
    return tiers if isinstance(tiers, dict) else {}


def renumbered(instrument):
    """H13: a motor definíciójának azon azonosítói, amelyek a validator 1.0.0-ban MÁS kérdést jelölnek
    (``numbering_changed.items``), illetve — ha a validator azonosítói teljesen mások (``validator_ids``, pl. QUIPS) —
    a validator saját azonosítói. → (ids frozenset, a motor szerinti változás-lista)"""
    if not isinstance(instrument, dict):
        return frozenset(), []
    nc = instrument.get("numbering_changed")
    changed = [str(x) for x in (nc.get("items") or ()) if isinstance(x, str)] if isinstance(nc, dict) else []
    vids = [str(x) for x in instrument.get("validator_ids") or () if isinstance(x, str)]
    own = {str(it.get("id")) for it in instrument.get("items") or () if isinstance(it, dict)}
    if vids and not (own & set(vids)):
        return frozenset(vids), sorted(set(vids))
    return frozenset(changed), changed


def guard(gid, *args):
    msg, effect = GUARD_TEXT[gid]
    if args:
        msg = {k: v % args for k, v in msg.items()}
    return {"id": gid, "message": msg, "effect": effect}


def minimal_json_doc(doc):
    """A json-módú plugin bemenete: csak válaszértékek és ítéletek — idézet, indoklás, megjegyzés soha."""
    out = {"schema": APPRAISAL_SCHEMA, "tool": doc.get("tool"), "scope": doc.get("scope"),
           "target": {k: v for k, v in (doc.get("target") or {}).items() if isinstance(v, (str, type(None)))},
           "assessor": doc.get("assessor") or "x", "status": doc.get("status") or "draft",
           "origin": doc.get("origin") or "human",
           "answers": {}}
    for k, v in (doc.get("answers") or {}).items():
        a = {"value": (v or {}).get("value")}
        res = (v or {}).get("resolution")
        if isinstance(res, dict) and res.get("step") in (0, -1, -2):
            a["resolution"] = {"step": res["step"], "rationale": "x" if res.get("rationale") else ""}
        out["answers"][k] = a
    for coll in ("domain_judgements", "applicability"):
        if isinstance(doc.get(coll), list):
            out[coll] = [{"domain": d.get("domain"), "pass": d.get("pass"), "judgement": d.get("judgement")}
                         for d in doc[coll] if isinstance(d, dict)]
    if isinstance(doc.get("overall"), dict):
        out["overall"] = {"judgement": doc["overall"].get("judgement")}
    return out


# ---------------------------------------------------------------------------- bridge: Markdown és olvasás
def parse_skeleton(text, checklist=False):
    """A plugin --skeleton kimenete → [{id, pass, domain}] (a validator saját hely-listája). A fejléc- és
    elválasztósorokat, valamint a táblán kívüli sorokat kihagyja."""
    slots, seen = [], set()
    domain, pas = None, None
    for line in (text or "").splitlines():
        m = _SKEL_PASS_RE.match(line)
        if m:
            pas = m.group(1).lower()
            continue
        m = _SKEL_HEAD_RE.match(line)
        if m:
            domain = group_key(m.group(1), m.group(2))
            continue
        if not line.startswith("|") or line.startswith("|---"):
            continue
        m = _SKEL_ROW_RE.match(line)
        if not m:
            continue
        sid = m.group(1)
        if sid.lower() in _SKEL_HEADER_CELLS:
            continue
        key = (pas, sid)
        if key in seen:
            continue
        seen.add(key)
        slots.append({"id": sid, "pass": pas if checklist else None,
                      "domain": domain if (domain and not checklist) else sid.split(".")[0]})
    return slots


def skeleton_numbering(text):
    """A váz számozás-jelölője (2.0.0: „numbering: robins-i 2016 Table A”), vagy None (1.0.0-nál nincs ilyen)."""
    m = _SKEL_NUMBERING_RE.search(text or "")
    return "numbering: %s" % m.group(1).strip() if m else None


def slot_key(slot):
    return "%s/%s" % (slot["pass"], slot["id"]) if slot.get("pass") else slot["id"]


APPLICABILITY_TOOLS = ("quadas2",)


def applicability_of(doc, tool):
    """A QUADAS-2 doménenkénti alkalmazhatósági ítéletei a dokumentumból ({domén: „Low” | „High” | „Unclear”}): a
    validator 2.0.0 a 3 domén ítéletét a rekordból olvassa, és nélküle a --verify / --rollup nem végleges."""
    if tool not in APPLICABILITY_TOOLS:
        return {}
    out = {}
    for a in doc.get("applicability") or ():
        if isinstance(a, dict) and a.get("judgement") in ("low", "high", "unclear"):
            out[str(a.get("domain"))] = a["judgement"].capitalize()
    return out


def bridge_markdown(tool, slots, values, fixed=False, numbering=None, applicability=None):
    """A validator sablonjával egyező Markdown — (szöveg, {kulcs: token}, invalid[], unexpressible[]).
    A kérdés-oszlopban „—”, a bizonyíték-oszlopban „[E<n>]”: válaszszótárbeli szó csak az ítélet-cellába kerül.
    ``fixed``: a javított kiadás szótára; ``numbering``: a váz számozás-jelölője, a fájl második sorába (a
    jelölővel a plugin tudja, hogy a fájl a publikált számozást használja); ``applicability``: {domén: ítélet} — a
    váz „**Domain N applicability:**” soraiként (QUADAS-2)."""
    tokens = answer_tokens(tool, fixed)
    unexpr = unexpressible(tool, fixed)
    sent, invalid, skipped = {}, [], []
    for s in slots:
        key = slot_key(s)
        val = values.get(key)
        if val is None:
            continue
        if val in unexpr:
            skipped.append(key)
            continue
        tok = tokens.get(val)
        if tok is None:
            invalid.append({"key": key, "item": s["id"], "pass": s.get("pass"), "reason": "unknown_value"})
            continue
        sent[key] = tok
    lines = ["# %s — MA-munkapad bridge" % tool, ""]
    if numbering:
        lines += ["<!-- %s -->" % numbering, ""]
    n = [0]

    def row(s, tripod=False):
        key = slot_key(s)
        tok = sent.get(key, "")
        n[0] += 1
        ev = "[E%d]" % n[0] if tok else ""
        if tripod:
            return "| %s | — | — | %s | %s |" % (s["id"], tok, ev)
        return "| %s | — | %s | %s |" % (s["id"], tok, ev)

    if tool == "probast-ai":
        for pas, head in (("development", "Quality (development)"), ("evaluation", "Risk of bias (evaluation)")):
            ps = [s for s in slots if s.get("pass") == pas]
            if not ps:
                continue
            lines += ["### %s — %d signalling questions" % (head, len(ps)), "",
                      "| SQ | Question | Answer | Evidence (quote or section) |", "|---|---|---|---|"]
            lines += [row(s) for s in ps]
            lines.append("")
    elif tool == "tripod-ai":
        lines += ["### TRIPOD+AI reporting check — %d items in scope" % len(slots), "",
                  "| Item | D/E | What it asks | Status | Where / what to add |", "|---|---|---|---|---|"]
        lines += [row(s, tripod=True) for s in slots]
        lines.append("")
    else:
        domains = []
        for s in slots:
            if s["domain"] not in domains:
                domains.append(s["domain"])
        for d in domains:
            lines += ["## Domain %s — —" % d, "",
                      "| # | Signalling question | Answer | Evidence (quote or section) |", "|---|---|---|---|"]
            lines += [row(s) for s in slots if s["domain"] == d]
            lines.append("")
            if fixed and (applicability or {}).get(d):
                lines += ["**Domain %s applicability:** %s" % (d, applicability[d]), ""]
    return "\n".join(lines) + "\n", sent, invalid, skipped


def parse_verify(text):
    """'N/M answered' + UNANSWERED-lista → {answered, expected, unanswered[]} vagy None (nem értelmezhető). A
    javított kiadás INVALID / UNRECOGNISED sorainak tételei az ``invalid`` listába kerülnek (ha van ilyen)."""
    m = _VERIFY_RE.search(text or "")
    if not m:
        return None
    um = _UNANSWERED_RE.search(text or "")
    out = {"answered": int(m.group(1)), "expected": int(m.group(2)),
           "unanswered": _ids(um.group(2)) if um else []}
    invalid = []
    for im in _INVALID_RE.finditer(text or ""):
        for iid in _INVALID_ID_RE.findall(im.group(2)):
            if iid not in invalid:
                invalid.append(iid)
    if invalid:
        out["invalid"] = invalid
    return out


def _why_ids(rx, why):
    out = []
    for m in rx.finditer(why or ""):
        for iid in _ids(m.group(1)):
            if iid not in out:
                out.append(iid)
    return out


def parse_rollup_signalling(text):
    """A konzervatív (jelző-kérdéses) rollup sorai → (domének, összítélet-szint, sorok).

    A domén indoklásából: ``forced_by`` — a problémát jelző tételek mindkét polaritással (az 1.0.0 csak a „'No' or
    'Probably no' at …” alakot ismerte; a 2.0.0 a fordított polaritásúakat „'Yes' or 'Probably yes' at … (reverse-
    worded)” alakban írja, és a középső szintű — „rules out low” — jelzéseket is így); ``unknown_at`` („no information
    at”), ``partial_at`` (QUIPS „'Partly' at”). Az INCOMPLETE domén (2.0.0) szint nélkül, ``incomplete`` jelzővel jön
    (``validator_missing`` / ``validator_invalid``: a plugin szerint üres / nem választható tételek)."""
    domains, overall, lines = [], None, []
    for line in (text or "").splitlines():
        if line.strip():
            lines.append(line.strip())
        m = _DOMAIN_RE.match(line)
        if m:
            verdict, why = m.group(4), m.group(5)
            reverse = _why_ids(_WHY_REVERSE_RE, why)
            forced = reverse + [i for i in _why_ids(_WHY_NORMAL_RE, why) if i not in reverse]
            d = {"domain": group_key(m.group(1), m.group(2)), "level": LEVEL.get(verdict), "forced_by": forced,
                 "unknown_at": _why_ids(_WHY_UNKNOWN_RE, why), "routers": []}
            partly = _why_ids(_WHY_PARTLY_RE, why)
            if partly:
                d["partial_at"] = partly
            if verdict == "INCOMPLETE":
                d["incomplete"] = True
                d["validator_missing"] = _why_ids(_WHY_MISSING_RE, why)
                bad = _why_ids(_WHY_INVALID_RE, why)
                if bad:
                    d["validator_invalid"] = bad
            domains.append(d)
            continue
        m = _ROUTERS_RE.search(line)
        if m and domains:
            domains[-1]["routers"] = _ids(m.group(1))
            continue
        m = _OVERALL_RE.search(line)
        if m:
            overall = LEVEL.get(m.group(1), None)
    return domains, overall, lines[:60]


def rollup_incomplete(text):
    """A 2.0.0 összítélete INCOMPLETE (van üres vagy nem választható hely) → (True, legalább-szint | None)."""
    m = _OVERALL_RE.search(text or "")
    if not m or m.group(1) != "INCOMPLETE":
        return False, None
    return True, ("high" if _AT_LEAST_HIGH_RE.search(text or "") else None)


def rollup_not_final(text):
    """A 2.0.0 --rollup maga mondja, hogy az ítélete nem végleges (1-es kilépési kód + jelzősor)."""
    return bool(_NOT_FINAL_RE.search(text or ""))


def parse_rollup_amstar2(text):
    rating = _AM_RATING_RE.search(text or "")
    crit = _AM_CRIT_RE.search(text or "")
    weak = _AM_WEAK_RE.search(text or "")
    unans = _AM_UNANS_RE.search(text or "")
    if not rating:
        return None
    out = {"rating": rating.group(1).lower().replace(" ", "_"),
           "critical_flaws": _ids(crit.group(2)) if crit else [],
           "weaknesses": _ids(weak.group(2)) if weak else [],
           "provisional": bool(unans) or bool(_NOT_SCORED_RE.search(text or ""))}
    na = _AM_NA_RE.search(text or "")
    if na:
        out["not_applicable"] = _ids(na.group(1))
    return out


def parse_rollup_grade(text):
    """→ {certainty, start} vagy None. A 2.0.0 ``CERTAINTY: INCOMPLETE`` (üres vagy nem választható hely) és
    ``CERTAINTY: UNRESOLVED`` (gyanított publikációs torzítás, döntés nélkül) bizonyosság nélkül jön; az utóbbinál a
    két lehetséges szint a ``range``-ben (leminősítés nélkül, leminősítéssel)."""
    cert = _GR_CERT_RE.search(text or "")
    start = _GR_START_RE.search(text or "")
    if not cert:
        return None
    word = cert.group(1)
    out = {"certainty": None if word in ("INCOMPLETE", "UNRESOLVED") else word.lower().replace(" ", "_"),
           "start": start.group(1).lower() if start else None}
    if word == "INCOMPLETE":
        out["incomplete"] = True
    elif word == "UNRESOLVED":
        out["unresolved"] = ["publication_bias"]
        rng = _GR_RANGE_RE.search(text or "")
        if rng:
            out["range"] = [rng.group(1).lower().replace(" ", "_"), rng.group(2).lower().replace(" ", "_")]
    return out


def parse_rollup_nos(text):
    tot = _NOS_TOTAL_RE.search(text or "")
    if not tot:
        return None
    dom = _NOS_DOM_RE.search(text or "")
    out = {"total": int(tot.group(1)), "max": int(tot.group(2)),
           "by_domain_text": dom.group(1).strip() if dom else None}
    if _AM_UNANS_RE.search(text or "") or _NOT_SCORED_RE.search(text or ""):
        out["provisional"] = True
    return out


def _text_lines(text, limit=60):
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()][:limit]


# ---------------------------------------------------------------------------- adapter
class ValidatorAdapter(Adapter):
    plugin = PLUGIN
    default_timeout = TIMEOUT
    accepted_returncodes = (0, 1)        # --verify: 1 = hiányos (nem hiba); az argparse-hiba 2

    def __init__(self, caps=None):
        super().__init__(caps)
        self._skel_cache = {}
        self._skel_lock = threading.Lock()

    @staticmethod
    def _json_cmd(cap, name):
        if cap.get("state") != "ok":
            return False
        return any(c.get("name") == name and c.get("available") and c.get("mode") == "json"
                   for c in cap.get("commands") or [])

    def mode(self, tool, cap=None):
        """'json' | 'bridge' | None — eszközönként (a deklarált json-parancs szerint)."""
        cap = self.detect() if cap is None else cap
        if tool not in TOOLS or cap.get("state") not in USABLE_STATES:
            return None
        script = TOOLS[tool][0]
        names = ("checklist.verify",) if script == "checklist.py" else ("appraise.rollup", "appraise.verify")
        if any(self._json_cmd(cap, n) for n in names) and (cap.get("scripts") or {}).get(script):
            return "json"
        if not (cap.get("scripts") or {}).get(script):
            return None
        return "bridge"

    @staticmethod
    def active_guards(cap):
        return set(cap.get("guards") or [])

    def status(self):
        """{state, version, mode, guards, tools: {eszköz: {mode, feature, guards[]}}, remedy} — a felületnek."""
        cap = self.detect()
        active = self.active_guards(cap)
        per_tool = {}
        for tool, (_script, _vt, feat) in sorted(TOOLS.items()):
            g = [gid for gid, tools in GUARD_TOOLS if tool in tools and gid in active]
            per_tool[tool] = {"mode": self.mode(tool, cap), "feature": feat, "guards": g}
        remedy = None
        state = cap.get("state") or "absent"
        if state not in USABLE_STATES:
            remedy = cap.get("todo") or _i18n("A validator plugin nem érhető el.", "The validator plugin is unavailable.")
        elif state == "legacy" and not active and fixed_release(cap):
            # a javított kiadás (2.0.0): bridge-mód (nincs --capabilities), de egyik 5.0-s őr sem kell
            remedy = _i18n("A validator %s bridge-módban fut (a --capabilities kézfogás hiányzik). Ennél a verziónál "
                           "az 5.0 őrei (H1–H4, H12, H13) nem kellenek: a hibák a pluginban javítva. A közvetlen "
                           "JSON-kapcsolathoz a kézfogás kell." % cap.get("version"),
                           "validator %s runs in bridge mode (no --capabilities handshake). This version needs none "
                           "of the 5.0 guards (H1–H4, H12, H13): the bugs are fixed in the plugin. The direct JSON "
                           "link needs the handshake." % cap.get("version"))
        elif state == "legacy":
            ids = ", ".join(guard_order(active)) or "—"
            remedy = _i18n("A validator %s régi (bridge) módban fut, az őrökkel (%s). A közvetlen JSON-kapcsolathoz a "
                           "validator 1.1 (V1) kell; addig is az őrök kijavítják az ismert hibáit, vagy megjelölik, "
                           "ahol az eredménye nem megbízható — ott a motor ítélete számít." % (cap.get("version") or "?", ids),
                           "validator %s runs in legacy (bridge) mode with the guards (%s). The direct JSON link needs "
                           "validator 1.1 (V1); meanwhile the guards correct its known bugs, or flag where its result "
                           "is unreliable — there the engine verdict counts." % (cap.get("version") or "?", ids))
        return {"plugin": PLUGIN, "state": state, "version": cap.get("version"), "mode": cap.get("mode"),
                "guards": guard_order(active), "tools": per_tool, "remedy": remedy}

    # -- futtatás
    def _call(self, script, args, parse="text", timeout=None, cwd=None):
        return self.run(args, timeout=timeout or TIMEOUT, script=script, parse=parse, cwd=cwd)

    def _skeleton(self, cap, tool, scope):
        script, vtool, _f = TOOLS[tool]
        path = (cap.get("scripts") or {}).get(script)
        try:
            st = os.stat(path)
            key = (path, st.st_mtime_ns, st.st_size, tool, scope)
        except (OSError, TypeError):
            key = None
        if key is not None:
            with self._skel_lock:
                if key in self._skel_cache:
                    slots, numbering = self._skel_cache[key]
                    return {"ok": True, "slots": slots, "numbering": numbering}
        res = self._call(script, ["--skeleton", vtool, "--scope", scope])
        if not res.get("ok"):
            return res
        slots = parse_skeleton(res["data"], checklist=(script == "checklist.py"))
        if not slots:
            return error("PLUGIN_FAILED", "A validator --skeleton kimenete nem értelmezhető (nincs tétel).",
                         {"plugin": PLUGIN, "tool": tool})
        numbering = skeleton_numbering(res["data"])
        if key is not None:
            with self._skel_lock:
                self._skel_cache[key] = (slots, numbering)
        return {"ok": True, "slots": slots, "numbering": numbering}

    def check(self, doc, instrument=None, timeout=None):
        """Az értékelés keresztellenőrzése a validatorral → ``{ok: True, data: szk.appraisal-result/v1}`` vagy
        hibaboríték (CAPABILITY_MISSING / PLUGIN_FAILED / TIMEOUT). Formai hibánál ValueError."""
        tool = check_doc(doc)
        cap = self.detect()
        mode = self.mode(tool, cap)
        if mode is None:
            return self._missing(cap)
        scope = _scope(doc, tool, instrument)
        if not SCOPE_RE.match(scope):
            raise ValueError("Érvénytelen hatókör (scope).")
        if TOOLS[tool][0] == "checklist.py" and scope not in CHECKLIST_SCOPES:
            scope = "both"
        # H13: az eltérő számozású tételek válaszai nem mennek át (a validatorban ugyanez az azonosító más kérdés)
        dropped = []
        if "H13" in self.active_guards(cap):
            ids, _changed = renumbered(instrument)
            if ids:
                answers = {}
                for k, v in (doc.get("answers") or {}).items():
                    if k.split("/")[-1] in ids and (v or {}).get("value") is not None:
                        dropped.append(k)
                    else:
                        answers[k] = v
                doc = dict(doc, answers=answers)
        work = tempfile.mkdtemp(prefix="validator-", dir=str(self.caps.tmp_dir()))
        try:
            if mode == "json":
                res = self._check_json(doc, tool, scope, cap, instrument, work, timeout)
            else:
                res = self._check_bridge(doc, tool, scope, cap, instrument, work, timeout)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if res.get("ok"):
            self._numbering_polarity(res["data"], tool, cap, instrument, doc, dropped)
            self._rule_differences(res["data"], tool, cap, doc, instrument, scope)
        return res

    @staticmethod
    def _rule_differences(out, tool, cap, doc, instrument=None, scope=None):
        """A javított kiadás és a motor dokumentált konvenció-eltérései: megjegyzés, ha a kiváltó válasz rögzítve van
        — a validator ítélete itt más konvenciót követ, nem hibás (ezért nem őr). C2: ``RULE_DIFFERENCES_FIXED``
        (ROBINS-I/-E, szigorúbb vs. „legalább”); C1: „Nem alkalmazható” kérdezett tételen (az eszköz-definíció
        ask_if-je szerint)."""
        if not fixed_release(cap):
            return
        values = _values(doc)
        name = {"robins-i": "ROBINS-I", "robins-e": "ROBINS-E", "rob2": "RoB 2"}.get(tool, tool)
        route = _Route(values, instrument, scope or _scope(doc, tool, instrument)) if isinstance(
            instrument, dict) else None
        for domain, items, cond in RULE_DIFFERENCES_FIXED.get(tool) or ():
            # csak a kérdezett tétel számít (a kóbor válasz a nem kérdezett tételen egyik oldalon sem számít)
            got = [route.value(i) if route else values.get(i) for i in items]
            if cond == "ni":
                hit = [i for i, v in zip(items, got) if v in _NI]
            else:
                hit = list(items) if all(v in _NPN for v in got) else []
            if not hit:
                continue
            what = "/".join(hit) + (" NI" if cond == "ni" else " N/PN")
            out.setdefault("notes", []).append({k: v % (name, what, domain, cap.get("version") or FIXED_VERSION)
                                                for k, v in RULE_DIFFERENCE_NOTE.items()})
            out.setdefault("rule_differences", []).append({"item": hit[0], "domain": domain, "kind": "C2"})
        if tool in NA_ASKED_TOOLS and isinstance(instrument, dict):
            for k in na_where_asked(values, instrument, scope or _scope(doc, tool, instrument)):
                it = next((x for x in instrument.get("items") or () if (x.get("key") or x.get("id")) == k), {})
                shown = it.get("official_id") or it.get("id") or k
                out.setdefault("notes", []).append({lang: v % (name, shown, cap.get("version") or FIXED_VERSION)
                                                    for lang, v in NA_ASKED_NOTE.items()})
                out.setdefault("rule_differences", []).append({"item": shown, "domain": str(it.get("domain")),
                                                               "kind": "C1"})

    def _numbering_polarity(self, out, tool, cap, instrument, doc, dropped):
        """H13 és H12 (a módtól független utófeldolgozás): őr-bejegyzés, összevethetőség, megbízhatóság."""
        active = self.active_guards(cap)
        guards = out.setdefault("guards", [])
        ids, changed = renumbered(instrument)
        if "H13" in active and ids:
            guards.append(dict(guard("H13", ", ".join(changed)), items=sorted(dropped)))
            out["comparable"] = False
        pol = polarity_differs(tool, instrument)
        if "H12" in active and pol:
            values = _values(doc)
            hit = [k for k in pol if values.get(k) is not None]
            if hit:
                doms = sorted({k.split(".")[0] for k in hit})
                guards.append(dict(guard("H12", ", ".join(hit), ", ".join(doms)), items=hit, domains=doms))
                out["unreliable_domains"] = doms
                ov = out.get("overall")
                if isinstance(ov, dict):
                    ov["reliable"] = False

    # -- json mód (V1)
    def _check_json(self, doc, tool, scope, cap, instrument, work, timeout):
        script, vtool, _f = TOOLS[tool]
        path = os.path.join(work, "appraisal.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(minimal_json_doc(dict(doc, scope=scope)), fh, ensure_ascii=False, allow_nan=False)
        verb = "--verify" if script == "checklist.py" else "--rollup"
        res = self._call(script, [verb, path, "--tool", vtool, "--scope", scope, "--json"], parse="json",
                         timeout=timeout, cwd=work)
        if not res.get("ok"):
            return res
        out = res["data"]
        if not isinstance(out, dict) or out.get("schema") != RESULT_SCHEMA:
            return error("PLUGIN_FAILED", "A validator kimenete nem szk.appraisal-result/v1.", {"plugin": PLUGIN})
        out = dict(out)
        out.setdefault("tool", tool)
        out.update(mode="json", validator_version=cap.get("version"), scope=out.get("scope") or scope)
        out.setdefault("legacy", False)
        guards = [g for g in out.get("guards") or [] if isinstance(g, dict)]
        active = self.active_guards(cap)
        if ("H1" in active and tool == "tripod-ai") or ("H2" in active and tool == "probast-ai"):
            got = self._skeleton(cap, tool, scope)
            if not got.get("ok"):
                return got
            own = self._own_completeness(tool, got["slots"], _values(doc), {}, fixed_release(cap))
            out["validator_reported"] = {"answered": out.get("answered"), "expected": out.get("expected"),
                                         "trusted": False}
            out.update(own)
            gid = "H1" if tool == "tripod-ai" else "H2"
            guards.append({"id": gid, "message": GUARD_TEXT[gid][0], "effect": GUARD_TEXT[gid][1]})
        if tool == "grade":
            self._grade_guards(out, doc, active, guards, instrument, None, fixed_release(cap))
        if tool == "amstar2" and "H4" in active:
            out.setdefault("conventions", {})["amstar2.partial_yes_critical"] = "weakness"
            guards.append({"id": "H4", "message": GUARD_TEXT["H4"][0], "effect": GUARD_TEXT["H4"][1]})
        out["guards"] = guards
        out["guards_active"] = guard_order(active)        # a telepített verzióra bekapcsolt őrök (üres: egy sem kell)
        return {"ok": True, "data": out}

    # -- bridge mód (1.0.0 és a javított 2.0.0: mindkettő kézfogás nélkül)
    @staticmethod
    def _own_completeness(tool, slots, values, sent, fixed=False):
        """A munkapad saját számlálása (H1/H2-őr): minden hatókörbe eső hely kapott-e a kanonikus szótárból
        értéket — menettel minősített kulccsal."""
        tokens = answer_tokens(tool, fixed)
        unexpr = unexpressible(tool, fixed)
        missing, per_pass = [], {}
        answered = 0
        for s in slots:
            key = slot_key(s)
            val = values.get(key)
            ok = val is not None and (val in tokens or val in unexpr)
            if ok:
                answered += 1
            else:
                missing.append({"item": s["id"], "pass": s.get("pass"), "key": key})
            if s.get("pass"):
                pp = per_pass.setdefault(s["pass"], {"expected": 0, "answered": 0})
                pp["expected"] += 1
                pp["answered"] += 1 if ok else 0
        for pp in per_pass.values():
            pp["complete"] = pp["answered"] == pp["expected"]
            pp["text"] = "%d/%d" % (pp["answered"], pp["expected"])
        expected = len(slots)
        return {"complete": answered == expected, "expected": expected, "answered": answered,
                "completeness_text": "%d/%d" % (answered, expected), "missing": missing,
                "per_pass": per_pass or None, "completeness_source": "workbench"}

    def _grade_guards(self, out, doc, active, guards, instrument, slots, fixed=False):
        """H3 (és az 1.0.0-n a „nagyon nagy hatás”): a validator GRADE-bizonyossága ilyenkor nem megbízható; a
        „suspected” feloldatlan, amíg ember nem dönt indoklással (4. döntés). A javított kiadásnál (``fixed``) a
        „nagyon nagy hatás” +2 a pluginban is; a −2-es publikációs leminősítést (indokolt „strongly suspected”) viszont
        nem fejezi ki — ott PB2."""
        values = _values(doc)
        answers = doc.get("answers") or {}
        pb_keys = [k for k in values if k.split(".")[0] == PUB_BIAS_DOMAIN]
        if slots:
            pb_keys = [slot_key(s) for s in slots if s["domain"] == PUB_BIAS_DOMAIN] or pb_keys
        grade = out.get("grade") if isinstance(out.get("grade"), dict) else {"certainty": None, "unresolved": []}
        grade = dict(grade)
        unresolved = [u for u in grade.get("unresolved") or [] if isinstance(u, str)]
        unreliable = False
        for k in pb_keys:
            v = values.get(k)
            if v not in ("suspected", "strongly_suspected"):
                continue
            if "H3" in active:
                unreliable = True
                if not any(g.get("id") == "H3" for g in guards):
                    guards.append({"id": "H3", "message": GUARD_TEXT["H3"][0], "effect": GUARD_TEXT["H3"][1]})
            res = (answers.get(k) or {}).get("resolution")
            resolved = (isinstance(res, dict) and res.get("step") in (0, -1)
                        and isinstance(res.get("rationale"), str) and res["rationale"].strip())
            if v == "suspected" and not resolved and "publication_bias" not in unresolved:
                unresolved.append("publication_bias")
            if fixed and v == "strongly_suspected" and _resolved_step(answers.get(k), (-2,)) is not None:
                unreliable = True
                if not any(g.get("id") == "PB2" for g in guards):
                    guards.append({"id": "PB2", "message": PB2_GUARD[0], "effect": PB2_GUARD[1]})
        if not fixed and any(v == "very_large" for v in values.values()):
            unreliable = True
            guards.append({"id": "VL", "message": VERY_LARGE_GUARD[0], "effect": VERY_LARGE_GUARD[1]})
        if unreliable or unresolved:
            if grade.get("certainty") is not None:
                grade["validator_certainty"] = grade.get("certainty")
            grade["certainty"] = None
            if unreliable:
                # feloldatlanul nincs mit megbízhatatlannak mondani: a bizonyosság hiányzik, a plugin nem téved
                grade["reliable"] = False
        grade["unresolved"] = unresolved
        out["grade"] = grade

    def _check_bridge(self, doc, tool, scope, cap, instrument, work, timeout):
        script, vtool, _f = TOOLS[tool]
        got = self._skeleton(cap, tool, scope)
        if not got.get("ok"):
            return got
        slots = got["slots"]
        fixed = fixed_release(cap)
        values = scoped_values(bridge_values(doc, tool, fixed), instrument, scope)
        md, sent, invalid, skipped = bridge_markdown(tool, slots, values, fixed, got.get("numbering"),
                                                     applicability_of(doc, tool))
        path = os.path.join(work, "appraisal.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(md)
        ver = self._call(script, ["--verify", path, "--tool", vtool, "--scope", scope], timeout=timeout, cwd=work)
        if not ver.get("ok"):
            return ver
        if _LEGACY_RE.search(ver["data"] or ""):
            # a 2.0.0 a jelölő nélküli, régi azonosítójú fájlt nem pontozza — a híd vázából ez nem fordulhat elő
            return error("PLUGIN_FAILED", "A validator a hídfájlt régi (1.x) számozásúnak látta, és nem pontozta.",
                         {"plugin": PLUGIN, "tool": tool})
        reported = parse_verify(ver["data"])
        if reported is None:
            return error("PLUGIN_FAILED", "A validator --verify kimenete nem értelmezhető.", {"plugin": PLUGIN})
        rollup_text = None
        if script == "appraise.py" and sent:
            rol = self._call(script, ["--rollup", path, "--tool", vtool, "--scope", scope], timeout=timeout, cwd=work)
            if not rol.get("ok"):
                return rol
            rollup_text = rol["data"]
        return {"ok": True, "data": self._bridge_result(doc, tool, scope, cap, instrument, slots, values, sent,
                                                         invalid, skipped, reported, rollup_text)}

    def _bridge_result(self, doc, tool, scope, cap, instrument, slots, values, sent, invalid, skipped, reported,
                       rollup_text):
        active = self.active_guards(cap)
        fixed = fixed_release(cap)
        own = self._own_completeness(tool, slots, values, sent, fixed)
        guards, notes = [], []
        algorithm = ALGORITHM.get(tool, "conservative")
        trusted = not (("H1" in active and tool == "tripod-ai") or ("H2" in active and tool == "probast-ai"))
        if not trusted:
            gid = "H1" if tool == "tripod-ai" else "H2"
            guards.append({"id": gid, "message": GUARD_TEXT[gid][0], "effect": GUARD_TEXT[gid][1]})
        reported = dict(reported, trusted=trusted,
                        agrees=(reported["answered"] == own["answered"] and reported["expected"] == own["expected"]))
        out = {"schema": RESULT_SCHEMA, "tool": tool, "validator_version": cap.get("version"), "legacy": True,
               "mode": "bridge", "scope": scope, "target": doc.get("target") if isinstance(doc.get("target"), dict)
               else {}, "invalid": invalid, "domains": [], "amstar2": None, "grade": None, "nos": None,
               "tripod": None, "conventions": {}, "validator_reported": reported}
        out.update(own)
        if invalid:
            out["complete"] = False
        tiers = _tiers(instrument)
        overall = {"implied": None, "level": None, "algorithm": algorithm, "official": algorithm == "published",
                   "basis": "validator", "lines": _text_lines(rollup_text), "provisional": not out["complete"]}
        if rollup_text and rollup_not_final(rollup_text):
            overall["provisional"] = True                   # 2.0.0: a plugin maga mondja, hogy nem végleges
        if rollup_text and algorithm == "conservative":
            doms, level, _lines = parse_rollup_signalling(rollup_text)
            missing_by_dom = {}
            for m in own["missing"]:
                for s in slots:
                    if slot_key(s) == m["key"]:
                        missing_by_dom.setdefault(s["domain"], []).append(m["item"])
            for d in doms:
                d.update(algorithm="conservative", implied=tiers.get(d["level"]),
                         missing=missing_by_dom.get(d["domain"], []))
                if d["missing"] and d["level"] == "low":
                    d["flags"] = ["validator_low_with_missing"]
                if d.pop("incomplete", False):
                    # 2.0.0: üres vagy nem választható hely → a domén ítélet nélkül (nem a megválaszoltakból LOW)
                    d["flags"] = d.get("flags", []) + ["incomplete"]
            out["domains"] = doms
            overall.update(level=level, implied=tiers.get(level) if level else None, label=NOT_OFFICIAL)
            incomplete, at_least = rollup_incomplete(rollup_text)
            if incomplete:
                overall.update(provisional=True, incomplete=True)
                if at_least:
                    overall["at_least"] = at_least
            notes.append(NOT_OFFICIAL)
        elif rollup_text and tool == "amstar2":
            am = parse_rollup_amstar2(rollup_text)
            if am is not None:
                am["convention"] = "weakness"
                out["amstar2"] = am
                overall.update(implied=am["rating"], provisional=am["provisional"] or overall["provisional"])
                out["conventions"]["amstar2.partial_yes_critical"] = "weakness"
            if "H4" in active:
                guards.append({"id": "H4", "message": GUARD_TEXT["H4"][0], "effect": GUARD_TEXT["H4"][1]})
            if skipped:
                notes.append(AMSTAR_NA_NOTE)
                if out["amstar2"] is not None:
                    out["amstar2"]["provisional"] = True
                overall["provisional"] = True
        elif rollup_text and tool == "grade":
            gr = parse_rollup_grade(rollup_text) or {}
            out["grade"] = {"certainty": gr.get("certainty"), "unresolved": list(gr.get("unresolved") or []),
                            "start": gr.get("start"), "reliable": True}
            for k in ("incomplete", "range"):
                if gr.get(k):
                    out["grade"][k] = gr[k]
            self._grade_guards(out, doc, active, guards, instrument, slots, fixed)
            overall.update(implied=out["grade"]["certainty"])
            if gr.get("incomplete") or gr.get("unresolved"):
                overall["provisional"] = True
        elif rollup_text and tool == "nos":
            out["nos"] = parse_rollup_nos(rollup_text)
            if (out["nos"] or {}).get("provisional"):
                overall["provisional"] = True
            notes.append(NOS_NOTE_FIXED if fixed else NOS_NOTE)
        elif tool == "jbi":
            notes.append({"hu": "JBI: nincs algoritmus és pontszám — a validator jelző-sorai csak segítség.",
                          "en": "JBI: no algorithm and no score — the validator's flag lines are only an aid."})
        elif tool == "probast-ai":
            overall["label"] = _i18n("HOLISZTIKUS — a PROBAST+AI-nak nincs algoritmusa; az összítélet a Tiéd.",
                                     "HOLISTIC — PROBAST+AI has no algorithm; the overall judgement is yours.")
        elif tool == "tripod-ai":
            counts = {"present": 0, "partial": 0, "missing": 0, "not_applicable": 0}
            for s in slots:
                v = values.get(slot_key(s))
                if v in counts:
                    counts[v] += 1
            out["tripod"] = {"scope": scope, "counts": counts}
            notes.append({"hu": "A TRIPOD+AI a jelentés teljességét méri, nem a módszertan helyességét.",
                          "en": "TRIPOD+AI measures reporting completeness, not methodological soundness."})
        out["overall"] = overall
        out["guards"] = guards
        out["guards_active"] = guard_order(active)        # a telepített verzióra bekapcsolt őrök (üres: egy sem kell)
        out["notes"] = notes
        return out
