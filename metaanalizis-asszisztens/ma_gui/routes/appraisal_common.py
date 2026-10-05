# -*- coding: utf-8 -*-
"""Közös rész az értékelés-végpontokhoz (terv 2.4, 3.4, 4.11, 5.4, 6.5, 11. fejezet 4–6. döntés).

**Motor-kötés (feature detection).** Az értékelő eszközök definíciói, a teljesség, az implikált ítélet, az
egyezés (κ), a forgalmi lámpa adatai és a rob-oszlop szinkron-javaslata a MOTOR függvényei (``metaelemzes.api``;
a v1 motor-munkafolyamat írja). A munkapad egyetlen ítéletet sem számol: ha egy homlokzat-függvény hiányzik, a
végpont 424 CAPABILITY_MISSING-et ad magyar üzenettel (a ``need``). Az elvárt nevek és az álnevek az ``ENGINE``
táblában; a végleges összekötést az integrátor itt, egy helyen igazítja.

**Tárolás (2.4).** ``04_torzitas_kockazat/appraisals/<egység>.<eszköz>[.<cél>].<értékelő>.json``
(``szk.appraisal/v1``). Az egység a vizsgálat azonosítója (studies.json ``study_id``) vagy a fenntartott
``review`` (AMSTAR 2: a saját áttekintés) és ``manuscript`` (TRIPOD+AI: a saját kézirat); a fájlnévben a
biztonságos alakja (slug) áll, a valódi azonosító a dokumentum ``target.study_id``/``target.unit`` mezőjében.
Fenntartott értékelők: ``consensus`` (a két független értékelés egyeztetett változata, ``status: consensus``) és
``ai`` (Claude-vázlat, ``origin: ai_draft``; 6. döntés: csak emberi jóváhagyással használható, és SOHA nem
számít értékelőnek — sem κ-ban, sem konszenzusban).

Adatvédelem (T10): naplóba és activity-be értékelés-szöveg (válasz, indoklás, idézet) nem kerül, csak út,
sha256, eszköz, egység és darabszám; a szabad szöveg mentés előtt a PHI-mintákon megy át."""
import re

from metaelemzes import api

from .. import security, store
from ..router import ApiError
from ._common import accepts

SCHEMA_DOC = "szk.appraisal/v1"
SCHEMA_RESULT = "szk.appraisal-result/v1"
SCHEMA_INSTRUMENT = "szk.instrument/v1"
SCHEMA_BUNDLE = "szk.appraisal-bundle/v1"
APPRAISAL_DIR = "04_torzitas_kockazat/appraisals"
INBOX_DIR = APPRAISAL_DIR + "/beerkezett"
MAX_FILES = 2000
MAX_IMPORT_BYTES = 4 * 1024 * 1024
MAX_TEXT = 4000
MAX_QUOTE = 1000

UNIT_REVIEW = "review"
UNIT_MANUSCRIPT = "manuscript"
SPECIAL_UNITS = (UNIT_REVIEW, UNIT_MANUSCRIPT)
RESERVED_SEGMENTS = ("consensus", "inbox", "import", "export", "rob-summary", "rob-sync", "agreement")
RATER_CONSENSUS = "consensus"
RATER_AI = "ai"
RESERVED_RATERS = (RATER_CONSENSUS, RATER_AI)
STATUSES = ("draft", "complete", "consensus")
ORIGINS = ("human", "ai_draft")
PASSES = ("development", "evaluation")

TOOL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RATER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ANSWER_KEY_RE = re.compile(r"^((development|evaluation)/)?[0-9A-Za-z.\-]+$")
_SLUG_BAD = re.compile(r"[^A-Za-z0-9_-]+")
_FILE_RE = re.compile(r"^([A-Za-z0-9_-]{1,64})\.([a-z0-9][a-z0-9-]{0,31})(?:\.([A-Za-z0-9][A-Za-z0-9_-]{0,63}))?"
                      r"\.([A-Za-z][A-Za-z0-9_-]{0,31})\.json$")


