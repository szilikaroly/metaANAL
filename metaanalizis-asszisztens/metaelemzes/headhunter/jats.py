# -*- coding: utf-8 -*-
"""JATS-elemző a Metaheadhunterhez (TERV_metaheadhunter.md 6.1) — csak memóriában, hálózat nélkül.

Mit csinál: egy áttekintés JATS-XML teljes szövegéből (Europe PMC ``fullTextXML`` vagy PMC ``efetch``)
kiolvassa azt, amire a bevont vizsgálatok kinyeréséhez (``included.py``, a1–a4 stratégiák) szükség van:

- **hivatkozások** (``Ref``): szöveg, első szerző, év, cím, folyóirat, és az azonosítók KÉT helyről:
  ``<pub-id pub-id-type="pmid|doi|pmcid">`` és a Europe PMC-dúsítás ``<ext-link ext-link-type="pmid|doi|
  pmcid" xlink:href="…">`` (+ regiszterszámok: ``pmc:clinical-trial`` ext-link és a szövegben álló NCT,
  ISRCTN, EudraCT …). Minden azonosító mellett ott a ``via`` (``jats.pub-id`` / ``jats.ext-link`` /
  ``jats.text``) — ezek **az áttekintés saját** azonosítói (``source: review``), az L4-ben API-val
  megerősítendők (N1);
- **Cochrane-szakaszok** (``RefGroup``, ``doc.ref_sections``): „References to studies included in this
  review" → al-listák/al-szakaszok, amelyek címe a vizsgálat Cochrane-azonosítója (pl. „Tameris 2013
  {published data only}"), kizárt / besorolásra váró / folyamatban lévő vizsgálatok, további hivatkozások;
  mindkét valós JATS-változatot kezeli (beágyazott ``<ref-list>``, illetve ``<sec sec-type="ref-list">``);
- **táblázatok** (``Table``): felirat, címke, fejléc-útvonalak oszloponként, rács a ``rowspan``/``colspan``
  kibontásával, cellánként a szöveg, a ``<p>``-darabok, a ``<sup>``-tartalom és az ``<xref>``-ek;
- **szakaszcímek, bekezdések** (``Paragraph``: szöveg + ``<xref>`` pozíciókkal), **ábrafeliratok**.

Szerzői jog (N4): a szöveg csak memóriában él; ez a modul semmit nem ír fájlba. A ``trim_for_fixture``
fejlesztői segéd tesztfixture-t készít: csak bibliográfiai adat, táblázat-szerkezet (első oszlop + rövid
számcellák) és rövid, a kinyerés által használt mondatok maradnak meg.

Biztonság: belső DTD-entitás (``<!ENTITY``) esetén elutasít (entitás-robbantás ellen); a HTML-névvel
írt entitásokat (``&nbsp;``) karakterre cseréli; méretkorlát ``MAX_BYTES``; mélységkorlát a bejárásnál.

Nyilvános API (a ``included.py`` és a későbbi ``extract.py``/``facade.show_text`` ezt használja)::

    doc = parse(xml_text_or_bytes, container="PMC6488980")
    doc.meta            # {'title','journal','year','pub_date','ids':{'pmid','pmcid','doi'},'license','article_type',
                        #  'is_cochrane','cochrane':{'cd_number','version'}}
    doc.refs            # {ref_id: Ref}, doc.ref_order: [ref_id …] dokumentum-sorrendben
    doc.groups          # {group_id: RefGroup}; doc.ref_sections: [RefSection]
    doc.tables          # [Table]; doc.figures: [Figure]; doc.paragraphs: [Paragraph]; doc.sections: [Section]
    doc.refs_for_rid(rid)            # ref-id vagy csoport-id → hivatkozás-azonosítók
    doc.expand_range(rid_a, rid_b)   # „[24–28]" típusú xref-tartomány kibontása
    doc.plain_text(parts=("methods", "results", "tables"))   # az ágensnek (show-text), NEM menthető
    find_quote(doc, quote)           # szó szerinti (szóközre normalizált) előfordulás — H004
    normalize_doi / normalize_pmid / normalize_pmcid / normalize_nct / norm_name / parse_author_year
"""
import html.entities
import re
import unicodedata
import xml.etree.ElementTree as ET

__all__ = [
    "JatsError", "JatsDoc", "Ref", "RefGroup", "RefSection", "Table", "Cell", "Figure", "Paragraph",
    "Section", "parse", "find_quote", "normalize_ws", "normalize_doi", "normalize_pmid",
    "normalize_pmcid", "normalize_nct", "norm_name", "norm_text", "parse_author_year",
    "first_author_from_text", "year_from_text", "section_kind", "classify_ref_section",
    "clean_group_title", "registry_ids_in_text", "trim_for_fixture", "MAX_BYTES",
]

XLINK = "{http://www.w3.org/1999/xlink}"
MAX_BYTES = 64 * 1024 * 1024
MAX_DEPTH = 400
MAX_TABLE_ROWS = 5000
MAX_TABLE_COLS = 200

_WS = re.compile(r"\s+")
_XML_BUILTIN = frozenset(["amp", "lt", "gt", "quot", "apos"])
_ENTITY_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]{1,31});")


class JatsError(ValueError):
    """A bemenet nem feldolgozható JATS (hibás XML, tiltott DTD-entitás, túl nagy)."""


# ---------------------------------------------------------------------------
# normalizálás (TERV 4.2 és 8.1)
# ---------------------------------------------------------------------------

_TRANSLIT = {"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss", "đ": "d",
             "Đ": "D", "ł": "l", "Ł": "L", "ı": "i", "þ": "th", "ð": "d"}
_DASHES = "‐‑‒–—―−﹘﹣－"


def normalize_ws(s):
    """Unicode NFC + szóköz-összevonás + szélek levágása (a szó szerinti idézet-összevetéshez)."""
    if s is None:
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFC", str(s))).strip()


def norm_text(s):
    """Összevetési alak: NFKD, ékezet le, átírás (ø→o, ß→ss …), kisbetű, nem-alfanumerikus → szóköz."""
    if not s:
        return ""
    s = "".join(_TRANSLIT.get(ch, ch) for ch in str(s))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^0-9a-z]+", " ", s.lower())
    return s.strip()


def norm_name(s):
    """Vezetéknév összevetési kulcsa: norm_text szóköz/kötőjel/aposztróf nélkül ('van Nielen' → 'vannielen')."""
    return norm_text(s).replace(" ", "")


def _fix_dashes(s):
    for d in _DASHES:
        s = s.replace(d, "-")
    return s


_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")


def normalize_doi(value):
    """DOI kisbetűsen, ``https://doi.org/`` / ``doi:`` előtag és záró írásjel nélkül; hibásra None."""
    if not value:
        return None
    s = unicodedata.normalize("NFKC", str(value)).strip()
    s = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    m = _DOI_RE.search(s)
    if not m:
        return None
    doi = m.group(0)
    doi = doi.rstrip(".,;:]}'")
    # záró ')' csak akkor marad, ha a DOI-n belül nyitott '(' párja
    while doi.endswith(")") and doi.count("(") < doi.count(")"):
        doi = doi[:-1]
    doi = doi.lower()
    return doi if re.match(r"^10\.\d{4,9}/\S+$", doi) else None


def normalize_pmid(value):
    if value is None:
        return None
    s = str(value).strip()
    s = re.sub(r"^(?:pmid|medline)\s*:?\s*", "", s, flags=re.I)
    m = re.search(r"(?:pubmed(?:\.ncbi\.nlm\.nih\.gov)?/|^)(\d{1,9})/?$", s)
    if not m:
        return None
    v = m.group(1).lstrip("0")
    return v or None


def normalize_pmcid(value):
    if value is None:
        return None
    s = str(value).strip()
    m = re.search(r"(?:PMC)?(\d{1,9})$", s, flags=re.I)
    if not m or not re.match(r"^(?:.*?/)?(?:PMC)?\d{1,9}$", s, flags=re.I):
        return None
    return "PMC" + m.group(1).lstrip("0")


def normalize_nct(value):
    m = re.search(r"NCT\s?(\d{8})\b", str(value or ""), flags=re.I)
    return ("NCT" + m.group(1)) if m else None


# A TERV 7. fejezetének regiszter-mintái (a szövegben).
_REGISTRY_PATTERNS = (
    ("nct", re.compile(r"\bNCT\s?\d{8}\b", re.I)),
    ("isrctn", re.compile(r"\bISRCTN\s?\d{8}\b", re.I)),
    ("actrn", re.compile(r"\bACTRN\d{14}\b", re.I)),
    ("chictr", re.compile(r"\bChiCTR[-A-Za-z0-9]*\d{6,}[-A-Za-z0-9]*\b")),
    ("eudract", re.compile(r"(?<![\d/-])(?:19|20)\d{2}-\d{6}-\d{2}(?![\d/-])")),
    ("irct", re.compile(r"\bIRCT\d+N\d+\b")),
    ("ctri", re.compile(r"\bCTRI/\d{4}/\d{2,3}/\d{6}\b")),
    ("drks", re.compile(r"\bDRKS\d{8}\b")),
    ("pactr", re.compile(r"\bPACTR\d{15}\b")),
    ("kct", re.compile(r"\bKCT\d{7}\b")),
    ("umin", re.compile(r"\bUMIN\d{9}\b")),
    ("ntr", re.compile(r"\bNTR\d{3,}\b")),
)


def registry_ids_in_text(text):
    """Regiszterszámok a szövegben (dokumentum-sorrendben, ismétlés nélkül): [(típus, érték)]."""
    out, seen = [], set()
    for kind, rx in _REGISTRY_PATTERNS:
        for m in rx.finditer(text or ""):
            v = m.group(0)
            if kind == "nct":
                v = normalize_nct(v)
            elif kind == "isrctn":
                v = "ISRCTN" + re.sub(r"\D", "", v)
            elif kind == "actrn":
                v = v.upper()
            key = (kind, v.upper())
            if key not in seen:
                seen.add(key)
                out.append((m.start(), kind, v))
    out.sort()
    return [(k, v) for _, k, v in out]


# ---------------------------------------------------------------------------
# szerző + év
# ---------------------------------------------------------------------------

