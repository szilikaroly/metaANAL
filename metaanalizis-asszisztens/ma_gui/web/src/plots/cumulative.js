/* plots/cumulative.js — kumulatív forest (terv 3.5.8, 4.6 cumulative; v1 E4c).
 *
 * A motor szk.ma.plot/v2 'cumulative' blokkjának pixelre képezése: minden sor a rendező kulcs (pl. év) szerinti
 * k-adik kumulatív összesítés. A tengely (cumulative.axis, ennek hiányában plot.axis), a tickek, a becslések és a
 * CI-k (elemzési skálán) és minden szöveg (display_text, i2_text, tau2_text, key_text, key_label, note) a motoré;
 * itt csak MA.geom-leképezés van, számformázás és számítás nincs. A k oszlop a motor egész száma (vagy k_text); a
 * rendezés leírása (order.text) és a kezdőknek szóló magyarázat (note) is a motoré.
 * Sorok: <g class="cu-row sr-row" data-uid (a hozzáadott vizsgálat) data-y data-lo data-hi data-k> (az sr-row osztály a
 * sorozat-ábrával közös kiemelés-stílusokhoz); az utolsó sor (a teljes adat
 * összesítése) gyémánt, a referencia-vonal ennek a becslése (a motor száma). A szín sosem egyedüli jelölés: a végső
 * sor alakja (gyémánt) és felirata is jelöl.
 *
 * MA.plots.cumulative.render(container, plot, {onDrill, signal}) → {svg, layout, focus, items, el} | null
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var S = MA.dom.svg;
  var G = MA.geom;
  var C = MA.plots.common;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  var W = 960;
  var ROW = 22;
  var TOP = 28;
  var X_LABEL = 8;
  var X_KEY = 262;
  var X_K = 300;
  var PX0 = 318;
  var PX1 = 610;
  var X_EFF = 626;
  var X_I2 = 864;
  var X_TAU = W - 10;

  function entriesOf(plot) {
    var c = plot && plot.cumulative;
    return c && Array.isArray(c.entries) ? c.entries : [];
  }

  function axisOf(plot) {
    return plot.cumulative && plot.cumulative.axis ? plot.cumulative.axis : plot.axis;
  }

  function layout(plot) {
    var ent = entriesOf(plot);
    var ax = axisOf(plot);
    var y0 = TOP + ROW;
    var axisY = y0 + ent.length * ROW + 10;
    return { W: W, H: axisY + 64, px0: PX0, px1: PX1, axis: ax, entries: ent, y0: y0, axisY: axisY,
      sx: G.linear(ax.domain, [PX0, PX1]), rowY: function (i) { return y0 + i * ROW; } };
  }

  function kText(e) {
    if (e.k_text) { return C.txt(e.k_text, ''); }
    return typeof e.k === 'number' ? String(e.k) : '';
  }

  function render(container, plot, opts) {
    opts = opts || {};
    if (!plot || !entriesOf(plot).length || !axisOf(plot)) {
      MA.dom.mount(container, MA.ui.emptyState('plots.cum.none'));
      return null;
    }
    var L = layout(plot);
    var cum = plot.cumulative;
    var byUid = C.studyIndex(plot);
    var svgEl = C.svgRoot(W, L.H, t('plots.cum.aria', { key: C.txt(cum.key_label, '') }), 'cu-svg');
    var hy = TOP + 10;
    svgEl.appendChild(S('g', { 'class': 'fp-head' },
      S('text', { 'class': 'fp-hcell', x: String(X_LABEL), y: G.px(hy) }, t('plots.cum.added')),
      S('text', { 'class': 'fp-hcell', x: G.px(X_KEY), y: G.px(hy), 'text-anchor': 'end' }, C.txt(cum.key_label, '')),
      S('text', { 'class': 'fp-hcell', x: G.px(X_K), y: G.px(hy), 'text-anchor': 'end' }, 'k'),
      S('text', { 'class': 'fp-hcell', x: G.px(X_EFF), y: G.px(hy) }, C.txt((plot.labels || {}).effect, plot.measure || '')),
      S('text', { 'class': 'fp-hcell', x: G.px(X_I2), y: G.px(hy), 'text-anchor': 'end' }, 'I²'),
      S('text', { 'class': 'fp-hcell', x: G.px(X_TAU), y: G.px(hy), 'text-anchor': 'end' }, 'τ²'),
      S('line', { 'class': 'fp-hline', x1: '4', x2: G.px(W - 4), y1: G.px(G.snap(hy + 8)), y2: G.px(G.snap(hy + 8)) })));
    // null-vonal és a végső (teljes adat) becslés referencia-vonala — mindkettő a motor száma
    var nullA = plot.scale ? plot.scale.null_analysis : null;
    if (G.isNum(nullA) && L.sx.inDomain(nullA)) {
      var nx = G.snap(L.sx(nullA));
      svgEl.appendChild(S('line', { 'class': 'fp-null', x1: G.px(nx), x2: G.px(nx), y1: G.px(L.y0 - 2), y2: G.px(L.axisY), 'data-at': String(nullA) }));
    }
    var last = L.entries[L.entries.length - 1];
    if (last && G.isNum(last.estimate) && L.sx.inDomain(last.estimate)) {
      var fx = G.snap(L.sx(last.estimate));
      svgEl.appendChild(S('line', { 'class': 'cu-final-line', x1: G.px(fx), x2: G.px(fx), y1: G.px(L.y0 - 2), y2: G.px(L.axisY),
        'data-est': String(last.estimate) }));
    }
    var items = [];
    L.entries.forEach(function (e, i) {
      var y = L.rowY(i);
      var cy = y + ROW / 2;
      var ty = cy + 4;
      var isLast = i === L.entries.length - 1;
      var s = byUid[e.added_row_uid] || {};
      var name = e.label || s.label || e.added_row_uid || '';
      var g = S('g', { 'class': ['cu-row sr-row', isLast ? 'is-final' : ''].join(' '), 'data-uid': e.added_row_uid || '', 'data-y': String(e.estimate),
        'data-lo': String(e.ci_lower), 'data-hi': String(e.ci_upper), 'data-k': kText(e), tabindex: '-1', role: 'listitem',
        'aria-label': t(isLast ? 'plots.cum.rowAriaFinal' : 'plots.cum.rowAria', { study: name, key: e.key_text || '', k: kText(e) }) + ', ' + C.txt(e.display_text) });
      g.appendChild(S('rect', { 'class': 'fp-rowbg', x: '2', y: G.px(y + 1), width: G.px(W - 4), height: G.px(ROW - 2), rx: '3', ry: '3' }));
      g.appendChild(S('text', { 'class': 'fp-label', x: String(X_LABEL), y: G.px(ty) }, '+ ' + C.clip(name, 34)));
      g.appendChild(S('text', { 'class': 'fp-cell', x: G.px(X_KEY), y: G.px(ty), 'text-anchor': 'end' }, e.key_text || ''));
      g.appendChild(S('text', { 'class': 'fp-cell', x: G.px(X_K), y: G.px(ty), 'text-anchor': 'end' }, kText(e)));
      var ci = G.clipInterval(e.ci_lower, e.ci_upper, L.sx);
      var yy = G.snap(cy);
      if (isLast && G.isNum(e.estimate) && L.sx.inDomain(e.estimate)) {
        g.appendChild(S('polygon', { 'class': 'fp-diamond cu-marker', points: C.diamond(ci.x1, L.sx(e.estimate), ci.x2, cy, 6) }));
      } else {
        g.appendChild(S('line', { 'class': 'fp-ci', x1: G.px(ci.x1), x2: G.px(ci.x2), y1: G.px(yy), y2: G.px(yy) }));
        if (G.isNum(e.estimate) && L.sx.inDomain(e.estimate)) {
          var cx = L.sx(e.estimate);
          g.appendChild(S('rect', { 'class': 'fp-square cu-marker', x: G.px(cx - 4), y: G.px(cy - 4), width: '8', height: '8' }));
        }
      }
      if (ci.clipLeft) { g.appendChild(S('polygon', { 'class': 'fp-arrow is-left', points: C.arrow(ci.x1, yy, -1, 4) })); }
      if (ci.clipRight) { g.appendChild(S('polygon', { 'class': 'fp-arrow is-right', points: C.arrow(ci.x2, yy, 1, 4) })); }
      g.appendChild(S('text', { 'class': 'fp-effect', x: G.px(X_EFF), y: G.px(ty) }, C.txt(e.display_text)));
      g.appendChild(S('text', { 'class': 'fp-weight', x: G.px(X_I2), y: G.px(ty), 'text-anchor': 'end' }, C.txt(e.i2_text, '')));
      g.appendChild(S('text', { 'class': 'fp-weight', x: G.px(X_TAU), y: G.px(ty), 'text-anchor': 'end' }, C.txt(e.tau2_text, '')));
      svgEl.appendChild(g);
      items.push(g);
    });
    var gAxis = S('g', { 'class': 'fp-axis' });
    C.bottomAxis(gAxis, L.axis, L.sx, L.axisY, { x0: PX0, x1: PX1, title: L.axis.title });
    svgEl.appendChild(gAxis);
    var drill = opts.onDrill || function (uid) { C.select(uid); };
    var nav = C.roving(items, {
      onActivate: function (uid) { if (uid) { drill(uid); } },
      onFocus: function (uid) { C.highlight(uid || null, 'cumulative'); },
      onBlur: function () { C.highlight(null, 'cumulative'); }
    });
    items.forEach(function (el) {
      el.addEventListener('mouseenter', function () { C.highlight(el.getAttribute('data-uid') || null, 'cumulative'); });
      el.addEventListener('mouseleave', function () { C.highlight(null, 'cumulative'); });
    });
    var wrap = h('figure', { 'class': 'plot-wrap cu-wrap', dataset: { plot: 'cumulative' } },
      h('div', { 'class': 'plot-scroll' }, svgEl),
      h('figcaption', { 'class': 'pl-legend' },
        h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-square', 'aria-hidden': 'true' }, '■'), ' ', t('plots.cum.legendStep')),
        h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-diamond', 'aria-hidden': 'true' }, '◆'), ' ',
          t('plots.cum.legendFinal', { text: last ? C.txt(last.display_text) : '—' })),
        h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym cu-dash', 'aria-hidden': 'true' }, '┆'), ' ', t('plots.cum.legendLine'))),
      cum.order && cum.order.text ? h('p', { 'class': 'pl-text', id: 'cumulative-order' }, C.txt(cum.order.text, '')) : null,
      // kezdőknek: a motor magyarázata (note), ennek hiányában a felület rövid súgója
      cum.note ? h('p', { 'class': 'pl-text muted', id: 'cumulative-note' }, C.txt(cum.note, '')) : h('p', { 'class': 'pl-text muted' }, t('plots.cum.help')),
      h('p', { 'class': 'pl-text muted' }, t('plots.cum.keys')));
    MA.dom.mount(container, wrap);
    C.highlightSync(wrap, opts.signal);
    return { svg: svgEl, layout: L, focus: nav.focus, items: items, el: wrap };
  }

  MA.plots.cumulative = { render: render, layout: layout };

  // ================================================================ önteszt (?selftest=1)
  MA.selftest.register('kumulatív forest: a motor számai pixelre (data-*), szövegek változatlanul', function (tt) {
    var plot = { measure: 'RR', scale: { analysis: 'log', ratio: true, null_analysis: 0 }, labels: {},
      axis: { domain: [-2, 1], ticks: [{ at: -2, text: '0.14' }, { at: 0, text: '1' }], title: { hu: 'RR', en: 'RR' } },
      studies: [{ row_uid: 'rcu0001', label: 'A 1948' }, { row_uid: 'rcu0002', label: '<b>B</b> 1950' }],
      cumulative: { key_label: { hu: 'év', en: 'year' }, axis: null, entries: [
        { added_row_uid: 'rcu0001', label: 'A 1948', key_text: '1948', k: 1, estimate: -0.9, ci_lower: -2.5, ci_upper: 0.2,
          display_text: { hu: '0.41 [0.08; 1.22]', en: '0.41 [0.08; 1.22]' }, i2_text: { hu: '–', en: '–' }, tau2_text: { hu: '–', en: '–' } },
        { added_row_uid: 'rcu0002', label: '<b>B</b> 1950', key_text: '1950', k: 2, estimate: -1.2, ci_lower: -1.8, ci_upper: -0.6,
          display_text: { hu: '0.30 [0.17; 0.55]', en: '0.30 [0.17; 0.55]' }, i2_text: { hu: '12%', en: '12%' }, tau2_text: { hu: '0.01', en: '0.01' } }] } };
    var host = h('div', { 'class': 'mg-selftest-host' });
    document.body.appendChild(host);
    try {
      var r = render(host, plot, {});
      tt.ok(!!r, 'kirajzolva');
      tt.eq(r.items.length, 2, 'két sor');
      var sx = r.layout.sx;
      var sq = r.items[0].querySelector('.cu-marker');
      tt.near(Number(sq.getAttribute('x')) + 4, sx(-0.9), 0.06, 'a négyzet a motor becslésén');
      tt.ok(r.items[1].querySelector('polygon.fp-diamond') !== null, 'a végső sor gyémánt');
      tt.eq(r.items[1].getAttribute('data-k'), '2', 'k a motor egész száma');
      tt.ok(r.svg.textContent.indexOf('0.30 [0.17; 0.55]') >= 0, 'a motor display_text-je változatlanul');
      tt.ok(r.items[0].querySelector('.fp-ci').getAttribute('x1') !== null, 'CI-vonal');
      tt.eq(r.svg.querySelectorAll('b').length, 0, 'a címke szövegként');
      tt.eq(r.svg.querySelector('.cu-final-line').getAttribute('data-est'), '-1.2', 'a referencia a végső becslés');
    } finally {
      host.parentNode.removeChild(host);
    }
  });
})();
