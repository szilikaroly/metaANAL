#!/usr/bin/env node
/* tests/gui/ui/e2e_v1.spec.js — a v1 elfogadási teszt (terv 9.3 „Elfogadás”, 8.6 2. réteg): végponttól végpontig a
 * VALÓDI munkapad-szerverrel (ma_gui.server, port 0) ideiglenes projekteken, a termék-builddel, a valódi motorral és —
 * ha elérhető — a valódi szk-plugins pluginokkal (validator 1.0.x bridge-módban, figure-forge).
 *
 * Futtatás:  node tests/gui/ui/e2e_v1.spec.js            (kilépési kód 1, ha bármi elbukik)
 *   E2E_V1_ONLY=appraisal,grade,dual,plots,figures,composer,hh,xrules,final,replay   — csak a megnevezett részek
 *                                                (és amire épülnek: pl. figures → appraisal + analysis)
 *   MA_GUI_PLUGIN_DIRS=<szk-plugins/plugins>   — a valódi pluginok (alapból a szokásos helyük, ha létezik)
 *   MA_GUI_TEST_FF_PYTHON=<python>             — matplotlibes interpreter a figure-forge-hoz (nélküle: „nem használható”
 *                                                állapot a teendővel; ha a figure-forge plugin nincs telepítve: a
 *                                                beépített SVG-audit)
 * A projekteket és a szervereket a tests/gui/ui/e2e_v1_server.py készíti (main / xrules / hh); a végén törlődnek
 * (E2E_V1_KEEP=1: megmaradnak).
 *
 * Elfogadási lépések (a végén ellenőrzőlista ✔/✖):
 *   1. értékelési munkafolyamatok: RoB 2, ROBINS-I, ROBINS-E, QUADAS-2, NOS, PROBAST+AI (34 hely, menetenkénti
 *      teljesség), TRIPOD+AI (52 tétel, D/E), AMSTAR 2 (mindkét konvenció) — a motor eszközeivel ÉS a validator
 *      keresztellenőrzésével (a doboz egyezik, vagy megmagyarázza az eltérést; H1–H4, H12, H13 őrök); két emberi
 *      értékelő → κ → konszenzus → forgalmi lámpa → rob-oszlop szinkron → „magas RoB nélkül” érzékenységi futás →
 *      X003/X006 tiszta; AI-vázlat (jelvény, kezdőbarát indoklás, jóváhagyás kell, kimarad a κ-ból);
 *   2. GRADE kimenetenként a motor tanácsával, „gyanított” publikációs torzítás → a rögzítés tiltva (X019), amíg ember
 *      nem dönt; az ember megerősíti a bizonyosságot; SoF két alapkockázattal → CSV (képletinjekció-őr) és Markdown;
 *   3. kettős kinyerés A/B → összevetés (csak írásmód vs valódi eltérés, súgó, hatás) → egyeztetés → konszenzus-CSV →
 *      elemzés;
 *   4. kumulatív és buborékábra a motor geometriájával;
 *   5. ábra-export: motor-SVG magyarul és angolul; figure-forge audit (ha a függőségei megvannak), különben a
 *      „nem használható” állapot a teendővel;
 *   6. composer-híd: a composer (teszt-csonk) prisma-flow.json-jának beolvasása;
 *   7. Metaheadhunter a felületről kazettákkal (élő hálózat nélkül): keresés → kinyerés → feloldás → duplum-megerősítés
 *      → kizárás → frissítés → egyesítés → a PRISMA-ellenőrzés átmegy;
 *   8. audit: a teljes X-szabálykészlet egy felépített projekten; a FINAL-kapu előbb tilt, majd átenged;
 *   9. lánc-visszajátszás (tests/gui/test_chain_replay.py) változatlanul.
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
const HELPER = path.join(__dirname, 'e2e_v1_server.py');
const CHROME_FALLBACK = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
// a részek függőségei a közös fő projekten: az elemzés (1f) a rob-szinkronra épül, a FINAL-kapu a végigvitt projektre
const DEPS = { analysis: ['appraisal'], grade: ['analysis'], plots: ['analysis'], figures: ['analysis'],
  final: ['appraisal', 'analysis', 'grade', 'dual', 'plots', 'composer', 'figures'] };
const ONLY = (function () {
  const out = [];
  const add = (part) => { if (out.indexOf(part) < 0) { out.push(part); (DEPS[part] || []).forEach(add); } };
  (process.env.E2E_V1_ONLY || '').split(',').map((s) => s.trim()).filter(Boolean).forEach(add);
  return out;
})();
const want = (part) => !ONLY.length || ONLY.indexOf(part) >= 0;
// ideiglenes projektek: a végén törlődnek (E2E_V1_KEEP=1: megmaradnak a hibakereséshez)
const TMPS = [];
const mkTmp = (prefix) => { const d = fs.mkdtempSync(path.join(os.tmpdir(), prefix)); TMPS.push(d); return d; };
const PRIVATE = 'Titkos-megjegyzés-7731';       // szabad szöveg: soha nem kerülhet az activity-naplóba

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
  const t0 = Date.now();
  try { await fn(); } catch (e) {
    check(false, 'kivétel: ' + (e && e.stack ? e.stack.split('\n').slice(0, 6).join(' | ') : e));
    await shot(CUR_PAGE, 'exc_' + name);
  }
  console.log('  (' + Math.round((Date.now() - t0) / 1000) + ' s)');
}
const CHECKLIST = [];
function step(label, tests) { CHECKLIST.push({ label, tests }); }

function run(cmd, args, opts) {
  const r = spawnSync(cmd, args, Object.assign({ cwd: ROOT, encoding: 'utf-8', maxBuffer: 64 * 1024 * 1024 }, opts || {}));
  if (r.status !== 0) { throw new Error(cmd + ' ' + args.join(' ') + ' → ' + r.status + '\n' + r.stdout + r.stderr); }
  return r.stdout;
}
const py = (args) => JSON.parse(run('python3', [HELPER].concat(args)).trim().split('\n').pop());

// ---------------------------------------------------------------- a valódi szerver (fajtánként külön folyamat)
function startServer(tmp, kind, env) {
  const proc = spawn('python3', [HELPER, 'serve', tmp, kind], { cwd: ROOT, stdio: ['pipe', 'pipe', 'pipe'],
    env: Object.assign({}, process.env, env || {}) });
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
    const timer = setTimeout(() => reject(new Error('a szerver nem válaszolt: ' + err)), 120000);
    waiters.push((v) => { clearTimeout(timer); resolve(v); });
  });
  return next().then((info) => Object.assign(info, {
    newCode: () => { const p = next(); proc.stdin.write('code\n'); return p.then((v) => v.code); },
    stop: () => new Promise((resolve) => { proc.on('exit', resolve); proc.stdin.write('quit\n'); setTimeout(() => proc.kill(), 15000); }),
    stderr: () => err
  }));
}

// ---------------------------------------------------------------- oldal-segédek
let browser;
let CUR_PAGE = null;
function attach(page, srv, sink) {
  page.on('console', (m) => {
    if (m.type() === 'error' && !/^Failed to load resource: the server responded with a status of 4\d\d/.test(m.text())) { sink.errors.push('console: ' + m.text()); }
  });
  page.on('pageerror', (e) => sink.errors.push('pageerror: ' + e.message + (process.env.E2E_V1_STACK ? ' @ ' + String(e.stack || '').split('\n').slice(0, 6).join(' | ') : '')));
  page.on('dialog', (d) => { sink.errors.push('dialog: ' + d.message()); d.dismiss().catch(() => null); });
  page.on('request', (r) => {
    const u = r.url();
    if (u.indexOf(srv.base + '/api/') !== 0) { return; }
    let body = null;
    try { body = r.postData() ? JSON.parse(r.postData()) : null; } catch (e) { body = null; }
    const url = new URL(u);
    sink.requests.push({ method: r.method(), path: url.pathname, query: url.search, body });
  });
  page.on('response', (r) => {
    const u = r.url();
    if (u.indexOf(srv.base) === 0 && r.status() >= 500) { sink.http5xx.push(r.status() + ' ' + r.request().method() + ' ' + u.slice(srv.base.length)); }
  });
}
async function openApp(srv, opts) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true, locale: 'hu-HU' });
  if (opts && opts.rater) {
    await ctx.addInitScript((r) => { try { localStorage.setItem('mag.pref.appraisal.rater', r); } catch (e) { /* nem kritikus */ } }, opts.rater);
  }
  const page = await ctx.newPage();
  CUR_PAGE = page;
  const sink = { errors: [], requests: [], http5xx: [] };
  attach(page, srv, sink);
  const code = await srv.newCode();
  await page.goto(srv.base + '/#launch=' + code);
  await page.waitForSelector('html[data-ready="1"]', { timeout: 30000 });
  return { ctx, page, sink };
}
async function txt(p, sel) { const el = await p.$(sel); return el ? ((await el.textContent()) || '') : ''; }
async function screen(p, id, timeout) {
  await p.waitForFunction((x) => {
    const r = window.MA && window.MA.app && window.MA.app.current();
    return r && r.id === x && !document.querySelector('#screen-root > .spinner');
  }, id, { timeout: timeout || 20000 });
  await p.waitForTimeout(200);
}
async function go(p, hash, id) {
  await p.evaluate((h) => { window.location.hash = h; }, hash);
  await screen(p, id);
}
async function waitToast(p, re, timeout) {
  await p.waitForFunction((s) => new RegExp(s).test(((document.getElementById('ma-toasts') || {}).textContent || '') +
    ((document.getElementById('ma-alerts') || {}).textContent || '')), re.source, { timeout: timeout || 20000 });
}
async function closeToasts(p) { await p.evaluate(() => document.querySelectorAll('#ma-toasts .toast .toast-close').forEach((b) => b.click())); }
/** A felület saját API-burkán át (token, fejlécek, hibaboríték) — mint a képernyők. */
async function api(p, method, apiPath, body, headers) {
  return p.evaluate(([m, u, b, hd]) => {
    const url = new URL(u, 'http://x');
    const query = {};
    url.searchParams.forEach((v, k) => { query[k] = v; });
    const opts = { query, toast: false };
    if (b !== null) { opts.body = b; }
    if (hd && hd.ifMatch) { opts.ifMatch = hd.ifMatch; }
    return window.MA.api.request(m, url.pathname, opts).then((env) => ({ ok: true, data: env.data, etag: env.etag, warnings: env.warnings }),
      (e) => ({ ok: false, code: e.code, http: e.http, message: e.message, details: e.details }));
  }, [method, apiPath, body === undefined ? null : body, headers || null]);
}
async function jobDone(p, job) {
  for (let i = 0; i < 600 && job && (job.status === 'queued' || job.status === 'running'); i++) {
    await p.waitForTimeout(200);
    const r = await api(p, 'GET', '/api/jobs/' + job.job_id);
    job = r.ok ? r.data : job;
  }
  return job;
}
function readJson(srv, rel) { return JSON.parse(fs.readFileSync(path.join(srv.proj, rel), 'utf-8')); }
function exists(srv, rel) { return fs.existsSync(path.join(srv.proj, rel)); }
function activityText(srv) { const p = path.join(srv.proj, '07_ellenorzes', 'activity.jsonl'); return fs.existsSync(p) ? fs.readFileSync(p, 'utf-8') : ''; }
async function finishPage(o, label) {
  check(o.sink.errors.length === 0, label + ': nincs konzolhiba / CSP-sértés / párbeszédablak: ' + o.sink.errors.join(' || '));
  check(o.sink.http5xx.length === 0, label + ': nincs 5xx válasz: ' + o.sink.http5xx.join(', '));
  const rep = await o.page.evaluate(() => {
    const ls = [];
    for (let i = 0; i < localStorage.length; i++) { ls.push(localStorage.key(i)); }
    return { ls, missing: window.MA.i18n.missing() };
  });
  check(rep.ls.every((k) => k.startsWith('mag.pref.')), label + ': localStorage csak mag.pref.* (' + rep.ls.join(',') + ')');
  check(rep.missing.length === 0, label + ': nincs hiányzó i18n-kulcs (' + rep.missing.join(', ') + ')');
  await o.ctx.close();
}

