# -*- coding: utf-8 -*-
"""Determinisztikus audit-csomag (ZIP) — ``szk.ma.audit-bundle/v1`` (terv 3.5.17, 4.17, 7.4, 7.7).

Egy bírálónak / társszerzőnek / a jövőbeli önmagadnak szóló, ellenőrizhető csomag: a projekt
döntései, specjei, commit-futásai, eredet-adatai, PRISMA-ja, a hash-láncolt tevékenységnapló és az
abból készült újrafuttató szkriptek (``rerun.cmd`` / ``rerun.sh``), egy ``manifest.json``-nal:

    {schema, project, created, data_class, redactions[], versions{engine, gui, selftest{checks, fail},
     plugins{…}}, files[{path, sha256, bytes, role}], runs[{run_id, outcome_id, data_sha256, spec_sha256}],
     activity_head, activity_records, rerun_scripts["rerun.cmd", "rerun.sh"], kb_snapshot{ids[], db_sha256},
     excluded[{pattern, reason}], policy{include, redact}}

**Determinizmus:** rendezett bejegyzések, rögzített időbélyeg (1980-01-01 00:00:00), ``ZIP_DEFLATED``,
rögzített jogosultság és rendszer-azonosító; a ``created`` a projektállapot ideje (a legkésőbbi
rögzített esemény, nem a falióra; ``SOURCE_DATE_EPOCH`` felülírja). Ugyanaz a projektállapot ugyanazt a
bájtsort adja.

**Soha nem kerül bele** (7.4, 7.7): a ``_privat/`` (C osztály), C osztályú projektből adattábla, PDF
(csak a dokumentum-azonosító + sha256, az eredet-adatban az oldal), a KB teljes szövege (csak az
azonosítók és az adatbázis sha256-ja), korábbi audit-csomagok és pillanatképek. B osztálynál az
adattáblák alapból kimaradnak; adattábla nélkül az eredet-adat bevitt értékei és az ábra-adat nyers
cellaoszlopai is. A kitakarási kapcsolók azonosak a pillanatképéivel (``snapshot.resolve_policy``).

A ``07_ellenorzes/activity.jsonl`` változatlanul kerül bele, hogy a hash-lánca ellenőrizhető maradjon.

Parancssor: ``python ma.py gui audit-export --project <mappa> [--out x.zip] [--redact …] [--keep …]``
(belépési pont: ``main(argv)``; ``python -m ma_gui.audit_export`` is). Csak stdlib.
"""
import argparse
import collections
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

if __package__ in (None, ""):                      # python ma_gui/audit_export.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "ma_gui"                          # noqa: A001

from . import __version__, activity, privacy, snapshot, store  # noqa: E402

SCHEMA = "szk.ma.audit-bundle/v1"
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644
OUT_DIR_REL = "07_ellenorzes/audit"
RERUN_SCRIPTS = ("rerun.cmd", "rerun.sh")
TEXT_SUFFIXES = (".json", ".md", ".txt", ".csv", ".tsv", ".svg", ".cmd", ".sh")
FIGURE_SUFFIXES = (".svg", ".png")
MAX_FILE_BYTES = 64 * 1024 * 1024

ROLES = ("manifest", "project", "log", "audit", "spec", "run", "results", "report", "plot", "figure",
         "provenance", "studies", "prisma", "appraisal", "sof", "activity", "rerun", "data", "grade", "headhunter")
# Metaheadhunter: a bekerülő állapotfájlok (a döntésnapló hash-lánca változatlanul); a gyorsítótár, a futás-naplók és
# a forrás-áttekintések kinyert szövegrészei (idézetek, jogvédett teljes szövegből) nem
HH_DIR = "01_kereses/headhunter"
HH_FILES = ("state.json", "studies.json", "merged.json", "prisma_flow.json", "update_search.json", "overlap.json")


