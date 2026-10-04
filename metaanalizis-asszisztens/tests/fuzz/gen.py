# -*- coding: utf-8 -*-
"""Seeded random dataset generator for differential testing (engine vs metafor).

Every dataset is generated from its own RNG, seeded by (seed, index), so a dataset can be
re-created exactly from its id ("<seed>-<index>") regardless of how the run was chunked.

Dataset types (field "type"):
  uni     yi/vi given directly; k = 2..40; vi from 1e-4..10 with extreme ratios (one study
          with vi ~ 1e-6..1e-7 among vi ~ 1..10), equal vi, identical yi, ties, outliers,
          small-study asymmetry; optional 1-3 moderators (continuous / binary / near-collinear),
          optional subgroup labels (2-4 groups, incl. single-study groups), an order key
          (publication year, with ties) for cumulative meta-analysis.
  bin     2x2 tables (e1, n1, e2, n2) incl. zero cells, double zeros, double 100 %, rare events,
          tiny arms (n = 1).
  cont    two-group mean/sd/n (MD, SMD, Glass, ROM), incl. n = 2, tiny/huge SD, m <= 0 (ROM NA).
  paired  pre/post m1, m2, sd1, sd2, r, n (MC, SMCC), incl. r = 1 with sd1 = sd2 (zero variance).
  prop    x/n (PR, PLN, PLO, PAS, PFT) incl. x = 0, x = n, n = 1.
  cor     r/n (COR, ZCOR) incl. r near +-1, r = 0, n = 3..5.

Stdlib only.  Usage:  python3 gen.py --n 20 --seed 1 > datasets.json
"""
import argparse
import json
import math
import random

TYPE_WEIGHTS = (("uni", 45), ("bin", 18), ("cont", 12), ("paired", 5), ("prop", 10), ("cor", 10))


def rng_for(seed, index):
    """Independent, reproducible RNG per dataset (int seeding is version-stable)."""
    return random.Random(int(seed) * 1000003 + int(index) * 7919 + 17)


def dataset_id(seed, index):
    return "%d-%05d" % (int(seed), int(index))


def parse_id(did):
    seed, index = did.rsplit("-", 1)
    return int(seed), int(index)


def _loguniform(rng, a, b):
    return math.exp(rng.uniform(math.log(a), math.log(b)))


def _weighted(rng, pairs):
    tot = sum(w for _, w in pairs)
    u = rng.uniform(0, tot)
    acc = 0.0
    for val, w in pairs:
        acc += w
        if u <= acc:
            return val
    return pairs[-1][0]


def _pick_k(rng, lo=2, hi=40):
    band = _weighted(rng, (("2", 8), ("3", 8), ("small", 24), ("mid", 35), ("big", 25)))
    if band == "2":
        k = 2
    elif band == "3":
        k = 3
    elif band == "small":
        k = rng.randint(4, 9)
    elif band == "mid":
        k = rng.randint(10, 20)
    else:
        k = rng.randint(21, 40)
    return max(lo, min(hi, k))


# ------------------------------------------------------------------ uni (yi, vi)
VI_KINDS = (("narrow", 20), ("logwide", 22), ("equal", 8), ("small", 10), ("large", 10),
            ("one_tiny", 10), ("extreme", 8), ("two_cluster", 12))
TAU_KINDS = (("zero", 18), ("small", 16), ("moderate", 20), ("large", 14), ("identical", 7),
             ("outlier", 12), ("asym", 13))


def _gen_vi(rng, k):
    kind = _weighted(rng, VI_KINDS)
    if kind == "narrow":
        vi = [rng.uniform(0.01, 0.1) for _ in range(k)]
    elif kind == "logwide":
        vi = [_loguniform(rng, 1e-4, 10.0) for _ in range(k)]
    elif kind == "equal":
        vi = [_loguniform(rng, 1e-4, 10.0)] * k
    elif kind == "small":
        vi = [_loguniform(rng, 1e-4, 1e-3) for _ in range(k)]
    elif kind == "large":
        vi = [_loguniform(rng, 1.0, 10.0) for _ in range(k)]
    elif kind == "one_tiny":
        vi = [_loguniform(rng, 0.05, 1.0) for _ in range(k)]
        vi[rng.randrange(k)] = _loguniform(rng, 1e-4, 1e-3)
    elif kind == "extreme":
        # variance ratio ~1e6..1e8: deliberately ill-conditioned
        vi = [_loguniform(rng, 1.0, 10.0) for _ in range(k)]
        vi[rng.randrange(k)] = _loguniform(rng, 1e-7, 1e-6)
    else:  # two_cluster
        vi = [(_loguniform(rng, 1e-4, 1e-3) if rng.random() < 0.5 else _loguniform(rng, 0.5, 10.0))
              for _ in range(k)]
    return kind, vi


