# -*- coding: utf-8 -*-
"""Metaheadhunter — L5: duplikátumok és vizsgálat-kapcsolás (TERV_metaheadhunter.md 8. fejezet), valamint a
közös döntésnapló (``decisions.jsonl``, 4.5–4.6) és projekt-fájl segédek.

Kezdőknek: ugyanaz a cikk több áttekintésben is szerepelhet (ez a *duplikátum*), és egy vizsgálatnak több
közleménye is lehet (fő eredmény, követés, protokoll, konferencia-absztrakt — ezek a *társközlemények*). Ez a
modul a közleményeket (rekordokat) vizsgálatokba rendezi:

* **L1 — azonos közlemény, biztos** (``certain``): közös PMID / DOI / PMCID / EID / OpenAlex-azonosító. Ezt a
  program automatikusan összevonja (``auto_applied``), de egy döntéssel visszavonható (``duplicate_reject``).
  Csak API-ból származó vagy API-val megerősített azonosító számít (az áttekintés szövegéből vett, meg nem
  erősített azonosító NEM). Ellentmondó azonosítók (pl. azonos DOI két PMID-del) → ``id_conflict`` (H007).
* **L2 — valószínűleg azonos közlemény** (``probable``/``possible``): cím-hasonlóság + első szerző + év.
  Mindig emberi döntés (EP3); kétség esetén megtartás (Bramer 2016; McKeown 2021).
* **L3 — azonos vizsgálat, más közlemény** (``probable``): közös *erős* regiszter-kapcsolat (a közlemény saját
  regisztrációs nyilatkozata: PubMed DataBank, absztrakt, Europe PMC annotáció; vagy erős + CT.gov
  ``RESULT``/``DERIVED``), vagy az áttekintés csoportosítása (Cochrane al-lista, „2.1/2.2" sorok). A csak CT.gov
  ``BACKGROUND`` alapú egyezés legfeljebb L4 (TERV 3.1: ez a típus megbízhatatlan).
* **L4 — lehetséges azonos vizsgálat** (``possible``): tipp (azonos első és utolsó szerző / hasonló cím, vagy
  csak gyenge regiszter-egyezés). Rejtett kettős közlésre figyelj (von Elm 2004; Tramèr 1997).

Az emberi döntések a ``decisions.jsonl``-ban élnek (csak hozzáfűzés, sha256-lánc); a ``studies.json``
klaszterei ezekből és a rekordokból **determinisztikusan újraszámolhatók** (``link`` / ``run_dedupe``). A
javaslat-azonosítók stabilak (az előző futás javaslataihoz illesztve), így a korábbi döntés a helyén marad.

Nyilvános API::

    doc = link(records, reviews, decisions, prior=studies_doc_or_None, now=None)  # → studies.json dokumentum
    run_dedupe(project_dir, now=None)                    # beolvas, újraszámol, atomikusan ír; összefoglalót ad
    duplicates_report(doc, reviews)                      # „ugyanaz a közlemény több áttekintésben" validálási lista
    select_proposals(proposals, kind=…, min_score=…, journal_match=…, …)   # tömeges jóváhagyás szűrője
    decide_proposal(project_dir, proposal_id, value, actor, reason=None)    # EP3 döntés (ember)
    decide_batch(project_dir, value, actor, reason=None, **filters)         # tömeges döntés, a szűrő a 'batch'-ben
    title_similarity(a, b), compare_bib(a, b), norm_text(s), norm_surname(name), rec_id_for(ids, cited_text)
    read_decisions(project_dir), append_decision(project_dir, …), verify_decision_chain(decisions),
    effective_decisions(decisions, kinds=None), ProjectLock(project_dir), write_json_atomic(path, doc)
"""
from __future__ import absolute_import

import copy
import difflib
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timezone

MODEL = "szk.ma.headhunter/v1"
STUDIES_SCHEMA = "szk.ma.headhunter.studies/v1"
DECISION_SCHEMA = "szk.ma.headhunter.decision/v1"
TOOL_ACTOR = "tool:headhunter"
ZERO_SHA = "0" * 64

#: az API-források (minden más azonosító-forrás — ``review``, ``user`` — API-megerősítést igényel, N1)
API_SOURCES = frozenset(["pubmed", "europepmc", "openalex", "scopus", "ctgov", "crossref", "pmc"])
#: L1 — közlemény-szintű azonosítók (a regiszterszám vizsgálat-szintű, ezért nincs itt)
L1_KINDS = ("pmid", "doi", "pmcid", "eid", "openalex")
#: a jelölt-szerepek, amelyek bevonás-állítást jelentenek
INCLUDED_ROLES = ("included", "included_companion")

TITLE_PROBABLE = 0.90
TITLE_POSSIBLE = 0.80
#: TERV 8.1: tiltólista a cím-normalizáláshoz
STOPWORDS = frozenset(["a", "an", "the", "of", "in", "on", "and", "for", "with", "to", "versus", "vs"])

#: a rekord-azonosító előtagjainak elsőbbsége (az összevonás „kanonikus" rekordja)
_REC_PRIORITY = ("rec-pmid-", "rec-doi-", "rec-pmcid-", "rec-eid-", "rec-oa-", "rec-nct-", "rec-x-")

#: a regiszter-kapcsolat erőssége (TERV 7. fejezet)
STRENGTH_RANK = {"strong": 3, "confirming": 2, "review": 1, "review_unconfirmed": 0, "weak": 0}

REPORT_ROLES = ("primary", "secondary", "companion", "protocol", "abstract", "registry_result", "erratum", "unknown")

KB_DEDUP = ["D-S04-101"]
KB_LINK = ["D-S04-102"]

__all__ = [
    "link", "run_dedupe", "duplicates_report", "classify_new_records", "membership_problems", "select_proposals",
    "decide_proposal",
    "decide_batch",
    "title_similarity", "compare_bib", "classify_l2", "l2_score", "norm_text", "norm_title", "norm_surname",
    "surname_display", "journal_key", "rec_id_for", "trusted_ids", "rec_sort_key",
    "read_decisions", "append_decision", "make_decision", "verify_decision_chain", "effective_decisions",
    "proposal_decision_kind", "ProjectLock", "LockError", "DecisionError", "write_json_atomic", "read_json",
    "dump_json", "hh_dir", "load_reviews", "load_studies", "utc_now", "candidate_index", "effective_origins",
    "canonical_map", "INCLUDED_ROLES", "API_SOURCES", "L1_KINDS",
]


# =============================================================================================
# idő és fájlok
# =============================================================================================

def utc_now(now=None):
    """UTC időbélyeg ``ÉÉÉÉ-HH-NNTÓÓ:PP:MMZ`` alakban. ``now``: ``None`` (most), ISO-szöveg, epoch vagy datetime."""
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


def hh_dir(project_dir):
    """A projekt headhunter-mappája: ``<projekt>/01_kereses/headhunter``."""
    return os.path.join(project_dir, "01_kereses", "headhunter")