# ---------------------------------------------------------------------------- motor-kötés
# kulcs → (elvárt név, álnevek, magyar leírás). A munkapad CSAK ezeken át hívja a motort (6.8: metaelemzes.api).
ENGINE = {
    "instruments": ("instruments_list", ("instrument_list", "list_instruments", "instruments"),
                    "az értékelő eszközök listája"),
    "instrument": ("instrument_get", ("get_instrument", "instrument", "load_instrument"),
                   "az értékelő eszköz definíciója (szk.instrument/v1)"),
    "check": ("appraisal_check", ("check_appraisal", "appraisal_verify"),
              "az értékelés teljessége és az implikált ítéletek (szk.appraisal-result/v1)"),
    "completeness": ("appraisal_completeness", (), "az értékelés teljessége"),
    "implied": ("appraisal_implied", (), "az implikált ítéletek"),
    "tripod": ("tripod_check", (), "a TRIPOD+AI jelentési teljessége"),
    "amstar2": ("amstar2_consistency", (), "az AMSTAR 2 besorolása (mindkét konvencióval)"),
    "validate": ("appraisal_validate", ("validate_appraisal", "appraisal_problems"),
                 "az értékelés szerkezeti és tartalmi ellenőrzése (6. döntés: AI-vázlat indoklása; 4. döntés)"),
    "consensus": ("appraisal_consensus", ("appraisal_agreement",),
                  "két független értékelő egyezése (κ) és az eltérések"),
    "pooled": ("appraisal_agreement_pooled", (), "az összes kettősen értékelt egység összevont egyezése (κ)"),
    "rob_summary": ("rob_summary", ("appraisal_rob_summary",), "a forgalmi lámpa adatai (szk.rob-summary/v1)"),
    "rob_sync": ("rob_sync_proposal", ("sync_rob_proposal", "appraisal_sync_rob", "rob_column_sync"),
                 "a kinyerési tábla rob oszlopának szinkron-javaslata"),
    "route": ("instrument_route", ("appraisal_route",), "eszköz-javaslat a vizsgálati elrendezés alapján"),
}


def engine_fn(key):
    """A motor homlokzat-függvénye (vagy None): az elvárt név, majd az álnevek közül az első hívható."""
    name, aliases, _what = ENGINE[key]
    for n in (name,) + tuple(aliases):
        fn = getattr(api, n, None)
        if callable(fn) and not isinstance(fn, type):
            return fn
    return None


def engine_missing(keys):
    names = ", ".join("metaelemzes.api.%s" % ENGINE[k][0] for k in keys)
    whats = "; ".join(ENGINE[k][2] for k in keys)
    return ApiError("CAPABILITY_MISSING",
                    "Ehhez a lépéshez a metaanalízis-motor értékelő funkciója kell (%s), de ez a motorváltozat még "
                    "nem tartalmazza (%s). Frissítsd a motort (git pull a metaanalizis-asszisztens mappában), majd "
                    "indítsd újra a munkapadot." % (whats, names),
                    {"engine_functions": [ENGINE[k][0] for k in keys], "engine": "metaelemzes.api"})


def need(key):
    fn = engine_fn(key)
    if fn is None:
        raise engine_missing([key])
    return fn


def engine_status():
    """{available: bool, functions: {kulcs: név|None}, missing: [...]} — a felület sávjához."""
    funcs = {}
    for key in ENGINE:
        fn = engine_fn(key)
        funcs[key] = getattr(fn, "__name__", None) if fn is not None else None
    core = ("instruments", "instrument")
    has_check = funcs["check"] is not None or (funcs["completeness"] is not None and funcs["implied"] is not None)
    missing = [ENGINE[k][0] for k in core if funcs[k] is None] + ([] if has_check else [ENGINE["check"][0]])
    return {"available": not missing, "functions": funcs, "missing": missing,
            "engine_version": str(getattr(api, "__version__", "")) or None}


def _call(fn, *args, **kw):
    """Hívás csak az elfogadott kulcsszavakkal (a motor szignatúrája még alakulhat)."""
    use = {k: v for k, v in kw.items() if v is not None and accepts(fn, k)}
    return fn(*args, **use)


