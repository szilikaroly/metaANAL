# -*- coding: utf-8 -*-
"""Metaheadhunter — L9: egyesítés (``merged.json``), lezárás (EP5), másodlagos adatok ellenőrzése (EP6) és az
exportok (TERV_metaheadhunter.md 11. fejezet).

Kezdőknek: a több áttekintésből kinyert és a frissítő keresésben talált közleményeket a ``dedup`` lépés
vizsgálatokba rendezte (egy vizsgálat = egy kutatás, akárhány közleménnyel). Ez a lépés ebből EGY nagy,
egyesített vizsgálatlistát készít:

* **vizsgálatonként egy tétel**: közlemények szereppel (fő közlemény, társközlemény, protokoll …),
  regiszterszámok, **proveniencia** (mely forrás-áttekintések vonták be, milyen címkével, mely bizonyíték —
  táblázat/szakasz + szó szerinti idézet — alapján), honnan került elő (``previous_reviews``,
  ``citation_search``, ``update_search``, ``manual``), a te szűrési döntéseid (cím/absztrakt, teljes szöveg);
* **másodlagos adatok** áttekintésenként külön (soha nem átlagolva): amit egy áttekintés táblázata közölt egy
  vizsgálatról (n, esemény, átlag, SD …). Ezek ``unverified`` jelölésűek, amíg az elsődleges közleménnyel össze
  nem veted (EP6, ``verify-secondary``); elemzési táblába (``03_adatok/<kimenet>.csv``) ellenőrizetlenül SOHA
  nem kerülnek (N2, H010). Ha két áttekintés ugyanarról eltérő számot közöl → ``conflicts[]`` (H018);
* **lezárás** (EP5, ``signoff``): csak akkor, ha nincs nyitott döntés (EP1–EP4) és a PRISMA-számok átmennek a
  motor ellenőrzésén; ezután ``final: true``. Ha a lezárás után bármi változik, a lezárás érvényét veszti
  (H017), és újra le kell zárni.

Exportok (``exports/``): ``studies.ma.json`` (``szk.ma.studies/v1``), ``<kimenet>_sablon.csv`` (az adatkinyerő
sablon oszlopai + azonosítók, proveniencia, ellenőrzés), ``masodlagos_adatok.csv`` (EP6 munkalista),
``records.ris`` (Rayyan/Covidence/EndNote), ``screening.csv`` (szűrési munkalista, visszatölthető). A
``03_adatok/`` és a ``02_szures/`` mappába csak kérésre (``--to-project``/``--prisma``), és csak ha a célfájl még
nem létezik (felülírás csak ``--force``-szal és naplózott döntéssel).

Nyilvános API::

    effective_reviews(reviews, decisions) → áttekintések a döntésekkel frissítve
    build_merged(state, reviews, studies_doc, decisions, update_doc=None, now=None) → merged.json dokumentum
    run_merge(project_dir, now=None) → eredmény (merged.json + prisma_flow.json)
    signoff(project_dir, actor, now=None) → eredmény (EP5)
    verify_secondary(project_dir, study_id, field, status, actor, review_id=None, ...) → döntés (EP6)
    outcome_rows(merged, outcome=None, for_analysis=False) → (fejléc, sorok)
    secondary_rows(merged) ; ris_text(merged, studies_doc) ; screening_rows(merged, studies_doc)
    run_export(project_dir, outcome=None, to_project=False, prisma=False, for_analysis=False, force=False, actor=None)
"""
from __future__ import absolute_import

import copy
import csv
import hashlib
import io
import json
import os
import re
import unicodedata

from . import state as S

MERGED_SCHEMA = "szk.ma.headhunter.merged/v1"
STUDIES_MA_SCHEMA = "szk.ma.studies/v1"
INCLUDED_ROLES = ("included", "included_companion")
FOUND_BY = (("review_extraction", "previous_reviews"), ("citation_search", "citation_search"),
            ("update_search", "update_search"), ("manual", "manual"))
OTHER_ROUTES = ("review_extraction", "citation_search", "manual")
KB_MERGE = ["D-S04-104", "D-S05-101"]
KB_SIGNOFF = ["D-S04-104", "D-S14-101"]
KB_SECONDARY = ["D-S05-101", "D-S05-102"]

#: az adatkinyerő sablon oszlopai (tudasbazis/sablonok/adatkinyero_sablon.csv)
TEMPLATE_COLUMNS = ("study", "study_id", "year", "subgroup", "design", "rob", "estimated", "m1", "sd1", "n1", "m2",
                    "sd2", "n2", "e1", "e2", "egyseg", "meroeszkoz", "idopont", "elemzesi_populacio",
                    "forras_oldal", "megjegyzes")
NUMERIC_TEMPLATE = ("m1", "sd1", "n1", "m2", "sd2", "n2", "e1", "e2")
#: a kimenet-sablon (03_adatok/<kimenet>.csv) további oszlopai
OUTCOME_EXTRA = ("registry", "pmid", "doi", "rec_ids", "forras_attekintesek", "adat_forras", "ellenorizve")
#: a másodlagos-adat munkalista további oszlopai (TERV 11.)
SECONDARY_EXTRA = ("adat_forras", "forras_attekintes", "forras_lokator", "ellenorizve", "kimenet_az_attekintesben",
                   "bizonyitek")


class MergeError(RuntimeError):
    def __init__(self, hu, en=None, code="HH_MERGE"):
        RuntimeError.__init__(self, hu)
        self.hu = hu
        self.en = en or hu
        self.code = code


def _expl(hu, en):
    return {"hu": hu, "en": en}


def _warn(code, hu, en, **extra):
    w = {"code": code, "hu": hu, "en": en}
    w.update(extra)
    return w


def _d():
    from . import dedup
    return dedup


# =============================================================================================
# döntések alkalmazása
# =============================================================================================

def effective_reviews(reviews, decisions):
    """Az áttekintések a hatályos emberi döntésekkel: ``review_select`` → ``status``, ``candidate_confirm`` /
    ``candidate_reject`` → a jelölt ``status``-a (a fájlban tárolt állapotot a döntésnapló felülírja)."""
    eff_r = S.effective_decisions(decisions, kinds=("review_select",))
    eff_c = S.effective_decisions(decisions, kinds=("candidate_confirm", "candidate_reject"))
    out = copy.deepcopy(list(reviews or []))
    for r in out:
        d = eff_r.get(("review", r.get("review_id")))
        if d and str(d.get("actor") or "").startswith("user:"):
            r["status"] = "selected" if d.get("value") == "include" else "excluded"
        for c in r.get("candidates") or []:
            dc = eff_c.get(("candidate", "%s#%s" % (r.get("review_id"), c.get("cand_id"))))
            if dc and str(dc.get("actor") or "").startswith("user:"):
                c["status"] = "confirmed" if dc.get("kind") == "candidate_confirm" else "rejected"
    return out


def retracted_retention(decisions):
    """Az emberi „visszavont közlemény tudatos megtartása" döntések: ``{study_id: decision}`` (``final_inclusion``
    döntés ``keep_retracted`` értékkel, indoklással; H013)."""
    out = {}
    for (ttype, tid), d in S.effective_decisions(decisions, kinds=("final_inclusion",)).items():
        if ttype == "study" and d.get("value") == "keep_retracted" and \
                str(d.get("actor") or "").startswith("user:") and (d.get("reason") or "").strip():
            out[tid] = d
    return out


def screening_status(records, decisions, state=None):
    """Rekordonkénti hatályos szűrési döntés (``eligibility.screening_status``; ha az nem tölthető be, egyszerű
    tartalék: szintenként az utolsó emberi ``screen`` döntés)."""
    try:
        from .eligibility import screening_status as _ss
        return _ss(records, decisions, state)
    except ImportError:
        pass
    cmap = _d().canonical_map(records)
    per = {}
    sup = set(d.get("supersedes") for d in decisions if d.get("supersedes"))
    for d in decisions:
        if d.get("kind") != "screen" or (d.get("target") or {}).get("type") != "record":
            continue
        if d.get("decision_id") in sup or not str(d.get("actor") or "").startswith("user:"):
            continue
        rid = cmap.get(d["target"]["id"], d["target"]["id"])
        per[(rid, d.get("level"))] = d
    out = {}
    for r in records:
        if r.get("status") == "merged_into":
            continue
        out[r["rec_id"]] = {}
        for lvl in ("title_abstract", "full_text"):
            d = per.get((r["rec_id"], lvl))
            out[r["rec_id"]][lvl] = {"value": d["value"] if d else None, "decision_id": d["decision_id"] if d else None,
                                     "reason_code": d.get("reason_code") if d else None,
                                     "status": "decided" if d else "pending", "by": {}}
    return out


