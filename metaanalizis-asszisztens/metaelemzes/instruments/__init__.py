# -*- coding: utf-8 -*-
"""Natív értékelő eszköz-definíciók (szk.instrument/v1; terv 4.11, 5.4, 9.3) és betöltőjük.

Egy eszköz = egy JSON-fájl ebben a mappában (<kulcs>.json): RoB 2, ROBINS-I, ROBINS-E, QUADAS-2, Newcastle–Ottawa,
QUIPS, JBI, PROBAST+AI, TRIPOD+AI, AMSTAR 2, GRADE. A tételek azonosítója, sorrendje, doménje és polaritás-címkéje a
szk-plugins validator (MIT, ugyanattól a szerzőtől) referenciafájljaiból származik — „forrás: szk-plugins validator
<verzió>” (a definíció 'source' mezője); a magyar szöveg és a súgó saját. A szó szerinti kérdésszövegek helyett
parafrázis áll a hivatalos számozással (eszközönként: 'licence').

    available()               → ['amstar2', 'grade', …] (a mappa *.json-jai)
    load(kulcs)               → Instrument (gyorsítótárazott, csak olvasásra)
    definition(kulcs)         → a definíció friss dict-másolata (+ 'sha256': a fájl bájtjainak hash-e)
    sha256(kulcs)             → a definíciófájl sha256-ja (az értékelés instrument_sha256-ja)
    index()                   → eszközönként egy összefoglaló sor (a munkapad eszköz-listája)
    fold(szöveg)              → a válasz-álnevek összevetési alakja (kisbetű, ékezet nélkül, '_' → szóköz)
    definition_problems(doc)  → a definíció belső ellentmondásai (álnév-ütközés, ismeretlen domén, számlálás …)
    contract_errors(doc, név) → a dokumentum eltérései a metaelemzes/contracts sémájától (a sémák részhalmaza)
    evaluate(feltétel, érték) → gépi feltétel (ask_if, rules[].if) háromértékű kiértékelése: True / False / None

A JSON-fájlok az egyetlen igazságforrás (kézzel szerkeszthetők; mentés: json.dumps(indent=2, ensure_ascii=False) +
"\n"). A validatortól való szerkezeti eltávolodást a tests/test_v1_instruments.py TestValidatorDrift őrzi (ha a
plugin elérhető; SZK_VALIDATOR_DIR), a validator 1.0.0 ismert polaritás-hibáinak javítását pedig eszközönként a
'validator_differences' mező rögzíti. Ahol a definíció a publikált eszközt követi a validator helyett (ROBINS-I 2016
tételkészlet, QUIPS a–g tételek), a 'validator_ids' mező őrzi a validator akkori azonosító-listáját: a sodródás-őr
azzal vet össze.

Gépi útválasztás és szabályok (v1, additív mezők):
    items[].ask_if        a 'condition' szöveg gépi párja: {"item": "2.3", "in": [...]}, {"all"|"any": [...]},
                          {"not": …}, {"always": true}. Hamis feltételnél a tételt nem kérdezik (a válasza nem
                          számít); igaz feltétel mellett a 'Nem alkalmazható' útválasztási ellentmondás, és a szabály
                          'Nincs információ'-ként kezeli (konzervatív).
    domains[].rules       sorrendben az első igaz szabály dönt ([{id, part?, scopes?, if, tier, because, kind?,
                          text?}]); több 'part' esetén a domén a legrosszabb rész. Szabály nélkül a polaritás-alapú
                          konzervatív szabály fut.
    domains[].verdict_labels  doménenkénti ítéletfelirat (pl. ROBINS-E D1: „alacsony, kivéve a nem kontrollált
                          zavaró tényezőket”).
    answers[].severity    'some': a problémás válasz csak a középső szintet kényszeríti (ROBINS-E „gyenge nem”).
    items[].official_id   a hivatalos tételszám, ha a kulcs eltér (Newcastle–Ottawa eset-kontroll).
    items[].parts         külön ítélt résztételek (AMSTAR 2 9. és 11.: RCT / NRSI); a tétel értéke a részekből adódik
                          (bármelyik 'Nem' → 'Nem').

Az álnév-tábla ESZKÖZÖNKÉNTI (H4 javítása): a 'PY' az AMSTAR 2-ben 'partial_yes', a RoB 2 / ROBINS / PROBAST+AI-ban
'probably_yes', a Newcastle–Ottawában pedig nem fogadott (kétértelmű)."""
import copy
import hashlib
import json
import math
import os
import re
import threading
import unicodedata

