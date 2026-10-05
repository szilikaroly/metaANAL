# -*- coding: utf-8 -*-
"""Metaheadhunter — forrás-áttekintések felkutatása (L1): ``find_reviews(query, filters)``.

Kezdőknek: ez a modul a témádban már megjelent szisztematikus áttekintéseket és metaanalíziseket keresi meg
a kiválasztott forrásokban (PubMed, Europe PMC, OpenAlex, Scopus), összevonja az ugyanarra a cikkre mutató
találatokat (közös PMID/DOI/PMCID/EID/OpenAlex-azonosító), és rangsorolt jelöltlistát ad:

* bibliográfiai adatok és azonosítók — minden azonosító mellett, melyik API adta (``idval``: ``source``,
  ``via``, ``at``),
* nyílt teljes szöveg elérhetősége (Europe PMC ``isOpenAccess``, PMCID),
* a keresési dátum, ha az absztraktból (vagy kérésre a nyílt teljes szövegből) kiolvasható — szó szerinti
  idézettel; ha nem, a megjelenés − 12 hónap TARTALÉK (``fallback: true``, H008, emberi jóváhagyás kell),
* a közölt vizsgálatszám (k) idézettel, AMSTAR 2-höz kapcsolódó JELZÉSEK (nem értékelés!),
* a rangsor komponensei (csak sorrend, nem minőségítélet; TERV 5.3).

Elérhetetlen forrás (kvóta, kulcs, hálózat) nem állítja meg a keresést: a forrás kimarad, a kimenet ezt
kimondja (H014, ``exit_code = 3``). Az absztraktot és a teljes szöveget csak memóriában használjuk — a
kimenetben csak ≤ 300 karakteres idézet szerepel (N4).

Fájlt nem ír: a hívó (``discover``/CLI) menti a ``reviews/<id>.json``-t (``to_review_doc``) és a
``state.searches[]`` sorokat (``result["searches"]``).
"""

from __future__ import absolute_import

import hashlib
import math
import re
import unicodedata
from datetime import datetime, timezone

from . import net
from .net import (SourceUnavailable, HttpError, ParseError, norm_pmid, norm_pmcid, norm_doi, norm_eid, norm_openalex,
                  as_list, to_int)

MODEL = "szk.ma.headhunter/v1"
REVIEW_SCHEMA = "szk.ma.headhunter.review/v1"
QUOTE_MAX = 300

DISCOVERY_SOURCES = ("pubmed", "europepmc", "openalex", "scopus")

#: a rangsor alapsúlyai (TERV 5.3; a ``settings``-ből felülírhatók; összegük 1)
#: A relevancia (a P és I fogalom a címben; csak az absztraktban: fél pont) a legnagyobb súly: egy élő próbán
#: (BCG–tuberkulózis) 0,25-ös súllyal a témába vágó klasszikus metaanalízisek (Colditz 1994, Mangtani 2014) a
#: 80–110. helyre csúsztak a friss, nyílt, de más kérdésű (diagnosztikai) áttekintések mögé.
DEFAULT_WEIGHTS = {"relevance": 0.60, "recency": 0.06, "size": 0.04, "systematic": 0.06, "meta_analysis": 0.06,
                   "cochrane": 0.06, "open_fulltext": 0.06, "signals": 0.06}

_ID_PRIORITY = {
    "pmid": ("pubmed", "europepmc", "scopus", "openalex"),
    "pmcid": ("pubmed", "europepmc", "openalex", "scopus"),
    "doi": ("pubmed", "europepmc", "scopus", "openalex"),
    "eid": ("scopus",),
    "openalex": ("openalex",),
}
_BIB_PRIORITY = ("pubmed", "europepmc", "scopus", "openalex")

_STOP = set("a an the of in on and or not for with to versus vs by at from as is are be than into among "
            "effect effects".split())
_FIELD_TAG = re.compile(r"\[[A-Za-z :/_-]{1,40}\]")


# ---------------------------------------------------------------------------------------------
# Lekérdezés-építés (TERV 5.1)
# ---------------------------------------------------------------------------------------------

def _strip_tags(q):
    """PubMed-mezőcímkék (``[tiab]``, ``[mh]`` …) eltávolítása más forrásokhoz."""
    return re.sub(r"\s+", " ", _FIELD_TAG.sub("", q or "")).strip()


def _quote(term):
    t = str(term).replace('"', " ").strip()
    if not t:
        return None
    if "*" in t and " " not in t:
        return t
    return '"%s"' % t if (" " in t or "-" in t) else t


def _blocks_from(query):
    """A ``state.pico.query_blocks`` (lista) vagy ``{"P": [...], "I": [...]}`` alak egységesítése."""
    blocks = []
    if isinstance(query, dict):
        if "query_blocks" in query:
            return _blocks_from(query["query_blocks"])
        for concept, val in query.items():
            if isinstance(val, dict):
                blocks.append({"concept": concept, "terms": as_list(val.get("terms")), "mesh": as_list(val.get("mesh"))})
            else:
                blocks.append({"concept": concept, "terms": as_list(val), "mesh": []})
    else:
        for b in as_list(query):
            if isinstance(b, dict):
                blocks.append({"concept": b.get("concept"), "terms": as_list(b.get("terms")),
                               "mesh": as_list(b.get("mesh"))})
    return [b for b in blocks if b["terms"] or b["mesh"]]


def build_queries(query, since=None, until=None, concepts=("P", "I")):
    """Forrásonkénti lekérdezések az SR/MA-szűrővel. ``query``: szöveg (témakifejezés, PubMed-szintaxis is
    lehet) vagy fogalomblokkok. Visszaad: ``{forrás: {...}}`` a pontos lekérdezésekkel (PRISMA-S)."""
    since_d = _date_str(since, start=True)
    until_d = _date_str(until, start=False)
    if isinstance(query, str):
        topic_pm = query.strip()
        topic_plain = _strip_tags(topic_pm)
        oa_search = topic_plain
        scopus_topic = "TITLE-ABS-KEY(%s)" % topic_plain
        terms_for_relevance = [[t] for t in _tokens(topic_plain)]
    else:
        blocks = _blocks_from(query)
        chosen = [b for b in blocks if b["concept"] in concepts] or blocks
        if not chosen:
            raise ValueError("Üres lekérdezés: adj meg legalább egy fogalomblokkot (P, I) vagy keresőkifejezést.")
        pm_parts, ep_parts, oa_parts, sc_parts = [], [], [], []
        terms_for_relevance = []
        for b in chosen:
            qt = [x for x in (_quote(t) for t in b["terms"]) if x]
            pm = ["%s[tiab]" % t for t in qt] + ['"%s"[mh]' % str(m).replace('"', "") for m in b["mesh"]]
            # Europe PMC: mezőmegjelölés nélkül a TELJES SZÖVEGBEN is keres (élő próba, BCG: 892 találat a
            # cím/absztrakt szerinti 78 helyett, sok más témájú áttekintéssel) → TITLE_ABS (+ KW, MeSH)
            ep = ["TITLE_ABS:%s" % t for t in qt] + ["KW:%s" % t for t in qt] + \
                ['MESH:"%s"' % str(m).replace('"', "") for m in b["mesh"]]
            pm_parts.append("(%s)" % " OR ".join(pm))
            ep_parts.append("(%s)" % " OR ".join(ep))
            if qt:
                oa_parts.append("(%s)" % " OR ".join(qt))
                sc_parts.append("TITLE-ABS-KEY(%s)" % " OR ".join(qt))
            terms_for_relevance.append([str(t) for t in b["terms"]] + [str(m) for m in b["mesh"]])
        topic_pm = " AND ".join(pm_parts)
        topic_plain = " AND ".join(ep_parts)
        oa_search = " AND ".join(oa_parts)
        scopus_topic = " AND ".join(sc_parts)
    pubmed = ('(%s) AND (systematic[sb] OR meta-analysis[pt] OR "meta-analysis"[ti] OR "systematic review"[ti])'
              % topic_pm)
    epmc = ('(%s) AND (PUB_TYPE:"systematic-review" OR PUB_TYPE:"meta-analysis" OR TITLE:"meta-analysis" OR '
            'TITLE:"systematic review")' % topic_plain)
    if since_d or until_d:
        epmc += " AND FIRST_PDATE:[%s TO %s]" % (since_d or "1800-01-01", until_d or "3000-12-31")
    oa_filter = [("type", "review")]
    if since_d:
        oa_filter.append(("from_publication_date", since_d))
    if until_d:
        oa_filter.append(("to_publication_date", until_d))
    scopus = ('%s AND (TITLE-ABS-KEY("meta-analysis") OR TITLE-ABS-KEY("systematic review")) AND DOCTYPE(re)'
              % scopus_topic)
    if since_d:
        scopus += " AND PUBYEAR > %d" % (int(since_d[:4]) - 1)
    if until_d:
        scopus += " AND PUBYEAR < %d" % (int(until_d[:4]) + 1)
    return {
        "pubmed": {"term": pubmed, "datetype": "pdat" if (since_d or until_d) else None,
                   "mindate": since_d.replace("-", "/") if since_d else None,
                   "maxdate": until_d.replace("-", "/") if until_d else None},
        "europepmc": {"query": epmc},
        "openalex": {"search": oa_search, "filter": oa_filter},
        "scopus": {"query": scopus},
        "_relevance_terms": terms_for_relevance,
    }


