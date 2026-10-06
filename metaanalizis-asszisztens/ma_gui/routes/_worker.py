# -*- coding: utf-8 -*-
"""A meleg worker-folyamatban futó munkapad-feladatok (terv 2.2, 2.6) — a ``JobManager`` hívási célpontjai.

``commit_analysis`` a „Rögzítés”: a motor-homlokzat ``api.analyze(mode='commit')``-ja és EGY activity-sor
egy feladatként, így a feladat csak akkor 'done', ha a futás és a napló is kész. A ``jobs.py`` maga
stdlib-only marad (a pluginadapterek is használják); ez a modul köti a motorhoz."""
import json
import os

from metaelemzes import api

from .. import activity

COMMIT_TARGET = "ma_gui.routes._worker:commit_analysis"
ACTIVITY_WARNING = "A tevékenységnaplót (07_ellenorzes/activity.jsonl) nem sikerült írni; a futás elkészült."


def _journal_summary_text(project_root, run_dir):
    """A CLI-vel bájtra azonos result.summary: a motor a futást a projektnaplóba írta (projekt.log_run, a CLI
    _run_summary-jével); ugyanabból a summary-ból a motor run_summary_text-je. Nincs napló/sor → None."""
    if not run_dir:
        return None
    want = os.path.normcase(os.path.realpath(os.path.join(project_root, run_dir)))
    try:
        rows = api.project_list(project_root, "runs")
    except Exception:                                    # noqa: BLE001 — napló nélküli projekt
        return None
    items = rows.get("items") if isinstance(rows, dict) else rows
    for row in sorted((r for r in (items or []) if isinstance(r, dict)), key=lambda r: -(r.get("id") or 0)):
        outdir = row.get("outdir")
        if not outdir or os.path.normcase(os.path.realpath(outdir)) != want:
            continue
        summ = row.get("summary")
        if isinstance(summ, str):
            try:
                summ = json.loads(summ)
            except ValueError:
                return None
        return api.run_summary_text(summ)
    return None


def commit_analysis(project_root, spec, spec_path, client_seq=None, actor="user"):
    """Commit-futás a meleg workerben (a munkapad ``POST /api/analyze {mode: commit}``-ja hívja):
    ``api.analyze(mode='commit')`` — a motor kimenetei + run.json a 05_elemzes/<kimenet>/<run_id>/
    mappába, a futás a projektnaplóba (``projekt.log_run``, actor) —, majd EGY activity-sor
    (``analyze.commit``: az egyenértékű argv, a bemenetek és a kimenetek sha256-ja, cellaérték nélkül).
    Az activity-hiba figyelmeztetés, nem bukás. Statisztikát nem számol: minden szám és szöveg a motoré
    (a summary a motor display_text-je)."""
    view = api.analyze(spec, mode="commit", project_root=project_root, spec_path=spec_path,
                       client_seq=client_seq, actor=actor)
    run = view.get("run") or {}
    files = [f for f in (run.get("files") or {}).values() if isinstance(f, dict) and f.get("path")]
    outputs = [(f["path"], f.get("sha256")) for f in files]
    if files:
        outputs.append(files[0]["path"].rsplit("/", 1)[0] + "/run.json")
    inputs = []
    data = run.get("data") or {}
    if data.get("path"):
        inputs.append((data["path"], data.get("sha256")))
    sp = run.get("spec") or {}
    if sp.get("path"):
        inputs.append((sp["path"], sp.get("sha256")))
    summary = _journal_summary_text(project_root, files[0]["path"].rsplit("/", 1)[0] if files else None)
    if summary is None:
        # tartalék (napló nélkül): ugyanazok a motor-szövegek, a CLI alakjában
        text = ((run.get("primary") or {}).get("display_text") or {}).get("hu")
        measure = ((spec.get("options") or {}).get("measure") if isinstance(spec, dict) else None) or ""
        parts = []
        if run.get("k") is not None:
            parts.append("k=%s" % run["k"])
        if text:
            parts.append(("%s %s" % (measure, text)).strip())
        summary = ", ".join(parts) or None
    details = {"run_id": run.get("run_id"), "spec": sp.get("name"), "client_seq": client_seq}
    try:
        rec = activity.ActivityLog(project_root).append(
            "analyze.commit", actor, argv=run.get("equivalent_argv"), inputs=inputs, outputs=outputs,
            result={"exit_code": 0, "summary": summary},
            details={k: v for k, v in details.items() if v is not None})
        view["activity"] = {"seq": rec["seq"]}
    except Exception:                                    # noqa: BLE001 — a futás már kész
        view["activity"] = None
        view["warnings"] = list(view.get("warnings") or []) + [ACTIVITY_WARNING]
    return view
