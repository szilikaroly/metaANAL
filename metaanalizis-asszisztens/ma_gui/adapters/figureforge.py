# -*- coding: utf-8 -*-
"""figure-forge adapter (terv 3.5.9, 4.12, 5.0 H5/H6, 5.1–5.3, 6.7, 7.3).

Mit tud, módonként (a caps-rekord alapján):

- **audit** — ``ff.py audit <fájl>``: szerkeszthetőség (szöveg-e a szöveg), betűkészlet-lánc, rétegnevek,
  tipográfiai javaslatok, glifák. ``json`` mód (F1+F5): ``ff.py audit <fájl> --json`` a stdout-ra; ``bridge``
  mód (0.2.x): a szöveges kimenet helyett a plugin által a fájl MELLÉ írt ``<név>.editability.json``-t olvassuk
  — ezért az SVG-t mindig a futásidejű tmp-be másoljuk, a projektmappába a plugin soha nem ír.
  **H5:** a 0.2.1 ``ff.py`` modulszinten importálja a matplotlibet, így az audit matplotlib nélkül sem fut. Ha az
  audit ``ModuleNotFoundError``-ral bukik (vagy a caps már így látta), az eredmény ``CAPABILITY_MISSING``
  ``state: unusable``, ``guard: H5`` és a PONTOS teendő (melyik interpreter, mit telepíts, FIGURE_FORGE_PYTHON).
  A hívó ilyenkor a stdlib-auditra (``svgaudit``) esik vissza.
- **export** — ``ff.py meta --request <szk.figure-request/v1> --json`` (F2/F5) → ``szk.figure-result/v1`` +
  a kimeneti fájlok (a tmp-ből beolvasva, kiterjesztés-, méret- és „mágikus bájt”-ellenőrzéssel). F2 előtt
  (H6: a ``forest`` alparancs nem meta-forest) nincs figure-forge-export: a hívó a motor-SVG-t exportálja.
- **helyi SVG-átalakító** a motor-SVG PNG/PDF-változatához (rsvg-convert, Inkscape, CairoSVG, ImageMagick) —
  egyszeri próbakonverzióval szondázva; ha nincs működő, csak SVG készül, magyar teendővel.

Ítéletet, rajzot, számítást nem végez; a számhűséget a ``svgaudit.numbers_check`` ellenőrzi (szövegösszevetés).
Argv-t, kimenetet, címkét nem naplóz (T10)."""
import json
import os
import re
import shutil
import tempfile
import threading

from . import base, svgaudit
from .base import USABLE_STATES, Adapter, error

PLUGIN = "figure-forge"
AUDIT_TIMEOUT = 120.0
EXPORT_TIMEOUT = 300.0
CONVERT_TIMEOUT = 120.0
PROBE_TIMEOUT = 20.0
FIGURE_REQUEST = "szk.figure-request/v1"
FIGURE_RESULT = "szk.figure-result/v1"
AUDIT_SCHEMA = "szk.ma.figure-audit/v1"
FORMATS = ("svg", "pdf", "tiff", "png", "pptx")
FF_ONLY_FORMATS = ("tiff", "pptx")
CONVERTIBLE = ("png", "pdf")
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAGIC = {"png": (b"\x89PNG\r\n\x1a\n",), "pdf": (b"%PDF",), "tiff": (b"II*\x00", b"MM\x00*"),
         "pptx": (b"PK\x03\x04",), "svg": None}
EXT = {"svg": ".svg", "pdf": ".pdf", "tiff": ".tiff", "png": ".png", "pptx": ".pptx"}
H5_MODULES = ("matplotlib", "numpy", "pandas")
_MISSING_RE = re.compile(r"(?:ModuleNotFoundError|ImportError): No module named '([A-Za-z0-9_.]+)'")
PIP_NAMES = {"PIL": "Pillow", "pptx": "python-pptx"}
_PROBE_SVG = (b"<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'>"
              b"<rect width='12' height='12' fill='#000000'/></svg>")


def _i18n(hu, en):
    return {"hu": hu, "en": en}


def missing_modules(text):
    """A stderr-ből a hiányzó modulok (legfelső szintű név), sorrendtartóan."""
    out = []
    for m in _MISSING_RE.finditer(text or ""):
        name = m.group(1).split(".")[0]
        if name not in out:
            out.append(name)
    return out


