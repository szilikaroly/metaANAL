/* screens/adapters_composer.js — 2 PRISMA › Composer-forrás: kézi ↔ composer-mód (terv 3.5.14, 4.13, 5.0 H7, 5.5).
 *
 * HTTP API (ma_gui/routes/adapters_composer.py):
 *   GET  /api/prisma/composer          → szk.ma.prisma-composer/v1 {status{state, version, mode, guards, remedy,
 *                                        can_refresh}, location{configured, outdir, project, state_exists, problems[]},
 *                                        current{mode: manual|composer, etag, has_numbers, source{…}, status_warnings[]},
 *                                        can_refresh, reason}
 *   PUT  /api/prisma/composer/config   ← {outdir, project}
 *   POST /api/prisma/composer/refresh  ← {dry_run?, confirm_replace_manual?} (+ If-Match: a PRISMA-fájl ETag-je)
 *                                       → {flow (szk.prisma-flow/v1), check (motor), status_warnings[], written, …}
 * A számok a composer exportjából jönnek (darabszámok), az ellenőrzés a motoré; a felület nem számol. A composer
 * figyelmeztetései szó szerint. Vissza kézire: a PRISMA képernyő indoklásos kézi felülírása (PUT /api/prisma/manual).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };
  var SLUG_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
  var BOXES = [['identified_databases', 'A1'], ['identified_registers', 'A2'], ['dedup_removed', 'D1'], ['automation_removed', 'D2'],
    ['removed_before_screening_n', 'D3'], ['screened', 'B'], ['excluded_screening', 'C'], ['sought_for_retrieval', 'E'],
    ['not_retrieved', 'F'], ['assessed_eligibility', 'G'], ['excluded_eligibility', 'H'], ['included_reports', 'J'], ['included_studies', 'I']];

  function stateCell(st, extra) {
    return MA.adaptersCaps ? MA.adaptersCaps.stateCell(st, extra) : h('span', null, String(st));
  }

  function warningList(list, id) {
    if (!list || !list.length) { return h('p', { 'class': 'muted', id: id, i18n: 'adp.comp.noWarnings' }); }
    return h('ul', { 'class': 'item-list', id: id }, list.map(function (w) {
      return h('li', { 'class': 'item', dataset: { code: w.code } }, MA.ui.badge('warning', null), ' ', h('span', { 'class': 'item-title' }, pick(w)));
    }));
  }

  function flowTable(flow) {
    var rows = BOXES.map(function (b) {
      var v = flow[b[0]];
      return h('tr', { dataset: { box: b[1] } }, h('th', { scope: 'row' }, b[1]), h('td', null, t('prisma.box.' + b[1])),
        h('td', { 'class': 'num' }, v === null || v === undefined ? '—' : String(v)));
    });
    var reasons = flow.excluded_eligibility_reasons || null;
    return [h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: 'comp-flow' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, ''), h('th', { scope: 'col', i18n: 'adp.comp.box' }), h('th', { scope: 'col', i18n: 'adp.comp.value' }))),
      h('tbody', null, rows))),
    reasons ? h('div', null, h('h4', { i18n: 'adp.comp.reasons' }), h('ul', { 'class': 'item-list', id: 'comp-reasons' }, Object.keys(reasons).map(function (k) {
      return h('li', { 'class': 'item' }, h('span', { 'class': 'item-title' }, k), String(reasons[k]));
    }))) : null];
  }

  function checkLine(check) {
    var s = (check && check.summary) || {};
    var n = function (k) { return typeof s[k] === 'number' ? String(s[k]) : '0'; };
    return h('p', { 'class': 'row', id: 'comp-check' }, MA.ui.badge(s.error ? 'error' : (s.warning ? 'warning' : 'ok'), null), ' ',
      t('adp.comp.checkSummary', { e: n('error'), w: n('warning'), i: n('info') }));
  }

  function render(root, ctx) {
    root.appendChild(MA.ui.spinner());
    return MA.api.get('/api/prisma/composer', { signal: ctx.signal }).then(function (env) {
      if (!ctx.alive()) { return; }
      build(root, ctx, env.data || {});
    });
  }

  function build(root, ctx, d) {
    var st = d.status || {};
    var loc = d.location || {};
    var cur = d.current || {};
    var src = cur.source || {};
    var composerMode = cur.mode === 'composer';
    var resultHost = h('section', { 'class': 'panel', id: 'comp-result', 'aria-live': 'polite', hidden: true });

    function showResult(data, written) {
      resultHost.hidden = false;
      MA.dom.mount(resultHost,
        h('h2', { 'class': 'panel-title', i18n: written ? 'adp.comp.refreshed' : 'adp.comp.previewTitle' }),
        checkLine(data.check), flowTable(data.flow || {}),
        h('h3', { i18n: 'adp.comp.warnings' }), warningList(data.status_warnings, 'comp-result-warnings'));
    }

    function refresh(dry, confirmed) {
      var body = { dry_run: !!dry };
      if (confirmed) { body.confirm_replace_manual = true; }
      var opts = { signal: ctx.signal, quiet: ['CONFLICT'] };
      if (cur.etag) { opts.ifMatch = cur.etag; }
      return MA.api.post('/api/prisma/composer/refresh', body, opts).then(function (env) {
        if (!ctx.alive()) { return; }
        if (dry) { showResult(env.data || {}, false); return; }
        MA.ui.toast({ kind: 'success', title: t('adp.comp.refreshed') });
        MA.store.set('process.prisma', null);
        ctx.rerender();
      }, function (err) {
        if (!ctx.alive()) { return; }
        if (err && err.code === 'CONFLICT' && err.details && err.details.needs_confirm) {
          MA.ui.confirm({ title: t('adp.comp.confirmTitle'), message: t('adp.comp.confirmBody'), okLabel: t('adp.comp.confirmOk'), danger: true })
            .then(function (ok) { if (ok) { refresh(false, true); } else { ctx.rerender(); } });
        } else if (err && err.code === 'CONFLICT') {
          MA.ui.toast({ kind: 'error', title: err.message || err.code });
          ctx.rerender();
        }
      });
    }

    function toManual() {
      MA.ui.modal({ title: t('adp.comp.toManual'), size: 'md', body: [h('p', { i18n: 'adp.comp.toManualBody' })],
        actions: [{ label: t('common.cancel'), kind: 'ghost' },
          { label: t('adp.comp.goPrisma'), kind: 'primary', onClick: function () { ctx.navigate('prisma'); return true; } }],
        onClose: function () { var r = MA.dom.$('#comp-mode-composer'); if (r) { r.checked = true; } } });
    }

    var canRefresh = !!d.can_refresh;
    var modeBox = h('fieldset', { 'class': 'adp-fieldset', id: 'comp-mode' }, h('legend', { i18n: 'adp.comp.source' }),
      h('div', { 'class': 'adp-choice-row' }, h('label', { 'class': 'adp-choice', htmlFor: 'comp-mode-manual' },
        h('input', { type: 'radio', name: 'comp-mode', id: 'comp-mode-manual', value: 'manual', checked: !composerMode,
          onchange: function () { if (composerMode) { toManual(); } } }), ' ', t('adp.comp.mode.manual'))),
      h('div', { 'class': 'adp-choice-row' }, h('label', { 'class': ['adp-choice', !canRefresh && !composerMode && 'is-disabled'], htmlFor: 'comp-mode-composer' },
        h('input', { type: 'radio', name: 'comp-mode', id: 'comp-mode-composer', value: 'composer', checked: composerMode,
          disabled: !canRefresh && !composerMode, onchange: function () { if (!composerMode) { refresh(false, false); } } }), ' ', t('adp.comp.mode.composer'))));

    var outdirIn = h('input', { type: 'text', id: 'comp-outdir', 'class': 'input', autocomplete: 'off', value: loc.outdir || '' });
    var projIn = h('input', { type: 'text', id: 'comp-project', 'class': 'input', autocomplete: 'off', value: loc.project || '' });
    var projErr = h('span', { 'class': 'pf-err', role: 'alert', id: 'comp-project-err', hidden: true }, t('adp.comp.projectBad'));
    var saveBtn = h('button', { type: 'button', 'class': 'btn', id: 'comp-save-loc', onclick: function () {
      var ok = SLUG_RE.test(projIn.value.trim());
      projIn.setAttribute('aria-invalid', ok ? 'false' : 'true');
      projErr.hidden = ok;
      if (!ok || !outdirIn.value.trim()) { (ok ? outdirIn : projIn).focus(); return; }
      saveBtn.disabled = true;
      MA.api.put('/api/prisma/composer/config', { outdir: outdirIn.value.trim(), project: projIn.value.trim() }, { signal: ctx.signal }).then(function () {
        if (!ctx.alive()) { return; }
        MA.ui.toast({ kind: 'success', title: t('adp.comp.locSaved') });
        ctx.rerender();
      }, function () { if (ctx.alive()) { saveBtn.disabled = false; } });
    } }, t('adp.comp.saveLoc'));

    var reasonText = d.reason ? pick(d.reason) : null;
    MA.dom.mount(root,
      h('section', { 'class': 'panel', id: 'comp-head', 'aria-labelledby': 'comp-h' },
        h('h2', { 'class': 'panel-title', id: 'comp-h' }, h('span', { i18n: 'adp.comp.title' }), ' ',
          MA.why ? MA.why.button({ title: t('adp.comp.why.title'), detail: t('adp.comp.why.body') }, { id: 'comp-why' }) : null),
        h('p', { 'class': 'muted', i18n: 'adp.comp.intro' }),
        h('p', { 'class': 'row', id: 'comp-plugin' }, t('adp.comp.plugin'), ' ', stateCell(st.state, [st.version, st.mode && MA.adaptersCaps ? MA.adaptersCaps.modeText(st.mode) : null].filter(function (x) { return !!x; }).join(' · '))),
        (st.guards || []).indexOf('H7') >= 0 || st.explicit_interpreter ? h('p', { 'class': 'muted', id: 'comp-h7' }, MA.ui.badge('info', 'H7'), ' ', t('adp.comp.h7')) : null,
        st.remedy && st.state !== 'ok' && st.state !== 'legacy' ? h('p', { 'class': 'adp-remedy', id: 'comp-remedy' }, MA.ui.badge('warning', null), ' ', pick(st.remedy)) : null),
      h('section', { 'class': 'panel', id: 'comp-source', 'aria-labelledby': 'comp-source-h' },
        h('h2', { 'class': 'panel-title', id: 'comp-source-h', i18n: 'adp.comp.source' }),
        modeBox,
        composerMode && src.refreshed ? h('p', { 'class': 'muted', id: 'comp-last' }, t('adp.comp.last', { ts: MA.i18n.ts(src.refreshed), version: src.composer_version || '—',
          sha: String(src.export_sha256 || '').slice(0, 12) || '—' })) : (composerMode ? null : h('p', { 'class': 'muted', id: 'comp-last', i18n: 'adp.comp.notYet' })),
        !canRefresh && reasonText ? h('p', { 'class': 'adp-remedy', id: 'comp-cant' }, MA.ui.badge('warning', null), ' ', h('span', { i18n: 'adp.comp.cantRefresh' }), ' ', reasonText) : null,
        h('div', { 'class': 'toolbar' },
          h('button', { type: 'button', 'class': 'btn', id: 'comp-preview', disabled: !canRefresh, onclick: function () { refresh(true, false); } }, t('adp.comp.preview')),
          h('button', { type: 'button', 'class': 'btn btn-primary', id: 'comp-refresh', disabled: !canRefresh, onclick: function () { refresh(false, false); } }, t('adp.comp.refresh')),
          composerMode ? h('button', { type: 'button', 'class': 'btn btn-ghost', id: 'comp-to-manual', onclick: toManual }, t('adp.comp.toManual')) : null),
        composerMode ? h('div', null, h('h3', { i18n: 'adp.comp.warnings' }), warningList(cur.status_warnings, 'comp-warnings')) : null),
      resultHost,
      h('section', { 'class': 'panel', id: 'comp-location', 'aria-labelledby': 'comp-loc-h' },
        h('h2', { 'class': 'panel-title', id: 'comp-loc-h', i18n: 'adp.comp.location' }),
        MA.proc.field(t('adp.comp.outdir'), outdirIn),
        h('div', { 'class': 'pf-field' }, h('label', { htmlFor: 'comp-project' }, t('adp.comp.project')), projIn, projErr),
        (loc.problems || []).length ? h('ul', { 'class': 'item-list', id: 'comp-loc-problems' }, loc.problems.map(function (p) {
          return h('li', { 'class': 'item' }, MA.ui.badge('warning', null), ' ', pick(p));
        })) : null,
        h('div', { 'class': 'toolbar' }, saveBtn)));
  }

  MA.app.registerScreen({ id: 'prisma-composer', title_key: 'adp.nav.composer', workspace: 'process', tab: 'prisma', order: 15, render: render });
})();
