/* screens/appraisal_tripod.js — 5 Torzítás › TRIPOD+AI: jelentési teljesség (52 altétel, D/E szűrő, hőtérkép, hiánylista,
 * „Saját kézirat” mód) — terv 3.5.11, 4.11, 5.4.
 *
 * A hőtérkép a bevont vizsgálatok TRIPOD+AI-értékeléseinek állapotait mutatja (■ közölt ▣ részben □ hiányzik · N/A; a jel
 * alakja és színe együtt jelöl) — GET /api/appraisals?tool=tripod-ai&answers=1 (állapot-értékek, bizonyíték nélkül).
 * A sor kiválasztásával az űrlap nyílik (MA.apprPanel), a hiánylista a motor check.tripod mezőjéből. „Saját kézirat”:
 * ugyanez a felhasználó saját predikciós modelles kéziratára (egység: manuscript). A TRIPOD+AI a jelentés teljességét méri,
 * NEM a módszertan helyességét — a felület kiírja. Útvonal: #/appraisal-tripod?mode=studies|manuscript&filter=both|D|E&unit=…
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var A = MA.appr;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };
  var TOOL = 'tripod-ai';
  var MANUSCRIPT = 'manuscript';
  var STATUS_SYM = { present: '■', partial: '▣', missing: '□', not_applicable: '·' };
  var FILTERS = { both: 'both', D: 'development', E: 'evaluation' };

  var S = null;

  function label(u) {
    if (u === MANUSCRIPT) { return t('appraisal.tr.manuscript'); }
    var s = (S.list.studies || []).filter(function (x) { return x.study_id === u; })[0];
    return s && s.label ? s.label : u;
  }

  function nav(extra) { return Object.assign({ mode: S.mode, filter: S.filter }, extra || {}); }

  function cols() { return A.itemsFor(S.inst, FILTERS[S.filter] || 'both', null); }

  function heatmap() {
    var items = S.list.items.filter(function (x) { return x.tool === TOOL && x.unit !== MANUSCRIPT; });
    if (!items.length) { return MA.ui.emptyState('appraisal.tr.none'); }
    var cs = cols();
    return h('div', { 'class': 'table-wrap tr-wrap' }, h('table', { 'class': 'table table-compact tr-heat', id: 'tr-heat' },
      h('caption', { 'class': 'sr-only' }, t('appraisal.tr.heatCaption')),
      h('thead', null, h('tr', null, h('th', { scope: 'col', i18n: 'appraisal.col.study' }),
        cs.map(function (it) { return h('th', { scope: 'col', 'class': 'tr-col', title: it.id + ' (' + (it.applies_to || '') + ') — ' + pick(it.text) }, it.id); }))),
      h('tbody', null, items.map(function (x) {
        var ans = x.answers || {};
        return h('tr', { dataset: { unit: x.unit, rater: x.rater }, 'class': S.unit === x.unit ? 'is-selected' : null },
          h('th', { scope: 'row' }, h('button', { type: 'button', 'class': 'btn-link tr-open', onclick: function () {
            MA.app.navigate('appraisal-tripod', nav({ unit: x.unit, rater: x.rater }));
          } }, label(x.unit)), ' ', x.origin === 'ai_draft' ? A.aiBadge() : null, h('span', { 'class': 'muted' }, ' ' + x.rater)),
          cs.map(function (it) {
            var v = ans[A.keyOf(it)] || null;
            var txt = v ? (A.answers(S.inst).filter(function (o) { return o.value === v; })[0] || { text: v }).text : t('appraisal.tr.unanswered');
            return h('td', { 'class': ['tr-cell', 'tr-' + (v || 'none')], dataset: { item: it.id, v: v || '' }, title: it.id + ': ' + txt },
              h('span', { 'aria-hidden': 'true' }, v ? STATUS_SYM[v] || '?' : ' '), h('span', { 'class': 'sr-only' }, it.id + ': ' + txt));
          }));
      }))),
      h('p', { 'class': 'muted ap-legend', id: 'tr-legend' }, Object.keys(STATUS_SYM).map(function (k) {
        var o = A.answers(S.inst).filter(function (x) { return x.value === k; })[0];
        return h('span', { 'class': 'tr-leg tr-' + k }, STATUS_SYM[k] + ' ' + (o ? o.text : k) + '   ');
      })));
  }

  /** a motor hiánylistája (check.tripod) az űrlap alatt */
  function missingBlock(view, check) {
    var tr = check && check.tripod;
    if (!tr) { return null; }
    var miss = tr.missing_items || [];
    return h('div', { 'class': 'tr-missing', id: 'tr-missing' },
      tr.reported_text ? h('p', { id: 'tr-reported' }, t('appraisal.tr.reported', { pct: pick(tr.reported_text) })) : null,
      h('h4', { i18n: 'appraisal.tr.missingTitle' }),
      miss.length ? h('ul', { 'class': 'item-list' }, miss.map(function (m) {
        var it = (S.inst.items || []).filter(function (x) { return x.id === m.item; })[0];
        var st = m.status ? (A.answers(S.inst).filter(function (o) { return o.value === m.status; })[0] || { text: m.status }).text : '';
        return h('li', { 'class': 'item', dataset: { item: m.item } }, MA.ui.badge(m.status === 'partial' ? 'warning' : 'error', m.item),
          h('span', { 'class': 'item-title' }, it ? pick(it.text) : ''), st ? h('span', { 'class': 'item-detail muted' }, st + (m.applies_to ? ' · ' + m.applies_to : '')) : null);
      })) : MA.ui.emptyState('appraisal.tr.noMissing'),
      tr.note ? h('p', { 'class': 'muted' }, pick(tr.note)) : null);
  }

  function render(root, ctx) {
    var p = ctx.params || {};
    S = { mode: p.mode === 'manuscript' ? 'manuscript' : 'studies', filter: FILTERS[p.filter] ? p.filter : 'both', unit: p.unit || null, rater: A.rater(), panel: null };
    if (S.mode === 'manuscript') { S.unit = MANUSCRIPT; }
    MA.dom.mount(root, MA.ui.spinner());
    return Promise.all([A.instrument(TOOL), A.instruments(), MA.api.get('/api/appraisals', { query: { tool: TOOL, answers: '1' }, signal: ctx.signal, toast: false })]).then(function (res) {
      if (!ctx.alive()) { return; }
      S.inst = res[0];
      S.list = res[2].data;
      var modeSeg = h('span', { 'class': 'seg', role: 'group', 'aria-label': t('appraisal.tr.mode') }, ['studies', 'manuscript'].map(function (m) {
        return h('button', { type: 'button', 'class': 'seg-btn', id: 'tr-mode-' + m, 'aria-pressed': S.mode === m ? 'true' : 'false',
          onclick: function () { MA.app.navigate('appraisal-tripod', { mode: m, filter: S.filter }); } }, t('appraisal.tr.mode.' + m));
      }));
      var filterSeg = h('span', { 'class': 'seg', role: 'group', 'aria-label': t('appraisal.tr.filter') }, Object.keys(FILTERS).map(function (f) {
        return h('button', { type: 'button', 'class': 'seg-btn', id: 'tr-filter-' + f, 'aria-pressed': S.filter === f ? 'true' : 'false',
          onclick: function () { MA.app.navigate('appraisal-tripod', nav({ filter: f, unit: S.mode === 'studies' ? S.unit : null })); } }, t('appraisal.tr.filter.' + f));
      }));
      var formSec = h('section', { 'class': 'panel ap-form-panel', id: 'ap-form-panel', i18nAttrs: { 'aria-label': 'appraisal.formAria' }, hidden: !S.unit });
      MA.dom.mount(root,
        h('section', { 'class': 'panel', id: 'tr-head' }, h('h2', { 'class': 'panel-title', i18n: 'appraisal.tr.title' }),
          h('div', { 'class': 'toolbar' }, modeSeg, filterSeg, A.raterField(function () { ctx.rerender(); }),
            MA.apprPanel.exchangeBar(function () { return S.rater; }, TOOL, function () { MA.app.refresh(); })),
          MA.apprPanel.sourceLine(res[1], S.inst),
          h('p', { 'class': 'ap-note', id: 'tr-note' }, MA.ui.badge('info', null), ' ', t('appraisal.tr.note')),
          S.mode === 'manuscript' ? h('p', { 'class': 'muted', i18n: 'appraisal.tr.manuscriptHelp' }) : null),
        S.mode === 'studies' ? h('section', { 'class': 'panel', id: 'tr-heat-panel' }, h('h3', null, t('appraisal.tr.heatTitle', { n: String(cols().length) })), heatmap()) : null,
        formSec);
      if (S.unit) {
        var r = p.rater || S.rater;
        if (!r) { MA.dom.mount(formSec, h('p', { 'class': 'ap-msg', role: 'alert', id: 'ap-need-rater' }, t('appraisal.needRater'))); return; }
        S.panel = MA.apprPanel.open(formSec, { tool: TOOL, unit: S.unit, target: null, rater: r, myRater: S.rater, inst: S.inst, label: label(S.unit),
          judgements: false, overall: false, extra: missingBlock,
          onRater: function (other) { MA.app.navigate('appraisal-tripod', nav({ unit: S.unit, rater: other })); },
          onSaved: function () { if (S.mode === 'studies') { ctx.rerender(); } } });
      }
    }, function (err) { if (ctx.alive() && err.code !== 'ABORTED') { MA.dom.mount(root, MA.ui.errorBox(err)); } });
  }

  MA.app.registerScreen({
    id: 'appraisal-tripod', title_key: 'appraisal.nav.tripod', workspace: 'appraisal', tab: 'appraisal', order: 50, render: render,
    onLeave: function () {
      if (S && S.panel && S.panel.dirty()) { return MA.ui.confirm({ title: t('appraisal.unsavedTitle'), message: t('appraisal.unsavedBody'), okLabel: t('appraisal.leave'), danger: true }); }
      return true;
    }
  });
})();
