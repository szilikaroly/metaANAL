# -*- coding: utf-8 -*-
"""Metaheadhunter — gépi ellenőrzések (H-kódok, TERV 15. fejezet): ``RULES``, ``RULE_STAGES``, ``KB_REFS`` és
``verify(project_dir)``.

Kezdőknek: a ``verify`` parancs ezeket futtatja. Minden találat kimondja, MI a baj, HOL (fájl/azonosító), MIT
tegyél (javasolt parancs), és melyik tudásbázis-szabályra épül (``kb show D-S…``). A ``RULES`` a motor
``validate.RULES``/``prisma.RULES`` alakját követi: ``{kód: (súlyosság, cím, teendő, forrás)}`` — így bekötés után
a tudásbázis a motor-szabályok közé veheti (TERV 21/5).

A találat alakja (a projekt-audit X-találataival összhangban)::

    {code, severity, stage, title, detail, artifacts[], suggested_command[], kb_refs[], explain{hu,en}, hu, en}
"""
from __future__ import absolute_import

import io
import os
import re

from . import state as S

#: kód → (súlyosság, cím, teendő, forrás) — a 15. fejezet táblája
RULES = {
    "H001": ("error", "Sémahiba egy headhunter-fájlban",
             "A hibás fájlt ne szerkeszd kézzel: futtasd újra a lépést, amely írta (vagy: rebuild).",
             "TERV_metaheadhunter.md 4. fejezet (adatmodell)"),
    "H002": ("error", "Bevont-vizsgálat állítás bizonyíték nélkül (vagy a bizonyíték nem létezik)",
             "Minden bevonás-állításhoz kell bizonyíték (áttekintés + hely + ≤ 300 karakteres szó szerinti idézet): "
             "futtasd újra az extract lépést, vagy vesd el a jelöltet (exclude --target rv-…#c…).",
             "TERV N1; PRISMA 2020 5. és 16a pont"),
    "H003": ("warning", "API-val meg nem erősített azonosító",
             "Futtasd a resolve lépést; a felhasználó vagy az áttekintés által adott azonosító csak API-megerősítés "
             "után számít (merge/exportnál hiba).",
             "TERV N1, 7. fejezet"),
    "H004": ("error", "Az ágens idézete nem található a forrásszövegben",
             "Az ágens javaslatát a program elutasította: ellenőrizd a show-text kimenetét, és idézz szó szerint.",
             "TERV 6.2 (ágens-osztályozás)"),
    "H005": ("error", "Érvénytelen vagy nem létező azonosító",
             "Formailag hibás vagy az API szerint nem létező PMID/DOI/PMCID/NCT: javítsd a feloldási döntést "
             "(decide --target p-res-… --value option:N|pmid:…).",
             "TERV 7. fejezet"),
    "H006": ("warning", "A kinyert vizsgálatszám eltér az áttekintés által közölttől",
             "Nézd át az EP2-ben a jelölteket: hiányzó tábla-sor, összevont vizsgálat vagy több közleményes vizsgálat?",
             "TERV 6.4"),
    "H007": ("error", "Azonosító-ütközés (pl. azonos DOI két PMID-del)",
             "Döntsd el az ütközési javaslatot (proposals --kind id_conflict).",
             "TERV 8.2"),
    "H008": ("warning", "A forrás-áttekintés keresési dátuma nem közölt, becsült",
             "A frissítő keresés ablaka a becsült dátumtól indul: hagyd jóvá (update --dry-run → update --actor …) vagy "
             "add meg kézzel (--anchor manual --start …).",
             "TERV 6.0/2, 12.1"),
    "H009": ("error", "Nyitott emberi ellenőrzőpont a lezáró lépésnél",
             "Előbb zárd le a nyitott EP1–EP4 tételeket (status), csak utána signoff / export --for-analysis.",
             "TERV N3, 2. fejezet"),
    "H010": ("warning", "Ellenőrizetlen másodlagos adat",
             "Az áttekintésből átvett számot az elsődleges közleménnyel ellenőrizd (verify-secondary …); elemzési "
             "exportnál (--for-analysis) ez hiba.",
             "TERV N2, 11. fejezet"),
    "H011": ("error", "A PRISMA-számok nem mennek át a motor ellenőrzésén",
             "Nézd meg a prisma --check kimenetét (P-kódok); a hiányzó döntéseket pótold, majd merge és prisma újra.",
             "PRISMA 2020 1. ábra; TERV 13. fejezet"),
    "H012": ("error", "Hiányos keresés (lapozás megszakadt / felső korlát)",
             "Szűkítsd a lekérdezést vagy emeld a korlátot (--cap / --max), és futtasd újra — a PRISMA-szám csak "
             "teljes keresésből véglegesíthető.",
             "PRISMA-S; TERV 12.2"),
    "H013": ("error", "Visszavont közlemény a bevont halmazban",
             "Zárd ki a visszavont közleményt (exclude --target st-… --reason-code …), vagy indokold a megtartását.",
             "Cochrane Handbook 4.4.6"),
    "H014": ("warning", "Forrás nem érhető el, a lépés részleges",
             "Ellenőrizd a forrásokat (sources --check); kvótánál várj a visszaállásig vagy adj kulcsot, majd futtasd "
             "újra a lépést.",
             "TERV 3.2"),
    "H015": ("error", "A döntésnapló hash-lánca sérült",
             "A decisions.jsonl-t kézzel nem szabad szerkeszteni: állítsd vissza a verziókövetésből.",
             "TERV 4.6"),
    "H016": ("error", "Titok-szivárgás gyanúja (kulcs vagy e-mail egy kimeneti fájlban)",
             "Töröld a kulcsot/e-mailt a fájlból, a kulcsot pedig cseréld le az Elsevier/OpenAlex fiókodban.",
             "TERV 3.5"),
    "H017": ("warning", "Elavult kimenet (egy korábbi lépés változott)",
             "Futtasd újra a jelzett lépést (status mutatja a sorrendet); lezárás után újra signoff kell.",
             "TERV 4.6"),
    "H018": ("warning", "Áttekintések közti adat-ellentmondás",
             "Ugyanarra a vizsgálatra az áttekintések eltérő számot közölnek: az elsődleges közleményből dönts (EP6).",
             "TERV N2, 11. fejezet"),
    "H019": ("error", "Teljes szöveg (vagy annak gyanúja) a headhunter-mappában",
             "Teljes szöveget a projektben nem tárolunk (szerzői jog): töröld a fájlt; csak ≤ 300 karakteres idézet "
             "maradhat.",
             "TERV N4"),
    "H020": ("error", "Közlemény több vizsgálatban, vagy vizsgálat nélküli aktív közlemény",
             "Futtasd: rebuild (vagy dedupe); ha megmarad, a kapcsolási döntéseket nézd át (proposals).",
             "TERV 8.3"),
}