def _date_str(value, start=True):
    if value in (None, ""):
        return None
    s = str(value).strip()
    if re.match(r"^\d{4}$", s):
        return s + ("-01-01" if start else "-12-31")
    if re.match(r"^\d{4}-\d{2}$", s):
        if start:
            return s + "-01"
        y, m = int(s[:4]), int(s[5:7])
        last = (datetime(y + (m // 12), m % 12 + 1, 1, tzinfo=timezone.utc).toordinal() -
                datetime(y, m, 1, tzinfo=timezone.utc).toordinal())
        return "%s-%02d" % (s, last)
    if re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}$", s):
        return s.replace("/", "-")
    raise ValueError("Érvénytelen dátum: %r (várt: ÉÉÉÉ, ÉÉÉÉ-HH vagy ÉÉÉÉ-HH-NN)." % value)


def _fold(text):
    """Kisbetűs, ékezet nélküli alak (``Calmette-Guérin`` → ``calmette-guerin``)."""
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def _tokens(text):
    return [w for w in re.findall(r"[a-z0-9][a-z0-9-]{2,}", _fold(text))
            if w not in _STOP and w not in ("and", "not")]


_SUFFIXES = ("ations", "ation", "ions", "ion", "ings", "ing", "ies", "ous", "us", "es", "ed", "al", "e", "s")


def _stem(word):
    """Könnyű angol szótő a relevancia-egyezéshez (``vaccine``/``vaccination`` → ``vaccin``; ``bacillus``/
    ``bacille`` → ``bacill``). Csak a rangsorhoz — a lekérdezést nem módosítja."""
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[:-len(suf)]
    return word


def _term_in(term, stems):
    toks = [_stem(w) for w in _tokens(term)]
    return bool(toks) and all(any(tt.startswith(w) for tt in stems) for w in toks)


def _group_hits(rel_terms, text):
    """Fogalomblokkonként: szerepel-e a blokk valamelyik kifejezése a szövegben (szótő-egyezéssel)."""
    stems = set(_stem(w) for w in _tokens(text))
    return [any(_term_in(t, stems) for t in group) for group in rel_terms or []]


# ---------------------------------------------------------------------------------------------
# Szövegminták: keresési dátum, k, jelzések (absztrakt/teljes szöveg, memóriában)
# ---------------------------------------------------------------------------------------------

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"])}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MON_RE = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_DATE_RES = (
    ("day", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MON_RE + r"),?\s+((?:19|20)\d{2})\b", re.I), "dmy"),
    ("day", re.compile(r"\b(" + _MON_RE + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})\b", re.I), "mdy"),
    ("day", re.compile(r"\b((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    ("month", re.compile(r"\b(" + _MON_RE + r"),?\s+((?:19|20)\d{2})\b", re.I), "my"),
    ("year", re.compile(r"\b((?:19|20)\d{2})\b"), "y"),
)
_SEARCH_CTX = re.compile(r"\bsearch(?:ed|es|ing)?\b|\bdate of (?:the )?(?:last )?search\b|\bup to date\b", re.I)
_SEARCH_WORD = re.compile(r"\bsearch(?:ed|es|ing)?\b", re.I)
#: megjelenési korlát („studies published from 2010 to 2020") — nem keresési dátum
_PUB_LIMIT = re.compile(r"\b(?:published|publication(?:s| date)?|dated)\b", re.I)
_DATE_LEAD = re.compile(r"(?:\b(?:to|until|till|through|thru|up to|on|in|as of|by)\s+|:\s*)$", re.I)


def _parse_dates(sentence):
    """A mondat dátumai ``(start, end, value, precision)`` alakban (átfedő, pontatlanabb találat nélkül)."""
    found = []
    taken = []
    for precision, rx, kind in _DATE_RES:
        for m in rx.finditer(sentence):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            try:
                if kind == "dmy":
                    d, mon, y = int(m.group(1)), _mon(m.group(2)), int(m.group(3))
                    val = "%04d-%02d-%02d" % (y, mon, d)
                elif kind == "mdy":
                    mon, d, y = _mon(m.group(1)), int(m.group(2)), int(m.group(3))
                    val = "%04d-%02d-%02d" % (y, mon, d)
                elif kind == "ymd":
                    y, mon, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    val = "%04d-%02d-%02d" % (y, mon, d)
                elif kind == "my":
                    mon, y = _mon(m.group(1)), int(m.group(2))
                    val = "%04d-%02d" % (y, mon)
                else:
                    val = "%04d" % int(m.group(1))
                if precision == "day":
                    datetime(int(val[:4]), int(val[5:7]), int(val[8:10]))
                elif precision == "month" and not 1 <= int(val[5:7]) <= 12:
                    continue
            except (KeyError, ValueError):
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), m.end(), val, precision))
    return sorted(found)


def _mon(text):
    t = text.lower().rstrip(".")
    if t in _MONTHS:
        return _MONTHS[t]
    return _MONTHS[t[:3]]


