#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CPU-parallel differential testing of the meta-analysis engine against R/metafor.

    python3 tests/fuzz/run_fuzz.py --n 2000 --jobs 4 --seed 1 --out <dir> [--only rma,escalc,...]

Pipeline: gen.py generates N seeded datasets -> the index range is split into chunks -> each
chunk runs in a spawn-safe worker process, which (1) runs run_metafor.R on the chunk and (2)
runs the engine on the same datasets and compares field by field -> the parent merges the
results, classifies every mismatch by (function, field, condition), subtracts the documented
KNOWN_DIFFERENCES and writes summary.json + summary.md to --out (default: a fresh temp dir).

Replay / triage:
    --ids 20261004-00042,20261004-00977   re-run exactly these generated datasets (verbose)
    --datasets file.json                  run hand-written / minimized datasets (list of dicts)
    --minimize 5                          greedily shrink one example dataset of each of the
                                          first 5 unexplained classes (drops studies while the
                                          same function/field still mismatches)

Exit status: 0 = no unexplained mismatch (or skipped: Rscript/metafor/jsonlite missing),
             1 = unexplained mismatches, 2 = harness failure (R crashed, engine import failed).
Stdlib only (the oracle needs R with metafor and jsonlite).
"""
import argparse
import collections
import json
import math
import multiprocessing as mp
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # .../metaanalizis-asszisztens
R_SCRIPT = os.path.join(HERE, "run_metafor.R")
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gen  # noqa: E402  (tests/fuzz/gen.py)
import exact  # noqa: E402  (tests/fuzz/exact.py)

GROUPS = ("escalc", "rma", "confint", "regtest", "ranktest", "trimfill", "leave1out", "influence",
          "mods", "subgroup", "cumul", "mh", "peto", "harbord", "peters", "rma_es")
METHODS = ("FE", "DL", "REML", "ML", "PM", "HE", "SJ")
TESTS = ("z", "t", "knha", "adhoc")
CI_MAP = {"z": "z", "t": "t", "knha": "hksj", "adhoc": "hksj_adhoc"}

# --------------------------------------------------------------------------- tolerances
# |engine - metafor| <= rtol * max(|engine|, |metafor|) + atol * scale
# scale: "vmed" = median(vi) of the dataset, "sqrt_vmed" = its square root, None = 1.
# The relative tolerance is 1e-6 everywhere; the absolute floors only matter for values that are
# zero up to rounding (identical yi, tau2 at the boundary, symmetric data ...).
ITERATIVE_RTOL = 1e-5   # floor for fields of REML / ML / PM fits (see the 'iterative' note in TOLERANCES)
ITERATIVE_FUNC = re.compile(r"(REML|ML|PM)")
TOLERANCES = {
    "loc": dict(rtol=1e-6, atol=1e-9, scale="sqrt_vmed",
                reason="estimates, SEs, CI/PI bounds, coefficients (effect-size scale). Absolute floor "
                       "1e-9*sqrt(median vi): an estimate that is 0 up to rounding (e.g. identical "
                       "yi = 0) is ~1e-17 in one program and exactly 0 in the other."),
    "var": dict(rtol=1e-6, atol=1e-8, scale="vmed",
                reason="tau2 and other variance-scale values. Both programs stop the tau2 iterations at an "
                       "absolute step of ~1e-10..1e-12*median(vi) (metafor control threshold/tol are set to "
                       "1e-12*median(vi) by run_metafor.R) and the engine snaps tau2 < 1e-10*median(vi) to "
                       "0, so a boundary tau2 can be 0 vs 1e-14."),
    "stat": dict(rtol=1e-6, atol=1e-8, scale=None, inf_above=1e12,
                 reason="z/t/Q/QM/F statistics (dimensionless); floor for statistics that are 0 up to rounding. "
                        "|stat| >= 1e12 counts as +-Inf: it only arises from an SE that is 0 up to rounding "
                        "(e.g. identical yi with Knapp-Hartung: q = 1e-33 in one program, exactly 0 in the "
                        "other); the p-value is 0 either way."),
    "p": dict(rtol=1e-5, atol=1e-12, scale=None, log_rtol=1e-5,
              reason="p-values (LOOSER: 1e-5). A p-value S(x) inherits the 1e-6 relative tolerance of its "
                     "statistic x amplified by |x S'(x)/S(x)| (about 1-3 for moderate p, about z^2 in the "
                     "tail), so it is compared at 1e-5 relative, or, in the tail, at 1e-5 relative on log(p) "
                     "(e.g. Q_E = 24.0202118558 vs 24.0202118526 already moves p by 1.6e-6)."),
    "pct": dict(rtol=1e-6, atol=1e-6, scale=None,
                reason="I2 in percent; the floor (1e-8 on the proportion scale) absorbs a boundary tau2 "
                       "of 1e-9*median(vi) (see 'var')."),
    "ratio": dict(rtol=1e-6, atol=1e-9, scale=None,
                  reason="dimensionless diagnostics (H2, hat, cook.d, dffits, rstudent, cov.r, Kendall tau)."),
    "infl": dict(rtol=1e-5, atol=1e-8, scale=None,
                 reason="(LOOSER: 1e-5) leave-one-out influence diagnostics (rstudent, dffits, cook.d, cov.r, "
                        "dfbetas) are scaled DIFFERENCES between the full and the reduced fit; when one study "
                        "barely moves the estimate, the 1e-6 agreement of both fits becomes ~1e-5 for the difference."),
    "es": dict(rtol=1e-6, atol=1e-12, scale=None,
               reason="escalc yi/vi: closed forms; floor only for yi = 0 exactly."),
    "iterative": dict(rtol=ITERATIVE_RTOL, atol=0.0, scale=None,
                      reason="(LOOSER: 1e-5) not a field kind but a floor on rtol for every field of a REML / ML / "
                             "PM fit: both programs stop iterating on a step-size criterion, which in a flat "
                             "likelihood leaves ~1e-6..1e-5 relative error in tau2 and everything derived from it "
                             "(the repo's own metafor reference test uses 2e-6 for these)."),
    "count": dict(rtol=0.0, atol=0.0, scale=None, reason="integers (k0, k): exact."),
    "exact": dict(rtol=0.0, atol=0.0, scale=None, reason="strings / booleans (side, is.infl): exact."),
}


# --------------------------------------------------------------------------- known differences
def _tag(name):
    return lambda rec: name in rec["tags"]


def _idx0(rec):
    return rec.get("idx") == 0


# Each entry: id, func (regex on the function key), field (regex), optional cond (callable on
# the mismatch record), reason, source (where the engine documents / implements the convention).
KNOWN_DIFFERENCES = [
    dict(id="I2_HT_leave1out", func=r"^leave1out:(REML|DL|PM)_", field=r"^I2$",
         reason="leave_one_out() reports the Higgins-Thompson I2 = (Q-df)/Q of each reduced fit (the "
                "engine's I2 convention, as in R meta); metafor's random-effects leave1out() reports "
                "100*tau2/(tau2+s2). Identical for FE (checked).",
         source="metaelemzes/sensitivity.py leave_one_out(): 'I2': r.I2; models.meta_analysis(): I2=het['I2']"),
    dict(id="I2_HT_cumul", func=r"^cumul:", field=r"^I2$",
         reason="cumulative() reports the Higgins-Thompson I2 (Q-based); metafor cumul() reports the "
                "tau2-based I2 for random-effects models.",
         source="metaelemzes/sensitivity.py cumulative(): 'I2': r.I2"),
    dict(id="cumul_first_step", func=r"^cumul:", field=r"^(ci_lower|ci_upper)$", cond=_idx0,
         reason="the first cumulative step (a single study) is reported by the engine as a common-effect "
                "z-interval; metafor refits the requested model (knha with k=1: df=0 -> NaN CI).",
         source="metaelemzes/sensitivity.py cumulative(): `if n == 1: meta_analysis(y, v, 'fixed', ci_method='z')`"),
    dict(id="RD_no_cc_only0", func=r"^escalc:RD_def$", field=r"^(yi|vi)$",
         cond=lambda rec: "zero_cell" in rec["tags"] or "double_zero" in rec["tags"] or "double_full" in rec["tags"],
         reason="RD: the engine applies no continuity correction for zero cells by default (Cochrane "
                "Handbook 10.4.4.1: RD is estimable with zero cells); metafor escalc(to='only0') adds 1/2 "
                "to all cells of such tables.",
         source="metaelemzes/effect_sizes.py two_by_two(): RD branch comment"),
    dict(id="RD_zero_variance_cc", func=r"^escalc:RD_(def|none)$", field=r"^row$", cond=_tag("metafor_NA"),
         reason="RD tables with zero variance (both arms 0 % or 100 %): the engine keeps the study with "
                "a variance computed after +cc (to avoid an infinite weight); metafor (to='none' or "
                "default for 0%/100% double tables) returns vi = 0, which cannot be pooled.",
         source="metaelemzes/effect_sizes.py two_by_two(): 'RD variancia ... folytonossági korrekcióval (0 variancia)'"),
    dict(id="QEp_df0", func=r"^(mh|peto):", field=r"^(p_Q|H2)$", cond=_tag("engine_NA"),
         reason="with <= 1 informative table the heterogeneity test is undefined: the engine returns None, "
                "metafor reports QEp = 1 and H2 = 1.",
         source="metaelemzes/models.py mantel_haenszel()/peto(): p_Q=None if df == 0, H2=None"),
    dict(id="PI_t_k-2_k2", func=r"^rma:", field=r"^pi_(lower|upper)\[t_k-2\]$", cond=_tag("k=2"),
         reason="t(k-2) prediction interval with k = 2 has 0 df: the engine returns None (with a warning); "
                "metafor predict(pi.type='riley') silently clamps the df to 1.",
         source="metaelemzes/models.py meta_analysis(): 'Predikciós intervallum t(k-2)-vel k >= 3 esetén'"),
    dict(id="loo_k_lt_3", func=r"^(leave1out|influence):", field=r"^#rows$",
         cond=lambda rec: rec["metafor"] not in (0, None) and rec["engine"] == 0,
         reason="leave_one_out()/influence() return [] for k < 3 by design (single-study refits are "
                "meaningless); metafor computes them.",
         source="metaelemzes/sensitivity.py leave_one_out()/influence(): `if k < 3: return []`"),
    dict(id="R2_FE_mods", func=r"^mods:FE_", field=r"^R2$", cond=_tag("engine_NA"),
         reason="for a common-effect meta-regression the engine reports no R2 (its R2 is the proportional "
                "reduction of tau2); metafor 4.x reports the adjusted R2 of the weighted lm(yi ~ X).",
         source="metaelemzes/moderators.py meta_regression(): `if has_int and method != 'FE'`"),
    dict(id="egger_perfect_fit", func=r"^regtest:", field=r"^(t|p|ci_lower|ci_upper|se_intercept|intercept)$",
         cond=_tag("identical_yi"),
         reason="identical yi give an exact fit z_i = c*prec_i; the engine treats the rounding-noise residual "
                "variance as 0 and returns t = NaN, metafor's lm() returns a finite t from the noise.",
         source="metaelemzes/bias.py egger_test(): 'tökéletes illeszkedés' (perfect fit) branch"),
    dict(id="trimfill_degenerate", func=r"^trimfill:", field=r"^!engine_error$",
         cond=lambda rec: "identical_yi" in rec["tags"] or "equal_vi" in rec["tags"],
         reason="identical yi (no asymmetry, L0/R0 react only to rounding noise) or equal vi (the side "
                "regression y ~ sqrt(v) is collinear): the engine refuses; metafor returns a "
                "noise-dependent result.",
         source="metaelemzes/bias.py trim_and_fill(): 'a hatásméretek azonosak' / 'a mintavételi varianciák azonosak'"),
    dict(id="subgroup_common_tau2_fallback", func=r"^subgroup:.*_common$", field=r"^!metafor_error$",
         cond=lambda rec: any("Number of parameters" in t for t in rec["tags"]),
         reason="k <= number of subgroups: metafor cannot fit rma(mods = ~ group); the engine falls back to "
                "separate tau2 per subgroup with a warning.",
         source="metaelemzes/moderators.py subgroup_analysis(): 'Közös τ² nem becsülhető (k = ... <= alcsoportok száma'"),
    dict(id="se_k0_negative_var", func=r"^trimfill:.*_L0$", field=r"^se_k0$", cond=_tag("metafor_NA"),
         reason="L0: the variance of S_r can be negative for small k; the engine clamps it to 0 (se_k0 = 0), "
                "metafor takes sqrt() of it (NaN).",
         source="metaelemzes/bias.py trim_and_fill(): `math.sqrt(max(0.0, var_sr))`"),
    dict(id="oracle_l1o_nonconv", kind="oracle", func=r"^(leave1out|influence):(REML|ML)", field=r".*",
         cond=_tag("metafor_NA"),
         reason="ORACLE limitation: metafor's Fisher scoring did not converge for this leave-one-out refit "
                "(leave1out()/influence() return NA); the engine's value is not verified by metafor here.",
         source="run_metafor.R: leave1out()/influence() reuse the fit's control (no retry possible)"),
    dict(id="trimfill_trim_to_one", kind="noise", func=r"^trimfill:", field=r".*", cond=_tag("trim_to_1"),
         reason="k - k0 <= 1: the trimmed estimate equals the single remaining study, whose centred value is "
                "0 up to rounding; its sign (and so S_r, k0, se_k0) is decided by floating-point noise "
                "in either program.",
         source="metaelemzes/bias.py trim_and_fill(): rank-sign step (sign(0) = 0)"),
    dict(id="R2_noise_tau2_RE_zero", kind="noise", func=r"^mods:", field=r"^R2$", cond=_tag("tau2_RE~0"),
         reason="the intercept-only tau2 is 0 up to the convergence threshold: metafor's R2 = "
                "(tau2_RE - tau2)/tau2_RE is a ratio of two rounding-level numbers (e.g. 4e-13 vs 1e-13 -> "
                "65 %); the engine snaps tau2_RE to 0 and reports R2 = 0.",
         source="metaelemzes/moderators.py meta_regression(): R2 block; models.optimize_tau2(): tau2 < 1e-10*scale -> 0"),
    dict(id="oracle_inexact", kind="oracle", func=r"^(mods|subgroup):", field=r".*", cond=_tag("exact=engine"),
         reason="ORACLE limitation: on ill-conditioned data (variance ratio >= 1e7) metafor's value differs from "
                "the exact rational-arithmetic result (tests/fuzz/exact.py) while the engine's agrees with it.",
         source="tests/fuzz/exact.py (exact WLS / Q_E / tr(P) / DL)"),
    dict(id="illconditioned_engine_closer", kind="noise", func=r"^(mods|subgroup):", field=r".*",
         cond=_tag("exact=neither:engine"),
         reason="ill-conditioned data: neither program reproduces the exact rational-arithmetic value to 1e-6, "
                "but the engine is the closer one.",
         source="tests/fuzz/exact.py"),
    dict(id="oracle_lower_likelihood", kind="oracle",
         func=r"^(rma|trimfill|leave1out|influence|cumul|subgroup|mods|rma_es):", field=r".*",
         cond=lambda rec: "eng_ll_higher" in rec["tags"] and "mf_ll_higher" not in rec["tags"],
         reason="ORACLE limitation: the engine's tau2 has a strictly higher (restricted) log-likelihood than "
                "metafor's (Fisher scoring stopped early / at a local maximum, or reset to 0), so the engine "
                "is the better maximizer.",
         source="run_fuzz.py ll_tags(): models.reml_loglik / ml_loglik, moderators.mr_loglik at both tau2"),
    dict(id="tau2_snap_zero", func=r"^(rma|trimfill|leave1out|influence|cumul|subgroup|mods|rma_es):",
         field=r".*", cond=_tag("tau2_snap"),
         reason="the engine snaps a (RE)ML tau2 below 1e-10*median(vi) to exactly 0 (metafor keeps e.g. 2e-12). "
                "Only visible with variance ratios >= ~1e6, where such a tau2 is not negligible next to the "
                "smallest vi (relative effect on z / cook.d / cov.r ~1e-6..1e-5).",
         source="metaelemzes/models.py optimize_tau2(): `if best_t < 1e-10 * scale: best_t = 0.0`"),
    dict(id="illconditioned_rounding", kind="noise", func=r"^(mods|subgroup|rma|influence|leave1out|cumul|trimfill):",
         field=r"^(?!!)",
         cond=lambda rec: (any(t in rec["tags"] for t in ("collinear", "vratio>=1e7", "vratio>=1e6", "df_res<=1"))
                           and not any(t.startswith("exact=") and "metafor" in t for t in rec["tags"])
                           and "mf_ll_higher" not in rec["tags"] and "rel>1e-4" not in rec["tags"]),
         reason="ill-conditioned input (near-collinear moderators: cond(X'WX) ~ 1e8, or sampling-variance "
                "ratio >= 1e6): both programs lose digits; differences <= 1e-4 relative that the exact "
                "adjudicator does not attribute to the engine are rounding.",
         source="gen.py 'collinear' moderators / 'extreme' variances; tests/fuzz/exact.py"),
    dict(id="rank_deficient_design", func=r"^mods:", field=r"^!engine_error$", cond=_tag("mf_dropped_predictors"),
         reason="rank-deficient moderator matrix: metafor silently drops the redundant predictors (warning), the "
                "engine refuses with SingularMatrixError.",
         source="metaelemzes/moderators.py meta_regression() -> linalg.inverse(): SingularMatrixError"),
    dict(id="oracle_nonconvergence", kind="oracle", func=r".*", field=r"^!metafor_error$",
         cond=lambda rec: any("did not converge" in t for t in rec["tags"]),
         reason="ORACLE limitation: metafor's Fisher scoring did not converge even after the run_metafor.R "
                "retries (looser threshold, step halving); the engine's value is unverified here.",
         source="run_metafor.R fit_rma() / fit_rma_sa()"),
    dict(id="flat_likelihood", kind="noise", func=r"(REML|ML)", field=r"^(?!!)",
         cond=lambda rec: "ll_equal" in rec["tags"] and "rel>1e-4" not in rec["tags"],
         reason="the two tau2 values have the same (RE)ML log-likelihood to 1e-9: the likelihood is flat there "
                "and the step-size stopping rules leave a small (< 1e-4 relative) difference.",
         source="run_fuzz.py ll_tags()"),
    dict(id="fallback_precision", kind="noise", func=r"(REML|ML)", field=r"^(?!!)",
         cond=lambda rec: ("eng_fs_fallback" in rec["tags"] and "rel>1e-4" not in rec["tags"]
                           and "mf_ll_higher" not in rec["tags"]),
         reason="the engine's Fisher scoring did not converge and the tau2 came from its profile-likelihood "
                "grid + golden-section fallback at the same maximum as metafor; the remaining < 1e-4 relative "
                "difference is the precision of that fallback (amplified in CI bounds near 0).",
         source="metaelemzes/models.py optimize_tau2(): info['fallback']"),
    dict(id="mh_zero_variance", func=r"^mh:", field=r"^(se|ci_lower|ci_upper|stat|p)$",
         cond=lambda rec: "metafor_NA" in rec["tags"] and ("has_double_full" in rec["tags"] or "has_double_zero" in rec["tags"]),
         reason="degenerate tables only (e.g. the single informative table is 100 % vs 100 %): the pooled MH "
                "variance is exactly 0; the engine reports se = 0 (CI = estimate) with a warning, metafor NA.",
         source="metaelemzes/models.py mantel_haenszel(): 'Nulla összesített variancia ... esetén se = 0'"),
    dict(id="REML_global_search", func=r"^(rma|trimfill|leave1out|influence|cumul|subgroup|mods|rma_es):",
         field=r".*", cond=lambda rec: "eng_global_search" in rec["tags"] and "mf_ll_higher" not in rec["tags"],
         reason="the engine checks the (RE)ML profile likelihood on a grid and takes the global maximum; "
                "metafor's Fisher scoring stops at a local maximum (the engine warns about this).",
         source="metaelemzes/models.py optimize_tau2() / tau2_info_warnings(): 'global_search'"),
]


def _rel(rec):
    try:
        a, b = float(rec["engine"]), float(rec["metafor"])
    except (TypeError, ValueError):
        return math.inf
    if math.isnan(a) or math.isnan(b) or math.isinf(a) or math.isinf(b):
        return math.inf
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


# Unexplained classes that triage attributed to an engine defect: they still count as FAIL (so the
# run keeps failing until the engine is fixed), but the summary groups them under these ids.
SUSPECTED_DEFECTS = []

# Defects fixed in the engine (record only; NOT used for classification). A mismatch that the old
# condition would still match is reported as an ordinary (untriaged / known) class, never hidden.
FIXED_DEFECTS = [
    dict(id="D1_reml_fallback",
         reason="(RE)ML tau2 = 0 below the interior maximum: oscillating Fisher scoring, and the grid fallback "
                "never refined between 0 and the first grid point.",
         fix="models.optimize_tau2(): damped (stepadj 0.5) Fisher-scoring retry; golden-section refinement on "
             "[0, first grid point] when the boundary is only a candidate",
         test="tests/test_fixes_R2b_numerics.py TestD1RemlBoundary"),
    dict(id="D2_mr_numerics",
         reason="meta-regression tr(P) = sum(w) - tr(M X'W^2X), DL tau2, I2_res and WLS via the explicit inverse "
                "of X'WX lost digits on ill-conditioned designs.",
         fix="linalg.WeightedQR (Householder QR of W^1/2 X): WLS, (X'WX)^-1, log det, tr(P) = sum w_i (1 - h_ii), "
             "tr(PP) from non-negative terms; QM via the Schur complement (no explicit inverse)",
         test="tests/test_fixes_R2b_numerics.py TestD2MetaRegressionNumerics (exact.py reference values)"),
    dict(id="D3_knha_perfect_fit",
         reason="Knapp-Hartung meta-regression on an exact fit raised SingularMatrixError ('kollineáris "
                "moderátorok?') or reported noise-driven t/F/p.",
         fix="moderators.meta_regression(): perfect-fit guard (weighted RSS <= 1e-20 sum(w y^2)): se = 0, "
             "t = +-Inf (0/0 = NaN for coefficients that are 0 up to rounding), QM = None, warning",
         test="tests/test_fixes_R2b_numerics.py TestD3KnhaPerfectFit"),
    dict(id="D4_begg_identical",
         reason="Begg test on identical yi gave noise-driven tau = +-1.",
         fix="bias.begg_test(): relative-tolerance 'identical effects' guard (round 2, DT-2) -> ModelError",
         test="tests/test_fixes_R2b_numerics.py TestD4BeggIdenticalEffects, tests/test_fixes_R3.py"),
]


def classify_defect(rec):
    for k in SUSPECTED_DEFECTS:
        if re.search(k["func"], rec["func"]) and re.search(k["field"], rec["field"]) and k["cond"](rec):
            return k["id"]
    return None


def known_kind(kid):
    for k in KNOWN_DIFFERENCES:
        if k["id"] == kid:
            return k.get("kind", "convention")
    return None


def classify_known(rec):
    for k in KNOWN_DIFFERENCES:
        if not re.search(k["func"], rec["func"]):
            continue
        if not re.search(k["field"], rec["field"]):
            continue
        cond = k.get("cond")
        if cond is not None and not cond(rec):
            continue
        return k["id"]
    return None


# --------------------------------------------------------------------------- value helpers
def rnum(x):
    """R JSON value -> Python (numbers, nan/inf from strings, lists recursively)."""
    if isinstance(x, list):
        return [rnum(a) for a in x]
    if isinstance(x, str):
        s = {"NA": math.nan, "NaN": math.nan, "Inf": math.inf, "-Inf": -math.inf}.get(x)
        if s is not None:
            return s
        return x
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, (int, float)):
        return float(x)
    return x


def as_list(x):
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, dict) and not x:
        return []
    return [x]


def scalar(x):
    x = rnum(x)
    if isinstance(x, list):
        return x[0] if len(x) == 1 else (None if not x else x)
    return x


def is_missing(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def match(eng, mf, kind, scales, iterative=False):
    """(ok, extra_tags)."""
    if isinstance(eng, bool) or isinstance(mf, bool) or kind == "exact":
        if is_missing(eng) and is_missing(mf):
            return True, ()
        if is_missing(eng):
            return False, ("engine_NA",)
        if is_missing(mf):
            return False, ("metafor_NA",)
        if isinstance(mf, float) and isinstance(eng, bool):
            mf = bool(mf)
        return (eng == mf), ()
    if is_missing(eng) and is_missing(mf):
        return True, ()
    if is_missing(eng):
        return False, ("engine_NA",)
    if is_missing(mf):
        return False, ("metafor_NA",)
    try:
        a, b = float(eng), float(mf)
    except (TypeError, ValueError):
        return eng == mf, ()
    t = TOLERANCES[kind]
    big = t.get("inf_above")
    if big is not None:      # |stat| beyond this is "infinite": an SE that is 0 up to rounding
        a = math.copysign(math.inf, a) if abs(a) >= big else a
        b = math.copysign(math.inf, b) if abs(b) >= big else b
    if math.isinf(a) or math.isinf(b):
        return a == b, ()
    sc = 1.0 if t["scale"] is None else scales.get(t["scale"], 1.0)
    rtol = max(t["rtol"], ITERATIVE_RTOL) if (iterative and t["rtol"] > 0) else t["rtol"]
    if abs(a - b) <= rtol * max(abs(a), abs(b)) + t["atol"] * sc:
        return True, ()
    lr = t.get("log_rtol")
    if lr and a > 0 and b > 0:
        la, lb = math.log(a), math.log(b)
        return abs(la - lb) <= lr * max(abs(la), abs(lb)), ()
    return False, ()


def short_msg(ex_or_msg):
    s = ex_or_msg if isinstance(ex_or_msg, str) else "%s: %s" % (type(ex_or_msg).__name__, ex_or_msg)
    s = re.sub(r"[-+]?\d[\d.eE+-]*", "#", s)
    return s.strip()[:70]


def jsonable(x):
    if isinstance(x, float):
        if math.isnan(x):
            return "NaN"
        if math.isinf(x):
            return "Inf" if x > 0 else "-Inf"
        return x
    if isinstance(x, (list, tuple)):
        return [jsonable(a) for a in x]
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (int, str, bool)) or x is None:
        return x
    return repr(x)


# --------------------------------------------------------------------------- accumulation
class Acc(object):
    """Per-worker accumulator: checks per (func, field) and mismatches per class and dataset."""

    def __init__(self):
        self.checks = collections.Counter()
        # (func, field, cond) -> {dataset_id: [n, example]}
        self.mism = collections.defaultdict(dict)
        self.records = []           # every mismatch (only kept with --verbose / replay)
        self.keep_records = False
        self.datasets = 0

    def add(self, rec):
        cond = ",".join(sorted(set(rec["tags"]))) or "general"
        key = (rec["func"], rec["field"], cond)
        slot = self.mism[key].get(rec["id"])
        if slot is None:
            self.mism[key][rec["id"]] = [1, {"idx": rec.get("idx"), "engine": jsonable(rec["engine"]),
                                             "metafor": jsonable(rec["metafor"])}]
        else:
            slot[0] += 1
        if self.keep_records:
            self.records.append({k: jsonable(v) for k, v in rec.items()})

    def to_dict(self):
        return {"checks": [[f, fl, n] for (f, fl), n in self.checks.items()],
                "mism": [[f, fl, c, d] for (f, fl, c), d in self.mism.items()],
                "records": self.records, "datasets": self.datasets}


class Ctx(object):
    """Comparison context of one dataset."""

    def __init__(self, acc, ds, tags=(), scales=None):
        self.acc = acc
        self.ds = ds
        self.id = ds["id"]
        self.tags = set(tags)
        self.scales = scales or {"vmed": 1.0, "sqrt_vmed": 1.0}

    def check(self, func, field):
        self.acc.checks[(func, field)] += 1

    def mismatch(self, func, field, tags, idx, eng, mf):
        rec = {"func": func, "field": field, "tags": sorted(self.tags | set(tags)), "id": self.id,
               "idx": idx, "engine": eng, "metafor": mf}
        r = _rel(rec)
        if math.isfinite(r) and r > 1e-4:     # magnitude bucket: keeps rounding-level and real
            rec["tags"] = sorted(set(rec["tags"]) | {"rel>1e-4"})   # differences in separate classes
        rec["known"] = classify_known(rec)
        self.acc.add(rec)

    def cmp(self, func, field, eng, mf, kind, idx=None, tags=(), scales=None, tagger=None):
        self.check(func, field)
        mf = scalar(mf)
        ok, extra = match(eng, mf, kind, scales or self.scales, bool(ITERATIVE_FUNC.search(func)))
        if not ok:
            more = list(tags) + list(extra)
            if tagger is not None:
                try:
                    more += list(tagger(idx))
                except Exception:      # pragma: no cover - tagging must never break a run
                    more.append("tagger_error")
            self.mismatch(func, field, more, idx, eng, mf)
        return ok

    def cmpv(self, func, field, engl, mfl, kind, tags=(), scales=None, tagger=None):
        engl = list(engl) if engl is not None else []
        mfl = as_list(rnum(mfl))
        if len(engl) != len(mfl):
            self.check(func, field + "#len")
            self.mismatch(func, field + "#len", tags, None, len(engl), len(mfl))
            return
        for i, (a, b) in enumerate(zip(engl, mfl)):
            self.cmp(func, field, a, b, kind, i, tags, scales, tagger)

    def call(self, func, thunk, r, na_keys=()):
        """Run the engine thunk; reconcile with the metafor result `r` (None / {'error': ..}).

        na_keys: metafor fields whose being all NA means "not estimable" — an engine exception
        then counts as agreement (e.g. MH OR with only double-zero tables: engine raises,
        metafor returns beta = NA)."""
        mf_err = r is None or (isinstance(r, dict) and "error" in r)
        mf_na = (not mf_err and bool(na_keys) and isinstance(r, dict)
                 and all(all(not finite(x) for x in as_list(rnum(r.get(k)))) for k in na_keys))
        try:
            e = thunk()
            e_err = None
        except Exception as ex:          # the engine's own error -> compare with metafor's state
            e, e_err = None, ex
        if e_err is not None and (mf_err or mf_na):
            self.check(func, "!status")
            return None
        if e_err is not None:
            self.check(func, "!status")
            self.mismatch(func, "!engine_error", ["msg=" + short_msg(e_err)] + mf_tags(r), None,
                          short_msg(e_err), "ok")
            return None
        if mf_err:
            self.check(func, "!status")
            msg = r.get("error", "?") if isinstance(r, dict) else "not computed"
            self.mismatch(func, "!metafor_error", ["msg=" + short_msg(str(msg))], None, "ok", str(msg)[:200])
            return None
        self.check(func, "!status")
        return e


# --------------------------------------------------------------------------- tags
def median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def uni_tags(ds):
    yi, vi = ds["yi"], ds["vi"]
    k = len(yi)
    t = set()
    if k == 2:
        t.add("k=2")
    elif k == 3:
        t.add("k=3")
    if len(set(yi)) == 1:
        t.add("identical_yi")
    if len(set(vi)) == 1:
        t.add("equal_vi")
    vr = max(vi) / min(vi)
    if vr >= 1e7:
        t.add("vratio>=1e7")       # the engine's own instability warning threshold
    elif vr >= 1e6:
        t.add("vratio>=1e6")
    return t


def eng_tags(info):
    t = []
    if not info:
        return t
    if info.get("global_search"):
        t.append("eng_global_search")
    if info.get("boundary_reset"):
        t.append("eng_boundary_reset")
    if info.get("fallback") or info.get("converged") is False:
        t.append("eng_fs_fallback")
    return t


def mf_tags(r):
    t = []
    if not isinstance(r, dict):
        return t
    w = " ".join(str(x) for x in as_list(r.get("warn")))
    if "local maximum" in w:
        t.append("mf_ll0_reset")
    if "converge" in w:
        t.append("mf_nonconv")
    if "Redundant predictors dropped" in w:
        t.append("mf_dropped_predictors")
    thr = scalar(r.get("thr"))
    if finite(thr) and thr > 1e-12:
        t.append("mf_loose_thr")
    return t


def bin_row_tags(row):
    a, n1, c, n2 = row["e1"], row["n1"], row["e2"], row["n2"]
    b, d = n1 - a, n2 - c
    if a == 0 and c == 0:
        return ["double_zero"]
    if b == 0 and d == 0:
        return ["double_full"]
    if min(a, b, c, d) == 0:
        return ["zero_cell"]
    return []


def bin_ds_tags(rows):
    t = set()
    for r in rows:
        for x in bin_row_tags(r):
            t.add("has_" + x)
    return t


def prop_row_tags(row):
    t = []
    if row["x"] == 0:
        t.append("x=0")
    if row["x"] == row["n"]:
        t.append("x=n")
    if row["n"] == 1:
        t.append("n=1")
    return t


def cor_row_tags(row):
    t = []
    if abs(row["r"]) >= 0.999:
        t.append("|r|>=0.999")
    if row["n"] <= 4:
        t.append("n<=4")
    return t


def cont_row_tags(row):
    t = []
    if min(row["n1"], row["n2"]) <= 3:
        t.append("n<=3")
    if row["m1"] <= 0 or row["m2"] <= 0:
        t.append("m<=0")
    return t


def paired_row_tags(row):
    t = []
    if row["r"] >= 0.999:
        t.append("r>=0.999")
    if row["n"] <= 3:
        t.append("n<=3")
    return t


# --------------------------------------------------------------------------- engine side
_ENGINE = {}


def engine():
    if not _ENGINE:
        from metaelemzes import effect_sizes as E, models as M, moderators as MO, bias as B, sensitivity as S
        _ENGINE.update(E=E, M=M, MO=MO, B=B, S=S)
    return _ENGINE


def subset_tagger(M, yi, vi, method, subset_of, eng_t2, mf_t2, scales):
    """Row tagger for refits on subsets (leave-one-out, cumulative): the engine's tau2-search
    flags on that subset and which program's tau2 has the higher (RE)ML likelihood there."""
    def tagger(i):
        if method not in ("REML", "ML") or i is None:
            return []
        sub = subset_of(i)
        y = [yi[j] for j in sub]
        v = [vi[j] for j in sub]
        if len(y) < 2:
            return []
        t = eng_tags(M.estimate_tau2(y, v, method)[1])
        a = eng_t2[i] if i < len(eng_t2) else None
        b = mf_t2[i] if i < len(mf_t2) else None
        return t + ll_tags(M, y, v, method, a, b, scales)
    return tagger


