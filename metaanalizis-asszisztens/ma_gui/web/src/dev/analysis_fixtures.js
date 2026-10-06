/* dev/analysis_fixtures.js — CSAK a fejlesztői buildben: dinamikus fixture-ök az elemzés-képernyőkhöz.
 *
 * Végpontok (terv 3.4, 2.6; a szerver jobs.py viselkedését utánozza):
 *   GET  /api/specs/<név>     — a munkamenetben mentett spec, különben a JSON-fixture (analysis_specs.json)
 *   PUT  /api/specs/<név>     — mentés If-Match-csel (eltérő ETag → 409 CONFLICT), új ETag
 *   POST /api/analyze         — explore (last-wins: a régebbi futó explore 'superseded') / commit (FIFO)
 *   GET  /api/jobs/<id>       — explore: 1. lekérdezésre kész; commit: queued → running → done
 *   GET  /api/runs/<id>/plot  — a munkamenetben rögzített futás nézetmodellje, különben a JSON-fixture
 * A nézetmodell a spec szerint választódik (becsült nélkül, magas RoB nélkül, kiugrók, fix hatás, DL τ²,
 * különben az elsődleges) — a változatok a motor valódi kimenetei (analysis_plots.json).
 * Tesztkapcsolók: MA.dev.analysis.setK(k) (pl. 55 → nincs automatikus explore), .setDelay(ms), .state.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var FX = MA.dev && MA.dev.fixtures;
  if (!FX) { return; }

  var NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
  var DATA_SHA = '9f3abbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
  var VARIANT_RUN = {
    primary: '20261004T211200Z-a1f3c2', no_estim: '20261004T211500Z-c9d2a7', no_rob: '20261005T091600Z-a7b8c9',
    fixed: '20261005T091500Z-f1e2d3', dl: '20261005T091500Z-d1a2b3', outliers: '20261005T091600Z-0f1e2d'
  };
  var state = { specs: {}, jobs: {}, n: 0, committed: {}, overrideK: null, exploreDelay: 120, reqId: 0 };

  // A fixture-futások az o1-tábla adatára DATA_SHA-val hivatkoznak; a kinyerési fixture-tábla ETag-je ettől független.
  // A két fixture-világ összekötése: a tábla első látott ETag-je = DATA_SHA; ha a tábla azóta változott (rács vagy
  // MA.dev.extraction.externalEdit), az új ETag — így a feltárás frissessége (FID-5) a fejlesztői buildben is
  // ugyanúgy viselkedik, mint a valódi szerveren.
  var O1 = '03_adatok/o1.csv';
  var baseEtag = {};
  MA.dev.mapTableSha = function (ds, etag) {
    if (ds !== O1 || !etag) { return etag; }
    if (!Object.prototype.hasOwnProperty.call(baseEtag, ds)) { baseEtag[ds] = etag; }
    return etag === baseEtag[ds] ? DATA_SHA : etag;
  };
  function liveSha(ds) {
    var ex = MA.dev.extraction && MA.dev.extraction.state ? MA.dev.extraction.state() : null;
    var tb = ex && ex.tables ? ex.tables[ds] : null;
    return tb ? MA.dev.mapTableSha(ds, tb.data.etag) : DATA_SHA;
  }

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  function meta(ms) {
    state.reqId += 1;
    return { engine: '0.2.0', elapsed_ms: ms || 1, project_rev: 41, request_id: 'q_dx' + String(state.reqId) };
  }
  function ok(schema, data, extra) { return Object.assign({ envelope: { ok: true, schema: schema, data: data, warnings: [], meta: meta(1) } }, extra || {}); }
  function fail(code, http, message, details) {
    var e = { code: code, http: http, message: message };
    if (details) { e.details = details; }
    return { status: http, envelope: { ok: false, error: e } };
  }

  // ---------------------------------------------------------------- JSON-útvonalak (átesés)
  function split(p) { return p.replace(/^\/+|\/+$/g, '').split('/'); }
  function matchPath(pattern, path) {
    var a = split(pattern), b = split(path);
    for (var i = 0; i < a.length; i++) {
      if (a[i] === '**') { return true; }
      if (i >= b.length || (a[i] !== '*' && a[i] !== b[i])) { return false; }
    }
    return a.length === b.length;
  }
  function subset(want, got) {
    if (!want) { return true; }
    if (!got) { return false; }
    return Object.keys(want).every(function (k) { return String(want[k]) === String(got[k]); });
  }
  function spec_(r) { return (r.query ? Object.keys(r.query).length : 0) + (r.body ? Object.keys(r.body).length : 0) + (r.path.indexOf('*') < 0 ? 1 : 0); }
  function jsonRoute(method, path, query, body) {
    var c = FX.routes.filter(function (r) {
      return (r.method || 'GET').toUpperCase() === method && matchPath(r.path, path) && subset(r.query, query) && subset(r.body, body);
    });
    c.sort(function (a, b) { return spec_(b) - spec_(a); });
    if (!c.length) { return null; }
    var r = c[0];
    var step = r;
    if (Array.isArray(r.sequence) && r.sequence.length) {
      step = r.sequence[r._pos < r.sequence.length ? r._pos : r.sequence.length - 1];
      r._pos += 1;
    }
    return { status: step.status || 200, envelope: clone(step.envelope), etag: step.etag || r.etag || null };
  }
  function fallthrough(req) {
    return jsonRoute(req.method, req.path, req.query, req.body) || fail('NOT_FOUND', 404, 'Nincs fixture ehhez: ' + req.method + ' ' + req.path);
  }
  function libraryData(path) {
    var r = jsonRoute('GET', path, {}, null);
    return r && r.envelope && r.envelope.ok ? r.envelope.data : null;
  }

  // ---------------------------------------------------------------- spec-ek
  FX.route('GET', '/api/specs/*', function (req) {
    var name = decodeURIComponent(req.path.split('/').pop());
    var st = state.specs[name];
    if (st) { return ok('szk.ma.analysis-spec/v1', clone(st.spec), { etag: st.etag }); }
    return fallthrough(req);
  });

  FX.route('PUT', '/api/specs/*', function (req) {
    var name = decodeURIComponent(req.path.split('/').pop());
    var body = req.body || {};
    var sp = body.spec && typeof body.spec === 'object' ? body.spec : body;
    if (!NAME_RE.test(name) || !sp || sp.name !== name || sp.schema !== 'szk.ma.analysis-spec/v1') {
      return fail('VALIDATION', 422, 'A spec neve vagy sémája érvénytelen (szk.ma.analysis-spec/v1, név: ^[a-z0-9][a-z0-9_-]{0,63}$).');
    }
    var cur = state.specs[name];
    var curEtag = cur ? cur.etag : null;
    if (!cur) {
      var base = jsonRoute('GET', '/api/specs/' + name, {}, null);
      if (base && base.envelope.ok) { curEtag = base.etag; }
    }
    var ifMatch = req.headers['If-Match'] || null;
    if (ifMatch && curEtag && ifMatch !== curEtag) {
      return fail('CONFLICT', 409, 'A spec-fájlt közben más is módosította (ETag-eltérés).', { cells: [], current_etag: curEtag });
    }
    var n = cur ? cur.n + 1 : 2;
    var etag = '"spec-' + name + '-' + String(n) + '"';
    var saved = clone(sp);
    delete saved.client_seq;
    state.specs[name] = { spec: saved, etag: etag, n: n };
    return ok('szk.ma.analysis-spec/v1', clone(saved), { etag: etag });
  });

  // ---------------------------------------------------------------- elemzés + feladatok
  function variantOf(sp) {
    var o = sp.options || {};
    var ex = (sp.filters && sp.filters.exclude) || [];
    if (ex.indexOf('estimated=igen') >= 0) { return 'no_estim'; }
    if (ex.indexOf('rob=high') >= 0) { return 'no_rob'; }
    if (o.outliers === true) { return 'outliers'; }
    if (o.model === 'fixed') { return 'fixed'; }
    if (o.tau2 === 'DL') { return 'dl'; }
    return 'primary';
  }

  function engineOptions() {
    var d = libraryData('/api/engine');
    return d && d.options ? d.options : {};
  }

  /** A spec.py argv_from_spec utánzata: csak a nem alapértelmezett, CLI-kapcsolós opciók. */
  function expandedArgv(sp) {
    var meta_ = engineOptions();
    var argv = ['ma.py', 'analyze', '--data', (sp.data && sp.data.path) || '03_adatok/o1.csv'];
    Object.keys(sp.options || {}).forEach(function (k) {
      var m = meta_[k];
      var v = sp.options[k];
      if (!m || !m.cli) { return; }
      if (k !== 'measure' && JSON.stringify(v) === JSON.stringify(m['default'])) { return; }
      if (v === true) { argv.push(m.cli); } else if (Array.isArray(v)) { if (v.length) { argv.push(m.cli, v.join(',')); } } else if (v !== null && v !== false && v !== undefined) { argv.push(m.cli, String(v)); }
    });
    ((sp.filters && sp.filters.exclude) || []).forEach(function (e) { argv.push('--exclude', e); });
    ((sp.filters && sp.filters.include) || []).forEach(function (e) { argv.push('--include', e); });
    return argv;
  }

  function stamp() {
    state.n += 1;
    var mm = state.n < 10 ? '0' + String(state.n) : String(state.n % 60);
    return { iso: '2026-10-05T09:' + mm + ':00Z', id: '20261005T09' + mm + '00Z-' + ('d0' + String(1000 + state.n)).slice(-6) };
  }

  function makeResult(sp, mode, clientSeq) {
    var variant = variantOf(sp);
    var tmpl = libraryData('/api/runs/' + VARIANT_RUN[variant]) || libraryData('/api/runs/' + VARIANT_RUN.primary);
    var plot = libraryData('/api/runs/' + VARIANT_RUN[variant] + '/plot');
    var run = clone(tmpl);
    var specPath = '05_elemzes/specs/' + sp.name + '.json';
    run.spec = { path: specPath, sha256: run.spec.sha256, name: sp.name, parent: sp.parent || null, purpose: sp.purpose || null };
    run.client_seq = clientSeq;
    run.expanded_argv = expandedArgv(sp);
    run.stale = false;
    if (run.data && sp.data && sp.data.path === O1) { run.data = Object.assign({}, run.data, { sha256: liveSha(O1) }); }
    if (mode === 'explore') {
      run.run_id = null;
      run.mode = 'explore';
      run.files = null;
      run.started = null;
      run.finished = null;
      run.equivalent_argv = ['ma.py', 'analyze', '--spec', specPath, '--project', '.'];
      plot.meta.run_id = null;
    } else {
      var s = stamp();
      run.run_id = s.id;
      run.mode = 'commit';
      run.started = s.iso;
      run.finished = s.iso;
      var out = '05_elemzes/' + (sp.outcome || 'o1') + '/' + s.id;
      run.equivalent_argv = ['ma.py', 'analyze', '--spec', specPath, '--out', out, '--project', '.'];
      run.expanded_argv = run.expanded_argv.concat(['--out', out]);
      Object.keys(run.files || {}).forEach(function (k) {
        run.files[k].path = out + '/' + run.files[k].path.split('/').pop();
      });
      plot.meta.run_id = s.id;
      state.committed[s.id] = { variant: variant, plot: clone(plot) };
    }
    if (typeof state.overrideK === 'number') { run.k = state.overrideK; }
    return { schema: 'szk.ma.analysis-result/v1', run: run, plot: mode === 'explore' ? plot : null,
      validation_summary: run.validation_summary, excluded: [] };
  }

  function snapshot(job) {
    var s = { job_id: job.id, kind: job.kind, mode: job.mode, client_seq: job.client_seq, status: job.status,
      elapsed_ms: job.polls * 40, run_ms: job.polls * 30 };
    if (job.status === 'done') { s.result = job.result; }
    if (job.error) { s.error = job.error; }
    if (job.superseded_by) { s.superseded_by = job.superseded_by; }
    return s;
  }

  FX.route('POST', '/api/analyze', function (req) {
    var b = req.body || {};
    var sp = b.spec;
    var mode = b.mode === 'commit' ? 'commit' : (b.mode === 'explore' ? 'explore' : null);
    if (!mode) { return fail('BAD_REQUEST', 400, "A 'mode' értéke explore vagy commit lehet."); }
    if (!sp || sp.schema !== 'szk.ma.analysis-spec/v1' || !NAME_RE.test(sp.name || '')) {
      return fail('VALIDATION', 422, 'Érvénytelen elemzési spec (szk.ma.analysis-spec/v1).');
    }
    var meta_ = engineOptions();
    var bad = Object.keys(sp.options || {}).filter(function (k) { return !Object.prototype.hasOwnProperty.call(meta_, k); });
    if (bad.length) { return fail('VALIDATION', 422, 'Ismeretlen opció(k) a specben: ' + bad.join(', ')); }
    var cur = sp.data && sp.data.path === O1 ? liveSha(O1) : DATA_SHA;
    if (mode === 'commit' && sp.data && sp.data.sha256 && sp.data.sha256 !== cur) {
      return fail('CONFLICT', 409, 'Az adattábla a legutóbbi explore óta megváltozott (data.sha256 eltér) — futtasd újra az explore-t.', { data_sha256: cur });
    }
    var id = 'j' + String(1000 + Object.keys(state.jobs).length + 1);
    var seq = typeof b.client_seq === 'number' ? b.client_seq : 0;
    var job = { id: id, kind: mode + ':' + (sp.outcome || '?'), mode: mode, client_seq: seq, status: mode === 'explore' ? 'running' : 'queued',
      polls: 0, spec: clone(sp), result: null, error: null, superseded_by: null };
    if (mode === 'explore') {
      Object.keys(state.jobs).forEach(function (k) {
        var o = state.jobs[k];
        if (o.mode === 'explore' && o.kind === job.kind && (o.status === 'running' || o.status === 'queued') && o.client_seq < seq) {
          o.status = 'superseded';
          o.superseded_by = id;
        }
      });
    }
    state.jobs[id] = job;
    return ok('szk.ma.job/v1', snapshot(job), { delay_ms: mode === 'explore' ? state.exploreDelay : 30 });
  });

  FX.route('GET', '/api/jobs/*', function (req) {
    var id = req.path.split('/').pop();
    var job = state.jobs[id];
    if (!job) { return fallthrough(req); }
    job.polls += 1;
    if (job.status === 'queued') {
      job.status = 'running';
    } else if (job.status === 'running') {
      job.status = 'done';
      job.result = makeResult(job.spec, job.mode, job.client_seq);
    }
    return ok('szk.ma.job/v1', snapshot(job), { delay_ms: 20 });
  });

  FX.route('GET', '/api/runs/*/plot', function (req) {
    var id = decodeURIComponent(req.path.split('/')[3] || '');
    var c = state.committed[id];
    if (c) { return ok('szk.ma.plot/v2', clone(c.plot)); }
    return fallthrough(req);
  });

  MA.dev.analysis = {
    state: state,
    setK: function (k) { state.overrideK = typeof k === 'number' ? k : null; },
    setDelay: function (ms) { state.exploreDelay = ms; },
    variantOf: variantOf,
    reset: function () { state.specs = {}; state.jobs = {}; state.committed = {}; state.overrideK = null; state.exploreDelay = 120; }
  };
})();