def selected_reviews(reviews):
    """A kiválasztott (EP1) áttekintések; ha még egy sincs kiválasztva, a nem kizárt/nem felváltott jelöltek (a
    ``dedup``/``eligibility`` modulokkal azonos szabály — így a PRISMA-számok és az egyesített lista egyeznek)."""
    sel = [r for r in reviews if r.get("status") == "selected"]
    if sel:
        return sel
    return [r for r in reviews if r.get("status") not in ("excluded", "superseded")]


def branch_membership(records, reviews):
    """Rekordonként (kanonikus, aktív) a PRISMA-ág: ``other`` (egyéb módszerek: kiválasztott áttekintés nem
    elutasított, bevonás-szerepű jelöltje, hivatkozáskövetés vagy kézi felvétel), ``database`` (csak a frissítő
    keresésből), vagy ``None`` (egyikbe sem számít — pl. ki nem választott áttekintésből). Az ``eligibility``
    modul ``branch_counts`` szabályával azonos."""
    d = _d()
    cmap = d.canonical_map(records)
    origins = d.effective_origins(records)
    counting = set()
    for r in selected_reviews(reviews):
        for c in r.get("candidates") or []:
            if c.get("status") == "rejected" or c.get("role_in_review") not in INCLUDED_ROLES or not c.get("rec_id"):
                continue
            counting.add(cmap.get(c["rec_id"], c["rec_id"]))
    out = {}
    for r in records:
        if r.get("status") == "merged_into":
            continue
        rid = r["rec_id"]
        routes = set(o.get("route") for o in origins.get(rid, []))
        if rid in counting or routes & set(["citation_search", "manual"]):
            out[rid] = "other"
        elif "update_search" in routes:
            out[rid] = "database"
        else:
            out[rid] = None
    return out


# =============================================================================================
# másodlagos adatok
# =============================================================================================

def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _same_value(a, b, tol):
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        if na == nb:
            return True
        return abs(na - nb) <= float(tol or 0) * max(abs(na), abs(nb))
    return re.sub(r"\s+", " ", str(a)).strip().lower() == re.sub(r"\s+", " ", str(b)).strip().lower()


def _sv_key(review_id, sv):
    return (review_id, sv.get("evidence_id"), sv.get("field"), sv.get("outcome") or None, sv.get("arm") or None)


def secondary_decisions(decisions):
    """A hatályos ``secondary_verify`` döntések ``(review_id, evidence_id, field, outcome, arm)`` kulcs szerint."""
    out = {}
    sup = set(d.get("supersedes") for d in decisions if d.get("supersedes"))
    for d in decisions:
        if d.get("kind") != "secondary_verify" or d.get("decision_id") in sup:
            continue
        if not str(d.get("actor") or "").startswith("user:"):
            continue
        ev = (d.get("evidence_ids") or [None])[0]
        out[(d.get("review_id"), ev, d.get("field"), d.get("outcome") or None, d.get("arm") or None)] = d
    return out


def _apply_secondary(review_id, values, sdec):
    out = []
    for sv in values or []:
        v = dict(sv)
        v.setdefault("status", "unverified")
        d = sdec.get(_sv_key(review_id, v))
        if d is None:
            # a mező + bizonyíték elég, ha a döntés nem adott kimenetet/kart
            d = next((x for k, x in sdec.items() if k[0] == review_id and k[1] == v.get("evidence_id")
                      and k[2] == v.get("field") and k[3] is None and k[4] is None), None)
        if d is not None and d.get("value") in ("verified", "discrepant", "not_applicable"):
            v["status"] = d["value"]
            v["verified_decision"] = d["decision_id"]
            if d.get("primary_locator"):
                v["primary_locator"] = d["primary_locator"]
            if d.get("primary_value") is not None:
                v["primary_value"] = d["primary_value"]
        elif v["status"] in ("verified", "discrepant") and not v.get("verified_decision"):
            v["status"] = "unverified"  # emberi döntés nélkül nem lehet ellenőrzött (N2)
        out.append(v)
    return out


def _conflicts(secondary, tol):
    groups = {}
    for blk in secondary:
        for v in blk["values"]:
            key = (v.get("field"), (v.get("outcome") or "").strip().lower() or None, v.get("arm") or None)
            groups.setdefault(key, []).append((blk["review_id"], v))
    out = []
    for (field, outcome, arm), items in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        if len(set(rv for rv, _v in items)) < 2:
            continue
        distinct = []
        for _rv, v in items:
            if not any(_same_value(v.get("value"), x, tol) for x in distinct):
                distinct.append(v.get("value"))
        if len(distinct) < 2:
            continue
        out.append({"field": field, "outcome": items[0][1].get("outcome"), "arm": arm,
                    "values": [{"review_id": rv, "value": v.get("value"), "evidence_id": v["evidence_id"]}
                               for rv, v in items],
                    "note": _expl("Az áttekintések eltérő értéket közölnek (%s): az elsődleges közlemény dönt (H018)."
                                  % field, "The reviews report different values (%s): the primary report decides "
                                           "(H018)." % field)})
    return out


# =============================================================================================
# egyesítés (L9)
# =============================================================================================

def _citation(rec):
    b = rec.get("bib") or {}
    parts = []
    if b.get("first_author"):
        parts.append(str(b["first_author"]) + (" et al." if len(b.get("authors") or []) > 1 else ""))
    if b.get("title"):
        parts.append(str(b["title"]).rstrip("."))
    src = []
    if b.get("journal"):
        src.append(str(b["journal"]))
    if b.get("year"):
        src.append(str(b["year"]))
    vol = ""
    if b.get("volume"):
        vol = str(b["volume"]) + ("(%s)" % b["issue"] if b.get("issue") else "")
    if b.get("pages"):
        vol += (":" if vol else "") + str(b["pages"])
    if vol:
        src.append(vol)
    if src:
        parts.append(" ".join(src[:2]) + (";" + src[2] if len(src) > 2 else ""))
    text = ". ".join(parts) or (rec.get("cited_as") or {}).get("text") or rec["rec_id"]
    return text[:300]


def _report_ids(rec):
    out = {}
    for k, v in (rec.get("ids") or {}).items():
        if k == "registry":
            vals = [x for x in v if isinstance(x, dict) and x.get("value") and x.get("source") and x.get("at")]
            if vals:
                out[k] = vals
        elif isinstance(v, dict) and v.get("value") and v.get("source") and v.get("at"):
            out[k] = dict((kk, vv) for kk, vv in v.items() if kk in ("value", "source", "via", "at", "confirmed_by",
                                                                      "retrieval"))
    return out


def _trusted_any(rec):
    d = _d()
    return any(d.trusted(v) for k, v in (rec.get("ids") or {}).items() if k != "registry" and isinstance(v, dict))


_FT_RANK = {"include": 3, "awaiting": 2, "not_retrieved": 1, "exclude": 0}


def _study_status(rep_status):
    """A vizsgálat állapota a jelentések szűrési állapotából: ``included`` (≥ 1 teljes szöveg szinten bevont
    jelentés), ``excluded`` (minden jelentés kizárva), ``awaiting`` (elbírálásra vár / nem elérhető), ``pending``."""
    fts = []
    any_pending = False
    for st in rep_status:
        ta, ft = st.get("title_abstract") or {}, st.get("full_text") or {}
        if ta.get("status") == "decided" and ta.get("value") == "exclude":
            fts.append("exclude")
            continue
        if ta.get("status") != "decided" or ft.get("status") != "decided":
            any_pending = True
            continue
        fts.append(ft.get("value"))
    if "include" in fts:
        return "included"
    if any_pending:
        return "pending"
    if fts and all(v == "exclude" for v in fts):
        return "excluded"
    return "awaiting"


#: az EP6 (másodlagos adat ellenőrzése) mezői — a lezárás (EP5) UTÁN jönnek, ezért nem részei az ujjlenyomatnak
_EP6_FIELDS = ("status", "verified_decision", "primary_locator", "primary_value")


