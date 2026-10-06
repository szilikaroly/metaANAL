# -*- coding: utf-8 -*-
"""„Mindent elrontó” projekt a v1 elfogadási teszthez (tests/gui/ui/e2e_v1.spec.js, 8. lépés): a 22 X-szabály
(X001–X022) MIND előjön egyszerre, a valódi motor commit-futásaival (BCG, RR). A hibákat a tests/test_v1_audit.py és
a tests/test_mvp_audit.py szabályonkénti forgatókönyveiből raktuk össze; a döntést (találat vagy sem) a motor
``project audit``-ja hozza — a felület ezt mutatja.

    python3 tests/gui/ui/e2e_v1_xrules.py <mappa>    → a projekt + a motor auditjának X-kódjai (önellenőrzés)

Kimenetek:
  o1  BCG (RR), eszköz: RoB 2 — X001 (a tábla a futás óta változott), X003 (rob ≠ értékelés), X004 (hiányzó
      értékelés), X005 (becsült sor, nincs érzékenységi futás), X006 (magas RoB, nincs érzékenységi futás), X010
      (nincs forrásoldal), X013 (becsült-jelölés ↔ eredet), X014 (több elemzett vizsgálat, mint bevont), X015
      (vizsgálatszám ↔ studies.json), X016 (nem előre rögzített elsődleges elemzés), X017 (indoklás nélküli
      felülbírálás), X022 (az eredet-fájl nem ehhez a táblához tartozik), X007 (rögzített GRADE más számokkal),
      X008 (SoF-cella ≠ motor), X002 (elavult ábra), X018 (az ábra QC-je nem tiszta), X009 (kettős kinyerés
      lezáratlan eltéréssel);
  o2  ugyanaz a BCG-tábla másolata — X019 (GRADE-piszkozat feloldatlan „suspected” publikációs torzítással);
  projekt: X011 (predikciósmodell-áttekintés PROBAST+AI nélkül), X012 (AMSTAR 2 ↔ besorolás), X020 (elbírálatlan
      rekord), X021 (kizárási okok ↔ döntési napló)."""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
TESTS = os.path.join(ROOT, "tests")
for p in (ROOT, TESTS):
    if p not in sys.path:
        sys.path.insert(0, p)

ALL = ["X%03d" % i for i in range(1, 23)]