_YEAR_TOKEN = re.compile(r"(?<![\d./-])((?:1[89]|20)\d{2})([a-z])?(?![\d])")
_INITIALS_TOKEN = re.compile(r"^(?:[A-Z][a-z]?(?:[.\-]\s*)?){1,4}\.?$|^[A-Z]{1,4}$|^[A-Z](?:-[A-Za-z])+\.?$")
_PARTICLES = frozenset(["van", "von", "de", "der", "den", "da", "di", "del", "della", "dos", "du", "la", "le",
                        "ten", "ter", "zu", "el", "al", "bin", "ibn", "st", "mac", "mc"])
_ET_AL = re.compile(r"\bet\.?\s*al\b\.?", re.I)


def _strip_initials(tokens):
    """Kezdőbetű-tokenek levágása a végéről és az elejéről (legalább egy token marad)."""
    toks = [t for t in tokens if t]
    while len(toks) > 1 and _INITIALS_TOKEN.match(toks[-1]) and not toks[-1].lower() in _PARTICLES:
        toks.pop()
    # elöl csak a ponttal írt vagy egybetűs kezdőbetű ('J. W. Anderson'), a betűszó ('GWP …') marad
    while len(toks) > 1 and _INITIALS_TOKEN.match(toks[0]) and not toks[0].lower() in _PARTICLES and \
            ("." in toks[0] or len(toks[0]) == 1):
        toks.pop(0)
    return toks


def first_author_from_text(text):
    """Első szerző vezetékneve egy formázatlan hivatkozás-szövegből ('Adetifa IM, Ota MO, …' → 'Adetifa').

    Konzervatív: ha nem dönthető el, None (a hívó ilyenkor nem illeszt szerzőre)."""
    if not text:
        return None
    s = normalize_ws(text).lstrip("*• ").strip()
    s = re.sub(r"^\[?\d{1,4}[.)\]]\s+", "", s)  # sorszám-előtag
    seg = re.split(r",|;|\bet\.?\s*al\b|\band\b|&", s, maxsplit=1)[0]
    if len(seg) > 60 or "." in seg.rstrip("."):
        # nincs vessző a szerzőlista elején ('WHO. Report …', 'J. W. Anderson …')
        m = re.match(r"^((?:[A-Z]\.\s*){1,4})([A-Z][\w'’\-]+(?:\s[A-Z][\w'’\-]+)?)", seg)
        if m:
            return m.group(2).strip()
        seg = re.split(r"\.\s", seg, maxsplit=1)[0]
        if len(seg) > 60:
            return None
    toks = _strip_initials(seg.replace(".", ". ").split())
    name = " ".join(toks).strip(" .")
    if not name or not re.search(r"[^\W\d_]", name):
        return None
    return name


def year_from_text(text):
    """Megjelenési év egy hivatkozás-szövegből: (év, utótag) vagy (None, None).

    Elsőbbség: Vancouver-alak ('2015;64(2)', '2010;29:439'), zárójeles év ('(2015)'), egyébként az utolsó
    év a DOI/URL-ek eltávolítása után."""
    if not text:
        return None, None
    s = re.sub(r"(?:https?://|doi:|10\.\d{4,9}/)\S+", " ", normalize_ws(text), flags=re.I)
    m = re.search(r"(?<![\d./-])((?:1[89]|20)\d{2})([a-z])?\s*[;:]\s*\d", s)
    if not m:
        m = re.search(r"(?<![\d./-])((?:1[89]|20)\d{2})([a-z])?\s*;", s)
    if not m:
        m = re.search(r"\(((?:1[89]|20)\d{2})([a-z])?\)", s)
    if not m:
        found = list(_YEAR_TOKEN.finditer(s))
        if not found:
            return None, None
        m = found[-1]
    y = int(m.group(1))
    if not 1800 <= y <= 2100:
        return None, None
    return y, (m.group(2) or None)


def parse_author_year(label):
    """Táblázat-sor / vizsgálatcímke → {'surname','year','suffix','label','raw','code'} vagy None.

    Példák (valós sorok mintájára): 'Adetifa 2010', '1 Acharjee, S. 2015 Israel', '2.1 Anderson, J. W. 2007
    USA', 'Reverri, E. J. USA 2015', 'van Nielen, M. 2014', 'Smith et al. (2010)', 'Sands 2004b',
    'EPIC‐HR 2021'. Év nélküli címkére None (nem találgatunk)."""
    if not label:
        return None
    raw = normalize_ws(label)
    s = raw.lstrip("*•†‡§¶# ").strip()
    code = None
    mcode = re.match(r"^(\d{1,3}(?:\.\d{1,2})?)[.)]?\s+(?=\D)", s)
    if mcode:
        code = mcode.group(1)
        s = s[mcode.end():]
    my = None
    for m in _YEAR_TOKEN.finditer(s):
        my = m
        break
    if not my:
        return None
    year = int(my.group(1))
    suffix = my.group(2)
    before = s[:my.start()]
    before = re.sub(r"[(\[]\s*$", "", before).strip()
    before = _ET_AL.split(before)[0]
    before = re.split(r"\band\b|&|;", before)[0]
    if "," in before:
        before = before.split(",")[0]
    toks = _strip_initials(re.sub(r"[()\[\]]", " ", before).split())
    surname = " ".join(toks).strip(" .,-")
    if not surname or not re.search(r"[^\W\d_]", surname):
        return None
    label_clean = "%s %d%s" % (surname, year, suffix or "")
    return {"surname": surname, "year": year, "suffix": suffix, "label": label_clean, "raw": raw,
            "code": code}


# ---------------------------------------------------------------------------
# szakasz-osztályozás
# ---------------------------------------------------------------------------

_SECTION_KINDS = (
    ("abstract", re.compile(r"^(abstract|summary)$")),
    ("methods", re.compile(r"\b(methods?|methodology|materials and methods|search methods|search strategy|"
                           r"literature search|data sources|eligibility criteria|selection criteria|"
                           r"data (extraction|collection)|study selection criteria)\b")),
    ("results", re.compile(r"\b(results?|findings|main results|description of studies|included studies|"
                           r"study selection|study characteristics|characteristics of included)\b")),
    ("discussion", re.compile(r"\b(discussion|limitations|strengths)\b")),
    ("conclusions", re.compile(r"\b(conclusions?|authors conclusions|implications)\b")),
    ("introduction", re.compile(r"\b(introduction|background|objectives?|rationale)\b")),
)


def section_kind(title):
    t = norm_text(title)
    if not t:
        return None
    for kind, rx in _SECTION_KINDS:
        if rx.search(t):
            return kind
    return None


_REF_SECTION_KINDS = (
    ("included", re.compile(r"^(references? to )?(the )?(studies|trials) included( in (this|the) review)?$|"
                            r"^references? to (the )?included (studies|trials)$|^included (studies|trials)$")),
    ("excluded", re.compile(r"(studies|trials) excluded from (this|the) review|^references? to (the )?excluded"
                            r" (studies|trials)$|^excluded (studies|trials)$")),
    ("awaiting", re.compile(r"awaiting (classification|assessment)")),
    ("ongoing", re.compile(r"ongoing (studies|trials)")),
    ("additional", re.compile(r"^additional references$")),
    ("other_versions", re.compile(r"other published versions of (this|the) review")),
)


def classify_ref_section(title):
    """Cochrane-hivatkozásszakasz típusa a címből (included/excluded/awaiting/ongoing/additional/other_versions)."""
    t = norm_text(title)
    for kind, rx in _REF_SECTION_KINDS:
        if rx.search(t):
            return kind
    return None


def clean_group_title(title):
    """'Tameris 2013 {published data only}' → 'Tameris 2013'."""
    t = normalize_ws(title)
    t = re.sub(r"\s*\{[^{}]*\}\s*$", "", t)
    return t.strip()


# ---------------------------------------------------------------------------
# szövegépítés pozíciókkal
# ---------------------------------------------------------------------------

class _TB(object):
    """Szöveg-építő: a szóközt menet közben vonja össze, így az xref-pozíciók pontosak maradnak."""

    __slots__ = ("parts", "length", "last_space")

    def __init__(self):
        self.parts = []
        self.length = 0
        self.last_space = True

    def add(self, s):
        if not s:
            return
        s = _WS.sub(" ", s)
        if self.last_space and s.startswith(" "):
            s = s[1:]
        if not s:
            return
        self.parts.append(s)
        self.length += len(s)
        self.last_space = s.endswith(" ")

    def space(self):
        if not self.last_space:
            self.add(" ")

    def text(self):
        return "".join(self.parts).rstrip()


def _local(tag):
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


_BLOCK_TAGS = frozenset([
    "p", "title", "label", "caption", "list", "list-item", "sec", "td", "th", "tr", "break", "def", "term",
    "disp-quote", "ref", "table-wrap-foot", "fn", "attrib", "def-item", "address", "boxed-text",
    "statement", "disp-formula", "speech", "verse-line",
])
_SKIP_TAGS = frozenset(["object-id", "graphic", "inline-graphic", "media", "alt-text", "long-desc",
                        "processing-meta", "tex-math"])
_CITE_MARK = re.compile(r"^[\[(]?\s*\d{1,4}(?:\s*[-\u2010-\u2015,;]\s*\d{1,4})*\s*[\])]?$")
_CITATION_PARTS = frozenset(["source", "year", "article-title", "person-group", "collab", "etal",
                             "publisher-name", "publisher-loc", "conf-name", "chapter-title", "date-in-citation",
                             "volume", "issue", "fpage", "lpage", "elocation-id", "edition", "comment", "uri",
                             "name", "string-name"])


