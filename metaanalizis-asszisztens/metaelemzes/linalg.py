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


class WeightedQR(object):
    """Householder-QR az A = W^½ X mátrixra (súlyozott legkisebb négyzetek, kis p).

    A normálegyenletek (XᵀWX explicit inverze) helyett: a kondíciószám cond(A), nem cond(A)², és
    a projekcióból adódó mennyiségek (reziduumok, 1 − h_ii, tr(P)) kiejtés nélkül számolhatók.
    Oszlopcsere (pivoting) és a sorok |A_i| szerint csökkenő rendezése: soronként stabil erősen
    eltérő súlyoknál is (Cox & Higham 1998).

    Mértékegység-független rangvizsgálat: minden oszlopot a saját eredeti súlyozott normájával
    (s_j = ‖W^½ x_j‖) skálázunk, és a faktorizáció a skálázott mátrixon fut (a b, a kovariancia és
    a log det a végén visszaskálázódik; a reziduumok, 1 − h_ii, tr(P) a skálától függetlenek).
    Egy oszlop akkor kollineáris, ha a többire vetítés utáni maradéka kisebb, mint rtol-szor a
    SAJÁT eredeti normája (relatív maradék = sin(az oszlop és a többi által kifeszített tér
    szöge)); így pl. egy dollárban mért GDP és egy arányként mért moderátor együtt nem tűnik
    kollineárisnak csak azért, mert a normáik 1e7-szer eltérnek (az R lm() dqrdc2-tesztje is az
    oszlop saját normájához mér). A főelem-választás is a skálázott maradéknormák szerint
    történik, így a döntés nem függ a moderátorok mértékegységétől. rtol = 1e-7 (mint az R
    qr() / lm tol-ja; a pontosan kollineáris tervek relatív maradéka ~1e-16, a még megbízhatóan
    számolható közel-kollineárisaké >= ~1e-7). Csupa-nulla oszlop: SingularMatrixError.
    """

    def __init__(self, x, w, rtol=1e-7):
        k, p = len(x), len(x[0])
        self.k, self.p = k, p
        self.sw = sw = [math.sqrt(a) for a in w]
        self.w = list(w)
        # oszlopskálák: az eredeti súlyozott oszlopnormák (a rangvizsgálat viszonyítási alapja)
        scale = [math.hypot(*[sw[i] * x[i][j] for i in range(k)]) for j in range(p)]
        if not all(math.isfinite(s) and s > 0 for s in scale):
            raise SingularMatrixError("szinguláris mátrix (kollineáris moderátorok?)")
        self.scale = scale
        order = sorted(range(k), key=lambda i: (-max(abs(x[i][j]) / scale[j] for j in range(p)) * sw[i], i))
        self.order = order
        cols = [[sw[i] * x[i][j] / scale[j] for i in order] for j in range(p)]
        perm = list(range(p))
        vs, betas = [], []
        r = [[0.0] * p for _ in range(p)]
        for j in range(p):
            norms = [math.hypot(*cols[c][j:]) if j < k else 0.0 for c in range(j, p)]
            jj = j + max(range(len(norms)), key=lambda t: norms[t])
            if jj != j:
                cols[j], cols[jj] = cols[jj], cols[j]
                perm[j], perm[jj] = perm[jj], perm[j]
                for row in r[:j]:
                    row[j], row[jj] = row[jj], row[j]
            nrm = norms[jj - j]
            # a skálázott oszlop eredeti normája 1: a maradék a saját eredeti norma rtol-szorosa alatt
            if not (nrm > rtol) or not math.isfinite(nrm):
                raise SingularMatrixError("szinguláris mátrix (kollineáris moderátorok?)")
            col = cols[j]
            alpha = -math.copysign(nrm, col[j])
            v = col[j:]
            v[0] -= alpha
            vv = sum(a * a for a in v)
            beta = 2.0 / vv
            for c in range(j + 1, p):
                cc = cols[c]
                s = beta * sum(a * b for a, b in zip(v, cc[j:]))
                if s:
                    for t in range(len(v)):
                        cc[j + t] -= s * v[t]
                r[j][c] = cc[j]
            r[j][j] = alpha
            vs.append(v)
            betas.append(beta)
        self.r, self.perm, self._v, self._beta = r, perm, vs, betas
        self._lev = None

    def _qt(self, z):
        """Qᵀz (z a rendezett sorrendben), helyben."""
        for j, (v, beta) in enumerate(zip(self._v, self._beta)):
            s = beta * sum(a * b for a, b in zip(v, z[j:]))
            if s:
                for t in range(len(v)):
                    z[j + t] -= s * v[t]
        return z

    def _q(self, z):
        """Qz (z a rendezett sorrendben), helyben."""
        for j in range(self.p - 1, -1, -1):
            v, beta = self._v[j], self._beta[j]
            s = beta * sum(a * b for a, b in zip(v, z[j:]))
            if s:
                for t in range(len(v)):
                    z[j + t] -= s * v[t]
        return z

    def _rsolve(self, c):
        p, r = self.p, self.r
        z = [0.0] * p
        for i in range(p - 1, -1, -1):
            z[i] = (c[i] - sum(r[i][j] * z[j] for j in range(i + 1, p))) / r[i][i]
        return z

    def solve(self, y):
        """(b, e, rss): a WLS-együtthatók, a reziduumok y − Xb (eredeti sorrend) és Σ w e²."""
        p, order = self.p, self.order
        c = self._qt([self.sw[i] * y[i] for i in order])
        z = self._rsolve(c[:p])
        b = [0.0] * p
        for j, pj in enumerate(self.perm):
            b[pj] = z[j] / self.scale[pj]
        rr = [0.0] * p + c[p:]
        rss = sum(a * a for a in c[p:])
        self._q(rr)
        e = [0.0] * self.k
        for t, i in enumerate(order):
            e[i] = rr[t] / self.sw[i]
        return b, e, rss

    def cov(self):
        """(XᵀWX)⁻¹ = S⁻¹ Π R⁻¹ R⁻ᵀ Πᵀ S⁻¹ (S = diag(oszlopskálák))."""
        p, r = self.p, self.r
        rinv = [[0.0] * p for _ in range(p)]
        for j in range(p):
            rinv[j][j] = 1.0 / r[j][j]
            for i in range(j - 1, -1, -1):
                rinv[i][j] = -sum(r[i][t] * rinv[t][j] for t in range(i + 1, j + 1)) / r[i][i]
        m = [[0.0] * p for _ in range(p)]
        for a in range(p):
            for b in range(a, p):
                pa, pb = self.perm[a], self.perm[b]
                s = sum(rinv[a][t] * rinv[b][t] for t in range(b, p)) / (self.scale[pa] * self.scale[pb])
                m[pa][pb] = m[pb][pa] = s
        return m

    def logdet(self):
        """log det(XᵀWX) = 2 Σ log|R_jj| + 2 Σ log s_j."""
        return 2.0 * (sum(math.log(abs(self.r[j][j])) for j in range(self.p))
                      + sum(math.log(s) for s in self.scale))

    def leverages(self):
        """(h, 1 − h) az eredeti sorrendben, h_ii = (W^½ X M Xᵀ W^½)_ii. A nagy hatású (h > ½)
        soroknál 1 − h = ‖(Qᵀe_i)[p:]‖², kiejtés nélkül (pl. domináns súlyú vizsgálat)."""
        if self._lev is not None:
            return self._lev
        k, p = self.k, self.p
        qcols = []
        for j in range(p):
            z = [0.0] * k
            z[j] = 1.0
            qcols.append(self._q(z))
        qrows = [[qcols[j][t] for j in range(p)] for t in range(k)]
        h_s = [sum(a * a for a in row) for row in qrows]
        m_s = []
        for t in range(k):
            if h_s[t] > 0.5:
                z = [0.0] * k
                z[t] = 1.0
                self._qt(z)
                m_s.append(sum(a * a for a in z[p:]))
            else:
                m_s.append(1.0 - h_s[t])
        h, m1 = [0.0] * k, [0.0] * k
        for t, i in enumerate(self.order):
            h[i], m1[i] = h_s[t], m_s[t]
        self._lev = (h, m1)
        self._qrows = qrows
        return self._lev

    def traces(self):
        """tr(P), tr(PP) a P = W − WX(XᵀWX)⁻¹XᵀW projekcióhoz, csak nemnegatív tagokból:
        tr(P) = Σ w_i (1 − h_ii); tr(PP) = Σ w_i²(1 − h_ii)² + Σ_i w_i Σ_{j≠i} w_j H_ij²,
        a második tag Q-sorokkal és előtag/utótag-összegekkel O(k p²) időben."""
        _, m1 = self.leverages()
        k, p, w = self.k, self.p, self.w
        ws = [w[i] for i in self.order]
        m1s = [m1[i] for i in self.order]
        q = self._qrows
        tr_p = sum(a * b for a, b in zip(ws, m1s))
        pre = [[[0.0] * p for _ in range(p)]]
        for t in range(k):
            g = [row[:] for row in pre[-1]]
            qt, wt = q[t], ws[t]
            for a in range(p):
                fa = wt * qt[a]
                ga = g[a]
                for b in range(p):
                    ga[b] += fa * qt[b]
            pre.append(g)
        suf = [[0.0] * p for _ in range(p)]
        off = 0.0
        for t in range(k - 1, -1, -1):
            qt, wt = q[t], ws[t]
            gp = pre[t]
            quad = sum(qt[a] * sum((gp[a][b] + suf[a][b]) * qt[b] for b in range(p)) for a in range(p))
            off += wt * max(0.0, quad)
            for a in range(p):
                fa = wt * qt[a]
                sa = suf[a]
                for b in range(p):
                    sa[b] += fa * qt[b]
        tr_pp = sum((a * b) ** 2 for a, b in zip(ws, m1s)) + off
        return tr_p, tr_pp
