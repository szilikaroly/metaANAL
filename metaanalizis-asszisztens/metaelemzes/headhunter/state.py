# -*- coding: utf-8 -*-
"""Metaheadhunter — projekt-állapot (``01_kereses/headhunter/state.json``), döntésnapló (``decisions.jsonl``),
futásnapló (``runs/<run_id>/``) és a fájl-segédek (TERV_metaheadhunter.md 2., 4. és 14. fejezet).

Kezdőknek: a Metaheadhunter minden adatát a projektmappa ``01_kereses/headhunter/`` almappájában tartja:

* ``state.json`` — a kérdésed (PICO), a kizárási okok szótára, a választott források és azok állapota, a lépések
  állapota (kész / emberi döntésre vár / elavult …), az ellenőrzőpontok (EP1–EP6) és a keresési napló
  (PRISMA-S). API-kulcs, e-mail-cím vagy annak bármilyen része SOHA nem kerül ide — csak „be van-e állítva".
* ``decisions.jsonl`` — minden emberi döntés egy sorban, csak hozzáfűzéssel; minden sor tartalmazza az előző
  sor sha256-ját (hash-lánc), így utólagos átírás kimutatható (H015). Betegadat nem kerülhet bele: az
  indoklást és az idézetet a program TAJ-szám-, születési dátum- és e-mail-mintára ellenőrzi, és gyanú esetén
  nem írja ki (N9).
* ``runs/<run_id>/run.json`` és ``progress.jsonl`` — a hosszabb lépések futásnaplója (argumentumok kulcsok
  nélkül, forrás-állapotok, időtartam); a ``CANCEL`` fájl együttműködő megszakítást kér.

Írás: atomikusan (ideiglenes fájl ugyanabban a mappában + ``os.replace``), írászár alatt (``.lock``). A zár a
``dedup.ProjectLock``-kal azonos (ha az importálható), így a lépések egymásba ágyazva sem akadnak el.

Nyilvános API (a CLI, a ``merge``/``update``/``prisma_map`` és később a facade használja)::

    hh_dir(project_dir), path(project_dir, *parts), rel(project_dir, abs_path)
    init_state(project_dir, question, pico=None, mode="harvest", ...) → state
    load_state(project_dir) → state (StateError, ha nincs inicializálva)
    save_state(project_dir, state) ; mutate_state(project_dir, fn) → state
    set_step(state, step, status, ...) ; mark_stale(state, step) ; set_checkpoint(state, ep, status, ...)
    add_searches(state, entries)
    append_decision(project_dir, kind, target, value, actor, **mezők) → döntés-dict
    read_decisions(project_dir) ; verify_chain(decisions) ; effective_decisions(decisions, kinds, key)
    new_run(project_dir, step, argv) ; Run.progress(...) ; Run.finish(...) ; Run.cancelled()
    validate(doc, name) → [hibaszöveg]
"""
from __future__ import absolute_import

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone

MODEL = "szk.ma.headhunter/v1"
STATE_SCHEMA = "szk.ma.headhunter.state/v1"
DECISION_SCHEMA = "szk.ma.headhunter.decision/v1"
TOOL_ACTOR = "tool:headhunter"
ZERO_SHA = "0" * 64

HH_REL = "01_kereses/headhunter"
FILES = {
    "state": "state.json",
    "studies": "studies.json",
    "decisions": "decisions.jsonl",
    "update": "update_search.json",
    "overlap": "overlap.json",
    "merged": "merged.json",
    "prisma": "prisma_flow.json",
}

#: a lépések (``state.steps`` kulcsai) a folyamat sorrendjében
STEPS = ("sources", "find_reviews", "select_reviews", "extract", "resolve", "dedupe", "overlap", "screen",
         "update_search", "merge", "prisma", "export")
STEP_STATUSES = ("not_started", "running", "done", "needs_human", "failed", "stale", "skipped")

#: elavulás: ha egy lépés kimenete változik, ezek a ráépülő lépések ``stale`` állapotba kerülnek (TERV 2.)
DOWNSTREAM = {
    "sources": (),
    "find_reviews": ("select_reviews", "extract", "resolve", "dedupe", "overlap", "screen", "update_search",
                     "merge", "prisma", "export"),
    "select_reviews": ("extract", "resolve", "dedupe", "overlap", "screen", "update_search", "merge", "prisma",
                       "export"),
    "extract": ("resolve", "dedupe", "overlap", "screen", "update_search", "merge", "prisma", "export"),
    "resolve": ("dedupe", "overlap", "screen", "merge", "prisma", "export"),
    "dedupe": ("overlap", "screen", "merge", "prisma", "export"),
    "overlap": ("export",),
    "screen": ("merge", "prisma", "export"),
    "update_search": ("screen", "merge", "prisma", "export"),
    "merge": ("prisma", "export"),
    "prisma": ("export",),
    "export": (),
}

CHECKPOINTS = ("EP1", "EP2", "EP3", "EP4", "EP5", "EP6")
CHECKPOINT_INFO = {
    "EP1": {"hu": "Forrás-áttekintések kiválasztása", "en": "Selecting the source reviews"},
    "EP2": {"hu": "Bizonytalan kinyert jelöltek megerősítése", "en": "Confirming uncertain extracted candidates"},
    "EP3": {"hu": "Duplikátum- és kapcsolás-javaslatok eldöntése", "en": "Deciding duplicate/link proposals"},
    "EP4": {"hu": "Jogosultság a saját PICO szerint (szűrés)", "en": "Eligibility against your own PICO"},
    "EP5": {"hu": "Végső bevonás lezárása (signoff)", "en": "Final inclusion sign-off"},
    "EP6": {"hu": "Másodlagos adatok ellenőrzése az elsődleges közleménnyel",
            "en": "Verifying secondary data against the primary report"},
}

DECISION_KINDS = ("review_select", "candidate_confirm", "candidate_reject", "id_confirm", "duplicate_accept",
                  "duplicate_reject", "study_link", "study_split", "role_set", "screen", "final_inclusion",
                  "secondary_verify", "update_window", "source_config", "criteria_set", "checkpoint", "note")
TARGET_TYPES = ("review", "candidate", "record", "study", "proposal", "project", "source", "window", "checkpoint")
#: ezeket a döntéseket csak ember hozhatja (``user:…``); az ágens és a program csak javasol (N3)
HUMAN_KINDS = frozenset(DECISION_KINDS) - frozenset(["note"])
LEVELS = ("title_abstract", "full_text", "final")

