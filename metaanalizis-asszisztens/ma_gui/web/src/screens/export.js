/* screens/export.js — 8 Export: audit-csomag és kitakaró pillanatkép (terv 3.5.17, 4.17, 7.4, 7.6, 7.7).
 *
 * HTTP API (3.4; az alakok a szerverrel egyeztetendők):
 *   POST /api/export/audit    ← {include{…}, redact{…}} → {path, sha256, bytes, deterministic, manifest{files[], redactions[], excluded[{pattern, reason}], activity_head, data_class}}
 *   POST /api/export/snapshot ← {include{…}, redact{…}, ack: true} → {path, sha256, bytes, redactions[], excluded[], network: false}
 *   (mindkettő: url — aláírt, 10 perces letöltési cím: /f/x/…, csatolmányként)
 *   GET  /api/log/activity?limit=1 → a hash-lánc állapota (verify, total)
 * A kapcsolók alapértéke az adatosztályból jön (7.4): B-nél az adattáblák alapból ki, C-nél tiltva; a _privat/ (C)
 * soha, a PDF-ek (D) csak doc-id + oldal + sha256. A felületet és a pillanatképet SOHA nem szabad feltölteni vagy
 * publikálni — Claude Artifactként sem (7.6): a képernyő ezt kifejezetten kimondja, és a pillanatkép csak tudomásulvétel után készül.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };

  var INCLUDE = ['decisions', 'specs_runs', 'provenance', 'appraisals', 'prisma', 'figures', 'activity', 'rerun', 'data_tables'];
  var REDACT = ['assessors', 'quotes', 'abs_paths'];
  // adatosztály → alapértékek (7.4); a C-nél az adattábla tiltott (nem kapcsolható be)
  var DEFAULTS = {
    A: { data_tables: true, assessors: false, quotes: true, abs_paths: true },
    B: { data_tables: false, assessors: true, quotes: true, abs_paths: true },
    C: { data_tables: false, assessors: true, quotes: true, abs_paths: true }
  };
  var E = null;

  function cls() {
    var p = MA.store.get('privacy') || {};
    var pr = MA.store.get('project') || {};
    var c = p.data_class || pr.data_class || 'B';
    return DEFAULTS[c] ? c : 'B';
  }

  function box(group, key, checked, disabled, labelKey, args) {
    var id = 'ex-' + group + '-' + key;
    var el = h('input', { type: 'checkbox', id: id, checked: checked, disabled: disabled, dataset: { key: key } });
    E.boxes[group][key] = el;
    return h('label', { 'class': ['exp-opt', disabled && 'is-disabled'], htmlFor: id }, el, ' ', t(labelKey || ('export.' + group + '.' + key), args || null));
  }

  function values(group) {
    var out = {};
    Object.keys(E.boxes[group]).forEach(function (k) { var el = E.boxes[group][k]; if (!el.disabled) { out[k] = el.checked; } });
    return out;
  }

  function result(d, kind) {
    var m = d.manifest || {};
    var red = d.redactions || m.redactions || [];
    var exc = d.excluded || m.excluded || [];
    var nfiles = typeof m.files_count === 'number' ? m.files_count : (Array.isArray(m.files) ? m.files.length : null);
    return h('div', { 'class': 'exp-result', role: 'status', id: 'exp-' + kind + '-result' },
      MA.ui.badge('ok', t('export.done.' + kind)),
      h('dl', { 'class': 'cap-privacy' },
        h('dt', { i18n: 'export.r.path' }), h('dd', null, h('code', null, d.path || '—')),
        h('dt', null, 'sha256'), h('dd', null, h('code', { 'class': 'exp-sha' }, d.sha256 || '—'), ' ', d.sha256 ? MA.ui.copyButton(d.sha256) : null),
        h('dt', { i18n: 'export.r.bytes' }), h('dd', null, d.bytes === undefined ? '—' : String(d.bytes)),
        nfiles !== null ? [h('dt', { i18n: 'export.r.files' }), h('dd', null, String(nfiles))] : null,
        m.activity_head ? [h('dt', { i18n: 'export.r.head' }), h('dd', null, h('code', null, String(m.activity_head)))] : null,
        red.length ? [h('dt', { i18n: 'export.r.redactions' }), h('dd', null, red.map(function (r) { return pick(r); }).join(' · '))] : null,
        exc.length ? [h('dt', { i18n: 'export.r.excluded' }), h('dd', null, h('ul', { 'class': 'cap-notes' }, exc.map(function (x) {
          return h('li', null, h('code', null, String(x.pattern || x.path || '')), x.reason ? ' — ' + pick(x.reason) : '');
        })))] : null),
      // aláírt, rövid életű letöltési cím (/f/x/…); a szerver csatolmányként adja (nem nyílik meg a munkapad originjén)
      typeof d.url === 'string' && /^\/f\//.test(d.url) ? h('p', { 'class': 'exp-download' },
        h('a', { 'class': 'btn', id: 'exp-' + kind + '-download', href: d.url, download: d.name || '' }, t('export.download')),
        ' ', h('span', { 'class': 'muted', i18n: 'export.downloadNote' })) : null,
      kind === 'snapshot' ? h('p', { 'class': 'exp-warn', role: 'note' }, MA.ui.badge('warning', null), ' ', t('export.snap.warn')) : null);
  }

  function run(kind, btn, host, ctx) {
    btn.disabled = true;
    MA.dom.mount(host, MA.ui.spinner('export.working'));
    var body = { include: values('include'), redact: values('redact') };
    // a pillanatkép csak tudomásulvétellel készül (a szerver is megköveteli: ack)
    if (kind === 'snapshot') { body.ack = !!E.ack.checked; }
    return MA.api.post('/api/export/' + kind, body, { signal: ctx.signal, timeoutMs: 120000 }).then(function (env) {
      if (!ctx.alive()) { return; }
      MA.dom.mount(host, result(env.data || {}, kind));
      btn.disabled = kind === 'snapshot' && !E.ack.checked;
    }, function (err) {
      if (!ctx.alive()) { return; }
      MA.dom.mount(host, MA.ui.errorBox(err));
      btn.disabled = kind === 'snapshot' && !E.ack.checked;
    });
  }

  function render(root, ctx) {
    var c = cls();
    var d = DEFAULTS[c];
    E = { boxes: { include: {}, redact: {} } };
    var chain = h('span', { 'class': 'muted', id: 'exp-chain' }, '…');
    var auditHost = h('div', { 'aria-live': 'polite' });
    var snapHost = h('div', { 'aria-live': 'polite' });
    var auditBtn = h('button', { type: 'button', 'class': 'btn btn-primary', id: 'exp-audit', onclick: function () { run('audit', auditBtn, auditHost, ctx); } }, t('export.audit.button'));
    var snapBtn = h('button', { type: 'button', 'class': 'btn btn-primary', id: 'exp-snapshot', disabled: true, 'aria-describedby': 'exp-snap-warn',
      onclick: function () { run('snapshot', snapBtn, snapHost, ctx); } }, t('export.snap.button'));
    E.ack = h('input', { type: 'checkbox', id: 'exp-ack', 'aria-describedby': 'exp-snap-warn', onchange: function () { snapBtn.disabled = !E.ack.checked; } });
    MA.dom.mount(root,
      h('section', { 'class': 'panel', 'aria-labelledby': 'exp-h' },
        h('h2', { 'class': 'panel-title', id: 'exp-h', i18n: 'export.title' }),
        h('p', null, MA.ui.badge(c === 'A' ? 'info' : 'warning', t('export.class', { cls: c })), ' ', t('export.classNote.' + c)),
        h('fieldset', { 'class': 'exp-group' }, h('legend', { i18n: 'export.include' }),
          INCLUDE.map(function (k) {
            if (k === 'activity') { return h('span', { 'class': 'exp-opt-wrap' }, box('include', k, true, false), ' ', chain); }
            if (k === 'data_tables') { return box('include', k, d.data_tables, c === 'C', c === 'C' ? 'export.include.data_tables_c' : null, { cls: c }); }
            return box('include', k, true, false);
          }),
          h('label', { 'class': 'exp-opt is-disabled' }, h('input', { type: 'checkbox', disabled: true, id: 'ex-include-private' }), ' ', t('export.include.private')),
          h('label', { 'class': 'exp-opt is-disabled' }, h('input', { type: 'checkbox', disabled: true, id: 'ex-include-pdfs' }), ' ', t('export.include.pdfs'))),
        h('fieldset', { 'class': 'exp-group' }, h('legend', { i18n: 'export.redact' }),
          REDACT.map(function (k) { return box('redact', k, d[k], false); })),
        h('div', { 'class': 'toolbar' }, auditBtn, h('span', { 'class': 'muted', i18n: 'export.audit.note' })),
        auditHost),
      h('section', { 'class': 'panel exp-snap', 'aria-labelledby': 'exp-s-h' },
        h('h2', { 'class': 'panel-title', id: 'exp-s-h', i18n: 'export.snap.title' }),
        h('p', null, t('export.snap.body')),
        h('div', { 'class': 'exp-warn', role: 'note', id: 'exp-snap-warn' }, MA.ui.badge('warning', t('export.snap.warnTitle')), ' ', t('export.snap.warn')),
        h('label', { 'class': 'exp-opt', htmlFor: 'exp-ack' }, E.ack, ' ', t('export.snap.ack')),
        h('div', { 'class': 'toolbar' }, snapBtn),
        snapHost));
    return MA.api.get('/api/log/activity', { query: { limit: 1 }, signal: ctx.signal, toast: false }).then(function (env) {
      if (!ctx.alive()) { return; }
      var a = env.data || {};
      var v = a.verify || {};
      MA.dom.mount(chain, v.ok === false ? MA.ui.badge('error', t('log.chain.broken', { seq: String(v.first_bad_seq) })) : t('export.chainOk', { n: typeof a.total === 'number' ? String(a.total) : '—' }));
    }, function () { if (ctx.alive()) { chain.textContent = ''; } });
  }

  MA.app.registerScreen({ id: 'export', title_key: 'tab.export', workspace: 'process', tab: 'export', order: 10, render: render });
})();
