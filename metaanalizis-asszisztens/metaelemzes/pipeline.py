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
    # ábrák (E4/E5): a plot_data.json sémája ('v2' = szk.ma.plot/v2; 'v1' = a korábbi kulcsok), a feliratok
    # nyelve (SVG-k; a plot_data megjelenítési preferenciája) és az annotált SVG (rétegek, sor-azonosítók)
    "plot_schema": "v2", "plot_locale": "hu", "svg_annotate": False,
}

PLOT_SCHEMAS = ("v1", "v2")
PLOT_SCHEMA_V2 = "szk.ma.plot/v2"

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
    "plot_schema": PLOT_SCHEMAS,
    "plot_locale": P.LANGS,
}


def check_options(opt):
    """Az enumerált opciók ellenőrzése → ValueError az opció nevével és a lehetséges értékekkel
    (még mielőtt bármi kimenet készülne)."""
    for key, allowed in OPTION_CHOICES.items():
        if opt.get(key) not in allowed:
            raise ValueError("%s: érvénytelen érték %r (lehetséges: %s)" % (
                key, opt.get(key), ", ".join("–" if a is None else str(a) for a in allowed)))
    if not isinstance(opt.get("svg_annotate"), bool):
        raise ValueError("svg_annotate: logikai érték kell (true/false), kapott: %r" % (opt.get("svg_annotate"),))
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


def run(rows, options=None, meta=None, table_rows=None):
    """Az elemzés. rows: a (szűrt) adatsorok; table_rows: a szűrés előtti teljes tábla (a read_table sorai),
    hogy a row_uid / row_index a teljes táblára vonatkozzon (mint a validálási dokumentumban); None = a rows
    a teljes tábla. → (out, es); az es.row_uids / es.table_index a plot_data v2-höz (a results.json-ba nem
    kerül)."""
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
    attach_row_ids(es, rows, meta, table_rows)
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


def attach_row_ids(es, rows, meta=None, table_rows=None):
    """A bevont vizsgálatok sor-azonosítója (es.row_uids; tableio.row_uids, terv 4.6) és táblabeli
    0-alapú sorindexe (es.table_index). table_rows: a szűrés előtti teljes tábla; ha a rows sorai nincsenek
    benne (más objektumok), a rows számít teljes táblának."""
    full = rows if table_rows is None else table_rows
    pos = {id(r): i for i, r in enumerate(full)}
    rix = list(getattr(es, "row_index", []) or range(len(es)))
    if table_rows is not None and not all(0 <= i < len(rows) and id(rows[i]) in pos for i in rix):
        full = rows
        pos = {id(r): i for i, r in enumerate(full)}
    uids = tableio.row_uids(full, meta)
    es.table_index = [pos.get(id(rows[i]), i) if 0 <= i < len(rows) else i for i in rix]
    es.row_uids = [uids[i] if 0 <= i < len(uids) else tableio.row_uid_for(lab, i)
                   for i, lab in zip(es.table_index, es.labels)]
    # nyers piszkozatnál (tableio.parse_table) a sor helye a kliens sorai között (meta['row_positions'], a
    # közbülső üres sorokkal együtt, mint a validálási dokumentum 'row'-ja) — a plot row_index-e
    grid = (meta or {}).get("row_positions")
    if grid is not None and len(grid) == len(full):
        es.table_position = [grid[i] if 0 <= i < len(grid) else i for i in es.table_index]
    return es


def _row_ids(es):
    """(row_uids, sorindex) — a pipeline.run után az es-en; más úton számolt es-nél a sorindexből. A sorindex
    ugyanaz, mint a validálási dokumentum 'row'-ja: fájlnál az adatsor indexe, nyers piszkozatnál a kliens sorai
    közötti hely (meta['row_positions'])."""
    uids, tix = getattr(es, "row_uids", None), getattr(es, "table_index", None)
    if uids is not None and tix is not None and len(uids) == len(es) == len(tix):
        grid = getattr(es, "table_position", None)
        return list(uids), list(grid if grid is not None and len(grid) == len(tix) else tix)
    rix = list(getattr(es, "row_index", []) or [])
    if len(rix) != len(es):
        rix = list(range(len(es)))
    rows = [{} for _ in range(max(rix) + 1 if rix else 0)]
    for i, r in zip(rix, es.rows):
        rows[i] = r
    uids = tableio.row_uids(rows)
    return [uids[i] for i in rix], rix


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


# az ábrák kódba égetett feliratai nyelvenként (a 'hu' a korábbi, byte-azonos szöveg)
_MODEL_LABELS = {"ivhet": {"hu": "IVhet modell (Doi 2015)", "en": "IVhet model (Doi 2015)"},
                 "random": {"hu": "Véletlen hatású modell", "en": "Random-effects model"},
                 "fixed": {"hu": "Közös (fix) hatású modell", "en": "Common-effect model"}}
_PLOT_TEXTS = {
    "subtotal": {"hu": "Alcsoport összesen (k = %d)", "en": "Subtotal (k = %d)"},
    "het": {"hu": "Heterogenitás: Q = %s (df = %d, p %s); I² (Q-alapú) = %s%%; τ² = %s",
            "en": "Heterogeneity: Q = %s (df = %d, p %s); I² (Q-based) = %s%%; τ² = %s"},
    "sg_diff": {"hu": "Alcsoport-különbség: Q_b = %s (df = %d, p %s)",
                "en": "Test for subgroup differences: Q_b = %s (df = %d, p %s)"},
    # színsemleges (a motor SVG-je és a munkapad interaktív ábrája más színnel rajzolja a PI-t; UX-03)
    "pi_line": {"hu": "Külön sor a forest alján: %d%%-os predikciós intervallum, %s.",
                "en": "Separate row at the bottom of the forest plot: %d%% prediction interval, %s."},
    "pi_label": {"hu": "%d%%-os predikciós intervallum (%s)", "en": "%d%% prediction interval (%s)"},
    "prop_axis": {"hu": "Egycsoportos arány: nincs nullhatás-vonal; a tengelyfeliratok arányok (az elemzési "
                        "skálán elhelyezve).",
                  "en": "Single-arm proportion: no null-effect line; tick labels are proportions (placed on the "
                        "analysis scale)."},
    "zcor_axis": {"hu": "A tengelyfeliratok r-értékek (Fisher z-skálán elhelyezve); szaggatott vonal: r = 0.",
                  "en": "Tick labels are r values (placed on the Fisher z scale); dashed line: r = 0."},
    "funnel_contour": {"hu": "Funnel plot (kontúr-javított)", "en": "Contour-enhanced funnel plot"},
    "funnel": {"hu": "Funnel plot", "en": "Funnel plot"},
    "doi": {"hu": "Doi-plot", "en": "Doi plot"},
}
_SHORT_EN = {"MC": "Mean change", "SMCC": "Standardized mean change", "PR": "Proportion", "PLN": "Proportion",
             "PLO": "Proportion", "PAS": "Proportion", "PFT": "Proportion", "GEN": "Effect"}


def _plot_opt(out, opt=None):
    return dict(DEFAULTS, **(opt or out.get("options", {})))


def _ptext(key, lang):
    return _PLOT_TEXTS[key][lang]


def _summary_label(key, r, lang="hu"):
    lab = _MODEL_LABELS[key][lang]
    if key == "random":
        lab = "%s (%s, %s)" % (lab, r.tau2_method, r.ci_method.upper())
    elif key == "fixed" and r.ci_method != "z":
        lab = "%s (%s)" % (lab, r.ci_method.upper())
    return lab


def _g3(v, minus="-"):
    return P.num_text("%.3g" % v, minus)


def _subgroup_note(g, model, minus="-"):
    # közös hatású modellnél a τ² nem becsült: '–', mint a láblécben
    if g.k <= 1:
        return None
    return "τ² = %s; I² = %.0f%%" % ("–" if model == "fixed" else _g3(g.tau2, minus), g.I2)


def _het_line(out, primary, lang="hu", minus="-"):
    return _ptext("het", lang) % (
        P.num_text("%.2f" % primary.Q, minus), primary.Q_df, _p(primary.p_Q), "%.0f" % primary.I2,
        _g3(primary.tau2, minus) if out["primary_model"] in ("random", "ivhet") else "–")


def _sg_diff_line(sg, lang="hu", minus="-"):
    return _ptext("sg_diff", lang) % (P.num_text("%.2f" % sg.Q_between, minus), sg.df_between, _p(sg.p_between))


_PI_METHOD_TEXT = {"t_k-2": "t(k%s2)", "t_k-1": "t(k%s1)", "z": "z"}


def pi_method_text(method, lang="hu"):
    """A PI-módszer olvasható alakja a feliratokban ('t_k-2' → 't(k-2)'; angolul U+2212 mínusszal, a motor
    nyelvi konvenciója szerint); ismeretlen érték változatlan."""
    tpl = _PI_METHOD_TEXT.get(method)
    if tpl is None:
        return method
    return tpl % (P.MINUS if lang == "en" else "-") if "%s" in tpl else tpl