def _sentences(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", text)
    return [p.strip() for p in parts if p.strip()]


def _clip_quote(sentence, start=None, end=None, limit=QUOTE_MAX):
    """Szó szerinti idézet ≤ ``limit`` karakter. Hosszú mondatnál a ``[start, end)`` találat (pl. a dátum)
    biztosan benne marad; a levágott részeket „…" jelzi (az idézet többi része betű szerinti részlet)."""
    s = sentence
    if len(s) <= limit:
        return s
    if start is None:
        return s[:limit - 1] + "…"
    width = limit - 2  # két „…" helye
    left = max(0, min(start, end - width))
    right = min(len(s), left + width)
    if left == 0:
        right = min(len(s), limit - 1)
    elif right == len(s):
        left = max(0, len(s) - (limit - 1))
    return ("…" if left > 0 else "") + s[left:right] + ("…" if right < len(s) else "")


def _is_publication_limit(before):
    """A dátum előtti szövegrész szerint megjelenési korlát-e (az utolsó „search" szó UTÁN „published…" áll,
    pl. „searched for studies published from January 2021 to February 2026")."""
    last_search = None
    for m in _SEARCH_WORD.finditer(before):
        last_search = m.end()
    tail = before[last_search:] if last_search is not None else before
    return bool(_PUB_LIMIT.search(tail))


def extract_search_date(text, section="Abstract", container=None):
    """A keresési dátum kiolvasása (TERV 6.0/2). Csak „search" szót tartalmazó mondatban, és csak olyan dátumot
    fogad el, amelyet ``to/until/through/up to/on/in/as of`` vagy kettőspont előz meg (a „published between
    2000 and 2019" / „searched for studies published from 2010 to 2020" típusú megjelenési korlát NEM keresési
    dátum). A mondat legkésőbbi ilyen dátumát adja.

    Visszaad: ``{"value", "precision", "quote", "locator"}`` vagy ``None``."""
    best = None
    for sent in _sentences(text):
        if not _SEARCH_CTX.search(sent):
            continue
        for start, end, val, prec in _parse_dates(sent):
            lead = sent[max(0, start - 12):start]
            if not _DATE_LEAD.search(lead):
                continue
            if _is_publication_limit(sent[:start]):
                continue
            key = (val + "-99")[:10]
            if best is None or key > best[0]:
                best = (key, {"value": val, "precision": prec, "quote": _clip_quote(sent, start, end),
                              "locator": {"container": container, "section": section}})
    return best[1] if best else None


_NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
for _i, _t in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split()):
    _NUM_WORDS[_t] = 20 + 10 * _i
    for _j, _u in enumerate("one two three four five six seven eight nine".split()):
        _NUM_WORDS["%s-%s" % (_t, _u)] = 20 + 10 * _i + _j + 1
        _NUM_WORDS["%s %s" % (_t, _u)] = 20 + 10 * _i + _j + 1
_NUM = r"(\d{1,4}|" + "|".join(re.escape(w) for w in sorted(_NUM_WORDS, key=len, reverse=True)) + r")"
_UNIT = (r"(randomi[sz]ed controlled trials|randomi[sz]ed clinical trials|randomi[sz]ed trials|controlled trials|"
         r"clinical trials|RCTs|trials|cohort studies|observational studies|studies|articles|publications|reports|"
         r"papers)")
_K_RES = (
    re.compile(r"\b" + _NUM + r"\s+(?:[A-Za-z-]+\s+){0,3}?" + _UNIT +
               r"(?:\s*\([^)]{0,80}\))?\s*(?:,\s*)?(?:were|was|have been|had been|are|met)\s+(?:finally\s+)?"
               r"(?:included|eligible|identified as eligible|selected|the inclusion criteria)", re.I),
    re.compile(r"\b(?:we\s+)?includ(?:ed|ing)\s+(?:a total of\s+)?" + _NUM + r"\s+(?:[A-Za-z-]+\s+){0,3}?" + _UNIT,
               re.I),
    re.compile(r"\b" + _NUM + r"\s+(?:[A-Za-z-]+\s+){0,3}?" + _UNIT + r"\s+(?:\([^)]{0,80}\)\s+)?(?:involving|with|"
               r"including|enrolling|comprising|totall?ing)\s+[\d,. ]+\s+(?:participants|patients|subjects|"
               r"individuals|people|women|men|children|adults|infants)", re.I),
)


def _unit(word):
    w = word.lower()
    if "trial" in w or "rct" in w:
        return "trials"
    if w in ("reports", "publications", "papers", "articles"):
        return "reports" if w in ("reports", "publications") else "studies"
    return "studies"


def _num(text):
    t = re.sub(r"\s+", " ", text.lower())
    return int(t) if t.isdigit() else _NUM_WORDS.get(t)


def extract_k(text, section="Abstract", container=None):
    """A közölt bevont-vizsgálatszám (TERV 6.0/3). Visszaad: ``{"value", "unit", "quote", "locator",
    "also": [...]}`` vagy ``None``. Pl. „24 trials in 26 reports" → 24 (trials) és also: 26 (reports)."""
    for sent in _sentences(text):
        for rx in _K_RES:
            m = rx.search(sent)
            if not m:
                continue
            k = _num(m.group(1))
            if k is None or k <= 0 or k > 5000:
                continue
            res = {"value": k, "unit": _unit(m.group(2)), "quote": _clip_quote(sent, m.start(), m.end()),
                   "locator": {"container": container, "section": section}, "also": []}
            span = sent[m.start():m.end() + 60]
            m2 = re.search(r"(?:\bin|\bfrom|\breported in|\bdescribed in|\()\s*" + _NUM +
                           r"\s+(reports|publications|articles|papers)\b", span, re.I)
            if m2 and _num(m2.group(1)) and _num(m2.group(1)) != k:
                res["also"].append({"value": _num(m2.group(1)), "unit": "reports"})
            return res
    return None


_DATABASES = (
    ("medline", r"\bmedline\b|\bpubmed\b"), ("embase", r"\bembase\b|\bexcerpta medica\b"),
    ("central", r"\bcentral\b|cochrane (?:central|library)"), ("wos", r"web of (?:science|knowledge)"),
    ("scopus", r"\bscopus\b"), ("cinahl", r"\bcinahl\b"), ("psycinfo", r"\bpsyc ?info\b"), ("lilacs", r"\blilacs\b"),
    ("google_scholar", r"google scholar"), ("ctgov", r"clinicaltrials\.gov"), ("ictrp", r"\bictrp\b"),
    ("cnki", r"\bcnki\b"), ("wanfang", r"\bwanfang\b"), ("proquest", r"\bproquest\b"), ("amed", r"\bamed\b"),
)


def extract_signals(text, section="Abstract", container=None):
    """AMSTAR 2-höz kapcsolódó JELZÉSEK (nem értékelés): regisztrált protokoll, megnevezett adatbázisok száma,
    torzításikockázat-értékelés említése, PRISMA említése. A nem említett jelzés ``None`` (nem ``False``: az
    absztraktban való hiány nem bizonyítja a hiányt). Visszaad: ``(signals, quotes)``."""
    t = text or ""
    sig = {"protocol_registered": None, "registration_id": None, "databases_searched_n": None,
           "rob_assessed": None, "prisma_mentioned": None}
    quotes = []

    def quote_for(rx, name):
        for sent in _sentences(t):
            m = re.search(rx, sent, re.I)
            if m:
                quotes.append({"about": "signal:" + name, "quote": _clip_quote(sent, m.start(), m.end()),
                               "locator": {"container": container, "section": section}})
                return m
        return None

    m = quote_for(r"\bCRD\s?\d{11}\b", "protocol_registered")
    if m:
        sig["protocol_registered"] = True
        sig["registration_id"] = re.sub(r"\s", "", m.group(0)).upper()
    elif quote_for(r"\bPROSPERO\b|\bosf\.io\b|\bOpen Science Framework\b|\bINPLASY\d*\b", "protocol_registered"):
        sig["protocol_registered"] = True
    names = set()
    for key, rx in _DATABASES:
        if re.search(rx, t, re.I):
            names.add(key)
    if names:
        sig["databases_searched_n"] = len(names)
    if quote_for(r"risk of bias|\bRoB ?2?\b|ROBINS|Newcastle[- ]Ottawa|\bJadad\b|quality assessment|"
                 r"methodological quality", "rob_assessed"):
        sig["rob_assessed"] = True
    if quote_for(r"\bPRISMA\b", "prisma_mentioned"):
        sig["prisma_mentioned"] = True
    return sig, quotes


