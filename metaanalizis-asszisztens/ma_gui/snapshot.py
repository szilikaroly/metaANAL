# -*- coding: utf-8 -*-
"""Kitakaró, csak olvasható HTML-pillanatkép (terv 2.5, 3.5.17, 4.17, 7.4, 7.6, 7.7).

Egyetlen önálló HTML-fájl a társszerzőknek: a termék-felület (``web/dist/snapshot.html``, a
``build_gui.py`` pillanatkép-változata) + a projekt állapota beágyazott JSON-ként. Python, szerver és
hálózat nélkül nyitható (``file://``); olvasni, szűrni, ábrát nézni és nyomtatni lehet, az író gombok
a pontos parancsot mutatják/másolják.

Hogyan készül:

1. **Rögzítés.** A szerver saját GET-végpontjait hívjuk folyamaton belül (``App.router.dispatch``), így a
   beágyazott válaszok alakja bájtra az élő felületé (``{ok, schema, data, warnings, meta}``). A
   pillanatkép-kliens (``web/src/snapshot/provider.js``) ugyanezekkel a borítékokkal válaszol a
   képernyőknek. Ha a ``/api/runs`` végpont még nincs a szerverben, a futásokat a ``run.json`` /
   ``plot_data.json`` fájlokból olvassuk (ugyanabban az alakban).
2. **Kitakarás** (7.4, 7.7) — a kapcsolók alapértéke az adatosztályból jön (``policy_defaults``):

   - **C osztály:** adattábla (cellaérték) SOHA — a kérés is elutasítva; a ``_privat/`` soha.
   - **B osztály:** adattábla alapból ki; az értékelők monogrammá.
   - **minden osztály:** a KB teljes szövege (``chunk``) soha; PDF soha, a dokumentumból csak az
     azonosító + sha256 (+ oldal az eredet-adatban); az eredet-idézetek és az abszolút utak alapból ki.
   - adattábla nélkül az ábrák nyers cellaoszlopai (pl. „Esemény/N”), az eredet-adat bevitt értékei és
     a validálás soronkénti üzenetei is kimaradnak (csak a darabszámok maradnak).

   A pillanatképben lévő ``manifest`` rögzíti a kitakarásokat és a kizárt mintákat.
3. **Determinizmus.** Ugyanaz a projektállapot ugyanazt a bájtsort adja: a kérésenként változó mezők
   (``elapsed_ms``, ``request_id``, ``project_rev``, ``generated`` …) rögzített értéket kapnak; a
   ``state_time`` a projekt utolsó rögzített eseményének ideje (nem a falióra; ``SOURCE_DATE_EPOCH``
   felülírja); a JSON rendezett kulcsú.
4. **CSP** (7.6): ``<meta http-equiv="Content-Security-Policy">`` sha256-hash-sel minden inline
   ``<script>``-re és ``<style>``-ra, ``connect-src 'none'`` — a fájl internetes gépen sem szól hálózatra.

Parancssor: ``python ma.py gui snapshot --project <mappa> --out x.html [--redact …] [--keep …]``
(belépési pont: ``main(argv)``; ``python -m ma_gui.snapshot`` is). Csak stdlib; statisztika és
számformázás nincs — minden számszöveg a motoré.
"""
import argparse
import base64
import collections
import datetime
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

if __package__ in (None, ""):                      # python ma_gui/snapshot.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "ma_gui"                          # noqa: A001

from . import __version__, privacy, security, store  # noqa: E402

SCHEMA = "szk.ma.snapshot/v1"
MANIFEST_SCHEMA = "szk.ma.snapshot-manifest/v1"
EXPORT_SCHEMA = "szk.ma.export-result/v1"

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"
TEMPLATE_CANDIDATES = (WEB_DIR / "dist" / "snapshot.html",)
CSP_PLACEHOLDER = "{{SNAPSHOT_CSP}}"
DATA_PLACEHOLDER = "{{SNAPSHOT_DATA}}"
OUT_DIR_REL = "07_ellenorzes/pillanatkep"
SNAPSHOT_SUFFIX = ".snapshot.html"
EPOCH_TS = "1980-01-01T00:00:00Z"
PROJECT_TOKEN = "<projekt>"
PROJECT_DIR_HINT = "<projektmappa>"

# A pillanatkép CSP-je (7.6). A hash-ek helye: {scripts} / {styles}.
CSP_TEMPLATE = ("default-src 'none'; script-src {scripts}; style-src {styles}; img-src data: blob:; "
                "connect-src 'none'; font-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; "
                "worker-src 'none'; manifest-src 'none'; base-uri 'none'; form-action 'none'")

# -------------------------------------------------------------------------------------- kapcsolók
# „Belefoglalás” (include) és „kitakarás” (redact) — a 3.5.17-es export-képernyő kapcsolói; ugyanezeket
# használja az audit-csomag is (audit_export.py).
INCLUDE_KEYS = ("decisions", "specs_runs", "provenance", "appraisals", "prisma", "figures", "activity", "rerun",
                "data_tables")
REDACT_KEYS = ("assessors", "quotes", "abs_paths")
LABELS = {
    "decisions": ("döntési napló és kapuk", "decision log and gates"),
    "specs_runs": ("specek és commit-futások", "specs and commit runs"),
    "provenance": ("eredet-adatok (dokumentum + oldal)", "provenance (document + page)"),
    "appraisals": ("értékelések", "appraisals"),
    "prisma": ("PRISMA + studies.json", "PRISMA + studies.json"),
    "figures": ("ábrák", "figures"),
    "activity": ("tevékenységnapló (hash-lánc)", "activity log (hash chain)"),
    "rerun": ("újrafuttató szkriptek", "rerun scripts"),
    "data_tables": ("adattáblák (cellaértékek)", "data tables (cell values)"),
    "assessors": ("értékelők → monogram", "assessors → initials"),
    "quotes": ("eredet-idézetek ki", "provenance quotes removed"),
    "abs_paths": ("abszolút utak ki", "absolute paths removed"),
}
# CLI-nevek (--redact / --keep): rövid, kezdőknek is érthető alakok is
CLI_ALIASES = {
    "tables": "data_tables", "tablak": "data_tables", "adattablak": "data_tables", "data_tables": "data_tables",
    "provenance": "provenance", "eredet": "provenance",
    "quotes": "quotes", "idezetek": "quotes",
    "assessors": "assessors", "ertekelok": "assessors",
    "paths": "abs_paths", "abs_paths": "abs_paths", "utak": "abs_paths",
    "log": "decisions", "decisions": "decisions", "naplo": "decisions",
    "runs": "specs_runs", "specs_runs": "specs_runs", "futasok": "specs_runs",
    "figures": "figures", "abrak": "figures",
    "prisma": "prisma", "activity": "activity", "appraisals": "appraisals", "rerun": "rerun",
}

# Kemény szabályok (nem kapcsolhatók): a manifeszt 'excluded' listája.
HARD_EXCLUDED = (
    ("_privat/**", ("C osztály / bizalmas adat — soha", "class C / confidential data — never")),
    ("**/*.pdf", ("D osztály (jogvédett teljes szöveg) — csak doc-id + oldal + sha256",
                  "class D (copyrighted full text) — doc id + page + sha256 only")),
    ("tudasbazis: chunk", ("a KB teljes szövege (helyi forrás) — soha, csak azonosító + az adatbázis sha256-ja",
                           "KB full text (local source) — never, ids + database sha256 only")),
)

KB_FIELDS = ("spec", "model", "pi", "heterogeneity", "bias")
KB_RUN_FIELDS = ("model", "pi", "heterogeneity", "bias")
LOG_KINDS = ("finding", "decision", "checkpoint", "grade", "run", "status")
MAX_KB_ITEMS = 400
MAX_ACTIVITY = 1000

_VOLATILE_ZERO = frozenset(["elapsed_ms", "project_rev"])
_VOLATILE_TS = frozenset(["generated", "generated_at", "checked_at", "probed_at", "now"])
_KB_ID_RE = re.compile(r"(?<![A-Za-z0-9_-])(D-S\d{2}-\d{3}|[VPX]\d{3}|K-[A-Za-z0-9_.-]{1,60})(?![A-Za-z0-9_-])")
_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_ABS_PATH_RE = re.compile(r"(?:(?<![\w/\\.])[A-Za-z]:[\\/]|(?<![\w/\\.:])/(?:home|Users|root|tmp|var|private|mnt|"
                          r"media|opt|srv|Volumes|run|data|workspace|usr|etc)[\\/])[^\s\"'<>|;,()\[\]{}]*")
_QUOTE_KEYS = frozenset(["quote", "quotes", "excerpt", "evidence_quote", "quote_text"])
_ASSESSOR_KEYS = frozenset(["assessor", "assessors", "rater", "raters", "extractor", "reviewer_name",
                            "assessor_name", "appraiser"])
# a v1 értékelés- és kettős-kinyerés-nézetek további értékelő-mezői (a fenntartott 'consensus' / 'ai' nem név)
_RATER_EXTRA_KEYS = frozenset(["second_assessor", "approved_by", "approver", "human_raters", "a_rater", "b_rater"])
_CELL_VALUE_KEYS = frozenset(["value", "values", "value_as_entered", "entered", "cell", "cells", "raw", "rows"])
# az eredet-adat cellája alatt (bármilyen mélységben) bevitt vagy származtatott értéket hordozó kulcsok: az átváltó
# bemenetei és kimenetei (conversion.request.inputs, outputs, outputs_text, cell_text), az egyeztetés A/B értéke
# és a korábbi bejegyzések (history[].value_as_entered) — adattábla nélkül ezek is kimaradnak (PRIV-2)
_PROV_VALUE_KEYS = _CELL_VALUE_KEYS | frozenset(["inputs", "outputs", "outputs_text", "cell_text", "a", "b",
                                                 "value_a", "value_b", "chosen_value", "consensus_value",
                                                 "final_value", "before", "after", "text"])
