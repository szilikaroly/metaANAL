# -*- coding: utf-8 -*-
"""Metaheadhunter — L6: hivatkozási mátrix és átfedés (Corrected Covered Area, CCA) a forrás-áttekintések között
(TERV_metaheadhunter.md 9. fejezet).

Kezdőknek: ha több áttekintés ugyanazokat a vizsgálatokat vonta be, az átfedésük nagy. Ezt a CCA méri
(Pieper 2014; a képletet Hennessy & Johnson 2020 és Ying 2025 is így közli)::

    CCA = (N − r) / (r·c − r)        (százalékban × 100)
    N = a bejelölt cellák száma (duplikációval), r = sorok (egyedi vizsgálatok/közlemények), c = oszlopok (áttekintések)

* ``c < 2`` vagy ``r = 0`` → ``cca_pct: null`` (nem értelmezhető);
* páronként (``c = 2``): ``CCA = (N − r) / r`` = közös / (A ∪ B);
* két szinten: ``report`` (Pieper eredeti, közlemény-szintű) és ``study`` (társközlemények összevonva);
* sávok (útmutató, nem merev szabály; Ying 2025 1. keretes tábla, Pieper 2014 alapján): 0–5% ``slight``
  (enyhe), 6–10% ``moderate`` (mérsékelt), 11–15% ``high`` (magas), > 15% ``very_high`` (nagyon magas). A forrás
  sávjai EGÉSZ százalékok, ezért egészre kerekítünk („fél felfelé"), és úgy sorolunk be. **Build-döntés, eltérés a
  TERV 9. fejezet „egy tizedesre kerekítve ≤ 5,0" szabályától:** a forrás saját kidolgozott példája (Ying 2025,
  1. példa: CCA = 5,3% → „slight overlap") a tizedes szabállyal „moderate" lenne; az egész-kerekítés a forrással
  egyezik, a terv határértékeinél (5,0 / 10,0 / 15,0) pedig ugyanazt adja. A tárolt ``cca_pct`` egy tizedesre
  kerekített;
* wCCA (Ying 2025): ugyanez a szerkezet, a darabszámok helyett a vizsgálatok mintaelemszámának négyzetgyökével
  súlyozva — ``wCCA = (wN − wr) / (wr·c − wr)``, ahol ``wN`` = Σ √n minden bejelölt cellára, ``wr`` = Σ √n az
  egyedi vizsgálatokra. Csak tájékoztató, és CSAK ellenőrzött (``verified``) mintaelemszámokkal; ha bármelyik
  sorhoz nincs ilyen, ``null``.

**Értelmezés** (Hennessy & Johnson 2020 lépései; a felületen és a riportban): bányászatnál az átfedés nem
torzítás (a duplumokat összevonjuk), hanem azt mutatja, mennyire ugyanazt az irodalmat találták a korábbi
áttekintések. A nagyon magas páronkénti átfedésnél (pl. ugyanazon szerzők frissítése) a régebbi áttekintés
helyettesíthető; alacsony átfedés azonos PICO mellett eltérő kritériumokra vagy keresési hiányra utal → a frissítő és a
saját keresés jelentősége nő.

Nyilvános API::

    cca(N, r, c) → százalék | None;  band(pct) → 'slight'|'moderate'|'high'|'very_high'|None
    pair_cca(shared, n_a, n_b) → százalék | None;  wcca(rows_in, weights, c) → százalék | None
    build_matrix(reviews, studies_doc, level="study", scope=None) → (oszlopok, sorok, megjegyzések)
    compute_overlap(reviews, studies_doc, level="study", scope=None, sample_sizes=None, now=None) → overlap.json
    matrix_csv(doc, reviews=None) → CSV-szöveg (';' elválasztó);  interpret(doc) → {hu, en}
    run_overlap(project_dir, level="study", scope=None, csv=False, now=None)
"""
from __future__ import absolute_import

import csv
import io
import math
import os
from decimal import Decimal, ROUND_HALF_UP

from . import dedup as _d

OVERLAP_SCHEMA = "szk.ma.headhunter.overlap/v1"
BANDS = (("slight", 5.0), ("moderate", 10.0), ("high", 15.0))
BAND_LABELS = {
    "slight": {"hu": "enyhe", "en": "slight"},
    "moderate": {"hu": "mérsékelt", "en": "moderate"},
    "high": {"hu": "magas", "en": "high"},
    "very_high": {"hu": "nagyon magas", "en": "very high"},
}
KB_OVERLAP = ["D-S13-101"]