SCHEMA = "szk.instrument/v1"
DIR = os.path.dirname(os.path.abspath(__file__))
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
ITEM_KEY_RE = re.compile(r"^((development|evaluation)/)?[0-9A-Za-z.\-]+$")
PASSES = ("development", "evaluation")
UNITS = ("result", "study", "outcome", "model", "index_test", "review")
POLARITIES = ("normal", "reverse", "router", "none")
KINDS = ("affirmative", "negative", "unknown", "partial", "na", "rating", "start")
ALGORITHMS = ("published", "count", "conservative", "none")
LEVELS = ("low", "some", "high", "critical", "ni")
RULE_TIERS = ("low", "some", "high")
PART_ORDER = ("no", "partial_yes", "yes")


class InstrumentError(LookupError):
    """Ismeretlen eszköz vagy hibás definíció."""


def fold(text):
    """Összevetési alak: NFKD ékezetmentesítés, kisbetű, '_' és '-' → szóköz, összevont szóközök."""
    s = unicodedata.normalize("NFKD", str(text if text is not None else "")).encode("ascii", "ignore").decode("ascii")
    s = s.lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


def _i18n_text(v, lang="hu"):
    if isinstance(v, dict):
        return v.get(lang) or v.get("en") or v.get("hu") or ""
    return v if isinstance(v, str) else ""


def available():
    out = []
    for fn in os.listdir(DIR):
        if fn.endswith(".json") and KEY_RE.match(fn[:-5]):
            out.append(fn[:-5])
    return sorted(out)


def path(key):
    if not isinstance(key, str) or not KEY_RE.match(key) or key not in available():
        raise InstrumentError("ismeretlen értékelő eszköz: %r (elérhető: %s)" % (key, ", ".join(available())))
    return os.path.join(DIR, key + ".json")


def raw(key):
    with open(path(key), "rb") as fh:
        return fh.read()


def sha256(key):
    return hashlib.sha256(raw(key)).hexdigest()


def _reject_constant(token):
    raise ValueError("nem véges szám a definícióban: %s" % token)


_CACHE = {}
_LOCK = threading.Lock()


def load(key):
    """Instrument a definíciófájlból; a fájl változásakor (más sha256) újratölt."""
    data = raw(key)
    digest = hashlib.sha256(data).hexdigest()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit.sha256 == digest:
            return hit
    doc = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    inst = Instrument(doc, digest)
    with _LOCK:
        _CACHE[key] = inst
    return inst


def definition(key):
    """A definíció dict-másolata, kiegészítve a fájl hash-ével ('sha256' — ezt írja az értékelés
    instrument_sha256-jába a felület)."""
    inst = load(key)
    out = copy.deepcopy(inst.doc)
    out["sha256"] = inst.sha256
    return out


def index():
    return [load(k).summary() for k in available()]


# ------------------------------------------------------------------ gépi feltételek (ask_if, rules[].if)
def condition_items(cond):
    """A feltételben hivatkozott tételkulcsok (előfordulási sorrendben, ismétlés nélkül)."""
    out = []

    def walk(c):
        if not isinstance(c, dict):
            return
        if isinstance(c.get("item"), str) and c["item"] not in out:
            out.append(c["item"])
        for k in ("all", "any"):
            for sub in c.get(k) or ():
                walk(sub)
        if "not" in c:
            walk(c["not"])
    walk(cond)
    return out


def evaluate(cond, value_of):
    """Háromértékű kiértékelés: True / False / None (ismeretlen: hiányzó válasz). value_of(kulcs) → kanonikus
    érték vagy None. {"always": true} mindig igaz; hiányzó/üres feltétel igaz."""
    if cond is None or cond == {}:
        return True
    if not isinstance(cond, dict):
        return None
    if "always" in cond:
        return bool(cond["always"])
    if "item" in cond:
        v = value_of(cond["item"])
        if v is None:
            return None
        return v in (cond.get("in") or ())
    if "all" in cond:
        res = [evaluate(c, value_of) for c in cond.get("all") or ()]
        if any(r is False for r in res):
            return False
        return None if any(r is None for r in res) else True
    if "any" in cond:
        res = [evaluate(c, value_of) for c in cond.get("any") or ()]
        if any(r is True for r in res):
            return True
        return None if any(r is None for r in res) else False
    if "not" in cond:
        r = evaluate(cond["not"], value_of)
        return None if r is None else not r
    return None


