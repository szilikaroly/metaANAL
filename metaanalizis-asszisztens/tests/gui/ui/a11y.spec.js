#!/usr/bin/env node
/* tests/gui/ui/a11y.spec.js — akadálymentességi bejárás CSAK billentyűzettel, minden MVP-képernyőn
 * (terv 3.5 „billentyűzettel teljesen kezelhető; a szín sosem egyedüli jelölés”, 8.6 akadálymentesség).
 *
 * Futtatás:  node tests/gui/ui/a11y.spec.js       (kilépési kód 1, ha bármi elbukik)
 *   MA_UI_DEV_HTML=<út>  — kész dev-build használata (a build kimarad).
 *
 * A dev-buildet ?fixtures=1-gyel nyitja (állapottartó fixture-háttér) szigorú CSP mellett, és egér nélkül:
 *   1. a képernyők között Alt+0…8-cal, a fül nélküli képernyőkre (projekt, képességek, vizsgálatok,
 *      eredmények) Tab-bejárással + Enterrel jut el; képernyőváltáskor a fókusz a #screen-title-re kerül;
 *   2. minden képernyőn beépített (axe-szerű, külső könyvtár nélküli) DOM-ellenőrzést futtat: akadálymentes
 *      név minden interaktív elemen és dialóguson, egyedi id-k, feloldható aria-hivatkozások, nincs pozitív
 *      tabindex, nincs fókuszálható elem aria-hidden alatt, grid/tablist szerkezet, aria-invalid mellett
 *      leírás, egyetlen <h1>, címszint-ugrás nélkül, tájékozódási pontok, és WCAG 2.1 AA kontraszt
 *      (szöveg 4,5:1, nagy szöveg 3:1) világos ÉS sötét témában;
 *   3. Tab-bejárás: minden fókuszpont látható, látható fókuszjelzést kap (outline vagy box-shadow), a fókusz
 *      halad (nincs csapda), és nem kerül rejtett / inert tartalomra;
 *   4. modális és nem modális ablakok billentyűzettel: átváltó (fókuszcsapda, Esc, fókusz vissza), KB-kereső
 *      (Ctrl+K), „Miért?” buborék, ábra-jelölők nyilakkal; nyelvváltás után is van név minden elemen.
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

// Az MVP képernyői (terv 9.2) és a v1 helyőrzői. how: 'alt' = Alt+szám; 'tab' = Tab-bejárás a megadott linkig.
const SCREENS = [
  { id: 'overview', how: 'alt', key: 'Alt+0' },
  { id: 'project', how: 'tab', href: '#/project', via: 'menu' },
  { id: 'extraction', how: 'alt', key: 'Alt+3', ready: '[role="grid"]' },
  { id: 'analysis', how: 'alt', key: 'Alt+4', ready: '.opt-grid, .plan-banner, .empty-state' },
  { id: 'results', how: 'tab', href: '#/results' },
  { id: 'prisma', how: 'alt', key: 'Alt+2', ready: '.pf-svg, .pf-box' },
  { id: 'studies', how: 'tab', href: '#/studies' },
  { id: 'log', how: 'alt', key: 'Alt+7', ready: '[role="tablist"]' },
  { id: 'capabilities', how: 'tab', href: '#/capabilities' },
  { id: 'export', how: 'alt', key: 'Alt+8' },
  { id: 'protocol', how: 'alt', key: 'Alt+1', placeholder: true },
  { id: 'appraisal', how: 'alt', key: 'Alt+5', placeholder: true },
  { id: 'grade', how: 'alt', key: 'Alt+6', placeholder: true }
];

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

// ---------------------------------------------------------------- az oldalon futó ellenőrzés (axe-szerű)
/* A böngészőben fut (page.evaluate). Visszaad: {violations: [{rule, el, msg}], stats}. */
function auditInPage(opts) {
  const V = [];
  const doc = document;
  const desc = (el) => {
    if (!el || !el.tagName) { return String(el); }
    let s = el.tagName.toLowerCase();
    if (el.id) { s += '#' + el.id; }
    const cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 3);
    if (cls.length) { s += '.' + cls.join('.'); }
    const r = el.getAttribute('role');
    if (r) { s += '[role=' + r + ']'; }
    const txt = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    return s + (txt ? ' «' + txt + '»' : '');
  };
  const add = (rule, el, msg) => { if (V.length < 400) { V.push({ rule, el: desc(el), msg: msg || '' }); } };
  const scope = doc.body;

  function hiddenByAncestor(el) {
    for (let e = el; e && e !== doc.documentElement; e = e.parentElement) {
      if (e.hidden || e.getAttribute('aria-hidden') === 'true' || e.inert) { return true; }
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') { return true; }
    }
    return false;
  }
  function rendered(el) {
    if (hiddenByAncestor(el)) { return false; }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function srOnly(el) {
    for (let e = el; e && e !== doc.body; e = e.parentElement) {
      if (e.classList && e.classList.contains('sr-only')) { return true; }
    }
    return false;
  }
  function textOf(node) {
    // a szöveg aria-hidden leszármazottak nélkül (akadálymentes név tartalomból)
    if (node.nodeType === 3) { return node.nodeValue; }
    if (node.nodeType !== 1) { return ''; }
    if (node.getAttribute('aria-hidden') === 'true' || node.hidden) { return ''; }
    if (node.tagName === 'STYLE' || node.tagName === 'SCRIPT') { return ''; }
    if (node.tagName === 'INPUT' || node.tagName === 'SELECT' || node.tagName === 'TEXTAREA') { return node.value || ''; }
    let s = '';
    if (node.getAttribute('aria-label')) { return node.getAttribute('aria-label'); }
    if (node.tagName.toLowerCase() === 'title' && node.namespaceURI === 'http://www.w3.org/2000/svg') { return node.textContent; }
    node.childNodes.forEach((c) => { s += textOf(c); });
    return s;
  }
  function byIds(list) {
    return (list || '').split(/\s+/).filter(Boolean).map((id) => doc.getElementById(id));
  }
  function accName(el) {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = byIds(lb).filter(Boolean).map((e) => textOf(e)).join(' ').trim();
      if (t) { return t; }
    }
    const al = (el.getAttribute('aria-label') || '').trim();
    if (al) { return al; }
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea' || tag === 'meter' || tag === 'progress') {
      if (el.id) {
        const l = doc.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (l && textOf(l).trim()) { return textOf(l).trim(); }
      }
      const wrap = el.closest('label');
      if (wrap && textOf(wrap).trim()) { return textOf(wrap).trim(); }
      if (tag === 'input' && (el.type === 'button' || el.type === 'submit' || el.type === 'reset') && el.value) { return el.value; }
      if (el.getAttribute('title')) { return el.getAttribute('title'); }
      if (el.getAttribute('placeholder')) { return el.getAttribute('placeholder'); }
      return '';
    }
    if (tag === 'svg' || el.namespaceURI === 'http://www.w3.org/2000/svg') {
      const t = el.querySelector(':scope > title');
      if (t && t.textContent.trim()) { return t.textContent.trim(); }
    }
    if (tag === 'fieldset') {
      const lg = el.querySelector(':scope > legend');
      if (lg && textOf(lg).trim()) { return textOf(lg).trim(); }
    }
    const nameFromContent = /^(button|a|summary|option|th|td|label|legend|h[1-6])$/.test(tag) ||
      /^(button|link|tab|menuitem|menuitemradio|menuitemcheckbox|option|checkbox|radio|switch|treeitem|gridcell|columnheader|rowheader|cell|row|tooltip|heading)$/.test(el.getAttribute('role') || '');
    if (nameFromContent) {
      const t = textOf(el).trim();
      if (t) { return t; }
    }
    return (el.getAttribute('title') || '').trim();
  }

  const all = Array.from(scope.querySelectorAll('*'));

  // 1. egyedi id-k; feloldható aria-hivatkozások
  const seen = {};
  all.forEach((el) => {
    if (el.id) {
      if (seen[el.id]) { add('duplicate-id', el, 'ismétlődő id: ' + el.id); }
      seen[el.id] = true;
    }
    ['aria-labelledby', 'aria-describedby', 'aria-controls', 'aria-owns', 'aria-errormessage', 'aria-activedescendant'].forEach((a) => {
      const v = el.getAttribute(a);
      if (v === null) { return; }
      const ids = v.split(/\s+/).filter(Boolean);
      if (!ids.length) { add('aria-ref', el, a + ' üres'); }
      ids.forEach((id) => { if (!doc.getElementById(id)) { add('aria-ref', el, a + ' → nincs ilyen id: ' + id); } });
    });
    const ti = el.getAttribute('tabindex');
    if (ti !== null && Number(ti) > 0) { add('tabindex-positive', el, 'tabindex=' + ti); }
  });

  // 2. interaktív elemek neve; fókuszálható elem nem lehet aria-hidden alatt
  const INTERACTIVE = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, [tabindex], ' +
    '[role="button"], [role="link"], [role="tab"], [role="checkbox"], [role="radio"], [role="switch"], [role="menuitem"], ' +
    '[role="option"], [role="slider"], [role="spinbutton"], [role="textbox"], [role="combobox"], [role="searchbox"], [contenteditable="true"]';
  const NO_NAME_NEEDED = /^(gridcell|row|rowgroup|cell|grid|table|listbox|region|group|presentation|none|document|application|dialog|alertdialog|tabpanel|toolbar|list|listitem|log|status|alert|main|navigation|banner|complementary|contentinfo|form|search|article|figure|img|separator|tooltip|columnheader|rowheader)$/;
  let interactive = 0;
  all.forEach((el) => {
    if (!el.matches(INTERACTIVE)) { return; }
    const ti = el.getAttribute('tabindex');
    const focusable = !el.disabled && (ti === null || Number(ti) >= 0);
    if (!rendered(el) && !srOnly(el)) {
      // rejtett elem: csak az a hiba, ha aria-hidden alatt fókuszálható maradt (és nem display:none)
      for (let e = el; e && e !== doc.body; e = e.parentElement) {
        if (e.getAttribute('aria-hidden') === 'true' && focusable && getComputedStyle(el).display !== 'none' &&
            getComputedStyle(el).visibility !== 'hidden' && !el.closest('[hidden]') && !el.closest('[inert]')) {
          add('focusable-in-aria-hidden', el, 'fókuszálható elem aria-hidden alatt');
          break;
        }
      }
      return;
    }
    if (ti !== null && Number(ti) < 0 && !el.matches('a[href], button, input, select, textarea, [role]')) { return; }
    const role = el.getAttribute('role') || '';
    if (role && NO_NAME_NEEDED.test(role) && !el.matches('a[href], button, input, select, textarea')) { return; }
    interactive++;
    if (!accName(el).trim()) { add('name', el, 'interaktív elemnek nincs akadálymentes neve'); }
  });

  // 3. dialógusok, rácsok, fülsorok, képek, aria-invalid
  scope.querySelectorAll('[role="dialog"], [role="alertdialog"], dialog').forEach((el) => {
    if (!rendered(el)) { return; }
    if (!accName(el).trim()) { add('dialog-name', el, 'dialógusnak nincs neve'); }
  });
  scope.querySelectorAll('[role="grid"], [role="treegrid"]').forEach((el) => {
    if (!rendered(el)) { return; }
    if (!accName(el).trim()) { add('grid-name', el, 'rácsnak nincs neve'); }
    if (!el.querySelector('[role="row"]')) { add('grid-structure', el, 'a rácsban nincs role=row'); }
    el.querySelectorAll('[role="row"]').forEach((row) => {
      if (!row.querySelector('[role="gridcell"], [role="columnheader"], [role="rowheader"]')) { add('grid-structure', row, 'sor cellák nélkül'); }
    });
  });
  scope.querySelectorAll('[role="tablist"]').forEach((tl) => {
    if (!rendered(tl)) { return; }
    const tabs = Array.from(tl.querySelectorAll('[role="tab"]'));
    if (!tabs.length) { add('tablist', tl, 'fülsor fülek nélkül'); }
    const sel = tabs.filter((t) => t.getAttribute('aria-selected') === 'true');
    if (tabs.length && sel.length !== 1) { add('tablist', tl, 'pontosan egy aria-selected=true fül kell (' + sel.length + ')'); }
    tabs.forEach((t) => { if (t.getAttribute('aria-selected') === null) { add('tablist', t, 'a fülről hiányzik az aria-selected'); } });
  });
  scope.querySelectorAll('[role="img"], svg').forEach((el) => {
    if (!rendered(el) || hiddenByAncestor(el)) { return; }
    if (el.tagName.toLowerCase() === 'svg' && el.closest('svg') !== el) { return; }
    if (el.tagName.toLowerCase() === 'svg' && !el.getAttribute('role') && !el.getAttribute('aria-label') && !el.querySelector(':scope > title')) {
      // díszítő SVG: legyen aria-hidden; az interaktív ábrák role=img|group + név
      if (el.getAttribute('aria-hidden') !== 'true' && el.getAttribute('focusable') !== 'false') { add('svg-name', el, 'SVG név vagy aria-hidden nélkül'); }
      return;
    }
    if ((el.getAttribute('role') === 'img' || el.getAttribute('role') === 'group' || el.getAttribute('role') === 'figure') && !accName(el).trim()) {
      add('img-name', el, 'képnek/ábrának nincs neve');
    }
  });
  scope.querySelectorAll('[aria-invalid="true"]').forEach((el) => {
    if (!rendered(el)) { return; }
    const d = byIds(el.getAttribute('aria-describedby')).concat(byIds(el.getAttribute('aria-errormessage'))).filter(Boolean);
    if (!d.some((x) => (x.textContent || '').trim())) { add('invalid-desc', el, 'aria-invalid=true leírás (aria-describedby) nélkül'); }
  });
  scope.querySelectorAll('img').forEach((el) => { if (el.getAttribute('alt') === null) { add('img-alt', el, 'img alt nélkül'); } });

  // 4. címsorok és tájékozódási pontok
  const modal = !!doc.querySelector('[role="dialog"][aria-modal="true"]');
  const h1 = Array.from(scope.querySelectorAll('h1')).filter(rendered);
  if (!modal && h1.length !== 1) { add('h1', h1[1] || scope, 'pontosan egy látható <h1> kell (' + h1.length + ')'); }
  let last = 1;
  Array.from(scope.querySelectorAll('h1, h2, h3, h4, h5, h6')).filter((h) => rendered(h) && !h.closest('[role="dialog"], .why-pop, .toast')).forEach((h) => {
    const lv = Number(h.tagName[1]);
    if (lv > last + 1) { add('heading-order', h, 'címszint-ugrás: h' + last + ' → h' + lv); }
    last = lv;
  });
  if (!modal && scope.querySelectorAll('main, [role="main"]').length !== 1) { add('landmark', scope, 'pontosan egy <main> kell'); }
  if (!modal && !scope.querySelector('nav, [role="navigation"]')) { add('landmark', scope, 'nincs <nav>'); }
  if (!doc.documentElement.getAttribute('lang')) { add('lang', doc.documentElement, 'nincs <html lang>'); }
  if (!scope.querySelector('[aria-live], [role="status"], [role="alert"], [role="log"]')) { add('live-region', scope, 'nincs élő régió (toast/állapot)'); }

  // 5. kontraszt (WCAG 2.1 AA): szöveg 4,5:1, nagy szöveg (≥ 24 px vagy ≥ 18,66 px félkövér) 3:1
  function parse(c) {
    const m = /rgba?\(([^)]+)\)/.exec(c || '');
    if (!m) { return null; }
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  }
  function over(fg, bg) { const a = fg[3]; return [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1]; }
  function lum(c) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  }
  function ratio(a, b) { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  function bgOf(el) {
    const stack = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') { return null; }   // gradiens/kép: nem mérhető
      const c = parse(cs.backgroundColor);
      if (c && c[3] > 0) { stack.push(c); if (c[3] >= 1) { break; } }
      if (e.namespaceURI === 'http://www.w3.org/2000/svg' && e.tagName.toLowerCase() !== 'svg') {
        // SVG-n belül a kitöltött téglalapok nem „háttér” CSS-értelemben: a súgó (pl.) saját rect-tel
        const rectBg = e.querySelector(':scope > rect.pl-tip-bg');
        if (rectBg) { const fc = parse(getComputedStyle(rectBg).fill); if (fc) { stack.push(fc); if (fc[3] >= 1) { break; } } }
      }
    }
    let bg = [255, 255, 255, 1];
    const root = parse(getComputedStyle(doc.documentElement).backgroundColor);
    if (root && root[3] > 0) { bg = over(root, bg); }
    const body = parse(getComputedStyle(doc.body).backgroundColor);
    if (body && body[3] > 0) { bg = over(body, bg); }
    for (let i = stack.length - 1; i >= 0; i--) { bg = over(stack[i], bg); }
    return bg;
  }
  let measured = 0;
  const seenEl = new Set();
  const walker = doc.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const tn = walker.currentNode;
    if (!tn.nodeValue.trim()) { continue; }
    const el = tn.parentElement;
    if (!el || seenEl.has(el)) { continue; }
    seenEl.add(el);
    if (!rendered(el) || srOnly(el) || el.closest('option, script, style, noscript, title, [aria-hidden="true"], .placeholder-hidden')) { continue; }
    if (el.closest(':disabled, [aria-disabled="true"]')) { continue; }          // WCAG 1.4.3: letiltott vezérlő kivétel
    const cs = getComputedStyle(el);
    if (Number(cs.opacity) === 0) { continue; }
    const isSvg = el.namespaceURI === 'http://www.w3.org/2000/svg';
    let fg = parse(isSvg ? cs.fill : cs.color);
    if (!fg) { continue; }
    const bg = bgOf(el);
    if (!bg) { continue; }
    let op = 1;
    for (let e = el; e; e = e.parentElement) { op *= Number(getComputedStyle(e).opacity); }
    fg = over([fg[0], fg[1], fg[2], fg[3] * op], bg);
    const px = parseFloat(cs.fontSize);
    const bold = Number(cs.fontWeight) >= 700;
    const large = px >= 24 || (px >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    const r = ratio(fg, bg);
    measured++;
    if (r + 0.005 < need) {
      add('contrast', el, 'kontraszt ' + r.toFixed(2) + ':1 < ' + need + ':1 (szín ' + fg.slice(0, 3).map(Math.round).join(',') + ' / háttér ' + bg.slice(0, 3).map(Math.round).join(',') + ', ' + px + 'px)');
    }
  }
  return { violations: V, stats: { interactive, measured, theme: doc.documentElement.getAttribute('data-theme'), opts } };
}

