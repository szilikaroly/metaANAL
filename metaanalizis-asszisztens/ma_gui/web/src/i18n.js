/* i18n.js — szótáras felületszövegek (HU alap, EN váltható; terv 3.3).
 *
 * A szótárak a build során a src/i18n/{hu,en}.json + src/i18n/{hu,en}/*.json fájlokból
 * állnak össze, és <script type="application/json" id="ma-i18n">-ként kerülnek a lapba.
 * A kulcsok laposak, ponttal tagoltak ("nav.tab.overview"). Paraméter: "{név}".
 * A motor i18n-objektumait ({hu, en}) a pick() választja ki — a felület NEM formáz számot.
 */
(function () {
  'use strict';
  var MA = window.MA;

  var LANGS = ['hu', 'en'];
  var dicts = { hu: {}, en: {} };
  var current = 'hu';
  var listeners = [];
  var missing = {};

  // A build (build_gui.py pack_i18n) a két szótárat egy fába teszi ({"a":{"b":["hu","en"]}}; ha egy kulcs
  // egyben előtag is, a saját szövege az alfa "" kulcsán áll), majd szöveges LZ77-tel tömöríti („lz1”):
  // '~~' = '~'; '~' + 2 jegy (eltolás-1) + 1 jegy (hossz-LZ_MIN) = visszahivatkozás; minden más szó szerint.
  var LZ_ALPHABET = '!#$%&()*+,-./0123456789:;=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[]^_abcdefghijklmnopqrstuvwxyz{|}';
  var LZ_MIN = 5;

  function lzDecode(z) {
    var n = LZ_ALPHABET.length;
    var out = [];                 // karaktertömb: a visszahivatkozás indexelése O(1) (sztring-ropé helyett)
    var i = 0;
    while (i < z.length) {
      var c = z.charAt(i);
      if (c !== '~') { out.push(c); i += 1; continue; }
      if (z.charAt(i + 1) === '~') { out.push('~'); i += 2; continue; }
      var a = LZ_ALPHABET.indexOf(z.charAt(i + 1));
      var b = LZ_ALPHABET.indexOf(z.charAt(i + 2));
      var len = LZ_ALPHABET.indexOf(z.charAt(i + 3)) + LZ_MIN;
      if (a < 0 || b < 0 || len < LZ_MIN) { throw new Error('i18n: hibás lz1-csomag'); }
      var from = out.length - (a * n + b + 1);
      if (from < 0) { throw new Error('i18n: hibás lz1-hivatkozás'); }
      for (var k = 0; k < len; k++) { out.push(out[from + k]); }   // átfedő másolás is helyes
      i += 4;
    }
    return out.join('');
  }

  function untree(node, prefix, out) {
    Object.keys(node).forEach(function (k) {
      var key = k === '' ? prefix : (prefix ? prefix + '.' + k : k);
      var v = node[k];
      if (Array.isArray(v)) {
        out.hu[key] = v[0];
        out.en[key] = v[1];
      } else {
        untree(v, key, out);
      }
    });
    return out;
  }

  function load() {
    var node = document.getElementById('ma-i18n');
    if (!node) { return; }
    try {
      var text = node.textContent || '';
      var data = node.getAttribute('data-enc') === 'lz1' ?
        untree(JSON.parse(lzDecode(text)), '', { hu: {}, en: {} }) : JSON.parse(text || '{}');
      LANGS.forEach(function (l) { dicts[l] = data[l] || {}; });
    } catch (e) {
      dicts = { hu: {}, en: {} };
      if (window.console) { window.console.error(e); }
    }
  }

  function interpolate(s, args) {
    if (!args) { return s; }
    return s.replace(/\{([a-zA-Z0-9_]+)\}/g, function (m, name) {
      if (!Object.prototype.hasOwnProperty.call(args, name)) { return m; }
      var v = args[name];
      return v === null || v === undefined ? '—' : String(v);
    });
  }

  /** t(key, args?) → szöveg az aktuális nyelven; hiányzó kulcsnál a HU, végül „⟦key⟧” (és feljegyzi). */
  function t(key, args) {
    var d = dicts[current];
    var s = d && Object.prototype.hasOwnProperty.call(d, key) ? d[key] : undefined;
    if (s === undefined) {
      missing[current + ':' + key] = true;
      s = Object.prototype.hasOwnProperty.call(dicts.hu, key) ? dicts.hu[key] : '⟦' + key + '⟧';
    }
    return interpolate(String(s), args);
  }

  function has(key, lang) {
    var d = dicts[lang || current];
    return !!d && Object.prototype.hasOwnProperty.call(d, key);
  }

  /**
   * pick(obj) — a motor nyelvfüggő szövege ({hu, en}) az aktuális nyelven; sztringet változatlanul ad;
   * null/hiányzó → '—' (terv 6.8: hiányzó mezőnél „—”, nem saját formázás).
   */
  function pick(obj, fallback) {
    if (obj === null || obj === undefined) { return fallback === undefined ? '—' : fallback; }
    if (typeof obj === 'string') { return obj; }
    if (typeof obj === 'object') {
      if (typeof obj[current] === 'string') { return obj[current]; }
      if (typeof obj.hu === 'string') { return obj.hu; }
      if (typeof obj.en === 'string') { return obj.en; }
    }
    return fallback === undefined ? '—' : fallback;
  }

  function lang() { return current; }

  function setLang(l, opts) {
    if (LANGS.indexOf(l) < 0 || l === current) { return false; }
    current = l;
    document.documentElement.setAttribute('lang', l);
    if (!(opts && opts.persist === false) && MA.prefs) { MA.prefs.set('lang', l); }
    listeners.slice().forEach(function (fn) {
      try { fn(l); } catch (e) { if (window.console) { window.console.error(e); } }
    });
    return true;
  }

  /** onChange(fn) → leiratkozó függvény; fn(lang) minden nyelvváltáskor. */
  function onChange(fn) {
    listeners.push(fn);
    return function () { listeners = listeners.filter(function (x) { return x !== fn; }); };
  }

  /** apply(root) — a data-i18n / data-i18n-attrs elemek újrafordítása helyben (pl. nyitott modálban). */
  function apply(root) {
    var r = root || document.body;
    Array.prototype.forEach.call(r.querySelectorAll('[data-i18n]'), function (el) {
      var args = null;
      var raw = el.getAttribute('data-i18n-args');
      if (raw) { try { args = JSON.parse(raw); } catch (e) { args = null; } }
      el.textContent = t(el.getAttribute('data-i18n'), args);
    });
    Array.prototype.forEach.call(r.querySelectorAll('[data-i18n-attrs]'), function (el) {
      el.getAttribute('data-i18n-attrs').split(';').forEach(function (pair) {
        var i = pair.indexOf('=');
        if (i > 0) { el.setAttribute(pair.slice(0, i), t(pair.slice(i + 1))); }
      });
    });
  }

  /** Időbélyeg megjelenítése sztring-szeleteléssel (ISO → 'ÉÉÉÉ-HH-NN ÓÓ:PP'), Date/Intl nélkül. */
  function ts(iso) {
    if (typeof iso !== 'string' || iso.length < 16) { return iso ? String(iso) : '—'; }
    return iso.slice(0, 10) + ' ' + iso.slice(11, 16);
  }

  function keys(l) { return Object.keys(dicts[l || current] || {}); }
  function missingKeys() { return Object.keys(missing); }

  load();

  MA.i18n = {
    LANGS: LANGS,
    t: t,
    has: has,
    pick: pick,
    lang: lang,
    setLang: setLang,
    onChange: onChange,
    apply: apply,
    ts: ts,
    keys: keys,
    missing: missingKeys,
    _reload: load
  };
})();
