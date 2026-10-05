/* components/adapters_validator.js — a validator plugin keresztellenőrzése egy értékelésre (terv 4.11, 5.0 H1–H4, 5.4, 6.5).
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
 *   render(result) → DOM              (a fenti eredmény-nézet; tesztekhez és más képernyőknek)
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

  function render(res) {
    var o = res.overall || {};
    var rep = res.validator_reported || null;
    var guards = Array.isArray(res.guards) ? res.guards : [];
    var untrusted = guards.filter(function (g) { return g.effect === 'completeness_recomputed'; }).map(function (g) { return g.id; });
    var rows = [];
    rows.push(h('li', { 'class': 'item', dataset: { k: 'mode' } }, MA.ui.badge(res.legacy ? 'warning' : 'ok', null), ' ',
      t('adp.val.modeLine', { version: res.validator_version || '?', mode: MA.adaptersCaps ? MA.adaptersCaps.modeText(res.mode) : String(res.mode || '') })));
    rows.push(h('li', { 'class': 'item', dataset: { k: 'completeness' } }, MA.ui.badge(res.complete ? 'ok' : 'warning', null), ' ',
      t('adp.val.completeness', { text: res.completeness_text || '—' }),
      rep ? h('span', { 'class': 'item-detail muted' }, t('adp.val.reported', { answered: String(rep.answered), expected: String(rep.expected) }),
        rep.trusted === false ? ' — ' + t('adp.val.untrusted', { guards: untrusted.join(', ') || '—' }) : null) : null));
    if (o.algorithm && (o.implied || o.level)) {
      rows.push(h('li', { 'class': 'item', dataset: { k: 'implied' } }, MA.ui.badge('info', null), ' ',
        t('adp.val.implied', { alg: t('adp.val.alg.' + o.algorithm), verdict: verdictText(res, o) }),
        o.official ? null : h('span', { 'class': 'item-detail adp-notofficial' }, t('adp.val.notOfficial'))));
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
    (Array.isArray(res.notes) ? res.notes : []).forEach(function (n) { rows.push(h('li', { 'class': 'item muted' }, pick(n))); });
    return h('div', { 'class': 'adp-val-result' },
      h('ul', { 'class': 'item-list' }, rows),
      guards.length ? h('div', { 'class': 'adp-guard-box' }, h('h4', { i18n: 'adp.val.guards' }),
        h('ul', { 'class': 'adp-guards' }, guards.map(function (gd) { return h('li', { dataset: { guard: gd.id } }, MA.ui.badge('info', gd.id), ' ', pick(gd.message)); }))) : null);
  }

  function box(getDoc, opts) {
    opts = opts || {};
    var out = h('div', { 'class': 'adp-val-out', role: 'status', 'aria-live': 'polite' });
    var btn = h('button', { type: 'button', 'class': 'btn btn-sm', id: opts.id || 'adp-val-run', onclick: function () {
      var doc = getDoc ? getDoc() : null;
      if (!doc) { return; }
      btn.disabled = true;
      MA.dom.mount(out, MA.ui.spinner('adp.val.running'));
      MA.api.post('/api/validator/check', { doc: minimal(doc) }, { toast: false }).then(function (env) {
        btn.disabled = false;
        MA.dom.mount(out, render(env.data || {}));
      }, function (err) {
        btn.disabled = false;
        MA.dom.mount(out, h('p', { 'class': 'muted adp-val-down' }, t('adp.val.down', { msg: err && err.message ? String(err.message) : '—' })));
      });
    } }, t('adp.val.run'));
    return h('section', { 'class': 'adp-val', 'aria-label': t('adp.val.title') },
      h('h3', { i18n: 'adp.val.title' }), btn, out);
  }

  MA.adaptersValidator = { box: box, render: render, minimal: minimal };
})();
