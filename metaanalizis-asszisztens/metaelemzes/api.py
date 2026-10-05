# -*- coding: utf-8 -*-
"""Motor-homlokzat (E1, terv 3.2): a parancssor, az ágensek és a MA-munkapad (ma_gui) közös belépési pontja.

Minden függvény ugyanazt a JSON-képes dict-et / listát adja, mint a megfelelő CLI-parancs JSON-kimenete
(a teszt bájtra összeveti):

    capabilities()                         ma.py --capabilities                (szk.capabilities/v1)
    rules_export()                         ma.py rules export --json           (V/P/X-szabályok)
    validate_file(path, measure, options)  ma.py validate --json               (szk.ma.validation/v1 + 'k')
    validate_request(req)                  ma.py validate --request-json       (szk.ma.validation/v1)
    convert_cli(kind, params)              ma.py convert <kind> …
    prisma_check(flow, template=None)      ma.py prisma check --json F --out-format json
    project_audit(dir, stage=None)         ma.py project audit <dir> --json    (szk.ma.project-audit/v1)
    project_status / project_list / project_show / project_export_json / project_activity
                                           ma.py project status|list|show|export --format json|activity --json
    kb_search / kb_show / kb_rules / kb_checklist
                                           ma.py kb search|show|rules|checklist --json

Továbbá: engine_info() (verzió, önteszt, mértékek, opció- és szabály-metaadat), read_table / write_table
(formátumtartó nyers tábla; a meta column_map-jével), column_map, validate_table (nyers cellák →
szk.ma.validation/v1; project_dir-rel az 'acknowledged' a „Nem hiba” döntésekből), analyze (spec → nézetmodell:
eredmények, szk.ma.plot/v2, szk.ma.run/v1; explore: semmit nem ír, commit: kimenetek + run.json + projektnapló),
convert (szk.ma.convert-request/v1 → szk.ma.convert-result/v1), kb_rules_for_field, a spec-segédek és a projektnapló
vékony burka. A számok kizárólag a motorban keletkeznek; a JSON-ban NaN/Infinity helyett null áll.
"""
import collections
import datetime
import glob
import hashlib
import json
import os
import platform
import re
import sys
import threading
import time

from . import __version__
from . import activity as _activity
from . import projekt as _projekt
from .activity import (ActivityError, ActivityLog, FORBIDDEN_KEYS, LOG_RELPATH, append as activity_append,  # noqa: F401
                       canonical_json, find_forbidden_key, head as activity_head, read as activity_read,
                       verify as activity_verify)
from .activity import SCHEMA as ACTIVITY_SCHEMA  # noqa: F401
from .projekt import (CONVENTIONS, DATA_CLASSES, PROJECT_META, REVIEW_TYPES, JournalRev, check_actor,  # noqa: F401
                      journal_marks, load_project_meta, save_project_meta, validate_project_meta)
# a projektnapló szókincse (a felület legördülői és sémái ugyanezt használják; ma_gui csak az api-n át importál)
from .projekt import KNOWN_AGENTS, RESOLVE_STATUSES, SEVERITIES, VERDICTS  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_INFO_SCHEMA = "szk.ma.engine-info/v1"
CAPABILITIES_SCHEMA = "szk.capabilities/v1"
ANALYSIS_RESULT_SCHEMA = "szk.ma.analysis-result/v1"
CONVERT_REQUEST_SCHEMA = "szk.ma.convert-request/v1"
CONVERT_RESULT_SCHEMA = "szk.ma.convert-result/v1"
KB_RULES_SCHEMA = "szk.ma.kb-rules/v1"
ACTIVITY_REPORT_SCHEMA = "szk.ma.activity-verify/v1"
MODES = ("explore", "commit")


def _jsonable(obj):
    from .pipeline import to_jsonable
    return to_jsonable(obj)


# ------------------------------------------------------------------ szabályok
# a szabálycímek angolul (a magyar a motor RULES-ából jön; a teszt őrzi, hogy minden szabálynak legyen)
RULE_TITLES_EN = {
    "V001": "Missing required column", "V002": "Missing required value", "V003": "Non-numeric value",
    "V004": "Invalid sample size", "V005": "Non-positive SD", "V006": "Invalid event count",
    "V007": "Duplicate study identifier", "V008": "Double-zero events", "V009": "Zero cell",
    "V010": "Invalid correlation", "V011": "Suspected SE reported as SD", "V012": "Suspected unit mismatch",
    "V013": "Suspected skewness", "V014": "Extreme effect size", "V015": "Few studies",
    "V016": "Publication-bias test underpowered", "V017": "Shared control arm used repeatedly",
    "V018": "Estimated (imputed) values", "V019": "Studies at high risk of bias", "V020": "Very small study",
    "V021": "Non-numeric value in optional column", "V022": "Row excluded from effect-size calculation",
    "V023": "Ambiguous number format", "V024": "Shifted row", "V025": "Text cell that looks like a formula",
    "V026": "No analysable study", "V027": "Unrecognised category value", "V028": "Duplicate column",
    "V029": "Missing study label", "V030": "Numerically unmanageable variance",
    "P001": "Invalid PRISMA count", "P002": "Number of screened records does not match",
    "P003": "Number of reports sought for retrieval does not match",
    "P004": "Number of reports assessed does not match", "P005": "Number of included reports does not match",
    "P006": "More included studies than included reports", "P007": "Exclusion reasons do not add up",
    "P008": "Missing exclusion reasons", "P009": "Other methods branch: number of reports assessed does not match",
    "P010": "More studies in the meta-analysis than included", "P011": "Derived (unreported) box value",
    "P012": "Relationship cannot be checked", "P013": "Screening not completed",
    "P014": "Fewer included studies than assessed minus excluded (2009 template)",
    "P015": "Updated review: totals do not match", "P016": "Other methods branch: more sought than identified",
    "P017": "Sources disagree on the counts",
    "X001": "Stale commit run: the data table changed after the run",
    "X003": "Table rob value differs from the final appraisal judgement",
    "X005": "Estimated rows present but no sensitivity run without them",
    "X006": "High-RoB rows present but no sensitivity run without them",
    "X010": "Analysed cell has no source page",
    "X013": "Row estimated flag contradicts cell provenance",
    "X014": "More analysed studies than included studies (I)",
    "X016": "Protocol deviation without decision: primary analysis is not the prespecified one",
    "X022": "Provenance sidecar does not belong to the current data table",
}
_RULE_KINDS = {"validate": "validation", "prisma": "prisma", "audit": "audit"}


def rules_export():
    """A motor összes szabálya (V: validate, P: prisma, X: audit) metaadattal — `ma.py rules export --json`.
    Elemenként: id, kind, module, severity, stage, title {hu, en}, advice, source, kb_refs, escalation,
    machine_check. A KB decision_rule táblájába ugyanezek kerülnek (kb build)."""
    from . import kb
    from .audit import ESCALATION, KB_REFS
    meta = kb._engine_rule_meta()
    rules = kb._engine_rules()
    out = []
    for code in sorted(rules, key=lambda c: ("VPX".index(c[0]) if c[0] in "VPX" else 3, c)):
        sev, title, advice, source = rules[code]
        stage, mod = meta[code]
        esc = ESCALATION.get(code)
        out.append(collections.OrderedDict((
            ("id", code), ("kind", _RULE_KINDS.get(mod, mod)), ("module", mod), ("severity", sev), ("stage", stage),
            ("title", {"hu": title, "en": RULE_TITLES_EN.get(code, title)}), ("advice", advice),
            ("source", source), ("kb_refs", list(KB_REFS.get(code, ())) if mod == "audit" else []),
            ("escalation", {"from": esc, "before": "warning"} if esc else None),
            ("machine_check", "metaelemzes.%s:%s" % (mod, code)))))
    return _jsonable(out)


# ------------------------------------------------------------------ mértékek, opciók
MEASURE_LABELS_EN = {
    "MD": "Mean difference (MD)", "SMD": "Standardised mean difference (Hedges g)",
    "COHEN_D": "Standardised mean difference (Cohen d, uncorrected)", "ROM": "Ratio of means",
    "SMD_GLASS": "Standardised mean difference (Glass Δ, control SD)", "MC": "Mean change (paired, raw)",
    "SMCC": "Standardised mean change (change-score SD)", "OR": "Odds ratio (OR)", "RR": "Risk ratio (RR)",
    "RD": "Risk difference (RD)", "PR": "Proportion", "PLN": "Proportion (log)", "PLO": "Proportion (logit)",
    "PAS": "Proportion (arcsine)", "PFT": "Proportion (Freeman–Tukey)", "COR": "Correlation (r)",
    "ZCOR": "Correlation (Fisher z → r)", "GEN": "Generic effect size",
}
_FAMILIES = (("CONTINUOUS", "continuous"), ("PAIRED", "paired"), ("BINARY", "binary"),
             ("PROPORTION", "proportion"), ("CORRELATION", "correlation"), ("GENERIC", "generic"))