def loo_subset(k):
    return lambda i: [j for j in range(k) if j != i]


def ll_tags(M, yi, vi, method, t_eng, t_mf, scales):
    """For (RE)ML fits whose tau2 differ: which program found the higher (restricted) likelihood?
    Also 'tau2_snap': the engine reports exactly 0 where metafor has 0 < tau2 <= 1e-9*median(vi)
    (the engine snaps tau2 < 1e-10*median(vi) to 0)."""
    if method not in ("REML", "ML") or not finite(t_eng) or not finite(t_mf):
        return []
    if t_eng == 0.0 and 0.0 < t_mf <= 1e-9 * scales.get("vmed", 1.0):
        return ["tau2_snap"]
    ok, _ = match(t_eng, t_mf, "var", scales)
    if ok:
        return []
    ll = M.reml_loglik if method == "REML" else M.ml_loglik
    try:
        a, b = ll(yi, vi, max(0.0, t_eng)), ll(yi, vi, max(0.0, t_mf))
    except (ArithmeticError, ValueError):
        return ["ll_error"]
    tol = 1e-9 * (1.0 + abs(a))
    if a > b + tol:
        return ["eng_ll_higher"]
    if b > a + tol:
        return ["mf_ll_higher"]
    return ["ll_equal"]


def cmp_rma(c, ds, R):
    M = engine()["M"]
    yi, vi = ds["yi"], ds["vi"]
    for m in METHODS:
        for tst in TESTS:
            key = "%s_%s" % (m, tst)
            func = "rma:" + key
            r = R.get(key)
            model = "fixed" if m == "FE" else "random"
            tm = None if m == "FE" else m
            res = c.call(func, lambda: M.meta_analysis(yi, vi, model=model, tau2_method=tm,
                                                       ci_method=CI_MAP[tst], pi_method="t_k-2"), r,
                         na_keys=("beta",))
            if res is None:
                continue
            tags = (eng_tags(res.get("tau2_info")) + mf_tags(r)
                    + ll_tags(M, yi, vi, m, res.tau2, scalar(r.get("tau2")), c.scales))
            c.cmp(func, "estimate", res.estimate, r.get("beta"), "loc", tags=tags)
            c.cmp(func, "se", res.se, r.get("se"), "loc", tags=tags)
            c.cmp(func, "stat", res.stat, r.get("zval"), "stat", tags=tags)
            c.cmp(func, "p", res.p, r.get("pval"), "p", tags=tags)
            c.cmp(func, "ci_lower", res.ci_lower, r.get("ci.lb"), "loc", tags=tags)
            c.cmp(func, "ci_upper", res.ci_upper, r.get("ci.ub"), "loc", tags=tags)
            c.cmp(func, "tau2", res.tau2, r.get("tau2"), "var", tags=tags)
            c.cmp(func, "Q", res.Q, r.get("QE"), "stat", tags=tags)
            c.cmp(func, "p_Q", res.p_Q, r.get("QEp"), "p", tags=tags)
            if m == "FE":
                c.cmp(func, "I2", res.I2, r.get("I2"), "pct", tags=tags)
                c.cmp(func, "H2", res.H2, r.get("H2"), "ratio", tags=tags)
                continue
            h = res.heterogeneity
            c.cmp(func, "I2", h.get("I2_from_tau2"), r.get("I2"), "pct", tags=tags)
            c.cmp(func, "H2", h.get("H2_from_tau2"), r.get("H2"), "ratio", tags=tags)
            c.cmp(func, "pi_lower[t_k-2]", res.pi_lower, r.get("pi.lb.riley"), "loc", tags=tags)
            c.cmp(func, "pi_upper[t_k-2]", res.pi_upper, r.get("pi.ub.riley"), "loc", tags=tags)
            pim = "z" if tst == "z" else "t_k-1"
            res2 = c.call(func + "/pi", lambda: M.meta_analysis(yi, vi, model=model, tau2_method=tm,
                                                                ci_method=CI_MAP[tst], pi_method=pim), r)
            if res2 is not None:
                c.cmp(func, "pi_lower[%s]" % pim, res2.pi_lower, r.get("pi.lb"), "loc", tags=tags)
                c.cmp(func, "pi_upper[%s]" % pim, res2.pi_upper, r.get("pi.ub"), "loc", tags=tags)


