/* dev/grade_backend.js — CSAK a fejlesztői buildben (build_gui.py --dev, ?fixtures=1).
 *
 * Állapottartó háttér a GRADE / SoF / Protokoll képernyőkhöz (3.5.12, „1 Protokoll”):
 *   GET/PUT /api/grade/<kimenet> (If-Match; indoklás nélküli ítélet → 422; rögzítés feloldatlan publikációs
 *   torzítással → 409 GATE_BLOCKED X019; hiányzó domén → 409), GET/PUT /api/sof/<kimenet> (dry_run, If-Match),
 *   POST /api/sof/<kimenet>/export, GET/PUT /api/protocol (If-Match → 409).
 * A kiinduló adatok a web/fixtures/grade.json (tests/gui/ui/gen_grade_fixtures.py: a motor-fixtúrákból).
 * FIGYELEM: ez nem a motor és nem a szerver: a bizonyosság itt csak a lépések egyszerű összege (a termékben a motor
 * számolja); a SoF előnézete a fixture motor-szövegeiből épül, külső alapkockázatnál abszolút hatás nélkül.
 * Tesztelői segédek: MA.dev.grade.{state(), engineMissing(true|false), bumpEtag('grade'|'protocol', kimenet?)}.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var FX = MA.dev && MA.dev.fixtures;
  if (!FX) { return; }

  var LEVELS = ['very low', 'low', 'moderate', 'high'];
  var S = null;
  function clone(o) { return o === undefined ? undefined : JSON.parse(JSON.stringify(o)); }
  function jsonData(method, path) {
    var hit = null;
    FX.routes.forEach(function (r) {
      if (!hit && r._file === 'grade.json' && (r.method || 'GET') === method && r.path === path && !r.query) { hit = r; }
    });
    return hit ? clone(hit.envelope.data) : null;
  }
  function ok(schema, d, etag) {
    S.rev += 1;
    return { status: 200, etag: etag || null, envelope: { ok: true, schema: schema, data: d, warnings: [], meta: { engine: '0.2.0', elapsed_ms: 1, project_rev: S.rev, request_id: 'q_dg' + String(S.rev) } } };
  }
  function fail(code, http, message, details) {
    var e = { code: code, http: http, message: message };
    if (details) { e.details = details; }
    return { status: http, envelope: { ok: false, error: e } };
  }
  function now() { return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'); }
  function oidOf(req) { return decodeURIComponent(req.path.split('/')[3] || ''); }
  function init() {
    if (S) { return S; }
    S = { rev: 61, n: 1, grade: {}, gradeEtag: {}, journal: {}, sof: {}, sofEtag: {}, protocol: jsonData('GET', '/api/protocol'),
      protocolEtag: '"pr-1"', missing: false };
    return S;
  }
  function etagNo(prefix) { S.n += 1; return '"' + prefix + '-' + String(S.n) + '"'; }

  // ---------------------------------------------------------------- GRADE
  var DOMAIN_HU = { risk_of_bias: 'torzítási kockázat', inconsistency: 'inkonzisztencia', indirectness: 'indirektség',
    imprecision: 'pontatlanság', publication_bias: 'publikációs torzítás' };
  function problems(doc) {
    var errors = [], unresolved = [], missing = [];
    Object.keys(DOMAIN_HU).forEach(function (d) {
      var x = (doc.domains || {})[d] || {};
      if (!x.rating) { missing.push(d); return; }
      if (!(x.rationale || '').trim()) { errors.push(DOMAIN_HU[d] + ': az ítélethez indoklás kell'); }
      if (d === 'publication_bias' && x.rating === 'suspected' && (x.step === null || x.step === undefined)) { unresolved.push(d); }
    });
    Object.keys(doc.upgrades || {}).forEach(function (u) {
      if (doc.upgrades[u] && !(((doc.upgrade_details || {})[u] || {}).rationale || '').trim()) { errors.push(u + ': a felminősítéshez indoklás kell'); }
    });
    return { errors: errors, unresolved: unresolved, missing: missing };
  }
  function certainty(doc, p) {
    if (p.missing.length || p.unresolved.length) { return null; }
    var lvl = doc.start === 'low' ? 2 : 4;
    Object.keys(DOMAIN_HU).forEach(function (d) { lvl += doc.domains[d].step || 0; });
    Object.keys(doc.upgrades || {}).forEach(function (u) { if (doc.upgrades[u]) { lvl += ((doc.upgrade_details || {})[u] || {}).step || 0; } });
    if (lvl < 1) { lvl = 1; }
    if (lvl > 4) { lvl = 4; }
    return LEVELS[lvl - 1];
  }
  function view(oid) {
    var v = jsonData('GET', '/api/grade/' + oid);
    if (!v) { return null; }
    var g = S.grade[oid] || null;
    var p = g ? problems(g) : { unresolved: [], missing: v.vocab.domains.slice() };
    v.grade = clone(g);
    v.unresolved = p.unresolved;
    v.missing = p.missing;
    v.run_matches = g && v.run ? g.run_id === v.run.run_id : null;
    if (S.journal[oid]) { v.journal = clone(S.journal[oid]); }
    return v;
  }

  FX.route('GET', '/api/grade/*', function (req) {
    init();
    var oid = oidOf(req);
    var v = view(oid);
    if (!v) { return fail('NOT_FOUND', 404, 'Nincs ilyen kimenet a projektben (ma-projekt.json → outcomes).'); }
    if (S.missing) {
      v.engine = Object.assign({}, v.engine, { grade_get: false, grade_put: false });
      return fail('CAPABILITY_MISSING', 424, 'A motor ebben a változatban még nem tudja ezt: a GRADE-ítélet beolvasása (hiányzik: metaelemzes.api.grade_get). Frissítsd a motort (a metaanalizis-asszisztens új változata), majd indítsd újra a munkapadot. A többi funkció addig is működik.',
        { feature: 'grade_get', missing: 'metaelemzes.api.grade_get', view: v });
    }
    return ok('szk.ma.grade-view/v1', v, S.gradeEtag[oid] || null);
  });

  FX.route('PUT', '/api/grade/*', function (req) {
    init();
    var oid = oidOf(req);
    var v = jsonData('GET', '/api/grade/' + oid);
    if (!v) { return fail('NOT_FOUND', 404, 'Nincs ilyen kimenet a projektben.'); }
    if (S.missing) { return fail('CAPABILITY_MISSING', 424, 'A motor ebben a változatban még nem tudja ezt: a GRADE-ítélet mentése (hiányzik: metaelemzes.api.grade_put).'); }
    var cur = S.gradeEtag[oid] || null;
    var im = req.headers['If-Match'] || null;
    if (cur && im !== cur) { return fail('CONFLICT', 409, 'A dokumentumot közben más is módosította (például egy ágens vagy egy másik lap). Töltsd be újra, és ismételd meg a módosítást.', { etag: cur }); }
    var b = req.body || {};
    var doc = clone(b.grade || {});
    if (!v.run) { return fail('NOT_FOUND', 404, 'Ehhez a kimenethez még nincs rögzített (commit) futás.'); }
    var p = problems(doc);
    if (p.errors.length) { return fail('VALIDATION', 422, 'A GRADE-ítélet hiányos vagy ellentmondásos: ' + p.errors.join('; ') + '.', { errors: p.errors }); }
    if (b.record === true) {
      if (p.missing.length) { return fail('GATE_BLOCKED', 409, 'A rögzítéshez mind az öt domén ítélete kell; hiányzik: ' + p.missing.map(function (d) { return DOMAIN_HU[d]; }).join(', ') + '.', { missing: p.missing }); }
      if (p.unresolved.length) {
        return fail('GATE_BLOCKED', 409, 'A publikációs torzítás „gyanított” (suspected) ítélete feloldatlan (X019, 11/4. döntés): válaszd ki, hogy 0 vagy −1 lépés legyen, és indokold; addig a GRADE nem rögzíthető.',
          { unresolved: p.unresolved, code: 'X019', kb_refs: ['X019'] });
      }
    }
    doc.schema = 'szk.ma.grade/v1';
    doc.outcome_id = oid;
    doc.run_id = v.run.run_id;
    var pb = doc.domains.publication_bias || {};
    pb.status = pb.rating === 'suspected' && (pb.step === null || pb.step === undefined) ? 'unresolved' : (pb.rating ? 'resolved' : null);
    doc.certainty = certainty(doc, p);
    doc.consistency_warning = null;
    doc.validator_rollup = null;
    S.grade[oid] = doc;
    S.gradeEtag[oid] = etagNo('grade-' + oid);
    var out = view(oid);
    out.recorded = null;
    if (b.record === true) {
      S.n += 1;
      S.journal[oid] = { id: S.n, ts: now(), certainty: doc.certainty };
      out.journal = clone(S.journal[oid]);
      out.recorded = { id: S.n, certainty: doc.certainty, warnings: [] };
    }
    return ok('szk.ma.grade-view/v1', out, S.gradeEtag[oid]);
  });

  // ---------------------------------------------------------------- SoF
  function sofView(oid) {
    var v = jsonData('GET', '/api/sof/' + oid);
    if (!v) { return null; }
    var st = S.sof[oid];
    if (st) { v.saved = clone(st.saved); v.saved_matches_run = st.saved ? true : null; v.inputs = clone(st.inputs); v.preview = clone(st.preview || v.preview); }
    var g = S.grade[oid];
    if (g && g.certainty) { v.certainty = g.certainty; v.certainty_source = 'grade'; }
    return v;
  }
  function preview(oid, inputs) {
    var base = jsonData('GET', '/api/sof/' + oid).preview;
    var row0 = base.rows[0];
    // a motor sof()-ja egy sort ad, alapkockázatonként „; ”-vel összefűzött cellákkal; a fejlesztői háttér a külső
    // alapkockázatot külön sorként mutatja, abszolút hatás nélkül (azt a termékben a motor számolja)
    base.rows = inputs.assumed_risks.map(function (r) {
      var row = clone(row0);
      if (r.source === 'external') {
        var lbl = r.label || 'külső';
        row.assumed_risk_text = { hu: lbl + ': ' + String(r.per_1000) + ' / 1000 fő', en: lbl + ': ' + String(r.per_1000) + ' per 1,000' };
        row.absolute_text = { hu: '—', en: '—' };
        row.sources = Object.assign({}, row.sources, { absolute: 'fixture: a külső alapkockázat abszolút hatását a motor sof() számolja' });
      }
      return row;
    });
    var used = base.footnotes.length;
    (inputs.footnotes || []).forEach(function (n, i) { base.footnotes.push({ id: String.fromCharCode(97 + used + i), text: { hu: n.text, en: n.text }, kind: 'user' }); });
    return base;
  }

  FX.route('GET', '/api/sof/*', function (req) {
    init();
    var oid = oidOf(req);
    var v = sofView(oid);
    if (!v) { return fail('NOT_FOUND', 404, 'Ehhez a kimenethez még nincs rögzített (commit) futás. A GRADE és a SoF csak commit-futásra hivatkozhat, hogy minden közölt szám reprodukálható legyen: Elemzés fül → Rögzítés (commit).'); }
    return ok('szk.ma.sof-view/v1', v, S.sofEtag[oid] || null);
  });

  FX.route('PUT', '/api/sof/*', function (req) {
    init();
    var oid = oidOf(req);
    var v = sofView(oid);
    if (!v) { return fail('NOT_FOUND', 404, 'Ehhez a kimenethez még nincs rögzített (commit) futás.'); }
    var b = req.body || {};
    var risks = b.assumed_risks || [];
    for (var i = 0; i < risks.length; i++) {
      if (risks[i].source === 'external' && !risks[i].per_1000) {
        return fail('VALIDATION', 422, 'A külső alapkockázathoz add meg az értékét (esemény / 1000 fő), és nevezd meg a forrását (' + String(i + 1) + '. alapkockázat).', { field: 'per_1000', index: i });
      }
    }
    var inputs = { assumed_risks: risks, footnotes: (b.footnotes || []).map(function (n) { return { id: null, text: n.text, source: 'user' }; }) };
    var pv = preview(oid, inputs);
    pv.inputs = inputs;
    if (b.dry_run === true) {
      v.preview = pv;
      return ok('szk.ma.sof-view/v1', v, S.sofEtag[oid] || null);
    }
    var cur = S.sofEtag[oid] || null;
    var im = req.headers['If-Match'] || null;
    if (cur !== im) { return fail('CONFLICT', 409, 'A(z) 06_kezirat/sof/' + oid + '.sof.json időközben megváltozott. Töltsd be újra.', { etag: cur }); }
    S.sof[oid] = { saved: pv, inputs: inputs, preview: pv };
    S.sofEtag[oid] = etagNo('sof-' + oid);
    var out = sofView(oid);
    return ok('szk.ma.sof-view/v1', out, S.sofEtag[oid]);
  });

  FX.route('POST', '/api/sof/*/export', function (req) {
    init();
    var oid = oidOf(req);
    var st = S.sof[oid];
    if (!st || !st.saved) { return fail('NOT_FOUND', 404, 'Még nincs mentett SoF-tábla ehhez a kimenethez: előbb nézd meg az előnézetet, és mentsd el (GRADE/SoF fül → SoF → Mentés).'); }
    var b = req.body || {};
    var lang = b.lang || 'en';
    var path = '06_kezirat/sof/' + oid + '.sof.' + lang + '.' + b.format;
    var out = { schema: 'szk.ma.sof-export/v1', format: b.format, lang: lang, path: path, sha256: 'f'.repeat(64), bytes: 1,
      url: '/f/_run%3A' + encodeURIComponent(path) + '/1791190000/fxsig', expires_at: 1791190000, run_id: st.saved.run_id };
    if (b.format === 'md') {
      var rows = st.saved.rows.map(function (r) { return '| ' + [r.outcome_text[lang], r.relative_text[lang], r.absolute_text[lang]].join(' | ') + ' |'; });
      out.content = ['## SoF', '', '| Outcome | Relative | Absolute |', '|---|---|---|'].concat(rows).join('\n') + '\n';
    }
    return ok('szk.ma.sof-export/v1', out);
  });

  // ---------------------------------------------------------------- protokoll
  FX.route('GET', '/api/protocol', function () {
    init();
    return ok('szk.ma.protocol/v1', clone(S.protocol), S.protocolEtag);
  });

  FX.route('PUT', '/api/protocol', function (req) {
    init();
    var im = req.headers['If-Match'] || null;
    if (im !== S.protocolEtag) { return fail('CONFLICT', 409, 'A(z) ma-projekt.json időközben megváltozott (például Excelben vagy egy ágens írta át): 0 eltérő cella, ebből 0 ütközik a te módosításoddal. Töltsd be újra, vagy fésüld össze a változásokat.', { etag: S.protocolEtag }); }
    var b = req.body || {};
    var p = S.protocol;
    var changed = [];
    if ('title' in b && b.title !== p.title) {
      if (!(b.title || '').trim()) { return fail('VALIDATION', 422, 'A projekt címe nem lehet üres.', { field: 'title' }); }
      p.title = b.title; changed.push('title');
    }
    if (b.question) {
      var q = Object.assign({}, p.question);
      ['P', 'I', 'C', 'O'].forEach(function (k) { if (k in b.question) { q[k] = b.question[k] || ''; } });
      if (JSON.stringify(q) !== JSON.stringify(p.question)) { p.question = q; changed.push('question'); }
    }
    if (b.review_type && b.review_type !== p.review_type) { p.review_type = b.review_type; changed.push('review_type'); }
    if ('registration' in b && JSON.stringify(b.registration || null) !== JSON.stringify(p.registration || null)) {
      p.registration = b.registration && (b.registration.registry || b.registration.id) ? { registry: b.registration.registry || null, id: b.registration.id || null } : null;
      changed.push('registration');
    }
    if (changed.length) { S.protocolEtag = etagNo('pr'); }
    var out = clone(p);
    out.changed = changed;
    return ok('szk.ma.protocol/v1', out, S.protocolEtag);
  });

  MA.dev.grade = {
    state: function () { return init(); },
    engineMissing: function (on) { init().missing = on !== false; },
    bumpEtag: function (what, oid) {
      init();
      if (what === 'protocol') { S.protocolEtag = etagNo('pr-ext'); } else { S.gradeEtag[oid || 'o1'] = etagNo('grade-ext'); }
    }
  };
})();
