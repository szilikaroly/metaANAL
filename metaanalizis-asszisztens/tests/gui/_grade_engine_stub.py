# -*- coding: utf-8 -*-
"""TESZT-CSONK a motor v1 GRADE / SoF / AMSTAR 2 homlokzatához (NEM a motor!) — a munkapad végpontjainak
(ma_gui/routes/grade*.py) tesztjéhez, amíg a ``metaelemzes.api`` v1-függvényei (a párhuzamos motor-munkafolyamat:
grade_help.py, a GRADE-tár, sof, amstar2_consistency) el nem készülnek. Az aláírások a
``ma_gui/routes/grade_engine`` dokumentációja szerintiek:

  grade_get(project_dir, outcome_id)                     grade_put(project_dir, outcome_id, doc, actor=None)
  grade_advice(run, rob_by_row=None, mid=None, project_dir=None)
  sof(run, assumed_risks, certainty=None, footnotes=None, project_dir=None)
  amstar2_consistency(answers, convention='meets')

A számok a FUTÁS saját fájljaiból (plot_data.json / results.json — a motor kimenete) jönnek; a csonk csak
összerakja őket. ``patch()`` → a ``mock.patch.object(api, név, …, create=True)`` kezelők listája."""
import json
import os
from unittest import mock

from metaelemzes import api

GRADE_DIR = os.path.join("06_kezirat", "grade")
LEVELS = ("very low", "low", "moderate", "high")
DOMAINS = ("risk_of_bias", "inconsistency", "indirectness", "imprecision", "publication_bias")
CRITICAL = ("2", "4", "7", "9", "11", "13", "15")
CALLS = []


def _path(project_dir, oid):
    return os.path.join(project_dir, GRADE_DIR, "%s.grade.json" % oid)


def grade_get(project_dir, outcome_id):
    CALLS.append(("grade_get", outcome_id))
    p = _path(project_dir, outcome_id)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _certainty(doc):
    doms = doc.get("domains") or {}
    total = 0
    for d in DOMAINS:
        dom = doms.get(d) or {}
        if not dom.get("rating") or dom.get("step") is None:
            return None
        total += dom["step"]
    for u, on in (doc.get("upgrades") or {}).items():
        if on:
            total += int(((doc.get("upgrade_details") or {}).get(u) or {}).get("step") or 0)
    start = 4 if doc.get("start", "high") == "high" else 2
    return LEVELS[max(1, min(4, start + total)) - 1]


