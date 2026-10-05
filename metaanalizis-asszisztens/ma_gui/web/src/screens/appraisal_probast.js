/* screens/appraisal_probast.js — 5 Torzítás › PROBAST+AI: két menet (fejlesztés / értékelés), alkalmazhatóság,
 * holisztikus összítélet (terv 3.5.11, 4.11, 5.4, 9.3: 34 jelző-kérdés, menetenként helyes teljesség).
 *
 * Egység: modell egy vizsgálatban (unit = vizsgálat, target = a modell rövid neve). A válaszkulcsok menettel minősítettek
 * (development/1.1, evaluation/1.1) — így a két menet nem „teljesíti” egymást (H2). A menetenkénti teljességet a motor
 * számolja (check.per_pass); a PROBAST+AI-nak nincs összítélet-algoritmusa: a felület ezt szó szerint kiírja, és az
 * indoklás kötelező. Útvonal: #/appraisal-probast?unit=LEE2023&target=XGB-PE.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var A = MA.appr;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var TOOL = 'probast-ai';
  var TID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

  var S = null;

  function label(u) {
    var s = (S.list.studies || []).filter(function (x) { return x.study_id === u; })[0];
    return s && s.label ? s.label : u;
  }

  function table() {
    var items = S.list.items.filter(function (x) { return x.tool === TOOL; });
    if (!items.length) { return MA.ui.emptyState('appraisal.pb.none'); }
    return h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: 'pb-list' },
      h('thead', null, h('tr', null, h('th', { scope: 'col', i18n: 'appraisal.col.study' }), h('th', { scope: 'col', i18n: 'appraisal.pb.model' }),
        h('th', { scope: 'col' }, t('appraisal.pass.development')), h('th', { scope: 'col' }, t('appraisal.pass.evaluation')),
        h('th', { scope: 'col', i18n: 'appraisal.col.overall' }), h('th', { scope: 'col', i18n: 'appraisal.col.raters' }))),
      h('tbody', null, items.map(function (x) {
        var c = x.check || {};
        var pp = c.per_pass || {};
        return h('tr', { dataset: { unit: x.unit, target: x.target || '', rater: x.rater } },
          h('th', { scope: 'row' }, h('button', { type: 'button', 'class': 'btn-link pb-open', onclick: function () {
            MA.app.navigate('appraisal-probast', { unit: x.unit, target: x.target || '', rater: x.rater });
          } }, label(x.unit))),
          h('td', null, x.target || '—'),
          h('td', { 'class': 'num' }, pp.development ? A.completeness(c, 'development') : '—'),
          h('td', { 'class': 'num' }, pp.evaluation ? A.completeness(c, 'evaluation') : '—'),
          h('td', null, x.overall && x.overall.judgement ? A.verdictBadge(S.inst, x.overall.judgement) : A.verdictBadge(S.inst, null)),
          h('td', null, x.origin === 'ai_draft' ? A.aiBadge() : null, ' ', x.rater));
      }))));
  }

  function render(root, ctx) {
    var p = ctx.params || {};
    S = { unit: p.unit || null, target: p.target || null, rater: A.rater(), panel: null };
    MA.dom.mount(root, MA.ui.spinner());
    return Promise.all([A.instrument(TOOL), A.instruments(), MA.api.get('/api/appraisals', { query: { tool: TOOL }, signal: ctx.signal, toast: false })]).then(function (res) {
      if (!ctx.alive()) { return; }
      S.inst = res[0];
      S.list = res[2].data;
      var uId = MA.dom.uid('pb-u'), mId = MA.dom.uid('pb-m');
      var unitInp = h('input', { id: uId, type: 'text', maxlength: '128', size: '14', list: uId + '-l', value: S.unit || '' });
      var modelInp = h('input', { id: mId, type: 'text', maxlength: '64', size: '12', value: S.target || '' });
      var go = h('button', { type: 'button', 'class': 'btn btn-sm', id: 'pb-go', onclick: function () {
        var u = unitInp.value.trim(), m = modelInp.value.trim();
        if (!u) { unitInp.setAttribute('aria-invalid', 'true'); unitInp.focus(); return; }
        if (m && !TID_RE.test(m)) { modelInp.setAttribute('aria-invalid', 'true'); modelInp.focus(); return; }
        MA.app.navigate('appraisal-probast', { unit: u, target: m });
      } }, t('appraisal.open'));
      var formSec = h('section', { 'class': 'panel ap-form-panel', id: 'ap-form-panel', i18nAttrs: { 'aria-label': 'appraisal.formAria' }, hidden: !S.unit });
      MA.dom.mount(root,
        h('section', { 'class': 'panel', id: 'pb-head' }, h('h2', { 'class': 'panel-title', i18n: 'appraisal.pb.title' }),
          h('div', { 'class': 'toolbar' }, h('label', { htmlFor: uId, i18n: 'appraisal.col.study' }), unitInp,
            h('datalist', { id: uId + '-l' }, (S.list.studies || []).map(function (s) { return h('option', { value: s.study_id }, s.label || s.study_id); })),
            h('label', { htmlFor: mId, i18n: 'appraisal.pb.model' }), modelInp, go,
            A.raterField(function () { ctx.rerender(); }),
            MA.apprPanel.exchangeBar(function () { return S.rater; }, TOOL, function () { MA.app.refresh(); })),
          MA.apprPanel.sourceLine(res[1], S.inst),
          h('p', { 'class': 'ap-note', id: 'pb-holistic' }, MA.ui.badge('info', null), ' ', t('appraisal.pb.holistic')),
          h('p', { 'class': 'muted', id: 'pb-h2' }, t('appraisal.pb.counting'))),
        h('section', { 'class': 'panel' }, table()),
        formSec);
      if (S.unit) {
        var r = p.rater || S.rater;
        if (!r) { MA.dom.mount(formSec, h('p', { 'class': 'ap-msg', role: 'alert', id: 'ap-need-rater' }, t('appraisal.needRater'))); return; }
        S.panel = MA.apprPanel.open(formSec, { tool: TOOL, unit: S.unit, target: S.target, rater: r, myRater: S.rater, inst: S.inst, label: label(S.unit),
          passes: true, applicability: true,
          consensusHref: MA.app.href('appraisal-consensus', { tool: TOOL, target: S.target || '', unit: S.unit }),
          onRater: function (other) { MA.app.navigate('appraisal-probast', { unit: S.unit, target: S.target || '', rater: other }); },
          onSaved: function () { ctx.rerender(); } });
      }
    }, function (err) { if (ctx.alive() && err.code !== 'ABORTED') { MA.dom.mount(root, MA.ui.errorBox(err)); } });
  }

  MA.app.registerScreen({
    id: 'appraisal-probast', title_key: 'appraisal.nav.probast', workspace: 'appraisal', tab: 'appraisal', order: 40, render: render,
    onLeave: function () {
      if (S && S.panel && S.panel.dirty()) { return MA.ui.confirm({ title: t('appraisal.unsavedTitle'), message: t('appraisal.unsavedBody'), okLabel: t('appraisal.leave'), danger: true }); }
      return true;
    }
  });
})();
