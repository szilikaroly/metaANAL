/* components/adapters_caps.js — „Funkciók a pluginokkal”: funkciónkénti állapot és teendő (terv 3.5.16, 4.1, 5.0–5.5).
 *
 * MA.adaptersCaps:
 *   panel(ctx) → <section>   GET /api/adapters (szk.ma.adapters/v1) táblázata: funkció, plugin, állapot (szimbólum ÉS
 *                            szöveg: ok ● / legacy ◑ / unusable ◐ / absent ○), mód (json | bridge), mi működik plugin
 *                            nélkül, a magyar teendő szó szerint (a szerveré), és a bekapcsolt H-őrök a szövegükkel.
 *                            A helyi SVG-átalakítók (PNG/PDF) sora alul. A Képességek képernyő (screens/capabilities.js)
 *                            illeszti be; az újraszondázás után a képernyő újrarajzolódik, ez újratölt.
 *   stateCell(state, extra?) → <span>  állapotjelölő (más adapter-képernyők is használják)
 *   modeText(mode) → szöveg
 * A szerver üzeneteit és teendőit textContent-tel, szó szerint mutatjuk.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };
  var STATES = { ok: 1, legacy: 1, unusable: 1, absent: 1 };

  function stateCell(state, extra) {
    var s = STATES[state] ? state : 'absent';
    return h('span', { 'class': ['cap-state', 'adp-state', 'is-' + s], dataset: { state: s } },
      h('span', { 'class': 'cap-sym', 'aria-hidden': 'true' }, MA.ui.symbol('cap_' + s)), ' ', t('adp.state.' + s),
      extra ? h('span', { 'class': 'muted' }, ' · ' + extra) : null);
  }

  function modeText(mode) {
    return mode === 'json' || mode === 'bridge' ? t('adp.mode.' + mode) : t('adp.mode.none');
  }

  function guardList(guards) {
    if (!guards || !guards.length) { return null; }
    return h('ul', { 'class': 'adp-guards' }, guards.map(function (g) {
      return h('li', { dataset: { guard: g.id } }, MA.ui.badge('info', g.id), ' ', pick(g.text));
    }));
  }

  function table(data) {
    var feats = Array.isArray(data.features) ? data.features : [];
    return h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: 'adp-features' },
      h('thead', null, h('tr', null, ['feature', 'plugin', 'state', 'mode', 'fallback', 'remedy'].map(function (k) {
        return h('th', { scope: 'col', i18n: 'adp.caps.col.' + k });
      }))),
      h('tbody', null, feats.map(function (f) {
        var fb = pick(f.fallback);
        return h('tr', { dataset: { feature: f.id, state: f.state } },
          h('th', { scope: 'row' }, pick(f.label) || f.id),
          h('td', null, f.plugin || '—'),
          h('td', null, stateCell(f.state)),
          h('td', null, modeText(f.mode)),
          h('td', null, fb || '—', f.fallback_used ? h('span', null, ' ', MA.ui.badge('neutral', t('adp.caps.fallbackUsed'))) : null),
          h('td', { 'class': 'adp-remedy' }, f.remedy ? pick(f.remedy) : '—',
            f.guards && f.guards.length ? h('div', { 'class': 'adp-guard-box' }, h('span', { 'class': 'muted' }, t('adp.caps.guardsLabel')), guardList(f.guards)) : null));
      }))));
  }

  function footer(data) {
    var convs = (Array.isArray(data.converters) ? data.converters : []).filter(function (c) { return c.ok; });
    var loc = data.composer_location || {};
    return h('ul', { 'class': 'item-list adp-foot', id: 'adp-foot' },
      h('li', { 'class': 'item', id: 'adp-converters' },
        MA.ui.badge(convs.length ? 'ok' : 'neutral', null), ' ',
        convs.length ? t('adp.caps.converters', { list: convs.map(function (c) { return c.name; }).join(', ') }) : t('adp.caps.convertersNone'),
        !convs.length && data.converter_remedy ? h('span', { 'class': 'item-detail muted' }, pick(data.converter_remedy)) : null),
      loc.configured ? h('li', { 'class': 'item' }, MA.ui.badge('ok', null), ' ', t('adp.caps.composerLoc', { project: loc.project || '—' })) : null);
  }

  function panel(ctx) {
    var body = h('div', { 'class': 'section-body', 'aria-busy': 'true' }, MA.ui.spinner());
    var sec = h('section', { 'class': 'panel', id: 'adp-caps', 'aria-labelledby': 'adp-caps-h' },
      h('h2', { 'class': 'panel-title', id: 'adp-caps-h' }, h('span', { i18n: 'adp.caps.title' }), ' ',
        MA.why ? MA.why.button({ title: t('adp.caps.why.title'), detail: t('adp.caps.why.body') }, { id: 'adp-caps-why' }) : null),
      h('p', { 'class': 'muted', i18n: 'adp.caps.intro' }),
      body);
    MA.api.get('/api/adapters', { signal: ctx && ctx.signal, toast: false }).then(function (env) {
      if (ctx && ctx.alive && !ctx.alive()) { return; }
      var d = env.data || {};
      MA.dom.mount(body, table(d), footer(d));
      body.removeAttribute('aria-busy');
    }, function (err) {
      if (err && err.code === 'ABORTED') { return; }
      MA.dom.mount(body, h('p', { 'class': 'muted', i18n: 'adp.caps.loadFailed' }), err && err.message ? h('p', { 'class': 'muted' }, String(err.message)) : null);
      body.removeAttribute('aria-busy');
    });
    return sec;
  }

  MA.adaptersCaps = { panel: panel, stateCell: stateCell, modeText: modeText };
})();
