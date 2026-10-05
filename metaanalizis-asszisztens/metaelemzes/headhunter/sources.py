# -*- coding: utf-8 -*-
"""Metaheadhunter — forrás-regiszter: választható források, beállítás (``state.json`` + környezet),
állapot-ellenőrzés (``sources --check``) és a CLI-hez/felülethez szükséges táblázat-adatok.

Kezdőknek: a Metaheadhunter több adatbázisból dolgozhat. Projektenként kiválaszthatod, melyikből:

=============  ==========================================  =================================================
kulcs          szolgáltatás                                kell-e kulcs?
=============  ==========================================  =================================================
``pubmed``     PubMed (NCBI E-utilities)                   nem (``MA_NCBI_APIKEY`` gyorsít)
``europepmc``  Europe PMC                                  nem
``openalex``   OpenAlex                                    nem, de ajánlott (``MA_OPENALEX_APIKEY``)
``scopus``     Scopus (Elsevier)                           IGEN: ``MA_SCOPUS_APIKEY`` (+ ``MA_SCOPUS_INSTTOKEN``)
``ctgov``      ClinicalTrials.gov                          nem
``crossref``   Crossref (automatikus tartalék, nem választható)  nem
=============  ==========================================  =================================================

Minden forrásnak állapota van: ``ok``, ``not_configured``, ``unreachable``, ``rate_limited``,
``unauthorized``, ``forbidden``, ``disabled``, ``unknown`` — magyar magyarázattal (mit jelent, mit tegyél).

Önálló belépési pont (amíg a ``python -m metaelemzes.headhunter sources --check`` nincs bekötve, és tartaléknak):
``python -m metaelemzes.headhunter.sources --check [--json] [--lang en] [--project <mappa>]``.

Kompatibilitás: a TERV 20.1 ``sources/<forrás>.py`` alcsomagot ír; a kliensmodulok itt laposan élnek
(``metaelemzes.headhunter.pubmed`` …), de a ``metaelemzes.headhunter.sources.pubmed`` import-útvonal is
működik (álnév), és ``sources.pubmed.Client`` attribútumként is elérhető.
"""

from __future__ import absolute_import

import argparse
import importlib
import json
import os
import sys

from . import net
from .net import get_env, status_explain, source_name

#: a felhasználó által választható források (a ``state.json`` ``sources`` kötelező kulcsai)
SELECTABLE = ("pubmed", "europepmc", "openalex", "scopus", "ctgov")
#: automatikus tartalék-forrás (nem választható, de az állapota látszik)
AUTOMATIC = ("crossref",)
ALL_SOURCES = SELECTABLE + AUTOMATIC

#: a kliensmodulok (lapos elrendezés; álnév: ``sources.<név>``)
CLIENT_MODULES = ("pubmed", "europepmc", "openalex", "scopus", "ctgov", "crossref")

#: hibaállapotok, amelyeknél a lépés részleges (H014, kilépési kód 3)
PROBLEM_STATUSES = ("unreachable", "rate_limited", "unauthorized", "forbidden")