_ACTOR_RE = re.compile(r"^(user|agent|tool):[^\s].{0,99}$")
_DID_RE = re.compile(r"^d-(\d{8}T\d{6}Z)-(\d{4,6})$")
_RUN_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")


class StateError(RuntimeError):
    """A headhunter-állapot nem használható (pl. nincs inicializálva). ``code``: gépi kód a CLI-nek/felületnek."""

    def __init__(self, message, code="HH_STATE", en=None):
        RuntimeError.__init__(self, message)
        self.code = code
        self.hu = message
        self.en = en or message


class DecisionError(ValueError):
    """Érvénytelen döntés (pl. nem emberi szereplő emberi döntéshez, betegadat-gyanús indoklás)."""


# =============================================================================================
# idő, utak, JSON
# =============================================================================================

def utc_now(now=None):
    """UTC időbélyeg ``ÉÉÉÉ-HH-NNTÓÓ:PP:MMZ``. ``now``: ``None`` (most), ISO-szöveg, epoch vagy datetime."""
    if now is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(now, str):
        return now
    elif isinstance(now, (int, float)):
        dt = datetime.fromtimestamp(float(now), timezone.utc)
    elif isinstance(now, datetime):
        dt = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    else:
        raise TypeError("now: ismeretlen típus")
    return dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact(ts):
    """``2026-10-05T10:20:00Z`` → ``20261005T102000Z``."""
    return re.sub(r"[-:]", "", ts)


def today(now=None):
    return utc_now(now)[:10]


def hh_dir(project_dir):
    """A projekt headhunter-mappája: ``<projekt>/01_kereses/headhunter``."""
    return os.path.join(project_dir, "01_kereses", "headhunter")


def path(project_dir, *parts):
    return os.path.join(hh_dir(project_dir), *parts)


def rel(project_dir, abs_path):
    """A projektmappához képest relatív út ``/`` elválasztóval (a sémák ``relpath`` mintája)."""
    r = os.path.relpath(os.path.abspath(abs_path), os.path.abspath(project_dir))
    return r.replace(os.sep, "/")


