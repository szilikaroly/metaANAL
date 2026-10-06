# -*- coding: utf-8 -*-
"""GRADE-segéd (E10; terv 3.5.12–3.5.13, 4.14, 6.4 X019, 11. döntés 4. és 6. pont): tanács doménenként, Summary of
Findings, AMSTAR 2-besorolás.

A motor itt SOHA nem dönt. Az advice() egy szk.ma.grade/v1 PISZKOZATOT ad: minden domén ítélete (rating, step) null,
mellette a számok (advisory) és egy kezdőknek is érthető javaslat (suggestion: summary, why — a „Miért?” panel
szövege —, flags, kb_refs). Az ítéletet ember hozza; a tár: projekt.save_grade_doc / record_grade_doc.

    advice(run, rob_by_row=None, mid=None, project_dir=None, …)   → szk.ma.grade/v1 piszkozat (tanáccsal; ROBINS-I /
                                                   ROBINS-E esetén GRADE 18; a felminősítés az elrendezésből)
    sof(run, assumed_risks=None, certainty=None, footnotes=None, …) → szk.ma.sof/v1 (a bizonyosság a kimenet
                                                   rögzített GRADE-ítéletéé; AI-vázlat / feloldatlan: nincs szint)
    save_sof / sof_certainty_problems / recorded_certainty           mentés csak rögzített GRADE-szinttel (4. döntés)
    absolute_effect(measure, rel, assumed_risk)    a totals.absolute_per_1000 általánosítása (RR, OR, RD)
    ois_binary(...) / ois_continuous(...)          optimális információméret (OIS) a power modullal
    parse_mid(mid, measure)                        MID: szám, szöveg ('0,75–1,25') vagy {lower, upper | value, source}
    statement(...)                                 GRADE-megfogalmazás (en: 'probably reduces'; hu megfelelője)
    amstar2_consistency(answers, convention=None, claimed=None)   AMSTAR 2 besorolás mindkét konvencióval
    unresolved_publication_bias(project_dir) / x019_findings(...)  X019-támasz (feloldatlan „suspected”)
    grade_run_mismatches(doc, run) / sof_run_mismatches(doc, run)  X007 / X008-támasz
    save_sof / load_sof / sof_markdown / sof_csv / sof_html

A run lehet: az api.analyze nézetmodellje (szk.ma.analysis-result/v1), egy results.json tartalma (dict), egy
futásmappa (results.json + plot_data.json + run.json) vagy annak egy fájlja, illetve run_id (project_dir-rel).
A számok a futásból jönnek; a szövegek {hu, en} párok, tizedesponttal (a contracts/README szabálya).
"""
import html
import json
import math
import os
import re
from decimal import Decimal, ROUND_HALF_UP

from . import __version__
from . import plots as PL
from . import projekt as P
from . import tableio
from .distributions import norm_ppf
from .effect_sizes import BINARY, PROPORTION
from .models import ModelError
from .power import power_analysis

GRADE_SCHEMA = P.GRADE_SCHEMA
SOF_SCHEMA = "szk.ma.sof/v1"
ANALYSIS_RESULT_SCHEMA = "szk.ma.analysis-result/v1"
PLOT_SCHEMA = "szk.ma.plot/v2"
SOF_DIR = "06_kezirat/sof"
DOMAINS = P.GRADE_DOMAINS
LEVELS = P.GRADE_LEVELS
SYMBOLS = {"high": "⊕⊕⊕⊕", "moderate": "⊕⊕⊕◯", "low": "⊕⊕◯◯", "very low": "⊕◯◯◯"}
LEVEL_LABELS = {"high": ("magas", "High"), "moderate": ("mérsékelt", "Moderate"), "low": ("alacsony", "Low"),
                "very low": ("nagyon alacsony", "Very low")}
RATIO = ("OR", "RR", "ROM")
MINUS = PL.MINUS
DEFAULT_RRR = 0.25           # OIS bináris kimenetnél: 25% relatív kockázatcsökkenés (GRADE 6; D-S13-007)
DEFAULT_SMD = 0.2            # OIS SMD-nél MID nélkül: 0,2 SD
RULE_OF_THUMB_EVENTS = 300   # GRADE 6 (Guyatt 2011): < 300 esemény → pontatlanság gyanúja
RULE_OF_THUMB_PARTICIPANTS = 400
EGGER_ALPHA = 0.10           # aszimmetria-tesztek szokásos szintje (Sterne 2011)
POOL_WORDS = ("control_pool", "pool", "kontroll-pool", "kontrollpool", "control pool")
_RUN_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
_OUTCOME_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")

KB = {
    "risk_of_bias": ["GRADE-03", "D-S13-003", "D-S12-002"],
    "inconsistency": ["GRADE-04", "GRADE-04a", "D-S13-004"],
    "indirectness": ["GRADE-05", "D-S13-005"],
    "imprecision": ["GRADE-06", "GRADE-06a", "GRADE-06b", "D-S13-006", "D-S13-007", "D-S13-014"],
    "publication_bias": ["GRADE-07", "GRADE-07a", "D-S13-008", "D-S11-018", "D-S11-020"],
    "upgrades": ["GRADE-08", "GRADE-08a", "GRADE-08b", "GRADE-08c", "D-S13-009"],
    "sof": ["GRADE-10", "GRADE-10a", "GRADE-10b", "GRADE-11", "D-S13-011", "D-S13-012", "D-S13-016"],
    "amstar2": ["AMSTAR2-00", "D-S13-021"],
    "general": ["GRADE-00", "GRADE-09", "D-S13-002", "D-S13-010"],
}


# ------------------------------------------------------------------ szöveg- és számsegédek
def _tr(hu, en):
    return {"hu": hu, "en": en}


def _round(x, nd=0):
    """Kerekítés a döntetlennél nullától el (a JS toFixed és a Python % eltérése helyett egyértelmű szabály)."""
    q = Decimal(1).scaleb(-nd)
    return float(Decimal(repr(float(x))).quantize(q, rounding=ROUND_HALF_UP))


def _fmt(x, nd=2, lang="hu"):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "–"
    s = ("%." + str(nd) + "f") % _round(x, nd)
    if s.startswith("-") and float(s) == 0:
        s = s[1:]
    return s.replace("-", MINUS) if lang == "en" else s


def _num(v):
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _count(v):
    """Darabszám: egész, ha egész értékű (a JSON-ban 357347, nem 357347.0)."""
    v = _num(v)
    return int(v) if v is not None and v.is_integer() and abs(v) < 2 ** 53 else v


def _int_text(v):
    v = _num(v)
    if v is None:
        return None
    return "%d" % v if float(v).is_integer() else "%g" % v


def _pct(v, nd=1):
    return "%s%%" % _fmt(v, nd)


def _both(fn):
    """{'hu': fn('hu'), 'en': fn('en')}"""
    return {"hu": fn("hu"), "en": fn("en")}


def _flag(code, hu, en, level="info"):
    return {"code": code, "level": level, "text": _tr(hu, en)}


def _names(labels, n=3):
    labels = [str(x) for x in labels]
    if len(labels) <= n:
        return ", ".join(labels)
    return "%s +%d" % (", ".join(labels[:n]), len(labels) - n)


def _parse_num(text):
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        v = float(text)
    elif isinstance(text, str):
        v = tableio.parse_number(text.replace("\u2212", "-").strip())
    else:
        raise ValueError("nem szám")
    if v is None or not math.isfinite(v):
        raise ValueError("üres vagy nem véges szám")
    return v


# ------------------------------------------------------------------ futás beolvasása
def _read_json(path):
    def bad(token):
        raise ValueError("nem véges szám: %s" % token)
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8-sig"), parse_constant=bad)


class RunData(object):
    """Egy futás adatai a tanácsadóhoz: results (a results.json tartalma), plot (szk.ma.plot/v2 vagy None), desc
    (szk.ma.run/v1 vagy None), folder (a futásmappa útja vagy None)."""

    def __init__(self, results, plot=None, desc=None, folder=None):
        from .pipeline import to_jsonable
        self.results = to_jsonable(results)
        self.plot = plot if isinstance(plot, dict) and plot.get("schema") == PLOT_SCHEMA else None
        self.desc = desc if isinstance(desc, dict) else None
        self.folder = folder

    @property
    def measure(self):
        opt = self.results.get("options") or {}
        m = opt.get("measure") or (self.results.get("effect_sizes") or {}).get("measure") or ""
        return str(m).upper()

    @property
    def primary(self):
        p = self.results.get("primary")
        return p if isinstance(p, dict) else None

    @property
    def bt(self):
        b = (self.results.get("back_transformed") or {}).get("estimate_ci")
        return list(b) if isinstance(b, (list, tuple)) and len(b) == 3 else [None, None, None]

    @property
    def pi_display(self):
        b = (self.results.get("back_transformed") or {}).get("pi")
        return list(b) if isinstance(b, (list, tuple)) and len(b) == 2 and None not in b else None

    @property
    def level(self):
        lv = (self.results.get("back_transformed") or {}).get("level") or (self.results.get("options") or {}).get(
            "level") or 0.95
        return float(lv)

    @property
    def totals(self):
        return self.results.get("totals") or {}

    @property
    def bias(self):
        return self.results.get("bias") or {}

    @property
    def run_id(self):
        rid = (self.desc or {}).get("run_id")
        return rid if isinstance(rid, str) and _RUN_ID.match(rid) else None

    @property
    def spec(self):
        return (self.desc or {}).get("spec") if isinstance((self.desc or {}).get("spec"), dict) else {}

    @property
    def data_sha256(self):
        return ((self.desc or {}).get("data") or {}).get("sha256")

    @property
    def display_text(self):
        prim = (self.desc or {}).get("primary") if isinstance((self.desc or {}).get("primary"), dict) else {}
        if isinstance(prim.get("display_text"), dict):
            return prim["display_text"]
        b = self.bt
        return PL.display_text(*b) if None not in b else None


def _find_run_dir(run_id, project_dir):
    from .spec import project_run_files
    for path, _layout, _folder, _logged in project_run_files(project_dir):
        try:
            doc = _read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("run_id") == run_id:
            return os.path.dirname(path)
    raise ValueError("nincs ilyen commit-futás a projektben: %s" % run_id)


def _load_dir(folder):
    res_path = os.path.join(folder, "results.json")
    if not os.path.isfile(res_path):
        raise ValueError("a futásmappában nincs results.json: %s" % folder)
    results = _read_json(res_path)
    plot = desc = None
    for name in ("plot_data.json", "run.json"):
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            try:
                doc = _read_json(p)
            except (OSError, ValueError):
                doc = None
            if name == "plot_data.json":
                plot = doc
            else:
                desc = doc
    return RunData(results, plot, desc, os.path.abspath(folder))


def load_run(run, project_dir=None):
    """A run bármely elfogadott alakja → RunData (lásd a modul leírását). Hibánál ValueError."""
    if isinstance(run, RunData):
        return run
    if isinstance(run, bytes):
        run = run.decode("utf-8")
    if isinstance(run, (str, os.PathLike)):
        p = os.fspath(run)
        if _RUN_ID.match(p) and not os.path.exists(p):
            if project_dir is None:
                raise ValueError("run_id-hez a projektmappa (project_dir) is kell: %s" % p)
            return _load_dir(_find_run_dir(p, project_dir))
        if not os.path.isabs(p) and project_dir is not None and not os.path.exists(p):
            p = os.path.join(project_dir, p)
        if os.path.isdir(p):
            return _load_dir(p)
        if os.path.isfile(p):
            return _load_dir(os.path.dirname(os.path.abspath(p)))
        raise ValueError("a futás nem található: %s" % p)
    if isinstance(run, dict):
        if run.get("schema") == ANALYSIS_RESULT_SCHEMA:
            if not isinstance(run.get("results"), dict):
                raise ValueError("az elemzési nézetben nincs 'results'")
            return RunData(run["results"], run.get("plot"), run.get("run"))
        if "options" in run and ("primary" in run or "effect_sizes" in run):
            return RunData(run)
    raise ValueError("ismeretlen futás-alak: az api.analyze nézetmodellje, results.json-tartalom, futásmappa, "
                     "fájlút vagy run_id adható meg")


# ------------------------------------------------------------------ skálák, küszöbök
def null_value(measure):
    """A nullhatás a MEGJELENÍTÉSI skálán: arány-mértékeknél 1, különbségeknél és korrelációnál 0; egycsoportos
    aránynál nincs (None)."""
    if measure in PROPORTION:
        return None
    return 1.0 if measure in RATIO else 0.0


def _to_analysis(measure, x):
    """Megjelenítési skála → elemzési skála (log arány-mértéknél, atanh a Fisher z-nél)."""
    x = _num(x)
    if x is None:
        return None
    if measure in RATIO:
        return math.log(x) if x > 0 else None
    if measure == "ZCOR":
        return math.atanh(x) if -1 < x < 1 else None
    return x


def _parse_mid_text(text):
    t = text.strip().replace("\u2212", "-").replace("±", "")
    t = re.sub(r"^[^\d\-+.,]*?[:=]\s*", "", t)            # 'MID: 0,75–1,25', 'RR = 0.8'
    t = re.sub(r"^[A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű ]+(?=[\d\-+.])", "", t)
    parts = [x for x in re.split(r"\s*(?:–|—|;|\.\.|\bto\b|\bés\b|/)\s*", t) if x.strip()]
    if len(parts) == 1:
        m = re.match(r"^\s*(-?\d+(?:[.,]\d+)?(?:e-?\d+)?)\s*-\s*(-?\d+(?:[.,]\d+)?(?:e-?\d+)?)\s*$", parts[0], re.I)
        if m:
            parts = [m.group(1), m.group(2)]
    if len(parts) == 1:
        return _parse_num(parts[0]), None
    if len(parts) == 2:
        return _parse_num(parts[0]), _parse_num(parts[1])
    raise ValueError("a MID egy szám vagy két szám (alsó–felső küszöb) lehet")


def parse_mid(mid, measure):
    """A minimális fontos különbség (MID / MCID) a MEGJELENÍTÉSI skálán → {lower, upper, scale: 'display', source,
    sd, text} vagy None. Elfogadott: szám (arány-mértéknél pl. 0.75 vagy 1.25 → [0.75; 1.333]; különbségnél 5 →
    [−5; 5]), szöveg ('0,75–1,25', '-5; 5', '5'), vagy {lower, upper} / {value} (+ source, sd). Egycsoportos aránynál
    nincs nullhatás, ezért MID sem adható. Hibánál ValueError (magyar üzenet)."""
    if mid is None or (isinstance(mid, str) and not mid.strip()):
        return None
    null = null_value(measure)
    if null is None:
        raise ValueError("egycsoportos aránynál (%s) nincs nullhatás, ezért MID sem adható meg" % measure)
    source = sd = None
    text = None
    if isinstance(mid, dict):
        source = mid.get("source")
        if mid.get("sd") is not None:
            sd = _parse_num(mid["sd"])
            if sd <= 0:
                raise ValueError("a MID-hez adott SD pozitív legyen")
        if "lower" in mid or "upper" in mid:
            lo = None if mid.get("lower") is None else _parse_num(mid.get("lower"))
            hi = None if mid.get("upper") is None else _parse_num(mid.get("upper"))
            if lo is None and hi is None:
                raise ValueError("a MID-nél legalább az egyik küszöb (lower / upper) kell")
            pair = (lo, hi)
        elif mid.get("value") is not None:
            pair = (_parse_num(mid["value"]), None)
        elif isinstance(mid.get("text"), str):
            text = mid["text"]
            pair = _parse_mid_text(mid["text"])
        else:
            raise ValueError("a MID objektum: {lower, upper} vagy {value} (+ source, sd)")
        single = "lower" not in mid and "upper" not in mid and pair[1] is None
    else:
        if isinstance(mid, str):
            text = mid.strip()
            pair = _parse_mid_text(mid)
        else:
            pair = (_parse_num(mid), None)
        single = pair[1] is None
    if single:
        v = pair[0]
        if measure in RATIO:
            if not v > 0 or v == 1:
                raise ValueError("arány-mértéknél a MID pozitív és 1-től különböző legyen (pl. 0.75 vagy 1.25)")
            lo, hi = (v, 1.0 / v) if v < 1 else (1.0 / v, v)
        else:
            if v == 0:
                raise ValueError("a MID nem lehet 0")
            lo, hi = -abs(v), abs(v)
    else:
        lo, hi = pair
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
    for v in (lo, hi):
        if v is None:
            continue
        if measure in RATIO and not v > 0:
            raise ValueError("arány-mértéknél a MID-küszöbök pozitívak legyenek")
        if measure in ("COR", "ZCOR") and not -1 < v < 1:
            raise ValueError("korrelációnál a MID-küszöbök a (−1; 1) tartományba essenek")
    if lo is not None and not lo < null:
        raise ValueError("a MID alsó küszöbe a nullhatás (%g) alatt legyen" % null)
    if hi is not None and not hi > null:
        raise ValueError("a MID felső küszöbe a nullhatás (%g) fölött legyen" % null)
    return {"lower": lo, "upper": hi, "scale": "display", "source": source if isinstance(source, str) else None,
            "sd": sd, "text": text}


def _thresholds(measure, mid):
    """A döntési küszöbök az ELEMZÉSI skálán (növekvő): MID-del a két (vagy egy) MID-küszöb, nélküle a nullhatás."""
    if mid:
        return sorted(t for t in (_to_analysis(measure, mid.get("lower")), _to_analysis(measure, mid.get("upper")))
                      if t is not None)
    return [] if null_value(measure) is None else [0.0]


def _crosses(lo, hi, t):
    return lo is not None and hi is not None and lo < t < hi


def _g(v, lang="hu"):
    """A felhasználó által megadott küszöb rövid alakja (felesleges nullák nélkül)."""
    s = "%.6g" % v
    return s.replace("-", MINUS) if lang == "en" else s


def _thr_text(measure, mid, lang):
    if mid:
        lo, hi = mid.get("lower"), mid.get("upper")
        if lo is not None and hi is not None and measure not in RATIO and abs(lo + hi) < 1e-12:
            return "MID ±%s" % _g(hi, lang)
        vals = [v for v in (lo, hi) if v is not None]
        if len(vals) == 1:
            return "MID %s" % _g(vals[0], lang)
        return "MID " + _pair_text(_g(vals[0], lang), _g(vals[1], lang), lang, vals[0] < 0)
    nv = null_value(measure)
    return ("nullhatás = %s" if lang == "hu" else "no effect = %s") % _fmt(nv, 0, lang)


def _pair_text(a, b, lang, negative):
    if lang == "en":
        return "%s to %s" % (a, b)
    return "[%s; %s]" % (a, b) if negative else "%s–%s" % (a, b)


def _range_text(lo, hi, lang, nd=None):
    nd = PL.decimals((lo, hi)) if nd is None else nd
    return _pair_text(_fmt(lo, nd, lang), _fmt(hi, nd, lang), lang, lo is not None and lo < 0)


# ------------------------------------------------------------------ vizsgálat-szintű adatok
def _study_rows(rd):
    """[{row_uid, study_id, label, weight_pct, y, lo, hi, rob}] az elemzett vizsgálatokra (plot/v2 vagy results)."""
    out = []
    if rd.plot and isinstance(rd.plot.get("studies"), list):
        for s in rd.plot["studies"]:
            if not isinstance(s, dict) or _num(s.get("weight_pct")) is None:
                continue
            out.append({"row_uid": s.get("row_uid"), "study_id": s.get("study_id"), "label": s.get("label"),
                        "weight_pct": float(s["weight_pct"]), "y": _num(s.get("y")), "lo": _num(s.get("lo")),
                        "hi": _num(s.get("hi")), "rob": (s.get("flags") or {}).get("rob")})
        if out:
            return out
    prim = rd.primary or {}
    labels, w = prim.get("labels") or [], prim.get("weights_pct") or []
    yi, vi = prim.get("yi") or [], prim.get("vi") or []
    z = norm_ppf(0.5 + rd.level / 2.0)
    for i, lab in enumerate(labels):
        y = _num(yi[i]) if i < len(yi) else None
        v = _num(vi[i]) if i < len(vi) else None
        se = math.sqrt(v) if v is not None and v >= 0 else None
        out.append({"row_uid": None, "study_id": None, "label": lab, "weight_pct": _num(w[i]) if i < len(w) else None,
                    "y": y, "lo": y - z * se if se is not None and y is not None else None,
                    "hi": y + z * se if se is not None and y is not None else None, "rob": None})
    return [r for r in out if r["weight_pct"] is not None]