def _content_hash(studies):
    """A vizsgálatlista tartalmi ujjlenyomata (a ``final``-mezők és az EP6-ellenőrzés mezői nélkül) — a lezárás
    érvényességéhez. Felülvizsgálat: korábban a másodlagos adat ``verified`` állapota is benne volt, így a TERV szerinti
    sorrend (EP5 lezárás → EP6 ellenőrzés → elemzési export) lehetetlen volt — minden EP6-döntés érvénytelenítette a
    lezárást (H017), és az ``export --for-analysis`` megtagadta a futást."""
    body = []
    for s in studies:
        x = dict((k, v) for k, v in s.items() if k not in ("final",))
        if x.get("secondary_data"):
            x["secondary_data"] = [{"review_id": blk.get("review_id"),
                                    "values": [dict((k, v) for k, v in val.items() if k not in _EP6_FIELDS)
                                               for val in blk.get("values") or []]}
                                   for blk in x["secondary_data"]]
        body.append(x)
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def build_merged(state, reviews, studies_doc, decisions, update_doc=None, now=None):
    """Az egyesített vizsgálatkészlet (``szk.ma.headhunter.merged/v1``). Tiszta függvény (nem ír fájlt).

    Visszaad: ``(merged_doc, warnings)``. A ``merged_doc`` additív mezői: ``content_sha256`` (lezárás-ellenőrzés),
    ``dropped`` (ki nem választott áttekintésből vagy elutasított jelöltből származó, ezért kimaradt
    vizsgálatok száma), ``summary`` (EP-k nyitott tételei)."""
    d = _d()
    state = state or {}
    reviews = effective_reviews(reviews, decisions)
    selected = selected_reviews(reviews)
    records = list((studies_doc or {}).get("records") or [])
    by_id = dict((r["rec_id"], r) for r in records)
    cmap = d.canonical_map(records)
    origins = d.effective_origins(records)
    status = screening_status(records, decisions, state)
    branch = branch_membership(records, reviews)
    tol = (state.get("settings") or {}).get("secondary_tolerance_rel", 0.0) or 0.0
    sdec = secondary_decisions(decisions)
    # emberi nyilatkozat: a közleménynek nincs API-ban elérhető azonosítója (pl. régi, nem indexelt folyóirat-
    # melléklet) — élő próbán (BCG: Madras 1980, Indian J Med Res Suppl.) e nélkül a lezárás után H003 hiba maradt
    no_id_ack = set(t[1] for t, dd in S.effective_decisions(decisions, kinds=("id_confirm",)).items()
                    if t[0] == "record" and str(dd.get("value")) == "no_identifier")
    keep_retracted = retracted_retention(decisions)
    warnings = []

    # jelöltek a kanonikus rekord szerint (kiválasztott áttekintések, nem elutasított, bevonás-szerepű)
    cand_by_rec = {}
    ev_by_review = {}
    citations_total = 0
    pending_candidates = 0
    for r in selected:
        evs = dict((e.get("evidence_id"), e) for e in r.get("evidence") or [])
        ev_by_review[r["review_id"]] = evs
        for c in r.get("candidates") or []:
            if c.get("status") == "rejected":
                continue
            if c.get("role_in_review") not in INCLUDED_ROLES:
                if c.get("role_in_review") == "unknown" and c.get("status") == "proposed":
                    pending_candidates += 1
                continue
            citations_total += 1
            if c.get("status") == "proposed":
                pending_candidates += 1
            good_ev = [e for e in c.get("evidence_ids") or [] if e in evs]
            if not good_ev:
                warnings.append(_warn("H002", "%s#%s: bevont-vizsgálat állítás bizonyíték nélkül — nem kerül a "
                                              "provenienciába." % (r["review_id"], c.get("cand_id")),
                                      "%s#%s: inclusion claim without evidence — left out of provenance."
                                      % (r["review_id"], c.get("cand_id")), review_id=r["review_id"]))
                continue
            if not c.get("rec_id"):
                continue
            cand_by_rec.setdefault(cmap.get(c["rec_id"], c["rec_id"]), []).append((r, c, good_ev))

    studies_out = []
    dropped = 0
    reports_included = set()
    pending_props = [p for p in (studies_doc or {}).get("proposals") or [] if p.get("status") == "pending"]
    for s in (studies_doc or {}).get("studies") or []:
        reps = []
        for rp in s.get("reports") or []:
            rid = cmap.get(rp.get("rec_id"), rp.get("rec_id"))
            if rid in by_id and by_id[rid].get("status") != "merged_into" and rid not in [x["rec_id"] for x in reps]:
                reps.append({"rec_id": rid, "role": rp.get("role") or "unknown",
                             "role_source": rp.get("role_source")})
        if not reps:
            continue
        in_branch = [x for x in reps if branch.get(x["rec_id"])]
        if not in_branch:
            dropped += 1
            continue
        routes = set()
        for x in reps:
            routes |= set(o.get("route") for o in origins.get(x["rec_id"], []))
        prov = {}
        secondary = {}
        for x in reps:
            for r, c, good_ev in cand_by_rec.get(x["rec_id"], []):
                p = prov.setdefault(r["review_id"], {"review_id": r["review_id"], "cand_ids": [],
                                                     "label_in_review": None, "evidence_ids": [],
                                                     "confidence": None, "candidate_status": []})
                if c["cand_id"] not in p["cand_ids"]:
                    p["cand_ids"].append(c["cand_id"])
                p["label_in_review"] = p["label_in_review"] or c.get("study_label_in_review") or \
                    (c.get("cited_as") or {}).get("text", "")[:120] or None
                for e in good_ev:
                    if e not in p["evidence_ids"]:
                        p["evidence_ids"].append(e)
                conf = c.get("confidence")
                if p["confidence"] is None or {"low": 0, "medium": 1, "high": 2}.get(conf, -1) > \
                        {"low": 0, "medium": 1, "high": 2}.get(p["confidence"], -1):
                    p["confidence"] = conf
                if c.get("status") not in p["candidate_status"]:
                    p["candidate_status"].append(c.get("status"))
                vals = _apply_secondary(r["review_id"], c.get("secondary_data"), sdec)
                vals = [v for v in vals if v.get("evidence_id") in ev_by_review.get(r["review_id"], {})]
                if vals:
                    secondary.setdefault(r["review_id"], []).extend(vals)
        found_by = [fb for route, fb in FOUND_BY if (route == "review_extraction" and prov) or
                    (route != "review_extraction" and route in routes)]
        if not found_by:
            found_by = ["manual"] if "manual" in routes else []
        if not found_by:
            dropped += 1
            continue
        rep_out, rep_status = [], []
        for x in reps:
            rec = by_id[x["rec_id"]]
            st = status.get(x["rec_id"]) or {}
            if branch.get(x["rec_id"]):
                rep_status.append(st)
            ta, ft = st.get("title_abstract") or {}, st.get("full_text") or {}
            elig = {"title_abstract": {"decision": ta.get("value"), "decision_id": ta.get("decision_id")}
                    if ta.get("status") == "decided" else None,
                    "full_text": {"decision": ft.get("value"), "reason_code": ft.get("reason_code"),
                                  "decision_id": ft.get("decision_id")} if ft.get("status") == "decided" else None}
            if ft.get("status") == "decided" and ft.get("value") == "include" and branch.get(x["rec_id"]):
                if not (ta.get("status") == "decided" and ta.get("value") == "exclude"):
                    reports_included.add(x["rec_id"])
            rep_out.append({"rec_id": x["rec_id"], "role": x["role"], "role_source": x["role_source"],
                            "ids": _report_ids(rec), "citation": _citation(rec),
                            "year": (rec.get("bib") or {}).get("year"), "branch": branch.get(x["rec_id"]),
                            "routes": sorted(set(o.get("route") for o in origins.get(x["rec_id"], []))),
                            "resolution": (rec.get("resolution") or {}).get("status"),
                            "screening_status": {"title_abstract": ta.get("status"),
                                                 "full_text": ft.get("status")},
                            "eligibility": elig})
        st_status = _study_status(rep_status)
        # a vizsgálat jogosultsága: a bevont (vagy a fő) jelentés döntése
        rep_main = next((r for r in rep_out if (r["eligibility"]["full_text"] or {}).get("decision") == "include"),
                        None) or next((r for r in rep_out if r["role"] == "primary"), rep_out[0])
        flags = set()
        for x in reps:
            rec = by_id[x["rec_id"]]
            fl = rec.get("flags") or {}
            if fl.get("retracted"):
                flags.add("retracted")
            if fl.get("conference_abstract"):
                flags.add("conference_abstract")
            if fl.get("preprint"):
                flags.add("preprint")
            if (rec.get("resolution") or {}).get("status") in ("unresolved", "ambiguous") or not _trusted_any(rec):
                flags.add("no_identifier_acknowledged" if x["rec_id"] in no_id_ack else "unresolved_ids")
            if (rec.get("resolution") or {}).get("unverified_live"):
                flags.add("unverified_live_source")
        if all((by_id[x["rec_id"]].get("flags") or {}).get("registry_record") for x in reps):
            flags.add("registry_only")
        if any("proposed" in p["candidate_status"] for p in prov.values()):
            flags.add("candidate_unconfirmed")
        sec_blocks = [{"review_id": rv, "values": vals} for rv, vals in sorted(secondary.items())]
        conflicts = _conflicts(sec_blocks, tol)
        if conflicts:
            flags.add("secondary_conflict")
            warnings.append(_warn("H018", "%s (%s): %d mezőben eltérő másodlagos érték az áttekintések között."
                                  % (s["study_id"], s.get("label"), len(conflicts)),
                                  "%s (%s): %d fields differ between reviews." % (s["study_id"], s.get("label"),
                                                                                    len(conflicts)),
                                  study_id=s["study_id"]))
        if "retracted" in flags and s["study_id"] in keep_retracted:
            flags.add("retracted_retention_documented")
        if st_status == "included" and "retracted" in flags and s["study_id"] not in keep_retracted:
            warnings.append(_warn("H013", "%s (%s): visszavont közlemény a bevont halmazban — zárd ki (exclude --target "
                                          "%s --reason-code …), vagy ha tudatosan megtartod, rögzítsd az indokot: decide "
                                          "--target %s --value keep_retracted --reason \"…\" --actor user:<név>. Amíg "
                                          "egyik sincs meg, a lezárás (EP5) nem lehetséges."
                                  % (s["study_id"], s.get("label"), s["study_id"], s["study_id"]),
                                  "%s (%s): retracted publication in the included set — exclude it or record why it is "
                                  "kept (decide --value keep_retracted); sign-off is blocked until then."
                                  % (s["study_id"], s.get("label")),
                                  study_id=s["study_id"]))
        if st_status == "included" and "unresolved_ids" in flags:
            warnings.append(_warn("H003", "%s (%s): van API-val meg nem erősített azonosítójú közlemény a bevont "
                                          "vizsgálatban." % (s["study_id"], s.get("label")),
                                  "%s (%s): a report without API-confirmed identifiers is in an included study."
                                  % (s["study_id"], s.get("label")), study_id=s["study_id"]))
        studies_out.append({
            "study_id": s["study_id"],
            "label": s.get("label") or s["study_id"],
            "status": st_status,
            "registry_ids": [dict((k, v) for k, v in iv.items() if k in ("value", "source", "via", "at",
                                                                          "confirmed_by", "retrieval"))
                             for iv in s.get("registry_ids") or [] if isinstance(iv, dict) and iv.get("value")],
            "reports": rep_out,
            "provenance": [dict((k, v) for k, v in p.items() if k != "candidate_status")
                           for _rv, p in sorted(prov.items())],
            "found_by": found_by,
            "eligibility": {"title_abstract": rep_main["eligibility"]["title_abstract"],
                            "full_text": rep_main["eligibility"]["full_text"]},
            "final": None,
            "secondary_data": sec_blocks,
            "conflicts": conflicts,
            "flags": sorted(flags),
            "n_reviews": len(prov),
        })
    studies_out.sort(key=lambda x: int(x["study_id"].split("-")[1]))
    content = _content_hash(studies_out)

    # lezárás (EP5): az utolsó hatályos signoff akkor érvényes, ha azóta nem változott a tartalom
    eff = S.effective_decisions(decisions, kinds=("checkpoint",))
    so = eff.get(("checkpoint", "EP5"))
    final = False
    if so and so.get("value") == "signoff" and str(so.get("actor") or "").startswith("user:"):
        if so.get("content_sha256") == content:
            final = True
            for st in studies_out:
                if st["status"] in ("included", "excluded"):
                    st["final"] = {"decision": "include" if st["status"] == "included" else "exclude",
                                   "decision_id": so["decision_id"]}
        else:
            warnings.append(_warn("H017", "A végső lezárás (EP5, %s) óta az egyesített halmaz megváltozott — a lezárás "
                                          "érvényét vesztette; ellenőrizd, és zárd le újra (signoff)."
                                  % so["decision_id"],
                                  "The merged set changed since the sign-off (%s); sign off again." % so["decision_id"]))
    sec_unverified = sum(1 for st in studies_out for blk in st["secondary_data"] for v in blk["values"]
                         if v.get("status") == "unverified")
    sec_unverified_incl = sum(1 for st in studies_out if st["status"] == "included" for blk in st["secondary_data"]
                              for v in blk["values"] if v.get("status") == "unverified")
    pending_studies = sum(1 for st in studies_out if st["status"] == "pending")
    counts = {
        "reviews_selected": sum(1 for r in reviews if r.get("status") == "selected"),
        "citations_total": citations_total,
        "studies_total": len(studies_out),
        "studies_included": sum(1 for st in studies_out if st["status"] == "included"),
        "reports_included": len(reports_included),
        "studies_from_update": sum(1 for st in studies_out if "update_search" in st["found_by"] and
                                   "previous_reviews" not in st["found_by"]),
        "pending_decisions": len(pending_props) + pending_studies + pending_candidates,
        "secondary_unverified": sec_unverified,
    }
    doc = {
        "schema": MERGED_SCHEMA,
        "model": S.MODEL,
        "generated": S.utc_now(now),
        "final": final,
        "counts": counts,
        "studies": studies_out,
        "prisma_flow": S.HH_REL + "/" + S.FILES["prisma"],
        "exports": {},
        "content_sha256": content,
        "dropped": dropped,
        "summary": {"pending_proposals": len(pending_props), "pending_studies": pending_studies,
                    "pending_candidates": pending_candidates, "secondary_unverified_included": sec_unverified_incl,
                    "conflicts": sum(len(st["conflicts"]) for st in studies_out),
                    "signoff_decision": so["decision_id"] if final else None},
    }
    return doc, warnings


