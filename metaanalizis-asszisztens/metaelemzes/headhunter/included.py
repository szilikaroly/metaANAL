# -*- coding: utf-8 -*-
"""A bevont vizsgálatok kinyerése egy forrás-áttekintésből (TERV_metaheadhunter.md 6.1–6.4) — bizonyítékkal.

Bemenet: egy ``jats.parse()``-szal beolvasott dokumentum (memóriában). Kimenet: a ``reviews/<id>.json``
(``szk.ma.headhunter.review/v1``) ``candidates[]`` és ``evidence[]`` tömbjébe illeszthető tételek, valamint a
közölt vizsgálatszám (``k_reported``) és a keresési dátum (``search_date``) bizonyítékkal, ha megtalálható.

Stratégiák megbízhatósági sorrendben (6.1):

- **a1** Cochrane-szakaszok — „References to studies included in this review" al-listái: minden közlemény egy
  jelölt (``group_key`` = a vizsgálat al-listája), ``high``, ``status: confirmed`` (N3 b — felülbírálható);
  kizárt → ``excluded_by_review[]`` (az ok a „Characteristics of excluded studies" táblából, ha van);
  besorolásra váró / folyamatban lévő → ``awaiting`` / ``ongoing`` szerepű jelölt; a „Characteristics of
  included studies" vizsgálatonkénti táblái megerősítő bizonyítékok.
- **a2** a bevont vizsgálatok táblázata — soronként: ``<xref ref-type="bibr">`` → ``high``; különben első
  szerző + év illesztése az irodalomjegyzékre (KIZÁRÓLAG első szerzőre: a PMC4122754 „Adetifa 2010" csapdája):
  egyedi → ``medium``, több → ``low``, nincs → ``low``; a „2.1/2.2", ``rowspan`` és ismétlődő címke-sorok egy
  jelöltet adnak (a lokátor a sorok listája).
- **a3** elemzési/forest-adattáblák — a vizsgálat neve megerősít (``medium``); az egyértelmű fejlécű
  számcellák (Events, Total, Mean, SD, N, hatásméret [CI]) **másodlagos adatként** (``secondary_value``,
  ``status: unverified``) rögzülnek cellánkénti lokátorral. Kétértelmű fejlécnél NEM rögzítünk számot (N1).
- **a4** szöveges állítás („We included N studies [12–24]") — a darabszám és az xref-tartomány megerősít;
  önmagában csak akkor ad jelöltet, ha nincs a1/a2/a3.
- **tartalék (6.2 b)** — az irodalomjegyzék minden tétele ``role_in_review: unknown``, ``low``, ``proposed``,
  ``needs_review: true`` (ezek NEM bevonás-állítások; az ágens/ember osztályozza őket). Ugyanez API-ból kapott
  hivatkozáslistára: ``candidates_from_reference_records``.

Nem alku tárgya (N1–N4): azonosító csak a dokumentumból (``source: review``, ``via: jats.*``) — L4-ben API-val
megerősítendő; szám csak idézve (nem számolunk, nem becsülünk); idézet ≤ 300 karakter, szó szerint
(táblázatnál a cellák szövege ' | ' elválasztóval); teljes szöveg nem kerül a kimenetbe.

Nyilvános API::

    result = extract_included(doc, "rv-pmid-31038197", at="2026-10-05T10:13:02Z")
    result["candidates"], result["evidence"], result["excluded_by_review"]
    result["k_reported"], result["search_date"], result["k_statements"], result["search_date_statements"]
    result["strategies"], result["n_study_groups"], result["warnings"], result["stats"]
    review = merge_into_review(review, result)            # idempotens beillesztés a review-dokumentumba
    candidates_from_reference_records(review_id, records, source="europepmc")   # API-irodalomjegyzékből
    inclusion_statements(doc), search_date_statements(doc), classify_table(table), classify_column(parts)

A jelöltek a sémán túl (additív, a séma megengedi) ezeket a mezőket is viszik: ``extract_key`` (újrafuttatás-
stabil kulcs), ``ref_id``, ``needs_review`` + ``review_reasons`` (EP2-sor), ``ref_hint`` (bizonytalan
illesztésnél a lehetséges hivatkozás — azonosító NÉLKÜL), ``study_registry_in_review``, ``context``.
"""
import copy
import datetime
import difflib
import json
import re

from . import jats as _jats
from .jats import normalize_ws, norm_name, norm_text, parse_author_year

__all__ = [
    "extract_included", "merge_into_review", "candidates_from_reference_records", "inclusion_statements",
    "search_date_statements", "classify_table", "classify_column", "parse_cell_values", "QUOTE_MAX",
    "TOOL_ACTOR",
]

QUOTE_MAX = 300
TEXT_MAX = 300
TOOL_ACTOR = "tool:headhunter"
_CELL_SEP = " | "


# ---------------------------------------------------------------------------
# kis segédek
# ---------------------------------------------------------------------------

def _now():
    return datetime.datetime.utcnow().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip(s, n=QUOTE_MAX):
    """Szó szerinti levágás (≤ n karakter, szóhatáron, három pont NÉLKÜL — az idézet a forrás része marad)."""
    s = normalize_ws(s)
    if len(s) <= n:
        return s
    cut = s[:n]
    sp = cut.rfind(" ")
    if sp >= int(n * 0.6):
        cut = cut[:sp]
    return cut.rstrip()


def _idval(value, via, at, source="review", kind=None):
    d = {"value": value, "source": source, "via": via, "at": at}
    if kind:
        d["type"] = kind
    return d


def _ids_from_ref(ref, at):
    ids = {}
    for k in ("pmid", "doi", "pmcid", "nct"):
        if k in ref.ids and ref.ids[k][0]:
            ids[k] = _idval(ref.ids[k][0], ref.ids[k][1], at)
    if ref.registry:
        ids["registry"] = [_idval(v, via, at, kind=kind) for kind, v, via in ref.registry]
    return ids


def _cited_from_ref(ref):
    text = ref.text or (ref.title or "") or ref.ref_id
    return {
        "text": _clip(text, TEXT_MAX) or ref.ref_id,
        "first_author": ref.first_author_display or ref.first_author,
        "year": ref.year,
        "title": _clip(ref.title, TEXT_MAX) if ref.title else None,
        "journal": _clip(ref.journal, 200) if ref.journal else None,
    }


def _label_key(surname, year, suffix=None):
    return "%s|%s|%s" % (norm_name(surname), year or "", (suffix or "").lower())


def _section_title(path):
    return path[-1] if path else None


# ---------------------------------------------------------------------------
# építő
# ---------------------------------------------------------------------------

class _Builder(object):
    def __init__(self, doc, review_id, at, actor):
        self.doc = doc
        self.review_id = review_id
        self.at = at
        self.actor = actor
        self.container = doc.container if doc is not None else None
        self.cands = []
        self.by_key = {}
        self.by_label = {}
        self.evidence = []
        self.excluded = []
        self.warnings = []
        self.strategies = []
        self.stats = {"tables_included": [], "tables_data": [], "rows_skipped": 0, "secondary_values": 0}

    # -- bizonyíték ----------------------------------------------------------
    def ev(self, kind, strategy, locator, quote, confidence):
        q = _clip(quote, QUOTE_MAX)
        if not q:
            q = _clip(locator.get("label") or locator.get("element_id") or locator.get("ref_id") or "?")
        loc = {"container": self.container}
        loc.update({k: v for k, v in locator.items() if v is not None})
        e = {
            "evidence_id": None,
            "review_id": self.review_id,
            "kind": kind,
            "strategy": strategy,
            "locator": loc,
            "quote": q,
            "extracted_by": self.actor,
            "at": self.at,
            "confidence": confidence,
        }
        self.evidence.append(e)
        e["_n"] = len(self.evidence)
        return e

    # -- jelölt --------------------------------------------------------------
    def get(self, key):
        return self.by_key.get(key)

    def add(self, key, cand):
        cand["extract_key"] = key
        self.cands.append(cand)
        self.by_key[key] = cand
        self.index_label(cand)
        return cand

    def index_label(self, cand):
        """Címke-index ('Felszín 2015' → jelöltek) a további táblák soraihoz (pl. RoB-tábla)."""
        lab = cand.get("study_label_in_review")
        pay = parse_author_year(lab) if lab else None
        if pay:
            lst = self.by_label.setdefault(_label_key(pay["surname"], pay["year"], pay.get("suffix")), [])
            if cand not in lst:
                lst.append(cand)

    def attach(self, cand, e):
        if e not in cand["_ev"]:
            cand["_ev"].append(e)

    def warn(self, code, hu, en, **detail):
        w = {"code": code, "hu": hu, "en": en}
        if detail:
            w["detail"] = detail
        self.warnings.append(w)

    def use(self, strategy):
        if strategy not in self.strategies:
            self.strategies.append(strategy)


def _new_cand(cited_as, label, group_key, ids, role, confidence, status, ref_id=None):
    return {
        "cand_id": None,
        "cited_as": cited_as,
        "study_label_in_review": label,
        "group_key": group_key,
        "ids": ids or {},
        "rec_id": None,
        "role_in_review": role,
        "evidence_ids": [],
        "confidence": confidence,
        "status": status,
        "secondary_data": [],
        "decision_ids": [],
        "ref_id": ref_id,
        "needs_review": False,
        "review_reasons": [],
        "_ev": [],
        "_sec": [],
    }


_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def _raise_conf(cand, conf):
    if _CONF_RANK[conf] > _CONF_RANK[cand["confidence"]]:
        cand["confidence"] = conf


def _reason(cand, code):
    if code not in cand["review_reasons"]:
        cand["review_reasons"].append(code)


# ---------------------------------------------------------------------------
# a1 — Cochrane-szakaszok
# ---------------------------------------------------------------------------

def _pool_refs(doc):
    """Az első-szerző + év illesztés jelöltkészlete: a Cochrane 'Additional references' / 'other versions'
    szakaszok és az áttekintés saját hivatkozása nélkül."""
    own = doc.meta.get("ids", {}) if doc.meta else {}
    out = []
    for rid in doc.ref_order:
        r = doc.refs[rid]
        if r.section in ("additional", "other_versions"):
            continue
        if own.get("pmid") and r.ids.get("pmid", (None,))[0] == own.get("pmid"):
            continue
        if own.get("doi") and r.ids.get("doi", (None,))[0] == own.get("doi"):
            continue
        out.append(r)
    return out