def _apply_rob(rows, rob_by_row):
    """A sorok RoB-kategóriája (low | some | high | None): rob_by_row (row_uid / study_id / címke → ítélet, vagy a
    sorok sorrendjében lista), ennek hiányában a futás saját rob-oszlopa (plot/v2 flags.rob)."""
    if rob_by_row is not None and isinstance(rob_by_row, (list, tuple)):
        if len(rob_by_row) != len(rows):
            raise ValueError("a rob_by_row lista hossza (%d) eltér az elemzett vizsgálatok számától (%d)"
                             % (len(rob_by_row), len(rows)))
        vals = list(rob_by_row)
    elif rob_by_row is not None:
        if not isinstance(rob_by_row, dict):
            raise ValueError("a rob_by_row szótár (row_uid / study_id / címke → ítélet) vagy lista legyen")
        vals = []
        for r in rows:
            v = None
            for key in (r.get("row_uid"), r.get("study_id"), r.get("label")):
                if key is not None and key in rob_by_row:
                    v = rob_by_row[key]
                    break
            vals.append(v)
    else:
        vals = [r.get("rob") for r in rows]
    for r, v in zip(rows, vals):
        cat = tableio.rob_category(v) if v is not None else None
        r["rob_cat"] = cat if cat in ("low", "some", "high") else None
        r["rob_value"] = v
    return rows


def _side(y, thresholds):
    return sum(1 for t in thresholds if y is not None and y > t)


# ------------------------------------------------------------------ gyermek-futás (magas RoB nélkül)
def _excludes_high_rob(filters):
    excl = filters.get("exclude") or []
    incl = filters.get("include") or []
    for cond, want in [(c, "high") for c in excl] + [(c, "low") for c in incl]:
        col, _, val = str(cond).partition("=")
        if not col or not tableio.is_rob_column(col.strip()):
            continue
        cats = {tableio.rob_category(v) for v in re.split(r"[|,;]", val)}
        if want in cats:
            return True
    return False


def _find_rob_child(rd, project_dir):
    """A futás legutóbbi „magas RoB nélkül” gyermek-futása (spec.parent = a futás spec-neve, ugyanazon az adaton,
    a szűrője kizárja a magas RoB-ot) vagy None."""
    if project_dir is None or rd.desc is None:
        return None
    parent = rd.spec.get("name")
    if not parent:
        return None
    from .spec import project_run_files
    best = None
    for path, _layout, _f, _l in project_run_files(project_dir):
        folder = os.path.dirname(os.path.abspath(path))
        if rd.folder and os.path.normcase(folder) == os.path.normcase(rd.folder):
            continue
        try:
            desc = _read_json(path)
        except (OSError, ValueError):
            continue
        sp = desc.get("spec") if isinstance(desc.get("spec"), dict) else {}
        if sp.get("parent") != parent or desc.get("mode") != "commit":
            continue
        if rd.data_sha256 and (desc.get("data") or {}).get("sha256") != rd.data_sha256:
            continue
        try:
            child = _load_dir(folder)
        except (OSError, ValueError):
            continue
        flt = ((child.results.get("input") or {}).get("filters") or {})
        if not _excludes_high_rob(flt):
            continue
        key = desc.get("run_id") or ""
        if best is None or key > best[0]:
            best = (key, child)
    return best[1] if best else None


def _conclusion(rd, thresholds):
    prim = rd.primary or {}
    est, lo, hi = _num(prim.get("estimate")), _num(prim.get("ci_lower")), _num(prim.get("ci_upper"))
    crossed = tuple(t for t in thresholds if _crosses(lo, hi, t))
    return {"side": _side(est, thresholds), "crossed": crossed}


# ------------------------------------------------------------------ OIS
def _min_n(power_of, target, cap=10 ** 9):
    if power_of(1) >= target:
        return 1
    hi = 2
    while power_of(hi) < target:
        hi *= 2
        if hi > cap:
            return None
    lo = hi // 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if power_of(mid) >= target:
            hi = mid
        else:
            lo = mid
    return hi


def ois_binary(control_risk, rrr=DEFAULT_RRR, alpha=0.05, power=0.8, intervention_risk=None):
    """Optimális információméret bináris kimenetre (GRADE 6; D-S13-007): egyetlen, kétkaros, egyenlő karú vizsgálat
    mintanagysága a p0 → p1 különbség kimutatásához (kétoldali α, erő), a power modul z-teszt alapú erejével
    (power_analysis(k = 1, effect = p0 − p1, v = [p0(1−p0) + p1(1−p1)]/n, measure GEN)). p1 = p0·(1 − rrr), vagy
    intervention_risk. → {per_arm, total, control_risk, intervention_risk, rrr, alpha, power, method}"""
    p0 = _num(control_risk)
    if p0 is None or not 0 < p0 < 1:
        raise ValueError("OIS: a kontrollkockázat a (0; 1) tartományba essen")
    p1 = _num(intervention_risk)
    if p1 is None:
        if not 0 < rrr < 1:
            raise ValueError("OIS: a relatív kockázatcsökkenés a (0; 1) tartományba essen")
        p1 = p0 * (1.0 - rrr)
    if not 0 < p1 < 1 or p1 == p0:
        raise ValueError("OIS: a beavatkozási kockázat a (0; 1) tartományba essen, és térjen el a kontrolltól")
    num = p0 * (1 - p0) + p1 * (1 - p1)
    diff = p0 - p1

    def pw(n):
        return power_analysis(1, effect=diff, v=num / n, measure="GEN", heterogeneity="fixed", alpha=alpha,
                              tails=2).power
    n = _min_n(pw, power)
    return {"kind": "binary", "per_arm": n, "total": None if n is None else 2 * n, "control_risk": p0,
            "intervention_risk": p1, "rrr": 1.0 - p1 / p0, "alpha": alpha, "power": power,
            "method": "power.power_analysis (k = 1, z-teszt, nem összevont variancia)"}


def ois_continuous(delta, sd=None, alpha=0.05, power=0.8, standardized=False):
    """OIS folytonos kimenetre: egyetlen, egyenlő karú vizsgálat a delta (MID; SMD-nél SD-egységben) kimutatásához.
    Nyers MD-nél a közös sd kell. A power modul power_analysis(k = 1, measure MD | SMD) erejével.
    → {per_arm, total, delta, sd, alpha, power, method}"""
    d = _num(delta)
    if d is None or d == 0:
        raise ValueError("OIS: a kimutatandó különbség (MID) nem lehet 0")
    if not standardized and (_num(sd) is None or sd <= 0):
        raise ValueError("OIS: nyers átlagkülönbségnél a tipikus SD (> 0) kell")

    def pw(n):
        if standardized:
            return power_analysis(1, effect=abs(d), n1=n, n2=n, measure="SMD", heterogeneity="fixed", alpha=alpha,
                                  tails=2).power
        return power_analysis(1, effect=abs(d), n1=n, n2=n, sd=sd, measure="MD", heterogeneity="fixed", alpha=alpha,
                              tails=2).power
    n = _min_n(pw, power)
    return {"kind": "smd" if standardized else "md", "per_arm": n, "total": None if n is None else 2 * n,
            "delta": abs(d), "sd": None if standardized else float(sd), "alpha": alpha, "power": power,
            "method": "power.power_analysis (k = 1, z-teszt)"}


def _typical_sd(rd):
    """MD-hez a vizsgálatok tipikus SD-je a varianciából visszaszámolva (sd² ≈ v / (1/n1 + 1/n2); a karlétszám a
    plot/v2 celláiból, ennek hiányában n/2), (n − 2)-vel súlyozott átlag négyzetgyöke. Közelítés: a MID-hez adott
    'sd' felülírja."""
    prim = rd.primary or {}
    vi = prim.get("vi") or []
    ni = (rd.results.get("effect_sizes") or {}).get("ni") or []
    cells = []
    if rd.plot and isinstance(rd.plot.get("studies"), list):
        cells = [s.get("cells") or {} for s in rd.plot["studies"] if _num(s.get("weight_pct")) is not None]
    num = den = 0.0
    for i, v in enumerate(vi):
        v = _num(v)
        if v is None or v <= 0:
            continue
        n1 = n2 = None
        if i < len(cells):
            try:
                n1, n2 = _parse_num(cells[i].get("n1")), _parse_num(cells[i].get("n2"))
            except (ValueError, TypeError):
                n1 = n2 = None
        if not (n1 and n2) and i < len(ni) and _num(ni[i]):
            n1 = n2 = _num(ni[i]) / 2.0
        if not (n1 and n2) or n1 + n2 <= 2:
            continue
        s2 = v / (1.0 / n1 + 1.0 / n2)
        num += (n1 + n2 - 2) * s2
        den += n1 + n2 - 2
    return math.sqrt(num / den) if den > 0 else None


def _risk(measure, x, acr):
    if measure == "RR":
        return acr * x
    if measure == "OR":
        return acr * x / (1.0 - acr + acr * x)
    return acr + x


# ------------------------------------------------------------------ doménenkénti tanács
_UNCERTAIN = {
    "borderline": _tr("Határeset: a számok a szokásos küszöb közelében vannak — a döntés mérlegelés, indokold.",
                      "Borderline: the numbers are close to the usual threshold — the decision is a judgement; "
                      "explain it."),
    "insufficient_data": _tr("Nincs elég adat: a motor nem tud ítéletet javasolni.",
                             "Not enough data: the engine cannot suggest a judgement."),
}


def _suggest(rating, step, status, summary, why, flags, kb, evidence=None):
    """A domén javaslata (11. döntés, 6. pont formája): javasolt ítélet és lépés, bizonyíték (evidence: rövid
    számsorok), indoklás egyszerű nyelven (why: asks — mit kérdez, because — miért ez a javaslat, change — mi
    változtatná meg, uncertain — bizonytalanság / határeset), flags, kb_refs; concern: leminősítést (vagy feloldandó
    „gyanított”-at) jelez."""
    parts = ([_UNCERTAIN[status]] if status in _UNCERTAIN else []) + \
        [f["text"] for f in flags if f.get("level") == "warning"]
    why = dict(why, uncertain={lang: " ".join(p[lang] for p in parts) for lang in ("hu", "en")} if parts else None)
    concern = (step is not None and step < 0) or rating in ("suspected", "strongly suspected")
    return {"rating": rating, "step": step, "status": status, "concern": concern, "summary": summary,
            "evidence": list(evidence or []), "why": why, "flags": flags, "kb_refs": list(kb)}


def _why(asks, because, change):
    return {"asks": asks, "because": because, "change": change, "uncertain": None}


def _ev(hu, en, text):
    """Bizonyíték-sor a „Motor-tanács” oszlophoz: {label {hu, en}, text {hu, en}}."""
    return {"label": _tr(hu, en), "text": text if isinstance(text, dict) else _tr(str(text), str(text))}


ROBINS_TOOLS = {"robins-i": "ROBINS-I", "robins-e": "ROBINS-E"}
_REVIEW_LEVEL_TOOLS = ("amstar2", "grade", "tripod-ai", "tripod", "probast-ai")
GRADE18 = "GRADE 18 (Schünemann et al. J Clin Epidemiol 2019;111:105)"


def _rob_tool(project_dir, oid, meta_o=None):
    """A kimenet vizsgálatonkénti RoB-eszköze a projektből → 'robins-i' | 'robins-e' | 'rob2' | 'mixed' | None:
    a kimenet appraisal_tool(s) mezője, a ma-projekt.json appraisal_tools-a (az áttekintés-szintűek nélkül), és a
    04_torzitas_kockazat/appraisals lezárt értékeléseinek eszköze (a kimenetre vagy kimenet nélkül). Több eszköz
    (pl. RoB 2 és ROBINS-I) → 'mixed'."""
    if project_dir is None:
        return None
    tools = set()
    meta_o = meta_o or {}
    own = meta_o.get("appraisal_tool")
    if isinstance(own, str) and own.strip():
        tools.add(own.strip().lower())
    for t in meta_o.get("appraisal_tools") or [] if isinstance(meta_o.get("appraisal_tools"), list) else []:
        if isinstance(t, str) and t.strip():
            tools.add(t.strip().lower())
    if not tools:
        try:
            meta = P.load_project_meta(project_dir) or {}
        except (ValueError, OSError):
            meta = {}
        for t in meta.get("appraisal_tools") or [] if isinstance(meta.get("appraisal_tools"), list) else []:
            if isinstance(t, str) and t.strip():
                tools.add(t.strip().lower())
    d = os.path.join(project_dir, "04_torzitas_kockazat", "appraisals")
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json") or fn.startswith("."):
                continue
            try:
                doc = _read_json(os.path.join(d, fn))
            except (OSError, ValueError):
                continue
            if not isinstance(doc, dict) or doc.get("status") not in ("complete", "consensus", "final"):
                continue
            if doc.get("origin") == "ai_draft" and not doc.get("approved_by"):
                continue
            tg = doc.get("target") if isinstance(doc.get("target"), dict) else {}
            if tg.get("outcome") not in (None, oid):
                continue
            if isinstance(doc.get("tool"), str):
                tools.add(doc["tool"].strip().lower())
    tools = {t for t in tools if t not in _REVIEW_LEVEL_TOOLS}
    if not tools:
        return None
    if len(tools) > 1:
        return "mixed" if tools & set(ROBINS_TOOLS) else sorted(tools)[0]
    return tools.pop()


_ROBINS_I_WORDS = re.compile(r"^\s*(?:moderate|serious|critical)(?:\s+risk(?:\s+of\s+bias)?)?\s*$", re.I)


def _robins_vocabulary(rows):
    """Igaz, ha a rob-értékek közt ROBINS-I-szó (moderate / serious / critical risk of bias) van — a RoB 2 (low /
    some concerns / high) és a ROBINS-E 2023 (… / very high) nem használja őket."""
    return any(isinstance(r.get("rob_value"), str) and _ROBINS_I_WORDS.match(r["rob_value"]) for r in rows)


def _robins_adjust(rob_tool, start, rating, status, pct, flags, because, kb):
    """GRADE 18 a ROBINS-I / ROBINS-E-vel értékelt nem randomizált bizonyítékra (methodology:M8): magas kiindulásnál
    a „serious” / „critical” ítéletű súly-többség −2 (a gyakorlatban legalább két szint); alacsony kiindulásnál a
    további RoB-leminősítés a zavaró tényezőket kétszer számolná. → (rating, status, flags, because, kb)"""
    name = ROBINS_TOOLS[rob_tool]
    kb = list(kb) + ["D-S13-001", "GRADE-02"]
    if start == "low":
        flags.append(_flag("grade18_double_count",
                           "%s-értékelés alacsony kiindulással: a %s már tartalmazza a zavaró tényezők és a szelekció "
                           "kockázatát, ezért alacsonyról indulva a RoB-doménben ugyanezért újra leminősíteni kettős "
                           "számolás (%s). Vagy indulj magasról (és itt minősíts le), vagy itt ne számold újra a "
                           "zavaró tényezőket." % (name, name, GRADE18),
                           "%s appraisal with a low start: %s already covers confounding and selection, so rating down "
                           "again for them in the risk-of-bias domain after starting low double counts it (%s). Either "
                           "start high (and rate down here) or do not count confounding again here." % (
                               name, name, GRADE18), "warning"))
        if rating != "not serious":
            status = "borderline"
        extra = _tr(" %s: alacsony kiindulásnál a zavaró tényezőket ne számold kétszer." % GRADE18,
                    " %s: with a low start do not count confounding twice." % GRADE18)
    elif start == "high":
        if pct["high"] >= 50:
            rating, status = "very serious", "suggested"
            flags[:] = [f for f in flags if f["code"] != "very_serious_possible"]
        flags.append(_flag("grade18_two_levels",
                           "%s-tel értékelt NRSI magas kiindulással (%s): a „serious” / „critical” ítéletű súly-többség "
                           "a gyakorlatban legalább két szint leminősítést jelent; a „moderate” túlsúly jellemzően egyet."
                           % (name, GRADE18),
                           "NRSI assessed with %s and a high start (%s): a weight majority judged 'serious' / "
                           "'critical' generally means rating down by at least two levels; a 'moderate' majority "
                           "typically one." % (name, GRADE18), "info"))
        extra = _tr(" %s: ROBINS-szel magasról indulva a „serious” / „critical” súly-többség −2." % GRADE18,
                    " %s: starting high with ROBINS, a 'serious' / 'critical' weight majority is −2." % GRADE18)
    else:
        flags.append(_flag("grade18_start",
                           "%s-értékelés: a kiindulás magas lehet (%s), ekkor a RoB-domén jellemzően több szinttel "
                           "minősít le; add meg a kiindulást." % (name, GRADE18),
                           "%s appraisal: the start may be high (%s), and then the risk-of-bias domain usually rates "
                           "down by more than one level; set the starting level." % (name, GRADE18), "info"))
        extra = _tr("", "")
    because = {k: because[k] + extra[k] for k in because}
    return rating, status, flags, because, kb


