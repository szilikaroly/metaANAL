# -*- coding: utf-8 -*-
"""A teljes elemzési folyamat: validálás → hatásméretek → modellek → heterogenitás →
alcsoport / meta-regresszió → torzítás → érzékenység → ábrák → riport.

A CLI (cli.py) és az ágensek ezt hívják; minden eredmény JSON-ba írható.
"""
import json
import math
import os

from . import __version__
from . import effect_sizes as E
from . import models as M
from . import moderators as MO
from . import bias as B
from . import sensitivity as SE
from . import validate as V
from . import plots as P
from . import tableio
from .tableio import NumText

DEFAULTS = {
    # tau2: None = a modell alapértelmezése (random → REML, ivhet → DL; az érzékenységi és
    # torzítás-elemzések is a modellét kapják); kifejezett érték minden modellre vonatkozik
    "measure": "SMD", "model": "random", "tau2": None, "ci": None, "pi": "t_k-2",
    "level": 0.95, "smd_vtype": "LS", "j_method": "exact", "cc": 0.5, "cc_to": "only0",
    "drop00": None, "mh": False, "peto": False, "rd_var": "sato", "subgroup": None,
    "common_tau2": False, "moderators": [], "metareg_test": "knha", "cumulative": None,
    "title": None, "left_label": None, "right_label": None, "label_col": "study",
    "bias_min_k": 10, "trimfill_estimator": "L0",
    # konvenció-opciók (az alapértékek a korábbi viselkedést adják)
    "md_vtype": "unequal", "glass_vtype": "METAN", "gen_smd_vtype": None,
    "pft_backtransform": "harmonic", "h_centre": "truncated", "trimfill_trim_model": None,
    "egger_ci_dist": "t", "begg_method": "auto", "begg_continuity": False,
    "metareg_robust": False, "outliers": False,
    # metareg_tau2: a meta-regresszió τ²-becslője (None = a --tau2, ha meta-regresszióban értelmezett,
    # különben REML); 'FE' = inverz-variancia súlyok (τ² = 0; a --robust így = Stata regress [aw=1/v])
    "metareg_tau2": None,
    # az alcsoport-elemzés a protokollban előre tervezett volt (csak ekkor írja a Methods 'Pre-specified'-et)
    "subgroup_prespecified": False,
}

# az enumerált opciók megengedett értékei (a CLI choices-szal azonos; programból hívva is ellenőrizzük)
OPTION_CHOICES = {
    "model": ("random", "fixed", "ivhet"),
    "smd_vtype": E.SMD_VTYPES,
    "glass_vtype": E.GLASS_VTYPES,
    "pft_backtransform": E.PFT_N_METHODS,
    "h_centre": M.H_CENTRES,
    "trimfill_trim_model": (None, "fixed", "random"),
    "trimfill_estimator": ("L0", "R0"),
    "egger_ci_dist": B.EGGER_CI_DISTS,
    "begg_method": B.BEGG_METHODS,
    "gen_smd_vtype": (None,) + tuple(E.SMD_VTYPES),
}


def check_options(opt):
    """Az enumerált opciók ellenőrzése → ValueError az opció nevével és a lehetséges értékekkel
    (még mielőtt bármi kimenet készülne)."""
    for key, allowed in OPTION_CHOICES.items():
        if opt.get(key) not in allowed:
            raise ValueError("%s: érvénytelen érték %r (lehetséges: %s)" % (
                key, opt.get(key), ", ".join("–" if a is None else str(a) for a in allowed)))
    try:
        E.md_vtype_name(opt.get("md_vtype"))
    except E.EffectSizeError as exc:
        raise ValueError("md_vtype: %s" % exc)
    if opt.get("tau2") is not None:
        t = str(opt["tau2"]).upper()
        if t not in M.TAU2_METHODS:
            raise ValueError("tau2: érvénytelen becslő %r (lehetséges: %s)" % (opt["tau2"], ", ".join(M.TAU2_METHODS)))
        opt["tau2"] = t
    if opt.get("metareg_tau2") is not None:
        t = str(opt["metareg_tau2"]).upper()
        if t not in MO.MR_TAU2_METHODS:
            raise ValueError("metareg_tau2: érvénytelen becslő %r (lehetséges: %s)"
                             % (opt["metareg_tau2"], ", ".join(MO.MR_TAU2_METHODS)))
        opt["metareg_tau2"] = t


def to_jsonable(obj):
    if isinstance(obj, M.MetaResult):
        return {k: to_jsonable(v) for k, v in obj.to_dict().items()}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def _display(measure, est, lo, hi, nh=None, pft_n="harmonic", se=None):
    """Becslés és CI az értelmezési skálán. PFT-nél pft_n='variance' (MetaXL / Barendregt 2013):
    m = 1/(4·se²) az adott eredmény saját SE-jéből; ha az SE nem ismert, a harmonikus átlag n."""
    if measure == "PFT" and pft_n == "variance" and not (se is not None and se > 0 and math.isfinite(se)):
        pft_n = "harmonic"
    if measure != "PFT":
        pft_n = "harmonic"
    return [E.back_transform(measure, v, nh, pft_n, se) if v is not None else None for v in (est, lo, hi)]


def _row_se(row, ci_method, level, single_fixed=False):
    """Egy sorrendi (kumulatív) sor SE-je a szimmetrikus CI-ből: (felső − alsó)/(2·krit), ahol krit
    z (z-CI, IVhet, az első, egyvizsgálatos sor), különben t(k−1) (t / HKSJ)."""
    from .distributions import norm_ppf, t_ppf
    lo, hi, kk = row.get("ci_lower"), row.get("ci_upper"), row.get("k") or 1
    if lo is None or hi is None:
        return None
    if single_fixed or kk < 2 or ci_method in (None, "z"):
        crit = norm_ppf(0.5 + level / 2.0)
    else:
        crit = t_ppf(0.5 + level / 2.0, kk - 1)
    return (hi - lo) / (2.0 * crit)


