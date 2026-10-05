#!/usr/bin/env node
/* tests/gui/ui/extraction_dual.spec.js — v1: kettős kinyerés és egyeztetés (terv 3.5.5, 4.9, 6.4 X009; 11/5. és 11/6.
 * döntés), a rács virtualizációja (3.3) és a kumulatív / buborék ábrák (3.5.8, E4c) böngészős tesztje.
 *
 * Futtatás:  node tests/gui/ui/extraction_dual.spec.js        (kilépési kód 1, ha bármi elbukik)
 *   MA_UI_DEV_HTML=<út>  — kész dev-build használata (a build kimarad).
 * A. rész — dev-build ?fixtures=1-gyel (src/dev/extraction_dual_backend.js; a borítékok a VALÓDI szerverből:
 *   tests/gui/ui/gen_extraction_fixtures.py), szigorú CSP: a motor összegzése és κ-ja kész szövegként, eltérés-lista
 *   állapotjellel (szimbólum + szöveg), billentyűzet (↑/↓, Enter, A/B gyorsválasztás), indoklás nélküli döntés tiltva,
 *   döntés If-Match-csel, saját érték, visszavonás, szűrés/rendezés, X009-kapu → CSV-írás, átvétel a kimenet táblájába
 *   (előnézet + megerősítés + a tábla ETag-je), 409, táblák egymás mellett (cella-diff, Enter → döntés), motor nélkül
 *   424, fájlcsere (fájlválasztó → bájthű base64, felülírás megerősítéssel, beérkezett mappa), export és sablon,
 *   virtualizáció 1500 eltérésnél és 1500 soros kinyerés-rácsnál, kumulatív forest és buborékábra a motor
 *   geometriájával, nyelvváltás, DOM-biztonság, böngészőtároló-szabály, i18n-teljesség.
 * B. rész — a VALÓDI szerverrel és a TERMÉK-builddel (tests/gui/ui/extraction_dual_server.py): B-import a
 *   felületről, minden eltérés eldöntése, konszenzus-CSV a lemezen, activity szabad szöveg nélkül; motor nélkül 424.
 */
'use strict';

const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const crypto = require('crypto');
const { spawn, spawnSync } = require('child_process');

function loadPlaywright() {
  for (const c of ['playwright', '/opt/node22/lib/node_modules/playwright']) {
    try { return require(c); } catch (e) { /* következő */ }
  }
  throw new Error('Playwright nem található');
}
const { chromium } = loadPlaywright();

const ROOT = path.resolve(__dirname, '..', '..', '..');
const WEB = path.join(ROOT, 'ma_gui', 'web');
const DEV = process.env.MA_UI_DEV_HTML || path.join(WEB, 'dist', 'index.dev.html');
const CHROME_FALLBACK = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const HELPER = path.join(__dirname, 'extraction_dual_server.py');
const FXD = JSON.parse(fs.readFileSync(path.join(WEB, 'fixtures', 'extraction_dual.json'), 'utf-8'));
const FXP = JSON.parse(fs.readFileSync(path.join(WEB, 'fixtures', 'extraction_plots.json'), 'utf-8'));
const SEP = '␟';
const SECRET = 'Titkos-Indoklás-Kovács';

const CSP = (n) => "default-src 'none'; script-src 'nonce-" + n + "'; style-src 'nonce-" + n + "'; " +
  "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

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
  try { await fn(); } catch (e) { check(false, 'kivétel: ' + (e && e.stack ? e.stack.split('\n').slice(0, 6).join(' | ') : e)); }
}

function fxRoute(method, p, body) {
  return FXD.routes.find((r) => r.method === method && r.path === p && JSON.stringify(r.body || null) === JSON.stringify(body || null));
}
const CMP = fxRoute('POST', '/api/compare', { outcome: 'o1' });
const VIEW = CMP.envelope.data;
const PLOT_BUBBLE = FXP.routes[0].envelope.data;
const PLOT_CAT = FXP.routes[1].envelope.data;
const X10 = VIEW.items.find((it) => it.field === 'e1' && it.key.indexOf('Ferguson') === 0);
const HU = JSON.parse(fs.readFileSync(path.join(WEB, 'src', 'i18n', 'hu', 'extraction_dual.json'), 'utf-8'));
const WHY_ASKS = Object.keys(HU).filter((k) => /^dual\.why\.[a-z0-9_]+\.asks$/.test(k) && k !== 'dual.why.gate.asks').map((k) => HU[k]);

// ---------------------------------------------------------------- szerverek
function startStatic() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (req.method === 'GET' && url.pathname === '/') {
      const nonce = crypto.randomBytes(18).toString('base64url');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': CSP(nonce), 'Cache-Control': 'no-store' });
      res.end(fs.readFileSync(DEV, 'utf-8').split('{{CSP_NONCE}}').join(nonce));
      return;
    }
    res.writeHead(404); res.end();
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({
    base: 'http://127.0.0.1:' + server.address().port,
    close: () => new Promise((r) => { if (server.closeAllConnections) { server.closeAllConnections(); } server.close(r); })
  })));
}

function startReal(tmp, flags) {
  const proc = spawn('python3', [HELPER, 'serve', tmp].concat(flags || []), { cwd: ROOT, stdio: ['pipe', 'pipe', 'pipe'] });
  let buf = '', err = '';
  proc.stderr.on('data', (d) => { err += d; });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('a szerver nem indult: ' + err)), 120000);
    proc.stdout.on('data', (d) => {
      buf += d;
      const i = buf.indexOf('\n');
      if (i >= 0) { clearTimeout(timer); resolve(JSON.parse(buf.slice(0, i))); }
    });
    proc.on('exit', (c) => { clearTimeout(timer); reject(new Error('a szerver kilépett (' + c + '): ' + err)); });
  }).then((info) => Object.assign(info, {
    stop: () => new Promise((resolve) => { proc.removeAllListeners('exit'); proc.on('exit', resolve); proc.stdin.write('quit\n'); setTimeout(() => proc.kill(), 15000); })
  }));
}