def dump_json(doc):
    """Kanonikus formázás (a motor szerződés-konvenciója): ``indent=2``, ``ensure_ascii=False``, záró újsor."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, IOError):
        return default


def write_json_atomic(path, doc):
    """Atomikus írás (ideiglenes fájl ugyanabban a mappában + ``os.replace``, TERV 4.6)."""
    d = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(d):
        os.makedirs(d)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(dump_json(doc))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


class LockError(RuntimeError):
    """Az írászár nem szerezhető meg (egy másik folyamat dolgozik a projekten)."""


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class ProjectLock(object):
    """Írászár a headhunter-mappán: ``.lock`` (``O_CREAT|O_EXCL``, PID + időbélyeg; 10 perc után elavultnak
    tekintjük — TERV 4.6). Folyamaton belül újrabelépő (a lépés közben hozzáfűzött döntés nem akad el)."""

    def __init__(self, project_dir, timeout=30.0, stale_after=600.0, poll=0.1):
        self.path = os.path.join(hh_dir(project_dir), ".lock")
        self.timeout = float(timeout)
        self.stale_after = float(stale_after)
        self.poll = float(poll)
        self.stale_removed = False

    def _entry(self):
        with _LOCKS_GUARD:
            ent = _LOCKS.get(self.path)
            if ent is None:
                ent = _LOCKS[self.path] = {"rlock": threading.RLock(), "depth": 0}
            return ent

    def __enter__(self):
        ent = self._entry()
        if not ent["rlock"].acquire(timeout=self.timeout):
            raise LockError("A headhunter-mappa zárolva van (ugyanebben a folyamatban). Próbáld újra később.")
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
                        self.stale_removed = True
                    except OSError:
                        pass
                    continue
                if time.time() - start > self.timeout:
                    ent["rlock"].release()
                    raise LockError("A headhunter-mappát egy másik folyamat zárolja (%s). Várj, amíg befejezi; "
                                    "ha 10 percnél régebbi, a zár elavultnak számít." % self.path)
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


def load_reviews(project_dir):
    """A ``reviews/*.json`` dokumentumok ``review_id`` szerint rendezve."""
    d = os.path.join(hh_dir(project_dir), "reviews")
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


def load_studies(project_dir):
    return read_json(os.path.join(hh_dir(project_dir), "studies.json"))


# =============================================================================================
# döntésnapló (decisions.jsonl) — csak hozzáfűzés, sha256-lánc (TERV 4.5/5)
# =============================================================================================

class DecisionError(ValueError):
    """Érvénytelen döntés (pl. nem emberi szereplő emberi döntéshez, ismeretlen cél)."""


DECISION_KINDS = ("review_select", "candidate_confirm", "candidate_reject", "id_confirm", "duplicate_accept",
                  "duplicate_reject", "study_link", "study_split", "role_set", "screen", "final_inclusion",
                  "secondary_verify", "update_window", "source_config", "criteria_set", "checkpoint", "note")
TARGET_TYPES = ("review", "candidate", "record", "study", "proposal", "project", "source", "window", "checkpoint")
_ACTOR_RE = re.compile(r"^(user|agent|tool):[^\s].{0,99}$")
_DID_RE = re.compile(r"^d-(\d{8}T\d{6}Z)-(\d{4,6})$")


def _canonical_line(d):
    body = dict((k, v) for k, v in d.items() if k != "sha256")
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def decision_sha256(d):
    """A sor kanonikus JSON-jának (az ``sha256`` mező nélkül, ``sort_keys``, ``ensure_ascii=False``, ``(",", ":")``)
    sha256-ja (TERV 4.5/5)."""
    return hashlib.sha256(_canonical_line(d).encode("utf-8")).hexdigest()


def read_decisions(project_dir):
    """A ``decisions.jsonl`` sorai (érvénytelen sor kimarad; a hash-láncot a ``verify_decision_chain`` nézi)."""
    path = os.path.join(hh_dir(project_dir), "decisions.jsonl")
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
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


def verify_decision_chain(decisions):
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


def _decision_seq(decisions, state=None):
    best = 0
    for d in decisions:
        m = _DID_RE.match(str(d.get("decision_id") or ""))
        if m:
            best = max(best, int(m.group(2)))
    try:
        best = max(best, int(((state or {}).get("counters") or {}).get("decision_seq") or 0))
    except (TypeError, ValueError):
        pass
    return best


def make_decision(kind, target_type, target_id, value, actor, prev_sha256=ZERO_SHA, seq=1, now=None, level=None,
                  reason_code=None, reason=None, evidence_ids=(), quote=None, proposed_by=None, batch=None,
                  kb_refs=(), supersedes=None):
    """Egy döntés-sor (``szk.ma.headhunter.decision/v1``) hash-sel. Nem ír fájlt."""
    if kind not in DECISION_KINDS:
        raise DecisionError("Ismeretlen döntés-fajta: %r" % (kind,))
    if target_type not in TARGET_TYPES:
        raise DecisionError("Ismeretlen döntés-cél: %r" % (target_type,))
    if not _ACTOR_RE.match(str(actor or "")):
        raise DecisionError("A döntéshozó alakja 'user:<név>', 'agent:<név>' vagy 'tool:<név>' (kapott: %r)." % (actor,))
    value = str(value or "")
    if not 1 <= len(value) <= 80:
        raise DecisionError("A döntés értéke 1–80 karakter lehet.")
    if quote is not None and len(quote) > 300:
        raise DecisionError("Az idézet legfeljebb 300 karakter lehet (N4).")
    if reason is not None and len(reason) > 2000:
        raise DecisionError("Az indoklás legfeljebb 2000 karakter lehet.")
    if reason_code is not None and not re.match(r"^X\d{1,3}$", str(reason_code)):
        raise DecisionError("A kizárási ok kódja X<szám> alakú (pl. X3).")
    ts = utc_now(now)
    compact = re.sub(r"[-:]", "", ts)  # 20261005T102000Z
    d = {
        "schema": DECISION_SCHEMA,
        "decision_id": "d-%s-%04d" % (compact, int(seq)),
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
        "prev_sha256": prev_sha256,
    }
    d["sha256"] = decision_sha256(d)
    return d


def _state_module():
    try:
        from . import state as _st
        return _st if hasattr(_st, "append_decision") else None
    except Exception:  # pragma: no cover - a párhuzamos fejlesztés alatt átmenetileg hiányozhat
        return None


def append_decision(project_dir, kind, target, value, actor, now=None, state=None, **fields):
    """Döntés hozzáfűzése a ``decisions.jsonl``-hoz zár alatt (a sorszám a naplóból és — ha van —
    ``state.counters.decision_seq``-ból: max + 1). ``target``: ``(típus, id)`` vagy ``{"type", "id"}``.

    Ha a ``state`` modul elérhető, annak ``append_decision``-jét hívja (egyetlen író a projektben: betegadat-
    szűrés, sérült lánc esetén nem fűz hozzá); a hash-szabály a kettőben bájtra azonos. Visszaad: a kiírt
    döntés-dict. Hiba: ``DecisionError`` (magyar üzenettel)."""
    st = _state_module()
    if st is not None:
        try:
            return st.append_decision(project_dir, kind, target, value, actor, now=now, state=state, **fields)
        except getattr(st, "DecisionError", DecisionError) as exc:
            raise DecisionError(str(exc))
    if isinstance(target, dict):
        ttype, tid = target.get("type"), target.get("id")
    else:
        ttype, tid = target
    if kind != "note" and not str(actor or "").startswith("user:"):
        raise DecisionError("Ezt a döntést (%s) ember hozza: a szereplő 'user:<név>' alakú legyen (N3)." % kind)
    path = os.path.join(hh_dir(project_dir), "decisions.jsonl")
    with ProjectLock(project_dir):
        existing = read_decisions(project_dir)
        if verify_decision_chain(existing):
            raise DecisionError("A döntésnapló hash-lánca sérült (H015) — új döntés nem fűzhető hozzá, amíg nem "
                                "tisztázod (verify).")
        if state is None:
            state = read_json(os.path.join(hh_dir(project_dir), "state.json"), {})
        prev = existing[-1].get("sha256") if existing else ZERO_SHA
        d = make_decision(kind, ttype, tid, value, actor, prev_sha256=prev or ZERO_SHA,
                          seq=_decision_seq(existing, state) + 1, now=now, **fields)
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return d


def effective_decisions(decisions, kinds=None, key=None):
    """A hatályos döntések célonként: a későbbi felülírja a korábbit; a ``supersedes``-zel visszavont döntés
    kiesik. ``key(d)`` alapértelmezése ``(target.type, target.id)``. Visszaad: ``{kulcs: döntés}`` (a döntés
    ``_order`` mezőt kap: a sorszáma a naplóban)."""
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


def proposal_decision_kind(proposal_kind, value):
    """A javaslat-fajtához tartozó döntés-fajta (``same_report``/``id_conflict`` → ``duplicate_*``,
    ``same_study``/``split_study`` → ``study_link``/``study_split``, ``resolution`` → ``id_confirm``)."""
    v = str(value).lower()
    if proposal_kind in ("same_report", "id_conflict"):
        if v not in ("accept", "reject"):
            raise DecisionError("Duplikátum-javaslatra 'accept' vagy 'reject' adható.")
        return "duplicate_accept" if v == "accept" else "duplicate_reject"
    if proposal_kind == "same_study":
        if v not in ("accept", "reject"):
            raise DecisionError("Vizsgálat-kapcsolás javaslatra 'accept' vagy 'reject' adható.")
        return "study_link"
    if proposal_kind == "split_study":
        return "study_split"
    if proposal_kind == "resolution":
        return "id_confirm"
    raise DecisionError("Ismeretlen javaslat-fajta: %r" % (proposal_kind,))


def _verdict(d):
    """Egy javaslatra vonatkozó döntés → ``accept`` / ``reject`` / ``None``."""
    if not d:
        return None
    kind = d.get("kind")
    v = str(d.get("value") or "").lower()
    if kind == "duplicate_accept":
        return "accept"
    if kind == "duplicate_reject":
        return "reject"
    if kind in ("study_link", "study_split", "id_confirm", "note"):
        if v in ("accept", "accepted", "link", "yes", "split"):
            return "accept"
        if v in ("reject", "rejected", "no", "keep_separate"):
            return "reject"
    return None


# =============================================================================================
# normalizálás és hasonlóság (TERV 4.2, 8.1)
# =============================================================================================

_TRANSLIT = {"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss", "đ": "d", "Đ": "D",
             "ł": "l", "Ł": "L", "ı": "i", "þ": "th", "ð": "d"}


def norm_text(s):
    """NFKD, ékezet le, átírás (ø→o, æ→ae, œ→oe, ß→ss, đ→d, ł→l, ı→i), kisbetű, nem alfanumerikus → szóköz,
    szóközök összevonása (TERV 8.1)."""
    if not s:
        return ""
    s = "".join(_TRANSLIT.get(ch, ch) for ch in str(s))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^0-9a-z]+", " ", s.lower())
    return s.strip()


def title_tokens(s):
    return [t for t in norm_text(s).split() if t not in STOPWORDS]


def norm_title(s):
    """A cím összevetési alakja: ``norm_text`` + tiltólista (a, an, the, of, in, on, and, for, with, to, versus, vs)."""
    return " ".join(title_tokens(s))


def title_similarity(a, b):
    """``max(difflib.SequenceMatcher.ratio, token-Jaccard)`` a normalizált címeken (0–1); ``None``, ha bármelyik
    cím hiányzik (TERV 8.1)."""
    ta, tb = norm_title(a), norm_title(b)
    if not ta or not tb:
        return None
    if ta == tb:
        return 1.0
    ratio = difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio()
    sa, sb = set(ta.split()), set(tb.split())
    jac = float(len(sa & sb)) / len(sa | sb) if (sa | sb) else 0.0
    return round(max(ratio, jac), 4)


_INITIALS_TOKEN = re.compile(r"^(?:[A-Z]{1,3}|(?:[A-Z]\.){1,3}|[A-Z]\.?-[A-Z]\.?)$")


def surname_display(name):
    """Vezetéknév megjelenítési alakja: 'Tameris MD' → 'Tameris'; 'van Nielen, M.' → 'van Nielen';
    'J. W. Anderson' → 'Anderson'. ``None``, ha nem dönthető el."""
    if not name:
        return None
    s = re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(name))).strip()
    s = re.split(r"\bet\.?\s*al\b", s)[0].strip(" ,;.")
    if "," in s:
        s = s.split(",")[0]
    toks = [t for t in s.replace(".", ". ").split() if t]
    while len(toks) > 1 and _INITIALS_TOKEN.match(toks[-1].rstrip(",")):
        toks.pop()
    while len(toks) > 1 and _INITIALS_TOKEN.match(toks[0]) and ("." in toks[0] or len(toks[0]) == 1):
        toks.pop(0)
    out = " ".join(toks).strip(" .,;")
    return out or None


def norm_surname(name):
    """Az első szerző összevetési kulcsa: a normalizált vezetéknév kötőjel/szóköz nélkül (TERV 8.1)."""
    disp = surname_display(name)
    return norm_text(disp).replace(" ", "") if disp else ""


def journal_key(j):
    return norm_text(j).replace(" ", "") if j else ""


def _year(x):
    try:
        y = int(str(x)[:4])
    except (TypeError, ValueError):
        return None
    return y if 1800 <= y <= 2100 else None


def compare_bib(a, b):
    """Két ``bib`` jellemzői: ``title_sim``, ``first_author_match`` (None, ha bármelyik ismeretlen),
    ``year_diff``, ``journal_match`` (ISSN vagy normalizált név; None, ha ismeretlen)."""
    a = a or {}
    b = b or {}
    fa, fb = norm_surname(a.get("first_author")), norm_surname(b.get("first_author"))
    ya, yb = _year(a.get("year")), _year(b.get("year"))
    ja, jb = journal_key(a.get("journal")), journal_key(b.get("journal"))
    issn_a = set(x for x in (a.get("issn"), a.get("essn")) if x)
    issn_b = set(x for x in (b.get("issn"), b.get("essn")) if x)
    if issn_a and issn_b:
        jm = bool(issn_a & issn_b) or (bool(ja) and ja == jb)
    elif ja and jb:
        jm = ja == jb or (len(ja) >= 4 and len(jb) >= 4 and (ja.startswith(jb) or jb.startswith(ja)))
    else:
        jm = None
    return {
        "title_sim": title_similarity(a.get("title"), b.get("title")),
        "first_author_match": (fa == fb) if (fa and fb) else None,
        "year_diff": abs(ya - yb) if (ya is not None and yb is not None) else None,
        "journal_match": jm,
    }


def l2_score(f):
    """TERV 8.1: ``score = title_sim + 0,03 (ha a folyóirat egyezik) − 0,05 × évkülönbség``, [0, 1]-re vágva
    (csak rendezéshez és megjelenítéshez). ``None``, ha nincs cím-hasonlóság."""
    ts = f.get("title_sim")
    if ts is None:
        return None
    s = ts + (0.03 if f.get("journal_match") else 0.0) - 0.05 * (f.get("year_diff") or 0)
    return round(min(1.0, max(0.0, s)), 4)


def classify_l2(f):
    """L2 szabály: ``probable`` — cím ≥ 0,90 ÉS első szerző egyezik ÉS évkülönbség ≤ 1; ``possible`` — cím
    ≥ 0,80 ÉS (első szerző VAGY év ≤ 1). Cím nélkül (feloldatlan hivatkozás): azonos első szerző és azonos év
    → ``possible`` (build-döntés: csak tipp, ember dönt). Visszaad: ``(certainty|None, rule)``."""
    ts = f.get("title_sim")
    fam = f.get("first_author_match")
    yd = f.get("year_diff")
    if ts is not None:
        if ts >= TITLE_PROBABLE and fam is True and yd is not None and yd <= 1:
            return "probable", "L2-title-author-year"
        if ts >= TITLE_POSSIBLE and (fam is True or (yd is not None and yd <= 1)):
            return "possible", "L2-title-author-year"
        return None, None
    if fam is True and yd == 0:
        return "possible", "L2-author-year-notitle"
    return None, None


# =============================================================================================
# azonosítók és rekordok
# =============================================================================================

def _sha1_10(text):
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:10]


def _norm_id(kind, value):
    """Helyi azonosító-normalizálás (TERV 4.2) — szándékosan önálló (nem függ a párhuzamosan fejlesztett
    modulok belső függvényeitől)."""
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value)).strip()
    if kind == "pmid":
        m = re.match(r"^(?:pmid:?\s*)?0*(\d{1,9})$", s, re.I)
        return m.group(1) if m else None
    if kind == "pmcid":
        m = re.match(r"^(?:pmcid:?\s*)?(?:PMC)?0*(\d{1,9})$", s, re.I)
        return "PMC" + m.group(1) if m else None
    if kind == "doi":
        s = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", s, flags=re.I)
        s = re.sub(r"^doi:\s*", "", s, flags=re.I)
        m = re.search(r"10\.\d{4,9}/\S+", s)
        if not m:
            return None
        d = m.group(0).rstrip(".,;:]}'\"").lower()
        while d.endswith(")") and d.count("(") < d.count(")"):
            d = d[:-1]
        return d
    if kind == "nct":
        m = re.search(r"NCT\s?(\d{8})\b", s, re.I)
        return "NCT" + m.group(1) if m else None
    if kind == "eid":
        m = re.search(r"2-s2\.0-(\d+)", s)
        if m:
            return "2-s2.0-" + m.group(1)
        return "2-s2.0-" + s if re.match(r"^\d{5,}$", s) else None
    if kind == "openalex":
        m = re.search(r"(?:^|/)(W\d+)$", s, re.I)
        return m.group(1).upper() if m else None
    return s or None


def norm_id(kind, value):
    return _norm_id(kind, value)


def _idv(ids, kind):
    v = (ids or {}).get(kind)
    if isinstance(v, dict):
        return v.get("value")
    return v


def trusted(idval):
    """Az azonosító API-ból származik, vagy API-val megerősített (N1)."""
    if not isinstance(idval, dict) or not idval.get("value"):
        return False
    return idval.get("source") in API_SOURCES or bool(idval.get("confirmed_by"))


def trusted_ids(rec):
    """``{fajta: normalizált érték}`` a rekord megbízható (API-forrású vagy API-val megerősített) L1-azonosítóiból."""
    out = {}
    for k in L1_KINDS:
        iv = (rec.get("ids") or {}).get(k)
        if trusted(iv):
            v = _norm_id(k, iv["value"])
            if v:
                out[k] = v
    return out


def rec_id_for(ids, cited_text=None, salt=None):
    """Rekord-azonosító (TERV 4.2): ``rec-pmid-…`` → ``rec-doi-<sha1[:10]>`` → ``rec-pmcid-…`` → ``rec-eid-…`` →
    ``rec-oa-w…`` → ``rec-nct-nct…`` → ``rec-x-<sha1(normalizált hivatkozás)[:10]>``. Az ``ids`` értékei lehetnek
    idval-dictek vagy puszta szövegek; idval-nál CSAK megbízható azonosító számít.

    Build-döntés: rövid (6 szónál kevesebb) hivatkozás-szövegnél a ``salt`` (pl. ``<review_id>#<cand_id>``) is a
    hash része, hogy két áttekintés „Smith 2010" sora ne vonódjon össze azonosító nélkül."""
    def get(k):
        v = (ids or {}).get(k)
        if isinstance(v, dict):
            return _norm_id(k, v.get("value")) if trusted(v) else None
        return _norm_id(k, v)
    p = get("pmid")
    if p:
        return "rec-pmid-%s" % p
    d = get("doi")
    if d:
        return "rec-doi-%s" % _sha1_10(d)
    pc = get("pmcid")
    if pc:
        return "rec-pmcid-%s" % pc.lower()
    e = get("eid")
    if e:
        return "rec-eid-%s" % re.sub(r"\D", "", e[7:])
    w = get("openalex")
    if w:
        return "rec-oa-%s" % w.lower()
    n = get("nct")
    if n:
        return "rec-nct-%s" % n.lower()
    norm = norm_text(cited_text)
    if not norm and not salt:
        raise ValueError("rec_id_for: nincs azonosító és hivatkozás-szöveg sem")
    if len(norm.split()) < 6 and salt:
        norm = "%s|%s" % (salt, norm)
    return "rec-x-%s" % _sha1_10(norm)


def rec_sort_key(rec_id):
    for i, p in enumerate(_REC_PRIORITY):
        if str(rec_id).startswith(p):
            return (i, str(rec_id))
    return (len(_REC_PRIORITY), str(rec_id))


# =============================================================================================
# jelölt-index, kanonikus rekord, proveniencia
# =============================================================================================

def _selected_reviews(reviews):
    sel = [r for r in reviews if r.get("status") == "selected"]
    if sel:
        return sel, False
    return [r for r in reviews if r.get("status") not in ("excluded", "superseded")], True


def candidate_index(reviews, selected_only=True):
    """``{rec_id: [(review_id, cand)]}`` a jelöltek ``rec_id``-je szerint (a nem elutasított jelöltek).
    ``selected_only``: csak a kiválasztott (EP1) áttekintések jelöltjei."""
    revs = _selected_reviews(reviews)[0] if selected_only else list(reviews)
    out = {}
    for r in revs:
        for c in r.get("candidates") or []:
            if c.get("status") == "rejected" or not c.get("rec_id"):
                continue
            out.setdefault(c["rec_id"], []).append((r["review_id"], c))
    return out


def canonical_map(records):
    """``{rec_id: kanonikus rec_id}`` a ``merged_into`` láncok mentén (körbe-mutatás ellen védve)."""
    by = dict((r["rec_id"], r) for r in records)
    out = {}
    for rid in by:
        seen = set()
        cur = rid
        while True:
            r = by.get(cur)
            nxt = r.get("merged_into") if (r and r.get("status") == "merged_into") else None
            if not nxt or nxt in seen or nxt not in by:
                break
            seen.add(cur)
            cur = nxt
        out[rid] = cur
    return out


def effective_origins(records):
    """``{kanonikus rec_id: [origin…]}`` — a beolvasztott rekordok eredetével együtt (proveniencia)."""
    cmap = canonical_map(records)
    out = {}
    for r in records:
        tgt = cmap.get(r["rec_id"], r["rec_id"])
        lst = out.setdefault(tgt, [])
        for o in r.get("origins") or []:
            if o not in lst:
                lst.append(o)
    for k in out:
        out[k].sort(key=lambda o: (o.get("route") or "", o.get("review_id") or "", o.get("cand_id") or "",
                                   o.get("search_id") or ""))
    return out


# =============================================================================================
# union-find
# =============================================================================================

class _UF(object):
    def __init__(self, items=()):
        self.p = {}
        for x in items:
            self.p[x] = x

    def add(self, x):
        self.p.setdefault(x, x)

    def find(self, x):
        self.add(x)
        root = x
        while self.p[root] != root:
            root = self.p[root]
        while self.p[x] != root:
            self.p[x], x = root, self.p[x]
        return root

    def union(self, a, b, prefer=None):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        if prefer is not None:
            keep, drop = (ra, rb) if prefer(ra) <= prefer(rb) else (rb, ra)
        else:
            keep, drop = (ra, rb) if ra <= rb else (rb, ra)
        self.p[drop] = keep
        return keep

    def groups(self):
        out = {}
        for x in self.p:
            out.setdefault(self.find(x), []).append(x)
        return out


# =============================================================================================
# javaslatok: stabil azonosítók
# =============================================================================================

_PREFIX = {"L1": "l1", "L2": "l2", "L3": "l3", "L4": "l4"}


def _prefix_of(rule):
    lvl = str(rule or "").split("-")[0]
    return _PREFIX.get(lvl, "lx")


def _signature(kind, rule, items, cmap=None):
    its = sorted(set((cmap or {}).get(i, i) for i in items))
    return "%s|%s|%s" % (kind, rule, ",".join(its))


def _assign_ids(proposals, prior_props, counters, cmap):
    """Stabil javaslat-azonosítók: az előző futás azonos (fajta, szabály, tételek) javaslata megtartja az
    azonosítóját (a beolvasztott rekordokat a kanonikusra képezve); az új javaslat a következő sorszámot kapja
    (a sorszám soha nem csökken)."""
    seq = dict((counters or {}).get("proposal_seq") or {})
    prior_by_sig = {}
    for p in prior_props or []:
        prior_by_sig.setdefault(_signature(p.get("kind"), p.get("rule"), p.get("items") or [], cmap),
                                p.get("proposal_id"))
        m = re.match(r"^p-([a-z0-9]+)-(\d+)$", str(p.get("proposal_id") or ""))
        if m:
            seq[m.group(1)] = max(seq.get(m.group(1), 0), int(m.group(2)))
    used = set()
    pending_new = []
    for p in proposals:
        sig = _signature(p["kind"], p["rule"], p["items"], cmap)
        pid = prior_by_sig.get(sig)
        if pid and pid not in used:
            p["proposal_id"] = pid
            used.add(pid)
        else:
            pending_new.append((sig, p))
    for sig, p in sorted(pending_new, key=lambda x: x[0]):
        pre = _prefix_of(p["rule"])
        seq[pre] = seq.get(pre, 0) + 1
        p["proposal_id"] = "p-%s-%04d" % (pre, seq[pre])
    counters["proposal_seq"] = dict(sorted(seq.items()))
    return proposals


# =============================================================================================
# magyarázatok
# =============================================================================================

def _expl(hu, en):
    return {"hu": hu, "en": en}


def _fmt(x):
    return "—" if x is None else ("%.2f" % x if isinstance(x, float) else str(x))


def _l2_expl(f, certainty):
    hu = ("Valószínűleg ugyanaz a közlemény (nincs közös azonosító): cím-hasonlóság %s, első szerző %s, "
          "évkülönbség %s, folyóirat %s. Nézd meg egymás mellett; kétség esetén ne vond össze."
          % (_fmt(f.get("title_sim")), {True: "egyezik", False: "eltér", None: "ismeretlen"}[f.get("first_author_match")],
             _fmt(f.get("year_diff")), {True: "egyezik", False: "eltér", None: "ismeretlen"}[f.get("journal_match")]))
    en = ("Probably the same report (no shared identifier): title similarity %s, first author %s, year difference %s, "
          "journal %s. Compare side by side; if in doubt, keep them separate."
          % (_fmt(f.get("title_sim")), {True: "matches", False: "differs", None: "unknown"}[f.get("first_author_match")],
             _fmt(f.get("year_diff")), {True: "matches", False: "differs", None: "unknown"}[f.get("journal_match")]))
    if certainty == "possible":
        hu = hu.replace("Valószínűleg ugyanaz", "Lehet, hogy ugyanaz")
        en = en.replace("Probably the same", "Possibly the same")
    return _expl(hu, en)


# =============================================================================================
# L5 — kapcsolás
# =============================================================================================

def _record_study_hints(rec):
    """Regiszter-kapcsolatok erősség szerint: ``{id: legerősebb erősség}``."""
    out = {}
    for l in rec.get("registry_links") or []:
        rid = l.get("id")
        if not rid:
            continue
        s = l.get("strength") or "weak"
        if rid not in out or STRENGTH_RANK.get(s, 0) > STRENGTH_RANK.get(out[rid], 0):
            out[rid] = s
    ids = rec.get("ids") or {}
    nct = ids.get("nct")
    if isinstance(nct, dict) and nct.get("value") and trusted(nct):
        v = _norm_id("nct", nct["value"])
        own = nct.get("source") in ("pubmed", "europepmc") or (
            (rec.get("flags") or {}).get("registry_record") and nct.get("source") == "ctgov")
        if v and own and STRENGTH_RANK.get(out.get(v), -1) < STRENGTH_RANK["strong"]:
            out[v] = "strong"  # a közlemény saját nyilatkozata, ill. maga a regiszter-rekord
    return out


def _blocks(recs):
    """Blokkolás az L2-páros összevetéshez: azonos első szerző, vagy legalább két közös (nem túl gyakori)
    címszó. Visszaad: rendezett ``(a, b)`` párok halmaza."""
    pairs = set()
    by_author = {}
    token_index = {}
    toks = {}
    for r in recs:
        b = r.get("bib") or {}
        fa = norm_surname(b.get("first_author"))
        if fa:
            by_author.setdefault(fa, []).append(r["rec_id"])
        t = set(x for x in title_tokens(b.get("title")) if len(x) >= 3 and not x.isdigit())
        toks[r["rec_id"]] = t
        for x in t:
            token_index.setdefault(x, []).append(r["rec_id"])
    for lst in by_author.values():
        lst = sorted(set(lst))
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                pairs.add((lst[i], lst[j]))
    n = max(1, len(recs))
    limit = max(50, n // 10)
    shared = {}
    for tok, lst in token_index.items():
        if len(lst) > limit:
            continue
        lst = sorted(set(lst))
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                key = (lst[i], lst[j])
                shared[key] = shared.get(key, 0) + 1
    for key, cnt in shared.items():
        a, b = key
        if cnt >= 2 or min(len(toks.get(a) or ()), len(toks.get(b) or ())) <= 2:
            pairs.add(key)
    return pairs


def _surnames(bib):
    b = bib or {}
    first = norm_surname(b.get("first_author"))
    last = None
    authors = b.get("authors") or []
    if b.get("last_author"):
        last = norm_surname(b.get("last_author"))
    elif authors and not b.get("authors_truncated") and len(authors) > 1:
        last = norm_surname(authors[-1])
    return first, last


def _role_for(rec, cand_info):
    """Szerep és forrása a rekord jellemzői és az áttekintés jelölése alapján (ember nélkül)."""
    fl = rec.get("flags") or {}
    if fl.get("erratum"):
        return "erratum", "rule"
    if fl.get("protocol"):
        return "protocol", "rule"
    if fl.get("registry_record"):
        return "registry_result", "rule"
    if fl.get("conference_abstract"):
        return "abstract", "rule"
    for _rv, c in cand_info:
        if c.get("primary_marked"):
            return "primary", "review_grouping"
    for _rv, c in cand_info:
        if c.get("role_in_review") == "included_companion":
            return "companion", "review_grouping"
    return None, None


def link(records, reviews=(), decisions=(), prior=None, now=None):
    """Rekordok → vizsgálat-klaszterek és duplikátum-javaslatok (L1–L4), a döntésnapló alkalmazásával.

    ``records``: a ``studies.json`` ``records[]`` (a ``resolve`` lépés kimenete, más lépések rekordjaival
    együtt); ``reviews``: a ``reviews/*.json`` dokumentumok (csoportosítás, bevonás-állítások);
    ``decisions``: a ``decisions.jsonl`` sorai; ``prior``: az előző ``studies.json`` (stabil vizsgálat- és
    javaslat-azonosítókhoz és a ``resolve`` saját javaslataihoz, amelyeket változatlanul átvesz).

    Visszaad: új ``studies.json`` dokumentum (``szk.ma.headhunter.studies/v1``) — determinisztikus, a
    ``generated`` időbélyeg kivételével bájtra azonos ugyanarra a bemenetre."""
    prior = prior or {}
    ts = utc_now(now)
    counters = copy.deepcopy(prior.get("counters") or {})
    recs = {}
    for r in records:
        rr = copy.deepcopy(r)
        if rr.get("merged_by") == "dedupe":
            rr["status"] = "active"
            rr["merged_into"] = None
            rr.pop("merged_by", None)
        rr.setdefault("status", "active")
        rr.setdefault("merged_into", None)
        recs[rr["rec_id"]] = rr
    eff = effective_decisions(decisions)
    cidx = candidate_index(reviews, selected_only=True)

    def active_ids():
        return sorted((rid for rid, r in recs.items() if r.get("status") != "merged_into"), key=rec_sort_key)

    proposals = []
    prior_props, carried = [], []
    for p in prior.get("proposals") or []:
        if p.get("kind") in ("same_report", "same_study", "id_conflict", "split_study") and \
                str(p.get("rule") or "")[:2] in ("L1", "L2", "L3", "L4"):
            prior_props.append(p)
        else:
            carried.append(copy.deepcopy(p))  # a feloldás (resolve) saját javaslatai változatlanul maradnak
    cmap0 = canonical_map(list(recs.values()))

    # ---- L1: közös megbízható azonosító -------------------------------------------------------
    l1_pairs = {}
    index = {}
    for rid in active_ids():
        for k, v in trusted_ids(recs[rid]).items():
            index.setdefault((k, v), []).append(rid)
    for key in sorted(index):
        lst = sorted(set(index[key]), key=rec_sort_key)
        for other in lst[1:]:
            pair = (lst[0], other)
            l1_pairs.setdefault(pair, []).append("%s:%s" % key)
    l1_props = []
    for (a, b), shared in sorted(l1_pairs.items()):
        ta, tb = trusted_ids(recs[a]), trusted_ids(recs[b])
        conflicting = ["%s:%s|%s" % (k, ta[k], tb[k]) for k in L1_KINDS if k in ta and k in tb and ta[k] != tb[k]]
        first_kind = shared[0].split(":")[0]
        if conflicting:
            p = {"kind": "id_conflict", "items": sorted([a, b]), "score": None,
                 "rule": "L1-conflict",
                 "features": {"shared_ids": sorted(set(shared)), "conflicting_ids": conflicting},
                 "status": "pending", "decision_id": None,
                 "explanation": _expl(
                     "Ellentmondó azonosítók: a két rekord közös azonosítója (%s) mellett más azonosítójuk eltér (%s). "
                     "Nézd meg a forrás-API-k válaszát, és döntsd el, ugyanaz-e a közlemény (H007)."
                     % (", ".join(sorted(set(shared))), ", ".join(conflicting)),
                     "Conflicting identifiers: besides the shared identifier (%s) other identifiers differ (%s). "
                     "Check the source APIs and decide whether this is the same report (H007)."
                     % (", ".join(sorted(set(shared))), ", ".join(conflicting)))}
        else:
            p = {"kind": "same_report", "items": sorted([a, b]), "certainty": "certain", "score": 1.0,
                 "rule": "L1-%s" % first_kind, "features": {"shared_ids": sorted(set(shared))},
                 "status": "auto_applied", "decision_id": None,
                 "explanation": _expl(
                     "Ugyanaz a közlemény: közös azonosító (%s) — automatikusan összevonva; egy döntéssel "
                     "visszavonható." % ", ".join(sorted(set(shared))),
                     "Same report: shared identifier (%s) — merged automatically; can be undone with one decision."
                     % ", ".join(sorted(set(shared))))}
        l1_props.append(p)

    l2_props = []

    # azonosító-kiosztás és döntés-alkalmazás L1-re
    _assign_ids(l1_props, prior_props, counters, cmap0)
    uf = _UF(active_ids())
    for p in l1_props:
        d = eff.get(("proposal", p["proposal_id"]))
        v = _verdict(d)
        if p["kind"] == "same_report":
            if v == "reject":
                p["status"] = "rejected"
                p["decision_id"] = d["decision_id"]
                continue
            if v == "accept":
                p["decision_id"] = d["decision_id"]
            a, b = p["items"]
            # klaszter-szintű ütközés-ellenőrzés (tranzitív összevonás ne olvasszon össze ellentmondót)
            if _clusters_conflict(uf, recs, a, b):
                p["status"] = "superseded"
                p["explanation"] = _expl(
                    p["explanation"]["hu"] + " (Nem vontuk össze: a tranzitív összevonás ellentmondó azonosítót "
                                             "hozna egy rekordba — lásd az id_conflict javaslatot.)",
                    p["explanation"]["en"] + " (Not merged: the transitive merge would combine conflicting "
                                             "identifiers — see the id_conflict proposal.)")
                continue
            uf.union(a, b, prefer=rec_sort_key)
        else:  # id_conflict
            if v == "accept":
                p["status"] = "accepted"
                p["decision_id"] = d["decision_id"]
                uf.union(p["items"][0], p["items"][1], prefer=rec_sort_key)
            elif v == "reject":
                p["status"] = "rejected"
                p["decision_id"] = d["decision_id"]
    _apply_merges(recs, uf)
    proposals.extend(l1_props)

    # ---- L2 -----------------------------------------------------------------------------------
    act = [recs[r] for r in active_ids()]
    for a, b in sorted(_blocks(act)):
        ra, rb = recs[a], recs[b]
        if _shares_trusted(ra, rb):
            continue  # L1 már elintézte (vagy ütközésként kezeli)
        f = compare_bib(ra.get("bib"), rb.get("bib"))
        certainty, rule = classify_l2(f)
        if not certainty:
            continue
        p = {"kind": "same_report", "items": sorted([a, b]), "certainty": certainty, "score": l2_score(f),
             "rule": rule,
             "features": {"title_sim": f["title_sim"], "first_author_match": f["first_author_match"],
                          "year_diff": f["year_diff"], "journal_match": f["journal_match"], "shared_ids": []},
             "status": "pending", "decision_id": None, "explanation": _l2_expl(f, certainty)}
        if rule == "L2-author-year-notitle":
            p["explanation"] = _expl(
                "Lehet, hogy ugyanaz a közlemény: azonos első szerző és év, de legalább az egyiknek nincs címe "
                "(feloldatlan hivatkozás). Nézd meg a hivatkozás szövegét.",
                "Possibly the same report: same first author and year, but at least one has no title (unresolved "
                "citation). Check the citation text.")
        l2_props.append(p)
    cmap1 = canonical_map(list(recs.values()))
    _assign_ids(l2_props, prior_props, counters, cmap1)
    for p in l2_props:
        d = eff.get(("proposal", p["proposal_id"]))
        v = _verdict(d)
        if v == "accept":
            p["status"] = "accepted"
            p["decision_id"] = d["decision_id"]
            uf.union(p["items"][0], p["items"][1], prefer=rec_sort_key)
        elif v == "reject":
            p["status"] = "rejected"
            p["decision_id"] = d["decision_id"]
    _apply_merges(recs, uf)
    proposals.extend(l2_props)
    cmap = canonical_map(list(recs.values()))

    # ---- L3/L4: azonos vizsgálat --------------------------------------------------------------
    l2_rejected = [tuple(p["items"]) for p in l2_props if p["status"] == "rejected"]
    study_props = _study_proposals(recs, cmap, cidx, l2_rejected)
    _assign_ids(study_props, prior_props, counters, cmap)
    suf = _UF(sorted(r for r in recs if recs[r].get("status") != "merged_into"))
    split_dec = {}
    for (ttype, tid), d in eff.items():
        if ttype == "record" and d.get("kind") == "study_split":
            split_dec[cmap.get(tid, tid)] = d
    accepted_links = []
    for p in study_props:
        d = eff.get(("proposal", p["proposal_id"]))
        v = _verdict(d)
        if v == "accept":
            p["status"] = "accepted"
            p["decision_id"] = d["decision_id"]
            accepted_links.append((d["_order"], p))
        elif v == "reject":
            p["status"] = "rejected"
            p["decision_id"] = d["decision_id"]
    for order, p in sorted(accepted_links, key=lambda x: x[0]):
        items = [cmap.get(i, i) for i in p["items"]]
        items = [i for i in items if not (i in split_dec and split_dec[i]["_order"] > order)]
        for i in items[1:]:
            suf.union(items[0], i)
    # a már egy klaszterbe került, de el nem bírált javaslat „superseded" (nem kell külön döntés)
    for p in study_props:
        if p["status"] == "pending":
            roots = set(suf.find(cmap.get(i, i)) for i in p["items"] if cmap.get(i, i) in suf.p)
            if len(roots) == 1 and len(p["items"]) > 1:
                p["status"] = "superseded"
    for p in l2_props:
        if p["status"] == "pending" and cmap.get(p["items"][0]) == cmap.get(p["items"][1]):
            p["status"] = "superseded"
    proposals.extend(study_props)

    # ---- vizsgálatok ----------------------------------------------------------------------------
    studies = _build_studies(recs, suf, cmap, cidx, eff, prior, counters, proposals)

    proposals = sorted(proposals + carried, key=lambda p: p.get("proposal_id") or "")
    for p in proposals:
        p.setdefault("decision_id", None)
    out_records = [recs[r] for r in sorted(recs)]
    doc = {
        "schema": STUDIES_SCHEMA,
        "model": MODEL,
        "generated": ts,
        "records": out_records,
        "studies": studies,
        "proposals": proposals,
        "counters": counters,
        "summary": _summary(out_records, studies, proposals),
    }
    return doc


def _shares_trusted(ra, rb):
    ta, tb = trusted_ids(ra), trusted_ids(rb)
    return any(k in tb and tb[k] == v for k, v in ta.items())


def _clusters_conflict(uf, recs, a, b):
    ra, rb = uf.find(a), uf.find(b)
    if ra == rb:
        return False
    ids_a, ids_b = {}, {}
    for x, root in ((x, uf.find(x)) for x in list(uf.p)):
        if root == ra:
            for k, v in trusted_ids(recs[x]).items():
                ids_a.setdefault(k, set()).add(v)
        elif root == rb:
            for k, v in trusted_ids(recs[x]).items():
                ids_b.setdefault(k, set()).add(v)
    return any(k in ids_b and not (ids_a[k] & ids_b[k]) for k in ids_a)


def _apply_merges(recs, uf):
    for root, members in uf.groups().items():
        if len(members) < 2:
            continue
        canon = sorted(members, key=rec_sort_key)[0]
        for m in members:
            if m == canon:
                continue
            r = recs[m]
            if r.get("status") == "merged_into" and r.get("merged_by") != "dedupe":
                continue
            r["status"] = "merged_into"
            r["merged_into"] = canon
            r["merged_by"] = "dedupe"


def _merge_same_signature(props):
    """Azonos (fajta, szabály, tételek) javaslatok összevonása (pl. két áttekintés ugyanazt a csoportosítást adja)."""
    out = []
    by = {}
    for p in props:
        sig = _signature(p["kind"], p["rule"], p["items"])
        if sig in by:
            q = by[sig]
            for rv in p.get("reviews") or []:
                if rv not in q.setdefault("reviews", []):
                    q["reviews"].append(rv)
            q["reviews"].sort()
            continue
        by[sig] = p
        out.append(p)
    return out


def _study_proposals(recs, cmap, cidx, l2_rejected=()):
    """L3/L4 javaslatok (azonos vizsgálat) az aktív, kanonikus rekordokon."""
    props = []
    active = sorted((r for r in recs if recs[r].get("status") != "merged_into"), key=rec_sort_key)
    # --- áttekintés-csoportosítás (group_key): az áttekintés szerint ugyanaz a vizsgálat
    groups = {}
    for rec_id, lst in cidx.items():
        canon = cmap.get(rec_id, rec_id)
        if canon not in recs or recs[canon].get("status") == "merged_into":
            continue
        for rv, c in lst:
            if c.get("role_in_review") not in INCLUDED_ROLES:
                continue
            gk = c.get("group_key")
            if gk:
                groups.setdefault((rv, gk), set()).add(canon)
    for (rv, gk), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        items = sorted(members)
        props.append({
            "kind": "same_study", "items": items, "certainty": "probable", "score": None,
            "rule": "L3-review-grouping", "reviews": [rv],
            "features": {"shared_ids": [], "group_key": gk},
            "status": "pending", "decision_id": None,
            "explanation": _expl(
                "Az áttekintés ezeket a közleményeket egy vizsgálatként sorolja fel (%s). Ha egyetértesz, "
                "kapcsold őket egy vizsgálatba (áttekintésenként tömegesen is jóváhagyható)." % gk,
                "The review lists these reports as one study (%s). If you agree, link them into one study "
                "(can be approved in bulk per review)." % gk)})
    props = _merge_same_signature(props)
    # két áttekintés eltérően csoportosít → megjegyzés (TERV 8.1 L4)
    cand_groups = {}
    for rec_id, lst in cidx.items():
        canon = cmap.get(rec_id, rec_id)
        for rv, c in lst:
            if c.get("role_in_review") in INCLUDED_ROLES:
                cand_groups.setdefault(rv, {})[canon] = c.get("group_key") or ("cand:" + str(c.get("cand_id")))
    for p in props:
        disagree = []
        for rv, m in sorted(cand_groups.items()):
            if rv in p["reviews"]:
                continue
            present = [m[i] for i in p["items"] if i in m]
            if len(present) >= 2 and len(set(present)) > 1:
                disagree.append(rv)
        if disagree:
            p["features"]["disagreeing_reviews"] = disagree
            p["explanation"] = _expl(
                p["explanation"]["hu"] + " Figyelem: %s külön vizsgálatként kezeli őket." % ", ".join(disagree),
                p["explanation"]["en"] + " Note: %s treats them as separate studies." % ", ".join(disagree))
    # --- regiszter-kapcsolat
    by_reg = {}
    for r in active:
        for rid, strength in _record_study_hints(recs[r]).items():
            by_reg.setdefault(rid, {})[r] = strength
    for rid, members in sorted(by_reg.items()):
        if len(members) < 2:
            continue
        S = set(r for r, s in members.items() if s == "strong")
        C = set(r for r, s in members.items() if s == "confirming")
        R = set(r for r, s in members.items() if s in ("review", "review_unconfirmed"))
        W = set(r for r, s in members.items() if s == "weak")
        covered = set()
        if S and len(S | C) >= 2:
            items = sorted(S | C)
            covered |= set(items)
            props.append({
                "kind": "same_study", "items": items, "certainty": "probable", "score": None,
                "rule": "L3-registry", "features": {"shared_ids": [], "shared_registry": [rid]},
                "status": "pending", "decision_id": None,
                "explanation": _expl(
                    "Közös regisztrációs szám (%s): legalább az egyik közlemény maga nevezi meg (PubMed DataBank, "
                    "absztrakt vagy Europe PMC annotáció). Valószínűleg ugyanannak a vizsgálatnak a közleményei."
                    % rid,
                    "Shared registration number (%s): at least one report states it itself (PubMed DataBank, "
                    "abstract or Europe PMC annotation). Probably reports of the same study." % rid)})
        rr = S | C | R
        R_conf = set(r for r in R if members[r] == "review")
        if R_conf and len(rr) >= 2 and (S or len(R_conf) >= 2) and not rr <= covered:
            items = sorted(rr)
            covered |= rr
            props.append({
                "kind": "same_study", "items": items, "certainty": "probable", "score": None,
                "rule": "L3-review-registry", "features": {"shared_ids": [], "shared_registry": [rid]},
                "status": "pending", "decision_id": None,
                "explanation": _expl(
                    "Az áttekintés(ek) ugyanazt a regisztrációs számot (%s) adják meg ezekhez a közleményekhez (a "
                    "szám a ClinicalTrials.gov-on létezik), vagy az egyik közlemény maga is megnevezi. Valószínűleg "
                    "ugyanaz a vizsgálat." % rid,
                    "The review(s) give the same registration number (%s) for these reports (the number exists on "
                    "ClinicalTrials.gov), or one report states it itself. Probably the same study." % rid)})
        allx = S | C | R | W
        if len(allx) >= 2 and not allx <= covered:
            props.append({
                "kind": "same_study", "items": sorted(allx), "certainty": "possible", "score": None,
                "rule": "L4-registry-weak", "features": {"shared_ids": [], "shared_registry": [rid]},
                "status": "pending", "decision_id": None,
                "explanation": _expl(
                    "Csak gyenge regiszter-egyezés (%s): a közlemények egyike sem nevezi meg maga a számot, a "
                    "kapcsolat a CT.gov hivatkozás-listájából vagy meg nem erősített áttekintés-adatból jön. A CT.gov "
                    "hivatkozás-típusa önmagában megbízhatatlan (BACKGROUND alatt a saját eredményközlés és idegen "
                    "cikk is szerepelhet). Csak tipp." % rid,
                    "Only a weak registry match (%s): no report states the number itself; the link comes from the "
                    "CT.gov reference list or unconfirmed review data. The CT.gov reference type alone is unreliable "
                    "(BACKGROUND may list the trial's own results paper or an unrelated article). A hint only." % rid)})
    # --- erratum → szülő-közlemény (TERV 8.2: az erratum nem önálló közlemény)
    for r in active:
        rec = recs[r]
        parents = set()
        if rec.get("erratum_for"):
            parents.add(cmap.get(rec["erratum_for"], rec["erratum_for"]))
        for x in active:
            for rel in recs[x].get("related") or []:
                if rel.get("type") == "erratum_in" and cmap.get(rel.get("rec_id"), rel.get("rec_id")) == r:
                    parents.add(x)
        for parent in sorted(parents):
            if parent == r or parent not in recs or recs[parent].get("status") == "merged_into":
                continue
            props.append({
                "kind": "same_study", "items": sorted([parent, r]), "certainty": "probable", "score": None,
                "rule": "L3-erratum", "features": {"shared_ids": []},
                "status": "pending", "decision_id": None,
                "explanation": _expl(
                    "Az egyik rekord a másik közlemény hibajegyzéke (erratum) — nem önálló közlemény; kapcsold a "
                    "szülő-közlemény vizsgálatához.",
                    "One record is an erratum of the other report — not a standalone report; link it to the parent "
                    "report's study.")})
    # --- L4: azonos első és utolsó szerző (vagy erősen hasonló cím) — tipp
    seen_pairs = set()
    for p in props:
        its = p["items"]
        for i in range(len(its)):
            for j in range(i + 1, len(its)):
                seen_pairs.add(tuple(sorted((its[i], its[j]))))
    l2_rejected = set(tuple(sorted(x)) for x in l2_rejected)
    by_first = {}
    for r in active:
        fa, _la = _surnames(recs[r].get("bib"))
        if fa:
            by_first.setdefault(fa, []).append(r)
    for fa, lst in sorted(by_first.items()):
        lst = sorted(lst)
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = lst[i], lst[j]
                if (a, b) in seen_pairs:
                    continue
                ba, bb = recs[a].get("bib") or {}, recs[b].get("bib") or {}
                ya, yb = _year(ba.get("year")), _year(bb.get("year"))
                if ya is None or yb is None or abs(ya - yb) > 5:
                    continue
                _fa, la = _surnames(ba)
                _fb, lb = _surnames(bb)
                ta, tb = set(title_tokens(ba.get("title"))), set(title_tokens(bb.get("title")))
                jac = float(len(ta & tb)) / len(ta | tb) if (ta and tb) else 0.0
                if la and lb:
                    if la != lb:
                        continue
                    last_match = True
                elif jac >= 0.4:
                    last_match = False
                else:
                    continue
                f = compare_bib(ba, bb)
                if classify_l2(f)[0] and (a, b) not in l2_rejected:
                    continue  # függő L2 javaslat már van rá
                props.append({
                    "kind": "same_study", "items": [a, b], "certainty": "possible", "score": None,
                    "rule": "L4-author", "features": {"shared_ids": [], "first_author_match": True,
                                                      "year_diff": abs(ya - yb), "title_sim": f["title_sim"]},
                    "status": "pending", "decision_id": None,
                    "explanation": _expl(
                        "Lehetséges társközlemény: azonos első szerző%s, %d év különbség, cím-átfedés %.2f. Nézd meg, "
                        "ugyanaz a vizsgálat-e (rejtett kettős közlésre is figyelj)."
                        % (" és utolsó szerző" if last_match else "", abs(ya - yb), jac),
                        "Possible companion report: same first author%s, %d years apart, title overlap %.2f. Check "
                        "whether it is the same study (watch for covert duplicate publication)."
                        % (" and last author" if last_match else "", abs(ya - yb), jac))})
    return _merge_same_signature(props)


def _build_studies(recs, suf, cmap, cidx, eff, prior, counters, proposals):
    """Vizsgálat-klaszterek stabil ``st-…`` azonosítókkal, szerepekkel, címkével, proveniencával."""
    prior_map = {}
    for s in prior.get("studies") or []:
        for rp in s.get("reports") or []:
            prior_map.setdefault(rp.get("rec_id"), s.get("study_id"))
    try:
        seq = int(counters.get("study_seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    for s in prior.get("studies") or []:
        m = re.match(r"^st-(\d+)$", str(s.get("study_id") or ""))
        if m:
            seq = max(seq, int(m.group(1)))
    # a beolvasztott rekordok korábbi vizsgálata is a kanonikushoz tartozik
    members_all = {}
    for rid in recs:
        members_all.setdefault(cmap.get(rid, rid), []).append(rid)
    groups = sorted((sorted(v, key=rec_sort_key) for v in suf.groups().values()), key=lambda g: rec_sort_key(g[0]))
    claimed = set()
    assigned = []
    for g in groups:
        prior_ids = set()
        for r in g:
            for m in members_all.get(r, [r]):
                if prior_map.get(m):
                    prior_ids.add(prior_map[m])
        sid = None
        for cand in sorted(prior_ids, key=lambda x: int(x.split("-")[1])):
            if cand not in claimed:
                sid = cand
                break
        assigned.append([sid, g])
        if sid:
            claimed.add(sid)
    for item in assigned:
        if item[0] is None:
            seq += 1
            item[0] = "st-%04d" % seq
    counters["study_seq"] = seq

    role_dec = {}
    for (ttype, tid), d in eff.items():
        if ttype == "record" and d.get("kind") == "role_set" and d.get("value") in REPORT_ROLES:
            role_dec[cmap.get(tid, tid)] = d
    split_dec = dict(((cmap.get(tid, tid)), d) for (ttype, tid), d in eff.items()
                     if ttype == "record" and d.get("kind") == "study_split")
    prop_by_items = {}
    for p in proposals:
        if p.get("decision_id") and p.get("kind") in ("same_study", "same_report", "id_conflict"):
            for i in p["items"]:
                prop_by_items.setdefault(cmap.get(i, i), set()).add(p["decision_id"])

    studies = []
    for sid, g in assigned:
        reports = []
        cand_infos = {}
        for r in g:
            info = []
            for m in members_all.get(r, [r]):
                info.extend(cidx.get(m, []))
            cand_infos[r] = info
        # szerepek
        roles = {}
        for r in g:
            if r in role_dec:
                roles[r] = (role_dec[r]["value"], "human")
                continue
            role, src = _role_for(recs[r], cand_infos[r])
            if role:
                roles[r] = (role, src)
        if not any(v[0] == "primary" for v in roles.values()):
            pool = [r for r in g if r not in roles]
            if pool:
                def pkey(r):
                    b = recs[r].get("bib") or {}
                    y = _year(b.get("year")) or 9999
                    p = (recs[r].get("ids") or {}).get("pmid") or {}
                    try:
                        pm = int(p.get("value")) if isinstance(p, dict) and p.get("value") else 10 ** 10
                    except (TypeError, ValueError):
                        pm = 10 ** 10
                    return (y, pm, r)
                roles[sorted(pool, key=pkey)[0]] = ("primary", "rule")
        for r in g:
            if r not in roles:
                fl = recs[r].get("flags") or {}
                roles[r] = ("secondary" if fl.get("secondary_analysis") else "companion", "rule") if len(g) > 1 \
                    else ("primary", "rule")
            reports.append({"rec_id": r, "role": roles[r][0], "role_source": roles[r][1]})
        reports.sort(key=lambda x: (REPORT_ROLES.index(x["role"]) if x["role"] in REPORT_ROLES else 99,
                                    rec_sort_key(x["rec_id"])))
        primary = next((x["rec_id"] for x in reports if x["role"] == "primary"), reports[0]["rec_id"])
        # proveniencia: mely áttekintések vonták be (nem elutasított, bevonás-szerepű jelölt)
        rv = set()
        for r in g:
            for review_id, c in cand_infos[r]:
                if c.get("role_in_review") in INCLUDED_ROLES and c.get("status") != "rejected":
                    rv.add(review_id)
        # regiszterszámok (erős és megerősítő kapcsolat)
        reg = {}
        for r in g:
            for m in members_all.get(r, [r]):
                ids = recs[m].get("ids") or {}
                for iv in ([ids.get("nct")] if ids.get("nct") else []) + list(ids.get("registry") or []):
                    if isinstance(iv, dict) and iv.get("value") and trusted(iv) and iv["value"] not in reg:
                        reg[iv["value"]] = copy.deepcopy(iv)
        dec_ids = set()
        for r in g:
            dec_ids |= prop_by_items.get(r, set())
            if r in role_dec:
                dec_ids.add(role_dec[r]["decision_id"])
            if r in split_dec:
                dec_ids.add(split_dec[r]["decision_id"])
        st = {
            "study_id": sid,
            "label": None,
            "registry_ids": [reg[k] for k in sorted(reg)],
            "reports": reports,
            "reviews": sorted(rv),
            "decision_ids": sorted(dec_ids),
            "primary_rec_id": primary,
        }
        st["_label_base"] = _label_base(recs[primary], cand_infos.get(primary) or
                                        [x for r in g for x in cand_infos[r]])
        studies.append(st)
    # címke-ütközés: a, b utótag (vizsgálat-azonosító sorrendben)
    studies.sort(key=lambda s: int(s["study_id"].split("-")[1]))
    by_label = {}
    for s in studies:
        by_label.setdefault(s["_label_base"], []).append(s)
    for base, lst in by_label.items():
        if len(lst) == 1:
            lst[0]["label"] = base
        else:
            for i, s in enumerate(lst):
                s["label"] = "%s%s" % (base, chr(ord("a") + i) if i < 26 else "-%d" % (i + 1))
    for s in studies:
        s.pop("_label_base", None)
    return studies


def _label_base(rec, cand_info):
    b = rec.get("bib") or {}
    sn = surname_display(b.get("first_author"))
    y = _year(b.get("year"))
    if sn and y:
        return "%s %d" % (sn, y)
    for _rv, c in cand_info or []:
        if c.get("study_label_in_review"):
            return str(c["study_label_in_review"])[:80]
    for _rv, c in cand_info or []:
        ca = c.get("cited_as") or {}
        sn2 = surname_display(ca.get("first_author"))
        if sn2 and ca.get("year"):
            return "%s %s" % (sn2, ca.get("year"))
    if sn:
        return sn
    t = (rec.get("cited_as") or {}).get("text") or b.get("title") or rec["rec_id"]
    return str(t)[:60]


def _summary(records, studies, proposals):
    by = {}
    for p in proposals:
        key = "%s/%s" % (p.get("kind"), p.get("status"))
        by[key] = by.get(key, 0) + 1
    pending = [p for p in proposals if p.get("status") == "pending"]
    return {
        "records_total": len(records),
        "records_active": sum(1 for r in records if r.get("status") != "merged_into"),
        "records_merged": sum(1 for r in records if r.get("status") == "merged_into"),
        "studies": len(studies),
        "multi_report_studies": sum(1 for s in studies if len(s["reports"]) > 1),
        "proposals": dict(sorted(by.items())),
        "pending": len(pending),
        "pending_by_certainty": dict(sorted(
            ((c, sum(1 for p in pending if p.get("certainty") == c)) for c in ("probable", "possible"))
        )),
        "id_conflicts_open": sum(1 for p in pending if p.get("kind") == "id_conflict"),
    }


# =============================================================================================
# invariánsok (TERV 4.5/4; H020) — a checks modul is használhatja
# =============================================================================================

def membership_problems(doc):
    """H020: közlemény több vizsgálatban, vizsgálat nélküli aktív közlemény, vagy beolvasztott rekord vizsgálatban.
    Visszaad: ``[{"code": "H020", "rec_id", "problem", "studies"}]`` (üres = ép)."""
    records = (doc or {}).get("records") or []
    status = dict((r["rec_id"], r.get("status")) for r in records)
    seen = {}
    for s in (doc or {}).get("studies") or []:
        for rp in s.get("reports") or []:
            seen.setdefault(rp.get("rec_id"), []).append(s.get("study_id"))
    out = []
    for rid, sids in sorted(seen.items()):
        if len(sids) > 1:
            out.append({"code": "H020", "rec_id": rid, "problem": "multiple_studies", "studies": sorted(sids)})
        if status.get(rid) == "merged_into":
            out.append({"code": "H020", "rec_id": rid, "problem": "merged_record_in_study", "studies": sids})
        if rid not in status:
            out.append({"code": "H020", "rec_id": rid, "problem": "unknown_record", "studies": sids})
    for rid, st in sorted(status.items()):
        if st != "merged_into" and rid not in seen:
            out.append({"code": "H020", "rec_id": rid, "problem": "no_study", "studies": []})
    return out


# =============================================================================================
# új rekordok összevetése az ismert halmazzal (frissítő keresés, TERV 12.4)
# =============================================================================================

def classify_new_records(new_records, known_records):
    """Az új (pl. frissítő keresésből jött) rekordok besorolása az ismert halmazhoz képest: L1 (közös megbízható
    azonosító) → ``already_known`` (PRISMA D1, lábjegyzetben külön szám); L2 → ``possible_duplicates`` (EP3
    javaslat lesz a ``link``-ben); a többi → ``new``. Fájlt nem ír. Visszaad:
    ``{"already_known": [{"rec_id", "known_rec_id", "shared_ids"}], "possible_duplicates": [{"rec_id",
    "known_rec_id", "certainty", "score", "features"}], "new": [rec_id…]}``."""
    known = [r for r in known_records or [] if r.get("status") != "merged_into"]
    cmap = canonical_map(list(known_records or []))
    index = {}
    for r in known:
        for k, v in trusted_ids(r).items():
            index.setdefault((k, v), set()).add(r["rec_id"])
    out = {"already_known": [], "possible_duplicates": [], "new": []}
    for r in new_records or []:
        hits = {}
        for k, v in trusted_ids(r).items():
            for kid in index.get((k, v), ()):
                hits.setdefault(cmap.get(kid, kid), []).append("%s:%s" % (k, v))
        if hits:  # azonos rec_id (ugyanaz a PMID) is „már ismert"
            kid = sorted(hits, key=rec_sort_key)[0]
            out["already_known"].append({"rec_id": r["rec_id"], "known_rec_id": kid, "shared_ids": sorted(hits[kid])})
            continue
        best = None
        for _a, b in sorted(p for p in _blocks(known + [r]) if r["rec_id"] in p):
            other = b if _a == r["rec_id"] else _a
            if other == r["rec_id"]:
                continue
            f = compare_bib(r.get("bib"), (next((x for x in known if x["rec_id"] == other), {}) or {}).get("bib"))
            cert, _rule = classify_l2(f)
            if cert:
                cand = {"rec_id": r["rec_id"], "known_rec_id": other, "certainty": cert, "score": l2_score(f),
                        "features": f}
                if best is None or (cand["score"] or 0) > (best["score"] or 0):
                    best = cand
        if best:
            out["possible_duplicates"].append(best)
        else:
            out["new"].append(r["rec_id"])
    return out


# =============================================================================================
# validálási lista: ugyanaz a közlemény több áttekintésben
# =============================================================================================

def duplicates_report(doc, reviews):
    """A felhasználó kérése szerinti „duplumok validálása": közleményenként, mely áttekintések (melyik jelölt,
    milyen hivatkozás-szöveggel és bizonyítékkal) hivatkozták — a biztos (azonosító-alapú) és az elfogadott
    összevonásokkal együtt. Visszaad: ``[{rec_id, label, n_reviews, citations: [...], merged_from: [...]}]``."""
    records = doc.get("records") or []
    cmap = canonical_map(records)
    cidx = candidate_index(reviews, selected_only=True)
    by_canon = {}
    for rid, lst in cidx.items():
        canon = cmap.get(rid, rid)
        for rv, c in lst:
            by_canon.setdefault(canon, []).append({
                "review_id": rv, "cand_id": c.get("cand_id"), "rec_id": rid,
                "cited_as": (c.get("cited_as") or {}).get("text"), "label_in_review": c.get("study_label_in_review"),
                "role_in_review": c.get("role_in_review"), "evidence_ids": list(c.get("evidence_ids") or []),
                "confidence": c.get("confidence"), "status": c.get("status")})
    recs = dict((r["rec_id"], r) for r in records)
    out = []
    for canon, cits in sorted(by_canon.items()):
        if len(cits) < 2:
            continue
        cits.sort(key=lambda x: (x["review_id"], x["cand_id"] or ""))
        r = recs.get(canon) or {}
        b = r.get("bib") or {}
        out.append({
            "rec_id": canon,
            "label": _label_base(r, []) if r else canon,
            "title": b.get("title"),
            "n_reviews": len(set(c["review_id"] for c in cits)),
            "n_citations": len(cits),
            "merged_from": sorted(set(c["rec_id"] for c in cits if c["rec_id"] != canon)),
            "citations": cits,
        })
    return out


# =============================================================================================
# javaslat-szűrés, döntések (EP3)
# =============================================================================================

def select_proposals(proposals, kind=None, rule=None, certainty=None, status="pending", min_score=None,
                     journal_match=None, review_id=None, ids=None):
    """Javaslatok szűrése (tömeges jóváhagyáshoz, pl. ``kind="same_report", min_score=0.97, journal_match=True``)."""
    out = []
    for p in proposals or []:
        if kind and p.get("kind") != kind:
            continue
        if rule and not str(p.get("rule") or "").startswith(rule):
            continue
        if certainty and p.get("certainty") != certainty:
            continue
        if status and p.get("status") != status:
            continue
        if min_score is not None and (p.get("score") is None or p["score"] < float(min_score)):
            continue
        if journal_match is not None and (p.get("features") or {}).get("journal_match") is not bool(journal_match):
            continue
        if review_id and review_id not in (p.get("reviews") or []):
            continue
        if ids and p.get("proposal_id") not in ids:
            continue
        out.append(p)
    return out


def _require_human(actor):
    if not str(actor or "").startswith("user:"):
        raise DecisionError("Ezt a döntést ember hozza: a szereplő 'user:<név>' alakú legyen (az ágens csak javasol, N3).")


def decide_proposal(project_dir, proposal_id, value, actor, reason=None, batch=None, now=None, supersedes=None):
    """EP3 döntés egy javaslatra (``accept``/``reject``; feloldásnál ``pmid:…``/``option:N``/``reject``).
    A javaslat fajtájából választja a döntés-fajtát; csak ember (``user:``) dönthet."""
    _require_human(actor)
    doc = load_studies(project_dir) or {}
    prop = next((p for p in doc.get("proposals") or [] if p.get("proposal_id") == proposal_id), None)
    if prop is None:
        raise DecisionError("Nincs ilyen javaslat: %s (futtasd: proposals)." % proposal_id)
    kind = proposal_decision_kind(prop["kind"], value if prop["kind"] != "resolution" else "accept")
    kb = KB_LINK if prop["kind"] in ("same_study", "split_study") else (["D-S03-103"] if prop["kind"] == "resolution"
                                                                       else KB_DEDUP)
    return append_decision(project_dir, kind, ("proposal", proposal_id), str(value), actor, now=now, reason=reason,
                           proposed_by=TOOL_ACTOR, batch=batch, kb_refs=kb, supersedes=supersedes)


def decide_batch(project_dir, value, actor, reason=None, now=None, **filters):
    """Tömeges döntés a szűrőnek megfelelő FÜGGŐ javaslatokra; a szűrő szövege a döntés ``batch`` mezőjébe kerül
    (N3: tömeges jóváhagyás csak kifejezett emberi művelettel, a szűrőfeltétel rögzítésével)."""
    _require_human(actor)
    doc = load_studies(project_dir) or {}
    filters.setdefault("status", "pending")
    sel = select_proposals(doc.get("proposals") or [], **filters)
    if not sel:
        return []
    ts = utc_now(now)
    desc = "; ".join("%s=%s" % (k, filters[k]) for k in sorted(filters) if filters[k] is not None)
    batch = ("batch-%s: %s" % (re.sub(r"[-:]", "", ts), desc))[:500]
    out = []
    for p in sel:
        out.append(decide_proposal(project_dir, p["proposal_id"], value, actor, reason=reason, batch=batch, now=ts))
    return out


# =============================================================================================
# projekt-szintű lépés
# =============================================================================================

def run_dedupe(project_dir, now=None):
    """L5 a projekten: ``studies.json`` + ``reviews/*.json`` + ``decisions.jsonl`` → új ``studies.json``
    (atomikusan, zár alatt). Visszaad: ``{ok, summary, pending, duplicates, warnings, next}``."""
    with ProjectLock(project_dir):
        prior = load_studies(project_dir)
        if not prior:
            return {"ok": False, "errors": [_expl("Még nincs studies.json — előbb futtasd a feloldást (resolve).",
                                                  "No studies.json yet — run resolve first.")],
                    "next": "resolve"}
        reviews = load_reviews(project_dir)
        decisions = read_decisions(project_dir)
        warnings = []
        chain = verify_decision_chain(decisions)
        if chain:
            warnings.append({"code": "H015", "hu": "A döntésnapló hash-lánca sérült (%d hiba)." % len(chain),
                             "en": "The decision log hash chain is broken (%d problems)." % len(chain)})
        doc = link(prior.get("records") or [], reviews, decisions, prior=prior, now=now)
        write_json_atomic(os.path.join(hh_dir(project_dir), "studies.json"), doc)
    pend = doc["summary"]["pending"]
    return {"ok": True, "summary": doc["summary"], "pending": [{"checkpoint": "EP3", "n": pend}] if pend else [],
            "duplicates": len(duplicates_report(doc, reviews)), "warnings": warnings,
            "next": "decide" if pend else "overlap"}
