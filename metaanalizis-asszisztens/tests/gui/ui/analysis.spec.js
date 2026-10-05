#!/usr/bin/env node
/* tests/gui/ui/analysis.spec.js — a 4 Elemzés képernyőinek böngészős tesztje (terv 3.5.6–3.5.8, 8.6 2. réteg).
 *
 * Futtatás:  node tests/gui/ui/analysis.spec.js            (kilépési kód 1, ha bármi elbukik)
 * Előfeltétel: python3; Playwright (globális: /opt/node22/lib/node_modules/playwright vagy NODE_PATH).
 *
 * 1. buildel (termék + --dev); helyi teszt-szerver (127.0.0.1) a {{CSP_NONCE}} cseréjével és a szerver szigorú
 *    CSP-fejlécével; a termék-buildhez a web/fixtures/*.json borítékokat szolgálja ki (token, Content-Type,
 *    egyszer használható indítókód), és a futás-fájlok aláírt URL-jét (/f/…) letöltésként.
 * 2. dev-build ?fixtures=1: Elemzési terv (űrlap a motor opció-metaadatából, automatikus explore, debounce,
 *    last-wins, k > 40, protokoll-eltérés sáv, parancs-előnézet, validálás, Spec JSON, rögzítés döntéssel és
 *    If-Match-csel, gyermek-futások) és Eredmények (futás-választó, összegző sáv, KB-jelvény, forest-geometria
 *    ±0,5 px, rétegek, kiemelés minden nyitott ábrán, billentyűzet, lefúrás + PDF-oldal, funnel/Doi/LOO/befolyás/
 *    kumulatív, elavult futás, DOM-biztonság, nyelv, téma, böngészőtároló), konzolhiba nélkül.
 * 3. termék-build valódi fetch-csel: eredmények, explore, letöltés aláírt URL-en.
 */
'use strict';

const path = require('path');
const fs = require('fs');
const http = require('http');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

function loadPlaywright() {
  for (const c of ['playwright', '/opt/node22/lib/node_modules/playwright']) {
    try { return require(c); } catch (e) { /* következő */ }
  }
  throw new Error('Playwright nem található (NODE_PATH vagy /opt/node22/lib/node_modules/playwright)');
}
const { chromium } = loadPlaywright();

const ROOT = path.resolve(__dirname, '..', '..', '..');
const WEB = path.join(ROOT, 'ma_gui', 'web');
const BUILD = path.join(WEB, 'build_gui.py');
const PROD = path.join(WEB, 'dist', 'index.html');
const DEV = path.join(WEB, 'dist', 'index.dev.html');
const FIXTURES = path.join(WEB, 'fixtures');
const CHROME_FALLBACK = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const CSP = (n) => "default-src 'none'; script-src 'nonce-" + n + "'; style-src 'nonce-" + n + "'; " +
  "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; " +
  "base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

const PRIMARY = '20261004T211200Z-a1f3c2';
const STALE = '20261003T180200Z-4c0d1e';

// ---------------------------------------------------------------- mini-tesztkeret
const results = [];
let currentTest = '';
function check(cond, msg) {
  results.push({ test: currentTest, pass: !!cond, msg });
  if (!cond) { console.log('    FAIL: ' + msg); }
  return !!cond;
}
async function test(name, fn) {
  currentTest = name;
  console.log('• ' + name);
  try { await fn(); } catch (e) { check(false, 'kivétel: ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join(' | ') : e)); }
}
function runBuild(args) {
  const r = spawnSync('python3', [BUILD].concat(args), { cwd: ROOT, encoding: 'utf-8' });
  if (r.status !== 0) { throw new Error('build hiba (' + args.join(' ') + '): ' + r.stderr + r.stdout); }
  return r.stdout.trim();
}

// ---------------------------------------------------------------- fixture-adatok (elvárt értékek)
function fixture(name) { return JSON.parse(fs.readFileSync(path.join(FIXTURES, name), 'utf-8')); }
const ENGINE = fixture('engine.json').routes[0].envelope.data;
const PLOTS = {};
fixture('analysis_plots.json').routes.forEach((r) => { PLOTS[r.path.split('/')[3]] = r.envelope.data; });
const PLOT = PLOTS[PRIMARY];
// a motor kész szövegei (a fixture-ök a valódi motorból: tests/gui/ui/gen_fixtures_from_engine.py; 4.0: tizedespont)
const primOf = (pl) => pl.summaries.filter((s) => s.primary === true)[0];
const PRIM = primOf(PLOT);
const RUNS_FX = {};
fixture('analysis_runs.json').routes.forEach((r) => { if (/^\/api\/runs\/[^/]+$/.test(r.path)) { RUNS_FX[r.path.split('/')[3]] = r.envelope.data; } });
const FIXED = '20261005T091500Z-f1e2d3';
const NO_ROB = '20261005T091600Z-a7b8c9';
const HART = PLOT.studies.filter((s) => s.row_uid === 'rbcg04')[0];
const INF = {};
PLOT.influence.forEach((e) => { INF[e.row_uid] = e; });

// ---------------------------------------------------------------- teszt-szerver
function loadRoutes() {
  const routes = [];
  for (const f of fs.readdirSync(FIXTURES).filter((x) => x.endsWith('.json')).sort()) {
    (JSON.parse(fs.readFileSync(path.join(FIXTURES, f), 'utf-8')).routes || []).forEach((r) => routes.push(Object.assign({ _pos: 0 }, r)));
  }
  return routes;
}
function matchPath(pattern, p) {
  const a = pattern.replace(/^\/+|\/+$/g, '').split('/');
  const b = p.replace(/^\/+|\/+$/g, '').split('/');
  for (let i = 0; i < a.length; i++) {
    if (a[i] === '**') { return true; }
    if (i >= b.length || (a[i] !== '*' && a[i] !== b[i])) { return false; }
  }
  return a.length === b.length;
}
function subset(want, got) {
  if (!want) { return true; }
  if (!got) { return false; }
  return Object.keys(want).every((k) => String(want[k]) === String(got[k]));
}
function spec(r) { return Object.keys(r.query || {}).length + Object.keys(r.body || {}).length + (r.path.indexOf('*') < 0 ? 1 : 0); }

function startServer() {
  const routes = loadRoutes();
  const state = { tokens: new Set(), codes: new Set(['GOODCODE1']), requests: [], open: new Set() };
  const server = http.createServer((req, res) => {
    state.open.add(res);
    res.on('close', () => state.open.delete(res));
    const url = new URL(req.url, 'http://127.0.0.1');
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      const send = (status, obj, headers) => {
        res.writeHead(status, Object.assign({ 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }, headers || {}));
        res.end(JSON.stringify(obj));
      };
      const err = (status, code, message) => send(status, { ok: false, error: { code, http: status, message } });
      if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/dev.html')) {
        const nonce = crypto.randomBytes(18).toString('base64url');
        const html = fs.readFileSync(url.pathname === '/' ? PROD : DEV, 'utf-8').split('{{CSP_NONCE}}').join(nonce);
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': CSP(nonce), 'X-Frame-Options': 'DENY', 'Cache-Control': 'no-store' });
        res.end(html);
        return;
      }
      if (url.pathname.startsWith('/f/')) {          // aláírt fájl-URL (T5): letöltés / PDF új lapon
        state.requests.push({ method: req.method, path: url.pathname });
        const name = decodeURIComponent(url.pathname.split('/').pop());
        const headers = { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' };
        if (url.pathname.indexOf('/f/run/') === 0) { headers['Content-Disposition'] = 'attachment; filename="' + name.replace(/[^\w.-]/g, '_') + '"'; }
        res.writeHead(200, headers);
        res.end('fixture ' + name + '\n');
        return;
      }
      if (!url.pathname.startsWith('/api/')) { res.writeHead(404); res.end(); return; }
      const query = {};
      url.searchParams.forEach((v, k) => { query[k] = v; });
      let parsed = null;
      if (body) { try { parsed = JSON.parse(body); } catch (e) { return err(400, 'BAD_REQUEST', 'bad json'); } }
      const rec = { method: req.method, path: url.pathname, query, body: parsed, token: req.headers['x-ma-token'] || null,
        ctype: req.headers['content-type'] || null, ifMatch: req.headers['if-match'] || null };
      state.requests.push(rec);
      if (req.method !== 'GET' && !/^application\/json/.test(rec.ctype || '')) { return err(415, 'UNSUPPORTED_MEDIA', 'json kell'); }
      if (req.method === 'POST' && url.pathname === '/api/session') {
        const code = parsed && parsed.launch_code;
        if (!state.codes.has(code)) { return err(403, 'FORBIDDEN', 'Az indítókód lejárt vagy már felhasználták.'); }
        state.codes.delete(code);
        const tok = 'tok-' + crypto.randomBytes(16).toString('hex');
        state.tokens.add(tok);
        return send(200, { ok: true, schema: 'szk.ma.session/v1', data: { token: tok }, warnings: [], meta: { engine: '0.2.0', elapsed_ms: 0, project_rev: 41, request_id: 'q_t' } });
      }
      if (!state.tokens.has(rec.token)) { return err(403, 'FORBIDDEN', 'Hiányzó vagy érvénytelen munkamenet-token.'); }
      if (req.method === 'POST' && url.pathname === '/api/fileurl' && parsed && parsed.path) {
        return send(200, { ok: true, schema: 'szk.ma.fileurl/v1', data: { url: '/f/run/1791200000/' + encodeURIComponent(parsed.path.split('/').pop()), expires_in_s: 600 },
          warnings: [], meta: { engine: '0.2.0', elapsed_ms: 0, project_rev: 41, request_id: 'q_f' } });
      }
      const cands = routes.filter((r) => (r.method || 'GET') === req.method && matchPath(r.path, url.pathname) && subset(r.query, query) && subset(r.body, parsed));
      cands.sort((a, b) => spec(b) - spec(a));
      if (!cands.length) { return err(404, 'NOT_FOUND', 'Nincs ilyen útvonal.'); }
      const route = cands[0];
      let step = route;
      if (Array.isArray(route.sequence)) { step = route.sequence[Math.min(route._pos, route.sequence.length - 1)]; route._pos += 1; }
      const deliver = () => { if (!res.writableEnded) { send(step.status || 200, step.envelope, step.etag || route.etag ? { ETag: step.etag || route.etag } : null); } };
      if (step.delay_ms) { setTimeout(deliver, Math.min(step.delay_ms, 25000)); } else { deliver(); }
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve({ state, base: 'http://127.0.0.1:' + server.address().port,
        close: () => new Promise((r) => { state.open.forEach((x) => x.destroy()); if (server.closeAllConnections) { server.closeAllConnections(); } server.close(r); }) });
    });
  });
}

