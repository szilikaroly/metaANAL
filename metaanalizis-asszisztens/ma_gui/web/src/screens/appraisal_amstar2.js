/* screens/appraisal_amstar2.js — 6 GRADE/SoF › AMSTAR 2 önellenőrzés: a saját áttekintés 16 tétele (7 kritikus), a motor
 * besorolása MINDKÉT „részben igen” konvencióval (KB AMSTAR2-00: alapból „meets”), a project audit bizonyíték-javaslatai
 * (terv 3.5.13, 4.11, 4.15 amstar2_hints, 5.4).
 *
 * Egység: review (a saját áttekintés), eszköz: amstar2 → 04_torzitas_kockazat/appraisals/review.amstar2.<értékelő>.json
 * (ugyanaz a fájl, mint a GET/PUT /api/amstar2-é). Űrlap és mentés: MA.apprPanel (/api/appraisals/review/amstar2);
 * a besorolás a motor check.amstar2 mezője (rating, alternative, differs, provisional) — a felület nem számol.
 * A javaslatok: GET /api/audit/project → amstar2_hints {tétel: {suggested, evidence[]}} (csak javaslat; az ember dönt).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var A = MA.appr;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };
  var TOOL = 'amstar2';
  var UNIT = 'review';

  var S = null;

  function ratingBadge(r) {
    if (!r || !r.rating) { return h('span', { 'class': 'muted' }, '—'); }
    return h('span', { 'class': ['am-rating', 'am-' + r.rating], dataset: { rating: r.rating } }, pick(r.rating_text) || A.verdictLabel(S.inst, r.rating));
  }

  function ids(list) { return list && list.length ? list.join(', ') : '—'; }

  function classification(view, check) {
    var am = check && check.amstar2;
    if (!am) { return h('p', { 'class': 'muted', id: 'am-class' }, t('appraisal.am.noRating')); }
    var alt = am.alternative || null;
    return h('div', { 'class': 'am-class', id: 'am-class' },
      h('p', null, h('strong', { i18n: 'appraisal.am.rating' }), ' ', ratingBadge(am), ' ',
        h('span', { 'class': 'muted' }, t('appraisal.am.convention', { conv: t('appraisal.am.conv.' + (am.convention || 'meets')), kb: am.kb_ref || 'AMSTAR2-00' })),
        am.provisional ? [' ', MA.ui.badge('warning', t('appraisal.am.provisional', { items: ids(am.unanswered) }))] : null),
      h('p', { 'class': 'muted' }, t('appraisal.am.flaws', { crit: ids(am.critical_flaws), weak: ids(am.weaknesses) })),
      alt ? h('p', { 'class': ['am-alt', am.differs ? 'is-diff' : 'is-same'], id: 'am-alt' },
        am.differs ? [h('strong', { i18n: 'appraisal.am.sensitivity' }), ' ', t('appraisal.am.ifWeakness'), ' ', ratingBadge(alt)]
          : t('appraisal.am.same')) : null,
      h('p', { 'class': 'ap-note' }, MA.ui.badge('info', null), ' ', t('appraisal.am.notGrade')));
  }

  function hintsBlock() {
    var hints = S.hints || {};
    var keys = Object.keys(hints);
    if (!keys.length) { return h('p', { 'class': 'muted', id: 'am-hints' }, t('appraisal.am.noHints')); }
    return h('div', { id: 'am-hints' }, h('h3', { i18n: 'appraisal.am.hintsTitle' }), h('ul', { 'class': 'item-list' }, keys.map(function (k) {
      var hnt = hints[k] || {};
      var o = A.answers(S.inst).filter(function (x) { return x.value === hnt.suggested; })[0];
      var it = (S.inst.items || []).filter(function (x) { return x.id === k; })[0];
      return h('li', { 'class': 'item', dataset: { item: k } }, MA.ui.badge(it && it.critical ? 'blocker' : 'neutral', k, it && it.critical ? { symbol: 'K' } : null),
        h('span', { 'class': 'item-title' }, t('appraisal.am.suggested', { answer: o ? o.text : String(hnt.suggested || '—') })),
        h('span', { 'class': 'item-detail muted' }, (hnt.evidence || []).map(pick).join(' · ')));
    })), h('p', { 'class': 'muted', i18n: 'appraisal.am.hintsNote' }));
  }

  function render(root, ctx) {
    var p = ctx.params || {};
    S = { rater: A.rater(), panel: null, hints: null };
    MA.dom.mount(root, MA.ui.spinner());
    var audit = MA.api.get('/api/audit/project', { signal: ctx.signal, toast: false }).then(function (env) { return env.data; }, function () { return null; });
    return Promise.all([A.instrument(TOOL), A.instruments(), audit]).then(function (res) {
      if (!ctx.alive()) { return; }
      S.inst = res[0];
      S.hints = res[2] && res[2].amstar2_hints ? res[2].amstar2_hints : null;
      var formSec = h('section', { 'class': 'panel ap-form-panel', id: 'ap-form-panel', i18nAttrs: { 'aria-label': 'appraisal.formAria' } });
      MA.dom.mount(root,
        h('section', { 'class': 'panel', id: 'am-head' }, h('h2', { 'class': 'panel-title', i18n: 'appraisal.am.title' }),
          h('div', { 'class': 'toolbar' }, A.raterField(function () { ctx.rerender(); }),
            MA.apprPanel.exchangeBar(function () { return S.rater; }, TOOL, function () { MA.app.refresh(); })),
          MA.apprPanel.sourceLine(res[1], S.inst),
          h('p', { 'class': 'muted' }, t('appraisal.am.intro')), hintsBlock()),
        formSec);
      var r = p.rater || S.rater;
      if (!r) { MA.dom.mount(formSec, h('p', { 'class': 'ap-msg', role: 'alert', id: 'ap-need-rater' }, t('appraisal.needRater'))); return; }
      S.panel = MA.apprPanel.open(formSec, { tool: TOOL, unit: UNIT, target: null, rater: r, myRater: S.rater, inst: S.inst, label: t('appraisal.am.unit'),
        judgements: false, overall: true, extra: classification,
        onRater: function (other) { MA.app.navigate('appraisal-amstar2', { rater: other }); } });
    }, function (err) { if (ctx.alive() && err.code !== 'ABORTED') { MA.dom.mount(root, MA.ui.errorBox(err)); } });
  }

  MA.app.registerScreen({
    id: 'appraisal-amstar2', title_key: 'appraisal.nav.amstar2', workspace: 'appraisal', tab: 'grade', order: 60, render: render,
    onLeave: function () {
      if (S && S.panel && S.panel.dirty()) { return MA.ui.confirm({ title: t('appraisal.unsavedTitle'), message: t('appraisal.unsavedBody'), okLabel: t('appraisal.leave'), danger: true }); }
      return true;
    }
  });
})();