class _Render(object):
    """Elem → szöveg (+ xref-ek pozícióval, sup-tartalom külön)."""

    def __init__(self, skip_pub_ids=False, keep_sup=True):
        self.tb = _TB()
        self.xrefs = []
        self.sups = []
        self.skip_pub_ids = skip_pub_ids
        self.keep_sup = keep_sup
        self.nosup = _TB()
        self.nocite = _TB()

    def run(self, elem):
        self._walk(elem, 0, False)
        return self

    def _add(self, s, in_sup, cite=False):
        self.tb.add(s)
        if not in_sup:
            self.nosup.add(s)
            if not cite:
                self.nocite.add(s)

    def _space(self):
        self.tb.space()
        self.nosup.space()
        self.nocite.space()

    def _guard(self):
        """Két egymás után álló hivatkozás-mező (pl. <source>…</source><year>) ne olvadjon össze."""
        if self.tb.parts and self.tb.parts[-1][-1:].isalnum():
            self._space()

    def _walk(self, el, depth, in_sup):
        if depth > MAX_DEPTH:
            return
        tag = _local(el.tag)
        if tag in _SKIP_TAGS or (self.skip_pub_ids and tag == "pub-id") or \
                (tag == "ext-link" and (el.get("ext-link-type") or "").lower() == "google-scholar"):
            if el.tail:
                self._add(el.tail, in_sup)
            return
        block = tag in _BLOCK_TAGS
        if block:
            self._space()
        elif tag in _CITATION_PARTS:
            self._guard()
        if tag == "name":
            pieces = []
            for ch in el:
                t = normalize_ws("".join(ch.itertext()))
                if t:
                    pieces.append(t)
            self._add(" ".join(pieces), in_sup)
        elif tag == "xref":
            sub = _Render(self.skip_pub_ids, self.keep_sup)
            sub._walk_children(el, depth + 1, in_sup)
            txt = sub.tb.text().strip()
            start = self.tb.length
            if txt:
                rtype = (el.get("ref-type") or "").lower()
                cite = bool(_CITE_MARK.match(txt)) or (rtype in ("table-fn", "fn") and len(txt) <= 4)
                # a teljes egészében felső indexes xref ('Egeland<xref><sup>i</sup></xref>') felső index marad
                sup_only = not sub.nosup.text().strip()
                self._add(txt, in_sup or sup_only, cite)
                start = self.tb.length - len(txt)
            end = self.tb.length
            for rid in (el.get("rid") or "").split():
                self.xrefs.append({"ref_type": el.get("ref-type") or "", "rid": rid, "text": txt,
                                   "start": start, "end": end})
            self.sups.extend(sub.sups)
        elif tag == "sup":
            sub = _Render(self.skip_pub_ids, self.keep_sup)
            sub._walk_children(el, depth + 1, True)
            txt = sub.tb.text().strip()
            self.sups.append(txt)
            # xref-ek a sup-ban (pl. <sup><xref …>12</xref></sup>)
            base = self.tb.length
            self.tb.add(sub.tb.text())
            for x in sub.xrefs:
                x = dict(x)
                x["start"] += base
                x["end"] += base
                self.xrefs.append(x)
        else:
            self._walk_children(el, depth, in_sup)
        if block:
            self._space()
        if el.tail:
            self._add(el.tail, in_sup)

    def _walk_children(self, el, depth, in_sup):
        if el.text:
            self._add(el.text, in_sup)
        for ch in el:
            self._walk(ch, depth + 1, in_sup)

    def text(self):
        return self.tb.text()


def _render(el, skip_pub_ids=False):
    if el is None:
        return ""
    r = _Render(skip_pub_ids=skip_pub_ids)
    r._walk_children(el, 0, False)
    return r.text().strip()


def _render_full(el, skip_pub_ids=False):
    r = _Render(skip_pub_ids=skip_pub_ids)
    if el is not None:
        r._walk_children(el, 0, False)
    return r


# ---------------------------------------------------------------------------
# modell
# ---------------------------------------------------------------------------

class Ref(object):
    """Egy irodalomjegyzék-tétel. ``ids``: {'pmid'|'doi'|'pmcid'|'nct': (érték, via)},
    ``registry``: [(típus, érték, via)] (NCT kivételével), ``section``: Cochrane-szakasz típusa vagy None."""

    __slots__ = ("ref_id", "label", "text", "authors", "first_author", "first_author_display", "year",
                 "year_suffix", "title", "journal", "volume", "issue", "fpage", "lpage", "ids", "registry",
                 "publication_type", "primary_marked", "section", "section_title", "group_id", "order",
                 "id_conflicts", "collab")

    def __init__(self, ref_id):
        self.ref_id = ref_id
        self.label = None
        self.text = ""
        self.authors = []
        self.first_author = None
        self.first_author_display = None
        self.year = None
        self.year_suffix = None
        self.title = None
        self.journal = None
        self.volume = None
        self.issue = None
        self.fpage = None
        self.lpage = None
        self.ids = {}
        self.registry = []
        self.publication_type = None
        self.primary_marked = False
        self.section = None
        self.section_title = None
        self.group_id = None
        self.order = 0
        self.id_conflicts = []
        self.collab = None

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self):
        return "<Ref %s %s %s>" % (self.ref_id, self.first_author, self.year)


class RefGroup(object):
    """Cochrane-csoport: egy vizsgálat közleményei (al-lista vagy al-szakasz, a címe a vizsgálat-ID)."""

    __slots__ = ("group_id", "title", "label", "section", "section_title", "ref_ids", "order")

    def __init__(self, group_id, title, section, section_title, order):
        self.group_id = group_id
        self.title = title
        self.label = clean_group_title(title)
        self.section = section
        self.section_title = section_title
        self.ref_ids = []
        self.order = order

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self):
        return "<RefGroup %s %r %s n=%d>" % (self.group_id, self.label, self.section, len(self.ref_ids))


class RefSection(object):
    __slots__ = ("kind", "title", "element_id", "group_ids", "ref_ids")

    def __init__(self, kind, title, element_id):
        self.kind = kind
        self.title = title
        self.element_id = element_id
        self.group_ids = []
        self.ref_ids = []

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


class Cell(object):
    """Táblacella. ``text``: teljes szöveg; ``text_nosup``: felső index nélkül (számértékekhez);
    ``text_label``: felső index és számos hivatkozás-jel (xref '13', '[4]') nélkül (vizsgálat-címkékhez:
    'Rosenstock, 2012<xref>13</xref>' → 'Rosenstock, 2012')."""
    __slots__ = ("text", "text_nosup", "text_label", "sups", "paragraphs", "xrefs", "is_header", "bold",
                 "colspan", "rowspan", "row", "col", "spanned", "origin")

    def __init__(self):
        self.text = ""
        self.text_nosup = ""
        self.text_label = ""
        self.sups = []
        self.paragraphs = []
        self.xrefs = []
        self.is_header = False
        self.bold = False
        self.colspan = 1
        self.rowspan = 1
        self.row = 0
        self.col = 0
        self.spanned = False
        self.origin = (0, 0)

    def copy_as_span(self, row, col):
        c = Cell()
        for k in ("text", "text_nosup", "text_label", "sups", "paragraphs", "xrefs", "is_header", "bold",
                  "colspan", "rowspan", "origin"):
            setattr(c, k, getattr(self, k))
        c.row, c.col, c.spanned = row, col, True
        return c

    def __repr__(self):
        return "<Cell r%d c%d %r>" % (self.row, self.col, self.text[:30])


class Table(object):
    """Táblázat: ``header`` és ``body`` sorok rácsként (minden sor ``ncols`` cella; a kifeszített cellák
    másolatai ``spanned=True``), ``columns[c]`` = a fejléc-útvonal ' / '-lel összefűzve."""

    __slots__ = ("element_id", "label", "caption", "caption_plain", "caption_xrefs", "footer", "section_path",
                 "header", "body", "ncols", "columns", "column_paths", "in_back", "order")

    def __init__(self):
        self.element_id = None
        self.label = None
        self.caption = ""
        self.caption_plain = ""  # felső indexek és számos hivatkozás-jelek nélkül (osztályozáshoz)
        self.caption_xrefs = []
        self.footer = ""
        self.section_path = []
        self.header = []
        self.body = []
        self.ncols = 0
        self.columns = []
        self.column_paths = []
        self.in_back = False
        self.order = 0

    @property
    def display_label(self):
        """Emberi címke: 'Table 1' / 'Comparison 1' / a felirat eleje."""
        if self.label:
            lab = self.label
            if re.match(r"^\d+(\.\d+)*$", lab):
                lab = "Table " + lab
            return lab
        return (self.caption or "")[:80] or None

    def __repr__(self):
        return "<Table %s %r rows=%d cols=%d>" % (self.element_id, self.display_label, len(self.body),
                                                   self.ncols)


class Figure(object):
    __slots__ = ("element_id", "label", "caption", "section_path")

    def __init__(self, element_id, label, caption, section_path):
        self.element_id = element_id
        self.label = label
        self.caption = caption
        self.section_path = section_path


class Paragraph(object):
    __slots__ = ("element_id", "text", "xrefs", "section_path", "in_abstract", "kind", "order")

    def __init__(self):
        self.element_id = None
        self.text = ""
        self.xrefs = []
        self.section_path = []
        self.in_abstract = False
        self.kind = None
        self.order = 0

    def __repr__(self):
        return "<Paragraph %s %r>" % ("/".join(self.section_path), self.text[:40])


class Section(object):
    __slots__ = ("element_id", "title", "path", "level", "kind", "in_abstract")

    def __init__(self, element_id, title, path, level, kind, in_abstract):
        self.element_id = element_id
        self.title = title
        self.path = path
        self.level = level
        self.kind = kind
        self.in_abstract = in_abstract


_NON_REF_XREF = frozenset(["fig", "table", "table-fn", "fn", "supplementary-material", "disp-formula", "app",
                           "boxed-text", "aff", "corresp", "author-notes", "statement", "list"])


