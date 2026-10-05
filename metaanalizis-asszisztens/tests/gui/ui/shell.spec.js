#!/usr/bin/env node
/* tests/gui/ui/shell.spec.js — a MA-munkapad keretének böngészős tesztje (terv 8.6, 2. réteg).
 *
 * Futtatás:  node tests/gui/ui/shell.spec.js            (kilépési kód 1, ha bármi elbukik)
 * Előfeltétel: python3; Playwright (globális: /opt/node22/lib/node_modules/playwright vagy NODE_PATH).
 *
 * Mit csinál:
 *   1. lefuttatja a buildet (termék + --dev);
 *   2. elindít egy helyi teszt-szervert (127.0.0.1, véletlen port), amely a HTML-ben a {{CSP_NONCE}}
 *      helyeket nonce-ra cseréli és a szerver szigorú CSP-fejlécét küldi (ma_gui/security.py
 *      HTML_CSP_TEMPLATE), az /api/* kéréseket pedig a web/fixtures/*.json borítékokkal szolgálja ki
 *      (tokent és Content-Type-ot ellenőrizve, egyszer használható indítókóddal);
 *   3. fej nélküli Chromiumban ellenőrzi: konzolhiba-mentes betöltés (CSP-sértés sincs), indítókód →
 *      token → history.replaceState, BroadcastChannel-tokenátadás új lapnak, billentyűzetes navigáció,
 *      témaváltás, nyelvváltás, hibatoast, munkamenet-vesztés, a böngészőtároló szabálya (7.6),
 *      a fixture-mód (?fixtures=1) és az oldalon belüli önteszt (?selftest=1).
 */
'use strict';

const path = require('path');
const fs = require('fs');
const http = require('http');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

