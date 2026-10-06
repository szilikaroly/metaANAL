#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Az értékelés-képernyők fejlesztői fixture-ei (ma_gui/web/fixtures/appraisal_*.json).

Alapból (FID-7) a MOTOR saját értékelő homlokzatával (``metaelemzes.api``: eszköz-definíciók, ellenőrzés, κ, összevont
egyezés, forgalmi lámpa, rob-szinkron) — a fixture-ök így a motor kimenetei, és a motor szerződéseinek
(metaelemzes/contracts) megfelelnek (sodródás-őr: tests/gui/test_v1_appraisal_drift.py). ``--stub``: a régi, a
validator referenciafájljaiból épített eszközök + ``tests/gui/_appraisal_engine_stub.py`` (csak összevetéshez).
A súlyok a BCG-példa motor-fixtúrájából (analysis_plots.json) jönnek.

Futtatás:  python3 tests/gui/ui/gen_appraisal_fixtures.py [--stub] [--check]
  --check   nem ír, csak összeveti a meglévő fájlokkal (1-es kód eltérésnél)"""
import argparse
import copy
import importlib.util
import json
import os
import re
import sys


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(HERE))

import _appraisal_engine_stub as STUB  # noqa: E402

FIX = os.path.join(ROOT, "ma_gui", "web", "fixtures")
DEFAULT_PLUGINS = "/home/user/szilikaroly/szk-plugins/plugins"
META = {"engine": "0.2.0", "elapsed_ms": 1, "project_rev": 41}
ITEM_RE = re.compile(r"^\*\*(?P<id>(?=[\w.\-]*\d)[A-Za-z0-9][\w.\-]*)\s*(?:\((?P<scope>[^)]*)\))?\s*(?:\*\*)?\s*[—–-]\s*"
                     r"(?P<text>.+?)\s*$")
CANON_ANSWER = {"yes": "yes", "probably yes": "probably_yes", "probably no": "probably_no", "no": "no",
                "no information": "no_information", "partial yes": "partial_yes", "unclear": "unclear",
                "not applicable": "not_applicable", "partly": "partly", "present": "present", "partial": "partial",
                "missing": "missing"}
CANON_VERDICT = {"low": "low", "some concerns": "some_concerns", "high": "high", "moderate": "moderate",
                 "serious": "serious", "critical": "critical", "no information": "no_information",
                 "very high": "very_high", "unclear": "unclear", "include": "include", "exclude": "exclude",
                 "seek further info": "seek_further_info", "critically low": "critically_low"}
FAMILY = {"rob2": "rob", "robins-i": "rob", "robins-e": "rob", "quadas2": "rob", "nos": "rob", "quips": "rob",
          "jbi": "rob", "probast-ai": "prediction", "tripod-ai": "reporting", "amstar2": "review", "robis": "review"}
UNIT = {"rob2": "result", "robins-i": "result", "robins-e": "result", "quadas2": "index_test", "nos": "study",
        "quips": "study", "jbi": "study", "probast-ai": "model", "tripod-ai": "study", "amstar2": "review",
        "robis": "review"}
ALG = {"rob2": "conservative", "robins-i": "conservative", "robins-e": "conservative", "quadas2": "conservative",
       "quips": "conservative", "nos": "count", "jbi": "none", "probast-ai": "none", "tripod-ai": "none",
       "amstar2": "published", "robis": "conservative"}
NAME_HU = {"rob2": "RoB 2 — randomizált vizsgálatok", "robins-i": "ROBINS-I — nem randomizált beavatkozás",
           "robins-e": "ROBINS-E — expozíció", "quadas2": "QUADAS-2 — diagnosztikus pontosság",
           "nos": "Newcastle–Ottawa skála", "quips": "QUIPS — prognosztikai tényező", "jbi": "JBI ellenőrzőlisták",
           "probast-ai": "PROBAST+AI — predikciós modell", "tripod-ai": "TRIPOD+AI — jelentési teljesség",
           "amstar2": "AMSTAR 2 — szisztematikus áttekintés", "robis": "ROBIS — áttekintés torzítási kockázata"}
DOMAIN_HU = {("rob2", "1"): "Randomizáció", ("rob2", "2"): "Eltérés a tervezett beavatkozástól",
             ("rob2", "3"): "Hiányzó kimeneti adatok", ("rob2", "4"): "A kimenet mérése",
             ("rob2", "5"): "Az eredmény szelektív közlése"}
HELP_HU = {
    ("rob2", "1.1"): "Azt kérdezi: valóban véletlen volt-e a csoportba sorolás (pl. számítógépes véletlenszám, "
                     "sorsolás). Ha csak annyit írnak, hogy „randomizáltuk”, módszer nélkül, a válasz NI (nincs "
                     "információ), nem PY. A váltakozó besorolás vagy a születési dátum szerinti beosztás nem véletlen: N.",
    ("rob2", "1.2"): "Előre tudhatták-e a beválogatók, ki melyik csoportba kerül? Központi randomizáció vagy "
                     "sorszámozott, átlátszatlan, lezárt borítékok: Y. Nyílt lista: N. Ez a tétel jelzi előre "
                     "legjobban a túlbecsült hatást.",
    ("rob2", "1.3"): "FORDÍTOTT tétel: itt az IGEN a probléma. Nem egyetlen eltérést nézünk, hanem a mintázatot: "
                     "erős prognosztikai tényezőben nagy eltérés, vagy sok változóban azonos irányú eltérés.",
    ("rob2", "2.1"): "IRÁNYÍTÓ kérdés: önmagában nem rontja az ítéletet (nyílt vizsgálatban mindig Y), csak azt "
                     "dönti el, kell-e a 2.3–2.5 kérdésekkel foglalkozni.",
    ("rob2", "2.6"): "Megfelelő volt-e az elemzés a besorolás hatásának becslésére? ITT vagy csak a kimeneti adat "
                     "nélküli résztvevőket kizáró „módosított ITT”: Y. A nem-adherens résztvevők kizárása vagy "
                     "as-treated elemzés: N.",
}


def env(schema, data, warnings=None, rid="q_ap"):
    return {"ok": True, "schema": schema, "data": data, "warnings": list(warnings or []),
            "meta": dict(META, request_id=rid)}


def i18n(hu, en=None):
    return {"hu": hu, "en": en if en is not None else hu}


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _help_paragraphs(text):
    """item-id → az utána álló magyarázó bekezdés (a következő tételig / címsorig)."""
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        m = ITEM_RE.match(line)
        if m or line.startswith("#"):
            if cur is not None and cur not in out:
                out[cur] = " ".join(x.strip() for x in buf if x.strip())
            cur, buf = (m.group("id") if m else None), []
            continue
        if cur is not None:
            buf.append(line)
    if cur is not None and cur not in out:
        out[cur] = " ".join(x.strip() for x in buf if x.strip())
    return out


def _answers(meta_answers):
    vals = [CANON_ANSWER[a.strip().lower()] for a in meta_answers.split("|") if a.strip().lower() in CANON_ANSWER]
    return STUB.answers_of(vals)


def _verdicts(key, meta_verdicts):
    if key == "nos":
        return STUB.verdicts_of(["good", "fair", "poor"])
    vals = [CANON_VERDICT[v.strip().lower()] for v in meta_verdicts.split("|") if v.strip().lower() in CANON_VERDICT]
    return STUB.verdicts_of(vals)


def build_instruments(plugins):
    vdir = os.path.join(plugins, "validator")
    ap = _load_module(os.path.join(vdir, "scripts", "appraise.py"), "_v_appraise")
    cl = _load_module(os.path.join(vdir, "scripts", "checklist.py"), "_v_checklist")
    version = _load_json(os.path.join(vdir, ".claude-plugin", "plugin.json"))["version"]
    tools = ap.load_all()
    out = {}
    for key, inst in sorted(tools.items()):
        if key in ("grade",):
            continue
        text = inst.path.read_text(encoding="utf-8")
        helps = _help_paragraphs(text)
        if key == "probast-ai":
            items, domains = [], []
            pv = cl.probast_items()
            for p in ("development", "evaluation"):
                for qid, title in pv[p]:
                    items.append(STUB._item(qid, qid.split(".")[0], title, pass_=p,
                                            help_text=i18n(helps.get(qid, ""), helps.get(qid, ""))))
            for did, name in sorted(cl.DOMAINS.items()):
                domains.append({"id": did, "title": i18n(name, name), "passes": ["development", "evaluation"],
                                "applicability": did != "4"})
            d = {"answers": _answers("Yes|Probably yes|Probably no|No|No information"),
                 "verdicts": STUB.verdicts_of(["low", "high", "unclear"]), "items": items, "domains": domains,
                 "passes": [{"id": "development", "label": i18n("Fejlesztés (minőség)", "Development (quality)")},
                            {"id": "evaluation", "label": i18n("Értékelés (torzítás)", "Evaluation (risk of bias)")}]}
        elif key == "tripod-ai":
            sections, cur = {}, "Title"
            for line in text.splitlines():
                h = re.match(r"^##+\s+(.+?)\s*$", line)
                if h and not h.group(1).startswith("TRIPOD+AI for Abstracts"):
                    cur = h.group(1)
                m = re.match(r"^\*\*(\d{1,2}[a-z]?)\s*\((D;E|D|E)\)\*\*", line)
                if m:
                    sections.setdefault(m.group(1), cur)
            order, items = [], []
            for iid, applies, title in cl.tripod_items():
                sec = sections.get(iid, "Other")
                if sec not in order:
                    order.append(sec)
                items.append(STUB._item(iid, sec, title, applies_to=applies,
                                        help_text=i18n(helps.get(iid, ""), helps.get(iid, ""))))
            d = {"answers": STUB.answers_of(["present", "partial", "missing", "not_applicable"]),
                 "status_vocab": ["present", "partial", "missing", "not_applicable"], "verdicts": [],
                 "items": items, "domains": [{"id": s, "title": i18n(s, s), "passes": None, "applicability": False}
                                             for s in order],
                 "scopes": ["both", "development", "evaluation"]}
        else:
            items = []
            crit = {c.strip() for c in inst.meta.get("critical", "").split(",") if c.strip()}
            for it in inst.items:
                tags = [t.strip().lower() for t in re.split(r"[;,/]", it["scope"])]
                kinds = [t for t in tags if t in ("router", "reverse")]
                stars = [t for t in tags if t.endswith("star")]
                scope = [t for t in tags if t not in kinds and t not in stars and t != "critical"] or ["all"]
                hhu = HELP_HU.get((key, it["id"]))
                hen = helps.get(it["id"], "")
                item = STUB._item(it["id"], it["domain"], it["text"], tags=kinds, scope=scope,
                                  help_text=i18n(hhu or hen, hen), critical=it["id"] in crit or "critical" in tags)
                if item["critical"] and "critical" not in item["tags"]:
                    item["tags"].append("critical")
                if stars:
                    item["stars"] = int(re.match(r"(\d+)", stars[0]).group(1))
                items.append(item)
            domains = [{"id": did, "title": i18n(DOMAIN_HU.get((key, did), name), name), "passes": None,
                        "applicability": key == "quadas2" and did in ("1", "2", "3")}
                       for did, name in inst.domains.items()]
            scopes = [s.strip() for s in inst.meta.get("scopes", "").split(",") if s.strip() and s.strip() != "all"]
            d = {"answers": _answers(inst.meta.get("answers", "Yes|No")),
                 "verdicts": _verdicts(key, inst.meta.get("verdicts", "Low|High|Unclear")), "items": items,
                 "domains": domains, "scopes": scopes}
        if key in ("amstar2",):
            # egyetlen tétel-lista (nincs doménje); az ítélet a „bizalom” skálája: magas = jó (kockázati szint: low)
            for it in d["items"]:
                it["domain"] = "1"
            d["domains"] = [{"id": "1", "title": i18n("Tételek (K = kritikus)", "Items (K = critical)"), "passes": None,
                             "applicability": False}]
            lv = {"high": "low", "moderate": "some", "low": "high", "critically_low": "high"}
            for v in d["verdicts"]:
                v["level"] = lv.get(v["value"], v["level"])
        alg = ALG.get(key, "conservative")
        doc = {"schema": "szk.instrument/v1", "key": key, "name": inst.meta.get("name", key),
               "name_i18n": i18n(NAME_HU.get(key, key), inst.meta.get("name", key)),
               "validator_version": version, "unit": UNIT.get(key, "study"), "family": FAMILY.get(key, "rob"),
               "source": {"kind": "fixture", "from": "szk-plugins validator %s (MIT)" % version},
               "rollup": {"algorithm": alg, "note": i18n(*STUB.ALG_TEXT[alg])}}
        doc.update(d)
        if key == "amstar2":
            doc["conventions"] = {"partial_yes_critical": {"default": "meets", "values": ["meets", "weakness"],
                                                           "kb": "AMSTAR2-00"}}
        doc["reference_sha256"] = STUB.sha(doc["items"])
        doc["counts"] = {"parsed": len(doc["items"]), "published": len(doc["items"])}
        if key == "probast-ai":
            doc["counts"]["per_pass"] = {p: sum(1 for it in doc["items"] if it["pass"] == p)
                                         for p in ("development", "evaluation")}
        out[key] = doc
    return out


# ---------------------------------------------------------------------------- mintaértékelések
def _doc(tool, unit, rater, target=None, scope=None, answers=None, dj=None, overall=None, status="draft",
         origin="human", **kw):
    d = {"schema": "szk.appraisal/v1", "tool": tool, "scope": scope, "instrument_sha256": None,
         "target": {"unit": unit, "study_id": None if unit in ("review", "manuscript") else unit,
                    "outcome": target if target in ("o1", "o2") else None, "result": None, "model": None,
                    "index_test": None, "key": target},
         "assessor": rater, "second_assessor": None, "status": status, "origin": origin, "approved_by": None,
         "approved_at": None, "answers": answers or {}, "domain_judgements": dj or [], "applicability": [],
         "overall": overall, "created": "2026-10-04T18:00:00Z", "updated": "2026-10-04T18:30:00Z"}
    d.update(kw)
    return d


def _a(value, quote=None, page=None, locator=None, rationale=None):
    a = {"value": value}
    if quote or page or locator:
        a["evidence"] = {"text": quote, "page": page, "locator": locator, "doc": None}
    if rationale:
        a["rationale"] = rationale
    return a


def _scoped_ids(inst, scope, pass_=None):
    eng = STUB.StubEngine({inst["key"]: inst})
    return [it for it in eng._scoped(inst, {"scope": scope}) if pass_ is None or it["pass"] == pass_]


def _reverse(it):
    """Fordított tétel: a motor definíciójában a 'polarity' (F1), a régi (validator) alakban a 'tags'."""
    return it.get("polarity") == "reverse" if "polarity" in it else "reverse" in (it.get("tags") or [])


def seeds(insts):
    rob2 = insts["rob2"]
    all_rob2 = _scoped_ids(rob2, "assignment")
    low = {it["key"]: _a("no" if _reverse(it) else "yes") for it in all_rob2}
    low["1.1"] = _a("yes", "A számítógéppel generált véletlen lista alapján (Methods, 2. bekezdés)", 3, "Methods")
    low["1.2"] = _a("probably_yes", "sorszámozott, lezárt borítékok", 3, "Methods")
    out = []
    dj_low = [{"domain": d["id"], "pass": None, "judgement": "low", "rationale": "", "override_reason": None,
               "decision_id": None} for d in rob2["domains"]]
    out.append(_doc("rob2", "ARONSON1948", "SzK", "o1", "assignment", copy.deepcopy(low), copy.deepcopy(dj_low),
                    {"judgement": "low", "rationale": "Minden domén alacsony.", "implied": None,
                     "override_reason": None}, status="complete"))
    kp = copy.deepcopy(low)
    kp["1.2"] = _a("no_information")
    kp_dj = copy.deepcopy(dj_low)
    kp_dj[0]["judgement"] = "some_concerns"
    out.append(_doc("rob2", "ARONSON1948", "KP", "o1", "assignment", kp, kp_dj,
                    {"judgement": "some_concerns", "rationale": "Az elrejtés nem közölt.", "implied": None,
                     "override_reason": None}, status="complete"))
    # hiányos vázlat: 3 tétel üres, D2 implikált MAGAS (2.6 = N)
    fer = copy.deepcopy(low)
    for k in ("2.7", "4.5", "5.2"):
        fer.pop(k, None)
    fer["2.6"] = _a("no", "per-protocol elemzés: a nem oltott résztvevők kizárva", 8, "Results")
    out.append(_doc("rob2", "FERGUSON1949", "SzK", "o1", "assignment", fer,
                    [{"domain": "2", "pass": None, "judgement": "high", "rationale": "", "override_reason": None,
                      "decision_id": None}], None))
    # AI-vázlat kezdőknek szóló indoklással (6. döntés)
    ai = copy.deepcopy(low)
    ai["1.1"] = _a("probably_yes", "„the children were allocated alternately”", 2, "Methods",
                   {"asks": "Valóban véletlen volt-e a csoportba sorolás?",
                    "because": "A cikk váltakozó besorolást említ, ami NEM véletlen — ezért inkább N lenne; a "
                               "vázlat PY-t javasolt, ezt ellenőrizd.",
                    "change": "Ha a módszertani rész véletlenszám-táblát említ, a válasz Y.",
                    "uncertain": "A cikk a randomizáció módját csak egy félmondatban írja le."})
    ai["1.2"] = _a("no_information", None, None, None,
                   {"asks": "Elrejtették-e a besorolást a beválogatás végéig?",
                    "because": "A cikk nem írja le, hogyan tárolták a besorolási listát.",
                    "change": "Ha a szerzők lezárt borítékot vagy központi listát említenének.",
                    "uncertain": "A cikk nem közli."})
    out.append(_doc("rob2", "ROSENTHAL1960", "ai", "o1", "assignment", ai, [], None, origin="ai_draft"))
    # felülbírált doménítélet indoklással (X017)
    hart = copy.deepcopy(low)
    hart_dj = copy.deepcopy(dj_low)
    hart_dj[0].update(judgement="some_concerns", override_reason="A kiindulási eltérés a kis elemszám miatt "
                      "véletlen lehet, de a TBC-kockázatban eltérő régiók.", decision_id=7)
    out.append(_doc("rob2", "HART1977", "SzK", "o1", "assignment", hart, hart_dj,
                    {"judgement": "some_concerns", "rationale": "D1 miatt.", "implied": None,
                     "override_reason": "A D1 felülbírálása miatt az összítélet is némi aggály.", "decision_id": 8},
                    status="complete"))
    # ROBINS-I vázlat
    ri = insts["robins-i"]
    ri_ans = {it["key"]: _a("probably_yes") for it in _scoped_ids(ri, "assignment")[:8]}
    out.append(_doc("robins-i", "FRIMODT1973", "SzK", "o1", "assignment", ri_ans, [], None))
    # PROBAST+AI: csak a fejlesztési menet kitöltve (16/34)
    pb = insts["probast-ai"]
    pb_ans = {it["key"]: _a("probably_yes" if it["id"] != "4.1" else "no", None, 4 if it["id"] == "1.1" else None)
              for it in pb["items"] if it["pass"] == "development"}
    pb_dj = [{"domain": d["id"], "pass": "development", "judgement": "high" if d["id"] == "4" else "low",
              "rationale": "", "override_reason": None, "decision_id": None} for d in pb["domains"]]
    out.append(_doc("probast-ai", "LEE2023", "SzK", "XGB-PE", "both", pb_ans, pb_dj, None, model="XGB-PE",
                    applicability=[{"domain": "1", "pass": "development", "judgement": "low", "rationale": ""}]))
    # TRIPOD+AI: két vizsgálat + a saját kézirat
    tr = insts["tripod-ai"]
    cyc = ["present", "present", "partial", "missing", "present", "not_applicable"]
    for unit, shift in (("LEE2023", 0), ("VARGA2024", 2), ("manuscript", 1)):
        ans = {}
        for i, it in enumerate(tr["items"]):
            if i % 7 == 6 and unit != "LEE2023":
                continue
            ans[it["key"]] = _a(cyc[(i + shift) % len(cyc)])
        out.append(_doc("tripod-ai", unit, "SzK", None, "both", ans, [], None))
    # AMSTAR 2: a 4. (kritikus) tételen „részben igen”, a 3. „nem”, a 13. hiányzik → a két konvenció eltér
    am = insts["amstar2"]
    am_ans = {it["key"]: _a("yes") for it in am["items"]}
    for it in am["items"]:                      # 9. és 11.: RCT / NRSI résztételek (F3) — csak RCT-t von be
        if it.get("parts"):
            am_ans[it["key"]] = {"value": None, "parts": {"RCT": "yes", "NRSI": "not_applicable"}}
    am_ans["4"] = _a("partial_yes", "4 adatbázis, regiszter nélkül", None, "Methods 2.2")
    am_ans["3"] = _a("no")
    am_ans.pop("13", None)
    out.append(_doc("amstar2", "review", "SzK", None, None, am_ans, [], None))
    return out


def rel_of(doc):
    tg = doc["target"]
    parts = [tg["unit"], doc["tool"]] + ([tg["key"]] if tg.get("key") else []) + [doc["assessor"]]
    return "04_torzitas_kockazat/appraisals/%s.json" % ".".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", action="store_true", help="(alapértelmezés) a valódi metaelemzes.api függvényeivel")
    ap.add_argument("--stub", action="store_true", help="a validator referenciái + a teszt-csonk (összevetéshez)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    plugins = (os.environ.get("MA_GUI_PLUGIN_DIRS") or DEFAULT_PLUGINS).split(os.pathsep)[0]
    if not os.path.isdir(os.path.join(plugins, "validator")):
        print("nincs validator plugin: %s — a fixture-ök változatlanok" % plugins, file=sys.stderr)
        return 0 if args.check else 2
    insts = build_instruments(plugins)
    eng = STUB.StubEngine(insts)
    SRC = "CSONK" if args.stub else "motor"
    if not args.stub:
        from metaelemzes import api
        insts = {x["key"]: api.instrument_get(x["key"]) for x in api.instruments_list()}
        eng = api
    docs = seeds(insts)
    for d in docs:
        d["instrument_sha256"] = insts[d["tool"]]["reference_sha256"]
    files = {}
    # eszközök
    routes = [{"method": "GET", "path": "/api/instruments",
               "envelope": env("szk.ma.instruments/v1", {
                   "schema": "szk.ma.instruments/v1", "instruments": eng.instruments_list(), "source": "engine",
                   "engine": {"available": True, "functions": {}, "missing": [], "engine_version": "0.3.0"},
                   "validator": {"state": "legacy", "version": "1.0.0", "mode": "bridge", "guards": ["H1", "H2", "H3", "H4"]}},
                   rid="q_ap0")}]
    for k in sorted(insts):
        routes.append({"method": "GET", "path": "/api/instruments/%s" % k,
                       "envelope": env("szk.instrument/v1", eng.instrument_get(k), rid="q_ap_" + k)})
    files["appraisal_instruments.json"] = {"description": "Értékelő eszközök (szk.instrument/v1) — a validator "
                                           "referenciáiból generálva (gen_appraisal_fixtures.py)", "routes": routes}
    # mintaértékelések (GET-borítékok; a dev-háttér ezekből indul)
    routes = []
    for d in docs:
        tg = d["target"]["key"]
        res = eng.appraisal_check(d, instrument=insts[d["tool"]])
        q = {"rater": d["assessor"]}
        if tg:
            q["target"] = tg
        view = {"schema": "szk.ma.appraisal-view/v1", "unit": d["target"]["unit"], "tool": d["tool"], "target": tg,
                "rater": d["assessor"], "path": rel_of(d), "exists": True, "doc": d, "check": res}
        routes.append({"method": "GET", "path": "/api/appraisals/%s/%s" % (d["target"]["unit"], d["tool"]),
                       "query": q, "etag": STUB.sha(d)[:16],
                       "envelope": env("szk.ma.appraisal-view/v1", view, rid="q_apd")})
    files["appraisal_docs.json"] = {"description": "Mintaértékelések (szk.appraisal/v1 + a %s ellenőrzése)" % SRC,
                                    "routes": routes}
    # konszenzus (Aronson: SzK vs KP; az AI-vázlat nincs ezen az egységen)
    a = next(d for d in docs if d["target"]["unit"] == "ARONSON1948" and d["assessor"] == "KP")
    b = next(d for d in docs if d["target"]["unit"] == "ARONSON1948" and d["assessor"] == "SzK")
    agreement = eng.appraisal_consensus(a, b, instrument=insts["rob2"])
    files["appraisal_consensus.json"] = {"description": "Konszenzus-nézet: két független emberi értékelés + κ (%s)" % SRC,
                                         "routes": [{"method": "GET", "path": "/api/appraisals/consensus/ARONSON1948/rob2",
                                                     "envelope": env("szk.ma.appraisal-consensus-view/v1",
                                                                     {"agreement": agreement}, rid="q_apc")}]}
    # összevont egyezés (F5): minden egység, ahol két független EMBERI értékelés van (a route szabálya: az első két
    # értékelő név szerint); a számok a motor appraisal_agreement_pooled-jából
    if not args.stub:
        by_unit = {}
        for d in docs:
            if d["tool"] == "rob2" and d["origin"] == "human" and d["status"] != "consensus":
                by_unit.setdefault((d["target"]["unit"], d["target"]["key"]), {})[d["assessor"]] = d
        pairs, units = [], []
        for (unit, tg), raters in sorted(by_unit.items()):
            if len(raters) >= 2:
                ra, rb = sorted(raters)[:2]
                pairs.append((raters[ra], raters[rb]))
                units.append({"unit": unit, "target": tg, "a": ra, "b": rb})
        pooled = eng.appraisal_agreement_pooled(pairs, instrument=insts["rob2"]) if pairs else None
        files["appraisal_agreement.json"] = {
            "description": "Összevont egyezés (motor: appraisal_agreement_pooled) a rob2/o1 emberi pároira",
            "routes": [{"method": "GET", "path": "/api/appraisals/agreement", "query": {"tool": "rob2", "target": "o1"},
                        "envelope": env("szk.ma.appraisal-agreement-view/v1", {
                            "schema": "szk.ma.appraisal-agreement-view/v1", "tool": "rob2", "target": "o1",
                            "units": units, "agreement": pooled}, rid="q_apa")}]}
    # forgalmi lámpa a BCG motor-fixtúra súlyaival
    plot = None
    try:
        ap_fx = _load_json(os.path.join(FIX, "analysis_plots.json"))
        for r in ap_fx["routes"]:
            dd = r["envelope"]["data"]
            if isinstance(dd, dict) and isinstance(dd.get("studies"), list) and dd["studies"] and "weight_pct" in dd["studies"][0]:
                plot = dd
                break
    except (OSError, ValueError, KeyError):
        plot = None
    studies = _load_json(os.path.join(FIX, "studies.json"))["routes"][0]["envelope"]["data"]
    rob_docs = [d for d in docs if d["tool"] == "rob2"]
    summ = eng.rob_summary(rob_docs, tool="rob2", outcome="o1", plot=plot, studies=studies)
    summ["source_run"] = {"run_id": "20261004T211200Z-a1f3c2", "stale": False}
    files["appraisal_robsummary.json"] = {"description": "Forgalmi lámpa (szk.rob-summary/v1, %s; súly: BCG-fixtúra)" % SRC,
                                          "routes": [{"method": "GET", "path": "/api/appraisals/rob-summary",
                                                      "envelope": env("szk.rob-summary/v1", summ, rid="q_aps")}]}
    # rob-szinkron előnézet a BCG-tábla fixtúrájából
    table = _load_json(os.path.join(FIX, "table.json"))["routes"][0]["envelope"]["data"]
    prop = eng.rob_sync_proposal(rob_docs, table["header"], [r["cells"] for r in table["rows"]], tool="rob2",
                                 row_uids=[r["row_uid"] for r in table["rows"]], studies=studies)
    files["appraisal_robsync.json"] = {"description": "rob-oszlop szinkron: előnézet (%s)" % SRC,
                                       "routes": [{"method": "POST", "path": "/api/appraisals/rob-sync",
                                                   "envelope": env("szk.ma.rob-sync/v1", {
                                                       "schema": "szk.ma.rob-sync/v1", "dataset": table["dataset"],
                                                       "etag": table["etag"], "column": "rob", "column_exists": False,
                                                       "proposal": prop, "applied": False}, rid="q_apy")}]}
    # beérkezett fájlok (5. döntés)
    files["appraisal_inbox.json"] = {"description": "A beérkezett mappa (04_torzitas_kockazat/appraisals/beerkezett/)",
                                     "routes": [{"method": "GET", "path": "/api/appraisals/inbox",
                                                 "envelope": env("szk.ma.appraisal-inbox/v1", {
                                                     "schema": "szk.ma.appraisal-inbox/v1",
                                                     "dir": "04_torzitas_kockazat/appraisals/beerkezett",
                                                     "files": [{"path": "04_torzitas_kockazat/appraisals/beerkezett/KP_rob2.json",
                                                                "name": "KP_rob2.json", "bytes": 4810, "sha256": "0" * 64,
                                                                "kind": "bundle", "items": 1,
                                                                "preview": [{"tool": "rob2", "unit": "FERGUSON1949",
                                                                             "assessor": "KP", "origin": "human",
                                                                             "status": "complete"}]}]}, rid="q_api")}]}
    changed = False
    for name, data in sorted(files.items()):
        text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
        path = os.path.join(FIX, name)
        old = None
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                old = fh.read()
        if old != text:
            changed = True
            if args.check:
                print("ELTÉR: %s" % name)
            else:
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                print("írva: %s (%d bájt)" % (name, len(text.encode("utf-8"))))
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