def _load_all(project_dir):
    state = S.load_state(project_dir)
    reviews = S.load_reviews(project_dir)
    studies = S.read_json(S.path(project_dir, S.FILES["studies"]))
    decisions = S.read_decisions(project_dir)
    update_doc = S.read_json(S.path(project_dir, S.FILES["update"]))
    return state, reviews, studies, decisions, update_doc


def checkpoint_items(merged, reviews, decisions):
    """Az ellenőrzőpontok nyitott tételei (EP1–EP6) a jelenlegi fájlokból."""
    reviews = effective_reviews(reviews, decisions)
    selected = [r for r in reviews if r.get("status") == "selected"]
    out = {}
    out["EP1"] = sum(1 for r in reviews if r.get("status") == "candidate") if not selected else 0
    s = (merged or {}).get("summary") or {}
    out["EP2"] = s.get("pending_candidates", sum(1 for r in selected for c in r.get("candidates") or []
                                                 if c.get("status") == "proposed"))
    out["EP3"] = s.get("pending_proposals", 0)
    out["EP4"] = s.get("pending_studies", 0)
    out["EP5"] = 0 if (merged or {}).get("final") else 1
    out["EP6"] = s.get("secondary_unverified_included", 0)
    return out


def run_merge(project_dir, now=None, with_prisma=True, env=None):
    """L9 a projekten: ``merged.json`` (+ ``prisma_flow.json`` és motor-ellenőrzés). Visszaad: CLI-boríték-szerű
    ``{ok, data, warnings, errors, pending, exit_code, next}``."""
    state, reviews, studies, decisions, update_doc = _load_all(project_dir)
    if not studies:
        return {"ok": False, "data": None, "warnings": [], "errors": [_expl(
            "Még nincs studies.json — előbb futtasd a feloldást és a duplumszűrést (resolve, dedup).",
            "No studies.json yet — run resolve and dedup first.")], "pending": [], "exit_code": 2, "next": "resolve"}
    chain = S.verify_chain(decisions)
    warnings = []
    if chain:
        warnings.append(_warn("H015", "A döntésnapló hash-lánca sérült (%d hiba) — a döntések hitelessége nem igazolt."
                              % len(chain), "The decision log hash chain is broken (%d problems)." % len(chain)))
    merged, w = build_merged(state, reviews, studies, decisions, update_doc, now=now)
    warnings.extend(w)
    errs = S.schema_errors(merged, "merged")
    if errs:
        return {"ok": False, "data": None, "warnings": warnings, "errors": [_expl(
            "A merged.json nem felel meg a sémának (H001): %s" % "; ".join(errs[:5]),
            "merged.json fails its schema (H001).")], "pending": [], "exit_code": 1, "next": None}
    flow_res = None
    with S.lock(project_dir):
        S.write_json_atomic(S.path(project_dir, S.FILES["merged"]), merged)
    if with_prisma:
        from . import prisma_map
        flow_res = prisma_map.run_prisma(project_dir, now=now, merged=merged, state=state, reviews=reviews,
                                         studies_doc=studies, decisions=decisions, update_doc=update_doc)
        warnings.extend(flow_res.get("warnings") or [])
    items = checkpoint_items(merged, reviews, decisions)
    pending = [{"checkpoint": ep, "n": n} for ep, n in sorted(items.items()) if n and ep != "EP5"]

    def upd(st):
        S.set_step(st, "merge", "needs_human" if any(p["checkpoint"] in ("EP2", "EP3", "EP4") for p in pending)
                   else "done", now=now, stale_downstream=True)
        if flow_res is not None:
            S.set_step(st, "prisma", "done" if flow_res.get("ok") else "failed", now=now)
        for ep, n in items.items():
            if ep == "EP1" and not n and any(r.get("status") == "selected"
                                              for r in effective_reviews(reviews, decisions)):
                S.set_checkpoint(st, "EP1", "done", open_items=0)
            elif ep in ("EP2", "EP3", "EP4", "EP6"):
                S.set_checkpoint(st, ep, "pending" if n else "done", open_items=n)
            elif ep == "EP5":
                S.set_checkpoint(st, "EP5", "done" if merged["final"] else "pending", open_items=n,
                                 decision_id=merged["summary"].get("signoff_decision"))
        st.setdefault("counters", {})["study_seq"] = max(int((st.get("counters") or {}).get("study_seq") or 0),
                                                         int(((studies or {}).get("counters") or {}).get("study_seq")
                                                             or 0))
    S.mutate_state(project_dir, upd, env=env, now=now)
    exit_code = 0
    if flow_res is not None and not flow_res.get("ok"):
        exit_code = 1
    elif any(p["checkpoint"] in ("EP2", "EP3", "EP4") for p in pending):
        exit_code = 4
    data = {"counts": merged["counts"], "final": merged["final"], "summary": merged["summary"],
            "dropped": merged["dropped"], "files": [S.HH_REL + "/" + S.FILES["merged"]]}
    if flow_res is not None:
        data["prisma"] = {"ok": flow_res.get("ok"), "summary": flow_res.get("summary"),
                          "file": flow_res.get("file"), "check_command": flow_res.get("check_command")}
        data["files"].append(flow_res.get("file"))
    first = next((p["checkpoint"] for p in pending if p["checkpoint"] in ("EP2", "EP3", "EP4")), None)
    hint = {"EP2": "list %s candidates --status proposed   →   confirm/exclude --target rv-…#c…",
            "EP3": "list %s proposals   →   confirm/exclude --target p-…",
            "EP4": "list %s studies --status pending   →   confirm/exclude --target st-… (kizárásnál --reason-code X…)"}
    if exit_code == 4 and first:
        nxt = hint[first] % project_dir
    elif exit_code == 0 and not merged["final"]:
        nxt = "signoff %s --actor user:<név>   (EP5), majd export" % project_dir
    elif exit_code == 0:
        nxt = "export %s --outcome <kimenet> --to-project" % project_dir
    else:
        nxt = "prisma %s" % project_dir
    return {"ok": exit_code in (0, 4), "data": data, "warnings": warnings, "errors": [], "pending": pending,
            "exit_code": exit_code, "next": nxt}