// ---------------------------------------------------------------- értékelő-segédek
async function setRater(p, r) {
  const inp = await p.waitForSelector('.ap-rater-input', { timeout: 10000 });
  await inp.fill(r);
  await inp.press('Enter');
  await p.waitForTimeout(300);
}
async function formReady(p) {
  await p.waitForSelector('#ap-form-panel #ap-comp-text', { timeout: 20000 });
  await p.waitForFunction(() => { const b = document.querySelector('#ap-form-panel .ap-panel-body'); return b && b.getAttribute('aria-busy') !== 'true'; }, null, { timeout: 20000 });
}
/** A választerv bekattintása a felületen (rádiógombok); a teljesség-szöveg a motor élő ellenőrzéséből. */
async function fillAnswers(p, answers) {
  const missing = await p.evaluate((ans) => {
    const miss = [];
    Object.keys(ans).forEach((k) => {
      // „tétel#rész”: résztételes tétel (AMSTAR 2 9./11. — RCT / NRSI külön választó, F3)
      const kp = k.split('#');
      const inp = kp.length === 2
        ? document.querySelector('.ap-slot[data-key="' + kp[0] + '"] .ap-part[data-part="' + kp[1] + '"] input[type="radio"][value="' + ans[k] + '"]')
        : document.querySelector('.ap-slot[data-key="' + k + '"] input[type="radio"][value="' + ans[k] + '"]');
      if (!inp) { miss.push(k); return; }
      if (!inp.checked) { inp.click(); }
    });
    return miss;
  }, answers);
  if (missing.length) { throw new Error('nincs ilyen válasz-hely a felületen: ' + missing.join(', ')); }
}
async function waitComp(p, text) {
  await p.waitForFunction((x) => (document.getElementById('ap-comp-text') || {}).textContent === x, text, { timeout: 20000 });
}
async function setOverall(p, value, rationale) {
  await p.selectOption('#ap-overall .ap-overall-select', value);
  if (rationale) {
    await p.fill('#ap-overall textarea.ap-rationale', rationale);
    await p.dispatchEvent('#ap-overall textarea.ap-rationale', 'change');
  }
}
async function saveComplete(p) {
  await closeToasts(p);
  await p.click('#ap-save-complete');
  await p.waitForFunction(() => /mentve|Mentve/.test((document.getElementById('ma-toasts') || {}).textContent || '') ||
    !!(document.getElementById('ap-form-msg') || {}).textContent, null, { timeout: 20000 });
  return txt(p, '#ap-form-msg');
}
async function impliedOverall(p) {
  return p.evaluate(() => { const v = document.querySelector('#ap-overall .ap-implied .ap-v'); return v ? v.dataset.v : null; });
}
/** A validator-doboz: futtatás, majd az „Összevetés a motorral” sorai. */
async function crossCheck(p) {
  await p.evaluate(() => { const d = document.getElementById('ap-val'); if (d) { d.open = true; } });
  await p.click('#ap-val-run');
  await p.waitForSelector('#ap-val .adp-val-result, #ap-val .adp-val-down', { timeout: 60000 });
  return p.evaluate(() => {
    const down = document.querySelector('#ap-val .adp-val-down');
    const rows = Array.from(document.querySelectorAll('#ap-val .adp-val-cmp li')).map((li) => ({ k: li.dataset.k, agree: li.dataset.agree === '1',
      text: li.textContent, why: (li.querySelector('.adp-val-why') || {}).textContent || '' }));
    const guards = Array.from(document.querySelectorAll('#ap-val .adp-guards li')).map((li) => li.dataset.guard);
    const mode = (document.querySelector('#ap-val li[data-k="mode"]') || {}).textContent || '';
    return { down: down ? down.textContent : null, rows, guards, mode, text: (document.getElementById('ap-val') || {}).textContent || '' };
  });
}
function compText(eng) { const c = eng.completeness_text; return c && typeof c === 'object' ? c.hu : c; }
let SHOT = 0;
async function shot(p, name) {
  const dir = process.env.E2E_V1_SHOTS;
  if (!dir || !p) { return; }
  fs.mkdirSync(dir, { recursive: true });
  await p.screenshot({ path: path.join(dir, String(++SHOT).padStart(3, '0') + '_' + name.replace(/[^A-Za-z0-9_-]+/g, '_').slice(0, 40) + '.png'), fullPage: true }).catch(() => null);
}
/** A doboz „egyezik, vagy megmagyarázza” követelménye: minden sor egyezik, vagy van ismert oka. */
function cmpOk(cc) {
  return cc.rows.length > 0 && cc.rows.every((r) => r.agree || (r.why && !/oka nem ismert/.test(r.why)));
}

// ================================================================ 1. értékelések (main szerver)
let MAIN = null;
const S = {};          // a részek közös állapota

async function ensureMain() {
  if (MAIN) { return MAIN; }
  const tmp = mkTmp('ma_e2e_v1_main_');
  MAIN = await startServer(tmp, 'main');
  MAIN.tmp = tmp;
  console.log('  main: ' + MAIN.proj + ' (pluginok: ' + (MAIN.plugins || '—') + '; figure-forge python: ' + (MAIN.ff_python || '—') + ')');
  return MAIN;
}
// a valódi plugin megvan-e (MA_GUI_PLUGIN_DIRS több mappát is felsorolhat)
const realPlugin = (name) => !!(MAIN && MAIN.plugins) && MAIN.plugins.split(path.delimiter).some((d) => d && fs.existsSync(path.join(d, name)));
const validatorReal = () => realPlugin('validator');

async function appraisalRob2() {
  const srv = await ensureMain();
  await test('1a. RoB 2 két emberi értékelővel (felületen kitöltve) — a teljesség és az implikált ítélet a motoré; validator-keresztellenőrzés', async () => {
    const o = await openApp(srv);
    const p = o.page;
    await go(p, '#/appraisal?tool=rob2&target=o1', 'appraisal');
    await setRater(p, 'SzK');
    await p.waitForSelector('#ap-matrix-table tbody tr');
    check((await p.$$('#ap-matrix-table tbody tr')).length === 13, 'a mátrixban a 13 vizsgálat (studies.json)');
    const runs = [['S01', 'SzK', 'low'], ['S01', 'KP', 'high'], ['S02', 'SzK', 'low'], ['S02', 'KP', 'low']];
    for (const [unit, rater, variant] of runs) {
      await go(p, '#/appraisal?tool=rob2&target=o1&unit=' + unit, 'appraisal');
      await setRater(p, rater);
      await go(p, '#/appraisal?tool=rob2&target=o1&unit=' + unit, 'appraisal');
      await formReady(p);
      const plan = py(['plan', 'rob2', 'assignment', variant]);
      await fillAnswers(p, plan.answers);
      await waitComp(p, plan.n + '/' + plan.n);
      const imp = await impliedOverall(p);
      await setOverall(p, imp, 'Egyezik a motor implikált ítéletével (' + rater + ').');
      const msg = await saveComplete(p);
      check(msg === '', unit + '/' + rater + ': mentés (' + msg + ')');
      const rel = '04_torzitas_kockazat/appraisals/' + unit + '.rob2.o1.' + rater + '.json';
      check(exists(srv, rel), unit + '/' + rater + ': a fájl a lemezen (' + rel + ')');
      const eng = py(['check', srv.proj, rel]);
      check(eng.complete === true && (await txt(p, '#ap-comp-text')) === compText(eng), unit + '/' + rater + ': a felület teljesség-szövege = a motor ellenőrzése a mentett fájlon (' + compText(eng) + ')');
      check(imp === eng.overall.implied && eng.doc.overall.judgement === imp && eng.doc.status === 'complete',
        unit + '/' + rater + ': implikált összítélet ' + imp + ' (a motoré: ' + eng.overall.implied + '), az ember ítélete rögzítve, státusz: complete');
      check((await txt(p, '#ap-overall .ap-implied')).indexOf('NEM') >= 0 || (await txt(p, '#ap-overall .ap-implied')).indexOf('Konzervatív') >= 0,
        unit + '/' + rater + ': az implikált ítélet az algoritmus címkéjével (nem hivatalos)');
      S['rob2_' + unit + '_' + rater] = eng;
      if (unit === 'S01' && rater === 'KP') {
        const cc = await crossCheck(p);
        if (validatorReal()) {
          check(!cc.down && /1\.0/.test(cc.mode) && /bridge|régi/i.test(cc.mode), 'validator: a valódi plugin bridge-módban (' + cc.mode.trim() + ')');
          check(cmpOk(cc), 'RoB 2: a keresztellenőrző doboz egyezik a motorral, vagy megmagyarázza az eltérést: ' + JSON.stringify(cc.rows));
          check(cc.rows.find((r) => r.k === 'completeness' && r.agree), 'RoB 2: a teljesség egyezik (validator = motor)');
        } else {
          check(!!cc.down, 'validator nélkül: érthető üzenet a dobozban (' + (cc.down || '').slice(0, 80) + ')');
        }
      }
    }
    check(activityText(srv).indexOf('Egyezik a motor implikált') < 0, 'az activity-naplóban nincs értékelés-szöveg');
    await finishPage(o, 'RoB 2');
  });

  await test('1b. AI-vázlat a beérkezett mappából: jelvény, kezdőbarát indoklás, jóváhagyás kell (a motor tartalmi ellenőrzése), kimarad a κ-ból', async () => {
    const ok = py(['aidraft', srv.proj, 'rob2', 'S01', 'o1', 'assignment']);
    const o = await openApp(srv, { rater: 'SzK' });
    const p = o.page;
    await go(p, '#/appraisal?tool=rob2&target=o1', 'appraisal');
    await closeToasts(p);
    await p.click('#ap-inbox-btn');
    await p.waitForSelector('#ap-inbox li.item');
    const items = await p.$$eval('#ap-inbox li.item', (ls) => ls.map((l) => l.dataset.path));
    check(items.indexOf(ok.path) >= 0, 'a beérkezett mappában az AI-vázlat (' + ok.path + ')');
    await p.click('#ap-inbox li.item[data-path="' + ok.path + '"] .ap-inbox-import');
    await waitToast(p, /importálva|Importálva|átvéve|Átvéve/);
    await go(p, '#/appraisal?tool=rob2&target=o1&unit=S01&rater=ai', 'appraisal');
    await formReady(p);
    await p.waitForSelector('#ap-ai-banner');
    check((await txt(p, '#ap-ai-banner')).indexOf('AI') >= 0 && !!(await p.$('#ap-approve')), 'AI-vázlat jelvény és „Jóváhagyás” gomb (még nincs jóváhagyva)');
    check((await p.$$('.ap-slot .ap-ai-mark')).length >= 20, 'tételenként AI-jelölés (indoklás van)');
    // kezdőbarát „Miért?”: a tétel egyszerű nyelvű indoklása (asks / because / change) és az idézet
    await p.click('.ap-item[data-id="1.1"] .why-btn');
    await p.waitForSelector('.why-pop');
    const why = await txt(p, '.why-pop');
    check(why.indexOf('Mit kérdez a 1.1 tétel') >= 0 && why.indexOf('Ha a közlemény mást írna') >= 0 && why.indexOf('teszt-idézet') >= 0,
      '„Miért?”: az AI egyszerű nyelvű indoklása és a szó szerinti idézet (' + why.replace(/\s+/g, ' ').slice(0, 120) + ')');
    await p.keyboard.press('Escape');
    // a konszenzus-nézetben az AI-vázlat NEM értékelő
    await go(p, '#/appraisal-consensus?tool=rob2&target=o1&unit=S01', 'appraisal-consensus');
    await p.waitForSelector('#cs-kappa');
    check((await txt(p, '#cs-excluded')).indexOf('AI') >= 0, 'a konszenzus-nézet kiírja: az AI-vázlat kimaradt a κ-ból');
    const cons = await api(p, 'GET', '/api/appraisals/consensus/S01/rob2?target=o1');
    check(cons.ok && cons.data.human_raters.join(',') === 'KP,SzK' && cons.data.excluded.length === 1 && cons.data.excluded[0].reason === 'ai_draft',
      'a κ két emberi értékelőből (KP, SzK); az AI-vázlat kizárva');
    // jóváhagyás a felületen: a jóváhagyó ember, megerősítéssel
    await go(p, '#/appraisal?tool=rob2&target=o1&unit=S01&rater=ai', 'appraisal');
    await formReady(p);
    await p.click('#ap-approve');
    await p.waitForSelector('#ap-approve-dialog');
    await p.click('.modal-actions .btn-primary');
    check((await txt(p, '#ap-approve-dialog')).length > 0 && !(await p.$('#ap-ai-banner.is-approved')), 'megerősítés nélkül nincs jóváhagyás');
    await p.check('#ap-approve-dialog input[type="checkbox"]');
    await p.click('.modal-actions .btn-primary');
    await p.waitForSelector('#ap-ai-banner.is-approved', { timeout: 15000 });
    const f = readJson(srv, '04_torzitas_kockazat/appraisals/S01.rob2.o1.ai.json');
    check(f.origin === 'ai_draft' && f.approved_by === 'SzK', 'jóváhagyva: approved_by = SzK, az eredet AI-vázlat marad');
    const cons2 = await api(p, 'GET', '/api/appraisals/consensus/S01/rob2?target=o1');
    check(cons2.ok && cons2.data.excluded.length === 1, 'jóváhagyás után sem értékelő (a κ-ból kimarad)');
    // hiányos AI-vázlat (egy tételnél nincs indoklás) → a motor tartalmi ellenőrzése (6. döntés) már az átvételt sem
    // engedi; és a felületen sem menthető AI-vázlat tételindoklás nélkül
    const bad = py(['aidraft', srv.proj, 'rob2', 'S02', 'o1', 'assignment']);
    const doc = JSON.parse(fs.readFileSync(path.join(srv.proj, bad.path), 'utf-8'));
    delete doc.answers['1.2'].rationale;
    fs.writeFileSync(path.join(srv.proj, bad.path), JSON.stringify(doc));
    await go(p, '#/appraisal?tool=rob2&target=o1', 'appraisal');
    await closeToasts(p);
    await p.click('#ap-inbox-btn');
    await p.waitForSelector('#ap-inbox li.item[data-path="' + bad.path + '"]');
    await p.click('#ap-inbox li.item[data-path="' + bad.path + '"] .ap-inbox-import');
    await waitToast(p, /import nem sikerült/);
    const terr = (await txt(p, '#ma-toasts')) + (await txt(p, '#ma-alerts'));
    check(/answers\[1\.2\]\.rationale/.test(terr) && /indoklás/.test(terr), 'hiányos AI-vázlat: az átvételt a motor üzenete utasítja el (1.2: indoklás) (' + terr.replace(/\s+/g, ' ').slice(0, 120) + ')');
    check(!exists(srv, '04_torzitas_kockazat/appraisals/S02.rob2.o1.ai.json'), 'a hiányos AI-vázlat nem került a projektbe');
    await p.click('.modal-actions .btn-primary');
    await closeToasts(p);
    await go(p, '#/appraisal?tool=rob2&target=o1&unit=S02&rater=ai', 'appraisal');
    await formReady(p);
    await fillAnswers(p, { '1.1': 'yes' });
    await p.click('#ap-save');
    await p.waitForFunction(() => /rationale/.test((document.getElementById('ap-form-msg') || {}).textContent || ''), null, { timeout: 15000 });
    check(!exists(srv, '04_torzitas_kockazat/appraisals/S02.rob2.o1.ai.json'), 'a felületen kézzel „AI-vázlatként” kitöltött, indoklás nélküli értékelés nem menthető (422, a motor üzenete)');
    await finishPage(o, 'AI-vázlat');
  });

  await test('1c. κ és konszenzus a felületen (a motor κ-szövege), forgalmi lámpa, rob-oszlop szinkron', async () => {
    const o = await openApp(srv, { rater: 'SzK' });
    const p = o.page;
    await go(p, '#/appraisal-consensus?tool=rob2&target=o1&unit=S01', 'appraisal-consensus');
    await p.waitForSelector('#cs-table');
    const cons = await api(p, 'GET', '/api/appraisals/consensus/S01/rob2?target=o1');
    check((await txt(p, '#cs-kappa')) === cons.data.agreement.kappa_text.hu, 'κ a motor szövegével (' + (await txt(p, '#cs-kappa')) + ')');
    const diffs = await p.$$eval('#cs-table tr.is-diff', (rs) => rs.map((r) => r.dataset.key));
    check(diffs.length === cons.data.agreement.disagree + diffs.filter((k) => /^(dom:|overall)/.test(k)).length, 'az eltérő sorok száma = a motor disagree-je (+ ítélet-sorok): ' + diffs.join(','));
    await p.click('#cs-save');
    check((await txt(p, '#cs-msg')).length > 0 && !exists(srv, '04_torzitas_kockazat/appraisals/S01.rob2.o1.consensus.json'), 'döntés nélkül a konszenzus nem menthető');
    const sideKP = cons.data.a.rater === 'KP' ? 'a' : 'b';
    for (const k of diffs) {
      await p.check('#cs-table tr[data-key="' + k + '"] input[data-side="' + sideKP + '"]');
      const r = await p.$('#cs-table tr[data-key="' + k + '"] .cs-reason');
      await r.fill('A KP olvasata pontosabb (' + k + ').');
      await r.dispatchEvent('change');
    }
    await p.click('#cs-save');
    await waitToast(p, /onszenzus/);
    const c = readJson(srv, '04_torzitas_kockazat/appraisals/S01.rob2.o1.consensus.json');
    check(c.status === 'consensus' && [c.assessor, c.second_assessor].sort().join(',') === 'KP,SzK' && c.overall.judgement === 'high',
      'konszenzus-fájl: status consensus, két emberi értékelő, összítélet a KP-é (high)');
    // a többi vizsgálat (S02–S13): a második értékelő csomagja a beérkezett mappából (lezárt értékelések)
    const units = ['S03', 'S04', 'S05', 'S06', 'S07', 'S08', 'S09', 'S10', 'S11', 'S12', 'S13'];
    const b1 = py(['bundle', srv.proj, 'rob2', 'o1', units.slice(0, 9).join(','), 'low']);
    const b2 = py(['bundle', srv.proj, 'rob2', 'o1', units.slice(9).join(','), 'high']);
    await go(p, '#/appraisal?tool=rob2&target=o1', 'appraisal');
    for (const b of [b1, b2]) {
      await closeToasts(p);
      await p.click('#ap-inbox-btn');
      await p.waitForSelector('#ap-inbox li.item[data-path="' + b.path + '"]');
      await p.click('#ap-inbox li.item[data-path="' + b.path + '"] .ap-inbox-import');
      await waitToast(p, /importálva|Importálva|átvéve|Átvéve/);
      await closeToasts(p);
      await p.waitForTimeout(300);
    }
    // forgalmi lámpa
    await go(p, '#/appraisal-summary?tool=rob2&outcome=o1', 'appraisal-summary');
    await p.waitForSelector('#rt-matrix');
    const summ = await api(p, 'GET', '/api/appraisals/rob-summary?tool=rob2&outcome=o1');
    const rows = await p.$$eval('#rt-matrix g.rt-row', (gs) => gs.map((g) => ({ id: g.dataset.study, overall: (g.querySelectorAll('g.rt-mark')[g.querySelectorAll('g.rt-mark').length - 1] || {}).dataset })));
    check(rows.length === 13 && summ.ok && summ.data.studies.length === 13, 'forgalmi lámpa: 13 vizsgálat (' + rows.length + ')');
    const byId = {};
    summ.data.studies.forEach((s) => { byId[s.study_id] = s; });
    check(rows.every((r) => byId[r.id] && (r.overall || {}).v === (byId[r.id].overall || '')), 'minden sor összítélete = a motor rob-summary-je');
    check(byId.S01.overall === 'high' && byId.S12.overall === 'high' && byId.S02.overall === 'low', 'S01 (konszenzus) és S12 magas, S02 alacsony');
    // rob-oszlop szinkron: előnézet → alkalmazás megerősítéssel
    await p.click('#sync-preview');
    await p.waitForSelector('#sync-table, #sync-none');
    const nCh = (await p.$$('#sync-table tbody tr')).length;
    check(nCh === 13, 'előnézet: 13 cella változik (a táblában még nincs rob oszlop): ' + nCh);
    await p.click('#sync-apply');
    await p.waitForSelector('.modal-backdrop');
    await p.click('.modal-backdrop .btn-primary, .modal-backdrop .btn-danger');
    await p.waitForSelector('#sync-applied', { timeout: 15000 });
    const csv = fs.readFileSync(path.join(srv.proj, '03_adatok', 'o1.csv'), 'utf-8');
    check(/;rob/.test(csv.split('\n')[0]), 'az o1.csv-ben rob oszlop');
    const prov = readJson(srv, '03_adatok/o1.prov.json');
    const robCells = (prov.cells || []).filter((c) => c.field === 'rob');
    check(robCells.length === 13 && robCells.every((c) => c.method === 'calculated'), 'eredet: a 13 rob-cella „calculated” (forrás: az értékelés)');
    await finishPage(o, 'konszenzus/szinkron');
  });
}

