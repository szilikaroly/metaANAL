# -*- coding: utf-8 -*-
"""Markdown riport: magyar összefoglaló + angol Methods-bekezdés a kézirathoz."""
import math

from . import effect_sizes as E

CI_NAMES_EN = {"z": "Wald-type (normal) confidence intervals", "t": "t-distribution based confidence intervals",
               "hksj": "the Hartung–Knapp–Sidik–Jonkman (HKSJ) method for confidence intervals",
               "hksj_adhoc": "the HKSJ method with the ad hoc variance correction (q* = max{1, q})"}
TAU2_NAMES_EN = {"DL": "the DerSimonian–Laird estimator", "REML": "restricted maximum likelihood (REML)",
                 "ML": "maximum likelihood", "PM": "the Paule–Mandel estimator", "HE": "the Hedges (HE) estimator",
                 "SJ": "the Sidik–Jonkman estimator"}
MEASURE_EN = {"MD": "mean difference (MD)", "SMD": "standardised mean difference (Hedges' g)",
              "COHEN_D": "standardised mean difference (Cohen's d)", "ROM": "log ratio of means",
              "OR": "odds ratio (OR)", "RR": "risk ratio (RR)", "RD": "risk difference (RD)",
              "PR": "raw proportion", "PLN": "log-transformed proportion", "PLO": "logit-transformed proportion",
              "PAS": "arcsine-transformed proportion", "PFT": "Freeman–Tukey double-arcsine transformed proportion",
              "COR": "correlation coefficient", "ZCOR": "Fisher z-transformed correlation", "GEN": "effect size"}


def _f(v, nd=2):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "–"
    return ("%." + str(nd) + "f") % v


def _p(p):
    if p is None:
        return "–"
    return "< 0.001" if p < 0.001 else "%.3f" % p


def methods_text_en(out):
    o = out["options"]
    m = o["measure"]
    r = out.get("random")
    parts = []
    parts.append("Effect sizes were expressed as the %s with %d%% confidence intervals (CIs)."
                 % (MEASURE_EN.get(m, m), round(o["level"] * 100)))
    if m in ("OR", "RR", "ROM"):
        parts.append("Analyses were performed on the log scale and results were back-transformed for presentation.")
    if m in ("OR", "RR", "RD"):
        parts.append("A continuity correction of %g was added to all cells of studies with a zero cell; studies with "
                     "no events (or all events) in both arms were excluded from OR/RR analyses." % o["cc"])
    if o["model"] == "ivhet":
        parts.append("Studies were pooled with the inverse variance heterogeneity (IVhet) model (Doi et al., 2015), "
                     "which uses inverse-variance weights and a variance inflated by the DerSimonian–Laird estimate "
                     "of τ²; random-effects (%s) and common-effect models were fitted as sensitivity analyses."
                     % TAU2_NAMES_EN.get(o["tau2"], o["tau2"]))
    elif o["model"] == "random":
        parts.append("Because between-study heterogeneity was anticipated, a random-effects model was used, with the "
                     "between-study variance (τ²) estimated by %s and %s." % (
                         TAU2_NAMES_EN.get(o["tau2"], o["tau2"]), CI_NAMES_EN.get(o["ci"] or "hksj")))
        parts.append("A %d%% prediction interval was calculated to describe the expected range of true effects in "
                     "a new setting (t-distribution with %s degrees of freedom)." % (
                         round(o["level"] * 100), "k−2" if o["pi"] == "t_k-2" else ("k−1" if o["pi"] == "t_k-1" else "∞ (normal)")))
        parts.append("A common-effect (inverse-variance) model was fitted as a sensitivity analysis.")
    else:
        parts.append("A common-effect (fixed-effect, inverse-variance) model was used.")
    if out.get("mh"):
        parts.append("Mantel–Haenszel pooled estimates were additionally calculated.")
    parts.append("Heterogeneity was assessed with Cochran's Q test, the I² statistic and τ²; confidence intervals "
                 "for τ² and I² were obtained with the Q-profile method.")
    if out.get("subgroups"):
        parts.append("Pre-specified subgroup analyses were performed (%s τ²), and differences between subgroups were "
                     "tested with a Q test for subgroup differences." % ("common" if o["common_tau2"] else "separate"))
    if out.get("metaregression"):
        parts.append("Mixed-effects meta-regression (%s; %s tests) was used to examine the moderator(s) %s." % (
            out["metaregression"].tau2_method,
            "Knapp–Hartung" if o["metareg_test"] == "knha" else "Wald-type z",
            ", ".join(o["moderators"])))
    parts.append("Influence was examined with leave-one-out analyses and influence diagnostics (externally "
                 "studentized residuals, DFFITS, Cook's distances, covariance ratios).")
    b = out.get("bias", {})
    if b.get("k", 0) >= o["bias_min_k"]:
        parts.append("Small-study effects were explored with contour-enhanced funnel plots, Egger's regression "
                     "test and the trim-and-fill method (%s estimator)." % o["trimfill_estimator"])
    else:
        parts.append("Because fewer than %d studies were available, tests for funnel plot asymmetry were not "
                     "interpreted." % o["bias_min_k"])
    parts.append("Analyses were conducted with metaelemzes v%s (Python; validated against the R package metafor)."
                 % out["engine"]["version"])
    return " ".join(parts)