// ---------------------------------------------------------------- böngésző-segédek
function watch(page, label) {
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') { errors.push(label + ' console: ' + m.text()); } });
  page.on('pageerror', (e) => errors.push(label + ' pageerror: ' + e.message));
  return errors;
}
async function ready(page) { await page.waitForSelector('html[data-ready="1"]', { timeout: 10000 }); }
const calls = (page, p, method) => page.evaluate(([p2, m2]) => window.MA.dev.fixtures.calls
  .filter((c) => c.path.indexOf(p2) === 0 && (!m2 || c.method === m2)).map((c) => ({ method: c.method, path: c.path, query: c.query, body: c.body, headers: c.headers })), [p, method || null]);
const txt = (page, sel) => page.textContent(sel);
const count = (page, sel) => page.$$eval(sel, (els) => els.length);
async function waitText(page, sel, part, timeout) {
  try {
    await page.waitForFunction(([s, p]) => { const e = document.querySelector(s); return !!e && e.textContent.indexOf(p) >= 0; }, [sel, part], { timeout: timeout || 5000 });
  } catch (e) {
    const got = await page.evaluate((s) => { const el = document.querySelector(s); return el ? el.textContent.slice(0, 400) : '(nincs elem)'; }, sel);
    throw new Error('várt szöveg nem jelent meg: ' + sel + ' ⊇ ' + JSON.stringify(part) + ' — kapott: ' + JSON.stringify(got));
  }
  return true;
}
async function goto(page, base, hash) {
  await page.evaluate((hs) => { window.location.hash = hs; }, hash);
  await page.waitForFunction((hs) => location.hash.indexOf(hs.split('?')[0]) === 0, hash);
}
async function storageReport(page) {
  return page.evaluate(async () => {
    const ls = [], ss = [];
    for (let i = 0; i < localStorage.length; i++) { ls.push(localStorage.key(i)); }
    for (let i = 0; i < sessionStorage.length; i++) { ss.push(sessionStorage.key(i)); }
    let idb = [];
    try { idb = indexedDB.databases ? (await indexedDB.databases()).map((d) => d.name) : []; } catch (e) { idb = ['?']; }
    let cacheKeys = [];
    try { cacheKeys = typeof caches !== 'undefined' ? await caches.keys() : []; } catch (e) { cacheKeys = []; }
    return { ls, ss, idb, cacheKeys, cookie: document.cookie, lsValues: ls.map((k) => localStorage.getItem(k)) };
  });
}

