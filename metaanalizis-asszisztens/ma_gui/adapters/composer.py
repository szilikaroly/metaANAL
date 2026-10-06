# -*- coding: utf-8 -*-
"""composer-adapter — PRISMA-számok a composer pluginból, csak olvasva (terv 3.5.14, 4.13, 5.0 H7, 5.5, 7.3).

- **Soha nem írjuk a composer állapotát.** Csak a ``prisma export --format flow-json`` (egy ideiglenes mappába) és
  a ``prisma status`` fut; mindkettő csak olvassa a ``<outdir>/prisma/<projekt>.json`` állapotot.
- **H7 (abszolút shebang: ``#!/Users/szili/anaconda3/bin/python3``):** a szkriptet MINDIG a saját
  interpreterünkkel hívjuk (``[python, scripts/prisma, …]``, a caps-rekord ``python``-ja), a shebangra soha nem
  hagyatkozunk; ``shell=False``, argv-lista.
- **H7 (nem atomikus ``save_state``):** ha a composer éppen írja az állapotot, az export ``JSONDecodeError``-ral
  bukhat, vagy csonka JSON-t írhat — 3 próbálkozás 200 ms-onként, utána érthető magyar hiba.
- **Leképezés** ``szk.prisma-flow/v1``-re (4.13): a C1 előtti export ``schema`` nélküli; az ``included`` a bevont
  JELENTÉSEK száma (J) → ``included_reports``; az I (``included_studies``) csak a C1-es exportból jön, különben
  ``null`` (a motor a ``studies.json``-ból veszi). A keresési napló-listák (``databases`` …, bennük a
  keresőkifejezésekkel) NEM kerülnek át; a dobozszámok egész számok, a kizárási okok ``{ok: darab}``.
- **Figyelmeztetések** (5.5, kötelező megismétlés): a ``status`` szövegének ``!``-lel kezdődő sorai (retmax-hiány,
  5D „függőben” rekordok) — a magyar szöveg szó szerint, mellette angol összefoglaló.
- **Helyfeloldás** (C2 előtt): ``ma-projekt.json`` ``composer: {outdir, project}``; ennek hiányában a
  ``COMPOSER_OUTDIR`` környezeti változó (a C2 neve). Kimenő hálózat nincs (a composer online módjait nem hívjuk).

Számot nem számol; a dobozok ellenőrzése a motoré (``api.prisma_check``)."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from .base import USABLE_STATES, Adapter, option

PLUGIN = "composer"
FLOW_SCHEMA = "szk.prisma-flow/v1"
TIMEOUT = 60.0
RETRIES = 3
RETRY_SLEEP = 0.2
MAX_EXPORT_BYTES = 8 * 1024 * 1024
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COUNT_KEYS = ("identified_databases", "identified_registers", "identified_other", "dedup_removed",
              "automation_removed", "removed_before_screening_n", "screened", "excluded_screening",
              "sought_for_retrieval", "not_retrieved", "assessed_eligibility", "excluded_eligibility",
              "included_reports", "included_studies", "undecided", "retrieval_gap")
REASONS_KEY = "excluded_eligibility_reasons"
MAX_REASONS = 200
_TRUNCATED_MARKERS = ("JSONDecodeError", "Expecting value", "Unterminated string", "Expecting ',' delimiter",
                      "Extra data")
_RETMAX_RE = re.compile(r"!\s*FIGYELEM:\s*(\d+)\s+azonosított rekord NEM került be")
_PENDING_RE = re.compile(r"Függőben \(nem ellenőrizhető\):\s*(\d+)")
_NO_STATE_MARK = "Nincs PRISMA állapot"


def _i18n(hu, en):
    return {"hu": hu, "en": en}


def _err(code, http, message, details=None):
    out = {"code": code, "http": http, "message": message}
    if details is not None:
        out["details"] = details
    return {"ok": False, "error": out}


def check_slug(value):
    if not isinstance(value, str) or not SLUG_RE.match(value):
        raise ValueError("A composer-projekt neve betűvel vagy számmal kezdődjön, és csak betűt, számot, pontot, "
                         "kötőjelet és aláhúzást tartalmazzon (legfeljebb 64 karakter).")
    return value


def resolve_outdir(value, project_root=None):
    """A ma-projekt.json / környezet outdir-értéke → abszolút Path (~ kifejtve; relatív út a projekthez képest)."""
    if not isinstance(value, str) or not value.strip():
        return None
    p = Path(os.path.expanduser(value.strip()))
    if not p.is_absolute() and project_root is not None:
        p = Path(project_root) / p
    return Path(os.path.abspath(str(p)))


def location(meta, project_root=None, env=None):
    """{configured, outdir, project, source, state_file, state_exists, state_mtime, problems[]} — hol a composer
    állapota. Nem olvassa a fájlt, csak ellenőrzi a létezését."""
    env = os.environ if env is None else env
    comp = meta.get("composer") if isinstance(meta, dict) and isinstance(meta.get("composer"), dict) else {}
    outdir_raw = comp.get("outdir") if isinstance(comp.get("outdir"), str) else None
    project = comp.get("project") if isinstance(comp.get("project"), str) else None
    source = "ma-projekt.json" if outdir_raw else None
    if not outdir_raw and env.get("COMPOSER_OUTDIR"):
        outdir_raw, source = env.get("COMPOSER_OUTDIR"), "env:COMPOSER_OUTDIR"
    outdir = resolve_outdir(outdir_raw, project_root)
    problems = []
    if project is not None and not SLUG_RE.match(project):
        problems.append(_i18n("A composer-projekt neve érvénytelen (ma-projekt.json composer.project).",
                              "Invalid composer project name (ma-projekt.json composer.project)."))
        project = None
    state_file = None
    exists = False
    mtime = None
    if outdir is not None and not outdir.is_dir():
        problems.append(_i18n("A composer kimeneti mappája nem létezik vagy nem mappa.",
                              "The composer output folder does not exist or is not a folder."))
    if outdir is not None and project:
        state_file = outdir / "prisma" / ("%s.json" % project)
        try:
            st = state_file.stat()
            exists = state_file.is_file()
            mtime = st.st_mtime
        except OSError:
            exists = False
        if not exists and outdir.is_dir():
            problems.append(_i18n("Nincs composer PRISMA-állapot ezen a néven (%s/prisma/%s.json). Ellenőrizd a projekt "
                                  "nevét, vagy hozd létre a composerben: prisma init --project %s."
                                  % (outdir.name, project, project),
                                  "No composer PRISMA state under this name (%s/prisma/%s.json). Check the project "
                                  "name, or create it in the composer: prisma init --project %s."
                                  % (outdir.name, project, project)))
    return {"configured": bool(outdir is not None and project), "outdir": str(outdir) if outdir else None,
            "project": project, "source": source, "state_file": str(state_file) if state_file else None,
            "state_exists": exists, "state_mtime": mtime, "problems": problems}


def _count(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError("nem nemnegatív egész")
    return v


def map_flow(raw, composer_version=None, project=None, generated=None):
    """A composer flow-json (C1 előtt séma nélküli) → ``szk.prisma-flow/v1``. Alak-hibánál ValueError (a mező
    nevével, érték nélkül)."""
    if not isinstance(raw, dict):
        raise ValueError("a composer exportja nem JSON-objektum")
    if raw.get("schema") not in (None, FLOW_SCHEMA):
        raise ValueError("ismeretlen séma a composer exportjában")
    if "screened" not in raw and "identified_databases" not in raw:
        raise ValueError("a composer exportja nem PRISMA-folyamat (hiányzik a screened / identified_databases)")
    out = {"schema": FLOW_SCHEMA}
    src = dict(raw)
    if src.get("included_reports") is None and src.get("included") is not None:
        src["included_reports"] = src.get("included")           # C1 előtt: included = J (jelentések), H7
    for key in COUNT_KEYS:
        try:
            out[key] = _count(src.get(key))
        except ValueError:
            raise ValueError("a(z) %s mező nem nemnegatív egész szám" % key) from None
    out["included"] = out["included_reports"]
    reasons = src.get(REASONS_KEY)
    if reasons is None or reasons == {}:
        out[REASONS_KEY] = None
    elif isinstance(reasons, dict):
        if len(reasons) > MAX_REASONS:
            raise ValueError("túl sok kizárási ok")
        clean = {}
        for k, v in reasons.items():
            if not isinstance(k, str) or not k.strip() or len(k) > 500:
                raise ValueError("érvénytelen kizárási ok")
            try:
                clean[k.strip()] = _count(v)
            except ValueError:
                raise ValueError("a kizárási okok darabszáma nem nemnegatív egész") from None
        out[REASONS_KEY] = clean
    else:
        raise ValueError("a kizárási okok nem {ok: darab} alakúak")
    out["other_methods"] = None
    out["project"] = project if project is not None else (raw.get("project") if isinstance(raw.get("project"), str)
                                                         else None)
    out["composer_version"] = composer_version
    out["generated"] = generated
    return out


def parse_status(text):
    """A ``prisma status`` szövegének figyelmeztetései → [{code, level, n?, hu, en}] (a magyar szó szerint)."""
    out = []
    lines = (text or "").splitlines()
    pending = None
    for ln in lines:
        m = _PENDING_RE.search(ln)
        if m:
            pending = int(m.group(1))
    for ln in lines:
        s = ln.strip()
        if not s.startswith("!"):
            continue
        body = s.lstrip("!").strip()
        m = _RETMAX_RE.search(s)
        if m:
            n = int(m.group(1))
            out.append({"code": "retmax", "level": "warning", "n": n, "hu": body,
                        "en": "%d identified record(s) were not imported into the corpus (retmax limit or "
                              "duplicates removed before screening); if it is the retmax limit, the search was not "
                              "exhaustive." % n})
        elif "függőben" in body.lower():
            out.append({"code": "pending_5d", "level": "warning", "n": pending, "hu": body,
                        "en": "%s record(s) are pending at the 5D bibliographic gate and cannot be cited as verified."
                              % ("?" if pending is None else pending)})
        else:
            out.append({"code": "other", "level": "warning", "hu": body,
                        "en": "Composer message (in Hungarian): " + body})
    return out


def _truncated(res):
    det = (res.get("error") or {}).get("details") or {}
    tail = det.get("stderr_tail") or ""
    return any(m in tail for m in _TRUNCATED_MARKERS)


def _no_state(res):
    det = (res.get("error") or {}).get("details") or {}
    return _NO_STATE_MARK in (det.get("stderr_tail") or "")


class ComposerAdapter(Adapter):
    plugin = PLUGIN
    default_timeout = TIMEOUT
    accepted_returncodes = (0,)
    sleep = staticmethod(time.sleep)

    @staticmethod
    def _json_cmd(cap, name):
        if cap.get("state") != "ok":
            return False
        return any(c.get("name") == name and c.get("available") and c.get("mode") == "json"
                   for c in cap.get("commands") or [])

    def mode(self, cap=None):
        """'json' (C1: deklarált prisma.export), 'bridge' (1.4.x) vagy None."""
        cap = self.detect() if cap is None else cap
        if cap.get("state") not in USABLE_STATES or not (cap.get("scripts") or {}).get("prisma"):
            return None
        return "json" if self._json_cmd(cap, "prisma.export") else "bridge"

    def status(self, loc=None):
        cap = self.detect()
        state = cap.get("state") or "absent"
        mode = self.mode(cap)
        remedy = None
        if state not in USABLE_STATES:
            remedy = cap.get("todo") or _i18n("A composer plugin nem érhető el.", "The composer plugin is unavailable.")
        elif loc is not None and not loc.get("configured"):
            remedy = _i18n("Add meg a composer kimeneti mappáját (ahol a collect/prisma dolgozik) és a projekt nevét — "
                           "a munkapad ezután onnan olvassa a PRISMA-számokat.",
                           "Set the composer output folder (where collect/prisma work) and the project name — the "
                           "workbench then reads the PRISMA numbers from there.")
        return {"plugin": PLUGIN, "state": state, "version": cap.get("version"), "mode": mode,
                "python": list(cap.get("python") or []) or None, "guards": list(cap.get("guards") or []),
                "explicit_interpreter": True, "remedy": remedy,
                "can_refresh": bool(mode and loc is not None and loc.get("configured") and loc.get("state_exists"))}

    def _base_args(self, outdir, project):
        return option("--outdir", str(outdir)) + option("--project", check_slug(project), regex=SLUG_RE)

    def export_flow(self, outdir, project, timeout=None):
        """``prisma export --format flow-json --out <tmp>`` → ``{ok, data: {flow (szk.prisma-flow/v1), raw_sha256,
        composer_version, mode, attempts}}`` vagy hibaboríték. Csonka állapot/export esetén újrapróbál (H7)."""
        cap = self.detect()
        mode = self.mode(cap)
        if mode is None:
            return self._missing(cap)
        outdir = Path(outdir)
        if not outdir.is_dir():
            return _err("NOT_FOUND", 404, "A composer kimeneti mappája nem létezik vagy nem mappa.")
        base_args = self._base_args(outdir, project)
        last = None
        for attempt in range(1, RETRIES + 1):
            work = tempfile.mkdtemp(prefix="composer-export-", dir=str(self.caps.tmp_dir()))
            try:
                out_dir = os.path.join(work, "out")
                args = base_args + ["export", "--format", "flow-json", "--out", out_dir]
                res = self.run(args, timeout=timeout or TIMEOUT, script="prisma", parse="text", cwd=work)
                if not res.get("ok"):
                    if _no_state(res):
                        return _err("NOT_FOUND", 404, "A composer nem talál PRISMA-állapotot ezen a projekt-néven "
                                                      "(%s) a megadott mappában. Ellenőrizd a nevet, vagy hozd létre a "
                                                      "composerben (prisma init --project %s)." % (project, project),
                                    {"plugin": PLUGIN, "project": project})
                    last = res
                    if _truncated(res) and attempt < RETRIES:
                        self.sleep(RETRY_SLEEP)
                        continue
                    if _truncated(res):
                        return self._truncated_error(attempt)
                    return res
                path = os.path.join(out_dir, "prisma-flow.json")
                try:
                    if os.path.getsize(path) > MAX_EXPORT_BYTES:
                        return _err("PLUGIN_FAILED", 502, "A composer exportja túl nagy.", {"plugin": PLUGIN})
                    with open(path, "rb") as fh:
                        raw_bytes = fh.read()
                    raw = json.loads(raw_bytes.decode("utf-8-sig"))
                except FileNotFoundError:
                    return _err("PLUGIN_FAILED", 502, "A composer nem írta ki a prisma-flow.json-t.", {"plugin": PLUGIN})
                except (OSError, ValueError):
                    if attempt < RETRIES:
                        self.sleep(RETRY_SLEEP)
                        continue
                    return self._truncated_error(attempt)
                try:
                    flow = map_flow(raw, cap.get("version"), project)
                except ValueError as exc:
                    return _err("PLUGIN_FAILED", 502, "A composer exportja nem a várt alakú (%s)." % exc,
                                {"plugin": PLUGIN})
                return {"ok": True, "data": {"flow": flow, "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                                             "composer_version": cap.get("version"), "mode": mode,
                                             "attempts": attempt}}
            finally:
                shutil.rmtree(work, ignore_errors=True)
        return last if last is not None else self._truncated_error(RETRIES)

    @staticmethod
    def _truncated_error(attempts):
        return _err("PLUGIN_FAILED", 502, "A composer állapotfájlja éppen íródik vagy csonka (a composer nem atomikusan "
                                          "ment, H7). %d próbálkozás után sem sikerült beolvasni; várj néhány "
                                          "másodpercet (amíg a composer befejezi a mentést), majd próbáld újra."
                    % attempts, {"plugin": PLUGIN, "guard": "H7", "attempts": attempts})

    def status_warnings(self, outdir, project, timeout=None):
        """``prisma status`` (json módban ``--json``) → ``{ok, data: {warnings[]}}``; a hibája nem végzetes."""
        cap = self.detect()
        mode = self.mode(cap)
        if mode is None:
            return self._missing(cap)
        base_args = self._base_args(Path(outdir), project)
        for attempt in range(1, RETRIES + 1):
            if mode == "json":
                res = self.run(base_args + ["status", "--json"], timeout=timeout or TIMEOUT, script="prisma",
                               parse="json")
            else:
                res = self.run(base_args + ["status"], timeout=timeout or TIMEOUT, script="prisma", parse="text")
            if res.get("ok"):
                break
            if _truncated(res) and attempt < RETRIES:
                self.sleep(RETRY_SLEEP)
                continue
            return res
        if mode == "json":
            data = res["data"]
            items = data.get("warnings") if isinstance(data, dict) else data
            warns = []
            for w in items or []:
                if isinstance(w, dict):
                    msg = w.get("message")
                    hu = msg.get("hu") if isinstance(msg, dict) else (msg if isinstance(msg, str) else None)
                    en = msg.get("en") if isinstance(msg, dict) else None
                    warns.append({"code": str(w.get("code") or "other"), "level": "warning",
                                  "n": w.get("n") if isinstance(w.get("n"), int) else None,
                                  "hu": hu or "", "en": en or hu or ""})
                elif isinstance(w, str):
                    warns.append({"code": "other", "level": "warning", "hu": w, "en": w})
            return {"ok": True, "data": {"warnings": warns, "mode": mode}}
        return {"ok": True, "data": {"warnings": parse_status(res["data"]), "mode": mode}}