#: kód → szakasz (S00…S14)
RULE_STAGES = {
    "H001": "S03", "H002": "S03", "H003": "S04", "H004": "S03", "H005": "S04", "H006": "S03", "H007": "S04",
    "H008": "S03", "H009": "S04", "H010": "S05", "H011": "S14", "H012": "S03", "H013": "S04", "H014": "S03",
    "H015": "S04", "H016": "S00", "H017": "S03", "H018": "S05", "H019": "S03", "H020": "S04",
}

#: kód → tudásbázis-szabály (15. fejezet; H001, H015, H017: nincs)
KB_REFS = {
    "H002": ["D-S03-102"], "H003": ["D-S03-103"], "H004": ["D-S03-102"], "H005": ["D-S03-103"],
    "H006": ["D-S03-102"], "H007": ["D-S04-101"], "H008": ["D-S03-104"], "H009": ["D-S04-104"],
    "H010": ["D-S05-101"], "H011": ["D-S14-101"], "H012": ["D-S03-105"], "H013": ["D-S04-103"],
    "H014": ["D-S03-106"], "H016": ["D-S00-101"], "H018": ["D-S05-102"], "H019": ["D-S03-107"],
    "H020": ["D-S04-102"],
}

_EN_TITLES = {
    "H001": "Schema error in a headhunter file", "H002": "Inclusion claim without evidence",
    "H003": "Identifier not confirmed by an API", "H004": "Agent quote not found in the source text",
    "H005": "Invalid or non-existent identifier", "H006": "Extracted study count differs from the review's",
    "H007": "Identifier conflict", "H008": "Source review search date not reported (estimated)",
    "H009": "Open human checkpoint at a closing step", "H010": "Unverified secondary data",
    "H011": "PRISMA counts fail the engine check", "H012": "Incomplete search (paging stopped / cap)",
    "H013": "Retracted publication in the included set", "H014": "Source unavailable, step partial",
    "H015": "Decision log hash chain broken", "H016": "Possible secret leak", "H017": "Stale output",
    "H018": "Data conflict between reviews", "H019": "Full text (suspected) in the headhunter folder",
    "H020": "Report in several studies, or active report without a study",
}

