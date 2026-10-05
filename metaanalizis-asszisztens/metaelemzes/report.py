# -*- coding: utf-8 -*-
"""Markdown riport: magyar összefoglaló + angol Methods-bekezdés a kézirathoz.

Elvek:
- minden szám az értelmezési skálán (OR/RR/ROM exp, arányok és ZCOR visszatranszformálva;
  PFT a harmonikus átlag n-nel), a fő eredménytől az alcsoportokon, a leave-one-out
  tartományon, a közös hatású érzékenységi modellen, a trim-and-fill-en és a kumulatív
  elemzésen át;
- minden CI-felirat a tényleges --level szintet mutatja;
- az I² pontbecslés és a CI-je ugyanarra a definícióra vonatkozik (Q-alapú I² +
  Higgins–Thompson CI; τ²-alapú I² + Q-profile CI — mindkettő felcímkézve);
- a Methods-bekezdés a TÉNYLEGESEN elvégzett elemzéseket írja le (k-tól függő kihagyások,
  modell-visszaesés, folytonossági korrekció, szűrők);
- a táblázatcellák Markdown-escape-eltek.
"""
import math
import re

from . import effect_sizes as E
from .plots import decimals

CI_NAMES_EN = {"z": "Wald-type (normal) confidence intervals", "t": "t-distribution based confidence intervals",
               "hksj": "the Hartung–Knapp–Sidik–Jonkman (HKSJ) method for confidence intervals",
               "hksj_adhoc": "the HKSJ method with the ad hoc variance correction (q* = max{1, q})"}
CI_NAMES_HU = {"z": "z", "t": "t", "hksj": "HKSJ", "hksj_adhoc": "HKSJ (ad hoc)"}
TAU2_NAMES_EN = {"DL": "the DerSimonian–Laird estimator", "REML": "restricted maximum likelihood (REML)",
                 "ML": "maximum likelihood", "PM": "the Paule–Mandel estimator", "HE": "the Hedges (HE) estimator",
                 "SJ": "the Sidik–Jonkman estimator"}
TAU2_SHORT_EN = {"DL": "DerSimonian–Laird", "REML": "REML", "ML": "maximum-likelihood", "PM": "Paule–Mandel",
                 "HE": "Hedges", "SJ": "Sidik–Jonkman", "rögzített": "fixed (pre-specified)"}
MEASURE_EN = {"MD": "mean difference (MD)", "SMD": "standardised mean difference (Hedges' g)",
              "COHEN_D": "standardised mean difference (Cohen's d)", "ROM": "ratio of means (ROM)",
              "SMD_GLASS": "standardised mean difference standardised by the control-group SD (Glass's Δ)",
              "MC": "mean change (paired design; mean of the within-person differences)",
              "SMCC": "standardised mean change (paired design; change-score standardisation, Hedges-corrected)",
              "OR": "odds ratio (OR)", "RR": "risk ratio (RR)", "RD": "risk difference (RD)",
              "PR": "raw proportion", "PLN": "log-transformed proportion", "PLO": "logit-transformed proportion",
              "PAS": "arcsine-transformed proportion", "PFT": "Freeman–Tukey double-arcsine transformed proportion",
              "COR": "correlation coefficient", "ZCOR": "Fisher z-transformed correlation", "GEN": "effect size"}
SCALE_EN = {"OR": "Analyses were performed on the log scale and results were back-transformed for presentation.",
            "RR": "Analyses were performed on the log scale and results were back-transformed for presentation.",
            "ROM": "Analyses were performed on the log scale and results were back-transformed for presentation.",
            "PLN": "Proportions were analysed on the log scale and back-transformed for presentation.",
            "PLO": "Proportions were analysed on the logit scale and back-transformed for presentation.",
            "PAS": "Proportions were analysed on the arcsine square-root scale and back-transformed for presentation.",
            "PFT": "Proportions were analysed on the Freeman–Tukey double-arcsine scale and back-transformed with "
                   "Miller's (1978) inversion formula using the harmonic mean of the sample sizes.",
            "PFT_variance": "Proportions were analysed on the Freeman–Tukey double-arcsine scale and back-transformed "
                            "with Miller's (1978) inversion formula using, for each pooled estimate and its CI, "
                            "m = 1/Var(t) of that estimate (MetaXL convention; Barendregt et al. 2013); the "
                            "prediction interval was back-transformed with the harmonic mean of the sample sizes.",
            "ZCOR": "Correlations were analysed as Fisher z values and back-transformed to r for presentation."}
SMD_VTYPE_EN = {
    "LS": "the large-sample variance formula (1/n1 + 1/n2 + y²/(2N))",
    "LS2": "the Borenstein et al. (2009) variance formula",
    "UB": "the unbiased variance estimator (Hedges 1983)",
    "METAN_COHEN": "the Stata metan / MetaXL variance formula N/(n1·n2) + d²/(2(N − 2))",
    "METAN_HEDGES": "the Stata metan / MetaXL variance formula N/(n1·n2) + g²/(2(N − 3.94))",
}
METAN_J_EN = "the approximate correction factor J = 1 − 3/(4N − 9)"
GLASS_VTYPE_EN = {
    "METAN": "the Stata metan / MetaXL variance N/(n1·n2) + Δ²/(2(n2 − 1)) without small-sample correction",
    "LS": "the large-sample variance (metafor SMD1, vtype LS)",
    "LS2": "the metafor SMD1 variance (vtype LS2)",
    "UB": "the unbiased variance with small-sample correction (metafor SMD1, vtype UB)",
    "SMD1H": "the metafor SMD1H variance (heteroscedastic arms)",
}
PI_DF_EN = {"t_k-2": "a t-distribution with k − 2 = %s degrees of freedom",
            "t_k-1": "a t-distribution with k − 1 = %s degrees of freedom",
            "z": "the standard normal distribution%s"}


# ------------------------------------------------------------------ formázás
def _f(v, nd=2):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "–"
    s = ("%." + str(nd) + "f") % v
    if s.startswith("-") and float(s) == 0:          # nincs "-0.00"
        s = s[1:]
    return s


def _tri(d):
    """'becslés [alsó; felső]' a nagyságrendhez igazított, közös tizedesjeggyel (RD 0.0033)."""
    d = list(d) if d else [None, None, None]
    nd = decimals(d)
    return "%s [%s; %s]" % (_f(d[0], nd), _f(d[1], nd), _f(d[2], nd))


def _pair(a, b):
    nd = decimals((a, b))
    return "%s; %s" % (_f(a, nd), _f(b, nd))


def _p(p):
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "–"
    return "< 0.001" if p < 0.001 else "%.3f" % p


def _p_eq(p):
    """'p < 0.001' / 'p = 0.042' (nincs 'p = < 0.001')."""
    s = _p(p)
    return "p " + s if s.startswith("<") else "p = " + s


def _col(out, name):
    """Az oszlop megjelenítési neve (eredeti CSV-fejléc; pipeline 'column_labels'), különben a megadott opció."""
    lab = (out.get("column_labels") or {}).get(name)
    return lab if lab else (out.get("options") or {}).get(name)


def _sentence(s):
    """Mondatzáró pont, de nem duplán (pl. 'Otsubo et al.' végű címkénél)."""
    return s if s.endswith(".") else s + "."