def _advise_rob(rd, rows, child, thresholds, project_dir, start=None, rob_tool=None):
    asks = _tr("Mennyire bízhatunk abban, hogy a bevont vizsgálatok jól voltak kivitelezve (randomizálás, "
               "elrejtés, vakítás, hiányzó adatok, szelektív közlés)? A GRADE azt nézi, hogy az összesített eredmény "
               "SÚLYÁNAK mekkora része jön korlátozott (magas vagy „some concerns” kockázatú) vizsgálatokból — nem a "
               "vizsgálatok darabszámát.",
               "How far can we trust that the included studies were well conducted (randomisation, concealment, "
               "blinding, missing data, selective reporting)? GRADE looks at how much of the WEIGHT of the pooled "
               "result comes from studies with limitations (high risk or some concerns) — not at the number of "
               "studies.")
    w = {"high": 0.0, "some": 0.0, "low": 0.0, None: 0.0}
    for r in rows:
        w[r.get("rob_cat")] += r["weight_pct"]
    total = sum(w.values()) or 100.0
    pct = {k: 100.0 * v / total for k, v in w.items()}
    high_rows = sorted((r for r in rows if r.get("rob_cat") == "high"), key=lambda r: -r["weight_pct"])
    concern = pct["high"] + pct["some"]
    n_assessed = sum(1 for r in rows if r.get("rob_cat"))
    child_info = None
    if child is not None and child.primary is not None:
        a, b = _conclusion(rd, thresholds), _conclusion(child, thresholds)
        changed = a["side"] != b["side"] or a["crossed"] != b["crossed"]
        child_info = {"run_id": child.run_id, "spec": child.spec.get("name"), "k": (child.primary or {}).get("k"),
                      "display_text": child.display_text, "conclusion_changed": changed}
    advisory = {"high_rob_weight_pct": pct["high"], "some_concerns_weight_pct": pct["some"],
                "low_rob_weight_pct": pct["low"], "unassessed_weight_pct": pct[None],
                "limited_weight_pct": concern, "k_assessed": n_assessed,
                "k": len(rows), "high_rob_studies": [r["label"] for r in high_rows],
                "sensitivity_run": child_info}
    flags = []
    if n_assessed == 0:
        why = _why(asks, _tr("Ehhez a futáshoz nincs torzításikockázat-értékelés (a táblában nincs rob oszlop, és "
                             "értékelés sem érkezett), így a súlyarány nem számolható.",
                             "There is no risk-of-bias assessment for this run (no rob column in the table and no "
                             "appraisal), so the weight share cannot be computed."),
                   _tr("Töltsd ki az értékeléseket (RoB 2 / ROBINS-I …), és a konszenzusos összítélet kerüljön a "
                       "tábla rob oszlopába.", "Complete the appraisals (RoB 2 / ROBINS-I …) and copy the consensus "
                       "overall judgement into the table's rob column."))
        return advisory, _suggest(None, None, "insufficient_data",
                                  _tr("nincs RoB-adat", "no risk-of-bias data"), why,
                                  [_flag("rob_missing", "Nincs RoB-értékelés.", "No risk-of-bias assessment.",
                                         "warning")], KB["risk_of_bias"])
    names = _names([r["label"] for r in high_rows])
    summary = _both(lambda lang: (
        ("magas RoB-ú vizsgálatok súlya %s%s; korlátozott összesen %s" if lang == "hu" else
         "weight of high-RoB studies %s%s; limited in total %s")
        % (_pct(pct["high"]), (" (%s)" % names) if names else "", _pct(concern))))
    because = _both(lambda lang: (
        "A súly %s-a magas, %s-a „some concerns” kockázatú vizsgálatokból jön (együtt %s)%s." % (
            _pct(pct["high"]), _pct(pct["some"]), _pct(concern),
            ("; %s súlyú vizsgálatnak nincs értékelése" % _pct(pct[None])) if n_assessed < len(rows) else "")
        if lang == "hu" else
        "%s of the weight comes from high-risk and %s from some-concerns studies (%s together)%s." % (
            _pct(pct["high"]), _pct(pct["some"]), _pct(concern),
            ("; %s of the weight has no assessment" % _pct(pct[None])) if n_assessed < len(rows) else "")))
    if child_info is not None:
        ct = child_info["display_text"] or {"hu": "–", "en": "–"}
        extra = _both(lambda lang: (
            " A magas RoB nélküli érzékenységi futás (%s, k = %s) eredménye %s — a következtetés %s." % (
                child_info["run_id"] or child_info["spec"], child_info["k"], ct[lang],
                "MEGVÁLTOZIK" if child_info["conclusion_changed"] else "nem változik")
            if lang == "hu" else
            " The sensitivity run without high-RoB studies (%s, k = %s) gives %s — the conclusion %s." % (
                child_info["run_id"] or child_info["spec"], child_info["k"], ct[lang],
                "CHANGES" if child_info["conclusion_changed"] else "does not change")))
        because = {k: because[k] + extra[k] for k in because}
    elif pct["high"] > 0:
        flags.append(_flag("no_sensitivity_run",
                           "Nincs „magas RoB nélkül” gyermek-futás (X006): futtasd, és nézd meg, változik-e a "
                           "következtetés.",
                           "No 'without high RoB' child run (X006): run it and check whether the conclusion "
                           "changes.", "warning"))
    if n_assessed < len(rows):
        flags.append(_flag("rob_partly_missing", "A súly %s-ának nincs RoB-értékelése." % _pct(pct[None]),
                           "%s of the weight has no risk-of-bias assessment." % _pct(pct[None]), "warning"))
    rule = _tr(" Szokásos lépés (GRADE-03): 0, ha a súly túlnyomó része alacsony kockázatú és a kizárás nem változtat "
               "a következtetésen; −1, ha a súly jelentős része (pl. többsége) korlátozott vizsgálatból jön, vagy a "
               "kizárás megváltoztatja a következtetést; −2, ha gyakorlatilag minden információ több kulcsdoménben "
               "súlyosan korlátozott vizsgálatból jön.",
               " Usual step (GRADE-03): 0 if most of the weight comes from low-risk studies and excluding the others "
               "does not change the conclusion; −1 if a substantial part (e.g. the majority) comes from studies with "
               "limitations, or excluding them changes the conclusion; −2 if practically all information comes from "
               "studies with serious limitations in several key domains.")
    because = {k: because[k] + rule[k] for k in because}
    evidence = [_ev("Magas RoB súlya", "Weight of high RoB", _both(lambda lang: "%s (%d %s)" % (
        _pct(pct["high"]), len(high_rows), "vizsgálat" if lang == "hu" else ("study" if len(high_rows) == 1 else
                                                                            "studies")))),
                _ev("„Some concerns” súlya", "Weight of some concerns", _pct(pct["some"])),
                _ev("Korlátozott összesen", "Limited in total", _pct(concern))]
    if n_assessed < len(rows):
        evidence.append(_ev("Értékelés nélkül", "Not assessed", _pct(pct[None])))
    if child_info is not None:
        ct = child_info["display_text"] or _tr("–", "–")
        evidence.append(_ev("Magas RoB nélküli futás", "Run without high RoB", _both(lambda lang: "%s, k = %s — %s" % (
            ct[lang], child_info["k"], ("a következtetés változik" if child_info["conclusion_changed"] else
                                        "a következtetés nem változik") if lang == "hu" else
            ("the conclusion changes" if child_info["conclusion_changed"] else "the conclusion does not change")))))
    if child_info is not None and child_info["conclusion_changed"]:
        rating, status = "serious", "suggested"
    elif concern >= 50:
        rating, status = "serious", "suggested"
        if pct["high"] >= 90:
            flags.append(_flag("very_serious_possible",
                               "Gyakorlatilag minden súly magas kockázatú vizsgálatból jön: −2 is mérlegelhető, ha "
                               "több kulcsdoménben súlyos a korlát.",
                               "Practically all weight comes from high-risk studies: −2 may be considered if the "
                               "limitations are serious in several key domains.", "warning"))
    elif concern >= 25 or pct[None] >= 25:
        rating, status = "not serious", "borderline"
    else:
        rating, status = "not serious", "suggested"
    kb = KB["risk_of_bias"]
    if rob_tool in ROBINS_TOOLS:
        rating, status, flags, because, kb = _robins_adjust(rob_tool, start, rating, status, pct, flags, because, kb)
    elif rob_tool == "mixed":
        flags.append(_flag("mixed_rob_tools",
                           "RoB 2 és ROBINS mellett: a randomizált és a nem randomizált bizonyítékot külön értékeld "
                           "(külön SoF-sor; D-S13-001, %s)." % GRADE18,
                           "RoB 2 and ROBINS together: rate randomised and non-randomised evidence separately "
                           "(separate SoF rows; D-S13-001, %s)." % GRADE18, "warning"))
    change = _tr("Ha a magas kockázatú vizsgálatok kizárása megváltoztatja a következtetést, vagy a súly többsége "
                 "korlátozott vizsgálatból jön, a −1 indokolt; ha a hiányzó értékelések pótlása vagy a konszenzus "
                 "más arányt ad, a javaslat is változik. Szubjektív kimenetnél vakítás nélkül a korlát súlyosabb.",
                 "If excluding the high-risk studies changes the conclusion, or most of the weight comes from "
                 "limited studies, −1 is warranted; completing the missing appraisals or the consensus may change "
                 "the shares and thus the suggestion. With subjective outcomes and no blinding the limitation "
                 "weighs more.")
    advisory["rob_tool"] = rob_tool
    return advisory, _suggest(rating, P.GRADE_RATINGS[rating], status, summary, _why(asks, because, change), flags,
                              kb, evidence)


def _advise_inconsistency(rd, rows, thresholds, mid):
    m = rd.measure
    prim = rd.primary or {}
    het = prim.get("heterogeneity") or {}
    k = prim.get("k") or len(rows)
    asks = _tr("Hasonló-e a hatás a vizsgálatokban, vagy annyira eltérnek, hogy ez bizonytalanná teszi a "
               "következtetést? Nem az I² egy határértéke dönt, hanem együtt: a vizsgálatok a döntési küszöb "
               "(nullhatás vagy MID) különböző oldalára esnek-e, mennyire fedik egymást a CI-k, és hová nyúlik a "
               "predikciós intervallum (PI: hol várható a hatás egy új vizsgálatban).",
               "Is the effect similar across studies, or do they differ so much that the conclusion becomes "
               "uncertain? No single I² cut-off decides; look together at whether the studies fall on different "
               "sides of the decision threshold (no effect or MID), how much their CIs overlap, and where the "
               "prediction interval reaches (PI: where the effect of a new study is expected).")
    i2, i2ht, i2qp = _num(prim.get("I2")), het.get("I2_ci_HT"), het.get("I2_ci_QP")
    pi_a = (_num(prim.get("pi_lower")), _num(prim.get("pi_upper")))
    ci_a = (_num(prim.get("ci_lower")), _num(prim.get("ci_upper")))
    advisory = {"k": k, "I2": i2, "I2_ci_HT": i2ht if isinstance(i2ht, list) else None,
                "I2_ci_QP": i2qp if isinstance(i2qp, list) else None, "tau2": _num(prim.get("tau2")),
                "tau": _num(prim.get("tau")), "Q": _num(prim.get("Q")), "p_Q": _num(prim.get("p_Q")),
                "pi": rd.pi_display, "pi_crosses_null": None, "pi_crosses_mid": None, "ci_crosses_null": None,
                "minority_weight_pct": None, "opposite_significant": None}
    if k is None or k < 2:
        why = _why(asks, _tr("Egyetlen vizsgálat van, így a vizsgálatok közötti eltérés nem ítélhető meg.",
                             "There is a single study, so between-study differences cannot be judged."),
                   _tr("További vizsgálatok. A kevés adatot a pontatlanságnál mérlegeld.",
                       "More studies. Weigh the scarce data under imprecision."))
        return advisory, _suggest(None, None, "not_applicable", _tr("k = 1: nem alkalmazható", "k = 1: not applicable"),
                                  why, [], KB["inconsistency"])
    i2_text = _both(lambda lang: "I² %s%s" % (_pct(i2, 0), (" [%s; %s]" % (_fmt(i2ht[0], 0), _fmt(i2ht[1], 0)))
                                               if isinstance(i2ht, list) and None not in i2ht else ""))
    nv = null_value(m)
    flags = []
    if nv is None:
        because = _both(lambda lang: (
            "%s, τ² = %s. Egycsoportos aránynál nincs nullhatás: a szóródás klinikai jelentőségét a PI (%s) alapján "
            "ítéld meg." % (i2_text["hu"], _fmt(prim.get("tau2"), 3), _range_text(*rd.pi_display, lang="hu")
                            if rd.pi_display else "–")
            if lang == "hu" else
            "%s, τ² = %s. A single-group proportion has no null effect: judge the clinical importance of the spread "
            "from the PI (%s)." % (i2_text["en"], _fmt(prim.get("tau2"), 3, "en"),
                                    _range_text(*rd.pi_display, lang="en") if rd.pi_display else "–")))
        why = _why(asks, because, _tr("Ha egy előre tervezett alcsoport magyarázza a szóródást (GRADE-04a).",
                                      "If a prespecified subgroup explains the spread (GRADE-04a)."))
        return advisory, _suggest(None, None, "human_judgement", _tr("%s — emberi ítélet" % i2_text["hu"],
                                                                     "%s — human judgement" % i2_text["en"]),
                                  why, flags, KB["inconsistency"])
    weights = {}
    for r in rows:
        weights.setdefault(_side(r["y"], thresholds), 0.0)
        weights[_side(r["y"], thresholds)] += r["weight_pct"]
    tot = sum(weights.values()) or 100.0
    minority = 100.0 - 100.0 * max(weights.values()) / tot if weights else 0.0
    below = [r for r in rows if r["hi"] is not None and r["hi"] < 0.0]
    above = [r for r in rows if r["lo"] is not None and r["lo"] > 0.0]
    opp = bool(below) and bool(above)
    pi_cross_null = _crosses(pi_a[0], pi_a[1], 0.0) if None not in pi_a else None
    ci_cross_null = _crosses(ci_a[0], ci_a[1], 0.0)
    pi_only = any(_crosses(pi_a[0], pi_a[1], t) and not _crosses(ci_a[0], ci_a[1], t) for t in thresholds) \
        if None not in pi_a else False
    pi_cross_mid = (any(_crosses(pi_a[0], pi_a[1], t) for t in thresholds) if None not in pi_a else None) \
        if mid else None
    advisory.update({"pi_crosses_null": pi_cross_null, "pi_crosses_mid": pi_cross_mid,
                     "ci_crosses_null": ci_cross_null, "minority_weight_pct": minority,
                     "opposite_significant": opp, "threshold_basis": "mid" if mid else "null"})
    pi_txt = _both(lambda lang: ("PI %s" % _range_text(*rd.pi_display, lang=lang)) if rd.pi_display else "")
    thr = _both(lambda lang: _thr_text(m, mid, lang))
    summary = _both(lambda lang: "%s%s%s" % (
        i2_text[lang], ("; %s" % pi_txt[lang]) if pi_txt[lang] else "",
        ((" átnyúlik: %s ⚠" if lang == "hu" else " crosses: %s ⚠") % thr[lang]) if pi_only else ""))
    because = _both(lambda lang: " ".join(x for x in (
        ("%s, τ² = %s (τ = %s)." % (i2_text[lang], _fmt(prim.get("tau2"), 3, lang), _fmt(prim.get("tau"), 3, lang))),
        (("A predikciós intervallum (%s) átnyúlik a küszöbön (%s), a CI viszont nem: egy új vizsgálatban a hatás "
          "iránya vagy jelentősége más lehet." if lang == "hu" else
          "The prediction interval (%s) crosses the threshold (%s) while the CI does not: in a new study the "
          "direction or importance of the effect may differ.") % (pi_txt[lang][3:], thr[lang])) if pi_only else "",
        (("A súly %s-a a küszöb (%s) másik oldalán van." if lang == "hu" else
          "%s of the weight lies on the other side of the threshold (%s).") % (_pct(minority), thr[lang])),
        (("Vannak egymással ellentétes irányú, szignifikáns vizsgálatok (%s ↔ %s)." if lang == "hu" else
          "Some studies are significant in opposite directions (%s ↔ %s).") % (
            _names([r["label"] for r in below], 2), _names([r["label"] for r in above], 2))) if opp else "")
        if x))
    evidence = [_ev("I² (95% CI)", "I² (95% CI)", _both(lambda lang: i2_text[lang][3:])),
                _ev("τ²", "τ²", _both(lambda lang: _fmt(prim.get("tau2"), 3, lang)))]
    if rd.pi_display:
        evidence.append(_ev("Predikciós intervallum", "Prediction interval",
                            _both(lambda lang: _range_text(*rd.pi_display, lang=lang))))
    evidence.append(_ev("A küszöb másik oldalán", "Other side of the threshold",
                        _both(lambda lang: ("a súly %s-a (%s)" if lang == "hu" else "%s of the weight (%s)")
                              % (_pct(minority), thr[lang]))))
    if opp and minority >= 10:
        rating, status = "serious", "suggested"
        flags.append(_flag("very_serious_possible",
                           "Ellentétes irányú, klinikailag fontos hatások: kivételesen −2 is mérlegelhető.",
                           "Clinically important effects in opposite directions: −2 may exceptionally be considered.",
                           "warning"))
    elif minority >= 10 and (pi_only or (i2 is not None and i2 >= 50)):
        rating, status = "serious", "suggested"
    elif minority < 10 and ((i2 is not None and i2 >= 50) or pi_cross_null):
        rating, status = "not serious", "borderline"
        flags.append(_flag("same_side", "Minden számottevő vizsgálat a küszöb ugyanazon oldalán van: pusztán a magas "
                                        "I² miatt ne minősíts le (D-S13-004).",
                           "All material studies lie on the same side of the threshold: do not rate down for a high "
                           "I² alone (D-S13-004).", "info"))
    else:
        rating, status = "not serious", "suggested"
    if k < 5:
        flags.append(_flag("few_studies", "k = %d: a heterogenitás becslése (τ², I²) nagyon bizonytalan; a nem "
                                          "szignifikáns Q nem bizonyít homogenitást." % k,
                           "k = %d: the heterogeneity estimates (τ², I²) are very uncertain; a non-significant Q "
                           "does not prove homogeneity." % k, "warning"))
    if rd.results.get("subgroups") or rd.results.get("metaregression"):
        flags.append(_flag("subgroups_present", "Van alcsoport- / meta-regressziós eredmény: ha hiteles és előre "
                                                "tervezett, magyarázhatja a heterogenitást (GRADE-04a).",
                           "Subgroup / meta-regression results exist: if credible and prespecified they may explain "
                           "the heterogeneity (GRADE-04a).", "info"))
    change = _tr("Ha egy előre tervezett, hiteles alcsoport-elemzés megmagyarázza az eltérést, alcsoportonként adj "
                 "becslést és külön ítéletet (GRADE-04a); ha minden vizsgálat a küszöb ugyanazon oldalán van, a magas "
                 "I² önmagában nem ok a leminősítésre. Ugyanazt a szóródást ne számold kétszer (pontatlanságnál is).",
                 "If a prespecified, credible subgroup analysis explains the differences, give estimates and separate "
                 "judgements per subgroup (GRADE-04a); if all studies lie on the same side of the threshold, a high "
                 "I² alone is no reason to rate down. Do not count the same spread twice (also under imprecision).")
    return advisory, _suggest(rating, P.GRADE_RATINGS[rating], status, summary, _why(asks, because, change), flags,
                              KB["inconsistency"], evidence)


def _advise_indirectness():
    asks = _tr("Ugyanarra a kérdésre válaszolnak-e a vizsgálatok, mint a protokoll (PICO)? Eltérő populáció vagy "
               "környezet, más dózis vagy kontroll (pl. placebo aktív kontroll helyett), helyettesítő (surrogate) "
               "végpont vagy túl rövid követés, illetve csak közvetett összehasonlítás csökkenti a bizonyosságot.",
               "Do the studies answer the same question as the protocol (PICO)? A different population or setting, "
               "another dose or comparator (e.g. placebo instead of an active control), a surrogate end point or "
               "too short follow-up, or only indirect comparisons lower the certainty.")
    because = _tr("Ezt a motor nem tudja kiszámolni: emberi (klinikai) ítélet kell. Vesd össze a vizsgálatjellemzők "
                  "táblázatát a protokoll PICO-jával, a súly szerint fontos vizsgálatokra összpontosítva.",
                  "The engine cannot compute this: it needs a human (clinical) judgement. Compare the table of study "
                  "characteristics with the protocol PICO, focusing on the studies that carry most weight.")
    change = _tr("−1, ha a súly jelentős részét adó vizsgálatoknál az eltérés valószínűleg módosítja a hatást; −2, ha "
                 "több súlyos eltérés halmozódik (pl. helyettesítő végpont és eltérő populáció).",
                 "−1 if the difference probably modifies the effect in studies carrying much of the weight; −2 if "
                 "several serious differences accumulate (e.g. surrogate end point and a different population).")
    return {"human_judgement": True}, _suggest(None, None, "human_judgement",
                                               _tr("— (emberi ítélet)", "— (human judgement)"),
                                               _why(asks, because, change), [], KB["indirectness"])


def _ois_for(rd, mid, ois_rrr, alpha, power):
    """(ois-dict vagy None, feltevés-szöveg {hu, en} vagy None, figyelmeztetés-flagek)"""
    m = rd.measure
    tot = rd.totals
    flags = []
    try:
        if m in BINARY:
            p0 = _num(tot.get("control_risk"))
            if p0 is None or not 0 < p0 < 1:
                return None, None, flags
            p1 = None
            if mid and ois_rrr is None:
                if m in ("RR", "OR") and mid.get("lower") is not None:
                    p1 = _risk(m, mid["lower"], p0)
                elif m == "RD" and mid.get("lower") is not None and p0 + mid["lower"] > 0:
                    p1 = p0 + mid["lower"]
            res = ois_binary(p0, ois_rrr or DEFAULT_RRR, alpha, power, intervention_risk=p1)
            txt = _both(lambda lang: (
                "kontrollkockázat %s/1000 (Σe2/Σn2), %s relatív kockázatcsökkenés, α = %s, erő %s" % (
                    _fmt(1000 * p0, 1), _pct(100 * res["rrr"], 0), _fmt(alpha, 2), _pct(100 * power, 0))
                if lang == "hu" else
                "control risk %s per 1,000 (Σe2/Σn2), %s relative risk reduction, α = %s, power %s" % (
                    _fmt(1000 * p0, 1, "en"), _pct(100 * res["rrr"], 0), _fmt(alpha, 2, "en"),
                    _pct(100 * power, 0))))
            return res, txt, flags
        if m == "MD":
            if not mid:
                return None, None, flags
            delta = min(abs(v) for v in (mid.get("lower"), mid.get("upper")) if v is not None)
            sd = mid.get("sd") or _typical_sd(rd)
            if not sd:
                return None, None, flags
            res = ois_continuous(delta, sd, alpha, power)
            approx = mid.get("sd") is None
            txt = _both(lambda lang: (
                "MID %s, tipikus SD %s%s, α = %s, erő %s" % (_fmt(delta, 2), _fmt(sd, 2),
                                                             " (a varianciákból visszaszámolva)" if approx else "",
                                                             _fmt(alpha, 2), _pct(100 * power, 0))
                if lang == "hu" else
                "MID %s, typical SD %s%s, α = %s, power %s" % (
                    _fmt(delta, 2, "en"), _fmt(sd, 2, "en"), " (back-calculated from the variances)" if approx else "",
                    _fmt(alpha, 2, "en"), _pct(100 * power, 0))))
            return res, txt, flags
        if m in ("SMD", "COHEN_D", "SMD_GLASS", "SMCC"):
            delta = min(abs(v) for v in (mid.get("lower"), mid.get("upper")) if v is not None) if mid else DEFAULT_SMD
            res = ois_continuous(delta, standardized=True, alpha=alpha, power=power)
            txt = _both(lambda lang: ("%s = %s SD%s, α = %s, erő %s" if lang == "hu" else
                                      "%s = %s SD%s, α = %s, power %s") % (
                "MID" if mid else ("feltételezett hatás" if lang == "hu" else "assumed effect"),
                _fmt(delta, 2, lang), "" if mid else (" (kis hatás; adj meg MID-et)" if lang == "hu" else
                                                     " (small effect; give a MID)"),
                _fmt(alpha, 2, lang), _pct(100 * power, 0)))
            return res, txt, flags
    except (ValueError, ModelError, ArithmeticError):
        return None, None, flags
    return None, None, flags


