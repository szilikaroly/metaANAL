# -*- coding: utf-8 -*-
"""A GRADE / SoF / AMSTAR 2 végpontok motor-kapcsolata funkció-felismeréssel (terv 3.2, 4.14, 5.4, 10.6 E10).

A motor v1-függvényei (``grade_help``, SoF, a GRADE-tár, az AMSTAR 2 konzisztencia, a műszer- és értékelés-tár)
a motor repójában párhuzamosan készülnek, és a ``metaelemzes.api`` homlokzaton át érhetők el. A munkapad ezeket
NÉV SZERINT keresi (``FUNCS``: a homlokzat lehetséges nevei), és ha egyik sincs meg, a végpont 424
CAPABILITY_MISSING hibát ad a pontos magyar teendővel — a többi végpont ettől még működik.

A hívás a függvény ALÁÍRÁSÁBÓL épül (``bind``): a paraméterneveket a munkapad ismert értékeiből tölti
(``project_dir`` / ``project_root``, ``outcome`` / ``outcome_id``, ``run`` / ``run_dir`` …), így a homlokzat
paraméter-elnevezése nem töri el a kapcsolatot; ha egy KÖTELEZŐ paramétert nem tudunk kitölteni, az is 424
(„a motor függvényének aláírása nem az elvárt”). Az elvárt aláírások (integrációs pontok, a végpontok
dokumentációjával együtt):

    grade_get(project_dir, outcome_id) -> szk.ma.grade/v1 | None
        a kimenet mentett GRADE-ítélete (06_kezirat/grade/<kimenet>.grade.json); None, ha még nincs
    grade_put(project_dir, outcome_id, doc, actor=None) -> szk.ma.grade/v1
        ellenőrzés (ValueError → 422, magyar üzenettel), a bizonyosság kiszámítása a domén-lépésekből
        ('certainty': None, amíg bármely domén — a publikációs torzítás 'suspected' ítélete is — feloldatlan),
        atomikus mentés; a mentett, normalizált dokumentumot adja vissza
    grade_record(project_dir, doc, actor=None, kb_db=None) -> {id, doc, warnings, path}      [opcionális]
        a mentett ítélet projektnapló-sora (projekt.add_grade, előjeles lépés-szövegekkel) + a fájl 'recorded'
        státusza (motor: projekt.record_grade_doc); ha nincs, a munkapad a meglévő api.project_grade-et hívja
        ugyanezekkel a szövegekkel
    grade_advice(run, rob_by_row=None, mid=None, project_dir=None) -> szk.ma.grade-advice/v1
        run: a commit-futás mappájának ABSZOLÚT útja (run.json, results.json, plot_data.json);
        mid: a felhasználó által beírt nyers szöveg (pl. '0,75–1,25') vagy None — a motor parszol
    sof(run, assumed_risks, certainty=None, footnotes=None, project_dir=None, grade=None) -> szk.ma.sof/v1
        assumed_risks: [{label, source: control_pool|external, per_1000: nyers szöveg | None, note}];
        grade: a mentett szk.ma.grade/v1 (bizonyosság + lábjegyzetek a domén-indoklásokból)
    sof_problems(project_dir, doc) -> [szöveg]                      [opcionális] a SoF bizonyossága csak a rögzített
        GRADE-ítéleté lehet (4. döntés; X008) — a munkapad a saját írása előtt ellenőriz vele
    sof_csv(doc, lang='hu', delimiter=';') / sof_markdown(doc, lang='hu')       [opcionális; különben a munkapad
        ugyanazokból a motor-szövegekből rendereli — grade_sof.render_csv / render_md]
    amstar2_consistency(answers, convention='meets') -> {rating, critical_flaws, weaknesses,
                                                          by_convention{meets: rating, weakness: rating}, …}
        answers: {tétel-azonosító: kanonikus válasz (yes | partial_yes | no | no_meta_analysis)}
    instrument_get(tool) -> szk.instrument/v1                     (AMSTAR 2: tool = 'amstar2'; az értékelés-
        végpontokkal közös név — routes/appraisal_common.py)

Állapot (2026-10-05): a motor ezeket a metaelemzes.grade_help (advice, sof, sof_csv, sof_markdown, amstar2_consistency)
és a metaelemzes.projekt (load_grade_doc, save_grade_doc, record_grade_doc) moduljaiban már megvalósította; a
homlokzatra (metaelemzes.api) az integrátor köti ki őket — a fenti nevek bármelyikén megtaláljuk.

A motor ``ValueError``-ja a router szerint 422 VALIDATION (a motor üzenete szó szerint)."""
import inspect

from metaelemzes import api

from ..router import ApiError