def _md(s):
    """Markdown-táblázatcella / felsorolás escape: '|' → '\\|', HTML-tagek semlegesítése, sortörés → szóköz."""
    s = "–" if s is None else str(s)
    s = re.sub(r"&(?=#?\w+;)", "&amp;", s)           # karakter-entitás ne oldódjon fel
    s = re.sub(r"<(?=[A-Za-z/!?])", "&lt;", s)        # HTML-tag ne jelenjen meg ('k < 10' marad olvasható)
    return s.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _lv(o):
    return int(round(o["level"] * 100))


def _bt(m, vals, nh=None, se=None, pft_n="harmonic"):
    """Visszatranszformálás az értelmezési skálára. PFT: harmonikus átlag n, vagy
    pft_n='variance' esetén (MetaXL) m = 1/(4·se²) az adott eredmény SE-jéből (ha ismert)."""
    if m != "PFT" or pft_n != "variance" or not (se is not None and se > 0 and math.isfinite(se)):
        pft_n, se = "harmonic", None
    out = []
    for x in vals:
        if x is None:
            out.append(None)
            continue
        try:
            out.append(E.back_transform(m, x, nh, pft_n, se))
        except E.EffectSizeError:
            out.append(None)
    return out


def _pft(o):
    return o.get("pft_backtransform") or "harmonic"


def _nh(out):
    nh = (out.get("back_transformed") or {}).get("n_harmonic")
    if nh is None and out["options"]["measure"] == "PFT":
        nh = E.harmonic_mean((out.get("effect_sizes") or {}).get("ni") or [])
    return nh


def _drop00(o):
    if o.get("drop00") is not None:
        return bool(o["drop00"])
    return o["measure"] in ("OR", "RR")


def _model_hu(r, kind):
    if kind == "random":
        return "véletlen hatású (%s τ², %s CI)" % (r.tau2_method, CI_NAMES_HU.get(r.ci_method, r.ci_method))
    if kind == "ivhet":
        return "IVhet (Doi 2015; %s τ²)" % (r.tau2_method or "DL")
    return "közös (fix) hatású" + ("" if r.ci_method == "z" else " (%s CI)" % CI_NAMES_HU.get(r.ci_method, r.ci_method))


# ------------------------------------------------------------------ Methods
def _filter_sentence(out):
    flt = (out.get("input") or {}).get("filters") or {}
    rep = flt.get("report") or []
    descs = []
    if rep:
        for fr in rep:
            if fr.get("mode") == "include":
                descs.append("only rows with %s were included (n = %d removed)" % (fr["filter"], fr.get("removed", 0)))
            else:
                descs.append("rows with %s were excluded (n = %d)" % (fr["filter"], fr.get("removed", 0)))
    else:
        descs += ["only rows with %s were included" % c for c in flt.get("include") or []]
        descs += ["rows with %s were excluded" % c for c in flt.get("exclude") or []]
    if not descs:
        return None
    s = "This is a restricted (sensitivity) analysis of the data set: %s" % "; ".join(descs)
    if flt.get("n_rows_read") is not None and flt.get("n_rows_kept") is not None:
        s += "; %d of %d rows were retained" % (flt["n_rows_kept"], flt["n_rows_read"])
    return s + "."


def _zero_cell_sentences(out):
    o = out["options"]
    m = o["measure"]
    cc, cc_to = o.get("cc", 0.5), o.get("cc_to", "only0")
    es = out.get("effect_sizes") or {}
    n_corr = len(es.get("corrected") or [])
    n_dz = sum(1 for e in es.get("excluded") or [] if "kettős nulla" in str(e.get("reason")))
    applies = cc_to != "none" and cc and cc > 0
    s = []
    n_dz_in = len(es.get("double_zero_included") or [])

    def nst(n):
        return "%d %s" % (n, "study" if n == 1 else "studies")

    if m in ("OR", "RR"):
        if not applies:
            s.append("No continuity correction was applied; studies whose effect size could not be computed "
                     "because of zero cells were excluded.")
        elif cc_to == "all":
            s.append("A continuity correction of %g was added to all cells of all studies." % cc)
        elif n_corr:
            s.append("A continuity correction of %g was added to all cells of studies with a zero cell (%s)."
                     % (cc, nst(n_corr)))
        else:
            s.append("No analysed study had a zero cell, so no continuity correction was applied.")
    elif m == "RD":
        if cc_to == "all" and applies:
            s.append("A continuity correction of %g was added to all cells of all studies." % cc)
        elif applies and n_corr:
            s.append("Risk differences were calculated without continuity correction; a correction of %g was "
                     "used only for the variance of studies whose variance would otherwise be zero (%s)."
                     % (cc, nst(n_corr)))
        else:
            s.append("Risk differences were calculated without continuity correction.")
    elif m in ("PR", "PLN", "PLO"):
        if not applies:
            s.append("No continuity correction was applied%s." % (
                "" if m == "PR" else "; studies with 0%% or 100%% events could not be analysed on the "
                "%s scale and were excluded" % ("log" if m == "PLN" else "logit")))
        elif cc_to == "all":
            s.append("A continuity correction of %g was applied to all studies (events + %g, sample size + %g)."
                     % (cc, cc, 2 * cc))
        elif n_corr:
            s.append("A continuity correction of %g was applied to studies with 0%% or 100%% events (%s)."
                     % (cc, nst(n_corr)))
        else:
            s.append("No analysed study had 0% or 100% events, so no continuity correction was applied.")
    elif m in ("PAS", "PFT"):
        s.append("No continuity correction was needed, because the %s transformation is defined for proportions "
                 "of 0 and 1." % ("Freeman–Tukey" if m == "PFT" else "arcsine"))
    if m in E.BINARY:
        if _drop00(o) and n_dz:
            s.append("Studies with no events (or only events) in both arms were excluded (%s)." % nst(n_dz))
        elif not _drop00(o) and n_dz_in:
            s.append("Studies with no events (or only events) in both arms were retained in the analysis (%s)."
                     % nst(n_dz_in))
    return s