# =============================================================================================
# EP5 — végső lezárás; EP6 — másodlagos adatok ellenőrzése
# =============================================================================================

def signoff(project_dir, actor, reason=None, now=None, env=None):
    """EP5: a végső bevonás emberi lezárása. Feltétel (H009, H011): nincs nyitott EP1–EP4 tétel, nincs függő
    javaslat, és a PRISMA-számok átmennek a motor ellenőrzésén. Ezután ``merged.final = true``; a döntés tükre a
    projektnaplóba is bekerül (``projekt.log_decision``, ``agent="planner"``), ha van projektnapló."""
    S.check_actor(actor, "checkpoint")
    res = run_merge(project_dir, now=now, env=env)
    if res["exit_code"] == 2:
        return res
    blockers = [p for p in res["pending"] if p["checkpoint"] in ("EP1", "EP2", "EP3", "EP4")]
    prisma_ok = ((res.get("data") or {}).get("prisma") or {}).get("ok")
    # H003: a végső halmazban csak API-val megerősített azonosítójú (vagy emberi nyilatkozattal „nincs azonosító")
    # közlemény lehet — élő próbán (szója: 6, BCG: 2 vizsgálat) a lezárás e nélkül átment, utána H003-hiba maradt
    cur = S.read_json(S.path(project_dir, S.FILES["merged"])) or {}
    unresolved = [st for st in cur.get("studies") or [] if st.get("status") == "included"
                  and "unresolved_ids" in (st.get("flags") or [])]
    # H013 (error): visszavont közlemény a bevont halmazban csak dokumentált emberi megtartással zárható le —
    # felülvizsgálat: korábban a lezárás figyelmeztetéssel átment
    retracted = [st for st in cur.get("studies") or [] if st.get("status") == "included"
                 and "retracted" in (st.get("flags") or [])
                 and "retracted_retention_documented" not in (st.get("flags") or [])]
    if blockers or not prisma_ok or unresolved or retracted:
        errs = []
        if retracted:
            errs.append(_expl(
                "A lezárás nem lehetséges: %d bevont vizsgálatnak visszavont (retracted) közleménye van (H013): %s. "
                "Teendő: zárd ki (exclude <projekt> --target st-… --reason-code X…), vagy ha tudatosan megtartod "
                "(pl. érzékenységi elemzéshez), rögzítsd az indokot: decide <projekt> --target st-… --value "
                "keep_retracted --reason \"…\" --actor user:<név>."
                % (len(retracted), ", ".join("%s (%s)" % (x["study_id"], x.get("label")) for x in retracted[:8])),
                "Sign-off blocked: %d included studies have a retracted report (H013) — exclude them or record why "
                "they are kept (decide --value keep_retracted)." % len(retracted)))
        if unresolved:
            errs.append(_expl(
                "A lezárás nem lehetséges: %d bevont vizsgálatnak van API-val meg nem erősített azonosítójú közleménye "
                "(H003): %s. Teendő: resolve (vagy a feloldási javaslat eldöntése), illetve ha a közleménynek valóban "
                "nincs azonosítója: decide <projekt> --target rec-… --value no_identifier --reason \"hol "
                "ellenőrizted\" --actor user:<név>."
                % (len(unresolved), ", ".join("%s (%s)" % (x["study_id"], x.get("label")) for x in unresolved[:8])),
                "Sign-off blocked: %d included studies have reports without API-confirmed identifiers (H003)."
                % len(unresolved)))
        if blockers:
            errs.append(_expl("A lezárás nem lehetséges, amíg nyitott emberi döntés van (H009): %s."
                              % ", ".join("%s: %d" % (p["checkpoint"], p["n"]) for p in blockers),
                              "Sign-off blocked by open checkpoints (H009): %s." %
                              ", ".join("%s: %d" % (p["checkpoint"], p["n"]) for p in blockers)))
        if not prisma_ok:
            errs.append(_expl("A PRISMA-számok nem mennek át a motor ellenőrzésén (H011) — javítsd a döntéseket.",
                              "The PRISMA numbers fail the engine check (H011)."))
        return {"ok": False, "data": res.get("data"), "warnings": res["warnings"], "errors": errs,
                "pending": res["pending"], "exit_code": 4 if (blockers or unresolved or retracted) else 1,
                "next": "status"}
    merged = S.read_json(S.path(project_dir, S.FILES["merged"]))
    counts = merged["counts"]
    txt = reason or ("Végső bevonás lezárva: %d vizsgálat (%d jelentés) a bevont halmazban, %d forrás-áttekintésből "
                     "és a frissítő keresésből." % (counts["studies_included"], counts["reports_included"],
                                                    counts["reviews_selected"]))
    d = S.append_decision(project_dir, "checkpoint", ("checkpoint", "EP5"), "signoff", actor, now=now, reason=txt,
                          level="final", kb_refs=KB_SIGNOFF, content_sha256=merged["content_sha256"],
                          studies_included=counts["studies_included"], reports_included=counts["reports_included"])
    log_id = None
    log_warn = []
    try:
        from .. import projekt
        log_id = projekt.log_decision(project_dir, "planner", "Metaheadhunter: végső bevonás lezárva (EP5) — %d "
                                      "vizsgálat, %d jelentés." % (counts["studies_included"],
                                                                   counts["reports_included"]),
                                      rationale=txt, stage="S04", kb_refs=",".join(KB_SIGNOFF), actor=actor,
                                      strict=False, warnings=log_warn,
                                      context=None)
    except Exception:
        log_id = None
    res2 = run_merge(project_dir, now=now, env=env)
    res2.setdefault("data", {})["signoff_decision"] = d["decision_id"]
    res2["data"]["project_log_id"] = log_id
    for w in log_warn:
        res2.setdefault("warnings", []).append(_warn("W-LOG", "Projektnapló: %s" % w, "Project log: %s" % w))
    if log_id is None:
        res2.setdefault("warnings", []).append(_warn(
            "W-LOG", "A projektnapló (projekt.sqlite) nem érhető el — a lezárás csak a decisions.jsonl-ban szerepel. "
                     "Ha van projektnapló, rögzítsd: ma.py project log … --agent planner --stage S04.",
            "Project log not available — the sign-off is recorded only in decisions.jsonl."))
    return res2


