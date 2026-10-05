#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A gen_fixtures.py állapotából (gen_state.json) megírja a ma_gui/web/fixtures/analysis_*.json fájlokat."""
import json
import os
import subprocess
import hashlib

REPO = str(__import__('pathlib').Path(__file__).resolve().parents[4])
SCR = os.environ.get('MA_FIXTURE_WORK') or os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(REPO, 'ma_gui', 'web', 'fixtures')
ST = json.load(open(os.path.join(SCR, 'gen_state.json'), encoding='utf-8'))
PLOTS = ST['plots']
OUTS = ST['outs']
RUN_IDS = ST['run_ids']
UIDS = ST['uid_by_base']
DOCS = ST['docs']
ENGINE = json.load(open(os.path.join(OUT, 'engine.json'), encoding='utf-8'))['routes'][0]['envelope']['data']
OPT_META = ENGINE['options']

_req = [0]


def meta(ms=1):
    _req[0] += 1
    return {'engine': '0.2.0', 'elapsed_ms': ms, 'project_rev': 41, 'request_id': 'q_an%02d' % _req[0]}


def env(schema, data, warnings=None, ms=1):
    return {'ok': True, 'schema': schema, 'data': data, 'warnings': warnings or [], 'meta': meta(ms)}


def err(code, http, message, details=None):
    e = {'code': code, 'http': http, 'message': message}
    if details is not None:
        e['details'] = details
    return {'ok': False, 'error': e}


def dump(name, obj, compact=False):
    with open(os.path.join(OUT, name), 'w', encoding='utf-8', newline='\n') as f:
        if compact:
            # egy útvonal soronként (a nagy nézetmodellek miatt tömören)
            f.write('{"description": %s,\n "routes": [\n' % json.dumps(obj['description'], ensure_ascii=False))
            f.write(',\n'.join('  ' + json.dumps(r, ensure_ascii=False, separators=(',', ':')) for r in obj['routes']))
            f.write('\n ]}\n')
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.write('\n')
    print('%-28s %7d bájt' % (name, os.path.getsize(os.path.join(OUT, name))))


