#!/usr/bin/env node
/* tests/gui/ui/run_all.js — a felület összes böngészős tesztje egymás után (terv 8.6, 2. réteg).
 *
 * Futtatás (bármely mappából):  node tests/gui/ui/run_all.js [--only név[,név…]] [--python] [--timeout mp]
 *   --only     csak a megnevezett spec(ek) (pl. --only shell,process; a '.spec.js' elhagyható)
 *   --python   előtte a böngésző nélküli Python-tesztek is (tests/gui/test_ui_static.py, tests/gui/ui/test_*.py)
 *   --timeout  specenkénti időkorlát másodpercben (alap: 900)
 *
 * Minden tests/gui/ui/*.spec.js külön node-folyamatban, egymás UTÁN fut (mindegyik maga építi a buildet —
 * párhuzamosan a dist/ fájljain versenyeznének). Kilépési kód: 0, ha minden zöld; 1, ha bármelyik spec vagy
 * Python-teszt elbukott, időtúllépett vagy nem indult el.
 */
'use strict';

const path = require('path');
const fs = require('fs');
const { spawnSync } = require('child_process');

const HERE = __dirname;
const ROOT = path.resolve(HERE, '..', '..', '..');

function parseArgs(argv) {
  const opts = { only: null, python: false, timeout: 900 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--only') { opts.only = (argv[++i] || '').split(',').map((s) => s.trim().replace(/\.spec\.js$/, '')).filter(Boolean); }
    else if (a === '--python') { opts.python = true; }
    else if (a === '--timeout') { opts.timeout = Number(argv[++i]) || opts.timeout; }
    else if (a === '-h' || a === '--help') { console.log(fs.readFileSync(__filename, 'utf-8').split('*/')[0]); process.exit(0); }
    else { console.error('ismeretlen kapcsoló: ' + a); process.exit(2); }
  }
  return opts;
}

function run(cmd, args, timeoutS) {
  const t0 = Date.now();
  const r = spawnSync(cmd, args, { cwd: ROOT, encoding: 'utf-8', timeout: timeoutS * 1000, maxBuffer: 64 * 1024 * 1024 });
  const out = (r.stdout || '') + (r.stderr || '');
  return { status: r.status, signal: r.signal, error: r.error, out, secs: Math.round((Date.now() - t0) / 1000) };
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const rows = [];

  if (opts.python) {
    const py = [path.join('tests', 'gui', 'test_ui_static.py')].concat(
      fs.readdirSync(HERE).filter((f) => /^test_.*\.py$/.test(f)).sort().map((f) => path.join('tests', 'gui', 'ui', f)));
    for (const f of py) {
      const r = run('python3', [f], opts.timeout);
      const m = /Ran (\d+) tests?/.exec(r.out);
      const ok = r.status === 0;
      rows.push({ name: f, ok, summary: (m ? m[1] + ' teszt' : '') + (ok ? ' OK' : ' HIBA'), secs: r.secs, out: r.out });
      console.log((ok ? '✔ ' : '✖ ') + f + ' — ' + rows[rows.length - 1].summary + ' (' + r.secs + ' s)');
    }
  }

  let specs = fs.readdirSync(HERE).filter((f) => f.endsWith('.spec.js')).sort();
  if (opts.only) {
    const unknown = opts.only.filter((n) => specs.indexOf(n + '.spec.js') < 0);
    if (unknown.length) { console.error('nincs ilyen spec: ' + unknown.join(', ')); process.exit(2); }
    specs = specs.filter((f) => opts.only.indexOf(f.replace(/\.spec\.js$/, '')) >= 0);
  }
  if (!specs.length) { console.error('nincs futtatható *.spec.js'); process.exit(1); }

  for (const f of specs) {
    console.log('▶ ' + f);
    const r = run(process.execPath, [path.join(HERE, f)], opts.timeout);
    const m = /(\d+)\/(\d+) ellenőrzés zöld/.exec(r.out);
    let why = '';
    if (r.error && r.error.code === 'ETIMEDOUT') { why = 'időtúllépés (' + opts.timeout + ' s)'; }
    else if (r.error) { why = 'nem indult: ' + r.error.message; }
    else if (r.status !== 0) { why = 'kilépési kód ' + r.status + (r.signal ? ' (' + r.signal + ')' : ''); }
    const ok = !why && !!m && m[1] === m[2];
    if (!why && !ok) { why = m ? (Number(m[2]) - Number(m[1])) + ' hibás ellenőrzés' : 'nincs összesítő sor'; }
    const summary = (m ? m[1] + '/' + m[2] + ' ellenőrzés' : '') + (ok ? ' zöld' : ' — ' + why);
    rows.push({ name: f, ok, summary, secs: r.secs, out: r.out });
    console.log((ok ? '✔ ' : '✖ ') + f + ' — ' + summary + ' (' + r.secs + ' s)');
    if (!ok) {
      const lines = r.out.split('\n');
      const fails = lines.filter((l) => /FAIL|✖|Error|hiba/i.test(l)).slice(0, 40);
      console.log('  ' + (fails.length ? fails : lines.slice(-40)).join('\n  '));
    }
  }

  const bad = rows.filter((r) => !r.ok);
  console.log('\n' + (rows.length - bad.length) + '/' + rows.length + ' tesztcsomag zöld' + (bad.length ? ' — HIBÁS: ' + bad.map((r) => r.name).join(', ') : ''));
  process.exit(bad.length ? 1 : 0);
}

main();