SECONDARY_STATUSES = ("verified", "discrepant", "not_applicable")


def verify_secondary(project_dir, study_id, field, status, actor, review_id=None, evidence_id=None, outcome=None,
                     arm=None, primary_locator=None, primary_value=None, reason=None, now=None):
    """EP6: egy másodlagos érték ellenőrzése az elsődleges közleménnyel (``verified`` / ``discrepant`` /
    ``not_applicable``). A ``primary_locator`` (pl. ``rec-pmid-23391465 p. 5, Table 2``) kötelező ``verified`` és
    ``discrepant`` esetén. Ha több áttekintés közölt értéket ugyanarra a mezőre, a ``review_id`` (vagy
    ``evidence_id``) kötelező. Visszaad: a döntés."""
    if status not in SECONDARY_STATUSES:
        raise S.DecisionError("Az állapot: %s." % ", ".join(SECONDARY_STATUSES))
    if status in ("verified", "discrepant") and not (primary_locator and str(primary_locator).strip()):
        raise S.DecisionError("Add meg, hol ellenőrizted az elsődleges közleményben (--primary-locator, pl. "
                              "'rec-pmid-23391465 p. 5, Table 2').")
    merged = S.read_json(S.path(project_dir, S.FILES["merged"]))
    if not merged:
        raise S.DecisionError("Még nincs merged.json — futtasd: merge.")
    st = next((x for x in merged.get("studies") or [] if x.get("study_id") == study_id), None)
    if st is None:
        raise S.DecisionError("Nincs ilyen vizsgálat az egyesített halmazban: %s" % study_id)
    hits = []
    for blk in st.get("secondary_data") or []:
        if review_id and blk["review_id"] != review_id:
            continue
        for v in blk["values"]:
            if v.get("field") != field:
                continue
            if evidence_id and v.get("evidence_id") != evidence_id:
                continue
            if outcome and (v.get("outcome") or "").strip().lower() != str(outcome).strip().lower():
                continue
            if arm and (v.get("arm") or None) != arm:
                continue
            hits.append((blk["review_id"], v))
    if not hits:
        raise S.DecisionError("Nincs ilyen másodlagos érték: %s / %s%s." % (study_id, field,
                                                                              (" / " + review_id) if review_id else ""))
    if len(hits) > 1:
        raise S.DecisionError("Több másodlagos érték illeszkedik (%d) — szűkíts --review, --evidence, --outcome vagy "
                              "--arm kapcsolóval: %s" % (len(hits), ", ".join("%s %s" % (rv, v.get("evidence_id"))
                                                                              for rv, v in hits[:6])))
    rv, v = hits[0]
    if status == "verified" and primary_value is not None and str(primary_value).strip() != "" and \
            not _same_value(primary_value, v.get("value"), 0):
        # felülvizsgálat: korábban az eltérő elsődleges értékkel „ellenőrzött" jelölés után az elemzési export az
        # ÁTTEKINTÉS (eltérő) számát írta be „ellenőrzött" címkével
        raise S.DecisionError("Az elsődleges közleményben talált érték (%s) eltér az áttekintésétől (%s): ez nem "
                              "„verified\", hanem „discrepant\" (--status discrepant). Az elemzésbe az elsődleges "
                              "közlemény értékét te írod be (03_adatok/<kimenet>.csv)." % (primary_value, v.get("value")))
    return S.append_decision(project_dir, "secondary_verify", ("study", study_id), status, actor, now=now,
                             reason=reason, evidence_ids=[v["evidence_id"]], kb_refs=KB_SECONDARY,
                             field=field, review_id=rv, outcome=v.get("outcome") or None, arm=v.get("arm") or None,
                             primary_locator=primary_locator, primary_value=primary_value,
                             secondary_value=v.get("value"))


# =============================================================================================
# exportok
# =============================================================================================

def _slug(text):
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t[:60] or "kimenet"


def _first_id(rep, kind):
    """Egy jelentés azonosítója exportokhoz — CSAK API-ból jött vagy API-val megerősített érték, és csak feloldott
    rekordnál (N1; felülvizsgálat: korábban az áttekintés meg nem erősített PMID-je/DOI-ja is a RIS-be és a
    kimenet-sablonba került, mintha ellenőrzött azonosító volna)."""
    if rep.get("resolution") in _d().UNVERIFIED_RESOLUTION:
        return None
    v = (rep.get("ids") or {}).get(kind)
    return v.get("value") if isinstance(v, dict) and _d().trusted(v) else None


def _primary(study):
    return next((r for r in study["reports"] if r["role"] == "primary"), study["reports"][0])


def _registry(study):
    vals = [iv.get("value") for iv in study.get("registry_ids") or [] if iv.get("value")]
    return ",".join(sorted(set(vals)))


def _norm_outcome(s):
    t = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _locator_text(ev):
    loc = (ev or {}).get("locator") or {}
    parts = [str(loc[k]) for k in ("container", "label", "section", "element_id") if loc.get(k)]
    if loc.get("row") is not None:
        parts.append("sor %s" % loc["row"])
    if loc.get("column"):
        parts.append("oszlop: %s" % loc["column"])
    if loc.get("page"):
        parts.append("p. %s" % loc["page"])
    return ", ".join(parts)


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def outcome_rows(merged, outcome=None, for_analysis=False):
    """A kimenet-sablon (``03_adatok/<kimenet>.csv``) sorai: bevont vizsgálatonként egy sor az adatkinyerő sablon
    oszlopaival + azonosítók, proveniencia és ellenőrzés. A számcellák ÜRESEK (az elsődleges közleményből töltendők);
    ``for_analysis`` mellett csak az EP6-ban ``verified`` másodlagos értékek kerülnek be (``adat_forras =
    masodlagos_ellenorzott``) — ellenőrizetlen másodlagos szám SOHA (N2, H010). Visszaad: ``(fejléc, sorok,
    kimaradt_ellenőrizetlen)``."""
    header = list(TEMPLATE_COLUMNS) + list(OUTCOME_EXTRA)
    rows = []
    skipped_unverified = 0
    want = _norm_outcome(outcome) if outcome else None
    for st in merged.get("studies") or []:
        if st.get("status") != "included":
            continue
        prim = _primary(st)
        inc_reports = [r["rec_id"] for r in st["reports"]
                       if ((r.get("eligibility") or {}).get("full_text") or {}).get("decision") == "include"]
        row = dict((c, "") for c in header)
        row.update({"study": st["label"], "study_id": st["study_id"], "year": _fmt(prim.get("year")),
                    "registry": _registry(st), "pmid": _first_id(prim, "pmid") or "", "doi": _first_id(prim, "doi") or "",
                    "rec_ids": ",".join(inc_reports or [prim["rec_id"]]),
                    "forras_attekintesek": ",".join(p["review_id"] for p in st.get("provenance") or []),
                    "adat_forras": "", "ellenorizve": "nem",
                    "megjegyzes": "Metaheadhunter: töltsd ki az elsődleges közleményből (%s); a másodlagos adatok "
                                  "munkalistája: 01_kereses/headhunter/exports/masodlagos_adatok.csv"
                                  % ",".join(inc_reports or [prim["rec_id"]])})
        if for_analysis:
            used, locs = {}, []
            for blk in st.get("secondary_data") or []:
                for v in blk["values"]:
                    if v.get("field") not in NUMERIC_TEMPLATE:
                        continue
                    if want and want not in _norm_outcome(v.get("outcome")):
                        continue
                    if v.get("status") != "verified":
                        skipped_unverified += 1 if v.get("status") == "unverified" else 0
                        continue
                    if v["field"] in used and not _same_value(used[v["field"]], v.get("value"), 0):
                        used[v["field"]] = None  # ellentmondó ellenőrzött értékek: nem írunk számot
                        continue
                    used.setdefault(v["field"], v.get("value"))
                    if v.get("primary_locator"):
                        locs.append(v["primary_locator"])
            filled = dict((k, v) for k, v in used.items() if v is not None)
            if filled:
                for k, v in filled.items():
                    row[k] = _fmt(v)
                row["adat_forras"] = "masodlagos_ellenorzott"
                row["ellenorizve"] = "igen"
                row["forras_oldal"] = "; ".join(sorted(set(locs)))[:300]
                row["megjegyzes"] = ("Metaheadhunter: áttekintésből átvett, az elsődleges közleménnyel ELLENŐRZÖTT "
                                     "érték(ek) (EP6); a hiányzó cellákat az elsődleges közleményből töltsd ki.")
        rows.append(row)
    return header, rows, skipped_unverified