_ID_FORMATS = {
    "pmid": re.compile(r"^[1-9]\d{0,8}$"),
    "pmcid": re.compile(r"^PMC\d{1,9}$"),
    "doi": re.compile(r"^10\.\d{4,9}/\S+$"),
    "nct": re.compile(r"^NCT\d{8}$"),
    "eid": re.compile(r"^2-s2\.0-\d+$"),
    "openalex": re.compile(r"^W\d+$"),
}

_FULLTEXT_RE = re.compile(r"<body[\s>]|<sec[\s>]")

#: a teljes-szöveg gyanújának küszöbe sima szövegnél (karakter; a ≤ 300 karakteres idézetek messze alatta)
LONG_TEXT_FIELD = 5000


def finding(code, detail_hu, detail_en=None, artifacts=(), suggested=(), severity=None, **extra):
    """Egy H-találat a 15. fejezet alakjában."""
    sev, title, action, _src = RULES[code]
    f = {"code": code, "severity": severity or sev, "stage": RULE_STAGES[code], "title": title,
         "detail": detail_hu, "artifacts": [a for a in artifacts if a], "suggested_command": list(suggested),
         "kb_refs": list(KB_REFS.get(code, [])),
         "explain": {"hu": "%s — %s Teendő: %s" % (title, detail_hu, action),
                     "en": "%s — %s" % (_EN_TITLES[code], detail_en or detail_hu)},
         "hu": detail_hu, "en": detail_en or detail_hu}
    f.update(extra)
    return f


def _walk_files(project_dir):
    hd = S.hh_dir(project_dir)
    for root, dirs, files in os.walk(hd):
        dirs[:] = [d for d in dirs if d not in ("cache",)]
        for fn in files:
            yield os.path.join(root, fn)