def _footer_notes(primary, measure, lv, lang="hu"):
    notes = []
    if primary.pi_lower is not None:
        notes.append(_ptext("pi_line", lang) % (lv, pi_method_text(primary.pi_method, lang)))
    if measure in E.PROPORTION:
        notes.append(_ptext("prop_axis", lang))
    elif measure == "ZCOR":
        notes.append(_ptext("zcor_axis", lang))
    return notes


def _footer(out, primary, measure, lv, lang="hu", minus="-"):
    footer = [_het_line(out, primary, lang, minus)]
    sg = out.get("subgroups")
    if sg and sg.Q_between is not None:
        footer.append(_sg_diff_line(sg, lang, minus))
    return footer + _footer_notes(primary, measure, lv, lang)


def _summary_keys(out):
    """A forest-összesítések modellkulcsai az ábra sorrendjében (közös hatásúnál a fix elöl)."""
    keys = [key for key in ("ivhet", "random", "fixed") if out.get(key) is not None]
    return keys[::-1] if out["primary_model"] == "fixed" else keys


def _forest_data(out, es, opt, lang="hu", minus="-"):
    """A forest plot rajzolási adatai (v1 plot_data) a kért nyelvű feliratokkal → (data, lábléc)."""
    measure = out["effect_sizes"]["measure"]
    primary = out["primary"]
    nh = E.harmonic_mean(es.ni) if measure == "PFT" else None
    pft_n = opt.get("pft_backtransform") or "harmonic"
    lv = round(opt["level"] * 100)
    summaries = []
    for key in ("ivhet", "random", "fixed"):
        r = out.get(key)
        if r is None:
            continue
        sm = {"label": _summary_label(key, r, lang), "estimate": r.estimate, "ci_lower": r.ci_lower,
              "ci_upper": r.ci_upper, "display": _display(measure, r.estimate, r.ci_lower, r.ci_upper, nh, pft_n, r.se),
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
                "label": _ptext("subtotal", lang) % g.k, "estimate": g.estimate,
                "ci_lower": g.ci_lower, "ci_upper": g.ci_upper,
                "display": g.get("display") or _display(measure, g.estimate, g.ci_lower, g.ci_upper, ng, pft_n, g.se),
                "pi_lower": None, "pi_upper": None,
                "note": _subgroup_note(g, opt.get("model"), minus)}})
    per_study_n = es.ni if measure == "PFT" else None
    if measure == "PFT" and pft_n == "variance":
        # MetaXL: a vizsgálati sor m-je is 1/Var(t) = 1/(4·v_i) = n_i + 0,5
        per_study_n = [1.0 / (4.0 * v) for v in es.vi]
    data = P.forest_data(measure, es.labels, es.yi, es.vi, weights, summaries, opt["level"],
                         per_study_n, sections)
    return data, _footer(out, primary, measure, lv, lang, minus)


def make_plots(out, es, opt=None, lang=None, annotate=None):
    """Forest + funnel SVG és a v1 rajzolási adatok (plot_schema='v1' plot_data; a v2-t a plot_document adja).

    lang: 'hu' | 'en' (alap: az opciók plot_locale-ja); annotate: rétegek és sor-azonosítók az SVG-kben
    (alap: svg_annotate). Az alapértelmezés (hu, annotálatlan) a korábbi kimenettel bájtra azonos."""
    opt = _plot_opt(out, opt)
    lang = P.check_lang(lang or opt.get("plot_locale") or "hu")
    annotate = bool(opt.get("svg_annotate")) if annotate is None else bool(annotate)
    minus = P.minus_for(lang, annotate)
    measure = out["effect_sizes"]["measure"]
    nh = E.harmonic_mean(es.ni) if measure == "PFT" else None
    lv = round(opt["level"] * 100)
    data, footer = _forest_data(out, es, opt, lang, minus)
    row_ids = list(zip(*_row_ids(es))) if annotate else None
    axis_t = P.axis_label(measure, _short(measure, lang), lang)
    svg_forest = P.forest_svg(data, title=opt.get("title"), left_label=opt.get("left_label"),
                              right_label=opt.get("right_label"),
                              effect_label="%s [%d%% CI]" % (_short(measure, lang), lv),
                              footer=footer, axis_title=axis_t, lang=lang, annotate=annotate, row_ids=row_ids)
    tf = out.get("bias", {}).get("trimfill")
    contour = measure not in E.PROPORTION
    svg_funnel = P.funnel_svg(es.yi, es.vi, out["fixed"].estimate, measure,
                              title=_ptext("funnel_contour" if contour else "funnel", lang),
                              filled_yi=tf.filled_yi if tf else None, filled_vi=tf.filled_vi if tf else None,
                              contour=contour, n_harmonic=nh, axis_title=axis_t, lang=lang, annotate=annotate,
                              row_ids=row_ids)
    _v1_extras(data, out, es, axis_t)
    return svg_forest, svg_funnel, data


def _v1_extras(data, out, es, axis_t):
    """A v1 plot_data további kulcsai (tengelycím, nullhatás, heterogenitás, funnel, Doi)."""
    measure = out["effect_sizes"]["measure"]
    primary = out["primary"]
    tf = out.get("bias", {}).get("trimfill")
    contour = measure not in E.PROPORTION
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
    return data


def make_doi_plot(out, es, lang=None, annotate=None):
    """Doi-plot SVG (|Z| a hatás függvényében, LFK-indexszel), vagy None, ha nincs LFK-eredmény."""
    lf = (out.get("bias") or {}).get("lfk")
    if lf is None:
        return None
    opt = _plot_opt(out)
    lang = P.check_lang(lang or opt.get("plot_locale") or "hu")
    annotate = bool(opt.get("svg_annotate")) if annotate is None else bool(annotate)
    measure = out["effect_sizes"]["measure"]
    nh = E.harmonic_mean(es.ni) if measure == "PFT" else None
    return P.doi_svg(lf.points, lf.lfk, lf.category, measure, labels=list(es.labels),
                     title=_ptext("doi", lang), n_harmonic=nh,
                     axis_title=P.axis_label(measure, _short(measure, lang), lang), lang=lang, annotate=annotate,
                     row_ids=list(zip(*_row_ids(es))) if annotate else None)


def _p(p):
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "= –"
    return "< 0.001" if p < 0.001 else "= %.3f" % p


def _short(measure, lang="hu"):
    if lang == "en" and measure in _SHORT_EN:
        return _SHORT_EN[measure]
    return {"SMD": "Hedges g", "COHEN_D": "Cohen d", "MD": "MD", "OR": "OR", "RR": "RR", "RD": "RD",
            "SMD_GLASS": "Glass Δ", "MC": "Átlagos változás", "SMCC": "Standardizált változás",
            "ROM": "Ratio of means", "PR": "Arány", "PLN": "Arány", "PLO": "Arány", "PAS": "Arány",
            "PFT": "Arány", "COR": "r", "ZCOR": "r", "GEN": "Hatás"}.get(measure, measure)


# ------------------------------------------------------------------ szk.ma.plot/v2 (E4a, E4b)
_ANALYSIS_SCALE = {"OR": "log", "RR": "log", "ROM": "log", "PLN": "log", "PLO": "logit", "ZCOR": "atanh",
                   "PAS": "asin_sqrt", "PFT": "pft"}
_COLUMNS = {
    "events1": ({"hu": "Esemény/N (1. kar)", "en": "Events/N (arm 1)"}, ("e1", "n1")),
    "events2": ({"hu": "Esemény/N (2. kar)", "en": "Events/N (arm 2)"}, ("e2", "n2")),
    "mean_sd1": ({"hu": "Átlag (SD) (1. kar)", "en": "Mean (SD) (arm 1)"}, ("m1", "sd1")),
    "n1": ({"hu": "N (1. kar)", "en": "N (arm 1)"}, ("n1",)),
    "mean_sd2": ({"hu": "Átlag (SD) (2. kar)", "en": "Mean (SD) (arm 2)"}, ("m2", "sd2")),
    "n2": ({"hu": "N (2. kar)", "en": "N (arm 2)"}, ("n2",)),
    "events": ({"hu": "Esemény/N", "en": "Events/N"}, ("x", "n")),
    "r": ({"hu": "r", "en": "r"}, ("r",)),
    "n": ({"hu": "N", "en": "N"}, ("n",)),
}
_MEASURE_COLUMNS = [(E.BINARY, ("events1", "events2")), (E.CONTINUOUS, ("mean_sd1", "n1", "mean_sd2", "n2")),
                    (E.PROPORTION, ("events",)), (E.CORRELATION, ("r", "n")), (E.PAIRED, ("n",)), (("GEN",), ("n",))]