_HU_DIGRAPHS = ("Dzs", "Cs", "Dz", "Gy", "Ly", "Ny", "Sz", "Ty", "Zs")


class SnapshotError(Exception):
    """Felhasználói hiba (pl. tiltott kapcsoló); ``code`` a 3.4-es hibakód."""

    def __init__(self, message, code="BAD_REQUEST"):
        super().__init__(message)
        self.message = message
        self.code = code


# -------------------------------------------------------------------------------------- szabályzat
def policy_defaults(data_class):
    """Az adatosztályból jövő alapértékek (7.4): {'include': {…}, 'redact': {…}}."""
    dc = privacy.normalize_data_class(data_class)
    include = {k: True for k in INCLUDE_KEYS}
    include["data_tables"] = dc == "A"
    redact = {"assessors": dc != "A", "quotes": True, "abs_paths": True}
    return {"include": include, "redact": redact}


def resolve_policy(data_class, include=None, redact=None):
    """A kért kapcsolók + az alapértékek → végleges szabályzat. Ismeretlen kulcs vagy C osztályban
    kért adattábla → SnapshotError (a hívó 400/403-at ad). Visszaad: {data_class, include, redact}."""
    dc = privacy.normalize_data_class(data_class)
    pol = policy_defaults(dc)
    for group, given, keys in (("include", include, INCLUDE_KEYS), ("redact", redact, REDACT_KEYS)):
        if given is None:
            continue
        if not isinstance(given, dict):
            raise SnapshotError("A(z) %s mező objektum legyen ({kulcs: true/false})." % group)
        for k, v in given.items():
            if k not in keys:
                raise SnapshotError("Ismeretlen %s-kapcsoló: %s (lehetséges: %s)." % (group, k, ", ".join(keys)))
            if not isinstance(v, bool):
                raise SnapshotError("A(z) %s.%s értéke true vagy false legyen." % (group, k))
            pol[group][k] = v
    if dc == "C" and pol["include"]["data_tables"]:
        raise SnapshotError("C osztályú (betegszintű) projektből adattábla soha nem kerülhet pillanatképbe vagy "
                            "audit-csomagba (terv 7.4).", code="FORBIDDEN")
    pol["data_class"] = dc
    return pol


def _label(key):
    hu, en = LABELS[key]
    return collections.OrderedDict((("key", key), ("hu", hu), ("en", en)))


def _reason(hu, en):
    return collections.OrderedDict((("hu", hu), ("en", en)))


def policy_report(pol):
    """(redactions, excluded) a manifeszthez: a bekapcsolt kitakarások és a kizárt minták indoklással."""
    redactions = [_label(k) for k in REDACT_KEYS if pol["redact"].get(k)]
    excluded = [collections.OrderedDict((("pattern", p), ("reason", _reason(*r)))) for p, r in HARD_EXCLUDED]
    dc = pol["data_class"]
    if not pol["include"]["data_tables"]:
        why = {"A": ("adattábla kikapcsolva", "data tables switched off"),
               "B": ("B osztály: adattábla alapból ki", "class B: data tables off by default"),
               "C": ("C osztály: adattábla soha", "class C: data tables never")}[dc]
        if dc == "B" and policy_defaults("B")["include"]["data_tables"] != pol["include"]["data_tables"]:
            why = ("adattábla kikapcsolva", "data tables switched off")
        excluded.append(collections.OrderedDict((("pattern", "03_adatok/**/*.csv"), ("reason", _reason(*why)))))
        redactions.append(_label("data_tables"))
    for k in INCLUDE_KEYS:
        if k != "data_tables" and not pol["include"].get(k):
            redactions.append(_label(k))
    return redactions, excluded


# -------------------------------------------------------------------------------------- segédek
def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def json_for_script(obj):
    """JSON <script type="application/json">-be: a '<' \\u003c-ként (nem zárhatja le a taget)."""
    text = canonical_json(obj)
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def query_string(query):
    """Kanonikus lekérdezés (rendezett kulcsok, RFC 3986 szerinti kódolás) — a JS-kliens ugyanígy képzi."""
    if not query:
        return ""
    parts = []
    for k in sorted(query):
        v = query[k]
        if v is None:
            continue
        parts.append("%s=%s" % (quote(str(k), safe="-_.~"), quote(str(v), safe="-_.~")))
    return "&".join(parts)


def route_key(method, path, query=None):
    qs = query_string(query)
    return "%s %s%s" % (method.upper(), path, ("?" + qs) if qs else "")


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    m = _TS_RE.match(value.strip())
    if not m:
        return None
    y, mo, d, hh, mm, ss, tz = m.groups()
    try:
        dt = datetime.datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss))
    except ValueError:
        return None
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        dt -= sign * datetime.timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return dt


def _fmt_ts(dt):
    return dt.replace(microsecond=0).isoformat() + "Z"


def source_date_epoch(environ=None):
    """A SOURCE_DATE_EPOCH (reprodukálható build) mint UTC-idő szöveg, vagy None."""
    env = os.environ if environ is None else environ
    raw = env.get("SOURCE_DATE_EPOCH")
    if not raw or not raw.strip().isdigit():
        return None
    return _fmt_ts(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(raw.strip())))


def state_time(objs, environ=None):
    """A projektállapot ideje: a rögzített adatok legkésőbbi időbélyege (ts, started, finished …)."""
    forced = source_date_epoch(environ)
    if forced:
        return forced
    best = None
    stack = list(objs)
    keys = ("ts", "started", "finished", "resolved_ts", "created", "updated")
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys:
                    dt = _parse_ts(v)
                    if dt is not None and (best is None or dt > best):
                        best = dt
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(o, list):
            stack.extend(x for x in o if isinstance(x, (dict, list)))
    return _fmt_ts(best) if best is not None else EPOCH_TS


def initials(name):
    """'Szili Károly' → 'SzK' (magyar kettős betűkkel); rövid azonosító (≤ 3 karakter) változatlan."""
    if not isinstance(name, str):
        return name
    s = name.strip()
    if len(s) <= 3:
        return s
    out = []
    for part in re.split(r"[\s._,;-]+", s):
        if not part:
            continue
        tri = next((d for d in _HU_DIGRAPHS if part[:len(d)].lower() == d.lower() and len(part) > len(d)), None)
        out.append(part[0].upper() + (tri[1:].lower() if tri else ""))
    return "".join(out)[:8] or "?"


def _actor_initials(value):
    if not isinstance(value, str):
        return value
    if ":" in value:
        kind, _, who = value.partition(":")
        return "%s:%s" % (kind, initials(who)) if who else value
    return value


def slug(text, fallback="projekt"):
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:48] or fallback


def _walk(obj, fn, key=None):
    """Mélységi bejárás; fn(kulcs, érték) → új érték (vagy _KEEP)."""
    if isinstance(obj, dict):
        out = collections.OrderedDict()
        for k, v in obj.items():
            nv = fn(k, v)
            if nv is _DROP:
                continue
            out[k] = _walk(nv, fn, k)
        return out
    if isinstance(obj, list):
        return [_walk(v, fn, key) for v in obj]
    return obj


_DROP = object()


# -------------------------------------------------------------------------------------- rögzítés
class _Recorder(object):
    """GET (és egy-egy olvasó POST) kérések a szerver saját útválasztóján át, folyamaton belül."""

    def __init__(self, app):
        import copy
        from . import router as _router
        self.app = app
        self.router = _router
        # ugyanazok az útvonalak, de szervernapló nélkül (egy pillanatkép ~150 belső kérés; a valódi kérések
        # naplózása közben változatlan marad)
        self.dispatcher = copy.copy(app.router)
        self.dispatcher.log_fn = None
        self.routes = collections.OrderedDict()

    def has_route(self, method, path):
        return self.app.router.find(method, path) is not None

    def call(self, method, path, query=None, body=None):
        qs = query_string(query)
        target = path + ("?" + qs if qs else "")
        raw = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = self.router.Request(self.app, method, target, {"Content-Type": "application/json"}, raw,
                                  request_id="snapshot")
        status, _headers, out = self.dispatcher.dispatch(req)
        if isinstance(out, self.router.Response):
            return status, None
        try:
            env = json.loads(out.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, AttributeError):
            return status, None
        return status, env if isinstance(env, dict) else None

    def get(self, path, query=None, key=None):
        """Sikeres GET → a boríték rögzítve (és visszaadva); hiba → None (a kliens „nincs a pillanatképben”)."""
        status, env = self.call("GET", path, query)
        if env is None or env.get("ok") is not True:
            return None
        self.routes[key or route_key("GET", path, query)] = env
        return env

    def put(self, key, data, schema, warnings=None):
        env = collections.OrderedDict((("ok", True), ("schema", schema), ("data", data),
                                       ("warnings", list(warnings or [])), ("meta", {})))
        self.routes[key] = env
        return env

    def refuse(self, key, message, code="NOT_FOUND"):
        self.routes[key] = collections.OrderedDict((
            ("ok", False), ("error", collections.OrderedDict((
                ("code", code), ("http", security.ERROR_CODES.get(code, 404)), ("message", message))))))


def _data(env):
    return env.get("data") if isinstance(env, dict) else None


def _norm_rel(p):
    return p.replace("\\", "/").lstrip("./") if isinstance(p, str) else p


def _is_private(rel):
    return isinstance(rel, str) and (rel.replace("\\", "/").split("/")[0].casefold()
                                     == privacy.PRIVATE_DIR.casefold())


def _is_private_real(root, path):
    """Az út a _privat/ alatt van-e — írt alakja szerint VAGY a szimbolikus linkek feloldása után (WS-6)."""
    return _is_private(path) or privacy.is_private_location(root, path)


