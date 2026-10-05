/* plots/common.js — közös SVG-segédek az elemzési ábrákhoz (terv 3.3, 3.5.7–3.5.8, 6.1, 6.8).
 *
 * Az ábrák (forest, funnel, doi, series, influence) a motor szk.ma.plot/v2 nézetmodelljét képezik
 * pixelre: tartomány (axis.domain), tengelyosztás (axis.ticks), poligonok és minden számszöveg
 * (display_text, *_text) a motorból jön; itt csak MA.geom-mal történik leképezés (a Math-objektumot
 * csak a geom.js használja), nincs számformázás.
 *
 * MA.plots.common:
 *   txt(v, fb)                    — a motor {hu, en} szövege (MA.i18n.pick)
 *   axisTicks(axis, scale)        — [{px, text, at}] (a tick-szöveg lehet sztring vagy {hu, en})
 *   bottomAxis(g, axis, sx, y, o) — vízszintes tengely: vonal, tickek, feliratok, cím
 *   leftAxis(g, axis, sy, x, o)   — függőleges tengely
 *   clipRect(svgEl, x, y, w, h)   — <clipPath> (egyedi id) → 'url(#id)'
 *   arrow(x, y, dir, size)        — nyílhegy-poligon pontjai (levágott CI)
 *   diamond(x1, xm, x2, cy, hh)   — gyémánt pontjai
 *   tooltip(svgEl, W)             — SVG-n belüli súgó (rect + szövegsorok; nincs style-attribútum)
 *   roving(items, opts)           — billentyűzetes bejárás (↑/↓/←/→/Home/End, Enter/Szóköz, Esc)
 *   highlightSync(root, signal?)  — a 'highlight'/'select' busz-események → .is-highlight/.is-selected
 *   clip(s, n)                    — címke rövidítése n karakterre (…); a teljes szöveg aria-label/súgó
 *   studyIndex(plot)              — row_uid → vizsgálat (plot.studies)
 */
