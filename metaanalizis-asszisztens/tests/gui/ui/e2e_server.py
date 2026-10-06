#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Segéd a végponttól végpontig tartó elfogadási teszthez (tests/gui/ui/e2e.spec.js; terv 9.2 „Elfogadás”).

    python3 tests/gui/ui/e2e_server.py serve <ideiglenes mappa>
        Ideiglenes projektet készít (BCG → o1, Normand → o2, két kis generált PDF a _privat/pdf/-ben és a
        03_adatok/documents.json jegyzék), és elindítja rá a VALÓDI munkapad-szervert (ma_gui.server.App, port 0,
        a szerver a saját válaszait is sémán ellenőrzi). Az első stdout-sor JSON: {url, base, code, proj, home}.
        stdin-parancsok: 'code' → új indítókód ({"code": …}); 'quit' (vagy EOF) → leállítás.
    python3 tests/gui/ui/e2e_server.py expected <projekt> <futás-mappa (projekt-relatív)>
        A futás motor-szövegei (plot_data.json) ÉS ugyanezek a motor formázóival újraszámolva a results.json
        számaiból (a számhűség-lánc, terv 6.7), plusz: a report.md és a motor forest.svg-je tartalmazza-e őket.
    python3 tests/gui/ui/e2e_server.py audit <projekt>
        api.project_audit (szk.ma.project-audit/v1) — az X-találatok a felület összevetéséhez.
    python3 tests/gui/ui/e2e_server.py convert '<szk.ma.convert-request/v1 JSON>'
        api.convert közvetlenül — az átváltó képernyő számainak összevetéséhez.