def cmp_confint(c, ds, R):
    M = engine()["M"]
    yi, vi = ds["yi"], ds["vi"]
    for m in ("REML", "DL"):
        func = "confint:" + m
        r = R.get(m)
        h = c.call(func, lambda: M.heterogeneity(yi, vi), r)
        if h is None:
            continue
        t2, i2, h2 = (as_list(rnum(r.get(x))) for x in ("tau2", "I2", "H2"))
        qp = h.get("tau2_ci_QP") or [None, None]
        c.cmp(func, "tau2.lb", qp[0], t2[1] if len(t2) > 2 else None, "var")
        c.cmp(func, "tau2.ub", qp[1], t2[2] if len(t2) > 2 else None, "var")
        ip = h.get("I2_ci_QP") or [None, None]
        hp = h.get("H2_ci_QP") or [None, None]
        c.cmp(func, "I2.lb", ip[0], i2[1] if len(i2) > 2 else None, "pct")
        c.cmp(func, "I2.ub", ip[1], i2[2] if len(i2) > 2 else None, "pct")
        c.cmp(func, "H2.lb", hp[0], h2[1] if len(h2) > 2 else None, "ratio")
        c.cmp(func, "H2.ub", hp[1], h2[2] if len(h2) > 2 else None, "ratio")