def _python_text(cap):
    py = cap.get("python") or []
    if not py:
        return "python"
    return " ".join('"%s"' % a if " " in a else a for a in py)


def _h5_active(cap):
    for issue in cap.get("known_issues") or []:
        if issue.get("id") == "H5":
            return True
    return "H5" in (cap.get("guards") or [])


def h5_remedy(cap, missing):
    """A H5 pontos teendője: melyik interpreterből mi hiányzik, mit telepíts, vagy hova mutasson a
    FIGURE_FORGE_PYTHON."""
    mods = [m for m in (missing or []) if m] or ["matplotlib"]
    py = _python_text(cap)
    pips = " ".join(PIP_NAMES.get(m, m) for m in mods)
    ver = cap.get("version") or "?"
    hu = ("%s nem importálható a(z) %s értelmezőben. A figure-forge %s az ff.py-ban modulszinten importálja a "
          "matplotlibet (H5), ezért az ábra-audit sem fut nélküle. Teendő: %s -m pip install %s — vagy állítsd a "
          "FIGURE_FORGE_PYTHON környezeti változót egy olyan Pythonra, amelyben ezek megvannak, majd kattints az "
          "Újraszondázásra. Addig a munkapad a saját (stdlib) ellenőrzésével auditálja a motor-SVG-t."
          % (", ".join(mods), py, ver, py, pips))
    en = ("%s cannot be imported in %s. figure-forge %s imports matplotlib at module level in ff.py (H5), so even "
          "the figure audit needs it. To fix: %s -m pip install %s — or set FIGURE_FORGE_PYTHON to a Python that has "
          "them, then click Re-probe. Until then the workbench audits the engine SVG with its own (stdlib) check."
          % (", ".join(mods), py, ver, py, pips))
    return _i18n(hu, en)


def _safe_name(name, default="figure.svg"):
    base_name = os.path.basename(str(name or "")) or default
    stem, ext = os.path.splitext(base_name)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-")[:60] or "figure"
    ext = ext.lower() if ext.lower() in (".svg", ".pdf") else ".svg"
    return stem + ext


def _magic_ok(fmt, data):
    sigs = MAGIC.get(fmt)
    if sigs is None:
        head = data[:4096].lstrip().lower()
        return head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head
    return any(data.startswith(s) for s in sigs)


def _within(child, parent):
    try:
        child = os.path.realpath(str(child))
        parent = os.path.realpath(str(parent))
        return os.path.commonpath([child, parent]) == parent and child != parent
    except ValueError:
        return False


# ---------------------------------------------------------------------------- helyi SVG-átalakítók
# név → (csak POSIX-on?, {formátum: argv-sablon}); a sablon helyei: {exe} {src} {out} {dpi}
CONVERTERS = (
    ("rsvg-convert", False, {"png": ["{exe}", "-f", "png", "-d", "{dpi}", "-p", "{dpi}", "-o", "{out}", "{src}"],
                             "pdf": ["{exe}", "-f", "pdf", "-o", "{out}", "{src}"]}),
    ("inkscape", False, {"png": ["{exe}", "{src}", "--export-type=png", "--export-dpi={dpi}", "--export-filename={out}"],
                         "pdf": ["{exe}", "{src}", "--export-type=pdf", "--export-filename={out}"]}),
    ("cairosvg", False, {"png": ["{exe}", "{src}", "-f", "png", "-d", "{dpi}", "-o", "{out}"],
                         "pdf": ["{exe}", "{src}", "-f", "pdf", "-o", "{out}"]}),
    ("magick", False, {"png": ["{exe}", "-density", "{dpi}", "{src}", "{out}"], "pdf": ["{exe}", "{src}", "{out}"]}),
    # Windows-on a convert.exe a lemez-átalakító rendszerprogram — ott csak a magick jöhet szóba
    ("convert", True, {"png": ["{exe}", "-density", "{dpi}", "{src}", "{out}"], "pdf": ["{exe}", "{src}", "{out}"]}),
)
_PROBE_CACHE = {}
_PROBE_LOCK = threading.Lock()


