# -*- coding: utf-8 -*-
"""A teljes elemzési folyamat: validálás → hatásméretek → modellek → heterogenitás →
alcsoport / meta-regresszió → torzítás → érzékenység → ábrák → riport.

A CLI (cli.py) és az ágensek ezt hívják; minden eredmény JSON-ba írható.
"""
import json
import math
import os

from . import __version__
from . import effect_sizes as E
from . import models as M
from . import moderators as MO
from . import bias as B
from . import sensitivity as SE
from . import validate as V
from . import plots as P

DEFAULTS = {
    "measure": "SMD", "model": "random", "tau2": "REML", "ci": None, "pi": "t_k-2",
    "level": 0.95, "smd_vtype": "LS", "j_method": "exact", "cc": 0.5, "cc_to": "only0",
    "drop00": None, "mh": False, "peto": False, "rd_var": "sato", "subgroup": None,
    "common_tau2": False, "moderators": [], "metareg_test": "knha", "cumulative": None,
    "title": None, "left_label": None, "right_label": None, "label_col": "study",
    "bias_min_k": 10, "trimfill_estimator": "L0",
}


def to_jsonable(obj):
    if isinstance(obj, M.MetaResult):
        return {k: to_jsonable(v) for k, v in obj.to_dict().items()}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def _display(measure, est, lo, hi, nh=None):
    return [E.back_transform(measure, v, nh) if v is not None else None for v in (est, lo, hi)]