def instrument(tool):
    """A motor eszköz-definíciója (szk.instrument/v1); ismeretlen eszköz → 404."""
    check_tool(tool)
    fn = need("instrument")
    try:
        inst = fn(tool)
    except (KeyError, LookupError):
        inst = None
    except ValueError as exc:
        raise ApiError("NOT_FOUND", "Ismeretlen értékelő eszköz: %s (%s)" % (tool, exc)) from None
    if not isinstance(inst, dict):
        raise ApiError("NOT_FOUND", "Ismeretlen értékelő eszköz: %s. A választható eszközök listája: GET "
                                    "/api/instruments." % tool)
    return inst


def instruments():
    fn = need("instruments")
    out = fn()
    if isinstance(out, dict):                      # {kulcs: definíció} vagy {instruments: [...]}
        out = out.get("instruments") if isinstance(out.get("instruments"), list) else [
            dict(v, key=v.get("key") or k) if isinstance(v, dict) else {"key": k} for k, v in sorted(out.items())]
    return [x for x in (out or []) if isinstance(x, dict)]


def check(doc, inst=None, project_dir=None):
    """szk.appraisal-result/v1 a motorból. Ha nincs egyesített check-függvény, a teljesség + implikált ítélet
    függvények eredménye összefésülve; a TRIPOD+AI / AMSTAR 2 külön függvénye (ha van) kiegészít. project_dir: a
    projekt konvenciói (pl. AMSTAR 2 „részben igen”) a ma-projekt.json-ból — ha a motor függvénye elfogadja."""
    fn = engine_fn("check")
    if fn is not None:
        res = _call(fn, doc, instrument=inst, project_dir=project_dir)
    else:
        comp, impl = engine_fn("completeness"), engine_fn("implied")
        if comp is None or impl is None:
            raise engine_missing(["check"])
        res = dict(_call(comp, doc, instrument=inst) or {})
        for k, v in dict(_call(impl, doc, instrument=inst) or {}).items():
            res.setdefault(k, v)
    res = dict(res or {})
    tool = doc.get("tool")
    if tool == "tripod-ai" and res.get("tripod") is None and engine_fn("tripod") is not None:
        res["tripod"] = _call(engine_fn("tripod"), doc, instrument=inst)
    if tool == "amstar2" and res.get("amstar2") is None and engine_fn("amstar2") is not None:
        answers = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in (doc.get("answers") or {}).items()}
        res["amstar2"] = _call(engine_fn("amstar2"), answers)
    res.setdefault("schema", SCHEMA_RESULT)
    res.setdefault("tool", tool)
    res["overrides"] = overrides(doc, res)
    return res


def validate_engine(doc, inst=None, project_dir=None):
    """A motor szerkezeti ellenőrzése (ha van ilyen függvénye) → problémák listája. project_dir: a projekt-szintű
    szabályok is (pl. C osztályú projektben AI-vázlat nem menthető — 6. döntés, 7.4); a végpontok MINDIG átadják."""
    fn = engine_fn("validate")
    if fn is None:
        return []
    out = _call(fn, doc, instrument=inst, project_dir=project_dir)
    if isinstance(out, dict):
        out = out.get("problems") or out.get("errors") or []
    return [str(p) for p in (out or [])]


# ---------------------------------------------------------------------------- azonosítók és utak
def check_tool(tool):
    if not isinstance(tool, str) or not TOOL_RE.match(tool):
        raise ApiError("BAD_REQUEST", "Érvénytelen eszköz-azonosító (kisbetű, szám, kötőjel; pl. rob2, probast-ai).")
    return tool


def check_rater(rater, required=True, allow_reserved=True):
    if rater in (None, ""):
        if required:
            raise ApiError("BAD_REQUEST", "Hiányzó értékelő (rater): add meg a monogramodat (pl. SzK).")
        return None
    if not isinstance(rater, str) or not RATER_RE.match(rater):
        raise ApiError("BAD_REQUEST", "Érvénytelen értékelő-azonosító: betűvel kezdődő, legfeljebb 32 karakter "
                                      "(betű, szám, _ vagy -), pl. SzK.")
    if not allow_reserved and rater.lower() in RESERVED_RATERS:
        raise ApiError("BAD_REQUEST", "A(z) '%s' fenntartott azonosító (konszenzus / AI-vázlat); emberi értékelőként "
                                      "a monogramodat add meg." % rater.lower())
    return RATER_CONSENSUS if rater.lower() == RATER_CONSENSUS else (RATER_AI if rater.lower() == RATER_AI else rater)


