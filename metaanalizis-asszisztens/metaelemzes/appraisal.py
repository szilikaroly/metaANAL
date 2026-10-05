# -*- coding: utf-8 -*-
"""Értékelések (szk.appraisal/v1): tárolás, teljesség, implikált ítélet, felülbírálás, egyezés (κ), konszenzus,
forgalmi lámpa (szk.rob-summary/v1) és a kinyerési tábla rob oszlopának szinkronja (terv 4.11, 5.4, 6.4–6.5; 11.
fejezet 4–6. döntés). Az eszközök a metaelemzes.instruments natív definíciói.

Tárolás: 04_torzitas_kockazat/appraisals/<egység>.<eszköz>[.<cél>].<értékelő>.json — az egység a vizsgálat
azonosítója (a fájlnévben biztonságos alakban) vagy a fenntartott 'review' (AMSTAR 2) / 'manuscript' (saját kézirat);
a cél (target.key) pl. a kimenet vagy a modell; a fenntartott értékelők 'consensus' (két független emberi
értékelés egyeztetett változata) és 'ai' (Claude-vázlat). Az írás atomi és ellenőrzött.

Számítás (a felület nem számol):
    check(doc)                 → szk.appraisal-result/v1: teljesség (cellánként, PROBAST+AI-nál menetenként — H1/H2
                                 őr), implikált doménítélet és összítélet 'algorithm' címkével, felülbírálások (X017),
                                 AMSTAR 2 (mindkét konvenció), GRADE (feloldatlan publikációs torzítás), NOS-csillagok,
                                 TRIPOD+AI jelentési teljesség
    validate(doc)              → szerkezeti és tartalmi hibák (magyarul)
    agreement(párok)           → szk.ma.appraisal-agreement/v1: Cohen-féle κ CI-vel (Fleiss–Cohen–Everitt 1969),
                                 eltérések; appraisal_consensus(a, b) ugyanez egy párra
    build_consensus(a, b, …)   → konszenzus-dokumentum (status: consensus)
    rob_summary(docs, tool, …) → szk.rob-summary/v1 (forgalmi lámpa + súlyarány egy futásból)
    rob_sync_proposal(…)       → szk.ma.rob-sync-proposal/v1 (javasolt rob-cellák, 'calculated' eredet)

Implikált ítélet: RoB 2, ROBINS-I/E, QUADAS-2, QUIPS — 'conservative' (validátor-kompatibilis szabály: amit a
válaszok kikényszerítenek; NEM a hivatalos folyamatábra); AMSTAR 2, GRADE — 'published'; NOS — 'count' (küszöb nincs);
PROBAST+AI, JBI, TRIPOD+AI — 'none' (emberi ítélet). Az AI-vázlat (origin: ai_draft) soha nem értékelő: a κ és a
konszenzus elutasítja (6. döntés)."""
import collections
import copy
import datetime
import hashlib
import json
import math
import os
import re
import tempfile

from . import __version__
from . import instruments as _I

SCHEMA = "szk.appraisal/v1"
RESULT_SCHEMA = "szk.appraisal-result/v1"
SUMMARY_SCHEMA = "szk.rob-summary/v1"
AGREEMENT_SCHEMA = "szk.ma.appraisal-agreement/v1"
SYNC_SCHEMA = "szk.ma.rob-sync-proposal/v1"
APPRAISAL_DIR = "04_torzitas_kockazat/appraisals"
STUDIES_FILE = "03_adatok/studies.json"
STATUSES = ("draft", "complete", "consensus")
FINAL_STATUSES = ("complete", "consensus")
ORIGINS = ("human", "ai_draft")
RATER_CONSENSUS = "consensus"
RATER_AI = "ai"
RESERVED_RATERS = (RATER_CONSENSUS, RATER_AI)
UNIT_REVIEW = "review"
UNIT_MANUSCRIPT = "manuscript"
SPECIAL_UNITS = (UNIT_REVIEW, UNIT_MANUSCRIPT)
RATER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
TOOL_RE = _I.KEY_RE
ANSWER_KEY_RE = _I.ITEM_KEY_RE
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
_SLUG_BAD = re.compile(r"[^A-Za-z0-9_-]+")
_FILE_RE = re.compile(r"^([A-Za-z0-9_-]{1,64})\.([a-z0-9][a-z0-9-]{0,31})(?:\.([A-Za-z0-9][A-Za-z0-9_-]{0,63}))?"
                      r"\.([A-Za-z][A-Za-z0-9_-]{0,31})\.json$")
Z95 = 1.959963984540054
MAX_TEXT = 4000
MAX_QUOTE = 1000
GRADE_LEVELS = ("very_low", "low", "moderate", "high")
GRADE_DOWN = {"not_serious": 0, "serious": -1, "very_serious": -2}
GRADE_UP = {"no": 0, "yes": 1, "very_large": 2}
AMSTAR2_CONVENTIONS = ("meets", "weakness")
LEVEL_ORDER = ("low", "some", "high", "critical", "ni")


class AppraisalError(ValueError):
    """Érvénytelen értékelés vagy művelet; a .problems a részletes (magyar) üzenetek listája."""

    def __init__(self, message, problems=None):
        ValueError.__init__(self, message)
        self.problems = list(problems or [])


def _t(hu, en=None):
    return {"hu": hu, "en": hu if en is None else en}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt(x, nd=2):
    return ("%.*f" % (nd, x)) if isinstance(x, (int, float)) and math.isfinite(x) else "—"


def _en_minus(s):
    return s.replace("-", "−")


def _nonempty(v):
    return isinstance(v, str) and bool(v.strip())


# ------------------------------------------------------------------ eszköz és azonosítók
def instrument_for(doc_or_tool, instrument=None):
    """Instrument a dokumentum eszközéhez: a megadott Instrument / definíció-dict, különben a natív definíció."""
    if isinstance(instrument, _I.Instrument):
        return instrument
    if isinstance(instrument, dict):
        return _I.Instrument(instrument, instrument.get("sha256"))
    tool = doc_or_tool.get("tool") if isinstance(doc_or_tool, dict) else doc_or_tool
    try:
        return _I.load(tool)
    except _I.InstrumentError as exc:
        raise AppraisalError(str(exc), [str(exc)]) from None


def unit_of(doc):
    tg = doc.get("target") if isinstance(doc.get("target"), dict) else {}
    u = tg.get("unit") or tg.get("study_id")
    return u if isinstance(u, str) and u.strip() else None


def target_key(doc):
    tg = doc.get("target") if isinstance(doc.get("target"), dict) else {}
    k = tg.get("key")
    return k if isinstance(k, str) and k else None


def slug(unit):
    """Az értékelési egység fájlnév-alakja (ugyanaz, mint a munkapad routes/appraisal_common.slug-ja)."""
    s = _SLUG_BAD.sub("_", str(unit or "").strip()).strip("_")[:64]
    if not s:
        raise AppraisalError("Az értékelési egység azonosítójából nem képezhető fájlnév.")
    return s


def rater_of(doc):
    """A fájlnév értékelő-része: 'consensus' a konszenzus-változatnál, 'ai' az AI-vázlatnál, különben az értékelő."""
    if doc.get("status") == "consensus":
        return RATER_CONSENSUS
    if doc.get("origin") == "ai_draft":
        return RATER_AI
    return doc.get("assessor")


def relpath(unit, tool, target, rater):
    if not isinstance(tool, str) or not TOOL_RE.match(tool):
        raise AppraisalError("Érvénytelen eszköz-azonosító: %r" % (tool,))
    if target is not None and not (isinstance(target, str) and TARGET_RE.match(target)):
        raise AppraisalError("Érvénytelen cél (target.key): betű/szám, _ vagy -, legfeljebb 64 karakter.")
    if not isinstance(rater, str) or not RATER_RE.match(rater):
        raise AppraisalError("Érvénytelen értékelő-azonosító: betűvel kezdődő, legfeljebb 32 karakter.")
    parts = [slug(unit), tool] + ([target] if target else []) + [rater]
    return "%s/%s.json" % (APPRAISAL_DIR, ".".join(parts))


def doc_relpath(doc):
    unit = unit_of(doc)
    if unit is None:
        raise AppraisalError("A dokumentumból hiányzik az értékelési egység (target.unit vagy target.study_id).")
    return relpath(unit, doc.get("tool"), target_key(doc), rater_of(doc))


def parse_name(name):
    """Fájlnév → (egység-slug, eszköz, cél | None, értékelő) vagy None."""
    m = _FILE_RE.match(os.path.basename(name))
    return (m.group(1), m.group(2), m.group(3), m.group(4)) if m else None


def is_rater(doc):
    """Független emberi értékelés-e (a κ és a konszenzus csak ezt fogadja el; 6. döntés)."""
    return (isinstance(doc, dict) and doc.get("origin", "human") == "human" and doc.get("status") != "consensus"
            and isinstance(doc.get("assessor"), str) and doc["assessor"].lower() not in RESERVED_RATERS)


# ------------------------------------------------------------------ normalizálás és ellenőrzés
def normalize(doc, instrument=None):
    """A válaszok kanonikus alakra hozása (eszközönkénti álnév-tábla: 'PY' az AMSTAR 2-ben partial_yes, a RoB 2-ben
    probably_yes) → (új dokumentum, [(kulcs, régi, új)]). A nem értelmezhető érték változatlan marad (a validate
    jelzi). Szöveges válasz ('PY') → {'value': …}."""
    inst = instrument_for(doc, instrument)
    out = copy.deepcopy(doc)
    changes = []
    answers = out.get("answers")
    if not isinstance(answers, dict):
        return out, changes
    for key, a in list(answers.items()):
        if isinstance(a, str):
            a = {"value": a}
            answers[key] = a
        if not isinstance(a, dict) or not isinstance(a.get("value"), str):
            continue
        it = inst.item(key)
        v = inst.canonical(a["value"], it) if it is not None else None
        if v is not None and v != a["value"]:
            changes.append((key, a["value"], v))
            a["value"] = v
    return out, changes


def _text_problem(v, where, out, limit=MAX_TEXT):
    if v is None:
        return
    if not isinstance(v, str):
        out.append("%s: szöveg vagy null kell" % where)
    elif len(v) > limit:
        out.append("%s: legfeljebb %d karakter" % (where, limit))