def _private_run_ids(app):
    """A _privat/ alól jövő commit-futások azonosítói (mappa, adatfájl vagy spec a _privat/ alatt)."""
    return set(_private_run_marks(app))


def _private_run_marks(app):
    """{run_id: [ujjlenyomatok]} a _privat/ alól jövő futásokra: a futás-azonosító és az adatfájl / spec sha256-ja —
    egy v1-válasz, amely ezek bármelyikét tartalmazza, a privát futásra hivatkozik (a pillanatképbe nem kerülhet)."""
    try:
        from .routes import runs as runs_mod
        out = {}
        for rid, (d, r) in runs_mod.run_index(app).items():
            if runs_mod.run_is_private(app, d, r):
                marks = [rid]
                for part in ("data", "spec"):
                    sha = (r.get(part) or {}).get("sha256") if isinstance(r.get(part), dict) else None
                    if isinstance(sha, str) and len(sha) >= 32:
                        marks.append(sha)
                out[rid] = marks
        return out
    except Exception:                                  # noqa: BLE001 — a futás-index hiánya nem állítja meg
        return {}


def _journal_run_private(root, row):
    """A projektnapló futás-sora (data_path / outdir) a _privat/ alól jön-e."""
    return isinstance(row, dict) and any(_is_private_real(root, row.get(k)) for k in ("data_path", "outdir"))


def _fallback_run_private(root, run, run_json_path):
    data = run.get("data") if isinstance(run.get("data"), dict) else {}
    sp = run.get("spec") if isinstance(run.get("spec"), dict) else {}
    return (privacy.is_private_location(root, os.path.dirname(str(run_json_path)))
            or any(_is_private_real(root, p) for p in (data.get("path"), sp.get("path"))))