def _level_str(v):
    """Alcsoport-szint szövegként: a számként beolvasott egész érték egészként ('2', nem '2.0');
    a tableio.NumText az eredeti szövegét adja ('01' és '1' két külön szint marad)."""
    if isinstance(v, NumText):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip() if isinstance(v, str) else str(v)


def _mod_levels(column):
    """Egy moderátor-oszlop különböző értékei a design_matrix logikája szerint: csupa számból álló
    oszlopban számként ('01' = '1' = 1.0), különben szövegként."""
    if all(isinstance(x, (int, float)) for x in column):
        return set(float(v) for v in column)
    return set(_level_str(v) for v in column)


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and math.isnan(v))


def _double_zero(r):
    try:
        e1, n1, e2, n2 = (float(r[c]) for c in ("e1", "n1", "e2", "n2"))
    except (KeyError, TypeError, ValueError):
        return False
    return (e1 == 0 and e2 == 0) or (e1 == n1 and e2 == n2)


def normalize_level(v):
    """Megbízhatósági szint: 0 < level < 1; a metafor-konvenció szerint 1 < x < 100 százalék (95 → 0.95).
    Számként írt szöveg ('0.9', '0,9') is jó. Érvénytelen érték → ValueError (a CLI --level szabálya,
    hogy a programból hívott pipeline se adjon csendben 0 szélességű CI-t vagy mély norm_ppf-hibát)."""
    try:
        x = float(str(v).strip().replace(",", "."))
    except ValueError:
        raise ValueError("level: érvénytelen szám: %r" % (v,))
    if 1 < x < 100:
        x = x / 100.0
    if not 0 < x < 1:
        raise ValueError("level: 0 < level < 1 szükséges (pl. 0.95 vagy 95), kapott: %r" % (v,))
    return x


def resolve_columns(opt, meta, rows):
    """A --subgroup / --moderators / --cumulative oszlopnevek feloldása (eredeti fejléc,
    kanonikus név vagy szinonima; tableio.resolve_column). Ismeretlen oszlop → ValueError
    az opció nevével és az elérhető oszlopokkal — még mielőtt bármi kimenet készülne."""
    from .tableio import resolve_column

    def res(name, flag):
        try:
            return resolve_column(name, meta, rows)
        except ValueError as exc:
            raise ValueError("%s: %s" % (flag, exc))

    cols = {}
    if opt.get("subgroup"):
        cols["subgroup"] = res(opt["subgroup"], "--subgroup")
    if opt.get("moderators"):
        cols["moderators"] = [res(m, "--moderators") for m in opt["moderators"]]
    if opt.get("cumulative"):
        cols["cumulative"] = res(opt["cumulative"], "--cumulative")
    return cols


def column_labels(cols, meta):
    """A feloldott oszlopkulcsok megjelenítési neve: az eredeti CSV-fejléc (pl. 'year' → 'év'),
    hogy a riport a felhasználó oszlopnevét mutassa, ne a belső kanonikus kulcsot."""
    mapping = (meta or {}).get("mapping") or {}

    def lab(key):
        for orig, k in mapping.items():
            if k == key:
                return orig.strip()
        return key

    out = {}
    for name, val in cols.items():
        out[name] = [lab(v) for v in val] if isinstance(val, list) else lab(val)
    return out