// ---------------------------------------------------------------- segédek
let browser, stat;
async function openDev(hash, opts) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [], dialogs = [];
  page.on('console', (m) => { if (m.type() === 'error') { errors.push('console: ' + m.text()); } });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => { dialogs.push(d.type() + ':' + d.message()); d.dismiss().catch(() => null); });
  await page.goto(stat.base + '/?fixtures=1' + ((opts && opts.query) || '') + (hash || ''));
  await page.waitForSelector('html[data-ready="1"]', { timeout: 10000 });
  return { ctx, page, errors, dialogs };
}
async function screen(page, id) {
  await page.waitForFunction((x) => { const r = document.getElementById('screen-root'); return r && r.dataset.screen === x && !r.querySelector(':scope > .panel[aria-busy="true"]'); }, id, { timeout: 10000 });
}
async function dualReady(page) {
  await screen(page, 'dual');
  await page.waitForSelector('#dx-items table[role="grid"], #dx-engine-missing, #dx-need-files, #dx-error', { timeout: 10000 });
}
async function goDual(page, outcome, extra) {
  await page.evaluate((h) => { window.location.hash = h; }, '#/dual?outcome=' + outcome + (extra || ''));
  await dualReady(page);
}
async function txt(page, sel) { const el = await page.$(sel); return el ? ((await el.textContent()) || '') : ''; }
async function count(page, sel) { return (await page.$$(sel)).length; }
async function calls(page, method, p) {
  return page.evaluate(([m, pp]) => window.MA.dev.fixtures.calls.filter((c) => c.method === m && c.path === pp), [method, p]);
}
async function waitToast(p, re) {
  await p.waitForFunction((s) => new RegExp(s).test(((document.getElementById('ma-toasts') || {}).textContent || '') + ((document.getElementById('ma-alerts') || {}).textContent || '')), re.source, { timeout: 15000 });
}
async function counterText(p) { return txt(p, '#dx-counter'); }
function rowSel(it) { return '#dx-items tr[data-key="' + (it.key + SEP + it.field).replace(/"/g, '\\"') + '"]'; }
async function selectItem(p, it) {
  await p.click(rowSel(it) + ' [role="gridcell"][data-col="field"]');
  await p.waitForFunction((k) => (document.getElementById('dx-detail-h') || {}).textContent.indexOf(k) >= 0, it.key.slice(0, 12));
}
async function decide(p, chosen, reason, next) {
  await p.check('#dx-detail .dx-choice input[type="radio"][value="' + chosen + '"]');
  await p.fill('#dx-reason', reason);
  const before = await counterText(p);
  await p.click(next ? '#dx-decide-next' : '#dx-decide');
  await p.waitForFunction((b) => (document.getElementById('dx-counter') || {}).textContent !== b, before, { timeout: 10000 });
}
async function decideAll(p, reason) {
  for (let i = 0; i < 40; i++) {
    const open = await p.$('#dx-detail .dx-choice input[type="radio"]');
    const c = await counterText(p);
    const m = /(\d+)\/(\d+)/.exec(c);
    if (!m || m[1] === m[2] || !open) { return; }
    await p.check('#dx-detail .dx-choice input[type="radio"][value="a"]');
    await p.fill('#dx-reason', reason + ' #' + i);
    await p.click('#dx-decide-next');
    await p.waitForFunction((b) => (document.getElementById('dx-counter') || {}).textContent !== b, c, { timeout: 10000 });
  }
}
async function finish(o, label) {
  check(o.errors.length === 0, label + ': nincs konzolhiba / CSP-sértés: ' + o.errors.join(' || '));
  check(o.dialogs.length === 0, label + ': nem nyílt alert/confirm (XSS-próba): ' + o.dialogs.join(' || '));
  const rep = await o.page.evaluate(() => {
    const ls = [], ss = [];
    for (let i = 0; i < localStorage.length; i++) { ls.push(localStorage.key(i)); }
    for (let i = 0; i < sessionStorage.length; i++) { ss.push(sessionStorage.key(i)); }
    return { ls, ss, vals: ls.map((k) => localStorage.getItem(k)), img: document.querySelectorAll('img, [onerror]').length };
  });
  check(rep.ls.every((k) => k.startsWith('mag.pref.')), label + ': localStorage csak mag.pref.* (' + rep.ls.join(',') + ')');
  check(rep.vals.every((v) => !/Ferguson|Rosenthal|13598|Titkos|Table 2/.test(v)), label + ': a preferenciákban nincs projektadat');
  check(rep.ss.every((k) => k === 'mag.token'), label + ': sessionStorage csak mag.token');
  check(rep.img === 0, label + ': nincs <img>/onerror a DOM-ban');
  const missing = await o.page.evaluate(() => window.MA.i18n.missing());
  check(missing.length === 0, label + ': nincs hiányzó i18n-kulcs (' + missing.join(', ') + ')');
  await o.ctx.close();
}

// ================================================================ A. rész — fixture-háttér
async function partA() {
  await test('regisztráció: a „Kettős kinyerés” valódi képernyő a 3 Kinyerés alatt; link a kinyerésből', async () => {
    const o = await openDev('#/extraction?outcome=o1');
    const p = o.page;
    await screen(p, 'extraction');
    await p.waitForSelector('a[href="#/dual?outcome=o1"]');
    check(/Kettős kinyerés/.test(await txt(p, '.app-subnav')), 'al-navigáció: Kettős kinyerés');
    await p.click('a.btn[href="#/dual?outcome=o1"]');
    await dualReady(p);
    check(!(await p.$('.placeholder')), 'nem helyőrző');
    check((await txt(p, '#dx-head-h')) === 'Kettős kinyerés', 'cím');
    check((await p.$eval('#dx-outcome', (s) => s.value)) === 'o1', 'kimenet: o1');
    check((await txt(p, '#dx-file-a')).indexOf('03_adatok/kettos/o1.A.csv') >= 0 && (await txt(p, '#dx-file-b')).indexOf('o1.B.csv') >= 0, 'A/B fájlok útja');
    check((await p.$eval('#dx-rater-a', (i) => i.value)) === 'SzK' && (await p.$eval('#dx-rater-b', (i) => i.value)) === 'KP', 'monogramok a konszenzus-fájlból');
    await finish(o, 'regisztráció');
  });

  await test('összegzés: egyezés, eltérés, κ CI-vel — a motor kész szövegei; Methods-szöveg', async () => {
    const o = await openDev('#/dual?outcome=o1');
    const p = o.page;
    await dualReady(p);
    const sm = VIEW.compare.summary;
    const line = await txt(p, '#dx-agreement');
    check(line.indexOf(sm.agree + '/' + sm.cells_compared) >= 0, 'egyezés a motor darabszámaival: ' + line.slice(0, 80));
    check(line.indexOf(sm.agreement_pct_text.hu) >= 0, 'egyezési arány a motor szövegével');
    check(line.indexOf(sm.by_field.rob.kappa_text.hu) >= 0, 'κ(rob) a motor szövegével');
    check((await count(p, '#dx-fields tbody tr')) === Object.keys(sm.by_field).length, 'oszloponkénti tábla');
    check((await count(p, '#dx-fields svg.dx-bar')) === Object.keys(sm.by_field).length, 'sávok (csak pixel)');
    const rob = await txt(p, '#dx-fields tr[data-field="rob"]');
    check(rob.indexOf(sm.by_field.rob.kappa_text.hu) >= 0, 'κ a rob sorban');
    await p.click('#dx-methods');
    await p.waitForSelector('#dx-methods-en');
    check((await txt(p, '#dx-methods-en')) === VIEW.agreement_text.en, 'Methods-szöveg (EN) szó szerint a motoré');
    check((await txt(p, '#dx-methods-hu')) === VIEW.agreement_text.hu, 'Methods-szöveg (HU)');
    await p.keyboard.press('Escape');
    await finish(o, 'összegzés');
  });

  await test('eltérés-lista: minden tétel állapotjellel (szimbólum + szöveg), súgó és hatás a motorból, HTML-címke szövegként', async () => {
    const o = await openDev('#/dual?outcome=o1');
    const p = o.page;
    await dualReady(p);
    check((await count(p, '#dx-items tbody tr[data-key]')) === VIEW.items.length, 'minden tétel egy sor (' + VIEW.items.length + ')');
    check((await p.getAttribute('#dx-items table', 'role')) === 'grid' && (await p.getAttribute('#dx-items table', 'aria-rowcount')) === String(VIEW.items.length + 1), 'role=grid, aria-rowcount');
    const x10 = X10;
    const row = await txt(p, rowSel(x10));
    check(row.indexOf(x10.hint.hu) >= 0 && row.indexOf(x10.impact.text.hu) >= 0, 'súgó és hatás a motor szövegével');
    const sym = await p.$eval(rowSel(x10) + ' .dx-st-sym', (e) => e.textContent);
    const sr = await p.$eval(rowSel(x10) + ' .dx-st .sr-only', (e) => e.textContent);
    check(sym === '≠' && sr === 'nyitott', 'állapot: szimbólum ≠ + szöveg „nyitott”');
    const fmt = VIEW.items.find((it) => it.kind === 'format_only');
    check((await p.$eval(rowSel(fmt) + ' .dx-st-sym', (e) => e.textContent)) === '=', 'csak írásmód: =');
    const onlyA = VIEW.items.find((it) => it.kind === 'only_a');
    check((await txt(p, rowSel(onlyA))).indexOf('<img src=x onerror=alert(1)>') >= 0, 'a HTML-címke szó szerint látszik');
    check((await txt(p, '.dx-legend')).indexOf('nyitott') >= 0, 'jelmagyarázat');
    check(/X009/.test(await txt(p, '#dx-gate')) && /Eldöntve 0\/11/.test(await counterText(p)), 'kapu: X009, Eldöntve 0/11');
    check((await p.getAttribute('#dx-write', 'aria-disabled')) === 'true', 'a CSV-írás tiltott, amíg van feloldatlan');
    await finish(o, 'lista');
  });

  await test('billentyűzet: ↑/↓ a listában, Enter → indoklás, „b” gyorsválasztás; Miért? (kezdőknek)', async () => {
    const o = await openDev('#/dual?outcome=o1');
    const p = o.page;
    await dualReady(p);
    await p.focus('#dx-items [role="gridcell"][tabindex="0"], #dx-items [role="rowheader"][tabindex="0"]');
    await p.keyboard.press('ArrowDown');
    await p.keyboard.press('ArrowDown');
    const key = await p.evaluate(() => document.activeElement.closest('tr').dataset.key);
    check(key === VIEW.items[2].key + SEP + VIEW.items[2].field, '↓↓ → a harmadik tétel');
    await p.keyboard.press('Enter');
    await p.waitForFunction(() => document.activeElement && document.activeElement.id === 'dx-reason');
    check((await txt(p, '#dx-detail-h')).indexOf(VIEW.items[2].key) >= 0, 'Enter → a tétel részletei, fókusz az indokláson');
    await p.focus('#dx-items tr[aria-selected="true"] [role="gridcell"][data-col="field"]');
    await p.keyboard.press('b');
    check(await p.$eval('#dx-detail .dx-choice input[value="b"]', (i) => i.checked), '„b” → B kiválasztva');
    check(await p.evaluate(() => document.activeElement.id === 'dx-reason'), 'a gyorsválasztás után az indoklásra ugrik');
    await p.click('#dx-hint .why-btn:not(.is-compact)');
    await p.waitForSelector('.why-pop, .why-panel');
    const why = await txt(p, '.why-panel');
    check(WHY_ASKS.some((q) => why.indexOf(q) >= 0), 'Miért? — egyszerű nyelvű magyarázat a súgó-kód családjához');
    await finish(o, 'billentyűzet');
  });

  await test('döntés: indoklás nélkül nem megy; rögzítés If-Match-csel; saját érték; visszavonás', async () => {
    const o = await openDev('#/dual?outcome=o1');
    const p = o.page;
    await dualReady(p);
    const it = VIEW.items[0];
    await selectItem(p, it);
    await p.check('#dx-detail .dx-choice input[value="a"]');
    await p.click('#dx-decide');
    check(/Indoklás nélkül/.test(await txt(p, '#dx-msg')), 'indoklás nélkül: magyar üzenet');
    check((await calls(p, 'POST', '/api/reconcile')).length === 0, 'kérés sem ment');
    await p.fill('#dx-reason', 'Table 2, 5. oldal — ' + SECRET);
    await p.click('#dx-decide');
    await waitToast(p, /Döntés rögzítve/);
    const c = (await calls(p, 'POST', '/api/reconcile'))[0];
    check(c && c.body.decisions.length === 1 && c.body.decisions[0].key === it.key && c.body.decisions[0].field === it.field &&
      c.body.decisions[0].chosen === 'a' && c.body.decisions[0].reason.indexOf(SECRET) >= 0, 'a döntés a kérésben (kulcs, mező, választás, indoklás)');
    check(c && c.headers['If-Match'] === CMP.etag, 'If-Match: a konszenzus-fájl ETag-je');
    check(/Eldöntve 1\/11/.test(await counterText(p)), 'számláló: 1/11');
    check((await p.$eval(rowSel(it) + ' .dx-st-sym', (e) => e.textContent)) === '✔', 'állapot: ✔');
    check((await txt(p, rowSel(it) + ' [data-col="dec"]')) === 'A értéke', 'döntés-oszlop');
    // saját érték
    const it2 = VIEW.items[5];
    await selectItem(p, it2);
    await p.check('#dx-detail .dx-choice input[value="other"]');
    await p.fill('#dx-reason', 'Mindkettő elírás');
    await p.click('#dx-decide');
    check(/add meg az értéket/.test(await txt(p, '#dx-msg')), 'saját érték üresen: üzenet');
    await p.fill('#dx-other', '28');
    await p.click('#dx-decide');
    await p.waitForFunction(() => /Eldöntve 2\//.test(document.getElementById('dx-counter').textContent));
    check((await txt(p, rowSel(it2) + ' [data-col="dec"]')) === 'saját: 28', 'saját érték a döntés-oszlopban');
    const last = (await calls(p, 'POST', '/api/reconcile')).pop();
    check(last.body.decisions[0].chosen === 'other' && last.body.decisions[0].value === '28', 'other + value a kérésben');
    // visszavonás
    await p.click('#dx-clear');
    await p.waitForFunction(() => /Eldöntve 1\//.test(document.getElementById('dx-counter').textContent));
    const cl = (await calls(p, 'POST', '/api/reconcile')).pop();
    check(cl.body.clear && cl.body.clear[0].key === it2.key, 'visszavonás: clear a kérésben');
    check((await p.$eval(rowSel(it2) + ' .dx-st-sym', (e) => e.textContent)) === '≠', 'újra nyitott');
    await finish(o, 'döntés');
  });

  await test('szűrés és rendezés: csak feloldatlan, csak hatásos, hatás szerint', async () => {
    const o = await openDev('#/dual?outcome=o1');
    const p = o.page;
    await dualReady(p);
    await selectItem(p, VIEW.items[0]);
    await decide(p, 'a', 'ok', false);
    await p.check('#dx-f-open');
    check((await count(p, '#dx-items tbody tr[data-key]')) === VIEW.items.filter((i) => !i.auto).length - 1, 'csak feloldatlan: az eldöntött és az automatikus kimarad');
    await p.uncheck('#dx-f-open');
    await p.check('#dx-f-impact');
    const byEngine = VIEW.items.some((i) => i.impact && typeof i.impact.material === 'boolean');
    const nImp = VIEW.items.filter((i) => i.impact && (byEngine ? i.impact.material === true : true)).length;
    check((await count(p, '#dx-items tbody tr[data-key]')) === nImp, 'csak hatásos (a motor material-jelzője szerint): ' + nImp);
    await p.uncheck('#dx-f-impact');
    await p.selectOption('#dx-sort', 'impact');
    const firstKey = await p.$eval('#dx-items tbody tr[data-key]', (r) => r.dataset.key);
    const ranked = VIEW.items.filter((i) => i.impact && typeof i.impact.rank === 'number').sort((a, b) => b.impact.rank - a.impact.rank)[0];
    check(firstKey === ranked.key + SEP + ranked.field, 'hatás szerint: a motor rangja szerint elöl');
    await finish(o, 'szűrés');
  });

  await test('X009-kapu: CSV-írás csak minden döntés után; konszenzus-CSV; átvétel a kimenet táblájába előnézettel', async () => {
    const o = await openDev('#/dual?outcome=o1');
    const p = o.page;
    await dualReady(p);
    // aria-disabled: fókuszálható marad, és magyarázatot ad (a Playwright kattintása ezt „nem engedett”-nek veszi)
    await p.focus('#dx-write');
    await p.keyboard.press('Enter');
    await waitToast(p, /még nem írható ki/);
    check((await calls(p, 'POST', '/api/reconcile')).filter((c) => c.body.write).length === 0, 'blokkolt állapotban nincs írás-kérés');
    await decideAll(p, 'Forrás: Table 2');
    check(/Eldöntve 11\/11/.test(await counterText(p)), 'minden eltérés eldöntve: ' + await counterText(p));
    check((await p.getAttribute('#dx-gate', 'class')).indexOf('is-ok') >= 0, 'a kapu zöld');
    check((await p.getAttribute('#dx-write', 'aria-disabled')) === 'false', 'a CSV-írás engedett');
    await p.click('#dx-write');
    await waitToast(p, /konszenzus-CSV kiírva/);
    const w = (await calls(p, 'POST', '/api/reconcile')).filter((c) => c.body.write === 'kettos');
    check(w.length === 1, 'write: kettos');
    await p.waitForSelector('#dx-export-consensus');
    check((await txt(p, '#dx-write-info')).indexOf('o1.consensus.csv') >= 0, 'utoljára kiírva: út');
    // átvétel a kimenet adattáblájába
    await p.click('#dx-write-outcome');
    await p.waitForSelector('.modal-backdrop');
    check(/03_adatok\/o1\.csv/.test(await txt(p, '.modal-backdrop')), 'megerősítés a cél útjával és az előnézet számaival');
    const dry = (await calls(p, 'POST', '/api/reconcile')).filter((c) => c.body.dry_run);
    check(dry.length === 1 && dry[0].body.write === 'outcome', 'előnézet (dry_run) a megerősítés előtt');
    await p.click('.modal-backdrop .btn-danger');
    await waitToast(p, /adattáblája frissítve/);
    const tg = (await calls(p, 'GET', '/api/table')).pop();
    const wo = (await calls(p, 'POST', '/api/reconcile')).filter((c) => c.body.write === 'outcome' && !c.body.dry_run);
    check(tg && tg.query.dataset === '03_adatok/o1.csv' && wo.length === 1 && !!wo[0].body.outcome_if_match, 'outcome_if_match: a tábla mostani ETag-je');
    await finish(o, 'kapu');
  });

  await test('409: más módosította a konszenzus-fájlt → figyelmeztetés és újratöltés', async () => {
    const o = await openDev('#/dual?outcome=o1');
    const p = o.page;
    await dualReady(p);
    await p.evaluate(() => window.MA.dev.dual.bumpEtag('o1'));
    const before = (await calls(p, 'POST', '/api/compare')).length;
    await selectItem(p, VIEW.items[1]);
    await p.check('#dx-detail .dx-choice input[value="b"]');
    await p.fill('#dx-reason', 'x');
    await p.click('#dx-decide');
    await waitToast(p, /újratöltöttem/);
    await p.waitForFunction((n) => window.MA.dev.fixtures.calls.filter((c) => c.path === '/api/compare').length > n, before);
    check(true, 'újra-összevetés a 409 után');
    await finish(o, '409');
  });

  await test('táblák egymás mellett: cellaszintű diff jellel (nem csak szín), Enter → a cella döntése', async () => {
    const o = await openDev('#/dual?outcome=o1');
    const p = o.page;
    await dualReady(p);
    await p.click('#dx-tab-tables');
    await p.waitForSelector('#dx-tables table[role="grid"]');
    check((await count(p, '#dx-tables tbody tr[data-key]')) === VIEW.a.rows.length + 1, 'sorok: A sorai + a csak B-ben lévő');
    const x10 = X10;
    const pair = VIEW.pairs.find((pp) => pp.key === x10.key);
    const cellSel = '#dx-tables tr[data-key="p:' + pair.row_uid_a + '"] [data-col="e1:A"]';
    check((await p.getAttribute(cellSel, 'class')).indexOf('is-diff') >= 0, 'eltérő cella: is-diff');
    check((await txt(p, cellSel + ' .dx-cell-sym')) === '≠' && (await txt(p, cellSel + ' .sr-only')).indexOf('nyitott') >= 0, 'szimbólum + képernyőolvasó-szöveg');
    const fmt = VIEW.items.find((it) => it.kind === 'format_only');
    const fp = VIEW.pairs.find((pp) => pp.key === fmt.key);
    check((await p.getAttribute('#dx-tables tr[data-key="p:' + fp.row_uid_a + '"] [data-col="n1:B"]', 'class')).indexOf('is-format') >= 0, 'csak írásmód: is-format');
    const onlyTxt = await p.$$eval('#dx-tables tr.is-only', (rs) => rs.map((r) => r.textContent).join(' | '));
    check(onlyTxt.indexOf('⊖ Tóth 2021') >= 0 && onlyTxt.indexOf('⊕ <img') >= 0, 'csak B-ben / csak A-ban lévő sor ⊖ / ⊕');
    await p.click(cellSel);
    await p.keyboard.press('Enter');
    await p.waitForFunction(() => !document.getElementById('dx-items-panel').hidden);
    check((await txt(p, '#dx-detail-h')).indexOf(x10.key) >= 0 && (await p.getAttribute('#dx-tab-items', 'aria-selected')) === 'true', 'Enter → az eltérés-lista, a cella döntésével');
    await finish(o, 'táblák');
  });

  await test('motor nélkül (424): magyar üzenet a szerverből, a fájlcsere megmarad', async () => {
    const o = await openDev('#/overview');
    const p = o.page;
    await p.evaluate(() => window.MA.dev.dual.engineMissing(true));
    await goDual(p, 'o1');
    await p.waitForSelector('#dx-engine-missing');
    const m = await txt(p, '#dx-engine-missing');
    check(m.indexOf('metaelemzes.api.compare') >= 0 && m.indexOf('Frissítsd a motort') >= 0, '424 a szerver üzenetével: ' + m.slice(0, 120));
    check(!!(await p.$('#dx-import-b')) && !!(await p.$('#dx-export-a')), 'az import/export gombok megmaradnak');
    check(!!(await p.$('#dx-engine-note')), 'a fejlécben is jelzi');
    await finish(o, '424');
  });

  await test('fájlcsere: beérkezett mappa → B import → összevetés; fájlválasztó → bájthű base64; felülírás megerősítéssel', async () => {
    const o = await openDev('#/dual?outcome=o3');
    const p = o.page;
    await dualReady(p);
    await p.waitForSelector('#dx-need-files');
    check((await txt(p, '#dx-file-b')).indexOf('hiányzik') >= 0, 'o3: a B hiányzik');
    await p.waitForSelector('#dx-inbox li');
    check((await txt(p, '#dx-inbox')).indexOf('o3.B.csv') >= 0, 'a beérkezett fájl a listában');
    await p.fill('#dx-rater-b', 'KP');
    await p.click('#dx-inbox li button.btn-primary');
    await waitToast(p, /B tábla importálva/);
    await dualReady(p);
    await p.waitForSelector('#dx-items table');
    const imp = (await calls(p, 'POST', '/api/kettos/import'))[0];
    check(imp.body.side === 'B' && imp.body.path === '03_adatok/kettos/beerkezett/o3.B.csv' && imp.body.rater === 'KP', 'import a beérkezett mappából (oldal, út, monogram)');
    // fájlválasztó: bájthű base64 (BOM, ékezet, CRLF), felülírás megerősítéssel
    await goDual(p, 'o1');
    await selectItem(p, VIEW.items[0]);
    await decide(p, 'a', 'Table 2', false);
    const bytes = Buffer.from('﻿vizsgálat;esemény1\r\nAronson 1948;4\r\n', 'utf-8');
    const [chooser] = await Promise.all([p.waitForEvent('filechooser'), p.click('#dx-import-a')]);
    await chooser.setFiles({ name: 'o1.A.csv', mimeType: 'text/csv', buffer: bytes });
    await p.waitForSelector('.modal-backdrop');
    check(/Felülírod/.test(await txt(p, '.modal-backdrop')), 'meglévő A: megerősítés kérés');
    await p.click('.modal-backdrop .btn-danger');
    await waitToast(p, /A tábla felülírva/);
    const imps = (await calls(p, 'POST', '/api/kettos/import')).filter((c) => c.body.side === 'A');
    check(imps.length === 2 && imps[0].body.content_b64 === bytes.toString('base64') && !imps[0].body.replace && imps[1].body.replace === true,
      'bájthű base64; második kérés replace: true');
    check(imps[0].body.filename === 'o1.A.csv', 'fájlnév a kérésben');
    await dualReady(p);
    await p.waitForSelector('#dx-items table');
    check(/elavult döntés: 1/.test(await txt(p, '#dx-gate')), 'a felülírás után a korábbi döntés elavult (⟳)');
    check((await p.$eval(rowSel(VIEW.items[0]) + ' .dx-st-sym', (e) => e.textContent)) === '⟳', 'állapot: ⟳');
    await finish(o, 'fájlcsere');
  });

  await test('export és sablon: letöltés a szerver bájtjaival; a sablon fájlneve a másik kinyerőé', async () => {
    const o = await openDev('#/dual?outcome=o1');
    const p = o.page;
    await dualReady(p);
    const [dl] = await Promise.all([p.waitForEvent('download'), p.click('#dx-export-a')]);
    check(dl.suggestedFilename() === 'o1.A.csv', 'fájlnév: o1.A.csv');
    const got = fs.readFileSync(await dl.path());
    const want = Buffer.from(fxRoute('POST', '/api/kettos/export', { outcome: 'o1', side: 'A', template: false }).envelope.data.content_b64, 'base64');
    check(got.equals(want), 'a letöltött bájtok = a szerver bájtjai');
    const [dl2] = await Promise.all([p.waitForEvent('download'), p.click('#dx-template-a')]);
    check(dl2.suggestedFilename() === 'o1.B.sablon.csv', 'sablon: o1.B.sablon.csv');
    const tpl = fs.readFileSync(await dl2.path(), 'utf-8');
    check(tpl.indexOf('row_uid') < 0 && /\nAronson 1948;;;;;;\n/.test(tpl), 'a sablonban csak a kulcs van kitöltve, row_uid nélkül');
    await finish(o, 'export');
  });

  await test('virtualizáció: 1500 eltérés és 1500 soros táblák — ablakos DOM, aria-rowcount, billentyűzet a végéig', async () => {
    const o = await openDev('#/overview');
    const p = o.page;
    await p.evaluate(([view, list]) => {
      const N = 1500;
      const v = JSON.parse(JSON.stringify(view));
      const it0 = v.items[0];
      v.items = [];
      v.pairs = [];
      v.a = { header: v.a.header, rows: [], n_rows: N, etag: 'x', format: v.a.format };
      v.b = { header: v.b.header, rows: [], n_rows: N, etag: 'y', format: v.b.format };
      for (let i = 0; i < N; i++) {
        const key = 'Vizsgálat ' + String(10000 + i);
        const ua = 'rva' + String(10000 + i), ub = 'rvb' + String(10000 + i);
        v.items.push(Object.assign({}, it0, { id: 'c' + i, key: key, a: String(i), b: String(i + 1), row_uid_a: ua, row_uid_b: ub, decision: null }));
        v.pairs.push({ key: key, row_uid_a: ua, row_uid_b: ub });
        v.a.rows.push({ row_uid: ua, cells: [key, String(i), '10', '1', '10', '2000', 'low'] });
        v.b.rows.push({ row_uid: ub, cells: [key, String(i + 1), '10', '1', '10', '2000', 'low'] });
      }
      v.progress = { total: N, decided: 0, unresolved: N, auto: 0, stale: 0, orphans: 0 };
      const l = JSON.parse(JSON.stringify(list));
      const big = JSON.parse(JSON.stringify(l.outcomes[0]));
      big.id = 'nagy';
      l.outcomes.push(big);
      const env = (schema, data) => ({ envelope: { ok: true, schema: schema, data: data, warnings: [], meta: { engine: '0.2.0', elapsed_ms: 1, project_rev: 1, request_id: 'q_big' } } });
      window.MA.dev.fixtures.route('GET', '/api/kettos', () => env('szk.ma.dual-list/v1', l), { first: true });
      window.MA.dev.fixtures.route('POST', '/api/compare', (req) => (req.body.outcome === 'nagy' ? env('szk.ma.dual-view/v1', v) : null), { first: true });
    }, [VIEW, fxRoute('GET', '/api/kettos').envelope.data]);
    await goDual(p, 'nagy');
    await p.waitForSelector('#dx-items.vt.is-virtual');
    check((await p.getAttribute('#dx-items table', 'aria-rowcount')) === '1501', 'eltérés-lista: aria-rowcount 1501');
    const n = await count(p, '#dx-items tbody tr[data-key]');
    check(n > 10 && n < 200, 'eltérés-lista: csak az ablak sorai a DOM-ban (' + n + ')');
    await p.focus('#dx-items [tabindex="0"]');
    await p.keyboard.press('Control+End');
    const last = await p.evaluate(() => { const r = document.activeElement.closest('tr'); return { idx: r.getAttribute('aria-rowindex'), key: r.dataset.key }; });
    check(last.idx === '1501' && last.key.indexOf('Vizsgálat 11499') === 0, 'Ctrl+End → az utolsó tétel (aria-rowindex 1501)');
    await p.keyboard.press('Enter');
    await p.waitForFunction(() => /11499/.test(document.getElementById('dx-detail-h').textContent));
    check(true, 'Enter → az utolsó tétel részletei');
    await p.click('#dx-tab-tables');
    await p.waitForSelector('#dx-tables.vt.is-virtual');
    check((await p.getAttribute('#dx-tables table', 'aria-rowcount')) === '1501', 'táblák: aria-rowcount 1501');
    const n2 = await count(p, '#dx-tables tbody tr[data-key]');
    check(n2 < 200, 'táblák: ablakos DOM (' + n2 + ')');
    await p.evaluate(() => { const v = document.querySelector('#dx-tables'); v.scrollIntoView(); v.scrollTop = 30000; });
    // a látható terület közepén valódi (kirajzolt) adatsor van, nem térkitöltő — és a teljes tábla közepéből
    await p.waitForFunction(() => {
      const v = document.querySelector('#dx-tables');
      const r = v.getBoundingClientRect();
      const el = document.elementFromPoint(r.left + 40, r.top + r.height / 2);
      const tr = el && el.closest ? el.closest('tr[data-key]') : null;
      return !!tr && Number(tr.getAttribute('aria-rowindex')) > 500;
    }, null, { timeout: 10000 });
    check(true, 'görgetésre a látható terület sorai kirajzolódnak (a tábla közepéből)');
    await finish(o, 'virtualizáció (lista)');
  });

  await test('kinyerés-rács virtualizációja: 1500 sor — ablakos DOM, aria, Ctrl+End, szerkesztés, mentés-kérés a teljes táblával', async () => {
    const o = await openDev('#/overview');
    const p = o.page;
    await p.evaluate(() => {
      const st = window.MA.dev.extraction.state();
      const t = st.tables['03_adatok/o1.csv'].data;
      const base = t.rows.slice();
      const rows = [];
      for (let i = 0; i < 1500; i++) {
        const r = base[i % base.length];
        rows.push({ row_uid: 'rg' + String(100000 + i), cells: r.cells.map((c, j) => (j === 0 ? c + ' #' + i : c)) });
      }
      t.rows = rows;
    });
    await p.evaluate(() => { window.location.hash = '#/extraction?outcome=o1'; });
    await p.waitForSelector('#screen-root[data-screen="extraction"] .mg.is-virtual', { timeout: 15000 });
    check((await p.getAttribute('.mg-table', 'aria-rowcount')) === '1501', 'aria-rowcount 1501');
    const n = await count(p, '.mg-table tbody tr[data-uid]');
    check(n > 10 && n < 200, 'csak az ablak sorai a DOM-ban (' + n + ')');
    await p.focus('.mg-table td[tabindex="0"]');
    await p.keyboard.press('Control+End');
    const pos = await p.evaluate(() => { const td = document.activeElement; const tr = td.closest('tr'); return { idx: tr.getAttribute('aria-rowindex'), uid: tr.dataset.uid }; });
    check(pos.idx === '1501' && pos.uid === 'rg101499', 'Ctrl+End → az utolsó sor kirajzolva, fókuszban');
    await p.keyboard.press('Control+Home');
    await p.keyboard.press('ArrowRight');
    await p.keyboard.press('PageDown');
    await p.keyboard.type('77');
    await p.keyboard.press('Enter');
    const v = await p.evaluate(() => { const tr = document.querySelector('.mg-table tr[data-uid="rg100010"]'); return tr ? tr.children[2].querySelector('.mg-t').textContent : null; });
    check(v === '77', 'szerkesztés virtuális módban (PageDown → 11. sor)');
    await p.evaluate(() => { document.querySelector('.mg').scrollTop = 20000; });
    await p.waitForFunction(() => !document.querySelector('.mg-table tr[data-uid="rg100000"]'));
    check(true, 'görgetésre a kigörgetett sorok kikerülnek a DOM-ból');
    await p.keyboard.press('ArrowDown');
    await p.waitForFunction(() => !!document.querySelector('.mg-table tr[data-uid="rg100012"]'));
    check(await p.evaluate(() => document.activeElement.closest('tr').dataset.uid === 'rg100012'), 'a kigörgetett aktív cellához a billentyű visszagörget (Enter után a 12. sor → ↓ a 13.)');
    await p.keyboard.press('Control+s');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.method === 'PUT' && c.path === '/api/table'), null, { timeout: 10000 });
    const put = (await calls(p, 'PUT', '/api/table')).pop();
    check(put.body.rows.length === 1500, 'a mentés a teljes (1500 soros) táblát küldi');
    await finish(o, 'virtualizáció (rács)');
  });

  await test('eredmények: kumulatív forest és buborékábra a motor geometriájával (sáv-poligon, egyenes, súly-terület)', async () => {
    const o = await openDev('#/overview');
    const p = o.page;
    await p.evaluate(([cont, cat]) => {
      window.__plotMode = 'cont';
      window.MA.dev.fixtures.route('GET', '/api/runs/*/plot', () => ({ envelope: { ok: true, schema: 'szk.ma.plot/v2', data: window.__plotMode === 'cat' ? cat : cont, warnings: [], meta: { engine: '0.2.0', elapsed_ms: 1, project_rev: 1, request_id: 'q_pl' } } }), { first: true });
    }, [PLOT_BUBBLE, PLOT_CAT]);
    await p.evaluate(() => { window.location.hash = '#/results?outcome=o1&view=bubble'; });
    await p.waitForSelector('#results-plot-panel .bu-svg', { timeout: 15000 });
    check(!!(await p.$('[role="tab"][data-tab="bubble"]')) && !!(await p.$('[role="tab"][data-tab="cumulative"]')), 'fülek: Kumulatív, Buborék');
    const b = PLOT_BUBBLE.bubble;
    check((await count(p, '#results-plot-panel .bu-point')) === b.points.length, 'buborék vizsgálatonként');
    const geo = await p.evaluate((bb) => {
      const L = window.MA.plots.bubble.layout({ bubble: bb });
      const out = [];
      bb.points.forEach((pt) => {
        const c = document.querySelector('#results-plot-panel .bu-point[data-uid="' + pt.row_uid + '"] .bu-bubble');
        out.push({ dx: Math.abs(Number(c.getAttribute('cx')) - L.sx(pt.x)), dy: Math.abs(Number(c.getAttribute('cy')) - L.sy(pt.y)), r: Number(c.getAttribute('r')), w: pt.weight_pct });
      });
      const poly = document.querySelector('#results-plot-panel polygon.bu-band').getAttribute('points').trim().split(/\s+/);
      const first = poly[0].split(',').map(Number);
      return { out, n: poly.length, firstOk: Math.abs(first[0] - L.sx(bb.band[0][0])) < 0.06 && Math.abs(first[1] - L.sy(bb.band[0][2])) < 0.06 };
    }, b);
    check(geo.out.every((g) => g.dx < 0.06 && g.dy < 0.06), 'a buborékok középpontja a motor x/y-ján');
    const ws = geo.out.filter((g) => g.w > 0).sort((x, y) => y.w - x.w);
    const ratio = (ws[0].r * ws[0].r) / (ws[ws.length - 1].r * ws[ws.length - 1].r);
    check(ws[ws.length - 1].r <= 3.01 || Math.abs(ratio - ws[0].w / ws[ws.length - 1].w) / (ws[0].w / ws[ws.length - 1].w) < 0.05, 'a buborék területe a motor súlyával arányos');
    check(geo.n === 2 * b.band.length && geo.firstOk, 'a sáv-poligon a motor rácspontjaiból (' + geo.n + ' pont)');
    check(!!(await p.$('#results-plot-panel path.bu-line')), 'illesztett egyenes');
    check((await txt(p, '#bubble-coef')) === b.coef_text.hu, 'a koefficiens-szöveg a motoré');
    if (b.pi_band && b.pi_band.length) {
      check((await count(p, '#results-plot-panel path.bu-pi')) === 2, 'predikciós sáv: két szaggatott él a motor rácsán');
    }
    if (b.note) { check((await txt(p, '#bubble-note')) === b.note.hu, 'kezdőknek szóló magyarázat: a motor note-ja'); }
    if (b.line_label) { check((await txt(p, '#results-plot-panel .bu-wrap figcaption')).indexOf(b.line_label.hu) >= 0, 'jelmagyarázat: a motor címkéje'); }
    await p.focus('#results-plot-panel .bu-point[tabindex="0"]');
    await p.keyboard.press('ArrowRight');
    await p.keyboard.press('Enter');
    await p.waitForFunction(() => (document.getElementById('results-drill') || {}).textContent.length > 10);
    check(true, 'billentyűzet: ←/→ + Enter → lefúrás');
    // kumulatív
    await p.click('[role="tab"][data-tab="cumulative"]');
    await p.waitForSelector('#results-plot-panel .cu-svg');
    const ent = PLOT_BUBBLE.cumulative.entries;
    check((await count(p, '#results-plot-panel .cu-row')) === ent.length, 'kumulatív: sor lépésenként (' + ent.length + ')');
    const rows = await p.$$eval('#results-plot-panel .cu-row', (gs) => gs.map((g) => ({ y: g.getAttribute('data-y'), t: g.querySelector('.fp-effect').textContent, k: g.getAttribute('data-k') })));
    check(rows.every((r, i) => r.y === String(ent[i].estimate) && r.t === ent[i].display_text.hu && r.k === String(ent[i].k)), 'data-y = a motor becslése, szöveg = a motor display_text-je, k a motoré');
    check((await count(p, '#results-plot-panel .cu-row.is-final polygon.fp-diamond')) === 1, 'a végső (összes vizsgálat) sor gyémánt');
    check((await p.getAttribute('#results-plot-panel .cu-final-line', 'data-est')) === String(ent[ent.length - 1].estimate), 'referencia: a végső becslés');
    const cum = PLOT_BUBBLE.cumulative;
    if (cum.order && cum.order.text) { check((await txt(p, '#cumulative-order')) === cum.order.text.hu, 'a rendezés leírása a motoré'); }
    if (cum.note) { check((await txt(p, '#cumulative-note')) === cum.note.hu, 'a kumulatív magyarázat a motoré'); }
    // kategóriás moderátor (a futás-nézet gyorsítótáraz: közvetlenül a rajzolóval, ugyanabban a lapban)
    const cat = await p.evaluate((pl) => {
      const host = document.createElement('div');
      document.body.appendChild(host);
      const r = window.MA.plots.bubble.render(host, pl, {});
      const out = { groups: host.querySelectorAll('.bu-group').length, band: host.querySelectorAll('polygon.bu-band').length,
        points: host.querySelectorAll('.bu-point').length, ticks: Array.from(host.querySelectorAll('.pl-tick-label')).map((t) => t.textContent),
        gy: Array.from(host.querySelectorAll('.bu-group')).map((g) => g.getAttribute('data-y')) };
      host.remove();
      return Object.assign(out, { ok: !!r });
    }, PLOT_CAT);
    const cb = PLOT_CAT.bubble;
    check(cat.ok && cat.groups === cb.groups.length && cat.band === 0, 'kategóriás: csoport-összesítők a motor alcsoportjaiból, sáv nélkül');
    check(cat.points === cb.points.length && cb.x_axis.ticks.every((tk) => cat.ticks.indexOf(tk.text) >= 0), 'kategóriás: pontok és a motor csoport-tickjei');
    check(cat.gy.every((y, i) => y === String(cb.groups[i].estimate)), 'kategóriás: a csoport-gyémánt a motor becslésén');
    await finish(o, 'ábrák');
  });

  await test('nyelvváltás (EN): felület angolul, a motor szövegei angol változatban', async () => {
    const o = await openDev('#/dual?outcome=o1');
    const p = o.page;
    await dualReady(p);
    await p.click('#lang-switch [data-lang="en"]');
    await p.waitForFunction(() => document.documentElement.lang === 'en');
    await dualReady(p);
    await p.waitForSelector('#dx-items table');
    check((await txt(p, '#dx-head-h')) === 'Double extraction', 'cím angolul');
    const x10 = X10;
    check((await txt(p, rowSel(x10))).indexOf(x10.hint.en) >= 0, 'a motor súgója angolul');
    await finish(o, 'nyelv');
  });
}

// ================================================================ B. rész — valódi szerver, termék-build
async function realPage(srv) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) { errors.push('console: ' + m.text()); } });
  await page.goto(srv.url);
  await page.waitForSelector('html[data-ready="1"]', { timeout: 30000 });
  return { ctx, page, errors };
}