def cmp_regtest(c, ds, R):
    B = engine()["B"]
    func = "regtest:lm"
    r = R.get("lm")
    e = c.call(func, lambda: B.egger_test(ds["yi"], ds["vi"]), r, na_keys=("zval",))
    if e is None:
        return
    c.cmp(func, "t", e.t, r.get("zval"), "stat")
    c.cmp(func, "p", e.p, r.get("pval"), "p")
    c.cmp(func, "df", e.df, r.get("dfs"), "count")
    c.cmp(func, "intercept", e.intercept, r.get("b_sei"), "stat")
    c.cmp(func, "se_intercept", e.se_intercept, r.get("se_sei"), "stat")
    c.cmp(func, "ci_lower", e.ci_lower, r.get("ci.lb_sei"), "stat")
    c.cmp(func, "ci_upper", e.ci_upper, r.get("ci.ub_sei"), "stat")
    c.cmp(func, "slope", e.slope, r.get("b_int"), "loc")
    c.cmp(func, "se_slope", e.se_slope, r.get("se_int"), "loc")


def cmp_ranktest(c, ds, R):
    B = engine()["B"]
    func = "ranktest:default"
    r = R.get("default")
    e = c.call(func, lambda: B.begg_test(ds["yi"], ds["vi"]), r, na_keys=("tau",))
    if e is None:
        return
    c.cmp(func, "tau", e.kendall_tau, r.get("tau"), "ratio")
    c.cmp(func, "p", e.p, r.get("pval"), "p")


def cmp_trimfill(c, ds, R):
    M, B = engine()["M"], engine()["B"]
    yi, vi = ds["yi"], ds["vi"]
    labels = ["s%d" % i for i in range(len(yi))]
    for m in ("FE", "DL", "REML"):
        for est in ("L0", "R0"):
            func = "trimfill:%s_%s" % (m, est)
            r = R.get("%s_%s" % (m, est))
            model = "fixed" if m == "FE" else "random"
            e = c.call(func, lambda: B.trim_and_fill(yi, vi, labels, model=model,
                                                     tau2_method=None if m == "FE" else m,
                                                     estimator=est, ci_method="z", level=0.95), r,
                       na_keys=("beta",))
            if e is None:
                continue
            tags = mf_tags(r)
            if m == "REML":
                tags += eng_tags(M.estimate_tau2(yi, vi, "REML")[1])
                # the trimming iterations refit the k - k0 most extreme-side studies: flag a
                # global-search tau2 in any of those subsets (not visible in the result object)
                k0_max = max(e.k0, int(scalar(r.get("k0")) or 0) if finite(scalar(r.get("k0"))) else e.k0)
                sgn = -1.0 if e.side == "right" else 1.0
                order = sorted(range(len(yi)), key=lambda i: (sgn * yi[i], i))
                for kk in range(len(yi), max(1, len(yi) - k0_max - 1), -1):
                    sub = order[:kk]
                    t_sub, info = M.estimate_tau2([yi[i] for i in sub], [vi[i] for i in sub], "REML")
                    if info.get("global_search") and "eng_global_search" not in tags:
                        tags.append("eng_global_search")
                    if info.get("fallback") and t_sub == 0.0 and "eng_fallback_to_0" not in tags:
                        tags.append("eng_fallback_to_0")   # the D1 pattern inside a trimming step
            k0_mf = scalar(r.get("k0"))
            if len(yi) - e.k0 <= 1 or (finite(k0_mf) and len(yi) - k0_mf <= 1):
                tags.append("trim_to_1")
            ok = c.cmp(func, "k0", e.k0, r.get("k0"), "count", tags=tags)
            c.cmp(func, "side", e.side, scalar(r.get("side")), "exact", tags=tags)
            c.cmp(func, "se_k0", e.se_k0, r.get("se.k0"), "ratio", tags=tags)
            if not ok:
                continue       # the filled analysis is not comparable when k0 differs
            a = e.adjusted
            if m == "REML":
                tags = tags + eng_tags(a.get("tau2_info")) + ll_tags(
                    M, list(yi) + list(e.filled_yi), list(vi) + list(e.filled_vi), m, a.tau2,
                    scalar(r.get("tau2")), c.scales)
            c.cmp(func, "estimate", a.estimate, r.get("beta"), "loc", tags=tags)
            c.cmp(func, "se", a.se, r.get("se"), "loc", tags=tags)
            c.cmp(func, "tau2", a.tau2, r.get("tau2"), "var", tags=tags)
            c.cmp(func, "ci_lower", a.ci_lower, r.get("ci.lb"), "loc", tags=tags)
            c.cmp(func, "ci_upper", a.ci_upper, r.get("ci.ub"), "loc", tags=tags)
            c.cmp(func, "p", a.p, r.get("pval"), "p", tags=tags)
            fill = sorted(x for x in as_list(rnum(r.get("fill"))) if finite(x))
            c.cmpv(func, "filled_yi", sorted(e.filled_yi), fill, "loc", tags=tags)


L1O_CFGS = (("REML", "z"), ("DL", "knha"), ("FE", "z"), ("PM", "z"))