# eredet-oszlopok (normalizált fejléc: kisbetű, ékezet, szóköz/kötőjel/aláhúzás nélkül)
_SOURCE_COLUMNS = {
    "doc": ("source", "sourcedoc", "sourcedocument", "forras", "forrasdoc", "forrasdokumentum", "document",
            "dokumentum", "doc", "pdf"),
    "page": ("page", "pages", "sourcepage", "oldal", "forrasoldal", "oldalszam", "pp"),
    "locator": ("locator", "sourcelocator", "lokator", "hely", "forrashely", "table", "tablazat"),
}
_ROB_FLAG = {"low": "low", "some": "some", "high": "high"}
_INFL_DECIMALS = 3


def _same(text):
    return {"hu": text, "en": text} if text else None


def _i18n(fn):
    """{'hu': fn('hu', '-'), 'en': fn('en', U+2212)} — a motor kész szövegei mindkét nyelven."""
    return {"hu": fn("hu", "-"), "en": fn("en", P.MINUS)}


def _p_text(p):
    return None if p is None or (isinstance(p, float) and math.isnan(p)) else _same("p " + _p(p))


def _pct(v):
    return None if v is None or not math.isfinite(v) else _same("%.0f%%" % v)


def _num_cell(v):
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    v = float(v)
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else "%.15g" % v


def _norm_col(name):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(name).strip().lower())
    return re.sub(r"[\s\-_]+", "", "".join(c for c in s if not unicodedata.combining(c)))


def _file_sha256(path):
    import hashlib
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _rob_flag(v):
    import unicodedata
    cat = tableio.rob_category(v)
    if cat == "high":
        t = unicodedata.normalize("NFKD", str(v).lower())
        if "critical" in t or "kritikus" in t:
            return "critical"
    return _ROB_FLAG.get(cat)


def _axis_doc(axis, title, pref, extra=None):
    """Tengely a v2-ben: tartomány és tickek (az SVG-vel azonos számítás), a tick-szöveg a megjelenítési
    nyelven (text) és mindkét nyelven (text_i18n)."""
    ticks = []
    for pos, lab in axis.ticks():
        tx = {"hu": lab, "en": P.num_text(lab, P.MINUS)}
        ticks.append({"at": pos, "text": tx[pref], "text_i18n": tx})
    doc = {"domain": [axis.lo, axis.hi], "ticks": ticks, "title": title}
    doc.update(extra or {})
    return doc


def _value_axis(lo, hi, title, pref, n=4, refs=None):
    """Egyszerű (identitás-skálájú) tengely: kerek tickek [lo, hi]-ban."""
    if not (lo < hi):
        lo, hi = lo - 0.5, hi + 0.5
    ticks = []
    for t in P._nice_ticks(lo, hi, n):
        tx = {"hu": "%g" % t, "en": P.num_text("%g" % t, P.MINUS)}
        ticks.append({"at": t, "text": tx[pref], "text_i18n": tx})
    return {"domain": [lo, hi], "ticks": ticks, "title": title, "refs": refs or []}


def _series_axis(intervals, measure, axis_n, extra_vals, title, pref):
    """LOO / kumulatív sorozat tengelye: mint a forest-tengely (azonos szélesség és tick-szabály)."""
    vals = [v for iv in intervals for v in iv if v is not None and math.isfinite(v)]
    vals += [v for v in extra_vals if v is not None and math.isfinite(v)]
    if not vals:
        return None
    ax = P._Axis(min(vals), max(vals), P.FOREST_X0, P.FOREST_X1, measure in E.RATIO_MEASURES, measure, axis_n)
    return _axis_doc(ax, title, pref)


def _placed(measure, analysis, display, axis_n):
    """PFT: a sor a saját feliratának tengelyhelyén (mint a forest_data); egyébként az elemzési érték."""
    if measure != "PFT" or not axis_n:
        return list(analysis)
    out = []
    for a, d in zip(analysis, display):
        pos = P.forward_transform("PFT", d, axis_n) if d is not None else None
        out.append(a if pos is None else pos)
    return out


def _disp(d):
    d = list(d) if d else [None, None, None]
    return {"est": d[0], "lo": d[1], "hi": d[2]}


def _source_columns(rows):
    found = {}
    for r in rows:
        for col in r:
            nc = _norm_col(col)
            for key, names in _SOURCE_COLUMNS.items():
                if nc in names and key not in found:
                    found[key] = col
    return found


PROVENANCE_SCHEMA = "szk.ma.provenance/v1"
_PROV_MAX_BYTES = 16 * 1024 * 1024


def provenance_path(data_path):
    """03_adatok/<kimenet>.csv → 03_adatok/<kimenet>.prov.json (terv 4.8)."""
    if not data_path:
        return None
    base, ext = os.path.splitext(str(data_path))
    return (base if ext.lower() in (".csv", ".tsv", ".txt") else str(data_path)) + ".prov.json"


def load_provenance(data_path):
    """Az adattábla eredet-oldalfájlja (szk.ma.provenance/v1) dict-ként, vagy None (nincs, olvashatatlan, nem
    ez a séma). A plot/v2 csak a forrás-lokátorokat (doc, page, locator) veszi át belőle — cellaértéket nem."""
    p = provenance_path(data_path)
    if not p or not os.path.isfile(p):
        return None
    try:
        if os.path.getsize(p) > _PROV_MAX_BYTES:
            return None
        with open(p, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != PROVENANCE_SCHEMA or not isinstance(doc.get("cells"), list):
        return None
    return doc


def provenance_sources(prov, measure=None):
    """{row_uid: {doc, page, locator}} az eredet-oldalfájlból: soronként az első olyan cella forrása, amelynek van
    dokumentuma vagy oldala — előbb a mérték kötelező oszlopai (pl. e1, n1, e2, n2) sorrendjében, majd a többi."""
    if not isinstance(prov, dict):
        return {}
    order = list(E.REQUIRED_COLUMNS.get(measure, ())) if measure else []
    best = {}
    for c in prov.get("cells") or ():
        if not isinstance(c, dict) or not isinstance(c.get("row_uid"), str):
            continue
        src = c.get("source") if isinstance(c.get("source"), dict) else {}
        doc = src.get("doc") if isinstance(src.get("doc"), str) and src.get("doc").strip() else None
        page = src.get("page") if isinstance(src.get("page"), int) and not isinstance(src.get("page"), bool) else None
        if doc is None and page is None:
            continue
        loc = src.get("locator") if isinstance(src.get("locator"), str) and src.get("locator").strip() else None
        field = c.get("field")
        rank = order.index(field) if field in order else len(order)
        uid = c["row_uid"]
        if uid not in best or rank < best[uid][0]:
            best[uid] = (rank, {"doc": doc, "page": page, "locator": loc})
    return {uid: v[1] for uid, v in best.items()}


def _source_value(v, key):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, float) and not isinstance(v, bool):
        if not math.isfinite(v):
            return None
        if key == "page" and v.is_integer():
            return int(v)
        return str(v) if isinstance(v, NumText) else _num_cell(v)
    return str(v).strip()


def _cells(measure, row, pref):
    fams = next((cols for meas, cols in _MEASURE_COLUMNS if measure in meas), ())
    minus = P.MINUS if pref == "en" else "-"
    out = {}
    for cid in fams:
        keys = _COLUMNS[cid][1]
        vals = [_num_cell(row.get(k)) for k in keys]
        if any(v is None for v in vals):
            continue
        vals = [P.num_text(v, minus) for v in vals]
        out[cid] = ("%s/%s" % tuple(vals) if cid.startswith("events") else
                    "%s (%s)" % tuple(vals) if cid.startswith("mean_sd") else vals[0])
    return out


def _bias_notes(out, opt, lang):
    b = out.get("bias") or {}
    measure = out["effect_sizes"]["measure"]
    notes = []
    if lang == "hu":
        notes += [b[key] for key in ("note", "binary_note", "smd_note") if b.get(key)]
        return notes
    if b.get("note"):
        notes.append("k = %d < %d: the funnel asymmetry tests are informative only and cannot be interpreted "
                     "(Sterne et al. 2011)." % (b.get("k"), opt["bias_min_k"]))
    if b.get("binary_note"):
        notes.append("For binary outcomes the classic Egger test is informative only (log OR and its standard error "
                     "are not independent, so it is prone to false positives); the Harbord and Peters tests are "
                     "recommended (Sterne et al. 2011). Both are based on log OR%s."
                     % ("" if measure == "OR" else ", even though the analysis used the %s scale" % measure))
    if b.get("smd_note"):
        notes.append("For SMDs the classic Egger test is informative only: the SMD and its standard error are "
                     "correlated, so it can be falsely positive without bias (Pustejovsky & Rodgers 2019; D-S11-005).")
    return notes


