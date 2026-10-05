#!/usr/bin/env node
/* tests/gui/ui/snapshot.spec.js — a kitakaró, csak olvasható HTML-pillanatkép böngészős tesztje (terv 2.5, 7.6, 8.5).
 *
 * Futtatás:  node tests/gui/ui/snapshot.spec.js            (kilépési kód 1, ha bármi elbukik)
 * Előfeltétel: python3; Playwright (globális: /opt/node22/lib/node_modules/playwright vagy NODE_PATH).
 *
 * Mit csinál:
 *   1. lefuttatja a buildet (a dist/snapshot.html sablonnal együtt), két kis projektet készít (A és B osztály;
 *      a tests/gui/test_snapshot.py make_project-jével) és a pillanatképüket (python3 -m ma_gui.snapshot);
 *   2. fej nélküli Chromiumban, file://-ból és egy helyi http-szerverről is megnyitja, és ellenőrzi:
 *      - NULLA hálózati kérés (a dokumentumon kívül) — Playwright-kérésfigyeléssel és útvonal-elfogással;
 *      - NULLA CSP-sértés és konzolhiba; egy szándékos fetch-et a connect-src 'none' blokkol;
 *      - a képernyők a beágyazott adatból működnek: áttekintés, eredmények (13 forest-jelölő data-uid-del, a
 *        motor display_text-je), napló (szűrés), kinyerés (A: tábla; B: kitakarva, cellaérték nélkül);
 *      - író művelet (kapu rögzítése) → párbeszédablak a pontos paranccsal, a vágólapra másolva; semmi sem íródik;
 *      - a sáv, a „Mi van benne?” részletek, a nyelvváltás; HTML-t tartalmazó szöveg szövegként jelenik meg;
 *      - böngészőtároló: csak mag.pref.* (projektadat soha, token sem);
 *   3. végponttól végpontig a valódi ma_gui szerverrel (indítókód → token): Export → audit-ZIP kétszer → azonos
 *      sha256 (8.6), letöltés aláírt címről; pillanatkép tudomásulvétellel → letöltés → a letöltött fájl
 *      file://-ból hálózat nélkül nyílik.
 */
'use strict';

const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const { spawn, spawnSync } = require('child_process');

function loadPlaywright() {
  const candidates = ['playwright', '/opt/node22/lib/node_modules/playwright'];
  for (const c of candidates) {
    try { return require(c); } catch (e) { /* következő */ }
  }
  throw new Error('Playwright nem található (NODE_PATH vagy /opt/node22/lib/node_modules/playwright)');
}
const { chromium } = loadPlaywright();

const ROOT = path.resolve(__dirname, '..', '..', '..');
const BUILD = path.join(ROOT, 'ma_gui', 'web', 'build_gui.py');
const CHROME_FALLBACK = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const XSS = '<img src=x onerror=window.__xss=1>';

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

function run(cmd, args, opts) {
  const r = spawnSync(cmd, args, Object.assign({ cwd: ROOT, encoding: 'utf-8', maxBuffer: 64 * 1024 * 1024 }, opts || {}));
  if (r.status !== 0) { throw new Error(cmd + ' ' + args.join(' ') + ' → ' + r.status + '\n' + r.stdout + r.stderr); }
  return r.stdout;
}

// ---------------------------------------------------------------- előkészítés
function makeSnapshots(tmp) {
  const home = path.join(tmp, 'home');
  fs.mkdirSync(home);
  const py = [
    'import sys, json',
    'sys.path.insert(0, "tests/gui"); sys.path.insert(0, ".")',
    'import test_snapshot as T',
    'from metaelemzes import projekt',
    'out = {}',
    'for c in ("A", "B"):',
    '    root = T.make_project(sys.argv[1], c, "proj" + c)',
    '    projekt.add_finding(root, "reviewer", "blocker", ' + JSON.stringify(XSS) + ', detail="html", stage="S05")',
    '    out[c] = root',
    'print(json.dumps(out))'
  ].join('\n');
  const roots = JSON.parse(run('python3', ['-c', py, tmp], { env: Object.assign({}, process.env, { HOME: home }) }).trim().split('\n').pop());
  const files = {};
  for (const c of Object.keys(roots)) {
    files[c] = path.join(tmp, 'snap' + c + '.snapshot.html');
    run('python3', ['-m', 'ma_gui.snapshot', '--project', roots[c], '--out', files[c]], { env: Object.assign({}, process.env, { HOME: home }) });
  }
  return { roots, files };
}