__all__ = ["cca", "band", "pair_cca", "wcca", "round1", "build_matrix", "compute_overlap", "matrix_csv", "interpret",
           "run_overlap", "BAND_LABELS"]


def round1(x):
    """Egy tizedesre kerekítés „fél felfelé" szabállyal (a bináris lebegőpontos ábrázolástól függetlenül)."""
    if x is None:
        return None
    return float(Decimal(repr(float(x))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def band(pct):
    """CCA-sáv a forrás egész-százalékos sávjai szerint (Pieper 2014; Ying 2025 1. keretes tábla): egészre kerekítve
    („fél felfelé") 0–5 enyhe, 6–10 mérsékelt, 11–15 magas, > 15 nagyon magas. Így 5,3% → enyhe (Ying 2025 1. példa),
    5,5% → mérsékelt; a 5,0 / 10,0 / 15,0 határ a terv szerinti sávba esik."""
    if pct is None:
        return None
    v = float(Decimal(repr(float(pct))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    for name, limit in BANDS:
        if v <= limit:
            return name
    return "very_high"


def cca(N, r, c):
    """Corrected Covered Area százalékban (nem kerekítve); ``c < 2`` vagy ``r = 0`` → ``None``."""
    if c is None or r is None or N is None or c < 2 or r <= 0:
        return None
    return 100.0 * (N - r) / float(r * c - r)


def pair_cca(shared, n_a, n_b):
    """Páronkénti CCA (``c = 2``): ``N = n_a + n_b``, ``r = n_a + n_b − shared`` → ``(N − r) / r``."""
    r = n_a + n_b - shared
    return cca(n_a + n_b, r, 2)


def wcca(rows_in, weights, c):
    """Súlyozott CCA (Ying 2025): ``(wN − wr) / (wr·c − wr)``; ``rows_in``: soronként a bejelölések száma,
    ``weights``: soronként √n (``None`` bármelyik sorban → ``None``)."""
    if c is None or c < 2 or not rows_in or any(w is None for w in weights):
        return None
    wr = sum(weights)
    if wr <= 0:
        return None
    wN = sum(k * w for k, w in zip(rows_in, weights))
    return 100.0 * (wN - wr) / (wr * c - wr)


# =============================================================================================
# mátrix
# =============================================================================================

def _columns(reviews):
    """Oszlopok: a kiválasztott áttekintések megjelenési év, majd azonosító szerint (az „index" az első —
    időrendben legkorábbi — áttekintés, amely a vizsgálatot bevonta; Pieper 2014)."""
    sel, fallback = _d._selected_reviews(reviews)

    def key(r):
        y = (r.get("bib") or {}).get("year")
        return (y if isinstance(y, int) else 9999, r["review_id"])
    return [r["review_id"] for r in sorted(sel, key=key)], fallback


def _scope_ok(cand, scope):
    if not scope:
        return True
    s = str(scope).strip().lower()
    for sd in cand.get("secondary_data") or []:
        if str(sd.get("outcome") or "").strip().lower() == s:
            return True
    return False


def build_matrix(reviews, studies_doc, level="study", scope=None):
    """A hivatkozási mátrix. Visszaad: ``(oszlopok, sorok, megjegyzések)``; egy sor:
    ``{key, label, in: [bool…], index_review, rec_ids, cands}``.

    ``level="report"``: sor = egyedi (kanonikus) közlemény; ``"study"``: sor = vizsgálat (társközlemények
    összevonva). Egy cella akkor igaz, ha az áttekintésnek van nem elutasított, bevonás-szerepű jelöltje, amely a
    sor közleményére (vagy a vizsgálat valamelyik közleményére) oldódott fel. ``scope``: csak az adott kimenetet
    közlő jelöltek (a jelölt ``secondary_data[].outcome`` mezője alapján)."""
    if level not in ("study", "report"):
        raise ValueError("level: 'study' vagy 'report'")
    cols, fallback = _columns(reviews)
    notes = []
    if fallback:
        notes.append({"code": "no_selected_reviews",
                      "hu": "Nincs kiválasztott (EP1) áttekintés — az összes nem kizárt áttekintéssel számoltunk.",
                      "en": "No selected (EP1) review — all non-excluded reviews were used."})
    col_index = dict((c, i) for i, c in enumerate(cols))
    records = (studies_doc or {}).get("records") or []
    cmap = _d.canonical_map(records)
    rec_by = dict((r["rec_id"], r) for r in records)
    study_of = {}
    labels = {}
    for s in (studies_doc or {}).get("studies") or []:
        labels[s["study_id"]] = s.get("label") or s["study_id"]
        for rp in s.get("reports") or []:
            study_of[rp["rec_id"]] = s["study_id"]
    rows = {}
    unlinked = 0
    revs = dict((r["review_id"], r) for r in reviews)
    for rv in cols:
        for c in revs[rv].get("candidates") or []:
            if c.get("status") == "rejected" or c.get("role_in_review") not in _d.INCLUDED_ROLES:
                continue
            if not _scope_ok(c, scope):
                continue
            rid = c.get("rec_id")
            if not rid:
                unlinked += 1
                continue
            canon = cmap.get(rid, rid)
            if level == "study":
                key = study_of.get(canon)
                if key is None:
                    unlinked += 1
                    continue
                label = labels.get(key, key)
            else:
                key = canon
                rec = rec_by.get(canon) or {}
                label = _d._label_base(rec, [(rv, c)]) if rec else canon
            row = rows.setdefault(key, {"key": key, "label": label, "in": [False] * len(cols), "index_review": None,
                                        "rec_ids": set(), "cands": []})
            row["in"][col_index[rv]] = True
            row["rec_ids"].add(canon)
            row["cands"].append("%s#%s" % (rv, c.get("cand_id")))
    if unlinked:
        notes.append({"code": "unlinked_candidates",
                      "hu": "%d bevont jelölt még nincs feloldva/vizsgálathoz rendelve — futtasd a resolve és a dedupe "
                            "lépést; addig az átfedés alulbecsült lehet." % unlinked,
                      "en": "%d included candidates are not resolved/linked to a study yet — run resolve and dedupe; "
                            "overlap may be underestimated until then." % unlinked})
    out = []
    for key in sorted(rows, key=lambda k: (_sort_num(k), k)):
        row = rows[key]
        for i, flag in enumerate(row["in"]):
            if flag:
                row["index_review"] = cols[i]
                break
        row["rec_ids"] = sorted(row["rec_ids"])
        row["cands"] = sorted(set(row["cands"]))
        out.append(row)
    return cols, out, notes


def _sort_num(k):
    try:
        return int(str(k).split("-")[-1]) if str(k).startswith("st-") else 0
    except ValueError:
        return 0


# =============================================================================================
# mintaelemszám a wCCA-hoz (csak ellenőrzött másodlagos adatból, vagy a hívó adja)
# =============================================================================================

def _verified_n(reviews, row):
    """Egy sor ellenőrzött mintaelemszáma: a sor jelöltjeinek ``secondary_data``-jából, CSAK ``verified``
    státuszú ``n_total`` (vagy azonos bizonyítékú ``n1`` + ``n2``) értékből. Eltérő értékek → ``None``."""
    by_ref = {}
    for r in reviews:
        for c in r.get("candidates") or []:
            by_ref["%s#%s" % (r["review_id"], c.get("cand_id"))] = c
    values = set()
    for ref in row["cands"]:
        c = by_ref.get(ref) or {}
        sds = [sd for sd in c.get("secondary_data") or [] if sd.get("status") == "verified"]
        tot = [sd for sd in sds if sd.get("field") == "n_total" and isinstance(sd.get("value"), (int, float))]
        if tot:
            values.add(float(tot[0]["value"]))
            continue
        n1 = [sd for sd in sds if sd.get("field") == "n1" and isinstance(sd.get("value"), (int, float))]
        n2 = [sd for sd in sds if sd.get("field") == "n2" and isinstance(sd.get("value"), (int, float))]
        if n1 and n2:
            values.add(float(n1[0]["value"]) + float(n2[0]["value"]))
    if len(values) == 1:
        v = values.pop()
        return v if v > 0 else None
    return None


# =============================================================================================
# számítás
# =============================================================================================

def compute_overlap(reviews, studies_doc, level="study", scope=None, sample_sizes=None, now=None):
    """Az ``overlap.json`` dokumentum (``szk.ma.headhunter.overlap/v1``): mátrix, N, r, c, CCA (összesen és
    páronként, sávval), wCCA (csak ellenőrzött mintaelemszámmal), kezdőbarát értelmezés.

    ``sample_sizes``: ``{sor-kulcs: n}`` — a hívó által adott, ELLENŐRZÖTT mintaelemszámok (pl. az EP6 után az
    elsődleges közleményből); ha nincs megadva, a jelöltek ``verified`` másodlagos adataiból."""
    cols, rows, notes = build_matrix(reviews, studies_doc, level=level, scope=scope)
    c = len(cols)
    r = len(rows)
    N = sum(sum(1 for x in row["in"] if x) for row in rows)
    pct = cca(N, r, c)
    doc = {
        "schema": OVERLAP_SCHEMA,
        "model": _d.MODEL,
        "generated": _d.utc_now(now),
        "level": level,
        "scope": scope or None,
        "reviews": cols,
        "rows": [{"key": row["key"], "label": row["label"], "in": row["in"], "index_review": row["index_review"],
                  "n_reviews": sum(1 for x in row["in"] if x), "rec_ids": row["rec_ids"]} for row in rows],
        "N": N,
        "r": r,
        "c": c,
        "cca_pct": round1(pct),
        "band": band(pct),
        "pairs": [],
        "wcca_pct": None,
        "per_review": [],
    }
    for j, rv in enumerate(cols):
        n_j = sum(1 for row in rows if row["in"][j])
        unique = sum(1 for row in rows if row["in"][j] and sum(1 for x in row["in"] if x) == 1)
        doc["per_review"].append({"review_id": rv, "n": n_j, "unique": unique,
                                  "index_for": sum(1 for row in rows if row["index_review"] == rv)})
    for a in range(c):
        for b in range(a + 1, c):
            n_a = sum(1 for row in rows if row["in"][a])
            n_b = sum(1 for row in rows if row["in"][b])
            shared = sum(1 for row in rows if row["in"][a] and row["in"][b])
            p = pair_cca(shared, n_a, n_b)
            doc["pairs"].append({"a": cols[a], "b": cols[b], "n_a": n_a, "n_b": n_b, "shared": shared,
                                 "cca_pct": round1(p), "band": band(p)})
    # wCCA — csak ellenőrzött mintaelemszámmal
    if c >= 2 and r > 0:
        weights = []
        for row in rows:
            n = (sample_sizes or {}).get(row["key"]) if sample_sizes is not None else _verified_n(reviews, row)
            weights.append(math.sqrt(n) if isinstance(n, (int, float)) and n > 0 else None)
        w = wcca([sum(1 for x in row["in"] if x) for row in rows], weights, c)
        doc["wcca_pct"] = round1(w)
        if w is None:
            missing = sum(1 for x in weights if x is None)
            notes.append({"code": "wcca_unavailable",
                          "hu": "wCCA nem számolható: %d sorhoz nincs ellenőrzött (az elsődleges közleménnyel egyeztetett) "
                                "mintaelemszám. Az áttekintésből vett szám másodlagos adat (EP6)." % missing,
                          "en": "wCCA not computed: %d rows have no verified (checked against the primary report) sample "
                                "size. Numbers taken from reviews are secondary data (EP6)." % missing})
        else:
            doc["wcca_band"] = band(w)
    doc["notes"] = interpret(doc)
    doc["warnings"] = notes
    doc["kb_refs"] = list(KB_OVERLAP)
    return doc


def interpret(doc):
    """Kezdőbarát értelmezés (Hennessy & Johnson 2020 lépései szerint) — ``{hu, en}``."""
    c, r, N = doc.get("c"), doc.get("r"), doc.get("N")
    lvl_hu = "vizsgálat" if doc.get("level") == "study" else "közlemény"
    lvl_en = "study" if doc.get("level") == "study" else "report"
    if doc.get("cca_pct") is None:
        return {"hu": "A CCA nem értelmezhető (legalább két áttekintés és egy bevont %s kell; most c = %s, r = %s)."
                      % (lvl_hu, c, r),
                "en": "CCA is not defined (at least two reviews and one included %s are needed; now c = %s, r = %s)."
                      % (lvl_en, c, r)}
    b = doc.get("band")
    hu = ["Összesített átfedés (%s-szint): CCA = %.1f%% (%s; N = %d, r = %d, c = %d). A sávok útmutatók, nem merev "
          "szabályok (Pieper 2014; Ying 2025)." % (lvl_hu, doc["cca_pct"], BAND_LABELS[b]["hu"], N, r, c)]
    en = ["Overall overlap (%s level): CCA = %.1f%% (%s; N = %d, r = %d, c = %d). Bands are guidance, not strict rules "
          "(Pieper 2014; Ying 2025)." % (lvl_en, doc["cca_pct"], BAND_LABELS[b]["en"], N, r, c)]
    vh = [p for p in doc.get("pairs") or [] if p.get("band") == "very_high"]
    low = [p for p in doc.get("pairs") or [] if p.get("band") == "slight"]
    if vh:
        hu.append("Nagyon magas páronkénti átfedés: %s — ha az egyik a másik frissítése (pl. ugyanazon szerzők), a "
                  "régebbi helyettesíthető." % ", ".join("%s–%s (%.1f%%)" % (p["a"], p["b"], p["cca_pct"]) for p in vh[:5]))
        en.append("Very high pairwise overlap: %s — if one updates the other (e.g. same authors), the older review "
                  "can be replaced." % ", ".join("%s–%s (%.1f%%)" % (p["a"], p["b"], p["cca_pct"]) for p in vh[:5]))
    if low:
        hu.append("Enyhe páronkénti átfedés: %s — azonos PICO mellett ez eltérő kritériumokra vagy keresési hiányra "
                  "utalhat; a frissítő és a saját keresés jelentősége nő." %
                  ", ".join("%s–%s" % (p["a"], p["b"]) for p in low[:5]))
        en.append("Slight pairwise overlap: %s — with the same PICO this may indicate different criteria or search "
                  "gaps; the update search and your own search matter more." %
                  ", ".join("%s–%s" % (p["a"], p["b"]) for p in low[:5]))
    hu.append("Bányászatnál az átfedés nem torzítás (a duplumokat összevonjuk): azt mutatja, mennyire ugyanazt az "
              "irodalmat találták a korábbi áttekintések.")
    en.append("When mining, overlap is not a bias (duplicates are merged): it shows how far earlier reviews found the "
              "same literature.")
    return {"hu": " ".join(hu), "en": " ".join(en)}


def matrix_csv(doc, reviews=None):
    """A mátrix CSV-ben (``;`` elválasztó, UTF-8): ``kulcs;cimke;<áttekintések…>;index_attekintes;attekintesek_szama``.
    A fejlécben az áttekintés címkéje „Elsőszerző Év (review_id)", ha a ``reviews`` megadott."""
    names = {}
    for r in reviews or []:
        b = r.get("bib") or {}
        sn = _d.surname_display(b.get("first_author"))
        names[r["review_id"]] = ("%s %s (%s)" % (sn, b.get("year"), r["review_id"])) if sn and b.get("year") \
            else r["review_id"]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    from .merge import excel_safe          # SEC-6: a címkék külső metaadatból jönnek (képlet-védelem)
    w.writerow(["kulcs", "cimke"] + [excel_safe(names.get(c, c)) for c in doc["reviews"]] + ["index_attekintes",
                                                                                             "attekintesek_szama"])
    for row in doc["rows"]:
        w.writerow([excel_safe(row["key"]), excel_safe(row["label"])] + ["1" if x else "0" for x in row["in"]] +
                   [row.get("index_review") or "", sum(1 for x in row["in"] if x)])
    w.writerow([])
    w.writerow(["N", doc["N"]])
    w.writerow(["r", doc["r"]])
    w.writerow(["c", doc["c"]])
    w.writerow(["CCA_%", "" if doc["cca_pct"] is None else ("%.1f" % doc["cca_pct"])])
    w.writerow(["sav", doc["band"] or ""])
    return buf.getvalue()


def run_overlap(project_dir, level="study", scope=None, csv=False, now=None, sample_sizes=None):
    """L6 a projekten: ``overlap.json`` (+ ``exports/overlap_matrix.csv``, ha ``csv``). Hálózatot nem használ.
    Visszaad: ``{ok, cca_pct, band, N, r, c, pairs, warnings, files, next}``."""
    hd = _d.hh_dir(project_dir)
    reviews = _d.load_reviews(project_dir)
    studies = _d.load_studies(project_dir)
    if not studies:
        return {"ok": False, "errors": [{"hu": "Még nincs studies.json — előbb futtasd a resolve és a dedupe lépést.",
                                         "en": "No studies.json yet — run resolve and dedupe first."}],
                "next": "resolve"}
    doc = compute_overlap(reviews, studies, level=level, scope=scope, sample_sizes=sample_sizes, now=now)
    files = []
    with _d.ProjectLock(project_dir):
        _d.write_json_atomic(os.path.join(hd, "overlap.json"), doc)
        files.append("01_kereses/headhunter/overlap.json")
        if csv:
            path = os.path.join(hd, "exports", "overlap_matrix.csv")
            if not os.path.isdir(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(matrix_csv(doc, reviews))
            files.append("01_kereses/headhunter/exports/overlap_matrix.csv")
    return {"ok": True, "cca_pct": doc["cca_pct"], "band": doc["band"], "N": doc["N"], "r": doc["r"], "c": doc["c"],
            "pairs": doc["pairs"], "wcca_pct": doc["wcca_pct"], "notes": doc["notes"], "warnings": doc["warnings"],
            "files": files, "next": "screen"}