def _scale_note_en(measure, pft_n="harmonic"):
    if measure in ("OR", "RR", "ROM"):
        return "The analysis was done on the log scale; reported values are back-transformed (exponentiated)."
    if measure == "PLO":
        return "Analysed on the logit scale; back-transformed to proportions."
    if measure == "PFT":
        if pft_n == "variance":
            return ("Analysed on the Freeman–Tukey scale; back-transformed with Miller's (1978) formula following "
                    "the MetaXL convention (Barendregt et al. 2013): each pooled estimate and its CI with m = 1/Var(t) "
                    "from its own variance; the prediction interval with the harmonic mean n.")
        return "Analysed on the Freeman–Tukey scale; back-transformed with Miller's (1978) formula, harmonic mean n."
    if measure == "ZCOR":
        return "Analysed on the Fisher z scale; back-transformed to r."
    if measure == "PAS":
        return "Analysed on the arcsine scale; back-transformed to proportions."
    if measure == "PLN":
        return "Analysed on the log scale; back-transformed to proportions."
    return None


def _tests_text(out, lang):
    b = out.get("bias") or {}
    if not b.get("performed"):
        return None
    info = {"hu": " (tájékoztató)", "en": " (informative only)"}[lang]
    parts = []
    for key, name in (("harbord", "Harbord"), ("peters", "Peters")):
        if b.get(key) is not None:
            parts.append("%s p %s" % (name, _p(b[key].p)))
    if b.get("egger") is not None:
        parts.append("Egger p %s%s" % (_p(b["egger"].p), info if (b.get("binary_note") or b.get("smd_note")) else ""))
    if b.get("begg") is not None:
        parts.append("Begg p %s" % _p(b["begg"].p))
    tf = b.get("trimfill")
    if tf is not None:
        side = {"hu": {"left": "bal oldal", "right": "jobb oldal"}, "en": {"left": "left side", "right": "right side"}}
        parts.append("trim-and-fill: k0 = %d (%s)" % (tf.k0, side[lang].get(tf.side, tf.side)))
    if not parts:
        return None
    txt = " · ".join(parts)
    if b.get("note"):
        min_k = b.get("min_k_recommended") or DEFAULTS["bias_min_k"]
        txt += {"hu": " — k < %d: csak tájékoztató", "en": " — k < %d: informative only"}[lang] % min_k
    return txt


def _tests_i18n(out):
    """A tölcsér-tesztek szövege {hu, en}, vagy None, ha nincs mit kiírni (k < 3, vagy minden teszt kimaradt)."""
    txt = {lg: _tests_text(out, lg) for lg in P.LANGS}
    return txt if all(isinstance(t, str) for t in txt.values()) else None


def _trimfill_text(out, measure, nh, pft_n, lang, minus):
    tf = (out.get("bias") or {}).get("trimfill")
    if tf is None:
        return None
    a = tf.adjusted
    d = _display(measure, a.estimate, a.ci_lower, a.ci_upper, nh, pft_n, a.se)
    lab = {"hu": "Trim-and-fill (%s): k0 = %d; korrigált becslés %s",
           "en": "Trim-and-fill (%s): k0 = %d; adjusted estimate %s"}[lang]
    return lab % (tf.estimator, tf.k0, P.fmt_triple(d[0], d[1], d[2], minus))


def _mirror_uids(tf, es, uids):
    """A pótolt (trim-and-fill) pontok tükörképének row_uid-ja: címke + variancia + y = 2·θ_vágás − y_i."""
    out = []
    used = set()
    for y, v, lab in zip(tf.filled_yi, tf.filled_vi, tf.get("filled_labels") or [None] * len(tf.filled_yi)):
        base = lab.split(": ", 1)[1] if isinstance(lab, str) and ": " in lab else None
        want = 2 * (tf.get("trim_estimate") or 0.0) - y
        cand = [i for i in range(len(es)) if i not in used and es.vi[i] == v and (base is None or es.labels[i] == base)]
        if not cand:
            out.append(None)
            continue
        i = min(cand, key=lambda j: abs(es.yi[j] - want))
        used.add(i)
        out.append(uids[i])
    return out


def participants_text(out):
    """Az elemzett vizsgálatok résztvevőinek száma kész szövegként {hu, en} (mint a report.md: tagolás nélkül),
    vagy None, ha nem minden elemzett vizsgálatnál ismert."""
    n = (out.get("totals") or {}).get("participants")
    if n is None or isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n):
        return None
    return _same("%d" % n if float(n).is_integer() else "%g" % n)


_ROB_ASSESSED = ("low", "some", "high", "critical")


def rob_high_count(es):
    """Az elemzett sorok közül a magas (vagy kritikus) torzítási kockázatúak száma (a plot/v2 flags.rob szerint);
    None, ha egyetlen elemzett sornak sincs RoB-értékelése (a 0 ilyenkor emberi ítéletet sugallna)."""
    flags = [_rob_flag(r.get("rob")) for r in es.rows]
    if not any(f in _ROB_ASSESSED for f in flags):
        return None
    return sum(1 for f in flags if f in ("high", "critical"))


def rob_missing_count(es):
    """Az elemzett sorok közül azok száma, amelyeknek nincs (értelmezhető) RoB-értékelése."""
    return sum(1 for r in es.rows if _rob_flag(r.get("rob")) not in _ROB_ASSESSED)


def _infl_text(v, minus):
    if isinstance(v, (list, tuple)):
        return "; ".join(_infl_text(x, minus) for x in v)
    if v is None or not math.isfinite(v):
        return "–"
    return P.num_text(P._fmt(v, _INFL_DECIMALS), minus)