def gen_uni(rng):
    k = _pick_k(rng)
    vkind, vi = _gen_vi(rng, k)
    sv = sorted(vi)
    scale = math.sqrt(sv[len(sv) // 2])
    tkind = _weighted(rng, TAU_KINDS)
    mu = rng.choice([0.0, rng.uniform(-1, 1), rng.uniform(-3, 3)])
    if tkind == "identical":
        yi = [mu] * k
    else:
        tau = {"zero": 0.0, "small": 0.3 * scale, "moderate": 1.0 * scale, "large": 3.0 * scale,
               "outlier": 0.5 * scale, "asym": 0.5 * scale}[tkind]
        yi = [rng.gauss(mu, math.sqrt(tau * tau + v)) for v in vi]
        if tkind == "outlier":
            yi[rng.randrange(k)] += rng.choice([-1, 1]) * (6.0 * scale + 1.0)
        elif tkind == "asym":
            yi = [y + 2.0 * math.sqrt(v) for y, v in zip(yi, vi)]
    if tkind != "identical" and rng.random() < 0.12:
        yi = [round(y, 1) for y in yi]       # ties
    ds = {"type": "uni", "vkind": vkind, "tkind": tkind, "yi": yi, "vi": vi}
    # moderators (1-3 columns)
    if k >= 4 and rng.random() < 0.6:
        p = rng.choice([1, 1, 2, 3])
        if k >= p + 2:
            cols, kinds = [], []
            for j in range(p):
                t = _weighted(rng, (("cont", 55), ("bin", 35), ("collinear", 10)))
                if t == "collinear" and j == 0:
                    t = "cont"
                if t == "cont":
                    c = [rng.uniform(0, 10) for _ in range(k)]
                elif t == "bin":
                    c = [float(rng.random() < 0.5) for _ in range(k)]
                    if len(set(c)) < 2:
                        c[0] = 1.0 - c[0]
                else:  # near-collinear with the first column
                    c = [x + rng.gauss(0, 1e-3) for x in cols[0]]
                cols.append(c)
                kinds.append(t)
            ds["mods"] = [[cols[j][r] for j in range(p)] for r in range(k)]
            ds["mod_kinds"] = kinds
            if rng.random() < 0.5:
                b = rng.uniform(-0.5, 0.5) * scale
                ds["yi"] = [y + b * ds["mods"][r][0] for r, y in enumerate(ds["yi"])]
    # subgroups
    if k >= 3 and rng.random() < 0.6:
        g = rng.choice([2, 2, 3, 4])
        g = min(g, k)
        labels = "ABCD"[:g]
        grp = [labels[i] if i < g else rng.choice(labels) for i in range(k)]
        rng.shuffle(grp)
        ds["groups"] = grp
        if rng.random() < 0.3:   # group-specific shift
            shift = {lab: rng.gauss(0, scale) for lab in labels}
            ds["yi"] = [y + shift[gg] for y, gg in zip(ds["yi"], grp)]
    # order key for cumulative MA (ties possible)
    ds["year"] = [rng.randint(1990, 1990 + max(2, k)) for _ in range(k)]
    return ds


# ------------------------------------------------------------------ binary
def _binom(rng, n, p):
    if n > 300:   # normal approximation is fine for the fuzz generator
        x = int(round(rng.gauss(n * p, math.sqrt(max(n * p * (1 - p), 1e-12)))))
        return max(0, min(n, x))
    return sum(1 for _ in range(n) if rng.random() < p)


def gen_bin(rng):
    k = _pick_k(rng, 2, 30)
    rare = rng.random() < 0.4
    rows = []
    for i in range(k):
        n1 = rng.choice([1, 5, 10, rng.randint(10, 100), rng.randint(100, 2000)])
        n2 = rng.choice([1, 5, 10, rng.randint(10, 100), rng.randint(100, 2000)])
        p2 = _loguniform(rng, 0.001, 0.05) if rare else rng.uniform(0.02, 0.9)
        orr = _loguniform(rng, 0.3, 3.0)
        o1 = p2 / (1 - p2) * orr
        p1 = o1 / (1 + o1)
        e1, e2 = _binom(rng, n1, p1), _binom(rng, n2, p2)
        u = rng.random()
        if u < 0.06:
            e1, e2 = 0, 0                 # double zero
        elif u < 0.09:
            e1, e2 = n1, n2               # double 100 %
        elif u < 0.13:
            e1 = 0
        elif u < 0.16:
            e2 = n2
        elif u < 0.18:
            e1, e2 = 0, n2                # 0 % vs 100 %
        rows.append({"study": "b%d" % i, "e1": e1, "n1": n1, "e2": e2, "n2": n2})
    return {"type": "bin", "rare": rare, "rows": rows}


# ------------------------------------------------------------------ continuous
def gen_cont(rng):
    k = _pick_k(rng, 2, 30)
    rows = []
    for i in range(k):
        n1 = rng.choice([2, 3, 5, rng.randint(5, 50), rng.randint(50, 500)])
        n2 = rng.choice([2, 3, 5, rng.randint(5, 50), rng.randint(50, 500)])
        sd1 = _loguniform(rng, 0.01, 50.0)
        sd2 = sd1 * _loguniform(rng, 0.2, 5.0)
        m2 = rng.uniform(0.5, 60.0)
        m1 = m2 + rng.gauss(0, sd1)
        if rng.random() < 0.04:
            m1 = -abs(m1)                 # ROM undefined
        rows.append({"study": "c%d" % i, "m1": m1, "sd1": sd1, "n1": n1, "m2": m2, "sd2": sd2, "n2": n2})
    return {"type": "cont", "rows": rows}


def gen_paired(rng):
    k = _pick_k(rng, 2, 25)
    rows = []
    for i in range(k):
        n = rng.choice([2, 3, 4, rng.randint(5, 60), rng.randint(60, 400)])
        sd1 = _loguniform(rng, 0.2, 20.0)
        sd2 = sd1 * _loguniform(rng, 0.5, 2.0)
        r = rng.choice([0.0, 0.5, 0.9, -0.3, 0.999, rng.uniform(-0.9, 0.99)])
        if rng.random() < 0.03:
            r, sd2 = 1.0, sd1             # zero change variance
        m1 = rng.uniform(5, 30)
        m2 = m1 - rng.gauss(1, 2)
        rows.append({"study": "p%d" % i, "m1": m1, "m2": m2, "sd1": sd1, "sd2": sd2, "r": r, "n": n})
    return {"type": "paired", "rows": rows}


def gen_prop(rng):
    k = _pick_k(rng, 2, 30)
    rows = []
    for i in range(k):
        n = rng.choice([1, 2, 5, rng.randint(5, 50), rng.randint(50, 1000)])
        p = rng.choice([0.0, 1.0, rng.uniform(0, 1), _loguniform(rng, 0.001, 0.1), 1 - _loguniform(rng, 0.001, 0.1)])
        x = _binom(rng, n, p)
        rows.append({"study": "q%d" % i, "x": x, "n": n})
    return {"type": "prop", "rows": rows}


def gen_cor(rng):
    k = _pick_k(rng, 2, 30)
    rows = []
    for i in range(k):
        n = rng.choice([3, 4, 5, rng.randint(5, 50), rng.randint(50, 2000)])
        r = rng.choice([rng.uniform(-0.99, 0.99), rng.uniform(-0.99, 0.99), 0.0, 0.999, -0.999, 0.9999,
                        -0.99999, rng.uniform(0.9, 0.999)])
        rows.append({"study": "r%d" % i, "r": r, "n": n})
    return {"type": "cor", "rows": rows}


GENERATORS = {"uni": gen_uni, "bin": gen_bin, "cont": gen_cont, "paired": gen_paired,
              "prop": gen_prop, "cor": gen_cor}


def generate(seed, index, types=None):
    """The dataset with the given (seed, index); `types` restricts the type mix."""
    rng = rng_for(seed, index)
    pairs = TYPE_WEIGHTS if not types else tuple((t, w) for t, w in TYPE_WEIGHTS if t in types)
    if not pairs:
        raise ValueError("no dataset type left after filtering: %r" % (types,))
    typ = _weighted(rng, pairs)
    ds = GENERATORS[typ](rng)
    ds["id"] = dataset_id(seed, index)
    return ds


def generate_many(seed, n, start=0, types=None):
    return [generate(seed, i, types) for i in range(start, start + n)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--types", default="", help="comma-separated subset of: " + ",".join(GENERATORS))
    a = ap.parse_args()
    types = [t for t in a.types.split(",") if t] or None
    print(json.dumps(generate_many(a.seed, a.n, a.start, types), indent=1))


if __name__ == "__main__":
    main()
