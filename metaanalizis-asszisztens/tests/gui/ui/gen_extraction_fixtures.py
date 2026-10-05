#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A kettős kinyerés (3.5.5) és a v1-ábrák (kumulatív forest, buborék) fixture-jei — a VALÓDI munkapad-szerverből
és a VALÓDI motorból, hogy ne sodródhassanak el.

    python3 tests/gui/ui/gen_extraction_fixtures.py           # újraírja a fixture-öket
    python3 tests/gui/ui/gen_extraction_fixtures.py --check   # eltérésnél kilépési kód 1 (teszt / CI)

Mit és honnan (ma_gui/web/fixtures/):

- ``extraction_dual.json`` — a ``ma_gui/routes/extraction_dual.py`` végpontjainak borítékai egy ideiglenes projekten
  (BCG → o1, Normand → o2, BCG-másolat → o3), a munkapad VALÓDI szerverével (port 0, sémaellenőrzéssel). Az
  összevetés: a homlokzat ``metaelemzes.api.compare``-je, ha már be van kötve; különben a motor v1 modulja
  (``metaelemzes/kettos.py``: compare, consensus_table, agreement_report) a homlokzat helyére illesztve; végső
  tartalékként a TESZT-CSONK (tests/gui/_extraction_engine_stub.py). Tartalom:
  GET /api/kettos (o3-nál csak az A tábla van meg, a B a beérkezett mappában), POST /api/compare o1/o2/o3-ra,
  a motor nélküli 424-es boríték, POST /api/kettos/export (A, B, sablon). A dinamikus háttér
  (src/dev/extraction_dual_backend.js) ezekből indul (döntések, visszavonás, CSV-írás, import állapottal).
- ``extraction_plots.json`` — GET /api/runs/<id>/plot: a motor ``szk.ma.plot/v2``-je (``api.analyze``, BCG, RR,
  kumulatív: év, moderátor: szélesség) a ``cumulative`` és a ``bubble`` blokkal — mindkettő a MOTORÉ (E4c; ha a motor
  még nem írná a buborékot, a generátor tartaléka a motor meta-regressziós együtthatóiból és SE-iből építi). Kategóriás
  változat (allokáció): a motor ezt még nem írja — a pontdiagram a motor alcsoport-összesítőivel
  (``summaries[kind=subgroup]``) a generátorban készül, a 4.6 szerződés szerint.

