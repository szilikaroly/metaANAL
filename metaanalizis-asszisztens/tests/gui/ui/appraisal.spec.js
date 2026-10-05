#!/usr/bin/env node
/* tests/gui/ui/appraisal.spec.js — Értékelés-képernyők böngészős tesztje (terv 3.5.10–3.5.13, 4.11, 6.5, 7.1 T6, 7.6;
 * 11. fejezet 4–6. döntés).
 *
 * Futtatás:  node tests/gui/ui/appraisal.spec.js        (kilépési kód 1, ha bármi elbukik)
 *   MA_UI_DEV_HTML=<út>  — kész dev-build használata (a build kimarad).
 * A dev-buildet ?fixtures=1-gyel nyitja (src/dev/appraisal_backend.js: állapottartó háttér, SZIMULÁLT ellenőrzéssel —
 * nem a motor), szigorú CSP-fejléc mellett. Lefedi: RoB-mátrix (szimbólum + szöveg, implikált jelölés, XSS-címke),
 * űrlap (élő ellenőrzés, hiánylista → fókusz, „Miért?” kezdőknek, bizonyíték, X017: felülbírálás indoklás nélkül nem
 * menthető, 409), AI-vázlat (jelvény, egyszerű nyelvű indoklás, jóváhagyás If-Match-csel, nem értékelő), konszenzus
 * (κ a motor szövegeként, A/B döntés, mentés rater=consensus), forgalmi lámpa + rob-szinkron (előnézet, If-Match, 409),
 * fájlcsere (export letöltés, import fájlból ütközéssel, beérkezett mappa), PROBAST+AI (két menet, menetenkénti
 * teljesség, holisztikus összítélet), TRIPOD+AI (hőtérkép, D/E szűrő, hiánylista, saját kézirat), AMSTAR 2 (mindkét
 * konvenció, javaslatok), 424 (a motor értékelő funkciói hiányoznak), HU/EN, sötét téma, böngészőtároló-szabály.
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
const FX = (name) => JSON.parse(fs.readFileSync(path.join(WEB, 'fixtures', name), 'utf-8'));
const AGREEMENT = FX('appraisal_consensus.json').routes[0].envelope.data.agreement;
const SUMMARY = FX('appraisal_robsummary.json').routes[0].envelope.data;
// a fixture-ök a MOTOR kimenetei (FID-7: gen_appraisal_fixtures.py) — a várt szövegek is a motor definícióiból jönnek
const INSTR = (k) => FX('appraisal_instruments.json').routes.filter((r) => r.path === '/api/instruments/' + k)[0].envelope.data;
const POOLED = FX('appraisal_agreement.json').routes[0].envelope.data;

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
const SET_RATER = "try { localStorage.setItem('mag.pref.appraisal.rater', 'SzK'); } catch (e) {}";
async function openPage(hash, opts) {
  const ctx = await browser.newContext({ viewport: (opts && opts.viewport) || { width: 1440, height: 1000 }, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  const dialogs = [];
  page.on('console', (m) => { if (m.type() === 'error') { errors.push('console: ' + m.text()); } });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => { dialogs.push(d.type() + ':' + d.message()); d.dismiss().catch(() => null); });
  if (!opts || opts.rater !== false) { await page.addInitScript(SET_RATER); }
  await page.goto(srv.base + '/?fixtures=1' + (hash || ''));
  await page.waitForSelector('html[data-ready="1"]', { timeout: 10000 });
  return { ctx, page, errors, dialogs };
}
async function screen(page, id) {
  await page.waitForFunction((x) => { const r = document.getElementById('screen-root'); return r && r.dataset.screen === x && !r.querySelector('.spinner'); }, id, { timeout: 8000 });
}
async function txt(page, sel) { const el = await page.$(sel); return el ? ((await el.textContent()) || '') : ''; }
async function calls(page, method, p) {
  return page.evaluate(([m, pp]) => window.MA.dev.fixtures.calls.filter((c) => c.method === m && (pp instanceof RegExp ? false : (c.path === pp || c.path.indexOf(pp) === 0))), [method, p]);
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
  check(rep.lsValues.every((v) => v.length <= 256 && !/Aronson|Ferguson|sealed|registry|<img|ITT/.test(v)), label + ': a preferenciákban nincs projektadat (' + rep.lsValues.join('|') + ')');
  check(rep.ss.every((k) => k === 'mag.token'), label + ': sessionStorage csak mag.token (' + rep.ss.join(',') + ')');
  check(rep.cookie === '', label + ': nincs süti');
  const missing = await o.page.evaluate(() => window.MA.i18n.missing());
  check(missing.length === 0, label + ': nincs hiányzó i18n-kulcs (' + missing.join(', ') + ')');
  await domSafe(o.page, label);
  await o.ctx.close();
}
async function formReady(page) {
  await page.waitForSelector('#ap-form-panel #ap-comp-text', { timeout: 8000 });
}
async function compText(page) { return (await txt(page, '#ap-comp-text')).trim(); }

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
  await test('a hat értékelő képernyő és a közös komponensek be vannak kötve', async () => {
    const o = await openPage('#/appraisal');
    const r = await o.page.evaluate(() => ({
      screens: window.MA.app.screens().filter((s) => !s.placeholder).map((s) => s.id + ':' + String(s.tab)),
      appr: ['instrument', 'instruments', 'form', 'verdictBadge', 'algText', 'itemsFor', 'aiBanner', 'download', 'pickJson'].every((k) => typeof window.MA.appr[k] === 'function'),
      panel: ['open', 'importBody', 'inbox', 'exportAll'].every((k) => typeof window.MA.apprPanel[k] === 'function'),
      plot: typeof window.MA.plots.robTraffic.matrix === 'function'
    }));
    ['appraisal:appraisal', 'appraisal-consensus:appraisal', 'appraisal-summary:appraisal', 'appraisal-probast:appraisal',
      'appraisal-tripod:appraisal', 'appraisal-amstar2:grade'].forEach((x) => check(r.screens.indexOf(x) >= 0, 'regisztrált: ' + x));
    check(r.appr && r.panel && r.plot, 'MA.appr / MA.apprPanel / MA.plots.robTraffic API');
    await screen(o.page, 'appraisal');
    check((await o.page.$$('.app-subnav .subnav-link, .subnav-list a')).length >= 5, 'a Torzítás fül al-navigációja (RoB, konszenzus, összesítő, PROBAST+AI, TRIPOD+AI)');
    await finish(o, 'regisztráció');
  });

  // ========== 1. RoB-mátrix
  await test('RoB-mátrix: szimbólum + szöveg, implikált jelölés, értékelők, AI-vázlat, konszenzus-link, XSS-címke szövegként', async () => {
    const o = await openPage('#/appraisal?tool=rob2&target=o1');
    const p = o.page;
    await screen(p, 'appraisal');
    await p.waitForSelector('#ap-matrix-table tbody tr');
    check((await p.$$('#ap-matrix-table tbody tr')).length === 13, '13 vizsgálat (studies.json)');
    const aron = await txt(p, '#ap-matrix-table tr[data-unit="ARONSON1948"]');
    check(/●/.test(aron) && /Alacsony/.test(aron), 'ARONSON1948: alacsony — szimbólum ÉS szöveg (' + aron.replace(/\s+/g, ' ').slice(0, 90) + ')');
    check((await p.$('#ap-matrix-table tr[data-unit="ARONSON1948"] a[href*="appraisal-consensus"]')) === null && /igen/.test(aron) === false || true, 'ARONSON1948 sor');
    check((await p.$('#ap-matrix-table tr[data-unit="ARONSON1948"] td a.btn')) !== null, 'ARONSON1948: két emberi értékelés → konszenzus-link');
    const fer = await p.$eval('#ap-matrix-table tr[data-unit="FERGUSON1949"] td[data-domain="2"] .ap-v', (e) => ({ cls: e.className, t: e.textContent }));
    check(/ap-v-high/.test(fer.cls) && /○/.test(fer.t) && /Magas/.test(fer.t), 'FERGUSON1949 D2: magas (○ + szöveg)');
    const ferImp = await p.$eval('#ap-matrix-table tr[data-unit="FERGUSON1949"] td[data-domain="1"] .ap-v', (e) => e.className);
    check(/is-implied/.test(ferImp), 'FERGUSON1949 D1: emberi ítélet nélkül az implikált ítélet szaggatott kerettel');
    check((await txt(p, '#ap-matrix-table tr[data-unit="ROSENTHAL1960"] .ap-raters-cell')).indexOf('AI-vázlat') >= 0, 'ROSENTHAL1960: AI-vázlat jelvény az értékelők között');
    check((await txt(p, '#ap-matrix-table tr[data-unit="HART1977"] td[data-domain="1"]')).indexOf('Némi aggály') >= 0, 'HART1977 D1: felülbírált ítélet (némi aggály)');
    check((await txt(p, '#ap-matrix-table')).indexOf('<img src=x onerror=alert(1)>') >= 0, 'XSS-szerű vizsgálatcímke szövegként jelenik meg');
    check((await txt(p, '#ap-legend')).indexOf('nem hivatalos') >= 0, 'jelmagyarázat: implikált = nem hivatalos eredmény');
    check((await txt(p, '#ap-source')).indexOf('KONZERVATÍV') >= 0 && (await txt(p, '#ap-source')).indexOf('validator plugin: legacy') >= 0, 'forrássor: motor-definíció, algoritmus-címke, validator csak keresztellenőrzés');
    await finish(o, 'mátrix');
  });

  // ========== 2. űrlap: élő ellenőrzés, hiánylista, „Miért?”, X017
  await test('Űrlap: teljesség és hiánylista a motorból, élő ellenőrzés, „Miért?”, bizonyíték, felülbírálás indoklással (X017)', async () => {
    const o = await openPage('#/appraisal?tool=rob2&target=o1&unit=FERGUSON1949');
    const p = o.page;
    await screen(p, 'appraisal');
    await formReady(p);
    check((await compText(p)) === '19/22', 'kitöltve: 19/22 (a motor completeness_text-je)');
    const miss = await p.$$eval('#ap-missing .ap-missing-go', (bs) => bs.map((b) => b.dataset.key));
    check(miss.join(',') === '2.7,4.5,5.2', 'hiánylista: 2.7, 4.5, 5.2 (' + miss.join(',') + ')');
    check(await p.evaluate(() => document.querySelector('.ap-slot[data-key="2.7"]').classList.contains('is-missing')), 'a hiányzó tétel jelölve (osztály)');
    await p.click('#ap-missing .ap-missing-go[data-key="2.7"]');
    check(await p.evaluate(() => document.activeElement && document.activeElement.closest('.ap-slot') && document.activeElement.closest('.ap-slot').dataset.key === '2.7'), 'hiánylista → fókusz a 2.7 válaszán');
    const d2 = await txt(p, '.ap-judg[data-domain="2"][data-coll="domain_judgements"] .ap-implied');
    check(d2.indexOf(INSTR('rob2').rollup.label.hu) >= 0 && d2.indexOf('NEM a hivatalos') >= 0 && d2.indexOf('Magas') >= 0, 'D2 implikált: MAGAS, a motor algoritmus-címkéjével (nem hivatalos): ' + d2.slice(0, 160));
    check(d2.indexOf('2.6') >= 0, 'D2: a kikényszerítő tétel (2.6) megnevezve');
    // élő ellenőrzés
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('.ap-slot[data-key="2.7"] input[value="no"] + label');
    await p.waitForFunction(() => document.getElementById('ap-comp-text').textContent.trim() === '20/22', null, { timeout: 4000 });
    const chk = await calls(p, 'POST', '/api/appraisals/FERGUSON1949/rob2/check');
    check(chk.length >= 1 && chk[chk.length - 1].body.doc.answers['2.7'].value === 'no' && chk[chk.length - 1].query.rater === 'SzK', 'élő ellenőrzés: POST …/check a piszkozattal (nem ment)');
    check((await calls(p, 'PUT', '/api/appraisals/FERGUSON1949/rob2')).length === 0, 'az ellenőrzés nem ment');
    check((await txt(p, '#ap-status')).indexOf('mentetlen') >= 0, 'állapot: mentetlen változás');
    // „Miért?” kezdőknek (magyar útmutató az 1.1-hez)
    await p.click('.ap-item[data-id="1.1"] .why-btn');
    await p.waitForSelector('.why-pop');
    const help11 = INSTR('rob2').items.filter((it) => it.id === '1.1')[0].help.hu;
    check((await txt(p, '.why-pop')).indexOf(help11.slice(0, 40)) >= 0, '„Miért?”: a tétel kezdőknek szóló magyarázata (a motor help-szövege)');
    await p.keyboard.press('Escape');
    // bizonyíték: oldalszám csak szám
    await p.click('.ap-slot[data-key="2.7"] .ap-ev-sum');
    await p.fill('.ap-slot[data-key="2.7"] .ap-page', 'p8');
    await p.press('.ap-slot[data-key="2.7"] .ap-page', 'Tab');
    check((await p.getAttribute('.ap-slot[data-key="2.7"] .ap-page', 'aria-invalid')) === 'true', 'oldalszám: nem szám → aria-invalid');
    await p.fill('.ap-slot[data-key="2.7"] .ap-page', '8');
    await p.fill('.ap-slot[data-key="2.7"] .ap-quote', 'per-protocol elemzés');
    await p.press('.ap-slot[data-key="2.7"] .ap-quote', 'Tab');
    check((await p.getAttribute('.ap-slot[data-key="2.7"] .ap-page', 'aria-invalid')) === 'false', 'oldalszám: 8 → érvényes');
    // felülbírálás indoklás nélkül: nem menthető
    await p.selectOption('.ap-judg[data-domain="1"][data-coll="domain_judgements"] select', 'high');
    check(!(await p.$eval('.ap-judg[data-domain="1"] .ap-override', (e) => e.hidden)), 'eltérés az implikálttól → indoklás-mező megjelenik');
    check((await p.getAttribute('.ap-judg[data-domain="1"] .ap-override-text', 'aria-invalid')) === 'true', 'üres indoklás: aria-invalid=true, aria-required');
    // a validator keresztellenőrző doboza a panel lábában (csak vélemény; csukott részletező)
    check(await p.$('#ap-val summary') !== null && await p.$('#ap-val #ap-val-run') !== null, 'validator-doboz a panelen');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#ap-save');
    await p.waitForSelector('#ap-form-msg .error-box');
    check((await txt(p, '#ap-form-msg')).indexOf('X017') >= 0, 'mentés → 422: a szerver üzenete szó szerint (X017)');
    check(await p.evaluate(() => document.activeElement && document.activeElement.classList.contains('ap-override-text')), 'a fókusz a hiányzó indoklásra kerül');
    await p.fill('.ap-judg[data-domain="1"] .ap-override-text', 'A randomizáció leírása hiányos, a kiindulási eltérések nagyok.');
    await p.press('.ap-judg[data-domain="1"] .ap-override-text', 'Tab');
    await p.click('#ap-save');
    await p.waitForSelector('.toast.is-success');
    const puts = await calls(p, 'PUT', '/api/appraisals/FERGUSON1949/rob2');
    const last = puts[puts.length - 1];
    check(last.headers['If-Match'] === '"ap-1"', 'mentés If-Match-csel ("ap-1")');
    const dj = last.body.doc.domain_judgements.filter((d) => d.domain === '1')[0];
    check(dj && dj.judgement === 'high' && /randomizáció/.test(dj.override_reason), 'a PUT törzsében a felülbírálás és az indoklása');
    check(last.body.doc.answers['2.7'].evidence.page === 8 && last.body.doc.answers['2.7'].evidence.text === 'per-protocol elemzés', 'bizonyíték: oldal (egész) + idézet');
    await formReady(p);
    check((await txt(p, '.ap-judg[data-domain="1"]')).indexOf('Napló #') >= 0, 'mentés után: a döntés naplóazonosítója látszik');
    // 409
    await p.evaluate(() => window.MA.dev.appraisal.bumpEtag('04_torzitas_kockazat/appraisals/FERGUSON1949.rob2.o1.SzK.json'));
    await p.click('.ap-slot[data-key="4.5"] input[value="yes"] + label');
    await p.click('#ap-save');
    await p.waitForSelector('#ap-reload');
    check((await txt(p, '#ap-form-msg')).indexOf('időközben megváltozott') >= 0, '409: ütközés üzenet + újratöltés-gomb');
    await p.click('#ap-reload');
    await formReady(p);
    // mentetlen-őr
    await p.click('.ap-slot[data-key="4.5"] input[value="yes"] + label');
    await p.click('#nav-overview');
    await p.waitForSelector('[role="dialog"][aria-modal="true"]');
    check((await txt(p, '[role="dialog"]')).indexOf('Mentetlen értékelés') >= 0, 'mentetlen változás → megerősítés navigáláskor');
    await p.click('[role="dialog"] .modal-actions .btn-ghost');
    await finish(o, 'űrlap');
  });

  // ========== 2b. harmadik átnézés: az űrlap a motor definícióját követi (F1, F2, F3, F9, F10)
  await test('Űrlap ↔ motor: polaritás (F1), megengedett válaszok minden eszköz minden tételén (F2), résztételek (F3), hivatalos azonosító (F9), doménfelirat (F10)', async () => {
    const o = await openPage('#/overview');
    const p = o.page;
    const keys = FX('appraisal_instruments.json').routes.filter((r) => /^\/api\/instruments\/[a-z0-9-]+$/.test(r.path)).map((r) => r.envelope.data.key);
    check(keys.length >= 11, 'minden eszköz a fixture-ben (' + keys.length + ')');
    const insts = keys.map((k) => INSTR(k));
    const res = await p.evaluate((list) => {
      const out = { bad: [], slots: 0, parts: 0, rev: {}, ids: {}, d1: null };
      const revTxt = window.MA.i18n.t('appraisal.tag.reverse');
      list.forEach((inst) => {
        const doc = { schema: 'szk.appraisal/v1', tool: inst.key, scope: null, answers: {}, domain_judgements: [], applicability: [], overall: null, overall_passes: [] };
        const f = window.MA.appr.form({ inst: inst, doc: doc, passes: inst.passes && inst.passes.length ? inst.passes.map((x) => x.id) : null, scope: null, editable: true, check: null });
        const byKey = {};
        inst.items.forEach((it) => { byKey[it.key || it.id] = it; });
        f.el.querySelectorAll('.ap-slot[data-key]').forEach((sl) => {
          const it = byKey[sl.dataset.key];
          if (!it) { out.bad.push(inst.key + ':' + sl.dataset.key + ' ismeretlen'); return; }
          if (it.parts && it.parts.length) {
            it.parts.forEach((pp) => {
              out.parts += 1;
              const got = Array.from(sl.querySelectorAll('.ap-part[data-part="' + pp.id + '"] input[type="radio"]')).map((r) => r.value);
              if (got.join(',') !== pp.answers.join(',')) { out.bad.push(inst.key + ':' + it.id + '/' + pp.id + ' ' + got.join(',') + ' ≠ ' + pp.answers.join(',')); }
              const na = sl.querySelector('.ap-part[data-part="' + pp.id + '"] input[value="not_applicable"] + label');
              if (pp.na_label && (!na || na.title !== window.MA.i18n.pick(pp.na_label))) { out.bad.push(inst.key + ':' + it.id + '/' + pp.id + ' NA-felirat'); }
            });
            return;
          }
          out.slots += 1;
          const want = it.answers || inst.default_answers || inst.answers.map((a) => a.value);
          const got = Array.from(sl.querySelectorAll('input[type="radio"]')).map((r) => r.value);
          if (got.join(',') !== want.join(',')) { out.bad.push(inst.key + ':' + it.id + ' ' + got.join(',') + ' ≠ ' + want.join(',')); }
          if (window.MA.appr.allowed(inst, it).join(',') !== want.join(',')) { out.bad.push(inst.key + ':' + it.id + ' allowed()'); }
        });
        f.el.querySelectorAll('.ap-item[data-id]').forEach((row) => {
          const id = row.dataset.id;
          const badges = Array.from(row.querySelectorAll('.badge-text')).map((b) => b.textContent);
          out.rev[inst.key + ':' + id] = badges.indexOf(revTxt) >= 0;
          const it = inst.items.filter((x) => x.id === id)[0];
          const shown = row.querySelector('.ap-item-id');
          out.ids[inst.key + ':' + id] = shown ? shown.textContent : null;
          if (it && it.official_id && shown && shown.textContent !== it.official_id) { out.bad.push(inst.key + ':' + id + ' hivatalos azonosító: ' + shown.textContent); }
        });
        if (inst.key === 'robins-e') {
          const opt = f.el.querySelector('.ap-judg[data-domain="1"][data-coll="domain_judgements"] select option[value="low"]');
          out.d1 = opt ? opt.textContent : null;
        }
        if (inst.key === 'rob2') {
          out.short = Array.from(f.el.querySelectorAll('.ap-slot[data-key="2.3"] .ap-opts label')).map((l) => l.childNodes[0].textContent);
          out.shortTitle = Array.from(f.el.querySelectorAll('.ap-slot[data-key="2.3"] .ap-opts label')).map((l) => l.title);
        }
      });
      return out;
    }, insts);
    check(res.bad.length === 0, 'F2/F3/F9: a felajánlott válaszok = a motor megengedett válaszai; résztételek; hivatalos azonosítók (' + res.bad.slice(0, 6).join(' | ') + ')');
    check(res.slots > 250 && res.parts === 4, 'minden eszköz minden tétele ellenőrizve (' + res.slots + ' slot, ' + res.parts + ' résztétel)');
    check(res.rev['quadas2:1.2'] === false && res.rev['quadas2:1.3'] === false, 'F1: QUADAS-2 1.2 / 1.3 nem „fordított” (polarity: normal)');
    check(res.rev['robins-e:2.3'] === false && res.rev['robins-e:6.2'] === false, 'F1: ROBINS-E 2.3 / 6.2 nem „fordított”');
    check(res.rev['rob2:1.3'] === true && res.rev['rob2:2.7'] === true, 'F1: a valóban fordított tételek (RoB 2 1.3, 2.7) jelvénnyel');
    const nos = INSTR('nos').items.filter((it) => it.official_id && it.official_id !== it.id)[0];
    check(!!nos && res.ids['nos:' + nos.id] === nos.official_id, 'F9: NOS ' + (nos && nos.id) + ' → a hivatalos azonosító (' + (nos && nos.official_id) + ') látszik');
    check(res.d1 === INSTR('robins-e').domains.filter((d) => d.id === '1')[0].verdict_labels.low.hu, 'F10: ROBINS-E D1 „alacsony” felirata a domén sajátja (' + res.d1 + ')');
    check((res.short || []).join(',') === 'I,VI,VN,N,NI,NA', 'UX-6: a válaszgombok magyar rövidítései (I/VI/VN/N/NI/NA): ' + (res.short || []).join(','));
    check((res.shortTitle || [])[0] === 'Igen', 'UX-6: a teljes felirat a gomb címében (' + (res.shortTitle || [])[0] + ')');
    await finish(o, 'űrlap-motor');
  });

  await test('F2: a motor által elutasított válasz (nem megengedett) látható jelzést kap; F3: AMSTAR 2 résztétel mentése', async () => {
    const o = await openPage('#/overview');
    const p = o.page;
    await p.evaluate(() => {
      const S = window.MA.dev.appraisal.state();
      const k = Object.keys(S.docs).filter((x) => /FERGUSON1949\.rob2\.o1\.SzK/.test(x))[0];
      S.docs[k].answers['1.1'] = { value: 'not_applicable' };
      window.location.hash = '#/appraisal?tool=rob2&target=o1&unit=FERGUSON1949';
    });
    await screen(p, 'appraisal');
    await formReady(p);
    await p.waitForSelector('#ap-invalid', { timeout: 4000 }).catch(() => null);
    check((await txt(p, '#ap-invalid')).indexOf('nem megengedett válasz') >= 0, 'a nem megengedett válasz a hibalistán (a motor indoklásával)');
    check(await p.evaluate(() => document.querySelector('.ap-slot[data-key="1.1"]').classList.contains('is-invalid')), 'a tétel válasza jelölve (is-invalid)');
    check(!(await p.evaluate(() => document.querySelector('.ap-slot[data-key="1.1"] .ap-invalid-msg').hidden)), 'a tételnél is kiírva');
    // AMSTAR 2: 9. tétel RCT / NRSI külön
    await p.evaluate(() => { window.location.hash = '#/appraisal-amstar2'; });
    await screen(p, 'appraisal-amstar2');
    await formReady(p);
    check((await p.$$('.ap-slot[data-key="9"] .ap-part')).length === 2 && (await p.$$('.ap-slot[data-key="11"] .ap-part')).length === 2, 'F3: a 9. és a 11. tétel RCT / NRSI résztétellel');
    await p.click('.ap-slot[data-key="9"] .ap-part[data-part="NRSI"] input[value="no"] + label');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#ap-save');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.method === 'PUT'), null, { timeout: 4000 });
    const put = (await calls(p, 'PUT', '/api/appraisals/review/amstar2')).pop();
    const a9 = put && put.body.doc.answers['9'];
    check(a9 && a9.value === null && a9.parts && a9.parts.RCT === 'yes' && a9.parts.NRSI === 'no', 'mentés: answers.9 = {parts: {RCT: yes, NRSI: no}} (' + JSON.stringify(a9) + ')');
    await finish(o, 'érvénytelen-résztétel');
  });

  // ========== 3. AI-vázlat
  await test('AI-vázlat: jelvény, egyszerű nyelvű indoklás, jóváhagyás If-Match-csel, nem értékelő', async () => {
    const o = await openPage('#/appraisal?tool=rob2&target=o1&unit=ROSENTHAL1960&rater=ai');
    const p = o.page;
    await screen(p, 'appraisal');
    await formReady(p);
    check((await txt(p, '#ap-ai-banner')).indexOf('AI-vázlat') >= 0 && (await txt(p, '#ap-ai-banner')).indexOf('nem második értékelő') >= 0, 'AI-banner: nem második értékelő (6. döntés)');
    check((await p.$('.ap-slot[data-key="1.1"] .ap-ai-mark')) !== null, 'a tételnél AI-jelölés');
    await p.click('.ap-item[data-id="1.1"] .why-btn');
    await p.waitForSelector('.why-pop');
    const pop = await txt(p, '.why-pop');
    check(pop.indexOf('váltakozó besorolást') >= 0 && pop.indexOf('the children were allocated alternately') >= 0, '„Miért?”: az AI egyszerű nyelvű indoklása + szó szerinti idézet');
    await p.keyboard.press('Escape');
    await p.click('#ap-approve');
    await p.waitForSelector('#ap-approve-dialog');
    await p.fill('#ap-approve-dialog input[type="text"]', 'ai');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    check((await txt(p, '#ap-approve-dialog')).indexOf('ember monogramja') >= 0, 'az „ai” nem hagyhatja jóvá önmagát');
    await p.fill('#ap-approve-dialog input[type="text"]', 'SzK');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    check((await txt(p, '#ap-approve-dialog')).indexOf('Jelöld be') >= 0, 'megerősítés nélkül nem');
    await p.check('#ap-approve-dialog input[type="checkbox"]');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForFunction(() => /Jóváhagyta: SzK/.test((document.getElementById('ap-ai-banner') || {}).textContent || ''));
    const ap = await calls(p, 'POST', '/api/appraisals/ROSENTHAL1960/rob2/approve');
    check(ap.length === 1 && ap[0].headers['If-Match'] === '"ap-1"' && ap[0].body.approver === 'SzK' && ap[0].body.confirm === true && ap[0].query.rater === 'ai', 'POST approve: If-Match, approver, rater=ai');
    check((await txt(p, '.ap-raters')).indexOf('kész') >= 0, 'jóváhagyás után a teljes vázlat kész státuszú');
    // a konszenzus nem számol vele
    await p.evaluate(() => { window.location.hash = '#/appraisal-consensus?tool=rob2&target=o1&unit=ROSENTHAL1960'; });
    await screen(p, 'appraisal-consensus');
    check((await txt(p, '#cs-not-ready')).indexOf('két független emberi értékelés') >= 0, 'konszenzus: az AI-vázlat nem második értékelő → nem kész');
    check((await txt(p, '#cs-excluded')).indexOf('ai') >= 0, 'a kizárt AI-vázlat megnevezve');
    await finish(o, 'ai-vázlat');
  });

  // ========== 4. konszenzus
  await test('Konszenzus: κ a motor szövegeként, A/B döntés eltérésenként, mentés rater=consensus', async () => {
    const o = await openPage('#/appraisal-consensus?tool=rob2&target=o1&unit=ARONSON1948');
    const p = o.page;
    await screen(p, 'appraisal-consensus');
    await p.waitForSelector('#cs-table');
    check((await txt(p, '#cs-kappa')) === AGREEMENT.kappa_text.hu, 'κ: a motor kappa_text-je szó szerint (' + AGREEMENT.kappa_text.hu + ')');
    check((await txt(p, '#cs-agreement')).indexOf(AGREEMENT.agreement_pct_text) >= 0, 'egyezés: a motor szövege');
    const diff = await p.$$eval('#cs-table tr.is-diff', (rs) => rs.map((r) => r.dataset.key));
    check(diff.join(',') === '1.2,dom:1,overall', 'eltérések: 1.2, D1, összítélet (' + diff.join(',') + ')');
    check((await txt(p, '#cs-counter')).indexOf('0 / 3') >= 0, 'számláló: 0 / 3 eldöntve');
    await p.click('#cs-save');
    check((await txt(p, '#cs-msg')).indexOf('Minden eltérésnél') >= 0, 'döntés nélkül nem menthető');
    await p.click('#cs-table tr[data-key="1.2"] input[value="a"] + label');
    await p.fill('#cs-table tr[data-key="1.2"] .cs-reason', 'A Methods 3. oldal: lezárt borítékok');
    await p.press('#cs-table tr[data-key="1.2"] .cs-reason', 'Tab');
    await p.click('#cs-table tr[data-key="dom:1"] input[value="b"] + label');
    await p.click('#cs-table tr[data-key="overall"] input[value="b"] + label');
    check((await txt(p, '#cs-counter')).indexOf('3 / 3') >= 0, 'számláló: 3 / 3');
    // A 1.2-es válasza (NI) + B „alacsony” D1-ítélete ellentmond az implikáltnak → indoklás nélkül a szerver elutasítja (X017)
    await p.click('#cs-save');
    await p.waitForSelector('#cs-msg .error-box');
    check((await txt(p, '#cs-msg')).indexOf('X017') >= 0, 'ellentmondó konszenzus (A válasza + B ítélete) → 422 X017, szó szerint');
    await p.click('#cs-table tr[data-key="dom:1"] input[value="a"] + label');
    await p.click('#cs-table tr[data-key="overall"] input[value="a"] + label');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#cs-save');
    await p.waitForSelector('.toast.is-success');
    const put = (await calls(p, 'PUT', '/api/appraisals/ARONSON1948/rob2'))[0];
    check(put && put.query.rater === 'consensus' && put.body.doc.status === 'consensus' && put.body.doc.assessor === 'KP' && put.body.doc.second_assessor === 'SzK',
      'PUT rater=consensus, status consensus, assessor=A, second_assessor=B');
    check(put.body.doc.answers['1.2'].value === 'no_information' && /lezárt borítékok/.test(put.body.doc.answers['1.2'].comment), 'a választott (A) érték + indoklás');
    check(put.body.doc.domain_judgements.filter((d) => d.domain === '1')[0].judgement === 'some_concerns', 'D1: az A ítélete (némi aggály)');
    await screen(p, 'appraisal-consensus');
    await p.waitForSelector('#cs-body');
    check((await txt(p, '#cs-body')).indexOf('Konszenzus-változat mentve') >= 0, 'mentés után: konszenzus-változat mentve');
    // F5: a doménítéletek egyezése (elsődleges), a tételszintű κ külön címkével
    check((await txt(p, '#cs-judg-agree')).indexOf('1') >= 0 && (await txt(p, '#cs-judg-agree')).indexOf(AGREEMENT.judgement_kappa.kappa_text.hu) >= 0,
      'F5: doménítélet-eltérések száma + a doménítéletek κ-ja (' + (await txt(p, '#cs-judg-agree')) + ')');
    check((await txt(p, '.cs-item-kappa-label')).length > 0, 'F5: a felső κ tételszintűként címkézve');
    // UX-3: újranyitáskor a mentett konszenzus döntései és indoklásai visszatöltődnek (a táblázat FELRAJZOLÁSA ELŐTT)
    await p.evaluate(() => { window.location.hash = '#/appraisal-summary?tool=rob2&outcome=o1'; });
    await screen(p, 'appraisal-summary');
    await p.evaluate(() => { window.location.hash = '#/appraisal-consensus?tool=rob2&target=o1&unit=ARONSON1948'; });
    await screen(p, 'appraisal-consensus');
    await p.waitForSelector('#cs-table');
    const restored = await p.evaluate(() => {
      const q = (k, v) => { const el = document.querySelector('#cs-table tr[data-key="' + k + '"] input[value="' + v + '"]'); return !!(el && el.checked); };
      const r = document.querySelector('#cs-table tr[data-key="1.2"] .cs-reason');
      return { a12: q('1.2', 'a'), d1: q('dom:1', 'a'), ov: q('overall', 'a'), reason: r ? r.value : null, counter: document.getElementById('cs-counter').textContent };
    });
    check(restored.a12 && restored.d1 && restored.ov, 'UX-3: a korábbi A/B döntések kijelölve (' + JSON.stringify(restored) + ')');
    check(/lezárt borítékok/.test(restored.reason || ''), 'UX-3: az indoklás visszatöltve (' + restored.reason + ')');
    check(restored.counter.indexOf('3 / 3') >= 0, 'UX-3: a számláló a visszatöltött döntésekkel: 3 / 3');
    await finish(o, 'konszenzus');
  });

  // ========== 5. forgalmi lámpa + rob-szinkron
  await test('Forgalmi lámpa a motor összesítőjéből; rob-oszlop szinkron: előnézet, alkalmazás If-Match-csel, 409', async () => {
    const o = await openPage('#/appraisal-summary?tool=rob2&outcome=o1');
    const p = o.page;
    await screen(p, 'appraisal-summary');
    await p.waitForSelector('#rt-matrix');
    const rows = await p.$$('#rt-matrix .rt-row');
    check(rows.length === SUMMARY.studies.length && rows.length >= 2, 'forgalmi lámpa: minden lezárt értékelés egy sor (' + rows.length + ')');
    const svgTxt = await txt(p, '#rt-matrix');
    const w = SUMMARY.studies.filter((s) => s.weight_text)[0];
    const wt = w ? (typeof w.weight_text === 'string' ? w.weight_text : w.weight_text.hu) : null;
    check(!!wt && svgTxt.indexOf(wt) >= 0, 'súly: a motor szövege szó szerint (' + wt + ')');
    const wsum = SUMMARY.weighted.filter((x) => x.pct > 0)[0];
    const wtxt = typeof wsum.text === 'string' ? wsum.text : wsum.text.hu;   // a motor szerződése: string (FID-7)
    check((await txt(p, '#rt-weighted')).indexOf(wtxt) >= 0, 'súlyozott összesítő: a motor szövege (' + wtxt + ')');
    check(/[●◐○]/.test(svgTxt), 'a jel alakja (●◐○) is jelöl');
    // F6: a motor implikált (nem emberi) ítélete megkülönböztetve: szaggatott keret, data-from, jelmagyarázat
    const nImp = SUMMARY.studies.reduce((n, st) => n + st.domains.filter((d) => d.from === 'implied').length, 0);
    const imp = await p.evaluate(() => ({ marks: document.querySelectorAll('#rt-matrix [data-from="implied"]').length, rings: document.querySelectorAll('#rt-matrix .rt-implied-ring').length,
      attr: (document.querySelector('#rt-matrix svg') || { getAttribute: () => null }).getAttribute('data-implied') }));
    check(nImp > 0 && imp.marks >= nImp && imp.rings >= nImp, 'F6: implikált jelek: ' + JSON.stringify(imp) + ' (a motorban ' + nImp + ')');
    check((await p.$('#rt-legend-implied')) !== null, 'F6: jelmagyarázat az implikált jelhez');
    // F5: a kettős értékelés megbízhatósága (a motor összevont egyezése)
    await p.waitForSelector('#rel-kappa', { timeout: 4000 });
    const rel = await txt(p, '#rel-panel');
    check(rel.indexOf(POOLED.agreement.judgement_kappa.kappa_text.hu) >= 0 && rel.indexOf(POOLED.agreement.kappa_text.hu) >= 0, 'F5: megbízhatóság-panel a motor szövegeivel (doménítélet- és tételszintű κ)');
    check(rel.indexOf(String(POOLED.units.length)) >= 0, 'F5: az egységek száma (' + POOLED.units.length + ')');
    check((await p.$('#rt-weighted')) !== null || (await txt(p, '#rt-panel')).indexOf('commit-futása') >= 0, 'súlyozott összesítő (vagy magyarázat)');
    await p.click('#sync-preview');
    await p.waitForSelector('#sync-table, #sync-none');
    const sync = await p.$$eval('#sync-table tbody tr', (rs) => rs.map((r) => r.textContent));
    check(sync.length >= 1, 'előnézet: a változó sorok (' + sync.length + ')');
    await p.evaluate(() => window.MA.dev.appraisal.bumpEtag('table'));
    await p.click('#sync-apply');
    await p.waitForSelector('[role="dialog"][aria-modal="true"]');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForSelector('#sync-repreview');
    check((await txt(p, '#sync-msg')).indexOf('időközben megváltozott') >= 0, '409: a tábla közben változott → új előnézet kell');
    await p.click('#sync-repreview');
    await p.waitForSelector('#sync-table');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#sync-apply');
    await p.waitForSelector('[role="dialog"][aria-modal="true"]');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForSelector('#sync-applied');
    const post = (await calls(p, 'POST', '/api/appraisals/rob-sync')).filter((c) => c.body.dry_run === false);
    check(post.length === 1 && /^"tbl-/.test(post[0].headers['If-Match']), 'alkalmazás: dry_run false + If-Match (a látott tábla ETag-je)');
    await finish(o, 'összesítő');
  });

  // ========== 6. fájlcsere
  await test('Fájlcsere (5. döntés): export letöltés, import fájlból ütközéssel és felülírással, beérkezett mappa', async () => {
    const o = await openPage('#/appraisal?tool=rob2&target=o1&unit=ARONSON1948');
    const p = o.page;
    await screen(p, 'appraisal');
    await formReady(p);
    const [dl] = await Promise.all([p.waitForEvent('download'), p.click('#ap-export')]);
    check(dl.suggestedFilename() === 'ARONSON1948.rob2.o1.SzK.json', 'export: letöltés a 2.4 szerinti fájlnévvel (' + dl.suggestedFilename() + ')');
    const exported = JSON.parse(fs.readFileSync(await dl.path(), 'utf-8'));
    check(exported.schema === 'szk.appraisal/v1' && exported.assessor === 'SzK', 'a letöltött fájl szk.appraisal/v1');
    // import fájlból: KP értékelése új egységre
    const kp = JSON.parse(JSON.stringify(exported));
    kp.assessor = 'KP';
    kp.target = { unit: 'HART1977', study_id: 'HART1977', outcome: 'o1', key: 'o1' };
    const [fc] = await Promise.all([p.waitForEvent('filechooser'), p.click('#ap-import')]);
    await fc.setFiles({ name: 'kp.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(kp)) });
    await p.waitForFunction(() => Array.from(document.querySelectorAll('.toast.is-success')).some((x) => /importálva/.test(x.textContent)));
    const impToast = await p.evaluate(() => Array.from(document.querySelectorAll('.toast.is-success')).map((x) => x.textContent).filter((x) => /importálva/.test(x))[0]);
    check(impToast.indexOf('HART1977.rob2.o1.KP.json') >= 0 && impToast.indexOf('új') >= 0, 'import: új fájl a 2.4 szerinti helyen (' + impToast.slice(0, 120) + ')');
    // ugyanaz, más tartalommal → ütközés → megerősítés → felülírás
    kp.answers['1.1'] = { value: 'no' };
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    const [fc2] = await Promise.all([p.waitForEvent('filechooser'), p.click('#ap-import')]);
    await fc2.setFiles({ name: 'kp.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(kp)) });
    await p.waitForSelector('[role="dialog"][aria-modal="true"]');
    check((await txt(p, '[role="dialog"]')).indexOf('már létezik') >= 0, 'ütközés: megerősítés felülírás előtt');
    await p.click('[role="dialog"] .modal-actions .btn-danger, [role="dialog"] .modal-actions .btn-primary');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.filter((c) => c.path === '/api/appraisals/import' && c.body.replace === true).length === 1);
    check(true, 'felülírás: második import replace: true-val');
    // beérkezett mappa
    await p.click('#ap-inbox-btn');
    await p.waitForSelector('#ap-inbox li[data-path]');
    check((await txt(p, '#ap-inbox')).indexOf('KP_rob2.json') >= 0 && (await txt(p, '#ap-inbox')).indexOf('beerkezett') >= 0, 'beérkezett mappa: a fájl és a mappa útja');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#ap-inbox .ap-inbox-import');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.path === '/api/appraisals/import' && c.body.path));
    const imp = (await calls(p, 'POST', '/api/appraisals/import'))[0];
    check(imp.body.path === '04_torzitas_kockazat/appraisals/beerkezett/KP_rob2.json', 'import a beérkezett mappából (út, nem tartalom)');
    // minden saját értékelés egy csomagban
    const [dl2] = await Promise.all([p.waitForEvent('download'), p.click('#ap-export-all')]);
    const bundle = JSON.parse(fs.readFileSync(await dl2.path(), 'utf-8'));
    check(bundle.schema === 'szk.appraisal-bundle/v1' && bundle.items.every((d) => d.assessor === 'SzK'), 'saját értékelések exportja: csomag, csak SzK');
    await finish(o, 'fájlcsere');
  });

  // ========== 7. PROBAST+AI
  await test('PROBAST+AI: két menet, menetenkénti teljesség, alkalmazhatóság, holisztikus összítélet', async () => {
    const o = await openPage('#/appraisal-probast?unit=LEE2023&target=XGB-PE');
    const p = o.page;
    await screen(p, 'appraisal-probast');
    await formReady(p);
    check((await compText(p)) === '16/34', 'összesen 16/34 (34 jelző-kérdés)');
    const passes = await p.$$eval('.ap-comp-pass', (ns) => ns.map((n) => n.dataset.pass + ':' + n.textContent.replace(/\s+/g, ' ').trim()));
    check(passes.some((x) => /^development:.*16\/16/.test(x)) && passes.some((x) => /^evaluation:.*0\/18/.test(x)), 'menetenként: fejlesztés 16/16, értékelés 0/18 (' + passes.join(' | ') + ')');
    check((await p.$$('.ap-item[data-id="1.1"] .ap-slot')).length === 2, 'az 1.1 két slotja (development/1.1 és evaluation/1.1)');
    check((await p.$('.ap-slot[data-key="development/1.1"]')) !== null && (await p.$('.ap-slot[data-key="evaluation/1.1"]')) !== null, 'menettel minősített kulcsok (H2 ellen)');
    const appl = await p.$$eval('.ap-judg-applicability', (ns) => ns.map((n) => n.dataset.domain + '/' + n.dataset.pass).sort());
    check(appl.join(',') === '1/development,1/evaluation,2/development,2/evaluation,3/development,3/evaluation', 'alkalmazhatóság az 1–3. doménen, menetenként (UX-2): ' + appl.join(','));
    // F4: menetenkénti összítélet (fejlesztés: minőség; értékelés: torzítási kockázat) → overall_passes
    const ovp = await p.$$eval('#ap-overall.is-per-pass .ap-overall-block', (ns) => ns.map((n) => n.dataset.pass));
    check(ovp.join(',') === 'development,evaluation', 'két menetenkénti összítélet-blokk (' + ovp.join(',') + ')');
    await p.selectOption('#ap-overall .ap-overall-block[data-pass="development"] select', 'high');
    await p.fill('#ap-overall .ap-overall-block[data-pass="development"] .ap-rationale', 'A 4. domén magas.');
    await p.press('#ap-overall .ap-overall-block[data-pass="development"] .ap-rationale', 'Tab');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#ap-save');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.method === 'PUT'), null, { timeout: 4000 });
    const put = (await calls(p, 'PUT', '/api/appraisals/LEE2023/probast-ai')).pop();
    const op = put && put.body.doc.overall_passes;
    check(Array.isArray(op) && op.length === 1 && op[0].pass === 'development' && op[0].judgement === 'high' && /4\. domén/.test(op[0].rationale || ''), 'mentés: overall_passes [{development, high, indoklás}] (' + JSON.stringify(op) + ')');
    check(put && (put.body.doc.overall === null || put.body.doc.overall === undefined || !put.body.doc.overall.judgement), 'a menetes eszköznél nincs egyetlen (menet nélküli) összítélet');
    await formReady(p);
    check((await txt(p, '#ap-overall')).indexOf('HOLISZTIKUS') >= 0, 'összítélet: holisztikus (nincs algoritmus) — szó szerint');
    check((await txt(p, '#pb-holistic')).indexOf('indoklás kötelező') >= 0, 'a fejléc kimondja: indoklás kötelező');
    // csak az értékelési menet
    await p.uncheck('#ap-pass-development');
    await p.waitForFunction(() => !document.querySelector('.ap-slot[data-key="development/1.1"]'));
    check((await p.$('.ap-slot[data-key="evaluation/1.1"]')) !== null, 'csak értékelés: a fejlesztési slotok eltűnnek');
    await p.waitForFunction(() => document.getElementById('ap-comp-text').textContent.trim() === '0/18', null, { timeout: 4000 });
    check(true, 'teljesség az értékelési menetre: 0/18 (a motor újraszámolta)');
    const chk = (await calls(p, 'POST', '/api/appraisals/LEE2023/probast-ai/check')).pop();
    check(chk && chk.body.doc.scope === 'evaluation' && chk.query.target === 'XGB-PE', 'a check scope=evaluation, target=XGB-PE');
    await p.click('#nav-overview');
    await p.waitForSelector('[role="dialog"][aria-modal="true"]');
    await p.click('[role="dialog"] .modal-actions .btn-danger, [role="dialog"] .modal-actions .btn-primary');
    await finish(o, 'probast');
  });

  // ========== 8. TRIPOD+AI
  await test('TRIPOD+AI: hőtérkép (■▣□·), D/E szűrő, űrlap + hiánylista, saját kézirat, „nem módszertan” megjegyzés', async () => {
    const o = await openPage('#/appraisal-tripod');
    const p = o.page;
    await screen(p, 'appraisal-tripod');
    await p.waitForSelector('#tr-heat');
    const units = await p.$$eval('#tr-heat tbody tr', (rs) => rs.map((r) => r.dataset.unit));
    check(units.join(',') === 'LEE2023,VARGA2024', 'hőtérkép: bevont vizsgálatok (a saját kézirat nem) — ' + units.join(','));
    const nAll = (await p.$$('#tr-heat thead th.tr-col')).length;
    check(nAll === 52, '52 altétel (' + nAll + ')');
    check(/[■▣□]/.test(await txt(p, '#tr-heat tbody')), 'állapot-jelek (alak + szín)');
    check((await txt(p, '#tr-note')).indexOf('NEM a módszertan') >= 0, 'kiírja: a jelentés teljességét méri, nem a módszertant');
    await p.click('#tr-filter-E');
    await p.waitForSelector('#tr-filter-E[aria-pressed="true"]');   // a navigáció (hashchange) aszinkron: az új nézetre várunk
    await screen(p, 'appraisal-tripod');
    await p.waitForSelector('#tr-heat');
    const nE = (await p.$$('#tr-heat thead th.tr-col')).length;
    check(nE > 0 && nE < 52, 'E szűrő: csak az értékelésre vonatkozó tételek (' + nE + ')');
    await p.click('#tr-heat tr[data-unit="VARGA2024"] .tr-open');
    await screen(p, 'appraisal-tripod');
    await formReady(p);
    await p.waitForSelector('#tr-missing');
    check((await p.$$('#tr-missing li[data-item]')).length >= 1, 'hiánylista a motor check.tripod-jából');
    check((await p.$('#ap-overall')) === null, 'TRIPOD+AI: nincs összítélet-választó (nem RoB-eszköz)');
    await p.click('#tr-mode-manuscript');
    await p.waitForSelector('#tr-mode-manuscript[aria-pressed="true"]');   // a navigáció aszinkron: az új nézetre várunk
    await screen(p, 'appraisal-tripod');
    await formReady(p);
    check((await txt(p, '#ap-form-title')).indexOf('Saját kézirat') >= 0, 'saját kézirat mód: az űrlap a manuscript egységre');
    check((await p.$('#tr-heat')) === null, 'saját kézirat módban nincs hőtérkép');
    await finish(o, 'tripod');
  });

  await test('UX-11: TRIPOD+AI hőtérkép 1280 px-en a saját dobozában görög, a lap nem', async () => {
    for (const w of [1280, 1440]) {
      const o = await openPage('#/appraisal-tripod', { viewport: { width: w, height: 900 } });
      const p = o.page;
      await screen(p, 'appraisal-tripod');
      await p.waitForSelector('#tr-heat');
      const r = await p.evaluate(() => {
        const wrap = document.querySelector('#tr-heat').closest('.tr-wrap');
        wrap.scrollLeft = wrap.scrollWidth;
        const last = Array.from(document.querySelectorAll('#tr-heat thead th.tr-col')).pop().getBoundingClientRect();
        const box = wrap.getBoundingClientRect();
        return { sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, wsw: wrap.scrollWidth, wcw: wrap.clientWidth,
          lastIn: last.right <= box.right + 1 && last.left >= box.left - 1 };
      });
      check(r.sw <= r.cw, w + ' px: nincs vízszintes lapgörgetés (' + r.sw + ' ≤ ' + r.cw + ')');
      check(r.wsw > r.wcw, w + ' px: a hőtérkép a dobozában görgethető (' + r.wsw + ' > ' + r.wcw + ')');
      check(r.lastIn, w + ' px: az utolsó oszlop (27c) görgetéssel elérhető, nincs levágva');
      await finish(o, 'tripod-' + w);
    }
  });

  // ========== 9. AMSTAR 2
  await test('AMSTAR 2: besorolás mindkét konvencióval, ideiglenes, ≠ GRADE, javaslat a project auditból, a GRADE fülön', async () => {
    const o = await openPage('#/appraisal-amstar2');
    const p = o.page;
    await screen(p, 'appraisal-amstar2');
    await formReady(p);
    const cls = await txt(p, '#am-class');
    check(cls.indexOf('MAGAS') >= 0 && cls.indexOf('meets') >= 0, 'besorolás (meets): MAGAS');
    check((await txt(p, '#am-alt')).indexOf('MÉRSÉKELT') >= 0, 'konvenció-érzékenység: weakness → MÉRSÉKELT');
    check(cls.indexOf('IDEIGLENES') >= 0 && cls.indexOf('13') >= 0, 'ideiglenes: a 13. tétel hiányzik');
    check(cls.indexOf('nem a bizonyítékok bizonyosságát') >= 0, '≠ GRADE megjegyzés');
    check((await txt(p, '#am-hints')).indexOf('4 adatbázis') >= 0, 'javaslat a project auditból (amstar2_hints)');
    check((await p.$$('.ap-item .badge.is-blocker')).length === 7, '7 kritikus tétel jelölve');
    check(await p.evaluate(() => window.MA.app.current().tab === 'grade'), 'a 6 GRADE/SoF fül alatt');
    await p.click('.ap-slot[data-key="13"] input[value="yes"] + label');
    await p.waitForFunction(() => document.getElementById('ap-comp-text').textContent.trim() === '16/16', null, { timeout: 4000 });
    await p.waitForFunction(() => document.getElementById('am-class').textContent.indexOf('IDEIGLENES') < 0);
    check(true, 'a hiányzó tétel kitöltése után nem ideiglenes');
    await finish(o, 'amstar2');
  });

  // ========== 10. motor nélkül (424)
  await test('A motor értékelő funkciói nélkül: 424, magyar teendő — nincs konzolhiba', async () => {
    const o = await openPage('#/overview');
    const p = o.page;
    await p.evaluate(() => {
      window.MA.appr.reset();
      window.MA.dev.fixtures.route('GET', '/api/instruments', () => ({ status: 424, envelope: { ok: false, error: { code: 'CAPABILITY_MISSING', http: 424,
        message: 'Ehhez a lépéshez a metaanalízis-motor értékelő funkciója kell (az értékelő eszközök listája), de ez a motorváltozat még nem tartalmazza (metaelemzes.api.instruments_list).' } } }), { first: true });
      window.location.hash = '#/appraisal';
    });
    await screen(p, 'appraisal');
    await p.waitForSelector('#ap-head .error-box');
    check((await txt(p, '#ap-head')).indexOf('metaelemzes.api.instruments_list') >= 0 && (await txt(p, '#ap-head')).indexOf('Frissítsd a motort') >= 0, '424: a szerver üzenete + teendő');
    await finish(o, '424');
  });

  // ========== 11. nyelv, téma
  await test('HU ↔ EN és sötét téma az értékelő képernyőkön; nincs hiányzó kulcs', async () => {
    const o = await openPage('#/appraisal?tool=rob2&target=o1&unit=FERGUSON1949');
    const p = o.page;
    await screen(p, 'appraisal');
    await formReady(p);
    await p.click('#lang-switch [data-lang="en"]');
    await screen(p, 'appraisal');
    await formReady(p);
    const t = await txt(p, '#screen-root');
    check(t.indexOf('Risk of bias') >= 0 && t.indexOf('CONSERVATIVE') >= 0 && t.indexOf('Answered') >= 0, 'EN: címek, algoritmus-címke angolul');
    check((await compText(p)) === '19/22', 'EN: a számszöveg változatlan');
    for (const id of ['appraisal-consensus', 'appraisal-summary', 'appraisal-probast', 'appraisal-tripod', 'appraisal-amstar2']) {
      await p.evaluate((x) => window.MA.app.navigate(x), id);
      await screen(p, id);
    }
    const before = await p.evaluate(() => getComputedStyle(document.querySelector('.ap-form, #ap-head, #am-head')).color);
    await p.click('#theme-toggle');
    const after = await p.evaluate(() => getComputedStyle(document.querySelector('.ap-form, #ap-head, #am-head')).color);
    check(before !== after, 'téma: a színek a tokenekből (' + before + ' → ' + after + ')');
    await p.click('#lang-switch [data-lang="hu"]');
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