async function appraisalOthers() {
  const srv = await ensureMain();
  const tools = [
    // [eszköz, képernyő-hash, egység, cél, hatókör, változat, plusz]
    ['robins-i', '#/appraisal?tool=robins-i&target=x1&unit=S04', 'appraisal', 'S04', 'x1', 'assignment', 'low'],
    ['robins-e', '#/appraisal?tool=robins-e&target=x1&unit=S05', 'appraisal', 'S05', 'x1', 'all', 'low'],
    ['quadas2', '#/appraisal?tool=quadas2&target=idx1&unit=S06', 'appraisal', 'S06', 'idx1', 'all', 'low'],
    ['nos', '#/appraisal?tool=nos&unit=S07', 'appraisal', 'S07', null, 'cohort', 'low'],
    ['tripod-ai', '#/appraisal-tripod?unit=S09', 'appraisal-tripod', 'S09', null, 'both', 'mixed'],
    ['amstar2', '#/appraisal-amstar2', 'appraisal-amstar2', 'review', null, 'all', 'mixed']
  ];
  await test('1d. ROBINS-I, ROBINS-E, QUADAS-2, NOS, TRIPOD+AI, AMSTAR 2 — önállóan (motor) és a validator-keresztellenőrzéssel', async () => {
    const o = await openApp(srv, { rater: 'SzK' });
    const p = o.page;
    for (const [tool, hash, scr, unit, target, scope, variant] of tools) {
      await go(p, hash, scr);
      await formReady(p);
      const plan = py(['plan', tool, scope, variant]);
      if (tool === 'nos' || tool === 'robins-i') {
        const cur = await p.evaluate(() => (document.querySelector('.ap-scope .seg-btn[aria-pressed="true"]') || {}).dataset);
        check(!cur || cur.scope === scope, tool + ': alapértelmezett hatókör ' + scope);
      }
      await fillAnswers(p, plan.answers);
      await waitComp(p, plan.n + '/' + plan.n);
      const imp = await impliedOverall(p);
      if (await p.$('#ap-overall .ap-overall-select')) {
        const vals = await p.$$eval('#ap-overall .ap-overall-select option', (os) => os.map((x) => x.value).filter(Boolean));
        await setOverall(p, imp || vals[0], 'Holisztikus/összítélet (teszt).');
      }
      const msg = await saveComplete(p);
      check(msg === '', tool + ': mentés lezártként (' + msg + ')');
      const rel = '04_torzitas_kockazat/appraisals/' + unit + '.' + tool + (target ? '.' + target : '') + '.SzK.json';
      check(exists(srv, rel), tool + ': a fájl a lemezen (' + rel + ')');
      const eng = py(['check', srv.proj, rel]);
      check(eng.complete && (await txt(p, '#ap-comp-text')) === compText(eng), tool + ': teljesség a motorból (' + compText(eng) + ')');
      if (tool === 'amstar2') {
        await p.waitForSelector('#am-class');
        const cls = await txt(p, '#am-class');
        check(eng.amstar2.differs === true && cls.indexOf(eng.amstar2.rating_text.hu) >= 0 && cls.indexOf(eng.amstar2.alternative.rating_text.hu) >= 0 &&
          !!(await p.$('#am-alt.is-diff')), 'AMSTAR 2: besorolás mindkét konvencióval (meets: ' + eng.amstar2.rating + ', weakness: ' + eng.amstar2.alternative.rating + ')');
      }
      if (tool === 'tripod-ai') {
        check(eng.answered === 52, 'TRIPOD+AI: 52 altétel');
        await go(p, '#/appraisal-tripod', 'appraisal-tripod');
        await p.waitForSelector('#tr-heat');
        const cols = (await p.$$('#tr-heat thead th.tr-col')).length;
        await p.click('#tr-filter-D');
        await p.waitForSelector('#tr-filter-D[aria-pressed="true"]');
        const colsD = (await p.$$('#tr-heat thead th.tr-col')).length;
        await p.click('#tr-filter-E');
        await p.waitForSelector('#tr-filter-E[aria-pressed="true"]');
        const colsE = (await p.$$('#tr-heat thead th.tr-col')).length;
        check(cols === 52 && colsD < 52 && colsE < 52 && colsD + colsE > 52, 'TRIPOD+AI hőtérkép: 52 oszlop; D-szűrő ' + colsD + ', E-szűrő ' + colsE + ' (a D;E tételek mindkettőben)');
        const cells = await p.$$eval('#tr-heat tr[data-unit="S09"] td.tr-cell', (cs) => cs.map((c) => c.dataset.v));
        check(cells.length === colsE && cells.every((v) => ['present', 'partial', 'missing'].indexOf(v) >= 0), 'a hőtérkép cellái a mentett státuszok');
        await go(p, hash, scr);
        await formReady(p);
      }
      const cc = await crossCheck(p);
      if (validatorReal()) {
        check(cmpOk(cc), tool + ': a validator-doboz egyezik a motorral, vagy megmagyarázza az eltérést: ' + JSON.stringify(cc.rows.map((r) => [r.k, r.agree, r.why.slice(0, 50)])));
        if (tool === 'tripod-ai') { check(cc.guards.indexOf('H1') >= 0 && cc.rows.find((r) => r.k === 'completeness' && r.agree), 'TRIPOD+AI: H1-őr — a munkapad számol, és egyezik a motorral'); }
        if (tool === 'amstar2') { check(cc.guards.indexOf('H4') >= 0 && cc.rows.find((r) => r.k === 'amstar2' && r.agree), 'AMSTAR 2: H4-őr — a validator „weakness” besorolása = a motoré ugyanazzal a konvencióval'); }
        if (tool === 'quadas2') { check(cc.guards.indexOf('H12') >= 0 && cc.rows.find((r) => r.k === 'overall' && !r.agree && /H12/.test(r.why)), 'QUADAS-2: H12 — a validator polaritás-hibája megmagyarázza az eltérő ítéletet'); }
        if (tool === 'robins-i') { check(cc.guards.indexOf('H13') >= 0 && cc.rows.find((r) => r.k === 'completeness' && !r.agree && /H13/.test(r.why)), 'ROBINS-I: H13 — az eltérő számozás megmagyarázza a teljesség-eltérést'); }
      } else {
        check(!!cc.down, tool + ': validator nélkül érthető üzenet');
      }
      S['other_' + tool] = eng;
    }
    await finishPage(o, 'további eszközök');
  });

  await test('1e. PROBAST+AI: 34 hely, menetenként helyes teljesség (16 + 18), holisztikus összítélet; validator H2', async () => {
    const o = await openApp(srv, { rater: 'SzK' });
    const p = o.page;
    await go(p, '#/appraisal-probast?unit=S08&target=M1', 'appraisal-probast');
    await formReady(p);
    check((await txt(p, '#pb-holistic')).length > 0, 'a holisztikus összítélet szövege kiírva');
    const plan = py(['plan', 'probast-ai', 'both', 'low']);
    check(plan.n === 34, 'a motor definíciója: 34 hely');
    const dev = {}, ev = {};
    Object.keys(plan.answers).forEach((k) => { (k.indexOf('development/') === 0 ? dev : ev)[k] = plan.answers[k]; });
    await fillAnswers(p, dev);
    await waitComp(p, '16/34');
    const pass = async () => p.$$eval('.ap-comp-pass', (ss) => ss.map((s) => s.dataset.pass + ':' + s.querySelector('.num').textContent));
    check((await pass()).join(',') === 'development:16/16,evaluation:0/18', 'csak a fejlesztési menet: 16/16 és 0/18 (' + (await pass()).join(',') + ')');
    if (validatorReal()) {
      const cc = await crossCheck(p);
      check(cc.guards.indexOf('H2') >= 0 && cmpOk(cc) && cc.rows.find((r) => r.k === 'completeness' && r.agree),
        'PROBAST+AI: H2 — a validator 32/34-et mondana, a munkapad 16/34-et számol (= a motor): ' + JSON.stringify(cc.rows.map((r) => [r.k, r.agree])));
      check(/32\/34/.test(cc.text), 'a validator saját (nem megbízható) számlálása is látszik (32/34)');
    }
    await fillAnswers(p, ev);
    await waitComp(p, '34/34');
    check((await pass()).join(',') === 'development:16/16,evaluation:18/18', 'mindkét menet: 16/16 és 18/18');
    const sel = await p.$$eval('#ap-overall .ap-overall-select option', (os) => os.map((x) => x.value).filter(Boolean));
    await p.selectOption('#ap-overall .ap-overall-select', sel[0]);
    check((await p.getAttribute('#ap-overall textarea.ap-rationale', 'aria-invalid')) === 'true', 'holisztikus összítélet: indoklás nélkül jelölt (kötelező)');
    await setOverall(p, sel[0], 'Holisztikus ítélet: minden domén alacsony kockázatú (teszt).');
    // F4: menetenkénti összítélet — a fejlesztési menet (minőség) és az értékelési menet (torzítási kockázat) külön
    const blocks = await p.$$eval('#ap-overall .ap-overall-block', (bs) => bs.map((b) => b.dataset.pass));
    check(blocks.join(',') === 'development,evaluation', 'F4: két menetenkénti összítélet (' + blocks.join(',') + ')');
    await p.selectOption('#ap-overall .ap-overall-block[data-pass="evaluation"] .ap-overall-select', sel[0]);
    await p.fill('#ap-overall .ap-overall-block[data-pass="evaluation"] textarea.ap-rationale', 'Értékelési menet: alacsony kockázat (teszt).');
    await p.dispatchEvent('#ap-overall .ap-overall-block[data-pass="evaluation"] textarea.ap-rationale', 'change');
    const msg = await saveComplete(p);
    check(msg === '', 'mentés (' + msg + ')');
    const eng = py(['check', srv.proj, '04_torzitas_kockazat/appraisals/S08.probast-ai.M1.SzK.json']);
    check(eng.per_pass.development.text === '16/16' && eng.per_pass.evaluation.text === '18/18' && eng.answered === 34, 'a motor a mentett fájlon: 16/16 + 18/18 = 34');
    const ops = (eng.doc.overall_passes || []).map((x) => x.pass + ':' + x.judgement).sort();
    check(ops.join(',') === 'development:' + sel[0] + ',evaluation:' + sel[0], 'F4: a fájlban overall_passes mindkét menetre (' + ops.join(',') + ')');
    await finishPage(o, 'PROBAST+AI');
  });
}