def _read_json(path):
    try:
        with open(str(path), "rb") as fh:
            obj = json.loads(fh.read().decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return obj


def _fallback_runs(root):
    """{kimenet: [run-leíró + outcome_id, stale]} és {run_id: plot_data} a fájlokból — ha a szerverben
    még nincs /api/runs (a futások szerver-oldali nézetének alakjában: szk.ma.run/v1 + outcome_id, stale)."""
    from metaelemzes import api as engine_api
    by_outcome = collections.OrderedDict()
    plots = {}
    for path, layout, folder, _logged in engine_api.project_run_files(str(root)):
        doc = _read_json(path)
        if not isinstance(doc, dict) or doc.get("mode", "commit") != "commit" or not doc.get("run_id"):
            continue
        if _fallback_run_private(root, doc, path):
            continue                                   # _privat/ alól jövő futás soha (7.4)
        run = collections.OrderedDict(doc)
        spec = run.get("spec") if isinstance(run.get("spec"), dict) else {}
        outcome = None
        if layout == "nested" and folder:
            outcome = folder
        if not outcome:
            sp = _read_json(os.path.join(str(root), *str(spec.get("path") or "").split("/"))) \
                if spec.get("path") else None
            outcome = (sp or {}).get("outcome") if isinstance(sp, dict) else None
        outcome = outcome or folder or "?"
        run["outcome_id"] = outcome
        data = run.get("data") if isinstance(run.get("data"), dict) else {}
        cur = None
        dpath = data.get("path")
        if isinstance(dpath, str) and dpath:
            full = dpath if os.path.isabs(dpath) else os.path.join(str(root), *dpath.split("/"))
            try:
                cur = store.sha256_file(full)
            except OSError:
                cur = None
        run["stale"] = bool(data.get("sha256")) and cur != data.get("sha256")
        by_outcome.setdefault(outcome, []).append(run)
        files = run.get("files") if isinstance(run.get("files"), dict) else {}
        pf = (files.get("plot") or {}).get("path") if isinstance(files.get("plot"), dict) else None
        cand = [os.path.join(os.path.dirname(path), "plot_data.json")]
        if isinstance(pf, str):
            cand.insert(0, os.path.join(str(root), *pf.split("/")))
        for c in cand:
            p = _read_json(c)
            if isinstance(p, dict) and p.get("axis") is not None:
                plots[run["run_id"]] = p
                break
    for runs in by_outcome.values():
        runs.sort(key=lambda r: (str(r.get("started") or ""), str(r.get("run_id"))))
    return by_outcome, plots


def _latest_per_spec(runs):
    """Spec-csoportonként (név, ennek hiányában út) a legutolsó commit-futás, időrendben."""
    last = collections.OrderedDict()
    ordered = sorted(runs, key=lambda r: (str(r.get("started") or r.get("finished") or ""), str(r.get("run_id"))))
    for r in ordered:
        sp = r.get("spec") if isinstance(r.get("spec"), dict) else {}
        key = sp.get("name") or sp.get("path") or r.get("run_id")
        last.pop(key, None)
        last[key] = r
    return list(last.values())


def _synthetic_caps(engine_version, ts):
    comp = collections.OrderedDict((
        ("id", "engine"), ("label", _reason("motor", "engine")), ("version", engine_version), ("state", "ok"),
        ("mode", "snapshot"), ("problems", []), ("features", [])))
    return collections.OrderedDict((("schema", "szk.ma.capabilities-report/v1"), ("generated", ts),
                                    ("probing", False), ("snapshot", True), ("components", [comp]),
                                    ("matrix", {}), ("problems", [])))


def _kb_context(run, plot):
    """A futás kontextusa a KB-szabályok szűréséhez — PONTOSAN a kliens analysis_shared.kbContext()-je szerint
    (modell, k, hatásméret szövegként), hogy a rögzített /api/kb/rules?…-kulcs a kliens kérésével egyezzen."""
    ctx = collections.OrderedDict()
    prim = next((x for x in (plot or {}).get("summaries") or [] if isinstance(x, dict) and x.get("primary") is True), None)
    model = (prim or {}).get("model") or ((run.get("primary") or {}).get("model") if isinstance(run.get("primary"), dict)
                                          else None)
    if model:
        ctx["model"] = str(model)
    k = run.get("k") if isinstance(run.get("k"), int) and not isinstance(run.get("k"), bool) else (plot or {}).get("k")
    if isinstance(k, int) and not isinstance(k, bool):
        ctx["k"] = str(k)
    measure = (plot or {}).get("measure") or run.get("measure")
    if measure:
        ctx["measure"] = str(measure)
    return ctx


def _privacy_view(d, dc):
    """A gépfüggő adatvédelmi állapotból csak a nem azonosító összegzés marad (utak, okok nélkül)."""
    d = d if isinstance(d, dict) else {}
    vault = d.get("vault") if isinstance(d.get("vault"), dict) else {}
    out = collections.OrderedDict()
    out["data_class"] = dc
    out["data_class_label"] = privacy.DATA_CLASS_LABELS.get(dc)
    out["gitignore_block_present"] = bool(d.get("gitignore_block_present"))
    out["vault"] = collections.OrderedDict((("under_root", bool(vault.get("under_root"))),
                                            ("tracked", bool(vault.get("tracked")))))
    out["cloud_sync"] = None
    out["tracked_sensitive_files"] = []
    out["history"] = {"in_history": bool((d.get("history") or {}).get("in_history"))} \
        if isinstance(d.get("history"), dict) else {"in_history": False}
    out["write_hold"] = {"active": False, "reason": None}
    out["open_blocked"] = {"blocked": False, "reasons": []}
    out["recommendations"] = []
    out["actions"] = []
    out["snapshot"] = True
    return out


def _validation_summary(doc):
    """Validálási összegzés cellaérték és üzenet nélkül: darabszámok és szabálykódok."""
    if not isinstance(doc, dict):
        return None
    codes = collections.Counter()
    for f in doc.get("findings") or []:
        if isinstance(f, dict) and isinstance(f.get("code"), str):
            codes[(f.get("code"), f.get("severity"))] += 1
    out = collections.OrderedDict()
    for k in ("schema", "measure", "n_rows", "k_analysable", "summary", "input_sha256"):
        if k in doc:
            out[k] = doc[k]
    out["codes"] = [collections.OrderedDict((("code", c), ("severity", s), ("count", n)))
                    for (c, s), n in sorted(codes.items(), key=lambda x: (str(x[0][0]), str(x[0][1])))]
    return out


def _validate_body(table, measure):
    header = list(table.get("header") or [])
    rows = []
    for r in table.get("rows") or []:
        cells = r.get("cells") if isinstance(r, dict) else r
        uid = r.get("row_uid") if isinstance(r, dict) else None
        rows.append([uid or ""] + [("" if c is None else str(c)) for c in (cells or [])])
    fmt = table.get("format") if isinstance(table.get("format"), dict) else {}
    return {"schema": "szk.ma.validate-request/v1", "dataset": table.get("dataset"), "measure": measure,
            "options": {}, "table": {"header": ["row_uid"] + header, "rows": rows,
                                     "decimal_mark": fmt.get("decimal_mark")},
            "base_sha256": table.get("etag")}


def record(app, pol):
    """A projekt állapota borítékokként: (routes, extra) — extra: {validation, outcomes, runs, kb_ids}."""
    rec = _Recorder(app)
    inc = pol["include"]
    extra = {"validation": collections.OrderedDict(), "runs": [], "outcomes": []}
    root = app.project_root
    kb_contexts = []

    # -- keret (a motor-önteszt jelvénye mindig a folyamaton belüli, determinisztikus összegzés: a szerver
    # háttérben futó öntesztjének pillanatnyi állapota — pending/running — nem kerülhet a pillanatképbe)
    eng = rec.get("/api/engine")
    engine_version = (_data(eng) or {}).get("engine_version") if eng else None
    if isinstance(_data(eng), dict):
        eng["data"]["selftest"] = _engine_selftest()
        # a szerver tudásbázis-építésének pillanatnyi állapota (lazy/building/ok) gépállapot, nem projektállapot
        eng["data"]["kb"] = {"state": "snapshot", "ok": None}
    prv = rec.get("/api/privacy")
    if prv is not None:
        prv["data"] = _privacy_view(_data(prv), pol["data_class"])
    proj = rec.get("/api/project")
    pdata = _data(proj) or {}
    if isinstance(pdata.get("tables"), list):
        pdata["tables"] = [t for t in pdata["tables"]
                           if isinstance(t, dict) and not _is_private_real(root, t.get("dataset"))]
    for k in ("recent",):
        pdata.pop(k, None)
    # a _privat/ alól jövő futások (CLI-commit a _privat/05_elemzes alá, vagy _privat/ adatfájlú futás) soha
    private_runs = _private_run_ids(app)

    # -- napló, kapuk, X-szabályok
    if inc["decisions"]:
        for kind in LOG_KINDS:
            env = rec.get("/api/log/" + kind)
            d = _data(env)
            if kind == "run" and isinstance(d, dict) and isinstance(d.get("items"), list):
                d["items"] = [r for r in d["items"] if not _journal_run_private(root, r)]
            if kind == "status" and isinstance(d, dict) and isinstance(d.get("runs"), list):
                d["runs"] = [r for r in d["runs"] if not _journal_run_private(root, r)]
        env = rec.get("/api/audit/project")
        d = _data(env)
        if isinstance(d, dict) and isinstance(d.get("inputs"), dict):
            # a _privat/ alatti fájlok ujjlenyomata (sha256) sem kerül a pillanatképbe
            d["inputs"] = collections.OrderedDict((k, v) for k, v in d["inputs"].items()
                                                  if not _is_private_real(root, k))
    if inc["activity"]:
        rec.get("/api/log/activity", {"limit": MAX_ACTIVITY}, key=route_key("GET", "/api/log/activity"))
    if inc["prisma"]:
        env = rec.get("/api/prisma")
        d = _data(env)
        if isinstance(d, dict) and isinstance(d.get("meta"), list):
            d["meta"] = [m for m in d["meta"] if not (isinstance(m, dict) and m.get("run_id") in private_runs)]
        rec.get("/api/studies")

    # -- kimenetek, futások, ábrák
    outcomes = []
    for o in pdata.get("outcomes") or []:
        if isinstance(o, dict) and isinstance(o.get("id"), str) and o["id"] not in outcomes:
            outcomes.append(o["id"])
    if inc["specs_runs"]:
        live_runs = rec.has_route("GET", "/api/runs")
        fb_runs, fb_plots = ({}, {}) if live_runs else _fallback_runs(root)
        for oid in fb_runs:
            if oid not in outcomes:
                outcomes.append(oid)
        all_kept = []
        for oid in outcomes:
            key = route_key("GET", "/api/runs", {"outcome": oid})
            if live_runs:
                # private=0: a szerver már a szűrt listából választ (a _privat/ futás nem takarhat el régebbit)
                env = rec.get("/api/runs", {"outcome": oid, "private": 0}, key=key)
                runs = list((_data(env) or {}).get("runs") or []) if env else []
            else:
                runs = fb_runs.get(oid, [])
            runs = [r for r in _latest_per_spec(r for r in runs if isinstance(r, dict) and r.get("run_id")
                                                and r.get("run_id") not in private_runs)]
            kept = []
            for r in runs:
                plot = None
                if inc["figures"]:
                    if live_runs:
                        penv = rec.get("/api/runs/%s/plot" % quote(r["run_id"], safe=""))
                        plot = _data(penv) if penv else None
                    elif r["run_id"] in fb_plots:
                        plot = fb_plots[r["run_id"]]
                        rec.put(route_key("GET", "/api/runs/%s/plot" % quote(r["run_id"], safe="")), plot,
                                "szk.ma.plot/v2")
                if plot is not None:
                    kctx = _kb_context(r, plot)
                    if kctx and kctx not in kb_contexts:
                        kb_contexts.append(kctx)
                kept.append(r)
                extra["runs"].append(collections.OrderedDict((("run_id", r["run_id"]), ("outcome_id", oid),
                                                              ("plot", plot is not None))))
            rec.put(key, collections.OrderedDict((("runs", kept),)), "szk.ma.runs/v1")
            all_kept.extend(kept)
        # kimenetenként a legutóbbi elsődleges futás (az Áttekintés táblája: GET /api/runs?primary=1)
        penv = rec.get("/api/runs", {"primary": 1, "private": 0},
                       key=route_key("GET", "/api/runs", {"primary": 1})) if live_runs else None
        if penv is None:
            prim_spec = {o.get("id"): o.get("primary_spec") for o in pdata.get("outcomes") or [] if isinstance(o, dict)}
            primary = collections.OrderedDict()
            for r in all_kept:
                sp = r.get("spec") if isinstance(r.get("spec"), dict) else {}
                want = prim_spec.get(r.get("outcome_id"))
                if (want and sp.get("path") == want) or (not want and (sp.get("purpose") == "primary"
                                                                       or not sp.get("parent"))):
                    primary[r.get("outcome_id")] = r
            rec.put(route_key("GET", "/api/runs", {"primary": 1}),
                    collections.OrderedDict((("runs", list(primary.values())),)), "szk.ma.runs/v1")
        else:
            ids = {r.get("run_id") for r in all_kept}
            pdat = _data(penv) or {}
            if isinstance(pdat.get("runs"), list):
                pdat["runs"] = [r for r in pdat["runs"] if isinstance(r, dict) and r.get("run_id") in ids]
        sdir = root / "05_elemzes" / "specs"
        if sdir.is_dir():
            for name in sorted(p.name for p in sdir.iterdir() if p.is_file() and p.name.endswith(".json")):
                rec.get("/api/specs/%s" % quote(name[:-5], safe=""))
    extra["outcomes"] = outcomes
    if rec.has_route("GET", "/api/kb/rules"):
        for f in KB_FIELDS:
            rec.get("/api/kb/rules", {"field": f})
            # az Eredmények ⓚ-sora a futás kontextusával kér (csak a futásra illő szabályok, UX-06)
            if f in KB_RUN_FIELDS:
                for kctx in kb_contexts:
                    rec.get("/api/kb/rules", dict(kctx, field=f))

    # -- adattáblák, eredet, dokumentumok
    measures = {}
    for o in pdata.get("outcomes") or []:
        if isinstance(o, dict) and isinstance(o.get("data"), str):
            measures[_norm_rel(o["data"])] = o.get("measure")
    datasets = [t.get("dataset") for t in pdata.get("tables") or [] if isinstance(t, dict)]
    for ds in measures:
        if ds not in datasets and not _is_private_real(root, ds) and (root / ds).is_file():
            datasets.append(ds)
    # a _privat/ alá mutató link (03_adatok/x.csv → ../_privat/…) is privát: a fájl valódi helye számít (WS-6)
    datasets = sorted(d for d in datasets if isinstance(d, str) and not _is_private_real(root, d))
    tables_on = inc["data_tables"]
    for ds in datasets:
        tkey = route_key("GET", "/api/table", {"dataset": ds})
        table = None
        if tables_on:
            env = rec.get("/api/table", {"dataset": ds})
            table = _data(env) if env else None
        else:
            rec.refuse(tkey, _excluded_message(pol), code="NOT_FOUND")
        if inc["provenance"]:
            rec.get("/api/provenance", {"dataset": ds})
        measure = measures.get(ds)
        if measure:
            vdoc = None
            if table is not None and rec.has_route("POST", "/api/validate"):
                status, venv = rec.call("POST", "/api/validate", body=_validate_body(table, measure))
                if venv is not None and venv.get("ok") is True:
                    rec.routes[route_key("POST", "/api/validate", {"dataset": ds})] = venv
                    vdoc = _data(venv)
            if vdoc is None:
                try:
                    from metaelemzes import api as engine_api
                    vdoc = engine_api.validate_file(str(root / ds), measure)
                except Exception:                      # noqa: BLE001 — a validálás hiánya nem állítja meg
                    vdoc = None
                if vdoc is not None and tables_on:
                    rec.put(route_key("POST", "/api/validate", {"dataset": ds}), vdoc, "szk.ma.validation/v1")
            if vdoc is not None:
                extra["validation"][ds] = _validation_summary(vdoc)
    # a dokumentum-jegyzék (csak azonosító + sha256 + oldalszám marad); C osztályban a _privat/ alatt él → soha
    docs_rel = app.documents_rel() if hasattr(app, "documents_rel") else store.DOCUMENTS_REL
    if (inc["provenance"] or tables_on) and not _is_private(docs_rel):
        rec.get("/api/documents")

    # -- v1: értékelések, GRADE/SoF, kettős kinyerés, ábra-export, Metaheadhunter (a képernyők pontos kérései)
    extra["rater_alias"] = _record_v1(rec, app, pol, pdata, outcomes, private_runs)

    # -- KB-tételek (a rögzített adatban hivatkozottak; a teljes szöveg soha)
    ids = set()
    for env in rec.routes.values():
        ids.update(_KB_ID_RE.findall(canonical_json(env)))
    for env in list(rec.routes.values()):
        d = _data(env)
        items = d.get("items") if isinstance(d, dict) else None
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict):
                for k in ("id", "rule_id"):
                    if isinstance(it.get(k), str) and _KB_ID_RE.fullmatch(it[k]):
                        ids.add(it[k])
    kb_ok = []
    for kid in sorted(ids)[:MAX_KB_ITEMS]:
        env = rec.get("/api/kb/item/%s" % quote(kid, safe=""))
        if env is None:
            continue
        d = _data(env) or {}
        if d.get("table") == "chunk" or d.get("local_only"):
            rec.routes.pop(route_key("GET", "/api/kb/item/%s" % quote(kid, safe="")), None)
            continue
        kb_ok.append(kid)
    extra["kb_ids"] = kb_ok
    extra["engine_version"] = engine_version
    # B/C osztályban (vagy kérésre) az értékelő-azonosítók álnévre — minden válaszban, nem csak az értékeléseknél
    return rename_raters(rec.routes, extra.get("rater_alias") or {}), extra