def _es_method_sentences(o):
    """A hatásméret-számítás konvenciói (variancia-képlet, korrekció) mértékenként."""
    m = o["measure"]
    vt = o.get("smd_vtype") or "LS"
    jm = "exact" if (o.get("j_method") or "exact") == "exact" else "approximate (1 − 3/(4·df − 1))"
    s = []
    if m == "SMD":
        if vt == "METAN_HEDGES":
            s.append("Hedges' g was computed with %s and %s." % (METAN_J_EN, SMD_VTYPE_EN[vt]))
        elif vt == "METAN_COHEN":
            # a metan Cohen-variancia g-re alkalmazva: egyik metan-konvencióval sem egyezik
            s.append("Hedges' g was computed with the %s small-sample correction factor and the variance formula "
                     "N/(n1·n2) + g²/(2(N − 2)) (the Stata metan / MetaXL Cohen's d variance applied to g; this "
                     "combination matches neither Stata metan convention)." % jm)
        else:
            s.append("Hedges' g was computed with the %s small-sample correction factor and %s."
                     % (jm, SMD_VTYPE_EN.get(vt, vt)))
    elif m == "COHEN_D":
        cv = "LS" if vt == "UB" else vt
        if cv == "METAN_HEDGES":
            s.append("Cohen's d (without small-sample correction) was computed with the variance formula "
                     "N/(n1·n2) + d²/(2(N − 3.94)) (the Stata metan / MetaXL Hedges' g variance applied to the "
                     "uncorrected d; this combination matches neither Stata metan convention).")
        else:
            s.append("Cohen's d (without small-sample correction) was computed with %s." % SMD_VTYPE_EN.get(cv, cv))
    elif m == "MD":
        pooled = E.md_vtype_name(o.get("md_vtype") or "unequal") == "pooled"
        s.append("Sampling variances of the mean differences were computed %s." % (
            "assuming equal variances in the two groups (pooled SD; metafor vtype HO)" if pooled else
            "without assuming equal variances (sd1²/n1 + sd2²/n2)"))
    elif m == "SMD_GLASS":
        gv = o.get("glass_vtype") or "METAN"
        s.append("Glass's Δ was computed with %s." % GLASS_VTYPE_EN.get(gv, gv))
    elif m in ("MC", "SMCC"):
        s.append("Paired (before–after or matched) designs were analysed from the within-person differences; the SD of "
                 "the differences was taken as reported, or derived from the arm SDs and the reported correlation, "
                 "or from the reported sum of squared deviations.")
        if m == "SMCC":
            sv = vt if vt in ("LS", "LS2") else "LS"
            s.append("The standardised mean change used the SD of the differences, the %s small-sample correction "
                     "and the %s variance formula." % (jm, sv))
    if m == "GEN" and o.get("gen_smd_vtype"):
        s.append("For studies reporting only a standardised mean difference and the group sizes, the sampling "
                 "variance was derived with %s." % SMD_VTYPE_EN.get(o["gen_smd_vtype"], o["gen_smd_vtype"]))
    return s


def _bias_test_names(o, b):
    tests = []
    if b.get("egger") is not None:
        tests.append("Egger's regression test" + (
            " (intercept CI with the normal quantile, as in dmetar)" if o.get("egger_ci_dist") == "norm" else ""))
    if b.get("harbord") is not None:
        tests.append("Harbord's score-based test")
    if b.get("peters") is not None:
        tests.append("Peters' test")
    bg = b.get("begg")
    if bg is not None:
        meth = o.get("begg_method") or "auto"
        if meth == "normal" or bg.get("method", "").startswith("normális"):
            how = "normal approximation" + (" with continuity correction" if o.get("begg_continuity") else
                                            " without continuity correction")
        else:
            how = "exact Kendall distribution"
        tests.append("Begg's rank correlation test (%s)" % how)
    tf = b.get("trimfill")
    if tf is not None:
        txt = "the trim-and-fill method (%s estimator" % o.get("trimfill_estimator")
        if tf.get("trim_model") == "fixed" and o.get("model") != "fixed":
            txt += "; trimming based on the common-effect model and the filled data pooled with the primary model, " \
                   "as in meta::trimfill"
        elif o.get("trimfill_trim_model") == "random":
            txt += "; trimming based on the random-effects model"
        tests.append(txt + ")")
    if b.get("lfk") is not None:
        tests.append("the Doi plot with the LFK index (Furuya-Kanamori et al. 2018; a heuristic with |LFK| ≤ 1, "
                     "1–2 and > 2 read as no, minor and major asymmetry, interpreted as a sensitivity measure only)")
    return tests


def _robust_weights(mr):
    """A robusztus (HC1) blokk súlyai: FE-súlyok 1/v (τ² = 0) vagy a modell súlyai 1/(v + τ²)."""
    return "fixed" if (mr.tau2_method == "FE" or not mr.tau2) else "random"