# ---------------------------------------------------------------------------------------------
# Azonosítók és összevonás
# ---------------------------------------------------------------------------------------------

def review_id_for(ids, fallback=None):
    """``rv-pmid-…`` → ``rv-doi-<sha1[:10]>`` → ``rv-eid-<számjegyek>`` → ``rv-oa-w…`` (TERV 4.2) →
    ``rv-pmc-…``; ha egyik sincs (pl. azonosító nélküli Europe PMC-rekord), ``rv-x-<sha1(fallback)[:10]>``."""
    def v(k):
        x = ids.get(k)
        return x.get("value") if isinstance(x, dict) else x
    if v("pmid"):
        return "rv-pmid-%s" % v("pmid")
    if v("doi"):
        return "rv-doi-%s" % hashlib.sha1(v("doi").encode("utf-8")).hexdigest()[:10]
    if v("eid"):
        return "rv-eid-%s" % re.sub(r"\D", "", v("eid")[7:] if v("eid").startswith("2-s2.0-") else v("eid"))
    if v("openalex"):
        return "rv-oa-%s" % v("openalex").lower()
    if v("pmcid"):
        return "rv-pmc-%s" % v("pmcid").lower()[3:]
    if fallback:
        return "rv-x-%s" % hashlib.sha1(str(fallback).encode("utf-8")).hexdigest()[:10]
    return None


class _UF(object):
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _is_cochrane(journal, doi):
    j = (journal or "").lower()
    return "cochrane database" in j or (doi or "").startswith("10.1002/14651858")


def _cochrane_info(doi):
    d = doi or ""
    m = re.search(r"(?i)\b(cd\d{6})(?:\.pub(\d+))?", d)
    if not m:
        return {"cd_number": None, "version": None}
    return {"cd_number": m.group(1).upper(), "version": int(m.group(2)) if m.group(2) else 1}


def _is_meta(title, pub_types):
    pts = " ".join(pub_types or []).lower()
    return "meta-analysis" in pts or bool(re.search(r"meta[- ]?analys[ie]s|metaanalys", title or "", re.I))


def _is_systematic(title, pub_types):
    pts = " ".join(pub_types or []).lower()
    return ("systematic review" in pts or "systematic-review" in pts
            or bool(re.search(r"systematic(?:al)?\s+(?:literature\s+)?review|umbrella review|overview of (?:systematic )?reviews",
                              title or "", re.I)))


# ---------------------------------------------------------------------------------------------
# Fő függvény
# ---------------------------------------------------------------------------------------------

def find_reviews(query=None, filters=None, sources=None, http=None, state=None, env=None, progress=None,
                 clients=None):
    """Szisztematikus áttekintések/metaanalízisek keresése és rangsorolása (L1).

    ``query``: témakifejezés (szöveg; PubMed-szintaxis is lehet) vagy fogalomblokkok
    (``[{"concept": "P", "terms": [...], "mesh": [...]}, …]`` vagy ``{"P": [...], "I": [...]}``); ha ``None``,
    a ``state["pico"]["query_blocks"]``.

    ``filters`` (mind opcionális): ``since``/``until`` (``ÉÉÉÉ`` | ``ÉÉÉÉ-HH`` | ``ÉÉÉÉ-HH-NN``; megjelenési
    dátum), ``max_per_source`` (alap 200), ``query_by_source`` (forrásonként szó szerint futtatandó lekérdezés,
    pl. ``--query-file``), ``enrich`` (alap ``True``: hiányzó metaadat/nyílt-szöveg információ pótlása
    kötegelt lekérdezéssel), ``fulltext_dates`` (alap ``False``: a nyílt teljes szövegből is keres keresési
    dátumot — memóriában), ``fulltext_limit`` (alap 20), ``weights`` (rangsor-súlyok), ``today``
    (``ÉÉÉÉ-HH-NN``, tesztekhez), ``concepts`` (alap ``("P", "I")``).

    ``sources``: forráslista vagy ``"a,b"`` (alap: a projekt bekapcsolt forrásai a ``state``-ből; a Scopus csak
    kulccsal). ``clients``: ``{forrás: Client}`` (tesztekhez).

    Visszaad: ``{"query", "filters", "generated", "sources", "searches", "candidates", "warnings",
    "exit_code", "stats"}``. A ``candidates`` rangsorolt (pontszám szerint csökkenő; a visszavont a végén)."""
    from . import sources as srcreg  # lusta import (körkörösség elkerülése)

    filters = dict(filters or {})
    if http is None:
        http = net.HttpClient(env=env)
    env = env if env is not None else http.env
    if query is None:
        query = ((state or {}).get("pico") or {}).get("query_blocks")
    if query in (None, "", []):
        raise ValueError("Nincs lekérdezés: add meg a témát (szöveg) vagy a PICO fogalomblokkokat.")
    max_n = int(filters.get("max_per_source") or filters.get("max") or 200)
    queries = build_queries(query, filters.get("since"), filters.get("until"),
                            concepts=tuple(filters.get("concepts") or ("P", "I")))
    overrides = filters.get("query_by_source") or {}
    sel = srcreg.select_sources(state=state, override=sources, env=env)
    use = [s for s in sel["use"] if s in DISCOVERY_SOURCES]
    now = http.now()
    run_at = net.utc_ts(now)
    stamp = net.compact_ts(now)

    def emit(phase, source=None, done=None, total=None, hu="", en=""):
        if progress:
            try:
                progress({"ts": net.utc_ts(http.now()), "step": "find_reviews", "phase": phase, "source": source,
                          "done": done, "total": total, "message": {"hu": hu, "en": en}})
            except Exception:  # pragma: no cover
                pass

    result_sources = {}
    warnings = []
    searches = []
    hits = []  # (source, normalized record, abstract, retrieval, search_id)

    for sk in sel["skipped"]:
        if sk["source"] in DISCOVERY_SOURCES:
            result_sources[sk["source"]] = {"status": sk["status"], "searched": False, "message": sk["message"]}
            if sk["status"] != "not_configured" or sources is not None:
                warnings.append({"code": "H014", "source": sk["source"], "hu": sk["message"]["hu"],
                                 "en": sk["message"]["en"]})

    def client(name):
        if clients and name in clients:
            return clients[name]
        return srcreg.make_client(name, http=http, env=env)

    for i, s in enumerate(use):
        emit("search", s, i, len(use), "Keresés: %s" % net.source_name(s), "Searching: %s" % net.source_name(s))
        sid = "s-%s-%s" % (s, stamp)
        n = 2
        while any(x["search_id"] == sid for x in searches):
            sid = "s-%s-%s-%d" % (s, stamp, n)
            n += 1
        try:
            if s == "pubmed":
                info = _search_pubmed(client(s), queries["pubmed"], overrides.get("pubmed"), max_n)
            elif s == "europepmc":
                info = _search_epmc(client(s), queries["europepmc"], overrides.get("europepmc"), max_n)
            elif s == "openalex":
                info = _search_openalex(client(s), queries["openalex"], overrides.get("openalex"), max_n)
            else:
                info = _search_scopus(client(s), queries["scopus"], overrides.get("scopus"), max_n)
        except SourceUnavailable as exc:
            result_sources[s] = {"status": exc.status, "searched": False, "message": exc.explain,
                                 "reset_at": exc.reset_at}
            warnings.append({"code": "H014", "source": s, "hu": exc.explain["hu"], "en": exc.explain["en"]})
            continue
        except (HttpError, ParseError) + net.SHAPE_ERRORS as exc:
            detail = net.redact(str(exc), env)[:240]
            if not isinstance(exc, (HttpError, ParseError)):
                detail = "váratlan válaszszerkezet (%s)" % type(exc).__name__
            msg = {"hu": "%s: a keresés hibát adott — %s" % (net.source_name(s), detail),
                   "en": "%s: the search failed — %s" % (net.source_name(s), detail)}
            result_sources[s] = {"status": "unreachable", "searched": False, "message": msg}
            warnings.append({"code": "H014", "source": s, "hu": msg["hu"], "en": msg["en"]})
            continue
        entry = {"search_id": sid, "purpose": "review_discovery", "source": s,
                 "platform": srcreg.SOURCE_INFO[s]["platform"], "query": info["query"], "filters": info["filters"],
                 "date_field": info["date_field"], "date_from": _date_or_none(filters.get("since")),
                 "date_to": _date_or_none(filters.get("until")), "run_at": run_at,
                 "count_total": info["count_total"], "count_retrieved": info["count_retrieved"],
                 "complete": bool(info["complete"]), "export": None, "note": info.get("note")}
        searches.append(entry)
        status = "ok"
        if info.get("error"):
            status = info["error"].get("status") or "unreachable"
            warnings.append({"code": "H014", "source": s, "hu": info["error"]["message"]["hu"],
                             "en": info["error"]["message"]["en"]})
        if not info["complete"] and info["count_total"] and info["count_retrieved"] < info["count_total"]:
            warnings.append({"code": "H012", "source": s,
                             "hu": "%s: %d találatból %d letöltve (felső korlát: %d vagy megszakadt lapozás) — szűkíts "
                                   "vagy emeld a korlátot (--max)." % (net.source_name(s), info["count_total"],
                                                                       info["count_retrieved"], max_n),
                             "en": "%s: %d of %d hits retrieved (cap %d or interrupted paging) — narrow the query or "
                                   "raise the cap (--max)." % (net.source_name(s), info["count_retrieved"],
                                                               info["count_total"], max_n)})
        result_sources[s] = {"status": status, "searched": True, "search_id": sid,
                             "count_total": info["count_total"], "count_retrieved": info["count_retrieved"],
                             "complete": bool(info["complete"])}
        for rec, abstract, retrieval in info["records"]:
            hits.append((s, rec, abstract, retrieval, sid))

    emit("merge", None, None, None, "Találatok összevonása", "Merging hits")
    clusters = _merge(hits)
    if filters.get("enrich", True):
        emit("enrich", None, None, None, "Hiányzó adatok pótlása", "Filling in missing data")
        _enrich(clusters, client, use, env, warnings)
    candidates = []
    today = filters.get("today") or net.utc_ts(http.now())[:10]
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(filters.get("weights") or {})
    for cl in clusters:
        candidates.append(_candidate(cl, queries["_relevance_terms"], today, weights, http))
    if filters.get("fulltext_dates"):
        _fulltext_dates(candidates, client, int(filters.get("fulltext_limit") or 20), use, warnings)
        for c in candidates:
            _rank(c, queries["_relevance_terms"], today, weights)
    _mark_superseded(candidates)
    candidates.sort(key=lambda c: (c["flags"]["retracted"], c["status"] == "superseded", -(c["rank"]["score"] or 0),
                                   -(c["bib"].get("year") or 0), c["review_id"]))
    exit_code = 3 if any(w["code"] == "H014" for w in warnings) else 0
    emit("done", None, len(candidates), len(candidates), "Kész: %d áttekintés-jelölt" % len(candidates),
         "Done: %d review candidates" % len(candidates))
    return {
        "kind": "review_candidates", "model": MODEL, "generated": net.utc_ts(http.now()),
        "query": {"input": query if isinstance(query, str) else _blocks_from(query),
                  "by_source": dict((k, v) for k, v in queries.items() if not k.startswith("_"))},
        "filters": dict((k, v) for k, v in filters.items() if k not in ("weights",)),
        "sources": result_sources, "searches": searches, "candidates": candidates, "warnings": warnings,
        "exit_code": exit_code, "stats": dict(http.stats),
    }