def cmp_leave1out(c, ds, R):
    M, S = engine()["M"], engine()["S"]
    yi, vi = ds["yi"], ds["vi"]
    labels = ["s%d" % i for i in range(len(yi))]
    for m, tst in L1O_CFGS:
        func = "leave1out:%s_%s" % (m, tst)
        r = R.get("%s_%s" % (m, tst))
        model = "fixed" if m == "FE" else "random"
        e = c.call(func, lambda: S.leave_one_out(yi, vi, labels, model, None if m == "FE" else m,
                                                 CI_MAP[tst], 0.95), r)
        if e is None:
            continue
        n_mf = len(as_list(rnum(r.get("estimate"))))
        if len(e) != n_mf:
            c.check(func, "#rows")
            c.mismatch(func, "#rows", [], None, len(e), n_mf)
            continue
        tagger = subset_tagger(M, yi, vi, m, loo_subset(len(yi)), [row["tau2"] for row in e],
                               as_list(rnum(r.get("tau2"))), c.scales)
        for ek, rk, kind in (("estimate", "estimate", "loc"), ("se", "se", "loc"), ("ci_lower", "ci.lb", "loc"),
                             ("ci_upper", "ci.ub", "loc"), ("p", "pval", "p"), ("tau2", "tau2", "var"),
                             ("Q", "Q", "stat"), ("I2", "I2", "pct")):
            if ek == "tau2" and m == "FE":
                continue       # metafor's FE leave1out() has no tau2 column (engine: 0)
            c.cmpv(func, ek, [row[ek] for row in e], r.get(rk), kind, tags=mf_tags(r), tagger=tagger)


def cmp_influence(c, ds, R):
    M, S = engine()["M"], engine()["S"]
    yi, vi = ds["yi"], ds["vi"]
    labels = ["s%d" % i for i in range(len(yi))]
    for m in ("REML", "DL", "FE"):
        func = "influence:" + m
        r = R.get(m)
        model = "fixed" if m == "FE" else "random"
        e = c.call(func, lambda: S.influence(yi, vi, labels, model, None if m == "FE" else m), r)
        if e is None:
            continue
        n_mf = len(as_list(rnum(r.get("rstudent"))))
        if len(e) != n_mf:
            c.check(func, "#rows")
            c.mismatch(func, "#rows", [], None, len(e), n_mf)
            continue
        tagger = subset_tagger(M, yi, vi, m, loo_subset(len(yi)), [row["tau2_del"] for row in e],
                               as_list(rnum(r.get("tau2.del"))), c.scales)
        full_tags = []
        if m in ("REML", "ML"):
            t_full, info = M.estimate_tau2(yi, vi, m)
            full_tags = eng_tags(info) + ll_tags(M, yi, vi, m, t_full, scalar(r.get("tau2")), c.scales)
        for ek, rk, kind in (("rstudent", "rstudent", "infl"), ("dffits", "dffits", "infl"),
                             ("cook_d", "cook.d", "infl"), ("cov_ratio", "cov.r", "infl"),
                             ("tau2_del", "tau2.del", "var"), ("Q_del", "QE.del", "stat"),
                             ("hat", "hat", "ratio"), ("weight_pct", "weight", "pct"),
                             ("dfbetas", "dfbs", "infl"), ("influential", "inf", "exact")):
            c.cmpv(func, ek, [row[ek] for row in e], r.get(rk), kind, tags=full_tags + mf_tags(r), tagger=tagger)


def cmp_mods(c, ds, R):
    MO, M = engine()["MO"], engine()["M"]
    yi, vi = ds["yi"], ds["vi"]
    x = [[1.0] + [float(v) for v in row] for row in ds["mods"]]
    names = ["intrcpt"] + ["x%d" % j for j in range(len(ds["mods"][0]))]
    extra = []
    if "collinear" in ds.get("mod_kinds", []):
        extra.append("collinear")
    if len(yi) - len(names) <= 1:
        extra.append("df_res<=1")
    for m in METHODS:
        for tst in ("z", "knha"):
            func = "mods:%s_%s" % (m, tst)
            r = R.get("%s_%s" % (m, tst))
            e = c.call(func, lambda: MO.meta_regression(yi, vi, x, names, m, tst), r, na_keys=("b",))
            if e is None:
                continue
            tags = extra + eng_tags(e.get("tau2_info")) + mf_tags(r)
            t_mf = scalar(r.get("tau2"))
            if m in ("REML", "ML") and finite(t_mf) and not match(e.tau2, t_mf, "var", c.scales)[0]:
                try:
                    la = MO.mr_loglik(x, yi, vi, e.tau2, m == "REML")
                    lb = MO.mr_loglik(x, yi, vi, max(0.0, t_mf), m == "REML")
                    tol = 1e-9 * (1.0 + abs(la))
                    tags.append("eng_ll_higher" if la > lb + tol else ("mf_ll_higher" if lb > la + tol else "ll_equal"))
                except Exception:
                    tags.append("ll_error")
            cf = e.coefficients
            cache = {}

            def ex_tag(field, idx=None, e=e, r=r, m=m, cache=cache):
                """Lazily adjudicate closed-form quantities with exact arithmetic."""
                try:
                    if field in ("QE", "QE_p"):
                        if "QE" not in cache:
                            cache["QE"] = exact.verdict(e.QE, scalar(r.get("QE")), exact.qe(x, yi, vi))
                        return ["exact=" + cache["QE"]]
                    if field == "tau2" and m == "DL":
                        if "tau2" not in cache:
                            cache["tau2"] = exact.verdict(e.tau2, scalar(r.get("tau2")), exact.tau2_dl(x, yi, vi))
                        return ["exact=" + cache["tau2"]]
                    if field == "I2_res":
                        if m == "FE":
                            v = exact.verdict(e.I2_res, scalar(r.get("I2")), exact.i2_fe(x, yi, vi))
                        else:
                            # each side against the exact I2 at its own tau2 (s2 = (k-p)/tr(P) is the
                            # ill-conditioned part)
                            xe = exact.i2_res(x, yi, vi, e.tau2)
                            xm = exact.i2_res(x, yi, vi, max(0.0, scalar(r.get("tau2"))))
                            ee = abs(e.I2_res - xe)
                            em = abs(scalar(r.get("I2")) - xm)
                            oke, okm = ee <= 1e-6 * abs(xe), em <= 1e-6 * abs(xm)
                            v = ("both" if oke and okm else "engine" if oke else "metafor" if okm else
                                 ("neither:engine" if ee < em else "neither:metafor"))
                        return ["exact=" + v]
                    if m == "DL" and field in ("b", "se", "stat", "p", "ci_lower", "ci_upper", "QM", "QM_p", "R2"):
                        return ex_tag("tau2")
                    if m == "FE" and field in ("b", "se", "stat", "p", "ci_lower", "ci_upper", "QM", "QM_p"):
                        if "b" not in cache:
                            eb = exact.coefficients(x, yi, vi, 0.0)
                            vs = [exact.verdict(q["estimate"], bm, xb) for q, bm, xb in
                                  zip(cf, as_list(rnum(r.get("b"))), eb)]
                            cache["b"] = ("metafor" if "metafor" in vs else ("engine" if "engine" in vs else
                                          ("both" if all(v == "both" for v in vs) else "neither")))
                        return ["exact=" + cache["b"]]
                except Exception:
                    return ["exact=error"]
                return []
            r2_tags = []
            if m not in ("FE",):
                try:
                    if m in ("REML", "ML"):
                        t0, info0 = M.estimate_tau2(yi, vi, m)
                        r2_tags += eng_tags(info0) + ll_tags(M, yi, vi, m, t0, scalar(r.get("tau2_0")), c.scales)
                    else:
                        t0 = M.estimate_tau2(yi, vi, m)[0] if m != "DL" else M.tau2_dl(yi, vi)
                    if t0 <= 1e-8 * c.scales["vmed"]:
                        r2_tags.append("tau2_RE~0")
                except Exception:
                    pass
            if finite(e.QE) and e.QE < 1e-10:
                tags.append("perfect_fit")      # residuals are 0 up to rounding (y = X b exactly)
            for fld, key, rk, kind in (("b", "estimate", "b", "loc"), ("se", "se", "se", "loc"),
                                       ("stat", "stat", "zval", "stat"), ("p", "p", "pval", "p"),
                                       ("ci_lower", "ci_lower", "ci.lb", "loc"), ("ci_upper", "ci_upper", "ci.ub", "loc")):
                c.cmpv(func, fld, [q[key] for q in cf], r.get(rk), kind, tags=tags,
                       tagger=lambda i, fld=fld: ex_tag(fld, i))
            for fld, val, rk, kind in (("QM", e.QM, "QM", "stat"), ("QM_p", e.QM_p, "QMp", "p"),
                                       ("QE", e.QE, "QE", "stat"), ("QE_p", e.QE_p, "QEp", "p"),
                                       ("tau2", e.tau2, "tau2", "var"), ("I2_res", e.I2_res, "I2", "pct")):
                c.cmp(func, fld, val, r.get(rk), kind, tags=tags, tagger=lambda i, fld=fld: ex_tag(fld, i))
            c.cmp(func, "R2", e.R2, r.get("R2"), "pct", tags=tags + r2_tags, tagger=lambda i: ex_tag("R2", i))


def cmp_subgroup(c, ds, R):
    MO, M = engine()["MO"], engine()["M"]
    yi, vi, groups = ds["yi"], ds["vi"], ds["groups"]
    labels = ["s%d" % i for i in range(len(yi))]
    small = ["single_study_group"] if min(collections.Counter(groups).values()) == 1 else []
    for m in ("REML", "DL"):
        for kind in ("sep", "common"):
            func = "subgroup:%s_%s" % (m, kind)
            r = R.get("%s_%s" % (m, kind))
            e = c.call(func, lambda: MO.subgroup_analysis(yi, vi, groups, labels, "random", m, "z", 0.95,
                                                          common_tau2=(kind == "common")), r)
            if e is None:
                continue
            tags = small + mf_tags(r)
            order = []
            for gg in groups:
                if gg not in order:
                    order.append(gg)
            xg = [[1.0] + [1.0 if gg == lev else 0.0 for lev in order[1:]] for gg in groups]
            if kind == "common" and m == "REML" and e.common_tau2 is not None:
                tags = tags + eng_tags(e.get("common_tau2_info"))
                t_mf = scalar(r.get("tau2"))
                if finite(t_mf) and not match(e.common_tau2, t_mf, "var", c.scales)[0]:
                    la = MO.mr_loglik(xg, yi, vi, e.common_tau2, True)
                    lb = MO.mr_loglik(xg, yi, vi, max(0.0, t_mf), True)
                    tol = 1e-9 * (1.0 + abs(la))
                    tags.append("eng_ll_higher" if la > lb + tol else ("mf_ll_higher" if lb > la + tol else "ll_equal"))
            if kind == "common" and m == "DL" and e.common_tau2 is not None:
                t_mf = scalar(r.get("tau2"))
                if finite(t_mf) and not match(e.common_tau2, t_mf, "var", c.scales)[0]:
                    order = []
                    for gg in groups:
                        if gg not in order:
                            order.append(gg)
                    xg = [[1.0] + [1.0 if gg == lev else 0.0 for lev in order[1:]] for gg in groups]
                    try:
                        tags = tags + ["exact=" + exact.verdict(e.common_tau2, t_mf, exact.tau2_dl(xg, yi, vi))]
                    except Exception:
                        tags = tags + ["exact=error"]
            levs = [str(x) for x in as_list(r.get("levels"))]
            by = {g.group: g for g in e.groups}
            est = as_list(rnum(r.get("estimate")))
            se = as_list(rnum(r.get("se")))
            if kind == "sep" and m == "REML":      # per-group REML fits: flags + likelihood check
                t2m = as_list(rnum(r.get("tau2")))
                gtags = []
                for j, lev in enumerate(levs):
                    g = by.get(lev)
                    if g is None or g.k < 2 or j >= len(t2m):
                        continue
                    idx = [i for i, gg in enumerate(groups) if str(gg) == lev]
                    yy, vv = [yi[i] for i in idx], [vi[i] for i in idx]
                    gtags += eng_tags(g.get("tau2_info")) + ll_tags(M, yy, vv, "REML", g.tau2, t2m[j], c.scales)
                tags = tags + sorted(set(gtags))
            for j, lev in enumerate(levs):
                g = by.get(lev)
                c.cmp(func, "estimate", g.estimate if g else None, est[j] if j < len(est) else None, "loc", j, tags)
                c.cmp(func, "se", g.se_wald if g else None, se[j] if j < len(se) else None, "loc", j, tags)
                if kind == "sep":
                    t2 = as_list(rnum(r.get("tau2")))
                    c.cmp(func, "tau2", g.tau2 if g else None, t2[j] if j < len(t2) else None, "var", j, tags)
            if kind == "common":
                c.cmp(func, "tau2_common", e.common_tau2, r.get("tau2"), "var", tags=tags)
            c.cmp(func, "Q_between", e.Q_between, r.get("QM"), "stat", tags=tags)
            c.cmp(func, "p_between", e.p_between, r.get("QMp"), "p", tags=tags)


