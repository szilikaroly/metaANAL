# -*- coding: utf-8 -*-
"""A motor szk.* adatszerződéseinek JSON Schema (2020-12) fájljai (terv 4.0, 4.19, 4.20; lásd README.md).

Fájl: <név>.v<N>.schema.json (pl. ma.plot.v2.schema.json); "$id": urn:szk:contract:<név>:<N>; a dokumentum
"schema" mezője: szk.<név>/v<N>. A fájl bájtjainak sha256-ja a szerződés-hash (szk.capabilities/v1
contracts[].sha256 — futásidejű sodródás-őr), ezért a formázás kanonikus: canonical_text() (a teszt őrzi).
Csak stdlib és os.path (importlib.resources nélkül), így a repóból és a pluginként telepített mappából is működik.

    load(name, version=None)    → a séma (dict; minden hívás friss példány)
    sha256(name, version=None)  → a fájl bájtjainak sha256-ja (hex)
    path(name, version=None)    → a fájl abszolút útja
    available()                 → [(név, főverzió)], rendezve
    index()                     → jegyzék: név, verzió, szerződés-azonosító, $id, fájl, sha256, irány, termelő, fogyasztó
    registry()                  → {$id: séma} — a fájlok közötti $ref-ek feloldásához
    sync(write=False)           → az analysis-spec generált options-része naprakész-e (write=True: frissíti)

A név megadható rövid ('ma.plot'), dokumentum-azonosító ('szk.ma.plot/v2'), URN ('urn:szk:contract:ma.plot:2')
vagy fájlnév ('ma.plot.v2.schema.json') alakban; verzió nélkül a legnagyobb meglévő főverzió.

    python3 -m metaelemzes.contracts [list] [--json]   jegyzék (név, verzió, irány, sha256)
    python3 -m metaelemzes.contracts check             a generált options-rész naprakész-e (eltérés: kilépési kód 1)
    python3 -m metaelemzes.contracts sync              a generált options-rész újraírása a spec.py-ból
"""
import hashlib
import json
import os
import re
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
SUFFIX = ".schema.json"
URN_PREFIX = "urn:szk:contract:"
DRAFT = "https://json-schema.org/draft/2020-12/schema"
PROSE_COMMENT = "a tervből (próza) — pontosítandó"

_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_URN_RE = re.compile(r"^urn:szk:contract:([a-z0-9][a-z0-9.\-]*):(\d+)#?$")
_FILE_RE = re.compile(r"^([a-z0-9][a-z0-9.\-]*?)\.v(\d+)$")
_DOC_RE = re.compile(r"^([a-z0-9][a-z0-9.\-]*)/v(\d+)$")

