/* screens/extraction_dual.js — 3 Kinyerés › Kettős kinyerés és egyeztetés (terv 3.5.5, 4.8, 4.9, 6.4 X009, 7.4;
 * 11. fejezet 5. döntés: a második kinyerő a saját gépén dolgozik, fájlcserével).
 *
 * Szerver: GET /api/kettos (kimenetenként az A/B tábla, a konszenzus-fájl, a beérkezett mappa), POST /api/compare
 * (a MOTOR összevetése: eltérések, súgók, hatás a hatásméretre és az összesített becslésre, κ — kész szövegekkel;
 * a szerver ehhez rendeli a döntéseket), POST /api/reconcile (döntés indoklással, visszavonás, konszenzus-CSV a
 * kettos/ mappába vagy a kimenet adattáblájába — If-Match), POST /api/kettos/import|export (fájlcsere, sablon).
 *
 * A felület nem számol és nem következtet: az egyezés, a κ, a súgó és a hatás a motor szövege; itt csak a
 * döntések begyűjtése, a darabszámok kiírása (a szerver progress-éből) és a megjelenítés történik. Minden állapot
 * szimbólumot és szöveget is kap (✔ eldöntve, ≠ nyitott, ⟳ elavult, = csak írásmód, ⊕ csak A, ⊖ csak B) — nem csak
 * színt. A két nézet (eltérés-lista, táblák egymás mellett) 1000 sor fölött virtualizált (MA.vtable).
 *
 * Útvonal: #/dual?outcome=o1[&view=items|tables][&item=<kulcs␟mező>]
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var G = MA.geom;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '—' : fb); };

  var SEP = '␟';
  var ROW_FIELD = '*';
  var SIDES = ['A', 'B'];
  var STATUS = {
    open: { sym: '≠', cls: 'is-open' },
    decided: { sym: '✔', cls: 'is-decided' },
    stale: { sym: '⟳', cls: 'is-stale' },
    auto: { sym: '=', cls: 'is-auto' }
  };
  var ROW_SYM = { only_a: '⊕', only_b: '⊖' };
  // a motor súgó-kódjai → a kezdőknek szóló „Miért?” magyarázat családja (11/6. döntés); ismeretlen kód → 'value'
  var HINT_FAMILY = { x10: 'x10', unit_x10: 'x10', thousands_decimal: 'x10', unit_factor: 'x10', se_sd_swap: 'se_sd_swap',
    variance_sd: 'se_sd_swap', arms_swapped: 'arms_swapped', arm_swap: 'arms_swapped', format_only: 'format_only', rounding: 'rounding',
    typo: 'typo', digit_typo: 'typo', digit_transposition: 'typo', sign: 'sign', percent_for_count: 'percent', events_gt_n: 'events',
    missing: 'missing', missing_a: 'missing', missing_b: 'missing', parse: 'parse', category: 'category', category_adjacent: 'category',
    value: 'value' };

  var S = null;

  function itemId(it) { return it.key + SEP + it.field; }
  function actor() { return MA.store.get('project.user.initials') || MA.store.get('session.user') || null; }
  function fieldLabel(f) { return f === ROW_FIELD ? t('dual.wholeRow') : f; }
  function statusOf(it) {
    if (it.auto) { return 'auto'; }
    if (it.stale) { return 'stale'; }
    if (it.decision) { return 'decided'; }
    return 'open';
  }
  function statusEl(it, compact) {
    var st = statusOf(it);
    var d = STATUS[st];
    return h('span', { 'class': ['dx-st', d.cls], title: t('dual.st.' + st + '.title') },
      h('span', { 'class': 'dx-st-sym', 'aria-hidden': 'true' }, d.sym),
      compact ? h('span', { 'class': 'sr-only' }, t('dual.st.' + st)) : h('span', null, t('dual.st.' + st)));
  }
  function raterOf(side) {
    var r = S.view && S.view.raters ? S.view.raters[side.toLowerCase()] : null;
    if (!r && S.info && S.info.consensus && S.info.consensus.raters) { r = S.info.consensus.raters[side.toLowerCase()]; }
    return r || null;
  }
  function decisionText(it) {
    var d = it.decision;
    if (!d) { return ''; }
    if (it.level === 'row') { return t('dual.dec.row.' + d.chosen + '.' + it.kind); }
    if (d.chosen === 'other') { return t('dual.dec.other', { value: d.value === null || d.value === undefined ? '' : d.value }); }
    return t('dual.dec.' + d.chosen);
  }

  // ---------------------------------------------------------------- base64 és fájlok (fájlcsere)
  function bytesToB64(buf) {
    var bytes = new Uint8Array(buf);
    var parts = [];
    for (var i = 0; i < bytes.length; i += 0x8000) {
      parts.push(String.fromCharCode.apply(null, Array.prototype.slice.call(bytes.subarray(i, i + 0x8000))));
    }
    return window.btoa(parts.join(''));
  }
  function b64ToBlob(b64, type) {
    var bin = window.atob(b64);
    var arr = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) { arr[i] = bin.charCodeAt(i); }
    return new Blob([arr], { type: type || 'text/csv' });
  }
  function download(name, blob) {
    var url = URL.createObjectURL(blob);
    var a = h('a', { href: url, download: name, 'class': 'sr-only' }, name);
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); if (a.parentNode) { a.parentNode.removeChild(a); } }, 1000);
  }
  function pickFile(cb) {
    var inp = h('input', { type: 'file', accept: '.csv,.tsv,.txt,text/csv,text/plain', 'class': 'sr-only' });
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0];
      if (inp.parentNode) { inp.parentNode.removeChild(inp); }
      if (!f) { return; }
      var rd = new FileReader();
      rd.onload = function () { cb(bytesToB64(rd.result), f.name); };
      rd.onerror = function () { MA.ui.toast({ kind: 'error', title: t('dual.import.readError'), message: f.name }); };
      rd.readAsArrayBuffer(f);
    });
    document.body.appendChild(inp);
    inp.click();
  }

  // ---------------------------------------------------------------- import / export (5. döntés)
  function importSide(side, body) {
    var rater = S.raterInputs[side] ? S.raterInputs[side].value.trim() : '';
    var payload = Object.assign({ outcome: S.outcome.id, side: side }, body);
    if (rater) { payload.rater = rater; }
    return MA.api.post('/api/kettos/import', payload, { toast: false }).then(done, function (err) {
      if (err.code === 'CONFLICT') {
        return MA.ui.confirm({ title: t('dual.import.replaceTitle', { side: side }), message: t('dual.import.replaceBody', { side: side }),
          okLabel: t('dual.import.replaceOk'), danger: true }).then(function (yes) {
          if (!yes) { return null; }
          return MA.api.post('/api/kettos/import', Object.assign(payload, { replace: true }), { toast: false }).then(done, fail);
        });
      }
      return fail(err);
    });
    function done(env) {
      var d = env.data;
      MA.ui.toast({ kind: 'success', title: t('dual.import.done.' + d.state, { side: side }), message: d.path,
        details: env.warnings && env.warnings.length ? env.warnings : null });
      S.ctx.rerender();
      return env;
    }
    function fail(err) {
      MA.ui.toast({ kind: 'error', title: t('dual.import.failed', { side: side }), message: err.message, code: err.code });
      return null;
    }
  }

  function exportSide(side, template) {
    return MA.api.post('/api/kettos/export', { outcome: S.outcome.id, side: side, template: !!template }, { toast: true }).then(function (env) {
      var d = env.data;
      download(d.filename, b64ToBlob(d.content_b64, d.media_type));
      MA.ui.toast({ kind: 'success', title: t(template ? 'dual.export.templateDone' : 'dual.export.done'), message: d.filename });
    }, function () { return null; });
  }

  function fileCard(side, st) {
    var exists = st && st.exists;
    var r = raterOf(side) || (side === 'A' ? actor() : '') || '';
    var inp = h('input', { type: 'text', 'class': 'dx-rater', id: 'dx-rater-' + side.toLowerCase(), maxlength: '32', value: r,
      autocomplete: 'off', spellcheck: 'false', 'aria-describedby': 'dx-rater-help' });
    S.raterInputs[side] = inp;
    return h('div', { 'class': 'dx-file', id: 'dx-file-' + side.toLowerCase(), dataset: { side: side } },
      h('h3', null, exists ? MA.ui.badge('ok', t('dual.file.present')) : MA.ui.badge('neutral', t('dual.file.missing'), { symbol: '○' }),
        ' ', t('dual.file.title', { side: side })),
      h('p', { 'class': 'dx-path' }, st ? st.path : '—'),
      h('div', { 'class': 'toolbar' },
        h('label', { htmlFor: inp.id }, t('dual.file.rater')), inp,
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'dx-import-' + side.toLowerCase(),
          onclick: function () { pickFile(function (b64, name) { importSide(side, { content_b64: b64, filename: name }); }); } },
        t('dual.file.import')),
        exists ? h('button', { type: 'button', 'class': 'btn btn-sm', id: 'dx-export-' + side.toLowerCase(), onclick: function () { exportSide(side); } },
          t('dual.file.export'), ' ↓') : null,
        exists ? h('button', { type: 'button', 'class': 'btn btn-sm btn-ghost', id: 'dx-template-' + side.toLowerCase(),
          title: t('dual.file.templateTitle'), onclick: function () { exportSide(side, true); } }, t('dual.file.template', { other: side === 'A' ? 'B' : 'A' }), ' ↓') : null));
  }

  function inboxList(list) {
    var files = (list.inbox || []).filter(function (f) { return !f.outcome_guess || f.outcome_guess === S.outcome.id; });
    if (!files.length) { return null; }
    return h('div', { 'class': 'dx-inbox-wrap', id: 'dx-inbox' },
      h('h3', { 'class': 'panel-subtitle' }, t('dual.inbox.title')),
      h('p', { 'class': 'dx-help' }, t('dual.inbox.help', { dir: (S.info && S.info.dir ? S.info.dir : 'kettos') + '/beerkezett/' })),
      h('ul', { 'class': 'dx-inbox' }, files.map(function (f) {
        return h('li', { dataset: { path: f.path } }, h('code', null, f.name),
          f.side_guess ? h('span', { 'class': 'muted' }, t('dual.inbox.guess', { side: f.side_guess })) : null,
          SIDES.map(function (side) {
            return h('button', { type: 'button', 'class': ['btn', 'btn-sm', f.side_guess === side ? 'btn-primary' : null],
              onclick: function () { importSide(side, { path: f.path }); } }, t('dual.inbox.importAs', { side: side }));
          }));
      })));
  }

  // ---------------------------------------------------------------- összegzés (motor)
  function bar(dis, total) {
    var W = 80, H = 10;
    var sx = G.linear([0, total > 0 ? total : 1], [0, W], { clamp: true });
    return MA.dom.svg('svg', { 'class': 'dx-bar', width: String(W), height: String(H), viewBox: '0 0 ' + W + ' ' + H, 'aria-hidden': 'true', focusable: 'false' },
      MA.dom.svg('rect', { 'class': 'dx-bar-bg', x: '0.5', y: '0.5', width: String(W - 1), height: String(H - 1) }),
      dis > 0 ? MA.dom.svg('rect', { 'class': 'dx-bar-fg', x: '0.5', y: '0.5', width: G.px(G.max(1, sx(dis) - 1)), height: String(H - 1) }) : null);
  }

  function summaryPanel() {
    var cmp = S.view.compare || {};
    var sm = cmp.summary || {};
    var byf = sm.by_field || {};
    var order = (S.view.columns || []).map(function (c) { return c.field; }).filter(function (f) { return byf[f]; });
    Object.keys(byf).forEach(function (f) { if (order.indexOf(f) < 0) { order.push(f); } });
    var kappas = order.filter(function (f) { return byf[f].kappa_text; });
    var n = function (v) { return h('strong', { 'class': 'num' }, v === null || v === undefined ? '—' : String(v)); };
    var line = h('p', { 'class': 'dx-summary-line', id: 'dx-agreement' },
      h('span', null, t('dual.sum.agree'), ' ', n(sm.agree), '/', n(sm.cells_compared), ' ', t('dual.sum.cells'),
        sm.agreement_pct_text ? [' (', MA.ui.num(sm.agreement_pct_text, { id: 'dx-agree-pct' }), ')'] : null),
      h('span', null, '· ', t('dual.sum.disagree'), ' ', n(sm.disagree)),
      h('span', null, '· ', t('dual.sum.onlyA'), ' ', n((cmp.only_a || []).length)),
      h('span', null, '· ', t('dual.sum.onlyB'), ' ', n((cmp.only_b || []).length)),
      h('span', null, '· ', t('dual.sum.matched'), ' ', n(sm.matched)),
      kappas.map(function (f) { return h('span', { 'class': 'dx-kappa', dataset: { field: f } }, '· κ(', f, ') ', MA.ui.num(byf[f].kappa_text)); }),
      h('span', { 'class': 'muted' }, t('dual.sum.engine')));
    var rows = order.map(function (f) {
      var b = byf[f];
      return h('tr', { dataset: { field: f } }, h('th', { scope: 'row' }, f),
        h('td', { 'class': 'num' }, String(b.compared)), h('td', { 'class': 'num' }, String(b.disagree)),
        h('td', null, bar(b.disagree, b.compared)),
        h('td', null, b.kappa_text ? MA.ui.num(b.kappa_text) : b.icc_text ? MA.ui.num(b.icc_text) : h('span', { 'class': 'muted' }, '—'),
          b.kappa_label || b.icc_label ? h('span', { 'class': 'muted' }, ' (' + pick(b.kappa_label || b.icc_label, '') + ')') : null));
    });
    var methods = S.view.agreement_text;
    return h('section', { 'class': 'panel', id: 'dx-summary', 'aria-labelledby': 'dx-summary-h' },
      h('h2', { 'class': 'panel-title', id: 'dx-summary-h' }, t('dual.sum.title')),
      line,
      rows.length ? h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'dx-fields', id: 'dx-fields' },
        h('caption', { 'class': 'sr-only' }, t('dual.sum.fieldsCaption')),
        h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('dual.sum.col.field')), h('th', { scope: 'col' }, t('dual.sum.col.compared')),
          h('th', { scope: 'col' }, t('dual.sum.col.disagree')), h('th', { scope: 'col' }, h('span', { 'class': 'sr-only' }, t('dual.sum.col.bar'))),
          h('th', { scope: 'col' }, t('dual.sum.col.kappa')))),
        h('tbody', null, rows))) : null,
      h('div', { 'class': 'toolbar' },
        methods ? h('button', { type: 'button', 'class': 'btn btn-sm', id: 'dx-methods', onclick: function () { methodsModal(methods); } }, t('dual.sum.methods')) : null,
        h('span', { 'class': 'muted' }, t('dual.sum.key', { key: (S.view.key || []).join(' + ') || '—' })),
        h('button', { type: 'button', 'class': 'btn btn-sm btn-ghost', id: 'dx-key-btn', onclick: keyDialog }, t('dual.key.change'))));
  }

  function methodsModal(text) {
    var body = h('div', null,
      h('p', { 'class': 'muted' }, t('dual.methods.help')),
      h('h3', null, 'English (Methods)'), h('p', { 'class': 'dx-methods', id: 'dx-methods-en', lang: 'en' }, text.en || ''),
      h('h3', null, 'Magyar'), h('p', { 'class': 'dx-methods', id: 'dx-methods-hu', lang: 'hu' }, text.hu || ''),
      h('div', { 'class': 'toolbar' }, MA.ui.copyButton(function () { return text.en || ''; }, t('dual.methods.copyEn')),
        MA.ui.copyButton(function () { return text.hu || ''; }, t('dual.methods.copyHu'))));
    MA.ui.modal({ title: t('dual.methods.title'), body: body, size: 'md', actions: [{ label: t('common.close'), kind: 'primary' }] });
  }

  function keyDialog() {
    var cands = S.view.key_candidates || [];
    var cur = S.view.key || [];
    var boxes = cands.map(function (f) {
      var id = MA.dom.uid('dxk');
      return h('label', { 'for': id, 'class': 'dx-key-opt' }, h('input', { type: 'checkbox', id: id, value: f, checked: cur.indexOf(f) >= 0 }), ' ', f);
    });
    var msg = h('div', { role: 'alert', 'class': 'dx-msg-host' });
    MA.ui.modal({ title: t('dual.key.title'), size: 'sm',
      body: h('div', null, h('p', { 'class': 'dx-help' }, t('dual.key.help')), h('fieldset', { 'class': 'dx-choice' },
        h('legend', null, t('dual.key.legend')), boxes), msg),
      actions: [{ label: t('common.cancel'), kind: 'ghost' }, { label: t('dual.key.apply'), kind: 'primary', onClick: function () {
        var key = boxes.map(function (b) { return b.querySelector('input'); }).filter(function (i) { return i.checked; }).map(function (i) { return i.value; });
        if (!key.length) { MA.dom.mount(msg, h('p', null, t('dual.key.none'))); return false; }
        S.key = key;
        compare();
        return true;
      } }] });
  }

  // ---------------------------------------------------------------- kapu (X009) és haladás
  function gatePanel() {
    var p = S.view.progress || {};
    var g = S.view.gate || {};
    return h('div', { 'class': ['dx-gate', g.blocked ? 'is-blocked' : 'is-ok'], id: 'dx-gate', role: 'status', 'aria-live': 'polite' },
      h('span', { 'class': 'dx-counter', id: 'dx-counter' }, t('dual.gate.decided', { decided: String(p.decided), total: String(p.total) })),
      h('span', null, t('dual.gate.unresolved', { n: String(p.unresolved) })),
      p.stale ? h('span', null, MA.ui.badge('stale', t('dual.gate.stale', { n: String(p.stale) }), { symbol: '⟳' })) : null,
      p.auto ? h('span', { 'class': 'muted' }, t('dual.gate.auto', { n: String(p.auto) })) : null,
      h('span', { id: 'dx-gate-msg' }, MA.ui.badge(g.blocked ? 'warning' : 'ok', 'X009', { symbol: g.blocked ? '⚠' : '✔' }), ' ', g.message || ''),
      MA.why.button({ kb: 'X009', code: 'X009', plain: { asks: t('dual.why.gate.asks'), because: t('dual.why.gate.because'), change: t('dual.why.gate.change') } }, { compact: true }));
  }

  // ---------------------------------------------------------------- eltérés-lista
  function filteredItems() {
    var list = (S.view.items || []).slice();
    if (S.filter.impact) {
      // „hatásos”: a motor ítélete (impact.material), ha adja; különben: van hatás-szövege
      var byEngine = list.some(function (it) { return it.impact && typeof it.impact.material === 'boolean'; });
      list = list.filter(function (it) {
        if (!it.impact) { return false; }
        return byEngine ? it.impact.material === true : !!(it.impact.text || it.impact.pooled_text);
      });
    }
    if (S.filter.open) { list = list.filter(function (it) { var s = statusOf(it); return s === 'open' || s === 'stale'; }); }
    if (S.filter.sort === 'impact') {
      list = list.map(function (it, i) { return { it: it, i: i }; }).sort(function (a, b) {
        var ra = a.it.impact && typeof a.it.impact.rank === 'number' ? a.it.impact.rank : null;
        var rb = b.it.impact && typeof b.it.impact.rank === 'number' ? b.it.impact.rank : null;
        if (ra === rb) { return a.i - b.i; }
        if (ra === null) { return 1; }
        if (rb === null) { return -1; }
        return rb - ra;
      }).map(function (x) { return x.it; });
    }
    return list;
  }

  function itemCols() {
    return [
      { id: 'st', label: t('dual.col.status') },
      { id: 'key', label: t('dual.col.key'), header: true },
      { id: 'field', label: t('dual.col.field') },
      { id: 'a', label: 'A' + (raterOf('A') ? ' · ' + raterOf('A') : '') },
      { id: 'b', label: 'B' + (raterOf('B') ? ' · ' + raterOf('B') : '') },
      { id: 'hint', label: t('dual.col.hint') },
      { id: 'impact', label: t('dual.col.impact') },
      { id: 'dec', label: t('dual.col.decision') }
    ];
  }

  function itemCell(it, c) {
    if (c.id === 'st') { return statusEl(it, true); }
    if (c.id === 'key') {
      return it.level === 'row' ? { node: h('span', null, h('span', { 'aria-hidden': 'true' }, ROW_SYM[it.kind] + ' '), it.key), title: it.key } : { text: it.key, title: it.key };
    }
    if (c.id === 'field') { return fieldLabel(it.field); }
    if (c.id === 'a' || c.id === 'b') {
      if (it.level === 'row') {
        var here = (c.id === 'a') === (it.kind === 'only_a');
        return { text: here ? t('dual.row.present') : t('dual.row.absent'), cls: here ? null : 'is-missing-side' };
      }
      var v = it[c.id];
      return v === null || v === undefined || v === '' ? { text: t('dual.empty'), cls: 'is-missing-side' } : { text: v, title: v };
    }
    if (c.id === 'hint') { var hs = pick(it.hint, ''); return { text: hs, title: hs }; }
    if (c.id === 'impact') { return it.impact ? { node: MA.ui.num(it.impact.text), title: pick(it.impact.pooled_text, '') } : ''; }
    if (c.id === 'dec') { return decisionText(it); }
    return '';
  }

  function onItemKey(ev, it) {
    if (ev.ctrlKey || ev.metaKey || ev.altKey || !it || it.auto) { return false; }
    var k = ev.key;
    if (k === 'a' || k === 'A' || k === 'b' || k === 'B') {
      selectItem(it, false);
      setChoice(k.toLowerCase());
      if (S.detail.reason) { S.detail.reason.focus(); }
      ev.preventDefault();
      return true;
    }
    return false;
  }

  function itemsView(host) {
    var list = filteredItems();
    S.items = MA.vtable.create({
      label: t('dual.items.aria'),
      columns: itemCols(),
      rows: list,
      key: function (it) { return itemId(it); },
      cell: itemCell,
      emptyKey: S.filter.open || S.filter.impact ? 'dual.items.noneFiltered' : 'dual.items.none',
      rowClass: function (it) { return ['dx-item', 'is-' + statusOf(it), it.level === 'row' ? 'is-only' : null]; },
      rowLabel: function (it) { return it.key + ' · ' + fieldLabel(it.field) + ': ' + t('dual.st.' + statusOf(it)); },
      onSelect: function (it) { selectItem(it, false); },
      onActivate: function (it) { selectItem(it, true); },
      onKey: onItemKey
    });
    S.items.el.id = 'dx-items';
    MA.dom.mount(host, h('div', { 'class': 'dx-tools', role: 'group', 'aria-label': t('dual.items.tools') },
      check('dx-f-impact', 'impact', t('dual.items.onlyImpact')),
      check('dx-f-open', 'open', t('dual.items.onlyOpen')),
      h('label', { 'for': 'dx-sort' }, t('dual.items.sort')),
      h('select', { id: 'dx-sort', onchange: function (ev) { S.filter.sort = ev.target.value; MA.prefs.set('dual.sort', ev.target.value); refreshItems(true); } },
        h('option', { value: 'engine', selected: S.filter.sort !== 'impact' }, t('dual.items.sortEngine')),
        h('option', { value: 'impact', selected: S.filter.sort === 'impact' }, t('dual.items.sortImpact'))),
      h('span', { 'class': 'muted' }, t('dual.items.keys'))),
    S.items.el,
    h('p', { 'class': 'dx-legend', 'aria-hidden': 'true' }, Object.keys(STATUS).map(function (st) {
      return h('span', null, h('span', { 'class': ['dx-st', STATUS[st].cls] }, STATUS[st].sym), ' ', t('dual.st.' + st));
    }), h('span', null, '⊕ ', t('dual.kind.only_a')), h('span', null, '⊖ ', t('dual.kind.only_b'))));
  }

  function check(id, key, label) {
    return h('label', { 'for': id }, h('input', { type: 'checkbox', id: id, checked: !!S.filter[key], onchange: function (ev) {
      S.filter[key] = ev.target.checked;
      MA.prefs.set('dual.' + key, ev.target.checked ? '1' : null);
      refreshItems(true);
    } }), ' ', label);
  }

  function refreshItems(keep) {
    if (!S.items) { return; }
    S.items.setRows(filteredItems(), keep);
    if (S.sel) {
      var still = (S.view.items || []).filter(function (x) { return itemId(x) === S.sel; })[0];
      if (still) { S.items.select(S.sel, { silent: true }); }
    }
  }

  // ---------------------------------------------------------------- részletek és döntés
  function whySpec(it) {
    var code = it.hint_code || it.kind;
    var w = HINT_FAMILY[code] || HINT_FAMILY[it.kind] || 'value';
    return { kb: it.kb || null, code: it.kb || undefined, title: pick(it.hint, ''),
      plain: { asks: t('dual.why.' + w + '.asks'), because: t('dual.why.' + w + '.because'), change: t('dual.why.' + w + '.change') } };
  }

  function setChoice(v) {
    if (!S.detail || !S.detail.radios) { return; }
    S.detail.radios.forEach(function (r) { r.checked = r.value === v; });
    if (S.detail.other) { S.detail.other.disabled = v !== 'other'; }
  }

  function selectItem(it, focusForm) {
    S.sel = itemId(it);
    S.ctx.setParams(Object.assign({}, S.ctx.params, { outcome: S.outcome.id, item: S.sel }));
    drawDetail(it);
    if (focusForm && S.detail.reason) { S.detail.reason.focus(); }
  }

  function nextOpen(after) {
    var list = filteredItems();
    var start = 0;
    for (var i = 0; i < list.length; i++) { if (itemId(list[i]) === after) { start = i + 1; break; } }
    for (var j = 0; j < list.length; j++) {
      var it = list[(start + j) % list.length];
      var st = statusOf(it);
      if (st === 'open' || st === 'stale') { return it; }
    }
    return null;
  }

  function drawDetail(it) {
    var host = S.els.detail;
    S.detail = { radios: [] };
    // a szakasz aria-labelledby-je (dx-detail-h) kiválasztott eltérés nélkül is feloldható legyen (UX-10)
    if (!it) { MA.dom.mount(host, h('h2', { 'class': 'panel-title', id: 'dx-detail-h' }, t('dual.detail.title')), h('p', { 'class': 'muted' }, t('dual.detail.pick'))); return; }
    var d = it.decision || null;
    var title = h('h2', { 'class': 'panel-title', id: 'dx-detail-h' }, h('span', { 'class': 'dx-key' }, it.key), ' · ', fieldLabel(it.field), ' ', statusEl(it));
    var ab = h('div', { 'class': 'dx-ab' }, SIDES.map(function (side) {
      var low = side.toLowerCase();
      var val;
      if (it.level === 'row') {
        val = (low === 'a') === (it.kind === 'only_a') ? t('dual.row.present') : t('dual.row.absent');
      } else {
        val = it[low] === null || it[low] === undefined || it[low] === '' ? t('dual.empty') : it[low];
      }
      return h('div', { dataset: { side: side } }, h('h3', null, side + (raterOf(side) ? ' · ' + raterOf(side) : '')),
        h('div', { 'class': 'dx-val', id: 'dx-val-' + low }, val));
    }));
    var hint = it.hint ? h('p', { 'class': 'dx-hint', id: 'dx-hint' }, h('strong', null, t('dual.detail.hint') + ' '), pick(it.hint, ''),
      it.kb ? [' ', MA.why.kbButton(it.kb)] : null, ' ', MA.why.button(whySpec(it))) : h('p', { 'class': 'dx-hint muted', id: 'dx-hint' }, t('dual.detail.noHint'), ' ', MA.why.button(whySpec(it)));
    var impact = it.impact ? h('p', { 'class': 'dx-impact', id: 'dx-impact' }, h('strong', null, t('dual.detail.impact') + ' '),
      MA.ui.num(it.impact.text), it.impact.pooled_text ? [' · ', t('dual.detail.pooled'), ' ', MA.ui.num(it.impact.pooled_text)] : null,
      h('span', { 'class': 'muted' }, ' — ' + t('dual.detail.fromEngine'))) : null;
    var kindLine = h('p', { 'class': 'muted' }, t('dual.kind.' + it.kind));
    if (it.auto) {
      MA.dom.mount(host, title, kindLine, ab, hint, h('p', { id: 'dx-auto-note' }, MA.ui.badge('ok', t('dual.st.auto'), { symbol: '=' }), ' ', t('dual.detail.auto')));
      return;
    }
    var name = MA.dom.uid('dxc');
    var choices = it.level === 'row' ? ['a', 'b'] : ['a', 'b', 'other'];
    var other = null;
    var opts = choices.map(function (c) {
      var id = MA.dom.uid('dxr');
      var r = h('input', { type: 'radio', id: id, name: name, value: c, checked: d ? d.chosen === c : false,
        onchange: function () { setChoice(c); if (c === 'other' && other) { other.focus(); } } });
      S.detail.radios.push(r);
      var lab = it.level === 'row' ? t('dual.choose.row.' + c + '.' + it.kind)
        : c === 'other' ? t('dual.choose.other') : t('dual.choose.' + c, { value: it[c] === null || it[c] === undefined || it[c] === '' ? t('dual.empty') : it[c] });
      var node = h('label', { htmlFor: id }, r, ' ', lab);
      if (c === 'other') {
        other = h('input', { type: 'text', id: 'dx-other', 'class': 'dx-other', maxlength: '2000', autocomplete: 'off', spellcheck: 'false',
          value: d && d.chosen === 'other' && d.value !== null ? d.value : '', disabled: !(d && d.chosen === 'other'),
          'aria-label': t('dual.choose.otherValue'), placeholder: t('dual.choose.otherPlaceholder') });
        return h('span', null, node, ' ', other);
      }
      return node;
    });
    S.detail.other = other;
    var reasonId = 'dx-reason';
    var reason = h('textarea', { id: reasonId, 'class': 'dx-reason', maxlength: '2000', rows: '2', 'aria-required': 'true',
      'aria-describedby': 'dx-reason-help', placeholder: t('dual.reason.placeholder') }, d && !it.stale ? d.reason || '' : '');
    reason.value = d && !it.stale ? d.reason || '' : '';
    reason.addEventListener('input', function () { S.formDirty = true; });
    S.detail.reason = reason;
    var msg = h('div', { role: 'alert', 'class': 'dx-msg-host', id: 'dx-msg' });
    S.detail.msg = msg;
    MA.dom.mount(host, title, kindLine, ab, hint, impact,
      it.stale ? h('p', { 'class': 'res-stale', id: 'dx-stale-note' }, MA.ui.badge('stale', t('dual.st.stale'), { symbol: '⟳' }), ' ',
        t('dual.detail.stale', { a: d && d.a !== null && d.a !== undefined ? d.a : '—', b: d && d.b !== null && d.b !== undefined ? d.b : '—' })) : null,
      d && !it.stale ? h('p', { 'class': 'muted', id: 'dx-decided-note' }, t('dual.detail.decidedBy', { who: d.actor || '—', when: MA.i18n.ts(d.ts) })) : null,
      h('fieldset', { 'class': 'dx-choice', role: 'radiogroup', 'aria-labelledby': 'dx-choice-legend' },
        h('legend', { id: 'dx-choice-legend' }, t('dual.choose.legend')), opts),
      h('label', { htmlFor: reasonId }, t('dual.reason.label')), reason,
      h('p', { 'class': 'dx-help', id: 'dx-reason-help' }, t('dual.reason.help')),
      h('div', { 'class': 'dx-actions' },
        h('button', { type: 'button', 'class': 'btn btn-primary', id: 'dx-decide', onclick: function () { decide(it, false); } }, t('dual.decide.save')),
        h('button', { type: 'button', 'class': 'btn', id: 'dx-decide-next', onclick: function () { decide(it, true); } }, t('dual.decide.saveNext')),
        d ? h('button', { type: 'button', 'class': 'btn btn-ghost', id: 'dx-clear', onclick: function () { clearDecision(it); } }, t('dual.decide.clear')) : null),
      msg);
  }

  function chosenValue() {
    var c = null;
    S.detail.radios.forEach(function (r) { if (r.checked) { c = r.value; } });
    return c;
  }

  function reconcile(body, opts) {
    var payload = Object.assign({ outcome: S.outcome.id }, body);
    if (S.key) { payload.key = S.key; }
    var me = actor();
    if (me && /^[A-Za-z][A-Za-z0-9_-]{0,31}$/.test(me)) { payload.actor = me; }
    return MA.api.post('/api/reconcile', payload, Object.assign({ ifMatch: S.etag || undefined, toast: false }, opts || {}));
  }

  function applyView(env) {
    var keepTables = { a: S.view.a, b: S.view.b };
    S.view = env.data;
    if (!S.view.a && keepTables.a) { S.view.a = keepTables.a; S.view.b = keepTables.b; }
    S.etag = env.etag || (S.view.consensus ? S.view.consensus.etag : null) || null;
    S.formDirty = false;
    MA.dom.mount(S.els.gate, gatePanel());
    MA.dom.mount(S.els.write, writePanel());
    refreshItems(true);
    if (S.tables) { S.tables.setRows(tableRows(), true); }
  }

  function handleError(err, focusEl) {
    if (err.code === 'CONFLICT') {
      MA.ui.toast({ kind: 'warning', title: t('dual.conflict.title'), message: err.message });
      compare();
      return;
    }
    if (S.detail && S.detail.msg) { MA.dom.mount(S.detail.msg, MA.ui.errorBox(err)); }
    if (focusEl) { focusEl.focus(); }
  }

  function decide(it, next) {
    MA.dom.clear(S.detail.msg);
    var c = chosenValue();
    if (!c) { MA.dom.mount(S.detail.msg, h('p', { 'class': 'ap-msg' }, t('dual.decide.noChoice'))); S.detail.radios[0].focus(); return null; }
    var reason = S.detail.reason.value.trim();
    if (!reason) { MA.dom.mount(S.detail.msg, h('p', { 'class': 'ap-msg' }, t('dual.decide.noReason'))); S.detail.reason.focus(); return null; }
    var dec = { key: it.key, field: it.field, chosen: c, reason: reason };
    if (c === 'other') {
      var v = S.detail.other ? S.detail.other.value : '';
      if (!v.trim()) { MA.dom.mount(S.detail.msg, h('p', { 'class': 'ap-msg' }, t('dual.decide.noValue'))); S.detail.other.focus(); return null; }
      dec.value = v;
    }
    return reconcile({ decisions: [dec] }).then(function (env) {
      applyView(env);
      MA.ui.toast({ kind: 'success', title: t('dual.decide.saved'), timeout: 2500 });
      var cur = (S.view.items || []).filter(function (x) { return itemId(x) === itemId(it); })[0] || it;
      var target = next ? nextOpen(itemId(it)) : cur;
      if (target) {
        S.items.select(itemId(target), { focus: !next });
        selectItem(target, !!next);
      } else {
        drawDetail(cur);
      }
      return env;
    }, function (err) { handleError(err, S.detail.reason); });
  }

  function clearDecision(it) {
    return reconcile({ clear: [{ key: it.key, field: it.field }] }).then(function (env) {
      applyView(env);
      var cur = (S.view.items || []).filter(function (x) { return itemId(x) === itemId(it); })[0] || it;
      drawDetail(cur);
      MA.ui.toast({ kind: 'info', title: t('dual.decide.cleared'), timeout: 2500 });
    }, function (err) { handleError(err); });
  }

  // ---------------------------------------------------------------- konszenzus-CSV
  function writePanel() {
    var p = S.view.progress || {};
    var blocked = !!(S.view.gate && S.view.gate.blocked);
    var csv = S.view.files && S.view.files.csv ? S.view.files.csv : null;
    var info = S.view.consensus && S.view.consensus.csv ? S.view.consensus.csv : null;
    return h('div', { 'class': 'dx-actions', id: 'dx-write-actions' },
      h('button', { type: 'button', 'class': 'btn btn-primary', id: 'dx-write', 'aria-disabled': blocked ? 'true' : 'false',
        title: blocked ? t('dual.write.blocked', { n: String(p.unresolved) }) : null, onclick: function () { writeCsv('kettos'); } },
      t('dual.write.kettos')),
      h('button', { type: 'button', 'class': 'btn', id: 'dx-write-outcome', 'aria-disabled': blocked ? 'true' : 'false',
        onclick: function () { writeCsv('outcome'); } }, t('dual.write.outcome', { path: S.outcome.data || '—' })),
      csv && csv.exists ? h('button', { type: 'button', 'class': 'btn btn-ghost', id: 'dx-export-consensus', onclick: function () { exportSide('consensus'); } },
        t('dual.write.download'), ' ↓') : null,
      info ? h('span', { 'class': 'muted', id: 'dx-write-info' }, t('dual.write.info', { path: info.path, when: MA.i18n.ts(info.written_at), by: info.by || '—' })) : null,
      blocked ? h('span', { 'class': 'muted', id: 'dx-write-hint' }, t('dual.write.blocked', { n: String(p.unresolved) })) : null);
  }

  function writeCsv(target) {
    var blocked = !!(S.view.gate && S.view.gate.blocked);
    if (blocked) {
      MA.ui.toast({ kind: 'warning', title: t('dual.write.blockedTitle'), message: t('dual.write.blocked', { n: String(S.view.progress.unresolved) }) });
      return null;
    }
    if (target === 'kettos') {
      return reconcile({ write: 'kettos' }).then(function (env) {
        applyView(env);
        var w = env.data.written && env.data.written.csv;
        MA.ui.toast({ kind: 'success', title: t('dual.write.done'), message: w ? w.path : '', details: env.warnings && env.warnings.length ? env.warnings : null });
      }, function (err) { MA.ui.toast({ kind: 'error', title: t('dual.write.failed'), message: err.message, code: err.code }); });
    }
    // a kimenet adattáblájába: előnézet (dry_run), megerősítés, majd írás a tábla mostani ETag-jével
    var dataset = S.outcome.data;
    if (!dataset) { MA.ui.toast({ kind: 'warning', title: t('dual.write.noData') }); return null; }
    return reconcile({ write: 'outcome', dry_run: true }).then(function (env) {
      var w = env.data.written && env.data.written.csv;
      return MA.ui.confirm({ title: t('dual.write.outcomeTitle'), danger: true, okLabel: t('dual.write.outcomeOk'),
        message: t('dual.write.outcomeBody', { path: dataset, rows: w ? String(w.rows) : '—', cells: w ? String(w.reconciled) : '—' }) }).then(function (yes) {
        if (!yes) { return null; }
        return MA.api.get('/api/table', { query: { dataset: dataset }, toast: false }).then(function (tenv) {
          return reconcile({ write: 'outcome', outcome_if_match: tenv.etag || null });
        }, function (err) {
          if (err.code === 'NOT_FOUND') { return reconcile({ write: 'outcome' }); }
          throw err;
        }).then(function (wenv) {
          applyView(wenv);
          MA.ui.toast({ kind: 'success', title: t('dual.write.outcomeDone'), message: dataset,
            actions: [{ label: t('dual.write.openTable'), onClick: function () { MA.app.navigate('extraction', { outcome: S.outcome.id }); } }] });
        });
      });
    }).catch(function (err) { if (err && err.code) { MA.ui.toast({ kind: 'error', title: t('dual.write.failed'), message: err.message, code: err.code }); } });
  }

  // ---------------------------------------------------------------- táblák egymás mellett (cellaszintű diff)
  function tableCols() {
    var out = [{ id: '_key', label: t('dual.col.key'), header: true }];
    (S.view.columns || []).forEach(function (c) {
      var keyCol = (S.view.key || []).indexOf(c.field) >= 0;
      if (keyCol) { return; }
      out.push({ id: c.field + ':A', field: c.field, side: 'A', label: c.field + ' · A', title: c.a || '' });
      out.push({ id: c.field + ':B', field: c.field, side: 'B', label: c.field + ' · B', title: c.b || '', cls: 'dx-side-b' });
    });
    return out;
  }

  function tableRows() {
    var a = S.view.a || { header: [], rows: [] };
    var b = S.view.b || { header: [], rows: [] };
    var cols = S.view.columns || [];
    var ia = {}, ib = {};
    cols.forEach(function (c) { ia[c.field] = a.header.indexOf(c.a); ib[c.field] = b.header.indexOf(c.b); });
    var byUidB = {};
    b.rows.forEach(function (r) { byUidB[r.row_uid] = r; });
    var pairA = {}, usedB = {};
    (S.view.pairs || []).forEach(function (p) { pairA[p.row_uid_a] = p; usedB[p.row_uid_b] = true; });
    var items = {};
    (S.view.items || []).forEach(function (it) { items[itemId(it)] = it; });
    var onlyA = {}, onlyB = {};
    (S.view.items || []).forEach(function (it) {
      if (it.kind === 'only_a' && it.row_uid_a) { onlyA[it.row_uid_a] = it; }
      if (it.kind === 'only_b' && it.row_uid_b) { onlyB[it.row_uid_b] = it; }
    });
    var out = [];
    function cellsOf(row, idx) {
      var o = {};
      Object.keys(idx).forEach(function (f) { o[f] = row && idx[f] >= 0 ? row.cells[idx[f]] : null; });
      return o;
    }
    a.rows.forEach(function (r) {
      var p = pairA[r.row_uid];
      if (p) {
        out.push({ id: 'p:' + r.row_uid, key: p.key, kind: 'pair', A: cellsOf(r, ia), B: cellsOf(byUidB[p.row_uid_b], ib), items: items });
      } else {
        var it = onlyA[r.row_uid];
        out.push({ id: 'a:' + r.row_uid, key: it ? it.key : t('dual.row.unmatched'), kind: 'only_a', item: it || null, A: cellsOf(r, ia), B: null, items: items });
      }
    });
    b.rows.forEach(function (r) {
      if (usedB[r.row_uid]) { return; }
      var it = onlyB[r.row_uid];
      out.push({ id: 'b:' + r.row_uid, key: it ? it.key : t('dual.row.unmatched'), kind: 'only_b', item: it || null, A: null, B: cellsOf(r, ib), items: items });
    });
    return out;
  }

  function tableCell(row, c) {
    if (c.id === '_key') {
      var sym = ROW_SYM[row.kind];
      return { node: h('span', null, sym ? h('span', { 'aria-hidden': 'true' }, sym + ' ') : null, row.key,
        sym ? h('span', { 'class': 'sr-only' }, ' — ' + t('dual.kind.' + row.kind)) : null), title: row.key };
    }
    var side = row[c.side];
    if (!side) { return { text: '—', cls: 'is-missing-side', title: t('dual.kind.' + row.kind) }; }
    var v = side[c.field];
    var text = v === null || v === undefined ? '' : v;
    var it = row.kind === 'pair' ? row.items[row.key + SEP + c.field] : null;
    if (!it) { return { text: text, title: text }; }
    var st = statusOf(it);
    var cls = st === 'open' ? 'is-diff' : st === 'decided' ? 'is-decided' : st === 'stale' ? 'is-stale' : 'is-format';
    return { node: h('span', { 'class': 'dx-cell' }, h('span', null, text),
      h('span', { 'class': 'dx-cell-sym', 'aria-hidden': 'true' }, STATUS[st].sym),
      h('span', { 'class': 'sr-only' }, ' (' + t('dual.st.' + st) + ')')), cls: cls, title: t('dual.st.' + st + '.title') };
  }

  function tablesView(host) {
    S.tables = MA.vtable.create({
      label: t('dual.tables.aria'),
      columns: tableCols(),
      rows: tableRows(),
      key: function (r) { return r.id; },
      cell: tableCell,
      rowClass: function (r) { return r.kind !== 'pair' ? 'is-only' : null; },
      onActivate: function (r, i) {
        var a = S.tables.active();
        var col = a && a.col && a.col.indexOf(':') > 0 ? a.col.split(':')[0] : null;
        var target = null;
        if (r.kind === 'pair' && col) { target = r.items[r.key + SEP + col] || null; }
        if (r.kind !== 'pair') { target = r.item; }
        if (target) { showItem(itemId(target)); }
      }
    });
    S.tables.el.id = 'dx-tables';
    MA.dom.mount(host, h('p', { 'class': 'dx-help' }, t('dual.tables.help')), S.tables.el,
      h('p', { 'class': 'dx-legend' }, Object.keys(STATUS).map(function (st) {
        return h('span', null, h('span', { 'class': ['dx-st', STATUS[st].cls], 'aria-hidden': 'true' }, STATUS[st].sym), ' ', t('dual.st.' + st));
      }), h('span', null, h('span', { 'aria-hidden': 'true' }, '⊕ '), t('dual.kind.only_a')), h('span', null, h('span', { 'aria-hidden': 'true' }, '⊖ '), t('dual.kind.only_b'))));
  }

  function showItem(id) {
    switchView('items');
    var it = (S.view.items || []).filter(function (x) { return itemId(x) === id; })[0];
    if (!it) { return; }
    if (!S.items.select(id, { focus: true })) {
      S.filter.open = false;
      S.filter.impact = false;
      refreshItems(false);
      S.items.select(id, { focus: true });
    }
    selectItem(it, false);
  }

  function switchView(v) {
    if (S.viewMode === v) { return; }
    S.viewMode = v;
    if (S.tabs && S.tabs.select) { S.tabs.select(v); }
    S.ctx.setParams(Object.assign({}, S.ctx.params, { outcome: S.outcome.id, view: v }));
    S.els.itemsPanel.hidden = v !== 'items';
    S.els.tablesPanel.hidden = v !== 'tables';
    if (v === 'tables' && !S.tables) { tablesView(S.els.tablesPanel); }
  }

  // ---------------------------------------------------------------- betöltés és felépítés
  function compare() {
    var body = { outcome: S.outcome.id, tables: true };
    if (S.key) { body.key = S.key; }
    MA.dom.mount(S.els.body, h('div', { 'class': 'panel', 'aria-busy': 'true' }, MA.ui.spinner('dual.loading')));
    return MA.api.post('/api/compare', body, { signal: S.ctx.signal, toast: false }).then(function (env) {
      if (!S.ctx.alive()) { return; }
      S.view = env.data;
      S.etag = env.etag || (env.data.consensus ? env.data.consensus.etag : null) || null;
      S.key = S.view.key && S.view.key.length ? S.view.key : S.key;
      S.warnings = env.warnings || [];
      drawBody();
    }, function (err) {
      if (!S.ctx.alive() || err.code === 'ABORTED') { return; }
      if (err.code === 'CAPABILITY_MISSING') {
        MA.dom.mount(S.els.body, h('section', { 'class': 'panel', id: 'dx-engine-missing' },
          h('h2', { 'class': 'panel-title' }, MA.ui.badge('cap_unusable', t('dual.engine.missingTitle'))),
          h('p', null, err.message), h('p', { 'class': 'dx-help' }, t('dual.engine.missingHelp'))));
        return;
      }
      MA.dom.mount(S.els.body, h('section', { 'class': 'panel', id: 'dx-error' }, MA.ui.errorBox(err)));
    });
  }

  function drawBody() {
    S.items = null;
    S.tables = null;
    S.els.gate = h('div', { id: 'dx-gate-host' }, gatePanel());
    S.els.write = h('div', { id: 'dx-write-host' }, writePanel());
    S.els.itemsPanel = h('div', { id: 'dx-items-panel', role: 'tabpanel', 'aria-labelledby': 'dx-tab-items' });
    S.els.tablesPanel = h('div', { id: 'dx-tables-panel', role: 'tabpanel', 'aria-labelledby': 'dx-tab-tables', hidden: true });
    S.els.detail = h('section', { 'class': 'panel dx-detail', id: 'dx-detail', 'aria-labelledby': 'dx-detail-h', 'aria-live': 'polite' });
    S.tabs = MA.ui.tabs({ label: t('dual.views'), active: 'items',
      tabs: [{ id: 'items', label: t('dual.view.items', { n: String((S.view.items || []).length) }) }, { id: 'tables', label: t('dual.view.tables') }],
      onSelect: function (v) { switchView(v); } });
    MA.dom.$$('[role="tab"]', S.tabs).forEach(function (b) { b.id = 'dx-tab-' + b.dataset.tab; });
    S.viewMode = 'items';
    MA.dom.mount(S.els.body,
      summaryPanel(),
      S.warnings && S.warnings.length ? h('div', { 'class': 'panel', id: 'dx-warnings', role: 'note' }, h('ul', null, S.warnings.map(function (w) {
        return h('li', null, MA.ui.badge('warning', null, { symbol: '⚠' }), ' ', w);
      }))) : null,
      h('section', { 'class': 'panel', id: 'dx-main', 'aria-labelledby': 'dx-main-h' },
        h('h2', { 'class': 'panel-title', id: 'dx-main-h' }, t('dual.main.title')),
        h('p', { 'class': 'dx-help' }, t('dual.main.help')),
        S.els.gate,
        h('div', { 'class': 'toolbar' }, S.tabs),
        S.els.itemsPanel, S.els.tablesPanel),
      S.els.detail,
      h('section', { 'class': 'panel', id: 'dx-write-panel', 'aria-labelledby': 'dx-write-h' },
        h('h2', { 'class': 'panel-title', id: 'dx-write-h' }, t('dual.write.title')),
        h('p', { 'class': 'dx-help' }, t('dual.write.help')), S.els.write));
    itemsView(S.els.itemsPanel);
    var want = S.ctx.params.item;
    var first = (S.view.items || []).filter(function (x) { return itemId(x) === want; })[0] || nextOpen(null);
    if (first) {
      S.items.select(itemId(first), { silent: true });
      drawDetail(first);
      S.sel = itemId(first);
    } else {
      drawDetail(null);
    }
    if (S.ctx.params.view === 'tables') { switchView('tables'); }
  }

  function render(root, ctx) {
    S = { ctx: ctx, els: {}, raterInputs: {}, view: null, etag: null, key: null, sel: null, formDirty: false,
      filter: { impact: MA.prefs.get('dual.impact', '') === '1', open: MA.prefs.get('dual.open', '') === '1', sort: MA.prefs.get('dual.sort', 'engine') } };
    MA.dom.mount(root, h('div', { 'class': 'panel', 'aria-busy': 'true' }, MA.ui.spinner('dual.loading')));
    return MA.api.get('/api/kettos', { signal: ctx.signal, toast: false }).then(function (env) {
      if (!ctx.alive()) { return null; }
      var list = env.data;
      var outs = list.outcomes || [];
      if (!outs.length) {
        MA.dom.mount(root, h('section', { 'class': 'panel', id: 'dx-no-outcomes' }, h('h2', { 'class': 'panel-title' }, t('dual.title')),
          MA.ui.emptyState('dual.noOutcomes'), h('a', { 'class': 'btn btn-sm', href: MA.app.href('project') }, t('dual.toProject'))));
        return null;
      }
      var want = ctx.params.outcome;
      var o = outs.filter(function (x) { return x.id === want; })[0] ||
        outs.filter(function (x) { return x.a.exists && x.b.exists; })[0] || outs[0];
      S.outcome = o;
      S.info = o;
      ctx.setTitle(o.id + ' · ' + pick(o.name, o.id));
      if (want !== o.id) { ctx.setParams(Object.assign({}, ctx.params, { outcome: o.id })); }
      build(root, list);
      if (o.a.exists && o.b.exists) { return compare(); }
      MA.dom.mount(S.els.body, h('section', { 'class': 'panel', id: 'dx-need-files' }, h('h2', { 'class': 'panel-title' }, t('dual.need.title')),
        h('ol', { 'class': 'dx-steps' }, h('li', null, t('dual.need.step1')), h('li', null, t('dual.need.step2')), h('li', null, t('dual.need.step3')))));
      return null;
    }, function (err) {
      if (ctx.alive() && err.code !== 'ABORTED') { MA.dom.mount(root, h('section', { 'class': 'panel' }, MA.ui.errorBox(err))); }
    });
  }

  function build(root, list) {
    var o = S.outcome;
    var sel = h('select', { id: 'dx-outcome', onchange: function () { S.ctx.navigate('dual', { outcome: sel.value }); } },
      list.outcomes.map(function (x) { return h('option', { value: x.id, selected: x.id === o.id }, x.id + ' · ' + pick(x.name, x.id)); }));
    S.els.body = h('div', { id: 'dx-body' });
    MA.dom.mount(root,
      h('section', { 'class': 'panel dx-head', id: 'dx-head', 'aria-labelledby': 'dx-head-h' },
        h('h2', { 'class': 'panel-title', id: 'dx-head-h' }, t('dual.title')),
        h('div', { 'class': 'toolbar' },
          h('label', { htmlFor: 'dx-outcome' }, t('dual.outcome')), sel,
          o.measure ? h('span', { 'class': 'muted' }, t('dual.measure', { measure: o.measure })) : null,
          h('span', { 'class': 'hdr-spacer' }),
          h('a', { 'class': 'btn btn-sm btn-ghost', href: MA.app.href('extraction', { outcome: o.id }) }, t('dual.toExtraction'))),
        h('p', { 'class': 'dx-help', id: 'dx-intro' }, t('dual.intro')),
        h('div', { 'class': 'dx-files' }, fileCard('A', o.a), fileCard('B', o.b)),
        h('p', { 'class': 'dx-help', id: 'dx-rater-help' }, t('dual.file.raterHelp')),
        inboxList(list),
        list.engine && !list.engine.available ? h('p', { id: 'dx-engine-note' }, MA.ui.badge('cap_unusable', t('dual.engine.missingShort')), ' ',
          t('dual.engine.missingHelp')) : null),
      S.els.body);
  }

  MA.app.registerScreen({
    id: 'dual',
    title_key: 'dual.title',
    workspace: 'extraction',
    tab: 'extraction',
    order: 20,
    render: render,
    onLeave: function () {
      if (!S || !S.formDirty) { return true; }
      return MA.ui.confirm({ title: t('dual.leave.title'), message: t('dual.leave.body'), okLabel: t('dual.leave.ok'), danger: true });
    }
  });

  // ================================================================ önteszt (?selftest=1)
  MA.selftest.register('kettős kinyerés: base64 oda-vissza (bájthű fájlcsere)', function (tt) {
    var bytes = new Uint8Array([0xef, 0xbb, 0xbf, 0x61, 0x3b, 0xc3, 0xa9, 0x0d, 0x0a, 0x00, 0xff]);
    var b64 = bytesToB64(bytes.buffer);
    tt.eq(b64, '77u/YTvDqQ0KAP8=', 'bájtok → base64 (BOM, ékezet, CRLF)');
    var bin = window.atob(b64);
    tt.eq(bin.length, bytes.length, 'vissza: azonos hossz');
    tt.eq(bin.charCodeAt(10), 0xff, 'vissza: azonos bájt');
  });
})();
