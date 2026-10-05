# -*- coding: utf-8 -*-
"""Metaheadhunter — nyilvános Python-homlokzat (TERV 20.5): a későbbi ``metaelemzes.api`` bekötés célpontja.

Minden függvény JSON-képes dict-et ad vissza: a parancssor borítékát (``{ok, command, data, warnings, errors,
pending, next, exit_code}``). A homlokzat a ``cli.execute`` útján ugyanazt a kódot futtatja, mint a parancssor —
így a GUI, az ``api.py`` és a CLI viselkedése (kilépési kódok, emberi döntés szabályai, naplózás) azonos.

Kezdőknek: ``run_step`` = egy lépés (keresés, kinyerés, feloldás …); ``decide`` = emberi döntés (mindig
``actor="user:<név>"``; az ágens és a program csak javasol — N3).

Példa::

    from metaelemzes.headhunter import facade as hh
    hh.init("projekt", "BCG és tuberkulózis", pico={"population": "tuberculosis", "intervention": "BCG vaccine"})
    hh.run_step("projekt", "find_reviews")
    hh.decide("projekt", "review_select", "rv-pmid-8309034", "include", "user:SzK")
"""
from __future__ import absolute_import

import json
import os
import tempfile

from . import cli

#: lépésnév → CLI-parancs
STEP_COMMANDS = {
    "find_reviews": "find-reviews", "extract": "extract", "resolve": "resolve", "dedupe": "dedupe",
    "overlap": "overlap", "screen_propose": "screen", "update_search": "update-search", "cite_search": "cite-search",
    "merge": "merge", "prisma": "prisma", "export": "export", "report": "report",
}

#: opció → CLI-kapcsoló (értékes kapcsolók)
_VALUE_FLAGS = {
    "query": "--query", "query_file": "--query-file", "since": "--since", "until": "--until", "max": "--max",
    "review": "--review", "strategy": "--strategy", "pdf": "--pdf", "level": "--level", "anchor": "--anchor",
    "start": "--start", "end": "--end", "overlap_months": "--overlap-months", "cap": "--cap", "cite": "--cite",
    "seeds": "--seeds", "direction": "--direction", "outcome": "--outcome", "sources": "--sources",
    "actor": "--actor", "lang": "--lang",
}
#: logikai kapcsolók
_BOOL_FLAGS = {
    "offline": "--offline", "fulltext_dates": "--fulltext-dates", "force": "--force",
    "include_unknown": "--include-unknown", "csv": "--csv", "dry_run": "--dry-run", "to_project": "--to-project",
    "prisma": "--prisma", "for_analysis": "--for-analysis", "check": "--check",
}


class FacadeError(ValueError):
    pass


def _args(options):
    out = []
    for k, v in sorted((options or {}).items()):
        if v is None or v is False:
            continue
        if k in _BOOL_FLAGS:
            if v:
                out.append(_BOOL_FLAGS[k])
        elif k in _VALUE_FLAGS:
            if isinstance(v, (list, tuple)):
                v = ",".join(str(x) for x in v)
            out.append("%s=%s" % (_VALUE_FLAGS[k], v))
        else:
            raise FacadeError("Ismeretlen opció: %s" % k)
    return out


def _run(argv):
    env, _a = cli.execute(argv)
    return env


def init(project_dir, question, pico=None, mode="harvest", actor=None):
    """Indítás: ``state.json``, PICO, okszótár. ``pico``: dict (``{question, population, intervention, …,
    query_blocks}`` vagy ``{pico, criteria, exclusion_reasons}``)."""
    argv = ["init", project_dir, "--question=%s" % question, "--mode=%s" % mode]
    tmp = None
    try:
        if pico is not None:
            fd, tmp = tempfile.mkstemp(suffix=".json", prefix="hh-pico-")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(pico, fh, ensure_ascii=False)
            argv.append("--pico=%s" % tmp)
        if actor:
            argv.append("--actor=%s" % actor)
        return _run(argv)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def sources_status(project_dir=None, check=False):
    argv = ["sources"] + ([project_dir] if project_dir else []) + (["--check"] if check else [])
    return _run(argv)


