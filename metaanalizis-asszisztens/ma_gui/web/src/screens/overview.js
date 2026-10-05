/* screens/overview.js — Áttekintés és „következő lépések” (terv 3.5.1).
 *
 * Adatforrások (mind szerver/motor; a felület nem számol):
 *   GET /api/log/checkpoint                            → szakaszok ellenőrzőpontjai (S00–S14, FINAL); a legutolsó számít
 *   GET /api/log/finding?status=open&severity=blocker  → nyitott blockerek
 *   GET /api/audit/project                             → szk.ma.project-audit/v1 (X-szabályok = következő lépések)
 *   GET /api/runs?primary=1                            → elsődleges commit-futások kimenetenként (szk.ma.run/v1 + stale, …)
 *   store 'project'                                    → kimenetek (szk.ma.project/v1)
 * Minden szakasz külön töltődik; egy hiba nem viszi el a többit. A „Miért?” gomb a KB-tételt mutatja (MA.why).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var P = MA.proc;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };

  var VERDICT = { PASS: ['P', 'ok'], PASS_WITH_FIXES: ['P', 'ok'], FAIL: ['F', 'error'] };
  var CERTAINTY = { high: 'grade.high', moderate: 'grade.moderate', low: 'grade.low', 'very low': 'grade.verylow' };
  // X-szabály → teendő-gomb (címke, cél-képernyő); a cél a kimenetet (outcome) kapja paraméterként
  var ACTIONS = {
    X001: ['rerun', 'analysis'], X002: ['redraw', 'results'], X003: ['sync', 'appraisal'], X004: ['open', 'appraisal'],
    X005: ['create', 'analysis'], X006: ['create', 'analysis'], X007: ['open', 'grade'], X008: ['open', 'grade'],
    X009: ['open', 'extraction'], X010: ['filter', 'extraction'], X011: ['open', 'appraisal'], X012: ['open', 'grade'],
    X013: ['sync', 'extraction'], X014: ['open', 'prisma'], X015: ['open', 'prisma'], X016: ['decide', 'analysis'],
    X017: ['open', 'appraisal'], X018: ['redraw', 'results'], X019: ['decide', 'grade'], X020: ['open', 'prisma'],
    X021: ['open', 'prisma'], X022: ['open', 'extraction']
  };

  function renderStages(env, blockers) {
    var latest = {};
    MA.api.list(env, 'checkpoints').forEach(function (c) {
      if (!latest[c.stage_id] || String(c.ts || '') >= String(latest[c.stage_id].ts || '')) { latest[c.stage_id] = c; }
    });
    var blocked = {};
    blockers.forEach(function (b) {
      var st = P.refs(b.stage_id);
      (st.length ? st : P.STAGES).forEach(function (s) { blocked[s] = true; });
    });
    return h('ol', { 'class': 'stage-strip' }, P.STAGES.map(function (s) {
      var c = latest[s];
      var v = c ? VERDICT[c.verdict] : null;
      var sym = v ? v[0] : (s === 'FINAL' ? '–' : '·');
      var label = c ? t('overview.verdict.' + c.verdict) : t('overview.verdict.none');
      var full = s + ': ' + label + (blocked[s] ? ', ' + t('overview.openBlocker') : '');
      return h('li', { 'class': ['stage', v && 'is-' + v[1], blocked[s] && 'is-blocked'], dataset: { stage: s } },
        h('a', { 'class': 'stage-link', href: MA.app.href('log', { tab: 'checkpoint', stage: s }), title: full, 'aria-label': full },
          h('span', { 'class': 'stage-id' }, s),
          h('span', { 'class': 'stage-mark', 'aria-hidden': 'true' }, blocked[s] ? '[' + sym + ']' : sym)));
    }));
  }

  function renderBlockers(items) {
    if (!items.length) { return MA.ui.emptyState('overview.noBlockers'); }
    return h('ul', { 'class': 'item-list' }, items.map(function (f) {
      var kb = P.refs(f.kb_refs);
      return h('li', { 'class': 'item', dataset: { id: f.id } },
        MA.ui.badge('blocker', '#' + String(f.id)),
        h('span', { 'class': 'item-stage' }, f.stage_id || '—'),
        h('span', { 'class': 'item-title' }, f.title || ''),
        h('span', { 'class': 'item-actions' },
          h('a', { 'class': 'btn btn-sm', href: MA.app.href('log', { tab: 'finding', finding: f.id }) }, t('overview.jump')),
          kb.length ? MA.why.button({ kb: kb, title: f.title, detail: f.detail }, { compact: true }) : null));
    }));
  }

  function renderNextSteps(env) {
    var d = env.data || {};
    var items = Array.isArray(d.findings) ? d.findings : [];
    var sum = d.summary || {};
    if (!items.length) { return MA.ui.emptyState('overview.noNextSteps'); }
    return [
      h('p', { 'class': 'ov-sum' }, ['error', 'warning', 'info'].map(function (k) {
        return typeof sum[k] === 'number' ? MA.ui.badge(k, String(sum[k]), { srLabel: t('ov.sev.' + k) }) : null;
      })),
      h('ul', { 'class': 'item-list' }, items.map(function (f) {
        var cmd = Array.isArray(f.suggested_command) ? 'python ' + f.suggested_command.join(' ') : null;
        var act = ACTIONS[f.code];
        return h('li', { 'class': 'item ov-x', dataset: { code: f.code } },
          MA.ui.badge(P.sevKind(f.severity), f.code),
          h('span', { 'class': 'item-stage' }, [f.stage, f.outcome].filter(function (x) { return !!x; }).join(' · ')),
          h('span', { 'class': 'item-title' }, pick(f.title)),
          f.detail ? h('span', { 'class': 'item-detail muted' }, pick(f.detail)) : null,
          cmd ? h('code', { 'class': 'cmd-inline' }, cmd) : null,
          h('span', { 'class': 'item-actions' },
            cmd ? MA.ui.copyButton(function () { return cmd; }) : null,
            act ? h('a', { 'class': 'btn btn-sm ov-act', href: MA.app.href(act[1], f.outcome ? { outcome: f.outcome } : null) }, t('ov.act.' + act[0])) : null,
            MA.why.button({ kb: [f.code].concat(P.refs(f.kb_refs)), code: f.code, title: f.title, detail: f.detail }, { compact: true })));
      }))];
  }

  function renderOutcomes(env) {
    var project = MA.store.get('project') || {};
    var outcomes = Array.isArray(project.outcomes) ? project.outcomes : [];
    var byOutcome = {};
    MA.api.list(env, 'runs').forEach(function (r) { if (r.outcome_id && !byOutcome[r.outcome_id]) { byOutcome[r.outcome_id] = r; } });
    if (!outcomes.length) { return MA.ui.emptyState('overview.noOutcomes'); }
    var head = ['outcome', 'k', 'participants', 'effect', 'i2', 'robHigh', 'grade', 'run'];
    var numCol = { k: 1, participants: 1, i2: 1, robHigh: 1 };
    var dash = '—';
    return h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table', id: 'overview-outcomes' },
      h('thead', null, h('tr', null, head.map(function (k) { return h('th', { scope: 'col', 'class': numCol[k] ? 'num' : null, i18n: 'overview.col.' + k }); }))),
      h('tbody', null, outcomes.map(function (o) {
        var r = byOutcome[o.id];
        var p = r && r.primary;
        return h('tr', { dataset: { outcome: o.id } },
          h('th', { scope: 'row' }, h('a', { href: MA.app.href('results', { outcome: o.id }) }, MA.i18n.pick(o.name, o.id))),
          h('td', { 'class': 'num' }, r && typeof r.k === 'number' ? String(r.k) : dash),
          h('td', { 'class': 'num' }, r ? MA.ui.numText(r.participants_text) : dash),
          h('td', null, p ? h('span', null, o.measure ? o.measure + ' ' : '', MA.ui.num(p.display_text)) : h('span', { 'class': 'muted', i18n: 'overview.noCommit' })),
          h('td', { 'class': 'num' }, p ? MA.ui.numText(p.i2_text) : dash),
          h('td', { 'class': 'num' }, r && typeof r.rob_high === 'number' ? String(r.rob_high) : dash),
          h('td', null, r && r.grade && CERTAINTY[r.grade.certainty] ? t(CERTAINTY[r.grade.certainty]) : dash),
          h('td', null, r ? (r.stale ? MA.ui.badge('stale', t('overview.run.stale')) : MA.ui.badge('ok', t('overview.run.current'))) : dash));
      }))));
  }

  function render(root, ctx) {
    var project = MA.store.get('project') || {};
    if (project.title) { ctx.setTitle(MA.i18n.pick(project.title, '')); }
    var stages = P.section('overview.stages', 'ov-stages');
    var blockers = P.section('overview.blockers', 'ov-blockers');
    var next = P.section('overview.nextSteps', 'ov-next');
    var outs = P.section('overview.outcomes', 'ov-outcomes');
    MA.dom.mount(root, stages.el, h('div', { 'class': 'grid-2' }, blockers.el, next.el), outs.el,
      h('p', { 'class': 'muted ov-legend', i18n: 'ov.legend' }));

    var blockerItems = [];
    var pBlockers = MA.api.get('/api/log/finding', { query: { status: 'open', severity: 'blocker' }, signal: ctx.signal, toast: false })
      .then(function (env) {
        blockerItems = MA.api.list(env, 'findings');
        if (ctx.alive()) { P.fill(blockers, renderBlockers(blockerItems)); }
      }, function (err) { if (ctx.alive() && err.code !== 'ABORTED') { P.fill(blockers, MA.ui.errorBox(err)); } });
    var pStages = pBlockers.then(function () {
      return P.load(stages, ctx, '/api/log/checkpoint', null, function (env) { return renderStages(env, blockerItems); });
    });
    return Promise.all([pStages, P.load(next, ctx, '/api/audit/project', null, renderNextSteps),
      P.load(outs, ctx, '/api/runs', { primary: 1 }, renderOutcomes)]);
  }

  MA.app.registerScreen({ id: 'overview', title_key: 'tab.overview', workspace: 'process', tab: 'overview', order: 10, render: render });
})();
