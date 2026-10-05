/* screens/appraisal_consensus.js — 5 Torzítás › Konszenzus: két független értékelő egymás mellett (terv 3.5.10, 5.4, 6.5).
 *
 * GET /api/appraisals/consensus/<unit>/<tool>?target=&a=&b= → a két EMBERI értékelés, a motor egyezés-számítása (κ CI-vel,
 * egyezés-arány — kész szövegként), a meglévő konszenzus-változat és a kizárt AI-vázlatok (6. döntés: sosem értékelő).
 * Az eltéréseknél tételenként A vagy B választható (indoklással); a konszenzus-változat mentése:
 * PUT /api/appraisals/<unit>/<tool>?rater=consensus (status: consensus, assessor = A, second_assessor = B; If-Match).
 * A felület nem számol egyezést: a κ és az arány a motoré; itt csak a választások összesítése (hány eltérés eldöntött).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var A = MA.appr;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };

  var S = null;

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  function ansLabel(v) {
    if (v === null || v === undefined) { return h('span', { 'class': 'muted' }, '—'); }
    var o = A.answers(S.inst).filter(function (x) { return x.value === v; })[0];
    return h('span', { 'class': 'ap-ans', title: o ? o.text : v, dataset: { v: v } }, o ? o.label : v);
  }

  function counter() {
    var n = 0, done = 0;
    Object.keys(S.rows).forEach(function (k) { if (!S.rows[k].agree) { n += 1; if (S.choice[k]) { done += 1; } } });
    MA.dom.mount(S.counterEl, t('appraisal.cons.decided', { done: String(done), n: String(n) }));
    return n === done;
  }

  function chooser(key, row) {
    if (row.agree) { return h('span', { 'class': 'ap-agree', title: t('appraisal.cons.agreeTitle') }, '='); }
    var name = MA.dom.uid('cs');
    var reason = h('input', { type: 'text', 'class': 'cs-reason', maxlength: '500', size: '18', 'aria-label': t('appraisal.cons.reasonFor', { key: key }),
      value: S.reason[key] || '', placeholder: t('appraisal.cons.reason') });
    reason.addEventListener('change', function () { S.reason[key] = reason.value.trim(); });
    return h('span', { 'class': 'cs-choice', role: 'radiogroup', 'aria-label': t('appraisal.cons.chooseFor', { key: key }) },
      ['a', 'b'].map(function (side) {
        var id = MA.dom.uid('csr');
        return h('span', { 'class': 'ap-opt' }, h('input', { type: 'radio', id: id, name: name, value: side, checked: S.choice[key] === side,
          dataset: { key: key, side: side }, onchange: function () { S.choice[key] = side; counter(); } }),
        h('label', { htmlFor: id }, side === 'a' ? 'A' : 'B'));
      }), reason);
  }

  function itemTable() {
    var a = S.data.a.doc, b = S.data.b.doc;
    var items = A.itemsFor(S.inst, a.scope || b.scope, null);
    var rows = [];
    items.forEach(function (it) {
      var key = A.keyOf(it);
      var va = (A.ans(a, key) || {}).value || null, vb = (A.ans(b, key) || {}).value || null;
      var agree = va === vb;
      S.rows[key] = { agree: agree, kind: 'answer' };
      rows.push(h('tr', { dataset: { key: key }, 'class': agree ? null : 'is-diff' },
        h('th', { scope: 'row' }, it.id), h('td', { 'class': 'cs-text' }, pick(it.text)),
        h('td', null, ansLabel(va)), h('td', null, ansLabel(vb)),
        h('td', null, agree ? h('span', { 'class': 'ap-agree' }, '=') : h('span', { 'class': 'ap-diff', title: t('appraisal.cons.diffTitle') }, '≠')),
        h('td', null, chooser(key, S.rows[key]))));
    });
    (S.inst.domains || []).forEach(function (d) {
      var ja = (A.judg(a, 'domain_judgements', d.id, null) || {}).judgement || null;
      var jb = (A.judg(b, 'domain_judgements', d.id, null) || {}).judgement || null;
      if (!ja && !jb) { return; }
      var key = 'dom:' + d.id;
      S.rows[key] = { agree: ja === jb, kind: 'domain', domain: d.id };
      rows.push(h('tr', { dataset: { key: key }, 'class': ['cs-judg-row', ja === jb ? null : 'is-diff'] },
        h('th', { scope: 'row' }, 'D' + d.id), h('td', null, t('appraisal.cons.domainJudgement', { title: pick(d.title) })),
        h('td', null, ja ? A.verdictBadge(S.inst, ja) : '—'), h('td', null, jb ? A.verdictBadge(S.inst, jb) : '—'),
        h('td', null, ja === jb ? '=' : '≠'), h('td', null, chooser(key, S.rows[key]))));
    });
    var oa = (a.overall || {}).judgement || null, ob = (b.overall || {}).judgement || null;
    if (oa || ob) {
      S.rows.overall = { agree: oa === ob, kind: 'overall' };
      rows.push(h('tr', { dataset: { key: 'overall' }, 'class': ['cs-judg-row', oa === ob ? null : 'is-diff'] },
        h('th', { scope: 'row', i18n: 'appraisal.col.overall' }), h('td', null, ''),
        h('td', null, oa ? A.verdictBadge(S.inst, oa) : '—'), h('td', null, ob ? A.verdictBadge(S.inst, ob) : '—'),
        h('td', null, oa === ob ? '=' : '≠'), h('td', null, chooser('overall', S.rows.overall))));
    }
    return h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact cs-table', id: 'cs-table' },
      h('caption', { 'class': 'sr-only' }, t('appraisal.cons.caption')),
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, '#'), h('th', { scope: 'col', i18n: 'appraisal.cons.col.item' }),
        h('th', { scope: 'col' }, 'A: ' + S.data.a.rater), h('th', { scope: 'col' }, 'B: ' + S.data.b.rater),
        h('th', { scope: 'col', i18n: 'appraisal.cons.col.agree' }), h('th', { scope: 'col', i18n: 'appraisal.cons.col.choice' }))),
      h('tbody', null, rows)));
  }

  /** a választásokból összeállított konszenzus-változat (adatválogatás, nem számítás) */
  function buildDoc() {
    var a = S.data.a.doc, b = S.data.b.doc;
    var base = S.data.consensus.doc ? clone(S.data.consensus.doc) : clone(a);
    var doc = Object.assign(base, { schema: 'szk.appraisal/v1', tool: S.tool, scope: a.scope || b.scope, target: clone(a.target),
      assessor: S.data.a.rater, second_assessor: S.data.b.rater, status: 'consensus', origin: 'human', approved_by: null, approved_at: null,
      answers: {}, domain_judgements: [], applicability: clone(a.applicability || []), overall: null });
    Object.keys(S.rows).forEach(function (key) {
      var r = S.rows[key];
      var side = r.agree ? 'a' : S.choice[key];
      var src = side === 'b' ? b : a;
      if (r.kind === 'answer') {
        var an = A.ans(src, key);
        if (an) {
          var c = clone(an);
          if (!r.agree && S.reason[key]) { c.comment = t('appraisal.cons.commentPrefix') + ' ' + S.reason[key]; }
          doc.answers[key] = c;
        }
      } else if (r.kind === 'domain') {
        var dj = A.judg(src, 'domain_judgements', r.domain, null);
        if (dj) {
          var cdj = clone(dj);
          if (!r.agree && S.reason[key]) { cdj.rationale = [cdj.rationale, t('appraisal.cons.commentPrefix') + ' ' + S.reason[key]].filter(function (x) { return !!x; }).join(' — '); }
          doc.domain_judgements.push(cdj);
        }
      } else if (r.kind === 'overall') {
        doc.overall = src.overall ? clone(src.overall) : null;
        if (doc.overall && !r.agree && S.reason[key]) { doc.overall.rationale = [doc.overall.rationale, t('appraisal.cons.commentPrefix') + ' ' + S.reason[key]].filter(function (x) { return !!x; }).join(' — '); }
      }
    });
    return doc;
  }

  function save() {
    MA.dom.clear(S.msg);
    if (!counter()) { MA.dom.mount(S.msg, h('p', { 'class': 'ap-msg' }, t('appraisal.cons.undecided'))); return; }
    var doc = buildDoc();
    MA.api.put(A.apiPath(S.unit, S.tool), { doc: doc }, { query: A.query(S.target, 'consensus'), ifMatch: S.data.consensus.etag || undefined, toast: false })
      .then(function (env) {
        MA.ui.toast({ kind: 'success', title: t('appraisal.cons.saved'), message: env.data.path, details: env.warnings && env.warnings.length ? env.warnings : null });
        S.ctx.rerender();
      }, function (err) { MA.dom.mount(S.msg, MA.ui.errorBox(err)); });
  }

  function prefill() {
    var c = S.data.consensus.doc;
    if (!c) { return; }
    var a = S.data.a.doc, b = S.data.b.doc;
    Object.keys(S.rows).forEach(function (key) {
      var r = S.rows[key];
      if (r.agree) { return; }
      var cv, av, bv;
      if (r.kind === 'answer') { cv = (A.ans(c, key) || {}).value; av = (A.ans(a, key) || {}).value; bv = (A.ans(b, key) || {}).value; }
      else if (r.kind === 'domain') { cv = (A.judg(c, 'domain_judgements', r.domain, null) || {}).judgement; av = (A.judg(a, 'domain_judgements', r.domain, null) || {}).judgement; bv = (A.judg(b, 'domain_judgements', r.domain, null) || {}).judgement; }
      else { cv = (c.overall || {}).judgement; av = (a.overall || {}).judgement; bv = (b.overall || {}).judgement; }
      if (cv && cv === av) { S.choice[key] = 'a'; } else if (cv && cv === bv) { S.choice[key] = 'b'; }
    });
  }

  function agreementLine(ag) {
    if (!ag) { return null; }
    return h('p', { 'class': 'cs-agreement', id: 'cs-agreement' },
      h('strong', { 'class': 'num', id: 'cs-kappa' }, pick(ag.kappa_text) || '—'), ' · ',
      t('appraisal.cons.agreementPct', { pct: pick(ag.agreement_pct_text) || '—' }), ' · ',
      t('appraisal.cons.disagree', { n: ag.disagree === undefined || ag.disagree === null ? '—' : String(ag.disagree) }),
      h('span', { 'class': 'muted' }, ' — ' + t('appraisal.cons.fromEngine')));
  }

  function chooseUnit(root, list) {
    var counts = {};
    list.items.forEach(function (x) {
      if ((x.target || null) !== (S.target || null)) { return; }
      counts[x.unit] = counts[x.unit] || { humans: 0, cons: false };
      if (x.human) { counts[x.unit].humans += 1; }
      if (x.rater === 'consensus') { counts[x.unit].cons = true; }
    });
    var ready = Object.keys(counts).filter(function (u) { return counts[u].humans >= 2; });
    MA.dom.mount(root, h('section', { 'class': 'panel' }, h('h2', { 'class': 'panel-title', i18n: 'appraisal.cons.title' }),
      h('p', { 'class': 'muted', i18n: 'appraisal.cons.pick' }),
      ready.length ? h('ul', { 'class': 'item-list', id: 'cs-units' }, ready.map(function (u) {
        var lab = (list.studies.filter(function (s) { return s.study_id === u; })[0] || {}).label || u;
        return h('li', { 'class': 'item' }, h('a', { href: MA.app.href('appraisal-consensus', { tool: S.tool, target: S.target || '', unit: u }) }, lab),
          counts[u].cons ? [' ', MA.ui.badge('ok', t('appraisal.cons.done'))] : null);
      })) : MA.ui.emptyState('appraisal.cons.noneReady')));
  }

  function render(root, ctx) {
    var p = ctx.params || {};
    S = { ctx: ctx, tool: p.tool || 'rob2', target: p.target || null, unit: p.unit || null, rows: {}, choice: {}, reason: {}, msg: null };
    MA.dom.mount(root, MA.ui.spinner());
    var instP = A.instrument(S.tool);
    if (!S.unit) {
      return Promise.all([instP, MA.api.get('/api/appraisals', { query: { tool: S.tool }, signal: ctx.signal, toast: false })]).then(function (res) {
        if (ctx.alive()) { S.inst = res[0]; chooseUnit(root, res[1].data); }
      }, function (err) { if (ctx.alive() && err.code !== 'ABORTED') { MA.dom.mount(root, MA.ui.errorBox(err)); } });
    }
    var q = { target: S.target || undefined, a: p.a || undefined, b: p.b || undefined };
    return Promise.all([instP, MA.api.get('/api/appraisals/consensus/' + encodeURIComponent(S.unit) + '/' + encodeURIComponent(S.tool),
      { query: q, signal: ctx.signal, toast: false })]).then(function (res) {
      if (!ctx.alive()) { return; }
      S.inst = res[0];
      S.data = res[1].data;
      S.warnings = res[1].warnings || [];
      draw(root);
    }, function (err) { if (ctx.alive() && err.code !== 'ABORTED') { MA.dom.mount(root, MA.ui.errorBox(err)); } });
  }

  function draw(root) {
    var d = S.data;
    var head = h('section', { 'class': 'panel', id: 'cs-head' },
      h('h2', { 'class': 'panel-title' }, t('appraisal.cons.titleFor', { unit: d.unit, tool: A.toolName(S.inst), target: d.target || '—' })),
      h('p', { 'class': 'muted' }, t('appraisal.cons.intro')),
      d.excluded && d.excluded.length ? h('p', { 'class': 'cs-excluded', id: 'cs-excluded' }, A.aiBadge(), ' ',
        t('appraisal.cons.excluded', { raters: d.excluded.map(function (x) { return x.rater; }).join(', ') })) : null);
    if (!d.ready) {
      MA.dom.mount(root, head, h('section', { 'class': 'panel' }, h('p', { 'class': 'ap-msg', id: 'cs-not-ready' }, d.reason || t('appraisal.cons.notReady')),
        h('p', null, h('a', { href: MA.app.href('appraisal', { tool: S.tool, target: S.target || '', unit: d.unit }), 'class': 'btn btn-sm' }, t('appraisal.cons.toForm')))));
      return;
    }
    S.rows = {};
    var table = itemTable();
    prefill();
    S.counterEl = h('span', { 'class': 'cs-counter', id: 'cs-counter', 'aria-live': 'polite' });
    S.msg = h('div', { role: 'alert', 'class': 'ap-msg-host', id: 'cs-msg' });
    MA.dom.mount(root, head,
      h('section', { 'class': 'panel', id: 'cs-body' },
        agreementLine(d.agreement),
        d.consensus.exists ? h('p', null, MA.ui.badge('ok', t('appraisal.cons.exists')), ' ', h('span', { 'class': 'muted' }, d.consensus.path)) : null,
        table,
        h('div', { 'class': 'toolbar' }, S.counterEl,
          h('button', { type: 'button', 'class': 'btn btn-primary', id: 'cs-save', onclick: save }, t('appraisal.cons.save')),
          h('a', { href: MA.app.href('appraisal', { tool: S.tool, target: S.target || '', unit: d.unit, rater: 'consensus' }), 'class': 'btn btn-ghost' }, t('appraisal.cons.openForm'))),
        S.msg));
    counter();
  }

  MA.app.registerScreen({ id: 'appraisal-consensus', title_key: 'appraisal.nav.consensus', workspace: 'appraisal', tab: 'appraisal', order: 20, render: render });
})();