def cmp_cumul(c, ds, R):
    S, M = engine()["S"], engine()["M"]
    yi, vi = ds["yi"], ds["vi"]
    labels = ["s%d" % i for i in range(len(yi))]
    for m, tst in (("REML", "z"), ("DL", "knha")):
        func = "cumul:%s_%s" % (m, tst)
        r = R.get("%s_%s" % (m, tst))
        e = c.call(func, lambda: S.cumulative(yi, vi, labels, ds["year"], "random", m, CI_MAP[tst], 0.95), r)
        if e is None:
            continue
        tags = mf_tags(r)
        order = S.cumulative_order(ds["year"])
        tagger = subset_tagger(M, yi, vi, m, lambda i: order[:i + 1], [row["tau2"] for row in e],
                               as_list(rnum(r.get("tau2"))), c.scales)
        for ek, rk, kind in (("estimate", "estimate", "loc"), ("ci_lower", "ci.lb", "loc"),
                             ("ci_upper", "ci.ub", "loc"), ("tau2", "tau2", "var"), ("I2", "I2", "pct")):
            c.cmpv(func, ek, [row[ek] for row in e], r.get(rk), kind, tags=tags, tagger=tagger)


def cmp_uni(acc, ds, R, only):
    sv = median(ds["vi"])
    c = Ctx(acc, ds, uni_tags(ds), {"vmed": sv, "sqrt_vmed": math.sqrt(sv)})
    for grp, fn, need in (("rma", cmp_rma, None), ("confint", cmp_confint, None), ("regtest", cmp_regtest, None),
                          ("ranktest", cmp_ranktest, None), ("trimfill", cmp_trimfill, None),
                          ("leave1out", cmp_leave1out, None), ("influence", cmp_influence, None),
                          ("mods", cmp_mods, "mods"), ("subgroup", cmp_subgroup, "groups"),
                          ("cumul", cmp_cumul, "year")):
        if only and grp not in only:
            continue
        if need and not ds.get(need):
            continue
        sub = R.get(grp)
        if sub is None:
            sub = {}
        if isinstance(sub, dict) and "error" in sub:
            c.check(grp, "!group")
            c.mismatch(grp, "!metafor_error", ["msg=" + short_msg(str(sub["error"]))], None, "", sub["error"])
            continue
        fn(c, ds, sub)


# --- effect sizes
def cmp_escalc_one(c, func, rows, r, measure, kw, row_tagger):
    E = engine()["E"]
    es = c.call(func, lambda: E.compute(rows, measure, **kw), r)
    if es is None:
        return None
    my = as_list(rnum(r.get("yi")))
    mv = as_list(rnum(r.get("vi")))
    if len(my) != len(rows) or len(mv) != len(rows):
        c.check(func, "#rows")
        c.mismatch(func, "#rows", [], None, len(rows), len(my))
        return es
    pos = {ri: j for j, ri in enumerate(es.row_index)}
    reasons = dict(es.excluded)
    for i, row in enumerate(rows):
        tags = row_tagger(row)
        mf_ok = finite(my[i]) and finite(mv[i]) and mv[i] > 0
        j = pos.get(i)
        c.check(func, "row")
        if j is None:
            if mf_ok:
                c.mismatch(func, "row", tags + ["engine_excluded"], i,
                           "excluded: " + str(reasons.get(row.get("study"), ""))[:80], [my[i], mv[i]])
            continue
        if not mf_ok:
            c.mismatch(func, "row", tags + ["metafor_NA"], i, [es.yi[j], es.vi[j]], [my[i], mv[i]])
            continue
        c.cmp(func, "yi", es.yi[j], my[i], "es", i, tags)
        c.cmp(func, "vi", es.vi[j], mv[i], "es", i, tags)
    return es


def cmp_rma_es(c, func, es, r, method):
    M = engine()["M"]
    if es is None or len(es.yi) < 1:
        return
    res = c.call(func, lambda: M.meta_analysis(es.yi, es.vi, model="random", tau2_method=method, ci_method="z"), r,
                 na_keys=("beta",))
    if res is None:
        return
    sv = median(es.vi)
    sc = {"vmed": sv, "sqrt_vmed": math.sqrt(sv)}
    tags = (eng_tags(res.get("tau2_info")) + mf_tags(r)
            + ll_tags(M, list(es.yi), list(es.vi), method, res.tau2, scalar(r.get("tau2")), sc))
    c.cmp(func, "k", res.k, r.get("k"), "count", tags=tags)
    c.cmp(func, "estimate", res.estimate, r.get("beta"), "loc", tags=tags, scales=sc)
    c.cmp(func, "se", res.se, r.get("se"), "loc", tags=tags, scales=sc)
    c.cmp(func, "tau2", res.tau2, r.get("tau2"), "var", tags=tags, scales=sc)


BIN_ESCALC = (("OR_d00", "OR", {}), ("RR_d00", "RR", {}), ("RD_def", "RD", {}),
              ("OR_all", "OR", {"cc_to": "all", "drop00": False}), ("RR_all", "RR", {"cc_to": "all", "drop00": False}),
              ("RD_all", "RD", {"cc_to": "all", "drop00": False}),
              ("OR_none", "OR", {"cc_to": "none", "drop00": False}),
              ("RR_none", "RR", {"cc_to": "none", "drop00": False}),
              ("RD_none", "RD", {"cc_to": "none", "drop00": False}))
CONT_ESCALC = (("MD_LS", "MD", {"md_vtype": "unequal"}), ("MD_HO", "MD", {"md_vtype": "pooled"}),
               ("SMD_LS", "SMD", {"smd_vtype": "LS"}), ("SMD_LS2", "SMD", {"smd_vtype": "LS2"}),
               ("SMD_UB", "SMD", {"smd_vtype": "UB"}),
               ("SMD1_LS", "SMD_GLASS", {"glass_vtype": "LS"}), ("SMD1_LS2", "SMD_GLASS", {"glass_vtype": "LS2"}),
               ("SMD1_UB", "SMD_GLASS", {"glass_vtype": "UB"}), ("SMD1H", "SMD_GLASS", {"glass_vtype": "SMD1H"}),
               ("COHEN_LS", "COHEN_D", {"smd_vtype": "LS"}), ("COHEN_LS2", "COHEN_D", {"smd_vtype": "LS2"}),
               ("ROM", "ROM", {}))
PAIRED_ESCALC = (("MC", "MC", {}), ("SMCC_LS", "SMCC", {"smd_vtype": "LS"}), ("SMCC_LS2", "SMCC", {"smd_vtype": "LS2"}))
PROP_ESCALC = tuple((m + suf, m, kw) for m in ("PR", "PLN", "PLO", "PAS", "PFT")
                    for suf, kw in (("", {}), ("_all", {"cc_to": "all"})))
COR_ESCALC = (("COR", "COR", {}), ("ZCOR", "ZCOR", {}))
RMA_ES = {"bin": (("OR_d00_REML", "OR_d00", "REML"), ("RD_all_DL", "RD_all", "DL")),
          "cont": (("SMD_LS_REML", "SMD_LS", "REML"),),
          "prop": (("PLO_REML", "PLO", "REML"),),
          "cor": (("ZCOR_REML", "ZCOR", "REML"),),
          "paired": ()}


def cmp_es_dataset(acc, ds, R, only):
    typ = ds["type"]
    rows = ds["rows"]
    spec, tagger = {"bin": (BIN_ESCALC, bin_row_tags), "cont": (CONT_ESCALC, cont_row_tags),
                    "paired": (PAIRED_ESCALC, paired_row_tags), "prop": (PROP_ESCALC, prop_row_tags),
                    "cor": (COR_ESCALC, cor_row_tags)}[typ]
    c = Ctx(acc, ds, bin_ds_tags(rows) if typ == "bin" else ())
    computed = {}
    if not only or "escalc" in only:
        E_R = R.get("escalc") or {}
        for key, measure, kw in spec:
            computed[key] = cmp_escalc_one(c, "escalc:" + key, rows, E_R.get(key), measure, kw, tagger)
        if not only or "rma_es" in only:
            RR = R.get("rma_es") or {}
            for key, src, method in RMA_ES[typ]:
                cmp_rma_es(c, "rma_es:" + key, computed.get(src), RR.get(key), method)
    if typ != "bin":
        return
    M, B = engine()["M"], engine()["B"]
    e1 = [x["e1"] for x in rows]
    n1 = [x["n1"] for x in rows]
    e2 = [x["e2"] for x in rows]
    n2 = [x["n2"] for x in rows]
    if not only or "mh" in only:
        for m in ("OR", "RR", "RD"):
            func = "mh:" + m
            r = (R.get("mh") or {}).get(m)
            e = c.call(func, lambda: M.mantel_haenszel(e1, n1, e2, n2, measure=m), r, na_keys=("beta",))
            if e is None:
                continue
            for ek, rk, kind in (("estimate", "beta", "loc"), ("se", "se", "loc"), ("stat", "zval", "stat"),
                                 ("p", "pval", "p"), ("ci_lower", "ci.lb", "loc"), ("ci_upper", "ci.ub", "loc"),
                                 ("Q", "QE", "stat"), ("p_Q", "QEp", "p"), ("I2", "I2", "pct"), ("H2", "H2", "ratio")):
                c.cmp(func, ek, getattr(e, ek), r.get(rk), kind)
    if not only or "peto" in only:
        func = "peto:OR"
        r = (R.get("peto") or {}).get("OR")
        e = c.call(func, lambda: M.peto(e1, n1, e2, n2), r, na_keys=("beta",))
        if e is not None:
            for ek, rk, kind in (("estimate", "beta", "loc"), ("se", "se", "loc"), ("stat", "zval", "stat"),
                                 ("p", "pval", "p"), ("ci_lower", "ci.lb", "loc"), ("ci_upper", "ci.ub", "loc"),
                                 ("Q", "QE", "stat"), ("p_Q", "QEp", "p"), ("I2", "I2", "pct"), ("H2", "H2", "ratio"),
                                 ("k_estimable", "k.yi", "count")):
                c.cmp(func, ek, getattr(e, ek), r.get(rk), kind)
    if not only or "harbord" in only:
        func = "harbord:lm"
        r = (R.get("harbord") or {}).get("lm")
        e = c.call(func, lambda: B.harbord_test(e1, n1, e2, n2), r, na_keys=("intercept",))
        if e is not None:
            for ek, rk, kind in (("k", "k", "count"), ("intercept", "intercept", "stat"),
                                 ("se_intercept", "se", "stat"), ("t", "t", "stat"), ("p", "p", "p"),
                                 ("slope", "slope", "stat")):
                c.cmp(func, ek, getattr(e, ek), r.get(rk), kind)
    if not only or "peters" in only:
        func = "peters:lm"
        r = (R.get("peters") or {}).get("lm")
        e = c.call(func, lambda: B.peters_test(e1, n1, e2, n2), r, na_keys=("slope",))
        if e is not None:
            for ek, rk, kind in (("k", "k", "count"), ("slope", "slope", "stat"), ("se_slope", "se", "stat"),
                                 ("t", "t", "stat"), ("p", "p", "p")):
                c.cmp(func, ek, getattr(e, ek), r.get(rk), kind)


