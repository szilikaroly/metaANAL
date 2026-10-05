#!/usr/bin/env node
/* tests/gui/ui/extraction.spec.js — 3 Kinyerés, átváltó és projekt/adatvédelem képernyők böngészős tesztje
 * (terv 3.5.2–3.5.4, 6.3, 7.1 T6, 7.6, 8.6 2. réteg).
 *
 * Futtatás:  node tests/gui/ui/extraction.spec.js        (kilépési kód 1, ha bármi elbukik)
 *   MA_UI_DEV_HTML=<út>  — kész dev-build használata (a build kimarad), pl. elkülönített fejlesztéshez.
 * A dev-buildet ?fixtures=1-gyel nyitja (src/dev/extraction_backend.js: állapottartó fixture-háttér), szigorú CSP mellett.
 * Ellenőrzi: rács (ARIA, rögzített oszlop, billentyűzet, TSV-beillesztés, undo/redo), élő validálás (debounce, cellára
 * képzés, blokkolt sor), „Nem hiba — indoklás”, mentés If-Match-csel, 409 cellaszintű diff (enyém/lemezen lévő),
 * 423 Excel-zár, külső módosítás, eredet-panel + PDF-oldal, dokumentum-jegyzék, átváltó (becsült / számított / 422),
 * projekt (legutóbbiak, adatvédelmi tételek, adatosztály-előnézet és -váltás, diff-előnézet + kifejezett jóváhagyás),
 * mentetlen-módosítás őr, nyelvváltás, DOM-biztonság (HTML-címke szövegként), böngészőtároló-szabály, konzolhiba-mentesség.
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
const O1 = '03_adatok/o1.csv';
const O2 = '03_adatok/o2.csv';
const O3 = '03_adatok/o3.csv';

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

// ---------------------------------------------------------------- szerver: a dev-HTML nonce-szal + CSP; /f/* = aláírt fájl-URL helye
function startServer() {
  const hits = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    hits.push(url.pathname);
    if (req.method === 'GET' && url.pathname === '/') {
      const nonce = crypto.randomBytes(18).toString('base64url');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': CSP(nonce), 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' });
      res.end(fs.readFileSync(DEV, 'utf-8').split('{{CSP_NONCE}}').join(nonce));
      return;
    }
    if (url.pathname.startsWith('/f/')) { res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' }); res.end('pdf'); return; }
    res.writeHead(404); res.end();
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({
    base: 'http://127.0.0.1:' + server.address().port, hits,
    close: () => new Promise((r) => { if (server.closeAllConnections) { server.closeAllConnections(); } server.close(r); })
  })));
}

// ---------------------------------------------------------------- böngésző-segédek
let browser, srv;
async function openPage(hash, opts) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [];
  const dialogs = [];
  page.on('console', (m) => { if (m.type() === 'error') { errors.push('console: ' + m.text()); } });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => { dialogs.push(d.type() + ':' + d.message()); d.dismiss().catch(() => null); });
  await page.goto(srv.base + '/?fixtures=1' + (opts && opts.query ? '&' + opts.query : '') + (hash || ''));
  await page.waitForSelector('html[data-ready="1"]', { timeout: 10000 });
  return { ctx, page, errors, dialogs };
}
async function settle(page) {
  await page.waitForTimeout(330);
  await page.waitForFunction(() => !document.querySelector('#ex-summary .spinner') && !!document.querySelector('#ex-summary .ex-k'), null, { timeout: 5000 });
}
async function openExtraction(outcome) {
  const o = await openPage('#/extraction?outcome=' + outcome);
  await o.page.waitForSelector('#screen-root[data-screen="extraction"] table[role="grid"]', { timeout: 5000 });
  await settle(o.page);
  return o;
}
/** A rács cellájának CSS-szelektora a fejléc szövege (eredeti oszlopnév) alapján. */
async function cell(page, uid, label) {
  const col = await page.evaluate((l) => {
    const th = Array.from(document.querySelectorAll('.mg-table thead th')).find((x) => { const s = x.querySelector('.mg-cl'); return s && s.textContent === l; });
    return th ? th.dataset.col : null;
  }, label);
  return '.mg-table tr[data-uid="' + uid + '"] td[data-col="' + col + '"]';
}
async function text(page, sel) { return (await page.textContent(sel)) || ''; }
async function cellText(page, uid, label) { return page.evaluate((s) => { const x = document.querySelector(s); return x ? x.querySelector('.mg-t').textContent : null; }, await cell(page, uid, label)); }
async function calls(page, method, p) {
  return page.evaluate(([m, pp]) => window.MA.dev.fixtures.calls.filter((c) => c.method === m && c.path === pp), [method, p]);
}
async function activeCell(page) {
  return page.evaluate(() => { const a = document.activeElement; const td = a && a.closest ? a.closest('td[role="gridcell"]') : null; return td ? { uid: td.parentNode.dataset.uid, col: td.dataset.col, tag: a.tagName } : null; });
}
async function typeInto(page, sel, value) { await page.fill(sel, ''); await page.type(sel, value); }
async function storageReport(page) {
  return page.evaluate(() => {
    const ls = [], ss = [];
    for (let i = 0; i < localStorage.length; i++) { ls.push(localStorage.key(i)); }
    for (let i = 0; i < sessionStorage.length; i++) { ss.push(sessionStorage.key(i)); }
    return { ls, ss, lsValues: ls.map((k) => localStorage.getItem(k)), cookie: document.cookie };
  });
}
function checkStorage(rep, where) {
  check(rep.ls.every((k) => k.startsWith('mag.pref.')), where + ': localStorage csak mag.pref.* (' + rep.ls.join(',') + ')');
  check(rep.lsValues.every((v) => v.length <= 256 && !/Ferguson|Aronson|Montreal|12,87|12\.87|<img/.test(v)), where + ': a preferenciákban nincs projektadat (' + rep.lsValues.join('|') + ')');
  check(rep.ss.every((k) => k === 'mag.token'), where + ': sessionStorage csak mag.token (' + rep.ss.join(',') + ')');
  check(rep.cookie === '', where + ': nincs süti');
}
async function finish(o, label) {
  check(o.errors.length === 0, label + ': nincs konzolhiba / CSP-sértés: ' + o.errors.join(' || '));
  check(o.dialogs.length === 0, label + ': nem nyílt alert/confirm (XSS-próba): ' + o.dialogs.join(' || '));
  checkStorage(await storageReport(o.page), label);
  await o.ctx.close();
}