def check_target(target):
    if target in (None, ""):
        return None
    if not isinstance(target, str) or not TARGET_RE.match(target):
        raise ApiError("BAD_REQUEST", "Érvénytelen cél (target): betű/szám, _ vagy -, legfeljebb 64 karakter "
                                      "(pl. o1, XGB-PE, index-1).")
    return target


def check_unit(unit):
    if not isinstance(unit, str) or not unit.strip() or len(unit) > 128:
        raise ApiError("BAD_REQUEST", "Érvénytelen értékelési egység (a vizsgálat azonosítója, legfeljebb 128 karakter).")
    if unit in RESERVED_SEGMENTS:
        raise ApiError("BAD_REQUEST", "A(z) '%s' fenntartott név, nem lehet vizsgálat-azonosító." % unit)
    return unit


def slug(unit):
    s = _SLUG_BAD.sub("_", unit.strip()).strip("_")[:64]
    if not s:
        raise ApiError("BAD_REQUEST", "Az értékelési egység azonosítójából nem képezhető fájlnév.")
    return s


def relpath(unit, tool, target, rater):
    """A 2.4 szerinti projekt-relatív út."""
    parts = [slug(unit), tool] + ([target] if target else []) + [rater]
    rel = "%s/%s.json" % (APPRAISAL_DIR, ".".join(parts))
    try:
        security.check_relpath(rel)
    except security.UnsafePath as exc:
        raise ApiError("BAD_REQUEST", "Az értékelés fájlneve nem engedett (%s)." % exc.message) from None
    return rel


def parse_name(name):
    """Fájlnév → (slug, eszköz, cél|None, értékelő) vagy None."""
    m = _FILE_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4)


def unit_of(doc):
    tg = doc.get("target") if isinstance(doc.get("target"), dict) else {}
    u = tg.get("unit") or tg.get("study_id")
    return u if isinstance(u, str) and u.strip() else None


# ---------------------------------------------------------------------------- dokumentumok
def load(app, rel):
    """(dokumentum vagy None, etag) — sérült JSON → 422."""
    doc, etag = app.store.load_json(rel)
    if doc is not None and not isinstance(doc, dict):
        raise store.Invalid("A(z) %s gyökere objektum legyen." % rel, {"path": rel})
    return doc, etag


def list_files(app):
    """[{path, slug, tool, target, rater}] az appraisals mappából (név szerint; a nem illeszkedő nevek kimaradnak)."""
    d = app.store.path(APPRAISAL_DIR)
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.iterdir()):
        if len(out) >= MAX_FILES:
            break
        if not p.is_file() or p.name.startswith("."):
            continue
        parsed = parse_name(p.name)
        if parsed is None:
            continue
        sl, tool, target, rater = parsed
        out.append({"path": "%s/%s" % (APPRAISAL_DIR, p.name), "slug": sl, "tool": tool, "target": target,
                    "rater": rater})
    return out


def siblings(app, unit, tool, target):
    """Ugyanazon egység + eszköz + cél összes értékelése: [{rater, path, doc, etag}] (sérült fájl kimarad)."""
    sl = slug(unit)
    out = []
    for f in list_files(app):
        if f["slug"] != sl or f["tool"] != tool or f["target"] != target:
            continue
        try:
            doc, etag = load(app, f["path"])
        except store.StoreError:
            continue
        if doc is None:
            continue
        u = unit_of(doc)
        if u is not None and u != unit:
            continue                            # fájlnév-ütközés más vizsgálattal: nem ehhez az egységhez tartozik
        out.append(dict(f, doc=doc, etag=etag))
    return out


# a cél (target.key) hiányában a fájlnév cél-része az eszköz egységéből — a motor appraisal.derived_target_key-ével
# azonos szabály (M5: két kimenet értékelése nem ütközhet egy fájlon)
_KEY_FALLBACK = {"result": ("outcome", "result"), "outcome": ("outcome",), "model": ("model",),
                 "index_test": ("index_test",)}