def _a1_cochrane(b):
    doc = b.doc
    sections = [s for s in doc.ref_sections if s.kind in ("included", "excluded", "awaiting", "ongoing")]
    if not any(s.kind == "included" for s in sections):
        return False
    b.use("jats_cochrane_included")
    role_of = {"included": "included", "awaiting": "awaiting", "ongoing": "ongoing"}
    excl_reasons = _cochrane_excluded_reasons(doc)
    for s in sections:
        units = [(gid, doc.groups[gid].ref_ids) for gid in s.group_ids if gid in doc.groups]
        grouped = set(r for _, rr in units for r in rr)
        loose = [r for r in s.ref_ids if r not in grouped]
        units.extend((None, [r]) for r in loose)
        for gid, rids in units:
            g = doc.groups.get(gid) if gid else None
            if s.kind == "excluded":
                for rid in rids:
                    ref = doc.refs[rid]
                    e = b.ev("reference_section", "jats_cochrane_included",
                             {"element_id": gid or rid, "ref_id": rid, "section": s.title,
                              "label": g.label if g else None},
                             g.title if g else ref.text, "high")
                    reason = excl_reasons.get(gid) if gid else None
                    rev = None
                    if reason:
                        if reason.get("evidence") is None:
                            reason["evidence"] = b.ev(
                                "table_row", "jats_cochrane_included",
                                {"element_id": reason["table"], "label": reason["table_label"],
                                 "section": reason["section"], "row": reason["row"],
                                 "column": reason["column"]},
                                reason["label"] + _CELL_SEP + reason["text"], "high")
                        rev = reason["evidence"]
                    b.excluded.append({
                        "cited_as": _clip(ref.text or ref.ref_id, TEXT_MAX),
                        "reason_quote": _clip(reason["text"], QUOTE_MAX) if reason else None,
                        "evidence_id": e,
                        "ids": _ids_from_ref(ref, b.at),
                        "study_label": g.label if g else None,
                        "group_key": gid,
                        "reason_evidence_id": rev,
                    })
                continue
            primary = [r for r in rids if doc.refs[r].primary_marked]
            study_reg = _registry_from_label(g.title if g else "", b.at)
            for rid in rids:
                ref = doc.refs[rid]
                role = role_of[s.kind]
                if s.kind == "included" and primary and rid not in primary:
                    role = "included_companion"
                label = g.label if g else _ref_label(ref)
                key = "ref:%s" % rid
                cand = b.get(key)
                if cand is None:
                    cand = b.add(key, _new_cand(_cited_from_ref(ref), label, gid or "ref:%s" % rid,
                                                _ids_from_ref(ref, b.at), role, "high",
                                                "confirmed" if s.kind == "included" else "proposed", ref_id=rid))
                    if primary:
                        cand["primary_marked"] = rid in primary
                    if study_reg:
                        cand["study_registry_in_review"] = study_reg
                e = b.ev("reference_section", "jats_cochrane_included",
                         {"element_id": gid or rid, "ref_id": rid, "section": s.title, "label": label},
                         g.title if g else ref.text, "high")
                b.attach(cand, e)
    # a „Characteristics of included/ongoing studies" vizsgálatonkénti táblái: megerősítés
    for t in doc.tables:
        gid = _caption_group(doc, t)
        if gid is None:
            continue
        g = doc.groups[gid]
        if g.section not in ("included", "ongoing", "awaiting"):
            continue
        for rid in g.ref_ids:
            cand = b.get("ref:%s" % rid)
            if cand is None:
                continue
            e = b.ev("table_row", "jats_cochrane_included",
                     {"element_id": t.element_id, "label": t.caption or g.label, "ref_id": rid,
                      "section": _section_title(t.section_path)},
                     t.caption or g.label, "high")
            b.attach(cand, e)
    return True


def _registry_from_label(title, at):
    out = []
    for kind, v in _jats.registry_ids_in_text(title or ""):
        out.append(_idval(v, "jats.group-title", at, kind=kind))
    return out


def _ref_label(ref):
    if ref.first_author and ref.year:
        return "%s %d%s" % (ref.first_author, ref.year, ref.year_suffix or "")
    return ref.first_author or None


def _caption_group(doc, t):
    """Cochrane vizsgálatonkénti tábla: a felirat egyetlen xref egy csoportra (pl. 'Andrews 2017')."""
    if not t.caption_xrefs:
        return None
    gids = [x["rid"] for x in t.caption_xrefs if x["rid"] in doc.groups]
    if len(gids) != 1:
        return None
    if normalize_ws(t.caption) != normalize_ws(t.caption_xrefs[0]["text"]):
        return None
    return gids[0]


def _cochrane_excluded_reasons(doc):
    """„Characteristics of excluded studies": Study | Reason for exclusion → {csoport-id: {'text', 'evidence'}}."""
    out = {}
    for t in doc.tables:
        if not t.columns or len(t.columns) < 2:
            continue
        if not re.search(r"reason", norm_text(t.columns[1])):
            continue
        sec = norm_text(" ".join(t.section_path))
        if "excluded" not in sec and "excluded" not in norm_text(t.caption):
            continue
        for ri, row in enumerate(t.body):
            gids = [x["rid"] for x in row[0].xrefs if x["rid"] in doc.groups]
            if not gids or not row[1].text:
                continue
            out[gids[0]] = {"text": row[1].text, "evidence": None, "table": t.element_id, "row": ri + 1,
                            "label": row[0].text, "table_label": t.display_label or _section_title(t.section_path),
                            "section": _section_title(t.section_path), "column": t.columns[1]}
    return out


# ---------------------------------------------------------------------------
# táblázat-osztályozás (a2/a3)
# ---------------------------------------------------------------------------

_INCLUDED_CAPTION = re.compile(
    r"characteristics of (the )?(included|eligible|selected) (studies|trials|rcts|articles)|"
    r"(studies|trials|rcts|articles|papers) included|included (studies|trials|rcts|articles)|"
    r"study characteristics|characteristics of (the )?(individual )?(studies|trials|rcts)|"
    r"characteristics of (each|the individual) (study|trial)|"
    r"summary of (the )?(included |eligible )?(studies|trials|rcts)|"
    r"description of (the )?(included )?(studies|trials)|"
    r"(studies|trials|rcts) (that met|meeting|fulfilling) (the )?(inclusion|eligibility) criteria|"
    r"eligible (studies|trials|rcts)|"
    r"(quality|risk of bias) (assessment|appraisal|evaluation)? ?of (the )?included (studies|trials|rcts)")
_NOT_INCLUDED_CAPTION = re.compile(r"excluded|ongoing|awaiting|search strateg|summary of findings|subgroup|"
                                   r"meta-?regression|sensitivity")
_STUDY_TOKENS = frozenset(["study", "studies", "id", "name", "names", "author", "authors", "first", "year", "years",
                           "publication", "published", "ref", "reference", "references", "trial", "trials", "code",
                           "country", "countries", "citation", "source", "no", "and", "s", "et", "al", "acronym",
                           "label", "identifier", "date", "of", "the", "included", "rct", "rcts", "location",
                           "pub", "publ", "sample", "size", "n", "y", "yr"])
_STUDY_CORE = frozenset(["study", "studies", "author", "authors", "reference", "references", "ref", "trial", "trials",
                         "code", "citation", "source", "rct", "rcts", "identifier", "acronym"])


class _StudyCol(object):
    """Tanulmány-címke oszlopfejléc: csak címke-jellegű szavakból áll ('Code Author (year) (country)',
    'Author name, publication year', 'Study ID') — a 'Study period' / 'Study design' NEM az."""

    @staticmethod
    def match(h):
        toks = (h or "").split()
        if not toks or not set(toks) & _STUDY_CORE:
            return False
        return all(t in _STUDY_TOKENS or re.fullmatch(r"\d{1,2}", t) for t in toks)


_STUDY_COL = _StudyCol()
_REF_COL = re.compile(r"^(ref|refs|reference|references|citation|source)$")
_CODE_COL = re.compile(r"^(no|n|#|code|id|number|nr|study no|study number)$")


def _col_norm(s):
    s = re.sub(r"[*†‡§¶#∗Δ]", " ", s or "")
    return norm_text(s)


def classify_table(t, doc=None):
    """Egy ``jats.Table`` → {'kind': 'included'|'data'|'cochrane_study'|'other', 'label_col', 'code_col',
    'ref_cols', 'columns': {c: classify_column(...)}, 'reason': str}."""
    info = {"kind": "other", "label_col": 0, "code_col": None, "ref_cols": [], "columns": {}, "reason": None}
    if doc is not None and _caption_group(doc, t) is not None:
        info["kind"] = "cochrane_study"
        info["reason"] = "caption-xref"
        return info
    if not t.body or t.ncols < 1:
        info["reason"] = "empty"
        return info
    heads = [_col_norm(c) for c in t.columns] if t.columns else [""] * t.ncols
    # címke-oszlop: az első tanulmány-szerű fejlécű oszlop (a sorszám-oszlop után)
    label_col = 0
    for ci, h in enumerate(heads[:3]):
        if h and _STUDY_COL.match(h) and not _CODE_COL.match(h):
            label_col = ci
            break
    if label_col > 0 and _CODE_COL.match(heads[label_col - 1] or "x"):
        info["code_col"] = label_col - 1
    info["label_col"] = label_col
    info["ref_cols"] = [ci for ci, h in enumerate(heads) if ci != label_col and _REF_COL.match(h or "-")]
    info["year_col"] = None
    for ci, h in enumerate(heads[:6]):
        if ci != label_col and re.fullmatch(r"(publication |pub )?years?( of publication)?|date", h or "-"):
            info["year_col"] = ci
            break
    for ci in range(t.ncols):
        if ci == label_col or ci == info["code_col"] or ci == info["year_col"]:
            continue
        cc = classify_column(t.column_paths[ci] if ci < len(t.column_paths) else [])
        if cc is not None:
            info["columns"][ci] = cc
    _resolve_arms(info["columns"])
    cap = norm_text("%s %s" % (t.label or "", t.caption or ""))
    label_head = heads[label_col] if label_col < len(heads) else ""
    head_ok = (not label_head) or bool(_STUDY_COL.match(label_head))
    parsed_rows = 0
    for row in t.body:
        cell = row[label_col] if label_col < len(row) else None
        if cell is None or (cell.spanned and cell.origin[0] != cell.row):
            continue
        if _row_xref_targets(doc, row, label_col, info["ref_cols"]) or parse_author_year(_label_text(cell, row, info)):
            parsed_rows += 1
    info["parsed_rows"] = parsed_rows
    if _INCLUDED_CAPTION.search(cap) and not _NOT_INCLUDED_CAPTION.search(cap) and head_ok and parsed_rows >= 1:
        info["kind"] = "included"
        info["reason"] = "caption"
        return info
    numeric = [c for c in info["columns"].values() if c.get("field")]
    if numeric and parsed_rows >= 2 and head_ok:
        info["kind"] = "data"
        info["reason"] = "numeric-columns"
        return info
    if label_head and _STUDY_COL.match(label_head) and parsed_rows >= 3 and not _NOT_INCLUDED_CAPTION.search(cap):
        # vizsgálatonkénti tábla „bevont" felirat nélkül (pl. 'Summaries of subjects' inclusion criteria')
        info["kind"] = "study_list"
        info["reason"] = "study-column"
        return info
    info["reason"] = "no-match"
    return info


def _label_text(cell, row=None, info=None):
    """A vizsgálat-címke szövege hivatkozás-jelek és felső indexek nélkül ('Rosenstock, 2012¹³' → 'Rosenstock, 2012');
    ha a táblának külön 'Year' oszlopa van, annak értéke hozzáfűzve ('Consroe et al.' + '1991')."""
    if cell is None:
        return ""
    txt = cell.text_label or cell.text_nosup or cell.text
    if row is not None and info is not None and info.get("year_col") is not None and \
            info["year_col"] < len(row) and not _jats._YEAR_TOKEN.search(txt):
        y = row[info["year_col"]].text_nosup or ""
        m = _jats._YEAR_TOKEN.search(y)
        if m:
            txt = "%s %s" % (txt, m.group(0))
    return txt