def methods_text_en(out, plots=True):
    """A TÉNYLEGESEN elvégzett elemzések leírása (nem a kért opcióké).
    plots=False: nem készült ábra (--no-plots), a funnel plotot nem említjük."""
    o = out["options"]
    m = o["measure"]
    lv = _lv(o)
    es = out.get("effect_sizes") or {}
    k = es.get("k", 0)
    pr = out.get("primary")
    pm = out.get("primary_model")
    parts = []
    fs = _filter_sentence(out)
    if fs:
        parts.append(fs)
    parts.append("Effect sizes were expressed as the %s with %d%% confidence intervals (CIs)."
                 % (MEASURE_EN.get(m, m), lv))
    parts += _es_method_sentences(o)
    if m == "PFT" and _pft(o) == "variance":
        parts.append(SCALE_EN["PFT_variance"])
    elif m in SCALE_EN:
        parts.append(SCALE_EN[m])
    parts += _zero_cell_sentences(out)
    if pr is None:
        parts.append("No study could be analysed.")
        return " ".join(parts)
    rnd = out.get("random")
    fb = out.get("model_fallback")
    if pm == "ivhet":
        tm = pr.tau2_method or "DL"
        parts.append("Studies were pooled with the inverse variance heterogeneity (IVhet) model (Doi et al., 2015), "
                     "which uses inverse-variance weights and a variance inflated by the %s estimate of τ²%s." % (
                         TAU2_SHORT_EN.get(tm, tm),
                         "" if tm == "DL" else " (the published IVhet model uses the DerSimonian–Laird estimator)"))
        if rnd is not None:
            parts.append("A random-effects model (%s; %s) and a common-effect model were fitted as sensitivity analyses."
                         % (TAU2_NAMES_EN.get(rnd.tau2_method, rnd.tau2_method),
                            CI_NAMES_EN.get(rnd.ci_method, rnd.ci_method)))
    elif pm == "random":
        parts.append("Because between-study heterogeneity was anticipated, a random-effects model was used, with the "
                     "between-study variance (τ²) estimated by %s and %s." % (
                         TAU2_NAMES_EN.get(pr.tau2_method, pr.tau2_method), CI_NAMES_EN.get(pr.ci_method, pr.ci_method)))
    elif fb:
        parts.append("Only one study was available, so no pooling was performed and the %s model could not be "
                     "fitted; the single study's estimate is reported." % (
                         "random-effects" if fb.get("requested") == "random" else "IVhet"))
    else:
        parts.append("A common-effect (fixed-effect, inverse-variance) model was used%s." % (
            "" if pr.ci_method == "z" else ", with %s" % CI_NAMES_EN.get(pr.ci_method, pr.ci_method)))
        if rnd is not None:
            parts.append("A random-effects model (%s; %s) was fitted as a sensitivity analysis." % (
                TAU2_NAMES_EN.get(rnd.tau2_method, rnd.tau2_method), CI_NAMES_EN.get(rnd.ci_method, rnd.ci_method)))
    if pr.pi_lower is not None:
        fmt = PI_DF_EN.get(pr.pi_method, "%s")
        parts.append("A %d%% prediction interval was calculated to describe the expected range of true effects in "
                     "a new setting (%s)." % (lv, fmt % (pr.pi_df if pr.pi_method != "z" else "")))
    elif pm == "random":
        parts.append("A prediction interval was not calculated because fewer than %d studies were available."
                     % (3 if o.get("pi") == "t_k-2" else 2))
    if pm == "random" and out.get("fixed") is not None:
        parts.append("A common-effect (inverse-variance) model was fitted as a sensitivity analysis.")
    if out.get("mh") is not None:
        parts.append("Mantel–Haenszel pooled estimates were additionally calculated%s." % (
            (" (variance of the risk difference: %s)" % (
                "Greenland & Robins 1985" if o.get("rd_var") == "gr" else "Sato, Greenland & Robins 1989"))
            if m == "RD" else ""))
    if out.get("peto") is not None:
        parts.append("Peto odds ratios were additionally calculated.")
    if k >= 2:
        if pm in ("random", "ivhet"):
            parts.append("Heterogeneity was assessed with Cochran's Q test, τ² and the I² statistic; I² is reported "
                         "both as the conventional Q-based value (Higgins–Thompson CI) and as the τ²-based value, "
                         "with Q-profile CIs for τ² and the τ²-based I².")
        else:
            parts.append("Heterogeneity was assessed with Cochran's Q test and the Q-based I² statistic "
                         "(Higgins–Thompson CI).")
        if k >= 3:
            if (o.get("h_centre") or "truncated") == "truncated":
                parts.append("H was reported as max(1, √(Q/df)), with the Higgins–Thompson CI of H and I² centred on "
                             "ln max(1, H) as in the R package meta, together with the modified H² = max(0, (Q − df)/df).")
            else:
                parts.append("H was reported as max(1, √(Q/df)), with the Higgins–Thompson CI of H and I² centred on "
                             "½·ln(Q/df) (untruncated, Borenstein et al. 2009), together with the modified "
                             "H² = max(0, (Q − df)/df).")
    sg = out.get("subgroups")
    if sg is not None:
        if o.get("model") == "fixed":
            within = "common-effect models within subgroups"
        elif o.get("model") == "ivhet" and sg.get("common_tau2") is not None:
            within = "IVhet models within subgroups with a common τ² across subgroups (%s, from the " \
                     "subgroup-factor model)" % TAU2_SHORT_EN.get(o.get("tau2") or "DL", o.get("tau2"))
        elif o.get("model") == "ivhet":
            within = "IVhet models within subgroups"
        elif sg.get("common_tau2") is not None:
            within = "random-effects models with a common τ² across subgroups"
        else:
            within = "random-effects models with a separate τ² in each subgroup"
        # az előzetes tervezettséget a motor nem tudja: csak kifejezett opcióra állítjuk (PRISMA 2020 13e)
        parts.append("%s analyses by %s were performed (%s), and differences between subgroups "
                     "were tested with a χ² test for subgroup differences (Q_between, based on the %s "
                     "standard errors of the subgroup estimates)." % (
                         "Pre-specified subgroup" if o.get("subgroup_prespecified") else "In addition, subgroup",
                         _col(out, "subgroup"), within,
                         "IVhet (heterogeneity-inflated)" if sg.get("Q_between_se") == "ivhet" else "Wald-type"))
    mr = out.get("metaregression")
    if mr is not None:
        mods_txt = ", ".join(x for x in (_col(out, "moderators") or [])
                             if x not in (out.get("metaregression_dropped") or []))
        tst = "Knapp–Hartung" if mr.test == "knha" else "Wald-type z"
        if mr.tau2_method == "FE":
            parts.append("Fixed-effect (inverse-variance weighted, τ² = 0) meta-regression (%s tests) was used to "
                         "examine the moderator(s) %s." % (tst, mods_txt))
        else:
            parts.append("Mixed-effects meta-regression (%s; %s tests) was used to examine the moderator(s) %s." % (
                TAU2_NAMES_EN.get(mr.tau2_method, mr.tau2_method), tst, mods_txt))
        if mr.get("robust"):
            if _robust_weights(mr) == "fixed":
                wtxt = ("computed with fixed-effect weights 1/v_i (equivalent to Stata regress with analytic weights "
                        "1/v_i and vce(robust))")
            else:
                wtxt = ("computed with the random-effects weights 1/(v_i + τ²) of the meta-regression (τ² by %s; "
                        "equivalent to metafor robust() with adjust = TRUE and one cluster per study; these equal "
                        "Stata regress with analytic weights 1/v_i and vce(robust) only when τ² = 0)"
                        % TAU2_SHORT_EN.get(mr.tau2_method, mr.tau2_method))
            parts.append("Heteroscedasticity-robust (HC1 sandwich) standard errors with t(k − p) tests and a robust "
                         "Wald F test were additionally reported, %s." % wtxt)
    sens = out.get("sensitivity") or {}
    if sens.get("leave_one_out"):
        parts.append("Influence was examined with leave-one-out analyses and influence diagnostics (externally "
                     "studentized residuals, DFFITS, Cook's distances, covariance ratios).")
    else:
        parts.append("Leave-one-out and influence analyses were not performed (fewer than 3 studies).")
    if sens.get("cumulative"):
        parts.append("A cumulative meta-analysis ordered by %s was performed." % _col(out, "cumulative"))
    ol = sens.get("outliers")
    if ol is not None:
        if ol.refit is not None:
            res = "the model was refitted without the %d flagged %s as a sensitivity analysis." % (
                ol.k_removed, "study" if ol.k_removed == 1 else "studies")
        elif ol.k_removed:
            res = "all %d studies met this criterion, so no refit without them was possible." % ol.k_removed
        else:
            res = "no study met this criterion."
        parts.append("Outlying studies were screened with the rule of dmetar::find.outliers (a study whose %d%% CI "
                     "lies entirely outside the %d%% CI of the pooled estimate); %s" % (lv, lv, res))
    b = out.get("bias") or {}
    tests = _bias_test_names(o, b)
    plot = "funnel plots" if m in E.PROPORTION else "contour-enhanced funnel plots"
    if not b.get("performed") or not tests:
        parts.append("Small-study effects were not assessed (fewer than 3 studies).")
    elif b.get("k", 0) >= o["bias_min_k"]:
        parts.append("Small-study effects were explored with %s." % (
            ("%s, %s" % (plot, _join_en(tests))) if plots else _join_en(tests)))
    else:
        parts.append("Because fewer than %d studies were available, funnel plot asymmetry (%s) was examined for "
                     "information only and was not interpreted." % (o["bias_min_k"], _join_en(tests)))
    if b.get("harbord") is not None or b.get("peters") is not None:
        parts.append("For the binary outcome, the Harbord and Peters tests (both based on the log odds ratio) were "
                     "preferred to Egger's test, which is prone to false-positive results with odds ratios "
                     "(Sterne et al. 2011).")
    if b.get("smd_note") and b.get("egger") is not None:
        parts.append("For standardised mean differences, Egger's test was regarded as informative only, because "
                     "the SMD and its standard error are correlated (Pustejovsky & Rodgers 2019).")
    if m in E.PROPORTION and b.get("performed") and plots:
        parts.append("For single-group proportions there is no meaningful null value, so funnel plots were drawn "
                     "without significance contours and asymmetry is difficult to interpret.")
    excl = es.get("excluded") or []
    if excl:
        n_val = sum(1 for e in excl if str(e.get("reason", "")).startswith("validálási hiba"))
        parts.append("%d row%s could not be analysed and %s excluded%s." % (
            len(excl), "" if len(excl) == 1 else "s", "was" if len(excl) == 1 else "were",
            (" (%d because of data errors found during validation)" % n_val) if n_val else ""))
    parts.append("Analyses were conducted with metaelemzes v%s (Python; validated against the R package metafor)."
                 % out["engine"]["version"])
    return " ".join(parts)


