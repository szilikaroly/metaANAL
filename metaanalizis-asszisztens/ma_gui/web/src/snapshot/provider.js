/* snapshot/provider.js — a csak olvasható pillanatkép kliense (terv 2.5, 3.5.17, 7.4, 7.6). CSAK a pillanatkép-buildben.
 *
 * A ma_gui/snapshot.py a szerver GET-válaszait (borítékait) a lapba ágyazza (<script type="application/json"
 * id="ma-snapshot">); ez a modul a hálózati réteg helyett ezekből válaszol, így minden képernyő változatlanul fut:
 *   - GET: pontos kulcs ('GET /api/runs?outcome=o1'), különben a lekérdezés nélküli kulcs + egyszerű szűrés
 *     (status/severity/stage/agent, limit) a data.items-en; a KB-kereső csak a beágyazott tételekben keres;
 *   - író kérés (POST/PUT): semmi sem íródik — párbeszédablak a PONTOS paranccsal (Windows és macOS/Linux
 *     alakban, másolható; a platformé rögtön a vágólapra kerül), a kérés READ_ONLY hibával zárul;
 *   - olvasó POST (validálás, explore, átváltó, fájl-URL): a beágyazott eredmény, vagy csendes READ_ONLY.
 * Hálózatot nem használ (a lap CSP-je: connect-src 'none'); munkamenet, token és változásfigyelés nincs.
 * Számot nem formáz: minden számszöveg a motoré; itt csak darabszám és azonosító jelenik meg.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var node = document.getElementById('ma-snapshot');
  if (!node || typeof MA.__snapSetTransport !== 'function') { return; }
  var S = null;
  try { S = JSON.parse(node.textContent || 'null'); } catch (e) { S = null; }
  if (!S || S.schema !== 'szk.ma.snapshot/v1' || !S.routes || typeof S.routes !== 'object') { return; }

  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var ROUTES = S.routes;
  var PROJECT = S.project || {};
  var MANIFEST = S.manifest || {};
  var READ_HTTP = 405;
  var SKIP_QUERY = { limit: 1, since: 1, refresh: 1, include: 1, client_seq: 1, data_class: 1 };
  var ENC = { '!': '%21', '\'': '%27', '(': '%28', ')': '%29', '*': '%2A' };

  // ---------------------------------------------------------------- kulcsok (a snapshot.py query_string-jével azonos)
  function enc(s) { return encodeURIComponent(String(s)).replace(/[!'()*]/g, function (c) { return ENC[c]; }); }

  function queryString(q) {
    if (!q) { return ''; }
    return Object.keys(q).sort().filter(function (k) { return q[k] !== undefined && q[k] !== null; }).map(function (k) {
      var v = Array.isArray(q[k]) ? q[k].join(',') : q[k];
      return enc(k) + '=' + enc(v);
    }).join('&');
  }

  function key(method, path, q) {
    var qs = queryString(q);
    return method + ' ' + path + (qs ? '?' + qs : '');
  }

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  function envelope(data, schema, warnings) {
    return { ok: true, schema: schema || null, data: data, warnings: warnings || [], meta: { engine: S.manifest && S.manifest.versions ? S.manifest.versions.engine : null, project_rev: 0, elapsed_ms: 0, request_id: 'snapshot' } };
  }

  function errorEnv(code, message, http, details) {
    return { ok: false, error: { code: code, http: http || 404, message: message || '', details: details || null } };
  }

  // ---------------------------------------------------------------- GET
  function filterItems(env, q) {
    var d = env.data;
    if (!d || !Array.isArray(d.items) || !q) { return env; }
    var items = d.items;
    Object.keys(q).forEach(function (k) {
      var v = q[k];
      if (SKIP_QUERY[k] || v === undefined || v === null || v === '') { return; }
      var fields = [k, k + '_id'];
      var has = items.some(function (it) { return it && fields.some(function (f) { return Object.prototype.hasOwnProperty.call(it, f); }); });
      if (!has) { return; }
      var wanted = String(v).split(',');
      items = items.filter(function (it) {
        return fields.some(function (f) {
          var x = it ? it[f] : undefined;
          if (x === undefined || x === null) { return false; }
          if (k === 'status' && wanted.indexOf('resolved') >= 0 && x !== 'open') { return true; }
          return wanted.indexOf(String(x)) >= 0;
        });
      });
    });
    var lim = q.limit !== undefined ? parseInt(String(q.limit), 10) : 0;
    if (lim > 0 && items.length > lim) { items = items.slice(items.length - lim); }
    d.items = items;
    return env;
  }

  function kbSearch(q) {
    var term = String(q.q || '').toLowerCase();
    var scope = String(q.scope || 'rule').split(',');
    var limit = parseInt(String(q.limit || '12'), 10) || 12;
    var res = { rule: [], knowledge: [], chunk: [] };
    Object.keys(ROUTES).sort().forEach(function (k) {
      if (k.indexOf('GET /api/kb/item/') !== 0) { return; }
      var d = ROUTES[k].data || {};
      var it = d.item || {};
      var sc = d.table === 'decision_rule' || it.rule_id ? 'rule' : 'knowledge';
      if (scope.indexOf(sc) < 0 || res[sc].length >= limit) { return; }
      var hay = JSON.stringify(it).toLowerCase() + ' ' + String(d.id || '').toLowerCase();
      if (term && hay.indexOf(term) >= 0) {
        res[sc].push(Object.assign({ id: d.id, snippet: it.recommendation || it.summary || it.title || '' }, it));
      }
    });
    return envelope({ query: q.q || '', scopes: scope, results: res, local_only: true, snapshot: true,
      note: { hu: t('snapshot.kbSearch'), en: t('snapshot.kbSearch') } }, 'szk.ma.kb-search/v1', [t('snapshot.kbSearch')]);
  }

  function answerGet(path, q) {
    var k = key('GET', path, q);
    if (ROUTES[k]) { return clone(ROUTES[k]); }
    var base = ROUTES['GET ' + path];
    if (base) { return base.ok ? filterItems(clone(base), q) : clone(base); }
    if (path === '/api/kb/search') { return kbSearch(q || {}); }
    if (path === '/api/changes') { return envelope({ rev: 0, changed: [], external: [], reset: false }, 'szk.ma.changes/v1'); }
    return errorEnv('NOT_FOUND', t('snapshot.missing'), 404);
  }

  // ---------------------------------------------------------------- író / olvasó POST, parancsok
  function matchWhen(when, body) {
    if (!when) { return true; }
    return Object.keys(when).every(function (k) { return body && body[k] === when[k]; });
  }

  function classify(method, path, body) {
    var list = Array.isArray(S.commands) ? S.commands : [];
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if ((c.method === '*' || c.method === method) && (c.path === '*' || c.path === path) && matchWhen(c.when, body)) { return c; }
    }
    return { kind: 'write', argv: ['ma.py', 'gui', '--project', '{project}'] };
  }

  function lookup(obj, name) {
    var cur = obj;
    name.split('.').forEach(function (p) { cur = cur && typeof cur === 'object' ? cur[p] : undefined; });
    return cur;
  }

  function textOf(v) {
    if (v === undefined || v === null) { return ''; }
    if (Array.isArray(v)) { return v.filter(function (x) { return x !== null && x !== undefined && x !== ''; }).map(String).join(','); }
    if (typeof v === 'object') { return ''; }
    return String(v);
  }

  var PH = /^\{([A-Za-z0-9_.]+)(?:\|([^}]*))?\}$/;
  function sub(part, ctx) {
    var m = PH.exec(part);
    if (!m) { return part; }
    var v = textOf(lookup(ctx, m[1]));
    if (v !== '') { return v; }
    return m[2] !== undefined ? m[2] : null;
  }

  /** fill(argv-sablon, törzs) → argv (a hiányzó kötelező mező helyén <mező>; az opcionális csoport elmarad). */
  function fill(tpl, body) {
    var ctx = Object.assign({}, body || {}, { project: PROJECT.dir_hint || '<projektmappa>' });
    var out = [];
    (tpl || []).forEach(function (part) {
      if (Array.isArray(part)) {
        var vals = part.map(function (p) { return sub(p, ctx); });
        if (vals.every(function (x) { return x !== null && x !== ''; })) { out = out.concat(vals); }
        return;
      }
      var x = sub(part, ctx);
      out.push(x === null || x === '' ? '<' + (PH.exec(part) || [part, part])[1] + '>' : x);
    });
    return out;
  }

  /** A két parancssor (MA.shell: a szöveg — vizsgálatcímke, indoklás — nem változtathatja meg a parancs
   * szerkezetét; WS-2). windows: null, ha egy argumentum Windowson nem idézhető biztonságosan (winUnsafe: a
   * kapcsolók neve). */
  function commandLines(argv) {
    var rest = argv[0] === 'ma.py' ? argv.slice(1) : argv;
    var w = MA.shell.windows(['ma.py'].concat(rest), ['py', '-3']);
    return { windows: w.line, winUnsafe: w.unsafe, posix: MA.shell.posix(['ma.py'].concat(rest), ['python3']) };
  }

  var openDialog = null;
  function showCommand(c, body) {
    var argv = fill(c.argv, body);
    var lines = commandLines(argv);
    var preferred = MA.shell.isWindows() ? lines.windows : lines.posix;
    var status = h('p', { 'class': 'muted snap-copy-status', role: 'status', id: 'snapshot-cmd-status' });
    // biztonságosan nem idézhető Windows-sort nem másolunk a vágólapra
    if (preferred && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(preferred).then(function () { status.textContent = t('snapshot.cmd.copied'); }, function () { /* nincs jog: a gombbal másolható */ });
    }
    if (openDialog) { openDialog.close(); }
    function block(labelKey, text, id) {
      if (text === null) {
        return h('div', { 'class': 'snap-cmd' },
          h('div', { 'class': 'snap-cmd-head' }, h('strong', { i18n: labelKey })),
          h('p', { 'class': 'snap-cmd-unsafe', id: id, role: 'note' },
            t('shell.unsafeWin', { opt: lines.winUnsafe.join(', ') })));
      }
      return h('div', { 'class': 'snap-cmd' },
        h('div', { 'class': 'snap-cmd-head' }, h('strong', { i18n: labelKey }), ' ', MA.ui.copyButton(text)),
        h('pre', { 'class': 'cmd', id: id, tabindex: '0' }, text));
    }
    openDialog = MA.ui.modal({
      title: t('snapshot.cmd.title'),
      size: 'lg',
      body: [
        h('p', { i18n: 'snapshot.cmd.body' }),
        c.note ? h('p', { 'class': 'snap-note' }, MA.i18n.pick(c.note, '')) : null,
        block('snapshot.cmd.windows', lines.windows, 'snapshot-cmd-windows'),
        block('snapshot.cmd.posix', lines.posix, 'snapshot-cmd-posix'),
        h('p', { 'class': 'muted', i18n: 'snapshot.cmd.hint' }),
        status
      ],
      actions: [{ label: t('common.close'), kind: 'primary' }],
      onClose: function () { openDialog = null; }
    });
    return argv;
  }

  function readOnlyError(c) {
    var msg = c && c.note ? MA.i18n.pick(c.note, '') : t('snapshot.readOnly');
    return errorEnv('READ_ONLY', msg, READ_HTTP);
  }

  function answerWrite(method, path, body) {
    if (method === 'POST' && path === '/api/validate' && body && body.dataset) {
      var v = ROUTES[key('POST', path, { dataset: body.dataset })];
      if (v) { return clone(v); }
    }
    var c = classify(method, path, body || {});
    if (c.kind === 'write') {
      var argv = showCommand(c, body || {});
      var err = readOnlyError(c);
      err.error.details = { command: argv };
      return err;
    }
    return readOnlyError(c);
  }

  // ---------------------------------------------------------------- transport (api.js)
  function transport(req) {
    var body = null;
    if (req.body) { try { body = JSON.parse(req.body); } catch (e) { body = null; } }
    var env = req.method === 'GET' ? answerGet(req.path, req.query || null) : answerWrite(req.method, req.path, body);
    var status = env.ok ? 200 : (env.error && env.error.http) || 404;
    return Promise.resolve({ status: status, etag: null, contentType: 'application/json', text: JSON.stringify(env) });
  }

  MA.__snapSetTransport(transport);

  // munkamenet és változásfigyelés nincs: a lap azonnal „bejelentkezett”, long-poll nélkül
  MA.api.session.init = function () {
    MA.store.set('session', { state: 'ok', snapshot: true });
    MA.bus.emit('session', 'ok');
    return Promise.resolve('ok');
  };
  MA.api.changes.start = function () {};
  MA.api.openFile = function (spec) {
    var page = spec && spec.page ? String(spec.page) : '—';
    var doc = spec && spec.doc ? String(spec.doc) : (spec && spec.path ? String(spec.path) : '—');
    MA.ui.toast({ kind: 'info', title: t('err.READ_ONLY'), message: t('snapshot.pdf', { doc: doc, page: page }) });
    return Promise.reject(MA.api.ApiError('READ_ONLY', t('snapshot.pdf', { doc: doc, page: page }), READ_HTTP));
  };

  // ---------------------------------------------------------------- sáv és részletek
  function infoList(items, fmt) {
    if (!items || !items.length) { return h('p', { 'class': 'muted', i18n: 'snapshot.info.none' }); }
    return h('ul', { 'class': 'snap-list' }, items.map(function (x) { return h('li', null, fmt(x)); }));
  }

  function showInfo() {
    var val = MANIFEST.validation || {};
    MA.ui.modal({
      title: t('snapshot.info.title'),
      size: 'lg',
      body: [
        h('p', { i18n: 'snapshot.info.body' }),
        h('dl', { 'class': 'snap-dl' },
          h('dt', { i18n: 'snapshot.info.state' }), h('dd', null, h('code', null, String(S.state_time || '—'))),
          h('dt', { i18n: 'snapshot.info.id' }), h('dd', null, h('code', { id: 'snapshot-state-id' }, String(S.state_id || '—'))),
          h('dt', { i18n: 'snapshot.info.class' }), h('dd', null, String(PROJECT.data_class || '—'))),
        h('h3', { i18n: 'snapshot.info.redactions' }),
        infoList(MANIFEST.redactions, function (r) { return MA.i18n.pick(r, ''); }),
        h('h3', { i18n: 'snapshot.info.excluded' }),
        infoList(MANIFEST.excluded, function (x) { return [h('code', null, String(x.pattern || '')), ' — ', MA.i18n.pick(x.reason, '')]; }),
        h('h3', { i18n: 'snapshot.info.runs' }),
        infoList(MANIFEST.runs, function (r) { return [h('code', null, String(r.run_id || '')), ' · ', String(r.outcome_id || '')]; }),
        h('h3', { i18n: 'snapshot.info.validation' }),
        infoList(Object.keys(val).sort(), function (ds) {
          var s = (val[ds] && val[ds].summary) || {};
          return [h('code', null, ds), ' — ', t('snapshot.info.counts', { e: String(s.error === undefined ? '—' : s.error), w: String(s.warning === undefined ? '—' : s.warning), i: String(s.info === undefined ? '—' : s.info) })];
        })
      ],
      actions: [{ label: t('common.close'), kind: 'primary' }]
    });
  }

  function banner() {
    var id = String(S.state_id || '').slice(0, 12);
    return h('div', { 'class': 'snap-banner', role: 'note', id: 'snapshot-banner' },
      MA.ui.badge('warning', t('snapshot.banner.title')),
      ' ',
      h('span', { 'class': 'snap-state', i18n: 'snapshot.banner.state', i18nArgs: { ts: MA.i18n.ts(S.state_time || ''), id: id, cls: String(PROJECT.data_class || '?') } }),
      ' ',
      h('button', { type: 'button', 'class': 'btn btn-sm btn-ghost', id: 'snapshot-info', i18n: 'snapshot.banner.details', onclick: showInfo }),
      ' ',
      h('strong', { 'class': 'snap-warn', i18n: 'snapshot.banner.warn' }));
  }

  function mountBanner() {
    if (document.getElementById('snapshot-banner')) { return; }
    var header = document.querySelector('.app-header');
    if (header && header.parentNode) { header.parentNode.insertBefore(banner(), header.nextSibling); }
  }

  document.documentElement.setAttribute('data-mode', 'snapshot');
  MA.snapshot = {
    data: S,
    project: PROJECT,
    manifest: MANIFEST,
    command: function (method, path, body) { return fill(classify(method, path, body || {}).argv, body || {}); },
    lines: commandLines,
    showInfo: showInfo
  };
  MA.store.subscribe('ui.ready', function (v) { if (v) { mountBanner(); } });
  // nyelvváltáskor a sáv újraépül (a jelvény szövege nem i18n-attribútumos)
  MA.i18n.onChange(function () {
    var old = document.getElementById('snapshot-banner');
    if (old && old.parentNode) { old.parentNode.replaceChild(banner(), old); }
  });
})();