A projekt adatai szándékosan hiányosak: a BCG-ből 3 sor és a Normand-ból 8 sor a felületen, beillesztéssel kerül be
(adatbevitel), a Normand Montreal-Home sorának átlaga/SD-je pedig az átváltóval (medián/IQR → átlag/SD)."""
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BCG_HEADER = ["vizsgálat", "esemény1", "n1", "esemény2", "n2", "szélesség", "év", "allokáció"]
NORMAND_HEADER = ["study", "m1", "sd1", "n1", "m2", "sd2", "n2", "estimated"]
BCG_FIRST = 10                     # ennyi sor van a fájlban; a többi 3-at a teszt illeszti be
PDFS = {
    "file:_privat/pdf/aronson1948.pdf": ("_privat/pdf/aronson1948.pdf", "Aronson 1948 — Am Rev Tuberc", 3),
    "file:_privat/pdf/normand1999.pdf": ("_privat/pdf/normand1999.pdf", "Normand 1999 — Stat Med (Edinburgh)", 2),
}


def make_pdf(title, pages):
    """Kicsi, érvényes többoldalas PDF (stdlib): minden oldalon a cím és az oldalszám."""
    objs = []

    def add(body):
        objs.append(body)
        return len(objs)
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objs) + 2 * pages + 1
    kids = []
    for i in range(1, pages + 1):
        text = ("%s - %d. oldal" % (title.encode("ascii", "replace").decode("ascii"), i)).replace("(", "[").replace(")", "]")
        stream = ("BT /F1 18 Tf 72 720 Td (%s) Tj ET" % text).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                         "/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_id, content, font)).encode("ascii")))
    pid = add(("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in kids), pages)).encode("ascii"))
    assert pid == pages_id
    cat = add(("<< /Type /Catalog /Pages %d 0 R >>" % pid).encode("ascii"))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, cat, xref)
    return bytes(out)


def _read_rows(path):
    with open(path, encoding="utf-8") as fh:
        lines = [ln.rstrip("\r\n") for ln in fh if ln.strip()]
    return [ln.split(";") for ln in lines[1:]]


def make_project(base):
    from metaelemzes import api
    proj = os.path.join(base, "projekt")
    home = os.path.join(base, "home")
    os.makedirs(home)
    api.project_init(proj, "BCG és Normand — elfogadási teszt")
    os.makedirs(os.path.join(proj, "03_adatok"), exist_ok=True)
    os.makedirs(os.path.join(proj, "_privat", "pdf"), exist_ok=True)
    bcg = _read_rows(os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv"))
    with open(os.path.join(proj, "03_adatok", "o1.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write(";".join(BCG_HEADER) + "\r\n")
        for r in bcg[:BCG_FIRST]:
            fh.write(";".join(r) + "\r\n")
    normand = _read_rows(os.path.join(ROOT, "peldak", "normand1999_folytonos.csv"))
    with open(os.path.join(proj, "03_adatok", "o2.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write(";".join(NORMAND_HEADER) + "\r\n")
        fh.write(";".join(normand[0] + ["nem"]) + "\r\n")            # Edinburgh; a többi beillesztéssel
    docs = []
    for did, (rel, title, pages) in sorted(PDFS.items()):
        with open(os.path.join(proj, rel), "wb") as fh:
            fh.write(make_pdf(title, pages))
        docs.append({"id": did, "root": "project", "path": rel, "sha256": None, "pages": pages, "title": title})
    with open(os.path.join(proj, "03_adatok", "documents.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": "szk.ma.documents/v1", "docs": docs}, fh, ensure_ascii=False, indent=1)
    meta = {"schema": "szk.ma.project/v1", "title": "BCG és Normand — elfogadási teszt", "data_class": "A",
            "outcomes": [{"id": "o1", "name": {"hu": "TBC-incidencia", "en": "TB incidence"}, "data": "03_adatok/o1.csv",
                          "measure": "RR", "primary_spec": "05_elemzes/specs/o1_primary.json"},
                         {"id": "o2", "name": {"hu": "Kórházi napok", "en": "Hospital days"}, "data": "03_adatok/o2.csv",
                          "measure": "MD", "primary_spec": "05_elemzes/specs/o2_primary.json"}]}
    with open(os.path.join(proj, "ma-projekt.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    return proj, home


def make_init_only_project(base):
    """DOC-1: a TELEPITES szerinti út — csak `ma.py project init` (nincs ma-projekt.json, nincs kimenet); a
    felhasználó a BCG-táblát bemásolja a 03_adatok/ alá. A kimenetet a munkapadon kell felvenni."""
    from metaelemzes import api
    proj = os.path.join(base, "projekt")
    home = os.path.join(base, "home")
    os.makedirs(home)
    api.project_init(proj, "Új projekt — csak project init")
    bcg = _read_rows(os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv"))
    with open(os.path.join(ROOT, "peldak", "bcg_oltas_RR.csv"), encoding="utf-8") as fh:
        header = fh.readline().strip()
    with open(os.path.join(proj, "03_adatok", "bcg.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write(header + "\r\n")
        for r in bcg:
            fh.write(";".join(r) + "\r\n")
    return proj, home


def serve(base, init_only=False):
    from ma_gui import caps as caps_mod
    from ma_gui import server
    proj, home = make_init_only_project(base) if init_only else make_project(base)
    app = server.App(proj, kb_db=os.path.join(base, "kb.sqlite"),
                     caps=caps_mod.Caps(runtime_dir=os.path.join(base, "caps"), env={}, home=home),
                     privacy_home=home, privacy_env={}, selftest=False, kb_build=True, caps_refresh=False,
                     idle_hours=0, watch_interval=0.3, validate_responses=True,
                     log_stream=open(os.path.join(base, "server.log"), "w", encoding="utf-8"))
    app.start(port=0)
    th = threading.Thread(target=app.serve_forever, name="e2e-serve", daemon=True)
    th.start()
    url = app.launch_url()
    print(json.dumps({"url": url, "base": url.split("/#")[0], "code": url.split("#launch=")[1], "proj": proj,
                      "home": home, "log": os.path.join(base, "server.log")}), flush=True)
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


def expected(proj, run_dir):
    """A futás szövegei: a plot_data.json (amit a felület kiír) és ugyanezek a results.json számaiból a motor
    formázóival (a felület nem formáz: ha a kettő egyezik, a DOM-szöveg = a motor száma)."""
    from metaelemzes import pipeline, plots as P
    d = os.path.join(proj, run_dir)

    def load(name):
        with open(os.path.join(d, name), encoding="utf-8") as fh:
            return json.load(fh)
    plot, res, run = load("plot_data.json"), load("results.json"), load("run.json")
    prim = [s for s in plot["summaries"] if s.get("primary")][0]
    bt = res["back_transformed"]
    rp = res["primary"]
    recomputed = {
        "display_text": P.display_text(*bt["estimate_ci"]),
        "pi_text": {"hu": P.fmt_pair(bt["pi"][0], bt["pi"][1]), "en": P.fmt_pair(bt["pi"][0], bt["pi"][1], P.MINUS)}
        if bt.get("pi") else None,
        "p_text": pipeline._p_text(rp["p"]),
        "i2_text": pipeline._pct(rp["I2"]),
        "tau2_text": {"hu": pipeline._g3(rp["tau2"]), "en": pipeline._g3(rp["tau2"], P.MINUS)}
        if res.get("primary_model") in ("random", "ivhet") else None,
        "k": rp["k"],
    }
    with open(os.path.join(d, "report.md"), encoding="utf-8") as fh:
        report = fh.read()
    with open(os.path.join(d, "forest.svg"), encoding="utf-8") as fh:
        svg = fh.read()
    by_uid = {s["row_uid"]: s for s in plot["studies"]}
    return {
        "run_id": run["run_id"], "measure": plot["measure"], "k": plot["k"],
        "plot": {"display_text": prim["display_text"], "pi_text": prim.get("pi_text"), "p_text": prim.get("p_text"),
                 "i2_text": plot["heterogeneity"]["i2_text"], "tau2_text": plot["heterogeneity"]["tau2_text"],
                 "het_text": plot["heterogeneity"]["text"], "subgroup_test": (plot.get("subgroup_test") or {}).get("text")},
        "recomputed": recomputed,
        "participants_text": run.get("participants_text"),
        "report_has_display": prim["display_text"]["hu"] in report,
        "svg_has_display": prim["display_text"]["hu"] in svg,
        "studies": [{"row_uid": s["row_uid"], "label": s["label"], "display_text": s["display_text"],
                     "y": s["y"], "lo": s["lo"], "hi": s["hi"], "source": s.get("source"),
                     "estimated": s["flags"]["estimated"]} for s in plot["studies"]],
        "study_by_label": {s["label"]: s["row_uid"] for s in plot["studies"]},
        "studies_recomputed_ok": all(
            s["display_text"] == P.display_text(s["display"]["est"], s["display"]["lo"], s["display"]["hi"])
            for s in plot["studies"]),
        "uids": sorted(by_uid),
        "loo": [{"row_uid": e.get("row_uid"), "display_text": e["display_text"]} for e in plot.get("loo") or []],
        "influence_text": plot.get("influence_text"),
        "influence": [{"row_uid": e.get("row_uid"), "rstudent_text": e.get("rstudent_text")} for e in
                      plot.get("influence") or []],
        "doi_lfk_text": (plot.get("doi") or {}).get("lfk_text"),
        "funnel_tests_text": (plot.get("funnel") or {}).get("tests_text"),
        "n_contours": len((plot.get("funnel") or {}).get("contours") or []),
    }


def convert(request_json):
    from metaelemzes import api
    return api.convert(json.loads(request_json))


def audit(proj):
    from metaelemzes import api
    return api.project_audit(proj)


def main(argv):
    if len(argv) >= 2 and argv[0] == "serve":
        serve(argv[1])
        return 0
    if len(argv) >= 2 and argv[0] == "serve-init":
        serve(argv[1], init_only=True)
        return 0
    if len(argv) >= 3 and argv[0] == "expected":
        print(json.dumps(expected(argv[1], argv[2]), ensure_ascii=False))
        return 0
    if len(argv) >= 2 and argv[0] == "convert":
        print(json.dumps(convert(argv[1]), ensure_ascii=False))
        return 0
    if len(argv) >= 2 and argv[0] == "audit":
        print(json.dumps(audit(argv[1]), ensure_ascii=False))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