def run_step(project_dir, step, **options):
    """Egy lépés futtatása. ``step`` ∈ ``STEP_COMMANDS``; az ``options`` a CLI kapcsolóinak megfelelői
    (pl. ``max=50``, ``sources="pubmed,europepmc"``, ``dry_run=True``, ``actor="user:…"``)."""
    if step not in STEP_COMMANDS:
        raise FacadeError("Ismeretlen lépés: %s (%s)" % (step, ", ".join(sorted(STEP_COMMANDS))))
    cmd = STEP_COMMANDS[step]
    opts = dict(options)
    if step == "screen_propose":
        argv = ["screen", project_dir, "propose"]
        if opts.pop("file", None):
            raise FacadeError("A screen_propose ágens-fájlját a CLI-n add át (screen … propose <fájl>).")
    else:
        argv = [cmd, project_dir]
    return _run(argv + _args(opts))


_KIND_VALUES = {
    "review_select": {"include": "include", "exclude": "exclude"},
    "candidate_confirm": {"confirm": "include", "include": "include"},
    "candidate_reject": {"reject": "exclude", "exclude": "exclude"},
}


def decide(project_dir, kind, target, value, actor, level=None, reason_code=None, reason=None, evidence_ids=(),
           batch=None, proposed_by=None, supersedes=None):
    """Emberi döntés rögzítése (``actor`` = ``user:<név>``). A ``kind`` a döntésnapló fajtája; a cél és az érték a
    CLI ``decide`` parancsának szabályai szerint (feloldási javaslatnál ``option:N`` / ``pmid:…``)."""
    if not str(actor or "").startswith("user:"):
        raise FacadeError("Döntést csak ember rögzíthet: actor='user:<név>' (N3).")
    if kind == "signoff":
        argv = ["signoff", project_dir, "--actor=%s" % actor] + (["--reason=%s" % reason] if reason else [])
        return _run(argv)
    if kind == "source_config":
        en = [s for s, on in (value or {}).items() if on] if isinstance(value, dict) else []
        dis = [s for s, on in (value or {}).items() if not on] if isinstance(value, dict) else []
        argv = ["sources", project_dir, "--actor=%s" % actor]
        if en:
            argv.append("--enable=%s" % ",".join(en))
        if dis:
            argv.append("--disable=%s" % ",".join(dis))
        return _run(argv)
    if kind == "update_window":
        return run_step(project_dir, "update_search", actor=actor, **(value if isinstance(value, dict) else {}))
    v = _KIND_VALUES.get(kind, {}).get(value, value)
    targets = target if isinstance(target, (list, tuple)) else [target]
    argv = ["decide", project_dir, "--target=%s" % ",".join(targets), "--value=%s" % v, "--actor=%s" % actor]
    if level:
        argv.append("--level=%s" % level)
    if reason_code:
        argv.append("--reason-code=%s" % reason_code)
    if reason:
        argv.append("--reason=%s" % reason)
    if batch or evidence_ids or proposed_by or supersedes:
        # ezek a CLI-ben a tömeges kapcsolókból / a javaslatból származnak; itt csak jelezzük, hogy nem vesznek el
        env = _run(argv)
        env.setdefault("warnings", []).append({"code": "INFO", "hu": "A batch/evidence_ids/proposed_by/supersedes "
                                                                     "mezőt a CLI a saját forrásából tölti.",
                                               "en": "batch/evidence_ids/proposed_by/supersedes are filled by the "
                                                     "CLI."})
        return env
    return _run(argv)


def status(project_dir):
    return _run(["status", project_dir])


def verify(project_dir, for_analysis=False):
    return _run(["verify", project_dir] + (["--for-analysis"] if for_analysis else []))


def rebuild(project_dir):
    return _run(["rebuild", project_dir])


def show_text(project_dir, review_id, parts=("methods", "results", "tables")):
    return _run(["show-text", project_dir, "--review=%s" % review_id, "--part=%s" % ",".join(parts)])


__all__ = ["init", "sources_status", "run_step", "decide", "status", "verify", "rebuild", "show_text",
           "STEP_COMMANDS", "FacadeError"]
