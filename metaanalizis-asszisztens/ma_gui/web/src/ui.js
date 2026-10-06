/* ui.js — közös felületi elemek: toast, modális, megerősítés, jelvény, menü, fülsor, segédek.
 * Minden szöveg textContent-tel kerül be (MA.dom.h). A szín sosem egyedüli jelölés: minden
 * állapotjelvény szimbólumot ÉS szöveget is visz (Okabe–Ito színek a CSS-tokenekből).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;

  // ---------------------------------------------------------------- szimbólumok
  var SYMBOLS = {
    error: '✖', warning: '⚠', info: 'ℹ', ok: '✔', success: '✔', stale: '⟳', blocker: '⛔',
    estimated: '◆', pending: '…', progress: '◔', neutral: '•',
    // képesség-állapotok (3.5.0 jelmagyarázat; legacy = bridge-adapter)
    cap_ok: '●', cap_unusable: '◐', cap_legacy: '◑', cap_absent: '○'
  };

  function symbol(kind) { return SYMBOLS[kind] || SYMBOLS.neutral; }

  /**
   * badge(kind, text, opts?) → <span class="badge is-<kind>">
   *   kind: error | warning | info | ok | stale | blocker | estimated | progress | neutral
   *   opts.symbol (felülírja), opts.title, opts.srLabel (képernyőolvasónak, pl. „hiba:”)
   */
  function badge(kind, text, opts) {
    opts = opts || {};
    var sym = opts.symbol === undefined ? symbol(kind) : opts.symbol;
    return h('span', { 'class': ['badge', 'is-' + (kind || 'neutral')], title: opts.title || null },
      sym ? h('span', { 'class': 'badge-sym', 'aria-hidden': 'true' }, sym) : null,
      opts.srLabel ? h('span', { 'class': 'sr-only' }, opts.srLabel + ' ') : null,
      text === undefined || text === null ? null : h('span', { 'class': 'badge-text' }, String(text)));
  }

  /** numText(i18nObj | string | null) — a motor kész számszövege az aktuális nyelven; hiány → „—”. */
  function numText(v) { return MA.i18n.pick(v, '—'); }

  /** num(i18nObj) → <span class="num"> a motor szövegével (tabular-nums). */
  function num(v, attrs) {
    return h('span', Object.assign({ 'class': 'num' }, attrs || {}), numText(v));
  }

  // ---------------------------------------------------------------- toast
  var MAX_TOASTS = 5;
  var toastRegion = null;
  var alertRegion = null;

  function regions() {
    if (!toastRegion || !document.body.contains(toastRegion)) {
      toastRegion = h('div', { 'class': 'toast-region', role: 'status', 'aria-live': 'polite', 'aria-relevant': 'additions', id: 'ma-toasts' });
      alertRegion = h('div', { 'class': 'toast-region toast-region-alert', role: 'alert', 'aria-live': 'assertive', id: 'ma-alerts' });
      document.body.appendChild(toastRegion);
      document.body.appendChild(alertRegion);
    }
    return { polite: toastRegion, alert: alertRegion };
  }

  /**
   * toast({kind, title, message?, details?: [string], timeout?, code?, actions?: [{label, onClick}]})
   * → {el, close()}. A hibák (error) nem tűnnek el maguktól; a többi alapból 6 s után.
   */
  function toast(opts) {
    opts = opts || {};
    var kind = opts.kind || 'info';
    var r = regions();
    var region = kind === 'error' ? r.alert : r.polite;
    var all = MA.dom.$$('.toast', r.polite).concat(MA.dom.$$('.toast', r.alert));
    if (all.length >= MAX_TOASTS) { all[0].remove(); }
    var el;
    var close = function () { if (el && el.parentNode) { el.parentNode.removeChild(el); } };
    el = h('div', { 'class': ['toast', 'is-' + kind], dataset: { code: opts.code || null } },
      h('span', { 'class': 'toast-sym', 'aria-hidden': 'true' }, symbol(kind)),
      h('div', { 'class': 'toast-body' },
        h('div', { 'class': 'toast-title' }, opts.title || ''),
        opts.message ? h('div', { 'class': 'toast-msg' }, opts.message) : null,
        opts.details && opts.details.length
          ? h('ul', { 'class': 'toast-details' }, opts.details.map(function (d) { return h('li', null, String(d)); }))
          : null,
        opts.actions && opts.actions.length
          ? h('div', { 'class': 'toast-actions' }, opts.actions.map(function (a) {
            return h('button', { type: 'button', 'class': 'btn btn-sm', onclick: function () { a.onClick(); close(); } }, a.label);
          }))
          : null),
      h('button', { type: 'button', 'class': 'btn-icon toast-close', 'aria-label': MA.i18n.t('toast.close'), onclick: close }, '×'));
    region.appendChild(el);
    var timeout = opts.timeout === undefined ? (kind === 'error' ? 0 : 6000) : opts.timeout;
    if (timeout > 0) { setTimeout(close, timeout); }
    if (MA.bus) { MA.bus.emit('toast', { kind: kind, title: opts.title, code: opts.code || null }); }
    return { el: el, close: close };
  }

  // ---------------------------------------------------------------- modális
  var openModals = [];

  function appRoot() { return document.getElementById('app'); }

  /**
   * modal({title, body: Node|[Node], actions?: [{label, kind?: 'primary'|'danger'|'ghost', onClick?(close) → false = nyitva marad}],
   *        onClose?, size?: 'sm'|'md'|'lg', closeLabel?}) → {el, body, close()}
   * role=dialog, aria-modal, fókuszcsapda, Esc = bezárás, a fókusz visszakerül a megnyitóra; a háttér inert.
   */
  function modal(opts) {
    opts = opts || {};
    var opener = document.activeElement;
    var titleId = MA.dom.uid('modal-title');
    var bodyEl = h('div', { 'class': 'modal-body' }, opts.body || null);
    var closed = false;
    var backdrop, dialog;

    function close(result) {
      if (closed) { return; }
      closed = true;
      document.removeEventListener('keydown', onKey, true);
      if (backdrop.parentNode) { backdrop.parentNode.removeChild(backdrop); }
      openModals = openModals.filter(function (m) { return m !== api; });
      if (!openModals.length && appRoot()) { appRoot().inert = false; appRoot().removeAttribute('aria-hidden'); }
      if (opener && typeof opener.focus === 'function' && document.body.contains(opener)) { opener.focus(); }
      if (opts.onClose) { opts.onClose(result); }
    }

    function onKey(ev) {
      if (openModals[openModals.length - 1] !== api) { return; }
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); close(null); return; }
      if (ev.key === 'Tab') {
        var f = MA.dom.focusables(dialog);
        if (!f.length) { ev.preventDefault(); dialog.focus(); return; }
        var first = f[0], last = f[f.length - 1];
        if (ev.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { ev.preventDefault(); last.focus(); } else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
      }
    }

    var actions = (opts.actions || []).map(function (a) {
      return h('button', {
        type: 'button',
        'class': ['btn', a.kind === 'primary' && 'btn-primary', a.kind === 'danger' && 'btn-danger', a.kind === 'ghost' && 'btn-ghost'],
        onclick: function () {
          var keep = a.onClick ? a.onClick(close) : undefined;
          if (keep !== false) { close(a.value === undefined ? a.label : a.value); }
        }
      }, a.label);
    });

    dialog = h('div', { 'class': ['modal', 'modal-' + (opts.size || 'md')], role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId, tabindex: '-1' },
      h('div', { 'class': 'modal-head' },
        h('h2', { id: titleId, 'class': 'modal-title' }, opts.title || ''),
        h('button', { type: 'button', 'class': 'btn-icon', 'aria-label': opts.closeLabel || MA.i18n.t('modal.close'), onclick: function () { close(null); } }, '×')),
      bodyEl,
      actions.length ? h('div', { 'class': 'modal-actions' }, actions) : null);
    backdrop = h('div', { 'class': 'modal-backdrop', onmousedown: function (ev) { if (ev.target === backdrop && opts.dismissable !== false) { close(null); } } }, dialog);

    var api = { el: dialog, body: bodyEl, close: close };
    openModals.push(api);
    document.body.appendChild(backdrop);
    if (appRoot()) { appRoot().inert = true; appRoot().setAttribute('aria-hidden', 'true'); }
    document.addEventListener('keydown', onKey, true);
    var f = MA.dom.focusables(bodyEl);
    (f[0] || MA.dom.focusables(dialog)[0] || dialog).focus();
    return api;
  }

  /** confirm({title, message, okLabel?, cancelLabel?, danger?}) → Promise<boolean> */
  function confirm(opts) {
    return new Promise(function (resolve) {
      var answered = false;
      modal({
        title: opts.title,
        size: 'sm',
        body: h('p', null, opts.message || ''),
        actions: [
          { label: opts.cancelLabel || MA.i18n.t('common.cancel'), kind: 'ghost', onClick: function () { answered = true; resolve(false); } },
          { label: opts.okLabel || MA.i18n.t('common.ok'), kind: opts.danger ? 'danger' : 'primary', onClick: function () { answered = true; resolve(true); } }
        ],
        onClose: function () { if (!answered) { resolve(false); } }
      });
    });
  }

  // ---------------------------------------------------------------- lenyíló menü (disclosure)
  /**
   * menu({label, items: [{label, onSelect?, href?}], buttonAttrs?}) → <div class="menu">
   * Gomb aria-expanded/aria-controls; ↑/↓ a tételek közt, Esc bezár, Enter választ.
   */
  function menu(opts) {
    var listId = MA.dom.uid('menu');
    var btn, list;
    function setOpen(open) {
      list.hidden = !open;
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) { var f = MA.dom.focusables(list); if (f[0]) { f[0].focus(); } }
    }
    var items = opts.items.map(function (it) {
      var attrs = {
        role: 'menuitem', 'class': 'menu-item',
        onclick: function () { setOpen(false); btn.focus(); if (it.onSelect) { it.onSelect(); } }
      };
      return it.href ? h('a', Object.assign({ href: it.href }, attrs), it.label) : h('button', Object.assign({ type: 'button' }, attrs), it.label);
    });
    list = h('div', { 'class': 'menu-list', role: 'menu', id: listId, hidden: true,
      onkeydown: function (ev) {
        var f = MA.dom.focusables(list);
        var i = f.indexOf(document.activeElement);
        if (ev.key === 'ArrowDown') { ev.preventDefault(); f[(i + 1) % f.length].focus(); } else if (ev.key === 'ArrowUp') { ev.preventDefault(); f[(i - 1 + f.length) % f.length].focus(); } else if (ev.key === 'Escape') { ev.preventDefault(); setOpen(false); btn.focus(); }
      } }, items);
    btn = h('button', Object.assign({ type: 'button', 'class': 'btn btn-ghost menu-button', 'aria-haspopup': 'true', 'aria-expanded': 'false', 'aria-controls': listId,
      onclick: function () { setOpen(list.hidden); } }, opts.buttonAttrs || {}), opts.label);
    var wrap = h('div', { 'class': 'menu' }, btn, list);
    wrap.addEventListener('focusout', function (ev) { if (!wrap.contains(ev.relatedTarget)) { list.hidden = true; btn.setAttribute('aria-expanded', 'false'); } });
    return wrap;
  }

  // ---------------------------------------------------------------- fülsor (WAI-ARIA tabs, kézi aktiválás)
  /**
   * tabs({label, tabs: [{id, label}], active, onSelect(id)}) → <div role="tablist">
   * ←/→/Home/End mozgat, Enter/Space aktivál; a panelt a hívó rendereli (aria-controls: opts.panelId).
   */
  function tabs(opts) {
    var buttons = [];
    var list = h('div', { role: 'tablist', 'class': 'tabs', 'aria-label': opts.label || null });
    function select(id) {
      buttons.forEach(function (b) {
        var on = b.dataset.tab === id;
        b.setAttribute('aria-selected', on ? 'true' : 'false');
        b.tabIndex = on ? 0 : -1;
      });
      opts.onSelect(id);
    }
    opts.tabs.forEach(function (t) {
      var b = h('button', { type: 'button', role: 'tab', 'class': 'tab', dataset: { tab: t.id }, 'aria-selected': t.id === opts.active ? 'true' : 'false',
        'aria-controls': opts.panelId || null, tabindex: t.id === opts.active ? '0' : '-1', onclick: function () { select(t.id); } }, t.label);
      buttons.push(b);
      list.appendChild(b);
    });
    list.addEventListener('keydown', function (ev) {
      var i = buttons.indexOf(document.activeElement);
      if (i < 0) { return; }
      var j = null;
      if (ev.key === 'ArrowRight') { j = (i + 1) % buttons.length; } else if (ev.key === 'ArrowLeft') { j = (i - 1 + buttons.length) % buttons.length; } else if (ev.key === 'Home') { j = 0; } else if (ev.key === 'End') { j = buttons.length - 1; }
      if (j !== null) { ev.preventDefault(); buttons[j].focus(); }
    });
    list.select = select;
    return list;
  }

  // ---------------------------------------------------------------- segédek
  /** debounce(fn, ms) → függvény .cancel()-lel és .flush()-sal (pl. 250 ms-os validálás). */
  function debounce(fn, ms) {
    var t = null, lastArgs = null, self = null;
    function d() {
      lastArgs = arguments;
      self = this;
      clearTimeout(t);
      t = setTimeout(function () { t = null; fn.apply(self, lastArgs); }, ms);
    }
    d.cancel = function () { clearTimeout(t); t = null; };
    d.flush = function () { if (t) { clearTimeout(t); t = null; fn.apply(self, lastArgs); } };
    return d;
  }

  /** copyButton(getText, label?) — vágólapra másoló gomb (pl. parancs-előnézet). */
  function copyButton(getText, label) {
    return h('button', { type: 'button', 'class': 'btn btn-sm',
      onclick: function () {
        var txt = typeof getText === 'function' ? getText() : String(getText);
        var p = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(txt) : Promise.reject(new Error('no clipboard'));
        p.then(function () { toast({ kind: 'success', title: MA.i18n.t('common.copied') }); },
          function () { toast({ kind: 'warning', title: MA.i18n.t('common.copyFailed') }); });
      } }, label || MA.i18n.t('common.copy'));
  }

  /** emptyState(key, args?) — üres/hiányzó adat jelzése. */
  function emptyState(key, args) { return h('p', { 'class': 'empty-state', i18n: key, i18nArgs: args || null }); }

  /** spinner(labelKey?) — betöltésjelző (aria-busy a szülőn a hívó dolga). */
  function spinner(labelKey) {
    return h('span', { 'class': 'spinner', role: 'status' }, h('span', { 'class': 'spinner-dot', 'aria-hidden': 'true' }), h('span', { 'class': 'spinner-text', i18n: labelKey || 'common.loading' }));
  }

  /** errorBox(err) — beágyazott hibapanel ApiError-ból (pl. egy képernyő-szakasz nem tölthető be). */
  function errorBox(err) {
    var code = err && err.code ? err.code : 'INTERNAL';
    var key = 'err.' + code;
    return h('div', { 'class': 'error-box', role: 'note' },
      badge('error', MA.i18n.has(key) ? MA.i18n.t(key) : MA.i18n.t('err.UNKNOWN', { code: code })),
      err && err.message && err.message !== code ? h('span', { 'class': 'error-box-msg' }, err.message) : null);
  }

  MA.ui = {
    SYMBOLS: SYMBOLS,
    symbol: symbol,
    badge: badge,
    num: num,
    numText: numText,
    toast: toast,
    modal: modal,
    confirm: confirm,
    menu: menu,
    tabs: tabs,
    debounce: debounce,
    copyButton: copyButton,
    emptyState: emptyState,
    spinner: spinner,
    errorBox: errorBox
  };
})();
