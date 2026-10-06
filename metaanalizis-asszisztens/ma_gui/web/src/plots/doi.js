/* plots/doi.js — Doi-plot és LFK-index (terv 3.5.8; Furuya-Kanamori, Barendregt & Doi 2018).
 *
 * x = hatás az elemzési skálán, y = |Z| fordított tengellyel (0 felül); a pontok a motor sorrendjében
 * (hatás szerint) összekötve. Az LFK-index és a kategória a motor szövege (U+2212 mínusszal).
 *
 * MA.plots.doi.render(container, plot, opts) → {svg, layout, focus(uid)} | null
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var S = MA.dom.svg;
  var G = MA.geom;
  var C = MA.plots.common;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  var W = 660;
  var H = 450;
  var X0 = 78;
  var X1 = W - 20;
  var Y0 = 16;
  var Y1 = H - 70;

  function layout(plot) {
    var d = plot.doi;
    var ax = d.axis || plot.axis;
    var yAx = d.y_axis || { domain: [0, 1], ticks: [], title: null };
    return { W: W, H: H, x0: X0, x1: X1, y0: Y0, y1: Y1, axis: ax, yAxis: yAx,
      sx: G.linear(ax.domain, [X0, X1]), sy: G.linear(yAx.domain, [Y0, Y1]) };
  }

  function render(container, plot, opts) {
    opts = opts || {};
    var d = plot && plot.doi;
    if (!d || !Array.isArray(d.points) || !d.points.length) {
      MA.dom.mount(container, MA.ui.emptyState('plots.doi.none'));
      return null;
    }
    var L = layout(plot);
    var svgEl = C.svgRoot(W, H, t('plots.doi.aria'), 'doi-svg');
    svgEl.setAttribute('role', 'list');
    var gAxes = S('g', { 'class': 'doi-axes' });
    C.bottomAxis(gAxes, L.axis, L.sx, Y1, { x0: X0, x1: X1, title: L.axis.title, grid: [Y0, Y1] });
    C.leftAxis(gAxes, L.yAxis, L.sy, X0, { y0: Y0, y1: Y1, title: L.yAxis.title, grid: [X0, X1] });
    svgEl.appendChild(gAxes);
    svgEl.appendChild(S('rect', { 'class': 'pl-frame', x: G.px(X0), y: G.px(Y0), width: G.px(X1 - X0), height: G.px(Y1 - Y0) }));
    var nullA = plot.scale ? plot.scale.null_analysis : null;
    if (G.isNum(nullA) && L.sx.inDomain(nullA)) {
      var nx = G.snap(L.sx(nullA));
      svgEl.appendChild(S('line', { 'class': 'doi-null', x1: G.px(nx), x2: G.px(nx), y1: G.px(Y0), y2: G.px(Y1) }));
    }
    var pts = d.points.map(function (p) { return [p.x, p.abs_z]; });
    svgEl.appendChild(S('path', { 'class': 'doi-line', d: G.pathD(pts, L.sx, L.sy) }));
    var byUid = C.studyIndex(plot);
    var items = [];
    var gPts = S('g', { 'class': 'doi-points' });
    d.points.forEach(function (p) {
      var s = byUid[p.row_uid] || {};
      var g = S('g', { 'class': 'doi-pt', 'data-uid': p.row_uid, 'data-x': String(p.x), 'data-z': String(p.abs_z), tabindex: '-1', role: 'listitem',
        'aria-label': (s.label || p.row_uid) + ', ' + C.txt(s.display_text) },
      S('circle', { 'class': 'doi-point', cx: G.px(L.sx(p.x)), cy: G.px(L.sy(p.abs_z)), r: '4.5' }));
      gPts.appendChild(g);
      items.push(g);
    });
    svgEl.appendChild(gPts);
    var tip = C.tooltip(svgEl, W);
    function showTip(uid, el) {
      var s = byUid[uid] || {};
      var c = el.querySelector('circle');
      tip.show([s.label || uid, C.txt(s.display_text)], Number(c.getAttribute('cx')), Number(c.getAttribute('cy')));
    }
    var drill = opts.onDrill || function (uid) { C.select(uid); };
    var nav = C.roving(items, {
      onActivate: function (uid) { drill(uid); },
      onFocus: function (uid, el) { C.highlight(uid, 'doi'); showTip(uid, el); },
      onBlur: function () { tip.hide(); C.highlight(null, 'doi'); },
      horizontal: true
    });
    items.forEach(function (el) {
      el.addEventListener('mouseenter', function () { C.highlight(el.getAttribute('data-uid'), 'doi'); showTip(el.getAttribute('data-uid'), el); });
      el.addEventListener('mouseleave', function () { C.highlight(null, 'doi'); tip.hide(); });
    });
    var wrap = h('figure', { 'class': 'plot-wrap doi-wrap', dataset: { plot: 'doi' } },
      h('p', { 'class': 'pl-headline doi-lfk' }, C.txt(d.lfk_text, '')),
      h('div', { 'class': 'plot-scroll' }, svgEl),
      d.note ? h('p', { 'class': 'pl-text muted' }, C.txt(d.note, '')) : null);
    MA.dom.mount(container, wrap);
    C.highlightSync(wrap, opts.signal);
    return { svg: svgEl, layout: L, focus: nav.focus, items: items, el: wrap };
  }

  MA.plots.doi = { render: render, layout: layout };
})();