function loadPlaywright() {
  const candidates = ['playwright', '/opt/node22/lib/node_modules/playwright'];
  for (const c of candidates) {
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

// ma_gui/security.py HTML_CSP_TEMPLATE (szó szerint)
const CSP = (n) => "default-src 'none'; script-src 'nonce-" + n + "'; style-src 'nonce-" + n + "'; " +
  "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; " +
  "base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

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

// ---------------------------------------------------------------- build
function runBuild(args) {
  const r = spawnSync('python3', [BUILD].concat(args), { cwd: ROOT, encoding: 'utf-8' });
  if (r.status !== 0) { throw new Error('build hiba (' + args.join(' ') + '): ' + r.stderr + r.stdout); }
  return r.stdout.trim();
}

// ---------------------------------------------------------------- teszt-szerver
function loadFixtureRoutes() {
  const routes = [];
  for (const f of fs.readdirSync(FIXTURES).filter((x) => x.endsWith('.json')).sort()) {
    const data = JSON.parse(fs.readFileSync(path.join(FIXTURES, f), 'utf-8'));
    (data.routes || []).forEach((r) => routes.push(Object.assign({ _file: f, _pos: 0 }, r)));
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

function startServer() {
  const routes = loadFixtureRoutes();
  const state = { tokens: new Set(), codes: new Set(['GOODCODE1', 'GOODCODE2']), requests: [], open: new Set() };
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
        res.writeHead(200, {
          'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': CSP(nonce), 'X-Frame-Options': 'DENY',
          'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'
        });
        res.end(html);
        return;
      }
      if (!url.pathname.startsWith('/api/')) { res.writeHead(404); res.end(); return; }
      const query = {};
      url.searchParams.forEach((v, k) => { query[k] = v; });
      let parsed = null;
      if (body) { try { parsed = JSON.parse(body); } catch (e) { return err(400, 'BAD_REQUEST', 'bad json'); } }
      const rec = { method: req.method, path: url.pathname, query, body: parsed, token: req.headers['x-ma-token'] || null,
        ctype: req.headers['content-type'] || null, seq: req.headers['x-ma-client-seq'] || null };
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
      const cands = routes.filter((r) => (r.method || 'GET') === req.method && matchPath(r.path, url.pathname) && subset(r.query, query) && subset(r.body, parsed));
      cands.sort((a, b) => (Object.keys(b.query || {}).length + Object.keys(b.body || {}).length) - (Object.keys(a.query || {}).length + Object.keys(a.body || {}).length));
      if (!cands.length) { return err(404, 'NOT_FOUND', 'Nincs ilyen útvonal.'); }
      const route = cands[0];
      let step = route;
      if (Array.isArray(route.sequence)) { step = route.sequence[Math.min(route._pos, route.sequence.length - 1)]; route._pos += 1; }
      const deliver = () => { if (!res.writableEnded) { send(step.status || 200, step.envelope, step.etag ? { ETag: step.etag } : null); } };
      if (step.delay_ms) { setTimeout(deliver, Math.min(step.delay_ms, 25000)); } else { deliver(); }
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      resolve({
        server, state, base: 'http://127.0.0.1:' + port,
        resetSequences: () => routes.forEach((r) => { r._pos = 0; }),
        close: () => new Promise((r) => { state.open.forEach((x) => x.destroy()); server.closeAllConnections && server.closeAllConnections(); server.close(r); })
      });
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
async function ready(page) {
  await page.waitForSelector('html[data-ready="1"]', { timeout: 10000 });
}
async function storageReport(page) {
  return page.evaluate(async () => {
    const ls = [], ss = [];
    for (let i = 0; i < localStorage.length; i++) { ls.push(localStorage.key(i)); }
    for (let i = 0; i < sessionStorage.length; i++) { ss.push(sessionStorage.key(i)); }
    let idb = [];
    try { idb = indexedDB.databases ? (await indexedDB.databases()).map((d) => d.name) : []; } catch (e) { idb = ['?' + e.message]; }
    let cacheKeys = [];
    try { cacheKeys = typeof caches !== 'undefined' ? await caches.keys() : []; } catch (e) { cacheKeys = []; }
    return { ls, ss, idb, cacheKeys, cookie: document.cookie, lsValues: ls.map((k) => localStorage.getItem(k)) };
  });
}
function checkStorage(rep, where) {
  check(rep.ls.every((k) => k.startsWith('mag.pref.')), where + ': localStorage csak mag.pref.* (' + rep.ls.join(',') + ')');
  check(rep.lsValues.every((v) => typeof v === 'string' && v.length <= 256), where + ': a preferenciák rövidek');
  check(rep.ss.every((k) => k === 'mag.token'), where + ': sessionStorage csak mag.token (' + rep.ss.join(',') + ')');
  check(rep.idb.length === 0, where + ': nincs IndexedDB (' + rep.idb.join(',') + ')');
  check(rep.cacheKeys.length === 0, where + ': nincs Cache Storage');
  check(rep.cookie === '', where + ': nincs süti');
}
async function screenIs(page, id) {
  await page.waitForFunction((x) => {
    const r = document.getElementById('screen-root');
    return location.hash.indexOf('#/' + x) === 0 && r && r.dataset.screen === x;
  }, id, { timeout: 4000 });
}
async function tabUntil(page, id, max) {
  for (let i = 0; i < (max || 60); i++) {
    await page.keyboard.press('Tab');
    const active = await page.evaluate(() => document.activeElement && document.activeElement.id);
    if (active === id) { return true; }
  }
  return false;
}

// ---------------------------------------------------------------- tesztek
(async () => {
  console.log(runBuild([]));
  console.log(runBuild(['--dev']));
  const srv = await startServer();
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
  } catch (e) {
    browser = await chromium.launch({ headless: true, executablePath: CHROME_FALLBACK });
  }

  // ========== 1. termék-build: indítókód → token, CSP, konzolhiba nélkül
  const ctx1 = await browser.newContext();
  const p1 = await ctx1.newPage();
  const err1 = watch(p1, 'prod');
  await test('termék-build: indítókód → token → replaceState, szigorú CSP mellett konzolhiba nélkül', async () => {
    await p1.goto(srv.base + '/#launch=GOODCODE1');
    await ready(p1);
    const hash = await p1.evaluate(() => location.hash);
    check(hash.indexOf('launch') < 0, 'az indítókód eltűnt a címsorból (' + hash + ')');
    check(hash === '#/overview', 'alapnézet: #/overview (' + hash + ')');
    const sess = srv.state.requests.filter((r) => r.path === '/api/session');
    check(sess.length === 1 && sess[0].body && sess[0].body.launch_code === 'GOODCODE1', 'POST /api/session {launch_code}');
    const tok = await p1.evaluate(() => sessionStorage.getItem('mag.token'));
    check(typeof tok === 'string' && tok.startsWith('tok-'), 'a token a sessionStorage-ban');
    const apiReqs = srv.state.requests.filter((r) => r.path !== '/api/session');
    check(apiReqs.length >= 4 && apiReqs.every((r) => r.token === tok), 'minden /api/* kérés X-MA-Token fejlécet visz');
    check(apiReqs.every((r) => /^\d+$/.test(r.seq || '')), 'minden kérés client_seq-et visz (X-MA-Client-Seq)');
    const header = await p1.textContent('.app-header');
    check(header.indexOf('motor 0.2.0') >= 0 && header.indexOf('3268/3268') >= 0, 'fejléc: motor-verzió és önteszt');
    check(header.indexOf('validator') >= 0 && header.indexOf('figure-forge') >= 0, 'fejléc: plugin-állapotok');
    check(header.indexOf('VÉDVE') >= 0, 'fejléc: adatvédelmi állapot');
    await p1.waitForSelector('#overview-outcomes', { timeout: 5000 });
    const table = await p1.textContent('#overview-outcomes');
    check(table.indexOf('0,49 [0,33; 0,73]') >= 0, 'áttekintés: a motor display_text-je (HU) szó szerint');
    check(table.indexOf('AKTUÁLIS') >= 0, 'áttekintés: futás-állapot');
    await p1.waitForFunction(() => /Külső: 2/.test(document.getElementById('external-changes').textContent), null, { timeout: 5000 });
    check(true, 'long-poll: a „Külső: 2” számláló megjelent');
    const noDev = await p1.evaluate(() => typeof window.MA.dev === 'undefined' && typeof window.MA.__devSetTransport === 'undefined');
    check(noDev, 'a termék-buildben nincs fixture-kód');
    const cspOk = await p1.evaluate(() => Array.from(document.scripts).every((s) => s.nonce && s.nonce.length >= 16));
    check(cspOk, 'minden <script> nonce-os (a szerver cserélte)');
    check(err1.length === 0, 'nincs konzolhiba / CSP-sértés: ' + err1.join(' || '));
  });

  await test('újratöltés: a token a sessionStorage-ból (nincs új indítókód-csere)', async () => {
    const before = srv.state.requests.filter((r) => r.path === '/api/session').length;
    await p1.reload();
    await ready(p1);
    const after = srv.state.requests.filter((r) => r.path === '/api/session').length;
    check(before === after, 'nincs újabb POST /api/session');
    check(err1.length === 0, 'nincs konzolhiba: ' + err1.join(' || '));
  });

  // ========== 2. új lap: BroadcastChannel-tokenátadás
  await test('új lap a tokent BroadcastChannel-en kapja a meglévő laptól', async () => {
    const p2 = await ctx1.newPage();
    const err2 = watch(p2, 'tab2');
    await p2.goto(srv.base + '/#/log');
    await ready(p2);
    const t1 = await p1.evaluate(() => sessionStorage.getItem('mag.token'));
    const t2 = await p2.evaluate(() => sessionStorage.getItem('mag.token'));
    check(!!t2 && t1 === t2, 'ugyanaz a token az új lapon');
    check((await p2.evaluate(() => location.hash)) === '#/log', 'az útvonal megmaradt');
    check((await p2.textContent('#screen-title')).indexOf('Napló') >= 0, 'a 7 Napló fül nyílt meg');
    check(err2.length === 0, 'nincs konzolhiba: ' + err2.join(' || '));
    await p2.close();
  });

  // ========== 3. hibatoast + munkamenet-vesztés
  await test('API-hiba → magyar toast; munkamenet-vesztés → panel', async () => {
    const before = err1.length;
    const code = await p1.evaluate(() => window.MA.api.get('/api/nincs-ilyen').then(() => 'ok', (e) => e.code));
    check(code === 'NOT_FOUND', 'ApiError.code = NOT_FOUND');
    await p1.waitForSelector('#ma-alerts .toast[data-code="NOT_FOUND"]', { timeout: 3000 });
    const txt = await p1.textContent('#ma-alerts .toast[data-code="NOT_FOUND"]');
    check(txt.indexOf('Nem található') >= 0 && txt.indexOf('Nincs ilyen útvonal.') >= 0, 'toast: magyar cím + a szerver üzenete szó szerint');
    await p1.click('#ma-alerts .toast[data-code="NOT_FOUND"] .toast-close');
    check(err1.slice(before).every((e) => /status of 404/.test(e)), 'csak a várt 404 került a konzolra');
    srv.state.tokens.clear();
    await p1.evaluate(() => window.MA.api.get('/api/engine', { toast: false }).catch(() => null));
    await p1.waitForSelector('#session-panel[data-state="lost"]', { timeout: 4000 });
    check(true, 'munkamenet-vesztés panel');
    check((await p1.evaluate(() => sessionStorage.getItem('mag.token'))) === null, 'a token törlődött');
  });

  await test('felhasznált indítókód → „nem érvényes” panel', async () => {
    const ctx = await browser.newContext();
    const p = await ctx.newPage();
    await p.goto(srv.base + '/#launch=GOODCODE1');
    await p.waitForSelector('#session-panel[data-state="failed"]', { timeout: 5000 });
    check((await p.evaluate(() => location.hash)).indexOf('launch') < 0, 'a kód akkor is törlődik a címsorból');
    checkStorage(await storageReport(p), 'sikertelen indítás');
    await ctx.close();
  });
  await ctx1.close();

  // ========== 4. fejlesztői build: ?fixtures=1
  const ctx3 = await browser.newContext();
  const p3 = await ctx3.newPage();
  const err3 = watch(p3, 'dev');
  await test('dev-build ?fixtures=1: betöltés konzolhiba nélkül, fixture-borítékokkal', async () => {
    await p3.goto(srv.base + '/dev.html?fixtures=1');
    await ready(p3);
    check((await p3.evaluate(() => location.hash)) === '#/overview', 'automatikus fixture-indítás, #/overview');
    await p3.waitForSelector('#overview-outcomes');
    check((await p3.textContent('#ov-blockers')).indexOf('#15') >= 0, 'áttekintés: nyitott blockerek');
    check((await p3.textContent('#ov-next')).indexOf('X001') >= 0, 'áttekintés: következő lépések (X001)');
    check((await p3.$$('#ov-stages .stage')).length === 16, 'áttekintés: 16 szakasz (S00–S14, FINAL)');
    const calls = await p3.evaluate(() => window.MA.dev.fixtures.calls.filter((c) => c.path !== '/api/session').every((c) => c.headers['X-MA-Token'] === window.MA.dev.fixtures.TOKEN));
    check(calls, 'fixture-módban is minden kérés tokennel megy');
    check(srv.state.requests.filter((r) => r.path.startsWith('/api/') && r.query && r.query.fixtures).length === 0, 'fixture-módban nincs hálózati API-kérés');
    // a tömörített i18n-csomag („lz1”) kicsomagolása gyors és teljes (lineáris idejű dekóder)
    const i18nLoad = await p3.evaluate(() => {
      const t0 = performance.now();
      for (let i = 0; i < 5; i++) { window.MA.i18n._reload(); }
      return { ms: (performance.now() - t0) / 5, hu: window.MA.i18n.keys('hu').length, en: window.MA.i18n.keys('en').length };
    });
    check(i18nLoad.ms < 60 && i18nLoad.hu > 1000 && i18nLoad.hu === i18nLoad.en, 'i18n-csomag kicsomagolása < 60 ms, teljes (' + JSON.stringify(i18nLoad) + ')');
    check(err3.length === 0, 'nincs konzolhiba: ' + err3.join(' || '));
  });

  await test('billentyűzetes navigáció: skip-link, Tab + Enter, nyilak, Alt+szám, súgó (?)', async () => {
    await p3.goto(srv.base + '/dev.html?fixtures=1#/overview');
    await ready(p3);
    await p3.keyboard.press('Tab');
    check((await p3.evaluate(() => document.activeElement.className)) === 'skip-link', 'első Tab: „Ugrás a tartalomra”');
    await p3.keyboard.press('Enter');
    check((await p3.evaluate(() => document.activeElement.id)) === 'main', 'a skip-link a fő tartalomra visz');
    await p3.evaluate(() => document.activeElement.blur());
    const reached = await tabUntil(p3, 'nav-extraction', 80);
    check(reached, 'Tab-bal elérhető a „3 Kinyerés” fül');
    const focusVisible = await p3.evaluate(() => {
      const s = getComputedStyle(document.activeElement);
      return s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2;
    });
    check(focusVisible, 'látható fókuszkeret');
    await p3.keyboard.press('Enter');
    await screenIs(p3, 'extraction');
    check((await p3.textContent('#screen-title')).indexOf('Kinyerés') >= 0, 'Enter → 3 Kinyerés');
    check((await p3.evaluate(() => document.activeElement.id)) === 'screen-title', 'a fókusz az új képernyő címére kerül');
    check((await p3.getAttribute('#nav-extraction', 'aria-current')) === 'page', 'aria-current="page"');
    check((await p3.$$('.ws-bar .ws-link')).length === 4, 'négy munkaterület a navigációban');
    check((await p3.getAttribute('#ws-extraction', 'aria-current')) === 'true', 'aktív munkaterület: Adatkinyerés és validálás');
    check((await p3.textContent('.ws-label')).indexOf('Adatkinyerés') >= 0, 'a munkaterület neve a cím felett');
    await p3.focus('#nav-prisma');
    await p3.keyboard.press('ArrowRight');
    check((await p3.evaluate(() => document.activeElement.id)) === 'nav-extraction', '→ a következő fülre lép');
    await p3.keyboard.press('End');
    check((await p3.evaluate(() => document.activeElement.id)) === 'nav-export', 'End → utolsó fül');
    await p3.keyboard.press('Home');
    check((await p3.evaluate(() => document.activeElement.id)) === 'nav-overview', 'Home → első fül');
    await p3.keyboard.press('ArrowLeft');
    check((await p3.evaluate(() => document.activeElement.id)) === 'nav-export', '← körbefordul');
    await p3.keyboard.press(' ');
    await screenIs(p3, 'export');
    check(true, 'Szóköz aktiválja a fület');
    await p3.evaluate(() => document.activeElement.blur());
    await p3.keyboard.press('Alt+4');
    await screenIs(p3, 'analysis');
    check((await p3.textContent('#screen-title')).indexOf('Elemzés') >= 0, 'Alt+4 → 4 Elemzés');
    await p3.keyboard.press('Alt+0');
    await screenIs(p3, 'overview');
    check(true, 'Alt+0 → Áttekintés');
    await p3.evaluate(() => document.activeElement.blur());
    await p3.keyboard.press('?');
    await p3.waitForSelector('[role="dialog"]', { timeout: 2000 });
    check((await p3.getAttribute('[role="dialog"]', 'aria-modal')) === 'true', 'súgó: role=dialog, aria-modal');
    check(await p3.evaluate(() => document.getElementById('app').inert === true), 'a háttér inert a modális alatt');
    await p3.keyboard.press('Tab');
    await p3.keyboard.press('Tab');
    await p3.keyboard.press('Tab');
    check(await p3.evaluate(() => !!document.activeElement.closest('[role="dialog"]')), 'fókuszcsapda a modálisban');
    await p3.keyboard.press('Escape');
    check((await p3.$('[role="dialog"]')) === null, 'Esc bezárja');
    await p3.goBack();
    await screenIs(p3, 'analysis');
    check(true, 'vissza gomb: előző fül');
    check(err3.length === 0, 'nincs konzolhiba: ' + err3.join(' || '));
  });

  await test('témaváltás: data-theme, aria-pressed, mag.pref.theme, megmarad újratöltés után', async () => {
    const before = await p3.getAttribute('html', 'data-theme');
    check(before === 'light' || before === 'dark', 'van data-theme (' + before + ')');
    await p3.click('#theme-toggle');
    const after = await p3.getAttribute('html', 'data-theme');
    check(after !== before && (after === 'light' || after === 'dark'), 'a téma váltott: ' + before + ' → ' + after);
    check((await p3.getAttribute('#theme-toggle', 'aria-pressed')) === (after === 'dark' ? 'true' : 'false'), 'aria-pressed');
    check((await p3.evaluate(() => localStorage.getItem('mag.pref.theme'))) === after, 'mag.pref.theme mentve');
    const bg1 = await p3.evaluate(() => getComputedStyle(document.body).backgroundColor);
    await p3.reload();
    await ready(p3);
    check((await p3.getAttribute('html', 'data-theme')) === after, 'újratöltés után is ' + after);
    await p3.click('#theme-toggle');
    const bg2 = await p3.evaluate(() => getComputedStyle(document.body).backgroundColor);
    check(bg1 !== bg2, 'a háttérszín a tokenekből követi a témát');
    check(err3.length === 0, 'nincs konzolhiba: ' + err3.join(' || '));
  });

  await test('nyelvváltás HU ↔ EN: felület, <html lang>, motor-szöveg, mag.pref.lang', async () => {
    await p3.goto(srv.base + '/dev.html?fixtures=1#/overview');
    await ready(p3);
    await p3.waitForSelector('#overview-outcomes');
    check((await p3.getAttribute('#lang-switch [data-lang="hu"]', 'aria-pressed')) === 'true', 'HU az alap');
    await p3.click('#lang-switch [data-lang="en"]');
    check((await p3.getAttribute('html', 'lang')) === 'en', '<html lang="en">');
    check((await p3.textContent('#nav-extraction')).indexOf('Extraction') >= 0, 'fül: Extraction');
    check((await p3.textContent('#screen-title')).indexOf('Overview') >= 0, 'cím: Overview');
    await p3.waitForSelector('#overview-outcomes');
    await p3.waitForFunction(() => document.getElementById('overview-outcomes').textContent.indexOf('0.49 [0.33; 0.73]') >= 0, null, { timeout: 3000 });
    check(true, 'a motor EN display_text-je jelenik meg (a felület nem formáz)');
    check((await p3.evaluate(() => localStorage.getItem('mag.pref.lang'))) === 'en', 'mag.pref.lang = en');
    check((await p3.textContent('.app-header')).indexOf('PROTECTED') >= 0, 'fejléc is EN');
    await p3.reload();
    await ready(p3);
    check((await p3.getAttribute('html', 'lang')) === 'en', 'újratöltés után is EN');
    await p3.click('#lang-switch [data-lang="hu"]');
    check((await p3.textContent('#nav-extraction')).indexOf('Kinyerés') >= 0, 'vissza HU');
    check(err3.length === 0, 'nincs konzolhiba: ' + err3.join(' || '));
  });

  await test('böngészőtároló: csak mag.pref.* és mag.token (7.6)', async () => {
    checkStorage(await storageReport(p3), 'dev');
  });
  await ctx3.close();

  // ========== 5. oldalon belüli önteszt
  await test('?selftest=1: az oldalon belüli állítások zöldek', async () => {
    const ctx = await browser.newContext();
    const p = await ctx.newPage();
    const err = watch(p, 'selftest');
    await p.goto(srv.base + '/dev.html?fixtures=1&selftest=1');
    await p.waitForSelector('html[data-selftest]', { timeout: 20000 });
    const out = await p.textContent('#selftest');
    const m = /SELFTEST pass=(\d+) fail=(\d+)/.exec(out);
    check(!!m && Number(m[1]) >= 80, 'legalább 80 állítás (' + (m ? m[1] : '?') + ')');
    check(!!m && m[2] === '0', 'fail=0' + (m && m[2] !== '0' ? '\n' + out.split('\n').filter((l) => l.startsWith('FAIL')).join('\n') : ''));
    check(err.length === 0, 'nincs konzolhiba: ' + err.join(' || '));
    checkStorage(await storageReport(p), 'selftest');
    await ctx.close();
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