def measures():
    """A mértékek: id, label {hu, en}, family, required_columns, scale (elemzési skála), ratio."""
    from . import effect_sizes as E
    from .pipeline import _ANALYSIS_SCALE
    family = {}
    for attr, name in _FAMILIES:
        for m in getattr(E, attr):
            family.setdefault(m, name)
    return [{"id": m, "label": {"hu": E.MEASURE_LABELS.get(m, m), "en": MEASURE_LABELS_EN.get(m, m)},
             "family": family.get(m), "required_columns": list(E.REQUIRED_COLUMNS.get(m, ())),
             "scale": _ANALYSIS_SCALE.get(m, "identity"), "ratio": m in E.RATIO_MEASURES}
            for m in E.ALL_MEASURES]


# Az elemzési opciók csoportja az űrlapon (UX-11): az alapbeállítások elöl, a bináris adat és az ábra-feliratok
# külön; a replikációs / varianciaváltozat- és kimenet-kapcsolók (advanced) összecsukva. Ismeretlen kulcs: advanced.
OPTION_GROUPS = collections.OrderedDict((
    ("basic", ("measure", "model", "tau2", "ci", "pi", "level", "subgroup", "subgroup_prespecified", "moderators",
               "cumulative", "outliers")),
    ("binary", ("cc", "cc_to", "drop00", "mh", "peto", "rd_var")),
    ("plot", ("title", "left_label", "right_label", "label_col", "plot_locale")),
    ("advanced", ("smd_vtype", "j_method", "md_vtype", "glass_vtype", "gen_smd_vtype", "pft_backtransform", "h_centre",
                  "common_tau2", "metareg_test", "metareg_robust", "metareg_tau2", "trimfill_estimator",
                  "trimfill_trim_model", "egger_ci_dist", "begg_method", "begg_continuity", "bias_min_k")),
    ("output", ("plot_schema", "svg_annotate")),
))
ADVANCED_GROUPS = ("advanced", "output")
# A felület súgója ott, ahol az argparse-súgó parancssori kapcsolóra vagy a parancssori súgó más részére utal
# („lásd lent”, „--spec”): a felhasználó a munkapadon nem lát kapcsolókat (UX-11). A „help” a CLI-é marad.
GUI_HELP = {
    "measure": {"hu": "Hatásméret: milyen mérőszámmal hasonlítjuk össze a csoportokat (bináris kimenetnél pl. RR, OR, RD; "
                      "folytonosnál MD, SMD). Ettől függ, mely oszlopok kellenek az adattáblában.",
                "en": "Effect measure: how the groups are compared (binary outcomes: e.g. RR, OR, RD; continuous: MD, SMD). "
                      "It determines which columns the data table needs."},
    "metareg_tau2": {"hu": "A meta-regresszió τ²-becslője (alap: a fenti τ²-becslő, ha ott értelmezett, különben REML); "
                           "FE = inverz-variancia súlyok, τ² = 0.",
                     "en": "The τ² estimator of the meta-regression (default: the τ² estimator above if defined there, "
                           "otherwise REML); FE = inverse-variance weights, τ² = 0."},
    "metareg_robust": {"hu": "Meta-regresszió robusztus (HC1 szendvics) standard hibákkal, t(k−p)-vel és robusztus "
                             "F-próbával. Moderátor(ok) kellenek hozzá.",
                       "en": "Meta-regression with robust (HC1 sandwich) standard errors, t(k−p) and a robust F test. "
                             "Needs moderator(s)."},
}


def _option_group(key):
    for g, keys in OPTION_GROUPS.items():
        if key in keys:
            return g
    return "advanced"


def option_metadata():
    """Az elemzési opciók metaadata az argparse-ból és a pipeline.DEFAULTS-ból GENERÁLVA (spec.option_table):
    kulcs → {default, type, choices, cli (fő kapcsoló), flags, dest, kind, nullable, required, help, group,
    advanced, gui_help?}. A group/advanced az űrlap tagolása (alap, bináris, ábra, haladó, kimenet); a gui_help
    {hu, en} a felület súgója ott, ahol a CLI-súgó parancssori kapcsolóra hivatkozik."""
    from . import spec as S
    out = collections.OrderedDict()
    for r in S.option_table():
        g = _option_group(r["key"])
        d = {"default": r["default"], "type": r["type"], "choices": r["choices"], "cli": r["flag"],
             "flags": r["flags"], "dest": r["dest"], "kind": r["kind"], "nullable": r["nullable"],
             "required": r["required"], "help": r["help"], "group": g, "advanced": g in ADVANCED_GROUPS}
        if r["key"] in GUI_HELP:
            d["gui_help"] = dict(GUI_HELP[r["key"]])
        out[r["key"]] = d
    return _jsonable(out)


# ------------------------------------------------------------------ önteszt, tudásbázis, képességek
_selftest_lock = threading.Lock()
_selftest_cache = {}


