# -*- coding: utf-8 -*-
"""TESZT-CSONK a motor v1 kettős kinyerés-homlokzatához (nem a motor!) — a munkapad kettős kinyerés-végpontjainak
és a felület fixture-einek tesztjéhez, amíg a ``metaelemzes.api`` v1-függvényei (a párhuzamos motor-munkafolyamat
``kettos.py``-ja) el nem készülnek. A függvénynevek és -alakok a ``ma_gui/routes/extraction_dual_common.ENGINE``
elvárásai:

  compare(a, b, key=None, tolerance=None, measure=None) -> szk.ma.compare-result/v1
      a, b: {header, rows (nyers szöveges cellák), row_uids, dataset, decimal_mark}
      + additív mezők: pairs[{key, row_uid_a, row_uid_b}], disagreements[].hint_code / kb, impact.pooled_text,
        summary.agreement_pct_text, summary.by_field[f].kappa_text ({hu, en}), agreement_text {hu, en}
  consensus_table(a, b, consensus, key=None) -> {header, rows[{row_uid, cells}], reconciled[{row_uid, field, key}]}
      (csak a ``patch(..., names=NAMES_FULL)`` illeszti be; alapból a munkapad szöveg-választó tartaléka fut)

A szabályok egyszerűsítettek (a valódi súgó-heurisztika, a hatás és a κ a motoré); a szövegek a 4.0 konvenció
szerint tizedesponttal készülnek. ``patch(engine)`` a ``metaelemzes.api``-ba illeszti a függvényeket
(``mock.patch.object(..., create=True)``)."""
import math
from unittest import mock

from metaelemzes import api

NAMES = ("compare",)
NAMES_FULL = ("compare", "consensus_table")
NUMERIC = ("m1", "sd1", "n1", "m2", "sd2", "n2", "e1", "e2", "se1", "se2")
CATEGORICAL = ("rob",)


def i18n(hu, en=None):
    return {"hu": hu, "en": en if en is not None else hu}


def _num(text):
    if text is None:
        return None
    t = str(text).strip().replace("−", "-")
    if not t:
        return None
    if "," in t and "." not in t:
        t = t.replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _fmt(v, nd=2):
    return ("%%.%df" % nd) % v


def _canon(header):
    cmap = api.column_map(list(header))
    out = []
    used = set()
    for name in header:
        hit = next((c for c, o in cmap.items() if o == name and c not in used), name)
        used.add(hit)
        out.append(hit)
    return out


def _rows(t):
    fields = _canon(t["header"])
    out = []
    for uid, cells in zip(t["row_uids"], t["rows"]):
        out.append((uid, {f: (cells[i] if i < len(cells) else "") for i, f in enumerate(fields)}))
    return fields, out


def _kappa(pairs):
    cats = sorted({x for p in pairs for x in p})
    n = len(pairs)
    if n == 0 or len(cats) < 2:
        return None, None
    po = sum(1 for a, b in pairs if a == b) / float(n)
    pe = sum((sum(1 for a, _ in pairs if a == c) / float(n)) * (sum(1 for _, b in pairs if b == c) / float(n))
             for c in cats)
    if pe >= 1:
        return None, None
    k = (po - pe) / (1 - pe)
    se = math.sqrt(max(po * (1 - po), 1e-12) / (n * (1 - pe) ** 2))
    return k, [max(-1.0, k - 1.96 * se), min(1.0, k + 1.96 * se)]