# kulcs → (a homlokzat lehetséges függvénynevei, a funkció magyar neve)
FUNCS = {
    "grade_get": (("grade_get", "grade_load", "load_grade_doc", "grade_doc_load"), "a GRADE-ítélet beolvasása"),
    "grade_put": (("grade_put", "grade_save", "save_grade_doc", "grade_doc_save"),
                  "a GRADE-ítélet mentése (bizonyosság a domén-lépésekből)"),
    "grade_record": (("grade_record", "record_grade_doc", "grade_doc_record"), "a GRADE-ítélet naplózása"),
    "grade_advice": (("grade_advice",), "a GRADE-tanácsadó (grade_help: bizonyítékok és KB-szabályok doménenként)"),
    "sof": (("sof", "sof_build"), "a Summary of Findings tábla (abszolút hatás alapkockázatonként)"),
    "sof_csv": (("sof_csv",), "a SoF CSV-exportja (Excel-biztos)"),
    "sof_markdown": (("sof_markdown",), "a SoF Markdown-exportja"),
    "sof_problems": (("sof_problems",), "a SoF bizonyosságának egyezése a rögzített GRADE-ítélettel (X008)"),
    "amstar2_consistency": (("amstar2_consistency",), "az AMSTAR 2 besorolás és konzisztencia (mindkét konvencióval)"),
    "instrument": (("instrument_get", "get_instrument", "instrument", "load_instrument"),
                   "a műszer-leírás (szk.instrument/v1)"),
}

# a bind() által ismert paraméternevek csoportjai (a homlokzat elnevezése így szabadon választható)
ALIASES = {
    "project_dir": ("project_dir", "project_root", "root", "project", "proj"),
    "outcome": ("outcome_id", "outcome"),
    "doc": ("doc", "appraisal", "document"),
    "run": ("run", "run_dir", "run_path", "rundir"),
    "run_id": ("run_id",),
    "actor": ("actor",),
    "kb_db": ("kb_db", "db"),
    "mid": ("mid", "mid_text"),
    "rob_by_row": ("rob_by_row",),
    "assumed_risks": ("assumed_risks", "risks"),
    "certainty": ("certainty",),
    "footnotes": ("footnotes",),
    "answers": ("answers",),
    "convention": ("convention",),
    "tool": ("tool", "key", "name"),
    "lang": ("lang", "locale"),
    "delimiter": ("delimiter",),
    "grade": ("grade", "grade_doc"),
}

MSG_MISSING = ("A motor ebben a változatban még nem tudja ezt: %s (hiányzik: metaelemzes.api.%s). Frissítsd a "
               "motort (a metaanalizis-asszisztens új változata), majd indítsd újra a munkapadot. A többi "
               "funkció addig is működik.")
MSG_SIGNATURE = ("A motor függvénye (metaelemzes.api.%s) más paramétereket vár, mint amit a munkapad ismer "
                 "(%s). Frissítsd együtt a motort és a munkapadot.")


def fn(key):
    """A homlokzat függvénye a kulcshoz (az első létező, hívható név) vagy None."""
    names, _label = FUNCS[key]
    for name in names:
        f = getattr(api, name, None)
        if callable(f):
            return f
    return None


def has(key):
    return fn(key) is not None


def available():
    """{kulcs: bool} — melyik motor-funkció érhető el most (a felület ebből tudja, mit tilt le)."""
    return {k: has(k) for k in sorted(FUNCS)}


def need(key):
    """A függvény, vagy 424 CAPABILITY_MISSING a funkció nevével és a teendővel."""
    f = fn(key)
    if f is None:
        names, label = FUNCS[key]
        raise ApiError("CAPABILITY_MISSING", MSG_MISSING % (label, names[0]),
                       {"feature": key, "missing": "metaelemzes.api.%s" % names[0]})
    return f


def _params(f):
    try:
        return inspect.signature(f).parameters
    except (TypeError, ValueError):
        return None


def bind(key, f, values):
    """A hívás kulcsszavas argumentumai f aláírásából: minden paraméter, amelynek a neve (vagy álneve) az
    ismert értékek közt van; ha egy kötelező paraméter nem tölthető ki: 424."""
    params = _params(f)
    if params is None:
        raise ApiError("CAPABILITY_MISSING", MSG_SIGNATURE % (FUNCS[key][0][0], "nem olvasható aláírás"))
    by_name = {}
    for canon, names in ALIASES.items():
        if canon in values:
            for n in names:
                by_name.setdefault(n, values[canon])
    kw, missing = {}, []
    for name, p in params.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if name in by_name:
            kw[name] = by_name[name]
        elif p.default is p.empty:
            missing.append(name)
    if missing:
        raise ApiError("CAPABILITY_MISSING", MSG_SIGNATURE % (f.__name__, "ismeretlen kötelező paraméter: %s"
                                                              % ", ".join(missing)),
                       {"feature": key, "missing_params": missing})
    return kw


def call(key, **values):
    """A motor-függvény hívása az ismert értékekkel (need + bind)."""
    f = need(key)
    return f(**bind(key, f, values))


def call_optional(key, **values):
    """Mint a call, de ha a függvény nincs meg: (False, None); különben (True, eredmény)."""
    f = fn(key)
    if f is None:
        return False, None
    return True, f(**bind(key, f, values))