def _num(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _totals(measure, es, out):
    """Résztvevők és események összesítése az ELEMZETT vizsgálatokra (GRADE SoF-hoz)."""
    t = {"k": len(es), "scope": "az elemzett vizsgálatok összesen (a kizárt sorok nélkül)"}
    ni = list(es.ni)
    known = [n for n in ni if n is not None]
    t["participants"] = _num(sum(known)) if known and len(known) == len(ni) else None
    if known and len(known) < len(ni):
        t["participants_partial"] = _num(sum(known))
        t["participants_known_k"] = len(known)
    rows = es.rows

    def colsum(c):
        vals = [r.get(c) for r in rows]
        return _num(sum(vals)) if vals and all(isinstance(v, (int, float)) for v in vals) else None

    if measure in E.CONTINUOUS or measure in E.BINARY:
        t["n1"], t["n2"] = colsum("n1"), colsum("n2")
    if measure in E.BINARY:
        t["events1"], t["events2"] = colsum("e1"), colsum("e2")
        if t["n1"] and t["events1"] is not None:
            t["risk1"] = t["events1"] / float(t["n1"])
        if t["n2"] and t["events2"] is not None:
            t["control_risk"] = t["events2"] / float(t["n2"])
        acr = t.get("control_risk")
        bt = (out.get("back_transformed") or {}).get("estimate_ci")
        if acr is not None and bt and None not in bt:
            def risk(x):
                if measure == "RR":
                    r = acr * x
                elif measure == "OR":
                    r = acr * x / (1 - acr + acr * x)
                else:
                    r = acr + x
                return min(1.0, max(0.0, r))
            ab = t["absolute_per_1000"] = {
                "assumed_control_risk_per_1000": 1000 * acr,
                # RD: maga a kockázatkülönbség (alapkockázattól független); RR/OR: az alapkockázatra vetítve
                "difference": [1000 * (x if measure == "RD" else risk(x) - acr) for x in bt],
                "intervention_risk": [1000 * risk(x) for x in bt],
                "note": ("Illusztratív: a feltételezett kontrollkockázat a kontrollkarok (2. kar) nyers "
                         "összesített kockázata (Σe2/Σn2); a GRADE SoF-hoz a klinikailag releváns "
                         "alapkockázatot használd, ha van."),
            }
            if measure == "RD" and any(not 0.0 <= acr + x <= 1.0 for x in bt):
                # a (véletlen hatású) RD nagyobb, mint amit ez az alapkockázat megenged: nincs levágott,
                # a 'difference'-nek ellentmondó beavatkozási kockázat
                ab["intervention_risk"] = [1000 * (acr + x) if 0.0 <= acr + x <= 1.0 else None for x in bt]
                ab["incompatible_with_baseline"] = True
                out["warnings"].append(
                    "Abszolút hatás (RD): az összesített kockázatkülönbség vagy CI-határa (%.1f [%.1f; %.1f]/1000) "
                    "nem egyeztethető össze a feltételezett kontrollkockázattal (%.1f/1000, Σe2/Σn2): a "
                    "beavatkozási kockázat 0 alá vagy 1 fölé esne. Az összesített RD főként a magas "
                    "alapkockázatú vizsgálatokból adódhat; a GRADE SoF abszolút hatásához az összesített "
                    "RR-t/OR-t alkalmazd egy klinikailag releváns alapkockázatra."
                    % (1000 * bt[0], 1000 * bt[1], 1000 * bt[2], 1000 * acr))
    if measure in E.PROPORTION:
        t["events"] = colsum("x")
    return t


def run(rows, options=None, meta=None):
    opt = dict(DEFAULTS)
    opt.update({k: v for k, v in (options or {}).items() if v is not None})
    measure = opt["measure"].upper()
    opt["measure"] = measure
    opt["level"] = normalize_level(opt["level"])
    check_options(opt)
    cols = resolve_columns(opt, meta, rows)
    out = {"engine": {"name": "metaelemzes", "version": __version__}, "options": opt,
           "input": {k: v for k, v in (meta or {}).items() if k != "mapping"}, "warnings": [],
           "columns": cols, "column_labels": column_labels(cols, meta)}
    # 0) szűrők (--exclude / --include): mi maradt ki, és hány sor maradt
    flt = dict(out["input"].get("filters") or {})
    frep = opt.get("filter_report") or []
    if frep or flt.get("exclude") or flt.get("include"):
        flt["report"] = list(frep)
        flt["n_rows_read"] = out["input"].get("n_rows")
        flt["n_rows_kept"] = len(rows)
        out["input"]["filters"] = flt
    # 1) validálás; a vizsgálat-szintű 'error' tételek sorai TÉNYLEG kimaradnak
    findings = V.validate(rows, measure, meta, opt)
    # soronként (nem címkénként): több karú vizsgálatnál csak a hibás sor marad ki
    blocking = V.blocking_rows(findings)
    # 2) hatásméretek
    es = E.compute(rows, measure, label_col=opt["label_col"], smd_vtype=opt["smd_vtype"],
                   j_method=opt["j_method"], cc=opt["cc"], cc_to=opt["cc_to"], drop00=opt["drop00"],
                   skip_rows=blocking, md_vtype=opt["md_vtype"], glass_vtype=opt["glass_vtype"],
                   gen_smd_vtype=opt["gen_smd_vtype"])
    findings += V.check_effect_sizes(es)
    out["validation"] = {"summary": V.summarize(findings), "findings": findings,
                         "blocked_studies": sorted({E.row_label(rows[i], i, opt["label_col"]) for i in blocking})}
    out["kb_refs"] = sorted(set(f["code"] for f in findings))
    k = len(es)
    nh = E.harmonic_mean(es.ni) if measure == "PFT" else None
    pft_n = opt["pft_backtransform"]
    hc = opt["h_centre"]
    out["effect_sizes"] = {"measure": measure, "label": E.MEASURE_LABELS.get(measure, measure),
                           "k": k, "labels": list(es.labels), "ni": list(es.ni),
                           "row_index": list(getattr(es, "row_index", []) or []),
                           "corrected": [lab for lab, note in zip(es.labels, es.notes)
                                         if note and "korrekci" in note],
                           "double_zero_included": [lab for lab, r in zip(es.labels, es.rows)
                                                    if measure in E.BINARY and _double_zero(r)],
                           "excluded": [{"study": a, "reason": b} for a, b in es.excluded],
                           "warnings": es.warnings}
    # a hatásméret-számítás felülírt opciói (pl. COHEN_D + UB → LS) a riportban is látsszanak; a kizárt
    # sorokat a riport külön listázza
    out["warnings"] += [w for w in es.warnings if "sor kimaradt a hatásméret-számításból" not in w]
    if k == 0:
        out["warnings"].append("Nincs elemezhető vizsgálat.")
        return out, es
    # 3) modellek
    req = opt["model"]
    # --ci a közös hatású ELSŐDLEGES modellre is vonatkozik (z/t; HKSJ: metafor-szerűen, figyelmeztetéssel);
    # a véletlen hatású elemzés mellé illesztett közös hatású érzékenységi modell Wald (z), mint a metaforban
    fixed_ci = (opt["ci"] or "z") if req == "fixed" else "z"
    fixed = M.meta_analysis(es.yi, es.vi, "fixed", ci_method=fixed_ci, level=opt["level"], labels=es.labels,
                            h_centre=hc)
    if req == "fixed" and fixed_ci in ("hksj", "hksj_adhoc") and k >= 2:
        out["warnings"].append("A Knapp–Hartung (HKSJ) módszer nem közös hatású modellhez készült (a metafor "
                               "is figyelmeztet); értelmezd óvatosan, vagy használd a z / t CI-t.")
    random_ = M.meta_analysis(es.yi, es.vi, "random", opt["tau2"], opt["ci"] or "hksj", opt["level"],
                              opt["pi"], es.labels, h_centre=hc) if k >= 2 else None
    primary = random_ if (req == "random" and random_ is not None) else fixed
    out["fixed"] = fixed
    out["random"] = random_
    out["primary_model"] = "random" if primary is random_ else "fixed"
    if req == "ivhet" and k >= 2:
        # IVhet: τ² alapértelmezésben DL (Doi 2015); kifejezett --tau2 esetén az (a motor figyelmeztet)
        out["ivhet"] = M.meta_analysis(es.yi, es.vi, "ivhet", opt["tau2"], level=opt["level"], labels=es.labels,
                                       h_centre=hc)
        primary = out["ivhet"]
        out["primary_model"] = "ivhet"
    if req in ("random", "ivhet") and k < 2:
        out["model_fallback"] = {"requested": req, "used": "fixed", "reason": "k = 1"}
        out["warnings"].append("k = 1: %s modell nem illeszthető, összesítés nem történt; az eredmény az "
                               "egyetlen vizsgálat becslése (közös hatású modellként, z-alapú CI)."
                               % ("véletlen hatású" if req == "random" else "IVhet"))
    out["primary"] = primary
    if measure in E.BINARY and opt["mh"]:
        cols4 = [[r.get(c) for r in es.rows] for c in ("e1", "n1", "e2", "n2")]
        try:
            out["mh"] = M.mantel_haenszel(*cols4, measure=measure, level=opt["level"], labels=es.labels,
                                          cc=opt["cc"], rd_var=opt["rd_var"], h_centre=hc)
        except (M.ModelError, ArithmeticError, ValueError) as exc:
            out["warnings"].append("MH: %s" % exc)
    if measure == "OR" and opt["peto"]:
        cols4 = [[r.get(c) for r in es.rows] for c in ("e1", "n1", "e2", "n2")]
        try:
            out["peto"] = M.peto(*cols4, level=opt["level"], labels=es.labels, h_centre=hc)
        except (M.ModelError, ArithmeticError, ValueError) as exc:
            out["warnings"].append("Peto: %s" % exc)
    out["back_transformed"] = {
        "estimate_ci": _display(measure, primary.estimate, primary.ci_lower, primary.ci_upper, nh, pft_n,
                                primary.se),
        # a PI-t mindig a harmonikus átlag n-nel (a MetaXL nem ad PI-t; metafor/meta konvenció)
        "pi": _display(measure, primary.pi_lower, primary.pi_lower, primary.pi_upper, nh)[1:]
        if primary.pi_lower is not None else None,
        "scale_note": _scale_note(measure, pft_n),
        "n_harmonic": nh,
        "pft_backtransform": pft_n if measure == "PFT" else None,
        "pft_m": (1.0 / (4.0 * primary.se ** 2)) if (measure == "PFT" and pft_n == "variance"
                                                   and primary.se and primary.se > 0) else None,
        "level": opt["level"],
    }
    out["totals"] = _totals(measure, es, out)
    # a sorrendi/érzékenységi elemzések ugyanazzal a CI-módszerrel, mint az elsődleges modell
    ci_eff = primary.ci_method if out["primary_model"] in ("random", "fixed") else None
    # 4) alcsoport / meta-regresszió
    if opt["subgroup"]:
        col = cols["subgroup"]
        groups = [r.get(col) for r in es.rows]
        missing = [lab for lab, g in zip(es.labels, groups) if _blank(g)]
        if missing:
            out["warnings"].append("Az alcsoport-oszlopban (%s) hiányzó érték van (%s); az alcsoport-elemzés "
                                   "kimaradt." % (opt["subgroup"], ", ".join(missing)))
        else:
            grp = [_level_str(g) for g in groups]
            if tableio.is_rob_column(col):
                grp, merged = tableio.rob_levels(grp)
                if merged:
                    out["warnings"].append("Alcsoport (%s): ugyanannak a RoB-kategóriának eltérő írásmódjai egy szintbe "
                                           "kerültek (%s); egységesítsd az adattáblában." % (opt["subgroup"], "; ".join(
                                               "'%s' = '%s'" % kv for kv in sorted(merged.items()))))
            try:
                sg = MO.subgroup_analysis(es.yi, es.vi, grp, es.labels, opt["model"], opt["tau2"], opt["ci"],
                                          opt["level"], opt["common_tau2"], opt["pi"], h_centre=hc)
            except (M.ModelError, ArithmeticError) as exc:
                sg = None
                out["warnings"].append("Alcsoport-elemzés: %s" % exc)
            rix = list(getattr(es, "row_index", []) or [])
            for g in (sg.groups if sg is not None else []):
                # pozíció szerint (nem címke szerint): ismétlődő címkéjű sorok (több kar) is jók
                g.indices = [i for i, x in enumerate(grp) if x == g.group]
                if len(rix) == k:
                    g.row_index = [rix[i] for i in g.indices]
                g.n_harmonic = E.harmonic_mean([es.ni[i] for i in g.indices]) if measure == "PFT" else None
                if measure == "PFT" and g.n_harmonic is None:
                    g.n_harmonic = nh
                g.display = _display(measure, g.estimate, g.ci_lower, g.ci_upper, g.n_harmonic, pft_n, g.se)
            if sg is not None:
                out["subgroups"] = sg
    if opt["moderators"]:
        # az együtthatók neve az eredeti oszlopnév (nem a belső kanonikus kulcs, pl. 'év', nem 'year')
        mods = list(zip(cols["moderators"], out["column_labels"]["moderators"]))
        # az elemzett vizsgálatokban állandó moderátor (pl. szűrés után egyetlen szint) nem becsülhető:
        # kategoriálisnál csendben eltűnne (csak tengelymetszet), numerikusnál szinguláris lenne
        const = [lab for key, lab in mods if len(_mod_levels([r.get(key) for r in es.rows])) < 2]
        if const:
            out["warnings"].append("Meta-regresszió: a(z) %s moderátor az elemzett vizsgálatokban egyetlen értéket "
                                   "vesz fel (nincs becsülhető hatás), ezért kimaradt a modellből." % ", ".join(const))
            mods = [(key, lab) for key, lab in mods if lab not in const]
            out["metaregression_dropped"] = const
    if opt["moderators"] and mods:
        try:
            view = [{lab: r.get(key) for key, lab in mods} for r in es.rows]
            x, names = MO.design_matrix(view, [lab for _, lab in mods])
            mr_tau2 = opt["metareg_tau2"] or (opt["tau2"] if opt["tau2"] in MO.MR_TAU2_METHODS else "REML")
            out["metaregression"] = MO.meta_regression(
                es.yi, es.vi, x, names, mr_tau2,
                opt["metareg_test"], opt["level"], robust=bool(opt["metareg_robust"]))
            if mr_tau2 == "FE" and opt["metareg_test"] == "knha":
                out["warnings"].append("Meta-regresszió: a Knapp–Hartung-próba nem közös hatású (FE) modellhez "
                                       "készült (a metafor is figyelmeztet); FE-súlyokkal a modell-alapú SE így a "
                                       "Stata regress [aw=1/v] (nem robusztus) SE-je. Wald-típusú próbához a z "
                                       "tesztet válaszd (metareg_test = z).")
        except (M.ModelError, ArithmeticError) as exc:
            out["warnings"].append("Meta-regresszió: %s" % exc)
    # 5) kis-vizsgálat hatások (minden teszt külön: egyik hibája nem viszi magával a többit)
    bias = {"performed": k >= 3, "k": k, "min_k_recommended": opt["bias_min_k"]}

    def _bias(key, label, fn):
        try:
            bias[key] = fn()
        except (M.ModelError, ArithmeticError, ValueError) as exc:
            out["warnings"].append("Torzítás-elemzés (%s): %s" % (label, exc))

    if k >= 3:
        _bias("egger", "Egger", lambda: B.egger_test(es.yi, es.vi, opt["egger_ci_dist"], opt["level"]))
        _bias("begg", "Begg", lambda: B.begg_test(es.yi, es.vi, opt["begg_method"], bool(opt["begg_continuity"])))
        # a korrigált modell az elsődleges modell CI-módszerével és szintjével (k0 = 0 esetén
        # így pontosan az elsődleges eredmény, mint a metafor trimfill-ben)
        _bias("trimfill", "trim-and-fill", lambda: B.trim_and_fill(
            es.yi, es.vi, es.labels, opt["model"], opt["tau2"], opt["trimfill_estimator"],
            ci_method=ci_eff or "z", level=opt["level"], trim_model=opt["trimfill_trim_model"], h_centre=hc))
        # Doi-plot / LFK-index (heurisztikus, érzékenységi jellegű; Furuya-Kanamori et al. 2018)
        _bias("lfk", "LFK-index", lambda: B.doi_plot_data(es.yi, es.vi))
        # bináris kimenet 2×2 cellákkal: Harbord és Peters (log OR alapú; Sterne et al. 2011)
        if measure in E.BINARY and _has_cells(es.rows):
            cols4 = [[r.get(c) for r in es.rows] for c in ("e1", "n1", "e2", "n2")]
            _bias("harbord", "Harbord", lambda: B.harbord_test(*cols4))
            _bias("peters", "Peters", lambda: B.peters_test(*cols4, cc=opt["cc"] if opt["cc"] else 0.5))
            bias["binary_note"] = ("Bináris kimenetnél a klasszikus Egger-teszt csak tájékoztató (a log OR és a "
                                   "standard hibája nem független, ezért álpozitív eredményre hajlamos); a Harbord- "
                                   "és a Peters-teszt az ajánlott (Sterne et al. 2011). Mindkettő log OR alapú%s."
                                   % ("" if measure == "OR" else ", akkor is, ha az elemzés %s skálán történt" % measure))
    # GEN + --gen-smd-vtype: közölt SMD-k (ugyanaz az yi–vi összefüggés)
    smd_like = measure in ("SMD", "COHEN_D", "SMD_GLASS", "SMCC") or (measure == "GEN" and opt.get("gen_smd_vtype"))
    if smd_like and bias.get("egger") is not None:
        bias["smd_note"] = ("SMD-nél a klasszikus Egger-teszt csak tájékoztató: az SMD és a standard hibája "
                            "összefügg, ezért torzítás nélkül is álpozitív lehet (Pustejovsky & Rodgers 2019; "
                            "D-S11-005).")
    if k < opt["bias_min_k"]:
        bias["note"] = ("k = %d < %d: a funnel-aszimmetria tesztek eredménye csak tájékoztató, "
                        "nem értelmezhető (Sterne et al. 2011)." % (k, opt["bias_min_k"]))
    out["bias"] = bias
    # 6) érzékenység
    sens = {}
    if k >= 3:
        sens["leave_one_out"] = SE.leave_one_out(es.yi, es.vi, es.labels, opt["model"], opt["tau2"],
                                                 ci_eff, opt["level"])
        sens["influence"] = SE.influence(es.yi, es.vi, es.labels, opt["model"], opt["tau2"], opt["level"])
    if opt["outliers"]:
        if k >= 3:
            try:
                sens["outliers"] = SE.outlier_screen(es.yi, es.vi, es.labels, opt["model"], opt["tau2"], opt["level"],
                                                     ci_eff, hc)
            except (M.ModelError, ArithmeticError) as exc:
                out["warnings"].append("Kiugró-szűrés: %s" % exc)
        else:
            out["warnings"].append("Kiugró-szűrés kihagyva: k = %d < 3." % k)
    if opt["cumulative"]:
        col = cols["cumulative"]
        keys = [r.get(col) for r in es.rows]
        miss = [lab for lab, kk in zip(es.labels, keys) if _blank(kk)]
        if len(miss) == k:
            out["warnings"].append("Kumulatív elemzés kihagyva: a(z) '%s' oszlop minden elemzett vizsgálatnál "
                                   "üres." % opt["cumulative"])
        else:
            if miss:
                out["warnings"].append("Kumulatív elemzés: %d vizsgálatnál hiányzik a rendezőkulcs (%s); ezek a "
                                       "sor végére kerültek (mint a metafor cumul()-ban)." % (len(miss), ", ".join(miss)))
            present = [kk for kk in keys if not _blank(kk)]
            if any(isinstance(kk, (int, float)) for kk in present) and any(isinstance(kk, str) for kk in present):
                txt = [lab for lab, kk in zip(es.labels, keys) if isinstance(kk, str) and not _blank(kk)]
                out["warnings"].append("Kumulatív elemzés: a(z) '%s' oszlop vegyesen tartalmaz számot és szöveget "
                                       "(szöveges: %s); természetes (szöveg szerinti) rendezés történt — ellenőrizd a "
                                       "sorrendet, vagy egységesítsd az oszlopot (pl. csak évszám)."
                                       % (out["column_labels"].get("cumulative") or opt["cumulative"], ", ".join(txt)))
            sens["cumulative"] = SE.cumulative(es.yi, es.vi, es.labels, keys, opt["model"], opt["tau2"],
                                               ci_eff, opt["level"])
            for i, r in enumerate(sens["cumulative"]):
                # a sor SE-je (a PFT MetaXL-visszatranszformáláshoz és a JSON-hoz)
                r.setdefault("se", _row_se(r, ci_eff, opt["level"], single_fixed=(i == 0)))
    out["sensitivity"] = sens
    out["warnings"] += [w for w in (primary.warnings or []) if w not in out["warnings"]]
    if measure == "PR":
        _pr_bound_warning(out, es, opt["level"])
    elif measure == "PFT" and k >= 2:
        _pft_range_warning(out, es, nh, pft_n)
    return out, es


def _pr_bound_warning(out, es, level):
    """Nyers arány (PR): a [0, 1]-en kívülre nyúló Wald-intervallumok (lehetetlen értékek) jelzése
    (D-S07-018, D-S07-019); csonkolás nincs, a számok a metaforéval egyeznek."""
    from .distributions import norm_ppf
    z = norm_ppf(0.5 + level / 2)

    def out_of(vals):
        return any(v is not None and (v < -1e-12 or v > 1 + 1e-12) for v in vals)

    where = []
    n_st = sum(1 for y, v in zip(es.yi, es.vi) if out_of((y - z * math.sqrt(v), y + z * math.sqrt(v))))
    if n_st:
        where.append("%d vizsgálat CI-je" % n_st)
    bt = out.get("back_transformed") or {}
    if out_of(bt.get("estimate_ci") or []):
        where.append("az összesített becslés CI-je")
    if out_of(bt.get("pi") or []):
        where.append("a predikciós intervallum")
    for key, lab in (("fixed", "a közös hatású"), ("random", "a véletlen hatású")):
        r = out.get(key)
        if r is not None and r is not out.get("primary") and out_of((r.ci_lower, r.ci_upper)):
            where.append("%s érzékenységi modell CI-je" % lab)
    sg = out.get("subgroups")
    bad_g = [str(g.group) for g in (sg.groups if sg is not None else []) if out_of((g.ci_lower, g.ci_upper))]
    if bad_g:
        where.append("az alcsoport(ok) CI-je (%s)" % ", ".join(bad_g))
    if where:
        out["warnings"].append(
            "Nyers arány (PR): %s 0 alá vagy 1 fölé nyúlik — lehetetlen érték (a Wald-intervallum a [0, 1] határ "
            "közelében torz). Elsődleges skálaként logit (--measure PLO) vagy binomiális GLMM (R: metafor "
            "rma.glmm / meta metaprop) ajánlott; a Freeman–Tukey (PFT) legfeljebb érzékenységi elemzés "
            "(D-S07-018, D-S07-019)." % ", ".join(where))


def _pft_range_warning(out, es, nh, pft_n):
    """PFT: a visszatranszformált összesített pontbecslés a saját vizsgálatai megfigyelt arányainak
    tartományán kívül (nagyon eltérő mintanagyságoknál a Miller-inverz félrevezető; Schwarzer et al. 2019)."""
    try:
        p = [float(r["x"]) / float(r["n"]) for r in es.rows]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return
    bad = []

    def check(lab, est, idx):
        lo, hi = min(p[i] for i in idx), max(p[i] for i in idx)
        if est is not None and (est < lo - 1e-9 or est > hi + 1e-9):
            bad.append("%s: %s, megfigyelt tartomány [%s; %s]" % (lab, _fmt4(est), _fmt4(lo), _fmt4(hi)))

    for key, lab in (("ivhet", "IVhet"), ("random", "véletlen hatású"), ("fixed", "közös hatású")):
        r = out.get(key)
        if r is not None:
            check(lab, _display("PFT", r.estimate, None, None, nh, pft_n, r.se)[0], range(len(p)))
    sg = out.get("subgroups")
    for g in (sg.groups if sg is not None else []):
        if g.k >= 2 and g.get("indices") and g.get("display"):
            check("alcsoport '%s'" % g.group, g.display[0], g.indices)
    if bad:
        out["warnings"].append(
            "Freeman–Tukey-visszatranszformálás (%s): az összesített becslés a megfigyelt arányok tartományán "
            "kívül esik (%s) — nagyon eltérő mintanagyságoknál a Miller-inverz félrevezető (Schwarzer et al. "
            "2019; K-KHN0607-079). Elsődleges elemzésként logit (--measure PLO) vagy binomiális GLMM (R: metafor "
            "rma.glmm / meta metaprop) ajánlott; érzékenységi elemzésként a másik visszatranszformálás "
            "(--pft-backtransform %s)." % (
                ("harmonikus átlag n = %.4g" % nh) if pft_n != "variance" else "m = 1/Var(t)", "; ".join(bad),
                "variance" if pft_n != "variance" else "harmonic"))


def _fmt4(v):
    return "%.4f" % v if v is not None else "–"


def _has_cells(rows):
    """Minden elemzett sorban van 2×2 cella (e1, n1, e2, n2 szám) → Harbord / Peters futtatható."""
    for r in rows:
        for c in ("e1", "n1", "e2", "n2"):
            v = r.get(c)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v:
                return False
    return bool(rows)


def _scale_note(measure, pft_n="harmonic"):
    if measure in ("OR", "RR", "ROM"):
        return "Az elemzés log-skálán történt; a közölt értékek exponenciálisan visszatranszformáltak."
    if measure == "PLO":
        return "Logit-skálán elemezve; visszatranszformálva arányra."
    if measure == "PFT":
        if pft_n == "variance":
            return ("Freeman–Tukey skálán elemezve; visszatranszformálás Miller (1978) képletével a MetaXL-konvenció "
                    "szerint (Barendregt et al. 2013): minden összesített becslés és CI-je a saját varianciájából "
                    "számolt m = 1/Var(t) értékkel; a predikciós intervallum a harmonikus átlag n-nel.")
        return "Freeman–Tukey skálán elemezve; visszatranszformálás Miller (1978) képletével, harmonikus átlag n-nel."
    if measure == "ZCOR":
        return "Fisher z-skálán elemezve; visszatranszformálva r-re."
    if measure == "PAS":
        return "Arcsin-skálán elemezve; visszatranszformálva arányra."
    if measure == "PLN":
        return "Log-skálán elemezve; visszatranszformálva arányra."
    return None


def make_plots(out, es, opt=None):
    """Forest + funnel SVG és a plot_data JSON (a figure-forge-hoz)."""
    opt = dict(DEFAULTS, **(opt or out.get("options", {})))
    measure = out["effect_sizes"]["measure"]
    primary = out["primary"]
    nh = E.harmonic_mean(es.ni) if measure == "PFT" else None
    pft_n = opt.get("pft_backtransform") or "harmonic"
    lv = round(opt["level"] * 100)
    summaries = []
    for key, lab in (("ivhet", "IVhet modell (Doi 2015)"), ("random", "Véletlen hatású modell"),
                     ("fixed", "Közös (fix) hatású modell")):
        r = out.get(key)
        if r is None:
            continue
        if key == "random":
            lab = "%s (%s, %s)" % (lab, r.tau2_method, r.ci_method.upper())
        elif key == "fixed" and r.ci_method != "z":
            lab = "%s (%s)" % (lab, r.ci_method.upper())
        sm = {"label": lab, "estimate": r.estimate, "ci_lower": r.ci_lower, "ci_upper": r.ci_upper,
              "display": _display(measure, r.estimate, r.ci_lower, r.ci_upper, nh, pft_n, r.se),
              "pi_lower": r.pi_lower, "pi_upper": r.pi_upper}
        if r.pi_lower is not None:
            sm["pi_display"] = _display(measure, r.pi_lower, r.pi_lower, r.pi_upper, nh)[1:]
        summaries.append(sm)
    if out["primary_model"] == "fixed":
        summaries.reverse()
    weights = primary.weights_pct
    sections = None
    if out.get("subgroups"):
        sections = []
        sg = out["subgroups"]
        col = (out.get("columns") or {}).get("subgroup") or opt.get("subgroup")
        grp = [_level_str(r.get(col)) for r in es.rows] if col else None
        for g in sg.groups:
            idx = g.get("indices")
            if idx is None:      # régi eredmény-objektum: pozíció a csoportváltozóból (nem címkéből)
                idx = [i for i, x in enumerate(grp or []) if x == str(g.group)]
            ng = g.get("n_harmonic") or nh
            sections.append({"title": str(g.group), "indices": list(idx),
                             "row_index": list(g.get("row_index") or []), "summary": {
                "label": "Alcsoport összesen (k = %d)" % g.k, "estimate": g.estimate,
                "ci_lower": g.ci_lower, "ci_upper": g.ci_upper,
                "display": g.get("display") or _display(measure, g.estimate, g.ci_lower, g.ci_upper, ng, pft_n, g.se),
                "pi_lower": None, "pi_upper": None,
                # közös hatású modellnél a τ² nem becsült: '–', mint a láblécben
                "note": ("τ² = %s; I² = %.0f%%" % ("–" if opt.get("model") == "fixed" else "%.3g" % g.tau2, g.I2))
                if g.k > 1 else None}})
    per_study_n = es.ni if measure == "PFT" else None
    if measure == "PFT" and pft_n == "variance":
        # MetaXL: a vizsgálati sor m-je is 1/Var(t) = 1/(4·v_i) = n_i + 0,5
        per_study_n = [1.0 / (4.0 * v) for v in es.vi]
    data = P.forest_data(measure, es.labels, es.yi, es.vi, weights, summaries, opt["level"],
                         per_study_n, sections)
    footer = ["Heterogenitás: Q = %.2f (df = %d, p %s); I² (Q-alapú) = %.0f%%; τ² = %s" % (
        primary.Q, primary.Q_df, _p(primary.p_Q), primary.I2,
        ("%.3g" % primary.tau2) if out["primary_model"] in ("random", "ivhet") else "–")]
    if out.get("subgroups"):
        sg = out["subgroups"]
        if sg.Q_between is not None:
            footer.append("Alcsoport-különbség: Q_b = %.2f (df = %d, p %s)" % (sg.Q_between, sg.df_between,
                                                                             _p(sg.p_between)))
    if primary.pi_lower is not None:
        footer.append("Piros vonal: %d%%-os predikciós intervallum (%s)." % (lv, primary.pi_method))
    if measure in E.PROPORTION:
        footer.append("Egycsoportos arány: nincs nullhatás-vonal; a tengelyfeliratok arányok (az elemzési "
                      "skálán elhelyezve).")
    elif measure == "ZCOR":
        footer.append("A tengelyfeliratok r-értékek (Fisher z-skálán elhelyezve); szaggatott vonal: r = 0.")
    axis_t = P.axis_label(measure, _short(measure))
    svg_forest = P.forest_svg(data, title=opt.get("title"), left_label=opt.get("left_label"),
                              right_label=opt.get("right_label"),
                              effect_label="%s [%d%% CI]" % (_short(measure), lv),
                              footer=footer, axis_title=axis_t)
    tf = out.get("bias", {}).get("trimfill")
    contour = measure not in E.PROPORTION
    svg_funnel = P.funnel_svg(es.yi, es.vi, out["fixed"].estimate, measure,
                              title="Funnel plot (kontúr-javított)" if contour else "Funnel plot",
                              filled_yi=tf.filled_yi if tf else None, filled_vi=tf.filled_vi if tf else None,
                              contour=contour, n_harmonic=nh, axis_title=axis_t)
    data["axis_title"] = axis_t
    data["null_value"] = P.default_null(measure)
    data["heterogeneity"] = {"Q": primary.Q, "df": primary.Q_df, "p": primary.p_Q, "I2": primary.I2,
                             "I2_from_tau2": primary.heterogeneity.get("I2_from_tau2"),
                             "tau2": primary.tau2}
    data["funnel"] = {"yi": es.yi, "sei": [math.sqrt(v) for v in es.vi], "center": out["fixed"].estimate,
                      "contour": contour, "contour_center": P.default_null(measure),
                      "filled_yi": tf.filled_yi if tf else [],
                      "filled_sei": [math.sqrt(v) for v in tf.filled_vi] if tf else []}
    lf = out.get("bias", {}).get("lfk")
    if lf is not None:
        data["doi"] = {"points": [dict(p, label=es.labels[p["study_index"]]) for p in lf.points],
                       "lfk": lf.lfk, "category": lf.category,
                       "note": "x: hatás az elemzési skálán; y: |Z| (fordított tengely, 0 felül)"}
    return svg_forest, svg_funnel, data


def make_doi_plot(out, es):
    """Doi-plot SVG (|Z| a hatás függvényében, LFK-indexszel), vagy None, ha nincs LFK-eredmény."""
    lf = (out.get("bias") or {}).get("lfk")
    if lf is None:
        return None
    measure = out["effect_sizes"]["measure"]
    nh = E.harmonic_mean(es.ni) if measure == "PFT" else None
    return P.doi_svg(lf.points, lf.lfk, lf.category, measure, labels=list(es.labels),
                     title="Doi-plot", n_harmonic=nh,
                     axis_title=P.axis_label(measure, _short(measure)))


def _p(p):
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "= –"
    return "< 0.001" if p < 0.001 else "= %.3f" % p


def _short(measure):
    return {"SMD": "Hedges g", "COHEN_D": "Cohen d", "MD": "MD", "OR": "OR", "RR": "RR", "RD": "RD",
            "SMD_GLASS": "Glass Δ", "MC": "Átlagos változás", "SMCC": "Standardizált változás",
            "ROM": "Ratio of means", "PR": "Arány", "PLN": "Arány", "PLO": "Arány", "PAS": "Arány",
            "PFT": "Arány", "COR": "r", "ZCOR": "r", "GEN": "Hatás"}.get(measure, measure)


PLOT_FILES = ("forest.svg", "funnel.svg", "doi.svg", "plot_data.json")


def write_outputs(out, es, outdir, report_md=None, plots=True):
    """A kimenetek írása. Egy korábbi futás ábrafájljai (PLOT_FILES), amelyeket ez a futás nem ír újra
    (--no-plots, k = 0, k < 3 → nincs doi.svg), törlődnek, hogy a mappa ne keverjen két elemzést."""
    os.makedirs(outdir, exist_ok=True)
    paths = {}
    if plots and out.get("primary") is not None:
        f_svg, fu_svg, data = make_plots(out, es)
        svgs = [("forest.svg", f_svg), ("funnel.svg", fu_svg)]
        doi = make_doi_plot(out, es)
        if doi is not None:
            svgs.append(("doi.svg", doi))
        for name, content in svgs:
            p = os.path.join(outdir, name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(content)
            paths[name] = p
        p = os.path.join(outdir, "plot_data.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(to_jsonable(data), fh, ensure_ascii=False, indent=1)
        paths["plot_data.json"] = p
    for name in PLOT_FILES:
        p = os.path.join(outdir, name)
        if name not in paths and os.path.isfile(p):
            os.remove(p)
    p = os.path.join(outdir, "results.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(to_jsonable(out), fh, ensure_ascii=False, indent=1)
    paths["results.json"] = p
    # vizsgálatonkénti hatásméretek
    from .tableio import write_csv
    from .distributions import norm_ppf
    z = norm_ppf(0.5 + out["options"]["level"] / 2)
    rows = []
    w = out["primary"].weights_pct if out.get("primary") is not None else [None] * len(es)
    for i, (lab, y, v) in enumerate(zip(es.labels, es.yi, es.vi)):
        se = math.sqrt(v)
        n = es.ni[i] if i < len(es.ni) else None
        rows.append([lab, y, v, se, y - z * se, y + z * se, w[i] if w else None, _num(n), es.notes[i]])
    p = os.path.join(outdir, "effect_sizes.csv")
    write_csv(p, ["study", "yi", "vi", "sei", "ci_lower", "ci_upper", "weight_pct", "n", "note"], rows)
    paths["effect_sizes.csv"] = p
    if report_md is not None:
        p = os.path.join(outdir, "report.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(report_md)
        paths["report.md"] = p
    return paths