# (név, főverzió) → (irány a motor szemszögéből, termelő, fogyasztó); a README táblázata ugyanez
CONTRACTS = {
    ("common", 1): ((), "—", "minden szk.*-séma ($ref)"),
    ("capabilities", 1): (("out",), "motor és pluginok (--capabilities)", "munkapad (caps.py)"),
    ("ma.validation", 1): (("out",), "motor (api.validate_table, validate --json)", "munkapad, ma-ellenorzo"),
    ("ma.validate-request", 1): (("in",), "munkapad (élő validálás)", "motor (validate --request-json)"),
    ("ma.analysis-spec", 1): (("in",), "munkapad (05_elemzes/specs/), spec.spec_from_argv", "motor (analyze --spec)"),
    ("ma.run", 1): (("out",), "motor (run.json, analyze --json-summary)", "munkapad, project audit"),
    ("ma.plot", 2): (("out",), "motor (plot_data.json)", "munkapad, figure-forge (meta), motor-SVG"),
    ("ma.convert-request", 1): (("in",), "munkapad (átváltó)", "motor (api.convert)"),
    ("ma.convert-result", 1): (("out",), "motor (api.convert)", "munkapad, provenance.conversion"),
    ("ma.provenance", 1): (("in",), "munkapad (03_adatok/<kimenet>.prov.json)", "motor (project audit: X010, X013, X022)"),
    ("ma.compare-result", 1): (("out",), "motor (api.compare, E6)", "munkapad (egyeztetés)"),
    ("ma.consensus", 1): (("in",), "munkapad (kettos/<kimenet>.consensus.json)", "motor (konszenzus-CSV, E6)"),
    ("ma.studies", 1): (("in",), "munkapad (03_adatok/studies.json)", "motor (prisma check --studies, X014), composer"),
    ("ma.project-audit", 1): (("out",), "motor (project audit --json, E8)", "munkapad, ma-ellenorzo, FINAL audit-kapu"),
    ("ma.activity", 1): (("in", "out"), "munkapad és motor (MA_ACTIVITY_LOG=1)", "munkapad, audit-csomag (verify)"),
    ("ma.project", 1): (("in", "out"), "munkapad (projekt.save_project_meta)", "motor (projekt, audit), munkapad"),
    # v1 értékelés (metaelemzes.instruments, metaelemzes.appraisal)
    ("instrument", 1): (("out",), "motor (metaelemzes/instruments/*.json, appraisal.instrument_get); validator --schema",
                        "munkapad (értékelő űrlapok), validator-adapter"),
    ("appraisal", 1): (("in", "out"), "munkapad és motor (04_torzitas_kockazat/appraisals/*.json, appraisal.save)",
                       "motor (appraisal.check, project audit X003/X017), validator ≥ 1.1"),
    ("appraisal-result", 1): (("out",), "motor (appraisal.check); validator --verify/--rollup --json",
                              "munkapad (teljesség, implikált ítélet, X017)"),
    ("rob-summary", 1): (("out",), "motor (appraisal.rob_summary)", "munkapad (forgalmi lámpa), figure-forge (rob)"),
    ("ma.appraisal-agreement", 1): (("out",), "motor (appraisal.agreement)", "munkapad (konszenzus-nézet)"),
    ("ma.rob-sync-proposal", 1): (("out",), "motor (appraisal.rob_sync_proposal)",
                                  "munkapad (rob-oszlop szinkron), CLI appraisal sync-rob"),
    # v1 GRADE / SoF (metaelemzes.grade_help, projekt GRADE-tár; E10)
    ("ma.grade", 1): (("in", "out"), "motor (grade_help.advice piszkozat; projekt.save_grade_doc / record_grade_doc)",
                      "munkapad (GRADE-lap), projekt.record_grade_doc, project audit (X007, X019)"),
    ("ma.sof", 1): (("out",), "motor (grade_help.sof; 06_kezirat/sof/<kimenet>.sof.json)",
                    "munkapad (SoF-tábla, export), project audit (X008)"),
}


class ContractError(LookupError):
    """Ismeretlen szerződés vagy hiányzó sémafájl."""