def compare_dataset(acc, ds, R, only):
    acc.datasets += 1
    if R is None or (isinstance(R, dict) and "error" in R):
        c = Ctx(acc, ds)
        c.check("dataset", "!R")
        c.mismatch("dataset", "!metafor_error", [], None, "", (R or {}).get("error", "missing") if isinstance(R, dict) else "missing")
        return
    if isinstance(R, list):        # an empty R list() is serialized as []
        R = {}
    if ds["type"] == "uni":
        cmp_uni(acc, ds, R, only)
    else:
        cmp_es_dataset(acc, ds, R, only)


# --------------------------------------------------------------------------- workers
def r_groups(only):
    """--only groups -> run_metafor.R groups (rma_es needs escalc)."""
    if not only:
        return ""
    g = set(only)
    if "rma_es" in g:
        g.add("escalc")
    return ",".join(sorted(g))


def process_chunk(job):
    """Worker entry point (top-level -> spawn-safe). Returns a picklable dict."""
    t0 = time.time()
    if job.get("datasets") is not None:
        dsl = job["datasets"]
    else:
        dsl = [gen.generate(job["seed"], i, job.get("types")) for i in job["indices"]]
    tag = "chunk%05d" % job["chunk"]
    inp = os.path.join(job["workdir"], tag + "_in.json")
    out = os.path.join(job["workdir"], tag + "_out.json")
    with open(inp, "w") as fh:
        json.dump(dsl, fh)
    cmd = [job["rscript"], R_SCRIPT, inp, out, r_groups(job["only"])]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=job.get("timeout"))
        rc, err = proc.returncode, proc.stderr
    except subprocess.TimeoutExpired:
        rc, err = -1, "timeout after %ss" % job.get("timeout")
    t_r = time.time() - t0
    if rc != 0 or not os.path.exists(out):
        return {"chunk": job["chunk"], "fatal": "Rscript failed (rc=%s): %s" % (rc, err[-2000:]),
                "ids": [d["id"] for d in dsl]}
    with open(out) as fh:
        R = json.load(fh)
    if not job.get("keep"):
        for p in (inp, out):
            try:
                os.remove(p)
            except OSError:
                pass
    t1 = time.time()
    acc = Acc()
    acc.keep_records = bool(job.get("records"))
    try:
        engine()
    except Exception:
        return {"chunk": job["chunk"], "fatal": "engine import failed:\n" + traceback.format_exc()[-3000:],
                "ids": [d["id"] for d in dsl]}
    only = set(job["only"] or ())
    for ds in dsl:
        try:
            compare_dataset(acc, ds, R.get(ds["id"]), only)
        except Exception:
            c = Ctx(acc, ds)
            c.check("harness", "!crash")
            c.mismatch("harness", "!crash", [], None, traceback.format_exc()[-1500:], "")
    res = acc.to_dict()
    res.update(chunk=job["chunk"], t_r=t_r, t_engine=time.time() - t1, n=len(dsl))
    return res