class JatsDoc(object):
    """Egy feldolgozott JATS-dokumentum (memóriában)."""

    def __init__(self):
        self.container = None
        self.meta = {}
        self.refs = {}
        self.ref_order = []
        self.groups = {}
        self.ref_sections = []
        self.tables = []
        self.figures = []
        self.paragraphs = []
        self.sections = []
        self.element_ids = set()
        self.warnings = []
        self._ref_pos = None
        self._num_index = None

    # -- hivatkozás-segédek ------------------------------------------------
    def ref_index(self, ref_id):
        if self._ref_pos is None:
            self._ref_pos = {rid: i for i, rid in enumerate(self.ref_order)}
        return self._ref_pos.get(ref_id)

    def refs_for_number(self, n):
        """Számozott hivatkozás ('11' felső indexként) → ref-id: a hivatkozás címkéje ('11.') alapján, ennek
        hiányában a szokásos azonosító-mintákból ('ref11', 'CR11', 'B11', '…-bib-0011'). Visszaad: (ref_id, mód)."""
        if self._num_index is None:
            by_label, by_id = {}, {}
            for rid in self.ref_order:
                lab = re.sub(r"\D", "", self.refs[rid].label or "")
                if lab and lab not in by_label:
                    by_label[lab] = rid
                m = re.match(r"^(?:ref|r|cr|b|bib|bibr|c|ref-)[-_]?0*(\d{1,4})$|.*\bbib[-_]?0*(\d{1,4})$", rid, re.I)
                if m:
                    num = m.group(1) or m.group(2)
                    by_id.setdefault(num, rid)
            self._num_index = (by_label, by_id)
        key = str(int(n)) if str(n).isdigit() else None
        if key is None:
            return None, None
        by_label, by_id = self._num_index
        if key in by_label:
            return by_label[key], "label"
        if key in by_id:
            return by_id[key], "id"
        return None, None

    def refs_for_rid(self, rid):
        """Egy xref-cél → hivatkozás-azonosítók (ref-id esetén önmaga, csoport-id esetén a csoport tagjai)."""
        if rid in self.refs:
            return [rid]
        g = self.groups.get(rid)
        if g is not None:
            return list(g.ref_ids)
        return []

    def expand_range(self, rid_a, rid_b):
        """'[24–28]' → a két végpont közti összes hivatkozás a jegyzék sorrendjében (ha mindkettő hivatkozás)."""
        ia, ib = self.ref_index(rid_a), self.ref_index(rid_b)
        if ia is None or ib is None or ib < ia or ib - ia > 500:
            return [r for r in (rid_a, rid_b) if r in self.refs]
        return self.ref_order[ia:ib + 1]

    def bibr_refs(self, xrefs, text=None):
        """Egy szövegrész bibr-xref-jei → hivatkozás-lista; a köztük álló '–'/'-'/'to' tartományt kibontja.

        ``xrefs``: a Paragraph/Cell xref-listája (start/end pozícióval), ``text``: a hozzájuk tartozó szöveg."""
        bib = [x for x in xrefs if (x.get("ref_type") not in _NON_REF_XREF and
                                    (x["rid"] in self.refs or x["rid"] in self.groups))]
        out = []
        seen = set()
        for i, x in enumerate(bib):
            ids = self.refs_for_rid(x["rid"])
            if i > 0 and text is not None:
                prev = bib[i - 1]
                between = text[prev["end"]:x["start"]].strip()
                if re.fullmatch(r"[-‐-―−]|to", _fix_dashes(between) if between else "x") and \
                        prev["rid"] in self.refs and x["rid"] in self.refs:
                    ids = self.expand_range(prev["rid"], x["rid"])
            for r in ids:
                if r not in seen:
                    seen.add(r)
                    out.append(r)
        return out

    # -- szöveg az ágensnek -------------------------------------------------
    def plain_text(self, parts=("methods", "results", "tables"), max_chars=None):
        """Szöveg a megadott részekből (abstract, introduction, methods, results, discussion, conclusions,
        other, tables, references). Csak memóriában használható (show-text; N4: nem menthető)."""
        want = set(parts or ())
        out = []
        last_path = None
        for p in self.paragraphs:
            kind = "abstract" if p.in_abstract else (p.kind or "other")
            if kind not in want:
                continue
            if p.section_path != last_path:
                out.append("\n## " + " > ".join(p.section_path))
                last_path = p.section_path
            out.append(p.text)
        if "tables" in want:
            for t in self.tables:
                out.append("\n## %s [%s] %s" % (t.display_label or "Table", t.element_id or "-", t.caption))
                if t.columns:
                    out.append(" | ".join(t.columns))
                for row in t.body:
                    out.append(" | ".join("" if c.spanned and c.col != c.origin[1] else c.text for c in row))
        if "references" in want:
            out.append("\n## References")
            for rid in self.ref_order:
                r = self.refs[rid]
                out.append("[%s] %s" % (rid, r.text))
        s = "\n".join(out).strip()
        if max_chars is not None and len(s) > max_chars:
            s = s[:max_chars]
        return s

    def all_text_normalized(self):
        """Minden szöveg (bekezdések, táblacellák, feliratok, hivatkozások) szóközre normalizálva — H004."""
        chunks = [p.text for p in self.paragraphs]
        for t in self.tables:
            chunks.append(t.caption)
            chunks.extend(c.text for row in t.header + t.body for c in row if not c.spanned)
            chunks.append(t.footer)
        chunks.extend(f.caption for f in self.figures)
        chunks.extend(self.refs[r].text for r in self.ref_order)
        chunks.extend(g.title for g in self.groups.values())
        return normalize_ws(" \n ".join(c for c in chunks if c))

    def summary(self):
        """Áttekintő (szöveg nélkül): darabszámok — fejlesztéshez és naplóhoz."""
        return {
            "container": self.container,
            "refs": len(self.refs),
            "groups": {k: sum(1 for g in self.groups.values() if g.section == k)
                       for k in ("included", "excluded", "awaiting", "ongoing", "additional", "other_versions")},
            "tables": len(self.tables),
            "figures": len(self.figures),
            "paragraphs": len(self.paragraphs),
            "meta": self.meta,
        }


def find_quote(doc_or_text, quote):
    """Szó szerinti előfordulás (szóközre és Unicode-NFC-re normalizálva). H004 ellenőrzéshez."""
    q = normalize_ws(quote)
    if not q:
        return False
    hay = doc_or_text.all_text_normalized() if isinstance(doc_or_text, JatsDoc) else normalize_ws(doc_or_text)
    return q in hay


# ---------------------------------------------------------------------------
# beolvasás
# ---------------------------------------------------------------------------

def _decode(source):
    if isinstance(source, bytes):
        if len(source) > MAX_BYTES:
            raise JatsError("A JATS-dokumentum túl nagy (%d bájt > %d)." % (len(source), MAX_BYTES))
        m = re.match(br"^\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9._-]+)[\"']", source)
        enc = m.group(1).decode("ascii") if m else "utf-8"
        try:
            text = source.decode(enc)
        except (LookupError, UnicodeDecodeError):
            text = source.decode("utf-8", errors="replace")
    else:
        text = str(source)
        if len(text) > MAX_BYTES:
            raise JatsError("A JATS-dokumentum túl nagy.")
    if text.startswith("﻿"):
        text = text[1:]
    return text


def _prepare(text):
    if re.search(r"<!ENTITY", text):
        raise JatsError("A JATS-dokumentum belső DTD-entitást deklarál (<!ENTITY) — biztonsági okból elutasítva.")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)

    def _ent(m):
        name = m.group(1)
        if name in _XML_BUILTIN:
            return m.group(0)
        cp = html.entities.name2codepoint.get(name)
        if cp is None:
            ch = html.entities.html5.get(name + ";")
            if ch is None:
                return m.group(0)
            return ch.replace("&", "&amp;").replace("<", "&lt;")
        ch = chr(cp)
        return {"&": "&amp;", "<": "&lt;"}.get(ch, ch)

    return _ENTITY_RE.sub(_ent, text)


def parse(source, container=None):
    """JATS (str vagy bytes) → JatsDoc. ``container``: a lokátorokba kerülő azonosító (pl. 'PMC6488980');
    ha nincs megadva, a dokumentum saját PMCID-je (vagy None)."""
    text = _prepare(_decode(source))
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise JatsError("Hibás XML: %s" % exc)
    if _local(root.tag) != "article":
        found = None
        for el in root.iter():
            if _local(el.tag) == "article":
                found = el
                break
        if found is None:
            raise JatsError("Nem JATS-dokumentum (nincs <article> elem).")
        root = found
    doc = JatsDoc()
    _parse_meta(doc, root)
    doc.container = container or doc.meta.get("ids", {}).get("pmcid") or None
    for el in root.iter():
        eid = el.get("id")
        if eid:
            doc.element_ids.add(eid)
    _Walker(doc).walk(root)
    _finish_refs(doc)
    return doc


# ---------------------------------------------------------------------------
# metaadat
# ---------------------------------------------------------------------------

def _find(el, path_tags):
    """Első leszármazott a megadott helyi tagnevű útvonalon (névtér-függetlenül)."""
    cur = [el]
    for tag in path_tags:
        nxt = []
        for c in cur:
            for ch in c:
                if _local(ch.tag) == tag:
                    nxt.append(ch)
        if not nxt:
            return None
        cur = nxt
    return cur[0]


def _iter_local(el, tag):
    for x in el.iter():
        if _local(x.tag) == tag:
            yield x


def _parse_meta(doc, root):
    meta = {"title": None, "journal": None, "year": None, "pub_date": None, "ids": {}, "license": None,
            "license_url": None,
            "article_type": root.get("article-type"), "is_cochrane": False, "cochrane": None,
            "root_id": root.get("id")}
    front = _find(root, ["front"])
    am = _find(front, ["article-meta"]) if front is not None else None
    jm = _find(front, ["journal-meta"]) if front is not None else None
    if am is not None:
        for aid in am:
            if _local(aid.tag) != "article-id":
                continue
            typ = (aid.get("pub-id-type") or "").lower()
            val = normalize_ws("".join(aid.itertext()))
            if typ in ("pmid", "medline"):
                v = normalize_pmid(val)
                if v:
                    meta["ids"].setdefault("pmid", v)
            elif typ in ("pmc", "pmcid"):
                v = normalize_pmcid(val)
                if v:
                    meta["ids"].setdefault("pmcid", v)
            elif typ == "doi":
                v = normalize_doi(val)
                if v:
                    meta["ids"].setdefault("doi", v)
        tg = _find(am, ["title-group", "article-title"])
        meta["title"] = _render(tg) or None
        dates = []
        for pd in am:
            if _local(pd.tag) == "pub-date":
                y = pd.find("year") if pd.find("year") is not None else _find(pd, ["year"])
                if y is not None and (y.text or "").strip()[:4].isdigit():
                    yy = int((y.text or "").strip()[:4])
                    ptype = (pd.get("pub-type") or pd.get("date-type") or "").lower()
                    if meta["year"] is None or ptype in ("epub", "pub", "ppub", "collection"):
                        meta["year"] = yy if meta["year"] is None else min(meta["year"], yy)
                    if ptype in ("", "epub", "pub", "ppub", "collection", "electronic", "print", "epub-ppub"):
                        dates.append(_date_parts(pd, yy))
        if dates:
            # a legkorábbi közlési dátum (a tartalék keresési dátumhoz: korábbi = szélesebb frissítési ablak)
            meta["pub_date"] = _date_str(sorted(dates, key=lambda d: (d[0], d[1] or 0, d[2] or 0))[0])
        perm = _find(am, ["permissions"])
        if perm is not None:
            for lic in perm:
                if _local(lic.tag) == "license":
                    urls = [lic.get(XLINK + "href") or ""]
                    for x in lic.iter():
                        t = _local(x.tag)
                        if t == "license_ref":
                            urls.append(normalize_ws("".join(x.itertext())))
                        elif t == "ext-link":
                            urls.append(x.get(XLINK + "href") or "")
                    urls = [u for u in urls if u]
                    cc = [u for u in urls if "creativecommons.org" in u.lower()]
                    href = (cc or urls or [""])[0]
                    meta["license_url"] = href or None
                    meta["license"] = _license_name(lic.get("license-type") or "", href,
                                                    normalize_ws("".join(lic.itertext()))[:600])
                    break
    if jm is not None:
        jt = _find(jm, ["journal-title-group", "journal-title"])
        if jt is not None:
            meta["journal"] = _render(jt) or None
        if not meta["journal"]:
            for jid in jm:
                if _local(jid.tag) == "journal-id" and (jid.get("journal-id-type") or "") in ("nlm-ta", "iso-abbrev"):
                    meta["journal"] = normalize_ws(jid.text)
                    break
    doi = meta["ids"].get("doi") or ""
    m = re.search(r"10\.1002/14651858\.(cd\d{6})(?:\.pub(\d+))?", doi, flags=re.I)
    jn = norm_text(meta["journal"] or "")
    if m or jn.startswith("cochrane database"):
        meta["is_cochrane"] = True
        cd = m.group(1).upper() if m else None
        if not cd and meta.get("root_id"):
            m2 = re.match(r"^(CD\d{6})(?:\.pub(\d+))?$", meta["root_id"])
            if m2:
                cd = m2.group(1)
                m = m2
        version = None
        if m is not None and m.group(2):
            version = int(m.group(2))
        elif cd:
            version = 1
        meta["cochrane"] = {"cd_number": cd, "version": version}
    doc.meta = meta