def _date_or_none(v):
    if v in (None, ""):
        return None
    s = str(v).strip().replace("/", "-")
    return s if re.match(r"^\d{4}(-\d{2}(-\d{2})?)?$", s) else None


# -- forrásonkénti keresés --------------------------------------------------------------------

def _search_pubmed(cl, q, override, max_n):
    term = override or q["term"]
    kw = {}
    if not override and q.get("datetype"):
        kw = {"datetype": q["datetype"], "mindate": q.get("mindate"), "maxdate": q.get("maxdate")}
    res = cl.esearch_all(term, cap=max_n, **kw)
    records = []
    if res["ids"]:
        summ = dict((r["pmid"], r) for r in cl.esummary(res["ids"]))
        fetched = {}
        try:
            for r in cl.efetch_records(res["ids"]):
                fetched[r["pmid"]] = r
        except (SourceUnavailable, HttpError, ParseError) + net.SHAPE_ERRORS:
            fetched = {}
        for pmid in res["ids"]:
            s = summ.get(pmid)
            if not s:
                continue
            f = fetched.get(pmid, {})
            rec = dict(s)
            rec.pop("raw", None)
            retrieval = rec.pop("retrieval", None)
            rec["comments_corrections"] = f.get("comments_corrections", [])
            if f.get("retracted"):
                rec["retracted"] = True
            if f.get("pub_types"):
                rec["pub_types"] = sorted(set(rec["pub_types"]) | set(f["pub_types"]))
            rec["_via"] = {"pmid": "pubmed.esearch", "doi": "pubmed.esummary", "pmcid": "pubmed.esummary"}
            records.append((rec, f.get("abstract") or "", retrieval))
    note = None
    if res.get("warnings"):
        note = "; ".join(res["warnings"])[:300]
    filters = None
    if kw:
        filters = {"datetype": kw["datetype"], "mindate": kw.get("mindate"), "maxdate": kw.get("maxdate")}
    return {"query": term, "filters": filters, "date_field": kw.get("datetype"), "count_total": res["count"],
            "count_retrieved": res["retrieved"], "complete": res["complete"], "records": records, "note": note,
            "error": _err_dict(res.get("error"))}


def _err_dict(err):
    if not err:
        return None
    return {"status": err.get("status"), "message": err.get("message")}


def _search_epmc(cl, q, override, max_n):
    from .europepmc import record as epmc_record
    query = override or q["query"]
    pager = cl.search(query, result_type="core", page_size=min(max_n, 100), max_results=max_n)
    records = []
    for raw in pager:
        rec = epmc_record(raw)
        abstract = rec.pop("abstract", "")
        rec["_via"] = {"pmid": "europepmc.search", "doi": "europepmc.search", "pmcid": "europepmc.search"}
        rec["_epmc"] = True
        records.append((rec, abstract, None))
    return {"query": query, "filters": {"resultType": "core"}, "date_field": "FIRST_PDATE" if "FIRST_PDATE" in query else None,
            "count_total": pager.total, "count_retrieved": pager.retrieved, "complete": bool(pager.complete),
            "records": records, "error": _pager_err(pager)}


