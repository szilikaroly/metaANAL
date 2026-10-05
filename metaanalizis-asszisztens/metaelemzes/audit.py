# -*- coding: utf-8 -*-
"""Projekt-audit: kereszt-artefaktum X-szabályok a projektmappán (szk.ma.project-audit/v1; terv 6.4, 4.15; E8).

Az X-szabályok a fájlok ÖSSZHANGJÁT ellenőrzik (adattábla ↔ eredet-oldalfájl ↔ spec ↔ commit-futás ↔ értékelés ↔
PRISMA), nem az adat helyességét (az a V-szabályoké). A kódok — a V- és P-szabályokhoz hasonlóan — tudásbázis-
azonosítók: a RULES szótár formátuma a validate.RULES-é (súlyosság, cím, teendő, forrás), a szakaszt a RULE_STAGES,
a kapcsolódó KB-szabályokat a KB_REFS adja.

A projektmappa (terv 2.4; minden fájl opcionális — ami hiányzik, azt a szabály nem tudja ellenőrizni, és ezt a
kimenet `not_checked` listája indokkal sorolja fel, találat helyett):

  ma-projekt.json                          kimenetek (id, data, measure, primary_spec)
  projekt.sqlite                           ellenőrzőpontok (szakasz-kontextus), döntések (X016)
  02_szures/prisma_flow.json | prisma_folyamat.md     bevont vizsgálatok száma (I; X014)
  03_adatok/<kimenet>.csv                  adattábla (row_uid, estimated, rob, forras_oldal …)
  03_adatok/<kimenet>.prov.json            szk.ma.provenance/v1 (X010, X013, X022)
  03_adatok/studies.json                   szk.ma.studies/v1 (I; vizsgálat-címkék)
  04_torzitas_kockazat/appraisals/*.json   szk.appraisal/v1 (X003)
  05_elemzes/specs/*.json                  szk.ma.analysis-spec/v1 (X005, X006, X016)
  05_elemzes/<kimenet>/<run_id>/run.json   szk.ma.run/v1 (+ results.json: a futás tényleges szűrői)

Belépési pontok: project_audit(mappa, stage=None) → szk.ma.project-audit/v1 szótár; audit_gate_errors(mappa) →
a FINAL audit-kaput elutasító (error szintű) találatok; require_gate(mappa) → ValueError, ha van ilyen;
format_text(jelentés) → a CLI szöveges kimenete; audit_schema() → a szerződés JSON Schemája.

Szakasz-kontextus: az X001 S08-tól hiba, előtte figyelmeztetés (ESCALATION). A szakasz a `stage` paraméter
(a FINAL kapu 'FINAL'-lal hív), ennek hiányában a projektnapló ellenőrzőpontjaiból adódik (a legnagyobb rögzített
szakasz; PASS / PASS_WITH_FIXES után a következő); napló vagy ellenőrzőpont nélkül ismeretlen — ilyenkor a
szigorúbb (RULES szerinti) súlyosság érvényes.
"""
import collections
import copy
import datetime
import hashlib
import json
import math
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

from . import __version__
from . import tableio
from .effect_sizes import REQUIRED_COLUMNS, PAIRED_INPUTS

SCHEMA = "szk.ma.project-audit/v1"
SCHEMA_ID = "urn:szk:contract:ma.project-audit:1"

# kód: (súlyosság, rövid cím, magyarázat/teendő, forrás) — a validate.RULES formátuma (a `kb build` betölti)
RULES = {
    "X001": ("error", "Elavult commit-futás: az adattábla a futás óta megváltozott",
             "A futás a tábla egy korábbi állapotából készült, így az eredményei — és minden rájuk hivatkozó GRADE-, "
             "SoF- és kéziratszám — nem a mostani adatot tükrözik. Futtasd újra ugyanazzal a speccel (ha a spec "
             "rögzíti a data.sha256-ot, előbb frissítsd), és a hivatkozásokat állítsd át az új futásra. Az S08 "
             "(szintézis) szakasztól hiba, előtte figyelmeztetés.", "engine"),
    "X003": ("error", "A tábla rob értéke eltér az értékelés végső összítéletétől",
             "A kinyerési tábla rob oszlopa nem egyezik a lezárt (konszenzusos) torzításikockázat-értékelés "
             "összítéletével, így a V019, a „magas RoB nélkül” érzékenységi futás és a GRADE RoB-doménje rossz "
             "értéket lát. Írd át a rob cellát az értékelés összítéletére (vagy javítsd az értékelést), majd "
             "futtasd újra az érintett elemzéseket.", "Cochrane Handbook 7–8; RoB 2 / ROBINS-I"),
    "X005": ("warning", "Becsült adatú sor van, de nincs „becsült nélkül” érzékenységi futás",
             "Futtass gyermek-futást a becsült / imputált sorok kizárásával (--exclude estimated=igen; specben: "
             "purpose sensitivity, parent az elsődleges spec), és közöld, változik-e a következtetés.",
             "Cochrane Handbook 6.5.2.10, 10.14"),
    "X006": ("warning", "Magas RoB-ú sor van, de nincs „magas RoB nélkül” érzékenységi futás",
             "Futtass gyermek-futást a magas torzítási kockázatú sorok kizárásával (--exclude rob=high; specben: "
             "purpose sensitivity, parent az elsődleges spec), és közöld az eredményt. A szűrőnek a mostani tábla "
             "minden magas RoB-ú sorát ki kell zárnia.", "Cochrane Handbook 7–8, 10.14"),
    "X010": ("warning", "Elemzett cellának nincs forrásoldala",
             "Minden elemzett számhoz rögzíts forráshelyet — oldalt vagy táblázat/ábra-lokátort az eredet-oldalfájl "
             "(.prov.json) source mezőjében, oldalfájl nélkül a forras_oldal oszlopban —, hogy az érték a "
             "közleményben visszakereshető és ellenőrizhető legyen.", "Cochrane Handbook 5; PRISMA 2020 9. tétel"),
    "X013": ("error", "A sor becsült-jelölése ellentmond a cellák eredetének",
             "Az eredet-oldalfájl (.prov.json) szerint a sor egy hatásméret-releváns cellája becsült / digitalizált "
             "/ imputált, de a sor estimated oszlopa nem 'igen' — vagy fordítva: a sor 'igen', de minden "
             "dokumentált cellája közölt érték. Hangold össze őket: az estimated oszlop vezérli a V018-at és az "
             "--exclude estimated=igen érzékenységi futást.", "Cochrane Handbook 6.5.2"),
    "X014": ("error", "Több elemzett vizsgálat, mint bevont vizsgálat (I)",
             "Az elemzett vizsgálatok (a commit-futás k-ja, illetve a tábla egyedi study_id-i) a bevont vizsgálatok "
             "(PRISMA I; studies.json) részhalmazai. Több karú vizsgálatnál a k a sorok száma, ilyenkor az egyedi "
             "study_id számít. Javítsd a studies.json-t, a PRISMA-számokat vagy az adattáblát.",
             "PRISMA 2020 1. ábra"),
    "X016": ("warning", "Protokoll-eltérés döntés nélkül: az elsődleges elemzés nem az előre rögzített",
             "Az elsődleges spec nincs előre rögzítve (prespecified: false), vagy eltér az előre rögzített spectől "
             "(modell, τ²-becslő, szűrők, adatfájl). Ha az eltérés indokolt, naplózd döntésként (project log <mappa> "
             "--agent … --stage S12 --kb X016,D-S12-006 --decision 'Protokoll-eltérés (<kimenet>): …' --rationale …), "
             "és a kéziratban közöld; különben az előre rögzített elemzés maradjon az elsődleges.",
             "PRISMA 2020 24c; Cochrane Handbook 10.14"),
    "X022": ("error", "Az eredet-oldalfájl nem a mostani adattáblához tartozik",
             "A .prov.json table_sha256-ja nem a mostani CSV hash-e (megszakadt kétfájlos írás vagy külső "
             "szerkesztés), és van olyan eredet-bejegyzés, amely nem egyeztethető a táblával (hiányzó sor vagy "
             "oszlop, eltérő érték). Ellenőrizd az érintett cellákat a forrással, majd mentsd újra az eredetet "
             "(mentéskor a table_sha256 frissül).", "engine"),
}

