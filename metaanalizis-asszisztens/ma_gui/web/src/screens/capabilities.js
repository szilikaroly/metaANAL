/* screens/capabilities.js — Képességek és adatvédelem (terv 3.5.16, 4.1, 5.2, 7.4–7.5).
 *
 * Adat: store 'caps' (GET /api/capabilities → ma_gui.caps.Caps.report(): {generated, probing, components[], matrix{}, problems[]})
 *       és store 'privacy' (GET /api/privacy → ma_gui.privacy.status()). [Újraszondázás] → POST /api/capabilities/refresh,
 *       majd a keret adatainak újratöltése (MA.app.loadBase), hogy a fejléc is frissüljön.
 * Állapotok (4.1): absent | unusable | legacy | ok — szimbólum ÉS szöveg (a szín sosem egyedüli jelölés).
 * A javaslatok szövege a szerveré, szó szerint. A beavatkozás (.gitignore-blokk, deny, pre-commit őr) a Projekt képernyőn.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };
  var STATES = { ok: 1, unusable: 1, legacy: 1, absent: 1 };

  function stateCell(st, extra) {
    var s = STATES[st] ? st : 'absent';
    return h('span', { 'class': ['cap-state', 'is-' + s], dataset: { state: s } },
      h('span', { 'class': 'cap-sym', 'aria-hidden': 'true' }, MA.ui.symbol('cap_' + s)), ' ', t('caps.state.' + s), extra ? h('span', { 'class': 'muted' }, ' · ' + extra) : null);
  }

  function lvlKind(l) { return l === 'error' ? 'error' : (l === 'warning' ? 'warning' : 'info'); }

  function notes(c) {
    var out = [];
    var eng = c.id === 'metaelemzes' || c.id === 'engine' ? MA.store.get('engine') : null;
    if (eng && eng.selftest && eng.selftest.checks !== undefined) {
      out.push(h('li', null, MA.ui.badge(eng.selftest.ok ? 'ok' : 'error', t('capab.selftest', { passed: String(eng.selftest.passed), checks: String(eng.selftest.checks) }))));
    }
    (c.problems || []).forEach(function (p) { out.push(h('li', null, MA.ui.badge(lvlKind(p.level), null), ' ', pick(p))); });
    if (c.todo) { out.push(h('li', { 'class': 'cap-todo' }, h('span', { 'class': 'muted' }, t('capab.todo') + ' '), pick(c.todo))); }
    (c.known_issues || []).forEach(function (k) {
      out.push(h('li', null, MA.ui.badge('warning', k.id), ' ', pick(k.summary), k.fixed_in ? h('span', { 'class': 'muted' }, ' ' + t('capab.fixedIn', { v: k.fixed_in })) : null));
    });
    if (c.guards && c.guards.length) { out.push(h('li', null, t('capab.guards', { list: c.guards.join(' ') }))); }
    if (c.missing_modules && c.missing_modules.length) { out.push(h('li', null, t('capab.missing', { list: c.missing_modules.join(', ') }))); }
    if (c.drift && c.drift.length) { out.push(h('li', null, MA.ui.badge('warning', t('capab.drift', { list: c.drift.join(', ') })))); }
    if (c.note) { out.push(h('li', null, pick(c.note))); }
    return out.length ? h('ul', { 'class': 'cap-notes' }, out) : '—';
  }

  function contracts(c) {
    var list = Array.isArray(c.contracts) ? c.contracts : [];
    if (!list.length) { return '—'; }
    return h('ul', { 'class': 'cap-notes' }, list.map(function (k) {
      var ok = k.ok !== false;
      return h('li', null, h('span', { 'aria-hidden': 'true' }, ok ? '✔ ' : '⚠ '), String(k.name),
        ok ? null : h('span', { 'class': 'muted' }, ' ' + t('capab.contractMismatch')));
    }));
  }

  function componentTable(caps) {
    var comps = Array.isArray(caps.components) ? caps.components : [];
    return h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table', id: 'cap-table' },
      h('thead', null, h('tr', null, ['component', 'version', 'state', 'contracts', 'notes'].map(function (k) { return h('th', { scope: 'col', i18n: 'capab.col.' + k }); }))),
      h('tbody', null, comps.map(function (c) {
        var mode = [c.mode, c.handshake && c.handshake !== 'ok' && c.state !== 'absent' ? t('capab.handshake', { h: c.handshake }) : null].filter(function (x) { return !!x; }).join(' · ');
        return h('tr', { dataset: { plugin: c.id } },
          h('th', { scope: 'row' }, pick(c.label) || c.id, c.python_version ? h('span', { 'class': 'muted cap-py' }, ' Python ' + c.python_version) : null),
          h('td', null, c.version || '—'),
          h('td', null, stateCell(c.state, mode)),
          h('td', null, contracts(c)),
          h('td', null, notes(c)));
      }))));
  }

  function matrixTable(caps) {
    var m = caps.matrix || {};
    var keys = Object.keys(m);
    if (!keys.length) { return null; }
    return h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: 'cap-matrix' },
      h('thead', null, h('tr', null, ['feature', 'standalone', 'plugin', 'withPlugin', 'note'].map(function (k) { return h('th', { scope: 'col', i18n: 'capab.col.' + k }); }))),
      h('tbody', null, keys.map(function (k) {
        var r = m[k] || {};
        return h('tr', { dataset: { feature: k } },
          h('th', { scope: 'row' }, pick(r.label) || k),
          h('td', null, r.standalone ? MA.ui.badge('ok', null) : MA.ui.badge('neutral', null), ' ', pick(r.standalone_note) || '—'),
          h('td', null, r.plugin || '—'),
          h('td', null, r.plugin ? stateCell(r.with_plugin) : '—'),
          h('td', null, pick(r.note) || '—', r.guards && r.guards.length ? h('span', { 'class': 'muted' }, ' · ' + t('capab.guards', { list: r.guards.join(' ') })) : null));
      }))));
  }

  function yesNo(v, okWhenTrue) {
    if (v === undefined || v === null) { return MA.ui.badge('neutral', t('capab.unknown')); }
    var good = okWhenTrue ? !!v : !v;
    return MA.ui.badge(good ? 'ok' : 'warning', t(v ? 'capab.yes' : 'capab.no'));
  }

  function privacyPanel(p) {
    if (!p) { return MA.ui.emptyState('capab.noPrivacy'); }
    var v = p.vault || {};
    var cloud = p.cloud_sync;
    var hist = p.history || {};
    var tracked = Array.isArray(p.tracked_sensitive_files) ? p.tracked_sensitive_files : [];
    var rows = [
      ['class', h('span', null, h('strong', null, p.data_class || '?'), p.data_class_label ? ' — ' + pick(p.data_class_label) : '')],
      ['vault', h('span', null, MA.ui.badge(v.tracked ? 'warning' : 'ok', t(v.tracked ? 'capab.vault.tracked' : (v.under_root ? 'capab.vault.idle' : 'capab.vault.none'))), v.reason ? ' ' + v.reason : '')],
      ['gitignore', yesNo(p.gitignore_block_present, true)],
      ['precommit', yesNo(p.precommit_guard_installed, true)],
      ['cloud', cloud ? h('span', null, MA.ui.badge('warning', String(cloud.kind || '')), ' ', String(cloud.path || '')) : MA.ui.badge('ok', t('capab.none'))],
      ['deny', yesNo(p.deny_rule_present, true)],
      ['tracked', tracked.length ? h('span', null, MA.ui.badge('error', String(tracked.length)), ' ', tracked.join(', ')) : MA.ui.badge('ok', t('capab.none'))],
      ['history', hist.checked === false ? MA.ui.badge('neutral', t('capab.notChecked')) : yesNo(hist.in_history, false)],
      ['hold', p.write_hold && p.write_hold.active ? h('span', null, MA.ui.badge('error', t('capab.yes')), ' ', p.write_hold.reason || '') : MA.ui.badge('ok', t('capab.no'))]
    ];
    var recs = Array.isArray(p.recommendations) ? p.recommendations : [];
    return [
      h('dl', { 'class': 'cap-privacy', id: 'cap-privacy' }, rows.map(function (r) { return [h('dt', { i18n: 'capab.p.' + r[0] }), h('dd', null, r[1])]; })),
      recs.length ? h('div', null, h('h3', { i18n: 'capab.recs' }), h('ul', { 'class': 'item-list', id: 'cap-recs' }, recs.map(function (x) { return h('li', { 'class': 'item' }, pick(x)); }))) : null,
      h('p', null, h('a', { 'class': 'btn', href: MA.app.href('project') }, t('capab.toProject')))
    ];
  }

  function render(root, ctx) {
    var caps = MA.store.get('caps') || {};
    var err = MA.store.get('capsError');
    var status = h('span', { 'class': 'muted', role: 'status', id: 'cap-status' },
      caps.generated ? t('capab.generated', { ts: MA.i18n.ts(caps.generated) }) : '', caps.probing ? ' · ' + t('capab.probing') : '');
    var btn = h('button', { type: 'button', 'class': 'btn', id: 'cap-refresh', onclick: function () {
      btn.disabled = true;
      status.textContent = t('capab.probingNow');
      MA.api.post('/api/capabilities/refresh', {}, { signal: ctx.signal }).then(function () { return MA.app.loadBase(); }).then(function () {
        if (ctx.alive()) { MA.ui.toast({ kind: 'success', title: t('capab.refreshed') }); ctx.rerender(); }
      }, function () { if (ctx.alive()) { btn.disabled = false; status.textContent = ''; } });
    } }, t('capab.refresh'));
    var probs = Array.isArray(caps.problems) ? caps.problems : [];
    MA.dom.mount(root,
      h('section', { 'class': 'panel', 'aria-labelledby': 'cap-h' },
        h('h2', { 'class': 'panel-title', id: 'cap-h', i18n: 'capab.title' }),
        h('p', { 'class': 'cap-status' }, btn, status),
        err ? MA.ui.errorBox(err) : null,
        probs.length ? h('ul', { 'class': 'item-list' }, probs.map(function (p) { return h('li', { 'class': 'item' }, MA.ui.badge(lvlKind(p.level), null), ' ', pick(p)); })) : null,
        componentTable(caps),
        h('p', { 'class': 'muted', i18n: 'legend.symbols' })),
      caps.matrix ? h('section', { 'class': 'panel', 'aria-labelledby': 'cap-m-h' }, h('h2', { 'class': 'panel-title', id: 'cap-m-h', i18n: 'capab.matrix' }), matrixTable(caps)) : null,
      MA.adaptersCaps ? MA.adaptersCaps.panel(ctx) : null,   // v1: funkciónkénti adapter-állapot és teendő (components/adapters_caps.js)
      h('section', { 'class': 'panel', 'aria-labelledby': 'cap-p-h' }, h('h2', { 'class': 'panel-title', id: 'cap-p-h', i18n: 'capab.privacy' }), privacyPanel(MA.store.get('privacy'))));
  }

  MA.app.registerScreen({ id: 'capabilities', title_key: 'screen.capabilities.title', workspace: 'process', tab: null, order: 10, render: render });
})();