SOURCE_INFO = {
    "pubmed": {
        "name": "PubMed", "platform": "PubMed (NCBI E-utilities API)",
        "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
        "default_enabled": True, "requires_key": False, "env": [net.ENV_NCBI_APIKEY, net.ENV_CONTACT_EMAIL],
        "auth": {"hu": "nincs; opcionális MA_NCBI_APIKEY (10 kérés/s 3 helyett)",
                 "en": "none; optional MA_NCBI_APIKEY (10 requests/s instead of 3)"},
        "role": {"hu": "áttekintések keresése, metaadat, hivatkozás-feloldás (ecitmatch), regiszterszám (DataBank)",
                 "en": "review search, metadata, citation matching (ecitmatch), registry numbers (DataBank)"},
    },
    "europepmc": {
        "name": "Europe PMC", "platform": "Europe PMC REST API",
        "base_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/",
        "default_enabled": True, "requires_key": False, "env": [],
        "auth": {"hu": "nincs", "en": "none"},
        "role": {"hu": "áttekintések keresése, nyílt teljes szöveg (bevont vizsgálatok kinyerése), hivatkozások, idézők",
                 "en": "review search, open full text (included-study extraction), references, citations"},
    },
    "openalex": {
        "name": "OpenAlex", "platform": "OpenAlex API",
        "base_url": "https://api.openalex.org/",
        "default_enabled": True, "requires_key": False, "env": [net.ENV_OPENALEX_APIKEY, net.ENV_CONTACT_EMAIL],
        "auth": {"hu": "nem kötelező; ajánlott ingyenes kulcs: MA_OPENALEX_APIKEY (saját napi keret)",
                 "en": "optional; a free key is recommended: MA_OPENALEX_APIKEY (own daily budget)"},
        "role": {"hu": "áttekintések keresése (type:review), irodalomjegyzék (referenced_works), idéző közlemények (cites:)",
                 "en": "review search (type:review), reference lists (referenced_works), citing works (cites:)"},
    },
    "scopus": {
        "name": "Scopus", "platform": "Scopus (Elsevier Scopus Search API)",
        "base_url": "https://api.elsevier.com/content/",
        "default_enabled": False, "requires_key": True, "env": [net.ENV_SCOPUS_APIKEY, net.ENV_SCOPUS_INSTTOKEN],
        "auth": {"hu": "MA_SCOPUS_APIKEY kötelező (dev.elsevier.com), MA_SCOPUS_INSTTOKEN opcionális (intézményi)",
                 "en": "MA_SCOPUS_APIKEY required (dev.elsevier.com), MA_SCOPUS_INSTTOKEN optional (institutional)"},
        "role": {"hu": "áttekintések keresése (DOCTYPE(re)), irodalomjegyzék (view=REF, ha jogosult), idézők (REFEID)",
                 "en": "review search (DOCTYPE(re)), reference lists (view=REF, if entitled), citing works (REFEID)"},
        "unverified_live": True,
    },
    "ctgov": {
        "name": "ClinicalTrials.gov", "platform": "ClinicalTrials.gov (API v2)",
        "base_url": "https://clinicaltrials.gov/api/v2/",
        "default_enabled": True, "requires_key": False, "env": [],
        "auth": {"hu": "nincs", "en": "none"},
        "role": {"hu": "regiszter-rekordok (NCT), regiszter-keresés a frissítéshez",
                 "en": "registry records (NCT), registry search for the update"},
    },
    "crossref": {
        "name": "Crossref", "platform": "Crossref REST API",
        "base_url": "https://api.crossref.org/",
        "default_enabled": True, "requires_key": False, "env": [net.ENV_CONTACT_EMAIL], "automatic": True,
        "auth": {"hu": "nincs (automatikus tartalék a DOI-feloldáshoz)", "en": "none (automatic DOI fallback)"},
        "role": {"hu": "DOI-metaadat tartalékként", "en": "DOI metadata as a fallback"},
    },
}


def client_module(source):
    """A forrás kliensmodulja (lusta import)."""
    if source not in CLIENT_MODULES:
        raise ValueError("Ismeretlen forrás: %r (választható: %s)." % (source, ", ".join(ALL_SOURCES)))
    return importlib.import_module("." + source, __package__)


def make_client(source, http=None, cfg=None, env=None):
    """``Client(http, cfg)`` példány a forráshoz."""
    return client_module(source).Client(http=http, cfg=cfg, env=env)


def key_flags(source, env=None):
    """``key_configured`` / ``insttoken_configured`` csak igen/nem (a kulcs értéke SOHA nem kerül ki)."""
    if source == "scopus":
        return get_env(net.ENV_SCOPUS_APIKEY, env) is not None, get_env(net.ENV_SCOPUS_INSTTOKEN, env) is not None
    if source == "openalex":
        return get_env(net.ENV_OPENALEX_APIKEY, env) is not None, None
    if source == "pubmed":
        return get_env(net.ENV_NCBI_APIKEY, env) is not None, None
    return None, None