def problems(doc, instrument=None, project_dir=None):
    """→ {'errors': [...], 'warnings': [...]} — séma (szk.appraisal/v1) + tartalom: ismert tétel, kanonikus és
    megengedett válasz, ismert domén és ítélet, eredet/státusz-szabályok (AI-vázlat: jóváhagyás, 6. döntés szerinti
    tételindoklás), GRADE-feloldás, C osztályú projektben nincs AI-vázlat."""
    errors, warnings = [], []
    if not isinstance(doc, dict):
        return {"errors": ["a dokumentum gyökere objektum legyen"], "warnings": []}
    errors.extend(_I.contract_errors(doc, "appraisal", 1))
    if not isinstance(doc.get("tool"), str) or (doc.get("tool") not in _I.available() and instrument is None):
        errors.append("tool: ismeretlen értékelő eszköz (%r); elérhető: %s" % (doc.get("tool"), ", ".join(
            _I.available())))
        return {"errors": errors, "warnings": warnings}
    inst = instrument_for(doc, instrument)
    if doc.get("tool") != inst.key:
        errors.append("tool: %r, de az eszköz-definíció %r" % (doc.get("tool"), inst.key))
    scope = inst.scope_of(doc.get("scope"))
    if scope is None:
        errors.append("scope: %s egyike kell (kapott: %r)" % (" | ".join(inst.scope_ids), doc.get("scope")))
        scope = inst.default_scope
    in_scope = {it["key"] for it in inst.slots(scope)}
    sha = doc.get("instrument_sha256")
    if isinstance(sha, str) and inst.sha256 and sha not in (inst.sha256, inst.doc.get("reference_sha256")):
        warnings.append("instrument_sha256: az eszköz-definíció a mentés óta változott (most: %s…)" % inst.sha256[:12])
    answers = doc.get("answers") if isinstance(doc.get("answers"), dict) else {}
    ai = doc.get("origin") == "ai_draft"
    for key, a in answers.items():
        where = "answers[%s]" % key
        if not isinstance(key, str) or not ANSWER_KEY_RE.match(key):
            errors.append("answers: érvénytelen kulcs %r (alak: ^((development|evaluation)/)?azonosító$)" % (key,))
            continue
        it = inst.item(key)
        if it is None:
            hint = ""
            if inst.passes and "/" not in key:
                hint = " — a PROBAST+AI kulcsa menettel minősített (development/%s, evaluation/%s)" % (key, key)
            errors.append("%s: az eszköznek nincs ilyen tétele%s" % (where, hint))
            continue
        if not isinstance(a, dict):
            errors.append("%s: objektum kell ({value, evidence?, rationale?})" % where)
            continue
        v = a.get("value")
        if v is not None:
            if key not in in_scope:
                warnings.append("%s: a tétel a választott hatókörön (%s) kívül esik, nem számít" % (where, scope))
            if v not in inst.answers:
                canon = inst.canonical(v, it)
                errors.append("%s.value: %r nem kanonikus érték%s" % (
                    where, v, (" (írd így: %s)" % canon) if canon else " — az eszköz válaszértékeinek egyike kell"))
            elif v not in inst.allowed(it):
                errors.append("%s.value: %r ennél a tételnél nem adható (lehet: %s)" % (
                    where, v, ", ".join(inst.allowed(it))))
        ev = a.get("evidence") if isinstance(a.get("evidence"), dict) else {}
        _text_problem(ev.get("text"), where + ".evidence.text", errors, MAX_QUOTE)
        _text_problem(a.get("comment"), where + ".comment", errors)
        rat = a.get("rationale")
        if isinstance(rat, dict):
            for k in ("asks", "because", "change", "uncertain"):
                _text_problem(rat.get(k), "%s.rationale.%s" % (where, k), errors)
        else:
            _text_problem(rat, where + ".rationale", errors)
        res = a.get("resolution")
        if isinstance(res, dict):
            _resolution_problems(inst, it, v, res, where, errors)
        if ai and v is not None:
            errors.extend(_ai_item_problems(a, where))
    _judgement_problems(doc, inst, errors)
    status, origin = doc.get("status"), doc.get("origin")
    if origin == "ai_draft":
        if status == "consensus":
            errors.append("status: AI-vázlat nem lehet konszenzus (a konszenzus két független emberi értékelés "
                          "egyeztetése; 6. döntés)")
        if status == "complete" and not _nonempty(doc.get("approved_by")):
            errors.append("status: AI-vázlat csak emberi jóváhagyás után lehet 'complete' (approved_by, "
                          "approved_at; 6. döntés)")
        if _nonempty(doc.get("approved_by")):
            if doc["approved_by"].lower() in RESERVED_RATERS:
                errors.append("approved_by: emberi értékelő monogramja kell")
            if not (isinstance(doc.get("approved_at"), str) and TS_RE.match(doc["approved_at"])):
                errors.append("approved_at: a jóváhagyás ideje (ISO 8601) kell")
    elif isinstance(doc.get("assessor"), str) and doc["assessor"].lower() in RESERVED_RATERS:
        errors.append("assessor: %r fenntartott azonosító (konszenzus / AI-vázlat); emberi értékelőnél a "
                      "monogramot add meg" % doc["assessor"])
    if status == "consensus":
        a2 = doc.get("second_assessor")
        if not (isinstance(a2, str) and RATER_RE.match(a2) and a2.lower() not in RESERVED_RATERS):
            errors.append("second_assessor: a konszenzushoz a második (emberi) értékelő monogramja kell")
        elif a2 == doc.get("assessor"):
            errors.append("second_assessor: két különböző értékelő kell")
    for k in ("created", "updated"):
        v = doc.get(k)
        if isinstance(v, str) and v and v != "…" and not TS_RE.match(v):
            warnings.append("%s: nem ISO 8601 időbélyeg" % k)
    if project_dir and ai:
        try:
            from . import projekt
            meta = projekt.load_project_meta(project_dir)
        except Exception:                                   # noqa: BLE001 — a meta hiánya nem akadály
            meta = None
        if isinstance(meta, dict) and str(meta.get("data_class") or "").upper() == "C":
            errors.append("origin: C osztályú (betegszintű adatot tartalmazó) projektben AI-vázlat nem menthető "
                          "(6. döntés, 7.4)")
    return {"errors": errors, "warnings": warnings}


def validate(doc, instrument=None, project_dir=None):
    """A dokumentum hibái (magyar üzenetek listája; üres: megfelel). A munkapad ``appraisal_validate``-ja."""
    return problems(doc, instrument, project_dir)["errors"]


appraisal_validate = validate


def _ai_item_problems(a, where):
    """6. döntés: AI-vázlatnál tételenként javaslat + szó szerinti bizonyíték hellyel + egyszerű nyelvű indoklás
    (mit kérdez, miért ez, mi változtatná meg) + bizonytalanság-jelölés, ha a cikk nem közli."""
    out = []
    rat = a.get("rationale") if isinstance(a.get("rationale"), dict) else {}
    missing = [k for k, hu in (("asks", "mit kérdez a tétel"), ("because", "miért ez a javaslat"),
                               ("change", "mi változtatná meg")) if not _nonempty(rat.get(k))]
    if missing:
        out.append("%s.rationale: AI-vázlatnál kötelező az egyszerű nyelvű indoklás (%s)" % (
            where, ", ".join({"asks": "asks — mit kérdez a tétel", "because": "because — miért ez a javaslat",
                              "change": "change — mi változtatná meg"}[k] for k in missing)))
    ev = a.get("evidence") if isinstance(a.get("evidence"), dict) else {}
    has_quote = _nonempty(ev.get("text")) and (ev.get("page") is not None or _nonempty(ev.get("locator")))
    if not has_quote and not _nonempty(rat.get("uncertain")):
        out.append("%s.evidence: AI-vázlatnál szó szerinti rövid idézet kell hellyel (oldal vagy táblázat), vagy — ha "
                   "a cikk nem közli — a bizonytalanság jelölése (rationale.uncertain)" % where)
    return out


def _resolution_problems(inst, item, value, res, where, errors):
    if inst.key != "grade" or item.get("domain") != "5":
        errors.append("%s.resolution: csak a GRADE publikációs torzítás tételénél adható" % where)
        return
    allowed = {"suspected": (0, -1), "strongly_suspected": (-1, -2)}.get(value)
    if allowed is None:
        errors.append("%s.resolution: csak 'suspected' vagy 'strongly_suspected' ítéletnél értelmes" % where)
        return
    if res.get("step") not in allowed:
        errors.append("%s.resolution.step: %s lehet" % (where, " vagy ".join(str(s) for s in allowed)))
    if not _nonempty(res.get("rationale")):
        errors.append("%s.resolution.rationale: indoklás kötelező (11. fejezet 4. döntés)" % where)


def _judgement_problems(doc, inst, errors):
    seen = set()
    for coll in ("domain_judgements", "applicability", "overall_passes"):
        items = doc.get(coll)
        if not isinstance(items, list):
            continue
        for i, dj in enumerate(items):
            pre = "%s[%d]" % (coll, i)
            if not isinstance(dj, dict):
                continue
            did, ps = str(dj.get("domain")), dj.get("pass")
            if coll != "overall_passes" and inst.domain(did) is None:
                errors.append("%s.domain: ismeretlen domén %r" % (pre, did))
                continue
            if ps is not None and ps not in inst.passes:
                errors.append("%s.pass: ennél az eszköznél nincs %r menet" % (pre, ps))
            if coll == "domain_judgements" and inst.passes and ps is None:
                errors.append("%s.pass: a PROBAST+AI doménítélete menetenként külön (development | evaluation)" % pre)
            if coll == "applicability" and not inst.domain(did).get("applicability"):
                errors.append("%s: a(z) %s doménhez nincs alkalmazhatósági ítélet" % (pre, did))
            j = dj.get("judgement")
            if j is not None and j not in inst.verdicts:
                errors.append("%s.judgement: %r nem az eszköz ítéletskálája (%s)" % (pre, j, " | ".join(inst.verdicts)))
            k = (coll, did, ps)
            if k in seen:
                errors.append("%s: ismétlődő ítélet (%s%s)" % (pre, did, "/" + ps if ps else ""))
            seen.add(k)
            for f in ("rationale", "override_reason"):
                _text_problem(dj.get(f), "%s.%s" % (pre, f), errors)
    ov = doc.get("overall")
    if isinstance(ov, dict):
        j = ov.get("judgement")
        if j is not None and j not in inst.verdicts:
            errors.append("overall.judgement: %r nem az eszköz ítéletskálája (%s)" % (j, " | ".join(inst.verdicts)
                                                                                    or "nincs ítélet"))
        for f in ("rationale", "override_reason"):
            _text_problem(ov.get(f), "overall." + f, errors)


# ------------------------------------------------------------------ értékelés-ellenőrzés (appraisal-result)
def _values(doc, inst, slots):
    """(kulcs → kanonikus érték, érvénytelenek, normalizáltak, hatókörön kívüliek)."""
    keys = {it["key"] for it in slots}
    vals, invalid, normalized, outside = {}, [], [], []
    answers = doc.get("answers") if isinstance(doc.get("answers"), dict) else {}
    for key, a in answers.items():
        raw = a.get("value") if isinstance(a, dict) else (a if isinstance(a, str) else None)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        it = inst.item(key) if isinstance(key, str) else None
        if it is None:
            invalid.append({"item": str(key), "pass": None, "key": str(key), "value": raw if isinstance(raw, str)
                            else None, "reason": _t("ismeretlen tétel", "unknown item")})
            continue
        if key not in keys:
            outside.append(key)
            continue
        v = inst.canonical(raw, it)
        if v is None:
            invalid.append({"item": it["id"], "pass": it.get("pass"), "key": key,
                            "value": raw if isinstance(raw, str) else None,
                            "reason": _t("nem megengedett válasz (lehet: %s)" % ", ".join(inst.allowed(it)),
                                         "answer not allowed (allowed: %s)" % ", ".join(inst.allowed(it)))})
            continue
        if v != raw:
            normalized.append({"key": key, "from": raw, "to": v})
        vals[key] = v
    return vals, invalid, normalized, outside


def _judgement_of(doc, coll, domain, ps):
    for dj in doc.get(coll) or ():
        if isinstance(dj, dict) and str(dj.get("domain")) == str(domain) and dj.get("pass") == ps:
            return dj
    return None


def _domain_rows(inst, slots, ps_list):
    """[(domain, pass, [tételek])] a definíció sorrendjében (menetes eszköznél menetenként)."""
    out = []
    for d in inst.domains:
        for ps in ps_list:
            its = [it for it in slots if str(it["domain"]) == str(d["id"]) and it.get("pass") == ps]
            if its:
                out.append((str(d["id"]), ps, its))
    return out


