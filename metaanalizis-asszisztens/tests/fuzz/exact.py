# -*- coding: utf-8 -*-
"""Exact rational-arithmetic reference values for adjudicating ill-conditioned mismatches.

When engine and metafor disagree on extreme data (e.g. a sampling-variance ratio of 1e7), either
side may have lost the digits. These helpers recompute the closed-form quantities exactly with
fractions.Fraction from the very same double inputs, so run_fuzz.py can tag a mismatch with
"exact=engine" (metafor is the inaccurate side: oracle limitation) or "exact=metafor" (engine
defect) or "exact=neither".

Covered (closed forms only): the weighted least squares fit of a meta-regression at a given
tau2 (coefficients, Q_E, model-based z or Knapp-Hartung SEs, t/z statistics and the omnibus QM),
tr(P), the DerSimonian-Laird, Hedges and Sidik-Jonkman tau2 with moderators, the Paule-Mandel
estimating equation Q_E(tau2) = k - p (to check a candidate PM tau2), and the common-effect
I2 = (Q_E - df)/Q_E. REML / ML are not covered (run_fuzz.py compares their likelihoods instead).
Stdlib only.
"""
import math
from fractions import Fraction as F


def _solve(A, b):
    n = len(A)
    M = [row[:] + [bb] for row, bb in zip(A, b)]
    for c in range(n):
        piv = next((r for r in range(c, n) if M[r][c] != 0), None)
        if piv is None:
            raise ZeroDivisionError("singular")
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c and M[r][c] != 0:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * q for a, q in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def _inverse(A):
    n = len(A)
    cols = [_solve(A, [F(int(i == j)) for i in range(n)]) for j in range(n)]
    return [list(r) for r in zip(*cols)]


def wls(X, y, v, tau2=0.0):
    """Exact WLS at the given tau2: (coefficients, (X'WX)^-1, residuals, weights)."""
    Xf = [[F(a) for a in row] for row in X]
    yf = [F(a) for a in y]
    t = F(tau2)
    w = [1 / (F(a) + t) for a in v]
    k, p = len(yf), len(Xf[0])
    A = [[sum(w[i] * Xf[i][a] * Xf[i][b] for i in range(k)) for b in range(p)] for a in range(p)]
    rhs = [sum(w[i] * Xf[i][a] * yf[i] for i in range(k)) for a in range(p)]
    b = _solve(A, rhs)
    e = [yf[i] - sum(Xf[i][j] * b[j] for j in range(p)) for i in range(k)]
    return b, _inverse(A), e, w, Xf


def qe(X, y, v, tau2=0.0):
    b, _, e, w, _ = wls(X, y, v, tau2)
    return float(sum(wi * ei * ei for wi, ei in zip(w, e)))


def coefficients(X, y, v, tau2=0.0):
    return [float(x) for x in wls(X, y, v, tau2)[0]]


def tr_p(X, v, tau2=0.0):
    b, Mi, e, w, Xf = wls(X, [0.0] * len(v), v, tau2)
    k, p = len(w), len(Xf[0])
    A = [[sum(w[i] ** 2 * Xf[i][a] * Xf[i][c] for i in range(k)) for c in range(p)] for a in range(p)]
    return sum(w) - sum(Mi[a][c] * A[c][a] for a in range(p) for c in range(p))


def tau2_dl(X, y, v):
    """DerSimonian-Laird tau2 with moderators: max(0, (Q_E - (k - p)) / tr(P))."""
    k, p = len(y), len(X[0])
    b, Mi, e, w, Xf = wls(X, y, v, 0.0)
    q = sum(wi * ei * ei for wi, ei in zip(w, e))
    t = tr_p(X, v, 0.0)
    return float(max(F(0), (q - (k - p)) / t))


def i2_fe(X, y, v):
    k, p = len(y), len(X[0])
    q = F(qe(X, y, v))
    return float(100 * max(F(0), (q - (k - p)) / q)) if q > 0 else 0.0


def i2_res(X, y, v, tau2):
    """Residual I2 = 100 tau2 / (tau2 + (k - p)/tr(P_FE)) (metafor rma.uni)."""
    k, p = len(y), len(X[0])
    s2 = F(k - p) / tr_p(X, v, 0.0)
    t = F(tau2)
    return float(100 * t / (t + s2))


