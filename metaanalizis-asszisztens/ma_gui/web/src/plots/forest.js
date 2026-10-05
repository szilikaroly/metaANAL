/* plots/forest.js — interaktív forest plot a szk.ma.plot/v2-ből (terv 3.5.7, 4.6).
 *
 * Súlyozott négyzetek (terület ∝ weight_pct), CI-vonalak levágott nyíllal, gyémántok (alcsoport- és
 * összesített becslés), PI-sáv, alcsoport-szakaszok + alcsoport-összesítések + alcsoport-különbség
 * szövege, szövegoszlopok a 'cells'-ből, tengely a motor tickjeiből, nullvonal. Minden vizsgálat
 * <g data-uid data-y data-lo data-hi>; kattintás / Enter → lefúrás (opts.onDrill).
 * Minden számszöveg a motoré (display_text, weight_text, pi_text, het_text, ticks[].text).
 *
 * MA.plots.forest.render(container, plot, opts) → {svg, layout, focus(uid)}
 *   opts: {layers: {pi, subgroups, fixed, estimated}, onDrill(uid), signal, label}
 * MA.plots.forest.layout(plot, layers) → geometria (önteszthez)
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var S = MA.dom.svg;
  var G = MA.geom;
  var C = MA.plots.common;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  var W = 980;
  var EST_SYM = '≈';      // a becsült érték jele (a ◆ az összesített becslésé, UX-07)
  var ROW = 22;
  var TOP = 26;
  var LABEL_X = 8;
  var LABEL_W = 214;
  var COL_W = 82;
  var RIGHT_W = 262;
  var ROB_SYM = { low: '●', some: '◐', high: '○', critical: '⊘' };
  var DEFAULT_LAYERS = { pi: true, subgroups: true, fixed: false, estimated: true };

  function layersOf(opts) {
    return Object.assign({}, DEFAULT_LAYERS, (opts && opts.layers) || {});
  }

  function primarySummary(plot) {
    var sums = plot.summaries || [];
    var p = sums.filter(function (s) { return s.primary === true; })[0];
    return p || sums.filter(function (s) { return s.kind === 'overall'; })[0] || null;
  }

  function buildRows(plot, layers) {
    var rows = [];
    var byUid = C.studyIndex(plot);
    var sections = Array.isArray(plot.sections) ? plot.sections : [];
    var useSections = layers.subgroups && sections.length > 0;
    if (useSections) {
      var seen = {};
      sections.forEach(function (sec) {
        rows.push({ type: 'section', sec: sec });
        (sec.row_uids || []).forEach(function (u) {
          if (byUid[u] && !seen[u]) { rows.push({ type: 'study', s: byUid[u], indent: true }); seen[u] = true; }
        });
        if (sec.summary) { rows.push({ type: 'subtotal', sm: sec.summary, sec: sec }); }
        rows.push({ type: 'gap' });
      });
      (plot.studies || []).forEach(function (s) { if (!seen[s.row_uid]) { rows.push({ type: 'study', s: s }); } });
    } else {
      (plot.studies || []).forEach(function (s) { rows.push({ type: 'study', s: s }); });
      rows.push({ type: 'gap' });
    }
    var prim = primarySummary(plot);
    (plot.summaries || []).forEach(function (sm) {
      if (sm.kind === 'subgroup') { return; }
      if (sm !== prim && sm.kind === 'overall' && sm.model === 'fixed' && !layers.fixed) { return; }
      rows.push({ type: 'summary', sm: sm, primary: sm === prim });
    });
    if (layers.pi && prim && G.isNum(prim.pi_lower) && G.isNum(prim.pi_upper)) { rows.push({ type: 'pi', sm: prim }); }
    if (plot.heterogeneity && plot.heterogeneity.text) { rows.push({ type: 'note', text: plot.heterogeneity.text, cls: 'fp-het' }); }
    if (useSections && plot.subgroup_test && plot.subgroup_test.text) {
      rows.push({ type: 'note', text: plot.subgroup_test.text, cls: 'fp-sgtest' });
    }
    return rows;
  }

  /** layout(plot, layers) → {W, H, px0, px1, sx, rows, axisY, colX[], effX, weightX, robX, rowY(i)} */
  function layout(plot, layers) {
    var lay = Object.assign({}, DEFAULT_LAYERS, layers || {});
    var cols = plot.columns || [];
    var px0 = LABEL_X + LABEL_W + cols.length * COL_W + 16;
    var px1 = W - RIGHT_W;
    var sx = G.linear(plot.axis.domain, [px0, px1]);
    var rows = buildRows(plot, lay);
    var y0 = TOP + ROW + 6;
    var axisY = y0 + rows.length * ROW + 8;
    var colX = cols.map(function (c, j) { return LABEL_X + LABEL_W + j * COL_W; });
    return {
      W: W, H: axisY + 76, px0: px0, px1: px1, sx: sx, rows: rows, axisY: axisY, y0: y0, colX: colX,
      effX: px1 + 18, weightX: W - 40, robX: W - 16, layers: lay,
      rowY: function (i) { return y0 + i * ROW; }
    };
  }

  // oszlopfejléc tördelése (pl. „Esemény/N (1. kar)” 82 px-en): szóhatáron legfeljebb két sorra, hogy a szomszédos
  // jobbra igazított fejlécek ne fedjék egymást; a sorok a fejléc-alapvonal fölé nőnek (a táblázat nem tolódik)
  var HEAD_CHARS = 12;
  function headLines(text) {
    var words = String(text || '').split(' ');
    if (String(text || '').length <= HEAD_CHARS || words.length < 2) { return [String(text || '')]; }
    var first = words[0];
    var i = 1;
    while (i < words.length - 1 && (first + ' ' + words[i]).length <= HEAD_CHARS) { first += ' ' + words[i]; i += 1; }
    return [first, words.slice(i).join(' ')];
  }

  function headCell(x, y, text, anchor) {
    var lines = headLines(text);
    var attrs = { 'class': 'fp-hcell', x: G.px(x), y: G.px(y - (lines.length - 1) * 12) };
    if (anchor) { attrs['text-anchor'] = anchor; }
    if (lines.length === 1) { return S('text', attrs, lines[0]); }
    // a sorvégi szóköz a szöveg-tartalomban megmarad (képernyőolvasó, keresés), a rajzon nem látszik
    return S('text', attrs, S('tspan', { x: G.px(x) }, lines[0] + ' '), S('tspan', { x: G.px(x), dy: '12' }, lines[1]));
  }

  function robLabel(rob) {
    return rob && MA.i18n.has('plots.rob.' + rob) ? t('plots.rob.' + rob) : t('plots.rob.none');
  }

  function studyAria(s, layers) {
    var parts = [s.label, C.txt(s.display_text)];
    if (s.weight_text) { parts.push(t('plots.forest.weightAria', { w: C.txt(s.weight_text) })); }
    if (s.flags && s.flags.rob) { parts.push(t('plots.forest.robAria', { rob: robLabel(s.flags.rob) })); }
    if (layers.estimated && s.flags && s.flags.estimated) { parts.push(t('plots.flag.estimated')); }
    if (s.flags && s.flags.outlier) { parts.push(t('plots.flag.outlier')); }
    if (s.flags && s.flags.influential) { parts.push(t('plots.flag.influential')); }
    return parts.join(', ');
  }

  function ciLine(g, lo, hi, sx, cy, engineClip, cls) {
    var ci = G.clipInterval(lo, hi, sx);
    var cl = ci.clipLeft || !!(engineClip && engineClip.left);
    var cr = ci.clipRight || !!(engineClip && engineClip.right);
    var yy = G.snap(cy);
    g.appendChild(S('line', { 'class': cls || 'fp-ci', x1: G.px(ci.x1), x2: G.px(ci.x2), y1: G.px(yy), y2: G.px(yy) }));
    if (cl) { g.appendChild(S('polygon', { 'class': 'fp-arrow is-left', points: C.arrow(ci.x1, yy, -1, 4) })); }
    if (cr) { g.appendChild(S('polygon', { 'class': 'fp-arrow is-right', points: C.arrow(ci.x2, yy, 1, 4) })); }
    return { x1: ci.x1, x2: ci.x2, clipLeft: cl, clipRight: cr };
  }

  function diamondShape(g, sm, sx, cy, cls) {
    var ci = G.clipInterval(sm.ci_lower, sm.ci_upper, sx);
    var d0 = sx.domain;
    var xm = sx(G.clamp(sm.estimate, G.min(d0[0], d0[1]), G.max(d0[0], d0[1])));
    var poly = S('polygon', { 'class': cls, points: C.diamond(ci.x1, xm, ci.x2, cy, 6.5),
      'data-est': String(sm.estimate), 'data-lo': String(sm.ci_lower), 'data-hi': String(sm.ci_upper), 'data-kind': sm.kind || '' });
    g.appendChild(poly);
    if (ci.clipLeft) { g.appendChild(S('polygon', { 'class': 'fp-arrow is-left', points: C.arrow(ci.x1 - 1, cy, -1, 4) })); }
    if (ci.clipRight) { g.appendChild(S('polygon', { 'class': 'fp-arrow is-right', points: C.arrow(ci.x2 + 1, cy, 1, 4) })); }
    return poly;
  }

  function render(container, plot, opts) {
    opts = opts || {};
    var L = layout(plot, layersOf(opts));
    var lay = L.layers;
    var cols = plot.columns || [];
    var labels = plot.labels || {};
    var svgEl = C.svgRoot(L.W, L.H, opts.label || t('plots.forest.aria'), 'fp-svg');
    var gMain = S('g', { 'class': 'fp-main' });
    svgEl.appendChild(gMain);
    var tip = null;
    var prim = primarySummary(plot);
    var maxW = G.max.apply(null, [0].concat((plot.studies || []).map(function (s) { return G.isNum(s.weight_pct) ? s.weight_pct : 0; })));

    // fejléc
    var hy = TOP + 12;
    var gHead = S('g', { 'class': 'fp-head' });
    gHead.appendChild(S('text', { 'class': 'fp-hcell', x: G.px(LABEL_X), y: G.px(hy) }, C.txt(labels.study, t('plots.forest.study'))));
    cols.forEach(function (c, j) {
      gHead.appendChild(headCell(L.colX[j] + COL_W - 6, hy, C.txt(c.title, c.id), 'end'));
    });
    gHead.appendChild(S('text', { 'class': 'fp-hcell', x: G.px(L.effX), y: G.px(hy) }, C.txt(labels.effect, plot.measure || '')));
    gHead.appendChild(S('text', { 'class': 'fp-hcell', x: G.px(L.weightX), y: G.px(hy), 'text-anchor': 'end' }, C.txt(labels.weight, t('plots.forest.weight'))));
    gHead.appendChild(S('text', { 'class': 'fp-hcell', x: G.px(L.robX), y: G.px(hy), 'text-anchor': 'middle' }, t('plots.forest.rob')));
    gHead.appendChild(S('line', { 'class': 'fp-hline', x1: '4', x2: G.px(L.W - 4), y1: G.px(G.snap(hy + 8)), y2: G.px(G.snap(hy + 8)) }));
    gMain.appendChild(gHead);

    // nullvonal és az összesített becslés referenciavonala
    var nullA = plot.scale ? plot.scale.null_analysis : null;
    var gGuides = S('g', { 'class': 'fp-guides' });
    gMain.appendChild(gGuides);
    if (G.isNum(nullA) && L.sx.inDomain(nullA)) {
      var nx = G.snap(L.sx(nullA));
      gGuides.appendChild(S('line', { 'class': 'fp-null', x1: G.px(nx), x2: G.px(nx), y1: G.px(L.y0 - 4), y2: G.px(L.axisY) }));
    }
    if (prim && G.isNum(prim.estimate) && L.sx.inDomain(prim.estimate)) {
      var ex = G.snap(L.sx(prim.estimate));
      gGuides.appendChild(S('line', { 'class': 'fp-ref', x1: G.px(ex), x2: G.px(ex), y1: G.px(L.y0 - 4), y2: G.px(L.axisY) }));
    }

    var items = [];
    L.rows.forEach(function (r, i) {
      var y = L.rowY(i);
      var cy = y + ROW / 2;
      var ty = cy + 4;
      if (r.type === 'gap') { return; }
      if (r.type === 'section') {
        var k = r.sec.summary && typeof r.sec.summary.k === 'number' ? r.sec.summary.k : (r.sec.row_uids || []).length;
        gMain.appendChild(S('text', { 'class': 'fp-section', x: G.px(LABEL_X), y: G.px(ty), 'data-section': r.sec.id || '' },
          '▾ ' + C.txt(r.sec.title, String(r.sec.id || '')) + ' ' + t('plots.forest.sectionK', { k: String(k) })));
        return;
      }
      if (r.type === 'study') {
        var s = r.s;
        var fl = s.flags || {};
        var cls = ['fp-study', fl.estimated && lay.estimated ? 'is-estimated' : '', fl.outlier ? 'is-outlier' : '', fl.influential ? 'is-influential' : ''].join(' ');
        var g = S('g', { 'class': cls, 'data-uid': s.row_uid, 'data-y': String(s.y), 'data-lo': String(s.lo), 'data-hi': String(s.hi),
          tabindex: '-1', role: 'listitem', 'aria-label': studyAria(s, lay) });
        g.appendChild(S('rect', { 'class': 'fp-rowbg', x: '2', y: G.px(y + 1), width: G.px(L.W - 4), height: G.px(ROW - 2), rx: '3', ry: '3' }));
        var lx = LABEL_X + (r.indent ? 12 : 0);
        var maxChars = r.indent ? 29 : 31;
        var labTxt = C.clip(s.label, maxChars);
        g.appendChild(S('text', { 'class': 'fp-label', x: G.px(lx), y: G.px(ty) }, labTxt));
        var marks = [];
        // a becsült érték jele nem ◆ (az az összesített becslésé): ≈ — a két jelölés nem csak színben tér el (UX-07)
        if (lay.estimated && fl.estimated) { marks.push({ sym: EST_SYM, cls: 'fp-flag is-estimated' }); }
        if (fl.outlier) { marks.push({ sym: '⚑', cls: 'fp-flag is-outlier' }); }
        if (fl.influential) { marks.push({ sym: '▲', cls: 'fp-flag is-influential' }); }
        marks.forEach(function (m, mi) {
          g.appendChild(S('text', { 'class': m.cls, x: G.px(L.colX.length ? L.colX[0] - 6 - mi * 12 : L.px0 - 18 - mi * 12), y: G.px(ty), 'text-anchor': 'end', 'aria-hidden': 'true' }, m.sym));
        });
        cols.forEach(function (c, j) {
          var v = s.cells && s.cells[c.id] !== undefined && s.cells[c.id] !== null ? String(s.cells[c.id]) : '—';
          g.appendChild(S('text', { 'class': 'fp-cell', x: G.px(L.colX[j] + COL_W - 6), y: G.px(ty), 'text-anchor': 'end' }, v));
        });
        ciLine(g, s.lo, s.hi, L.sx, cy, s.clip, 'fp-ci');
        if (L.sx.inDomain(s.y)) {
          var rr = G.areaRadius(s.weight_pct, maxW, 8, 2.2);
          var cx = L.sx(s.y);
          g.appendChild(S('rect', { 'class': 'fp-square', x: G.px(cx - rr), y: G.px(cy - rr), width: G.px(2 * rr), height: G.px(2 * rr) }));
        }
        g.appendChild(S('text', { 'class': 'fp-effect', x: G.px(L.effX), y: G.px(ty) }, C.txt(s.display_text)));
        g.appendChild(S('text', { 'class': 'fp-weight', x: G.px(L.weightX), y: G.px(ty), 'text-anchor': 'end' }, C.txt(s.weight_text)));
        var rob = fl.rob || null;
        g.appendChild(S('text', { 'class': 'fp-rob is-' + (rob || 'none'), x: G.px(L.robX), y: G.px(ty), 'text-anchor': 'middle', 'aria-hidden': 'true' },
          rob && ROB_SYM[rob] ? ROB_SYM[rob] : '–'));
        gMain.appendChild(g);
        items.push(g);
        return;
      }
      if (r.type === 'subtotal' || r.type === 'summary') {
        var sm = r.sm;
        var kindCls = r.type === 'subtotal' ? 'is-subtotal' : ('is-' + (sm.kind === 'sensitivity' ? 'sensitivity' : (r.primary ? 'primary' : (sm.model || 'overall'))));
        var gs = S('g', { 'class': 'fp-sum ' + kindCls, 'data-summary': sm.id || '' });
        gs.appendChild(S('text', { 'class': 'fp-sum-label' + (r.primary ? ' is-primary' : ''), x: G.px(LABEL_X + (r.type === 'subtotal' ? 12 : 0)), y: G.px(ty) },
          C.clip(C.txt(sm.label, ''), r.type === 'subtotal' ? 31 : 48)));
        if (sm.het_text && r.type === 'subtotal') {
          gs.appendChild(S('text', { 'class': 'fp-sum-het', x: G.px(L.colX.length ? L.colX[0] + 4 : L.px0 - 4), y: G.px(ty), 'text-anchor': L.colX.length ? 'start' : 'end' }, C.txt(sm.het_text, '')));
        }
        if (G.isNum(sm.ci_lower) && G.isNum(sm.ci_upper) && G.isNum(sm.estimate)) {
          diamondShape(gs, sm, L.sx, cy, 'fp-diamond ' + kindCls);
        }
        gs.appendChild(S('text', { 'class': 'fp-effect fp-sum-effect', x: G.px(L.effX), y: G.px(ty) }, C.txt(sm.display_text)));
        if (r.type === 'summary' && sm.p_text) {
          gs.appendChild(S('text', { 'class': 'fp-weight fp-sum-p', x: G.px(L.robX + 6), y: G.px(ty), 'text-anchor': 'end' }, C.txt(sm.p_text, '')));
        }
        gMain.appendChild(gs);
        return;
      }
      if (r.type === 'pi') {
        var gp = S('g', { 'class': 'fp-pi-row' });
        gp.appendChild(S('text', { 'class': 'fp-sum-label', x: G.px(LABEL_X), y: G.px(ty) }, C.txt(r.sm.pi_label, t('plots.forest.pi'))));
        var pci = G.clipInterval(r.sm.pi_lower, r.sm.pi_upper, L.sx);
        gp.appendChild(S('rect', { 'class': 'fp-pi', x: G.px(pci.x1), y: G.px(cy - 3), width: G.px(G.max(1, pci.x2 - pci.x1)), height: '6',
          'data-lo': String(r.sm.pi_lower), 'data-hi': String(r.sm.pi_upper) }));
        if (!pci.clipLeft) { gp.appendChild(S('line', { 'class': 'fp-pi-cap', x1: G.px(pci.x1), x2: G.px(pci.x1), y1: G.px(cy - 6), y2: G.px(cy + 6) })); } else { gp.appendChild(S('polygon', { 'class': 'fp-arrow is-left', points: C.arrow(pci.x1, cy, -1, 4) })); }
        if (!pci.clipRight) { gp.appendChild(S('line', { 'class': 'fp-pi-cap', x1: G.px(pci.x2), x2: G.px(pci.x2), y1: G.px(cy - 6), y2: G.px(cy + 6) })); } else { gp.appendChild(S('polygon', { 'class': 'fp-arrow is-right', points: C.arrow(pci.x2, cy, 1, 4) })); }
        gp.appendChild(S('text', { 'class': 'fp-effect', x: G.px(L.effX), y: G.px(ty) }, C.txt(r.sm.pi_text)));
        gMain.appendChild(gp);
        return;
      }
      if (r.type === 'note') {
        gMain.appendChild(S('text', { 'class': 'fp-note ' + (r.cls || ''), x: G.px(LABEL_X), y: G.px(ty) }, C.txt(r.text, '')));
      }
    });

    // tengely + bal/jobb felirat
    var gAxis = S('g', { 'class': 'fp-axis' });
    C.bottomAxis(gAxis, plot.axis, L.sx, L.axisY, { x0: L.px0, x1: L.px1, title: plot.axis.title });
    var anchorX = G.isNum(nullA) && L.sx.inDomain(nullA) ? L.sx(nullA) : (L.px0 + L.px1) / 2;
    if (labels.left) {
      gAxis.appendChild(S('text', { 'class': 'fp-side is-left', x: G.px(anchorX - 8), y: G.px(L.axisY + 56), 'text-anchor': 'end' }, '← ' + C.txt(labels.left, '')));
    }
    if (labels.right) {
      gAxis.appendChild(S('text', { 'class': 'fp-side is-right', x: G.px(anchorX + 8), y: G.px(L.axisY + 56), 'text-anchor': 'start' }, C.txt(labels.right, '') + ' →'));
    }
    gMain.appendChild(gAxis);

    tip = C.tooltip(svgEl, L.W);
    var byUid = C.studyIndex(plot);
    function showTip(uid, el) {
      var s = byUid[uid];
      if (!s) { return; }
      var lines = [s.label, C.txt(s.display_text) + (s.weight_text ? ' · ' + t('plots.forest.weightAria', { w: C.txt(s.weight_text) }) : '')];
      if (s.flags && s.flags.rob) { lines.push(t('plots.forest.robAria', { rob: robLabel(s.flags.rob) })); }
      if (s.flags && s.flags.estimated) { lines.push(EST_SYM + ' ' + t('plots.flag.estimated')); }
      if (s.flags && s.flags.outlier) { lines.push('⚑ ' + t('plots.flag.outlierLong')); }
      if (s.flags && s.flags.influential) { lines.push('▲ ' + t('plots.flag.influentialLong')); }
      var sq = el.querySelector('.fp-square');
      var x = sq ? Number(sq.getAttribute('x')) : L.px0;
      var y = Number(el.querySelector('.fp-rowbg').getAttribute('y'));
      tip.show(lines, x, y);
    }
    var drill = opts.onDrill || function (uid) { C.select(uid); };
    var nav = C.roving(items, {
      onActivate: function (uid) { drill(uid); },
      onFocus: function (uid, el) { C.highlight(uid, 'forest'); showTip(uid, el); },
      onBlur: function () { tip.hide(); C.highlight(null, 'forest'); },
      onEscape: function () { tip.hide(); }
    });
    items.forEach(function (el) {
      el.addEventListener('mouseenter', function () { C.highlight(el.getAttribute('data-uid'), 'forest'); showTip(el.getAttribute('data-uid'), el); });
      el.addEventListener('mouseleave', function () { C.highlight(null, 'forest'); tip.hide(); });
    });

    function anyFlag(k) { return (plot.studies || []).some(function (x) { return !!(x.flags && x.flags[k]); }); }
    // jelmagyarázat (HTML, a szín sosem egyedüli jelölés)
    var legend = h('p', { 'class': 'pl-legend fp-legend' },
      h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-square', 'aria-hidden': 'true' }, '■'), ' ', t('plots.forest.legendStudy')),
      h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-diamond', 'aria-hidden': 'true' }, '◆'), ' ', t('plots.forest.legendSummary')),
      lay.pi ? h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-pi', 'aria-hidden': 'true' }, '▬'), ' ', t('plots.forest.legendPi')) : null,
      h('span', { 'class': 'pl-key' }, h('span', { 'aria-hidden': 'true' }, '◀ ▶'), ' ', t('plots.forest.legendClip')),
      h('span', { 'class': 'pl-key' }, t('plots.forest.legendRob')),
      lay.estimated ? h('span', { 'class': 'pl-key' }, h('span', { 'class': 'pl-sym is-estimated', 'aria-hidden': 'true' }, EST_SYM), ' ', t('plots.flag.estimated')) : null,
      // minden jel, ami az ábrán megjelenhet, a jelmagyarázatban is (UX-07)
      anyFlag('outlier') ? h('span', { 'class': 'pl-key', 'data-key': 'outlier' }, h('span', { 'class': 'pl-sym is-outlier', 'aria-hidden': 'true' }, '⚑'), ' ', t('plots.flag.outlierLong')) : null,
      anyFlag('influential') ? h('span', { 'class': 'pl-key', 'data-key': 'influential' }, h('span', { 'class': 'pl-sym is-influential', 'aria-hidden': 'true' }, '▲'), ' ', t('plots.flag.influentialLong')) : null);

    svgEl.setAttribute('role', 'list');
    var list = h('div', { 'class': 'plot-scroll' }, svgEl);
    var wrap = h('figure', { 'class': 'plot-wrap fp-wrap', dataset: { plot: 'forest' } }, list, legend);
    MA.dom.mount(container, wrap);
    C.highlightSync(wrap, opts.signal);
    return { svg: svgEl, layout: L, focus: nav.focus, items: items, el: wrap };
  }

  var LAYERS = ['pi', 'subgroups', 'fixed', 'estimated'];

  /** A rétegek UI-preferenciája (mag.pref.forest.layers = 'pi,subgroups,…'); nincs → alapértelmezés. */
  function layersPref() {
    var raw = MA.prefs.get('forest.layers', null);
    if (raw === null) { return Object.assign({}, DEFAULT_LAYERS); }
    var on = raw.split(',');
    var out = {};
    LAYERS.forEach(function (k) { out[k] = on.indexOf(k) >= 0; });
    return out;
  }

  function saveLayers(l) {
    MA.prefs.set('forest.layers', LAYERS.filter(function (k) { return l[k]; }).join(','));
  }

  MA.plots.forest = { render: render, layout: layout, DEFAULT_LAYERS: DEFAULT_LAYERS, LAYERS: LAYERS, layersPref: layersPref, saveLayers: saveLayers };
})();
