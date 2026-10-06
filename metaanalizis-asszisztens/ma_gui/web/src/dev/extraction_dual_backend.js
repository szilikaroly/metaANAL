/* dev/extraction_dual_backend.js — CSAK a fejlesztői buildben (build_gui.py --dev, ?fixtures=1).
 *
 * Állapottartó háttér a kettős kinyerés képernyőhöz (3.5.5): GET /api/kettos, POST /api/compare, POST /api/reconcile
 * (döntés indoklással, visszavonás, If-Match → 409, feloldatlan eltérésnél a CSV-írás 409 GATE_BLOCKED X009, dry_run),
 * POST /api/kettos/import — új / ugyanaz / felülírás; a felülírás a korábbi döntéseket elavulttá teszi —,
 * POST /api/kettos/export. A kiinduló adatok a web/fixtures/extraction_dual.json (tests/gui/ui/gen_extraction_fixtures.py:
 * a munkapad VALÓDI szerverének borítékai). FIGYELEM: ez nem a szerver és nem a motor — az összevetés (eltérések,
 * súgók, hatás, κ) a fixture-ből jön változatlanul; itt csak a döntések hozzárendelése és a darabszámok (a szerver
 * build_items-ének egyszerű mása) történik.
 * Tesztelői segédek: MA.dev.dual.{state(), engineMissing(true|false), bumpEtag(kimenet), reset()}.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var FX = MA.dev && MA.dev.fixtures;
  if (!FX) { return; }

  var FILE = 'extraction_dual.json';
  var SEP = '␟';
  var S = null;

  function clone(o) { return o === undefined ? undefined : JSON.parse(JSON.stringify(o)); }
  function fixture(method, path, body) {
    var hit = null;
    FX.routes.forEach(function (r) {
      if (hit || r._file !== FILE || (r.method || 'GET') !== method || r.path !== path) { return; }
      if (body && JSON.stringify(r.body || null) !== JSON.stringify(body)) { return; }
      hit = r;
    });
    return hit;
  }
  function ok(schema, d, etag, warnings) {
    S.rev += 1;
    return { status: 200, etag: etag || null, envelope: { ok: true, schema: schema, data: d, warnings: warnings || [],
      meta: { engine: '0.2.0', elapsed_ms: 1, project_rev: S.rev, request_id: 'q_dd' + String(S.rev) } } };
  }
  function fail(code, http, message, details) {
    var e = { code: code, http: http, message: message };
    if (details) { e.details = details; }
    return { status: http, envelope: { ok: false, error: e } };
  }
  function now() { return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'); }
  function b64(text) {
    var bytes = new TextEncoder().encode(text);
    var s = '';
    for (var i = 0; i < bytes.length; i++) { s += String.fromCharCode(bytes[i]); }
    return window.btoa(s);
  }

  function init() {
    if (S) { return S; }
    var lst = fixture('GET', '/api/kettos');
    S = { rev: 90, n: 1, missing: false, files: {}, decisions: {}, etag: {}, csv: {}, raters: {} };
    (lst ? lst.envelope.data.outcomes : []).forEach(function (o) {
      S.files[o.id] = { a: !!o.a.exists, b: !!o.b.exists };
      S.decisions[o.id] = {};
      S.etag[o.id] = o.consensus.etag ? '"' + o.consensus.etag + '"' : null;
      S.csv[o.id] = null;
      S.raters[o.id] = clone(o.consensus.raters || {});
    });
    return S;
  }
  function etagNo() { S.n += 1; return '"dx-' + String(S.n) + '"'; }
  function bare(tag) { return tag ? String(tag).replace(/^W\//, '').replace(/^"|"$/g, '') : null; }

  // ---------------------------------------------------------------- nézet (a szerver build_items-ének mása)
  function view(oid, tables) {
    var r = fixture('POST', '/api/compare', { outcome: oid });
    if (!r) { return null; }
    var v = clone(r.envelope.data);
    var dec = S.decisions[oid] || {};
    var p = { total: 0, decided: 0, unresolved: 0, auto: 0, stale: 0, orphans: 0 };
    var seen = {};
    v.items.forEach(function (it) {
      var k = it.key + SEP + it.field;
      var d = dec[k] || null;
      seen[k] = true;
      it.decision = d ? { chosen: d.chosen, value: d.value, reason: d.reason, actor: d.actor, ts: d.ts } : null;
      it.stale = !!(d && it.level === 'cell' && (d.a !== it.a || d.b !== it.b));
      if (it.auto) { p.auto += 1; }
      if (it.stale) { p.stale += 1; }
      if (it.needs_decision) {
        p.total += 1;
        if (d && !it.stale) { p.decided += 1; }
      }
    });
    p.unresolved = p.total - p.decided;
    p.orphans = Object.keys(dec).filter(function (k) { return !seen[k]; }).length;
    v.progress = p;
    v.gate = { code: 'X009', blocked: p.unresolved > 0,
      message: p.unresolved ? String(p.unresolved) + ' feloldatlan eltérés: amíg van ilyen, az S08 PASS nem rögzíthető (X009), és a konszenzus-CSV nem írható ki.'
        : 'Nincs feloldatlan eltérés (X009 rendben).' };
    var et = S.etag[oid];
    v.consensus = Object.assign({}, v.consensus, { exists: !!et, etag: bare(et), csv: S.csv[oid], decisions: Object.keys(dec).length });
    v.files.consensus = Object.assign({}, v.files.consensus, { etag: bare(et), exists: !!et });
    if (S.csv[oid]) { v.files.csv = { path: S.csv[oid].path, exists: true, etag: S.csv[oid].sha256, bytes: 1024 }; }
    v.raters = clone(S.raters[oid] || v.raters);
    if (!tables) { delete v.a; delete v.b; }
    return v;
  }

  FX.route('GET', '/api/kettos', function () {
    init();
    var lst = clone(fixture('GET', '/api/kettos').envelope.data);
    lst.outcomes.forEach(function (o) {
      var f = S.files[o.id] || {};
      o.a.exists = !!f.a;
      o.b.exists = !!f.b;
      o.consensus.exists = !!S.etag[o.id];
      o.consensus.etag = bare(S.etag[o.id]);
      o.consensus.decisions = Object.keys(S.decisions[o.id] || {}).length;
      o.consensus.raters = clone(S.raters[o.id] || {});
      o.consensus.csv = S.csv[o.id];
      o.csv.exists = !!S.csv[o.id];
    });
    lst.inbox = lst.inbox.filter(function (x) { return !(x.outcome_guess && S.files[x.outcome_guess] && S.files[x.outcome_guess][String(x.side_guess || '').toLowerCase()]); });
    if (S.missing) { lst.engine = Object.assign({}, lst.engine, { available: false, missing: ['compare'] }); }
    return ok('szk.ma.dual-list/v1', lst);
  });

  function missing() {
    var r = fixture('POST', '/api/compare', { outcome: 'o1', _engine: 'missing' });
    return { status: 424, envelope: clone(r.envelope) };
  }

  FX.route('POST', '/api/compare', function (req) {
    init();
    var b = req.body || {};
    if (S.missing) { return missing(); }
    var f = S.files[b.outcome];
    if (!f) { return fail('NOT_FOUND', 404, 'Nincs ilyen kimenet a projektben: ' + b.outcome + '.'); }
    if (!f.a || !f.b) { return fail('NOT_FOUND', 404, 'Hiányzik a(z) ' + (f.a ? 'B' : 'A') + ' tábla. Importáld a kinyerő CSV-jét.'); }
    var v = view(b.outcome, b.tables !== false);
    if (!v) { return fail('NOT_FOUND', 404, 'Nincs fixture ehhez a kimenethez.'); }
    return ok('szk.ma.dual-view/v1', v, S.etag[b.outcome]);
  });

  FX.route('POST', '/api/reconcile', function (req) {
    init();
    var b = req.body || {};
    var oid = b.outcome;
    if (S.missing) { return missing(); }
    var f = S.files[oid];
    if (!f || !f.a || !f.b) { return fail('NOT_FOUND', 404, 'Hiányzik az A vagy a B tábla.'); }
    var dry = b.dry_run === true;
    var cur = S.etag[oid] || null;
    var im = req.headers['If-Match'] || null;
    if (!dry && bare(im) !== bare(cur)) {
      return fail('CONFLICT', 409, 'A konszenzus-fájlt közben más is módosította (például a másik ablakban vagy egy ágens). Töltsd újra az összevetést, és döntsd el újra a módosított tételeket.', { etag: bare(cur) });
    }
    var base = view(oid, false);
    var byKey = {};
    base.items.forEach(function (it) { byKey[it.key + SEP + it.field] = it; });
    var dec = clone(S.decisions[oid] || {});
    var changed = 0, cleared = 0;
    var list = b.decisions || [];
    for (var i = 0; i < list.length; i++) {
      var d = list[i];
      var where = String(i + 1) + '. döntés (mező: ' + d.field + ')';
      var it = byKey[d.key + SEP + d.field];
      if (!it) { return fail('VALIDATION', 422, 'A ' + where + ' már nem tartozik eltéréshez (a táblák közben változhattak). Töltsd újra az összevetést.', { index: i }); }
      if (!it.needs_decision) { return fail('VALIDATION', 422, 'A ' + where + ' csak írásmódban tér el (' + it.kind + '): nem kell dönteni.', { index: i }); }
      if (it.level === 'row' && d.chosen === 'other') { return fail('VALIDATION', 422, 'A ' + where + ' egy egész sorra vonatkozik: csak az A vagy a B változata választható.', { index: i }); }
      if (!String(d.reason || '').trim()) {
        return fail('VALIDATION', 422, 'Indoklás nélkül nem rögzíthető döntés: ' + where + '. Írd le röviden, miért ez a helyes érték (pl. „Table 2 lábjegyzete: SD”).', { index: i, missing: 'reason' });
      }
      var value = it.level === 'row' ? null : d.chosen === 'a' ? it.a : d.chosen === 'b' ? it.b : d.value;
      if (d.chosen === 'other' && (value === null || value === undefined)) { return fail('VALIDATION', 422, 'Saját érték választásához add meg az értéket: ' + where + '.', { index: i, missing: 'value' }); }
      dec[d.key + SEP + d.field] = { chosen: d.chosen, value: value, reason: String(d.reason).trim(), actor: b.actor || 'user', ts: now(), a: it.a, b: it.b };
      changed += 1;
    }
    (b.clear || []).forEach(function (c) { var k = c.key + SEP + c.field; if (dec[k]) { delete dec[k]; cleared += 1; } });
    var saved = S.decisions[oid];
    S.decisions[oid] = dec;
    var v = view(oid, false);
    var written = { csv: null, changed: changed, cleared: cleared, decision_id: null };
    if (b.write) {
      if (v.progress.unresolved) {
        S.decisions[oid] = saved;
        return fail('GATE_BLOCKED', 409, String(v.progress.unresolved) + ' eltérés még feloldatlan (vagy a döntése elavult): a konszenzus-CSV csak akkor írható ki, ha minden eltérésről döntöttél (X009).',
          { code: 'X009', unresolved: v.progress.unresolved, blockers: [{ code: 'X009', title: 'Kettős kinyerés: feloldatlan eltérés (' + String(v.progress.unresolved) + ')' }] });
      }
      var target = b.write === 'outcome' ? (fixture('GET', '/api/kettos').envelope.data.outcomes.filter(function (o) { return o.id === oid; })[0] || {}).data : v.files.csv.path;
      if (b.write === 'outcome' && !dry && !b.outcome_if_match) {
        S.decisions[oid] = saved;
        return fail('CONFLICT', 409, 'A kimenet adattáblája (' + target + ') már létezik: a felülírásához a betöltött változat etagje kell (outcome_if_match).', { path: target });
      }
      var recon = Object.keys(dec).filter(function (k) { return k.split(SEP)[1] !== '*'; }).length;
      written.csv = { path: target, sha256: 'c0ffee' + String(S.n), prov: target.replace(/\.csv$/, '.prov.json'), rows: base.a ? base.a.n_rows : 14,
        reconciled: recon, builder: 'server', preview: null };
      if (!dry) {
        S.csv[oid] = { path: target, sha256: written.csv.sha256, prov: written.csv.prov, target: b.write, builder: 'server', rows: written.csv.rows,
          reconciled: recon, written_at: now(), by: b.actor || 'user' };
      }
    }
    if (dry) {
      S.decisions[oid] = saved;
      var vd = view(oid, false);
      vd.dry_run = true;
      vd.written = written;
      return ok('szk.ma.dual-view/v1', vd, cur);
    }
    if (changed || cleared || b.write || !cur) { S.etag[oid] = etagNo(); }
    v = view(oid, false);
    v.written = written;
    var warnings = [];
    if (changed || cleared) { warnings = []; }
    return ok('szk.ma.dual-view/v1', v, S.etag[oid], warnings);
  });

  FX.route('POST', '/api/kettos/import', function (req) {
    init();
    var b = req.body || {};
    var oid = b.outcome;
    var side = String(b.side || '').toUpperCase();
    var f = S.files[oid];
    if (!f) { return fail('NOT_FOUND', 404, 'Nincs ilyen kimenet a projektben: ' + oid + '.'); }
    if (side !== 'A' && side !== 'B') { return fail('BAD_REQUEST', 400, 'Az oldal A vagy B lehet.'); }
    if (!b.content_b64 && !b.path) { return fail('BAD_REQUEST', 400, 'Az import törzse {content_b64} vagy {path}.'); }
    if (b.path && b.path.indexOf('/kettos/beerkezett/') < 0) { return fail('FORBIDDEN', 403, 'Importálni csak a kettos/beerkezett/ mappából lehet (CSV-fájl).'); }
    var low = side.toLowerCase();
    var state = 'new';
    if (f[low]) {
      if (!b.replace) {
        return fail('CONFLICT', 409, 'A(z) ' + side + ' tábla már létezik, más tartalommal. Ha a most importált változat a helyes, importáld újra „felülírás” jelöléssel (a korábbi döntések elavulhatnak).');
      }
      state = 'replaced';
      // a korábbi döntések a régi táblára épültek → elavultak (a szerverben a tábla A/B szövege változik)
      var dec = S.decisions[oid] || {};
      Object.keys(dec).forEach(function (k) { if (k.split(SEP)[1] !== '*') { dec[k][low] = '(régi érték)'; } });
    }
    f[low] = true;
    if (b.rater) { S.raters[oid] = Object.assign({}, S.raters[oid], (function () { var o = {}; o[low] = b.rater; return o; })()); }
    var warnings = state === 'replaced' && Object.keys(S.decisions[oid] || {}).length
      ? ['A korábbi döntések erre a táblára épültek: az érintett tételeket az összevetés elavultként jelöli, ezeket újra el kell dönteni.'] : [];
    return ok('szk.ma.dual-import/v1', { outcome: oid, side: side, path: '03_adatok/kettos/' + oid + '.' + side + '.csv', state: state,
      etag: 'f00d' + String(S.rev), rows: 13, columns: 7, header: [], format: null, rater: b.rater || null, source: b.path || null }, null, warnings);
  });

  FX.route('POST', '/api/kettos/export', function (req) {
    init();
    var b = req.body || {};
    var side = String(b.side || '');
    if (side === 'consensus') {
      var c = S.csv[b.outcome];
      if (!c) { return fail('NOT_FOUND', 404, 'Nincs exportálható konszenzus tábla.'); }
      var text = 'konszenzus (fejlesztői háttér — a valódi fájl a szerveren készül)\n';
      return ok('szk.ma.dual-export/v1', { outcome: b.outcome, side: 'CONSENSUS', path: c.path, filename: c.path.split('/').pop(), template: false,
        content_b64: b64(text), sha256: c.sha256, bytes: text.length, rows: null, media_type: 'text/csv' });
    }
    var r = fixture('POST', '/api/kettos/export', { outcome: b.outcome, side: side.toUpperCase(), template: !!b.template });
    if (!r) { return fail('NOT_FOUND', 404, 'Nincs exportálható ' + side + ' tábla.'); }
    return ok('szk.ma.dual-export/v1', clone(r.envelope.data));
  });

  MA.dev.dual = {
    state: function () { return init(); },
    engineMissing: function (on) { init().missing = !!on; },
    bumpEtag: function (oid) { init(); S.etag[oid] = etagNo(); },
    reset: function () { S = null; init(); }
  };
})();
