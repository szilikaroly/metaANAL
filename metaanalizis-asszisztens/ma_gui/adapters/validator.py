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

**Őrök** (5.0; aktívak, ha a hiba a telepített verzióban él — legacy módban a beépített tábla, újabb pluginnál a
kézfogás ``known_issues``-a szerint):

- **H1** (TRIPOD+AI) és **H2** (PROBAST+AI): a teljességet a munkapad SAJÁT JSON-jából számolja, menettel
  minősített kulcsokkal (``development/1.1``); a plugin számlálása ``validator_reported``-ként, ``trusted: false``
  jelöléssel kerül mellé. A státuszt soha nem soron belüli kereséssel olvassuk, csak a státusz-cellából.
- **H3** (GRADE): a publikációs torzítás „suspected”/„strongly suspected” értékénél a validator rollupja NEM
  megbízható (sosem minősít le). A motor konvenciója él (11. fejezet, 4. döntés): a „suspected” FELOLDATLAN, amíg
  ember nem dönt 0 vagy −1 között indoklással (``grade.unresolved``); a bizonyosság ilyenkor ``null``.
- **H4** (AMSTAR 2): eszközönkénti álnév-tábla a globális ``_norm`` helyett — a ``partial_yes`` mindig
  „Partial yes” szöveggel megy (soha „PY”, amit a validator „probably yes”-nek olvasna); az 1.0.0 besorolása a
  ``weakness`` konvenciónak felel meg, és így is címkézzük (a projekt konvenciója ``meets``, KB AMSTAR2-00).
- **H12** (polaritás): a validator 1.0.0 referenciafájljában néhány tétel polaritás-címkéje eltér a publikált
  eszköztől (QUADAS-2 1.2/1.3, ROBINS-E 2.3/6.2 és ROBINS-I 6.3 „reverse”, a ROBINS-E 5.2 nem) — a motor
  definíciója a publikált változatot követi. Ha ilyen tétel válaszolt, az érintett domének és az összítélet
  validator-ítélete nem megbízható (``reliable: false``, ``unreliable_domains``); a motor ítélete számít.
- **H13** (számozás): a motor a publikált számozást használja (ROBINS-I 2016: ``numbering_changed``; QUIPS a–g:
  ``validator_ids``), a validator 1.0.0 a régit — ugyanaz az azonosító MÁS kérdést jelöl. Ezeket a tételeket a híd
  NEM küldi át (különben a validator rossz kérdésre adott választ értékelne); a validator teljessége és ítélete így
  nem vethető össze a motoréval (``comparable: false``).

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
UNEXPRESSIBLE = {"amstar2": ("not_applicable",)}
PUB_BIAS_DOMAIN = "5"

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
                  "„weakness” konvenció szerinti (a projekt konvenciója „meets”, KB AMSTAR2-00).",
            "en": "H4: 'Partial yes' was sent canonically ('Partial yes', never 'PY'); the validator 1.0.0 rating "
                  "follows the 'weakness' convention (the project convention is 'meets', KB AMSTAR2-00)."},
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
_DOMAIN_RE = re.compile(r"^\s*Domain (\S+) \((.*)\): (HIGH / SERIOUS|SOME CONCERNS / UNCLEAR|LOW)\s+—\s+(.*)$")
_ROUTERS_RE = re.compile(r"routing questions answered, not scored:\s*(.*)$")
_OVERALL_RE = re.compile(r"Implied overall:\s*(LOW|SOME CONCERNS|HIGH / SERIOUS)")
_AM_CRIT_RE = re.compile(r"Critical flaws \((\d+)\):\s*(.*)$", re.M)
_AM_WEAK_RE = re.compile(r"Non-critical weaknesses \((\d+)\):\s*(.*)$", re.M)
_AM_RATING_RE = re.compile(r"OVERALL CONFIDENCE IN THE RESULTS:\s*(HIGH|MODERATE|CRITICALLY LOW|LOW)")
_AM_UNANS_RE = re.compile(r"(\d+) item\(s\) unanswered")
_GR_START_RE = re.compile(r"Start:\s*(HIGH|LOW)")
_GR_CERT_RE = re.compile(r"CERTAINTY:\s*(HIGH|MODERATE|LOW|VERY LOW)")
_NOS_TOTAL_RE = re.compile(r"TOTAL:\s*(\d+)/(\d+) stars")
_NOS_DOM_RE = re.compile(r"Stars by domain:\s*(.*)$", re.M)
_SKEL_HEAD_RE = re.compile(r"^##\s+(?:.*?\b(?:Domain|Checklist|domain|Phase|Section|Item group))\s+(\S+)\s+[—–-]")
_SKEL_PASS_RE = re.compile(r"^###\s+.*\((development|evaluation)\)", re.I)
_SKEL_ROW_RE = re.compile(r"^\|\s*([0-9A-Za-z][0-9A-Za-z.\-]*)\s*\|")
_SKEL_HEADER_CELLS = ("#", "sq", "item")
LEVEL = {"HIGH / SERIOUS": "high", "SOME CONCERNS / UNCLEAR": "some", "SOME CONCERNS": "some", "LOW": "low"}


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
            domain = m.group(1)
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