async function partB() {
  await test('valódi szerver + motor (vagy teszt-csonk): B import a felületről → minden döntés → konszenzus-CSV a lemezen', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ma_dual_ui1_'));
    const srv = await startReal(tmp, ['--stub']);
    try {
      const o = await realPage(srv);
      const p = o.page;
      await goDual(p, 'o1');
      await p.waitForSelector('#dx-need-files');
      const [chooser] = await Promise.all([p.waitForEvent('filechooser'), p.click('#dx-import-b')]);
      await chooser.setFiles({ name: 'o1.B.csv', mimeType: 'text/csv', buffer: Buffer.from(srv.b_csv, 'utf-8') });
      await dualReady(p);
      await p.waitForSelector('#dx-items table', { timeout: 15000 });
      check(fs.readFileSync(path.join(srv.proj, '03_adatok', 'kettos', 'o1.B.csv'), 'utf-8') === srv.b_csv, 'a B tábla bájtra azonosan a kettos/ mappában');
      const m = /(\d+)\/(\d+)/.exec(await counterText(p));
      check(m && Number(m[2]) > 5, 'eltérések a motorból (' + (m ? m[2] : '?') + ')');
      await decideAll(p, SECRET);
      check(/Eldöntve (\d+)\/\1/.test(await counterText(p)), 'minden eltérés eldöntve: ' + await counterText(p));
      await p.click('#dx-write');
      await p.waitForSelector('#dx-export-consensus', { timeout: 15000 });
      const csv = fs.readFileSync(path.join(srv.proj, '03_adatok', 'kettos', 'o1.consensus.csv'), 'utf-8');
      check(csv.split('\n')[0].indexOf('vizsgálat;esemény1') === 0 && csv.indexOf('Tóth 2021') < 0, 'konszenzus-CSV az A formátumában (csak-B sor: A változata → kimarad)');
      const doc = JSON.parse(fs.readFileSync(path.join(srv.proj, '03_adatok', 'kettos', 'o1.consensus.json'), 'utf-8'));
      check(doc.schema === 'szk.ma.consensus/v1' && doc.decisions.length === Number(m[2]) && doc.decisions.every((d) => d.reason.indexOf(SECRET) >= 0), 'konszenzus-fájl: minden döntés indoklással');
      const prov = JSON.parse(fs.readFileSync(path.join(srv.proj, '03_adatok', 'kettos', 'o1.consensus.prov.json'), 'utf-8'));
      check(prov.cells.length > 0 && prov.cells.every((c) => c.method === 'reconciled'), 'eredet: reconciled');
      const act = fs.readFileSync(path.join(srv.proj, '07_ellenorzes', 'activity.jsonl'), 'utf-8');
      check(act.indexOf('kettos.reconcile') >= 0 && act.indexOf('kettos.import') >= 0, 'activity-sorok');
      check(act.indexOf(SECRET) < 0 && act.indexOf('Ferguson') < 0 && act.indexOf('13598') < 0, 'az activity-naplóban nincs indoklás, kulcs vagy cellaérték');
      check(o.errors.length === 0, 'nincs konzolhiba: ' + o.errors.join(' | '));
      await o.ctx.close();
    } finally {
      await srv.stop();
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  });

  await test('valódi szerver, motor-függvény nélkül: 424 magyarul, az import működik', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ma_dual_ui0_'));
    const srv = await startReal(tmp, ['--no-engine']);
    try {
      const o = await realPage(srv);
      const p = o.page;
      await goDual(p, 'o1');
      const [chooser] = await Promise.all([p.waitForEvent('filechooser'), p.click('#dx-import-b')]);
      await chooser.setFiles({ name: 'o1.B.csv', mimeType: 'text/csv', buffer: Buffer.from(srv.b_csv, 'utf-8') });
      await p.waitForSelector('#dx-engine-missing', { timeout: 15000 });
      check((await txt(p, '#dx-engine-missing')).indexOf('metaelemzes.api.compare') >= 0, '424 a szerver magyar üzenetével');
      check(fs.existsSync(path.join(srv.proj, '03_adatok', 'kettos', 'o1.B.csv')), 'a B import a motor nélkül is megtörtént');
      check(o.errors.length === 0, 'nincs konzolhiba: ' + o.errors.join(' | '));
      await o.ctx.close();
    } finally {
      await srv.stop();
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  });
}

(async () => {
  if (!process.env.MA_UI_DEV_HTML) {
    for (const args of [['--dev'], []]) {
      const r = spawnSync('python3', [path.join(WEB, 'build_gui.py')].concat(args), { cwd: ROOT, encoding: 'utf-8' });
      if (r.status !== 0) { console.error('build hiba: ' + r.stderr + r.stdout); process.exit(1); }
    }
  }
  stat = await startStatic();
  try { browser = await chromium.launch({ headless: true }); } catch (e) { browser = await chromium.launch({ headless: true, executablePath: CHROME_FALLBACK }); }
  try {
    await partA();
    await partB();
  } finally {
    await browser.close();
    await stat.close();
  }
  const fails = results.filter((r) => !r.pass);
  console.log('\n' + (results.length - fails.length) + '/' + results.length + ' ellenőrzés zöld');
  if (fails.length) {
    fails.forEach((f) => console.log('  ✖ [' + f.test + '] ' + f.msg));
    process.exit(1);
  }
})();
