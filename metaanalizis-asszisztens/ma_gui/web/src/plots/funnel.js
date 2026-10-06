/* plots/funnel.js — kontúr-javított funnel plot (terv 3.5.8, 4.6 funnel).
 *
 * x = hatás az elemzési skálán, y = SE (fordított: 0 felül). A kontúr-poligonokat (p = 0,10 / 0,05 /
 * 0,01), a pszeudo-CI töréspontjait, a középvonalat és a tengelyosztást a motor adja — a JS nem számol
 * x = c ± z·se-t, csak pixelre képez. Vizsgálat: kör; pótolt (trim-and-fill) pont: üres rombusz
 * (eltérő alak, nem csak szín). Minden pont <… data-uid data-x data-se>.
 *
 * MA.plots.funnel.render(container, plot, opts) → {svg, layout, focus(uid)} | null (nincs funnel-adat)
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
  var H = 470;
  var X0 = 78;
  var X1 = W - 20;
  var Y0 = 16;
  var Y1 = H - 70;

  function layout(plot) {
    var fu = plot.funnel;
    var ax = fu.axis || plot.axis;
    var yAx = fu.y_axis || { domain: [0, fu.se_max], ticks: [], title: null };
    return {
      W: W, H: H, x0: X0, x1: X1, y0: Y0, y1: Y1, axis: ax, yAxis: yAx,
      sx: G.linear(ax.domain, [X0, X1]),
      sy: G.linear(yAx.domain, [Y0, Y1])        // 0 (SE) felül
    };
  }

  function render(container, plot, opts) {
    opts = opts || {};
    var fu = plot && plot.funnel;
    if (!fu || !Array.isArray(fu.points) || !fu.points.length) {
      MA.dom.mount(container, MA.ui.emptyState('plots.funnel.none'));
      return null;
    }
    var L = layout(plot);
    var svgEl = C.svgRoot(W, H, t('plots.funnel.aria'), 'fn-svg');
    svgEl.setAttribute('role', 'list');
    var clipUrl = C.clipRect(svgEl, X0, Y0, X1 - X0, Y1 - Y0);
    var gArea = S('g', { 'class': 'fn-area', 'clip-path': clipUrl });
    svgEl.appendChild(gArea);
    var contours = (Array.isArray(fu.contours) ? fu.contours.slice() : []).sort(function (a, b) { return a.p - b.p; });
    if (contours.length) {
      // a legkülső sáv (p < legkisebb p) a háttér; befelé haladva világosodik
      gArea.appendChild(S('rect', { 'class': 'fn-band is-outside', x: G.px(X0), y: G.px(Y0), width: G.px(X1 - X0), height: G.px(Y1 - Y0) }));
      contours.forEach(function (c, i) {
        var bandCls = i === contours.length - 1 ? 'is-inner' : 'is-band-' + (i + 1);
        gArea.appendChild(S('polygon', { 'class': 'fn-contour ' + bandCls, 'data-p': String(c.p), points: G.points(c.polygon, L.sx, L.sy) }));
      });
    } else {
      gArea.appendChild(S('rect', { 'class': 'fn-band is-inner', x: G.px(X0), y: G.px(Y0), width: G.px(X1 - X0), height: G.px(Y1 - Y0) }));
    }
    if (Array.isArray(fu.pseudo_ci) && fu.pseudo_ci.length > 1) {
      gArea.appendChild(S('path', { 'class': 'fn-pseudo', d: G.pathD(fu.pseudo_ci, L.sx, L.sy) }));
    }
    var nullC = G.isNum(fu.contour_center) ? fu.contour_center : (plot.scale ? plot.scale.null_analysis : null);
    if (G.isNum(nullC) && L.sx.inDomain(nullC)) {
      var nx = G.snap(L.sx(nullC));
      gArea.appendChild(S('line', { 'class': 'fn-null', x1: G.px(nx), x2: G.px(nx), y1: G.px(Y0), y2: G.px(Y1) }));
    }
    if (G.isNum(fu.center) && L.sx.inDomain(fu.center)) {
      var cx = G.snap(L.sx(fu.center));
      gArea.appendChild(S('line', { 'class': 'fn-center', x1: G.px(cx), x2: G.px(cx), y1: G.px(Y0), y2: G.px(Y1) }));
    }
    svgEl.appendChild(S('rect', { 'class': 'pl-frame', x: G.px(X0), y: G.px(Y0), width: G.px(X1 - X0), height: G.px(Y1 - Y0) }));

    var gAxes = S('g', { 'class': 'fn-axes' });
    C.bottomAxis(gAxes, L.axis, L.sx, Y1, { x0: X0, x1: X1, title: L.axis.title });
    C.leftAxis(gAxes, L.yAxis, L.sy, X0, { y0: Y0, y1: Y1, title: L.yAxis.title });
    svgEl.appendChild(gAxes);

    var byUid = C.studyIndex(plot);
    var gPts = S('g', { 'class': 'fn-points' });
    var items = [];
    fu.points.forEach(function (p) {
      var s = byUid[p.row_uid] || {};
      var x = L.sx(p.x);
      var y = L.sy(p.se);
      var g = S('g', { 'class': 'fn-pt', 'data-uid': p.row_uid, 'data-x': String(p.x), 'data-se': String(p.se), tabindex: '-1', role: 'listitem',
        'aria-label': (s.label || p.row_uid) + ', ' + C.txt(s.display_text) },
      S('circle', { 'class': 'fn-point', cx: G.px(x), cy: G.px(y), r: '4.5' }));
      gPts.appendChild(g);
      items.push(g);
    });
    (fu.filled || []).forEach(function (p, i) {
      var x = L.sx(p.x);
      var y = L.sy(p.se);
      var d = 'M' + G.px(x) + ' ' + G.px(y - 6) + ' L' + G.px(x + 6) + ' ' + G.px(y) + ' L' + G.px(x) + ' ' + G.px(y + 6) + ' L' + G.px(x - 6) + ' ' + G.px(y) + ' Z';
      gPts.appendChild(S('path', { 'class': 'fn-filled', d: d, 'data-x': String(p.x), 'data-se': String(p.se), 'data-filled': String(i),
        'data-mirror': p.mirror_of || '', role: 'img', 'aria-label': C.txt(p.label, t('plots.funnel.filled')) }));
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
      onFocus: function (uid, el) { C.highlight(uid, 'funnel'); showTip(uid, el); },
      onBlur: function () { tip.hide(); C.highlight(null, 'funnel'); },
      horizontal: true
    });
    items.forEach(function (el) {
      el.addEventListener('mouseenter', function () { C.highlight(el.getAttribute('data-uid'), 'funnel'); showTip(el.getAttribute('data-uid'), el); });
      el.addEventListener('mouseleave', function () { C.highlight(null, 'funnel'); tip.hide(); });
    });

    var keys = [
      h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-point', 'aria-hidden': 'true' }, '●'), ' ', t('plots.funnel.study')),
      (fu.filled || []).length ? h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-filled', 'aria-hidden': 'true' }, '◇'), ' ', t('plots.funnel.filled')) : null,
      fu.pseudo_ci ? h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-pseudo', 'aria-hidden': 'true' }, '┄'), ' ', t('plots.funnel.pseudo')) : null,
      h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-center', 'aria-hidden': 'true' }, '│'), ' ', C.txt(fu.center_label, t('plots.funnel.center')))
    ];
    if (contours.length) {
      keys.push(h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-swatch is-outside', 'aria-hidden': 'true' }), ' ', C.txt(fu.outside_text, '')));
      contours.forEach(function (c, i) {
        var bandCls = i === contours.length - 1 ? 'is-inner' : 'is-band-' + (i + 1);
        keys.push(h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-swatch ' + bandCls, 'aria-hidden': 'true' }), ' ', C.txt(c.band_text, '')));
      });
    }
    var wrap = h('figure', { 'class': 'plot-wrap fn-wrap', dataset: { plot: 'funnel' } },
      h('div', { 'class': 'plot-scroll' }, svgEl),
      h('p', { 'class': 'pl-legend' }, keys),
      fu.tests_text ? h('p', { 'class': 'pl-text fn-tests' }, C.txt(fu.tests_text, '')) : null,
      fu.trimfill_text ? h('p', { 'class': 'pl-text fn-trimfill' }, C.txt(fu.trimfill_text, '')) : null);
    MA.dom.mount(container, wrap);
    C.highlightSync(wrap, opts.signal);
    return { svg: svgEl, layout: L, focus: nav.focus, items: items, el: wrap };
  }

  MA.plots.funnel = { render: render, layout: layout };
})();
