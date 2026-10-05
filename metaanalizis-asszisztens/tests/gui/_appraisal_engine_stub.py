# -*- coding: utf-8 -*-
"""TESZT-CSONK a motor v1 értékelő homlokzatához (nem a motor!) — a munkapad értékelés-végpontjainak és a felület
fixture-einek tesztjéhez, amíg a ``metaelemzes.api`` v1-függvényei (a párhuzamos motor-munkafolyamat) el nem
készülnek. A függvénynevek és -alakok a ``ma_gui/routes/appraisal_common.ENGINE`` elvárásai:

  instruments_list() -> [összegzés]                      instrument_get(tool) -> szk.instrument/v1
  appraisal_check(doc, instrument=None) -> szk.appraisal-result/v1
  appraisal_consensus(doc_a, doc_b, instrument=None) -> egyezés (κ CI-vel, tételenkénti eltérések)
  rob_summary(appraisals, tool, outcome=None, plot=None, studies=None) -> szk.rob-summary/v1
  rob_sync_proposal(appraisals, header, rows, tool, row_uids=None, studies=None, column=None) -> javaslat

A szabályok egyszerűsítettek (a validator „konzervatív” logikájának tükre); a valódi algoritmus a motoré.
``patch(engine)`` a ``metaelemzes.api``-ba illeszti a függvényeket (``mock.patch.object(..., create=True)``)."""
import copy
import hashlib
import json
import math
from unittest import mock

LEVELS = {"low": "low", "some_concerns": "some", "moderate": "some", "unclear": "some", "high": "high",
          "serious": "high", "critical": "high", "very_high": "high", "no_information": "ni",
          "good": "low", "fair": "some", "poor": "high", "include": "low", "exclude": "high",
          "seek_further_info": "some", "critically_low": "high"}
NO_ISH = ("no", "probably_no")
YES_ISH = ("yes", "probably_yes", "partial_yes")
UNKNOWN = ("no_information", "unclear", "partly")
ANSWER_TEXT = {
    "yes": ("Y", "Igen", "Yes"), "probably_yes": ("PY", "Valószínűleg igen", "Probably yes"),
    "probably_no": ("PN", "Valószínűleg nem", "Probably no"), "no": ("N", "Nem", "No"),
    "no_information": ("NI", "Nincs információ", "No information"),
    "partial_yes": ("RI", "Részben igen", "Partial yes"), "unclear": ("?", "Nem egyértelmű", "Unclear"),
    "not_applicable": ("NA", "Nem alkalmazható", "Not applicable"), "partly": ("R", "Részben", "Partly"),
    "present": ("K", "Közölt", "Present"), "partial": ("RK", "Részben közölt", "Partial"),
    "missing": ("H", "Hiányzik", "Missing"),
}
VERDICT_TEXT = {
    "low": ("Alacsony", "Low"), "some_concerns": ("Némi aggály", "Some concerns"), "high": ("Magas", "High"),
    "moderate": ("Mérsékelt", "Moderate"), "serious": ("Súlyos", "Serious"), "critical": ("Kritikus", "Critical"),
    "no_information": ("Nincs információ", "No information"), "very_high": ("Nagyon magas", "Very high"),
    "unclear": ("Nem egyértelmű", "Unclear"), "include": ("Bevonható", "Include"), "exclude": ("Kizárandó", "Exclude"),
    "seek_further_info": ("További információ kell", "Seek further info"), "critically_low": ("Kritikusan alacsony",
                                                                                           "Critically low"),
    "good": ("Jó", "Good"), "fair": ("Közepes", "Fair"), "poor": ("Gyenge", "Poor"),
}
ALG_TEXT = {
    "conservative": ("KONZERVATÍV — amit a válaszok kikényszerítenek; nem a hivatalos folyamatábra",
                     "CONSERVATIVE — what the answers force; not the official flowchart"),
    "published": ("PUBLIKÁLT algoritmus", "PUBLISHED algorithm"),
    "count": ("csillagszámlálás (a küszöbök nem hivatalosak)", "star count (thresholds are not official)"),
    "none": ("HOLISZTIKUS — nincs algoritmus, emberi ítélet", "HOLISTIC — no algorithm, human judgement"),
}