// ---------------------------------------------------------------- segédek
let browser, srv;
async function openPage(hash, opts) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: (opts && opts.scheme) || 'light' });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') { errors.push('console: ' + m.text()); } });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => { errors.push('dialog: ' + d.message()); d.dismiss().catch(() => null); });
  await page.goto(srv.base + '/?fixtures=1' + (hash || ''));
  await page.waitForSelector('html[data-ready="1"]', { timeout: 10000 });
  return { ctx, page, errors };
}
async function screenReady(page, s) {
  try {
    await page.waitForFunction((x) => { const r = document.getElementById('screen-root'); return r && r.dataset.screen === x && !r.querySelector('.spinner'); }, s.id, { timeout: 8000 });
    if (s.ready) { await page.waitForSelector(s.ready, { timeout: 8000 }); }
  } catch (e) {
    const st = await page.evaluate(() => ({ hash: location.hash, screen: (document.getElementById('screen-root') || {}).dataset,
      dialog: (document.querySelector('[role="dialog"]') || {}).textContent, active: document.activeElement && document.activeElement.outerHTML.slice(0, 120) }));
    throw new Error('a(z) ' + s.id + ' képernyő nem állt be: ' + JSON.stringify(st));
  }
  await page.waitForTimeout(150);
}
async function activeInfo(page) {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) { return { body: true }; }
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    let hiddenAnc = false;
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      if (e.getAttribute('aria-hidden') === 'true' || e.inert || e.hidden) { hiddenAnc = true; break; }
    }
    if (!el.dataset.a11yId) { el.dataset.a11yId = String(Math.random()).slice(2); }
    const ow = parseFloat(cs.outlineWidth) || 0;
    let indicator = (cs.outlineStyle !== 'none' && ow > 0) || (cs.boxShadow && cs.boxShadow !== 'none');
    if (!indicator) {
      // tartalék: a fókusz más látható jelzése (SVG-jelölőnél pl. a sorháttér körvonala) — a részfa
      // számított stílusa fókuszban és fókusz nélkül eltér-e
      const sig = () => [el].concat(Array.from(el.querySelectorAll('*')).slice(0, 40)).map((x) => {
        const c = getComputedStyle(x);
        return [c.outlineStyle, c.boxShadow, c.backgroundColor, c.borderColor, c.color, c.stroke, c.strokeWidth, c.fill, c.textDecorationLine].join('|');
      }).join(';');
      const on = sig();
      el.blur();
      const off = sig();
      el.focus({ preventScroll: true });
      indicator = on !== off;
    }
    return {
      id: el.dataset.a11yId,
      tag: el.tagName.toLowerCase(), elId: el.id, cls: el.getAttribute('class') || '', role: el.getAttribute('role') || '',
      href: el.getAttribute('href'),
      text: (el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40),
      w: r.width, h: r.height,
      inView: r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth,
      indicator,
      hiddenAnc,
      inDialog: !!el.closest('[role="dialog"][aria-modal="true"]')
    };
  });
}
function label(a) { return a.body ? '<body>' : a.tag + (a.elId ? '#' + a.elId : '') + (a.role ? '[' + a.role + ']' : '') + ' «' + a.text + '»'; }