def selftest_status(refresh=False):
    """Gyors, folyamaton belüli önteszt: a tests/source_cases.py forrás-esetei (≈ 3300 ellenőrzés, néhány
    száz ms; az első hívás után gyorsítótárból). Ha a tests/ mappa nincs meg (pl. csak a motor van telepítve):
    None. → {state: pass|fail|error, ok, cases, active, checks, passed, failed, elapsed_ms, source}."""
    tests = os.path.join(ROOT, "tests")
    if not os.path.isfile(os.path.join(tests, "source_cases.py")):
        return None
    with _selftest_lock:
        if _selftest_cache and not refresh:
            return dict(_selftest_cache)
        t0 = time.monotonic()
        res = {"state": "error", "ok": False, "cases": None, "active": None, "checks": None, "passed": None,
               "failed": None, "elapsed_ms": None, "source": "tests/source_cases.py"}
        added = tests not in sys.path
        try:
            if added:
                sys.path.insert(0, tests)
            import importlib
            sc = importlib.import_module("source_cases")
            cases = sc.load_cases()
            active = [c for c in cases if c.get("status", "active") == "active"]
            checks = [e[4] is True for c in active for e in sc.check_case(c)]
            failed = checks.count(False)
            res.update({"state": "pass" if not failed else "fail", "ok": not failed, "cases": len(cases),
                        "active": len(active), "checks": len(checks), "passed": len(checks) - failed,
                        "failed": failed})
        except Exception as exc:   # noqa: BLE001 — az önteszt hibája jelvény, nem kivétel
            res["reason"] = "Az önteszt nem futott le (%s: %s)." % (type(exc).__name__, exc)
        finally:
            if added and tests in sys.path:
                sys.path.remove(tests)
        res["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        _selftest_cache.clear()
        _selftest_cache.update(res)
        return dict(res)


def kb_status(db=None):
    """A tudásbázis állapota építés nélkül: {db, built, fresh} (fresh: a seed-ujjlenyomat egyezik; None,
    ha nem dönthető el)."""
    from . import kb
    path = db or kb.DEFAULT_DB
    out = {"db": path, "built": os.path.isfile(path), "fresh": None}
    if out["built"]:
        try:
            con = kb.connect(path, readonly=True)
            try:
                out["fresh"] = kb._meta_get(con, "seed_fingerprint") == kb._seed_fingerprint()
            finally:
                con.close()
        except Exception:   # noqa: BLE001 — sérült/zárolt adatbázis: nem dönthető el
            out["fresh"] = None
    return out


def kb_ensure_built(db=None):
    """A tudásbázis felépítése, ha hiányzik vagy elavult (= kb.ensure_built; a munkapad indításkor hívja).
    → True, ha most épült újra; különben a kb.ensure_built visszatérési értéke."""
    from . import kb
    return kb.ensure_built(db)


def _fts5_ok():
    try:
        import sqlite3
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        finally:
            con.close()
        return True
    except Exception:   # noqa: BLE001
        return False


def _gui_available():
    import importlib.util
    try:
        return importlib.util.find_spec("ma_gui") is not None
    except (ImportError, ValueError):
        return False


# (név, argv, bemenő szerződések, kimenő szerződések, igények)
ENGINE_COMMANDS = (
    ("analyze", ("ma.py", "analyze"), ("szk.ma.analysis-spec/v1",), ("szk.ma.run/v1", "szk.ma.plot/v2"), ()),
    ("validate", ("ma.py", "validate"), ("szk.ma.validate-request/v1",), ("szk.ma.validation/v1",), ()),
    ("es", ("ma.py", "es"), (), (), ()),
    ("convert", ("ma.py", "convert"), (), (), ()),
    ("power", ("ma.py", "power"), (), (), ()),
    ("prisma", ("ma.py", "prisma", "check"), (), (), ()),
    ("project", ("ma.py", "project"), ("szk.ma.project/v1",), ("szk.ma.activity/v1",), ()),
    ("project-audit", ("ma.py", "project", "audit"), ("szk.ma.provenance/v1", "szk.ma.studies/v1"),
     ("szk.ma.project-audit/v1",), ()),
    ("kb", ("ma.py", "kb"), (), (), ("sqlite3.fts5",)),
    ("rules-export", ("ma.py", "rules", "export", "--json"), (), (), ()),
    ("contracts", ("ma.py", "contracts"), (), (), ()),
    ("gui", ("ma.py", "gui"), (), (), ("ma_gui",)),
    ("selftest", ("ma.py", "selftest"), (), (), ()),
)


def capabilities():
    """A motor szk.capabilities/v1 kézfogása (`ma.py --capabilities`): parancsok, szerződések (a
    metaelemzes/contracts/ sémafájljainak bájt-sha256-ja és iránya a motor szemszögéből), igények."""
    from . import contracts
    have = {"sqlite3.fts5": _fts5_ok(), "ma_gui": _gui_available()}
    missing = [m for m in ("sqlite3.fts5",) if not have[m]]
    cmds = [{"name": name, "argv": list(argv), "in": list(cin), "out": list(cout), "needs": list(needs),
             "available": all(have.get(n, True) for n in needs)} for name, argv, cin, cout, needs in ENGINE_COMMANDS]
    issues = []
    if not have["sqlite3.fts5"]:
        issues.append({"id": "no-fts5", "summary": "A Python sqlite3 moduljában nincs FTS5: a tudásbázis (kb) nem "
                                                   "használható; az elemzés és a validálás igen."})
    return _jsonable({
        "schema": CAPABILITIES_SCHEMA, "plugin": "metaelemzes", "version": __version__,
        "python": platform.python_version(), "ok": True,
        "contracts": collections.OrderedDict((r["schema"], {"dir": r["dir"], "sha256": r["sha256"]})
                                             for r in contracts.index()),
        "commands": cmds, "requires": {"modules": ["sqlite3", "sqlite3.fts5"], "missing": missing},
        "known_issues": issues})


def stages():
    """A munkafolyamat szakaszai (S00–S14) magyar és angol névvel — a tudásbázis 'stage' seed-jéből, adatbázis-
    építés nélkül (a felület a szakaszkódok mellé a nevet is kiírja, UX-08): [{id, name: {hu, en}}]."""
    from . import kb
    out = []
    try:
        files = sorted(glob.glob(os.path.join(kb.SEED_DIR, "stages*.json")))
        rows = []
        for f in files:
            with open(f, encoding="utf-8") as fh:
                rows.extend(x for x in json.load(fh) if isinstance(x, dict) and x.get("stage_id"))
    except (OSError, ValueError):
        return out
    for r in sorted(rows, key=lambda x: (x.get("ord") is None, x.get("ord"), x["stage_id"])):
        out.append({"id": r["stage_id"], "name": {"hu": r.get("name_hu") or r["stage_id"],
                                                    "en": r.get("name_en") or r.get("name_hu") or r["stage_id"]}})
    return out


def engine_info(selftest=True, db=None):
    """A motor leírása a felületnek (GET /api/engine): verzió, Python, önteszt (selftest=False: null), a
    tudásbázis állapota, szerződések (név → főverzió), mértékek, opció-metaadat, szabálylista és a szakaszok
    neve (stages)."""
    from . import contracts
    return _jsonable(collections.OrderedDict((
        ("schema", ENGINE_INFO_SCHEMA), ("engine_version", __version__), ("python", platform.python_version()),
        ("selftest", selftest_status() if selftest else None), ("kb", kb_status(db)),
        ("contracts", collections.OrderedDict(contracts.available())),
        ("measures", measures()), ("options", option_metadata()), ("rules", rules_export()),
        ("stages", stages()))))


# ------------------------------------------------------------------ tábla
def column_map(header):
    """Kanonikus oszlopnév → eredeti fejléc, a motor oszlopfelismerésével (tableio.column_map) — ugyanaz, mint
    a validálási dokumentum column_map-je, de validálás nélkül (a GET /api/table is ezt adja)."""
    from . import tableio
    return tableio.column_map(header)


def read_table(path):
    """CSV/TSV → (fejléc, sorok nyers cellaszövegként, formátum-metaadat) — tableio.read_raw (a kódolás,
    tagoló, sorvég, idézés, BOM és az üres sorok megőrzésével; a számokat nem értelmezi). A sorok a nem üres
    adatsorok: az i. sor = a validate_file / analyze 'row' = i és a plot row_index = i. A meta 'column_map'-je
    a kanonikus oszlopnév → eredeti fejléc leképezés (column_map; a write_table figyelmen kívül hagyja)."""
    from . import tableio
    header, rows, meta = tableio.read_raw(path)
    meta["column_map"] = tableio.column_map(header)
    return header, rows, meta


def write_table(path, header, rows_text, meta=None):
    """A read_table párja: formátumtartó, atomi írás → a kiírt bájtok sha256-ja (tableio.write_raw)."""
    from . import tableio
    return tableio.write_raw(path, header, rows_text, meta)


def validate_table(header, rows_text, measure, options=None, decimal_mark=None, delimiter=None, lines=None,
                   project_dir=None, dataset=None):
    """Nyers cellák → szk.ma.validation/v1 (ugyanaz az értelmezés és validálás, mint a fájlnál).

    project_dir megadásakor minden megállapítás 'acknowledged' mezőt kap: a projektnapló legutóbbi aktív
    „Nem hiba — indoklás” döntésének azonosítója, amelynek context-je ugyanarra a szabályra (code), sorra
    (row_uid) és mezőkre (fields) vonatkozik (dataset: a tábla projekt-relatív útja; ha a döntés is megadta,
    egyeznie kell) — különben null. Lásd acknowledge_findings."""
    from . import validate
    doc = validate.validation_document(header, rows_text, measure, options, decimal_mark=decimal_mark,
                                       delimiter=delimiter, lines=lines, with_row_uids=project_dir is not None)
    grid = doc.pop("_row_uids", None)
    if project_dir is not None:
        acknowledge_findings(doc, project_dir, dataset, grid)
    return _jsonable(doc)


def validate_file(path, measure, options=None, project_dir=None, dataset=None):
    """`ma.py validate --data F --measure M --json`: szk.ma.validation/v1 + a korábbi 'k' kulcs.
    project_dir / dataset: mint a validate_table-nél (acknowledged)."""
    from . import validate
    doc = validate.validation_document_from_file(path, measure, options, with_row_uids=project_dir is not None)
    grid = doc.pop("_row_uids", None)
    doc["k"] = doc["k_analysable"]
    if project_dir is not None:
        acknowledge_findings(doc, project_dir, dataset, grid)
    return _jsonable(doc)


ACK_CONTEXT_KIND = "validation"


def acknowledgements(project_dir):
    """[(döntés-id, context)] — a projektnapló AKTÍV döntései, amelyek context-je validálási megállapításra
    vonatkozik (kind = 'validation' vagy kind nélkül, de szabálykóddal). Nincs napló: []."""
    if not os.path.isfile(_projekt.db_path(project_dir)):
        return []
    import sqlite3
    try:
        decisions = _projekt.list_items(project_dir, "decisions", status="active")
    except (sqlite3.Error, OSError):         # zárolt / sérült napló: a validálás nem bukik el, csak nem jelöl
        return []
    out = []
    for d in decisions:
        ctx = d.get("context")
        if not isinstance(ctx, dict) or not isinstance(ctx.get("code"), str):
            continue
        if ctx.get("kind", ACK_CONTEXT_KIND) != ACK_CONTEXT_KIND:
            continue
        out.append((d["id"], ctx))
    return out


def _finding_uids(f, grid):
    """A megállapítás sorainak row_uid-jai: a 'row_uid' és (többsoros szabálynál) a 'rows' összes sora."""
    out = [f["row_uid"]] if f.get("row_uid") else []
    for r in f.get("rows") or ():
        if grid is not None and isinstance(r, int) and 0 <= r < len(grid) and grid[r] and grid[r] not in out:
            out.append(grid[r])
    return out


def acknowledge_findings(doc, project_dir, dataset=None, row_uids=None, entries=None):
    """A validálási dokumentum megállapításaira az 'acknowledged' (döntés-id vagy None). Illeszkedés (a kulcs:
    code, row_uid, fields): azonos szabálykód; a döntés row_uid-ja a megállapítás sorai között van (táblaszintű
    megállapításnál a döntésnek sincs row_uid-ja); ha a döntés megadta a mezőket, azok a megállapítás mezői
    (sorrendtől függetlenül); ha a döntés és a hívó is megadta a táblát (dataset), egyeznek. Több illeszkedő
    döntésből a legutóbbi (legnagyobb azonosítójú) számít. row_uids: a 'rows' indexek → row_uid (a többsoros
    szabályokhoz); entries: előre lekérdezett acknowledgements(project_dir)."""
    entries = acknowledgements(project_dir) if entries is None else entries
    for f in doc.get("findings") or ():
        uids = _finding_uids(f, row_uids)
        hit = None
        for did, ctx in entries:
            if ctx.get("code") != f.get("code"):
                continue
            if dataset and ctx.get("dataset") and ctx.get("dataset") != dataset:
                continue
            cu = ctx.get("row_uid")
            if cu is None:
                if uids:
                    continue
            elif cu not in uids:
                continue
            fields = ctx.get("fields")
            if fields and sorted(fields) != sorted(f.get("fields") or []):
                continue
            hit = did if hit is None else max(hit, did)
        f["acknowledged"] = hit
    return doc


def validate_request(req):
    """`ma.py validate --request-json`: szk.ma.validate-request/v1 (dict) → szk.ma.validation/v1."""
    from . import validate
    return _jsonable(validate.validation_from_request(req))


def validate_request_text(text):
    """Mint a validate_request, JSON-szövegből (ValueError: érvénytelen JSON vagy kérés)."""
    from . import validate
    return _jsonable(validate.validation_from_request_text(text))


# ------------------------------------------------------------------ spec-segédek (spec.py)
def spec_from_argv(argv, project_root=None, **kw):
    from . import spec as S
    return S.spec_from_argv(argv, project_root, **kw)


def argv_from_spec(spec, project_root=None, **kw):
    from . import spec as S
    return S.argv_from_spec(spec, project_root, **kw)


def options_from_spec(spec):
    from . import spec as S
    return S.options_from_spec(spec)


def validate_spec(spec):
    from . import spec as S
    return S.validate_spec(spec)


def load_spec(path):
    from . import spec as S
    return S.load_spec(path)


def save_spec(path, spec):
    from . import spec as S
    return S.save_spec(path, spec)


def spec_sha256(spec):
    from . import spec as S
    return S.spec_sha256(spec)


def project_run_files(project_root):
    """A projekt commit-futásainak run.json-jai (= spec.project_run_files; a project audit, a munkapad futás-listája,
    a pillanatkép és az audit-export ugyanezt a felderítést használja) → [(abszolút út, elrendezés, kimenet-mappa vagy
    None, [(adatfájl, adat-sha256)])]."""
    from . import spec as S
    return S.project_run_files(project_root)


def run_summary_text(summary):
    """A futás rövid szövege a tevékenységnapló result.summary-jéhez (= activity.run_summary_text; a CLI és a
    munkapad bájtra azonos szöveget ír): 'k=13, RR 0.49 [0.33; 0.73]'."""
    return _activity.run_summary_text(summary)


# ------------------------------------------------------------------ elemzés
def _shell_join(args):
    import shlex
    import subprocess
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def _excluded(es, rows, table_rows, meta):
    """A kizárt sorok: 'row' ugyanaz, mint a validálási dokumentumban (fájlnál az adatsor indexe, nyers piszkozatnál
    a kliens sorai közötti hely: meta['row_positions']), 'row_uid' a sor azonosítója."""
    from . import tableio, validate
    pos = {id(r): i for i, r in enumerate(table_rows)}
    uids = tableio.row_uids(table_rows, meta)
    grid = (meta or {}).get("row_positions")
    out = []
    for (lab, why), i in zip(es.excluded, validate.excluded_rows(es, len(rows))):
        t = pos.get(id(rows[i])) if i is not None else None
        row = grid[t] if t is not None and grid is not None and t < len(grid) else t
        out.append({"study": lab, "row": row, "row_uid": uids[t] if t is not None else None, "reason": why})
    return out


def _results_view(out, data_rel):
    res = _jsonable(out)
    inp = dict(res.get("input") or {})
    inp["path"] = data_rel
    res["input"] = inp
    return res


def analyze(spec, table=None, mode="explore", outdir=None, project_root=None, spec_path=None, client_seq=None,
            run_id=None, date=None, actor=None, plots=True, log=True):
    """Elemzés specből → nézetmodell (szk.ma.analysis-result/v1):
    {schema, run (szk.ma.run/v1), plot (szk.ma.plot/v2; k = 0: null), results (a results.json tartalma, a
    data-úttal projekt-relatívan), validation_summary, findings, excluded (row, row_uid), warnings, command}.

    spec: szk.ma.analysis-spec/v1 dict vagy a spec-fájl útja (ekkor a spec.path és a hash a fájlé).
    table: None → a spec data.path fájlja (a project_root-hoz, ennek hiányában a munkakönyvtárhoz relatív); vagy
    nyers tábla {header, rows, decimal_mark?, delimiter?, lines?} (mentetlen piszkozat; csak explore).
    mode='explore': semmit nem ír (run_id null, nincs files). mode='commit': a kimenetek (a CLI-vel azonos fájlok)
    + run.json az outdir-be (alap: <projekt>/05_elemzes/<kimenet>/<run_id>), és ha van projektnapló, a futás
    bekerül (projekt.log_run; actor). Elavult (data.sha256-tól eltérő) adattal a commit SpecError.
    A tevékenységnaplót (activity.jsonl) a hívó vezeti (a munkapad saját láncába ír)."""
    from . import pipeline, report, tableio, spec as S
    from .cli import _ANALYZE_OUTPUTS, _refuse_overwrite, _run_summary
    started = datetime.datetime.now(datetime.timezone.utc)
    t0 = time.monotonic()
    if mode not in MODES:
        raise ValueError("mode: %r (lehetséges: %s)" % (mode, ", ".join(MODES)))
    if isinstance(spec, (str, bytes, os.PathLike)):
        spec_path = os.fspath(spec)
        spec = S.load_spec(spec_path)
    errs = S.validate_spec(spec)
    if errs:
        raise S.SpecError("érvénytelen elemzési spec: %s" % "; ".join(errs))
    root = project_root
    warnings = []
    data_rel = spec["data"]["path"]
    if table is None:
        data_file = S.data_path(spec, root)
        if not os.path.isfile(data_file):
            raise S.SpecError("a spec adatfájlja nem található: %s (a data.path a projektgyökérhez relatív)"
                              % data_file)
        with open(data_file, "rb") as fh:
            raw = fh.read()
        data_sha = hashlib.sha256(raw).hexdigest()
        want = spec["data"].get("sha256")
        if want and want != data_sha:
            if mode == "commit":
                S.check_data_sha256(spec, data_file)
            warnings.append("Az adatfájl tartalma megváltozott a spec rögzítése óta (data.sha256); a commit-futás "
                            "elutasítaná.")
        rows, meta = tableio.read_table_bytes(raw, data_rel)       # results.json / report.md: projekt-relatív út
    else:
        if mode == "commit":
            raise ValueError("commit-futás csak mentett adattáblából indítható (table=None)")
        if not isinstance(table, dict):
            raise ValueError("a 'table' objektum legyen: {header, rows, decimal_mark?, delimiter?, lines?}")
        data_file, data_sha = None, None
        rows, meta = tableio.parse_table(table.get("header") or [], table.get("rows") or [],
                                         table.get("decimal_mark"), table.get("delimiter"), table.get("lines"))
    table_rows = rows
    prov_file = data_file
    if prov_file is None:
        try:
            prov_file = S.data_path(spec, root)
        except (S.SpecError, ValueError, KeyError, TypeError):
            prov_file = None
    provenance = pipeline.load_provenance(prov_file)
    exclude, include = S.filters_from_spec(spec)
    frep = []
    rows = tableio.apply_filters(rows, exclude, include, meta=meta, report=frep)
    meta["filters"] = {"exclude": exclude, "include": include}
    opts = S.options_from_spec(spec)
    opts["filter_report"] = frep
    out, es = pipeline.run(rows, opts, meta, table_rows=table_rows)
    digest = None
    if spec_path and os.path.isfile(spec_path):
        digest = S.sha256_file(spec_path)
    elif root is not None:
        digest = S.spec_sha256(spec)
    rid, paths, files, reserved = None, None, None, None
    if mode == "commit":
        if run_id is not None and not re.fullmatch(S.RUN_ID_PATTERN[1:-1], run_id):
            raise S.SpecError("run_id: érvénytelen azonosító %r (alak: 20261004T211200Z-a1f3c2)" % (run_id,))
        journal = log and root is not None
        if journal:
            _projekt.connect(root).close()          # nem inicializált projekt: hiba, mielőtt bármi íródna
            actor = _projekt.check_actor(actor)
        rid = run_id
        if outdir is None:
            base = os.path.join(root if root is not None else ".", S.ANALYSIS_DIR, S.outcome_dir(spec["outcome"]))
            if rid is None:
                # egyedi run_id és saját futásmappa (atomi lefoglalás): két commit sosem írja felül egymást
                rid, reserved = S.unique_run_id(started, data_sha, root, base_dir=base)
                outdir = reserved
            else:
                outdir = os.path.join(base, rid)
                if os.path.exists(os.path.join(outdir, "run.json")):
                    raise S.SpecError("a(z) %s futásmappa már egy rögzített futásé (run.json); a commit nem írja "
                                      "felül — adj meg másik run_id-t vagy outdir-t" % outdir)
        elif rid is None:
            rid = S.unique_run_id(started, data_sha, root)[0]
    try:
        if mode == "commit":
            _refuse_overwrite(data_file, [os.path.join(outdir, n) for n in _ANALYZE_OUTPUTS])
            md = report.build_report(out, opts.get("title"), date or datetime.date.today().isoformat(), plots=plots)
            paths = pipeline.write_outputs(out, es, outdir, md, plots=plots,
                                           run_info={"run_id": rid, "spec_sha256": digest, "data_sha256": data_sha},
                                           provenance=provenance)
            files = paths
    except BaseException:
        if reserved is not None:
            try:
                os.rmdir(reserved)
            except OSError:
                pass
        raise
    command = None
    try:
        if spec_path:
            command = ["ma.py", "analyze", "--spec", S._disp_path(spec_path, root)]
            if outdir is not None:
                command += ["--out", S._disp_path(outdir, root)]
            if root is not None:
                command += ["--project", "."]
        else:
            command = ["ma.py"] + S.argv_from_spec(spec, root, out_dir=outdir, log_to_project=root is not None)
    except S.SpecError:
        command = None
    finished = datetime.datetime.now(datetime.timezone.utc)
    desc = S.run_descriptor(out, mode=mode, run_id=rid, spec=spec, spec_path=spec_path, spec_hash=digest,
                            equivalent_argv=command, data_file=data_file, data_sha256=data_sha,
                            data_rows=(out.get("input") or {}).get("n_rows"), files=files, project_root=root,
                            started=started, finished=finished, elapsed_ms=int((time.monotonic() - t0) * 1000),
                            client_seq=client_seq, es=es)
    if data_file is None:
        desc["data"]["path"] = data_rel
    if mode == "commit":
        S.write_run_json(outdir, desc)
        if log and root is not None:
            if spec_path:
                argv = ["analyze", "--spec", os.path.abspath(spec_path)]
            else:
                argv = S.argv_from_spec(spec, root, absolute=True)
            argv += ["--out", os.path.abspath(outdir), "--project", os.path.abspath(root)]
            prog = [os.path.basename(sys.executable) or "python3", os.path.join(ROOT, "ma.py")]
            _projekt.log_run(root, _shell_join(prog + argv), os.path.abspath(data_file), os.path.abspath(outdir),
                             __version__, _jsonable(_run_summary(out)), actor=actor)
    plot = None
    if out.get("primary") is not None:
        plot = pipeline.plot_document(out, es, run_info={"run_id": rid, "spec_sha256": digest,
                                                         "data_sha256": data_sha}, provenance=provenance)
    view = collections.OrderedDict((
        ("schema", ANALYSIS_RESULT_SCHEMA), ("run", desc), ("plot", plot), ("results", _results_view(out, data_rel)),
        ("validation_summary", dict((out.get("validation") or {}).get("summary") or {})),
        ("findings", (out.get("validation") or {}).get("findings") or []),
        ("excluded", _excluded(es, rows, table_rows, meta)),
        ("filter_report", frep), ("warnings", warnings + list(out.get("warnings") or [])), ("command", command)))
    return _jsonable(view)


# ------------------------------------------------------------------ átváltás (szk.ma.convert-*)
def _tr(hu, en):
    """Kétnyelvű szöveg (i18n-objektum, terv 4.0): {'hu': …, 'en': …}."""
    return collections.OrderedDict((("hu", hu), ("en", en)))


# kind → (CLI-fajta, kötelező/elfogadott numerikus bemenetek, kimenetek, becslés?, módszer-id, hivatkozás,
#         függvény, KB-hivatkozások, feltevések {hu, en})
_CONVERT = collections.OrderedDict((
    ("median_to_mean_sd", ("median", ("n", "median", "q1", "q3", "min", "max"), ("mean", "sd"), True,
                           "luo2018+wan2014", "Cochrane Handbook 6.5.2.5; Luo 2018; Wan 2014",
                           "conversions.mean_from_median / sd_from_median",
                           ("D-S05-010", "D-S05-011", "F-MOR20-010"), (_tr("közel normális eloszlás", "approximately normal distribution"),))),
    ("se_to_sd", ("se", ("se", "n"), ("sd",), False, "sd_from_se", "Cochrane Handbook 6.5.2.2",
                  "conversions.sd_from_se", ("D-S05-006", "F-MOR20-002"),
                  (_tr("a közölt érték a karátlag standard hibája (nem SD)",
                       "the reported value is the standard error of the arm mean (not the SD)"),))),
    ("ci_to_sd", ("ci", ("lower", "upper", "n", "level"), ("sd",), False, "sd_from_ci_t", "Cochrane Handbook 6.5.2.2",
                  "conversions.sd_from_ci", ("D-S05-007", "F-MOR20-003"),
                  (_tr("a CI a karátlagé (nem a csoportközi különbségé); t-eloszlás n − 1 szabadsági fokkal",
                       "the CI belongs to the arm mean (not to the between-group difference); t distribution with "
                       "n − 1 degrees of freedom"),))),
    ("ci_to_se", ("se-from-ci", ("lower", "upper", "level", "df", "log"), ("se",), False, "se_from_ci",
                  "Cochrane Handbook 6.5.2.3", "conversions.se_from_ci", ("D-S05-008", "D-S05-009", "F-BOR09-032"),
                  (_tr("szimmetrikus CI (arány-mértéknél a log-skálán: log=igen)",
                       "symmetric CI (for ratio measures on the log scale: log=yes)"),))),
    ("p_to_se", ("se-from-p", ("estimate", "p", "df", "log"), ("se",), False, "se_from_p",
                 "Cochrane Handbook 6.5.2.3", "conversions.se_from_p", ("D-S05-008", "D-S05-009", "F-BOR09-031"),
                 (_tr("pontos (nem kerekített, nem '<' alakú) kétoldali p-érték",
                      "exact (not rounded, not of the '<' form) two-sided p-value"),))),
    ("smd_variance", ("smd-var", ("g", "n1", "n2", "vtype", "j_method"), ("vi", "sei"), False, "smd_variance",
                      "Borenstein 2009 4. fejezet", "conversions.smd_variance", ("F-BOR09-030",), ())),
    ("combine_groups", ("combine", ("n1", "m1", "sd1", "n2", "m2", "sd2"), ("n", "mean", "sd"), False,
                        "combine_groups", "Cochrane Handbook 6.5.2.10 (6.5.a táblázat)", "conversions.combine_groups",
                        ("D-S05-015",), ())),
    ("change_sd", ("change", ("sd_baseline", "sd_final", "corr"), ("sd_change",), True, "sd_change_from_corr",
                   "Cochrane Handbook 6.5.2.8", "conversions.sd_change", ("D-S05-013", "F-MOR20-017"),
                   (_tr("a kiindulási és a végponti érték korrelációja (corr) más vizsgálatból vagy feltevésből származik",
                       "the baseline–final correlation (corr) comes from another study or an assumption"),))),
    ("corr_from_change", ("corr-from-change", ("sd_baseline", "sd_final", "sd_change"), ("corr",), False,
                          "corr_from_change", "Cochrane Handbook 6.5.2.8", "conversions.corr_from_change",
                          ("D-S05-013",), ())),
    # a karonkénti kimenet: n_1, n_2, … (és events_1, …)
    ("split_shared_control", ("split-control", ("n", "arms", "events"), ("n", "events"), True, "split_shared_control",
                              "Cochrane Handbook 23.3.4", "conversions.split_shared_control", ("D-S05-015",),
                              (_tr("a közös kontrollcsoport egyenlő felosztása a karok között",
                                   "the shared control group is split equally between the arms"),))),
    ("paired_sums", ("paired-sums", ("n", "sum_d", "sum_sq_dev"), ("mdiff", "sd_diff", "n"), False, "paired_from_sums",
                     "páros különbségek átlaga és SD-je az összegekből", "conversions.paired_from_sums",
                     ("D-S07-010",), ())),
    ("t_to_d", ("d-from-t", ("t", "n1", "n2", "vtype", "j_method", "hedges"), ("d", "g", "J", "vi", "sei"), False,
                "d_from_t", "Borenstein 2009 4. fejezet", "conversions.d_from_t", ("D-S05-008",),
                (_tr("független mintás t-próba két karral", "independent-samples t-test with two arms"),))),
    ("logor_to_d", ("logor-to-d", ("y", "v"), ("y", "v"), True, "logor_to_d", "Borenstein 2009 7. fejezet",
                    "conversions.logor_to_d", ("D-S07-021", "F-KHN0102-012", "F-SIM24-012"),
                    (_tr("a mögöttes folytonos változó logisztikus eloszlású (Hasselblad–Hedges)",
                        "the underlying continuous variable follows a logistic distribution (Hasselblad–Hedges)"),))),
    ("d_to_logor", ("d-to-logor", ("y", "v"), ("y", "v"), True, "d_to_logor", "Borenstein 2009 7. fejezet",
                    "conversions.d_to_logor", ("D-S07-021", "F-KHN0102-013"),
                    (_tr("a mögöttes folytonos változó logisztikus eloszlású (Hasselblad–Hedges)",
                        "the underlying continuous variable follows a logistic distribution (Hasselblad–Hedges)"),))),
    ("r_to_d", ("r-to-d", ("y", "v"), ("y", "v"), True, "r_to_d", "Borenstein 2009 7. fejezet",
                "conversions.r_to_d", ("D-S07-021", "F-KHN0102-014", "F-SIM24-011"),
                (_tr("a korreláció egy dichotóm csoportosítás pont-biszeriális megfelelője",
                     "the correlation is the point-biserial counterpart of a dichotomous grouping"),))),
    ("d_to_r", ("d-to-r", ("y", "v", "n1", "n2"), ("y", "v"), True, "d_to_r", "Borenstein 2009 7. fejezet",
                "conversions.d_to_r", ("D-S07-021", "F-SIM24-010"),
                (_tr("a d két, n1 és n2 létszámú csoport különbsége",
                     "d is the difference between two groups of sizes n1 and n2"),))),
))
CONVERT_KINDS = tuple(_CONVERT)
_BOOL_INPUTS = ("log", "hedges")
_TEXT_INPUTS = ("vtype", "j_method")
_TRUE = ("1", "true", "yes", "igen", "i", "y")
_FALSE = ("0", "false", "no", "nem", "n", "")
_ESTIMATED_NOTE = _tr("Becsült érték: jelöld az adattáblában (estimated=igen) és végezz nélküle érzékenységi "
                      "elemzést (D-S05-023).",
                      "Estimated value: mark it in the data table (estimated=yes) and run a sensitivity analysis "
                      "without it (D-S05-023).")
_SKEW_NOTE = _tr("Az átlag < 2·SD: nemnegatív változónál ferde eloszlás gyanúja (V013) — a mediánból becsült érték "
                 "óvatosan kezelendő.",
                 "Mean < 2·SD: for a non-negative variable this suggests a skewed distribution (V013) — treat the "
                 "value estimated from the median with caution.")
# a CLI (convert_values) magyar megjegyzéseinek angol megfelelője (a kulcs a szöveg eleje)
_NOTE_EN = (("Az LS2 variancia Hedges-féle g-t feltételez",
             "The LS2 variance assumes Hedges' g: to get Hedges' g (and its variance) set the hedges input."),
            ("Az UB variancia Hedges-féle g-t feltételez",
             "The UB variance assumes Hedges' g: to get Hedges' g (and its variance) set the hedges input."))


def _note_i18n(text):
    for prefix, en in _NOTE_EN:
        if text.startswith(prefix):
            return _tr(text, en)
    return _tr(text, text)


def _convert_input(kind, name, value):
    from .tableio import parse_number
    if name in _BOOL_INPUTS:
        if isinstance(value, bool) or value is None:
            return bool(value)
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        t = str(value).strip().lower()
        if t in _TRUE or t in _FALSE:
            return t in _TRUE
        # a hibaüzenet a mezőt nevezi meg, az értéket soha (T10: a bevitt cellaszöveg nem kerülhet hibába)
        raise ValueError("%s: %s: igen/nem érték kell" % (kind, name))
    if name in _TEXT_INPUTS:
        if value is None:
            return None
        return str(value).strip().upper() if name == "vtype" else str(value).strip().lower()
    if isinstance(value, bool):
        raise ValueError("%s: %s: szám kell (logikai érték helyett)" % (kind, name))
    try:
        x = parse_number(value)
    except ValueError:
        raise ValueError("%s: %s: nem értelmezhető szám" % (kind, name)) from None
    if name == "level" and x is not None and 1 < x < 100:
        x = x / 100.0
    return x


def _num_text(v):
    """Kijelzési szöveg {hu, en} a motor egyetlen konvenciójával (mint a plot/run display_text-je: tizedespont;
    hu: kötőjel-mínusz, en: U+2212). A táblába írandó szöveg nem ez, hanem a cell_text (_cell_text)."""
    if v is None:
        return None
    from .plots import MINUS, num_text
    t = "%.4g" % v
    return {"hu": t, "en": num_text(t, MINUS)}


def _cell_mark(target):
    """A cellába írt szám tizedesjele: target.decimal_mark; ennek hiányában a target.delimiter szerint (';' → ',',
    más → '.'); egyiknél sem (a munkapad korábbi alapértéke) ','."""
    t = target if isinstance(target, dict) else {}
    if t.get("decimal_mark") in (",", "."):
        return t["decimal_mark"]
    if t.get("delimiter") in (",", ";", "\t"):
        return "," if t["delimiter"] == ";" else "."
    return ","


def _cell_text(v, mark):
    """A táblába írandó szöveg (nyers cella, ASCII mínusz): a tableio ugyanazt a számot olvassa vissza."""
    if v is None:
        return None
    t = "%.4g" % v
    return t.replace(".", ",") if mark == "," else t


def convert(request):
    """szk.ma.convert-request/v1 → szk.ma.convert-result/v1 (terv 4.7). A számítás a `ma.py convert`
    útja (cli.convert_values); a bemenetek nyers szövegek is lehetnek ('12,5'; a tableio értelmezi). Az
    'estimated' a motor döntése: becslés true, algebrai átalakítás false. A request 'target'-je visszhangként a
    válaszba kerül. outputs_text: kijelzési szöveg {hu, en} (a display_text konvenciója); cell_text: a cellába írandó
    szöveg a cél tábla tizedesjelével (target.decimal_mark, ennek hiányában target.delimiter; alapból ',').
    assumptions és warnings: kétnyelvű szövegek ({hu, en}, terv 4.0) — a felület a választott nyelvet mutatja.
    Hibás kérés vagy érvénytelen bemenet: ValueError (magyar üzenettel)."""
    from . import conversions as C
    from .cli import convert_namespace, convert_values
    if not isinstance(request, dict):
        raise ValueError("a kérés JSON-objektum legyen")
    if request.get("schema") != CONVERT_REQUEST_SCHEMA:
        raise ValueError("ismeretlen kérés-séma: %r (várt: %s)" % (request.get("schema"), CONVERT_REQUEST_SCHEMA))
    kind = request.get("kind")
    if kind not in _CONVERT:
        raise ValueError("ismeretlen átváltás: %r (lehetséges: %s)" % (kind, ", ".join(_CONVERT)))
    cli_kind, names, outputs, estimated, mid, citation, function, kb_refs, assumptions = _CONVERT[kind]
    inputs = request.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("az 'inputs' objektum legyen (név → érték)")
    unknown = sorted(set(inputs) - set(names))
    if unknown:
        raise ValueError("%s: ismeretlen bemenet: %s (elfogadott: %s)" % (kind, ", ".join(unknown), ", ".join(names)))
    params = {n: _convert_input(kind, n, inputs[n]) for n in names if n in inputs}
    params = {k: v for k, v in params.items() if v is not None or k in _BOOL_INPUTS}
    method = request.get("method")
    if kind == "median_to_mean_sd":
        if method not in (None, "luo", "hozo"):
            raise ValueError("median_to_mean_sd: a módszer luo vagy hozo lehet, kapott: %r" % (method,))
        params["method"] = method or "luo"
        if params["method"] == "hozo":
            mid, citation = "hozo2005+wan2014", "Cochrane Handbook 6.5.2.5; Hozo 2005; Wan 2014"
    elif method is not None:
        raise ValueError("%s: ehhez az átváltáshoz nincs módszerválasztás (method)" % kind)
    try:
        res = convert_values(convert_namespace(cli_kind, params))
    except C.ConversionError as exc:
        raise ValueError(str(exc))
    out = collections.OrderedDict()
    for n in outputs:
        if isinstance(res.get(n), (list, tuple)):        # karonkénti lista (split-control) → n_1, n_2, …
            out.update(("%s_%d" % (n, i + 1), v) for i, v in enumerate(res[n]))
        elif n in res:
            out[n] = res[n]
    warnings = []
    if kind == "median_to_mean_sd" and out.get("mean") is not None and out.get("sd") is not None and \
            out["mean"] < 2 * out["sd"]:
        warnings.append(_SKEW_NOTE)
    if res.get("megjegyzés"):
        warnings.append(_note_i18n(res["megjegyzés"]))
    if estimated:
        warnings.append(_ESTIMATED_NOTE)
        kb_refs = tuple(kb_refs) + ("D-S05-023",)
    doc = collections.OrderedDict((
        ("schema", CONVERT_RESULT_SCHEMA), ("kind", kind), ("engine_version", __version__), ("outputs", out),
        ("outputs_text", collections.OrderedDict((n, _num_text(v)) for n, v in out.items())),
        ("cell_text", collections.OrderedDict((n, _cell_text(v, _cell_mark(request.get("target"))))
                                              for n, v in out.items())),
        ("estimated", estimated),
        ("method", {"id": mid, "citation": citation, "function": function, "kb_refs": list(kb_refs)}),
        ("assumptions", [dict(a) for a in assumptions]), ("warnings", [dict(w) for w in warnings])))
    if request.get("target") is not None:
        doc["target"] = request["target"]
    return _jsonable(doc)


def convert_cli(kind, params=None):
    """`ma.py convert <kind> --… ` JSON-kimenete (a 'figyelem' sorral): params = argparse-név → érték."""
    from .cli import CONVERT_NOTE, convert_namespace, convert_values
    res = convert_values(convert_namespace(kind, params))
    res["figyelem"] = CONVERT_NOTE
    return _jsonable(res)


# ------------------------------------------------------------------ PRISMA
def prisma_check(flow, studies=None, template=None):
    """A PRISMA-folyamatábra számainak ellenőrzése (= `prisma check --json F --out-format json`). flow: kanonikus
    vagy 2009-es mezőnevek, vagy a composer prisma-flow.json-ja. studies: szk.ma.studies/v1 (dict vagy út): ha a
    flow-ból hiányzik, az I (bevont vizsgálatok) = a vizsgálatok száma, a J (bevont jelentések) = a reports
    rec_id-jeinek uniója; ha a flow-ban más érték áll, P017 (a források eltérnek)."""
    from . import prisma
    if not isinstance(flow, dict):
        raise ValueError("a flow objektum legyen")
    flow = prisma.from_composer(flow) if "dedup_removed" in flow else dict(flow)
    extra = []
    if studies is not None:
        if isinstance(studies, (str, os.PathLike)):
            with open(studies, encoding="utf-8-sig") as fh:
                studies = json.load(fh)
        lst = studies.get("studies") if isinstance(studies, dict) else None
        if not isinstance(lst, list):
            raise ValueError("studies: szk.ma.studies/v1 dokumentum kell ({studies: [...]})")
        ids = {str(s.get("study_id")) for s in lst if isinstance(s, dict) and s.get("study_id") not in (None, "")}
        recs = {str(r.get("rec_id")) for s in lst if isinstance(s, dict) for r in (s.get("reports") or [])
                if isinstance(r, dict) and r.get("rec_id") not in (None, "")}
        vals, _reasons, _seen = prisma.normalize(flow)
        sev, title, advice, ref = prisma.RULES["P017"]
        for field, n in (("included_studies", len(ids)), ("included_reports", len(recs) if recs else None)):
            if n is None:
                continue
            raw = vals.get(field)
            try:
                # a doboz szövegként is érkezhet (pl. a felület '15'-öt küld): ugyanaz az értelmezés, mint a P001-é
                have = prisma._as_count(raw)
            except ValueError:
                continue        # értelmezhetetlen doboz: a check_flow P001-gyel jelzi, összevetni nem lehet
            if have is None:
                flow[field] = n     # hiányzó vagy üres doboz: a studies.json-ból
            elif have != n:
                extra.append({"code": "P017", "severity": sev, "title": title, "study": None,
                              "detail": "%s: flow = %d, studies.json = %d" % (prisma.box_label(field), have, n),
                              "advice": advice, "source": ref, "fields": [field]})
    res = prisma.check_flow(flow, template)
    res.findings = extra + res.findings
    return _jsonable(res.to_dict())


# ------------------------------------------------------------------ projekt
def project_audit(project_dir, stage=None, now=None):
    """`ma.py project audit <mappa> --json` (szk.ma.project-audit/v1)."""
    from . import audit
    return _jsonable(audit.project_audit(project_dir, stage=stage, now=now))


def audit_gate_errors(project_dir):
    """A FINAL audit-kaput elutasító (error szintű) X-találatok."""
    from . import audit
    return _jsonable(audit.audit_gate_errors(project_dir))


def project_init(project_dir, title, question=None):
    """Projektmappa és napló létrehozása → {dir, templates: [a bemásolt sablonok relatív útja]}."""
    return {"dir": project_dir, "templates": _projekt.init(project_dir, title, question)}


def outcome_from_args(outcome_id, name=None, data=None, measure=None, critical=None):
    """Kimenet-leíró a megadott mezőkből; a mérték a motor mértékei közül (nagybetűsítve). → dict; ValueError."""
    o = {"id": outcome_id}
    if name is not None:
        if isinstance(name, str):
            if not name.strip():
                raise ValueError("a kimenet neve nem lehet üres")
            o["name"] = {"hu": name.strip(), "en": name.strip()}
        else:
            o["name"] = name
    if data is not None:
        o["data"] = str(data).replace("\\", "/")
    if measure is not None:
        m = str(measure).strip().upper()
        known = [x["id"] for x in measures()]
        if m not in known:
            raise ValueError("ismeretlen hatásméret: %s (lehetséges: %s)" % (measure, ", ".join(known)))
        o["measure"] = m
    if critical is not None:
        o["critical"] = bool(critical)
    return o


def project_outcome_doc(project_dir, meta, outcome, data_class=None, replace=False):
    """A ma-projekt.json új tartalma a kimenettel (írás nélkül; a munkapad a saját tárolójával írja) →
    (dokumentum, kimenet). Lásd projekt.outcome_meta_doc."""
    return _projekt.outcome_meta_doc(project_dir, meta, outcome, data_class=data_class, replace=replace)


def project_outcome_add(project_dir, outcome_id, name=None, data=None, measure=None, critical=None, data_class=None,
                        replace=False):
    """`ma.py project outcome <mappa> --id … --name … --data … --measure …`: kimenet felvétele a ma-projekt.json-ba
    (ha még nincs ilyen fájl, létrejön) → {path, outcome, outcomes}."""
    o = outcome_from_args(outcome_id, name, data, measure, critical)
    doc, added = _projekt.add_outcome(project_dir, o, data_class=data_class, replace=replace)
    return {"path": _projekt.project_meta_path(project_dir), "outcome": added,
            "outcomes": [x.get("id") for x in doc.get("outcomes") or []]}


def project_log(project_dir, agent, decision, rationale=None, stage=None, kb_refs=None, alternatives=None,
                supersedes=None, kb_db=None, strict=False, actor=None, context=None):
    """Döntés a projektnaplóba → {id, warnings}. context: a döntés gépi kontextusa (projekt.check_context;
    pl. „Nem hiba — indoklás”: {kind: 'validation', dataset, row_uid, code, fields}) — ebből jelöli a
    validate_table(project_dir=…) a megállapítást 'acknowledged'-ként."""
    warns = []
    rid = _projekt.log_decision(project_dir, agent, decision, rationale, stage, kb_refs, alternatives, supersedes,
                                kb_db=kb_db, strict=strict, warnings=warns, actor=actor, context=context)
    return {"id": rid, "warnings": warns}


def project_finding(project_dir, agent, severity, title, detail=None, stage=None, evidence=None, kb_refs=None,
                    kb_db=None, strict=False, actor=None):
    warns = []
    rid = _projekt.add_finding(project_dir, agent, severity, title, detail, stage, evidence, kb_refs, kb_db=kb_db,
                               strict=strict, warnings=warns, actor=actor)
    return {"id": rid, "warnings": warns}


def project_resolve(project_dir, finding_id, status, resolution, actor=None):
    _projekt.resolve_finding(project_dir, finding_id, status, resolution, actor=actor)
    return {"id": finding_id, "status": status}


def project_checkpoint(project_dir, stage, agent, verdict, summary=None, actor=None, audit_gate=False):
    warns = []
    rid = _projekt.checkpoint(project_dir, stage, agent, verdict, summary, warnings=warns, actor=actor,
                              audit_gate=audit_gate)
    return {"id": rid, "stages": _projekt.parse_stage(stage), "verdict": verdict, "warnings": warns}


def project_grade(project_dir, outcome, certainty, kb_db=None, strict=False, actor=None, **domains):
    warns = []
    rid = _projekt.add_grade(project_dir, outcome, certainty, kb_db=kb_db, strict=strict, warnings=warns,
                             actor=actor, **domains)
    return {"id": rid, "warnings": warns}


def project_status(project_dir):
    """`ma.py project status <mappa> --json`."""
    return _jsonable(_projekt.status(project_dir))


def project_list(project_dir, kind, status=None, severity=None, stage=None):
    """`ma.py project list <mappa> <fajta> --json`."""
    return _jsonable(_projekt.list_items(project_dir, kind, status=status, severity=severity, stage=stage))


def project_show(project_dir, kind, item_id):
    """`ma.py project show <mappa> <fajta> <id> --json`."""
    return _jsonable(_projekt.get_item(project_dir, kind, item_id))


def project_export_json(project_dir):
    """`ma.py project export <mappa> --format json` fájltartalma (szk.ma.journal-export/v1)."""
    return _jsonable(_projekt.export_json(project_dir))


def project_export_markdown(project_dir):
    """`ma.py project export <mappa>` (Markdown) fájltartalma."""
    return _projekt.export_markdown(project_dir)


def project_activity(project_dir, anchor=None):
    """`ma.py project activity <mappa> --json`: a tevékenységnapló hash-láncának ellenőrzése →
    {schema, path, ok, first_bad_seq, message, records, head}."""
    ok, bad, msg = _activity.verify(project_dir, anchor)
    try:
        n = len(_activity.read(project_dir)) if ok else None
    except Exception:   # noqa: BLE001 — a verify már megmondta, mi a baj
        n = None
    return {"schema": ACTIVITY_REPORT_SCHEMA, "path": LOG_RELPATH, "ok": ok, "first_bad_seq": bad, "message": msg,
            "records": n, "head": _activity.head(project_dir) if ok else None}


# ------------------------------------------------------------------ tudásbázis
class UnknownStage(ValueError):
    """A kb rules --stage értéke nem ismert szakasz (az üzenet a CLI-ével azonos)."""


_FINAL_STAGE = "S14"      # a FINAL ellenőrzés szabályai az S14-éi (D-S14-025)


def kb_search(query, limit=8, scopes=("rule", "knowledge", "chunk"), source=None, db=None):
    """`ma.py kb search Q --json`: {rule: [...], knowledge: [...], chunk: [...]} (a megadott körök)."""
    from . import kb
    res = kb.search(query, limit, db, scopes, source)
    if source and res.get("rule"):
        # a szabályok forrásai vesszős listában (source_ids): csak azok, amelyek erre a forrásra hivatkoznak
        res["rule"] = [r for r in res["rule"]
                       if source in [x.strip() for x in (r.get("source_ids") or "").split(",")]]
    return _jsonable(res)


def kb_show(item_id, db=None):
    """`ma.py kb show ID`: a tétel minden mezője (None, ha nincs ilyen)."""
    from . import kb
    item = kb.show(item_id, db)
    return None if item is None else _jsonable(item)


def kb_rules(stage=None, agent=None, db=None):
    """`ma.py kb rules [--stage S] [--agent A] --json`. A szakasz lehet tartomány (S07-S12) vagy FINAL (= S14);
    ismeretlen szakasz: UnknownStage."""
    from . import kb
    wanted = [None]
    if stage:
        stages = kb.stage_ids(db)
        try:
            wanted = [_FINAL_STAGE if st == _projekt.FINAL else st for st in _projekt.parse_stage(stage)]
        except ValueError:
            wanted = [kb.normalize_stage(stage)]
        if not wanted or any(st not in stages for st in wanted):
            raise UnknownStage("Ismeretlen szakasz: %s. Elérhető: %s, tartomány (pl. S01-S02) vagy FINAL (= %s)" % (
                stage, ", ".join(stages) or "–", _FINAL_STAGE))
    return _jsonable([r for st in wanted for r in kb.rules(st, agent, db)])


def kb_checklist(name, db=None):
    """`ma.py kb checklist NÉV --json` (üres lista: nincs ilyen ellenőrzőlista)."""
    from . import kb
    return _jsonable(kb.checklist(name, db))


def _field_tokens(field):
    """Egy eredmény- vagy opciómező keresett alakjai: a név, a CLI-kapcsolói és a kötőjeles alak."""
    toks = {field, "--" + field.replace("_", "-")}
    try:
        from . import spec as S
        for r in S.option_table():
            if r["key"] == field or r["dest"] == field:
                toks.update(r["flags"])
                toks.add(r["key"])
    except Exception:   # noqa: BLE001 — a mezőnév magában is kereshető
        pass
    return toks


_CTX_MODEL_RE = re.compile(r"(?:primary_model|options\.model)\s*==\s*'([A-Za-z_]+)'")
_CTX_KMIN_RE = re.compile(r"^\s*k\s*(?:≥|>=)\s*(\d+)", re.I)
_CTX_KMAX_RE = re.compile(r"(?<![A-Za-z0-9_])k\s*<\s*(\d+)")
_CTX_MEASURE_RE = re.compile(r"options\.measure\s+in\s+\(([^)]*)\)")


def kb_rule_applies(rule, context):
    """Illik-e a döntési szabály egy futás kontextusára (UX-06) — csak a szabály saját gépi feltételeiből:
    a machine_check-ben rögzített modell (``primary_model == 'fixed'``), az egyvizsgálatos visszaesés
    (``model_fallback.reason == 'k = 1'``), a feltétel k-küszöbe (elején ``K ≥ 10, …``; bárhol ``k < 10``) és a mértéklista
    (``options.measure in (SMD, COHEN_D)``). context: {model, k, measure} (bármelyik hiányozhat)."""
    if not context:
        return True
    mc = rule.get("machine_check") or ""
    cond = rule.get("title") or rule.get("condition") or ""
    model, k, measure = context.get("model"), context.get("k"), context.get("measure")
    if model:
        need = set(_CTX_MODEL_RE.findall(mc))
        if need and model not in need:
            return False
    if isinstance(k, int) and not isinstance(k, bool):
        if "'k = 1'" in mc and k > 1:
            return False
        m = _CTX_KMIN_RE.match(cond)
        if m and k < int(m.group(1)):
            return False
        m = _CTX_KMAX_RE.search(cond)                  # „… k < 10 …”: kevés vizsgálatra szóló szabály
        if m and k >= int(m.group(1)):
            return False
    if measure:
        m = _CTX_MEASURE_RE.search(mc)
        if m:
            allowed = {x.strip().strip("'\"").upper() for x in m.group(1).split(",")}
            if str(measure).upper() not in allowed:
                return False
    return True


def kb_rules_for_field(field, db=None, context=None):
    """A mezőre (results.json-kulcs, elemzési opció vagy V/P/X-kód) hivatkozó KB-döntési szabályok (a felület
    ⓚ-jelvényei, 3.5.7): {field, items: [{id, stage_id, strength, title, recommendation, machine_check}]}. A
    machine_check tokenjei közül egyezik: a mező neve, a CLI-kapcsolója, vagy pontozott útvonal eleme
    (pl. 'options.model', 'random.tau2'). context ({model, k, measure}, egy futásé): csak a futásra illő
    szabályok (kb_rule_applies) — pl. véletlen hatású, k = 13 futásnál nincs fix hatású vagy k = 1-es szabály."""
    from . import kb
    if not isinstance(field, str) or not re.fullmatch(r"[A-Za-z0-9_.\-]{1,80}", field):
        raise ValueError("érvénytelen mezőnév: %r" % (field,))
    want = _field_tokens(field)
    db = db or kb.DEFAULT_DB
    kb.ensure_built(db)
    con = kb.connect(db, readonly=True)
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT rule_id, stage_id, strength, condition, recommendation, machine_check FROM decision_rule "
            "WHERE machine_check IS NOT NULL ORDER BY stage_id, rule_id")]
    finally:
        con.close()
    items = []
    for r in rows:
        toks = re.findall(r"[A-Za-z0-9_.\-]+", r["machine_check"] or "")
        hit = False
        for t in toks:
            t = t.strip(".")
            parts = t.split(".")
            if t in want or field in parts:
                hit = True
                break
        if hit or r["rule_id"] == field:
            it = {"id": r["rule_id"], "stage_id": r["stage_id"], "strength": r["strength"],
                  "title": r["condition"], "recommendation": r["recommendation"], "machine_check": r["machine_check"]}
            if kb_rule_applies(it, context):
                items.append(it)
    return {"schema": KB_RULES_SCHEMA, "field": field, "items": items}
