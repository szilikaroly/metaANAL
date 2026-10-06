/* api.js — a munkapad-szerver HTTP API-jának kliense (terv 3.3, 3.4, 4.2, 7.1 T1/T4).
 *
 * - fetch-burok: X-MA-Token, Accept/Content-Type: application/json, csak saját /api/* útvonal;
 * - boríték: {ok, schema, data, warnings, meta} | {ok:false, error:{code, http, message, details}};
 * - hiba → ApiError + toast (a 3.4 kódjai magyar címmel, a szerver üzenete szó szerint);
 * - client_seq (monoton), „legutolsó nyer” futtató (latest);
 * - munkamenet: #launch=<kód> → POST /api/session → token (sessionStorage), majd history.replaceState;
 *   új lap a tokent BroadcastChannel-en kapja a meglévő laptól;
 * - változásfigyelés: GET /api/changes?since=<rev> long-poll, visszalépő újrapróbálással.
 */
(function () {
  'use strict';
  var MA = window.MA;

  var TOKEN_KEY = 'mag.token';                 // sessionStorage — az EGYETLEN ott tárolt kulcs
  var CHANNEL_NAME = 'mag-session';
  var SESSION_PATH = '/api/session';
  var SESSION_BODY_KEY = 'launch_code';        // POST /api/session törzse: {"launch_code": "<kód>"}
  var LAUNCH_RE = /^#launch=([A-Za-z0-9_-]{4,256})(?:&(.*))?$/;
  var DEFAULT_TIMEOUT_MS = 35000;
  var HANDOFF_WAIT_MS = 1200;
  var CHANGES_TIMEOUT_MS = 40000;              // a szerver 25 s-ig tartja a kérést
  var BACKOFF_MIN_MS = 1000;
  var BACKOFF_MAX_MS = 30000;

  // 3.4 hibakódok (+ kliensoldaliak). A cím az i18n 'err.<KÓD>' kulcsa.
  var ERROR_CODES = ['BAD_REQUEST', 'FORBIDDEN', 'NOT_FOUND', 'METHOD_NOT_ALLOWED', 'CONFLICT', 'GATE_BLOCKED',
    'PAYLOAD_TOO_LARGE', 'UNSUPPORTED_MEDIA', 'VALIDATION', 'LOCKED', 'CAPABILITY_MISSING', 'PLUGIN_FAILED',
    'TIMEOUT', 'INTERNAL', 'NETWORK', 'BAD_RESPONSE', 'ABORTED', 'NO_SESSION'];
  var SILENT_CODES = { ABORTED: 1 };

  // ---------------------------------------------------------------- ApiError
  function ApiError(code, message, http, details, envelope) {
    var e = new Error(message || code);
    e.name = 'ApiError';
    e.code = code;
    e.http = http || 0;
    e.details = details || null;
    e.envelope = envelope || null;
    e.isApiError = true;
    return e;
  }

  // ---------------------------------------------------------------- token
  var memToken = null;

  function readToken() {
    if (memToken) { return memToken; }
    try { memToken = window.sessionStorage.getItem(TOKEN_KEY); } catch (e) { memToken = memToken || null; }
    return memToken;
  }

  function writeToken(tok) {
    memToken = tok;
    try { window.sessionStorage.setItem(TOKEN_KEY, tok); } catch (e) { /* csak memóriában */ }
  }

  function clearToken() {
    memToken = null;
    try { window.sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* nincs tároló */ }
  }

  // ---------------------------------------------------------------- transport
  function fetchTransport(req) {
    return window.fetch(req.url, {
      method: req.method,
      headers: req.headers,
      body: req.body,
      signal: req.signal,
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
      referrerPolicy: 'no-referrer'
    }).then(function (res) {
      return res.text().then(function (text) {
        return {
          status: res.status,
          etag: res.headers.get('ETag'),
          contentType: res.headers.get('Content-Type') || '',
          text: text
        };
      });
    });
  }

  var transport = fetchTransport;

  /*<dev>*/
  // Csak a --dev buildben: a fejlesztői fixture-háttér cseréli le a hálózati réteget.
  MA.__devSetTransport = function (t) { transport = t; };
  /*</dev>*/

  /*<snapshot>*/
  // Csak a pillanatkép-buildben (terv 2.5, 7.6): a beágyazott adatból válaszoló réteg (src/snapshot/provider.js)
  // cseréli le a hálózatot; a READ_ONLY hibát a pillanatkép maga jelzi (párbeszédablak), ezért nincs róla toast.
  MA.__snapSetTransport = function (t) { transport = t; };
  SILENT_CODES.READ_ONLY = 1;
  ERROR_CODES.push('READ_ONLY');
  /*</snapshot>*/

  // ---------------------------------------------------------------- client_seq
  var seqCounter = 0;
  function nextSeq() { seqCounter += 1; return seqCounter; }

  // ---------------------------------------------------------------- meta
  var lastMeta = {};
  function noteMeta(meta) {
    if (!meta || typeof meta !== 'object') { return; }
    var changed = meta.project_rev !== lastMeta.project_rev || meta.engine !== lastMeta.engine;
    lastMeta = Object.assign({}, lastMeta, meta);
    if (changed && MA.store) {
      MA.store.set('meta', { engine: lastMeta.engine || null, project_rev: lastMeta.project_rev === undefined ? null : lastMeta.project_rev });
    }
  }

  // ---------------------------------------------------------------- toast a hibákhoz
  function errorDetails(err) {
    var d = err.details;
    var out = [];
    if (!d || typeof d !== 'object') { return out; }
    if (Array.isArray(d.blockers)) {
      d.blockers.forEach(function (b) {
        out.push('#' + (b.id === undefined ? '?' : b.id) + (b.stage ? ' ' + b.stage : '') + (b.title ? ' — ' + b.title : ''));
      });
    }
    if (Array.isArray(d.cells)) {
      out.push(MA.i18n.t('err.detail.cells', { n: String(d.cells.length) }));
    }
    if (typeof d.stderr_tail === 'string' && d.stderr_tail) { out.push(d.stderr_tail); }
    if (typeof d.hint === 'string' && d.hint) { out.push(d.hint); }
    return out;
  }

  function toastError(err) {
    if (!MA.ui || !MA.ui.toast) { return; }
    var key = 'err.' + err.code;
    MA.ui.toast({
      kind: 'error',
      title: MA.i18n.has(key) ? MA.i18n.t(key) : MA.i18n.t('err.UNKNOWN', { code: err.code }),
      message: err.message && err.message !== err.code ? err.message : '',
      details: errorDetails(err),
      code: err.code
    });
  }

  // ---------------------------------------------------------------- kérés
  function buildUrl(path, query) {
    if (typeof path !== 'string' || !/^\/api\/[A-Za-z0-9_./%-]*$/.test(path) || path.indexOf('..') >= 0) {
      throw ApiError('BAD_REQUEST', 'api: csak saját /api/* útvonal hívható');
    }
    if (!query) { return path; }
    var parts = [];
    Object.keys(query).forEach(function (k) {
      var v = query[k];
      if (v === undefined || v === null) { return; }
      (Array.isArray(v) ? v : [v]).forEach(function (x) {
        parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(String(x)));
      });
    });
    return parts.length ? path + '?' + parts.join('&') : path;
  }

  /**
   * request(method, path, opts?) → Promise<envelope>
   * opts: query{}, body (objektum; POST/PUT alapból {}), signal, ifMatch, headers{},
   *       toast (alap true), quiet [kódok, amelyekre nincs toast], seq (true → client_seq a törzsbe),
   *       clientSeq (szám — adott client_seq), timeoutMs (alap 35000)
   * A feloldott boríték kiegészül: env.etag (ETag fejléc vagy null), env.client_seq.
   */
  function request(method, path, opts) {
    opts = opts || {};
    var m = String(method || 'GET').toUpperCase();
    if (['GET', 'POST', 'PUT'].indexOf(m) < 0) { return Promise.reject(ApiError('BAD_REQUEST', 'api: nem engedett metódus')); }
    var url;
    try { url = buildUrl(path, opts.query); } catch (e) { return Promise.reject(e); }
    var seq = typeof opts.clientSeq === 'number' ? opts.clientSeq : nextSeq();
    var headers = { 'Accept': 'application/json', 'X-MA-Client-Seq': String(seq) };
    var tok = readToken();
    if (tok && path !== SESSION_PATH) { headers['X-MA-Token'] = tok; }
    var body;
    if (m !== 'GET') {
      headers['Content-Type'] = 'application/json';
      var payload = opts.body === undefined ? {} : opts.body;
      if ((opts.seq || typeof opts.clientSeq === 'number') && payload && typeof payload === 'object' && !Array.isArray(payload)) {
        payload = Object.assign({}, payload, { client_seq: seq });
      }
      body = JSON.stringify(payload);
    }
    if (opts.ifMatch) { headers['If-Match'] = String(opts.ifMatch); }
    if (opts.headers) {
      Object.keys(opts.headers).forEach(function (k) {
        if (/^(x-ma-token|cookie|host|origin)$/i.test(k)) { return; }
        headers[k] = String(opts.headers[k]);
      });
    }

    var ctrl = new AbortController();
    var timedOut = false;
    var timer = setTimeout(function () { timedOut = true; ctrl.abort(); }, opts.timeoutMs || DEFAULT_TIMEOUT_MS);
    var offOuter = null;
    if (opts.signal) {
      if (opts.signal.aborted) { ctrl.abort(); } else {
        var onAbort = function () { ctrl.abort(); };
        opts.signal.addEventListener('abort', onAbort);
        offOuter = function () { opts.signal.removeEventListener('abort', onAbort); };
      }
    }
    function done() { clearTimeout(timer); if (offOuter) { offOuter(); } }

    var wantToast = opts.toast !== false;
    var quiet = opts.quiet || [];

    return transport({ method: m, url: url, path: path, query: opts.query || null, headers: headers, body: body, signal: ctrl.signal })
      .then(function (res) {
        done();
        var env = null;
        if (/application\/json/i.test(res.contentType || '') && res.text) {
          try { env = JSON.parse(res.text); } catch (e) { env = null; }
        }
        if (!env || typeof env !== 'object' || typeof env.ok !== 'boolean') {
          throw ApiError('BAD_RESPONSE', MA.i18n.t('err.BAD_RESPONSE.detail', { status: String(res.status) }), res.status);
        }
        if (env.ok !== true) {
          var er = env.error || {};
          throw ApiError(er.code || 'INTERNAL', er.message || '', er.http || res.status, er.details || null, env);
        }
        noteMeta(env.meta);
        env.etag = res.etag || null;
        env.client_seq = seq;
        return env;
      }, function (e) {
        done();
        if (e && e.isApiError) { throw e; }
        if (e && e.name === 'AbortError') {
          throw timedOut ? ApiError('TIMEOUT', MA.i18n.t('err.TIMEOUT.client'), 0) : ApiError('ABORTED', 'aborted', 0);
        }
        throw ApiError('NETWORK', MA.i18n.t('err.NETWORK.detail'), 0);
      })
      .catch(function (err) {
        if (err.code === 'FORBIDDEN' && path !== SESSION_PATH && !opts.noProbe) { probeSession(); }
        if (wantToast && !SILENT_CODES[err.code] && quiet.indexOf(err.code) < 0) { toastError(err); }
        throw err;
      });
  }

  function get(path, opts) { return request('GET', path, opts); }
  function post(path, body, opts) { return request('POST', path, Object.assign({}, opts, { body: body })); }
  function put(path, body, opts) { return request('PUT', path, Object.assign({}, opts, { body: body })); }

  /**
   * latest() → run(fn): „legutolsó nyer” futtató (pl. 250 ms-os validálás, explore-elemzés).
   * fn(seq, signal) → Promise<envelope>; a run() a legutolsó hívás eredményével old fel,
   * a régebbiek null-lal (felülírt), és az előző kérés AbortSignal-ja megszakad.
   */
  function latest() {
    var currentSeq = 0;
    var ctrl = null;
    return function run(fn) {
      if (ctrl) { ctrl.abort(); }
      ctrl = new AbortController();
      var mySeq = nextSeq();
      currentSeq = mySeq;
      var p;
      try { p = Promise.resolve(fn(mySeq, ctrl.signal)); } catch (e) { p = Promise.reject(e); }
      return p.then(function (v) {
        return mySeq === currentSeq ? v : null;
      }, function (err) {
        if (mySeq !== currentSeq || (err && err.code === 'ABORTED')) { return null; }
        throw err;
      });
    };
  }

  /** list(env, key?) — tömb a borítékból: data (ha tömb), data[key], data.items; különben []. */
  function list(env, key) {
    var d = env && env.data;
    if (Array.isArray(d)) { return d; }
    if (d && key && Array.isArray(d[key])) { return d[key]; }
    if (d && Array.isArray(d.items)) { return d.items; }
    return [];
  }

  // ---------------------------------------------------------------- munkamenet
  var channel = null;
  var probing = false;

  function setSessionState(s, extra) {
    if (MA.store) { MA.store.set('session', Object.assign({ state: s }, extra || {})); }
    if (MA.bus) { MA.bus.emit('session', s); }
  }

  function openChannel() {
    if (channel || typeof window.BroadcastChannel !== 'function') { return channel; }
    channel = new window.BroadcastChannel(CHANNEL_NAME);
    channel.onmessage = function (ev) {
      var msg = ev.data;
      if (!msg || typeof msg !== 'object') { return; }
      if (msg.type === 'token-request' && readToken() && typeof msg.id === 'string') {
        channel.postMessage({ type: 'token-offer', to: msg.id, token: readToken() });
      }
    };
    return channel;
  }

  /** Token kérése a többi laptól (BroadcastChannel); ≤ HANDOFF_WAIT_MS ms. */
  function requestHandoff() {
    if (typeof window.BroadcastChannel !== 'function') { return Promise.resolve(null); }
    return new Promise(function (resolve) {
      var id = 'r' + String(Date.now()) + '-' + String(nextSeq());
      var bc = new window.BroadcastChannel(CHANNEL_NAME);
      var finished = false;
      function finish(tok) {
        if (finished) { return; }
        finished = true;
        clearTimeout(timer);
        bc.close();
        resolve(tok);
      }
      bc.onmessage = function (ev) {
        var msg = ev.data;
        if (msg && msg.type === 'token-offer' && msg.to === id && typeof msg.token === 'string' && msg.token) {
          finish(msg.token);
        }
      };
      var timer = setTimeout(function () { finish(null); }, HANDOFF_WAIT_MS);
      bc.postMessage({ type: 'token-request', id: id });
    });
  }

  /** A #launch=<kód> kiolvasása és AZONNALI törlése a címsorból (history.replaceState). */
  function takeLaunchCode() {
    var m = LAUNCH_RE.exec(window.location.hash || '');
    if (!m) { return null; }
    var rest = m[2] ? '#' + m[2] : '';
    window.history.replaceState(null, '', window.location.pathname + window.location.search + rest);
    return m[1];
  }

  function exchange(code) {
    var body = {};
    body[SESSION_BODY_KEY] = code;
    return request('POST', SESSION_PATH, { body: body, toast: false }).then(function (env) {
      var d = env.data || {};
      var tok = typeof d.token === 'string' ? d.token : (typeof d.session_token === 'string' ? d.session_token : null);
      if (!tok) { throw ApiError('BAD_RESPONSE', MA.i18n.t('err.BAD_RESPONSE.detail', { status: '200' }), 200); }
      writeToken(tok);
      return tok;
    });
  }

  /**
   * session.init() → Promise<'ok' | 'none' | 'failed'>
   *   1) #launch=<kód> → csere tokenre (a kód a címsorból azonnal törlődik);
   *   2) sessionStorage-token; 3) BroadcastChannel-átadás egy meglévő laptól; különben 'none'.
   */
  function sessionInit() {
    setSessionState('pending');
    var code = takeLaunchCode();
    var p;
    if (code) {
      p = exchange(code).then(function () { return 'ok'; }, function (err) {
        // lejárt/felhasznált kód: ha van még érvényes token (pl. frissítés), azzal megyünk tovább
        if (readToken()) { return 'ok'; }
        setSessionState('failed', { code: err.code, message: err.message });
        return 'failed';
      });
    } else if (readToken()) {
      p = Promise.resolve('ok');
    } else {
      p = requestHandoff().then(function (tok) {
        if (tok) { writeToken(tok); return 'ok'; }
        return 'none';
      });
    }
    return p.then(function (state) {
      if (state === 'ok') { openChannel(); setSessionState('ok'); } else if (state === 'none') { setSessionState('none'); }
      return state;
    });
  }

  /** 403 után: valóban elveszett-e a munkamenet? (könnyű GET-próba; ha az is 403 → 'lost') */
  function probeSession() {
    if (probing) { return; }
    probing = true;
    request('GET', '/api/engine', { toast: false, noProbe: true }).then(function () {
      probing = false;
    }, function (err) {
      probing = false;
      if (err.code === 'FORBIDDEN') {
        clearToken();
        changes.stop();
        setSessionState('lost');
      }
    });
  }

  // ---------------------------------------------------------------- változásfigyelés (long-poll)
  var changes = (function () {
    var running = false;
    var rev = null;
    var ctrl = null;
    var timer = null;
    var backoff = 0;
    var external = {};      // út → true (a legutóbbi nyugtázás óta)

    function isExternal(c) {
      if (c.external === true) { return true; }
      if (c.external === false) { return false; }
      var a = typeof c.actor === 'string' ? c.actor : '';
      return !!a && a !== 'user' && a.indexOf('user:') !== 0;
    }

    /**
     * A válasz normalizálása: a szerver ChangeWatcher-e {rev, changed:[út], external:[út], reset}
     * alakot ad; a {rev, changes:[{path, actor, …}]} alakot is elfogadjuk.
     */
    function normalize(d) {
      if (Array.isArray(d.changes)) { return d.changes; }
      var ext = Array.isArray(d.external) ? d.external : [];
      return (Array.isArray(d.changed) ? d.changed : []).map(function (p) {
        return { path: p, external: ext.indexOf(p) >= 0, actor: ext.indexOf(p) >= 0 ? 'external' : 'user' };
      });
    }

    function publish(items) {
      var prev = (MA.store.get('changes') || {}).items || [];
      items.forEach(function (c) { if (c && isExternal(c)) { external[c.path || c.kind || '?'] = true; } });
      MA.store.set('changes', {
        rev: rev,
        external_count: Object.keys(external).length,
        items: prev.concat(items).slice(-50)
      });
    }

    function setConn(s) {
      if (MA.store.get('connection') !== s) { MA.store.set('connection', s); }
    }

    function schedule(ms) {
      clearTimeout(timer);
      timer = setTimeout(poll, ms);
    }

    function poll() {
      if (!running) { return; }
      ctrl = new AbortController();
      var hadRev = rev !== null;
      request('GET', '/api/changes', { query: rev === null ? {} : { since: rev }, signal: ctrl.signal, toast: false, timeoutMs: CHANGES_TIMEOUT_MS })
        .then(function (env) {
          if (!running) { return; }
          backoff = 0;
          setConn('ok');
          var d = env.data || {};
          if (typeof d.rev === 'number') { rev = d.rev; }
          var items = normalize(d);
          if (items.length || d.reset) {
            publish(items);
            // az első (since nélküli) kérésre adott reset nem jelent elvesztett állapotot
            MA.bus.emit('changes', { rev: rev, changes: items, reset: !!d.reset && hadRev });
          }
          schedule(0);
        }, function (err) {
          if (!running || err.code === 'ABORTED') { return; }
          if (err.code === 'FORBIDDEN' || err.code === 'NO_SESSION') { running = false; return; }
          if (err.code === 'TIMEOUT') { schedule(0); return; }   // a long-poll lejárt: azonnal újra
          setConn('retrying');
          backoff = backoff ? backoff * 2 : BACKOFF_MIN_MS;
          if (backoff > BACKOFF_MAX_MS) { backoff = BACKOFF_MAX_MS; }
          schedule(backoff);
        });
    }

    return {
      /** start(rev?) — figyelés indítása (alapból a legutóbbi boríték project_rev-jétől). */
      start: function (fromRev) {
        if (running) { return; }
        running = true;
        if (typeof fromRev === 'number') { rev = fromRev; } else if (typeof lastMeta.project_rev === 'number') { rev = lastMeta.project_rev; }
        schedule(0);
      },
      stop: function () {
        running = false;
        clearTimeout(timer);
        if (ctrl) { ctrl.abort(); }
      },
      /** ack() — a „Külső: n” számláló nullázása (⟳ frissítés után). */
      ack: function () {
        external = {};
        var cur = MA.store.get('changes') || {};
        MA.store.set('changes', { rev: rev, external_count: 0, items: cur.items || [] });
      },
      running: function () { return running; },
      rev: function () { return rev; }
    };
  })();

  // ---------------------------------------------------------------- aláírt fájl-URL (PDF új lapon, T5)
  /**
   * openFile({doc, page?, path?}) — POST /api/fileurl → {url}; új lapon nyitja (#page=N).
   * A lapot szinkronban nyitjuk meg (felugróablak-szűrő), majd átirányítjuk.
   */
  function openFile(spec) {
    var w = window.open('', '_blank');
    if (w) { try { w.opener = null; } catch (e) { /* nem kritikus */ } }
    return post('/api/fileurl', { doc: spec.doc, path: spec.path || null }).then(function (env) {
      var url = env.data && env.data.url;
      if (typeof url !== 'string' || url.indexOf('/f/') !== 0) { throw ApiError('BAD_RESPONSE', 'fileurl', 200); }
      var full = url + (spec.page ? '#page=' + encodeURIComponent(String(spec.page)) : '');
      if (w) { w.location.replace(full); } else { window.open(full, '_blank', 'noopener'); }
      return full;
    }, function (err) {
      if (w) { w.close(); }
      throw err;
    });
  }

  MA.api = {
    ApiError: ApiError,
    ERROR_CODES: ERROR_CODES,
    request: request,
    get: get,
    post: post,
    put: put,
    latest: latest,
    nextSeq: nextSeq,
    list: list,
    openFile: openFile,
    lastMeta: function () { return Object.assign({}, lastMeta); },
    session: {
      init: sessionInit,
      token: readToken,
      clear: clearToken,
      TOKEN_KEY: TOKEN_KEY
    },
    changes: changes
  };
})();
