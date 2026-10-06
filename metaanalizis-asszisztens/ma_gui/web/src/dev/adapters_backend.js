/* dev/adapters_backend.js — CSAK a fejlesztői buildben (build_gui.py --dev, ?fixtures=1).
 *
 * Állapottartó háttér az adapter-képernyőkhöz: GET /api/adapters, GET /api/figures, POST /api/figures/export (409 →
 * felülírás), POST /api/figures/audit, GET /api/prisma/composer, PUT …/config, POST …/refresh (előnézet, kézi számok
 * felülírása megerősítéssel), POST /api/validator/check. A válaszok a VALÓDI szerver rögzített borítékai
 * (web/fixtures/adapters_*.json; tests/gui/ui/gen_adapters_fixtures.py) — ez a fájl csak a „világot” és a
 * sorrendet választja a ``query.fx`` jelölő szerint; számot nem állít elő.
 * Tesztelői segédek: MA.dev.adapters.{scenario('legacy'|'h5'|'ok'), manualNumbers(true|false), state()}.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var FX = MA.dev && MA.dev.fixtures;
  if (!FX) { return; }

  var S = { scenario: 'legacy', exported: {}, comp: 'unconfigured', manual: false };
  function clone(o) { return o === undefined ? undefined : JSON.parse(JSON.stringify(o)); }

  function fx(file, method, path, name, extra) {
    var hit = null;
    FX.routes.forEach(function (r) {
      if (hit || r._file !== file || (r.method || 'GET') !== method || r.path !== path) { return; }
      var q = r.query || {};
      if (q.fx !== name) { return; }
      if (extra && Object.keys(extra).some(function (k) { return q[k] !== extra[k]; })) { return; }
      hit = r;
    });
    if (!hit) {
      return { status: 404, envelope: { ok: false, error: { code: 'NOT_FOUND', http: 404, message: 'Nincs fixture: ' + method + ' ' + path + ' fx=' + name } } };
    }
    return { status: hit.status || 200, etag: hit.etag || null, envelope: clone(hit.envelope) };
  }

  // ---------------------------------------------------------------- képességek
  FX.route('GET', '/api/adapters', function () { return fx('adapters_caps.json', 'GET', '/api/adapters', S.scenario); });

  // ---------------------------------------------------------------- ábrák
  FX.route('GET', '/api/figures', function (req) {
    if (!req.query.run) { return fx('adapters_figures.json', 'GET', '/api/figures', 'legacy-norun'); }
    if (S.scenario === 'legacy') {
      return fx('adapters_figures.json', 'GET', '/api/figures', Object.keys(S.exported).length ? 'legacy-after' : 'legacy');
    }
    return fx('adapters_figures.json', 'GET', '/api/figures', S.scenario);
  });
  FX.route('POST', '/api/figures/export', function (req) {
    var b = req.body || {};
    var stem = b.stem || ('fig_' + b.kind + '_o1');
    if (S.scenario === 'h5') { return fx('adapters_figures.json', 'POST', '/api/figures/export', 'h5'); }
    if (S.scenario === 'ok' && b.renderer === 'figure-forge') {
      return fx('adapters_figures.json', 'POST', '/api/figures/export', /bad/.test(stem) ? 'issues' : 'ff');
    }
    if (S.exported[stem] && !b.overwrite) { return fx('adapters_figures.json', 'POST', '/api/figures/export', 'conflict'); }
    S.exported[stem] = true;
    return fx('adapters_figures.json', 'POST', '/api/figures/export', 'legacy');
  });
  FX.route('POST', '/api/figures/audit', function () { return fx('adapters_figures.json', 'POST', '/api/figures/audit', 'legacy'); });

  // ---------------------------------------------------------------- composer
  function compView(name) { return fx('adapters_composer.json', name === 'configured' ? 'PUT' : 'GET', name === 'configured' ? '/api/prisma/composer/config' : '/api/prisma/composer', name); }
  FX.route('GET', '/api/prisma/composer', function () {
    if (S.scenario === 'h5') { return compView('absent'); }
    if (S.comp === 'configured' && S.manual) { return compView('manual'); }
    return compView(S.comp);
  });
  FX.route('PUT', '/api/prisma/composer/config', function () {
    if (S.comp === 'unconfigured') { S.comp = 'configured'; }
    return compView('configured');
  });
  FX.route('POST', '/api/prisma/composer/refresh', function (req) {
    var b = req.body || {};
    if (S.comp === 'unconfigured') {
      return { status: 400, envelope: { ok: false, error: { code: 'BAD_REQUEST', http: 400, message: 'Előbb add meg a composer kimeneti mappáját és a projekt nevét.' } } };
    }
    if (b.dry_run) { return fx('adapters_composer.json', 'POST', '/api/prisma/composer/refresh', 'preview'); }
    if (S.manual && S.comp !== 'composer' && !b.confirm_replace_manual) {
      return fx('adapters_composer.json', 'POST', '/api/prisma/composer/refresh', 'needs-confirm');
    }
    S.comp = 'composer';
    return fx('adapters_composer.json', 'POST', '/api/prisma/composer/refresh', 'written');
  });

  // ---------------------------------------------------------------- validator
  FX.route('POST', '/api/validator/check', function (req) {
    if (S.scenario === 'h5') { return fx('adapters_validator.json', 'POST', '/api/validator/check', 'absent'); }
    if (S.scenario === 'ok') { return fx('adapters_validator.json', 'POST', '/api/validator/check', 'json'); }
    var tool = ((req.body || {}).doc || {}).tool || 'rob2';
    var r = fx('adapters_validator.json', 'POST', '/api/validator/check', 'legacy', { tool: tool });
    return r.status === 404 ? fx('adapters_validator.json', 'POST', '/api/validator/check', 'legacy', { tool: 'rob2' }) : r;
  });

  MA.dev.adapters = {
    scenario: function (name) { S.scenario = name; S.exported = {}; S.comp = name === 'legacy' ? 'unconfigured' : S.comp; },
    manualNumbers: function (on) { S.manual = !!on; },
    configure: function () { S.comp = 'configured'; },
    state: function () { return clone(S); }
  };
})();
