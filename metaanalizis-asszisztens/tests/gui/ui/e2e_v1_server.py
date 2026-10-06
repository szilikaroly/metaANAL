#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Segéd a v1 elfogadási teszthez (tests/gui/ui/e2e_v1.spec.js; terv 9.3 „Elfogadás”).

    python3 tests/gui/ui/e2e_v1_server.py serve <ideiglenes mappa> <fajta>
        Ideiglenes projektet készít, és elindítja rá a VALÓDI munkapad-szervert (ma_gui.server.App, port 0, a szerver a
        saját válaszait is sémán ellenőrzi). Fajták:
          main     BCG (o1, RR, 13 vizsgálat, studies.json) és kettős kinyerés (o3: az A és a B táblát a felület
                   importálja), PDF-ek és dokumentum-jegyzék nélkül; pluginok:
                   a valódi szk-plugins (MA_GUI_PLUGIN_DIRS, ennek hiányában a szokásos helyen), előtte a composer
                   TESZT-CSONKJA (tests/gui/_adapters_stubs.py) egy kitalált PRISMA-állapottal; figure-forge-hoz a
                   MA_GUI_TEST_FF_PYTHON / FIGURE_FORGE_PYTHON interpreter (ha van).
          xrules   „mindent elrontó” projekt: a v1 X-szabályok (X001–X022) mind előjönnek (a motor maga dönt).
          hh       üres projekt a Metaheadhunterhez; a CLI a tests/reference/headhunter/cassettes/gui_e2e kazettákat
                   JÁTSSZA VISSZA (MA_HH_CASSETTE=replay — hálózat nélkül).
        Az első stdout-sor JSON: {url, base, code, proj, home, log, …}. stdin: 'code' → új indítókód; 'quit' → leállítás.
    python3 tests/gui/ui/e2e_v1_server.py plan <eszköz> <hatókör> <változat>
        Választerv a felületi kitöltéshez (kulcs → kanonikus érték) a motor eszköz-definíciójából — változat: low | high
        | mixed | partial.
    python3 tests/gui/ui/e2e_v1_server.py check <projekt> <értékelés-fájl (projekt-relatív)>
        A motor (api.appraisal_check) eredménye a MENTETT fájlra — a felület számainak összevetéséhez.
    python3 tests/gui/ui/e2e_v1_server.py bundle <projekt> <eszköz> <kimenet> <egységek vesszővel> <ítélet>
        Lezárt (complete) RoB 2 értékelések egy csomagban a beérkezett mappába (a „második kinyerő” fájljai).
    python3 tests/gui/ui/e2e_v1_server.py aidraft <projekt> <eszköz> <egység> <cél|-> <hatókör>
        AI-vázlat (origin: ai_draft) a beérkezett mappába — tételenként javaslat, idézet hellyel, egyszerű nyelvű
        indoklás (6. döntés).
    python3 tests/gui/ui/e2e_v1_server.py audit <projekt> [szakasz]
    python3 tests/gui/ui/e2e_v1_server.py json <projekt> <projekt-relatív út>
    python3 tests/gui/ui/e2e_v1_server.py figure <projekt> <futás-mappa> <fajta> <nyelv> <rétegek 0|1>
        A motor render_figure-je a futás mappájából (sha256 + a számszövegek).
    python3 tests/gui/ui/e2e_v1_server.py kettos <projekt> <kimenet>
        A motor összevetése (api.kettos_project_compare) — az eltérések száma és fajtái.