# -------------------------------------------------------------------------------------- v1-képernyők
# a munkapad Metaheadhunter-lépései → a parancssor parancsai (routes/headhunter.STEPS-szel azonos)
HH_STEP_COMMANDS = (("find_reviews", "find-reviews"), ("extract", "extract"), ("resolve", "resolve"),
                    ("dedupe", "dedupe"), ("overlap", "overlap"), ("update_search", "update-search"),
                    ("cite_search", "cite-search"), ("merge", "merge"), ("prisma", "prisma"), ("export", "export"),
                    ("verify", "verify"))
RESERVED_RATERS = ("consensus", "ai")
MAX_APPRAISAL_DOCS = 400
MAX_HH_REVIEWS = 60
ROB_TOOLS = ("rob2", "robins-i", "robins-e", "quadas2", "nos", "quips", "jbi")
HH_LISTS = ("status", "studies", "proposals", "overlap", "merged", "prisma", "update", "decisions")
# <egység>.<eszköz>[.<cél>].<értékelő>.json — a munkapad routes/appraisal_common._FILE_RE-jével azonos
_APPRAISAL_FILE_RE = re.compile(r"^([A-Za-z0-9_-]{1,64})\.([a-z0-9][a-z0-9-]{0,31})(?:\.([A-Za-z0-9][A-Za-z0-9_-]{0,63}))?"
                                r"\.([A-Za-z][A-Za-z0-9_-]{0,31})\.json$")


def enc_path(text):
    """Egy útszakasz a JS encodeURIComponent-jével azonos kódolásban (a kliens kulcsa ezzel egyezik)."""
    return quote(str(text), safe="!*'()~")


def rater_aliases(raters, pol):
    """Értékelő-azonosító → a pillanatképben látható alak. B/C osztályban (vagy kérésre) monogram, legfeljebb 3
    karakter — így a kitakaró bejárás (initials) változatlanul hagyja, és a kulcsok (…?rater=) is egyeznek; ütközésnél
    sorszám. A fenntartott értékelők (consensus, ai) változatlanok."""
    out = collections.OrderedDict()
    used = set()
    for r in sorted(set(x for x in raters if isinstance(x, str))):
        if r in RESERVED_RATERS or not pol["redact"]["assessors"]:
            out[r] = r
            continue
        base = (initials(r) or "?")[:3]
        alias, n = base, 2
        while alias in used or alias in RESERVED_RATERS:
            alias = "%s%d" % (base[:2], n)
            n += 1
        used.add(alias)
        out[r] = alias
    return out


def rename_raters(routes, aliases):
    """Az értékelő-azonosító az álnevére MINDEN rögzített válaszban: a fájlnevekben (…<egység>.<eszköz>[.<cél>].
    <értékelő>.json — értékben, listaelemben és szótárkulcsban is, pl. a project audit 'inputs'-a), az értékelés-
    nézetekben pedig minden pontosan egyező szövegben (pl. az egyezés 'a' / 'b' értékelője). Így a kitakaró bejárás
    és a kliens kulcsai (…?rater=) egyformán az álnevet látják."""
    changes = {r: a for r, a in aliases.items() if r != a}
    if not changes:
        return routes
    alt = "|".join(re.escape(r) for r in sorted(changes, key=len, reverse=True))
    pat = re.compile(r"\.(%s)\.json\b" % alt)
    # SEC-5: a korábbi felülbírálás-naplóbejegyzések szövegében a név „(értékelő: <név>)” alakban áll
    named = re.compile(r"\((értékelő|rater|jóváhagyó|approver): (%s)\)" % alt)

    def sub(text):
        text = pat.sub(lambda m: "." + changes[m.group(1)] + ".json", text)
        return named.sub(lambda m: "(%s: %s)" % (m.group(1), changes[m.group(2)]), text)

    def walk(node, exact):
        if isinstance(node, dict):
            return collections.OrderedDict((sub(k) if isinstance(k, str) else k, walk(v, exact)) for k, v in node.items())
        if isinstance(node, list):
            return [walk(x, exact) for x in node]
        if isinstance(node, str):
            return changes[node] if exact and node in changes else sub(node)
        return node
    out = collections.OrderedDict()
    for key, env in routes.items():
        out[key] = walk(env, key.startswith(("GET /api/appraisals", "GET /api/instruments")))
    return out


def _record_appraisals(rec, app, pol, pdata, outcomes):
    """Értékelő eszközök, értékelések (egyenként is), konszenzus-nézetek és a forgalmi lámpa → rater-álnevek."""
    lst = rec.get("/api/appraisals")
    items = [it for it in ((_data(lst) or {}).get("items") or []) if isinstance(it, dict) and it.get("tool")]
    aliases = rater_aliases([it.get("rater") for it in items], pol)
    tools = sorted(set(it["tool"] for it in items) | set(
        t for t in (pdata.get("appraisal_tools") or []) if isinstance(t, str)))
    if rec.get("/api/instruments") is not None:
        for tool in tools:
            rec.get("/api/instruments/%s" % enc_path(tool))
    for tool in sorted(set(it["tool"] for it in items)):
        rec.get("/api/appraisals", {"tool": tool})
        if tool == "tripod-ai":
            rec.get("/api/appraisals", {"tool": tool, "answers": "1"})
    groups = collections.OrderedDict()
    for it in items[:MAX_APPRAISAL_DOCS]:
        unit = it.get("unit")
        if not isinstance(unit, str) or not it.get("rater"):
            continue
        # a fájlnév cél-része (a lista 'target' mezője a dokumentum target-objektuma is lehet)
        m = _APPRAISAL_FILE_RE.match(str(it.get("path") or "").rsplit("/", 1)[-1])
        target = m.group(3) if m else (it.get("target") if isinstance(it.get("target"), str) else None)
        q = {"rater": aliases.get(it["rater"], it["rater"])}
        if target:
            q["target"] = target
        path = "/api/appraisals/%s/%s" % (enc_path(unit), enc_path(it["tool"]))
        status, env = rec.call("GET", path, {"rater": it["rater"], "target": target})
        if env is not None and env.get("ok") is True:
            rec.routes[route_key("GET", path, q)] = env
        if it.get("human"):
            groups.setdefault((unit, it["tool"], target), []).append(it["rater"])
    for (unit, tool, target), raters in groups.items():
        if len(raters) >= 2:
            rec.get("/api/appraisals/consensus/%s/%s" % (enc_path(unit), enc_path(tool)),
                    {"target": target} if target else None)
    for tool in sorted(set(it["tool"] for it in items) & set(ROB_TOOLS)):
        rec.get("/api/appraisals/rob-summary", {"tool": tool})
        for oid in outcomes:
            rec.get("/api/appraisals/rob-summary", {"tool": tool, "outcome": oid})
    return aliases


def _record_v1(rec, app, pol, pdata, outcomes, private_runs):
    """A v1-képernyők (értékelések, GRADE/SoF, Protokoll, kettős kinyerés, ábra-export, adapterek,
    Metaheadhunter) GET-válaszai a kitakarási szabályok szerint. Visszaad: az értékelő-álnevek térképe."""
    inc = pol["include"]
    root = app.project_root
    aliases = {}
    before = set(rec.routes)
    if inc["appraisals"]:
        aliases = _record_appraisals(rec, app, pol, pdata, outcomes)
        # GRADE / SoF: a bizonyosság ítélete és a motor tanácsa (számok a futásból, kész szövegekkel)
        for oid in outcomes:
            genv = rec.get("/api/grade/%s" % enc_path(oid))
            if genv is not None:
                # a tanács ugyanazzal a lekérdezéssel, amit a felület küld (grade.js loadAdvice: mentett MID + futás):
                # MID nélkül a pontatlanság-ellenőrzés a nullhatáshoz mérne (FID-6)
                gd = _data(genv) or {}
                mid = ((gd.get("grade") or {}).get("mid_text") or "").strip() if isinstance(gd, dict) else ""
                run_id = ((gd.get("run") or {}).get("run_id") if isinstance(gd.get("run"), dict) else None) \
                    if isinstance(gd, dict) else None
                q = dict((k, v) for k, v in (("mid", mid or None), ("run", run_id)) if v)
                rec.get("/api/grade/%s/advice" % enc_path(oid), q or None)
                if q:
                    rec.get("/api/grade/%s/advice" % enc_path(oid))     # tartalék (a kliens kulcsa nélkül)
                rec.get("/api/sof/%s" % enc_path(oid))
    rec.get("/api/protocol")
    # kettős kinyerés: a jegyzék mindig (darabszámok, utak — a _privat/ alattiak nélkül); az összevetés cellaértéket
    # hordoz, ezért csak adattáblákkal (A osztály vagy kérésre)
    env = rec.get("/api/kettos")
    d = _data(env)
    if isinstance(d, dict):
        d["outcomes"] = [o for o in d.get("outcomes") or [] if isinstance(o, dict)
                         and not _is_private_real(root, o.get("dir") or "")
                         and not _is_private_real(root, o.get("data") or "")]
        d["inbox"] = [x for x in d.get("inbox") or [] if isinstance(x, dict)
                      and not _is_private_real(root, x.get("path") or "")]
        if inc["data_tables"]:
            for o in d["outcomes"]:
                a, b = o.get("a") or {}, o.get("b") or {}
                if a.get("exists") and b.get("exists"):
                    status, venv = rec.call("POST", "/api/compare", body={"outcome": o["id"], "tables": True})
                    if venv is not None and venv.get("ok") is True:
                        rec.routes[route_key("POST", "/api/compare", {"outcome": o["id"]})] = venv
    # ábra-export: a commit-futások ábrái és a már exportált ábrák (X002 elavulás-jelöléssel)
    if inc["figures"]:
        fenv = rec.get("/api/figures")
        fd = _data(fenv)
        if isinstance(fd, dict):
            fd["runs"] = [r for r in fd.get("runs") or [] if isinstance(r, dict) and r.get("run_id") not in private_runs]
            for r in fd["runs"]:
                rec.get("/api/figures", {"run": r["run_id"]})
    rec.get("/api/adapters")
    # Metaheadhunter: állapot, források (kulcs-ÉRTÉK soha, csak beállítva igen/nem), listák; a kiválasztott
    # áttekintések részletei (legfeljebb MAX_HH_REVIEWS); az idézetek az általános idézet-kitakarással mennek ki
    st = rec.get("/api/headhunter/status")
    if st is not None and (_data(st) or {}).get("initialized", (_data(st) or {}).get("state") is not None):
        rec.get("/api/headhunter/sources")
        for name in HH_LISTS[1:]:
            rec.get("/api/headhunter/" + name)
        renv = rec.get("/api/headhunter/reviews")
        sel = [it.get("review_id") for it in ((_data(renv) or {}).get("items") or [])
               if isinstance(it, dict) and it.get("status") == "selected" and it.get("review_id")]
        for rid in sel[:MAX_HH_REVIEWS]:
            rec.get("/api/headhunter/reviews/%s" % enc_path(rid))
    # a _privat/ alól jövő futásra hivatkozó v1-válasz (pl. a GRADE / SoF / ábra-export alapja egy privát futás)
    # nem kerülhet bele: a kliens „nincs a pillanatképben” üzenetet kap (a manifeszt '_privat/** — soha' ígérete)
    marks = [m for ms in _private_run_marks(app).values() for m in ms] + ["_privat/05_elemzes"]
    for key in sorted(set(rec.routes) - before):
        text = canonical_json(rec.routes[key])
        if any(m in text for m in marks):
            del rec.routes[key]
            if key.startswith("GET ") and not any(m in key for m in marks):
                rec.refuse(key, "Ez a nézet egy _privat/ alól jövő (bizalmas) futásra hivatkozik, ezért nincs a "
                                "pillanatképben (terv 7.4).")
    return aliases