def _row_xref_targets(doc, row, label_col, ref_cols, with_mode=False):
    """A sor hivatkozásai: <xref> (bibr/ref/ref-list/sec) a címke- és hivatkozás-oszlopban, valamint a címke
    számos felső indexe ('Almeida 2013<sup>11</sup>') a hivatkozás-címke alapján. ``with_mode``: [(ref_id, mód)],
    mód ∈ {'xref', 'sup_label', 'sup_id'}."""
    if doc is None:
        return []
    cells = [row[label_col]] + [row[c] for c in ref_cols if c < len(row)]
    out = []
    seen = set()
    for c in cells:
        if c is None:
            continue
        for rid in doc.bibr_refs(c.xrefs, c.text):
            if rid not in seen:
                seen.add(rid)
                out.append((rid, "xref"))
    if not out:
        lab = row[label_col] if label_col < len(row) else None
        for sup in (lab.sups if lab is not None else []):
            sup_n = _jats._fix_dashes(sup)
            if not re.fullmatch(r"\d{1,4}(?:\s*[-,]\s*\d{1,4})*", sup_n.strip()):
                continue
            nums = []
            for part in re.split(r"\s*,\s*", sup_n.strip()):
                if "-" in part:
                    lo, hi = [int(x) for x in part.split("-", 1)]
                    if 0 < hi - lo <= 50:
                        nums.extend(range(lo, hi + 1))
                elif part:
                    nums.append(int(part))
            for n in nums:
                rid, how = doc.refs_for_number(n)
                if rid and rid not in seen:
                    seen.add(rid)
                    out.append((rid, "sup_" + how))
    return out if with_mode else [r for r, _ in out]


# -- oszlopfejlécek (a3) --------------------------------------------------------

_ARM1 = re.compile(r"\b(experimental|experiment|intervention|interventions|treatment|treated|active|exposed|"
                   r"vaccinated|vaccine|cases|study group|group 1|arm 1|exp|int|tx)\b")
_ARM2 = re.compile(r"\b(control|controls|placebo|comparator|comparison|usual care|standard care|sham|"
                   r"unexposed|unvaccinated|group 2|arm 2|ctrl|ctl)\b")
_EFFECT_MEASURES = (
    ("SMD", re.compile(r"\b(std\.? mean difference|standardi[sz]ed mean difference|hedges'?\s?g|cohen'?s\s?d)\b",
                       re.I), re.compile(r"\bSMD\b")),
    ("MD", re.compile(r"\b(mean difference|weighted mean difference)\b", re.I), re.compile(r"\b(W?MD)\b")),
    ("RR", re.compile(r"\b(risk ratio|relative risk|rate ratio)\b", re.I), re.compile(r"\bRR\b")),
    ("OR", re.compile(r"\b(odds ratio)\b", re.I), re.compile(r"\bOR\b")),
    ("HR", re.compile(r"\b(hazard ratio)\b", re.I), re.compile(r"\bHR\b")),
    ("RD", re.compile(r"\b(risk difference)\b", re.I), re.compile(r"\bRD\b")),
)
_GENERIC = frozenset(["group", "groups", "arm", "arms", "n", "no", "of", "the", "value", "values", "number",
                      "total", "data", "per", "in", "all"])


def _measure_of(part):
    """Egy fejlécrész mértéke: 'e_n', 'm_sd', 'e', 'n', 'm', 'sd', 'effect' vagy None (+ maradék szavak)."""
    p = part
    rules = (
        ("e_n", r"^(?:(?:no|number) of )?(events?|cases|deaths|responders|n)\s*(?:/|of)\s*(total|n|participants|"
                r"patients|subjects)(?: %)?$|^n\s*/\s*n(?: %)?$|^n n(?: %)?$"),
        ("m_sd", r"^mean\s*(?:\(\s*sd\s*\)|sd|\+\s*sd|\+/-\s*sd|±\s*sd)$"),
        ("e", r"^(?:(?:no|number) of )?(events?|cases|deaths|responders|n events)$"),
        ("n", r"^(total|n|no|number|sample size|sample|participants|patients|subjects|randomi[sz]ed|analy[sz]ed|"
              r"n analy[sz]ed|n randomi[sz]ed|no of participants|number of participants|no of patients|"
              r"number of patients|total n|n total|total participants|no participants)$"),
        ("m", r"^(mean|means|mean value|average)$"),
        ("sd", r"^(sd|s d|std dev|std deviation|standard deviation)$"),
    )
    for name, rx in rules:
        if re.match(rx, p):
            return name, ""
    return None, p


def _norm_header(p):
    q = _jats._fix_dashes(p).replace("+/-", "±")
    q = re.sub(r"[*†‡§¶#∗Δ]", " ", q).lower()
    q = re.sub(r"[^\w±/%]+", " ", q).replace("_", " ")
    q = re.sub(r"\s*±\s*", " ± ", q)
    q = re.sub(r"\s+", " ", q).strip()
    q = q.replace("mean ± sd", "mean sd")
    return q


def _measure_with_arm(nrm):
    """Mérték egy fejlécrészben, akár karszóval együtt ('Intervention mean', 'Events (control)')."""
    measure, _ = _measure_of(nrm)
    if measure:
        return measure, None
    for idx, armrx in ((1, _ARM1), (2, _ARM2)):
        mm = armrx.search(nrm)
        if mm:
            stripped = (nrm[:mm.start()] + " " + nrm[mm.end():])
            stripped = re.sub(r"\b(group|arm)\b", " ", stripped)
            stripped = re.sub(r"\s+", " ", stripped).strip()
            measure, _ = _measure_of(stripped)
            if measure:
                return measure, idx
    return None, None


def classify_column(parts):
    """Fejléc-útvonal (felülről lefelé) → {'measure', 'field' (None, ha a kar nem dönthető el), 'arm_label',
    'arm_index', 'arm_candidate', 'effect_measure', 'outcome', 'ambiguous', 'header'} vagy None (nem számoszlop).

    A mértéket (Events, Total, N, Mean, SD, Mean (SD), n/N, hatásméret [CI]) a legalsó olyan fejlécrész adja,
    amely TISZTÁN mérték (pl. 'Age (mean ± SD)' nem az → nem rögzítünk számot); a kart (kísérleti/kontroll) a többi
    részből ismerjük fel; ami marad, az a kimenet ('CRP (mg/L)')."""
    if not parts:
        return None
    raw_parts = [normalize_ws(p) for p in parts if normalize_ws(p)]
    if not raw_parts:
        return None
    norm_parts = [_norm_header(p) for p in raw_parts]
    last_raw, last = raw_parts[-1], norm_parts[-1]
    # hatásméret-oszlop (a rövidítés csak nagybetűvel: az 'or' kötőszó NEM esélyhányados)
    for code, rx_long, rx_abbr in _EFFECT_MEASURES:
        if (rx_long.search(last_raw) or rx_abbr.search(last_raw)) and \
                not re.search(r"\bweight\b|\bp ?value\b|\bp\b$|heterogen|\bi2\b|\btau|\bno\b|\bnumber\b", last):
            return {"measure": "effect", "field": "effect", "effect_measure": code, "arm_label": None,
                    "arm_index": None, "arm_candidate": None, "outcome": " / ".join(raw_parts[:-1]) or None,
                    "ambiguous": False, "header": " / ".join(raw_parts)}
    # a mérték-rész: alulról az első tiszta mérték
    mi, measure, arm_in_measure = None, None, None
    for i in range(len(norm_parts) - 1, -1, -1):
        m, armi = _measure_with_arm(norm_parts[i])
        if m:
            mi, measure, arm_in_measure = i, m, armi
            break
    if measure is None:
        return None
    if mi < len(norm_parts) - 2:
        # a mérték alatt egynél több további fejlécszint → nem egyértelmű
        return None
    arm_label, arm_index, arm_candidate = None, None, None
    if arm_in_measure:
        arm_label, arm_index = raw_parts[mi], arm_in_measure
    others = [(i, raw_parts[i], norm_parts[i]) for i in range(len(raw_parts)) if i != mi]
    used = set()
    if arm_index is None:
        # felismerhető kar a mérték alatt, majd felett (a mértékhez legközelebbi)
        order = sorted(others, key=lambda x: (0 if x[0] > mi else 1, abs(x[0] - mi)))
        for i, raw, nrm in order:
            a1, a2 = bool(_ARM1.search(nrm)), bool(_ARM2.search(nrm))
            if a1 != a2:
                arm_label, arm_index = raw, (1 if a1 else 2)
                used.add(i)
                break
    if arm_index is None and others:
        # kar-jelölt: a mérték alatti rész, ennek hiányában a közvetlenül fölötte álló
        below = [x for x in others if x[0] > mi]
        cand = below[0] if below else [x for x in others if x[0] == mi - 1]
        if isinstance(cand, list):
            cand = cand[0] if cand else None
        if cand is not None:
            arm_candidate = cand[1]
            used.add(cand[0])
    outcome = " / ".join(raw for i, raw, _ in others if i not in used) or None
    field = None
    if measure == "n" and arm_index is None and arm_candidate is None:
        field = "n_total"
    elif arm_index in (1, 2):
        field = measure
    return {"measure": measure, "field": field, "effect_measure": None, "arm_label": arm_label,
            "arm_index": arm_index, "arm_candidate": arm_candidate, "outcome": outcome,
            "ambiguous": field is None, "header": " / ".join(raw_parts)}


def _resolve_arms(columns):
    """Kar-feloldás táblaszinten: ha a számoszlopok pontosan KÉT kar-címke alá tartoznak, és ezek közül legalább
    az egyik felismerhető (kísérleti/kontroll), a másik a komplementer (pl. 'MVA85A' ↔ 'Placebo'). Kettőnél több
    vagy felismerhetetlen kar → a mező kétértelmű marad (nem rögzítünk számot)."""
    arm_cols = [c for c in columns.values() if c.get("measure") not in (None, "effect") and
                (c.get("arm_index") in (1, 2) or c.get("arm_candidate"))]
    labels = []
    idx = {}
    for c in arm_cols:
        lab = c.get("arm_label") if c.get("arm_index") in (1, 2) else c.get("arm_candidate")
        if lab not in labels:
            labels.append(lab)
        if c.get("arm_index") in (1, 2):
            idx[lab] = c["arm_index"]
    for lab in labels:
        if lab in idx:
            continue
        nrm = norm_text(lab)
        if _ARM1.search(nrm) and not _ARM2.search(nrm):
            idx[lab] = 1
        elif _ARM2.search(nrm) and not _ARM1.search(nrm):
            idx[lab] = 2
    if len(labels) == 2 and len(idx) == 1:
        known_lab, known_i = list(idx.items())[0]
        other = labels[1] if known_lab == labels[0] else labels[0]
        idx[other] = 3 - known_i
    ok = len(labels) == 2 and len(idx) == 2 and set(idx.values()) == {1, 2}
    for c in arm_cols:
        if c.get("field") is not None:
            continue
        lab = c.get("arm_candidate")
        if ok and lab in idx:
            c["arm_label"] = lab
            c["arm_index"] = idx[lab]
            c["field"] = c["measure"]
            c["ambiguous"] = False
        elif c["measure"] == "n" and norm_text(lab or "") in ("total", "overall", "all"):
            c["field"] = "n_total"
            c["ambiguous"] = False
    if not ok:
        # félig felismert karok (pl. három kar) → a kar-függő mezők is kétértelműek
        for c in arm_cols:
            if c.get("field") in ("e", "n", "m", "sd") and len(labels) != 2:
                c["field"] = None
                c["ambiguous"] = True


# -- cellaértékek -----------------------------------------------------------------

_NUM = r"-?\d+(?:\.\d+)?"
_FOOTMARK = re.compile(r"[*†‡§¶#∗]+$")


def _num(s, allow_thousands=False):
    s = _jats._fix_dashes(normalize_ws(s)).strip()
    s = _FOOTMARK.sub("", s).strip()
    if allow_thousands and re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d{1,3}(?: \d{3})+", s):
        s = s.replace(",", "").replace(" ", "")
    if not re.fullmatch(_NUM, s):
        return None
    return int(s) if re.fullmatch(r"-?\d+", s) else float(s)


