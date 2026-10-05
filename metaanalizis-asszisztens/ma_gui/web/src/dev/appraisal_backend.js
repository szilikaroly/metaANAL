/* dev/appraisal_backend.js — CSAK a fejlesztői buildben (build_gui.py --dev, ?fixtures=1).
 *
 * Állapottartó háttér az értékelés-képernyőkhöz (3.5.10–3.5.13): GET /api/appraisals (lista), GET/PUT
 * /api/appraisals/<unit>/<tool> (ETag, If-Match → 409, X017-indoklás → 422, „kész” feltételei), POST …/check,
 * POST …/approve (AI-vázlat jóváhagyása), GET /api/appraisals/consensus/<unit>/<tool>, POST /api/appraisals/import
 * (ütközés → 409, replace), POST /api/appraisals/export, POST /api/appraisals/rob-sync (előnézet a fixtúrából,
 * alkalmazás If-Match-csel). A kiinduló adatok: web/fixtures/appraisal_*.json (gen_appraisal_fixtures.py).
 * FIGYELEM: ez NEM a motor. A teljesség és az „implikált” ítélet itt egy leegyszerűsített szimuláció (a validator
 * konzervatív logikájának tükre) — csak a felület tesztjéhez; a κ és minden számszöveg a fixtúrából jön.
 * Tesztelői segédek: MA.dev.appraisal.{state(), bumpEtag(path), doc(path)}.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var FX = MA.dev && MA.dev.fixtures;
  if (!FX) { return; }

  var S = null;
  var DIR = '04_torzitas_kockazat/appraisals/';
  var NO_ISH = { no: 1, probably_no: 1 };
  var YES_ISH = { yes: 1, probably_yes: 1, partial_yes: 1 };
  var UNKNOWN = { no_information: 1, unclear: 1, partly: 1 };

  function clone(o) { return o === undefined ? undefined : JSON.parse(JSON.stringify(o)); }
  function routesOf(file) { return FX.routes.filter(function (r) { return r._file === file; }); }
  function ok(schema, d, etag, warnings) {
    S.rev += 1;
    return { status: 200, etag: etag || null, envelope: { ok: true, schema: schema, data: d, warnings: warnings || [],
      meta: { engine: '0.2.0', elapsed_ms: 1, project_rev: S.rev, request_id: 'q_da' + String(S.rev) } } };
  }
  function fail(code, http, message, details) {
    var e = { code: code, http: http, message: message };
    if (details) { e.details = details; }
    return { status: http, envelope: { ok: false, error: e } };
  }
  function now() { return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'); }

  function init() {
    if (S) { return; }
    S = { rev: 41, insts: {}, docs: {}, etags: {}, n: 0, list: null, table_etag: '"tbl-1"' };
    routesOf('appraisal_instruments.json').forEach(function (r) {
      var d = r.envelope.data;
      if (d && d.schema === 'szk.instrument/v1') { S.insts[d.key] = d; }
    });
    routesOf('appraisal_docs.json').forEach(function (r) {
      var v = r.envelope.data;
      S.docs[v.path] = clone(v.doc);
      S.etags[v.path] = 1;
    });
  }

  function slug(u) { return String(u).replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 64); }
  function pathOf(unit, tool, target, rater) { return DIR + [slug(unit), tool].concat(target ? [target] : []).concat([rater]).join('.') + '.json'; }
  function etag(p) { return '"ap-' + String(S.etags[p] || 0) + '"'; }
  function unitOf(doc) { var tg = doc.target || {}; return tg.unit || tg.study_id; }
  function isHuman(doc, rater) {
    return doc.origin !== 'ai_draft' && rater !== 'ai' && rater !== 'consensus' && doc.status !== 'consensus';
  }
  function parse(p) {
    var parts = p.slice(DIR.length, -5).split('.');
    return { slug: parts[0], tool: parts[1], target: parts.length === 4 ? parts[2] : null, rater: parts[parts.length - 1] };
  }

  // ---------------------------------------------------------------- SZIMULÁLT ellenőrzés (nem a motor!)
  function val(doc, key) { var a = (doc.answers || {})[key]; return a && typeof a === 'object' ? a.value : null; }
  function verdictFor(inst, lev) { var v = (inst.verdicts || []).filter(function (x) { return x.level === lev; })[0]; return v ? v.value : null; }
  function scoped(inst, doc) {
    var scope = doc.scope;
    var passes = (inst.passes || []).length > 0;
    return inst.items.filter(function (it) {
      if (passes) { return !(scope === 'development' || scope === 'evaluation') || it.pass === scope; }
      if (it.applies_to) {
        if (scope === 'development') { return it.applies_to.indexOf('D') >= 0; }
        if (scope === 'evaluation') { return it.applies_to.indexOf('E') >= 0; }
        return true;
      }
      var sc = it.scope || ['all'];
      return !scope || sc.indexOf('all') >= 0 || sc.indexOf(scope) >= 0;
    });
  }
  function frac(a, b) { return String(a) + '/' + String(b); }

  function check(doc) {
    var inst = S.insts[doc.tool];
    var allowed = {};
    (inst.answers || []).forEach(function (a) { allowed[a.value] = 1; });
    var items = scoped(inst, doc);
    var missing = [], answered = 0;
    items.forEach(function (it) {
      var v = val(doc, it.key);
      if (v === null || v === undefined || !allowed[v]) { missing.push({ item: it.id, pass: it.pass || null, key: it.key }); } else { answered += 1; }
    });
    var alg = (inst.rollup || {}).algorithm || 'none';
    var res = { schema: 'szk.appraisal-result/v1', tool: inst.key, engine_version: 'dev-szimuláció', complete: missing.length === 0,
      expected: items.length, answered: answered, completeness_text: frac(answered, items.length), missing: missing, invalid: [],
      domains: [], overall: { implied: null, algorithm: alg, text: null }, per_pass: null, amstar2: null, tripod: null, guards: [], notes: [], warnings: [] };
    if ((inst.passes || []).length) {
      res.per_pass = {};
      inst.passes.forEach(function (p) {
        var its = items.filter(function (it) { return it.pass === p.id; });
        var n = its.filter(function (it) { return allowed[val(doc, it.key)]; }).length;
        res.per_pass[p.id] = { expected: its.length, answered: n, complete: n === its.length, text: frac(n, its.length) };
      });
    }
    if (alg === 'conservative') {
      var worst = 'low', anyNone = false;
      inst.domains.forEach(function (d) {
        var its = items.filter(function (it) { return String(it.domain) === String(d.id); });
        if (!its.length) { return; }
        var forced = [], unk = [], miss = [], routers = [];
        its.forEach(function (it) {
          var v = val(doc, it.key);
          if ((it.tags || []).indexOf('router') >= 0) { if (v) { routers.push(it.id); } return; }
          if (!v) { miss.push(it.id); return; }
          var bad = (it.tags || []).indexOf('reverse') >= 0 ? !!YES_ISH[v] : !!NO_ISH[v];
          if (bad) { forced.push(it.id); } else if (UNKNOWN[v]) { unk.push(it.id); }
        });
        var lev = forced.length ? 'high' : (miss.length ? null : (unk.length ? 'some' : 'low'));
        var why = forced.length ? { hu: "'Nem' / 'Valószínűleg nem' itt: " + forced.join(', '), en: "'No' or 'Probably no' at " + forced.join(', ') }
          : (miss.length ? { hu: 'hiányzó válasz: ' + miss.join(', '), en: 'unanswered: ' + miss.join(', ') }
            : (unk.length ? { hu: 'nincs információ: ' + unk.join(', '), en: 'no information at ' + unk.join(', ') }
              : { hu: 'egyik jelző-kérdés sem jelez problémát', en: 'no signalling question flags a problem' }));
        res.domains.push({ domain: d.id, pass: null, implied: lev ? verdictFor(inst, lev) : null, algorithm: alg, forced_by: forced, unknown_at: unk,
          routers: routers, missing: miss, text: why });
        if (lev === null) { anyNone = true; } else if (lev === 'high' || (lev === 'some' && worst === 'low')) { worst = lev; }
      });
      if (worst === 'high' || !anyNone) { res.overall.implied = verdictFor(inst, worst); }
    }
    if (inst.key === 'amstar2') { res.amstar2 = amstar(inst, doc); res.overall.implied = res.amstar2.rating; }
    if (inst.key === 'tripod-ai') {
      var counts = {};
      items.forEach(function (it) { var v = val(doc, it.key) || 'unanswered'; counts[v] = (counts[v] || 0) + 1; });
      res.tripod = { scope: doc.scope || 'both', counts: counts,
        missing_items: items.filter(function (it) { var v = val(doc, it.key); return v === 'missing' || v === 'partial'; })
          .map(function (it) { return { item: it.id, applies_to: it.applies_to, status: val(doc, it.key) }; }),
        note: { hu: 'A TRIPOD+AI a jelentés teljességét méri, nem a módszertan helyességét.', en: 'TRIPOD+AI measures reporting completeness, not methodological soundness.' } };
    }
    res.overrides = overrides(doc, res);
    return res;
  }

  var RATING_TEXT = { high: ['MAGAS', 'HIGH'], moderate: ['MÉRSÉKELT', 'MODERATE'], low: ['ALACSONY', 'LOW'], critically_low: ['KRITIKUSAN ALACSONY', 'CRITICALLY LOW'] };
  function amstar(inst, doc) {
    var out = {};
    ['meets', 'weakness'].forEach(function (conv) {
      var flaws = [], weak = [], unans = [];
      inst.items.forEach(function (it) {
        var v = val(doc, it.key);
        if (!v) { unans.push(it.id); return; }
        if (v === 'yes') { return; }
        if (v === 'partial_yes') { if (it.critical && conv === 'weakness') { weak.push(it.id); } return; }
        if (it.critical) { flaws.push(it.id); } else { weak.push(it.id); }
      });
      var r = flaws.length > 1 ? 'critically_low' : (flaws.length ? 'low' : (weak.length > 1 ? 'moderate' : 'high'));
      out[conv] = { convention: conv, rating: r, rating_text: { hu: RATING_TEXT[r][0], en: RATING_TEXT[r][1] }, critical_flaws: flaws,
        weaknesses: weak, unanswered: unans, provisional: unans.length > 0 };
    });
    var main = clone(out.meets);
    main.alternative = out.weakness;
    main.differs = out.meets.rating !== out.weakness.rating;
    main.kb_ref = 'AMSTAR2-00';
    return main;
  }

  function overrides(doc, res) {
    var imp = {};
    (res.domains || []).forEach(function (d) { imp[d.domain + '|' + (d.pass || '')] = d; });
    var out = [];
    (doc.domain_judgements || []).forEach(function (dj) {
      var d = imp[dj.domain + '|' + (dj.pass || '')];
      if (d && dj.judgement && d.implied && dj.judgement !== d.implied && d.algorithm !== 'none') {
        out.push({ domain: dj.domain, pass: dj.pass || null, judgement: dj.judgement, implied: d.implied, algorithm: d.algorithm,
          reason_missing: !(dj.override_reason && String(dj.override_reason).trim()) });
      }
    });
    var ov = doc.overall || {};
    if (ov.judgement && res.overall.implied && ov.judgement !== res.overall.implied && res.overall.algorithm !== 'none') {
      out.push({ domain: 'overall', pass: null, judgement: ov.judgement, implied: res.overall.implied, algorithm: res.overall.algorithm,
        reason_missing: !(ov.override_reason && String(ov.override_reason).trim()) });
    }
    return out;
  }

  // ---------------------------------------------------------------- nézetek
  function siblings(unit, tool, target) {
    return Object.keys(S.docs).sort().filter(function (p) {
      var x = parse(p);
      return x.slug === slug(unit) && x.tool === tool && x.target === (target || null);
    }).map(function (p) { var x = parse(p); return { rater: x.rater, path: p, doc: S.docs[p], etag: etag(p) }; });
  }

  function studies() {
    var r = FX.routes.filter(function (x) { return x._file === 'studies.json' && x.method === 'GET'; })[0];
    return r ? r.envelope.data.studies.map(function (s) { return { study_id: s.study_id, label: s.label, design: s.design, outcomes: s.outcomes }; }) : [];
  }

  function outcomes() {
    var r = FX.routes.filter(function (x) { return x._file === 'project.json' && x.method === 'GET' && x.path === '/api/project'; })[0];
    return r ? (r.envelope.data.outcomes || []).map(function (o) { return { id: o.id, name: o.name, data: o.data, measure: o.measure }; }) : [];
  }

  function skeleton(unit, tool, target, rater) {
    var inst = S.insts[tool];
    var sc = inst.scopes && inst.scopes.length ? inst.scopes[0] : ((inst.passes || []).length ? 'both' : null);
    var special = unit === 'review' || unit === 'manuscript';
    return { schema: 'szk.appraisal/v1', tool: tool, scope: sc, instrument_sha256: inst.reference_sha256,
      target: { unit: unit, study_id: special ? null : unit, outcome: target === 'o1' || target === 'o2' ? target : null, result: null, model: null, index_test: null, key: target || null },
      assessor: rater, second_assessor: null, status: rater === 'consensus' ? 'consensus' : 'draft', origin: rater === 'ai' ? 'ai_draft' : 'human',
      approved_by: null, approved_at: null, answers: {}, domain_judgements: [], applicability: [], overall: null, created: null, updated: null };
  }

  function view(unit, tool, target, rater) {
    var p = pathOf(unit, tool, target, rater);
    var exists = !!S.docs[p];
    var doc = exists ? clone(S.docs[p]) : skeleton(unit, tool, target, rater);
    var sibs = siblings(unit, tool, target);
    var st = studies().filter(function (s) { return s.study_id === unit; })[0] || null;
    return { schema: 'szk.ma.appraisal-view/v1', unit: unit, tool: tool, target: target || null, rater: rater, path: p, exists: exists,
      etag: exists ? etag(p) : null, doc: doc, check: check(doc),
      instrument: { key: tool, name: S.insts[tool].name, reference_sha256: S.insts[tool].reference_sha256, unit: S.insts[tool].unit },
      raters: sibs.map(function (s) { return { rater: s.rater, path: s.path, origin: s.doc.origin, status: s.doc.status, assessor: s.doc.assessor,
        approved_by: s.doc.approved_by, updated: s.doc.updated, human: isHuman(s.doc, s.rater) }; }),
      study: st, human_raters: sibs.filter(function (s) { return isHuman(s.doc, s.rater); }).map(function (s) { return s.rater; }) };
  }

  function summary(p, withAnswers) {
    var x = parse(p), doc = S.docs[p], c = check(doc);
    var out = { path: p, tool: x.tool, target: x.target, rater: x.rater, unit: unitOf(doc) || x.slug, etag: etag(p), human: isHuman(doc, x.rater),
      origin: doc.origin, status: doc.status, assessor: doc.assessor, second_assessor: doc.second_assessor, approved_by: doc.approved_by,
      updated: doc.updated, scope: doc.scope, target_obj: doc.target,
      domain_judgements: (doc.domain_judgements || []).map(function (d) { return { domain: d.domain, pass: d.pass || null, judgement: d.judgement }; }),
      applicability: (doc.applicability || []).map(function (d) { return { domain: d.domain, judgement: d.judgement }; }),
      overall: doc.overall ? { judgement: doc.overall.judgement } : null,
      check: { complete: c.complete, expected: c.expected, answered: c.answered, completeness_text: c.completeness_text, per_pass: c.per_pass,
        tripod: c.tripod, amstar2: c.amstar2, domains: c.domains.map(function (d) { return { domain: d.domain, pass: d.pass, implied: d.implied, algorithm: d.algorithm }; }),
        overall: { implied: c.overall.implied, algorithm: c.overall.algorithm },
        overrides_missing: c.overrides.filter(function (o) { return o.reason_missing; }).length } };
    if (withAnswers) {
      out.answers = {};
      Object.keys(doc.answers || {}).forEach(function (k) { out.answers[k] = val(doc, k); });
    }
    return out;
  }

  function q(req) { return req.query || {}; }
  function parts(path) { var s = path.split('/'); return { unit: decodeURIComponent(s[3]), tool: decodeURIComponent(s[4]) }; }

  FX.route('GET', '/api/appraisals', function (req) {
    init();
    var qq = q(req);
    var items = Object.keys(S.docs).sort().filter(function (p) {
      var x = parse(p);
      return (!qq.tool || x.tool === qq.tool) && (!qq.unit || x.slug === slug(qq.unit)) && (qq.target === undefined || x.target === (qq.target || null));
    }).map(function (p) { return summary(p, qq.answers === '1'); });
    return ok('szk.ma.appraisals/v1', { schema: 'szk.ma.appraisals/v1', dir: DIR.slice(0, -1), items: items, studies: studies(), outcomes: outcomes(),
      engine: { available: true, missing: [], functions: {} } });
  });

  FX.route('GET', '/api/appraisals/*/*', function (req) {
    init();
    var x = parts(req.path);
    if (x.unit === 'consensus') { return fail('BAD_REQUEST', 400, 'fenntartott'); }
    var qq = q(req);
    if (!qq.rater) { return fail('BAD_REQUEST', 400, 'Hiányzó értékelő (rater): add meg a monogramodat (pl. SzK).'); }
    if (!S.insts[x.tool]) { return fail('NOT_FOUND', 404, 'Ismeretlen értékelő eszköz: ' + x.tool); }
    var v = view(x.unit, x.tool, qq.target || null, qq.rater);
    return ok('szk.ma.appraisal-view/v1', v, v.etag);
  });

  function rules(doc, c) {
    var miss = c.overrides.filter(function (o) { return o.reason_missing; });
    if (miss.length) {
      return fail('VALIDATION', 422, 'Az ítéleted eltér a motor implikált ítéletétől (' + miss.map(function (o) { return o.domain === 'overall' ? 'összítélet' : 'D' + o.domain; }).join(', ') +
        '), de nincs indoklás. Írd le röviden, miért döntöttél másképp — az indoklás döntésként a projektnaplóba kerül (X017).', { overrides: miss });
    }
    if (doc.status === 'complete' || doc.status === 'consensus') {
      if (!c.complete) { return fail('VALIDATION', 422, "Az értékelés még nem teljes (kitöltve: " + c.completeness_text + "); 'kész' státusz csak minden tétel megválaszolása után adható.", { missing: c.missing }); }
      if (doc.origin === 'ai_draft' && !doc.approved_by) { return fail('VALIDATION', 422, 'AI-vázlat csak emberi jóváhagyás után lehet kész (6. döntés).'); }
    }
    return null;
  }

  FX.route('PUT', '/api/appraisals/*/*', function (req) {
    init();
    var x = parts(req.path), qq = q(req);
    var doc = (req.body || {}).doc;
    var p = pathOf(x.unit, x.tool, qq.target || null, qq.rater);
    var im = req.headers['If-Match'] || null;
    if ((S.docs[p] && im !== etag(p)) || (!S.docs[p] && im)) {
      return fail('CONFLICT', 409, 'A(z) ' + p + ' időközben megváltozott (például egy másik lapon vagy egy ágens írta). Töltsd be újra.', { dataset: p, etag: S.docs[p] ? etag(p) : null });
    }
    if (!doc || doc.tool !== x.tool || (qq.rater !== 'consensus' && qq.rater !== 'ai' && doc.assessor !== qq.rater)) {
      return fail('VALIDATION', 422, 'Az értékelés alakja hibás: assessor/tool', { problems: ['assessor: a kérés értékelője kell'] });
    }
    if (qq.rater === 'consensus') {
      var humans = siblings(x.unit, x.tool, qq.target).filter(function (s) { return isHuman(s.doc, s.rater); }).map(function (s) { return s.rater; });
      var lack = [doc.assessor, doc.second_assessor].filter(function (r) { return humans.indexOf(r) < 0; });
      if (lack.length) { return fail('VALIDATION', 422, 'Konszenzus csak két független emberi értékelés után menthető (AI-vázlat nem számít értékelőnek). Hiányzik: ' + lack.join(', ') + '.', { missing_raters: lack }); }
    }
    var c = check(doc);
    var bad = rules(doc, c);
    if (bad) { return bad; }
    var saved = clone(doc);
    saved.updated = now();
    saved.created = saved.created || saved.updated;
    S.n += 1;
    c.overrides.forEach(function (o) {
      var dj = o.domain === 'overall' ? saved.overall : (saved.domain_judgements || []).filter(function (d) { return String(d.domain) === String(o.domain) && (d.pass || null) === (o.pass || null); })[0];
      if (dj && !dj.decision_id) { dj.decision_id = 100 + S.n; }
    });
    S.docs[p] = saved;
    S.etags[p] = (S.etags[p] || 0) + 1;
    var v = view(x.unit, x.tool, qq.target || null, qq.rater);
    return ok('szk.ma.appraisal-view/v1', v, v.etag);
  });

  FX.route('POST', '/api/appraisals/*/*/check', function (req) {
    init();
    var doc = (req.body || {}).doc;
    return ok('szk.ma.appraisal-check/v1', { schema: 'szk.ma.appraisal-check/v1', check: check(doc), problems: [] });
  });

  FX.route('POST', '/api/appraisals/*/*/approve', function (req) {
    init();
    var x = parts(req.path), qq = q(req), b = req.body || {};
    var p = pathOf(x.unit, x.tool, qq.target || null, qq.rater);
    var cur = S.docs[p];
    if (!cur) { return fail('NOT_FOUND', 404, 'Nincs ilyen értékelés: ' + p); }
    if (!req.headers['If-Match']) { return fail('BAD_REQUEST', 400, 'A jóváhagyáshoz If-Match kell (a betöltött változat ETag-je): csak azt hagyhatod jóvá, amit láttál.'); }
    if (req.headers['If-Match'] !== etag(p)) { return fail('CONFLICT', 409, 'A(z) ' + p + ' időközben megváltozott. Töltsd be újra.'); }
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,31}$/.test(b.approver || '') || /^(ai|consensus)$/i.test(b.approver)) { return fail('BAD_REQUEST', 400, "A(z) '" + String(b.approver) + "' fenntartott vagy érvénytelen azonosító."); }
    if (cur.origin !== 'ai_draft') { return fail('VALIDATION', 422, 'Csak AI-vázlat hagyható jóvá.'); }
    if (cur.approved_by) { return fail('CONFLICT', 409, 'Ezt az AI-vázlatot már jóváhagyta: ' + cur.approved_by + '.'); }
    var next = clone(cur);
    next.approved_by = b.approver;
    next.approved_at = now();
    var c = check(next);
    var bad = rules(Object.assign({}, next, { status: 'draft' }), c);
    if (bad) { return bad; }
    var warnings = [];
    if (c.complete) { next.status = 'complete'; } else { warnings.push('A vázlat jóváhagyva, de még nem teljes (kitöltve: ' + c.completeness_text + ').'); }
    next.approval_decision_id = 200 + S.rev;
    S.docs[p] = next;
    S.etags[p] += 1;
    var v = view(x.unit, x.tool, qq.target || null, qq.rater);
    return ok('szk.ma.appraisal-view/v1', v, v.etag, warnings);
  });

  FX.route('GET', '/api/appraisals/consensus/*/*', function (req) {
    init();
    var s = req.path.split('/');
    var unit = decodeURIComponent(s[4]), tool = decodeURIComponent(s[5]);
    var target = q(req).target || null;
    var sibs = siblings(unit, tool, target);
    var humans = sibs.filter(function (x) { return isHuman(x.doc, x.rater); });
    var cons = sibs.filter(function (x) { return x.rater === 'consensus'; })[0] || null;
    var excluded = sibs.filter(function (x) { return x.doc.origin === 'ai_draft'; }).map(function (x) { return { rater: x.rater, path: x.path, origin: 'ai_draft', approved_by: x.doc.approved_by, reason: 'ai_draft' }; });
    var d = { schema: 'szk.ma.appraisal-consensus-view/v1', unit: unit, tool: tool, target: target, human_raters: humans.map(function (x) { return x.rater; }),
      excluded: excluded, ready: humans.length >= 2, a: null, b: null, agreement: null,
      consensus: { exists: !!cons, path: pathOf(unit, tool, target, 'consensus'), etag: cons ? cons.etag : null, doc: cons ? clone(cons.doc) : null },
      instrument: { key: tool, name: S.insts[tool].name } };
    function side(x) { return { rater: x.rater, path: x.path, etag: x.etag, doc: clone(x.doc), check: check(x.doc) }; }
    if (humans.length < 2) {
      d.reason = 'Konszenzushoz két független emberi értékelés kell ugyanerre az egységre és célra (az AI-vázlat nem számít második értékelőnek). Most: ' + String(humans.length) + '.';
      if (humans.length) { d.a = side(humans[0]); }
      return ok(d.schema, d);
    }
    d.a = side(humans[0]);
    d.b = side(humans[1]);
    var fx = FX.routes.filter(function (r) { return r._file === 'appraisal_consensus.json'; })[0];
    var agr = fx && unit === 'ARONSON1948' ? clone(fx.envelope.data.agreement) : { kappa_text: { hu: 'κ = — (fixtúra nincs)', en: 'κ = — (no fixture)' }, agreement_pct_text: '—', items: [], domains: [] };
    // a tételenkénti eltérés a mostani állapotból (címke-egyezés, nem számítás)
    var keys = {};
    [d.a.doc, d.b.doc].forEach(function (doc) { Object.keys(doc.answers || {}).forEach(function (k) { keys[k] = 1; }); });
    agr.items = Object.keys(keys).sort().map(function (k) { var va = val(d.a.doc, k), vb = val(d.b.doc, k); return { key: k, a: va, b: vb, agree: !!va && va === vb }; });
    agr.disagree = agr.items.filter(function (i) { return !i.agree; }).length;
    d.agreement = agr;
    return ok(d.schema, d, null, excluded.length ? [String(excluded.length) + ' AI-vázlat kimaradt az egyezés-számításból (6. döntés: nem értékelő).'] : []);
  });

  FX.route('POST', '/api/appraisals/import', function (req) {
    init();
    var b = req.body || {};
    var payload = b.doc;
    if (b.path) {
      var inbox = FX.routes.filter(function (r) { return r._file === 'appraisal_inbox.json'; })[0];
      var f = inbox && inbox.envelope.data.files.filter(function (x) { return x.path === b.path; })[0];
      if (!f) { return fail('NOT_FOUND', 404, 'Nincs ilyen fájl: ' + b.path); }
      var base = clone(S.docs[pathOf('ARONSON1948', 'rob2', 'o1', 'KP')]);
      base.target = { unit: 'FERGUSON1949', study_id: 'FERGUSON1949', outcome: 'o1', key: 'o1' };
      payload = { schema: 'szk.appraisal-bundle/v1', items: [base] };
    }
    if (!payload || (payload.schema !== 'szk.appraisal/v1' && payload.schema !== 'szk.appraisal-bundle/v1')) {
      return fail('VALIDATION', 422, 'A fájl nem értékelés (szk.appraisal/v1) és nem értékelés-csomag (szk.appraisal-bundle/v1).');
    }
    var docs = payload.schema === 'szk.appraisal-bundle/v1' ? payload.items : [payload];
    var plan = docs.map(function (doc) {
      var rater = doc.status === 'consensus' ? 'consensus' : (doc.origin === 'ai_draft' && !/^[A-Za-z]/.test(doc.assessor || '') ? 'ai' : doc.assessor);
      var p = pathOf(unitOf(doc), doc.tool, (doc.target || {}).key || null, rater);
      var cur = S.docs[p];
      var state = !cur ? 'new' : (JSON.stringify(cur) === JSON.stringify(doc) ? 'same' : 'conflict');
      return { path: p, doc: doc, state: state, unit: unitOf(doc), tool: doc.tool, target: (doc.target || {}).key || null, rater: rater, origin: doc.origin, status: doc.status };
    });
    var conflicts = plan.filter(function (x) { return x.state === 'conflict'; });
    if (conflicts.length && !b.replace) {
      return fail('CONFLICT', 409, String(conflicts.length) + ' értékelés már létezik ugyanezzel az értékelővel, más tartalommal. Ha a beérkezett változat a helyes, importáld újra „felülírás” jelöléssel.',
        { conflicts: conflicts.map(function (x) { return { path: x.path, unit: x.unit, tool: x.tool, target: x.target, rater: x.rater }; }) });
    }
    plan.forEach(function (x) { if (x.state !== 'same') { S.docs[x.path] = clone(x.doc); S.etags[x.path] = (S.etags[x.path] || 0) + 1; } });
    return ok('szk.ma.appraisal-import/v1', { schema: 'szk.ma.appraisal-import/v1', source: b.path || null,
      imported: plan.map(function (x) { return { path: x.path, unit: x.unit, tool: x.tool, target: x.target, rater: x.rater, etag: etag(x.path), origin: x.origin, status: x.status,
        state: x.state === 'conflict' ? 'replaced' : x.state }; }) });
  });

  FX.route('POST', '/api/appraisals/export', function (req) {
    init();
    var b = req.body || {};
    if (b.all) {
      var items = Object.keys(S.docs).sort().filter(function (p) { var x = parse(p); return (!b.rater || x.rater === b.rater) && (!b.tool || x.tool === b.tool); })
        .map(function (p) { return clone(S.docs[p]); });
      if (!items.length) { return fail('NOT_FOUND', 404, 'Nincs exportálható értékelés ezzel a szűréssel.'); }
      return ok('szk.ma.appraisal-export/v1', { schema: 'szk.ma.appraisal-export/v1', filename: 'ertekelesek_' + (b.rater || 'mind') + '.json',
        bundle: { schema: 'szk.appraisal-bundle/v1', exported_at: now(), rater: b.rater || null, items: items }, n: items.length });
    }
    var p = pathOf(b.unit, b.tool, b.target || null, b.rater);
    if (!S.docs[p]) { return fail('NOT_FOUND', 404, 'Nincs ilyen értékelés: ' + p); }
    return ok('szk.ma.appraisal-export/v1', { schema: 'szk.ma.appraisal-export/v1', filename: p.slice(DIR.length), doc: clone(S.docs[p]), sha256: null, path: p });
  });

  FX.route('POST', '/api/appraisals/rob-sync', function (req) {
    init();
    var b = req.body || {};
    var fx = FX.routes.filter(function (r) { return r._file === 'appraisal_robsync.json'; })[0];
    var d = clone(fx.envelope.data);
    d.etag = S.table_etag;
    if (b.dry_run === false) {
      if (!req.headers['If-Match']) { return fail('BAD_REQUEST', 400, 'Az alkalmazáshoz If-Match kell (az előnézetben látott tábla ETag-je).'); }
      if (req.headers['If-Match'] !== S.table_etag) { return fail('CONFLICT', 409, 'A(z) ' + d.dataset + ' időközben megváltozott (például Excelben). Töltsd be újra az előnézetet.', { dataset: d.dataset, etag: S.table_etag }); }
      S.table_etag = '"tbl-' + String(S.rev + 2) + '"';
      d.etag = S.table_etag;
      d.applied = true;
      d.provenance_etag = '"prov-' + String(S.rev) + '"';
      return ok(d.schema, d, d.etag);
    }
    return ok(d.schema, d, d.etag);
  });

  MA.dev.appraisal = {
    state: function () { init(); return S; },
    doc: function (p) { init(); return S.docs[p] ? clone(S.docs[p]) : null; },
    bumpEtag: function (p) { init(); if (p === 'table') { S.table_etag = '"tbl-x' + String(S.rev) + '"'; } else { S.etags[p] = (S.etags[p] || 0) + 1; } }
  };
})();
