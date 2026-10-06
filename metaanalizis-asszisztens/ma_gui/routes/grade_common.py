# -*- coding: utf-8 -*-
"""Közös segédek a GRADE / SoF / AMSTAR 2 / protokoll végpontokhoz (routes/grade*.py).

- ``outcome_of(app, id)``: a kimenet a ma-projekt.json-ból (404, ha nincs); az azonosító mintája a motoré.
- ``primary_run(app, outcome_id, run_id=None)``: a GRADE / SoF alapja — a kimenet legutóbbi elsődleges
  commit-futása (vagy a megadott futás, ha a kimenethez tartozik); a leíró a ``runs.decorate`` mezőivel
  (``stale``: X001). Explore-futásra GRADE nem hivatkozhat (terv 2.6).
- ``content_etag(doc)``: tartalom-alapú ETag (kanonikus JSON sha256-ja) olyan dokumentumhoz, amelyet a motor
  ír a saját helyére (GRADE-tár, értékelés-tár) — az If-Match így a hely ismerete nélkül is működik.
- ``own_dir_writes(app, dirs)``: a motor közvetlen írásait a változásfigyelő a munkapad saját írásának látja
  (nem „külső” szerkesztés)."""
import contextlib
import hashlib
import json
import os
import re

from .. import store
from ..router import ApiError
from . import runs as _runs

OUTCOME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
PROJECT_JSON = "ma-projekt.json"
MSG_NO_OUTCOME = ("Nincs ilyen kimenet a projektben (ma-projekt.json → outcomes). Vedd fel a Protokoll fülön "
                  "(Kimenetek → Új kimenet), vagy: python ma.py project outcome <mappa> --id … --name …")
MSG_NO_RUN = ("Ehhez a kimenethez még nincs rögzített (commit) futás. A GRADE és a SoF csak commit-futásra "
              "hivatkozhat, hogy minden közölt szám reprodukálható legyen: Elemzés fül → Rögzítés (commit).")
MAX_LIST = 200


def outcome_id_arg(raw):
    if not isinstance(raw, str) or not OUTCOME_RE.match(raw):
        raise ApiError("BAD_REQUEST", "Érvénytelen kimenet-azonosító (betű, szám, '_', '.', '-'; legfeljebb 64 "
                                      "karakter).")
    return raw


def outcome_of(app, raw):
    """(kimenet-objektum a ma-projekt.json-ból, a projekt metaadata)."""
    oid = outcome_id_arg(raw)
    meta, _etag = app.project_meta()
    for o in (meta or {}).get("outcomes") or []:
        if isinstance(o, dict) and o.get("id") == oid:
            return dict(o), meta
    raise ApiError("NOT_FOUND", MSG_NO_OUTCOME, {"outcome": oid})


def primary_run(app, outcome_id, run_id=None):
    """A GRADE / SoF futása: a megadott (a kimenethez tartozó commit-) futás, vagy a legutóbbi elsődleges.
    → dekorált futás-leíró vagy None (nincs commit-futás)."""
    if run_id:
        rel_dir, run = _runs.find_run(app, run_id)
        out = _runs.decorate(app, rel_dir, run)
        if out.get("outcome_id") != outcome_id:
            raise ApiError("BAD_REQUEST", "A megadott futás (%s) nem ehhez a kimenethez tartozik." % run_id)
        return out
    runs = _runs.list_runs(app, outcome_id, primary=True)
    return runs[0] if runs else None


def require_run(app, outcome_id, run_id=None):
    run = primary_run(app, outcome_id, run_id)
    if run is None:
        raise ApiError("NOT_FOUND", MSG_NO_RUN, {"outcome": outcome_id})
    return run


def run_dir_abs(app, run):
    """A futás mappájának abszolút útja (a motor-függvények bemenete)."""
    try:
        return str(app.store.path(run["dir"]))
    except store.StoreError as exc:
        raise ApiError("NOT_FOUND", "A futás mappája nem olvasható (%s)." % exc.message) from None