def plot_document(out, es, opt=None, run_info=None, data=None, provenance=None):
    """szk.ma.plot/v2 (terv 4.6) — a felület, a figure-forge és a motor-SVG közös nézetmodellje. Minden szám
    és szöveg a motoré: y/lo/hi és estimate/ci_* az elemzési skálán (PFT-nél a forest-tengely n-jével
    elhelyezve, az eredeti FT-értékek az 'analysis' mezőben), display/display_text a megjelenítési skálán,
    a tengelyosztás és a kontúr-poligonok kész. run_info: {'run_id', 'spec_sha256', 'data_sha256'} (a futás-
    leíróból; hiányzó data_sha256 → az input fájl hash-e). data: a make_plots v1 adatai (újraszámolás nélkül).
    provenance: az adattábla eredet-oldalfájlja (szk.ma.provenance/v1; load_provenance) — ebből a studies[].source
    (doc, page, locator; a PDF-oldalig tartó lefúráshoz), ha a táblában nincs forrás-oszlop (az oszlop elsőbbséget
    élvez)."""
    opt = _plot_opt(out, opt)
    pref = P.check_lang(opt.get("plot_locale") or "hu")
    measure = out["effect_sizes"]["measure"]
    primary = out["primary"]
    k = len(es)
    nh = E.harmonic_mean(es.ni) if measure == "PFT" else None
    pft_n = opt.get("pft_backtransform") or "harmonic"
    lv = round(opt["level"] * 100)
    if data is None:
        data = _forest_data(out, es, opt)[0]
    keys = _summary_keys(out)
    uids, tix = _row_ids(es)
    axis, null = P.forest_axis(data)
    axis_n = data.get("axis_n")
    fixed_model = opt.get("model") == "fixed"
    sens = out.get("sensitivity") or {}
    ri = run_info or {}
    axis_title = {lg: P.axis_label(measure, _short(measure, lg), lg) for lg in P.LANGS}

    doc = {"schema": PLOT_SCHEMA_V2}
    doc["meta"] = {"engine_version": __version__, "run_id": ri.get("run_id"),
                   "data_sha256": ri.get("data_sha256") or _file_sha256((out.get("input") or {}).get("path")),
                   "spec_sha256": ri.get("spec_sha256")}
    doc["measure"] = measure
    null_disp = E.back_transform(measure, null, nh) if null is not None else None
    doc["scale"] = {"analysis": _ANALYSIS_SCALE.get(measure, "identity"), "ratio": measure in E.RATIO_MEASURES,
                    "null_analysis": null, "null_display": null_disp}
    if measure == "PFT":
        doc["scale"]["axis_n"] = axis_n
    doc["level"] = opt["level"]
    doc["k"] = k
    doc["display_locale"] = pref
    doc["axis"] = _axis_doc(axis, axis_title, pref)
    doc["labels"] = {"left": _same(opt.get("left_label")), "right": _same(opt.get("right_label")),
                     "title": _same(opt.get("title")),
                     "study": {lg: P.tr("study", lg) for lg in P.LANGS},
                     "effect": {lg: "%s [%d%% CI]" % (_short(measure, lg), lv) for lg in P.LANGS},
                     "weight": {lg: P.tr("weight", lg) for lg in P.LANGS}}
    fams = next((cols for meas, cols in _MEASURE_COLUMNS if measure in meas), ())
    cells = [_cells(measure, r, pref) for r in es.rows]
    doc["columns"] = [{"id": cid, "title": dict(_COLUMNS[cid][0])} for cid in fams if any(cid in c for c in cells)]

    # vizsgálatok
    infl = sens.get("influence") or []
    infl = infl if len(infl) == k else []
    ol = sens.get("outliers")
    ol_idx = set(ol.get("flagged_index") or []) if ol is not None else set()
    w_fixed = list(out["fixed"].weights_pct) if out.get("fixed") is not None else []
    section_of = {}
    for sec in data.get("sections") or []:
        for i in sec["indices"]:
            section_of[i] = sec["title"]
    src_cols = _source_columns(es.rows)
    prov_src = provenance_sources(provenance, measure) if provenance else {}
    studies = []
    for i, s in enumerate(data["studies"]):
        row = es.rows[i] if i < len(es.rows) else {}
        d = s["display"]
        w = s["weight_pct"]
        note = es.notes[i] if i < len(es.notes) else ""
        st = {"row_uid": uids[i], "row_index": tix[i], "study_id": _source_value(row.get("study_id"), "id"),
              "label": s["label"], "section": section_of.get(i), "y": s["y"], "lo": s["lo"], "hi": s["hi"],
              "se": math.sqrt(es.vi[i]), "weight_pct": w,
              "weight_fixed_pct": w_fixed[i] if i < len(w_fixed) else None,
              "weight_text": _same("%.1f%%" % w) if w is not None else None,
              "cells": cells[i], "display": _disp(d), "display_text": P.display_text(*d),
              "clip": {"left": s["lo"] < axis.lo, "right": s["hi"] > axis.hi},
              "flags": {"estimated": tableio.yes_no(row.get("estimated")) == "yes", "rob": _rob_flag(row.get("rob")),
                        "zero_cell_corrected": bool(note and "korrekci" in note),
                        "influential": bool(infl[i]["influential"]) if infl else False,
                        "outlier": bool(infl[i].get("outlier_flag")) if infl else False}}
        st["flags"]["outlier"] = st["flags"]["outlier"] or i in ol_idx
        if s.get("analysis"):
            st["analysis"] = dict(zip(("y", "lo", "hi"), s["analysis"]))
        if src_cols or provenance:
            ps = prov_src.get(uids[i]) or {}
            st["source"] = {key: _source_value(row.get(src_cols[key]), key) if key in src_cols else None
                            for key in ("doc", "page", "locator")}
            for key in ("doc", "page", "locator"):
                if st["source"][key] is None:
                    st["source"][key] = ps.get(key)
        studies.append(st)
    doc["studies"] = studies

    def summary_doc(sm, sid, kind, model, k_, label, p, het, section=None):
        d = sm.get("display") or [sm["estimate"], sm["ci_lower"], sm["ci_upper"]]
        res = {"id": sid, "kind": kind, "model": model, "k": k_, "label": label,
               "estimate": sm["estimate"], "ci_lower": sm["ci_lower"], "ci_upper": sm["ci_upper"],
               "pi_lower": sm.get("pi_lower"), "pi_upper": sm.get("pi_upper"),
               "display": _disp(d), "display_text": P.display_text(*d), "p_text": _p_text(p), "het_text": het}
        if section is not None:
            res["section"] = section
        if sm.get("pi_lower") is not None:
            pd = sm.get("pi_display") or [sm["pi_lower"], sm["pi_upper"]]
            res["pi_display"] = {"lo": pd[0], "hi": pd[1]}
            res["pi_text"] = _i18n(lambda lg, mn: P.fmt_pair(pd[0], pd[1], mn))
        if sm.get("analysis"):
            res["analysis"] = dict(zip(("estimate", "ci_lower", "ci_upper"), sm["analysis"]))
        return res

    # összesítések
    summaries = []
    for sm, key in zip(data.get("summaries") or [], keys):
        r = out[key]
        het = _i18n(lambda lg, mn: ("I² = %.0f%%" % r.I2) if key == "fixed" else
                    "τ² = %s; I² = %.0f%%" % (_g3(r.tau2, mn), r.I2))
        res = summary_doc(sm, "overall_" + key, "overall", key, r.k,
                          {lg: _summary_label(key, r, lg) for lg in P.LANGS}, r.p, het)
        res["primary"] = key == out["primary_model"]
        if r.pi_lower is not None:
            res["pi_label"] = {lg: _ptext("pi_label", lg) % (lv, pi_method_text(r.pi_method, lg)) for lg in P.LANGS}
        summaries.append(res)
    sections = []
    sg = out.get("subgroups")
    if data.get("sections") and sg is not None:
        for sec, g in zip(data["sections"], sg.groups):
            sid = sec["title"]
            sm = sec["summary"]
            het = _i18n(lambda lg, mn: _subgroup_note(g, opt.get("model"), mn)) if g.k > 1 else None
            sdoc = summary_doc(sm, "sub_" + sid, "subgroup", g.get("model") or opt.get("model"), g.k,
                               {lg: _ptext("subtotal", lg) % g.k for lg in P.LANGS}, g.get("p"), het, section=sid)
            summaries.append(sdoc)
            sections.append({"id": sid, "title": sid, "k": g.k, "row_uids": [uids[i] for i in sec["indices"]],
                             "indices": list(sec["indices"]), "row_index": [tix[i] for i in sec["indices"]],
                             "summary": sdoc})
    doc["sections"] = sections
    doc["summaries"] = summaries
    doc["subgroup_test"] = None
    if sg is not None and sg.Q_between is not None:
        doc["subgroup_test"] = {"Q": sg.Q_between, "df": sg.df_between, "p": sg.p_between,
                                "text": _i18n(lambda lg, mn: _sg_diff_line(sg, lg, mn))}
    h = primary.heterogeneity or {}
    tau2_shown = out["primary_model"] in ("random", "ivhet")
    doc["heterogeneity"] = {"Q": primary.Q, "df": primary.Q_df, "p": primary.p_Q, "I2": primary.I2,
                            "I2_from_tau2": h.get("I2_from_tau2"), "tau2": primary.tau2,
                            "text": _i18n(lambda lg, mn: _het_line(out, primary, lg, mn)),
                            "q_text": _i18n(lambda lg, mn: "%s (df = %d)" % (P.num_text("%.2f" % primary.Q, mn),
                                                                           primary.Q_df)),
                            "p_text": _p_text(primary.p_Q), "i2_text": _pct(primary.I2),
                            "tau2_text": _i18n(lambda lg, mn: _g3(primary.tau2, mn) if tau2_shown else "–")}

    # funnel (E4b): pontok, pótolt pontok, kontúr-poligonok, pszeudo-CI — az SVG-vel azonos geometria
    tf = (out.get("bias") or {}).get("trimfill")
    contour = measure not in E.PROPORTION
    geo = P.FunnelGeometry(es.yi, es.vi, out["fixed"].estimate, measure, tf.filled_yi if tf else None,
                           tf.filled_vi if tf else None, contour, nh)
    band = {0.10: "p > 0.10", 0.05: "0.05 < p < 0.10", 0.01: "0.01 < p < 0.05"}
    filled = []
    if tf is not None and tf.filled_yi:
        for y, v, mir in zip(tf.filled_yi, tf.filled_vi, _mirror_uids(tf, es, uids)):
            lab = next((st["label"] for st in studies if st["row_uid"] == mir), None)
            filled.append({"x": y, "se": math.sqrt(v), "mirror_of": mir,
                           "label": {"hu": "pótolt (trim-and-fill)" + (": %s" % lab if lab else ""),
                                     "en": "filled (trim-and-fill)" + (": %s" % lab if lab else "")}})
    doc["funnel"] = {
        "points": [{"row_uid": uids[i], "x": es.yi[i], "se": math.sqrt(es.vi[i])} for i in range(k)],
        "filled": filled, "center": out["fixed"].estimate,
        "center_label": {"hu": "közös hatású becslés", "en": "common-effect estimate"},
        "contour_center": geo.null if geo.contour else None, "se_max": geo.se_max, "pseudo_ci": geo.pseudo_ci(),
        "contours": [{"p": p_, "z": z, "polygon": poly, "band_text": _same(band[p_])} for p_, z, poly in geo.contours()],
        "outside_text": _same("p < 0.01") if geo.contour else None,
        "tests_text": _tests_i18n(out),
        "trimfill_text": _i18n(lambda lg, mn: _trimfill_text(out, measure, nh, pft_n, lg, mn)) if tf else None,
        "axis": _axis_doc(geo.axis, axis_title, pref),
        "y_axis": _value_axis(0.0, geo.se_max, {lg: P.tr("se_axis", lg) for lg in P.LANGS}, pref)}
    if doc["funnel"]["tests_text"] is None:
        del doc["funnel"]["tests_text"]         # a szerződés i18n-objektumot vár (nem null): nincs teszt → nincs kulcs

    # Doi-plot
    lf = (out.get("bias") or {}).get("lfk")
    if lf is not None:          # k < 3: nincs Doi-plot (a kulcs hiányzik)
        dax, maxz = P.doi_axes(lf.points, measure, nh)
        cat = {lg: P.lfk_category(lf.category, lg) for lg in P.LANGS}
        from xml.sax.saxutils import unescape
        doc["doi"] = {"points": [{"row_uid": uids[p_["study_index"]], "x": p_["y"], "abs_z": p_["abs_z"]}
                                 for p_ in lf.points],
                      "lfk": lf.lfk, "category": cat,
                      "lfk_text": _i18n(lambda lg, mn: P.tr("lfk", lg) % (P.num_text(P._fmt(lf.lfk, 2), mn), cat[lg])),
                      "note": {lg: unescape(" ".join(P.TEXTS["doi_note"][lg])) for lg in P.LANGS},
                      "axis": _axis_doc(dax, axis_title, pref),
                      "y_axis": _value_axis(0.0, maxz, {lg: P.tr("abs_z", lg) for lg in P.LANGS}, pref)}

    # érzékenység: LOO, befolyás, kumulatív
    loo = []
    for i, r in enumerate(sens.get("leave_one_out") or []):
        if len(sens["leave_one_out"]) != k:
            break
        d = _display(measure, r["estimate"], r["ci_lower"], r["ci_upper"], nh, pft_n, r.get("se"))
        est, lo, hi = _placed(measure, (r["estimate"], r["ci_lower"], r["ci_upper"]), d, axis_n)
        e = {"omitted_row_uid": uids[i], "label": r["omitted"], "estimate": est, "ci_lower": lo, "ci_upper": hi,
             "display": _disp(d), "display_text": P.display_text(*d), "p_text": _p_text(r.get("p")),
             "i2_text": _pct(r.get("I2")),
             "tau2_text": _i18n(lambda lg, mn: "–" if fixed_model else _g3(r["tau2"], mn))}
        if measure == "PFT" and axis_n:
            e["analysis"] = {"estimate": r["estimate"], "ci_lower": r["ci_lower"], "ci_upper": r["ci_upper"]}
        loo.append(e)
    doc["loo"] = loo
    doc["loo_axis"] = _series_axis([(e["ci_lower"], e["ci_upper"]) for e in loo], measure, axis_n,
                                   [null] + [sm["estimate"] for sm in summaries if sm.get("primary")],
                                   axis_title, pref) if loo else None
    influence = []
    for i, r in enumerate(infl):
        e = {"row_uid": uids[i], "label": r["study"], "influential": bool(r["influential"]),
             "outlier": bool(r.get("outlier_flag")), "decimals": _INFL_DECIMALS}
        for f in ("rstudent", "dffits", "cook_d", "cov_ratio", "hat", "dfbetas", "tau2_del", "Q_del", "weight_pct"):
            e[f] = r.get(f)
            if f in ("rstudent", "dffits", "cook_d", "cov_ratio", "hat", "dfbetas"):
                e[f + "_text"] = _i18n(lambda lg, mn: _infl_text(r.get(f), mn))
        influence.append(e)
    doc["influence"] = influence
    doc["influence_axes"] = doc["influence_text"] = doc["influence_note"] = None
    if influence:
        from .distributions import norm_ppf
        cook_ref = norm_ppf(0.75) ** 2      # χ²₁ medián (metafor: pchisq(cook.d, 1) > 0.5)
        hat_ref = 3.0 / k

        def vax(f, refs, titles):
            vals = [e[f] for e in influence if isinstance(e[f], (int, float)) and math.isfinite(e[f])]
            vals += [rf["at"] for rf in refs] + [0.0]
            return _value_axis(min(vals), max(vals), titles, pref, refs=refs)

        doc["influence_axes"] = {
            "rstudent": vax("rstudent", [{"at": -1.96, "text": _i18n(lambda lg, mn: P.num_text("-1.96", mn))},
                                         {"at": 1.96, "text": _same("1.96")}],
                            {"hu": "Studentizált reziduum (rstudent)", "en": "Studentized residual (rstudent)"}),
            "cook_d": vax("cook_d", [{"at": cook_ref, "text": {"hu": "χ²₁ medián = %.3f" % cook_ref,
                                                               "en": "χ²₁ median = %.3f" % cook_ref}}],
                          {"hu": "Cook-távolság", "en": "Cook's distance"}),
            "hat": vax("hat", [{"at": hat_ref, "text": _same("3/k = %.3f" % hat_ref)}],
                       {"hu": "hat (súly)", "en": "hat (weight)"})}
        names = [e["label"] for e in influence if e["influential"]]
        doc["influence_text"] = {"hu": "Befolyásos vizsgálat (metafor-kritériumok): %s." % (", ".join(names) or "nincs"),
                                 "en": "Influential studies (metafor criteria): %s." % (", ".join(names) or "none")}
        doc["influence_note"] = {
            "hu": "Befolyásos (metafor-kritériumok): |DFFITS| > 3·√(p/(k−p)), a Cook-távolság χ²ₚ-eloszlásbeli alsó "
                  "farokterülete > 50%, hat > 3·p/k, vagy |DFBETAS| > 1; kiugró: |rstudent| > 1.96.",
            "en": "Influential (metafor criteria): |DFFITS| > 3·√(p/(k−p)), lower-tail area of χ²ₚ cut off by Cook's "
                  "distance > 50%, hat > 3·p/k, or |DFBETAS| > 1; outlier: |rstudent| > 1.96."}
    doc["cumulative"] = None
    cum = sens.get("cumulative")
    col = (out.get("columns") or {}).get("cumulative")
    if cum and col and len(cum) == k:
        order = SE.cumulative_order([r.get(col) for r in es.rows])
        entries = []
        for n, (r, i) in enumerate(zip(cum, order)):
            d = _display(measure, r["estimate"], r["ci_lower"], r["ci_upper"], nh, pft_n, r.get("se"))
            est, lo, hi = _placed(measure, (r["estimate"], r["ci_lower"], r["ci_upper"]), d, axis_n)
            key = r.get("key")
            e = {"added_row_uid": uids[i], "label": r["added"],
                 "key_text": "–" if _blank(key) else _level_str(key), "k": r["k"],
                 "estimate": est, "ci_lower": lo, "ci_upper": hi, "display": _disp(d),
                 "display_text": P.display_text(*d),
                 "i2_text": _pct(r.get("I2")) if r["k"] > 1 else _same("–"),
                 "tau2_text": _i18n(lambda lg, mn: _g3(r["tau2"], mn) if r["k"] > 1 and not fixed_model else "–")}
            if measure == "PFT" and axis_n:
                e["analysis"] = {"estimate": r["estimate"], "ci_lower": r["ci_lower"], "ci_upper": r["ci_upper"]}
            e["row_index"] = tix[i]         # E4c: a hozzáadott vizsgálat táblabeli sora (lefúrás, data-row)
            entries.append(e)
        lab = (out.get("column_labels") or {}).get("cumulative") or opt.get("cumulative")
        doc["cumulative"] = {"key_label": _same(lab), "entries": entries,
                             "axis": _series_axis([(e["ci_lower"], e["ci_upper"]) for e in entries], measure, axis_n,
                                                  [null], axis_title, pref)}
        doc["cumulative"].update(_cumulative_extras([r.get(col) for r in es.rows], col, lab, entries))
    doc["bubble"] = _bubble_doc(out, es, studies, uids, tix, axis_title, pref, null, nh)
    notes = []
    sn = (out.get("back_transformed") or {}).get("scale_note")
    if sn:
        notes.append({"hu": sn, "en": _scale_note_en(measure, pft_n) or sn})
    for hu_line, en_line in zip(_footer_notes(primary, measure, lv, "hu"), _footer_notes(primary, measure, lv, "en")):
        notes.append({"hu": hu_line, "en": en_line})
    for hu_n, en_n in zip(_bias_notes(out, opt, "hu"), _bias_notes(out, opt, "en")):
        notes.append({"hu": hu_n, "en": en_n})
    doc["notes"] = notes
    return doc