def parse_cell_values(cell_text, measure, sups=()):
    """Cella → [(alap-mező, érték, nyers)] a mérték szerint; kétértelműre üres lista (nem találgatunk).

    alap-mezők: 'e','n','m','sd','effect','ci_lo','ci_hi'. A számot nem alakítjuk át (csak str → szám)."""
    if any(re.search(r"\d", s or "") for s in (sups or ())):
        return []  # számos felső index (pl. 10^7) — nem egyértelmű
    raw = normalize_ws(cell_text)
    if not raw or re.fullmatch(r"(?i)n/?a|nr|ne|nd|-|–|—|\.|…|not reported|not applicable", raw):
        return []
    t = _jats._fix_dashes(raw)
    t = _FOOTMARK.sub("", t).strip()
    out = []
    if measure in ("e", "n", "m", "sd"):
        v = _num(t, allow_thousands=measure in ("e", "n"))
        if v is not None and (measure not in ("e", "n") or (isinstance(v, int) and v >= 0)):
            out.append((measure, v, raw))
    elif measure == "e_n":
        mm = re.fullmatch(r"(\d{1,3}(?:,\d{3})+|\d+)\s*/\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\s*\(\s*\d+(?:\.\d+)?\s*%?\s*\))?", t)
        if mm:
            e = _num(mm.group(1), True)
            n = _num(mm.group(2), True)
            if e is not None and n is not None and e <= n:
                out.extend([("e", e, raw), ("n", n, raw)])
    elif measure == "m_sd":
        mm = re.fullmatch(r"(%s)\s*(?:\(\s*(%s)\s*\)|±\s*(%s))" % (_NUM, _NUM, _NUM), t)
        if mm:
            sd = mm.group(2) if mm.group(2) is not None else mm.group(3)
            out.extend([("m", _num(mm.group(1)), raw), ("sd", _num(sd), raw)])
    elif measure == "effect":
        mm = re.fullmatch(r"(%s)\s*[\[(]\s*(%s)\s*(?:,|;|to|-)\s*(%s)\s*[\])]" % (_NUM, _NUM, _NUM), t)
        if mm:
            out.extend([("effect", _num(mm.group(1)), raw), ("ci_lo", _num(mm.group(2)), raw),
                        ("ci_hi", _num(mm.group(3)), raw)])
    return out


# ---------------------------------------------------------------------------
# a2/a3 — táblázat-sorok
# ---------------------------------------------------------------------------

def _row_groups(t, info):
    """Sorok csoportosítása vizsgálatonként: 'N.M' kód, rowspan, ismétlődő címke →
    [(csoport-kulcs, [sorindex], alcím)] — az alcím a megelőző teljes szélességű sor szövege (pl. 'Pain/short term')."""
    lc = info["label_col"]
    groups = []
    last_key = None
    heading = None
    for ri, row in enumerate(t.body):
        cell = row[lc] if lc < len(row) else None
        if cell is None:
            continue
        full_width = (cell.colspan >= max(2, t.ncols)) and not cell.spanned
        if not full_width and t.ncols >= 3 and cell.text:
            # minden más cella üres vagy a címkecella kifeszítése → alcím-sor (pl. 'Pain/immediate term'),
            # ha a címke nem vizsgálat (nincs év, nincs hivatkozás)
            others = [c for c in row if c.origin != cell.origin]
            if all(not c.text for c in others) and not parse_author_year(_label_text(cell, row, info)) and \
                    not cell.xrefs and not cell.sups:
                full_width = True
        if full_width:
            last_key = None
            heading = cell.text or None
            continue
        if cell.spanned and cell.origin[1] == lc and cell.origin[0] != ri:
            # függőleges kifeszítés: ugyanaz a vizsgálat
            if groups:
                groups[-1][1].append(ri)
            continue
        label_text = _label_text(cell, row, info)
        code = None
        if info.get("code_col") is not None:
            code = normalize_ws(row[info["code_col"]].text) or None
        pay = parse_author_year(label_text)
        if pay and pay.get("code"):
            code = code or pay["code"]
        major = None
        if code and re.fullmatch(r"\d{1,3}\.\d{1,2}", code):
            major = "code:" + code.split(".")[0]
        text_key = norm_text(re.sub(r"^\s*\d{1,3}(?:\.\d{1,2})?[.)]?\s+", "", label_text))
        key = major or ("text:" + text_key if text_key else "row:%d" % ri)
        if groups and key == last_key and (major or text_key):
            groups[-1][1].append(ri)
        else:
            groups.append((key, [ri], heading))
        last_key = key
    return groups


def _match_author_year(doc, pool_index, surname, year, suffix):
    key = norm_name(surname)
    hits = [r for r in pool_index.get(key, []) if r.year == year]
    if suffix and len(hits) > 1:
        suf = [r for r in hits if (r.year_suffix or "") == suffix]
        if len(suf) == 1:
            return suf
    return hits


def _pool_index(doc):
    idx = {}
    for r in _pool_refs(doc):
        if r.first_author:
            idx.setdefault(norm_name(r.first_author), []).append(r)
    return idx


def _row_quote(row, label_col):
    first = row[label_col].text if label_col < len(row) else ""
    nxt = None
    for c in row[label_col + 1:]:
        if c.text and not (c.spanned and c.origin[1] != c.col):
            nxt = c.text
            break
    q = first
    if nxt and len(first) + len(_CELL_SEP) + 10 <= QUOTE_MAX:
        q = first + _CELL_SEP + nxt
    return _clip(q, QUOTE_MAX)


def _process_table(b, t, info, pool_idx, strategy, mode):
    """Egy tábla sorai → jelöltek/bizonyítékok (+ másodlagos adatok).

    ``mode``: 'create' (új jelöltet is létrehozhat, illesztetlen sorból is — elsődleges a2-tábla a1 nélkül),
    'ref' (új jelölt csak hivatkozásra illesztett sorból; az illesztetlen sor figyelmeztetés),
    'attach' (csak meglévő jelölthöz fűz bizonyítékot — Cochrane-áttekintésnél és minőség/RoB-tábláknál)."""
    doc = b.doc
    lc = info["label_col"]
    is_data = strategy == "jats_forest"
    kind = "forest_plot" if is_data else "table_row"
    groups = _row_groups(t, info)
    used = 0
    implicit = info.get("kind") == "study_list"
    info["arm_level_rows"] = _arm_level_rows(t, info, groups)
    for gkey, rows, heading in groups:
        first_ri = rows[0]
        row = t.body[first_ri]
        cell = row[lc]
        label_text = _label_text(cell, row, info)
        targets = []
        how = {}
        for ri in rows:
            for rid, mode_ in _row_xref_targets(doc, t.body[ri], lc, info["ref_cols"], with_mode=True):
                if rid not in how:
                    targets.append(rid)
                    how[rid] = mode_
        pay = parse_author_year(label_text)
        if not targets and not pay:
            b.stats["rows_skipped"] += len(rows)
            continue
        group_key = "%s:%s" % (t.element_id, gkey if not gkey.startswith("text:") else "r%d" % (first_ri + 1))
        locator = {"element_id": t.element_id, "label": t.display_label, "section": _section_title(t.section_path),
                   "row": first_ri + 1, "column": t.columns[lc] if lc < len(t.columns) and t.columns[lc] else None}
        if len(rows) > 1:
            locator["rows"] = [r + 1 for r in rows]
        quote = _row_quote(row, lc)
        label = pay["label"] if pay else None
        cands = []
        if targets:
            for rid in targets:
                ref = doc.refs.get(rid)
                if ref is None:
                    continue
                conf = "medium" if (is_data or implicit or how.get(rid) == "sup_id") else "high"
                key = "ref:%s" % rid
                cand = b.get(key)
                if cand is None:
                    if mode == "attach" or ref.section in ("excluded", "additional", "other_versions"):
                        continue
                    cand = b.add(key, _new_cand(_cited_from_ref(ref), label or _ref_label(ref) or
                                                _clip(cell.text_label or cell.text, 120) or None, group_key,
                                                _ids_from_ref(ref, b.at), "included", conf, "proposed", ref_id=rid))
                loc = dict(locator)
                loc["ref_id"] = rid
                b.attach(cand, b.ev(kind, strategy, loc, quote, conf))
                if cand["status"] == "proposed":
                    _raise_conf(cand, conf)
                cands.append(cand)
        else:
            lk = _label_key(pay["surname"], pay["year"], pay.get("suffix"))
            known = b.by_label.get(lk, [])
            hits = _match_author_year(doc, pool_idx, pay["surname"], pay["year"], pay.get("suffix"))
            if known:
                conf = "low" if implicit else "medium"
                for cand in known:
                    loc = dict(locator)
                    if cand.get("ref_id"):
                        loc["ref_id"] = cand["ref_id"]
                    b.attach(cand, b.ev(kind, strategy, loc, quote, conf))
                    cands.append(cand)
            elif len(hits) == 1 and (mode != "attach" or b.get("ref:%s" % hits[0].ref_id) is not None):
                ref = hits[0]
                key = "ref:%s" % ref.ref_id
                cand = b.get(key)
                conf = "low" if implicit else "medium"
                if cand is None:
                    cand = b.add(key, _new_cand(_cited_from_ref(ref), pay["label"], group_key,
                                                _ids_from_ref(ref, b.at), "included", conf, "proposed",
                                                ref_id=ref.ref_id))
                    _reason(cand, "author_year_match")
                loc = dict(locator)
                loc["ref_id"] = ref.ref_id
                b.attach(cand, b.ev(kind, strategy, loc, quote, conf))
                if cand["status"] == "proposed":
                    _raise_conf(cand, conf)
                cands.append(cand)
            elif mode == "create":
                key = "label:%s" % lk
                cand = b.get(key)
                if cand is None:
                    cited = {"text": _clip(label_text, TEXT_MAX) or pay["label"], "first_author": pay["surname"],
                             "year": pay["year"], "title": None, "journal": None}
                    ids = {}
                    reg = _registry_in_row(t.body[first_ri], b.at)
                    if reg.get("nct"):
                        ids["nct"] = reg["nct"]
                    if reg.get("registry"):
                        ids["registry"] = reg["registry"]
                    cand = b.add(key, _new_cand(cited, pay["label"], group_key, ids, "included", "low", "proposed"))
                    if hits:
                        cand["ref_hint"] = {"reason": "ambiguous_first_author_year",
                                            "ref_ids": [r.ref_id for r in hits],
                                            "texts": [_clip(r.text, 200) for r in hits]}
                        _reason(cand, "ambiguous_ref_match")
                    else:
                        _reason(cand, "no_ref_match")
                b.attach(cand, b.ev(kind, strategy, locator, quote, "low"))
                cands.append(cand)
            else:
                b.warn("table_row_unmatched",
                       "A(z) %s %d. sora („%s”) nem illeszthető egyetlen jelölthöz vagy hivatkozáshoz sem; nem vettük "
                       "fel új jelöltként (ellenőrizd: elírt év vagy szerző?)." % (
                           t.display_label or t.element_id, first_ri + 1, _clip(label_text, 80)),
                       "Row %d of %s ('%s') matches no candidate or reference; not added as a new candidate." % (
                           first_ri + 1, t.display_label or t.element_id, _clip(label_text, 80)),
                       table=t.element_id, row=first_ri + 1, label=_clip(label_text, 120),
                       ambiguous_ref_ids=[r.ref_id for r in hits])
                continue
        if not cands:
            continue
        used += 1
        if implicit:
            for cand in cands:
                _reason(cand, "implicit_study_table")
        if info["columns"]:
            _secondary_from_rows(b, t, info, rows, cands, strategy, kind, heading)
    return used


