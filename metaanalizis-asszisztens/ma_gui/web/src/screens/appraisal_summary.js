/* screens/appraisal_summary.js — 5 Torzítás › Összesítő: forgalmi lámpa, súlyozott összesítő és a rob oszlop szinkronja
 * (terv 3.5.10, 4.12 szk.rob-summary/v1, 6.5 „RoB → elemzés”, X003).
 *
 * GET /api/appraisals/rob-summary?tool=&outcome= → a motor összesítője (vizsgálatonkénti végső ítéletek, súlyok a kimenet
 * elsődleges commit-futásából, kész szövegekkel) → MA.plots.robTraffic. A „rob oszlop szinkron” előnézete:
 * POST /api/appraisals/rob-sync {tool, outcome, dry_run: true} → a motor javaslata (sor, előtte → utána, forrás);
 * alkalmazás: ugyanez dry_run: false + If-Match (az előnézetben látott tábla ETag-je) → a tároló írja a CSV-t, a cellák
 * eredete „calculated”. 409: a tábla közben változott → új előnézet kell.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var A = MA.appr;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };

  var S = null;

  function summaryPanel(sec, ctx) {
    var q = { tool: S.tool };
    if (S.outcome) { q.outcome = S.outcome; }
    return MA.proc.load(sec, ctx, '/api/appraisals/rob-summary', q, function (env) {
      var d = env.data;
      var src = d.source_run;
      return [
        (env.warnings || []).length ? h('ul', { 'class': 'ap-warn-list' }, env.warnings.map(function (w) { return h('li', null, MA.ui.badge('warning', null), ' ', w); })) : null,
        (d.studies || []).length ? h('div', { 'class': 'rt-wrap' }, MA.plots.robTraffic.matrix(d, S.inst)) : MA.ui.emptyState('appraisal.sum.empty'),
        h('p', { 'class': 'muted ap-legend' }, A.verdicts(S.inst).map(function (v) { return [A.verdictBadge(S.inst, v.value), ' ']; }), A.verdictBadge(S.inst, null)),
        h('h3', { i18n: 'appraisal.sum.weightedTitle' }),
        MA.plots.robTraffic.weighted(d, S.inst) || h('p', { 'class': 'muted', i18n: 'appraisal.sum.noWeights' }),
        h('p', { 'class': 'muted', id: 'rt-source' }, src && src.run_id ? t('appraisal.sum.sourceRun', { run: src.run_id }) : t('appraisal.sum.noRun'),
          src && src.stale ? [' ', MA.ui.badge('stale', t('appraisal.sum.stale'))] : null,
          ' · ', t('appraisal.sum.fromEngine'))
      ];
    });
  }

  function syncTable(d) {
    var p = d.proposal || {};
    var ch = p.changes || [];
    return [
      h('p', null, t('appraisal.sync.target', { dataset: d.dataset, column: d.column }),
        d.column_exists ? null : [' ', MA.ui.badge('info', t('appraisal.sync.newColumn'))]),
      ch.length ? h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: 'sync-table' },
        h('thead', null, h('tr', null, h('th', { scope: 'col', i18n: 'appraisal.col.study' }), h('th', { scope: 'col', i18n: 'appraisal.sync.before' }),
          h('th', { scope: 'col', i18n: 'appraisal.sync.after' }), h('th', { scope: 'col', i18n: 'appraisal.sync.source' }))),
        h('tbody', null, ch.map(function (c) {
          return h('tr', { dataset: { uid: c.row_uid } }, h('th', { scope: 'row' }, c.label || c.study_id || c.row_uid),
            h('td', null, c.before ? c.before : h('span', { 'class': 'muted', i18n: 'appraisal.sync.empty' })),
            h('td', null, h('strong', null, c.after)),
            h('td', { 'class': 'muted' }, String(c.source || '—')));
        })))) : h('p', { 'class': 'ap-msg is-ok', id: 'sync-none' }, t('appraisal.sync.nothing')),
      (p.unmatched || []).length ? h('p', { 'class': 'muted' }, t('appraisal.sync.unmatched', { list: p.unmatched.map(function (u) { return u.study_id || u.label || '?'; }).join(', ') })) : null,
      (p.warnings || []).length ? h('ul', { 'class': 'ap-warn-list' }, p.warnings.map(function (w) { return h('li', null, MA.ui.badge('warning', null), ' ', pick(w)); })) : null
    ];
  }

  function syncPanel(host) {
    var body = h('div', { 'class': 'stack', id: 'sync-body' });
    var msg = h('div', { role: 'alert', id: 'sync-msg' });
    var applyBtn = h('button', { type: 'button', 'class': 'btn btn-primary', id: 'sync-apply', disabled: true, onclick: apply }, t('appraisal.sync.apply'));
    function preview(keepMsg) {
      if (keepMsg !== true) { MA.dom.clear(msg); }
      applyBtn.disabled = true;
      MA.proc.pend(body, true);
      return MA.api.post('/api/appraisals/rob-sync', { tool: S.tool, outcome: S.outcome || null, dry_run: true }, { toast: false }).then(function (env) {
        MA.proc.pend(body, false);
        S.sync = env.data;
        S.syncEtag = env.etag || env.data.etag || null;
        MA.dom.mount(body, syncTable(env.data));
        applyBtn.disabled = !((env.data.proposal || {}).changes || []).length;
      }, function (err) { MA.proc.pend(body, false); MA.dom.mount(body, MA.ui.errorBox(err)); });
    }
    function apply() {
      if (!S.sync) { return; }
      var n = (S.sync.proposal.changes || []).length;
      MA.ui.confirm({ title: t('appraisal.sync.confirmTitle'), message: t('appraisal.sync.confirmBody', { n: String(n), dataset: S.sync.dataset, column: S.sync.column }),
        okLabel: t('appraisal.sync.apply') }).then(function (yes) {
        if (!yes) { return; }
        var etag = S.syncEtag && S.syncEtag.charAt(0) === '"' ? S.syncEtag : '"' + String(S.syncEtag) + '"';
        MA.api.post('/api/appraisals/rob-sync', { tool: S.tool, outcome: S.outcome || null, dry_run: false }, { ifMatch: etag, toast: false }).then(function (env) {
          MA.ui.toast({ kind: 'success', title: t('appraisal.sync.done', { n: String(n) }), message: env.data.dataset, details: env.warnings && env.warnings.length ? env.warnings : null });
          MA.dom.mount(msg, h('p', { 'class': 'ap-msg is-ok', id: 'sync-applied' }, t('appraisal.sync.applied', { dataset: env.data.dataset })));
          preview(true);
        }, function (err) {
          MA.dom.mount(msg, MA.ui.errorBox(err), err.code === 'CONFLICT' ? h('button', { type: 'button', 'class': 'btn btn-sm', id: 'sync-repreview', onclick: function () { preview(); } }, t('appraisal.sync.repreview')) : null);
        });
      });
    }
    MA.dom.mount(host, h('h2', { 'class': 'panel-title', i18n: 'appraisal.sync.title' }),
      h('p', { 'class': 'muted', i18n: 'appraisal.sync.intro' }),
      h('div', { 'class': 'toolbar' }, h('button', { type: 'button', 'class': 'btn', id: 'sync-preview', onclick: function () { preview(); } }, t('appraisal.sync.preview')), applyBtn),
      body, msg);
  }

  function render(root, ctx) {
    var p = ctx.params || {};
    S = { tool: p.tool || 'rob2', outcome: p.outcome || null, inst: null, sync: null, syncEtag: null };
    MA.dom.mount(root, MA.ui.spinner());
    return Promise.all([A.instrument(S.tool), A.instruments(), MA.api.get('/api/appraisals', { query: { tool: S.tool }, signal: ctx.signal, toast: false })]).then(function (res) {
      if (!ctx.alive()) { return; }
      S.inst = res[0];
      var outs = res[2].data.outcomes || [];
      if (!S.outcome && outs.length) { S.outcome = outs[0].id; }
      var rob = (res[1].instruments || []).filter(function (x) { return A.isRobFamily(x); });
      var toolSel = h('select', { id: 'sum-tool', onchange: function () { MA.app.navigate('appraisal-summary', { tool: toolSel.value, outcome: S.outcome }); } },
        rob.map(function (x) { return h('option', { value: x.key, selected: x.key === S.tool }, pick(x.name_i18n) || pick(x.name) || x.key); }));
      var outSel = h('select', { id: 'sum-outcome', onchange: function () { MA.app.navigate('appraisal-summary', { tool: S.tool, outcome: outSel.value }); } },
        outs.map(function (o) { return h('option', { value: o.id, selected: o.id === S.outcome }, o.id + ' — ' + pick(o.name)); }));
      var sec = MA.proc.section('appraisal.sum.title', 'rt-panel');
      var syncHost = h('section', { 'class': 'panel', id: 'sync-panel' });
      MA.dom.mount(root,
        h('section', { 'class': 'panel' }, h('div', { 'class': 'toolbar' }, h('label', { htmlFor: 'sum-tool', i18n: 'appraisal.tool' }), toolSel,
          h('label', { htmlFor: 'sum-outcome', i18n: 'appraisal.outcome' }), outSel)),
        sec.el, syncHost);
      summaryPanel(sec, ctx);
      syncPanel(syncHost);
    }, function (err) { if (ctx.alive() && err.code !== 'ABORTED') { MA.dom.mount(root, MA.ui.errorBox(err)); } });
  }

  MA.app.registerScreen({ id: 'appraisal-summary', title_key: 'appraisal.nav.summary', workspace: 'appraisal', tab: 'appraisal', order: 30, render: render });
})();
