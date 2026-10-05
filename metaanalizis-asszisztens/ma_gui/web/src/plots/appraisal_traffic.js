/* plots/appraisal_traffic.js — forgalmi lámpa és súlyozott összesítő a motor szk.rob-summary/v1-éből (terv 3.5.10, 4.12).
 *
 * MA.plots.robTraffic:
 *   matrix(summary, inst) → <svg>   vizsgálat × domén + összítélet; a jel ALAKJA (●◐○, „=” üres) és a színe együtt jelöl
 *                                    (a szín sosem egyedüli); minden cella <title>-t visz; a súly a motor kész szövege
 *   weighted(summary, inst) → <svg> | null   a súlyozott összesítő sáv(ok): a szélesség a motor százalékának pixel-
 *                                    leképezése (MA.geom.linear), a felirat a motor szövege (weighted[].text)
 * Nem számol: a súlyok, arányok és szövegek a motoréi; a felület csak pixelre képez.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var S = MA.dom.svg;
  var G = MA.geom;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };

  var ROW = 26, LABEL_W = 230, COL = 40, HEAD = 34;

  function clip(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }

  function mark(inst, v, x, y, extra) {
    var lv = v ? MA.appr.level(inst, v) : null;
    var label = v ? MA.appr.verdictLabel(inst, v) : t('appraisal.empty');
    return S('g', { 'class': ['rt-mark', 'ap-v-' + (lv || 'none')].join(' '), 'data-v': v || '' },
      S('title', null, (extra ? extra + ': ' : '') + label),
      S('text', { x: G.px(x), y: G.px(y), 'text-anchor': 'middle', 'dominant-baseline': 'central', 'class': 'rt-sym' }, MA.appr.SYM[lv || 'none']));
  }

  function matrix(summary, inst) {
    var doms = summary.domains || inst.domains || [];
    var studies = summary.studies || [];
    var W = LABEL_W + COL * (doms.length + 1) + 80;
    var H = HEAD + ROW * studies.length + 8;
    var tid = MA.dom.uid('rt-t');
    var svg = S('svg', { viewBox: '0 0 ' + W + ' ' + H, 'class': 'rt-svg', role: 'img', 'aria-labelledby': tid, id: 'rt-matrix' },
      S('title', { id: tid }, t('appraisal.sum.svgTitle', { tool: MA.appr.toolName(inst), n: String(studies.length) })));
    var head = S('g', { 'class': 'rt-head' });
    doms.forEach(function (d, i) {
      head.appendChild(S('g', null, S('title', null, pick(d.title)),
        S('text', { x: G.px(LABEL_W + COL * i + COL / 2), y: 20, 'text-anchor': 'middle' }, MA.appr.domainShort(d))));
    });
    head.appendChild(S('text', { x: G.px(LABEL_W + COL * doms.length + COL / 2), y: 20, 'text-anchor': 'middle', 'class': 'rt-overall-h' }, t('appraisal.col.overallShort')));
    head.appendChild(S('text', { x: G.px(LABEL_W + COL * (doms.length + 1) + 8), y: 20 }, t('appraisal.sum.weight')));
    svg.appendChild(head);
    var desc = [];
    studies.forEach(function (st, r) {
      var y = HEAD + ROW * r + ROW / 2;
      var g = S('g', { 'class': 'rt-row', 'data-study': st.study_id || '' });
      if (r % 2 === 1) { g.appendChild(S('rect', { x: 0, y: G.px(HEAD + ROW * r), width: W, height: ROW, 'class': 'rt-band' })); }
      var label = pick(st.label) || st.study_id || '';
      g.appendChild(S('g', null, S('title', null, label),
        S('text', { x: 6, y: G.px(y), 'dominant-baseline': 'central', 'class': 'rt-label' }, clip(label, 32))));
      var cells = [];
      doms.forEach(function (d, i) {
        var v = (st.judgements || {})[d.id] || null;
        cells.push(MA.appr.domainShort(d) + ' ' + (v ? MA.appr.verdictLabel(inst, v) : '—'));
        g.appendChild(mark(inst, v, LABEL_W + COL * i + COL / 2, y, MA.appr.domainShort(d)));
      });
      g.appendChild(mark(inst, st.overall || null, LABEL_W + COL * doms.length + COL / 2, y, t('appraisal.col.overall')));
      var wt = pick(st.weight_text);
      g.appendChild(S('text', { x: G.px(LABEL_W + COL * (doms.length + 1) + 8), y: G.px(y), 'dominant-baseline': 'central', 'class': 'rt-weight num' }, wt || '—'));
      desc.push(label + ': ' + cells.join(', ') + '; ' + t('appraisal.col.overall') + ' ' + (st.overall ? MA.appr.verdictLabel(inst, st.overall) : '—') + (wt ? ' (' + wt + ')' : ''));
      svg.appendChild(g);
    });
    svg.appendChild(S('desc', null, desc.join(' | ')));
    return svg;
  }

  function weighted(summary, inst) {
    var bars = [];
    if (Array.isArray(summary.weighted) && summary.weighted.length) { bars.push({ label: t('appraisal.col.overall'), parts: summary.weighted }); }
    var byDom = summary.weighted_by_domain || {};
    (summary.domains || []).forEach(function (d) { if (Array.isArray(byDom[d.id])) { bars.push({ label: MA.appr.domainShort(d), parts: byDom[d.id] }); } });
    var any = bars.some(function (b) { return b.parts.some(function (p) { return G.isNum(p.pct); }); });
    if (!any) { return null; }
    var BW = 520, X0 = 90, BH = 22, GAP = 10;
    var H = bars.length * (BH + GAP) + 10;
    var sx = G.linear([0, 100], [X0, X0 + BW], { clamp: true });
    var tid = MA.dom.uid('rt-w');
    var svg = S('svg', { viewBox: '0 0 ' + String(X0 + BW + 20) + ' ' + String(H), 'class': 'rt-svg rt-weighted', role: 'img', 'aria-labelledby': tid, id: 'rt-weighted' },
      S('title', { id: tid }, t('appraisal.sum.weightedTitle')));
    var desc = [];
    bars.forEach(function (b, i) {
      var y = 5 + i * (BH + GAP);
      var g = S('g', { 'class': 'rt-bar' });
      g.appendChild(S('text', { x: 4, y: G.px(y + BH / 2), 'dominant-baseline': 'central' }, b.label));
      var acc = 0;
      b.parts.forEach(function (p) {
        if (!G.isNum(p.pct) || p.pct <= 0) { return; }
        var x1 = sx(acc), x2 = sx(acc + p.pct);
        acc += p.pct;
        var txt = pick(p.text);
        g.appendChild(S('rect', { x: G.px(x1), y: G.px(y), width: G.px(G.max(0, x2 - x1)), height: BH, 'class': 'rt-seg ap-v-' + (p.level || 'none') },
          S('title', null, t('appraisal.sum.level.' + (p.level || 'none')) + ': ' + (txt || '—'))));
        if (txt && x2 - x1 > 40) { g.appendChild(S('text', { x: G.px((x1 + x2) / 2), y: G.px(y + BH / 2), 'text-anchor': 'middle', 'dominant-baseline': 'central', 'class': 'rt-seg-text' }, txt)); }
        desc.push(b.label + ' ' + t('appraisal.sum.level.' + (p.level || 'none')) + ' ' + (txt || '—'));
      });
      svg.appendChild(g);
    });
    svg.appendChild(S('desc', null, desc.join('; ')));
    return svg;
  }

  MA.plots = MA.plots || {};
  MA.plots.robTraffic = { matrix: matrix, weighted: weighted };
})();