# ------------------------------------------------------------------ az eszköz
class Instrument(object):
    """Egy szk.instrument/v1 definíció olvasási nézete (tételkulcs, hatókör, válasz-álnevek, ítéletek)."""

    def __init__(self, doc, digest=None):
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            raise InstrumentError("nem %s dokumentum" % SCHEMA)
        self.doc = doc
        self.sha256 = digest
        self.key = doc["key"]
        self.name = doc.get("name") or self.key
        self.unit = doc.get("unit")
        self.family = doc.get("family")
        self.items = list(doc.get("items") or [])
        self.by_key = {it["key"]: it for it in self.items}
        self.domains = list(doc.get("domains") or [])
        self.by_domain = {str(d["id"]): d for d in self.domains}
        self.answers = {}
        self._alias = {}
        self.alias_conflicts = []
        for a in doc.get("answers") or []:
            if isinstance(a, dict) and isinstance(a.get("value"), str):
                self.answers[a["value"]] = a
        for value, a in self.answers.items():
            for token in [value, a.get("label")] + list(a.get("aliases") or []):
                if not isinstance(token, str) or not token.strip():
                    continue
                f = fold(token)
                prev = self._alias.get(f)
                if prev is not None and prev != value:
                    self.alias_conflicts.append((token, prev, value))
                    continue
                self._alias[f] = value
        self.default_answers = list(doc.get("default_answers") or self.answers)
        self.verdicts = []
        self._verdict = {}
        for v in doc.get("verdicts") or []:
            if isinstance(v, str):
                v = {"value": v, "label": {"hu": v, "en": v}, "level": None}
            if isinstance(v, dict) and isinstance(v.get("value"), str):
                self.verdicts.append(v["value"])
                self._verdict[v["value"]] = v
        self.scopes = []
        for s in doc.get("scopes") or []:
            if isinstance(s, str):
                s = {"id": s, "label": {"hu": s, "en": s}, "default": False}
            if isinstance(s, dict) and isinstance(s.get("id"), str):
                self.scopes.append(s)
        self.scope_ids = [s["id"] for s in self.scopes]
        dflt = [s["id"] for s in self.scopes if s.get("default")]
        self.default_scope = dflt[0] if dflt else (self.scope_ids[0] if self.scope_ids else None)
        self.passes = [p["id"] for p in doc.get("passes") or [] if isinstance(p, dict)]
        self.rollup = dict(doc.get("rollup") or {})
        self.algorithm = self.rollup.get("algorithm", "none")
        self.rob_column = doc.get("rob_column") or None

    # -- hatókör és tételek
    def scope_of(self, scope):
        """A dokumentum hatóköre → érvényes hatókör-azonosító (None / üres: az alapértelmezett)."""
        if scope in (None, ""):
            return self.default_scope
        return scope if scope in self.scope_ids else None

    def slots(self, scope=None):
        """A hatókörbe eső tételek (sorrendben). Ismeretlen hatókör → []."""
        sc = self.scope_of(scope)
        if sc is None:
            return []
        return [it for it in self.items if not it.get("scopes") or sc in it["scopes"]]

    def item(self, key):
        return self.by_key.get(key)

    def domain(self, did):
        return self.by_domain.get(str(did))

    def domain_title(self, did, lang="hu"):
        d = self.domain(did)
        return _i18n_text(d.get("title"), lang) if d else str(did)

    def display_id(self, item):
        """A tétel hivatalos száma (official_id), különben az azonosítója."""
        if not isinstance(item, dict):
            return str(item)
        return item.get("official_id") or item.get("id")

    def rules(self, did, scope=None):
        """A domén gépi szabályai a hatókörre szűrve ([] → polaritás-alapú konzervatív szabály)."""
        d = self.domain(did)
        out = []
        for r in (d or {}).get("rules") or ():
            if isinstance(r, dict) and (not r.get("scopes") or scope in r["scopes"]):
                out.append(r)
        return out

    def parts(self, item):
        p = item.get("parts") if isinstance(item, dict) else None
        return [x for x in p if isinstance(x, dict) and isinstance(x.get("id"), str)] if isinstance(p, list) else []

    def part_allowed(self, item, part_id):
        for p in self.parts(item):
            if p["id"] == part_id:
                return list(p.get("answers") or self.allowed(item))
        return []

    def combine_parts(self, item, values):
        """Résztételek → a tétel értéke (AMSTAR 2: bármelyik 'Nem' → 'Nem'; különben 'Részben igen', ha van; különben
        'Igen'; ha minden rész 'Nem alkalmazható' → 'not_applicable', ha a tétel megengedi, különben None)."""
        vals = [v for v in values if isinstance(v, str)]
        if not vals:
            return None
        rated = [v for v in vals if v != "not_applicable"]
        for v in PART_ORDER:
            if v in rated:
                return v if v in self.allowed(item) else None
        if not rated:
            return "not_applicable" if "not_applicable" in self.allowed(item) else None
        return None

    # -- válaszok
    def allowed(self, item):
        vals = item.get("answers") if isinstance(item, dict) else None
        return list(vals) if vals else list(self.default_answers)

    def canonical(self, value, item=None):
        """Nyers válasz (kanonikus érték, rövidítés vagy álnév; magyarul is) → kanonikus érték, vagy None, ha nem
        értelmezhető, vagy a tételnél nem megengedett."""
        if isinstance(value, dict):
            value = value.get("value")
        if not isinstance(value, str) or not value.strip():
            return None
        v = value if value in self.answers else self._alias.get(fold(value))
        if v is None:
            return None
        if item is not None and v not in self.allowed(item):
            return None
        return v

    def kind(self, value):
        a = self.answers.get(value)
        return a.get("kind") if a else None

    def answer_severity(self, value):
        """A válasz saját szintje ('some': csak a középső szintet kényszeríti — ROBINS-E „gyenge nem”), vagy None."""
        a = self.answers.get(value)
        return a.get("severity") if a else None

    def answer_label(self, value, lang="hu"):
        a = self.answers.get(value)
        if not a:
            return str(value)
        return _i18n_text(a.get("text"), lang) or a.get("label") or value

    def signal(self, item, value):
        """Konzervatív jelzés egy válaszra: 'problem' | 'unknown' | 'partial' | None (rendben / irányító / N/A)."""
        pol = item.get("polarity", "normal")
        if pol in ("router", "none"):
            return None
        kind = self.kind(value)
        if kind == "unknown":
            return "unknown"
        if kind == "partial":
            return "partial"
        if kind == "na" or kind is None:
            return None
        if pol == "reverse":
            return "problem" if kind == "affirmative" else None
        return "problem" if kind == "negative" else None

    # -- ítéletek
    def verdict(self, value):
        return self._verdict.get(value)

    def level(self, value):
        """Ítélet → forgalmilámpa-szint (low | some | high | critical | ni) vagy None."""
        v = self._verdict.get(value)
        return v.get("level") if v else None

    def verdict_label(self, value, lang="hu", domain=None):
        """Az ítélet felirata; domain megadásakor a domén saját felirata (verdict_labels), ha van."""
        if domain is not None:
            d = self.domain(domain)
            own = ((d or {}).get("verdict_labels") or {}).get(value)
            if own:
                return _i18n_text(own, lang)
        v = self._verdict.get(value)
        return _i18n_text(v.get("label"), lang) if v else str(value)

    def rob_value(self, verdict):
        """Konszenzusos összítélet → a kinyerési tábla 'rob' cellájának értéke (None: nincs leképezés)."""
        if not self.rob_column:
            return None
        return self.rob_column.get(verdict)

    def summary(self):
        d = self.doc
        return {"key": self.key, "name": self.name, "label": d.get("label"), "unit": self.unit,
                "unit_label": d.get("unit_label"), "family": self.family, "rollup": {
                    "algorithm": self.algorithm, "official": bool(self.rollup.get("official")),
                    "basis": self.rollup.get("basis"), "label": self.rollup.get("label"),
                    "note": self.rollup.get("note")},
                "counts": d.get("counts"), "passes": list(self.passes), "scopes": list(self.scope_ids),
                "default_scope": self.default_scope, "items": len(self.items),
                "reference_sha256": d.get("reference_sha256"), "sha256": self.sha256,
                "validator_version": d.get("validator_version"),
                "attribution": (d.get("source") or {}).get("attribution"),
                "rob_column": bool(self.rob_column), "licence": (d.get("licence") or {}).get("instrument_licence")}