Semmilyen adat nem kitalált: a vizsgálatok a peldak/ BCG- és Normand-példái; az értékelések TESZT-értékelések (a
fájlban ``assessor`` monogrammal), amelyeket a teszt maga tölt ki."""
import base64
import hashlib
import json
import os
import shutil
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(GUI))
for p in (ROOT, GUI, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULT_PLUGINS = "/home/user/szilikaroly/szk-plugins/plugins"
CASSETTES = os.path.join(ROOT, "tests", "reference", "headhunter", "cassettes", "gui_e2e")
BCG_CSV = os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv")
NORMAND_CSV = os.path.join(ROOT, "peldak", "normand1999_folytonos.csv")
COMPOSER_PROJECT = "bcg"
# a composer-csonk PRISMA-állapota (kitalált, de ellentmondásmentes számok; csak a híd-olvasás próbája)
COMPOSER_FLOW = {
    "databases": [{"source": "PubMed", "query": "BCG AND tuberculosis", "count_total": 180}], "registers": [],
    "other_sources": [], "identified_databases": 180, "identified_registers": 0, "identified_other": 0,
    "identified_total": 180, "retrieved_total": 180, "retrieval_gap": 0, "dedup_removed": 20,
    "removed_before_screening": [], "removed_before_screening_n": 0, "screened": 160, "excluded_screening": 120,
    "sought_for_retrieval": 40, "not_retrieved": 2, "assessed_eligibility": 38, "excluded_eligibility": 25,
    "excluded_eligibility_reasons": {"nem randomizált": 15, "más kimenet": 10}, "included": 13, "undecided": 0,
}


def _read_rows(path):
    with open(path, encoding="utf-8") as fh:
        lines = [ln.rstrip("\r\n") for ln in fh if ln.strip()]
    return lines[0].split(";"), [ln.split(";") for ln in lines[1:]]


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)


def study_id(i):
    return "S%02d" % (i + 1)


def bcg_studies():
    _h, rows = _read_rows(BCG_CSV)
    return [{"study_id": study_id(i), "label": r[0], "design": "rct_parallel", "outcomes": ["o1", "o3"]}
            for i, r in enumerate(rows)]


def plugin_dirs():
    raw = os.environ.get("MA_GUI_PLUGIN_DIRS")
    if raw:
        return raw
    return DEFAULT_PLUGINS if os.path.isdir(DEFAULT_PLUGINS) else ""


def ff_python():
    for name in ("MA_GUI_TEST_FF_PYTHON", "FIGURE_FORGE_PYTHON"):
        v = os.environ.get(name)
        if v and os.path.isfile(v):
            return v
    return None


# ---------------------------------------------------------------------------- projektek
def make_main(base):
    from metaelemzes import api
    import _adapters_stubs as STUBS
    import gen_extraction_fixtures as GEN
    proj = os.path.join(base, "projekt")
    home = os.path.join(base, "home")
    os.makedirs(home)
    api.project_init(proj, "BCG — v1 elfogadási teszt")
    head, rows = _read_rows(BCG_CSV)
    with open(os.path.join(proj, "03_adatok", "o1.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write(";".join(head) + "\r\n")
        for r in rows:
            fh.write(";".join(r) + "\r\n")
    _write_json(os.path.join(proj, "03_adatok", "studies.json"), {"schema": "szk.ma.studies/v1", "studies": bcg_studies()})
    # kettős kinyerés (o3): az A kinyerő táblája már a kettos/ mappában (a B-t a felület importálja)
    a, b = GEN.o3_tables()
    os.makedirs(os.path.join(proj, "03_adatok", "kettos"), exist_ok=True)
    meta = {"schema": "szk.ma.project/v1", "title": "BCG — v1 elfogadási teszt", "data_class": "A",
            "review_type": "intervention", "appraisal_tools": ["rob2"],
            "outcomes": [{"id": "o1", "name": {"hu": "TBC-incidencia", "en": "TB incidence"}, "data": "03_adatok/o1.csv",
                          "measure": "RR", "primary_spec": "05_elemzes/specs/o1_primary.json", "critical": True},
                         {"id": "o3", "name": {"hu": "TBC-incidencia (kettős kinyerés)",
                                               "en": "TB incidence (double extraction)"},
                          "data": "03_adatok/o3.csv", "measure": "RR", "primary_spec": "05_elemzes/specs/o3_primary.json",
                          "critical": False}]}
    _write_json(os.path.join(proj, "ma-projekt.json"), meta)
    # pluginok: a composer TESZT-CSONKJA elöl, utána a valódi szk-plugins
    stubs = os.path.join(base, "stub_plugins")
    STUBS.make_plugins(stubs, composer={"mode": "legacy"})
    outdir = os.path.join(base, "composer_out")
    STUBS.composer_state(outdir, project=COMPOSER_PROJECT, flow=dict(COMPOSER_FLOW),
                         status_text="PRISMA 2020 — projekt: bcg\n  Azonosított összesen: 180\n")
    return proj, home, {"b_csv": GEN.csv_text(b), "a_csv": GEN.csv_text(a), "stub_plugins": stubs,
                        "composer_outdir": outdir, "composer_project": COMPOSER_PROJECT}


def make_hh(base):
    from metaelemzes import api
    proj = os.path.join(base, "projekt")
    home = os.path.join(base, "home")
    os.makedirs(home)
    api.project_init(proj, "BCG — Metaheadhunter elfogadási teszt")
    _write_json(os.path.join(proj, "ma-projekt.json"),
                {"schema": "szk.ma.project/v1", "title": "BCG — Metaheadhunter elfogadási teszt", "data_class": "A",
                 "outcomes": []})
    return proj, home, {"cassettes": CASSETTES}


def serve(base, kind):
    from ma_gui import caps as caps_mod
    from ma_gui import server
    extra = {}
    env = {"PATH": "/usr/bin:/bin"}
    if kind == "main":
        proj, home, extra = make_main(base)
        dirs = [extra["stub_plugins"]] + [d for d in plugin_dirs().split(os.pathsep) if d]
        env["MA_GUI_PLUGIN_DIRS"] = os.pathsep.join(dirs)
        ff = ff_python()
        if ff:
            env["FIGURE_FORGE_PYTHON"] = ff
        extra.update(plugins=plugin_dirs(), ff_python=ff)
    elif kind == "xrules":
        import e2e_v1_xrules as XR
        proj, home, extra = XR.make_project(base)
    elif kind == "hh":
        proj, home, extra = make_hh(base)
        mode = "record" if os.environ.get("E2E_V1_HH_RECORD") == "1" else "replay"
        if mode == "record":                # csak fejlesztői rögzítés: a régi kazetták helyett újak (élő hálózat)
            shutil.rmtree(CASSETTES, ignore_errors=True)
            os.makedirs(CASSETTES)
        os.environ["MA_HH_CASSETTE"] = mode
        os.environ["MA_HH_CASSETTE_FILE"] = CASSETTES
        extra["cassette_mode"] = mode
        for k in ("MA_SCOPUS_APIKEY", "MA_SCOPUS_INSTTOKEN", "MA_OPENALEX_APIKEY", "MA_NCBI_APIKEY"):
            os.environ.pop(k, None)
    else:
        raise SystemExit("ismeretlen fajta: %s" % kind)
    app = server.App(proj, kb_db=os.path.join(base, "kb.sqlite"),
                     caps=caps_mod.Caps(runtime_dir=os.path.join(base, "caps"), env=env, home=home),
                     privacy_home=home, privacy_env={}, selftest=False, kb_build=True, caps_refresh=False,
                     idle_hours=0, watch_interval=0.3, validate_responses=True,
                     log_stream=open(os.path.join(base, "server.log"), "w", encoding="utf-8"))
    app.start(port=0)
    th = threading.Thread(target=app.serve_forever, name="e2e-v1-serve", daemon=True)
    th.start()
    url = app.launch_url()
    info = {"url": url, "base": url.split("/#")[0], "code": url.split("#launch=")[1], "proj": proj, "home": home,
            "log": os.path.join(base, "server.log"), "kind": kind}
    info.update(extra)
    print(json.dumps(info, ensure_ascii=False), flush=True)
    try:
        for line in sys.stdin:
            cmd = line.strip()
            if cmd == "code":
                print(json.dumps({"code": app.new_launch_code()}), flush=True)
            elif cmd == "quit":
                break
    finally:
        app.shutdown()
        th.join(15)


# ---------------------------------------------------------------------------- értékelés-segédek
def items_in_scope(inst, scope):
    """A felület (MA.appr.itemsFor) szabálya: a hatókörbe eső tételek (menettel minősített kulccsal)."""
    has_passes = bool(inst.get("passes"))
    out = []
    for it in inst.get("items") or []:
        if has_passes:
            if scope in ("development", "evaluation") and it.get("pass") != scope:
                continue
        elif it.get("applies_to"):
            if scope == "development" and "D" not in str(it["applies_to"]):
                continue
            if scope == "evaluation" and "E" not in str(it["applies_to"]):
                continue
        else:
            sc = it.get("scope") if isinstance(it.get("scope"), list) else ["all"]
            if scope and "all" not in sc and scope not in sc:
                continue
        out.append(it)
    return out


def _allowed(inst, it):
    """A motor szabálya (F2): a tétel saját 'answers'-e, különben az eszköz 'default_answers'-e, különben minden."""
    if isinstance(it.get("answers"), list) and it["answers"]:
        return list(it["answers"])
    if isinstance(inst.get("default_answers"), list) and inst["default_answers"]:
        return list(inst["default_answers"])
    return [a["value"] if isinstance(a, dict) else a for a in inst.get("answers") or inst.get("status_vocab") or []]


# résztételes tétel (AMSTAR 2 9. és 11.: RCT / NRSI — F3): a terv kulcsa „<tétel>#<rész>”; a teszt-áttekintés csak
# RCT-ket von be (NRSI: nem alkalmazható)
PART_PLAN = {"RCT": "yes", "NRSI": "not_applicable"}


def _good(inst, it, vals):
    """A „rendben” válasz: normál tételnél igen, fordítottnál nem; irányító kérdésnél nem (pl. a résztvevők nem
    tudták a besorolást) — a motor maga dönti el, mit implikál."""
    pol = it.get("polarity")
    for cand in (("no",) if pol in ("reverse", "router") else ()) + ("yes", "present", "not_serious", "undetected",
                                                                     "high", "no"):
        if cand in vals:
            return cand
    return vals[0]


def plan(tool, scope, variant):
    from metaelemzes import api
    inst = api.instrument_get(tool)
    out = {}
    its = items_in_scope(inst, scope)
    for n, it in enumerate(its):
        vals = _allowed(inst, it)
        key = it.get("key") or ((it["pass"] + "/" + it["id"]) if it.get("pass") else it["id"])
        v = _good(inst, it, vals)
        if variant == "high" and n < 2 and it.get("polarity") in (None, "normal"):
            v = "no" if "no" in vals else v               # az első két (normál) tétel „nem” → magas kockázat
        elif variant == "mixed" and tool == "tripod-ai":
            v = ("present", "partial", "missing")[n % 3] if n % 3 < len(vals) else v
        elif variant == "mixed" and tool == "amstar2" and it["id"] in ("2", "7"):
            v = "partial_yes" if "partial_yes" in vals else v
        if it.get("parts"):
            for part in it["parts"]:
                out["%s#%s" % (key, part["id"])] = PART_PLAN.get(part["id"], part["answers"][0])
            continue
        out[key] = v
    if variant == "partial":
        keys = sorted(out)
        for k in keys[len(keys) // 2:]:
            del out[k]
    return {"tool": tool, "scope": scope, "variant": variant, "answers": out, "n": len(its)}


def _plan_answers(plan_answers):
    """A terv → szk.appraisal/v1 answers (a „tétel#rész” kulcsok a tétel parts-objektumába)."""
    out = {}
    for k, v in plan_answers.items():
        if "#" in k:
            key, part = k.split("#", 1)
            out.setdefault(key, {"value": None, "parts": {}})["parts"][part] = v
        else:
            out[k] = {"value": v}
    return out


def check_file(proj, rel):
    from metaelemzes import api
    with open(os.path.join(proj, rel), encoding="utf-8") as fh:
        doc = json.load(fh)
    res = api.appraisal_check(doc, project_dir=proj)
    keep = ("complete", "answered", "expected", "completeness_text", "per_pass", "missing", "overall", "amstar2",
            "nos", "tripod", "grade")
    out = {k: res.get(k) for k in keep}
    out["domains"] = [{"domain": d.get("domain"), "pass": d.get("pass"), "implied": d.get("implied"),
                       "algorithm": d.get("algorithm")} for d in res.get("domains") or []]
    out["doc"] = {k: doc.get(k) for k in ("tool", "scope", "assessor", "status", "origin", "approved_by", "overall",
                                           "domain_judgements", "overall_passes", "applicability")}
    return out


def _complete_doc(tool, unit, outcome, assessor, judgement, scope):
    from metaelemzes import api
    inst = api.instrument_get(tool)
    p = plan(tool, scope, "high" if judgement == "high" else "low")
    doc = {"schema": "szk.appraisal/v1", "tool": tool, "scope": scope, "instrument_sha256": None,
           "target": {"unit": unit, "study_id": unit, "outcome": outcome, "result": None, "model": None,
                      "index_test": None, "key": outcome},
           "assessor": assessor, "second_assessor": None, "status": "complete", "origin": "human",
           "approved_by": None, "approved_at": None,
           "answers": _plan_answers(p["answers"]),
           "domain_judgements": [], "applicability": [], "overall": None, "created": None, "updated": None}
    res = api.appraisal_check(doc)
    imp = (res.get("overall") or {}).get("implied")
    doc["overall"] = {"judgement": imp, "rationale": "a motor implikált ítéletével egyezik (teszt)",
                      "override_reason": None, "decision_id": None}
    doc["instrument_sha256"] = inst.get("reference_sha256")
    return doc, imp


def bundle(proj, tool, outcome, units, judgement):
    """Egy második kinyerő lezárt értékelései egy csomag-fájlban a beérkezett mappába."""
    docs = []
    for u in units:
        doc, _imp = _complete_doc(tool, u, outcome, "KP", judgement, "assignment")
        docs.append(doc)
    path = os.path.join(proj, "04_torzitas_kockazat", "appraisals", "beerkezett", "kp_%s_%s_%s.json" % (tool, outcome, judgement))
    _write_json(path, {"schema": "szk.appraisal-bundle/v1", "items": docs})
    return {"path": os.path.relpath(path, proj).replace(os.sep, "/"), "n": len(docs),
            "implied": [_complete_doc(tool, u, outcome, "KP", judgement, "assignment")[1] for u in units[:1]]}


def aidraft(proj, tool, unit, target, scope):
    """AI-vázlat a 6. döntés szerint: tételenként érték + idézet hellyel + egyszerű nyelvű indoklás."""
    from metaelemzes import api
    inst = api.instrument_get(tool)
    p = plan(tool, scope, "low")
    answers = _plan_answers(p["answers"])
    for k, a in answers.items():
        a.update({"evidence": {"text": "(teszt-idézet a %s tételhez)" % k, "page": 3, "locator": "Methods",
                               "doc": None},
                  "rationale": {"asks": "Mit kérdez a %s tétel — egyszerű nyelven (teszt)." % k,
                                "because": "A közlemény Methods része alapján (teszt).",
                                "change": "Ha a közlemény mást írna (teszt)."}})
    doc = {"schema": "szk.appraisal/v1", "tool": tool, "scope": scope,
           "instrument_sha256": inst.get("reference_sha256"),
           "target": {"unit": unit, "study_id": unit if unit not in ("review", "manuscript") else None,
                      "outcome": None, "result": None, "model": target if inst.get("unit") == "model" else None,
                      "index_test": None, "key": target},
           "assessor": "ai", "second_assessor": None, "status": "draft", "origin": "ai_draft",
           "approved_by": None, "approved_at": None, "answers": answers, "domain_judgements": [], "applicability": [],
           "overall": None, "created": None, "updated": None}
    path = os.path.join(proj, "04_torzitas_kockazat", "appraisals", "beerkezett",
                        "ai_%s_%s.json" % (tool, unit))
    _write_json(path, doc)
    return {"path": os.path.relpath(path, proj).replace(os.sep, "/"), "n": len(answers),
            "bytes": os.path.getsize(path)}


def figure(proj, run_dir, kind, lang, annotate):
    from metaelemzes import api
    d = os.path.join(proj, run_dir)
    with open(os.path.join(d, "plot_data.json"), encoding="utf-8") as fh:
        plot = json.load(fh)
    try:
        res = api.render_figure(plot, kind, lang=lang, annotate=bool(int(annotate)), run_dir=d, project_root=proj)
    except Exception as e:      # noqa: BLE001 — a teszt az okot is látja
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    svg = res.get("svg") or ""
    own = None
    p = os.path.join(d, kind + ".svg")
    if os.path.isfile(p):
        with open(p, "rb") as fh:
            own = hashlib.sha256(fh.read()).hexdigest()
    return {"ok": True, "sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(), "own_sha256": own,
            "len": len(svg), "lang": res.get("lang"), "display": [s["display_text"][lang] for s in plot["summaries"]
                                                               if s.get("primary")]}


def kettos(proj, outcome):
    from metaelemzes import api
    res = api.kettos_project_compare(proj, outcome)
    items = res.get("disagreements") or res.get("items") or []
    kinds = {}
    for it in items:
        k = it.get("status") or it.get("kind")
        kinds[k] = kinds.get(k, 0) + 1
    return {"n": len(items), "kinds": kinds, "summary": res.get("summary")}


_EMAIL_RE = None


def scrub_cassettes(directory):
    """A rögzített GUI-kazetták utókezelése (idempotens): a rögzítő ``slim`` szűrője (net.SLIM_JSON_KEYS,
    net._SLIM_XML: irodalomjegyzék-lista, affiliációk, azonosító- és támogatás-listák — a kazetta-méretkorlát,
    tests/test_headhunter_net.py) és minden e-mail-szerű szöveg kivágása (személyes adat ne kerüljön a tesztadatba).
    Az absztraktot és a JATS-törzset a rögzítő már kivágta. → {fájl: kivágott elemek száma}"""
    import re
    from metaelemzes.headhunter import net
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    note = " — GUI-elfogadási teszt (e2e_v1.spec.js 7. lépés), rögzítve a felületről; slim + e-mail-címek kivágva."
    out = {}
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(directory, fn)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        n = [0]

        def mails(o):
            if isinstance(o, dict):
                return {k: mails(v) for k, v in o.items()}
            if isinstance(o, list):
                return [mails(v) for v in o]
            if isinstance(o, str):
                t, k = email.subn("«email»", o)
                n[0] += k
                return t
            return o
        for it in doc.get("interactions") or []:
            resp = it.get("response") or {}
            labels = list(resp.get("redactions") or [])
            if isinstance(resp.get("body_text"), str):
                t, more = net.slim_xml_text(resp["body_text"])
                labels += more
                n[0] += len(more)
                t, k = email.subn("«email»", t)
                n[0] += k
                if k:
                    labels.append("scrub:email")
                resp["body_text"] = t
            if "body_json" in resp:
                before = n[0]
                obj, more = net.slim_json_obj(resp["body_json"])
                labels += more
                n[0] += len(more)
                resp["body_json"] = mails(obj)
                if n[0] > before + len(more):
                    labels.append("scrub:email")
            if labels:
                resp["redactions"] = sorted(set(labels))
        if note not in (doc.get("notes") or ""):
            doc["notes"] = (doc.get("notes") or "") + note
        # a kazetta-méretkorlát (250 000 bájt / fájl): a túl nagy kazetta interakciónként több részre bomlik
        # (a lejátszó a mappa minden kazettáját betölti; a kérés-kulcs a fájltól független)
        parts = _split_interactions(doc, net)
        for i, part in enumerate(parts):
            ppath = path if i == 0 else path[:-len(".json")] + "-%d.json" % (i + 1)
            with open(ppath, "w", encoding="utf-8") as fh:
                fh.write(net.dump_json(part))
        out[fn] = n[0]
    return out


CASSETTE_MAX = 240000


def _split_interactions(doc, net):
    """→ [doc, …]: egy-egy rész a ``CASSETTE_MAX`` alatt (ha egy interakció maga nagyobb, külön részbe kerül)."""
    if len(net.dump_json(doc).encode("utf-8")) < CASSETTE_MAX:
        return [doc]
    head = dict((k, v) for k, v in doc.items() if k != "interactions")
    parts, cur = [], []
    for it in doc.get("interactions") or []:
        trial = dict(head, interactions=cur + [it])
        if cur and len(net.dump_json(trial).encode("utf-8")) >= CASSETTE_MAX:
            parts.append(cur)
            cur = [it]
        else:
            cur.append(it)
    if cur:
        parts.append(cur)
    out = []
    for i, its in enumerate(parts):
        out.append(dict(head, interactions=its) if i == 0 else
                   dict(head, name="%s-%d" % (doc.get("name"), i + 1), interactions=its))
    return out


def main(argv):
    if len(argv) >= 3 and argv[0] == "serve":
        serve(argv[1], argv[2])
        return 0
    cmd = argv[0] if argv else ""
    out = None
    if cmd == "plan" and len(argv) >= 4:
        out = plan(argv[1], argv[2], argv[3])
    elif cmd == "check" and len(argv) >= 3:
        out = check_file(argv[1], argv[2])
    elif cmd == "bundle" and len(argv) >= 6:
        out = bundle(argv[1], argv[2], argv[3], argv[4].split(","), argv[5])
    elif cmd == "aidraft" and len(argv) >= 6:
        out = aidraft(argv[1], argv[2], argv[3], None if argv[4] == "-" else argv[4], argv[5])
    elif cmd == "audit" and len(argv) >= 2:
        from metaelemzes import api
        out = api.project_audit(argv[1], stage=argv[2] if len(argv) >= 3 else None)
    elif cmd == "json" and len(argv) >= 3:
        with open(os.path.join(argv[1], argv[2]), encoding="utf-8") as fh:
            out = json.load(fh)
    elif cmd == "figure" and len(argv) >= 6:
        out = figure(argv[1], argv[2], argv[3], argv[4], argv[5])
    elif cmd == "kettos" and len(argv) >= 3:
        out = kettos(argv[1], argv[2])
    elif cmd == "scrub" and len(argv) >= 2:
        out = scrub_cassettes(argv[1])
    elif cmd == "b64" and len(argv) >= 2:
        with open(argv[1], "rb") as fh:
            out = {"b64": base64.b64encode(fh.read()).decode("ascii")}
    if out is None:
        print(__doc__, file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