(function () {
  'use strict';
  var MA = window.MA;
  var S = MA.dom.svg;
  var G = MA.geom;

  MA.plots = MA.plots || {};

  function txt(v, fb) { return MA.i18n.pick(v, fb === undefined ? '—' : fb); }

  function axisTicks(axis, scale) {
    var list = (axis && axis.ticks ? axis.ticks : []).map(function (tk) {
      return { at: tk.at, text: txt(tk.text, '') };
    });
    return G.ticks(list, scale);
  }

  /** Vízszintes tengely: o = {x0, x1, title?, tickLen?, grid?: [yTop, yBottom], cls?} */
  function bottomAxis(g, axis, sx, y, o) {
    o = o || {};
    var yy = G.snap(y);
    g.appendChild(S('line', { 'class': 'pl-axis-line', x1: G.px(o.x0), x2: G.px(o.x1), y1: G.px(yy), y2: G.px(yy) }));
    axisTicks(axis, sx).forEach(function (tk) {
      var x = G.snap(tk.px);
      if (o.grid) {
        g.appendChild(S('line', { 'class': 'pl-grid', x1: G.px(x), x2: G.px(x), y1: G.px(o.grid[0]), y2: G.px(o.grid[1]) }));
      }
      g.appendChild(S('line', { 'class': 'pl-tick', x1: G.px(x), x2: G.px(x), y1: G.px(yy), y2: G.px(yy + (o.tickLen || 5)) }));
      g.appendChild(S('text', { 'class': 'pl-tick-label', x: G.px(tk.px), y: G.px(yy + 18), 'text-anchor': 'middle', 'data-at': String(tk.at) }, tk.text));
    });
    var title = o.title === undefined ? (axis ? axis.title : null) : o.title;
    if (title) {
      g.appendChild(S('text', { 'class': 'pl-axis-title', x: G.px((o.x0 + o.x1) / 2), y: G.px(yy + 36), 'text-anchor': 'middle' }, txt(title, '')));
    }
  }

  /** Függőleges tengely: o = {y0, y1, title?, grid?: [xLeft, xRight]} */
  function leftAxis(g, axis, sy, x, o) {
    o = o || {};
    var xx = G.snap(x);
    g.appendChild(S('line', { 'class': 'pl-axis-line', x1: G.px(xx), x2: G.px(xx), y1: G.px(o.y0), y2: G.px(o.y1) }));
    axisTicks(axis, sy).forEach(function (tk) {
      var y = G.snap(tk.px);
      if (o.grid) {
        g.appendChild(S('line', { 'class': 'pl-grid', x1: G.px(o.grid[0]), x2: G.px(o.grid[1]), y1: G.px(y), y2: G.px(y) }));
      }
      g.appendChild(S('line', { 'class': 'pl-tick', x1: G.px(xx - 5), x2: G.px(xx), y1: G.px(y), y2: G.px(y) }));
      g.appendChild(S('text', { 'class': 'pl-tick-label', x: G.px(xx - 8), y: G.px(y + 4), 'text-anchor': 'end', 'data-at': String(tk.at) }, tk.text));
    });
    var title = o.title === undefined ? (axis ? axis.title : null) : o.title;
    if (title) {
      var cy = (o.y0 + o.y1) / 2;
      var tx = xx - 44;
      g.appendChild(S('text', { 'class': 'pl-axis-title', x: G.px(tx), y: G.px(cy), 'text-anchor': 'middle',
        transform: 'rotate(-90 ' + G.px(tx) + ' ' + G.px(cy) + ')' }, txt(title, '')));
    }
  }

  function clipRect(svgEl, x, y, w, h) {
    var id = MA.dom.uid('pl-clip');
    var defs = svgEl.querySelector('defs');
    if (!defs) { defs = S('defs'); svgEl.insertBefore(defs, svgEl.firstChild); }
    defs.appendChild(S('clipPath', { id: id }, S('rect', { x: G.px(x), y: G.px(y), width: G.px(w), height: G.px(h) })));
    return 'url(#' + id + ')';
  }

  /** Nyílhegy (levágott CI vége): dir = -1 balra, +1 jobbra. */
  function arrow(x, y, dir, size) {
    var s = size || 5;
    return [G.px(x) + ',' + G.px(y), G.px(x - dir * s * 1.4) + ',' + G.px(y - s), G.px(x - dir * s * 1.4) + ',' + G.px(y + s)].join(' ');
  }

  function diamond(x1, xm, x2, cy, hh) {
    return [G.px(x1) + ',' + G.px(cy), G.px(xm) + ',' + G.px(cy - hh), G.px(x2) + ',' + G.px(cy), G.px(xm) + ',' + G.px(cy + hh)].join(' ');
  }

  function clip(s, n) {
    var str = String(s === null || s === undefined ? '' : s);
    return str.length > n ? str.slice(0, n - 1) + '…' : str;
  }

  function studyIndex(plot) {
    var idx = {};
    ((plot && plot.studies) || []).forEach(function (s) { idx[s.row_uid] = s; });
    return idx;
  }

  /**
   * tooltip(svgEl, W) → {show(lines, x, y), hide()} — az SVG-n belüli súgó (a CSP miatt style-attribútum
   * nélkül: a helyzet SVG-attribútum). A sorok a motor szövegei (display_text …).
   */
  function tooltip(svgEl, W) {
    var g = S('g', { 'class': 'pl-tip', 'aria-hidden': 'true', visibility: 'hidden' });
    var rect = S('rect', { 'class': 'pl-tip-bg', rx: '4', ry: '4' });
    g.appendChild(rect);
    svgEl.appendChild(g);
    function hide() { g.setAttribute('visibility', 'hidden'); }
    function show(lines, x, y) {
      while (g.childNodes.length > 1) { g.removeChild(g.lastChild); }
      var maxLen = 0;
      lines.forEach(function (ln, i) {
        var s = String(ln);
        if (s.length > maxLen) { maxLen = s.length; }
        g.appendChild(S('text', { 'class': i === 0 ? 'pl-tip-title' : 'pl-tip-line', x: '8', y: G.px(16 + i * 15) }, s));
      });
      var w = G.min(W - 8, 16 + maxLen * 6.6);
      var hgt = 10 + lines.length * 15;
      rect.setAttribute('width', G.px(w));
      rect.setAttribute('height', G.px(hgt));
      var tx = G.clamp(x + 12, 4, W - w - 4);
      var ty = G.max(4, y - hgt - 8);
      g.setAttribute('transform', 'translate(' + G.px(tx) + ' ' + G.px(ty) + ')');
      g.setAttribute('visibility', 'visible');
      svgEl.appendChild(g);         // mindig legfelül
    }
    return { show: show, hide: hide, el: g };
  }

  /**
   * roving(items, opts) — billentyűzetes bejárás egy elemsoron (roving tabindex).
   *   items: [el] sorrendben (minden elem data-uid-ot visz)
   *   opts: {onActivate(uid, el), onFocus(uid, el), onEscape(), horizontal: bool}
   * Az első elem tabindex=0, a többi -1. ↑/↓ (és ←/→, ha horizontal) mozgat, Home/End, Enter/Szóköz aktivál.
   */
  function roving(items, opts) {
    opts = opts || {};
    var current = 0;
    function setActive(i, focus) {
      if (!items.length) { return; }
      current = G.clamp(i, 0, items.length - 1);
      items.forEach(function (el, j) { el.setAttribute('tabindex', j === current ? '0' : '-1'); });
      if (focus) { items[current].focus(); }
    }
    items.forEach(function (el, i) {
      el.addEventListener('focus', function () {
        setActive(i, false);
        if (opts.onFocus) { opts.onFocus(el.getAttribute('data-uid'), el); }
      });
      el.addEventListener('blur', function () { if (opts.onBlur) { opts.onBlur(el.getAttribute('data-uid'), el); } });
      el.addEventListener('keydown', function (ev) {
        var k = ev.key;
        var next = null;
        if (k === 'ArrowDown' || (opts.horizontal && k === 'ArrowRight')) { next = i + 1; } else if (k === 'ArrowUp' || (opts.horizontal && k === 'ArrowLeft')) { next = i - 1; } else if (k === 'Home') { next = 0; } else if (k === 'End') { next = items.length - 1; } else if (k === 'PageDown') { next = i + 5; } else if (k === 'PageUp') { next = i - 5; }
        if (next !== null) {
          ev.preventDefault();
          setActive(next, true);
          return;
        }
        if (k === 'Enter' || k === ' ') {
          ev.preventDefault();
          if (opts.onActivate) { opts.onActivate(el.getAttribute('data-uid'), el); }
          return;
        }
        if (k === 'Escape' && opts.onEscape) { opts.onEscape(ev); }
      });
      el.addEventListener('click', function () {
        setActive(i, false);
        if (opts.onActivate) { opts.onActivate(el.getAttribute('data-uid'), el); }
      });
    });
    setActive(0, false);
    return {
      focus: function (uid) {
        for (var j = 0; j < items.length; j++) {
          if (items[j].getAttribute('data-uid') === uid) { setActive(j, true); return true; }
        }
        return false;
      },
      setActive: setActive,
      items: items
    };
  }

  /**
   * highlightSync(root, signal?) — a busz 'highlight' {row_uid, source} és 'select' {row_uid} eseményeire a
   * root [data-uid] elemei .is-highlight / .is-selected osztályt kapnak (minden nyitott ábrán ugyanaz).
   * A legutóbbi állapotot a store 'analysis.highlight' és 'analysis.selected' kulcsa őrzi (memória).
   */
  function applyMarks(root) {
    var hl = MA.store.get('analysis.highlight');
    var sel = MA.store.get('analysis.selected');
    MA.dom.$$('[data-uid]', root).forEach(function (el) {
      var u = el.getAttribute('data-uid');
      el.classList.toggle('is-highlight', !!hl && u === hl);
      el.classList.toggle('is-selected', !!sel && u === sel);
    });
  }

  function highlightSync(root, signal) {
    var offs = [
      MA.store.subscribe('analysis.highlight', function () { applyMarks(root); }),
      MA.store.subscribe('analysis.selected', function () { applyMarks(root); })
    ];
    applyMarks(root);
    function off() { offs.forEach(function (f) { f(); }); offs = []; }
    if (signal) { signal.addEventListener('abort', off); }
    return off;
  }

  function highlight(uid, source) {
    var v = uid || null;
    if (MA.store.get('analysis.highlight') !== v) { MA.store.set('analysis.highlight', v); }
    MA.bus.emit('highlight', { row_uid: v, source: source || null });
  }

  function select(uid) {
    MA.store.set('analysis.selected', uid || null);
    MA.bus.emit('drilldown', { row_uid: uid || null });
  }

  /** Egy ábra SVG-gyökere (viewBox, role=group + címke); a méretezés CSS-ből. */
  function svgRoot(W, H, labelText, cls) {
    return S('svg', { 'class': ['pl-svg', cls || ''].join(' '), viewBox: '0 0 ' + G.px(W) + ' ' + G.px(H),
      width: G.px(W), height: G.px(H), role: 'group', 'aria-label': labelText, focusable: 'false' });
  }

  MA.plots.common = {
    txt: txt,
    axisTicks: axisTicks,
    bottomAxis: bottomAxis,
    leftAxis: leftAxis,
    clipRect: clipRect,
    arrow: arrow,
    diamond: diamond,
    clip: clip,
    studyIndex: studyIndex,
    tooltip: tooltip,
    roving: roving,
    highlightSync: highlightSync,
    applyMarks: applyMarks,
    highlight: highlight,
    select: select,
    svgRoot: svgRoot
  };
})();