# ------------------------------------------------------------------ E4c: kumulatív sorrend, buborékábra, motor-SVG
BUBBLE_GRID_N = 51      # a buborékábra rácsa: ennyi egyenközű pont a moderátor megfigyelt [min; max] tartományán


def _cumulative_extras(keys, col, lab, entries):
    """A kumulatív blokk kiegészítései: a rendezés leírása (oszlop, irány, rendezés típusa, hiányzó kulcsok) és
    kezdőbarát magyarázat (a sensitivity.cumulative_order szabályai szerint)."""
    present = [kk for kk in keys if not _blank(kk)]
    n_missing = len(keys) - len(present)
    is_num = [isinstance(kk, (int, float)) and not isinstance(kk, bool) for kk in present]
    numeric = all(is_num)
    mixed = any(is_num) and not numeric
    name = lab or col
    hu = "Sorrend: a(z) „%s” oszlop szerint növekvő (%s)" % (name, "számként" if numeric else
                                                            "szövegként, természetes rendezéssel")
    en = "Order: ascending by '%s' (%s)" % (name, "numeric" if numeric else "as text, natural sort")
    if n_missing:
        hu += "; %d vizsgálatnál hiányzik a rendezőkulcs, ezek a sor végére kerültek" % n_missing
        en += "; %d stud%s without a sorting key placed last" % (n_missing, "y" if n_missing == 1 else "ies")
    if mixed:
        hu += "; az oszlop vegyesen tartalmaz számot és szöveget — ellenőrizd a sorrendet"
        en += "; the column mixes numbers and text — check the order"
    order = {"column": col, "label": _same(name), "direction": "ascending", "sort": "numeric" if numeric else "natural",
             "missing_last": True, "n_missing": n_missing, "mixed_types": mixed,
             "row_uids": [e["added_row_uid"] for e in entries], "text": {"hu": hu + ".", "en": en + "."}}
    note = {"hu": "Kumulatív metaanalízis: minden sor az addig (a rendezőkulcs szerint) bevont vizsgálatok együttes "
                  "becslése, így látszik, hogyan változott a becslés és a bizonytalansága, ahogy újabb vizsgálatok "
                  "jelentek meg. Az utolsó sor (gyémánt) a teljes elemzés. A sorok nem független eredmények "
                  "(ugyanazokat az adatokat elemzik újra), ezért a „mikortól szignifikáns” kérdésre nem adnak "
                  "megbízható választ.",
            "en": "Cumulative meta-analysis: each row pools the studies included up to that point (in the order of "
                  "the sorting key), showing how the estimate and its uncertainty changed as new studies appeared. "
                  "The last row (diamond) is the full analysis. The rows are not independent results (the same data "
                  "are re-analysed), so they cannot reliably answer 'from when was it significant'."}
    return {"order": order, "note": note}