def _join_en(items):
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ------------------------------------------------------------------ riport
def _filter_lines(out):
    flt = (out.get("input") or {}).get("filters") or {}
    rep = flt.get("report") or []
    if not (rep or flt.get("exclude") or flt.get("include")):
        return []
    L = []
    head = "> **Szűrés (érzékenységi elemzés):**"
    if flt.get("n_rows_read") is not None and flt.get("n_rows_kept") is not None:
        head += " %d sorból %d maradt." % (flt["n_rows_read"], flt["n_rows_kept"])
    L.append(head)
    if rep:
        for fr in rep:
            L.append("> - %s `%s`: %d sor kizárva%s" % (
                "--include" if fr.get("mode") == "include" else "--exclude", fr["filter"].replace("`", "'"),
                fr.get("removed", 0),
                (" (%s)" % ", ".join(_md(x) for x in fr.get("removed_labels") or [])) if fr.get("removed_labels") else ""))
    else:
        for c in flt.get("include") or []:
            L.append("> - --include `%s`" % str(c).replace("`", "'"))
        for c in flt.get("exclude") or []:
            L.append("> - --exclude `%s`" % str(c).replace("`", "'"))
    L.append("")
    return L


def _totals_rows(out, m):
    t = out.get("totals") or {}
    L = []
    if not t:
        return L
    if t.get("participants") is not None:
        L.append("| Résztvevők (N, elemzett vizsgálatok) | %s |" % _int(t["participants"]))
    elif t.get("participants_partial") is not None:
        L.append("| Résztvevők (N) | ≥ %s (csak %d/%d vizsgálatnál ismert) |" % (
            _int(t["participants_partial"]), t.get("participants_known_k", 0), t.get("k", 0)))
    else:
        L.append("| Résztvevők (N) | – (az adatban nincs mintanagyság) |")
    if m in E.BINARY and t.get("events1") is not None and t.get("n1"):
        L.append("| Események: 1. kar / 2. (kontroll) kar | %s/%s (%.1f%%) / %s/%s (%.1f%%) |" % (
            _int(t["events1"]), _int(t["n1"]), 100.0 * t["events1"] / t["n1"],
            _int(t["events2"]), _int(t["n2"]), 100.0 * t["events2"] / t["n2"] if t["n2"] else float("nan")))
    elif m in E.CONTINUOUS and t.get("n1") is not None:
        L.append("| Résztvevők karonként (1. / 2. kar) | %s / %s |" % (_int(t["n1"]), _int(t["n2"])))
    elif m in E.PROPORTION and t.get("events") is not None and t.get("participants"):
        L.append("| Események / résztvevők (nyers) | %s/%s |" % (_int(t["events"]), _int(t["participants"])))
    ab = t.get("absolute_per_1000")
    if ab:
        d = ab["difference"]
        nd = 0 if max(abs(x) for x in d if x is not None) >= 10 else 1
        L.append("| Abszolút hatás / 1000 fő [%d%% CI]¹ | %s [%s; %s] (feltételezett kontrollkockázat: %.1f/1000%s) |" % (
            _lv(out["options"]), _f(d[0], nd), _f(d[1], nd), _f(d[2], nd), ab["assumed_control_risk_per_1000"],
            "; **az RD ezzel nem összeegyeztethető** — lásd az értelmezési figyelmeztetéseket"
            if ab.get("incompatible_with_baseline") else ""))
    return L


def _int(v):
    if isinstance(v, float) and v.is_integer():
        return "%d" % v
    return "%s" % v