# ------------------------------------------------------------------ a definíció belső ellenőrzése
def definition_problems(doc):
    """A szk.instrument/v1 definíció ellentmondásai (a séma-ellenőrzésen túl) → [magyar üzenet]."""
    p = list(contract_errors(doc, "instrument", 1))
    if p:
        return p
    try:
        inst = Instrument(doc)
    except InstrumentError as exc:
        return [str(exc)]
    for token, a, b in inst.alias_conflicts:
        p.append("álnév-ütközés: %r → %s és %s" % (token, a, b))
    keys, ids = set(), set()
    for it in inst.items:
        k = it["key"]
        if k in keys:
            p.append("ismétlődő tételkulcs: %s" % k)
        keys.add(k)
        if not ITEM_KEY_RE.match(k):
            p.append("%s: a kulcs alakja nem megfelelő" % k)
        want = ("%s/%s" % (it["pass"], it["id"])) if it.get("pass") else it["id"]
        if k != want:
            p.append("%s: a kulcs nem a (menet/)azonosító (%s)" % (k, want))
        ids.add((it.get("pass"), it["id"]))
        if str(it["domain"]) not in inst.by_domain:
            p.append("%s: ismeretlen domén %r" % (k, it["domain"]))
        if it.get("pass") is not None and it["pass"] not in inst.passes:
            p.append("%s: ismeretlen menet %r" % (k, it["pass"]))
        for v in inst.allowed(it):
            if v not in inst.answers:
                p.append("%s: ismeretlen válaszérték %r" % (k, v))
        for s in it.get("scopes") or []:
            if s not in inst.scope_ids:
                p.append("%s: ismeretlen hatókör %r" % (k, s))
        if it.get("polarity", "normal") not in POLARITIES:
            p.append("%s: ismeretlen polaritás" % k)
    order = {it["key"]: i for i, it in enumerate(inst.items)}

    def cond_problems(cond, where, before=None):
        for ref in condition_items(cond):
            if ref not in order:
                p.append("%s: ismeretlen tételre hivatkozik (%s)" % (where, ref))
            elif before is not None and order[ref] >= before:
                p.append("%s: csak korábbi tételre hivatkozhat (%s)" % (where, ref))

        def values(c):
            if not isinstance(c, dict):
                return
            if "item" in c:
                for v in c.get("in") or ():
                    if v not in inst.answers:
                        p.append("%s: ismeretlen válaszérték %r" % (where, v))
            for k in ("all", "any"):
                for sub in c.get(k) or ():
                    values(sub)
            if "not" in c:
                values(c["not"])
        values(cond)

    for it in inst.items:
        if it.get("ask_if") is not None:
            cond_problems(it["ask_if"], "%s.ask_if" % it["key"], order[it["key"]])
            if "not_applicable" not in inst.allowed(it):
                p.append("%s: feltételes tétel ('ask_if'), de nem adható rá 'Nem alkalmazható'" % it["key"])
        seen_parts = set()
        for part in inst.parts(it):
            if part["id"] in seen_parts:
                p.append("%s.parts: ismétlődő rész %s" % (it["key"], part["id"]))
            seen_parts.add(part["id"])
            for v in part.get("answers") or ():
                if v not in inst.answers:
                    p.append("%s.parts[%s]: ismeretlen válaszérték %r" % (it["key"], part["id"], v))
    for d in inst.domains:
        rules = [r for r in d.get("rules") or () if isinstance(r, dict)]
        for i, r in enumerate(rules):
            where = "domains[%s].rules[%d]" % (d["id"], i)
            if r.get("tier") not in RULE_TIERS:
                p.append("%s.tier: %s egyike kell" % (where, " | ".join(RULE_TIERS)))
            cond_problems(r.get("if"), where + ".if")
            for ref in r.get("because") or ():
                if ref not in order:
                    p.append("%s.because: ismeretlen tétel %s" % (where, ref))
            for s in r.get("scopes") or ():
                if s not in inst.scope_ids:
                    p.append("%s.scopes: ismeretlen hatókör %r" % (where, s))
        if rules:
            for s in inst.scope_ids:
                in_scope = [r for r in rules if not r.get("scopes") or s in r["scopes"]]
                has_items = any(str(it["domain"]) == str(d["id"]) for it in inst.slots(s))
                for part in sorted({r.get("part") or "main" for r in in_scope}):
                    last = [r for r in in_scope if (r.get("part") or "main") == part][-1]
                    if (last.get("if") or {}).get("always") is not True:
                        p.append("domains[%s].rules (%s, %s): az utolsó szabály legyen {\"always\": true}" % (
                            d["id"], s, part))
                if has_items and not in_scope:
                    p.append("domains[%s].rules: a(z) %s hatókörre nincs szabály" % (d["id"], s))
    for a in inst.answers.values():
        if a.get("severity") not in (None, "some", "high"):
            p.append("answers[%s].severity: some | high" % a["value"])
    for v in inst.default_answers:
        if v not in inst.answers:
            p.append("default_answers: ismeretlen érték %r" % v)
    for a in inst.answers.values():
        if a.get("kind") not in KINDS:
            p.append("answers[%s].kind: %s egyike kell" % (a["value"], " | ".join(KINDS)))
    if inst.algorithm not in ALGORITHMS:
        p.append("rollup.algorithm: %s egyike kell" % " | ".join(ALGORITHMS))
    if inst.algorithm == "conservative":
        tiers = inst.rollup.get("tiers") or {}
        for t in ("low", "some", "high"):
            if tiers.get(t) not in inst.verdicts:
                p.append("rollup.tiers.%s: az ítéletskála eleme kell" % t)
    for v, col in (inst.rob_column or {}).items():
        if v not in inst.verdicts:
            p.append("rob_column: ismeretlen ítélet %r" % v)
    for v in inst.verdicts:
        if inst.level(v) not in LEVELS + (None,):
            p.append("verdicts[%s].level: %s egyike kell" % (v, " | ".join(LEVELS)))
    if not inst.default_scope:
        p.append("scopes: legalább egy hatókör kell")
    counts = doc.get("counts") or {}
    if counts.get("parsed") is not None and counts["parsed"] != len(inst.items):
        p.append("counts.parsed (%s) ≠ tételszám (%d)" % (counts["parsed"], len(inst.items)))
    if counts.get("published") is not None and counts["published"] != len(inst.items):
        p.append("counts.published (%s) ≠ tételszám (%d)" % (counts["published"], len(inst.items)))
    for s in inst.scope_ids:
        want = (counts.get("per_scope") or {}).get(s)
        if want is not None and want != len(inst.slots(s)):
            p.append("counts.per_scope.%s (%s) ≠ %d" % (s, want, len(inst.slots(s))))
    for ps, n in ((counts.get("per_pass") or {}).items()):
        if n != sum(1 for it in inst.items if it.get("pass") == ps):
            p.append("counts.per_pass.%s ≠ a menet tételszáma" % ps)
    return p