def fit_stats(X, y, v, tau2=0.0, test="z"):
    """Exact WLS inference at the given tau2, as meta_regression() / rma.uni report it:
    {'b': [...], 'se': [...], 'stat': [...], 'QM': float or None} (floats). test 'knha' scales the
    covariance by s2 = Q_E(tau2)/(k - p) and reports QM as F = QM/m; the omnibus test excludes the
    intercept when the first column is all ones. With s2 = 0 (exact perfect fit) se = 0 and stat/QM
    are None."""
    b, Mi, e, w, Xf = wls(X, y, v, tau2)
    k, p = len(y), len(Xf[0])
    q = F(1)
    if test == "knha":
        q = sum(wi * ei * ei for wi, ei in zip(w, e)) / (k - p)
        if q == 0:         # exact perfect fit: s2 = 0, se = 0, t = b/0 undefined
            return {"b": [float(a) for a in b], "se": [0.0] * p, "stat": None, "QM": None}
    var = [q * Mi[j][j] for j in range(p)]
    se = [_sqrt(a) for a in var]
    stat = [float(b[j]) / se[j] if se[j] > 0 else None for j in range(p)]
    idx = list(range(1, p)) if all(row[0] == 1 for row in Xf) else list(range(p))
    qm = None
    if idx:
        sub = _inverse([[Mi[a][c] for c in idx] for a in idx])
        qm = sum(b[a] * sum(sub[i][j] * b[c] for j, c in enumerate(idx)) for i, a in enumerate(idx)) / q
        if test == "knha":
            qm = qm / len(idx)
        qm = float(qm)
    return {"b": [float(a) for a in b], "se": se, "stat": stat, "QM": qm}


def _sqrt(x):
    """float(sqrt(x)) of a non-negative Fraction, from an exact integer square root (>= 80 bits)."""
    if x <= 0:
        return 0.0
    n, d = x.numerator, x.denominator
    shift = max(0, (d.bit_length() - n.bit_length()) // 2 + 80)
    return math.isqrt((n << (2 * shift)) // d) / (1 << shift)


def pm_equation(X, y, v, tau2):
    """Q_E(tau2) / (k - p) for a candidate Paule-Mandel tau2 (exactly 1 at the PM root; at the
    boundary tau2 = 0 the PM estimate is 0 iff this ratio is <= 1)."""
    k, p = len(y), len(X[0])
    b, _, e, w, _ = wls(X, y, v, tau2)
    return float(sum(wi * ei * ei for wi, ei in zip(w, e)) / (k - p))


def tau2_he(X, y, v):
    """Hedges (HE) tau2 with moderators: max(0, (RSS_OLS - tr(P_OLS V)) / (k - p))."""
    k, p = len(y), len(X[0])
    b, Mi, e, w, Xf = wls(X, y, [1.0] * k, 0.0)
    rss = sum(ei * ei for ei in e)
    h = [sum(Xf[i][a] * Mi[a][c] * Xf[i][c] for a in range(p) for c in range(p)) for i in range(k)]
    tr_pv = sum(F(vi) * (1 - hi) for vi, hi in zip(v, h))
    return float(max(F(0), (rss - tr_pv) / (k - p)))


def tau2_sj(X, y, v):
    """Sidik-Jonkman tau2 with moderators (metafor): t0 = sum (y - ybar)^2 / k, tau2 = t0 Q_E(t0)/(k - p).
    t0 is rounded to a double first, as both programs evaluate Q_E at the double t0."""
    k, p = len(y), len(X[0])
    yf = [F(a) for a in y]
    ybar = sum(yf) / k
    t0 = float(sum((a - ybar) ** 2 for a in yf) / k)
    if t0 <= 0:
        return 0.0
    b, _, e, w, _ = wls(X, y, v, t0)
    return float(F(t0) * sum(wi * ei * ei for wi, ei in zip(w, e)) / (k - p))


def is_perfect_fit(X, y, v, tau2=0.0, rtol=1e-26, floor=1e-28):
    """The engine's Knapp-Hartung perfect-fit criterion (metaelemzes.moderators.is_perfect_fit:
    rss <= rtol * centred weighted SS of y, or rss <= floor * sum w y^2) on the EXACT weighted rss
    at tau2. True: the residuals of the actual double inputs are rounding-level, so the engine's
    se = 0 convention is backed by exact arithmetic."""
    b, _, e, w, Xf = wls(X, y, v, tau2)
    yf = [F(a) for a in y]
    rss = sum(wi * ei * ei for wi, ei in zip(w, e))
    swy2 = sum(wi * a * a for wi, a in zip(w, yf))
    if all(row[0] == 1 for row in Xf):
        m = sum(wi * a for wi, a in zip(w, yf)) / sum(w)
        tss = sum(wi * (a - m) ** 2 for wi, a in zip(w, yf))
    else:
        tss = swy2
    return rss <= F(rtol) * tss or rss <= F(floor) * swy2


def verdict(engine, metafor, exact_value, rtol=1e-6):
    """Which side agrees with the exact value: 'engine' / 'metafor' / 'both', or, when neither is
    within rtol, 'neither:engine' / 'neither:metafor' (the closer one)."""
    def err(x):
        try:
            return abs(float(x) - exact_value)
        except (TypeError, ValueError):
            return float("inf")
    ea, eb = err(engine), err(metafor)
    tol = rtol * abs(exact_value) + 1e-300
    a, b = ea <= tol, eb <= tol
    if a and b:
        return "both"
    if a or b:
        return "engine" if a else "metafor"
    return "neither:engine" if ea < eb else "neither:metafor"