def _excluded_message(pol):
    dc = pol["data_class"]
    if dc == "C":
        return ("Az adattábla nincs a pillanatképben: C osztályú (betegszintű) projektből cellaérték soha nem "
                "kerülhet bele (terv 7.4).")
    if dc == "B":
        return ("Az adattábla nincs a pillanatképben: B osztályú (nem publikált) adat, alapból kitakarva. A teljes "
                "táblát az élő munkapad mutatja.")
    return "Az adattábla nincs a pillanatképben (a készítője kikapcsolta)."


# -------------------------------------------------------------------------------------- kitakarás
def _path_scrubber(root, home):
    roots = []
    for r in {str(root), os.path.realpath(str(root))}:
        for v in (r, r.replace("/", "\\"), r.replace("\\", "/")):
            if v and v not in roots:
                roots.append(v)
    roots.sort(key=len, reverse=True)
    homes = []
    if home:
        for h in {str(home), os.path.realpath(str(home))}:
            if h and len(h) > 1 and h not in homes:
                homes.append(h)
        homes.sort(key=len, reverse=True)

    def scrub(s):
        if not isinstance(s, str) or not s:
            return s
        for r in roots:
            if r in s:
                s = s.replace(r, PROJECT_TOKEN)
        for h in homes:
            if h in s:
                s = s.replace(h, "~")

        def tail(m):
            p = m.group(0).rstrip("/\\")
            last = re.split(r"[\\/]", p)[-1] if p else ""
            return "<…>/" + last if last else "<…>"
        return _ABS_PATH_RE.sub(tail, s)
    return scrub


def apply_redactions(routes, pol, root, home=None):
    """A kitakarások alkalmazása minden rögzített borítékra → (új routes, számlálók)."""
    counts = collections.Counter()
    scrub = _path_scrubber(root, home) if pol["redact"]["abs_paths"] else None
    tables_on = pol["include"]["data_tables"]

    def fn(k, v):
        lk = str(k).lower()
        if lk in _VOLATILE_ZERO and isinstance(v, (int, float)) and not isinstance(v, bool):
            return 0
        if pol["redact"]["quotes"] and lk in _QUOTE_KEYS and v not in (None, ""):
            counts["quotes"] += 1
            return None
        if pol["redact"]["assessors"]:
            if lk in ("actor", "resolved_actor") and isinstance(v, str):
                nv = _actor_initials(v)
                if nv != v:
                    counts["assessors"] += 1
                return nv
            if lk in _ASSESSOR_KEYS or lk in _RATER_EXTRA_KEYS:
                def one(x):
                    return x if not isinstance(x, str) or x in RESERVED_RATERS else initials(x)
                counts["assessors"] += 1
                if isinstance(v, list):
                    return [one(x) if isinstance(x, str) else x for x in v]
                if isinstance(v, dict) and all(isinstance(x, (str, type(None))) for x in v.values()):
                    return collections.OrderedDict((kk, one(x)) for kk, x in v.items())
                return one(v)
        if scrub is not None and isinstance(v, str):
            nv = scrub(v)
            if nv != v:
                counts["abs_paths"] += 1
            return nv
        if scrub is not None and isinstance(v, list) and all(isinstance(x, str) for x in v):
            nv = [scrub(x) for x in v]
            if nv != v:
                counts["abs_paths"] += 1
            return nv
        return v

    out = collections.OrderedDict()
    for key, env in routes.items():
        env = _walk(env, fn)
        path = key.split(" ", 1)[1].split("?", 1)[0]
        data = env.get("data") if env.get("ok") else None
        if isinstance(data, dict):
            if path.endswith("/plot") and not tables_on:
                n = _strip_plot_cells(data)
                counts["plot_cells"] += n
            elif path == "/api/provenance" and not tables_on:
                counts["provenance_values"] += _strip_provenance_values(data)
            elif path == "/api/documents":
                counts["documents"] += _strip_documents(data)
            elif path.startswith("/api/appraisals") and pol["redact"]["quotes"]:
                counts["quotes"] += _strip_evidence_text(data)
            elif path == "/api/compare" and not tables_on:          # pragma: no cover — tábla nélkül nem rögzül
                data.clear()
            elif path == "/api/project" and scrub is None:
                pass
        out[key] = env
    return out, counts


def _strip_evidence_text(data):
    """Az értékelések bizonyíték-idézete (answers[*].evidence.text) az idézet-kitakarással kimarad; az oldal és a
    lokátor marad (a forrás így visszakereshető)."""
    counter = [0]

    def strip(node):
        if isinstance(node, dict):
            ev = node.get("evidence")
            if isinstance(ev, dict) and ev.get("text") not in (None, ""):
                ev["text"] = None
                counter[0] += 1
            for v in node.values():
                strip(v)
        elif isinstance(node, list):
            for x in node:
                strip(x)
    strip(data)
    return counter[0]


def _strip_plot_cells(plot):
    """Adattábla nélkül a forest nyers cellaoszlopai (pl. „Esemény/N”) kimaradnak."""
    n = 0
    for s in plot.get("studies") or []:
        if isinstance(s, dict) and s.get("cells"):
            s["cells"] = {}
            n += 1
    if plot.get("columns"):
        plot["columns"] = []
    return n


def _strip_provenance_values(data):
    """Az eredet-adatból a bevitt és a származtatott értékek kimaradnak — a cella alatt BÁRMILYEN mélységben
    (az átváltó bemenetei/kimenetei, az egyeztetés A/B értéke, a korábbi bejegyzések értékei, idézetek); a
    dokumentum, az oldal, a módszer, az átváltás fajtája és a szereplők maradnak."""
    prov = data.get("provenance") if isinstance(data.get("provenance"), dict) else data
    counter = [0]

    def strip(node):
        if isinstance(node, dict):
            for k in list(node.keys()):
                lk = str(k).lower()
                if lk in _PROV_VALUE_KEYS or lk in _QUOTE_KEYS:
                    node.pop(k, None)
                    counter[0] += 1
                else:
                    strip(node[k])
        elif isinstance(node, list):
            for x in node:
                strip(x)

    for c in prov.get("cells") or []:
        if isinstance(c, dict):
            strip(c)
    return counter[0]


def _strip_documents(data):
    """A dokumentum-jegyzékből csak az azonosító, a sha256 és az oldalszám marad (PDF soha, út sem)."""
    n = 0
    docs = data.get("docs")
    if not isinstance(docs, list):
        return 0
    keep = ("id", "sha256", "pages", "kind", "page_count")
    new = []
    for d in docs:
        if not isinstance(d, dict):
            continue
        nd = collections.OrderedDict((k, d[k]) for k in keep if k in d)
        if len(nd) != len(d):
            n += 1
        new.append(nd)
    data["docs"] = new
    return n


def _normalize_envelopes(routes, ts):
    """A kérésenként változó meta-mezők rögzítése (determinizmus)."""
    out = collections.OrderedDict()
    for key in sorted(routes):
        env = routes[key]
        if env.get("ok") is True:
            meta = env.get("meta") if isinstance(env.get("meta"), dict) else {}
            env["meta"] = collections.OrderedDict((("engine", meta.get("engine")), ("project_rev", 0),
                                                   ("elapsed_ms", 0), ("request_id", "snapshot")))
            data = env.get("data")
            if isinstance(data, dict):
                env["data"] = _walk(data, lambda k, v: ts if (str(k) in _VOLATILE_TS and isinstance(v, str)) else v)
                if key.split("?", 1)[0] == "GET /api/project":
                    env["data"]["rev"] = 0
            env["warnings"] = [w for w in env.get("warnings") or [] if isinstance(w, str)]
        out[key] = env
    return out


