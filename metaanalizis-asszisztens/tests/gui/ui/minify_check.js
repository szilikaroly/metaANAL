#!/usr/bin/env node
/* tests/gui/ui/minify_check.js — a termék-build JS-tömörítésének (build_gui.py minify_js) ellenőrzője.
 *
 * Bemenet (stdin): JSON-tömb [{name, orig, min}]. Mindkét forrást espree-vel (ES2019, classic script)
 * elemzi, a helyadatokat (range/loc/start/end) elhagyja, és az AST-ket összeveti. Engedett eltérések:
 *   • a nem számított, idézőjeles, azonosító-alakú objektumkulcs ({'class': x} → {class: x});
 *   • névtelen függvénykifejezés → blokktörzsű nyílfüggvény, DE csak ha a jelentés azonos: a törzsben
 *     (beágyazva is) nincs this / arguments / super / new.target / yield / await, nem konstruktorhívás
 *     operandusa, és a modulban minden 'new' beépített konstruktort hív (NEW_OK).
 * Kimenet: 'OK <n>' vagy az eltérések modulonként; kilépési kód 1.
 *
 * Futtatja: tests/gui/ui/test_minify.py (python3), ha van node és espree.
 */
'use strict';

const fs = require('fs');

function loadEspree() {
  const cands = ['espree', '/opt/node-tools/node_modules/espree', '/opt/node22/lib/node_modules/eslint/node_modules/espree'];
  for (const c of cands) {
    try { return require(c); } catch (e) { /* következő */ }
  }
  return null;
}

const espree = loadEspree();
if (!espree) { console.log('SKIP espree nem található'); process.exit(3); }

const SKIP = new Set(['range', 'loc', 'start', 'end']);
// build_gui.py _NEW_OK — a nyílfüggvényre alakítás csak akkor engedett, ha 'new' után csak ezek állnak
const NEW_OK = new Set(['Error', 'TypeError', 'RangeError', 'SyntaxError', 'Promise', 'Date', 'RegExp', 'Array',
  'Object', 'Map', 'Set', 'WeakMap', 'WeakSet', 'ArrayBuffer', 'DataView', 'Uint8Array', 'Uint16Array', 'Uint32Array',
  'Int32Array', 'Float64Array', 'URL', 'URLSearchParams', 'AbortController', 'BroadcastChannel', 'Blob', 'File',
  'FileReader', 'FormData', 'Headers', 'Request', 'Response', 'Event', 'CustomEvent', 'KeyboardEvent', 'MouseEvent',
  'FocusEvent', 'ClipboardEvent', 'DataTransfer', 'MutationObserver', 'ResizeObserver', 'IntersectionObserver',
  'TextEncoder', 'TextDecoder', 'Image']);

function norm(n) {
  if (Array.isArray(n)) { return n.map(norm); }
  if (!n || typeof n !== 'object') { return n; }
  if (n.type === 'Property' && !n.computed && n.key && n.key.type === 'Literal' && typeof n.key.value === 'string' &&
      /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(n.key.value)) {
    n = Object.assign({}, n, { key: { type: 'Identifier', name: n.key.value } });
  }
  if (n.type === 'ArrowFunctionExpression' && n.body && n.body.type === 'BlockStatement') {
    n = Object.assign({}, n, { type: 'FunctionExpression' });
  }
  const o = {};
  for (const k of Object.keys(n).sort()) {
    if (!SKIP.has(k)) { o[k] = norm(n[k]); }
  }
  return o;
}

function walk(n, fn, parent) {
  if (Array.isArray(n)) { n.forEach((c) => walk(c, fn, parent)); return; }
  if (!n || typeof n !== 'object' || typeof n.type !== 'string') { return; }
  fn(n, parent);
  for (const k of Object.keys(n)) {
    if (!SKIP.has(k) && n[k] && typeof n[k] === 'object') { walk(n[k], fn, n); }
  }
}

function lexicalUses(fnNode) {
  const bad = [];
  walk(fnNode.body, (n) => {
    if (n.type === 'ThisExpression' || n.type === 'Super' || n.type === 'MetaProperty' ||
        n.type === 'YieldExpression' || n.type === 'AwaitExpression' ||
        (n.type === 'Identifier' && n.name === 'arguments')) { bad.push(n.type + (n.name ? ':' + n.name : '')); }
  });
  fnNode.params.forEach((p) => walk(p, (n) => { if (n.type === 'Identifier' && n.name === 'arguments') { bad.push('arguments'); } }));
  return bad;
}

/** A párhuzamos bejárás: minden függvénykifejezés → nyílfüggvény átalakítás jelentésmegőrző-e. */
function arrowProblems(orig, min) {
  const problems = [];
  let converted = 0;
  (function pair(a, b, parentB) {
    if (Array.isArray(a)) { a.forEach((x, i) => pair(x, b[i], parentB)); return; }
    if (!a || typeof a !== 'object') { return; }
    if (a.type === 'FunctionExpression' && b.type === 'ArrowFunctionExpression') {
      converted++;
      const bad = lexicalUses(a);
      if (a.id) { bad.push('névvel ellátott függvény'); }
      if (parentB && parentB.type === 'NewExpression' && parentB.callee === b) { bad.push('konstruktorhívás operandusa'); }
      if (bad.length) { problems.push('nyílfüggvény @' + a.start + ': ' + bad.join(', ')); }
    }
    for (const k of Object.keys(a)) {
      if (!SKIP.has(k) && a[k] && typeof a[k] === 'object') { pair(a[k], b[k], b); }
    }
  })(orig, min, null);
  if (converted) {
    walk(min, (n) => {
      if (n.type !== 'NewExpression') { return; }
      const c = n.callee;
      const ok = (c.type === 'Identifier' && NEW_OK.has(c.name)) ||
        (c.type === 'MemberExpression' && !c.computed && c.object.type === 'Identifier' && c.object.name === 'window' && NEW_OK.has(c.property.name));
      if (!ok) { problems.push('a modulban saját konstruktor is van (new @' + n.start + '), mégis van nyílfüggvényre alakítás'); }
    });
  }
  return problems;
}

function parse(text) {
  return espree.parse(text, { ecmaVersion: 2019, sourceType: 'script' });
}

const items = JSON.parse(fs.readFileSync(0, 'utf-8'));
let bad = 0;
for (const it of items) {
  let a, b;
  try { a = JSON.stringify(norm(parse(it.orig))); } catch (e) { console.log('ORIG-PARSE ' + it.name + ': ' + e.message); bad++; continue; }
  try { b = JSON.stringify(norm(parse(it.min))); } catch (e) { console.log('MIN-PARSE ' + it.name + ': ' + e.message); bad++; continue; }
  if (a !== b) {
    let i = 0;
    while (i < a.length && a[i] === b[i]) { i++; }
    console.log('DIFF ' + it.name + ' @' + i + '\n  orig: ' + a.slice(Math.max(0, i - 160), i + 160) +
      '\n  min:  ' + b.slice(Math.max(0, i - 160), i + 160));
    bad++;
    continue;
  }
  const probs = arrowProblems(parse(it.orig), parse(it.min));
  if (probs.length) {
    console.log('ARROW ' + it.name + ':\n  ' + probs.slice(0, 10).join('\n  '));
    bad++;
  }
}
console.log(bad ? 'FAIL ' + bad : 'OK ' + items.length);
process.exit(bad ? 1 : 0);