def slot_key(slot):
    return "%s/%s" % (slot["pass"], slot["id"]) if slot.get("pass") else slot["id"]


def bridge_markdown(tool, slots, values):
    """A validator sablonjával egyező Markdown — (szöveg, {kulcs: token}, invalid[], unexpressible[]).
    A kérdés-oszlopban „—”, a bizonyíték-oszlopban „[E<n>]”: válaszszótárbeli szó csak az ítélet-cellába kerül."""
    tokens = ANSWER_TOKENS[tool]
    unexpr = UNEXPRESSIBLE.get(tool, ())
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
    return "\n".join(lines) + "\n", sent, invalid, skipped


def parse_verify(text):
    """'N/M answered' + UNANSWERED-lista → {answered, expected, unanswered[]} vagy None (nem értelmezhető)."""
    m = _VERIFY_RE.search(text or "")
    if not m:
        return None
    um = _UNANSWERED_RE.search(text or "")
    return {"answered": int(m.group(1)), "expected": int(m.group(2)),
            "unanswered": _ids(um.group(2)) if um else []}


def parse_rollup_signalling(text):
    """A konzervatív (jelző-kérdéses) rollup sorai → (domének, összítélet-szint, sorok)."""
    domains, overall, lines = [], None, []
    for line in (text or "").splitlines():
        if line.strip():
            lines.append(line.strip())
        m = _DOMAIN_RE.match(line)
        if m:
            why = m.group(4)
            forced, unknown = [], []
            if why.startswith("'No' or 'Probably no' at "):
                forced = _ids(why.split(" at ", 1)[1])
            elif why.startswith("no information at "):
                unknown = _ids(why.split(" at ", 1)[1])
            domains.append({"domain": m.group(1), "level": LEVEL[m.group(3)], "forced_by": forced,
                            "unknown_at": unknown, "routers": []})
            continue
        m = _ROUTERS_RE.search(line)
        if m and domains:
            domains[-1]["routers"] = _ids(m.group(1))
            continue
        m = _OVERALL_RE.search(line)
        if m:
            overall = LEVEL.get(m.group(1), None)
    return domains, overall, lines[:60]


def parse_rollup_amstar2(text):
    rating = _AM_RATING_RE.search(text or "")
    crit = _AM_CRIT_RE.search(text or "")
    weak = _AM_WEAK_RE.search(text or "")
    unans = _AM_UNANS_RE.search(text or "")
    if not rating:
        return None
    return {"rating": rating.group(1).lower().replace(" ", "_"),
            "critical_flaws": _ids(crit.group(2)) if crit else [],
            "weaknesses": _ids(weak.group(2)) if weak else [],
            "provisional": bool(unans)}


def parse_rollup_grade(text):
    cert = _GR_CERT_RE.search(text or "")
    start = _GR_START_RE.search(text or "")
    if not cert:
        return None
    return {"certainty": cert.group(1).lower().replace(" ", "_"),
            "start": start.group(1).lower() if start else None}