def _advise_imprecision(rd, mid, thresholds, ois_rrr, alpha, power):
    m = rd.measure
    prim = rd.primary or {}
    tot = rd.totals
    k = prim.get("k")
    asks = _tr("Elég pontos-e a becslés a döntéshez? A konfidencia-intervallumot (CI) a döntési küszöbhöz mérjük — a "
               "minimális fontos különbséghez (MID), ennek hiányában a nullhatáshoz —, nem a p-értékhez; és megnézzük, "
               "elég nagy-e a teljes minta (optimális információméret, OIS: amennyi résztvevő egyetlen, megfelelő "
               "erejű vizsgálathoz kellene).",
               "Is the estimate precise enough for a decision? The confidence interval (CI) is compared with the "
               "decision threshold — the minimal important difference (MID), or no effect if there is none — not "
               "with the p value; and we check whether the total sample is large enough (optimal information size, "
               "OIS: the number of participants a single adequately powered trial would need).")
    lo, hi = _num(prim.get("ci_lower")), _num(prim.get("ci_upper"))
    nv = null_value(m)
    ci_null = _crosses(lo, hi, 0.0) if nv is not None else None
    crossed = [t for t in thresholds if _crosses(lo, hi, t)]
    ci_mid = (len(crossed) > 0) if mid else None
    ci_both = (len(crossed) >= 2) if mid else None
    participants = _count(tot.get("participants"))
    if participants is None and _num(tot.get("n1")) is not None and _num(tot.get("n2")) is not None:
        participants = _count(_num(tot["n1"]) + _num(tot["n2"]))
    events = None
    if m in BINARY and _num(tot.get("events1")) is not None and _num(tot.get("events2")) is not None:
        events = _count(_num(tot["events1"]) + _num(tot["events2"]))
    ois, ois_txt, flags = _ois_for(rd, mid, ois_rrr, alpha, power)
    ois_total = ois["total"] if ois else None
    ois_met = (participants >= ois_total) if (ois_total is not None and participants is not None) else None
    advisory = {"k": k, "ci": rd.bt, "ci_crosses_null": ci_null, "ci_crosses_mid": ci_mid,
                "ci_crosses_both_mid": ci_both, "threshold_basis": "mid" if mid else "null",
                "participants": participants, "events": events, "ois": ois_total, "ois_met": ois_met,
                "ois_assumptions": ois, "events_below_300": (events < RULE_OF_THUMB_EVENTS) if events is not None else
                None}
    bt = rd.bt
    thr = _both(lambda lang: _thr_text(m, mid, lang))
    ci_txt = _both(lambda lang: _range_text(bt[1], bt[2], lang=lang) if None not in bt[1:] else "–")
    crosses_any = (ci_mid if mid else ci_null)
    parts_hu, parts_en = [], []
    if nv is None:
        parts_hu.append("Egycsoportos aránynál nincs nullhatás: a CI (%s) szélességét a klinikai döntéshez mérd." %
                        ci_txt["hu"])
        parts_en.append("A single-group proportion has no null effect: judge the CI width (%s) against the clinical "
                        "decision." % ci_txt["en"])
    else:
        par = _both(lambda lang: ci_txt[lang] if ci_txt[lang].startswith("[") else "(%s)" % ci_txt[lang])
        parts_hu.append("A %d%%-os CI %s %s a küszöböt (%s)." % (round(100 * rd.level), par["hu"],
                                                                  "átlépi" if crosses_any else "nem lépi át",
                                                                  thr["hu"]))
        parts_en.append("The %d%% CI %s %s the threshold (%s)." % (round(100 * rd.level), par["en"],
                                                                   "crosses" if crosses_any else "does not cross",
                                                                   thr["en"]))
        if mid and ci_null and not ci_mid:
            parts_hu.append("A CI tartalmazza a nullhatást, de mindkét MID-en belül marad: a hatás pontosan "
                            "„kicsi vagy nincs”.")
            parts_en.append("The CI includes no effect but stays within both MIDs: the effect is precisely "
                            "'little or none'.")
    if ois_total is not None:
        parts_hu.append("OIS ≈ %s fő (%s); a résztvevők száma %s, ez %s." % (
            ois_total, ois_txt["hu"], _int_text(participants) or "ismeretlen",
            "meghaladja" if ois_met else ("nem éri el" if ois_met is False else "nem vethető össze")))
        parts_en.append("OIS ≈ %s participants (%s); the analysis has %s, which %s it." % (
            ois_total, ois_txt["en"], _int_text(participants) or "an unknown number",
            "exceeds" if ois_met else ("does not reach" if ois_met is False else "cannot be compared with")))
    elif m in BINARY:
        parts_hu.append("OIS nem számolható (hiányzó kontrollkockázat).")
        parts_en.append("OIS cannot be computed (no control risk).")
    elif m == "MD" and not mid:
        parts_hu.append("OIS-hez MID kell; addig a durva ökölszabály ≈ %d résztvevő." % RULE_OF_THUMB_PARTICIPANTS)
        parts_en.append("The OIS needs a MID; until then the rough rule of thumb is ≈ %d participants."
                        % RULE_OF_THUMB_PARTICIPANTS)
    if events is not None:
        parts_hu.append("Események összesen: %s%s." % (_int_text(events), " (< 300: kevés)" if events <
                                                                            RULE_OF_THUMB_EVENTS else ""))
        parts_en.append("Total events: %s%s." % (_int_text(events), " (< 300: few)" if events < RULE_OF_THUMB_EVENTS
                                                  else ""))
    if not mid and nv is not None:
        flags.append(_flag("no_mid", "Nincs MID: a nullhatáshoz mértünk. Add meg a protokollban rögzített MID-et "
                                     "(ellenőrzött forrással), ha van.",
                           "No MID: compared with no effect. Enter the MID fixed in the protocol (with a verified "
                           "source), if any.", "info"))
    if k is not None and k < 5:
        flags.append(_flag("few_studies", "k = %d: kevés vizsgálatnál a véletlen hatású CI-t ne fogadd el névértéken "
                                          "(a τ² bizonytalan; GRADE-06a)." % k,
                           "k = %d: with few studies do not take the random-effects CI at face value (τ² is "
                           "uncertain; GRADE-06a)." % k, "warning"))
    if m in ("SMD", "COHEN_D", "SMD_GLASS", "SMCC"):
        flags.append(_flag("smd_scale", "SMD: a küszöböt SD-egységben add meg, vagy alakítsd vissza ismert skálára "
                                        "(GRADE-10b).",
                           "SMD: give the threshold in SD units, or convert back to a familiar scale (GRADE-10b).",
                           "info"))
    if nv is None:
        rating, status = None, "human_judgement"
    elif ci_both:
        rating, status = "very serious", "suggested"
    elif crosses_any:
        rating, status = "serious", "borderline" if not mid else "suggested"
        if (events is not None and events < 100) or (ois_met is False and participants is not None and ois_total
                                                     and participants < ois_total / 2):
            flags.append(_flag("very_serious_possible", "Nagyon kevés esemény / résztvevő mellett átlépett küszöb: −2 "
                                                        "is mérlegelhető.",
                               "Threshold crossed with very few events / participants: −2 may be considered.",
                               "warning"))
    elif ois_met is False:
        rating, status = "serious", "suggested"
    elif events is not None and events < RULE_OF_THUMB_EVENTS and ois_met is None:
        rating, status = "not serious", "borderline"
    else:
        rating, status = "not serious", "suggested" if (ois_met or nv is None) else "borderline"
    summary = _both(lambda lang: "; ".join(x for x in (
        (("CI %s %s" if lang == "hu" else "CI %s %s") % (
            ci_txt[lang], ("átlépi: %s" if lang == "hu" else "crosses %s") % thr[lang] if crosses_any else
            (("nem lépi át: %s ✔" if lang == "hu" else "does not cross %s ✔") % thr[lang]))) if nv is not None else
        ("CI %s" % ci_txt[lang]),
        ("OIS %s" % ("✔" if ois_met else "✗")) if ois_met is not None else "",
        (("%s esemény" if lang == "hu" else "%s events") % _int_text(events)) if events is not None else "") if x))
    change = _tr("Ha a CI egyszerre tartalmaz fontos előnyt és elhanyagolható hatást (átlépi a küszöböt), −1; ha "
                 "fontos előnyt és fontos ártalmat is, −2. Szűk CI mellett is −1 jöhet szóba, ha a résztvevők száma "
                 "az OIS alatt marad. Egy előre rögzített MID megadása megváltoztathatja a javaslatot. A "
                 "heterogenitás miatt széles CI-t ne számold kétszer (lásd inkonzisztencia).",
                 "If the CI includes both an important benefit and a negligible effect (crosses the threshold), −1; "
                 "if it includes both important benefit and important harm, −2. Even with a narrow CI, −1 may apply "
                 "when the sample is below the OIS. Entering a prespecified MID may change the suggestion. Do not "
                 "count a CI widened by heterogeneity twice (see inconsistency).")
    because = {"hu": " ".join(parts_hu), "en": " ".join(parts_en)}
    evidence = [_ev("%d%% CI" % round(100 * rd.level), "%d%% CI" % round(100 * rd.level), ci_txt)]
    if nv is not None:
        evidence.append(_ev("Döntési küszöb", "Decision threshold", thr))
    if ois_total is not None:
        evidence.append(_ev("OIS", "OIS", _both(lambda lang: ("%s fő — %s" if lang == "hu" else
                                                              "%s participants — %s") % (ois_total, ois_txt[lang]))))
    if participants is not None:
        evidence.append(_ev("Résztvevők", "Participants", _int_text(participants)))
    if events is not None:
        evidence.append(_ev("Események", "Events", _int_text(events)))
    return advisory, _suggest(rating, P.GRADE_RATINGS.get(rating) if rating else None, status, summary,
                              _why(asks, because, change), flags, KB["imprecision"], evidence)


def recommended_bias_tests(measure, bias):
    """A mértékhez illő kis-vizsgálat teszt(ek) (Sterne 2011; D-S13-008, D-S11-005): bináris → Harbord / Peters (ha
    van 2×2 cella), különben Egger (OR-nál álpozitív lehet); folytonos és egyéb → Egger (SMD-nél csak tájékoztató);
    egycsoportos arány → nincs."""
    if measure in PROPORTION:
        return []
    if measure in BINARY:
        have = [t for t in ("harbord", "peters") if isinstance(bias.get(t), dict)]
        return have or ["egger"]
    return ["egger"]


def _advise_publication_bias(rd):
    m = rd.measure
    b = rd.bias
    k = b.get("k") if b.get("k") is not None else (rd.primary or {}).get("k")
    min_k = b.get("min_k_recommended") or 10
    interpretable = bool(k is not None and k >= min_k and m not in PROPORTION)
    rec = recommended_bias_tests(m, b)

    def p_of(name):
        t = b.get(name)
        return _num(t.get("p")) if isinstance(t, dict) else None
    lfk = b.get("lfk") if isinstance(b.get("lfk"), dict) else {}
    tf = b.get("trimfill") if isinstance(b.get("trimfill"), dict) else {}
    advisory = {"k": k, "min_k": min_k, "tests_interpretable": interpretable, "recommended_tests": rec,
                "egger_p": p_of("egger"), "harbord_p": p_of("harbord"), "peters_p": p_of("peters"),
                "begg_p": p_of("begg"), "lfk": _num(lfk.get("lfk")), "lfk_category": lfk.get("category"),
                "trimfill_k0": tf.get("k0")}
    rec_p = [(t, p_of(t)) for t in rec if p_of(t) is not None]
    tests_missing = interpretable and not rec_p
    signal = interpretable and any(p < EGGER_ALPHA for _t, p in rec_p)
    advisory["asymmetry_signal"] = signal if interpretable else None
    names = {"egger": "Egger", "harbord": "Harbord", "peters": "Peters", "begg": "Begg"}
    tests_txt = " · ".join("%s p %s" % (names[t], _fmt(p, 2)) for t, p in rec_p)
    asks = _tr("Hiányozhatnak-e szisztematikusan vizsgálatok vagy eredmények (pl. a kicsi, „negatív” vizsgálatok nem "
               "jelentek meg)? Az ítélet „nem észlelt” vagy „erősen gyanított”; a keresés teljessége (regiszterek, "
               "szürke irodalom), a regisztrált, de nem közölt vizsgálatok és a kis, ipari finanszírozású vizsgálatok "
               "túlsúlya legalább annyit számít, mint a funnel-tesztek.",
               "Could studies or results be systematically missing (e.g. small 'negative' studies never published)? "
               "The judgement is 'undetected' or 'strongly suspected'; the completeness of the search (registries, "
               "grey literature), registered but unpublished trials and a preponderance of small industry-funded "
               "studies matter at least as much as funnel tests.")
    convention = _tr("Ha „gyanított”-at (suspected) választasz, az ítélet FELOLDATLAN marad, és a GRADE nem "
                     "rögzíthető, amíg nem döntesz 0 és −1 között indoklással; az „erősen gyanított” −1 (11. döntés, "
                     "4. pont; X019).",
                     "If you choose 'suspected', the judgement stays UNRESOLVED and the GRADE cannot be recorded until "
                     "you decide between 0 and −1 with a rationale; 'strongly suspected' is −1 (decision 11.4; "
                     "X019).")
    flags = [_flag("suspected_unresolved", convention["hu"], convention["en"], "info")]
    if m in ("SMD", "COHEN_D", "SMD_GLASS", "SMCC"):
        flags.append(_flag("smd_egger", "SMD-nél a klasszikus Egger-teszt álpozitív lehet (D-S11-005): csak "
                                        "tájékoztató.",
                           "With SMD the classic Egger test may be falsely positive (D-S11-005): informative only.",
                           "warning"))
    if m in BINARY and rec == ["egger"]:
        flags.append(_flag("no_cells", "Bináris kimenet 2×2 cellák nélkül: Harbord / Peters nem futott; az Egger-teszt "
                                       "OR-nál álpozitív lehet.",
                           "Binary outcome without 2×2 cells: Harbord / Peters did not run; Egger may be falsely "
                           "positive for OR.", "warning"))
    if advisory["lfk"] is not None and abs(advisory["lfk"]) > 2:
        flags.append(_flag("lfk_heuristic", "LFK %s (%s): heurisztika, nem döntési alap (GRADE-07a)." % (
            _fmt(advisory["lfk"], 2), lfk.get("category") or ""),
            "LFK %s: a heuristic, not a basis for the judgement (GRADE-07a)." % _fmt(advisory["lfk"], 2, "en"),
            "info"))
    if tf.get("k0"):
        flags.append(_flag("trimfill", "Trim-and-fill: %s kitöltött vizsgálat — érzékenységi jelzés, nem korrigált "
                                       "eredmény (GRADE-07a)." % tf.get("k0"),
                           "Trim-and-fill: %s filled studies — a sensitivity signal, not a corrected result "
                           "(GRADE-07a)." % tf.get("k0"), "info"))
    if tests_missing:
        flags.append(_flag("tests_missing", "A mértékhez illő teszt nem futott (pl. hibás vagy hiányzó cellák): a "
                                            "funnel plotot és a keresés teljességét nézd.",
                           "The test suited to the measure did not run (e.g. invalid or missing cells): look at the "
                           "funnel plot and the completeness of the search.", "warning"))
    off = [t for t in ("egger", "begg") if t not in rec and p_of(t) is not None and p_of(t) < EGGER_ALPHA]
    if m in PROPORTION:
        because = _tr("Egycsoportos aránynál a funnel-alapú tesztek nem ajánlottak (D-S11-008): a keresés teljessége "
                      "és a regiszterek alapján ítélj.",
                      "Funnel-based tests are not recommended for single-group proportions (D-S11-008): judge from the "
                      "completeness of the search and the registries.")
        rating, step, status = None, None, "human_judgement"
        summary = _tr("tesztek nem ajánlottak — emberi ítélet", "tests not recommended — human judgement")
    elif not interpretable:
        because = _both(lambda lang: (
            "k = %s < %s: a funnel-aszimmetria tesztek nem értelmezhetők (Sterne 2011), és a szimmetrikus kép sem "
            "zárja ki a torzítást. Ítélj a keresés teljessége, a regiszterek és a finanszírozás alapján (D-S11-018)."
            % (k, min_k) if lang == "hu" else
            "k = %s < %s: funnel asymmetry tests are not interpretable (Sterne 2011), and a symmetric plot does not "
            "rule out bias. Judge from the completeness of the search, registries and funding (D-S11-018)."
            % (k, min_k)))
        rating, step, status = None, None, "human_judgement"
        summary = _both(lambda lang: ("k = %s < %s: tesztek nem értelmezhetők" if lang == "hu" else
                                      "k = %s < %s: tests not interpretable") % (k, min_k))
    else:
        because = _both(lambda lang: " ".join(x for x in (
            ("k = %s ≥ %s; a mértékhez illő teszt(ek): %s." % (k, min_k, tests_txt or "–") if lang == "hu" else
             "k = %s ≥ %s; tests suited to the measure: %s." % (k, min_k, tests_txt or "–")),
            (("Az ajánlott teszt aszimmetriát jelez (p < %s): ez kis-vizsgálat hatás, nem bizonyított publikációs "
              "torzítás — heterogenitás vagy a kis vizsgálatok nagyobb torzítási kockázata is okozhatja." if lang ==
              "hu" else
              "The recommended test signals asymmetry (p < %s): this is a small-study effect, not proven publication "
              "bias — heterogeneity or a higher risk of bias in small studies can also cause it.") % _fmt(EGGER_ALPHA,
                                                                                                          2, lang))
            if signal else
            ("Az ajánlott teszt nem jelez aszimmetriát; ez önmagában nem zárja ki a torzítást." if lang == "hu" else
             "The recommended test does not signal asymmetry; this alone does not rule out bias."),
            (("Nem ajánlott teszt jelez (%s) — csak tájékoztató." if lang == "hu" else
              "A non-recommended test signals (%s) — informative only.") % ", ".join(names[t] for t in off))
            if off else "") if x))
        if signal:
            rating, step, status = "suspected", None, "borderline"
            summary = _both(lambda lang: ("aszimmetria-jel (%s) → gyanított: FELOLDATLAN, dönts 0 / −1" if lang == "hu"
                                          else "asymmetry signal (%s) → suspected: UNRESOLVED, decide 0 / −1")
                            % tests_txt)
        else:
            rating, step = "undetected", 0
            status = "borderline" if (off or tests_missing or (advisory["lfk"] is not None and
                                                               abs(advisory["lfk"]) > 2)) else "suggested"
            summary = _both(lambda lang: ("%s — nem jelez" if lang == "hu" else "%s — no signal") % (tests_txt or "–"))
    change = _tr("Regisztrált, de nem közölt vizsgálatok, hiányos keresés vagy több egybevágó jel (pl. "
                 "kontúr-funnelben a nem szignifikáns régióból hiányzó kis vizsgálatok) a „gyanított” / „erősen "
                 "gyanított” felé visz; teljes keresés regiszterekkel a „nem észlelt” felé. A fail-safe N és a "
                 "trim-and-fill korrigált becslése nem döntési alap.",
                 "Registered but unpublished trials, an incomplete search or several concordant signals (e.g. small "
                 "studies missing from the non-significant region of a contour funnel) point towards "
                 "'suspected' / 'strongly suspected'; a complete search including registries towards 'undetected'. "
                 "Fail-safe N and the trim-and-fill adjusted estimate are not a basis for the judgement.")
    why = _why(asks, {lang: because[lang] + " " + convention[lang] for lang in ("hu", "en")}, change)
    evidence = [_ev("k", "k", _both(lambda lang: "%s (%s)" % (k, ("tesztek értelmezhetők" if interpretable else
                                                                   "tesztek nem értelmezhetők") if lang == "hu" else
                                                              ("tests interpretable" if interpretable else
                                                               "tests not interpretable"))))]
    for t, p in rec_p:
        evidence.append(_ev(names[t], names[t], "p %s" % _fmt(p, 2)))
    for t in ("egger", "begg"):
        if t not in rec and p_of(t) is not None and m not in PROPORTION:
            evidence.append(_ev("%s (tájékoztató)" % names[t], "%s (informative)" % names[t],
                                "p %s" % _fmt(p_of(t), 2)))
    if advisory["lfk"] is not None:
        evidence.append(_ev("LFK (heurisztika)", "LFK (heuristic)", _both(lambda lang: _fmt(advisory["lfk"], 2, lang))))
    return advisory, _suggest(rating, step, status, summary, why, flags, KB["publication_bias"], evidence)