def sha(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


# -------------------------------------------------------------------- spec-ek
def default_options():
    return {k: v['default'] for k, v in OPT_META.items()}


def spec(name, purpose='primary', parent=None, exclude=None, **opts):
    o = default_options()
    o.update({'measure': 'RR', 'subgroup': 'allokáció', 'cumulative': 'év'})
    o.update(opts)
    return {
        'schema': 'szk.ma.analysis-spec/v1', 'name': name, 'outcome': 'o1', 'purpose': purpose,
        'prespecified': purpose == 'primary', 'protocol_ref': 'protokoll 9.2' if purpose == 'primary' else None,
        'parent': parent, 'data': {'path': '03_adatok/o1.csv'}, 'options': o,
        'filters': {'include': [], 'exclude': exclude or []},
        'kb_refs': ['D-S08-001', 'D-S09-007', 'D-S12-005'] if purpose == 'primary' else ['D-S12-003'],
    }


SPEC_PRIMARY = spec('o1_primary')
SPEC_NO_ESTIM = spec('o1_primary_no_estim', purpose='sensitivity', parent='o1_primary', exclude=['estimated=igen'])

# -------------------------------------------------------------------- futás-leírók
CLI = {k: v.get('cli') for k, v in OPT_META.items()}


def expanded_argv(sp):
    argv = ['ma.py', 'analyze', '--data', sp['data']['path']]
    d = default_options()
    for k, v in sp['options'].items():
        flag = CLI.get(k)
        if not flag:
            continue
        if k == 'measure':
            argv += [flag, v]
            continue
        if v == d.get(k):
            continue
        if isinstance(v, bool):
            if v:
                argv.append(flag)
        elif isinstance(v, list):
            if v:
                argv += [flag, ','.join(v)]
        elif v is not None:
            argv += [flag, str(v)]
    for e in sp['filters'].get('exclude') or []:
        argv += ['--exclude', e]
    for e in sp['filters'].get('include') or []:
        argv += ['--include', e]
    return argv


def run_desc(variant, sp, started, stale=False, data_sha=None):
    rid = RUN_IDS[variant]
    p = PLOTS[variant]
    o = OUTS[variant]
    ov = p['summaries'][0]
    out_dir = '05_elemzes/o1/%s' % rid
    spath = '05_elemzes/specs/%s.json' % sp['name']
    tot = o.get('totals') or {}
    part = tot.get('participants')
    files = {}
    for key, fn in (('results', 'results.json'), ('plot', 'plot_data.json'), ('report', 'report.md'),
                    ('forest_svg', 'forest.svg'), ('funnel_svg', 'funnel.svg'), ('doi_svg', 'doi.svg')):
        files[key] = {'path': '%s/%s' % (out_dir, fn), 'sha256': sha(rid + fn)}
    vs = (o.get('validation') or {}).get('summary') or {'error': 0, 'warning': 0, 'info': 0}
    return {
        'schema': 'szk.ma.run/v1', 'run_id': rid, 'mode': 'commit', 'outcome_id': 'o1',
        'spec': {'path': spath, 'sha256': p['meta']['spec_sha256'], 'name': sp['name'], 'parent': sp['parent'],
                 'purpose': sp['purpose']},
        'equivalent_argv': ['ma.py', 'analyze', '--spec', spath, '--out', out_dir, '--project', '.'],
        'expanded_argv': expanded_argv(sp) + ['--out', out_dir],
        'engine_version': '0.2.0',
        'data': {'path': '03_adatok/o1.csv', 'sha256': data_sha or p['meta']['data_sha256'], 'rows': 13},
        'files': files,
        'k': o['primary']['k'],
        'primary': {'model': o['primary']['model'], 'display_text': ov['display_text'],
                    'i2_text': {'hu': p['heterogeneity']['i2_text']['hu'].split(' ')[0], 'en': p['heterogeneity']['i2_text']['en'].split(' ')[0]},
                    'pi_text': ov.get('pi_text'), 'tau2_text': p['heterogeneity'].get('tau2_text')},
        'measure': 'RR',
        'participants_text': {'hu': '{:,}'.format(part).replace(',', ' '), 'en': '{:,}'.format(part)} if part else None,
        'rob_high': sum(1 for s in p['studies'] if s['flags']['rob'] == 'high'),
        'stale': stale,
        'validation_summary': vs,
        'client_seq': None,
        'elapsed_ms': 84,
        'started': started, 'finished': started,
    }


RUN_PRIMARY = run_desc('primary', SPEC_PRIMARY, '2026-10-04T21:12:00Z')
RUN_PRIMARY['client_seq'] = 57
RUN_CHILD = run_desc('no_estim', SPEC_NO_ESTIM, '2026-10-04T21:15:00Z')
RUN_STALE = run_desc('stale', SPEC_PRIMARY, '2026-10-03T18:02:00Z', stale=True)
RUN_STALE['client_seq'] = 12
SPEC_VARIANTS = {
    'no_rob': spec('o1_primary_no_rob_high', purpose='sensitivity', parent='o1_primary', exclude=['rob=high']),
    'fixed': spec('o1_primary_fixed', purpose='sensitivity', parent='o1_primary', model='fixed'),
    'dl': spec('o1_primary_dl', purpose='sensitivity', parent='o1_primary', tau2='DL'),
    'outliers': spec('o1_primary_no_outliers', purpose='sensitivity', parent='o1_primary', outliers=True),
}
VARIANT_RUNS = {'primary': RUN_PRIMARY, 'stale': RUN_STALE, 'no_estim': RUN_CHILD}
for _v, _sp in SPEC_VARIANTS.items():
    VARIANT_RUNS[_v] = run_desc(_v, _sp, '2026-10-05T09:16:00Z')

# -------------------------------------------------------------------- analysis_plots.json
plot_routes = []
for variant, desc in (('primary', 'elsődleges commit (AKTUÁLIS)'), ('stale', 'régi commit (ELAVULT, X001; a régi adatban Aronson 1948 e1 = 6 és egy HTML-darabot tartalmazó címke — DOM-biztonsági teszt)'),
                      ('no_estim', 'gyermek: becsült sorok nélkül (exclude estimated=igen)'), ('no_rob', 'gyermek: magas RoB nélkül (exclude rob=high)'),
                      ('fixed', 'gyermek: közös (fix) hatás — 3 befolyásos vizsgálat (metafor-kritériumok)'), ('dl', 'gyermek: DL τ²'),
                      ('outliers', 'gyermek: kiugrók nélkül (--outliers; dmetar find.outliers)')):
    plot_routes.append({'method': 'GET', 'path': '/api/runs/%s/plot' % RUN_IDS[variant], '_variant': variant,
                        'envelope': env('szk.ma.plot/v2', PLOTS[variant], ms=3)})
# ismeretlen futás-azonosító (pl. egy most rögzített elsődleges futás) → az elsődleges nézetmodell
fallback = json.loads(json.dumps(PLOTS['primary']))
fallback['meta']['run_id'] = None
plot_routes.append({'method': 'GET', 'path': '/api/runs/*/plot', '_variant': 'primary',
                    'envelope': env('szk.ma.plot/v2', fallback, ms=3)})
for r in plot_routes:
    r.pop('_variant')
dump('analysis_plots.json', {
    'description': 'GET /api/runs/<run_id>/plot — szk.ma.plot/v2 (4.6) a BCG-példán (13 vizsgálat, RR, REML + HKSJ, alcsoport: allokáció, kumulatív: év). '
                   'A számok a motorból (metaelemzes.pipeline; a metafor 4.4-gyel 1e-5-ön belül egyeztetve: becslés, CI (HKSJ), PI (t, k−2), τ², LOO, kumulatív, alcsoportok, Q_between; a befolyás-diagnosztika a metafor influence() Wald-modellen számolt értékeivel egyezik), '
                   'a szövegek a motor fmt_triple-logikájával (HU tizedesvessző, U+2212). Változatok: elsődleges, elavult, és a gyermek-futások (becsült nélkül, magas RoB nélkül, fix hatás, DL, kiugrók nélkül). '
                   'Kiegészítő mezők a szerződés fölött (a felület olvassa, ha van): studies[].weight_text, summaries[].p_text/pi_label, '
                   'heterogeneity.i2_text/tau2_text/q_text, funnel.axis/y_axis/contours[].band_text/outside_text, doi.axis/y_axis, '
                   'loo_axis, cumulative.axis, influence[].<mező>_text, influence_axes, influence_text.',
    'routes': plot_routes}, compact=True)

# -------------------------------------------------------------------- analysis_runs.json
dump('analysis_runs.json', {
    'description': 'GET /api/runs?outcome=<id> — egy kimenet commit-futásai (szk.ma.run/v1 + szerveroldali mezők: outcome_id, stale (X001), '
                   'participants_text, rob_high, primary.i2_text/pi_text, expanded_argv). A ?primary=1 változat a runs.json-ban. '
                   'GET /api/runs/<run_id> — egy futás leírója (a dev-fixture ebből építi a gyermek-futások commit-eredményét).',
    'routes': [
        {'method': 'GET', 'path': '/api/runs', 'query': {'outcome': 'o1'},
         'envelope': env('szk.ma.runs/v1', {'runs': [RUN_PRIMARY, RUN_CHILD, RUN_STALE]})},
        {'method': 'GET', 'path': '/api/runs', 'query': {'outcome': 'o2'},
         'envelope': env('szk.ma.runs/v1', {'runs': []})},
    ] + [{'method': 'GET', 'path': '/api/runs/%s' % RUN_IDS[v], 'envelope': env('szk.ma.run/v1', d)} for v, d in VARIANT_RUNS.items()]})

# -------------------------------------------------------------------- analysis_specs.json
dump('analysis_specs.json', {
    'description': 'GET /api/specs/<név> — szk.ma.analysis-spec/v1 (4.4; az options PONTOSAN a motor DEFAULTS-kulcsai, a /api/engine '
                   'opció-metaadatából). PUT/új spec: dinamikus fixture (src/dev/analysis_fixtures.js). Ismeretlen név → 404.',
    'routes': [
        {'method': 'GET', 'path': '/api/specs/o1_primary', 'etag': '"spec-o1_primary-1"',
         'envelope': env('szk.ma.analysis-spec/v1', SPEC_PRIMARY)},
        {'method': 'GET', 'path': '/api/specs/o1_primary_no_estim', 'etag': '"spec-o1_primary_no_estim-1"',
         'envelope': env('szk.ma.analysis-spec/v1', SPEC_NO_ESTIM)},
        {'method': 'GET', 'path': '/api/specs/*', 'status': 404,
         'envelope': err('NOT_FOUND', 404, 'Nincs ilyen elemzési spec (05_elemzes/specs/).')},
    ]})

# -------------------------------------------------------------------- analysis_jobs.json (statikus tartalék; a dev-build dinamikusan válaszol)
explore_run = dict(RUN_PRIMARY)
explore_run.update({'run_id': None, 'mode': 'explore', 'files': None, 'stale': False, 'client_seq': 1, 'started': None, 'finished': None})
explore_run['equivalent_argv'] = ['ma.py', 'analyze', '--spec', '05_elemzes/specs/o1_primary.json', '--project', '.']
explore_run['expanded_argv'] = expanded_argv(SPEC_PRIMARY)
explore_plot = json.loads(json.dumps(PLOTS['primary']))
explore_plot['meta']['run_id'] = None
commit_run = dict(RUN_PRIMARY)
commit_run.update({'run_id': '20261005T091500Z-b7e4d0', 'started': '2026-10-05T09:15:00Z', 'finished': '2026-10-05T09:15:00Z'})
commit_run['equivalent_argv'] = ['ma.py', 'analyze', '--spec', '05_elemzes/specs/o1_primary.json', '--out', '05_elemzes/o1/20261005T091500Z-b7e4d0', '--project', '.']
dump('analysis_jobs.json', {
    'description': 'POST /api/analyze + GET /api/jobs/<id> — statikus tartalék (jobs.py snapshot-alak: job_id, kind, mode, client_seq, status, '
                   'elapsed_ms, result | error). A dev-buildben a src/dev/analysis_fixtures.js dinamikusan válaszol (last-wins, superseded, '
                   'spec-függő nézetmodell, commit-sor).',
    'routes': [
        {'method': 'POST', 'path': '/api/analyze', 'body': {'mode': 'explore'},
         'envelope': env('szk.ma.job/v1', {'job_id': 'j_fx_explore', 'kind': 'explore:o1', 'mode': 'explore', 'client_seq': 1,
                                           'status': 'done', 'elapsed_ms': 84, 'run_ms': 80,
                                           'result': {'schema': 'szk.ma.analysis-result/v1', 'run': explore_run, 'plot': explore_plot,
                                                      'validation_summary': RUN_PRIMARY['validation_summary'], 'excluded': []}}, ms=84)},
        {'method': 'POST', 'path': '/api/analyze', 'body': {'mode': 'commit'},
         'envelope': env('szk.ma.job/v1', {'job_id': 'j_fx_commit', 'kind': 'commit:o1', 'mode': 'commit', 'client_seq': 2,
                                           'status': 'queued', 'elapsed_ms': 0, 'run_ms': 0})},
        {'method': 'GET', 'path': '/api/jobs/j_fx_commit', 'sequence': [
            {'status': 200, 'envelope': env('szk.ma.job/v1', {'job_id': 'j_fx_commit', 'kind': 'commit:o1', 'mode': 'commit', 'client_seq': 2,
                                                             'status': 'running', 'elapsed_ms': 40, 'run_ms': 20})},
            {'status': 200, 'envelope': env('szk.ma.job/v1', {'job_id': 'j_fx_commit', 'kind': 'commit:o1', 'mode': 'commit', 'client_seq': 2,
                                                             'status': 'done', 'elapsed_ms': 120, 'run_ms': 96,
                                                             'result': {'schema': 'szk.ma.analysis-result/v1', 'run': commit_run,
                                                                        'validation_summary': RUN_PRIMARY['validation_summary'], 'excluded': []}})}]},
        {'method': 'GET', 'path': '/api/jobs/*', 'status': 404, 'envelope': err('NOT_FOUND', 404, 'Nincs ilyen feladat (lejárt vagy ismeretlen job_id).')},
    ]}, compact=True)

# -------------------------------------------------------------------- analysis_kb.json
def kb_show(rid):
    r = subprocess.run(['python3', 'ma.py', 'kb', 'show', rid], cwd=REPO, capture_output=True, text=True)
    return json.loads(r.stdout)


FIELD_RULES = {'model': ['D-S08-001', 'D-S08-004', 'D-S02-006'], 'pi': ['D-S09-007'],
               'heterogeneity': ['D-S09-001', 'D-S09-003', 'D-S09-004'], 'bias': ['V016', 'D-S02-010'],
               'sensitivity': ['D-S12-002', 'D-S12-003', 'D-S12-005', 'D-S12-009'], 'spec': ['D-S12-006', 'D-S10-001']}
ITEMS = sorted(set(sum(FIELD_RULES.values(), [])) | {'D-S12-006'})
kb = {i: kb_show(i) for i in ITEMS}
kb_routes = []
for field, ids in FIELD_RULES.items():
    items = [{'id': i, 'stage_id': kb[i]['stage_id'], 'strength': kb[i]['strength'], 'title': kb[i]['condition'][:140],
              'machine_check': kb[i]['machine_check']} for i in ids]
    kb_routes.append({'method': 'GET', 'path': '/api/kb/rules', 'query': {'field': field},
                      'envelope': env('szk.ma.kb-rules/v1', {'field': field, 'items': items})})
kb_routes.append({'method': 'GET', 'path': '/api/kb/rules', 'envelope': env('szk.ma.kb-rules/v1', {'field': None, 'items': []})})
for i in ITEMS:
    item = dict(kb[i])
    table = item.pop('_table', None)
    # a szerver (ma_gui/routes/kb.py get_item) alakja
    data = {'id': i, 'table': table, 'item': item, 'local_only': table == 'chunk',
            'note': {'hu': 'helyi forrás — nem exportálható', 'en': 'local source — not for export'}}
    kb_routes.append({'method': 'GET', 'path': '/api/kb/item/%s' % i, 'envelope': env('szk.ma.kb-item/v1', data)})
kb_routes.append({'method': 'GET', 'path': '/api/kb/item/*', 'status': 404, 'envelope': err('NOT_FOUND', 404, 'Nincs ilyen KB-tétel.')})
dump('analysis_kb.json', {
    'description': 'GET /api/kb/rules?field=<eredménymező> (a mezőre hivatkozó KB-szabályok, 3.5.7 ⓚ-jelvények; a szervernél még '
                   'egyeztetendő) és GET /api/kb/item/<id> (a ma_gui/routes/kb.py alakja: {id, table, item, local_only, note}) — '
                   'a tudasbazis.sqlite valódi decision_rule sorai (ma.py kb show).',
    'routes': kb_routes})

# (a /api/table, /api/provenance, /api/documents, /api/fileurl, /api/log/decision fixture-jei a kinyerés-képernyőé)
p = os.path.join(OUT, 'analysis_drilldown.json')
if os.path.exists(p):
    os.remove(p)