def parse_rollup_nos(text):
    tot = _NOS_TOTAL_RE.search(text or "")
    if not tot:
        return None
    dom = _NOS_DOM_RE.search(text or "")
    return {"total": int(tot.group(1)), "max": int(tot.group(2)),
            "by_domain_text": dom.group(1).strip() if dom else None}


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
                    return {"ok": True, "slots": self._skel_cache[key]}
        res = self._call(script, ["--skeleton", vtool, "--scope", scope])
        if not res.get("ok"):
            return res
        slots = parse_skeleton(res["data"], checklist=(script == "checklist.py"))
        if not slots:
            return error("PLUGIN_FAILED", "A validator --skeleton kimenete nem értelmezhető (nincs tétel).",
                         {"plugin": PLUGIN, "tool": tool})
        if key is not None:
            with self._skel_lock:
                self._skel_cache[key] = slots
        return {"ok": True, "slots": slots}

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
        return res

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
            own = self._own_completeness(tool, got["slots"], _values(doc), {})
            out["validator_reported"] = {"answered": out.get("answered"), "expected": out.get("expected"),
                                         "trusted": False}
            out.update(own)
            gid = "H1" if tool == "tripod-ai" else "H2"
            guards.append({"id": gid, "message": GUARD_TEXT[gid][0], "effect": GUARD_TEXT[gid][1]})
        if tool == "grade":
            self._grade_guards(out, doc, active, guards, instrument, None)
        if tool == "amstar2" and "H4" in active:
            out.setdefault("conventions", {})["amstar2.partial_yes_critical"] = "weakness"
            guards.append({"id": "H4", "message": GUARD_TEXT["H4"][0], "effect": GUARD_TEXT["H4"][1]})
        out["guards"] = guards
        return {"ok": True, "data": out}

    # -- bridge mód (1.0.0)
    @staticmethod
    def _own_completeness(tool, slots, values, sent):
        """A munkapad saját számlálása (H1/H2-őr): minden hatókörbe eső hely kapott-e a kanonikus szótárból
        értéket — menettel minősített kulccsal."""
        tokens = ANSWER_TOKENS[tool]
        unexpr = UNEXPRESSIBLE.get(tool, ())
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

    def _grade_guards(self, out, doc, active, guards, instrument, slots):
        """H3 (és a „nagyon nagy hatás”): a validator GRADE-bizonyossága ilyenkor nem megbízható; a „suspected”
        feloldatlan, amíg ember nem dönt indoklással (4. döntés)."""
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
        if any(v == "very_large" for v in values.values()):
            unreliable = True
            guards.append({"id": "VL", "message": VERY_LARGE_GUARD[0], "effect": VERY_LARGE_GUARD[1]})
        if unreliable or unresolved:
            if grade.get("certainty") is not None:
                grade["validator_certainty"] = grade.get("certainty")
            grade["certainty"] = None
            grade["reliable"] = False
        grade["unresolved"] = unresolved
        out["grade"] = grade

    def _check_bridge(self, doc, tool, scope, cap, instrument, work, timeout):
        script, vtool, _f = TOOLS[tool]
        got = self._skeleton(cap, tool, scope)
        if not got.get("ok"):
            return got
        slots = got["slots"]
        values = _values(doc)
        md, sent, invalid, skipped = bridge_markdown(tool, slots, values)
        path = os.path.join(work, "appraisal.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(md)
        ver = self._call(script, ["--verify", path, "--tool", vtool, "--scope", scope], timeout=timeout, cwd=work)
        if not ver.get("ok"):
            return ver
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
        own = self._own_completeness(tool, slots, values, sent)
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
            out["domains"] = doms
            overall.update(level=level, implied=tiers.get(level) if level else None, label=NOT_OFFICIAL)
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
            gr = parse_rollup_grade(rollup_text)
            out["grade"] = {"certainty": gr["certainty"] if gr else None, "unresolved": [],
                            "start": gr["start"] if gr else None, "reliable": True}
            self._grade_guards(out, doc, active, guards, instrument, slots)
            overall.update(implied=out["grade"]["certainty"])
        elif rollup_text and tool == "nos":
            out["nos"] = parse_rollup_nos(rollup_text)
            notes.append({"hu": "NOS: a validator 1.0.0 a „Részben igen”-t minden tételen 1 csillagnak veszi; a "
                                "csillagszámot a motor is számolja. Hivatalos küszöb nincs.",
                          "en": "NOS: validator 1.0.0 scores 'Partial yes' as 1 star on every item; the engine counts "
                                "stars too. There is no official threshold."})
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
        out["notes"] = notes
        return out