def _conservative(inst, doc, slots, vals):
    tiers = inst.rollup.get("tiers") or {}
    domains, lines = [], []
    tiers_seen = []
    for did, ps, its in _domain_rows(inst, slots, [None] + inst.passes):
        forced, forced_some, unknown, partial, routers, missing = [], [], [], [], [], []
        for it in its:
            v = vals.get(it["key"])
            if it.get("polarity") == "router":
                if v is not None:
                    routers.append(it["id"])
                continue
            if v is None:
                missing.append(it["id"])
                continue
            sig = inst.signal(it, v)
            if sig == "problem":
                (forced_some if it.get("severity") == "some" else forced).append(it["id"])
            elif sig == "unknown":
                unknown.append(it["id"])
            elif sig == "partial":
                partial.append(it["id"])
        if forced:
            tier = "high"
            why = _t("„Nem”/„Valószínűleg nem” (fordított kérdésnél „Igen”) itt: %s" % ", ".join(forced),
                     "'No'/'Probably no' (or 'Yes' on a reverse question) at %s" % ", ".join(forced))
        elif missing:
            tier = None
            why = _t("hiányzó válasz: %s — a domén implikált ítélete még nem számolható" % ", ".join(missing),
                     "unanswered: %s — no implied judgement yet" % ", ".join(missing))
        elif forced_some or unknown or partial:
            tier = "some"
            bits_hu, bits_en = [], []
            if forced_some:
                bits_hu.append("legalább a középső szintet kikényszeríti: %s" % ", ".join(forced_some))
                bits_en.append("forces at least the middle level: %s" % ", ".join(forced_some))
            if unknown:
                bits_hu.append("nincs információ: %s" % ", ".join(unknown))
                bits_en.append("no information at %s" % ", ".join(unknown))
            if partial:
                bits_hu.append("részleges válasz: %s" % ", ".join(partial))
                bits_en.append("partial answer at %s" % ", ".join(partial))
            why = _t("; ".join(bits_hu), "; ".join(bits_en))
        else:
            tier = "low"
            why = _t("egyik jelző-kérdés sem jelez problémát", "no signalling question flags a problem")
        implied = tiers.get(tier) if tier else None
        dj = _judgement_of(doc, "domain_judgements", did, ps)
        domains.append({"domain": did, "pass": ps, "implied": implied, "algorithm": "conservative",
                        "forced_by": forced + forced_some, "unknown_at": unknown, "partial_at": partial,
                        "routers": routers, "missing": missing, "judgement": dj.get("judgement") if dj else None,
                        "level": inst.level(implied) if implied else None, "text": why})
        tiers_seen.append(tier)
        title = inst.domain_title(did)
        lines.append(_t("%s. domén (%s): %s — %s" % (did, title, inst.verdict_label(implied) if implied else "—",
                                                     why["hu"]),
                        "Domain %s (%s): %s — %s" % (did, inst.domain_title(did, "en"),
                                                     inst.verdict_label(implied, "en") if implied else "—",
                                                     why["en"])))
    if "high" in tiers_seen:
        otier = "high"
    elif None in tiers_seen or not tiers_seen:
        otier = None
    elif "some" in tiers_seen:
        otier = "some"
    else:
        otier = "low"
    overall = tiers.get(otier) if otier else None
    lines.append(_t("Implikált összítélet: %s — a legrosszabb domén szerint." % (
        inst.verdict_label(overall) if overall else "— (hiányos domén)"),
        "Implied overall: %s — set by the worst domain." % (inst.verdict_label(overall, "en") if overall
                                                            else "— (incomplete domain)")))
    lines.append(_t("Ez NEM a hivatalos %s folyamatábra: azt mutatja, amit a válaszok kikényszerítenek. Határesetben "
                    "vesd össze a hivatalos algoritmussal, és ha eltérsz, indokold." % inst.name,
                    "This is NOT the official %s flowchart: it shows what the answers force. Check borderline "
                    "domains against the official algorithm and give a reason if you override." % inst.name))
    return domains, overall, lines


def _flags_only(inst, doc, slots, vals):
    """'none' algoritmus (PROBAST+AI, JBI): doménenként csak jelzések, implikált ítélet nélkül."""
    domains = []
    for did, ps, its in _domain_rows(inst, slots, [None] + inst.passes):
        flags, unknown, missing = [], [], []
        for it in its:
            v = vals.get(it["key"])
            if v is None:
                missing.append(it["id"])
                continue
            sig = inst.signal(it, v)
            if sig in ("problem", "partial"):
                flags.append(it["id"])
            elif sig == "unknown":
                unknown.append(it["id"])
        dj = _judgement_of(doc, "domain_judgements", did, ps)
        text = None
        if flags or unknown:
            text = _t("jelzések (nem ítélet): %s" % ", ".join(
                (["probléma: " + ", ".join(flags)] if flags else []) + (["nincs információ: " + ", ".join(unknown)]
                                                                        if unknown else [])),
                "flags (not a judgement): %s" % ", ".join(
                    (["problem: " + ", ".join(flags)] if flags else []) + (["no information: " + ", ".join(unknown)]
                                                                           if unknown else [])))
        domains.append({"domain": did, "pass": ps, "implied": None, "algorithm": "none", "forced_by": [],
                        "unknown_at": unknown, "routers": [], "missing": missing, "flags": flags,
                        "judgement": dj.get("judgement") if dj else None, "text": text})
    return domains


def amstar2_rating(answers, convention="meets", instrument=None):
    """AMSTAR 2 besorolás a publikált táblából (KB AMSTAR2-00) → blokk mindkét konvencióval. answers: {tétel: érték}
    (kanonikus érték vagy álnév; az AMSTAR 2-ben a 'PY' = partial_yes)."""
    inst = instrument_for("amstar2", instrument)
    convention = convention if convention in AMSTAR2_CONVENTIONS else "meets"
    vals = {}
    for it in inst.items:
        raw = answers.get(it["key"]) if isinstance(answers, dict) else None
        raw = raw.get("value") if isinstance(raw, dict) else raw
        v = inst.canonical(raw, it) if raw is not None else None
        if v is not None:
            vals[it["key"]] = v
    return _amstar2_block(inst, inst.slots(), vals, convention)


def _amstar2_block(inst, slots, vals, convention):
    def classify(conv):
        flaws, weak, pyc, na, unans = [], [], [], [], []
        for it in slots:
            v = vals.get(it["key"])
            if v is None:
                unans.append(it["id"])
            elif v == "not_applicable":
                na.append(it["id"])
            elif v == "partial_yes":
                if it.get("critical"):
                    pyc.append(it["id"])
                    if conv == "weakness":
                        weak.append(it["id"])
            elif v == "no":
                (flaws if it.get("critical") else weak).append(it["id"])
        rating = ("critically_low" if len(flaws) > 1 else "low" if flaws else "moderate" if len(weak) > 1
                  else "high")
        return {"convention": conv, "rating": rating,
                "rating_text": _t(inst.verdict_label(rating), inst.verdict_label(rating, "en")),
                "critical_flaws": flaws, "weaknesses": weak, "partial_yes_critical": pyc, "not_applicable": na,
                "unanswered": unans, "provisional": bool(unans)}
    main = classify(convention)
    other = classify("weakness" if convention == "meets" else "meets")
    out = dict(main)
    out["alternative"] = other
    out["differs"] = main["rating"] != other["rating"]
    out["kb_ref"] = "AMSTAR2-00"
    out["text"] = _t(
        "Besorolás: %s (kritikus hiba %d; nem kritikus gyengeség %d)%s%s" % (
            inst.verdict_label(main["rating"]).upper(), len(main["critical_flaws"]), len(main["weaknesses"]),
            " — IDEIGLENES: %d tétel hiányzik" % len(main["unanswered"]) if main["unanswered"] else "",
            (" · ha a „részben igen” kritikus tételen gyengeség lenne: %s"
             % inst.verdict_label(other["rating"]).upper())
            if out["differs"] else ""),
        "Rating: %s (critical flaws %d; non-critical weaknesses %d)%s%s" % (
            inst.verdict_label(main["rating"], "en").upper(), len(main["critical_flaws"]), len(main["weaknesses"]),
            " — PROVISIONAL: %d item(s) unanswered" % len(main["unanswered"]) if main["unanswered"] else "",
            (" · if 'partial yes' on a critical item counted as a weakness: %s"
             % inst.verdict_label(other["rating"], "en").upper()) if out["differs"] else ""))
    out["note"] = _t("Az AMSTAR 2 az áttekintés eredményeibe vetett bizalmat minősíti, nem a bizonyosságot (az a "
                     "GRADE).", "AMSTAR 2 rates confidence in the review's results, not certainty (that is GRADE).")
    return out


def _grade_block(inst, doc, slots, vals):
    answers = doc.get("answers") if isinstance(doc.get("answers"), dict) else {}
    by_dom = {str(it["domain"]): it for it in slots}
    start = vals.get(by_dom["0"]["key"]) if "0" in by_dom else None
    domains, missing, unresolved = {}, [], []
    down_total, up_total = 0, 0
    for d in inst.domains:
        did, gk, role = str(d["id"]), d.get("grade_key"), d.get("role")
        it = by_dom.get(did)
        if it is None or role == "start":
            continue
        v = vals.get(it["key"])
        entry = {"rating": v, "step": None, "status": "answered" if v is not None else "missing"}
        if v is None:
            missing.append(gk)
        elif role == "downgrade":
            if did == "5":
                a = answers.get(it["key"]) if isinstance(answers.get(it["key"]), dict) else {}
                res = a.get("resolution") if isinstance(a.get("resolution"), dict) else None
                if v == "undetected":
                    entry["step"] = 0
                elif v == "suspected":
                    if res and res.get("step") in (0, -1) and _nonempty(res.get("rationale")):
                        entry.update(step=res["step"], status="resolved", decision_id=res.get("decision_id"))
                    else:
                        entry["status"] = "unresolved"
                        unresolved.append(gk)
                elif v == "strongly_suspected":
                    step = -1
                    if res and res.get("step") in (-1, -2) and _nonempty(res.get("rationale")):
                        step = res["step"]
                        entry.update(status="resolved", decision_id=res.get("decision_id"))
                    entry["step"] = step
            else:
                entry["step"] = GRADE_DOWN.get(v)
            if isinstance(entry["step"], int):
                down_total += entry["step"]
        else:
            entry["step"] = GRADE_UP.get(v, 0)
            up_total += entry["step"]
        domains[gk] = entry
    certainty, provisional = None, bool(missing) or start is None
    upgrades_applied = up_total > 0 and down_total == 0
    if start in ("high", "low") and not missing and not unresolved:
        idx = GRADE_LEVELS.index(start) + down_total + (up_total if down_total == 0 else 0)
        certainty = GRADE_LEVELS[max(0, min(3, idx))]
    lines = []
    if unresolved:
        lines.append(_t("A publikációs torzítás „gyanított” ítélete FELOLDATLAN: dönts 0 vagy −1 között, és indokold "
                        "(11. fejezet 4. döntés). Addig a bizonyosság nem rögzíthető (X019).",
                        "Publication bias 'suspected' is UNRESOLVED: decide 0 or −1 with a reason (decision 4). "
                        "Certainty cannot be recorded until then (X019)."))
    if up_total and down_total:
        lines.append(_t("Felminősítés leminősítés mellett NEM alkalmazva: a GRADE csak le nem minősített bizonyítékot "
                        "minősít fel.", "Upgrades recorded alongside downgrades were NOT applied."))
    return {"start": start, "domains": domains, "downgrade_total": down_total, "upgrade_total": up_total,
            "upgrades_applied": upgrades_applied, "certainty": certainty, "unresolved": unresolved,
            "missing": missing, "provisional": provisional, "lines": lines,
            "note": _t("A bizonyosság KIMENETENKÉNT érvényes, nem vizsgálatonként és nem az áttekintésre.",
                       "Certainty is rated PER OUTCOME, never per study or per review.")}


def _nos_block(inst, slots, vals, scope):
    per, total, mx, missing = collections.OrderedDict(), 0, 0, []
    for d in inst.domains:
        its = [it for it in slots if str(it["domain"]) == str(d["id"])]
        if not its:
            continue
        got = 0
        for it in its:
            v = vals.get(it["key"])
            if v is None:
                missing.append(it["id"])
            elif v == "yes":
                got += int(it.get("stars", 1))
            elif v == "partial_yes":
                got += 1
        dm = sum(int(it.get("stars", 1)) for it in its)
        per[str(d["id"])] = {"domain": str(d["id"]), "stars": got, "max": dm}
        total += got
        mx += dm
    return {"variant": scope, "domains": list(per.values()), "total": total, "max": mx, "missing": missing,
            "provisional": bool(missing),
            "text": _t("Csillagok: %s — összesen %d/%d" % (", ".join("%s=%d/%d" % (k, v["stars"], v["max"])
                                                                   for k, v in per.items()), total, mx),
                       "Stars: %s — total %d/%d" % (", ".join("%s=%d/%d" % (k, v["stars"], v["max"])
                                                            for k, v in per.items()), total, mx)),
            "note": _t("NINCS hivatalos küszöb: a 7–9 / 4–6 / 0–3 határok egy AHRQ-átváltásból jönnek. A doménenkénti "
                       "csillagokat közöld, ne csak az összeget.",
                       "There is NO official threshold: the 7–9 / 4–6 / 0–3 cut-offs come from an AHRQ conversion. "
                       "Report the per-domain stars, not the total alone.")}


