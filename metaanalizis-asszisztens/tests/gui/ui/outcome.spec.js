#!/usr/bin/env node
/* tests/gui/ui/outcome.spec.js — a valódi munkapad-szerverrel egy CSAK `project init`-tel létrehozott projekten
 * (TELEPITES 6–7.: nincs ma-projekt.json, nincs kimenet) — a 2026-10-05-i bírálat regressziós tesztjei:
 *
 *   DOC-1  a kimenet felvétele a felületen (név, adattábla, hatásméret) → az Elemzési terv eléri az explore-t és a
 *          commitot;
 *   UX-01  az Áttekintés „Következő lépések” panelje a még nem ellenőrizhető szabályokat is megnevezi;
 *   UX-13  az X016-sávnak van teendő-gombja (döntés a naplóba X016 + D-S12-006 hivatkozással);
 *   FID-1  az Áttekintés a FUTÁS hatásméretével címkéz (OR-ral rögzített futás nem „RR”);
 *   UX-02  RoB-értékelés nélkül „nincs értékelés” (nem 0);
 *   FID-3  a terv futás-táblájának I²-oszlopa a motor i2_text-je;
 *   FID-5  az adattábla változása után a feltárás eredménye „elavult — az adattábla változott”, az Eredmények
 *          explore-választása is jelzi;
 *   FID-4  angolul a tengelyfeliratok a motor angol tick-szövegei (U+2212 mínusz);
 *   UX-06  az Eredmények ⓚ-jelvényei a futás kontextusával (modell, k, hatásméret) kérve, nem illő szabály nélkül;
 *   UX-15  a lefúrásban nincs link a még helyőrző Értékelés-képernyőre.
 * Futtatás: node tests/gui/ui/outcome.spec.js (kilépési kód 1, ha bármi elbukik).
 */
'use strict';

const path = require('path');
const fs = require('fs');
const os = require('os');
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
const DATA = '03_adatok/bcg.csv';

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

function startServer(tmp) {
  const proc = spawn('python3', [HELPER, 'serve-init', tmp], { cwd: ROOT, stdio: ['pipe', 'pipe', 'pipe'] });
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
    stop: () => new Promise((resolve) => { proc.on('exit', resolve); proc.stdin.write('quit\n'); setTimeout(() => proc.kill(), 15000); })
  }));
}

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
async function explored(p) {
  await p.waitForFunction(() => {
    const e = document.getElementById('plan-status');
    return !!e && /kész/.test(e.textContent || '') && /^\d+$/.test(e.getAttribute('data-seq') || '');
  }, null, { timeout: 30000 });
}
/** GET a felület saját API-kliensén át (token, fejlécek) → data. */
async function api(p, pathname, query) {
  return p.evaluate(([u, q]) => window.MA.api.get(u, { query: q || null, toast: false }).then((env) => env.data), [pathname, query || null]);
}

