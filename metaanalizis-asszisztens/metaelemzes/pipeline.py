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


def run(rows, options=None, meta=None):
    opt = dict(DEFAULTS)
    opt.update({k: v for k, v in (options or {}).items() if v is not None})
    measure = opt["measure"].upper()
    opt["measure"] = measure
    out = {"engine": {"name": "metaelemzes", "version": __version__}, "options": opt,
           "input": {k: v for k, v in (meta or {}).items() if k != "mapping"}, "warnings": []}
    # 1) validálás
    findings = V.validate(rows, measure, meta)
    # 2) hatásméretek
    es = E.compute(rows, measure, label_col=opt["label_col"], smd_vtype=opt["smd_vtype"],
                   j_method=opt["j_method"], cc=opt["cc"], cc_to=opt["cc_to"], drop00=opt["drop00"])
    findings += V.check_effect_sizes(es)
    out["validation"] = {"summary": V.summarize(findings), "findings": findings}
    out["kb_refs"] = sorted(set(f["code"] for f in findings))
    k = len(es)
    nh = E.harmonic_mean(es.ni) if measure == "PFT" else None
    out["effect_sizes"] = {"measure": measure, "label": E.MEASURE_LABELS.get(measure, measure),
                           "k": k, "excluded": [{"study": a, "reason": b} for a, b in es.excluded],
                           "warnings": es.warnings}
    if k == 0:
        out["warnings"].append("Nincs elemezhető vizsgálat.")
        return out, es
    # 3) modellek
    fixed = M.meta_analysis(es.yi, es.vi, "fixed", level=opt["level"], labels=es.labels)
    random_ = M.meta_analysis(es.yi, es.vi, "random", opt["tau2"], opt["ci"] or "hksj", opt["level"],
                              opt["pi"], es.labels) if k >= 2 else None
    primary = random_ if (opt["model"] == "random" and random_ is not None) else fixed
    out["fixed"] = fixed
    out["random"] = random_
    out["primary_model"] = "random" if primary is random_ else "fixed"
    if opt["model"] == "ivhet" and k >= 2:
        out["ivhet"] = M.meta_analysis(es.yi, es.vi, "ivhet", level=opt["level"], labels=es.labels)
        primary = out["ivhet"]
        out["primary_model"] = "ivhet"
    out["primary"] = primary
    if measure in E.BINARY and opt["mh"]:
        cols = [[r.get(c) for r in es.rows] for c in ("e1", "n1", "e2", "n2")]
        try:
            out["mh"] = M.mantel_haenszel(*cols, measure=measure, level=opt["level"], labels=es.labels,
                                          cc=opt["cc"], rd_var=opt["rd_var"])
        except M.ModelError as exc:
            out["warnings"].append("MH: %s" % exc)
    if measure == "OR" and opt["peto"]:
        cols = [[r.get(c) for r in es.rows] for c in ("e1", "n1", "e2", "n2")]
        try:
            out["peto"] = M.peto(*cols, level=opt["level"], labels=es.labels)
        except M.ModelError as exc:
            out["warnings"].append("Peto: %s" % exc)
    out["back_transformed"] = {
        "estimate_ci": _display(measure, primary.estimate, primary.ci_lower, primary.ci_upper, nh),
        "pi": _display(measure, primary.pi_lower, primary.pi_lower, primary.pi_upper, nh)[1:]
        if primary.pi_lower is not None else None,
        "scale_note": _scale_note(measure),
    }
    # 4) alcsoport / meta-regresszió
    if opt["subgroup"]:
        groups = [r.get(opt["subgroup"]) for r in es.rows]
        if any(g is None for g in groups):
            out["warnings"].append("Az alcsoport-oszlopban (%s) hiányzó érték van; az alcsoport-elemzés "
                                   "kimaradt." % opt["subgroup"])
        else:
            out["subgroups"] = MO.subgroup_analysis(es.yi, es.vi, [str(g) for g in groups], es.labels,
                                                    opt["model"], opt["tau2"], opt["ci"], opt["level"],
                                                    opt["common_tau2"], opt["pi"])
    if opt["moderators"]:
        try:
            x, names = MO.design_matrix(es.rows, opt["moderators"])
            out["metaregression"] = MO.meta_regression(
                es.yi, es.vi, x, names,
                opt["tau2"] if opt["tau2"] in ("REML", "ML", "DL") else "REML",
                opt["metareg_test"], opt["level"])
        except (M.ModelError, ArithmeticError) as exc:
            out["warnings"].append("Meta-regresszió: %s" % exc)
    # 5) kis-vizsgálat hatások
    bias = {"performed": k >= 3, "k": k, "min_k_recommended": opt["bias_min_k"]}
    if k >= 3:
        try:
            bias["egger"] = B.egger_test(es.yi, es.vi)
            bias["begg"] = B.begg_test(es.yi, es.vi)
            bias["trimfill"] = B.trim_and_fill(es.yi, es.vi, es.labels, opt["model"], opt["tau2"],
                                               opt["trimfill_estimator"])
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
                                                 opt["ci"], opt["level"])
        sens["influence"] = SE.influence(es.yi, es.vi, es.labels, opt["model"], opt["tau2"], opt["level"])
    if opt["cumulative"]:
        keys = [r.get(opt["cumulative"]) for r in es.rows]
        if all(kk is not None for kk in keys):
            sens["cumulative"] = SE.cumulative(es.yi, es.vi, es.labels, keys, opt["model"], opt["tau2"],
                                               opt["ci"], opt["level"])
    out["sensitivity"] = sens
    out["warnings"] += list(primary.warnings or [])
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
    summaries = []
    for key, lab in (("ivhet", "IVhet modell (Doi 2015)"), ("random", "Véletlen hatású modell"),
                     ("fixed", "Közös (fix) hatású modell")):
        r = out.get(key)
        if r is None:
            continue
        if key == "random":
            lab = "%s (%s, %s)" % (lab, r.tau2_method, r.ci_method.upper())
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
        for g in sg.groups:
            idx = [es.labels.index(lab) for lab in g.labels]
            sections.append({"title": str(g.group), "indices": idx, "summary": {
                "label": "Alcsoport összesen (k = %d)" % g.k, "estimate": g.estimate,
                "ci_lower": g.ci_lower, "ci_upper": g.ci_upper,
                "display": _display(measure, g.estimate, g.ci_lower, g.ci_upper, nh),
                "pi_lower": None, "pi_upper": None,
                "note": ("τ² = %.3g; I² = %.0f%%" % (g.tau2, g.I2)) if g.k > 1 else None}})
    per_study_n = es.ni if measure == "PFT" else None
    data = P.forest_data(measure, es.labels, es.yi, es.vi, weights, summaries, opt["level"],
                         per_study_n, sections)
    het = primary.heterogeneity
    footer = ["Heterogenitás: Q = %.2f (df = %d, p %s); I² = %.0f%%; τ² = %s" % (
        primary.Q, primary.Q_df, _p(primary.p_Q), primary.I2,
        ("%.3g" % primary.tau2) if out["primary_model"] in ("random", "ivhet") else "–")]
    if out.get("subgroups"):
        sg = out["subgroups"]
        footer.append("Alcsoport-különbség: Q_b = %.2f (df = %d, p %s)" % (sg.Q_between, sg.df_between, _p(sg.p_between)))
    if primary.pi_lower is not None:
        footer.append("Piros vonal: %d%%-os predikciós intervallum (%s)." % (round(opt["level"] * 100), primary.pi_method))
    svg_forest = P.forest_svg(data, title=opt.get("title"), left_label=opt.get("left_label"),
                              right_label=opt.get("right_label"),
                              effect_label="%s [%d%% CI]" % (_short(measure), round(opt["level"] * 100)),
                              footer=footer)
    tf = out.get("bias", {}).get("trimfill")
    svg_funnel = P.funnel_svg(es.yi, es.vi, out["fixed"].estimate, measure,
                              title="Funnel plot (kontúr-javított)",
                              filled_yi=tf.filled_yi if tf else None, filled_vi=tf.filled_vi if tf else None)
    data["heterogeneity"] = {"Q": primary.Q, "df": primary.Q_df, "p": primary.p_Q, "I2": primary.I2,
                             "tau2": primary.tau2}
    data["funnel"] = {"yi": es.yi, "sei": [math.sqrt(v) for v in es.vi], "center": out["fixed"].estimate,
                      "filled_yi": tf.filled_yi if tf else [],
                      "filled_sei": [math.sqrt(v) for v in tf.filled_vi] if tf else []}
    return svg_forest, svg_funnel, data


def _p(p):
    if p is None:
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
        rows.append([lab, y, v, se, y - z * se, y + z * se, w[i] if w else None, es.notes[i]])
    p = os.path.join(outdir, "effect_sizes.csv")
    write_csv(p, ["study", "yi", "vi", "sei", "ci_lower", "ci_upper", "weight_pct", "note"], rows)
    paths["effect_sizes.csv"] = p
    if report_md is not None:
        p = os.path.join(outdir, "report.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(report_md)
        paths["report.md"] = p
    return paths