function serveFile(file) {
  const body = fs.readFileSync(file);
  const seen = [];
  const srv = http.createServer((req, res) => {
    seen.push(req.method + ' ' + req.url);
    if (req.url === '/snap.html' || req.url.indexOf('/snap.html?') === 0) {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      res.end(body);
      return;
    }
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('nincs');
  });
  return new Promise((resolve) => srv.listen(0, '127.0.0.1', () => resolve({
    base: 'http://127.0.0.1:' + srv.address().port, seen,
    close: () => new Promise((r) => srv.close(r))
  })));
}

function startServer(root, home) {
  const py = [
    'import sys',
    'sys.path.insert(0, ".")',
    'from ma_gui import server',
    'app = server.App(sys.argv[1], selftest=False, kb_build=False, caps_refresh=False, idle_hours=0, log_stream=None,',
    '                 privacy_home=sys.argv[2], privacy_env={})',
    'app.start(port=0)',
    'print("URL=" + app.launch_url(), flush=True)',
    'app.serve_forever()'
  ].join('\n');
  const proc = spawn('python3', ['-c', py, root, home], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
  let buf = '';
  let err = '';
  proc.stderr.on('data', (d) => { err += d; });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('a szerver nem indult el: ' + err)), 30000);
    proc.stdout.on('data', (d) => {
      buf += d;
      const m = /URL=(\S+)/.exec(buf);
      if (m) { clearTimeout(timer); resolve({ url: m[1], stop: () => proc.kill() }); }
    });
    proc.on('exit', (code) => { clearTimeout(timer); reject(new Error('a szerver kilépett (' + code + '): ' + err)); });
  });
}

// ---------------------------------------------------------------- oldal-segédek
function watch(page) {
  const w = { requests: [], errors: [], csp: [] };
  page.on('request', (r) => w.requests.push(r.url()));
  page.on('console', (m) => {
    const txt = m.text();
    if (m.type() === 'error') { w.errors.push(txt); }
    if (/Content Security Policy|Refused to/i.test(txt)) { w.csp.push(txt); }
  });
  page.on('pageerror', (e) => w.errors.push('pageerror: ' + e.message));
  return w;
}

async function cspCounter(ctx) {
  await ctx.addInitScript(() => {
    window.__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => { window.__csp.push(e.violatedDirective + ' ' + e.blockedURI); });
  });
}

async function ready(page) {
  await page.waitForSelector('html[data-ready="1"]', { timeout: 20000 });
  await page.waitForSelector('#snapshot-banner', { timeout: 5000 });
}

async function screen(page, id) {
  await page.waitForFunction((x) => {
    const r = window.MA && window.MA.app && window.MA.app.current();
    return r && r.id === x && !document.querySelector('#screen-root .spinner, .screen-host > .spinner');
  }, id, { timeout: 15000 });
  await page.waitForTimeout(300);
}

async function txt(page, sel) { return (await page.textContent(sel)) || ''; }

function onlyDocument(urls, docUrl) {
  return urls.filter((u) => u.split('#')[0] !== docUrl.split('#')[0]);
}