def _pager_err(pager):
    if pager.error is None:
        return None
    return {"status": pager.error.status, "message": pager.error.explain}


def _search_openalex(cl, q, override, max_n):
    from .openalex import work_record, filter_string
    search = override or q["search"]
    flt = q["filter"]
    pager = cl.works(filter=flt, search=search, max_results=max_n)
    records = []
    for raw in pager:
        rec = work_record(raw)
        rec["pub_types"] = [rec.get("type")] if rec.get("type") else []
        rec["retracted"] = rec.get("is_retracted", False)
        rec["_via"] = {"pmid": "openalex.works", "doi": "openalex.works", "pmcid": "openalex.works",
                       "openalex": "openalex.works"}
        records.append((rec, "", None))
    return {"query": "search=%s" % search, "filters": {"filter": filter_string(flt)},
            "date_field": "from_publication_date" if any(k == "from_publication_date" for k, _v in flt) else None,
            "count_total": pager.total, "count_retrieved": pager.retrieved, "complete": bool(pager.complete),
            "records": records, "error": _pager_err(pager)}


def _search_scopus(cl, q, override, max_n):
    query = override or q["query"]
    pager = cl.search(query, max_results=max_n, normalize=True)
    records = []
    for rec in pager:
        rec = dict(rec)
        rec["pub_types"] = [x for x in (rec.get("doctype_desc"),) if x]
        rec["_via"] = {"eid": "scopus.search", "doi": "scopus.search", "pmid": "scopus.search"}
        records.append((rec, "", None))
    return {"query": query, "filters": {"field": "restricted (bibliographic)"},
            "date_field": "PUBYEAR" if "PUBYEAR" in query else None, "count_total": pager.total,
            "count_retrieved": pager.retrieved, "complete": bool(pager.complete), "records": records,
            "error": _pager_err(pager), "note": "Scopus: élőben nem igazolt kliens (unverified-live)"}


# -- összevonás és jelölt-építés ------------------------------------------------------------

def _rec_keys(rec):
    keys = []
    for k, fn in (("pmid", norm_pmid), ("doi", norm_doi), ("pmcid", norm_pmcid), ("eid", norm_eid),
                  ("openalex", norm_openalex)):
        v = fn(rec.get(k))
        if v:
            keys.append((k, v))
    return keys


def _merge(hits):
    uf = _UF()
    nodes = []
    for idx, h in enumerate(hits):
        node = ("hit", idx)
        uf.find(node)
        for key in _rec_keys(h[1]):
            uf.union(node, key)
        nodes.append(node)
    groups = {}
    for idx, node in enumerate(nodes):
        groups.setdefault(uf.find(node), []).append(hits[idx])
    return [groups[k] for k in sorted(groups, key=lambda r: str(r))]


def _pick(cluster, field, order=_BIB_PRIORITY):
    for src in order:
        for s, rec, _a, _r, _sid in cluster:
            if s == src and rec.get(field) not in (None, "", []):
                return rec.get(field)
    return None


def _enrich(clusters, client, use, env, warnings):
    """Kötegelt pótlás: PubMed-metaadat és absztrakt a csak más forrásban talált PMID-ekhez; Europe PMC
    nyílt-szöveg információ a PubMed-találatokhoz. Elérhetetlenségnél csendben kihagy (a keresés H014-e
    már jelzett)."""
    need_pm = []
    need_epmc = []
    for cl in clusters:
        srcs = set(h[0] for h in cl)
        pmid = None
        for h in cl:
            pmid = pmid or norm_pmid(h[1].get("pmid"))
        if pmid and "pubmed" not in srcs:
            need_pm.append(pmid)
        if pmid and not any(h[1].get("_epmc") for h in cl):
            need_epmc.append(pmid)
    by_pmid = {}
    for i, cl in enumerate(clusters):
        for h in cl:
            p = norm_pmid(h[1].get("pmid"))
            if p:
                by_pmid.setdefault(p, i)
    if need_pm and "pubmed" in use:
        try:
            pm = client("pubmed")
            summ = dict((r["pmid"], r) for r in pm.esummary(need_pm))
            fetched = dict((r["pmid"], r) for r in pm.efetch_records(need_pm))
            for p in need_pm:
                s = summ.get(p)
                if not s:
                    continue
                f = fetched.get(p, {})
                rec = dict(s)
                rec.pop("raw", None)
                retrieval = rec.pop("retrieval", None)
                rec["comments_corrections"] = f.get("comments_corrections", [])
                rec["retracted"] = bool(rec.get("retracted") or f.get("retracted"))
                rec["_via"] = {"pmid": "pubmed.esummary", "doi": "pubmed.esummary", "pmcid": "pubmed.esummary"}
                rec["_enriched"] = True
                clusters[by_pmid[p]].append(("pubmed", rec, f.get("abstract") or "", retrieval, None))
        except (SourceUnavailable, HttpError, ParseError) + net.SHAPE_ERRORS:
            pass
    if need_epmc and "europepmc" in use:
        try:
            from .europepmc import record as epmc_record
            got = client("europepmc").lookup_many_pmids(need_epmc, result_type="core", chunk=25)
            for p, raw in got.items():
                rec = epmc_record(raw)
                abstract = rec.pop("abstract", "")
                rec["_via"] = {"pmid": "europepmc.search", "doi": "europepmc.search", "pmcid": "europepmc.search"}
                rec["_epmc"] = True
                rec["_enriched"] = True
                clusters[by_pmid[p]].append(("europepmc", rec, abstract, None, None))
        except (SourceUnavailable, HttpError, ParseError) + net.SHAPE_ERRORS:
            pass