(async () => {
  spawnSync('python3', [BUILD], { cwd: ROOT, encoding: 'utf-8' });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ma_outcome_'));
  const srv = await startServer(tmp);
  let browser;
  try { browser = await chromium.launch({ headless: true }); } catch (e) { browser = await chromium.launch({ headless: true, executablePath: CHROME_FALLBACK }); }
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [];
  const requests = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) { errors.push('console: ' + m.text()); } });
  page.on('request', (r) => {
    const u = r.url();
    if (u.indexOf(srv.base + '/api/') !== 0) { return; }
    let body = null;
    try { body = r.postData() ? JSON.parse(r.postData()) : null; } catch (e) { body = null; }
    const url = new URL(u);
    requests.push({ method: r.method(), path: url.pathname, query: url.searchParams, body });
  });

  await test('DOC-1: csak project init után a kimenet a felületen vehető fel, és eléri az explore-t', async () => {
    await page.goto(srv.url);
    await page.waitForSelector('html[data-ready="1"]', { timeout: 30000 });
    check(!fs.existsSync(path.join(srv.proj, 'ma-projekt.json')), 'kiindulás: nincs ma-projekt.json (csak project init)');
    await go(page, '#/overview', 'overview');
    await page.waitForSelector('#no-outcomes #outcome-add');
    check((await txt(page, '#no-outcomes')).indexOf('project outcome') >= 0, 'az Áttekintés is felkínálja a kimenet felvételét (gomb + parancs)');
    await go(page, '#/analysis', 'analysis');
    await page.waitForSelector('#no-outcomes');
    const empty = await txt(page, '#no-outcomes');
    check(empty.indexOf('ma-projekt.json') < 0 && empty.indexOf('kimenet') >= 0, 'az üres állapot kezdőknek szól (nincs JSON-kulcs a szövegben)');
    await page.click('#outcome-add');
    await page.waitForSelector('#oc-name');
    await page.fill('#oc-name', 'TBC-incidencia');
    await page.selectOption('#oc-data', DATA);
    await page.selectOption('#oc-measure', 'RR');
    await page.click('[role="dialog"] .btn-primary');
    await page.waitForSelector('#plan-form', { timeout: 20000 });
    const meta = JSON.parse(fs.readFileSync(path.join(srv.proj, 'ma-projekt.json'), 'utf-8'));
    check(meta.outcomes.length === 1 && meta.outcomes[0].id === 'o1' && meta.outcomes[0].data === DATA && meta.outcomes[0].measure === 'RR',
      'ma-projekt.json: a kimenet (o1, ' + DATA + ', RR) a szerver írta');
    check(meta.outcomes[0].primary_spec === '05_elemzes/specs/o1_primary.json', 'primary_spec: projekt-relatív út (a motor auditja így keresi)');
    await explored(page);
    check(/\[.*;.*\]/.test(await txt(page, '#plan-summary')), 'az automatikus explore a motor szövegével fut');
    const act = fs.readFileSync(path.join(srv.proj, '07_ellenorzes', 'activity.jsonl'), 'utf-8');
    check(act.indexOf('"project.outcome"') >= 0, 'a kimenet felvétele a tevékenységnaplóban');
  });

  await test('FID-1, UX-13, FID-3: OR-ral rögzített futás; X016-döntés gombbal; I² a futás-táblában', async () => {
    await page.selectOption('#opt-measure', 'OR');
    await page.waitForFunction(() => /OR/.test((document.getElementById('plan-cmd-expanded') || {}).textContent || ''), null, { timeout: 20000 });
    await explored(page);
    await page.click('#plan-commit');
    await page.waitForSelector('.toast.is-success', { timeout: 30000 });
    await page.waitForFunction(() => document.querySelectorAll('#plan-runs-table tbody tr').length >= 1, null, { timeout: 20000 });
    const runs = await api(page, '/api/runs', { outcome: 'o1' });
    const run = runs.runs[0];
    const plot = JSON.parse(fs.readFileSync(path.join(srv.proj, run.dir, 'plot_data.json'), 'utf-8'));
    check(run.measure === 'OR' && plot.measure === 'OR', 'a futás hatásmérete OR (run.json / plot_data.json)');
    const row = await page.$$eval('#plan-runs-table tbody tr', (rs) => rs.map((r) => Array.from(r.children).map((c) => c.textContent.trim())));
    check(row.length >= 1 && row[0][4] === plot.heterogeneity.i2_text.hu, 'futás-tábla I²: a motor i2_text-je (' + (row[0] || [])[4] + ' = ' + plot.heterogeneity.i2_text.hu + ')');
    check(row.length >= 1 && row[0][3].indexOf('OR ') === 0, 'futás-tábla: a futás saját hatásmérete (' + (row[0] || [])[3] + ')');
    // X016: a mentett, nem előre rögzített elsődleges spec → a sávon teendő-gomb
    await page.waitForSelector('#plan-x016-decide', { timeout: 10000 });
    const n0 = requests.length;
    await page.click('#plan-x016-decide');
    await page.waitForSelector('[role="dialog"] .plan-reason');
    await page.fill('[role="dialog"] .plan-reason', 'Nem volt regisztrált protokoll; a modellt a Cochrane-ajánlás szerint választottuk.');
    await page.click('[role="dialog"] .btn-primary');
    await page.waitForFunction(() => /Döntés rögzítve/.test(document.body.textContent), null, { timeout: 10000 });
    const dec = requests.slice(n0).filter((r) => r.path === '/api/log/decision')[0];
    check(!!dec && dec.body.kb_refs.indexOf('X016') >= 0 && dec.body.kb_refs.indexOf('D-S12-006') >= 0 && dec.body.context.kind === 'analysis',
      'X016: a döntés a naplóba (X016 + D-S12-006, analysis-kontextus)');
    // Áttekintés: a futás hatásmérete (OR), nem a kimenet alapértéke (RR); RoB értékelés nélkül
    await go(page, '#/overview', 'overview');
    await page.waitForSelector('#overview-outcomes tbody tr');
    const cells = await page.$$eval('#overview-outcomes tbody tr', (rs) => rs.map((r) => Array.from(r.children).map((c) => c.textContent.trim())));
    check(cells[0][3].indexOf('OR ' + plot.summaries.filter((s) => s.primary)[0].display_text.hu) === 0, 'Áttekintés: „' + cells[0][3] + '” (FID-1: a futás mértéke)');
    check(cells[0][5] === 'nincs értékelés', 'Áttekintés: RoB magas — „nincs értékelés” (UX-02), nem 0');
  });

  await test('UX-01: a következő lépések a még nem ellenőrizhető szabályokat is megnevezik', async () => {
    await page.waitForSelector('#ov-next .section-body:not([aria-busy])');
    const next = await txt(page, '#ov-next');
    const audit = await api(page, '/api/audit/project');
    check(audit.not_checked.length > 0, 'az audit not_checked listája nem üres (' + audit.not_checked.length + ')');
    check(next.indexOf('nem talált teendőt') < 0, 'nincs hamis „nincs teendő”');
    check((await page.$('#ov-not-checked')) !== null && next.indexOf('PRISMA') >= 0, 'a hiányzó lépések kezdőknek szóló szöveggel (pl. PRISMA-folyamatábra)');
    const codes = await page.$$eval('#ov-not-checked li', (ls) => ls.map((l) => l.getAttribute('data-code')));
    check(codes.length >= 1 && codes.every((c) => audit.not_checked.some((n) => n.code === c)), 'a csoportok az audit szabálykódjai (' + codes.join(', ') + ')');
  });

  await test('FID-5: az adattábla változása után a feltárás eredménye elavult (terv és Eredmények)', async () => {
    await go(page, '#/analysis?outcome=o1', 'analysis');
    await page.waitForSelector('#plan-form');
    await page.waitForSelector('#plan-summary', { timeout: 30000 });       // a legutóbbi (friss) feltárás eredménye
    check(((await txt(page, '#plan-result-fresh')) || '').trim() === '', 'kiindulás: a feltárás eredménye friss');
    if (await page.$eval('#plan-auto', (e) => e.checked)) { await page.click('#plan-auto'); }
    const file = path.join(srv.proj, ...DATA.split('/'));
    const lines = fs.readFileSync(file, 'utf-8').split('\r\n').filter((l) => l);
    fs.writeFileSync(file, lines.slice(0, lines.length - 3).join('\r\n') + '\r\n');
    await page.waitForFunction(() => /adattábla változott/.test((document.getElementById('plan-result-fresh') || {}).textContent || ''), null, { timeout: 20000 });
    check(true, 'a terv eredménye „elavult — az adattábla változott”');
    await go(page, '#/results?outcome=o1&run=explore', 'results');
    await page.waitForSelector('#results-summary');
    check((await page.$('#results-explore-stale')) !== null, 'Eredmények (explore): elavult-jelzés a táblaváltozás miatt');
  });

  await test('FID-4, UX-06, UX-15: angol tickek, ⓚ a futás kontextusával, nincs link a helyőrzőre', async () => {
    const runs = await api(page, '/api/runs', { outcome: 'o1' });
    const run = runs.runs[0];
    const plot = JSON.parse(fs.readFileSync(path.join(srv.proj, run.dir, 'plot_data.json'), 'utf-8'));
    await go(page, '#/results?outcome=o1&run=' + run.run_id, 'results');
    await page.waitForSelector('.res-kb .kb-badge');
    await page.waitForTimeout(500);
    // (a kérés gyorsítótárazott: ugyanezzel a kontextussal már a korábbi Eredmények-nézet is kérhette)
    const kbq = requests.filter((r) => r.path === '/api/kb/rules' && r.query.get('field') === 'model' && r.query.get('k') === String(run.k))[0];
    const kbAll = requests.filter((r) => r.path === '/api/kb/rules').map((r) => r.query.toString());
    check(!!kbq && kbq.query.get('model') === 'random' && kbq.query.get('measure') === 'OR' && kbq.query.get('k') === String(run.k),
      'a KB-szabályok a futás kontextusával kérve (model, k, measure): ' + kbAll.join(' | '));
    const ids = await page.$$eval('.res-kb .kb-badge', (bs) => bs.map((b) => b.getAttribute('data-kb')));
    check(ids.indexOf('D-S08-011') < 0 && ids.indexOf('D-S08-016') < 0 && ids.indexOf('D-S10-006') < 0, 'nincs k = 1-es, IVhet- vagy fix hatású szabály egy véletlen hatású, k = ' + run.k + ' futásnál');
    // lefúrás: nincs link a helyőrző Értékelés-képernyőre
    await page.click('#results-plot-panel g.fp-study');
    await page.waitForSelector('#drilldown');
    check((await page.$$('#drilldown a[href*="appraisal"]')).length === 0, 'a lefúrásban nincs link a még készülő Értékelés-képernyőre');
    // angolul: a befolyás-ábra tickjei a motor angol szövegei (U+2212)
    await page.evaluate(() => window.MA.i18n.setLang('en'));
    await go(page, '#/results?outcome=o1&run=' + run.run_id + '&view=influence', 'results');
    await page.waitForSelector('#results-plot-panel .pl-tick-label');
    const ticks = await page.$$eval('#results-plot-panel .pl-tick-label', (ts) => ts.map((t) => ({ at: Number(t.getAttribute('data-at')), text: t.textContent })));
    const want = {};
    Object.keys(plot.influence_axes || {}).forEach((k) => (plot.influence_axes[k].ticks || []).forEach((tk) => { want[k + ':' + tk.at] = tk.text_i18n.en; }));
    const enTexts = Object.values(want);
    check(ticks.length > 0 && ticks.every((tk) => enTexts.indexOf(tk.text) >= 0), 'angol tickek = text_i18n.en (' + ticks.map((x) => x.text).join(' ') + ')');
    check(ticks.filter((tk) => tk.at < 0).every((tk) => tk.text.indexOf('−') === 0 && tk.text.indexOf('-') < 0), 'negatív tick angolul U+2212-vel');
    await page.evaluate(() => window.MA.i18n.setLang('hu'));
  });

  await test('higiénia: nincs konzol- vagy oldalhiba', async () => {
    check(errors.length === 0, 'konzol/oldal hibák: ' + errors.join(' | '));
  });

  await browser.close();
  await srv.stop();
  fs.rmSync(tmp, { recursive: true, force: true });
  const failed = results.filter((r) => !r.pass);
  console.log('\n' + (results.length - failed.length) + '/' + results.length + ' ellenőrzés zöld' + (failed.length ? ', ' + failed.length + ' HIBÁS' : ''));
  failed.forEach((f) => console.log('  ✖ [' + f.test + '] ' + f.msg));
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
