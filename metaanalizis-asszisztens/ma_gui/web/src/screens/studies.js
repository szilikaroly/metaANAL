/* screens/studies.js — vizsgálat ↔ jelentés térkép (terv 3.5.14, 4.10: 03_adatok/studies.json).
 *
 * HTTP API (3.4; egyeztetendő): GET /api/studies → szk.ma.studies/v1 + {path, summary{studies, reports}, problems[]} + ETag
 *                               PUT /api/studies ← szk.ma.studies/v1 (If-Match) → ugyanez + új ETag; 409 CONFLICT, 422 VALIDATION.
 * Az I (vizsgálatok) és J (jelentések uniója) értékét a szerver adja (summary); a felület nem számol.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var P = MA.proc;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  var DESIGNS = ['', 'rct_parallel', 'rct_cluster', 'rct_crossover', 'nrsi', 'cohort', 'case_control', 'cross_sectional', 'diagnostic_accuracy', 'prediction_model', 'other'];
  var ROLES = ['primary', 'secondary', 'other'];
  var S = null;

  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function str(v) { return v === null || v === undefined ? '' : String(v); }

  function take(env) {
    var d = env.data || {};
    S.data = d;
    S.etag = env.etag;
    S.studies = clone(Array.isArray(d.studies) ? d.studies : []);
    S.dirty = false;
  }

  function touch() {
    S.dirty = true;
    S.els.save.disabled = false;
    S.els.state.textContent = t('studies.unsaved');
  }

  function input(id, value, label, onInput) {
    var el = h('input', { type: 'text', id: id, value: str(value), 'aria-label': label, oninput: function () { onInput(el.value); touch(); } });
    return el;
  }

  function optionList(list, cur, prefix) {
    var opts = list.map(function (v) { return { value: v, label: v ? t(prefix + v) : '—' }; });
    if (cur && list.indexOf(cur) < 0) { opts.push({ value: cur, label: cur }); }
    return opts;
  }

  function reportItems(st, si, host) {
    var reps = st.reports = Array.isArray(st.reports) ? st.reports : [];
    MA.dom.mount(host, reps.map(function (r, ri) {
      var lab = t('studies.report', { n: String(ri + 1), study: str(st.study_id) });
      var role = P.select({ id: 'st-role-' + si + '-' + ri, 'aria-label': lab + ' — ' + t('studies.col.role'),
        onchange: function () { r.role = role.value; touch(); } }, optionList(ROLES, r.role || 'primary', 'studies.role.'), r.role || 'primary');
      return h('li', { 'class': 'st-rep' },
        input('st-rec-' + si + '-' + ri, r.rec_id, lab + ' — ' + t('studies.col.rec'), function (v) { r.rec_id = v; }),
        role,
        input('st-doc-' + si + '-' + ri, r.doc, lab + ' — ' + t('studies.col.doc'), function (v) { r.doc = v || null; }),
        h('button', { type: 'button', 'class': 'btn-icon', 'aria-label': t('studies.removeReport', { n: String(ri + 1) }), onclick: function () {
          reps.splice(ri, 1); reportItems(st, si, host); touch();
          var b = MA.dom.$('#st-addrep-' + si); if (b) { b.focus(); }
        } }, '×'));
    }));
  }

  function row(st, si, ctx) {
    var reps = h('ul', { 'class': 'st-reps' });
    reportItems(st, si, reps);
    var label = str(st.label) || str(st.study_id) || '—';
    var design = P.select({ id: 'st-design-' + si, 'aria-label': t('studies.col.design') + ' — ' + label,
      onchange: function () { st.design = design.value || null; touch(); } }, optionList(DESIGNS, st.design || '', 'studies.design.'), st.design || '');
    return h('tr', { dataset: { study: str(st.study_id) } },
      h('th', { scope: 'row', 'class': 'st-head' }, h('span', { 'class': 'muted' }, '#' + String(si + 1) + ' '), h('span', { 'class': 'st-label' }, label)),
      h('td', null, input('st-id-' + si, st.study_id, t('studies.col.id') + ' — ' + label, function (v) { st.study_id = v; })),
      h('td', null, input('st-lab-' + si, st.label, t('studies.col.label') + ' — #' + String(si + 1), function (v) { st.label = v; })),
      h('td', null, input('st-reg-' + si, st.registration, t('studies.col.reg') + ' — ' + label, function (v) { st.registration = v || null; })),
      h('td', null, design),
      h('td', null, input('st-out-' + si, (st.outcomes || []).join(', '), t('studies.col.outcomes') + ' — ' + label, function (v) {
        st.outcomes = v.split(',').map(function (x) { return x.trim(); }).filter(function (x) { return !!x; });
      })),
      h('td', null, reps, h('button', { type: 'button', 'class': 'btn btn-sm', id: 'st-addrep-' + si, onclick: function () {
        st.reports.push({ rec_id: '', role: st.reports.length ? 'secondary' : 'primary', doc: null });
        reportItems(st, si, reps);
        touch();
        var i = MA.dom.$('#st-rec-' + si + '-' + (st.reports.length - 1)); if (i) { i.focus(); }
      } }, t('studies.addReport'))),
      h('td', null, h('button', { type: 'button', 'class': 'btn btn-sm btn-danger', 'aria-label': t('studies.remove', { label: label }), onclick: function () {
        MA.ui.confirm({ title: t('studies.removeTitle'), message: t('studies.remove', { label: label }), danger: true }).then(function (ok) {
          if (ok) { S.studies.splice(si, 1); S.dirty = true; keep(); ctx.rerender(); }
        });
      } }, '×')));
  }

  function keep() { MA.store.set('process.studies', { data: S.data, etag: S.etag, studies: S.studies, dirty: S.dirty }); }

  function save(ctx) {
    S.els.save.disabled = true;
    return MA.api.put('/api/studies', { schema: 'szk.ma.studies/v1', studies: S.studies }, { ifMatch: S.etag }).then(function (env) {
      if (!ctx.alive()) { return; }
      take(env);
      keep();
      MA.ui.toast({ kind: 'success', title: t('studies.saved') });
      ctx.rerender();
    }, function () { if (ctx.alive()) { S.els.save.disabled = false; } });
  }

  function render(root, ctx) {
    var kept = MA.store.get('process.studies');
    MA.store.set('process.studies', null);
    root.appendChild(MA.ui.spinner());
    return (kept ? Promise.resolve(null) : MA.api.get('/api/studies', { signal: ctx.signal })).then(function (env) {
      if (!ctx.alive()) { return; }
      S = { els: {} };
      if (kept) { Object.assign(S, kept); } else { take(env); }
      var mine = S;
      ctx.onCleanup(function () { if (S === mine) { if (S.dirty) { keep(); } S = null; } });
      var d = S.data;
      var sum = d.summary || {};
      var probs = Array.isArray(d.problems) ? d.problems : [];
      S.els.save = h('button', { type: 'button', 'class': 'btn btn-primary', id: 'st-save', disabled: !S.dirty, onclick: function () { save(ctx); } }, t('studies.save'));
      S.els.state = h('span', { 'class': 'muted', role: 'status' }, S.dirty ? t('studies.unsaved') : '');
      var cols = ['id', 'label', 'reg', 'design', 'outcomes', 'reports', 'actions'];
      MA.dom.mount(root,
        h('section', { 'class': 'panel', 'aria-labelledby': 'st-h' },
          h('h2', { 'class': 'panel-title', id: 'st-h', i18n: 'studies.title' }),
          h('p', { id: 'st-summary' }, t('studies.summary', { i: sum.studies === undefined ? '—' : String(sum.studies), j: sum.reports === undefined ? '—' : String(sum.reports), path: d.path || '03_adatok/studies.json' })),
          probs.length ? h('ul', { 'class': 'item-list' }, probs.map(function (p) { return h('li', { 'class': 'item' }, MA.ui.badge(P.sevKind(p.severity || 'warning'), p.code || null), ' ', MA.i18n.pick(p.message || p, '')); })) : null,
          h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact st-table', id: 'st-table' },
            h('caption', { 'class': 'sr-only', i18n: 'studies.title' }),
            h('thead', null, h('tr', null, h('th', { scope: 'col', i18n: 'studies.col.study' }), cols.map(function (c) { return h('th', { scope: 'col', i18n: 'studies.col.' + c }); }))),
            h('tbody', null, S.studies.length ? S.studies.map(function (st, i) { return row(st, i, ctx); }) : h('tr', null, h('td', { colspan: 8 }, MA.ui.emptyState('studies.empty')))))),
          h('div', { 'class': 'toolbar' },
            h('button', { type: 'button', 'class': 'btn', id: 'st-add', onclick: function () {
              S.studies.push({ study_id: '', label: '', registration: null, design: null, outcomes: [], reports: [] });
              S.dirty = true; keep(); ctx.rerender();
            } }, t('studies.add')),
            S.els.save,
            h('button', { type: 'button', 'class': 'btn btn-ghost', id: 'st-discard', onclick: function () { S.dirty = false; ctx.rerender(); } }, t('studies.discard')),
            h('a', { 'class': 'btn btn-ghost', href: MA.app.href('prisma') }, t('studies.back')),
            S.els.state)));
      if (kept && MA.dom.$('#st-id-' + (S.studies.length - 1)) && !S.studies[S.studies.length - 1].study_id) { MA.dom.$('#st-id-' + (S.studies.length - 1)).focus(); }
    });
  }

  function onLeave(ctx, info) {
    if (!S || !S.dirty || info.reason !== 'navigate') { return true; }
    return MA.ui.confirm({ title: t('prisma.unsavedTitle'), message: t('prisma.unsavedBody'), okLabel: t('prisma.leave'), danger: true }).then(function (ok) {
      if (ok && S) { S.dirty = false; }
      return ok;
    });
  }

  MA.app.registerScreen({ id: 'studies', title_key: 'studies.title', workspace: 'process', tab: 'prisma', order: 20, render: render, onLeave: onLeave });
})();
