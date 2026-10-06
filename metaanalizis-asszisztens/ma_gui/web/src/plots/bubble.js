/* plots/bubble.js — buborékábra a meta-regresszióhoz (terv 3.5.8, 4.6 bubble; v1 E4c).
 *
 * A motor szk.ma.plot/v2 'bubble' blokkjának pixelre képezése. x = a moderátor, y = a hatás az elemzési skálán; a
 * buborék TERÜLETE a motor súlyával (weight_pct) arányos (MA.geom.areaRadius — pixel-leképezés). Az illesztett
 * egyenes (line: [[x, y], …]) és a CI-sáv (band: [[x, alsó, felső], …], vagy kész band_polygon: [[x, y], …]) a
 * motor rácsán, a koefficiensek kovarianciájából számolt pontok: a felület csak a pontokat köti össze (felső él
 * előre, alsó él visszafelé) — nem számol, nem interpolál. A predikciós sáv (pi_band: [[x, alsó, felső], …]) két
 * szaggatott vonal a motor pontjain. A tengelyek (x_axis, y_axis: domain, ticks, title), a referencia-vonalak
 * (y_axis.refs, ennek hiányában a scale.null_analysis) és minden szöveg (coef_text, x_text, display_text, weight_text,
 * line_label, band_label, pi_band_label, note) a motoré.
 * Kategóriás moderátornál (moderator.type = 'categorical') buborék helyett csoportonkénti pontdiagram: a pontok a
 * motor x-helyén (a csoport sorszáma), csoportonként a motor összesítője (groups[]: estimate, ci_lower, ci_upper,
 * display_text) gyémánttal és CI-vel.
 * Jelölők: <g data-uid data-x data-y data-w> — a render-konzisztencia teszthez. A szín sosem egyedüli jelölés.
 *
 * MA.plots.bubble.render(container, plot, {onDrill, signal}) → {svg, layout, focus, items, el} | null
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var S = MA.dom.svg;
  var G = MA.geom;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  var W = 780;
  var H = 460;
  var ML = 86, MR = 24, MT = 18, MB = 70;
  var R_MAX = 20, R_MIN = 3;

  MA.plots = MA.plots || {};

  function C() { return MA.plots.common; }

  function isCategorical(b) { return !!(b && b.moderator && b.moderator.type === 'categorical'); }

  function available(plot) {
    var b = plot && plot.bubble;
    return !!(b && Array.isArray(b.points) && b.points.length && b.x_axis && b.y_axis);
  }

  function layout(plot) {
    var b = plot.bubble;
    return { W: W, H: H, x0: ML, x1: W - MR, y0: H - MB, y1: MT,
      sx: G.linear(b.x_axis.domain, [ML, W - MR]), sy: G.linear(b.y_axis.domain, [H - MB, MT]) };
  }

  /** A sáv poligonja a motor pontjaiból: kész band_polygon, vagy a [[x, alsó, felső], …] rács (felső él előre, alsó
   *  vissza) — csak a pontok sorrendje, számítás nincs. */
  function bandPolygon(b) {
    if (Array.isArray(b.band_polygon) && b.band_polygon.length > 2) { return b.band_polygon; }
    var band = Array.isArray(b.band) ? b.band.filter(function (p) { return p && p.length >= 3 && G.isNum(p[0]) && G.isNum(p[1]) && G.isNum(p[2]); }) : [];
    if (band.length < 2) { return null; }
    var upper = band.map(function (p) { return [p[0], p[2]]; });
    var lower = band.slice().reverse().map(function (p) { return [p[0], p[1]]; });
    return upper.concat(lower);
  }

  function render(container, plot, opts) {
    opts = opts || {};
    var CC = C();
    if (!available(plot)) {
      MA.dom.mount(container, MA.ui.emptyState('plots.bubble.none'));
      return null;
    }
    var b = plot.bubble;
    var L = layout(plot);
    var cat = isCategorical(b);
    var modLabel = CC.txt(b.moderator && b.moderator.label, (b.moderator && b.moderator.name) || '');
    var svgEl = CC.svgRoot(W, H, t(cat ? 'plots.bubble.ariaCat' : 'plots.bubble.aria', { moderator: modLabel }), 'bu-svg');
    var clip = CC.clipRect(svgEl, L.x0, L.y1, L.x1 - L.x0, L.y0 - L.y1);
    svgEl.appendChild(S('rect', { 'class': 'pl-frame', x: G.px(L.x0), y: G.px(L.y1), width: G.px(L.x1 - L.x0), height: G.px(L.y0 - L.y1) }));
    // tengelyek (a motor tickjei)
    var gAxis = S('g', { 'class': 'bu-axes' });
    CC.bottomAxis(gAxis, b.x_axis, L.sx, L.y0, { x0: L.x0, x1: L.x1, title: b.x_axis.title, grid: [L.y1, L.y0] });
    CC.leftAxis(gAxis, b.y_axis, L.sy, L.x0, { y0: L.y0, y1: L.y1, title: b.y_axis.title, grid: [L.x0, L.x1] });
    svgEl.appendChild(gAxis);
    var plotG = S('g', { 'clip-path': clip });
    svgEl.appendChild(plotG);
    // referencia-vonalak: a motor y_axis.refs-e, ennek hiányában a null-hatás (scale.null_analysis)
    var refs = Array.isArray(b.y_axis.refs) && b.y_axis.refs.length ? b.y_axis.refs
      : (plot.scale && G.isNum(plot.scale.null_analysis) ? [{ at: plot.scale.null_analysis, text: null }] : []);
    refs.forEach(function (r) {
      if (!G.isNum(r.at) || !L.sy.inDomain(r.at)) { return; }
      var y = G.snap(L.sy(r.at));
      plotG.appendChild(S('line', { 'class': 'fp-null bu-ref', x1: G.px(L.x0), x2: G.px(L.x1), y1: G.px(y), y2: G.px(y), 'data-at': String(r.at) }));
      if (r.text) { svgEl.appendChild(S('text', { 'class': 'pl-tick-label bu-ref-label', x: G.px(L.x1 - 4), y: G.px(y - 4), 'text-anchor': 'end' }, CC.txt(r.text, ''))); }
    });
    // sáv és illesztett egyenes (folytonos moderátor)
    var poly = cat ? null : bandPolygon(b);
    if (poly) { plotG.appendChild(S('polygon', { 'class': 'bu-band', points: G.points(poly, L.sx, L.sy) })); }
    // predikciós sáv: a motor [[x, alsó, felső], …] rácsa két szaggatott vonalként (felső, alsó)
    var pi = !cat && Array.isArray(b.pi_band) ? b.pi_band.filter(function (q) { return q && q.length >= 3 && G.isNum(q[0]) && G.isNum(q[1]) && G.isNum(q[2]); }) : [];
    if (pi.length > 1) {
      plotG.appendChild(S('path', { 'class': 'bu-pi', 'data-edge': 'upper', d: G.pathD(pi.map(function (q) { return [q[0], q[2]]; }), L.sx, L.sy) }));
      plotG.appendChild(S('path', { 'class': 'bu-pi', 'data-edge': 'lower', d: G.pathD(pi.map(function (q) { return [q[0], q[1]]; }), L.sx, L.sy) }));
    }
    if (!cat && Array.isArray(b.line) && b.line.length > 1) {
      plotG.appendChild(S('path', { 'class': 'bu-line', d: G.pathD(b.line, L.sx, L.sy) }));
    }
    // csoport-összesítők (kategóriás moderátor)
    var groups = cat && Array.isArray(b.groups) ? b.groups : [];
    groups.forEach(function (g) {
      if (!G.isNum(g.x) || !G.isNum(g.estimate)) { return; }
      var cx = L.sx(g.x) + 26;
      var gy = G.isNum(g.ci_lower) ? L.sy(g.ci_lower) : L.sy(g.estimate);
      var gy2 = G.isNum(g.ci_upper) ? L.sy(g.ci_upper) : L.sy(g.estimate);
      var my = L.sy(g.estimate);
      var gg = S('g', { 'class': 'bu-group', 'data-group': String(g.id || ''), 'data-y': String(g.estimate), 'data-lo': String(g.ci_lower), 'data-hi': String(g.ci_upper) },
        S('line', { 'class': 'fp-ci', x1: G.px(G.snap(cx)), x2: G.px(G.snap(cx)), y1: G.px(gy), y2: G.px(gy2) }),
        S('polygon', { 'class': 'fp-diamond', points: [G.px(cx) + ',' + G.px(my - 7), G.px(cx + 6) + ',' + G.px(my), G.px(cx) + ',' + G.px(my + 7), G.px(cx - 6) + ',' + G.px(my)].join(' ') }),
        S('title', null, CC.txt(g.label, String(g.id || '')) + ': ' + CC.txt(g.display_text)));
      plotG.appendChild(gg);
    });
    // buborékok: a nagyok alul (súly szerint csökkenő sorrend a rajzolásban; a billentyűzet az x szerinti sorrendet követi)
    var pts = b.points.filter(function (p) { return G.isNum(p.x) && G.isNum(p.y); });
    var ptsG = S('g', { role: 'list', 'aria-label': t('plots.bubble.listAria'), 'clip-path': clip });
    svgEl.appendChild(ptsG);
    var maxW = 0;
    pts.forEach(function (p) { if (G.isNum(p.weight_pct) && p.weight_pct > maxW) { maxW = p.weight_pct; } });
    var byUid = CC.studyIndex(plot);
    var nodes = {};
    var tip = CC.tooltip(svgEl, W);
    pts.slice().sort(function (p, q) { return (q.weight_pct || 0) - (p.weight_pct || 0); }).forEach(function (p) {
      var cx = L.sx(p.x), cy = L.sy(p.y);
      var r = cat ? 5 : G.areaRadius(p.weight_pct, maxW, R_MAX, R_MIN);
      var s = byUid[p.row_uid] || {};
      var name = p.label || s.label || p.row_uid;
      var lines = [name, modLabel + ': ' + CC.txt(p.x_text, ''), CC.txt(p.display_text || s.display_text)];
      if (p.weight_text || s.weight_text) { lines.push(t('plots.bubble.weight', { w: CC.txt(p.weight_text || s.weight_text) })); }
      var g = S('g', { 'class': ['bu-point', p.flags && p.flags.estimated ? 'is-estimated' : ''].join(' '), 'data-uid': p.row_uid || '', 'data-x': String(p.x),
        'data-y': String(p.y), 'data-w': G.isNum(p.weight_pct) ? String(p.weight_pct) : '', tabindex: '-1', role: 'listitem',
        'aria-label': t('plots.bubble.pointAria', { study: name, x: CC.txt(p.x_text, ''), effect: CC.txt(p.display_text || s.display_text),
          weight: CC.txt(p.weight_text || s.weight_text, '—') }) },
      S('circle', { 'class': 'bu-bubble', cx: G.px(cx), cy: G.px(cy), r: G.px(r) }),
      S('circle', { 'class': 'bu-center', cx: G.px(cx), cy: G.px(cy), r: '1.5' }));
      g.addEventListener('mouseenter', function () { tip.show(lines, cx, cy - r); CC.highlight(p.row_uid, 'bubble'); });
      g.addEventListener('mouseleave', function () { tip.hide(); CC.highlight(null, 'bubble'); });
      g.addEventListener('focus', function () { tip.show(lines, cx, cy - r); });
      g.addEventListener('blur', function () { tip.hide(); });
      ptsG.appendChild(g);
      nodes[p.row_uid] = g;
    });
    var order = pts.slice().sort(function (p, q) { return p.x - q.x || p.y - q.y; }).map(function (p) { return nodes[p.row_uid]; }).filter(function (x) { return !!x; });
    var drill = opts.onDrill || function (uid) { CC.select(uid); };
    var nav = CC.roving(order, {
      horizontal: true,
      onActivate: function (uid) { if (uid) { drill(uid); } },
      onFocus: function (uid) { CC.highlight(uid, 'bubble'); },
      onBlur: function () { CC.highlight(null, 'bubble'); }
    });
    var wrap = h('figure', { 'class': 'plot-wrap bu-wrap', dataset: { plot: 'bubble' } },
      h('div', { 'class': 'plot-scroll' }, svgEl),
      h('figcaption', { 'class': 'pl-legend' },
        cat ? h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-point', 'aria-hidden': 'true' }, '●'), ' ', t('plots.bubble.legendPointCat'))
          : h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-point', 'aria-hidden': 'true' }, '◯'), ' ', t('plots.bubble.legendBubble')),
        cat ? h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-diamond', 'aria-hidden': 'true' }, '◆'), ' ', t('plots.bubble.legendGroup')) : null,
        !cat && b.line && b.line.length ? h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym bu-sym-line', 'aria-hidden': 'true' }, '━'), ' ',
          b.line_label ? CC.txt(b.line_label, '') : t('plots.bubble.legendLine')) : null,
        poly ? h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-swatch bu-swatch', 'aria-hidden': 'true' }), ' ',
          b.band_label ? CC.txt(b.band_label, '') : t('plots.bubble.legendBand')) : null,
        pi.length > 1 ? h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym bu-sym-pi', 'aria-hidden': 'true' }, '┄'), ' ',
          b.pi_band_label ? CC.txt(b.pi_band_label, '') : t('plots.bubble.legendPi')) : null),
      b.coef_text ? h('p', { 'class': 'pl-text', id: 'bubble-coef' }, MA.ui.num(b.coef_text)) : null,
      // kezdőknek: a motor magyarázata (note), ennek hiányában a felület rövid súgója
      b.note ? h('p', { 'class': 'pl-text muted', id: 'bubble-note' }, CC.txt(b.note, '')) : h('p', { 'class': 'pl-text muted' }, t(cat ? 'plots.bubble.helpCat' : 'plots.bubble.help')),
      h('p', { 'class': 'pl-text muted' }, t('plots.bubble.keys')));
    MA.dom.mount(container, wrap);
    CC.highlightSync(wrap, opts.signal);
    return { svg: svgEl, layout: L, focus: nav.focus, items: order, el: wrap, polygon: poly, pi: pi };
  }

  MA.plots.bubble = { render: render, layout: layout, available: available, bandPolygon: bandPolygon };

  // ================================================================ önteszt (?selftest=1)
  MA.selftest.register('buborékábra: a motor sávja és egyenese pixelre, terület-arányos buborék', function (tt) {
    var plot = { measure: 'RR', scale: { analysis: 'log', ratio: true, null_analysis: 0 }, studies: [],
      bubble: { moderator: { name: 'ablat', label: { hu: 'szélesség', en: 'latitude' }, type: 'continuous' },
        x_axis: { domain: [10, 60], ticks: [{ at: 20, text: '20' }, { at: 40, text: '40' }], title: { hu: 'szélesség', en: 'latitude' } },
        y_axis: { domain: [-2, 1], ticks: [{ at: -1.6094, text: '0.2' }, { at: 0, text: '1' }], title: { hu: 'RR', en: 'RR' } },
        points: [{ row_uid: 'rbu0001', label: 'P1', x: 20, y: -0.3, weight_pct: 25, x_text: '20', display_text: { hu: '0.74', en: '0.74' } },
          { row_uid: 'rbu0002', label: 'P2', x: 50, y: -1.5, weight_pct: 6.25, x_text: '50', display_text: { hu: '0.22', en: '0.22' } }],
        line: [[10, 0.2], [60, -1.4]], band: [[10, -0.1, 0.5], [35, -0.8, -0.4], [60, -1.9, -0.9]],
        pi_band: [[10, -0.6, 1.0], [60, -2.4, -0.4]], line_label: { hu: 'Illesztett egyenes', en: 'Fitted line' },
        coef_text: { hu: 'meredekség −0.03', en: 'slope −0.03' } } };
    var host = h('div', { 'class': 'mg-selftest-host' });
    document.body.appendChild(host);
    try {
      var r = render(host, plot, {});
      tt.ok(!!r, 'kirajzolva');
      var L = r.layout;
      var c1 = host.querySelector('[data-uid="rbu0001"] .bu-bubble');
      var c2 = host.querySelector('[data-uid="rbu0002"] .bu-bubble');
      tt.near(Number(c1.getAttribute('cx')), L.sx(20), 0.06, 'x a moderátor értékén');
      tt.near(Number(c1.getAttribute('cy')), L.sy(-0.3), 0.06, 'y a hatáson (elemzési skála)');
      tt.near(Number(c1.getAttribute('r')), 2 * Number(c2.getAttribute('r')), 0.11, 'négyszeres súly → kétszeres sugár (terület-arányos)');
      tt.eq(r.polygon.length, 6, 'a sáv poligonja a motor 3 rácspontjából (felső + alsó él)');
      tt.deepEq(r.polygon[0], [10, 0.5], 'a felső él az első rácspont felső határával indul');
      tt.deepEq(r.polygon[5], [10, -0.1], 'az alsó él az első rácspont alsó határával zárul');
      tt.ok(host.querySelector('path.bu-line').getAttribute('d').indexOf('M') === 0, 'az illesztett egyenes a motor pontjain');
      tt.eq(host.querySelectorAll('path.bu-pi').length, 2, 'predikciós sáv: két szaggatott él a motor rácsán');
      tt.ok(host.textContent.indexOf('Illesztett egyenes') >= 0 || host.textContent.indexOf('Fitted line') >= 0, 'a jelmagyarázat a motor címkéje');
      tt.ok(host.textContent.indexOf('meredekség −0.03') >= 0 || host.textContent.indexOf('slope −0.03') >= 0, 'a koefficiens-szöveg a motoré');
    } finally {
      host.parentNode.removeChild(host);
    }
  });
})();