def _advise_upgrades(rd, start, design=None):
    """Felminősítési tanács (GRADE-08; D-S13-009) felminősítésenként: {suggested, candidate_step, summary, evidence,
    why, flags, kb_refs}. Csak a nagy hatás számolható (RR / OR: > 2 vagy < 0,5 → +1; > 5 vagy < 0,2 → +2); a
    dózis–válasz és az ellentétes zavaró tényezők emberi ítéletek. Javaslat (suggested) csak nem randomizált
    bizonyítéknál lehet — az ELRENDEZÉS dönt (design: RCT | NRSI | observational | mixed), nem a kiindulás: a
    ROBINS-I-gyel értékelt NRSI magasról indul, mégis felminősíthető (GRADE 18; methodology:M9). Ismeretlen
    elrendezésnél a kiindulás csak jelölt feltételezés (alacsony → nem randomizált, magas → RCT)."""
    m = rd.measure
    est, lo, hi = rd.bt
    design = P.grade_design(design)
    assumed = design not in P.GRADE_DESIGNS
    obs = (start == "low") if assumed else design in ("NRSI", "observational")
    cand, ci_beyond = None, None
    if m in ("RR", "OR") and None not in (est, lo, hi) and est > 0 and lo > 0:
        far = max(est, 1.0 / est)
        cand = 2 if far > 5 else (1 if far > 2 else 0)
        ci_beyond = (hi < 0.5 or lo > 2.0)
    large_asks = _tr("Olyan nagy-e a hatás, hogy zavaró tényezők valószínűleg nem magyarázzák (megfigyeléses "
                     "vizsgálatoknál)?",
                     "Is the effect so large that confounding is unlikely to explain it (observational studies)?")
    if cand:
        because = _both(lambda lang: (
            "%s %s: a relatív hatás %s, ezért +%d mérlegelhető%s." % (
                m, _fmt(est, 2), "nagyon nagy (> 5 vagy < 0.2)" if cand == 2 else "nagy (> 2 vagy < 0.5)", cand,
                "" if obs else " — de RCT-%snál a felminősítés ritkán indokolt" % ("kiindulás" if assumed else
                                                                                   "bizonyíték"))
            if lang == "hu" else
            "%s %s: the relative effect is %s, so +%d may be considered%s." % (
                m, _fmt(est, 2, "en"), "very large (> 5 or < 0.2)" if cand == 2 else "large (> 2 or < 0.5)", cand,
                "" if obs else " — but rating up is rarely justified %s" % (
                    "when starting from RCTs" if assumed else "for RCT evidence"))))
    elif cand == 0:
        because = _tr("A relatív hatás nem éri el a nagy hatás tájékoztató határát (RR > 2 vagy < 0.5).",
                      "The relative effect does not reach the indicative large-effect threshold (RR > 2 or < 0.5).")
    else:
        because = _tr("Ennél a mértéknél a nagy hatás nem számolható gépiesen: emberi ítélet.",
                      "For this measure a large effect cannot be computed mechanically: human judgement.")
    large = {"suggested": bool(cand) and obs and bool(ci_beyond), "candidate_step": cand,
             "summary": _both(lambda lang: ("+%d mérlegelhető" % cand if lang == "hu" else "+%d may be considered"
                                            % cand) if cand else ("—" if cand == 0 else
                                                                  ("emberi ítélet" if lang == "hu" else
                                                                   "human judgement"))),
             "evidence": ([_ev("Relatív hatás", "Relative effect", PL.display_text(est, lo, hi)),
                           _ev("A CI is a határon túl", "CI also beyond the threshold",
                               _tr("igen" if ci_beyond else "nem", "yes" if ci_beyond else "no"))]
                          if cand is not None else []),
             "why": _why(large_asks, because,
                         _tr("Plauzibilis zavaró tényező, érdemi leminősítés vagy a határt el nem érő CI ellene szól; "
                             "konzisztens, nagy hatás több vizsgálatban mellette (GRADE-08a).",
                             "Plausible confounding, important downgrades or a CI not reaching the threshold argue "
                             "against it; a consistent large effect across studies argues for it (GRADE-08a).")),
             "flags": [], "kb_refs": ["GRADE-08", "GRADE-08a", "D-S13-009"]}
    if obs:
        large["why"]["uncertain"] = _tr("Az elrendezés nincs megadva: a kiindulásból (alacsony) feltételezve nem "
                                        "randomizált.", "Design not given: assumed non-randomised from the (low) "
                                        "start.") if assumed else None
    elif design == "mixed":
        large["why"]["uncertain"] = _tr("Vegyes elrendezés (RCT és nem randomizált): a felminősítést külön, a nem "
                                        "randomizált bizonyítékra mérlegeld.", "Mixed designs (randomised and non-"
                                        "randomised): consider rating up separately, for the non-randomised evidence.")
    else:
        large["why"]["uncertain"] = _tr("RCT-kiindulás: felminősítés ritkán indokolt.",
                                        "RCT start: rating up is rarely justified.") if assumed else \
            _tr("Randomizált vizsgálatok: felminősítés ritkán indokolt.",
                "Randomised trials: rating up is rarely justified.")
    large["design"] = design if not assumed else None
    human = _tr("emberi ítélet", "human judgement")
    dose = {"suggested": False, "candidate_step": None, "summary": human, "evidence": [], "flags": [],
            "why": _why(_tr("Változik-e hihetően és következetesen a hatás a dózissal vagy az expozíció mértékével?",
                            "Does the effect change plausibly and consistently with dose or exposure?"),
                        _tr("A motor nem végez dózis–hatás metaanalízist; a vizsgálatok átlagdózisára futtatott "
                            "meta-regresszió erre nem elegendő.",
                            "The engine does not run dose–response meta-analysis; a meta-regression on mean study "
                            "doses is not sufficient."),
                        _tr("Megfelelő módszerrel (pl. dosresmeta) kimutatott, konzisztens grádiens: +1 (GRADE-08b).",
                            "A consistent gradient shown with an appropriate method (e.g. dosresmeta): +1 "
                            "(GRADE-08b).")),
            "kb_refs": ["GRADE-08", "GRADE-08b", "D-S13-009"]}
    opp = {"suggested": False, "candidate_step": None, "summary": human, "evidence": [], "flags": [],
           "why": _why(_tr("A maradék zavaró tényezők a megfigyelt hatás CSÖKKENTÉSE irányába hatnának-e?",
                           "Would residual confounding act to REDUCE the observed effect?"),
                       _tr("Ehhez a zavaró tényezők és várható irányuk konkrét ismerete kell (ROBINS-I zavaró "
                           "tényezők doménje).",
                           "This needs specific knowledge of the confounders and their expected direction (ROBINS-I "
                           "confounding domain)."),
                       _tr("Ha minden plauzibilis zavaró tényező a hatás ellen dolgozik, +1 (GRADE-08c).",
                           "If every plausible confounder works against the effect, +1 (GRADE-08c).")),
           "kb_refs": ["GRADE-08", "GRADE-08c", "D-S13-009"]}
    return {"large_effect": large, "dose_response": dose, "opposing_confounding": opp}


# ------------------------------------------------------------------ advice()
def _project_outcome(project_dir, oid):
    if project_dir is None or oid is None:
        return None
    try:
        meta = P.load_project_meta(project_dir)
    except (ValueError, OSError):
        return None
    for o in (meta or {}).get("outcomes") or []:
        if isinstance(o, dict) and o.get("id") == oid:
            return o
    return None


def _sanitize_id(s):
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))[:64]
    if not s or not re.match(r"[A-Za-z0-9_]", s[0]):
        s = ("o_" + s)[:64]
    return s


def _outcome_id(rd, given, project_dir):
    if given is not None:
        if not isinstance(given, str) or not _OUTCOME_ID.match(given):
            raise ValueError("érvénytelen kimenet-azonosító: %r" % (given,))
        return given
    if rd.folder:
        parent = os.path.dirname(rd.folder)
        if os.path.basename(os.path.dirname(parent)) == "05_elemzes":
            return _sanitize_id(os.path.basename(parent))
        if os.path.basename(parent) == "05_elemzes":
            return _sanitize_id(os.path.basename(rd.folder))
    if rd.spec.get("name"):
        return _sanitize_id(rd.spec["name"])
    path = (rd.results.get("input") or {}).get("path")
    if path:
        return _sanitize_id(os.path.splitext(os.path.basename(str(path)))[0])
    return "kimenet"


def run_summary(rd):
    """A futás GRADE-hez rögzített adatai (k, résztvevők, mérték, hatás-szöveg) — X007 ezekkel vet össze."""
    rd = load_run(rd)
    disp = rd.display_text
    m = rd.measure
    return {"k": (rd.primary or {}).get("k"), "participants": _count(rd.totals.get("participants")), "measure": m,
            "display_text": disp,
            "effect_text": {lang: ("%s %s" % (m, disp[lang])).strip() for lang in ("hu", "en")} if disp else None,
            "data_sha256": rd.data_sha256 if isinstance(rd.data_sha256, str) else None,
            "spec_name": rd.spec.get("name")}


def advice(run, rob_by_row=None, mid=None, project_dir=None, outcome_id=None, start=None, importance=None,
           rob_child=None, run_id=None, ois_rrr=None, alpha=0.05, power=0.8, design=None, rob_tool=None):
    """GRADE-tanács egy (commit-)futásra → szk.ma.grade/v1 PISZKOZAT: minden domén ítélete null; doménenként
    'advisory' (számok) és 'suggestion' (javasolt ítélet és lépés; status: suggested | borderline | human_judgement |
    not_applicable | insufficient_data; concern; summary; evidence [{label, text}]; why — a „Miért?” panel: asks (mit
    kérdez), because (miért ez a javaslat), change (mi változtatná meg), uncertain (határeset / hiányzó adat); flags;
    kb_refs). A felminősítési tanács az advice.upgrades-ben (felminősítésenként). Soha nem végleges: az ítéletet ember
    hozza (projekt.save_grade_doc).

    rob_by_row: {row_uid | study_id | címke: RoB-ítélet} vagy lista a vizsgálatok sorrendjében (alapból a futás saját
    rob oszlopa). mid: lásd parse_mid. project_dir: a „magas RoB nélkül” gyermek-futás felderítéséhez (vagy
    rob_child: a gyermek-futás bármely run-alakban) és a ma-projekt.json kimenet-adataihoz (grade_start, critical).
    ois_rrr: az OIS relatív kockázatcsökkenése (alap: 25%, vagy a MID-ből). design: a bizonyíték elrendezése (RCT |
    NRSI | observational | mixed; alap: a studies.json-ból — a felminősítési tanács erre épül, nem a kiindulásra).
    rob_tool: a vizsgálatonkénti RoB-eszköz (alap: a projektből; ROBINS-I / ROBINS-E → GRADE 18 szerinti tanács)."""
    rd = load_run(run, project_dir)
    if rd.primary is None:
        raise ValueError("A futásban nincs összesített eredmény (k = 0): GRADE-tanács nem adható.")
    if run_id is not None and run_id != rd.run_id:
        raise ValueError("a megadott run_id (%s) nem a futásé (%s)" % (run_id, rd.run_id))
    m = rd.measure
    mid_n = parse_mid(mid, m)
    oid = _outcome_id(rd, outcome_id, project_dir)
    meta_o = _project_outcome(project_dir, oid) or {}
    if start is None and meta_o.get("grade_start") in ("high", "low"):
        start = meta_o["grade_start"]
    if start not in ("high", "low", None):
        raise ValueError("start: high, low vagy None lehet")
    if importance is None and meta_o.get("critical") is True:
        importance = "critical"
    rows = _apply_rob(_study_rows(rd), rob_by_row)
    thresholds = _thresholds(m, mid_n)
    child = load_run(rob_child, project_dir) if rob_child is not None else _find_rob_child(rd, project_dir)
    tool_source = "parameter" if rob_tool is not None else None
    if rob_tool is None:
        rob_tool = _rob_tool(project_dir, oid, meta_o)
        tool_source = "project" if rob_tool else None
    if rob_tool is None and _robins_vocabulary(rows):
        rob_tool, tool_source = "robins-i", "vocabulary"
    rob_tool = rob_tool.strip().lower() if isinstance(rob_tool, str) and rob_tool.strip() else None
    design = P.grade_design(design)
    if design is None and project_dir is not None:
        design = _studies_design(rd, project_dir)
    if design is None and rob_tool in ROBINS_TOOLS:
        design = "NRSI" if rob_tool == "robins-i" else "observational"
    if design is not None and design not in P.GRADE_DESIGNS:
        raise ValueError("design: %s vagy None lehet" % " | ".join(P.GRADE_DESIGNS))
    adv, sug = {}, {}
    adv["risk_of_bias"], sug["risk_of_bias"] = _advise_rob(rd, rows, child, thresholds, project_dir, start, rob_tool)
    adv["risk_of_bias"]["rob_tool_source"] = tool_source
    if tool_source == "vocabulary":
        sug["risk_of_bias"]["flags"].append(_flag(
            "rob_tool_from_vocabulary",
            "A rob oszlop ROBINS-I-szókincset használ (moderate / serious / critical): az eszközt ebből feltételeztem; "
            "add meg a projektben (appraisal_tools), ha nem így van.",
            "The rob column uses ROBINS-I vocabulary (moderate / serious / critical): the tool was assumed from it; "
            "declare it in the project (appraisal_tools) if this is wrong.", "info"))
    adv["inconsistency"], sug["inconsistency"] = _advise_inconsistency(rd, rows, thresholds, mid_n)
    adv["indirectness"], sug["indirectness"] = _advise_indirectness()
    adv["imprecision"], sug["imprecision"] = _advise_imprecision(rd, mid_n, thresholds, ois_rrr, alpha, power)
    adv["publication_bias"], sug["publication_bias"] = _advise_publication_bias(rd)
    domains = {}
    for d in DOMAINS:
        domains[d] = {"rating": None, "step": None, "rationale": None, "advisory": adv[d], "suggestion": sug[d]}
    domains["publication_bias"]["status"] = "open"
    notes = [_tr("Csak javaslat: a motor nem dönt. Minden domén ítéletét te választod ki és indoklod; a bizonyosság a "
                 "kiindulásból és a te lépéseidből adódik.",
                 "Advice only: the engine does not decide. You choose and justify every domain judgement; the "
                 "certainty follows from the starting level and your steps.")]
    if start is None:
        notes.append(_tr("Add meg a kiindulást: randomizált vizsgálatok → magas; megfigyeléses → alacsony (GRADE-02); "
                         "ROBINS-I-gyel értékelt nem randomizált vizsgálatok → magas is lehet (%s)." % GRADE18,
                         "Set the starting level: randomised trials → high; observational → low (GRADE-02); "
                         "non-randomised studies assessed with ROBINS-I → may start high (%s)." % GRADE18))
    if rd.run_id is None:
        notes.append(_tr("Ez nem commit-futás: a GRADE csak rögzített (commit) futásra hivatkozhat (terv 2.6).",
                         "This is not a commit run: GRADE may only refer to a committed run (design 2.6)."))
    doc = {
        "schema": GRADE_SCHEMA, "outcome_id": oid, "importance": importance, "run_id": rd.run_id,
        "start": start, "start_reason": None, "design": design, "domains": domains,
        "upgrades": {u: False for u in P.GRADE_UPGRADES}, "certainty": None, "consistency_warning": None,
        "validator_rollup": None, "status": "draft", "mid": mid_n, "run_summary": run_summary(rd),
        "advice": {"engine": {"name": "metaelemzes", "version": __version__}, "notes": notes,
                   "upgrades": _advise_upgrades(rd, start, design),
                   "kb_refs": sorted({r for key in DOMAINS + ("upgrades", "general") for r in KB[key]}),
                   "expected_certainty_if_suggested": _expected_if(start, sug)},
    }
    norm, errors = P.validate_grade_doc(doc)
    if errors:                                           # belső hiba: a piszkozatnak mindig érvényesnek kell lennie
        raise AssertionError("advice: érvénytelen piszkozat: %s" % "; ".join(errors))
    return norm


def _expected_if(start, sug):
    """A javaslatok szerinti szint (csak tájékoztató; None, ha a kiindulás vagy egy javaslat hiányzik)."""
    steps = [sug[d].get("step") for d in DOMAINS]
    return P.grade_arithmetic(start, steps, 0)["certainty"]      # a motor egyetlen GRADE-szabálya


# ------------------------------------------------------------------ abszolút hatás, SoF
def absolute_effect(measure, rel, assumed_risk):
    """Abszolút hatás /1000 egy feltételezett alapkockázatra (a totals.absolute_per_1000 általánosítása; GRADE-10a,
    D-S13-012). rel: [becslés, alsó, felső] a megjelenítési skálán (RR, OR vagy RD); assumed_risk: (0; 1).
    RR: kockázat = ACR·RR; OR: ACR·OR / (1 − ACR + ACR·OR); RD: ACR + RD (a különbség maga az RD, az alapkockázattól
    független). → {risk_per_1000: [3], difference_per_1000: [3], incompatible: bool} — a 0 alá vagy 1 fölé eső
    kockázat None (incompatible: True)."""
    acr = _num(assumed_risk)
    if acr is None or not 0 < acr < 1:
        raise ValueError("az alapkockázat a (0; 1) tartományba essen (/1000-ben: 0 < x < 1000)")
    if measure not in BINARY:
        raise ValueError("abszolút hatás csak bináris mértéknél (RR, OR, RD) számolható")
    risks, diffs, bad = [], [], False
    for x in rel:
        x = _num(x)
        if x is None or (measure in ("RR", "OR") and x < 0):
            risks.append(None)
            diffs.append(None)
            continue
        r = _risk(measure, x, acr)
        if not 0.0 <= r <= 1.0:
            bad = True
            risks.append(None)
            diffs.append(1000.0 * x if measure == "RD" else None)
            continue
        risks.append(1000.0 * r)
        diffs.append(1000.0 * (x if measure == "RD" else r - acr))
    return {"risk_per_1000": risks, "difference_per_1000": diffs, "incompatible": bad}


def _nd_per_1000(vals):
    vals = [abs(v) for v in vals if v is not None]
    return 0 if not vals or max(vals) >= 1 else 1


def _abs_words(v, nd, lang):
    s = _fmt(abs(v), nd, lang)
    if lang == "en":
        return "%s %s" % (s, "fewer" if v < 0 else "more")
    return "%s %s" % (s, "kevesebb" if v < 0 else "több")


def _abs_text(diff, lang):
    if None in diff:
        return "nem számolható (az alapkockázattal nem egyeztethető)" if lang == "hu" else \
            "not computable (incompatible with the assumed risk)"
    nd = _nd_per_1000(diff)
    est, lo, hi = diff
    if lang == "en":
        return "%s per 1,000 (from %s to %s)" % (_abs_words(est, nd, lang), _abs_words(lo, nd, lang),
                                                 _abs_words(hi, nd, lang))
    lo_w, hi_w = _abs_words(lo, nd, lang), _abs_words(hi, nd, lang)
    return "1000 főre %s (%stől %sig)" % (_abs_words(est, nd, lang), lo_w, hi_w)


def _per_1000_text(v, lang, ci=None):
    nd = _nd_per_1000([v] + list(ci or []))
    if ci and None not in ci:
        return ("%s per 1,000 (%s to %s)" % (_fmt(v, nd, lang), _fmt(ci[0], nd, lang), _fmt(ci[1], nd, lang))
                if lang == "en" else "1000 főre %s (%s–%s)" % (_fmt(v, nd, lang), _fmt(ci[0], nd, lang),
                                                                _fmt(ci[1], nd, lang)))
    return "%s per 1,000" % _fmt(v, nd, lang) if lang == "en" else "1000 főre %s" % _fmt(v, nd, lang)


