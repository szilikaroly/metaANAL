/* geom.js — pixel-leképezés (terv 3.3, 6.1, 6.8).
 *
 * AZ EGYETLEN modul, amely Math.*-t használhat. Csak a motor által adott tartományt
 * (axis.domain), tengelyosztást (axis.ticks[].at), poligonokat és súlyokat képezi pixelre.
 * Statisztikát, kerekített számszöveget, tengelyosztást NEM számol: minden megjelenített
 * számszöveg a motor display_text / ticks[].text mezőjéből jön.
 */
(function () {
  'use strict';
  var MA = window.MA;

  function isNum(v) { return typeof v === 'number' && isFinite(v); }

  function assertDomain(domain, name) {
    if (!Array.isArray(domain) || domain.length !== 2 || !isNum(domain[0]) || !isNum(domain[1]) || domain[0] === domain[1]) {
      throw new Error('geom: érvénytelen ' + (name || 'tartomány'));
    }
  }

  /**
   * linear(domain, range, opts?) → scale(v) = pixel (lebegőpontos)
   *   domain: [d0, d1] a motorból (pl. axis.domain, elemzési skálán)
   *   range:  [r0, r1] pixelben (fordított tengelyhez r0 > r1)
   *   opts.clamp: true → a tartományon kívüli értéket a szélre szorítja
   * scale.invert(px), scale.domain, scale.range, scale.kind = 'linear', scale.inDomain(v)
   */
  function linear(domain, range, opts) {
    assertDomain(domain, 'domain');
    assertDomain(range, 'range');
    var d0 = domain[0], d1 = domain[1], r0 = range[0], r1 = range[1];
    var k = (r1 - r0) / (d1 - d0);
    var clamp = !!(opts && opts.clamp);
    var lo = Math.min(d0, d1), hi = Math.max(d0, d1);
    function scale(v) {
      if (!isNum(v)) { return NaN; }
      if (clamp) { v = Math.min(hi, Math.max(lo, v)); }
      return r0 + (v - d0) * k;
    }
    scale.invert = function (px) { return d0 + (px - r0) / k; };
    scale.domain = [d0, d1];
    scale.range = [r0, r1];
    scale.kind = 'linear';
    scale.inDomain = function (v) { return isNum(v) && v >= lo && v <= hi; };
    return scale;
  }

  /**
   * log10(domain, range, opts?) — megjelenítési skálán (> 0) adott tartomány log10-leképezése
   * (pl. ha a motor arányskálán adja a domaint). Nem pozitív értékre NaN.
   */
  function log10(domain, range, opts) {
    assertDomain(domain, 'domain');
    if (!(domain[0] > 0 && domain[1] > 0)) { throw new Error('geom: log10-skálához pozitív tartomány kell'); }
    var inner = linear([Math.log10(domain[0]), Math.log10(domain[1])], range, opts);
    var lo = Math.min(domain[0], domain[1]), hi = Math.max(domain[0], domain[1]);
    var clamp = !!(opts && opts.clamp);
    function scale(v) {
      if (!isNum(v) || v <= 0) { return NaN; }
      if (clamp) { v = Math.min(hi, Math.max(lo, v)); }
      return inner(Math.log10(v));
    }
    scale.invert = function (px) { return Math.pow(10, inner.invert(px)); };
    scale.domain = [domain[0], domain[1]];
    scale.range = [range[0], range[1]];
    scale.kind = 'log10';
    scale.inDomain = function (v) { return isNum(v) && v >= lo && v <= hi; };
    return scale;
  }

  /**
   * band(n, range, padding?) — n egyforma sáv (pl. forest-sorok). Visszaad: {step, at(i), center(i)}.
   * padding: a sávköz aránya [0, 1) (alap 0).
   */
  function band(n, range, padding) {
    if (!(n >= 0) || !isNum(range[0]) || !isNum(range[1])) { throw new Error('geom: érvénytelen sáv'); }
    var p = isNum(padding) ? Math.max(0, Math.min(0.95, padding)) : 0;
    var step = n > 0 ? (range[1] - range[0]) / n : 0;
    return {
      step: step,
      bandwidth: step * (1 - p),
      at: function (i) { return range[0] + i * step + step * p / 2; },
      center: function (i) { return range[0] + (i + 0.5) * step; }
    };
  }

  /** snap(px, strokeWidth=1) — éles vonal: páratlan vastagságnál fél pixelre, párosnál egészre igazít. */
  function snap(px, strokeWidth) {
    if (!isNum(px)) { return px; }
    var w = isNum(strokeWidth) ? Math.round(strokeWidth) : 1;
    return (w % 2 === 1) ? Math.floor(px) + 0.5 : Math.round(px);
  }

  /** px(v) — SVG-attribútumhoz: pixelérték 0,1 px-re kerekítve, sztringként ('—' helyett ''). */
  function px(v) {
    if (!isNum(v)) { return '0'; }
    var r = Math.round(v * 10) / 10;
    return String(r === 0 ? 0 : r);
  }

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function min() { return Math.min.apply(null, arguments); }
  function max() { return Math.max.apply(null, arguments); }
  function abs(v) { return Math.abs(v); }
  function round(v) { return Math.round(v); }
  function floor(v) { return Math.floor(v); }
  function ceil(v) { return Math.ceil(v); }

  /** ticks(axisTicks, scale) → [{px, text, at}] — a motor ticks[] tömbjének pixelre képezése (a tartományon kívülit elhagyja). */
  function ticks(axisTicks, scale) {
    return (axisTicks || []).filter(function (t) {
      return t && isNum(t.at) && (!scale.inDomain || scale.inDomain(t.at));
    }).map(function (t) {
      return { px: scale(t.at), text: t.text === undefined || t.text === null ? '' : String(t.text), at: t.at };
    });
  }

  /** points(polygon, sx, sy) → 'x,y x,y …' SVG points-sztring a motor [[x, y], …] poligonjából. */
  function points(polygon, sx, sy) {
    return (polygon || []).filter(function (p) { return p && isNum(p[0]) && isNum(p[1]); }).map(function (p) {
      return px(sx(p[0])) + ',' + px(sy(p[1]));
    }).join(' ');
  }

  /** pathD(polyline, sx, sy) → 'M x y L x y …' (nyitott vonal). */
  function pathD(polyline, sx, sy) {
    var pts = (polyline || []).filter(function (p) { return p && isNum(p[0]) && isNum(p[1]); });
    return pts.map(function (p, i) { return (i === 0 ? 'M' : 'L') + px(sx(p[0])) + ' ' + px(sy(p[1])); }).join(' ');
  }

  /**
   * areaRadius(weight, maxWeight, rMax, rMin?) — jelölő sugara úgy, hogy a TERÜLET arányos legyen
   * a motor súlyával (weight_pct). Ez pixel-leképezés, nem statisztika.
   */
  function areaRadius(weight, maxWeight, rMax, rMin) {
    if (!isNum(weight) || !isNum(maxWeight) || maxWeight <= 0 || weight <= 0) { return isNum(rMin) ? rMin : 0; }
    var r = rMax * Math.sqrt(weight / maxWeight);
    return isNum(rMin) ? Math.max(rMin, r) : r;
  }

  /**
   * clipInterval(lo, hi, scale) — CI-szakasz levágása a tartományra: {x1, x2, clipLeft, clipRight}.
   * (A motor clip-jelzője az irányadó; ez csak a pixelhatárokat adja.)
   */
  function clipInterval(lo, hi, scale) {
    var d = scale.domain, dl = Math.min(d[0], d[1]), dh = Math.max(d[0], d[1]);
    var cl = isNum(lo) && lo < dl, cr = isNum(hi) && hi > dh;
    var a = isNum(lo) ? Math.max(dl, Math.min(dh, lo)) : dl;
    var b = isNum(hi) ? Math.max(dl, Math.min(dh, hi)) : dh;
    return { x1: scale(a), x2: scale(b), clipLeft: cl, clipRight: cr };
  }

  MA.geom = {
    isNum: isNum,
    linear: linear,
    log10: log10,
    band: band,
    snap: snap,
    px: px,
    clamp: clamp,
    min: min,
    max: max,
    abs: abs,
    round: round,
    floor: floor,
    ceil: ceil,
    ticks: ticks,
    points: points,
    pathD: pathD,
    areaRadius: areaRadius,
    clipInterval: clipInterval
  };
})();