def make_project(base):
    import test_v1_audit as T
    pj = T.Proj(base, name="projekt")
    home = os.path.join(base, "home")
    os.makedirs(home, exist_ok=True)
    o1 = {"id": "o1", "name": {"hu": "Tbc", "en": "TB"}, "data": "03_adatok/o1.csv", "measure": "RR",
          "primary_spec": "05_elemzes/specs/o1_primary.json", "appraisal_tool": "rob2"}
    o2 = {"id": "o2", "name": {"hu": "Tbc (másolat)", "en": "TB (copy)"}, "data": "03_adatok/o2.csv",
          "measure": "RR", "primary_spec": "05_elemzes/specs/o2_primary.json", "appraisal_tool": "rob2"}
    pj.meta(outcomes=[o1, o2], review_type="prediction_model", appraisal_tools=["rob2", "probast-ai", "amstar2"])
    rows = T.bcg_rows()
    rows[0]["rob"] = "high"                                    # X006 (magas RoB, gyermek-futás nincs)
    rows[1]["estimated"] = "igen"                              # X005 (+ X013: az eredet nem becsült)
    pj.csv(rows)
    pj.csv(T.bcg_rows(), rel="03_adatok/o2.csv")
    pj.studies(n=12, outcomes=("o1", "o2"))                    # X014: 13 elemzett vizsgálat, 12 bevont; X015
    # elsődleges spec, nem előre rögzített (X016), valódi commit-futás
    rel, doc = pj.spec()
    doc = dict(doc, prespecified=False, protocol_ref=None)
    pj.write(rel, doc)
    pj.spec_doc = doc
    d1 = pj.engine()
    rel2, doc2 = pj.spec(name="o2_primary", outcome="o2", data="03_adatok/o2.csv")
    d2 = pj.engine(spec_rel=rel2, doc=doc2, minutes=1)
    pj.spec_rel, pj.spec_doc = rel, doc
    # eredet: a 2. sor becsültként jelölt, de a cellák „reported” (X013), oldal nélkül (X010)
    cells = []
    for i, r in enumerate(rows):
        for f in ("e1", "n1", "e2", "n2"):
            cells.append({"row_uid": r["row_uid"], "field": f, "value_as_entered": str(r[f]), "method": "reported",
                          "estimated": False, "source": {"doc": "pmid:%d" % (100 + i), "page": None,
                                                         "locator": None}})
    pj.write("03_adatok/o1.prov.json", {"schema": "szk.ma.provenance/v1", "table": "03_adatok/o1.csv",
                                        "table_sha256": pj.sha("03_adatok/o1.csv"), "cells": cells})
    # értékelések: S01 alacsony (a tábla magasat ír → X003), S02 felülbírálás indoklás nélkül (X017), S03–S10
    # rendben, S11–S13 hiányzik (X004); PROBAST+AI egy vizsgálatra sincs (X011)
    pj.appraisal("S01", judgement="low")
    _x017(pj, T)
    for i in range(2, 10):
        pj.appraisal(T.sid(i), judgement="low")
    # AMSTAR 2: minden „igen”, de a megadott besorolás „critically low” (X012)
    pj.appraisal("review", tool="amstar2", status="complete", outcome=None,
                 answers={str(k): {"value": "yes"} for k in range(1, 17)},
                 overall={"judgement": "critically_low", "rationale": "önellenőrzés"})
    # GRADE: o1 rögzítve, de más számokkal (X007); o2 piszkozat feloldatlan „suspected”-del (X019)
    g = _grade_doc(d1, "o1", status="recorded", run_summary={"k": 11, "participants": 1000, "measure": "RR",
                                                            "display_text": {"hu": "0.52 [0.35; 0.77]",
                                                                             "en": "0.52 [0.35; 0.77]"}})
    pj.write("06_kezirat/grade/o1.grade.json", g)
    g2 = _grade_doc(d2, "o2", status="draft", pb={"rating": "suspected", "step": None, "status": "unresolved",
                                                   "rationale": None})
    pj.write("06_kezirat/grade/o2.grade.json", g2)
    # SoF: a relatív hatás szövege nem a motoré (X008)
    n = T.run_numbers(d1)
    est, lo, hi = n["bt"]
    pj.write("06_kezirat/sof/o1.sof.json", {"schema": "szk.ma.sof/v1", "outcome_id": "o1", "run_id": d1["run_id"],
                                            "measure": "RR", "rows": [{
                                                "outcome_id": "o1", "label": {"hu": "Tbc", "en": "TB"}, "k": 13,
                                                "participants": n["participants"],
                                                "studies_text": {"hu": "x", "en": "x"},
                                                "relative": {"measure": "RR", "estimate": est, "ci_lower": lo,
                                                             "ci_upper": hi, "level": 0.95,
                                                             "display_text": {"hu": "0.48 [0.33; 0.73]",
                                                                              "en": "0.48 [0.33; 0.73]"},
                                                             "text": {"hu": "RR", "en": "RR"}},
                                                "effect": None, "absolute": [], "certainty": None, "footnotes": [],
                                                "sources": {}}]})
    # ábrák: egy elavult (más plot_data-ból, X002) és egy QC-hibás (X018)
    _figure(pj, "fig_stale", plot_sha="ab" * 32, run_id=None)
    _figure(pj, "fig_qc", plot_sha=pj.sha(d1["_dir"] + "/plot_data.json"), run_id=d1["run_id"], clean=False,
            residual_violations=[{"label": "Aronson 1948", "kind": "overlap"}])
    # kettős kinyerés: egy feloldatlan eltérés (X009)
    a = T.bcg_rows()
    pj.csv(a, "03_adatok/kettos/o1.A.csv")
    b = copy.deepcopy(a)
    b[2]["e2"] = 12
    pj.csv(b, "03_adatok/kettos/o1.B.csv")
    # PRISMA: elbírálatlan rekordok (X020) és a döntési napló kizárási okai eltérnek (X021)
    flow = {"identified_databases": 120, "dedup_removed": 20, "screened": 100, "excluded_screening": 80,
            "sought_for_retrieval": 20, "not_retrieved": 2, "assessed_eligibility": 18, "excluded_eligibility": 5,
            "excluded_eligibility_reasons": {"wrong population": 3, "Wrong design": 2}, "included": 13,
            "included_studies": 13, "undecided": 3}
    pj.write("02_szures/prisma_flow.json", flow)
    log = [["r%d" % i, "exclude", "wrong population", "eligibility"] for i in range(2)] + \
          [["r%d" % i, "exclude", "wrong design", "eligibility"] for i in range(2, 5)] + \
          [["r%d" % i, "include", "", "eligibility"] for i in range(5, 18)]
    pj.write("02_szures/dontesek.csv", "rec_id;decision;reason;phase\n" + "".join(";".join(r) + "\n" for r in log))
    # végül a tábla változik a futás után: X001 (elavult futás) és X022 (az eredet-fájl egy korábbi tábláé)
    rows[4]["n1"] = 5070
    pj.csv(rows)
    return pj.root, home, {}