def i18n(hu, en=None):
    return {"hu": hu, "en": en if en is not None else hu}


def answers_of(values):
    return [{"value": v, "label": ANSWER_TEXT[v][0], "text": i18n(ANSWER_TEXT[v][1], ANSWER_TEXT[v][2]),
             "aliases": [ANSWER_TEXT[v][0].lower()]} for v in values]


def verdicts_of(values):
    return [{"value": v, "label": i18n(*VERDICT_TEXT[v]), "level": LEVELS.get(v, "ni")} for v in values]


def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------- beépített mini-eszközök (tesztekhez)
def _item(iid, domain, text, tags=(), pass_=None, applies_to=None, scope=("all",), help_text=None, critical=False):
    key = "%s/%s" % (pass_, iid) if pass_ else iid
    return {"id": iid, "domain": domain, "pass": pass_, "key": key, "text": i18n(text, text), "tags": list(tags),
            "applies_to": applies_to, "scope": list(scope), "critical": critical,
            "help": i18n("Segítség: " + text, "Help: " + text) if help_text is None else help_text}


def builtin_instruments():
    rob2 = {
        "schema": "szk.instrument/v1", "key": "rob2", "name": "RoB 2 (teszt-csonk)", "unit": "result",
        "family": "rob", "scopes": ["assignment", "adherence"],
        "answers": answers_of(["yes", "probably_yes", "probably_no", "no", "no_information"]),
        "verdicts": verdicts_of(["low", "some_concerns", "high"]),
        "domains": [{"id": "1", "title": i18n("Randomizáció", "Randomisation process"), "passes": None,
                     "applicability": False},
                    {"id": "2", "title": i18n("Eltérés a tervezett beavatkozástól", "Deviations"), "passes": None,
                     "applicability": False}],
        "items": [_item("1.1", "1", "Was the allocation sequence random?"),
                  _item("1.2", "1", "Was the allocation sequence concealed?"),
                  _item("1.3", "1", "Baseline differences suggest a problem?", tags=["reverse"]),
                  _item("2.1", "2", "Participants aware of assignment?", tags=["router"], scope=["assignment"]),
                  _item("2.6", "2", "Appropriate analysis?", scope=["assignment"]),
                  _item("2.9", "2", "Adherence item?", scope=["adherence"])],
        "rollup": {"algorithm": "conservative", "note": i18n(*ALG_TEXT["conservative"])},
    }
    probast = {
        "schema": "szk.instrument/v1", "key": "probast-ai", "name": "PROBAST+AI (teszt-csonk)", "unit": "model",
        "family": "prediction",
        "passes": [{"id": "development", "label": i18n("Fejlesztés (minőség)", "Development (quality)")},
                   {"id": "evaluation", "label": i18n("Értékelés (torzítás)", "Evaluation (risk of bias)")}],
        "answers": answers_of(["yes", "probably_yes", "probably_no", "no", "no_information"]),
        "verdicts": verdicts_of(["low", "high", "unclear"]),
        "domains": [{"id": "1", "title": i18n("Résztvevők", "Participants"), "passes": ["development", "evaluation"],
                     "applicability": True},
                    {"id": "4", "title": i18n("Elemzés", "Analysis"), "passes": ["development", "evaluation"],
                     "applicability": False}],
        "items": [_item("1.1", "1", "Appropriate data sources?", pass_="development"),
                  _item("4.1", "4", "Sample size?", pass_="development"),
                  _item("1.1", "1", "Appropriate data sources?", pass_="evaluation"),
                  _item("4.1", "4", "Sample size?", pass_="evaluation"),
                  _item("4.2", "4", "Calibration?", pass_="evaluation")],
        "rollup": {"algorithm": "none", "note": i18n(*ALG_TEXT["none"])},
    }
    amstar = {
        "schema": "szk.instrument/v1", "key": "amstar2", "name": "AMSTAR 2 (teszt-csonk)", "unit": "review",
        "family": "review", "answers": answers_of(["yes", "partial_yes", "no"]),
        "verdicts": [dict(v, level={"high": "low", "moderate": "some"}.get(v["value"], "high"))
                     for v in verdicts_of(["high", "moderate", "low", "critically_low"])],
        "domains": [{"id": "1", "title": i18n("Tételek", "Items"), "passes": None, "applicability": False}],
        "items": [_item("1", "1", "PICO?"), _item("2", "1", "Protocol?", critical=True, tags=["critical"]),
                  _item("3", "1", "Design selection explained?"), _item("4", "1", "Search?", critical=True,
                                                                        tags=["critical"])],
        "rollup": {"algorithm": "published", "note": i18n(*ALG_TEXT["published"])},
        "conventions": {"partial_yes_critical": {"default": "meets", "values": ["meets", "weakness"],
                                                 "kb": "AMSTAR2-00"}},
    }
    tripod = {
        "schema": "szk.instrument/v1", "key": "tripod-ai", "name": "TRIPOD+AI (teszt-csonk)", "unit": "study",
        "family": "reporting", "scopes": ["both", "development", "evaluation"],
        "answers": answers_of(["present", "partial", "missing", "not_applicable"]),
        "status_vocab": ["present", "partial", "missing", "not_applicable"],
        "verdicts": [], "domains": [{"id": "T", "title": i18n("Tételek", "Items"), "passes": None,
                                     "applicability": False}],
        "items": [_item("1", "T", "Title", applies_to="D;E"), _item("3a", "T", "Background", applies_to="D;E"),
                  _item("11", "T", "How missing data were handled", applies_to="D"),
                  _item("12", "T", "Evaluation item", applies_to="E")],
        "rollup": {"algorithm": "none", "note": i18n(*ALG_TEXT["none"])},
    }
    out = {}
    for inst in (rob2, probast, amstar, tripod):
        inst["reference_sha256"] = sha(inst["items"])
        inst["counts"] = {"parsed": len(inst["items"]), "published": len(inst["items"])}
        out[inst["key"]] = inst
    return out