Normalizálva (dokumentáltan): a borítékok meta-mezői (elapsed_ms = 1, request_id, project_rev) — minden más (ETag =
tartalom-hash, row_uid = a CSV row_uid oszlopa) determinisztikus."""
import argparse
import base64
import collections
import contextlib
import copy
import io
import json
import math
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE.parent))

from metaelemzes import api  # noqa: E402
from metaelemzes import distributions as dist  # noqa: E402

FX = ROOT / "ma_gui" / "web" / "fixtures"
DUAL = FX / "extraction_dual.json"
PLOTS = FX / "extraction_plots.json"
XSS = "<img src=x onerror=alert(1)>"
BUBBLE_RUN = "20261005T120000Z-b0b1e1"
BUBBLE_CAT_RUN = "20261005T120100Z-b0b1e2"

# ---------------------------------------------------------------------------- A/B táblák
BCG = [l.split(";") for l in (ROOT / "peldak" / "bcg_oltas_RR.csv").read_text(encoding="utf-8").splitlines()]
ROB = ["low", "some", "low", "high", "some", "some", "low", "low", "some", "high", "low", "some", "low"]


def o1_tables():
    head = ["row_uid", "vizsgálat", "esemény1", "n1", "esemény2", "n2", "év", "rob"]
    a, b = [head], [head]
    for i, r in enumerate(BCG[1:]):
        study, e1, n1, e2, n2, _lat, year, _alloc = r
        ra = ["rdxa%02d" % (i + 1), study, e1, n1, e2, n2, year, ROB[i]]
        rb = ["rdxb%02d" % (i + 1), study, e1, n1, e2, n2, year, ROB[i]]
        if study.startswith("Ferguson"):
            rb[2] = "60"                                  # 10× (tizedesjel / mértékegység?)
        elif study == "Rosenthal et al 1960":
            rb[2], rb[3], rb[4], rb[5] = e2, n2, e1, n1   # felcserélt karok
        elif study.startswith("Aronson"):
            rb[3] = "123.0"                               # csak írásmód
        elif study.startswith("Hart"):
            rb[7] = "some"                                # kategória (rob)
        elif study == "Comstock et al 1974":
            rb[4] = ""                                    # hiányzik B-ben
        elif study.startswith("Vandiviere"):
            rb[2] = "NR"                                  # nem szám
        elif study.startswith("Coetzee"):
            rb[2] = "27"                                  # eltérő érték
        a.append(ra)
        b.append(rb)
    a.append(["rdxa14", XSS + " 2001", "1", "10", "2", "20", "2001", "low"])     # csak A (HTML-címke: szövegként!)
    b.append(["rdxb14", "Tóth 2021", "7", "70", "8", "80", "2021", "low"])       # csak B
    return a, b


def o2_tables():
    norm = [l.split(";") for l in (ROOT / "peldak" / "normand1999_folytonos.csv").read_text(encoding="utf-8").splitlines()]
    head = ["row_uid"] + norm[0]
    a, b = [head], [head]
    for i, r in enumerate(norm[1:]):
        ra = ["rdya%02d" % (i + 1)] + r
        rb = ["rdyb%02d" % (i + 1)] + list(r)
        if r[0] == "Edinburgh":
            rb[3] = "3.78"                                # SD helyett SE (47/√155)
        elif r[0] == "Orpington-Mild":
            rb[5] = "92"                                  # m2 eltér
        a.append(ra)
        b.append(rb)
    return a, b


def o3_tables():
    a, b = o1_tables()
    a = [r for r in a if not r[1].startswith("<img")]
    b = [r for r in b if not r[1].startswith("Tóth")]
    a = [[c.replace("rdxa", "rdza") for c in r] for r in a]
    b = [[c.replace("rdxb", "rdzb") for c in r] for r in b]
    for r in b:
        if r[1].startswith("Ferguson"):
            r[2] = "6"                                    # o3-ban kevesebb eltérés
    return a, b


def csv_text(rows, sep=";"):
    return "".join(sep.join(r) + "\n" for r in rows)


def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------- borítékok
class Recorder(object):
    def __init__(self):
        self.n = 0

    def norm(self, env, etag=None):
        self.n += 1
        env = copy.deepcopy(env)
        env.pop("_etag", None)
        if env.get("ok"):
            env["meta"] = collections.OrderedDict((("engine", env["meta"].get("engine")), ("elapsed_ms", 1),
                                                   ("project_rev", 70 + self.n), ("request_id", "q_dx%03d" % self.n)))
        return env


ENGINE_NOTE = {
    "facade": "a VALÓDI motor (metaelemzes.api.compare)",
    "module": "a VALÓDI motor kettős kinyerés-modulja (metaelemzes/kettos.py: compare, consensus_table, agreement_report; "
              "a homlokzatba kötése az integrátoré)",
    "stub": "a TESZT-CSONK (tests/gui/_extraction_engine_stub.py; a motor v1 compare-je még készül)"}


def engine_source(C):
    """'facade' (metaelemzes.api.compare megvan) | 'module' (metaelemzes/kettos.py megvan) | 'stub'."""
    if C.engine_fn("compare") is not None:
        return "facade"
    try:
        from metaelemzes import kettos
    except ImportError:
        return "stub"
    return "module" if callable(getattr(kettos, "compare", None)) else "stub"


def engine_patches(st, mock, C, STUB):
    """A motor-függvények a homlokzatra: a valódi modul, ha a homlokzat még nem köti be; különben a csonk."""
    src = engine_source(C)
    if src == "module":
        from metaelemzes import kettos
        for n in ("compare", "consensus_table", "agreement_report"):
            if callable(getattr(kettos, n, None)):
                st.enter_context(mock.patch.object(api, n, getattr(kettos, n), create=True))
    elif src == "stub":
        for p in STUB.patch(STUB.StubEngine()):
            st.enter_context(p)
    return src


def gen_dual():
    import test_routes_harness as H                    # noqa: E402 — a futó szerver (tests/gui)
    import _extraction_engine_stub as STUB             # noqa: E402
    from unittest import mock
    from ma_gui.routes import extraction_dual_common as C

    rec = Recorder()
    routes = []
    tmp = H.tmpdir("ma_fx_dual_")
    try:
        proj, home = H.make_project(tmp)
        shutil.copy(str(ROOT / "peldak" / "bcg_oltas_RR.csv"), os.path.join(proj, "03_adatok", "o3.csv"))
        meta_p = os.path.join(proj, "ma-projekt.json")
        with open(meta_p, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta["outcomes"].append({"id": "o3", "name": {"hu": "Súlyos TBC", "en": "Severe TB"}, "data": "03_adatok/o3.csv",
                                 "measure": "RR"})
        with open(meta_p, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False)
        srv = H.Srv(proj, home, tmp, name="fx")
        try:
            with contextlib.ExitStack() as st:
                engine_patches(st, mock, C, STUB)
                tables = {"o1": o1_tables(), "o2": o2_tables(), "o3": o3_tables()}
                seps = {"o1": ";", "o2": ",", "o3": ";"}
                raters = ("SzK", "KP")
                for oid in ("o1", "o2"):
                    for side, rows, rater in (("A", tables[oid][0], raters[0]), ("B", tables[oid][1], raters[1])):
                        srv.ok("POST", "/api/kettos/import", {"outcome": oid, "side": side, "rater": rater,
                                                              "content_b64": b64(csv_text(rows, seps[oid]))})
                srv.ok("POST", "/api/kettos/import", {"outcome": "o3", "side": "A", "rater": "SzK",
                                                      "content_b64": b64(csv_text(tables["o3"][0]))})
                inbox = os.path.join(proj, "03_adatok", "kettos", "beerkezett")
                os.makedirs(inbox, exist_ok=True)
                with open(os.path.join(inbox, "o3.B.csv"), "w", encoding="utf-8", newline="") as fh:
                    fh.write(csv_text(tables["o3"][1]))
                lst = srv.ok("GET", "/api/kettos")
                routes.append({"method": "GET", "path": "/api/kettos", "envelope": rec.norm(lst)})
                for oid, side, tpl in (("o1", "A", False), ("o1", "B", False), ("o1", "A", True), ("o2", "A", False),
                                       ("o2", "B", False), ("o3", "A", False), ("o3", "A", True)):
                    ex = srv.ok("POST", "/api/kettos/export", {"outcome": oid, "side": side, "template": tpl})
                    routes.append({"method": "POST", "path": "/api/kettos/export",
                                   "body": {"outcome": oid, "side": side, "template": tpl}, "envelope": rec.norm(ex)})
                for oid in ("o1", "o2"):
                    cmp_ = srv.ok("POST", "/api/compare", {"outcome": oid, "tables": True})
                    routes.append({"method": "POST", "path": "/api/compare", "body": {"outcome": oid},
                                   "etag": cmp_["_etag"], "envelope": rec.norm(cmp_)})
                # o3: a B a beérkezett mappából — a háttér ezt adja az import után
                srv.ok("POST", "/api/kettos/import", {"outcome": "o3", "side": "B", "rater": "KP",
                                                      "path": "03_adatok/kettos/beerkezett/o3.B.csv"})
                cmp3 = srv.ok("POST", "/api/compare", {"outcome": "o3", "tables": True})
                routes.append({"method": "POST", "path": "/api/compare", "body": {"outcome": "o3"},
                               "etag": cmp3["_etag"], "envelope": rec.norm(cmp3)})
                # motor nélkül: 424 (a szerver magyar üzenetével)
                with contextlib.ExitStack() as st2:
                    for n in set(n for k in C.ENGINE for n in (C.ENGINE[k][0],) + tuple(C.ENGINE[k][1])):
                        if hasattr(api, n):
                            st2.enter_context(mock.patch.object(api, n, None))
                    status, _h, env = srv.call("POST", "/api/compare", {"outcome": "o1"})
                    assert status == 424, env
                    routes.append({"method": "POST", "path": "/api/compare", "body": {"outcome": "o1", "_engine": "missing"},
                                   "status": 424, "envelope": env})
        finally:
            srv.stop()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    engine_note = ENGINE_NOTE[engine_source(C)]
    return {"description": "Kettős kinyerés (3.5.5): a munkapad VALÓDI szerverének borítékai (tests/gui/ui/"
                           "gen_extraction_fixtures.py) egy ideiglenes projekten — o1: BCG, A/B eltérésekkel (10×, felcserélt "
                           "karok, csak írásmód, kategória, hiányzó, nem szám, csak A / csak B sor, HTML-címke); o2: Normand "
                           "(SE/SD csere); o3: csak az A tábla, a B a beérkezett mappában. Az összevetést %s adja. A "
                           "döntéseket, a visszavonást, a CSV-írást és az importot a src/dev/extraction_dual_backend.js "
                           "állapottartóan szimulálja (nem a szerver)." % engine_note,
            "routes": routes}


# ---------------------------------------------------------------------------- ábrák
def _spec(options):
    return {"schema": "szk.ma.analysis-spec/v1", "name": "o1_bubble", "outcome": "o1", "purpose": "primary",
            "prespecified": False, "protocol_ref": None, "parent": None, "data": {"path": "03_adatok/o1.csv"},
            "options": options, "filters": {"include": [], "exclude": []}, "kb_refs": []}


def _analyze(root, options):
    with contextlib.redirect_stdout(io.StringIO()):
        return api.analyze(_spec(options), mode="explore", project_root=root)


def _num(v, nd=2):
    return ("%%.%df" % nd) % v


def _i18n(hu, en=None):
    return {"hu": hu, "en": en if en is not None else hu.replace("-", "−")}


def _inv2(m):
    a, b, c, d = m[0][0], m[0][1], m[1][0], m[1][1]
    det = a * d - b * c
    return [[d / det, -b / det], [-c / det, a / det]]


def _yaxis(lo, hi, measure):
    pad = (hi - lo) * 0.06
    dom = [lo - pad, hi + pad]
    ticks = []
    for v in (0.05, 0.1, 0.2, 0.5, 1, 2, 5):
        at = math.log(v)
        if dom[0] <= at <= dom[1]:
            txt = ("%g" % v)
            ticks.append({"at": at, "text": txt, "text_i18n": {"hu": txt, "en": txt}})
    return {"domain": dom, "ticks": ticks, "title": {"hu": "%s (log-skálán ábrázolva)" % measure, "en": "%s (log scale)" % measure},
            "refs": [{"at": 0.0, "text": {"hu": "nincs hatás (RR = 1)", "en": "no effect (RR = 1)"}}]}


def _bubble_fallback(cont, lat):
    """A 4.6 szerinti 'bubble' blokk, ha a motor még nem írja (E4c előtt): a pontok a motor plot-dokumentumából, az
    egyenes a motor meta-regressziós együtthatóiból, a sáv a motor SE-ihez skálázott (X'WX)⁻¹-ből (t-kvantilis)."""
    plot = cont["plot"]
    mr = cont["results"]["metaregression"]
    b0, b1 = mr["coefficients"][0]["estimate"], mr["coefficients"][1]["estimate"]
    se0, se1 = mr["coefficients"][0]["se"], mr["coefficients"][1]["se"]
    tau2 = mr["tau2"]
    studies = plot["studies"]
    xs = [lat[s["row_index"]] for s in studies]
    w = [1.0 / (s["se"] ** 2 + tau2) for s in studies]
    xtwx = [[sum(w), sum(wi * x for wi, x in zip(w, xs))], [sum(wi * x for wi, x in zip(w, xs)), sum(wi * x * x for wi, x in zip(w, xs))]]
    inv = _inv2(xtwx)
    s2 = (se1 ** 2) / inv[1][1]
    assert abs(inv[0][0] * s2 - se0 ** 2) / se0 ** 2 < 1e-6, "a kovariancia-rekonstrukció nem egyezik a motor SE-ivel"
    vb = [[v * s2 for v in row] for row in inv]
    crit = dist.t_ppf(0.975, mr["k"] - mr["p"]) if mr["test"] == "knha" else dist.norm_ppf(0.975)
    lo_x, hi_x = 10.0, 60.0
    grid = [lo_x + (hi_x - lo_x) * i / 25.0 for i in range(26)]
    line, band = [], []
    for x in grid:
        pred = b0 + b1 * x
        se = math.sqrt(vb[0][0] + 2 * x * vb[0][1] + x * x * vb[1][1])
        line.append([x, pred])
        band.append([x, pred - crit * se, pred + crit * se])
    ys = [s["y"] for s in studies] + [p[1] for p in band] + [p[2] for p in band]
    points = [{"row_uid": s["row_uid"], "label": s["label"], "x": x, "y": s["y"], "weight_pct": s["weight_pct"],
               "weight_text": s.get("weight_text"), "x_text": _i18n("%g" % x), "display_text": s["display_text"],
               "flags": s.get("flags") or {}} for s, x in zip(studies, xs)]
    return {"moderator": {"name": "szélesség", "label": {"hu": "földrajzi szélesség (°)", "en": "latitude (°)"}, "type": "continuous"},
            "x_axis": {"domain": [lo_x, hi_x], "ticks": [{"at": float(v), "text": str(v), "text_i18n": {"hu": str(v), "en": str(v)}}
                                                         for v in (10, 20, 30, 40, 50, 60)],
                       "title": {"hu": "földrajzi szélesség (°)", "en": "latitude (°)"}},
            "y_axis": _yaxis(min(ys), max(ys), "RR"), "points": points, "line": line, "band": band,
            "coef_text": _i18n("meredekség (log RR / fok): %s, p = %s" % (_num(b1, 4), _num(mr["coefficients"][1]["p"], 3))),
            "note": {"hu": "FIXTURE: a sáv a generátorban készült (a motor E4c-je még nem írja).",
                     "en": "FIXTURE: band built in the generator (engine E4c does not write it yet)."}}


def gen_plots():
    tmp = Path(os.path.realpath(__import__("tempfile").mkdtemp(prefix="ma_fx_plots_")))
    try:
        (tmp / "03_adatok").mkdir()
        shutil.copy(str(ROOT / "peldak" / "bcg_oltas_RR.csv"), str(tmp / "03_adatok" / "o1.csv"))
        cont = _analyze(str(tmp), {"measure": "RR", "cumulative": "év", "moderators": ["szélesség"]})
        cat = _analyze(str(tmp), {"measure": "RR", "subgroup": "allokáció"})
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    rows = BCG[1:]
    lat = [float(r[5]) for r in rows]
    alloc = [r[7] for r in rows]

    # --- folytonos moderátor: a motor E4c 'bubble' blokkja, ha már megvan; különben a generátor építi (lent)
    plot = cont["plot"]
    if not plot.get("bubble"):
        plot["bubble"] = _bubble_fallback(cont, lat)
    plot["meta"]["run_id"] = BUBBLE_RUN

    # --- kategóriás moderátor (allokáció): pontdiagram; a csoport-összesítők a motor alcsoport-összesítései
    pc = cat["plot"]
    levels = []
    for a in alloc:
        if a not in levels:
            levels.append(a)
    subs = [s for s in pc["summaries"] if s.get("kind") == "subgroup"]
    groups = []
    for i, lv in enumerate(levels):
        s = [x for x in subs if (x.get("label") or {}).get("en", "").lower().startswith(lv) or x.get("id", "").endswith(lv)]
        s = s[0] if s else None
        groups.append({"id": lv, "label": {"hu": lv, "en": lv}, "x": float(i), "estimate": s["estimate"] if s else None,
                       "ci_lower": s["ci_lower"] if s else None, "ci_upper": s["ci_upper"] if s else None,
                       "display_text": s["display_text"] if s else None, "k": s.get("k") if s else None})
    cpoints = []
    for s in pc["studies"]:
        lv = alloc[s["row_index"]]
        cpoints.append({"row_uid": s["row_uid"], "label": s["label"], "x": float(levels.index(lv)), "y": s["y"],
                        "weight_pct": s["weight_pct"], "weight_text": s.get("weight_text"), "x_text": _i18n(lv),
                        "display_text": s["display_text"], "group": lv})
    cys = [s["y"] for s in pc["studies"]] + [g["ci_lower"] for g in groups if g["ci_lower"] is not None] + \
        [g["ci_upper"] for g in groups if g["ci_upper"] is not None]
    pc["bubble"] = {
        "moderator": {"name": "allokáció", "label": {"hu": "allokáció", "en": "allocation"}, "type": "categorical"},
        "x_axis": {"domain": [-0.5, len(levels) - 0.5], "ticks": [{"at": float(i), "text": lv, "text_i18n": {"hu": lv, "en": lv}}
                                                                  for i, lv in enumerate(levels)],
                   "title": {"hu": "allokáció", "en": "allocation"}},
        "y_axis": _yaxis(min(cys), max(cys), "RR"),
        "points": cpoints, "line": [], "band": [], "groups": groups,
        "coef_text": pc["subgroup_test"]["text"] if pc.get("subgroup_test") else None}
    pc["meta"]["run_id"] = BUBBLE_CAT_RUN
    rec = Recorder()
    env = lambda p: rec.norm({"ok": True, "schema": "szk.ma.plot/v2", "data": p, "warnings": [],  # noqa: E731
                              "meta": {"engine": api.__version__}})
    return {"description": "v1-ábrák (3.5.8, E4c): GET /api/runs/<id>/plot a VALÓDI motor szk.ma.plot/v2-jével (api.analyze, "
                           "BCG, RR) — %s: kumulatív (év) és buborék (szélesség), mindkettő a motoré; %s: kategóriás "
                           "moderátor (allokáció): pontdiagram a motor alcsoport-összesítőivel — ezt a motor még nem írja, a "
                           "generátor (tests/gui/ui/gen_extraction_fixtures.py) építi a 4.6 szerződés szerint." % (BUBBLE_RUN, BUBBLE_CAT_RUN),
            "routes": [{"method": "GET", "path": "/api/runs/%s/plot" % BUBBLE_RUN, "envelope": env(plot)},
                       {"method": "GET", "path": "/api/runs/%s/plot" % BUBBLE_CAT_RUN, "envelope": env(pc)}]}


def dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    out = {DUAL: dump(gen_dual()), PLOTS: dump(gen_plots())}
    bad = []
    for path, text in out.items():
        if a.check:
            cur = path.read_text(encoding="utf-8") if path.is_file() else None
            if cur != text:
                bad.append(path.name)
        else:
            path.write_text(text, encoding="utf-8")
            print("írva: %s (%d bájt)" % (path.relative_to(ROOT), len(text.encode("utf-8"))))
    if bad:
        print("ELTÉRÉS (futtasd: python3 tests/gui/ui/gen_extraction_fixtures.py): %s" % ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
