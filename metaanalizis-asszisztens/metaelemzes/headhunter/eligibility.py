# -*- coding: utf-8 -*-
"""Metaheadhunter — L7: jogosultsági szűrés a SAJÁT PICO szerint (TERV_metaheadhunter.md 10. fejezet, EP4) és a
PRISMA 2020 dobozszámok a bányászott (egyéb módszerek) és a frissítő (adatbázis) ágra (13. fejezet).

Kezdőknek: attól, hogy egy korábbi áttekintés bevont egy vizsgálatot, még nem biztos, hogy a TE kérdésedhez is
illik — a te PICO-d eltérhet. Ezért minden egyedi közleményt (rekordot) újra szűrsz: előbb cím/absztrakt
szinten, majd teljes szöveg szinten. A program **javasol** (gépi jelzések a kritériumok ``machine_hint``-jeiből:
publikációtípus, év, nyelv, ember/állat, visszavont közlemény; ágens-javaslat szó szerinti absztrakt-idézettel),
de **ember dönt**; a döntés a ``decisions.jsonl``-ba kerül (``kind: screen``), teljes szöveg szinten kötelező
kizárási okkal (``state.exclusion_reasons``, PRISMA 16a). Kettős szűrésnél (``settings.reviewers``) bírálónként
külön döntés, ütközés-lista, feloldó döntés (``supersedes``), egyezési százalék és Cohen-kappa.

Támogatott ``machine_hint`` kulcsok (egy kritériumon; mind csak JAVASLAT):
``pub_types_exclude`` [..], ``pub_types_include`` [..], ``year_min``, ``year_max``, ``languages`` [engedett],
``languages_exclude`` [..], ``humans_only`` (true), ``title_exclude_any`` [szavak] (alacsony bizonyosság),
``exclude_retracted`` (alapból igaz — a visszavont közlemény mindig kizárási javaslatot kap, H013).

Nyilvános API::

    machine_proposals(records, state, now=None) → [javaslat]
    import_agent_proposals(agent_doc, records, abstract_lookup, now=None) → (elfogadott, elutasított)
    run_screen_propose(project_dir, agent_doc=None, abstract_lookup=None, now=None) → screening.json
    record_screen_decision(project_dir, rec_id, level, value, actor, reason_code=None, reason=None, proposal_id=None)
    import_screening_csv(project_dir, text_or_path, now=None) → {imported, errors}
    screening_status(records, decisions, state=None) → {rec_id: {...}};  agreement(decisions, level, reviewers)
    cohen_kappa(pairs);  screening_list(project_dir) → sorok a CLI/felület számára;  open_items(...)
    branch_counts(studies_doc, reviews, decisions, state=None) → PRISMA-dobozok (kanonikus motor-nevekkel) + ``hh``
"""
from __future__ import absolute_import

import csv
import io
import os
import re

from . import dedup as _d

SCREENING_SCHEMA = "szk.ma.headhunter.screening/v1"
LEVELS = ("title_abstract", "full_text")
VALUES = {"title_abstract": ("include", "exclude", "unclear"),
          "full_text": ("include", "exclude", "awaiting", "not_retrieved")}
TOOL_ACTOR = _d.TOOL_ACTOR
AGENT_DEFAULT = "agent:ma-metaheadhunter"
KB_SCREEN = ["D-S04-104"]
QUOTE_MAX = 300

__all__ = ["machine_proposals", "import_agent_proposals", "run_screen_propose", "record_screen_decision",
           "import_screening_csv", "screening_status", "agreement", "cohen_kappa", "screening_list", "open_items",
           "branch_counts", "verify_quote", "LEVELS", "VALUES"]


def _expl(hu, en):
    return {"hu": hu, "en": en}