// ================================================================ elemzés + érzékenység (1f), kumulatív és buborék (4)
/** Elemzési terv a felületen: opciók → automatikus explore → rögzítés (PUT spec + commit). */
async function planCommit(p, sink, outcome, extra, label) {
  await go(p, '#/analysis?outcome=' + outcome, 'analysis');
  await p.waitForSelector('#plan-form');
  await p.waitForFunction(() => { const e = document.getElementById('plan-status'); return !!e && /kész/.test(e.textContent || '') && /^\d+$/.test(e.getAttribute('data-seq') || ''); }, null, { timeout: 60000 });
  for (const [k, v] of Object.entries(extra || {})) {
    if (await p.$('select#opt-' + k)) { await p.selectOption('#opt-' + k, v); } else { await p.fill('#opt-' + k, v); }
  }
  if (Object.keys(extra || {}).length) {
    await p.waitForFunction(([vals]) => {
      const st = (document.getElementById('plan-status') || {}).textContent || '';
      const cmd = (document.getElementById('plan-cmd-expanded') || {}).textContent || '';
      return /kész/.test(st) && vals.every((v) => cmd.indexOf(v) >= 0);
    }, [Object.values(extra)], { timeout: 60000 });
  }
  const summary = await txt(p, '#plan-summary');
  await closeToasts(p);
  await p.click('#plan-commit');
  await p.waitForFunction(() => /Rögzítve \(commit\)|a rögzítés nem sikerült/.test((document.getElementById('plan-status') || {}).textContent || ''), null, { timeout: 120000 });
  check((await txt(p, '#plan-status')).indexOf('Rögzítve (commit)') >= 0, label + ': sikeres rögzítés (' + (await txt(p, '#plan-status')) + ')');
  await closeToasts(p);
  const r = await api(p, 'GET', '/api/runs?outcome=' + outcome);
  const run = r.ok && r.data.runs.length ? r.data.runs[0] : null;
  check(!!run && run.mode === 'commit', label + ': commit-futás (' + (run ? run.run_id : '—') + ')');
  return { run, summary };
}
function auditCodes(a, outcome) {
  return (a.findings || []).filter((f) => !outcome || f.outcome === outcome).map((f) => f.code);
}
async function overviewCodes(p) {
  await go(p, '#/overview', 'overview');
  await p.waitForFunction(() => !!document.querySelector('li.ov-x, #ov-not-checked, .empty-state'), null, { timeout: 30000 });
  return p.$$eval('li.ov-x', (ls) => ls.map((l) => ({ code: l.dataset.code, stage: (l.querySelector('.item-stage') || {}).textContent || '' })));
}

async function analysisSection() {
  const srv = await ensureMain();
  await test('1f. elemzés (o1: kumulatív év szerint, moderátor: szélesség) → commit → „magas RoB nélkül” gyermek-futás → X003 és X006 tiszta', async () => {
    const o = await openApp(srv, { rater: 'SzK' });
    const p = o.page;
    const before = py(['audit', srv.proj]);
    const res = await planCommit(p, o.sink, 'o1', { cumulative: 'év', moderators: 'szélesség' }, 'o1');
    S.o1run = res.run;
    const mid = py(['audit', srv.proj]);
    check(auditCodes(mid, 'o1').indexOf('X006') >= 0, 'a magas RoB-ú sorok miatt X006 jelez (gyermek-futás még nincs): ' + auditCodes(mid, 'o1').join(','));
    check(auditCodes(mid, 'o1').indexOf('X003') < 0, 'X003 tiszta: a tábla rob oszlopa = a végső értékelések (szinkron után)');
    // „magas RoB nélkül” gyermek-futás egy kattintással
    await p.waitForSelector('#child-no_rob_high:not([disabled])', { timeout: 30000 });
    await closeToasts(p);
    await p.click('#child-no_rob_high');
    await waitToast(p, /Gyermek-futás rögzítve/, 120000);
    const after = py(['audit', srv.proj]);
    const codes = auditCodes(after, 'o1');
    check(codes.indexOf('X006') < 0 && codes.indexOf('X003') < 0, 'a gyermek-futás után X003 és X006 tiszta (o1: ' + (codes.join(',') || '—') + ')');
    const spec = readJson(srv, '05_elemzes/specs/o1_primary_no_rob_high.json');
    check(spec.purpose === 'sensitivity' && spec.parent === 'o1_primary' && spec.filters.exclude.indexOf('rob=high') >= 0, 'a gyermek-spec: sensitivity, szülő o1_primary, kizárás rob=high');
    // a felület áttekintése ugyanazt mutatja, mint a motor
    const ov = await overviewCodes(p);
    const gui = ov.map((x) => x.code).sort().join(',');
    const eng = (after.findings || []).map((f) => f.code).sort().join(',');
    check(gui === eng, 'Áttekintés: a teendő-lista = a motor project audit-ja (' + gui + ')');
    check(ov.filter((x) => /o1/.test(x.stage)).every((x) => ['X003', 'X006'].indexOf(x.code) < 0), 'az Áttekintésen az o1-nél nincs X003/X006');
    S.auditBefore = before;
    await finishPage(o, 'elemzés');
  });
}

async function plotsSection() {
  const srv = await ensureMain();
  await test('4. kumulatív forest és buborékábra a valódi motor geometriájával (o1 commit-futás)', async () => {
    const o = await openApp(srv);
    const p = o.page;
    let run = S.o1run;
    if (!run) { const r = await api(p, 'GET', '/api/runs?outcome=o1'); run = r.data.runs.find((x) => !x.parent) || r.data.runs[0]; }
    const pl = await api(p, 'GET', '/api/runs/' + run.run_id + '/plot');
    check(pl.ok && pl.data.cumulative && pl.data.bubble, 'a futás plot_data-jában van cumulative és bubble blokk');
    const plot = pl.data;
    await go(p, '#/results?outcome=o1&run=' + run.run_id + '&view=cumulative', 'results');
    await p.waitForSelector('#results-plot-panel .cu-svg', { timeout: 20000 });
    const ent = plot.cumulative.entries;
    const rows = await p.$$eval('#results-plot-panel .cu-row', (gs) => gs.map((g) => ({ y: g.getAttribute('data-y'), t: g.querySelector('.fp-effect').textContent, k: g.getAttribute('data-k') })));
    check(rows.length === ent.length && ent.length === 13, 'kumulatív: lépésenként egy sor (' + rows.length + ')');
    check(rows.every((r, i) => r.y === String(ent[i].estimate) && r.t === ent[i].display_text.hu && r.k === String(ent[i].k)), 'kumulatív: data-y = a motor becslése, a szöveg a motor display_text-je, k a motoré');
    check((await p.$$('#results-plot-panel .cu-row.is-final polygon.fp-diamond')).length === 1, 'a végső sor gyémánt');
    check((await p.getAttribute('#results-plot-panel .cu-final-line', 'data-est')) === String(ent[ent.length - 1].estimate), 'referencia-vonal: a végső becslés');
    if (plot.cumulative.order && plot.cumulative.order.text) { check((await txt(p, '#cumulative-order')) === plot.cumulative.order.text.hu, 'a rendezés leírása a motoré (év szerint)'); }
    await p.click('[role="tab"][data-tab="bubble"]');
    await p.waitForSelector('#results-plot-panel .bu-svg', { timeout: 20000 });
    const b = plot.bubble;
    check((await p.$$('#results-plot-panel .bu-point')).length === b.points.length && b.points.length === 13, 'buborék: vizsgálatonként egy (' + b.points.length + ')');
    const geo = await p.evaluate((bb) => {
      const L = window.MA.plots.bubble.layout({ bubble: bb });
      const out = bb.points.map((pt) => {
        const c = document.querySelector('#results-plot-panel .bu-point[data-uid="' + pt.row_uid + '"] .bu-bubble');
        return { dx: Math.abs(Number(c.getAttribute('cx')) - L.sx(pt.x)), dy: Math.abs(Number(c.getAttribute('cy')) - L.sy(pt.y)), r: Number(c.getAttribute('r')), w: pt.weight_pct };
      });
      const poly = document.querySelector('#results-plot-panel polygon.bu-band');
      const pts = poly ? poly.getAttribute('points').trim().split(/\s+/) : [];
      const first = pts.length ? pts[0].split(',').map(Number) : [NaN, NaN];
      return { out, n: pts.length, firstOk: !!bb.band && Math.abs(first[0] - L.sx(bb.band[0][0])) < 0.06 && Math.abs(first[1] - L.sy(bb.band[0][2])) < 0.06 };
    }, b);
    check(geo.out.every((g) => g.dx < 0.06 && g.dy < 0.06), 'a buborékok középpontja a motor x/y-ján (±0,06 px)');
    const ws = geo.out.filter((g) => g.w > 0).sort((x, y) => y.w - x.w);
    const ratio = (ws[0].r * ws[0].r) / (ws[ws.length - 1].r * ws[ws.length - 1].r);
    check(ws[ws.length - 1].r <= 3.01 || Math.abs(ratio - ws[0].w / ws[ws.length - 1].w) / (ws[0].w / ws[ws.length - 1].w) < 0.05, 'a buborék területe a motor súlyával arányos');
    check(geo.n === 2 * b.band.length && geo.firstOk, 'a konfidencia-sáv a motor rácspontjaiból (' + geo.n + ' pont)');
    check(!!(await p.$('#results-plot-panel path.bu-line')), 'illesztett meta-regressziós egyenes');
    check((await txt(p, '#bubble-coef')) === b.coef_text.hu, 'a koefficiens-szöveg a motoré (' + b.coef_text.hu + ')');
    if (b.pi_band && b.pi_band.length) { check((await p.$$('#results-plot-panel path.bu-pi')).length === 2, 'predikciós sáv a motor rácsán'); }
    await finishPage(o, 'ábrák');
  });
}

// ================================================================ 2. GRADE + SoF
/** egyszerű CSV-cellabontás (idézőjelek, kettőzött idézőjel) — a képletinjekció-őr ellenőrzéséhez */
function csvCells(text, sep) {
  const out = [];
  let cur = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"' && text[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') { q = false; } else { cur += ch; }
    } else if (ch === '"' && cur === '') { q = true; } else if (ch === sep || ch === '\n') { out.push(cur.replace(/\r$/, '')); cur = ''; } else { cur += ch; }
  }
  if (cur) { out.push(cur); }
  return out;
}
async function rate(p, dm, rating, rationale) {
  await p.selectOption('#gr-rating-' + dm, rating);
  if (rationale !== undefined) { await p.fill('#gr-rat-' + dm, rationale); }
}