def target_key(doc, inst=None):
    """A fájlnév cél-része: target.key, különben az eszköz egysége szerinti mező (outcome / result / model /
    index_test) fájlnév-alakban; None, ha egyik sincs."""
    tg = doc.get("target") if isinstance(doc.get("target"), dict) else {}
    k = tg.get("key")
    if isinstance(k, str) and k:
        return k
    for field in _KEY_FALLBACK.get((inst or {}).get("unit"), ()):
        v = tg.get(field)
        if isinstance(v, str) and v.strip():
            v = v.strip()
            v = v if TARGET_RE.match(v) else _SLUG_BAD.sub("_", v).strip("_")[:64]
            if v and TARGET_RE.match(v):
                return v
    return None


def is_consensus_doc(doc):
    """Konszenzus-dokumentum-e: status 'consensus', VAGY van consensus_of (a még feloldatlan konszenzus-vázlat is) —
    a motor appraisal.is_consensus_doc-jával azonos (C1): soha nem független értékelő, és csak a consensus-fájlba
    kerülhet."""
    if not isinstance(doc, dict):
        return False
    return doc.get("status") == "consensus" or (isinstance(doc.get("consensus_of"), list) and bool(doc["consensus_of"]))


def is_human(doc, rater=None):
    """Független emberi értékelés-e (a κ és a konszenzus csak ezeket látja; 6. döntés)."""
    if not isinstance(doc, dict):
        return False
    if doc.get("origin") == "ai_draft" or is_consensus_doc(doc):
        return False
    r = rater or doc.get("assessor")
    return isinstance(r, str) and r.lower() not in RESERVED_RATERS and doc.get("status") != "consensus"


def skeleton(unit, tool, target, rater, inst=None):
    """Üres szk.appraisal/v1 (még nem mentett)."""
    tg = {"unit": unit, "study_id": None if unit in SPECIAL_UNITS else unit, "outcome": None, "result": None,
          "model": None, "index_test": None, "key": target}
    doc = {"schema": SCHEMA_DOC, "tool": tool, "scope": None, "instrument_sha256": None, "target": tg,
           "assessor": rater, "second_assessor": None, "status": "consensus" if rater == RATER_CONSENSUS else "draft",
           "origin": "ai_draft" if rater == RATER_AI else "human", "approved_by": None, "approved_at": None,
           "answers": {}, "domain_judgements": [], "applicability": [], "overall": None,
           "created": None, "updated": None}
    if inst:
        doc["instrument_sha256"] = inst.get("reference_sha256")
        scopes = inst_scopes(inst)
        if scopes:
            doc["scope"] = scopes[0]
    return doc


def inst_scopes(inst):
    sc = inst.get("scopes")
    if isinstance(sc, list):
        return [s if isinstance(s, str) else s.get("id") for s in sc if isinstance(s, (str, dict))]
    if inst.get("passes"):
        return ["both"] + [p.get("id") for p in inst["passes"] if isinstance(p, dict)]
    return []


def answer_values(inst):
    """Az eszköz kanonikus válaszértékei (a TRIPOD+AI státusz-szótárával együtt)."""
    vals = set()
    for a in inst.get("answers") or ():
        if isinstance(a, dict) and isinstance(a.get("value"), str):
            vals.add(a["value"])
        elif isinstance(a, str):
            vals.add(a)
    for v in inst.get("status_vocab") or ():
        vals.add(v.get("value") if isinstance(v, dict) else v)
    return vals


def verdict_values(inst):
    vals = set()
    for v in inst.get("verdicts") or ():
        if isinstance(v, dict) and isinstance(v.get("value"), str):
            vals.add(v["value"])
        elif isinstance(v, str):
            vals.add(v)
    return vals


def _text(v, field, problems, limit=MAX_TEXT):
    if v is None:
        return
    if not isinstance(v, str):
        problems.append("%s: szöveg vagy null kell" % field)
    elif len(v) > limit:
        problems.append("%s: legfeljebb %d karakter" % (field, limit))