def preflight(rscript):
    if not rscript:
        return None, "Rscript not found on PATH"
    code = ("ok <- suppressWarnings(suppressPackageStartupMessages(c(requireNamespace('metafor', quietly=TRUE), "
            "requireNamespace('jsonlite', quietly=TRUE)))); "
            "if (!all(ok)) { cat('MISSING', c('metafor','jsonlite')[!ok]); quit(status=3) }; "
            "cat(R.version$major, R.version$minor, as.character(packageVersion('metafor')), "
            "as.character(packageVersion('jsonlite')))")
    try:
        p = subprocess.run([rscript, "-e", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as ex:
        return None, "Rscript could not be started: %s" % ex
    if p.returncode != 0:
        return None, "R packages missing (%s)" % (p.stdout.strip() or p.stderr.strip()[-300:])
    parts = p.stdout.split()
    return {"R": "%s.%s" % (parts[0], parts[1]), "metafor": parts[2], "jsonlite": parts[3]}, None


# --------------------------------------------------------------------------- merge & report
def merge(results):
    checks = collections.Counter()
    classes = {}
    records, fatal = [], []
    stats = {"n": 0, "t_r": 0.0, "t_engine": 0.0}
    for res in results:
        if res.get("fatal"):
            fatal.append({"chunk": res["chunk"], "error": res["fatal"], "ids": res["ids"][:5]})
            continue
        stats["n"] += res["n"]
        stats["t_r"] += res["t_r"]
        stats["t_engine"] += res["t_engine"]
        for f, fl, n in res["checks"]:
            checks[(f, fl)] += n
        for f, fl, cond, d in res["mism"]:
            slot = classes.setdefault((f, fl, cond), {})
            for did, (n, ex) in d.items():
                slot[did] = [n, ex]
        records.extend(res.get("records") or [])
    return checks, classes, records, fatal, stats


def build_summary(checks, classes, fatal, stats, meta):
    rows = []
    for (f, fl, cond), d in classes.items():
        tags = [] if cond == "general" else cond.split(",")
        any_rec = next(iter(d.values()))[1]
        rec = {"func": f, "field": fl, "tags": tags, "idx": any_rec.get("idx"),
               "engine": any_rec.get("engine"), "metafor": any_rec.get("metafor")}
        known = classify_known(rec)
        defect = None if known else classify_defect(rec)
        ids = sorted(d)
        rows.append({
            "func": f, "field": fl, "cond": cond, "status": "KNOWN" if known else "FAIL", "known_id": known,
            "known_kind": known_kind(known) if known else None, "defect": defect,
            "n_mismatch": sum(v[0] for v in d.values()), "n_datasets": len(d),
            "n_checks": checks.get((f, fl), 0),
            "examples": [dict(id=i, **d[i][1]) for i in ids[:3]],
            "all_ids": ids[:200],
        })
    rows.sort(key=lambda r: (r["status"] != "FAIL", r["func"], r["field"], -r["n_datasets"]))
    fails = [r for r in rows if r["status"] == "FAIL"]
    by_func = collections.Counter()
    for (f, fl), n in checks.items():
        by_func[f.split(":")[0]] += n
    return {
        "meta": meta, "stats": stats,
        "totals": {"checks": sum(checks.values()), "classes": len(rows), "fail_classes": len(fails),
                   "known_classes": len(rows) - len(fails),
                   "fail_mismatches": sum(r["n_mismatch"] for r in fails),
                   "known_mismatches": sum(r["n_mismatch"] for r in rows if r["status"] == "KNOWN"),
                   "fatal_chunks": len(fatal),
                   "known_by_kind": dict(collections.Counter(r["known_kind"] for r in rows if r["status"] == "KNOWN")),
                   "fail_by_defect": dict(collections.Counter(r["defect"] or "untriaged" for r in fails)),
                   "fail_datasets_by_defect": {k: len(set(i for r in fails if (r["defect"] or "untriaged") == k
                                                          for i in r["all_ids"]))
                                               for k in set(r["defect"] or "untriaged" for r in fails)}},
        "checks_by_group": dict(sorted(by_func.items())),
        "classes": rows, "fatal": fatal,
        "tolerances": {k: {kk: vv for kk, vv in v.items()} for k, v in TOLERANCES.items()},
        "known_differences": [{k: v for k, v in kd.items() if k != "cond"} for kd in KNOWN_DIFFERENCES],
        "suspected_defects": [{k: v for k, v in kd.items() if k != "cond"} for kd in SUSPECTED_DEFECTS],
        "fixed_defects": FIXED_DEFECTS,
    }


def _fmt(v):
    if isinstance(v, float):
        return "%.10g" % v
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v[:4]) + (", …" if len(v) > 4 else "") + "]"
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "…"


def write_markdown(summary, path):
    m, t = summary["meta"], summary["totals"]
    L = ["# Engine vs metafor differential fuzz", "",
         "- command: `%s`" % m["command"],
         "- seed %s, n = %s datasets, jobs = %s, groups = %s" % (m["seed"], m["n"], m["jobs"], m["only"] or "all"),
         "- R %s, metafor %s, jsonlite %s; Python %s" % (m["versions"].get("R"), m["versions"].get("metafor"),
                                                        m["versions"].get("jsonlite"), m["python"]),
         "- wall time %.1f s (R CPU %.1f s, engine+compare CPU %.1f s)" % (m["wall"], summary["stats"]["t_r"],
                                                                         summary["stats"]["t_engine"]),
         "- checks: %d; mismatch classes: %d unexplained (FAIL, %d mismatches), %d known (%d mismatches); fatal chunks: %d"
         % (t["checks"], t["fail_classes"], t["fail_mismatches"], t["known_classes"], t["known_mismatches"],
            t["fatal_chunks"]), ""]
    L += ["## Unexplained classes by suspected defect", "", "| suspected defect | classes | datasets | description |",
          "|---|---:|---:|---|"]
    desc = {k["id"]: k["reason"] for k in SUSPECTED_DEFECTS}
    for k, n in sorted(t["fail_by_defect"].items()):
        L.append("| %s | %d | %d | %s |" % (k, n, t["fail_datasets_by_defect"].get(k, 0),
                                           desc.get(k, "not yet triaged")))
    L += ["", "## Checks per group", "", "| group | checks |", "|---|---:|"]
    L += ["| %s | %d |" % (g, n) for g, n in summary["checks_by_group"].items()]
    for status, title in (("FAIL", "Unexplained mismatch classes"), ("KNOWN", "Known / documented differences")):
        rows = [r for r in summary["classes"] if r["status"] == status]
        L += ["", "## %s (%d)" % (title, len(rows)), ""]
        if not rows:
            L.append("none")
            continue
        L += ["| function | field | condition | mismatches | datasets | checks | %s | example (id[idx]: engine vs metafor) |"
              % ("known id (kind)" if status == "KNOWN" else "status"), "|---|---|---|---:|---:|---:|---|---|"]
        for r in rows:
            ex = r["examples"][0]
            L.append("| %s | %s | %s | %d | %d | %d | %s | %s[%s]: %s vs %s |" % (
                r["func"], r["field"], r["cond"].replace("|", "/"), r["n_mismatch"], r["n_datasets"],
                r["n_checks"], ("%s (%s)" % (r["known_id"], r["known_kind"])) if r["known_id"]
                else "FAIL " + (r["defect"] or "untriaged"),
                ex["id"], ex.get("idx"), _fmt(ex.get("engine")),
                _fmt(ex.get("metafor"))))
    if summary["fatal"]:
        L += ["", "## Fatal chunk errors", ""]
        for f in summary["fatal"]:
            L.append("- chunk %s (%s …): `%s`" % (f["chunk"], ", ".join(f["ids"]), f["error"][-300:].replace("\n", " ")))
    if summary.get("minimized"):
        L += ["", "## Minimized datasets", ""]
        for mz in summary["minimized"]:
            L += ["### %s / %s (%s)" % (mz["func"], mz["field"], mz["cond"]), "",
                  "k: %s -> %s; engine %s vs metafor %s" % (mz["k_from"], mz["k_to"], _fmt(mz.get("engine")),
                                                            _fmt(mz.get("metafor"))), "",
                  "```json", json.dumps(mz["dataset"]), "```", ""]
    L += ["", "## Tolerances", "", "|engine - metafor| <= rtol * max(|engine|, |metafor|) + atol * scale", "",
          "| kind | rtol | atol | atol scale | +-Inf above | reason |", "|---|---:|---:|---|---:|---|"]
    for k, v in TOLERANCES.items():
        L.append("| %s | %g | %g | %s | %s | %s |" % (k, v["rtol"], v["atol"], v["scale"] or "1",
                                                     "%g" % v["inf_above"] if v.get("inf_above") else "-", v["reason"]))
    L += ["", "## Known differences (excluded from failure counts)", "",
          "kind: convention = deliberate, documented engine convention; oracle = metafor is the "
          "inaccurate/failing side; noise = both results are rounding-noise driven.", "",
          "| id | kind | function | field | reason | source |", "|---|---|---|---|---|---|"]
    for k in KNOWN_DIFFERENCES:
        L.append("| %s | %s | `%s` | `%s` | %s | %s |" % (k["id"], k.get("kind", "convention"), k["func"],
                                                       k["field"], k["reason"], k["source"]))
    L += ["", "## Suspected engine defects (still counted as FAIL)", "", "| id | function | field | description | source |",
          "|---|---|---|---|---|"]
    for k in SUSPECTED_DEFECTS:
        L.append("| %s | `%s` | `%s` | %s | %s |" % (k["id"], k["func"], k["field"], k["reason"], k["source"]))
    L += ["", "## Fixed engine defects (not used for classification)", "", "| id | defect | fix | regression test |",
          "|---|---|---|---|"]
    for k in FIXED_DEFECTS:
        L.append("| %s | %s | %s | %s |" % (k["id"], k["reason"], k["fix"], k["test"]))
    L += ["", "Replay a dataset: `python3 tests/fuzz/run_fuzz.py --ids <id>[,<id>...] --only <group>`", ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


# --------------------------------------------------------------------------- minimizer
def n_studies(ds):
    return len(ds["yi"]) if ds["type"] == "uni" else len(ds["rows"])


def drop_study(ds, i, new_id):
    d = json.loads(json.dumps(ds))
    if d["type"] == "uni":
        for key in ("yi", "vi", "mods", "groups", "year"):
            if d.get(key) is not None:
                del d[key][i]
    else:
        del d["rows"][i]
    d["id"] = new_id
    return d


def class_hit(res, func, field, did):
    for f, fl, cond, d in res.get("mism", []):
        if f == func and fl == field and did in d:
            return d[did][1]
    return None


def minimize(ds, func, field, only, rscript, workdir, min_k=2, max_rounds=80):
    cur, hit = ds, None
    group = func.split(":")[0]
    groups = [group] if group in GROUPS else list(only or [])
    for rnd in range(max_rounds):
        k = n_studies(cur)
        if k <= min_k:
            break
        cands = [drop_study(cur, i, "%s~%d.%d" % (ds["id"], rnd, i)) for i in range(k)]
        res = process_chunk({"chunk": 90000 + rnd, "datasets": cands, "only": groups, "workdir": workdir,
                             "rscript": rscript, "timeout": 1800})
        if res.get("fatal"):
            break
        nxt = None
        for cd in cands:
            h = class_hit(res, func, field, cd["id"])
            if h is not None:
                nxt, hit = cd, h
                break
        if nxt is None:
            break
        cur = nxt
    return cur, hit


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="Differential fuzz testing: engine vs R/metafor.")
    ap.add_argument("--n", type=int, default=200, help="number of generated datasets (default 200)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--out", default=None, help="output directory (default: new temp dir outside the repo)")
    ap.add_argument("--only", default="", help="comma-separated groups: " + ",".join(GROUPS))
    ap.add_argument("--types", default="", help="restrict generated dataset types: " + ",".join(gen.GENERATORS))
    ap.add_argument("--chunk", type=int, default=0, help="datasets per worker task (default: auto)")
    ap.add_argument("--ids", default="", help="re-run only these dataset ids (<seed>-<index>[,...] or @file); "
                                              "verbose when <= 20")
    ap.add_argument("--datasets", default="", help="run the datasets in this JSON file instead of generating")
    ap.add_argument("--minimize", type=int, default=0, help="minimize one example of the first N FAIL classes")
    ap.add_argument("--verbose", action="store_true", help="print every mismatch record")
    ap.add_argument("--keep", action="store_true", help="keep the per-chunk R input/output JSON in --out/work")
    ap.add_argument("--timeout", type=int, default=3600, help="per-chunk R timeout in seconds")
    a = ap.parse_args(argv)

    only = [g for g in a.only.split(",") if g]
    bad = [g for g in only if g not in GROUPS]
    if bad:
        ap.error("unknown group(s) %s; choose from %s" % (",".join(bad), ",".join(GROUPS)))
    types = [t for t in a.types.split(",") if t] or None
    rscript = shutil.which("Rscript")
    versions, why = preflight(rscript)
    if versions is None:
        print("SKIP: differential fuzz needs R with metafor and jsonlite — %s." % why)
        return 0
    out = a.out or tempfile.mkdtemp(prefix="metafor_fuzz_")
    os.makedirs(out, exist_ok=True)
    workdir = os.path.join(out, "work")
    os.makedirs(workdir, exist_ok=True)
    t_start = time.time()

    # ---- jobs
    jobs = []
    verbose = a.verbose
    if a.datasets:
        with open(a.datasets) as fh:
            dsl = json.load(fh)
        if isinstance(dsl, dict):
            dsl = [dsl]
        for i, d in enumerate(dsl):
            d.setdefault("id", "file-%05d" % i)
        n_total = len(dsl)
        size = a.chunk or max(1, min(100, -(-n_total // max(1, a.jobs))))
        for ci in range(0, n_total, size):
            jobs.append({"datasets": dsl[ci:ci + size]})
        verbose = verbose or n_total <= 20
        seed = None
    elif a.ids:
        spec = a.ids
        if spec.startswith("@"):
            with open(spec[1:]) as fh:
                spec = fh.read()
        ids = [x.strip() for x in re.split(r"[,\s]+", spec) if x.strip()]
        dsl = []
        for did in ids:
            s, idx = gen.parse_id(did)
            dsl.append(gen.generate(s, idx, types))
        n_total = len(dsl)
        size = a.chunk or max(1, min(100, -(-n_total // max(1, a.jobs))))
        for ci in range(0, n_total, size):
            jobs.append({"datasets": dsl[ci:ci + size]})
        verbose = verbose or n_total <= 20
        seed = sorted(set(gen.parse_id(x)[0] for x in ids))
    else:
        n_total, seed = a.n, a.seed
        size = a.chunk or max(5, min(100, -(-n_total // (4 * max(1, a.jobs)))))
        for ci in range(0, n_total, size):
            jobs.append({"seed": a.seed, "indices": list(range(ci, min(n_total, ci + size))), "types": types})
    for i, j in enumerate(jobs):
        j.update(chunk=i, only=only, workdir=workdir, rscript=rscript, keep=a.keep, records=verbose,
                 timeout=a.timeout)

    # ---- run
    results = []
    print("metafor fuzz: %d datasets, %d chunks, %d workers -> %s" % (n_total, len(jobs), a.jobs, out),
          file=sys.stderr)
    if a.jobs <= 1 or len(jobs) == 1:
        for j in jobs:
            results.append(process_chunk(j))
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=a.jobs) as pool:
            done = 0
            for res in pool.imap_unordered(process_chunk, jobs):
                results.append(res)
                done += res.get("n", 0) or 0
                print("  %d/%d datasets (%.0f s)" % (done, n_total, time.time() - t_start), file=sys.stderr)
    checks, classes, records, fatal, stats = merge(results)
    meta = {"command": " ".join([os.path.relpath(sys.argv[0], os.getcwd())] + (argv or sys.argv[1:])),
            "seed": seed, "n": n_total, "jobs": a.jobs, "only": only, "types": types, "versions": versions,
            "python": sys.version.split()[0], "wall": time.time() - t_start}
    summary = build_summary(checks, classes, fatal, stats, meta)

    # ---- minimize
    if a.minimize:
        mins = []
        by_id = {}
        fails = [r for r in summary["classes"] if r["status"] == "FAIL"]
        for r in fails[:a.minimize]:
            did = r["examples"][0]["id"]
            if did not in by_id:
                try:
                    s, idx = gen.parse_id(did)
                    by_id[did] = gen.generate(s, idx, types)
                except ValueError:
                    continue
            ds = by_id[did]
            small, hit = minimize(ds, r["func"], r["field"], only, rscript, workdir)
            mins.append({"func": r["func"], "field": r["field"], "cond": r["cond"], "k_from": n_studies(ds),
                         "k_to": n_studies(small), "engine": (hit or r["examples"][0]).get("engine"),
                         "metafor": (hit or r["examples"][0]).get("metafor"), "dataset": small})
            print("  minimized %s/%s: k %d -> %d" % (r["func"], r["field"], n_studies(ds), n_studies(small)),
                  file=sys.stderr)
        summary["minimized"] = mins
        summary["meta"]["wall"] = time.time() - t_start

    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(jsonable(summary), fh, indent=1)
    write_markdown(summary, os.path.join(out, "summary.md"))
    if records:
        with open(os.path.join(out, "records.json"), "w") as fh:
            json.dump(records, fh, indent=1)

    # ---- console report
    t = summary["totals"]
    print("checks %d | FAIL classes %d (%d mismatches) | KNOWN classes %d (%d; %s) | fatal chunks %d | %.1f s"
          % (t["checks"], t["fail_classes"], t["fail_mismatches"], t["known_classes"], t["known_mismatches"],
             ", ".join("%s %d" % kv for kv in sorted(t["known_by_kind"].items())), t["fatal_chunks"],
             summary["meta"]["wall"]))
    for k, n in sorted(t["fail_by_defect"].items()):
        print("  FAIL %-22s %4d classes, %4d datasets" % (k, n, t["fail_datasets_by_defect"].get(k, 0)))
    for r in summary["classes"]:
        if r["status"] != "FAIL" or (r["defect"] and not verbose):
            continue
        ex = r["examples"][0]
        print("  FAIL %-28s %-22s %-40s n=%-5d ds=%-4d e.g. %s[%s] %s vs %s  [%s]" % (
            r["func"], r["field"], r["cond"][:40], r["n_mismatch"], r["n_datasets"], ex["id"], ex.get("idx"),
            _fmt(ex.get("engine")), _fmt(ex.get("metafor")), r["defect"] or "untriaged"))
    for f in fatal:
        print("  FATAL chunk %s: %s" % (f["chunk"], f["error"][-500:]))
    if verbose:
        for rec in records:
            print("  %s %s/%s %s idx=%s engine=%s metafor=%s tags=%s" % (
                "KNOWN" if rec.get("known") else "MISM ", rec["func"], rec["field"], rec["id"], rec.get("idx"),
                _fmt(rec["engine"]), _fmt(rec["metafor"]), ",".join(rec["tags"])))
    print("summary: %s" % os.path.join(out, "summary.md"))
    if fatal:
        return 2
    return 1 if t["fail_classes"] else 0


if __name__ == "__main__":
    sys.exit(main())