# -------------------------------------------------------------------------------------- parancsok
# Az író kérések helyett a pillanatkép a pontos parancsot mutatja/másolja (2.5). A sablon elemei:
# szöveg (szó szerint), "{mező}" / "{mező|alap}" (a kérés törzséből; a {project} a projektmappa), vagy
# lista = opcionális csoport (csak ha minden mezője kitöltött). Az argv-ket a teszt a motor
# argparse-ával ellenőrzi (tests/gui/test_snapshot.py).
def command_templates():
    def w(method, path, argv, when=None, note=None):
        d = collections.OrderedDict((("method", method), ("path", path), ("kind", "write"), ("argv", argv)))
        if when:
            d["when"] = when
        if note:
            d["note"] = _reason(*note)
        return d

    def r(method, path, when=None, note=None):
        d = collections.OrderedDict((("method", method), ("path", path), ("kind", "read")))
        if when:
            d["when"] = when
        if note:
            d["note"] = _reason(*note)
        return d
    live = ["ma.py", "gui", "--project", "{project}"]
    return [
        r("POST", "/api/validate", note=("Élő validáláshoz az élő munkapad kell.", "Live validation needs the "
                                                                                    "live workbench.")),
        r("POST", "/api/convert", note=("Az átváltó az élő munkapadban fut.", "The converter runs in the live "
                                                                              "workbench.")),
        r("POST", "/api/compare"),
        r("POST", "/api/fileurl", note=("A PDF-ek és fájlok nincsenek a pillanatképben (csak doc-id + oldal + "
                                        "sha256).", "PDFs and files are not in the snapshot (doc id + page + "
                                                    "sha256 only).")),
        r("PUT", "/api/prisma/manual", when={"dry_run": True}),
        r("POST", "/api/privacy/apply", when={"dry_run": True}),
        r("POST", "/api/analyze", when={"mode": "explore"},
          note=("Az új (explore) elemzés az élő munkapadban fut; a pillanatkép a rögzített futásokat mutatja.",
                "New (explore) analyses run in the live workbench; the snapshot shows the committed runs.")),
        w("POST", "/api/analyze", ["ma.py", "analyze", "--spec", "05_elemzes/specs/{spec.name}.json", "--project",
                                   "{project}"], when={"mode": "commit"}),
        w("POST", "/api/log/decision", ["ma.py", "project", "log", "{project}", "--agent", "{agent|user}",
                                        "--decision", "{decision}", ["--rationale", "{rationale}"],
                                        ["--stage", "{stage}"], ["--kb", "{kb_refs}"],
                                        ["--alternatives", "{alternatives}"], ["--supersedes", "{supersedes}"]]),
        w("POST", "/api/log/finding", ["ma.py", "project", "finding", "{project}", "--agent", "{agent|user}",
                                       "--severity", "{severity}", "--title", "{title}", ["--detail", "{detail}"],
                                       ["--stage", "{stage}"], ["--evidence", "{evidence}"], ["--kb", "{kb_refs}"]]),
        w("POST", "/api/log/resolve", ["ma.py", "project", "resolve", "{project}", "{id}", "--status", "{status}",
                                       "--resolution", "{resolution|-}"]),
        w("POST", "/api/log/checkpoint", ["ma.py", "project", "checkpoint", "{project}", "--stage", "{stage}",
                                          "--agent", "{agent|user}", "--verdict", "{verdict}",
                                          ["--summary", "{summary}"]]),
        w("POST", "/api/export/audit", ["ma.py", "gui", "audit-export", "--project", "{project}"]),
        w("POST", "/api/export/snapshot", ["ma.py", "gui", "snapshot", "--project", "{project}"]),
        w("PUT", "/api/table", live, note=("A tábla az élő munkapadban vagy Excelben szerkeszthető.",
                                           "Edit the table in the live workbench or in Excel.")),
        # Metaheadhunter: a lépések és a döntések pontos parancsa (az ember döntése a saját --actor user:<név>-vel)
        w("POST", "/api/headhunter/run", ["ma.py", "headhunter", "init", "{project}", "--question",
                                          "{options.question}"], when={"step": "init"}),
        w("POST", "/api/headhunter/run", ["ma.py", "headhunter", "sources", "{project}", "--check"],
          when={"step": "sources_check"}),
    ] + [
        w("POST", "/api/headhunter/run", ["ma.py", "headhunter", cmd, "{project}"], when={"step": step})
        for step, cmd in HH_STEP_COMMANDS
    ] + [
        w("POST", "/api/headhunter/decide", ["ma.py", "headhunter", "decide", "{project}", "--target", "{target}",
                                             "--value", "{value}", "--actor", "user:<név>",
                                             ["--reason", "{reason}"]], when={"kind": "decide"}),
        w("POST", "/api/headhunter/decide", ["ma.py", "headhunter", "signoff", "{project}", "--actor", "user:<név>"],
          when={"kind": "signoff"}),
        w("*", "*", live, note=("Ehhez a művelethez az élő munkapad kell (a pillanatkép csak olvasható).",
                                "This action needs the live workbench (the snapshot is read-only)."))
    ]


# -------------------------------------------------------------------------------------- sablon, CSP
def load_template():
    """A pillanatkép-sablon (build_gui.py --snapshot kimenete); ha nincs meg, a forrásból épül."""
    for p in TEMPLATE_CANDIDATES:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    build_py = WEB_DIR / "build_gui.py"
    if not build_py.is_file():
        raise SnapshotError("A pillanatkép-sablon hiányzik (ma_gui/web/dist/snapshot.html), és a forrás sincs meg.",
                            code="CAPABILITY_MISSING")
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ma_build_gui", str(build_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build(variant="snapshot")


_BLOCK_RE = re.compile(r"<(script|style)\b([^>]*)>(.*?)</\1\s*>", re.S | re.I)
_TYPE_RE = re.compile(r"""\btype\s*=\s*["']?([^"'\s>]+)""", re.I)
_JS_TYPES = ("", "text/javascript", "application/javascript", "module")


def csp_hash(text):
    return "'sha256-%s'" % base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


def inline_hashes(html):
    """(script-hash-ek, style-hash-ek) a végrehajtható inline elemekre (adatblokkok nélkül)."""
    scripts, styles = [], []
    for m in _BLOCK_RE.finditer(html):
        tag, attrs, body = m.group(1).lower(), m.group(2), m.group(3)
        if tag == "script":
            t = _TYPE_RE.search(attrs)
            if t and t.group(1).lower() not in _JS_TYPES:
                continue
            if re.search(r"\bsrc\s*=", attrs, re.I):
                raise SnapshotError("Külső szkript nem lehet a pillanatképben.", code="INTERNAL")
            scripts.append(csp_hash(body))
        else:
            styles.append(csp_hash(body))
    return scripts, styles


def make_csp(html):
    scripts, styles = inline_hashes(html)
    return CSP_TEMPLATE.format(scripts=" ".join(scripts) or "'none'", styles=" ".join(styles) or "'none'")


def render(template, payload):
    """Sablon + adat → kész HTML (a CSP a végleges tartalom hash-eivel)."""
    if template.count(CSP_PLACEHOLDER) != 1 or template.count(DATA_PLACEHOLDER) != 1:
        raise SnapshotError("A pillanatkép-sablon hibás (helyőrzők).", code="INTERNAL")
    if "{{CSP_NONCE}}" in template or "nonce=" in template:
        raise SnapshotError("A pillanatkép-sablonban nem maradhat nonce.", code="INTERNAL")
    html = template.replace(DATA_PLACEHOLDER, json_for_script(payload))
    csp = make_csp(html.replace(CSP_PLACEHOLDER, ""))
    return html.replace(CSP_PLACEHOLDER, csp)


# -------------------------------------------------------------------------------------- összeállítás
def _engine_selftest():
    """A motor-önteszt folyamaton belül (metaelemzes.api.selftest_status; gyorsítótárazott) a fejléc jelvényéhez."""
    try:
        from metaelemzes import api as engine_api
        res = engine_api.selftest_status()
    except Exception:                                  # noqa: BLE001 — a jelvény legyen 'ismeretlen'
        res = None
    if not isinstance(res, dict):
        return {"state": "unknown", "ok": None}
    return {k: res.get(k) for k in ("state", "ok", "cases", "active", "checks", "passed", "failed")}


def _open_app(project_dir, lang):
    from . import server
    return server.App(project_dir, lang=lang, selftest=False, kb_build=False, caps_refresh=False, log_stream=None,
                      idle_hours=0, sweep_tmp=False)