async function gradeSection() {
  const srv = await ensureMain();
  await test('2a. GRADE (o1) a motor tanácsával; „gyanított” publikációs torzítás → X019 tilt, amíg ember nem dönt; az ember erősíti meg a bizonyosságot', async () => {
    const o = await openApp(srv);
    const p = o.page;
    await go(p, '#/grade?outcome=o1', 'grade');
    await p.waitForSelector('#gr-adv-inconsistency .gr-adv', { timeout: 30000 });
    const view = await api(p, 'GET', '/api/grade/o1');
    const adv = await api(p, 'GET', '/api/grade/o1/advice?run=' + view.data.run.run_id);
    check(adv.ok && view.ok && adv.data.advice.run_id === view.data.run.run_id, 'a tanács a kimenet legutóbbi elsődleges commit-futására szól (' + view.data.run.run_id + ')');
    for (const dm of ['risk_of_bias', 'inconsistency', 'imprecision', 'publication_bias']) {
      const sg = (adv.data.advice.domains[dm] || {}).suggestion || {};
      const cell = await txt(p, '#gr-adv-' + dm);
      check(sg.summary && cell.indexOf(sg.summary.hu) >= 0, dm + ': a tanács a motor szövegével („' + (sg.summary ? sg.summary.hu.slice(0, 60) : '—') + '”)');
    }
    check((await txt(p, '#gr-adv-risk_of_bias')).indexOf('Aronson 1948') >= 0, 'a RoB-tanács a konszenzusos (magas) Aronson 1948-at is megnevezi — az értékelések a GRADE-ig érnek');
    await rate(p, 'risk_of_bias', 'not serious', 'A magas RoB-ú vizsgálatok súlya 17% — a gyermek-futás szerint nem változtat. ' + PRIVATE);
    await rate(p, 'inconsistency', 'serious', 'I² 92%, a PI átnyúlik az 1-en.');
    await rate(p, 'indirectness', 'not serious', 'A populáció és az oltás egyezik a kérdéssel.');
    await rate(p, 'imprecision', 'not serious', 'A CI nem lépi át az 1-et; OIS teljesül.');
    await rate(p, 'publication_bias', 'suspected', 'A tesztek gyenge ereje miatt gyanú marad.');
    check(!!(await p.$('#gr-step-publication_bias input[type="radio"]')) && (await txt(p, '#gr-step-publication_bias')).indexOf('feloldatlan') >= 0, '„gyanított”: feloldatlan, 0 / −1 választó');
    await closeToasts(p);
    await p.click('#gr-save');
    await waitToast(p, /GRADE-ítélet mentve/);
    await screen(p, 'grade');
    const g1 = readJson(srv, '06_kezirat/grade/o1.grade.json');
    check(g1.domains.publication_bias.status === 'unresolved' && g1.certainty === null, 'a lemezen: a publikációs torzítás feloldatlan, bizonyosság nincs');
    check(await p.$eval('#gr-x019', (e) => !e.hidden) && await p.$eval('#gr-record', (b) => b.disabled), 'X019-sáv látszik, a rögzítés tiltva');
    const a1 = py(['audit', srv.proj]);
    check(auditCodes(a1, 'o1').indexOf('X019') >= 0, 'a motor project audit-ja is X019-et jelez (' + auditCodes(a1, 'o1').join(',') + ')');
    // a szerver kapuja akkor is tilt, ha a gomb tiltását megkerülik
    await p.evaluate(() => { document.getElementById('gr-record').disabled = false; });
    await p.click('#gr-record');
    await p.waitForSelector('#gr-msg .gr-gate', { timeout: 15000 });
    check((await txt(p, '#gr-msg')).indexOf('X019') >= 0, 'GATE_BLOCKED X019 a szervertől is');
    check(readJson(srv, '06_kezirat/grade/o1.grade.json').certainty === null, 'tiltott rögzítés után sincs bizonyosság');
    // az ember dönt: −1 indoklással
    const radios = await p.$$('#gr-step-publication_bias input[type="radio"]');
    await radios[1].check();
    check(await p.$eval('#gr-x019', (e) => e.hidden) && !(await p.$eval('#gr-record', (b) => b.disabled)), 'a döntés után az X019-sáv eltűnik, a rögzítés engedett');
    await p.click('#gr-record');
    await p.waitForSelector('#gr-human-cert:not([hidden])', { timeout: 15000 });
    const pre = await p.$eval('#gr-human-cert-sel', (e) => e.value);
    const rec422 = o.sink.requests.filter((r) => r.method === 'PUT' && r.path === '/api/grade/o1' && r.body && r.body.record).length;
    check(pre === 'low' && rec422 >= 1, 'a végső bizonyosság emberi ítélet: a választó a motor számolt szintjével előtöltve (' + pre + '), még nincs rögzítve');
    const draft = readJson(srv, '06_kezirat/grade/o1.grade.json');
    const log0 = await api(p, 'GET', '/api/log/grade');
    check(draft.certainty_source !== 'human' && draft.status !== 'recorded' && !log0.data.items.some((r) => r.outcome === 'o1'),
      'megerősítés nélkül nincs rögzítés: a piszkozatban csak a motor számolt szintje (' + draft.certainty + ', ' + (draft.certainty_source || '—') + '), naplósor nincs');
    await closeToasts(p);
    await p.click('#gr-record');
    await waitToast(p, /rögzítve/);
    await screen(p, 'grade');
    const log = await api(p, 'GET', '/api/log/grade');
    const row = log.data.items.filter((r) => r.outcome === 'o1').pop();
    check(row && row.certainty === 'low' && /^−1 /.test(row.publication_bias) && /^−1 /.test(row.inconsistency), 'projektnapló: alacsony; inkonzisztencia −1, publikációs torzítás −1 (' + JSON.stringify(row && [row.certainty, row.inconsistency, row.publication_bias]) + ')');
    const g2 = readJson(srv, '06_kezirat/grade/o1.grade.json');
    check(g2.certainty === 'low' && g2.domains.publication_bias.step === -1, 'a GRADE-fájl: low, PB −1');
    const a2 = py(['audit', srv.proj]);
    check(['X019', 'X007'].every((c) => auditCodes(a2, 'o1').indexOf(c) < 0), 'rögzítés után X019 és X007 tiszta (o1: ' + (auditCodes(a2, 'o1').join(',') || '—') + ')');
    check(activityText(srv).indexOf(PRIVATE) < 0, 'az activity-naplóban nincs indoklás-szöveg');
    await finishPage(o, 'GRADE');
  });

  await test('2b. SoF két alapkockázattal (a kontrollcsoportból + külső) → a motor szövegei; CSV (képletinjekció-őr) és Markdown export', async () => {
    const o = await openApp(srv);
    const p = o.page;
    await go(p, '#/grade?outcome=o1', 'grade');
    await p.waitForSelector('#sof-table', { timeout: 30000 });
    await p.click('#sof-add-risk');
    await p.fill('#sof-label-1', '=HYPERLINK("http://x")');
    await p.fill('#sof-per-1', '12,5');
    await p.click('#sof-add-note');
    await p.fill('#sof-note-0', '@SUM(A1) — saját lábjegyzet');
    await p.click('#sof-preview');
    await p.waitForFunction(() => /HYPERLINK/.test((document.querySelector('#sof-table') || {}).textContent || ''), null, { timeout: 15000 });
    check((await txt(p, '#sof-notes')).indexOf('12.5/1000') >= 0, 'előnézet: a külső alapkockázat (12,5 / 1000 — a motor értelmezésében) a lábjegyzetben');
    await closeToasts(p);
    await p.click('#sof-save');
    await waitToast(p, /SoF-tábla mentve/);
    const sof = readJson(srv, '06_kezirat/sof/o1.sof.json');
    const cells = await p.$$eval('#sof-table tbody tr', (trs) => trs.map((tr) => { const o = {}; tr.querySelectorAll('[data-col]').forEach((c) => { o[c.dataset.col] = c.textContent; }); return o; }));
    const rows = sof.rows || [];
    const r0 = rows[0] || {};
    const abs = r0.absolute || [];
    check(rows.length === 1 && cells.length === 1 && abs.length === 2, 'egy kimenet-sor, benne két alapkockázat (a mentett SoF-ban és a táblában)');
    const pickT = (x) => (x && typeof x === 'object' ? x.hu : x) || '';
    check(cells[0] && abs.every((a) => cells[0].absolute_text.indexOf(a.text.hu) >= 0) && cells[0].relative_text.indexOf(r0.relative.text.hu) >= 0,
      'a cellák a motor szövegei (relatív: ' + pickT(r0.relative_text) + '; abszolút: ' + abs.map((a) => a.text.hu).join(' | ') + ')');
    check(r0.certainty === 'low', 'a SoF bizonyossága a rögzített GRADE-é (low)');
    const a = py(['audit', srv.proj]);
    check(auditCodes(a, 'o1').indexOf('X008') < 0, 'X008 tiszta: a SoF cellái = a motor eredménye');
    await p.selectOption('#sof-lang', 'hu');
    await p.click('#sof-export-csv');
    await p.waitForSelector('#sof-dl', { timeout: 15000 });
    const csvPath = path.join(srv.proj, '06_kezirat', 'sof', 'o1.sof.hu.csv');
    const csv = fs.readFileSync(csvPath, 'utf-8');
    check(csv.charCodeAt(0) === 0xFEFF && csv.split('\n')[0].indexOf(';') >= 0, 'CSV: BOM, magyar tagoló (;)');
    check(csv.indexOf("'=HYPERLINK") >= 0 && !/(^|;)"?=HYPERLINK/m.test(csv), 'képletinjekció-őr: a „=” kezdetű címke elé \' került');
    const bad = csvCells(csv.replace(/^\uFEFF/, ''), ';').filter((c) => /^[=+\-@\t\r]/.test(c));
    check(bad.length === 0, 'képletinjekció-őr: egyetlen cella sem kezdődik = + - @ jellel (' + bad.join(' | ') + ')');
    const href = await p.$eval('#sof-dl', (x) => x.getAttribute('href'));
    const dl = await p.evaluate((u) => fetch(u).then((r) => r.arrayBuffer()).then((b) => b.byteLength), href);
    check(/^\/f\//.test(href) && dl === fs.statSync(csvPath).size, 'aláírt letöltési link, bájtra a fájl');
    await p.click('#sof-export-md');
    await p.waitForSelector('#sof-md', { timeout: 15000 });
    const md = await txt(p, '#sof-md');
    check(/^\|/m.test(md) && md.indexOf('0.49') >= 0, 'Markdown-tábla a motor szövegeivel');
    await finishPage(o, 'SoF');
  });
}

// ================================================================ 3. kettős kinyerés
async function dualReady(p) {
  await screen(p, 'dual');
  await p.waitForSelector('#dx-items table[role="grid"], #dx-engine-missing, #dx-need-files, #dx-error', { timeout: 20000 });
}
async function counterText(p) { return txt(p, '#dx-counter'); }
async function importSideUI(p, side, rater, name, text) {
  await p.fill('#dx-rater-' + side, rater);
  const [chooser] = await Promise.all([p.waitForEvent('filechooser'), p.click('#dx-import-' + side)]);
  await chooser.setFiles({ name, mimeType: 'text/csv', buffer: Buffer.from(text, 'utf-8') });
  await waitToast(p, new RegExp(side.toUpperCase() + '[^]*(import|átv|felvé|mentve)', 'i')).catch(() => null);
  await p.waitForTimeout(500);
}

async function dualSection() {
  const srv = await ensureMain();
  await test('3. kettős kinyerés A/B a felületen → összevetés (csak írásmód vs valódi eltérés, súgó, hatás) → egyeztetés → konszenzus-CSV → elemzés', async () => {
    const o = await openApp(srv);
    const p = o.page;
    await go(p, '#/dual?outcome=o3', 'dual');
    await dualReady(p);
    await importSideUI(p, 'a', 'SzK', 'o3.A.csv', srv.a_csv);
    await dualReady(p);
    await importSideUI(p, 'b', 'KP', 'o3.B.csv', srv.b_csv);
    await dualReady(p);
    await p.waitForSelector('#dx-items table', { timeout: 20000 });
    check(fs.readFileSync(path.join(srv.proj, '03_adatok', 'kettos', 'o3.B.csv'), 'utf-8') === srv.b_csv, 'a B tábla bájtra azonosan a kettos/ mappában');
    const view = await api(p, 'POST', '/api/compare', { outcome: 'o3' });
    const eng = py(['kettos', srv.proj, 'o3']);
    const items = view.data.items;
    const real = items.filter((i) => !i.auto);
    const fmt = items.filter((i) => i.auto);
    check(items.length === eng.n && real.length >= 5 && fmt.length >= 1, 'az eltérések a motor összevetéséből: ' + real.length + ' valódi, ' + fmt.length + ' csak írásmód (motor: ' + eng.n + ')');
    check(new RegExp('Eldöntve 0/' + real.length).test(await counterText(p)), 'számláló: 0/' + real.length + ' (a csak-írásmód eltérés nem kér döntést): ' + await counterText(p));
    check((await p.$$('#dx-items tbody tr[data-key]')).length === items.length, 'a listában minden eltérés (' + items.length + ')');
    const fmtRow = await p.evaluate((k) => { const tr = Array.from(document.querySelectorAll('#dx-items tbody tr[data-key]')).find((r) => r.dataset.key.indexOf(k) === 0); return tr ? tr.textContent : ''; }, fmt[0].key);
    check(/írásmód|automatikus|auto/i.test(fmtRow), 'a csak-írásmód eltérés jelölve (' + fmtRow.replace(/\s+/g, ' ').slice(0, 80) + ')');
    // a kijelölt valódi eltérés: súgó és hatás a motor szövegével
    const first = real[0];
    const hint = await txt(p, '#dx-hint');
    const impact = await txt(p, '#dx-impact');
    check(first.hint && hint.indexOf(first.hint.hu.slice(0, 40)) >= 0, 'súgó a motorból („' + (first.hint ? first.hint.hu.slice(0, 50) : '—') + '”)');
    check(!first.impact || impact.indexOf(first.impact.text.hu) >= 0, 'hatás a motorból (' + (first.impact ? first.impact.text.hu : '—') + ')');
    // X009-kapu: írás csak minden döntés után
    check((await p.getAttribute('#dx-write', 'aria-disabled')) === 'true', 'a konszenzus-CSV írása tiltva, amíg van feloldatlan eltérés (X009)');
    for (let i = 0; i < 40; i++) {
      const c = await counterText(p);
      const m = /(\d+)\/(\d+)/.exec(c);
      if (!m || m[1] === m[2] || !(await p.$('#dx-detail .dx-choice input[type="radio"]'))) { break; }
      await p.check('#dx-detail .dx-choice input[type="radio"][value="a"]');
      await p.fill('#dx-reason', 'A forrás Table 1 szerint az A értéke helyes. ' + PRIVATE);
      await p.click('#dx-decide-next');
      await p.waitForFunction((b) => (document.getElementById('dx-counter') || {}).textContent !== b, c, { timeout: 15000 });
    }
    check(new RegExp('Eldöntve ' + real.length + '/' + real.length).test(await counterText(p)), 'minden eltérés eldöntve: ' + await counterText(p));
    await closeToasts(p);
    await p.click('#dx-write');
    await p.waitForSelector('#dx-export-consensus', { timeout: 20000 });
    const ccsv = fs.readFileSync(path.join(srv.proj, '03_adatok', 'kettos', 'o3.consensus.csv'), 'utf-8');
    check(ccsv.split('\n')[0].indexOf('vizsgálat') >= 0 && ccsv.split('\n').filter((l) => l.trim()).length === 14, 'konszenzus-CSV: fejléc + 13 sor');
    await closeToasts(p);
    await p.click('#dx-write-outcome');
    await p.waitForSelector('.modal-backdrop');
    check(/03_adatok\/o3\.csv/.test(await txt(p, '.modal-backdrop')), 'megerősítés a cél útjával');
    await p.click('.modal-backdrop .btn-danger');
    await waitToast(p, /adattáblája frissítve|frissítve/);
    check(exists(srv, '03_adatok/o3.csv'), 'a kimenet adattáblája (03_adatok/o3.csv) megvan');
    const a = py(['audit', srv.proj]);
    check(auditCodes(a, 'o3').indexOf('X009') < 0, 'X009 tiszta (o3: ' + (auditCodes(a, 'o3').join(',') || '—') + ')');
    const act = activityText(srv);
    check(act.indexOf('kettos.reconcile') >= 0 && act.indexOf(PRIVATE) < 0 && act.indexOf('Ferguson') < 0, 'activity: van egyeztetés-sor, de indoklás és cellaérték nincs');
    // elemzés a konszenzus-táblán
    const res = await planCommit(p, o.sink, 'o3', {}, 'o3');
    const pl = await api(p, 'GET', '/api/runs/' + res.run.run_id + '/plot');
    const prim = pl.data.summaries.find((x) => x.primary);
    check(pl.data.k === 13 && res.summary.indexOf(prim.display_text.hu) >= 0, 'o3 elemzés: k = 13, az explore-összegzés a motor szövege (' + prim.display_text.hu + ')');
    if (S.o1run) {
      const p1 = await api(p, 'GET', '/api/runs/' + S.o1run.run_id + '/plot');
      check(p1.data.summaries.find((x) => x.primary).display_text.hu === prim.display_text.hu, 'a konszenzus (az A értékei) = a BCG eredeti adatai: ugyanaz az összesített hatás, mint az o1-nél');
    }
    S.o3run = res.run;
    await finishPage(o, 'kettős kinyerés');
  });
}

// ================================================================ 5. ábra-export
const sha256 = (buf) => require('crypto').createHash('sha256').update(buf).digest('hex');

async function figuresSection() {
  const srv = await ensureMain();
  await test('5. ábra-export: motor-SVG magyarul és angolul (bájtra a motor render_figure-je), számhűség-QC; figure-forge audit vagy „nem használható” teendővel', async () => {
    const o = await openApp(srv);
    const p = o.page;
    let run = S.o1run;
    if (!run) { const r = await api(p, 'GET', '/api/runs?outcome=o1'); run = r.data.runs.find((x) => !x.parent) || r.data.runs[0]; }
    // az Eredményekből a futás ábra-exportjára
    await go(p, '#/results?outcome=o1&run=' + run.run_id + '&view=forest', 'results');
    await p.waitForSelector('#results-figures', { timeout: 20000 });
    check((await p.getAttribute('#results-figures', 'href')).indexOf('#/figures?run=' + run.run_id) === 0, 'Eredmények → „Ábra-export” link a futásra');
    await p.click('#results-figures');
    await screen(p, 'figures');
    await p.waitForSelector('#fig-form', { timeout: 20000 });
    check(await p.$eval('#fig-run', (sel) => sel.value) === run.run_id, 'az ábra-export a futással nyílik');
    // a figure-forge várt állapota: hiányzik (stdlib-audit) / telepítve, de nem használható (H5 + teendő) / használható
    const capsFF = async () => {
      const caps = await api(p, 'GET', '/api/adapters');
      return (caps.data.features || []).find ? caps.data.features.find((f) => f.id === 'figure_audit') : (caps.data.features || {}).figure_audit;
    };
    const fa0 = await capsFF();
    const ffPlugin = realPlugin('figure-forge');
    const ffState = !ffPlugin ? 'absent' : (srv.ff_python || ['legacy', 'ok'].indexOf(fa0 && fa0.state) >= 0) ? 'usable' : 'unusable';
    const ffOk = ffState === 'usable';
    check(!!fa0 && (ffState === 'usable' ? ['legacy', 'ok'].indexOf(fa0.state) >= 0 : fa0.state === ffState),
      'képességek: figure-forge audit állapota ' + (fa0 && fa0.state) + ' (várt: ' + ffState + ')');
    for (const [kind, lang] of [['forest', 'hu'], ['forest', 'en'], ['doi', 'en']]) {
      await p.click('#fig-kind-' + kind);
      await p.selectOption('#fig-lang', lang);
      const stem = 'fig_' + kind + '_o1_' + lang;
      await p.fill('#fig-stem', stem);
      await closeToasts(p);
      await p.click('#fig-export');
      await p.waitForFunction((st) => { const f = document.getElementById('fig-files'); return f && f.textContent.indexOf(st + '.svg') >= 0; }, stem, { timeout: 60000 });
      const rel = '06_kezirat/abrak/' + stem + '.svg';
      const file = fs.readFileSync(path.join(srv.proj, rel));
      const eng = py(['figure', srv.proj, run.dir, kind, lang, '1']);
      check(eng.ok && sha256(file) === eng.sha256, kind + '/' + lang + ': az exportált SVG bájtra a motor render_figure-je (' + (eng.ok ? eng.sha256.slice(0, 12) : eng.error) + ')');
      const svgText = file.toString('utf-8');
      if (kind === 'forest') check(eng.ok && eng.display.every((d) => svgText.indexOf(d) >= 0), kind + '/' + lang + ': a motor összesítő szövege az ábrán (' + (eng.display || []).join(', ') + ')');
      if (lang === 'en') { check(/−|lang="en"|Weight|Study|Total|Overall|RR/.test(svgText), kind + '/en: angol feliratú ábra'); }
      const badge = await p.$eval('#fig-qc-badge', (b) => b.dataset.badge);
      const res = readJson(srv, '06_kezirat/abrak/' + stem + '.result.json');
      check(res.qc && res.qc.badge === badge && res.server_check && res.server_check.ok === true, kind + '/' + lang + ': QC a szerver újraellenőrzésével (számhűség: ' + JSON.stringify(res.server_check && { ok: res.server_check.ok, n: res.server_check.checked || res.server_check.n }) + ')');
      check(/(\d+)\/\1 motorszöveg/.test(await txt(p, '#fig-result')), kind + '/' + lang + ': számhűség 100% (' + ((/\d+\/\d+ motorszöveg/.exec(await txt(p, '#fig-result')) || [''])[0]) + ')');
      if (ffState === 'usable') {
        check(!!(await p.$('#fig-qc-ff')) && /figure-forge audit/.test(await txt(p, '#fig-qc-ff')) && /Szerkeszthető/.test(await txt(p, '#fig-qc-ff')),
          kind + '/' + lang + ': figure-forge audit a QC-ben (' + (await txt(p, '#fig-qc-ff')).replace(/\s+/g, ' ').slice(0, 80) + ')');
        check(badge === 'ok', kind + '/' + lang + ': QC zöld');
      } else if (ffState === 'unusable') {
        const down = await txt(p, '#fig-qc-ffdown');
        check(/H5|nem használható/.test(down) && /pip install matplotlib/.test(down), kind + '/' + lang + ': figure-forge „nem használható” — a teendő kiírva (' + down.replace(/\s+/g, ' ').slice(0, 90) + ')');
      } else {
        // nincs telepítve: nincs figure-forge blokk, a beépített (stdlib) audit fut
        check(!(await p.$('#fig-qc-ff')) && !(await p.$('#fig-qc-ffdown')) && /Szerkeszthető/.test(await txt(p, '#fig-qc-std')),
          kind + '/' + lang + ': figure-forge nincs telepítve — a beépített SVG-audit a QC-ben (' + (await txt(p, '#fig-qc-std')).replace(/\s+/g, ' ').slice(0, 80) + ')');
      }
    }
    // a képesség-mátrix a végén is ugyanazt mondja
    const fa = await capsFF();
    check(!!fa && fa.state === fa0.state, 'képességek: figure-forge audit állapota változatlan (' + (fa && fa.state) + ')');
    const a = py(['audit', srv.proj]);
    check(['X002', 'X018'].every((c) => auditCodes(a).indexOf(c) < 0), 'X002/X018: az exportált ábrák frissek és QC-tiszták (' + auditCodes(a).join(',') + ')');
    await finishPage(o, 'ábra-export');
  });
}

// ================================================================ 6. composer-híd
async function composerSection() {
  const srv = await ensureMain();
  await test('6. composer-híd: a composer (teszt-csonk) prisma-flow.json-ja → előnézet a motor ellenőrzésével → frissítés → PRISMA', async () => {
    const o = await openApp(srv);
    const p = o.page;
    await go(p, '#/prisma-composer', 'prisma-composer');
    await p.waitForSelector('#comp-project');
    const plug = (await txt(p, '#comp-plugin')) + (await txt(p, '#comp-head'));
    check(/1\.4\.1/.test(plug) && /H7|saját Python/.test(plug), 'a composer-csonk (1.4.1, legacy) felismerve, H7-őr leírása (' + plug.replace(/\s+/g, ' ').slice(0, 100) + ')');
    await p.fill('#comp-project', srv.composer_project);
    await p.fill('#comp-outdir', srv.composer_outdir);
    await p.click('#comp-save-loc');
    await p.waitForFunction(() => { const b = document.querySelector('#comp-refresh'); return b && !b.disabled; }, null, { timeout: 20000 });
    await p.click('#comp-preview');
    await p.waitForSelector('#comp-flow', { timeout: 30000 });
    check((await txt(p, '#comp-flow tr[data-box="B"]')).indexOf('160') >= 0, 'előnézet: B (szűrt) = 160 a composer flow-jából');
    check((await txt(p, '#comp-flow tr[data-box="J"]')).indexOf('13') >= 0, 'J = 13 (a composer „included” értéke, jelentések — H7)');
    check((await txt(p, '#comp-check')).indexOf('A motor ellenőrzése') >= 0, 'a motor PRISMA-ellenőrzése az előnézeten');
    check(!exists(srv, '02_szures/prisma_flow.json'), 'előnézet: még semmi nem íródott');
    await closeToasts(p);
    await p.click('#comp-refresh');
    const modal = await p.waitForSelector('.modal-backdrop', { timeout: 5000 }).catch(() => null);
    if (modal) { await p.click('.modal-backdrop .btn-primary, .modal-backdrop .btn-danger'); }
    await p.waitForFunction(() => { const r = document.querySelector('#comp-mode-composer'); return r && r.checked; }, null, { timeout: 30000 });
    const flow = readJson(srv, '02_szures/prisma_flow.json');
    check(flow.schema === 'szk.prisma-flow/v1' && flow.source && flow.source.kind === 'composer' && flow.screened === 160 && flow.included_reports === 13,
      'a PRISMA-folyamat a composerből (source.kind = composer; B = 160, J = 13)');
    const pr = await api(p, 'GET', '/api/prisma');
    const errs = (((pr.data || {}).check || {}).findings || []).filter((f) => f.severity === 'error');
    check(pr.ok && errs.length === 0, 'a motor PRISMA-ellenőrzése hibátlan (' + errs.map((f) => f.code).join(',') + ')');
    await go(p, '#/prisma', 'prisma');
    check((await txt(p, '#screen-root')).indexOf('160') >= 0, 'a PRISMA-képernyő a composer számait mutatja');
    check(activityText(srv).indexOf('composer') >= 0 || activityText(srv).indexOf('prisma') >= 0, 'activity-sor a frissítésről');
    await finishPage(o, 'composer');
  });
}

// ================================================================ 7. Metaheadhunter (kazettákkal)
const HH_REVIEWS = ['rv-pmid-41206639', 'rv-pmid-40250749'];        // BCG-újraoltás, ill. BCG-hatás (irodalomjegyzékből)
const HH_EXCLUDE_REVIEW = 'rv-pmid-42458287';                        // MDR-TB jellemzők — más PICO
const HH_CONFIRM = { 'rv-pmid-41206639': ['c0003', 'c0023', 'c0024', 'c0025', 'c0026'], 'rv-pmid-40250749': ['c0017', 'c0028'] };
const HH_REJECT = { 'rv-pmid-40250749': 'c0002' };
const HH_WINDOW = { start: '2026-09-01', end: '2026-10-05' };      // a rögzítés napjához kötött frissítési ablak

async function hhWait(p, timeout) {
  const t0 = Date.now();
  await p.waitForTimeout(250);
  for (;;) {
    const r = await api(p, 'GET', '/api/headhunter/runs');
    const jobs = r.ok ? r.data.jobs : [];
    if (!(r.ok && (r.data.active || jobs.some((j) => j.status === 'queued' || j.status === 'running')))) { return jobs; }
    if (Date.now() - t0 > (timeout || 300000)) { throw new Error('a Metaheadhunter-feladat nem fejeződött be'); }
    await p.waitForTimeout(400);
  }
}
async function hhStep(p, step) {
  await p.evaluate((s) => { window.location.hash = '#/headhunter?step=' + s; }, step);
  await p.waitForFunction((s) => {
    const r = document.getElementById('screen-root');
    const c = document.getElementById('hh-card');
    return r && r.dataset.screen === 'headhunter' && c && c.dataset.step === s && !c.querySelector('.spinner');
  }, step, { timeout: 20000 });
  await p.waitForTimeout(300);
}
async function modalOk(p) {
  await p.waitForSelector('[role="dialog"] .modal-actions .btn-primary, [role="dialog"] .modal-actions .btn-danger', { timeout: 8000 });
  await p.click('[role="dialog"] .modal-actions .btn-primary, [role="dialog"] .modal-actions .btn-danger');
}
async function lastJob(p, step) {
  const r = await api(p, 'GET', '/api/headhunter/runs');
  return (r.data.jobs || []).find((j) => j.step === step) || null;
}

async function hhSection() {
  const record = process.env.E2E_V1_HH_RECORD === '1';
  const cassettes = path.join(ROOT, 'tests', 'reference', 'headhunter', 'cassettes', 'gui_e2e');
  await test('7. Metaheadhunter a felületről, kazettákkal (élő hálózat nélkül): keresés → kinyerés → feloldás → duplum-megerősítés → kizárás → frissítés → egyesítés → PRISMA-ellenőrzés', async () => {
    if (!record && !fs.existsSync(cassettes)) { check(false, 'a kazetták hiányoznak (' + cassettes + ') — rögzítés: E2E_V1_HH_RECORD=1'); return; }
    const tmp = mkTmp('ma_e2e_v1_hh_');
    const srv = await startServer(tmp, 'hh', record ? { E2E_V1_HH_RECORD: '1' } : {});
    try {
      check(srv.cassette_mode === (record ? 'record' : 'replay'), 'kazetta-mód: ' + srv.cassette_mode + ' (élő hálózat ' + (record ? 'CSAK rögzítéskor' : 'nélkül') + ')');
      const o = await openApp(srv);
      const p = o.page;
      // kérdés (PICO) → init
      await hhStep(p, 'pico');
      await p.fill('#hh-pico-question', 'BCG vaccine efficacy against tuberculosis');
      await p.fill('#hh-pico-population', 'tuberculosis');
      await p.fill('#hh-pico-intervention', 'BCG vaccine; BCG vaccination');
      await p.click('#hh-pico-submit');
      await hhWait(p);
      const st0 = await api(p, 'GET', '/api/headhunter/status');
      check(st0.ok && st0.data.initialized, 'init a felületről (PICO jóváhagyva)');
      // források: az OpenAlex kikapcsolása (emberi döntés; a listás lekérdezések napi kerete közös)
      await hhStep(p, 'sources');
      await p.waitForSelector('#hh-src-openalex');
      if (await p.$eval('#hh-src-openalex', (c) => c.checked)) { await p.click('#hh-src-openalex'); await hhWait(p); }
      const src = await api(p, 'GET', '/api/headhunter/sources');
      check(src.data.rows.find((r) => r.source === 'openalex').enabled === false && src.data.rows.find((r) => r.source === 'pubmed').enabled, 'források: PubMed/Europe PMC be, OpenAlex ki (döntés)');
      // EP1: áttekintések keresése és kiválasztása
      await hhStep(p, 'reviews');
      await p.fill('#hh-find-max', '8');
      await closeToasts(p);
      await p.click('#hh-find');
      await hhWait(p);
      const fj = await lastJob(p, 'find_reviews');
      check(fj && fj.status === 'done', 'áttekintés-keresés kész (' + (fj && fj.status) + (fj && fj.error ? ' — ' + fj.error.message : '') + ')');
      await hhStep(p, 'reviews');
      await p.waitForSelector('#hh-rv-list li[data-review]', { timeout: 20000 });
      const found = await p.$$eval('#hh-rv-list li[data-review]', (ls) => ls.map((l) => l.dataset.review));
      check(HH_REVIEWS.concat([HH_EXCLUDE_REVIEW]).every((r) => found.indexOf(r) >= 0), 'a rangsorban a várt áttekintések (' + found.length + ' db)');
      for (const rv of HH_REVIEWS) {
        await p.click('#hh-rv-list li[data-review="' + rv + '"] .hh-rv-include');
        await modalOk(p);
        await hhWait(p);
        await hhStep(p, 'reviews');
      }
      await p.click('#hh-rv-list li[data-review="' + HH_EXCLUDE_REVIEW + '"] .hh-rv-exclude');
      await p.waitForSelector('#hh-reason');
      await p.fill('#hh-reason', 'Más PICO: MDR-TB jellemzők, nem BCG-hatás.');
      await modalOk(p);
      await hhWait(p);
      const rvs = await api(p, 'GET', '/api/headhunter/reviews');
      const byId = {};
      (rvs.data.items || []).forEach((r) => { byId[r.review_id] = r.status; });
      check(HH_REVIEWS.every((r) => byId[r] === 'selected') && byId[HH_EXCLUDE_REVIEW] === 'excluded', 'EP1: két áttekintés kiválasztva, egy kizárva indoklással');
      // EP2: kinyerés → jelöltek egyenkénti megerősítése / elvetése → feloldás
      await hhStep(p, 'reviews');
      await closeToasts(p);
      await p.click('#hh-extract-all');
      await hhWait(p);
      const ej = await lastJob(p, 'extract');
      check(ej && ej.status === 'done', 'kinyerés kész (' + (ej && ej.status) + ')');
      for (const rv of HH_REVIEWS) {
        await hhStep(p, 'extract');
        await p.click('[role="tab"][data-tab="' + rv + '"]');
        await p.waitForSelector('#hh-cand-table tr[data-cand]', { timeout: 20000 });
        for (const c of HH_CONFIRM[rv]) {
          await p.click('#hh-cand-table tr[data-cand="' + c + '"] .hh-cand-confirm');
          await hhWait(p);
          await hhStep(p, 'extract');
          await p.click('[role="tab"][data-tab="' + rv + '"]');
          await p.waitForSelector('#hh-cand-table tr[data-cand="' + c + '"].is-confirmed', { timeout: 20000 });
        }
        if (HH_REJECT[rv]) {
          await p.click('#hh-cand-table tr[data-cand="' + HH_REJECT[rv] + '"] .hh-cand-reject');
          await p.waitForSelector('#hh-reason');
          await p.fill('#hh-reason', 'Betegségteher-tanulmány, nem bevont vizsgálat.');
          await modalOk(p);
          await hhWait(p);
        }
      }
      await hhStep(p, 'extract');
      await closeToasts(p);
      await p.click('#hh-resolve');
      await hhWait(p);
      const rj = await lastJob(p, 'resolve');
      check(rj && rj.status === 'done', 'azonosító-feloldás kész (' + (rj && rj.status) + ')');
      const studies1 = await api(p, 'GET', '/api/headhunter/studies');
      check(studies1.ok, 'vizsgálat-lista olvasható');
      // EP3: duplumok — a „ugyanaz a vizsgálat” javaslat megerősítése
      await hhStep(p, 'dedupe');
      await closeToasts(p);
      await p.click('#hh-dedupe');
      await hhWait(p);
      await hhStep(p, 'dedupe');
      await p.waitForSelector('article.hh-prop[data-proposal]', { timeout: 20000 });
      const props = await p.$$eval('article.hh-prop[data-proposal]', (as) => as.map((a) => a.dataset.proposal));
      check(props.length >= 1, 'duplum-javaslat a motor szabályából (' + props.join(',') + ')');
      await p.click('article.hh-prop[data-proposal="' + props[0] + '"] .hh-prop-accept');
      await hhWait(p);
      const pr = await api(p, 'GET', '/api/headhunter/proposals?status=all');
      const accepted = (pr.data.items || pr.data.proposals || []).find((x) => x.proposal_id === props[0]);
      check(accepted && accepted.status === 'accepted', 'EP3: a javaslat elfogadva (' + (accepted && accepted.status) + ')');
      // EP4 (1): a bányászott vizsgálatok szűrése
      async function screenAll(includeIds) {
        await hhStep(p, 'screen');
        const btn = (await p.$('#hh-sc-merge')) || (await p.$('#hh-sc-refresh'));
        await btn.click();
        await hhWait(p);
        await hhStep(p, 'screen');
        await p.waitForSelector('#hh-sc-list');
        const ids = await p.$$eval('#hh-sc-list .hh-sc-row.is-pending', (rs) => rs.map((r) => r.dataset.study));
        let inc = 0, exc = 0;
        for (const id of ids) {
          await p.click('#hh-sc-list .hh-sc-row[data-study="' + id + '"]');
          const include = includeIds === null ? true : includeIds.indexOf(id) >= 0;
          await p.click('.hh-sc-actions [data-act="' + (include ? 'include' : 'excludeTa') + '"]');
          if (!include) { await modalOk(p); exc++; } else { inc++; }
          await hhWait(p);
        }
        return { n: ids.length, inc, exc };
      }
      const s1 = await screenAll(null);
      check(s1.n >= 4 && s1.inc === s1.n, 'EP4: a bányászott vizsgálatok bevonva (' + s1.inc + ')');
      // frissítő keresés a rögzített ablakkal
      await hhStep(p, 'update');
      await p.selectOption('#hh-up-anchor', 'manual');
      await p.fill('#hh-up-start', HH_WINDOW.start);
      await p.fill('#hh-up-end', HH_WINDOW.end);
      await p.fill('#hh-up-months', '');
      await closeToasts(p);
      await p.click('#hh-up-run');
      await modalOk(p);
      await hhWait(p);
      const uj = await lastJob(p, 'update_search');
      check(uj && uj.status === 'done', 'frissítő keresés kész (' + (uj && uj.status) + (uj && uj.partial ? ', részleges' : '') + ')');
      const up = await api(p, 'GET', '/api/headhunter/update');
      check(up.ok && up.data.window && up.data.window.end_date === HH_WINDOW.end && up.data.window.start_date === HH_WINDOW.start, 'az ablak a felületen megadott (' + JSON.stringify(up.data.window && [up.data.window.start_date, up.data.window.end_date]) + ')');
      // EP4 (2): az új rekordok címszintű kizárása (egy kivétellel: az első bevonva)
      await hhStep(p, 'screen');
      const pend = await (async () => { await (await p.$('#hh-sc-refresh')).click(); await hhWait(p); await hhStep(p, 'screen'); return p.$$eval('#hh-sc-list .hh-sc-row.is-pending', (rs) => rs.map((r) => r.dataset.study)); })();
      const s2 = await screenAll(pend.slice(0, 1));
      check(s2.n >= 1 && s2.exc === s2.n - 1, 'EP4: az új rekordok elbírálva (' + s2.inc + ' bevonva, ' + s2.exc + ' kizárva címszinten)');
      // egyesítés + PRISMA
      await hhStep(p, 'merge');
      await closeToasts(p);
      await p.click('#hh-merge');
      await hhWait(p);
      await hhStep(p, 'merge');
      await p.click('#hh-prisma-run');
      await hhWait(p);
      const pj = await lastJob(p, 'prisma');
      check(pj && pj.status === 'done', 'PRISMA-leképezés kész (' + (pj && pj.status) + ')');
      const prisma = await api(p, 'GET', '/api/headhunter/prisma');
      const pc = ((prisma.data || {}).check) || {};
      check(prisma.ok && pc.ok === true && pc.summary && pc.summary.error === 0, 'a felületen: a motor PRISMA-ellenőrzése 0 hiba (' + JSON.stringify(pc.summary || null) + ')');
      const chk = JSON.parse(run('python3', ['ma.py', 'prisma', 'check', '--json', path.join(srv.proj, '01_kereses', 'headhunter', 'prisma_flow.json'), '--out-format', 'json'], { cwd: ROOT }));
      check(chk.ok === true && chk.summary.error === 0 && chk.summary.warning === pc.summary.warning, 'ma.py prisma check ugyanazt mondja: ' + JSON.stringify(chk.summary));
      check(chk.counts && chk.counts.screened > 0, 'PRISMA: szűrt rekordok ' + (chk.counts && chk.counts.screened) + ', más módszerrel azonosított: ' + JSON.stringify(chk.counts && chk.counts.other_methods_identified));
      await hhStep(p, 'merge');
      const mtxt = await txt(p, '#hh-card');
      check(/EP5|lezárás|sign/i.test(mtxt), 'az egyesítés lépésen a lezárás (EP5) teendője látszik');
      // napló: hash-lánc, szabad szöveg az activity-naplóban nincs
      const ver = await api(p, 'GET', '/api/headhunter/decisions');
      check(ver.ok && (ver.data.items || ver.data.decisions || []).length >= 10, 'döntésnapló: ' + ((ver.data.items || ver.data.decisions || []).length) + ' döntés');
      const act = activityText(srv);
      check(act.indexOf('MDR-TB jellemzők') < 0 && act.indexOf('Betegségteher') < 0, 'az activity-naplóban nincs indoklás-szöveg');
      check(srv.stderr().indexOf('CassetteMiss') < 0, 'nem volt ismeretlen (a kazettában nem szereplő) kérés');
      await finishPage(o, 'Metaheadhunter');
    } finally {
      await srv.stop();
      if (record) { console.log('  kazetták utókezelése (affiliáció, e-mail): ' + JSON.stringify(py(['scrub', cassettes]))); }
    }
  });
}

// ================================================================ 8. audit: teljes X-szabálykészlet + FINAL-kapu
const ALL_X = Array.from({ length: 22 }, (_, i) => 'X' + String(i + 1).padStart(3, '0'));

async function finalGateUI(p) {
  await go(p, '#/log?tab=checkpoint&stage=FINAL', 'log');
  await p.waitForSelector('#log-checkpoints');
  await p.selectOption('#log-gate-stage', 'FINAL').catch(() => null);
  await p.selectOption('#log-gate-verdict', 'PASS');
  await p.waitForFunction(() => { const e = document.getElementById('log-gate-pre'); return e && e.textContent.trim().length > 0; }, null, { timeout: 30000 });
  return { pre: await txt(p, '#log-gate-pre'), disabled: await p.$eval('#log-gate-submit', (b) => b.disabled) };
}

async function xrulesSection() {
  await test('8a. a teljes X-szabálykészlet (X001–X022) egy felépített projekten: a felület a motor auditját mutatja; a FINAL-kapu tilt', async () => {
    const tmp = mkTmp('ma_e2e_v1_xr_');
    const srv = await startServer(tmp, 'xrules');
    try {
      const o = await openApp(srv);
      const p = o.page;
      const eng = py(['audit', srv.proj]);
      const engFinal = py(['audit', srv.proj, 'FINAL']);
      const engCodes = Array.from(new Set((engFinal.findings || []).map((f) => f.code))).sort();
      check(ALL_X.every((c) => engCodes.indexOf(c) >= 0), 'a motor (FINAL): mind a 22 X-szabály jelez (' + engCodes.length + '/22; hiányzik: ' + ALL_X.filter((c) => engCodes.indexOf(c) < 0).join(',') + ')');
      const ov = await overviewCodes(p);
      const guiCodes = Array.from(new Set(ov.map((x) => x.code))).sort();
      const engNow = Array.from(new Set((eng.findings || []).map((f) => f.code))).sort();
      check(guiCodes.join(',') === engNow.join(','), 'Áttekintés: a teendő-lista kódjai = a motor auditja (' + guiCodes.length + ' kód)');
      check((await p.$$('li.ov-x')).length === (eng.findings || []).length, 'Áttekintés: minden találat egy sor (' + (eng.findings || []).length + ')');
      await go(p, '#/log?tab=audit', 'log');
      await p.waitForSelector('#log-audit li[data-code]', { timeout: 20000 });
      const logCodes = Array.from(new Set(await p.$$eval('#log-audit li[data-code]', (ls) => ls.map((l) => l.dataset.code)))).sort();
      check(logCodes.join(',') === engNow.join(','), 'Napló / X-szabályok: ugyanaz a ' + logCodes.length + ' kód');
      const g = await finalGateUI(p);
      const errs = Array.from(new Set((engFinal.findings || []).filter((f) => f.severity === 'error').map((f) => f.code))).sort();
      check(g.disabled && errs.every((c) => g.pre.indexOf(c) >= 0), 'FINAL PASS a felületen tiltva, a hibakódok megnevezve (' + errs.join(',') + ')');
      const direct = await api(p, 'POST', '/api/log/checkpoint', { stage: 'FINAL', agent: 'user', verdict: 'PASS', summary: 'kísérlet' });
      check(!direct.ok && direct.code === 'GATE_BLOCKED' && errs.every((c) => JSON.stringify(direct.details || {}).indexOf(c) >= 0), 'a szerver is elutasítja: GATE_BLOCKED, audit_errors: ' + errs.length + ' kód');
      await finishPage(o, 'X-szabályok');
    } finally {
      await srv.stop();
    }
  });
}

async function finalSection() {
  const srv = await ensureMain();
  await test('8b. FINAL-kapu a végigvitt projekten: előbb tilt (a megnevezett hibákkal), a javítás után átenged', async () => {
    const o = await openApp(srv, { rater: 'SzK' });
    const p = o.page;
    const before = py(['audit', srv.proj, 'FINAL']);
    const errs = Array.from(new Set((before.findings || []).filter((f) => f.severity === 'error').map((f) => f.code))).sort();
    const g1 = await finalGateUI(p);
    check(errs.length > 0 && g1.disabled && errs.every((c) => g1.pre.indexOf(c) >= 0), 'FINAL tiltva: ' + errs.join(',') + ' (' + g1.pre.replace(/\s+/g, ' ').slice(0, 120) + ')');
    console.log('  FINAL előtt (hibák): ' + JSON.stringify((before.findings || []).filter((f) => f.severity === 'error').map((f) => [f.code, f.outcome, String(f.detail).slice(0, 80)])));
    // javítás a felületen: az o3 (kettős kinyerés) vizsgálatainak RoB 2-értékelése a második értékelőtől, majd rob-szinkron
    if (errs.indexOf('X004') >= 0 || errs.indexOf('X003') >= 0) {
      const units = Array.from({ length: 13 }, (_, i) => 'S' + String(i + 1).padStart(2, '0'));
      const b = py(['bundle', srv.proj, 'rob2', 'o3', units.join(','), 'low']);
      await go(p, '#/appraisal?tool=rob2&target=o3', 'appraisal');
      await closeToasts(p);
      await p.click('#ap-inbox-btn');
      await p.waitForSelector('#ap-inbox li.item[data-path="' + b.path + '"]');
      await p.click('#ap-inbox li.item[data-path="' + b.path + '"] .ap-inbox-import');
      await waitToast(p, /importálva|Importálva|átvéve|Átvéve/);
      await go(p, '#/appraisal-summary?tool=rob2&outcome=o3', 'appraisal-summary');
      await p.click('#sync-preview');
      await p.waitForSelector('#sync-table, #sync-none');
      if (await p.$('#sync-table')) {
        await p.click('#sync-apply');
        await p.waitForSelector('.modal-backdrop');
        await p.click('.modal-backdrop .btn-primary, .modal-backdrop .btn-danger');
        await p.waitForSelector('#sync-applied', { timeout: 15000 });
      }
    }
    const mid = py(['audit', srv.proj, 'FINAL']);
    const left = (mid.findings || []).filter((f) => f.severity === 'error');
    // a rob-szinkron megváltoztatta az o3 tábláját → a futás elavult (X001): újrafuttatás a felületen
    if (left.some((f) => f.code === 'X001')) {
      for (const oc of Array.from(new Set(left.filter((f) => f.code === 'X001').map((f) => f.outcome)))) { await planCommit(p, o.sink, oc, {}, oc + ' (újra)'); }
    }
    const after = py(['audit', srv.proj, 'FINAL']);
    const errs2 = (after.findings || []).filter((f) => f.severity === 'error');
    check(errs2.length === 0, 'a javítások után nincs FINAL-hiba (' + JSON.stringify(errs2.map((f) => [f.code, f.outcome, String(f.detail).slice(0, 80)])) + ')');
    const g2 = await finalGateUI(p);
    check(!g2.disabled, 'a FINAL előzetes ellenőrzése tiszta (' + g2.pre.replace(/\s+/g, ' ').slice(0, 100) + ')');
    await p.fill('#log-gate-summary', 'Minden X-szabály tiszta; RoB, GRADE, SoF, kettős kinyerés lezárva.');
    await closeToasts(p);
    await p.click('#log-gate-submit');
    await p.waitForFunction(() => /FINAL/.test((document.getElementById('log-gate-result') || {}).textContent || ''), null, { timeout: 20000 });
    const cps = await api(p, 'GET', '/api/log/checkpoint');
    const fin = (cps.data.items || []).filter((c) => (c.stage_id || c.stage) === 'FINAL').pop();
    check(fin && fin.verdict === 'PASS', 'FINAL PASS rögzítve a projektnaplóban (#' + (fin && fin.id) + ')');
    await finishPage(o, 'FINAL');
  });
}

function replaySection() {
  return test('9. lánc-visszajátszás (tests/gui/test_chain_replay.py): a leképezett ellenőrzések 100%-a zöld', async () => {
    const r = spawnSync('python3', [path.join(ROOT, 'tests', 'gui', 'test_chain_replay.py')], { cwd: ROOT, encoding: 'utf-8', maxBuffer: 64 * 1024 * 1024 });
    const out = (r.stdout || '') + (r.stderr || '');
    const m = /Ran (\d+) tests?/.exec(out);
    check(r.status === 0 && m && /\nOK/.test(out), 'test_chain_replay: ' + (m ? m[1] + ' teszt' : '?') + ' — ' + (r.status === 0 ? 'OK' : 'HIBA'));
    const rep = fs.readFileSync(path.join(ROOT, 'tests', 'gui', 'chain_replay_coverage.md'), 'utf-8');
    const z = /— ebből zöld \| \| (\d+) \(([\d.]+)% a leképezettekből\)/.exec(rep);
    check(z && z[2] === '100.0', 'lefedettségi jelentés: a leképezett ellenőrzések ' + (z ? z[1] + ' db, ' + z[2] + '%' : '?') + ' zöld');
  });
}

// ================================================================ fő folyamat
(async () => {
  run('python3', [BUILD]);
  try { browser = await chromium.launch({ headless: true }); } catch (e) { browser = await chromium.launch({ headless: true, executablePath: CHROME_FALLBACK }); }
  const t0 = Date.now();
  try {
    if (want('appraisal')) {
      await appraisalRob2();
      await appraisalOthers();
      step('1. Értékelések: RoB 2 (két értékelő, κ, konszenzus, forgalmi lámpa, rob-szinkron), AI-vázlat', ['1a.', '1b.', '1c.']);
      step('1. Értékelések: ROBINS-I/E, QUADAS-2, NOS, TRIPOD+AI, AMSTAR 2, PROBAST+AI — motor + validator-doboz', ['1d.', '1e.']);
    }
    if (want('analysis')) {
      await analysisSection();
      step('1. Érzékenységi futás „magas RoB nélkül” → X003/X006 tiszta', ['1f.']);
    }
    if (want('grade')) {
      await gradeSection();
      step('2. GRADE (motor-tanács, X019-kapu, emberi bizonyosság) + SoF (két alapkockázat, CSV-őr, Markdown)', ['2a.', '2b.']);
    }
    if (want('dual')) {
      await dualSection();
      step('3. Kettős kinyerés A/B → összevetés → egyeztetés → konszenzus-CSV → elemzés', ['3.']);
    }
    if (want('plots')) {
      await plotsSection();
      step('4. Kumulatív és buborékábra a motor geometriájával', ['4.']);
    }
    if (want('composer')) {
      await composerSection();
      step('6. Composer-híd (teszt-csonk prisma-flow.json) → PRISMA', ['6.']);
    }
    if (want('hh')) {
      await hhSection();
      step('7. Metaheadhunter a felületről kazettákkal: keresés → … → PRISMA-ellenőrzés', ['7.']);
    }
    if (want('figures')) {
      await figuresSection();
      step('5. Ábra-export: motor-SVG hu/en, számhűség, figure-forge audit vagy teendő', ['5.']);
    }
    if (want('xrules')) {
      await xrulesSection();
      step('8. Audit: a teljes X-szabálykészlet egy felépített projekten; FINAL tilt', ['8a.']);
    }
    if (want('final')) {
      await finalSection();
      step('8. FINAL-kapu a végigvitt projekten: tilt, majd a javítás után átenged', ['8b.']);
    }
    if (want('replay')) {
      await replaySection();
      step('9. Lánc-visszajátszás változatlanul 100% a leképezhetőkre', ['9.']);
    }
  } finally {
    if (MAIN) { await MAIN.stop(); }
    await browser.close();
    if (process.env.E2E_V1_KEEP !== '1') { TMPS.forEach((d) => { try { fs.rmSync(d, { recursive: true, force: true }); } catch (e) { /* marad */ } }); }
  }
  // ellenőrzőlista
  console.log('\nElfogadási ellenőrzőlista (terv 9.3):');
  CHECKLIST.forEach((s) => {
    const rs = results.filter((r) => s.tests.some((tn) => r.test.indexOf(tn) === 0));
    const ok = rs.length > 0 && rs.every((r) => r.pass);
    console.log('  ' + (ok ? '✔' : '✖') + ' ' + s.label + ' (' + rs.filter((r) => r.pass).length + '/' + rs.length + ')');
  });
  const fails = results.filter((r) => !r.pass);
  console.log('\nidő: ' + Math.round((Date.now() - t0) / 1000) + ' s');
  console.log((results.length - fails.length) + '/' + results.length + ' ellenőrzés zöld');
  if (fails.length) {
    fails.forEach((f) => console.log('  ✖ [' + f.test + '] ' + f.msg));
    process.exit(1);
  }
})().catch((e) => { console.error(e); process.exit(1); });