def secondary_rows(merged, reviews_by_id=None):
    """A másodlagos-adat munkalista (``masodlagos_adatok.csv``) sorai: vizsgálatonként, áttekintésenként és
    kimenetenként egy sor az adatkinyerő sablon oszlopaival + ``adat_forras`` (mindig ``masodlagos``),
    ``forras_attekintes``, ``forras_lokator``, ``ellenorizve`` (``nem`` / ``igen`` / ``elteres``). Ez ELLENŐRZÉSI
    munkalista, nem elemzési tábla."""
    header = list(TEMPLATE_COLUMNS) + list(SECONDARY_EXTRA)
    rows = []
    reviews_by_id = reviews_by_id or {}
    for st in merged.get("studies") or []:
        prim = _primary(st)
        for blk in st.get("secondary_data") or []:
            evs = dict((e.get("evidence_id"), e) for e in (reviews_by_id.get(blk["review_id"]) or {}).get("evidence")
                       or [])
            by_outcome = {}
            for v in blk["values"]:
                by_outcome.setdefault(v.get("outcome") or "", []).append(v)
            groups = {}
            for outc, vals in by_outcome.items():
                fields = [v.get("field") for v in vals]
                if len(fields) == len(set(fields)):
                    groups[(outc, "")] = vals          # egy sor kimenetenként (n1/n2 … maguk jelölik a kart)
                else:
                    for v in vals:                     # ugyanaz a mező többször (több kar/időpont): karonként
                        groups.setdefault((outc, v.get("arm") or ""), []).append(v)
            for (outc, arm), vals in sorted(groups.items()):
                row = dict((c, "") for c in header)
                row.update({"study": st["label"], "study_id": st["study_id"], "year": _fmt(prim.get("year")),
                            "subgroup": arm, "adat_forras": "masodlagos", "forras_attekintes": blk["review_id"],
                            "kimenet_az_attekintesben": outc})
                arms = {}
                for v in vals:
                    f = str(v.get("field") or "")
                    if v.get("arm") and f[-1:] in ("1", "2"):
                        arms.setdefault(f[-1], v["arm"])
                extra, locs, quotes, states = [], [], [], set()
                if arms and not arm:
                    extra.append(", ".join("%s. kar: %s" % (k, arms[k]) for k in sorted(arms)))
                for v in vals:
                    states.add(v.get("status") or "unverified")
                    if v.get("field") in NUMERIC_TEMPLATE:
                        row[v["field"]] = _fmt(v.get("value"))
                    else:
                        extra.append("%s=%s" % (v.get("field"), _fmt(v.get("value"))))
                    if v.get("unit") and not row["egyseg"]:
                        row["egyseg"] = v["unit"]
                    ev = evs.get(v.get("evidence_id"))
                    lt = _locator_text(ev) or v.get("evidence_id")
                    if lt and lt not in locs:
                        locs.append(lt)
                    q = (ev or {}).get("quote")
                    if q and q not in quotes:
                        quotes.append(q)
                row["forras_lokator"] = " | ".join(locs)[:300]
                row["bizonyitek"] = " | ".join(quotes)[:300]
                row["ellenorizve"] = "elteres" if "discrepant" in states else (
                    "igen" if states <= set(["verified", "not_applicable"]) else "nem")
                row["megjegyzes"] = ("MÁSODLAGOS adat (áttekintésből) — elemzés előtt ellenőrizd az elsődleges "
                                     "közleménnyel (verify-secondary)." + (" Egyéb: " + "; ".join(extra) if extra
                                                                            else ""))[:300]
                rows.append(row)
    return header, rows


def _ris_escape(s):
    return re.sub(r"[\r\n]+", " ", str(s or "")).strip()


def ris_text(merged, studies_doc=None):
    """RIS-export (Rayyan, Covidence, EndNote, Zotero) az egyesített halmaz minden közleményéről. Csak
    bibliográfiai adat (absztrakt nincs)."""
    recs = dict((r["rec_id"], r) for r in (studies_doc or {}).get("records") or [])
    out = []
    for st in merged.get("studies") or []:
        for rp in st["reports"]:
            rec = recs.get(rp["rec_id"]) or {}
            b = rec.get("bib") or {}
            flags = rec.get("flags") or {}
            ty = "CTREG" if flags.get("registry_record") else ("CPAPER" if flags.get("conference_abstract") else "JOUR")
            lines = ["TY  - %s" % ty, "ID  - %s" % rp["rec_id"]]
            if b.get("title"):
                lines.append("TI  - %s" % _ris_escape(b["title"]))
            for a in (b.get("authors") or ([b["first_author"]] if b.get("first_author") else [])):
                lines.append("AU  - %s" % _ris_escape(a))
            if b.get("year"):
                lines.append("PY  - %s" % b["year"])
            if b.get("journal"):
                lines.append("JO  - %s" % _ris_escape(b["journal"]))
            if b.get("volume"):
                lines.append("VL  - %s" % _ris_escape(b["volume"]))
            if b.get("issue"):
                lines.append("IS  - %s" % _ris_escape(b["issue"]))
            if b.get("pages"):
                pg = str(b["pages"]).split("-", 1)
                lines.append("SP  - %s" % _ris_escape(pg[0]))
                if len(pg) > 1 and pg[1]:
                    lines.append("EP  - %s" % _ris_escape(pg[1]))
            doi = _first_id(rp, "doi")
            if doi:
                lines.append("DO  - %s" % doi)
            pmid = _first_id(rp, "pmid")
            if pmid:
                lines.append("AN  - PMID:%s" % pmid)
            nct = _first_id(rp, "nct")
            if nct:
                lines.append("AN  - %s" % nct)
            lines.append("N1  - Metaheadhunter %s (%s, szerep: %s); forrás-áttekintések: %s; eredet: %s"
                         % (st["study_id"], _ris_escape(st["label"]), rp["role"],
                            ",".join(p["review_id"] for p in st.get("provenance") or []) or "—",
                            ",".join(st.get("found_by") or [])))
            lines.append("ER  - ")
            out.append("\n".join(lines))
    return "\n\n".join(out) + ("\n" if out else "")


def screening_rows(merged):
    """Szűrési munkalista (``screening.csv``): az első öt oszlop az ``eligibility.import_screening_csv``
    visszatöltési formátuma (``rec_id;level;decision;reason_code;actor``), a többi kontextus (mely
    áttekintések vonták be — ez nem jogosultság)."""
    header = ["rec_id", "level", "decision", "reason_code", "actor", "study_id", "study", "role", "title_or_citation",
              "year", "pmid", "doi", "included_by_reviews", "found_by", "current_title_abstract", "current_full_text"]
    rows = []
    for st in merged.get("studies") or []:
        for rp in st["reports"]:
            if not rp.get("branch"):
                continue
            el = rp.get("eligibility") or {}
            ta, ft = el.get("title_abstract") or {}, el.get("full_text") or {}
            level = "title_abstract" if not ta else "full_text"
            rows.append({"rec_id": rp["rec_id"], "level": level, "decision": "", "reason_code": "", "actor": "",
                         "study_id": st["study_id"], "study": st["label"], "role": rp["role"],
                         "title_or_citation": rp.get("citation") or "", "year": _fmt(rp.get("year")),
                         "pmid": _first_id(rp, "pmid") or "", "doi": _first_id(rp, "doi") or "",
                         "included_by_reviews": ",".join(p["review_id"] for p in st.get("provenance") or []),
                         "found_by": ",".join(st.get("found_by") or []),
                         "current_title_abstract": ta.get("decision") or "",
                         "current_full_text": (ft.get("decision") or "") + (
                             " (%s)" % ft["reason_code"] if ft.get("reason_code") else "")})
    return header, rows


