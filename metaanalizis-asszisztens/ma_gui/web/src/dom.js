/* dom.js — biztonságos DOM-építők (terv 7.1 T6, 6.8).
 *
 * Minden szöveg textContent-tel / szövegcsomóponttal kerül a DOM-ba; HTML-sztringből
 * soha nem építünk. Az SVG createElementNS-szel készül. A stílus csak CSSOM-mal
 * (el.style.prop) állítható, mert a CSP a style-attribútumot tiltja.
 */
(function () {
  'use strict';
  var MA = window.MA;

  var SVG_NS = 'http://www.w3.org/2000/svg';

  // HTML-t értelmező vagy eseménykezelőt sztringből adó attribútumok: tiltva.
  var FORBIDDEN_ATTRS = {
    innerhtml: 1, outerhtml: 1, srcdoc: 1, style: 1, formaction: 1, action: 1
  };
  var URL_ATTRS = { href: 1, src: 1, 'xlink:href': 1, poster: 1, data: 1, cite: 1 };
  var BOOL_PROPS = { checked: 1, disabled: 1, hidden: 1, selected: 1, readOnly: 1, required: 1, multiple: 1, inert: 1 };

  var uidCounter = 0;

  function DomError(message) {
    var e = new Error(message);
    e.name = 'DomError';
    return e;
  }

  /** Biztonságos URL? Csak hash, relatív saját útvonal (/f/…, /api/…), blob: és data:image/. */
  function safeUrl(value) {
    var v = String(value);
    var lower = v.replace(/[\u0000- ]+/g, '').toLowerCase();
    if (lower.charAt(0) === '#') { return true; }
    if (lower.indexOf('blob:') === 0) { return true; }
    if (lower.indexOf('data:image/') === 0 && lower.indexOf('data:image/svg') !== 0) { return true; }
    if (/^\/(?!\/)/.test(lower)) { return true; }        // gyökér-relatív, nem protokoll-relatív
    return false;
  }

  function appendChildren(el, children) {
    for (var i = 0; i < children.length; i++) {
      var c = children[i];
      if (c === null || c === undefined || c === false || c === true) { continue; }
      if (Array.isArray(c)) { appendChildren(el, c); continue; }
      if (typeof c === 'string' || typeof c === 'number') {
        el.appendChild(document.createTextNode(String(c)));
        continue;
      }
      if (c && typeof c.nodeType === 'number') { el.appendChild(c); continue; }
      throw DomError('dom: ismeretlen gyermek-típus');
    }
    return el;
  }

  function classList(value) {
    if (Array.isArray(value)) {
      return value.filter(function (x) { return !!x; }).join(' ');
    }
    if (value && typeof value === 'object') {
      return Object.keys(value).filter(function (k) { return !!value[k]; }).join(' ');
    }
    return value ? String(value) : '';
  }

  function setAttrs(el, attrs, isSvg) {
    if (!attrs) { return; }
    Object.keys(attrs).forEach(function (key) {
      var val = attrs[key];
      if (val === undefined || val === null || val === false) {
        if (BOOL_PROPS[key] && !isSvg) { el[key] = false; }
        return;
      }
      var lk = key.toLowerCase();
      if (FORBIDDEN_ATTRS[lk]) {
        if (lk === 'style') { throw DomError('dom: a style-attribútum tiltott (CSP); használd: styles: {…}'); }
        throw DomError('dom: tiltott attribútum: ' + key);
      }
      if (key === 'text') { el.textContent = String(val); return; }
      if (key === 'i18n') {
        el.setAttribute('data-i18n', String(val));
        if (attrs.i18nArgs) { el.setAttribute('data-i18n-args', JSON.stringify(attrs.i18nArgs)); }
        el.textContent = MA.i18n ? MA.i18n.t(String(val), attrs.i18nArgs) : String(val);
        return;
      }
      if (key === 'i18nArgs') { return; }
      if (key === 'i18nAttrs') {
        // {'aria-label': 'kulcs', title: 'kulcs'} — nyelvváltáskor MA.i18n.apply frissíti
        var pairs = [];
        Object.keys(val).forEach(function (a) {
          var la = a.toLowerCase();
          if (la.indexOf('on') === 0 || FORBIDDEN_ATTRS[la] || URL_ATTRS[la]) {
            throw DomError('dom: i18nAttrs nem állíthat ilyen attribútumot: ' + a);
          }
          el.setAttribute(a, MA.i18n ? MA.i18n.t(val[a]) : val[a]);
          pairs.push(a + '=' + val[a]);
        });
        el.setAttribute('data-i18n-attrs', pairs.join(';'));
        return;
      }
      if (key === 'class' || key === 'className') {
        var cls = classList(val);
        if (cls) { el.setAttribute('class', cls); }
        return;
      }
      if (key === 'styles') {
        Object.keys(val).forEach(function (p) {
          if (p.indexOf('-') >= 0) { el.style.setProperty(p, String(val[p])); } else { el.style[p] = String(val[p]); }
        });
        return;
      }
      if (key === 'dataset') {
        Object.keys(val).forEach(function (d) {
          if (val[d] !== undefined && val[d] !== null) { el.dataset[d] = String(val[d]); }
        });
        return;
      }
      if (key === 'on') {
        Object.keys(val).forEach(function (ev) { el.addEventListener(ev, val[ev]); });
        return;
      }
      if (lk.indexOf('on') === 0) {
        if (typeof val !== 'function') { throw DomError('dom: eseménykezelő csak függvény lehet: ' + key); }
        el.addEventListener(lk.slice(2), val);
        return;
      }
      if (key === 'ref') {
        if (typeof val === 'function') { val(el); }
        return;
      }
      if (URL_ATTRS[lk]) {
        if (!safeUrl(val)) { throw DomError('dom: nem engedett URL a(z) ' + key + ' attribútumban'); }
      }
      if (!isSvg && (key === 'value')) { el.value = String(val); return; }
      if (!isSvg && BOOL_PROPS[key]) { el[key] = !!val; return; }
      if (!isSvg && key === 'htmlFor') { el.setAttribute('for', String(val)); return; }
      if (val === true) { el.setAttribute(key, ''); return; }
      el.setAttribute(key, String(val));
    });
  }

  /**
   * h(tag, attrs, ...children) → HTMLElement
   * attrs: class/className (sztring | tömb | {osztály: bool}), id, text, i18n, i18nArgs, i18nAttrs,
   *   role, aria-*, data-*, dataset{}, styles{} (CSSOM), on{ev: fn}, onclick: fn, ref: fn(el),
   *   value, checked, disabled, hidden, type, href (csak biztonságos), title, tabindex …
   * children: sztring/szám (szövegként), Node, tömb (lapítva), null/false (kihagyva).
   */
  function h(tag, attrs) {
    if (typeof tag !== 'string' || !/^[a-z][a-z0-9-]*$/.test(tag)) { throw DomError('dom: érvénytelen tag'); }
    if (tag === 'script' || tag === 'style' || tag === 'iframe' || tag === 'object' || tag === 'embed' || tag === 'link' || tag === 'meta' || tag === 'base') {
      throw DomError('dom: tiltott elem: ' + tag);
    }
    var el = document.createElement(tag);
    var rest = Array.prototype.slice.call(arguments, 2);
    if (attrs && (typeof attrs !== 'object' || Array.isArray(attrs) || typeof attrs.nodeType === 'number')) {
      rest.unshift(attrs);
      attrs = null;
    }
    setAttrs(el, attrs, false);
    return appendChildren(el, rest);
  }

  /** svg(tag, attrs, ...children) → SVGElement (createElementNS). A <text>-be csak szöveg kerülhet. */
  function svg(tag, attrs) {
    if (typeof tag !== 'string' || !/^[a-zA-Z][a-zA-Z0-9]*$/.test(tag)) { throw DomError('dom: érvénytelen SVG-tag'); }
    if (tag === 'script' || tag === 'foreignObject' || tag === 'style') { throw DomError('dom: tiltott SVG-elem: ' + tag); }
    var el = document.createElementNS(SVG_NS, tag);
    var rest = Array.prototype.slice.call(arguments, 2);
    if (attrs && (typeof attrs !== 'object' || Array.isArray(attrs) || typeof attrs.nodeType === 'number')) {
      rest.unshift(attrs);
      attrs = null;
    }
    setAttrs(el, attrs, true);
    return appendChildren(el, rest);
  }

  function text(s) { return document.createTextNode(s === null || s === undefined ? '' : String(s)); }

  function frag() {
    var f = document.createDocumentFragment();
    return appendChildren(f, Array.prototype.slice.call(arguments));
  }

  function clear(el) {
    while (el.firstChild) { el.removeChild(el.firstChild); }
    return el;
  }

  function mount(el) {
    clear(el);
    return appendChildren(el, Array.prototype.slice.call(arguments, 1));
  }

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function on(el, type, fn, opts) {
    el.addEventListener(type, fn, opts);
    return function off() { el.removeEventListener(type, fn, opts); };
  }

  function uid(prefix) {
    uidCounter += 1;
    return (prefix || 'ma') + '-' + uidCounter;
  }

  /** Fókuszálható elemek egy gyökéren belül (modális fókuszcsapdához, roving tabindexhez). */
  function focusables(root) {
    return $$('a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])', root)
      .filter(function (el) { return !el.hidden && el.getClientRects().length > 0; });
  }

  /** Gépel-e a felhasználó (input/textarea/select/contenteditable)? — globális gyorsbillentyűkhöz. */
  function isTyping(target) {
    var t = target || document.activeElement;
    if (!t || !t.tagName) { return false; }
    var tag = t.tagName.toLowerCase();
    if (tag === 'textarea' || tag === 'select') { return true; }
    if (tag === 'input') {
      var type = (t.getAttribute('type') || 'text').toLowerCase();
      return ['button', 'checkbox', 'radio', 'submit', 'reset', 'range', 'color', 'file'].indexOf(type) < 0;
    }
    return !!t.isContentEditable;
  }

  MA.dom = {
    SVG_NS: SVG_NS,
    h: h,
    svg: svg,
    text: text,
    frag: frag,
    clear: clear,
    mount: mount,
    $: $,
    $$: $$,
    on: on,
    uid: uid,
    focusables: focusables,
    isTyping: isTyping,
    safeUrl: safeUrl
  };
})();