def default_source_cfg(source, env=None):
    """Egy forrás alapbeállítása (``common.v1#/$defs/source_cfg``)."""
    info = SOURCE_INFO[source]
    key, tok = key_flags(source, env)
    enabled = bool(info["default_enabled"])
    status = "unknown"
    if source == "scopus":
        enabled = bool(key)  # kulccsal alapból bekapcsolva, kulcs nélkül nem
        status = "unknown" if key else "not_configured"
    cfg = {"enabled": enabled, "status": status, "checked_at": None,
           "message": status_explain(source, status) if status != "unknown" else None,
           "reset_at": None, "key_configured": key, "insttoken_configured": tok}
    if source == "scopus":
        cfg["entitlement"] = "unknown" if key else "none"
    return cfg


def default_config(env=None):
    """A ``state.json`` ``sources`` objektumának alapértéke (TERV 3.3)."""
    return dict((s, default_source_cfg(s, env)) for s in ALL_SOURCES)


_CFG_KEYS = ("enabled", "status", "checked_at", "message", "reset_at", "key_configured", "insttoken_configured",
             "entitlement")


def normalize_config(cfg=None, env=None):
    """A tárolt beállítás kiegészítése a hiányzó forrásokkal és a környezet aktuális kulcs-állapotával.

    A Scopus kulcs nélkül mindig ``not_configured`` (akkor is, ha korábban be volt állítva)."""
    out = {}
    cfg = cfg or {}
    for s in ALL_SOURCES:
        base = default_source_cfg(s, env)
        cur = cfg.get(s) or {}
        merged = dict(base)
        for k in _CFG_KEYS:
            if k in cur:
                merged[k] = cur[k]
        key, tok = key_flags(s, env)
        merged["key_configured"] = key
        merged["insttoken_configured"] = tok
        if s == "scopus":
            if not key:
                merged["status"] = "not_configured"
                merged["message"] = status_explain(s, "not_configured")
                merged["entitlement"] = "none"
            elif merged.get("status") == "not_configured":
                merged["status"] = "unknown"
                merged["message"] = None
                merged["entitlement"] = "unknown"
        if s in AUTOMATIC:
            merged["enabled"] = True
        out[s] = dict((k, merged.get(k)) for k in _CFG_KEYS if k in merged)
    return out


def effective_status(source, cfg_entry):
    """Megjelenítendő állapot: kikapcsolt forrásnál ``disabled`` (kivéve a nem beállított Scopust)."""
    st = (cfg_entry or {}).get("status") or "unknown"
    if not (cfg_entry or {}).get("enabled", True) and st != "not_configured":
        return "disabled"
    return st


def state_path(project_dir):
    return os.path.join(project_dir, "01_kereses", "headhunter", "state.json")