def bubble_moderator(out, es=None):
    """(oszlopkulcs, címke), ha a meta-regresszió pontosan egy folytonos (numerikus) moderátorral futott (a kimaradt,
    állandó moderátorok nélkül; kategóriás moderátor dummy-kódolt — együtthatója 'név=szint' —, ezért nem ilyen);
    különben None. es nélkül (pl. a riportból) az együtthatók neve dönt; es-sel az értékek végességét is nézi."""
    mr = out.get("metaregression")
    if mr is None or mr.get("_vcov") is None:
        return None
    coefs = mr.get("coefficients") or []
    if len(coefs) != 2 or coefs[0].get("name") != "intercept":
        return None
    keys = (out.get("columns") or {}).get("moderators") or []
    labs = (out.get("column_labels") or {}).get("moderators") or []
    dropped = set(out.get("metaregression_dropped") or [])
    mods = [(key, lab) for key, lab in zip(keys, labs) if lab not in dropped]
    if len(mods) != 1 or coefs[1].get("name") != mods[0][1]:
        return None
    if es is None:
        return mods[0]
    vals = [r.get(mods[0][0]) for r in es.rows]
    if not vals or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals):
        return None
    return mods[0]


def _bubble_doc(out, es, studies, uids, tix, axis_title, pref, null, nh):
    """A v2 'bubble' blokkja (E4c) egy folytonos moderátoros meta-regresszióhoz, különben None. Pontok: x = moderátor,
    y = hatás az elemzési skálán, weight_pct = a meta-regresszió súlya (1/(v_i + τ²), %-ban; a buborék területe);
    line / band / pi_band: [[x, …]] a moderátor megfigyelt tartományának egyenközű rácsán, a moderators.predict
    (= metafor predict(rma(yi, vi, mods = ~x), newmods = …)) értékei: CI = ŷ ± krit·√(x0ᵀVx0), PI = ŷ ±
    krit·√(x0ᵀVx0 + τ²), V a próba szerinti együttható-kovariancia (vcov), krit t(k − p) (knha) vagy z."""
    mod = bubble_moderator(out, es)
    if mod is None:
        return None
    key, lab = mod
    mr = out["metaregression"]
    measure = out["effect_sizes"]["measure"]
    k = len(es)
    xs = [float(r.get(key)) for r in es.rows]
    tau2 = mr.tau2 or 0.0
    w = [1.0 / (v + tau2) for v in es.vi]
    sw = sum(w)
    wp = [100.0 * a / sw for a in w]
    lo, hi = min(xs), max(xs)
    n = BUBBLE_GRID_N
    grid = [lo + (hi - lo) * j / (n - 1.0) for j in range(n)]
    grid[-1] = hi
    pred = MO.predict(mr, [[1.0, g] for g in grid])
    level = mr.get("level") or 0.95
    lv = int(round(level * 100))
    crit = MO.prediction_crit(mr, level)
    df = mr.k - mr.p
    points = []
    for i in range(k):
        st = studies[i] if i < len(studies) else {}
        fl = st.get("flags") or {}
        xt = _num_cell(xs[i])
        points.append({"row_uid": uids[i], "row_index": tix[i], "label": es.labels[i], "x": xs[i], "y": es.yi[i],
                       "se": math.sqrt(es.vi[i]), "weight_pct": wp[i], "weight_text": _same("%.1f%%" % wp[i]),
                       "x_text": {"hu": xt, "en": P.num_text(xt, P.MINUS)},
                       "display": st.get("display"), "display_text": st.get("display_text"),
                       "flags": {"estimated": bool(fl.get("estimated")), "rob": fl.get("rob")}})
    band = [[g, p_["ci_lower"], p_["ci_upper"]] for g, p_ in zip(grid, pred)]
    x0, x1, y0, y1 = P.BUBBLE_FRAME
    xax = P._Axis(lo, hi, x0, x1)
    yvals = list(es.yi) + [v for b_ in band for v in b_[1:]] + ([null] if null is not None else [])
    yax = P._Axis(min(yvals), max(yvals), 0, y1 - y0, measure in E.RATIO_MEASURES, measure, nh)
    c0, c1 = mr.coefficients
    test_txt = "t(%d)" % df if mr.test == "knha" else "z"
    ratio = None
    if measure in E.RATIO_MEASURES:
        try:
            ratio = [math.exp(c1[f]) for f in ("estimate", "ci_lower", "ci_upper")]
        except (OverflowError, TypeError, ValueError):
            ratio = None

    def coef_text(lg, mn):
        s = {"hu": "Meredekség (%s): %s / egység; p %s (%s); maradék τ² = %s; R² = %s",
             "en": "Slope (%s): %s per unit; p %s (%s); residual τ² = %s; R² = %s"}[lg] % (
            lab, P.fmt_triple(c1["estimate"], c1["ci_lower"], c1["ci_upper"], mn), _p(c1.get("p")), test_txt,
            _g3(tau2, mn), "–" if mr.get("R2") is None else "%.0f%%" % mr.R2)
        if ratio is not None and all(math.isfinite(v) for v in ratio):
            s += {"hu": "; %s-szorzó / egység: %s", "en": "; %s ratio per unit: %s"}[lg] % (
                measure, P.fmt_triple(ratio[0], ratio[1], ratio[2], mn))
        return s

    rng = {lg: "%s–%s" % (P.num_text(_num_cell(lo), mn), P.num_text(_num_cell(hi), mn))
           for lg, mn in (("hu", "-"), ("en", P.MINUS))}
    note = {"hu": "Buborékábra: minden kör egy vizsgálat — vízszintesen a moderátor (%s) értéke, függőlegesen a "
                  "hatásméret; a kör területe a vizsgálat meta-regressziós súlyával arányos. A folytonos vonal a modell "
                  "szerinti átlagos hatás a moderátor függvényében; a kitöltött sáv ennek %d%%-os konfidenciasávja (az "
                  "együtthatók kovarianciájából), a szaggatott vonalak a %d%%-os predikciós sáv: ide esne egy új, hasonló "
                  "vizsgálat valódi hatása. A vonal csak a megfigyelt tartományban (%s) értelmezhető. A meta-regresszió "
                  "vizsgálatok közötti, megfigyelési összefüggés: nem bizonyít ok-okozatot (ökológiai torzítás), és "
                  "kevés vizsgálatnál (ökölszabály: legalább 10 vizsgálat moderátoronként) bizonytalan."
                  % (lab, lv, lv, rng["hu"]),
            "en": "Bubble plot: each circle is a study — horizontally the moderator (%s), vertically the effect size; "
                  "the circle's area is proportional to the study's meta-regression weight. The solid line is the "
                  "model's mean effect as a function of the moderator; the shaded band is its %d%% confidence band "
                  "(from the coefficient covariance), the dashed lines the %d%% prediction band: where the true effect "
                  "of a new, similar study would be expected. The line is interpretable only within the observed range "
                  "(%s). Meta-regression is an observational, between-study association: it does not prove causation "
                  "(ecological bias) and is uncertain with few studies (rule of thumb: at least 10 studies per "
                  "moderator)." % (lab, lv, lv, rng["en"])}
    if k < 10:
        note["hu"] += " Itt k = %d < 10, ezért az összefüggés különösen bizonytalan." % k
        note["en"] += " Here k = %d < 10, so the association is particularly uncertain." % k
    if mr.get("robust") is not None:
        note["hu"] += " A sávok a modell-alapú kovarianciából készültek (a robusztus SE-k az eredménytáblában)."
        note["en"] += " The bands use the model-based covariance (the robust SEs are in the results table)."
    return {
        "moderator": {"name": key, "label": _same(lab), "type": "continuous", "coefficient": lab},
        "model": {"k": mr.k, "p": mr.p, "tau2": tau2, "tau2_method": mr.tau2_method, "test": mr.test,
                  "df": df if mr.test == "knha" else None, "crit": crit, "level": level, "R2": mr.get("R2"),
                  "QM": mr.get("QM"), "QM_p": mr.get("QM_p")},
        "coefficients": [{"name": c["name"], "estimate": c["estimate"], "se": c["se"], "ci_lower": c["ci_lower"],
                          "ci_upper": c["ci_upper"], "p": c.get("p")} for c in (c0, c1)],
        "vcov": [list(row) for row in mr._vcov],
        "points": points,
        "line": [[g, p_["pred"]] for g, p_ in zip(grid, pred)],
        "band": band,
        "pi_band": [[g, p_["pi_lower"], p_["pi_upper"]] for g, p_ in zip(grid, pred)],
        "grid_n": n, "x_range": [lo, hi],
        "x_axis": _axis_doc(xax, _same(lab), pref),
        "y_axis": _axis_doc(yax, axis_title, pref, {"refs": [{"at": null, "text": None}] if null is not None else []}),
        "coef_text": _i18n(coef_text),
        "line_label": {"hu": "Illesztett meta-regressziós egyenes", "en": "Fitted meta-regression line"},
        "band_label": {"hu": "%d%%-os konfidenciasáv" % lv, "en": "%d%% confidence band" % lv},
        "pi_band_label": {"hu": "%d%%-os predikciós sáv" % lv, "en": "%d%% prediction band" % lv},
        "note": note,
    }