# ---------------------------------------------------------------------------- a csonk
class StubEngine(object):
    """A motor v1 értékelő függvényeinek CSONKJA (lásd a modul leírását)."""

    def __init__(self, instruments=None):
        self.insts = instruments if instruments is not None else builtin_instruments()
        self.calls = []

    # -- eszközök
    def instruments_list(self):
        self.calls.append("instruments_list")
        out = []
        for k, inst in sorted(self.insts.items()):
            out.append({"key": k, "name": inst.get("name"), "unit": inst.get("unit"), "family": inst.get("family"),
                        "rollup": inst.get("rollup"), "counts": inst.get("counts"),
                        "passes": [p["id"] for p in inst.get("passes") or []], "scopes": inst.get("scopes") or [],
                        "reference_sha256": inst.get("reference_sha256")})
        return out

    def instrument_get(self, tool):
        self.calls.append("instrument_get")
        if tool not in self.insts:
            raise KeyError(tool)
        return copy.deepcopy(self.insts[tool])

    # -- segédek
    def _scoped(self, inst, doc):
        scope = doc.get("scope")
        items = []
        passes = [p["id"] for p in inst.get("passes") or []]
        for it in inst["items"]:
            if passes:
                if scope in ("development", "evaluation") and it["pass"] != scope:
                    continue
            elif it.get("applies_to"):
                if scope in ("development", "evaluation") and scope[0].upper() not in it["applies_to"]:
                    continue
            elif scope and "all" not in it.get("scope", ["all"]) and scope not in it.get("scope", []):
                continue
            items.append(it)
        return items

    @staticmethod
    def _val(doc, key):
        a = (doc.get("answers") or {}).get(key)
        return a.get("value") if isinstance(a, dict) else None

    def _verdict(self, inst, level):
        for v in inst.get("verdicts") or []:
            if v.get("level") == level:
                return v["value"]
        return None

    # -- ellenőrzés
    def appraisal_check(self, doc, instrument=None):
        self.calls.append("appraisal_check")
        inst = instrument or self.insts[doc["tool"]]
        allowed = {a["value"] for a in inst["answers"]}
        items = self._scoped(inst, doc)
        missing, invalid, answered = [], [], 0
        for it in items:
            v = self._val(doc, it["key"])
            if v is None:
                missing.append({"item": it["id"], "pass": it["pass"], "key": it["key"]})
            elif v not in allowed:
                invalid.append({"item": it["id"], "pass": it["pass"], "key": it["key"]})
            else:
                answered += 1
        alg = (inst.get("rollup") or {}).get("algorithm", "none")
        res = {"schema": "szk.appraisal-result/v1", "tool": inst["key"], "engine_version": "stub", "legacy": False,
               "complete": not missing and not invalid, "expected": len(items), "answered": answered,
               "completeness_text": "%d/%d" % (answered, len(items)), "missing": missing, "invalid": invalid,
               "domains": [], "overall": {"implied": None, "algorithm": alg, "text": i18n(*ALG_TEXT[alg])},
               "per_pass": None, "amstar2": None, "nos": None, "tripod": None, "guards": [],
               "notes": [i18n(*ALG_TEXT[alg])], "warnings": []}
        passes = [p["id"] for p in inst.get("passes") or []]
        if passes:
            res["per_pass"] = {}
            for p in passes:
                its = [it for it in items if it["pass"] == p]
                ans = sum(1 for it in its if self._val(doc, it["key"]) in allowed)
                res["per_pass"][p] = {"expected": len(its), "answered": ans, "complete": ans == len(its),
                                      "text": "%d/%d" % (ans, len(its))}
        if alg == "conservative":
            worst = "low"
            any_none = False
            for d in inst["domains"]:
                its = [it for it in items if it["domain"] == d["id"]]
                if not its:
                    continue
                forced, unk, routers, miss = [], [], [], []
                for it in its:
                    v = self._val(doc, it["key"])
                    if "router" in it["tags"]:
                        if v is not None:
                            routers.append(it["id"])
                        continue
                    if v is None:
                        miss.append(it["id"])
                        continue
                    bad = v in YES_ISH if "reverse" in it["tags"] else v in NO_ISH
                    if bad:
                        forced.append(it["id"])
                    elif v in UNKNOWN:
                        unk.append(it["id"])
                if forced:
                    level, why = "high", ("'Nem' / 'Valószínűleg nem' itt: %s" % ", ".join(forced),
                                          "'No' or 'Probably no' at %s" % ", ".join(forced))
                elif miss:
                    level, why = None, ("hiányzó válasz: %s" % ", ".join(miss), "unanswered: %s" % ", ".join(miss))
                elif unk:
                    level, why = "some", ("nincs információ: %s" % ", ".join(unk), "no information at %s" % ", ".join(unk))
                else:
                    level, why = "low", ("egyik jelző-kérdés sem jelez problémát", "no signalling question flags a problem")
                implied = self._verdict(inst, level) if level else None
                res["domains"].append({"domain": d["id"], "pass": None, "implied": implied, "algorithm": alg,
                                       "forced_by": forced, "unknown_at": unk, "routers": routers, "missing": miss,
                                       "text": i18n(*why)})
                if level is None:
                    any_none = True
                elif level == "high" or (level == "some" and worst == "low"):
                    worst = level
            if worst == "high" or not any_none:
                res["overall"]["implied"] = self._verdict(inst, worst)
        if inst["key"] == "amstar2":
            res["amstar2"] = self.amstar2(inst, doc)
            res["overall"]["implied"] = res["amstar2"]["rating"]
        if inst["key"] == "tripod-ai":
            counts = {}
            for it in items:
                v = self._val(doc, it["key"])
                counts[v or "unanswered"] = counts.get(v or "unanswered", 0) + 1
            res["tripod"] = {"scope": doc.get("scope") or "both", "counts": counts,
                             "missing_items": [{"item": it["id"], "applies_to": it.get("applies_to")}
                                               for it in items if self._val(doc, it["key"]) in ("missing", "partial")],
                             "note": i18n("A TRIPOD+AI a jelentés teljességét méri, nem a módszertan helyességét.",
                                          "TRIPOD+AI measures reporting completeness, not methodological soundness.")}
        return res

    def amstar2(self, inst, doc):
        crit = {it["id"] for it in inst["items"] if it.get("critical")}
        out = {}
        for conv in ("meets", "weakness"):
            flaws, weak, unans = [], [], []
            for it in inst["items"]:
                v = self._val(doc, it["key"])
                if v is None:
                    unans.append(it["id"])
                elif v == "yes":
                    continue
                elif v == "partial_yes":
                    if it["id"] in crit and conv == "weakness":
                        weak.append(it["id"])
                elif it["id"] in crit:
                    flaws.append(it["id"])
                else:
                    weak.append(it["id"])
            rating = ("critically_low" if len(flaws) > 1 else "low" if flaws else "moderate" if len(weak) > 1
                      else "high")
            out[conv] = {"convention": conv, "rating": rating, "rating_text": i18n(*VERDICT_TEXT[rating]),
                         "critical_flaws": flaws, "weaknesses": weak, "unanswered": unans,
                         "provisional": bool(unans)}
        main = dict(out["meets"])
        main["alternative"] = out["weakness"]
        main["differs"] = out["meets"]["rating"] != out["weakness"]["rating"]
        main["kb_ref"] = "AMSTAR2-00"
        return main

    # -- egyezés
    def appraisal_consensus(self, a, b, instrument=None):
        self.calls.append("appraisal_consensus")
        for d in (a, b):
            if d.get("origin") == "ai_draft":
                raise ValueError("AI-vázlat nem értékelő")
        keys = sorted(set(a.get("answers") or {}) | set(b.get("answers") or {}))
        pairs, items = [], []
        for k in keys:
            va, vb = self._val(a, k), self._val(b, k)
            items.append({"key": k, "a": va, "b": vb, "agree": va == vb and va is not None})
            if va is not None and vb is not None:
                pairs.append((va, vb))
        n = len(pairs)
        po = sum(1 for x, y in pairs if x == y) / n if n else None
        cats = sorted({c for p in pairs for c in p})
        pe = sum((sum(1 for x, _ in pairs if x == c) / n) * (sum(1 for _, y in pairs if y == c) / n) for c in cats) if n else None
        kappa = (po - pe) / (1 - pe) if n and pe is not None and pe < 1 else None
        ci = None
        if kappa is not None:
            se = math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2))
            ci = [kappa - 1.959964 * se, kappa + 1.959964 * se]
        txt = "κ = %.2f [%.2f; %.2f]" % (kappa, ci[0], ci[1]) if kappa is not None else "κ = —"
        doms = []
        for da in a.get("domain_judgements") or []:
            db = next((x for x in b.get("domain_judgements") or [] if x.get("domain") == da.get("domain")
                       and x.get("pass") == da.get("pass")), {})
            doms.append({"domain": da.get("domain"), "pass": da.get("pass"), "a": da.get("judgement"),
                         "b": db.get("judgement"), "agree": da.get("judgement") == db.get("judgement")})
        oa, ob = (a.get("overall") or {}).get("judgement"), (b.get("overall") or {}).get("judgement")
        return {"schema": "szk.ma.appraisal-agreement/v1", "tool": a.get("tool"), "a": a.get("assessor"),
                "b": b.get("assessor"), "items_compared": n, "agree": sum(1 for x, y in pairs if x == y),
                "disagree": sum(1 for x, y in pairs if x != y),
                "agreement_pct_text": ("%.1f%%" % (100 * po)) if po is not None else "—",
                "kappa": kappa, "kappa_ci": ci, "kappa_text": i18n(txt, txt.replace("-", "−")),
                "items": items, "domains": doms, "overall": {"a": oa, "b": ob, "agree": oa == ob}, "excluded": []}

    # -- forgalmi lámpa és szinkron
    def _finals(self, appraisals):
        groups = {}
        for d in appraisals:
            u = (d.get("target") or {}).get("study_id") or (d.get("target") or {}).get("unit")
            groups.setdefault(u, []).append(d)
        out = {}
        for u, docs in groups.items():
            cons = [d for d in docs if d.get("status") == "consensus"]
            done = [d for d in docs if d.get("status") == "complete" and d.get("origin") != "ai_draft"]
            appr = [d for d in docs if d.get("origin") == "ai_draft" and d.get("approved_by")]
            pick = (cons or done or appr or [None])[0]
            if pick is not None:
                out[u] = (pick, "consensus" if cons else ("single" if done else "ai_approved"))
        return out

    def rob_summary(self, appraisals, tool, outcome=None, plot=None, studies=None):
        self.calls.append("rob_summary")
        inst = self.insts[tool]
        labels = {s["study_id"]: s.get("label") for s in (studies or {}).get("studies") or []}
        weights = {}
        for s in (plot or {}).get("studies") or []:
            weights[s.get("label")] = (s.get("weight_pct", s.get("weight")), s.get("weight_text"))
        rows = []
        for u, (doc, src) in sorted(self._finals(appraisals).items()):
            lab = labels.get(u) or u
            w, wt = weights.get(lab, (None, None))
            rows.append({"study_id": u, "label": lab, "weight_pct": w,
                         "weight_text": wt if wt is not None else (i18n("%.1f%%" % w) if w is not None else None),
                         "judgements": {d.get("domain"): d.get("judgement") for d in doc.get("domain_judgements") or []},
                         "overall": (doc.get("overall") or {}).get("judgement"), "source": src})
        weighted = []
        tot = sum(r["weight_pct"] or 0 for r in rows)
        for lev in ("low", "some", "high"):
            vs = [r for r in rows if LEVELS.get(r["overall"]) == lev]
            pct = sum(r["weight_pct"] or 0 for r in vs) / tot * 100 if tot else None
            weighted.append({"level": lev, "n": len(vs), "pct": pct, "text": i18n("%.1f%%" % pct) if pct is not None else None})
        return {"schema": "szk.rob-summary/v1", "tool": tool, "outcome": outcome,
                "domains": [{"id": d["id"], "title": d["title"]} for d in inst["domains"]], "studies": rows,
                "scale": inst["verdicts"], "weighted": weighted}

    def rob_sync_proposal(self, appraisals, header, rows, tool, row_uids=None, studies=None, column=None):
        self.calls.append("rob_sync_proposal")
        finals = self._finals(appraisals)
        by_label = {}
        for s in (studies or {}).get("studies") or []:
            by_label[(s.get("label") or "").lower()] = s["study_id"]
        from metaelemzes import api
        cmap = api.column_map(list(header))
        si = header.index(cmap["study"]) if cmap.get("study") in header else 0
        ri = header.index(cmap["rob"]) if cmap.get("rob") in header else None
        changes, unchanged = [], 0
        for i, cells in enumerate(rows):
            lab = cells[si] if si < len(cells) else ""
            u = by_label.get(lab.lower(), lab)
            if u not in finals:
                continue
            doc, src = finals[u]
            j = (doc.get("overall") or {}).get("judgement")
            if not j:
                continue
            after = VERDICT_TEXT[j][1].lower()
            before = cells[ri] if ri is not None and ri < len(cells) else ""
            if before == after:
                unchanged += 1
                continue
            changes.append({"row_uid": (row_uids or [None] * len(rows))[i], "row": i, "study_id": u, "label": lab,
                            "before": before, "after": after, "judgement": j, "source": src})
        return {"schema": "szk.ma.rob-sync/v1", "column": column or "rob", "changes": changes,
                "unchanged": unchanged, "unmatched": [], "warnings": []}


NAMES = ("instruments_list", "instrument_get", "appraisal_check", "appraisal_consensus", "rob_summary",
         "rob_sync_proposal")


def patch(engine, names=NAMES):
    """[mock.patch.object(api, név, …, create=True)] — a hívó start()/stop()-ja (vagy ExitStack)."""
    from metaelemzes import api
    return [mock.patch.object(api, n, getattr(engine, n), create=True) for n in names]
