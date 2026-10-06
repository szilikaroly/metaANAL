/* components/adapters_validator.js — a validator plugin keresztellenőrzése egy értékelésre (terv 4.11, 5.0 H1–H4, H12, H13, 5.4, 6.5).
 *
 * MA.adaptersValidator:
 *   box(getDoc, opts?) → <section>   „Ellenőrzés a validatorral” gomb; kattintásra POST /api/validator/check
 *                                    {doc} (csak a válaszértékek és ítéletek mennek — idézet, indoklás, megjegyzés NEM),
 *                                    és a szk.appraisal-result/v1 megjelenítése: mód (json | bridge), teljesség (a
 *                                    munkapad számolja; a validator saját számlálása mellette, H1/H2-nél „nem
 *                                    megbízható” jelöléssel), implikált ítélet az algoritmus címkéjével — SOHA nem
 *                                    hivatalos eredményként —, AMSTAR 2 (konvencióval), GRADE (H3: feloldatlan
 *                                    „gyanított”, a bizonyosság a motoré), NOS, és a bekapcsolt őrök szövege.
 *                                    getDoc() → szk.appraisal/v1 (pl. az MA.apprPanel aktuális dokumentuma).
 *                                    opts.engine() → a motor ellenőrzése (szk.appraisal-result/v1, pl. az űrlap
 *                                    check-je): ekkor „Összevetés a motorral” — teljesség, implikált ítélet, AMSTAR 2,
 *                                    GRADE, NOS soronként EGYEZIK, vagy ELTÉR az okkal (H12/H13 őr, más algoritmus,
 *                                    a validator egyszerűsített szabálya, ideiglenes eredmény). Csak címkék és
 *                                    darabszámok összevetése — számítás nincs.
 *   render(result, engine?) → DOM     (a fenti eredmény-nézet; tesztekhez és más képernyőknek)
 *   minimal(doc) → a küldendő, szöveg nélküli dokumentum
 * Az ítélet a felhasználóé: a doboz csak a plugin véleményét mutatja, semmit nem ír vissza.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };
  var LEVELS = { low: 1, some: 1, high: 1 };

  function minimal(doc) {
    var out = { schema: 'szk.appraisal/v1', tool: doc.tool, scope: doc.scope || null, target: doc.target || {},
      assessor: doc.assessor || 'x', status: doc.status || 'draft', origin: doc.origin || 'human', answers: {} };
    Object.keys(doc.answers || {}).forEach(function (k) {
      var a = doc.answers[k] || {};
      var m = { value: a.value === undefined ? null : a.value };
      if (a.resolution && (a.resolution.step === 0 || a.resolution.step === -1 || a.resolution.step === -2)) {
        m.resolution = { step: a.resolution.step, rationale: a.resolution.rationale ? 'x' : '' };
      }
      out.answers[k] = m;
    });
    return out;
  }

  function val(v) {
    var k = 'adp.val.v.' + String(v);
    return MA.i18n.has(k) ? t(k) : String(v);
  }

  function verdictText(res, o) {
    if (o.implied) { return val(o.implied); }
    if (o.level && LEVELS[o.level]) { return t('adp.val.level.' + o.level); }
    return '—';
  }

  function gmsg(res, effects) {
    return (Array.isArray(res.guards) ? res.guards : []).filter(function (g) { return effects.indexOf(g.effect) >= 0; })
      .map(function (g) { return pick(g.message); }).join(' ');
  }

  /** soronként: {k, agree, text, why} — az ok a validator őreiből vagy a szabály/algoritmus különbségéből */
  function compare(res, eng) {
    var rows = [];
    var num = gmsg(res, ['numbering_differs']);
    function row(k, a, b, text, why) {
      if (a === undefined || b === undefined) { return; }
      var same = a === b;
      rows.push({ k: k, agree: same, text: text, why: same ? '' : (why || t('adp.val.cmp.unexplained')) });
    }
    row('completeness', String(res.answered) + '/' + String(res.expected), String(eng.answered) + '/' + String(eng.expected),
      t('adp.val.cmp.completeness', { v: String(res.answered) + '/' + String(res.expected), e: String(eng.answered) + '/' + String(eng.expected) }),
      num || t('adp.val.cmp.compWhy'));
    var vo = res.overall || {}, eo = eng.overall || {};
    if ((vo.implied || eo.implied) && !res.amstar2 && !res.grade) {
      var why = gmsg(res, ['numbering_differs', 'polarity_differs', 'rollup_unreliable']) ||
        (vo.algorithm !== eo.algorithm ? t('adp.val.cmp.algWhy', { v: t('adp.val.alg.' + (vo.algorithm || 'none')), e: t('adp.val.alg.' + (eo.algorithm || 'none')) })
          : (vo.provisional ? t('adp.val.cmp.provisional') : t('adp.val.cmp.ruleWhy')));
      row('overall', vo.implied || null, eo.implied || null,
        t('adp.val.cmp.overall', { v: vo.implied ? val(vo.implied) : '—', e: eo.implied ? val(eo.implied) : '—' }), why);
    }
    if (res.amstar2 && eng.amstar2) {
      var ea = eng.amstar2.convention === res.amstar2.convention ? eng.amstar2 : (eng.amstar2.alternative || {});
      row('amstar2', res.amstar2.rating || null, ea.rating || null,
        t('adp.val.cmp.amstar', { v: res.amstar2.rating ? val(res.amstar2.rating) : '—', e: ea.rating ? val(ea.rating) : '—', conv: res.amstar2.convention || '—' }),
        res.amstar2.provisional ? t('adp.val.cmp.provisional') : null);
    }
    if (res.grade && eng.grade) {
      row('grade', res.grade.certainty || null, eng.grade.certainty || null,
        t('adp.val.cmp.grade', { v: res.grade.certainty ? val(res.grade.certainty) : '—', e: eng.grade.certainty ? val(eng.grade.certainty) : '—' }),
        gmsg(res, ['rollup_unreliable']) || null);
    }
    if (res.nos && eng.nos && typeof res.nos.total === 'number') {
      row('nos', res.nos.total, eng.nos.total, t('adp.val.cmp.nos', { v: String(res.nos.total), e: String(eng.nos.total) }), t('adp.val.cmp.nosWhy'));
    }
    return rows;
  }

  function cmpList(res, eng) {
    var rows = compare(res, eng);
    return h('div', { 'class': 'adp-val-cmp-box' }, h('h4', { i18n: 'adp.val.cmp.title' }),
      h('ul', { 'class': 'item-list adp-val-cmp' }, rows.map(function (r) {
        return h('li', { 'class': 'item', dataset: { k: r.k, agree: r.agree ? '1' : '0' } },
          MA.ui.badge(r.agree ? 'ok' : 'warning', t(r.agree ? 'adp.val.cmp.agree' : 'adp.val.cmp.differs'), { symbol: r.agree ? '=' : '≠' }), ' ', r.text,
          r.why ? h('span', { 'class': 'item-detail adp-val-why' }, r.why) : null);
      })));
  }

  function render(res, eng) {
    var o = res.overall || {};
    var rep = res.validator_reported || null;
    var guards = Array.isArray(res.guards) ? res.guards : [];
    var untrusted = guards.filter(function (g) { return g.effect === 'completeness_recomputed'; }).map(function (g) { return g.id; });
    // H12 / H13: a validator más kérdéseket / polaritást lát — a teljessége nem vethető össze a motoréval (UX-9)
    var notComparable = res.comparable === false || guards.some(function (g) { return g.effect === 'numbering_differs'; });
    var notes = (Array.isArray(res.notes) ? res.notes : []);
    var notesText = notes.map(pick);
    var noteSaysOfficial = notesText.some(function (x) { return /hivatalos folyamatábra|official flowchart/i.test(x); });
    var rows = [];
    // a javított validator (2.0.0) is bridge-módban fut (nincs kézfogás), de őr nélkül: ott nem „régi, őrökkel”
    var plain = res.mode === 'bridge' && Array.isArray(res.guards_active) && !res.guards_active.length;
    var modeTxt = plain ? t('adp.mode.bridgePlain') : (MA.adaptersCaps ? MA.adaptersCaps.modeText(res.mode) : String(res.mode || ''));
    rows.push(h('li', { 'class': 'item', dataset: { k: 'mode' } }, MA.ui.badge(res.legacy && !plain ? 'warning' : 'ok', null), ' ',
      t('adp.val.modeLine', { version: res.validator_version || '?', mode: modeTxt })));
    if (notComparable) {
      rows.push(h('li', { 'class': 'item', dataset: { k: 'completeness', comparable: '0' } }, MA.ui.badge('neutral', null), ' ',
        t('adp.val.completenessSent', { text: res.completeness_text || '—' })));
    } else {
      rows.push(h('li', { 'class': 'item', dataset: { k: 'completeness' } }, MA.ui.badge(res.complete ? 'ok' : 'warning', null), ' ',
        t(untrusted.length ? 'adp.val.completeness' : 'adp.val.completenessValidator', { text: res.completeness_text || '—' }),
        rep ? h('span', { 'class': 'item-detail muted' }, t('adp.val.reported', { answered: String(rep.answered), expected: String(rep.expected) }),
          rep.trusted === false ? ' — ' + t('adp.val.untrusted', { guards: untrusted.join(', ') || '—' }) : null) : null));
    }
    if (o.algorithm && (o.implied || o.level)) {
      // a „nem hivatalos” mondat egyszer: ha a validator megjegyzése már kimondja, itt nem ismételjük (UX-9)
      rows.push(h('li', { 'class': 'item', dataset: { k: 'implied' } }, MA.ui.badge('info', null), ' ',
        t('adp.val.implied', { alg: t('adp.val.alg.' + o.algorithm), verdict: verdictText(res, o) }),
        o.official || noteSaysOfficial ? null : h('span', { 'class': 'item-detail adp-notofficial' }, t('adp.val.notOfficial'))));
    }
    if (res.amstar2) {
      rows.push(h('li', { 'class': 'item', dataset: { k: 'amstar2' } }, MA.ui.badge('info', null), ' ',
        t('adp.val.amstar', { rating: res.amstar2.rating ? val(res.amstar2.rating) : '—', conv: res.amstar2.convention || '—' })));
    }
    if (res.grade) {
      var g = res.grade;
      rows.push(h('li', { 'class': 'item', dataset: { k: 'grade' } }, MA.ui.badge(g.certainty ? 'info' : 'warning', null), ' ',
        t('adp.val.grade', { c: g.certainty ? val(g.certainty) : '—' }),
        g.reliable === false ? h('span', { 'class': 'item-detail' }, t('adp.val.gradeUnreliable')) : null,
        (g.unresolved || []).indexOf('publication_bias') >= 0 ? h('span', { 'class': 'item-detail adp-unresolved' }, t('adp.val.unresolved')) : null));
    }
    if (res.nos && typeof res.nos.total === 'number') {
      rows.push(h('li', { 'class': 'item', dataset: { k: 'nos' } }, MA.ui.badge('info', null), ' ', t('adp.val.nos', { total: String(res.nos.total), max: String(res.nos.max) })));
    }
    notes.forEach(function (n) { rows.push(h('li', { 'class': 'item muted' }, pick(n))); });
    return h('div', { 'class': 'adp-val-result' },
      eng ? cmpList(res, eng) : null,
      h('ul', { 'class': 'item-list' }, rows),
      guards.length ? h('div', { 'class': 'adp-guard-box' }, h('h4', { i18n: 'adp.val.guards' }),
        h('ul', { 'class': 'adp-guards' }, guards.map(function (gd) { return h('li', { dataset: { guard: gd.id } }, MA.ui.badge('info', gd.id), ' ', pick(gd.message)); }))) : null);
  }

  function box(getDoc, opts) {
    opts = opts || {};
    var out = h('div', { 'class': 'adp-val-out', role: 'status', 'aria-live': 'polite' });
    var btn = h('button', { type: 'button', 'class': 'btn btn-sm', id: opts.id || 'adp-val-run', onclick: function () {
      var cur = getDoc ? getDoc() : null;
      if (!cur) { return; }
      // FID-4: a motor-összevetés UGYANARRA a dokumentumra (pillanatkép a kattintáskor) — nem az élő ellenőrzés esetleg
      // korábbi eredményére; opts.engine(doc) → érték vagy Promise
      var doc = JSON.parse(JSON.stringify(cur));
      btn.disabled = true;
      MA.dom.mount(out, MA.ui.spinner('adp.val.running'));
      var engP = Promise.resolve(opts.engine ? opts.engine(doc) : null).then(null, function () { return null; });
      Promise.all([MA.api.post('/api/validator/check', { doc: minimal(doc) }, { toast: false }), engP]).then(function (res) {
        btn.disabled = false;
        MA.dom.mount(out, render(res[0].data || {}, res[1] || null));
      }, function (err) {
        btn.disabled = false;
        MA.dom.mount(out, h('p', { 'class': 'muted adp-val-down' }, t('adp.val.down', { msg: err && err.message ? String(err.message) : '—' })));
      });
    } }, t('adp.val.run'));
    return h('section', { 'class': 'adp-val', 'aria-label': t('adp.val.title') },
      h('h3', { i18n: 'adp.val.title' }), btn, out);
  }

  MA.adaptersValidator = { box: box, render: render, minimal: minimal, compare: compare };
})();
