# -*- coding: utf-8 -*-
"""Metaheadhunter — összefoglaló jelentés: ``exports/report.md`` (``report`` parancs).

Kezdőknek: a jelentés a módszertani leíráshoz (PRISMA 2020: 7. pont „egyéb módszerek", PRISMA-S) ad nyersanyagot:
mely forrás-áttekintésekből bányásztál, milyen keresési dátummal, hány bevont vizsgálatot nyertél ki és milyen
úton, hány közlemény lett egy vizsgálat (társközlemények), mekkora az átfedés (CCA), mi volt a frissítő keresés
ablaka és lekérdezése, és milyen PRISMA-számok jöttek ki. Csak bibliográfiai tényt és a fájlokban már meglévő
számokat írja ki — semmit nem számol újra és nem talál ki (N1); a nyitott ellenőrzőpontokat és a H-kódokat is
felsorolja, hogy látszódjon, mi nincs még kész.
"""
from __future__ import absolute_import

import os

from . import state as S

REPORT_REL = "exports/report.md"


def _md_escape(text):
    return str(text if text is not None else "—").replace("|", "\\|").replace("\n", " ")


def _table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        out.append("| " + " | ".join(_md_escape(x) for x in r) + " |")
    return out


def build_report(project_dir, lang="hu"):
    """A jelentés Markdown-szövege és a szakaszok listája (fájlt nem ír)."""
    from .merge import effective_reviews
    state = S.load_state(project_dir)
    reviews = effective_reviews(S.load_reviews(project_dir), S.read_decisions(project_dir))
    studies = S.read_json(S.path(project_dir, S.FILES["studies"])) or {}
    merged = S.read_json(S.path(project_dir, S.FILES["merged"])) or {}
    overlap = S.read_json(S.path(project_dir, S.FILES["overlap"])) or {}
    update_doc = S.read_json(S.path(project_dir, S.FILES["update"])) or {}
    flow = S.read_json(S.path(project_dir, S.FILES["prisma"])) or {}
    pico = state.get("pico") or {}
    L = []
    sections = []

    def sec(title):
        sections.append(title)
        L.append("")
        L.append("## " + title)
        L.append("")

    L.append("# Metaheadhunter — összefoglaló jelentés")
    L.append("")
    L.append("Készült: %s · mód: %s · eszköz: metaelemzes.headhunter" % (S.utc_now(), state.get("mode")))
    L.append("")
    L.append("> Ez a jelentés a projektfájlokból készül; minden szám a `01_kereses/headhunter/` fájljaiból jön. "
             "Az áttekintésekből átvett számok **másodlagos adatok** — elemzés előtt az elsődleges közleménnyel "
             "ellenőrizendők (N2, EP6).")

    sec("Kérdés és PICO")
    L.append("- Kérdés: %s" % pico.get("question"))
    for k, lab in (("population", "Populáció"), ("intervention", "Beavatkozás"), ("comparator", "Összehasonlítás"),
                   ("outcomes", "Kimenetek"), ("study_designs", "Vizsgálattípusok")):
        v = pico.get(k)
        if v:
            L.append("- %s: %s" % (lab, ", ".join(v) if isinstance(v, list) else v))
    for b in pico.get("query_blocks") or []:
        L.append("- Fogalomblokk %s: %s%s" % (b.get("concept"), ", ".join(b.get("terms") or []),
                                             (" · MeSH: " + ", ".join(b.get("mesh"))) if b.get("mesh") else ""))

    sec("Keresések (PRISMA-S)")
    rows = []
    for s in state.get("searches") or []:
        rows.append([s.get("search_id"), s.get("purpose"), s.get("platform") or s.get("source"), s.get("run_at"),
                     "%s – %s" % (s.get("date_from") or "", s.get("date_to") or "") if (s.get("date_from") or
                                                                                         s.get("date_to")) else "—",
                     s.get("count_total"), s.get("count_retrieved"), "igen" if s.get("complete") else "NEM",
                     (s.get("query") or "")[:400]])
    L.extend(_table(["Azonosító", "Cél", "Platform", "Futás", "Dátumablak", "Találat", "Letöltve", "Teljes",
                     "Lekérdezés"], rows) if rows else ["(még nem futott keresés)"])

    sec("Forrás-áttekintések (EP1)")
    sel = [r for r in reviews if r.get("status") == "selected"]
    rows = []
    for r in sorted(sel, key=lambda r: ((r.get("bib") or {}).get("year") or 0, r["review_id"])):
        b = r.get("bib") or {}
        sd = r.get("search_date") or {}
        last = (r.get("extraction_runs") or [{}])[-1]
        roles = {}
        for c in r.get("candidates") or []:
            roles[c.get("role_in_review")] = roles.get(c.get("role_in_review"), 0) + 1
        rows.append([r["review_id"], "%s %s" % (b.get("first_author") or "", b.get("year") or ""),
                     (b.get("title") or "")[:120], "%s%s" % (sd.get("value") or "—", " (becsült, H008)"
                                                            if sd.get("fallback") else ""),
                     (r.get("k_reported") or {}).get("value"), (r.get("fulltext") or {}).get("route"),
                     last.get("strategy"), ", ".join("%s: %d" % kv for kv in sorted(roles.items(),
                                                                                   key=lambda kv: str(kv[0])))])
    L.append("Kiválasztva: %d / %d jelölt áttekintés." % (len(sel), len(reviews)))
    L.append("")
    L.extend(_table(["Áttekintés", "Szerző, év", "Cím", "Keresési dátum", "Közölt k", "Teljes szöveg", "Kinyerés",
                     "Jelöltek szerepe"], rows) if rows else ["(nincs kiválasztott áttekintés)"])

    sec("Feloldás és duplumszűrés (EP3)")
    recs = studies.get("records") or []
    active = [r for r in recs if r.get("status") != "merged_into"]
    res_status = {}
    for r in recs:
        st = (r.get("resolution") or {}).get("status") or "—"
        res_status[st] = res_status.get(st, 0) + 1
    summ = studies.get("summary") or {}
    L.append("- Közlemény-rekordok: %d (aktív: %d, beolvasztva: %d)" % (len(recs), len(active),
                                                                      len(recs) - len(active)))
    L.append("- Feloldás: %s" % (", ".join("%s: %d" % kv for kv in sorted(res_status.items())) or "—"))
    L.append("- Vizsgálatok: %d (ebből több közleményes: %s)" % (len(studies.get("studies") or []),
                                                                  summ.get("multi_report_studies", "—")))
    props = studies.get("proposals") or []
    by_kind = {}
    for p in props:
        k = "%s/%s" % (p.get("kind"), p.get("status"))
        by_kind[k] = by_kind.get(k, 0) + 1
    L.append("- Javaslatok: %s" % (", ".join("%s: %d" % kv for kv in sorted(by_kind.items())) or "nincs"))

    sec("Átfedés a forrás-áttekintések között (CCA)")
    if overlap:
        L.append("- Szint: %s · N = %s, r = %s, c = %s → CCA = %s%% (%s)" % (
            overlap.get("level"), overlap.get("N"), overlap.get("r"), overlap.get("c"), overlap.get("cca_pct"),
            overlap.get("band")))
        if overlap.get("wcca_pct") is not None:
            L.append("- wCCA = %s%%" % overlap.get("wcca_pct"))
    else:
        L.append("(az overlap lépés még nem futott)")

    sec("Frissítő keresés")
    w = update_doc.get("window") or {}
    if w:
        L.append("- Ablak: %s – %s (horgony: %s, átfedés: %s hónap)" % (w.get("start_date"), w.get("end_date"),
                                                                      w.get("anchor"), w.get("overlap_months")))
        res = update_doc.get("results") or {}
        if isinstance(res, dict):
            L.append("- Eredmény: %s" % ", ".join("%s: %s" % (k, v) for k, v in sorted(res.items())
                                                  if isinstance(v, (int, float, str))))
    else:
        L.append("(a frissítő keresés még nem futott)")

    sec("Egyesített vizsgálatlista és PRISMA 2020")
    c = merged.get("counts") or {}
    if c:
        L.append("- " + ", ".join("%s: %s" % kv for kv in sorted(c.items())))
        L.append("- Lezárva (EP5): %s" % ("igen" if merged.get("final") else "nem"))
    else:
        L.append("(a merge még nem futott)")
    if flow:
        L.append("")
        L.extend(_table(["PRISMA-doboz", "Érték"], [[k, v] for k, v in sorted(flow.items())
                                                     if isinstance(v, (int, float)) and not isinstance(v, bool)]))

    sec("Nyitott tételek és gépi ellenőrzések")
    for cp in state.get("checkpoints") or []:
        if cp.get("status") == "pending" and cp.get("open_items"):
            L.append("- %s: %s tétel vár döntésre — %s" % (cp["id"], cp["open_items"],
                                                           S.CHECKPOINT_INFO.get(cp["id"], {}).get("hu", "")))
    try:
        from . import checks
        res = checks.verify(project_dir)
        for f in res["findings"][:80]:
            L.append("- [%s %s] %s" % (f["code"], f["severity"], f["hu"]))
        if not res["findings"]:
            L.append("- Nincs H-találat.")
    except Exception as exc:  # pragma: no cover
        L.append("- (az ellenőrzés nem futott: %s)" % exc)
    return "\n".join(L) + "\n", sections


def write_report(project_dir, lang="hu"):
    text, sections = build_report(project_dir, lang=lang)
    fp = S.path(project_dir, *REPORT_REL.split("/"))
    d = os.path.dirname(fp)
    if not os.path.isdir(d):
        os.makedirs(d)
    S.write_text_atomic(fp, text)
    return {"file": S.rel(project_dir, fp), "sections": sections, "warnings": []}


__all__ = ["build_report", "write_report", "REPORT_REL"]