def _date_parts(pd, year):
    """<pub-date> → (év, hó|None, nap|None); a hibás hó/nap elhagyva (nem találgatunk)."""
    def num(tag, lo, hi):
        el = _find(pd, [tag])
        txt = (el.text or "").strip() if el is not None else ""
        if txt.isdigit() and lo <= int(txt) <= hi:
            return int(txt)
        return None
    month = num("month", 1, 12)
    day = num("day", 1, 31) if month else None
    return (year, month, day)


def _date_str(parts):
    y, m, d = parts
    if m and d:
        return "%04d-%02d-%02d" % (y, m, d)
    if m:
        return "%04d-%02d" % (y, m)
    return "%04d" % y


def _license_name(ltype, href, text=""):
    h = (href or "").lower()
    m = re.search(r"creativecommons\.org/licenses/([a-z\-]+)/", h)
    if m:
        return "cc " + m.group(1)
    if "publicdomain" in h:
        return "public domain"
    m = re.search(r"\bCC[ -]?(BY(?:-N[CD])*(?:-SA)?)\b", text or "", flags=re.I)
    if m:
        return "cc " + m.group(1).lower()
    m = re.search(r"Creative\s+Commons\s+Attribution((?:[\s-]+(?:Non-?Commercial|No-?Derivs|No-?Derivatives|"
                  r"Share-?Alike))*)", text or "", flags=re.I)
    if m:
        parts = ["by"]
        tail = m.group(1).lower()
        if "commercial" in tail:
            parts.append("nc")
        if "deriv" in tail:
            parts.append("nd")
        if "share" in tail:
            parts.append("sa")
        return "cc " + "-".join(parts)
    lt = (ltype or "").strip().lower()
    if lt:
        return lt.replace("open-access", "open access")
    return None


# ---------------------------------------------------------------------------
# bejárás: szakaszok, bekezdések, táblák, ábrák, hivatkozások
# ---------------------------------------------------------------------------

_SECTIONISH = frozenset(["sec", "abstract", "trans-abstract", "notes", "app", "app-group", "ack", "boxed-text",
                         "ref-list", "glossary", "fn-group", "back", "body", "front"])


class _Walker(object):
    def __init__(self, doc):
        self.doc = doc
        self.table_n = 0
        self.para_n = 0
        self.ref_n = 0
        self.group_n = 0

    def walk(self, root):
        for ch in root:
            tag = _local(ch.tag)
            if tag == "front":
                am = _find(ch, ["article-meta"])
                if am is not None:
                    for ab in am:
                        if _local(ab.tag) == "abstract" and (ab.get("abstract-type") or "") in ("", "structured"):
                            self._walk(ab, [], 1, ctx={"abstract": True, "back": False, "refkind": None,
                                                       "reftitle": None, "group": None})
            elif tag in ("body", "back"):
                self._walk(ch, [], 0, ctx={"abstract": False, "back": tag == "back", "refkind": None,
                                           "reftitle": None, "group": None})
            elif tag == "sub-article":
                continue

    def _title_of(self, el):
        for ch in el:
            if _local(ch.tag) == "title":
                return _render(ch)
        return None

    def _walk(self, el, path, depth, ctx):
        if depth > MAX_DEPTH:
            return
        tag = _local(el.tag)
        if tag == "abstract":
            title = self._title_of(el) or "Abstract"
            path = path + [title]
            self.doc.sections.append(Section(el.get("id"), title, path, len(path), "abstract", True))
        elif tag in _SECTIONISH and tag not in ("body", "back", "front"):
            title = self._title_of(el)
            is_reflike = tag == "ref-list" or (el.get("sec-type") or "") == "ref-list"
            if title:
                rk = classify_ref_section(title) if is_reflike or ctx.get("refkind") else None
                if is_reflike and rk is None and ctx.get("refkind") is None and \
                        norm_text(title) not in ("references", "reference", "bibliography", "literature cited"):
                    rk = None
                newctx = dict(ctx)
                if rk is not None:
                    newctx["refkind"] = rk
                    newctx["reftitle"] = title
                    newctx["refsec_id"] = el.get("id")
                    newctx["group"] = None
                    self.doc.ref_sections.append(RefSection(rk, title, el.get("id")))
                elif ctx.get("refkind") is not None and is_reflike:
                    # csoport (vizsgálat) a Cochrane-szakaszon belül
                    gid = el.get("id") or "grp-%04d" % (self.group_n + 1)
                    self.group_n += 1
                    g = RefGroup(gid, title, ctx["refkind"], ctx.get("reftitle"), self.group_n)
                    self.doc.groups[gid] = g
                    for rs in reversed(self.doc.ref_sections):
                        if rs.kind == ctx["refkind"]:
                            rs.group_ids.append(gid)
                            break
                    newctx["group"] = gid
                ctx = newctx
                path = path + [title]
                kind = section_kind(title) if not ctx.get("abstract") else "abstract"
                if kind is None and path[:-1]:
                    for s in reversed(self.doc.sections):
                        if s.path == path[:-1]:
                            kind = s.kind
                            break
                self.doc.sections.append(Section(el.get("id"), title, path, len(path), kind, ctx["abstract"]))
            elif is_reflike and ctx.get("refkind") is not None and ctx.get("group") is None and \
                    (el.get("sec-type") or "") == "ref-list":
                pass
        if tag == "ref":
            self._ref(el, ctx)
            return
        if tag == "table-wrap":
            self._table(el, path, ctx)
            return
        if tag == "fig":
            lab = None
            cap = ""
            for ch in el:
                t = _local(ch.tag)
                if t == "label":
                    lab = _render(ch)
                elif t == "caption":
                    cap = _render(ch)
            self.doc.figures.append(Figure(el.get("id"), lab, cap, list(path)))
            return
        if tag == "p":
            self._para(el, path, ctx)
            # a bekezdésben lévő táblákat/ábrákat (Cochrane: <p><table-wrap>) is fel kell venni
            for ch in el.iter():
                t = _local(ch.tag)
                if ch is el:
                    continue
                if t == "table-wrap":
                    self._table(ch, path, ctx)
                elif t == "fig":
                    self._walk(ch, path, depth + 1, ctx)
            return
        if tag in ("title", "label", "caption"):
            return
        for ch in el:
            self._walk(ch, path, depth + 1, ctx)

    # -- bekezdés --------------------------------------------------------------
    def _para(self, el, path, ctx):
        # a bekezdésen belüli tábla/ábra/lista-tábla szövege nem a bekezdés része
        r = _Render()
        r.tb.add("")
        self._render_para(el, r, 0)
        txt = r.text().strip()
        if not txt:
            return
        p = Paragraph()
        p.element_id = el.get("id")
        p.text = txt
        p.xrefs = r.xrefs
        p.section_path = list(path)
        p.in_abstract = bool(ctx.get("abstract"))
        kind = None
        for s in reversed(self.doc.sections):
            if s.path == path:
                kind = s.kind
                break
        p.kind = "abstract" if p.in_abstract else kind
        self.para_n += 1
        p.order = self.para_n
        self.doc.paragraphs.append(p)

    def _render_para(self, el, r, depth):
        if el.text:
            r._add(el.text, False)
        for ch in el:
            t = _local(ch.tag)
            if t in ("table-wrap", "fig", "table", "disp-formula", "supplementary-material"):
                r._space()
                if ch.tail:
                    r._add(ch.tail, False)
                continue
            r._walk(ch, depth + 1, False)

    # -- hivatkozás --------------------------------------------------------------
    def _ref(self, el, ctx):
        rid = el.get("id") or "ref-auto-%04d" % (self.ref_n + 1)
        if rid in self.doc.refs:
            rid = "%s-dup%d" % (rid, self.ref_n + 1)
        self.ref_n += 1
        ref = Ref(rid)
        ref.order = self.ref_n
        ref.section = ctx.get("refkind")
        ref.section_title = ctx.get("reftitle")
        ref.group_id = ctx.get("group")
        _fill_ref(ref, el)
        self.doc.refs[rid] = ref
        self.doc.ref_order.append(rid)
        if ref.group_id and ref.group_id in self.doc.groups:
            self.doc.groups[ref.group_id].ref_ids.append(rid)
        if ref.section:
            for rs in reversed(self.doc.ref_sections):
                if rs.kind == ref.section:
                    rs.ref_ids.append(rid)
                    break

    # -- táblázat --------------------------------------------------------------
    def _table(self, el, path, ctx):
        self.table_n += 1
        t = Table()
        t.order = self.table_n
        t.element_id = el.get("id") or "table-%04d" % self.table_n
        t.section_path = list(path)
        t.in_back = bool(ctx.get("back"))
        tbl = None
        for ch in el:
            tag = _local(ch.tag)
            if tag == "label":
                t.label = _render(ch) or None
            elif tag == "caption":
                rr = _render_full(ch)
                t.caption = rr.text().strip()
                t.caption_plain = normalize_ws(rr.nocite.text())
                t.caption_xrefs = rr.xrefs
            elif tag == "table-wrap-foot":
                t.footer = _render(ch)
            elif tag == "table" and tbl is None:
                tbl = ch
            elif tag == "alternatives" and tbl is None:
                for a in ch:
                    if _local(a.tag) == "table":
                        tbl = a
                        break
        if tbl is not None:
            _fill_grid(t, tbl)
        self.doc.tables.append(t)