def _candidate(cluster, rel_terms, today, weights, http):
    at = net.utc_ts(http.now())
    ids = {}
    conflicts = []
    for k in ("pmid", "pmcid", "doi", "eid", "openalex"):
        fn = {"pmid": norm_pmid, "pmcid": norm_pmcid, "doi": norm_doi, "eid": norm_eid, "openalex": norm_openalex}[k]
        values = {}
        for s, rec, _a, _r, _sid in cluster:
            v = fn(rec.get(k))
            if v:
                values.setdefault(v, []).append((s, (rec.get("_via") or {}).get(k) or "%s.search" % s))
        if len(values) > 1:
            conflicts.append({"id_type": k, "values": sorted(values)})
        if values:
            chosen = None
            for src in _ID_PRIORITY.get(k, _BIB_PRIORITY):
                for v, origins in sorted(values.items()):
                    for (s, via) in origins:
                        if s == src:
                            chosen = (v, s, via)
                            break
                    if chosen:
                        break
                if chosen:
                    break
            if not chosen:
                v, origins = sorted(values.items())[0]
                chosen = (v, origins[0][0], origins[0][1])
            ids[k] = {"value": chosen[0], "source": chosen[1], "via": chosen[2], "at": at}
    authors = _pick(cluster, "authors") or []
    pub_types = sorted(set(pt for _s, rec, _a, _r, _sid in cluster for pt in (rec.get("pub_types") or []) if pt))
    title = _pick(cluster, "title")
    journal = _pick(cluster, "journal")
    year = _pick(cluster, "year")
    bib = {"title": title, "authors": list(authors)[:50], "authors_truncated": bool(_pick(cluster, "authors_truncated")),
           "first_author": _pick(cluster, "first_author"), "year": to_int(year), "journal": journal,
           "volume": _pick(cluster, "volume"), "issue": _pick(cluster, "issue"), "pages": _pick(cluster, "pages"),
           "pub_types": pub_types, "language": _pick(cluster, "language")}
    doi = ids.get("doi", {}).get("value")
    is_cochrane = _is_cochrane(journal, doi)
    found_by = sorted(set(sid for _s, _r, _a, _ret, sid in cluster if sid))
    found_in = sorted(set(s for s, _r, _a, _ret, sid in cluster if sid))
    retrievals = [ret for _s, _r, _a, ret, _sid in cluster if ret]
    # nyílt teljes szöveg (Europe PMC adat alapján)
    epmc = [rec for s, rec, _a, _r, _sid in cluster if s == "europepmc"]
    oa = any(r.get("is_open_access") for r in epmc)
    license_ = next((r.get("license") for r in epmc if r.get("license")), None)
    pmcid = ids.get("pmcid", {}).get("value")
    if oa and pmcid:
        fulltext = {"available": True, "route": "europepmc_oa", "license": license_, "checked_at": at}
    elif pmcid:
        fulltext = {"available": True, "route": "pmc_efetch", "license": license_, "checked_at": at}
    else:
        fulltext = {"available": False, "route": "none", "license": None, "checked_at": at if epmc else None}
    retracted = any(rec.get("retracted") or rec.get("is_retracted") or "Retracted Publication" in (rec.get("pub_types") or [])
                    for _s, rec, _a, _r, _sid in cluster)
    updates = []
    for _s, rec, _a, _r, _sid in cluster:
        for cc in rec.get("comments_corrections") or []:
            if cc.get("type") in ("UpdateOf", "UpdateIn", "RetractionIn", "ErratumIn") and \
                    {"type": cc.get("type"), "pmid": cc.get("pmid")} not in updates:
                updates.append({"type": cc.get("type"), "pmid": cc.get("pmid")})
    # szövegminták (absztrakt, memóriában) — legjobb forrás: PubMed, aztán Europe PMC
    abstract = ""
    abs_container = None
    for src in ("pubmed", "europepmc"):
        for s, rec, a, _r, _sid in cluster:
            if s == src and a and not abstract:
                abstract = a
                abs_container = "pubmed:%s" % norm_pmid(rec.get("pmid")) if norm_pmid(rec.get("pmid")) else src
    preview = []
    sd = extract_search_date(abstract, "Abstract", abs_container) if abstract else None
    k = extract_k(abstract, "Abstract", abs_container) if abstract else None
    sig, sig_quotes = extract_signals(abstract, "Abstract", abs_container) if abstract else (
        {"protocol_registered": None, "registration_id": None, "databases_searched_n": None, "rob_assessed": None,
         "prisma_mentioned": None}, [])
    search_date = None
    if sd:
        search_date = {"value": sd["value"], "precision": sd["precision"], "fallback": False, "evidence_id": None}
        preview.append({"about": "search_date", "quote": sd["quote"], "locator": sd["locator"]})
    else:
        search_date = _fallback_date(cluster, bib)
    k_reported = None
    if k:
        k_reported = {"value": k["value"], "unit": k["unit"], "evidence_id": None}
        if k.get("also"):
            k_reported["also"] = k["also"]
        preview.append({"about": "k_reported", "quote": k["quote"], "locator": k["locator"]})
    preview.extend(sig_quotes)
    # hibajegyzék/helyesbítés (élő próba: „Corrigendum: A Meta-Analysis of …" önálló jelöltként jelent meg)
    erratum = "Published Erratum" in pub_types or bool(re.match(r"^\s*(corrigendum|erratum|correction|"
                                                                r"retraction note|expression of concern)\b",
                                                                title or "", re.I))
    is_meta = _is_meta(title, pub_types)
    systematic = _is_systematic(title, pub_types) or is_meta or is_cochrane
    signals = {"meta_analysis": is_meta, "protocol_registered": sig["protocol_registered"],
               "registration_id": sig["registration_id"], "databases_searched_n": sig["databases_searched_n"],
               "rob_assessed": sig["rob_assessed"], "prisma_mentioned": sig["prisma_mentioned"],
               "retracted": retracted, "open_fulltext": fulltext["available"] and fulltext["route"] == "europepmc_oa"}
    fb = None
    if not ids:
        ext = next(("%s:%s" % (rec.get("source_db"), rec.get("id")) for _s, rec, _a, _r, _sid in cluster
                    if rec.get("source_db") and rec.get("id")), None)
        fb = ext or "%s|%s|%s" % ((title or "").lower(), year, (bib.get("first_author") or "").lower())
    cand = {
        "review_id": review_id_for(ids, fallback=fb),
        "status": "candidate",
        "superseded_by": None,
        "ids": ids,
        "bib": bib,
        "is_cochrane": is_cochrane,
        "cochrane": _cochrane_info(doi) if is_cochrane else {"cd_number": None, "version": None},
        "found_by": found_by,
        "found_in": found_in,
        "retrievals": retrievals,
        "signals": signals,
        "search_date": search_date,
        "k_reported": k_reported,
        "fulltext": fulltext,
        "flags": {"retracted": retracted, "narrative_suspect": not systematic and not erratum,
                  "erratum": erratum, "id_conflicts": conflicts,
                  "updates": updates, "enriched_from": sorted(set(s for s, rec, _a, _r, _sid in cluster
                                                                  if rec.get("_enriched"))),
                  "unverified_live": any(s == "scopus" for s in found_in)},
        "proposal": None,
        "evidence_preview": preview,
    }
    if erratum and not retracted:
        cand["proposal"] = {"action": "exclude", "reason": {
            "hu": "Hibajegyzék / helyesbítés (erratum, corrigendum), nem önálló áttekintés — kizárás javasolt; "
                  "az eredeti cikket válaszd ki (ha a listában van).",
            "en": "Erratum/corrigendum notice, not a review in itself — exclusion proposed; select the original "
                  "article instead."}}
    elif retracted:
        cand["proposal"] = {"action": "exclude", "reason": {
            "hu": "Visszavont közlemény (Retracted Publication / RetractionIn) — kizárás javasolt.",
            "en": "Retracted publication — exclusion proposed."}}
    elif not systematic:
        cand["proposal"] = {"action": "check", "reason": {
            "hu": "A cím és a publikációtípus alapján nem biztos, hogy szisztematikus áttekintés vagy metaanalízis "
                  "(narratív áttekintés lehet) — ellenőrizd.",
            "en": "Title and publication type do not clearly indicate a systematic review or meta-analysis "
                  "(may be a narrative review) — please check."}}
    # csak blokkonkénti igen/nem kerül a jelöltbe (az absztrakt maga nem — N4); a to_review_doc nem menti
    cand["_abstract_hits"] = _group_hits(rel_terms, abstract) if abstract else []
    _rank(cand, rel_terms, today, weights)
    return cand


def _fallback_date(cluster, bib):
    """Tartalék keresési dátum: a megjelenés − 12 hónap (H008; emberi jóváhagyás kell, TERV 6.0/2)."""
    pubdate = _pick(cluster, "pubdate") or _pick(cluster, "date") or _pick(cluster, "first_publication_date") \
        or _pick(cluster, "cover_date")
    y = m = None
    if pubdate:
        mm = re.match(r"^(\d{4})[-/ ](\d{2}|[A-Za-z]{3})", str(pubdate))
        if mm:
            y = int(mm.group(1))
            mon = mm.group(2)
            m = int(mon) if mon.isdigit() else _MONTHS.get(mon.lower())
    if y is None:
        y = bib.get("year")
    if not y:
        return {"value": None, "precision": "unknown", "fallback": True, "evidence_id": None}
    if m:
        y2, m2 = (y - 1, m)
        return {"value": "%04d-%02d" % (y2, m2), "precision": "month", "fallback": True, "evidence_id": None}
    return {"value": "%04d" % (y - 1), "precision": "year", "fallback": True, "evidence_id": None}