def build_report(out, title=None, date=None):
    o = out["options"]
    m = o["measure"]
    L = []
    L.append("# %s" % (title or o.get("title") or "Metaanalízis-riport"))
    meta = []
    if date:
        meta.append("Dátum: %s" % date)
    meta.append("Motor: metaelemzes v%s" % out["engine"]["version"])
    if out.get("input", {}).get("path"):
        meta.append("Adat: `%s`" % out["input"]["path"])
    L.append(" · ".join(meta))
    L.append("")
    v = out["validation"]["summary"]
    L.append("> **Validálás:** %d hiba, %d figyelmeztetés, %d megjegyzés. %s" % (
        v["error"], v["warning"], v["info"],
        "A hibás sorok kimaradtak az elemzésből — lásd lent." if v["error"] else ""))
    L.append("")
    es = out["effect_sizes"]
    if out.get("primary") is None:
        L.append("Nincs elemezhető vizsgálat.")
        return "\n".join(L)
    pr = out["primary"]
    bt = out["back_transformed"]
    d = bt["estimate_ci"]
    L.append("## Fő eredmény")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append("| Hatásméret | %s |" % es["label"])
    L.append("| Vizsgálatok száma (k) | %d |" % es["k"])
    L.append("| Modell | %s |" % ("véletlen hatású (%s τ², %s CI)" % (pr.tau2_method, pr.ci_method.upper())
                                    if out["primary_model"] == "random" else
                                    ("IVhet (Doi 2015; DL τ²)" if out["primary_model"] == "ivhet" else "közös (fix) hatású")))
    L.append("| Összesített becslés [%d%% CI] | **%s [%s; %s]** |" % (round(o["level"] * 100), _f(d[0]), _f(d[1]), _f(d[2])))
    L.append("| p | %s (%s = %.2f%s) |" % (_p(pr.p), pr.test, pr.stat, (", df = %d" % pr.df) if pr.df else ""))
    if bt.get("pi"):
        L.append("| Predikciós intervallum | %s; %s |" % (_f(bt["pi"][0]), _f(bt["pi"][1])))
    h = pr.heterogeneity
    L.append("| Q (df), p | %.2f (%d), %s |" % (pr.Q, pr.Q_df, _p(pr.p_Q)))
    i2ci = h.get("I2_ci_QP")
    L.append("| I² [95%% CI, Q-profile] | %.1f%% %s |" % (pr.I2, ("[%.1f; %.1f]" % tuple(i2ci)) if i2ci else ""))
    if out["primary_model"] in ("random", "ivhet"):
        tci = h.get("tau2_ci_QP")
        L.append("| τ² [95%% CI] ; τ | %s %s ; %s |" % (_f(pr.tau2, 4), ("[%s; %s]" % (_f(tci[0], 4), _f(tci[1], 4))) if tci else "", _f(pr.tau, 4)))
    if bt.get("scale_note"):
        L.append("")
        L.append("_%s_" % bt["scale_note"])
    L.append("")
    fx = out.get("fixed")
    if fx is not None and out["primary_model"] != "fixed":
        dd = [E.back_transform(m, x) for x in (fx.estimate, fx.ci_lower, fx.ci_upper)] if m != "PFT" else [None] * 3
        L.append("Érzékenység — közös hatású modell: %s [%s; %s]." % (_f(dd[0]), _f(dd[1]), _f(dd[2])))
    for key, name in (("mh", "Mantel–Haenszel"), ("peto", "Peto")):
        r = out.get(key)
        if r is not None:
            dd = [E.back_transform(m if key == "mh" else "OR", x) for x in (r.estimate, r.ci_lower, r.ci_upper)]
            L.append("%s: %s [%s; %s], p = %s." % (name, _f(dd[0]), _f(dd[1]), _f(dd[2]), _p(r.p)))
    L.append("")
    L.append("## Értelmezési figyelmeztetések")
    L.append("")
    warns = list(out.get("warnings") or [])
    if out["primary_model"] == "random" and bt.get("pi") and d[1] is not None and m not in E.PROPORTION:
        null = 1.0 if m in ("OR", "RR", "ROM") else 0.0
        if (bt["pi"][0] - null) * (bt["pi"][1] - null) < 0 and (d[1] - null) * (d[2] - null) > 0:
            warns.append("A CI nem tartalmazza a nullhatást, de a predikciós intervallum igen: egy új "
                         "környezetben a hatás iránya is eltérhet.")
    if pr.I2 >= 75:
        warns.append("Jelentős heterogenitás (I² ≥ 75%): az összesített becslés önmagában félrevezető lehet; "
                     "vizsgáld a heterogenitás forrásait (előre tervezett alcsoportok / meta-regresszió).")
    for w_ in warns:
        L.append("- %s" % w_)
    if not warns:
        L.append("- —")
    L.append("")
    # alcsoportok
    sg = out.get("subgroups")
    if sg is not None:
        L.append("## Alcsoport-elemzés (%s)" % o["subgroup"])
        L.append("")
        L.append("| Alcsoport | k | Becslés [CI] | τ² | I² |")
        L.append("|---|---|---|---|---|")
        for g in sg.groups:
            dd = [E.back_transform(m, x) for x in (g.estimate, g.ci_lower, g.ci_upper)] if m != "PFT" else [g.estimate, g.ci_lower, g.ci_upper]
            L.append("| %s | %d | %s [%s; %s] | %s | %.0f%% |" % (g.group, g.k, _f(dd[0]), _f(dd[1]), _f(dd[2]), _f(g.tau2, 4), g.I2))
        L.append("")
        L.append("Alcsoport-különbség: Q_b = %.2f, df = %d, p = %s." % (sg.Q_between, sg.df_between, _p(sg.p_between)))
        for w_ in sg.warnings:
            L.append("- %s" % w_)
        L.append("")
    mr = out.get("metaregression")
    if mr is not None:
        L.append("## Meta-regresszió")
        L.append("")
        L.append("| Együttható | Becslés | SE | %s | p | CI |" % ("t" if mr.test == "knha" else "z"))
        L.append("|---|---|---|---|---|---|")
        for c in mr.coefficients:
            L.append("| %s | %.4f | %.4f | %.2f | %s | [%.4f; %.4f] |" % (c["name"], c["estimate"], c["se"], c["stat"], _p(c["p"]), c["ci_lower"], c["ci_upper"]))
        L.append("")
        L.append("Reziduális τ² = %s; QE(%d) = %.2f, p = %s; moderátor-teszt (%s) p = %s; R² = %s%%." % (
            _f(mr.tau2, 4), mr.QE_df, mr.QE, _p(mr.QE_p), mr.QM_type, _p(mr.QM_p), _f(mr.R2, 1)))
        for w_ in mr.warnings:
            L.append("- %s" % w_)
        L.append("")
    # torzítás
    b = out.get("bias", {})
    L.append("## Kis-vizsgálat hatások / publikációs torzítás")
    L.append("")
    if b.get("note"):
        L.append("> %s" % b["note"])
        L.append("")
    if b.get("egger") is not None:
        eg = b["egger"]
        L.append("- Egger-teszt: tengelymetszet = %.3f (SE %.3f), t(%d) = %.2f, p = %s" % (eg.intercept, eg.se_intercept, eg.df, eg.t, _p(eg.p)))
    if b.get("begg") is not None:
        bg = b["begg"]
        L.append("- Begg–Mazumdar: Kendall τ = %.3f, p = %s (%s)" % (bg.kendall_tau, _p(bg.p), bg.method))
    if b.get("trimfill") is not None:
        tf = b["trimfill"]
        a = tf.adjusted
        dd = [E.back_transform(m, x) for x in (a.estimate, a.ci_lower, a.ci_upper)] if m != "PFT" else [None] * 3
        L.append("- Trim-and-fill (%s): becsült hiányzó vizsgálat = %d (%s oldal); korrigált becslés %s [%s; %s]" % (
            tf.estimator, tf.k0, "bal" if tf.side == "left" else "jobb", _f(dd[0]), _f(dd[1]), _f(dd[2])))
    L.append("")
    # érzékenység
    sens = out.get("sensitivity", {})
    if sens.get("leave_one_out"):
        loo = sens["leave_one_out"]
        ests = [E.back_transform(m, r["estimate"]) if m != "PFT" else r["estimate"] for r in loo]
        L.append("## Érzékenységi elemzés")
        L.append("")
        L.append("Leave-one-out: az összesített becslés tartománya %s – %s." % (_f(min(ests)), _f(max(ests))))
        sig_change = [] if m in E.PROPORTION else [
            r["omitted"] for r in loo
            if (r["ci_lower"] > 0) != (pr.ci_lower > 0) or (r["ci_upper"] < 0) != (pr.ci_upper < 0)]
        if sig_change:
            L.append("Szignifikancia-váltás az alábbi vizsgálat(ok) kihagyásakor: %s." % ", ".join(sig_change))
        infl = [r["study"] for r in sens.get("influence", []) if r["influential"]]
        L.append("Befolyásos vizsgálat (metafor-kritériumok): %s." % (", ".join(infl) if infl else "nincs"))
        outl = [r["study"] for r in sens.get("influence", []) if r["outlier_flag"]]
        if outl:
            L.append("Kiugró (|rstudent| > 1.96): %s." % ", ".join(outl))
        L.append("")
    # vizsgálatok
    L.append("## Vizsgálatonkénti hatásméretek")
    L.append("")
    L.append("Lásd: `effect_sizes.csv`, `forest.svg`.")
    if es["excluded"]:
        L.append("")
        L.append("Kizárt sorok:")
        for ex in es["excluded"]:
            L.append("- %s — %s" % (ex["study"], ex["reason"]))
    L.append("")
    # validálás
    fnd = out["validation"]["findings"]
    if fnd:
        L.append("## Adatvalidálás")
        L.append("")
        L.append("| Kód | Súlyosság | Vizsgálat | Részlet | Teendő |")
        L.append("|---|---|---|---|---|")
        for f in fnd:
            L.append("| %s | %s | %s | %s | %s |" % (f["code"], f["severity"], f["study"] or "–",
                                                   (f["detail"] or f["title"]).replace("|", "/"), f["advice"].replace("|", "/")))
        L.append("")
    L.append("## Methods (angol, kéziratba)")
    L.append("")
    L.append(methods_text_en(out))
    L.append("")
    L.append("---")
    L.append("_Automatikusan generált riport. Az eredményeket az ellenőrző (reviewer) ágens és a szerző "
             "validálja; klinikai következtetés csak a bizonyosság (GRADE) értékelése után vonható le._")
    return "\n".join(L)