def load_state(project_dir):
    """A projekt ``state.json``-ja (csak olvasás; ``None``, ha még nincs)."""
    path = state_path(project_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def config_from_state(state=None, env=None):
    return normalize_config((state or {}).get("sources"), env)


def parse_source_list(text):
    """``"pubmed,europepmc"`` → ``["pubmed", "europepmc"]`` (ismeretlen név → ``ValueError`` magyarul)."""
    if text is None:
        return None
    items = text if isinstance(text, (list, tuple)) else str(text).split(",")
    out = []
    for it in items:
        s = str(it).strip().lower()
        if not s:
            continue
        if s not in ALL_SOURCES:
            raise ValueError("Ismeretlen forrás: %r. Választható: %s." % (s, ", ".join(SELECTABLE)))
        if s not in out:
            out.append(s)
    return out


def select_sources(state=None, override=None, env=None, include_automatic=False, cfg=None):
    """Melyik forrásokat használja egy lépés. ``override`` (``--sources a,b``) felülírja a projekt-beállítást.

    Visszaad: ``{"use": [forrás…], "skipped": [{"source", "status", "message"}]}`` — a kihagyás oka
    kezdőbarát üzenettel (pl. Scopus kulcs nélkül, kikapcsolt forrás, ismert kvóta-kimerülés)."""
    cfg = cfg or config_from_state(state, env)
    wanted = parse_source_list(override) if override is not None else [s for s in SELECTABLE if cfg[s].get("enabled")]
    if include_automatic:
        for s in AUTOMATIC:
            if s not in wanted:
                wanted.append(s)
    use, skipped = [], []
    for s in wanted:
        entry = cfg.get(s) or {}
        if s == "scopus" and not entry.get("key_configured"):
            skipped.append({"source": s, "status": "not_configured", "message": status_explain(s, "not_configured")})
            continue
        if entry.get("status") == "rate_limited" and entry.get("reset_at"):
            if entry["reset_at"] > net.utc_ts():
                skipped.append({"source": s, "status": "rate_limited",
                                "message": entry.get("message") or status_explain(s, "rate_limited", entry["reset_at"])})
                continue
        if entry.get("status") == "unauthorized":
            skipped.append({"source": s, "status": "unauthorized",
                            "message": entry.get("message") or status_explain(s, "unauthorized")})
            continue
        use.append(s)
    return {"use": use, "skipped": skipped}


def check_sources(sources=None, http=None, env=None, state=None):
    """Próbakérés forrásonként (3.2). Visszaad: ``{forrás: check-eredmény}``. Hálózati hiba nem állítja meg."""
    if http is None:
        http = net.HttpClient(env=env)
    env = env if env is not None else http.env
    srcs = parse_source_list(sources) if sources is not None else list(ALL_SOURCES)
    cfg = config_from_state(state, env)
    out = {}
    for s in srcs:
        client = make_client(s, http=http, cfg=cfg.get(s), env=env)
        out[s] = client.check()
    return out


def apply_checks(cfg, results):
    """Az ellenőrzés eredményének beírása a forrás-beállításba (csak a sémában engedett mezők; kulcs-érték
    SOHA). Visszaad: új ``sources`` dict (a hívó menti a ``state.json``-ba)."""
    out = dict((k, dict(v)) for k, v in (cfg or {}).items())
    for s, res in (results or {}).items():
        entry = out.setdefault(s, default_source_cfg(s))
        entry["status"] = res.get("status", "unknown")
        entry["checked_at"] = res.get("checked_at")
        entry["message"] = res.get("message")
        entry["reset_at"] = res.get("reset_at")
        if res.get("key_configured") is not None:
            entry["key_configured"] = bool(res["key_configured"])
        if s == "scopus":
            entry["insttoken_configured"] = bool(res.get("insttoken_configured"))
            if res.get("entitlement") in ("unknown", "search_only", "search_and_ref", "none"):
                entry["entitlement"] = res["entitlement"]
    return out


def set_enabled(cfg, enable=(), disable=(), env=None):
    """Forrásválasztás (``sources set --enable … --disable …``). Visszaad: ``(új_cfg, változások,
    figyelmeztetések)``; a változások a ``source_config`` döntés értéke (a hívó naplózza)."""
    enable = parse_source_list(enable) or []
    disable = parse_source_list(disable) or []
    both = set(enable) & set(disable)
    if both:
        raise ValueError("Ugyanaz a forrás nem lehet egyszerre be- és kikapcsolva: %s." % ", ".join(sorted(both)))
    for s in enable + disable:
        if s in AUTOMATIC:
            raise ValueError("A(z) %s automatikus tartalék-forrás, nem kapcsolható." % source_name(s))
    out = normalize_config(cfg, env)
    changes, warnings = [], []
    for s in enable:
        if not out[s]["enabled"]:
            out[s]["enabled"] = True
            changes.append({"source": s, "enabled": True})
        if s == "scopus" and not out[s].get("key_configured"):
            warnings.append({"code": "H014", "source": s, "hu": status_explain(s, "not_configured")["hu"],
                             "en": status_explain(s, "not_configured")["en"]})
    for s in disable:
        if out[s]["enabled"]:
            out[s]["enabled"] = False
            changes.append({"source": s, "enabled": False})
    return out, changes, warnings


def table_rows(cfg, lang="hu"):
    """Forrás-táblázat sorai (CLI és felület): név, be/ki, állapot, kulcs igen/nem, jogosultság, visszaállás,
    üzenet (``lang`` nyelven), szerep, élőben igazolt-e."""
    lang = "en" if lang == "en" else "hu"
    rows = []
    for s in ALL_SOURCES:
        e = (cfg or {}).get(s) or default_source_cfg(s)
        info = SOURCE_INFO[s]
        st = effective_status(s, e)
        msg = e.get("message") or status_explain(s, st, e.get("reset_at"))
        if st == "disabled":
            msg = status_explain(s, "disabled")
        rows.append({
            "source": s, "name": info["name"], "enabled": bool(e.get("enabled")), "status": st,
            "automatic": bool(info.get("automatic")), "key_configured": e.get("key_configured"),
            "insttoken_configured": e.get("insttoken_configured"), "entitlement": e.get("entitlement"),
            "reset_at": e.get("reset_at"), "checked_at": e.get("checked_at"),
            "message": msg.get(lang) if isinstance(msg, dict) else msg,
            "auth": info["auth"][lang], "role": info["role"][lang],
            "unverified_live": bool(info.get("unverified_live")),
        })
    return rows


_STATUS_HU = {"ok": "rendben", "not_configured": "nincs beállítva", "unreachable": "nem elérhető",
              "rate_limited": "keret elfogyott", "unauthorized": "kulcs elutasítva", "forbidden": "nincs jogosultság",
              "disabled": "kikapcsolva", "unknown": "nem ellenőrzött"}


def format_table(rows, lang="hu"):
    """Egyszerű szöveges táblázat a terminálra (magyarul, ``lang="en"`` angolul)."""
    en = lang == "en"
    head = ("Source", "On", "Status", "Key", "Message") if en else ("Forrás", "Be", "Állapot", "Kulcs", "Üzenet")
    lines = []
    data = []
    for r in rows:
        if r["automatic"]:
            on = "auto"
        else:
            on = ("yes" if en else "igen") if r["enabled"] else ("no" if en else "nem")
        key = r["key_configured"]
        key_s = "—" if key is None else (("yes" if en else "igen") if key else ("no" if en else "nem"))
        if r["source"] == "scopus" and r.get("insttoken_configured"):
            key_s += " +token"
        st = r["status"] if en else _STATUS_HU.get(r["status"], r["status"])
        if r["status"] == "rate_limited" and r.get("reset_at"):
            st += " (%s %s)" % ("until" if en else "eddig:", r["reset_at"])
        if r["source"] == "scopus" and r.get("entitlement") not in (None, "unknown", "none"):
            st += " [%s]" % r["entitlement"]
        data.append((r["name"], on, st, key_s, r["message"] or ""))
    widths = [max(len(head[i]), max([len(d[i]) for d in data] or [0])) for i in range(4)]
    fmt = "  ".join("%%-%ds" % w for w in widths) + "  %s"
    lines.append(fmt % head)
    lines.append("  ".join("-" * w for w in widths) + "  " + "-" * 20)
    for d in data:
        lines.append(fmt % d)
    if any(r["unverified_live"] for r in rows):
        lines.append("")
        lines.append("Note: the Scopus client is not yet verified live from the build environment; verify on your "
                     "machine with this command (MA_SCOPUS_APIKEY set)." if en else
                     "Megjegyzés: a Scopus-kliens a fejlesztői környezetből élőben nem volt igazolható; a saját gépeden "
                     "ezzel a paranccsal igazold (beállított MA_SCOPUS_APIKEY mellett).")
    return "\n".join(lines)


def sources_status(state=None, check=False, http=None, env=None, sources=None, project_dir=None, lang="hu"):
    """A ``sources [--check]`` parancs adatai (a facade és a CLI ezt csomagolja).

    Visszaad: ``{"sources": {forrás: cfg}, "rows": [...], "results": {…}|None, "warnings": [{code, hu, en,
    source}], "exit_code": 0|3, "checked": bool, "secrets": {...csak igen/nem}}``. Fájlt NEM ír: a
    frissített ``sources`` objektumot a hívó menti a ``state.json``-ba."""
    if state is None and project_dir:
        state = load_state(project_dir)
    env = env if env is not None else (http.env if http is not None else None)
    cfg = config_from_state(state, env)
    results = None
    if check:
        srcs = parse_source_list(sources) if sources is not None else list(ALL_SOURCES)
        results = check_sources(srcs, http=http, env=env, state=state)
        cfg = apply_checks(cfg, results)
    rows = table_rows(cfg, lang=lang)
    warnings = []
    exit_code = 0
    for r in rows:
        if r["enabled"] and r["status"] in PROBLEM_STATUSES:
            e = cfg[r["source"]]
            msg = e.get("message") or status_explain(r["source"], r["status"], r.get("reset_at"))
            warnings.append({"code": "H014", "source": r["source"], "status": r["status"],
                             "hu": msg.get("hu") if isinstance(msg, dict) else msg,
                             "en": msg.get("en") if isinstance(msg, dict) else msg})
            if not r["automatic"]:
                exit_code = 3
    return {"sources": cfg, "rows": rows, "results": results, "warnings": warnings, "exit_code": exit_code,
            "checked": bool(check), "secrets": net.secret_status(env)}


# ---------------------------------------------------------------------------------------------
# Önálló CLI (tartalék belépési pont): python -m metaelemzes.headhunter.sources --check
# ---------------------------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m metaelemzes.headhunter.sources",
        description="Metaheadhunter — források állapota (PubMed, Europe PMC, OpenAlex, Scopus, ClinicalTrials.gov, "
                    "Crossref). Kulcsot csak környezeti változóból olvasunk; parancssori kulcs-kapcsoló nincs.")
    p.add_argument("--check", action="store_true", help="próbakérés minden forráshoz (hálózatot használ)")
    p.add_argument("--project", default=None, help="projektmappa (a state.json forrásválasztását olvassa; nem ír)")
    p.add_argument("--sources", default=None, help="csak ezek a források, vesszővel (pl. pubmed,scopus)")
    p.add_argument("--json", action="store_true", help="gépi kimenet (boríték: ok, data, warnings, errors, next)")
    p.add_argument("--lang", choices=("hu", "en"), default="hu")
    p.add_argument("--offline", action="store_true", help="hálózat nélkül (a --check-et letiltja)")
    return p