class StubEngine(object):
    def __init__(self):
        self.calls = []

    # ------------------------------------------------------------------ compare
    def compare(self, a, b, key=None, tolerance=None, measure=None):
        self.calls.append(("compare", key, measure))
        fa, ra = _rows(a)
        fb, rb = _rows(b)
        if not key:
            key = ["study_id"] if "study_id" in fa and "study_id" in fb else ["study"]
        for k in key:
            if k not in fa or k not in fb:
                raise ValueError("Hiányzó kulcsoszlop: %s" % k)

        def kt(cells):
            return "|".join(cells.get(k, "").strip() for k in key)

        ia = {kt(c): (u, c) for u, c in ra}
        ib = {kt(c): (u, c) for u, c in rb}
        common = [f for f in fa if f in fb and f not in key]
        dis, by_field, pairs = [], {}, []
        agree = compared = 0
        cat_pairs = {}
        for k, (ua, ca) in ia.items():
            if k not in ib:
                continue
            ub, cb = ib[k]
            pairs.append({"key": k, "row_uid_a": ua, "row_uid_b": ub})
            for f in common:
                sa, sb = ca.get(f, ""), cb.get(f, "")
                bf = by_field.setdefault(f, {"compared": 0, "disagree": 0})
                bf["compared"] += 1
                compared += 1
                if f in CATEGORICAL:
                    cat_pairs.setdefault(f, []).append((sa.strip().lower(), sb.strip().lower()))
                if sa.strip() == sb.strip():
                    agree += 1
                    continue
                va, vb = _num(sa), _num(sb)
                d = {"key": k, "row_uid_a": ua, "row_uid_b": ub, "field": f, "a": sa, "b": sb,
                     "a_value": va, "b_value": vb, "hint": None, "hint_code": None, "impact": None}
                if not sa.strip():
                    d["kind"] = "missing_a"
                elif not sb.strip():
                    d["kind"] = "missing_b"
                elif f in CATEGORICAL:
                    d["kind"] = "category"
                elif va is not None and vb is not None and abs(va - vb) <= (tolerance or 0) + 1e-12:
                    d["kind"] = "format_only"
                    d["hint"] = i18n("csak írásmódban tér el (V023)", "formatting only (V023)")
                    d["hint_code"] = "format_only"
                    d["kb"] = "V023"
                elif va is None or vb is None:
                    d["kind"] = "parse"
                    d["hint"] = i18n("az egyik érték nem szám", "one value is not a number")
                    d["hint_code"] = "parse"
                else:
                    d["kind"] = "value"
                    self._hint(d, f, va, vb, ca, cb)
                    d["impact"] = {"measure": measure or "MD", "yi_a": round(va / 10.0, 4), "yi_b": round(vb / 10.0, 4),
                                   "text": i18n("%s %s → %s" % (measure or "MD", _fmt(va / 10.0), _fmt(vb / 10.0))),
                                   "pooled_text": i18n("összesített: 0.49 → %s" % _fmt(0.49 + (vb - va) / 100.0),
                                                       "pooled: 0.49 → %s" % _fmt(0.49 + (vb - va) / 100.0)),
                                   "rank": round(abs(va - vb), 4)}
                if d["kind"] != "format_only":
                    bf["disagree"] += 1
                else:
                    agree += 0
                dis.append(d)
        for f, prs in cat_pairs.items():
            k, ci = _kappa(prs)
            by_field[f]["kappa"] = k
            by_field[f]["kappa_ci"] = ci
            if k is not None:
                by_field[f]["kappa_text"] = i18n("κ = %s [%s; %s]" % (_fmt(k), _fmt(ci[0]), _fmt(ci[1])))
        n_dis = sum(1 for d in dis if d["kind"] != "format_only")
        pct = 100.0 * (compared - n_dis) / compared if compared else None
        only_a = [k for k in ia if k not in ib]
        only_b = [k for k in ib if k not in ia]
        dis.sort(key=lambda d: -((d.get("impact") or {}).get("rank") or 0))
        summary = {"rows_a": len(ra), "rows_b": len(rb), "matched": len(pairs), "cells_compared": compared,
                   "agree": compared - n_dis, "disagree": n_dis, "agreement_pct": pct, "by_field": by_field,
                   "agreement_pct_text": i18n(_fmt(pct, 1) + "%") if pct is not None else None}
        kap = [(f, v) for f, v in sorted(by_field.items()) if v.get("kappa_text")]
        txt_hu = ("A két kinyerő %d vizsgálat %d celláját vetette össze; az egyezés %s volt%s. Az eltéréseket "
                  "konszenzussal oldottuk fel." % (len(pairs), compared, summary["agreement_pct_text"]["hu"] if pct is not None
                                                   else "–",
                                                   "".join("; %s: %s" % (f, v["kappa_text"]["hu"]) for f, v in kap)))
        txt_en = ("Two extractors compared %d cells of %d studies; agreement was %s%s. Disagreements were resolved by "
                  "consensus." % (compared, len(pairs), summary["agreement_pct_text"]["en"] if pct is not None else "–",
                                  "".join("; %s: %s" % (f, v["kappa_text"]["en"]) for f, v in kap)))
        return {"schema": "szk.ma.compare-result/v1", "key": list(key), "summary": summary, "disagreements": dis,
                "only_a": only_a, "only_b": only_b, "pairs": pairs, "agreement_text": {"hu": txt_hu, "en": txt_en}}

    @staticmethod
    def _hint(d, f, va, vb, ca, cb):
        lo, hi = sorted([abs(va), abs(vb)])
        if lo > 0 and abs(hi / lo - 10) < 0.05:
            d.update(hint=i18n("10× eltérés: tizedesjel vagy mértékegység? (V012 jellegű)",
                               "10× difference: decimal mark or unit? (V012-like)"), hint_code="x10", kb="V012")
            return
        if f.startswith("sd"):
            n = _num(ca.get("n" + f[-1]))
            if n and lo > 0 and abs(hi / lo - math.sqrt(n)) / math.sqrt(n) < 0.03:
                d.update(hint=i18n("a ≈ b·√n: SE/SD csere? (V011)", "a ≈ b·√n: SE/SD swap? (V011)"),
                         hint_code="se_sd_swap", kb="V011")
                return
        if f[-1:] in ("1", "2"):
            other = f[:-1] + ("2" if f[-1] == "1" else "1")
            if _num(ca.get(f)) == _num(cb.get(other)) and _num(ca.get(other)) == _num(cb.get(f)):
                d.update(hint=i18n("felcserélt karok (kezelt ↔ kontroll)?", "swapped arms (treatment ↔ control)?"),
                         hint_code="arms_swapped")
                return
        d.update(hint=i18n("eltérő érték — nézd meg a forrást", "different value — check the source"),
                 hint_code="value")

    # ------------------------------------------------------------------ konszenzus-tábla (opcionális)
    def consensus_table(self, a, b, consensus, key=None):
        self.calls.append(("consensus_table", key))
        fa, ra = _rows(a)
        fb, rb = _rows(b)
        key = key or consensus.get("key")
        dec = {(d["key"], d["field"]): d for d in consensus.get("decisions") or []}

        def kt(cells):
            return "|".join(cells.get(k, "").strip() for k in key)

        ib = {kt(c): (u, c) for u, c in rb}
        ka = set()
        rows, rec = [], []
        for uid, c in ra:
            k = kt(c)
            ka.add(k)
            if k not in ib and (k, "*") in dec and dec[(k, "*")]["chosen"] == "b":
                continue
            cells = []
            for f in fa:
                d = dec.get((k, f))
                if d is not None:
                    cells.append(d.get("value") or "")
                    rec.append({"row_uid": uid, "field": f, "key": k})
                else:
                    cells.append(c.get(f, ""))
            rows.append({"row_uid": uid, "cells": cells})
        for k, (uid, c) in ib.items():
            if k not in ka and (k, "*") in dec and dec[(k, "*")]["chosen"] == "b":
                rows.append({"row_uid": uid, "cells": [c.get(f, "") for f in fa]})
        return {"header": list(a["header"]), "rows": rows, "reconciled": rec}


def patch(engine, names=NAMES):
    """[mock.patch.object(...)] a metaelemzes.api-ra (create=True)."""
    return [mock.patch.object(api, n, getattr(engine, n), create=True) for n in names]
