#!/usr/bin/env node
/* tests/gui/ui/process.spec.js — Folyamat és audit képernyők böngészős tesztje (terv 3.5.1, 3.5.14–3.5.17, 7.1 T6, 7.6, 8.6).
 *
 * Futtatás:  node tests/gui/ui/process.spec.js        (kilépési kód 1, ha bármi elbukik)
 *   MA_UI_DEV_HTML=<út>  — kész dev-build használata (a build kimarad).
 * A dev-buildet ?fixtures=1-gyel nyitja (src/dev/process_backend.js: állapottartó háttér), szigorú CSP-fejléc mellett.
 * Lefedi: Áttekintés (szakaszok, blockerek, X-teendők, kimenetek, „Miért?” buborék billentyűzettel), PRISMA (élő P-ellenőrzés,
 * aria-invalid/describedby, folyamatábra-dobozok, levezetett érték, kizárási okok, mentés If-Match-csel, 409, composer-mód
 * kézi felülírással, mentetlen-őr), vizsgálat ↔ jelentés (szerkesztés, mentés, 422), Napló (fülek, szűrők, új megállapítás/
 * döntés, lezárás motor-üzenettel, kapu előzetes tiltással és GATE_BLOCKED versennyel, X-szabályok, tevékenység, KB-kereső
 * Ctrl+K-val, helyi forrás címke), Képességek (állapotok, újraszondázás, adatvédelem), Export (adatosztály-alapértékek,
 * audit-ZIP, pillanatkép tudomásulvétellel, Artifact-tilalom), HU/EN, sötét téma, DOM-biztonság (HTML-szerű címke
 * szövegként), böngészőtároló-szabály, konzolhiba-mentesség.
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
const XSS = '<img src=x onerror=alert(1)>';
const CSP = (n) => "default-src 'none'; script-src 'nonce-" + n + "'; style-src 'nonce-" + n + "'; " +
  "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

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

// ---------------------------------------------------------------- segédek
let browser, srv;
async function openPage(hash, opts) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await ctx.newPage();
  const errors = [];
  const dialogs = [];
  page.on('console', (m) => { if (m.type() === 'error') { errors.push('console: ' + m.text()); } });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => { dialogs.push(d.type() + ':' + d.message()); d.dismiss().catch(() => null); });
  if (opts && opts.init) { await page.addInitScript(opts.init); }
  await page.goto(srv.base + '/?fixtures=1' + (hash || ''));
  await page.waitForSelector('html[data-ready="1"]', { timeout: 10000 });
  return { ctx, page, errors, dialogs };
}
async function screen(page, id) {
  await page.waitForFunction((x) => { const r = document.getElementById('screen-root'); return r && r.dataset.screen === x && !r.querySelector('.spinner'); }, id, { timeout: 6000 });
}
async function txt(page, sel) { const el = await page.$(sel); return el ? ((await el.textContent()) || '') : ''; }
async function calls(page, method, p) {
  return page.evaluate(([m, pp]) => window.MA.dev.fixtures.calls.filter((c) => c.method === m && c.path === pp), [method, p]);
}
async function storageReport(page) {
  return page.evaluate(() => {
    const ls = [], ss = [];
    for (let i = 0; i < localStorage.length; i++) { ls.push(localStorage.key(i)); }
    for (let i = 0; i < sessionStorage.length; i++) { ss.push(sessionStorage.key(i)); }
    return { ls, ss, lsValues: ls.map((k) => localStorage.getItem(k)), cookie: document.cookie };
  });
}
async function domSafe(page, where) {
  const r = await page.evaluate(() => ({
    img: document.querySelectorAll('img').length,
    onerr: document.querySelectorAll('[onerror]').length,
    scripts: Array.from(document.scripts).filter((s) => !s.nonce).length
  }));
  check(r.img === 0 && r.onerr === 0 && r.scripts === 0, where + ': nincs <img>/onerror/nonce nélküli <script> a DOM-ban (' + JSON.stringify(r) + ')');
}
async function finish(o, label) {
  check(o.errors.length === 0, label + ': nincs konzolhiba / CSP-sértés: ' + o.errors.join(' || '));
  check(o.dialogs.length === 0, label + ': nem nyílt alert/confirm (XSS-próba): ' + o.dialogs.join(' || '));
  const rep = await storageReport(o.page);
  check(rep.ls.every((k) => k.startsWith('mag.pref.')), label + ': localStorage csak mag.pref.* (' + rep.ls.join(',') + ')');
  check(rep.lsValues.every((v) => v.length <= 256 && !/Aronson|Comstock|<img|412|nincs TBC|SE\/SD|BCG/.test(v)), label + ': a preferenciákban nincs projektadat (' + rep.lsValues.join('|') + ')');
  check(rep.ss.every((k) => k === 'mag.token'), label + ': sessionStorage csak mag.token (' + rep.ss.join(',') + ')');
  check(rep.cookie === '', label + ': nincs süti');
  const missing = await o.page.evaluate(() => window.MA.i18n.missing());
  check(missing.length === 0, label + ': nincs hiányzó i18n-kulcs (' + missing.join(', ') + ')');
  await domSafe(o.page, label);
  await o.ctx.close();
}
async function waitCheck(page) {
  await page.waitForTimeout(300);
  await page.waitForFunction(() => { const s = document.querySelector('.pf-status'); return s && /ellenőrizve|checked/.test(s.textContent); }, null, { timeout: 4000 });
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

  // ========== 0. regisztráció
  await test('a hat képernyő és a „Miért?” komponens be van kötve', async () => {
    const o = await openPage('#/overview');
    const r = await o.page.evaluate(() => ({
      screens: window.MA.app.screens().filter((s) => !s.placeholder).map((s) => s.id + ':' + String(s.tab)),
      why: ['button', 'open', 'panel', 'item', 'openItem', 'kbButton', 'strength', 'close'].every((k) => typeof window.MA.why[k] === 'function')
    }));
    ['overview:overview', 'prisma:prisma', 'studies:prisma', 'log:log', 'capabilities:null', 'export:export'].forEach((x) => check(r.screens.indexOf(x) >= 0, 'regisztrált: ' + x));
    check(r.why, 'MA.why API (button, open, panel, item, openItem, kbButton, close)');
    await finish(o, 'regisztráció');
  });

  // ========== 1. Áttekintés
  await test('Áttekintés: szakaszok, blockerek, X-teendők, kimenetek, „Miért?” buborék (billentyűzet)', async () => {
    const o = await openPage('#/overview');
    const p = o.page;
    await screen(p, 'overview');
    await p.waitForSelector('#ov-stages .stage');
    check((await p.$$('#ov-stages .stage')).length === 16, '16 szakasz (S00–S14, FINAL)');
    check((await txt(p, '#ov-stages li[data-stage="S04"]')).indexOf('[F]') >= 0, 'S04: FAIL + nyitott blocker → [F]');
    check(await p.evaluate(() => document.querySelector('#ov-stages li[data-stage="S05"]').classList.contains('is-blocked')), 'S05: nyitott blocker jelölés (osztály + szimbólum)');
    check((await p.getAttribute('#ov-stages li[data-stage="S00"] a', 'aria-label')).indexOf('PASS') >= 0, 'a szakasz-link akadálymentes címkéje');
    check((await txt(p, '#ov-blockers')).indexOf('#12') >= 0 && (await txt(p, '#ov-blockers')).indexOf('#15') >= 0, 'nyitott blockerek: #12, #15');
    const next = await txt(p, '#ov-next');
    check(next.indexOf('X001') >= 0 && next.indexOf('python ma.py analyze --spec 05_elemzes/specs/o1_primary.json') >= 0, 'X001 + javasolt parancs');
    check((await p.getAttribute('#ov-next li[data-code="X001"] .ov-act', 'href')) === '#/analysis?outcome=o1', 'X001 → újrafuttatás az Elemzés képernyőn (o1)');
    check((await p.getAttribute('#ov-next li[data-code="X010"] .ov-act', 'href')) === '#/extraction?outcome=o1', 'X010 → szűrés a Kinyerésben');
    await p.waitForSelector('#overview-outcomes');
    check((await txt(p, '#overview-outcomes')).indexOf('0,49 [0,33; 0,73]') >= 0, 'kimenet: a motor display_text-je szó szerint');
    // „Miért?” a #15 blockeren: billentyűzettel nyit, KB-tétel (V011) betöltődik, Esc zár és visszaadja a fókuszt
    await p.focus('#ov-blockers li[data-id="15"] .why-btn');
    await p.keyboard.press('Enter');
    await p.waitForSelector('.why-pop[role="dialog"]');
    check((await p.getAttribute('.why-pop', 'aria-modal')) === 'false', 'a buborék nem modális (aria-modal=false)');
    check((await p.getAttribute('#ov-blockers li[data-id="15"] .why-btn', 'aria-expanded')) === 'true', 'aria-expanded=true');
    check(await p.evaluate(() => !!document.activeElement.closest('.why-pop')), 'a fókusz a buborékba kerül');
    await p.waitForFunction(() => /SD helyett SE gyanúja/.test(document.querySelector('.why-pop').textContent));
    const pop = await txt(p, '.why-pop');
    check(pop.indexOf('Mit tegyél?') >= 0 && pop.indexOf('Ellenőrizd a forrást') >= 0, 'KB-javaslat egyszerű nyelven (V011)');
    check((await p.$$('.why-pop .kb-ref[data-kb="D-S07-004"]')).length === 1, 'kapcsolódó KB-tétel gomb (D-S07-004)');
    await p.keyboard.press('Escape');
    check((await p.$('.why-pop')) === null, 'Esc bezárja a buborékot');
    check(await p.evaluate(() => document.activeElement.classList.contains('why-btn')), 'a fókusz visszakerül a „Miért?” gombra');
    // X001: még nincs KB-tétel → a motor szövege + jelzés
    await p.click('#ov-next li[data-code="X001"] .why-btn');
    await p.waitForSelector('.why-pop .why-nokb');
    check((await txt(p, '.why-pop')).indexOf('Az elsődleges commit-futás régebbi') >= 0, 'X001: a motor szövege a buborékban');
    await p.click('#ov-next li[data-code="X001"] .why-btn');
    check((await p.$('.why-pop')) === null, 'ugyanaz a gomb újra: bezár (kapcsoló)');
    // KB-tétel modális a buborékból
    await p.click('#ov-blockers li[data-id="12"] .why-btn');
    await p.waitForSelector('.why-pop .why-open-kb');
    await p.click('.why-pop .why-open-kb');
    await p.waitForSelector('[role="dialog"][aria-modal="true"] .kb-fields');
    const modal = await txt(p, '[role="dialog"][aria-modal="true"]');
    check(modal.indexOf('P007') >= 0 && modal.indexOf('PRISMA 2020 16a tétel') >= 0 && modal.indexOf('kötelező') >= 0, 'KB-tétel modális: P007 mezői (hely, erősség)');
    await p.keyboard.press('Escape');
    check(await p.evaluate(() => !!document.activeElement.closest('li[data-id="12"]') && document.activeElement.classList.contains('why-btn')), 'a KB-modális bezárása után a fókusz a „Miért?” gombon');
    // ugrás a naplóba, a tétel kiemelve és fókuszban
    await p.click('#ov-blockers li[data-id="15"] a.btn');
    await screen(p, 'log');
    await p.waitForSelector('#log-row-15.is-selected');
    await p.waitForFunction(() => document.activeElement && document.activeElement.id === 'log-row-15');
    check(true, 'ugrás → Napló, a #15 sor kiemelve és fókuszban');
    await finish(o, 'áttekintés');
  });

  // ========== 2. PRISMA
  await test('PRISMA: élő P-ellenőrzés, aria-invalid, folyamatábra, levezetett érték, okok, mentés If-Match-csel', async () => {
    const o = await openPage('#/prisma');
    const p = o.page;
    await screen(p, 'prisma');
    await p.waitForSelector('#pf-list li[data-code="P007"]');
    check((await p.getAttribute('#pf-in-H', 'aria-invalid')) === 'true', 'H doboz: aria-invalid=true (P007)');
    check((await p.getAttribute('#pf-in-H', 'aria-describedby')) === 'pf-f-0', 'H doboz: aria-describedby → a megállapítás');
    check((await p.getAttribute('#pf-in-B', 'aria-invalid')) === 'false', 'B doboz rendben');
    check(await p.evaluate(() => document.querySelector('.pf-box[data-box="H"]').classList.contains('is-error')), 'folyamatábra: a H doboz hibás (osztály)');
    check((await txt(p, '.pf-box[data-box="H"]')).indexOf('✖') === 0, 'folyamatábra: a hibát szimbólum is jelöli (nem csak szín)');
    check((await txt(p, '.pf-svg')).indexOf('Szűrt rekordok (B): 325') >= 0, 'folyamatábra: a beírt szám szövegként');
    check((await txt(p, '#pf-summary')).replace(/\s+/g, '').indexOf('✖hiba:1') >= 0, 'összesítő: ✖ 1');
    check((await p.$$('.pf-svg line.pf-arrow')).length >= 8, 'nyilak a dobozok között');
    // ugrás a dobozra
    await p.click('#pf-list li[data-code="P007"] .pf-goto');
    check(await p.evaluate(() => document.activeElement.id === 'pf-in-H'), '„ugrás: H” → a H mező kap fókuszt');
    // E törlése → a motor levezeti (P011) — előnézet, nem mentés
    await p.fill('#pf-in-E', '');
    await waitCheck(p);
    await p.waitForSelector('#pf-list li[data-code="P011"]');
    check((await txt(p, '.pf-svg')).indexOf('levezetve: 54') >= 0, 'levezetett érték a motor derived-jéből (E = 54)');
    check(await p.evaluate(() => document.querySelector('#pf-in-E').classList.contains('is-warning') === false && document.querySelector('.pf-box[data-box="E"]').classList.contains('is-info')), 'E doboz: info-jelölés');
    let puts = await calls(p, 'PUT', '/api/prisma/manual');
    check(puts.length >= 1 && puts.every((c) => c.body.dry_run === true) && puts[puts.length - 1].body.flow.sought_for_retrieval === null, 'előnézet: PUT {dry_run: true}, az üres doboz null');
    check(puts.every((c) => /^\d+$/.test(c.headers['X-MA-Client-Seq']) && typeof c.body.client_seq === 'number'), 'client_seq a fejlécben és a törzsben (legutolsó nyer)');
    await p.fill('#pf-in-E', '54');
    await waitCheck(p);
    // gyors gépelés: csak a legutolsó kérés eredménye számít
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.fill('#pf-rn-1', '');
    await p.type('#pf-rn-1', '13', { delay: 30 });
    await waitCheck(p);
    puts = await calls(p, 'PUT', '/api/prisma/manual');
    check(puts.length <= 2, 'debounce: gépelés közben legfeljebb 1–2 előnézet (' + puts.length + ')');
    check((await p.$('#pf-list li[data-code="P007"]')) === null, 'az okok javítása után a P007 eltűnik');
    check((await p.getAttribute('#pf-in-H', 'aria-invalid')) === 'false' && (await p.getAttribute('#pf-in-H', 'aria-describedby')) === null, 'H: aria-invalid=false, nincs describedby');
    check(!(await p.evaluate(() => document.querySelector('.pf-box[data-box="H"]').classList.contains('is-error'))), 'a H doboz már nem hibás');
    // ismétlődő ok → mentés tiltva, jelölve
    await p.click('#pf-add-reason');
    check(await p.evaluate(() => document.activeElement.id === 'pf-r-3'), '+ kizárási ok → az új mező kap fókuszt');
    await p.type('#pf-r-3', 'nem BCG-oltás');
    check((await p.getAttribute('#pf-r-3', 'aria-invalid')) === 'true' && (await p.getAttribute('#pf-r-2', 'aria-invalid')) === 'true', 'ismétlődő ok: mindkét mező aria-invalid');
    check(await p.$eval('#pf-save', (b) => b.disabled) && !(await p.$eval('#pf-dup', (x) => x.hidden)), 'ismétlődő ok: a mentés tiltva, megjegyzés látszik');
    await p.click('.pf-reason-row:nth-child(4) .btn-icon');
    check(await p.evaluate(() => document.activeElement.id === 'pf-add-reason'), 'ok törlése → a fókusz a „+ kizárási ok” gombra kerül');
    await waitCheck(p);
    check(!(await p.$eval('#pf-save', (b) => b.disabled)), 'a mentés újra engedélyezett');
    // mentetlen-őr
    await p.click('#nav-overview');
    await p.waitForSelector('[role="dialog"][aria-modal="true"]');
    check((await txt(p, '[role="dialog"]')).indexOf('Nem mentett') >= 0, 'mentetlen változás → megerősítés navigáláskor');
    await p.click('[role="dialog"] .modal-actions .btn-ghost');
    await p.waitForFunction(() => location.hash === '#/prisma');
    check(true, 'Mégse → a PRISMA képernyő marad');
    // mentés
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#pf-save');
    await p.waitForSelector('.toast.is-success');
    const save = (await calls(p, 'PUT', '/api/prisma/manual')).filter((c) => !c.body.dry_run);
    check(save.length === 1 && save[0].headers['If-Match'] === '"prisma-1"', 'mentés: PUT If-Match: "prisma-1"');
    check(save[0].body.flow.excluded_eligibility_reasons['nincs TBC-kimenet'] === '13' && save[0].body.flow.screened === '325', 'a flow nyers szövegként megy (a motor parszol)');
    await screen(p, 'prisma');
    check((await txt(p, '#pf-list')).indexOf('nem talált eltérést') >= 0, 'mentés után: nincs P-megállapítás');
    check((await txt(p, '.pf-saved, #pf-save-why')).indexOf('mentve') >= 0, 'állapot: mentve');
    await finish(o, 'prisma');
  });

  await test('PRISMA: 409 ütközés, composer-mód (csak olvasható) és kézi felülírás kötelező indoklással; P006/P001 előnézet', async () => {
    const o = await openPage('#/prisma');
    const p = o.page;
    await screen(p, 'prisma');
    await p.fill('#pf-in-I', '16');
    await waitCheck(p);
    check((await p.$('#pf-list li[data-code="P006"]')) !== null && (await p.getAttribute('#pf-in-I', 'aria-invalid')) === 'true', 'I = 16 > J = 15 → P006, az I mező hibás');
    await p.fill('#pf-in-I', '13');
    await p.fill('#pf-in-B', '12a');
    await waitCheck(p);
    check((await p.$('#pf-list li[data-code="P001"]')) !== null && (await p.getAttribute('#pf-in-B', 'aria-invalid')) === 'true', 'nem szám (12a) → P001 a motortól (a felület nem parszol)');
    await p.fill('#pf-in-B', '325');
    await waitCheck(p);
    await p.evaluate(() => window.MA.dev.process.bumpEtag('prisma'));
    await p.click('#pf-save');
    await p.waitForSelector('#ma-alerts .toast[data-code="CONFLICT"]');
    check((await txt(p, '#ma-alerts .toast[data-code="CONFLICT"]')).indexOf('közben megváltozott') >= 0, '409 CONFLICT: a szerver üzenete szó szerint');
    await p.click('#ma-alerts .toast[data-code="CONFLICT"] .toast-close');
    // composer-mód
    await p.evaluate(() => window.MA.dev.process.prismaMode('composer'));
    await p.click('#pf-discard');
    await screen(p, 'prisma');
    await p.waitForSelector('#pf-override');
    check(await p.$eval('#pf-in-A1', (i) => i.readOnly && i.getAttribute('aria-readonly') === 'true'), 'composer-mód: a mezők csak olvashatók');
    check((await p.$('#pf-save')) === null, 'composer-mód: nincs mentés gomb');
    check((await txt(p, '#pf-head')).indexOf('composer (bcg') >= 0 && (await txt(p, '#pf-list')).indexOf('retmax') >= 0, 'composer forrás és állapotszöveg');
    await p.click('#pf-override');
    await p.waitForSelector('#pf-override-reason');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    check((await p.getAttribute('#pf-override-reason', 'aria-invalid')) === 'true' && !(await p.$eval('[role="dialog"] .pf-err', (x) => x.hidden)), 'indoklás nélkül nem írható felül');
    await p.fill('#pf-override-reason', 'A composer exportja a kézi kizárási okokat nem bontja; a teljes szöveg szintű napló alapján.');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await screen(p, 'prisma');
    await p.waitForSelector('#pf-save');
    check(!(await p.$eval('#pf-in-A1', (i) => i.readOnly)) && (await txt(p, '#pf-form')).indexOf('kézi felülírás') >= 0, 'felülírás: szerkeszthető, az indoklás látszik');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#pf-save');
    await p.waitForSelector('.toast.is-success');
    const save = (await calls(p, 'PUT', '/api/prisma/manual')).filter((c) => !c.body.dry_run);
    check(save.length === 1 && /teljes szöveg szintű napló/.test(save[0].body.override_reason || ''), 'mentés: override_reason a törzsben');
    await finish(o, 'prisma-composer');
  });

  // ========== 3. vizsgálat ↔ jelentés
  await test('Vizsgálat ↔ jelentés: HTML-szerű címke szövegként, jelentés hozzáadása, mentés If-Match-csel, 422', async () => {
    const o = await openPage('#/studies');
    const p = o.page;
    await screen(p, 'studies');
    check((await p.$$('#st-table tbody tr')).length === 13, '13 vizsgálat (BCG)');
    const xs = await p.evaluate(() => { const tr = document.querySelector('#st-table tr[data-study="COMSTOCK1976"]'); return tr ? tr.querySelector('.st-label').textContent : null; });
    check(xs === 'Comstock et al 1976 ' + '<img src=x onerror=alert(1)>', 'HTML-szerű címke szövegként jelenik meg (' + xs + ')');
    check(await p.$eval('#st-lab-12', (i) => i.value.indexOf('<img') >= 0), 'a címke a mezőben is szöveg');
    check((await txt(p, '#st-summary')).indexOf('I = 13') >= 0 && (await txt(p, '#st-summary')).indexOf('J = 15') >= 0, 'összesítő a szervertől (I = 13, J = 15)');
    check(!!(await p.$('.app-subnav a[href="#/prisma"]')) && !!(await p.$('.app-subnav a[href="#/studies"]')), 'al-navigáció a PRISMA fülön');
    await p.click('#st-addrep-0');
    check(await p.evaluate(() => document.activeElement.id === 'st-rec-0-1'), '+ jelentés → az új mező kap fókuszt');
    await p.type('#st-rec-0-1', 'pmid:99999999');
    await p.selectOption('#st-role-0-1', 'secondary');
    await p.selectOption('#st-design-1', 'cohort');
    await p.click('#st-save');
    await p.waitForSelector('.toast.is-success');
    const put = await calls(p, 'PUT', '/api/studies');
    check(put.length === 1 && put[0].headers['If-Match'] === '"studies-1"', 'PUT /api/studies If-Match-csel');
    check(put[0].body.schema === 'szk.ma.studies/v1' && put[0].body.studies[0].reports[1].rec_id === 'pmid:99999999' && put[0].body.studies[1].design === 'cohort', 'a törzs szk.ma.studies/v1, az új jelentés és elrendezés benne');
    await screen(p, 'studies');
    check((await txt(p, '#st-summary')).indexOf('J = 16') >= 0, 'mentés után a szerver összesítője: J = 16');
    // ismétlődő study_id → 422 a szervertől
    await p.click('#st-add');
    await screen(p, 'studies');
    check(await p.evaluate(() => document.activeElement.id === 'st-id-13'), '+ vizsgálat → az új sor azonosító-mezője kap fókuszt');
    await p.type('#st-id-13', 'HART1977');
    await p.click('#st-save');
    await p.waitForSelector('#ma-alerts .toast[data-code="VALIDATION"]');
    check((await txt(p, '#ma-alerts .toast[data-code="VALIDATION"]')).indexOf('Ismétlődő vagy üres study_id: HART1977') >= 0, '422: a szerver üzenete szó szerint');
    await p.click('#ma-alerts .toast .toast-close');
    // törlés megerősítéssel
    await p.click('#st-table tr:last-child .btn-danger');
    await p.waitForSelector('[role="dialog"][aria-modal="true"]');
    await p.click('[role="dialog"] .btn-danger');
    await screen(p, 'studies');
    check((await p.$$('#st-table tbody tr')).length === 14 - 1, 'a vizsgálat törölve (megerősítés után)');
    await p.click('#st-discard');
    await screen(p, 'studies');
    await finish(o, 'studies');
  });

  // ========== 4. Napló
  await test('Napló: fülek billentyűzettel, szűrők, új megállapítás és döntés, lezárás motor-üzenettel', async () => {
    const o = await openPage('#/log');
    const p = o.page;
    await screen(p, 'log');
    await p.waitForSelector('#log-findings');
    check((await txt(p, '#log-tab-finding')).indexOf('⛔ 2') >= 0 && (await txt(p, '#log-tab-audit')).indexOf('3') >= 0, 'fül-jelvények: ⛔ 2 blocker, 3 X-szabály');
    check((await txt(p, '#log-findings')).indexOf('Vizsgálatcímke ellenőrzése: ' + XSS) >= 0, 'HTML-szerű cím szövegként a táblában');
    check((await p.$$('#log-findings tbody tr')).length === 4, 'alapszűrő: 4 nyitott megállapítás');
    await p.selectOption('#log-f-severity', 'blocker');
    await p.waitForFunction(() => document.querySelectorAll('#log-findings tbody tr').length === 2);
    check(true, 'súlyosság=blocker → 2 sor');
    const q = (await calls(p, 'GET', '/api/log/finding')).pop().query;
    check(q.status === 'open' && q.severity === 'blocker', 'a szűrők a szerver lekérdezésébe kerülnek');
    await p.selectOption('#log-f-severity', '');
    await p.selectOption('#log-f-status', '');
    await p.waitForFunction(() => document.querySelectorAll('#log-findings tbody tr').length === 5);
    await p.selectOption('#log-f-agent', 'reviewer');
    await p.waitForFunction(() => document.querySelectorAll('#log-findings tbody tr').length === 3);
    check(true, 'ügynök-szűrő: reviewer → 3 sor');
    await p.selectOption('#log-f-agent', '');
    // új megállapítás
    await p.click('#nf-toggle');
    check((await p.getAttribute('#nf-toggle', 'aria-expanded')) === 'true' && await p.evaluate(() => document.activeElement.id === 'nf-sev'), 'új megállapítás: kinyílik, fókusz az első mezőn');
    await p.click('#nf-submit');
    check((await p.getAttribute('#nf-title', 'aria-invalid')) === 'true', 'cím nélkül nem küldhető (aria-invalid)');
    await p.selectOption('#nf-sev', 'blocker');
    await p.selectOption('#nf-stage', 'S06');
    await p.fill('#nf-title', 'RoB-értékelés hiányzik 2 vizsgálatnál');
    await p.fill('#nf-kb', 'D-S06-008, XYZ');
    await p.click('#nf-submit');
    await p.waitForSelector('.toast.is-warning');
    check((await txt(p, '.toast.is-warning')).indexOf('#16') >= 0 && (await txt(p, '.toast.is-warning')).indexOf('XYZ') >= 0, 'rögzítve #16; a szerver figyelmeztetése (ismeretlen KB) látszik');
    const nf = (await calls(p, 'POST', '/api/log/finding'))[0].body;
    check(nf.agent === 'user' && nf.severity === 'blocker' && nf.stage === 'S06' && nf.kb_refs.join(',') === 'D-S06-008,XYZ', 'POST /api/log/finding törzse');
    await p.waitForSelector('#log-row-16');
    check((await txt(p, '#log-tab-finding')).indexOf('⛔ 3') >= 0, 'a blocker-szám frissül: ⛔ 3');
    // lezárás: blocker wontfix → a motor elutasítja, üzenete a modálisban
    await p.click('#log-row-12 .log-resolve');
    await p.waitForSelector('#rs-text');
    await p.check('[role="dialog"] input[value="wontfix"]');
    await p.fill('#rs-text', 'nem javítjuk');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForFunction(() => /csak fixed vagy indokolt invalid/.test(document.querySelector('[role="dialog"] .pf-err').textContent));
    check(true, 'blocker wontfix → a motor üzenete szó szerint a modálisban');
    await p.check('[role="dialog"] input[value="fixed"]');
    await p.fill('#rs-text', 'A kizárási okok összege javítva (02_szures).');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForFunction(() => !document.querySelector('[role="dialog"]'));
    await p.waitForFunction(() => { const r = document.getElementById('log-row-12'); return r && /javítva/.test(r.textContent); });
    const rs = (await calls(p, 'POST', '/api/log/resolve')).pop().body;
    check(rs.id === 12 && rs.status === 'fixed' && /javítva/.test(rs.resolution), 'POST /api/log/resolve {id, status, resolution}');
    // döntések fül: billentyűzettel (→ + Enter)
    await p.focus('#log-tab-finding');
    await p.keyboard.press('ArrowRight');
    check(await p.evaluate(() => document.activeElement.id === 'log-tab-decision'), '→ a következő fülre lép');
    await p.keyboard.press('Enter');
    await p.waitForSelector('#log-decisions');
    check((await p.getAttribute('#log-tab-decision', 'aria-selected')) === 'true' && (await p.getAttribute('#log-panel', 'aria-labelledby')) === 'log-tab-decision', 'aria-selected és a panel címkéje');
    check(await p.evaluate(() => location.hash.indexOf('tab=decision') >= 0), 'a fül az URL-ben (replaceState)');
    check((await txt(p, '#log-decisions')).indexOf('Vizsgálatcímke rögzítve: ' + XSS) >= 0, 'HTML-szerű döntésszöveg szövegként');
    await p.click('#nd-toggle');
    await p.fill('#nd-decision', 'A Hart 1977 SD-jét SE-ként kezeljük (táblázat lábjegyzete)');
    await p.click('#nd-submit');
    check((await p.getAttribute('#nd-rationale', 'aria-invalid')) === 'true', 'indoklás nélkül nem rögzíthető');
    await p.fill('#nd-rationale', 'A 2. táblázat lábjegyzete: „values are mean (SE)”.');
    await p.fill('#nd-kb', 'V011');
    await p.selectOption('#nd-stage', 'S05');
    await p.click('#nd-submit');
    await p.waitForSelector('.toast.is-success');
    const nd = (await calls(p, 'POST', '/api/log/decision')).pop().body;
    check(nd.agent === 'user' && nd.stage === 'S05' && nd.kb_refs[0] === 'V011' && /lábjegyzete/.test(nd.rationale), 'POST /api/log/decision törzse');
    await p.waitForFunction(() => /SE-ként kezeljük/.test(document.getElementById('log-decisions').textContent));
    check(true, 'az új döntés a listában');
    await finish(o, 'napló');
  });

  await test('Kapuk: előzetes tiltás megnevezett okkal, GATE_BLOCKED (verseny) szó szerint, FAIL, FINAL + X-hibák', async () => {
    const o = await openPage('#/log?tab=checkpoint&stage=S05');
    const p = o.page;
    await screen(p, 'log');
    await p.waitForSelector('#log-checkpoints');
    check((await p.$eval('#log-gate-stage', (s) => s.value)) === 'S05', 'a szakasz az URL-ből (stage=S05)');
    await p.waitForFunction(() => document.getElementById('log-gate-submit').disabled === true);
    check((await txt(p, '#log-gate-pre')).indexOf('#15') >= 0 && (await p.getAttribute('#log-gate-submit', 'aria-describedby')) === 'log-gate-pre', 'S05 PASS: tiltva, az ok (#15) megnevezve és a gombhoz kötve');
    await p.selectOption('#log-gate-verdict', 'FAIL');
    check(!(await p.$eval('#log-gate-submit', (b) => b.disabled)), 'FAIL mindig rögzíthető');
    await p.fill('#log-gate-summary', 'SE/SD tisztázatlan');
    await p.click('#log-gate-submit');
    await p.waitForFunction(() => /Ellenőrzőpont rögzítve/.test(document.getElementById('log-gate-result').textContent));
    await p.waitForFunction(() => /SE\/SD tisztázatlan/.test(document.getElementById('log-checkpoints').textContent));
    check(true, 'FAIL rögzítve, a lista frissül');
    // S06: nincs blocker → nyitható; közben egy ágens blockert rögzít → a motor elutasít
    await p.selectOption('#log-gate-stage', 'S06');
    await p.selectOption('#log-gate-verdict', 'PASS');
    await p.waitForFunction(() => /nincs nyitott blocker/.test(document.getElementById('log-gate-pre').textContent));
    check(!(await p.$eval('#log-gate-submit', (b) => b.disabled)), 'S06 PASS: engedélyezett');
    await p.evaluate(() => window.MA.dev.process.addFinding({ severity: 'blocker', stage_id: 'S06', title: 'Ágens: RoB 2 hiányzik (Coetzee 1968)', agent: 'reviewer' }));
    await p.click('#log-gate-submit');
    await p.waitForSelector('#log-gate-blocked[role="alert"]');
    const blk = await txt(p, '#log-gate-blocked');
    check(blk.indexOf("1 nyitott 'blocker' megállapítás van — ennél a szakasznál (S06); PASS nem adható.") >= 0, 'GATE_BLOCKED: a motor üzenete szó szerint');
    check(blk.indexOf('RoB 2 hiányzik') >= 0 && (await p.$$('#log-gate-blocked a.btn')).length === 1, 'a blockerek listája ugrás-linkkel');
    check((await p.$$('#ma-alerts .toast[data-code="GATE_BLOCKED"]')).length === 0, 'nincs dupla jelzés (a hiba helyben, role=alert)');
    // FINAL: bármely blocker + X-hibák (project audit)
    await p.selectOption('#log-gate-stage', 'FINAL');
    await p.waitForFunction(() => /X001/.test(document.getElementById('log-gate-pre').textContent));
    check(await p.$eval('#log-gate-submit', (b) => b.disabled), 'FINAL: tiltva (blocker bármely szakaszban + X001)');
    // X-szabályok és tevékenység
    await p.click('#log-tab-audit');
    await p.waitForSelector('#log-audit li[data-code="X001"]');
    check((await txt(p, '#log-audit')).indexOf('python ma.py analyze --spec') >= 0 && (await p.$$('#log-audit li[data-code="X001"] button')).length >= 2, 'X-szabályok: javasolt parancs + másolás + Miért?');
    await p.click('#log-audit li[data-code="X001"] .item-actions .btn:not(.why-btn)');
    await p.waitForSelector('.toast.is-success');
    check((await p.evaluate(() => navigator.clipboard.readText())) === 'python ma.py analyze --spec 05_elemzes/specs/o1_primary.json', 'a parancs a vágólapra kerül');
    await p.click('#log-tab-activity');
    await p.waitForSelector('#log-chain');
    check((await txt(p, '#log-chain')).indexOf('lánc ép: 412') >= 0 && (await txt(p, '#log-activity')).indexOf('analyze.commit') >= 0, 'tevékenységnapló: lánc ép, bejegyzések');
    await p.click('#log-tab-grade');
    await p.waitForSelector('#log-grades');
    check((await txt(p, '#log-grades')).indexOf('alacsony') >= 0 && (await txt(p, '#log-grades')).indexOf('RR 0,49 [0,33; 0,73]') >= 0, 'GRADE-napló');
    await p.click('#log-tab-run');
    await p.waitForSelector('#log-runs');
    check((await txt(p, '#log-runs')).indexOf('9f3abbbbbbbb') >= 0, 'futtatások: rövid data-sha256');
    await finish(o, 'kapuk');
  });

  await test('KB-kereső: Ctrl+K bárhonnan, debounce, körök, helyi forrás címke, kiemelés <mark>-kal, tétel-modális, ?kb=', async () => {
    const o = await openPage('#/overview');
    const p = o.page;
    await screen(p, 'overview');
    await p.keyboard.press('Control+k');
    await screen(p, 'log');
    await p.waitForFunction(() => document.activeElement && document.activeElement.id === 'kb-q');
    check(true, 'Ctrl+K → Napló, a fókusz a keresőn');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.type('#kb-q', 'predikciós intervallum', { delay: 15 });
    await p.waitForSelector('#kb-results');
    const s = await calls(p, 'GET', '/api/kb/search');
    check(s.length === 1 && s[0].query.q === 'predikciós intervallum' && s[0].query.scope === 'rule', 'debounce: egy kérés a teljes kifejezéssel (scope=rule)');
    check((await p.$$('#kb-results li[data-scope="rule"]')).length >= 3 && (await txt(p, '#kb-results')).indexOf('D-S08-020') >= 0, 'szabály-találatok (valódi KB)');
    await p.check('#kb-scope-chunk');
    await p.waitForSelector('#kb-results li[data-scope="chunk"]');
    const ch = await txt(p, '#kb-results');
    check(ch.indexOf('helyi forrás — nem exportálható') >= 0, 'teljes szöveg: „helyi forrás — nem exportálható” címke');
    check(ch.indexOf('<script>alert(1)</script>') >= 0 && (await p.$$('#kb-results script')).length === 0, 'a szövegrész HTML-je szövegként jelenik meg');
    check((await p.$$('#kb-results mark')).length === 1 && (await txt(p, '#kb-results mark')) === 'predikciós intervallum', 'a találat kiemelése <mark> (a motor [ ] jelei)');
    await p.click('#kb-results li[data-scope="chunk"] .kb-open');
    await p.waitForSelector('[role="dialog"] .kb-fields');
    check((await txt(p, '[role="dialog"]')).indexOf('helyi forrás — nem exportálható') >= 0 && (await txt(p, '[role="dialog"]')).indexOf('SZINTETIKUS') >= 0, 'szövegrész-tétel: helyi forrás címke');
    const item = await calls(p, 'GET', '/api/kb/item/cochrane_handbook%23412');
    check(item.length === 1, 'a # kódolva megy (cochrane_handbook%23412)');
    await p.keyboard.press('Escape');
    check(await p.evaluate(() => document.activeElement.classList.contains('kb-open')), 'Esc → a fókusz vissza a megnyitó gombra');
    await p.check('#kb-scope-knowledge');
    await p.waitForSelector('#kb-results li[data-scope="knowledge"]');
    check((await txt(p, '#kb-results')).indexOf('K-SIM24-059') >= 0, 'tudás-találatok');
    await p.goto(srv.base + '/?fixtures=1#/log?kb=V006');
    await p.waitForSelector('html[data-ready="1"]');
    await p.waitForSelector('[role="dialog"] .kb-fields');
    check((await txt(p, '[role="dialog"]')).indexOf('Érvénytelen eseményszám') >= 0, '?kb=V006 → a KB-tétel megnyílik (Kinyerés → Napló link)');
    await p.keyboard.press('Escape');
    await finish(o, 'kb');
  });

  // ========== 5. Képességek
  await test('Képességek: állapotok szimbólummal és szöveggel, teendők, mátrix, adatvédelem, újraszondázás', async () => {
    const o = await openPage('#/capabilities');
    const p = o.page;
    await screen(p, 'capabilities');
    const st = await p.evaluate(() => Array.from(document.querySelectorAll('#cap-table tbody tr')).map((tr) => tr.dataset.plugin + ':' + tr.querySelector('.cap-state').dataset.state));
    check(st.join(',') === 'metaelemzes:ok,validator:legacy,figure-forge:unusable,composer:ok,presubmit:absent', 'négy állapot (ok, legacy, unusable, absent): ' + st.join(','));
    const tb = await txt(p, '#cap-table');
    check(tb.indexOf('◐') >= 0 && tb.indexOf('telepítve, de nem használható') >= 0 && tb.indexOf('○') >= 0 && tb.indexOf('nincs telepítve') >= 0, 'szimbólum ÉS szöveg (a szín nem egyedüli jelölés)');
    check(tb.indexOf('matplotlib hiányzik') >= 0 && tb.indexOf('FIGURE_FORGE_PYTHON') >= 0 && tb.indexOf('claude plugin install presubmit@szk-plugins') >= 0, 'problémák és teendők szó szerint');
    check(tb.indexOf('önteszt 3268/3268') >= 0 && tb.indexOf('őrök: H1 H2 H3 H4') >= 0, 'motor-önteszt, validator-őrök');
    check((await p.$$('#cap-matrix tbody tr')).length >= 10, 'funkció-mátrix (5.2)');
    const pr = await txt(p, '#cap-privacy');
    check(pr.indexOf('A — publikált aggregált') >= 0 && pr.indexOf('onedrive') >= 0, 'adatvédelem: osztály, felhőszinkron');
    check((await txt(p, '#cap-recs')).indexOf('OneDrive szinkronizálja') >= 0, 'javaslatok a szerver szövegével');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#cap-refresh');
    await p.waitForSelector('.toast.is-success');
    check((await calls(p, 'POST', '/api/capabilities/refresh')).length === 1 && (await calls(p, 'GET', '/api/capabilities')).length === 1, 'POST …/refresh, majd a keret újratöltése');
    await screen(p, 'capabilities');
    check((await txt(p, '#cap-status')).indexOf('2026-10-05 09:30') >= 0, 'az új állapot ideje');
    await finish(o, 'képességek');
  });

  // ========== 6. Export
  await test('Export: adatosztály-alapértékek, tiltott elemek, audit-ZIP, pillanatkép tudomásulvétellel, Artifact-tilalom', async () => {
    const o = await openPage('#/export');
    const p = o.page;
    await screen(p, 'export');
    await p.waitForFunction(() => /412/.test(document.getElementById('exp-chain').textContent));
    check(await p.$eval('#ex-include-data_tables', (c) => c.checked && !c.disabled), 'A osztály: adattáblák alapból bent');
    check(await p.$eval('#ex-include-private', (c) => c.disabled && !c.checked) && await p.$eval('#ex-include-pdfs', (c) => c.disabled && !c.checked), '_privat/ és PDF: tiltva');
    check(await p.$eval('#exp-snapshot', (b) => b.disabled), 'a pillanatkép gomb tudomásulvétel nélkül tiltva');
    const warn = await txt(p, '#exp-snap-warn');
    check(warn.indexOf('Claude Artifact') >= 0 && warn.indexOf('ne töltsd fel') >= 0, 'kifejezett figyelmeztetés: ne töltsd fel, Claude Artifactként sem');
    await p.uncheck('#ex-redact-quotes');
    await p.click('#exp-audit');
    await p.waitForSelector('#exp-audit-result');
    const a = (await calls(p, 'POST', '/api/export/audit'))[0].body;
    check(a.include.data_tables === true && a.include.activity === true && a.redact.quotes === false && a.redact.abs_paths === true && !('private' in a.include), 'audit-kérés: include/redact a kapcsolók szerint');
    const ar = await txt(p, '#exp-audit-result');
    check(ar.indexOf('07_ellenorzes/audit/2026-10-05/bcg-oltas-audit.zip') >= 0 && ar.indexOf('e3b4') >= 0 && ar.indexOf('_privat/**') >= 0, 'eredmény: út, sha256, kizárások');
    await p.check('#exp-ack');
    check(!(await p.$eval('#exp-snapshot', (b) => b.disabled)), 'tudomásulvétel után engedélyezett');
    await p.click('#exp-snapshot');
    await p.waitForSelector('#exp-snapshot-result');
    check((await txt(p, '#exp-snapshot-result')).indexOf('Claude Artifact') >= 0, 'az eredmény mellett is ott a tilalom');
    // B és C osztály alapértékei
    await p.evaluate(() => window.MA.store.set('privacy', Object.assign({}, window.MA.store.get('privacy'), { data_class: 'B' })));
    await p.click('#nav-overview');
    await screen(p, 'overview');
    await p.click('#nav-export');
    await screen(p, 'export');
    check(await p.$eval('#ex-include-data_tables', (c) => !c.checked && !c.disabled) && await p.$eval('#ex-redact-assessors', (c) => c.checked), 'B osztály: adattáblák ki, értékelők monogrammal');
    await p.evaluate(() => window.MA.store.set('privacy', Object.assign({}, window.MA.store.get('privacy'), { data_class: 'C' })));
    await p.click('#nav-overview');
    await screen(p, 'overview');
    await p.click('#nav-export');
    await screen(p, 'export');
    check(await p.$eval('#ex-include-data_tables', (c) => !c.checked && c.disabled), 'C osztály: adattábla tiltva');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#exp-audit');
    await p.waitForSelector('#exp-audit-result');
    check(!('data_tables' in (await calls(p, 'POST', '/api/export/audit'))[0].body.include), 'C osztály: a tiltott adattábla nem is kérhető');
    await finish(o, 'export');
  });

  // ========== 7. nyelv, téma, minden képernyő
  await test('HU ↔ EN és sötét téma minden folyamat-képernyőn; nincs hiányzó kulcs, nincs konzolhiba', async () => {
    const o = await openPage('#/overview');
    const p = o.page;
    await p.click('#lang-switch [data-lang="en"]');
    const expect = { overview: 'Plain', prisma: 'Flow diagram', studies: 'Study ↔ report map', log: 'Knowledge-base search', capabilities: 'Capabilities (engine and plugins)', export: 'Audit bundle' };
    for (const id of Object.keys(expect)) {
      await p.evaluate((x) => window.MA.app.navigate(x), id);
      await screen(p, id);
      await p.waitForTimeout(150);
      const t = await txt(p, '#screen-root');
      check(id === 'overview' ? /plain-language/.test(t) : t.indexOf(expect[id]) >= 0, 'EN: ' + id);
    }
    await p.evaluate(() => window.MA.app.navigate('prisma'));
    await screen(p, 'prisma');
    check((await txt(p, '.pf-svg')).indexOf('Records screened (B): 325') >= 0, 'EN: a folyamatábra címkéi angolul, a számok változatlanok');
    await p.click('#pf-list li[data-code="P007"] .why-btn');
    await p.waitForSelector('.why-pop');
    check((await txt(p, '.why-pop')).indexOf('What to do?') >= 0, 'EN: „Why?” buborék');
    await p.keyboard.press('Escape');
    const before = await p.evaluate(() => getComputedStyle(document.querySelector('.pf-box rect')).fill);
    await p.click('#theme-toggle');
    const theme = await p.getAttribute('html', 'data-theme');
    const after = await p.evaluate(() => getComputedStyle(document.querySelector('.pf-box rect')).fill);
    check(before !== after, 'téma (' + theme + '): a folyamatábra színei a tokenekből követik (' + before + ' → ' + after + ')');
    await p.click('#lang-switch [data-lang="hu"]');
    await screen(p, 'prisma');
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
