/* dev/process_backend.js — CSAK a fejlesztői buildben (build_gui.py --dev, ?fixtures=1).
 *
 * Állapottartó háttér a folyamat-képernyők útvonalaihoz (3.5.1, 3.5.14–3.5.17):
 *   GET /api/log/finding|decision|checkpoint (szűrőkkel, mint a projekt.list_items), POST /api/log/finding|resolve|checkpoint
 *   (a kapu a motor szabálya szerint: PASS nyitott blocker mellett 409 GATE_BLOCKED), GET /api/prisma, PUT /api/prisma/manual
 *   (dry_run: a web/fixtures/prisma.json motor-generált előnézetei közül a pontosan egyező flow-é; mentés If-Match-csel),
 *   GET/PUT /api/studies (ETag, If-Match → 409, ismétlődő study_id → 422).
 * A kiinduló adatok a web/fixtures/*.json (tests/gui/ui/gen_process_fixtures.py a valódi motorból generálta).
 * FIGYELEM: ez nem a motor: a P-ellenőrzés itt csak előre kiszámolt válaszok kikeresése (a termékben a motor számol).
 * Tesztelői segédek: MA.dev.process.{state(), addFinding(f), prismaMode('composer'|'manual'), bumpEtag('prisma'|'studies')}.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var FX = MA.dev && MA.dev.fixtures;
  if (!FX) { return; }

  var S = null;
  function clone(o) { return o === undefined ? undefined : JSON.parse(JSON.stringify(o)); }
  function route(file, method, path, query) {
    var hit = null;
    FX.routes.forEach(function (r) {
      if (hit || r._file !== file || (r.method || 'GET') !== method || r.path !== path) { return; }
      if (JSON.stringify(r.query || null) !== JSON.stringify(query || null) || r.body) { return; }
      hit = r;
    });
    return hit;
  }
  function data(file, method, path, query) { var r = route(file, method, path, query); return r ? clone(r.envelope.data) : null; }
  function ok(schema, d, etag) { S.rev += 1; return { status: 200, etag: etag || null, envelope: { ok: true, schema: schema, data: d, warnings: [], meta: { engine: '0.2.0', elapsed_ms: 1, project_rev: S.rev, request_id: 'q_dp' + String(S.rev) } } }; }
  function fail(code, http, message, details) {
    var e = { code: code, http: http, message: message };
    if (details) { e.details = details; }
    return { status: http, envelope: { ok: false, error: e } };
  }
  function now() { return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'); }

  function init() {
    if (S) { return; }
    var pr = data('prisma.json', 'GET', '/api/prisma', null);
    S = {
      rev: 41,
      findings: data('log_finding.json', 'GET', '/api/log/finding', null).items,
      decisions: data('log_decision.json', 'GET', '/api/log/decision', null).items,
      checkpoints: data('log_checkpoint.json', 'GET', '/api/log/checkpoint', null).items,
      prisma: { state: pr, base: clone(pr), etag: 1, flow: null },
      studies: { doc: data('studies.json', 'GET', '/api/studies', null), etag: 1 }
    };
  }

  // ---------------------------------------------------------------- napló
  function stagesOf(v) { return v ? String(v).split(/[,;\s]+/).filter(function (x) { return !!x; }) : null; }
  function stageMatch(item, stage) {
    if (!stage) { return true; }
    var st = stagesOf(item.stage_id);
    return st === null || st.indexOf(stage) >= 0;
  }
  function openBlockers(stage) {
    return S.findings.filter(function (f) {
      return f.severity === 'blocker' && (f.status === 'open' || f.status === 'wontfix') && (stage === 'FINAL' || stageMatch(f, stage));
    });
  }

  FX.route('GET', '/api/log/finding', function (req) {
    init();
    var q = req.query || {};
    var items = S.findings.filter(function (f) {
      if (q.status && (q.status === 'resolved' ? f.status === 'open' : f.status !== q.status)) { return false; }
      if (q.severity && f.severity !== q.severity) { return false; }
      return stageMatch(f, q.stage);
    });
    return ok('szk.ma.log/v1', { kind: 'finding', items: clone(items) });
  });

  FX.route('GET', '/api/log/decision', function (req) {
    init();
    var q = req.query || {};
    var extra = MA.dev.extraction && MA.dev.extraction.decisions ? MA.dev.extraction.decisions().map(function (d) {
      var b = d.body || {};
      return { id: d.id, ts: now(), agent: b.agent || 'user', stage_id: b.stage || null, decision: b.decision, rationale: b.rationale || null,
        alternatives: b.alternatives || null, kb_refs: (b.kb_refs || []).join(','), status: 'active', supersedes: null, kb_unverified: null };
    }) : [];
    var items = S.decisions.concat(extra).filter(function (d) { return (!q.status || d.status === q.status) && stageMatch(d, q.stage); });
    return ok('szk.ma.log/v1', { kind: 'decision', items: clone(items) });
  });

  FX.route('GET', '/api/log/checkpoint', function () {
    init();
    return ok('szk.ma.log/v1', { kind: 'checkpoint', items: clone(S.checkpoints) });
  });

  function nextId(list) { var m = 0; list.forEach(function (x) { if (x.id > m) { m = x.id; } }); return m + 1; }

  FX.route('POST', '/api/log/finding', function (req) {
    init();
    var b = req.body || {};
    if (['blocker', 'major', 'minor', 'info'].indexOf(b.severity) < 0 || !b.title) { return fail('BAD_REQUEST', 400, 'Hiányzó mező: title / severity.'); }
    var f = { id: nextId(S.findings), ts: now(), agent: b.agent || 'user', stage_id: b.stage || null, severity: b.severity, title: b.title,
      detail: b.detail || null, evidence: b.evidence || null, kb_refs: (b.kb_refs || []).join(',') || null, status: 'open', resolution: null,
      resolved_ts: null, kb_unverified: null };
    S.findings.push(f);
    var unknown = (b.kb_refs || []).filter(function (x) { return !/^([VPX]\d{3}|D-S\d{2}-\d{3}|K-[A-Z0-9]+-\d+)$/.test(x); });
    var w = unknown.length ? ['Ismeretlen KB-azonosító (nem ellenőrzött): ' + unknown.join(', ')] : [];
    var r = ok('szk.ma.log-write/v1', { kind: 'finding', id: f.id, warnings: w });
    r.envelope.warnings = w;
    return r;
  });

  FX.route('POST', '/api/log/resolve', function (req) {
    init();
    var b = req.body || {};
    var f = S.findings.filter(function (x) { return x.id === b.id; })[0];
    if (!f) { return fail('VALIDATION', 422, 'nincs ilyen megállapítás: ' + String(b.id)); }
    if (f.severity === 'blocker' && b.status === 'wontfix') {
      return fail('VALIDATION', 422, 'A #' + f.id + ' blocker: csak fixed vagy indokolt invalid státusszal zárható, wontfix-szel nem — javítatlan blocker mellett a szakasz nem kaphat PASS-t.');
    }
    if (b.status === 'open' && f.status === 'open') { return fail('VALIDATION', 422, 'a #' + f.id + ' megállapítás már nyitott'); }
    f.status = b.status;
    f.resolution = b.status === 'open' ? 'újranyitva: ' + b.resolution : b.resolution;
    f.resolved_ts = b.status === 'open' ? null : now();
    return ok('szk.ma.log-write/v1', { kind: 'resolve', id: f.id, warnings: [] });
  });

  FX.route('POST', '/api/log/checkpoint', function (req) {
    init();
    var b = req.body || {};
    if (b.verdict === 'PASS' || b.verdict === 'PASS_WITH_FIXES') {
      var bl = openBlockers(b.stage);
      if (bl.length) {
        var where = b.stage === 'FINAL' ? 'a záró (FINAL) ellenőrzőponthoz egyetlen szakaszban sem lehet nyitott blocker' : 'ennél a szakasznál (' + b.stage + ')';
        var msg = String(bl.length) + " nyitott 'blocker' megállapítás van — " + where + '; ' + b.verdict + ' nem adható. Nyitott: ' +
          bl.map(function (x) { return '#' + x.id + ' [' + (x.stage_id || '–') + '] ' + x.title; }).join('; ');
        return fail('GATE_BLOCKED', 409, msg, { blockers: bl.map(function (x) { return { id: x.id, stage: x.stage_id, title: x.title, status: x.status }; }), stage: b.stage, verdict: b.verdict });
      }
    }
    var c = { id: nextId(S.checkpoints), ts: now(), stage_id: b.stage, agent: b.agent || 'user', verdict: b.verdict, summary: b.summary || null };
    S.checkpoints.push(c);
    return ok('szk.ma.log-write/v1', { kind: 'checkpoint', id: c.id, warnings: [] });
  });

  // ---------------------------------------------------------------- PRISMA
  function etag(kind) { return '"' + kind + '-' + String(S[kind].etag) + '"'; }
  function cannedCheck(flow) {
    var key = JSON.stringify(flow);
    var hit = null;
    FX.routes.forEach(function (r) {
      if (!hit && r._file === 'prisma.json' && r.method === 'PUT' && r.body && r.body.flow && JSON.stringify(r.body.flow) === key) { hit = r; }
    });
    return hit ? clone(hit.envelope.data.check) : null;
  }

  FX.route('GET', '/api/prisma', function () {
    init();
    return ok('szk.ma.prisma/v1', clone(S.prisma.state), etag('prisma'));
  });

  FX.route('PUT', '/api/prisma/manual', function (req) {
    init();
    var b = req.body || {};
    var flow = b.flow || {};
    var chk = cannedCheck(flow) || clone(S.prisma.base.check);
    var st = clone(S.prisma.state);
    st.check = chk;
    st.flow = clone(flow);
    if (b.dry_run) { st.saved = false; return ok('szk.ma.prisma/v1', st, null); }
    if (req.headers['If-Match'] !== etag('prisma')) {
      return fail('CONFLICT', 409, 'A 02_szures/prisma_flow.json közben megváltozott (más program vagy ágens írta). Töltsd újra.', { expected: req.headers['If-Match'] || null, current: etag('prisma') });
    }
    st.mode = 'manual';
    st.source = { kind: 'manual', updated: now(), actor: 'SzK' };
    st.override = b.override_reason ? { reason: b.override_reason, decision_id: nextId(S.decisions) } : null;
    st.composer_status = [];
    st.saved = true;
    S.prisma.state = st;
    S.prisma.etag += 1;
    return ok('szk.ma.prisma/v1', clone(st), etag('prisma'));
  });

  // ---------------------------------------------------------------- vizsgálat ↔ jelentés
  FX.route('GET', '/api/studies', function () {
    init();
    return ok('szk.ma.studies/v1', clone(S.studies.doc), etag('studies'));
  });

  FX.route('PUT', '/api/studies', function (req) {
    init();
    var b = req.body || {};
    if (req.headers['If-Match'] !== etag('studies')) {
      return fail('CONFLICT', 409, 'A 03_adatok/studies.json közben megváltozott. Töltsd újra.', { current: etag('studies') });
    }
    var seen = {}, bad = [], recs = {};
    (b.studies || []).forEach(function (s) {
      var id = String(s.study_id || '').trim();
      if (!id || seen[id]) { bad.push(id || '(üres)'); }
      seen[id] = true;
      (s.reports || []).forEach(function (r) { if (r.rec_id) { recs[r.rec_id] = true; } });
    });
    if (bad.length) { return fail('VALIDATION', 422, 'Ismétlődő vagy üres study_id: ' + bad.join(', ') + '.'); }
    var doc = clone(S.studies.doc);
    doc.studies = clone(b.studies || []);
    doc.summary = { studies: doc.studies.length, reports: Object.keys(recs).length };
    S.studies.doc = doc;
    S.studies.etag += 1;
    S.prisma.state.studies = { path: doc.path, studies: doc.summary.studies, reports: doc.summary.reports };
    return ok('szk.ma.studies/v1', clone(doc), etag('studies'));
  });

  // ---------------------------------------------------------------- képességek: a refresh eredménye a további GET-ekben is
  FX.route('GET', '/api/capabilities', function () {
    init();
    return ok('szk.ma.capabilities-matrix/v1', clone(S.caps || data('capabilities.json', 'GET', '/api/capabilities', null)));
  });
  FX.route('POST', '/api/capabilities/refresh', function () {
    init();
    S.caps = data('capabilities.json', 'POST', '/api/capabilities/refresh', null);
    return ok('szk.ma.capabilities-matrix/v1', clone(S.caps));
  });

  MA.dev.process = {
    state: function () { init(); return S; },
    addFinding: function (f) { init(); var x = Object.assign({ id: nextId(S.findings), ts: now(), agent: 'reviewer', status: 'open', kb_refs: null, detail: null }, f); S.findings.push(x); return x.id; },
    prismaMode: function (mode) {
      init();
      var d = data('prisma.json', 'GET', '/api/prisma', mode === 'composer' ? { variant: 'composer' } : null);
      S.prisma.state = d;
      S.prisma.etag += 1;
    },
    bumpEtag: function (kind) { init(); S[kind].etag += 1; }
  };
})();