// ================================================================ tesztek
(async () => {
  console.log(runBuild([]));
  console.log(runBuild(['--dev']));
  const srv = await startServer();
  let browser;
  try { browser = await chromium.launch({ headless: true }); } catch (e) { browser = await chromium.launch({ headless: true, executablePath: CHROME_FALLBACK }); }
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const p = await ctx.newPage();
  const errs = watch(p, 'dev');
  const dialogs = [];
  p.on('dialog', (d) => { dialogs.push(d.message()); d.dismiss(); });
  const optionNames = Object.keys(ENGINE.options);

  // ---------------------------------------------------------------- Elemzési terv
  await test('terv: az űrlap a motor opció-metaadatából, automatikus explore, parancs-előnézet', async () => {
    await p.goto(srv.base + '/dev.html?fixtures=1#/analysis');
    await ready(p);
    await p.waitForSelector('#plan-form');
    check((await txt(p, '#screen-title')).indexOf('Elemzési terv') >= 0, 'cím: Elemzési terv');
    const fields = await p.$$eval('#plan-form [data-opt]', (els) => els.map((e) => e.getAttribute('data-opt')));
    check(JSON.stringify(fields.slice().sort()) === JSON.stringify(optionNames.slice().sort()), 'minden engine.options-kulcs egy mező (' + fields.length + '/' + optionNames.length + ')');
    const measureOpts = await p.$$eval('#opt-measure option', (els) => els.map((e) => e.value));
    check(JSON.stringify(measureOpts) === JSON.stringify(ENGINE.options.measure.choices), 'mérték: a motor választható értékei, sorrendben');
    check((await txt(p, '#opt-tau2 option')).indexOf('alapérték') >= 0, 'τ²: null alapérték → „alapérték (a motoré)” opció');
    check((await txt(p, '#opt-tau2-help')) === ENGINE.options.tau2.help, 'súgó: a motor argparse-help szövege szó szerint');
    check((await txt(p, '[data-field="tau2"] .opt-cli')) === '--tau2', 'CLI-kapcsoló a metaadatból');
    check(await p.$eval('#opt-tau2', (e) => e.getAttribute('aria-describedby').indexOf('opt-tau2-help') >= 0), 'aria-describedby a súgóra');
    // UX-11: a motor csoportjai; a haladó (replikáció, varianciaváltozat, kimenet) opciók összecsukva, az alapok elöl
    const advNames = optionNames.filter((k) => ENGINE.options[k].advanced);
    check(advNames.length >= 10 && (await count(p, 'details.opt-group.is-advanced [data-opt]')) === advNames.length, 'haladó opciók összecsukott csoportban (' + advNames.length + ')');
    check(!(await p.$eval('details.opt-group.is-advanced', (d) => d.open)), 'a haladó csoport alapból zárva');
    const firstGroup = await p.$eval('#plan-form .opt-group', (e) => e.getAttribute('data-group'));
    check(firstGroup === 'basic' && (await p.$('fieldset.opt-group[data-group="basic"] #opt-measure')) !== null, 'elöl az alapbeállítások (mérték, modell …)');
    check((await txt(p, 'label[for="opt-plot_schema"]')).indexOf('--plot-schema') < 0 && (await txt(p, 'label[for="opt-svg_annotate"]')).indexOf('--svg-annotate') < 0, 'minden mezőnek magyar címkéje van (nem a CLI-kapcsoló)');
    check((await txt(p, '#opt-measure-help')).indexOf('--spec') < 0 && (await txt(p, '#opt-measure-help')).indexOf('lásd lent') < 0, 'a mérték súgója a felületnek szól (nincs --spec / „lásd lent”)');
    await waitText(p, '#plan-summary', PRIM.display_text.hu, 8000);
    check(PRIM.display_text.hu === '0.49 [0.33; 0.73]', 'automatikus explore (k ≤ 40): a motor display_text-je');
    const an = await calls(p, '/api/analyze', 'POST');
    check(an.length >= 1 && an[0].body.mode === 'explore' && typeof an[0].body.client_seq === 'number', 'POST /api/analyze {mode: explore, client_seq}');
    check(an.length >= 1 && JSON.stringify(Object.keys(an[0].body.spec.options).sort()) === JSON.stringify(optionNames.slice().sort()), 'a spec options-kulcsai = DEFAULTS-kulcsok');
    check(an.length >= 1 && an[0].body.spec.schema === 'szk.ma.analysis-spec/v1' && an[0].body.spec.name === 'o1_primary', 'szk.ma.analysis-spec/v1');
    check((await txt(p, '#plan-cmd-main')) === 'python ma.py analyze --spec 05_elemzes/specs/o1_primary.json --project .', 'parancs-előnézet: equivalent_argv szó szerint');
    check((await txt(p, '#plan-cmd-expanded')) === 'python ma.py analyze --data 03_adatok/o1.csv --measure RR --subgroup allokáció --cumulative év', 'egyenértékű argv');
    check((await txt(p, '#plan-banner')).indexOf('Nincs eltérés') >= 0, 'sáv: nincs eltérés a mentett, előre rögzített spec-től');
    check((await count(p, '#plan-runs-table tbody tr')) === 3, 'futások: 3 commit-futás');
    check((await txt(p, '#plan-runs-table tr[data-run="' + STALE + '"]')).indexOf('ELAVULT') >= 0, 'elavult futás ELAVULT jelvénnyel (X001)');
    const i2cell = await p.$eval('#plan-runs-table tr[data-run="' + PRIMARY + '"]', (tr) => tr.children[4].textContent.trim());
    check(i2cell === PLOT.heterogeneity.i2_text.hu, 'futás-tábla I²: a motor i2_text-je (FID-3 / UX-14: ' + i2cell + ')');
    check((await count(p, '#plan-kb-refs .kb-badge')) === 3, 'KB ehhez: a spec kb_refs-e');
    check((await count(p, '.plan-child-buttons button[id^="child-"]')) === 5, 'öt gyermek-gomb');
    check((await txt(p, '[data-child="no_estim"]')).indexOf('1 futás') >= 0, 'meglévő gyermek-futás jelölve');
  });

  await test('terv: protokoll-eltérés sáv, debounce, last-wins, k > 40', async () => {
    const n0 = (await calls(p, '/api/analyze', 'POST')).length;
    await p.selectOption('#opt-tau2', 'DL');
    await p.waitForSelector('#plan-diff li[data-key="tau2"]');
    const line = await txt(p, '#plan-diff li[data-key="tau2"]');
    check(line.indexOf('τ²-becslő') >= 0 && line.indexOf('«alap»') >= 0 && line.indexOf('«DL»') >= 0, 'eltérés-sor: τ² alap → DL (' + line + ')');
    check((await txt(p, '#plan-banner')).indexOf('X016') >= 0 && (await p.getAttribute('#plan-banner', 'class')).indexOf('is-warning') >= 0, 'protokoll-eltérés (X016) figyelmeztetés');
    check((await p.getAttribute('[data-field="tau2"]', 'class')).indexOf('is-changed') >= 0, 'a módosított mező jelölve');
    await p.waitForFunction((n) => window.MA.dev.fixtures.calls.filter((c) => c.path === '/api/analyze').length > n, n0, { timeout: 4000 });
    await waitText(p, '#plan-cmd-expanded', '--tau2 DL');
    check(true, 'debounce után automatikus explore; a parancs követi a spec-et');
    // last-wins: két explore egymás után, lassú válasszal — a régebbi eldobva
    await p.evaluate(() => window.MA.dev.analysis.setDelay(500));
    const n1 = (await calls(p, '/api/analyze', 'POST')).length;
    await p.selectOption('#opt-model', 'fixed');
    await p.click('#plan-explore');
    await p.waitForTimeout(60);
    await p.selectOption('#opt-model', 'random');
    await p.click('#plan-explore');
    await waitText(p, '#plan-status', 'kész', 6000);
    await p.waitForTimeout(700);
    const an = (await calls(p, '/api/analyze', 'POST')).slice(n1);
    check(an.length === 2 && an[0].body.spec.options.model === 'fixed' && an[1].body.spec.options.model === 'random', 'két explore-kérés (fixed, majd random)');
    check(an.length === 2 && an[1].body.client_seq > an[0].body.client_seq, 'client_seq monoton nő');
    check((await txt(p, '#plan-summary')).indexOf(PRIM.display_text.hu) >= 0 && (await txt(p, '#plan-summary')).indexOf(primOf(PLOTS[FIXED]).display_text.hu) < 0, 'a legutolsó nyer (a fix hatású válasz eldobva)');
    check((await p.getAttribute('#plan-status', 'data-seq')) === String(an[1].body.client_seq) && (await txt(p, '#plan-status')).indexOf('client_seq') < 0, 'státusz: a legutolsó client_seq (data-seq; a szövegben nincs belső azonosító — UX-12)');
    await p.evaluate(() => window.MA.dev.analysis.setDelay(120));
    // k > 40: nincs automatikus futás
    await p.evaluate(() => window.MA.dev.analysis.setK(55));
    await p.click('#plan-explore');
    await waitText(p, '#plan-khint', '55 > 40');
    check(await p.$eval('#plan-auto', (e) => e.disabled), 'k = 55: az automatikus explore kikapcsolva');
    const n2 = (await calls(p, '/api/analyze', 'POST')).length;
    await p.selectOption('#opt-pi', 't_k-1');
    await p.waitForTimeout(900);
    check((await calls(p, '/api/analyze', 'POST')).length === n2, 'k > 40: módosításra nincs explore');
    await p.selectOption('#opt-pi', 't_k-2');
    await p.evaluate(() => window.MA.dev.analysis.setK(null));
    await p.click('#plan-explore');
    await waitText(p, '#plan-khint', 'k = 13');
    check(!(await p.$eval('#plan-auto', (e) => e.disabled)), 'k = 13: az automatikus explore újra engedélyezett');
    // automatikus kikapcsolva → nincs futás; preferencia
    await p.uncheck('#plan-auto');
    check((await p.evaluate(() => localStorage.getItem('mag.pref.analysis.autoExplore'))) === '0', 'mag.pref.analysis.autoExplore = 0');
    const n3 = (await calls(p, '/api/analyze', 'POST')).length;
    await p.selectOption('#opt-ci', 'z');
    await p.waitForTimeout(700);
    check((await calls(p, '/api/analyze', 'POST')).length === n3, 'automatikus kikapcsolva: nincs explore');
    check((await txt(p, '#plan-cmd')).indexOf('változtak') >= 0, 'a parancs-előnézet jelzi, hogy elavult');
    await p.selectOption('#opt-ci', '');
    await p.check('#plan-auto');
  });

  await test('terv: validálás (szám, szűrő), Spec JSON modális, billentyűzet', async () => {
    await p.fill('#opt-level', 'abc');
    check((await p.getAttribute('#opt-level', 'aria-invalid')) === 'true', 'hibás szám → aria-invalid');
    check((await txt(p, '#opt-level-err')).length > 0, 'hibaüzenet (aria-describedby)');
    check(await p.$eval('#plan-explore', (e) => e.disabled) && await p.$eval('#plan-commit', (e) => e.disabled), 'hibás mezővel nincs futtatás / rögzítés');
    await p.fill('#opt-level', '0,95');
    check((await p.getAttribute('#opt-level', 'aria-invalid')) === 'false' && !(await p.$eval('#plan-commit', (e) => e.disabled)), 'tizedesvesszős szám elfogadva');
    await p.fill('#flt-exclude', 'rob');
    check((await p.getAttribute('#flt-exclude', 'aria-invalid')) === 'true', 'szűrő: oszlop=érték formátum kell');
    await p.fill('#flt-exclude', 'rob=high');
    await p.waitForSelector('#plan-diff li[data-key="filters.exclude"]');
    check(true, 'a szűrő-eltérés a sávban');
    await p.fill('#flt-exclude', '');
    await p.waitForFunction(() => !document.querySelector('#plan-diff li[data-key="filters.exclude"]'));
    await p.focus('#plan-json');
    await p.keyboard.press('Enter');
    await p.waitForSelector('#plan-json-pre');
    const json = await txt(p, '#plan-json-pre');
    check(json.indexOf('"schema": "szk.ma.analysis-spec/v1"') >= 0 && json.indexOf('"tau2": "DL"') >= 0, 'Spec JSON: a piszkozat');
    await p.keyboard.press('Escape');
    check((await p.$('[role="dialog"]')) === null && (await p.evaluate(() => document.activeElement.id)) === 'plan-json', 'Esc bezár, a fókusz visszatér');
    await p.fill('#plan-search', 'trim');
    const visible = await p.$$eval('#plan-form .opt-field', (els) => els.filter((e) => !e.hidden).map((e) => e.getAttribute('data-field')));
    check(visible.length >= 2 && visible.every((f) => f.indexOf('trimfill') === 0), 'opció-keresés szűr (' + visible.join(',') + ')');
    await p.fill('#plan-search', '');
  });

  await test('terv: rögzítés protokoll-eltéréssel — döntés, PUT If-Match, commit-feladat', async () => {
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#plan-commit');
    await p.waitForSelector('[role="dialog"] .plan-reason');
    check((await txt(p, '[role="dialog"]')).indexOf('X016') >= 0 && (await txt(p, '[role="dialog"]')).indexOf('τ²-becslő') >= 0, 'megerősítő ablak: eltérés + X016');
    const withReason = p.locator('[role="dialog"] button', { hasText: 'Rögzítés indoklással' });
    await withReason.click();
    check((await p.$('[role="dialog"]')) !== null && (await p.getAttribute('.plan-reason', 'aria-invalid')) === 'true', 'indoklás nélkül nem rögzíthető');
    await p.fill('.plan-reason', 'A protokoll 9.2 DL-t írt; REML-ről DL-re a reprodukció miatt (dátum: 2026-10-05).');
    await withReason.click();
    await p.waitForSelector('.toast.is-success', { timeout: 8000 });
    check((await txt(p, '.toast.is-success')).indexOf('Rögzítve') >= 0, 'sikeres rögzítés (toast)');
    const all = await p.evaluate(() => window.MA.dev.fixtures.calls.map((c) => ({ m: c.method, p: c.path, b: c.body, h: c.headers })));
    const iDec = all.findIndex((c) => c.p === '/api/log/decision');
    const iPut = all.findIndex((c) => c.m === 'PUT' && c.p === '/api/specs/o1_primary');
    const iAn = all.findIndex((c) => c.p === '/api/analyze' && c.b && c.b.mode === 'commit');
    check(iDec >= 0 && all[iDec].b.rationale.indexOf('protokoll 9.2') >= 0 && all[iDec].b.kb_refs.indexOf('D-S12-006') >= 0 && !!all[iDec].b.decision, 'döntés a naplóba (D-S12-006)');
    // PRIV-4: előbb a spec mentése (a szerver PHI-őre itt állít meg), csak utána a döntés
    check(iPut >= 0 && iPut < iDec && all[iPut].h['If-Match'] === '"spec-o1_primary-1"' && all[iPut].b.options.tau2 === 'DL', 'PUT /api/specs/o1_primary If-Match-csel, a döntés ELŐTT');
    check(iAn > iDec && all[iAn].b.spec.data.sha256 === '9f3a' + 'b'.repeat(60), 'commit a spec-kel és az adat sha256-jával');
    check(all.filter((c) => c.p.indexOf('/api/jobs/') === 0).length >= 2, 'a feladat lekérdezése (queued → running → done)');
    await p.waitForFunction(() => document.querySelectorAll('#plan-runs-table tbody tr').length === 4);
    check(true, 'az új commit-futás a listában');
    check((await txt(p, '#plan-banner')).indexOf('Nincs eltérés') >= 0, 'mentés után nincs eltérés');
    await p.click('.toast.is-success .toast-close');
  });

  await test('terv: érzékenységi gyermek-futások egy kattintással', async () => {
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#child-no_rob_high');
    await p.waitForSelector('.toast.is-success', { timeout: 8000 });
    const put = (await calls(p, '/api/specs/o1_primary_no_rob_high', 'PUT'))[0];
    check(!!put && put.body.purpose === 'sensitivity' && put.body.parent === 'o1_primary' && put.body.filters.exclude.indexOf('rob=high') >= 0 &&
      put.body.kb_refs[0] === 'D-S12-002', 'gyermek-spec: purpose sensitivity, parent, exclude rob=high, D-S12-002');
    await p.waitForFunction(() => Array.from(document.querySelectorAll('#plan-runs-table tbody tr')).some((r) => r.textContent.indexOf('└ o1_primary_no_rob_high') >= 0));
    const row = await p.$$eval('#plan-runs-table tbody tr', (rs) => rs.map((r) => r.textContent).filter((t) => t.indexOf('o1_primary_no_rob_high') >= 0)[0]);
    check(row.indexOf(RUNS_FX[NO_ROB].primary.display_text.hu) >= 0 && row.indexOf('AKTUÁLIS') >= 0, 'a gyermek-futás a motor szövegével (k = 9): ' + row);
    await p.click('.toast.is-success .toast-close');
    await p.click('#child-fixed');
    await p.waitForSelector('.toast.is-success', { timeout: 8000 });
    const fx = (await calls(p, '/api/specs/o1_primary_fixed', 'PUT'))[0];
    check(!!fx && fx.body.options.model === 'fixed', 'fix hatású gyermek: options.model = fixed');
    await p.click('.toast.is-success .toast-close');
    check(errs.length === 0, 'nincs konzolhiba: ' + errs.join(' || '));
  });

  // ---------------------------------------------------------------- Eredmények
  await test('eredmények: futás-választó, összegző sáv (a motor szövegei), KB-jelvény', async () => {
    await goto(p, srv.base, '#/results?outcome=o1&run=' + PRIMARY);
    await p.waitForSelector('#results-summary');
    check((await p.getAttribute('#nav-analysis', 'aria-current')) === 'page', '4 Elemzés fül aktív');
    const opts = await p.$$eval('#results-run option', (os) => os.map((o) => o.textContent));
    check(opts.length >= 6 && opts.some((o) => o.indexOf('ELAVULT') >= 0) && opts.some((o) => o.indexOf('Explore') >= 0), 'futás-választó: commit-futások + explore (' + opts.length + ')');
    check((await txt(p, '#results-run-badge')).indexOf('AKTUÁLIS') >= 0, 'AKTUÁLIS jelvény');
    const line = await txt(p, '.res-line');
    ['RR ' + PRIM.display_text.hu, PRIM.p_text.hu, 'PI ' + PRIM.pi_text.hu, 'I² ' + PLOT.heterogeneity.i2_text.hu, 'τ² ' + PLOT.heterogeneity.tau2_text.hu, 'k 13',
      'N ' + RUNS_FX[PRIMARY].participants_text.hu].forEach((part) => check(line.indexOf(part) >= 0, 'összegző sáv: ' + part));
    check(line.indexOf('RR 0.49 [0.33; 0.73]') >= 0 && line.indexOf('p = 0.002') >= 0, 'összegző sáv: tizedespont (4.0), mint a report.md');
    check((await txt(p, '.res-het')) === PLOT.heterogeneity.text.hu, 'heterogenitás-szöveg szó szerint');
    // UX-03: a PI-megjegyzés színsemleges (az interaktív forest nem piros vonallal rajzolja), nyers token nélkül
    const notes = await txt(p, '.res-notes');
    check(notes.indexOf('Piros') < 0 && notes.indexOf('t_k-2') < 0 && notes.indexOf('predikciós intervallum, t(k-2)') >= 0, 'PI-megjegyzés: színsemleges, olvasható módszer');
    const kbq = (await calls(p, '/api/kb/rules', 'GET')).filter((c) => c.query.field === 'model').pop();
    check(!!kbq && kbq.query.model === 'random' && kbq.query.k === '13' && kbq.query.measure === 'RR', 'ⓚ-jelvények a futás kontextusával (UX-06)');
    await p.waitForSelector('.res-kb .kb-badge[data-kb="D-S08-001"]');
    check((await count(p, '.res-kb .kb-badge')) === 9, 'ⓚ-jelvények a modell / PI / heterogenitás / torzítás mezőkhöz');
    await p.click('.res-kb .kb-badge[data-kb="D-S08-001"]');
    await waitText(p, '[role="dialog"] .kb-fields', 'kimenetenként rögzíted');
    check((await txt(p, '[role="dialog"] .modal-title')) === 'KB-tétel: D-S08-001', 'KB-tétel modális ablak');
    // UX-05: olvasható szabálykártya — a táblázat neve, a gépi mezők és az angol kulcsszavak nem a fő szövegben
    const dlgMain = await p.$eval('[role="dialog"] .kb-item', (el) => Array.from(el.children).filter((c) => !c.matches('details')).map((c) => c.textContent).join(' '));
    check(dlgMain.indexOf('decision_rule') < 0 && dlgMain.indexOf('[EN:') < 0 && dlgMain.indexOf('Gépi ellenőrzés') < 0 && dlgMain.indexOf('Kinek szól') < 0,
      'a fő szövegben nincs táblanév, gépi ellenőrzés, „Kinek szól” és [EN: …]');
    check((await p.$('[role="dialog"] details.kb-adv-box')) !== null && !(await p.$eval('[role="dialog"] details.kb-adv-box', (d) => d.open)), 'a gépi adatok egy összecsukott „Haladó” blokkban');
    check((await txt(p, '[role="dialog"] .kb-fields')).indexOf('S08 · Szintézis') >= 0, 'a szakasz a nevével (S08 · Szintézis …)');
    await p.keyboard.press('Escape');
    check((await p.evaluate(() => document.activeElement.getAttribute('data-kb'))) === 'D-S08-001', 'Esc után a fókusz a jelvényen');
  });

  await test('forest: jelölő-pozíció ±0,5 px, tengely = motor-tickek, szakaszok, PI, levágott gyémánt', async () => {
    const geo = await p.evaluate(() => {
      const wrap = document.querySelector('#results-plot-panel .fp-wrap');
      const ticks = Array.from(wrap.querySelectorAll('.fp-axis .pl-tick-label')).map((t) => ({ at: Number(t.getAttribute('data-at')), x: Number(t.getAttribute('x')), text: t.textContent }));
      const a = ticks[0], b = ticks[ticks.length - 1];
      const map = (v) => a.x + (v - a.at) / (b.at - a.at) * (b.x - a.x);
      let maxDev = 0;
      const studies = Array.from(wrap.querySelectorAll('g.fp-study')).map((g) => {
        const sq = g.querySelector('.fp-square');
        const y = Number(g.getAttribute('data-y'));
        if (sq) { maxDev = Math.max(maxDev, Math.abs(Number(sq.getAttribute('x')) + Number(sq.getAttribute('width')) / 2 - map(y))); }
        return { uid: g.getAttribute('data-uid'), y, lo: Number(g.getAttribute('data-lo')), hi: Number(g.getAttribute('data-hi')) };
      });
      return { ticks: ticks.map((t) => t.text), maxDev, studies };
    });
    const want = PLOT.axis.ticks.filter((t) => t.at >= PLOT.axis.domain[0] && t.at <= PLOT.axis.domain[1]).map((t) => t.text);
    check(JSON.stringify(geo.ticks) === JSON.stringify(want), 'tengelyfeliratok = axis.ticks[].text (' + geo.ticks.join(' ') + ')');
    check(geo.maxDev <= 0.5, 'négyzet-középpontok ±0,5 px (max eltérés ' + geo.maxDev.toFixed(3) + ' px)');
    check(geo.studies.length === 13, '13 vizsgálat <g data-uid data-y data-lo data-hi>');
    const byUid = {};
    PLOT.studies.forEach((s) => { byUid[s.row_uid] = s; });
    check(geo.studies.every((s) => byUid[s.uid] && Math.abs(byUid[s.uid].y - s.y) < 1e-9 && Math.abs(byUid[s.uid].lo - s.lo) < 1e-9 && Math.abs(byUid[s.uid].hi - s.hi) < 1e-9), 'data-y/lo/hi = a nézetmodell (elemzési skála)');
    check((await count(p, '#results-plot-panel .fp-section')) === 3 && (await count(p, '#results-plot-panel .fp-diamond.is-subtotal')) === 3, '3 alcsoport-szakasz és -összesítés');
    const clipped = PLOT.summaries.filter((s) => s.kind === 'subgroup' && (s.ci_lower < PLOT.axis.domain[0] || s.ci_upper > PLOT.axis.domain[1]));
    const arrows = await count(p, '#results-plot-panel .fp-sum.is-subtotal .fp-arrow');
    check(arrows === clipped.reduce((n, s) => n + (s.ci_lower < PLOT.axis.domain[0] ? 1 : 0) + (s.ci_upper > PLOT.axis.domain[1] ? 1 : 0), 0), 'a tengelyen túlnyúló alcsoport-gyémánt végén nyíl (a motor tengelye szerint: ' + arrows + ')');
    check((await txt(p, '#results-plot-panel .fp-sgtest')) === PLOT.subgroup_test.text.hu && PLOT.subgroup_test.text.hu.indexOf('1.86') >= 0, 'alcsoport-különbség szövege');
    const pi = await p.$eval('#results-plot-panel .fp-pi', (r) => [Number(r.getAttribute('data-lo')), Number(r.getAttribute('data-hi'))]);
    check(Math.abs(pi[0] - PLOT.summaries[0].pi_lower) < 1e-9 && Math.abs(pi[1] - PLOT.summaries[0].pi_upper) < 1e-9, 'PI-sáv a motor pi_lower/pi_upper-éből');
    check((await count(p, '#results-plot-panel .fp-rob.is-high')) === 4 && (await count(p, '#results-plot-panel .fp-flag.is-estimated')) === 2, 'RoB-szimbólumok és becsült-jelölés (alak + szín)');
    const effects = await p.$$eval('#results-plot-panel g.fp-study .fp-effect', (els) => els.map((e) => e.textContent));
    check(PLOT.studies.every((s) => effects.indexOf(s.display_text.hu) >= 0), 'minden vizsgálat display_text-je szó szerint');
  });

  await test('forest: rétegek (PI, fix) — mag.pref.forest.layers', async () => {
    await p.uncheck('#layer-pi');
    check((await count(p, '#results-plot-panel .fp-pi')) === 0, 'PI-réteg ki');
    await p.check('#layer-fixed');
    check((await count(p, '#results-plot-panel .fp-diamond.is-fixed')) === 1, 'fix hatású gyémánt be');
    check((await p.evaluate(() => localStorage.getItem('mag.pref.forest.layers'))) === 'subgroups,fixed,estimated', 'preferencia mentve');
    await p.check('#layer-pi');
    await p.uncheck('#layer-fixed');
  });

  await test('kiemelés minden nyitott ábrán, billentyűzet (↑/↓/Home/End/Enter/Esc), lefúrás + PDF-oldal', async () => {
    await p.selectOption('#results-side', 'funnel');
    await p.waitForSelector('#results-side-plot svg.fn-svg');
    await p.hover('#results-plot-panel g.fp-study[data-uid="rbcg04"] .fp-label');
    await p.waitForSelector('#results-side-plot .fn-pt[data-uid="rbcg04"].is-highlight', { timeout: 3000 });
    check(true, 'rámutatás a forestben → a funnel-pont kiemelve');
    check((await txt(p, '#results-plot-panel .pl-tip')).indexOf(HART.display_text.hu) >= 0, 'súgó: a motor display_text-je');
    await p.mouse.move(5, 5);
    await p.waitForFunction(() => !document.querySelector('.is-highlight[data-uid="rbcg04"]'));
    check(true, 'az egér elhagyásakor a kiemelés megszűnik');
    await p.focus('#results-plot-panel g.fp-study[data-uid="rbcg01"]');
    await p.keyboard.press('ArrowDown');
    check((await p.evaluate(() => document.activeElement.getAttribute('data-uid'))) === 'rbcg02', '↓ a következő vizsgálat');
    await p.keyboard.press('End');
    check((await p.evaluate(() => document.activeElement.getAttribute('data-uid'))) === 'rbcg13', 'End az utolsó');
    await p.keyboard.press('Home');
    check((await p.evaluate(() => document.activeElement.getAttribute('data-uid'))) === 'rbcg01', 'Home az első');
    await p.keyboard.press('Enter');
    await p.waitForSelector('#drilldown');
    check((await txt(p, '#drilldown .drill-title')) === 'Lefúrás: Aronson 1948', 'Enter → lefúrás');
    check((await p.evaluate(() => location.hash)).indexOf('row=rbcg01') >= 0, 'a kiválasztás az URL-ben (row=)');
    check(!!(await p.$('#results-side-plot .fn-pt[data-uid="rbcg01"].is-selected')), 'a kiválasztás minden ábrán');
    await waitText(p, '#drilldown .drill-cells', '123');
    check((await txt(p, '#drilldown .drill-cell[data-col="esemény1"] dd')) === '4', 'kinyerési sor (GET /api/table)');
    // az eredet a kinyerés-képernyő fixture-jéből jön (GET /api/provenance): lista vagy üres-állapot, hiba nélkül
    await p.waitForFunction(() => { const e = document.querySelector('#drilldown .drill-prov'); return !!e && e.childNodes.length > 0; });
    const prov = await txt(p, '#drilldown .drill-prov');
    check(!(await p.$('#drilldown .drill-prov .error-box')) && (prov.indexOf('közölt') >= 0 || prov.indexOf('nincs eredet') >= 0), 'cellaszintű eredet (GET /api/provenance): ' + prov.slice(0, 80));
    check((await txt(p, '#drilldown [data-fact="loo"]')).indexOf(PLOT.loo[0].display_text.hu) >= 0, 'LOO nélküle: a motor szövege');
    check((await txt(p, '#drilldown [data-fact="influence"]')).indexOf('rstudent ' + INF.rbcg01.rstudent_text.hu) >= 0, 'befolyás: a motor szövege');
    check((await p.getAttribute('#drilldown .drill-jump', 'href')) === '#/extraction?outcome=o1&row=rbcg01', 'Ugrás a sorhoz');
    check((await txt(p, '#drilldown .drill-page-text')).indexOf('3. oldal') >= 0, 'az oldalszám szövegként is (Safari)');
    const [popup] = await Promise.all([ctx.waitForEvent('page'), p.click('#drilldown .drill-pdf')]);
    await popup.waitForURL(/#page=3$/, { timeout: 5000 });
    check(popup.url().indexOf('/f/file%3A_privat%2Fpdf%2Faronson1948.pdf/') >= 0, 'PDF új lapon, aláírt URL-en a 3. oldalon');
    await popup.close();
    await p.focus('#drilldown');
    await p.keyboard.press('Escape');
    await p.waitForFunction(() => !document.getElementById('drilldown'));
    check((await p.evaluate(() => document.activeElement.getAttribute('data-uid'))) === 'rbcg01', 'Esc bezár, a fókusz a vizsgálaton');
    check((await p.evaluate(() => location.hash)).indexOf('row=') < 0, 'a row= paraméter törölve');
    await p.selectOption('#results-side', '');
  });

  await test('további ábrák: funnel (kontúrok), Doi, LOO, befolyás, kumulatív — fülek billentyűzettel', async () => {
    await p.click('[role="tab"][data-tab="funnel"]');
    await p.waitForSelector('#results-plot-panel .fn-svg');
    const ps = await p.$$eval('#results-plot-panel .fn-contour', (els) => els.map((e) => e.getAttribute('data-p')));
    check(JSON.stringify(ps) === JSON.stringify(['0.01', '0.05', '0.1']), 'kontúr-poligonok p = 0,01 / 0,05 / 0,10 (a motorból)');
    check((await count(p, '#results-plot-panel .fn-pt')) === 13 && (await count(p, '#results-plot-panel path.fn-filled')) === 1, '13 vizsgálat + 1 pótolt pont (eltérő alak)');
    check((await count(p, '#results-plot-panel .fn-pseudo')) === 1, 'pszeudo-CI a motor töréspontjaiból');
    check((await txt(p, '#results-plot-panel .fn-tests')) === PLOT.funnel.tests_text.hu, 'torzítás-tesztek szövege');
    check((await p.evaluate(() => location.hash)).indexOf('view=funnel') >= 0, 'a nézet az URL-ben');
    await p.focus('[role="tab"][data-tab="funnel"]');
    await p.keyboard.press('ArrowRight');
    check((await p.evaluate(() => document.activeElement.getAttribute('data-tab'))) === 'doi', '→ a következő fülre');
    await p.keyboard.press('Enter');
    await p.waitForSelector('#results-plot-panel .doi-svg');
    check((await txt(p, '#results-plot-panel .doi-lfk')) === PLOT.doi.lfk_text.hu && PLOT.doi.lfk_text.hu === 'LFK-index: -4.10 (jelentős aszimmetria)', 'Doi: LFK a motor szövegével');
    check((await count(p, '#results-plot-panel .doi-pt')) === 13, 'Doi: 13 pont');
    await p.click('[role="tab"][data-tab="loo"]');
    await p.waitForSelector('#results-plot-panel .sr-row');
    check((await count(p, '#results-plot-panel .sr-row')) === 13 && !!(await p.$('#results-plot-panel .sr-overall')), 'LOO: 13 sor + összesített referencia');
    check((await txt(p, '#results-plot-panel .sr-row[data-uid="rbcg04"] .fp-effect')) === PLOT.loo[3].display_text.hu, 'LOO: Hart nélkül a motor szövege');
    await p.click('[role="tab"][data-tab="influence"]');
    await p.waitForSelector('#results-plot-panel .inf-table');
    check((await count(p, '#results-plot-panel .inf-table tbody tr')) === 13 && (await count(p, '#results-plot-panel .inf-panel')) === 3, 'befolyás: táblázat + 3 kis ábra');
    check((await txt(p, '#results-plot-panel .inf-summary')) === PLOT.influence_text.hu, 'befolyás: a motor összegzése');
    check((await txt(p, '#results-plot-panel tr[data-uid="rbcg04"]')).indexOf(INF.rbcg04.rstudent_text.hu) >= 0, 'rstudent a motor szövegével');
    await p.click('[role="tab"][data-tab="cumulative"]');
    await p.waitForSelector('#results-plot-panel .sr-row');
    const cax = PLOT.cumulative.axis.domain;
    const cclip = PLOT.cumulative.entries.reduce((n, e) => n + (e.ci_lower < cax[0] ? 1 : 0) + (e.ci_upper > cax[1] ? 1 : 0), 0);
    check((await count(p, '#results-plot-panel .sr-row')) === 13 && (await count(p, '#results-plot-panel .fp-arrow')) === cclip, 'kumulatív: 13 lépés, a tengelyen túlnyúló CI nyíllal (a motor tengelye szerint: ' + cclip + ')');
    check((await p.evaluate(() => localStorage.getItem('mag.pref.results.view'))) === 'cumulative', 'mag.pref.results.view');
    await p.click('[role="tab"][data-tab="forest"]');
  });

  await test('befolyásos jelzés: fix hatású gyermek-futás (a motor metafor-kritériumai)', async () => {
    const val = await p.$$eval('#results-run option', (os) => (os.filter((o) => o.textContent.indexOf('o1_primary_fixed') >= 0)[0] || {}).value);
    check(!!val, 'a fix hatású futás a választóban');
    await p.selectOption('#results-run', val);
    await waitText(p, '.res-line', 'RR ' + primOf(PLOTS[FIXED]).display_text.hu);
    const nInf = PLOTS[FIXED].studies.filter((s) => s.flags.influential).length;
    check(nInf === 3 && (await count(p, '#results-plot-panel .fp-flag.is-influential')) === nInf, 'forest: 3 befolyásos vizsgálat jelölve');
    // UX-07: minden jel, ami az ábrán van, a jelmagyarázatban és a buboréksúgóban is
    const leg = await txt(p, '#results-plot-panel .fp-legend');
    check((await p.$('#results-plot-panel .fp-legend [data-key="influential"]')) !== null && leg.indexOf('▲') >= 0 && leg.indexOf('befolyásos') >= 0, 'jelmagyarázat: ▲ befolyásos');
    check(leg.indexOf('≈') >= 0 && (await count(p, '#results-plot-panel .fp-flag.is-estimated')) > 0 &&
      (await p.$$eval('#results-plot-panel .fp-flag.is-estimated', (fs) => fs.every((f) => f.textContent === '≈'))), 'becsült jele ≈ (nem a ◆, ami az összesített becslésé)');
    const infUid = PLOTS[FIXED].studies.filter((s) => s.flags.influential)[0].row_uid;
    await p.hover('#results-plot-panel g.fp-study[data-uid="' + infUid + '"]');
    await p.waitForTimeout(200);
    check((await txt(p, '#results-plot-panel')).indexOf('befolyásos vizsgálat') >= 0, 'buboréksúgó: befolyásos (szöveggel)');
    await p.click('[role="tab"][data-tab="influence"]');
    await p.waitForSelector('#results-plot-panel .inf-table');
    check((await count(p, '#results-plot-panel .inf-table .badge.is-warning')) === 3 && (await count(p, '#results-plot-panel .inf-mark.is-flag')) === 9, 'befolyás: jelvény + háromszög a 3 kis ábrán');
    await p.click('[role="tab"][data-tab="forest"]');
  });

  await test('elavult futás (X001) és DOM-biztonság: HTML-darab a címkében szövegként', async () => {
    await goto(p, srv.base, '#/results?outcome=o1&run=' + STALE + '&view=forest');
    await waitText(p, '#results-run-badge', 'ELAVULT');
    check((await txt(p, '.res-stale')).indexOf('X001') >= 0, 'ELAVULT + X001 megjegyzés');
    check((await txt(p, '.res-line')).indexOf('RR ' + primOf(PLOTS[STALE]).display_text.hu) >= 0 && primOf(PLOTS[STALE]).display_text.hu !== PRIM.display_text.hu, 'a régi futás a saját számával');
    const label = await p.getAttribute('#results-plot-panel g.fp-study[data-uid="rbcg13"]', 'aria-label');
    check(label.indexOf('Comstock et al 1976 <img src=x onerror=alert(1)>') === 0, 'a címke szövegként (aria-label)');
    check((await txt(p, '#results-plot-panel g.fp-study[data-uid="rbcg13"] .fp-label')).indexOf('<img') >= 0, 'a címke szövegként a DOM-ban');
    await p.click('#results-plot-panel g.fp-study[data-uid="rbcg13"] .fp-label');
    await p.waitForSelector('#drilldown');
    check((await txt(p, '#drilldown .drill-title')) === 'Lefúrás: Comstock et al 1976 <img src=x onerror=alert(1)>', 'lefúrás-cím szövegként');
    check((await count(p, 'img')) === 0 && dialogs.length === 0, 'nincs <img> elem és nincs alert');
    await p.keyboard.press('Escape');
  });

  await test('explore-eredmény az Eredményekben; nyelvváltás; téma', async () => {
    await goto(p, srv.base, '#/results?outcome=o1&run=explore');
    await p.waitForSelector('#results-summary');
    check((await p.$eval('#results-run', (s) => s.value)) === '' && (await txt(p, '.res-head')).indexOf('nincs run_id') >= 0, 'explore: nem rögzített, run_id nélkül');
    check((await txt(p, '#results-downloads')).indexOf('nincs fájlja') >= 0, 'explore-futásnak nincs letölthető fájlja');
    await goto(p, srv.base, '#/results?outcome=o1&run=' + PRIMARY + '&view=forest');
    await p.waitForSelector('#results-plot-panel .fp-sgtest');
    await p.click('#lang-switch [data-lang="en"]');
    await waitText(p, '.res-line', 'RR ' + PRIM.display_text.en);
    check((await txt(p, '#results-plot-panel .fp-sgtest')) === PLOT.subgroup_test.text.en && PLOT.subgroup_test.text.en.indexOf('Test for subgroup differences') === 0, 'EN: a motor angol szövege');
    check((await txt(p, '[role="tab"][data-tab="influence"]')) === 'Influence' && (await txt(p, '#screen-title')).indexOf('Results') >= 0, 'EN felület');
    await p.click('#lang-switch [data-lang="hu"]');
    await waitText(p, '.res-line', 'RR ' + PRIM.display_text.hu);
    const before = await p.$eval('#results-plot-panel .fp-square', (e) => getComputedStyle(e).fill);
    await p.click('#theme-toggle');
    const after = await p.$eval('#results-plot-panel .fp-square', (e) => getComputedStyle(e).fill);
    check(before !== after, 'téma: az ábra színei a tokenekből (' + before + ' → ' + after + ')');
    await p.click('#theme-toggle');
  });

  await test('böngészőtároló: csak mag.pref.* (rövid UI-preferencia) és a token; projektadat soha', async () => {
    const rep = await storageReport(p);
    check(rep.ls.every((k) => k.startsWith('mag.pref.')), 'localStorage csak mag.pref.* (' + rep.ls.join(',') + ')');
    check(rep.lsValues.every((v) => v.length <= 256 && !/Aronson|0,49|0\.49|rbcg|o1_primary/.test(v)), 'a preferenciákban nincs projektadat');
    check(rep.ss.every((k) => k === 'mag.token'), 'sessionStorage csak a token');
    check(rep.idb.length === 0 && rep.cacheKeys.length === 0 && rep.cookie === '', 'nincs IndexedDB, Cache, süti');
    check(errs.length === 0, 'nincs konzolhiba a dev-tesztek alatt: ' + errs.join(' || '));
  });
  await ctx.close();

  // ---------------------------------------------------------------- termék-build, valódi fetch
  await test('termék-build: eredmények és explore valódi fetch-csel, szigorú CSP, letöltés aláírt URL-en', async () => {
    const c2 = await browser.newContext({ acceptDownloads: true });
    const q = await c2.newPage();
    const e2 = watch(q, 'prod');
    await q.goto(srv.base + '/#launch=GOODCODE1');
    await ready(q);
    await goto(q, srv.base, '#/results?outcome=o1');
    await q.waitForSelector('#results-summary');
    check((await txt(q, '.res-line')).indexOf('RR ' + PRIM.display_text.hu) >= 0, 'összegző sáv a szerver fixture-éből');
    check((await count(q, '#results-plot-panel g.fp-study')) === 13, 'forest a termék-buildben');
    const [dl] = await Promise.all([q.waitForEvent('download'), q.click('#results-downloads [data-file="report"]')]);
    check(dl.suggestedFilename() === 'report.md', 'report.md letöltése');
    const fu = srv.state.requests.filter((r) => r.path === '/api/fileurl').pop();
    check(!!fu && fu.body.path === '05_elemzes/o1/' + PRIMARY + '/report.md' && fu.body.doc === null, 'POST /api/fileurl {doc: null, path}');
    check(srv.state.requests.some((r) => r.path === '/f/run/1791200000/report.md'), 'a fájl az aláírt /f/… URL-ről');
    await goto(q, srv.base, '#/analysis');
    await waitText(q, '#plan-summary', PRIM.display_text.hu, 8000);
    check(true, 'explore a termék-buildben (POST /api/analyze)');
    const api = srv.state.requests.filter((r) => r.path && r.path.indexOf('/api/') === 0 && r.path !== '/api/session');
    check(api.every((r) => !!r.token), 'minden /api/* kérés tokennel');
    check(e2.length === 0, 'nincs konzolhiba / CSP-sértés: ' + e2.join(' || '));
    check(await q.evaluate(() => typeof window.MA.dev === 'undefined'), 'a termék-buildben nincs fixture-kód');
    await c2.close();
  });

  await browser.close();
  await srv.close();
  const failed = results.filter((r) => !r.pass);
  console.log('\n' + (results.length - failed.length) + '/' + results.length + ' ellenőrzés zöld' + (failed.length ? ', ' + failed.length + ' HIBÁS' : ''));
  failed.forEach((f) => console.log('  ✖ [' + f.test + '] ' + f.msg));
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