def _norm_ws(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def verify_quote(quote, text):
    """Szó szerinti előfordulás (szóközre normalizálva; H004-mintájú ellenőrzés az absztrakton)."""
    q = _norm_ws(quote)
    return bool(q) and q in _norm_ws(text)


# =============================================================================================
# kritériumok, okszótár
# =============================================================================================

def _reason_for(state, criterion_id, keywords=()):
    """A kritériumhoz kötött kizárási ok kódja (``state.exclusion_reasons[].criterion``); ha nincs, a címkében
    kulcsszóval keres; különben ``None`` (az embernek kell választania)."""
    reasons = (state or {}).get("exclusion_reasons") or []
    for r in reasons:
        if criterion_id and r.get("criterion") == criterion_id:
            return r.get("code")
    for r in reasons:
        lab = " ".join(str(v) for v in (r.get("label") or {}).values()).lower()
        if any(k in lab for k in keywords):
            return r.get("code")
    return None


def _reason_label(state, code):
    for r in (state or {}).get("exclusion_reasons") or []:
        if r.get("code") == code:
            lab = r.get("label") or {}
            return lab.get("hu") or lab.get("en") or code
    return code


def _active_records(studies_doc):
    recs = (studies_doc or {}).get("records") or []
    return [r for r in recs if r.get("status") != "merged_into"]


def machine_proposals(records, state, now=None):
    """Gépi szűrési javaslatok (cím/absztrakt szint) a kritériumok ``machine_hint``-jeiből és a visszavonásból.
    Csak kizárási (vagy „bizonytalan") javaslat születik; bevonást a gép nem javasol. Minden javaslat
    ``proposed_by: tool:headhunter``, indoklással és a kritériumra mutató okkóddal (ha a szótárban van)."""
    at = _d.utc_now(now)
    crits = [c for c in (state or {}).get("criteria") or [] if c.get("machine_hint")]
    out = []
    for r in records:
        if r.get("status") == "merged_into":
            continue
        b = r.get("bib") or {}
        fl = r.get("flags") or {}
        pts = [str(x).lower() for x in b.get("pub_types") or []]
        rid = r["rec_id"]

        def add(rule, crit, suggestion, conf, basis, hu, en, keywords=()):
            out.append({"rec_id": rid, "level": "title_abstract", "suggestion": suggestion,
                        "criterion": crit.get("id") if crit else None,
                        "reason_code": _reason_for(state, crit.get("id") if crit else None, keywords)
                        if suggestion == "exclude" else None,
                        "rule": rule, "basis": basis, "confidence": conf, "proposed_by": TOOL_ACTOR,
                        "explanation": _expl(hu, en), "quote": None, "at": at})
        retract_hint = True
        for c in crits:
            if (c.get("machine_hint") or {}).get("exclude_retracted") is False:
                retract_hint = False
        if fl.get("retracted") and retract_hint:
            add("M-retracted", None, "exclude", "high", {"flags.retracted": True},
                "Visszavont közlemény — kizárási javaslat (H013); az okot rögzítsd.",
                "Retracted publication — proposed for exclusion (H013); record the reason.",
                keywords=("visszavon", "retract"))
        for c in crits:
            h = c.get("machine_hint") or {}
            if c.get("type") == "include" and h.get("pub_types_include"):
                want = [str(x).lower() for x in h["pub_types_include"]]
                if pts and not any(w in pts for w in want):
                    add("M-pub-type-include", c, "exclude", "medium", {"pub_types": b.get("pub_types")},
                        "A publikációtípus (%s) nem szerepel a megengedettek (%s) között (%s)."
                        % (", ".join(b.get("pub_types") or []), ", ".join(h["pub_types_include"]), c.get("id")),
                        "Publication type (%s) is not among the allowed ones (%s) (%s)."
                        % (", ".join(b.get("pub_types") or []), ", ".join(h["pub_types_include"]), c.get("id")))
            if h.get("pub_types_exclude"):
                bad = [x for x in h["pub_types_exclude"] if str(x).lower() in pts]
                if bad:
                    add("M-pub-type", c, "exclude", "high", {"pub_types": bad},
                        "Publikációtípus: %s — a(z) %s kritérium szerint kizárási javaslat." % (", ".join(bad), c.get("id")),
                        "Publication type: %s — proposed for exclusion under criterion %s." % (", ".join(bad), c.get("id")))
            y = b.get("year")
            if isinstance(y, int):
                if h.get("year_min") is not None and y < int(h["year_min"]):
                    add("M-year", c, "exclude", "high", {"year": y, "year_min": h["year_min"]},
                        "Megjelenési év %d < %s (%s)." % (y, h["year_min"], c.get("id")),
                        "Publication year %d < %s (%s)." % (y, h["year_min"], c.get("id")))
                if h.get("year_max") is not None and y > int(h["year_max"]):
                    add("M-year", c, "exclude", "high", {"year": y, "year_max": h["year_max"]},
                        "Megjelenési év %d > %s (%s)." % (y, h["year_max"], c.get("id")),
                        "Publication year %d > %s (%s)." % (y, h["year_max"], c.get("id")))
            lang = str(b.get("language") or "").lower()
            if lang:
                if h.get("languages") and lang not in [str(x).lower() for x in h["languages"]]:
                    add("M-language", c, "exclude", "high", {"language": lang},
                        "Nyelv: %s — nem szerepel az engedettek (%s) között (%s)."
                        % (lang, ", ".join(h["languages"]), c.get("id")),
                        "Language: %s — not among the allowed ones (%s) (%s)." % (lang, ", ".join(h["languages"]), c.get("id")))
                if h.get("languages_exclude") and lang in [str(x).lower() for x in h["languages_exclude"]]:
                    add("M-language", c, "exclude", "high", {"language": lang},
                        "Nyelv: %s — kizárt nyelv (%s)." % (lang, c.get("id")),
                        "Language: %s — excluded language (%s)." % (lang, c.get("id")))
            if h.get("humans_only") and fl.get("animal_only"):
                add("M-animal", c, "exclude", "high", {"mesh": "Animals (Humans nélkül)"},
                    "A MeSH-indexelés szerint állatkísérlet (Animals, Humans nélkül) — %s." % c.get("id"),
                    "MeSH indexing indicates an animal study (Animals without Humans) — %s." % c.get("id"))
            if h.get("title_exclude_any") and b.get("title"):
                t = " %s " % _d.norm_text(b["title"])
                hits = [w for w in h["title_exclude_any"] if " %s " % _d.norm_text(w) in t]
                if hits:
                    add("M-title-terms", c, "unclear", "low", {"title_terms": hits},
                        "A cím a kizáró kifejezések egyikét tartalmazza (%s) — ellenőrizd (%s)."
                        % (", ".join(hits), c.get("id")),
                        "The title contains an excluding term (%s) — check it (%s)." % (", ".join(hits), c.get("id")))
    out.sort(key=lambda p: (p["rec_id"], p["level"], p["rule"], p.get("criterion") or ""))
    return out


# =============================================================================================
# ágens-javaslatok (idézettel, azonosító nélkül)
# =============================================================================================

_ID_KEYS = ("pmid", "doi", "pmcid", "pmc", "nct", "eid", "openalex", "ids", "identifiers", "registry", "isrctn")


def import_agent_proposals(agent_doc, records, abstract_lookup, now=None):
    """Ágens szűrési javaslatainak ellenőrzött beolvasása.

    ``agent_doc``: ``{"actor": "agent:…", "items": [{"rec_id", "level", "suggestion", "criterion", "reason_code",
    "quote", "rationale"}]}``. Szabályok: a szereplő ``agent:``; a rekord létezik; a szint/javaslat érvényes; az
    idézet ≤ 300 karakter és SZÓ SZERINT megtalálható a rekord absztraktjában (``abstract_lookup(rekord)`` →
    szöveg|None, élőben lekérve, csak memóriában); minden azonosító-mező eldobva (N1). Visszaad:
    ``(elfogadott javaslatok, elutasított tételek okkal)``."""
    at = _d.utc_now(now)
    actor = str((agent_doc or {}).get("actor") or AGENT_DEFAULT)
    accepted, rejected = [], []
    if not actor.startswith("agent:"):
        return [], [{"index": None, "code": "actor", "hu": "Az ágens-javaslat szereplője 'agent:<név>' legyen.",
                     "en": "The agent proposal actor must be 'agent:<name>'."}]
    recs = dict((r["rec_id"], r) for r in records)
    cmap = _d.canonical_map(records)
    cache = {}
    for i, item in enumerate((agent_doc or {}).get("items") or []):
        if not isinstance(item, dict):
            rejected.append({"index": i, "code": "shape", "hu": "Nem objektum.", "en": "Not an object."})
            continue
        dropped = sorted(k for k in item if k in _ID_KEYS)
        rid = cmap.get(item.get("rec_id"), item.get("rec_id"))
        level = item.get("level") or "title_abstract"
        sug = item.get("suggestion")
        if rid not in recs:
            rejected.append({"index": i, "code": "unknown_record", "hu": "Ismeretlen rekord: %s" % item.get("rec_id"),
                             "en": "Unknown record: %s" % item.get("rec_id")})
            continue
        if level not in LEVELS or sug not in VALUES[level]:
            rejected.append({"index": i, "code": "value", "hu": "Érvénytelen szint vagy javaslat.",
                             "en": "Invalid level or suggestion."})
            continue
        quote = item.get("quote")
        if quote is not None:
            if len(quote) > QUOTE_MAX:
                rejected.append({"index": i, "code": "quote_too_long", "hu": "Az idézet hosszabb 300 karakternél (N4).",
                                 "en": "The quote is longer than 300 characters (N4)."})
                continue
            if rid not in cache:
                try:
                    cache[rid] = abstract_lookup(recs[rid]) if abstract_lookup else None
                except Exception:  # elérhetetlen forrás: az idézet nem ellenőrizhető
                    cache[rid] = None
            if not cache[rid] or not verify_quote(quote, cache[rid]):
                rejected.append({"index": i, "code": "H004",
                                 "hu": "Az idézet nem található szó szerint a rekord absztraktjában — elutasítva (H004).",
                                 "en": "The quote is not found verbatim in the record's abstract — rejected (H004).",
                                 "rec_id": rid})
                continue
        reason_code = item.get("reason_code")
        if reason_code is not None and not re.match(r"^X\d{1,3}$", str(reason_code)):
            reason_code = None
        rationale = _norm_ws(item.get("rationale"))[:600] or None
        accepted.append({"rec_id": rid, "level": level, "suggestion": sug, "criterion": item.get("criterion"),
                         "reason_code": reason_code, "rule": "A-agent", "basis": {"dropped_id_fields": dropped}
                         if dropped else {}, "confidence": "medium", "proposed_by": actor,
                         "explanation": _expl(rationale or "Ágens-javaslat.", rationale or "Agent proposal."),
                         "quote": _norm_ws(quote) if quote else None, "at": at})
    return accepted, rejected


# =============================================================================================
# döntések
# =============================================================================================

def _screen_key(d):
    t = d.get("target") or {}
    return (t.get("id"), d.get("level"), d.get("actor"))


def screening_status(records, decisions, state=None):
    """Rekordonként és szintenként a hatályos szűrési döntés. Egyes szűrésnél a legutóbbi emberi döntés; kettős
    szűrésnél (``settings.reviewers`` ≥ 2) a bírálók egyező döntése, ütközésnél ``conflict``, amíg egy feloldó
    (``supersedes``-zel) döntés nem születik. Visszaad:
    ``{rec_id: {level: {"value", "decision_id", "reason_code", "status": decided|conflict|partial|pending}}}``."""
    cmap = _d.canonical_map(records)
    reviewers = [r for r in (((state or {}).get("settings") or {}).get("reviewers") or []) if r]
    dual = len(reviewers) >= 2
    per = {}
    for i, d in enumerate(decisions or []):
        if d.get("kind") != "screen" or (d.get("target") or {}).get("type") != "record":
            continue
        if not str(d.get("actor") or "").startswith("user:"):
            continue
        rid = cmap.get(d["target"]["id"], d["target"]["id"])
        lvl = d.get("level")
        if lvl not in LEVELS:
            continue
        dd = dict(d)
        dd["_order"] = i
        per.setdefault((rid, lvl), []).append(dd)
    out = {}
    for r in records:
        if r.get("status") == "merged_into":
            continue
        out[r["rec_id"]] = {}
        for lvl in LEVELS:
            ds = per.get((r["rec_id"], lvl), [])
            superseded = set(x.get("supersedes") for x in ds if x.get("supersedes"))
            ds = [x for x in ds if x.get("decision_id") not in superseded]
            st = {"value": None, "decision_id": None, "reason_code": None, "status": "pending", "by": {}}
            if ds:
                resolving = [x for x in ds if x.get("supersedes")]
                latest_by = {}
                for x in ds:
                    latest_by[x["actor"]] = x
                st["by"] = dict((a, x["value"]) for a, x in sorted(latest_by.items()))
                if resolving:
                    fin = resolving[-1]
                    st.update(value=fin["value"], decision_id=fin["decision_id"], reason_code=fin.get("reason_code"),
                              status="decided")
                elif not dual:
                    fin = ds[-1]
                    st.update(value=fin["value"], decision_id=fin["decision_id"], reason_code=fin.get("reason_code"),
                              status="decided")
                else:
                    vals = [latest_by[a] for a in reviewers if a in latest_by]
                    if len(vals) < len(reviewers):
                        st["status"] = "partial"
                    elif len(set(x["value"] for x in vals)) == 1:
                        fin = vals[-1]
                        rc = next((x.get("reason_code") for x in vals if x.get("reason_code")), None)
                        st.update(value=fin["value"], decision_id=fin["decision_id"], reason_code=rc, status="decided")
                    else:
                        st["status"] = "conflict"
            out[r["rec_id"]][lvl] = st
    return out


def cohen_kappa(pairs):
    """Cohen-kappa két bíráló döntéspárjaira (``[(a, b)]``). Visszaad: ``(egyezés%, kappa|None, n)``;
    a kappa nem értelmezhető (``None``), ha a várható egyezés 1."""
    n = len(pairs)
    if n == 0:
        return None, None, 0
    po = sum(1 for a, b in pairs if a == b) / float(n)
    cats = sorted(set([a for a, _ in pairs] + [b for _, b in pairs]))
    pe = 0.0
    for c in cats:
        pa = sum(1 for a, _ in pairs if a == c) / float(n)
        pb = sum(1 for _, b in pairs if b == c) / float(n)
        pe += pa * pb
    kappa = None if abs(1.0 - pe) < 1e-12 else (po - pe) / (1.0 - pe)
    return round(100.0 * po, 1), (round(kappa, 3) if kappa is not None else None), n


def agreement(decisions, level, reviewers, records=None):
    """Két bíráló egyezése egy szinten (a riporthoz): ``{reviewers, n, percent, kappa, conflicts}``."""
    if len(reviewers or []) < 2:
        return {"reviewers": list(reviewers or []), "n": 0, "percent": None, "kappa": None, "conflicts": []}
    a, b = reviewers[0], reviewers[1]
    cmap = _d.canonical_map(records or [])
    latest = {}
    for d in decisions or []:
        if d.get("kind") == "screen" and d.get("level") == level and d.get("actor") in (a, b) and \
                (d.get("target") or {}).get("type") == "record":
            rid = cmap.get(d["target"]["id"], d["target"]["id"])
            latest[(rid, d["actor"])] = d.get("value")
    pairs, conflicts = [], []
    for rid in sorted(set(k[0] for k in latest)):
        if (rid, a) in latest and (rid, b) in latest:
            pairs.append((latest[(rid, a)], latest[(rid, b)]))
            if latest[(rid, a)] != latest[(rid, b)]:
                conflicts.append(rid)
    pct, kappa, n = cohen_kappa(pairs)
    return {"reviewers": [a, b], "n": n, "percent": pct, "kappa": kappa, "conflicts": conflicts}


def record_screen_decision(project_dir, rec_id, level, value, actor, reason_code=None, reason=None, proposal_id=None,
                           supersedes=None, now=None, evidence_ids=()):
    """EP4 döntés rögzítése (ember: ``user:…``). Teljes szöveg szintű kizárásnál kötelező az ok (a szótár kódja,
    vagy — ha a projektnek nincs okszótára — szöveges indoklás). Ha ``proposal_id`` adott (a ``screening.json``
    javaslata), a döntés a javaslatot fogadja el/bírálja felül: ``proposed_by`` és az idézet átkerül."""
    if not str(actor or "").startswith("user:"):
        raise _d.DecisionError("A szűrési döntést ember hozza ('user:<név>'); az ágens csak javasol (N3).")
    if level not in LEVELS:
        raise _d.DecisionError("A szint 'title_abstract' vagy 'full_text'.")
    if value not in VALUES[level]:
        raise _d.DecisionError("Ezen a szinten a döntés: %s." % ", ".join(VALUES[level]))
    hd = _d.hh_dir(project_dir)
    state = _d.read_json(os.path.join(hd, "state.json"), {}) or {}
    studies = _d.load_studies(project_dir) or {}
    recs = studies.get("records") or []
    cmap = _d.canonical_map(recs)
    if rec_id not in cmap:
        raise _d.DecisionError("Ismeretlen rekord: %s" % rec_id)
    rec_id = cmap[rec_id]
    codes = [r.get("code") for r in state.get("exclusion_reasons") or []]
    if reason_code is not None and codes and reason_code not in codes:
        raise _d.DecisionError("Ismeretlen kizárási ok: %s (a szótárban: %s)." % (reason_code, ", ".join(codes)))
    if level == "full_text" and value == "exclude":
        if codes and not reason_code:
            raise _d.DecisionError("Teljes szöveg szintű kizárásnál kötelező az ok (--reason-code X…; PRISMA 16a).")
        if not codes and not (reason_code or (reason and reason.strip())):
            raise _d.DecisionError("Teljes szöveg szintű kizárásnál kötelező az indoklás.")
    proposed_by, quote = None, None
    if proposal_id:
        scr = _d.read_json(os.path.join(hd, "screening.json"), {}) or {}
        prop = next((p for p in (scr.get("proposals") or []) + (scr.get("agent_proposals") or [])
                     if p.get("proposal_id") == proposal_id), None)
        if prop is None:
            raise _d.DecisionError("Nincs ilyen szűrési javaslat: %s" % proposal_id)
        proposed_by, quote = prop.get("proposed_by"), prop.get("quote")
    return _d.append_decision(project_dir, "screen", ("record", rec_id), value, actor, now=now, state=state,
                              level=level, reason_code=reason_code, reason=reason, proposed_by=proposed_by,
                              quote=quote, kb_refs=KB_SCREEN, supersedes=supersedes, evidence_ids=evidence_ids)


def _unsafe(x):
    s = "" if x is None else str(x)
    return s[1:] if s[:1] == "'" and s[1:2] in ("=", "+", "-", "@", "\t", "\r") else s


def import_screening_csv(project_dir, text_or_path, now=None, default_actor=None):
    """Szűrési döntések visszatöltése (Rayyan/Covidence után): ``rec_id;level;decision;reason_code;actor``
    (fejléc kötelező; az ``actor`` ``user:`` előtagú). ``default_actor``: az importot végző ember (``user:…``) — az
    üres ``actor`` cellájú sorok döntéshozója (kettős szűrésnél a sorok saját szűrőjüket nevezik meg). Visszaad:
    ``{imported: [decision_id…], errors: [...]}``; hibás sor nem kerül be (a többi igen)."""
    if os.path.exists(str(text_or_path)):
        with open(text_or_path, encoding="utf-8-sig") as fh:
            text = fh.read()
    else:
        text = str(text_or_path)
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    out = {"imported": [], "errors": []}
    if not rows:
        return out
    header = [h.strip().lower() for h in rows[0]]
    need = ["rec_id", "level", "decision", "reason_code", "actor"]
    if header[:5] != need:
        out["errors"].append({"line": 1, "hu": "A fejléc: %s" % ";".join(need), "en": "Header must be: %s" % ";".join(need)})
        return out
    for n, row in enumerate(rows[1:], 2):
        if not any(x.strip() for x in row):
            continue
        row = (row + [""] * 5)[:5]
        # az export képlet-védelme (merge.excel_safe) visszafordítva: „'=…” → „=…”
        rid, level, dec, rc, actor = [_unsafe(x).strip() for x in row]
        actor = actor or (default_actor or "")
        try:
            d = record_screen_decision(project_dir, rid, level, dec, actor, reason_code=rc or None, now=now)
            out["imported"].append(d["decision_id"])
        except _d.DecisionError as exc:
            out["errors"].append({"line": n, "hu": str(exc), "en": str(exc)})
    return out


# =============================================================================================
# javaslat-fájl (screening.json) és lista
# =============================================================================================

def _assign(props, prior, prefix):
    """Stabil ``p-scr-…`` / ``p-agt-…`` azonosítók (rekord, szint, szabály, kritérium, javaslattevő szerint)."""
    def sig(p):
        return "|".join(str(p.get(k) or "") for k in ("rec_id", "level", "rule", "criterion", "proposed_by"))
    prior_by = {}
    seq = 0
    for p in prior or []:
        prior_by.setdefault(sig(p), p.get("proposal_id"))
        m = re.match(r"^p-%s-(\d+)$" % prefix, str(p.get("proposal_id") or ""))
        if m:
            seq = max(seq, int(m.group(1)))
    used = set()
    fresh = []
    for p in props:
        pid = prior_by.get(sig(p))
        if pid and pid not in used:
            p["proposal_id"] = pid
            used.add(pid)
        else:
            fresh.append(p)
    for p in sorted(fresh, key=sig):
        seq += 1
        p["proposal_id"] = "p-%s-%04d" % (prefix, seq)
    return sorted(props, key=lambda p: p["proposal_id"]), seq


def run_screen_propose(project_dir, agent_doc=None, abstract_lookup=None, now=None):
    """L7 javaslatok: a ``screening.json`` (``szk.ma.headhunter.screening/v1`` — additív új fájl) gépi és ágens-
    javaslatokkal, a hatályos döntések állapotával és az EP4 nyitott tételeivel. Döntést NEM hoz."""
    hd = _d.hh_dir(project_dir)
    with _d.ProjectLock(project_dir):
        state = _d.read_json(os.path.join(hd, "state.json"), {}) or {}
        studies = _d.load_studies(project_dir) or {}
        prior = _d.read_json(os.path.join(hd, "screening.json"), {}) or {}
        decisions = _d.read_decisions(project_dir)
        records = studies.get("records") or []
        active = _active_records(studies)
        mprops, _seq = _assign(machine_proposals(active, state, now=now), prior.get("proposals"), "scr")
        agent_props = list(prior.get("agent_proposals") or [])
        rejected = list(prior.get("rejected_agent_items") or [])
        if agent_doc is not None:
            acc, rej = import_agent_proposals(agent_doc, records, abstract_lookup, now=now)
            keep = [p for p in agent_props if (p["rec_id"], p["level"], p["proposed_by"]) not in
                    set((a["rec_id"], a["level"], a["proposed_by"]) for a in acc)]
            agent_props = keep + acc
            rejected = rej
        agent_props, _aseq = _assign(agent_props, prior.get("agent_proposals"), "agt")
        status = screening_status(records, decisions, state)
        for p in mprops + agent_props:
            st = (status.get(p["rec_id"]) or {}).get(p["level"]) or {}
            p["status"] = "decided" if st.get("status") == "decided" else "pending"
            p["decision_id"] = st.get("decision_id")
        reviewers = (((state.get("settings") or {}).get("reviewers")) or [])
        doc = {"schema": SCREENING_SCHEMA, "model": _d.MODEL, "generated": _d.utc_now(now),
               "proposals": mprops, "agent_proposals": agent_props, "rejected_agent_items": rejected,
               "agreement": dict((lvl, agreement(decisions, lvl, reviewers, records)) for lvl in LEVELS)
               if len(reviewers) >= 2 else None,
               "summary": _screen_summary(active, status, mprops, agent_props),
               "kb_refs": list(KB_SCREEN)}
        _d.write_json_atomic(os.path.join(hd, "screening.json"), doc)
    open_n = doc["summary"]["open_title_abstract"] + doc["summary"]["open_full_text"]
    return {"ok": True, "summary": doc["summary"], "rejected_agent_items": rejected,
            "pending": [{"checkpoint": "EP4", "n": open_n}] if open_n else [],
            "exit_code": 4 if open_n else 0, "next": "decide" if open_n else "update-search"}


def _screen_summary(active, status, mprops, aprops):
    s = {"records": len(active), "machine_proposals": len(mprops), "agent_proposals": len(aprops),
         "open_title_abstract": 0, "open_full_text": 0, "conflicts": 0}
    for r in active:
        st = status.get(r["rec_id"]) or {}
        ta = st.get("title_abstract") or {}
        ft = st.get("full_text") or {}
        if ta.get("status") == "conflict" or ft.get("status") == "conflict":
            s["conflicts"] += 1
        if ta.get("status") != "decided":
            s["open_title_abstract"] += 1
        elif ta.get("value") in ("include", "unclear") and ft.get("status") != "decided":
            s["open_full_text"] += 1
    return s


def open_items(studies_doc, decisions, state=None):
    """EP4 nyitott tételei: ``{title_abstract: [rec_id], full_text: [rec_id], conflicts: [rec_id]}``."""
    records = (studies_doc or {}).get("records") or []
    status = screening_status(records, decisions, state)
    out = {"title_abstract": [], "full_text": [], "conflicts": []}
    for rid in sorted(status):
        ta, ft = status[rid]["title_abstract"], status[rid]["full_text"]
        if "conflict" in (ta["status"], ft["status"]):
            out["conflicts"].append(rid)
        if ta["status"] != "decided":
            out["title_abstract"].append(rid)
        elif ta["value"] in ("include", "unclear") and ft["status"] != "decided":
            out["full_text"].append(rid)
    return out


def screening_list(project_dir):
    """Sorok a CLI ``screen list`` és a felület számára: rekord, címke, cím, év, folyóirat, eredet (mely
    áttekintések vonták be — kontextus, nem jogosultság), javaslatok, döntések, állapot."""
    hd = _d.hh_dir(project_dir)
    state = _d.read_json(os.path.join(hd, "state.json"), {}) or {}
    studies = _d.load_studies(project_dir) or {}
    reviews = _d.load_reviews(project_dir)
    scr = _d.read_json(os.path.join(hd, "screening.json"), {}) or {}
    decisions = _d.read_decisions(project_dir)
    records = studies.get("records") or []
    status = screening_status(records, decisions, state)
    origins = _d.effective_origins(records)
    cidx = _d.candidate_index(reviews, selected_only=False)
    cmap = _d.canonical_map(records)
    by_canon = {}
    for rid, lst in cidx.items():
        for rv, c in lst:
            if c.get("role_in_review") in _d.INCLUDED_ROLES:
                by_canon.setdefault(cmap.get(rid, rid), set()).add(rv)
    props = {}
    for p in (scr.get("proposals") or []) + (scr.get("agent_proposals") or []):
        props.setdefault(p["rec_id"], []).append(p)
    study_of = {}
    for s in studies.get("studies") or []:
        for rp in s.get("reports") or []:
            study_of[rp["rec_id"]] = (s["study_id"], s.get("label"), rp.get("role"))
    rows = []
    for r in _active_records(studies):
        b = r.get("bib") or {}
        st = status.get(r["rec_id"]) or {}
        sid = study_of.get(r["rec_id"], (None, None, None))
        rows.append({"rec_id": r["rec_id"], "study_id": sid[0], "study_label": sid[1], "role": sid[2],
                     "title": b.get("title") or (r.get("cited_as") or {}).get("text"), "year": b.get("year"),
                     "journal": b.get("journal"), "first_author": b.get("first_author"),
                     "routes": sorted(set(o.get("route") for o in origins.get(r["rec_id"], []))),
                     "included_by_reviews": sorted(by_canon.get(r["rec_id"], set())),
                     "flags": sorted(k for k, v in (r.get("flags") or {}).items() if v),
                     "proposals": [{"proposal_id": p["proposal_id"], "level": p["level"],
                                    "suggestion": p["suggestion"], "reason_code": p.get("reason_code"),
                                    "proposed_by": p["proposed_by"], "rule": p["rule"],
                                    "explanation": p.get("explanation")} for p in props.get(r["rec_id"], [])],
                     "title_abstract": st.get("title_abstract"), "full_text": st.get("full_text")})
    return rows


# =============================================================================================
# PRISMA 2020 dobozszámok (13. fejezet) — kanonikus motor-nevekkel
# =============================================================================================

_OTHER_ROUTES = ("review_extraction", "citation_search", "manual")


def branch_counts(studies_doc, reviews, decisions, state=None, update_doc=None):
    """A két ág dobozai a szűrési döntésekből, a ``metaelemzes.prisma.COUNT_FIELDS`` kanonikus neveivel.

    * **Egyéb módszerek** (előző áttekintések + hivatkozáskövetés + kézi): az egyedi (kanonikus, az elfogadott
      összevonások utáni) közlemény-rekordok, amelyeket egy kiválasztott áttekintés nem elutasított, bevonás-szerepű
      jelöltje, a hivatkozáskövetés vagy kézi felvétel hozott. ``other_methods_sought`` = azonosított − cím/absztrakt
      alapján kizárt (ez utóbbi a PRISMA-ábrán nem doboz → ``hh.other_methods_title_excluded``);
      ``_not_retrieved`` / ``_assessed`` (= keresett − nem elérhető) / ``_excluded`` (+ okok ``{ok: n}``).
    * **Adatbázis-ág** (frissítő keresés): a csak ``update_search`` eredetű rekordok (az egyéb ágban már ismert
      rekord a duplumszűrésnél kiesik — ``hh.already_known``): ``screened``, ``excluded_screening``, ``sought``,
      ``not_retrieved``, ``assessed``, ``excluded_eligibility`` (+ okok). Az azonosítás (A1/A2) és a duplikátum
      (D1) a frissítő keresés (``update_search.json``) dolga; ha ``update_doc`` adott, onnan vesszük.
    * ``included_reports`` (J) = a két ág teljes szöveg szinten bevont jelentései; ``included_studies`` (I) = azok
      a vizsgálat-klaszterek, amelyeknek ≥ 1 bevont jelentése van; ``awaiting`` = a még el nem bírált (függő vagy
      „elbírálásra vár") jelentések (így a motor egyenletei — J + várakozó = G − H + egyéb ág — a folyamat közben
      is teljesülnek).

    Visszaad: ``{"flow": {kanonikus kulcs: szám…, okok…}, "hh": {részletek}, "complete": bool}``."""
    records = (studies_doc or {}).get("records") or []
    origins = _d.effective_origins(records)
    cmap = _d.canonical_map(records)
    status = screening_status(records, decisions, state)
    sel = set(r["review_id"] for r in _d._selected_reviews(reviews)[0])
    counting = {}
    citations_total = 0
    per_review = {}
    pending_candidates = 0
    for r in reviews:
        if r["review_id"] not in sel:
            continue
        for c in r.get("candidates") or []:
            if c.get("status") == "rejected":
                continue
            if c.get("role_in_review") in _d.INCLUDED_ROLES:
                citations_total += 1
                per_review[r["review_id"]] = per_review.get(r["review_id"], 0) + 1
                if c.get("rec_id"):
                    counting.setdefault(cmap.get(c["rec_id"], c["rec_id"]), set()).add(r["review_id"])
            elif c.get("role_in_review") == "unknown" and c.get("status") == "proposed":
                pending_candidates += 1
    other, db = [], []
    already_known = 0
    for r in records:
        if r.get("status") == "merged_into":
            continue
        rid = r["rec_id"]
        routes = set(o.get("route") for o in origins.get(rid, []))
        in_other = rid in counting or bool(routes & set(["citation_search", "manual"]))
        if in_other:
            other.append(rid)
            if "update_search" in routes:
                already_known += 1
        elif "update_search" in routes:
            db.append(rid)

    def tally(ids):
        t = {"identified": len(ids), "ta_excluded": 0, "ta_pending": 0, "not_retrieved": 0, "ft_excluded": 0,
             "ft_included": 0, "ft_awaiting": 0, "ft_pending": 0, "reasons": {}, "included_ids": []}
        for rid in ids:
            st = status.get(rid) or {}
            ta = st.get("title_abstract") or {}
            ft = st.get("full_text") or {}
            if ta.get("status") != "decided":
                t["ta_pending"] += 1
                continue
            if ta.get("value") == "exclude":
                t["ta_excluded"] += 1
                continue
            if ft.get("status") != "decided":
                t["ft_pending"] += 1
                continue
            v = ft.get("value")
            if v == "not_retrieved":
                t["not_retrieved"] += 1
            elif v == "exclude":
                t["ft_excluded"] += 1
                lab = _reason_label(state, ft.get("reason_code")) if ft.get("reason_code") else "ok nélkül"
                t["reasons"][lab] = t["reasons"].get(lab, 0) + 1
            elif v == "include":
                t["ft_included"] += 1
                t["included_ids"].append(rid)
            else:
                t["ft_awaiting"] += 1
        return t

    to, td = tally(other), tally(db)
    flow = {}
    if to["identified"]:
        sought = to["identified"] - to["ta_excluded"]
        flow.update({
            "other_methods_identified": to["identified"],
            "other_methods_sought": sought,
            "other_methods_not_retrieved": to["not_retrieved"],
            "other_methods_assessed": sought - to["not_retrieved"],
            "other_methods_excluded": to["ft_excluded"],
            "other_methods_excluded_reasons": dict(sorted(to["reasons"].items())),
        })
    if td["identified"] or update_doc:
        screened = td["identified"]
        sought = screened - td["ta_excluded"]
        flow.update({
            "screened": screened,
            "excluded_screening": td["ta_excluded"],
            "sought": sought,
            "not_retrieved": td["not_retrieved"],
            "assessed": sought - td["not_retrieved"],
            "excluded_eligibility": td["ft_excluded"],
            "excluded_eligibility_reasons": dict(sorted(td["reasons"].items())),
        })
        if update_doc:
            res = update_doc.get("results") or {}
            flow["duplicates_removed"] = int(res.get("duplicates_within") or 0) + int(res.get("already_known") or 0)
    no_db_branch = not td["identified"] and not update_doc and not any(
        "update_search" in set(o.get("route") for o in origins.get(r["rec_id"], [])) for r in records)
    if no_db_branch:
        # nincs frissítő keresés: az adatbázis-ág üres (0), hogy a motor egyenletei teljesüljenek
        for k in ("identified_databases", "identified_registers", "duplicates_removed", "screened",
                  "excluded_screening", "sought", "not_retrieved", "assessed", "excluded_eligibility"):
            flow[k] = 0
    awaiting = sum(t[k] for t in (to, td) for k in ("ta_pending", "ft_pending", "ft_awaiting"))
    included_ids = sorted(set(to["included_ids"] + td["included_ids"]))
    flow["included_reports"] = len(included_ids)
    study_of = {}
    for s in (studies_doc or {}).get("studies") or []:
        for rp in s.get("reports") or []:
            study_of[rp["rec_id"]] = s["study_id"]
    flow["included_studies"] = len(set(study_of.get(r, r) for r in included_ids))
    if awaiting:
        flow["awaiting"] = awaiting
    hh = {
        "other_methods_title_excluded": to["ta_excluded"],
        "citations_total": citations_total,
        "citations_per_review": dict(sorted(per_review.items())),
        "already_known": already_known,
        "pending": {"other": {"title_abstract": to["ta_pending"], "full_text": to["ft_pending"],
                              "awaiting": to["ft_awaiting"]},
                    "database": {"title_abstract": td["ta_pending"], "full_text": td["ft_pending"],
                                 "awaiting": td["ft_awaiting"]}},
        "pending_candidates": pending_candidates,
        "included_rec_ids": included_ids,
        "no_database_branch": no_db_branch,
    }
    return {"flow": flow, "hh": hh, "complete": awaiting == 0 and pending_candidates == 0}
