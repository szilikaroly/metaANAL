/* store.js — alkalmazásállapot, eseménybusz, UI-preferenciák, művelet-alapú undo/redo (terv 3.3, 7.6).
 *
 * Az állapot CSAK memóriában él. Projektadat soha nem kerül böngészőtárolóba; a localStorage-ba
 * kizárólag 'mag.pref.*' kulcsú UI-preferencia írható (MA.prefs), a token a munkamenet-tárolóban van
 * (api.js). A szabályt a selftest és a shell.spec.js ellenőrzi.
 */
(function () {
  'use strict';
  var MA = window.MA;

  // ---------------------------------------------------------------- állapot
  var state = {};
  var subs = [];   // {path, fn}

  function splitPath(path) {
    if (path === '' || path === null || path === undefined) { return []; }
    return String(path).split('.');
  }

  /** get(path) — pl. get('project'), get('project.outcomes'); ismeretlen út → undefined. */
  function get(path) {
    var parts = splitPath(path);
    var cur = state;
    for (var i = 0; i < parts.length; i++) {
      if (cur === null || cur === undefined || typeof cur !== 'object') { return undefined; }
      cur = cur[parts[i]];
    }
    return cur;
  }

  function related(a, b) {
    // a és b ugyanaz, vagy egyik a másik őse
    if (a === b || a === '' || b === '') { return true; }
    return a.indexOf(b + '.') === 0 || b.indexOf(a + '.') === 0;
  }

  function notify(path) {
    subs.slice().forEach(function (s) {
      if (related(s.path, path)) {
        try { s.fn(get(s.path), path); } catch (e) { if (window.console) { window.console.error(e); } }
      }
    });
  }

  /** set(path, value) — beállít (a köztes objektumokat létrehozza) és értesíti az érintett feliratkozókat. */
  function set(path, value) {
    var parts = splitPath(path);
    if (!parts.length) { throw new Error('store.set: üres út'); }
    var cur = state;
    for (var i = 0; i < parts.length - 1; i++) {
      var k = parts[i];
      if (k === '__proto__' || k === 'constructor' || k === 'prototype') { throw new Error('store.set: tiltott kulcs'); }
      if (cur[k] === null || typeof cur[k] !== 'object') { cur[k] = {}; }
      cur = cur[k];
    }
    var last = parts[parts.length - 1];
    if (last === '__proto__' || last === 'constructor' || last === 'prototype') { throw new Error('store.set: tiltott kulcs'); }
    cur[last] = value;
    notify(String(path));
    return value;
  }

  /** update(path, fn) — set(path, fn(get(path))). */
  function update(path, fn) { return set(path, fn(get(path))); }

  /** subscribe(path, fn) → leiratkozó; fn(value, changedPath) ha az út vagy őse/leszármazottja változik. */
  function subscribe(path, fn) {
    var entry = { path: String(path || ''), fn: fn };
    subs.push(entry);
    return function () { subs = subs.filter(function (s) { return s !== entry; }); };
  }

  /** reset(path?) — út törlése (vagy a teljes állapoté, pl. munkamenet-vesztéskor). */
  function reset(path) {
    if (!path) { state = {}; notify(''); return; }
    set(path, undefined);
  }

  // ---------------------------------------------------------------- eseménybusz
  var handlers = {};

  /** on(event, fn) → leiratkozó. Események: 'route', 'lang', 'theme', 'session', 'changes', 'refresh', 'toast'. */
  function on(ev, fn) {
    (handlers[ev] = handlers[ev] || []).push(fn);
    return function () { handlers[ev] = (handlers[ev] || []).filter(function (x) { return x !== fn; }); };
  }

  function emit(ev, payload) {
    (handlers[ev] || []).slice().forEach(function (fn) {
      try { fn(payload); } catch (e) { if (window.console) { window.console.error(e); } }
    });
  }

  // ---------------------------------------------------------------- preferenciák (localStorage, csak 'mag.pref.*')
  var PREF_PREFIX = 'mag.pref.';
  var PREF_KEY_RE = /^[a-z][a-zA-Z0-9_.-]{0,63}$/;
  var PREF_MAX_LEN = 256;
  var prefMemory = {};

  function prefStorage() {
    try { return window.localStorage; } catch (e) { return null; }
  }

  /** prefs.get(name, fallback) — csak rövid UI-preferencia (nyelv, téma, utolsó nézet …). */
  function prefGet(name, fallback) {
    if (!PREF_KEY_RE.test(name)) { throw new Error('prefs: érvénytelen kulcs'); }
    var ls = prefStorage();
    var v = null;
    try { v = ls ? ls.getItem(PREF_PREFIX + name) : null; } catch (e) { v = null; }
    if (v === null && Object.prototype.hasOwnProperty.call(prefMemory, name)) { v = prefMemory[name]; }
    return v === null || v === undefined ? (fallback === undefined ? null : fallback) : v;
  }

  /** prefs.set(name, value) — sztring, ≤ 256 karakter; projektadatot ide TILOS írni. null → törlés. */
  function prefSet(name, value) {
    if (!PREF_KEY_RE.test(name)) { throw new Error('prefs: érvénytelen kulcs'); }
    var ls = prefStorage();
    if (value === null || value === undefined) {
      delete prefMemory[name];
      try { if (ls) { ls.removeItem(PREF_PREFIX + name); } } catch (e) { /* tároló nem elérhető */ }
      return;
    }
    var s = String(value);
    if (s.length > PREF_MAX_LEN) { throw new Error('prefs: túl hosszú érték (UI-preferencia, nem adat)'); }
    prefMemory[name] = s;
    try { if (ls) { ls.setItem(PREF_PREFIX + name, s); } } catch (e) { /* privát mód: csak memóriában */ }
  }

  // ---------------------------------------------------------------- undo / redo
  /**
   * Művelet-típusok (táblaszerkesztés):
   *   {type:'cell', dataset, row_uid, field, before, after}
   *   {type:'rows-insert', dataset, index, rows:[{row_uid, cells:{field: text}}]}
   *   {type:'rows-delete', dataset, index, rows:[…]}            (a rows-insert inverze)
   *   {type:'batch', label?, ops:[…]}                           (pl. TSV-beillesztés, átváltó-tranzakció)
   *   egyéb: {type:'custom', do:{…}, undo:{…}} — az apply maga értelmezi
   */
  function invertOp(op) {
    if (!op || typeof op !== 'object') { throw new Error('invertOp: hiányzó művelet'); }
    switch (op.type) {
      case 'cell':
        return Object.assign({}, op, { before: op.after, after: op.before });
      case 'rows-insert':
        return Object.assign({}, op, { type: 'rows-delete' });
      case 'rows-delete':
        return Object.assign({}, op, { type: 'rows-insert' });
      case 'batch':
        return Object.assign({}, op, { ops: op.ops.slice().reverse().map(invertOp) });
      case 'custom':
        return Object.assign({}, op, { 'do': op.undo, undo: op['do'] });
      default:
        throw new Error('invertOp: ismeretlen művelet-típus: ' + op.type);
    }
  }

  /**
   * createHistory({apply(op, meta), limit=500, onChange?}) → history
   *   push(op)  — egy MÁR végrehajtott műveletet rögzít (a redo-verem törlődik)
   *   undo()    — az utolsó művelet inverzét ÚJ műveletként alkalmazza (apply(inv, {kind:'undo'}));
   *               a szerveren ez új, naplózott mentés lesz (terv 3.3) → visszaadja az inverzet vagy null-t
   *   redo()    — az utoljára visszavont műveletet újra alkalmazza (apply(op, {kind:'redo'}))
   *   canUndo(), canRedo(), clear(), size() → {undo, redo}, peek() → utolsó művelet
   */
  function createHistory(opts) {
    var apply = opts && opts.apply;
    if (typeof apply !== 'function') { throw new Error('createHistory: apply(op) kötelező'); }
    var limit = (opts && opts.limit) || 500;
    var undoStack = [];
    var redoStack = [];
    function changed() { if (opts.onChange) { opts.onChange({ undo: undoStack.length, redo: redoStack.length }); } }
    return {
      push: function (op) {
        invertOp(op);                       // validálás: invertálható-e
        undoStack.push(op);
        if (undoStack.length > limit) { undoStack.shift(); }
        redoStack = [];
        changed();
      },
      undo: function () {
        if (!undoStack.length) { return null; }
        var op = undoStack.pop();
        var inv = invertOp(op);
        apply(inv, { kind: 'undo', original: op });
        redoStack.push(op);
        changed();
        return inv;
      },
      redo: function () {
        if (!redoStack.length) { return null; }
        var op = redoStack.pop();
        apply(op, { kind: 'redo' });
        undoStack.push(op);
        changed();
        return op;
      },
      canUndo: function () { return undoStack.length > 0; },
      canRedo: function () { return redoStack.length > 0; },
      peek: function () { return undoStack.length ? undoStack[undoStack.length - 1] : null; },
      size: function () { return { undo: undoStack.length, redo: redoStack.length }; },
      clear: function () { undoStack = []; redoStack = []; changed(); }
    };
  }

  MA.store = {
    get: get,
    set: set,
    update: update,
    subscribe: subscribe,
    reset: reset,
    createHistory: createHistory,
    invertOp: invertOp
  };
  MA.bus = { on: on, emit: emit };
  MA.prefs = { get: prefGet, set: prefSet, PREFIX: PREF_PREFIX };
})();