def _rank(cand, rel_terms, today, weights):
    if rel_terms:
        in_title = _group_hits(rel_terms, cand["bib"].get("title") or "")
        in_abs = cand.get("_abstract_hits") or [False] * len(rel_terms)
        hits = sum(1.0 if t else (0.5 if (i < len(in_abs) and in_abs[i]) else 0.0) for i, t in enumerate(in_title))
        relevance = hits / float(len(rel_terms))
    else:
        relevance = None
    try:
        this_year = int(str(today)[:4])
    except ValueError:
        this_year = datetime.now(timezone.utc).year
    sd = cand.get("search_date") or {}
    y = None
    if sd.get("value") and not sd.get("fallback"):
        y = int(sd["value"][:4])
    elif cand["bib"].get("year"):
        y = int(cand["bib"]["year"])
    recency = None if y is None else max(0.0, min(1.0, 1.0 - (this_year - y) / 15.0))
    k = (cand.get("k_reported") or {}).get("value")
    size = None if not k else min(1.0, math.log(1 + k) / math.log(51))
    sig = cand["signals"]
    sig_n = sum(1 for key in ("protocol_registered", "rob_assessed", "prisma_mentioned") if sig.get(key)) + (
        1 if (sig.get("databases_searched_n") or 0) >= 2 else 0)
    comps = {
        "relevance": None if relevance is None else round(relevance, 4),
        "recency": None if recency is None else round(recency, 4),
        "size": None if size is None else round(size, 4),
        "systematic": 0.0 if cand["flags"]["narrative_suspect"] else 1.0,
        "meta_analysis": 1.0 if sig.get("meta_analysis") else 0.0,
        "cochrane": 1.0 if cand["is_cochrane"] else 0.0,
        "open_fulltext": 1.0 if cand["fulltext"]["available"] else 0.0,
        "signals": round(sig_n / 4.0, 4),
    }
    score = sum(weights.get(k2, 0.0) * (v or 0.0) for k2, v in comps.items())
    if cand["flags"]["retracted"] or cand["flags"].get("erratum"):
        score = 0.0
    cand["rank"] = {"score": round(score, 4), "components": comps}


def _mark_superseded(cands):
    """Cochrane-változatok: azonos CD-számnál a legfrissebb változat a jelölt, a régebbiek ``superseded``."""
    by_cd = {}
    for c in cands:
        cd = (c.get("cochrane") or {}).get("cd_number")
        if c["is_cochrane"] and cd:
            by_cd.setdefault(cd, []).append(c)
    for cd, group in by_cd.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda c: ((c["cochrane"].get("version") or 0), c["bib"].get("year") or 0), reverse=True)
        latest = group[0]
        for old in group[1:]:
            old["status"] = "superseded"
            old["superseded_by"] = latest["review_id"]


def _fulltext_dates(cands, client, limit, use, warnings):
    """Keresési dátum a nyílt teljes szövegből (Europe PMC JATS, memóriában; legfeljebb ``limit`` darab)."""
    if "europepmc" not in use:
        return
    todo = [c for c in cands if c["fulltext"]["route"] == "europepmc_oa" and (c["search_date"] or {}).get("fallback")]
    if not todo:
        return
    cl = client("europepmc")
    for c in todo[:limit]:
        pmcid = c["ids"].get("pmcid", {}).get("value")
        try:
            xml = cl.fulltext_xml(pmcid)
        except (SourceUnavailable, HttpError):
            break
        if not xml:
            continue
        try:
            root = net.parse_xml(xml)
        except ParseError:
            continue
        sd = None
        for sec in net.iter_local(root, "sec"):
            title = net.xml_text(sec.find("title")) if sec.find("title") is not None else ""
            if not re.search(r"method|search|data source|information source|eligib", title, re.I):
                continue
            paras = " ".join(net.xml_text(p) for p in net.iter_local(sec, "p"))
            sd = extract_search_date(paras, section=title or "Methods", container=pmcid)
            if sd:
                break
        if sd:
            c["search_date"] = {"value": sd["value"], "precision": sd["precision"], "fallback": False,
                                "evidence_id": None}
            c["evidence_preview"].append({"about": "search_date", "quote": sd["quote"], "locator": sd["locator"]})
        del xml, root


# ---------------------------------------------------------------------------------------------
# Átalakítás review.v1 dokumentummá (a discover/CLI menti)
# ---------------------------------------------------------------------------------------------

def to_review_doc(cand, extracted_by="tool:headhunter"):
    """Egy jelöltből sémahelyes ``szk.ma.headhunter.review/v1`` dokumentum (``candidates: []``; az idézetekből
    ``evidence[]`` bizonyíték-objektumok lesznek, a ``search_date``/``k_reported``/``signals`` hivatkozik rájuk)."""
    rid = cand["review_id"]
    at = next((v["at"] for v in cand["ids"].values() if isinstance(v, dict) and v.get("at")), net.utc_ts())
    evidence = []
    sig_ev = []
    search_date = dict(cand["search_date"]) if cand.get("search_date") else None
    k_rep = dict(cand["k_reported"]) if cand.get("k_reported") else None
    if k_rep:
        k_rep.pop("also", None)
    for i, p in enumerate(cand.get("evidence_preview") or [], 1):
        eid = "ev-%s-%04d" % (rid, i)
        loc = dict((k, v) for k, v in (p.get("locator") or {}).items() if v is not None)
        evidence.append({"evidence_id": eid, "review_id": rid, "kind": "text", "strategy": "jats_text",
                         "locator": loc, "quote": p["quote"][:QUOTE_MAX], "extracted_by": extracted_by, "at": at,
                         "confidence": "medium"})
        if p["about"] == "search_date" and search_date is not None:
            search_date["evidence_id"] = eid
        elif p["about"] == "k_reported" and k_rep is not None:
            k_rep["evidence_id"] = eid
        elif p["about"].startswith("signal:"):
            sig_ev.append(eid)
    signals = dict(cand["signals"])
    signals["evidence_ids"] = sig_ev
    doc = {
        "schema": REVIEW_SCHEMA, "model": MODEL, "review_id": rid, "status": cand["status"],
        "superseded_by": cand.get("superseded_by"), "ids": cand["ids"],
        "bib": dict((k, v) for k, v in cand["bib"].items()), "retrievals": cand.get("retrievals") or [],
        "is_cochrane": cand["is_cochrane"], "cochrane": cand["cochrane"], "found_by": cand["found_by"],
        "rank": cand["rank"], "signals": signals, "fulltext": cand["fulltext"],
        "candidates": [], "evidence": evidence, "decision_ids": [],
    }
    if search_date is not None:
        doc["search_date"] = search_date
    if k_rep is not None:
        doc["k_reported"] = k_rep
    # a gépi jelzések és a javaslat (pl. visszavont / hibajegyzék / narratív gyanú) a fájlban is megmaradnak —
    # korábban csak a find kimenetében látszottak (additív mezők)
    fl = cand.get("flags") or {}
    doc["flags"] = dict((k, fl.get(k)) for k in ("retracted", "erratum", "narrative_suspect", "updates",
                                                  "unverified_live") if k in fl)
    if cand.get("proposal"):
        doc["proposal"] = cand["proposal"]
    return doc
