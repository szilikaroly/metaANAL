#!/usr/bin/env node
/* tests/gui/ui/grade.spec.js — GRADE / SoF (3.5.12) és Protokoll („1 Protokoll”) böngészős tesztje (terv 4.14, 6.5, 7.6,
 * 11/4. és 11/6. döntés).
 *
 * Futtatás:  node tests/gui/ui/grade.spec.js        (kilépési kód 1, ha bármi elbukik)
 *   MA_UI_DEV_HTML=<út>  — kész dev-build használata (a build kimarad).
 * A. rész — dev-build ?fixtures=1-gyel (web/fixtures/grade.json + src/dev/grade_backend.js), szigorú CSP mellett:
 *    a motor-tanács (bizonyíték, KB-jelvény, „Miért?” — a motor szövege elsőbbséget kap), az ítélet + kötelező indoklás
 *    (aria-invalid + leírás), a publikációs torzítás „gyanított” ítélete FELOLDATLAN (X019-sáv, a rögzítés tiltva és a
 *    szerver 409-e is megjelenik), feloldás 0/−1-gyel, rögzítés If-Match-csel, bizonyosság a háttérből, SoF (a motor
 *    szövegei forrásmezővel, alapkockázat-szerkesztő, előnézet, mentés, CSV/Markdown export), 424 (motor-tár nélkül),
 *    commit-futás nélküli kimenet, Protokoll (PICO, regisztráció, 409, előre rögzített jelölés hivatkozás nélkül nem
 *    megy), HU/EN, sötét téma, DOM-biztonság, böngészőtároló-szabály, mentetlen-őr.
 * B. rész — a VALÓDI szerverrel (tests/gui/ui/grade_server.py): a motor-tár nélkül 424 a felületen; teszt-csonk motorral
 *    a teljes GRADE-út (mentés → X019 → feloldás → rögzítés a projektnaplóba), SoF mentés + Excel-biztos CSV a lemezen,
 *    Protokoll (ma-projekt.json, kimenet szerkesztése, spec előre rögzített jelölése).
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
  throw new Error('Playwright nem található (NODE_PATH vagy /opt/node22/lib/node_modules/playwright)');
}
const { chromium } = loadPlaywright();

const ROOT = path.resolve(__dirname, '..', '..', '..');
const WEB = path.join(ROOT, 'ma_gui', 'web');
const DEV = process.env.MA_UI_DEV_HTML || path.join(WEB, 'dist', 'index.dev.html');
const HELPER = path.join(__dirname, 'grade_server.py');
const FX = JSON.parse(fs.readFileSync(path.join(WEB, 'fixtures', 'grade.json'), 'utf-8'));
const CHROME_FALLBACK = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const CSP = (n) => "default-src 'none'; script-src 'nonce-" + n + "'; style-src 'nonce-" + n + "'; " +
  "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
function fx(p) { return FX.routes.filter((r) => r.path === p)[0].envelope.data; }

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

function startReal(tmp, mode) {
  // mode: 'stub' (teszt-csonk motor), 'none' (a motor GRADE-függvényei kikapcsolva → 424), egyébként a valódi motor
  const args = [HELPER, 'serve', tmp].concat(mode === 'stub' ? ['--stub'] : (mode === 'none' ? ['--no-engine'] : []));
  const proc = spawn('python3', args, { cwd: ROOT, stdio: ['pipe', 'pipe', 'pipe'] });
  let buf = '';
  let err = '';
  const waiters = [];
  proc.stderr.on('data', (d) => { err += d; });
  proc.stdout.on('data', (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + 1);
      const w = waiters.shift();
      if (w) { w(JSON.parse(line)); }
    }
  });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('a szerver nem indult: ' + err)), 120000);
    waiters.push((v) => { clearTimeout(timer); resolve(v); });
  }).then((info) => Object.assign(info, {
    stop: () => new Promise((resolve) => { proc.on('exit', resolve); proc.stdin.write('quit\n'); setTimeout(() => proc.kill(), 15000); })
  }));
}

// ---------------------------------------------------------------- segédek
let browser, stat;
async function openDev(hash, opts) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: (opts && opts.scheme) || 'light' });
  const page = await ctx.newPage();
  const errors = [];
  const dialogs = [];
  page.on('console', (m) => { if (m.type() === 'error') { errors.push('console: ' + m.text()); } });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => { dialogs.push(d.type() + ':' + d.message()); d.dismiss().catch(() => null); });
  await page.goto(stat.base + '/?fixtures=1' + (hash || ''));
  await page.waitForSelector('html[data-ready="1"]', { timeout: 10000 });
  return { ctx, page, errors, dialogs };
}
async function screen(page, id) {
  await page.waitForFunction((x) => { const r = document.getElementById('screen-root'); return r && r.dataset.screen === x && !r.querySelector(':scope > .spinner'); }, id, { timeout: 10000 });
}
async function txt(page, sel) { const el = await page.$(sel); return el ? ((await el.textContent()) || '') : ''; }
async function calls(page, method, p) {
  return page.evaluate(([m, pp]) => window.MA.dev.fixtures.calls.filter((c) => c.method === m && c.path === pp), [method, p]);
}
async function finish(o, label) {
  check(o.errors.length === 0, label + ': nincs konzolhiba / CSP-sértés: ' + o.errors.join(' || '));
  check(o.dialogs.length === 0, label + ': nem nyílt alert/confirm (XSS-próba): ' + o.dialogs.join(' || '));
  const rep = await o.page.evaluate(() => {
    const ls = [], ss = [];
    for (let i = 0; i < localStorage.length; i++) { ls.push(localStorage.key(i)); }
    for (let i = 0; i < sessionStorage.length; i++) { ss.push(sessionStorage.key(i)); }
    return { ls, ss, vals: ls.map((k) => localStorage.getItem(k)), cookie: document.cookie, img: document.querySelectorAll('img, [onerror]').length };
  });
  check(rep.ls.every((k) => k.startsWith('mag.pref.')), label + ': localStorage csak mag.pref.* (' + rep.ls.join(',') + ')');
  check(rep.vals.every((v) => !/indoklás|CRD42|TBC|Smith/.test(v)), label + ': a preferenciákban nincs projektadat');
  check(rep.ss.every((k) => k === 'mag.token'), label + ': sessionStorage csak mag.token');
  check(rep.cookie === '' && rep.img === 0, label + ': nincs süti és nincs <img>/onerror a DOM-ban');
  const missing = await o.page.evaluate(() => window.MA.i18n.missing());
  check(missing.length === 0, label + ': nincs hiányzó i18n-kulcs (' + missing.join(', ') + ')');
  await o.ctx.close();
}
async function rate(page, dm, rating, rationale) {
  await page.selectOption('#gr-rating-' + dm, rating);
  if (rationale !== undefined) { await page.fill('#gr-rat-' + dm, rationale); }
}

// ================================================================ A. rész — fixture-háttér
async function partA() {
  await test('regisztráció: a GRADE és a Protokoll valódi képernyő (nem helyőrző)', async () => {
    const o = await openDev('#/overview');
    const r = await o.page.evaluate(() => window.MA.app.screens().filter((s) => !s.placeholder).map((s) => s.id + ':' + String(s.tab)));
    check(r.indexOf('grade:grade') >= 0 && r.indexOf('protocol:protocol') >= 0, 'regisztrálva: ' + r.join(' '));
    await o.page.keyboard.press('Alt+6');
    await screen(o.page, 'grade');
    check((await o.page.evaluate(() => location.hash)).indexOf('#/grade') === 0, 'Alt+6 → GRADE');
    await o.page.keyboard.press('Alt+1');
    await screen(o.page, 'protocol');
    await o.page.waitForSelector('#pr-question');
    check(true, 'Alt+1 → Protokoll');
    await finish(o, 'regisztráció');
  });

  await test('GRADE: a futás és a motor-tanács a motor szövegeivel; KB-jelvény; „Miért?” (motor-szöveg elsőbbsége); XSS szövegként', async () => {
    const o = await openDev('#/grade?outcome=o1');
    const p = o.page;
    await screen(p, 'grade');
    await p.waitForSelector('#gr-adv-inconsistency .gr-adv');
    const run = fx('/api/grade/o1').run;
    check((await txt(p, '#gr-effect')) === 'RR ' + run.display_text.hu, 'a futás hatás-szövege a motoré: ' + (await txt(p, '#gr-effect')));
    const adv = fx('/api/grade/o1/advice').advice;
    const sg = (dm) => adv.domains[dm].suggestion;
    check((await txt(p, '#gr-adv-inconsistency')).indexOf(sg('inconsistency').summary.hu) >= 0, 'inkonzisztencia: a motor összefoglalója (' + sg('inconsistency').summary.hu + ')');
    check((await txt(p, '#gr-adv-publication_bias')).indexOf(sg('publication_bias').summary.hu) >= 0, 'publikációs torzítás: a motor teszt-szövege');
    check((await p.$$('#gr-adv-risk_of_bias .kb-ref')).length === sg('risk_of_bias').kb_refs.length, 'KB-jelvények a RoB-tanácsnál (a motor kb_refs-e)');
    check((await txt(p, '#gr-adv-inconsistency')).indexOf('javaslat: súlyos') >= 0, 'a javaslat szótári címkével');
    check((await txt(p, '#gr-adv-indirectness')).indexOf('emberi ítélet') >= 0, 'a javaslat állapota (emberi ítélet)');
    check((await txt(p, '#gr-adv-indirectness')).indexOf('<img src=x onerror=alert(1)>') >= 0, 'a HTML-szerű szöveg szövegként jelenik meg');
    check((await txt(p, '#gr-adv-notes')).indexOf('Csak javaslat') >= 0, 'a motor megjegyzése: csak javaslat');
    const up = adv.advice.upgrades.large_effect;
    check((await txt(p, '#gr-uadv-large_effect')).indexOf(up.summary ? up.summary.hu : adv.advice.upgrades.flags[0].text.hu) >= 0, 'felminősítés: a motor összefoglalója');
    const ev0 = sg('inconsistency').evidence && sg('inconsistency').evidence[0];
    if (ev0) { check((await txt(p, '#gr-adv-inconsistency')).indexOf(ev0.text.hu) >= 0, 'a motor bizonyítéka szövegként: ' + ev0.text.hu); }
    // „Miért?”: a motor kezdőknek szóló szövege (inkonzisztencia), illetve a felület tartalék magyarázata (indirektség)
    await p.click('#gr-why-inconsistency');
    await p.waitForSelector('.why-pop');
    check((await txt(p, '.why-pop')).indexOf(sg('inconsistency').why.because.hu.slice(0, 40)) >= 0, '„Miért?”: a motor szövege');
    await p.keyboard.press('Escape');
    await p.click('#gr-why-indirectness');
    await p.waitForSelector('.why-pop');
    check((await txt(p, '.why-pop')).indexOf('PICO') >= 0, '„Miért?”: tartalék magyarázat, ha a motor nem ad');
    await p.keyboard.press('Escape');
    check(await p.$eval('#gr-record', (b) => b.disabled), 'hiányzó doménekkel a rögzítés tiltva');
    check((await txt(p, '#gr-record-why')).indexOf('hiányzó domén') >= 0, 'a tiltás oka kiírva');
    check((await txt(p, '#gr-journal')).indexOf('#1') >= 0, 'a projektnapló utolsó GRADE-sora');
    await finish(o, 'GRADE-tanács');
  });

  await test('GRADE: kötelező indoklás, „gyanított” publikációs torzítás feloldatlan (X019), szerver-kapu, feloldás, rögzítés', async () => {
    const o = await openDev('#/grade?outcome=o1');
    const p = o.page;
    await screen(p, 'grade');
    await p.waitForSelector('#gr-adv-inconsistency .gr-adv');
    await rate(p, 'risk_of_bias', 'serious');
    check((await txt(p, '#gr-step-risk_of_bias')).trim() === '−1', 'súlyos → −1 (szótári címke)');
    await rate(p, 'inconsistency', 'not serious', 'Az alcsoport (szélesség) magyarázza.');
    check((await txt(p, '#gr-concern-inconsistency')).indexOf('leminősítést jelez') >= 0, 'a tanács leminősítést jelez → külön figyelmeztetés');
    await rate(p, 'publication_bias', 'suspected');
    check(await p.$('#gr-step-publication_bias input[type="radio"]') !== null, 'gyanított: 0 / −1 választó');
    check((await txt(p, '#gr-step-publication_bias')).indexOf('feloldatlan') >= 0, 'gyanított: feloldatlan jelvény');
    check(await p.$eval('#gr-x019', (e) => !e.hidden), 'X019-sáv látszik');
    // mentés indoklás nélkül: a felület jelöli, kérés nem megy
    await p.click('#gr-save');
    const inv = await p.$eval('#gr-rat-risk_of_bias', (e) => ({ inv: e.getAttribute('aria-invalid'), desc: e.getAttribute('aria-describedby'), focus: document.activeElement === e }));
    check(inv.inv === 'true' && inv.desc === 'gr-rat-risk_of_bias-err' && inv.focus, 'aria-invalid + leírás + fókusz az első hibán');
    check((await txt(p, '#gr-rat-risk_of_bias-err')).length > 0, 'a hiba szövege a leírásban');
    check((await calls(p, 'PUT', '/api/grade/o1')).length === 0, 'hibás űrlappal nincs kérés');
    await p.fill('#gr-rat-risk_of_bias', 'A súly 41%-a magas RoB-ú vizsgálatból.');
    await rate(p, 'indirectness', 'not serious', 'A populáció és az oltás egyezik a kérdéssel.');
    await rate(p, 'imprecision', 'not serious', 'A CI szűk, nem lépi át az 1-et.');
    await p.fill('#gr-rat-publication_bias', 'A tesztek gyenge ereje miatt gyanú marad.');
    await p.click('#gr-save');
    await p.waitForFunction(() => /mentve/.test((document.getElementById('ma-toasts') || {}).textContent || ''));
    await screen(p, 'grade');
    let put = (await calls(p, 'PUT', '/api/grade/o1')).pop();
    check(put && put.body.record === false && put.body.grade.domains.publication_bias.step === null, 'piszkozat: a PB lépése null (feloldatlan)');
    check(put && !put.headers['If-Match'], 'új ítéletnél nincs If-Match');
    check(put && put.body.grade.domains.risk_of_bias.step === -1 && put.body.grade.run_id === fx('/api/grade/o1').run.run_id, 'a lépés és a futás a kérésben');
    check(await p.$eval('#gr-x019', (e) => !e.hidden), 'mentés után is feloldatlan');
    check((await txt(p, '#gr-cert')).indexOf('feloldatlan') >= 0, 'bizonyosság: — (feloldatlan)');
    // a szerver kapuja akkor is érvényes, ha a gomb tiltását megkerülik
    await p.evaluate(() => { document.getElementById('gr-record').disabled = false; });
    await p.click('#gr-record');
    await p.waitForSelector('#gr-msg .gr-gate');
    check((await txt(p, '#gr-msg')).indexOf('X019') >= 0 && (await p.$('#gr-msg [role="alert"]')) !== null, 'GATE_BLOCKED X019 a felületen (role=alert)');
    // feloldás: −1
    const radios = await p.$$('#gr-step-publication_bias input[type="radio"]');
    await radios[1].check();
    check(await p.$eval('#gr-x019', (e) => e.hidden), 'feloldás után az X019-sáv eltűnik');
    check(!(await p.$eval('#gr-record', (b) => b.disabled)), 'a rögzítés engedélyezett');
    await p.click('#gr-record');
    // a végső bizonyosság emberi ítélet (GRADE-09): az első rögzítés menti a piszkozatot és megerősítést kér, a
    // választó a motor számolt szintjével előtöltve; az ember megerősíti → második rögzítés a certainty-vel
    await p.waitForSelector('#gr-human-cert:not([hidden])');
    check(await p.$eval('#gr-human-cert-sel', (e) => e.value) === 'low', 'a választó a számolt szinttel előtöltve (alacsony)');
    await p.click('#gr-record');
    await p.waitForFunction(() => /rögzítve/.test((document.getElementById('ma-toasts') || {}).textContent || ''));
    await screen(p, 'grade');
    put = (await calls(p, 'PUT', '/api/grade/o1')).pop();
    check(put.body.record === true && put.body.grade.domains.publication_bias.step === -1, 'rögzítés: record + −1');
    check(put.body.certainty === 'low', 'a rögzítés az ember megerősített szintjével (' + put.body.certainty + ')');
    check(/^"grade-o1-/.test(put.headers['If-Match'] || ''), 'rögzítés If-Match-csel (' + put.headers['If-Match'] + ')');
    check((await txt(p, '#gr-cert-val')).indexOf('alacsony') >= 0, 'bizonyosság a háttérből: alacsony (' + (await txt(p, '#gr-cert-val')) + ')');
    check((await txt(p, '#gr-journal')).indexOf('alacsony') >= 0, 'a napló sora frissült');
    // felminősítés indoklás nélkül
    await p.check('#gr-up-large_effect');
    await p.click('#gr-save');
    check((await p.$eval('#gr-urat-large_effect', (e) => e.getAttribute('aria-invalid'))) === 'true', 'felminősítéshez is kötelező az indoklás');
    await finish(o, 'GRADE-út');
  });

  await test('FID-2/FID-3: elavult előtöltés nem rögzíthető; a rögzített EMBERI bizonyosság nem „javaslat”, az eltérő motor-szint külön látszik', async () => {
    const o = await openDev('#/grade?outcome=o1');
    const p = o.page;
    await screen(p, 'grade');
    await p.waitForSelector('#gr-adv-inconsistency .gr-adv');
    await rate(p, 'risk_of_bias', 'serious', 'A súly 41%-a magas RoB-ú vizsgálatból.');
    await rate(p, 'inconsistency', 'not serious', 'Az alcsoport magyarázza.');
    await rate(p, 'indirectness', 'not serious', 'A populáció egyezik.');
    await rate(p, 'imprecision', 'not serious', 'A CI szűk.');
    await rate(p, 'publication_bias', 'undetected', 'A tölcsér szimmetrikus.');
    await p.click('#gr-record');
    await p.waitForSelector('#gr-human-cert:not([hidden])');
    const first = await p.$eval('#gr-human-cert-sel', (e) => e.value);
    check(first === 'moderate', 'előtöltés: a motor MOSTANI számolt szintje (mérsékelt): ' + first);
    // FID-2: szerkesztés után az előtöltés elavult — nem maradhat a választóban
    await rate(p, 'imprecision', 'serious', 'A CI átlépi a klinikai küszöböt.');
    check(await p.$eval('#gr-human-cert-sel', (e) => e.value) === '', 'FID-2: szerkesztés után a választó üres (nincs elavult „mérsékelt”)');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#gr-record');
    await p.waitForFunction(() => document.getElementById('gr-human-cert-sel') && document.getElementById('gr-human-cert-sel').value !== '', null, { timeout: 4000 });
    const puts = await calls(p, 'PUT', '/api/grade/o1');
    check(puts.length >= 1 && puts[0].body.certainty === undefined, 'FID-2: a második rögzítés NEM a régi szinttel ment (certainty nincs a kérésben)');
    check(await p.$eval('#gr-human-cert-sel', (e) => e.value) === 'low', 'FID-2: az új előtöltés a 422 friss számolt szintje (alacsony)');
    // az ember mást választ (mérsékelt) → rögzítés; a nézet az emberi ítéletet mutatja, a motor eltérő szintjét külön
    await p.selectOption('#gr-human-cert-sel', 'moderate');
    await p.click('#gr-record');
    await p.waitForFunction(() => /rögzítve/.test((document.getElementById('ma-toasts') || {}).textContent || ''));
    await screen(p, 'grade');
    const put = (await calls(p, 'PUT', '/api/grade/o1')).pop();
    check(put.body.certainty === 'moderate', 'a rögzítés az ember szintjével (mérsékelt)');
    check(await p.$eval('#gr-cert-val', (e) => e.dataset.source) === 'human', 'FID-3: a bizonyosság forrása: ember (data-source=human)');
    const cert = await txt(p, '#gr-cert');
    check(cert.indexOf('mérsékelt') >= 0 && cert.indexOf('javasl') < 0, 'FID-3: „mérsékelt”, nem a motor javaslataként (' + cert + ')');
    check((await txt(p, '#gr-cert-engine')).indexOf('alacsony') >= 0, 'FID-3: a motor eltérő számolt szintje (alacsony) külön jelezve');
    await finish(o, 'FID-2/3');
  });

  await test('F11: külső alapkockázat forrás-hivatkozással (a lábjegyzetbe), hiányzó hivatkozásnál figyelmeztetés', async () => {
    const o = await openDev('#/grade?outcome=o1');
    const p = o.page;
    await screen(p, 'grade');
    await p.waitForSelector('#sof-table');
    await p.click('#sof-add-risk');
    await p.waitForSelector('#sof-cite-1');
    check((await txt(p, '#sof-cite-warn-1')).length > 0, 'hivatkozás nélkül: figyelmeztetés (' + (await txt(p, '#sof-cite-warn-1')) + ')');
    await p.fill('#sof-label-1', 'Magyar regiszter');
    await p.fill('#sof-per-1', '12,5');
    await p.fill('#sof-cite-1', 'KSH Népegészségügyi Adattár, 2023');
    await p.press('#sof-cite-1', 'Tab');
    check((await txt(p, '#sof-cite-warn-1')).trim() === '', 'hivatkozással: nincs figyelmeztetés');
    await p.evaluate(() => window.MA.dev.fixtures.reset());
    await p.click('#sof-preview');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.method === 'PUT' && c.path === '/api/sof/o1'), null, { timeout: 4000 });
    const put = (await calls(p, 'PUT', '/api/sof/o1')).pop();
    check(put.body.assumed_risks[1].note === 'KSH Népegészségügyi Adattár, 2023' && put.body.assumed_risks[1].source === 'external', 'a hivatkozás a kérésben (assumed_risks[1].note)');
    await finish(o, 'F11');
  });

  await test('GRADE: mentetlen-őr, 424 (motor-tár nélkül), commit-futás nélküli kimenet', async () => {
    const o = await openDev('#/grade?outcome=o1');
    const p = o.page;
    await screen(p, 'grade');
    await rate(p, 'risk_of_bias', 'serious', 'x');
    await p.evaluate(() => { window.location.hash = '#/overview'; });
    await p.waitForSelector('[role="dialog"]');
    check((await txt(p, '[role="dialog"]')).indexOf('nincsenek elmentve') >= 0, 'mentetlen-őr');
    await p.click('[role="dialog"] .btn-danger');
    await screen(p, 'overview');
    await p.evaluate(() => { window.MA.dev.grade.engineMissing(true); window.location.hash = '#/grade?outcome=o1'; });
    await screen(p, 'grade');
    await p.waitForSelector('#gr-engine-missing');
    check((await txt(p, '#gr-engine-missing')).indexOf('Frissítsd a motort') >= 0, '424: a szerver magyar üzenete szó szerint');
    check(await p.$eval('#gr-save', (b) => b.disabled) && await p.$eval('#gr-record', (b) => b.disabled), 'motor-tár nélkül mentés/rögzítés tiltva');
    check((await txt(p, '#gr-effect')).indexOf('0.49') >= 0, 'a futás adatai a 424 részleteiből is látszanak');
    await p.evaluate(() => { window.MA.dev.grade.engineMissing(false); window.location.hash = '#/grade?outcome=o2'; });
    await screen(p, 'grade');
    await p.waitForSelector('#gr-norun');
    check(!(await p.$('#gr-domains')) && !(await p.$('#sof-panel')), 'commit-futás nélkül nincs űrlap, csak a teendő');
    await finish(o, 'GRADE-őrök');
  });

  await test('SoF: a motor szövegei forrásmezővel; alapkockázat-szerkesztő; előnézet; mentés; CSV/Markdown export', async () => {
    const o = await openDev('#/grade?outcome=o1');
    const p = o.page;
    await screen(p, 'grade');
    await p.waitForSelector('#sof-table');
    const row = fx('/api/sof/o1').preview.rows[0];
    const cells = await p.$$eval('#sof-table tbody tr:first-child > *', (els) => els.map((e) => ({ t: e.textContent, src: e.dataset.source || '', col: e.dataset.col })));
    const abs = cells.filter((c) => c.col === 'absolute_text')[0];
    check(abs && abs.t === row.absolute_text.hu, 'abszolút hatás: a motor szövege (' + (abs && abs.t) + ')');
    check(abs && abs.src === row.sources.absolute, 'a cella forrásmezője (data-source: ' + (abs && abs.src) + ')');
    check(cells.filter((c) => c.col === 'relative_text')[0].t === row.relative_text.hu, 'relatív hatás: a motor szövege');
    check((await txt(p, '#sof-statement')).indexOf(fx('/api/sof/o1').preview.statement.hu) >= 0, 'közérthető összefoglaló: a motor mondata');
    // export mentés előtt: a szerver 404-e (toast)
    await p.click('#sof-export-csv');
    await p.waitForFunction(() => /mentett SoF/.test((document.getElementById('ma-alerts') || {}).textContent || ''));
    check(true, 'mentés előtti export: érthető hiba');
    // külső alapkockázat érték nélkül → 422; értékkel → előnézet új sorral
    await p.click('#sof-add-risk');
    await p.fill('#sof-label-1', 'Magyar regiszter');
    await p.click('#sof-preview');
    await p.waitForFunction(() => /1000 fő/.test((document.getElementById('ma-alerts') || {}).textContent || ''));
    await p.fill('#sof-per-1', '12,5');
    await p.click('#sof-preview');
    await p.waitForFunction(() => document.querySelectorAll('#sof-table tbody tr').length === 2);
    check((await txt(p, '#sof-table tbody tr:nth-child(2)')).indexOf('12,5 / 1000 fő') >= 0, 'a külső alapkockázat nyers szövegként megy a motorhoz');
    let put = (await calls(p, 'PUT', '/api/sof/o1')).pop();
    check(put.body.dry_run === true && put.body.assumed_risks[1].per_1000 === '12,5' && put.body.assumed_risks[1].source === 'external', 'előnézet: dry_run, nyers szöveg');
    await p.click('#sof-add-note');
    await p.fill('#sof-note-0', 'Saját lábjegyzet');
    await p.click('#sof-save');
    await p.waitForFunction(() => /SoF-tábla mentve/.test((document.getElementById('ma-toasts') || {}).textContent || ''));
    put = (await calls(p, 'PUT', '/api/sof/o1')).pop();
    check(put.body.dry_run === false && put.body.footnotes[0].text === 'Saját lábjegyzet', 'mentés a lábjegyzettel');
    check((await txt(p, '#sof-saved')).indexOf('06_kezirat/sof/o1.sof.json') >= 0, 'mentve: az út kiírva');
    await p.selectOption('#sof-lang', 'en');
    await p.click('#sof-export-csv');
    await p.waitForSelector('#sof-dl');
    const ex = (await calls(p, 'POST', '/api/sof/o1/export')).pop();
    check(ex.body.format === 'csv' && ex.body.lang === 'en', 'export-kérés: csv, en');
    check(/^\/f\//.test(await p.$eval('#sof-dl', (a) => a.getAttribute('href'))), 'aláírt letöltési link (/f/…)');
    await p.click('#sof-export-md');
    await p.waitForSelector('#sof-md');
    check((await txt(p, '#sof-md')).indexOf('| Outcome |') >= 0, 'Markdown-szöveg másolható');
    await finish(o, 'SoF');
  });

  await test('Protokoll: PICO, regisztráció (If-Match, 409), előre rögzített jelölés hivatkozással', async () => {
    const o = await openDev('#/protocol');
    const p = o.page;
    await screen(p, 'protocol');
    await p.waitForSelector('#pr-q-P');
    const pr = fx('/api/protocol');
    check((await p.inputValue('#pr-q-P')) === pr.question.P, 'P a ma-projekt.json-ból');
    check((await p.inputValue('#pr-reg-id')) === 'CRD42024000001', 'regisztrációs azonosító');
    check(await p.$eval('#pr-save', (b) => b.disabled), 'változás nélkül a mentés tiltva');
    await p.fill('#pr-q-C', 'oltatlan kontrollcsoport');
    await p.selectOption('#pr-type', 'exposure');
    check((await txt(p, '#pr-type-help')).indexOf('ROBINS-E') >= 0, 'a típus kezdőknek szóló súgója');
    await p.click('#pr-save');
    await p.waitForFunction(() => /Protokoll mentve/.test((document.getElementById('ma-toasts') || {}).textContent || ''));
    const put = (await calls(p, 'PUT', '/api/protocol')).pop();
    check(put.headers['If-Match'] === '"pr-1"' && put.body.question.C === 'oltatlan kontrollcsoport' && put.body.review_type === 'exposure', 'PUT If-Match-csel, a változott mezőkkel');
    check(put.body.registration.registry === 'PROSPERO' && put.body.registration.id === 'CRD42024000001', 'a regisztráció megmarad');
    // ütközés: valaki közben írta
    await screen(p, 'protocol');
    await p.evaluate(() => window.MA.dev.grade.bumpEtag('protocol'));
    await p.fill('#pr-q-O', 'TBC');
    await p.click('#pr-save');
    await p.waitForFunction(() => /időközben megváltozott/.test((document.getElementById('ma-alerts') || {}).textContent || ''));
    check(true, '409: a szerver üzenete');
    // előre rögzített jelölés hivatkozás nélkül: nem megy ki kérés
    const n0 = (await calls(p, 'PUT', '/api/specs/o1_primary_no_estim')).length;
    await p.check('#pr-sp-pre-1');
    await p.click('#pr-sp-save-1');
    check((await txt(p, '#pr-sp-st-1')).indexOf('hivatkozással') >= 0, 'hivatkozás nélkül figyelmeztet');
    check((await calls(p, 'PUT', '/api/specs/o1_primary_no_estim')).length === n0, 'nincs mentés hivatkozás nélkül');
    check((await p.$$('#pr-outcome-table tbody tr')).length === pr.outcomes.length, 'a kimenetek listája');
    await finish(o, 'Protokoll');
  });

  await test('angol nyelv és sötét téma: a felület angol, a motor-szöveg az angol változat', async () => {
    const o = await openDev('#/grade?outcome=o1', { scheme: 'dark' });
    const p = o.page;
    await screen(p, 'grade');
    await p.evaluate(() => window.MA.i18n.setLang('en'));
    await screen(p, 'grade');
    await p.waitForSelector('#sof-table');
    check((await txt(p, '#gr-domains')).indexOf('Risk of bias') >= 0, 'angol domén-nevek');
    const abs = await p.$eval('#sof-table [data-col="absolute_text"]', (e) => e.textContent);
    check(abs === fx('/api/sof/o1').preview.rows[0].absolute_text.en, 'angol motor-szöveg (' + abs + ')');
    const bg = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
    check(bg !== 'rgb(255, 255, 255)', 'sötét téma: ' + bg);
    await p.evaluate(() => window.MA.i18n.setLang('hu'));
    await finish(o, 'EN/sötét');
  });
}

// ================================================================ B. rész — valódi szerver
async function waitToast(p, re) {
  await p.waitForFunction((s) => new RegExp(s).test(((document.getElementById('ma-toasts') || {}).textContent || '') + ((document.getElementById('ma-alerts') || {}).textContent || '')), re.source, { timeout: 20000 });
}

async function realPage(srv) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) { errors.push('console: ' + m.text()); } });
  await page.goto(srv.url);
  await page.waitForSelector('html[data-ready="1"]', { timeout: 30000 });
  return { ctx, page, errors };
}

async function partB() {
  await test('valódi szerver, motor v1 nélkül: a GRADE 424-et mutat magyarul, a Protokoll működik', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ma_grade_ui0_'));
    const srv = await startReal(tmp, 'none');
    try {
      const o = await realPage(srv);
      const p = o.page;
      await p.evaluate(() => { window.location.hash = '#/grade?outcome=o1'; });
      await screen(p, 'grade');
      await p.waitForSelector('#gr-engine-missing');
      const m = await txt(p, '#gr-engine-missing');
      check(m.indexOf('metaelemzes.api.grade_get') >= 0 && m.indexOf('Frissítsd a motort') >= 0, '424 a szerver üzenetével: ' + m.slice(0, 120));
      check((await txt(p, '.gr-meta')).indexOf(srv.run_id) >= 0, 'a futás a 424 részleteiből');
      await p.waitForSelector('#sof-body .error-box');
      check((await txt(p, '#sof-body')).indexOf('metaelemzes.api.sof') >= 0, 'a SoF is jelzi a hiányzó motor-funkciót');
      await p.evaluate(() => { window.location.hash = '#/protocol'; });
      await screen(p, 'protocol');
      await p.waitForSelector('#pr-q-P');
      check(true, 'a Protokoll a motor v1 nélkül is betölt');
      check(o.errors.length === 0, 'nincs konzolhiba: ' + o.errors.join(' | '));
      await o.ctx.close();
    } finally {
      await srv.stop();
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  });

  await test('valódi szerver + teszt-csonk motor: GRADE mentés → X019 → feloldás → napló; SoF + Excel-biztos CSV; Protokoll', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ma_grade_ui1_'));
    const srv = await startReal(tmp, 'stub');
    try {
      const o = await realPage(srv);
      const p = o.page;
      await p.evaluate(() => { window.location.hash = '#/grade?outcome=o1'; });
      await screen(p, 'grade');
      await p.waitForSelector('#gr-adv-inconsistency .gr-adv');
      await rate(p, 'risk_of_bias', 'serious', 'magas RoB-ú vizsgálatok súlya');
      await rate(p, 'inconsistency', 'not serious', 'alcsoport magyarázza');
      await rate(p, 'indirectness', 'not serious', 'egyezik a kérdéssel');
      await rate(p, 'imprecision', 'not serious', 'szűk CI');
      await rate(p, 'publication_bias', 'suspected', 'gyenge tesztek');
      await p.click('#gr-save');
      await waitToast(p, /GRADE-ítélet mentve/);
      await screen(p, 'grade');
      const f = path.join(srv.proj, '06_kezirat', 'grade', 'o1.grade.json');
      const saved = JSON.parse(fs.readFileSync(f, 'utf-8'));
      check(saved.domains.publication_bias.status === 'unresolved' && saved.certainty === null, 'a lemezen: feloldatlan, bizonyosság nélkül');
      check(await p.$eval('#gr-x019', (e) => !e.hidden), 'X019-sáv a valódi szerver nézetéből');
      const radios = await p.$$('#gr-step-publication_bias input[type="radio"]');
      await radios[0].check();
      await p.click('#gr-record');
      // a végső bizonyosság emberi ítélet (GRADE-09): a motor számolt szintje csak előtöltés — az első rögzítés a
      // piszkozatot menti, és a megerősítést kéri; az ember a (motor által előtöltött) szintet erősíti meg
      await p.waitForSelector('#gr-human-cert:not([hidden])');
      check(await p.$eval('#gr-human-cert-sel', (e) => e.value) === 'moderate', 'a választó a motor számolt szintjével előtöltve');
      const pending = await p.evaluate(() => window.MA.api.get('/api/log/grade', { toast: false }).then((e) => e.data.items.filter((r) => r.outcome === 'o1').length));
      check(pending === 0, 'megerősítés nélkül nincs naplósor (' + pending + ')');
      await p.click('#gr-record');
      await waitToast(p, /rögzítve/);
      await screen(p, 'grade');
      const log = await p.evaluate(() => window.MA.api.get('/api/log/grade', { toast: false }).then((e) => e.data.items));
      const row = log.filter((r) => r.outcome === 'o1').pop();
      check(row && row.certainty === 'moderate' && /^−1 serious: /.test(row.risk_of_bias) && /^0 suspected: /.test(row.publication_bias),
        'projektnapló: előjeles lépés-szövegek, mérsékelt (' + JSON.stringify(row && [row.certainty, row.risk_of_bias, row.publication_bias]) + ')');
      check((await txt(p, '#gr-cert-val')).indexOf('mérsékelt') >= 0, 'bizonyosság a motorból (csonk)');
      // SoF mentés + CSV
      await p.waitForSelector('#sof-save');
      await p.click('#sof-save');
      await waitToast(p, /SoF-tábla mentve/);
      await p.click('#sof-export-csv');
      await p.waitForSelector('#sof-dl');
      const csv = fs.readFileSync(path.join(srv.proj, '06_kezirat', 'sof', 'o1.sof.en.csv'), 'utf-8');
      check(csv.charCodeAt(0) === 0xFEFF && csv.indexOf('"\'=HYPERLINK(""x"")"') >= 0 && csv.indexOf("'@SUM(A1)") >= 0, 'a CSV Excel-biztos (BOM, \' előtag)');
      const href = await p.$eval('#sof-dl', (a) => a.getAttribute('href'));
      const dl = await p.evaluate((u) => fetch(u).then((r) => r.status), href);
      check(dl === 200, 'az aláírt link letölthető (' + dl + ')');
      // Protokoll: PICO + regisztráció → ma-projekt.json; kimenet szerkesztése; spec előre rögzítettnek jelölése
      await p.evaluate(() => { window.location.hash = '#/protocol'; });
      await screen(p, 'protocol');
      await p.waitForSelector('#pr-q-P');
      await p.fill('#pr-q-P', 'oltatlan személyek');
      await p.selectOption('#pr-reg-registry', 'PROSPERO');
      await p.fill('#pr-reg-id', 'CRD42024999999');
      await p.click('#pr-save');
      await waitToast(p, /Protokoll mentve/);
      let meta = JSON.parse(fs.readFileSync(path.join(srv.proj, 'ma-projekt.json'), 'utf-8'));
      check(meta.question.P === 'oltatlan személyek' && meta.protocol.registration.id === 'CRD42024999999', 'ma-projekt.json: PICO és regisztráció');
      await screen(p, 'protocol');
      await p.click('#pr-oc-edit-o2');
      await p.waitForSelector('#pr-oc-critical');
      await p.check('#pr-oc-critical');
      await p.click('[role="dialog"] .btn-primary');
      await waitToast(p, /Kimenet felvéve/);
      meta = JSON.parse(fs.readFileSync(path.join(srv.proj, 'ma-projekt.json'), 'utf-8'));
      check(meta.outcomes.filter((x) => x.id === 'o2')[0].critical === true, 'a kimenet szerkesztése (kritikus) a meglévő outcome-API-n át');
      await screen(p, 'protocol');
      await p.waitForSelector('#pr-sp-pre-0');
      await p.check('#pr-sp-pre-0');
      await p.fill('#pr-sp-ref-0', 'protokoll 9.2');
      await p.click('#pr-sp-save-0');
      await waitToast(p, /Elemzési terv mentve/);
      const spec = JSON.parse(fs.readFileSync(path.join(srv.proj, '05_elemzes', 'specs', 'o1_primary.json'), 'utf-8'));
      check(spec.prespecified === true && spec.protocol_ref === 'protokoll 9.2', 'a spec előre rögzített, hivatkozással');
      const act = fs.readFileSync(path.join(srv.proj, '07_ellenorzes', 'activity.jsonl'), 'utf-8');
      check(act.indexOf('grade.record') >= 0 && act.indexOf('sof.save') >= 0 && act.indexOf('project.protocol') >= 0, 'activity-sorok');
      check(act.indexOf('gyenge tesztek') < 0 && act.indexOf('oltatlan személyek') < 0, 'az activity-naplóban nincs szabad szöveg');
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
