#!/usr/bin/env node
/* tests/gui/ui/e2e.spec.js — az MVP elfogadási teszt (terv 9.2 „Elfogadás”, 8.6 2. réteg): végponttól végpontig,
 * plugin nélkül, a VALÓDI munkapad-szerverrel (ma_gui.server, port 0) egy ideiglenes projekten, a termék-builddel.
 *
 * Futtatás:  node tests/gui/ui/e2e.spec.js            (kilépési kód 1, ha bármi elbukik)
 * Előfeltétel: python3; Playwright (globális: /opt/node22/lib/node_modules/playwright vagy NODE_PATH).
 * A projektet és a szervert a tests/gui/ui/e2e_server.py készíti (BCG → o1, Normand → o2, két generált PDF).
 *
 * A BCG (RR) és a Normand (MD; tizedesvesszős CSV) példán:
 *   projekt megnyitása (indítókód → token, adatvédelmi állapot) → adatbevitel beillesztéssel (TSV) → élő validálás
 *   (hiba bevitele → a cella jelölve → javítás → a jelzés eltűnik) → eredet (dokumentum + oldal) → [Normand]
 *   átváltó (medián/IQR → átlag/SD: becsült jelölés + eredet) és „Nem hiba — indoklás” döntés → spec → explore →
 *   commit → forest / funnel / Doi / LOO / befolyás a motor szövegeivel → kattintás egy vizsgálatra → lefúrás a
 *   kinyerési sorig és a forrás-PDF oldaláig (aláírt URL) → megállapítás + kapu (előzetes tiltás, GATE_BLOCKED egy
 *   párhuzamos „ágens” blockerére, lezárás, PASS) → project audit X-találat (adatmódosítás után elavult futás = X001)
 *   → audit-ZIP (kétszer azonos sha256) → pillanatkép (nulla hálózati kérés).
 * Számhűség (6.7): a felületen látott számok = a futás plot_data.json szövegei = a results.json számai a motor
 * formázóival (és a report.md / motor-SVG ugyanazt írja).
 * A végén ellenőrzőlista (lépésenként ✔/✖) és a szokásos „N/M ellenőrzés zöld” összesítő.
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
const BUILD = path.join(ROOT, 'ma_gui', 'web', 'build_gui.py');
const HELPER = path.join(__dirname, 'e2e_server.py');
const CHROME_FALLBACK = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const O1 = '03_adatok/o1.csv';
const O2 = '03_adatok/o2.csv';
const PDF_BCG = 'file:_privat/pdf/aronson1948.pdf';
const PDF_NORMAND = 'file:_privat/pdf/normand1999.pdf';
// a BCG utolsó 3 sora és a Normand 8 sora — „Excelből” (tabulátor, sorvég CRLF); a Normand SD-k tizedesvesszővel,
// a Montreal-Home átlag/SD üresen (az átváltó tölti ki)
const BCG_PASTE = [
  ['Comstock et al 1974', '186', '50634', '141', '27338', '18', '1974', 'systematic'],
  ['Comstock & Webster 1969', '5', '2498', '3', '2341', '33', '1969', 'systematic'],
  ['Comstock et al 1976', '27', '16913', '29', '17854', '33', '1976', 'systematic']
];
const NORMAND_PASTE = [
  ['Orpington-Mild', '27', '7,0', '31', '29', '4,0', '32', 'nem'],
  ['Orpington-Moderate', '64', '17,0', '75', '119', '29,0', '71', 'nem'],
  ['Orpington-Severe', '66', '20,0', '18', '137', '48,0', '18', 'nem'],
  ['Montreal-Home', '', '', '8', '18', '11,0', '13', 'nem'],
  ['Montreal-Transfer', '19', '7,0', '57', '18', '4,0', '52', 'nem'],
  ['Newcastle', '52', '45,0', '34', '41', '34,0', '33', 'nem'],
  ['Umea', '21', '16,0', '110', '31', '27,0', '183', 'nem'],
  ['Uppsala', '30', '27,0', '60', '23', '20,0', '52', 'nem']
];
const tsv = (rows) => rows.map((r) => r.join('\t')).join('\r\n') + '\r\n';

// ---------------------------------------------------------------- mini-tesztkeret + elfogadási ellenőrzőlista
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
const CHECKLIST = [];
/** Az elfogadási lépés (9.2) a hozzá tartozó tesztek ellenőrzéseiből: zöld, ha mindegyik zöld. */
function step(label, tests) { CHECKLIST.push({ label, tests }); }

function run(cmd, args, opts) {
  const r = spawnSync(cmd, args, Object.assign({ cwd: ROOT, encoding: 'utf-8', maxBuffer: 64 * 1024 * 1024 }, opts || {}));
  if (r.status !== 0) { throw new Error(cmd + ' ' + args.join(' ') + ' → ' + r.status + '\n' + r.stdout + r.stderr); }
  return r.stdout;
}
const py = (args) => JSON.parse(run('python3', [HELPER].concat(args)).trim().split('\n').pop());

// ---------------------------------------------------------------- a valódi szerver
function startServer(tmp) {
  const proc = spawn('python3', [HELPER, 'serve', tmp], { cwd: ROOT, stdio: ['pipe', 'pipe', 'pipe'] });
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
  const next = () => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('a szerver nem válaszolt: ' + err)), 60000);
    waiters.push((v) => { clearTimeout(timer); resolve(v); });
  });
  return next().then((info) => Object.assign(info, {
    newCode: () => { const p = next(); proc.stdin.write('code\n'); return p.then((v) => v.code); },
    stop: () => new Promise((resolve) => { proc.on('exit', resolve); proc.stdin.write('quit\n'); setTimeout(() => proc.kill(), 15000); }),
    stderr: () => err
  }));
}