# kód → a szabály szakasza (a KB decision_rule.stage_id-je és a találat 'stage' mezője)
RULE_STAGES = {"X001": "S08", "X003": "S06", "X005": "S12", "X006": "S12", "X010": "S05", "X013": "S05",
               "X014": "S04", "X016": "S12", "X022": "S05"}

# kód → kapcsolódó tudásbázis-szabályok (a találat 'kb_refs' mezője)
KB_REFS = {"X001": (), "X003": ("D-S06-008", "D-S13-003"), "X005": ("D-S12-003", "D-S05-023"),
           "X006": ("D-S12-002",), "X010": ("D-S05-001", "D-S05-002"), "X013": ("D-S05-023",),
           "X014": ("P010",), "X016": ("D-S12-006", "D-S02-017"), "X022": ("D-S05-001",)}

# kód → a szakasz, amelytől a RULES szerinti súlyosság érvényes; előtte 'warning' (6.4: „error (S08-tól)”)
ESCALATION = {"X001": "S08"}

DATA_DIR = "03_adatok"
ANALYSIS_DIR = "05_elemzes"
SPEC_DIR = ANALYSIS_DIR + "/specs"
APPRAISAL_DIR = "04_torzitas_kockazat/appraisals"
STUDIES_FILE = DATA_DIR + "/studies.json"
PRISMA_JSON = "02_szures/prisma_flow.json"
PRISMA_MD = "02_szures/prisma_folyamat.md"
META_FILE = "ma-projekt.json"
JOURNAL_FILE = "projekt.sqlite"
FINAL = "FINAL"

SEVERITIES = ("error", "warning", "info")
_SEV_LABEL = {"error": "HIBA", "warning": "FIGYELEM", "info": "MEGJEGYZÉS"}
_ESTIMATED_METHODS = ("estimated", "digitized", "imputed")
_FINAL_STATUSES = ("complete", "final", "consensus")
# nem torzításikockázat-ítéletet adó eszközök (az összítéletük 'high' / 'low' mást jelent: bizalom, bizonyosság)
_NON_ROB_TOOLS = ("amstar", "grade", "tripod", "prisma", "nos", "newcastle")
_CONSENSUS = ("consensus", "konszenzus")
_DEVIATION_REFS = ("X016", "D-S12-006", "D-S02-017")
_DEVIATION_WORDS = re.compile(r"protokoll-?\s?elt[ée]r[ée]s|protocol deviation", re.I)
# az elemzés tartalmát nem érintő opciók (X016 összevetés)
_COSMETIC = ("title", "left_label", "right_label", "label_col", "plot_schema", "plot_locale", "svg_annotate")
# a forrásHELY oszlopa (a „forras” / „source” nem: D-S05-002 szerint a forrás TÍPUSA, pl. forras=absztrakt)
_PAGE_COLUMNS = ("forrasoldal", "forrashely", "oldal", "oldalszam", "sourcepage", "page", "pages", "locator")
_MAX_LIST = 500


# ------------------------------------------------------------------ segédek
def _now_utc(now=None):
    t = now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    if isinstance(t, str):
        return t
    if t.tzinfo is not None:
        t = t.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stage_index(stage):
    if stage is None:
        return None
    if stage == FINAL:
        return 99
    m = re.fullmatch(r"S(\d{2})", str(stage))
    return int(m.group(1)) if m else None


def _norm_stage(stage):
    """'S8', 's08', 'FINAL', 'S07-S09' (→ a legnagyobb) → 'S08' | 'FINAL'; None → None; érvénytelen → ValueError."""
    if stage is None or not str(stage).strip():
        return None
    from .projekt import parse_stage
    st = parse_stage(stage)
    return st[-1] if st else None


def severity_for(code, stage=None):
    """A szabály súlyossága a megadott szakasz-kontextusban (ismeretlen szakasz: a RULES szerinti)."""
    sev = RULES[code][0]
    start = ESCALATION.get(code)
    idx = _stage_index(stage)
    if start is not None and idx is not None and idx < _stage_index(start):
        return "warning"
    return sev


def _clean(obj):
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _short(h):
    return (h[:12] + "…") if isinstance(h, str) and len(h) > 12 else (h or "–")


