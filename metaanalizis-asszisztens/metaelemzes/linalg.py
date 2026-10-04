# -*- coding: utf-8 -*-
"""Minimális lineáris algebra a meta-regresszióhoz (kis p×p mátrixok)."""
import math


class SingularMatrixError(ArithmeticError):
    pass


def transpose(a):
    return [list(col) for col in zip(*a)]


def matmul(a, b):
    bt = transpose(b)
    return [[sum(x * y for x, y in zip(row, col)) for col in bt] for row in a]


def matvec(a, v):
    return [sum(x * y for x, y in zip(row, v)) for row in a]


def identity(n):
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def inverse(a):
    """Gauss–Jordan elimináció részleges főelem-kiválasztással."""
    n = len(a)
    m = [list(map(float, row)) + e for row, e in zip(a, identity(n))]
    scale = max(abs(x) for row in a for x in row) or 1.0
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12 * scale:
            raise SingularMatrixError("szinguláris mátrix (kollineáris moderátorok?)")
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        m[col] = [x / p for x in m[col]]
        for r in range(n):
            if r != col:
                f = m[r][col]
                if f:
                    m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [row[n:] for row in m]


def logdet_spd(a):
    """log det(A) szimmetrikus pozitív definit A-ra (Cholesky-felbontás)."""
    n = len(a)
    low = [[0.0] * n for _ in range(n)]
    out = 0.0
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(low[i][m] * low[j][m] for m in range(j))
            if i == j:
                if not (s > 0):
                    raise SingularMatrixError("nem pozitív definit mátrix (kollineáris moderátorok?)")
                low[i][i] = math.sqrt(s)
                out += math.log(s)
            else:
                low[i][j] = s / low[j][j]
    return out


def xtwx(x, w):
    """Xᵀ diag(w) X."""
    p = len(x[0])
    out = [[0.0] * p for _ in range(p)]
    for row, wi in zip(x, w):
        for i in range(p):
            ri = row[i] * wi
            if ri:
                o = out[i]
                for j in range(p):
                    o[j] += ri * row[j]
    return out


def xtwy(x, w, y):
    p = len(x[0])
    out = [0.0] * p
    for row, wi, yi in zip(x, w, y):
        for i in range(p):
            out[i] += row[i] * wi * yi
    return out


def trace(a):
    return sum(a[i][i] for i in range(len(a)))