/** Tab-ot nyom, amíg pred(info) igaz nem lesz (legfeljebb max lépés); visszaadja a talált elem adatait vagy null-t. */
async function tabUntil(page, pred, max, shift) {
  for (let i = 0; i < (max || 120); i++) {
    await page.keyboard.press(shift ? 'Shift+Tab' : 'Tab');
    const a = await activeInfo(page);
    if (!a.body && pred(a)) { return a; }
  }
  return null;
}

/** Tab-bejárás a képernyőn: fókuszjelzés, láthatóság, haladás. */
async function tabWalk(page, where, stops) {
  await page.focus('#screen-title');
  const seen = [];
  const problems = [];
  let prev = null;
  let repeats = 0;
  for (let i = 0; i < stops; i++) {
    await page.keyboard.press('Tab');
    const a = await activeInfo(page);
    if (a.body) { break; }                                           // a dokumentum vége (a böngésző felülete jönne)
    if (prev && a.id === prev.id) { repeats++; if (repeats > 1) { problems.push('a fókusz nem halad: ' + label(a)); break; } } else { repeats = 0; }
    if (!a.indicator) { problems.push('nincs látható fókuszjelzés: ' + label(a)); }
    if (a.w < 1 || a.h < 1) { problems.push('láthatatlan fókuszpont: ' + label(a)); }
    if (!a.inView) { problems.push('a fókuszpont nem gördült a nézetbe: ' + label(a)); }
    if (a.hiddenAnc) { problems.push('rejtett (aria-hidden/inert) tartalomra került a fókusz: ' + label(a)); }
    if (seen.length && a.id === seen[0].id) { break; }               // körbeért
    seen.push(a);
    prev = a;
  }
  const distinct = new Set(seen.map((x) => x.id)).size;
  check(problems.length === 0, where + ': Tab-bejárás (' + distinct + ' fókuszpont) — ' + (problems.slice(0, 8).join(' | ') || 'rendben'));
  return seen;
}