_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_SHA_OR_NULL = {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"}
_I18N = {"type": "object", "required": ["hu", "en"],
         "properties": {"hu": {"type": "string"}, "en": {"type": "string"}, "key": {"type": "string"}},
         "additionalProperties": False}
_TOGGLE_MAP = {"type": "object", "additionalProperties": {"type": "boolean"}}
# A manifest.json sémája (schema_lite-részhalmaz; a tesztek ezzel ellenőrzik a csomagot).
MANIFEST_SCHEMA = {
    "type": "object",
    "required": ["schema", "project", "created", "data_class", "redactions", "versions", "files", "runs",
                 "activity_head", "rerun_scripts", "kb_snapshot", "excluded"],
    "properties": {
        "schema": {"const": SCHEMA},
        "project": {"type": "string", "pattern": "^[a-z0-9-]{1,48}$"},
        "title": {"type": "string"},
        "created": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$"},
        "data_class": {"enum": ["A", "B", "C"]},
        "policy": {"type": "object", "required": ["include", "redact"],
                   "properties": {"include": _TOGGLE_MAP, "redact": _TOGGLE_MAP}, "additionalProperties": False},
        "redactions": {"type": "array", "items": _I18N},
        "notes": {"type": "array", "items": _I18N},
        "versions": {"type": "object", "required": ["engine", "selftest", "plugins"],
                     "properties": {
                         "engine": {"type": ["string", "null"]}, "gui": {"type": ["string", "null"]},
                         "selftest": {"oneOf": [{"type": "null"}, {
                             "type": "object", "required": ["checks", "fail"],
                             "properties": {"checks": {"type": ["integer", "null"], "minimum": 0},
                                            "fail": {"type": ["integer", "null"], "minimum": 0},
                                            "state": {"type": ["string", "null"]}},
                             "additionalProperties": False}]},
                         "plugins": {"type": "object", "additionalProperties": {
                             "type": "object", "properties": {"version": {"type": ["string", "null"]},
                                                              "state": {"type": ["string", "null"]}}}}},
                     "additionalProperties": False},
        "files": {"type": "array", "items": {
            "type": "object", "required": ["path", "sha256", "bytes", "role"],
            "properties": {"path": {"type": "string", "pattern": "^(?!/)(?!.*\\.\\.)(?!_privat/)[^\\\\:]+$"},
                           "sha256": _SHA, "bytes": {"type": "integer", "minimum": 0},
                           "role": {"enum": ["project", "log", "audit", "spec", "run", "results", "report", "plot",
                                             "figure", "provenance", "studies", "prisma", "appraisal", "sof",
                                             "activity", "rerun", "data", "grade", "headhunter"]}},
            "additionalProperties": False}},
        "runs": {"type": "array", "items": {
            "type": "object", "required": ["run_id", "outcome_id", "data_sha256", "spec_sha256"],
            "properties": {"run_id": {"type": "string"}, "outcome_id": {"type": ["string", "null"]},
                           "data_sha256": _SHA_OR_NULL, "spec_sha256": _SHA_OR_NULL},
            "additionalProperties": False}},
        "activity_head": _SHA_OR_NULL,
        "activity_seq": {"type": ["integer", "null"], "minimum": 1},
        "activity_records": {"type": "integer", "minimum": 0},
        "rerun_scripts": {"type": "array", "items": {"enum": ["rerun.cmd", "rerun.sh"]}},
        "kb_snapshot": {"type": "object", "required": ["ids", "db_sha256"],
                        "properties": {"ids": {"type": "array", "items": {"type": "string", "maxLength": 80}},
                                       "db_sha256": _SHA_OR_NULL},
                        "additionalProperties": False},
        "excluded": {"type": "array", "items": {
            "type": "object", "required": ["pattern", "reason"],
            "properties": {"pattern": {"type": "string"}, "reason": _I18N}, "additionalProperties": False}},
    },
    "additionalProperties": False,
}


class AuditExportError(snapshot.SnapshotError):
    pass


def _reason(hu, en):
    return collections.OrderedDict((("hu", hu), ("en", en)))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _is_private(rel):
    first = rel.replace("\\", "/").split("/", 1)[0]
    return first.casefold() == privacy.PRIVATE_DIR.casefold()


class _Bundle(object):
    """A csomag tartalma: projekt-relatív út → (bájtok, szerep); a kizárások indoklással."""

    def __init__(self, root, pol, scrub):
        self.root = Path(root)
        self.pol = pol
        self.scrub = scrub
        self.files = collections.OrderedDict()
        self.excluded = collections.OrderedDict()
        self.notes = []

    def exclude(self, pattern, hu, en):
        self.excluded.setdefault(pattern, _reason(hu, en))

    def add_bytes(self, rel, data, role, scrub_text=True):
        rel = rel.replace("\\", "/")
        if _is_private(rel):
            self.exclude(privacy.PRIVATE_DIR + "/**", "C osztály / bizalmas adat — soha", "class C / confidential data — never")
            return False
        if rel.lower().endswith(".pdf"):
            return False
        if scrub_text and self.scrub is not None and rel.lower().endswith(TEXT_SUFFIXES):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if text is not None:
                new = self.scrub(text)
                if new != text:
                    data = new.encode("utf-8")
        self.files[rel] = (data, role)
        return True

    def add_json(self, rel, obj, role):
        data = (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
        return self.add_bytes(rel, data, role)

    def private_hit(self, rel):
        """A fájl a _privat/ alatt van-e — az írt út VAGY a linkek feloldása utáni valódi helye szerint (WS-6:
        egy 03_adatok/x.csv → ../_privat/titkos.csv link is privát). Találatnál a kizárás a manifesztbe kerül."""
        if _is_private(rel) or privacy.is_private_location(self.root, rel):
            self.exclude(privacy.PRIVATE_DIR + "/**", "C osztály / bizalmas adat — soha", "class C / confidential data — never")
            if not _is_private(rel):
                self.exclude(rel, "a _privat/ alá mutató hivatkozás (link) — soha",
                             "link pointing into _privat/ — never")
            return True
        return False

    def add_file(self, rel, role, transform=None):
        rel = rel.replace("\\", "/")
        if self.private_hit(rel):
            return False
        try:
            path = store.resolve_under(self.root, rel)
        except Exception:                               # noqa: BLE001 — a projekten kívülre mutató út
            return False
        if not path.is_file():
            return False
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                self.exclude(rel, "túl nagy fájl (64 MB felett)", "file too large (over 64 MB)")
                return False
            data = path.read_bytes()
        except OSError:
            return False
        if transform is not None:
            data = transform(data)
            if data is None:
                return False
        return self.add_bytes(rel, data, role)

    def glob(self, pattern):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.glob(pattern) if p.is_file())


def _json_transform(fn):
    def t(data):
        try:
            obj = json.loads(data.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            return None
        obj = fn(obj)
        if obj is None:
            return None
        return (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    return t


def _strip_prov(obj, pol):
    if not isinstance(obj, dict):
        return obj
    if not pol["include"]["data_tables"]:
        snapshot._strip_provenance_values({"provenance": obj})
    if pol["redact"]["quotes"]:
        for c in obj.get("cells") or []:
            if isinstance(c, dict):
                for k in list(c.keys()):
                    if k.lower() in snapshot._QUOTE_KEYS:
                        c.pop(k, None)
                src = c.get("source")
                if isinstance(src, dict):
                    for k in list(src.keys()):
                        if k.lower() in snapshot._QUOTE_KEYS:
                            src.pop(k, None)
    return obj


def _strip_results_details(obj):
    """results.json adattábla nélkül: a validálási megállapítások ``detail`` mezője (cellaértékek, pl. „e1 = 98765,
    n1 = 123”) kiürül; a kód, a súlyosság, a vizsgálat és a teendő marad."""
    if not isinstance(obj, dict):
        return obj
    val = obj.get("validation") if isinstance(obj.get("validation"), dict) else {}
    for f in val.get("findings") or []:
        if isinstance(f, dict) and f.get("detail") not in (None, ""):
            f["detail"] = None
    return obj


_MD_SPLIT = re.compile(r"(?<!\\)\|")


def _redact_report_details(data):
    """report.md adattábla nélkül: az „Adatvalidálás” szakasz táblájában a „Részlet” oszlop (a motor
    cellaértékes részletei) helyén „(kitakarva)”; a többi sor és oszlop változatlan."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    out, in_section, col = [], False, None
    for line in text.split("\n"):
        if line.startswith("## "):
            in_section = line.strip() == "## Adatvalidálás"
            col = None
        elif in_section and line.startswith("|"):
            cells = _MD_SPLIT.split(line)
            if col is None:
                names = [c.strip() for c in cells]
                col = names.index("Részlet") if "Részlet" in names else -1
            elif col > 0 and len(cells) > col and not set(cells[col].strip()) <= set("-: "):
                cells[col] = " (kitakarva) "
                line = "|".join(cells)
        out.append(line)
    return "\n".join(out).encode("utf-8")


def _initials_keep_reserved(v):
    """Monogram, de a fenntartott értékelő-azonosítók (consensus, ai) nem nevek: változatlanok."""
    return v if not isinstance(v, str) or v in snapshot.RESERVED_RATERS else snapshot.initials(v)


def _strip_assessors(obj):
    def fn(k, v):
        lk = str(k).lower()
        if lk in ("actor", "resolved_actor") and isinstance(v, str):
            return snapshot._actor_initials(v)
        if lk in snapshot._ASSESSOR_KEYS or lk in snapshot._RATER_EXTRA_KEYS:
            if isinstance(v, list):
                return [_initials_keep_reserved(x) for x in v]
            if isinstance(v, dict) and all(isinstance(x, (str, type(None))) for x in v.values()):
                return collections.OrderedDict((kk, _initials_keep_reserved(x)) for kk, x in v.items())
            return _initials_keep_reserved(v)
        return v
    return snapshot._walk(obj, fn)


# értékelés-fájl útja bárhol egy szövegben (napló context.dataset, project_audit inputs-kulcs) → az értékelő monogram
_APPRAISAL_PATH_RE = re.compile(r"(04_torzitas_kockazat/appraisals/(?:[A-Za-z0-9_-]+/)?[A-Za-z0-9_-]{1,64}"
                                r"\.[a-z0-9][a-z0-9-]{0,31}(?:\.[A-Za-z0-9][A-Za-z0-9_-]{0,63})?)"
                                r"\.([A-Za-z][A-Za-z0-9_-]{0,31})\.json")
# a korábbi felülbírálás-bejegyzések szövege: „… (értékelő: <név>)”
_NAMED_RATER_RE = re.compile(r"\((értékelő|rater|jóváhagyó|approver): ([^()\n]{1,64})\)")


def _rater_text(text):
    """SEC-5: szabad szövegben (napló, audit-kulcs) az értékelés-fájl útjának értékelő-része és a „(értékelő: név)”
    monogrammá — ugyanúgy, ahogy az értékelés-fájl neve a csomagban (_appraisal_rel)."""
    if not isinstance(text, str):
        return text
    text = _APPRAISAL_PATH_RE.sub(lambda m: "%s.%s.json" % (m.group(1), _initials_keep_reserved(m.group(2))), text)
    return _NAMED_RATER_RE.sub(lambda m: "(%s: %s)" % (m.group(1), _initials_keep_reserved(m.group(2).strip())), text)


def _rater_obj(obj):
    """_rater_text minden szöveges értékre ÉS szótárkulcsra (pl. project_audit 'inputs': {út: sha})."""
    if isinstance(obj, dict):
        return collections.OrderedDict((_rater_text(k), _rater_obj(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return [_rater_obj(x) for x in obj]
    return _rater_text(obj)


def _appraisal_rel(rel, redact_assessors):
    """04_…/appraisals/<vizsgálat>.<eszköz>[.<cél>].<értékelő>.json → az értékelő monogrammá (ha kell)."""
    if not redact_assessors:
        return rel
    head, _, name = rel.rpartition("/")
    parts = name.split(".")
    if len(parts) >= 4 and parts[-1].lower() == "json":
        parts[-2] = _initials_keep_reserved(parts[-2])
    return (head + "/" if head else "") + ".".join(parts)


def _kb_db_path(app):
    db = getattr(app, "kb_db", None) if app is not None else None
    if db:
        return db
    try:
        from metaelemzes import api as engine_api
        return engine_api.kb_status().get("db")
    except Exception:                                  # noqa: BLE001
        return None


def _plugins(app):
    """A pluginok (id → {version, state}) a már lefutott képesség-felderítésből; felderítést nem indít."""
    caps = getattr(app, "_caps", None) if app is not None else None
    if caps is None:
        return {}
    try:
        rep = caps.report()
    except Exception:                                  # noqa: BLE001
        return {}
    out = collections.OrderedDict()
    for c in sorted((rep or {}).get("components") or [], key=lambda x: str(x.get("id")) if isinstance(x, dict) else ""):
        if isinstance(c, dict) and c.get("id") not in (None, "engine", "metaelemzes"):
            out[str(c["id"])] = collections.OrderedDict((("version", c.get("version")), ("state", c.get("state"))))
    return out


def _runs(root, private_out=None):
    """Minden commit-futás a projektből: [{run_id, outcome_id, data_sha256, spec_sha256, dir}] időrendben. A
    _privat/ alól jövő futások (mappa, adatfájl vagy spec a _privat/ alatt, a linkek feloldása után is) kimaradnak
    — a manifeszt runs[] listájából is (7.4); a számukat a private_out lista kapja."""
    from metaelemzes import api as engine_api
    out = []
    for path, layout, folder, _logged in engine_api.project_run_files(str(root)):
        doc = snapshot._read_json(path)
        if not isinstance(doc, dict) or doc.get("mode", "commit") != "commit" or not doc.get("run_id"):
            continue
        if snapshot._fallback_run_private(root, doc, path):
            if private_out is not None:
                private_out.append(doc["run_id"])
            continue
        spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
        data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
        outcome = folder if layout == "nested" and folder else None
        if outcome is None and isinstance(spec.get("path"), str):
            sp = snapshot._read_json(os.path.join(str(root), *spec["path"].split("/")))
            outcome = sp.get("outcome") if isinstance(sp, dict) else None
        out.append({"run_id": doc["run_id"], "outcome_id": outcome or folder, "data_sha256": data.get("sha256"),
                    "spec_sha256": spec.get("sha256"), "dir": os.path.dirname(path),
                    "started": str(doc.get("started") or "")})
    out.sort(key=lambda r: (r["started"], r["run_id"]))
    return out


def _activity_records(root):
    log = activity.ActivityLog(str(root))
    try:
        records = log.read()
    except Exception:                                  # noqa: BLE001
        records = []
    try:
        head = log.head()
    except Exception:                                  # noqa: BLE001
        head = None
    return records, head


def collect(root, pol, app=None, environ=None):
    """A csomag tartalma (_Bundle) és a manifeszt alapadatai."""
    root = Path(os.path.realpath(str(root)))
    scrub = snapshot._path_scrubber(root, os.path.expanduser("~")) if pol["redact"]["abs_paths"] else None
    b = _Bundle(root, pol, scrub)
    inc = pol["include"]
    for pattern, (hu, en) in snapshot.HARD_EXCLUDED:
        b.exclude(pattern, hu, en)
    b.exclude(OUT_DIR_REL + "/**", "korábbi audit-csomagok", "earlier audit bundles")
    b.exclude("**/*" + snapshot.SNAPSHOT_SUFFIX, "pillanatképek (külön export)", "snapshots (separate export)")
    b.exclude("projekt.sqlite", "a napló a projekt/naplo.json + naplo.md exportban van (determinisztikus)",
              "the journal is in the projekt/naplo.json + naplo.md export (deterministic)")

    b.add_file("ma-projekt.json", "project", _json_transform(lambda o: _strip_assessors(o) if pol["redact"]["assessors"] else o))

    stamps = []
    # -- döntési napló (determinisztikus export), X-szabályok
    if inc["decisions"]:
        if (root / "projekt.sqlite").is_file():
            from metaelemzes import api as engine_api
            j = engine_api.project_export_json(str(root))
            if pol["redact"]["assessors"]:
                j = _rater_obj(_strip_assessors(j))
            stamps.append(j)
            b.add_json("projekt/naplo.json", j, "log")
            md = engine_api.project_export_markdown(str(root))
            if pol["redact"]["assessors"]:
                md = _rater_text(md)
            b.add_bytes("projekt/naplo.md", md.encode("utf-8"), "log")
    else:
        b.exclude("projekt/naplo.*", "döntési napló kikapcsolva", "decision log switched off")

    # -- specek, futások, ábrák
    private_runs = []
    runs = _runs(root, private_runs)
    if private_runs:
        b.exclude("_privat/05_elemzes/**", "a _privat/ alól jövő futások (mappa vagy adatfájl a _privat/ alatt) — soha",
                  "runs from _privat/ (folder or data file under _privat/) — never")
    stamps.append([{"started": r["started"]} for r in runs])
    if inc["specs_runs"]:
        for rel in b.glob("05_elemzes/specs/*.json"):
            b.add_file(rel, "spec")
        for r in runs:
            d = Path(r["dir"])
            if not store.is_within(str(d), str(root)):
                b.exclude(r["run_id"], "projekten kívüli futásmappa (csak a runs[] listában)",
                          "run folder outside the project (listed in runs[] only)")
                continue
            base = d.relative_to(root).as_posix()
            b.add_file(base + "/run.json", "run")
            if inc["data_tables"]:
                b.add_file(base + "/results.json", "results")
                b.add_file(base + "/report.md", "report")
            else:
                # adattábla nélkül a validálási megállapítások részletei (pl. „e1 = 98765, n1 = 123”) cellaértéket
                # hordoznak: a results.json-ban kiürítve, a report.md Adatvalidálás-táblájában a Részlet oszlop
                # kitakarva (PRIV-3)
                b.add_file(base + "/results.json", "results", _json_transform(_strip_results_details))
                b.add_file(base + "/report.md", "report", _redact_report_details)
            b.add_file(base + "/effect_sizes.csv", "results")
            if inc["figures"]:
                if inc["data_tables"]:
                    b.add_file(base + "/plot_data.json", "plot")
                else:
                    b.add_file(base + "/plot_data.json", "plot",
                               _json_transform(lambda o: (snapshot._strip_plot_cells(o), o)[1] if isinstance(o, dict) else o))
                for p in sorted(d.iterdir()):
                    if p.is_file() and p.suffix.lower() in FIGURE_SUFFIXES:
                        b.add_file(base + "/" + p.name, "figure")
    else:
        b.exclude("05_elemzes/**", "specek és futások kikapcsolva", "specs and runs switched off")
    if inc["figures"]:
        for rel in b.glob("06_kezirat/abrak/*"):
            if rel.lower().endswith(FIGURE_SUFFIXES):
                b.add_file(rel, "figure")
            elif rel.lower().endswith(".result.json"):
                b.add_file(rel, "figure")
    def not_private_run(data):
        """A fájl változatlanul — kivéve, ha egy _privat/ alól jövő futásra épül (akkor kimarad)."""
        try:
            o = json.loads(data.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            return data
        return None if isinstance(o, dict) and o.get("run_id") in private_runs else data
    for rel in b.glob("06_kezirat/sof/*.json"):
        b.add_file(rel, "sof", not_private_run)

    # -- adatok: eredet, tábla, studies
    if inc["provenance"]:
        for rel in b.glob("03_adatok/*.prov.json") + b.glob("03_adatok/**/*.prov.json"):
            if rel not in b.files:
                b.add_file(rel, "provenance", _json_transform(lambda o: _strip_prov(o, pol)))
    else:
        b.exclude("03_adatok/*.prov.json", "eredet-adatok kikapcsolva", "provenance switched off")
    if inc["data_tables"]:
        for rel in b.glob("03_adatok/*.csv") + b.glob("03_adatok/*.tsv"):
            b.add_file(rel, "data")
    if inc["prisma"]:
        b.add_file("02_szures/prisma_flow.json", "prisma")
        b.add_file("02_szures/prisma_folyamat.md", "prisma")
        b.add_file("03_adatok/studies.json", "studies")
        # Metaheadhunter (meglévő metaanalízisek bányászata): állapot, vizsgálat-térkép, egyesítés, PRISMA-folyam,
        # frissítő keresés, átfedés és a döntésnapló (hash-lánc: változatlanul)
        hh_tr = _json_transform(lambda o: _strip_assessors(o) if pol["redact"]["assessors"] else o)
        hh_any = False
        for name in HH_FILES:
            hh_any = b.add_file(HH_DIR + "/" + name, "headhunter", hh_tr) or hh_any
        if b.add_file(HH_DIR + "/decisions.jsonl", "headhunter"):
            hh_any = True
            if pol["redact"]["assessors"]:
                b.notes.append(_reason("A 01_kereses/headhunter/decisions.jsonl változatlan, hogy a hash-lánca "
                                       "ellenőrizhető maradjon (a döntéshozók ott nincsenek kitakarva).",
                                       "01_kereses/headhunter/decisions.jsonl is unchanged so that its hash chain stays "
                                       "verifiable (decision makers are not redacted there)."))
        if hh_any:
            b.exclude(HH_DIR + "/cache/**", "a Metaheadhunter API-gyorsítótára (gépállapot)",
                      "the Metaheadhunter API cache (machine state)")
            b.exclude(HH_DIR + "/runs/**", "a Metaheadhunter futás-naplói (gépállapot)",
                      "the Metaheadhunter run logs (machine state)")
            b.exclude(HH_DIR + "/reviews/**", "a forrás-áttekintésekből kinyert szövegrészek (jogvédett teljes szöveg "
                                              "idézetei) — az azonosítók a studies.json-ban",
                      "text extracted from source reviews (quotes of copyrighted full text) — identifiers are in "
                      "studies.json")
    else:
        b.exclude("02_szures/**", "PRISMA kikapcsolva", "PRISMA switched off")
    docs = snapshot._read_json(root / store.DOCUMENTS_REL) if (root / store.DOCUMENTS_REL).is_file() else None
    if isinstance(docs, dict) and isinstance(docs.get("docs"), list):
        reduced = {"schema": docs.get("schema"), "docs": docs["docs"]}
        snapshot._strip_documents(reduced)
        b.add_json(store.DOCUMENTS_REL, reduced, "provenance")

    # -- értékelések
    if inc["appraisals"]:
        for rel in b.glob("04_torzitas_kockazat/appraisals/*.json"):
            tr = _json_transform(lambda o: _strip_assessors(o) if pol["redact"]["assessors"] else o)
            if b.private_hit(rel):
                continue
            try:
                # egy szabálytalan fájlnév (pl. ':' a névben) vagy a projekten kívülre mutató link csak ezt az egy
                # fájlt hagyja ki, nem az egész exportot (WS-7)
                path = store.resolve_under(root, rel)
            except Exception:                           # noqa: BLE001 — store.Forbidden és társai
                b.exclude(rel, "szabálytalan fájlnév vagy a projekten kívülre mutató út — kihagyva",
                          "invalid file name or path pointing outside the project — skipped")
                continue
            try:
                data = tr(path.read_bytes())
            except OSError:
                data = None
            if data is not None:
                b.add_bytes(_appraisal_rel(rel, pol["redact"]["assessors"]), data, "appraisal")
        # GRADE-ítéletek kimenetenként (06_kezirat/grade/<kimenet>.grade.json; a SoF-fájlok külön, 'sof' szereppel);
        # a _privat/ alól jövő futásra épülő ítélet nem (a futás-azonosítója sem kerülhet a csomagba)
        def grade_tr(o):
            if isinstance(o, dict) and o.get("run_id") in private_runs:
                return None
            return _strip_assessors(o) if pol["redact"]["assessors"] else o
        for rel in b.glob("06_kezirat/grade/*.grade.json"):
            b.add_file(rel, "grade", _json_transform(grade_tr))
    else:
        b.exclude("04_torzitas_kockazat/appraisals/**", "értékelések kikapcsolva", "appraisals switched off")

    # -- tevékenységnapló (változatlanul) + újrafuttató szkriptek
    records, head = _activity_records(root)
    stamps.append(records)
    if inc["activity"]:
        b.add_file(activity.LOG_RELPATH, "activity")
        if pol["redact"]["assessors"] or pol["redact"]["abs_paths"]:
            b.notes.append(_reason("A 07_ellenorzes/activity.jsonl változatlan, hogy a hash-lánca ellenőrizhető maradjon "
                                   "(a szereplők és az utak ott nincsenek kitakarva).",
                                   "07_ellenorzes/activity.jsonl is unchanged so that its hash chain stays verifiable "
                                   "(actors and paths are not redacted there)."))
    else:
        b.exclude(activity.LOG_RELPATH, "tevékenységnapló kikapcsolva", "activity log switched off")
    if inc["rerun"]:
        b.add_bytes("rerun.sh", activity.render_rerun_sh(records, ".").encode("utf-8"), "rerun", scrub_text=False)
        b.add_bytes("rerun.cmd", activity.render_rerun_cmd(records, ".").encode("utf-8"), "rerun", scrub_text=False)

    # -- X-szabályok (a projektállapot idejével, hogy determinisztikus legyen)
    ts = snapshot.state_time(stamps, environ)
    if inc["decisions"]:
        try:
            from metaelemzes import api as engine_api
            rep = engine_api.project_audit(str(root), now=ts)
        except Exception:                              # noqa: BLE001 — a hiányzó audit a csomagot nem állítja meg
            rep = None
        if isinstance(rep, dict):
            if pol["redact"]["assessors"]:
                rep = _rater_obj(rep)
            b.add_json("audit/project_audit.json", rep, "audit")
    if not inc["data_tables"]:
        why = {"A": ("adattábla kikapcsolva", "data tables switched off"),
               "B": ("B osztály: adattábla alapból ki", "class B: data tables off by default"),
               "C": ("C osztály: adattábla soha", "class C: data tables never")}[pol["data_class"]]
        b.exclude("03_adatok/**/*.csv", *why)
    return b, ts, runs, head, records


def _kb_ids(files):
    ids = set()
    for rel, (data, _role) in files.items():
        if rel.lower().endswith((".json", ".md", ".jsonl", ".txt")):
            try:
                ids.update(snapshot._KB_ID_RE.findall(data.decode("utf-8")))
            except UnicodeDecodeError:
                pass
    return sorted(ids)


def _selftest():
    try:
        from metaelemzes import api as engine_api
        st = engine_api.selftest_status()
    except Exception:                                  # noqa: BLE001
        st = None
    if not isinstance(st, dict):
        return None
    return collections.OrderedDict((("checks", st.get("checks")), ("fail", st.get("failed")), ("state", st.get("state"))))


def zip_bytes(entries):
    """[(név, bájtok)] → determinisztikus ZIP (rendezett nevek, 1980-01-01, ZIP_DEFLATED, rögzített attribútumok)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for name, data in sorted(entries, key=lambda e: e[0]):
            zi = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3                     # Unix (minden platformon azonos fejléc)
            zi.external_attr = (0o100000 | FILE_MODE) << 16
            zi.flag_bits |= 0x800                    # UTF-8 fájlnevek
            zf.writestr(zi, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    return buf.getvalue()


def build(project_dir=None, app=None, include=None, redact=None, environ=None):
    """Az audit-ZIP bájtjai és leírása: (zip_bytes, info). Vagy ``app`` (futó szerver) vagy ``project_dir``.
    info: {sha256, bytes, name, created, data_class, manifest, redactions, excluded, deterministic: true}."""
    own = app is None
    if own:
        if project_dir is None:
            raise AuditExportError("Hiányzó projektmappa.")
        app = snapshot._open_app(project_dir, "hu")
    try:
        app.require_open()
        root = app.project_root
        pol = snapshot.resolve_policy(app.data_class(), include, redact)
        bundle, ts, runs, head, records = collect(root, pol, app=app, environ=environ)
        redactions, _ = snapshot.policy_report(pol)
        meta, _ = app.project_meta_safe()
        title = meta.get("title") if isinstance(meta, dict) and isinstance(meta.get("title"), str) else root.name
        db = _kb_db_path(app)
        db_sha = None
        if db and os.path.isfile(db):
            try:
                db_sha = store.sha256_file(db)
            except OSError:
                db_sha = None
        files = []
        for rel in sorted(bundle.files):
            data, role = bundle.files[rel]
            files.append(collections.OrderedDict((("path", rel), ("sha256", _sha(data)), ("bytes", len(data)),
                                                  ("role", role))))
        engine_version = None
        try:
            from metaelemzes import api as engine_api
            engine_version = getattr(engine_api, "__version__", None)
        except Exception:                              # noqa: BLE001
            pass
        manifest = collections.OrderedDict((
            ("schema", SCHEMA),
            ("project", snapshot.slug(title)),
            ("title", title),
            ("created", ts),
            ("data_class", pol["data_class"]),
            ("policy", collections.OrderedDict((("include", pol["include"]), ("redact", pol["redact"])))),
            ("redactions", redactions),
            ("notes", bundle.notes),
            ("versions", collections.OrderedDict((("engine", engine_version), ("gui", __version__),
                                                  ("selftest", _selftest()), ("plugins", _plugins(app))))),
            ("files", files),
            ("runs", [collections.OrderedDict((("run_id", r["run_id"]), ("outcome_id", r["outcome_id"]),
                                               ("data_sha256", r["data_sha256"]), ("spec_sha256", r["spec_sha256"])))
                      for r in runs]),
            ("activity_head", head.get("hash") if isinstance(head, dict) else None),
            ("activity_seq", head.get("seq") if isinstance(head, dict) else None),
            ("activity_records", len(records)),
            ("rerun_scripts", [n for n in RERUN_SCRIPTS if n in bundle.files]),
            ("kb_snapshot", collections.OrderedDict((("ids", _kb_ids(bundle.files)), ("db_sha256", db_sha)))),
            ("excluded", [collections.OrderedDict((("pattern", p), ("reason", r))) for p, r in bundle.excluded.items()]),
        ))
        mbytes = (json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        entries = [("manifest.json", mbytes)] + [(rel, bundle.files[rel][0]) for rel in bundle.files]
        data = zip_bytes(entries)
        digest = _sha(data)
        name = "%s-audit-%s.zip" % (snapshot.slug(title), digest[:8])
        info = collections.OrderedDict((
            ("sha256", digest), ("bytes", len(data)), ("name", name), ("created", ts),
            ("data_class", pol["data_class"]), ("deterministic", True), ("manifest", manifest),
            ("redactions", redactions), ("excluded", manifest["excluded"])))
        return data, info
    finally:
        if own:
            app.close()


def default_rel(info):
    """07_ellenorzes/audit/<projektállapot napja>/<név>.zip"""
    return "%s/%s/%s" % (OUT_DIR_REL, info["created"][:10], info["name"])


# -------------------------------------------------------------------------------------- parancssor
def build_parser(prog="python ma.py gui audit-export"):
    p = argparse.ArgumentParser(
        prog=prog,
        description="Determinisztikus audit-csomag (ZIP) a projektről: döntések, specek, commit-futások, eredet, "
                    "PRISMA, tevékenységnapló és újrafuttató szkriptek, manifest.json-nal. Ugyanaz a projektállapot "
                    "ugyanazt a bájtsort adja.",
        epilog="Kapcsolók (--redact / --keep): tables, provenance, quotes, assessors, paths, log, runs, figures, "
               "prisma, activity, appraisals, rerun. C osztálynál adattábla soha; _privat/, PDF és a KB teljes "
               "szövege soha.")
    p.add_argument("--project", default=".", help="a projektmappa (alap: az aktuális mappa)")
    p.add_argument("--out", help="a kimeneti ZIP (alap: <projekt>/%s/<dátum>/<név>.zip; ha az adatosztály miatt ott "
                                 "nem írható, a _privat/export/ alá — mint a munkapadon)" % OUT_DIR_REL)
    p.add_argument("--redact", action="append", metavar="MIT", help="kitakarás / kihagyás (vesszővel több is)")
    p.add_argument("--keep", action="append", metavar="MIT", help="megtartás / belefoglalás (vesszővel több is)")
    p.add_argument("--json", action="store_true", help="a leírás JSON-ként a stdout-ra")
    return p


def main(argv=None, out=None):
    """``ma.py gui audit-export`` belépési pontja → kilépési kód (0 kész, 2 hibás kérés, 1 egyéb hiba)."""
    out = out or sys.stdout
    a = build_parser().parse_args(argv)
    project = Path(os.path.expanduser(a.project))
    if not project.is_dir():
        print("HIBA: a projektmappa nem létezik vagy nem mappa: %s" % project, file=out)
        return 2
    app = None
    try:
        include, redact_ = snapshot.toggles_from_cli(a.redact, a.keep)
        app = snapshot._open_app(str(project), "hu")
        data, info = build(app=app, include=include, redact=redact_)
        # ugyanaz a hely és ugyanazok az írási szabályok, mint a munkapadon (C osztály → _privat/export/, WS-8)
        target, warnings = snapshot.cli_save(app, default_rel(info), data, a.out)
    except snapshot.SnapshotError as exc:
        print("HIBA: %s" % exc.message, file=out)
        return 2
    except Exception as exc:                          # noqa: BLE001 — érthető üzenet, nem traceback
        msg = getattr(exc, "message", None) or str(exc)
        print("HIBA: az audit-csomag nem készült el (%s: %s)" % (type(exc).__name__, msg), file=out)
        return 1
    finally:
        if app is not None:
            app.close()
    if a.json:
        print(snapshot.canonical_json(dict(info, path=str(target), warnings=warnings)), file=out)
    else:
        for w in warnings:
            print("Figyelem: %s" % w, file=out)
        m = info["manifest"]
        print("Audit-csomag kész: %s" % target, file=out)
        print("  %d bájt · sha256 %s · %d fájl · projektállapot: %s · adatosztály: %s" % (
            info["bytes"], info["sha256"], len(m["files"]), info["created"], info["data_class"]), file=out)
        if info["redactions"]:
            print("  Kitakarva: %s" % ", ".join(r["hu"] for r in info["redactions"]), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