def dump_json(doc):
    """Kanonikus formázás (a motor szerződés-konvenciója): ``indent=2``, ``ensure_ascii=False``, záró újsor."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def read_json(file_path, default=None):
    try:
        with open(file_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, IOError, ValueError):
        return default


def write_text_atomic(file_path, text):
    """Atomikus szövegírás (ideiglenes fájl ugyanabban a mappában + ``os.replace``, TERV 4.6)."""
    d = os.path.dirname(os.path.abspath(file_path))
    if not os.path.isdir(d):
        os.makedirs(d)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o644)  # a mkstemp 0600-at ad; a munkapad (ugyanaz a felhasználó) és a git olvassa
        except OSError:  # pragma: no cover
            pass
        os.replace(tmp, file_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return file_path


def write_json_atomic(file_path, doc):
    return write_text_atomic(file_path, dump_json(doc))


def _dedup_module():
    """A ``dedup`` modul (lusta import; ``None``, ha nem tölthető be)."""
    try:
        from . import dedup
        return dedup
    except Exception:  # pragma: no cover - a párhuzamos fejlesztés alatt átmenetileg hiányozhat
        return None


# =============================================================================================
# írászár
# =============================================================================================

class LockError(RuntimeError):
    """Az írászár nem szerezhető meg (egy másik folyamat dolgozik a projekten)."""


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class _FallbackLock(object):
    """Tartalék írászár (ha a ``dedup`` nem importálható): ``.lock`` ``O_CREAT|O_EXCL``, PID + időbélyeg, 10 perc
    után elavult; folyamaton belül újrabelépő."""

    def __init__(self, project_dir, timeout=30.0, stale_after=600.0, poll=0.1):
        self.path = os.path.join(hh_dir(project_dir), ".lock")
        self.timeout = float(timeout)
        self.stale_after = float(stale_after)
        self.poll = float(poll)

    def _entry(self):
        with _LOCKS_GUARD:
            ent = _LOCKS.get(self.path)
            if ent is None:
                ent = _LOCKS[self.path] = {"rlock": threading.RLock(), "depth": 0}
            return ent

    def __enter__(self):
        ent = self._entry()
        if not ent["rlock"].acquire(timeout=self.timeout):
            raise LockError("A headhunter-mappa zárolva van. Próbáld újra később.")
        if ent["depth"] > 0:
            ent["depth"] += 1
            return self
        d = os.path.dirname(self.path)
        if not os.path.isdir(d):
            os.makedirs(d)
        start = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                with os.fdopen(fd, "w") as fh:
                    fh.write("%d %s\n" % (os.getpid(), utc_now()))
                ent["depth"] = 1
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    continue
                if age > self.stale_after:
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                if time.time() - start > self.timeout:
                    ent["rlock"].release()
                    raise LockError("A headhunter-mappát egy másik folyamat zárolja (%s). Várj, amíg befejezi; ha "
                                    "10 percnél régebbi, a zár elavultnak számít." % self.path)
                time.sleep(self.poll)

    def __exit__(self, *exc):
        ent = self._entry()
        try:
            ent["depth"] -= 1
            if ent["depth"] <= 0:
                ent["depth"] = 0
                try:
                    os.remove(self.path)
                except OSError:
                    pass
        finally:
            ent["rlock"].release()
        return False


def lock(project_dir, timeout=30.0):
    """Az írászár (kontextuskezelő). A ``dedup.ProjectLock``-ot használja, ha elérhető — így a többi lépés
    zárjával egymásba ágyazható (ugyanabban a folyamatban újrabelépő)."""
    d = _dedup_module()
    if d is not None and hasattr(d, "ProjectLock"):
        return d.ProjectLock(project_dir, timeout=timeout)
    return _FallbackLock(project_dir, timeout=timeout)


# =============================================================================================
# sémák (futásidejű validálás, TERV 20. fejezet)
# =============================================================================================

CONTRACT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contracts")
SCHEMA_FILES = {
    "state": "ma.headhunter.state.v1.schema.json",
    "review": "ma.headhunter.review.v1.schema.json",
    "studies": "ma.headhunter.studies.v1.schema.json",
    "decision": "ma.headhunter.decision.v1.schema.json",
    "update-search": "ma.headhunter.update-search.v1.schema.json",
    "overlap": "ma.headhunter.overlap.v1.schema.json",
    "merged": "ma.headhunter.merged.v1.schema.json",
    "cassette": "ma.headhunter.cassette.v1.schema.json",
}
_REGISTRY = {"reg": None, "loaded": False}


def _registry():
    if _REGISTRY["loaded"]:
        return _REGISTRY["reg"]
    _REGISTRY["loaded"] = True
    try:
        from ma_gui import schema_lite  # stdlib-only, ugyanabban a pluginban érkezik
        _REGISTRY["reg"] = (schema_lite, schema_lite.load_schema_dir(CONTRACT_DIR))
    except Exception:
        _REGISTRY["reg"] = None
    return _REGISTRY["reg"]


def _load_schema(name):
    with open(os.path.join(CONTRACT_DIR, SCHEMA_FILES[name]), encoding="utf-8") as fh:
        return json.load(fh)


def validate(doc, name):
    """A dokumentum ellenőrzése a ``contracts/`` sémája szerint. Visszaad: hibaszövegek listája (üres = rendben).
    Ha a ``ma_gui.schema_lite`` nem importálható, a kötelező kulcsok és a ``schema``-konstans minimális
    ellenőrzésére esik vissza (az első elem ilyenkor ``info:`` előtagú megjegyzés)."""
    schema = _load_schema(name)
    reg = _registry()
    if reg is not None:
        schema_lite, registry = reg
        return [str(e) for e in schema_lite.validate(doc, schema, registry=registry)]
    errs = ["info: a teljes sémavalidálás nem érhető el (ma_gui.schema_lite hiányzik) — minimális ellenőrzés"]
    if not isinstance(doc, dict):
        return errs + ["/: objektum kell"]
    for k in schema.get("required") or []:
        if k not in doc:
            errs.append("/%s: hiányzó kötelező mező" % k)
    const = ((schema.get("properties") or {}).get("schema") or {}).get("const")
    if const and doc.get("schema") != const:
        errs.append("/schema: %r kell" % const)
    return errs


def schema_errors(doc, name):
    """Csak a valódi hibák (az ``info:`` megjegyzés nélkül)."""
    return [e for e in validate(doc, name) if not e.startswith("info:")]


# =============================================================================================
# titok- és betegadat-őr
# =============================================================================================

def secret_leaks(text, env=None):
    """H016: a nyilvántartott titkok (kulcsok, e-mail) előfordulása a szövegben (csak a helyük/fajtájuk)."""
    try:
        from . import net
        return net.find_secret_leaks(text, env=env) or []
    except Exception:
        leaks = []
        for name in ("MA_SCOPUS_APIKEY", "MA_SCOPUS_INSTTOKEN", "MA_OPENALEX_APIKEY", "MA_NCBI_APIKEY",
                     "MA_CONTACT_EMAIL"):
            v = (env if env is not None else os.environ).get(name)
            if v and len(v) >= 4 and v in text:
                leaks.append(name)
        return leaks


_EMAIL_RE = re.compile(r"(?i)(?<![a-z0-9._%+\-])[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,24}"
                       r"(?![a-z0-9\-])")
_TAJ_RE = re.compile(r"(?<![\w.,/])([0-9]{3})[ \-]{0,2}([0-9]{3})[ \-]{0,2}([0-9]{3})(?!\w)")
_BIRTH_RE = re.compile(r"(?i)(?<![a-z])(?:sz[uü]l|dob(?![a-z])|birth)")
_FULL_DATE_RE = re.compile(r"(?<![0-9])(?:\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{4})(?![0-9])")


def _taj_ok(digits):
    if len(digits) != 9 or digits == "0" * 9:
        return False
    return sum(int(c) * (3 if i % 2 == 0 else 7) for i, c in enumerate(digits[:8])) % 10 == int(digits[8])


def phi_patterns(text):
    """Betegadat-gyanú szabad szövegben (indoklás, idézet): ``taj_cdv`` (érvényes CDV-jű TAJ-szám), ``full_date``
    (teljes dátum születési kontextusban), ``email``. A ``ma_gui.privacy.text_patterns``-t használja, ha
    elérhető. Értéket SOHA nem ad vissza, csak a minták nevét."""
    if not isinstance(text, str) or len(text) < 6:
        return []
    found = []
    try:
        from ma_gui import privacy
        found = list(privacy.text_patterns(text))
    except Exception:
        if any(_taj_ok(m.group(1) + m.group(2) + m.group(3)) for m in _TAJ_RE.finditer(text)):
            found.append("taj_cdv")
        if _BIRTH_RE.search(text) and _FULL_DATE_RE.search(text):
            found.append("full_date")
    if "@" in text and _EMAIL_RE.search(text) and "email" not in found:
        found.append("email")
    return found


# =============================================================================================
# állapot (state.json)
# =============================================================================================

DEFAULT_SETTINGS = {
    "title_similarity_probable": 0.9,
    "title_similarity_possible": 0.8,
    "overlap_window_months": 6,
    "max_reviews": 50,
    "quote_max_chars": 300,
    "secondary_tolerance_rel": 0.0,
    "reviewers": [],
}


def default_exclusion_reasons():
    """A PRISMA 16a kizárási okok alap-szótára (a felhasználó bővítheti/átírhatja a ``state.json``-ban)."""
    items = [
        ("X1", "P", "nem megfelelő populáció", "wrong population"),
        ("X2", "I", "nem megfelelő beavatkozás / expozíció", "wrong intervention / exposure"),
        ("X3", "C", "nem megfelelő összehasonlítás (kontroll)", "wrong comparator"),
        ("X4", "O", "nem közöl releváns kimenetet", "no relevant outcome reported"),
        ("X5", "S", "nem megfelelő vizsgálati elrendezés", "wrong study design"),
        ("X6", None, "nem elsődleges közlemény (áttekintés, szerkesztőségi, kommentár)",
         "not a primary report (review, editorial, comment)"),
        ("X7", None, "duplikált / új adatot nem tartalmazó közlemény", "duplicate report / no new data"),
        ("X8", None, "visszavont közlemény", "retracted publication"),
        ("X9", "L", "nyelv (a protokoll szerint kizárva)", "language (excluded per protocol)"),
        ("X10", None, "egyéb ok (indoklásban részletezve)", "other reason (detailed in the reason text)"),
    ]
    return [{"code": c, "criterion": None, "label": {"hu": hu, "en": en}, "domain": dom}
            for c, dom, hu, en in items]


#: az alap gépi előszűrési kritériumok (csak JAVASLATOT adnak — a döntés emberi, N3). Élő próbán (BCG) a CLI
#: ``init``-je kritériumok nélkül indult, így a ``screen propose`` 245 rekordra 0 javaslatot adott (a TERV 10.
#: fejezete szerinti „Review", „Editorial" kizárási javaslat sem született).
DEFAULT_PUB_TYPES_EXCLUDE = ("Review", "Systematic Review", "Meta-Analysis", "Editorial", "Comment",
                             "Practice Guideline", "Guideline", "News", "Retracted Publication")


def default_criteria():
    """Alap kritériumok, ha a felhasználó (vagy a ``ma-tervezo`` protokollja) nem adott meg sajátot."""
    return [
        {"id": "E1", "type": "exclude", "domain": "S",
         "text": "Nem elsődleges közlemény (áttekintés, metaanalízis, szerkesztőségi, kommentár, irányelv) — gépi "
                 "javaslat a PubMed publikációtípusa alapján; a döntés emberi.",
         "machine_hint": {"pub_types_exclude": list(DEFAULT_PUB_TYPES_EXCLUDE), "exclude_retracted": True}},
        {"id": "E2", "type": "include", "domain": "P",
         "text": "Emberi vizsgálat — a csak állatkísérletként indexelt közlemény kizárási javaslatot kap.",
         "machine_hint": {"humans_only": True}},
    ]


def _default_reasons_for(criteria):
    reasons = default_exclusion_reasons()
    if criteria is None:  # az alap kritériumokhoz kötjük az okokat (a gépi javaslat így okkódot is kap)
        for r in reasons:
            if r["code"] == "X6":
                r["criterion"] = "E1"
            if r["code"] == "X1":
                r["criterion"] = "E2"
    return reasons


def _split_terms(text):
    """Szabadszavas kifejezések szétbontása (``;``, ``|``, sortörés vagy `` OR `` mentén; a vessző a kifejezés része
    maradhat)."""
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        out = []
        for t in text:
            out.extend(_split_terms(t))
        return out
    parts = re.split(r"\s*(?:;|\||\n|\bOR\b)\s*", str(text))
    return [p.strip() for p in parts if p and p.strip()]


def pico_from_args(question, population=None, intervention=None, comparator=None, outcomes=None,
                   study_designs=None, mesh=None):
    """Egyszerű PICO (szöveges mezők) → ``state.pico`` a fogalomblokkokkal (``query_blocks``). A blokkokat a
    felhasználó vagy a ``ma-tervezo`` később pontosíthatja (``--pico pico.json``)."""
    mesh = mesh or {}
    pico = {"question": str(question).strip(), "population": population or None, "intervention": intervention or None,
            "comparator": comparator or None, "outcomes": _split_terms(outcomes), "study_designs":
            _split_terms(study_designs), "other": None, "query_blocks": []}
    for concept, val in (("P", population), ("I", intervention), ("C", comparator)):
        terms = _split_terms(val)
        if terms or mesh.get(concept):
            pico["query_blocks"].append({"concept": concept, "terms": terms, "mesh": _split_terms(mesh.get(concept))})
    return pico


def _norm_pico(pico, question):
    p = copy.deepcopy(pico or {})
    if question:
        p["question"] = str(question).strip()
    if not p.get("question"):
        raise StateError("A kutatási kérdés (--question) kötelező.", "BAD_REQUEST", "The research question is required.")
    for k in ("population", "intervention", "comparator", "other"):
        if k in p and p[k] is not None and not isinstance(p[k], str):
            p[k] = str(p[k])
    for k in ("outcomes", "study_designs"):
        if k in p:
            p[k] = [str(x) for x in (p[k] if isinstance(p[k], list) else _split_terms(p[k]))]
    blocks = []
    for b in p.get("query_blocks") or []:
        if not isinstance(b, dict):
            continue
        concept = b.get("concept") if b.get("concept") in ("P", "I", "C", "O", "S", "other") else "other"
        blocks.append({"concept": concept, "terms": [str(t) for t in _split_terms(b.get("terms"))],
                       "mesh": [str(m) for m in _split_terms(b.get("mesh"))]})
    p["query_blocks"] = blocks
    return p


def new_state(question, pico=None, mode="harvest", criteria=None, exclusion_reasons=None, settings=None, env=None,
              now=None):
    """Új ``state.json`` dokumentum (nem ír fájlt)."""
    if mode not in ("harvest", "own_update"):
        raise StateError("A mód 'harvest' (más áttekintések bányászata) vagy 'own_update' (a saját korábbi "
                         "áttekintésed frissítése).", "BAD_REQUEST")
    ts = utc_now(now)
    try:
        from . import sources as _src
        srcs = _src.default_config(env)
    except Exception:  # pragma: no cover - tartalék, ha a forrás-regiszter nem tölthető be
        srcs = dict((s, {"enabled": s != "scopus", "status": "unknown" if s != "scopus" else "not_configured"})
                    for s in ("pubmed", "europepmc", "openalex", "scopus", "ctgov", "crossref"))
    st = dict(DEFAULT_SETTINGS)
    st.update(settings or {})
    st["contact_email_set"] = bool((env if env is not None else os.environ).get("MA_CONTACT_EMAIL"))
    state = {
        "schema": STATE_SCHEMA,
        "model": MODEL,
        "tool_version": _tool_version(),
        "created": ts,
        "updated": ts,
        "mode": mode,
        "pico": _norm_pico(pico, question),
        "criteria": list(criteria) if criteria is not None else default_criteria(),
        "exclusion_reasons": list(exclusion_reasons or _default_reasons_for(criteria)),
        "sources": srcs,
        "steps": dict((s, {"status": "not_started", "updated": None, "run_id": None, "message": None})
                      for s in STEPS),
        "checkpoints": [{"id": ep, "status": "pending", "decided_by": None, "at": None, "decision_id": None,
                         "open_items": None} for ep in CHECKPOINTS],
        "searches": [],
        "settings": st,
        "counters": {"decision_seq": 0, "study_seq": 0, "evidence_seq": 0},
        "project_link": {"project_title": None, "studies_json": "03_adatok/studies.json",
                         "prisma_flow": "02_szures/prisma_flow.json"},
    }
    return state


def _tool_version():
    try:
        from . import __version__
        return "metaelemzes-headhunter/%s" % __version__
    except Exception:  # pragma: no cover
        return "metaelemzes-headhunter"


def is_initialized(project_dir):
    return os.path.isfile(path(project_dir, FILES["state"]))


def init_state(project_dir, question, pico=None, mode="harvest", criteria=None, exclusion_reasons=None,
               settings=None, actor=None, env=None, now=None, force=False):
    """A headhunter-mappa és a ``state.json`` létrehozása (L0). Létező állapotot csak ``force``-szal ír felül — a
    döntésnapló ilyenkor is megmarad (csak hozzáfűzés). Ha ``actor`` (``user:…``) adott, a PICO és a
    kritériumok jóváhagyása ``criteria_set`` döntésként kerül a naplóba."""
    if not os.path.isdir(project_dir):
        raise StateError("A projektmappa nem létezik: %s" % project_dir, "BAD_REQUEST")
    hd = hh_dir(project_dir)
    if is_initialized(project_dir) and not force:
        raise StateError("A Metaheadhunter már inicializálva van ebben a projektben (%s/state.json). A PICO "
                         "módosításához használd a --force kapcsolót (a döntésnapló megmarad)." % HH_REL,
                         "HH_ALREADY_INITIALIZED", "Metaheadhunter is already initialized (use --force).")
    for sub in ("reviews", "exports", "runs", "agent_classification", os.path.join("cache", "http")):
        d = os.path.join(hd, sub)
        if not os.path.isdir(d):
            os.makedirs(d)
    gi = os.path.join(hd, ".gitignore")
    if not os.path.exists(gi):
        write_text_atomic(gi, "# Metaheadhunter: gyorsítótár és futásnaplók nem kerülnek verziókezelésbe\n"
                              "cache/\nruns/\n.lock\n")
    with lock(project_dir):
        old = load_state(project_dir) if is_initialized(project_dir) else None
        state = new_state(question, pico=pico, mode=mode, criteria=criteria, exclusion_reasons=exclusion_reasons,
                          settings=settings, env=env, now=now)
        if old:
            # a futási előzmények (keresési napló, számlálók, forrás-állapotok) megmaradnak
            state["created"] = old.get("created") or state["created"]
            state["searches"] = old.get("searches") or []
            state["counters"] = dict(state["counters"], **(old.get("counters") or {}))
            state["sources"] = old.get("sources") or state["sources"]
            for s in STEPS:
                if (old.get("steps") or {}).get(s):
                    state["steps"][s] = old["steps"][s]
            state["checkpoints"] = old.get("checkpoints") or state["checkpoints"]
            mark_stale(state, "find_reviews", now=now)
        save_state(project_dir, state, env=env, now=now)
        if actor:
            d = append_decision(project_dir, "criteria_set", ("project", "pico"), "approved", actor, now=now,
                                reason="PICO és kritériumok jóváhagyva: %s" % state["pico"]["question"][:300],
                                kb_refs=["D-S03-101"])
            state["counters"]["decision_seq"] = max(state["counters"].get("decision_seq") or 0, _seq_of(d))
            save_state(project_dir, state, env=env, now=now)
    return state


def load_state(project_dir, required=True):
    """A ``state.json`` (``required=False`` mellett hiányzó állapotnál ``None``)."""
    st = read_json(path(project_dir, FILES["state"]))
    if not isinstance(st, dict):
        if required:
            raise StateError("A Metaheadhunter még nincs inicializálva ebben a projektben. Indítsd így: "
                             "python -m metaelemzes.headhunter init <projekt> --question \"…\"",
                             "HH_NOT_INITIALIZED", "Metaheadhunter is not initialized in this project (run init).")
        return None
    st.setdefault("steps", {})
    for s in STEPS:
        st["steps"].setdefault(s, {"status": "not_started", "updated": None, "run_id": None, "message": None})
    have = set(c.get("id") for c in st.get("checkpoints") or [])
    st.setdefault("checkpoints", [])
    for ep in CHECKPOINTS:
        if ep not in have:
            st["checkpoints"].append({"id": ep, "status": "pending", "decided_by": None, "at": None,
                                      "decision_id": None, "open_items": None})
    st["checkpoints"].sort(key=lambda c: c.get("id") or "")
    st.setdefault("searches", [])
    st.setdefault("counters", {"decision_seq": 0, "study_seq": 0})
    st.setdefault("settings", dict(DEFAULT_SETTINGS))
    return st


def _sanitize_sources(state, env=None):
    try:
        from . import sources as _src
        state["sources"] = _src.normalize_config(state.get("sources"), env)
    except Exception:  # pragma: no cover
        pass


def save_state(project_dir, state, env=None, now=None, validate_schema=True):
    """A ``state.json`` atomikus írása. A forrás-beállítás a környezet aktuális kulcs-állapotával frissül (csak
    igen/nem), a ``settings.contact_email_set`` igen/nem. Titok-szivárgás (H016) vagy sémahiba esetén nem ír."""
    st = state
    st["schema"] = STATE_SCHEMA
    st["model"] = MODEL
    st["updated"] = utc_now(now)
    _sanitize_sources(st, env)
    st.setdefault("settings", {})["contact_email_set"] = bool(
        (env if env is not None else os.environ).get("MA_CONTACT_EMAIL"))
    text = dump_json(st)
    leaks = secret_leaks(text, env)
    if leaks:
        raise StateError("Titok-szivárgás gyanúja (H016): a state.json-ba kulcs vagy e-mail-cím kerülne — nem írom "
                         "ki. Ellenőrizd a PICO/kritérium szövegét.", "H016")
    if validate_schema:
        errs = schema_errors(st, "state")
        if errs:
            raise StateError("A state.json nem felel meg a sémának (H001): %s" % "; ".join(errs[:5]), "H001")
    write_text_atomic(path(project_dir, FILES["state"]), text)
    return st


def mutate_state(project_dir, fn, env=None, now=None):
    """Olvasás–módosítás–írás zár alatt: ``fn(state)`` helyben módosít (visszatérési értéke figyelmen kívül
    marad). Visszaad: a mentett állapot."""
    with lock(project_dir):
        st = load_state(project_dir)
        fn(st)
        return save_state(project_dir, st, env=env, now=now)


def set_step(state, step, status, run_id=None, message=None, now=None, stale_downstream=False):
    """Egy lépés állapota (``state.steps.<lépés>``); ``stale_downstream``: a ráépülő kész lépések elavulnak."""
    if step not in STEPS:
        raise ValueError("Ismeretlen lépés: %r" % (step,))
    if status not in STEP_STATUSES:
        raise ValueError("Ismeretlen lépés-állapot: %r" % (status,))
    if run_id is not None and not _RUN_RE.match(str(run_id)):
        run_id = None
    state.setdefault("steps", {})[step] = {"status": status, "updated": utc_now(now), "run_id": run_id,
                                           "message": message}
    if stale_downstream:
        mark_stale(state, step, now=now)
    return state


def mark_stale(state, step, now=None):
    """A ``step`` lépésre épülő, már lefutott (``done``/``needs_human``) lépések ``stale`` állapotba kerülnek."""
    changed = []
    for s in DOWNSTREAM.get(step, ()):
        cur = (state.get("steps") or {}).get(s) or {}
        if cur.get("status") in ("done", "needs_human"):
            state["steps"][s] = dict(cur, status="stale", updated=utc_now(now),
                                     message={"hu": "Elavult: egy korábbi lépés (%s) kimenete megváltozott — futtasd "
                                                    "újra." % step,
                                              "en": "Stale: an earlier step (%s) changed — run it again." % step})
            changed.append(s)
    return changed


def get_checkpoint(state, ep):
    for c in state.get("checkpoints") or []:
        if c.get("id") == ep:
            return c
    c = {"id": ep, "status": "pending", "decided_by": None, "at": None, "decision_id": None, "open_items": None}
    state.setdefault("checkpoints", []).append(c)
    state["checkpoints"].sort(key=lambda x: x.get("id") or "")
    return c


def set_checkpoint(state, ep, status, open_items=None, decided_by=None, decision_id=None, at=None):
    if ep not in CHECKPOINTS:
        raise ValueError("Ismeretlen ellenőrzőpont: %r" % (ep,))
    if status not in ("pending", "done", "not_applicable"):
        raise ValueError("Ismeretlen ellenőrzőpont-állapot: %r" % (status,))
    c = get_checkpoint(state, ep)
    c["status"] = status
    c["open_items"] = None if open_items is None else max(0, int(open_items))
    if decided_by is not None:
        c["decided_by"] = decided_by
    if decision_id is not None:
        c["decision_id"] = decision_id
    if at is not None:
        c["at"] = at
    return c


def add_searches(state, entries):
    """Keresési napló (PRISMA-S): a ``state.searches[]`` bővítése; azonos ``search_id`` felülíródik."""
    by = dict((s.get("search_id"), i) for i, s in enumerate(state.setdefault("searches", [])))
    for e in entries or []:
        e = dict((k, v) for k, v in e.items() if not str(k).startswith("_"))
        if e.get("search_id") in by:
            state["searches"][by[e["search_id"]]] = e
        else:
            by[e.get("search_id")] = len(state["searches"])
            state["searches"].append(e)
    return state


def unique_search_id(source, ts, taken):
    """``s-<forrás>-<ÉÉÉÉHHNNTÓÓPPMMZ>`` (``-2``, ``-3`` … ütközésnél)."""
    base = "s-%s-%s" % (source, compact(ts))
    sid, n = base, 2
    while sid in taken:
        sid = "%s-%d" % (base, n)
        n += 1
    taken.add(sid)
    return sid


def exclusion_reason_label(state, code, lang="hu"):
    for r in (state or {}).get("exclusion_reasons") or []:
        if r.get("code") == code:
            lab = r.get("label") or {}
            return lab.get(lang) or lab.get("hu") or lab.get("en") or code
    return code


# =============================================================================================
# döntésnapló (decisions.jsonl) — csak hozzáfűzés, sha256-lánc (TERV 4.5/5)
# =============================================================================================

def _canonical_line(d):
    body = dict((k, v) for k, v in d.items() if k != "sha256")
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def decision_sha256(d):
    """A sor kanonikus JSON-jának (``sha256`` mező nélkül, ``sort_keys``, ``ensure_ascii=False``, ``(",", ":")``)
    sha256-ja — a ``dedup`` modullal bájtra azonos szabály."""
    return hashlib.sha256(_canonical_line(d).encode("utf-8")).hexdigest()


def read_decisions(project_dir):
    """A ``decisions.jsonl`` sorai (az olvashatatlan sor kimarad; a hash-láncot a ``verify_chain`` nézi)."""
    out = []
    try:
        with open(path(project_dir, FILES["decisions"]), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    out.append(d)
    except (OSError, IOError):
        pass
    return out


def verify_chain(decisions):
    """H015: a hash-lánc ellenőrzése. Visszaad: ``[{"index", "decision_id", "problem"}]`` (üres = ép)."""
    problems = []
    prev = ZERO_SHA
    for i, d in enumerate(decisions):
        if d.get("prev_sha256") != prev:
            problems.append({"index": i, "decision_id": d.get("decision_id"), "problem": "prev_sha256"})
        if d.get("sha256") != decision_sha256(d):
            problems.append({"index": i, "decision_id": d.get("decision_id"), "problem": "sha256"})
        prev = d.get("sha256") or ""
    return problems


def _seq_of(d):
    m = _DID_RE.match(str((d or {}).get("decision_id") or ""))
    return int(m.group(2)) if m else 0


def _next_seq(decisions, state=None):
    best = max([_seq_of(d) for d in decisions] or [0])
    try:
        best = max(best, int(((state or {}).get("counters") or {}).get("decision_seq") or 0))
    except (TypeError, ValueError):
        pass
    return best + 1


def check_actor(actor, kind=None):
    """A szereplő alakja ``user:``/``agent:``/``tool:``; emberi döntésnél (``HUMAN_KINDS``) csak ``user:``."""
    if not _ACTOR_RE.match(str(actor or "")):
        raise DecisionError("A döntéshozó alakja 'user:<név>' (pl. --actor user:SzK) — kapott: %r." % (actor,))
    if kind in HUMAN_KINDS and not str(actor).startswith("user:"):
        raise DecisionError("Ezt a döntést (%s) ember hozza: a szereplő 'user:<név>' alakú legyen; az ágens és a "
                            "program csak javasol (N3)." % kind)


def make_decision(kind, target_type, target_id, value, actor, prev_sha256=ZERO_SHA, seq=1, now=None, level=None,
                  reason_code=None, reason=None, evidence_ids=(), quote=None, proposed_by=None, batch=None,
                  kb_refs=(), supersedes=None, extra=None):
    """Egy döntés-sor (``szk.ma.headhunter.decision/v1``) hash-sel. Nem ír fájlt. ``extra``: további, a sémában
    additívan felvett mezők (pl. ``field``, ``review_id``, ``primary_locator`` a másodlagos adat ellenőrzéséhez)."""
    if kind not in DECISION_KINDS:
        raise DecisionError("Ismeretlen döntés-fajta: %r" % (kind,))
    if target_type not in TARGET_TYPES:
        raise DecisionError("Ismeretlen döntés-cél: %r" % (target_type,))
    check_actor(actor, kind)
    value = str(value if value is not None else "")
    if not 1 <= len(value) <= 80:
        raise DecisionError("A döntés értéke 1–80 karakter lehet.")
    if not str(target_id or "").strip():
        raise DecisionError("A döntés célja (azonosító) nem lehet üres.")
    if level is not None and level not in LEVELS:
        raise DecisionError("A szint 'title_abstract', 'full_text' vagy 'final' lehet.")
    if quote is not None and len(quote) > 300:
        raise DecisionError("Az idézet legfeljebb 300 karakter lehet (N4).")
    if reason is not None and len(reason) > 2000:
        raise DecisionError("Az indoklás legfeljebb 2000 karakter lehet.")
    if reason_code is not None and not re.match(r"^X\d{1,3}$", str(reason_code)):
        raise DecisionError("A kizárási ok kódja X<szám> alakú (pl. X3).")
    for name, text in (("reason", reason), ("quote", quote), ("batch", batch)):
        pats = phi_patterns(text) if text else []
        if pats:
            raise DecisionError("Betegadat-gyanú a döntés szövegében (mező: %s, minta: %s). A döntésnaplóba "
                                "azonosító (TAJ-szám, születési dátum, e-mail-cím) nem kerülhet — írd le azonosító "
                                "nélkül." % (name, ", ".join(pats)))
    ts = utc_now(now)
    d = {
        "schema": DECISION_SCHEMA,
        "decision_id": "d-%s-%04d" % (compact(ts), int(seq)),
        "ts": ts,
        "actor": actor,
        "kind": kind,
        "target": {"type": target_type, "id": str(target_id)},
        "value": value,
        "level": level,
        "reason_code": reason_code,
        "reason": reason,
        "evidence_ids": list(evidence_ids or []),
        "quote": quote,
        "proposed_by": proposed_by,
        "batch": batch,
        "kb_refs": list(kb_refs or []),
        "supersedes": supersedes,
    }
    for k, v in (extra or {}).items():
        if k in d or k in ("prev_sha256", "sha256"):
            continue
        if isinstance(v, str):
            pats = phi_patterns(v)
            if pats:
                raise DecisionError("Betegadat-gyanú a döntés szövegében (mező: %s, minta: %s)." % (k, ", ".join(pats)))
        d[k] = v
    d["prev_sha256"] = prev_sha256
    d["sha256"] = decision_sha256(d)
    return d


def append_decision(project_dir, kind, target, value, actor, now=None, state=None, **fields):
    """Döntés hozzáfűzése a ``decisions.jsonl``-hoz zár alatt. ``target``: ``(típus, id)`` vagy ``{"type", "id"}``.
    A sorszám a naplóból és a ``state.counters.decision_seq``-ból adódik (max + 1). A nem szabványos kulcsszavas
    mezők az ``extra``-ba kerülnek. Visszaad: a kiírt döntés-dict."""
    if isinstance(target, dict):
        ttype, tid = target.get("type"), target.get("id")
    else:
        ttype, tid = target
    std = ("level", "reason_code", "reason", "evidence_ids", "quote", "proposed_by", "batch", "kb_refs", "supersedes")
    kw = dict((k, fields.pop(k)) for k in list(fields) if k in std)
    extra = fields.pop("extra", None) or {}
    extra.update(fields)
    with lock(project_dir):
        existing = read_decisions(project_dir)
        if state is None:
            state = read_json(path(project_dir, FILES["state"]), {}) or {}
        problems = verify_chain(existing)
        if problems:
            raise DecisionError("A döntésnapló hash-lánca sérült (H015, %d hiba) — új döntés nem fűzhető hozzá, amíg "
                                "nem tisztázod (verify)." % len(problems))
        prev = existing[-1].get("sha256") if existing else ZERO_SHA
        d = make_decision(kind, ttype, tid, value, actor, prev_sha256=prev or ZERO_SHA,
                          seq=_next_seq(existing, state), now=now, extra=extra, **kw)
        fp = path(project_dir, FILES["decisions"])
        if not os.path.isdir(os.path.dirname(fp)):
            os.makedirs(os.path.dirname(fp))
        with open(fp, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return d


def effective_decisions(decisions, kinds=None, key=None):
    """A hatályos döntések célonként: a későbbi felülírja a korábbit, a ``supersedes``-zel visszavont kiesik.
    ``key(d)`` alapértelmezése ``(target.type, target.id)``. Visszaad: ``{kulcs: döntés}`` (``_order`` mezővel)."""
    superseded = set(d.get("supersedes") for d in decisions if d.get("supersedes"))
    out = {}
    for i, d in enumerate(decisions):
        if d.get("decision_id") in superseded:
            continue
        if kinds and d.get("kind") not in kinds:
            continue
        t = d.get("target") or {}
        k = key(d) if key else (t.get("type"), t.get("id"))
        dd = dict(d)
        dd["_order"] = i
        out[k] = dd
    return out


def batch_label(filters, now=None):
    """Tömeges döntés azonosítója a szűrőfeltétellel (N3: a szűrő a döntésben rögzül)."""
    desc = "; ".join("%s=%s" % (k, filters[k]) for k in sorted(filters) if filters[k] not in (None, "", False))
    return ("batch-%s: %s" % (compact(utc_now(now)), desc or "kifejezett lista"))[:500]


# =============================================================================================
# áttekintés-fájlok (reviews/<id>.json)
# =============================================================================================

def review_path(project_dir, review_id):
    if not re.match(r"^rv-[a-z0-9][a-z0-9-]{2,80}$", str(review_id or "")):
        raise StateError("Érvénytelen áttekintés-azonosító: %r" % (review_id,), "BAD_REQUEST")
    return path(project_dir, "reviews", "%s.json" % review_id)


def load_reviews(project_dir):
    """A ``reviews/*.json`` dokumentumok ``review_id`` szerint rendezve."""
    d = path(project_dir, "reviews")
    out = []
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json") and not fn.startswith("."):
            doc = read_json(os.path.join(d, fn))
            if isinstance(doc, dict) and doc.get("review_id"):
                out.append(doc)
    out.sort(key=lambda r: r["review_id"])
    return out


def load_review(project_dir, review_id):
    doc = read_json(review_path(project_dir, review_id))
    if not isinstance(doc, dict):
        raise StateError("Nincs ilyen forrás-áttekintés: %s" % review_id, "NOT_FOUND")
    return doc


def save_review(project_dir, doc):
    return write_json_atomic(review_path(project_dir, doc["review_id"]), doc)


def selected_reviews(reviews):
    """A kiválasztott (EP1) áttekintések; ha még egyik sincs kiválasztva, üres lista."""
    return [r for r in reviews if r.get("status") == "selected"]


def apply_review_decision(project_dir, review_id, value, decision_id):
    """``review_select`` döntés tükrözése a ``reviews/<id>.json``-ban (``include`` → ``selected``, ``exclude`` →
    ``excluded``)."""
    with lock(project_dir):
        doc = load_review(project_dir, review_id)
        doc["status"] = "selected" if value == "include" else "excluded"
        ids = doc.setdefault("decision_ids", [])
        if decision_id not in ids:
            ids.append(decision_id)
        save_review(project_dir, doc)
    return doc


def apply_candidate_decision(project_dir, review_id, cand_id, value, decision_id):
    """``candidate_confirm``/``candidate_reject`` tükrözése a jelölt ``status`` mezőjében."""
    with lock(project_dir):
        doc = load_review(project_dir, review_id)
        cand = next((c for c in doc.get("candidates") or [] if c.get("cand_id") == cand_id), None)
        if cand is None:
            raise StateError("Nincs ilyen jelölt: %s#%s" % (review_id, cand_id), "NOT_FOUND")
        cand["status"] = "confirmed" if value == "confirm" else "rejected"
        cand.pop("needs_review", None)
        if value == "confirm" and cand.get("role_in_review") == "unknown":
            # irodalomjegyzékből jött (szerep: ismeretlen) jelölt emberi megerősítése = bevont vizsgálat (EP2);
            # élő próbán enélkül a megerősített jelöltek kimaradtak a feloldásból (resolve csak a bevont szerepűeket
            # veszi). Az eredeti szerep és a döntés nyoma megmarad.
            cand["role_in_review"] = "included"
            cand["role_origin"] = {"extracted": "unknown", "set_by": decision_id}
        ids = cand.setdefault("decision_ids", [])
        if decision_id not in ids:
            ids.append(decision_id)
        save_review(project_dir, doc)
    return cand


# =============================================================================================
# futásnapló (runs/<run_id>/)
# =============================================================================================

_SECRET_ARG = re.compile(r"(?i)(api[_-]?key|insttoken|token|mailto|email|password|secret)")


def new_run_id(now=None):
    return "%s-%s" % (compact(utc_now(now)), hashlib.sha1(os.urandom(16)).hexdigest()[:6])


def clean_argv(argv):
    """Az argumentumlista kulcsok nélkül (a CLI-nek nincs kulcs-kapcsolója; biztonsági háló)."""
    out = []
    skip = False
    for a in argv or []:
        a = str(a)
        if skip:
            out.append("«redacted»")
            skip = False
            continue
        if a.startswith("--") and _SECRET_ARG.search(a):
            if "=" in a:
                out.append(a.split("=", 1)[0] + "=«redacted»")
            else:
                out.append(a)
                skip = True
            continue
        out.append(a)
    return out


class Run(object):
    """Egy hosszabb lépés futása: ``runs/<run_id>/run.json`` + ``progress.jsonl``; ``CANCEL`` → megszakítás."""

    def __init__(self, project_dir, step, argv=None, now=None, env=None):
        self.project_dir = project_dir
        self.step = step
        self.env = env
        self.run_id = new_run_id(now)
        self.dir = path(project_dir, "runs", self.run_id)
        self.started = time.time()
        self.started_ts = utc_now(now)
        self.argv = clean_argv(argv)
        os.makedirs(self.dir)
        self._write({"run_id": self.run_id, "step": step, "status": "running", "started": self.started_ts,
                     "argv": self.argv, "tool_version": _tool_version()})

    def _write(self, doc):
        text = dump_json(doc)
        if secret_leaks(text, self.env):
            doc = dict(doc, argv=["«redacted»"])
            text = dump_json(doc)
        write_text_atomic(os.path.join(self.dir, "run.json"), text)

    def progress(self, entry):
        """Egy ``progress.jsonl`` sor (``{"ts","step","phase","done","total","source","message":{hu,en}}``)."""
        e = dict(entry or {})
        e.setdefault("ts", utc_now())
        e.setdefault("step", self.step)
        line = json.dumps(e, ensure_ascii=False, sort_keys=True)
        if secret_leaks(line, self.env):
            return
        with open(os.path.join(self.dir, "progress.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")

    def cancelled(self):
        return os.path.exists(os.path.join(self.dir, "CANCEL"))

    def finish(self, status, sources=None, stats=None, exit_code=None, message=None):
        self._write({"run_id": self.run_id, "step": self.step, "status": status, "started": self.started_ts,
                     "finished": utc_now(), "duration_s": round(time.time() - self.started, 3), "argv": self.argv,
                     "tool_version": _tool_version(), "sources": sources or {}, "stats": stats or {},
                     "exit_code": exit_code, "message": message})
        return self


def new_run(project_dir, step, argv=None, now=None, env=None):
    return Run(project_dir, step, argv=argv, now=now, env=env)