def tripod_check(doc, instrument=None):
    """TRIPOD+AI jelentési teljesség (D/E szűréssel): altételenkénti státusz, közöltségi arány, hiánylista. Az üres
    sablon 0/52 (H1 őr: cellánként számol, nem sorszöveg-keresés)."""
    inst = instrument_for(doc if isinstance(doc, dict) else "tripod-ai", instrument)
    scope = inst.scope_of(doc.get("scope")) or inst.default_scope
    slots = inst.slots(scope)
    vals, _inv, _norm, _out = _values(doc, inst, slots)
    counts = collections.OrderedDict((k, 0) for k in ("present", "partial", "missing", "not_applicable",
                                                      "unanswered"))
    missing_items, partial_items, unassessed, by_section = [], [], [], collections.OrderedDict()
    for it in slots:
        v = vals.get(it["key"])
        st = v if v in counts else "unanswered"
        counts[st] += 1
        sec = by_section.setdefault(it["domain"], collections.OrderedDict(
            (k, 0) for k in ("present", "partial", "missing", "not_applicable", "unanswered")))
        sec[st] += 1
        row = {"item": it["id"], "applies_to": it.get("applies_to"), "status": v, "section": it["domain"]}
        if st == "missing":
            missing_items.append(row)
        elif st == "partial":
            partial_items.append(row)
        elif st == "unanswered":
            unassessed.append(row)
    assessed = counts["present"] + counts["partial"] + counts["missing"]
    rep = 100.0 * counts["present"] / assessed if assessed else None
    rep2 = 100.0 * (counts["present"] + counts["partial"]) / assessed if assessed else None
    answered = len(slots) - counts["unanswered"]
    sc = next((x for x in inst.scopes if x["id"] == scope), {})
    return {"scope": scope, "applies_to": sc.get("applies_to"),
            "expected": len(slots), "answered": answered, "assessed": assessed, "counts": dict(counts),
            "reported_pct": rep, "reported_or_partial_pct": rep2,
            "reported_text": ("%.1f%%" % rep) if rep is not None else "—",
            "completeness_text": "%d/%d" % (answered, len(slots)),
            "missing_items": missing_items + partial_items, "partial_items": partial_items,
            "not_reported": missing_items, "unassessed": unassessed,
            "by_section": [dict(section=k, **v) for k, v in by_section.items()],
            "note": _t("A TRIPOD+AI a jelentés teljességét méri, nem a módszertan helyességét.",
                       "TRIPOD+AI measures reporting completeness, not methodological soundness.")}


def _overrides(doc, domains, overall_implied, algorithm, provisional):
    """X017: emberi ítélet ≠ implikált → indoklás kell (és a naplóbeli döntés azonosítója)."""
    out = []
    by = {(d["domain"], d.get("pass")): d for d in domains}
    for dj in doc.get("domain_judgements") or ():
        if not isinstance(dj, dict) or not _nonempty(dj.get("judgement")):
            continue
        d = by.get((str(dj.get("domain")), dj.get("pass")))
        if not d or not d.get("implied") or d.get("algorithm") in ("none", None):
            continue
        if dj["judgement"] != d["implied"]:
            out.append({"domain": str(dj.get("domain")), "pass": dj.get("pass"), "judgement": dj["judgement"],
                        "implied": d["implied"], "algorithm": d["algorithm"],
                        "reason_missing": not _nonempty(dj.get("override_reason")),
                        "decision_missing": dj.get("decision_id") in (None, "")})
    ov = doc.get("overall") if isinstance(doc.get("overall"), dict) else None
    if ov and _nonempty(ov.get("judgement")) and overall_implied and algorithm not in ("none", "count") \
            and not provisional and ov["judgement"] != overall_implied:
        out.append({"domain": "overall", "pass": None, "judgement": ov["judgement"], "implied": overall_implied,
                    "algorithm": algorithm, "reason_missing": not _nonempty(ov.get("override_reason")),
                    "decision_missing": ov.get("decision_id") in (None, "")})
    return out


def check(doc, instrument=None, conventions=None, project_dir=None):
    """szk.appraisal-result/v1 egy értékelésre: teljesség (cellánként; PROBAST+AI-nál menetenként), érvénytelen
    válaszok, implikált doménítéletek és összítélet az eszköz 'algorithm' címkéjével, felülbírálások (X017), valamint
    az eszköz saját blokkja (amstar2 / grade / nos / tripod). conventions: a projekt konvenciói (ma-projekt.json
    'conventions'; alap: amstar2_partial_yes_critical = meets)."""
    if not isinstance(doc, dict):
        raise AppraisalError("Az értékelés JSON-objektum legyen.")
    inst = instrument_for(doc, instrument)
    if conventions is None and project_dir:
        try:
            from . import projekt
            conventions = (projekt.load_project_meta(project_dir) or {}).get("conventions")
        except Exception:                                   # noqa: BLE001
            conventions = None
    conventions = conventions or {}
    scope = inst.scope_of(doc.get("scope"))
    warnings = []
    if scope is None:
        warnings.append(_t("Ismeretlen hatókör (%r); lehet: %s." % (doc.get("scope"), ", ".join(inst.scope_ids)),
                           "Unknown scope (%r); allowed: %s." % (doc.get("scope"), ", ".join(inst.scope_ids))))
    slots = inst.slots(scope) if scope else []
    vals, invalid, normalized, outside = _values(doc, inst, slots)
    missing = [{"item": it["id"], "pass": it.get("pass"), "key": it["key"]} for it in slots if it["key"] not in vals]
    expected, answered = len(slots), len(slots) - len(missing)
    per_pass = None
    if inst.passes:
        per_pass = {}
        for ps in inst.passes:
            its = [it for it in slots if it.get("pass") == ps]
            if not its:
                continue
            n = sum(1 for it in its if it["key"] in vals)
            per_pass[ps] = {"expected": len(its), "answered": n, "complete": n == len(its),
                            "text": "%d/%d" % (n, len(its))}
    if normalized:
        warnings.append(_t("Nem kanonikus válaszérték(ek) álnévként értelmezve (mentés előtt normalizálódik): %s" % (
            ", ".join("%s: %s → %s" % (n["key"], n["from"], n["to"]) for n in normalized)),
            "Non-canonical answer value(s) read as aliases: %s" % ", ".join(
                "%s: %s → %s" % (n["key"], n["from"], n["to"]) for n in normalized)))
    if outside:
        warnings.append(_t("A hatókörön kívüli válaszok nem számítanak: %s" % ", ".join(outside),
                           "Answers outside the scope are ignored: %s" % ", ".join(outside)))
    alg = inst.algorithm
    domains, overall_implied, lines, provisional = [], None, [], False
    blocks = {"amstar2": None, "grade": None, "nos": None, "tripod": None}
    conv_out = {}
    if alg == "conservative":
        domains, overall_implied, lines = _conservative(inst, doc, slots, vals)
    elif inst.key == "amstar2":
        conv = conventions.get("amstar2_partial_yes_critical", "meets")
        conv = conv if conv in AMSTAR2_CONVENTIONS else "meets"
        blk = _amstar2_block(inst, slots, vals, conv)
        blocks["amstar2"] = blk
        overall_implied, provisional = blk["rating"], blk["provisional"]
        conv_out["amstar2.partial_yes_critical"] = blk["convention"]
        lines = [blk["text"]]
    elif inst.key == "grade":
        blk = _grade_block(inst, doc, slots, vals)
        blocks["grade"] = blk
        overall_implied, provisional = blk["certainty"], blk["provisional"] or bool(blk["unresolved"])
        conv_out["grade.publication_bias_suspected"] = "unresolved"
        for d in inst.domains:
            gk = d.get("grade_key")
            if gk in blk["domains"]:
                e = blk["domains"][gk]
                domains.append({"domain": str(d["id"]), "pass": None, "implied": None, "algorithm": "published",
                                "forced_by": [], "unknown_at": [], "routers": [],
                                "missing": [] if e["rating"] else [gk], "judgement": e["rating"],
                                "text": _t("%s: lépés %s%s" % (inst.domain_title(d["id"]),
                                                               "?" if e["step"] is None else "%+d" % e["step"],
                                                               " (FELOLDATLAN)" if e["status"] == "unresolved" else ""),
                                           "%s: step %s%s" % (inst.domain_title(d["id"], "en"),
                                                              "?" if e["step"] is None else "%+d" % e["step"],
                                                              " (UNRESOLVED)" if e["status"] == "unresolved" else ""))})
        lines = list(blk["lines"])
    elif alg == "count":
        blk = _nos_block(inst, slots, vals, scope)
        blocks["nos"] = blk
        provisional = blk["provisional"]
        lines = [blk["text"], blk["note"]]
    else:
        if inst.key == "tripod-ai":
            blocks["tripod"] = tripod_check(doc, inst)
            lines = [blocks["tripod"]["note"]]
        else:
            domains = _flags_only(inst, doc, slots, vals)
    ov = doc.get("overall") if isinstance(doc.get("overall"), dict) else {}
    overall = {"implied": overall_implied, "algorithm": alg, "official": bool(inst.rollup.get("official")),
               "basis": inst.rollup.get("basis"), "label": inst.rollup.get("label"), "text": inst.rollup.get("label"),
               "lines": lines, "provisional": provisional, "judgement": ov.get("judgement"),
               "level": inst.level(overall_implied) if overall_implied else None}
    overrides = _overrides(doc, domains, overall_implied, alg, provisional)
    notes = []
    if inst.rollup.get("note"):
        notes.append(inst.rollup["note"])
    if doc.get("origin") == "ai_draft":
        notes.append(_t("AI-vázlat: emberi jóváhagyásig csak javaslat, és SOHA nem számít második értékelőnek (6. "
                        "döntés).", "AI draft: only a suggestion until approved by a human, and NEVER a second rater "
                                    "(decision 6)."))
        ai_problems = []
        for key, a in (doc.get("answers") or {}).items():
            if isinstance(a, dict) and a.get("value") is not None:
                ai_problems.extend(_ai_item_problems(a, "answers[%s]" % key))
        if ai_problems:
            warnings.append(_t("Az AI-vázlat %d tételénél hiányzik a kötelező bizonyíték vagy egyszerű nyelvű "
                               "indoklás." % len(ai_problems),
                               "%d AI-draft item(s) lack the required evidence or plain-language rationale."
                               % len(ai_problems)))
    if any(o["reason_missing"] for o in overrides):
        warnings.append(_t("Az implikálttól eltérő ítélet indoklás nélkül (X017): %s" % ", ".join(
            ("összítélet" if o["domain"] == "overall" else "D%s%s" % (o["domain"], "/" + o["pass"] if o["pass"]
                                                                     else "")) for o in overrides
            if o["reason_missing"]), "Judgement differs from the implied one without a reason (X017)."))
    return {"schema": RESULT_SCHEMA, "tool": inst.key, "validator_version": None, "engine_version": __version__,
            "legacy": False, "instrument_sha256": inst.sha256, "scope": scope,
            "target": doc.get("target") if isinstance(doc.get("target"), dict) else {},
            "complete": not missing and not invalid and scope is not None, "expected": expected,
            "answered": answered, "completeness_text": "%d/%d" % (answered, expected), "per_pass": per_pass,
            "missing": missing, "invalid": invalid, "domains": domains, "overall": overall,
            "amstar2": blocks["amstar2"], "grade": blocks["grade"], "nos": blocks["nos"], "tripod": blocks["tripod"],
            "conventions": conv_out, "overrides": overrides, "guards": [], "notes": notes, "warnings": warnings}


appraisal_check = check


def override_problems(doc, result=None, instrument=None):
    """X017-jelöltek: [{domain, pass, judgement, implied, algorithm, reason_missing, decision_missing}]."""
    res = result if isinstance(result, dict) else check(doc, instrument)
    return list(res.get("overrides") or [])


