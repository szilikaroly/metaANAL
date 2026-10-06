/* selftest.js — oldalon belüli önteszt (terv 8.6, 1. réteg): index.html?selftest=1
 *
 * MA.selftest.register(name, fn(t)) — fn szinkron vagy Promise; t.ok / t.eq / t.near / t.throws / t.deepEq.
 * Az eredmény a <pre id="selftest">-be kerül (soronként 'ok - …' / 'FAIL - …', végül
 * 'SELFTEST pass=N fail=M'), és a <html data-selftest="pass|fail"> attribútumba.
 * Fej nélküli futtatás: chrome --headless=new --virtual-time-budget=15000 --dump-dom "<url>?selftest=1#launch=<kód>"
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;

  var tests = [];
  var lastResult = null;

  /** register(name, fn) — képernyő- és ábra-modulok is regisztrálhatnak saját tesztet. */
  function register(name, fn) { tests.push({ name: String(name), fn: fn }); }

  function fmt(v) {
    try { return JSON.stringify(v); } catch (e) { return String(v); }
  }

  function makeT(results, name) {
    function record(pass, msg) { results.push({ test: name, pass: !!pass, msg: msg || '' }); return !!pass; }
    return {
      ok: function (cond, msg) { return record(!!cond, msg); },
      eq: function (a, b, msg) { return record(a === b, (msg || '') + (a === b ? '' : ' — kapott ' + fmt(a) + ', várt ' + fmt(b))); },
      deepEq: function (a, b, msg) { var s1 = fmt(a), s2 = fmt(b); return record(s1 === s2, (msg || '') + (s1 === s2 ? '' : ' — kapott ' + s1 + ', várt ' + s2)); },
      near: function (a, b, tol, msg) {
        var d = a - b;
        var pass = typeof a === 'number' && typeof b === 'number' && d <= tol && -d <= tol;
        return record(pass, (msg || '') + (pass ? '' : ' — kapott ' + fmt(a) + ', várt ' + fmt(b) + ' ± ' + tol));
      },
      throws: function (fn, msg) {
        var threw = false;
        try { fn(); } catch (e) { threw = true; }
        return record(threw, msg);
      }
    };
  }

  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  /** waitFor(pred, timeoutMs=3000) → Promise<bool> — aszinkron állításokhoz (pl. útvonalváltás). */
  function waitFor(pred, timeoutMs) {
    var limit = timeoutMs || 3000;
    var waited = 0;
    function loop() {
      var v = false;
      try { v = pred(); } catch (e) { v = false; }
      if (v) { return Promise.resolve(true); }
      if (waited >= limit) { return Promise.resolve(false); }
      waited += 25;
      return wait(25).then(loop);
    }
    return loop();
  }

  function output(results, show) {
    var pass = results.filter(function (r) { return r.pass; }).length;
    var fail = results.length - pass;
    var lines = results.map(function (r) { return (r.pass ? 'ok - ' : 'FAIL - ') + r.test + (r.msg ? ': ' + r.msg : ''); });
    lines.push('SELFTEST pass=' + pass + ' fail=' + fail);
    var pre = document.getElementById('selftest');
    if (!pre) {
      pre = h('pre', { id: 'selftest', 'class': 'selftest-output', 'aria-label': 'selftest' });
      document.body.appendChild(pre);
    }
    pre.hidden = !show;
    pre.textContent = lines.join('\n');
    document.documentElement.setAttribute('data-selftest', fail ? 'fail' : 'pass');
    return { pass: pass, fail: fail, results: results };
  }

  /** run({show}) → Promise<{pass, fail, results}> — az összes regisztrált teszt, sorban. */
  function run(opts) {
    var results = [];
    var chain = Promise.resolve();
    tests.forEach(function (tc) {
      chain = chain.then(function () {
        var t = makeT(results, tc.name);
        return Promise.resolve().then(function () { return tc.fn(t); }).catch(function (e) {
          results.push({ test: tc.name, pass: false, msg: 'kivétel: ' + (e && e.message ? e.message : String(e)) });
        });
      });
    });
    return chain.then(function () {
      lastResult = output(results, !(opts && opts.show === false));
      return lastResult;
    });
  }

  // ================================================================ alap-tesztek
  register('dom: szöveg mindig szövegként', function (t) {
    var el = h('div', null, '<b>x</b>', 3);
    t.eq(el.childNodes.length, 2, 'két szövegcsomópont');
    t.eq(el.textContent, '<b>x</b>3', 'nincs HTML-értelmezés');
    t.eq(el.querySelector('b'), null, 'nem jött létre <b>');
    var el2 = h('span', { text: '<i>y</i>' });
    t.eq(el2.querySelector('i'), null, 'text attribútum is szövegként');
  });

  register('dom: tiltott attribútumok és URL-ek', function (t) {
    var bad = {};
    bad['inner' + 'HTML'] = '<b>x</b>';           // (a build-lint a szó szerinti tiltott neveket keresi)
    t.throws(function () { h('div', bad); }, 'HTML-értelmező attribútum tiltott');
    t.throws(function () { h('div', { style: 'color:red' }); }, 'style-attribútum tiltott (CSP)');
    t.throws(function () { h('a', { href: 'java' + 'script:alert(1)' }); }, 'script-séma URL tiltott');
    t.throws(function () { h('a', { href: 'https:' + '//example.org/' }); }, 'külső URL tiltott');
    t.throws(function () { h('a', { href: '//example.org/' }); }, 'protokoll-relatív URL tiltott');
    t.throws(function () { h('div', { onclick: 'alert(1)' }); }, 'sztring-eseménykezelő tiltott');
    t.throws(function () { h('script'); }, '<script> nem építhető');
    t.throws(function () { MA.dom.svg('foreignObject'); }, 'foreignObject nem építhető');
    t.ok(!!h('a', { href: '#/overview' }), 'hash-link engedett');
    t.ok(!!h('a', { href: '/f/doc/1/abc' }), 'saját gyökér-relatív link engedett');
  });

  register('dom: SVG névtér és CSSOM-stílus', function (t) {
    var c = MA.dom.svg('circle', { cx: 1, cy: 2, r: 3 });
    t.eq(c.namespaceURI, MA.dom.SVG_NS, 'SVG-névtér');
    t.eq(c.getAttribute('r'), '3', 'attribútum sztringként');
    var d = h('div', { styles: { width: '10px', '--x': '1' } });
    t.eq(d.style.width, '10px', 'CSSOM-stílus');
  });

  register('geom: lineáris leképezés', function (t) {
    var s = MA.geom.linear([-2, 1], [0, 300]);
    t.near(s(-2), 0, 1e-9, 'bal szél');
    t.near(s(1), 300, 1e-9, 'jobb szél');
    t.near(s(0), 200, 1e-9, 'null-vonal');
    t.near(s.invert(200), 0, 1e-9, 'invert');
    var r = MA.geom.linear([0, 1], [100, 0]);
    t.near(r(0.25), 75, 1e-9, 'fordított tengely');
    var c = MA.geom.linear([0, 1], [0, 10], { clamp: true });
    t.near(c(5), 10, 1e-9, 'clamp');
    t.ok(!c.inDomain(2) && c.inDomain(0.5), 'inDomain');
    t.throws(function () { MA.geom.linear([1, 1], [0, 1]); }, 'üres tartomány hiba');
  });

  register('geom: log10-leképezés', function (t) {
    var s = MA.geom.log10([0.1, 10], [0, 200]);
    t.near(s(1), 100, 1e-9, 'RR = 1 középen');
    t.near(s(0.1), 0, 1e-9, 'bal szél');
    t.near(s.invert(100), 1, 1e-9, 'invert');
    t.ok(isNaN(s(0)), 'nem pozitív érték → NaN');
    t.throws(function () { MA.geom.log10([0, 10], [0, 1]); }, 'nem pozitív tartomány');
  });

  register('geom: pixelre igazítás, sávok, tickek, poligon', function (t) {
    t.eq(MA.geom.snap(10.3, 1), 10.5, '1 px-es vonal fél pixelre');
    t.eq(MA.geom.snap(10.6, 2), 11, '2 px-es vonal egész pixelre');
    t.eq(MA.geom.px(1.26), '1.3', 'px: 0,1 px-es kerekítés');
    var b = MA.geom.band(4, [0, 100]);
    t.near(b.center(0), 12.5, 1e-9, 'sáv-közép');
    var s = MA.geom.linear([-1, 1], [0, 200]);
    var tk = MA.geom.ticks([{ at: -1, text: '0.37' }, { at: 0, text: '1' }, { at: 5, text: 'kívül' }], s);
    t.eq(tk.length, 2, 'tartományon kívüli tick elhagyva');
    t.eq(tk[1].text, '1', 'a tick szövege a motoré');
    t.near(tk[1].px, 100, 1e-9, 'tick pixel');
    t.eq(MA.geom.points([[-1, 0], [1, 1]], s, MA.geom.linear([0, 1], [10, 0])), '0,10 200,0', 'poligon pontsor');
    t.near(MA.geom.areaRadius(25, 100, 10), 5, 1e-9, 'terület-arányos sugár');
    var ci = MA.geom.clipInterval(-3, 0.5, s);
    t.ok(ci.clipLeft && !ci.clipRight && ci.x1 === 0, 'CI levágása');
  });

  register('i18n: HU és EN kulcskészlet azonos', function (t) {
    var hu = MA.i18n.keys('hu'), en = MA.i18n.keys('en');
    t.ok(hu.length > 50, 'van szótár (' + hu.length + ' kulcs)');
    var missingEn = hu.filter(function (k) { return en.indexOf(k) < 0; });
    var missingHu = en.filter(function (k) { return hu.indexOf(k) < 0; });
    t.deepEq(missingEn, [], 'EN-ből hiányzó kulcsok');
    t.deepEq(missingHu, [], 'HU-ból hiányzó kulcsok');
    t.eq(MA.i18n.pick({ hu: '0,49 [0,33; 0,73]', en: '0.49 [0.33; 0.73]' }), MA.i18n.lang() === 'en' ? '0.49 [0.33; 0.73]' : '0,49 [0,33; 0,73]', 'pick: a motor szövege');
    t.eq(MA.i18n.pick(null), '—', 'hiányzó szám → „—”');
    t.eq(MA.i18n.t('changes.external', { n: '2' }).indexOf('2') >= 0, true, 'paraméter-behelyettesítés');
  });

  register('i18n: minden 3.4-es hibakódnak van magyar címe', function (t) {
    MA.api.ERROR_CODES.forEach(function (c) {
      t.ok(MA.i18n.has('err.' + c, 'hu') && MA.i18n.has('err.' + c, 'en'), 'err.' + c);
    });
  });

  register('store: állapot, feliratkozás, undo/redo', function (t) {
    var seen = [];
    var off = MA.store.subscribe('selftest.a', function (v) { seen.push(v); });
    MA.store.set('selftest.a.b', 1);
    MA.store.set('selftest.x', 2);
    t.eq(MA.store.get('selftest.a.b'), 1, 'get');
    t.eq(seen.length, 1, 'csak az érintett út értesül');
    off();
    MA.store.set('selftest.a.b', 3);
    t.eq(seen.length, 1, 'leiratkozás');
    t.throws(function () { MA.store.set('__proto__.x', 1); }, '__proto__ tiltott');
    var table = { r1: { e1: '4' } };
    var hist = MA.store.createHistory({ apply: function (op) {
      if (op.type === 'cell') { table[op.row_uid][op.field] = op.after; }
      if (op.type === 'batch') { op.ops.forEach(function (o) { table[o.row_uid][o.field] = o.after; }); }
    } });
    table.r1.e1 = '140';
    hist.push({ type: 'cell', dataset: 'o1', row_uid: 'r1', field: 'e1', before: '4', after: '140' });
    t.ok(hist.canUndo() && !hist.canRedo(), 'van visszavonható');
    var inv = hist.undo();
    t.eq(table.r1.e1, '4', 'undo: új műveletként visszaállít');
    t.eq(inv.after, '4', 'az inverz művelet');
    hist.redo();
    t.eq(table.r1.e1, '140', 'redo');
    table.r1.n1 = '123';
    hist.push({ type: 'batch', ops: [{ type: 'cell', row_uid: 'r1', field: 'e1', before: '140', after: '6' }, { type: 'cell', row_uid: 'r1', field: 'n1', before: '123', after: '306' }] });
    table.r1.e1 = '6'; table.r1.n1 = '306';
    hist.undo();
    t.ok(table.r1.e1 === '140' && table.r1.n1 === '123', 'batch undo');
    t.eq(MA.store.invertOp({ type: 'rows-insert', index: 2, rows: [] }).type, 'rows-delete', 'sor-beszúrás inverze');
    MA.store.reset('selftest');
  });

  register('tároló-szabály: csak mag.pref.* és a token (7.6)', function (t) {
    var ls = [];
    var ss = [];
    try { for (var i = 0; i < window.localStorage.length; i++) { ls.push(window.localStorage.key(i)); } } catch (e) { /* nincs */ }
    try { for (var j = 0; j < window.sessionStorage.length; j++) { ss.push(window.sessionStorage.key(j)); } } catch (e) { /* nincs */ }
    t.deepEq(ls.filter(function (k) { return k.indexOf('mag.pref.') !== 0; }), [], 'localStorage: csak mag.pref.*');
    t.deepEq(ss.filter(function (k) { return k !== MA.api.session.TOKEN_KEY; }), [], 'sessionStorage: csak a token');
    t.throws(function () { MA.prefs.set('x', new Array(300).join('a')); }, 'hosszú érték nem preferencia');
    t.throws(function () { MA.prefs.set('Rossz kulcs', '1'); }, 'kulcsformátum');
    t.eq(document.cookie, '', 'nincs süti');
  });

  register('CSP-higiénia a DOM-ban', function (t) {
    var scripts = MA.dom.$$('script');
    t.ok(scripts.length > 0 && scripts.every(function (s) { return s.hasAttribute('nonce') || s.nonce; }), 'minden <script> nonce-os');
    t.eq(MA.dom.$$('[style]').length, 0, 'nincs style-attribútum');
    var inline = MA.dom.$$('*').filter(function (el) {
      return Array.prototype.some.call(el.attributes, function (a) { return /^on/i.test(a.name); });
    });
    t.eq(inline.length, 0, 'nincs inline eseménykezelő');
    t.eq(MA.dom.$$('link[rel="stylesheet"], script[src], iframe, object, embed').length, 0, 'nincs külső erőforrás');
  });

  register('api: csak saját /api/* útvonal, hibák', function (t) {
    return MA.api.get('https:' + '//example.org/api/x', { toast: false }).then(function () {
      t.ok(false, 'külső URL-t nem szabad hívni');
    }, function (e) {
      t.eq(e.code, 'BAD_REQUEST', 'külső URL elutasítva');
    }).then(function () {
      var run = MA.api.latest();
      var resolveFirst;
      var p1 = run(function () { return new Promise(function (r) { resolveFirst = r; }); });
      var p2 = run(function () { return Promise.resolve('második'); });
      resolveFirst('első');
      return Promise.all([p1, p2]).then(function (v) {
        t.eq(v[0], null, 'latest: a felülírt kérés null');
        t.eq(v[1], 'második', 'latest: a legutolsó nyer');
      });
    });
  });

  register('keret: navigáció, fülek, képernyők', function (t) {
    var start = MA.app.current();
    MA.app.TABS.forEach(function (tb) {
      t.ok(!!document.getElementById('nav-' + tb.id), 'nav-link: ' + tb.id);
    });
    MA.app.screens().forEach(function (s) {
      t.ok(MA.i18n.has(s.title_key, 'hu') && MA.i18n.has(s.title_key, 'en'), 'cím-kulcs: ' + s.id);
    });
    var tabs = MA.app.TABS.map(function (tb) { return tb.id; });
    var chain = Promise.resolve();
    tabs.forEach(function (id) {
      chain = chain.then(function () {
        MA.app.navigate(id);
        return waitFor(function () { var c = MA.app.current(); return c && c.tab === id; }).then(function (ok) {
          t.ok(ok, 'útvonal: ' + id);
          var link = document.getElementById('nav-' + id);
          t.eq(link && link.getAttribute('aria-current'), 'page', 'aria-current: ' + id);
          var root = document.getElementById('screen-root');
          t.ok(root && root.childNodes.length > 0, 'a képernyő renderelt: ' + id);
        });
      });
    });
    return chain.then(function () {
      var ev = new KeyboardEvent('keydown', { key: '3', code: 'Digit3', altKey: true, bubbles: true });
      document.body.dispatchEvent(ev);
      return waitFor(function () { var c = MA.app.current(); return c && c.tab === 'extraction'; });
    }).then(function (ok) {
      t.ok(ok, 'Alt+3 → 3 Kinyerés');
      if (start) { MA.app.navigate(start.id, start.params); }
      return waitFor(function () { var c = MA.app.current(); return !start || (c && c.id === start.id); });
    }).then(function () {
      t.deepEq(MA.i18n.missing(), [], 'nem kértünk hiányzó i18n-kulcsot');
    });
  });

  MA.selftest = {
    register: register,
    run: run,
    waitFor: waitFor,
    result: function () { return lastResult; }
  };
})();
