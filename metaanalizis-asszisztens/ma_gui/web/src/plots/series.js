/* plots/series.js — leave-one-out és kumulatív sorozat forest-szerű ábrán (terv 3.5.8, 4.6 loo/cumulative).
 *
 * Minden sor a motor kész display_text-je (és i2_text-je); a referencia az elsődleges összesített
 * becslés (függőleges vonal + CI-sáv). Tengely: plot.loo_axis / plot.cumulative.axis, ha a motor
 * adja, különben plot.axis. Sorok: <g data-uid data-y data-lo data-hi> (LOO: a kihagyott vizsgálat).
 *
 * MA.plots.series.render(container, plot, {kind: 'loo'|'cumulative', onDrill, signal}) → {...} | null
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var S = MA.dom.svg;
  var G = MA.geom;
  var C = MA.plots.common;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  var W = 900;
  var ROW = 22;
  var TOP = 26;
  var LABEL_W = 250;
  var PX0 = 280;
  var PX1 = 620;

  function entriesOf(plot, kind) {
    if (kind === 'cumulative') {
      return plot.cumulative && Array.isArray(plot.cumulative.entries) ? plot.cumulative.entries.map(function (e) {
        return { uid: e.added_row_uid, label: e.label, key: e.key_text, k: e.k, y: e.estimate, lo: e.ci_lower, hi: e.ci_upper,
          text: e.display_text, extra: e.i2_text };
      }) : [];
    }
    return Array.isArray(plot.loo) ? plot.loo.map(function (e) {
      return { uid: e.omitted_row_uid, label: e.label, y: e.estimate, lo: e.ci_lower, hi: e.ci_upper, text: e.display_text, extra: e.i2_text };
    }) : [];
  }

  function axisOf(plot, kind) {
    if (kind === 'cumulative' && plot.cumulative && plot.cumulative.axis) { return plot.cumulative.axis; }
    if (kind === 'loo' && plot.loo_axis) { return plot.loo_axis; }
    return plot.axis;
  }

  function layout(plot, kind) {
    var ent = entriesOf(plot, kind);
    var ax = axisOf(plot, kind);
    var y0 = TOP + ROW;
    var axisY = y0 + ent.length * ROW + 10;
    return { W: W, H: axisY + 60, px0: PX0, px1: PX1, sx: G.linear(ax.domain, [PX0, PX1]), axis: ax, entries: ent, y0: y0, axisY: axisY,
      rowY: function (i) { return y0 + i * ROW; } };
  }

  function render(container, plot, opts) {
    opts = opts || {};
    var kind = opts.kind === 'cumulative' ? 'cumulative' : 'loo';
    var L = layout(plot, kind);
    if (!L.entries.length) {
      MA.dom.mount(container, MA.ui.emptyState(kind === 'loo' ? 'plots.loo.none' : 'plots.cumulative.none'));
      return null;
    }
    var byUid = C.studyIndex(plot);
    var svgEl = C.svgRoot(W, L.H, t(kind === 'loo' ? 'plots.loo.aria' : 'plots.cumulative.aria'), 'sr-svg');
    svgEl.setAttribute('role', 'list');
    var hy = TOP + 12;
    var head = S('g', { 'class': 'fp-head' },
      S('text', { 'class': 'fp-hcell', x: '8', y: G.px(hy) }, t(kind === 'loo' ? 'plots.loo.omitted' : 'plots.cumulative.added')),
      kind === 'cumulative' ? S('text', { 'class': 'fp-hcell', x: G.px(LABEL_W + 18), y: G.px(hy), 'text-anchor': 'end' },
        C.txt(plot.cumulative.key_label, '')) : null,
      S('text', { 'class': 'fp-hcell', x: G.px(PX1 + 18), y: G.px(hy) }, C.txt((plot.labels || {}).effect, plot.measure || '')),
      S('text', { 'class': 'fp-hcell', x: G.px(W - 12), y: G.px(hy), 'text-anchor': 'end' }, 'I²'),
      S('line', { 'class': 'fp-hline', x1: '4', x2: G.px(W - 4), y1: G.px(G.snap(hy + 8)), y2: G.px(G.snap(hy + 8)) }));
    svgEl.appendChild(head);
    // referencia: az elsődleges összesített becslés és CI-je
    var sums = plot.summaries || [];
    var prim = sums.filter(function (s) { return s.primary === true; })[0] || sums[0] || null;
    if (prim && G.isNum(prim.ci_lower) && G.isNum(prim.ci_upper)) {
      var band = G.clipInterval(prim.ci_lower, prim.ci_upper, L.sx);
      svgEl.appendChild(S('rect', { 'class': 'sr-band', x: G.px(band.x1), y: G.px(L.y0 - 2), width: G.px(G.max(1, band.x2 - band.x1)),
        height: G.px(L.axisY - L.y0 - 4) }));
      if (L.sx.inDomain(prim.estimate)) {
        var ex = G.snap(L.sx(prim.estimate));
        svgEl.appendChild(S('line', { 'class': 'sr-overall', x1: G.px(ex), x2: G.px(ex), y1: G.px(L.y0 - 2), y2: G.px(L.axisY), 'data-est': String(prim.estimate) }));
      }
    }
    var nullA = plot.scale ? plot.scale.null_analysis : null;
    if (G.isNum(nullA) && L.sx.inDomain(nullA)) {
      var nx = G.snap(L.sx(nullA));
      svgEl.appendChild(S('line', { 'class': 'fp-null', x1: G.px(nx), x2: G.px(nx), y1: G.px(L.y0 - 2), y2: G.px(L.axisY) }));
    }
    var items = [];
    L.entries.forEach(function (e, i) {
      var y = L.rowY(i);
      var cy = y + ROW / 2;
      var ty = cy + 4;
      var s = byUid[e.uid] || {};
      var name = e.label || s.label || e.uid;
      var g = S('g', { 'class': 'sr-row', 'data-uid': e.uid, 'data-y': String(e.y), 'data-lo': String(e.lo), 'data-hi': String(e.hi),
        tabindex: '-1', role: 'listitem',
        'aria-label': (kind === 'loo' ? t('plots.loo.rowAria', { study: name }) : t('plots.cumulative.rowAria', { study: name, key: e.key || '' })) + ', ' + C.txt(e.text) });
      g.appendChild(S('rect', { 'class': 'fp-rowbg', x: '2', y: G.px(y + 1), width: G.px(W - 4), height: G.px(ROW - 2), rx: '3', ry: '3' }));
      g.appendChild(S('text', { 'class': 'fp-label', x: '8', y: G.px(ty) }, (kind === 'loo' ? '− ' : '+ ') + C.clip(name, 34)));
      if (kind === 'cumulative') {
        g.appendChild(S('text', { 'class': 'fp-cell', x: G.px(LABEL_W + 18), y: G.px(ty), 'text-anchor': 'end' }, e.key || ''));
      }
      var ci = G.clipInterval(e.lo, e.hi, L.sx);
      var yy = G.snap(cy);
      g.appendChild(S('line', { 'class': 'fp-ci', x1: G.px(ci.x1), x2: G.px(ci.x2), y1: G.px(yy), y2: G.px(yy) }));
      if (ci.clipLeft) { g.appendChild(S('polygon', { 'class': 'fp-arrow is-left', points: C.arrow(ci.x1, yy, -1, 4) })); }
      if (ci.clipRight) { g.appendChild(S('polygon', { 'class': 'fp-arrow is-right', points: C.arrow(ci.x2, yy, 1, 4) })); }
      if (L.sx.inDomain(e.y)) {
        var cx = L.sx(e.y);
        g.appendChild(S('rect', { 'class': 'fp-square sr-marker', x: G.px(cx - 4), y: G.px(cy - 4), width: '8', height: '8' }));
      }
      g.appendChild(S('text', { 'class': 'fp-effect', x: G.px(PX1 + 18), y: G.px(ty) }, C.txt(e.text)));
      g.appendChild(S('text', { 'class': 'fp-weight', x: G.px(W - 12), y: G.px(ty), 'text-anchor': 'end' }, C.txt(e.extra)));
      svgEl.appendChild(g);
      items.push(g);
    });
    var gAxis = S('g', { 'class': 'fp-axis' });
    C.bottomAxis(gAxis, L.axis, L.sx, L.axisY, { x0: PX0, x1: PX1, title: L.axis.title });
    svgEl.appendChild(gAxis);
    var drill = opts.onDrill || function (uid) { C.select(uid); };
    var nav = C.roving(items, {
      onActivate: function (uid) { drill(uid); },
      onFocus: function (uid) { C.highlight(uid, 'series'); },
      onBlur: function () { C.highlight(null, 'series'); }
    });
    items.forEach(function (el) {
      el.addEventListener('mouseenter', function () { C.highlight(el.getAttribute('data-uid'), 'series'); });
      el.addEventListener('mouseleave', function () { C.highlight(null, 'series'); });
    });
    var wrap = h('figure', { 'class': 'plot-wrap sr-wrap', dataset: { plot: kind } },
      h('div', { 'class': 'plot-scroll' }, svgEl),
      h('p', { 'class': 'pl-legend' },
        h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-band', 'aria-hidden': 'true' }, '▮'), ' ',
          t('plots.series.reference', { text: prim ? C.txt(prim.display_text) : '—' }))));
    MA.dom.mount(container, wrap);
    C.highlightSync(wrap, opts.signal);
    return { svg: svgEl, layout: L, focus: nav.focus, items: items, el: wrap };
  }

  MA.plots.series = { render: render, layout: layout };
})();