const STATS = [];
async function audit(page, where, opts) {
  const r = await page.evaluate(auditInPage, opts || {});
  STATS.push({ where, interactive: r.stats.interactive, measured: r.stats.measured, violations: r.violations.length });
  const v = r.violations;
  const byRule = {};
  v.forEach((x) => { (byRule[x.rule] = byRule[x.rule] || []).push(x.el + (x.msg ? ' — ' + x.msg : '')); });
  const summary = Object.keys(byRule).map((k) => k + ' (' + byRule[k].length + '): ' + byRule[k].slice(0, 4).join(' ‖ ')).join('\n      ');
  check(v.length === 0, where + ': DOM-ellenőrzés [' + r.stats.interactive + ' interaktív elem, ' + r.stats.measured + ' szövegkontraszt, téma ' + r.stats.theme + '] — ' + (summary || 'rendben'));
  return r;
}

/** Gépelés közben (szövegmező, select) az Alt+szám szándékosan nem vált képernyőt (macOS-en az Option+szám
 *  karaktert ír) — a felhasználó előbb Tab-bal kilép a mezőből; ezt teszi ez is. */
async function leaveTyping(page) {
  for (let i = 0; i < 120 && await page.evaluate(() => window.MA.dom.isTyping()); i++) { await page.keyboard.press('Tab'); }
}

