#!/usr/bin/env node
/* tests/gui/ui/adapters.spec.js — plugin-adapterek a felületen (terv 3.5.9, 3.5.14, 3.5.16, 4.11–4.13, 5.0, 6.5, 6.7, 8.6).
 *
 * Futtatás:  node tests/gui/ui/adapters.spec.js        (kilépési kód 1, ha bármi elbukik)
 *   MA_UI_DEV_HTML=<út>  — kész dev-build használata (a build kimarad).
 * A dev-buildet ?fixtures=1-gyel nyitja (src/dev/adapters_backend.js: a valódi szerver rögzített válaszai három
 * „világban”: legacy / h5 / ok), szigorú CSP-fejléc mellett. Lefedi:
 *   - Képességek: funkciónkénti állapot (szimbólum + szöveg), mód, tartalék, magyar teendő, H-őrök (H1–H7), átalakítók;
 *   - Ábra-export: futás-választás, ábrafajták (nem elérhető okkal), megjelenítők (figure-forge H6-teendővel), formátumok
 *     (SVG mindig; PNG/PDF/TIFF/PPTX okkal letiltva), fájlnév-ellenőrzés, export → QC (szerver-számhűség, figure-forge és
 *     stdlib audit, kihagyott formátumok okkal), 409 → felülírás megerősítéssel, újra-ellenőrzés, H5-világ (a figure-forge
 *     audit nem fut — a teendő látszik), F2-világ (figure-forge export, egy hiányzó szám → nem zöld);
 *   - Composer-forrás: beállítatlan → hely mentése → előnézet → frissítés (kézi számok megerősítéssel felülírva) →
 *     composer-mód a figyelmeztetésekkel; visszaváltás kézire a PRISMA-képernyőre vezet; al-navigáció a PRISMA fülön;
 *   - validator-keresztellenőrzés komponens: H2 (16/34 vs 32/34 „nem megbízható”), H3 (feloldatlan, nem megbízható),
 *     implikált ítélet „NEM hivatalos” címkével, 424 → érthető üzenet;
 *   - HU/EN, sötét téma, billentyűzet, DOM-biztonság, böngészőtároló-szabály, konzolhiba-mentesség.
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
const DEV = process.env.MA_UI_DEV_HTML || path.join(WEB, 'dist', 'index.dev.html');
const CHROME_FALLBACK = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const CSP = (n) => "default-src 'none'; script-src 'nonce-" + n + "'; style-src 'nonce-" + n + "'; " +
  "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
const FIG = JSON.parse(fs.readFileSync(path.join(WEB, 'fixtures', 'adapters_figures.json'), 'utf-8'));
const RUN_ID = FIG.routes.filter((r) => r.query && r.query.run)[0].query.run;
const DOCS = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests', 'gui', 'adapters_golden', 'docs.json'), 'utf-8'));

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
  try { await fn(); } catch (e) { check(false, 'kivétel: ' + (e && e.stack ? e.stack.split('\n').slice(0, 5).join(' | ') : e)); }
}

function startServer() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (req.method === 'GET' && url.pathname === '/') {
      const nonce = crypto.randomBytes(18).toString('base64url');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': CSP(nonce), 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' });
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

let browser, srv;
async function openPage(hash, opts) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [];
  const dialogs = [];
  page.on('console', (m) => { if (m.type() === 'error') { errors.push('console: ' + m.text()); } });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => { dialogs.push(d.type() + ':' + d.message()); d.dismiss().catch(() => null); });
  await page.goto(srv.base + '/?fixtures=1' + (hash || ''));
  await page.waitForSelector('html[data-ready="1"]', { timeout: 10000 });
  if (opts && opts.scenario) {
    await page.evaluate((s) => window.MA.dev.adapters.scenario(s), opts.scenario);
    await page.evaluate(() => window.MA.app.refresh());
  }
  return { ctx, page, errors, dialogs };
}
async function screen(page, id) {
  await page.waitForFunction((x) => { const r = document.getElementById('screen-root'); return r && r.dataset.screen === x && !r.querySelector('.spinner'); }, id, { timeout: 8000 });
}
async function txt(page, sel) { const el = await page.$(sel); return el ? ((await el.textContent()) || '') : ''; }
async function calls(page, method, p) {
  return page.evaluate(([m, pp]) => window.MA.dev.fixtures.calls.filter((c) => c.method === m && c.path === pp), [method, p]);
}
async function go(page, id, params) {
  await page.evaluate(([x, prm]) => window.MA.app.navigate(x, prm || null), [id, params || null]);
  await screen(page, id);
}
async function finish(o, label) {
  check(o.errors.length === 0, label + ': nincs konzolhiba / CSP-sértés: ' + o.errors.join(' || '));
  check(o.dialogs.length === 0, label + ': nem nyílt natív alert/confirm: ' + o.dialogs.join(' || '));
  const rep = await o.page.evaluate(() => {
    const ls = [], ss = [];
    for (let i = 0; i < localStorage.length; i++) { ls.push(localStorage.key(i)); }
    for (let i = 0; i < sessionStorage.length; i++) { ss.push(sessionStorage.key(i)); }
    return { ls, ss, lsValues: ls.map((k) => localStorage.getItem(k)), cookie: document.cookie };
  });
  check(rep.ls.every((k) => k.startsWith('mag.pref.')), label + ': localStorage csak mag.pref.* (' + rep.ls.join(',') + ')');
  check(rep.lsValues.every((v) => v.length <= 256 && !/Aronson|0\.49|glp1|PubMed_Downloads/.test(v)), label + ': a preferenciákban nincs projektadat');
  check(rep.ss.every((k) => k === 'mag.token'), label + ': sessionStorage csak mag.token');
  check(rep.cookie === '', label + ': nincs süti');
  const missing = await o.page.evaluate(() => window.MA.i18n.missing());
  check(missing.length === 0, label + ': nincs hiányzó i18n-kulcs (' + missing.join(', ') + ')');
  const dom = await o.page.evaluate(() => ({ img: document.querySelectorAll('img').length, onerr: document.querySelectorAll('[onerror]').length,
    scripts: Array.from(document.scripts).filter((s) => !s.nonce).length }));
  check(dom.img === 0 && dom.onerr === 0 && dom.scripts === 0, label + ': DOM-biztonság (' + JSON.stringify(dom) + ')');
  await o.ctx.close();
}
async function modalOk(page, label) {
  await page.waitForSelector('.modal');
  const buttons = await page.$$('.modal-actions button');
  let clicked = false;
  for (const b of buttons) {
    if (((await b.textContent()) || '').indexOf(label) >= 0) { await b.click(); clicked = true; break; }
  }
  check(clicked, 'modális gomb: ' + label);
  await page.waitForFunction(() => !document.querySelector('.modal'), null, { timeout: 4000 });
}

// ---------------------------------------------------------------- tesztek
(async () => {
  if (!process.env.MA_UI_DEV_HTML) {
    const r = spawnSync('python3', [path.join(WEB, 'build_gui.py'), '--dev'], { cwd: ROOT, encoding: 'utf-8' });
    if (r.status !== 0) { console.error('build hiba: ' + r.stderr + r.stdout); process.exit(1); }
    console.log(r.stdout.trim());
  }
  srv = await startServer();
  try { browser = await chromium.launch({ headless: true }); } catch (e) { browser = await chromium.launch({ headless: true, executablePath: CHROME_FALLBACK }); }

  await test('regisztráció: Ábra-export (4 Elemzés) és Composer-forrás (2 PRISMA) al-képernyő, komponensek', async () => {
    const o = await openPage('#/overview');
    const r = await o.page.evaluate(() => ({
      screens: window.MA.app.screens().filter((s) => !s.placeholder).map((s) => s.id + ':' + String(s.tab)),
      caps: typeof window.MA.adaptersCaps.panel === 'function', val: typeof window.MA.adaptersValidator.box === 'function'
    }));
    check(r.screens.indexOf('figures:analysis') >= 0, 'figures a 4-es fülön');
    check(r.screens.indexOf('prisma-composer:prisma') >= 0, 'prisma-composer a 2-es fülön');
    check(r.caps && r.val, 'MA.adaptersCaps / MA.adaptersValidator');
    await go(o.page, 'prisma');
    check((await txt(o.page, '.app-subnav')).indexOf('Composer-forrás') >= 0, 'al-navigáció: Composer-forrás a PRISMA fülön');
    await finish(o, 'regisztráció');
  });

  await test('Képességek: funkciónkénti állapot, mód, teendő és őrök (legacy / h5 / ok)', async () => {
    const o = await openPage('#/capabilities');
    const p = o.page;
    await screen(p, 'capabilities');
    await p.waitForSelector('#adp-features tbody tr');
    const row = async (id) => txt(p, '#adp-features tr[data-feature="' + id + '"]');
    const rob = await row('rob');
    check(rob.indexOf('régi verzió (bridge)') >= 0 && rob.indexOf('◑') >= 0, 'rob: legacy szimbólummal és szöveggel (' + rob.slice(0, 80) + ')');
    check(rob.indexOf('bridge (régi verzió, őrökkel)') >= 0, 'rob: mód = bridge');
    check(rob.indexOf('V1') >= 0, 'rob: magyar teendő (V1 a json módhoz)');
    check((await row('tripod')).indexOf('H1') >= 0 && (await row('probast')).indexOf('H2') >= 0, 'H1 a TRIPOD+AI-nál, H2 a PROBAST+AI-nál');
    check((await row('grade')).indexOf('H3') >= 0 && (await row('amstar2')).indexOf('H4') >= 0, 'H3 a GRADE-nél, H4 az AMSTAR 2-nél');
    check((await row('pub_figure')).indexOf('H6') >= 0, 'publikációs ábra: H6-teendő (motor-SVG az F2-ig)');
    const pr = await row('prisma');
    check(pr.indexOf('H7') >= 0 && pr.indexOf('kimeneti mappáját') >= 0, 'composer: H7 és a hely-teendő');
    check((await txt(p, '#adp-converters')).indexOf('nincs működő helyi átalakító') >= 0, 'átalakító nélkül: magyar teendő');
    await p.click('#adp-caps-why');
    await p.waitForSelector('.why-pop');
    check((await txt(p, '.why-pop')).indexOf('őrökkel javítja') >= 0, '„Miért?”: az állapotok kezdőknek szóló magyarázata');
    await p.keyboard.press('Escape');
    // H5-világ
    await p.evaluate(() => window.MA.dev.adapters.scenario('h5'));
    await p.evaluate(() => window.MA.app.refresh());
    await screen(p, 'capabilities');
    await p.waitForSelector('#adp-features tr[data-feature="figure_audit"][data-state="unusable"]');
    const fa = await row('figure_audit');
    check(fa.indexOf('telepítve, de nem használható') >= 0 && fa.indexOf('◐') >= 0, 'figure_audit: unusable (◐ + szöveg)');
    check(fa.indexOf('pip install matplotlib') >= 0 && fa.indexOf('FIGURE_FORGE_PYTHON') >= 0 && fa.indexOf('H5') >= 0, 'H5: pontos teendő (pip + FIGURE_FORGE_PYTHON) és őr');
    check(fa.indexOf('most ez működik') >= 0 && fa.indexOf('stdlib') >= 0, 'a tartalék (stdlib-audit) jelölve');
    check((await row('rob')).indexOf('nincs telepítve') >= 0 && (await row('rob')).indexOf('claude plugin install validator') >= 0, 'validator hiányzik: telepítési parancs');
    // ok-világ
    await p.evaluate(() => window.MA.dev.adapters.scenario('ok'));
    await p.evaluate(() => window.MA.app.refresh());
    await screen(p, 'capabilities');
    await p.waitForSelector('#adp-features tr[data-feature="rob"][data-state="ok"]');
    const states = await p.$$eval('#adp-features tbody tr', (rs) => rs.map((r) => r.dataset.feature + ':' + r.dataset.state));
    ['rob', 'probast', 'tripod', 'grade', 'amstar2', 'figure_audit', 'pub_figure', 'prisma'].forEach((f) => check(states.indexOf(f + ':ok') >= 0, 'ok-világ: ' + f + ' működik'));
    check((await row('rob')).indexOf('közvetlen (json)') >= 0, 'ok-világ: json mód');
    await finish(o, 'képességek');
  });

  await test('Ábra-export (legacy): választók, formátum-okok, export → QC, 409 → felülírás, újra-ellenőrzés', async () => {
    const o = await openPage('#/figures');
    const p = o.page;
    await screen(p, 'figures');
    await p.waitForSelector('#fig-form');
    check(await p.$eval('#fig-run', (s) => s.value) !== '', 'a futás automatikusan kiválasztva');
    check((await p.evaluate(() => window.location.hash)).indexOf('run=') >= 0, 'a futás az URL-ben (megosztható nézet)');
    check(await p.$eval('#fig-kind-forest', (r) => r.checked), 'forest alapból');
    check(await p.$eval('#fig-kind-loo', (r) => r.disabled), 'LOO: nem elérhető (motorfüggvény kell)');
    check((await txt(p, '#fig-kinds')).indexOf('render_figure') >= 0, 'a letiltott fajta oka kiírva');
    check(await p.$eval('#fig-r-ff', (r) => r.disabled) && await p.$eval('#fig-r-engine', (r) => r.checked), 'figure-forge letiltva (0.2.1), motor-SVG kiválasztva');
    check((await txt(p, '#fig-r-ff-why')).indexOf('H6') >= 0, 'a figure-forge H6-teendője');
    check(await p.$eval('#fig-fmt-svg', (c) => c.checked && c.disabled), 'SVG mindig');
    for (const f of ['png', 'pdf', 'tiff', 'pptx']) {
      check(await p.$eval('#fig-fmt-' + f, (c) => c.disabled), f + ': letiltva');
    }
    check((await txt(p, '.adp-fmt[data-format="png"]')).indexOf('rsvg-convert') >= 0, 'PNG: átalakító-teendő');
    check((await txt(p, '.adp-fmt[data-format="tiff"]')).indexOf('figure-forge') >= 0, 'TIFF: figure-forge kell');
    check(await p.$eval('#fig-stem', (i) => i.value) === 'fig_forest_o1', 'alapértelmezett fájlnév');
    await p.fill('#fig-stem', '-rossz név');
    check(await p.$eval('#fig-stem', (i) => i.getAttribute('aria-invalid') === 'true') && await p.$eval('#fig-export', (b) => b.disabled), 'hibás fájlnév: aria-invalid, export tiltva');
    await p.fill('#fig-stem', 'fig_forest_o1');
    await p.selectOption('#fig-lang', 'en');
    await p.click('#fig-export');
    await p.waitForSelector('#fig-qc-badge');
    check(await p.$eval('#fig-qc-badge', (b) => b.dataset.badge) === 'ok', 'QC: zöld (a szerver döntése)');
    const qc = await txt(p, '#fig-result');
    check(/\d+\/\d+ motorszöveg szó szerint az ábrán/.test(qc), 'számhűség-sor a szerver darabszámaival');
    check(qc.indexOf('figure-forge audit') >= 0 && qc.indexOf('saját SVG-ellenőrzése') >= 0, 'figure-forge audit és stdlib-ellenőrzés');
    check((await txt(p, '#fig-files')).indexOf('06_kezirat/abrak/fig_forest_o1.svg') >= 0, 'az elkészült SVG');
    const sk = await txt(p, '#fig-skipped');
    check(sk.indexOf('PNG') >= 0 && sk.indexOf('TIFF') >= 0 && sk.indexOf('figure-forge') >= 0, 'a kihagyott formátumok okkal (' + sk.slice(0, 60) + ')');
    check((await txt(p, '#fig-warnings')).indexOf('render_figure') >= 0, 'figyelmeztetés: a futás saját (magyar) SVG-je készült');
    const body = (await calls(p, 'POST', '/api/figures/export'))[0].body;
    check(body.renderer === 'engine' && body.lang === 'en' && body.formats[0] === 'svg' && body.overwrite === false, 'a kérés: motor-SVG, en, svg, overwrite=false');
    await p.waitForSelector('#fig-existing-table tr[data-stem="fig_forest_o1"]');
    check(true, 'a meglévő ábrák listája frissült');
    // ugyanaz újra → 409 → megerősítés → felülírás
    await p.click('#fig-export');
    await modalOk(p, 'Felülírás');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.filter((c) => c.path === '/api/figures/export').length === 3);
    const all = await calls(p, 'POST', '/api/figures/export');
    check(all[1].body.overwrite === false && all[2].body.overwrite === true, '409 → megerősítés → overwrite: true');
    // újra-ellenőrzés a listából
    await p.click('#fig-existing-table tr[data-stem="fig_forest_o1"] button');
    await p.waitForFunction(() => /fig_forest_o1\.svg/.test((document.querySelector('#fig-result') || {}).textContent || '') && !document.querySelector('#fig-files'));
    check((await calls(p, 'POST', '/api/figures/audit'))[0].body.path === '06_kezirat/abrak/fig_forest_o1.svg', 'újra-ellenőrzés: POST /api/figures/audit');
    // előnézet: QC NÉLKÜL címkével
    check((await txt(p, '#fig-preview')).indexOf('QC NÉLKÜL') >= 0, 'a böngészős PNG csak előnézet (QC NÉLKÜL)');
    // billentyűzet: a megjelenítő-rádiók elérhetők
    await p.focus('#fig-r-engine');
    check(await p.evaluate(() => document.activeElement && document.activeElement.id === 'fig-r-engine'), 'billentyűzettel fókuszálható');
    await finish(o, 'ábra-export');
  });

  await test('Ábra-export: H5-világ (a figure-forge audit nem fut) és F2-világ (figure-forge export, hiányzó szám)', async () => {
    const o = await openPage('#/figures', { scenario: 'h5' });
    const p = o.page;
    await screen(p, 'figures');
    await p.waitForSelector('#fig-form');
    await p.click('#fig-kind-doi');
    check(await p.$eval('#fig-stem', (i) => i.value) === 'fig_doi_o1', 'a fájlnév követi az ábrafajtát');
    await p.click('#fig-export');
    await p.waitForSelector('#fig-qc-ffdown');
    const down = await txt(p, '#fig-qc-ffdown');
    check(down.indexOf('H5') >= 0 && down.indexOf('pip install matplotlib') >= 0, 'H5: a figure-forge audit nem fut, a teendő kiírva');
    check((await txt(p, '#fig-qc-std')).indexOf('glifák') >= 0, 'stdlib-ellenőrzés + „glifák csak figure-forge-dzsal”');
    await p.evaluate(() => window.MA.dev.adapters.scenario('ok'));
    await go(p, 'figures', { run: RUN_ID });
    await p.waitForSelector('#fig-form');
    check(await p.$eval('#fig-r-ff', (r) => !r.disabled && r.checked), 'F2: figure-forge elérhető és alapértelmezett');
    for (const f of ['pdf', 'png', 'tiff', 'pptx']) {
      check(await p.$eval('#fig-fmt-' + f, (c) => !c.disabled), f + ': elérhető figure-forge-dzsal');
    }
    await p.click('#fig-r-engine');
    check(await p.$eval('#fig-fmt-tiff', (c) => c.disabled) && await p.$eval('#fig-fmt-png', (c) => c.disabled), 'motor-SVG-re váltva a TIFF/PNG (átalakító nélkül) letiltva');
    await p.click('#fig-r-ff');
    await p.check('#fig-fmt-tiff');
    await p.fill('#fig-stem', 'fig2_forest');
    await p.click('#fig-export');
    await p.waitForSelector('#fig-qc-badge');
    check(await p.$eval('#fig-qc-badge', (b) => b.dataset.badge) === 'ok', 'F2: zöld');
    check((await txt(p, '#fig-files')).indexOf('.tiff') >= 0, 'F2: TIFF elkészült');
    check((await txt(p, '#fig-qc-list')).indexOf('figure-forge számhűség') >= 0 && (await txt(p, '#fig-qc-list')).indexOf('címke-QC: tiszta') >= 0, 'F2: figure-forge számhűség és címke-QC');
    check((await calls(p, 'POST', '/api/figures/export')).slice(-1)[0].body.renderer === 'figure-forge', 'a kérés: figure-forge');
    await p.fill('#fig-stem', 'fig2_forest_bad');
    await p.click('#fig-export');
    await p.waitForFunction(() => { const b = document.querySelector('#fig-qc-badge'); return b && b.dataset.badge === 'issues'; });
    check((await txt(p, '#fig-qc-list')).indexOf('Hiányzik az ábráról') >= 0, 'hiányzó szám: a hiányzó motorszöveg kiírva, nem zöld');
    await finish(o, 'ábra-export h5/ok');
  });

  await test('Composer-forrás: beállítás → előnézet → frissítés (kézi számok megerősítéssel) → composer-mód', async () => {
    const o = await openPage('#/prisma-composer');
    const p = o.page;
    await screen(p, 'prisma-composer');
    check(await p.$eval('#comp-mode-manual', (r) => r.checked) && await p.$eval('#comp-mode-composer', (r) => r.disabled), 'beállítatlan: kézi mód, a composer-mód tiltva');
    check((await txt(p, '#comp-cant')).indexOf('kimeneti mappáját') >= 0, 'a tiltás oka kiírva');
    check((await txt(p, '#comp-h7')).indexOf('saját Python-értelmezőjével') >= 0, 'H7: explicit interpreter, csak olvasás');
    await p.fill('#comp-project', 'rossz név');
    await p.fill('#comp-outdir', '/home/szk/Documents/PubMed_Downloads');
    await p.click('#comp-save-loc');
    check(await p.$eval('#comp-project', (i) => i.getAttribute('aria-invalid') === 'true'), 'hibás projekt-név: aria-invalid');
    await p.fill('#comp-project', 'glp1');
    await p.click('#comp-save-loc');
    await p.waitForFunction(() => { const b = document.querySelector('#comp-refresh'); return b && !b.disabled; });
    check((await calls(p, 'PUT', '/api/prisma/composer/config'))[0].body.project === 'glp1', 'PUT config');
    await p.click('#comp-preview');
    await p.waitForSelector('#comp-flow');
    check((await txt(p, '#comp-flow tr[data-box="B"]')).indexOf('946') >= 0, 'előnézet: a dobozszámok (B = 946)');
    check((await txt(p, '#comp-flow tr[data-box="J"]')).indexOf('25') >= 0, 'J = a composer „included” (jelentések)');
    check((await txt(p, '#comp-check')).indexOf('A motor ellenőrzése') >= 0, 'a motor ellenőrzése');
    check((await txt(p, '#comp-result-warnings')).indexOf('FIGYELEM: 12 azonosított rekord') >= 0, 'a composer figyelmeztetése szó szerint');
    await p.evaluate(() => window.MA.dev.adapters.manualNumbers(true));
    await p.click('#comp-refresh');
    await modalOk(p, 'Igen, a composerből');
    await p.waitForSelector('#comp-warnings');
    const refr = await calls(p, 'POST', '/api/prisma/composer/refresh');
    check(refr.length === 3 && !refr[1].body.confirm_replace_manual && refr[2].body.confirm_replace_manual === true, 'kézi számok: 409 → megerősítés → confirm_replace_manual');
    check(await p.$eval('#comp-mode-composer', (r) => r.checked), 'composer-mód');
    check((await txt(p, '#comp-last')).indexOf('composer 1.4.1') >= 0, 'utolsó frissítés: verzió + export sha256');
    check((await txt(p, '#comp-warnings')).indexOf('függőben') >= 0, 'a 5D „függőben” figyelmeztetés');
    await p.click('#comp-to-manual');
    await modalOk(p, 'PRISMA képernyő');
    await screen(p, 'prisma');
    check(true, 'vissza kézire: a PRISMA képernyő indoklásos felülírása');
    await finish(o, 'composer');
  });

  await test('validator-keresztellenőrzés: H2/H3 őrök, NEM hivatalos implikált ítélet, 424', async () => {
    const o = await openPage('#/capabilities');
    const p = o.page;
    await screen(p, 'capabilities');
    async function mount(doc) {
      await p.evaluate((d) => {
        const old = document.getElementById('adp-val-test');
        if (old) { old.remove(); }
        const el = window.MA.adaptersValidator.box(() => d, { id: 'adp-val-run' });
        el.id = 'adp-val-test';
        document.getElementById('screen-root').appendChild(el);
      }, doc);
      await p.click('#adp-val-run');
      await p.waitForSelector('#adp-val-test .adp-val-result, #adp-val-test .adp-val-down');
      return txt(p, '#adp-val-test');
    }
    const prob = await mount(DOCS.probast_dev);
    check(prob.indexOf('16/34') >= 0 && prob.indexOf('32/34') >= 0 && prob.indexOf('nem megbízható') >= 0, 'H2: a munkapad 16/34, a validator 32/34 — nem megbízható');
    const sent = (await calls(p, 'POST', '/api/validator/check'))[0].body.doc;
    check(sent.answers['development/1.1'].value === 'probably_yes' && !('evidence' in sent.answers['development/1.1']), 'csak az érték megy át, idézet nem');
    const rob = await mount(DOCS.rob2);
    check(rob.indexOf('KONZERVATÍV') >= 0 && rob.indexOf('NEM a hivatalos folyamatábra') >= 0, 'implikált ítélet algoritmus-címkével, nem hivatalos');
    const gr = await mount(DOCS.grade_strong);
    check(gr.indexOf('nem megbízható') >= 0 && gr.indexOf('H3') >= 0, 'H3: a validator GRADE-bizonyossága nem megbízható');
    const am = await mount(DOCS.amstar2_py);
    check(am.indexOf('weakness') >= 0 && am.indexOf('H4') >= 0, 'H4: weakness konvenció címkézve');
    await p.evaluate(() => window.MA.dev.adapters.scenario('h5'));
    const down = await mount(DOCS.rob2);
    check(down.indexOf('nem érhető el') >= 0 && down.indexOf('claude plugin install validator') >= 0, '424: érthető üzenet a teendővel');
    await finish(o, 'validator');
  });

  await test('HU ↔ EN és sötét téma az adapter-képernyőkön', async () => {
    const o = await openPage('#/figures');
    const p = o.page;
    await screen(p, 'figures');
    await p.click('#lang-switch [data-lang="en"]');
    await screen(p, 'figures');
    check((await txt(p, '#fig-form')).indexOf('Export a figure') >= 0 && (await txt(p, '#fig-form')).indexOf('engine SVG') >= 0, 'EN: ábra-export');
    await go(p, 'prisma-composer');
    check((await txt(p, '#screen-root')).indexOf('PRISMA numbers from the composer') >= 0, 'EN: composer');
    await go(p, 'capabilities');
    await p.waitForSelector('#adp-features');
    check((await txt(p, '#adp-caps')).indexOf('Features with plugins') >= 0 && (await txt(p, '#adp-features')).indexOf('old version (bridge)') >= 0, 'EN: képességek');
    await p.click('#theme-toggle');
    check(['dark', 'light'].indexOf(await p.getAttribute('html', 'data-theme')) >= 0, 'téma váltható');
    await p.click('#lang-switch [data-lang="hu"]');
    await screen(p, 'capabilities');
    await finish(o, 'nyelv-téma');
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