def build(project_dir=None, app=None, include=None, redact=None, lang="hu", template=None, environ=None,
          home=None):
    """A pillanatkép bájtjai és leírása: (html_bytes, info). Vagy ``app`` (futó szerver) vagy
    ``project_dir`` kell. info: {sha256, bytes, state_time, state_id, name, manifest, redactions, excluded}."""
    own = app is None
    if own:
        if project_dir is None:
            raise SnapshotError("Hiányzó projektmappa.")
        app = _open_app(project_dir, lang)
    try:
        app.require_open()
        dc = app.data_class()
        pol = resolve_policy(dc, include, redact)
        routes, extra = record(app, pol)
        home = home if home is not None else os.path.expanduser("~")
        routes, counts = apply_redactions(routes, pol, app.project_root, home)
        # a projektállapot ideje csak projekt-eredetű adatból (a KB és a motor metaadata nem az)
        ts = state_time([env for k, env in routes.items()
                         if not k.startswith(("GET /api/kb/", "GET /api/engine", "GET /api/capabilities"))], environ)
        routes = _normalize_envelopes(routes, ts)
        if "GET /api/engine" in routes and routes["GET /api/engine"].get("ok"):
            routes["GET /api/capabilities"] = collections.OrderedDict((
                ("ok", True), ("schema", "szk.ma.capabilities-report/v1"),
                ("data", _synthetic_caps(extra.get("engine_version"), ts)), ("warnings", []),
                ("meta", routes["GET /api/engine"]["meta"])))
        proj = _data(routes.get("GET /api/project")) or {}
        title = proj.get("title") if isinstance(proj.get("title"), str) else app.project_root.name
        redactions, excluded = policy_report(pol)
        counts_out = collections.OrderedDict(sorted((k, v) for k, v in counts.items() if v))
        manifest = collections.OrderedDict((
            ("schema", MANIFEST_SCHEMA),
            ("project", slug(title)),
            ("title", title),
            ("created", ts),
            ("state_time", ts),
            ("data_class", pol["data_class"]),
            ("policy", collections.OrderedDict((("include", pol["include"]), ("redact", pol["redact"])))),
            ("redactions", redactions),
            ("excluded", excluded),
            ("redaction_counts", counts_out),
            ("routes", sorted(routes)),
            ("runs", extra["runs"]),
            ("outcomes", extra["outcomes"]),
            ("validation", extra["validation"]),
            ("kb_ids", extra["kb_ids"]),
            ("versions", collections.OrderedDict((("engine", extra.get("engine_version")), ("gui", __version__)))),
        ))
        dir_hint = PROJECT_DIR_HINT if pol["redact"]["abs_paths"] else str(app.project_root)
        payload = collections.OrderedDict((
            ("schema", SCHEMA),
            ("project", collections.OrderedDict((("title", title), ("name", slug(title)),
                                                 ("data_class", pol["data_class"]), ("dir_hint", dir_hint)))),
            ("state_time", ts),
            ("manifest", manifest),
            ("routes", routes),
            ("commands", command_templates()),
        ))
        state_id = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        payload["state_id"] = state_id
        tpl = template if template is not None else load_template()
        html = render(tpl, payload).encode("utf-8")
        name = "%s-%s-%s%s" % (slug(title), ts[:10], state_id[:8], SNAPSHOT_SUFFIX)
        info = collections.OrderedDict((
            ("sha256", hashlib.sha256(html).hexdigest()), ("bytes", len(html)), ("state_time", ts),
            ("state_id", state_id), ("name", name), ("data_class", pol["data_class"]),
            ("redactions", redactions), ("excluded", excluded), ("manifest", manifest), ("network", False)))
        return html, info
    finally:
        if own:
            app.close()


PRIVATE_EXPORT_REL = privacy.PRIVATE_DIR + "/export"


def export_target(app, rel):
    """Az export helye az írási szabályok szerint (ugyanaz a szerveren és a parancssorban, WS-8): a kért
    projekt-relatív hely, vagy ha az írás-tartás (C osztály, vagy vault által követett B projekt .gitignore
    nélkül) ezt nem engedi, a _privat/export/ alatti → (rel, figyelmeztetések). Ha az sem írható:
    SnapshotError(FORBIDDEN) az okkal."""
    ok, reason = app.can_write(rel)
    if ok:
        return rel, []
    alt = "%s/%s" % (PRIVATE_EXPORT_REL, rel.rsplit("/", 1)[-1])
    ok2, _reason2 = app.can_write(alt)
    if ok2:
        return alt, ["Az export a %s alá került, mert a %s nem írható (%s) — a vault ezt a mappát nem tolja fel."
                     % (PRIVATE_EXPORT_REL + "/", rel, reason)]
    raise SnapshotError(reason or "Az export helye nem írható.", code="FORBIDDEN")


def cli_save(app, default_rel, data, out_arg=None):
    """A parancssori export mentése (snapshot és audit-export közös): --out nélkül a szerverrel azonos hely
    (export_target); --out-tal a megadott fájl — ha az a projekten belül van, ugyanazokkal az írási
    szabályokkal (C osztálynál csak a _privat/ alá). → (abszolút út, figyelmeztetések)."""
    root = Path(os.path.realpath(str(app.project_root)))
    if out_arg:
        target = Path(os.path.realpath(os.path.expanduser(str(out_arg))))
        if store.is_within(str(target), str(root)) and target != root:
            rel = target.relative_to(root).as_posix()
            ok, reason = app.can_write(rel)
            if not ok:
                raise SnapshotError("A megadott hely (%s) nem írható: %s Adj meg a _privat/ alatti helyet, vagy hagyd "
                                    "el az --out kapcsolót." % (rel, reason), code="FORBIDDEN")
        write(target, data)
        return target, []
    rel, warnings = export_target(app, default_rel)
    target = root.joinpath(*rel.split("/"))
    write(target, data)
    return target, warnings


def write(path, data):
    """Atomikus írás (tmp + os.replace) tetszőleges helyre."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s%s.tmp" % (security.TMP_PREFIX, path.name))
    with open(str(tmp), "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))
    return path


# -------------------------------------------------------------------------------------- parancssor
def _parse_toggle_list(values):
    out = []
    for v in values or []:
        for part in str(v).split(","):
            p = part.strip().lower().replace("-", "_")
            if not p:
                continue
            key = CLI_ALIASES.get(p) or CLI_ALIASES.get(p.replace("_", ""))
            if key is None:
                raise SnapshotError("Ismeretlen kapcsoló: %s (lehetséges: %s)." % (
                    part.strip(), ", ".join(sorted(set(CLI_ALIASES)))))
            out.append(key)
    return out


def toggles_from_cli(redact_list, keep_list):
    """--redact X (kitakar / kihagy) és --keep X (megtart / belefoglal) → (include, redact) kérés."""
    include, redact_ = {}, {}
    for key, on in [(k, True) for k in _parse_toggle_list(redact_list)] + \
                   [(k, False) for k in _parse_toggle_list(keep_list)]:
        if key in REDACT_KEYS:
            redact_[key] = on
        else:
            include[key] = not on
    return include, redact_


def build_parser(prog="python ma.py gui snapshot"):
    p = argparse.ArgumentParser(
        prog=prog,
        description="Csak olvasható, kitakaró HTML-pillanatkép a projektről társszerzőknek: egyetlen fájl, "
                    "Python és hálózat nélkül nyitható. Soha ne töltsd fel és ne publikáld (Claude Artifactként "
                    "sem).",
        epilog="Kapcsolók (--redact / --keep): tables (adattáblák), provenance (eredet), quotes (idézetek), "
               "assessors (értékelők nevei → monogram), paths (abszolút utak), log, runs, figures, prisma, "
               "activity. Alapértékek az adatosztályból: A — táblák benne; B — táblák ki, értékelők monogrammal; "
               "C — táblák soha. Idézetek és abszolút utak mindig ki, ha nem kéred másként.")
    p.add_argument("--project", default=".", help="a projektmappa (alap: az aktuális mappa)")
    p.add_argument("--out", help="a kimeneti HTML (alap: <projekt>/%s/<név>%s; ha az adatosztály miatt ott nem "
                                 "írható, a _privat/export/ alá — mint a munkapadon)" % (OUT_DIR_REL, SNAPSHOT_SUFFIX))
    p.add_argument("--redact", action="append", metavar="MIT", help="kitakarás / kihagyás (vesszővel több is)")
    p.add_argument("--keep", action="append", metavar="MIT", help="megtartás / belefoglalás (vesszővel több is)")
    p.add_argument("--lang", choices=("hu", "en"), default="hu", help="a felület nyelve (alap: hu)")
    p.add_argument("--json", action="store_true", help="a leírás JSON-ként a stdout-ra")
    return p


def main(argv=None, out=None):
    """``ma.py gui snapshot`` belépési pontja → kilépési kód (0 kész, 2 hibás kérés, 1 egyéb hiba)."""
    out = out or sys.stdout
    a = build_parser().parse_args(argv)
    project = Path(os.path.expanduser(a.project))
    if not project.is_dir():
        print("HIBA: a projektmappa nem létezik vagy nem mappa: %s" % project, file=out)
        return 2
    app = None
    try:
        include, redact_ = toggles_from_cli(a.redact, a.keep)
        app = _open_app(str(project), a.lang)
        html, info = build(app=app, include=include, redact=redact_, lang=a.lang)
        target, warnings = cli_save(app, "%s/%s" % (OUT_DIR_REL, info["name"]), html, a.out)
    except SnapshotError as exc:
        print("HIBA: %s" % exc.message, file=out)
        return 2
    except Exception as exc:                      # noqa: BLE001 — érthető üzenet, nem traceback
        msg = getattr(exc, "message", None) or str(exc)
        print("HIBA: a pillanatkép nem készült el (%s: %s)" % (type(exc).__name__, msg), file=out)
        return 1
    finally:
        if app is not None:
            app.close()
    if a.json:
        print(canonical_json(dict(info, path=str(target), warnings=warnings)), file=out)
    else:
        for w in warnings:
            print("Figyelem: %s" % w, file=out)
        print("Pillanatkép kész: %s" % target, file=out)
        print("  %d bájt · sha256 %s · projektállapot: %s · adatosztály: %s" % (
            info["bytes"], info["sha256"], info["state_time"], info["data_class"]), file=out)
        if info["redactions"]:
            print("  Kitakarva: %s" % ", ".join(r["hu"] for r in info["redactions"]), file=out)
        print("  Figyelem: soha ne töltsd fel és ne publikáld (Claude Artifactként sem); csak közvetlenül oszd meg "
              "a társszerzőkkel.", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
