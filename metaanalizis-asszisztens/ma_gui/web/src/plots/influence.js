/* plots/influence.js — befolyás-diagnosztika (terv 3.5.8): táblázat + kis ábrák (rstudent, Cook, hat).
 *
 * A számszövegek a motor <mező>_text mezői; a „befolyásos” és „kiugró” jelzés a motor metafor-
 * kritériumaiból jön (influential / outlier) — a felület küszöböt nem számol. A kis ábrák tengelyét
 * (tartomány, tickek, referenciavonalak) a motor influence_axes mezője adja; ha nincs, a tartomány a
 * pixel-elrendezéshez az adatok terjedelme (MA.geom), tick-felirat nélkül.
 *
 * MA.plots.influence.render(container, plot, opts) → {table, panels[]} | null
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var S = MA.dom.svg;
  var G = MA.geom;
  var C = MA.plots.common;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  var COLS = ['rstudent', 'dffits', 'cook_d', 'cov_ratio', 'hat', 'dfbetas'];
  var PANELS = ['rstudent', 'cook_d', 'hat'];
  var PW = 300;
  var ROW = 18;
  var PX0 = 116;
  var PX1 = PW - 14;

  function colTitle(key) { return MA.i18n.has('plots.influence.col.' + key) ? t('plots.influence.col.' + key) : key; }

  function flagCell(e) {
    var out = [];
    if (e.influential) { out.push(MA.ui.badge('warning', t('plots.flag.influential'), { symbol: '▲' })); }
    if (e.outlier) { out.push(MA.ui.badge('info', t('plots.flag.outlier'), { symbol: '⚑' })); }
    return out.length ? out : h('span', { 'class': 'muted' }, '–');
  }

  function axisFor(plot, key, list) {
    var ax = plot.influence_axes && plot.influence_axes[key];
    if (ax && Array.isArray(ax.domain)) { return ax; }
    var vals = list.map(function (e) { return e[key]; }).filter(G.isNum);
    var lo = G.min.apply(null, [0].concat(vals));
    var hi = G.max.apply(null, [0].concat(vals));
    if (lo === hi) { hi = lo + 1; }
    return { domain: [lo, hi], ticks: [], title: null, refs: [] };
  }

  function panel(plot, key, list, byUid, onDrill) {
    var ax = axisFor(plot, key, list);
    var H = 30 + list.length * ROW + 44;
    var sx = G.linear(ax.domain, [PX0, PX1]);
    var y0 = 26;
    var svgEl = C.svgRoot(PW, H, colTitle(key), 'inf-svg');
    svgEl.appendChild(S('text', { 'class': 'inf-title', x: '6', y: '16' }, C.txt(ax.title, colTitle(key))));
    var zero = sx.inDomain(0) ? sx(0) : PX0;
    (ax.refs || []).forEach(function (r) {
      if (!sx.inDomain(r.at)) { return; }
      var x = G.snap(sx(r.at));
      svgEl.appendChild(S('line', { 'class': 'inf-ref', x1: G.px(x), x2: G.px(x), y1: G.px(y0 - 4), y2: G.px(y0 + list.length * ROW), 'data-at': String(r.at) }));
      if (r.text) {
        svgEl.appendChild(S('text', { 'class': 'inf-ref-label', x: G.px(G.min(x, PW - 4)), y: G.px(y0 + list.length * ROW + 40),
          'text-anchor': x > PW * 0.6 ? 'end' : 'middle' }, C.txt(r.text, '')));
      }
    });
    list.forEach(function (e, i) {
      var cy = y0 + i * ROW + ROW / 2;
      var s = byUid[e.row_uid] || {};
      var v = e[key];
      var g = S('g', { 'class': ['inf-row', e.influential ? 'is-influential' : '', e.outlier ? 'is-outlier' : ''].join(' '), 'data-uid': e.row_uid,
        'data-v': String(v) });
      g.appendChild(S('rect', { 'class': 'fp-rowbg', x: '1', y: G.px(cy - ROW / 2 + 1), width: G.px(PW - 2), height: G.px(ROW - 2), rx: '2', ry: '2' }));
      g.appendChild(S('text', { 'class': 'inf-label', x: '6', y: G.px(cy + 4) }, C.clip(e.label || s.label || e.row_uid, 17)));
      if (G.isNum(v)) {
        var x = sx(G.clamp(v, G.min(ax.domain[0], ax.domain[1]), G.max(ax.domain[0], ax.domain[1])));
        var yy = G.snap(cy);
        g.appendChild(S('line', { 'class': 'inf-bar', x1: G.px(zero), x2: G.px(x), y1: G.px(yy), y2: G.px(yy) }));
        if (e.influential) {
          g.appendChild(S('polygon', { 'class': 'inf-mark is-flag', points: [G.px(x) + ',' + G.px(cy - 6), G.px(x + 5.5) + ',' + G.px(cy + 4), G.px(x - 5.5) + ',' + G.px(cy + 4)].join(' ') }));
        } else {
          g.appendChild(S('circle', { 'class': 'inf-mark', cx: G.px(x), cy: G.px(cy), r: '3.5' }));
        }
      }
      g.addEventListener('mouseenter', function () { C.highlight(e.row_uid, 'influence'); });
      g.addEventListener('mouseleave', function () { C.highlight(null, 'influence'); });
      g.addEventListener('click', function () { onDrill(e.row_uid); });
      svgEl.appendChild(g);
    });
    var gAxis = S('g', { 'class': 'fp-axis' });
    C.bottomAxis(gAxis, ax, sx, y0 + list.length * ROW + 2, { x0: PX0, x1: PX1, title: null });
    svgEl.appendChild(gAxis);
    return h('figure', { 'class': 'inf-panel', dataset: { stat: key } }, svgEl);
  }

  function render(container, plot, opts) {
    opts = opts || {};
    var list = plot && Array.isArray(plot.influence) ? plot.influence : [];
    if (!list.length) {
      MA.dom.mount(container, MA.ui.emptyState('plots.influence.none'));
      return null;
    }
    var byUid = C.studyIndex(plot);
    var drill = opts.onDrill || function (uid) { C.select(uid); };
    var rows = [];
    var table = h('table', { 'class': 'table table-compact inf-table' },
      h('caption', { 'class': 'sr-only' }, t('plots.influence.caption')),
      h('thead', null, h('tr', null,
        h('th', { scope: 'col' }, t('plots.forest.study')),
        COLS.map(function (k) { return h('th', { scope: 'col', 'class': 'num' }, colTitle(k)); }),
        h('th', { scope: 'col' }, t('plots.influence.col.flag')))),
      h('tbody', null, list.map(function (e) {
        var s = byUid[e.row_uid] || {};
        var btn = h('button', { type: 'button', 'class': 'btn-link inf-drill', 'data-uid': e.row_uid,
          onclick: function () { drill(e.row_uid); },
          onfocus: function () { C.highlight(e.row_uid, 'influence'); },
          onblur: function () { C.highlight(null, 'influence'); } }, e.label || s.label || e.row_uid);
        var tr = h('tr', { 'data-uid': e.row_uid, 'class': { 'is-flagged': !!(e.influential || e.outlier) },
          onmouseenter: function () { C.highlight(e.row_uid, 'influence'); },
          onmouseleave: function () { C.highlight(null, 'influence'); } },
        h('th', { scope: 'row' }, btn),
        COLS.map(function (k) { return h('td', { 'class': 'num' }, C.txt(e[k + '_text'])); }),
        h('td', null, flagCell(e)));
        rows.push(tr);
        return tr;
      })));
    var panels = PANELS.map(function (k) { return panel(plot, k, list, byUid, drill); });
    var wrap = h('figure', { 'class': 'plot-wrap inf-wrap', dataset: { plot: 'influence' } },
      plot.influence_text ? h('p', { 'class': 'pl-headline inf-summary' }, C.txt(plot.influence_text, '')) : null,
      h('div', { 'class': 'table-wrap' }, table),
      h('div', { 'class': 'inf-panels' }, panels),
      h('p', { 'class': 'pl-legend' },
        h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-point', 'aria-hidden': 'true' }, '●'), ' ', t('plots.influence.keyStudy')),
        h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-flag', 'aria-hidden': 'true' }, '▲'), ' ', t('plots.flag.influential')),
        h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-ref', 'aria-hidden': 'true' }, '┆'), ' ', t('plots.influence.keyRef'))),
      plot.influence_note ? h('p', { 'class': 'pl-text muted' }, C.txt(plot.influence_note, '')) : null);
    MA.dom.mount(container, wrap);
    C.highlightSync(wrap, opts.signal);
    return { table: table, rows: rows, panels: panels, el: wrap,
      focus: function (uid) {
        var b = MA.dom.$('.inf-drill[data-uid="' + uid + '"]', table);
        if (b) { b.focus(); return true; }
        return false;
      } };
  }

  MA.plots.influence = { render: render };
})();