def doc_problems(doc, inst, unit, tool, target, rater):
    """szk.appraisal/v1 alakellenőrzés a kérés útjához és az eszközhöz (problémák listája, értékek nélkül)."""
    p = []
    if not isinstance(doc, dict):
        return ["a dokumentum gyökere objektum legyen"]
    if doc.get("schema") != SCHEMA_DOC:
        p.append("schema: '%s' kell" % SCHEMA_DOC)
    if doc.get("tool") != tool:
        p.append("tool: a kérés eszköze (%s) kell" % tool)
    tg = doc.get("target")
    if not isinstance(tg, dict):
        p.append("target: objektum kell")
        tg = {}
    u = unit_of(doc)
    if u is not None and u != unit:
        p.append("target: másik értékelési egység (a kérés útja: %s)" % unit)
    if (tg.get("key") or None) != target:
        p.append("target.key: a kérés célja (%s) kell" % (target or "nincs"))
    if rater == RATER_CONSENSUS:
        if doc.get("status") != "consensus":
            p.append("status: a konszenzus-változat státusza 'consensus'")
        for k in ("assessor", "second_assessor"):
            r = doc.get(k)
            if not (isinstance(r, str) and RATER_RE.match(r) and r.lower() not in RESERVED_RATERS):
                p.append("%s: a konszenzushoz két emberi értékelő monogramja kell" % k)
        if doc.get("assessor") == doc.get("second_assessor"):
            p.append("second_assessor: két különböző értékelő kell")
    elif rater == RATER_AI:
        if doc.get("origin") != "ai_draft":
            p.append("origin: az 'ai' értékelő fájlja csak AI-vázlat (ai_draft) lehet")
    elif doc.get("assessor") != rater:
        p.append("assessor: a kérés értékelője (%s) kell" % rater)
    if doc.get("status") not in STATUSES:
        p.append("status: %s" % " | ".join(STATUSES))
    elif doc.get("status") == "consensus" and rater != RATER_CONSENSUS:
        p.append("status: 'consensus' csak a konszenzus-változaté (rater=consensus)")
    if doc.get("origin") not in ORIGINS:
        p.append("origin: human | ai_draft")
    elif doc.get("origin") == "ai_draft" and rater == RATER_CONSENSUS:
        p.append("origin: a konszenzus-változat emberi döntés (human), nem AI-vázlat")
    scopes = inst_scopes(inst)
    if doc.get("scope") is not None and scopes and doc.get("scope") not in scopes:
        p.append("scope: %s" % " | ".join(scopes))
    answers = doc.get("answers")
    allowed = answer_values(inst)
    if not isinstance(answers, dict):
        p.append("answers: objektum kell")
        answers = {}
    for key, a in answers.items():
        where = "answers[%s]" % key if ANSWER_KEY_RE.match(str(key)) else "answers{kulcs}"
        if not ANSWER_KEY_RE.match(str(key)):
            p.append("%s: a kulcs alakja ^((development|evaluation)/)?azonosító$" % where)
            continue
        if not isinstance(a, dict):
            p.append("%s: objektum kell ({value, evidence?, rationale?})" % where)
            continue
        v = a.get("value")
        if v is not None and (not isinstance(v, str) or (allowed and v not in allowed)):
            p.append("%s.value: az eszköz válaszértékeinek egyike kell (szabad szöveg nem)" % where)
        ev = a.get("evidence")
        if ev is not None:
            if not isinstance(ev, dict):
                p.append("%s.evidence: objektum kell" % where)
            else:
                _text(ev.get("text"), where + ".evidence.text", p, MAX_QUOTE)
                _text(ev.get("doc"), where + ".evidence.doc", p, 200)
                _text(ev.get("locator"), where + ".evidence.locator", p, 200)
                pg = ev.get("page")
                if pg is not None and (isinstance(pg, bool) or not isinstance(pg, int) or pg < 0 or pg > 100000):
                    p.append("%s.evidence.page: nemnegatív egész szám vagy null" % where)
        _text(a.get("comment"), where + ".comment", p)
        rat = a.get("rationale")
        if rat is not None:
            if isinstance(rat, dict):
                for k in ("asks", "because", "change", "uncertain"):
                    _text(rat.get(k), "%s.rationale.%s" % (where, k), p)
            else:
                _text(rat, where + ".rationale", p)
    verdicts = verdict_values(inst)
    for coll in ("domain_judgements", "applicability"):
        items = doc.get(coll)
        if items is None:
            continue
        if not isinstance(items, list):
            p.append("%s: lista kell" % coll)
            continue
        for i, dj in enumerate(items):
            pre = "%s[%d]" % (coll, i)
            if not isinstance(dj, dict) or not isinstance(dj.get("domain"), str):
                p.append(pre + ": {domain, judgement, …} kell")
                continue
            j = dj.get("judgement")
            if j is not None and verdicts and j not in verdicts:
                p.append(pre + ".judgement: az eszköz ítéletskálájának egyike kell")
            if dj.get("pass") is not None and dj.get("pass") not in PASSES:
                p.append(pre + ".pass: development | evaluation | null")
            for k in ("rationale", "override_reason"):
                _text(dj.get(k), "%s.%s" % (pre, k), p)
    ov = doc.get("overall")
    if ov is not None:
        if not isinstance(ov, dict):
            p.append("overall: objektum vagy null kell")
        else:
            j = ov.get("judgement")
            if j is not None and verdicts and j not in verdicts:
                p.append("overall.judgement: az eszköz ítéletskálájának egyike kell")
            for k in ("rationale", "override_reason"):
                _text(ov.get(k), "overall." + k, p)
    for k in ("approved_by",):
        r = doc.get(k)
        if r is not None and not (isinstance(r, str) and RATER_RE.match(r) and r.lower() not in RESERVED_RATERS):
            p.append("%s: emberi értékelő monogramja vagy null" % k)
    return p