def _fold(s):
    """Kis/nagybetű-, ékezet- és szóközfüggetlen összevetési kulcs."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", s).strip()


def _colkey(s):
    return re.sub(r"[^a-z0-9]", "", _fold(s))


def _mentions(text, name):
    return bool(name) and re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(name), text or "", re.I) is not None


def _relpath_ok(p):
    return isinstance(p, str) and bool(p) and re.fullmatch(r"(?!/)(?![A-Za-z]:)(?!.*\.\.)[^\\:]+", p) is not None


def _plural_list(items, limit=8):
    items = list(items)
    s = ", ".join(items[:limit])
    return s + (" … (+%d)" % (len(items) - limit) if len(items) > limit else "")


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _count(v):
    """Doboz-érték → nemnegatív egész vagy None (a PRISMA-fájlok számai; a hibás értéket a P001 jelzi)."""
    if isinstance(v, str) and re.fullmatch(r"\s*\d+\s*", v):
        return int(v)
    if isinstance(v, float) and v.is_integer() and v >= 0:
        return int(v)
    return v if _int(v) is not None and v >= 0 else None


class _Ctx(object):
    """Egy audit-futás állapota: a projektgyökér, a beolvasott fájlok hash-e, a nem ellenőrizhető tételek."""

    def __init__(self, root, stage):
        self.root = os.path.abspath(root)
        self.stage = stage
        self.inputs = {}
        self.not_checked = []
        self.findings = []
        self._json = {}
        self._tables = {}

    # utak: a projektgyökérhez relatív, '/'-elválasztós alak
    def abs(self, rel):
        if os.path.isabs(rel):
            return rel
        return os.path.join(self.root, *rel.split("/"))

    def rel(self, path):
        ap = os.path.abspath(path)
        try:
            r = os.path.relpath(ap, self.root)
        except ValueError:
            return ap
        if r == os.pardir or r.startswith(os.pardir + os.sep) or os.path.isabs(r):
            return ap
        return r.replace(os.sep, "/")

    def isfile(self, rel):
        return os.path.isfile(self.abs(rel))

    def read_bytes(self, rel):
        with open(self.abs(rel), "rb") as fh:
            raw = fh.read()
        self.inputs[self.rel(self.abs(rel))] = hashlib.sha256(raw).hexdigest()
        return raw

    def sha(self, rel):
        key = self.rel(self.abs(rel))
        if key not in self.inputs:
            try:
                self.read_bytes(rel)
            except OSError:
                return None
        return self.inputs.get(key)

    def load_json(self, rel, code=None, outcome=None, what=None):
        """JSON-fájl → objektum; hiányzó fájl → None (csendben); hibás → None + not_checked-tétel."""
        key = self.rel(self.abs(rel))
        if key in self._json:
            return self._json[key]
        obj = None
        try:
            raw = self.read_bytes(rel)
            obj = json.loads(raw.decode("utf-8-sig"), parse_constant=lambda name: None)
        except FileNotFoundError:
            obj = None
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self.skip(code, outcome, "%s nem olvasható%s: %s" % (key, (" (%s)" % what) if what else "",
                                                                   _exc_text(exc)))
            obj = None
        self._json[key] = obj
        return obj

    def table(self, rel, outcome=None, codes=()):
        """Adattábla → (sorok, meta, row_uid-ok) vagy None (hiányzó / olvashatatlan fájl: not_checked)."""
        key = self.rel(self.abs(rel))
        if key in self._tables:
            return self._tables[key]
        res = None
        if not self.isfile(rel):
            for c in codes:
                self.skip(c, outcome, "az adattábla nem található: %s" % key)
        else:
            try:
                raw = self.read_bytes(rel)
                rows, meta = tableio.read_table_bytes(raw, key)
                res = (rows, meta, tableio.row_uids(rows, meta))
            except Exception as exc:     # a hibás tábla az auditot nem állíthatja le
                for c in codes:
                    self.skip(c, outcome, "az adattábla nem olvasható (%s): %s" % (key, _exc_text(exc)))
        self._tables[key] = res
        return res

    def skip(self, code, outcome, reason):
        item = {"code": code, "outcome": outcome, "reason": reason}
        if item not in self.not_checked:
            self.not_checked.append(item)

    def add(self, code, outcome, detail, artifacts=(), suggested_command=None, **extra):
        f = collections.OrderedDict()
        f["code"] = code
        f["severity"] = severity_for(code, self.stage)
        f["stage"] = RULE_STAGES[code]
        f["outcome"] = outcome
        f["title"] = RULES[code][1]
        f["detail"] = detail
        f["advice"] = RULES[code][2]
        f["source"] = RULES[code][3]
        f["artifacts"] = list(dict.fromkeys(a for a in artifacts if _relpath_ok(a)))   # 4.0: projekt-relatív utak
        f["suggested_command"] = list(suggested_command) if suggested_command else None
        f["kb_refs"] = list(KB_REFS.get(code, ()))
        for k, v in extra.items():
            if v is not None:
                f[k] = v[:_MAX_LIST] if isinstance(v, list) else v
        self.findings.append(f)


def _exc_text(exc):
    return "%s: %s" % (type(exc).__name__, exc)


# ------------------------------------------------------------------ a projekt beolvasása
class _Outcome(object):
    def __init__(self, oid):
        self.id = oid
        self.data = None
        self.measure = None
        self.primary_spec = None
        self.runs = []
        self.specs = []


def _load_meta(ctx):
    if not ctx.isfile(META_FILE):
        return None
    from . import projekt
    try:
        meta = projekt.load_project_meta(ctx.root)
        ctx.sha(META_FILE)
        return meta
    except (ValueError, OSError) as exc:
        ctx.skip(None, None, "%s érvénytelen, a kimenetek a többi fájlból adódnak: %s" % (META_FILE, exc))
        raw = ctx.load_json(META_FILE)
        return raw if isinstance(raw, dict) else None


def _load_specs(ctx):
    out = []
    d = ctx.abs(SPEC_DIR)
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.lower().endswith(".json"):
            continue
        rel = SPEC_DIR + "/" + name
        doc = ctx.load_json(rel, what="elemzési spec")
        if not isinstance(doc, dict):
            continue
        data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
        filters = doc.get("filters") if isinstance(doc.get("filters"), dict) else {}
        out.append({"rel": rel, "doc": doc, "name": doc.get("name") if isinstance(doc.get("name"), str) else None,
                    "outcome": doc.get("outcome") if isinstance(doc.get("outcome"), str) else None,
                    "purpose": doc.get("purpose"), "prespecified": doc.get("prespecified"),
                    "parent": doc.get("parent"), "data_path": data.get("path"),
                    "filters": (_str_list(filters.get("exclude")), _str_list(filters.get("include")))})
    return out


def _str_list(v):
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _load_runs(ctx):
    """05_elemzes/<kimenet>/<futás>/run.json (csak commit-futások), időrendben."""
    out = []
    base = ctx.abs(ANALYSIS_DIR)
    if not os.path.isdir(base):
        return out
    for oid in sorted(os.listdir(base)):
        odir = os.path.join(base, oid)
        if oid == "specs" or not os.path.isdir(odir):
            continue
        for sub in sorted(os.listdir(odir)):
            rel = "%s/%s/%s/run.json" % (ANALYSIS_DIR, oid, sub)
            if not ctx.isfile(rel):
                continue
            doc = ctx.load_json(rel, what="futás-leíró")
            if not isinstance(doc, dict):
                continue
            if doc.get("mode", "commit") != "commit":
                continue
            spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
            data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
            out.append({"rel": rel, "dir": "%s/%s/%s" % (ANALYSIS_DIR, oid, sub), "outcome": oid, "doc": doc,
                        "run_id": doc.get("run_id") if isinstance(doc.get("run_id"), str) else sub,
                        "spec_name": spec.get("name") if isinstance(spec.get("name"), str) else None,
                        "spec_path": spec.get("path") if isinstance(spec.get("path"), str) else None,
                        "data_path": data.get("path") if isinstance(data.get("path"), str) else None,
                        "data_sha": data.get("sha256") if isinstance(data.get("sha256"), str) else None,
                        "k": _int(doc.get("k")),
                        "sort": (str(doc.get("started") or ""), str(doc.get("run_id") or ""), sub)})
    out.sort(key=lambda r: r["sort"])
    return out


def _run_results(ctx, run):
    """A futás results.json-ja: (exclude, include, measure) — a futás TÉNYLEGES szűrői."""
    if "results" not in run:
        res = ctx.load_json(run["dir"] + "/results.json", what="futás-eredmény")
        excl, incl, measure = None, None, None
        if isinstance(res, dict):
            inp = res.get("input") if isinstance(res.get("input"), dict) else {}
            if isinstance(inp.get("filters"), dict):
                excl, incl = _str_list(inp["filters"].get("exclude")), _str_list(inp["filters"].get("include"))
            opts = res.get("options") if isinstance(res.get("options"), dict) else {}
            measure = opts.get("measure") if isinstance(opts.get("measure"), str) else None
        run["results"] = (excl, incl, measure)
    return run["results"]


def _run_filters(ctx, run, specs_by_path):
    """A futás szűrői: results.json input.filters, ennek hiányában a futás specje."""
    excl, incl, _ = _run_results(ctx, run)
    if excl is not None or incl is not None:
        return excl or [], incl or []
    sp = specs_by_path.get(run["spec_path"]) if run["spec_path"] else None
    if sp is not None:
        return sp["filters"]
    return None


def _latest_runs(runs):
    """Futás-csoportonként (spec neve vagy útja) a legutolsó commit-futás."""
    last = collections.OrderedDict()
    for r in runs:
        last[r["spec_name"] or r["spec_path"] or r["dir"]] = r
    return list(last.values())


def _outcomes(ctx, meta, specs, runs):
    outs = collections.OrderedDict()

    def get(oid):
        if oid not in outs:
            outs[oid] = _Outcome(oid)
        return outs[oid]
    if isinstance(meta, dict) and isinstance(meta.get("outcomes"), list):
        for o in meta["outcomes"]:
            if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not o["id"]:
                continue
            oc = get(o["id"])
            oc.data = o.get("data") if _relpath_ok(o.get("data")) else None
            oc.measure = o.get("measure") if isinstance(o.get("measure"), str) else None
            oc.primary_spec = o.get("primary_spec") if _relpath_ok(o.get("primary_spec")) else None
    for sp in specs:
        if sp["outcome"]:
            get(sp["outcome"]).specs.append(sp)
    for r in runs:
        get(r["outcome"]).runs.append(r)
    for oc in outs.values():
        _resolve_outcome(ctx, oc)
    d = ctx.abs(DATA_DIR)
    if os.path.isdir(d):
        covered = {os.path.normcase(os.path.normpath(ctx.abs(_prov_rel(oc.data)))) for oc in outs.values()}
        for name in sorted(os.listdir(d)):
            if name.endswith(".prov.json") and os.path.normcase(os.path.join(d, name)) not in covered:
                oid = name[:-len(".prov.json")]
                if oid not in outs:
                    _resolve_outcome(ctx, get(oid))
    return list(outs.values())


def _prov_rel(data):
    """03_adatok/<kimenet>.csv → 03_adatok/<kimenet>.prov.json"""
    return (data[:-4] if data.lower().endswith(".csv") else data) + ".prov.json"


def _resolve_outcome(ctx, oc):
    """A kimenet adatfájlja és hatásmértéke, ha a ma-projekt.json nem adja meg: elsődleges spec, többi spec,
    legutóbbi futás, végül 03_adatok/<kimenet>.csv."""
    specs = _primary_candidates(oc) + oc.specs
    if oc.data is None:
        oc.data = next((sp["data_path"] for sp in specs if _relpath_ok(sp["data_path"])), None)
    if oc.data is None:
        oc.data = next((ctx.rel(ctx.abs(r["data_path"])) for r in reversed(oc.runs)
                        if r["data_path"] and ctx.isfile(r["data_path"])), None)
    if oc.data is None:
        oc.data = "%s/%s.csv" % (DATA_DIR, oc.id)
    if oc.measure is None:
        opts = [sp["doc"].get("options") for sp in specs]
        oc.measure = next((o["measure"] for o in opts if isinstance(o, dict) and isinstance(o.get("measure"), str)),
                          None)
    if oc.measure is None:
        oc.measure = next((m for m in (_run_results(ctx, r)[2] for r in reversed(oc.runs)) if m), None)


def _primary_candidates(oc):
    if oc.primary_spec:
        return [sp for sp in oc.specs if sp["rel"] == oc.primary_spec]
    return [sp for sp in oc.specs if sp["purpose"] == "primary"]


def _same_path(ctx, a, b):
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(ctx.abs(a))) == os.path.normcase(os.path.normpath(ctx.abs(b)))


def _journal(ctx):
    """(checkpointok, aktív döntések) a projektnaplóból, csak olvasva; napló nélkül (None, None)."""
    p = ctx.abs(JOURNAL_FILE)
    if not os.path.isfile(p):
        return None, None
    ctx.sha(JOURNAL_FILE)
    try:
        con = sqlite3.connect(Path(p).as_uri() + "?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error as exc:
        ctx.skip(None, None, "%s nem nyitható meg: %s" % (JOURNAL_FILE, _exc_text(exc)))
        return None, None
    try:
        con.row_factory = sqlite3.Row
        cps = [dict(r) for r in con.execute("SELECT stage_id, verdict FROM checkpoint ORDER BY id")]
        decs = [dict(r) for r in con.execute(
            "SELECT id, stage_id, decision, rationale, alternatives, kb_refs FROM decision "
            "WHERE status = 'active' ORDER BY id")]
        return cps, decs
    except sqlite3.Error as exc:
        ctx.skip(None, None, "%s nem olvasható: %s" % (JOURNAL_FILE, _exc_text(exc)))
        return None, None
    finally:
        con.close()


def _journal_stage(checkpoints):
    """A projekt elért szakasza az ellenőrzőpontokból: a legnagyobb rögzített szakasz, PASS / PASS_WITH_FIXES
    után a következő (legfeljebb S14); FINAL-ellenőrzőpont után FINAL; ellenőrzőpont nélkül None."""
    best = None
    for cp in checkpoints or ():
        sid = str(cp.get("stage_id") or "").strip().upper()
        if sid == FINAL:
            return FINAL
        m = re.fullmatch(r"S(\d{2})", sid)
        if not m:
            continue
        i = int(m.group(1))
        if cp.get("verdict") in ("PASS", "PASS_WITH_FIXES"):
            i = min(i + 1, 14)
        best = i if best is None else max(best, i)
    return None if best is None else "S%02d" % best


# ------------------------------------------------------------------ közös tábla-segédek
def _row_label(row, i):
    lab = row.get("study")
    return str(lab) if lab not in (None, "") else "#%d" % (i + 1)


def _columns(meta):
    mapping = meta.get("mapping") or {}
    return [mapping.get(c, c) for c in meta.get("columns") or []]


def _relevant_fields(measure, meta):
    """A hatásméret-releváns kanonikus oszlopok (a tábla sorrendjében); ismeretlen mértéknél minden számoszlop."""
    m = (measure or "").upper()
    if m in REQUIRED_COLUMNS:
        f = set(REQUIRED_COLUMNS[m])
        if m == "GEN":
            f |= {"vi", "sei"}
        if m in ("MC", "SMCC"):
            f |= {c for alts in PAIRED_INPUTS.values() for alt in alts for c in alt}
    else:
        f = set(tableio.NUMERIC) - {"year"}
    return [c for c in dict.fromkeys(_columns(meta)) if c in f]


def _present(v):
    return v is not None and not (isinstance(v, str) and not v.strip())


def _prov(ctx, oc, codes):
    """A kimenet eredet-oldalfájlja → (rel, dokumentum, {(row_uid, field): cella}) vagy None."""
    rel = _prov_rel(oc.data)
    if not ctx.isfile(rel):
        for c in codes:
            ctx.skip(c, oc.id, "nincs eredet-oldalfájl (%s)" % rel)
        return None
    doc = ctx.load_json(rel, codes[0] if codes else None, oc.id, "eredet-oldalfájl")
    if not isinstance(doc, dict) or not isinstance(doc.get("cells"), list):
        if doc is not None:
            for c in codes:
                ctx.skip(c, oc.id, "%s nem szk.ma.provenance/v1 (hiányzó cells lista)" % rel)
        return None
    index = {}
    for cell in doc["cells"]:
        if isinstance(cell, dict) and isinstance(cell.get("row_uid"), str) and isinstance(cell.get("field"), str):
            index[(cell["row_uid"], cell["field"])] = cell
    return rel, doc, index


def _cell_estimated(cell):
    return cell.get("estimated") is True or cell.get("method") in _ESTIMATED_METHODS


def _cell_located(cell):
    src = cell.get("source") if isinstance(cell.get("source"), dict) else {}
    page = src.get("page")
    if _int(page) is not None and page >= 1:
        return True
    if isinstance(page, str) and page.strip():
        return True
    if isinstance(src.get("locator"), str) and src["locator"].strip():
        return True
    # számított cella: a forrása az átváltás bemenete (convert-request a conversion mezőben)
    return cell.get("method") == "calculated" and isinstance(cell.get("conversion"), dict) and bool(cell["conversion"])


# ------------------------------------------------------------------ X001
def _x001(ctx, oc):
    if not oc.runs:
        ctx.skip("X001", oc.id, "nincs commit-futás (%s/%s/*/run.json)" % (ANALYSIS_DIR, oc.id))
        return
    primary = {sp["name"] for sp in _primary_candidates(oc)}
    for run in _latest_runs(oc.runs):
        if not run["data_path"] or not run["data_sha"]:
            ctx.skip("X001", oc.id, "%s: hiányzik a data.path vagy a data.sha256" % run["rel"])
            continue
        data_rel = ctx.rel(ctx.abs(run["data_path"]))
        now = ctx.sha(run["data_path"]) if ctx.isfile(run["data_path"]) else None
        if now == run["data_sha"].lower():
            continue
        if run["spec_name"]:
            which = "%s spec (%s) legutóbbi commit-futásának (%s)" % (
                "Az elsődleges" if run["spec_name"] in primary else "A(z)", run["spec_name"], run["run_id"])
        else:
            which = "A(z) %s commit-futás" % run["run_id"]
        if now is None:
            detail = "%s adatfájlja már nem létezik: %s" % (which, data_rel)
        else:
            detail = "%s adat-hash-e %s, a %s mostani hash-e %s" % (which, _short(run["data_sha"]), data_rel,
                                                                     _short(now))
        ctx.add("X001", oc.id, detail, [run["rel"], data_rel], _rerun_command(run), run_id=run["run_id"])


def _rerun_command(run):
    if run["spec_path"]:
        return ["ma.py", "analyze", "--spec", run["spec_path"], "--project", "."]
    argv = run["doc"].get("equivalent_argv")
    if isinstance(argv, list) and all(isinstance(x, str) for x in argv) and argv:
        out, skip = [], False
        for x in argv:
            if skip:
                skip = False
                continue
            if x == "--out":
                skip = True
                continue
            if x.startswith("--out="):
                continue
            out.append(x)
        return out
    return None


# ------------------------------------------------------------------ X005 / X006
_CHILD = {"X005": ("estimated", "estimated=igen", "becsult_nelkul", "becsült"),
          "X006": ("rob", "rob=high", "magas_rob_nelkul", "magas RoB-ú")}


def _flagged_rows(code, rows):
    if code == "X005":
        return [i for i, r in enumerate(rows) if tableio.yes_no(r.get("estimated")) == "yes"]
    return [i for i, r in enumerate(rows) if tableio.rob_category(r.get("rob")) == "high"]


def _removes_all(filters, column, rows, meta, flagged):
    """A szűrők (exclude, include) a mostani táblán minden jelölt sort kizárnak, marad sor, és van köztük az
    oszlopra (estimated / rob) vonatkozó szűrő."""
    excl, incl = filters
    refs = False
    for cond in list(excl) + list(incl):
        col = str(cond).partition("=")[0]
        try:
            if tableio.resolve_column(col, meta, rows) == column:
                refs = True
        except ValueError:
            return False
    if not refs:
        return False
    try:
        kept = tableio.apply_filters(rows, excl or None, incl or None, meta=meta)
    except ValueError:
        return False
    kept_ids = {id(r) for r in kept}
    return bool(kept) and not any(id(rows[i]) in kept_ids for i in flagged)


def _x005_x006(ctx, oc, code, tab, specs_by_path):
    column, filt, suffix, what = _CHILD[code]
    rows, meta, uids = tab
    flagged = _flagged_rows(code, rows)
    if not flagged:
        return
    runs = [r for r in _latest_runs(oc.runs) if r["data_path"] is None or _same_path(ctx, r["data_path"], oc.data)]
    for r in runs:
        f = _run_filters(ctx, r, specs_by_path)
        if f is not None and _removes_all(f, column, rows, meta, flagged):
            return
    pending = [sp["name"] or sp["rel"] for sp in oc.specs
               if _removes_all(sp["filters"], column, rows, meta, flagged)]
    labels = [_row_label(rows[i], i) for i in flagged]
    detail = "%d %s sor (%s), de egyik commit-futás szűrője sem zárja ki mindet" % (
        len(flagged), what, _plural_list(labels))
    if pending:
        detail += "; a(z) %s spec megvan, de nincs (érvényes) commit-futása" % _plural_list(pending)
    ctx.add(code, oc.id, detail, [oc.data] + [sp["rel"] for sp in oc.specs if (sp["name"] or sp["rel"]) in pending],
            _child_command(ctx, oc, filt, suffix), row_uids=[uids[i] for i in flagged])


def _parent_spec(ctx, oc, specs_by_path):
    prim = _primary_candidates(oc)
    if len(prim) == 1:
        return prim[0]["doc"]
    for r in reversed(oc.runs):
        sp = specs_by_path.get(r["spec_path"]) if r["spec_path"] else None
        if sp is not None and sp["purpose"] in (None, "primary"):
            return sp["doc"]
    return None


def _child_command(ctx, oc, filt, suffix):
    """Javasolt parancs a gyermek-futáshoz: az elsődleges spec + a kizáró szűrő (spec.argv_from_spec)."""
    specs_by_path = {sp["rel"]: sp for sp in oc.specs}
    parent = _parent_spec(ctx, oc, specs_by_path)
    if parent is not None and isinstance(parent.get("name"), str):
        child = copy.deepcopy(parent)
        name = re.sub(r"[^a-z0-9_-]", "_", ("%s_%s" % (parent["name"], suffix)).lower())[:64]
        child.update({"name": name, "purpose": "sensitivity", "parent": parent["name"]})
        child.pop("prespecified", None)
        f = child.get("filters") if isinstance(child.get("filters"), dict) else {}
        child["filters"] = {"exclude": _str_list(f.get("exclude")) + [filt], "include": _str_list(f.get("include"))}
        try:
            from . import spec as S
            return ["ma.py"] + S.argv_from_spec(child, project_root=ctx.root,
                                                out_dir=ctx.abs("%s/%s/%s" % (ANALYSIS_DIR, oc.id, name)),
                                                log_to_project=True)
        except Exception:    # érvénytelen spec vagy betöltési hiba: tartalék parancs
            pass
    if oc.measure and _relpath_ok(oc.data):
        return ["ma.py", "analyze", "--data", oc.data, "--measure", oc.measure.upper(), "--exclude", filt,
                "--out", "%s/%s/%s" % (ANALYSIS_DIR, oc.id, suffix), "--project", "."]
    return None


# ------------------------------------------------------------------ X010 / X013
def _x010(ctx, oc, tab, prov):
    rows, meta, uids = tab
    fields = _relevant_fields(oc.measure, meta)
    if prov is not None:
        rel, _, index = prov
        missing = []
        for i, (r, u) in enumerate(zip(rows, uids)):
            for f in fields:
                if _present(r.get(f)):
                    cell = index.get((u, f))
                    if cell is None or not _cell_located(cell):
                        missing.append((i, u, f))
        if missing:
            nrows = len({m[0] for m in missing})
            by_row = collections.OrderedDict()
            for i, u, f in missing:
                by_row.setdefault(i, []).append(f)
            ctx.add("X010", oc.id, "%d elemzett cellának nincs forrásoldala (%d sor): %s" % (
                len(missing), nrows, _plural_list("%s (%s)" % (_row_label(rows[i], i), ", ".join(fs))
                                                  for i, fs in by_row.items())),
                [oc.data, rel], None, cells=[{"row_uid": u, "field": f} for _, u, f in missing])
        return
    mapping = meta.get("mapping") or {}
    page_col = next((mapping.get(c, c) for c in meta.get("columns") or [] if _colkey(c) in _PAGE_COLUMNS), None)
    if page_col is None:
        ctx.skip("X010", oc.id, "nincs eredet-oldalfájl és forras_oldal oszlop sem (%s)" % oc.data)
        return
    bad = [i for i, r in enumerate(rows) if any(_present(r.get(f)) for f in fields) and not _present(r.get(page_col))]
    if bad:
        ctx.add("X010", oc.id, "%d sorban üres a '%s' oszlop (eredet-oldalfájl nincs): %s" % (
            len(bad), page_col, _plural_list(_row_label(rows[i], i) for i in bad)), [oc.data], None,
            row_uids=[uids[i] for i in bad])


def _x013(ctx, oc, tab, prov):
    if prov is None:
        return
    rows, meta, uids = tab
    rel, _, index = prov
    fields = _relevant_fields(oc.measure, meta)
    has_col = "estimated" in set(_columns(meta))
    wrong_no, wrong_yes = [], []
    for i, (r, u) in enumerate(zip(rows, uids)):
        present = [f for f in fields if _present(r.get(f))]
        cells = {f: index.get((u, f)) for f in present}
        est = [f for f, c in cells.items() if c is not None and _cell_estimated(c)]
        flag = tableio.yes_no(r.get("estimated")) == "yes"
        if est and not flag:
            wrong_no.append((i, est))
        elif flag and present and all(c is not None for c in cells.values()) and not est:
            wrong_yes.append(i)
    if not wrong_no and not wrong_yes:
        return
    parts = []
    if wrong_no:
        parts.append("eredet szerint becsült, de a sor estimated jelzője %s: %s" % (
            "nem 'igen'" if has_col else "hiányzik (nincs estimated oszlop)",
            _plural_list("%s (%s)" % (_row_label(rows[i], i), ", ".join(fs)) for i, fs in wrong_no)))
    if wrong_yes:
        parts.append("a sor estimated = 'igen', de minden dokumentált cellája közölt érték: %s" %
                     _plural_list(_row_label(rows[i], i) for i in wrong_yes))
    ctx.add("X013", oc.id, "; ".join(parts), [oc.data, rel], None,
            row_uids=[uids[i] for i, _ in wrong_no] + [uids[i] for i in wrong_yes])


# ------------------------------------------------------------------ X022
def _values_match(row_value, entered, field):
    if entered is None:
        return True
    try:
        na = tableio.parse_number(entered) is None
    except ValueError:
        na = False
    if row_value is None or (isinstance(row_value, str) and not row_value.strip()):
        return na or not str(entered).strip()
    if na:
        return False
    return tableio.values_equal(row_value, entered, field)


def _x022(ctx, oc, tab, prov):
    if prov is None:
        return
    rel, doc, index = prov
    want = doc.get("table_sha256")
    if not isinstance(want, str) or not want:
        ctx.skip("X022", oc.id, "%s: hiányzik a table_sha256" % rel)
        return
    now = ctx.sha(oc.data)
    if now == want.lower():
        return
    if tab is None:
        ctx.add("X022", oc.id, "a %s eredet-oldalfájl táblája (%s) %s; egyik cella sem egyeztethető" % (
            rel, oc.data, "nem olvasható" if now else "nem található"), [rel, oc.data], None)
        return
    rows, meta, uids = tab
    cols = set(_columns(meta))
    by_uid = dict(zip(uids, rows))
    bad = []
    for (u, f), cell in index.items():
        if u not in by_uid:
            bad.append((u, f, "nincs ilyen sor"))
        elif f not in cols:
            bad.append((u, f, "nincs ilyen oszlop"))
        elif not _values_match(by_uid[u].get(f), cell.get("value_as_entered"), f):
            bad.append((u, f, "eltérő érték (eredet: %r)" % (cell.get("value_as_entered"),)))
    if not bad:
        return
    label = {u: _row_label(r, i) for i, (u, r) in enumerate(zip(uids, rows))}
    ctx.add("X022", oc.id, "a %s table_sha256-ja %s, a %s mostani hash-e %s; %d nem egyeztethető cella: %s" % (
        rel, _short(want), oc.data, _short(now), len(bad),
        _plural_list("%s/%s — %s" % (label.get(u, u), f, why) for u, f, why in bad)),
        [rel, oc.data], None, cells=[{"row_uid": u, "field": f} for u, f, _ in bad])


# ------------------------------------------------------------------ X003
def _appraisals(ctx):
    """Lezárt értékelések: [{'rel', 'study', 'tool', 'outcome', 'category', 'raw', 'consensus', 'updated'}]."""
    d = ctx.abs(APPRAISAL_DIR)
    if not os.path.isdir(d):
        ctx.skip("X003", None, "nincs értékelés-mappa (%s)" % APPRAISAL_DIR)
        return None
    out = []
    for name in sorted(os.listdir(d)):
        if not name.lower().endswith(".json"):
            continue
        rel = APPRAISAL_DIR + "/" + name
        doc = ctx.load_json(rel, "X003", None, "értékelés")
        if not isinstance(doc, dict) or doc.get("schema") not in (None, "szk.appraisal/v1"):
            continue
        status = str(doc.get("status") or "").strip().lower()
        if status not in _FINAL_STATUSES:
            continue
        target = doc.get("target") if isinstance(doc.get("target"), dict) else {}
        overall = doc.get("overall") if isinstance(doc.get("overall"), dict) else {}
        stem = name[:-5].split(".")
        tool = doc.get("tool") if isinstance(doc.get("tool"), str) else (stem[1] if len(stem) > 1 else "")
        if _colkey(tool).startswith(_NON_ROB_TOOLS):
            continue
        study = target.get("study_id") if isinstance(target.get("study_id"), str) else stem[0]
        raw = overall.get("judgement")
        if not isinstance(raw, str) or not raw.strip():
            continue
        cat = tableio.rob_category(raw)
        if not cat:
            ctx.skip("X003", None, "%s: az összítélet (%r) nem RoB-kategória" % (rel, raw))
            continue
        assessor = _fold(doc.get("assessor"))
        out.append({"rel": rel, "study": study, "tool": tool,
                    "outcome": target.get("outcome") if isinstance(target.get("outcome"), str) else None,
                    "category": cat, "raw": raw,
                    "consensus": status == "consensus" or assessor in _CONSENSUS or "consensus" in stem[2:],
                    "updated": str(doc.get("updated") or "")})
    if not out:
        ctx.skip("X003", None, "nincs lezárt (status: complete) értékelés (%s)" % APPRAISAL_DIR)
    return out


def _final_judgements(ctx, appraisals, oid):
    """vizsgálat → (kategória, nyers ítélet, [fájlok]) a kimenetre vonatkozó lezárt értékelésekből."""
    groups = collections.OrderedDict()
    for a in appraisals:
        if a["outcome"] is not None and a["outcome"] != oid:
            continue
        groups.setdefault(_fold(a["study"]), []).append(a)
    out = {}
    for key, items in groups.items():
        cons = [a for a in items if a["consensus"]]
        if cons:
            pick = sorted(cons, key=lambda a: a["updated"])[-1:]
        elif len({a["category"] for a in items}) == 1:
            pick = items
        else:
            ctx.skip("X003", oid, "%s: a lezárt értékelések összítélete eltér, konszenzus nincs (%s)" % (
                items[0]["study"], ", ".join(a["rel"] for a in items)))
            continue
        out[key] = (pick[0]["category"], pick[0]["raw"], [a["rel"] for a in items])
    return out


def _study_keys(row, label_to_id):
    keys = []
    for v in (row.get("study_id"), row.get("study")):
        if _present(v):
            keys.append(_fold(v))
    if _present(row.get("study")) and _fold(row.get("study")) in label_to_id:
        keys.append(label_to_id[_fold(row.get("study"))])
    return keys


def _x003(ctx, oc, tab, appraisals, label_to_id):
    rows, meta, uids = tab
    finals = _final_judgements(ctx, appraisals, oc.id)
    if not finals:
        return
    if "rob" not in set(_columns(meta)):
        ctx.skip("X003", oc.id, "az adattáblában nincs rob oszlop (%s)" % oc.data)
        return
    bad, files = [], []
    for i, (r, u) in enumerate(zip(rows, uids)):
        hit = next((finals[k] for k in _study_keys(r, label_to_id) if k in finals), None)
        if hit is None:
            continue
        cat, raw, rels = hit
        if tableio.rob_category(r.get("rob")) != cat:
            cell = r.get("rob")
            bad.append((i, u, "%s: rob = %s, az értékelés összítélete: '%s'" % (
                _row_label(r, i), ("'%s'" % cell) if _present(cell) else "üres", raw)))
            files += rels
    if bad:
        ctx.add("X003", oc.id, "; ".join(b[2] for b in bad[:20]) + (" … (+%d)" % (len(bad) - 20) if len(bad) > 20
                                                                     else ""),
                [oc.data] + files, None, row_uids=[b[1] for b in bad])


# ------------------------------------------------------------------ X014
def _included_studies(ctx):
    """(I, forrás) — studies.json, ennek hiányában a PRISMA-fájlok; nincs adat: (None, None)."""
    st = ctx.load_json(STUDIES_FILE, "X014", None, "vizsgálat-jegyzék")
    if isinstance(st, dict) and isinstance(st.get("studies"), list):
        return len(st["studies"]), STUDIES_FILE
    from . import prisma
    flow = ctx.load_json(PRISMA_JSON, "X014", None, "PRISMA-folyamat")
    if isinstance(flow, dict):
        i = _count(prisma.normalize(flow)[0].get("included_studies"))
        if i is not None:
            return i, PRISMA_JSON
    if ctx.isfile(PRISMA_MD):
        try:
            text = ctx.read_bytes(PRISMA_MD).decode("utf-8-sig")
            i = _count(prisma.parse_markdown_table(text).get("included_studies"))
        except (OSError, UnicodeDecodeError, ValueError):
            i = None
        if i is not None:
            return i, PRISMA_MD
    return None, None


def _x014(ctx, oc, tab, included):
    n_i, src = included
    if n_i is None:
        ctx.skip("X014", oc.id, "a bevont vizsgálatok száma (I) ismeretlen (%s, %s vagy %s)" % (
            STUDIES_FILE, PRISMA_JSON, PRISMA_MD))
        return
    problems, artifacts = [], [src]
    n_sid = None
    if tab is not None and "study_id" in set(_columns(tab[1])):
        n_sid = len({_fold(r.get("study_id")) for r in tab[0] if _present(r.get("study_id"))})
        if n_sid > n_i:
            problems.append("az adattábla egyedi study_id-jainak száma %d > I = %d" % (n_sid, n_i))
            artifacts.append(oc.data)
    latest = [r for r in _latest_runs(oc.runs) if r["k"] is not None]
    if latest:
        top = max(latest, key=lambda r: r["k"])
        if top["k"] > n_i and (n_sid is None or n_sid > n_i):
            problems.append("a(z) %s commit-futás k = %d > I = %d" % (top["run_id"], top["k"], n_i))
            artifacts.append(top["rel"])
    elif n_sid is None:
        ctx.skip("X014", oc.id, "nincs commit-futás és study_id oszlop sem")
    if problems:
        ctx.add("X014", oc.id, "%s (I forrása: %s)" % ("; ".join(problems), src), artifacts, None)


# ------------------------------------------------------------------ X016
def _analysis_content(sp):
    """A spec elemzési tartalma összevetéshez: (adatfájl, opciók a DEFAULTS-szal kiegészítve, szűrők)."""
    doc = sp["doc"]
    opts = doc.get("options") if isinstance(doc.get("options"), dict) else {}
    try:
        from .pipeline import DEFAULTS
        full = dict(DEFAULTS)
    except Exception:
        full = {}
    full.update(opts)
    if isinstance(full.get("measure"), str):
        full["measure"] = full["measure"].upper()
    for k in _COSMETIC:
        full.pop(k, None)
    excl, incl = sp["filters"]
    return sp["data_path"], full, (sorted(excl), sorted(incl))


def _content_diff(a, b):
    """Az eltérések szövege: 'tau2: DL → REML'."""
    (pa, oa, fa), (pb, ob, fb) = _analysis_content(a), _analysis_content(b)
    out = []
    if pa != pb:
        out.append("adat: %s → %s" % (pa, pb))
    for k in list(oa) + [k for k in ob if k not in oa]:
        va, vb = oa.get(k), ob.get(k)
        if va != vb:
            out.append("%s: %s → %s" % (k, "alapérték" if va is None else json.dumps(va, ensure_ascii=False),
                                        "alapérték" if vb is None else json.dumps(vb, ensure_ascii=False)))
    for name, x, y in (("kizárás", fa[0], fb[0]), ("szűkítés", fa[1], fb[1])):
        if x != y:
            out.append("%s: %s → %s" % (name, ", ".join(x) or "–", ", ".join(y) or "–"))
    return out


def _deviation_decided(decisions, oc, primary, single):
    for d in decisions or ():
        refs = {x.strip() for x in re.split(r"[,;|\s]+", d.get("kb_refs") or "") if x.strip()}
        text = " ".join(str(d.get(k) or "") for k in ("decision", "rationale", "alternatives"))
        if not (refs & set(_DEVIATION_REFS) or _DEVIATION_WORDS.search(text)):
            continue
        if single or _mentions(text, oc.id) or _mentions(text, primary["name"]):
            return d
    return None


def _x016(ctx, oc, decisions, single):
    prim = _primary_candidates(oc)
    if oc.primary_spec and not prim:
        ctx.skip("X016", oc.id, "a ma-projekt.json primary_spec fájlja nem található (%s)" % oc.primary_spec)
        return
    if len(prim) > 1:
        ran = [sp for r in reversed(oc.runs) for sp in prim if r["spec_name"] == sp["name"]]
        if not ran:
            ctx.skip("X016", oc.id, "több elsődleges spec (%s), és egyiknek sincs commit-futása" % ", ".join(
                sp["rel"] for sp in prim))
            return
        prim = ran[:1]
    if not prim:
        if oc.specs:
            ctx.skip("X016", oc.id, "nincs elsődleges spec (purpose: primary vagy ma-projekt.json primary_spec)")
        return
    p = prim[0]
    prespec = [sp for sp in oc.specs if sp["prespecified"] is True and sp["purpose"] in (None, "primary")
               and sp is not p]
    reasons = []
    if p["prespecified"] is not True:
        same = [q for q in prespec if not _content_diff(q, p)]
        differing = [q for q in prespec if _content_diff(q, p)]
        if same:
            return
        if differing:
            q = differing[0]
            reasons.append("az elsődleges %s eltér az előre rögzített %s spectől: %s" % (
                p["rel"], q["rel"], "; ".join(_content_diff(q, p))))
        elif p["prespecified"] is False:
            reasons.append("az elsődleges %s nincs előre rögzítve (prespecified: false)" % p["rel"])
        else:
            ctx.skip("X016", oc.id, "nincs előre rögzített spec (prespecified: true), és az elsődleges %s sem jelöli "
                     "(prespecified hiányzik)" % p["rel"])
            return
    if not reasons:
        return
    if decisions is None:
        reasons.append("projektnapló (%s) nélkül döntés nem ellenőrizhető" % JOURNAL_FILE)
    elif _deviation_decided(decisions, oc, p, single) is not None:
        return
    else:
        reasons.append("a naplóban nincs rá döntés (kb: %s)" % ", ".join(_DEVIATION_REFS[:2]))
    ctx.add("X016", oc.id, "; ".join(reasons), [p["rel"]] + [q["rel"] for q in prespec], None, spec=p["name"])


# ------------------------------------------------------------------ fő belépési pont
def project_audit(project_dir, stage=None, now=None):
    """A projektmappa X-szabályai → szk.ma.project-audit/v1 szótár.

    stage: a szakasz-kontextus ('S08', 'FINAL' …; None: a projektnapló ellenőrzőpontjaiból). now: az
    időbélyeg (datetime vagy kész szöveg; teszthez). A hiányzó vagy hibás opcionális fájl nem hiba: az érintett
    szabály kimarad, az ok a 'not_checked' listába kerül."""
    if not os.path.isdir(project_dir):
        raise FileNotFoundError("Nincs ilyen projektmappa: %s" % project_dir)
    ctx = _Ctx(project_dir, None)
    checkpoints, decisions = _journal(ctx)
    ctx.stage = _norm_stage(stage) if stage is not None else _journal_stage(checkpoints)
    meta = _load_meta(ctx)
    specs = _load_specs(ctx)
    runs = _load_runs(ctx)
    outcomes = _outcomes(ctx, meta, specs, runs)
    specs_by_path = {sp["rel"]: sp for sp in specs}
    if not outcomes:
        ctx.skip(None, None, "a projektben nincs kimenet (%s, %s, %s/<kimenet>/*/run.json vagy %s/*.prov.json)" % (
            META_FILE, SPEC_DIR, ANALYSIS_DIR, DATA_DIR))
    appraisals = _appraisals(ctx) if outcomes else None
    included = _included_studies(ctx) if outcomes else (None, None)
    st = ctx.load_json(STUDIES_FILE)
    label_to_id = {}
    if isinstance(st, dict) and isinstance(st.get("studies"), list):
        for s in st["studies"]:
            if isinstance(s, dict) and _present(s.get("label")) and _present(s.get("study_id")):
                label_to_id[_fold(s["label"])] = _fold(s["study_id"])
    single = len(outcomes) == 1
    for oc in outcomes:
        _x001(ctx, oc)
        tab = ctx.table(oc.data, oc.id, ("X003", "X005", "X006", "X010", "X013"))
        prov = _prov(ctx, oc, ("X013", "X022") if tab is not None else ())
        if tab is not None:
            if appraisals:
                _x003(ctx, oc, tab, appraisals, label_to_id)
            _x005_x006(ctx, oc, "X005", tab, specs_by_path)
            _x005_x006(ctx, oc, "X006", tab, specs_by_path)
            _x010(ctx, oc, tab, prov)
            _x013(ctx, oc, tab, prov)
        _x014(ctx, oc, tab, included)
        _x016(ctx, oc, decisions, single)
        _x022(ctx, oc, tab, prov)
    order = {o.id: i for i, o in enumerate(outcomes)}
    findings = sorted(ctx.findings, key=lambda f: (SEVERITIES.index(f["severity"]), f["code"],
                                                   order.get(f["outcome"], -1)))
    summary = collections.OrderedDict((s, sum(1 for f in findings if f["severity"] == s)) for s in SEVERITIES)
    rep = collections.OrderedDict()
    rep["schema"] = SCHEMA
    rep["engine_version"] = __version__
    rep["project"] = os.path.basename(os.path.normpath(os.path.abspath(project_dir)))
    rep["title"] = meta.get("title") if isinstance(meta, dict) and isinstance(meta.get("title"), str) else None
    rep["generated"] = _now_utc(now)
    rep["stage"] = ctx.stage
    rep["summary"] = summary
    rep["findings"] = findings
    rep["not_checked"] = ctx.not_checked
    rep["rules_checked"] = sorted(RULES)
    rep["outcomes"] = [o.id for o in outcomes]
    rep["inputs"] = collections.OrderedDict(sorted((k, v) for k, v in ctx.inputs.items() if _relpath_ok(k)))
    rep["amstar2_hints"] = {}
    return _clean(rep)


def audit_gate_errors(project_dir):
    """A FINAL audit-kaput elutasító találatok (error szintű X-találatok FINAL szakasz-kontextusban)."""
    return [f for f in project_audit(project_dir, stage=FINAL)["findings"] if f["severity"] == "error"]


def gate_message(errors):
    """A FINAL audit-kapu elutasításának szövege (üres lista: None)."""
    if not errors:
        return None
    groups = collections.OrderedDict()
    for f in errors:
        groups.setdefault((f["code"], f.get("outcome")), []).append(f)
    return ("%d hiba szintű X-szabály találat van a projekt-auditban, ezért a FINAL ellenőrzőpont nem adható: %s "
            "(részletek: ma.py project audit <mappa>)" % (len(errors), "; ".join(
                "%s%s%s: %s" % (code, (" [%s]" % oid) if oid else "", (" (%d×)" % len(fs)) if len(fs) > 1 else "",
                                fs[0]["title"]) for (code, oid), fs in groups.items())))


def require_gate(project_dir):
    """'checkpoint --stage FINAL --audit-gate': ValueError, ha van error szintű X-találat."""
    msg = gate_message(audit_gate_errors(project_dir))
    if msg:
        raise ValueError(msg)


# ------------------------------------------------------------------ megjelenítés, metaadat, séma
def format_text(rep):
    """A jelentés szöveges alakja (ma.py project audit <mappa>)."""
    s = rep["summary"]
    lines = ["Projekt-audit (%s; szakasz: %s): %d hiba, %d figyelmeztetés, %d megjegyzés" % (
        rep["project"], rep.get("stage") or "ismeretlen", s["error"], s["warning"], s["info"])]
    for f in rep["findings"]:
        lines.append("[%s] %-10s %s — %s: %s" % (f["code"], _SEV_LABEL[f["severity"]], f.get("outcome") or "(projekt)",
                                                 f["title"], f["detail"]))
        if f.get("suggested_command"):
            lines.append("    javasolt parancs: %s" % " ".join(_quote(x) for x in f["suggested_command"]))
        if f.get("kb_refs"):
            lines.append("    KB: %s" % ", ".join(f["kb_refs"]))
    if rep.get("not_checked"):
        lines.append("Nem ellenőrizhető:")
        for n in rep["not_checked"]:
            lines.append("  - %s%s: %s" % (n.get("code") or "audit", (" [%s]" % n["outcome"]) if n.get("outcome")
                                           else "", n["reason"]))
    if not rep["findings"]:
        lines.append("Nincs X-szabály találat (ellenőrzött szabályok: %s)." % ", ".join(rep["rules_checked"]))
    return "\n".join(lines)


def _quote(s):
    return s if re.fullmatch(r"[\w./=:,@+-]+", s or "") else "'%s'" % str(s).replace("'", "'\\''")


def to_json(rep):
    """Szigorú JSON (NaN/Infinity nélkül), UTF-8 szövegként."""
    return json.dumps(_clean(rep), ensure_ascii=False, indent=1, allow_nan=False)


def rules_table():
    """A szabályok metaadata (rules export, KB): [{code, severity, stage, title, advice, source, kb_refs,
    escalation}]."""
    return [{"code": c, "severity": RULES[c][0], "stage": RULE_STAGES[c], "title": RULES[c][1],
             "advice": RULES[c][2], "source": RULES[c][3], "kb_refs": list(KB_REFS.get(c, ())),
             "escalation": ({"from": ESCALATION[c], "before": "warning"} if c in ESCALATION else None)}
            for c in sorted(RULES)]


def audit_schema():
    """A szk.ma.project-audit/v1 JSON Schema (2020-12)."""
    sev = {"enum": list(SEVERITIES)}
    count = {"type": "integer", "minimum": 0}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": SCHEMA_ID,
        "title": "Projekt-audit (X-szabályok; ma.py project audit <mappa> --json)", "type": "object",
        "required": ["schema", "project", "generated", "summary", "findings"],
        "properties": {
            "schema": {"const": SCHEMA},
            "engine_version": {"type": "string"},
            "project": {"type": "string"},
            "title": {"type": ["string", "null"]},
            "generated": {"type": "string"},
            "stage": {"type": ["string", "null"], "pattern": r"^(S\d{2}|FINAL)$"},
            "summary": {"type": "object", "required": list(SEVERITIES),
                        "properties": {s: count for s in SEVERITIES}},
            "findings": {"type": "array", "items": {
                "type": "object",
                "required": ["code", "severity", "stage", "outcome", "title", "detail", "artifacts",
                             "suggested_command", "kb_refs"],
                "properties": {
                    "code": {"type": "string", "pattern": r"^X\d{3}$"}, "severity": sev,
                    "stage": {"type": "string", "pattern": r"^S\d{2}$"},
                    "outcome": {"type": ["string", "null"]}, "title": {"type": "string"},
                    "detail": {"type": "string"}, "advice": {"type": "string"}, "source": {"type": "string"},
                    "artifacts": {"type": "array", "items": {"type": "string"}},
                    "suggested_command": {"type": ["array", "null"], "items": {"type": "string"}},
                    "kb_refs": {"type": "array", "items": {"type": "string"}},
                    "run_id": {"type": "string"}, "spec": {"type": ["string", "null"]},
                    "row_uids": {"type": "array", "items": {"type": "string"}},
                    "cells": {"type": "array", "items": {"type": "object", "required": ["row_uid", "field"],
                                                         "properties": {"row_uid": {"type": "string"},
                                                                        "field": {"type": "string"}}}}}}},
            "not_checked": {"type": "array", "items": {"type": "object", "required": ["code", "outcome", "reason"],
                                                       "properties": {"code": {"type": ["string", "null"]},
                                                                      "outcome": {"type": ["string", "null"]},
                                                                      "reason": {"type": "string"}}}},
            "rules_checked": {"type": "array", "items": {"type": "string"}},
            "outcomes": {"type": "array", "items": {"type": "string"}},
            "inputs": {"type": "object", "additionalProperties": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
                       "description": "a beolvasott fájlok sha256-ja (projekt-relatív út → hash; gyorsítótár-kulcs)"},
            "amstar2_hints": {"type": "object"},
        }}


def cli_main(project_dir, as_json=False, stage=None, out=None):
    """'ma.py project audit <mappa> [--json] [--stage S]' → kilépési kód (1, ha van error szintű találat)."""
    import sys
    out = out or sys.stdout
    rep = project_audit(project_dir, stage=stage)
    out.write((to_json(rep) if as_json else format_text(rep)) + "\n")
    return 1 if rep["summary"]["error"] else 0