def grade_put(project_dir, outcome_id, doc, actor=None):
    CALLS.append(("grade_put", outcome_id, actor))
    if doc.get("start_reason") == "boom":
        raise ValueError("A motor elutasította: a kiindulás indoklása ellentmondásos.")
    out = dict(doc)
    out["certainty"] = _certainty(doc)
    out["consistency_warning"] = None
    out["validator_rollup"] = None
    p = _path(project_dir, outcome_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return out


def _load(run_dir, name):
    with open(os.path.join(run_dir, name), encoding="utf-8") as fh:
        return json.load(fh)


def _t(hu, en=None):
    return {"hu": hu, "en": en if en is not None else hu}


def grade_advice(run, rob_by_row=None, mid=None, project_dir=None):
    CALLS.append(("grade_advice", os.path.basename(run), mid))
    plot = _load(run, "plot_data.json")
    het = plot.get("heterogeneity") or {}
    prim = [s for s in plot.get("summaries") or [] if s.get("primary")][0]
    return {
        "schema": "szk.ma.grade-advice/v1", "measure": plot.get("measure"), "k": plot.get("k"),
        "start": {"suggested": "high", "reason": _t("randomizált vizsgálatok", "randomised trials")},
        "domains": {
            "risk_of_bias": {"suggested_rating": None, "summary": _t("nincs RoB-értékelés (csonk)", "no RoB (stub)"),
                             "evidence": [], "kb_refs": ["D-S13-003"], "concern": False,
                             "why": {"asks": _t("Mennyire megbízhatók a vizsgálatok?", "How trustworthy?")}},
            "inconsistency": {"suggested_rating": "serious",
                              "summary": {"hu": "I² " + het["i2_text"]["hu"], "en": "I² " + het["i2_text"]["en"]},
                              "evidence": [{"label": _t("PI"), "text": prim.get("pi_text")}],
                              "kb_refs": ["D-S09-007"], "concern": True,
                              "why": {"asks": _t("Egyeznek-e az eredmények?", "Are results consistent?")}},
            "indirectness": {"suggested_rating": None, "summary": _t("emberi ítélet", "human judgement"),
                             "evidence": [], "kb_refs": [], "concern": False},
            "imprecision": {"suggested_rating": "not serious", "summary": prim.get("display_text"),
                            "evidence": [{"label": _t("MID"), "text": _t(mid or "—")}], "kb_refs": [], "concern": False},
            "publication_bias": {"suggested_rating": "suspected", "status": "unresolved",
                                 "summary": _t("teszt-csonk: gyanított", "stub: suspected"), "evidence": [],
                                 "kb_refs": ["X019"], "concern": True},
        },
        "upgrades": {"large_effect": {"suggested": False, "summary": prim.get("display_text")},
                     "dose_response": {"suggested": False}, "opposing_confounding": {"suggested": False}},
        "run_dir": os.path.basename(run),
    }


def sof(run, assumed_risks, certainty=None, footnotes=None, project_dir=None):
    CALLS.append(("sof", os.path.basename(run), [r.get("source") for r in assumed_risks], certainty,
                  len(footnotes or [])))
    plot = _load(run, "plot_data.json")
    res = _load(run, "results.json")
    prim = [s for s in plot.get("summaries") or [] if s.get("primary")][0]
    ab = (res.get("totals") or {}).get("absolute_per_1000") or {}
    rows = []
    for r in assumed_risks:
        if r["source"] == "control_pool":
            base = ab.get("assumed_control_risk_per_1000")
            text = _t("%.0f / 1000" % base if base is not None else "—")
            label = _t("kontroll-pool", "control-group pool")
        else:
            text = _t("%s / 1000" % r.get("per_1000"))
            label = _t(r.get("label") or "külső", r.get("label") or "external")
        d = ab.get("difference") or [None, None, None]
        absolute = _t("%.0f [%.0f; %.0f] / 1000" % tuple(d)) if None not in d else _t("—")
        rows.append({"outcome": plot.get("title") or _t("o1"), "participants_text": _t("%s (%s)" % (
            res.get("totals", {}).get("participants"), plot.get("k"))), "relative_text": prim.get("display_text"),
            "assumed_risk": {"label": label, "source": r["source"], "text": text}, "absolute_text": absolute,
            "certainty": certainty, "certainty_text": _t(certainty or "—"),
            "footnotes": [chr(97 + i) for i in range(len(footnotes or []))],
            "sources": {"relative_text": "plot_data.json:summaries[primary].display_text",
                        "absolute_text": "results.json:totals.absolute_per_1000"}})
    rows.append({"outcome": _t("=HYPERLINK(\"x\")"), "relative_text": _t("-0.5"), "absolute_text": _t("+1 | 2"),
                 "participants_text": _t("@SUM(A1)"), "certainty_text": _t("\tx")})
    return {"schema": "szk.ma.sof/v1", "measure": plot.get("measure"), "rows": rows,
            "footnotes": [{"id": chr(97 + i), "text": _t(f.get("text") or "")} for i, f in enumerate(footnotes or [])],
            "statement": _t("A BCG valószínűleg csökkenti a TBC-t.", "BCG probably reduces TB."),
            "assumed_risks": assumed_risks}


def amstar2_consistency(answers, convention="meets"):
    CALLS.append(("amstar2_consistency", len(answers), convention))

    def rate(conv):
        crit = sum(1 for k, v in answers.items() if k in CRITICAL and (v == "no" or (v == "partial_yes" and
                                                                                      conv == "weakness")))
        weak = sum(1 for k, v in answers.items() if k not in CRITICAL and v == "no")
        if crit > 1:
            return "critically_low"
        if crit == 1:
            return "low"
        return "moderate" if weak > 1 else "high"
    by = {"meets": rate("meets"), "weakness": rate("weakness")}
    return {"rating": by[convention], "by_convention": by, "convention": convention}


FUNCS = {"grade_get": grade_get, "grade_put": grade_put, "grade_advice": grade_advice, "sof": sof,
         "amstar2_consistency": amstar2_consistency}


def patch(only=None):
    """[patcher] — a megadott (vagy minden) csonk-függvény a metaelemzes.api-ban."""
    return [mock.patch.object(api, name, fn, create=True) for name, fn in FUNCS.items()
            if only is None or name in only]


def absent(names):
    """[patcher] — a megadott homlokzat-függvények „hiányoznak” (None: a munkapad nem hívhatónak látja)."""
    out = []
    for n in names:
        out.append(mock.patch.object(api, n, None, create=True))
    return out
