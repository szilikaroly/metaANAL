/* components/extraction_vtable.js — csak olvasható, virtualizált adatrács (v1: kettős kinyerés, terv 3.3, 3.5.5).
 *
 * MA.vtable.create(opts) → rács. WAI-ARIA grid-minta: role=grid / row / columnheader / rowheader / gridcell,
 * roving tabindex a cellákon, aria-rowcount = a teljes sorszám + fejléc, aria-rowindex = a sor helye a teljes
 * listában; aria-selected a kiválasztott soron. VIRTUAL_MIN sor fölött csak a görgetési ablak (+ ráhagyás) sorai
 * vannak a DOM-ban (két térkitöltő sorral), a billentyűzetes navigáció a célt előbb az ablakba görgeti; ha az aktív
 * sor kigörgetődik, a fókusz a rácsra kerül, és a következő billentyű visszagörget hozzá.
 *
 * opts: {label, columns: [{id, label, header?, cls?, title?}], rows: [adat], key(row) → szöveg,
 *        cell(row, col, index) → Node | szöveg | {node|text, cls?, title?}, rowClass?(row) → osztály(ok),
 *        rowLabel?(row) → szöveg (a sor aria-label-je), onSelect?(row, index), onActivate?(row, index),
 *        onKey?(ev, row, index) → true = kezelve, virtualMin? (alap 1000), emptyKey? (i18n-kulcs)}
 * Billentyűk: nyilak, Home/End (+Ctrl), PageUp/PageDown, Enter = onActivate, Szóköz = kiválasztás.
 * Csak MA.dom / MA.geom; szám nem formázódik (a cellák a hívó kész szövegei).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var G = MA.geom;

  var VIRTUAL_MIN = 1000;
  var OVERSCAN = 12;
  var PAGE = 10;
  var counter = 0;

  function create(opts) {
    counter += 1;
    var gid = 'vt' + counter;
    var cols = opts.columns || [];
    var rows = [];
    var keyOf = opts.key || function (r, i) { return String(i); };
    var virtualMin = opts.virtualMin === undefined ? VIRTUAL_MIN : opts.virtualMin;
    var virtual = false;
    var trs = {};            // index → tr (kirajzolt sorok)
    var act = null;          // {index, col}
    var sel = null;          // kiválasztott sor indexe
    var rowH = 27;
    var win = { start: 0, end: 0 };
    var padTop = null, padBottom = null;
    var raf = false;
    var quiet = false;

    var thead = h('thead');
    var tbody = h('tbody');
    var table = h('table', { 'class': 'vt-table', role: 'grid', id: gid, 'aria-label': opts.label || null, 'aria-readonly': 'true' }, thead, tbody);
    var el = h('div', { 'class': 'vt' }, table);
    el.addEventListener('scroll', function () {
      if (!virtual || raf) { return; }
      raf = true;
      window.requestAnimationFrame(function () { raf = false; if (virtual) { renderWindow(false); } });
    });

    function buildHead() {
      MA.dom.mount(thead, h('tr', { role: 'row', 'aria-rowindex': '1' }, cols.map(function (c, i) {
        return h('th', { role: 'columnheader', scope: 'col', 'aria-colindex': String(i + 1), dataset: { col: c.id },
          'class': c.cls || null, title: c.title || null }, c.label);
      })));
    }

    function cellNode(r, c, i) {
      var v = opts.cell ? opts.cell(r, c, i) : (r[c.id] === undefined || r[c.id] === null ? '' : String(r[c.id]));
      if (v && typeof v === 'object' && !(v instanceof window.Node) && !Array.isArray(v)) { return v; }
      return { node: v };
    }

    function buildRow(i) {
      var r = rows[i];
      var tr = h('tr', { role: 'row', 'aria-rowindex': String(i + 2), dataset: { index: String(i), key: keyOf(r, i) },
        'class': opts.rowClass ? opts.rowClass(r, i) : null, 'aria-selected': sel === i ? 'true' : 'false',
        'aria-label': opts.rowLabel ? opts.rowLabel(r, i) : null });
      cols.forEach(function (c, ci) {
        var v = cellNode(r, c, i);
        var content = v.node !== undefined ? v.node : (v.text === undefined || v.text === null ? '' : String(v.text));
        tr.appendChild(h(c.header ? 'th' : 'td', {
          role: c.header ? 'rowheader' : 'gridcell', scope: c.header ? 'row' : null, tabindex: '-1',
          'aria-colindex': String(ci + 1), dataset: { col: c.id }, 'class': [c.cls, v.cls], title: v.title || null
        }, content));
      });
      trs[i] = tr;
      return tr;
    }

    function cellEl(i, col) {
      var tr = trs[i];
      if (!tr) { return null; }
      var ci = 0;
      for (var k = 0; k < cols.length; k++) { if (cols[k].id === col) { ci = k; break; } }
      return tr.children[ci] || null;
    }

    function spacer(which) {
      return h('tr', { 'class': 'vt-spacer', 'aria-hidden': 'true', role: 'presentation', dataset: { pad: which } },
        h('td', { colspan: String(cols.length || 1), role: 'presentation' }));
    }

    function setPad(tr, px) { tr.firstChild.style.height = String(px) + 'px'; tr.hidden = px <= 0; }

    function windowRange() {
      var headH = thead.getBoundingClientRect ? thead.getBoundingClientRect().height : 0;
      var viewH = el.clientHeight || 600;
      var first = G.floor(G.max(0, el.scrollTop - headH) / rowH);
      var count = G.ceil(viewH / rowH) + 1;
      var start = G.min(G.max(0, first - OVERSCAN), rows.length);
      return { start: start, end: G.max(start, G.min(rows.length, first + count + OVERSCAN)) };
    }

    function renderWindow(force) {
      var r = windowRange();
      if (!force && r.start === win.start && r.end === win.end) { return; }
      var lost = false;
      Object.keys(trs).forEach(function (k) {
        var i = Number(k);
        if (i >= r.start && i < r.end) { return; }
        if (trs[k].contains(document.activeElement)) { lost = true; }
        if (trs[k].parentNode) { trs[k].parentNode.removeChild(trs[k]); }
        delete trs[k];
      });
      var ref = padBottom;
      for (var j = r.end - 1; j >= r.start; j--) {
        if (!trs[j]) { tbody.insertBefore(buildRow(j), ref); }
        ref = trs[j];
      }
      win = r;
      if (r.end > r.start) {
        var any = trs[r.start];
        var hh = any && any.getBoundingClientRect ? any.getBoundingClientRect().height : 0;
        if (hh > 4) { rowH = hh; }
      }
      setPad(padTop, r.start * rowH);
      setPad(padBottom, (rows.length - r.end) * rowH);
      paintActive();
      if (lost) {
        quiet = true;
        try { table.focus({ preventScroll: true }); } finally { quiet = false; }
      }
    }

    function render() {
      buildHead();
      trs = {};
      MA.dom.clear(tbody);
      virtual = rows.length > virtualMin;
      el.classList.toggle('is-virtual', virtual);
      table.setAttribute('aria-rowcount', String(rows.length + 1));
      table.setAttribute('aria-colcount', String(cols.length));
      if (!rows.length) {
        tbody.appendChild(h('tr', { role: 'row' }, h('td', { role: 'gridcell', colspan: String(cols.length || 1), 'class': 'vt-empty' },
          MA.i18n.t(opts.emptyKey || 'dual.vt.empty'))));
        act = null;
        table.tabIndex = 0;
        return;
      }
      if (act && act.index >= rows.length) { act = { index: rows.length - 1, col: act.col }; }
      if (sel !== null && sel >= rows.length) { sel = null; }
      if (virtual) {
        padTop = spacer('top');
        padBottom = spacer('bottom');
        tbody.appendChild(padTop);
        tbody.appendChild(padBottom);
        win = { start: -1, end: -1 };
        renderWindow(true);
      } else {
        padTop = padBottom = null;
        for (var i = 0; i < rows.length; i++) { tbody.appendChild(buildRow(i)); }
        paintActive();
      }
      if (!act) { act = { index: 0, col: cols[0] ? cols[0].id : null }; paintActive(); }
    }

    function paintActive() {
      MA.dom.$$('[tabindex="0"]', tbody).forEach(function (x) { x.tabIndex = -1; });
      var c = act ? cellEl(act.index, act.col) : null;
      if (c) { c.tabIndex = 0; }
      table.tabIndex = c ? -1 : 0;
    }

    function ensureRendered(i) {
      if (!virtual || trs[i]) { return; }
      var headH = thead.getBoundingClientRect ? thead.getBoundingClientRect().height : 0;
      var viewH = el.clientHeight || 600;
      var top = i * rowH;
      if (top < el.scrollTop) { el.scrollTop = top; } else { el.scrollTop = G.max(0, top + headH + rowH - viewH); }
      renderWindow(true);
    }

    function setActive(i, col, focus) {
      if (!rows.length) { return; }
      i = G.max(0, G.min(rows.length - 1, i));
      act = { index: i, col: col || (act && act.col) || cols[0].id };
      if (focus) { ensureRendered(i); }
      paintActive();
      var c = cellEl(i, act.col);
      if (focus && c) {
        c.focus({ preventScroll: true });
        if (c.scrollIntoView) { c.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
      }
    }

    function select(i, notify) {
      if (i === null || i === undefined || i < 0 || i >= rows.length) { sel = null; } else { sel = i; }
      MA.dom.$$('tr[aria-selected="true"]', tbody).forEach(function (x) { x.setAttribute('aria-selected', 'false'); });
      if (sel !== null && trs[sel]) { trs[sel].setAttribute('aria-selected', 'true'); }
      if (notify !== false && sel !== null && opts.onSelect) { opts.onSelect(rows[sel], sel); }
    }

    function colIndex(id) {
      for (var k = 0; k < cols.length; k++) { if (cols[k].id === id) { return k; } }
      return 0;
    }

    function fromEvent(ev) {
      var c = ev.target && ev.target.closest ? ev.target.closest('[role="gridcell"], [role="rowheader"]') : null;
      if (!c || !tbody.contains(c)) { return null; }
      return { index: Number(c.parentNode.dataset.index), col: c.dataset.col };
    }

    table.addEventListener('click', function (ev) {
      var p = fromEvent(ev);
      if (!p || isNaN(p.index)) { return; }
      if (ev.target.closest && ev.target.closest('button, a, input, select, textarea')) { return; }
      setActive(p.index, p.col, true);
      select(p.index);
    });
    table.addEventListener('dblclick', function (ev) {
      var p = fromEvent(ev);
      if (p && !isNaN(p.index) && opts.onActivate) { opts.onActivate(rows[p.index], p.index); }
    });
    table.addEventListener('focusin', function (ev) {
      if (ev.target === table && act && rows.length && !quiet) { setActive(act.index, act.col, true); return; }
      var p = fromEvent(ev);
      if (p && !isNaN(p.index) && (!act || act.index !== p.index || act.col !== p.col)) { setActive(p.index, p.col, false); }
    });
    table.addEventListener('keydown', function (ev) {
      if (!act || !rows.length) { return; }
      if (ev.target.closest && ev.target.closest('input, select, textarea, button, a') && ev.target !== table) { return; }
      if (opts.onKey && opts.onKey(ev, rows[act.index], act.index)) { return; }
      var k = ev.key;
      var ctrl = ev.ctrlKey || ev.metaKey;
      var ci = colIndex(act.col);
      var handled = true;
      if (k === 'ArrowDown') { setActive(act.index + 1, act.col, true); }
      else if (k === 'ArrowUp') { setActive(act.index - 1, act.col, true); }
      else if (k === 'ArrowRight') { setActive(act.index, cols[G.min(cols.length - 1, ci + 1)].id, true); }
      else if (k === 'ArrowLeft') { setActive(act.index, cols[G.max(0, ci - 1)].id, true); }
      else if (k === 'Home') { setActive(ctrl ? 0 : act.index, cols[0].id, true); }
      else if (k === 'End') { setActive(ctrl ? rows.length - 1 : act.index, cols[cols.length - 1].id, true); }
      else if (k === 'PageDown') { setActive(act.index + PAGE, act.col, true); }
      else if (k === 'PageUp') { setActive(act.index - PAGE, act.col, true); }
      else if (k === ' ' || k === 'Spacebar') { select(act.index); }
      else if (k === 'Enter') { select(act.index); if (opts.onActivate) { opts.onActivate(rows[act.index], act.index); } }
      else { handled = false; }
      if (handled) { ev.preventDefault(); }
    });

    rows = (opts.rows || []).slice();
    render();

    return {
      el: el,
      table: table,
      id: gid,
      rows: function () { return rows; },
      setRows: function (list, keep) {
        var keepKey = keep && sel !== null && rows[sel] ? keyOf(rows[sel], sel) : null;
        var actKey = keep && act && rows[act.index] ? keyOf(rows[act.index], act.index) : null;
        var focused = table.contains(document.activeElement);
        rows = (list || []).slice();
        sel = null;
        var ai = 0;
        for (var i = 0; i < rows.length; i++) {
          var kk = keyOf(rows[i], i);
          if (keepKey !== null && kk === keepKey) { sel = i; }
          if (actKey !== null && kk === actKey) { ai = i; }
        }
        act = rows.length ? { index: ai, col: act ? act.col : cols[0].id } : null;
        render();
        if (focused && act) { setActive(act.index, act.col, true); }
      },
      /** select(key, {focus}) — a kulcsú sor kiválasztása (és fókusza) */
      select: function (key, o) {
        for (var i = 0; i < rows.length; i++) {
          if (keyOf(rows[i], i) === key) {
            setActive(i, act ? act.col : null, !!(o && o.focus));
            if (!(o && o.focus)) { ensureRendered(i); paintActive(); }
            select(i, !(o && o.silent));
            return true;
          }
        }
        return false;
      },
      selected: function () { return sel === null ? null : rows[sel]; },
      selectedIndex: function () { return sel; },
      refresh: function () { var a = act; render(); if (a) { act = a; paintActive(); } },
      focus: function () { if (act) { setActive(act.index, act.col, true); } else { table.focus(); } },
      active: function () { return act ? { index: act.index, col: act.col, row: rows[act.index] } : null; },
      virtual: function () { return virtual; },
      renderedCount: function () { return Object.keys(trs).length; },
      row: function (i) { return trs[i] || null; }
    };
  }

  MA.vtable = { create: create, VIRTUAL_MIN: VIRTUAL_MIN };

  // ================================================================ önteszt (?selftest=1)
  MA.selftest.register('vtable: virtualizált, csak olvasható rács (aria, billentyűzet)', function (tt) {
    var list = [];
    for (var i = 0; i < 1300; i++) { list.push({ k: 'k' + String(i), a: '<b>x' + String(i) + '</b>' }); }
    var picked = [];
    var g = create({ label: 'vt-selftest', columns: [{ id: 'k', label: 'kulcs', header: true }, { id: 'a', label: 'A' }], rows: list,
      key: function (r) { return r.k; }, onSelect: function (r) { picked.push(r.k); } });
    var host = h('div', { 'class': 'mg-selftest-host' }, g.el);
    document.body.appendChild(host);
    try {
      tt.ok(g.virtual(), 'virtuális 1000 sor fölött');
      tt.eq(g.table.getAttribute('aria-rowcount'), '1301', 'aria-rowcount');
      tt.ok(g.renderedCount() < 150, 'csak az ablak kirajzolt');
      tt.eq(g.table.querySelectorAll('b').length, 0, 'a cella szövegként (nem HTML)');
      g.focus();
      var key = function (k, o) { document.activeElement.dispatchEvent(new KeyboardEvent('keydown', Object.assign({ key: k, bubbles: true, cancelable: true }, o || {}))); };
      key('End', { ctrlKey: true });
      tt.eq(g.active().index, 1299, 'Ctrl+End');
      tt.eq(document.activeElement.parentNode.getAttribute('aria-rowindex'), '1301', 'aria-rowindex a teljes listában');
      key(' ');
      tt.eq(picked[picked.length - 1], 'k1299', 'Szóköz → kiválasztás');
      tt.eq(g.row(1299).getAttribute('aria-selected'), 'true', 'aria-selected');
      tt.ok(g.select('k640', { focus: true }), 'kiválasztás kulccsal');
      tt.eq(document.activeElement.parentNode.dataset.key, 'k640', 'a kiválasztott sor fókuszban');
    } finally {
      host.parentNode.removeChild(host);
    }
  });
})();