def fill_implied(doc, result=None, instrument=None):
    """A dokumentum doménítéleteibe és összítéletébe beírja a motor implikált ítéletét (pillanatkép a mentéskor)."""
    res = result if isinstance(result, dict) else check(doc, instrument)
    out = copy.deepcopy(doc)
    by = {(d["domain"], d.get("pass")): d.get("implied") for d in res.get("domains") or ()}
    for dj in out.get("domain_judgements") or ():
        if isinstance(dj, dict):
            dj["implied"] = by.get((str(dj.get("domain")), dj.get("pass")))
    if isinstance(out.get("overall"), dict):
        out["overall"]["implied"] = (res.get("overall") or {}).get("implied")
    return out


# ------------------------------------------------------------------ felülbírálás, jóváhagyás
def set_judgement(doc, domain, judgement, reason=None, pass_=None, rationale=None, project_dir=None, actor=None,
                  instrument=None, coll="domain_judgements"):
    """Doménítélet vagy összítélet (domain='overall') rögzítése. Ha eltér az implikálttól, az indoklás kötelező (X017);
    project_dir megadásakor a felülbírálás döntésként a projektnaplóba kerül (agent 'user', S06), és a decision_id
    visszaíródik. → (új dokumentum, decision_id | None)."""
    inst = instrument_for(doc, instrument)
    if judgement is not None and judgement not in inst.verdicts:
        raise AppraisalError("Az ítélet az eszköz skálájának eleme legyen: %s." % " | ".join(inst.verdicts))
    out = copy.deepcopy(doc)
    res = check(out, inst)
    if domain == "overall":
        implied = (res.get("overall") or {}).get("implied")
        if (res.get("overall") or {}).get("provisional"):
            implied = None
        target = out.get("overall") if isinstance(out.get("overall"), dict) else {}
        out["overall"] = target
    else:
        if inst.domain(domain) is None:
            raise AppraisalError("Ismeretlen domén: %r" % (domain,))
        d = next((x for x in res.get("domains") or () if x["domain"] == str(domain) and x.get("pass") == pass_), None)
        implied = d.get("implied") if d else None
        lst = out.setdefault(coll, [])
        target = next((x for x in lst if isinstance(x, dict) and str(x.get("domain")) == str(domain)
                       and x.get("pass") == pass_), None)
        if target is None:
            target = {"domain": str(domain), "pass": pass_, "judgement": None, "implied": None, "rationale": None,
                      "override_reason": None, "decision_id": None}
            lst.append(target)
    target["judgement"] = judgement
    target["implied"] = implied
    if rationale is not None:
        target["rationale"] = rationale
    decision_id = None
    algorithm = (res.get("overall") or {}).get("algorithm")
    if judgement is not None and implied is not None and judgement != implied and algorithm not in ("none", "count"):
        if not _nonempty(reason):
            raise AppraisalError("Az ítélet (%s) eltér az implikálttól (%s): indoklás kötelező (X017)." % (
                inst.verdict_label(judgement), inst.verdict_label(implied)))
        target["override_reason"] = reason.strip()
        if project_dir:
            from . import projekt
            where = "összítélet" if domain == "overall" else "%s. domén%s" % (domain, " (%s)" % pass_ if pass_ else "")
            decision_id = projekt.log_decision(
                project_dir, "user", "Értékelés felülbírálása — %s %s, %s: %s → %s" % (
                    inst.name, unit_of(out) or "?", where, inst.verdict_label(implied), inst.verdict_label(judgement)),
                rationale=reason.strip(), stage="S06", actor=actor, context={"kind": "other", "code": "X017"})
            target["decision_id"] = decision_id
    else:
        target["override_reason"] = None
        target["decision_id"] = None
    return out, decision_id


def approve_ai_draft(doc, approver, now=None, instrument=None):
    """AI-vázlat emberi jóváhagyása (6. döntés): approved_by / approved_at; teljes vázlatnál status 'complete'.
    A jóváhagyott vázlat sem lesz értékelő (κ, konszenzus). → (új dokumentum, check-eredmény)."""
    if doc.get("origin") != "ai_draft":
        raise AppraisalError("Csak AI-vázlat (origin: ai_draft) hagyható jóvá.")
    if not (isinstance(approver, str) and RATER_RE.match(approver) and approver.lower() not in RESERVED_RATERS):
        raise AppraisalError("A jóváhagyó emberi értékelő monogramja kell (pl. SzK).")
    out = copy.deepcopy(doc)
    errs = []
    for key, a in (out.get("answers") or {}).items():
        if isinstance(a, dict) and a.get("value") is not None:
            errs.extend(_ai_item_problems(a, "answers[%s]" % key))
    if errs:
        raise AppraisalError("Az AI-vázlat nem hagyható jóvá: hiányzik a kötelező bizonyíték vagy indoklás.", errs)
    out["approved_by"] = approver
    out["approved_at"] = now or now_iso()
    res = check(out, instrument)
    if res["complete"]:
        out["status"] = "complete"
    return out, res


