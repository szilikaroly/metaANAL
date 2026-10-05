#!/usr/bin/env node
/* tests/gui/ui/headhunter.spec.js — Metaheadhunter varázsló (screens/headhunter*.js) böngészős tesztje a dev-buildben,
 * a fixture-ökkel (ma_gui/web/fixtures/headhunter_*.json; generálja: python3 tests/gui/test_headhunter_fixtures.py --write).
 *
 * Futtatás:  node tests/gui/ui/headhunter.spec.js        (kilépési kód 1, ha bármi elbukik)
 *   MA_UI_DEV_HTML=<út>  — kész dev-build használata (a build kimarad).
 * Lefedi: regisztráció a 2 PRISMA fül alatt, kilenc lépés állapot- és EP-jelvényekkel, billentyűzetes lépéssor (nyilak,
 * Home/End, Enter), Források (táblázat, be/ki kapcsolás döntésként If-Match-csel), Áttekintések (rangsor a CLI kész
 * pontszámával, kizárás ok-modálissal, Esc = mégse), Kinyerés (idézet + lokátor, k-eltérés, megerősítés, tömeges
 * megerősítés), Duplumok (egymás melletti összevetés ≠-jelöléssel, elfogadás, szűrős tömeges elfogadás), Átfedés (CCA a
 * fixture szövegével, hőtérkép-cellák, mátrix és táblázatos nézete), Szűrés (listbox billentyűkkel: ↓, X → okkód-modális,
 * I), Frissítés (eredmények, előnézet futtatása), Egyesítés (dobozok a fájl számaival, lezárás tiltva nyitott EP-nél),
 * feladatpanel (fut → kész, megszakítás), HU/EN, sötét téma, konzolhiba-mentesség, tároló- és DOM-szabályok.
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

function fx(name) { return JSON.parse(fs.readFileSync(path.join(WEB, 'fixtures', name), 'utf-8')).routes; }
function route(routes, p, query) {
  return routes.filter((r) => r.path === p && JSON.stringify(r.query || null) === JSON.stringify(query || null))[0];
}
const ST = route(fx('headhunter_status.json'), '/api/headhunter/status');
const STATUS = ST.envelope.data;
const SOURCES = route(fx('headhunter_status.json'), '/api/headhunter/sources', { lang: 'hu' }).envelope.data;
const RV = fx('headhunter_reviews.json');
const REVIEWS = route(RV, '/api/headhunter/reviews').envelope.data.items;
const PROPS = route(fx('headhunter_studies.json'), '/api/headhunter/proposals', { status: 'pending' });
const OVERLAP = route(fx('headhunter_overlap.json'), '/api/headhunter/overlap').envelope.data;
const MERGED = route(fx('headhunter_merge.json'), '/api/headhunter/merged').envelope.data;
const PRISMA = route(fx('headhunter_merge.json'), '/api/headhunter/prisma').envelope.data;
const UPDATE = route(fx('headhunter_merge.json'), '/api/headhunter/update').envelope.data;

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

let browser, srv;
async function openPage(hash, opts) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: (opts && opts.dark) ? 'dark' : 'light' });
  const page = await ctx.newPage();
  const errors = [];
  const dialogs = [];
  page.on('console', (m) => { if (m.type() === 'error') { errors.push('console: ' + m.text()); } });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => { dialogs.push(d.type() + ':' + d.message()); d.dismiss().catch(() => null); });
  await page.goto(srv.base + '/?fixtures=1' + (hash || ''));
  await page.waitForSelector('html[data-ready="1"]', { timeout: 10000 });
  return { ctx, page, errors, dialogs };
}
async function screen(page, step) {
  await page.waitForFunction((s) => {
    const r = document.getElementById('screen-root');
    const c = document.getElementById('hh-card');
    return r && r.dataset.screen === 'headhunter' && c && c.dataset.step === s && !c.querySelector('.spinner') && !r.querySelector('.hh-card-host > .spinner');
  }, step, { timeout: 8000 });
}
async function txt(page, sel) { const el = await page.$(sel); return el ? ((await el.textContent()) || '') : ''; }
async function calls(page, method, p) {
  return page.evaluate(([m, pp]) => window.MA.dev.fixtures.calls.filter((c) => c.method === m && c.path === pp), [method, p]);
}
async function modalOpen(page) { await page.waitForSelector('[role="dialog"][aria-modal="true"]', { timeout: 4000 }); }
async function finish(o, label) {
  check(o.errors.length === 0, label + ': nincs konzolhiba / CSP-sértés: ' + o.errors.join(' || '));
  check(o.dialogs.length === 0, label + ': nem nyílt alert/confirm: ' + o.dialogs.join(' || '));
  const rep = await o.page.evaluate(() => {
    const ls = [], ss = [];
    for (let i = 0; i < localStorage.length; i++) { ls.push(localStorage.key(i)); }
    for (let i = 0; i < sessionStorage.length; i++) { ss.push(sessionStorage.key(i)); }
    return { ls, ss, lsValues: ls.map((k) => localStorage.getItem(k)), cookie: document.cookie };
  });
  check(rep.ls.every((k) => k.startsWith('mag.pref.')), label + ': localStorage csak mag.pref.* (' + rep.ls.join(',') + ')');
  check(rep.lsValues.every((v) => v.length <= 256 && !/synthetic|rv-pmid|Smith|NCT0/.test(v)), label + ': a preferenciákban nincs projektadat');
  check(rep.ss.every((k) => k === 'mag.token'), label + ': sessionStorage csak mag.token');
  check(rep.cookie === '', label + ': nincs süti');
  const missing = await o.page.evaluate(() => window.MA.i18n.missing());
  check(missing.length === 0, label + ': nincs hiányzó i18n-kulcs (' + missing.join(', ') + ')');
  const dom = await o.page.evaluate(() => ({ img: document.querySelectorAll('img').length, onerr: document.querySelectorAll('[onerror]').length,
    scripts: Array.from(document.scripts).filter((s) => !s.nonce).length, style: document.querySelectorAll('[style]').length }));
  check(dom.img === 0 && dom.onerr === 0 && dom.scripts === 0, label + ': DOM-biztonság (' + JSON.stringify(dom) + ')');
  await o.ctx.close();
}

(async () => {
  if (!process.env.MA_UI_DEV_HTML) {
    const r = spawnSync('python3', [path.join(WEB, 'build_gui.py'), '--dev'], { cwd: ROOT, encoding: 'utf-8' });
    if (r.status !== 0) { console.error('build hiba: ' + r.stderr + r.stdout); process.exit(1); }
    console.log(r.stdout.trim());
  }
  srv = await startServer();
  try { browser = await chromium.launch({ headless: true }); } catch (e) { browser = await chromium.launch({ headless: true, executablePath: CHROME_FALLBACK }); }

  await test('regisztráció: a 2 PRISMA fül alképernyője, kilenc lépés, EP-jelvények, billentyűzetes lépéssor', async () => {
    const o = await openPage('#/headhunter?step=sources');
    const p = o.page;
    await screen(p, 'sources');
    const reg = await p.evaluate(() => window.MA.app.screens().filter((s) => s.id === 'headhunter').map((s) => s.tab + ':' + s.workspace)[0]);
    check(reg === 'prisma:process', 'regisztrálva: prisma fül, process munkaterület (' + reg + ')');
    check((await p.$$('.app-subnav a.subnav-link')).length >= 2 && (await txt(p, '.app-subnav')).indexOf('Metaheadhunter') >= 0, 'a PRISMA fül al-navigációjában megjelenik');
    check((await p.$$('#hh-steps .hh-step')).length === 9, 'kilenc varázsló-lépés');
    check((await txt(p, '.hh-question')).indexOf(STATUS.state.pico.question) >= 0, 'a kérdés a fejlécben (state.json)');
    const ep2 = STATUS.cli.checkpoints.EP2;
    check((await txt(p, '#hh-steps li[data-step="extract"]')).indexOf(String(ep2) + ' nyitott') >= 0, 'Kinyerés: ' + ep2 + ' nyitott tétel (a CLI EP2-je)');
    check(await p.evaluate(() => document.querySelector('#hh-steps li[data-step="overlap"]').classList.contains('is-done')), 'Átfedés: kész (state.steps.overlap = done)');
    check((await p.getAttribute('#hh-steps li[data-step="sources"] a', 'aria-current')) === 'step', 'aria-current=step az aktuális lépésen');
    check((await p.$('#hh-card details.hh-what')) !== null, '„Mit jelent?” lenyíló (a szerver help-szövege)');
    // billentyűzet: nyilak a lépéssoron, Enter navigál
    await p.focus('#hh-steps li[data-step="sources"] a');
    await p.keyboard.press('ArrowRight');
    check(await p.evaluate(() => document.activeElement.closest('li').dataset.step === 'pico'), '→ a következő lépésre viszi a fókuszt');
    await p.keyboard.press('End');
    check(await p.evaluate(() => document.activeElement.closest('li').dataset.step === 'merge'), 'End → az utolsó lépés');
    await p.keyboard.press('Home');
    await p.keyboard.press('ArrowRight');
    await p.keyboard.press('ArrowRight');
    await p.keyboard.press('Enter');
    await screen(p, 'reviews');
    check(/step=reviews/.test(await p.evaluate(() => window.location.hash)), 'Enter → #/headhunter?step=reviews');
    check(await p.evaluate(() => document.activeElement && document.activeElement.id === 'hh-card'), 'a fókusz a lépés kártyájára kerül');
    await finish(o, 'regisztráció');
  });

  await test('Források: táblázat a CLI soraival, be/ki kapcsolás döntésként (If-Match: state.json), feladatpanel', async () => {
    const o = await openPage('#/headhunter?step=sources');
    const p = o.page;
    await screen(p, 'sources');
    check((await p.$$('#hh-src-table tbody tr')).length === SOURCES.rows.length, 'a forrás-sorok száma = a CLI soraié (' + SOURCES.rows.length + ')');
    check((await txt(p, '#hh-src-table tr[data-source="scopus"]')).indexOf('nincs beállítva') >= 0, 'Scopus: „nincs beállítva” (kulcs nélkül)');
    check(await p.evaluate(() => !document.getElementById('hh-src-scopus').checked), 'Scopus kikapcsolva');
    await p.click('#hh-src-openalex');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.path === '/api/headhunter/decide'));
    const dc = (await calls(p, 'POST', '/api/headhunter/decide'))[0];
    check(dc.body.kind === 'sources' && JSON.stringify(dc.body.options) === JSON.stringify({ disable: ['openalex'] }), 'decide {kind: sources, options: {disable: [openalex]}}');
    check(dc.headers['If-Match'] === SOURCES.state_etag, 'If-Match = a state.json ETag-je');
    check(dc.body.actor === undefined, 'a szereplő nem a kliensből jön');
    // feladat: ellenőrzés → 202 (fut) → kész
    await p.click('#hh-src-check');
    await p.waitForSelector('#hh-job:not([hidden])');
    const run = (await calls(p, 'POST', '/api/headhunter/run'))[0];
    check(run && run.body.step === 'sources_check', 'POST /api/headhunter/run {step: sources_check}');
    await p.waitForSelector('#hh-job #hh-cancel', { timeout: 4000 });
    check((await txt(p, '#hh-job')).indexOf('Keresés a PubMedben') >= 0, 'futás közben a progress.jsonl utolsó sora látszik');
    check((await p.$('#hh-job progress')) !== null, 'előrehaladás-jelző (a CLI done/total értékeiből)');
    await p.click('#hh-cancel');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => /\/cancel$/.test(c.path)));
    check(true, 'Megszakítás → POST …/runs/<id>/cancel');
    await p.waitForFunction(() => /kész/.test((document.getElementById('hh-job') || {}).textContent || ''), null, { timeout: 8000 });
    check((await p.$('#hh-job #hh-cancel')) === null, 'kész állapotban nincs megszakítás-gomb');
    await finish(o, 'források');
  });

  await test('Áttekintések: rangsor a CLI pontszámával, kizárás ok-modálissal (Esc = mégse), kiválasztás', async () => {
    const o = await openPage('#/headhunter?step=reviews');
    const p = o.page;
    await screen(p, 'reviews');
    const ids = await p.$$eval('#hh-rv-list li.hh-rv', (els) => els.map((e) => e.dataset.review));
    check(JSON.stringify(ids) === JSON.stringify(REVIEWS.map((r) => r.review_id)), 'sorrend = a szerver rangsora');
    check((await txt(p, '#hh-rv-list li.hh-rv:first-child .hh-rv-rank')).indexOf(REVIEWS[0].rank.score_text) >= 0, 'pontszám: a CLI kész szövege (' + REVIEWS[0].rank.score_text + ')');
    check((await txt(p, '#hh-rv-list li.hh-rv:first-child')).indexOf('Cochrane') >= 0, 'Cochrane-jelvény');
    const cand = REVIEWS.filter((r) => r.status === 'candidate')[0];
    const sel = '#hh-rv-list li[data-review="' + cand.review_id + '"]';
    await p.click(sel + ' .hh-rv-exclude');
    await modalOpen(p);
    await p.keyboard.press('Escape');
    check((await calls(p, 'POST', '/api/headhunter/decide')).length === 0, 'Esc: mégse, nincs döntés');
    await p.click(sel + ' .hh-rv-exclude');
    await modalOpen(p);
    await p.click('[role="dialog"] .modal-actions .btn-danger');
    check((await txt(p, '[role="dialog"] .hh-err')).indexOf('okát') >= 0, 'kizárásnál az indoklás kötelező (hibaüzenet, a modális nyitva)');
    await p.click('[role="dialog"] .hh-preset:first-child');
    await p.click('[role="dialog"] .modal-actions .btn-danger');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.path === '/api/headhunter/decide'));
    const dc = (await calls(p, 'POST', '/api/headhunter/decide'))[0];
    check(dc.body.target === cand.review_id && dc.body.value === 'exclude' && dc.body.reason === 'más PICO', 'decide: target, value=exclude, reason a gyorsgombból');
    check(dc.headers['If-Match'] === cand.etag, 'If-Match = az áttekintés-fájl ETag-je');
    await finish(o, 'áttekintések');
  });

  await test('Kinyerés: idézet + lokátor, k-eltérés, megerősítés, tömeges megerősítés megerősítő párbeszéddel', async () => {
    const o = await openPage('#/headhunter?step=extract');
    const p = o.page;
    await screen(p, 'extract');
    await p.waitForSelector('#hh-cand-table');
    check((await p.$$('[role="tab"]')).length === REVIEWS.filter((r) => r.status === 'selected').length, 'fül a kiválasztott áttekintésenként');
    const det = route(RV, '/api/headhunter/reviews/' + REVIEWS[0].review_id);
    const c0 = det.envelope.data.candidates[0];
    check((await txt(p, '#hh-cand-table tr[data-cand="' + c0.cand_id + '"] blockquote')) === c0.evidence[0].quote, 'a bizonyíték szó szerint (' + c0.evidence[0].quote + ')');
    check((await txt(p, '#hh-cand-table tr[data-cand="' + c0.cand_id + '"] figcaption')).indexOf(c0.evidence[0].locator.section || c0.evidence[0].locator.container) >= 0, 'lokátor az idézet alatt');
    const proposed = det.envelope.data.candidates.filter((c) => c.status === 'proposed')[0];
    await p.click('#hh-cand-table tr[data-cand="' + proposed.cand_id + '"] .hh-cand-confirm');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.path === '/api/headhunter/decide'));
    let dc = (await calls(p, 'POST', '/api/headhunter/decide'))[0];
    check(dc.body.target === REVIEWS[0].review_id + '#' + proposed.cand_id && dc.body.value === 'include', 'megerősítés: target rv-…#c…, value=include');
    check(dc.headers['If-Match'] === det.etag, 'If-Match = a részletnézet ETag-je');
    await p.waitForSelector('#hh-cand-table');
    // a második áttekintés: k-eltérés (H006)
    const rvB = REVIEWS.filter((r) => r.status === 'selected')[1];
    await p.click('[role="tab"][data-tab="' + rvB.review_id + '"]');
    await p.waitForFunction((id) => /H006|eltér/.test((document.getElementById('hh-ex-panel') || {}).textContent || '') && document.querySelector('#hh-cand-table'), rvB.review_id);
    if (rvB.k_reported && rvB.k_reported.value !== rvB.n_groups) {
      check((await txt(p, '#hh-ex-panel .hh-ex-head')).indexOf('eltér a közölt számtól') >= 0, 'k-eltérés jelzése (H006)');
    }
    await p.click('#hh-confirm-all');
    await modalOpen(p);
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.filter((c) => c.path === '/api/headhunter/decide').length >= 2);
    dc = (await calls(p, 'POST', '/api/headhunter/decide'))[1];
    check(dc.body.kind === 'batch' && dc.body.batch.type === 'all_candidates' && dc.body.batch.review === rvB.review_id, 'tömeges megerősítés: batch all_candidates az áttekintésre');
    await finish(o, 'kinyerés');
  });

  await test('Duplumok: egymás melletti összevetés ≠ jelöléssel, elfogadás, szűrős tömeges elfogadás', async () => {
    const o = await openPage('#/headhunter?step=dedupe');
    const p = o.page;
    await screen(p, 'dedupe');
    const items = PROPS.envelope.data.items;
    check((await p.$$('#hh-props article.hh-prop')).length === items.length, 'javaslat-kártyák: ' + items.length);
    const titleOf = (r) => (r.bib && r.bib.title) || (r.reports && r.reports[0] && r.reports[0].bib.title) || (r.cited_as && (r.cited_as.title || r.cited_as.text)) || '';
    const norm = (v) => String(v || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    const sr = items.filter((x) => x.records.length > 1 && new Set(x.records.map((r) => norm(titleOf(r)))).size > 1)[0];
    check(!!sr, 'van eltérő című javaslat a fixture-ben');
    const card = '#hh-props article[data-proposal="' + sr.proposal_id + '"]';
    check((await p.$$(card + ' table.hh-sbs thead th')).length === 1 + sr.records.length, 'egymás mellett: ' + sr.records.length + ' oszlop');
    check(await p.evaluate((c) => document.querySelector(c + ' tr[data-field="title"]').classList.contains('hh-diff'), card), 'az eltérő cím kiemelve');
    check((await txt(p, card + ' tr[data-field="title"] th')).indexOf('≠') >= 0, 'az eltérést jel is mutatja (nem csak szín)');
    check(!sr.score_text || (await txt(p, card)).indexOf(sr.score_text) >= 0, 'a pontszám a CLI kész szövege');
    check((await txt(p, card)).indexOf(sr.rule) >= 0, 'a szabály neve látszik');
    await p.click(card + ' .hh-prop-accept');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.path === '/api/headhunter/decide'));
    let dc = (await calls(p, 'POST', '/api/headhunter/decide'))[0];
    check(dc.body.target === sr.proposal_id && dc.body.value === 'accept' && dc.headers['If-Match'] === PROPS.etag, 'elfogadás: value=accept, If-Match = studies.json');
    await p.waitForSelector('#hh-dd-batch');
    await p.click('#hh-dd-batch');
    await modalOpen(p);
    await p.fill('#hh-batch-min', '');
    const probable = items.filter((x) => x.certainty === 'probable' && x.kind !== 'resolution').length;
    await p.waitForFunction((n) => document.querySelector('[role="dialog"] [aria-live]').textContent.indexOf(String(n)) >= 0, probable);
    check(true, 'a szűrőnek megfelelő javaslatok száma élőben (' + probable + ')');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.filter((c) => c.path === '/api/headhunter/decide').length >= 2, null, { timeout: 4000 }).catch(() => null);
    dc = (await calls(p, 'POST', '/api/headhunter/decide'))[1];
    check(dc && dc.body.kind === 'batch' && dc.body.batch.type === 'all_proposals' && dc.body.batch.min_score === undefined && dc.body.batch.certainty === 'probable',
      'tömeges elfogadás: batch all_proposals {certainty, min_score} (a szűrő a döntésben)');
    await finish(o, 'duplumok');
  });

  await test('Átfedés: CCA a fixture szövegével, hőtérkép, hivatkozási mátrix és táblázatos nézete', async () => {
    const o = await openPage('#/headhunter?step=overlap');
    const p = o.page;
    await screen(p, 'overlap');
    check((await txt(p, '#hh-cca .hh-tile-v')) === OVERLAP.cca_text + '%', 'CCA = a motor értéke (' + OVERLAP.cca_text + '%)');
    check((await txt(p, '#hh-cca')).indexOf('nagyon magas') >= 0 || OVERLAP.band !== 'very_high', 'sáv-címke szöveggel');
    const c = OVERLAP.reviews.length;
    check((await p.$$('svg.hh-heat g.hh-heat-cell')).length === c * c, 'hőtérkép: c × c cella');
    const pair = OVERLAP.pairs[0];
    const cell = await p.$eval('svg.hh-heat g.hh-heat-cell[data-a="' + pair.a + '"][data-b="' + pair.b + '"]', (g) => ({ cca: g.getAttribute('data-cca'), cls: g.getAttribute('class'), t: g.textContent }));
    check(cell.cca === pair.cca_text && cell.cls.indexOf('band-' + pair.band) >= 0 && cell.t.indexOf(pair.cca_text + '%') >= 0, 'páronkénti cella: a pár cca_text-je és sávja');
    check((await p.$$('#hh-pairs tbody tr')).length === OVERLAP.pairs.length, 'páronkénti táblázat');
    check((await p.$$('svg.hh-matrix g.hh-mx-cell')).length === OVERLAP.rows.length * c, 'mátrix: sor × áttekintés cella');
    check((await p.$$('svg.hh-matrix g.hh-mx-cell.is-in')).length === OVERLAP.rows.reduce((a, r) => a + r.in.filter((x) => x).length, 0), 'bejelölt cellák = N');
    await p.click('#hh-mx-toggle');
    check((await p.getAttribute('#hh-mx-toggle', 'aria-pressed')) === 'true', 'táblázatos nézet (aria-pressed)');
    check((await p.$$('#hh-matrix-table tbody tr')).length === OVERLAP.rows.length, 'táblázatos mátrix: soronként egy vizsgálat');
    await finish(o, 'átfedés');
  });

  await test('Szűrés: listbox billentyűkkel (↓, X → okkód kötelező, I), döntés a studies.json ETag-jével', async () => {
    const o = await openPage('#/headhunter?step=screen');
    const p = o.page;
    await screen(p, 'screen');
    const pending = MERGED.studies.filter((s) => s.status === 'pending');
    check((await p.$$('#hh-sc-list [role="option"]')).length === pending.length, 'függő vizsgálatok: ' + pending.length);
    await p.focus('#hh-sc-list');
    await p.keyboard.press('ArrowDown');
    const second = pending[1].study_id;
    check((await p.getAttribute('#hh-sc-' + second, 'aria-selected')) === 'true', '↓ → a második vizsgálat kijelölve');
    check((await txt(p, '#hh-sc-detail')).indexOf(pending[1].label) >= 0, 'a részletek a kijelölt vizsgálatot mutatják (proveniencia)');
    await p.keyboard.press('x');
    await modalOpen(p);
    await p.click('[role="dialog"] .modal-actions .btn-danger');
    check((await txt(p, '[role="dialog"] .hh-err')).indexOf('PRISMA 16a') >= 0, 'teljes szöveg szintű kizárásnál az okkód kötelező');
    await p.selectOption('#hh-reason-code', 'X4');
    await p.click('[role="dialog"] .modal-actions .btn-danger');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.path === '/api/headhunter/decide'));
    let dc = (await calls(p, 'POST', '/api/headhunter/decide'))[0];
    check(dc.body.target === second && dc.body.value === 'exclude' && dc.body.level === 'full_text' && dc.body.reason_code === 'X4', 'X: exclude, full_text, X4');
    check(dc.headers['If-Match'] === STATUS.files.studies.etag, 'If-Match = a studies.json ETag-je');
    await p.waitForSelector('#hh-sc-' + second + ' .badge.is-ok');
    check(await p.evaluate(() => document.activeElement && document.activeElement.id === 'hh-sc-list'), 'a fókusz a listán marad');
    await p.keyboard.press('i');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.filter((c) => c.path === '/api/headhunter/decide').length >= 2);
    dc = (await calls(p, 'POST', '/api/headhunter/decide'))[1];
    check(dc.body.value === 'include' && dc.body.level === 'both', 'I: bevonás mindkét szinten');
    await finish(o, 'szűrés');
  });

  await test('Frissítés és Egyesítés: eredmények, előnézet, PRISMA-dobozok a fájl számaival, lezárás tiltva', async () => {
    const o = await openPage('#/headhunter?step=update');
    const p = o.page;
    await screen(p, 'update');
    check((await p.$$('table.hh-up-queries tbody tr')).length === UPDATE.queries.length, 'lekérdezések: ' + UPDATE.queries.length);
    check((await txt(p, '#hh-up-window')).indexOf(UPDATE.window.start_date) >= 0, 'az ablak kezdete a fájlból');
    await p.selectOption('#hh-up-anchor', 'manual');
    await p.fill('#hh-up-start', 'tegnap');
    await p.click('#hh-up-preview');
    check((await txt(p, '.hh-up-form .hh-err')).indexOf('ÉÉÉÉ-HH-NN') >= 0, 'hibás dátum: magyar hibaüzenet, nincs kérés');
    await p.fill('#hh-up-start', '2021-01-01');
    await p.click('#hh-up-preview');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.some((c) => c.path === '/api/headhunter/run'));
    const run = (await calls(p, 'POST', '/api/headhunter/run'))[0];
    check(run.body.step === 'update_search' && run.body.options.dry_run === true && run.body.options.start === '2021-01-01', 'előnézet: update_search dry_run, kézi kezdet');
    await p.evaluate(() => { window.location.hash = '#/headhunter?step=merge'; });
    await screen(p, 'merge');
    check((await p.$$('#hh-mg-tiles .hh-tile')).length === 8, 'nyolc számláló');
    const f = PRISMA.flow;
    for (const k of ['other_methods_identified', 'other_methods_assessed', 'identified_databases', 'included_studies']) {
      const v = await txt(p, '.hh-pbox[data-box="' + k + '"] .hh-pbox-v');
      check(v === (f[k] === null || f[k] === undefined ? '—' : String(f[k])), 'PRISMA ' + k + ' = ' + v);
    }
    check(await p.evaluate(() => document.getElementById('hh-signoff').disabled), 'lezárás tiltva nyitott EP-nél (H009)');
    check((await txt(p, '.hh-signoff')).indexOf('EP4') >= 0, 'a nyitott ellenőrzőpontok felsorolva');
    check((await p.getAttribute('#hh-open-prisma', 'href')) === '#/prisma', 'link a PRISMA-képernyőre');
    await p.check('#hh-exp-prisma');
    await p.fill('#hh-exp-outcome', 'o1');
    await p.click('#hh-exp-run');
    await p.waitForFunction(() => window.MA.dev.fixtures.calls.filter((c) => c.path === '/api/headhunter/run').length >= 2);
    const ex = (await calls(p, 'POST', '/api/headhunter/run'))[1];
    check(ex.body.step === 'export' && ex.body.options.prisma === true && ex.body.options.outcome === 'o1', 'export: prisma + kimenet');
    await finish(o, 'frissítés-egyesítés');
  });

  await test('Kérdés (PICO), angol nyelv, sötét téma, 409-es ütközés kezelése', async () => {
    const o = await openPage('#/headhunter?step=pico', { dark: true });
    const p = o.page;
    await screen(p, 'pico');
    check((await txt(p, '#hh-card')).indexOf(STATUS.state.pico.question) >= 0, 'a jóváhagyott kérdés látszik');
    check((await p.$$('table.hh-reasons tr')).length === STATUS.state.exclusion_reasons.length, 'kizárási okok szótára');
    await p.evaluate(() => window.MA.i18n.setLang('en'));
    await screen(p, 'pico');
    check((await txt(p, '#hh-card-h')) === 'Question (PICO)', 'angol cím');
    check((await txt(p, '#hh-steps')).indexOf('Duplicates') >= 0, 'angol lépésnevek');
    // ütközés: a szerver 409-et ad (közben más írta a fájlt) → hibaüzenet, nincs összeomlás
    await p.evaluate(() => {
      window.MA.dev.fixtures.route('POST', '/api/headhunter/decide', () => ({ status: 409, envelope: { ok: false, error: { code: 'CONFLICT', http: 409,
        message: 'A(z) 01_kereses/headhunter/state.json közben megváltozott.', details: { hh_code: 'PRECONDITION_FAILED' } } } }), { first: true });
    });
    await p.evaluate(() => { window.location.hash = '#/headhunter?step=sources'; });
    await screen(p, 'sources');
    await p.click('#hh-src-pubmed');
    await p.waitForSelector('.toast.is-error');
    check((await txt(p, '.toast.is-error')).indexOf('közben megváltozott') >= 0, '409: a szerver üzenete szó szerint');
    check(await p.evaluate(() => document.getElementById('hh-src-pubmed').checked), 'hibánál a kapcsoló visszaáll');
    await p.evaluate(() => window.MA.i18n.setLang('hu'));
    await finish(o, 'pico-en-sötét');
  });

  await browser.close();
  await srv.close();
  const failed = results.filter((r) => !r.pass);
  console.log('\nheadhunter.spec: ' + (results.length - failed.length) + '/' + results.length + ' rendben');
  if (failed.length) {
    failed.forEach((f) => console.log('  ✖ [' + f.test + '] ' + f.msg));
    process.exit(1);
  }
})();