def _check_files(project_dir, env=None):
    out = []
    for fp in _walk_files(project_dir):
        try:
            with io.open(fp, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except (OSError, IOError):
            continue
        rel = S.rel(project_dir, fp)
        if S.secret_leaks(text, env):
            out.append(finding("H016", "Titok-szivárgás gyanúja: %s" % rel, "Possible secret leak: %s" % rel,
                               artifacts=[rel]))
        if _FULLTEXT_RE.search(text):
            out.append(finding("H019", "Teljes szöveg gyanúja (JATS-töredék): %s" % rel,
                               "Full-text suspicion (JATS fragment): %s" % rel, artifacts=[rel]))
    return out


def _review_checks(project_dir, reviews):
    out = []
    for r in reviews:
        rid = r.get("review_id")
        art = "%s/reviews/%s.json" % (S.HH_REL, rid)
        errs = S.schema_errors(r, "review")
        if errs:
            out.append(finding("H001", "reviews/%s.json: sémahiba (%s)" % (rid, "; ".join(errs[:3])),
                               "reviews/%s.json: schema error" % rid, artifacts=[art],
                               suggested=["extract %s --review %s" % (project_dir, rid)]))
        evs = dict((e.get("evidence_id"), e) for e in r.get("evidence") or [])
        for c in r.get("candidates") or []:
            if c.get("role_in_review") in ("included", "included_companion") and c.get("status") != "rejected":
                if not any(e in evs for e in c.get("evidence_ids") or []):
                    out.append(finding("H002", "%s#%s: bevonás-állítás bizonyíték nélkül." % (rid, c.get("cand_id")),
                                       "%s#%s: inclusion claim without evidence." % (rid, c.get("cand_id")),
                                       artifacts=[art]))
        for e in r.get("evidence") or []:
            if len(e.get("quote") or "") > 300:
                out.append(finding("H002", "%s: 300 karakternél hosszabb idézet." % e.get("evidence_id"),
                                   "%s: quote longer than 300 characters." % e.get("evidence_id"), artifacts=[art]))
        if r.get("status") != "selected":
            continue
        sd = r.get("search_date") or {}
        if sd.get("fallback") or not sd.get("value"):
            out.append(finding("H008", "%s: a keresési dátum nem közölt, becsült (%s)." % (rid, sd.get("value")),
                               "%s: search date estimated (%s)." % (rid, sd.get("value")), artifacts=[art],
                               suggested=["update %s --dry-run" % project_dir]))
        k = (r.get("k_reported") or {}).get("value")
        inc = [c for c in r.get("candidates") or [] if c.get("role_in_review") in ("included",)
               and c.get("status") != "rejected"]
        groups = set((c.get("study_group") or c.get("cand_id")) for c in inc)
        if k and inc and len(groups) != int(k):
            out.append(finding("H006", "%s: az áttekintés %s vizsgálatot közöl, a kinyerés %d vizsgálat-csoportot talált."
                               % (rid, k, len(groups)), "%s: review reports %s studies, extraction found %d."
                               % (rid, k, len(groups)), artifacts=[art],
                               suggested=["list %s candidates --review %s" % (project_dir, rid)]))
    return out


def _id_format_problems(records):
    out = []
    for r in records or []:
        if r.get("status") == "merged_into":
            continue
        for k, v in (r.get("ids") or {}).items():
            val = v.get("value") if isinstance(v, dict) else v
            rx = _ID_FORMATS.get(k)
            if rx and val is not None and not rx.match(str(val)):
                out.append((r.get("rec_id"), k, val))
    return out


def _studies_checks(project_dir, studies):
    out = []
    if not studies:
        return out
    art = "%s/studies.json" % S.HH_REL
    errs = S.schema_errors(studies, "studies")
    if errs:
        out.append(finding("H001", "studies.json: sémahiba (%s)" % "; ".join(errs[:3]), "studies.json: schema error",
                           artifacts=[art], suggested=["rebuild %s" % project_dir]))
    for rec_id, k, val in _id_format_problems(studies.get("records")):
        out.append(finding("H005", "%s: formailag hibás %s: %r." % (rec_id, k, val), "%s: malformed %s: %r."
                           % (rec_id, k, val), artifacts=[art], suggested=["resolve %s --force" % project_dir]))
    for p in studies.get("proposals") or []:
        if p.get("kind") == "id_conflict" and p.get("status") == "pending":
            out.append(finding("H007", "%s: azonosító-ütközés vár döntésre." % p.get("proposal_id"),
                               "%s: identifier conflict pending." % p.get("proposal_id"), artifacts=[art],
                               suggested=["proposals %s --kind id_conflict" % project_dir]))
    try:
        from . import dedup
        for m in dedup.membership_problems(studies):
            out.append(finding("H020", "%s: %s (%s)." % (m.get("rec_id"), m.get("problem"),
                                                         ", ".join(m.get("studies") or []) or "-"),
                               "%s: %s." % (m.get("rec_id"), m.get("problem")), artifacts=[art],
                               suggested=["rebuild %s" % project_dir]))
    except ImportError:  # pragma: no cover
        pass
    return out


def _merged_checks(project_dir, merged, for_analysis=False):
    out = []
    if not merged:
        return out
    art = "%s/merged.json" % S.HH_REL
    errs = S.schema_errors(merged, "merged")
    if errs:
        out.append(finding("H001", "merged.json: sémahiba (%s)" % "; ".join(errs[:3]), "merged.json: schema error",
                           artifacts=[art], suggested=["merge %s" % project_dir]))
    for s in merged.get("studies") or []:
        sid, label = s.get("study_id"), s.get("label")
        flags = s.get("flags") or []
        if s.get("conflicts"):
            out.append(finding("H018", "%s (%s): %d adat-ellentmondás az áttekintések között." % (
                sid, label, len(s["conflicts"])), "%s: data conflicts between reviews." % sid, artifacts=[art]))
        if s.get("status") == "included" and "retracted" in flags and \
                "retracted_retention_documented" not in flags:
            out.append(finding("H013", "%s (%s): visszavont közlemény a bevont halmazban." % (sid, label),
                               "%s: retracted publication included." % sid, artifacts=[art],
                               suggested=["exclude %s --target %s --reason-code …" % (project_dir, sid),
                                          "decide %s --target %s --value keep_retracted --reason \"…\" "
                                          "--actor user:<név>" % (project_dir, sid)]))
        if s.get("status") == "included" and "unresolved_ids" in flags:
            out.append(finding("H003", "%s (%s): API-val meg nem erősített azonosító." % (sid, label),
                               "%s: identifier not confirmed by an API." % sid, artifacts=[art],
                               severity="error" if merged.get("final") else None,
                               suggested=["resolve %s" % project_dir]))
    n = (merged.get("summary") or {}).get("secondary_unverified_included") or 0
    if n:
        out.append(finding("H010", "%d ellenőrizetlen másodlagos érték a bevont vizsgálatoknál (EP6)." % n,
                           "%d unverified secondary values (EP6)." % n, artifacts=[art],
                           severity="error" if for_analysis else None,
                           suggested=["verify-secondary %s --study st-… --field … --status verified "
                                      "--primary-locator … --actor user:<név>" % project_dir]))
    return out


def _state_checks(project_dir, state, update_doc, decisions, merged):
    out = []
    art = "%s/state.json" % S.HH_REL
    errs = S.schema_errors(state, "state")
    if errs:
        out.append(finding("H001", "state.json: sémahiba (%s)" % "; ".join(errs[:3]), "state.json: schema error",
                           artifacts=[art]))
    if update_doc:
        errs = S.schema_errors(update_doc, "update-search")
        if errs:
            out.append(finding("H001", "update_search.json: sémahiba (%s)" % "; ".join(errs[:3]),
                               "update_search.json: schema error", artifacts=["%s/update_search.json" % S.HH_REL]))
    chain = S.verify_chain(decisions)
    if chain:
        out.append(finding("H015", "A döntésnapló hash-lánca sérült (%d hiba)." % len(chain),
                           "Decision log hash chain broken (%d problems)." % len(chain),
                           artifacts=["%s/decisions.jsonl" % S.HH_REL]))
    current = set(q.get("search_id") for q in (update_doc or {}).get("queries") or [])
    current |= set(c.get("search_id") for c in (update_doc or {}).get("citation_search") or [])
    for s in state.get("searches") or []:
        if s.get("complete") is not False:
            continue
        purpose = s.get("purpose") or ""
        if purpose.startswith(("update_", "citation_")) and s.get("search_id") not in current:
            continue  # egy későbbi futás felváltotta (a napló megmarad, PRISMA-S)
        if purpose == "review_discovery":
            out.append(finding("H012", "Az áttekintés-keresés nem teljes: %s (%s) — a felső korlát (--max) vagy a "
                                       "lapozás megállította; lehet, hogy kimaradt áttekintés." % (
                                           s.get("search_id"), s.get("source")),
                               "Review discovery incomplete: %s." % s.get("search_id"), severity="warning",
                               artifacts=[art], suggested=["find %s --max 1000" % project_dir]))
        else:
            out.append(finding("H012", "Hiányos keresés: %s (%s) — a PRISMA-szám nem véglegesíthető; szűkíts vagy "
                                       "emeld a korlátot (--cap), és futtasd újra." % (s.get("search_id"),
                                                                                     s.get("source")),
                               "Incomplete search: %s." % s.get("search_id"), artifacts=[art],
                               suggested=["update %s --cap 10000 --actor user:<név>" % project_dir]))
    for k, v in sorted((state.get("sources") or {}).items()):
        if v.get("enabled") and v.get("status") in ("unreachable", "rate_limited", "unauthorized", "forbidden"):
            out.append(finding("H014", "%s: %s" % (k, v.get("status")), "%s: %s" % (k, v.get("status")),
                               artifacts=[art], suggested=["sources %s --check" % project_dir], source=k))
    for step, info in sorted((state.get("steps") or {}).items()):
        if (info or {}).get("status") == "stale":
            out.append(finding("H017", "Elavult lépés: %s — futtasd újra." % step, "Stale step: %s." % step,
                               artifacts=[art]))
    if merged and merged.get("final"):
        cps = dict((c.get("id"), c) for c in state.get("checkpoints") or [])
        open_eps = [ep for ep in ("EP1", "EP2", "EP3", "EP4")
                    if (cps.get(ep) or {}).get("status") == "pending" and (cps.get(ep) or {}).get("open_items")]
        if open_eps:
            out.append(finding("H009", "A lezárt halmaz mellett nyitott ellenőrzőpont: %s." % ", ".join(open_eps),
                               "Open checkpoints after sign-off: %s." % ", ".join(open_eps), artifacts=[art],
                               suggested=["status %s" % project_dir]))
    return out


def _prisma_checks(project_dir, merged):
    out = []
    flow = S.read_json(S.path(project_dir, S.FILES["prisma"])) if "prisma" in S.FILES else None
    if not flow or not merged:
        return out
    try:
        from . import prisma_map
        res = prisma_map.check(flow)
    except Exception as exc:  # pragma: no cover - a motor hiányában
        return [finding("H011", "A PRISMA-ellenőrzés nem futott: %s" % exc, artifacts=[S.HH_REL + "/prisma_flow.json"],
                        severity="warning")]
    errs = [f for f in res.get("findings") or [] if f.get("severity") == "error"]
    if errs:
        out.append(finding("H011", "A motor PRISMA-ellenőrzése hibát talált: %s." % ", ".join(
            "%s %s" % (f.get("code"), f.get("title") or "") for f in errs[:5]),
            "Engine PRISMA check failed: %s." % ", ".join(f.get("code") or "" for f in errs[:5]),
            artifacts=[S.HH_REL + "/prisma_flow.json"], suggested=["prisma %s --check" % project_dir]))
    return out


def verify(project_dir, for_analysis=False, env=None):
    """Minden H-ellenőrzés a projekten (hálózat nélkül). Visszaad: ``{findings, n_error, n_warning, checker}``."""
    state = S.load_state(project_dir)
    reviews = S.load_reviews(project_dir)
    studies = S.read_json(S.path(project_dir, S.FILES["studies"]))
    merged = S.read_json(S.path(project_dir, S.FILES["merged"]))
    update_doc = S.read_json(S.path(project_dir, S.FILES["update"]))
    decisions = S.read_decisions(project_dir)
    findings = []
    findings += _state_checks(project_dir, state, update_doc, decisions, merged)
    findings += _review_checks(project_dir, reviews)
    findings += _studies_checks(project_dir, studies)
    findings += _merged_checks(project_dir, merged, for_analysis=for_analysis)
    findings += _prisma_checks(project_dir, merged)
    findings += _check_files(project_dir, env=env)
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["code"]))
    n_err = sum(1 for f in findings if f["severity"] == "error")
    return {"findings": findings, "n_error": n_err, "n_warning": len(findings) - n_err,
            "checker": "metaelemzes.headhunter.checks"}


def engine_rules():
    """A ``RULES`` a motor-szabályok formátumában (a ``kb.ENGINE_RULESETS`` bekötéséhez)."""
    return dict(RULES)


__all__ = ["RULES", "RULE_STAGES", "KB_REFS", "verify", "finding", "engine_rules"]
