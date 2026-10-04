# -*- coding: utf-8 -*-
"""Parancssori felület.  Használat:  python ma.py <parancs> [opciók]   (súgó: -h)

Parancsok (magyar álnévvel):
  analyze  / elemez       teljes elemzés CSV-ből → riport, ábrák, JSON
  validate / validal      csak adatvalidálás (kilépési kód 1, ha hiba van)
  es       / hatasmeret   vizsgálatonkénti hatásméretek CSV-be
  convert  / konvertal    adatkinyerési konverziók (medián/IQR, SE, CI → SD stb.)
  kb       / tudasbazis   tudásbázis: build, ingest, search, show, rules, checklist, sql, stats
  project  / projekt      projektnapló: init, log, finding, resolve, checkpoint, grade, status, export
  selftest / onteszt      a beépített tesztek futtatása
"""
import argparse
import datetime
import json
import os
import sys

from . import __version__


def _utf8_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _print_json(obj):
    from .pipeline import to_jsonable
    print(json.dumps(to_jsonable(obj), ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ analyze
_PATH_OPTIONS = ("--data", "--out", "--project")
_SCALES = {"OR": ("log", "arány (exp)"), "RR": ("log", "arány (exp)"), "ROM": ("log", "arány (exp)"),
           "PLN": ("log", "arány"), "PLO": ("logit", "arány"), "PAS": ("arcsin", "arány"),
           "PFT": ("Freeman–Tukey", "arány"), "ZCOR": ("Fisher z", "r")}


def _replay_command(a, outdir):
    """Újrafuttatható parancssor a projektnaplóhoz: abszolút útvonalak (program, --data,
    --out, --project) és shell-idézőjelezés (POSIX: shlex.join; Windows: list2cmdline)."""
    import shlex
    import subprocess
    args = list(getattr(a, "_argv", None) or sys.argv[1:])
    res, i = [], 0
    while i < len(args):
        t = args[i]
        opt, eq, val = t.partition("=")
        if t.startswith("--") and not eq and len(t) > 2 and i + 1 < len(args) and \
                any(o.startswith(t) for o in _PATH_OPTIONS):
            res += [t, os.path.abspath(args[i + 1])]
            i += 2
            continue
        if t.startswith("--") and eq and len(opt) > 2 and any(o.startswith(opt) for o in _PATH_OPTIONS):
            res.append("%s=%s" % (opt, os.path.abspath(val)))
        else:
            res.append(t)
        i += 1
    if not a.out:
        res += ["--out", os.path.abspath(outdir)]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ma_py = os.path.join(here, "ma.py")
    prog = [os.path.basename(sys.executable) or "python3"]
    prog += [ma_py] if os.path.exists(ma_py) else ["-m", "metaelemzes"]
    if os.name == "nt":
        return subprocess.list2cmdline(prog + res)
    return shlex.join(prog + res)


def _run_summary(out):
    """A futás összefoglalója a projektnaplóba, a skála egyértelmű jelölésével."""
    measure = out["effect_sizes"]["measure"]
    summ = {"k": out["effect_sizes"]["k"], "measure": measure}
    pr = out.get("primary")
    if pr is None:
        return summ
    scale, disp = _SCALES.get(measure, ("nyers", "nyers"))
    bt = (out.get("back_transformed") or {}).get("estimate_ci") or [None, None, None]
    summ.update({"model": out.get("primary_model"), "scale": scale,
                 "estimate": pr.estimate, "ci": [pr.ci_lower, pr.ci_upper],
                 "display_scale": disp, "estimate_display": bt[0], "ci_display": [bt[1], bt[2]],
                 "level": out.get("options", {}).get("level"), "I2": pr.I2})
    return summ


def cmd_analyze(a):
    from . import tableio, pipeline, report, projekt
    if a.project:   # nem inicializált projekt: hiba, MIELŐTT bármilyen kimenet készülne
        projekt.connect(a.project).close()
    rows, meta = tableio.read_table(a.data)
    filter_report = []
    n_before = len(rows)
    rows = tableio.apply_filters(rows, a.exclude, a.include, meta=meta, report=filter_report)
    meta["filters"] = {"exclude": a.exclude, "include": a.include}
    for fr in filter_report:
        print("Szűrő (%s) %s: %d sor kizárva%s" % (
            "kizáró" if fr["mode"] == "exclude" else "megtartó", fr["filter"], fr["removed"],
            (" — " + ", ".join(fr["removed_labels"])) if fr["removed_labels"] else ""))
        if fr["mode"] == "exclude" and fr["removed"] == 0:
            print("FIGYELEM: a(z) '%s' szűrő egyetlen sorra sem illeszkedett (az eredmény azonos a "
                  "szűretlen elemzéssel)." % fr["filter"])
    if filter_report and not rows:
        print("FIGYELEM: a szűrés után nem maradt sor (%d sorból)." % n_before)
    opts = {"measure": a.measure, "model": a.model, "tau2": a.tau2, "ci": a.ci, "pi": a.pi,
            "level": a.level, "smd_vtype": a.smd_vtype, "j_method": a.j_method, "cc": a.cc,
            "cc_to": a.cc_to, "mh": a.mh, "peto": a.peto, "rd_var": a.rd_var, "subgroup": a.subgroup,
            "common_tau2": a.common_tau2,
            "moderators": [m.strip() for m in a.moderators.split(",")] if a.moderators else [],
            "metareg_test": a.metareg_test, "cumulative": a.cumulative, "title": a.title,
            "left_label": a.left_label, "right_label": a.right_label,
            "trimfill_estimator": a.trimfill_estimator, "filter_report": filter_report}
    if a.drop00 is not None:
        opts["drop00"] = a.drop00 == "yes"
    out, es = pipeline.run(rows, opts, meta)
    date = a.date or datetime.date.today().isoformat()
    md = report.build_report(out, a.title, date)
    outdir = a.out or os.path.join(os.path.dirname(os.path.abspath(a.data)), "eredmeny")
    paths = pipeline.write_outputs(out, es, outdir, md, plots=not a.no_plots)
    if a.project:
        projekt.log_run(a.project, _replay_command(a, outdir), os.path.abspath(a.data), os.path.abspath(outdir),
                        __version__, pipeline.to_jsonable(_run_summary(out)))
    v = out["validation"]["summary"]
    print("Kész: %s" % outdir)
    for k, p in sorted(paths.items()):
        print("  - %s" % p)
    print("Validálás: %d hiba, %d figyelmeztetés, %d megjegyzés" % (v["error"], v["warning"], v["info"]))
    if out.get("primary") is None:
        print("HIBA: nincs elemezhető vizsgálat (k = 0); lásd a validálási tételeket és a kizárt sorokat.",
              file=sys.stderr)
        return 1
    bt = out["back_transformed"]["estimate_ci"]
    print("Összesített becslés: %.4g [%.4g; %.4g], k = %d, I² = %.1f%%" % (
        bt[0], bt[1], bt[2], out["effect_sizes"]["k"], out["primary"].I2))
    return 0


# ----------------------------------------------------------------- validate
def cmd_validate(a):
    from . import tableio, validate, effect_sizes
    rows, meta = tableio.read_table(a.data)
    f = validate.validate(rows, a.measure.upper(), meta)
    # ugyanaz a kizárás, mint az elemzésben: a vizsgálat-szintű hibás sorok kimaradnak
    es = effect_sizes.compute(rows, a.measure.upper(), skip_labels=validate.blocking_reasons(f))
    f += validate.check_effect_sizes(es)
    if a.json:
        _print_json({"summary": validate.summarize(f), "findings": f, "k": len(es),
                     "excluded": [{"study": s, "reason": r} for s, r in es.excluded]})
    else:
        s = validate.summarize(f)
        print("Adatvalidálás (%s, %d sor): %d hiba, %d figyelmeztetés, %d megjegyzés" % (
            a.measure.upper(), len(rows), s["error"], s["warning"], s["info"]))
        for x in f:
            print("[%s] %-7s %s — %s%s" % (x["code"], x["severity"], x["study"] or "(globális)", x["title"],
                                         (": " + x["detail"]) if x["detail"] else ""))
        print("Elemezhető vizsgálatok: k = %d (%d sor kimarad)" % (len(es), len(es.excluded)))
        for lab, why in es.excluded:
            print("  kimarad: %s — %s" % (lab, why))
    return 1 if any(x["severity"] == "error" for x in f) else 0


def cmd_es(a):
    from . import tableio, validate, effect_sizes as E
    from .distributions import norm_ppf
    import math
    rows, meta = tableio.read_table(a.data)
    findings = validate.validate(rows, a.measure.upper(), meta, {"smd_vtype": a.smd_vtype, "cc": a.cc})
    es = E.compute(rows, a.measure.upper(), smd_vtype=a.smd_vtype, cc=a.cc,
                   skip_labels=validate.blocking_reasons(findings))
    z = norm_ppf(0.975)
    out_rows = []
    for lab, y, v, note in zip(es.labels, es.yi, es.vi, es.notes):
        se = math.sqrt(v)
        out_rows.append([lab, y, v, se, y - z * se, y + z * se, note])
    target = a.out or os.path.splitext(a.data)[0] + "_hatasmeretek.csv"
    tableio.write_csv(target, ["study", "yi", "vi", "sei", "ci_lower", "ci_upper", "note"], out_rows)
    print("Kiírva: %s (%d vizsgálat, %d kizárva)" % (target, len(es), len(es.excluded)))
    for lab, why in es.excluded:
        print("  kizárva: %s — %s" % (lab, why))
    return 0


# ------------------------------------------------------------------ convert
_CONVERT_REQUIRED = {
    "median": ("n", "median"),
    "se": ("se", "n"),
    "ci": ("lower", "upper", "n"),
    "combine": ("n1", "m1", "sd1", "n2", "m2", "sd2"),
    "change": ("sd_baseline", "sd_final", "corr"),
    "se-from-ci": ("lower", "upper"),
}


def cmd_convert(a):
    from . import conversions as C
    k = a.kind
    missing = [n for n in _CONVERT_REQUIRED.get(k, ()) if getattr(a, n, None) is None]
    if k == "median" and not ((a.q1 is not None and a.q3 is not None) or (a.min is not None and a.max is not None)):
        missing.append("q1+q3 vagy min+max")
    if missing:
        raise C.ConversionError("%s: hiányzó kötelező argumentum: %s" % (
            k, ", ".join("--" + m.replace("_", "-") if "+" not in m else m for m in missing)))
    if k == "median":
        mean = C.mean_from_median(a.n, a.median, a.q1, a.q3, a.min, a.max, a.method)
        sd = C.sd_from_median(a.n, a.q1, a.q3, a.min, a.max, median=a.median)
        res = {"mean": mean, "sd": sd, "method": "Luo 2018 (átlag) + Wan 2014 (SD)" if a.method == "luo" else "Hozo 2005 (átlag) + Wan 2014 (SD)"}
    elif k == "se":
        res = {"sd": C.sd_from_se(a.se, a.n)}
    elif k == "ci":
        res = {"sd": C.sd_from_ci(a.lower, a.upper, a.n, a.level)}
    elif k == "combine":
        n, m, sd = C.combine_groups(a.n1, a.m1, a.sd1, a.n2, a.m2, a.sd2)
        res = {"n": n, "mean": m, "sd": sd}
    elif k == "change":
        res = {"sd_change": C.sd_change(a.sd_baseline, a.sd_final, a.corr)}
    elif k == "se-from-ci":
        res = {"se": C.se_from_ci(a.lower, a.upper, a.level, a.log)}
    else:
        raise SystemExit("ismeretlen konverzió")
    res["figyelem"] = "Becsült érték — jelöld az adattáblában (estimated=igen) és végezz érzékenységi elemzést."
    _print_json(res)
    return 0


# ------------------------------------------------------------------------ kb
def cmd_kb(a):
    from . import kb
    if a.kb_cmd == "build":
        _print_json(kb.build(a.db))
    elif a.kb_cmd == "ingest":
        report = []
        kb.ingest(a.path, a.source_id, a.citation, a.db, report=report)
        n_err = 0
        for r in report:
            if r["status"] == "ok":
                print("%s: %s darab ← %s%s" % (r["source_id"], r["chunks"], r["file"],
                                               (" (FIGYELEM: %s)" % r["message"]) if r["message"] else ""))
            elif r["status"] == "error":
                n_err += 1
                print("HIBA: %s" % r["message"] if r["file"] in r["message"] else
                      "HIBA: %s: %s" % (r["file"], r["message"]), file=sys.stderr)
            else:
                print("kihagyva: %s (%s)" % (r["file"], r["message"]))
        if not any(r["status"] in ("ok", "duplicate") for r in report):
            print("Nem töltődött be egyetlen fájl sem (támogatott: PDF/DOCX/TXT/MD): %s" % a.path, file=sys.stderr)
            return 1
        if n_err:
            print("%d fájl betöltése nem sikerült (a többi betöltve)." % n_err, file=sys.stderr)
            return 1
    elif a.kb_cmd == "search":
        res = kb.search(a.query, a.limit, a.db, tuple(a.scope.split(",")), a.source)
        if a.json:
            _print_json(res)
        else:
            for scope, title in (("rule", "SZABÁLYOK"), ("knowledge", "TUDÁS"), ("chunk", "TELJES SZÖVEG")):
                items = res.get(scope) or []
                if not items:
                    continue
                print("== %s (%d)" % (title, len(items)))
                for it in items:
                    if scope == "rule":
                        print("[%s] (%s, %s) HA %s → %s  {%s %s}" % (it["id"], it["stage_id"], it["strength"],
                              it["condition"], it["recommendation"], it["source_ids"] or "", it["locator"] or ""))
                    elif scope == "knowledge":
                        print("[%s] (%s, %s) %s — %s  {%s %s}" % (it["id"], it["stage_id"], it["kind"], it["title"],
                              it["snippet"], it["source_id"], it["locator"] or ""))
                    else:
                        print("[#%s] %s %s — %s" % (it["id"], it["source_id"], it["locator"] or "", it["snippet"]))
    elif a.kb_cmd == "show":
        item = kb.show(a.id, a.db)
        if item is None:
            print("Nincs ilyen azonosító: %s" % a.id)
            return 1
        _print_json(item)
    elif a.kb_cmd == "rules":
        if a.stage:
            stages = kb.stage_ids(a.db)
            if kb.normalize_stage(a.stage) not in stages:
                print("Ismeretlen szakasz: %s. Elérhető: %s" % (a.stage, ", ".join(stages) or "–"), file=sys.stderr)
                return 1
        rows = kb.rules(a.stage, a.agent, a.db)
        if not rows:
            print("Nincs szabály erre a szűrésre (szakasz: %s, ágens: %s) — a tudásbázis nem fedi le; ne adj meg "
                  "kitalált szabály-ID-t." % (a.stage or "mind", a.agent or "mind"), file=sys.stderr)
        if a.json:
            _print_json(rows)
        else:
            for r in rows:
                print("[%s] %s | %s | HA %s → %s" % (r["rule_id"], r["stage_id"], r["strength"], r["condition"], r["recommendation"]))
    elif a.kb_cmd == "checklist":
        rows = kb.checklist(a.name, a.db)
        if not rows:
            names = kb.checklist_names(a.db)
            print("Nincs ilyen (vagy üres) ellenőrzőlista: %s. Elérhető: %s" % (
                a.name, ", ".join(names) or "– (a tudásbázisban még nincs ellenőrzőlista: checklists*.json seed)"),
                file=sys.stderr)
            return 1
        if a.json:
            _print_json(rows)
        else:
            for r in rows:
                print("[%s] %s%s" % (r["item_id"], (r["section"] + ": ") if r["section"] else "", r["text"]))
    elif a.kb_cmd == "sql":
        info = {}
        cols, rows = kb.query(a.sql, db=a.db, max_rows=a.max_rows, timeout=a.timeout, info=info)
        if info.get("truncated"):
            print("FIGYELEM: az eredmény csonkolva — csak az első %d sor jelenik meg, több is van. Használj "
                  "LIMIT/OFFSET-et vagy --max-rows-t (0 = korlát nélkül)." % a.max_rows, file=sys.stderr)
        if a.json:
            _print_json([dict(zip(cols, r)) for r in rows])
        else:
            print("\t".join(cols))
            for r in rows:
                print("\t".join("" if v is None else str(v) for v in r))
    elif a.kb_cmd == "stats":
        _print_json(kb.stats(a.db))
    return 0


# ------------------------------------------------------------------- project
def cmd_project(a):
    from . import projekt
    d = a.dir
    if a.p_cmd == "init":
        copied = projekt.init(d, a.title, a.question)
        print("Projekt létrehozva: %s" % d)
        for c in copied:
            print("  sablon: %s" % c)
    elif a.p_cmd in ("log", "finding", "checkpoint", "grade"):
        warns = []
        kbdb = getattr(a, "kb_db", None)
        if a.p_cmd == "log":
            print("döntés #%d" % projekt.log_decision(d, a.agent, a.decision, a.rationale, a.stage, a.kb, a.alternatives,
                                                      a.supersedes, kb_db=kbdb, strict=a.strict, warnings=warns))
        elif a.p_cmd == "finding":
            print("megállapítás #%d" % projekt.add_finding(d, a.agent, a.severity, a.title, a.detail, a.stage, a.evidence,
                                                           a.kb, kb_db=kbdb, strict=a.strict, warnings=warns))
        elif a.p_cmd == "checkpoint":
            stages = projekt.parse_stage(a.stage)
            rid = projekt.checkpoint(d, a.stage, a.agent, a.verdict, a.summary, warnings=warns)
            if len(stages) == 1:
                print("ellenőrzőpont #%d (%s: %s)" % (rid, stages[0], a.verdict))
            else:
                print("ellenőrzőpontok #%d–#%d (%s: %s)" % (rid - len(stages) + 1, rid, ", ".join(stages), a.verdict))
        else:
            print("GRADE #%d" % projekt.add_grade(d, a.outcome, a.certainty, kb_db=kbdb, strict=a.strict, warnings=warns,
                  k=a.k, participants=a.participants, effect=a.effect, risk_of_bias=a.rob,
                  inconsistency=a.inconsistency, indirectness=a.indirectness, imprecision=a.imprecision,
                  publication_bias=a.publication_bias, upgrades=a.upgrades, rationale=a.rationale, kb_refs=a.kb))
        for w in warns:
            print("FIGYELEM: %s" % w, file=sys.stderr)
    elif a.p_cmd == "resolve":
        projekt.resolve_finding(d, a.id, a.status, a.resolution)
        print("megállapítás #%d → %s" % (a.id, a.status))
    elif a.p_cmd == "status":
        st = projekt.status(d)
        _print_json(st)
        for w in st.get("warnings", []):
            print("FIGYELEM: %s" % w, file=sys.stderr)
    elif a.p_cmd == "show":
        _print_json(projekt.get_item(d, a.kind, a.id))
    elif a.p_cmd == "list":
        _print_json(projekt.list_items(d, a.kind, status=a.status, severity=a.severity, stage=a.stage))
    elif a.p_cmd == "export":
        md = projekt.export_markdown(d)
        target = a.out or os.path.join(d, "07_ellenorzes", "dontesi_naplo.md")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("Kiírva: %s" % target)
    return 0


def cmd_selftest(a):
    import unittest
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tests = os.path.join(here, "tests")
    sys.path.insert(0, tests)
    suite = unittest.defaultTestLoader.discover(tests)
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if res.wasSuccessful() else 1


# ------------------------------------------------------------------- parser
def _level(s):
    """--level: 0 < level < 1 (pl. 0.95); a metafor-konvenció szerint az 1 < x < 100 érték
    százalék (95 → 0.95)."""
    try:
        x = float(str(s).strip().replace(",", "."))
    except ValueError:
        raise argparse.ArgumentTypeError("érvénytelen szám: %r" % s)
    if 1 < x < 100:
        x = x / 100.0
    if not 0 < x < 1:
        raise argparse.ArgumentTypeError("0 < level < 1 szükséges (pl. 0.95 vagy 95), kapott: %s" % s)
    return x


_STAGE_HELP = "szakasz: S00–S14, tartomány (pl. S01-S02, szakaszonként kibontva) vagy FINAL"
_KB_HELP = ("tudásbázis-azonosítók vesszővel (pl. V015,S08); csak a kb show/kb search által ismert ID — "
            "ismeretlen ID: figyelmeztetés és jelölés, --strict esetén hiba")


def build_parser():
    from .effect_sizes import ALL_MEASURES
    from .models import TAU2_METHODS, CI_METHODS, PI_METHODS
    p = argparse.ArgumentParser(prog="ma.py", description="Metaanalízis-motor (metaelemzes v%s)" % __version__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    an = sub.add_parser("analyze", aliases=["elemez"], help="teljes elemzés")
    an.add_argument("--data", required=True, help="CSV/TSV (; vagy , elválasztó, tizedesvessző is)")
    an.add_argument("--measure", required=True, type=str.upper, choices=ALL_MEASURES)
    an.add_argument("--model", default="random", choices=["random", "fixed", "ivhet"])
    an.add_argument("--tau2", default="REML", type=str.upper, choices=TAU2_METHODS)
    an.add_argument("--ci", default=None, choices=CI_METHODS, help="alap: random→hksj, fixed→z")
    an.add_argument("--pi", default="t_k-2", choices=PI_METHODS)
    an.add_argument("--level", type=_level, default=0.95, help="megbízhatósági szint: 0.95 vagy 95")
    an.add_argument("--smd-vtype", default="LS", choices=["LS", "LS2", "UB"])
    an.add_argument("--j-method", default="exact", choices=["exact", "approx"])
    an.add_argument("--cc", type=float, default=0.5, help="folytonossági korrekció")
    an.add_argument("--cc-to", default="only0", choices=["only0", "all", "none"],
                    help="only0: csak nulla cellás vizsgálatnál (RD-nél nincs korrekció, Cochrane 10.4.4.1); "
                         "all: minden vizsgálatnál (RD-nél is, metafor to='all'); none: soha")
    an.add_argument("--drop00", choices=["yes", "no"], default=None)
    an.add_argument("--mh", action="store_true", help="Mantel–Haenszel (bináris)")
    an.add_argument("--peto", action="store_true", help="Peto OR")
    an.add_argument("--rd-var", default="sato", choices=["sato", "gr"])
    an.add_argument("--subgroup", help="alcsoport-oszlop")
    an.add_argument("--common-tau2", action="store_true")
    an.add_argument("--moderators", help="meta-regresszió moderátorai, vesszővel")
    an.add_argument("--metareg-test", default="knha", choices=["knha", "z"])
    an.add_argument("--cumulative", help="kumulatív elemzés rendező oszlopa (pl. year)")
    an.add_argument("--trimfill-estimator", default="L0", choices=["L0", "R0"])
    an.add_argument("--exclude", action="append", help="sorszűrő: oszlop=érték (ismételhető)")
    an.add_argument("--include", action="append", help="sorszűrő: oszlop=érték (ismételhető)")
    an.add_argument("--title")
    an.add_argument("--left-label", help="forest plot bal oldali felirat (pl. 'kontroll jobb')")
    an.add_argument("--right-label")
    an.add_argument("--out", help="kimeneti mappa")
    an.add_argument("--project", help="projektmappa (a futás naplózásához)")
    an.add_argument("--date", help="riport dátuma (alap: ma)")
    an.add_argument("--no-plots", action="store_true")
    an.set_defaults(func=cmd_analyze)

    va = sub.add_parser("validate", aliases=["validal"], help="adatvalidálás")
    va.add_argument("--data", required=True)
    va.add_argument("--measure", required=True, type=str.upper, choices=ALL_MEASURES)
    va.add_argument("--json", action="store_true")
    va.set_defaults(func=cmd_validate)

    e = sub.add_parser("es", aliases=["hatasmeret"], help="hatásméretek CSV-be")
    e.add_argument("--data", required=True)
    e.add_argument("--measure", required=True, type=str.upper, choices=ALL_MEASURES)
    e.add_argument("--smd-vtype", default="LS", choices=["LS", "LS2", "UB"])
    e.add_argument("--cc", type=float, default=0.5)
    e.add_argument("--out")
    e.set_defaults(func=cmd_es)

    c = sub.add_parser("convert", aliases=["konvertal"], help="konverziók")
    c.add_argument("kind", choices=["median", "se", "ci", "combine", "change", "se-from-ci"])
    for name in ("n", "median", "q1", "q3", "min", "max", "se", "lower", "upper", "n1", "m1", "sd1",
                 "n2", "m2", "sd2", "sd_baseline", "sd_final", "corr"):
        c.add_argument("--" + name.replace("_", "-"), dest=name, type=float)
    c.add_argument("--level", type=_level, default=0.95, help="megbízhatósági szint: 0.95 vagy 95")
    c.add_argument("--method", default="luo", choices=["luo", "hozo"])
    c.add_argument("--log", action="store_true", help="arány-mérték CI-je (log-skála)")
    c.set_defaults(func=cmd_convert)

    k = sub.add_parser("kb", aliases=["tudasbazis"], help="tudásbázis")
    k.add_argument("--db", help="adatbázis útvonala (alap: tudasbazis/tudasbazis.sqlite)")
    ks = k.add_subparsers(dest="kb_cmd")
    ks.add_parser("build", help="újraépítés a seed JSON-okból")
    ki = ks.add_parser("ingest", help="teljes szöveg betöltése (PDF/DOCX/TXT/MD fájl vagy mappa)")
    ki.add_argument("path")
    ki.add_argument("--source-id")
    ki.add_argument("--citation")
    kq = ks.add_parser("search", help="keresés (szabályok, tudás, teljes szöveg)")
    kq.add_argument("query")
    kq.add_argument("--limit", type=int, default=8)
    kq.add_argument("--scope", default="rule,knowledge,chunk")
    kq.add_argument("--source")
    kq.add_argument("--json", action="store_true")
    kw = ks.add_parser("show", help="egy tétel teljes adatai")
    kw.add_argument("id")
    kr = ks.add_parser("rules", help="döntési szabályok (a reviewer a motor V-szabályait is látja)")
    kr.add_argument("--stage", help="szakasz: S00–S14")
    kr.add_argument("--agent", choices=["planner", "reviewer", "evaluator", "orchestrator", "engine"])
    kr.add_argument("--json", action="store_true")
    kc = ks.add_parser("checklist", help="ellenőrzőlista (PRISMA2020, PREFLIGHT, REVIEWER, EVALUATOR, AMSTAR2, GRADE)")
    kc.add_argument("name")
    kc.add_argument("--json", action="store_true")
    kl = ks.add_parser("sql", help="csak-olvasó SQL (SELECT/WITH)")
    kl.add_argument("sql")
    kl.add_argument("--json", action="store_true")
    kl.add_argument("--max-rows", type=int, default=500, help="legfeljebb ennyi sor (0 = korlát nélkül; alap: 500)")
    kl.add_argument("--timeout", type=float, default=10.0, help="időkorlát másodpercben (0 = nincs; alap: 10)")
    ks.add_parser("stats", help="statisztika")
    k.set_defaults(func=cmd_kb)

    pj = sub.add_parser("project", aliases=["projekt"], help="projektnapló")
    pjs = pj.add_subparsers(dest="p_cmd")
    pi = pjs.add_parser("init")
    pi.add_argument("dir")
    pi.add_argument("--title", required=True)
    pi.add_argument("--question")
    pl = pjs.add_parser("log", help="döntés naplózása")
    pl.add_argument("dir")
    pl.add_argument("--agent", required=True)
    pl.add_argument("--decision", required=True)
    pl.add_argument("--rationale")
    pl.add_argument("--stage", help=_STAGE_HELP)
    pl.add_argument("--kb", help=_KB_HELP)
    pl.add_argument("--alternatives")
    pl.add_argument("--supersedes", type=int)
    pf = pjs.add_parser("finding", help="ellenőrzési megállapítás")
    pf.add_argument("dir")
    pf.add_argument("--agent", required=True)
    pf.add_argument("--severity", required=True, choices=["blocker", "major", "minor", "info"])
    pf.add_argument("--title", required=True)
    pf.add_argument("--detail")
    pf.add_argument("--stage", help=_STAGE_HELP)
    pf.add_argument("--evidence")
    pf.add_argument("--kb", help=_KB_HELP)
    pr = pjs.add_parser("resolve")
    pr.add_argument("dir")
    pr.add_argument("id", type=int)
    pr.add_argument("--status", required=True, choices=["fixed", "wontfix", "invalid"])
    pr.add_argument("--resolution", required=True)
    pc = pjs.add_parser("checkpoint")
    pc.add_argument("dir")
    pc.add_argument("--stage", required=True, help=_STAGE_HELP + "; FINAL: záró ellenőrzőpont (bármely nyitott "
                    "blocker kizárja a PASS-t)")
    pc.add_argument("--agent", required=True)
    pc.add_argument("--verdict", required=True, choices=["PASS", "PASS_WITH_FIXES", "FAIL"])
    pc.add_argument("--summary")
    pg = pjs.add_parser("grade")
    pg.add_argument("dir")
    pg.add_argument("--outcome", required=True)
    pg.add_argument("--certainty", required=True, choices=["high", "moderate", "low", "very low"])
    for name in ("effect", "rob", "inconsistency", "indirectness", "imprecision", "publication_bias",
                 "upgrades", "rationale", "kb"):
        pg.add_argument("--" + name.replace("_", "-"), dest=name, help=_KB_HELP if name == "kb" else None)
    pg.add_argument("--k", type=int)
    pg.add_argument("--participants", type=int)
    for sp in (pl, pf, pg):
        sp.add_argument("--strict", action="store_true",
                        help="ismeretlen tudásbázis-azonosító esetén hiba (alap: figyelmeztetés és jelölés)")
        sp.add_argument("--kb-db", help="a --kb ellenőrzéséhez használt tudásbázis (alap: tudasbazis/tudasbazis.sqlite)")
    ps = pjs.add_parser("status")
    ps.add_argument("dir")
    psh = pjs.add_parser("show", help="egy tétel minden mezője (pl. project show <mappa> finding 3)")
    psh.add_argument("dir")
    psh.add_argument("kind", choices=["finding", "decision", "checkpoint", "grade", "run"])
    psh.add_argument("id", type=int)
    pli = pjs.add_parser("list", help="tételek listája (pl. project list <mappa> findings --status open)")
    pli.add_argument("dir")
    pli.add_argument("kind", choices=["findings", "decisions", "checkpoints", "grades", "runs"])
    pli.add_argument("--status", choices=["open", "fixed", "wontfix", "invalid", "resolved",
                                          "active", "superseded", "reverted"],
                     help="megállapításnál: open | fixed | wontfix | invalid | resolved (= nem nyitott); "
                          "döntésnél: active | superseded | reverted")
    pli.add_argument("--severity", choices=["blocker", "major", "minor", "info"])
    pli.add_argument("--stage", help=_STAGE_HELP)
    pe = pjs.add_parser("export")
    pe.add_argument("dir")
    pe.add_argument("--out")
    pj.set_defaults(func=cmd_project)

    st = sub.add_parser("selftest", aliases=["onteszt"], help="tesztek futtatása")
    st.set_defaults(func=cmd_selftest)
    return p


def main(argv=None):
    _utf8_stdout()
    parser = build_parser()
    a = parser.parse_args(argv)
    a._argv = list(argv) if argv is not None else sys.argv[1:]   # a projektnapló parancssorához
    if not getattr(a, "func", None):
        parser.print_help()
        return 2
    if a.cmd in ("kb", "tudasbazis") and not a.kb_cmd:
        parser.parse_args([a.cmd, "-h"])
    if a.cmd in ("project", "projekt") and not a.p_cmd:
        parser.parse_args([a.cmd, "-h"])
    try:
        return a.func(a)
    except Exception as exc:  # felhasználóbarát hibaüzenet, kód 1
        if os.environ.get("METAELEMZES_DEBUG"):
            raise
        print("HIBA: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