/** Billentyűzettel a képernyőre: Alt+szám vagy Tab-bejárás a linkig (+ Enter). */
async function goByKeyboard(page, s) {
  if (s.how === 'alt') {
    await leaveTyping(page);
    await page.keyboard.press(s.key);
  } else if (s.via === 'menu') {
    // a fejléc felhasználói menüje (menü-gomb → menüpont)
    await page.focus('#screen-title');
    const btn = await tabUntil(page, (a) => a.tag === 'button' && /menu/.test(a.cls + ' ' + a.role) || (a.tag === 'button' && a.elId === 'user-menu'), 80, true);
    check(!!btn, s.id + ': a menü-gomb elérhető Tab-bal');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(100);
    let a = await activeInfo(page);
    for (let i = 0; i < 10 && a.href !== s.href; i++) { await page.keyboard.press('ArrowDown'); a = await activeInfo(page); }
    check(a.href === s.href, s.id + ': a menüpont elérhető nyilakkal');
    await page.keyboard.press('Enter');
  } else {
    await page.focus('#screen-title');
    let a = await tabUntil(page, (x) => (x.href || '').indexOf(s.href) === 0, 150);
    if (!a) { await page.focus('#screen-title'); a = await tabUntil(page, (x) => (x.href || '').indexOf(s.href) === 0, 150, true); }
    check(!!a, s.id + ': a(z) ' + s.href + ' link elérhető Tab-bal');
    await page.keyboard.press('Enter');
  }
  await screenReady(page, s);
  const focusOk = await page.evaluate(() => document.activeElement && document.activeElement.id === 'screen-title');
  check(focusOk, s.id + ': képernyőváltás után a fókusz a #screen-title-en');
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

  for (const theme of ['light', 'dark']) {
    await test('csak billentyűzettel minden MVP-képernyőn — ' + theme + ' téma: DOM-ellenőrzés, kontraszt, Tab-bejárás', async () => {
      const o = await openPage('#/overview', { scheme: theme });
      const p = o.page;
      await screenReady(p, SCREENS[0]);
      const cur = await p.evaluate(() => document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
      check(cur === theme || cur === 'auto', 'téma: ' + cur);
      // a skip-link az első Tab-megálló és a fő tartalomra visz
      await p.keyboard.press('Tab');
      const first = await activeInfo(p);
      check(/skip/.test(first.cls), 'az első Tab-megálló a „Ugrás a tartalomra” link (' + label(first) + ')');
      await p.keyboard.press('Enter');
      check(await p.evaluate(() => !!document.activeElement.closest('main')), 'a skip-link a <main>-be viszi a fókuszt');
      for (const s of SCREENS) {
        if (s.id !== 'overview') { await goByKeyboard(p, s); }
        await audit(p, s.id + ' [' + theme + ']');
        if (theme === 'light') { await tabWalk(p, s.id, s.placeholder ? 25 : 60); }
      }
      check(o.errors.length === 0, 'nincs konzolhiba: ' + o.errors.join(' || '));
      await o.ctx.close();
    });
  }

  await test('modális és nem modális ablakok billentyűzettel: átváltó, eredet-panel, KB-kereső, „Miért?”', async () => {
    const o = await openPage('#/extraction');
    const p = o.page;
    await screenReady(p, SCREENS[2]);
    // az átváltó gombja Tab-bal
    const opener = await tabUntil(p, (a) => a.tag === 'button' && /átváltó|convert/i.test(a.text), 200);
    check(!!opener, 'az átváltó gomb elérhető Tab-bal');
    if (opener) {
      await p.keyboard.press('Enter');
      await p.waitForSelector('[role="dialog"][aria-modal="true"]', { timeout: 5000 });
      await p.waitForTimeout(150);
      let a = await activeInfo(p);
      check(a.inDialog, 'a fókusz a modális ablakba kerül (' + label(a) + ')');
      await audit(p, 'átváltó (modális)');
      const ids = new Set();
      let trapped = true;
      for (let i = 0; i < 40; i++) {
        await p.keyboard.press('Tab');
        a = await activeInfo(p);
        if (!a.inDialog) { trapped = false; break; }
        ids.add(a.id);
        if (!a.indicator) { check(false, 'átváltó: fókuszjelzés hiányzik: ' + label(a)); }
      }
      check(trapped && ids.size > 2, 'fókuszcsapda: Tab a modálison belül marad (' + ids.size + ' elem)');
      for (let i = 0; i < 5; i++) { await p.keyboard.press('Shift+Tab'); a = await activeInfo(p); if (!a.inDialog) { trapped = false; } }
      check(trapped, 'fókuszcsapda: Shift+Tab is a modálison belül marad');
      await p.keyboard.press('Escape');
      await p.waitForFunction(() => !document.querySelector('[role="dialog"][aria-modal="true"]'));
      a = await activeInfo(p);
      check(a.id === opener.id, 'Esc után a fókusz visszakerül a nyitó gombra (' + label(a) + ')');
    }
    // eredet-panel Alt+P
    await p.keyboard.press('Alt+p');
    await p.waitForTimeout(150);
    await audit(p, 'kinyerés + eredet-panel (Alt+P)');
    // KB-kereső Ctrl+K bárhonnan
    await p.keyboard.press('Control+k');
    await p.waitForFunction(() => document.activeElement && document.activeElement.id === 'kb-q', null, { timeout: 5000 });
    await p.keyboard.type('heterogenitás');
    await p.waitForTimeout(700);
    await audit(p, 'KB-kereső (Ctrl+K)');
    // „Miért?” buborék az Áttekintésen (gépelés közben az Alt+szám szándékosan nem vált: előbb Esc / Shift+Tab)
    check(await p.evaluate(() => window.MA.dom.isTyping()), 'a keresőmezőben gépelés közben vagyunk');
    await p.keyboard.press('Alt+0');
    await p.waitForTimeout(200);
    check(await p.evaluate(() => document.getElementById('screen-root').dataset.screen === 'log'), 'gépelés közben az Alt+0 nem vált képernyőt');
    await leaveTyping(p);
    await p.keyboard.press('Alt+0');
    await screenReady(p, SCREENS[0]);
    const why = await tabUntil(p, (x) => /why-btn/.test(x.cls), 120);
    check(!!why, 'a „Miért?” gomb elérhető Tab-bal');
    if (why) {
      await p.keyboard.press('Enter');
      await p.waitForSelector('.why-pop[role="dialog"]');
      await p.waitForTimeout(300);
      check(await p.evaluate(() => !!document.activeElement.closest('.why-pop')), 'a fókusz a buborékba kerül');
      await audit(p, '„Miért?” buborék');
      await p.keyboard.press('Escape');
      const back = await activeInfo(p);
      check(back.id === why.id, 'Esc után a fókusz a „Miért?” gombon');
    }
    check(o.errors.length === 0, 'nincs konzolhiba: ' + o.errors.join(' || '));
    await o.ctx.close();
  });

  await test('ábrák billentyűzettel: jelölők nyilakkal, a fókuszált jelölőnek neve és fókuszjelzése van', async () => {
    const o = await openPage('#/results?outcome=o1');
    const p = o.page;
    await screenReady(p, { id: 'results', ready: '.fp-svg' });
    const mk = await tabUntil(p, (a) => /fp-study/.test(a.cls) || (a.role === 'img' && /fp/.test(a.cls)), 120);
    check(!!mk, 'a forest első jelölője elérhető Tab-bal');
    if (mk) {
      const names = [mk.text];
      for (let i = 0; i < 3; i++) {
        await p.keyboard.press('ArrowDown');
        const a = await activeInfo(p);
        names.push(a.text);
        check(a.indicator, 'a jelölőn látható fókuszjelzés (' + label(a) + ')');
      }
      check(new Set(names).size === 4 && names.every((x) => x), 'ArrowDown jelölőről jelölőre lép, mindegyiknek neve van (' + names.join(' | ') + ')');
      // lefúrás billentyűzettel: Enter a jelölőn → lefúrás-panel → „Ugrás a sorhoz” → a Kinyerés rácsában a sor cellája fókuszban
      const uid = await p.evaluate(() => document.activeElement.getAttribute('data-uid'));
      await p.keyboard.press('Enter');
      await p.waitForSelector('#drilldown .drill-jump', { timeout: 5000 });
      await audit(p, 'eredmények — lefúrás-panel');
      const jump = await tabUntil(p, (a) => /drill-jump/.test(a.cls), 60);
      check(!!jump, 'az „Ugrás a sorhoz” link elérhető Tab-bal');
      if (jump) {
        await p.keyboard.press('Enter');
        await screenReady(p, SCREENS[2]);
        await p.waitForFunction((u) => { const a = document.activeElement; const row = a && a.closest('[role="row"]'); return !!row && a.getAttribute('role') === 'gridcell' && (row.getAttribute('data-uid') === u || row.dataset.uid === u); }, uid, { timeout: 5000 })
          .then(() => check(true, 'lefúrás: a Kinyerés rácsában a(z) ' + uid + ' sor cellája fókuszban'))
          .catch(async () => check(false, 'lefúrás: a(z) ' + uid + ' sor cellája nincs fókuszban (' + label(await activeInfo(p)) + ')'));
        await p.keyboard.press('Alt+4');
        await screenReady(p, SCREENS[3]);
        await p.goto(srv.base + '/?fixtures=1#/results?outcome=o1');
        await p.waitForSelector('html[data-ready="1"]');
        await screenReady(p, { id: 'results', ready: '.fp-svg' });
      }
    }
    // a további ábrák fülei nyilakkal
    const tab = await tabUntil(p, (a) => a.role === 'tab', 120);
    check(!!tab, 'az ábra-fülek elérhetők Tab-bal');
    if (tab) {
      const seen = [tab.text];
      for (let i = 0; i < 6; i++) {
        await p.keyboard.press('ArrowRight');
        await p.keyboard.press('Enter');                          // kézi aktiválás is működjön
        await p.waitForTimeout(250);
        const a = await activeInfo(p);
        seen.push(a.text);
        const sel = await p.evaluate(() => { const t = document.activeElement; return t && t.getAttribute('aria-selected'); });
        check(sel === 'true', 'a fül aktív (aria-selected) Enter után: ' + label(a));
        await audit(p, 'eredmények — ' + a.text);
      }
      check(new Set(seen).size >= 6, 'nyilakkal végig a fülökön (' + seen.join(' | ') + ')');
    }
    check(o.errors.length === 0, 'nincs konzolhiba: ' + o.errors.join(' || '));
    await o.ctx.close();
  });

  await test('napló: fülek nyilakkal, minden fülpanel és az új bejegyzés űrlapja', async () => {
    const o = await openPage('#/log');
    const p = o.page;
    await screenReady(p, { id: 'log', ready: '[role="tablist"]' });
    const tab = await tabUntil(p, (a) => a.role === 'tab', 80);
    check(!!tab, 'a napló fülsora elérhető Tab-bal');
    if (tab) {
      const n = await p.evaluate(() => document.querySelectorAll('#screen-root [role="tablist"] [role="tab"]').length);
      const seen = [];
      for (let i = 0; i < n; i++) {
        const a = await activeInfo(p);
        seen.push(a.text);
        check(a.role === 'tab' && a.indicator, 'fül fókuszban, látható jelzéssel: ' + label(a));
        await p.keyboard.press('Enter');
        await p.waitForTimeout(250);
        await p.waitForFunction(() => !document.querySelector('#screen-root .spinner'), null, { timeout: 5000 }).catch(() => null);
        await audit(p, 'napló — ' + a.text);
        // vissza a fülre (a panel tartalma kaphatta a fókuszt), majd a következőre
        const back = await p.evaluate(() => { const t = document.querySelector('#screen-root [role="tab"][aria-selected="true"]'); return !!t && document.activeElement === t; });
        if (!back) { await tabUntil(p, (x) => x.role === 'tab' && x.text === a.text, 80, true); }
        await p.keyboard.press('ArrowRight');
      }
      check(new Set(seen).size === n, 'nyilakkal végig a ' + n + ' fülön (' + seen.join(' | ') + ')');
    }
    // vissza az első fülre (Home + Enter), majd az új megállapítás űrlapja billentyűzettel
    await tabUntil(p, (x) => x.role === 'tab', 80, true);
    await p.keyboard.press('Home');
    await p.keyboard.press('Enter');
    await p.waitForTimeout(250);
    const nb = await tabUntil(p, (a) => a.tag === 'button' && /új megállapítás|new finding/i.test(a.text), 120);
    check(!!nb, 'az „Új megállapítás” gomb elérhető Tab-bal');
    if (nb) {
      await p.keyboard.press('Enter');
      await p.waitForTimeout(300);
      await audit(p, 'napló — új bejegyzés űrlap');
    }
    check(o.errors.length === 0, 'nincs konzolhiba: ' + o.errors.join(' || '));
    await o.ctx.close();
  });

  await test('betöltési hiba (pl. hiányzó szerver-végpont): nem marad „Betöltés …” élő régió, a hiba szövegként látszik', async () => {
    const o = await openPage('#/overview');
    const p = o.page;
    await screenReady(p, SCREENS[0]);
    await p.evaluate(() => {
      const nf = () => ({ status: 404, envelope: { ok: false, error: { code: 'NOT_FOUND', http: 404, message: 'Nincs ilyen végpont.' } } });
      ['/api/prisma', '/api/studies', '/api/validate'].forEach((x) => window.MA.dev.fixtures.route(x === '/api/validate' ? 'POST' : 'GET', x, nf, { first: true }));
    });
    for (const s of [SCREENS[5], SCREENS[6], SCREENS[2]]) {
      await leaveTyping(p);
      await p.evaluate((x) => window.MA.app.navigate(x), s.id);
      await p.waitForFunction((x) => { const r = document.getElementById('screen-root'); return r && r.dataset.screen === x && r.querySelector('.error-box'); }, s.id, { timeout: 8000 });
      await p.waitForTimeout(600);
      const st = await p.evaluate(() => ({
        spinners: Array.from(document.querySelectorAll('#screen-root .spinner')).map((x) => x.textContent.trim()),
        busy: document.getElementById('main').getAttribute('aria-busy'),
        err: (document.querySelector('#screen-root .error-box') || {}).textContent || ''
      }));
      check(st.spinners.length === 0, s.id + ': nincs ott maradt töltésjelző (' + st.spinners.join(' | ') + ')');
      check(st.busy === null, s.id + ': a <main> nem marad aria-busy');
      check(/Nincs ilyen végpont/.test(st.err), s.id + ': a szerver üzenete a hibadobozban');
      await audit(p, s.id + ' — betöltési hiba');
    }
    await o.ctx.close();
  });

  await test('angol felület: minden MVP-képernyőn van név és nincs hiányzó kulcs', async () => {
    const o = await openPage('#/overview');
    const p = o.page;
    await screenReady(p, SCREENS[0]);
    await p.focus('#screen-title');
    const lang = await tabUntil(p, (a) => a.tag === 'button' && /^(EN|English)/.test(a.text) || (a.tag === 'button' && /lang/.test(a.cls) && /EN/.test(a.text)), 80, true);
    check(!!lang, 'a nyelvváltó elérhető Tab-bal');
    await p.keyboard.press('Enter');
    await p.waitForFunction(() => document.documentElement.lang === 'en');
    for (const s of SCREENS.filter((x) => x.how === 'alt' && !x.placeholder).slice(1).concat([SCREENS[0]])) {
      await goByKeyboard(p, s);
      await audit(p, s.id + ' [en]');
    }
    const missing = await p.evaluate(() => window.MA.i18n.missing());
    check(missing.length === 0, 'nincs hiányzó i18n-kulcs: ' + missing.join(', '));
    check(o.errors.length === 0, 'nincs konzolhiba: ' + o.errors.join(' || '));
    await o.ctx.close();
  });

  await browser.close();
  await srv.close();
  console.log('\nDOM-ellenőrzések (hely — interaktív elem / mért szövegkontraszt / sértés):');
  STATS.forEach((x) => console.log('  ' + x.where + ' — ' + x.interactive + ' / ' + x.measured + ' / ' + x.violations));
  const failed = results.filter((r) => !r.pass);
  console.log('\n' + (results.length - failed.length) + '/' + results.length + ' ellenőrzés zöld' + (failed.length ? ', ' + failed.length + ' HIBÁS' : ''));
  failed.forEach((f) => console.log('  ✖ [' + f.test + '] ' + f.msg));
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