def _fill_ref(ref, el):
    cit = None
    for ch in el.iter():
        if _local(ch.tag) in ("mixed-citation", "element-citation", "citation", "nlm-citation"):
            cit = ch
            break
    lab = None
    for ch in el:
        if _local(ch.tag) == "label":
            lab = _render(ch)
    ref.label = lab or None
    src = cit if cit is not None else el
    ref.publication_type = (src.get("publication-type") or src.get("citation-type") or None) if cit is not None else None
    text = _render(src, skip_pub_ids=True) if cit is not None else _render(el, skip_pub_ids=True)
    if lab and text.startswith(lab):
        text = text[len(lab):].strip()
    text = re.sub(r"\[\s*DOI:\s*\]", "", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = normalize_ws(text)
    if text.startswith("*"):
        ref.primary_marked = True
        text = text.lstrip("* ").strip()
    ref.text = text
    # szerzők
    authors = []
    display = None
    pgs = [x for x in _iter_local(src, "person-group")]
    name_holders = []
    if pgs:
        for pg in pgs:
            if (pg.get("person-group-type") or "author") == "author":
                name_holders.append(pg)
                break
        if not name_holders:
            name_holders = pgs[:1]
    else:
        name_holders = [src]
    for holder in name_holders:
        for ch in holder:
            t = _local(ch.tag)
            if t in ("name", "string-name"):
                sn = None
                gn = None
                for part in ch:
                    pt = _local(part.tag)
                    if pt == "surname":
                        sn = normalize_ws("".join(part.itertext()))
                    elif pt == "given-names":
                        gn = normalize_ws("".join(part.itertext()))
                if not sn and t == "string-name":
                    sn = first_author_from_text(normalize_ws("".join(ch.itertext())))
                if sn:
                    authors.append(sn)
                    if display is None:
                        display = ("%s %s" % (sn, gn)).strip() if gn else sn
            elif t == "collab" and not authors:
                ref.collab = normalize_ws("".join(ch.itertext())) or None
    if not authors and src is not None:
        # a <name> elemek mélyebben is lehetnek (mixed-citation közvetlen gyermekei helyett)
        for nm in _iter_local(src, "name"):
            sn = None
            gn = None
            for part in nm:
                pt = _local(part.tag)
                if pt == "surname":
                    sn = normalize_ws("".join(part.itertext()))
                elif pt == "given-names":
                    gn = normalize_ws("".join(part.itertext()))
            if sn:
                authors.append(sn)
                if display is None:
                    display = ("%s %s" % (sn, gn)).strip() if gn else sn
    ref.authors = authors[:50]
    if authors:
        ref.first_author = authors[0]
        ref.first_author_display = display
    elif ref.collab:
        ref.first_author = ref.collab
        ref.first_author_display = ref.collab
    else:
        fa = first_author_from_text(text)
        ref.first_author = fa
        ref.first_author_display = fa
    # mezők
    for ch in src.iter():
        t = _local(ch.tag)
        if t == "article-title" and ref.title is None:
            ref.title = _render(ch) or None
        elif t == "source" and ref.journal is None:
            ref.journal = _render(ch) or None
        elif t == "year" and ref.year is None:
            m = re.match(r"\s*((?:1[89]|20)\d{2})([a-z])?", "".join(ch.itertext()))
            if m:
                ref.year = int(m.group(1))
                ref.year_suffix = m.group(2)
        elif t == "volume" and ref.volume is None:
            ref.volume = normalize_ws("".join(ch.itertext())) or None
        elif t == "issue" and ref.issue is None:
            ref.issue = normalize_ws("".join(ch.itertext())) or None
        elif t == "fpage" and ref.fpage is None:
            ref.fpage = normalize_ws("".join(ch.itertext())) or None
        elif t == "lpage" and ref.lpage is None:
            ref.lpage = normalize_ws("".join(ch.itertext())) or None
    if ref.year is None:
        y, suf = year_from_text(text)
        if y is None:
            for ch in src.iter():
                if _local(ch.tag) == "ext-link" and (ch.get("ext-link-type") or "") == "google-scholar":
                    m = re.search(r"publication_year=((?:1[89]|20)\d{2})", ch.get(XLINK + "href") or "")
                    if m:
                        y = int(m.group(1))
                        break
        ref.year, ref.year_suffix = y, suf
    if ref.title is None and cit is not None:
        ref.title = _title_from_text(text)
    # azonosítók
    _collect_ids(ref, el, text)


def _title_from_text(text):
    """Óvatos cím-becslés formázatlan hivatkozásból ('Szerzők. Cím. Folyóirat …'); bizonytalanra None."""
    s = normalize_ws(text)
    m = _ET_AL.search(s)
    if m and m.start() < 400:
        rest = s[m.end():].lstrip(" .,")
    else:
        # a szerzőlista vége: az első '. ' amelyet nagybetűs, legalább 2 betűs szó követ
        m2 = re.search(r"(?:\b[A-Z]{1,4}|\b[A-Z][a-z]?-?[A-Za-z]?)\.\s+(?=[A-Z0-9\"'“][a-z0-9])", s)
        if not m2 or m2.start() > 400:
            return None
        rest = s[m2.end():]
    piece = re.split(r"(?<=[a-z0-9)\]?!])\.\s+(?=[A-Z])", rest, maxsplit=1)[0].strip()
    piece = piece.rstrip(".")
    if len(piece) < 12 or len(piece) > 400:
        return None
    if re.match(r"^(?:[A-Z][\w'\-]+ [A-Z]{1,3},\s*){2,}", piece):
        return None
    return piece


def _set_id(ref, kind, value, via):
    if not value:
        return
    cur = ref.ids.get(kind)
    if cur is None:
        ref.ids[kind] = (value, via)
    elif cur[0] != value:
        ref.id_conflicts.append({"kind": kind, "kept": cur[0], "other": value, "via": via})


def _collect_ids(ref, el, text):
    for ch in el.iter():
        t = _local(ch.tag)
        if t == "pub-id":
            typ = (ch.get("pub-id-type") or "").lower()
            val = normalize_ws("".join(ch.itertext()))
            if typ in ("pmid", "medline"):
                _set_id(ref, "pmid", normalize_pmid(val), "jats.pub-id")
            elif typ == "doi":
                _set_id(ref, "doi", normalize_doi(val), "jats.pub-id")
            elif typ in ("pmcid", "pmc"):
                _set_id(ref, "pmcid", normalize_pmcid(val), "jats.pub-id")
            elif typ in ("other", "publisher-id") and _DOI_RE.search(val or ""):
                if "doi" not in ref.ids:
                    _set_id(ref, "doi", normalize_doi(val), "jats.pub-id")
        elif t == "ext-link":
            typ = (ch.get("ext-link-type") or "").lower()
            href = (ch.get(XLINK + "href") or "").strip()
            if typ == "pmid":
                _set_id(ref, "pmid", normalize_pmid(href), "jats.ext-link")
            elif typ == "doi":
                _set_id(ref, "doi", normalize_doi(href), "jats.ext-link")
            elif typ in ("pmcid", "pmc"):
                _set_id(ref, "pmcid", normalize_pmcid(href), "jats.ext-link")
            elif "clinical-trial" in typ or typ in ("clintrialgov", "clinicaltrials"):
                nct = normalize_nct(href) or normalize_nct("".join(ch.itertext()))
                if nct:
                    _set_id(ref, "nct", nct, "jats.ext-link")
                else:
                    for kind, v in registry_ids_in_text(href + " " + "".join(ch.itertext())):
                        _add_registry(ref, kind, v, "jats.ext-link")
            elif typ in ("uri", "url", ""):
                if re.search(r"doi\.org/10\.", href, flags=re.I):
                    if "doi" not in ref.ids:
                        _set_id(ref, "doi", normalize_doi(href), "jats.ext-link")
                m = re.search(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d{1,9})", href)
                if m and "pmid" not in ref.ids:
                    _set_id(ref, "pmid", normalize_pmid(m.group(1)), "jats.ext-link")
                m = re.search(r"/pmc/articles/(PMC\d+)", href, flags=re.I)
                if m and "pmcid" not in ref.ids:
                    _set_id(ref, "pmcid", normalize_pmcid(m.group(1)), "jats.ext-link")
    # a szövegben álló azonosítók (csak ha strukturáltan nincs meg)
    full = normalize_ws("".join(el.itertext()))
    if "doi" not in ref.ids:
        m = re.search(r"(?:doi:\s*|doi\.org/)(10\.\d{4,9}/[^\s\"<>]+)", full, flags=re.I)
        if m:
            _set_id(ref, "doi", normalize_doi(m.group(1)), "jats.text")
    if "pmid" not in ref.ids:
        m = re.search(r"\bPMID:?\s*(\d{1,9})\b", full)
        if m:
            _set_id(ref, "pmid", normalize_pmid(m.group(1)), "jats.text")
    if "pmcid" not in ref.ids:
        m = re.search(r"\bPMCID:?\s*(PMC\d{1,9})\b", full)
        if m:
            _set_id(ref, "pmcid", normalize_pmcid(m.group(1)), "jats.text")
    for kind, v in registry_ids_in_text(full):
        if kind == "nct":
            if "nct" not in ref.ids:
                _set_id(ref, "nct", v, "jats.text")
        else:
            _add_registry(ref, kind, v, "jats.text")


def _add_registry(ref, kind, value, via):
    for k, v, _ in ref.registry:
        if k == kind and v.upper() == value.upper():
            return
    ref.registry.append((kind, value, via))


def _finish_refs(doc):
    # A csoportcím regiszterszámát (pl. 'NCT04518410 {published data only}') NEM adjuk a hivatkozásokhoz:
    # az a vizsgálat címkéje, nem a közlemény azonosítója (included.py a csoport szintjén kezeli).
    # Üres csoportok (hivatkozás nélkül) eldobása a szakasz-listákból.
    for rs in doc.ref_sections:
        rs.group_ids = [g for g in rs.group_ids if g in doc.groups and doc.groups[g].ref_ids]


# ---------------------------------------------------------------------------
# táblázat-rács
# ---------------------------------------------------------------------------

def _int_attr(el, name, default=1, cap=1000):
    try:
        v = int(str(el.get(name) or default).strip())
    except ValueError:
        v = default
    return max(1, min(v, cap))


def _cell_from(el, is_header):
    c = Cell()
    r = _Render()
    r._walk_children(el, 0, False)
    c.text = r.text().strip()
    c.text_nosup = r.nosup.text().strip()
    c.text_label = normalize_ws(re.sub(r"[\[(]\s*[,;\-\u2010-\u2015]*\s*[\])]", " ", r.nocite.text()))
    c.sups = [s for s in r.sups if s]
    c.xrefs = r.xrefs
    paras = []
    for ch in el:
        if _local(ch.tag) == "p":
            pt = _render(ch)
            if pt:
                paras.append(pt)
    c.paragraphs = paras or ([c.text] if c.text else [])
    c.is_header = is_header
    bold_txt = normalize_ws(" ".join(normalize_ws("".join(b.itertext())) for b in _iter_local(el, "bold")))
    c.bold = bool(c.text) and bold_txt.replace(" ", "") == c.text.replace(" ", "")
    c.colspan = _int_attr(el, "colspan", cap=MAX_TABLE_COLS)
    c.rowspan = _int_attr(el, "rowspan", cap=MAX_TABLE_ROWS)
    return c


def _fill_grid(t, tbl):
    raw_rows = []  # (is_thead, [ (el, tag) ])
    for ch in tbl:
        tag = _local(ch.tag)
        if tag in ("thead", "tbody", "tfoot"):
            for tr in ch:
                if _local(tr.tag) == "tr":
                    raw_rows.append((tag == "thead", tr))
        elif tag == "tr":
            raw_rows.append((False, ch))
    raw_rows = raw_rows[:MAX_TABLE_ROWS]
    grid = []
    occupied = {}
    header_flags = []
    for ri, (in_thead, tr) in enumerate(raw_rows):
        row = []
        ci = 0
        cells_el = [c for c in tr if _local(c.tag) in ("td", "th")]
        for cel in cells_el:
            while (ri, ci) in occupied:
                row.append(occupied.pop((ri, ci)))
                ci += 1
            if ci >= MAX_TABLE_COLS:
                break
            cell = _cell_from(cel, in_thead or _local(cel.tag) == "th")
            cell.row, cell.col = ri, ci
            cell.origin = (ri, ci)
            row.append(cell)
            for dc in range(1, cell.colspan):
                if ci + dc >= MAX_TABLE_COLS:
                    break
                row.append(cell.copy_as_span(ri, ci + dc))
            for dr in range(1, cell.rowspan):
                if ri + dr >= len(raw_rows):
                    break
                for dc in range(cell.colspan):
                    if ci + dc >= MAX_TABLE_COLS:
                        break
                    occupied[(ri + dr, ci + dc)] = cell.copy_as_span(ri + dr, ci + dc)
            ci += cell.colspan
        while (ri, ci) in occupied:
            row.append(occupied.pop((ri, ci)))
            ci += 1
        grid.append(row)
        header_flags.append(in_thead)
    ncols = max([len(r) for r in grid] or [0])
    for ri, row in enumerate(grid):
        while len(row) < ncols:
            c = Cell()
            c.row, c.col, c.origin = ri, len(row), (ri, len(row))
            row.append(c)
    # fejléc: thead, vagy (thead nélkül) az elejéről a csupa-th / csupa-félkövér sorok
    n_head = 0
    if any(header_flags):
        while n_head < len(grid) and header_flags[n_head]:
            n_head += 1
    else:
        while n_head < len(grid) and n_head < 3:
            row = grid[n_head]
            filled = [c for c in row if c.text]
            if len(filled) >= 2 and all(c.is_header or c.bold for c in filled) and \
                    (n_head + 1 < len(grid)):
                n_head += 1
            else:
                break
    t.header = grid[:n_head]
    t.body = grid[n_head:]
    for row in t.header:
        for c in row:
            c.is_header = True
    t.ncols = ncols
    paths = []
    for ci in range(ncols):
        parts = []
        for row in t.header:
            txt = row[ci].text if ci < len(row) else ""
            if txt and (not parts or parts[-1] != txt):
                parts.append(txt)
        paths.append(parts)
    t.column_paths = paths
    t.columns = [" / ".join(p) for p in paths]


# ---------------------------------------------------------------------------
# fejlesztői segéd: fixture-vágás (N4 — csak bibliográfia + szerkezet + rövid mondatok)
# ---------------------------------------------------------------------------

_KEEP_SENTENCE_DEFAULT = (
    re.compile(r"\binclud(?:ed|ing)\b|\bcriteria\b|\bwere\s+eligible\b", re.I),
    re.compile(r"(?=.*(?:19|20)\d{2})(?:.*\bsearch\w*|.*\bdatabases?\b|.*\binception\b|.*\b(?:medline|embase|pubmed|"
               r"cinahl|psycinfo|scopus|web of science|central)\b)", re.I | re.S),
    re.compile(r"\bdate of (?:the )?(?:last )?search\b|\blast search", re.I),
)
_NUMERIC_CELL = re.compile(r"^[\s\d.,±/()\[\]%;:<>=+\-‐-―−·NRA*†‡§¶a-dto]{0,40}$")


def _sentence_spans(text):
    spans = []
    start = 0
    for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z(\[])", text):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return spans


def _trim_paragraph(p, keep_rx, max_sentence=300):
    """A bekezdés helyett csak a minta-illeszkedő, ≤ max_sentence hosszú mondatok (xref-ekkel)."""
    tokens = []  # (text, xref_el_or_None)

    def walk(el):
        if el.text:
            tokens.append((el.text, None))
        for ch in el:
            t = _local(ch.tag)
            if t == "xref":
                tokens.append(("".join(ch.itertext()), ch))
            elif t in ("table-wrap", "fig", "list", "disp-formula", "supplementary-material"):
                tokens.append((" ", None))
            else:
                walk(ch)
            if ch.tail:
                tokens.append((ch.tail, None))

    walk(p)
    flat = []
    pos = 0
    for txt, x in tokens:
        txt = _WS.sub(" ", txt)
        flat.append((pos, pos + len(txt), txt, x))
        pos += len(txt)
    full = "".join(t for _, _, t, _ in flat)
    keep = []
    for a, b in _sentence_spans(full):
        sent = full[a:b].strip()
        if 0 < len(sent) <= max_sentence and any((rx(sent) if callable(rx) else rx.search(sent)) for rx in keep_rx):
            keep.append((a, b))
    new = ET.Element(p.tag, {k: v for k, v in p.attrib.items() if k == "id"})
    if not keep:
        return None
    last = None
    for ki, (a, b) in enumerate(keep):
        if ki:
            if last is None:
                new.text = (new.text or "") + " "
            else:
                last.tail = (last.tail or "") + " "
        for s, e, txt, x in flat:
            if e <= a or s >= b:
                continue
            if x is not None:
                xe = ET.SubElement(new, x.tag, {k: v for k, v in x.attrib.items() if k in ("rid", "ref-type")})
                xe.text = txt
                last = xe
            else:
                piece = txt[max(0, a - s):max(0, min(len(txt), b - s))]
                if last is None:
                    new.text = (new.text or "") + piece
                else:
                    last.tail = (last.tail or "") + piece
    return new


def _trim_cell(cell, first_col, keep_all):
    if keep_all or first_col:
        for ch in list(cell):
            if _local(ch.tag) in ("graphic", "inline-graphic", "list"):
                cell.remove(ch)
        return
    txt = normalize_ws("".join(cell.itertext()))
    if txt and _NUMERIC_CELL.match(txt) and len(txt) <= 40:
        attrs = dict(cell.attrib)
        sups = [normalize_ws("".join(s.itertext())) for s in _iter_local(cell, "sup")]
        cell.clear()
        cell.attrib.update(attrs)
        if sups and txt.endswith(sups[-1]) and len(sups[-1]) <= 3:
            cell.text = txt[:len(txt) - len(sups[-1])]
            s = ET.SubElement(cell, "sup")
            s.text = sups[-1]
        else:
            cell.text = txt
        return
    attrs = dict(cell.attrib)
    cell.clear()
    cell.attrib.update(attrs)
    if txt:
        cell.text = "…"


def trim_for_fixture(source, keep_patterns=None, max_additional_refs=5, source_note=None, max_authors=3,
                     drop_uncited_refs=False):
    """Valós JATS → kis, jogtiszta teszt-fixture (str).

    Megtartja: article-meta azonosítók, cím, folyóirat, év, licenc-hivatkozás; szakaszcímek; a mintákra
    illeszkedő rövid (≤ 300 karakter) mondatok xref-ekkel; táblázatok szerkezete (fejléc, első oszlop, rövid
    számcellák; a többi cella '…'; vizsgálat-sor nélküli táblából csak a fejléc és az első sor); ábra-címkék; az
    összes hivatkozás (bibliográfiai adat; hivatkozásonként az első ``max_authors`` szerző + 'et al.') — a Cochrane
    „Additional references" szakaszból csak ``max_additional_refs`` darab. Eldobja: absztrakt-, bekezdés- és
    cellaszöveg, függelékek, köszönetnyilvánítás, kiegészítő anyag, google-scholar linkek, formázási attribútumok.
    ``keep_patterns``: regexek vagy egyargumentumú függvények (mondat → bool). ``drop_uncited_refs``: a vágás után
    sehonnan nem hivatkozott (és nem Cochrane-szakaszbeli) hivatkozások is kimaradnak — a hívó ellenőrizze, hogy
    a kinyerés eredménye nem változott."""
    keep_rx = tuple(keep_patterns) if keep_patterns else _KEEP_SENTENCE_DEFAULT
    text = _prepare(_decode(source))
    root = ET.fromstring(text)
    if _local(root.tag) != "article":
        for el in root.iter():
            if _local(el.tag) == "article":
                root = el
                break

    def drop_children(el, tags):
        for ch in list(el):
            if _local(ch.tag) in tags:
                el.remove(ch)

    drop_children(root, ("processing-meta", "sub-article", "floats-group"))
    front = _find(root, ["front"])
    if front is not None:
        am = _find(front, ["article-meta"])
        jm = _find(front, ["journal-meta"])
        for ch in list(front):
            if ch is not am and ch is not jm:
                front.remove(ch)
        if jm is not None:
            for ch in list(jm):
                if _local(ch.tag) not in ("journal-id", "journal-title-group", "issn"):
                    jm.remove(ch)
        if am is not None:
            for ch in list(am):
                t = _local(ch.tag)
                if t in ("article-id", "title-group", "pub-date", "volume", "issue", "elocation-id", "fpage",
                         "lpage"):
                    continue
                if t == "permissions":
                    for pc in list(ch):
                        if _local(pc.tag) == "license":
                            for lc in list(pc):
                                pc.remove(lc)
                            pc.text = None
                        elif _local(pc.tag) not in ("copyright-year",):
                            ch.remove(pc)
                    continue
                if t == "abstract":
                    _trim_container(ch, keep_rx)
                    continue
                am.remove(ch)
            tg = _find(am, ["title-group"])
            if tg is not None:
                for ch in list(tg):
                    if _local(ch.tag) != "article-title":
                        tg.remove(ch)
    for part in ("body", "back"):
        el = _find(root, [part])
        if el is not None:
            _trim_container(el, keep_rx, max_additional_refs=max_additional_refs)
    _compact_tree(root, max_authors)
    if drop_uncited_refs:
        _drop_uncited_refs(root)
    out = ET.tostring(root, encoding="unicode")
    note = source_note or "trimmed JATS fixture"
    note = note.replace("--", "-")
    return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!-- %s -->\n%s\n" % (note, out)


_DROP_ATTRS = frozenset(["style", "{http://www.w3.org/XML/1998/namespace}lang", "name-style", "valign", "align",
                         "frame", "rules", "border", "orientation", "position", "content-type", "specific-use"])


def _compact_tree(root, max_authors=3):
    """Fixture-tömörítés: formázási attribútumok és oszlopdefiníciók le, szóközök összevonva, hivatkozásonként az
    első ``max_authors`` szerző marad (a többi helyén 'et al.')."""
    for parent in root.iter():
        for ch in list(parent):
            if _local(ch.tag) in ("col", "colgroup"):
                parent.remove(ch)
    for r in [x for x in root.iter() if _local(x.tag) == "ref"]:
        for holder in [r] + list(r.iter()):
            kids = [k for k in holder if _local(k.tag) in ("name", "string-name")]
            if max_authors and len(kids) > max_authors:
                for k in kids[max_authors:]:
                    holder.remove(k)
                kids[max_authors - 1].tail = ", et al. "
    for el in root.iter():
        for a in list(el.attrib):
            if a in _DROP_ATTRS:
                del el.attrib[a]
        if el.text:
            el.text = _WS.sub(" ", el.text)
        if el.tail:
            el.tail = _WS.sub(" ", el.tail)


def _drop_uncited_refs(root):
    cited = set()
    for x in root.iter():
        if _local(x.tag) == "xref":
            cited.update((x.get("rid") or "").split())
    # a hivatkozott tartományok ('[24–28]') közbülső tagjai is kellenek: a két végpont közti összes hivatkozás
    order = [r.get("id") for r in root.iter() if _local(r.tag) == "ref"]
    pos = {rid: i for i, rid in enumerate(order)}
    hit = sorted(pos[r] for r in cited if r in pos)
    keep = set(cited)
    for a, b in zip(hit, hit[1:]):
        if b - a <= 60:
            keep.update(order[a:b + 1])

    def walk(el, protected):
        for ch in list(el):
            tag = _local(ch.tag)
            prot = protected
            if tag in ("ref-list", "sec"):
                title = None
                for x in ch:
                    if _local(x.tag) == "title":
                        title = _render(x)
                kind = classify_ref_section(title) if title else None
                if kind in ("included", "excluded", "awaiting", "ongoing"):
                    prot = True
            if tag == "ref":
                if not prot and ch.get("id") not in keep:
                    el.remove(ch)
                continue
            walk(ch, prot)

    walk(root, False)


def _trim_container(el, keep_rx, max_additional_refs=5, depth=0, refkind=None):
    if depth > MAX_DEPTH:
        return
    for ch in list(el):
        t = _local(ch.tag)
        if t in ("app-group", "app", "ack", "supplementary-material", "fn-group", "glossary", "graphic",
                 "media", "inline-graphic", "disp-formula", "list", "def-list", "boxed-text", "statement"):
            el.remove(ch)
            continue
        if t == "notes" and (ch.get("notes-type") or "") not in ("data-and-analyses", "study-characteristics"):
            # a jegyzetek közül csak a táblázatos (elemzések, vizsgálat-jellemzők) marad
            has_table = any(_local(x.tag) == "table-wrap" for x in ch.iter())
            if not has_table:
                el.remove(ch)
                continue
        if t == "ref-list" or (t == "sec" and (ch.get("sec-type") or "") == "ref-list"):
            title = None
            for x in ch:
                if _local(x.tag) == "title":
                    title = _render(x)
            kind = classify_ref_section(title) if title else None
            sub_kind = kind or refkind
            if sub_kind == "additional":
                _limit_refs(ch, max_additional_refs)
            for r in ch.iter():
                if _local(r.tag) == "ext-link" and (r.get("ext-link-type") or "") == "google-scholar":
                    pass
            _strip_scholar_links(ch)
            _trim_container(ch, keep_rx, max_additional_refs, depth + 1, sub_kind)
            continue
        if t == "ref":
            continue
        if t == "p":
            has_table = any(_local(x.tag) == "table-wrap" for x in ch.iter())
            if has_table:
                for tw in [x for x in ch.iter() if _local(x.tag) == "table-wrap"]:
                    _trim_table(tw)
                tws = [x for x in ch.iter() if _local(x.tag) == "table-wrap"]
                ch.clear()
                ch.text = None
                for tw in tws:
                    ch.append(tw)
                    tw.tail = None
                continue
            newp = _trim_paragraph(ch, keep_rx)
            idx = list(el).index(ch)
            el.remove(ch)
            if newp is not None:
                el.insert(idx, newp)
            continue
        if t == "table-wrap":
            _trim_table(ch)
            continue
        if t == "fig":
            for x in list(ch):
                if _local(x.tag) not in ("label", "caption"):
                    ch.remove(x)
                elif _local(x.tag) == "caption":
                    cap = _render(x)
                    x.clear()
                    tt = ET.SubElement(x, "title")
                    tt.text = cap[:150]
            continue
        _trim_container(ch, keep_rx, max_additional_refs, depth + 1, refkind)


def _strip_scholar_links(el):
    for parent in el.iter():
        for ch in list(parent):
            if _local(ch.tag) == "ext-link" and (ch.get("ext-link-type") or "") == "google-scholar":
                tail = ch.tail
                parent.remove(ch)
                if tail:
                    kids = list(parent)
                    if kids:
                        kids[-1].tail = (kids[-1].tail or "") + tail
                    else:
                        parent.text = (parent.text or "") + tail


def _limit_refs(el, n):
    """Az 'Additional references' szakaszban csak az első n hivatkozás (és csoport) marad."""
    count = 0
    for ch in list(el):
        t = _local(ch.tag)
        if t in ("ref", "ref-list", "sec"):
            has_ref = t == "ref" or any(_local(x.tag) == "ref" for x in ch.iter())
            if not has_ref:
                continue
            count += 1
            if count > n:
                el.remove(ch)


def _trim_table(tw):
    for ch in list(tw):
        t = _local(ch.tag)
        if t in ("graphic", "alternatives", "table-wrap-foot"):
            if t == "alternatives":
                tbl = None
                for a in ch:
                    if _local(a.tag) == "table":
                        tbl = a
                if tbl is not None:
                    tw.append(tbl)
            tw.remove(ch)
        elif t == "caption":
            cap_title = None
            for x in ch:
                if _local(x.tag) == "title":
                    cap_title = x
            if cap_title is None:
                txt = _render(ch)
                for x in list(ch):
                    ch.remove(x)
                ch.text = None
                tt = ET.SubElement(ch, "title")
                tt.text = txt[:200]
            else:
                for x in list(ch):
                    if x is not cap_title:
                        ch.remove(x)
    for tbl in [x for x in tw.iter() if _local(x.tag) == "table"]:
        rows = [x for x in tbl.iter() if _local(x.tag) == "tr"]
        thead_rows = set()
        for sec in tbl:
            if _local(sec.tag) == "thead":
                for tr in sec:
                    thead_rows.add(id(tr))
        # vizsgálat-sorok nélküli tábla (pl. összefoglaló, 'Summary of findings'): a fejléc és az első sor marad
        relevant = False
        body_rows = [tr for tr in rows if id(tr) not in thead_rows]
        for tr in body_rows:
            cells = [c for c in tr if _local(c.tag) in ("td", "th")]
            if not cells:
                continue
            first = cells[0]
            if any(_local(x.tag) == "xref" and (x.get("ref-type") or "") not in _NON_REF_XREF for x in first.iter()) \
                    or parse_author_year(normalize_ws("".join(first.itertext()))):
                relevant = True
                break
        if not relevant:
            kept = 0
            for parent in tbl.iter():
                for tr in [x for x in parent if _local(x.tag) == "tr" and id(x) not in thead_rows]:
                    kept += 1
                    if kept > 1:
                        parent.remove(tr)
            rows = [x for x in tbl.iter() if _local(x.tag) == "tr"]
        for ri, tr in enumerate(rows):
            cells = [c for c in tr if _local(c.tag) in ("td", "th")]
            is_head = id(tr) in thead_rows or all(
                _local(c.tag) == "th" or any(_local(b.tag) == "bold" for b in c.iter()) for c in cells if
                normalize_ws("".join(c.itertext())))
            for ci, c in enumerate(cells):
                _trim_cell(c, ci == 0, is_head and ri < 3)


if __name__ == "__main__":  # pragma: no cover - fejlesztői segéd
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="JATS-segéd (fejlesztői): összegzés vagy fixture-vágás.")
    ap.add_argument("path")
    ap.add_argument("--trim", action="store_true", help="fixture-vágás a szabványos kimenetre")
    ap.add_argument("--note", default=None)
    a = ap.parse_args()
    with open(a.path, "rb") as fh:
        data = fh.read()
    if a.trim:
        sys.stdout.write(trim_for_fixture(data, source_note=a.note))
    else:
        d = parse(data)
        json.dump(d.summary(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
