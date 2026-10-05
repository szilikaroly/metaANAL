/* screens/extraction.js — 3 Kinyerés: adatkinyerés és élő validáció (terv 3.5.3, 3.5.4, 4.3, 4.8, 6.2–6.3, 7.3).
 *
 * HTTP API (3.4) — a kérés-alakok az egyeztetendő feltevések (a szerver párhuzamosan készül):
 *   GET  /api/table?dataset=<relút>          → data: {dataset, etag, header, rows: [{row_uid, cells, line}], format, uid}
 *   PUT  /api/table   If-Match: <etag>       ← {dataset, header, rows: [{row_uid, cells}], provenance?}
 *        409 CONFLICT details: {dataset, etag, diff: [{row_uid, column, base, theirs, mine, conflict}], base_known, truncated}
 *        423 LOCKED (Excel-zár) → „Zárd be a fájlt az Excelben, majd [Újra]”
 *   POST /api/validate                         ← szk.ma.validate-request/v1 (piszkozat, NEM ment) → szk.ma.validation/v1
 *   GET/PUT /api/provenance?dataset=         ↔ szk.ma.provenance/v1 (If-Match)
 *   GET/PUT /api/documents                     ↔ szk.ma.documents/v1 (If-Match); POST /api/fileurl (MA.api.openFile)
 *   POST /api/log/decision                     ← {agent:'user', decision, rationale, kb_refs:[V0xx], stage, context} („Nem hiba — indoklás”)
 * A felület NEM számol: a szabályok, a k, a cellára képzés kulcsai (row/rows/row_uid/fields + column_map) a motoré;
 * a cellák nyers szövegek; minden megjelenített számszöveg a motoré vagy darabszám.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  var DEBOUNCE_MS = 250;
  var AUTOSAVE_MS = 400;
  var SEV = ['error', 'warning', 'info'];
  var METHODS = ['reported', 'calculated', 'estimated', 'digitized', 'imputed', 'author_contact', 'reconciled', 'external_edit'];
  var EST = { estimated: 1, digitized: 1, imputed: 1 };
  var PROV_KIND = { estimated: 'estimated', digitized: 'estimated', imputed: 'estimated', reconciled: 'reconciled', external_edit: 'external' };
  var SRC_COL = 'src';
  var YES = 'igen';
  var PROV_SCHEMA = 'szk.ma.provenance/v1';
  var DOCS_SCHEMA = 'szk.ma.documents/v1';

  var G = null;     // a látható rács (MA.grid)
  /** A pillanatkép (csak olvasható HTML) módja: a rács nem szerkeszthető, az író eszközök tiltva (UX-16). */
  function readOnly() { return document.documentElement.getAttribute('data-mode') === 'snapshot'; }
  var V = null;     // a képernyőpéldány: {ctx, run, els, timer}

  function st() { return MA.store.get('extraction.draft') || null; }
  function setDraft(d) { MA.store.set('extraction.draft', d); }
  function cleanEtag(e) { return e ? String(e).replace(/^W\//, '').replace(/"/g, '') : ''; }
  function pad2(n) { return (n < 10 ? '0' : '') + String(n); }
  function nowHM() { var x = new Date(); return pad2(x.getHours()) + ':' + pad2(x.getMinutes()); }
  function nowIso() { return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'); }
  function clone(o) { return o === undefined ? undefined : JSON.parse(JSON.stringify(o)); }
  function actor() { return MA.store.get('project.user.initials') || MA.store.get('session.user') || 'user'; }
  function dirty(d) { return !!(d && (d.tableDirty || d.provDirty)); }
  /** Módosítás jelölése; a ver-számláló miatt a közben futó mentés nem törli az újabb módosítás jelzőjét. */
  function touch(d, prov) {
    if (prov) { d.provDirty = true; d.pver = (d.pver || 0) + 1; } else { d.tableDirty = true; d.tver = (d.tver || 0) + 1; }
  }
  function str(v) { return v === null || v === undefined ? '' : String(v); }

  // ---------------------------------------------------------------- oszlop ↔ kanonikus mező (a motor column_map-je)
  function dedupe(names) {
    var seen = {};
    return names.map(function (n) { seen[n] = (seen[n] || 0) + 1; return seen[n] === 1 ? n : n + '#' + seen[n]; });
  }

  /** colMap: kanonikus → oszlop-id; fieldOf: oszlop-id → kanonikus (column_map nélkül: a fejléc neve). */
  function mapColumns(d) {
    var cm = d.validation && d.validation.column_map ? d.validation.column_map : (d.tableColumnMap || null);
    var colMap = {}, fieldOf = {};
    d.header.forEach(function (name, i) { fieldOf['c' + i] = name; });
    if (cm) {
      Object.keys(cm).forEach(function (canon) {
        var i = d.header.indexOf(cm[canon]);
        if (i >= 0 && !colMap[canon]) { colMap[canon] = 'c' + i; fieldOf['c' + i] = canon; }
      });
    }
    d.header.forEach(function (name, i) {
      var low = String(name).trim().toLowerCase();
      if (!colMap[low] && !(cm && cm[low])) { colMap[low] = colMap[low] || 'c' + i; }
    });
    d.colMap = colMap;
    d.fieldOf = fieldOf;
  }
  function colOf(d, field) { return d.colMap ? d.colMap[field] || null : null; }
  function fieldOf(d, col) { return d.fieldOf && d.fieldOf[col] ? d.fieldOf[col] : col; }

  function requiredNumeric(d) {
    var ms = MA.store.get('engine.measures') || [];
    var out = {};
    ms.forEach(function (m) {
      if (m.id === d.measure && Array.isArray(m.required_columns)) {
        m.required_columns.forEach(function (f) { var c = colOf(d, f); if (c) { out[c] = true; } });
      }
    });
    return out;
  }

  // ---------------------------------------------------------------- megállapítások → cellák (6.3/4)
  function findingTitle(f) {
    if (MA.i18n.lang() !== 'hu') {
      var rules = MA.store.get('engine.rules') || [];
      for (var i = 0; i < rules.length; i++) {
        if ((rules[i].id || rules[i].code) === f.code && rules[i].title) { return MA.i18n.pick(rules[i].title, ''); }
      }
    }
    return MA.i18n.pick(f.title, f.code);
  }

  function findingUids(f, uids) {
    var out = [];
    function add(u) { if (u && out.indexOf(u) < 0) { out.push(u); } }
    if (Array.isArray(f.rows)) { f.rows.forEach(function (i) { add(uids[i]); }); }
    add(f.row_uid);
    if (typeof f.row === 'number') { add(uids[f.row]); }
    return out;
  }

  /**
   * mapFindings(validation, uids, colOfField) → {cells, rows, byUid, problem} — a motor megállapításai a rács
   * dekorációira; a sor a row_uid-ból vagy a 0-alapú row/rows indexből (a kéréskori sorrendben), az oszlop a
   * kanonikus mezőből (column_map). Mezőhöz nem köthető sorszintű jelzés a sorfejlécre kerül.
   */
  function mapFindings(v, uids, colOfField) {
    var out = { cells: {}, rows: {}, byUid: {}, problem: {} };
    function rowEntry(u) { return out.rows[u] || (out.rows[u] = { blocked: false, kinds: [], messages: [] }); }
    ((v && v.findings) || []).forEach(function (f) {
      var kind = f.acknowledged ? 'ack' : (SEV.indexOf(f.severity) >= 0 ? f.severity : 'info');
      var msg = f.code + ' ' + findingTitle(f) + (f.detail ? ': ' + MA.i18n.pick(f.detail, '') : '');
      var cols = (f.fields || []).map(colOfField).filter(function (c) { return !!c; });
      findingUids(f, uids).forEach(function (u) {
        (out.byUid[u] = out.byUid[u] || []).push(f);
        if (!f.acknowledged) { out.problem[u] = true; }
        if (f.blocking) { rowEntry(u).blocked = true; }
        if (!cols.length) {
          var r = rowEntry(u);
          if (r.kinds.indexOf(kind) < 0) { r.kinds.push(kind); }
          r.messages.push(msg);
          return;
        }
        var cu = out.cells[u] || (out.cells[u] = {});
        cols.forEach(function (c) {
          var e = cu[c] || (cu[c] = { kinds: [], messages: [] });
          if (e.kinds.indexOf(kind) < 0) { e.kinds.push(kind); }
          e.messages.push(msg);
        });
      });
    });
    ((v && v.excluded) || []).forEach(function (x) {
      var u = typeof x.row === 'number' ? uids[x.row] : null;
      if (!u) { return; }
      var r = rowEntry(u);
      r.blocked = true;
      r.messages.push(MA.i18n.pick(x.reason, ''));
      out.problem[u] = true;
    });
    return out;
  }

  function mergeDeco(a, b) {
    Object.keys(b.cells).forEach(function (u) {
      var cu = a.cells[u] || (a.cells[u] = {});
      Object.keys(b.cells[u]).forEach(function (c) {
        var e = cu[c] || (cu[c] = { kinds: [], messages: [] });
        b.cells[u][c].kinds.forEach(function (k) { if (e.kinds.indexOf(k) < 0) { e.kinds.push(k); } });
        e.messages = e.messages.concat(b.cells[u][c].messages);
      });
    });
    return a;
  }

  function provDeco(d) {
    var out = { cells: {} };
    function put(u, c, kind, msg) {
      var cu = out.cells[u] || (out.cells[u] = {});
      var e = cu[c] || (cu[c] = { kinds: [], messages: [] });
      if (e.kinds.indexOf(kind) < 0) { e.kinds.push(kind); e.messages.push(msg); }
    }
    (d.prov.cells || []).forEach(function (p) {
      var kind = PROV_KIND[p.method];
      var c = colOf(d, p.field);
      if (kind && c) { put(p.row_uid, c, kind, t('extraction.prov.method.' + p.method)); }
    });
    Object.keys(d.extMarks || {}).forEach(function (k) {
      var i = k.indexOf('|');
      put(k.slice(0, i), k.slice(i + 1), 'external', t('extraction.conflict.takenTheirs'));
    });
    return out;
  }

  // ---------------------------------------------------------------- eredet (4.8)
  function findProv(d, uid, field) {
    var list = d.prov.cells || [];
    for (var i = 0; i < list.length; i++) { if (list[i].row_uid === uid && list[i].field === field) { return list[i]; } }
    return null;
  }

  function putProv(d, entry) {
    d.prov.cells = (d.prov.cells || []).filter(function (p) { return !(p.row_uid === entry.row_uid && p.field === entry.field); });
    if (!entry.__absent) { d.prov.cells.push(entry); }
  }

  function withHistory(prev, next) {
    if (!prev) { return next; }
    var hist = Array.isArray(prev.history) ? prev.history.slice() : [];
    hist.push({ value_as_entered: prev.value_as_entered === undefined ? null : prev.value_as_entered, method: prev.method,
      source: prev.source || null, extracted_by: prev.extracted_by || null, extracted_at: prev.extracted_at || null });
    next.history = hist.slice(-50);
    return next;
  }

  function sourceSummary(d, uid) {
    var list = (d.prov.cells || []).filter(function (p) { return p.row_uid === uid && p.source && (p.source.page || p.source.locator); });
    if (!list.length) { return ''; }
    var s = list[0].source;
    return t('extraction.src.short', { page: s.page === null || s.page === undefined ? '?' : String(s.page), loc: s.locator || '' }).trim() +
      (list.length > 1 ? ' +' + String(list.length - 1) : '');
  }

  function provForSave(d) {
    var doc = clone(d.prov);
    doc.schema = PROV_SCHEMA;
    doc.table = d.dataset;
    doc.table_sha256 = cleanEtag(d.etag) || null;
    return doc;
  }

  // ---------------------------------------------------------------- betöltés
  function provFrom(env, dataset) {
    var data = env && env.data;
    var doc = data && data.schema === PROV_SCHEMA ? data : (data && data.provenance) || null;
    doc = doc ? clone(doc) : { schema: PROV_SCHEMA, table: dataset, table_sha256: null, cells: [] };
    if (!Array.isArray(doc.cells)) { doc.cells = []; }
    delete doc.state;
    return doc;
  }

  function docsFrom(env) {
    var data = env && env.data;
    var doc = data && Array.isArray(data.docs) ? data : (data && data.documents) || { schema: DOCS_SCHEMA, docs: [] };
    return clone(doc);
  }

  function load(target, ctx) {
    var q = { query: { dataset: target.dataset }, signal: ctx.signal };
    var soft = function (p) { return p.then(null, function (e) { if (e.code === 'ABORTED') { throw e; } return null; }); };
    return Promise.all([
      MA.api.get('/api/table', Object.assign({ toast: false }, q)),
      soft(MA.api.get('/api/provenance', Object.assign({ quiet: ['NOT_FOUND'] }, q))),
      soft(MA.api.get('/api/documents', { signal: ctx.signal, quiet: ['NOT_FOUND'] }))
    ]).then(function (res) {
      var data = res[0].data || {};
      var header = Array.isArray(data.header) ? data.header.slice() : [];
      var d = {
        outcome: target.id, name: target.name, dataset: data.dataset || target.dataset, measure: target.measure,
        header: header,
        rows: (data.rows || []).map(function (r) {
          var cells = {};
          header.forEach(function (x, i) { cells['c' + i] = str(r.cells ? r.cells[i] : ''); });
          return { uid: r.row_uid, cells: cells };
        }),
        format: data.format || {},
        tableColumnMap: data.column_map && typeof data.column_map === 'object' ? data.column_map : null,
        etag: res[0].etag || data.etag || null,
        prov: provFrom(res[1], target.dataset),
        provEtag: res[1] ? res[1].etag || null : null,
        provState: res[1] && res[1].data ? res[1].data.state || null : null,
        docs: docsFrom(res[2]),
        docsEtag: res[2] ? res[2].etag || null : null,
        tableDirty: false, provDirty: false, provKeys: {}, validation: null, uids: [], extMarks: {},
        savedAt: null, locked: null, external: false, saving: null
      };
      d.history = MA.store.createHistory({ apply: applyOp, limit: 500, onChange: function () { refreshToolbar(); } });
      mapColumns(d);
      return d;
    });
  }

  // ---------------------------------------------------------------- undo/redo (store.createHistory)
  function applyOp(op) {
    var d = st();
    if (!d || !G) { return; }
    if (op.type === 'cell') { G.set(op.row_uid, op.field, op.after); touch(d); } else if (op.type === 'rows-insert') {
      G.insertRows(op.index, op.rows.map(function (r) { return { uid: r.row_uid, cells: r.cells }; }));
      touch(d);
    } else if (op.type === 'rows-delete') {
      G.deleteRows(op.rows.map(function (r) { return r.row_uid; }));
      touch(d);
    } else if (op.type === 'batch') { op.ops.forEach(applyOp); } else if (op.type === 'custom') {
      (op['do'].prov || []).forEach(function (e) { putProv(d, clone(e)); d.provKeys[e.row_uid + '|' + e.field] = true; });
      touch(d, true);
    }
  }

  function afterHistory() {
    refreshSrcAll();
    redecorate();
    refreshStatus();
    scheduleValidate();
  }

  // ---------------------------------------------------------------- validálás (6.3)
  function validationBody(d) {
    return {
      schema: 'szk.ma.validate-request/v1',
      dataset: d.dataset,
      measure: d.measure,
      options: {},
      table: {
        header: ['row_uid'].concat(d.header),
        rows: G.rows().map(function (r) { return [r.uid].concat(d.header.map(function (x, i) { return str(r.cells['c' + i]); })); }),
        decimal_mark: d.format && d.format.decimal_mark ? d.format.decimal_mark : null
      },
      base_sha256: cleanEtag(d.etag) || null
    };
  }

  function validateNow() {
    var d = st();
    if (!d || !G || !V) { return Promise.resolve(null); }
    var inst = V;
    var body = validationBody(d);
    var uids = G.rows().map(function (r) { return r.uid; });
    inst.validating = true;
    renderSummary();
    return inst.run(function (seq, signal) {
      return MA.api.post('/api/validate', body, { clientSeq: seq, signal: signal, toast: false });
    }).then(function (env) {
      if (!env || V !== inst || !inst.ctx.alive()) { return null; }
      inst.validating = false;
      inst.validationError = null;
      d.validation = env.data || null;
      d.validationMs = env.meta && typeof env.meta.elapsed_ms === 'number' ? env.meta.elapsed_ms : null;
      d.uids = uids;
      var before = JSON.stringify([d.colMap, d.fieldOf]);
      mapColumns(d);
      if (JSON.stringify([d.colMap, d.fieldOf]) !== before || !inst.colsMeta) { updateColumnMeta(d); renderProv(); }
      applyValidation();
      return env;
    }, function (err) {
      if (V !== inst || !inst.ctx.alive()) { return null; }
      inst.validating = false;
      inst.validationError = err;
      renderSummary();
      renderFindings();
      return null;
    });
  }

  function scheduleValidate() { if (V && V.debounced) { V.debounced(); } }

  function updateColumnMeta(d) {
    var num = requiredNumeric(d);
    V.colsMeta = true;
    G.updateColumns(d.header.map(function (name, i) {
      var id = 'c' + i;
      var canon = fieldOf(d, id);
      return { id: id, hint: canon !== name ? canon : null, numeric: !!num[id] };
    }));
  }

  function applyValidation() {
    var d = st();
    if (!d || !G) { return; }
    redecorate();
    renderSummary();
    renderFindings();
    applyFilter();
    var s = (d.validation && d.validation.summary) || {};
    var badges = [];
    if (s.error) { badges.push({ kind: 'error', text: String(s.error), title: t('extraction.sum.errors', { n: String(s.error) }) }); }
    if (s.warning) { badges.push({ kind: 'warning', text: String(s.warning), title: t('extraction.sum.warnings', { n: String(s.warning) }) }); }
    MA.app.setTabBadges('extraction', badges);
  }

  function redecorate() {
    var d = st();
    if (!d || !G) { return; }
    var m = mapFindings(d.validation, d.uids || [], function (f) { return colOf(d, f); });
    V.problem = m.problem;
    V.byUid = m.byUid;
    G.setDecorations(mergeDeco({ cells: m.cells, rows: m.rows }, provDeco(d)));
  }

  function applyFilter() {
    if (!G || !V) { return; }
    var only = MA.prefs.get('extraction.onlyProblems', '0') === '1';
    var prob = V.problem || {};
    G.setFilter(only ? function (r) { return !!prob[r.uid]; } : null);
    if (V.els.count) { V.els.count.textContent = t('extraction.rowsShown', { n: String(G.visibleCount()), total: String(G.rows().length) }); }
  }

  // ---------------------------------------------------------------- mentés (If-Match, 409, 423, 403 PHI / írás-tartás)
  /** save({phi_override?, consent?}) — PUT /api/table (+ eredet egy tranzakcióban) vagy csak PUT /api/provenance. */
  function save(opts) {
    opts = opts || {};
    var d = st();
    if (!d || !G) { return Promise.resolve(false); }
    if (G.editing()) { G.commit(); }
    if (!dirty(d)) { return Promise.resolve(true); }
    if (d.saving) { return d.saving; }
    var p;
    var withProv = d.provDirty;
    var tver = d.tver, pver = d.pver;
    var provOnly = !d.tableDirty;
    if (!provOnly) {
      var body = {
        dataset: d.dataset,
        header: d.header.slice(),
        rows: G.rows().map(function (r) { return { row_uid: r.uid, cells: d.header.map(function (x, i) { return str(r.cells['c' + i]); }) }; })
      };
      if (withProv) {
        body.provenance = provForSave(d);
        // az oldalfájl is feltételes írás: a betöltött eredet-etag (null = még nincs oldalfájl)
        body.provenance_if_match = cleanEtag(d.provEtag) || null;
      }
      if (opts.phi_override) { body.phi_override = opts.phi_override; }
      if (opts.consent) { body.consent = true; }
      p = MA.api.put('/api/table', body, { ifMatch: d.etag || undefined, quiet: ['CONFLICT', 'LOCKED', 'FORBIDDEN'] }).then(function (env) {
        var data = env.data || {};
        d.etag = env.etag || data.etag || d.etag;
        if (d.tver === tver) { d.tableDirty = false; }
        if (withProv) {
          if (data.provenance_etag) { d.provEtag = data.provenance_etag; }
          if (d.pver === pver) { d.provDirty = false; d.provKeys = {}; }
          return MA.api.get('/api/provenance', { query: { dataset: d.dataset }, toast: false }).then(function (pe) {
            if (d.pver === pver) { d.prov = provFrom(pe, d.dataset); }
            d.provEtag = pe.etag || d.provEtag;
            d.provState = pe.data && pe.data.state ? pe.data.state : null;
          }, function () { return null; });
        }
        return null;
      });
    } else {
      p = MA.api.put('/api/provenance', provForSave(d), { query: { dataset: d.dataset }, ifMatch: d.provEtag || undefined, quiet: ['CONFLICT', 'LOCKED', 'FORBIDDEN'] })
        .then(function (env) {
          d.provEtag = env.etag || (env.data && env.data.etag) || d.provEtag;
          if (env.data && env.data.state) { d.provState = env.data.state; }
          if (d.pver === pver) { d.provDirty = false; d.provKeys = {}; }
        });
    }
    d.saving = p.then(function () {
      d.saving = null;
      d.locked = null;
      d.hold = null;
      d.savedAt = nowHM();
      afterSave();
      scheduleValidate();
      return true;
    }, function (err) {
      d.saving = null;
      if (err.code === 'CONFLICT') {
        // az eredet-oldalfájl változott (a tábla nem): friss eredetre fésüljük a sajátunkat, majd újramentés
        if (provOnly || (err.details && err.details.kind === 'provenance')) { return provConflict(d, err); }
        conflictDialog(err);
      } else if (err.code === 'LOCKED') {
        // részleges írás: a CSV már az új változat (új etag), csak az oldalfájl maradt ki
        if (err.details && err.details.partial && err.details.etag) { d.etag = err.details.etag; }
        d.locked = err;
      } else if (err.code === 'FORBIDDEN' && err.details && Array.isArray(err.details.phi)) {
        phiDialog(err);
      } else if (err.code === 'FORBIDDEN') {
        d.hold = err;
      }
      afterSave();
      return false;
    });
    refreshStatus();
    return d.saving;
  }

  function afterSave() {
    if (!V) { return; }
    refreshStatus();
    renderAlerts();
    renderProv();
    refreshSrcAll();
    redecorate();
  }

  /** PHI/TAJ-gyanú (7.4): a találatok (érték nélkül) és a hamis riasztás indokolt, naplózott felülbírálása. */
  function phiDialog(err) {
    var det = err.details || {};
    var ta = h('textarea', { id: 'ex-phi-reason', rows: '3', 'aria-required': 'true' });
    var errLine = h('p', { 'class': 'ex-cf-err', role: 'alert', hidden: true }, t('extraction.ack.required'));
    var m = MA.ui.modal({
      title: t('extraction.phi.title'),
      body: [
        h('p', null, err.message || ''),
        det.summary ? h('p', { 'class': 'muted' }, det.summary) : null,
        h('ul', { 'class': 'ex-phi-list' }, (det.phi || []).slice(0, 20).map(function (f) {
          return h('li', null, f.kind === 'column' ? t('extraction.phi.col', { col: f.column || '—', pattern: f.pattern || '' })
            : t('extraction.phi.cell', { col: f.column || '—', row: typeof f.row_index === 'number' ? String(f.row_index + 1) : '—', pattern: f.pattern || '' }));
        })),
        h('label', { htmlFor: 'ex-phi-reason' }, t('extraction.phi.reason')), ta,
        h('p', { 'class': 'muted' }, t('extraction.phi.help')), errLine
      ],
      actions: [
        { label: t('common.cancel'), kind: 'ghost' },
        { label: t('extraction.phi.override'), kind: 'danger', onClick: function () {
          var why = ta.value.trim();
          if (!why) { ta.setAttribute('aria-invalid', 'true'); errLine.hidden = false; ta.focus(); return false; }
          save({ phi_override: why });
          return true;
        } }
      ]
    });
    m.el.id = 'ex-phi';
  }

  /** Az eredet-oldalfájl közben változott: a friss változatra ráírjuk a saját módosított bejegyzéseinket. */
  function provConflict(d, err) {
    return MA.api.get('/api/provenance', { query: { dataset: d.dataset }, toast: false }).then(function (pe) {
      var mine = {};
      (d.prov.cells || []).forEach(function (c) { mine[c.row_uid + '|' + c.field] = c; });
      var fresh = provFrom(pe, d.dataset);
      Object.keys(d.provKeys).forEach(function (k) {
        var i = k.indexOf('|');
        putProv({ prov: fresh }, mine[k] || { row_uid: k.slice(0, i), field: k.slice(i + 1), __absent: true });
      });
      d.prov = fresh;
      d.provEtag = pe.etag || null;
      MA.ui.toast({ kind: 'warning', title: t('extraction.prov.conflict'), message: err.message || '' });
      afterSave();
      return false;
    });
  }

  function conflictDialog(err) {
    var d = st();
    var det = err.details || {};
    var diff = Array.isArray(det.diff) ? det.diff : [];
    var choice = {};
    diff.forEach(function (x, i) { if (!x.conflict) { choice[i] = 'theirs'; } });
    var errLine = h('p', { 'class': 'ex-cf-err', role: 'alert', hidden: true }, t('extraction.conflict.chooseAll'));
    var inputs = [];
    var labelCol = colOf(d, 'study') || 'c0';
    function cellText(v) { return v === null || v === undefined ? t('extraction.conflict.none') : String(v); }
    function setAll(v) {
      inputs.forEach(function (x) { x.el.checked = x.value === v; if (x.el.checked) { choice[x.i] = v; } });
      errLine.hidden = true;
    }
    var rows = diff.map(function (x, i) {
      var label = G && G.row(x.row_uid) ? G.get(x.row_uid, labelCol) : x.row_uid;
      var name = 'ex-cf-' + String(i);
      function radio(v) {
        var el = h('input', { type: 'radio', name: name, value: v, checked: choice[i] === v, onchange: function () { choice[i] = v; errLine.hidden = true; } });
        inputs.push({ el: el, value: v, i: i });
        return h('label', { 'class': 'ex-cf-opt' }, el, ' ', t('extraction.conflict.' + v));
      }
      return h('tr', { 'class': x.conflict ? 'is-conflict' : null, dataset: { uid: x.row_uid, column: x.column } },
        h('th', { scope: 'row' }, label || '—', x.conflict ? h('span', { 'class': 'sr-only' }, ' — ' + t('extraction.conflict.isConflict')) : null),
        h('td', null, x.column),
        h('td', null, det.base_known ? cellText(x.base) : '—'),
        h('td', null, cellText(x.theirs)),
        h('td', null, cellText(x.mine)),
        h('td', null, h('div', { role: 'radiogroup', 'aria-label': t('extraction.conflict.choice') + ': ' + (label || '') + ' · ' + x.column }, radio('mine'), radio('theirs'))));
    });
    var modal = MA.ui.modal({
      title: t('extraction.conflict.title'),
      size: 'lg',
      body: [
        h('p', null, err.message || t('err.CONFLICT')),
        det.base_known ? null : h('p', { 'class': 'muted' }, t('extraction.conflict.noBase')),
        det.truncated ? h('p', { 'class': 'muted' }, t('extraction.conflict.truncated')) : null,
        h('div', { 'class': 'row' },
          h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-cf-all-mine', onclick: function () { setAll('mine'); } }, t('extraction.conflict.allMine')),
          h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-cf-all-theirs', onclick: function () { setAll('theirs'); } }, t('extraction.conflict.allTheirs'))),
        h('div', { 'class': 'table-wrap ex-cf-wrap' }, h('table', { 'class': 'table table-compact', id: 'ex-conflict-table' },
          h('thead', null, h('tr', null, ['study', 'column', 'base', 'theirsCol', 'mineCol', 'choice'].map(function (k) {
            return h('th', { scope: 'col' }, t('extraction.conflict.' + k));
          }))),
          h('tbody', null, rows))),
        errLine
      ],
      actions: [
        { label: t('common.cancel'), kind: 'ghost' },
        { label: t('extraction.conflict.reload'), kind: 'danger', onClick: function () { reloadDataset(); } },
        { label: t('extraction.conflict.merge'), kind: 'primary', onClick: function () {
          for (var i = 0; i < diff.length; i++) { if (!choice[i]) { errLine.hidden = false; return false; } }
          mergeAndSave(diff, choice);
          return true;
        } }
      ]
    });
    modal.el.id = 'ex-conflict';
  }

  function mergeAndSave(diff, choice) {
    var d = st();
    return MA.api.get('/api/table', { query: { dataset: d.dataset } }).then(function (env) {
      var td = env.data || {};
      var tHeader = Array.isArray(td.header) ? td.header : [];
      var tNames = dedupe(tHeader);
      var myNames = dedupe(d.header);
      var nameToCol = {};
      myNames.forEach(function (n, i) { nameToCol[n] = 'c' + i; });
      var added = tNames.filter(function (n) { return nameToCol[n] === undefined; });
      var theirs = {};
      (td.rows || []).forEach(function (r) { theirs[r.row_uid] = r; });
      if (added.length) {      // szerkezeti változás (új oszlop): a fejléc bővül, a visszavonási verem törlődik
        added.forEach(function (n) {
          var ti = tNames.indexOf(n);
          d.header.push(tHeader[ti]);
          var id = 'c' + String(d.header.length - 1);
          nameToCol[n] = id;
          G.rows().forEach(function (r) { r.cells[id] = theirs[r.uid] ? str(theirs[r.uid].cells[ti]) : ''; });
        });
        var keep = G.rows();
        G.setColumns(columnsFor(d));
        G.setRows(keep);
        d.history.clear();
      }
      function rowFrom(tr) {
        var cells = {};
        tNames.forEach(function (n, ti) { if (nameToCol[n]) { cells[nameToCol[n]] = str(tr.cells[ti]); } });
        cells[SRC_COL] = sourceSummary(d, tr.row_uid);
        return { uid: tr.row_uid, cells: cells };
      }
      var ops = [];
      var addRows = [];
      var del = [];
      diff.forEach(function (x, i) {
        if (choice[i] !== 'theirs') { return; }
        var mine = G.row(x.row_uid);
        var tr = theirs[x.row_uid];
        if (!tr) { if (mine && del.indexOf(x.row_uid) < 0) { del.push(x.row_uid); } return; }
        if (!mine) { if (!addRows.some(function (r) { return r.uid === x.row_uid; })) { addRows.push(rowFrom(tr)); } return; }
        var col = nameToCol[x.column];
        if (!col) { return; }
        var after = x.theirs === null || x.theirs === undefined ? '' : String(x.theirs);
        var before = G.get(x.row_uid, col);
        if (before !== after) {
          G.set(x.row_uid, col, after);
          ops.push({ type: 'cell', dataset: d.dataset, row_uid: x.row_uid, field: col, before: before, after: after });
        }
        d.extMarks[x.row_uid + '|' + col] = true;
      });
      del.map(function (u) { return { u: u, i: G.rows().indexOf(G.row(u)) }; }).sort(function (a, b) { return b.i - a.i; }).forEach(function (x) {
        var r = G.row(x.u);
        ops.push({ type: 'rows-delete', dataset: d.dataset, index: x.i, rows: [{ row_uid: r.uid, cells: Object.assign({}, r.cells) }] });
        G.deleteRows([x.u]);
      });
      if (addRows.length) {
        var at = G.rows().length;
        G.insertRows(at, addRows);
        ops.push({ type: 'rows-insert', dataset: d.dataset, index: at, rows: addRows.map(function (r) { return { row_uid: r.uid, cells: r.cells }; }) });
      }
      if (ops.length) { d.history.push({ type: 'batch', label: 'merge', ops: ops }); }
      d.etag = env.etag || td.etag || d.etag;
      touch(d);
      redecorate();
      return save();
    });
  }

  function reloadDataset() {
    var d = st();
    if (d) { d.tableDirty = false; d.provDirty = false; }
    setDraft(null);
    if (V) { V.ctx.rerender(); }
  }

  // ---------------------------------------------------------------- szerkesztés a rácsban
  function onGridChange(batch) {
    var d = st();
    if (!d) { return; }
    var ops = [];
    if (batch.inserted) {
      ops.push({ type: 'rows-insert', dataset: d.dataset, index: batch.inserted.index,
        rows: batch.inserted.rows.map(function (r) { return { row_uid: r.uid, cells: r.cells }; }) });
    }
    batch.cells.forEach(function (c) { ops.push({ type: 'cell', dataset: d.dataset, row_uid: c.uid, field: c.col, before: c.before, after: c.after }); });
    if (!ops.length) { return; }
    d.history.push(ops.length === 1 ? ops[0] : { type: 'batch', label: batch.source, ops: ops });
    touch(d);
    refreshStatus();
    scheduleValidate();
  }

  function newRow(taken) {
    var cells = {};
    var d = st();
    d.header.forEach(function (x, i) { cells['c' + i] = ''; });
    cells[SRC_COL] = '';
    return { uid: MA.grid.newUid(taken), cells: cells };
  }

  function addRow() {
    var d = st();
    if (!d || !G) { return; }
    if (G.rows().length >= G.maxRows) { MA.ui.toast({ kind: 'warning', title: t('grid.tooManyRows', { max: String(G.maxRows) }) }); return; }
    var taken = {};
    G.rows().forEach(function (r) { taken[r.uid] = true; });
    var r = newRow(taken);
    var pos = G.active();
    var at = pos && pos.rowIndex >= 0 ? pos.rowIndex + 1 : G.rows().length;
    G.insertRows(at, [r]);
    d.history.push({ type: 'rows-insert', dataset: d.dataset, index: at, rows: [{ row_uid: r.uid, cells: r.cells }] });
    touch(d);
    G.focusCell(r.uid, 'c0');
    refreshStatus();
    scheduleValidate();
  }

  function deleteRow() {
    var d = st();
    var pos = G ? G.active() : null;
    if (!d || !pos) { return; }
    var r = G.row(pos.uid);
    var label = G.get(pos.uid, colOf(d, 'study') || 'c0');
    MA.ui.confirm({ title: t('extraction.delRow.title'), message: t('extraction.delRow.body', { n: String(pos.rowIndex + 1), label: label || '—' }),
      okLabel: t('extraction.delRow.ok'), danger: true }).then(function (ok) {
      if (!ok || !G.row(r.uid)) { return; }
      d.history.push({ type: 'rows-delete', dataset: d.dataset, index: pos.rowIndex, rows: [{ row_uid: r.uid, cells: Object.assign({}, r.cells) }] });
      G.deleteRows([r.uid]);
      touch(d);
      refreshStatus();
      scheduleValidate();
    });
  }

  function pasteDialog() {
    var ta = h('textarea', { 'class': 'ex-paste-area', rows: '8', id: 'ex-paste-text', spellcheck: 'false', 'aria-describedby': 'ex-paste-help' });
    MA.ui.modal({
      title: t('extraction.paste.title'),
      body: [h('p', { id: 'ex-paste-help', 'class': 'muted' }, t('extraction.paste.help')), ta],
      actions: [
        { label: t('common.cancel'), kind: 'ghost' },
        { label: t('extraction.paste.ok'), kind: 'primary', onClick: function () {
          if (!G.active() && G.rows().length) { G.focusCell(G.rows()[0].uid, 'c0'); }
          var r = G.paste(ta.value);
          if (r) { MA.ui.toast({ kind: 'success', title: t('extraction.paste.done', { rows: String(r.rows), changed: String(r.changed), added: String(r.inserted) }) }); }
        } }
      ]
    });
  }

  function undo() { var d = st(); if (d && d.history.undo()) { afterHistory(); } }
  function redo() { var d = st(); if (d && d.history.redo()) { afterHistory(); } }

  // ---------------------------------------------------------------- „Nem hiba — indoklás” (naplózott döntés)
  function acknowledge(f) {
    var d = st();
    var blocking = !!(f.blocking || f.severity === 'error');
    var ta = h('textarea', { id: 'ex-ack-rationale', rows: '4', 'aria-required': 'true',
      'aria-describedby': blocking ? 'ex-ack-blocking ex-ack-help' : 'ex-ack-help' });
    var errLine = h('p', { 'class': 'ex-cf-err', role: 'alert', hidden: true }, t('extraction.ack.required'));
    MA.ui.modal({
      title: t('extraction.ack.title', { code: f.code }),
      body: [
        h('p', null, MA.ui.badge(f.severity === 'error' ? 'error' : (f.severity === 'warning' ? 'warning' : 'info'), f.code), ' ',
          h('strong', null, findingTitle(f)), f.study ? ' — ' + f.study : ''),
        f.detail ? h('p', { 'class': 'muted' }, MA.i18n.pick(f.detail, '')) : null,
        f.advice ? h('p', { 'class': 'ex-ack-advice' }, h('strong', null, t('extraction.ack.advice') + ' '), MA.i18n.pick(f.advice, '')) : null,
        // a sort kizáró hibánál az indoklás nem változtat az elemzésen — ezt ki kell mondani (UX-10)
        blocking ? h('p', { 'class': 'ex-ack-blocking', id: 'ex-ack-blocking', role: 'note' },
          MA.ui.badge('blocker', t('extraction.f.blocking')), ' ', t('extraction.ack.blocking')) : null,
        h('p', null, MA.why.button({ kb: [f.kb_id || f.code], code: f.code, title: findingTitle(f), detail: f.detail, advice: f.advice })),
        h('label', { htmlFor: 'ex-ack-rationale' }, t('extraction.ack.rationale')),
        ta,
        h('p', { id: 'ex-ack-help', 'class': 'muted' }, t('extraction.ack.help')),
        errLine
      ],
      actions: [
        { label: t('common.cancel'), kind: 'ghost' },
        { label: t('extraction.ack.submit'), kind: 'primary', onClick: function () {
          var why = ta.value.trim();
          if (!why) { ta.setAttribute('aria-invalid', 'true'); errLine.hidden = false; ta.focus(); return false; }
          var uids = findingUids(f, d.uids || []);
          MA.api.post('/api/log/decision', {
            agent: 'user',
            decision: t('extraction.ack.decision', { code: f.code, study: f.study || '—' }),
            rationale: why,
            kb_refs: [f.kb_id || f.code],
            stage: null,
            context: { kind: 'validation', dataset: d.dataset, row_uid: uids[0] || null, code: f.code, fields: f.fields || [] }
          }).then(function (env) {
            var id = env.data && (env.data.id || env.data.decision_id);
            f.acknowledged = typeof id === 'number' ? id : true;
            MA.ui.toast({ kind: 'success', title: t('extraction.ack.done', { id: id === undefined ? '?' : String(id) }) });
            applyValidation();
            scheduleValidate();
          }, function () { return null; });
          return true;
        } }
      ]
    });
  }

  // ---------------------------------------------------------------- nézet: fejléc, összesítő, figyelmeztetések
  function refreshToolbar() {
    var d = st();
    if (!V || !V.els.undo || !d) { return; }
    V.els.undo.disabled = !d.history.canUndo();
    V.els.redo.disabled = !d.history.canRedo();
    V.els.save.disabled = !dirty(d) || !!d.saving;
  }

  function refreshStatus() {
    var d = st();
    if (!V || !V.els.status || !d) { return; }
    var state = d.saving ? MA.ui.badge('progress', t('extraction.saving'))
      : (dirty(d) ? MA.ui.badge('warning', t('extraction.unsaved'), { symbol: '●' })
        : MA.ui.badge('ok', d.savedAt ? t('extraction.savedAt', { time: d.savedAt }) : t('extraction.loaded')));
    var sha = cleanEtag(d.etag);
    MA.dom.mount(V.els.status, h('code', { 'class': 'ex-path' }, d.dataset), ' ', state,
      sha ? h('span', { 'class': 'muted ex-sha', title: sha }, ' · sha ' + sha.slice(0, 4) + '…') : null);
    refreshToolbar();
  }

  function renderSummary() {
    var d = st();
    if (!V || !V.els.summary || !d) { return; }
    var v = d.validation;
    var nodes = [h('span', { 'class': 'ex-sum-label' }, d.validationMs === null || d.validationMs === undefined
      ? t('extraction.sum.label') : t('extraction.sum.labelMs', { ms: String(d.validationMs) }))];
    if (V.validationError) {
      nodes.push(MA.ui.errorBox(V.validationError));
    } else if (v) {
      var s = v.summary || {};
      nodes.push(MA.ui.badge('error', t('extraction.sum.errors', { n: String(s.error || 0) })),
        MA.ui.badge('warning', t('extraction.sum.warnings', { n: String(s.warning || 0) })),
        MA.ui.badge('info', t('extraction.sum.infos', { n: String(s.info || 0) })),
        h('span', { 'class': 'ex-k' }, t('extraction.sum.k', { k: typeof v.k_analysable === 'number' ? String(v.k_analysable) : '—', n: String((d.uids || []).length) })));
      if (dirty(d)) { nodes.push(h('span', { 'class': 'muted' }, t('extraction.sum.draft'))); }
    }
    if (V.validating) { nodes.push(MA.ui.spinner('extraction.sum.running')); }
    MA.dom.mount(V.els.summary, nodes);
  }

  function renderAlerts() {
    var d = st();
    if (!V || !V.els.alerts || !d) { return; }
    var out = [];
    // a pillanatképben mindig látszik, hogy a tábla nem szerkeszthető (UX-16); a többi riasztás nem írja felül
    if (readOnly()) {
      out.push(h('p', { 'class': 'ex-readonly muted', id: 'ex-readonly', role: 'note' }, MA.ui.badge('info', t('extraction.readOnlyTitle')), ' ', t('extraction.readOnly')));
    }
    if (d.locked) {
      out.push(h('div', { 'class': 'ex-alert is-error', id: 'ex-locked', role: 'alert' },
        MA.ui.badge('error', t('extraction.locked.title')),
        h('span', null, ' ' + (d.locked.message || t('err.LOCKED')) + ' '),
        h('button', { type: 'button', 'class': 'btn btn-sm btn-primary', id: 'ex-locked-retry', onclick: function () { save(); } }, t('extraction.locked.retry'))));
    }
    if (d.hold) {
      var cls = MA.store.get('project.data_class');
      out.push(h('div', { 'class': 'ex-alert is-error', id: 'ex-hold', role: 'alert' },
        MA.ui.badge('blocker', t('extraction.hold.title')),
        h('span', null, ' ' + (d.hold.message || '') + ' '),
        h('a', { 'class': 'btn btn-sm', href: MA.app.href('project') }, t('extraction.hold.privacy')),
        cls === 'B' ? h('button', { type: 'button', 'class': 'btn btn-sm btn-danger', id: 'ex-hold-consent', onclick: function () {
          MA.ui.confirm({ title: t('extraction.hold.consentTitle'), message: t('extraction.hold.consentBody', { path: d.dataset }), okLabel: t('extraction.hold.consent'), danger: true })
            .then(function (ok) { if (ok) { save({ consent: true }); } });
        } }, t('extraction.hold.consent')) : null));
    }
    if (d.external) {
      out.push(h('div', { 'class': 'ex-alert is-warning', id: 'ex-external' },
        MA.ui.badge('stale', t('extraction.external.title')),
        h('span', null, ' ' + t(dirty(d) ? 'extraction.external.dirty' : 'extraction.external.clean', { path: d.dataset }) + ' '),
        dirty(d) ? null : h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-external-reload', onclick: reloadDataset }, t('extraction.external.reload')),
        h('button', { type: 'button', 'class': 'btn btn-sm btn-ghost', onclick: function () { d.external = false; renderAlerts(); } }, t('extraction.external.dismiss'))));
    }
    if (d.provState && d.provState.in_sync === false) {
      out.push(h('div', { 'class': 'ex-alert is-warning', id: 'ex-prov-sync' }, MA.ui.badge('warning', 'X022'), ' ', t('extraction.prov.outOfSync')));
    }
    MA.dom.mount(V.els.alerts, out);
  }

  // ---------------------------------------------------------------- megállapítások panel
  function jumpTo(f) {
    var d = st();
    var uids = findingUids(f, d.uids || []);
    if (!uids.length || !G.row(uids[0])) { return; }
    var cols = (f.fields || []).map(function (x) { return colOf(d, x); }).filter(function (c) { return !!c; });
    if (V.problem && !V.problem[uids[0]] && MA.prefs.get('extraction.onlyProblems', '0') === '1') {
      MA.prefs.set('extraction.onlyProblems', '0');
      if (V.els.only) { V.els.only.checked = false; }
      applyFilter();
    }
    G.focusCell(uids[0], cols[0] || 'c0');
  }

  function findingItem(f) {
    var d = st();
    var sev = SEV.indexOf(f.severity) >= 0 ? f.severity : 'info';
    var uids = findingUids(f, d.uids || []);
    var fields = (f.fields || []).join(', ');
    return h('li', { 'class': ['ex-f', 'is-' + sev, f.acknowledged && 'is-ack', f.blocking && 'is-blocking'], dataset: { code: f.code, uid: uids[0] || '' } },
      h('div', { 'class': 'ex-f-head' },
        MA.ui.badge(sev, f.code, { srLabel: t('extraction.sev.' + sev) }),
        f.study ? h('span', { 'class': 'ex-f-study' }, f.study) : h('span', { 'class': 'ex-f-study muted' }, t('extraction.f.tableLevel')),
        fields ? h('span', { 'class': 'ex-f-fields' }, fields) : null,
        f.blocking ? MA.ui.badge('blocker', t('extraction.f.blocking')) : null,
        f.acknowledged ? MA.ui.badge('ok', t('extraction.f.acknowledged', { id: typeof f.acknowledged === 'number' ? String(f.acknowledged) : '' })) : null),
      h('div', { 'class': 'ex-f-title' }, findingTitle(f)),
      f.detail ? h('div', { 'class': 'ex-f-detail' }, MA.i18n.pick(f.detail, '')) : null,
      f.advice ? h('div', { 'class': 'ex-f-advice muted' }, MA.i18n.pick(f.advice, '') + (f.source && f.source !== 'engine' ? ' (' + f.source + ')' : '')) : null,
      h('div', { 'class': 'ex-f-actions' },
        uids.length ? h('button', { type: 'button', 'class': 'btn btn-sm ex-f-jump', onclick: function () { jumpTo(f); } }, t('extraction.f.jump')) : null,
        f.acknowledged || readOnly() ? null : h('button', { type: 'button', 'class': 'btn btn-sm ex-f-ack', onclick: function () { acknowledge(f); },
          title: f.blocking || f.severity === 'error' ? t('extraction.ack.blockingTitle') : null }, t('extraction.f.ack')),
        MA.why.button({ kb: [f.kb_id || f.code], code: f.code, title: findingTitle(f), detail: f.detail, advice: f.advice }, { compact: true }),
        f.code === 'V018' ? h('a', { 'class': 'btn btn-sm', href: MA.app.href('analysis', { outcome: d.outcome, exclude: 'estimated=igen' }) }, t('extraction.f.sensitivity')) : null,
        h('a', { 'class': 'btn btn-sm btn-ghost', href: MA.app.href('log', { kb: f.kb_id || f.code }) }, t('extraction.f.kb', { code: f.kb_id || f.code }))));
  }

  function renderFindings() {
    var d = st();
    if (!V || !V.els.findings || !d) { return; }
    var list = (d.validation && d.validation.findings) || [];
    if (!d.validation) {
      // a validálás hibája a fenti összesítőben látszik; itt ne maradjon „validálás …” jelző
      MA.dom.mount(V.els.findings, V.validationError && !V.validating ? MA.ui.emptyState('extraction.f.unavailable') : MA.ui.spinner('extraction.sum.running'));
      return;
    }
    if (!list.length) { MA.dom.mount(V.els.findings, MA.ui.emptyState('extraction.f.none')); return; }
    MA.dom.mount(V.els.findings, SEV.map(function (sev) {
      var items = list.filter(function (f) { return (SEV.indexOf(f.severity) >= 0 ? f.severity : 'info') === sev; });
      if (!items.length) { return null; }
      var hid = 'ex-fg-' + sev;
      return h('section', { 'class': 'ex-fg', 'aria-labelledby': hid, dataset: { severity: sev } },
        h('h3', { id: hid, 'class': 'ex-fg-title' }, h('span', { 'aria-hidden': 'true' }, MA.ui.symbol(sev) + ' '), t('extraction.fg.' + sev, { n: String(items.length) })),
        h('ul', { 'class': 'ex-f-list' }, items.map(findingItem)));
    }));
  }

  // ---------------------------------------------------------------- eredet-panel (4.8)
  function docOptions(d, selected) {
    var docs = (d.docs && d.docs.docs) || [];
    return [h('option', { value: '' }, t('extraction.prov.noDoc'))].concat(docs.map(function (x) {
      return h('option', { value: x.id, selected: x.id === selected }, x.id + (x.title ? ' — ' + MA.i18n.pick(x.title, '') : ''));
    }));
  }

  function renderProv() {
    var d = st();
    if (!V || !V.els.prov || !d || !G) { return; }
    var pos = G.active();
    var body = V.els.provBody;
    if (!pos || pos.col === SRC_COL) { MA.dom.mount(body, MA.ui.emptyState('extraction.prov.pick')); V.els.provTitle.textContent = t('extraction.prov.title'); return; }
    var field = fieldOf(d, pos.col);
    var label = G.get(pos.uid, colOf(d, 'study') || 'c0');
    var colName = d.header[Number(pos.col.slice(1))];
    V.els.provTitle.textContent = t('extraction.prov.titleCell', { study: label || '—', col: colName === field ? field : colName + ' (' + field + ')' });
    var p = findProv(d, pos.uid, field);
    var src = (p && p.source) || {};
    var value = G.get(pos.uid, pos.col);
    var info = [
      h('p', { 'class': 'ex-prov-line' }, t('extraction.prov.value', { value: value || '—' }),
        p && p.value_as_entered !== null && p.value_as_entered !== undefined ? ' ' + t('extraction.prov.entered', { value: p.value_as_entered }) : '',
        ' · ', t('extraction.prov.methodLabel'), ' ', h('strong', null, p ? t('extraction.prov.method.' + (METHODS.indexOf(p.method) >= 0 ? p.method : 'reported')) : t('extraction.prov.none')),
        p && EST[p.method] ? [' ', MA.ui.badge('estimated', t('extraction.prov.estimated'))] : null),
      p && p.conversion ? h('p', { 'class': 'ex-prov-line muted' }, t('extraction.prov.conversion', {
        kind: str(p.conversion.kind || (p.conversion.request && p.conversion.request.kind)),
        fn: str(p.conversion.method && p.conversion.method['function'] ? p.conversion.method['function'] : (p.conversion.result && p.conversion.result.method ? p.conversion.result.method['function'] : '')),
        engine: str(p.conversion.engine_version) })) : null,
      src.doc || src.page || src.locator || src.quote ? h('p', { 'class': 'ex-prov-line' },
        t('extraction.prov.source'), ' ', src.doc || '—',
        src.page ? ' · ' + t('extraction.prov.page', { page: String(src.page) }) : '',
        src.locator ? ' · ' + src.locator : '',
        src.quote ? h('q', { 'class': 'ex-quote' }, src.quote) : null) : null,
      h('p', { 'class': 'ex-prov-line' },
        src.doc ? h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-open-source', onclick: function () {
          MA.api.openFile({ doc: src.doc, page: src.page || null }).then(null, function () { return null; });
        } }, src.page ? t('extraction.prov.openPage', { page: String(src.page) }) : t('extraction.prov.openDoc')) : null,
        p && p.extracted_by ? h('span', { 'class': 'muted' }, ' ' + t('extraction.prov.by', { who: p.extracted_by, when: MA.i18n.ts(p.extracted_at) })) : null,
        p && p.verified_by ? [' ', MA.ui.badge('ok', t('extraction.prov.verifiedBy', { who: p.verified_by }))] : null),
      p && p.history && p.history.length ? h('details', { 'class': 'ex-prov-hist' },
        h('summary', null, t('extraction.prov.history', { n: String(p.history.length) })),
        h('ol', null, p.history.slice().reverse().map(function (x) {
          return h('li', null, (x.value_as_entered === null || x.value_as_entered === undefined ? '—' : '„' + x.value_as_entered + '”') + ' · ' +
            t('extraction.prov.method.' + (METHODS.indexOf(x.method) >= 0 ? x.method : 'reported')) +
            (x.source && x.source.page ? ' · ' + t('extraction.prov.page', { page: String(x.source.page) }) : '') +
            (x.extracted_by ? ' · ' + x.extracted_by : '') + (x.extracted_at ? ' ' + MA.i18n.ts(x.extracted_at) : ''));
        }))) : null
    ];
    // szerkesztő űrlap
    var method = h('select', { id: 'ex-prov-method' }, METHODS.map(function (m) {
      return h('option', { value: m, selected: (p ? p.method : 'reported') === m }, t('extraction.prov.method.' + m));
    }));
    var doc = h('select', { id: 'ex-prov-doc' }, docOptions(d, src.doc || ''));
    var page = h('input', { type: 'text', id: 'ex-prov-page', inputmode: 'numeric', 'class': 'ex-in-short', value: src.page ? String(src.page) : '' });
    var loc = h('input', { type: 'text', id: 'ex-prov-locator', value: src.locator || '', placeholder: 'Table 2 / Fig. 3B / Suppl. S4' });
    var quote = h('textarea', { id: 'ex-prov-quote', rows: '2', maxlength: '500', value: src.quote || '' });
    var verified = h('input', { type: 'checkbox', id: 'ex-prov-verified', checked: !!(p && p.verified_by) });
    var err = h('p', { 'class': 'ex-cf-err', role: 'alert', hidden: true }, t('extraction.prov.badPage'));
    function fld(id, key, ctl) { return h('div', { 'class': 'ex-fld' }, h('label', { htmlFor: id }, t(key)), ctl); }
    var form = h('form', { 'class': 'ex-prov-form', id: 'ex-prov-form', onsubmit: function (ev) {
      ev.preventDefault();
      var pg = page.value.trim();
      if (pg && !/^\d{1,6}$/.test(pg)) { page.setAttribute('aria-invalid', 'true'); err.hidden = false; page.focus(); return; }
      var entry = {
        row_uid: pos.uid, field: field, value_as_entered: value, method: method.value, estimated: !!EST[method.value],
        source: { doc: doc.value || null, page: pg ? Number(pg) : null, locator: loc.value.trim() || null, quote: quote.value.trim() || null },
        conversion: p && p.conversion ? p.conversion : null,
        reconciliation: p && p.reconciliation ? p.reconciliation : null,
        extracted_by: p && p.extracted_by ? p.extracted_by : actor(),
        extracted_at: p && p.extracted_at ? p.extracted_at : nowIso(),
        verified_by: verified.checked ? (p && p.verified_by ? p.verified_by : actor()) : null,
        verified_at: verified.checked ? (p && p.verified_at ? p.verified_at : nowIso()) : null
      };
      setProv(d, pos.uid, field, withHistory(p, entry));
      save();
    } },
    h('div', { 'class': 'ex-prov-grid' },
      fld('ex-prov-method', 'extraction.prov.f.method', method),
      fld('ex-prov-doc', 'extraction.prov.f.doc', doc),
      fld('ex-prov-page', 'extraction.prov.f.page', page),
      fld('ex-prov-locator', 'extraction.prov.f.locator', loc)),
    fld('ex-prov-quote', 'extraction.prov.f.quote', quote),
    h('div', { 'class': 'row' },
      h('label', { 'class': 'ex-check' }, verified, ' ', t('extraction.prov.f.verified')),
      h('span', { 'class': 'hdr-spacer' }),
      p ? h('button', { type: 'button', 'class': 'btn btn-sm btn-ghost', id: 'ex-prov-remove', onclick: function () {
        setProv(d, pos.uid, field, { row_uid: pos.uid, field: field, __absent: true });
        save();
      } }, t('extraction.prov.remove')) : null,
      h('button', { type: 'submit', 'class': 'btn btn-sm btn-primary', id: 'ex-prov-save' }, t('extraction.prov.save'))),
    err);
    // a pillanatképben az eredet csak olvasható: az űrlap nem jelenik meg (UX-16)
    MA.dom.mount(body, info, readOnly() ? null : form);
  }

  function setProv(d, uid, field, entry) {
    var prev = findProv(d, uid, field);
    var before = prev ? clone(prev) : { row_uid: uid, field: field, __absent: true };
    putProv(d, entry);
    d.provKeys[uid + '|' + field] = true;
    d.history.push({ type: 'custom', 'do': { prov: [clone(entry)] }, undo: { prov: [before] } });
    touch(d, true);
    refreshSrc(uid);
    redecorate();
    refreshStatus();
  }

  function refreshSrc(uid) { var d = st(); if (G && d && G.row(uid)) { G.set(uid, SRC_COL, sourceSummary(d, uid)); } }
  function refreshSrcAll() { var d = st(); if (G && d) { G.rows().forEach(function (r) { G.set(r.uid, SRC_COL, sourceSummary(d, r.uid)); }); } }

  function openRowSource(pos) {
    var d = st();
    var list = (d.prov.cells || []).filter(function (p) { return p.row_uid === pos.uid && p.source && p.source.doc; });
    if (!list.length) { MA.ui.toast({ kind: 'info', title: t('extraction.prov.noSource') }); return; }
    MA.api.openFile({ doc: list[0].source.doc, page: list[0].source.page || null }).then(null, function () { return null; });
  }

  // ---------------------------------------------------------------- dokumentum-jegyzék (documents.json)
  function documentsDialog() {
    var d = st();
    var tbody = h('tbody');
    function fill() {
      var docs = (d.docs && d.docs.docs) || [];
      MA.dom.mount(tbody, docs.length ? docs.map(function (x) {
        return h('tr', null, h('th', { scope: 'row' }, h('code', null, x.id)), h('td', null, x.title ? MA.i18n.pick(x.title, '') : '—'),
          h('td', null, x.root || '—'), h('td', null, h('code', null, x.path || '—')), h('td', { 'class': 'num' }, x.pages === null || x.pages === undefined ? '—' : String(x.pages)));
      }) : h('tr', null, h('td', { colspan: '5' }, MA.ui.emptyState('extraction.docs.none'))));
    }
    fill();
    var id = h('input', { type: 'text', id: 'ex-doc-id', placeholder: 'pmid:31234567 · doi:10.… · file:…' });
    var root = h('select', { id: 'ex-doc-root' }, h('option', { value: 'project' }, 'project'), h('option', { value: 'composer_outdir' }, 'composer_outdir'));
    var path = h('input', { type: 'text', id: 'ex-doc-path', placeholder: '_privat/pdf/…' });
    var pages = h('input', { type: 'text', id: 'ex-doc-pages', inputmode: 'numeric', 'class': 'ex-in-short' });
    var err = h('p', { 'class': 'ex-cf-err', role: 'alert', hidden: true });
    function fld(fid, key, ctl) { return h('div', { 'class': 'ex-fld' }, h('label', { htmlFor: fid }, t(key)), ctl); }
    var form = h('form', { 'class': 'ex-doc-form', onsubmit: function (ev) {
      ev.preventDefault();
      var did = id.value.trim(), pth = path.value.trim(), pg = pages.value.trim();
      var problem = !/^[a-z][a-z0-9_-]*:\S/.test(did) ? 'extraction.docs.badId'
        : (!pth || /^\/|\.\.|\\/.test(pth) ? 'extraction.docs.badPath' : (pg && !/^\d{1,6}$/.test(pg) ? 'extraction.docs.badPages'
          : (((d.docs && d.docs.docs) || []).some(function (x) { return x.id === did; }) ? 'extraction.docs.dupId' : null)));
      if (problem) { err.textContent = t(problem); err.hidden = false; return; }
      err.hidden = true;
      var next = clone(d.docs) || { schema: DOCS_SCHEMA, docs: [] };
      next.schema = DOCS_SCHEMA;
      next.docs = (next.docs || []).concat([{ id: did, root: root.value, path: pth, sha256: null, pages: pg ? Number(pg) : null }]);
      MA.api.put('/api/documents', next, { ifMatch: d.docsEtag || undefined }).then(function (env) {
        d.docs = env.data && (Array.isArray(env.data.docs) || env.data.documents) ? docsFrom(env) : next;
        d.docsEtag = env.etag || d.docsEtag;
        id.value = ''; path.value = ''; pages.value = '';
        fill();
        renderProv();
        MA.ui.toast({ kind: 'success', title: t('extraction.docs.added', { id: did }) });
      }, function (e) {
        if (e.code === 'CONFLICT') {
          MA.api.get('/api/documents', { toast: false }).then(function (env) { d.docs = docsFrom(env); d.docsEtag = env.etag || null; fill(); }, function () { return null; });
        }
      });
    } },
    h('div', { 'class': 'ex-prov-grid' }, fld('ex-doc-id', 'extraction.docs.id', id), fld('ex-doc-root', 'extraction.docs.root', root),
      fld('ex-doc-path', 'extraction.docs.path', path), fld('ex-doc-pages', 'extraction.docs.pages', pages)),
    h('div', { 'class': 'row' }, h('button', { type: 'submit', 'class': 'btn btn-primary btn-sm', id: 'ex-doc-add' }, t('extraction.docs.add'))), err);
    var m = MA.ui.modal({
      title: t('extraction.docs.title'),
      size: 'lg',
      body: [h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: 'ex-docs-table' },
        h('thead', null, h('tr', null, ['id', 'titleCol', 'root', 'path', 'pages'].map(function (k) { return h('th', { scope: 'col' }, t('extraction.docs.' + k)); }))), tbody)),
        h('h3', { 'class': 'ex-sub' }, t('extraction.docs.new')), form],
      actions: [{ label: t('common.close'), kind: 'primary' }]
    });
    m.el.id = 'ex-docs';
  }

  // ---------------------------------------------------------------- átváltó (3.5.4; screens/convert.js)
  function openConvert() {
    var d = st();
    var pos = G ? G.active() : null;
    if (!pos) { MA.ui.toast({ kind: 'info', title: t('extraction.convert.noRow') }); return; }
    var p = findProv(d, pos.uid, fieldOf(d, pos.col));
    var src = p && p.source ? p.source : ((d.prov.cells || []).filter(function (x) { return x.row_uid === pos.uid && x.source; })[0] || {}).source || {};
    var values = {};
    d.header.forEach(function (x, i) { values[fieldOf(d, 'c' + i)] = G.get(pos.uid, 'c' + i); });
    MA.convertDialog.open({
      dataset: d.dataset,
      rowUid: pos.uid,
      study: G.get(pos.uid, colOf(d, 'study') || 'c0'),
      columns: d.header.map(function (name, i) { return { id: 'c' + i, label: name, field: fieldOf(d, 'c' + i) }; }),
      activeField: fieldOf(d, pos.col),
      values: values,
      decimalMark: d.format && d.format.decimal_mark ? d.format.decimal_mark : null,
      documents: (d.docs && d.docs.docs) || [],
      source: { doc: src.doc || null, page: src.page || null, locator: src.locator || null, quote: null },
      onApply: applyConversion
    });
  }

  /** A motor eredményének beírása egy tranzakcióban: cellák + sor estimated + eredet (+ a szerver activity-je). */
  function applyConversion(res) {
    var d = st();
    if (!d || !G) { return Promise.resolve(false); }
    var uid = res.rowUid;
    if (!G.row(uid)) { return Promise.resolve(false); }
    var r = res.result || {};
    var method = r.estimated ? 'estimated' : 'calculated';
    var ops = [];
    var writes = res.writes.slice();
    var estCol = colOf(d, 'estimated');
    if (r.estimated && estCol && G.get(uid, estCol) !== YES) { writes.push({ col: estCol, text: YES, noProv: true }); }
    var before = [], after = [];
    writes.forEach(function (w) {
      var old = G.get(uid, w.col);
      if (old !== w.text) {
        G.set(uid, w.col, w.text);
        ops.push({ type: 'cell', dataset: d.dataset, row_uid: uid, field: w.col, before: old, after: w.text });
      }
      if (w.noProv) { return; }
      var field = fieldOf(d, w.col);
      var prev = findProv(d, uid, field);
      before.push(prev ? clone(prev) : { row_uid: uid, field: field, __absent: true });
      var entry = withHistory(prev, {
        row_uid: uid, field: field, value_as_entered: w.text, method: method, estimated: !!EST[method],
        source: res.source || null,
        conversion: { kind: r.kind || res.request.kind, request: res.request, outputs: r.outputs || null, outputs_text: r.outputs_text || null,
          estimated: !!r.estimated, method: r.method || null, assumptions: r.assumptions || [], warnings: r.warnings || [], engine_version: r.engine_version || null },
        reconciliation: null, extracted_by: actor(), extracted_at: nowIso(), verified_by: null
      });
      putProv(d, entry);
      d.provKeys[uid + '|' + field] = true;
      after.push(clone(entry));
    });
    if (after.length) { ops.push({ type: 'custom', 'do': { prov: after }, undo: { prov: before } }); touch(d, true); }
    if (ops.some(function (o) { return o.type === 'cell'; })) { touch(d); }
    if (ops.length) { d.history.push({ type: 'batch', label: 'convert', ops: ops }); }
    refreshSrc(uid);
    redecorate();
    refreshStatus();
    renderProv();
    scheduleValidate();
    return save();
  }

  // ---------------------------------------------------------------- képernyő
  function columnsFor(d) {
    var num = requiredNumeric(d);
    var cols = d.header.map(function (name, i) {
      var id = 'c' + i;
      var canon = fieldOf(d, id);
      return { id: id, label: name, hint: canon !== name ? canon : null, frozen: i === 0, width: i === 0 ? 13 : null, numeric: !!num[id] };
    });
    cols.push({ id: SRC_COL, label: t('extraction.col.source'), readonly: true, title: t('extraction.col.sourceTitle') });
    return cols;
  }

  function guardLeave(reason) {
    var d = st();
    if (readOnly()) { if (d) { setDraft(null); } return Promise.resolve(true); }
    if (!dirty(d)) { if (d) { setDraft(null); } return Promise.resolve(true); }
    return new Promise(function (resolve) {
      var answered = false;
      function done(v) { answered = true; resolve(v); }
      MA.ui.modal({
        title: t('extraction.leave.title'),
        size: 'sm',
        body: h('p', null, t('extraction.leave.body')),
        actions: [
          { label: t('extraction.leave.stay'), kind: 'ghost', onClick: function () { done(false); } },
          { label: t('extraction.leave.discard'), kind: 'danger', onClick: function () { setDraft(null); done(true); } },
          { label: t('extraction.leave.save'), kind: 'primary', onClick: function () {
            answered = true;
            save().then(function (ok) { if (ok) { setDraft(null); } resolve(!!ok); });
          } }
        ],
        onClose: function () { if (!answered) { resolve(false); } }
      });
    });
  }

  /** Mentés fókuszvesztéskor (3.5.3): a munkaterületet elhagyva, vagy ha az ablak elveszti a fókuszt (pl. Excel). */
  function scheduleAutosave(windowBlur) {
    if (!V) { return; }
    clearTimeout(V.autosave);
    V.autosave = setTimeout(function () {
      var d = st();
      if (!V || !d || !dirty(d) || d.saving || d.locked || d.hold || document.querySelector('.modal-backdrop')) { return; }
      if (!windowBlur && V.work && V.work.contains(document.activeElement)) { return; }
      save();
    }, AUTOSAVE_MS);
  }

  function onKey(ev) {
    if (!V || !G) { return; }
    var ctrl = ev.ctrlKey || ev.metaKey;
    if (document.querySelector('.modal-backdrop')) { return; }
    if (ctrl && !ev.altKey && (ev.key === 's' || ev.key === 'S')) { ev.preventDefault(); save(); return; }
    if (ev.altKey && !ctrl && ev.code === 'KeyP') { ev.preventDefault(); toggleProv(); return; }
    if (ctrl && !ev.altKey && (ev.key === 'z' || ev.key === 'Z' || ev.key === 'y' || ev.key === 'Y')) {
      if (MA.dom.isTyping(ev.target) || !V.root.contains(ev.target)) { return; }
      ev.preventDefault();
      if (ev.key === 'y' || ev.key === 'Y' || ev.shiftKey) { redo(); } else { undo(); }
    }
  }

  function toggleProv(force) {
    var on = force === undefined ? MA.prefs.get('extraction.prov', '1') !== '1' : !!force;
    MA.prefs.set('extraction.prov', on ? '1' : '0');
    V.els.prov.hidden = !on;
    V.els.provBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    if (on) { renderProv(); }
  }

  function switchOutcome(target) {
    var d = st();
    guardLeave('navigate').then(function (ok) {
      if (ok) { V.ctx.navigate('extraction', target.id ? { outcome: target.id } : { dataset: target.dataset }); } else if (V && V.els.outcome && d) { V.els.outcome.value = d.dataset; }
    });
  }

  function build(root, ctx, d, targets) {
    var engine = MA.store.get('engine') || {};
    var measures = Array.isArray(engine.measures) ? engine.measures : [];
    var els = V.els;
    els.outcome = h('select', { id: 'ex-outcome', onchange: function () {
      switchOutcome(targets.filter(function (o) { return o.dataset === els.outcome.value; })[0]);
    } }, targets.map(function (o) {
      return h('option', { value: o.dataset, selected: o.dataset === d.dataset }, (o.id ? o.id + ' · ' : '') + MA.i18n.pick(o.name, o.dataset));
    }));
    els.measure = h('select', { id: 'ex-measure', onchange: function () {
      d.measure = els.measure.value;
      ctx.setParams(Object.assign({}, ctx.params, { outcome: d.outcome || undefined, measure: d.measure }));
      d.validation = null;
      V.colsMeta = false;
      validateNow();
    } }, (measures.length ? measures : [{ id: d.measure, label: d.measure }]).map(function (m) {
      return h('option', { value: m.id, selected: m.id === d.measure }, m.id + ' — ' + MA.i18n.pick(m.label, m.id));
    }));
    els.status = h('span', { 'class': 'ex-status', id: 'ex-status', 'aria-live': 'polite' });
    els.undo = h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-undo', onclick: undo, title: 'Ctrl+Z' }, '↶ ', t('extraction.tb.undo'));
    els.redo = h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-redo', onclick: redo, title: 'Ctrl+Y' }, '↷ ', t('extraction.tb.redo'));
    els.save = h('button', { type: 'button', 'class': 'btn btn-sm btn-primary', id: 'ex-save', onclick: function () { save(); } }, t('extraction.tb.save'));
    els.only = h('input', { type: 'checkbox', id: 'ex-only', checked: MA.prefs.get('extraction.onlyProblems', '0') === '1', onchange: function () {
      MA.prefs.set('extraction.onlyProblems', els.only.checked ? '1' : '0');
      applyFilter();
    } });
    els.provBtn = h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-prov-toggle', 'aria-pressed': 'false', 'aria-controls': 'ex-prov', onclick: function () { toggleProv(); } }, t('extraction.tb.prov'));
    els.count = h('span', { 'class': 'muted ex-count', 'aria-live': 'polite' });
    var dual = MA.app.screens().some(function (s) { return s.id === 'dual' && !s.placeholder; });
    var head = h('div', { 'class': 'panel ex-head' },
      h('div', { 'class': 'toolbar' },
        h('label', { htmlFor: 'ex-outcome' }, t('extraction.outcome')), els.outcome,
        h('label', { htmlFor: 'ex-measure' }, t('extraction.measure')), els.measure,
        els.status),
      h('div', { 'class': 'toolbar', role: 'toolbar', 'aria-label': t('extraction.tb.label') },
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-add-row', onclick: addRow }, t('extraction.tb.addRow')),
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-del-row', onclick: deleteRow }, t('extraction.tb.delRow')),
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-paste', onclick: pasteDialog }, t('extraction.tb.paste')),
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-convert', onclick: openConvert }, t('extraction.tb.convert')),
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ex-docs-btn', onclick: documentsDialog }, t('extraction.tb.docs')),
        dual ? h('a', { 'class': 'btn btn-sm', href: MA.app.href('dual', { outcome: d.outcome }) }, t('extraction.tb.dual')) : null,
        els.undo, els.redo, els.save,
        h('label', { 'class': 'ex-check', htmlFor: 'ex-only' }, els.only, ' ', t('extraction.tb.onlyProblems')),
        els.provBtn, els.count));
    els.alerts = h('div', { 'class': 'ex-alerts', id: 'ex-alerts', 'aria-live': 'polite' });
    els.summary = h('div', { 'class': 'ex-summary', id: 'ex-summary', role: 'status', 'aria-live': 'polite' });

    G = MA.grid.create({
      label: t('extraction.grid.label', { dataset: d.dataset }),
      readOnly: readOnly(),
      columns: columnsFor(d),
      rows: d.rows.map(function (r) { var c = Object.assign({}, r.cells); c[SRC_COL] = sourceSummary(d, r.uid); return { uid: r.uid, cells: c }; }),
      newRow: newRow,
      onChange: onGridChange,
      onActive: function () { if (els.prov && !els.prov.hidden) { renderProv(); } },
      onActivate: function (pos) { if (pos.col === SRC_COL) { openRowSource(pos); } }
    });
    d.rows = G.rows();
    els.provTitle = h('h2', { 'class': 'panel-title', id: 'ex-prov-h' }, t('extraction.prov.title'));
    els.provBody = h('div', { 'class': 'ex-prov-body' });
    els.prov = h('section', { 'class': 'panel ex-prov', id: 'ex-prov', 'aria-labelledby': 'ex-prov-h' }, els.provTitle, els.provBody);
    els.findings = h('div', { 'class': 'ex-findings-body', id: 'ex-findings-body' });
    var work = h('div', { 'class': 'ex-main' },
      h('div', { 'class': 'ex-left' }, h('div', { 'class': 'panel ex-grid-panel' }, G.el,
        h('p', { 'class': 'muted ex-keys' }, t('extraction.keys'))), els.prov),
      h('section', { 'class': 'panel ex-findings', id: 'ex-findings', 'aria-labelledby': 'ex-findings-h' },
        h('h2', { 'class': 'panel-title', id: 'ex-findings-h' }, t('extraction.f.title')), els.findings));
    if (readOnly()) {
      // a pillanatképben semmi sem menthető: az író eszközök tiltva, a rács csak olvasható (UX-16)
      ['#ex-add-row', '#ex-del-row', '#ex-paste', '#ex-convert', '#ex-docs-btn'].forEach(function (sel) {
        var b = MA.dom.$(sel, head);
        if (b) { b.disabled = true; b.title = t('extraction.readOnly'); }
      });
      [els.undo, els.redo, els.save].forEach(function (b) { b.disabled = true; });
    }
    MA.dom.mount(root, head, els.alerts, els.summary, work);
    var provOn = MA.prefs.get('extraction.prov', '1') === '1';
    els.prov.hidden = !provOn;
    els.provBtn.setAttribute('aria-pressed', provOn ? 'true' : 'false');

    work.addEventListener('focusout', function (ev) {
      var to = ev.relatedTarget;
      if (to && (work.contains(to) || (to.closest && to.closest('.modal-backdrop')))) { return; }
      scheduleAutosave();
    });
    V.work = work;
    var onBlur = function () { scheduleAutosave(true); };
    window.addEventListener('blur', onBlur);
    document.addEventListener('keydown', onKey);
    var offChanges = MA.bus.on('changes', function (c) {
      var cur = st();
      if (!cur || !c || !Array.isArray(c.changes)) { return; }
      var hit = c.changes.some(function (x) {
        var ext = x.external === true || (x.actor && x.actor !== 'user' && String(x.actor).indexOf('user:') !== 0);
        return ext && (x.path === cur.dataset || x.path === cur.dataset.replace(/\.[^.\/]+$/, '') + '.prov.json');
      });
      if (hit) { cur.external = true; renderAlerts(); }
    });
    ctx.onCleanup(function () {
      window.removeEventListener('blur', onBlur);
      document.removeEventListener('keydown', onKey);
      offChanges();
      if (V) { clearTimeout(V.autosave); if (V.debounced) { V.debounced.cancel(); } }
      G = null;
      V = null;
    });

    if (G.rows().length > G.maxRows) { MA.ui.toast({ kind: 'warning', title: t('grid.tooManyRows', { max: String(G.maxRows) }) }); }
    refreshStatus();
    renderAlerts();
    renderProv();
    if (d.validation) { updateColumnMeta(d); applyValidation(); } else {
      renderSummary();
      renderFindings();
    }
    applyFilter();
    focusRowParam(ctx);
    return validateNow();
  }

  /** #/extraction?outcome=o1&row=<row_uid> — lefúrás az ábrákról (3.5.7): a sor első cellája kerül fókuszba
   *  (ha a „csak problémás sorok” szűrő elrejtené, a szűrő kikapcsol). */
  function focusRowParam(ctx) {
    var uid = ctx.params && ctx.params.row;
    if (!uid || !G || !G.row(uid)) { return; }
    if (V.problem && !V.problem[uid] && MA.prefs.get('extraction.onlyProblems', '0') === '1') {
      MA.prefs.set('extraction.onlyProblems', '0');
      if (V.els.only) { V.els.only.checked = false; }
      applyFilter();
    }
    G.focusCell(uid, 'c0');
  }

  function render(root, ctx) {
    var project = MA.store.get('project') || {};
    var targets = (Array.isArray(project.outcomes) ? project.outcomes : []).filter(function (o) { return o && typeof o.data === 'string'; })
      .map(function (o) { return { id: o.id, dataset: o.data, measure: o.measure || null, name: o.name || o.id }; });
    (Array.isArray(project.tables) ? project.tables : []).forEach(function (tb) {   // kimenethez nem kötött táblák (GET /api/project tables[])
      if (tb && typeof tb.dataset === 'string' && !targets.some(function (o) { return o.dataset === tb.dataset; })) {
        targets.push({ id: null, dataset: tb.dataset, measure: null, name: tb.dataset });
      }
    });
    var p = ctx.params || {};
    var target = null;
    var want = p.outcome || (p.dataset ? null : MA.prefs.get('extraction.outcome', null));
    targets.forEach(function (o) { if (!target && ((want && o.id === want) || (p.dataset && o.dataset === p.dataset))) { target = o; } });
    if (!target && p.dataset) { target = { id: null, dataset: p.dataset, measure: p.measure || null, name: p.dataset }; targets.push(target); }
    if (!target) { target = targets[0] || null; }
    if (!target) { root.appendChild(h('div', { 'class': 'panel' }, MA.ui.emptyState('extraction.noDatasets'))); return null; }
    target = Object.assign({}, target);
    if (p.measure) { target.measure = p.measure; }
    if (target.id) { MA.prefs.set('extraction.outcome', target.id); }
    ctx.setTitle(MA.i18n.pick(target.name, target.dataset));
    V = { ctx: ctx, root: root, run: MA.api.latest(), els: {}, validating: false, validationError: null, problem: {}, byUid: {} };
    V.debounced = MA.ui.debounce(function () { validateNow(); }, DEBOUNCE_MS);
    var inst = V;
    var d = st();
    var ready;
    if (d && d.dataset === target.dataset) {
      if (p.measure) { d.measure = p.measure; }
      ready = Promise.resolve(d);
    } else {
      root.appendChild(MA.ui.spinner('extraction.loading'));
      ready = load(target, ctx);
    }
    return ready.then(function (draft) {
      if (!ctx.alive() || V !== inst) { return null; }
      if (!draft.measure) { draft.measure = target.measure || 'RR'; }
      setDraft(draft);
      return build(root, ctx, draft, targets);
    });
  }

  MA.app.registerScreen({
    id: 'extraction',
    title_key: 'tab.extraction',
    workspace: 'extraction',
    tab: 'extraction',
    order: 10,
    render: render,
    onLeave: function (ctx, info) { return guardLeave(info && info.reason); }
  });

  MA.extraction = { mapFindings: mapFindings, save: save, current: st, grid: function () { return G; } };

  // ================================================================ önteszt (termékben is fut)
  MA.selftest.register('kinyerés: megállapítás → cella (row/rows/row_uid + column_map)', function (tt) {
    var v = {
      column_map: { study: 'vizsgálat', e1: 'esemény1', n1: 'n1' },
      findings: [
        { code: 'V006', severity: 'error', title: 'Érvénytelen eseményszám', study: 'B', row: 1, row_uid: null, fields: ['e1', 'n1'], detail: 'e1 > n1', blocking: true },
        { code: 'V007', severity: 'warning', title: 'Ismétlődő', study: 'A', row: 0, rows: [0, 1], fields: [], blocking: false },
        { code: 'V018', severity: 'info', title: 'Becsült', study: 'A', row: null, row_uid: 'ra0001', fields: ['e1'], blocking: false, acknowledged: 7 },
        { code: 'V015', severity: 'warning', title: 'Kevés', study: null, row: null, fields: [], blocking: false }
      ],
      excluded: [{ study: 'B', row: 1, reason: 'V006' }]
    };
    var cmap = { e1: 'c1', n1: 'c2', study: 'c0' };
    var m = mapFindings(v, ['ra0001', 'rb0002'], function (f) { return cmap[f] || null; });
    tt.deepEq(m.cells.rb0002.c1.kinds, ['error'], 'V006 → e1 cella hibás');
    tt.ok(!!m.cells.rb0002.c2, 'V006 → n1 cella is');
    tt.ok(m.rows.rb0002.blocked, 'blokkoló → a sor szürke');
    tt.deepEq(m.rows.ra0001.kinds, ['warning'], 'V007 (rows) mező nélkül → sorfejléc');
    tt.deepEq(m.cells.ra0001.c1.kinds, ['ack'], 'indokolt (acknowledged) → ack');
    tt.ok(m.problem.rb0002 && m.problem.ra0001, 'problémás sorok');
    tt.eq(Object.keys(m.cells).length, 2, 'táblaszintű (V015) nem kerül cellára');
  });
})();