# ------------------------------------------------------------------ mentés és betöltés
def _atomic_write(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _canonical_bytes(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def save(project_dir, doc, instrument=None, now=None, expect_sha256=None):
    """Ellenőrzött, atomi mentés a 2.4 szerinti útra. Normalizál (álnév → kanonikus), kitölti az instrument_sha256,
    engine_version, created/updated mezőket és az implikált ítéletek pillanatképét. Lezárt státusznál (complete,
    consensus) a teljesség és az X017-indoklás is feltétel. expect_sha256: a meglévő fájl várt hash-e (optimista
    zárolás; None: nem ellenőrzi; '' : a fájl még nem létezhet).
    → {'path', 'sha256', 'doc', 'check'}"""
    inst = instrument_for(doc, instrument)
    out, _changes = normalize(doc, inst)
    ts = now or now_iso()
    out["schema"] = SCHEMA
    out["instrument_sha256"] = inst.sha256
    out["engine_version"] = __version__
    if not (isinstance(out.get("created"), str) and TS_RE.match(out["created"])):
        out["created"] = ts
    out["updated"] = ts
    if out.get("scope") in (None, ""):
        out["scope"] = inst.default_scope
    res = check(out, inst, project_dir=project_dir)
    out = fill_implied(out, res, inst)
    errs = list(problems(out, inst, project_dir)["errors"])
    if out.get("status") in FINAL_STATUSES:
        if not res["complete"]:
            errs.append("status: lezárt értékeléshez minden hatókörbe eső tétel válasza kell (%s; hiányzik: %s)" % (
                res["completeness_text"], ", ".join(m["key"] for m in res["missing"][:20])))
        miss = [o for o in res["overrides"] if o["reason_missing"]]
        if miss:
            errs.append("az implikálttól eltérő ítélet indoklás nélkül (X017): %s" % ", ".join(
                "összítélet" if o["domain"] == "overall" else "D%s%s" % (o["domain"], "/" + o["pass"] if o["pass"]
                                                                         else "") for o in miss))
    if errs:
        raise AppraisalError("Az értékelés nem menthető (%d hiba)." % len(errs), errs)
    rel = doc_relpath(out)
    path = os.path.join(project_dir, *rel.split("/"))
    if expect_sha256 is not None:
        have = None
        if os.path.exists(path):
            with open(path, "rb") as fh:
                have = hashlib.sha256(fh.read()).hexdigest()
        if (have or "") != (expect_sha256 or ""):
            raise AppraisalError("Az értékelés-fájl időközben megváltozott (%s); töltsd be újra." % rel)
    data = _canonical_bytes(out)
    _atomic_write(path, data)
    return {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "doc": out, "check": res}


def load(path):
    """Egy értékelés-fájl → dokumentum (dict). Hibás JSON → AppraisalError."""
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8-sig"), parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AppraisalError("Az értékelés-fájl nem olvasható (%s): %s" % (os.path.basename(path), exc)) from None
    if not isinstance(doc, dict):
        raise AppraisalError("Az értékelés-fájl gyökere objektum legyen: %s" % os.path.basename(path))
    return doc


def _reject_constant(token):
    raise ValueError("nem véges szám: %s" % token)


def list_appraisals(project_dir, tool=None):
    """A projekt értékelései: [{path, unit, tool, target, rater, doc | None, problems}] (név szerint rendezve)."""
    d = os.path.join(project_dir, *APPRAISAL_DIR.split("/"))
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        parsed = parse_name(name)
        if parsed is None or name.startswith("."):
            continue
        sl, tl, tg, rater = parsed
        if tool and tl != tool:
            continue
        rel = "%s/%s" % (APPRAISAL_DIR, name)
        try:
            doc = load(os.path.join(d, name))
            probs = []
        except AppraisalError as exc:
            doc, probs = None, [str(exc)]
        out.append({"path": rel, "unit": (unit_of(doc) if doc else None) or sl, "tool": tl, "target": tg,
                    "rater": rater, "doc": doc, "problems": probs})
    return out


# ------------------------------------------------------------------ egyezés (κ) és konszenzus
def cohen_kappa(pairs, level=0.95):
    """Cohen-féle κ névleges kategóriákra, aszimptotikus SE-vel (Fleiss, Cohen & Everitt 1969) és normál CI-vel.
    pairs: [(a, b)] → {n, categories, table, po, pe, kappa, se, ci} (κ None, ha nem értelmezhető: n = 0 vagy pe = 1)."""
    n = len(pairs)
    cats = sorted({a for a, _ in pairs} | {b for _, b in pairs})
    idx = {c: i for i, c in enumerate(cats)}
    table = [[0] * len(cats) for _ in cats]
    for a, b in pairs:
        table[idx[a]][idx[b]] += 1
    out = {"n": n, "categories": cats, "table": table, "po": None, "pe": None, "kappa": None, "se": None,
           "ci": None, "level": level}
    if n == 0:
        return out
    p = [[c / float(n) for c in row] for row in table]
    row = [sum(r) for r in p]
    col = [sum(p[i][j] for i in range(len(cats))) for j in range(len(cats))]
    po = sum(p[i][i] for i in range(len(cats)))
    pe = sum(row[i] * col[i] for i in range(len(cats)))
    out.update(po=po, pe=pe)
    if pe >= 1.0 - 1e-12:
        return out
    k = (po - pe) / (1.0 - pe)
    a_term = sum(p[i][i] * (1.0 - (row[i] + col[i]) * (1.0 - k)) ** 2 for i in range(len(cats)))
    b_term = (1.0 - k) ** 2 * sum(p[i][j] * (col[i] + row[j]) ** 2 for i in range(len(cats))
                                  for j in range(len(cats)) if i != j)
    c_term = (k - pe * (1.0 - k)) ** 2
    var = (a_term + b_term - c_term) / (n * (1.0 - pe) ** 2)
    se = math.sqrt(max(var, 0.0))
    z = Z95 if abs(level - 0.95) < 1e-12 else _z(level)
    out.update(kappa=k, se=se, ci=[max(-1.0, k - z * se), min(1.0, k + z * se)])
    return out


def _z(level):
    from .distributions import norm_ppf
    return norm_ppf(0.5 + level / 2.0)


def _kappa_text(k):
    if k["kappa"] is None:
        return _t("κ nem számolható (%s)" % ("nincs közös tétel" if not k["n"] else "egyetlen kategória"),
                  "κ not estimable (%s)" % ("no common item" if not k["n"] else "single category"))
    hu = "κ = %s [%s; %s]" % (_fmt(k["kappa"]), _fmt(k["ci"][0]), _fmt(k["ci"][1]))
    return _t(hu, _en_minus(hu))


def _require_rater(doc, side):
    if not isinstance(doc, dict):
        raise AppraisalError("Az %s értékelés JSON-objektum legyen." % side)
    if doc.get("origin") == "ai_draft":
        raise AppraisalError("AI-vázlat nem értékelő: sem a κ-ban, sem a konszenzusban nem szerepelhet (6. döntés).")
    if doc.get("status") == "consensus":
        raise AppraisalError("A konszenzus-változat nem független értékelés.")
    if not is_rater(doc):
        raise AppraisalError("Az %s értékelésnek emberi értékelője kell (assessor: monogram)." % side)


def agreement(pairs, instrument=None, level=0.95):
    """Tételszintű egyezés több (A, B) értékelés-páron összevonva → szk.ma.appraisal-agreement/v1. Csak független
    emberi értékelés (origin: human, nem konszenzus); az AI-vázlatot elutasítja."""
    pairs = list(pairs)
    if not pairs:
        raise AppraisalError("Legalább egy értékelés-pár kell.")
    inst = instrument_for(pairs[0][0], instrument)
    raters_a, raters_b = set(), set()
    cat_pairs, items, domains = [], [], []
    for doc_a, doc_b in pairs:
        _require_rater(doc_a, "A")
        _require_rater(doc_b, "B")
        if doc_a.get("tool") != inst.key or doc_b.get("tool") != inst.key:
            raise AppraisalError("A két értékelés eszköze eltér (%s, %s)." % (doc_a.get("tool"), doc_b.get("tool")))
        if doc_a.get("assessor") == doc_b.get("assessor"):
            raise AppraisalError("Ugyanaz az értékelő (%s) mindkét oldalon." % doc_a.get("assessor"))
        sa, sb = inst.scope_of(doc_a.get("scope")), inst.scope_of(doc_b.get("scope"))
        if sa != sb:
            raise AppraisalError("A két értékelés hatóköre eltér (%s, %s)." % (sa, sb))
        if (unit_of(doc_a), target_key(doc_a)) != (unit_of(doc_b), target_key(doc_b)):
            raise AppraisalError("A két értékelés más egységre vagy célra vonatkozik (%s, %s)." % (
                unit_of(doc_a), unit_of(doc_b)))
        raters_a.add(doc_a["assessor"])
        raters_b.add(doc_b["assessor"])
        slots = inst.slots(sa)
        va, _, _, _ = _values(doc_a, inst, slots)
        vb, _, _, _ = _values(doc_b, inst, slots)
        unit = unit_of(doc_a)
        for it in slots:
            a, b = va.get(it["key"]), vb.get(it["key"])
            items.append({"key": it["key"], "unit": unit, "domain": str(it["domain"]), "pass": it.get("pass"),
                          "a": a, "b": b, "agree": a is not None and a == b})
            if a is not None and b is not None:
                cat_pairs.append((a, b))
        for coll in ("domain_judgements", "applicability"):
            keys = []
            for dj in (doc_a.get(coll) or []) + (doc_b.get(coll) or []):
                if isinstance(dj, dict):
                    k = (str(dj.get("domain")), dj.get("pass"))
                    if k not in keys:
                        keys.append(k)
            for did, ps in keys:
                ja = (_judgement_of(doc_a, coll, did, ps) or {}).get("judgement")
                jb = (_judgement_of(doc_b, coll, did, ps) or {}).get("judgement")
                domains.append({"domain": did, "pass": ps, "unit": unit, "kind": coll, "a": ja, "b": jb,
                                "agree": ja is not None and ja == jb})
    k = cohen_kappa(cat_pairs, level)
    agree = sum(1 for a, b in cat_pairs if a == b)
    pct = 100.0 * agree / len(cat_pairs) if cat_pairs else None
    oa = ob = None
    if len(pairs) == 1:
        oa = ((pairs[0][0].get("overall") or {}) if isinstance(pairs[0][0].get("overall"), dict) else {}).get(
            "judgement")
        ob = ((pairs[0][1].get("overall") or {}) if isinstance(pairs[0][1].get("overall"), dict) else {}).get(
            "judgement")
    return {"schema": AGREEMENT_SCHEMA, "tool": inst.key, "a": ", ".join(sorted(raters_a)),
            "b": ", ".join(sorted(raters_b)), "pairs": len(pairs), "items_compared": len(cat_pairs), "agree": agree,
            "disagree": len(cat_pairs) - agree, "agreement_pct": pct,
            "agreement_pct_text": ("%.1f%%" % pct) if pct is not None else "—",
            "kappa": k["kappa"], "kappa_se": k["se"], "kappa_ci": k["ci"], "level": level,
            "kappa_method": "Cohen 1960; aszimptotikus SE: Fleiss, Cohen & Everitt 1969; normál CI",
            "kappa_text": _kappa_text(k), "categories": k["categories"], "table": k["table"], "items": items,
            "disagreements": [x for x in items if x["a"] is not None and x["b"] is not None and not x["agree"]],
            "domains": domains, "overall": {"a": oa, "b": ob, "agree": oa is not None and oa == ob} if len(pairs) == 1
            else None, "excluded": [],
            "notes": [_t("Csak a mindkét értékelőnél megválaszolt tételek számítanak; az AI-vázlat sosem értékelő.",
                         "Only items answered by both raters count; an AI draft is never a rater.")]}


def appraisal_consensus(doc_a, doc_b, instrument=None):
    """Két független emberi értékelés egyezése (egy pár) → szk.ma.appraisal-agreement/v1 (a munkapad
    konszenzus-nézete)."""
    return agreement([(doc_a, doc_b)], instrument)


def build_consensus(doc_a, doc_b, resolutions=None, judgements=None, instrument=None, now=None, paths=None):
    """Konszenzus-dokumentum két független emberi értékelésből. resolutions: {tételkulcs: {value, reason}} az
    eltérő tételekre; judgements: {'domain_judgements': [...], 'applicability': [...], 'overall': {...}} (ha
    hiányzik, az egyező ítéleteket veszi át). → (dokumentum, [feloldatlan tételkulcsok]); feloldatlan eltérésnél
    a státusz 'draft'."""
    agree = appraisal_consensus(doc_a, doc_b, instrument)
    inst = instrument_for(doc_a, instrument)
    resolutions = resolutions or {}
    ts = now or now_iso()
    answers, unresolved, records = {}, [], []
    ans_a = doc_a.get("answers") if isinstance(doc_a.get("answers"), dict) else {}
    for it in agree["items"]:
        key, a, b = it["key"], it["a"], it["b"]
        if a is not None and a == b:
            answers[key] = copy.deepcopy(ans_a.get(key)) if isinstance(ans_a.get(key), dict) else {"value": a}
            answers[key]["value"] = a
            continue
        if a is None and b is None:
            continue
        r = resolutions.get(key)
        rv = inst.canonical(r.get("value"), inst.item(key)) if isinstance(r, dict) else None
        if rv is None or not _nonempty((r or {}).get("reason")):
            unresolved.append(key)
            continue
        answers[key] = {"value": rv, "comment": r["reason"].strip()}
        records.append({"key": key, "a": a, "b": b, "value": rv, "reason": r["reason"].strip()})
    judgements = judgements or {}
    out_j = {}
    for coll in ("domain_judgements", "applicability"):
        if coll in judgements:
            out_j[coll] = copy.deepcopy(judgements[coll])
            continue
        lst = []
        for d in agree["domains"]:
            if d["kind"] == coll and d["agree"]:
                src = _judgement_of(doc_a, coll, d["domain"], d["pass"]) or {}
                lst.append({"domain": d["domain"], "pass": d["pass"], "judgement": d["a"], "implied": None,
                            "rationale": src.get("rationale"), "override_reason": src.get("override_reason"),
                            "decision_id": src.get("decision_id")})
            elif d["kind"] == coll:
                unresolved.append("%s:%s%s" % (coll, d["domain"], "/" + d["pass"] if d["pass"] else ""))
        out_j[coll] = lst
    overall = judgements.get("overall")
    if overall is None:
        ov_a = doc_a.get("overall") if isinstance(doc_a.get("overall"), dict) else {}
        ov_b = doc_b.get("overall") if isinstance(doc_b.get("overall"), dict) else {}
        if ov_a.get("judgement") is not None and ov_a.get("judgement") == ov_b.get("judgement"):
            overall = copy.deepcopy(ov_a)
        elif ov_a.get("judgement") is not None or ov_b.get("judgement") is not None:
            unresolved.append("overall")
    paths = list(paths or [None, None])
    doc = {"schema": SCHEMA, "tool": inst.key, "scope": inst.scope_of(doc_a.get("scope")),
           "instrument_sha256": inst.sha256, "target": copy.deepcopy(doc_a.get("target") or {}),
           "assessor": doc_a["assessor"], "second_assessor": doc_b["assessor"],
           "status": "draft" if unresolved else "consensus", "origin": "human", "approved_by": None,
           "approved_at": None, "answers": answers, "domain_judgements": out_j["domain_judgements"],
           "applicability": out_j["applicability"], "overall": overall,
           "consensus_of": [{"assessor": doc_a["assessor"], "path": paths[0], "updated": doc_a.get("updated")},
                            {"assessor": doc_b["assessor"], "path": paths[1], "updated": doc_b.get("updated")}],
           "resolutions": records, "engine_version": __version__, "created": ts, "updated": ts}
    return fill_implied(doc, None, inst), unresolved


# ------------------------------------------------------------------ forgalmi lámpa és rob-szinkron
def _fold(s):
    return _I.fold(s)


def _studies_maps(studies):
    """(fold(címke) → fold(study_id), fold(study_id) → címke) a szk.ma.studies/v1 dokumentumból."""
    l2i, i2l = {}, {}
    for s in (studies or {}).get("studies") or [] if isinstance(studies, dict) else []:
        if isinstance(s, dict) and isinstance(s.get("study_id"), str):
            if isinstance(s.get("label"), str) and s["label"].strip():
                l2i[_fold(s["label"])] = _fold(s["study_id"])
                i2l[_fold(s["study_id"])] = s["label"]
    return l2i, i2l


def _category(inst, verdict):
    """Ítélet → RoB-kategória (a tableio.rob_category-vel összevethető: low | some | high | no_info), vagy None."""
    lev = inst.level(verdict)
    return {"low": "low", "some": "some", "high": "high", "critical": "high", "ni": "no_info"}.get(lev)


def _finals(docs, inst, outcome=None):
    """Egységenként a végső értékelés (az X003 audit logikája): konszenzus (legutóbbi) > egyező lezárt emberi
    értékelések > jóváhagyott AI-vázlat; eltérő lezárt értékeléseknél 'conflict'; csak vázlatnál 'draft'.
    → OrderedDict(fold(egység) → {unit, doc, basis, files, assessors, conflict})"""
    groups = collections.OrderedDict()
    for i, (doc, path) in enumerate(docs):
        if not isinstance(doc, dict) or doc.get("tool") != inst.key:
            continue
        tg = doc.get("target") if isinstance(doc.get("target"), dict) else {}
        if outcome is not None and isinstance(tg.get("outcome"), str) and tg["outcome"] != outcome:
            continue
        u = unit_of(doc)
        if u is None:
            continue
        groups.setdefault(_fold(u), []).append((doc, path))
    out = collections.OrderedDict()
    for key, items in groups.items():
        unit = unit_of(items[0][0])
        cons = [(d, p) for d, p in items if d.get("status") == "consensus" and d.get("origin", "human") == "human"]
        human = [(d, p) for d, p in items if d.get("status") == "complete" and d.get("origin", "human") == "human"]
        ai_ok = [(d, p) for d, p in items if d.get("origin") == "ai_draft" and d.get("status") == "complete"
                 and _nonempty(d.get("approved_by"))]
        drafts = [(d, p) for d, p in items if d.get("status") == "draft"]
        entry = {"unit": unit, "doc": None, "basis": None, "files": [p for _, p in items if p],
                 "assessors": sorted({d.get("assessor") for d, _ in items if isinstance(d.get("assessor"), str)}),
                 "conflict": False}
        if cons:
            pick = sorted(cons, key=lambda x: str(x[0].get("updated") or ""))[-1]
            entry.update(doc=pick[0], basis="consensus", source=pick[1])
        elif human or ai_ok:
            pool = human or ai_ok
            cats = {_category(inst, ((d.get("overall") or {}) if isinstance(d.get("overall"), dict) else {}).get(
                "judgement")) for d, _ in pool}
            if len(cats) == 1:
                pick = sorted(pool, key=lambda x: str(x[0].get("updated") or ""))[-1]
                basis = ("agreeing" if len(pool) > 1 else "single") if human else "ai_approved"
                entry.update(doc=pick[0], basis=basis, source=pick[1])
            else:
                pick = sorted(pool, key=lambda x: str(x[0].get("updated") or ""))[-1]
                entry.update(doc=pick[0], basis="conflict", conflict=True, source=pick[1])
        elif drafts:
            pick = sorted(drafts, key=lambda x: str(x[0].get("updated") or ""))[-1]
            entry.update(doc=pick[0], basis="draft", source=pick[1])
        else:
            continue
        out[key] = entry
    return out


def _pair_docs(appraisals, paths):
    docs = []
    paths = list(paths or [])
    for i, a in enumerate(appraisals or []):
        if isinstance(a, tuple) and len(a) == 2:
            docs.append((a[0], a[1]))
        elif isinstance(a, dict) and isinstance(a.get("doc"), dict) and "schema" not in a:
            docs.append((a["doc"], a.get("path")))
        else:
            docs.append((a, paths[i] if i < len(paths) else None))
    return docs


def rob_summary(appraisals, tool, outcome=None, plot=None, studies=None, paths=None, instrument=None):
    """szk.rob-summary/v1: vizsgálatonként a doménítéletek (emberi ítélet, ennek hiányában az implikált — jelölve),
    az összítélet és a súlyarány egy futás plot_data.json-jából (szk.ma.plot/v2 studies[].weight_pct; a sorok a
    study_id, a címke vagy a studies.json alapján párosulnak). appraisals: dokumentumok (vagy {doc, path})."""
    inst = instrument_for(tool, instrument)
    docs = _pair_docs(appraisals, paths)
    finals = _finals(docs, inst, outcome)
    l2i, i2l = _studies_maps(studies)
    ps_list = [None] + inst.passes
    headers = []
    for d in inst.domains:
        for ps in ps_list:
            if any(str(it["domain"]) == str(d["id"]) and it.get("pass") == ps for it in inst.items):
                headers.append({"id": str(d["id"]), "pass": ps, "title": d.get("title"),
                                "short": "%s%s" % (d.get("short") or d["id"], ("/" + ps[0].upper()) if ps else "")})
    weights, rows_of, labels, unmatched = {}, {}, {}, []
    total_w = 0.0
    for s in (plot or {}).get("studies") or [] if isinstance(plot, dict) else []:
        if not isinstance(s, dict):
            continue
        w = s.get("weight_pct", s.get("weight"))
        w = float(w) if isinstance(w, (int, float)) and not isinstance(w, bool) and math.isfinite(w) else None
        if w is not None:
            total_w += w
        keys = [_fold(x) for x in (s.get("study_id"), s.get("label")) if isinstance(x, str) and x.strip()]
        if isinstance(s.get("label"), str) and _fold(s["label"]) in l2i:
            keys.append(l2i[_fold(s["label"])])
        hit = next((k for k in keys if k in finals), None)
        if hit is None:
            unmatched.append({"row_uid": s.get("row_uid"), "study_id": s.get("study_id"), "label": s.get("label"),
                              "weight_pct": w})
            continue
        if w is not None:
            weights[hit] = weights.get(hit, 0.0) + w
        rows_of.setdefault(hit, []).append(s.get("row_uid"))
        labels.setdefault(hit, s.get("label"))
    studies_out = []
    for key, f in finals.items():
        doc = f["doc"]
        res = check(doc, inst)
        imp = {(d["domain"], d.get("pass")): d.get("implied") for d in res["domains"]}
        doms, jmap = [], {}
        for h in headers:
            dj = _judgement_of(doc, "domain_judgements", h["id"], h["pass"]) or {}
            j = dj.get("judgement")
            im = imp.get((h["id"], h["pass"]))
            shown = j if j is not None else im
            doms.append({"domain": h["id"], "pass": h["pass"], "judgement": j, "implied": im,
                         "level": inst.level(shown) if shown else None,
                         "from": "judgement" if j is not None else ("implied" if im is not None else None)})
            jmap[("%s/%s" % (h["pass"], h["id"])) if h["pass"] else h["id"]] = shown
        ov = (doc.get("overall") or {}) if isinstance(doc.get("overall"), dict) else {}
        oj, oi = ov.get("judgement"), res["overall"].get("implied")
        shown = oj if oj is not None else oi
        w = weights.get(key)
        rob_val = inst.rob_value(oj) if oj is not None and f["basis"] not in ("draft", "conflict") else None
        studies_out.append({
            "study_id": f["unit"], "label": i2l.get(key) or labels.get(key) or f["unit"],
            "weight_pct": w, "weight_text": ("%.1f%%" % w) if w is not None else None,
            "rows": [r for r in rows_of.get(key, []) if isinstance(r, str)], "judgements": jmap, "domains": doms,
            "overall": oj, "overall_implied": oi, "level": inst.level(shown) if shown else None,
            "source": f["basis"], "status": doc.get("status"), "origin": doc.get("origin"),
            "assessors": f["assessors"], "files": f["files"], "complete": res["complete"], "rob_value": rob_val})
    weighted = []
    for lev in LEVEL_ORDER + ("unassessed",):
        if lev == "unassessed":
            vs_w = sum(u["weight_pct"] or 0.0 for u in unmatched)
            n = len(unmatched)
        else:
            vs = [s for s in studies_out if s["level"] == lev and s["source"] not in ("draft", "conflict")]
            vs_w = sum(s["weight_pct"] or 0.0 for s in vs)
            n = len(vs)
        if lev in ("critical", "ni", "unassessed") and n == 0:
            continue
        pct = 100.0 * vs_w / total_w if total_w > 0 else None
        weighted.append({"level": lev, "n": n, "pct": pct, "text": ("%.1f%%" % pct) if pct is not None else None})
    high = [w["pct"] for w in weighted if w["level"] in ("high", "critical") and w["pct"] is not None]
    notes = [inst.rollup.get("label") or _t(inst.algorithm)]
    if plot is None:
        notes.append(_t("Súlyok nélkül: nincs megadott futás (plot_data.json).", "No weights: no run given."))
    if any(s["source"] == "conflict" for s in studies_out):
        notes.append(_t("Eltérő lezárt értékelések konszenzus nélkül: ezeknél előbb egyeztess.",
                        "Conflicting final appraisals without consensus: reconcile first."))
    return {"schema": SUMMARY_SCHEMA, "tool": inst.key, "outcome": outcome, "name": inst.name,
            "instrument_sha256": inst.sha256, "algorithm": inst.algorithm, "engine_version": __version__,
            "domains": headers, "scale": copy.deepcopy(inst.doc.get("verdicts") or []), "studies": studies_out,
            "weighted": weighted, "high_weight_pct": sum(high) if high and total_w > 0 else (0.0 if total_w > 0
                                                                                            else None),
            "weights_source": ({"run_id": ((plot.get("meta") or {}).get("run_id") if isinstance(plot.get("meta"),
                                                                                                dict) else None),
                                "total_weight_pct": total_w} if isinstance(plot, dict) else None),
            "unmatched_plot_rows": unmatched, "notes": notes}


appraisal_rob_summary = rob_summary


def rob_sync_proposal(appraisals, header, rows, tool, row_uids=None, studies=None, column=None, paths=None,
                      instrument=None):
    """szk.ma.rob-sync-proposal/v1: a kinyerési tábla rob oszlopának javasolt cellái a végső összítéletből (az X003
    audit párosításával: study_id, study-címke, studies.json címke → study_id). Csak eltérő kategória vált
    változássá (a 'High risk of bias' és a 'high' egy kategória). A javasolt értékek eszközönként: low / some /
    high / critical (ROBINS-I 'nincs információ': 'no information'). A cellák eredete 'calculated'."""
    from . import tableio
    inst = instrument_for(tool, instrument)
    header = [("" if h is None else str(h)) for h in (header or [])]
    rows = [list(r) for r in (rows or [])]
    cmap = tableio.column_map(header)
    sid_i = header.index(cmap["study_id"]) if cmap.get("study_id") in header else None
    lab_i = header.index(cmap["study"]) if cmap.get("study") in header else None
    rob_name = cmap.get("rob") if cmap.get("rob") in header else None
    rob_i = header.index(rob_name) if rob_name is not None else None
    col = rob_name or column or "rob"
    out = {"schema": SYNC_SCHEMA, "tool": inst.key, "column": col, "column_exists": rob_i is not None,
           "changes": [], "unchanged": 0, "unmatched": [], "conflicts": [], "skipped": [], "warnings": []}
    if not inst.rob_column:
        out["warnings"].append(_t("A(z) %s nem torzításikockázat-eszköz: a rob oszlop nem szinkronizálható." %
                                  inst.name, "%s is not a risk-of-bias tool: no rob sync." % inst.name))
        return out
    if row_uids is None:
        try:
            parsed, meta = tableio.parse_table(header, [[("" if c is None else str(c)) for c in r] for r in rows])
            row_uids = tableio.row_uids(parsed, meta)
        except Exception:                                   # noqa: BLE001 — azonosító nélkül is javasolható
            row_uids = [None] * len(rows)
    finals = _finals(_pair_docs(appraisals, paths), inst)
    l2i, _i2l = _studies_maps(studies)
    seen_studies = set()
    for i, cells in enumerate(rows):
        def cell(j):
            return str(cells[j]).strip() if j is not None and j < len(cells) and cells[j] is not None else ""
        sid, lab = cell(sid_i), cell(lab_i)
        keys = [_fold(x) for x in (sid, lab) if x]
        if lab and _fold(lab) in l2i:
            keys.append(l2i[_fold(lab)])
        uid = row_uids[i] if i < len(row_uids) else None
        hit = next((k for k in keys if k in finals), None)
        if hit is None:
            out["unmatched"].append({"row": i, "row_uid": uid, "study_id": sid or None, "label": lab or None})
            continue
        seen_studies.add(hit)
        f = finals[hit]
        if f["basis"] == "conflict":
            out["conflicts"].append({"row": i, "row_uid": uid, "study_id": f["unit"], "files": f["files"]})
            continue
        if f["basis"] == "draft":
            out["skipped"].append({"row": i, "row_uid": uid, "study_id": f["unit"],
                                   "reason": _t("csak vázlat-értékelés van (nincs lezárt / konszenzusos)",
                                                "only draft appraisals (no final / consensus)")})
            continue
        doc = f["doc"]
        judgement = ((doc.get("overall") or {}) if isinstance(doc.get("overall"), dict) else {}).get("judgement")
        after = inst.rob_value(judgement) if judgement else None
        if not after:
            out["skipped"].append({"row": i, "row_uid": uid, "study_id": f["unit"],
                                   "reason": _t("nincs rögzített összítélet" if not judgement else
                                                "az összítéletnek nincs rob-megfelelője",
                                                "no overall judgement recorded" if not judgement else
                                                "no rob mapping for the overall judgement")})
            continue
        before = cell(rob_i)
        if before and tableio.rob_category(before) == tableio.rob_category(after):
            out["unchanged"] += 1
            continue
        out["changes"].append({"row_uid": uid, "row": i, "study_id": f["unit"], "label": lab or None,
                               "before": before, "after": after, "judgement": judgement, "basis": f["basis"],
                               "source": f.get("source"), "files": f["files"],
                               "provenance": {"method": "calculated", "derived_from": "appraisal", "tool": inst.key,
                                              "files": f["files"]}})
    missing_rows = [f["unit"] for k, f in finals.items() if k not in seen_studies and f["basis"] not in ("draft",)]
    if missing_rows:
        out["warnings"].append(_t("Értékelt vizsgálat, amelynek nincs sora a táblában: %s" % ", ".join(missing_rows),
                                  "Appraised studies without a table row: %s" % ", ".join(missing_rows)))
    if not out["column_exists"] and out["changes"]:
        out["warnings"].append(_t("A táblában nincs rob oszlop: alkalmazáskor '%s' néven jön létre." % col,
                                  "The table has no rob column: '%s' is added when applied." % col))
    return out


# ------------------------------------------------------------------ projektszintű segédek (CLI: appraisal …)
def _project_docs(project_dir, tool):
    return [(e["doc"], e["path"]) for e in list_appraisals(project_dir, tool) if e["doc"] is not None]


def _load_json(path):
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8-sig"), parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _outcome_data(project_dir, outcome):
    from . import projekt
    meta = projekt.load_project_meta(project_dir)
    for oc in (meta or {}).get("outcomes") or []:
        if isinstance(oc, dict) and oc.get("id") == outcome:
            return oc.get("data"), meta
    raise AppraisalError("A(z) %r kimenet nincs a ma-projekt.json-ban." % (outcome,))


def default_tool(project_dir):
    """A projekt RoB-eszköze: a ma-projekt.json appraisal_tools első rob-eszköze, különben a leggyakoribb
    rob-eszköz az értékelés-fájlok között."""
    try:
        from . import projekt
        meta = projekt.load_project_meta(project_dir) or {}
    except Exception:                                       # noqa: BLE001
        meta = {}
    for t in meta.get("appraisal_tools") or []:
        if t in _I.available() and _I.load(t).rob_column:
            return t
    cnt = collections.Counter(e["tool"] for e in list_appraisals(project_dir) if e["tool"] in _I.available()
                              and _I.load(e["tool"]).rob_column)
    return cnt.most_common(1)[0][0] if cnt else None


def project_rob_summary(project_dir, outcome, tool=None, plot=None):
    """rob_summary a projekt fájljaiból. plot: dict, vagy a plot_data.json / run.json útja (a projekthez képest is)."""
    tool = tool or default_tool(project_dir)
    if not tool:
        raise AppraisalError("Nincs RoB-eszköz a projektben (appraisal_tools, illetve értékelés-fájlok).")
    if isinstance(plot, str):
        p = plot if os.path.isabs(plot) else os.path.join(project_dir, plot)
        if os.path.isdir(p):
            p = os.path.join(p, "plot_data.json")
        doc = _load_json(p)
        if isinstance(doc, dict) and doc.get("schema") == "szk.ma.run/v1":
            rel = ((doc.get("files") or {}).get("plot") or {}).get("path")
            doc = _load_json(os.path.join(project_dir, rel)) if isinstance(rel, str) else None
        if not isinstance(doc, dict):
            raise AppraisalError("A futás plot_data.json-ja nem olvasható: %s" % plot)
        plot = doc
    studies = _load_json(os.path.join(project_dir, *STUDIES_FILE.split("/")))
    return rob_summary(_project_docs(project_dir, tool), tool, outcome=outcome, plot=plot, studies=studies)


def project_rob_sync(project_dir, outcome, tool=None, column=None):
    """rob_sync_proposal a kimenet adattáblájára (+ 'table', 'table_sha256' az optimista alkalmazáshoz)."""
    from . import tableio
    tool = tool or default_tool(project_dir)
    if not tool:
        raise AppraisalError("Nincs RoB-eszköz a projektben (appraisal_tools, illetve értékelés-fájlok).")
    data, _meta = _outcome_data(project_dir, outcome)
    if not isinstance(data, str):
        raise AppraisalError("A(z) %s kimenetnek nincs adattáblája (outcomes[].data)." % outcome)
    path = os.path.join(project_dir, *data.split("/"))
    header, rows, fmt = tableio.read_raw(path)
    with open(path, "rb") as fh:
        raw = fh.read()
    parsed, meta = tableio.read_table_bytes(raw, path)
    uids = tableio.row_uids(parsed, meta)
    studies = _load_json(os.path.join(project_dir, *STUDIES_FILE.split("/")))
    prop = rob_sync_proposal(_project_docs(project_dir, tool), header, rows, tool, row_uids=uids, studies=studies,
                             column=column, instrument=None)
    prop["table"] = data
    prop["table_sha256"] = fmt.get("sha256")
    prop["outcome"] = outcome
    return prop


def apply_rob_sync(project_dir, proposal, actor=None, now=None):
    """A javaslat alkalmazása (CLI 'appraisal sync-rob --apply'): formátumtartó, atomi CSV-írás, majd — ha van — a
    .prov.json frissítése (method: calculated, forrás: az értékelés-fájl; table_sha256 = az új CSV hash-e). A tábla
    hash-ének egyeznie kell a javaslatéval (különben a tábla időközben változott). → {applied, table, table_sha256,
    provenance}"""
    from . import tableio
    rel = proposal.get("table")
    if not isinstance(rel, str):
        raise AppraisalError("A javaslatban nincs tábla (project_rob_sync kimenete kell).")
    path = os.path.join(project_dir, *rel.split("/"))
    header, rows, fmt = tableio.read_raw(path)
    if proposal.get("table_sha256") and fmt.get("sha256") != proposal["table_sha256"]:
        raise AppraisalError("A tábla a javaslat óta megváltozott (%s): kérj új javaslatot." % rel)
    changes = [c for c in proposal.get("changes") or [] if isinstance(c, dict)]
    if not changes:
        return {"applied": 0, "table": rel, "table_sha256": fmt.get("sha256"), "provenance": None}
    cmap = tableio.column_map(header)
    name = cmap.get("rob") if cmap.get("rob") in header else None
    if name is None:
        header = list(header) + [proposal.get("column") or "rob"]
        idx = len(header) - 1
    else:
        idx = header.index(name)
    for r in rows:
        while len(r) < len(header):
            r.append("")
    for c in changes:
        i = c.get("row")
        if not isinstance(i, int) or not 0 <= i < len(rows):
            raise AppraisalError("Érvénytelen sorindex a javaslatban: %r" % (i,))
        rows[i][idx] = c["after"]
    new_sha = tableio.write_raw(path, header, rows, fmt)
    prov_rel = re.sub(r"\.[^./]+$", "", rel) + ".prov.json"
    prov_path = os.path.join(project_dir, *prov_rel.split("/"))
    prov_out = None
    if os.path.isfile(prov_path):
        prov = _load_json(prov_path)
        if isinstance(prov, dict) and isinstance(prov.get("cells"), list):
            ts = now or now_iso()
            cells = [dict(c) for c in prov["cells"] if isinstance(c, dict)]
            by_key = {(c.get("row_uid"), c.get("field")): i for i, c in enumerate(cells)}
            for ch in changes:
                if not ch.get("row_uid"):
                    continue
                entry = {"row_uid": ch["row_uid"], "field": "rob", "value_as_entered": ch["after"],
                         "method": "calculated", "estimated": False,
                         "source": {"doc": None, "page": None, "locator": ch.get("source")},
                         "extracted_by": actor, "extracted_at": ts}
                k = (ch["row_uid"], "rob")
                if k in by_key:
                    old = cells[by_key[k]]
                    hist = list(old.get("history") or [])
                    hist.append({kk: old.get(kk) for kk in ("value_as_entered", "method", "extracted_by",
                                                            "extracted_at")})
                    entry["history"] = hist[-50:]
                    cells[by_key[k]] = entry
                else:
                    by_key[k] = len(cells)
                    cells.append(entry)
            prov = dict(prov, cells=cells, table_sha256=new_sha)
            _atomic_write(prov_path, (json.dumps(prov, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
                          .encode("utf-8"))
            prov_out = prov_rel
    return {"applied": len(changes), "table": rel, "table_sha256": new_sha, "provenance": prov_out}


# ------------------------------------------------------------------ eszköz-javaslat (a validator --route mintájára)
_ROUTES = (
    (r"\b(prediction model|prognostic model|risk score|nomogram|machine learning model|ai model|algorithm "
     r"validation|diagnostic model|predikci[oó]s modell|kock[aá]zati pontsz[aá]m)", "probast-ai",
     _t("predikciós modell — minőség és torzítási kockázat", "prediction model — quality and risk of bias")),
    (r"\b(tripod|reporting completeness|jelent[eé]si teljess[eé]g)", "tripod-ai",
     _t("predikciós modell — a jelentés teljessége", "prediction model — reporting completeness")),
    (r"\b(diagnostic (test )?accuracy|sensitivity and specificity|index test|reference standard|dta|diagnosztikai "
     r"pontoss[aá]g|indexteszt)", "quadas2", _t("diagnosztikai pontossági vizsgálat", "diagnostic accuracy study")),
    (r"\b(prognostic factor|prognostic marker|prognosztikai t[eé]nyez)", "quips",
     _t("prognosztikai tényezős vizsgálat", "prognostic factor study")),
    (r"\b(umbrella review|overview of reviews|systematic review|meta-analys|szisztematikus [aá]ttekint|metaanal)",
     "amstar2", _t("szisztematikus áttekintés — módszertani minőség", "systematic review — methodological quality")),
    (r"\b(rct|randomi[sz]ed|randomiz[aá]lt|cluster.?randomi|crossover)", "rob2",
     _t("randomizált vizsgálat", "randomised trial")),
    (r"\b(non.?randomi[sz]ed|nrsi|quasi.?experimental|interrupted time series|before.?after|nem randomiz[aá]lt)",
     "robins-i", _t("nem randomizált beavatkozásos vizsgálat", "non-randomised study of an intervention")),
    (r"\b(exposure|environmental|occupational|expoz[ií]ci[oó]|k[oö]rnyezeti|foglalkoz[aá]si)", "robins-e",
     _t("expozíciós megfigyeléses vizsgálat", "observational study of an exposure")),
    (r"\b(cohort|case.?control|kohorsz|eset.?kontroll)", "nos",
     _t("kohorsz vagy eset-kontroll vizsgálat (csillagrendszer; ha lehet, inkább ROBINS-I / ROBINS-E)",
        "cohort or case-control study (star system; prefer ROBINS-I / ROBINS-E)")),
    (r"\b(cross.?sectional|case series|case report|prevalence|qualitative|keresztmetszeti|esetsorozat|"
     r"esetismertet|prevalencia|kvalitat[ií]v)", "jbi", _t("JBI-ellenőrzőlista (elrendezés szerint)",
                                                           "JBI checklist (by design)")),
    (r"\b(certainty of evidence|summary of findings|grade|bizonyoss[aá]g)", "grade",
     _t("a bizonyíték bizonyossága kimenetenként", "certainty of evidence per outcome")),
)
_DESIGN_CODES = {"rct": "rob2", "rct_parallel": "rob2", "rct_cluster": "rob2", "rct_crossover": "rob2",
                 "nrsi": "robins-i", "cohort": "robins-i", "case_control": "robins-i", "exposure": "robins-e",
                 "diagnostic": "quadas2", "dta": "quadas2", "prognostic_factor": "quips",
                 "prediction_model": "probast-ai", "cross_sectional": "jbi", "case_series": "jbi",
                 "case_report": "jbi", "prevalence": "jbi", "qualitative": "jbi", "systematic_review": "amstar2"}


def instrument_route(design):
    """Eszköz-javaslat a vizsgálati elrendezésből (szabad szöveg magyarul/angolul, vagy studies.json design-kód) →
    [{tool, name, why}]. Több találat normális (pl. PROBAST+AI és TRIPOD+AI); a választás a kérdésé."""
    text = str(design or "")
    hits, seen = [], set()
    code = _fold(text).replace(" ", "_")
    if code in _DESIGN_CODES:
        t = _DESIGN_CODES[code]
        seen.add(t)
        hits.append({"tool": t, "name": _I.load(t).name, "why": _t("elrendezés-kód: %s" % code,
                                                                     "design code: %s" % code)})
    for pattern, tool, why in _ROUTES:
        if tool not in seen and re.search(pattern, text, re.I):
            seen.add(tool)
            hits.append({"tool": tool, "name": _I.load(tool).name, "why": why})
    return hits


# ------------------------------------------------------------------ eszköz-lista a homlokzatnak
def instruments_list():
    """Az eszközök összefoglalója (a munkapad eszköz-listája)."""
    return _I.index()


def instrument_get(tool):
    """Az eszköz szk.instrument/v1 definíciója (+ 'sha256'; tételenként a 'scopes' lista 'scope' néven is — a munkapad
    űrlapja ezt a nevet olvassa); ismeretlen eszköz → KeyError."""
    try:
        out = _I.definition(tool)
    except _I.InstrumentError as exc:
        raise KeyError(str(exc)) from None
    for it in out.get("items") or []:
        if isinstance(it.get("scopes"), list):
            it.setdefault("scope", list(it["scopes"]))
    return out
