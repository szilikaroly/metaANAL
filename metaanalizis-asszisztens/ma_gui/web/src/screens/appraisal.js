/* screens/appraisal.js — 5 Torzítás: RoB 2 / ROBINS-I / ROBINS-E / QUADAS-2 / NOS / QUIPS / JBI (terv 3.5.10, 4.11, 5.4, 6.5).
 *
 * Felül az eszköz (a motor natív definíciói: GET /api/instruments), a cél (RoB 2 / ROBINS: eredményenként → a kimenet),
 * az értékelő monogramja (csak UI-preferencia) és a fájlcsere (5. döntés: import fájlból / a beérkezett mappából,
 * export); alatta a vizsgálat × domén mátrix (●◐○ szimbólum ÉS szöveg; a szaggatott keret az implikált ítélet —
 * a motor címkézett javaslata, nem hivatalos eredmény), kitöltöttség, értékelők, konszenzus; a kiválasztott sor
 * értékelése a MA.apprPanel űrlapján (jelző-kérdések, bizonyíték, „Miért?”, felülbírálás indoklással, AI-vázlat
 * jóváhagyása). Útvonal: #/appraisal?tool=rob2&target=o1&unit=ARONSON1948[&rater=KP].
 * HTTP: GET /api/instruments, GET /api/instruments/<tool>, GET /api/appraisals?tool=, és a MA.apprPanel végpontjai.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var A = MA.appr;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };
  var TID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

  var S = null;

  function studyLabel(unit) {
    var s = (S.list && S.list.studies || []).filter(function (x) { return x.study_id === unit; })[0];
    return s && s.label ? s.label : unit;
  }

  function params(extra) {
    return Object.assign({ tool: S.tool, target: S.target || '' }, extra || {});
  }

  /** a mátrix sora: a megjelenített változat (konszenzus > saját > első emberi > AI-vázlat) — kiválasztás, nem számítás */
  function rowData(unit) {
    var items = S.list.items.filter(function (x) { return x.unit === unit && x.tool === S.tool && (x.target || null) === (S.target || null); });
    var by = function (f) { return items.filter(f)[0] || null; };
    var shown = by(function (x) { return x.rater === 'consensus'; }) || by(function (x) { return x.rater === S.rater; }) ||
      by(function (x) { return x.human; }) || by(function (x) { return x.origin === 'ai_draft'; });
    return { items: items, shown: shown };
  }

  function cell(shownItem, domainId) {
    if (!shownItem) { return h('td', { 'class': 'ap-cell is-empty' }, A.verdictBadge(S.inst, null)); }
    var dj = (shownItem.domain_judgements || []).filter(function (d) { return String(d.domain) === String(domainId) && !d.pass; })[0];
    if (dj && dj.judgement) { return h('td', { 'class': 'ap-cell', dataset: { domain: domainId } }, A.verdictBadge(S.inst, dj.judgement)); }
    var imp = ((shownItem.check || {}).domains || []).filter(function (d) { return String(d.domain) === String(domainId); })[0];
    return h('td', { 'class': 'ap-cell', dataset: { domain: domainId } },
      imp && imp.implied ? A.verdictBadge(S.inst, imp.implied, { implied: true, title: t('appraisal.impliedShort') }) : A.verdictBadge(S.inst, null));
  }

  function overallCell(shownItem) {
    if (!shownItem) { return h('td', { 'class': 'ap-cell is-empty' }, A.verdictBadge(S.inst, null)); }
    var j = shownItem.overall && shownItem.overall.judgement;
    if (j) { return h('td', { 'class': 'ap-cell ap-overall-cell' }, A.verdictBadge(S.inst, j)); }
    var imp = (shownItem.check || {}).overall || {};
    return h('td', { 'class': 'ap-cell ap-overall-cell' }, imp.implied ? A.verdictBadge(S.inst, imp.implied, { implied: true, title: t('appraisal.impliedShort') }) : A.verdictBadge(S.inst, null));
  }

  function units() {
    var out = [];
    (S.list.studies || []).forEach(function (s) { out.push(s.study_id); });
    S.list.items.forEach(function (x) {
      if (x.tool === S.tool && out.indexOf(x.unit) < 0 && x.unit !== 'review' && x.unit !== 'manuscript') { out.push(x.unit); }
    });
    return out;
  }

  function matrix() {
    var doms = (S.inst.domains || []);
    var head = h('tr', null, h('th', { scope: 'col', i18n: 'appraisal.col.study' }),
      doms.map(function (d) { return h('th', { scope: 'col', title: pick(d.title), 'class': 'ap-col-dom' }, A.domainShort(d)); }),
      h('th', { scope: 'col', i18n: 'appraisal.col.overall' }), h('th', { scope: 'col', i18n: 'appraisal.col.filled' }),
      h('th', { scope: 'col', i18n: 'appraisal.col.raters' }), h('th', { scope: 'col', i18n: 'appraisal.col.consensus' }));
    var rows = units().map(function (u) {
      var rd = rowData(u);
      var sh = rd.shown;
      var comp = sh && sh.check ? A.completeness(sh.check) : '—';
      var humans = rd.items.filter(function (x) { return x.human; }).length;
      var cons = rd.items.filter(function (x) { return x.rater === 'consensus'; })[0];
      var selected = S.unit === u;
      return h('tr', { dataset: { unit: u }, 'class': selected ? 'is-selected' : null, 'aria-selected': selected ? 'true' : null },
        h('th', { scope: 'row' }, h('button', { type: 'button', 'class': 'btn-link ap-open', dataset: { unit: u },
          onclick: function () { MA.app.navigate('appraisal', params({ unit: u })); } }, studyLabel(u))),
        doms.map(function (d) { return cell(sh, d.id); }), overallCell(sh),
        h('td', { 'class': 'num' }, comp, sh && sh.check && !sh.check.complete ? [' ', MA.ui.badge('warning', null, { title: t('appraisal.incomplete') })] : null,
          sh && sh.check && sh.check.overrides_missing ? [' ', MA.ui.badge('error', 'X017', { title: t('appraisal.overrideMissingTitle') })] : null),
        h('td', { 'class': 'ap-raters-cell' }, rd.items.filter(function (x) { return x.rater !== 'consensus'; }).map(function (x) {
          return h('span', { 'class': ['ap-chip', 'is-static', !x.human && 'is-nonhuman'], dataset: { rater: x.rater } },
            x.origin === 'ai_draft' ? A.aiBadge() : null, ' ', x.rater, x.status !== 'draft' ? ' ✔' : ' …');
        })),
        h('td', null, cons ? MA.ui.badge('ok', t('appraisal.yes')) : (humans >= 2 ? h('a', { href: MA.app.href('appraisal-consensus', params({ unit: u })), 'class': 'btn btn-sm' },
          t('appraisal.toConsensus')) : h('span', { 'class': 'muted' }, '—'))));
    });
    return [
      h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact ap-matrix', id: 'ap-matrix-table' },
        h('caption', { 'class': 'sr-only' }, t('appraisal.matrixCaption', { tool: A.toolName(S.inst) })),
        h('thead', null, head), h('tbody', null, rows))),
      legend(),
      h('p', { 'class': 'toolbar' },
        h('a', { href: MA.app.href('appraisal-summary', { tool: S.tool, outcome: S.target || null }), 'class': 'btn btn-sm', id: 'ap-to-summary' }, t('appraisal.toSummary')),
        h('a', { href: MA.app.href('appraisal-consensus', params()), 'class': 'btn btn-sm btn-ghost', id: 'ap-to-consensus-list' }, t('appraisal.nav.consensus')))
    ];
  }

  function legend() {
    return h('p', { 'class': 'muted ap-legend', id: 'ap-legend' },
      A.verdicts(S.inst).map(function (v) { return [A.verdictBadge(S.inst, v.value), ' ']; }),
      A.verdictBadge(S.inst, null), ' · ', h('span', { 'class': 'ap-v is-implied ap-legend-implied' }, t('appraisal.legendImplied')));
  }

  function header(root) {
    var list = S.instList;
    var rob = (list.instruments || []).filter(function (x) { return A.isRobFamily(x); });
    var toolSel = h('select', { id: 'ap-tool', onchange: function () { MA.app.navigate('appraisal', { tool: toolSel.value }); } },
      rob.map(function (x) { return h('option', { value: x.key, selected: x.key === S.tool }, pick(x.name_i18n) || pick(x.name) || x.key); }));
    var tgId = MA.dom.uid('ap-tg');
    var dl = MA.dom.uid('ap-tgl');
    var tgInp = h('input', { id: tgId, type: 'text', list: dl, maxlength: '64', size: '10', value: S.target || '', 'aria-describedby': tgId + '-h' });
    function commitTarget() {
      var v = tgInp.value.trim();
      if (v && !TID_RE.test(v)) { tgInp.setAttribute('aria-invalid', 'true'); return; }
      tgInp.setAttribute('aria-invalid', 'false');
      if ((v || null) !== (S.target || null)) { MA.app.navigate('appraisal', { tool: S.tool, target: v }); }
    }
    tgInp.addEventListener('change', commitTarget);
    tgInp.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); commitTarget(); } });
    MA.dom.mount(root,
      h('h2', { 'class': 'panel-title', i18n: 'appraisal.title' }),
      h('div', { 'class': 'toolbar ap-toolbar' },
        h('label', { htmlFor: 'ap-tool', i18n: 'appraisal.tool' }), toolSel,
        h('label', { htmlFor: tgId, i18n: 'appraisal.target' }), tgInp,
        h('datalist', { id: dl }, (S.list.outcomes || []).map(function (o) { return h('option', { value: o.id }, pick(o.name)); })),
        A.raterField(function () { MA.app.navigate('appraisal', params({ unit: S.unit || null })); }),
        MA.apprPanel.exchangeBar(function () { return S.rater; }, S.tool, function () { MA.app.refresh(); })),
      h('p', { 'class': 'muted', id: tgId + '-h' }, t('appraisal.targetHint', { unit: pick(S.inst.unit_label) || (MA.i18n.has('appraisal.units.' + S.inst.unit) ? t('appraisal.units.' + S.inst.unit) : String(S.inst.unit || '—')) })),
      MA.apprPanel.sourceLine(list, S.inst),
      list.engine && list.engine.available === false ? h('p', { 'class': 'ap-msg', role: 'note' }, t('appraisal.engineMissing', { names: (list.engine.missing || []).join(', ') })) : null);
  }

  function render(root, ctx) {
    var p = ctx.params || {};
    // más képernyők linkjei: ?outcome= (Áttekintés X-teendői) a cél, ?study= (Eredmények lefúrása) az egység álneve
    if (p.target === undefined && p.outcome) { p = Object.assign({}, p, { target: p.outcome }); }
    S = { ctx: ctx, tool: p.tool || null, target: p.target === undefined ? null : (p.target || null), unit: p.unit || null,
      rater: A.rater(), list: null, inst: null, instList: null, panel: null };
    var head = h('section', { 'class': 'panel ap-head', id: 'ap-head' }, MA.ui.spinner());
    var mat = MA.proc.section('appraisal.matrix', 'ap-matrix');
    var formSec = h('section', { 'class': 'panel ap-form-panel', id: 'ap-form-panel', hidden: true, i18nAttrs: { 'aria-label': 'appraisal.formAria' } });
    MA.dom.mount(root, head, mat.el, formSec);
    return A.instruments().then(function (list) {
      if (!ctx.alive()) { return null; }
      S.instList = list;
      var rob = (list.instruments || []).filter(function (x) { return A.isRobFamily(x); });
      if (!S.tool || !rob.some(function (x) { return x.key === S.tool; })) {
        var pref = ((MA.store.get('project') || {}).appraisal_tools || []).filter(function (k) { return rob.some(function (x) { return x.key === k; }); })[0];
        S.tool = pref || (rob[0] && rob[0].key) || 'rob2';
      }
      return Promise.all([A.instrument(S.tool), MA.api.get('/api/appraisals', { query: { tool: S.tool }, signal: ctx.signal, toast: false })]);
    }).then(function (res) {
      if (!res || !ctx.alive()) { return; }
      S.inst = res[0];
      S.list = res[1].data;
      if (!S.unit && p.study && (S.list.studies || []).some(function (x) { return x.study_id === p.study; })) { S.unit = p.study; }
      if (p.target === undefined && (S.inst.unit === 'result') && S.list.outcomes && S.list.outcomes.length) {
        S.target = S.list.outcomes[0].id;
      }
      ctx.setTitle(A.toolName(S.inst));
      header(head);
      MA.proc.fill(mat, matrix());
      if (S.unit) { openForm(formSec, p.rater); }
    }, function (err) {
      if (!ctx.alive() || err.code === 'ABORTED') { return; }
      MA.dom.mount(head, h('h2', { 'class': 'panel-title', i18n: 'appraisal.title' }), MA.ui.errorBox(err),
        err.code === 'CAPABILITY_MISSING' ? h('p', { 'class': 'muted', i18n: 'appraisal.engineMissingHelp' }) : null);
      MA.proc.fill(mat, MA.ui.emptyState('appraisal.noData'));
    });
  }

  function openForm(host, raterParam) {
    host.hidden = false;
    var r = raterParam || S.rater;
    if (!r) {
      MA.dom.mount(host, h('p', { 'class': 'ap-msg', role: 'alert', id: 'ap-need-rater' }, t('appraisal.needRater')));
      var inp = MA.dom.$('.ap-rater-input');
      if (inp) { inp.focus(); }
      return;
    }
    S.panel = MA.apprPanel.open(host, {
      tool: S.tool, unit: S.unit, target: S.target, rater: r, myRater: S.rater, inst: S.inst, label: studyLabel(S.unit),
      consensusHref: MA.app.href('appraisal-consensus', params({ unit: S.unit })),
      onRater: function (other) { MA.app.navigate('appraisal', params({ unit: S.unit, rater: other })); },
      onSaved: function () {
        MA.api.get('/api/appraisals', { query: { tool: S.tool }, toast: false }).then(function (env) {
          S.list = env.data;
          var sec = MA.dom.$('#ap-matrix');
          if (sec) { MA.dom.mount(sec.querySelector('.section-body'), matrix()); }
        }, function () { /* a mátrix a következő betöltéskor frissül */ });
      }
    });
  }

  MA.app.registerScreen({
    id: 'appraisal', title_key: 'appraisal.nav.rob', workspace: 'appraisal', tab: 'appraisal', order: 10,
    render: render,
    onLeave: function () {
      if (S && S.panel && S.panel.dirty()) {
        return MA.ui.confirm({ title: t('appraisal.unsavedTitle'), message: t('appraisal.unsavedBody'), okLabel: t('appraisal.leave'), danger: true });
      }
      return true;
    }
  });
})();