# ------------------------------------------------------------------ szerződés-ellenőrzés (séma-részhalmaz)
def _contracts():
    from .. import contracts
    return contracts


_REGISTRY = {}


def _registry():
    c = _contracts()
    key = []
    for n, v in c.available():
        st = os.stat(c.path(n, v))
        key.append((n, v, st.st_mtime_ns, st.st_size))
    key = tuple(key)
    reg = _REGISTRY.get(key)
    if reg is None:
        reg = c.registry()
        _REGISTRY.clear()
        _REGISTRY[key] = reg
    return reg


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_type(v, t):
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "string":
        return isinstance(v, str)
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "number":
        return _is_num(v) and (not isinstance(v, float) or math.isfinite(v))
    if t == "integer":
        return _is_num(v) and (isinstance(v, int) or (math.isfinite(v) and float(v).is_integer()))
    return False


def _json_eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_num(a) and _is_num(b):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_eq(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _non_finite(inst, where, out):
    if isinstance(inst, float) and not math.isfinite(inst):
        out.append("%s: nem véges szám" % where)
    elif isinstance(inst, dict):
        for k, v in inst.items():
            _non_finite(v, "%s.%s" % (where, k), out)
    elif isinstance(inst, list):
        for i, v in enumerate(inst):
            _non_finite(v, "%s[%d]" % (where, i), out)


class _Check(object):
    """A metaelemzes/contracts sémáinak kulcsszó-részhalmaza (ugyanaz, mint a ma_gui.schema_lite-é)."""

    def __init__(self, registry):
        self.registry = registry

    def resolve(self, ref, root):
        base, _, frag = ref.partition("#")
        node = self.registry[base] if base else root
        doc = node
        if frag:
            for part in frag.lstrip("/").split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                node = node[int(part)] if isinstance(node, list) else node[part]
        return node, doc

    def run(self, inst, schema, root, where, out):
        if schema is True:
            return
        if schema is False:
            out.append("%s: nem megengedett" % where)
            return
        if "$ref" in schema:
            target, troot = self.resolve(schema["$ref"], root)
            self.run(inst, target, troot, where, out)
        if "type" in schema:
            types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_is_type(inst, t) for t in types):
                out.append("%s: típus %s kell" % (where, "|".join(types)))
                return
        if "const" in schema and not _json_eq(inst, schema["const"]):
            out.append("%s: %r kell" % (where, schema["const"]))
        if "enum" in schema and not any(_json_eq(inst, e) for e in schema["enum"]):
            out.append("%s: %s egyike kell" % (where, schema["enum"]))
        if "oneOf" in schema:
            hits = 0
            for sub in schema["oneOf"]:
                tmp = []
                self.run(inst, sub, root, where, tmp)
                hits += 0 if tmp else 1
            if hits != 1:
                out.append("%s: pontosan egy változatnak kell megfelelnie (%d illeszkedik)" % (where, hits))
        if isinstance(inst, dict):
            for k in schema.get("required", ()):
                if k not in inst:
                    out.append("%s: hiányzó kötelező mező: %s" % (where, k))
            props = schema.get("properties", {})
            for k, v in inst.items():
                if k in props:
                    self.run(v, props[k], root, "%s.%s" % (where, k), out)
                elif "additionalProperties" in schema:
                    self.run(v, schema["additionalProperties"], root, "%s.%s" % (where, k), out)
        if isinstance(inst, list):
            if "items" in schema:
                for i, v in enumerate(inst):
                    self.run(v, schema["items"], root, "%s[%d]" % (where, i), out)
            if len(inst) < schema.get("minItems", 0) or len(inst) > schema.get("maxItems", float("inf")):
                out.append("%s: elemszám %d (minItems/maxItems)" % (where, len(inst)))
        if isinstance(inst, str):
            if "pattern" in schema and not re.search(schema["pattern"], inst):
                out.append("%s: nem illeszkedik a mintára" % where)
            if len(inst) < schema.get("minLength", 0) or len(inst) > schema.get("maxLength", float("inf")):
                out.append("%s: hossz %d (minLength/maxLength)" % (where, len(inst)))
        if _is_num(inst):
            if "minimum" in schema and inst < schema["minimum"]:
                out.append("%s: kisebb, mint %r" % (where, schema["minimum"]))
            if "maximum" in schema and inst > schema["maximum"]:
                out.append("%s: nagyobb, mint %r" % (where, schema["maximum"]))
            if "exclusiveMinimum" in schema and inst <= schema["exclusiveMinimum"]:
                out.append("%s: legfeljebb %r lehet" % (where, schema["exclusiveMinimum"]))
            if "exclusiveMaximum" in schema and inst >= schema["exclusiveMaximum"]:
                out.append("%s: legalább %r lehet" % (where, schema["exclusiveMaximum"]))


def contract_errors(doc, name, version=1):
    """A dokumentum eltérései a metaelemzes/contracts <név>.v<verzió> sémájától → [üzenet] (üres: megfelel)."""
    c = _contracts()
    reg = _registry()
    short, ver = c.parse(name, version)
    schema = reg[c.urn(short, ver if ver is not None else 1)]
    out = []
    _non_finite(doc, "$", out)
    _Check(reg).run(doc, schema, schema, "$", out)
    return out