# ---------------------------------------------------------------------------- felülbírálás (X017)
def _norm(v):
    return v if isinstance(v, str) and v else None


def overrides(doc, res):
    """Az ember ítélete eltér a motor implikált ítéletétől → indoklás kell (X017). A motor saját listáját (ha ad)
    vesszük; különben a dokumentum és a motor ``domains[].implied`` / ``overall.implied`` mezőinek összevetése
    (címkék egyezése — nem számítás). → [{domain, pass, judgement, implied, reason_missing, algorithm}]"""
    given = res.get("overrides") if isinstance(res, dict) else None
    if isinstance(given, list) and all(isinstance(x, dict) for x in given):
        return given
    implied = {}
    for d in (res or {}).get("domains") or ():
        if isinstance(d, dict) and d.get("domain") is not None:
            implied[(str(d.get("domain")), d.get("pass"))] = (d.get("implied"), d.get("algorithm"))
    out = []
    for dj in doc.get("domain_judgements") or ():
        if not isinstance(dj, dict):
            continue
        key = (str(dj.get("domain")), dj.get("pass"))
        imp, alg = implied.get(key, (None, None))
        j = _norm(dj.get("judgement"))
        if j and _norm(imp) and j != imp and alg not in ("none", None):
            out.append({"domain": key[0], "pass": key[1], "judgement": j, "implied": imp, "algorithm": alg,
                        "reason_missing": not (isinstance(dj.get("override_reason"), str)
                                               and dj["override_reason"].strip())})
    ov = doc.get("overall") if isinstance(doc.get("overall"), dict) else None
    rov = (res or {}).get("overall") if isinstance((res or {}).get("overall"), dict) else {}
    if ov and _norm(ov.get("judgement")) and _norm(rov.get("implied")) and ov["judgement"] != rov["implied"] \
            and rov.get("algorithm") not in ("none", None):
        out.append({"domain": "overall", "pass": None, "judgement": ov["judgement"], "implied": rov["implied"],
                    "algorithm": rov.get("algorithm"),
                    "reason_missing": not (isinstance(ov.get("override_reason"), str) and ov["override_reason"].strip())})
    return out


def label_of(inst, verdict):
    for v in inst.get("verdicts") or ():
        if isinstance(v, dict) and v.get("value") == verdict:
            lab = v.get("label")
            if isinstance(lab, dict):
                return lab.get("hu") or lab.get("en") or verdict
            return lab or verdict
    return verdict