def _registry_in_row(row, at):
    ids = {}
    for c in row:
        for kind, v in _jats.registry_ids_in_text(c.text):
            if kind == "nct" and "nct" not in ids:
                ids["nct"] = _idval(v, "jats.table", at)
            elif kind != "nct":
                ids.setdefault("registry", []).append(_idval(v, "jats.table", at, kind=kind))
    return ids


def _arm_level_rows(t, info, groups):
    """Igaz, ha a tábla sorai NEM vizsgálatonként egyek (kar/alcsoport-sorok, szétesett címkecellák): ilyenkor az
    'N' oszlop nem a vizsgálat teljes létszáma → 'n_total' nem rögzíthető (kétértelmű)."""
    lc = info["label_col"]
    ncols = [ci for ci, c in info["columns"].items() if c.get("field") == "n_total"]
    if not ncols:
        return False
    grouped = set(r for _, rows, _ in groups for r in rows)
    for ri, row in enumerate(t.body):
        cell = row[lc] if lc < len(row) else None
        if cell is None or ri in grouped:
            continue
        others = [c for c in row if c.origin != cell.origin and c.text]
        if others and not (cell.colspan >= max(2, t.ncols)):
            heading_like = not cell.text or not parse_author_year(_label_text(cell, row, info))
            if heading_like and any(row[ci].text for ci in ncols if ci < len(row)):
                return True
    for _, rows, _ in groups:
        if len(rows) < 2:
            continue
        for ci in ncols:
            vals = set()
            for ri in rows:
                c = t.body[ri][ci] if ci < len(t.body[ri]) else None
                if c is not None and c.text and not (c.spanned and c.origin[0] != ri):
                    vals.add(c.text)
            if len(vals) > 1:
                return True
    return False


def _secondary_from_rows(b, t, info, rows, cands, strategy, kind, heading=None):
    """A sor-csoport egyértelmű fejlécű számcellái → másodlagos értékek (status: unverified), cellánkénti
    bizonyítékkal. A kifeszített (rowspan) és a csoporton belül ismétlődő cellák csak egyszer számítanak."""
    lc = info["label_col"]
    outcome_default = _clip(heading, 200) if heading else (_clip(t.caption, 200) if t.caption else None)
    seen = set()
    conf = "medium" if strategy == "jats_forest" else "high"
    for ri in rows:
        row = t.body[ri]
        for ci, col in sorted(info["columns"].items()):
            if ci >= len(row) or not col.get("field"):
                continue
            cell = row[ci]
            if cell.spanned and (cell.origin[0] != ri or cell.origin[1] != ci):
                continue
            if col["field"] == "n_total" and info.get("arm_level_rows"):
                continue
            vals = parse_cell_values(cell.text_nosup, col["measure"], cell.sups)
            if not vals:
                continue
            sig = (ci, tuple((v[0], json.dumps(v[1])) for v in vals))
            if sig in seen:
                continue
            seen.add(sig)
            loc = {"element_id": t.element_id, "label": t.display_label,
                   "section": _section_title(t.section_path), "row": ri + 1, "column": col["header"]}
            if len(cands) == 1 and cands[0].get("ref_id"):
                loc["ref_id"] = cands[0]["ref_id"]
            quote = _clip(row[lc].text + _CELL_SEP + cell.text, QUOTE_MAX)
            e = b.ev(kind, strategy, loc, quote, conf)
            items = []
            for base, value, raw in vals:
                if col["measure"] == "effect":
                    field = base
                elif col["field"] == "n_total":
                    field = "n_total"
                else:
                    field = "%s%d" % (base, col["arm_index"])
                items.append((field, value, raw))
            if col["measure"] == "effect" and col.get("effect_measure"):
                items.append(("measure", col["effect_measure"], col["header"]))
            for field, value, raw in items:
                sv = {"field": field, "value": value, "unit": None,
                      "outcome": col.get("outcome") or outcome_default, "arm": col.get("arm_label"),
                      "evidence_id": e, "status": "unverified", "verified_decision": None,
                      "primary_locator": None, "raw": _clip(raw, 100), "data_source": "secondary",
                      "column": col["header"]}
                if col["measure"] == "effect":
                    sv["measure"] = col.get("effect_measure")
                for cand in cands:
                    cand["_sec"].append(sv)
                    b.stats["secondary_values"] += 1
            for cand in cands:
                b.attach(cand, e)


# ---------------------------------------------------------------------------
# a4 — szöveges állítások (darabszám + xref-tartomány)
# ---------------------------------------------------------------------------

_SMALL = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
          "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_NUMWORD = (r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[\s-](?:one|two|three|four|five|six|"
            r"seven|eight|nine))?|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
            r"ten|one hundred|a hundred|one|two|three|four|five|six|seven|eight|nine)")
_NUMBER = r"(?<!than )(?<!least )(?<!most )(?<!< )(?<!> )(?<!≥ )(?<!≤ )(?P<num>\d{1,4}|%s)" % _NUMWORD
_MODS = (r"(?:(?:eligible|relevant|unique|individual|separate|primary|original|independent|published|"
         r"unpublished|randomi[sz]ed|non-?randomi[sz]ed|quasi-?randomi[sz]ed|controlled|clinical|cohort|"
         r"observational|prospective|retrospective|case-control|cross-sectional|parallel(?:-group)?|"
         r"cross-?over|cluster(?:-randomi[sz]ed)?|placebo-controlled|double-blind(?:ed)?|phase\s+(?:[1-4]|i{1,3}|iv)|"
         r"comparative|interventional|diagnostic|full-text|additional|new|distinct|human|"
         r"intervention|experimental)[\s,]+){0,5}")
_UNIT = r"(?P<unit>studies|study|trials|trial|rcts|rct|articles|article|papers|publications|reports|cohorts)"
_ADV = r"(?:(?:finally|ultimately|eventually|therefore|thus|then|subsequently|also|all)\s+)?"
_PAREN = r"(?:\s*[\[(][^\])]{0,80}[\])])?"
# a szám és az ige közti töltelék: legfeljebb 8 szó, tagadás/kizárás nélkül ('65 trials comprising 72 interventions
# and N = 8608 participants were included')
_FILL = r"(?:(?!(?:not|no|excluded|without|than)\b)[\w=.,%%()'\u2019-]+\s+){0,8}?"
_STATEMENTS = (
    ("meta_analysis", re.compile(
        r"\b%s\s+%s%s%s(?:\s+with\s+[\w\s,-]{0,40}?)?\s+(?:were\s+|was\s+)?%s(?:used|included|pooled|entered|"
        r"combined|analy[sz]ed)\s+in\s+(?:the|this|our|a)\s+(?:quantitative\s+synthesis|pooled\s+analysis|"
        r"(?:\w+\s+){0,2}meta-?analys[ie]s)" % (_NUMBER, _MODS, _UNIT, _PAREN, _ADV), re.I)),
    ("review", re.compile(
        r"\binclud(?:ed|ing)\s+(?:a\s+total\s+of\s+|(?:(?:study|trial|individual|aggregate|summary|participant)"
        r"[\s-]+(?:level\s+|participant\s+)?)?data\s+from\s+|only\s+|altogether\s+)?%s\s+%s%s" %
        (_NUMBER, _MODS, _UNIT), re.I)),
    ("review", re.compile(
        r"\b%s\s+%s%s%s\s+%s(?:were|was|are|is)\s+%s(?:included|eligible|selected\s+for\s+inclusion|"
        r"eligible\s+for\s+inclusion)\b" % (_NUMBER, _MODS, _UNIT, _PAREN, _FILL, _ADV), re.I)),
    ("review", re.compile(
        r"\b%s\s+%s%s%s\s+(?:\w+\s+){0,4}?(?:met|fulfilled|satisfied|meeting|fulfilling)\s+(?:the\s+|our\s+|all\s+"
        r"(?:the\s+)?)?(?:inclusion|eligibility|selection)\s+criteria" % (_NUMBER, _MODS, _UNIT, _PAREN), re.I)),
    ("review", re.compile(
        r"\b%s\s+%s%s\s+included\s+in\s+(?:the|this|our)\s+(?:systematic\s+)?(?:review|analysis|overview)" %
        (_NUMBER, _MODS, _UNIT), re.I)),
)
_NUM_UNIT = re.compile(r"\b%s\s+%s%s\b" % (_NUMBER, _MODS, _UNIT), re.I)
_UNIT_MAP = {"studies": "studies", "study": "studies", "cohorts": "studies", "trials": "trials",
             "trial": "trials", "rcts": "trials", "rct": "trials", "articles": "reports", "article": "reports",
             "papers": "reports", "publications": "reports", "reports": "reports"}


def _word_number(s):
    s = s.lower().replace("-", " ").strip()
    if s.isdigit():
        return int(s)
    if s in ("one hundred", "a hundred"):
        return 100
    parts = s.split()
    if len(parts) == 1:
        if parts[0] in _SMALL:
            return _SMALL.index(parts[0])
        return _TENS.get(parts[0])
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _SMALL[1:10]:
        return _TENS[parts[0]] + _SMALL.index(parts[1])
    return None


def _sentences(text):
    out = []
    start = 0
    for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\u201c\"])", text):
        if re.search(r"(?:\b(?:et al|Fig|Figs|Tab|vs|e\.g|i\.e|approx|ref|refs|No|no|p)\.)$", text[max(0, m.start() - 8):m.start()]):
            continue
        out.append((start, m.start()))
        start = m.end()
    out.append((start, len(text)))
    return out


def _quote_window(text, a, b, mstart, mend, n=QUOTE_MAX):
    sent = text[a:b].strip()
    if len(sent) <= n:
        return sent
    lo = max(a, mstart - 80)
    hi = min(b, lo + n)
    w = text[lo:hi]
    if lo > a:
        sp = w.find(" ")
        if 0 <= sp < 30:
            w = w[sp + 1:]
    return _clip(w, n)


def _xref_cluster(doc, p, a, b, after):
    """Az illeszkedés utáni első bibr-xref-csoport (pl. '[24–28, 38–59]') → hivatkozás-azonosítók."""
    xs = [x for x in p.xrefs if a <= x["start"] < b and x["start"] >= after and
          (x["rid"] in doc.refs or x["rid"] in doc.groups)]
    if not xs:
        return []
    xs.sort(key=lambda x: x["start"])
    if xs[0]["start"] - after > 160:
        return []
    cluster = [xs[0]]
    for x in xs[1:]:
        gap = _jats._fix_dashes(p.text[cluster[-1]["end"]:x["start"]]).strip()
        if gap == "" or re.fullmatch(r"[\s,;\-\[\]()]*|and|to", gap):
            cluster.append(x)
        else:
            break
    return doc.bibr_refs(cluster, p.text)