def _x017(pj, T):
    from metaelemzes import instruments as I
    inst = I.load("rob2")
    ans = {}
    for it in inst.slots("assignment"):
        allowed = inst.allowed(it)
        v = "no" if it.get("polarity") == "reverse" else ("yes" if "yes" in allowed else allowed[0])
        ans[it["key"]] = {"value": v}
    ans.update({"2.3": {"value": "no"}, "4.3": {"value": "no"}, "2.6": {"value": "no"},
                "2.7": {"value": "probably_yes"}})
    dj = [{"domain": str(i), "pass": None, "judgement": "low"} for i in range(1, 6)]
    rel = pj.appraisal("S02", answers={}, domain_judgements=dj, overall={"judgement": "high", "rationale": "x"})
    with open(pj.p(rel), encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["answers"] = ans
    doc["scope"] = "assignment"
    pj.write(rel, doc)


def _grade_doc(d, outcome, status, run_summary=None, pb=None):
    doc = {"schema": "szk.ma.grade/v1", "outcome_id": outcome, "importance": "critical", "run_id": d["run_id"],
           "start": "high", "start_reason": "RCT",
           "domains": {k: {"rating": "not serious", "step": 0, "rationale": "ok"} for k in
                       ("risk_of_bias", "inconsistency", "indirectness", "imprecision")},
           "upgrades": {"large_effect": False, "dose_response": False, "opposing_confounding": False},
           "certainty": "high", "status": status}
    doc["domains"]["publication_bias"] = pb or {"rating": "undetected", "step": 0, "status": "resolved",
                                                "rationale": "a tesztek nem jeleznek"}
    if run_summary is not None:
        doc["run_summary"] = run_summary
    return doc


def _figure(pj, stem, plot_sha=None, run_id=None, **kw):
    doc = {"schema": "szk.figure-result/v1", "kind": "forest", "stem": stem, "ff_version": "0.3.0",
           "formats": {"svg": "06_kezirat/abrak/%s.svg" % stem}, "dpi": 600, "clean": True, "labels_checked": 64,
           "residual_violations": [], "glyphs": {"ok": True, "missing": [], "font": "Arial"},
           "numbers": {"ok": True, "checked": 31, "mismatches": []},
           "source": {"plot_sha256": plot_sha, "run_id": run_id}}
    doc.update(kw)
    pj.write("06_kezirat/abrak/%s.svg" % stem, "<svg/>")
    pj.write("06_kezirat/abrak/%s.result.json" % stem, doc)


if __name__ == "__main__":
    import tempfile
    from metaelemzes import api
    base = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="xrules_")
    proj, _h, _x = make_project(base)
    rep = api.project_audit(proj, stage="FINAL")
    got = sorted({f["code"] for f in rep["findings"]})
    print(json.dumps({"proj": proj, "codes": got, "missing": [c for c in ALL if c not in got],
                      "not_checked": sorted({n["code"] for n in rep["not_checked"]})}, ensure_ascii=False))
