# -*- coding: utf-8 -*-
"""Exact rational-arithmetic reference values for adjudicating ill-conditioned mismatches.

When engine and metafor disagree on extreme data (e.g. a sampling-variance ratio of 1e7), either
side may have lost the digits. These helpers recompute the closed-form quantities exactly with
fractions.Fraction from the very same double inputs, so run_fuzz.py can tag a mismatch with
"exact=engine" (metafor is the inaccurate side: oracle limitation) or "exact=metafor" (engine
defect) or "exact=neither".

Covered (closed forms only): the weighted least squares fit of a meta-regression at a given
tau2 (coefficients, Q_E), tr(P), the DerSimonian-Laird tau2 with moderators, and the
common-effect I2 = (Q_E - df)/Q_E. Iterative estimators (REML, ML, PM) are not covered.
Stdlib only.
"""
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