FIGURE_KINDS = ("cumulative", "loo", "bubble")
EXTRA_PLOT_FILES = ("cumulative.svg", "bubble.svg")
_RUN_FILE_KINDS = ("forest", "funnel", "doi")
_FIGURE_TITLES = {"cumulative": {"hu": "Kumulatív metaanalízis", "en": "Cumulative meta-analysis"},
                  "loo": {"hu": "Leave-one-out érzékenységi elemzés", "en": "Leave-one-out sensitivity analysis"},
                  "bubble": {"hu": "Buborékábra (meta-regresszió)", "en": "Bubble plot (meta-regression)"}}


def render_figure(plot, kind, lang="hu", annotate=False):
    """A motor SVG-je egy szk.ma.plot/v2 dokumentum kész blokkjából (E4c; a munkapad ábra-exportja és a
    write_outputs ugyanezt hívja): {'svg', 'lang', 'kind'}. kind: 'cumulative' | 'loo' | 'bubble'. A forest, a
    funnel és a Doi-plot a futás saját SVG-je (make_plots / make_doi_plot) — ezekre None (a hívó a futás fájljára
    vált). Hiányzó blokk, ismeretlen fajta vagy nem v2 dokumentum → ValueError (magyar üzenet)."""
    lang = P.check_lang(lang or "hu")
    if kind in _RUN_FILE_KINDS:
        return None
    if kind not in FIGURE_KINDS:
        raise ValueError("ismeretlen ábrafajta: %r (a dokumentumból rajzolható: %s)" % (kind, ", ".join(FIGURE_KINDS)))
    if not isinstance(plot, dict) or plot.get("schema") != PLOT_SCHEMA_V2:
        raise ValueError("a %s ábrához szk.ma.plot/v2 dokumentum kell (plot_data.json)" % kind)
    null = (plot.get("scale") or {}).get("null_analysis")
    null = null if isinstance(null, (int, float)) and not isinstance(null, bool) else None
    effect = P._pick((plot.get("labels") or {}).get("effect"), lang) or None
    title = _FIGURE_TITLES[kind][lang]
    annotate = bool(annotate)
    if kind == "cumulative":
        cum = plot.get("cumulative")
        ents = (cum or {}).get("entries") or []
        if not ents:
            raise ValueError("ebben a futásban nincs kumulatív elemzés (adj meg rendező oszlopot: --cumulative)")
        footer = [P._pick(t, lang) for t in ((cum.get("order") or {}).get("text"), cum.get("note")) if t]
        svg = P.series_svg(ents, cum.get("axis") or plot.get("axis"), "cumulative", cum.get("key_label"), effect, null,
                           ents[-1].get("estimate"), title, footer, lang, annotate)
    elif kind == "loo":
        loo = plot.get("loo") or []
        if not loo:
            raise ValueError("ebben a futásban nincs leave-one-out elemzés (legalább 3 vizsgálat kell)")
        ref = next((s.get("estimate") for s in plot.get("summaries") or [] if s.get("primary")), None)
        rix = {s.get("row_uid"): s.get("row_index") for s in plot.get("studies") or []}
        svg = P.series_svg(loo, plot.get("loo_axis") or plot.get("axis"), "loo", None, effect, null, ref, title,
                           None, lang, annotate, row_index=rix)
    else:
        bub = plot.get("bubble")
        if not bub or not bub.get("points"):
            raise ValueError("ebben a futásban nincs buborékábra (egyetlen folytonos moderátoros meta-regresszió kell: "
                             "--moderators <oszlop>)")
        svg = P.bubble_svg(bub, null, title, lang, annotate)
    return {"svg": svg, "lang": lang, "kind": kind}


def make_extra_plots(out, es, doc=None, lang=None, annotate=None, run_info=None, provenance=None):
    """Az E4c motor-SVG-k fájlnév szerint: {'cumulative.svg': …, 'bubble.svg': …} — csak a ténylegesen futtatott
    elemzésekre (kumulatív elemzés; egyetlen folytonos moderátoros meta-regresszió); egyébként üres dict (a
    kimenet így bájtra azonos a korábbival). doc: a futás v2 dokumentuma (újraszámolás nélkül); lang / annotate:
    alapból a plot_locale / svg_annotate opció."""
    has_cum = bool((out.get("sensitivity") or {}).get("cumulative"))
    if out.get("primary") is None or not (has_cum or bubble_moderator(out, es) is not None):
        return {}
    opt = _plot_opt(out)
    lang = P.check_lang(lang or opt.get("plot_locale") or "hu")
    annotate = bool(opt.get("svg_annotate")) if annotate is None else bool(annotate)
    if not isinstance(doc, dict) or doc.get("schema") != PLOT_SCHEMA_V2:
        doc = plot_document(out, es, run_info=run_info, provenance=provenance)
    res = {}
    for kind, name in (("cumulative", "cumulative.svg"), ("bubble", "bubble.svg")):
        if doc.get(kind):
            res[name] = render_figure(doc, kind, lang, annotate)["svg"]
    return res


def plot_data(out, es, opt=None, run_info=None, data=None, provenance=None):
    """A plot_data.json tartalma a plot_schema opció szerint: 'v2' (alapértelmezés) vagy 'v1'."""
    o = _plot_opt(out, opt)
    if o.get("plot_schema") == "v1":
        return data if data is not None else make_plots(out, es, o)[2]
    return plot_document(out, es, o, run_info, data, provenance)


PLOT_FILES = ("forest.svg", "funnel.svg", "doi.svg", "plot_data.json")


_AUTO = object()


def write_outputs(out, es, outdir, report_md=None, plots=True, run_info=None, provenance=_AUTO):
    """A kimenetek írása. Egy korábbi futás ábrafájljai (PLOT_FILES), amelyeket ez a futás nem ír újra
    (--no-plots, k = 0, k < 3 → nincs doi.svg), törlődnek, hogy a mappa ne keverjen két elemzést.
    A plot_data.json a plot_schema opció szerint v2 (alapértelmezés) vagy v1; run_info: lásd plot_document.
    provenance: az eredet-oldalfájl (dict vagy None); alapból a beolvasott adatfájl mellől (load_provenance)."""
    if provenance is _AUTO:
        provenance = load_provenance((out.get("input") or {}).get("path"))
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
        pdoc = plot_data(out, es, run_info=run_info, data=data, provenance=provenance)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(to_jsonable(pdoc), fh, ensure_ascii=False, indent=1)
        paths["plot_data.json"] = p
        # E4c: kumulatív és buborék-SVG csak a ténylegesen futtatott elemzésekhez (különben nincs új fájl)
        for name, content in sorted(make_extra_plots(out, es, pdoc, run_info=run_info, provenance=provenance).items()):
            p = os.path.join(outdir, name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(content)
            paths[name] = p
    for name in PLOT_FILES + EXTRA_PLOT_FILES:
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