def _fill(template, exe, src, out, dpi):
    return [part.replace("{exe}", exe).replace("{src}", src).replace("{out}", out).replace("{dpi}", str(int(dpi)))
            for part in template]


def _run_converter(argv, out_path, fmt, tmp, timeout=CONVERT_TIMEOUT):
    try:
        res = base._run(argv, timeout, 1024 * 1024, cwd=tmp)
    except (OSError, ValueError):
        return None
    if res["timed_out"] or res["returncode"] != 0:
        return None
    try:
        if not os.path.isfile(out_path) or os.path.getsize(out_path) > MAX_OUTPUT_BYTES:
            return None
        with open(out_path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    return data if data and _magic_ok(fmt, data) else None


def find_converters(env=None, tmp=None, probe=True):
    """[{name, path, formats, ok}] — a PATH-on talált SVG-átalakítók; ``probe``: egyszeri próbakonverzió (10×10-es
    SVG → PNG), útvonal+mtime szerint gyorsítótárazva. Csak a ``ok: True`` jelölt használható."""
    env = os.environ if env is None else env
    path_var = env.get("PATH", "")
    found = []
    for name, posix_only, templates in CONVERTERS:
        if posix_only and os.name == "nt":
            continue
        exe = shutil.which(name, path=path_var) if path_var else None
        if not exe:
            continue
        ok = True
        if probe:
            ok = _probe(exe, templates, tmp)
        found.append({"name": name, "path": exe, "formats": sorted(templates), "ok": ok})
    return found


def _probe(exe, templates, tmp):
    try:
        st = os.stat(exe)
        key = (os.path.realpath(exe), st.st_mtime_ns, st.st_size)
    except OSError:
        return False
    with _PROBE_LOCK:
        if key in _PROBE_CACHE:
            return _PROBE_CACHE[key]
    work = tempfile.mkdtemp(prefix="svgconv-probe-", dir=str(tmp) if tmp else None)
    try:
        src = os.path.join(work, "probe.svg")
        out = os.path.join(work, "probe.png")
        with open(src, "wb") as fh:
            fh.write(_PROBE_SVG)
        ok = _run_converter(_fill(templates["png"], exe, src, out, 72), out, "png", work, PROBE_TIMEOUT) is not None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    with _PROBE_LOCK:
        _PROBE_CACHE[key] = ok
    return ok


def convert_svg(svg_bytes, fmt, dpi=600, env=None, tmp=None):
    """A motor-SVG átalakítása PNG/PDF-re az első működő helyi átalakítóval → (bájtok, átalakító-név) vagy
    (None, None). A bemenet és a kimenet a futásidejű tmp-ben, a projektmappán kívül (7.3)."""
    if fmt not in CONVERTIBLE:
        return None, None
    for conv in find_converters(env, tmp):
        if not conv["ok"]:
            continue
        templates = dict((n, t) for n, _p, t in CONVERTERS).get(conv["name"]) or {}
        if fmt not in templates:
            continue
        work = tempfile.mkdtemp(prefix="svgconv-", dir=str(tmp) if tmp else None)
        try:
            src = os.path.join(work, "figure.svg")
            out = os.path.join(work, "figure" + EXT[fmt])
            with open(src, "wb") as fh:
                fh.write(svg_bytes)
            data = _run_converter(_fill(templates[fmt], conv["path"], src, out, dpi), out, fmt, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if data is not None:
            return data, conv["name"]
    return None, None


NO_CONVERTER = _i18n(
    "PNG/PDF-hez nincs működő helyi SVG-átalakító (rsvg-convert, Inkscape, CairoSVG vagy ImageMagick). Telepítsd "
    "például a librsvg-t (rsvg-convert) vagy az Inkscape-et, vagy használd a figure-forge-ot az F2 után. Addig az "
    "SVG készül: ezt a folyóiratok és az ábraszerkesztők (Inkscape, Illustrator) is elfogadják.",
    "No working local SVG converter for PNG/PDF (rsvg-convert, Inkscape, CairoSVG or ImageMagick). Install e.g. "
    "librsvg (rsvg-convert) or Inkscape, or use figure-forge after F2. Until then the SVG is produced: journals and "
    "figure editors (Inkscape, Illustrator) accept it.")
FF_ONLY = _i18n(
    "TIFF és PPTX csak a figure-forge meta-exportjával (F2) készül; a mostani figure-forge ezt még nem tudja.",
    "TIFF and PPTX are only produced by the figure-forge meta export (F2), which the installed figure-forge lacks.")


# ---------------------------------------------------------------------------- adapter
class FigureForgeAdapter(Adapter):
    plugin = PLUGIN
    default_timeout = EXPORT_TIMEOUT
    accepted_returncodes = (0, 3)        # F5: 0 tiszta, 3 maradék QC-sértés

    # -- módok
    @staticmethod
    def _json_cmd(cap, name):
        if cap.get("state") != "ok":
            return False
        return any(c.get("name") == name and c.get("available") and c.get("mode") == "json"
                   for c in cap.get("commands") or [])

    def audit_mode(self, cap=None):
        """'json' (F1+F5: deklarált audit), 'bridge' (0.2.x: editability.json olvasása) vagy None."""
        cap = self.detect() if cap is None else cap
        if cap.get("state") not in USABLE_STATES:
            return None
        return "json" if self._json_cmd(cap, "audit") else "bridge"

    def export_mode(self, cap=None):
        """'json', ha a kézfogás deklarálja a ``meta`` parancsot (F2/F5); különben None (H6: motor-SVG)."""
        cap = self.detect() if cap is None else cap
        return "json" if self._json_cmd(cap, "meta") else None

    def flowchart_mode(self, cap=None):
        cap = self.detect() if cap is None else cap
        if cap.get("state") not in USABLE_STATES:
            return None
        return "json" if self._json_cmd(cap, "flowchart") else "bridge"

    # -- teendők
    def _unusable_remedy(self, cap):
        state = cap.get("state") or "absent"
        if state == "absent":
            return cap.get("todo") or _i18n("A figure-forge nincs telepítve: claude plugin install figure-forge@szk-plugins.",
                                            "figure-forge is not installed: claude plugin install figure-forge@szk-plugins.")
        missing = list(cap.get("missing_modules") or [])
        if missing and (set(missing) & set(H5_MODULES)):
            return h5_remedy(cap, missing)
        return cap.get("todo")

    def status(self):
        """A felületnek: állapot, verzió, interpreter, és funkciónként a mód, a tartalék és a teendő."""
        cap = self.detect()
        state = cap.get("state") or "absent"
        audit = self.audit_mode(cap)
        export = self.export_mode(cap)
        flow = self.flowchart_mode(cap)
        unusable = self._unusable_remedy(cap) if state not in USABLE_STATES else None
        ver = cap.get("version") or "?"
        export_remedy = None
        if export is None:
            export_remedy = unusable or _i18n(
                "A figure-forge %s nem rajzol meta-analitikus ábrát (H6: nincs gyémánt, predikciós intervallum, "
                "alcsoport); a publikációs ábra addig a motor SVG-je. A figure-forge F2-es (meta) változatával itt "
                "automatikusan elérhető lesz." % ver,
                "figure-forge %s does not draw meta-analytic figures (H6: no diamond, prediction interval, subgroups); "
                "the publication figure is the engine SVG until then. It becomes available here with the figure-forge "
                "F2 (meta) release." % ver)
        return {
            "plugin": PLUGIN, "state": state, "version": cap.get("version"), "mode": cap.get("mode"),
            "handshake": cap.get("handshake"), "python": list(cap.get("python") or []) or None,
            "guards": list(cap.get("guards") or []), "missing_modules": list(cap.get("missing_modules") or []),
            "audit": {"mode": audit, "available": audit is not None, "fallback": "stdlib",
                      "guard": "H5" if (audit is None and state == "unusable" and _h5_active(cap)) else None,
                      "remedy": unusable if audit is None else None},
            "export": {"mode": export, "available": export is not None, "fallback": "engine_svg",
                       "remedy": export_remedy},
            "flowchart": {"mode": flow, "available": flow is not None, "remedy": unusable if flow is None else None},
        }

    def _missing_error(self, cap, missing=None):
        missing = list(missing or cap.get("missing_modules") or [])
        state = cap.get("state") or "absent"
        if missing and (set(missing) & set(H5_MODULES)):
            remedy = h5_remedy(cap, missing)
            return error("CAPABILITY_MISSING", remedy["hu"],
                         {"plugin": PLUGIN, "state": "unusable", "guard": "H5", "missing": missing, "todo": remedy,
                          "python": list(cap.get("python") or []) or None})
        remedy = self._unusable_remedy(cap)
        msg = (remedy or {}).get("hu") or ("A figure-forge nem érhető el (állapot: %s)." % state)
        return error("CAPABILITY_MISSING", msg, {"plugin": PLUGIN, "state": state, "todo": remedy,
                                                 "problems": cap.get("problems") or []})

    def _h5_from_failure(self, cap, res):
        """Futás közbeni ModuleNotFoundError (pl. a caps legacy-nak látta, de az audit importja elhasal) → H5."""
        if res.get("ok") or (res.get("error") or {}).get("code") != "PLUGIN_FAILED":
            return None
        missing = missing_modules(((res.get("error") or {}).get("details") or {}).get("stderr_tail"))
        if not missing:
            return None
        return self._missing_error(cap, missing)

    # -- audit
    def audit(self, data, name="figure.svg", timeout=None):
        """``ff.py audit`` egy SVG/PDF másolatán a tmp-ben → ``{ok: True, data: szk.ma.figure-audit/v1}`` vagy
        hibaboríték (CAPABILITY_MISSING H5-teendővel / PLUGIN_FAILED / TIMEOUT)."""
        cap = self.detect()
        mode = self.audit_mode(cap)
        if mode is None:
            return self._missing_error(cap)
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("Az auditálandó fájl üres.")
        tmp_root = self.caps.tmp_dir()
        work = tempfile.mkdtemp(prefix="ff-audit-", dir=str(tmp_root))
        try:
            fname = _safe_name(name)
            path = os.path.join(work, fname)
            with open(path, "wb") as fh:
                fh.write(bytes(data))
            args = ["audit", path] + (["--json"] if mode == "json" else [])
            res = self.run(args, timeout=timeout or AUDIT_TIMEOUT, parse="json" if mode == "json" else "text",
                           cwd=work)
            if not res.get("ok"):
                return self._h5_from_failure(cap, res) or res
            if mode == "json":
                report = res["data"]
            else:
                rep_path = os.path.splitext(path)[0] + ".editability.json"
                try:
                    with open(rep_path, "rb") as fh:
                        report = json.loads(fh.read().decode("utf-8-sig"))
                except (OSError, ValueError):
                    return error("PLUGIN_FAILED", "A figure-forge audit nem írt értelmezhető jelentést "
                                                  "(.editability.json).", {"plugin": PLUGIN})
            if not isinstance(report, dict):
                return error("PLUGIN_FAILED", "A figure-forge audit-jelentése nem objektum.", {"plugin": PLUGIN})
            return {"ok": True, "data": normalize_audit(report, mode, cap.get("version")),
                    "elapsed_ms": res.get("elapsed_ms")}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # -- export (F2)
    def export(self, request, plot_path, project_root=None, timeout=None):
        """``ff.py meta --request <kérés> --json`` → ``{ok, data: szk.figure-result/v1, files: {fmt: bytes}}``.
        A kimeneteket a tmp-ből olvassuk be (csak az out-mappán belüli, engedett kiterjesztésű, ellenőrzött
        fejlécű fájl); a projektbe a HÍVÓ írja őket."""
        cap = self.detect()
        if self.export_mode(cap) != "json":
            if cap.get("state") not in USABLE_STATES:
                return self._missing_error(cap)
            return error("CAPABILITY_MISSING", self.status()["export"]["remedy"]["hu"],
                         {"plugin": PLUGIN, "state": cap.get("state"), "guard": "H6",
                          "todo": self.status()["export"]["remedy"]})
        if not isinstance(request, dict) or request.get("schema") != FIGURE_REQUEST:
            raise ValueError("szk.figure-request/v1 kérés kell.")
        tmp_root = self.caps.tmp_dir()
        work = tempfile.mkdtemp(prefix="ff-export-", dir=str(tmp_root))
        try:
            out_dir = os.path.join(work, "out")
            os.makedirs(out_dir)
            req = json.loads(json.dumps(request))
            req.setdefault("input", {})["plot_path"] = str(plot_path)
            if project_root is not None:
                req["root"] = str(project_root)
            req.setdefault("out", {})["dir"] = out_dir
            req_path = os.path.join(work, "request.json")
            with open(req_path, "w", encoding="utf-8") as fh:
                json.dump(req, fh, ensure_ascii=False, allow_nan=False)
            res = self.run(["meta", "--request", req_path, "--json"], timeout=timeout or EXPORT_TIMEOUT, cwd=work)
            if not res.get("ok"):
                return self._h5_from_failure(cap, res) or res
            doc = res["data"]
            if not isinstance(doc, dict) or doc.get("schema") not in (FIGURE_RESULT, None):
                return error("PLUGIN_FAILED", "A figure-forge eredménye nem szk.figure-result/v1.", {"plugin": PLUGIN})
            files, problems = {}, []
            for fmt, p in sorted((doc.get("formats") or {}).items()):
                if fmt not in FORMATS or not isinstance(p, str):
                    continue
                full = p if os.path.isabs(p) else os.path.join(out_dir, p)
                if not _within(full, out_dir) or not os.path.isfile(full):
                    problems.append({"format": fmt, "code": "outside_or_missing"})
                    continue
                if os.path.getsize(full) > MAX_OUTPUT_BYTES:
                    problems.append({"format": fmt, "code": "too_large"})
                    continue
                with open(full, "rb") as fh:
                    blob = fh.read()
                if not _magic_ok(fmt, blob):
                    problems.append({"format": fmt, "code": "bad_header"})
                    continue
                files[fmt] = blob
            return {"ok": True, "data": doc, "files": files, "problems": problems,
                    "returncode": res.get("returncode"), "elapsed_ms": res.get("elapsed_ms")}
        finally:
            shutil.rmtree(work, ignore_errors=True)


def normalize_audit(report, mode, version):
    """A figure-forge audit-jelentése (0.2.x editability.json vagy F5 JSON) → a munkapad egységes alakja; a
    címkék listája kimarad (csak a tipográfiai javaslatok darabszáma és első néhány eleme kerül át)."""
    svg = report.get("svg") if isinstance(report.get("svg"), dict) else None
    if svg is None and isinstance(report.get("editability"), dict):
        svg = report["editability"].get("svg") if isinstance(report["editability"].get("svg"), dict) else None
    pdf = report.get("pdf") if isinstance(report.get("pdf"), dict) else None
    sugg = report.get("typography_suggestions")
    if sugg is None and isinstance(report.get("typography"), dict):
        sugg = report["typography"].get("suggestions") or report["typography"].get("substitutions")
    sugg = [s for s in (sugg or []) if isinstance(s, dict)]
    missing = report.get("missing_glyphs")
    if missing is None and isinstance(report.get("glyphs"), dict):
        missing = report["glyphs"].get("missing")
    out = {"schema": AUDIT_SCHEMA, "source": "figure-forge", "mode": mode, "plugin_version": version,
           "typography": {"suggestions_n": len(sugg),
                          "suggestions": [{"from": str(s.get("from", "")), "to": str(s.get("to", ""))}
                                          for s in sugg[:svgaudit.MAX_SUGGESTIONS]]},
           "glyphs": None if missing is None else {"ok": not missing, "missing": [str(m) for m in missing][:50]}}
    if svg is not None:
        fams = svg.get("font_families") or []
        out.update({"kind": "svg", "editable": bool(svg.get("editable")),
                    "text_elements": _int(svg.get("text_elements")),
                    "outlined_text_groups": _int(svg.get("outlined_text_groups")),
                    "font_families": [str(f) for f in fams][:20],
                    "font_fallback_stack": bool(svg.get("font_fallback_stack")),
                    "named_layers": _int(svg.get("named_layers"))})
    elif pdf is not None:
        out.update({"kind": "pdf", "editable": bool(pdf.get("editable")), "type3_fonts": _int(pdf.get("type3_fonts")),
                    "truetype_embedded": _int(pdf.get("truetype_embedded"))})
    else:
        out.update({"kind": None, "editable": None})
    return out


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None