// ---------------------------------------------------------------- tesztek
(async () => {
  if (!process.env.MA_UI_DEV_HTML) {
    for (const args of [[], ['--dev']]) {
      const r = spawnSync('python3', [path.join(WEB, 'build_gui.py')].concat(args), { cwd: ROOT, encoding: 'utf-8' });
      if (r.status !== 0) { console.error('build hiba: ' + r.stderr + r.stdout); process.exit(1); }
      console.log(r.stdout.trim());
    }
  }
  srv = await startServer();
  try { browser = await chromium.launch({ headless: true }); } catch (e) { browser = await chromium.launch({ headless: true, executablePath: CHROME_FALLBACK }); }

  // ========== 1. betöltés, ARIA, élő validálás, cella-dekorációk
  await test('kinyerés: tábla, ARIA-rács, rögzített oszlop, motor-validálás cellákra képezve', async () => {
    const o = await openExtraction('o1');
    const p = o.page;
    check((await p.$$('.mg-table tbody tr[role="row"]')).length === 13, '13 sor (BCG)');
    check((await p.getAttribute('.mg-table', 'role')) === 'grid' && (await p.getAttribute('.mg-table', 'aria-rowcount')) === '14', 'role=grid, aria-rowcount');
    check((await p.$$('.mg-table th[role="rowheader"]')).length === 13 && (await p.$$('.mg-table th[role="columnheader"]')).length === 12, 'sor- és oszlopfejlécek');
    check((await p.$$('.mg-table td[role="gridcell"][tabindex="0"]')).length === 1, 'roving tabindex: egy fókuszálható cella');
    const hdr = await text(p, '.mg-table thead');
    check(hdr.indexOf('esemény1') >= 0 && hdr.indexOf('e1') >= 0, 'fejléc: eredeti név + a motor kanonikus neve (column_map)');
    const frozen = await p.evaluate(() => { const td = document.querySelector('.mg-table tbody td.mg-frozen'); return td ? getComputedStyle(td).position + '|' + td.dataset.col : null; });
    check(frozen === 'sticky|c0', 'az első oszlop rögzített (sticky): ' + frozen);
    const v = await calls(p, 'POST', '/api/validate');
    check(v.length >= 1, 'POST /api/validate a betöltéskor');
    const b = v[v.length - 1].body;
    check(b.schema === 'szk.ma.validate-request/v1' && b.table.header[0] === 'row_uid' && b.measure === 'RR' && b.dataset === O1, 'validate-request: row_uid-oszlop, mérték, dataset');
    check(b.table.rows[1][2] === '600' && typeof b.table.rows[3][3] === 'string' && b.table.rows[3][3] === '13,598', 'a cellák nyers szövegként mennek (600, „13,598”)');
    const sum = await text(p, '#ex-summary');
    check(/1 hiba/.test(sum) && /1 figyelmeztetés/.test(sum) && /6 tájékoztatás/.test(sum) && /k = 12\/13/.test(sum), 'összesítő a motorból: ' + sum.replace(/\s+/g, ' '));
    const e1 = await cell(p, 'rbcg02', 'esemény1');
    check((await p.getAttribute(e1, 'aria-invalid')) === 'true', 'V006 → az e1 cella aria-invalid');
    const desc = await p.evaluate((s) => { const x = document.querySelector(s); const d = document.getElementById(x.getAttribute('aria-describedby')); return d ? d.textContent : ''; }, e1);
    check(/V006/.test(desc) && /e1 = 600/.test(desc), 'aria-describedby → a megállapítás szövege: ' + desc);
    check((await text(p, e1 + ' .mg-m')).indexOf('✖') >= 0, 'szimbólum is (✖), nem csak szín');
    check(await p.evaluate(() => document.querySelector('tr[data-uid="rbcg02"]').classList.contains('is-blocked')), 'blokkoló hiba → a sor szürke (kimarad)');
    const n1 = await cell(p, 'rbcg04', 'n1');
    check(await p.evaluate((s) => document.querySelector(s).classList.contains('is-warning'), n1), 'V023 → figyelmeztető cella');
    check((await text(p, n1 + ' .mg-m')).indexOf('⟳') >= 0, 'külső szerkesztésű cella (eredet: external_edit) ⟳');
    check((await text(p, (await cell(p, 'rbcg10', 'esemény1')) + ' .mg-m')).indexOf('◆') >= 0, 'becsült (digitalizált) cella ◆');
    check((await text(p, (await cell(p, 'rbcg06', 'esemény2')) + ' .mg-m')).indexOf('≡') >= 0, 'egyeztetett cella ≡');
    check((await p.$$('#ex-findings section[data-severity="error"] li.ex-f')).length === 1, 'megállapítások: 1 hiba-csoport tétel');
    check((await p.$$('#ex-findings section[data-severity="info"] li.ex-f')).length === 6, 'megállapítások: 6 tájékoztatás');
    check((await text(p, '#ex-findings')).indexOf('Érvénytelen eseményszám') >= 0, 'a motor címe szó szerint');
    check((await text(p, '#nav-extraction .nav-badges')).indexOf('1') >= 0, 'fül-jelvény a piszkozat validálásából');
    check((await text(p, '.mg-table tr[data-uid="rbcg01"]')).indexOf('3. o. Table 1') >= 0, 'forrás-oszlop az eredetből (oldal, hely)');
    await finish(o, 'betöltés');
  });

  // ========== 2. billentyűzet, szerkesztés, javítás → a jelzés eltűnik, undo/redo
  await test('kinyerés: billentyűzet (nyilak, gépelés, Enter, Esc, Home/End), V006 javítása, Ctrl+Z / Ctrl+Y', async () => {
    const o = await openExtraction('o1');
    const p = o.page;
    await p.click(await cell(p, 'rbcg01', 'vizsgálat'));
    await p.keyboard.press('ArrowRight');
    await p.keyboard.press('ArrowDown');
    let a = await activeCell(p);
    check(a && a.uid === 'rbcg02' && a.col === 'c1', '→ ↓ mozgat (Ferguson e1)');
    const outline = await p.evaluate(() => { const s = getComputedStyle(document.activeElement); return s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2; });
    check(outline, 'látható fókuszkeret a cellán');
    await p.keyboard.press('End');
    a = await activeCell(p);
    check(a && a.col === 'src', 'End → a sor utolsó oszlopa');
    await p.keyboard.press('Home');
    await p.keyboard.press('ArrowRight');
    await p.keyboard.type('7');
    check((await activeCell(p)).tag === 'INPUT', 'gépelés → szerkesztőmező');
    await p.keyboard.press('Escape');
    check((await cellText(p, 'rbcg02', 'esemény1')) === '600' && (await activeCell(p)).tag === 'TD', 'Esc elveti, a fókusz a cellán marad');
    await p.keyboard.type('6');
    await p.keyboard.press('Enter');
    check((await cellText(p, 'rbcg02', 'esemény1')) === '6', 'Enter rögzít („6”)');
    a = await activeCell(p);
    check(a && a.uid === 'rbcg03', 'Enter után a következő sorra lép');
    await settle(p);
    const v = await calls(p, 'POST', '/api/validate');
    check(v.length >= 2 && v[v.length - 1].body.table.rows[1][2] === '6', 'élő validálás 250 ms után a teljes piszkozattal');
    check((await p.getAttribute(await cell(p, 'rbcg02', 'esemény1'), 'aria-invalid')) === null, 'a V006 jelzés eltűnt');
    check(/0 hiba/.test(await text(p, '#ex-summary')) && /k = 13\/13/.test(await text(p, '#ex-summary')), 'k = 13/13');
    check((await text(p, '#ex-status')).indexOf('nem mentett') >= 0, 'állapot: nem mentett módosítás');
    check((await calls(p, 'PUT', '/api/table')).length === 0, 'a validálás nem ment');
    await p.keyboard.press('Control+z');
    await settle(p);
    check((await cellText(p, 'rbcg02', 'esemény1')) === '600', 'Ctrl+Z visszaállítja');
    check((await p.getAttribute(await cell(p, 'rbcg02', 'esemény1'), 'aria-invalid')) === 'true', 'a V006 visszatér');
    await p.keyboard.press('Control+y');
    await settle(p);
    check((await cellText(p, 'rbcg02', 'esemény1')) === '6', 'Ctrl+Y újra alkalmazza');
    await p.keyboard.press('F2');
    check((await activeCell(p)).tag === 'INPUT', 'F2 → szerkesztés');
    await p.keyboard.press('Tab');
    a = await activeCell(p);
    check(a && a.tag === 'TD' && a.col !== 'c0', 'szerkesztés közben a Tab rögzít és jobbra lép');
    await p.keyboard.press('Shift+ArrowRight');
    check((await p.$$('.mg-table td[aria-selected="true"]')).length === 2, 'Shift+nyíl: tartomány-kijelölés');
    await p.keyboard.press('Escape');
    await p.keyboard.press('Tab');
    check(await p.evaluate(() => !document.activeElement.closest('.mg-table')), 'szerkesztésen kívül a Tab elhagyja a rácsot');
    await finish(o, 'billentyűzet');
  });

  // ========== 3. megállapítás-panel és „Nem hiba — indoklás”
  await test('megállapítások: Ugrás a cellára, „Nem hiba — indoklás” → naplózott döntés, „indokolt” állapot', async () => {
    const o = await openExtraction('o1');
    const p = o.page;
    await p.click('#ex-findings li.ex-f[data-code="V023"] .ex-f-jump');
    const a = await activeCell(p);
    check(a && a.uid === 'rbcg04' && a.col === 'c2', 'Ugrás → a hivatkozott cella (Hart n1) kap fókuszt');
    // UX-10: hibánál a párbeszéd kimondja, hogy az indoklás nem hozza vissza a sort; van „Miért?”; nincs „(engine)”
    check((await text(p, '#ex-findings')).indexOf('(engine)') < 0, 'a tanácsok mellett nincs belső „(engine)” forrásjelölés');
    await p.click('#ex-findings li.ex-f[data-code="V006"] .ex-f-ack');
    await p.waitForSelector('[role="dialog"] #ex-ack-rationale');
    const blk = await p.$eval('[role="dialog"]', (d) => {
      const n = d.querySelector('#ex-ack-blocking');
      return { note: n ? n.textContent : '', role: n ? n.getAttribute('role') : null, why: !!d.querySelector('.why-btn'),
        advice: (d.querySelector('.ex-ack-advice') || {}).textContent || '', ta: d.querySelector('#ex-ack-rationale').getAttribute('aria-describedby') || '' };
    });
    check(/kimarad az elemzésből/.test(blk.note) && /amíg az értéket ki nem javítod/.test(blk.note) && blk.role === 'note',
      'V006 (hiba): a párbeszéd kimondja, hogy a sor kimarad, amíg nem javítod (' + blk.note + ')');
    check(blk.why, 'a párbeszédben van „Miért?” gomb');
    check(blk.ta.split(' ').indexOf('ex-ack-blocking') >= 0, 'az indoklás mező aria-describedby-ja a „kimarad” megjegyzésre is mutat');
    check(/negatív vagy nagyobb, mint n/.test(blk.advice) && blk.advice.indexOf('(engine)') < 0, 'a motor tanácsa „(engine)” nélkül: ' + blk.advice);
    await p.click('[role="dialog"] .modal-actions .btn:not(.btn-primary)');
    await p.waitForFunction(() => !document.querySelector('.modal-backdrop'));
    await p.click('#ex-findings li.ex-f[data-code="V023"] .ex-f-ack');
    await p.waitForSelector('[role="dialog"] #ex-ack-rationale');
    check(!(await p.$('[role="dialog"] #ex-ack-blocking')), 'figyelmeztetésnél (V023) nincs „kimarad” megjegyzés');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    check(!(await p.$eval('[role="dialog"] .ex-cf-err', (x) => x.hidden)), 'indoklás nélkül nem küldhető (hibaüzenet)');
    check((await p.getAttribute('#ex-ack-rationale', 'aria-invalid')) === 'true', 'aria-invalid a mezőn');
    await p.fill('#ex-ack-rationale', 'A közlemény 2. táblázatában ezres tagolással: 13 598 fő.');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForSelector('#ex-findings li.ex-f[data-code="V023"].is-ack', { timeout: 4000 });
    const d = await calls(p, 'POST', '/api/log/decision');
    check(d.length === 1, 'POST /api/log/decision');
    const body = d[0].body;
    check(body.agent === 'user' && body.kb_refs[0] === 'V023' && body.context.row_uid === 'rbcg04' && /13 598/.test(body.rationale), 'a döntés: agent=user, kb_refs=[V023], a sor és az indoklás');
    check((await text(p, '#ex-findings li.ex-f[data-code="V023"]')).indexOf('indokolt #101') >= 0, 'a jelzés „indokolt #101”');
    await settle(p);
    check(await p.evaluate((s) => document.querySelector(s).classList.contains('is-ack'), await cell(p, 'rbcg04', 'n1')), 'a cella „indokolt” jelölést kap (a motor nem némul el)');
    await finish(o, 'indoklás');
  });

  // ========== 4. TSV-beillesztés Excelből
  await test('TSV-beillesztés: új sor, Excel-vágólap (ClipboardEvent), „2,000” szövegként → V023, beillesztő ablak', async () => {
    const o = await openExtraction('o1');
    const p = o.page;
    await p.click(await cell(p, 'rbcg13', 'vizsgálat'));
    await p.click('#ex-add-row');
    let a = await activeCell(p);
    check(a && a.col === 'c0' && a.uid !== 'rbcg13' && /^r[0-9a-v]{8}$/.test(a.uid), '+ sor: új sor a kijelölt után, kliens-oldali row_uid (' + (a && a.uid) + ')');
    const tsv = 'Új vizsgálat A\t3\t2,000\t5\t1990\r\n"Új ""B"" vizsgálat"\t1\t50\t2\t48\r\n';
    await p.evaluate((x) => {
      const dt = new DataTransfer();
      dt.setData('text/plain', x);
      document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    }, tsv);
    await settle(p);
    check((await p.$$('.mg-table tbody tr')).length === 15, 'a hiányzó sor a tábla végére került (15 sor)');
    check((await cellText(p, a.uid, 'n1')) === '2,000', 'a „2,000” szövegként maradt');
    const rows = await p.$$eval('.mg-table tbody tr', (trs) => trs.map((tr) => tr.querySelector('td').textContent));
    check(rows.indexOf('Új "B" vizsgálat') >= 0, 'Excel-idézés feloldva');
    const f = await p.$('#ex-findings li.ex-f[data-code="V023"][data-uid="' + a.uid + '"]');
    check(!!f, 'a motor V023-at ad az új sorra');
    await p.keyboard.press('Control+z');
    await settle(p);
    check((await p.$$('.mg-table tbody tr')).length === 14, 'Ctrl+Z: a beillesztés egy lépésben visszavonva');
    await p.click('#ex-paste');
    await p.waitForSelector('#ex-paste-text');
    await p.fill('#ex-paste-text', 'X1\t1\t10\t1\t10');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForSelector('#ma-toasts .toast');
    check((await text(p, '#ma-toasts')).indexOf('Beillesztve') >= 0, 'beillesztő ablak: visszajelzés');
    await finish(o, 'beillesztés');
  });

  // ========== 5. mentés If-Match-csel, 409 ütközés cellaszintű diffel
  await test('mentés: Ctrl+S If-Match-csel; külső szerkesztés → 409 → cellánként enyém / lemezen lévő → összefésülés', async () => {
    const o = await openExtraction('o1');
    const p = o.page;
    const etag0 = await p.evaluate(() => window.MA.extraction.current().etag);
    await p.click(await cell(p, 'rbcg02', 'esemény1'));
    await p.keyboard.type('6');
    await p.keyboard.press('Enter');
    await p.keyboard.press('Control+s');
    await p.waitForFunction(() => /mentve/.test(document.getElementById('ex-status').textContent), null, { timeout: 4000 });
    const puts = await calls(p, 'PUT', '/api/table');
    check(puts.length === 1 && puts[0].headers['If-Match'] === etag0, 'PUT /api/table If-Match = a betöltött ETag');
    check(puts[0].body.dataset === O1 && puts[0].body.rows[1].row_uid === 'rbcg02' && puts[0].body.rows[1].cells[1] === '6', 'a törzs: dataset, header, rows [{row_uid, cells}]');
    check(puts[0].body.header.indexOf('src') < 0 && puts[0].body.rows[0].cells.length === 10, 'a virtuális forrás-oszlop nem kerül a CSV-be');
    // mentés közbeni szerkesztés: a lassú mentés nem törölheti az újabb módosítás jelzőjét
    await p.evaluate(() => window.MA.dev.extraction.slowSave(500));
    await p.click(await cell(p, 'rbcg07', 'esemény1'));
    await p.keyboard.type('9');
    await p.keyboard.press('Enter');
    await p.keyboard.press('Control+s');
    await p.waitForFunction(() => /mentés …/.test(document.getElementById('ex-status').textContent), null, { timeout: 2000 });
    await p.click(await cell(p, 'rbcg08', 'esemény1'));
    await p.keyboard.type('7');
    await p.keyboard.press('Enter');
    await p.waitForFunction(() => !window.MA.extraction.current().saving, null, { timeout: 4000 });
    check(await p.evaluate(() => window.MA.extraction.current().tableDirty === true), 'a mentés alatti újabb szerkesztés jelzője megmarad');
    check((await text(p, '#ex-status')).indexOf('nem mentett') >= 0, 'állapot: nem mentett (az újabb módosítás miatt)');
    await p.evaluate(() => window.MA.dev.extraction.slowSave(0));
    await p.keyboard.press('Control+s');
    await p.waitForFunction(() => /mentve/.test(document.getElementById('ex-status').textContent), null, { timeout: 4000 });
    const p3 = await calls(p, 'PUT', '/api/table');
    check(p3.length === 3 && p3[2].body.rows[7].cells[1] === '7' && p3[2].body.rows[6].cells[1] === '9', 'a második mentés a későbbi módosítást is viszi');
    // külső szerkesztés: egy ütköző és egy nem ütköző cella
    await p.click(await cell(p, 'rbcg03', 'esemény1'));
    await p.keyboard.type('5');
    await p.keyboard.press('Enter');
    await p.evaluate((ds) => { window.MA.dev.extraction.externalEdit(ds, 'rbcg03', 'esemény1', '4'); window.MA.dev.extraction.externalEdit(ds, 'rbcg05', 'év', '1974'); }, O1);
    await p.waitForSelector('#ex-external');
    check((await text(p, '#ex-external')).indexOf('összefésülheted') >= 0, 'külső módosítás jelzése (mentetlen piszkozat mellett)');
    await p.keyboard.press('Control+s');
    await p.waitForSelector('#ex-conflict', { timeout: 4000 });
    const rowsCf = await p.$$eval('#ex-conflict-table tbody tr', (trs) => trs.map((tr) => ({ uid: tr.dataset.uid, col: tr.dataset.column, conflict: tr.classList.contains('is-conflict'),
      cells: Array.from(tr.querySelectorAll('td')).slice(1, 4).map((td) => td.textContent),
      theirsChecked: !!tr.querySelector('input[value="theirs"]:checked'), mineChecked: !!tr.querySelector('input[value="mine"]:checked') })));
    const c1 = rowsCf.find((r) => r.uid === 'rbcg03');
    const c2 = rowsCf.find((r) => r.uid === 'rbcg05');
    check(c1 && c1.conflict && c1.cells.join('|') === '3|4|5', 'ütköző cella: korábbi 3 · lemezen 4 · enyém 5');
    check(c2 && !c2.conflict && c2.theirsChecked, 'nem ütköző külső változás: alapból a lemezen lévő');
    check(c1 && !c1.mineChecked && !c1.theirsChecked, 'az ütközőnél nincs alapértelmezett választás');
    await p.click('#ex-conflict .modal-actions .btn-primary');
    check(!(await p.$eval('#ex-conflict .ex-cf-err', (x) => x.hidden)), 'választás nélkül nem fésül össze');
    await p.check('#ex-conflict-table tr[data-uid="rbcg03"] input[value="mine"]');
    await p.click('#ex-conflict .modal-actions .btn-primary');
    await p.waitForFunction(() => !document.getElementById('ex-conflict') && /mentve/.test(document.getElementById('ex-status').textContent), null, { timeout: 4000 });
    const puts2 = await calls(p, 'PUT', '/api/table');
    const last = puts2[puts2.length - 1];
    const st = await p.evaluate((ds) => window.MA.dev.extraction.state().tables[ds].data, O1);
    check(puts2.length === 5 && last.headers['If-Match'] !== puts2[3].headers['If-Match'], 'újramentés a friss ETaggel');
    check((await cellText(p, 'rbcg03', 'esemény1')) === '5' && (await cellText(p, 'rbcg05', 'év')) === '1974', 'összefésülve: enyém (5) + lemezen lévő (1974)');
    check(st.rows[2].cells[1] === '5' && st.rows[4].cells[6] === '1974', 'a „szerveren” az összefésült változat');
    check((await text(p, (await cell(p, 'rbcg05', 'év')) + ' .mg-m')).indexOf('⟳') >= 0, 'az átvett cella külső-szerkesztés jelölést kap');
    await finish(o, 'mentés/ütközés');
  });

  // ========== 6. 423 Excel-zár + DOM-biztonság
  await test('423 LOCKED (Excel-zár) → üzenet és Újra; HTML-szerű vizsgálatcímke szövegként (DOM-biztonság)', async () => {
    const o = await openExtraction('o2');
    const p = o.page;
    const grid = await text(p, '.mg-table tbody');
    check(grid.indexOf('<img src=x onerror=alert(1)>') >= 0 && grid.indexOf('"><script>alert(1)</script>') >= 0, 'a címkék szó szerint, szövegként látszanak');
    check((await p.$$('#screen-root img, #screen-root script, .mg-table b')).length === 0, 'nem jött létre <img>/<script> elem');
    check(await p.evaluate(() => typeof window.__xss === 'undefined'), 'nem futott kód');
    check((await text(p, '#ex-summary')).indexOf('k = 4/4') >= 0 && !!(await p.$('#ex-findings li.ex-f[data-code="V015"]')), 'kis k: V015 (táblaszintű)');
    await p.click(await cell(p, 'rmrt01', 'e1'));
    await p.keyboard.type('1');
    await p.keyboard.press('Enter');
    await p.keyboard.press('Control+s');
    await p.waitForSelector('#ex-locked', { timeout: 4000 });
    const t1 = await text(p, '#ex-locked');
    check(t1.indexOf('Zárd be a fájlt az Excelben') >= 0, 'a szerver üzenete szó szerint: ' + t1);
    check((await p.getAttribute('#ex-locked', 'role')) === 'alert', 'role=alert');
    check((await text(p, '#ex-status')).indexOf('nem mentett') >= 0, 'a piszkozat megmarad');
    await p.evaluate((ds) => window.MA.dev.extraction.lock(ds, false), O2);
    await p.click('#ex-locked-retry');
    await p.waitForFunction(() => !document.getElementById('ex-locked') && /mentve/.test(document.getElementById('ex-status').textContent), null, { timeout: 4000 });
    check(true, 'Újra → mentve, a zár-üzenet eltűnt');
    await finish(o, 'zár');
  });

  // ========== 7. eredet-panel, PDF-oldal, dokumentum-jegyzék
  await test('eredet-panel (Alt+P): forrás, előzmény, PDF az oldalon (aláírt URL), szerkesztés PUT If-Match-csel, dokumentum-jegyzék', async () => {
    const o = await openExtraction('o1');
    const p = o.page;
    await p.click(await cell(p, 'rbcg10', 'esemény1'));
    const prov = await text(p, '#ex-prov');
    check(prov.indexOf('DIGITALIZÁLT') >= 0 && prov.indexOf('7. oldal') >= 0 && prov.indexOf('Figure 2') >= 0 && prov.indexOf('17/1716') >= 0, 'módszer, oldal (szövegként is), hely, idézet');
    check(prov.indexOf('előzmény (1)') >= 0, 'előzmény');
    const [popup] = await Promise.all([p.waitForEvent('popup'), p.click('#ex-open-source')]);
    await popup.waitForURL(/\/f\//, { timeout: 4000 });
    const pu = popup.url();
    check(/\/f\/doi%3A10\.0000%2Fbcg\.rosenthal1961\//.test(pu) && /#page=7$/.test(pu), 'aláírt fájl-URL új lapon, #page=7: ' + pu);
    const fu = await calls(p, 'POST', '/api/fileurl');
    check(fu.length === 1 && fu[0].body.doc === 'doi:10.0000/bcg.rosenthal1961', 'POST /api/fileurl {doc}');
    await popup.close();
    await p.click(await cell(p, 'rbcg01', 'esemény2'));
    check((await text(p, '#ex-prov')).indexOf('nincs rögzítve') >= 0, 'eredet nélküli cella');
    await p.selectOption('#ex-prov-doc', 'file:_privat/pdf/aronson1948.pdf');
    await p.fill('#ex-prov-page', 'x');
    await p.click('#ex-prov-save');
    check((await p.getAttribute('#ex-prov-page', 'aria-invalid')) === 'true', 'hibás oldalszám: aria-invalid');
    await p.fill('#ex-prov-page', '3');
    await p.fill('#ex-prov-locator', 'Table 1');
    await p.fill('#ex-prov-quote', '11 cases among 139 unvaccinated');
    await p.click('#ex-prov-save');
    await p.waitForFunction(() => /mentve/.test(document.getElementById('ex-status').textContent), null, { timeout: 4000 });
    const pp = await calls(p, 'PUT', '/api/provenance');
    check(pp.length === 1 && pp[0].query.dataset === O1 && pp[0].headers['If-Match'] === '"prov-o1-1"', 'PUT /api/provenance?dataset=, If-Match');
    const entry = pp[0].body.cells.find((c) => c.row_uid === 'rbcg01' && c.field === 'e2');
    check(entry && entry.method === 'reported' && entry.source.page === 3 && entry.source.locator === 'Table 1' && entry.extracted_by === 'SzK', 'szk.ma.provenance/v1 bejegyzés (kanonikus mező: e2)');
    check(pp[0].body.schema === 'szk.ma.provenance/v1' && pp[0].body.table === O1, 'séma és tábla');
    check((await text(p, '.mg-table tr[data-uid="rbcg01"] td[data-col="src"]')).indexOf('+2') >= 0, 'a forrás-oszlop frissült');
    await p.keyboard.press('Alt+p');
    check((await p.$eval('#ex-prov', (x) => x.hidden)) && (await p.getAttribute('#ex-prov-toggle', 'aria-pressed')) === 'false', 'Alt+P elrejti a panelt');
    await p.keyboard.press('Alt+p');
    check(!(await p.$eval('#ex-prov', (x) => x.hidden)), 'Alt+P újra megjeleníti');
    await p.click('#ex-docs-btn');
    await p.waitForSelector('#ex-docs-table');
    check((await p.$$('#ex-docs-table tbody tr')).length === 6, 'dokumentum-jegyzék: 6 tétel');
    check((await text(p, '#ex-docs-table')).indexOf('<b>Coetzee</b>') >= 0 && (await p.$$('#ex-docs img, #ex-docs b')).length === 0, 'HTML-szerű cím szövegként');
    await p.fill('#ex-doc-id', 'xyz');
    await p.click('#ex-doc-add');
    check((await text(p, '#ex-docs .ex-cf-err')).indexOf('azonosító') >= 0, 'érvénytelen azonosító elutasítva');
    await p.fill('#ex-doc-id', 'pmid:12345678');
    await p.fill('#ex-doc-path', '_privat/pdf/uj.pdf');
    await p.fill('#ex-doc-pages', '9');
    await p.click('#ex-doc-add');
    await p.waitForFunction(() => document.querySelectorAll('#ex-docs-table tbody tr').length === 7, null, { timeout: 4000 });
    const pd = await calls(p, 'PUT', '/api/documents');
    check(pd.length === 1 && pd[0].headers['If-Match'] === '"docs-1"' && pd[0].body.schema === 'szk.ma.documents/v1' && pd[0].body.docs[6].pages === 9, 'PUT /api/documents If-Match-csel, szk.ma.documents/v1');
    await p.keyboard.press('Escape');
    await finish(o, 'eredet');
  });

  // ========== 8. átváltó
  await test('átváltó: medián/IQR → átlag, SD (motor, becsült), 422 inline, beírás egy tranzakcióban; SE → SD (számított); undo', async () => {
    const o = await openExtraction('o3');
    const p = o.page;
    check((await p.getAttribute(await cell(p, 'rnrm05', 'm1'), 'aria-invalid')) === 'true', 'V002: Montreal-Home m1 hiányzik');
    await p.click(await cell(p, 'rnrm05', 'm1'));
    await p.click('#ex-convert');
    await p.waitForSelector('#cv-modal');
    check((await p.inputValue('#cv-kind')) === 'median_to_mean_sd' && (await p.inputValue('#cv-in-n')) === '8', 'típus: medián/IQR; n a sorból (n1 = 8)');
    const mCol = await p.evaluate((s) => document.querySelector(s).dataset.col, await cell(p, 'rnrm05', 'm1'));
    check((await p.inputValue('#cv-target-mean')) === mCol, 'cél: m1 (a kar szerint)');
    await p.fill('#cv-in-median', '12,5');
    await p.fill('#cv-in-q1', '10');
    await p.waitForSelector('#cv-error:not([hidden])', { timeout: 4000 });
    check((await text(p, '#cv-error')).indexOf('min+max vagy Q1+Q3 kell') >= 0, '422 VALIDATION a motor üzenetével, a modálisban');
    await p.fill('#cv-in-q3', '16');
    await p.waitForFunction(() => /12\.87/.test(document.getElementById('cv-result').textContent), null, { timeout: 4000 });
    const res = await text(p, '#cv-result');
    check(res.indexOf('5.361') >= 0 && res.indexOf('BECSÜLT ÉRTÉK') >= 0 && res.indexOf('conversions.mean_from_median') >= 0, 'a motor kijelzési szövegei (12.87; 5.361 — tizedespont, 4.0), módszer, becsült');
    check(res.indexOf('közel normális') >= 0 && res.indexOf('D-S05-023') >= 0 && res.indexOf('Cochrane Handbook 6.5.2.5') >= 0, 'feltevés, figyelmeztetés ({hu, en}), forrás');
    const cv = await calls(p, 'POST', '/api/convert');
    const req = cv[cv.length - 1].body;
    check(req.schema === 'szk.ma.convert-request/v1' && req.method === 'luo' && req.inputs.median === '12,5' && req.inputs.n === '8', 'convert-request: nyers szövegek, módszer');
    check(req.target.row_uid === 'rnrm05' && req.target.fields.mean === 'm1' && req.target.fields.sd === 'sd1' && req.target.decimal_mark === ',', 'cél: row_uid, kanonikus mezők, tizedesjel');
    await p.fill('#cv-page', '7');
    await p.fill('#cv-locator', 'Table 2');
    await p.click('#cv-modal .modal-actions .btn-primary');
    await p.waitForFunction(() => !document.getElementById('cv-modal') && /mentve/.test(document.getElementById('ex-status').textContent), null, { timeout: 5000 });
    check((await cellText(p, 'rnrm05', 'm1')) === '12,87' && (await cellText(p, 'rnrm05', 'sd1')) === '5,361', 'a cellákba a motor cell_text-je került (a tábla tizedesvesszőjével)');
    check((await cellText(p, 'rnrm05', 'estimated')) === 'igen', 'a sor estimated = igen');
    const puts = await calls(p, 'PUT', '/api/table');
    const pv = puts[puts.length - 1].body.provenance;
    const pm = pv && pv.cells.find((c) => c.row_uid === 'rnrm05' && c.field === 'm1');
    check(!!pm && pm.method === 'estimated' && pm.estimated === true && pm.conversion.kind === 'median_to_mean_sd' && pm.source.page === 7, 'egy tranzakció: tábla + eredet (estimated, conversion, forrás)');
    await settle(p);
    check(!(await p.$('#ex-findings li.ex-f[data-code="V002"]')) && !!(await p.$('#ex-findings li.ex-f[data-code="V018"][data-uid="rnrm05"]')), 'V002 eltűnt, V018 (becsült) megjelent');
    check((await text(p, (await cell(p, 'rnrm05', 'm1')) + ' .mg-m')).indexOf('◆') >= 0, 'becsült cella ◆');
    await p.click(await cell(p, 'rnrm05', 'm1'));
    await p.keyboard.press('Control+z');
    await settle(p);
    check((await cellText(p, 'rnrm05', 'm1')) === '' && (await cellText(p, 'rnrm05', 'estimated')) === 'nem', 'Ctrl+Z: az átváltás egy lépésben visszavonva');
    check(await p.evaluate(() => !window.MA.extraction.current().prov.cells.some((c) => c.row_uid === 'rnrm05' && c.field === 'm1')), 'az eredet-bejegyzés is visszavonva');
    await p.keyboard.press('Control+y');
    check((await cellText(p, 'rnrm05', 'm1')) === '12,87', 'Ctrl+Y');
    // SE → SD: algebrai, nem becsült
    await p.click(await cell(p, 'rnrm02', 'sd1'));
    await p.click('#ex-convert');
    await p.waitForSelector('#cv-modal');
    await p.selectOption('#cv-kind', 'se_to_sd');
    check((await p.inputValue('#cv-in-n')) === '31', 'n a sorból (31)');
    await p.fill('#cv-in-n', '52');
    await p.fill('#cv-in-se', '0,42');
    await p.waitForFunction(() => /3\.029/.test(document.getElementById('cv-result').textContent), null, { timeout: 4000 });
    check((await text(p, '#cv-result')).indexOf('SZÁMÍTOTT') >= 0, 'algebrai átalakítás: SZÁMÍTOTT (nem becsült)');
    await p.click('#cv-modal .modal-actions .btn-primary');
    await p.waitForFunction(() => !document.getElementById('cv-modal'), null, { timeout: 4000 });
    check((await cellText(p, 'rnrm02', 'sd1')) === '3,029' && (await cellText(p, 'rnrm02', 'estimated')) === 'nem', 'SD beírva (cell_text), a sor nem lett becsült');
    const calc = await p.evaluate(() => window.MA.extraction.current().prov.cells.find((c) => c.row_uid === 'rnrm02' && c.field === 'sd1'));
    check(calc && calc.method === 'calculated' && calc.estimated === false && calc.history.length === 1, 'eredet: calculated (+ előzmény a korábbi „reported”-ről)');
    await finish(o, 'átváltó');
  });

  // ========== 9. mentetlen módosítás őre, nyelvváltás
  await test('mentetlen módosítás: navigáláskor Maradok / Elvetés / Mentés és tovább; nyelvváltás megtartja a piszkozatot', async () => {
    const o = await openExtraction('o1');
    const p = o.page;
    await p.click(await cell(p, 'rbcg02', 'esemény1'));
    await p.keyboard.type('6');
    await p.keyboard.press('Enter');
    await p.click('#lang-switch [data-lang="en"]');
    await p.waitForFunction(() => /Findings/.test(document.getElementById('ex-findings').textContent), null, { timeout: 4000 });
    check((await cellText(p, 'rbcg02', 'esemény1')) === '6', 'EN: a piszkozat megmaradt');
    check((await text(p, '#ex-status')).indexOf('unsaved changes') >= 0, 'EN felület');
    await settle(p);
    await p.click('#lang-switch [data-lang="hu"]');
    await settle(p);
    await p.click('#nav-overview');
    await p.waitForSelector('[role="dialog"]');
    check((await text(p, '[role="dialog"]')).indexOf('Mentetlen módosítások') >= 0, 'őr-dialógus');
    await p.click('[role="dialog"] .modal-actions .btn-ghost');
    await p.waitForTimeout(150);
    check((await p.evaluate(() => location.hash)).indexOf('#/extraction') === 0 && (await cellText(p, 'rbcg02', 'esemény1')) === '6', 'Maradok: a képernyő és a piszkozat marad');
    await p.click('#nav-overview');
    await p.waitForSelector('[role="dialog"]');
    await p.click('[role="dialog"] .modal-actions .btn-danger');
    await p.waitForSelector('#screen-root[data-screen="overview"]');
    await p.click('#nav-extraction');
    await p.waitForSelector('#screen-root[data-screen="extraction"] table[role="grid"]');
    await settle(p);
    check((await cellText(p, 'rbcg02', 'esemény1')) === '600', 'Elvetés: újratöltve a lemezről');
    await p.click(await cell(p, 'rbcg02', 'esemény1'));
    await p.keyboard.type('6');
    await p.keyboard.press('Enter');
    await p.keyboard.press('Alt+0');
    await p.waitForSelector('[role="dialog"]');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForSelector('#screen-root[data-screen="overview"]');
    check((await calls(p, 'PUT', '/api/table')).length === 1, 'Mentés és tovább: PUT, majd navigálás');
    await finish(o, 'őr');
  });

  // ========== 10. projekt és adatvédelem
  await test('projekt: legutóbbiak, adatvédelmi tételek, osztály-előnézet és -váltás, diff-előnézet + kifejezett jóváhagyás', async () => {
    const o = await openPage('#/project');
    const p = o.page;
    await p.waitForSelector('#pj-checks');
    check((await p.$$('.pj-recent-btn')).length === 3, 'legutóbbi projektek (3)');
    const chk = await p.$$eval('#pj-checks li', (ls) => ls.map((l) => l.dataset.check + ':' + l.className));
    check(chk.some((c) => /^vault:.*is-warn/.test(c)) && chk.some((c) => /^cloud:.*is-warn/.test(c)) && chk.some((c) => /^gitignore:.*is-ok/.test(c)), 'tételek: vault ⚠, felhőszinkron ⚠, .gitignore-blokk ✔');
    check((await text(p, '#pj-privacy')).indexOf('VÉDVE') >= 0 && (await text(p, '#pj-privacy')).indexOf('OneDrive') >= 0, 'állapot és a szerver javaslatai szó szerint');
    check((await text(p, '#pj-class-panel')).indexOf('betegszintű') >= 0, 'adatosztályok magyarázattal');
    await p.focus('#pj-class-A');
    await p.keyboard.press('ArrowDown');
    check(await p.isChecked('#pj-class-B'), 'nyíllal B osztály');
    await p.waitForFunction(() => /Előnézet B osztállyal/.test(document.getElementById('pj-class-preview').textContent), null, { timeout: 4000 });
    const prev = await calls(p, 'GET', '/api/privacy');
    check(prev.some((c) => c.query.data_class === 'B'), 'GET /api/privacy?data_class=B (előnézet, nem ír)');
    check((await calls(p, 'POST', '/api/project')).length === 0, 'az előnézet nem módosít');
    await p.click('#pj-class-apply');
    await p.waitForSelector('[role="dialog"]');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForSelector('#pj-act-precommit', { timeout: 4000 });
    const pr = await calls(p, 'POST', '/api/project');
    check(pr.length === 1 && pr[0].body.action === 'data_class' && pr[0].body.data_class === 'B', 'POST /api/project {action: data_class, B}');
    check((await text(p, '.privacy-summary')).indexOf('B osztály') >= 0, 'a fejléc is frissült');
    await p.click('#pj-act-precommit');
    await p.waitForSelector('#pj-apply');
    check((await text(p, '#pj-apply')).indexOf('Következmény') >= 0 && (await text(p, '#pj-apply')).indexOf('csak tartalék') >= 0, 'előnézet: a következmény szövege (vault-mentés megállhat)');
    await p.click('#pj-apply .modal-actions .btn-primary');
    check(!(await p.$eval('#pj-apply .ex-cf-err', (x) => x.hidden)), 'jóváhagyás nélkül nem alkalmazható');
    check((await calls(p, 'POST', '/api/privacy/apply')).filter((c) => c.body.confirm).length === 0, 'nem ment ki írás');
    await p.check('#pj-agree');
    await p.click('#pj-apply .modal-actions .btn-primary');
    await p.waitForFunction(() => !document.getElementById('pj-apply') && !document.getElementById('pj-act-precommit'), null, { timeout: 4000 });
    const ap = (await calls(p, 'POST', '/api/privacy/apply')).map((c) => c.body);
    check(ap.length === 2 && ap[0].dry_run === true && ap[1].confirm === true && ap[1].action === 'precommit', 'dry_run → confirm sorrend');
    // .gitignore- és deny-hiány: diff-előnézet
    await p.evaluate(() => window.MA.dev.extraction.privacy({ gitignore: false, deny: false }));
    await p.evaluate(() => window.MA.app.refresh());
    await p.waitForSelector('#pj-act-gitignore', { timeout: 4000 });
    check(!!(await p.$('#pj-act-deny')) && (await p.$$eval('#pj-checks li[data-check="gitignore"].is-bad', (x) => x.length)) === 1, 'hiányzó blokk: ✖ és felajánlott műveletek');
    await p.click('#pj-act-gitignore');
    await p.waitForSelector('#pj-diff');
    check((await text(p, '#pj-diff')).indexOf('+_privat/') >= 0 && (await p.$$('#pj-diff .pj-dl.is-add')).length >= 9, 'diff-előnézet (+ sorok jelölve, nem csak színnel)');
    await p.check('#pj-agree');
    await p.click('#pj-apply .modal-actions .btn-primary');
    await p.waitForFunction(() => !document.getElementById('pj-act-gitignore'), null, { timeout: 4000 });
    const gi = (await calls(p, 'POST', '/api/privacy/apply')).map((c) => c.body).filter((b) => b.action === 'gitignore');
    check(gi.length === 2 && gi[1].confirm === true && /^[0-9a-f]{64}$/.test(gi[1].base_sha256), 'confirm + base_sha256 (az előnézet alapja)');
    // megnyitás és új projekt
    await p.click('#pj-open-btn');
    check(!(await p.$eval('#pj-open .ex-cf-err', (x) => x.hidden)), 'üres útvonal: hibaüzenet');
    check((await text(p, '#pj-open')).indexOf('új példány') >= 0, 'egy szerver = egy projekt: a felület kimondja');
    await p.click('.pj-recent-btn[data-path="reviews/glp1-terhesseg"]');
    await p.waitForSelector('#ma-alerts .toast[data-code="BAD_REQUEST"]');
    check((await text(p, '#ma-alerts')).indexOf('új példányt') >= 0, 'másik projektmappa → a szerver üzenete szó szerint (BAD_REQUEST)');
    await p.click('#ma-alerts .toast .toast-close');
    await p.click('.pj-recent-btn[data-path="reviews/bcg-oltas"]');
    await p.waitForFunction(() => /Megnyitva/.test(document.getElementById('ma-toasts').textContent), null, { timeout: 4000 });
    check((await calls(p, 'POST', '/api/project')).some((c) => c.body.action === 'open' && c.body.path === 'reviews/bcg-oltas'), 'legutóbbi → POST open');
    await p.click('#pj-init summary');
    check((await p.inputValue('#pj-init-path')) === 'reviews/bcg-oltas', 'project init: a mostani projektmappa előtöltve');
    await p.fill('#pj-init-title', 'Új áttekintés');
    await p.check('#pj-init-class-C');
    await p.click('#pj-init-btn');
    await p.waitForSelector('[role="dialog"]');
    await p.click('[role="dialog"] .modal-actions .btn-primary');
    await p.waitForFunction(() => /Új áttekintés/.test(document.querySelector('.brand-project').textContent), null, { timeout: 4000 });
    const ini = (await calls(p, 'POST', '/api/project')).find((c) => c.body.action === 'init');
    check(ini && ini.body.data_class === 'C' && ini.body.title === 'Új áttekintés' && ini.body.path === 'reviews/bcg-oltas', 'POST init {path, title, data_class}');
    await p.click('#nav-extraction');
    await p.waitForSelector('#screen-root[data-screen="extraction"] .empty-state');
    check(true, 'új (üres) projekt: a kinyerés üres-állapotot mutat');
    await finish(o, 'projekt');
  });

  // ========== 11. PHI-szkenner és írás-tartás (403)
  await test('mentés: PHI-gyanú (403) → érték nélküli találatok, indokolt felülbírálás; B osztály írás-tartás → hozzájárulás', async () => {
    const o = await openExtraction('o1');
    const p = o.page;
    await p.click(await cell(p, 'rbcg07', 'allokáció'));
    await p.keyboard.type('kovacs.janos@pelda.hu');
    await p.keyboard.press('Enter');
    await p.keyboard.press('Control+s');
    await p.waitForSelector('#ex-phi', { timeout: 4000 });
    const dlg = await text(p, '#ex-phi');
    check(dlg.indexOf('PHI-gyanú') >= 0 && dlg.indexOf('7. sor') >= 0 && dlg.indexOf('email') >= 0, 'a találat: oszlop, sor, minta');
    check(dlg.indexOf('kovacs.janos@pelda.hu') < 0, 'a gyanús érték nem jelenik meg a jelentésben (T10)');
    await p.click('#ex-phi .modal-actions .btn-danger');
    check(!(await p.$eval('#ex-phi .ex-cf-err', (x) => x.hidden)), 'indoklás nélkül nem bírálható felül');
    await p.fill('#ex-phi-reason', 'Az allokáció oszlopban tesztcím; nem betegadat.');
    await p.click('#ex-phi .modal-actions .btn-danger');
    await p.waitForFunction(() => /mentve/.test(document.getElementById('ex-status').textContent), null, { timeout: 4000 });
    const puts = await calls(p, 'PUT', '/api/table');
    check(puts.length === 2 && !puts[0].body.phi_override && /tesztcím/.test(puts[1].body.phi_override || ''), 'újraküldés phi_override-dal');
    await p.evaluate(() => window.MA.api.post('/api/project', { action: 'data_class', data_class: 'B' }).then(() => window.MA.app.loadBase()));
    await p.click(await cell(p, 'rbcg07', 'allokáció'));
    await p.keyboard.type('random');
    await p.keyboard.press('Enter');
    await p.keyboard.press('Control+s');
    await p.waitForSelector('#ex-hold', { timeout: 4000 });
    check((await text(p, '#ex-hold')).indexOf('Írás-tartás: B osztály') >= 0 && (await p.getAttribute('#ex-hold', 'role')) === 'alert', 'írás-tartás: a szerver indoklása, role=alert');
    check(!!(await p.$('#ex-hold a[href="#/project"]')), 'hivatkozás az adatvédelmi teendőkhöz');
    await p.click('#ex-hold-consent');
    await p.waitForSelector('[role="dialog"]');
    await p.click('[role="dialog"] .modal-actions .btn-danger');
    await p.waitForFunction(() => !document.getElementById('ex-hold') && /mentve/.test(document.getElementById('ex-status').textContent), null, { timeout: 4000 });
    const last = (await calls(p, 'PUT', '/api/table')).pop();
    check(last.body.consent === true, 'B osztály: kifejezett, naplózott hozzájárulással (consent: true)');
    await finish(o, 'PHI/írás-tartás');
  });

  // ========== 12. oldalon belüli önteszt (a saját állításokkal)
  await test('?selftest=1: a rács, a cellára képzés, az átváltó és a projekt öntesztje is zöld', async () => {
    const o = await openPage('', { query: 'selftest=1' });
    const p = o.page;
    await p.waitForSelector('html[data-selftest]', { timeout: 30000 });
    const out = await text(p, '#selftest');
    const m = /SELFTEST pass=(\d+) fail=(\d+)/.exec(out);
    check(!!m && m[2] === '0', 'fail=0' + (m && m[2] !== '0' ? '\n' + out.split('\n').filter((l) => l.startsWith('FAIL')).join('\n') : ''));
    ['grid: TSV', 'grid: billentyűzet', 'kinyerés: megállapítás', 'átváltó: cellaszöveg', 'projekt: adatvédelmi'].forEach((n) => check(out.indexOf('ok - ' + n) >= 0, 'önteszt fut: ' + n));
    await finish(o, 'önteszt');
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