// ---------------------------------------------------------------- tesztek
(async () => {
  run('python3', [BUILD]);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ma_snap_ui_'));
  const { roots, files } = makeSnapshots(tmp);
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
  } catch (e) {
    browser = await chromium.launch({ headless: true, executablePath: CHROME_FALLBACK });
  }

  const urlA = 'file://' + files.A;
  const ctx = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'] });
  await cspCounter(ctx);
  const page = await ctx.newPage();
  const w = watch(page);

  await test('file:// — betöltés hálózat, CSP-sértés és konzolhiba nélkül; sáv és csak olvasható mód', async () => {
    await page.goto(urlA + '#/overview');
    await ready(page);
    await screen(page, 'overview');
    check((await page.getAttribute('html', 'data-mode')) === 'snapshot', 'html[data-mode=snapshot]');
    const banner = await txt(page, '#snapshot-banner');
    check(banner.indexOf('Pillanatkép') >= 0 && banner.indexOf('csak olvasható') >= 0, 'a sáv: „Pillanatkép — csak olvasható”');
    check(banner.indexOf('Claude Artifact') >= 0, 'a sáv: ne töltsd fel, ne publikáld (Artifactként sem)');
    check(/azonosító: [0-9a-f]{12}/.test(banner), 'állapot-azonosító a sávban');
    const ov = await txt(page, '#main');
    check(ov.indexOf('TBC') >= 0, 'áttekintés: a kimenet sora (TBC)');
    check(ov.indexOf('nincs a pillanatképben') < 0 || ov.indexOf('project audit') >= 0, 'áttekintés: a fő adatok betöltve');
    check(onlyDocument(w.requests, urlA).length === 0, 'nulla hálózati kérés (a dokumentumon kívül): ' + onlyDocument(w.requests, urlA).join(', '));
    check(w.csp.length === 0 && (await page.evaluate(() => window.__csp.length)) === 0, 'nulla CSP-sértés: ' + w.csp.join(' | '));
    check(w.errors.length === 0, 'nincs konzolhiba: ' + w.errors.join(' | '));
  });

  await test('eredmények: forest a beágyazott plot/v2-ből (13 jelölő, data-uid, motor-szöveg)', async () => {
    await page.goto(urlA + '#/results?outcome=o1');
    await screen(page, 'results');
    await page.waitForSelector('#results-grid svg g[data-uid]', { timeout: 10000 });
    const n = await page.$$eval('#results-grid svg g[data-uid]', (gs) => new Set(gs.map((g) => g.getAttribute('data-uid'))).size);
    check(n === 13, '13 vizsgálat-jelölő (' + n + ')');
    const eff = await txt(page, '#results-effect');
    check(/\[.*;.*\]/.test(eff), 'összesített hatás a motor szövegével (' + eff + ')');
    check((await txt(page, '#results-run-badge')).length > 0, 'futás-jelvény (AKTUÁLIS/ELAVULT)');
    check(onlyDocument(w.requests, urlA).length === 0, 'továbbra sincs hálózati kérés');
  });

  await test('napló: tételek, szűrés a beágyazott adaton; HTML-t tartalmazó cím szövegként', async () => {
    await page.goto(urlA + '#/log');
    await screen(page, 'log');
    const body = await txt(page, '#log-main');
    check(body.indexOf('SE/SD csere gyanú') >= 0, 'a megállapítás látszik');
    check(body.indexOf(XSS) >= 0, 'a HTML-t tartalmazó cím szó szerint, szövegként');
    check((await page.evaluate(() => window.__xss)) === undefined, 'nem futott le injektált kód');
    await page.selectOption('#log-f-severity', 'blocker');
    await page.waitForTimeout(400);
    const filtered = await txt(page, '#log-main');
    check(filtered.indexOf(XSS) >= 0 && filtered.indexOf('SE/SD csere gyanú') < 0, 'szűrés súlyosságra (blocker) a pillanatképben is működik');
  });

  await test('író művelet → pontos parancs (Windows és macOS/Linux), vágólapon; semmi sem íródik', async () => {
    await page.goto(urlA + '#/log');
    await screen(page, 'log');
    await page.selectOption('#log-gate-stage', 'S09');
    await page.selectOption('#log-gate-verdict', 'PASS');
    await page.fill('#log-gate-summary', 'Rendben, átnézve');
    await page.click('#log-gate-submit');
    await page.waitForSelector('#snapshot-cmd-posix', { timeout: 5000 });
    const posix = await txt(page, '#snapshot-cmd-posix');
    const win = await txt(page, '#snapshot-cmd-windows');
    check(posix === "python3 ma.py project checkpoint '<projektmappa>' --stage S09 --agent user --verdict PASS --summary 'Rendben, átnézve'", 'POSIX-parancs: ' + posix);
    check(win === 'py -3 ma.py project checkpoint "<projektmappa>" --stage S09 --agent user --verdict PASS --summary "Rendben, átnézve"', 'Windows-parancs: ' + win);
    await page.waitForFunction(() => /vágólapra/.test((document.getElementById('snapshot-cmd-status') || {}).textContent || ''), null, { timeout: 3000 }).catch(() => null);
    let clip = '';
    try { clip = await page.evaluate(() => navigator.clipboard.readText()); } catch (e) { clip = ''; }
    check(clip === posix, 'a parancs a vágólapon (' + clip + ')');
    const dialog = await txt(page, '[role="dialog"]');
    check(dialog.indexOf('csak olvasható') >= 0 && dialog.indexOf('metaanalizis-asszisztens') >= 0, 'kezdőknek szóló magyarázat a párbeszédablakban');
    await page.keyboard.press('Escape');
    check(onlyDocument(w.requests, urlA).length === 0, 'az író kérés sem ment ki a hálózatra');
  });

  await test('kinyerés: A osztály — a tábla benne van; „Mi van benne?” részletek; nyelvváltás', async () => {
    await page.goto(urlA + '#/extraction?outcome=o1');
    await screen(page, 'extraction');
    await page.waitForSelector('[role="grid"]', { timeout: 10000 });
    const grid = await txt(page, '[role="grid"]');
    check(grid.indexOf('54321') >= 0 && grid.indexOf('Aronson 1948') >= 0, 'a tábla cellái látszanak (A osztály)');
    await page.click('#snapshot-info');
    await page.waitForSelector('#snapshot-state-id');
    const info = await txt(page, '[role="dialog"]');
    check(info.indexOf('eredet-idézetek ki') >= 0 && info.indexOf('_privat/**') >= 0 && info.indexOf('**/*.pdf') >= 0, 'részletek: kitakarások és kizárt minták');
    await page.keyboard.press('Escape');
    await page.click('#lang-switch [data-lang="en"]');
    await page.waitForTimeout(300);
    check((await txt(page, '#snapshot-banner')).indexOf('read-only') >= 0, 'EN: a sáv is angolul');
    await page.click('#lang-switch [data-lang="hu"]');
  });

  await test('kinyerés pillanatképben: csak olvasható rács, tiltott író gombok, nincs „mentetlen” kérdés (UX-16)', async () => {
    await page.goto(urlA + '#/extraction?outcome=o1');
    await screen(page, 'extraction');
    await page.waitForSelector('[role="grid"] td[role="gridcell"]', { timeout: 10000 });
    check((await page.getAttribute('[role="grid"]', 'aria-readonly')) === 'true', 'a rács aria-readonly="true"');
    const note = await txt(page, '#ex-readonly');
    check(note.indexOf('Csak olvasható') >= 0 && note.indexOf('nem szerkeszthető') >= 0, 'a „csak olvasható” megjegyzés látszik: ' + note);
    const dis = await page.$$eval('#ex-add-row, #ex-del-row, #ex-paste, #ex-convert, #ex-docs-btn, #ex-undo, #ex-redo, #ex-save',
      (bs) => bs.map((b) => b.id + ':' + b.disabled));
    check(dis.length === 8 && dis.every((x) => /:true$/.test(x)), 'minden író gomb tiltva: ' + dis.join(', '));
    const before = await txt(page, '[role="grid"] tbody');
    const td = await page.$('[role="grid"] tbody tr:first-child td[role="gridcell"]:nth-of-type(2)');
    await td.click();
    await page.keyboard.press('F2');
    await page.keyboard.type('999');
    await page.keyboard.press('Enter');
    await page.keyboard.press('Delete');
    await page.waitForTimeout(300);
    check(!(await page.$('[role="grid"] input, [role="grid"] textarea')), 'a cellában nem nyílik szerkesztő');
    check((await txt(page, '[role="grid"] tbody')) === before, 'gépelés / Delete nem változtat a táblán');
    check((await txt(page, '#screen-root')).indexOf('nem mentett módosítás') < 0, 'nincs „nem mentett módosítás” jelzés');
    await page.evaluate(() => { window.location.hash = '#/overview'; });
    await screen(page, 'overview');
    check(!(await page.$('.modal-backdrop')), 'elnavigáláskor nincs „Maradok / Elvetés / Mentés” kérdés');
    check(onlyDocument(w.requests, urlA).length === 0, 'semmi sem ment ki a hálózatra');
  });

  await test('Windows: nem biztonságosan idézhető szöveg → nincs egysoros Windows-parancs, semmi sem kerül a vágólapra (WS-2)', async () => {
    const wctx = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'],
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36' });
    await wctx.addInitScript(() => { Object.defineProperty(Navigator.prototype, 'platform', { get: () => 'Win32' }); });
    const wp = await wctx.newPage();
    const ww = watch(wp);
    try {
      await wp.goto(urlA + '#/log');
      await ready(wp);
      await screen(wp, 'log');
      check(await wp.evaluate(() => window.MA.shell.isWindows()), 'a böngésző Windowsnak látszik');
      // biztonságos szöveg: a Windows-sor kerül a vágólapra
      await wp.selectOption('#log-gate-stage', 'S09');
      await wp.selectOption('#log-gate-verdict', 'PASS');
      await wp.fill('#log-gate-summary', 'Rendben, átnézve');
      await wp.click('#log-gate-submit');
      await wp.waitForSelector('#snapshot-cmd-windows', { timeout: 5000 });
      const winSafe = await txt(wp, '#snapshot-cmd-windows');
      await wp.waitForFunction(() => /vágólapra/.test((document.getElementById('snapshot-cmd-status') || {}).textContent || ''), null, { timeout: 3000 }).catch(() => null);
      let clip = await wp.evaluate(() => navigator.clipboard.readText()).catch(() => '');
      check(clip === winSafe && /^py -3 ma\.py /.test(clip), 'Windowson a Windows-sor kerül a vágólapra (' + clip + ')');
      await wp.keyboard.press('Escape');
      // veszélyes szöveg: " % $ ` → nincs Windows-sor, a vágólap érintetlen
      await wp.evaluate(() => navigator.clipboard.writeText('ÉRINTETLEN'));
      const evil = 'Rendben "x" & 100% $env:USERNAME `whoami`';
      await wp.fill('#log-gate-summary', evil);
      await wp.click('#log-gate-submit');
      await wp.waitForSelector('#snapshot-cmd-posix', { timeout: 5000 });
      const unsafe = await wp.$eval('#snapshot-cmd-windows', (e) => ({ tag: e.tagName, cls: e.className, text: e.textContent }));
      check(unsafe.tag === 'P' && /snap-cmd-unsafe/.test(unsafe.cls) && unsafe.text.indexOf('--summary') >= 0 && unsafe.text.indexOf('nem lehet biztonságosan') >= 0,
        'a Windows-blokk helyett magyarázat a kapcsoló nevével: ' + unsafe.text);
      check(unsafe.text.indexOf('whoami') < 0 && unsafe.text.indexOf('USERNAME') < 0, 'a magyarázat nem ismétli a veszélyes szöveget');
      const posix = await txt(wp, '#snapshot-cmd-posix');
      check(posix.endsWith("--summary 'Rendben \"x\" & 100% $env:USERNAME `whoami`'"), 'a POSIX-sor egyetlen egyszeres idézőjeles argumentum: ' + posix);
      const winCopy = await wp.$$eval('[role="dialog"] .snap-cmd', (bs) => bs.map((b) => !!b.querySelector('button')));
      check(winCopy.length === 2 && winCopy[0] === false && winCopy[1] === true, 'a Windows-blokkban nincs másológomb (' + winCopy.join(',') + ')');
      await wp.waitForTimeout(500);
      clip = await wp.evaluate(() => navigator.clipboard.readText()).catch(() => '');
      check(clip === 'ÉRINTETLEN', 'a vágólapra semmi sem került (' + clip + ')');
      check(((await wp.textContent('#snapshot-cmd-status')) || '') === '', 'nincs „vágólapra másolva” üzenet');
      check(ww.errors.length === 0, 'nincs konzolhiba: ' + ww.errors.join(' | '));
    } finally {
      await wctx.close();
    }
  });

  await test('böngészőtároló: csak mag.pref.* (projektadat és token soha, 7.6)', async () => {
    const st = await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }));
    check(st.local.every((k) => k.indexOf('mag.pref.') === 0), 'localStorage: ' + st.local.join(','));
    check(st.session.length === 0, 'sessionStorage üres: ' + st.session.join(','));
    check(w.errors.length === 0, 'nincs konzolhiba: ' + w.errors.join(' | '));
    check((await page.evaluate(() => window.__csp.length)) === 0, 'nulla CSP-sértés');
  });
  await ctx.close();

  // ---------------------------------------------------------------- B osztály http-n, útvonal-elfogással
  await test('B osztály http-ről: a tábla kitakarva, cellaérték sehol; nulla kérés; connect-src \'none\' blokkol', async () => {
    const srv = await serveFile(files.B);
    const c2 = await browser.newContext();
    await cspCounter(c2);
    const routed = [];
    await c2.route('**/*', (route) => { routed.push(route.request().url()); route.continue(); });
    const p2 = await c2.newPage();
    const w2 = watch(p2);
    const url = srv.base + '/snap.html';
    await p2.goto(url + '#/extraction?outcome=o1');
    await ready(p2);
    await screen(p2, 'extraction');
    await p2.waitForTimeout(500);
    const main = await txt(p2, '#main');
    check(main.indexOf('B osztály') >= 0 || main.indexOf('nincs a pillanatképben') >= 0, 'kinyerés: kitakarva (B osztály)');
    check((await p2.content()).indexOf('54321') < 0, 'B: a cellaérték nincs a lapban');
    await p2.goto(url + '#/results?outcome=o1');
    await screen(p2, 'results');
    await p2.waitForSelector('#results-grid svg g[data-uid]', { timeout: 10000 });
    check((await p2.$$('#results-grid svg g[data-uid]')).length >= 13, 'B: az ábra a becslésekkel megvan');
    check(onlyDocument(routed, url).length === 0 && srv.seen.length === 1, 'útvonal-elfogás: csak a dokumentum (' + routed.join(', ') + ' / ' + srv.seen.join(', ') + ')');
    check(w2.errors.length === 0 && (await p2.evaluate(() => window.__csp.length)) === 0, 'nincs konzolhiba / CSP-sértés: ' + w2.errors.join(' | '));
    // szándékos hálózati kísérlet: a CSP (connect-src 'none') blokkolja, és sértésként jelenti
    const res = await p2.evaluate((u) => window.fetch(u + '/kiszivargas').then(() => 'ment', () => 'blokkolva'), srv.base);
    await p2.waitForTimeout(200);
    check(res === 'blokkolva', 'fetch blokkolva (' + res + ')');
    check(srv.seen.length === 1, 'a szerverhez nem jutott el kérés: ' + srv.seen.join(', '));
    check((await p2.evaluate(() => window.__csp.filter((x) => /connect-src/.test(x)).length)) >= 1, 'CSP-sértés jelentve (connect-src)');
    await c2.close();
    await srv.close();
  });

  // ---------------------------------------------------------------- végponttól végpontig, valódi szerverrel
  await test('élő szerver: audit-ZIP kétszer → azonos sha256; pillanatkép → letöltés → offline megnyitás', async () => {
    const srv = await startServer(roots.A, path.join(tmp, 'home'));
    const c3 = await browser.newContext({ acceptDownloads: true });
    try {
      const p3 = await c3.newPage();
      const w3 = watch(p3);
      await p3.goto(srv.url);
      await p3.waitForSelector('html[data-ready="1"]', { timeout: 20000 });
      await p3.goto(srv.url.split('#')[0] + '#/export');
      await p3.waitForSelector('#exp-audit');
      const shas = [];
      for (let i = 0; i < 2; i++) {
        await p3.click('#exp-audit');
        await p3.waitForSelector('#exp-audit-result .exp-sha', { timeout: 30000 });
        shas.push(await txt(p3, '#exp-audit-result .exp-sha'));
        if (i === 0) { await p3.evaluate(() => { document.getElementById('exp-audit-result').remove(); }); }
      }
      check(/^[0-9a-f]{64}$/.test(shas[0]) && shas[0] === shas[1], 'audit-ZIP kétszer → azonos sha256 (' + shas.join(' / ') + ')');
      const [dl] = await Promise.all([p3.waitForEvent('download'), p3.click('#exp-audit-download')]);
      const zipPath = await dl.path();
      const zipSha = require('crypto').createHash('sha256').update(fs.readFileSync(zipPath)).digest('hex');
      check(zipSha === shas[0] && /\.zip$/.test(dl.suggestedFilename()), 'a letöltött ZIP sha256-ja egyezik (' + dl.suggestedFilename() + ')');
      check(await p3.$eval('#exp-snapshot', (b) => b.disabled), 'pillanatkép: tudomásulvétel nélkül tiltva');
      await p3.check('#exp-ack');
      await p3.click('#exp-snapshot');
      await p3.waitForSelector('#exp-snapshot-download', { timeout: 30000 });
      const [dl2] = await Promise.all([p3.waitForEvent('download'), p3.click('#exp-snapshot-download')]);
      const snapPath = path.join(tmp, 'letoltott.snapshot.html');
      await dl2.saveAs(snapPath);
      check(/\.snapshot\.html$/.test(dl2.suggestedFilename()), 'letöltés csatolmányként: ' + dl2.suggestedFilename());
      check(w3.errors.length === 0, 'élő felület: nincs konzolhiba: ' + w3.errors.join(' | '));
      // a letöltött pillanatkép offline
      const p4 = await c3.newPage();
      const w4 = watch(p4);
      const u4 = 'file://' + snapPath;
      await p4.goto(u4 + '#/results?outcome=o1');
      await ready(p4);
      await p4.waitForSelector('#results-grid svg g[data-uid]', { timeout: 10000 });
      check(onlyDocument(w4.requests, u4).length === 0 && w4.errors.length === 0, 'a letöltött pillanatkép hálózat és hiba nélkül nyílik');
    } finally {
      await c3.close();
      srv.stop();
    }
  });

  await browser.close();
  fs.rmSync(tmp, { recursive: true, force: true });
  const failed = results.filter((r) => !r.pass);
  console.log('\n' + (results.length - failed.length) + '/' + results.length + ' ellenőrzés zöld' + (failed.length ? ', ' + failed.length + ' HIBÁS' : ''));
  failed.forEach((f) => console.log('  ✖ [' + f.test + '] ' + f.msg));
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