def _level_str(v):
    """Alcsoport-szint szövegként: a számként beolvasott egész érték egészként ('2', nem '2.0')."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip() if isinstance(v, str) else str(v)


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and math.isnan(v))


def _double_zero(r):
    try:
        e1, n1, e2, n2 = (float(r[c]) for c in ("e1", "n1", "e2", "n2"))
    except (KeyError, TypeError, ValueError):
        return False
    return (e1 == 0 and e2 == 0) or (e1 == n1 and e2 == n2)


def resolve_columns(opt, meta, rows):
    """A --subgroup / --moderators / --cumulative oszlopnevek feloldása (eredeti fejléc,
    kanonikus név vagy szinonima; tableio.resolve_column). Ismeretlen oszlop → ValueError
    az opció nevével és az elérhető oszlopokkal — még mielőtt bármi kimenet készülne."""
    from .tableio import resolve_column

    def res(name, flag):
        try:
            return resolve_column(name, meta, rows)
        except ValueError as exc:
            raise ValueError("%s: %s" % (flag, exc))

    cols = {}
    if opt.get("subgroup"):
        cols["subgroup"] = res(opt["subgroup"], "--subgroup")
    if opt.get("moderators"):
        cols["moderators"] = [res(m, "--moderators") for m in opt["moderators"]]
    if opt.get("cumulative"):
        cols["cumulative"] = res(opt["cumulative"], "--cumulative")
    return cols


def column_labels(cols, meta):
    """A feloldott oszlopkulcsok megjelenítési neve: az eredeti CSV-fejléc (pl. 'year' → 'év'),
    hogy a riport a felhasználó oszlopnevét mutassa, ne a belső kanonikus kulcsot."""
    mapping = (meta or {}).get("mapping") or {}

    def lab(key):
        for orig, k in mapping.items():
            if k == key:
                return orig.strip()
        return key

    out = {}
    for name, val in cols.items():
        out[name] = [lab(v) for v in val] if isinstance(val, list) else lab(val)
    return out


def _num(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _totals(measure, es, out):
    """Résztvevők és események összesítése az ELEMZETT vizsgálatokra (GRADE SoF-hoz)."""
    t = {"k": len(es), "scope": "az elemzett vizsgálatok összesen (a kizárt sorok nélkül)"}
    ni = list(es.ni)
    known = [n for n in ni if n is not None]
    t["participants"] = _num(sum(known)) if known and len(known) == len(ni) else None
    if known and len(known) < len(ni):
        t["participants_partial"] = _num(sum(known))
        t["participants_known_k"] = len(known)
    rows = es.rows

    def colsum(c):
        vals = [r.get(c) for r in rows]
        return _num(sum(vals)) if vals and all(isinstance(v, (int, float)) for v in vals) else None

    if measure in E.CONTINUOUS or measure in E.BINARY:
        t["n1"], t["n2"] = colsum("n1"), colsum("n2")
    if measure in E.BINARY:
        t["events1"], t["events2"] = colsum("e1"), colsum("e2")
        if t["n1"] and t["events1"] is not None:
            t["risk1"] = t["events1"] / float(t["n1"])
        if t["n2"] and t["events2"] is not None:
            t["control_risk"] = t["events2"] / float(t["n2"])
        acr = t.get("control_risk")
        bt = (out.get("back_transformed") or {}).get("estimate_ci")
        if acr is not None and bt and None not in bt:
            def risk(x):
                if measure == "RR":
                    r = acr * x
                elif measure == "OR":
                    r = acr * x / (1 - acr + acr * x)
                else:
                    r = acr + x
                return min(1.0, max(0.0, r))
            t["absolute_per_1000"] = {
                "assumed_control_risk_per_1000": 1000 * acr,
                # RD: maga a kockázatkülönbség (alapkockázattól független); RR/OR: az alapkockázatra vetítve
                "difference": [1000 * (x if measure == "RD" else risk(x) - acr) for x in bt],
                "intervention_risk": [1000 * risk(x) for x in bt],
                "note": ("Illusztratív: a feltételezett kontrollkockázat a kontrollkarok (2. kar) nyers "
                         "összesített kockázata (Σe2/Σn2); a GRADE SoF-hoz a klinikailag releváns "
                         "alapkockázatot használd, ha van."),
            }
    if measure in E.PROPORTION:
        t["events"] = colsum("x")
    return t


def run(rows, options=None, meta=None):
    opt = dict(DEFAULTS)
    opt.update({k: v for k, v in (options or {}).items() if v is not None})
    measure = opt["measure"].upper()
    opt["measure"] = measure
    cols = resolve_columns(opt, meta, rows)
    out = {"engine": {"name": "metaelemzes", "version": __version__}, "options": opt,
           "input": {k: v for k, v in (meta or {}).items() if k != "mapping"}, "warnings": [],
           "columns": cols, "column_labels": column_labels(cols, meta)}
    # 0) szűrők (--exclude / --include): mi maradt ki, és hány sor maradt
    flt = dict(out["input"].get("filters") or {})
    frep = opt.get("filter_report") or []
    if frep or flt.get("exclude") or flt.get("include"):
        flt["report"] = list(frep)
        flt["n_rows_read"] = out["input"].get("n_rows")
        flt["n_rows_kept"] = len(rows)
        out["input"]["filters"] = flt
    # 1) validálás; a vizsgálat-szintű 'error' tételek sorai TÉNYLEG kimaradnak
    findings = V.validate(rows, measure, meta, opt)
    blocking = V.blocking_reasons(findings) if hasattr(V, "blocking_reasons") else V.blocking_labels(findings)
    # 2) hatásméretek
    es = E.compute(rows, measure, label_col=opt["label_col"], smd_vtype=opt["smd_vtype"],
                   j_method=opt["j_method"], cc=opt["cc"], cc_to=opt["cc_to"], drop00=opt["drop00"],
                   skip_labels=blocking)
    findings += V.check_effect_sizes(es)
    out["validation"] = {"summary": V.summarize(findings), "findings": findings,
                         "blocked_studies": sorted(blocking)}
    out["kb_refs"] = sorted(set(f["code"] for f in findings))
    k = len(es)
    nh = E.harmonic_mean(es.ni) if measure == "PFT" else None
    out["effect_sizes"] = {"measure": measure, "label": E.MEASURE_LABELS.get(measure, measure),
                           "k": k, "labels": list(es.labels), "ni": list(es.ni),
                           "row_index": list(getattr(es, "row_index", []) or []),
                           "corrected": [lab for lab, note in zip(es.labels, es.notes)
                                         if note and "korrekci" in note],
                           "double_zero_included": [lab for lab, r in zip(es.labels, es.rows)
                                                    if measure in E.BINARY and _double_zero(r)],
                           "excluded": [{"study": a, "reason": b} for a, b in es.excluded],
                           "warnings": es.warnings}
    if k == 0:
        out["warnings"].append("Nincs elemezhető vizsgálat.")
        return out, es
    # 3) modellek
    req = opt["model"]
    # --ci a közös hatású ELSŐDLEGES modellre is vonatkozik (z/t; HKSJ: metafor-szerűen, figyelmeztetéssel);
    # a véletlen hatású elemzés mellé illesztett közös hatású érzékenységi modell Wald (z), mint a metaforban
    fixed_ci = (opt["ci"] or "z") if req == "fixed" else "z"
    fixed = M.meta_analysis(es.yi, es.vi, "fixed", ci_method=fixed_ci, level=opt["level"], labels=es.labels)
    if req == "fixed" and fixed_ci in ("hksj", "hksj_adhoc") and k >= 2:
        out["warnings"].append("A Knapp–Hartung (HKSJ) módszer nem közös hatású modellhez készült (a metafor "
                               "is figyelmeztet); értelmezd óvatosan, vagy használd a z / t CI-t.")
    random_ = M.meta_analysis(es.yi, es.vi, "random", opt["tau2"], opt["ci"] or "hksj", opt["level"],
                              opt["pi"], es.labels) if k >= 2 else None
    primary = random_ if (req == "random" and random_ is not None) else fixed
    out["fixed"] = fixed
    out["random"] = random_
    out["primary_model"] = "random" if primary is random_ else "fixed"
    if req == "ivhet" and k >= 2:
        out["ivhet"] = M.meta_analysis(es.yi, es.vi, "ivhet", level=opt["level"], labels=es.labels)
        primary = out["ivhet"]
        out["primary_model"] = "ivhet"
    if req in ("random", "ivhet") and k < 2:
        out["model_fallback"] = {"requested": req, "used": "fixed", "reason": "k = 1"}
        out["warnings"].append("k = 1: %s modell nem illeszthető, összesítés nem történt; az eredmény az "
                               "egyetlen vizsgálat becslése (közös hatású modellként, z-alapú CI)."
                               % ("véletlen hatású" if req == "random" else "IVhet"))
    out["primary"] = primary
    if measure in E.BINARY and opt["mh"]:
        cols4 = [[r.get(c) for r in es.rows] for c in ("e1", "n1", "e2", "n2")]
        try:
            out["mh"] = M.mantel_haenszel(*cols4, measure=measure, level=opt["level"], labels=es.labels,
                                          cc=opt["cc"], rd_var=opt["rd_var"])
        except (M.ModelError, ArithmeticError, ValueError) as exc:
            out["warnings"].append("MH: %s" % exc)
    if measure == "OR" and opt["peto"]:
        cols4 = [[r.get(c) for r in es.rows] for c in ("e1", "n1", "e2", "n2")]
        try:
            out["peto"] = M.peto(*cols4, level=opt["level"], labels=es.labels)
        except (M.ModelError, ArithmeticError, ValueError) as exc:
            out["warnings"].append("Peto: %s" % exc)
    out["back_transformed"] = {
        "estimate_ci": _display(measure, primary.estimate, primary.ci_lower, primary.ci_upper, nh),
        "pi": _display(measure, primary.pi_lower, primary.pi_lower, primary.pi_upper, nh)[1:]
        if primary.pi_lower is not None else None,
        "scale_note": _scale_note(measure),
        "n_harmonic": nh,
        "level": opt["level"],
    }
    out["totals"] = _totals(measure, es, out)
    # a sorrendi/érzékenységi elemzések ugyanazzal a CI-módszerrel, mint az elsődleges modell
    ci_eff = primary.ci_method if out["primary_model"] in ("random", "fixed") else None
    # 4) alcsoport / meta-regresszió
    if opt["subgroup"]:
        col = cols["subgroup"]
        groups = [r.get(col) for r in es.rows]
        missing = [lab for lab, g in zip(es.labels, groups) if _blank(g)]
        if missing:
            out["warnings"].append("Az alcsoport-oszlopban (%s) hiányzó érték van (%s); az alcsoport-elemzés "
                                   "kimaradt." % (opt["subgroup"], ", ".join(missing)))
        else:
            grp = [_level_str(g) for g in groups]
            try:
                sg = MO.subgroup_analysis(es.yi, es.vi, grp, es.labels, opt["model"], opt["tau2"], opt["ci"],
                                          opt["level"], opt["common_tau2"], opt["pi"])
            except (M.ModelError, ArithmeticError) as exc:
                sg = None
                out["warnings"].append("Alcsoport-elemzés: %s" % exc)
            rix = list(getattr(es, "row_index", []) or [])
            for g in (sg.groups if sg is not None else []):
                # pozíció szerint (nem címke szerint): ismétlődő címkéjű sorok (több kar) is jók
                g.indices = [i for i, x in enumerate(grp) if x == g.group]
                if len(rix) == k:
                    g.row_index = [rix[i] for i in g.indices]
                g.n_harmonic = E.harmonic_mean([es.ni[i] for i in g.indices]) if measure == "PFT" else None
                if measure == "PFT" and g.n_harmonic is None:
                    g.n_harmonic = nh
                g.display = _display(measure, g.estimate, g.ci_lower, g.ci_upper, g.n_harmonic)
            if sg is not None:
                out["subgroups"] = sg
    if opt["moderators"]:
        # az együtthatók neve az eredeti oszlopnév (nem a belső kanonikus kulcs, pl. 'év', nem 'year')
        mods = list(zip(cols["moderators"], out["column_labels"]["moderators"]))
        # az elemzett vizsgálatokban állandó moderátor (pl. szűrés után egyetlen szint) nem becsülhető:
        # kategoriálisnál csendben eltűnne (csak tengelymetszet), numerikusnál szinguláris lenne
        const = [lab for key, lab in mods if len(set(_level_str(r.get(key)) for r in es.rows)) < 2]
        if const:
            out["warnings"].append("Meta-regresszió: a(z) %s moderátor az elemzett vizsgálatokban egyetlen értéket "
                                   "vesz fel (nincs becsülhető hatás), ezért kimaradt a modellből." % ", ".join(const))
            mods = [(key, lab) for key, lab in mods if lab not in const]
            out["metaregression_dropped"] = const
    if opt["moderators"] and mods:
        try:
            view = [{lab: r.get(key) for key, lab in mods} for r in es.rows]
            x, names = MO.design_matrix(view, [lab for _, lab in mods])
            out["metaregression"] = MO.meta_regression(
                es.yi, es.vi, x, names,
                opt["tau2"] if opt["tau2"] in MO.MR_TAU2_METHODS else "REML",
                opt["metareg_test"], opt["level"])
        except (M.ModelError, ArithmeticError) as exc:
            out["warnings"].append("Meta-regresszió: %s" % exc)
    # 5) kis-vizsgálat hatások
    bias = {"performed": k >= 3, "k": k, "min_k_recommended": opt["bias_min_k"]}
    if k >= 3:
        try:
            bias["egger"] = B.egger_test(es.yi, es.vi)
            bias["begg"] = B.begg_test(es.yi, es.vi)
            # a korrigált modell az elsődleges modell CI-módszerével és szintjével (k0 = 0 esetén
            # így pontosan az elsődleges eredmény, mint a metafor trimfill-ben)
            bias["trimfill"] = B.trim_and_fill(es.yi, es.vi, es.labels, opt["model"], opt["tau2"],
                                               opt["trimfill_estimator"], ci_method=ci_eff or "z",
                                               level=opt["level"])
        except (M.ModelError, ArithmeticError) as exc:
            out["warnings"].append("Torzítás-elemzés: %s" % exc)
    if k < opt["bias_min_k"]:
        bias["note"] = ("k = %d < %d: a funnel-aszimmetria tesztek eredménye csak tájékoztató, "
                        "nem értelmezhető (Sterne et al. 2011)." % (k, opt["bias_min_k"]))
    out["bias"] = bias
    # 6) érzékenység
    sens = {}
    if k >= 3:
        sens["leave_one_out"] = SE.leave_one_out(es.yi, es.vi, es.labels, opt["model"], opt["tau2"],
                                                 ci_eff, opt["level"])
        sens["influence"] = SE.influence(es.yi, es.vi, es.labels, opt["model"], opt["tau2"], opt["level"])
    if opt["cumulative"]:
        col = cols["cumulative"]
        keys = [r.get(col) for r in es.rows]
        miss = [lab for lab, kk in zip(es.labels, keys) if _blank(kk)]
        if len(miss) == k:
            out["warnings"].append("Kumulatív elemzés kihagyva: a(z) '%s' oszlop minden elemzett vizsgálatnál "
                                   "üres." % opt["cumulative"])
        else:
            if miss:
                out["warnings"].append("Kumulatív elemzés: %d vizsgálatnál hiányzik a rendezőkulcs (%s); ezek a "
                                       "sor végére kerültek (mint a metafor cumul()-ban)." % (len(miss), ", ".join(miss)))
            sens["cumulative"] = SE.cumulative(es.yi, es.vi, es.labels, keys, opt["model"], opt["tau2"],
                                               ci_eff, opt["level"])
    out["sensitivity"] = sens
    out["warnings"] += [w for w in (primary.warnings or []) if w not in out["warnings"]]
    return out, es


def _scale_note(measure):
    if measure in ("OR", "RR", "ROM"):
        return "Az elemzés log-skálán történt; a közölt értékek exponenciálisan visszatranszformáltak."
    if measure == "PLO":
        return "Logit-skálán elemezve; visszatranszformálva arányra."
    if measure == "PFT":
        return "Freeman–Tukey skálán elemezve; visszatranszformálás Miller (1978) képletével, harmonikus átlag n-nel."
    if measure == "ZCOR":
        return "Fisher z-skálán elemezve; visszatranszformálva r-re."
    if measure == "PAS":
        return "Arcsin-skálán elemezve; visszatranszformálva arányra."
    if measure == "PLN":
        return "Log-skálán elemezve; visszatranszformálva arányra."
    return None


def make_plots(out, es, opt=None):
    """Forest + funnel SVG és a plot_data JSON (a figure-forge-hoz)."""
    opt = dict(DEFAULTS, **(opt or out.get("options", {})))
    measure = out["effect_sizes"]["measure"]
    primary = out["primary"]
    nh = E.harmonic_mean(es.ni) if measure == "PFT" else None
    lv = round(opt["level"] * 100)
    summaries = []
    for key, lab in (("ivhet", "IVhet modell (Doi 2015)"), ("random", "Véletlen hatású modell"),
                     ("fixed", "Közös (fix) hatású modell")):
        r = out.get(key)
        if r is None:
            continue
        if key == "random":
            lab = "%s (%s, %s)" % (lab, r.tau2_method, r.ci_method.upper())
        elif key == "fixed" and r.ci_method != "z":
            lab = "%s (%s)" % (lab, r.ci_method.upper())
        sm = {"label": lab, "estimate": r.estimate, "ci_lower": r.ci_lower, "ci_upper": r.ci_upper,
              "display": _display(measure, r.estimate, r.ci_lower, r.ci_upper, nh),
              "pi_lower": r.pi_lower, "pi_upper": r.pi_upper}
        if r.pi_lower is not None:
            sm["pi_display"] = _display(measure, r.pi_lower, r.pi_lower, r.pi_upper, nh)[1:]
        summaries.append(sm)
    if out["primary_model"] == "fixed":
        summaries.reverse()
    weights = primary.weights_pct
    sections = None
    if out.get("subgroups"):
        sections = []
        sg = out["subgroups"]
        col = (out.get("columns") or {}).get("subgroup") or opt.get("subgroup")
        grp = [_level_str(r.get(col)) for r in es.rows] if col else None
        for g in sg.groups:
            idx = g.get("indices")
            if idx is None:      # régi eredmény-objektum: pozíció a csoportváltozóból (nem címkéből)
                idx = [i for i, x in enumerate(grp or []) if x == str(g.group)]
            ng = g.get("n_harmonic") or nh
            sections.append({"title": str(g.group), "indices": list(idx),
                             "row_index": list(g.get("row_index") or []), "summary": {
                "label": "Alcsoport összesen (k = %d)" % g.k, "estimate": g.estimate,
                "ci_lower": g.ci_lower, "ci_upper": g.ci_upper,
                "display": g.get("display") or _display(measure, g.estimate, g.ci_lower, g.ci_upper, ng),
                "pi_lower": None, "pi_upper": None,
                "note": ("τ² = %.3g; I² = %.0f%%" % (g.tau2, g.I2)) if g.k > 1 else None}})
    per_study_n = es.ni if measure == "PFT" else None
    data = P.forest_data(measure, es.labels, es.yi, es.vi, weights, summaries, opt["level"],
                         per_study_n, sections)
    footer = ["Heterogenitás: Q = %.2f (df = %d, p %s); I² (Q-alapú) = %.0f%%; τ² = %s" % (
        primary.Q, primary.Q_df, _p(primary.p_Q), primary.I2,
        ("%.3g" % primary.tau2) if out["primary_model"] in ("random", "ivhet") else "–")]
    if out.get("subgroups"):
        sg = out["subgroups"]
        if sg.Q_between is not None:
            footer.append("Alcsoport-különbség: Q_b = %.2f (df = %d, p %s)" % (sg.Q_between, sg.df_between,
                                                                             _p(sg.p_between)))
    if primary.pi_lower is not None:
        footer.append("Piros vonal: %d%%-os predikciós intervallum (%s)." % (lv, primary.pi_method))
    if measure in E.PROPORTION:
        footer.append("Egycsoportos arány: nincs nullhatás-vonal; a tengelyfeliratok arányok (az elemzési "
                      "skálán elhelyezve).")
    elif measure == "ZCOR":
        footer.append("A tengelyfeliratok r-értékek (Fisher z-skálán elhelyezve); szaggatott vonal: r = 0.")
    axis_t = P.axis_label(measure, _short(measure))
    svg_forest = P.forest_svg(data, title=opt.get("title"), left_label=opt.get("left_label"),
                              right_label=opt.get("right_label"),
                              effect_label="%s [%d%% CI]" % (_short(measure), lv),
                              footer=footer, axis_title=axis_t)
    tf = out.get("bias", {}).get("trimfill")
    contour = measure not in E.PROPORTION
    svg_funnel = P.funnel_svg(es.yi, es.vi, out["fixed"].estimate, measure,
                              title="Funnel plot (kontúr-javított)" if contour else "Funnel plot",
                              filled_yi=tf.filled_yi if tf else None, filled_vi=tf.filled_vi if tf else None,
                              contour=contour, n_harmonic=nh, axis_title=axis_t)
    data["axis_title"] = axis_t
    data["null_value"] = P.default_null(measure)
    data["heterogeneity"] = {"Q": primary.Q, "df": primary.Q_df, "p": primary.p_Q, "I2": primary.I2,
                             "I2_from_tau2": primary.heterogeneity.get("I2_from_tau2"),
                             "tau2": primary.tau2}
    data["funnel"] = {"yi": es.yi, "sei": [math.sqrt(v) for v in es.vi], "center": out["fixed"].estimate,
                      "contour": contour, "contour_center": P.default_null(measure),
                      "filled_yi": tf.filled_yi if tf else [],
                      "filled_sei": [math.sqrt(v) for v in tf.filled_vi] if tf else []}
    return svg_forest, svg_funnel, data


def _p(p):
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "= –"
    return "< 0.001" if p < 0.001 else "= %.3f" % p


def _short(measure):
    return {"SMD": "Hedges g", "COHEN_D": "Cohen d", "MD": "MD", "OR": "OR", "RR": "RR", "RD": "RD",
            "ROM": "Ratio of means", "PR": "Arány", "PLN": "Arány", "PLO": "Arány", "PAS": "Arány",
            "PFT": "Arány", "COR": "r", "ZCOR": "r", "GEN": "Hatás"}.get(measure, measure)


def write_outputs(out, es, outdir, report_md=None, plots=True):
    os.makedirs(outdir, exist_ok=True)
    paths = {}
    if plots and out.get("primary") is not None:
        f_svg, fu_svg, data = make_plots(out, es)
        for name, content in (("forest.svg", f_svg), ("funnel.svg", fu_svg)):
            p = os.path.join(outdir, name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(content)
            paths[name] = p
        p = os.path.join(outdir, "plot_data.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(to_jsonable(data), fh, ensure_ascii=False, indent=1)
        paths["plot_data.json"] = p
    p = os.path.join(outdir, "results.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(to_jsonable(out), fh, ensure_ascii=False, indent=1)
    paths["results.json"] = p
    # vizsgálatonkénti hatásméretek
    from .tableio import write_csv
    from .distributions import norm_ppf
    z = norm_ppf(0.5 + out["options"]["level"] / 2)
    rows = []
    w = out["primary"].weights_pct if out.get("primary") is not None else [None] * len(es)
    for i, (lab, y, v) in enumerate(zip(es.labels, es.yi, es.vi)):
        se = math.sqrt(v)
        n = es.ni[i] if i < len(es.ni) else None
        rows.append([lab, y, v, se, y - z * se, y + z * se, w[i] if w else None, _num(n), es.notes[i]])
    p = os.path.join(outdir, "effect_sizes.csv")
    write_csv(p, ["study", "yi", "vi", "sei", "ci_lower", "ci_upper", "weight_pct", "n", "note"], rows)
    paths["effect_sizes.csv"] = p
    if report_md is not None:
        p = os.path.join(outdir, "report.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(report_md)
        paths["report.md"] = p
    return paths
