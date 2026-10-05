/* screens/analysis_shared.js — az elemzés-képernyők (plan.js, results.js) közös segédei: MA.analysis.
 *
 *   outcomes(), pickOutcome(id)            — a projekt kimenetei (szk.ma.project/v1)
 *   engineOptions(), measureLabel(id)      — a motor opció-metaadata (GET /api/engine; 3.5.6: nincs JS-opciólista)
 *   cmd(argv)                              — a futás-leíró equivalent_argv-jából a pontos parancs (idézőjelezve)
 *   analyze(body, {clientSeq, signal})     — POST /api/analyze → GET /api/jobs/<id> lekérdezés a végállapotig
 *   loadRuns(outcome, signal)              — GET /api/runs?outcome= (+ a munkamenetben rögzítettek), időrendben
 *   addRun(run), loadPlot(runId, signal)   — commit-futás felvétele; szk.ma.plot/v2 (gyorsítótárazva)
 *   runBadge(run), runLabel(run), shortSha — AKTUÁLIS / ELAVULT (X001) / explore
 *   kbRules(field, signal), kbButton(id), openKb(id) — KB-jelvények (ⓚ) és a KB-tétel modális ablaka
 *   drilldown(host, opts)                  — lefúrás: kinyerési sor + eredet + PDF-oldal + LOO/befolyás
 *   download(path)                         — aláírt URL-en (POST /api/fileurl) letöltés
 *   linkSignals(a, b)                      — két AbortSignal egyesítése
 * Állapot csak memóriában (MA.store 'analysis.*'); böngészőtárolóba csak UI-preferencia kerül (MA.prefs).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '—' : fb); };

  var TERMINAL = { done: 1, error: 1, superseded: 1, timeout: 1 };
  var POLL_MS = [60, 120, 200, 300, 500, 800, 1000];
  var JOB_TIMEOUT_MS = 120000;

  // ---------------------------------------------------------------- projekt, motor
  function outcomes() {
    var p = MA.store.get('project') || {};
    return Array.isArray(p.outcomes) ? p.outcomes : [];
  }

  function pickOutcome(id) {
    var list = outcomes();
    for (var i = 0; i < list.length; i++) { if (list[i].id === id) { return list[i]; } }
    var withSpec = list.filter(function (o) { return !!o.primary_spec; })[0];
    return withSpec || list[0] || null;
  }

  function engineOptions() {
    var e = MA.store.get('engine') || {};
    return e.options && typeof e.options === 'object' ? e.options : {};
  }

  function measureLabel(id) {
    var e = MA.store.get('engine') || {};
    var m = (e.measures || []).filter(function (x) { return x.id === id; })[0];
    return m && m.label ? pick(m.label, id) : id;
  }

  // ---------------------------------------------------------------- parancs
  var SAFE_ARG = /^[\w./:=,+@%\u00C0-\u024F-]+$/;
  function quoteArg(a) {
    var s = String(a);
    return SAFE_ARG.test(s) ? s : '"' + s.replace(/(["\\$\x60])/g, '\\$1') + '"';
  }
  function cmd(argv) {
    if (!Array.isArray(argv) || !argv.length) { return ''; }
    return 'python ' + argv.map(quoteArg).join(' ');
  }

  // ---------------------------------------------------------------- jelek
  function linkSignals(a, b) {
    var c = new AbortController();
    [a, b].forEach(function (s) {
      if (!s) { return; }
      if (s.aborted) { c.abort(); } else { s.addEventListener('abort', function () { c.abort(); }); }
    });
    return c.signal;
  }

  function wait(ms, signal) {
    return new Promise(function (resolve, reject) {
      var tm = setTimeout(resolve, ms);
      if (signal) {
        signal.addEventListener('abort', function () {
          clearTimeout(tm);
          reject(MA.api.ApiError('ABORTED', 'aborted', 0));
        });
      }
    });
  }

  /**
   * analyze(body, opts) → Promise<job-snapshot> — POST /api/analyze; ha a feladat még nem kész,
   * GET /api/jobs/<id> visszalépő lekérdezéssel a végállapotig (done | error | superseded | timeout).
   * 'error' / 'timeout' → ApiError (a feladat hibakódjával); 'superseded' → a snapshot (a hívó eldobja).
   */
  function analyze(body, opts) {
    opts = opts || {};
    var started = Date.now();
    var reqOpts = { signal: opts.signal, quiet: opts.quiet || [] };
    if (typeof opts.clientSeq === 'number') { reqOpts.clientSeq = opts.clientSeq; } else { reqOpts.seq = true; }
    return MA.api.post('/api/analyze', body, reqOpts).then(function (env) {
      return follow(env.data || {}, 0);
    });
    function follow(job, n) {
      if (job.status && TERMINAL[job.status]) { return finish(job); }
      if (!job.job_id) { return finish(job); }
      if (Date.now() - started > JOB_TIMEOUT_MS) {
        throw MA.api.ApiError('TIMEOUT', t('analysis.job.timeout'), 0);
      }
      return wait(POLL_MS[n < POLL_MS.length ? n : POLL_MS.length - 1], opts.signal).then(function () {
        return MA.api.get('/api/jobs/' + encodeURIComponent(job.job_id), { signal: opts.signal, toast: opts.toast });
      }).then(function (env) {
        if (opts.onProgress) { opts.onProgress(env.data || {}); }
        return follow(env.data || {}, n + 1);
      });
    }
    function finish(job) {
      if (job.status === 'error' || job.status === 'timeout') {
        var e = job.error || {};
        var err = MA.api.ApiError(job.status === 'timeout' ? 'TIMEOUT' : (e.code || 'INTERNAL'), e.message || '', 0, e.details || null);
        if (opts.toast !== false) { MA.ui.toast({ kind: 'error', title: t('analysis.job.failed'), message: e.message || job.status, code: err.code }); }
        throw err;
      }
      return job;
    }
  }

  // ---------------------------------------------------------------- futások
  function runTime(r) { return (r && (r.started || r.finished)) || ''; }

  function addRun(run) {
    if (!run || !run.run_id) { return; }
    var key = 'analysis.extraRuns';
    var list = (MA.store.get(key) || []).filter(function (r) { return r.run_id !== run.run_id; });
    list.push(run);
    MA.store.set(key, list);
  }

  /** loadRuns(outcome, signal) → Promise<[run]> — a kimenet commit-futásai, legújabb elöl. */
  function loadRuns(outcomeId, signal) {
    return MA.api.get('/api/runs', { query: { outcome: outcomeId }, signal: signal, toast: false, quiet: ['NOT_FOUND'] }).then(function (env) {
      return MA.api.list(env, 'runs');
    }, function (err) {
      if (err.code === 'ABORTED') { throw err; }
      if (err.code === 'NOT_FOUND') { return []; }
      throw err;
    }).then(function (list) {
      var seen = {};
      var all = [];
      list.concat(MA.store.get('analysis.extraRuns') || []).forEach(function (r) {
        if (!r || !r.run_id || seen[r.run_id]) { return; }
        if (r.outcome_id && r.outcome_id !== outcomeId) { return; }
        seen[r.run_id] = true;
        all.push(r);
      });
      all.sort(function (a, b) { return runTime(a) < runTime(b) ? 1 : (runTime(a) > runTime(b) ? -1 : 0); });
      return all;
    });
  }

  var plotCache = {};
  function loadPlot(runId, signal) {
    if (plotCache[runId]) { return Promise.resolve(plotCache[runId]); }
    return MA.api.get('/api/runs/' + encodeURIComponent(runId) + '/plot', { signal: signal }).then(function (env) {
      var p = env.data;
      if (!p || !p.axis || !Array.isArray(p.studies)) { throw MA.api.ApiError('BAD_RESPONSE', t('analysis.plot.bad'), 200); }
      plotCache[runId] = p;
      return p;
    });
  }

  function shortSha(s) { return typeof s === 'string' && s.length > 4 ? s.slice(0, 4) + '…' : (s || '—'); }

  function runBadge(run) {
    if (!run) { return null; }
    if (!run.run_id) { return MA.ui.badge('info', t('analysis.run.explore'), { title: t('analysis.run.exploreTitle') }); }
    if (run.stale) { return MA.ui.badge('stale', t('analysis.run.stale'), { title: t('analysis.run.staleTitle') }); }
    return MA.ui.badge('ok', t('analysis.run.current'), { title: t('analysis.run.currentTitle') });
  }

  function runLabel(run) {
    if (!run.run_id) { return t('analysis.run.exploreLabel'); }
    var sp = run.spec || {};
    return MA.i18n.ts(runTime(run)) + ' · ' + (sp.parent ? '└ ' : '') + (sp.name || '—') + ' · ' +
      t(run.stale ? 'analysis.run.stale' : 'analysis.run.current');
  }

  // ---------------------------------------------------------------- KB
  var kbCache = {};
  function kbRules(field) {
    if (kbCache[field]) { return kbCache[field]; }
    // a gyorsítótárazott kérés nem kötődik egy képernyő élettartamához (signal nélkül)
    var p = MA.api.get('/api/kb/rules', { query: { field: field }, toast: false }).then(function (env) {
      return MA.api.list(env, 'items');
    }, function (err) {
      delete kbCache[field];
      if (err.code === 'ABORTED') { throw err; }
      return [];
    });
    kbCache[field] = p;
    return p;
  }

  var KB_FIELDS = ['condition', 'recommendation', 'rationale', 'strength', 'machine_check', 'locator', 'source_ids'];

  function openKb(id) {
    var body = h('div', { 'class': 'kb-item', 'aria-busy': 'true' }, MA.ui.spinner());
    var m = MA.ui.modal({ title: t('analysis.kb.title', { id: id }), body: body, size: 'lg',
      actions: [{ label: t('common.close'), kind: 'primary' }] });
    MA.api.get('/api/kb/item/' + encodeURIComponent(id), { toast: false }).then(function (env) {
      // szerver-alak: {id, table, item: {…}, local_only, note}; a lapos alakot is elfogadjuk
      var d0 = env.data || {};
      var d = d0.item && typeof d0.item === 'object' ? Object.assign({}, d0.item, { local_only: d0.local_only, note: d0.note, kind: d0.table }) : d0;
      body.removeAttribute('aria-busy');
      var dl = h('dl', { 'class': 'kb-fields' });
      KB_FIELDS.forEach(function (f) {
        if (d[f] === undefined || d[f] === null || d[f] === '') { return; }
        dl.appendChild(h('dt', null, t('analysis.kb.field.' + f)));
        dl.appendChild(h('dd', null, pick(d[f], '')));
      });
      MA.dom.mount(body,
        h('p', { 'class': 'kb-meta' }, MA.ui.badge('neutral', d.stage_id || d.kind || 'KB'), ' ',
          d.local_only ? MA.ui.badge('warning', pick(d.note, t('analysis.kb.localOnly'))) : null),
        dl);
    }, function (err) {
      body.removeAttribute('aria-busy');
      MA.dom.mount(body, MA.ui.errorBox(err));
    });
    return m;
  }

  function kbButton(id, title) {
    return h('button', { type: 'button', 'class': 'kb-badge', 'data-kb': id, title: title || t('analysis.kb.open', { id: id }),
      'aria-label': t('analysis.kb.open', { id: id }), onclick: function () { openKb(id); } },
    h('span', { 'aria-hidden': 'true' }, 'ⓚ'), id);
  }

  /** kbRow(labelText, field, signal) → <span> címke + a mezőhöz kötött KB-szabályok ⓚ-jelvényei. */
  function kbRow(labelNode, field, signal) {
    var host = h('span', { 'class': 'kb-list', 'data-field': field });
    kbRules(field).then(function (items) {
      if (signal && signal.aborted) { return; }
      MA.dom.mount(host, items.map(function (it) { return kbButton(it.id || it.rule_id, pick(it.title, '')); }));
    }, function () { /* megszakítva */ });
    return h('span', { 'class': 'kb-row' }, labelNode, ' ', host);
  }

  // ---------------------------------------------------------------- lefúrás
  var dataCache = {};
  function loadDataset(kind, dataset) {
    var key = kind + ':' + dataset;
    if (!dataCache[key]) {
      dataCache[key] = MA.api.get('/api/' + kind, { query: { dataset: dataset }, toast: false }).then(function (env) {
        return env.data || {};
      }, function (err) {
        delete dataCache[key];
        throw err;
      });
    }
    return dataCache[key];
  }
  MA.bus.on('changes', function () { dataCache = {}; });
  MA.bus.on('refresh', function () { dataCache = {}; plotCache = {}; kbCache = {}; });

  /** A kinyerési sor a táblából (GET /api/table: {header, rows: [{row_uid, cells: […]}]}): row_uid, címke, sorindex. */
  function tableRow(table, s) {
    var header = Array.isArray(table.header) ? table.header : [];
    var objs = (Array.isArray(table.rows) ? table.rows : []).map(function (r) {
      var o = {};
      var cells = Array.isArray(r) ? r : (r && Array.isArray(r.cells) ? r.cells : null);
      if (cells) { header.forEach(function (c, i) { o[c] = cells[i]; }); } else if (r && typeof r === 'object') { o = Object.assign({}, r.cells || r); }
      if (r && r.row_uid) { o.row_uid = r.row_uid; }
      return o;
    });
    function hasLabel(o) { return Object.keys(o).some(function (k) { return k !== 'row_uid' && o[k] === s.label; }); }
    var hit = objs.filter(function (o) { return o.row_uid === s.row_uid; })[0] || objs.filter(hasLabel)[0] ||
      (typeof s.row_index === 'number' ? objs[s.row_index] : null);
    return { row: hit || null, header: header.length ? header : (hit ? Object.keys(hit) : []) };
  }

  var METHOD_KIND = { reported: 'ok', calculated: 'info', estimated: 'estimated', digitized: 'estimated', imputed: 'estimated',
    author_contact: 'info', reconciled: 'ok', external_edit: 'warning' };

  function provCell(c) {
    var src = c.source || {};
    var method = c.method || 'reported';
    return h('li', { 'class': 'drill-prov-item', 'data-field': c.field },
      h('span', { 'class': 'drill-field' }, c.field), ' ',
      MA.ui.badge(METHOD_KIND[method] || 'neutral', MA.i18n.has('analysis.prov.method.' + method) ? t('analysis.prov.method.' + method) : method),
      src.page !== undefined && src.page !== null ? h('span', { 'class': 'drill-page' }, ' ' + t('analysis.drill.page', { page: String(src.page) })) : null,
      src.locator ? h('span', { 'class': 'muted' }, ' · ' + String(src.locator)) : null,
      src.quote ? h('q', { 'class': 'drill-quote' }, String(src.quote)) : null);
  }

  /**
   * drilldown(host, {plot, run, outcome, uid, onClose, signal}) — a lefúrási panel (3.5.7):
   * kinyerési sor (GET /api/table), cellaszintű eredet (GET /api/provenance), PDF-oldal aláírt URL-en,
   * „Ugrás a sorhoz”, LOO nélküle, befolyás, RoB. Esc bezár.
   */
  function drilldown(host, o) {
    var plot = o.plot || {};
    var s = MA.plots.common.studyIndex(plot)[o.uid];
    if (!s) { MA.dom.clear(host); return null; }
    var headId = MA.dom.uid('drill-h');
    var dataset = o.outcome ? (o.outcome.data || o.outcome.id) : null;
    var rowHost = h('div', { 'class': 'drill-row', 'aria-busy': 'true' }, MA.ui.spinner());
    var provHost = h('div', { 'class': 'drill-prov' });
    var loo = (plot.loo || []).filter(function (e) { return e.omitted_row_uid === s.row_uid; })[0];
    var inf = (plot.influence || []).filter(function (e) { return e.row_uid === s.row_uid; })[0];
    var fl = s.flags || {};
    var src = s.source || {};
    function close() { if (o.onClose) { o.onClose(); } }
    var pdfBtn = src.doc ? h('button', { type: 'button', 'class': 'btn btn-sm drill-pdf', 'data-doc': src.doc,
      onclick: function () { MA.api.openFile({ doc: src.doc, page: src.page || null }).catch(function () { /* toast már volt */ }); } },
    src.page ? t('analysis.drill.pdfPage', { page: String(src.page) }) : t('analysis.drill.pdf'), ' ↗') : null;
    var panel = h('section', { 'class': 'panel drill', id: 'drilldown', role: 'region', 'aria-labelledby': headId, tabindex: '-1', 'data-uid': s.row_uid,
      onkeydown: function (ev) { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); close(); } } },
    h('div', { 'class': 'drill-head' },
      h('h3', { id: headId, 'class': 'drill-title' }, t('analysis.drill.title', { study: s.label })),
      h('span', { 'class': 'muted drill-meta' }, t('analysis.drill.meta', { row: typeof s.row_index === 'number' ? String(s.row_index + 1) : '—', uid: s.row_uid })),
      h('button', { type: 'button', 'class': 'btn-icon drill-close', 'aria-label': t('analysis.drill.close'), onclick: close }, '×')),
    h('p', { 'class': 'drill-effect' }, h('span', { 'class': 'muted' }, (plot.measure || '') + ' '), MA.ui.num(s.display_text),
      s.weight_text ? h('span', { 'class': 'muted' }, ' · ' + t('plots.forest.weightAria', { w: pick(s.weight_text) })) : null,
      fl.estimated ? [' ', MA.ui.badge('estimated', t('plots.flag.estimated'))] : null,
      fl.outlier ? [' ', MA.ui.badge('info', t('plots.flag.outlier'), { symbol: '⚑' })] : null,
      fl.influential ? [' ', MA.ui.badge('warning', t('plots.flag.influential'), { symbol: '▲' })] : null),
    rowHost,
    provHost,
    h('p', { 'class': 'drill-actions' },
      pdfBtn,
      src.page ? h('span', { 'class': 'drill-page-text' }, ' ' + t('analysis.drill.pageText', { page: String(src.page), loc: src.locator || '' })) : null,
      ' ',
      o.outcome ? h('a', { 'class': 'btn btn-sm drill-jump', href: MA.app.href('extraction', { outcome: o.outcome.id, row: s.row_uid }) }, t('analysis.drill.jump')) : null,
      ' ',
      h('a', { 'class': 'btn btn-sm btn-ghost', href: MA.app.href('appraisal', { study: s.study_id || s.row_uid }) }, t('analysis.drill.appraise'))),
    h('ul', { 'class': 'drill-facts' },
      loo ? h('li', { 'data-fact': 'loo' }, t('analysis.drill.loo'), ' ', MA.ui.num(loo.display_text),
        loo.i2_text ? h('span', { 'class': 'muted' }, ', I² ' + pick(loo.i2_text)) : null) : null,
      inf ? h('li', { 'data-fact': 'influence' }, t('analysis.drill.influence', {
        rstudent: pick(inf.rstudent_text), cook: pick(inf.cook_d_text), hat: pick(inf.hat_text) }), ' ',
      inf.influential ? MA.ui.badge('warning', t('plots.flag.influential'), { symbol: '▲' }) : h('span', { 'class': 'muted' }, t('analysis.drill.notInfluential'))) : null,
      h('li', { 'data-fact': 'rob' }, t('analysis.drill.rob'), ' ',
        h('span', { 'class': 'rob-sym is-' + (fl.rob || 'none'), 'aria-hidden': 'true' }, { low: '●', some: '◐', high: '○', critical: '⊘' }[fl.rob] || '–'), ' ',
        MA.i18n.has('plots.rob.' + (fl.rob || 'none')) ? t('plots.rob.' + (fl.rob || 'none')) : String(fl.rob))));
    MA.dom.mount(host, panel);
    var rowUid = null;
    if (dataset) {
      loadDataset('table', dataset).then(function (table) {
        var hit = tableRow(table, s);
        rowUid = hit.row && hit.row.row_uid ? hit.row.row_uid : null;
        rowHost.removeAttribute('aria-busy');
        if (!hit.row) { MA.dom.mount(rowHost, MA.ui.emptyState('analysis.drill.noRow')); return; }
        MA.dom.mount(rowHost, h('dl', { 'class': 'drill-cells' }, hit.header.filter(function (c) { return c !== 'row_uid'; }).map(function (c) {
          return h('div', { 'class': 'drill-cell', 'data-col': c }, h('dt', null, c), h('dd', null, hit.row[c] === undefined || hit.row[c] === null ? '—' : String(hit.row[c])));
        })));
      }, function (err) {
        rowHost.removeAttribute('aria-busy');
        if (err.code !== 'ABORTED') { MA.dom.mount(rowHost, MA.ui.errorBox(err)); }
      });
      loadDataset('table', dataset).catch(function () { return null; }).then(function () {
        return loadDataset('provenance', dataset);
      }).then(function (prov) {
        var uid = rowUid || s.row_uid;
        var cells = (Array.isArray(prov.cells) ? prov.cells : []).filter(function (c) { return c.row_uid === uid; });
        MA.dom.mount(provHost, cells.length
          ? [h('h4', { 'class': 'drill-sub' }, t('analysis.drill.provenance')), h('ul', { 'class': 'drill-prov-list' }, cells.map(provCell))]
          : MA.ui.emptyState('analysis.drill.noProv'));
      }, function (err) {
        if (err.code !== 'ABORTED') { MA.dom.mount(provHost, MA.ui.errorBox(err)); }
      });
    } else {
      MA.dom.clear(rowHost);
    }
    if (o.focus !== false) { panel.focus(); }
    return panel;
  }

  // ---------------------------------------------------------------- letöltés aláírt URL-en
  function download(path) {
    return MA.api.post('/api/fileurl', { doc: null, path: path }).then(function (env) {
      var url = env.data && env.data.url;
      if (typeof url !== 'string' || url.indexOf('/f/') !== 0) { throw MA.api.ApiError('BAD_RESPONSE', 'fileurl', 200); }
      var a = h('a', { href: url, download: String(path).split('/').pop(), 'class': 'sr-only' }, '.');
      document.body.appendChild(a);
      a.click();
      a.parentNode.removeChild(a);
      return url;
    });
  }

  MA.analysis = {
    outcomes: outcomes,
    pickOutcome: pickOutcome,
    engineOptions: engineOptions,
    measureLabel: measureLabel,
    cmd: cmd,
    linkSignals: linkSignals,
    analyze: analyze,
    addRun: addRun,
    loadRuns: loadRuns,
    loadPlot: loadPlot,
    shortSha: shortSha,
    runBadge: runBadge,
    runLabel: runLabel,
    kbRules: kbRules,
    kbButton: kbButton,
    kbRow: kbRow,
    openKb: openKb,
    drilldown: drilldown,
    download: download,
    _tableRow: tableRow,
    _plotCache: function () { return plotCache; }
  };
})();