def run_summary(run):
    """A futás rövid leírása a válaszokhoz (a motor kész szövegeivel; számot itt nem formázunk)."""
    if not run:
        return None
    prim = run.get("primary") if isinstance(run.get("primary"), dict) else {}
    sp = run.get("spec") if isinstance(run.get("spec"), dict) else {}
    return {"run_id": run.get("run_id"), "dir": run.get("dir"), "stale": run.get("stale"), "k": run.get("k"),
            "measure": run.get("measure"), "display_text": prim.get("display_text"), "i2_text": prim.get("i2_text"),
            "pi_text": prim.get("pi_text"), "participants_text": run.get("participants_text"),
            "spec": sp.get("name"), "finished": run.get("finished") or run.get("started")}


def results_of(app, run):
    """A futás results.json-ja (dict) vagy None — a projektnapló k / résztvevő mezőihez (motor-számok)."""
    rel = _runs._file_rel(run, run.get("dir") or "", "results", "results.json")
    try:
        path = app.store.path(rel)
    except store.StoreError:
        return None
    return _runs._read_json(str(path)) if path.is_file() else None


def canonical_bytes(doc):
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def content_etag(doc):
    """Tartalom-alapú ETag (None, ha nincs dokumentum)."""
    if doc is None:
        return None
    return hashlib.sha256(canonical_bytes(doc)).hexdigest()


def norm_etag(tag):
    """ETag-összevetéshez: W/ előtag és idézőjelek nélkül, kisbetűvel (mint a tároló)."""
    if tag is None:
        return None
    t = str(tag).strip()
    if t.startswith("W/"):
        t = t[2:]
    return t.strip().strip('"').strip().lower() or None


def check_if_match(req, current_etag):
    """If-Match a tartalom-ETag-gel: ha a kérés adott meg ETag-et és az eltér → 409 CONFLICT; ha a dokumentum
    már létezik, az If-Match kötelező (különben egy régi lap csendben felülírná)."""
    want = norm_etag(req.header("If-Match"))
    if current_etag is None:
        if want not in (None, "", "*") and want != "null":
            raise ApiError("CONFLICT", "A dokumentumot közben törölték vagy áthelyezték; töltsd be újra.",
                           {"etag": None})
        return
    if not want:
        raise ApiError("CONFLICT", "A dokumentum már létezik: a mentéshez a betöltött változat ETag-je kell "
                                   "(If-Match). Töltsd be újra az oldalt.", {"etag": current_etag})
    if want != current_etag and want != "*":
        raise ApiError("CONFLICT", "A dokumentumot közben más is módosította (például egy ágens vagy egy másik "
                                   "lap). Töltsd be újra, és ismételd meg a módosítást.", {"etag": current_etag})


def _listing(app, dir_rel):
    out = {}
    try:
        d = app.store.path(dir_rel)
    except store.StoreError:
        return out
    if not d.is_dir():
        return out
    for i, entry in enumerate(os.scandir(str(d))):
        if i >= 5000:
            break
        if entry.is_file() and entry.name.endswith(".json"):
            st = entry.stat()
            out["%s/%s" % (dir_rel, entry.name)] = (st.st_mtime_ns, st.st_size)
    return out


@contextlib.contextmanager
def own_dir_writes(app, dirs):
    """A motor (a tárolót megkerülő) írásai alatt a változásfigyelő nem szkennel, és a megadott mappák
    megváltozott .json fájljai a saját írásunknak számítanak (nem „külső” szerkesztés)."""
    before = {}
    for d in dirs:
        before.update(_listing(app, d))
    with app.own_writes() as note:
        yield
        after = {}
        for d in dirs:
            after.update(_listing(app, d))
        for rel, sig in after.items():
            if before.get(rel) != sig:
                note(rel)


def text_or_none(v, max_len, field):
    if v is None:
        return None
    if not isinstance(v, str):
        raise ApiError("BAD_REQUEST", "A(z) %s mező szöveg legyen." % field)
    if len(v) > max_len:
        raise ApiError("BAD_REQUEST", "A(z) %s mező túl hosszú (legfeljebb %d karakter)." % (field, max_len))
    if "\x00" in v:
        raise ApiError("BAD_REQUEST", "A(z) %s mező NUL karaktert tartalmaz." % field)
    return v.strip() or None