// ---------------------------------------------------------------- oldal-segédek
let browser, srv, ctx, page;
const errors = [];
const http4xx = [];
const requests = [];          // a felület /api/* kérései (útvonal, törzs) — a hívási sorrend ellenőrzéséhez
const allowed4xx = [];         // az elvárt hibaválaszok (pl. GATE_BLOCKED) — ezek nem hibák
async function txt(p, sel) { return (await p.textContent(sel)) || ''; }
async function screen(p, id) {
  await p.waitForFunction((x) => {
    const r = window.MA && window.MA.app && window.MA.app.current();
    return r && r.id === x && !document.querySelector('#screen-root > .spinner');
  }, id, { timeout: 20000 });
  await p.waitForTimeout(250);
}
async function go(p, hash, id) {
  await p.evaluate((h) => { window.location.hash = h; }, hash);
  await screen(p, id);
}
async function settle(p) {
  await p.waitForTimeout(400);
  await p.waitForFunction(() => !document.querySelector('#ex-summary .spinner') && !!document.querySelector('#ex-summary .ex-k'), null, { timeout: 10000 });
}
async function openExtraction(p, outcome) {
  await go(p, '#/extraction?outcome=' + outcome, 'extraction');
  await p.waitForSelector('#screen-root[data-screen="extraction"] table[role="grid"]', { timeout: 10000 });
  await settle(p);
}
/** A rács cellájának szelektora: sor = a vizsgálat címkéje (első oszlop), oszlop = az eredeti fejléc-név. */
async function cellSel(p, label, col) {
  const r = await p.evaluate(([l, c]) => {
    const th = Array.from(document.querySelectorAll('.mg-table thead th')).find((x) => { const s = x.querySelector('.mg-cl'); return s && s.textContent === c; });
    const tr = Array.from(document.querySelectorAll('.mg-table tbody tr')).find((x) => { const t = x.querySelector('td .mg-t'); return t && t.textContent === l; });
    return th && tr ? { col: th.dataset.col, uid: tr.dataset.uid } : null;
  }, [label, col]);
  if (!r) { throw new Error('nincs ilyen cella: ' + label + ' / ' + col); }
  return '.mg-table tr[data-uid="' + r.uid + '"] td[data-col="' + r.col + '"]';
}
async function uidOf(p, label) {
  return p.evaluate((l) => { const tr = Array.from(document.querySelectorAll('.mg-table tbody tr')).find((x) => { const t = x.querySelector('td .mg-t'); return t && t.textContent === l; }); return tr ? tr.dataset.uid : null; }, label);
}
async function cellText(p, label, col) { return p.evaluate((s) => { const x = document.querySelector(s); return x ? x.querySelector('.mg-t').textContent : null; }, await cellSel(p, label, col)); }
async function typeCell(p, label, col, value) {
  await p.click(await cellSel(p, label, col));
  await p.keyboard.press('Enter');
  await p.keyboard.press('Control+a');
  await p.keyboard.type(value);
  await p.keyboard.press('Enter');
}
async function saveTable(p) {
  await p.keyboard.press('Control+s');
  await p.waitForFunction(() => /mentve/.test((document.getElementById('ex-status') || {}).textContent || ''), null, { timeout: 10000 });
}
async function pasteRows(p, afterLabel, rows) {
  await p.click(await cellSel(p, afterLabel, p.__firstCol));
  await p.click('#ex-add-row');
  await p.waitForTimeout(150);
  await p.evaluate((x) => {
    const dt = new DataTransfer();
    dt.setData('text/plain', x);
    document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, tsv(rows));
  await settle(p);
}
/** A felület saját API-burkán át (token, fejlécek, hibaboríték) — mint a képernyők. */
async function api(p, method, apiPath, body) {
  return p.evaluate(([m, u, b]) => {
    const url = new URL(u, 'http://x');
    const query = {};
    url.searchParams.forEach((v, k) => { query[k] = v; });
    const opts = { query, toast: false };
    if (b !== null) { opts.body = b; }
    return window.MA.api.request(m, url.pathname, opts).then((env) => ({ ok: true, data: env.data, etag: env.etag }),
      (e) => ({ ok: false, code: e.code, http: e.http, message: e.message, details: e.details }));
  }, [method, apiPath, body === undefined ? null : body]);
}
async function latestRun(p, outcome) {
  const r = await api(p, 'GET', '/api/runs?outcome=' + outcome);
  return r.ok && r.data.runs.length ? r.data.runs[0] : null;
}

// ---------------------------------------------------------------- tesztek
(async () => {
  run('python3', [BUILD]);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ma_e2e_'));
  srv = await startServer(tmp);
  try { browser = await chromium.launch({ headless: true }); } catch (e) { browser = await chromium.launch({ headless: true, executablePath: CHROME_FALLBACK }); }
  ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true, permissions: ['clipboard-read', 'clipboard-write'] });
  await ctx.addInitScript(() => {
    window.__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => { window.__csp.push(e.violatedDirective + ' ' + e.blockedURI); });
  });
  page = await ctx.newPage();
  // a böngésző minden 4xx-választ „Failed to load resource” konzolsorként naplóz — ezeket a HTTP-ellenőrzés kezeli
  // (csak az elvárt 4xx engedett: allowed4xx); minden más konzolhiba hiba
  page.on('console', (m) => { if (m.type() === 'error' && !/^Failed to load resource: the server responded with a status of 4\d\d/.test(m.text())) { errors.push('console: ' + m.text()); } });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => { errors.push('dialog: ' + d.message()); d.dismiss().catch(() => null); });
  page.on('request', (r) => {
    const u = r.url();
    if (u.indexOf(srv.base + '/api/') !== 0) { return; }
    let body = null;
    try { body = r.postData() ? JSON.parse(r.postData()) : null; } catch (e) { body = null; }
    const url = new URL(u);
    requests.push({ method: r.method(), path: url.pathname, query: url.search, body });
  });
  page.on('response', (r) => {
    const u = r.url();
    if (u.indexOf(srv.base) === 0 && r.status() >= 400) { http4xx.push(r.status() + ' ' + r.request().method() + ' ' + u.slice(srv.base.length)); }
  });
  const ctxState = {};

  // ======================================================== 1. projekt megnyitása
  await test('projekt megnyitása: indítókód → token, fejléc, adatvédelmi állapot (A osztály)', async () => {
    await page.goto(srv.url);
    await page.waitForSelector('html[data-ready="1"]', { timeout: 30000 });
    check((await page.evaluate(() => location.hash)).indexOf('launch=') < 0, 'az indítókód eltűnt a címsorból');
    check((await page.evaluate(() => sessionStorage.getItem('mag.token') || '')).length > 10, 'token a sessionStorage-ban (csak ott)');
    const header = await txt(page, '.app-header');
    check(header.indexOf('BCG és Normand') >= 0, 'fejléc: a projekt címe');
    check(header.indexOf('VÉDVE') >= 0 || header.indexOf('A osztály') >= 0 || header.indexOf('védve') >= 0, 'fejléc: adatvédelmi állapot (' + header.replace(/\s+/g, ' ').slice(0, 160) + ')');
    await go(page, '#/project', 'project');
    await page.waitForSelector('#pj-privacy');
    const pj = await txt(page, '#screen-root');
    check(pj.indexOf('A — publikált aggregált') >= 0 || pj.indexOf('A osztály') >= 0, 'projekt-képernyő: adatosztály A');
    const reuse = await page.evaluate((code) => fetch('/api/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ launch_code: code }) }).then((r) => r.status), srv.code);
    allowed4xx.push('403 POST /api/session');
    check(reuse === 403, 'az indítókód másodszor nem használható (403)');
  });

  // ======================================================== 2. BCG: adatbevitel, élő validálás, eredet
  await test('BCG: beillesztés (TSV) → 13 sor; élő validálás: hiba → jelölt cella → javítás; mentés', async () => {
    await openExtraction(page, 'o1');
    page.__firstCol = 'vizsgálat';
    check((await page.$$('.mg-table tbody tr[role="row"]')).length === 10, 'a fájlban 10 sor');
    await pasteRows(page, 'Rosenthal et al 1961', BCG_PASTE);
    check((await page.$$('.mg-table tbody tr[role="row"]')).length === 13, 'beillesztés után 13 sor');
    check((await cellText(page, 'Comstock et al 1976', 'n2')) === '17854', 'a beillesztett cellák szövegként');
    check(/k = 13\/13/.test(await txt(page, '#ex-summary')), 'élő validálás: k = 13/13 (' + (await txt(page, '#ex-summary')).replace(/\s+/g, ' ') + ')');
    // hiba bevitele: esemény > n
    await typeCell(page, 'Aronson 1948', 'esemény1', '600');
    await settle(page);
    const e1 = await cellSel(page, 'Aronson 1948', 'esemény1');
    check((await page.getAttribute(e1, 'aria-invalid')) === 'true', 'V006: a cella aria-invalid');
    const desc = await page.evaluate((s) => { const x = document.querySelector(s); const d = document.getElementById(x.getAttribute('aria-describedby')); return d ? d.textContent : ''; }, e1);
    check(/V006/.test(desc), 'a cella leírása a motor megállapítása (' + desc.slice(0, 80) + ')');
    check(/1 hiba/.test(await txt(page, '#ex-summary')) && /k = 12\/13/.test(await txt(page, '#ex-summary')), 'összesítő: 1 hiba, k = 12/13');
    check(!!(await page.$('#ex-findings li.ex-f[data-code="V006"]')), 'a megállapítás-panelen V006');
    // javítás
    await typeCell(page, 'Aronson 1948', 'esemény1', '4');
    await settle(page);
    check((await page.getAttribute(e1, 'aria-invalid')) === null, 'javítás után a jelzés eltűnt');
    check(/0 hiba/.test(await txt(page, '#ex-summary')) && /k = 13\/13/.test(await txt(page, '#ex-summary')), 'k = 13/13 újra');
    await saveTable(page);
    const disk = fs.readFileSync(path.join(srv.proj, O1), 'utf-8');
    check(disk.indexOf('Comstock et al 1976') >= 0 && disk.indexOf('row_uid') >= 0, 'a CSV a lemezen (row_uid oszloppal)');
  });

  await test('BCG: eredet a forrás-PDF oldalával (dokumentum-jegyzék) — a lefúráshoz', async () => {
    await page.click(await cellSel(page, 'Aronson 1948', 'esemény1'));
    await page.waitForSelector('#ex-prov-doc');
    await page.selectOption('#ex-prov-doc', PDF_BCG);
    await page.fill('#ex-prov-page', '2');
    await page.fill('#ex-prov-locator', 'Table 1');
    await page.click('#ex-prov-save');
    await page.waitForFunction(() => /mentve/.test(document.getElementById('ex-status').textContent), null, { timeout: 8000 });
    const prov = JSON.parse(fs.readFileSync(path.join(srv.proj, '03_adatok', 'o1.prov.json'), 'utf-8'));
    const uid = await uidOf(page, 'Aronson 1948');
    const c = (prov.cells || []).find((x) => x.row_uid === uid && x.field === 'e1');
    check(!!c && c.source && c.source.doc === PDF_BCG && c.source.page === 2, 'o1.prov.json: e1 ← ' + PDF_BCG + ', 2. oldal');
    ctxState.aronsonUid = uid;
  });

  // ======================================================== 3. spec → explore → commit (BCG)
  /** Elemzési terv: a spec a motor opció-metaadatából; automatikus explore; rögzítés (PUT spec + commit-feladat). */
  async function planAndCommit(outcome, extra, label) {
    allowed4xx.push('404 GET /api/specs/' + outcome + '_primary');
    await go(page, '#/analysis?outcome=' + outcome, 'analysis');
    await page.waitForSelector('#plan-form');
    await page.waitForFunction(() => { const e = document.getElementById('plan-status'); return !!e && /kész/.test(e.textContent || '') && /^\d+$/.test(e.getAttribute('data-seq') || ''); }, null, { timeout: 30000 });
    const n0 = requests.length;
    for (const [k, v] of Object.entries(extra || {})) { await page.fill('#opt-' + k, v); }
    if (Object.keys(extra || {}).length) {
      await page.waitForFunction(([vals]) => {
        const st = (document.getElementById('plan-status') || {}).textContent || '';
        const cmd = (document.getElementById('plan-cmd-expanded') || {}).textContent || '';
        return /kész/.test(st) && vals.every((v) => cmd.indexOf(v) >= 0);
      }, [Object.values(extra)], { timeout: 30000 });
    }
    const summary = await txt(page, '#plan-summary');
    const cmd = await txt(page, '#plan-cmd-main');
    check(cmd.indexOf('python ma.py analyze') === 0, label + ': parancs-előnézet (' + cmd + ')');
    const explores = requests.filter((r) => r.path === '/api/analyze' && r.body && r.body.mode === 'explore');
    check(explores.length >= 1 && explores.every((r) => typeof r.body.client_seq === 'number'), label + ': automatikus explore (POST /api/analyze {mode: explore, client_seq})');
    if (Object.keys(extra || {}).length) {
      const after = explores.filter((r) => requests.indexOf(r) >= n0);
      check(after.length >= 1 && Object.entries(extra).every(([k, v]) => after[after.length - 1].body.spec.options[k] === v), label + ': a módosított spec-kel új explore (' + JSON.stringify(extra) + ')');
    }
    check(/\[.*;.*\]/.test(summary), label + ': explore-összegzés a motor szövegével (' + summary.replace(/\s+/g, ' ').slice(0, 90) + ')');
    await page.evaluate(() => document.querySelectorAll('#ma-toasts .toast .toast-close').forEach((b) => b.click()));
    await page.click('#plan-commit');
    await page.waitForFunction(() => /Rögzítve \(commit\)|a rögzítés nem sikerült/.test((document.getElementById('plan-status') || {}).textContent || ''), null, { timeout: 90000 });
    check((await txt(page, '#plan-status')).indexOf('Rögzítve (commit)') >= 0, label + ': sikeres rögzítés (' + (await txt(page, '#plan-status')) + ')');
    const commits = requests.filter((r) => r.path === '/api/analyze' && r.body && r.body.mode === 'commit');
    const puts = requests.filter((r) => r.method === 'PUT' && r.path === '/api/specs/' + outcome + '_primary');
    check(commits.length >= 1 && puts.length >= 1, label + ': PUT /api/specs/' + outcome + '_primary, majd POST /api/analyze {mode: commit}');
    await page.evaluate(() => document.querySelectorAll('#ma-toasts .toast .toast-close').forEach((b) => b.click()));
    const run = await latestRun(page, outcome);
    check(!!run && run.mode === 'commit' && /^\d{8}T\d{6}Z-[0-9a-f]{6}$/.test(run.run_id), label + ': commit-futás run_id-vel');
    check(fs.existsSync(path.join(srv.proj, run.dir, 'run.json')) && fs.existsSync(path.join(srv.proj, '05_elemzes', 'specs', outcome + '_primary.json')), label + ': run.json és a mentett spec a lemezen');
    return { run, summary };
  }

  /** Az eredmény-képernyő számai = a futás motor-szövegei (plot_data.json) = a results.json a motor formázóival. */
  async function resultsFidelity(outcome, run, exploreSummary, label) {
    const ex = py(['expected', srv.proj, run.dir]);
    await go(page, '#/results?outcome=' + outcome + '&run=' + run.run_id + '&view=forest', 'results');
    await page.waitForSelector('#results-summary .res-line', { timeout: 20000 });
    await page.waitForSelector('#results-plot-panel g.fp-study', { timeout: 20000 });
    const line = await txt(page, '.res-line');
    const want = [ex.measure + ' ' + ex.plot.display_text.hu, ex.plot.p_text.hu, 'PI ' + ex.plot.pi_text.hu, 'I² ' + ex.plot.i2_text.hu,
      'τ² ' + ex.plot.tau2_text.hu, 'k ' + ex.k];
    want.forEach((w) => check(line.indexOf(w) >= 0, label + ': összegző sáv „' + w + '” (' + line.replace(/\s+/g, ' ').slice(0, 160) + ')'));
    ['display_text', 'pi_text', 'p_text', 'i2_text', 'tau2_text'].forEach((k) => {
      check(JSON.stringify(ex.plot[k]) === JSON.stringify(ex.recomputed[k]), label + ': számhűség — ' + k + ' (plot_data) = a results.json száma a motor formázójával (' + JSON.stringify(ex.recomputed[k]) + ')');
    });
    check(ex.k === ex.recomputed.k, label + ': k = results.primary.k');
    check(ex.report_has_display && ex.svg_has_display, label + ': a report.md és a motor forest.svg-je ugyanazt a szöveget írja');
    check(exploreSummary.indexOf(ex.plot.display_text.hu) >= 0, label + ': az explore ugyanazt a számot mutatta (' + exploreSummary.replace(/\s+/g, ' ').slice(0, 80) + ')');
    check((await txt(page, '.res-het')) === ex.plot.het_text.hu, label + ': heterogenitás-szöveg szó szerint');
    check((await txt(page, '#results-run-badge')).indexOf('AKTUÁLIS') >= 0, label + ': AKTUÁLIS jelvény');
    // forest: minden jelölő a nézetmodellből, a szöveg a motoré
    const geo = await page.$$eval('#results-plot-panel g.fp-study', (gs) => gs.map((g) => ({ uid: g.getAttribute('data-uid'), y: Number(g.getAttribute('data-y')),
      lo: Number(g.getAttribute('data-lo')), hi: Number(g.getAttribute('data-hi')), eff: (g.querySelector('.fp-effect') || {}).textContent })));
    check(geo.length === ex.k, label + ': forest — ' + ex.k + ' vizsgálat-jelölő');
    const by = {};
    ex.studies.forEach((s) => { by[s.row_uid] = s; });
    check(ex.studies_recomputed_ok, label + ': a vizsgálatonkénti display_text = a motor formázója a display-értékekből');
    check(geo.every((g) => by[g.uid] && g.y === by[g.uid].y && g.lo === by[g.uid].lo && g.hi === by[g.uid].hi && g.eff === by[g.uid].display_text.hu),
      label + ': forest data-y/lo/hi és a szöveg = plot_data.json (bitre)');
    if (ex.plot.subgroup_test) {
      check((await txt(page, '#results-plot-panel .fp-sgtest')) === ex.plot.subgroup_test.hu, label + ': alcsoport-különbség a motor szövegével');
    }
    // funnel
    await page.click('[role="tab"][data-tab="funnel"]');
    await page.waitForSelector('#results-plot-panel .fn-svg', { timeout: 10000 });
    check((await page.$$('#results-plot-panel .fn-pt')).length === ex.k && (await page.$$('#results-plot-panel .fn-contour')).length === ex.n_contours,
      label + ': funnel — ' + ex.k + ' pont, ' + ex.n_contours + ' kontúr-poligon a motorból');
    check((await txt(page, '#results-plot-panel .fn-tests')) === (ex.funnel_tests_text || {}).hu, label + ': funnel — a torzítás-tesztek szövege');
    // Doi
    await page.click('[role="tab"][data-tab="doi"]');
    await page.waitForSelector('#results-plot-panel .doi-svg', { timeout: 10000 });
    check((await txt(page, '#results-plot-panel .doi-lfk')) === ex.doi_lfk_text.hu && (await page.$$('#results-plot-panel .doi-pt')).length === ex.k,
      label + ': Doi — LFK a motor szövegével (' + ex.doi_lfk_text.hu + '), ' + ex.k + ' pont');
    // LOO
    await page.click('[role="tab"][data-tab="loo"]');
    await page.waitForSelector('#results-plot-panel .sr-row', { timeout: 10000 });
    const loo = await page.$$eval('#results-plot-panel .sr-row .fp-effect', (els) => els.map((e) => e.textContent));
    check(loo.length === ex.k && ex.loo.every((e) => loo.indexOf(e.display_text.hu) >= 0), label + ': LOO — ' + ex.k + ' sor, a motor szövegeivel');
    // befolyás
    await page.click('[role="tab"][data-tab="influence"]');
    await page.waitForSelector('#results-plot-panel .inf-table', { timeout: 10000 });
    const inf = await txt(page, '#results-plot-panel .inf-table');
    check((await page.$$('#results-plot-panel .inf-table tbody tr')).length === ex.k && (await txt(page, '#results-plot-panel .inf-summary')) === ex.influence_text.hu,
      label + ': befolyás — táblázat és a motor összegzése');
    check(ex.influence.every((e) => !e.rstudent_text || inf.indexOf(e.rstudent_text.hu) >= 0), label + ': befolyás — rstudent a motor szövegével');
    await page.click('[role="tab"][data-tab="forest"]');
    await page.waitForSelector('#results-plot-panel g.fp-study');
    return ex;
  }

  /** Kattintás egy vizsgálatra → lefúrás: a kinyerési sor, a PDF-oldal aláírt URL-en, ugrás a sorhoz. */
  async function drill(outcome, ex, studyLabel, pdfDoc, pdfPage, cells, label) {
    const uid = ex.study_by_label[studyLabel];
    check(!!uid, label + ': a vizsgálat a nézetmodellben (' + studyLabel + ')');
    await page.click('#results-plot-panel g.fp-study[data-uid="' + uid + '"] .fp-label');
    await page.waitForSelector('#drilldown', { timeout: 10000 });
    check((await txt(page, '#drilldown .drill-title')) === 'Lefúrás: ' + studyLabel, label + ': lefúrás-panel (' + studyLabel + ')');
    check((await page.evaluate(() => location.hash)).indexOf('row=' + uid) >= 0, label + ': a kiválasztás az URL-ben (row=)');
    await page.waitForFunction((v) => { const e = document.querySelector('#drilldown .drill-cells'); return !!e && v.every((x) => e.textContent.indexOf(x) >= 0); }, cells, { timeout: 10000 });
    check(true, label + ': a kinyerési sor cellái a lefúrásban (' + cells.join(', ') + ')');
    check((await txt(page, '#drilldown .drill-page-text')).indexOf(pdfPage + '. oldal') >= 0, label + ': az oldalszám szövegként is');
    const pdfBtn = await page.$('#drilldown .drill-pdf');
    check(!!pdfBtn && (await pdfBtn.getAttribute('data-doc')) === pdfDoc, label + ': „PDF” gomb a forrás-dokumentummal');
    // a felület window.open('')-nel nyit új lapot (felugróablak-szűrő), majd location.replace(<aláírt URL>#page=N).
    // Fej nélküli Chromiumban nincs PDF-néző, a lap about:blank marad — ezért a teszt a window.open visszaadott
    // ablakát vékony burokkal figyeli (a valódi lap navigál), és a hálózati kérést is elkapja.
    await page.evaluate(() => {
      window.__opened = [];
      if (window.__openWrapped) { return; }
      window.__openWrapped = true;
      const orig = window.open;
      window.open = function (u, tgt, feat) {
        const w = orig.call(window, u, tgt, feat);
        if (!w) { return w; }
        return { set opener(v) { try { w.opener = v; } catch (e) { /* nem kritikus */ } },
          location: { replace: (x) => { window.__opened.push(x); w.location.replace(x); } }, close: () => w.close() };
      };
    });
    const reqP = ctx.waitForEvent('request', { predicate: (r) => r.url().indexOf(srv.base + '/f/') === 0, timeout: 15000 });
    const [popup] = await Promise.all([ctx.waitForEvent('page'), page.click('#drilldown .drill-pdf')]);
    const fReq = await reqP;
    const opened = await page.evaluate(() => window.__opened.slice());
    const purl = srv.base + (opened[0] || '');
    check(opened.length === 1 && fReq.url() === purl.split('#')[0], label + ': az új lap az aláírt URL-re navigál (' + (opened[0] || '—') + ')');
    const pm = /^\/f\/([^/]+)\/(\d+)\/[A-Za-z0-9_-]+#page=(\d+)$/.exec(opened[0] || '');
    check(!!pm && decodeURIComponent(pm[1]) === pdfDoc && pm[3] === String(pdfPage) && Number(pm[2]) > Date.now() / 1000,
      label + ': /f/<dokumentum>/<lejárat>/<aláírás>#page=' + pdfPage + ' (a forrás-oldal; a lejárat a jövőben)');
    const resp = await ctx.request.get(purl.split('#')[0]);
    const body = await resp.body();
    const file = fs.readFileSync(path.join(srv.proj, pdfDoc.replace(/^file:/, '')));
    check(resp.status() === 200 && /application\/pdf/.test(resp.headers()['content-type'] || '') && Buffer.compare(body, file) === 0,
      label + ': a PDF a szerverről (200, application/pdf, bájtra a forrásfájl)');
    check(/frame-ancestors 'none'/.test(resp.headers()['content-security-policy'] || '') && resp.headers()['x-content-type-options'] === 'nosniff',
      label + ': a fájl-válasz biztonsági fejlécei (CSP frame-ancestors, nosniff)');
    const forged = await ctx.request.get(purl.split('#')[0].replace(/\/([^/]+)$/, '/' + 'A'.repeat(43)));
    allowed4xx.push('403 GET /f/');
    check(forged.status() === 403, label + ': hamisított aláírás → 403');
    await popup.close();
    // ugrás a kinyerési sorhoz
    const href = await page.getAttribute('#drilldown .drill-jump', 'href');
    check(href === '#/extraction?outcome=' + outcome + '&row=' + uid, label + ': „Ugrás a sorhoz” link (' + href + ')');
    await page.click('#drilldown .drill-jump');
    await screen(page, 'extraction');
    await page.waitForFunction((u) => { const a = document.activeElement; const tr = a && a.closest ? a.closest('tr') : null; return !!tr && tr.dataset.uid === u; }, uid, { timeout: 10000 });
    check(true, label + ': a kinyerés-képernyőn a sor első cellája fókuszban (' + uid + ')');
    return uid;
  }

  await test('BCG: elemzési terv (alcsoport: allokáció, kumulatív: év) → explore → commit', async () => {
    ctxState.o1 = await planAndCommit('o1', { subgroup: 'allokáció', cumulative: 'év' }, 'BCG');
  });
  await test('BCG: eredmények — forest/funnel/Doi/LOO/befolyás a motor szövegeivel; számhűség (6.7)', async () => {
    ctxState.ex1 = await resultsFidelity('o1', ctxState.o1.run, ctxState.o1.summary, 'BCG');
  });
  await test('BCG: lefúrás — kinyerési sor, forrás-PDF a 2. oldalon (aláírt URL), ugrás a sorhoz', async () => {
    await drill('o1', ctxState.ex1, 'Aronson 1948', PDF_BCG, 2, ['4', '123', '11', '139'], 'BCG');
  });

  // ======================================================== 4. Normand: tizedesvesszős beillesztés, átváltó, „Nem hiba”
  await test('Normand: beillesztés (tizedesvessző) → 9 sor; élő validálás: a hiányzó átlag/SD (V002) jelölve', async () => {
    await openExtraction(page, 'o2');
    page.__firstCol = 'study';
    check((await page.$$('.mg-table tbody tr[role="row"]')).length === 1, 'a fájlban 1 sor (Edinburgh)');
    await pasteRows(page, 'Edinburgh', NORMAND_PASTE);
    check((await page.$$('.mg-table tbody tr[role="row"]')).length === 9, 'beillesztés után 9 sor');
    check((await cellText(page, 'Umea', 'sd2')) === '27,0', 'a „27,0” szövegként (tizedesvessző) maradt');
    const m1 = await cellSel(page, 'Montreal-Home', 'm1');
    check((await page.getAttribute(m1, 'aria-invalid')) === 'true', 'Montreal-Home m1 hiányzik → a cella jelölve');
    check(!!(await page.$('#ex-findings li.ex-f[data-code="V002"]')) && /k = 8\/9/.test(await txt(page, '#ex-summary')), 'V002 a panelen; k = 8/9 (a sor kimarad)');
    // a 2. hiba-próba: negatív SD → V-hiba → javítás
    await typeCell(page, 'Uppsala', 'sd1', '-27,0');
    await settle(page);
    const sd = await cellSel(page, 'Uppsala', 'sd1');
    check((await page.getAttribute(sd, 'aria-invalid')) === 'true', 'negatív SD → a cella jelölve (' + (await txt(page, '#ex-summary')).replace(/\s+/g, ' ') + ')');
    await typeCell(page, 'Uppsala', 'sd1', '27,0');
    await settle(page);
    check((await page.getAttribute(sd, 'aria-invalid')) === null, 'javítás után a jelzés eltűnt');
  });

  await test('Normand: átváltó (medián/IQR → átlag/SD) — a motor számai, becsült jelölés + eredet; mentés', async () => {
    await page.click(await cellSel(page, 'Montreal-Home', 'm1'));
    await page.click('#ex-convert');
    await page.waitForSelector('#cv-modal');
    check((await page.inputValue('#cv-kind')) === 'median_to_mean_sd' && (await page.inputValue('#cv-in-n')) === '8', 'típus: medián/IQR; n a sorból (n1 = 8)');
    await page.fill('#cv-in-median', '12,5');
    await page.fill('#cv-in-q1', '10');
    await page.fill('#cv-in-q3', '16');
    const direct = py(['convert', JSON.stringify({ schema: 'szk.ma.convert-request/v1', kind: 'median_to_mean_sd', method: 'luo',
      inputs: { n: '8', median: '12,5', q1: '10', q3: '16' }, target: { dataset: O2, row_uid: await uidOf(page, 'Montreal-Home'), fields: { mean: 'm1', sd: 'sd1' }, decimal_mark: ',' } })]);
    await page.waitForFunction((v) => (document.getElementById('cv-result') || {}).textContent.indexOf(v) >= 0, direct.outputs_text.mean.hu, { timeout: 10000 });
    const res = await txt(page, '#cv-result');
    check(res.indexOf(direct.outputs_text.mean.hu) >= 0 && res.indexOf(direct.outputs_text.sd.hu) >= 0, 'a modális a motor szövegeit mutatja (' + direct.outputs_text.mean.hu + '; ' + direct.outputs_text.sd.hu + ')');
    check(direct.estimated === true && res.indexOf('BECSÜLT') >= 0, 'BECSÜLT érték (a motor döntése)');
    await page.selectOption('#cv-doc', PDF_NORMAND);
    await page.fill('#cv-page', '1');
    await page.fill('#cv-locator', 'Table 2');
    await page.click('#cv-modal .modal-actions .btn-primary');
    await page.waitForFunction(() => !document.getElementById('cv-modal'), null, { timeout: 10000 });
    await settle(page);
    check((await cellText(page, 'Montreal-Home', 'm1')) === direct.cell_text.mean && (await cellText(page, 'Montreal-Home', 'sd1')) === direct.cell_text.sd,
      'a cellákba a motor cell_text-je került (tizedesvessző: ' + direct.cell_text.mean + ', ' + direct.cell_text.sd + ')');
    check((await cellText(page, 'Montreal-Home', 'estimated')) === 'igen', 'a sor estimated = igen');
    check((await txt(page, (await cellSel(page, 'Montreal-Home', 'm1')) + ' .mg-m')).indexOf('◆') >= 0, 'a cella becsült jelölést kap (◆)');
    check((await page.getAttribute(await cellSel(page, 'Montreal-Home', 'm1'), 'aria-invalid')) === null && /k = 9\/9/.test(await txt(page, '#ex-summary')), 'a V002 eltűnt, k = 9/9');
    await saveTable(page);
    const prov = JSON.parse(fs.readFileSync(path.join(srv.proj, '03_adatok', 'o2.prov.json'), 'utf-8'));
    const uid = await uidOf(page, 'Montreal-Home');
    const cells = (prov.cells || []).filter((c) => c.row_uid === uid && (c.field === 'm1' || c.field === 'sd1'));
    check(cells.length === 2 && cells.every((c) => c.method === 'estimated' && c.estimated === true && c.source && c.source.doc === PDF_NORMAND && c.source.page === 1),
      'o2.prov.json: m1 és sd1 — estimated, forrás: ' + PDF_NORMAND + ', 1. oldal');
    const csv = fs.readFileSync(path.join(srv.proj, O2), 'utf-8');
    check(csv.indexOf(direct.cell_text.mean) >= 0 && csv.indexOf('27,0') >= 0 && /;/.test(csv.split('\n')[0]), 'a CSV pontosvesszős, tizedesvesszős (' + direct.cell_text.mean + ')');
    ctxState.montrealUid = uid;
    ctxState.conv = direct;
  });

  await test('Normand: „Nem hiba — indoklás” (V020) → naplózott döntés, a megállapítás indokolt (a motor is jelöli)', async () => {
    const li = '#ex-findings li.ex-f[data-code="V020"]';
    await page.waitForSelector(li);
    await page.click(li + ' .ex-f-ack');
    await page.waitForSelector('[role="dialog"] #ex-ack-rationale');
    await page.click('[role="dialog"] .modal-actions .btn-primary');
    check((await page.getAttribute('#ex-ack-rationale', 'aria-invalid')) === 'true', 'indoklás nélkül nem küldhető');
    await page.fill('#ex-ack-rationale', 'A Montreal-Home kar valóban kicsi (n = 8); a közlemény szerint így tervezték, nem adathiba.');
    await page.click('[role="dialog"] .modal-actions .btn-primary');
    await page.waitForSelector(li + '.is-ack', { timeout: 10000 });
    const dec = requests.filter((r) => r.path === '/api/log/decision').pop();
    check(!!dec && dec.body.agent === 'user' && dec.body.kb_refs.indexOf('V020') >= 0 && dec.body.context.row_uid === ctxState.montrealUid && dec.body.context.code === 'V020',
      'POST /api/log/decision {agent: user, kb_refs: [V020], context: {code, row_uid}}');
    const m = /indokolt #(\d+)/.exec(await txt(page, li));
    check(!!m, 'a jelzés „indokolt #<id>” (' + (await txt(page, li)).replace(/\s+/g, ' ').slice(0, 80) + ')');
    // a szerver felől is: a mentett tábla (GET /api/table) piszkozatként validálva → a motor acknowledged-je
    const tb = await api(page, 'GET', '/api/table?dataset=' + encodeURIComponent(O2));
    const v = await api(page, 'POST', '/api/validate', { schema: 'szk.ma.validate-request/v1', measure: 'MD', dataset: O2,
      table: { header: ['row_uid'].concat(tb.data.header), rows: tb.data.rows.map((r) => [r.row_uid].concat(r.cells)) } });
    const decs = await api(page, 'GET', '/api/log/decision');
    const items = decs.ok ? (decs.data.items || []) : [];
    const row = items.find((d) => m && d.id === Number(m[1]));
    check(!!row && row.context && row.context.code === 'V020' && row.actor === 'user', 'a döntés a projektnaplóban, gépi kontextussal (actor: user)');
    const f = v.ok ? v.data.findings.find((x) => x.code === 'V020') : null;
    check(!!f && f.acknowledged === Number(m && m[1]), 'POST /api/validate: acknowledged = #' + (m && m[1]) + ' (' + (v.ok ? JSON.stringify(f && f.acknowledged) : v.code + ' ' + v.message) + ')');
  });

  await test('Normand: elemzési terv → explore → commit (MD)', async () => {
    ctxState.o2 = await planAndCommit('o2', {}, 'Normand');
  });
  await test('Normand: eredmények — forest/funnel/Doi/LOO/befolyás a motor szövegeivel; számhűség (6.7); becsült jelölés', async () => {
    ctxState.ex2 = await resultsFidelity('o2', ctxState.o2.run, ctxState.o2.summary, 'Normand');
    const uid = ctxState.ex2.study_by_label['Montreal-Home'];
    check(ctxState.ex2.studies.find((s) => s.row_uid === uid).estimated === true && !!(await page.$('#results-plot-panel g.fp-study[data-uid="' + uid + '"] .fp-flag.is-estimated')),
      'Normand: a becsült sor jelölve a forestben (◆)');
  });
  await test('Normand: lefúrás — kinyerési sor (a becsült értékekkel), forrás-PDF az 1. oldalon', async () => {
    await drill('o2', ctxState.ex2, 'Montreal-Home', PDF_NORMAND, 1, [ctxState.conv.cell_text.mean, ctxState.conv.cell_text.sd, '8'], 'Normand');
  });

  // ======================================================== 5. megállapítás + kapu
  await test('napló: új blocker-megállapítás → a kapu előre tiltja (megnevezett ok) → lezárás → PASS engedett', async () => {
    await go(page, '#/log', 'log');
    await page.waitForSelector('#log-findings');
    await page.click('#nf-toggle');
    await page.selectOption('#nf-sev', 'blocker');
    await page.selectOption('#nf-stage', 'S05');
    await page.fill('#nf-title', 'Montreal-Home: medián/IQR-ből becsült átlag — érzékenységi elemzés kell');
    await page.fill('#nf-kb', 'V020');
    await page.click('#nf-submit');
    await page.waitForSelector('.toast.is-success, .toast.is-warning', { timeout: 10000 });
    const nf = requests.filter((r) => r.path === '/api/log/finding' && r.method === 'POST').pop();
    check(!!nf && nf.body.severity === 'blocker' && nf.body.stage === 'S05' && nf.body.agent === 'user', 'POST /api/log/finding {blocker, S05, agent: user}');
    const list = await api(page, 'GET', '/api/log/finding?status=open');
    const f1 = (list.data.items || []).find((x) => /Montreal-Home/.test(x.title));
    check(!!f1, 'a megállapítás a naplóban (#' + (f1 && f1.id) + ')');
    ctxState.f1 = f1.id;
    await page.evaluate(() => document.querySelectorAll('#ma-toasts .toast .toast-close').forEach((b) => b.click()));
    await go(page, '#/log?tab=checkpoint&stage=S05', 'log');
    await page.waitForSelector('#log-checkpoints');
    await page.selectOption('#log-gate-verdict', 'PASS');
    await page.waitForFunction(() => document.getElementById('log-gate-submit').disabled === true, null, { timeout: 10000 });
    check((await txt(page, '#log-gate-pre')).indexOf('#' + f1.id) >= 0 && (await page.getAttribute('#log-gate-submit', 'aria-describedby')) === 'log-gate-pre',
      'S05 PASS: tiltva, az ok (#' + f1.id + ') megnevezve és a gombhoz kötve');
    // lezárás a felületen
    await go(page, '#/log', 'log');
    await page.waitForSelector('#log-row-' + f1.id);
    await page.click('#log-row-' + f1.id + ' .log-resolve');
    await page.waitForSelector('#rs-text');
    await page.check('[role="dialog"] input[value="fixed"]');
    await page.fill('#rs-text', 'Érzékenységi elemzés tervezve (becsült sor nélkül), a 05_elemzes alatt.');
    await page.click('[role="dialog"] .modal-actions .btn-primary');
    await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), null, { timeout: 10000 });
    const rs = requests.filter((r) => r.path === '/api/log/resolve').pop();
    check(!!rs && rs.body.id === f1.id && rs.body.status === 'fixed', 'POST /api/log/resolve {id, status: fixed}');
    await go(page, '#/log?tab=checkpoint&stage=S05', 'log');
    await page.waitForSelector('#log-checkpoints');
    await page.selectOption('#log-gate-verdict', 'PASS');
    await page.waitForFunction(() => /nincs nyitott blocker/.test(document.getElementById('log-gate-pre').textContent) && !document.getElementById('log-gate-submit').disabled, null, { timeout: 10000 });
    check(true, 'lezárás után az S05 PASS engedélyezett');
  });

  await test('kapu: egy párhuzamos „ágens” (CLI) blockert rögzít → GATE_BLOCKED szó szerint → lezárás → PASS rögzítve', async () => {
    // a felület előzetes ellenőrzése után a CLI rögzít egy blockert (ugyanabba a projektnaplóba)
    run('python3', ['ma.py', 'project', 'finding', srv.proj, '--agent', 'reviewer', '--severity', 'blocker', '--stage', 'S05',
      '--title', 'Ágens: a Normand-adatok SD-oszlopa SE lehet (Uppsala)']);
    await page.fill('#log-gate-summary', 'Kinyerés ellenőrizve (BCG, Normand)');
    allowed4xx.push('409 POST /api/log/checkpoint');
    await page.click('#log-gate-submit');
    await page.waitForSelector('#log-gate-blocked[role="alert"]', { timeout: 10000 });
    const blk = await txt(page, '#log-gate-blocked');
    check(blk.indexOf("1 nyitott 'blocker' megállapítás van — ennél a szakasznál (S05); PASS nem adható.") >= 0, 'GATE_BLOCKED: a motor üzenete szó szerint');
    check(blk.indexOf('SD-oszlopa SE lehet') >= 0, 'a blockerek listája (az ágens megállapítása)');
    const list = await api(page, 'GET', '/api/log/finding?status=open');
    const f2 = (list.data.items || []).find((x) => /SD-oszlopa/.test(x.title));
    check(!!f2 && f2.agent === 'reviewer', 'az ágens megállapítása a naplóban (#' + (f2 && f2.id) + ')');
    await go(page, '#/log', 'log');
    await page.waitForSelector('#log-row-' + f2.id, { timeout: 10000 });
    await page.click('#log-row-' + f2.id + ' .log-resolve');
    await page.waitForSelector('#rs-text');
    await page.check('[role="dialog"] input[value="invalid"]');
    await page.fill('#rs-text', 'A közlemény 2. táblázata szerint SD (nem SE); ellenőrizve.');
    await page.click('[role="dialog"] .modal-actions .btn-primary');
    await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), null, { timeout: 10000 });
    await go(page, '#/log?tab=checkpoint&stage=S05', 'log');
    await page.waitForSelector('#log-checkpoints');
    await page.selectOption('#log-gate-verdict', 'PASS');
    await page.waitForFunction(() => !document.getElementById('log-gate-submit').disabled, null, { timeout: 10000 });
    await page.fill('#log-gate-summary', 'Kinyerés ellenőrizve (BCG, Normand)');
    await page.click('#log-gate-submit');
    await page.waitForFunction(() => /Ellenőrzőpont rögzítve/.test(document.getElementById('log-gate-result').textContent), null, { timeout: 10000 });
    const cps = await api(page, 'GET', '/api/log/checkpoint');
    const cp = (cps.data.items || []).find((c) => c.verdict === 'PASS' && /S05/.test(String(c.stage_id || c.stage || '')));
    check(!!cp && cp.actor === 'user', 'PASS rögzítve a projektnaplóban (S05, actor: user)');
  });

  // ======================================================== 6. project audit: adatmódosítás → elavult futás (X001)
  await test('project audit: a BCG-adat módosítása után a futás elavult — X001 az áttekintésben, a naplóban, az eredményeknél; FINAL tiltva', async () => {
    await openExtraction(page, 'o1');
    page.__firstCol = 'vizsgálat';
    await typeCell(page, 'Aronson 1948', 'szélesség', '45');
    await settle(page);
    await saveTable(page);
    const audit = py(['audit', srv.proj]);
    const x001 = (audit.findings || []).filter((f) => f.code === 'X001');
    check(x001.length >= 1 && x001.some((f) => JSON.stringify(f).indexOf('o1') >= 0), 'api.project_audit: X001 az o1-re');
    await go(page, '#/overview', 'overview');
    await page.waitForSelector('#ov-next li[data-code="X001"]', { timeout: 15000 });
    const next = await txt(page, '#ov-next li[data-code="X001"]');
    check(next.indexOf('X001') >= 0 && (await page.getAttribute('#ov-next li[data-code="X001"] .ov-act', 'href')) === '#/analysis?outcome=o1', 'áttekintés: X001 → újrafuttatás az Elemzés képernyőn (o1)');
    check(next.indexOf('python ma.py analyze --spec 05_elemzes/specs/o1_primary.json') >= 0, 'áttekintés: a javasolt parancs a motor auditjából');
    await page.waitForSelector('#overview-outcomes');
    const ov = await txt(page, '#overview-outcomes');
    check(ov.indexOf(ctxState.ex2.plot.display_text.hu) >= 0, 'áttekintés: a Normand-kimenet a motor szövegével');
    check(ov.indexOf('ELAVULT') >= 0, 'áttekintés: az o1 futása ELAVULT');
    await go(page, '#/results?outcome=o1&run=' + ctxState.o1.run.run_id, 'results');
    await page.waitForSelector('.res-stale', { timeout: 15000 });
    check((await txt(page, '#results-run-badge')).indexOf('ELAVULT') >= 0 && (await txt(page, '.res-stale')).indexOf('X001') >= 0, 'eredmények: ELAVULT + X001');
    check((await txt(page, '.res-line')).indexOf(ctxState.ex1.plot.display_text.hu) >= 0, 'a régi futás a saját (változatlan) számával');
    await go(page, '#/log?tab=audit', 'log');
    await page.waitForSelector('#log-audit li[data-code="X001"]', { timeout: 15000 });
    check((await txt(page, '#log-audit')).indexOf('python ma.py analyze --spec') >= 0, 'napló / X-szabályok: X001 a javasolt paranccsal');
    await go(page, '#/log?tab=checkpoint&stage=FINAL', 'log');
    await page.waitForSelector('#log-checkpoints');
    await page.selectOption('#log-gate-verdict', 'PASS');
    await page.waitForFunction(() => /X001/.test(document.getElementById('log-gate-pre').textContent) && document.getElementById('log-gate-submit').disabled, null, { timeout: 15000 })
      .catch(async (e) => { console.log('    [FINAL] előzetes ellenőrzés: ' + (await txt(page, '#log-gate-pre'))); throw e; });
    check(true, 'FINAL PASS: tiltva az X001 audit-hiba miatt (megnevezve; FINAL-kontextusban, mint a motor kapuja)');
    const cur = await api(page, 'GET', '/api/audit/project');
    const curX = (cur.data.findings || []).find((f) => f.code === 'X001');
    check(!!curX && curX.severity === 'warning', 'a mostani szakaszban (S08 előtt) az X001 figyelmeztetés — a FINAL-nál hiba (eszkaláció)');
    const direct = await api(page, 'POST', '/api/log/checkpoint', { stage: 'FINAL', agent: 'user', verdict: 'PASS', summary: 'kísérlet' });
    allowed4xx.push('409 POST /api/log/checkpoint');
    check(!direct.ok && direct.code === 'GATE_BLOCKED' && JSON.stringify(direct.details || {}).indexOf('X001') >= 0, 'a szerver is elutasítja: GATE_BLOCKED, audit_errors: X001');
  });

  // ======================================================== 7. audit-export és pillanatkép
  await test('audit-export: ZIP kétszer → azonos sha256; letöltés; manifest és ép tevékenységlánc', async () => {
    await go(page, '#/export', 'export');
    await page.waitForSelector('#exp-audit');
    const shas = [];
    for (let i = 0; i < 2; i++) {
      await page.click('#exp-audit');
      await page.waitForSelector('#exp-audit-result .exp-sha', { timeout: 60000 });
      shas.push(await txt(page, '#exp-audit-result .exp-sha'));
      if (i === 0) { await page.evaluate(() => { document.getElementById('exp-audit-result').remove(); }); }
    }
    check(/^[0-9a-f]{64}$/.test(shas[0]) && shas[0] === shas[1], 'audit-ZIP kétszer → azonos sha256 (' + shas[0].slice(0, 12) + '…)');
    const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#exp-audit-download')]);
    const zipPath = path.join(tmp, 'audit.zip');
    await dl.saveAs(zipPath);
    const zbytes = fs.readFileSync(zipPath);
    check(crypto.createHash('sha256').update(zbytes).digest('hex') === shas[0] && /\.zip$/.test(dl.suggestedFilename()), 'a letöltött ZIP sha256-ja egyezik (' + dl.suggestedFilename() + ')');
    const z = JSON.parse(run('python3', ['-c', [
      'import json, sys, zipfile, tempfile, os',
      'sys.path.insert(0, ".")',
      'from metaelemzes import api',
      'z = zipfile.ZipFile(sys.argv[1])',
      'names = z.namelist()',
      'm = json.loads(z.read("manifest.json"))',
      'd = tempfile.mkdtemp()',
      'act = [n for n in names if n.endswith("activity.jsonl")]',
      'ok = None',
      'if act:',
      '    os.makedirs(os.path.join(d, "07_ellenorzes"), exist_ok=True)',
      '    open(os.path.join(d, "07_ellenorzes", "activity.jsonl"), "wb").write(z.read(act[0]))',
      '    ok = api.project_activity(d)["ok"]',
      'print(json.dumps({"names": names, "schema": m.get("schema"), "files": len(m.get("files") or []), "chain_ok": ok,',
      '                  "pdf": [n for n in names if n.lower().endswith(".pdf")], "privat": [n for n in names if "_privat" in n]}))'
    ].join('\n'), zipPath]).trim());
    check(z.names.indexOf('manifest.json') >= 0 && z.files > 0 && /audit-bundle/.test(z.schema || ''), 'manifest.json (szk.ma.audit-bundle; ' + z.files + ' fájl)');
    check(z.chain_ok === true, 'a ZIP-beli activity.jsonl hash-lánca ép');
    check(z.pdf.length === 0 && z.privat.length === 0, 'nincs PDF és _privat/ a csomagban');
    check(z.names.some((n) => /rerun\.(sh|cmd)$/.test(n)), 'rerun.sh / rerun.cmd');
  });

  await test('pillanatkép: tudomásulvétel → letöltés → file://-ból NULLA hálózati kérés, nulla CSP-sértés; ugyanazok a számok', async () => {
    check(await page.$eval('#exp-snapshot', (b) => b.disabled), 'tudomásulvétel nélkül tiltva');
    await page.check('#exp-ack');
    await page.click('#exp-snapshot');
    await page.waitForSelector('#exp-snapshot-download', { timeout: 60000 });
    const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#exp-snapshot-download')]);
    const snapPath = path.join(tmp, 'letoltott.snapshot.html');
    await dl.saveAs(snapPath);
    check(/\.snapshot\.html$/.test(dl.suggestedFilename()), 'letöltés csatolmányként: ' + dl.suggestedFilename());
    const html = fs.readFileSync(snapPath, 'utf-8');
    check(html.indexOf("connect-src 'none'") >= 0 && html.indexOf('%PDF-1.') < 0 && html.indexOf('/Type /Catalog') < 0, 'CSP connect-src \'none\'; PDF-tartalom nincs benne');
    const c2 = await browser.newContext();
    await c2.addInitScript(() => {
      window.__csp = [];
      document.addEventListener('securitypolicyviolation', (e) => { window.__csp.push(e.violatedDirective + ' ' + e.blockedURI); });
    });
    const reqs = [];
    const errs2 = [];
    await c2.route('**/*', (route) => { reqs.push(route.request().url()); route.continue(); });
    const p2 = await c2.newPage();
    p2.on('console', (m) => { if (m.type() === 'error') { errs2.push(m.text()); } });
    p2.on('pageerror', (e) => errs2.push(e.message));
    const url = 'file://' + snapPath;
    await p2.goto(url + '#/results?outcome=o2');
    await p2.waitForSelector('html[data-ready="1"]', { timeout: 30000 });
    await p2.waitForSelector('#snapshot-banner');
    await p2.waitForSelector('#results-grid svg g[data-uid], #results-plot-panel svg g[data-uid]', { timeout: 15000 });
    const body = await p2.textContent('#main');
    check(body.indexOf(ctxState.ex2.plot.display_text.hu) >= 0, 'a pillanatkép ugyanazt a számot mutatja (Normand: ' + ctxState.ex2.plot.display_text.hu + ')');
    await p2.evaluate(() => { window.location.hash = '#/overview'; });
    await p2.waitForTimeout(800);
    const others = reqs.filter((u) => u.split('#')[0] !== url);
    check(others.length === 0, 'NULLA hálózati kérés a dokumentumon kívül (' + others.join(', ') + ')');
    check((await p2.evaluate(() => window.__csp.length)) === 0 && errs2.length === 0, 'nulla CSP-sértés és konzolhiba (' + errs2.join(' | ') + ')');
    await c2.close();
  });

  // ======================================================== 8. higiénia: konzol, HTTP, tároló, napló-adatvédelem
  await test('higiénia: nincs konzolhiba / CSP-sértés; csak az elvárt 4xx; tároló-szabály; napló cellaérték nélkül', async () => {
    check(errors.length === 0, 'nincs konzolhiba, pageerror vagy alert: ' + errors.join(' || '));
    check((await page.evaluate(() => window.__csp.length)) === 0, 'nulla CSP-sértés a munkapadon');
    const unexpected = http4xx.filter((h) => !allowed4xx.some((a) => h.indexOf(a) === 0));
    check(unexpected.length === 0, 'csak az elvárt 4xx-válaszok (' + unexpected.join(', ') + ')');
    const st = await page.evaluate(() => {
      const ls = [], ss = [];
      for (let i = 0; i < localStorage.length; i++) { ls.push(localStorage.key(i)); }
      for (let i = 0; i < sessionStorage.length; i++) { ss.push(sessionStorage.key(i)); }
      return { ls, ss, vals: ls.map((k) => localStorage.getItem(k)) };
    });
    check(st.ls.every((k) => k.indexOf('mag.pref.') === 0) && st.ss.every((k) => k === 'mag.token'), 'localStorage csak mag.pref.*, sessionStorage csak a token');
    check(st.vals.every((v) => !/Aronson|Montreal|Comstock|12,87|0\.4\d \[/.test(v)), 'a preferenciákban nincs projektadat');
    const act = fs.readFileSync(path.join(srv.proj, '07_ellenorzes', 'activity.jsonl'), 'utf-8');
    const slog = fs.readFileSync(srv.log, 'utf-8');
    ['Aronson', 'Montreal-Home', 'Comstock', ctxState.conv.cell_text.mean, '17854'].forEach((secret) => {
      check(act.indexOf(secret) < 0 && slog.indexOf(secret) < 0, 'a tevékenység- és a szervernaplóban nincs cellaérték („' + secret + '”)');
    });
    const chain = py(['audit', srv.proj]);
    check(Array.isArray(chain.findings), 'project audit a végállapoton is fut');
    const actRep = JSON.parse(run('python3', ['ma.py', 'project', 'activity', srv.proj, '--json']));
    check(actRep.ok === true && actRep.records > 10, 'a tevékenységnapló hash-lánca ép (' + actRep.records + ' bejegyzés; ma.py project activity)');
  });

  await browser.close();
  await srv.stop();
  fs.rmSync(tmp, { recursive: true, force: true });

  // ---------------------------------------------------------------- elfogadási ellenőrzőlista (terv 9.2)
  step('1. Projekt megnyitása (indítókód → token, adatvédelmi állapot)', ['projekt megnyitása']);
  step('2. Adatbevitel: TSV-beillesztés (BCG; Normand tizedesvesszővel)', ['BCG: beillesztés', 'Normand: beillesztés']);
  step('3. Élő validálás: hiba → jelölt cella → javítás → a jelzés eltűnik', ['BCG: beillesztés', 'Normand: beillesztés']);
  step('4. Átváltó: medián/IQR → átlag/SD, becsült jelölés + eredet (Normand)', ['Normand: átváltó']);
  step('5. Spec → explore → commit (BCG, Normand)', ['BCG: elemzési terv', 'Normand: elemzési terv']);
  step('6. Forest/funnel/Doi/LOO/befolyás a motor szövegeivel; számhűség (6.7)', ['BCG: eredmények', 'Normand: eredmények']);
  step('7. Lefúrás a kinyerési sorig és a forrás-PDF oldaláig (aláírt URL)', ['BCG: eredet', 'BCG: lefúrás', 'Normand: lefúrás']);
  step('8. „Nem hiba — indoklás” döntés (naplózva, a motor is jelöli)', ['Normand: „Nem hiba']);
  step('9. Megállapítás + kapu: előzetes tiltás, GATE_BLOCKED → lezárás → PASS', ['napló: új blocker', 'kapu: egy párhuzamos']);
  step('10. Project audit: adatmódosítás után elavult futás = X001; FINAL tiltva', ['project audit']);
  step('11. Audit-export ZIP (kétszer azonos sha256, ép lánc)', ['audit-export']);
  step('12. Pillanatkép: nulla hálózati kérés, nulla CSP-sértés', ['pillanatkép']);
  step('13. Higiénia: konzol, HTTP, tároló, naplók cellaérték nélkül', ['higiénia']);
  console.log('\nElfogadási ellenőrzőlista (terv 9.2 — BCG + Normand, plugin nélkül, valódi szerver):');
  CHECKLIST.forEach((c) => {
    const rs = results.filter((r) => c.tests.some((pre) => r.test.indexOf(pre) === 0));
    const ok = rs.length > 0 && rs.every((r) => r.pass);
    console.log('  ' + (ok ? '✔' : '✖') + ' ' + c.label + ' (' + rs.filter((r) => r.pass).length + '/' + rs.length + ')');
  });
  const failed = results.filter((r) => !r.pass);
  console.log('\n' + (results.length - failed.length) + '/' + results.length + ' ellenőrzés zöld' + (failed.length ? ', ' + failed.length + ' HIBÁS' : ''));
  failed.forEach((f) => console.log('  ✖ [' + f.test + '] ' + f.msg));
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
