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

  function ansLabel(v, a) {
    // résztételes tételnél (AMSTAR 2 9., 11.) a részek is (RCT / NRSI)
    var parts = a && a.parts && typeof a.parts === 'object' ? Object.keys(a.parts).filter(function (k) { return !!a.parts[k]; }) : [];
    if ((v === null || v === undefined) && !parts.length) { return h('span', { 'class': 'muted' }, '—'); }
    if (parts.length) {
      return h('span', { 'class': 'ap-ans', dataset: { v: v || '' } }, parts.map(function (k, i) { return (i ? ' · ' : '') + k + ' ' + A.answerLabel(S.inst, a.parts[k]); }).join(''));
    }
    var o = A.answers(S.inst).filter(function (x) { return x.value === v; })[0];
    return h('span', { 'class': 'ap-ans', title: o ? o.text : v, dataset: { v: v } }, o ? o.label : v);
  }

  function ansSig(a) { return a ? JSON.stringify([a.value || null, a.parts || null]) : JSON.stringify([null, null]); }

  function counter() {
    var n = 0, done = 0;
    Object.keys(S.rows).forEach(function (k) { if (!S.rows[k].agree) { n += 1; if (S.choice[k]) { done += 1; } } });
    MA.dom.mount(S.counterEl, t('appraisal.cons.decided', { done: String(done), n: String(n) }));
    return n === done;
  }

  function chooser(key, row) {
    if (row.agree) { return h('span', { 'class': 'ap-agree', title: t('appraisal.cons.agreeTitle') }, '='); }
    var name = MA.dom.uid('cs');
    var reason = h('input', { type: 'text', 'class': 'cs-reason', maxlength: '500', size: '18', 'aria-label': t('appraisal.cons.reasonFor', { key: row.label || key }),
      value: S.reason[key] || '', placeholder: t('appraisal.cons.reason'), dataset: { key: key } });
    reason.addEventListener('change', function () { S.reason[key] = reason.value.trim(); });
    return h('span', { 'class': 'cs-choice', role: 'radiogroup', 'aria-label': t('appraisal.cons.chooseFor', { key: row.label || key }) },
      ['a', 'b'].map(function (side) {
        var id = MA.dom.uid('csr');
        return h('span', { 'class': 'ap-opt' }, h('input', { type: 'radio', id: id, name: name, value: side, checked: S.choice[key] === side,
          dataset: { key: key, side: side }, onchange: function () { S.choice[key] = side; counter(); } }),
        h('label', { htmlFor: id }, side === 'a' ? 'A' : 'B'));
      }), reason);
  }

  function passesOf(a, b) {
    if (!S.inst.passes || !S.inst.passes.length) { return [null]; }
    var sc = a.scope || b.scope || 'both';
    return sc === 'both' ? S.inst.passes.map(function (p) { return p.id; }) : [sc];
  }

  /** a táblázat sorai (adatválogatás): tételek, doménítéletek, alkalmazhatóság (menetenként is), összítélet(ek) */
  function collectRows() {
    var a = S.data.a.doc, b = S.data.b.doc;
    var rows = [];
    A.itemsFor(S.inst, a.scope || b.scope, null).forEach(function (it) {
      var key = A.keyOf(it);
      var aa = A.ans(a, key), bb = A.ans(b, key);
      rows.push({ key: key, kind: 'answer', it: it, a: aa, b: bb, agree: ansSig(aa) === ansSig(bb), label: A.itemId(it) + (it.pass ? ' (' + t('appraisal.pass.' + it.pass) + ')' : '') });
    });
    var ps = passesOf(a, b);
    (S.inst.domains || []).forEach(function (d) {
      ps.forEach(function (p) {
        [['domain_judgements', 'dom'], ['applicability', 'app']].forEach(function (cc) {
          if (cc[0] === 'applicability' && !d.applicability) { return; }
          var ja = (A.judg(a, cc[0], d.id, p) || {}).judgement || null;
          var jb = (A.judg(b, cc[0], d.id, p) || {}).judgement || null;
          if (!ja && !jb) { return; }
          rows.push({ key: cc[1] + ':' + d.id + (p ? ':' + p : ''), kind: 'judgement', coll: cc[0], domain: d, pass: p, a: ja, b: jb, agree: ja === jb,
            label: A.domainShort(d) + (p ? ' (' + t('appraisal.pass.' + p) + ')' : '') });
        });
      });
    });
    ps.forEach(function (p) {
      if (!p) { return; }
      var oa = (A.judg(a, 'overall_passes', 'overall', p) || {}).judgement || null;
      var ob = (A.judg(b, 'overall_passes', 'overall', p) || {}).judgement || null;
      if (!oa && !ob) { return; }
      rows.push({ key: 'ovp:' + p, kind: 'judgement', coll: 'overall_passes', domain: { id: 'overall' }, pass: p, a: oa, b: ob, agree: oa === ob,
        label: t('appraisal.col.overall') + ' (' + t('appraisal.pass.' + p) + ')' });
    });
    var o1 = (a.overall || {}).judgement || null, o2 = (b.overall || {}).judgement || null;
    if (o1 || o2) { rows.push({ key: 'overall', kind: 'overall', a: o1, b: o2, agree: o1 === o2, label: t('appraisal.col.overall') }); }
    return rows;
  }

  function itemTable(rows) {
    var trs = rows.map(function (r) {
      var title = r.kind === 'answer' ? h('td', { 'class': 'cs-text' }, pick(r.it.text))
        : h('td', null, r.kind === 'overall' || r.coll === 'overall_passes' ? '' : (r.coll === 'applicability' ? t('appraisal.applicability') : t('appraisal.cons.domainJudgement', { title: pick(r.domain.title) })));
      var cellA = r.kind === 'answer' ? ansLabel((r.a || {}).value || null, r.a) : (r.a ? A.verdictBadge(S.inst, r.a, { domain: r.domain && r.domain.id !== 'overall' ? r.domain : null }) : '—');
      var cellB = r.kind === 'answer' ? ansLabel((r.b || {}).value || null, r.b) : (r.b ? A.verdictBadge(S.inst, r.b, { domain: r.domain && r.domain.id !== 'overall' ? r.domain : null }) : '—');
      return h('tr', { dataset: { key: r.key }, 'class': [r.kind !== 'answer' && 'cs-judg-row', r.agree ? null : 'is-diff'] },
        h('th', { scope: 'row' }, r.label), title, h('td', null, cellA), h('td', null, cellB),
        h('td', null, r.agree ? h('span', { 'class': 'ap-agree' }, '=') : h('span', { 'class': 'ap-diff', title: t('appraisal.cons.diffTitle') }, '≠')),
        h('td', null, chooser(r.key, r)));
    });
    return h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact cs-table', id: 'cs-table' },
      h('caption', { 'class': 'sr-only' }, t('appraisal.cons.caption')),
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, '#'), h('th', { scope: 'col', i18n: 'appraisal.cons.col.item' }),
        h('th', { scope: 'col' }, 'A: ' + S.data.a.rater), h('th', { scope: 'col' }, 'B: ' + S.data.b.rater),
        h('th', { scope: 'col', i18n: 'appraisal.cons.col.agree' }), h('th', { scope: 'col', i18n: 'appraisal.cons.col.choice' }))),
      h('tbody', null, trs)));
  }

  function withReason(text, reason) {
    return [text, t('appraisal.cons.commentPrefix') + ' ' + reason].filter(function (x) { return !!x; }).join(' — ');
  }

  /** a választásokból összeállított konszenzus-változat (adatválogatás, nem számítás) */
  function buildDoc() {
    var a = S.data.a.doc, b = S.data.b.doc;
    var base = S.data.consensus.doc ? clone(S.data.consensus.doc) : clone(a);
    var doc = Object.assign(base, { schema: 'szk.appraisal/v1', tool: S.tool, scope: a.scope || b.scope, target: clone(a.target),
      assessor: S.data.a.rater, second_assessor: S.data.b.rater, status: 'consensus', origin: 'human', approved_by: null, approved_at: null,
      answers: {}, domain_judgements: [], applicability: [], overall_passes: [], overall: null });
    Object.keys(S.rows).forEach(function (key) {
      var r = S.rows[key];
      var side = r.agree ? 'a' : S.choice[key];
      var src = side === 'b' ? b : a;
      var reason = !r.agree ? S.reason[key] : null;
      if (r.kind === 'answer') {
        var an = A.ans(src, key);
        if (an) {
          var c = clone(an);
          if (reason) { c.comment = t('appraisal.cons.commentPrefix') + ' ' + reason; }
          doc.answers[key] = c;
        }
      } else if (r.kind === 'judgement') {
        var dj = A.judg(src, r.coll, r.domain.id, r.pass);
        if (dj) {
          var cdj = clone(dj);
          if (reason) { cdj.rationale = withReason(cdj.rationale, reason); }
          doc[r.coll].push(cdj);
        }
      } else if (r.kind === 'overall') {
        doc.overall = src.overall ? clone(src.overall) : null;
        if (doc.overall && reason) { doc.overall.rationale = withReason(doc.overall.rationale, reason); }
      }
    });
    if (!doc.overall_passes.length) { delete doc.overall_passes; }
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

  /** a mentett konszenzus-indoklás visszafejtése („… — Konszenzus: <ok>” vagy „Konszenzus: <ok>”) */
  function savedReason(text) {
    var pre = t('appraisal.cons.commentPrefix');
    if (typeof text !== 'string' || !text) { return ''; }
    var i = text.lastIndexOf(pre);
    if (i < 0 || (i > 0 && (i < 3 || text.slice(i - 3, i) !== ' — '))) { return ''; }
    return text.slice(i + pre.length).trim();
  }

  /** a meglévő konszenzus-változat döntései (A vagy B) és indoklásai a táblázat FELRAJZOLÁSA ELŐTT (UX-3: különben a
   *  rádiógombok üresen jelennek meg, és egy újabb mentés törölné a rögzített indoklásokat) */
  function prefill(rows) {
    var c = S.data.consensus.doc;
    if (!c) { return; }
    var a = S.data.a.doc, b = S.data.b.doc;
    rows.forEach(function (r) {
      if (r.agree) { return; }
      var cv, av, bv, why = '';
      if (r.kind === 'answer') {
        var ca = A.ans(c, r.key);
        cv = ca ? ansSig(ca) : null; av = ansSig(A.ans(a, r.key)); bv = ansSig(A.ans(b, r.key));
        why = savedReason((ca || {}).comment);
      } else if (r.kind === 'judgement') {
        var cj = A.judg(c, r.coll, r.domain.id, r.pass) || {};
        cv = cj.judgement; av = r.a; bv = r.b;
        why = savedReason(cj.rationale);
      } else {
        cv = (c.overall || {}).judgement; av = r.a; bv = r.b;
        why = savedReason((c.overall || {}).rationale);
      }
      if (cv && cv === av) { S.choice[r.key] = 'a'; } else if (cv && cv === bv) { S.choice[r.key] = 'b'; }
      if (why) { S.reason[r.key] = why; }
    });
  }

  function kappaPart(label, k) {
    if (!k) { return null; }
    return [' · ', h('span', { 'class': 'cs-kappa-sub' }, label + ': ', h('span', { 'class': 'num' }, pick(k.kappa_text || k.text) || '—'))];
  }

  /** F5: a felső κ TÉTELSZINTŰ (jelző-kérdések, másodlagos) — így is felirat; mellette a doménítéletek és az
   *  összítélet eltérésének száma, és ha a motor ad, a doménítéletek κ-ja (elsődleges mérték) */
  function agreementLine(ag, rows) {
    if (!ag) { return null; }
    var domDiff = rows.filter(function (r) { return r.kind === 'judgement' && r.coll === 'domain_judgements' && !r.agree; }).length;
    var domAll = rows.filter(function (r) { return r.kind === 'judgement' && r.coll === 'domain_judgements'; }).length;
    var ovDiff = rows.filter(function (r) { return (r.kind === 'overall' || r.coll === 'overall_passes') && !r.agree; }).length;
    return h('div', { 'class': 'cs-agreement', id: 'cs-agreement' },
      h('p', { id: 'cs-judg-agree' }, h('strong', null, t('appraisal.cons.judgDiff', { n: String(domDiff), all: String(domAll) })),
        ovDiff ? [' · ', h('strong', { 'class': 'cs-ov-diff' }, t('appraisal.cons.overallDiff'))] : null,
        kappaPart(t('appraisal.cons.judgKappa'), ag.judgement_kappa), kappaPart(t('appraisal.cons.overallKappa'), ag.overall_kappa)),
      h('p', null, h('span', { 'class': 'cs-item-kappa-label' }, t('appraisal.cons.itemKappa')), ' ',
        h('strong', { 'class': 'num', id: 'cs-kappa' }, pick(ag.kappa_text) || '—'), ' · ',
        t('appraisal.cons.agreementPct', { pct: pick(ag.agreement_pct_text) || '—' }), ' · ',
        t('appraisal.cons.disagree', { n: ag.disagree === undefined || ag.disagree === null ? '—' : String(ag.disagree) }),
        h('span', { 'class': 'muted' }, ' — ' + t('appraisal.cons.fromEngine'))),
      ag.notes && ag.notes.length ? h('p', { 'class': 'muted cs-notes' }, ag.notes.map(pick).join(' ')) : null);
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
    var rows = collectRows();
    rows.forEach(function (r) { S.rows[r.key] = r; });
    prefill(rows);
    var table = itemTable(rows);
    S.counterEl = h('span', { 'class': 'cs-counter', id: 'cs-counter', 'aria-live': 'polite' });
    S.msg = h('div', { role: 'alert', 'class': 'ap-msg-host', id: 'cs-msg' });
    MA.dom.mount(root, head,
      h('section', { 'class': 'panel', id: 'cs-body' },
        agreementLine(d.agreement, rows),
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