def studies_ma(merged, now=None):
    """``szk.ma.studies/v1`` (``03_adatok/studies.json``): a bevont vizsgálatok és a teljes szöveg szinten bevont
    jelentéseik (PRISMA I és J)."""
    out = []
    for st in merged.get("studies") or []:
        if st.get("status") != "included":
            continue
        reps = [{"rec_id": r["rec_id"], "role": r["role"]} for r in st["reports"]
                if ((r.get("eligibility") or {}).get("full_text") or {}).get("decision") == "include"]
        out.append({"study_id": st["study_id"], "label": st["label"],
                    "registration": (_registry(st).split(",")[0] or None) if _registry(st) else None,
                    "reports": reps, "found_by": st.get("found_by"),
                    "source_reviews": [p["review_id"] for p in st.get("provenance") or []]})
    return {"schema": STUDIES_MA_SCHEMA, "generated": S.utc_now(now),
            "source": {"kind": "headhunter", "file": S.HH_REL + "/" + S.FILES["merged"],
                       "final": bool(merged.get("final"))},
            "studies": out}


def _csv_text(header, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, delimiter=";", lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _write_new(project_dir, rel_target, text, force, actor, what, now=None):
    """Írás a projekt fő mappáiba (``03_adatok``, ``02_szures``) csak ha a célfájl nem létezik; különben
    eltérés-összefoglaló. Felülírás csak ``force`` + ``actor`` mellett, naplózott döntéssel."""
    target = os.path.join(project_dir, *rel_target.split("/"))
    if os.path.exists(target):
        try:
            with open(target, encoding="utf-8") as fh:
                old = fh.read()
        except (OSError, UnicodeDecodeError):
            old = None
        if old == text:
            return {"file": rel_target, "written": False, "status": "unchanged"}
        if not force:
            diff = {"old_lines": len((old or "").splitlines()), "new_lines": len(text.splitlines())}
            return {"file": rel_target, "written": False, "status": "exists", "diff": diff,
                    "message": _expl("A(z) %s már létezik — nem írom felül. Felülírás: --force --actor user:<név> "
                                     "(naplózott döntés)." % rel_target,
                                     "%s already exists — not overwritten (use --force --actor)." % rel_target)}
        if not actor:
            raise MergeError("Felülíráshoz add meg, ki dönt (--actor user:<név>).", code="BAD_REQUEST")
        S.append_decision(project_dir, "note", ("project", rel_target), "overwrite", actor, now=now,
                          reason="Metaheadhunter export: %s felülírva (%s)." % (rel_target, what))
    S.write_text_atomic(target, text)
    return {"file": rel_target, "written": True, "status": "written"}


def run_export(project_dir, outcome=None, to_project=False, prisma=False, for_analysis=False, force=False, actor=None,
               now=None, env=None):
    """Exportok (11. fejezet). Mindig: ``exports/studies.ma.json``, ``records.ris``, ``screening.csv``,
    ``masodlagos_adatok.csv``, ``<kimenet>_sablon.csv``. ``to_project``: ``03_adatok/studies.json`` és — ha
    ``outcome`` adott — ``03_adatok/<kimenet>.csv``; ``prisma``: ``02_szures/prisma_flow.json`` (csak ha nem
    létezik). ``for_analysis``: csak lezárt (EP5) halmazból, és csak ellenőrzött másodlagos értékkel (H009, H010)."""
    state, reviews, studies, decisions, update_doc = _load_all(project_dir)
    stored = S.read_json(S.path(project_dir, S.FILES["merged"]))
    if not stored or not studies:
        return {"ok": False, "data": None, "warnings": [], "errors": [_expl("Még nincs merged.json — futtasd: merge.",
                                                                           "No merged.json — run merge.")],
                "pending": [], "exit_code": 2, "next": "merge"}
    warnings, errors = [], []
    # az exportot mindig a FRISS döntésekből számoljuk (felülvizsgálat: a tárolt merged.json elavult lehetett — pl. a
    # lezárás utáni döntések vagy az EP6-ellenőrzések nem jelentek meg az elemzési exportban)
    merged, _mw = build_merged(state, reviews, studies, decisions, update_doc, now=now)
    if merged.get("content_sha256") != stored.get("content_sha256") or bool(merged.get("final")) != \
            bool(stored.get("final")):
        warnings.append(_warn("H017", "A merged.json elavult volt (azóta új döntés született) — az export a friss "
                                      "állapotból készült; futtasd a merge lépést is a PRISMA-számok frissítéséhez.",
                              "merged.json was stale — the export uses the current decisions; run merge too."))
    if for_analysis and not merged.get("final"):
        errors.append(_expl("Az elemzési export (--for-analysis) csak a végső lezárás (EP5, signoff) után futhat (H009).",
                            "--for-analysis requires the EP5 sign-off (H009)."))
        return {"ok": False, "data": None, "warnings": warnings, "errors": errors,
                "pending": [{"checkpoint": "EP5", "n": 1}], "exit_code": 4, "next": "signoff"}
    ex = S.path(project_dir, "exports")
    files = []
    rel_ex = S.HH_REL + "/exports/"

    def put(name, text):
        leaks = S.secret_leaks(text, env)
        if leaks:
            raise MergeError("Titok-szivárgás gyanúja (H016) az exportban (%s) — nem írom ki." % name, code="H016")
        S.write_text_atomic(os.path.join(ex, name), text)
        files.append(rel_ex + name)

    sm = studies_ma(merged, now=now)
    put("studies.ma.json", S.dump_json(sm))
    put("records.ris", ris_text(merged, studies))
    h, rows = screening_rows(merged)
    put("screening.csv", _csv_text(h, rows))
    rv_by_id = dict((r["review_id"], r) for r in reviews)
    h, rows = secondary_rows(merged, rv_by_id)
    put("masodlagos_adatok.csv", _csv_text(h, rows))
    oname = _slug(outcome) if outcome else "kimenet"
    h, rows, skipped = outcome_rows(merged, outcome=outcome, for_analysis=for_analysis)
    otext = _csv_text(h, rows)
    put("%s_sablon.csv" % oname, otext)
    unver = (merged.get("summary") or {}).get("secondary_unverified_included", 0)
    if unver:
        warnings.append(_warn("H010", "%d ellenőrizetlen másodlagos érték van a bevont vizsgálatoknál — ezek nem "
                                      "kerülnek elemzési táblába; ellenőrizd őket (verify-secondary, EP6)." % unver,
                              "%d unverified secondary values in included studies — never written to analysis tables "
                              "(EP6)." % unver))
    project_files = []
    if to_project:
        project_files.append(_write_new(project_dir, "03_adatok/studies.json", S.dump_json(sm), force, actor,
                                        "studies.json", now=now))
        if outcome:
            project_files.append(_write_new(project_dir, "03_adatok/%s.csv" % oname, otext, force, actor,
                                            "kimenet-sablon", now=now))
    if prisma:
        flow = S.read_json(S.path(project_dir, S.FILES["prisma"]))
        if not flow:
            errors.append(_expl("Nincs prisma_flow.json — futtasd: merge (vagy prisma).",
                                "No prisma_flow.json — run merge."))
        else:
            project_files.append(_write_new(project_dir, "02_szures/prisma_flow.json", S.dump_json(flow), force,
                                            actor, "PRISMA-folyamat", now=now))
    exports = dict((os.path.basename(f).replace(".", "_"), f) for f in files)
    merged["exports"] = exports
    with S.lock(project_dir):
        S.write_json_atomic(S.path(project_dir, S.FILES["merged"]), merged)

    def upd(st):
        S.set_step(st, "export", "done", now=now)
    S.mutate_state(project_dir, upd, env=env, now=now)
    for pf in project_files:
        if pf.get("status") == "exists":
            warnings.append(_warn("W-EXISTS", pf["message"]["hu"], pf["message"]["en"], file=pf["file"]))
    return {"ok": not errors, "data": {"files": files, "project_files": project_files,
                                       "studies_included": len(sm["studies"]),
                                       "outcome_rows": len(rows), "unverified_secondary_skipped": skipped},
            "warnings": warnings, "errors": errors, "pending": [], "exit_code": 1 if errors else 0, "next": None}