def _version(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ContractError("érvénytelen főverzió: %r" % (value,))
    if isinstance(value, int):
        if value < 1:
            raise ContractError("érvénytelen főverzió: %r" % (value,))
        return value
    m = re.match(r"^v?(\d+)$", str(value).strip().lower())
    if not m or int(m.group(1)) < 1:
        raise ContractError("érvénytelen főverzió: %r (pl. 1, 'v1')" % (value,))
    return int(m.group(1))


def parse(name, version=None):
    """Bármely elfogadott névalak (+ verzió) → (rövid név, főverzió | None)."""
    raw = str(name).strip()
    low = raw.lower()
    ver = None
    m = _URN_RE.match(low)
    if m:
        low, ver = m.group(1), int(m.group(2))
    else:
        if low.endswith(SUFFIX):
            low = low[:-len(SUFFIX)]
        m = _DOC_RE.match(low) or _FILE_RE.match(low)
        if m:
            low, ver = m.group(1), int(m.group(2))
        if low.startswith("szk."):
            low = low[4:]
    if not _NAME_RE.match(low):
        raise ContractError("érvénytelen szerződésnév: %r" % (raw,))
    given = _version(version)
    if ver is not None and given is not None and ver != given:
        raise ContractError("ellentmondó főverzió: %r és %r" % (raw, version))
    return low, (ver if ver is not None else given)


def filename(name, version):
    return "%s.v%d%s" % (name, version, SUFFIX)


def urn(name, version):
    return "%s%s:%d" % (URN_PREFIX, name, version)


def document_id(name, version):
    """A dokumentumok "schema" mezője: szk.<név>/v<N>."""
    return "szk.%s/v%d" % (name, version)


def available():
    """[(név, főverzió)] a mappában lévő sémafájlokból, rendezve."""
    out = []
    for fn in os.listdir(DIR):
        if fn.endswith(SUFFIX):
            m = _FILE_RE.match(fn[:-len(SUFFIX)])
            if m and _NAME_RE.match(m.group(1)):
                out.append((m.group(1), int(m.group(2))))
    return sorted(out)


def _resolve(name, version=None):
    short, ver = parse(name, version)
    have = [v for n, v in available() if n == short]
    if not have:
        raise ContractError("ismeretlen szerződés: %s (elérhető: %s)" % (
            short, ", ".join(sorted({n for n, _ in available()}))))
    if ver is None:
        ver = max(have)
    elif ver not in have:
        raise ContractError("a(z) %s szerződésnek nincs v%d sémája (elérhető: %s)" % (
            short, ver, ", ".join("v%d" % v for v in sorted(have))))
    return short, ver


def path(name, version=None):
    short, ver = _resolve(name, version)
    return os.path.join(DIR, filename(short, ver))


def raw(name, version=None):
    """A sémafájl bájtjai (a hash ezekből készül)."""
    with open(path(name, version), "rb") as fh:
        return fh.read()


def _reject_constant(token):
    raise ValueError("nem véges szám a sémában: %s" % token)


def _loads(data):
    return json.loads(data.decode("utf-8"), parse_constant=_reject_constant)


def load(name, version=None):
    """A séma dict-ként (minden hívás új példány, a hívó szabadon módosíthatja)."""
    return _loads(raw(name, version))


def sha256(name, version=None):
    """A sémafájl bájtjainak sha256-ja (64 kisbetűs hex) — a capabilities contracts[].sha256 értéke."""
    return hashlib.sha256(raw(name, version)).hexdigest()


def canonical_text(schema):
    """A sémafájlok kanonikus formája (a bájt-azonos másolatok és a stabil hash feltétele)."""
    return json.dumps(schema, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def registry():
    """{$id: séma} minden sémafájlból — a "urn:szk:contract:…#/…" hivatkozások feloldásához."""
    return {urn(n, v): load(n, v) for n, v in available()}


def index():
    """A szerződés-jegyzék (fájlonként egy sor)."""
    rows = []
    for n, v in available():
        direction, producer, consumer = CONTRACTS.get((n, v), ((), "", ""))
        rows.append({"name": n, "version": v, "schema": document_id(n, v), "id": urn(n, v), "file": filename(n, v),
                     "sha256": sha256(n, v), "dir": list(direction), "producer": producer, "consumer": consumer})
    return rows


def direction(name, version=None):
    """Az irány a motor szemszögéből: ['in'], ['out'], ['in', 'out'] vagy [] (a közös definícióké)."""
    short, ver = _resolve(name, version)
    return list(CONTRACTS.get((short, ver), ((),))[0])


# ---------------------------------------------------------------- generált rész (analysis-spec options)
def generated_options():
    """Az analysis-spec 'options' generált része (required, properties) a spec.py introspekciójából."""
    from ..spec import analysis_spec_schema
    gen = analysis_spec_schema()["properties"]["options"]
    return {"required": gen["required"], "properties": gen["properties"]}


def _atomic_write_text(target, text):
    tmp = "%s.tmp%d" % (target, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def sync(write=False):
    """Az ma.analysis-spec.v1 generált options-része egyezik-e a spec.py-ból generálttal. write=True: eltérésnél
    újraírja (a többi rész változatlan). → True, ha (most már) naprakész."""
    schema = load("ma.analysis-spec", 1)
    opts = schema["properties"]["options"]
    gen = generated_options()
    if all(opts.get(k) == v for k, v in gen.items()):
        return True
    if not write:
        return False
    for k, v in gen.items():
        opts[k] = v
    _atomic_write_text(path("ma.analysis-spec", 1), canonical_text(schema))
    return True


def _main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    cmd = args[0] if args else "list"
    if cmd == "list":
        rows = index()
        if as_json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            for r in rows:
                print("%-24s v%d  %-8s %s  %s" % (r["name"], r["version"], ",".join(r["dir"]) or "-",
                                                 r["sha256"][:12], r["file"]))
        return 0
    if cmd in ("check", "sync"):
        ok = sync(write=(cmd == "sync"))
        if ok:
            print("ma.analysis-spec.v1: a generált options-rész naprakész")
            return 0
        print("ma.analysis-spec.v1: a generált options-rész eltér a spec.py-tól — futtasd: "
              "python3 -m metaelemzes.contracts sync", file=sys.stderr)
        return 1
    print("HIBA: ismeretlen parancs: %s (list, check, sync)" % cmd, file=sys.stderr)
    return 2