def inclusion_statements(doc):
    """„We included N studies [refs]" típusú mondatok → [{value, unit, kind ('review'|'meta_analysis'), quote,
    locator, ref_ids, in_abstract, section_kind}] dokumentum-sorrendben."""
    out = []
    for p in doc.paragraphs:
        text = _jats._fix_dashes(p.text)
        for a, b in _sentences(text):
            sent = text[a:b]
            seen_spans = []
            for kind, rx in _STATEMENTS:
                for m in rx.finditer(sent):
                    s0, s1 = a + m.start("num"), a + m.end("num")
                    if any(s0 < y and x < s1 for x, y in seen_spans):
                        continue
                    n = _word_number(m.group("num"))
                    if n is None:
                        continue
                    seen_spans.append((s0, s1))
                    k = kind
                    tail = sent[m.end():m.end() + 60].lower()
                    if k == "review" and re.match(r"\s*(?:in|into)\s+(?:the|this|our|a)\s+(?:\w+\s+){0,2}"
                                                   r"meta-?analys", tail):
                        k = "meta_analysis"
                    refs = _xref_cluster(doc, p, a, b, a + m.start())
                    base = {
                        "kind": k,
                        "quote": _quote_window(p.text, a, b, a + m.start(), a + m.end()),
                        "locator": {"container": doc.container, "element_id": p.element_id,
                                    "section": " > ".join(p.section_path) if p.section_path else None},
                        "ref_ids": refs,
                        "in_abstract": p.in_abstract,
                        "section_kind": p.kind,
                        "order": p.order,
                    }
                    out.append(dict(base, value=n, unit=_UNIT_MAP.get(m.group("unit").lower(), "unknown")))
                    # további szám+egység ugyanabban az állításban: '12 reports (13 randomised trials)',
                    # '24 trials in 26 reports' — mindkettő rögzül (TERV 6.0/3)
                    for m2 in _NUM_UNIT.finditer(sent, m.end("unit"), m.end()):
                        t0, t1 = a + m2.start("num"), a + m2.end("num")
                        n2 = _word_number(m2.group("num"))
                        if n2 is None or any(t0 < y and x < t1 for x, y in seen_spans):
                            continue
                        seen_spans.append((t0, t1))
                        out.append(dict(base, value=n2, unit=_UNIT_MAP.get(m2.group("unit").lower(), "unknown"),
                                        secondary_in_statement=True))
    return out


# -- keresési dátum (6.0/2) ---------------------------------------------------------

_MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
           "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
           "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|" \
       r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_DATE_FORMS = (
    re.compile(r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+%s,?\s+(?P<year>(?:19|20)\d{2})" % _MON, re.I),
    re.compile(r"%s\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>(?:19|20)\d{2})" % _MON, re.I),
    re.compile(r"(?P<year>(?:19|20)\d{2})-(?P<mnum>\d{2})-(?P<day>\d{2})"),
    re.compile(r"%s,?\s+(?P<year>(?:19|20)\d{2})" % _MON, re.I),
    re.compile(r"(?P<year>(?:19|20)\d{2})(?![\d-])"),
)
_SEARCH_CUE = re.compile(
    r"\b(searched|search(?:es)?\s+(?:was|were)\s+(?:conducted|performed|run|carried\s+out|updated|completed)|"
    r"last\s+search(?:ed)?|date\s+of\s+(?:the\s+)?(?:last\s+|most\s+recent\s+|final\s+)?search|"
    r"(?:literature|database|electronic)\s+search(?:es)?|search(?:es)?\s+(?:up\s+)?to|search\s+date|"
    r"(?:were|was)\s+searched)\b", re.I)
_DATE_LEAD = re.compile(r"\b(?:up\s+to|until|till|through|thru|to|on|in|as\s+of|before|by|ending)\s+$|"
                        r"date\s+of\s+(?:the\s+)?(?:last\s+|most\s+recent\s+|final\s+)?search\s*[:,]?\s*(?:was\s+)?$|"
                        r"search\s+date\s*[:,]?\s*$|[-–]\s*$", re.I)


def _parse_date_at(text, pos_limit):
    """Az összes dátum a szövegben: [(start, end, value, precision)]."""
    found = []
    taken = []
    for rx in _DATE_FORMS:
        for m in rx.finditer(text):
            if any(m.start() < y and x < m.end() for x, y in taken):
                continue
            gd = m.groupdict()
            year = int(gd["year"])
            month = None
            if gd.get("mon"):
                month = _MONTHS.get(gd["mon"].lower().rstrip("."))
                if month is None:
                    month = _MONTHS.get(gd["mon"].lower()[:3])
            elif gd.get("mnum"):
                month = int(gd["mnum"])
            day = int(gd["day"]) if gd.get("day") else None
            if month is not None and not 1 <= month <= 12:
                continue
            if day is not None and not 1 <= day <= 31:
                continue
            if day and month:
                val, prec = "%04d-%02d-%02d" % (year, month, day), "day"
            elif month:
                val, prec = "%04d-%02d" % (year, month), "month"
            else:
                val, prec = "%04d" % year, "year"
            taken.append((m.start(), m.end()))
            found.append((m.start(), m.end(), val, prec))
    found.sort()
    return found


def search_date_statements(doc):
    """A keresés (utolsó) dátuma az absztrakt/módszertan mondataiból: [{value, precision, quote, locator,
    in_abstract}] — csak olyan dátum, amelyet 'to/until/through/on/in/as of …' vagy 'date of search' vezet be."""
    out = []
    for p in doc.paragraphs:
        if not (p.in_abstract or p.kind in ("methods", None)):
            continue
        text = p.text
        for a, b in _sentences(text):
            sent = text[a:b]
            if not _SEARCH_CUE.search(sent):
                continue
            prev_end = None
            for s, e, val, prec in _parse_date_at(sent, len(sent)):
                lead = sent[max(0, s - 60):s]
                listed = prev_end is not None and re.fullmatch(r"\s*(?:,|and|,\s*and|&|or)\s*", sent[prev_end:s])
                if not (_DATE_LEAD.search(lead) or listed):
                    continue
                if prec == "year" and re.match(r"\s*[-–]\s*\d", sent[e:e + 3]):
                    continue
                prev_end = e
                out.append({"value": val, "precision": prec,
                            "quote": _quote_window(text, a, b, a + s, a + e),
                            "locator": {"container": doc.container, "element_id": p.element_id,
                                        "section": " > ".join(p.section_path) if p.section_path else None},
                            "in_abstract": p.in_abstract, "order": p.order})
    return out


def _best_search_date(stmts):
    if not stmts:
        return None
    # a legkésőbbi dátum; azonos dátumnál a pontosabb
    rank = {"day": 3, "month": 2, "year": 1}

    def key(s):
        v = s["value"]
        return (v[:4], v[5:7] or "00", v[8:10] or "00", rank[s["precision"]])

    return sorted(stmts, key=key)[-1]


_UNIT_RANK = {"studies": 3, "trials": 3, "reports": 1, "unknown": 0}


def _best_k(stmts):
    best, conflicts = _best_k_of(stmts, "review")
    if best is None:
        # nincs „included in the review" állítás: a metaanalízisbe bevont szám (gyakran ugyanaz) a tartalék
        best, conflicts = _best_k_of(stmts, "meta_analysis")
    return best, conflicts


def _best_k_of(stmts, kind):
    """A közölt vizsgálatszám: elsőként az absztrakt első állítása, különben az Eredmények legnagyobb értéke
    (a részszámok — 'one study for inpatients' — kisebbek). Bevezetés/megbeszélés (más áttekintések számai) kimarad.
    Visszaad: (állítás, eltérések) — eltérés, ha az absztrakt és az Eredmények maximuma különbözik."""
    rev = [s for s in stmts if s["kind"] == kind]
    abstract = [s for s in rev if s["in_abstract"]]
    results = [s for s in rev if not s["in_abstract"] and s["section_kind"] == "results"]
    other = [s for s in rev if not s["in_abstract"] and s["section_kind"] in (None, "other")]
    best = None
    if abstract:
        first = abstract[0]
        same = [s for s in abstract if s["order"] == first["order"] and s["quote"] == first["quote"]]
        best = sorted(same, key=lambda s: (_UNIT_RANK.get(s["unit"], 0), -abstract.index(s)))[-1]
    elif results:
        best = sorted(results, key=lambda s: (_UNIT_RANK.get(s["unit"], 0), s["value"], -s["order"]))[-1]
    elif other:
        best = sorted(other, key=lambda s: (_UNIT_RANK.get(s["unit"], 0), s["value"], -s["order"]))[-1]
    if best is None:
        return None, []
    conflicts = []
    if abstract and results:
        rmax = max(s["value"] for s in results if _UNIT_RANK.get(s["unit"], 0) >= _UNIT_RANK.get(best["unit"], 0)) \
            if any(_UNIT_RANK.get(s["unit"], 0) >= _UNIT_RANK.get(best["unit"], 0) for s in results) else 0
        if rmax > best["value"]:
            conflicts = sorted(set([(best["value"], best["unit"])] +
                                   [(s["value"], s["unit"]) for s in results if s["value"] == rmax]))
    return best, conflicts


def _a4_statements(b, have_candidates):
    doc = b.doc
    stmts = inclusion_statements(doc)
    used = False
    for s in stmts:
        if s["kind"] != "review" or not s["ref_ids"]:
            continue
        refset = [r for r in s["ref_ids"] if r in doc.refs]
        if not refset:
            continue
        consistent = len(refset) == s["value"]
        for rid in refset:
            ref = doc.refs[rid]
            key = "ref:%s" % rid
            cand = b.get(key)
            if cand is None and have_candidates:
                continue
            conf = "medium" if consistent else "low"
            if cand is None:
                cand = b.add(key, _new_cand(_cited_from_ref(ref), _ref_label(ref), "stmt:%s" % (
                    s["locator"].get("element_id") or s["order"]), _ids_from_ref(ref, b.at), "included", conf,
                    "proposed", ref_id=rid))
                if not consistent:
                    _reason(cand, "statement_count_mismatch")
            e = b.ev("text", "jats_xref", {"element_id": s["locator"].get("element_id"),
                                          "section": s["locator"].get("section"), "ref_id": rid},
                     s["quote"], conf)
            b.attach(cand, e)
            used = True
    if used:
        b.use("jats_xref")
    return stmts


# ---------------------------------------------------------------------------
# tartalék: irodalomjegyzék (6.2 b)
# ---------------------------------------------------------------------------

def _citation_context(doc):
    ctx = {}
    for p in doc.paragraphs:
        rids = doc.bibr_refs(p.xrefs, p.text)
        kind = "abstract" if p.in_abstract else (p.kind or "other")
        for rid in rids:
            c = ctx.setdefault(rid, {"n_citations": 0, "cited_in": [], "cited_in_tables": []})
            c["n_citations"] += 1
            if kind not in c["cited_in"]:
                c["cited_in"].append(kind)
    for t in doc.tables:
        for row in t.body:
            for cell in row:
                if cell.spanned:
                    continue
                for rid in doc.bibr_refs(cell.xrefs, cell.text):
                    c = ctx.setdefault(rid, {"n_citations": 0, "cited_in": [], "cited_in_tables": []})
                    if t.element_id not in c["cited_in_tables"]:
                        c["cited_in_tables"].append(t.element_id)
    return ctx


def _fallback_reflist(b, only_missing=False):
    doc = b.doc
    ctx = _citation_context(doc)
    n = 0
    for rid in doc.ref_order:
        ref = doc.refs[rid]
        if ref.section in ("other_versions",):
            continue
        key = "ref:%s" % rid
        if b.get(key) is not None:
            continue
        cand = b.add(key, _new_cand(_cited_from_ref(ref), _ref_label(ref), None, _ids_from_ref(ref, b.at),
                                    "unknown", "low", "proposed", ref_id=rid))
        cand["context"] = ctx.get(rid, {"n_citations": 0, "cited_in": [], "cited_in_tables": []})
        _reason(cand, "role_unknown")
        e = b.ev("reference_list", "reflist_api",
                 {"ref_id": rid, "label": ref.label, "section": ref.section_title or "References"},
                 ref.text or ref.title or rid, "low")
        b.attach(cand, e)
        n += 1
    if n:
        b.use("reflist_api")
    return n


# ---------------------------------------------------------------------------
# fő belépési pont
# ---------------------------------------------------------------------------

def extract_included(doc, review_id, at=None, actor=TOOL_ACTOR, fallback="auto", strategies=None):
    """A bevont-vizsgálat jelöltek kinyerése egy JATS-dokumentumból (a1 → a2/a3 → a4 → tartalék).

    ``fallback``: 'auto' (irodalomjegyzék csak ha nincs bevont-jelölt), 'always' (a nem-jelölt hivatkozások
    is, ``unknown`` szereppel), 'never'. ``strategies``: a futtatandók részhalmaza ('a1','a2','a3','a4').
    A kimenet determinisztikus (azonos bemenet → azonos azonosítók és sorrend; csak ``at`` változhat)."""
    at = at or _now()
    strategies = set(strategies or ("a1", "a2", "a3", "a4"))
    b = _Builder(doc, review_id, at, actor)
    pool_idx = _pool_index(doc)
    has_a1 = _a1_cochrane(b) if "a1" in strategies else False
    if "a2" in strategies or "a3" in strategies:
        _tables_selected(b, pool_idx, strategies, has_a1)
    have = any(c["role_in_review"] in ("included", "included_companion") for c in b.cands)
    stmts = _a4_statements(b, have) if "a4" in strategies else inclusion_statements(doc)
    _hints_from_statements(b, stmts)
    _warn_unclaimed(b, stmts)
    have = any(c["role_in_review"] in ("included", "included_companion") for c in b.cands)
    if fallback == "always" or (fallback == "auto" and not have):
        _fallback_reflist(b)
        if not have:
            b.warn("no_included_structure",
                   "Nem találtam szerkezetbe foglalt bevont-vizsgálat listát (Cochrane-szakasz, jellemzők táblája "
                   "vagy „We included N studies [hivatkozások]” mondat). Az irodalomjegyzék tételei „ismeretlen” "
                   "szerepű jelöltek: az ágens vagy te döntöd el, melyik bevont vizsgálat (EP2).",
                   "No structured list of included studies was found; reference-list items are 'unknown' "
                   "candidates for agent/human classification (EP2).")
    # k és keresési dátum bizonyítékkal
    best_k, k_conf = _best_k(stmts)
    k_reported = None
    if best_k is not None:
        e = b.ev("text", "jats_text", best_k["locator"], best_k["quote"], "medium")
        k_reported = {"value": best_k["value"], "unit": best_k["unit"], "evidence_id": e}
        if k_conf:
            b.warn("k_statements_differ",
                   "Az áttekintés több, eltérő vizsgálatszámot közöl: %s." % ", ".join(
                       "%d %s" % kv for kv in k_conf),
                   "The review states differing study counts: %s." % ", ".join("%d %s" % kv for kv in k_conf),
                   values=[list(kv) for kv in k_conf])
    sd_stmts = search_date_statements(doc)
    best_sd = _best_search_date(sd_stmts)
    search_date = None
    if best_sd is not None:
        e = b.ev("text", "jats_text", best_sd["locator"], best_sd["quote"], "medium")
        search_date = {"value": best_sd["value"], "precision": best_sd["precision"], "fallback": False,
                       "evidence_id": e}
    _flag_review_needs(b)
    return _finalize(b, k_reported, search_date, stmts, sd_stmts)


_CONFIRM_ONLY_CAPTION = re.compile(r"quality|risk of bias|bias assessment|appraisal|grade")
_PRIMARY_CAPTION = re.compile(r"characteristic")


def _tables_selected(b, pool_idx, strategies, has_a1):
    """a2 (bevont-vizsgálat táblák), a3 (adattáblák) és a felirat nélküli vizsgálat-táblák a megfelelő móddal:
    Cochrane-áttekintésnél minden tábla csak megerősít ('attach'); egyébként az első 'characteristics' tábla
    hozhat létre illesztetlen jelöltet is ('create'), a többi csak hivatkozásra illesztettet ('ref'); a minőség/RoB
    táblák csak akkor teremtenek jelöltet, ha más bevont-tábla nincs."""
    doc = b.doc
    infos = [(t, classify_table(t, doc)) for t in doc.tables]
    inc = [(t, i) for t, i in infos if i["kind"] == "included"]

    def prio(t):
        cap = norm_text("%s %s" % (t.label or "", t.caption or ""))
        if _CONFIRM_ONLY_CAPTION.search(cap):
            return 2
        return 0 if _PRIMARY_CAPTION.search(cap) else 1

    inc.sort(key=lambda it: (prio(it[0]), it[0].order))
    primary_done = False
    if "a2" in strategies:
        for t, info in inc:
            if has_a1:
                mode = "attach"
            elif not primary_done:
                mode = "create"
            elif prio(t) == 2:
                mode = "attach"
            else:
                mode = "ref"
            if _process_table(b, t, info, pool_idx, "jats_table", mode):
                b.use("jats_table")
                b.stats["tables_included"].append(t.element_id)
                if mode == "create":
                    primary_done = True
        have = any(c["role_in_review"] in ("included", "included_companion") for c in b.cands)
        for t, info in infos:
            if info["kind"] != "study_list":
                continue
            mode = "attach" if (has_a1 or have) else "ref"
            if _process_table(b, t, info, pool_idx, "jats_table", mode):
                b.use("jats_table")
                b.stats.setdefault("tables_study_list", []).append(t.element_id)
    if "a3" in strategies:
        have = any(c["role_in_review"] in ("included", "included_companion") for c in b.cands)
        for t, info in infos:
            if info["kind"] != "data":
                continue
            mode = "attach" if has_a1 else ("ref" if have else "create")
            if _process_table(b, t, info, pool_idx, "jats_forest", mode):
                b.use("jats_forest")
                b.stats["tables_data"].append(t.element_id)


def _hints_from_statements(b, stmts):
    """Táblázat-sor illesztés nélkül: ha az a4-mondat hivatkozásai közül pontosan egy 'gazdátlan' hivatkozás első
    szerzője egyezik → ``ref_hint`` (azonosító NÉLKÜL; emberi megerősítéshez)."""
    doc = b.doc
    claimed = set(c.get("ref_id") for c in b.cands if c.get("ref_id"))
    residual = []
    for s in stmts:
        if s["kind"] != "review":
            continue
        for rid in s["ref_ids"]:
            if rid in doc.refs and rid not in claimed and rid not in residual:
                residual.append(rid)
    claimed_hints = set()
    for c in b.cands:
        if c.get("ref_id") or c.get("ref_hint") or c["role_in_review"] != "included":
            continue
        fa = c["cited_as"].get("first_author")
        if not fa:
            continue
        same = [rid for rid in residual if doc.refs[rid].first_author and
                norm_name(doc.refs[rid].first_author) == norm_name(fa)]
        if len(same) == 1:
            r = doc.refs[same[0]]
            c["ref_hint"] = {"reason": "inclusion_statement_first_author", "ref_ids": [r.ref_id],
                             "texts": [_clip(r.text, 200)], "year_in_ref": r.year,
                             "year_in_table": c["cited_as"].get("year")}
            _reason(c, "ref_hint_available")
            claimed_hints.add(r.ref_id)
        else:
            pool = [r for r in _pool_refs(doc) if r.first_author and norm_name(r.first_author) == norm_name(fa)]
            year = c["cited_as"].get("year")
            similar = [r for r in _pool_refs(doc) if r.first_author and r.year == year and
                       norm_name(r.first_author) != norm_name(fa) and
                       difflib.SequenceMatcher(None, norm_name(r.first_author), norm_name(fa)).ratio() >= 0.8]
            if len(pool) == 1:
                r = pool[0]
                c["ref_hint"] = {"reason": "first_author_only", "ref_ids": [r.ref_id],
                                 "texts": [_clip(r.text, 200)], "year_in_ref": r.year,
                                 "year_in_table": year}
                _reason(c, "ref_hint_available")
            elif not pool and len(similar) == 1:
                # elírt név ('Domingues' ↔ 'Dominguez'): csak tipp, azonosító NÉLKÜL
                r = similar[0]
                c["ref_hint"] = {"reason": "similar_first_author_same_year", "ref_ids": [r.ref_id],
                                 "texts": [_clip(r.text, 200)], "year_in_ref": r.year,
                                 "year_in_table": year}
                _reason(c, "ref_hint_available")


def _warn_unclaimed(b, stmts):
    """Az „included" állítás hivatkozásai közül azok, amelyekhez nincs jelölt (a táblázat hiányos lehet) → EP2."""
    doc = b.doc
    if not any(c["role_in_review"] in ("included", "included_companion") for c in b.cands):
        return
    claimed = set(c.get("ref_id") for c in b.cands if c.get("ref_id"))
    hinted = set(r for c in b.cands for r in ((c.get("ref_hint") or {}).get("ref_ids") or []))
    left = []
    for s_ in stmts:
        if s_["kind"] != "review":
            continue
        for rid in s_["ref_ids"]:
            if rid in doc.refs and rid not in claimed and rid not in hinted and rid not in left:
                left.append(rid)
    if left:
        labels = [_ref_label(doc.refs[r]) or r for r in left]
        b.warn("statement_refs_unclaimed",
               "A „bevont vizsgálatok” mondat %d hivatkozásához nincs jelölt (a táblázat hiányos lehet): %s. "
               "Nézd át őket (EP2): az ágens vagy te döntöd el, bevont vizsgálatok-e." % (
                   len(left), ", ".join(labels[:12]) + (" …" if len(labels) > 12 else "")),
               "%d references cited in the 'included studies' statement have no candidate (the table may be "
               "incomplete): %s." % (len(left), ", ".join(labels[:12]) + (" …" if len(labels) > 12 else "")),
               ref_ids=left[:200])


def _flag_review_needs(b):
    for c in b.cands:
        if c["confidence"] != "high":
            _reason(c, "confidence_%s" % c["confidence"])
        if c["role_in_review"] == "unknown":
            _reason(c, "role_unknown")
        c["needs_review"] = bool(c["review_reasons"]) and not (
            c["status"] == "confirmed" and c["confidence"] == "high")


def _finalize(b, k_reported, search_date, stmts, sd_stmts):
    # azonosítók kiosztása: bizonyíték a létrehozás sorrendjében, jelölt a felvétel sorrendjében
    for i, e in enumerate(b.evidence, 1):
        e["evidence_id"] = "ev-%s-%04d" % (b.review_id, i)
    evidence = []
    for e in b.evidence:
        d = {k: v for k, v in e.items() if not k.startswith("_")}
        evidence.append(d)
    cands = []
    for i, c in enumerate(b.cands, 1):
        c["cand_id"] = "c%04d" % i
        c["evidence_ids"] = [e["evidence_id"] for e in c["_ev"]]
        sec = []
        seen = set()
        for sv in c["_sec"]:
            d = dict(sv)
            d["evidence_id"] = sv["evidence_id"]["evidence_id"]
            sig = (d["field"], d["evidence_id"], json.dumps(d["value"]))
            if sig in seen:
                continue
            seen.add(sig)
            sec.append(d)
        c["secondary_data"] = sec
        out = {k: v for k, v in c.items() if not k.startswith("_")}
        cands.append(out)
    excluded = []
    for x in b.excluded:
        d = dict(x)
        d["evidence_id"] = x["evidence_id"]["evidence_id"]
        if d.get("reason_evidence_id") is None:
            d.pop("reason_evidence_id", None)
        else:
            d["reason_evidence_id"] = x["reason_evidence_id"]["evidence_id"]
        excluded.append(d)
    if k_reported:
        k_reported = dict(k_reported, evidence_id=k_reported["evidence_id"]["evidence_id"])
    if search_date:
        search_date = dict(search_date, evidence_id=search_date["evidence_id"]["evidence_id"])
    groups = set()
    for c in cands:
        if c["role_in_review"] in ("included", "included_companion"):
            groups.add(c.get("group_key") or c["extract_key"])
    stats = dict(b.stats)
    stats.update({
        "n_candidates": len(cands),
        "n_included_candidates": sum(1 for c in cands if c["role_in_review"] in ("included", "included_companion")),
        "n_needs_review": sum(1 for c in cands if c.get("needs_review")),
        "n_evidence": len(evidence),
        "n_excluded_by_review": len(excluded),
    })
    return {
        "review_id": b.review_id,
        "container": b.container,
        "at": b.at,
        "strategies": list(b.strategies),
        "candidates": cands,
        "evidence": evidence,
        "excluded_by_review": excluded,
        "k_reported": k_reported,
        "search_date": search_date,
        "k_statements": [_public_stmt(s) for s in stmts],
        "search_date_statements": [_public_stmt(s) for s in sd_stmts],
        "n_study_groups": len(groups),
        "warnings": b.warnings,
        "stats": stats,
    }


def _public_stmt(s):
    return {k: v for k, v in s.items() if k != "order"}


# ---------------------------------------------------------------------------
# API-ból kapott irodalomjegyzék (Europe PMC /references, PubMed ReferenceList, OpenAlex referenced_works,
# Scopus view=REF) → jelöltek (unknown, low, needs_review)
# ---------------------------------------------------------------------------

def _norm_record(rec, source):
    """Különböző API-alakok → {'text','first_author','year','title','journal','ids':{…},'key'}."""
    r = rec or {}
    ids = {}
    title = r.get("title") or r.get("display_name") or r.get("ref-title") or r.get("articleTitle")
    if isinstance(title, dict):
        title = title.get("ref-titletext") or title.get("$")
    journal = r.get("journal") or r.get("journalAbbreviation") or r.get("sourcetitle") or r.get("source_title")
    year = r.get("year") or r.get("pubYear") or r.get("publication_year") or r.get("publicationyear")
    if isinstance(year, dict):
        year = year.get("@first") or year.get("$")
    first = r.get("first_author")
    text = r.get("text") or r.get("citation") or r.get("Citation")
    # Europe PMC
    if r.get("authorString") and not first:
        first = _jats.first_author_from_text(r["authorString"])
    src = (r.get("source") or "").upper() if isinstance(r.get("source"), str) else ""
    if src == "MED" and r.get("id"):
        ids["pmid"] = str(r["id"])
    if src == "PMC" and r.get("id"):
        ids["pmcid"] = str(r["id"])
    # OpenAlex
    if isinstance(r.get("ids"), dict):
        for k in ("pmid", "pmcid", "doi", "openalex"):
            if r["ids"].get(k):
                ids[k] = str(r["ids"][k])
    oid = r.get("id")
    if isinstance(oid, str) and re.search(r"openalex\.org/W\d+|^W\d+$", oid):
        ids["openalex"] = oid
    if r.get("authorships") and not first:
        try:
            nm = r["authorships"][0]["author"]["display_name"]
            first = nm.split()[-1] if nm else None
        except (KeyError, IndexError, TypeError, AttributeError):
            pass
    # PubMed ReferenceList
    aids = r.get("article_ids") or r.get("ArticleIdList")
    if isinstance(aids, dict):
        for k, v in aids.items():
            kk = {"pubmed": "pmid", "pmc": "pmcid"}.get(k.lower(), k.lower())
            if kk in ("pmid", "pmcid", "doi") and v:
                ids[kk] = str(v)
    # Scopus (view=REF; a mezőnevek élőben nem igazoltak — TERV 23.1)
    for k in ("ce:doi", "prism:doi", "doi"):
        if r.get(k) and isinstance(r.get(k), str):
            ids.setdefault("doi", r[k])
    eid = r.get("eid") or r.get("scopus-eid")
    if not eid and r.get("scopus-id"):
        eid = "2-s2.0-%s" % r["scopus-id"]
    if eid:
        ids["eid"] = str(eid)
    al = r.get("author-list")
    if isinstance(al, dict) and not first:
        au = al.get("author")
        if isinstance(au, list) and au:
            first = au[0].get("ce:surname") or au[0].get("ce:indexed-name")
        elif isinstance(au, dict):
            first = au.get("ce:surname") or au.get("ce:indexed-name")
    # normalizálás
    nids = {}
    if ids.get("pmid"):
        v = _jats.normalize_pmid(ids["pmid"])
        if v:
            nids["pmid"] = v
    if ids.get("pmcid"):
        v = _jats.normalize_pmcid(ids["pmcid"])
        if v:
            nids["pmcid"] = v
    if ids.get("doi"):
        v = _jats.normalize_doi(ids["doi"])
        if v:
            nids["doi"] = v
    if ids.get("openalex"):
        m = re.search(r"(W\d+)", ids["openalex"])
        if m:
            nids["openalex"] = m.group(1)
    if ids.get("eid"):
        m = re.search(r"(2-s2\.0-\d+)", ids["eid"])
        if m:
            nids["eid"] = m.group(1)
    try:
        y = int(str(year)[:4]) if year else None
        if y is not None and not 1800 <= y <= 2100:
            y = None
    except ValueError:
        y = None
    if not text:
        bits = [b for b in (r.get("authorString"), title, journal, str(y) if y else None) if b]
        text = ". ".join(str(x).strip().rstrip(".") for x in bits)
    key = r.get("key") or r.get("ref_id") or nids.get("pmid") or nids.get("doi") or nids.get("openalex") or \
        nids.get("eid") or norm_text(text)[:60]
    return {"text": normalize_ws(text) or None, "first_author": first, "year": y,
            "title": normalize_ws(title) if title else None, "journal": normalize_ws(journal) if journal else None,
            "ids": nids, "key": str(key)}


def candidates_from_reference_records(review_id, records, source, at=None, actor=TOOL_ACTOR, via=None,
                                      container=None):
    """API-ból kapott irodalomjegyzék → jelöltek (``role_in_review: unknown``, ``low``, ``needs_review``).

    Az azonosítók itt API-válaszból jönnek (``source`` = az API kulcsa, ``via`` pl. 'europepmc.references'),
    de a tétel attól még CSAK hivatkozás, nem bevonás-állítás (6.2). A kimenet alakja az ``extract_included``-é."""
    at = at or _now()
    via = via or "%s.references" % source
    b = _Builder(None, review_id, at, actor)
    b.container = container
    seen = set()
    for i, rec in enumerate(records or (), 1):
        n = _norm_record(rec, source)
        if not (n["text"] or n["title"] or n["ids"]):
            continue
        key = "api:%s:%s" % (source, n["key"])
        if key in seen:
            continue
        seen.add(key)
        ids = {k: _idval(v, via, at, source=source) for k, v in n["ids"].items()}
        cited = {"text": _clip(n["text"] or n["title"] or n["key"], TEXT_MAX), "first_author": n["first_author"],
                 "year": n["year"], "title": _clip(n["title"], TEXT_MAX) if n["title"] else None,
                 "journal": _clip(n["journal"], 200) if n["journal"] else None}
        label = ("%s %d" % (n["first_author"], n["year"])) if n["first_author"] and n["year"] else None
        cand = b.add(key, _new_cand(cited, label, None, ids, "unknown", "low", "proposed"))
        _reason(cand, "role_unknown")
        e = b.ev("reference_list", "reflist_api", {"label": "%s #%d" % (via, i), "section": "references"},
                 n["text"] or n["title"] or n["key"], "low")
        b.attach(cand, e)
    if b.cands:
        b.use("reflist_api")
    _flag_review_needs(b)
    return _finalize(b, None, None, [], [])


# ---------------------------------------------------------------------------
# beillesztés a review-dokumentumba (idempotens)
# ---------------------------------------------------------------------------

def _ev_sig(e):
    return json.dumps({"kind": e["kind"], "strategy": e["strategy"], "locator": e["locator"], "quote": e["quote"]},
                      sort_keys=True, ensure_ascii=False)


def _max_seq(items, field, rx):
    n = 0
    for it in items:
        m = re.search(rx, it.get(field) or "")
        if m:
            n = max(n, int(m.group(1)))
    return n


def merge_into_review(review, result):
    """Az ``extract_included`` eredményét beilleszti egy review-dokumentumba (új dict-et ad vissza).

    Idempotens: ugyanaz az eredmény másodszor beillesztve nem hoz létre új jelöltet vagy bizonyítékot. A meglévő
    jelölt emberi/feloldási állapota (``status``, ``decision_ids``, ``rec_id``, ``confirmed_by``, ellenőrzött
    másodlagos adat) megmarad; csak új bizonyíték és új (ellenőrizetlen) másodlagos érték kerül hozzá."""
    rv = copy.deepcopy(review)
    rid = rv.get("review_id") or result.get("review_id")
    rv.setdefault("candidates", [])
    rv.setdefault("evidence", [])
    ev_by_sig = {_ev_sig(e): e["evidence_id"] for e in rv["evidence"]}
    next_ev = _max_seq(rv["evidence"], "evidence_id", r"-(\d+)$") + 1
    id_map = {}
    for e in result.get("evidence", []):
        sig = _ev_sig(e)
        if sig in ev_by_sig:
            id_map[e["evidence_id"]] = ev_by_sig[sig]
            continue
        new_id = "ev-%s-%04d" % (rid, next_ev)
        next_ev += 1
        d = dict(e, evidence_id=new_id, review_id=rid)
        rv["evidence"].append(d)
        ev_by_sig[sig] = new_id
        id_map[e["evidence_id"]] = new_id
    by_key = {c.get("extract_key"): c for c in rv["candidates"] if c.get("extract_key")}
    next_c = _max_seq(rv["candidates"], "cand_id", r"^c(\d+)$") + 1
    for c in result.get("candidates", []):
        nc = copy.deepcopy(c)
        nc["evidence_ids"] = [id_map.get(x, x) for x in c.get("evidence_ids", [])]
        for sv in nc.get("secondary_data", []):
            sv["evidence_id"] = id_map.get(sv["evidence_id"], sv["evidence_id"])
        old = by_key.get(c.get("extract_key"))
        if old is None:
            nc["cand_id"] = "c%04d" % next_c
            next_c += 1
            rv["candidates"].append(nc)
            by_key[nc.get("extract_key")] = nc
            continue
        for x in nc["evidence_ids"]:
            if x not in old.setdefault("evidence_ids", []):
                old["evidence_ids"].append(x)
        have = set((s.get("field"), s.get("evidence_id")) for s in old.setdefault("secondary_data", []))
        for sv in nc.get("secondary_data", []):
            if (sv["field"], sv["evidence_id"]) not in have:
                old["secondary_data"].append(sv)
        oids = old.setdefault("ids", {})
        for k, v in nc.get("ids", {}).items():
            if k not in oids:
                oids[k] = v
        if _CONF_RANK.get(nc.get("confidence"), 0) > _CONF_RANK.get(old.get("confidence"), 0) and \
                old.get("status") == "proposed":
            old["confidence"] = nc["confidence"]
        for k in ("ref_hint", "context", "study_registry_in_review"):
            if nc.get(k) and not old.get(k):
                old[k] = nc[k]
    if result.get("excluded_by_review"):
        ex = rv.setdefault("excluded_by_review", [])
        sigs = set((x.get("cited_as"), x.get("group_key")) for x in ex)
        for x in result["excluded_by_review"]:
            if (x.get("cited_as"), x.get("group_key")) in sigs:
                continue
            d = dict(x)
            d["evidence_id"] = id_map.get(x["evidence_id"], x["evidence_id"])
            ex.append(d)
            sigs.add((x.get("cited_as"), x.get("group_key")))
    for fld in ("k_reported", "search_date"):
        val = result.get(fld)
        if val and not (rv.get(fld) or {}).get("evidence_id"):
            d = dict(val)
            d["evidence_id"] = id_map.get(val.get("evidence_id"), val.get("evidence_id"))
            rv[fld] = d
    return rv