def build_report(out, title=None, date=None, plots=True):
    """plots=False (--no-plots): a riport nem hivatkozik a nem elkészült SVG-kre."""
    o = out["options"]
    m = o["measure"]
    lv = _lv(o)
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
    L += _filter_lines(out)
    v = out["validation"]["summary"]
    es = out["effect_sizes"]
    n_val = sum(1 for e in es.get("excluded") or [] if str(e.get("reason", "")).startswith("validálási hiba"))
    L.append("> **Validálás:** %d hiba, %d figyelmeztetés, %d megjegyzés. %s" % (
        v["error"], v["warning"], v["info"],
        ("A hibás sorok (%d) kimaradtak az elemzésből — lásd lent." % n_val) if n_val else
        ("A hibák miatt az elemzés nem teljes — lásd lent." if v["error"] else "")))
    L.append("")
    if out.get("primary") is None:
        L.append("Nincs elemezhető vizsgálat.")
        if es.get("excluded"):
            L.append("")
            L.append("Kizárt sorok:")
            for ex in es["excluded"]:
                L.append("- %s — %s" % (_md(ex["study"]), _md(ex["reason"])))
        return "\n".join(L)
    pr = out["primary"]
    pm = out["primary_model"]
    bt = out["back_transformed"]
    nh = _nh(out)
    pft = _pft(o)
    fixed_model = o.get("model") == "fixed"
    d = bt["estimate_ci"]
    L.append("## Fő eredmény")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append("| Hatásméret | %s |" % es["label"])
    L.append("| Vizsgálatok száma (k) | %d%s |" % (es["k"], (" (%d sor kizárva, lásd lent)" % len(es["excluded"]))
                                                   if es.get("excluded") else ""))
    L += _totals_rows(out, m)
    model_txt = _model_hu(pr, pm)
    fb = out.get("model_fallback")
    if fb:
        model_txt += " — k = 1: a kért %s modell nem illeszthető, nincs összesítés" % (
            "véletlen hatású" if fb.get("requested") == "random" else "IVhet")
    L.append("| Modell | %s |" % model_txt)
    L.append("| %s [%d%% CI] | **%s** |" % ("Becslés (egyetlen vizsgálat)" if fb else "Összesített becslés", lv, _tri(d)))
    stat = pr.stat if pr.stat is not None and not (isinstance(pr.stat, float) and math.isnan(pr.stat)) else None
    L.append("| p | %s (%s = %s%s) |" % (_p(pr.p), pr.test, ("%.2f" % stat) if stat is not None else "–",
                                        (", df = %d" % pr.df) if pr.df else ""))
    if bt.get("pi"):
        pdf = {"t_k-2": "t(k−2 = %s)", "t_k-1": "t(k−1 = %s)"}.get(pr.pi_method, "z%s")
        L.append("| Predikciós intervallum [%d%%, %s] | %s |" % (
            lv, pdf % (pr.pi_df if pr.pi_method != "z" else ""), _pair(bt["pi"][0], bt["pi"][1])))
    h = pr.heterogeneity or {}
    qp_note = None
    if pr.k >= 2:
        L.append("| Q (df), p | %.2f (%d), %s |" % (pr.Q, pr.Q_df, _p(pr.p_Q)))
        i2ht = h.get("I2_ci_HT")
        L.append("| I² (Q-alapú) [%d%% CI, Higgins–Thompson] | %.1f%% %s |" % (
            lv, pr.I2, ("[%.1f; %.1f]" % tuple(i2ht)) if i2ht else "(CI nem számolható)"))
        if h.get("H") is not None:
            hci = h.get("H_ci_HT")
            L.append("| H (Q-alapú) [%d%% CI, Higgins–Thompson%s] ; módosított H² | %s %s ; %s |" % (
                lv, "" if h.get("H_ci_centre", "truncated") == "truncated" else ", középpont ½·ln(Q/df)", _f(h["H"], 2),
                ("[%s; %s]" % (_f(hci[0], 2), _f(hci[1], 2))) if hci else "(CI nem számolható)", _f(h.get("H2_M"), 3)))
        if pm in ("random", "ivhet"):
            i2t, i2qp = h.get("I2_from_tau2"), h.get("I2_ci_QP")
            if i2t is not None:
                L.append("| I² (τ²-alapú, mint a metafor) [%d%% CI, Q-profile] | %.1f%% %s |" % (
                    lv, i2t, ("[%.1f; %.1f]" % tuple(i2qp)) if i2qp else "(CI nem számolható)"))
            tci = h.get("tau2_ci_QP")
            L.append("| τ² [%d%% CI, Q-profile] ; τ | %s %s ; %s |" % (
                lv, _f(pr.tau2, 4), ("[%s; %s]" % (_f(tci[0], 4), _f(tci[1], 4))) if tci else "", _f(pr.tau, 4)))
            # a Q-profile CI nem a DL (vagy más nem-REML) becslőhöz tartozik: a pontbecslés kívül eshet rajta
            if tci and pr.tau2 is not None and None not in tci and (
                    pr.tau2 < tci[0] - 1e-10 * max(1.0, tci[0]) or pr.tau2 > tci[1] + 1e-10 * max(1.0, tci[1])):
                qp_note = ("A τ²-pontbecslés (%s) és a τ²-alapú I² kívül esik a Q-profile CI-n: a Q-profile CI nem "
                           "ebből a becslőből származik (a metafor confint() ugyanezt adja). Erős heterogenitásnál a "
                           "%s alulbecsülheti a τ²-et; érzékenységi elemzésként a REML vagy PM becslő ajánlott."
                           % (pr.tau2_method or "?", pr.tau2_method or "becslő"))
    if bt.get("scale_note"):
        L.append("")
        L.append("_%s_" % bt["scale_note"])
    if qp_note:
        L.append("")
        L.append("_%s_" % qp_note)
    if (out.get("totals") or {}).get("absolute_per_1000"):
        L.append("")
        L.append("_¹ %s_" % out["totals"]["absolute_per_1000"]["note"])
    L.append("")
    fx = out.get("fixed")
    if fx is not None and pm != "fixed":
        L.append("Érzékenység — közös hatású modell [%d%% CI]: %s." % (
            lv, _tri(_bt(m, (fx.estimate, fx.ci_lower, fx.ci_upper), nh, fx.se, pft))))
    rnd = out.get("random")
    if rnd is not None and pm in ("ivhet", "fixed"):
        L.append("Érzékenység — véletlen hatású modell (%s τ², %s CI) [%d%% CI]: %s." % (
            rnd.tau2_method, CI_NAMES_HU.get(rnd.ci_method, rnd.ci_method), lv,
            _tri(_bt(m, (rnd.estimate, rnd.ci_lower, rnd.ci_upper), nh, rnd.se, pft))))
    for key, name in (("mh", "Mantel–Haenszel"), ("peto", "Peto")):
        r = out.get(key)
        if r is not None:
            dd = _bt(m if key == "mh" else "OR", (r.estimate, r.ci_lower, r.ci_upper))
            L.append("%s [%d%% CI]: %s, %s." % (name, lv, _tri(dd), _p_eq(r.p)))
    L.append("")
    L.append("## Értelmezési figyelmeztetések")
    L.append("")
    warns = list(out.get("warnings") or [])
    if pm == "random" and bt.get("pi") and d[1] is not None and m not in E.PROPORTION:
        null = 1.0 if m in ("OR", "RR", "ROM") else 0.0
        if (bt["pi"][0] - null) * (bt["pi"][1] - null) < 0 and (d[1] - null) * (d[2] - null) > 0:
            warns.append("A CI nem tartalmazza a nullhatást, de a predikciós intervallum igen: egy új "
                         "környezetben a hatás iránya is eltérhet.")
    i2_vals = [("Q-alapú", pr.I2)]
    if pm in ("random", "ivhet") and h.get("I2_from_tau2") is not None:
        i2_vals.append(("τ²-alapú", h["I2_from_tau2"]))
    if pr.k >= 2 and max(v_ for _, v_ in i2_vals) >= 75:
        warns.append("Jelentős heterogenitás (%s; I² ≥ 75%%): az összesített becslés önmagában félrevezető lehet; "
                     "vizsgáld a heterogenitás forrásait (előre tervezett alcsoportok / meta-regresszió)."
                     % ", ".join("I² %s = %.0f%%" % (n_, v_) for n_, v_ in i2_vals))
    for w_ in warns:
        L.append("- %s" % _md(w_))
    if not warns:
        L.append("- Nincs külön értelmezési figyelmeztetés.")
    L.append("")
    # alcsoportok
    sg = out.get("subgroups")
    if sg is not None:
        L.append("## Alcsoport-elemzés (%s)" % _md(_col(out, "subgroup")))
        L.append("")
        if o.get("model") == "fixed":
            within = "közös hatású modell alcsoportonként"
        elif o.get("model") == "ivhet" and sg.get("common_tau2") is not None:
            within = "IVhet modell alcsoportonként, közös τ² az alcsoportokban (τ² = %s)" % _f(sg.common_tau2, 4)
        elif o.get("model") == "ivhet":
            within = "IVhet modell alcsoportonként"
        elif sg.get("common_tau2") is not None:
            within = "véletlen hatású modell, közös τ² az alcsoportokban (τ² = %s)" % _f(sg.common_tau2, 4)
        else:
            within = "véletlen hatású modell, alcsoportonként külön τ²; az egyvizsgálatos alcsoport közös hatású"
        L.append("_Modell: %s._" % within)
        L.append("")
        L.append("| Alcsoport | k | Becslés [%d%% CI] | τ² | I² (Q-alapú) |" % lv)
        L.append("|---|---|---|---|---|")
        for g in sg.groups:
            dd = g.get("display") or _bt(m, (g.estimate, g.ci_lower, g.ci_upper), g.get("n_harmonic") or nh, g.se, pft)
            # közös hatású modellnél a τ² nem becsült (0 a modell feltevése): '–', mint a fő táblázatban
            L.append("| %s | %d | %s | %s | %s |" % (_md(g.group), g.k, _tri(dd),
                                                   _f(g.tau2, 4) if g.k > 1 and not fixed_model else "–",
                                                   ("%.0f%%" % g.I2) if g.k > 1 else "–"))
        L.append("")
        if any(g.get("weight_share_pct") is not None for g in sg.groups):
            # külön táblázat: a fő alcsoport-táblázat oszlopszerkezete változatlan marad
            L.append("| Alcsoport | Súlyrészesedés a teljes modellben² | Nyers súlyösszeg |")
            L.append("|---|---|---|")
            for g in sg.groups:
                share, raw = g.get("weight_share_pct"), g.get("weight_share_raw")
                L.append("| %s | %s | %s |" % (_md(g.group), ("%.1f%%" % share) if share is not None else "–",
                                             ("%.4g" % raw) if raw is not None else "–"))
            L.append("")
            L.append("_² Az alcsoport vizsgálatainak összsúlya a teljes (alcsoportok nélküli) %s modellben, a teljes "
                     "súly %%-ában (MetaXL / RevMan 'Subtotal' súly)._" % (
                         "véletlen hatású" if o.get("model") == "random" else
                         ("IVhet" if o.get("model") == "ivhet" else "közös hatású")))
            L.append("")
        if sg.Q_between is not None:
            L.append("Alcsoport-különbség: Q_b = %.2f, df = %d, %s." % (sg.Q_between, sg.df_between, _p_eq(sg.p_between)))
        else:
            L.append("Alcsoport-különbség: nem számolható.")
        if sg.get("Q_between_note"):
            L.append("_%s._" % _md(sg.Q_between_note))
        if m == "PFT" and pft == "variance":
            L.append("_Az alcsoport-becslések visszatranszformálása a MetaXL-konvenció szerint az alcsoport-becslés "
                     "saját varianciájából számolt m = 1/Var(t) = 1/(4·SE²) értékkel (hiányzó SE esetén az alcsoport "
                     "harmonikus átlag n-jével)._")
        elif m == "PFT":
            L.append("_Az alcsoport-becslések visszatranszformálása az alcsoport saját harmonikus átlag n-jével._")
        for w_ in sg.warnings:
            L.append("- %s" % _md(w_))
        L.append("")
    mr = out.get("metaregression")
    if mr is not None:
        L.append("## Meta-regresszió")
        L.append("")
        L.append("| Együttható | Becslés | SE | %s | p | %d%% CI |" % ("t" if mr.test == "knha" else "z", lv))
        L.append("|---|---|---|---|---|---|")
        for c in mr.coefficients:
            L.append("| %s | %.4f | %.4f | %.2f | %s | [%.4f; %.4f] |" % (_md(c["name"]), c["estimate"], c["se"],
                                                                      c["stat"], _p(c["p"]), c["ci_lower"], c["ci_upper"]))
        L.append("")
        L.append("_Az együtthatók az elemzési skálán (%s) értendők._" % (
            "log" if m in ("OR", "RR", "ROM", "PLN") else ("logit" if m == "PLO" else
                                                          ("Fisher z" if m == "ZCOR" else "nyers/transzformált"))))
        L.append("")
        L.append("Reziduális τ² = %s; QE(%d) = %s, %s; moderátor-teszt (%s) %s; R² = %s." % (
            "– (közös hatású meta-regresszió, τ² = 0)" if mr.tau2_method == "FE" else _f(mr.tau2, 4),
            mr.QE_df, _f(mr.QE, 2), _p_eq(mr.QE_p), mr.QM_type, _p_eq(mr.QM_p),
            ("%s%%" % _f(mr.R2, 1)) if mr.R2 is not None else "–"))
        rb = mr.get("robust")
        if rb:
            L.append("")
            if _robust_weights(mr) == "fixed":
                wtxt = "FE-súlyok 1/v_i, mint a Stata `regress … [aw = 1/v], vce(robust)`"
            else:
                # a modell súlyai: = metafor robust(), a Stata aweight-es regress-szel csak τ² = 0-nál egyezik
                wtxt = ("súlyok a meta-regresszióé: 1/(v_i + τ²), τ² = %s (%s) — mint a metafor "
                        "`robust(…, cluster = vizsgálat, adjust = TRUE)`; a Stata `regress … [aw = 1/v], vce(robust)` "
                        "eredményével csak τ² = 0 (FE-súlyok) esetén egyezik"
                        % (_f(mr.tau2, 4), mr.tau2_method))
            L.append("**Robusztus (HC1 szendvics) standard hibák** — t(%d) próbák; %s:" % (rb["df"], wtxt))
            L.append("")
            L.append("| Együttható | Becslés | Robusztus SE | t | p | %d%% CI |" % lv)
            L.append("|---|---|---|---|---|---|")
            for c in rb["coefficients"]:
                L.append("| %s | %.4f | %.4f | %.2f | %s | [%.4f; %.4f] |" % (
                    _md(c["name"]), c["estimate"], c["se"], c["t"], _p(c["p"]), c["ci_lower"], c["ci_upper"]))
            L.append("")
            fpart = ("robusztus Wald F(%s, %s) = %s, %s; " % (rb["F_df1"], rb["F_df2"], _f(rb["F"], 2), _p_eq(rb["F_p"]))
                     if rb.get("F") is not None else "")
            L.append("%ssúlyozott R² = %s; root MSE = %s." % (fpart, _f(rb.get("r2"), 3), _f(rb.get("root_mse"), 4)))
        for w_ in mr.warnings:
            L.append("- %s" % _md(w_))
        L.append("")
    # torzítás
    b = out.get("bias", {})
    L.append("## Kis-vizsgálat hatások / publikációs torzítás")
    L.append("")
    if b.get("note"):
        L.append("> %s" % b["note"])
        L.append("")
    for key in ("binary_note", "smd_note"):
        if b.get(key):
            L.append("> %s" % _md(b[key]))
            L.append("")
    if b.get("egger") is not None:
        eg = b["egger"]
        L.append("- Egger-teszt%s: tengelymetszet = %s (SE %s; %d%% CI %s, %s-kvantilis), t(%d) = %s, %s" % (
            " (bináris kimenetnél csak tájékoztató)" if b.get("binary_note") else
            " (SMD-nél csak tájékoztató)" if b.get("smd_note") else "",
            _f(eg.intercept, 3), _f(eg.se_intercept, 3), int(round((eg.get("level") or o["level"]) * 100)),
            "[%s; %s]" % (_f(eg.ci_lower, 3), _f(eg.ci_upper, 3)),
            "z" if eg.get("ci_dist") == "norm" else "t(k−2)", eg.df, _f(eg.t, 2), _p_eq(eg.p)))
    for key, name in (("harbord", "Harbord-teszt (pontszám-alapú, log OR)"), ("peters", "Peters-teszt (log OR ~ 1/N)")):
        r = b.get(key)
        if r is None:
            continue
        if key == "harbord":
            L.append("- %s: tengelymetszet = %s (SE %s), t(%d) = %s, %s; k = %d" % (
                name, _f(r.intercept, 3), _f(r.se_intercept, 3), r.df, _f(r.t, 2), _p_eq(r.p), r.k))
        else:
            L.append("- %s: meredekség = %s (SE %s), t(%d) = %s, %s; k = %d" % (
                name, _f(r.slope, 3), _f(r.se_slope, 3), r.df, _f(r.t, 2), _p_eq(r.p), r.k))
    if b.get("begg") is not None:
        bg = b["begg"]
        L.append("- Begg–Mazumdar: Kendall τ = %s, %s; módszer: %s" % (_f(bg.kendall_tau, 3), _p_eq(bg.p), bg.method))
    if b.get("trimfill") is not None:
        tf = b["trimfill"]
        a = tf.adjusted
        trim_txt = ""
        if tf.get("trim_model") and tf.get("trim_model") != a.model:
            trim_txt = "; vágás a %s modellel" % ("közös hatású" if tf.trim_model == "fixed" else "véletlen hatású")
        L.append("- Trim-and-fill (%s%s): becsült hiányzó vizsgálat = %d (%s oldal); korrigált becslés [%d%% CI, %s]: %s" % (
            tf.estimator, trim_txt, tf.k0, "bal" if tf.side == "left" else "jobb", int(round(a.level * 100)),
            CI_NAMES_HU.get(a.ci_method, a.ci_method), _tri(_bt(m, (a.estimate, a.ci_lower, a.ci_upper), nh, a.se, pft))))
    if b.get("lfk") is not None:
        lf = b["lfk"]
        L.append("- Doi-plot, LFK-index (heurisztikus, érzékenységi jellegű mutató; nem szignifikancia-teszt): "
                 "%s — %s (|LFK| ≤ 1: nincs, 1–2: kisebb, > 2: jelentős aszimmetria%s)" % (
                     _f(lf.lfk, 2), lf.category, "; `doi.svg`" if plots else ""))
    if m in E.PROPORTION and b.get("performed") and plots:
        L.append("- Egycsoportos aránynál nincs nullhatás: a funnel plot kontúrok nélkül készült, az aszimmetria "
                 "nehezen értelmezhető.")
    if not b.get("performed"):
        L.append("- Nem készült (k < 3).")
    L.append("")
    # érzékenység
    sens = out.get("sensitivity", {})
    if sens.get("leave_one_out") or sens.get("cumulative") or sens.get("outliers") is not None:
        L.append("## Érzékenységi elemzés")
        L.append("")
    if sens.get("leave_one_out"):
        loo = sens["leave_one_out"]
        ests = [x for x in (_bt(m, [r["estimate"]], nh, r.get("se"), pft)[0] for r in loo) if x is not None]
        if ests:
            nd = decimals((min(ests), max(ests)))
            L.append("Leave-one-out: az összesített becslés tartománya %s – %s." % (_f(min(ests), nd), _f(max(ests), nd)))
        sig_change = [] if m in E.PROPORTION else [
            r["omitted"] for r in loo
            if (r["ci_lower"] > 0) != (pr.ci_lower > 0) or (r["ci_upper"] < 0) != (pr.ci_upper < 0)]
        if sig_change:
            L.append(_sentence("Szignifikancia-váltás az alábbi vizsgálat(ok) kihagyásakor: %s" % ", ".join(_md(x) for x in sig_change)))
        infl = [r["study"] for r in sens.get("influence", []) if r["influential"]]
        L.append(_sentence("Befolyásos vizsgálat (metafor-kritériumok): %s" % (", ".join(_md(x) for x in infl) if infl else "nincs")))
        outl = [r["study"] for r in sens.get("influence", []) if r["outlier_flag"]]
        if outl:
            L.append(_sentence("Kiugró (|rstudent| > 1.96): %s" % ", ".join(_md(x) for x in outl)))
        L.append("")
    elif out["effect_sizes"]["k"] < 3:
        L.append("_Leave-one-out és befolyás-elemzés nem készült (k < 3)._")
        L.append("")
    ol = sens.get("outliers")
    if ol is not None:
        L.append("### Kiugró vizsgálatok szűrése (dmetar::find.outliers szabály)")
        L.append("")
        L.append("_Kiugró az a vizsgálat, amelynek %d%%-os CI-je teljesen az összesített %d%%-os CI-n kívül esik; "
                 "egylépéses szűrés, majd újraillesztés ugyanazzal a modellel. Érzékenységi elemzés — a kiugró "
                 "vizsgálatot nem szabad automatikusan kizárni._" % (lv, lv))
        L.append("")
        if not ol.flagged:
            L.append("Nincs kiugró vizsgálat.")
        else:
            L.append(_sentence("Kiugró vizsgálat(ok) (%d): %s" % (ol.k_removed, ", ".join(_md(x) for x in ol.flagged))))
            rf = ol.refit
            if rf is not None:
                L.append("Újraillesztés nélkülük (k = %d) [%d%% CI]: %s; τ² = %s; I² (Q-alapú) = %.1f%%." % (
                    rf.k, lv, _tri(_bt(m, (rf.estimate, rf.ci_lower, rf.ci_upper), nh, rf.se, pft)),
                    "–" if fixed_model else _f(rf.tau2, 4), rf.I2))
        for w_ in ol.warnings or []:
            L.append("- %s" % _md(w_))
        L.append("")
    if sens.get("cumulative"):
        L.append("### Kumulatív metaanalízis (rendezés: %s)" % _md(_col(out, "cumulative")))
        L.append("")
        L.append("| k | Hozzáadott vizsgálat | Rendezőkulcs | Becslés [%d%% CI] | τ² | I² (Q-alapú) |" % lv)
        L.append("|---|---|---|---|---|---|")
        for r in sens["cumulative"]:
            key = r.get("key")
            L.append("| %d | %s | %s | %s | %s | %s |" % (
                r["k"], _md(r["added"]), _md(_int(key) if key is not None else "–"),
                _tri(_bt(m, (r["estimate"], r["ci_lower"], r["ci_upper"]), nh, r.get("se"), pft)),
                _f(r.get("tau2"), 4) if r["k"] > 1 and not fixed_model else "–",
                ("%.0f%%" % r["I2"]) if r.get("I2") is not None and r["k"] > 1 else "–"))
        L.append("")
        L.append("_Az első sor egyetlen vizsgálat (közös hatású); a további sorok a(z) %s modell szerint._"
                 % ("véletlen hatású" if o.get("model") == "random" else
                    ("IVhet" if o.get("model") == "ivhet" else "közös hatású")))
        L.append("")
    # vizsgálatok
    L.append("## Vizsgálatonkénti hatásméretek")
    L.append("")
    if plots:
        L.append("Lásd: `effect_sizes.csv`, `forest.svg`, `funnel.svg`%s." % (
            ", `doi.svg`" if (out.get("bias") or {}).get("lfk") is not None else ""))
    else:
        L.append("Lásd: `effect_sizes.csv` (ábrák nem készültek: --no-plots).")
    if es["excluded"]:
        L.append("")
        L.append("Kizárt sorok:")
        for ex in es["excluded"]:
            L.append("- %s — %s" % (_md(ex["study"]), _md(ex["reason"])))
    L.append("")
    # validálás
    fnd = out["validation"]["findings"]
    if fnd:
        L.append("## Adatvalidálás")
        L.append("")
        L.append("| Kód | Súlyosság | Vizsgálat | Részlet | Teendő |")
        L.append("|---|---|---|---|---|")
        for f in fnd:
            L.append("| %s | %s | %s | %s | %s |" % (f["code"], f["severity"], _md(f["study"] or "–"),
                                                   _md(f["detail"] or f["title"]), _md(f["advice"])))
        L.append("")
    L.append("## Methods (angol, kéziratba)")
    L.append("")
    L.append(methods_text_en(out, plots=plots))
    L.append("")
    L.append("---")
    L.append("_Automatikusan generált riport. Az eredményeket az ellenőrző (reviewer) ágens és a szerző "
             "validálja; klinikai következtetés csak a bizonyosság (GRADE) értékelése után vonható le._")
    return "\n".join(L)
