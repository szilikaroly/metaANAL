#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fixture-generátor az elemzés-képernyőkhöz (BCG, RR, REML + HKSJ).

A számok a motorból jönnek (metaelemzes.pipeline — a metafor 4.4-hez validált motor), a
display_text-ek a motor fmt_triple/decimals logikájával készülnek (HU: tizedesvessző, U+2212
mínusz). Egy külön R-szkript (check_metafor.R) a fő számokat a metaforral is ellenőrzi.

Kimenet: ma_gui/web/fixtures/analysis_*.json
"""
import contextlib
import copy
import csv
import hashlib
import io
import json
import math
import os
import subprocess
import sys

REPO = str(__import__('pathlib').Path(__file__).resolve().parents[4])
SCR = os.environ.get('MA_FIXTURE_WORK') or os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(REPO, 'ma_gui', 'web', 'fixtures')
sys.path.insert(0, REPO)

from metaelemzes import tableio, pipeline, plots as P  # noqa: E402

ENGINE_VERSION = '0.2.0'
MINUS = '−'
XSS = '<img src=x onerror=alert(1)>'

# -------------------------------------------------------------------- formázás (a motoré)
def num(v, nd, lang):
    s = '%.*f' % (nd, v)
    if s.startswith('-') and float(s) == 0:
        s = s[1:]
    s = s.replace('-', MINUS)
    if lang == 'hu':
        s = s.replace('.', ',')
    return s


def i18n(fn):
    return {'hu': fn('hu'), 'en': fn('en')}


def same(text):
    return {'hu': text, 'en': text}


def triple(est, lo, hi):
    nd = P.decimals((est, lo, hi))
    return i18n(lambda L: '%s [%s; %s]' % (num(est, nd, L), num(lo, nd, L), num(hi, nd, L)))


def pair(lo, hi):
    nd = P.decimals((lo, hi))
    return i18n(lambda L: '[%s; %s]' % (num(lo, nd, L), num(hi, nd, L)))


def pct(v, nd=0):
    return i18n(lambda L: num(v, nd, L) + '%')


def g3(v, lang):
    s = '%.3g' % v
    s = s.replace('-', MINUS)
    return s.replace('.', ',') if lang == 'hu' else s


def ptext(p, prefix=True):
    if p is None:
        return same('–')
    if p < 0.001:
        return i18n(lambda L: ('p ' if prefix else '') + '< ' + num(0.001, 3, L))
    return i18n(lambda L: ('p = ' if prefix else '') + num(p, 3, L))


def tick_text(v):
    return '%g' % v


def r6(v):
    return None if v is None else float('%.6g' % v) if abs(v) >= 1e-3 or v == 0 else float('%.6g' % v)


def round_floats(o):
    if isinstance(o, float):
        if o != o or o in (float('inf'), float('-inf')):
            return None
        return float('%.7g' % o)
    if isinstance(o, dict):
        return {k: round_floats(v) for k, v in o.items()}
    if isinstance(o, list):
        return [round_floats(v) for v in o]
    return o


# -------------------------------------------------------------------- adatok
# a kinyerés-képernyő fixture-jével (table.json, provenance.json, documents.json) összhangban
ORDER = ['Aronson 1948', 'Ferguson & Simes 1949', 'Rosenthal et al 1960', 'Hart & Sutherland 1977', 'Frimodt-Moller et al 1973',
         'Stein & Aronson 1953', 'Vandiviere et al 1973', 'TPT Madras 1980', 'Coetzee & Berjak 1968', 'Rosenthal et al 1961',
         'Comstock et al 1974', 'Comstock & Webster 1969', 'Comstock et al 1976']
ALLOC = {'random': 'low', 'alternate': 'some', 'systematic': 'high'}
ROB = {}
ESTIMATED = {'Coetzee & Berjak 1968', 'Rosenthal et al 1961'}
UID_OVERRIDE = {lab: 'rbcg%02d' % (i + 1) for i, lab in enumerate(ORDER)}
PAGES = {'Aronson 1948': (3, 'Table 1'), 'Ferguson & Simes 1949': (5, 'Table 2'), 'Rosenthal et al 1960': (2, 'Table 1'),
         'Hart & Sutherland 1977': (6, 'Table 2'), 'Frimodt-Moller et al 1973': (5, 'Table 4'),
         'Stein & Aronson 1953': (4, 'Table 3'), 'Vandiviere et al 1973': (3, 'Table 2'), 'TPT Madras 1980': (9, 'Table 5'),
         'Coetzee & Berjak 1968': (4, 'Fig. 2'), 'Rosenthal et al 1961': (7, 'Figure 2'),
         'Comstock et al 1974': (5, 'Table 3'), 'Comstock & Webster 1969': (2, 'Table 1'), 'Comstock et al 1976': (4, 'Table 2')}
DOCS = {'Aronson 1948': 'file:_privat/pdf/aronson1948.pdf', 'Ferguson & Simes 1949': 'file:_privat/pdf/ferguson1949.pdf',
        'Stein & Aronson 1953': 'file:_privat/pdf/stein1953.pdf', 'Coetzee & Berjak 1968': 'file:_privat/pdf/coetzee1968.pdf',
        'Rosenthal et al 1961': 'doi:10.0000/bcg.rosenthal1961'}


def slug(label):
    s = label.lower().replace('&', '').replace('et al', '')
    out = []
    for ch in s:
        out.append(ch if ch.isalnum() else '-')
    s = '-'.join(x for x in ''.join(out).split('-') if x)
    return s.replace('ø', 'o')


def uid(label, i):
    if label in UID_OVERRIDE:
        return UID_OVERRIDE[label]
    return 'r' + hashlib.sha1(('%s|%d' % (label, i)).encode('utf-8')).hexdigest()[:6]


def base_rows():
    rows = list(csv.reader(open(os.path.join(REPO, 'peldak', 'bcg_oltas_RR.csv'), encoding='utf-8'), delimiter=';'))
    return rows[0], rows[1:]


def write_csv(path, header, data, labels=None, overrides=None):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['row_uid'] + header + ['rob', 'estimated'])
        for i, r in enumerate(data):
            r = list(r)
            lab = r[0]
            if overrides and lab in overrides:
                for col, val in overrides[lab].items():
                    r[header.index(col)] = val
            shown = labels.get(lab, lab) if labels else lab
            r[0] = shown
            w.writerow([uid(lab, i)] + r + [ROB[lab], 'igen' if lab in ESTIMATED else 'nem'])


def run_engine(path, options, exclude=None):
    rows, meta = tableio.read_table(path)
    rep = []
    if exclude:
        rows = tableio.apply_filters(rows, exclude, None, meta=meta, report=rep)
    meta['filters'] = {'exclude': exclude, 'include': None}
    opts = dict(options)
    opts['filter_report'] = rep
    with contextlib.redirect_stdout(io.StringIO()):
        out, es = pipeline.run(rows, opts, meta)
        _, _, data = pipeline.make_plots(out, es)
    out = json.loads(json.dumps(pipeline.to_jsonable(out)))
    data = json.loads(json.dumps(pipeline.to_jsonable(data)))
    return rows, out, es, data


# -------------------------------------------------------------------- tengelyek (a motor _Axis-logikája)
def axis_from(lo, hi, x0=300, x1=640, ratio=True, measure='RR'):
    ax = P._Axis(lo, hi, x0, x1, ratio, measure, None)
    return {'domain': [r6(ax.lo), r6(ax.hi)], 'ticks': [{'at': r6(p), 'text': lab} for p, lab in ax.ticks()]}


def forest_domain(studies, summaries, sec_sums):
    vals = []
    for s in studies:
        vals += [s['lo'], s['hi']]
    for sm in summaries + sec_sums:
        vals += [v for v in (sm.get('ci_lower'), sm.get('ci_upper'), sm.get('pi_lower'), sm.get('pi_upper'))
                 if v is not None and math.isfinite(v)]
    null = 0.0
    vals.append(null)
    lo, hi = min(vals), max(vals)
    core = sorted(vals)
    if len(core) > 6:
        q_lo, q_hi = core[1], core[-2]
        span = q_hi - q_lo
        lo, hi = max(lo, q_lo - span), min(hi, q_hi + span)
    must = [sm['estimate'] for sm in summaries + sec_sums if sm.get('estimate') is not None]
    must.append(null)
    return min([lo] + must), max([hi] + must)


def nice_ticks(lo, hi, n=4):
    return P._nice_ticks(lo, hi, n)


def linear_axis(lo, hi, title, n=4, refs=None):
    ticks = nice_ticks(lo, hi, n)
    return {'domain': [r6(lo), r6(hi)], 'ticks': [{'at': r6(t), 'text': ('%g' % t).replace('-', MINUS)} for t in ticks],
            'title': title, 'refs': refs or []}


def nice_ceiling(v):
    if v <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * mag >= v:
            return m * mag
    return 10 * mag


# -------------------------------------------------------------------- szótárak
MODEL_LABEL = {
    'random': ('Véletlen hatású modell', 'Random-effects model'),
    'fixed': ('Közös (fix) hatású modell', 'Common-effect model'),
    'ivhet': ('IVhet modell (Doi 2015)', 'IVhet model (Doi 2015)'),
}
LFK_CAT = {'nincs aszimmetria': 'no asymmetry', 'kisebb aszimmetria': 'minor asymmetry',
           'jelentős aszimmetria': 'major asymmetry'}
SCALE_NOTE_EN = 'The analysis was done on the log scale; reported values are back-transformed (exponentiated).'
BINARY_NOTE_EN = ('For binary outcomes the classic Egger test is informative only (log OR and its standard error are '
                  'not independent, so it is prone to false positives); the Harbord and Peters tests are recommended '
                  '(Sterne et al. 2011). Both are based on log OR even when the analysis is on the RR scale.')
TRIMFILL_WARN_EN = ("The trim-and-fill adjusted estimate is a sensitivity analysis, not a 'true' effect; it can be "
                    "misleading under heterogeneity.")
INFL_NOTE = {
    'hu': 'Befolyásos (metafor-kritériumok): |DFFITS| > 3·√(p/(k−p)), a Cook-távolság χ²ₚ-eloszlásbeli alsó '
          'farokterülete > 50%, hat > 3·p/k, vagy bármely |DFBETAS| > 1.',
    'en': 'Influential (metafor criteria): |DFFITS| > 3·√(p/(k−p)), lower-tail area of χ²ₚ cut off by Cook\'s '
          'distance > 50%, hat > 3·p/k, or any |DFBETAS| > 1.'}


def model_label(r, kind):
    hu, en = MODEL_LABEL[kind]
    if kind == 'random':
        extra = '%s, %s' % (r['tau2_method'], r['ci_method'].upper())
        return {'hu': '%s (%s)' % (hu, extra), 'en': '%s (%s)' % (en, extra)}
    if kind == 'fixed' and r.get('ci_method') not in (None, 'z'):
        return {'hu': '%s (%s)' % (hu, r['ci_method'].upper()), 'en': '%s (%s)' % (en, r['ci_method'].upper())}
    return {'hu': hu, 'en': en}


def i2_text(h):
    lo, hi = h['I2_ci_HT']
    return i18n(lambda L: '%s%% [%s; %s]' % (num(h['I2'], 0, L), num(lo, 0, L), num(hi, 0, L)))


def summary_obj(r, kind, sid, primary_kind):
    exp = math.exp
    s = {'id': sid, 'kind': 'overall', 'model': kind, 'label': model_label(r, kind),
         'k': r['k'], 'estimate': r6(r['estimate']), 'ci_lower': r6(r['ci_lower']), 'ci_upper': r6(r['ci_upper']),
         'pi_lower': r6(r.get('pi_lower')), 'pi_upper': r6(r.get('pi_upper')),
         'display': {'est': r6(exp(r['estimate'])), 'lo': r6(exp(r['ci_lower'])), 'hi': r6(exp(r['ci_upper']))},
         'display_text': triple(exp(r['estimate']), exp(r['ci_lower']), exp(r['ci_upper'])),
         'p_text': ptext(r['p']), 'primary': kind == primary_kind}
    if r.get('pi_lower') is not None:
        s['pi_text'] = pair(exp(r['pi_lower']), exp(r['pi_upper']))
        s['pi_label'] = {'hu': 'Predikciós intervallum (t, k − 2 = %d)' % r['pi_df'],
                         'en': 'Prediction interval (t, k − 2 = %d)' % r['pi_df']} if r.get('pi_method') == 't_k-2' else \
            {'hu': 'Predikciós intervallum', 'en': 'Prediction interval'}
    if kind in ('random', 'ivhet'):
        s['het_text'] = i18n(lambda L: 'τ² = %s; I² = %s%%' % (g3(r['tau2'], L), num(r['I2'], 0, L)))
    else:
        s['het_text'] = i18n(lambda L: 'I² = %s%%' % num(r['I2'], 0, L))
    return s


# -------------------------------------------------------------------- plot/v2 építése
def build_plot(rows, out, es, data, run_id, data_sha, spec_sha, labels_by_uid, uid_by_label, docs):
    exp = math.exp
    measure = 'RR'
    primary_kind = out['primary_model']
    prim = out['primary']
    level = out['options']['level']
    # összesítések
    summaries = []
    for kind in ('ivhet', 'random', 'fixed'):
        r = out.get(kind)
        if r is None:
            continue
        summaries.append(summary_obj(r, kind, 'overall_' + kind, primary_kind))
    summaries.sort(key=lambda s: 0 if s['primary'] else 1)
    # kiugró-szűrés → érzékenységi összesítés
    outl = (out.get('sensitivity') or {}).get('outliers')
    outlier_labels = set()
    if outl:
        outlier_labels = set(outl.get('flagged') or [])
        ref = outl.get('refit')
        if ref:
            s = summary_obj(ref, ref.get('model', 'random'), 'sens_outliers', None)
            s['kind'] = 'sensitivity'
            s['primary'] = False
            s['label'] = {'hu': 'Kiugrók nélkül (k = %d; %s)' % (ref['k'], ', '.join(sorted(outlier_labels))),
                          'en': 'Without outliers (k = %d; %s)' % (ref['k'], ', '.join(sorted(outlier_labels)))}
            summaries.append(s)
    # vizsgálatok
    weights = prim['weights_pct']
    wfix = out['fixed']['weights_pct'] if out.get('fixed') else [None] * len(weights)
    z = 1.959963984540054
    infl = {d['study']: d for d in (out.get('sensitivity') or {}).get('influence') or []}
    labels = list(es.labels)
    sec_of = {}
    sections = []
    sg = out.get('subgroups')
    sec_sums = []
    if sg:
        for g in sg['groups']:
            idx = g['indices']
            uids = [uid_by_label[labels[i]] for i in idx]
            for u in uids:
                sec_of[u] = str(g['group'])
            sm = {'id': 'sub_' + str(g['group']), 'kind': 'subgroup', 'model': g.get('model', 'random'),
                  'k': g['k'],
                  'label': {'hu': 'Alcsoport összesen (k = %d)' % g['k'], 'en': 'Subtotal (k = %d)' % g['k']},
                  'estimate': r6(g['estimate']), 'ci_lower': r6(g['ci_lower']), 'ci_upper': r6(g['ci_upper']),
                  'display': {'est': r6(exp(g['estimate'])), 'lo': r6(exp(g['ci_lower'])), 'hi': r6(exp(g['ci_upper']))},
                  'display_text': triple(exp(g['estimate']), exp(g['ci_lower']), exp(g['ci_upper'])),
                  'p_text': ptext(g.get('p'))}
            if g['k'] > 1:
                if out['options'].get('model') == 'fixed':
                    sm['het_text'] = i18n(lambda L, g=g: 'I² = %s%%' % num(g['I2'], 0, L))
                else:
                    sm['het_text'] = i18n(lambda L, g=g: 'τ² = %s; I² = %s%%' % (g3(g['tau2'], L), num(g['I2'], 0, L)))
            sec_sums.append(sm)
            sections.append({'id': str(g['group']), 'title': same(str(g['group'])), 'row_uids': uids, 'summary': sm})
    studies = []
    for i, lab in enumerate(labels):
        y = es.yi[i]
        se = math.sqrt(es.vi[i])
        lo, hi = y - z * se, y + z * se
        studies.append({'lo': lo, 'hi': hi})
    # a forest_svg tengelylogikája; az alcsoport-összesítéseknek csak a pontbecslése kötelező (a k = 2-es
    # HKSJ-CI ne nyomja össze az ábrát — a levágott gyémánt végén nyíl jelzi a folytatást)
    dlo, dhi = forest_domain(studies, [dict(s) for s in summaries if s['kind'] == 'overall'],
                             [{'estimate': s['estimate']} for s in sec_sums])
    axis = axis_from(dlo, dhi)
    axis['title'] = {'hu': 'Relatív kockázat (RR, log-skála)', 'en': 'Risk ratio (RR, log scale)'}
    d0, d1 = axis['domain']
    st_out = []
    order = []
    if sections:
        for sec in sections:
            order += sec['row_uids']
    else:
        order = [uid_by_label[l] for l in labels]
    idx_by_uid = {uid_by_label[l]: i for i, l in enumerate(labels)}
    for u in order:
        i = idx_by_uid[u]
        lab = labels[i]
        y = es.yi[i]
        se = math.sqrt(es.vi[i])
        lo, hi = y - z * se, y + z * se
        row = es.rows[i] if hasattr(es, 'rows') else rows[i]
        e1, n1, e2, n2 = (int(float(row[k])) for k in ('e1', 'n1', 'e2', 'n2'))
        base_label = labels_by_uid[u]
        page, loc = PAGES[base_label]
        inf = infl.get(lab, {})
        studies_entry = {
            'row_uid': u, 'row_index': int(es.row_index[i]) if hasattr(es, 'row_index') else i,
            'study_id': slug(base_label), 'label': lab, 'section': sec_of.get(u),
            'y': r6(y), 'lo': r6(lo), 'hi': r6(hi), 'se': r6(se),
            'weight_pct': r6(weights[i]), 'weight_fixed_pct': r6(wfix[i]) if wfix[i] is not None else None,
            'weight_text': pct(weights[i], 1),
            'cells': {'ev_trt': '%d/%d' % (e1, n1), 'ev_ctl': '%d/%d' % (e2, n2)},
            'display': {'est': r6(exp(y)), 'lo': r6(exp(lo)), 'hi': r6(exp(hi))},
            'display_text': triple(exp(y), exp(lo), exp(hi)),
            'clip': {'left': lo < d0, 'right': hi > d1},
            'flags': {'estimated': base_label in ESTIMATED, 'rob': ROB[base_label], 'zero_cell_corrected': False,
                      'influential': bool(inf.get('influential')), 'outlier': lab in outlier_labels},
            'source': {'doc': docs[base_label], 'page': page, 'locator': loc},
        }
        st_out.append(studies_entry)
    # heterogenitás
    h = prim['heterogeneity']
    het = {'Q': r6(prim['Q']), 'df': prim['Q_df'], 'p': prim['p_Q'], 'I2': r6(prim['I2']), 'tau2': r6(prim['tau2']),
           'i2_text': i2_text(h), 'q_text': i18n(lambda L: '%s (df = %d)' % (num(prim['Q'], 2, L), prim['Q_df'])),
           'p_text': ptext(prim['p_Q'])}
    if primary_kind in ('random', 'ivhet'):
        het['tau2_text'] = i18n(lambda L: g3(prim['tau2'], L))
        tq = h.get('tau2_ci_QP')
        if tq:
            het['tau2_ci_text'] = pair(tq[0], tq[1])
    else:
        het['tau2_text'] = same('–')
    het['text'] = {
        'hu': 'Heterogenitás: Q = %s (df = %d; %s); I² = %s; τ² = %s' % (
            num(prim['Q'], 2, 'hu'), prim['Q_df'], ptext(prim['p_Q'])['hu'], het['i2_text']['hu'], het['tau2_text']['hu']),
        'en': 'Heterogeneity: Q = %s (df = %d, %s); I² = %s; τ² = %s' % (
            num(prim['Q'], 2, 'en'), prim['Q_df'], ptext(prim['p_Q'])['en'], het['i2_text']['en'], het['tau2_text']['en'])}
    sgt = None
    if sg and sg.get('Q_between') is not None:
        sgt = {'Q': r6(sg['Q_between']), 'df': sg['df_between'], 'p': r6(sg['p_between']),
               'text': {'hu': 'Alcsoport-különbség: Q = %s; df = %d; %s' % (num(sg['Q_between'], 2, 'hu'), sg['df_between'], ptext(sg['p_between'])['hu']),
                        'en': 'Test for subgroup differences: Q = %s, df = %d, %s' % (num(sg['Q_between'], 2, 'en'), sg['df_between'], ptext(sg['p_between'])['en'])},
               'note': {'hu': sg.get('Q_between_note') or '', 'en': 'Q_between from the Wald standard errors of the subgroup estimates (not HKSJ), χ²(G−1) distribution.'}}
    # funnel
    fu = data['funnel']
    yi, sei = fu['yi'], fu['sei']
    fy, fs = fu.get('filled_yi') or [], fu.get('filled_sei') or []
    maxse = max(list(sei) + list(fs)) * 1.08
    center = fu['center']
    nullc = 0.0
    allx = list(yi) + list(fy) + [center + 1.96 * maxse, center - 1.96 * maxse, nullc + 2.576 * maxse, nullc - 2.576 * maxse]
    fax = axis_from(min(allx), max(allx), 70, 610)
    fax['title'] = axis['title']
    bias = out.get('bias') or {}
    tf = bias.get('trimfill')
    filled = []
    if tf:
        for j, (x, s) in enumerate(zip(fy, fs)):
            src = tf['filled_labels'][j].replace('kitöltött: ', '')
            filled.append({'x': r6(x), 'se': r6(s), 'mirror_of': uid_by_label.get(src),
                           'label': {'hu': 'pótolt (trim-and-fill): ' + src, 'en': 'filled (trim-and-fill): ' + src}})
    contours = []
    for p_, zc, band in ((0.10, 1.645, ('p > 0,10', 'p > 0.10')), (0.05, 1.96, ('0,05 < p < 0,10', '0.05 < p < 0.10')),
                         (0.01, 2.576, ('0,01 < p < 0,05', '0.01 < p < 0.05'))):
        contours.append({'p': p_, 'z': zc, 'polygon': [[nullc, 0.0], [r6(nullc - zc * maxse), r6(maxse)], [r6(nullc + zc * maxse), r6(maxse)]],
                         'band_text': {'hu': band[0], 'en': band[1]}})
    parts_hu, parts_en = [], []
    for key, nm in (('harbord', 'Harbord'), ('peters', 'Peters'), ('egger', 'Egger')):
        t = bias.get(key)
        if t and t.get('p') is not None:
            ph, pe = ptext(t['p'])['hu'], ptext(t['p'])['en']
            if key == 'egger':
                parts_hu.append('%s %s (tájékoztató)' % (nm, ph))
                parts_en.append('%s %s (informative only)' % (nm, pe))
            else:
                parts_hu.append('%s %s' % (nm, ph))
                parts_en.append('%s %s' % (nm, pe))
    if bias.get('begg') and bias['begg'].get('p') is not None:
        parts_hu.append('Begg %s' % ptext(bias['begg']['p'])['hu'])
        parts_en.append('Begg %s' % ptext(bias['begg']['p'])['en'])
    if tf:
        side = {'right': ('jobb oldal', 'right side'), 'left': ('bal oldal', 'left side')}.get(tf['side'], (tf['side'], tf['side']))
        parts_hu.append('trim-and-fill: k0 = %d (%s)' % (tf['k0'], side[0]))
        parts_en.append('trim-and-fill: k0 = %d (%s)' % (tf['k0'], side[1]))
    funnel = {
        'points': [{'row_uid': uid_by_label[labels[i]], 'x': r6(yi[i]), 'se': r6(sei[i])} for i in range(len(yi))],
        'filled': filled, 'center': r6(center), 'center_label': {'hu': 'közös hatású becslés', 'en': 'common-effect estimate'},
        'se_max': r6(maxse),
        'pseudo_ci': [[r6(center - 1.96 * maxse), r6(maxse)], [r6(center), 0.0], [r6(center + 1.96 * maxse), r6(maxse)]],
        'contour_center': nullc, 'contours': contours,
        'outside_text': {'hu': 'p < 0,01', 'en': 'p < 0.01'},
        'tests_text': {'hu': ' · '.join(parts_hu), 'en': ' · '.join(parts_en)},
        'axis': fax,
        'y_axis': linear_axis(0.0, maxse, {'hu': 'Standard hiba (fordított)', 'en': 'Standard error (inverted)'}),
    }
    if tf:
        adj = tf['adjusted']
        funnel['trimfill_text'] = {
            'hu': 'Trim-and-fill (%s): k0 = %d; korrigált becslés %s' % (tf['estimator'], tf['k0'], triple(exp(adj['estimate']), exp(adj['ci_lower']), exp(adj['ci_upper']))['hu']),
            'en': 'Trim-and-fill (%s): k0 = %d; adjusted estimate %s' % (tf['estimator'], tf['k0'], triple(exp(adj['estimate']), exp(adj['ci_lower']), exp(adj['ci_upper']))['en'])}
    # doi
    doi = None
    lf = bias.get('lfk')
    if lf:
        ys = [p['y'] for p in lf['points']]
        zs = [p['abs_z'] for p in lf['points']]
        dax = axis_from(min(ys), max(ys), 70, 610)
        dax['title'] = axis['title']
        cat_en = LFK_CAT.get(lf['category'], lf['category'])
        doi = {'points': [{'row_uid': uid_by_label[labels[p['study_index']]], 'x': r6(p['y']), 'abs_z': r6(p['abs_z'])} for p in lf['points']],
               'lfk': r6(lf['lfk']), 'category': {'hu': lf['category'], 'en': cat_en},
               'lfk_text': {'hu': 'LFK-index: %s (%s)' % (num(lf['lfk'], 2, 'hu'), lf['category']),
                            'en': 'LFK index: %s (%s)' % (num(lf['lfk'], 2, 'en'), cat_en)},
               'note': {'hu': 'Heurisztikus mutató (|LFK| ≤ 1 nincs, 1–2 kisebb, > 2 jelentős aszimmetria) — nem szignifikancia-teszt.',
                        'en': 'Heuristic index (|LFK| ≤ 1 none, 1–2 minor, > 2 major asymmetry) — not a significance test.'},
               'axis': dax,
               'y_axis': linear_axis(0.0, max(zs + [1.0]) * 1.08, {'hu': '|Z| (fordított, 0 felül)', 'en': '|Z| (inverted, 0 at top)'})}
    # LOO
    sens = out.get('sensitivity') or {}
    loo = []
    lvals = []
    for e in sens.get('leave_one_out') or []:
        u = uid_by_label[e['omitted']]
        loo.append({'omitted_row_uid': u, 'label': e['omitted'], 'estimate': r6(e['estimate']), 'ci_lower': r6(e['ci_lower']),
                    'ci_upper': r6(e['ci_upper']),
                    'display_text': triple(exp(e['estimate']), exp(e['ci_lower']), exp(e['ci_upper'])),
                    'i2_text': pct(e['I2']), 'tau2_text': i18n(lambda L, e=e: g3(e['tau2'], L)), 'p_text': ptext(e['p'])})
        lvals += [e['ci_lower'], e['ci_upper']]
    loo_axis = None
    if loo:
        ov = summaries[0]
        lvals += [ov['ci_lower'], ov['ci_upper'], 0.0]
        loo_axis = axis_from(min(lvals), max(lvals), 300, 640)
        loo_axis['title'] = axis['title']
    # kumulatív
    cum = None
    if sens.get('cumulative'):
        ent = []
        cvals = []
        for e in sens['cumulative']:
            ent.append({'added_row_uid': uid_by_label[e['added']], 'label': e['added'],
                        'key_text': ('%g' % e['key']) if isinstance(e['key'], (int, float)) else str(e['key']),
                        'k': e['k'], 'estimate': r6(e['estimate']), 'ci_lower': r6(e['ci_lower']), 'ci_upper': r6(e['ci_upper']),
                        'display_text': triple(exp(e['estimate']), exp(e['ci_lower']), exp(e['ci_upper'])),
                        'i2_text': pct(e['I2']) if e['k'] > 1 else same('–')})
            cvals.append({'lo': e['ci_lower'], 'hi': e['ci_upper']})
        clo, chi = forest_domain(cvals, [summaries[0]], [])
        cax = axis_from(clo, chi, 300, 640)
        cax['title'] = axis['title']
        cum = {'key_label': {'hu': 'év', 'en': 'year'}, 'entries': ent, 'axis': cax}
    # befolyás
    influence = []
    k = len(labels)
    p_ = 1
    for lab in labels:
        d = infl.get(lab)
        if not d:
            continue
        e = {'row_uid': uid_by_label[lab], 'label': lab}
        for key, nd in (('rstudent', 2), ('dffits', 2), ('cook_d', 3), ('cov_ratio', 3), ('hat', 3), ('dfbetas', 2)):
            v = d.get(key)
            e[key] = r6(v)
            e[key + '_text'] = i18n(lambda L, v=v, nd=nd: num(v, nd, L)) if v is not None else same('–')
        e['influential'] = bool(d.get('influential'))
        e['outlier'] = lab in outlier_labels
        e['decimals'] = 2
        influence.append(e)
    infl_axes = None
    if influence:
        rmax = max(abs(e['rstudent']) for e in influence)
        rlim = max(2.0, nice_ceiling(rmax * 1.05))
        cook_ref = 0.4549364231195724            # qchisq(0.5, df = p = 1)
        hat_ref = 3.0 * p_ / k
        cmax = nice_ceiling(max(max(e['cook_d'] for e in influence), cook_ref) * 1.05)
        hmax = nice_ceiling(max(max(e['hat'] for e in influence), hat_ref) * 1.05)
        infl_axes = {
            'rstudent': linear_axis(-rlim, rlim, {'hu': 'Studentizált reziduum (rstudent)', 'en': 'Studentized residual (rstudent)'}, 4,
                                    [{'at': 0.0, 'text': same('0')}]),
            'cook_d': linear_axis(0.0, cmax, {'hu': 'Cook-távolság', 'en': "Cook's distance"}, 4,
                                  [{'at': r6(cook_ref), 'text': {'hu': 'χ²₁ medián = ' + num(cook_ref, 3, 'hu'), 'en': 'χ²₁ median = ' + num(cook_ref, 3, 'en')}}]),
            'hat': linear_axis(0.0, hmax, {'hu': 'hat (súly)', 'en': 'hat (weight)'}, 4,
                               [{'at': r6(hat_ref), 'text': i18n(lambda L: '3p/k = ' + num(hat_ref, 3, L))}]),
        }
    infl_summary = None
    if influence:
        flagged = [e['label'] for e in influence if e['influential']]
        infl_summary = {'hu': ('Befolyásos vizsgálat (metafor-kritériumok): ' + (', '.join(flagged) if flagged else 'nincs') + '.'),
                        'en': ('Influential studies (metafor criteria): ' + (', '.join(flagged) if flagged else 'none') + '.')}
    notes = []
    bt = out.get('back_transformed') or {}
    if bt.get('scale_note'):
        notes.append({'hu': bt['scale_note'], 'en': SCALE_NOTE_EN})
    if bias.get('binary_note'):
        notes.append({'hu': bias['binary_note'], 'en': BINARY_NOTE_EN})
    if tf and tf.get('warnings'):
        notes.append({'hu': tf['warnings'][0], 'en': TRIMFILL_WARN_EN})
    plot = {
        'schema': 'szk.ma.plot/v2',
        'meta': {'engine_version': ENGINE_VERSION, 'run_id': run_id, 'data_sha256': data_sha, 'spec_sha256': spec_sha},
        'measure': measure,
        'scale': {'analysis': 'log', 'ratio': True, 'null_analysis': 0.0, 'null_display': 1.0},
        'level': level,
        'axis': axis,
        'labels': {'left': {'hu': 'BCG jobb', 'en': 'Favours BCG'}, 'right': {'hu': 'kontroll jobb', 'en': 'Favours control'},
                   'title': None, 'study': {'hu': 'Vizsgálat', 'en': 'Study'},
                   'effect': {'hu': 'RR [95% CI]', 'en': 'RR [95% CI]'}, 'weight': {'hu': 'Súly', 'en': 'Weight'}},
        'columns': [{'id': 'ev_trt', 'title': {'hu': 'Esem./N (I)', 'en': 'Events/N (I)'}},
                    {'id': 'ev_ctl', 'title': {'hu': 'Esem./N (K)', 'en': 'Events/N (C)'}}],
        'studies': st_out,
        'sections': sections or None,
        'summaries': summaries,
        'subgroup_test': sgt,
        'heterogeneity': het,
        'funnel': funnel,
        'doi': doi,
        'loo': loo,
        'loo_axis': loo_axis,
        'influence': influence,
        'influence_axes': infl_axes,
        'influence_text': infl_summary,
        'influence_note': INFL_NOTE,
        'cumulative': cum,
        'bubble': None,
        'notes': notes,
    }
    return round_floats(plot)


# -------------------------------------------------------------------- futtatás
def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def main():
    header, data = base_rows()
    for r in data:
        ROB[r[0]] = ALLOC[r[header.index('allokáció')]]
    labels0 = [r[0] for r in data]
    uid_by_base = {lab: uid(lab, i) for i, lab in enumerate(labels0)}
    docs = {lab: DOCS.get(lab) for lab in labels0}
    full = os.path.join(SCR, 'bcg_full.csv')
    write_csv(full, header, data)
    stale_csv = os.path.join(SCR, 'bcg_stale.csv')
    xss_label = 'Comstock et al 1976 ' + XSS
    write_csv(stale_csv, header, data, labels={'Comstock et al 1976': xss_label},
              overrides={'Aronson 1948': {'esemény1': '6'}})
    base_opts = {'measure': 'RR', 'subgroup': 'allokáció', 'cumulative': 'év'}
    variants = {
        'primary': (full, dict(base_opts), None),
        'stale': (stale_csv, dict(base_opts), None),
        'no_estim': (full, dict(base_opts), ['estimated=igen']),
        'no_rob': (full, dict(base_opts), ['rob=high']),
        'fixed': (full, dict(base_opts, model='fixed'), None),
        'dl': (full, dict(base_opts, tau2='DL'), None),
        'outliers': (full, dict(base_opts, outliers=True), None),
    }
    run_ids = {
        'primary': '20261004T211200Z-a1f3c2', 'stale': '20261003T180200Z-4c0d1e', 'no_estim': '20261004T211500Z-c9d2a7',
        'no_rob': '20261005T091600Z-a7b8c9', 'fixed': '20261005T091500Z-f1e2d3', 'dl': '20261005T091500Z-d1a2b3',
        'outliers': '20261005T091600Z-0f1e2d',
    }
    data_sha = {'full': '9f3abbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                'stale': '4c0dcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'}
    plots = {}
    outs = {}
    for name, (path, opts, excl) in variants.items():
        rows, out, es, pdata = run_engine(path, opts, excl)
        labels_by_uid = {}
        uid_by_label = {}
        for lab in es.labels:
            base = lab if lab in uid_by_base else lab.replace(' ' + XSS, '')
            u = uid_by_base[base]
            labels_by_uid[u] = base
            uid_by_label[lab] = u
        dsha = data_sha['stale' if name == 'stale' else 'full']
        plot = build_plot(rows, out, es, pdata, run_ids[name], dsha, sha('spec:' + name), labels_by_uid, uid_by_label, docs)
        plots[name] = plot
        outs[name] = out
        prim = out['primary']
        print('%-9s k=%2d %s  I2=%.1f tau2=%.4f PI=%s outliers=%s infl=%s' % (
            name, prim['k'], plot['summaries'][0]['display_text']['en'], prim['I2'], prim['tau2'],
            plot['summaries'][0].get('pi_text', {}).get('en'),
            [s['label'] for s in plot['studies'] if s['flags']['outlier']],
            [s['label'] for s in plot['studies'] if s['flags']['influential']]))
    json.dump({'plots': plots, 'outs': {k: {'primary': v['primary'], 'totals': v.get('totals'), 'validation': v.get('validation'),
                                             'options': v.get('options'), 'input': v.get('input')} for k, v in outs.items()},
               'run_ids': run_ids, 'uid_by_base': uid_by_base, 'docs': docs},
              open(os.path.join(SCR, 'gen_state.json'), 'w', encoding='utf-8'), ensure_ascii=False)


if __name__ == '__main__':
    main()