def _parse_risks(assumed_risks, rd, warnings):
    """[{label, kind: control_pool | external, source, per_1000}] — a kontroll-pool a motorból (Σe2/Σn2)."""
    entries = [{"label": None, "source": "control_pool"}] if assumed_risks is None else assumed_risks
    if isinstance(entries, dict):
        entries = [entries]
    out = []
    for i, e in enumerate(entries):
        if isinstance(e, (int, float, str)) and not isinstance(e, bool):
            e = {"per_1000": e}
        if not isinstance(e, dict):
            raise ValueError("assumed_risks[%d]: {label, per_1000, source} objektum legyen" % i)
        src = e.get("source")
        kind = e.get("kind")
        pool = kind == "control_pool" or (isinstance(src, str) and src.strip().lower() in POOL_WORDS) or \
            (e.get("per_1000") in (None, "") and kind is None and src is None)
        cite = e.get("citation") or e.get("reference") or e.get("note")
        if not pool and isinstance(src, str) and src.strip() and src.strip().lower() != "external":
            cite = cite or src.strip()
        if pool:
            cr = _num(rd.totals.get("control_risk"))
            if cr is None or not 0 < cr < 1:
                warnings.append(_tr("A kontroll-pool alapkockázat nem számolható (nincs Σe2/Σn2, vagy 0): kihagyva.",
                                    "The control-pool baseline risk cannot be computed (no Σe2/Σn2, or 0): "
                                    "skipped."))
                continue
            label = _i18n_in(e.get("label")) if e.get("label") else _tr("kontroll-pool", "control-arm pool")
            out.append({"label": label, "kind": "control_pool", "source": "totals.control_risk (Σe2/Σn2)",
                        "per_1000": 1000.0 * cr})
            continue
        raw = e.get("per_1000")
        try:
            v = _parse_num(raw)
        except (ValueError, TypeError):
            raise ValueError("assumed_risks[%d].per_1000: szám kell (pl. 12 vagy 12,5)" % i) from None
        if not 0 < v < 1000:
            raise ValueError("assumed_risks[%d].per_1000: 0 és 1000 közé essen (kizárólag)" % i)
        out.append({"label": _i18n_in(e.get("label")) if e.get("label") else _tr("külső alapkockázat",
                                                                                 "external baseline risk"),
                    "kind": "external",
                    "source": cite if isinstance(cite, str) and cite.strip() else None, "per_1000": v})
    return out


def _direction(rd):
    est = rd.bt[0]
    nv = null_value(rd.measure)
    if est is None or nv is None:
        return None
    return "reduce" if est < nv else ("increase" if est > nv else "none")


def statement(certainty, direction, important=True, outcome=None, intervention=None):
    """GRADE-megfogalmazás (GRADE-11, D-S13-016) {hu, en}. direction: reduce | increase | none; important=False:
    „kevés vagy semmi különbség” (csak ha a pontbecslés a MID-en belül van). None, ha nincs bizonyosság."""
    certainty = P.grade_token(certainty)
    if certainty not in LEVELS or direction is None:
        return None
    o = outcome or {}
    o_en, o_hu = o.get("en") or "the outcome", o.get("hu") or "a kimenet"
    i_en = intervention.get("en") if isinstance(intervention, dict) else (intervention or "the intervention")
    i_hu = intervention.get("hu") if isinstance(intervention, dict) else (intervention or "a beavatkozás")
    if certainty == "very low":
        en = "The evidence is very uncertain about the effect of %s on %s." % (i_en, o_en)
        hu = "%s: a bizonyíték nagyon bizonytalan (%s hatása)." % (o_hu[:1].upper() + o_hu[1:], i_hu)
        return {"hu": hu, "en": en[:1].upper() + en[1:]}
    trivial = direction == "none" or not important
    verbs_en = {"high": ("reduces", "increases", "results in little to no difference in"),
                "moderate": ("probably reduces", "probably increases",
                             "probably results in little to no difference in"),
                "low": ("may reduce", "may increase", "may result in little to no difference in")}
    verbs_hu = {"high": ("csökkenti", "növeli", "kevés vagy semmi különbséget eredményez"),
                "moderate": ("valószínűleg csökkenti", "valószínűleg növeli",
                             "valószínűleg kevés vagy semmi különbséget eredményez"),
                "low": ("csökkentheti", "növelheti", "kevés vagy semmi különbséget eredményezhet")}
    idx = 2 if trivial else (0 if direction == "reduce" else 1)
    en = "%s %s %s." % (i_en, verbs_en[certainty][idx], o_en)
    hu = "%s: %s %s." % (o_hu[:1].upper() + o_hu[1:], i_hu, verbs_hu[certainty][idx])
    return {"hu": hu, "en": en[:1].upper() + en[1:]}


def certainty_symbols(certainty):
    return SYMBOLS.get(P.grade_token(certainty))


def _i18n_in(v):
    if v is None:
        return None
    if isinstance(v, str):
        return {"hu": v, "en": v}
    if isinstance(v, dict) and isinstance(v.get("hu"), str) and isinstance(v.get("en"), str):
        return {"hu": v["hu"], "en": v["en"]}
    raise ValueError("szöveg vagy {hu, en} pár kell")


_EFFECT_KIND = {"MD": "mean_difference", "SMD": "standardised_mean_difference", "COHEN_D":
                "standardised_mean_difference", "SMD_GLASS": "standardised_mean_difference", "ROM": "ratio_of_means",
                "MC": "mean_change", "SMCC": "standardised_mean_change", "RD": "risk_difference", "COR": "correlation",
                "ZCOR": "correlation", "GEN": "generic"}


def _effect_text(m, bt, lang, level):
    est, lo, hi = bt
    if None in bt:
        return "–"
    lv = "%d%% CI" % round(100 * level)
    if m == "ROM":
        p = [100.0 * (x - 1.0) for x in bt]
        nd = 0 if max(abs(x) for x in p) >= 10 else 1

        def w(x):
            return "%s%% %s" % (_fmt(abs(x), nd, lang), ("lower" if x < 0 else "higher") if lang == "en" else
                                ("alacsonyabb" if x < 0 else "magasabb"))
        return ("%s (from %s to %s)" % (w(p[0]), w(p[1]), w(p[2])) if lang == "en" else
                "%s (%stól %sig)" % (w(p[0]), w(p[1]), w(p[2])))
    if m in ("MD", "SMD", "COHEN_D", "SMD_GLASS", "MC", "SMCC", "GEN"):
        nd = PL.decimals(bt)
        lab = {"COHEN_D": "SMD", "SMD_GLASS": "SMD", "SMCC": "SMC", "GEN": ""}.get(m, m)

        def w(x):
            return "%s %s" % (_fmt(abs(x), nd, lang), ("lower" if x < 0 else "higher") if lang == "en" else
                              ("alacsonyabb" if x < 0 else "magasabb"))
        if lang == "en":
            return ("%s %s (from %s to %s)" % (lab, w(est), w(lo), w(hi))).strip()
        return ("%s %s (%stól %sig)" % (lab, w(est), w(lo), w(hi))).strip()
    nd = PL.decimals(bt)
    lab = {"RD": "RD", "COR": "r", "ZCOR": "r"}.get(m, "")
    if lang == "en":
        return ("%s %s (%s %s to %s)" % (lab, _fmt(est, nd, lang), lv, _fmt(lo, nd, lang), _fmt(hi, nd, lang))).strip()
    return ("%s %s (%s %s; %s)" % (lab, _fmt(est, nd), lv, _fmt(lo, nd), _fmt(hi, nd))).strip()


def _grade_footnotes(grade):
    """[(domén, {hu, en})] a GRADE-ítélet le- és felminősítéseiből (indoklással) — a SoF lábjegyzetei."""
    out = []
    doms = grade.get("domains") or {}
    for d in DOMAINS:
        dom = doms.get(d) or {}
        step, rating = dom.get("step"), dom.get("rating")
        if rating is None or (step == 0 and not (d == "publication_bias" and rating == "suspected")):
            continue
        hu_d, en_d = P.GRADE_DOMAIN_LABELS[d]
        hu_r, en_r = P.GRADE_RATING_LABELS.get(rating, (rating, rating))
        why = (dom.get("rationale") or "").strip()
        st = "–" if step is None else ("0" if step == 0 else "%s%d" % (MINUS, abs(step)))
        hu = "%s: %s (%s)%s" % (hu_d, st.replace(MINUS, "−"), hu_r, (" — " + why) if why else "")
        en = "%s: %s (%s)%s" % (en_d, st, en_r, (" — " + why) if why else "")
        out.append((d, {"hu": hu, "en": en}))
    ups = P.grade_upgrade_steps(grade)
    if ups:
        labels = {"large_effect": ("nagy hatás", "large effect"), "dose_response": ("dózis–válasz", "dose–response"),
                  "opposing_confounding": ("ellentétes zavaró tényezők", "opposing confounding")}
        det = grade.get("upgrade_details") if isinstance(grade.get("upgrade_details"), dict) else {}
        for k, st in ups.items():
            why = ((det.get(k) or {}).get("rationale") or "").strip() if isinstance(det.get(k), dict) else ""
            hu_l, en_l = labels.get(k, (k, k))
            out.append(("upgrades", {"hu": "Felminősítés: +%d (%s)%s" % (st, hu_l, (" — " + why) if why else ""),
                                     "en": "Rated up: +%d (%s)%s" % (st, en_l, (" — " + why) if why else "")}))
    return out


_BASIS_LABELS = {"draft": (" (piszkozat — a GRADE még nincs rögzítve)", " (draft — GRADE not yet recorded)")}


def _certainty_text(certainty, basis):
    """A bizonyosság-cella {hu, en}; a még nem rögzített GRADE-ből (piszkozat) jövő szint jelölve, a jóvá nem hagyott
    AI-vázlat és a feloldatlan publikációs torzítás szint nélkül, felirattal. (A paraméterként megadott szintet a
    certainty_basis 'parameter' és a figyelmeztetés jelzi; menteni rögzített GRADE nélkül nem lehet.)"""
    if not certainty:
        if basis == "ai_draft":
            return _tr("— AI-vázlat (jóváhagyásra vár)", "— AI draft (awaiting approval)")
        if basis == "unresolved":
            return _tr("— feloldatlan publikációs torzítás (X019)", "— unresolved publication bias (X019)")
        return None
    hu_x, en_x = _BASIS_LABELS.get(basis, ("", ""))
    return {"hu": "%s %s%s" % (SYMBOLS[certainty], LEVEL_LABELS[certainty][0], hu_x),
            "en": "%s %s%s" % (SYMBOLS[certainty], LEVEL_LABELS[certainty][1], en_x)}


def sof_grade_state(grade):
    """A GRADE-dokumentum (szk.ma.grade/v1, normalizált) bizonyossága a SoF számára → {certainty, basis, reason,
    warning}. basis: 'recorded' (rögzített, végleges) | 'draft' (még nem rögzített — ideiglenes, nem menthető) |
    'ai_draft' (jóvá nem hagyott AI-vázlat: a szint NEM kerül a SoF-ba; 6. döntés, contracts:M3) | 'unresolved'
    (feloldatlan „suspected” publikációs torzítás: nincs szint; 4. döntés, X019)."""
    cert = P.grade_token(grade.get("certainty"))
    cert = cert if cert in LEVELS else None
    if P._unapproved_ai(grade):
        return {"certainty": None, "basis": "ai_draft",
                "reason": _tr("jóvá nem hagyott AI-vázlat", "unapproved AI draft"),
                "warning": _tr("A GRADE-ítélet AI-vázlat (jóváhagyásra vár): a bizonyossága nem kerül a SoF-ba, amíg "
                               "ember jóvá nem hagyja és rögzíti (6. döntés).",
                               "The GRADE judgement is an AI draft (awaiting approval): its certainty is not shown in "
                               "the SoF until a person approves and records it (decision 6).")}
    if P.grade_doc_open(grade)["unresolved"]:
        return {"certainty": None, "basis": "unresolved",
                "reason": _tr("feloldatlan „gyanított” publikációs torzítás", "unresolved 'suspected' publication bias"),
                "warning": _tr("A publikációs torzítás „gyanított” ítélete feloldatlan: az ilyen kimenet bizonyossága "
                               "nem kerülhet a SoF-ba (4. döntés; X019).",
                               "Publication bias 'suspected' is unresolved: such an outcome's certainty cannot go into "
                               "the SoF (decision 4; X019).")}
    if grade.get("status") == "recorded" and cert is not None:
        return {"certainty": cert, "basis": "recorded", "reason": None, "warning": None}
    return {"certainty": cert, "basis": "draft" if cert is not None else None,
            "reason": _tr("a GRADE-ítélet még nincs rögzítve", "the GRADE judgement is not recorded yet"),
            "warning": _tr("A GRADE-ítélet még piszkozat (nincs rögzítve): a bizonyosság ideiglenes, a SoF így nem "
                           "menthető (X008).",
                           "The GRADE judgement is still a draft (not recorded): the certainty is provisional and "
                           "this SoF cannot be saved (X008).") if cert is not None else None}


