/* components/grid.js — szerkeszthető adattábla (terv 3.3, 3.5.3, 6.2, 7.1 T6).
 *
 * MA.grid.create(opts) → rács. A cellák SZÖVEGKÉNT maradnak (a számot a motor értelmezi);
 * a rács csak „számnak látszik-e” halvány előjelzést ad (UX, 6.2).
 * Virtualizáció (v1, terv 3.3): VIRTUAL_MIN (1000) sor fölött csak a görgetési ablak sorai (+ ráhagyás) vannak a
 * DOM-ban, a többit két térkitöltő sor helyettesíti; a modell (rows) teljes marad. Az aria-rowcount a teljes
 * sorszám, az aria-rowindex a sor helye a teljes táblában (WAI-ARIA virtualizált rács). A billentyűzetes
 * navigáció a cél sort előbb az ablakba görgeti; az aktív (és a szerkesztett) cella sora mindig kirajzolt; a
 * dekorációk (setDecorations) a modellben élnek, és a kirajzolt sorokra kerülnek. Legfeljebb MAX_ROWS (5000,
 * = a szerver security.MAX_ROWS-a) sor.
 * Akadálymentesség: role=grid / row / columnheader / rowheader / gridcell, roving tabindex,
 * aria-invalid + aria-describedby a jelzett cellákon; a jelzés mindig szimbólum is (nem csak szín).
 *
 * opts: {label, columns: [{id, label, hint?, frozen?, width? (em), readonly?, numeric?}],
 *        rows: [{uid, cells: {colId: szöveg}}], newRow() → {uid, cells}, maxRows?,
 *        onChange(batch), onActive(pos), onKey(ev, pos) → true = kezelve, onActivate(pos),
 *        readOnly? — csak olvasható rács (pl. a pillanatképben): minden oszlop readonly, beillesztés/kivágás nincs}
 *   batch: {source: 'edit'|'paste'|'clear'|'cut', cells: [{uid, col, before, after}],
 *           inserted: null | {index, rows: [{uid, cells}]}} — a rács a változást MÁR alkalmazta.
 * Billentyűk: nyilak, Home/End (+Ctrl), PageUp/PageDown, Shift+nyíl = tartomány, Enter/F2 = szerkesztés,
 *   gépelés = felülírás, Delete/Backspace = törlés, Esc = elvetés, Enter/Tab szerkesztés közben = rögzít és
 *   lép; Ctrl+C/X/V = TSV (Excel). Szerkesztésen kívül a Tab elhagyja a rácsot (WAI-ARIA grid minta).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var G = MA.geom;

  var MAX_ROWS = 5000;
  var VIRTUAL_MIN = 1000;   // ennél több sor: ablakos kirajzolás
  var OVERSCAN = 15;        // ráhagyás az ablak két szélén (sor)
  var ROW_H = 27;           // alapértelmezett sormagasság (px), az első kirajzolt sorról mérve pontosítjuk
  var PAGE = 10;
  var SYM = { error: '✖', warning: '⚠', info: 'ℹ', ack: '✓', estimated: '◆', reconciled: '≡', external: '⟳' };
  var KINDS = ['error', 'warning', 'info', 'ack', 'estimated', 'reconciled', 'external'];
  var NUMBERISH = /^\s*[-+−]?(\d[\d\s.,'  ]*)?([.,]\d+)?\s*%?\s*$/;
  var counter = 0;

  // ---------------------------------------------------------------- TSV (Excel-vágólap)
  /** parseTSV(text) → [[cella]] — Excel-idézés ("a""b", cellán belüli tab/új sor) kezelve. */
  function parseTSV(text) {
    var s = String(text === null || text === undefined ? '' : text).replace(/\r\n?/g, '\n');
    var rows = [], row = [], cell = '', q = false, start = true;
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      if (q) {
        if (ch === '"') {
          if (s.charAt(i + 1) === '"') { cell += '"'; i++; } else { q = false; }
        } else { cell += ch; }
        continue;
      }
      if (ch === '"' && start) { q = true; start = false; continue; }
      if (ch === '\t') { row.push(cell); cell = ''; start = true; continue; }
      if (ch === '\n') { row.push(cell); rows.push(row); row = []; cell = ''; start = true; continue; }
      cell += ch;
      start = false;
    }
    if (q) {      // lezáratlan idézőjel: nyers felosztás (nem találgatunk)
      return s.replace(/\n$/, '').split('\n').map(function (l) { return l.split('\t'); });
    }
    if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
    return rows;
  }

  function toTSV(matrix) {
    return matrix.map(function (r) {
      return r.map(function (c) {
        var v = c === null || c === undefined ? '' : String(c);
        return /[\t\n\r"]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
      }).join('\t');
    }).join('\r\n');
  }

  /** newUid(taken?) → 'r' + 8 base32-karakter (a szerver UID_RE-jének megfelelő, véletlen). */
  function newUid(taken) {
    var abc = '0123456789abcdefghijklmnopqrstuv';
    for (var tries = 0; tries < 20; tries++) {
      var buf = new Uint8Array(8);
      window.crypto.getRandomValues(buf);
      var s = 'r';
      for (var i = 0; i < 8; i++) { s += abc.charAt(buf[i] & 31); }
      if (!taken || !taken[s]) { return s; }
    }
    throw new Error('grid: nem sikerült egyedi row_uid-ot képezni');
  }

  // ---------------------------------------------------------------- rács
  function create(opts) {
    counter += 1;
    var gid = 'mg' + counter;
    var cols = [];
    var colIdx = {};
    var rows = [];
    var byUid = {};
    var trs = {};
    var tds = {};
    var visible = [];
    var filterFn = null;
    var act = null;          // {uid, col}
    var anc = null;          // tartomány-horgony
    var edit = null;         // {uid, col, input, before}
    var decorated = [];
    var decoMap = {};        // az utolsó setDecorations-térkép (virtuális módban a kirajzolt sorokra kerül)
    var descSeq = 0;
    var maxRows = opts.maxRows || MAX_ROWS;
    var virtualMin = opts.virtualMin === undefined ? VIRTUAL_MIN : opts.virtualMin;
    var virtual = false;
    var win = { start: 0, end: 0 };   // a kirajzolt látható-sor tartomány (virtuális módban)
    var rowPos = {};                  // uid → index a teljes táblában (virtuális módban, a sorszámokhoz)
    var rowH = ROW_H;
    var padTop = null, padBottom = null;
    var rafPending = false;
    var quietFocus = false;  // a rácsra tett fókusz (kigörgetett aktív sor) ne görgessen vissza

    var thead = h('thead');
    var tbody = h('tbody');
    var table = h('table', { 'class': 'mg-table', role: 'grid', id: gid, 'aria-label': opts.label || null, 'aria-multiselectable': 'true',
      'aria-readonly': opts.readOnly ? 'true' : null }, thead, tbody);
    var descBox = h('div', { 'class': 'mg-desc', hidden: true });
    var el = h('div', { 'class': 'mg' }, table, descBox);
    el.addEventListener('scroll', function () {
      if (!virtual || rafPending) { return; }
      rafPending = true;
      window.requestAnimationFrame(function () { rafPending = false; if (virtual) { renderWindow(); } });
    });

    function frozenLeft(i) {
      var em = 3.2;
      for (var k = 0; k < i; k++) { if (cols[k].frozen) { em += cols[k].width || 12; } }
      return em + 'em';
    }

    function buildHead() {
      var trh = h('tr', { role: 'row', 'aria-rowindex': '1' },
        h('th', { role: 'columnheader', scope: 'col', 'class': 'mg-rh mg-frozen', 'aria-colindex': '1' }, '#'));
      cols.forEach(function (c, i) {
        trh.appendChild(h('th', {
          role: 'columnheader', scope: 'col', 'aria-colindex': String(i + 2), dataset: { col: c.id },
          'class': ['mg-ch', c.frozen && 'mg-frozen', c.readonly && 'mg-ro', c.numeric && 'mg-num'],
          styles: c.frozen ? { left: frozenLeft(i), width: (c.width || 12) + 'em', 'min-width': (c.width || 12) + 'em' } : null,
          title: c.title || null
        }, h('span', { 'class': 'mg-cl' }, c.label), c.hint ? h('span', { 'class': 'mg-hint' }, c.hint) : null));
      });
      MA.dom.mount(thead, trh);
    }

    function looksBad(col, text) {
      var c = cols[colIdx[col]];
      return !!(c && c.numeric && text !== '' && !NUMBERISH.test(text));
    }

    function paintCell(td, col, text) {
      td.firstChild.textContent = text;
      td.classList.toggle('mg-nonnum', looksBad(col, text));
    }

    function buildRow(r) {
      var tr = h('tr', { role: 'row', dataset: { uid: r.uid } },
        h('th', { role: 'rowheader', scope: 'row', 'class': 'mg-rh mg-frozen', 'aria-colindex': '1' },
          h('span', { 'class': 'mg-rn' }), h('span', { 'class': 'mg-rm', 'aria-hidden': 'true' }), h('span', { 'class': 'sr-only mg-rs' })));
      tds[r.uid] = {};
      cols.forEach(function (c, i) {
        var td = h('td', {
          role: 'gridcell', tabindex: '-1', 'aria-colindex': String(i + 2), dataset: { col: c.id },
          'class': [c.frozen && 'mg-frozen', c.readonly && 'mg-ro', c.numeric && 'mg-num'],
          'aria-readonly': c.readonly ? 'true' : null,
          styles: c.frozen ? { left: frozenLeft(i), width: (c.width || 12) + 'em', 'min-width': (c.width || 12) + 'em', 'max-width': (c.width || 12) + 'em' } : null
        }, h('span', { 'class': 'mg-t' }), h('span', { 'class': 'mg-m', 'aria-hidden': 'true' }));
        paintCell(td, c.id, r.cells[c.id] === undefined || r.cells[c.id] === null ? '' : String(r.cells[c.id]));
        tds[r.uid][c.id] = td;
        tr.appendChild(td);
      });
      trs[r.uid] = tr;
      return tr;
    }

    function numberRow(r, i) {
      var tr = trs[r.uid];
      if (!tr) { return; }
      tr.setAttribute('aria-rowindex', String(i + 2));
      tr.querySelector('.mg-rn').textContent = String(i + 1);
    }

    function renumber() {
      visible = rows.filter(function (r) { return !filterFn || filterFn(r); });
      var vis = {};
      visible.forEach(function (r) { vis[r.uid] = true; });
      if (virtual) {
        rowPos = {};
        rows.forEach(function (r, i) { rowPos[r.uid] = i; });
        if (edit && !vis[edit.uid]) { commitEdit(0, 0, false); }
        renderWindow(true);
      } else {
        rows.forEach(function (r, i) {
          numberRow(r, i);
          trs[r.uid].hidden = !vis[r.uid];
        });
      }
      table.setAttribute('aria-rowcount', String(rows.length + 1));
      table.setAttribute('aria-colcount', String(cols.length + 1));
      if (act && (!byUid[act.uid] || !vis[act.uid])) { act = null; }
      if (!act && visible.length && cols.length) { setActive(visible[0].uid, cols[0].id, false); }
    }

    function render(keepDeco) {
      buildHead();
      trs = {};
      tds = {};
      byUid = {};
      MA.dom.clear(tbody);
      clearDeco();
      if (!keepDeco) { decoMap = {}; }
      rows.forEach(function (r) { byUid[r.uid] = r; });
      virtual = rows.length > virtualMin;
      el.classList.toggle('is-virtual', virtual);
      if (virtual) {
        padTop = spacer('top');
        padBottom = spacer('bottom');
        tbody.appendChild(padTop);
        tbody.appendChild(padBottom);
        win = { start: 0, end: 0 };
      } else {
        padTop = padBottom = null;
        table.removeAttribute('tabindex');
        rows.forEach(function (r) { tbody.appendChild(buildRow(r)); });
      }
      renumber();
      if (!virtual) { applyDeco(); }
    }

    // ---------------------------------------------------------------- virtuális ablak
    function spacer(which) {
      return h('tr', { 'class': 'mg-spacer', 'aria-hidden': 'true', role: 'presentation', dataset: { pad: which } },
        h('td', { colspan: String(cols.length + 1), role: 'presentation' }));
    }

    function setPad(tr, px) {
      tr.firstChild.style.height = String(px) + 'px';
      tr.hidden = px <= 0;
    }

    function measureRow() {
      var any = tbody.querySelector('tr[data-uid]');
      var hh = any && any.getBoundingClientRect ? any.getBoundingClientRect().height : 0;
      if (hh > 4) { rowH = hh; }
    }

    function visIndex(uid) {
      for (var i = 0; i < visible.length; i++) { if (visible[i].uid === uid) { return i; } }
      return -1;
    }

    /** A látható (szűrt) sorok [start, end) tartománya a görgetési helyzetből (+ ráhagyás). Ha az aktív cella
     *  sora kigörgetődik, a fókusz a rácsra (table, tabindex) kerül: a következő billentyű visszagörget hozzá. */
    function windowRange() {
      var headH = thead.getBoundingClientRect ? thead.getBoundingClientRect().height : 0;
      var viewH = el.clientHeight || 600;
      var first = G.floor(G.max(0, el.scrollTop - headH) / rowH);
      var count = G.ceil(viewH / rowH) + 1;
      var start = G.min(G.max(0, first - OVERSCAN), visible.length);
      var end = G.min(visible.length, first + count + OVERSCAN);
      return { start: start, end: G.max(start, end) };
    }

    function unrender(uid) {
      var tr = trs[uid];
      if (tr && tr.parentNode) { tr.parentNode.removeChild(tr); }
      delete trs[uid];
      delete tds[uid];
    }

    /** Ablakos kirajzolás: a tartományon kívüli sorok kikerülnek, a hiányzók bekerülnek (sorrendben). */
    function renderWindow(force) {
      if (!virtual) { return; }
      var r = windowRange();
      if (!force && r.start === win.start && r.end === win.end) { return; }
      var keep = {};
      for (var i = r.start; i < r.end; i++) { keep[visible[i].uid] = true; }
      var focusLost = false;
      Object.keys(trs).forEach(function (uid) {
        if (keep[uid]) { return; }
        if (edit && edit.uid === uid) { commitEdit(0, 0, false); }
        if (trs[uid].contains(document.activeElement)) { focusLost = true; }
        unrender(uid);
      });
      var ref = padBottom;
      for (var j = r.end - 1; j >= r.start; j--) {
        var row = visible[j];
        if (!trs[row.uid]) { tbody.insertBefore(buildRow(row), ref); }
        ref = trs[row.uid];
      }
      win = r;
      setPad(padTop, r.start * rowH);
      setPad(padBottom, (visible.length - r.end) * rowH);
      for (var k = r.start; k < r.end; k++) { numberRow(visible[k], rowPos[visible[k].uid]); }
      if (r.end > r.start) {
        var before = rowH;
        measureRow();
        if (before !== rowH) {
          setPad(padTop, r.start * rowH);
          setPad(padBottom, (visible.length - r.end) * rowH);
        }
      }
      var a = act ? td(act.uid, act.col) : null;
      if (a) { a.tabIndex = 0; }
      // ha az aktív cella nincs kirajzolva, maga a rács fókuszálható (Tab-bal is elérhető marad)
      table.tabIndex = a ? -1 : 0;
      applyDeco();
      paintSelection();
      if (focusLost) {
        quietFocus = true;
        try { table.focus({ preventScroll: true }); } finally { quietFocus = false; }
      }
    }

    /** A sor kirajzolása (virtuális módban az ablak odagörgetésével). */
    function ensureRendered(uid) {
      if (!virtual || trs[uid]) { return; }
      var i = visIndex(uid);
      if (i < 0) { return; }
      var headH = thead.getBoundingClientRect ? thead.getBoundingClientRect().height : 0;
      var viewH = el.clientHeight || 600;
      var top = i * rowH;
      if (top < el.scrollTop) { el.scrollTop = top; } else { el.scrollTop = G.max(0, top + headH + rowH - viewH); }
      renderWindow(true);
    }

    // ---------------------------------------------------------------- aktív cella, tartomány
    function td(uid, col) { return tds[uid] ? tds[uid][col] : null; }

    function posOf(uid, col) {
      var vi = -1;
      for (var i = 0; i < visible.length; i++) { if (visible[i].uid === uid) { vi = i; break; } }
      return { uid: uid, col: col, row: vi, colIndex: colIdx[col], rowIndex: rows.indexOf(byUid[uid]) };
    }

    function rangeCells() {
      if (!act) { return []; }
      var a = anc || act;
      var p = posOf(act.uid, act.col), q = posOf(a.uid, a.col);
      var r1 = p.row < q.row ? p.row : q.row, r2 = p.row < q.row ? q.row : p.row;
      var c1 = p.colIndex < q.colIndex ? p.colIndex : q.colIndex, c2 = p.colIndex < q.colIndex ? q.colIndex : p.colIndex;
      var out = [];
      for (var r = r1; r <= r2; r++) {
        var line = [];
        for (var c = c1; c <= c2; c++) { line.push({ uid: visible[r].uid, col: cols[c].id }); }
        out.push(line);
      }
      return out;
    }

    function paintSelection() {
      MA.dom.$$('[aria-selected="true"]', tbody).forEach(function (x) { x.removeAttribute('aria-selected'); });
      if (!anc || !act || (anc.uid === act.uid && anc.col === act.col)) { return; }
      rangeCells().forEach(function (line) {
        line.forEach(function (p) { var x = td(p.uid, p.col); if (x) { x.setAttribute('aria-selected', 'true'); } });
      });
    }

    function setActive(uid, col, focus, extend) {
      if (focus || !act) { ensureRendered(uid); }
      var next = td(uid, col);
      if (!next) {
        if (virtual && byUid[uid] && colIdx[col] !== undefined) {   // kirajzolatlan sor: a modellben aktív
          if (act) { var p0 = td(act.uid, act.col); if (p0) { p0.tabIndex = -1; } }
          anc = extend ? (anc || act || { uid: uid, col: col }) : null;
          act = { uid: uid, col: col };
          if (opts.onActive) { opts.onActive(posOf(uid, col)); }
        }
        return;
      }
      if (act) { var prev = td(act.uid, act.col); if (prev) { prev.tabIndex = -1; } }
      if (extend) { anc = anc || act || { uid: uid, col: col }; } else { anc = null; }
      act = { uid: uid, col: col };
      next.tabIndex = 0;
      paintSelection();
      if (focus) {
        next.focus({ preventScroll: true });
        if (next.scrollIntoView) { next.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
      }
      if (opts.onActive) { opts.onActive(posOf(uid, col)); }
    }

    function move(dr, dc, extend) {
      if (!act || !visible.length) { return; }
      var p = posOf(act.uid, act.col);
      var r = p.row + dr, c = p.colIndex + dc;
      if (r < 0) { r = 0; }
      if (r > visible.length - 1) { r = visible.length - 1; }
      if (c < 0) { c = 0; }
      if (c > cols.length - 1) { c = cols.length - 1; }
      setActive(visible[r].uid, cols[c].id, true, extend);
    }

    // ---------------------------------------------------------------- változtatás
    function setText(uid, col, text) {
      var r = byUid[uid];
      if (!r || colIdx[col] === undefined) { return; }
      var v = text === null || text === undefined ? '' : String(text);
      r.cells[col] = v;
      var x = td(uid, col);
      if (x && !(edit && edit.uid === uid && edit.col === col)) { paintCell(x, col, v); }
    }

    function emit(source, changes, inserted) {
      if (!changes.length && !inserted) { return; }
      if (opts.onChange) { opts.onChange({ source: source, cells: changes, inserted: inserted || null }); }
    }

    function changeCells(list, source) {
      var changes = [];
      list.forEach(function (c) {
        var r = byUid[c.uid];
        var col = cols[colIdx[c.col]];
        if (!r || !col || col.readonly) { return; }
        var before = r.cells[c.col] === undefined || r.cells[c.col] === null ? '' : String(r.cells[c.col]);
        if (before === c.text) { return; }
        setText(c.uid, c.col, c.text);
        changes.push({ uid: c.uid, col: c.col, before: before, after: c.text });
      });
      return changes;
    }

    function insertRows(index, list) {
      if (rows.length + list.length > maxRows) { throw new Error(t('grid.tooManyRows', { max: String(maxRows) })); }
      var at = index < 0 || index > rows.length ? rows.length : index;
      var ref = at < rows.length ? trs[rows[at].uid] : null;
      var goVirtual = !virtual && rows.length + list.length > virtualMin;
      list.forEach(function (r, k) {
        var row = { uid: r.uid, cells: Object.assign({}, r.cells) };
        rows.splice(at + k, 0, row);
        byUid[row.uid] = row;
        if (!virtual && !goVirtual) { tbody.insertBefore(buildRow(row), ref); }
      });
      if (goVirtual) { relayout(); return; }
      renumber();
    }

    /** Teljes újrarajzolás a módváltáskor (virtuális ↔ teljes), az aktív cella és a dekorációk megtartásával. */
    function relayout() {
      var keepAct = act, keepAnc = anc;
      if (edit) { commitEdit(0, 0, false); }
      act = null;
      render(true);
      if (keepAct && byUid[keepAct.uid]) { anc = keepAnc; setActive(keepAct.uid, keepAct.col, false); }
    }

    function deleteRows(uids) {
      var set = {};
      uids.forEach(function (u) { set[u] = true; });
      if (edit && set[edit.uid]) { cancelEdit(); }
      var actIdx = act ? posOf(act.uid, act.col).row : 0;
      var actCol = act ? act.col : (cols[0] && cols[0].id);
      rows = rows.filter(function (r) {
        if (!set[r.uid]) { return true; }
        unrender(r.uid);
        delete byUid[r.uid];
        return false;
      });
      decorated = decorated.filter(function (x) { return document.body.contains(x) || tbody.contains(x); });
      if (act && set[act.uid]) { act = null; }
      anc = null;
      if (virtual && rows.length <= virtualMin) { render(true); } else { renumber(); }
      if (!act && visible.length && actCol) {
        var i = actIdx > visible.length - 1 ? visible.length - 1 : actIdx;
        setActive(visible[i].uid, actCol, false);
      }
    }

    // ---------------------------------------------------------------- szerkesztés
    function beginEdit(initial) {
      if (!act || edit) { return; }
      var c = cols[colIdx[act.col]];
      if (!c || c.readonly) {
        if (initial === undefined && opts.onActivate) { opts.onActivate(posOf(act.uid, act.col)); }
        return;
      }
      ensureRendered(act.uid);
      var cell = td(act.uid, act.col);
      if (!cell) { return; }
      var r = byUid[act.uid];
      var before = r.cells[act.col] === undefined || r.cells[act.col] === null ? '' : String(r.cells[act.col]);
      var input = h('input', { type: 'text', 'class': 'mg-input', value: initial === undefined ? before : initial, spellcheck: 'false', autocomplete: 'off',
        'aria-label': c.label + ' — ' + t('grid.row', { n: String(rows.indexOf(r) + 1) }) });
      edit = { uid: act.uid, col: act.col, input: input, before: before };
      cell.classList.add('is-editing');
      cell.firstChild.hidden = true;
      cell.appendChild(input);
      input.addEventListener('blur', function () { if (edit && edit.input === input) { commitEdit(0, 0, false); } });
      input.addEventListener('input', function () { cell.classList.toggle('mg-nonnum', looksBad(edit ? edit.col : '', input.value)); });
      input.focus();
      var n = input.value.length;
      input.setSelectionRange(n, n);
    }

    function endEditDom() {
      var cell = td(edit.uid, edit.col);
      var input = edit.input;
      edit = null;
      if (cell) {
        cell.classList.remove('is-editing');
        cell.firstChild.hidden = false;
        if (input.parentNode === cell) { cell.removeChild(input); }
      }
      return cell;
    }

    function commitEdit(dr, dc, refocus) {
      if (!edit) { return; }
      var uid = edit.uid, col = edit.col, value = edit.input.value;
      var cell = endEditDom();
      var changes = changeCells([{ uid: uid, col: col, text: value }], 'edit');
      if (cell) { paintCell(cell, col, byUid[uid].cells[col] || ''); }
      emit('edit', changes);
      if (refocus !== false) {
        if (dr || dc) { move(dr, dc, false); } else { setActive(uid, col, true); }
      }
    }

    function cancelEdit() {
      if (!edit) { return; }
      var uid = edit.uid, col = edit.col;
      var cell = endEditDom();
      if (cell) { paintCell(cell, col, byUid[uid].cells[col] || ''); }
      setActive(uid, col, true);
    }

    // ---------------------------------------------------------------- vágólap
    function selectionMatrix() {
      return rangeCells().map(function (line) {
        return line.map(function (p) { var v = byUid[p.uid].cells[p.col]; return v === undefined || v === null ? '' : String(v); });
      });
    }

    function clearSelection(source) {
      var list = [];
      rangeCells().forEach(function (line) { line.forEach(function (p) { list.push({ uid: p.uid, col: p.col, text: '' }); }); });
      emit(source || 'clear', changeCells(list));
    }

    /** paste(text) — TSV beillesztése az aktív cellától (a hiányzó sorok a tábla végére kerülnek). */
    function paste(text) {
      if (opts.readOnly) { return null; }
      var m = parseTSV(text);
      if (!m.length || !act) { return null; }
      var top = rangeCells()[0] ? rangeCells()[0][0] : act;
      var p0 = posOf(top.uid, top.col);
      var width = 0;
      m.forEach(function (r) { if (r.length > width) { width = r.length; } });
      var dropped = p0.colIndex + width - cols.length;
      var need = p0.row + m.length - visible.length;
      var inserted = null;
      if (need > 0) {
        if (rows.length + need > maxRows) {
          MA.ui.toast({ kind: 'warning', title: t('grid.tooManyRows', { max: String(maxRows) }) });
          need = maxRows - rows.length;
          m = m.slice(0, visible.length - p0.row + (need > 0 ? need : 0));
        }
        if (need > 0) {
          var taken = {};
          rows.forEach(function (r) { taken[r.uid] = true; });
          var fresh = [];
          for (var k = 0; k < need; k++) {
            var nr = opts.newRow ? opts.newRow(taken) : { uid: newUid(taken), cells: {} };
            taken[nr.uid] = true;
            fresh.push(nr);
          }
          inserted = { index: rows.length, rows: fresh.map(function (r) { return { uid: r.uid, cells: Object.assign({}, r.cells) }; }) };
          insertRows(rows.length, fresh);
        }
      }
      var list = [];
      m.forEach(function (line, ri) {
        var row = visible[p0.row + ri];
        if (!row) { return; }
        line.forEach(function (v, ci) {
          var c = cols[p0.colIndex + ci];
          if (c && !c.readonly) { list.push({ uid: row.uid, col: c.id, text: v }); }
        });
      });
      var changes = changeCells(list);
      emit('paste', changes, inserted);
      if (dropped > 0) { MA.ui.toast({ kind: 'warning', title: t('grid.pasteColsDropped', { n: String(dropped) }) }); }
      return { rows: m.length, cols: width, inserted: inserted ? inserted.rows.length : 0, changed: changes.length };
    }

    // ---------------------------------------------------------------- események
    function cellFromEvent(ev) {
      var x = ev.target && ev.target.closest ? ev.target.closest('td[role="gridcell"]') : null;
      if (!x || !tbody.contains(x)) { return null; }
      var tr = x.parentNode;
      return { uid: tr.dataset.uid, col: x.dataset.col };
    }

    table.addEventListener('mousedown', function (ev) {
      var p = cellFromEvent(ev);
      if (!p || (edit && edit.uid === p.uid && edit.col === p.col)) { return; }
      if (edit) { commitEdit(0, 0, false); }
      ev.preventDefault();
      setActive(p.uid, p.col, true, ev.shiftKey);
    });
    table.addEventListener('dblclick', function (ev) {
      var p = cellFromEvent(ev);
      if (p && !edit) { setActive(p.uid, p.col, true); beginEdit(); }
    });
    table.addEventListener('focusin', function (ev) {
      if (ev.target === table && act && virtual && !quietFocus) {     // a rácsra került fókusz: vissza az aktív cellához
        setActive(act.uid, act.col, true);
        return;
      }
      var p = cellFromEvent(ev);
      if (p && !edit && (!act || act.uid !== p.uid || act.col !== p.col)) { setActive(p.uid, p.col, false); }
    });

    table.addEventListener('keydown', function (ev) {
      var pos = act ? posOf(act.uid, act.col) : null;
      if (opts.onKey && opts.onKey(ev, pos, !!edit)) { return; }
      var k = ev.key;
      if (edit) {
        if (k === 'Enter') { ev.preventDefault(); commitEdit(ev.shiftKey ? -1 : 1, 0); } else if (k === 'Tab') { ev.preventDefault(); commitEdit(0, ev.shiftKey ? -1 : 1); } else if (k === 'Escape') { ev.preventDefault(); ev.stopPropagation(); cancelEdit(); }
        return;
      }
      if (!act) { return; }
      var ctrl = ev.ctrlKey || ev.metaKey;
      var handled = true;
      if (k === 'ArrowDown') { move(1, 0, ev.shiftKey); } else if (k === 'ArrowUp') { move(-1, 0, ev.shiftKey); } else if (k === 'ArrowRight') { move(0, 1, ev.shiftKey); } else if (k === 'ArrowLeft') { move(0, -1, ev.shiftKey); } else if (k === 'Home') { move(ctrl ? -visible.length : 0, -cols.length, ev.shiftKey); } else if (k === 'End') { move(ctrl ? visible.length : 0, cols.length, ev.shiftKey); } else if (k === 'PageDown') { move(PAGE, 0, ev.shiftKey); } else if (k === 'PageUp') { move(-PAGE, 0, ev.shiftKey); } else if (k === 'Enter' || k === 'F2') { beginEdit(); } else if (k === 'Delete' || k === 'Backspace') { clearSelection('clear'); } else if (k === 'Escape' && anc) { anc = null; paintSelection(); } else if (k === 'a' && ctrl) {
        anc = { uid: visible[0].uid, col: cols[0].id };
        setActive(visible[visible.length - 1].uid, cols[cols.length - 1].id, false, true);
      } else if (k.length === 1 && !ctrl && !ev.altKey) { beginEdit(k); } else { handled = false; }
      if (handled) { ev.preventDefault(); }
    });

    table.addEventListener('copy', function (ev) {
      if (edit || !act) { return; }
      ev.preventDefault();
      ev.clipboardData.setData('text/plain', toTSV(selectionMatrix()));
    });
    table.addEventListener('cut', function (ev) {
      if (edit || !act || opts.readOnly) { return; }
      ev.preventDefault();
      ev.clipboardData.setData('text/plain', toTSV(selectionMatrix()));
      clearSelection('cut');
    });
    table.addEventListener('paste', function (ev) {
      var text = ev.clipboardData ? ev.clipboardData.getData('text/plain') : '';
      if (edit) {
        if (!/[\t\n]/.test(text.replace(/\r?\n$/, ''))) { return; }   // egy cella: a mező maga illeszti be
        commitEdit(0, 0, false);
      }
      ev.preventDefault();
      paste(text);
    });

    // ---------------------------------------------------------------- dekorációk
    /**
     * setDecorations({cells: {uid: {col: {kinds: [...], messages: [...]}}}, rows: {uid: {blocked, kinds, messages}}})
     * kinds: error (aria-invalid) | warning | info | ack | estimated | reconciled | external.
     */
    function setDecorations(map) {
      decoMap = map || {};
      applyDeco();
    }

    function clearDeco() {
      decorated.forEach(function (x) {
        x.removeAttribute('aria-invalid');
        x.removeAttribute('aria-describedby');
        KINDS.forEach(function (k) { x.classList.remove('is-' + k); });
        x.classList.remove('is-blocked');
        if (x.tagName === 'TR') { return; }
        var m = x.querySelector(x.tagName === 'TH' ? '.mg-rm' : '.mg-m');
        if (m) { m.textContent = ''; }
        var s = x.querySelector('.mg-rs');
        if (s) { s.textContent = ''; }
        x.removeAttribute('title');
      });
      decorated = [];
      MA.dom.clear(descBox);
      descSeq = 0;
    }

    /** A dekorációs térkép a KIRAJZOLT sorokra (teljes módban: mindre). */
    function applyDeco() {
      clearDeco();
      var map = decoMap;
      var cellMap = (map && map.cells) || {};
      Object.keys(cellMap).forEach(function (uid) {
        if (!tds[uid]) { return; }
        Object.keys(cellMap[uid]).forEach(function (col) {
          var x = td(uid, col);
          var d = cellMap[uid][col];
          if (!x || !d) { return; }
          var kinds = KINDS.filter(function (k) { return d.kinds && d.kinds.indexOf(k) >= 0; });
          if (!kinds.length) { return; }
          kinds.forEach(function (k) { x.classList.add('is-' + k); });
          if (kinds.indexOf('error') >= 0) { x.setAttribute('aria-invalid', 'true'); }
          x.querySelector('.mg-m').textContent = kinds.map(function (k) { return SYM[k]; }).join('');
          var msgs = (d.messages || []).filter(function (s) { return !!s; });
          if (msgs.length) {
            descSeq += 1;
            var id = gid + '-d' + descSeq;
            descBox.appendChild(h('span', { id: id }, msgs.join('; ')));
            x.setAttribute('aria-describedby', id);
            x.setAttribute('title', msgs.join('\n'));
          }
          decorated.push(x);
        });
      });
      var rowMap = (map && map.rows) || {};
      Object.keys(rowMap).forEach(function (uid) {
        var tr = trs[uid];
        var d = rowMap[uid];
        if (!tr || !d) { return; }
        var th = tr.firstChild;
        var kinds = KINDS.filter(function (k) { return d.kinds && d.kinds.indexOf(k) >= 0; });
        kinds.forEach(function (k) { th.classList.add('is-' + k); });
        th.querySelector('.mg-rm').textContent = kinds.map(function (k) { return SYM[k]; }).join('');
        var msgs = (d.messages || []).slice();
        if (d.blocked) { tr.classList.add('is-blocked'); msgs.unshift(t('grid.rowBlocked')); decorated.push(tr); }
        th.querySelector('.mg-rs').textContent = msgs.length ? ' — ' + msgs.join('; ') : '';
        if (msgs.length) { th.setAttribute('title', msgs.join('\n')); }
        decorated.push(th);
      });
    }

    // ---------------------------------------------------------------- nyilvános felület
    function setColumns(list) {
      cols = list.map(function (c) { return Object.assign({}, c, opts.readOnly ? { readonly: true } : null); });
      colIdx = {};
      cols.forEach(function (c, i) { colIdx[c.id] = i; });
    }

    /** updateColumns([{id, hint?, numeric?}]) — fejléc-súgó és szám-előjelzés frissítése újrarajzolás nélkül. */
    function updateColumns(list) {
      list.forEach(function (u) {
        var i = colIdx[u.id];
        if (i === undefined) { return; }
        var c = cols[i];
        c.hint = u.hint || null;
        c.numeric = !!u.numeric;
        var th = thead.querySelector('th[data-col="' + c.id + '"]');
        if (th) {
          th.classList.toggle('mg-num', c.numeric);
          var hs = th.querySelector('.mg-hint');
          if (c.hint && !hs) { th.appendChild(h('span', { 'class': 'mg-hint' }, c.hint)); } else if (hs && !c.hint) { th.removeChild(hs); } else if (hs) { hs.textContent = c.hint; }
        }
        rows.forEach(function (r) {
          var x = td(r.uid, c.id);
          if (x) { x.classList.toggle('mg-num', c.numeric); paintCell(x, c.id, r.cells[c.id] === undefined || r.cells[c.id] === null ? '' : String(r.cells[c.id])); }
        });
      });
    }

    setColumns(opts.columns || []);
    rows = (opts.rows || []).map(function (r) { return { uid: r.uid, cells: Object.assign({}, r.cells) }; });
    render();

    return {
      el: el,
      table: table,
      id: gid,
      rows: function () { return rows; },
      row: function (uid) { return byUid[uid] || null; },
      columns: function () { return cols.slice(); },
      cell: td,
      get: function (uid, col) { var r = byUid[uid]; return r && r.cells[col] !== undefined && r.cells[col] !== null ? String(r.cells[col]) : ''; },
      set: setText,
      change: function (list, source) { var ch = changeCells(list); emit(source || 'edit', ch); return ch; },
      setRows: function (list) {
        if (edit) { cancelEdit(); }
        rows = list.map(function (r) { return { uid: r.uid, cells: Object.assign({}, r.cells) }; });
        act = null;
        anc = null;
        render();
      },
      setColumns: function (list) { if (edit) { cancelEdit(); } setColumns(list); act = null; render(); },
      virtual: function () { return virtual; },
      renderedCount: function () { return Object.keys(trs).length; },
      updateColumns: updateColumns,
      insertRows: insertRows,
      deleteRows: deleteRows,
      paste: paste,
      setDecorations: setDecorations,
      setFilter: function (fn) { filterFn = fn || null; renumber(); },
      focusCell: function (uid, col) { if (edit) { commitEdit(0, 0, false); } setActive(uid, col, true); },
      active: function () { return act ? posOf(act.uid, act.col) : null; },
      selection: function () { return rangeCells(); },
      editing: function () { return !!edit; },
      commit: function () { if (edit) { commitEdit(0, 0, true); } },
      cancel: cancelEdit,
      beginEdit: beginEdit,
      visibleCount: function () { return visible.length; },
      maxRows: maxRows
    };
  }

  MA.grid = { create: create, parseTSV: parseTSV, toTSV: toTSV, newUid: newUid, MAX_ROWS: MAX_ROWS, VIRTUAL_MIN: VIRTUAL_MIN, SYMBOLS: SYM };

  // ================================================================ önteszt (?selftest=1; termékben is fut)
  MA.selftest.register('grid: TSV (Excel-idézés)', function (tt) {
    tt.deepEq(parseTSV('a\tb\r\n1\t2\r\n'), [['a', 'b'], ['1', '2']], 'egyszerű TSV, záró CRLF');
    tt.deepEq(parseTSV('"x\ty"\t"he said ""hi"""\n"two\nlines"\tz'), [['x\ty', 'he said "hi"'], ['two\nlines', 'z']], 'idézett tab, idézőjel, új sor');
    tt.deepEq(parseTSV('2,000\t\t3'), [['2,000', '', '3']], 'üres cella, ezres tagolás szövegként');
    tt.eq(toTSV([['a"b', 'c\td'], ['1', '']]), '"a""b"\t"c\td"\r\n1\t', 'TSV-írás idézéssel');
    tt.deepEq(parseTSV(toTSV([['<b>x</b>', 'p\nq']])), [['<b>x</b>', 'p\nq']], 'oda-vissza');
    tt.ok(/^r[0-9a-v]{8}$/.test(newUid()), 'row_uid-alak (r + 8 base32)');
  });

  MA.selftest.register('grid: billentyűzet, szerkesztés, beillesztés, dekoráció', function (tt) {
    var changes = [];
    var g = create({
      label: 'selftest',
      columns: [{ id: 'a', label: 'study', frozen: true, width: 10 }, { id: 'b', label: 'e1', numeric: true }, { id: 'c', label: 'n1', numeric: true }],
      rows: [{ uid: 'rt0001', cells: { a: '<img src=x onerror=alert(1)>', b: '4', c: '123' } }, { uid: 'rt0002', cells: { a: 'B', b: '6', c: '306' } }],
      onChange: function (b) { changes.push(b); }
    });
    var host = h('div', { 'class': 'sr-only' }, g.el);
    document.body.appendChild(host);
    try {
      tt.eq(g.table.getAttribute('role'), 'grid', 'role=grid');
      tt.eq(g.table.querySelectorAll('img').length, 0, 'a címke szövegként, nem HTML-ként');
      tt.eq(g.cell('rt0001', 'a').textContent.indexOf('<img'), 0, 'a szöveg szó szerint látszik');
      g.focusCell('rt0001', 'a');
      var key = function (k, o) { document.activeElement.dispatchEvent(new KeyboardEvent('keydown', Object.assign({ key: k, bubbles: true, cancelable: true }, o || {}))); };
      key('ArrowRight');
      tt.eq(document.activeElement, g.cell('rt0001', 'b'), '→ a következő cellára');
      key('ArrowDown');
      tt.eq(document.activeElement, g.cell('rt0002', 'b'), '↓ a következő sorra');
      tt.eq(g.cell('rt0002', 'b').tabIndex, 0, 'roving tabindex');
      key('9');
      tt.ok(g.editing(), 'gépelés → szerkesztés');
      document.activeElement.value = '60';
      key('Enter');
      tt.eq(g.get('rt0002', 'b'), '60', 'Enter rögzít');
      tt.eq(changes.length, 1, 'onChange egyszer');
      tt.deepEq(changes[0].cells[0], { uid: 'rt0002', col: 'b', before: '6', after: '60' }, 'a változás leírása');
      g.focusCell('rt0001', 'c');
      key('Enter');
      document.activeElement.value = 'NR';
      key('Escape');
      tt.eq(g.get('rt0001', 'c'), '123', 'Esc elveti');
      g.focusCell('rt0002', 'b');
      var r = g.paste('2,000\tx\n7\t8');
      tt.eq(r.inserted, 1, 'a hiányzó sor hozzáadva');
      tt.eq(g.rows().length, 3, 'három sor');
      tt.eq(g.get('rt0002', 'b'), '2,000', 'a cella szöveg marad');
      tt.ok(g.cell('rt0002', 'c').classList.contains('mg-nonnum'), 'halvány „nem szám” előjelzés');
      g.setDecorations({ cells: { rt0001: { b: { kinds: ['error'], messages: ['V006 e1 > n1'] } } }, rows: { rt0001: { blocked: true, kinds: ['error'] } } });
      var c = g.cell('rt0001', 'b');
      tt.eq(c.getAttribute('aria-invalid'), 'true', 'aria-invalid');
      var d = document.getElementById(c.getAttribute('aria-describedby'));
      tt.ok(!!d && d.textContent === 'V006 e1 > n1', 'aria-describedby → a megállapítás szövege');
      tt.eq(c.querySelector('.mg-m').textContent, '✖', 'szimbólum is (nem csak szín)');
      tt.ok(g.cell('rt0001', 'a').parentNode.classList.contains('is-blocked'), 'blokkolt sor');
      g.setDecorations({});
      tt.eq(c.getAttribute('aria-invalid'), null, 'a dekoráció törölhető');
    } finally {
      host.parentNode.removeChild(host);
    }
  });

  MA.selftest.register('grid: virtualizáció (> 1000 sor), billentyűzet és aria', function (tt) {
    var list = [];
    for (var i = 0; i < 1500; i++) { list.push({ uid: 'rv' + String(10000 + i), cells: { a: 'S' + String(i + 1), b: String(i) } }); }
    var changes = [];
    var g = create({ label: 'selftest-virtual', columns: [{ id: 'a', label: 'study' }, { id: 'b', label: 'e1', numeric: true }], rows: list,
      onChange: function (b) { changes.push(b); } });
    var host = h('div', { 'class': 'mg-selftest-host' }, g.el);
    document.body.appendChild(host);
    try {
      tt.ok(g.virtual(), 'virtuális mód 1000 sor fölött');
      tt.eq(g.table.getAttribute('aria-rowcount'), '1501', 'aria-rowcount = a teljes sorszám + fejléc');
      tt.ok(g.renderedCount() < 200, 'csak az ablak sorai vannak a DOM-ban (' + String(g.renderedCount()) + ')');
      g.setDecorations({ cells: { rv11400: { b: { kinds: ['error'], messages: ['V006'] } } } });
      g.focusCell('rv11400', 'b');
      var cell = g.cell('rv11400', 'b');
      tt.ok(!!cell && document.activeElement === cell, 'a fókuszált sor kirajzolva és fókuszban');
      tt.eq(cell.parentNode.getAttribute('aria-rowindex'), '1402', 'aria-rowindex a teljes táblában');
      tt.eq(cell.getAttribute('aria-invalid'), 'true', 'a dekoráció a később kirajzolt sorra is rákerül');
      var key = function (k, o) { document.activeElement.dispatchEvent(new KeyboardEvent('keydown', Object.assign({ key: k, bubbles: true, cancelable: true }, o || {}))); };
      key('End', { ctrlKey: true });
      tt.eq(g.active().rowIndex, 1499, 'Ctrl+End → az utolsó sor');
      tt.ok(document.activeElement === g.cell('rv11499', 'b'), 'az utolsó sor kirajzolva, fókuszban');
      key('Home', { ctrlKey: true });
      tt.ok(document.activeElement === g.cell('rv10000', 'a'), 'Ctrl+Home → az első cella');
      key('PageDown');
      tt.eq(g.active().row, 10, 'PageDown');
      key('7');
      document.activeElement.value = '77';
      key('Enter');
      tt.eq(g.get('rv10010', 'a'), '77', 'szerkesztés virtuális módban');
      tt.eq(changes.length, 1, 'onChange');
      g.setFilter(function (r) { return r.cells.b === '1200' || r.cells.b === '3'; });
      tt.eq(g.visibleCount(), 2, 'szűrés virtuális módban');
      g.setFilter(null);
      g.deleteRows(list.slice(0, 600).map(function (r) { return r.uid; }));
      tt.ok(!g.virtual(), 'a küszöb alá csökkenve teljes kirajzolás');
      tt.eq(g.renderedCount(), 900, 'minden sor a DOM-ban');
    } finally {
      host.parentNode.removeChild(host);
    }
  });
})();
