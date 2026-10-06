/* dev/fixture_backend.js — CSAK a fejlesztői buildben (build_gui.py --dev).
 *
 * '?fixtures=1' esetén a hálózati réteget a beépített fixture-borítékokra cseréli
 * (web/fixtures/*.json → <script type="application/json" id="ma-fixtures">).
 * A termék-build ezt a fájlt és a fixture-adatokat nem tartalmazza (a build ellenőrzi).
 *
 * Fixture-fájl formátum (web/fixtures/<név>.json):
 *   { "description": "…", "routes": [
 *       { "method": "GET", "path": "/api/runs/*\/plot", "query": {"k": "v"}?, "body": {"k": "v"}?,
 *         "status": 200, "etag": "…"?, "delay_ms": 0?, "envelope": { "ok": true, … } }
 *     | { "method": …, "path": …, "sequence": [ {status, envelope, delay_ms}, … ] }   (sorban; az utolsó ismétlődik)
 *   ] }
 * Dinamikus válasz: MA.dev.fixtures.route(method, pathPattern, handler(req) → {status, envelope} | Promise).
 */
(function () {
  'use strict';
  var MA = window.MA;

  var FIXTURE_TOKEN = 'fx-token-0001';
  var params = new URLSearchParams(window.location.search);
  var active = params.get('fixtures') === '1';

  var routes = [];
  var handlers = [];
  var calls = [];

  function loadRoutes() {
    var node = document.getElementById('ma-fixtures');
    if (!node) { return; }
    var data = JSON.parse(node.textContent || '{}');
    Object.keys(data).sort().forEach(function (file) {
      (data[file].routes || []).forEach(function (r, i) {
        routes.push(Object.assign({ _file: file, _index: i, _pos: 0 }, r));
      });
    });
  }

  function splitPath(p) { return p.replace(/^\/+|\/+$/g, '').split('/'); }

  function matchPath(pattern, path) {
    var a = splitPath(pattern), b = splitPath(path);
    for (var i = 0; i < a.length; i++) {
      if (a[i] === '**') { return true; }
      if (i >= b.length) { return false; }
      if (a[i] !== '*' && a[i] !== b[i]) { return false; }
    }
    return a.length === b.length;
  }

  function subsetMatch(want, got) {
    if (!want) { return true; }
    if (!got || typeof got !== 'object') { return false; }
    return Object.keys(want).every(function (k) {
      var w = want[k], g = got[k];
      if (w && typeof w === 'object') { return JSON.stringify(w) === JSON.stringify(g); }
      return String(w) === String(g);
    });
  }

  function specificity(r) {
    return (r.query ? Object.keys(r.query).length : 0) + (r.body ? Object.keys(r.body).length : 0) +
      (r.path.indexOf('*') < 0 ? 1 : 0);
  }

  function envelopeError(code, http, message) {
    return { status: http, envelope: { ok: false, error: { code: code, http: http, message: message } } };
  }

  function sleep(ms, signal) {
    return new Promise(function (resolve, reject) {
      if (!ms) { resolve(); return; }
      var t = setTimeout(resolve, ms);
      if (signal) {
        signal.addEventListener('abort', function () {
          clearTimeout(t);
          var e = new Error('aborted');
          e.name = 'AbortError';
          reject(e);
        });
      }
    });
  }

  function parseQuery(url) {
    var out = {};
    var i = url.indexOf('?');
    if (i < 0) { return out; }
    new URLSearchParams(url.slice(i + 1)).forEach(function (v, k) { out[k] = v; });
    return out;
  }

  function pick(req) {
    for (var h = 0; h < handlers.length; h++) {
      if (handlers[h].method === req.method && matchPath(handlers[h].path, req.path)) { return { handler: handlers[h].fn }; }
    }
    var cands = routes.filter(function (r) {
      return (r.method || 'GET').toUpperCase() === req.method && matchPath(r.path, req.path) &&
        subsetMatch(r.query, req.query) && subsetMatch(r.body, req.body);
    });
    cands.sort(function (a, b) { return specificity(b) - specificity(a); });
    return cands.length ? { route: cands[0] } : null;
  }

  function respond(req) {
    var path = req.url.split('?')[0];
    var r = {
      method: req.method,
      path: path,
      query: parseQuery(req.url),
      body: req.body ? JSON.parse(req.body) : null,
      headers: Object.assign({}, req.headers)
    };
    calls.push(r);
    if (path !== '/api/session' && r.headers['X-MA-Token'] !== FIXTURE_TOKEN) {
      return Promise.resolve(envelopeError('FORBIDDEN', 403, 'Hiányzó vagy érvénytelen munkamenet-token.'));
    }
    if (req.method !== 'GET' && r.headers['Content-Type'] !== 'application/json') {
      return Promise.resolve(envelopeError('UNSUPPORTED_MEDIA', 415, 'A kérés törzse csak application/json lehet.'));
    }
    var hit = pick(r);
    if (!hit) {
      return Promise.resolve(envelopeError('NOT_FOUND', 404, 'Nincs fixture ehhez: ' + r.method + ' ' + path));
    }
    if (hit.handler) {
      return Promise.resolve(hit.handler(r)).then(function (res) {
        return { status: res.status || (res.envelope && res.envelope.ok === false ? res.envelope.error.http : 200), envelope: res.envelope, etag: res.etag, delay_ms: res.delay_ms };
      });
    }
    var route = hit.route;
    var step = route;
    if (Array.isArray(route.sequence) && route.sequence.length) {
      step = route.sequence[route._pos < route.sequence.length ? route._pos : route.sequence.length - 1];
      route._pos += 1;
    }
    return Promise.resolve({ status: step.status || 200, envelope: step.envelope, etag: step.etag || route.etag, delay_ms: step.delay_ms });
  }

  function fixtureTransport(req) {
    return respond(req).then(function (res) {
      return sleep(res.delay_ms || 0, req.signal).then(function () {
        if (req.signal && req.signal.aborted) {
          var e = new Error('aborted');
          e.name = 'AbortError';
          throw e;
        }
        return {
          status: res.status,
          etag: res.etag || null,
          contentType: 'application/json',
          text: JSON.stringify(res.envelope)
        };
      });
    });
  }

  MA.dev = MA.dev || {};
  MA.dev.fixtures = {
    active: active,
    TOKEN: FIXTURE_TOKEN,
    calls: calls,
    routes: routes,
    /** route(method, pathPattern, handler, {first}?) — dinamikus fixture (elsőbbséget kap a JSON-útvonalakkal
     *  szemben; a korábban regisztrált nyer, kivéve {first: true}: az a meglévő kezelők elé kerül — tesztekhez). */
    route: function (method, path, fn, opts) {
      var hd = { method: String(method).toUpperCase(), path: path, fn: fn };
      if (opts && opts.first) { handlers.unshift(hd); } else { handlers.push(hd); }
    },
    reset: function () { routes.forEach(function (r) { r._pos = 0; }); calls.length = 0; }
  };

  if (active) {
    loadRoutes();
    MA.__devSetTransport(fixtureTransport);
    // indítókód nélküli megnyitás: automatikus "#launch=fixture" (a meglévő útvonal megmarad)
    if (!MA.api.session.token() && !/^#launch=/.test(window.location.hash)) {
      var rest = window.location.hash ? '&' + window.location.hash.slice(1) : '';
      window.history.replaceState(null, '', window.location.pathname + window.location.search + '#launch=fixture' + rest);
    }
  }
})();