def main(argv=None, stdout=None, env=None, http=None):
    """CLI. Kilépési kódok (TERV 14.): 0 rendben; 2 használati hiba; 3 egy bekapcsolt forrás nem érhető el."""
    out = stdout or sys.stdout
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code not in (None, 0) else 0
    try:
        if args.sources:
            parse_source_list(args.sources)
        res = sources_status(check=args.check and not args.offline, http=http, env=env, sources=args.sources,
                             project_dir=args.project, lang=args.lang)
    except ValueError as exc:
        msg = net.redact(str(exc), env)
        if args.json:
            out.write(net.dump_json({"ok": False, "data": None, "warnings": [],
                                     "errors": [{"code": "USAGE", "hu": msg, "en": msg}], "pending": [], "next": None}))
        else:
            out.write(msg + "\n")
        return 2
    nxt = None if args.check else "python -m metaelemzes.headhunter sources --check"
    if args.json:
        data = {"sources": res["sources"], "rows": res["rows"], "checked": res["checked"], "secrets": res["secrets"]}
        env_doc = {"ok": res["exit_code"] == 0, "data": data,
                   "warnings": [{"code": w["code"], "hu": w["hu"], "en": w["en"]} for w in res["warnings"]],
                   "errors": [], "pending": [], "next": nxt}
        out.write(net.redact(net.dump_json(env_doc), env))
    else:
        title = "Metaheadhunter — sources" if args.lang == "en" else "Metaheadhunter — források"
        lines = [title, ""]
        lines.append(format_table(res["rows"], lang=args.lang))
        if not args.check:
            lines.append("")
            lines.append(("Not checked yet. Run: %s" if args.lang == "en" else "Még nem ellenőriztük. Futtasd: %s")
                         % "python -m metaelemzes.headhunter sources --check")
        out.write(net.redact("\n".join(lines) + "\n", env))
    return res["exit_code"]


# ---------------------------------------------------------------------------------------------
# Álnevek: metaelemzes.headhunter.sources.<forrás> (a TERV 20.1/20.5 import-útvonala)
# ---------------------------------------------------------------------------------------------

def _install_aliases():
    for _name in CLIENT_MODULES:
        _mod = importlib.import_module("." + _name, __package__)
        sys.modules.setdefault(__name__ + "." + _name, _mod)
        globals()[_name] = _mod


_install_aliases()


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
