# -*- coding: utf-8 -*-
"""Parancssori felület.  Használat:  python ma.py <parancs> [opciók]   (súgó: -h)

Parancsok (magyar álnévvel):
  analyze  / elemez       teljes elemzés CSV-ből → riport, ábrák, JSON
  validate / validal      csak adatvalidálás (kilépési kód 1, ha hiba van)
  es       / hatasmeret   vizsgálatonkénti hatásméretek CSV-be
  convert  / konvertal    adatkinyerési konverziók (medián/IQR, SE, CI → SD, SMD-variancia, párosított összegek)
  power    / ero          prospektív erőelemzés (Hedges & Pigott 2001; dmetar::power.analysis)
  prisma   / prisma       PRISMA folyamatábra-számok ellenőrzése (prisma check; kilépési kód 1, ha hibás)
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
            "trimfill_estimator": a.trimfill_estimator, "filter_report": filter_report,
            "md_vtype": a.md_vtype, "glass_vtype": a.glass_vtype, "gen_smd_vtype": a.gen_smd_vtype,
            "pft_backtransform": a.pft_backtransform, "h_centre": a.ht_centre,
            "trimfill_trim_model": a.trimfill_trim_model, "egger_ci_dist": a.egger_ci_dist,
            "begg_method": a.begg_method, "begg_continuity": a.begg_continuity,
            "metareg_robust": a.robust, "outliers": a.outliers}
    if a.drop00 is not None:
        opts["drop00"] = a.drop00 == "yes"
    if a.robust and not a.moderators:
        print("FIGYELEM: a --robust csak meta-regresszióval (--moderators) értelmezett; most nincs hatása.")
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
    ol = (out.get("sensitivity") or {}).get("outliers")
    if ol is not None:
        print("Kiugró-szűrés: %d kiugró vizsgálat%s" % (ol.k_removed, (" (%s)" % ", ".join(ol.flagged)) if ol.flagged else ""))
    return 0


# ----------------------------------------------------------------- validate
def cmd_validate(a):
    from . import tableio, validate, effect_sizes
    rows, meta = tableio.read_table(a.data)
    copt = _compute_opts(a)
    f = validate.validate(rows, a.measure.upper(), meta, copt)
    # ugyanaz a kizárás, mint az elemzésben: a vizsgálat-szintű hibás sorok kimaradnak
    es = effect_sizes.compute(rows, a.measure.upper(), skip_labels=validate.blocking_reasons(f), **copt)
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


def _compute_opts(a):
    """A hatásméret-számítás konvenció-opciói (validate / es / analyze közös)."""
    return {"smd_vtype": a.smd_vtype, "md_vtype": a.md_vtype, "glass_vtype": a.glass_vtype,
            "gen_smd_vtype": a.gen_smd_vtype}


def cmd_es(a):
    from . import tableio, validate, effect_sizes as E
    from .distributions import norm_ppf
    import math
    rows, meta = tableio.read_table(a.data)
    copt = _compute_opts(a)
    findings = validate.validate(rows, a.measure.upper(), meta, dict(copt, cc=a.cc))
    es = E.compute(rows, a.measure.upper(), cc=a.cc, skip_labels=validate.blocking_reasons(findings), **copt)
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
    "smd-var": ("g", "n1", "n2"),
    "paired-sums": ("n", "sum_d", "sum_sq_dev"),
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
    elif k == "smd-var":
        import math
        v = C.smd_variance(a.g, a.n1, a.n2, a.vtype, a.j_method)
        res = {"vi": v, "sei": math.sqrt(v), "vtype": a.vtype, "j_method": a.j_method}
    elif k == "paired-sums":
        mean_d, sd_d = C.paired_from_sums(a.n, a.sum_d, a.sum_sq_dev)
        res = {"mdiff": mean_d, "sd_diff": sd_d, "n": a.n}
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
            if not any(res.get(s) for s in ("rule", "knowledge", "chunk")):
                print("Nincs találat: %r (kör: %s%s). Tipp: a teljes szöveg nagyrészt angol nyelvű — próbáld "
                      "angol kulcsszóval is (pl. 'heterogeneity')." % (
                          a.query, a.scope, (", forrás: %s" % a.source) if a.source else ""))
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


# --------------------------------------------------------------------- power
def cmd_power(a):
    from . import power as PW
    kw = {"effect": a.effect, "n1": a.n1, "n2": a.n2, "v": a.v, "sd": a.sd, "OR": a.odds_ratio,
          "measure": a.measure, "heterogeneity": a.heterogeneity, "tau2": a.tau2, "i2": a.i2,
          "alpha": a.alpha, "tails": a.tails, "factors": a.factors}
    if a.target_power is not None:
        res = PW.studies_needed(a.target_power, **kw)
        core = res.result
    else:
        if a.k is None:
            raise ValueError("power: --k (a várható vizsgálatszám) vagy --target-power megadása kötelező")
        res = core = PW.power_analysis(a.k, **kw)
    if a.json:
        _print_json(res)
        return 0
    print("Prospektív erőelemzés (%s)" % core.method)
    print("  feltételezett hatás: %s = %.4g%s" % (core.measure, core.effect,
                                                  (" (OR = %g → d = ln OR·√3/π)" % a.odds_ratio) if a.odds_ratio else ""))
    print("  vizsgálatonkénti variancia v = %.6g; heterogenitás: %s (tényező %.4g, τ² = %.4g)" % (
        core.v_study, core.heterogeneity, core.het_factor, core.tau2))
    print("  α = %g, %d-oldali; kritikus z = %.4f" % (core.alpha, core.tails, core.z_crit))
    if a.target_power is not None:
        if res.k is None:
            print("A %.0f%%-os célerő %d vizsgálattal sem érhető el (erő ott: %.4f)." % (
                100 * a.target_power, core.k, core.power))
        else:
            print("Szükséges vizsgálatszám a %.0f%%-os erőhöz: k = %d (erő: %.4f; λ = %.4f)" % (
                100 * a.target_power, res.k, res.power, core.lambda_))
    else:
        print("k = %d: λ = %.4f, az összesített becslés SE-je %.4g → erő = %.6g (%.2f%%)" % (
            core.k, core.lambda_, core.se_pooled, core.power, 100 * core.power))
    for w in core.warnings or []:
        print("FIGYELEM: %s" % w)
    print("Megjegyzés: a prospektív erő a tervezéshez való; elkészült metaanalízis utólagos ereje nem informatív.")
    return 0


# -------------------------------------------------------------------- prisma
_PRISMA_LETTER_FIELDS = (("A1", "identified_databases"), ("A2", "identified_registers"),
                         ("D1", "duplicates_removed"), ("D2", "automation_removed"), ("D3", "other_removed"),
                         ("B", "screened"), ("C", "excluded_screening"), ("E", "sought"),
                         ("F", "not_retrieved"), ("G", "assessed"), ("H", "excluded_eligibility"),
                         ("J", "included_reports"), ("I", "included_studies"))


def _parse_reasons(text):
    """'ok A:5; ok B:4' → {ok: n}."""
    out = {}
    for part in str(text).split(";"):
        if not part.strip():
            continue
        name, sep, num = part.rpartition(":")
        if not sep:
            name, sep, num = part.rpartition("=")
        if not sep or not name.strip():
            raise ValueError("--reasons: 'ok: szám; ok: szám' alakban add meg (kapott: %r)" % part.strip())
        out[name.strip()] = int(num.strip())
    return out


def cmd_prisma(a):
    from . import prisma
    flow = {}
    src = None
    if a.json_file:
        with open(a.json_file, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        flow = prisma.from_composer(data) if "dedup_removed" in data else dict(data)
        src = a.json_file
    if a.composer:
        with open(a.composer, encoding="utf-8-sig") as fh:
            flow.update(prisma.from_composer(json.load(fh)))
        src = a.composer
    if a.md:
        with open(a.md, encoding="utf-8-sig") as fh:
            flow.update(prisma.parse_markdown_table(fh.read()))
        src = a.md
    for letter, field in _PRISMA_LETTER_FIELDS:
        v = getattr(a, "L_" + letter)
        if v is not None:
            flow[field] = v
    for name in ("identified_other", "included_meta", "awaiting"):
        if getattr(a, name, None) is not None:
            flow[name] = getattr(a, name)
    if a.reasons:
        flow["excluded_eligibility_reasons"] = _parse_reasons(a.reasons)
    if not flow:
        raise ValueError("prisma check: adj meg bemenetet (--json, --composer, --md vagy --A1 … dobozértékek)")
    res = prisma.check_flow(flow, a.template)
    if a.out_format == "json":
        _print_json(res.to_dict())
    else:
        s = res.summary()
        print("PRISMA-folyamatábra ellenőrzése (%s%s): %s — %d hiba, %d figyelmeztetés, %d megjegyzés" % (
            res.template, (", " + src) if src else "", "RENDBEN" if res.ok else "HIBÁS", s["error"], s["warning"],
            s["info"]))
        for f in res.findings:
            print("[%s] %-7s %s%s" % (f["code"], f["severity"], f["title"], (": " + f["detail"]) if f["detail"] else ""))
            if f["severity"] == "error":
                print("        teendő: %s" % f["advice"])
        if res.derived:
            print("Levezetett értékek: %s" % ", ".join("%s = %s" % (k, v) for k, v in sorted(res.derived.items())
                                                        if not isinstance(v, dict)))
    return 0 if res.ok else 1


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


def _md_vtype(s):
    from .effect_sizes import md_vtype_name, EffectSizeError
    try:
        return md_vtype_name(s)
    except EffectSizeError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def _measure_epilog():
    """A mértékek és a szükséges CSV-oszlopok listája a súgóhoz (a motor táblázataiból)."""
    from .effect_sizes import ALL_MEASURES, REQUIRED_COLUMNS, MEASURE_LABELS, PAIRED_INPUTS
    lines = ["mértékek és szükséges oszlopok (a CSV fejlécében; magyar szinonimák is jók):"]
    for m in ALL_MEASURES:
        cols = ", ".join(REQUIRED_COLUMNS.get(m, ()))
        if m == "GEN":
            cols += " + vi vagy sei (vagy n1, n2 a --gen-smd-vtype-pal)"
        elif m in ("MC", "SMCC"):
            cols += " + átlagos változás és annak SD-je (lásd lent)"
        lines.append("  %-9s %s — %s" % (m, MEASURE_LABELS.get(m, m), cols))
    mean = " | ".join("+".join(c) for c in PAIRED_INPUTS["mean"])
    sd = " | ".join("+".join(c) for c in PAIRED_INPUTS["sd"])
    lines.append("  párosított (MC, SMCC): n mellett az átlagos változás (%s) és a változás SD-je (%s) — "
                 "az első teljes készlet nyer" % (mean, sd))
    lines.append("  SMD_GLASS: Glass Δ = (m1 − m2)/sd2, a 2. kar (kontroll) SD-jével")
    return "\n".join(lines)


def _add_es_conventions(sp):
    """A hatásméret-számítás konvenció-kapcsolói (analyze / validate / es)."""
    from .effect_sizes import SMD_VTYPES, GLASS_VTYPES
    sp.add_argument("--smd-vtype", default="LS", type=str.upper, choices=SMD_VTYPES,
                    help="SMD / Cohen d / SMCC variancia: LS (alap), LS2 (Borenstein), UB (torzítatlan), "
                         "METAN_COHEN (Stata metan/MetaXL: N/(n1n2) + d²/(2(N−2))), METAN_HEDGES "
                         "(N/(n1n2) + g²/(2(N−3.94)), közelítő J)")
    sp.add_argument("--md-vtype", default="unequal", type=_md_vtype, metavar="{unequal,pooled}",
                    help="MD variancia: unequal (alap; sd1²/n1 + sd2²/n2, metafor LS) vagy pooled (közös SD, "
                         "metafor HO)")
    sp.add_argument("--glass-vtype", default="METAN", type=str.upper, choices=GLASS_VTYPES,
                    help="SMD_GLASS variancia: METAN (alap; Stata metan/MetaXL), LS, LS2, UB, SMD1H (metafor)")
    sp.add_argument("--gen-smd-vtype", default=None, type=str.upper, choices=SMD_VTYPES,
                    help="GEN: ha egy sorban nincs vi/sei, de van n1 és n2, a yi-t közölt SMD-nek veszi és "
                         "ezzel a képlettel számol varianciát")


_STAGE_HELP = "szakasz: S00–S14, tartomány (pl. S01-S02, szakaszonként kibontva) vagy FINAL"
_KB_HELP = ("tudásbázis-azonosítók vesszővel (pl. V015,S08); csak a kb show/kb search által ismert ID — "
            "ismeretlen ID: figyelmeztetés és jelölés, --strict esetén hiba")


def build_parser():
    from .effect_sizes import ALL_MEASURES, PFT_N_METHODS, SMD_VTYPES
    from .models import TAU2_METHODS, CI_METHODS, PI_METHODS, H_CENTRES
    from .bias import EGGER_CI_DISTS, BEGG_METHODS
    from .power import HETEROGENEITY_FACTORS, POWER_MEASURES
    from .prisma import TEMPLATES
    p = argparse.ArgumentParser(prog="ma.py", description="Metaanalízis-motor (metaelemzes v%s)" % __version__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    epilog = _measure_epilog()
    an = sub.add_parser("analyze", aliases=["elemez"], help="teljes elemzés", epilog=epilog,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    an.add_argument("--data", required=True, help="CSV/TSV (; vagy , elválasztó, tizedesvessző is)")
    an.add_argument("--measure", required=True, type=str.upper, choices=ALL_MEASURES,
                    help="hatásméret (a szükséges oszlopokat lásd lent)")
    an.add_argument("--model", default="random", choices=["random", "fixed", "ivhet"])
    an.add_argument("--tau2", default=None, type=str.upper, choices=TAU2_METHODS,
                    help="τ²-becslő (alap: a modellé — random → REML, ivhet → DL, a Doi 2015 szerint; "
                         "ivhet-nél más becslő figyelmeztetéssel)")
    an.add_argument("--ci", default=None, choices=CI_METHODS, help="alap: random→hksj, fixed→z")
    an.add_argument("--pi", default="t_k-2", choices=PI_METHODS)
    an.add_argument("--level", type=_level, default=0.95, help="megbízhatósági szint: 0.95 vagy 95")
    _add_es_conventions(an)
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
    an.add_argument("--trimfill-trim-model", default=None, choices=["fixed", "random"],
                    help="a trim-and-fill vágási modellje (alap: az elsődleges modell, mint a metafor; fixed: "
                         "közös hatással vág, a kitöltött adatokat az elsődleges modellel összesíti — a "
                         "meta::trimfill alapértelmezése)")
    an.add_argument("--egger-ci-dist", default="t", choices=EGGER_CI_DISTS,
                    help="az Egger-tengelymetszet CI-je: t (alap; t(k−2), metafor) vagy norm (z, dmetar)")
    an.add_argument("--begg-method", default="auto", choices=BEGG_METHODS,
                    help="Begg-teszt: auto (alap; metafor: pontos Kendall-eloszlás, kötésnél normális), exact, "
                         "normal (Stata metabias)")
    an.add_argument("--begg-continuity", action="store_true",
                    help="Begg normális közelítés folytonossági korrekcióval (Stata metabias 2. sora)")
    an.add_argument("--ht-centre", "--ht-center", dest="ht_centre", default="truncated", choices=H_CENTRES,
                    help="a Higgins–Thompson H/I² CI középpontja: truncated (alap; ln max(1, H), R meta) vagy "
                         "untruncated (½·ln(Q/df), Borenstein 2009)")
    an.add_argument("--pft-backtransform", default="harmonic", choices=PFT_N_METHODS,
                    help="PFT visszatranszformálás n-je: harmonic (alap; metafor/meta) vagy variance (MetaXL: "
                         "m = 1/Var(t) az adott összesített becslésből)")
    an.add_argument("--robust", action="store_true",
                    help="meta-regresszió: robusztus (HC1 szendvics) SE-k, t(k−p) és robusztus F is (Stata "
                         "regress, vce(robust)); --moderators kell hozzá")
    an.add_argument("--outliers", action="store_true",
                    help="kiugró vizsgálatok szűrése (dmetar::find.outliers: a vizsgálat CI-je teljesen az "
                         "összesített CI-n kívül) és újraillesztés nélkülük (érzékenységi elemzés)")
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

    va = sub.add_parser("validate", aliases=["validal"], help="adatvalidálás", epilog=epilog,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    va.add_argument("--data", required=True)
    va.add_argument("--measure", required=True, type=str.upper, choices=ALL_MEASURES)
    _add_es_conventions(va)
    va.add_argument("--json", action="store_true")
    va.set_defaults(func=cmd_validate)

    e = sub.add_parser("es", aliases=["hatasmeret"], help="hatásméretek CSV-be", epilog=epilog,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    e.add_argument("--data", required=True)
    e.add_argument("--measure", required=True, type=str.upper, choices=ALL_MEASURES)
    _add_es_conventions(e)
    e.add_argument("--cc", type=float, default=0.5)
    e.add_argument("--out")
    e.set_defaults(func=cmd_es)

    c = sub.add_parser("convert", aliases=["konvertal"], help="konverziók")
    c.add_argument("kind", choices=["median", "se", "ci", "combine", "change", "se-from-ci", "smd-var", "paired-sums"],
                   help="smd-var: közölt SMD (--g) + karlétszámok → variancia (--vtype); paired-sums: n, Σd, "
                        "Σ(d − d̄)² → átlagos változás és SD")
    for name in ("n", "median", "q1", "q3", "min", "max", "se", "lower", "upper", "n1", "m1", "sd1",
                 "n2", "m2", "sd2", "sd_baseline", "sd_final", "corr", "g", "sum_d", "sum_sq_dev"):
        c.add_argument("--" + name.replace("_", "-"), dest=name, type=float)
    c.add_argument("--vtype", default="LS", type=str.upper, choices=SMD_VTYPES, help="smd-var: a variancia-képlet")
    c.add_argument("--j-method", default="exact", choices=["exact", "approx"], help="smd-var: a J korrekció")
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

    pw = sub.add_parser("power", aliases=["ero"], help="prospektív erőelemzés (Hedges & Pigott 2001)",
                        description="Prospektív erő egy TERVEZETT metaanalízis összesített hatásának z-tesztjéhez "
                                    "(dmetar::power.analysis konvenció). Példa: power --k 18 --effect 0.7 --n1 15 "
                                    "--n2 15 --heterogeneity moderate; vagy --target-power 0.8 a szükséges k-hoz.")
    pw.add_argument("--k", type=int, help="a várható vizsgálatszám")
    pw.add_argument("--effect", type=float, help="feltételezett hatás (SMD-nél d; MD-nél nyers különbség; GEN-nél "
                                                 "az elemzési skálán)")
    pw.add_argument("--or", dest="odds_ratio", type=float, help="feltételezett OR (d = ln OR·√3/π; az --effect helyett)")
    pw.add_argument("--n1", type=float, help="vizsgálatonkénti létszám az 1. karban")
    pw.add_argument("--n2", type=float, help="vizsgálatonkénti létszám a 2. karban")
    pw.add_argument("--v", type=float, help="vizsgálatonkénti mintavételi variancia (felülírja a képletet)")
    pw.add_argument("--sd", type=float, help="közös SD (nyers MD-hez)")
    pw.add_argument("--measure", default="SMD", type=str.upper, choices=POWER_MEASURES)
    pw.add_argument("--heterogeneity", default="fixed", choices=["fixed", "low", "moderate", "high"],
                    help="heterogenitás szóban (dmetar: 1 / 1.33 / 1.67 / 2 tényező)")
    pw.add_argument("--tau2", type=float, help="abszolút τ² (elsőbbséget élvez)")
    pw.add_argument("--i2", type=float, help="I² %%-ban (τ² = v·I²/(100 − I²))")
    pw.add_argument("--alpha", type=float, default=0.05)
    pw.add_argument("--tails", type=int, default=2, choices=[1, 2])
    pw.add_argument("--factors", default="dmetar", choices=sorted(HETEROGENEITY_FACTORS),
                    help="a szóbeli heterogenitás tényezői: dmetar (alap) vagy hedges_pigott (4/3, 5/3, 2)")
    pw.add_argument("--target-power", type=float, help="célerő (pl. 0.8): a szükséges legkisebb k kiszámítása")
    pw.add_argument("--json", action="store_true")
    pw.set_defaults(func=cmd_power)

    pz = sub.add_parser("prisma", help="PRISMA folyamatábra-számok ellenőrzése")
    pzs = pz.add_subparsers(dest="prisma_cmd")
    pzc = pzs.add_parser("check", help="a dobozszámok konzisztenciája (kilépési kód 1, ha hibás)",
                         description="Bemenet: --json (flow-szótár), --composer (prisma-flow.json), --md "
                                     "(02_szures/prisma_folyamat.md) és/vagy a dobozok betűjeleivel (--A1 … --I); "
                                     "a betűs értékek felülírják a fájlból olvasottakat.")
    pzc.add_argument("--json", dest="json_file", help="flow JSON (kanonikus vagy 2009-es mezőnevek)")
    pzc.add_argument("--composer", help="a composer plugin prisma-flow.json kimenete")
    pzc.add_argument("--md", help="a projekt prisma_folyamat.md táblázata")
    for letter, field in _PRISMA_LETTER_FIELDS:
        pzc.add_argument("--" + letter, dest="L_" + letter, type=int, metavar="N", help=field)
    pzc.add_argument("--other", dest="identified_other", type=int, metavar="N",
                     help="identified_other (egyéb forrásból, a fő ágba olvasztva)")
    pzc.add_argument("--meta", dest="included_meta", type=int, metavar="N", help="included_meta (metaanalízisben)")
    pzc.add_argument("--awaiting", type=int, metavar="N", help="elbírálásra váró")
    pzc.add_argument("--reasons", help="a H kizárási okai: 'ok A: 5; ok B: 4'")
    pzc.add_argument("--template", default=None, type=lambda x: x.upper().replace("_", "").replace(" ", ""),
                     choices=TEMPLATES, help="PRISMA2020 | PRISMA2009 (alap: felismerés a mezőnevekből)")
    pzc.add_argument("--out-format", default="text", choices=["text", "json"])
    pz.set_defaults(func=cmd_prisma)

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
    if a.cmd == "prisma" and not a.prisma_cmd:
        parser.parse_args([a.cmd, "-h"])
    try:
        return a.func(a)
    except BrokenPipeError:
        # a kimenet olvasója (pl. `| head`) bezárta a csövet: nem programhiba, nincs "HIBA:" üzenet
        # (a Python-dokumentáció ajánlása: a stdout a /dev/null-ra, hogy a kilépéskori flush se hibázzon)
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return 1
    except Exception as exc:  # felhasználóbarát hibaüzenet, kód 1
        if os.environ.get("METAELEMZES_DEBUG"):
            raise
        print("HIBA: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