def _design_class(code):
    """studies.json design-kód / szöveg → 'RCT' | 'NRSI' | 'observational' | None."""
    t = str(code or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not t:
        return None
    if re.search(r"(^|_)(non_?randomi[sz]ed|nrsi|quasi|before_?after|interrupted|its|nem_randomiz)", t):
        return "NRSI"
    if re.match(r"^(rct|randomi[sz]ed|randomiz[aá]lt|cluster_?randomi|crossover)", t):
        return "RCT"
    if re.search(r"(cohort|case_?control|cross_?sectional|exposure|observational|kohorsz|eset_?kontroll|"
                 r"megfigyel|keresztmetszeti)", t):
        return "observational"
    return None


def _studies_design(rd, project_dir):
    """A futás vizsgálatainak elrendezése a studies.json-ból (design): mind RCT → 'RCT'; RCT és nem randomizált →
    'mixed'; csak NRSI → 'NRSI'; más nem randomizált → 'observational'; ha egy vizsgálaté ismeretlen: None (nem
    találgatunk; a kiindulásból sem — M9)."""
    try:
        st = _read_json(os.path.join(project_dir, "03_adatok", "studies.json"))
    except (OSError, ValueError):
        return None
    by = {}
    for x in (st or {}).get("studies") or [] if isinstance(st, dict) else []:
        if not isinstance(x, dict):
            continue
        cls = _design_class(x.get("design"))
        for key in (x.get("study_id"), x.get("label")):
            if isinstance(key, str) and key.strip():
                by[key.strip().casefold()] = cls
    rows = _study_rows(rd)
    if not rows or not by:
        return None
    found = set()
    for r in rows:
        cls = next((by[str(k).strip().casefold()] for k in (r.get("study_id"), r.get("label"))
                    if k is not None and str(k).strip().casefold() in by), None)
        if cls is None:
            return None
        found.add(cls)
    if found == {"RCT"}:
        return "RCT"
    if "RCT" in found:
        return "mixed"
    return "NRSI" if found == {"NRSI"} else "observational"


def _studies_text(k, participants, design):
    dsg = {"RCT": ("RCT", "RCT", "RCTs"), "observational": ("megfigyeléses vizsgálat", "observational study",
                                                           "observational studies"),
           "NRSI": ("nem randomizált vizsgálat", "non-randomised study", "non-randomised studies"),
           "mixed": ("vizsgálat (RCT és nem randomizált)", "study (randomised and non-randomised)",
                     "studies (randomised and non-randomised)")}.get(design, ("vizsgálat", "study", "studies"))
    p = _int_text(participants)
    hu = "%s (%d %s)" % (p, k, dsg[0]) if p else "%d %s" % (k, dsg[0])
    en = "%s (%d %s)" % (p, k, dsg[1] if k == 1 else dsg[2]) if p else "%d %s" % (k, dsg[1] if k == 1 else dsg[2])
    return {"hu": hu, "en": en}


def _letters(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(97 + r) + s
    return s


def sof(run, assumed_risks=None, certainty=None, footnotes=None, project_dir=None, outcome_id=None, label=None,
        intervention=None, comparison=None, population=None, setting=None, mid=None, design=None, importance=None,
        grade=None, run_id=None):
    """Summary of Findings sor (szk.ma.sof/v1) egy futásból. Minden szám a motoré:
    résztvevők és k (totals), relatív hatás (back_transformed.estimate_ci; display_text a motoré), alapkockázatonként
    abszolút hatás /1000 CI-vel (absolute_effect), bizonyosság (⊕ szimbólumok), GRADE-megfogalmazás, lábjegyzetek.

    assumed_risks: None → a kontroll-pool (Σe2/Σn2; 4.14); különben [{label, per_1000, source}] — source
    'control_pool' (a pool) vagy a forrás szövege; per_1000 szám vagy szöveg ('12,5'). certainty: szint, None, vagy
    egy szk.ma.grade/v1 dokumentum (grade=…): ekkor a bizonyosság és a lábjegyzetek a domén-indoklásokból jönnek;
    project_dir-rel a mentett GRADE-ítélet is betöltődik, ha nincs megadva. footnotes: további lábjegyzetek (szöveg
    vagy {hu, en}). mid: a megfogalmazáshoz („kevés vagy semmi különbség”); ha nincs, a GRADE-ítélet mid-je."""
    rd = load_run(run, project_dir)
    if rd.primary is None:
        raise ValueError("A futásban nincs összesített eredmény (k = 0): SoF-sor nem készíthető.")
    if run_id is not None and run_id != rd.run_id:
        raise ValueError("a megadott run_id (%s) nem a futásé (%s)" % (run_id, rd.run_id))
    m = rd.measure
    oid = _outcome_id(rd, outcome_id, project_dir)
    warnings = []
    if isinstance(certainty, dict):
        grade, certainty = certainty, None
    certainty = P.grade_token(certainty)              # egy GRADE-szótár: 'very_low' → 'very low' (contracts:m2)
    if certainty not in LEVELS + (None,):
        raise ValueError("certainty: %s vagy None lehet" % " | ".join(LEVELS))
    explicit = certainty
    if grade is None and project_dir is not None:     # a mentett ítélet akkor is, ha a bizonyosság paraméter (M6)
        try:
            grade = P.load_grade_doc(project_dir, oid)
        except ValueError:
            grade = None
    basis = "parameter" if certainty is not None else None
    if grade is not None:
        g, errs = P.validate_grade_doc(grade)
        if errs:
            raise ValueError("a GRADE-dokumentum érvénytelen: %s" % "; ".join(errs))
        grade = g
        state = sof_grade_state(grade)
        basis = state["basis"]
        if explicit is not None and explicit != state["certainty"]:
            # 4. döntés, M6: a SoF bizonyossága a kimenet GRADE-ítéletéé — paraméterrel nem írható felül
            raise ValueError(
                "A megadott bizonyosság (%s) ellentmond a kimenet GRADE-ítéletének (%s: %s): a SoF bizonyossága csak a "
                "rögzített GRADE-ítéleté lehet (4. döntés; X008). Hagyd el a bizonyosság-paramétert, vagy javítsd és "
                "rögzítsd a GRADE-et." % (explicit, P.GRADE_DIR + "/%s.grade.json" % oid,
                                          state["certainty"] or "nincs bizonyosság — %s" % state["reason"]["hu"]))
        certainty = state["certainty"]
        if state["warning"] is not None:
            warnings.append(state["warning"])
        if grade.get("run_id") and rd.run_id and grade["run_id"] != rd.run_id:
            warnings.append(_tr("A GRADE-ítélet másik futásra hivatkozik (%s ≠ %s): nézd át az új számokkal (X007)."
                                % (grade["run_id"], rd.run_id),
                                "The GRADE judgement refers to another run (%s ≠ %s): review it with the new "
                                "numbers (X007)." % (grade["run_id"], rd.run_id)))
        if mid is None and isinstance(grade.get("mid"), dict):
            mid = grade["mid"]
        if design is None and grade.get("design") in P.GRADE_DESIGNS:
            design = grade["design"]                  # NEM a kiindulásból (M9: NRSI ROBINS-I-gyel is magasról indul)
        if importance is None:
            importance = grade.get("importance")
    elif certainty is not None:
        warnings.append(_tr("A bizonyosság paraméterként megadva, rögzített GRADE-ítélet nélkül: a SoF így nem menthető "
                            "(save_sof), és a projekt-audit X008-cal jelzi.",
                            "Certainty given as a parameter without a recorded GRADE judgement: this SoF cannot be "
                            "saved (save_sof), and the project audit flags it (X008)."))
    design = P.grade_design(design)
    if design is None and project_dir is not None:
        design = _studies_design(rd, project_dir)
    meta_o = _project_outcome(project_dir, oid) or {}
    lab = _i18n_in(label) if label is not None else (_i18n_in(meta_o.get("name")) if meta_o.get("name") else
                                                     {"hu": oid, "en": oid})
    mid_n = parse_mid(mid, m) if not (isinstance(mid, dict) and mid.get("scale") == "display") else mid
    bt = rd.bt
    k = int((rd.primary or {}).get("k") or 0)
    participants = _count(rd.totals.get("participants"))
    if participants is None:
        warnings.append(_tr("A résztvevők száma nem minden vizsgálatnál ismert (totals.participants_partial): a SoF "
                            "résztvevő-cellája hiányos.", "The number of participants is not known for every study "
                            "(totals.participants_partial): the SoF participants cell is incomplete."))
    relative = effect = None
    absolute = []
    level = rd.level
    if m in ("RR", "OR", "ROM") and None not in bt:
        disp = PL.display_text(*bt)
        nd = PL.decimals(bt)
        relative = {"measure": m, "estimate": bt[0], "ci_lower": bt[1], "ci_upper": bt[2], "level": level,
                    "display_text": disp,
                    "text": {"hu": "%s %s (%d%% CI %s–%s)" % (m, _fmt(bt[0], nd), round(100 * level), _fmt(bt[1], nd),
                                                              _fmt(bt[2], nd)),
                             "en": "%s %s (%d%% CI %s to %s)" % (m, _fmt(bt[0], nd, "en"), round(100 * level),
                                                                 _fmt(bt[1], nd, "en"), _fmt(bt[2], nd, "en"))}}
    if m not in ("RR", "OR") and None not in bt:
        effect = {"kind": _EFFECT_KIND.get(m, "proportion" if m in PROPORTION else "generic"), "measure": m,
                  "estimate": bt[0], "ci_lower": bt[1], "ci_upper": bt[2], "level": level,
                  "display_text": PL.display_text(*bt), "text": _both(lambda lang: _effect_text(m, bt, lang, level))}
    if m in BINARY and None not in bt:
        for r in _parse_risks(assumed_risks, rd, warnings):
            a = absolute_effect(m, bt, r["per_1000"] / 1000.0)
            item = {"label": r["label"], "kind": r["kind"], "source": r["source"],
                    "assumed_risk_per_1000": r["per_1000"], "risk_per_1000": a["risk_per_1000"],
                    "difference_per_1000": a["difference_per_1000"], "incompatible": a["incompatible"],
                    "baseline_text": _both(lambda lang: _per_1000_text(r["per_1000"], lang)),
                    "risk_text": (_both(lambda lang: _per_1000_text(a["risk_per_1000"][0], lang,
                                                                    a["risk_per_1000"][1:]))
                                  if None not in a["risk_per_1000"] else None),
                    "text": _both(lambda lang: _abs_text(a["difference_per_1000"], lang))}
            if a["incompatible"]:
                warnings.append(_tr("Abszolút hatás (%s): a beavatkozási kockázat 0 alá vagy 1 fölé esne ezzel az "
                                    "alapkockázattal (%s/1000)." % (r["label"]["hu"], _fmt(r["per_1000"], 1)),
                                    "Absolute effect (%s): the intervention risk would fall below 0 or above 1 with "
                                    "this baseline risk (%s per 1,000)." % (r["label"]["en"],
                                                                            _fmt(r["per_1000"], 1, "en"))))
            absolute.append(item)
    elif assumed_risks:
        warnings.append(_tr("Alapkockázat csak bináris kimenetnél (RR, OR, RD) alkalmazható; itt figyelmen kívül "
                            "maradt.", "Baseline risks apply only to binary outcomes (RR, OR, RD); ignored here."))
    notes = []
    fn = []
    refs = {}
    if grade is not None:
        for d, txt in _grade_footnotes(grade):
            if basis == "ai_draft":         # a jóvá nem hagyott AI-vázlat indoklása sem emberi ítélet (6. döntés)
                txt = {"hu": "AI-vázlat (jóváhagyásra vár) — " + txt["hu"],
                       "en": "AI draft (awaiting approval) — " + txt["en"]}
            fn.append({"id": _letters(len(fn)), "text": txt, "domain": d, "kind": "grade"})
            refs.setdefault("certainty", []).append(fn[-1]["id"])
    for item in absolute:
        if item["kind"] == "control_pool":
            txt = _tr("Alapkockázat (%s): a kontrollkarok összesített kockázata (Σe2/Σn2 = %s/1000) — illusztratív; a "
                      "klinikailag releváns alapkockázat forrással adható meg (GRADE-10a)." % (
                          item["label"]["hu"], _fmt(item["assumed_risk_per_1000"], 1)),
                      "Assumed risk (%s): pooled risk of the control arms (Σe2/Σn2 = %s per 1,000) — illustrative; "
                      "a clinically relevant baseline risk with a source can be added (GRADE-10a)." % (
                          item["label"]["en"], _fmt(item["assumed_risk_per_1000"], 1, "en")))
        else:
            txt = {"hu": "Alapkockázat (%s): %s/1000 — forrás: %s." % (item["label"]["hu"],
                                                                        _fmt(item["assumed_risk_per_1000"], 1),
                                                                        item["source"] or "nincs megadva"),
                   "en": "Assumed risk (%s): %s per 1,000 — source: %s." % (item["label"]["en"],
                                                                           _fmt(item["assumed_risk_per_1000"], 1,
                                                                                "en"),
                                                                           item["source"] or "not given")}
        fn.append({"id": _letters(len(fn)), "text": txt, "domain": None, "kind": "baseline"})
        item["footnotes"] = [fn[-1]["id"]]
        refs.setdefault("absolute", []).append(fn[-1]["id"])
    if m in ("SMD", "COHEN_D", "SMD_GLASS", "SMCC"):
        fn.append({"id": _letters(len(fn)), "kind": "note", "domain": None,
                   "text": _tr("SMD: alakítsd vissza ismert skálára, vagy viszonyítsd a MID-hez (GRADE-10b).",
                               "SMD: convert back to a familiar scale or relate it to the MID (GRADE-10b).")})
        refs.setdefault("effect", []).append(fn[-1]["id"])
    if m == "RD":
        fn.append({"id": _letters(len(fn)), "kind": "note", "domain": None,
                   "text": _tr("Az összesített kockázatkülönbség (RD) erősen függ az alapkockázattól; a SoF abszolút "
                               "hatásához lehetőleg RR / OR + alapkockázat (GRADE-10a).",
                               "The pooled risk difference depends strongly on the baseline risk; prefer RR / OR plus "
                               "a baseline risk for the SoF absolute effect (GRADE-10a).")})
        refs.setdefault("absolute", []).append(fn[-1]["id"])
    for extra in footnotes or []:
        fn.append({"id": _letters(len(fn)), "text": _i18n_in(extra.get("text") if isinstance(extra, dict) and
                                                              "text" in extra else extra),
                   "domain": extra.get("domain") if isinstance(extra, dict) else None, "kind": "user"})
        refs.setdefault("comments", []).append(fn[-1]["id"])
    direction = _direction(rd)
    important = True
    if mid_n and direction in ("reduce", "increase"):
        lo_t, hi_t = mid_n.get("lower"), mid_n.get("upper")
        est = bt[0]
        important = not ((lo_t is None or est > lo_t) and (hi_t is None or est < hi_t))
    elif direction in ("reduce", "increase") and certainty in LEVELS and certainty != "very low":
        notes.append(_tr("MID nélkül a megfogalmazás csak az irányt követi; a „kevés vagy semmi különbség” "
                         "megfogalmazáshoz MID kell.",
                         "Without a MID the wording only follows the direction; 'little to no difference' needs a "
                         "MID."))
    out_lab = lab
    stmt = statement(certainty, direction, important, out_lab, _i18n_in(intervention))
    design = design if design in P.GRADE_DESIGNS else None
    row = {"outcome_id": oid, "label": lab, "importance": importance, "k": k, "participants": participants,
           "design": design,
           "studies_text": _studies_text(k, participants, design), "relative": relative, "effect": effect,
           "absolute": absolute, "certainty": certainty, "certainty_basis": basis,
           "certainty_symbols": certainty_symbols(certainty),
           "certainty_text": _certainty_text(certainty, basis),
           "statement": stmt, "footnotes": fn, "footnote_refs": refs,
           "sources": {"participants": "totals.participants", "k": "totals.k (primary.k)",
                       "relative": "back_transformed.estimate_ci", "effect": "back_transformed.estimate_ci",
                       "absolute": "sof.absolute (grade_help.absolute_effect)",
                       "certainty": "szk.ma.grade/v1 certainty" if grade is not None else "paraméter"},
           "warnings": warnings}
    header = {k_: v for k_, v in (("population", population), ("setting", setting), ("intervention", intervention),
                                  ("comparison", comparison)) if v is not None}
    _flat_cells(row)
    doc = {"schema": SOF_SCHEMA, "outcome_id": oid, "run_id": rd.run_id, "measure": m,
           "title": _tr("Az eredmények összefoglalása (SoF)", "Summary of findings"),
           "header": {k_: _i18n_in(v) for k_, v in header.items()} if header else None, "rows": [row],
           "columns": [{"key": k_, "label": _tr(*SOF_COLUMNS[k_])} for k_ in SOF_COLUMN_ORDER],
           "footnotes": [dict(f) for f in fn], "statement": stmt,
           "engine": {"name": "metaelemzes", "version": __version__}, "notes": notes,
           "kb_refs": list(KB["sof"])}
    return doc


SOF_COLUMN_ORDER = ("outcome_text", "participants_text", "relative_text", "assumed_risk_text", "absolute_text",
                    "certainty_cell", "comments_text")
SOF_COLUMNS = {"outcome_text": ("Kimenet", "Outcome"),
               "participants_text": ("Résztvevők (vizsgálatok)", "No. of participants (studies)"),
               "relative_text": ("Relatív hatás (95% CI)", "Relative effect (95% CI)"),
               "assumed_risk_text": ("Alapkockázat", "Assumed risk"),
               "absolute_text": ("Abszolút hatás (95% CI)", "Absolute effect (95% CI)"),
               "certainty_cell": ("A bizonyíték bizonyossága (GRADE)", "Certainty of the evidence (GRADE)"),
               "comments_text": ("Megjegyzés", "Comments")}


def _refs_suffix(ids):
    return (" " + ",".join(ids)) if ids else ""


def _flat_cells(row):
    """A SoF-sor kész cellaszövegei {hu, en} (a munkapad exportja és a táblázat ezekből épít; több alapkockázat
    „; ”-vel elválasztva). Minden szám a sor motor-szövegeiből jön."""
    refs = row.get("footnote_refs") or {}
    dash = _tr("—", "—")
    rel = (row.get("relative") or {}).get("text") or (row.get("effect") or {}).get("text") or dash
    rel = {lang: rel[lang] + (_refs_suffix(refs.get("effect")) if not row.get("relative") else "")
           for lang in ("hu", "en")}
    abss = row.get("absolute") or []
    if abss:
        base = {lang: "; ".join("%s: %s%s" % (a["label"][lang], a["baseline_text"][lang],
                                              _refs_suffix(a.get("footnotes"))) for a in abss)
                for lang in ("hu", "en")}
        absolute = {lang: "; ".join(a["text"][lang] for a in abss) for lang in ("hu", "en")}
    else:
        base = dash
        absolute = (row.get("effect") or {}).get("text") if row.get("relative") and row.get("effect") else dash
    cert = row.get("certainty_text") or _tr("— (még nincs GRADE-ítélet)", "— (no GRADE judgement yet)")
    stmt = row.get("statement") or _tr("", "")
    row["outcome_text"] = row.get("label") or _tr(row["outcome_id"], row["outcome_id"])
    row["participants_text"] = row["studies_text"]
    row["relative_text"] = rel
    row["assumed_risk_text"] = base
    row["absolute_text"] = absolute
    row["certainty_cell"] = {lang: cert[lang] + _refs_suffix(refs.get("certainty")) for lang in ("hu", "en")}
    row["comments_text"] = {lang: (stmt[lang] + _refs_suffix(refs.get("comments"))).strip() for lang in ("hu", "en")}


def sof_path(project_dir, outcome_id):
    if not isinstance(outcome_id, str) or not _OUTCOME_ID.match(outcome_id):
        raise ValueError("érvénytelen kimenet-azonosító: %r" % (outcome_id,))
    return os.path.join(project_dir, *SOF_DIR.split("/"), "%s.sof.json" % outcome_id)


def recorded_certainty(project_dir, outcome_id):
    """A kimenet RÖGZÍTETT GRADE-bizonyossága → {certainty, run_id, source, path} vagy None. Forrás: a
    06_kezirat/grade/<kimenet>.grade.json, ha 'recorded' (és nem jóvá nem hagyott AI-vázlat, nem feloldatlan);
    GRADE-dokumentum nélkül a projektnapló legutóbbi grade-sora (a régi `project grade` út; futás nélkül)."""
    try:
        g = P.load_grade_doc(project_dir, outcome_id)
    except ValueError:
        g = None
    if g is not None:
        st = sof_grade_state(g)
        if st["basis"] == "recorded":
            return {"certainty": st["certainty"], "run_id": g.get("run_id"), "source": "grade",
                    "path": "%s/%s.grade.json" % (P.GRADE_DIR, outcome_id)}
        return None
    if not os.path.isfile(P.db_path(project_dir)):
        return None
    try:
        rows = [r for r in P.list_items(project_dir, "grades") if r.get("outcome") == outcome_id]
    except Exception:       # noqa: BLE001 — olvashatatlan napló: nincs rögzített GRADE
        return None
    if not rows:
        return None
    last = sorted(rows, key=lambda r: r.get("id") or 0)[-1]
    c = P.grade_token(last.get("certainty"))
    if c not in LEVELS:
        return None
    return {"certainty": c, "run_id": None, "source": "journal", "path": None, "id": last.get("id")}


def sof_certainty_problems(project_dir, doc):
    """A SoF-sorok bizonyossága vs. a kimenet rögzített GRADE-ítélete (4. döntés; M6) → [szöveg]. Hiba: bizonyosság
    rögzített GRADE nélkül (piszkozat, feloldatlan, jóvá nem hagyott AI-vázlat vagy semmi), eltérő szint, vagy a
    rögzített GRADE másik futásra vonatkozik. A bizonyosság nélküli (null) sor rendben van."""
    out = []
    for i, row in enumerate(doc.get("rows") or []):
        if not isinstance(row, dict):
            continue
        c = P.grade_token(row.get("certainty"))
        if c is None:
            continue
        oid = row.get("outcome_id") or doc.get("outcome_id")
        where = "%d. sor (%s)" % (i + 1, oid)
        ref = recorded_certainty(project_dir, oid) if isinstance(oid, str) and _OUTCOME_ID.match(oid) else None
        if ref is None:
            out.append("%s: a bizonyosság (%s) mellé nincs rögzített GRADE-ítélet (piszkozat, feloldatlan publikációs "
                       "torzítás, jóvá nem hagyott AI-vázlat, vagy nincs GRADE)" % (where, c))
        elif ref["certainty"] != c:
            out.append("%s: a bizonyosság (%s) ≠ a rögzített GRADE-é (%s)" % (where, c, ref["certainty"]))
        elif ref["run_id"] and doc.get("run_id") != ref["run_id"]:
            out.append("%s: a rögzített GRADE a(z) %s futásra vonatkozik, a SoF a(z) %s futásra" % (
                where, ref["run_id"], doc.get("run_id") or "–"))
    return out


def save_sof(project_dir, doc):
    """A SoF mentése: <projekt>/06_kezirat/sof/<kimenet>.sof.json (atomikus, UTF-8, 'updated' UTC) → a dokumentum.
    A bizonyosság csak a kimenet rögzített GRADE-ítéletéé lehet (ugyanarra a futásra; 4. döntés, methodology:M6):
    piszkozat, feloldatlan publikációs torzítás, jóvá nem hagyott AI-vázlat vagy eltérő szint mellett ValueError
    (a bizonyosság nélküli SoF menthető)."""
    if not isinstance(doc, dict) or doc.get("schema") != SOF_SCHEMA:
        raise ValueError("szk.ma.sof/v1 dokumentum kell")
    probs = sof_certainty_problems(project_dir, doc)
    if probs:
        raise ValueError("A SoF nem menthető: %s. A SoF bizonyossága a rögzített GRADE-ítéleté (4. döntés; X008, "
                         "X019) — előbb rögzítsd a GRADE-et (ma.py grade record), vagy készítsd a SoF-ot "
                         "bizonyosság nélkül." % "; ".join(probs))
    doc = dict(doc)
    doc["updated"] = P._grade_utc_now()
    path = sof_path(project_dir, doc.get("outcome_id"))
    P._grade_write_json(path, doc)
    return doc


def load_sof(project_dir, outcome_id):
    p = sof_path(project_dir, outcome_id)
    if not os.path.isfile(p):
        return None
    doc = _read_json(p)
    if not isinstance(doc, dict) or doc.get("schema") != SOF_SCHEMA:
        raise ValueError("nem szk.ma.sof/v1 dokumentum: %s" % p)
    return doc


_SOF_HEAD = {"hu": ("Kimenet", "Résztvevők (vizsgálatok)", "Relatív hatás (95% CI)", "Alapkockázat",
                    "Abszolút hatás (95% CI)", "Bizonyosság", "Megjegyzés"),
             "en": ("Outcome", "Participants (studies)", "Relative effect (95% CI)", "Assumed risk",
                    "Absolute effect (95% CI)", "Certainty", "Comments")}


def sof_table(doc, lang="hu"):
    """A SoF táblázat sorai szövegként: [fejléc] + soronként (alapkockázatonként) egy sor; lábjegyzet-betűkkel."""
    if lang not in ("hu", "en"):
        raise ValueError("lang: hu vagy en")
    rows = [list(_SOF_HEAD[lang])]
    for r in doc.get("rows") or []:
        refs = r.get("footnote_refs") or {}

        def sup(key):
            ids = refs.get(key) or []
            return (" " + ",".join(ids)) if ids else ""
        rel = (r.get("relative") or {}).get("text") or {}
        eff = (r.get("effect") or {}).get("text") or {}
        cert = (r.get("certainty_text") or {}).get(lang) or "—"
        stmt = (r.get("statement") or {}).get(lang) or ""
        base = [((r.get("label") or {}).get(lang) or r.get("outcome_id") or ""),
                (r.get("studies_text") or {}).get(lang) or "",
                (rel.get(lang) or "—") if r.get("relative") else (eff.get(lang) or "—") + sup("effect")]
        abss = r.get("absolute") or []
        if not abss:
            rows.append(base + ["—", (eff.get(lang) or "—") if r.get("relative") else "—", cert + sup("certainty"),
                                stmt + sup("comments")])
        for i, a in enumerate(abss):
            line = base if i == 0 else ["", "", ""]
            ids = a.get("footnotes") or []
            rows.append(line + [((a.get("label") or {}).get(lang, "") + ": " + (a.get("baseline_text") or {}).get(
                lang, "")) + ((" " + ",".join(ids)) if ids else ""), (a.get("text") or {}).get(lang, ""),
                                (cert + sup("certainty")) if i == 0 else "", (stmt + sup("comments")) if i == 0
                                else ""])
    return rows


def _footnote_lines(doc, lang):
    out = []
    for r in doc.get("rows") or []:
        for f in r.get("footnotes") or []:
            out.append("%s) %s" % (f["id"], (f.get("text") or {}).get(lang, "")))
    return out


def sof_markdown(doc, lang="hu"):
    """A SoF Markdown-táblázatként (lábjegyzetekkel)."""
    rows = sof_table(doc, lang)

    def cell(s):
        return str(s).replace("|", "\\|").replace("\n", " ")
    L = ["| " + " | ".join(cell(x) for x in rows[0]) + " |", "|" + "---|" * len(rows[0])]
    L += ["| " + " | ".join(cell(x) for x in r) + " |" for r in rows[1:]]
    fl = _footnote_lines(doc, lang)
    if fl:
        L += [""] + fl
    return "\n".join(L) + "\n"


def _csv_safe(s):
    s = str(s)
    return ("'" + s) if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def sof_csv(doc, lang="hu", delimiter=";"):
    """A SoF CSV-ként (Excel-biztos: a képletnek látszó cella elé aposztróf; a hívó UTF-8 BOM-mal írja)."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    for r in sof_table(doc, lang):
        w.writerow([_csv_safe(x) for x in r])
    for line in _footnote_lines(doc, lang):
        w.writerow([_csv_safe(line)])
    return buf.getvalue()


def sof_html(doc, lang="hu"):
    """A SoF egyszerű HTML-táblázatként (Wordbe másoláshoz; minden szöveg escape-elve)."""
    rows = sof_table(doc, lang)
    e = html.escape
    L = ["<table>", "<thead><tr>" + "".join("<th>%s</th>" % e(x) for x in rows[0]) + "</tr></thead>", "<tbody>"]
    L += ["<tr>" + "".join("<td>%s</td>" % e(str(x)) for x in r) + "</tr>" for r in rows[1:]]
    L.append("</tbody></table>")
    fl = _footnote_lines(doc, lang)
    if fl:
        L.append("<ol type=\"a\">" + "".join("<li>%s</li>" % e(x.split(") ", 1)[1] if ") " in x else x) for x in fl) +
                 "</ol>")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ AMSTAR 2
AMSTAR2_ITEMS = tuple(str(i) for i in range(1, 17))
AMSTAR2_CRITICAL = ("2", "4", "7", "9", "11", "13", "15")
AMSTAR2_PARTIAL_YES = ("2", "4", "7", "8", "9")
AMSTAR2_NO_MA = ("11", "12", "15")
AMSTAR2_PARTS_ITEMS = ("9", "11")       # RCT / NRSI részek (Shea 2017; instruments/amstar2.json "parts")
AMSTAR2_ANSWERS = ("yes", "partial_yes", "no", "not_applicable")     # = metaelemzes/instruments/amstar2.json
AMSTAR2_RATINGS = ("high", "moderate", "low", "critically_low")
AMSTAR2_CONVENTIONS = P.CONVENTIONS["amstar2_partial_yes_critical"]
_AMSTAR_ALIASES = {        # ékezet nélküli, kisbetűs alakok (_amstar_answer normalizál)
    "yes": "yes", "y": "yes", "igen": "yes", "i": "yes",
    "partial_yes": "partial_yes", "partial yes": "partial_yes", "partially yes": "partial_yes",
    "reszben igen": "partial_yes", "reszben": "partial_yes", "ri": "partial_yes", "py": "partial_yes",
    "no": "no", "n": "no", "nem": "no",
    "not_applicable": "not_applicable", "na": "not_applicable", "n/a": "not_applicable",
    "not applicable": "not_applicable", "nem alkalmazhato": "not_applicable", "no_meta_analysis": "not_applicable",
    "no meta-analysis": "not_applicable", "no meta analysis": "not_applicable",
    "no meta-analysis conducted": "not_applicable", "nma": "not_applicable",
    "nem volt metaanalizis": "not_applicable", "nincs metaanalizis": "not_applicable",
}
_AMSTAR_RATING_ALIASES = {
    "high": "high", "magas": "high", "moderate": "moderate", "mérsékelt": "moderate", "merseklet": "moderate",
    "low": "low", "alacsony": "low", "critically low": "critically_low", "critically_low": "critically_low",
    "kritikusan alacsony": "critically_low",
}
AMSTAR2_RATING_LABELS = {"high": ("MAGAS", "HIGH"), "moderate": ("MÉRSÉKELT", "MODERATE"),
                         "low": ("ALACSONY", "LOW"), "critically_low": ("KRITIKUSAN ALACSONY", "CRITICALLY LOW")}


def _amstar_answer(raw):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(raw).strip().lower())
    t = re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c)))
    return _AMSTAR_ALIASES.get(t)


def _amstar_item(key):
    s = str(key).strip().upper()
    s = re.sub(r"^(AMSTAR ?2?[-_ ]?|ITEM ?|TÉTEL ?|Q)", "", s)
    s = s.lstrip("0") or "0"
    return s if s in AMSTAR2_ITEMS else None


def _amstar_parts(item, val):
    """AMSTAR 2 9. és 11. tétel (methodology:M11): a hivatalos űrlap RCT-re és NRSI-re KÜLÖN ítél. Egy
    {value?, parts: {RCT, NRSI}} válasz → (a részekből adódó érték, ellentmondás / hiba | None, van-e 'parts'). A
    szabály az értékelés-motoré (appraisal._raw_answer, Instrument.combine_parts): bármelyik rész „Nem” → „Nem”;
    „csak NRSI / csak RCT” (illetve „nem volt metaanalízis”) = Nem alkalmazható; félkész részeknél az explicit érték
    számít. 'parts' nélkül → (a nyers érték, None, False)."""
    raw = val.get("value") if isinstance(val, dict) else val
    if not isinstance(val, dict) or val.get("parts") is None:
        return raw, None, False
    from . import appraisal as _A                       # késői import: az appraisal modul nem függ a grade_help-től
    inst = _A.instrument_for("amstar2")
    it = inst.item(item)
    if it is None or not inst.parts(it):
        return raw, None, False
    value, conflict = _A._raw_answer(inst, it, val)
    return value, conflict, True


def _amstar_rating(answers_by_item, convention):
    crit, weak = [], []
    for item in AMSTAR2_ITEMS:
        a = answers_by_item.get(item)
        if a in (None, "yes", "not_applicable"):
            continue
        if a == "no":
            (crit if item in AMSTAR2_CRITICAL else weak).append(item)
        elif a == "partial_yes" and convention == "weakness":
            weak.append(item)           # minden „részben igen” tételen (a 8. tételen is) — appraisal._amstar2_block
    if len(crit) > 1:
        rating = "critically_low"
    elif len(crit) == 1:
        rating = "low"
    elif len(weak) > 1:
        rating = "moderate"
    else:
        rating = "high"
    return rating, crit, weak


def amstar2_consistency(answers, convention=None, claimed=None):
    """AMSTAR 2 (Shea 2017) összbesorolás a hivatalos algoritmussal, MINDKÉT konvencióval (KB AMSTAR2-00; terv 3.5.13,
    5.4, H4). Kritikus tételek: 2, 4, 7, 9, 11, 13, 15. Kritikus tételen a „nem” kritikus hiba, egyéb tételen nem
    kritikus gyengeség; a „nem volt metaanalízis” (11, 12, 15) nem hiba. A „részben igen” (csak 2, 4, 7, 8, 9):
    'meets' (projektkonvenció) — nem hiba; 'weakness' (a validator 2.0.0 szabálya) — nem kritikus gyengeség, a 8.
    tételen is (a validator 1.0.0 csak a kritikus tételeken számolta annak).
    Magas: legfeljebb egy nem kritikus gyengeség; mérsékelt: több nem kritikus gyengeség; alacsony: egy kritikus
    hiba; kritikusan alacsony: egynél több.

    answers: {tétel ('1'–'16', 'AMSTAR2-02', 2 …): válasz ('yes' | 'partial_yes' | 'no' | 'not_applicable' — a
    metaelemzes/instruments/amstar2.json szókincse —, vagy magyar / rövid alak; {value: …} is; a 9. és 11. tételnél
    {parts: {RCT, NRSI}} is — bármelyik rész „Nem” → „Nem”, ellentmondó value → érvénytelen; methodology:M11)}, vagy
    egy szk.appraisal/v1 dokumentum (answers mezővel). claimed: egy
    máshonnan (validator, ember) kapott besorolás — eltérésnél consistency_warning.
    → {instrument, algorithm, convention, rating, critical_flaws, weaknesses, by_convention {meets, weakness},
    convention_sensitive, partial_yes_critical, not_applicable, answered, expected, missing, invalid, parts_missing,
    complete, provisional, claimed, consistency_warning, label, text, sensitivity_text, notes, kb_refs}"""
    convention = convention or P.DEFAULT_CONVENTIONS["amstar2_partial_yes_critical"]
    if convention not in AMSTAR2_CONVENTIONS:
        raise ValueError("convention: %s lehet (kapott: %r)" % (" | ".join(AMSTAR2_CONVENTIONS), convention))
    if isinstance(answers, dict) and isinstance(answers.get("answers"), dict) and "schema" in answers:
        answers = answers["answers"]
    if not isinstance(answers, dict):
        raise ValueError("answers: {tétel: válasz} objektum legyen")
    by_item, invalid, notes = {}, [], []
    parts_missing = []
    for key, val in answers.items():
        item = _amstar_item(key)
        raw = val.get("value") if isinstance(val, dict) else val
        if item is None:
            invalid.append({"item": str(key), "value": raw, "reason": _tr("ismeretlen tétel (1–16)",
                                                                          "unknown item (1–16)")})
            continue
        raw, conflict, has_parts = _amstar_parts(item, val)
        if conflict:
            invalid.append({"item": item, "value": raw if isinstance(raw, str) else None, "reason": _tr(
                "a részek (RCT / NRSI) hibásak vagy ellentmondanak a tétel értékének: %s" % conflict,
                "the parts (RCT / NRSI) are invalid or contradict the item value: %s" % conflict)})
            continue
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if item in AMSTAR2_PARTS_ITEMS and not (has_parts and isinstance(val.get("parts"), dict) and all(
                val["parts"].get(p) not in (None, "") for p in ("RCT", "NRSI"))):
            parts_missing.append(item)
        a = _amstar_answer(raw) if isinstance(raw, str) else None
        if a is None:
            invalid.append({"item": item, "value": raw, "reason": _tr("ismeretlen válasz (igen / részben igen / nem / "
                                                                      "nem volt metaanalízis)",
                                                                      "unknown answer (yes / partial yes / no / no "
                                                                      "meta-analysis)")})
            continue
        if a == "partial_yes" and str(raw).strip().lower() == "py":
            notes.append(_tr("A(z) %s. tétel „PY” válaszát „részben igen”-nek vettük (AMSTAR 2-ben nincs „valószínűleg "
                             "igen”; H4) — írd ki: partial_yes." % item,
                             "Item %s: 'PY' was read as 'partial yes' (AMSTAR 2 has no 'probably yes'; H4) — write "
                             "partial_yes." % item))
        if a == "partial_yes" and item not in AMSTAR2_PARTIAL_YES:
            invalid.append({"item": item, "value": raw,
                            "reason": _tr("ennél a tételnél nincs „részben igen” (csak 2, 4, 7, 8, 9)",
                                          "this item has no 'partial yes' (only 2, 4, 7, 8, 9)")})
            continue
        if a == "not_applicable" and item not in AMSTAR2_NO_MA:
            invalid.append({"item": item, "value": raw, "reason": _tr("a „nem volt metaanalízis” csak a 11., 12. és "
                                                                      "15. tételnél adható",
                                                                      "'no meta-analysis' only applies to items 11, "
                                                                      "12 and 15")})
            continue
        by_item[item] = a
    by_conv = {}
    for conv in AMSTAR2_CONVENTIONS:
        r, crit, weak = _amstar_rating(by_item, conv)
        by_conv[conv] = {"rating": r, "critical_flaws": crit, "weaknesses": weak}
    sel = by_conv[convention]
    missing = [i for i in AMSTAR2_ITEMS if i not in by_item and not any(x["item"] == i for x in invalid)]
    py_crit = [i for i in AMSTAR2_CRITICAL if by_item.get(i) == "partial_yes"]
    sensitive = by_conv["meets"]["rating"] != by_conv["weakness"]["rating"]
    other = "weakness" if convention == "meets" else "meets"
    provisional = bool(missing or invalid)
    claimed_n = None
    warning = None
    if claimed is not None:
        claimed_n = _AMSTAR_RATING_ALIASES.get(str(claimed).strip().lower().replace("_", " ")) or \
            _AMSTAR_RATING_ALIASES.get(str(claimed).strip().lower())
        if claimed_n is None:
            warning = "A megadott besorolás (%r) nem AMSTAR 2-szint (magas / mérsékelt / alacsony / kritikusan " \
                      "alacsony)." % (claimed,)
        elif claimed_n != sel["rating"]:
            if claimed_n == "low" and sel["rating"] == "moderate":
                notes.append(_tr("A megadott „alacsony” a „mérsékelt” helyett: az AMSTAR 2 megengedi, ha több nem "
                                 "kritikus gyengeség együtt csökkenti a bizalmat — indokold.",
                                 "'Low' instead of 'moderate': AMSTAR 2 allows it when several non-critical "
                                 "weaknesses together reduce confidence — justify it."))
            else:
                warning = "A megadott besorolás (%s) nem egyezik a válaszokból adódóval (%s, '%s' konvenció)%s." % (
                    AMSTAR2_RATING_LABELS[claimed_n][0], AMSTAR2_RATING_LABELS[sel["rating"]][0], convention,
                    ("; a '%s' konvencióval (a validator szabálya) viszont egyezik" % other)
                    if by_conv[other]["rating"] == claimed_n else "")
    if parts_missing:
        notes.append(_tr("A(z) %s tételt a hivatalos AMSTAR 2-űrlap RCT-re és NRSI-re KÜLÖN ítélteti — add meg a részeket "
                         "({parts: {RCT, NRSI}}; „csak NRSI / csak RCT” = not_applicable); bármelyik rész „Nem” → a "
                         "tétel „Nem” (kritikus hiba)." % ", ".join(parts_missing),
                         "Item(s) %s are rated SEPARATELY for RCTs and NRSI on the official AMSTAR 2 form — give the "
                         "parts ({parts: {RCT, NRSI}}; 'only NRSI / only RCTs' = not_applicable); 'No' on either part "
                         "makes the item 'No' (critical flaw)." % ", ".join(parts_missing)))
    if sel["rating"] == "moderate":
        notes.append(_tr("Több nem kritikus gyengeség együtt az „alacsony” besorolást is indokolhatja (emberi döntés, "
                         "indoklással).", "Several non-critical weaknesses together may justify 'low' (human "
                                          "decision, with a rationale)."))
    notes.append(_tr("Az AMSTAR 2 az áttekintés eredményeibe vetett bizalmat minősíti, nem a bizonyosságot (≠ GRADE).",
                     "AMSTAR 2 rates confidence in the results of the review, not the certainty of evidence "
                     "(≠ GRADE)."))
    lab = AMSTAR2_RATING_LABELS[sel["rating"]]
    text = {"hu": "Besorolás: %s (kritikus hiba %d; nem kritikus gyengeség %d)%s" % (
        lab[0], len(sel["critical_flaws"]), len(sel["weaknesses"]),
        (" — IDEIGLENES: %d tétel hiányzik vagy érvénytelen" % (len(missing) + len(invalid))) if provisional else ""),
        "en": "Rating: %s (critical flaws %d; non-critical weaknesses %d)%s" % (
        lab[1], len(sel["critical_flaws"]), len(sel["weaknesses"]),
        (" — PROVISIONAL: %d item(s) missing or invalid" % (len(missing) + len(invalid))) if provisional else "")}
    sens = None
    if sensitive:
        ol = AMSTAR2_RATING_LABELS[by_conv[other]["rating"]]
        sens = {"hu": "Konvenció-érzékenység: ha a „részben igen” %s → %s (KB AMSTAR2-00)." % (
            "gyengeség" if other == "weakness" else "nem hiba", ol[0]),
            "en": "Convention sensitivity: if 'partial yes' %s → %s (KB AMSTAR2-00)." % (
            "is a weakness" if other == "weakness" else "is not a flaw", ol[1])}
    return {"instrument": "amstar2", "algorithm": "published", "convention": convention, "rating": sel["rating"],
            "critical_flaws": list(sel["critical_flaws"]), "weaknesses": list(sel["weaknesses"]),
            "by_convention": by_conv, "convention_sensitive": sensitive, "partial_yes_critical": py_crit,
            "not_applicable": [i for i in AMSTAR2_ITEMS if by_item.get(i) == "not_applicable"],
            "answered": len(by_item), "expected": len(AMSTAR2_ITEMS), "missing": missing, "invalid": invalid,
            "parts_missing": parts_missing,
            "complete": not provisional, "provisional": provisional, "claimed": claimed_n,
            "consistency_warning": warning, "label": {"hu": lab[0], "en": lab[1]}, "text": text,
            "sensitivity_text": sens, "notes": notes, "kb_refs": list(KB["amstar2"])}


# ------------------------------------------------------------------ audit-támasz (X019, X007, X008)
X_RULES = {
    "X019": ("error", "A GRADE publikációs torzítás doménje feloldatlan („suspected”)",
             "A „gyanított” (suspected) publikációs torzítás ítélete addig feloldatlan, amíg ember nem választ 0-t "
             "vagy −1-et indoklással; ilyen kimenet GRADE-je nem rögzíthető, és nem kerülhet a SoF-ba. Döntsd el a "
             "GRADE-lapon (a „Miért?” panel a tesztek értelmezhetőségét és a keresés szempontjait mutatja), vagy "
             "válaszd a „nem észlelt” / „erősen gyanított” ítéletet. Az S13 (bizonyosság) szakasztól hiba, előtte "
             "figyelmeztetés.", "GRADE Handbook 5.2.5; 11. döntés, 4. pont"),
}
X_RULE_STAGES = {"X019": "S13"}
X_KB_REFS = {"X019": ("D-S13-008", "GRADE-07")}
X_ESCALATION = {"X019": "S13"}
X_TITLES_EN = {"X019": "GRADE publication bias domain unresolved ('suspected')"}


def unresolved_publication_bias(project_dir):
    """A projekt feloldatlan („suspected”, lépés nélküli) publikációs torzítású GRADE-ítéletei (X019):
    [{outcome_id, path, run_id, status}] — a hibás GRADE-fájlok nem szerepelnek (lásd list_grade_docs errors)."""
    out = []
    for e in P.list_grade_docs(project_dir):
        doc = e.get("doc")
        if e.get("errors") or not isinstance(doc, dict):
            continue
        if P.grade_doc_open(doc)["unresolved"]:
            out.append({"outcome_id": e["outcome_id"], "path": e["path"], "run_id": doc.get("run_id"),
                        "status": (doc.get("domains") or {}).get("publication_bias", {}).get("status")})
    return out


def x019_findings(project_dir):
    """X019-találatok a project audit-hoz (a súlyosság eszkalációját — S13-tól hiba — a hívó végzi):
    [{code, outcome, detail, artifacts, kb_refs}]."""
    out = []
    for u in unresolved_publication_bias(project_dir):
        out.append({"code": "X019", "outcome": u["outcome_id"],
                    "detail": "%s: a publikációs torzítás „gyanított”, de nincs 0 / −1 döntés indoklással (run %s)" % (
                        u["path"], u["run_id"] or "—"),
                    "artifacts": [u["path"]], "kb_refs": list(X_KB_REFS["X019"])})
    return out


def grade_run_mismatches(doc, run):
    """X007-támasz: a GRADE-dokumentum (run_summary, run_id) vagy egy projektnapló-sor (k, participants, effect) és a
    futás eltérései → [szöveg]. Üres lista: egyezik."""
    rd = load_run(run)
    probs = []
    summ = run_summary(rd)
    if isinstance(doc, dict) and doc.get("schema") == GRADE_SCHEMA:
        if doc.get("run_id") and rd.run_id and doc["run_id"] != rd.run_id:
            probs.append("run_id: %s ≠ %s" % (doc["run_id"], rd.run_id))
        got = doc.get("run_summary") or {}
        k, part = got.get("k"), got.get("participants")
        eff = (got.get("display_text") or {}).get("hu") if isinstance(got.get("display_text"), dict) else None
    else:
        k, part, eff = (doc or {}).get("k"), (doc or {}).get("participants"), (doc or {}).get("effect")
    if k is not None and summ["k"] is not None and int(k) != int(summ["k"]):
        probs.append("k: %s ≠ %s" % (k, summ["k"]))
    if part is not None and summ["participants"] is not None and float(part) != float(summ["participants"]):
        probs.append("résztvevők: %s ≠ %s" % (_int_text(part), _int_text(summ["participants"])))
    want = (summ.get("display_text") or {}).get("hu")
    if eff and want and want not in str(eff):
        probs.append("hatás-szöveg: %r nem tartalmazza a futás %r szövegét" % (eff, want))
    return probs


def sof_run_mismatches(doc, run):
    """X008-támasz: a mentett SoF celláinak eltérése a futás motor-szövegeitől és az újraszámolt abszolút hatástól
    → [szöveg]."""
    rd = load_run(run)
    probs = []
    if not isinstance(doc, dict) or doc.get("schema") != SOF_SCHEMA:
        return ["nem szk.ma.sof/v1 dokumentum"]
    if doc.get("run_id") and rd.run_id and doc["run_id"] != rd.run_id:
        probs.append("run_id: %s ≠ %s" % (doc["run_id"], rd.run_id))
    bt = rd.bt
    want = PL.display_text(*bt) if None not in bt else None
    for r in doc.get("rows") or []:
        if r.get("k") != (rd.primary or {}).get("k"):
            probs.append("k: %s ≠ %s" % (r.get("k"), (rd.primary or {}).get("k")))
        cell = (r.get("relative") or r.get("effect") or {}).get("display_text")
        if want and cell and cell != want:
            probs.append("relatív hatás: %s ≠ %s" % (cell.get("hu"), want["hu"]))
        for a in r.get("absolute") or []:
            try:
                again = absolute_effect(rd.measure, bt, a["assumed_risk_per_1000"] / 1000.0)
            except (ValueError, KeyError, TypeError):
                probs.append("abszolút hatás (%s): nem számolható újra" % (a.get("label") or {}).get("hu"))
                continue
            if _both(lambda lang: _abs_text(again["difference_per_1000"], lang)) != a.get("text"):
                probs.append("abszolút hatás (%s): a cella nem egyezik az újraszámolttal" % (a.get("label") or {}).get(
                    "hu"))
    return probs
