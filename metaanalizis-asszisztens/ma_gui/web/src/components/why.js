/* components/why.js — „Miért?” magyarázó (11. fejezet, 6. döntés: kezdőknek is érthető indoklás; 3.5.15 KB).
 *
 * Újrahasznosítható súgó-buborék minden megállapításhoz, szabályhoz és opcióhoz. A szöveg forrása a KB-tétel
 * (GET /api/kb/item/<id>: {id, table, item{…}, local_only, note}) és a hívó által átadott motor-szövegek
 * (a megállapítás title/detail/advice/source mezői) — a felület semmit nem talál ki és nem számol.
 *
 * MA.why:
 *   button(spec, opts?) → <button>           „Miért?” gomb; kattintásra buborék (role=dialog, nem modális)
 *       opts: {label?, compact?: true (csak „?”), id?}
 *   open(spec, anchor?) → {el, close}         buborék megnyitása (Esc / kattintás kívül / fókusz elhagyása zárja)
 *   panel(spec) → <div>                       ugyanez beágyazott panelként (a KB-részek aszinkron töltődnek)
 *   item(id) → Promise<item|null>             KB-tétel (gyorsítótárazva; ismeretlen azonosító → null)
 *   openItem(id) → modal                      a KB-tétel minden mezője (szövegrésznél „helyi forrás — nem exportálható”)
 *   kbButton(id) → <button>                   „ⓚ ID” → openItem
 *   strength(s) → jelvény                     KB-szabály erőssége (must | should | consider | avoid)
 *   close()                                   a nyitott buborék bezárása
 *   spec = {kb?: id | [id…], code?, title?, detail?, advice?, source?,
 *           plain?: {asks?, because?, change?, uncertain?},     ← AI-vázlat / egyszerű nyelvű indoklás (6. döntés)
 *           evidence?: {quote?, page?, locator?}}
 * Minden szöveg textContent-tel kerül be; a motor {hu, en} szövegeit az MA.i18n.pick választja.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };

  var cache = {};
  var cur = null;
  var STRENGTH = { must: 1, should: 1, consider: 1, avoid: 1 };
  var FIELDS = ['condition', 'recommendation', 'rationale', 'title', 'body', 'text', 'how_to_verify', 'expression', 'variables',
    'strength', 'stage_id', 'applies_to', 'kind', 'checklist', 'section', 'machine_check', 'source_ids', 'source_id', 'locator', 'ref', 'notes', 'citation'];

  function item(id) {
    if (!cache[id]) {
      cache[id] = MA.api.get('/api/kb/item/' + encodeURIComponent(id), { toast: false }).then(function (env) {
        var d = env.data || {};
        var it = d.item && typeof d.item === 'object' ? d.item : d;
        return Object.assign({}, it, { id: d.id || id, table: d.table || it.table || null, local_only: !!d.local_only, note: d.note || null });
      }, function (err) {
        if (err.code === 'NOT_FOUND') { return null; }
        delete cache[id];
        throw err;
      });
    }
    return cache[id];
  }

  function kbIds(spec) { return MA.proc.refs(spec.kb === undefined ? spec.code : spec.kb); }

  function sec(key, text, cls) {
    var s = pick(text);
    return s ? h('div', { 'class': ['why-sec', cls] }, h('h4', { i18n: key }), h('p', null, s)) : null;
  }

  /** a KB-szabály erőssége jelvényként: kötelező (!), kerülendő (⊘), ajánlott / mérlegelendő (ℹ) */
  function strengthBadge(s) {
    if (!STRENGTH[s]) { return null; }
    return MA.ui.badge(s === 'must' || s === 'avoid' ? 'warning' : 'info', t('why.strength.' + s), s === 'must' ? { symbol: '!' } : (s === 'avoid' ? { symbol: '⊘' } : null));
  }

  /** A KB-tétel magyarázó részei (a hívó motor-szövegei elsőbbséget kapnak). */
  function kbPart(spec, it) {
    if (!it) { return h('p', { 'class': 'muted why-nokb', i18n: 'why.noKb' }); }
    var title = pick(it.title || it.condition || it.name || it.text);
    return h('div', { 'class': 'why-kb' },
      h('p', { 'class': 'why-kbhead' }, MA.ui.badge('neutral', it.id || it.rule_id || ''), ' ', strengthBadge(it.strength),
        it.local_only ? MA.ui.badge('warning', pick(it.note) || t('kb.localOnly')) : null),
      title && title !== pick(spec.title) ? h('p', { 'class': 'why-kbtitle' }, title) : null,
      spec.detail ? null : sec('why.what', it.title ? it.condition || it.body : it.body),
      spec.advice ? null : sec('why.todo', it.recommendation || it.how_to_verify),
      sec('why.why', it.rationale),
      sec('why.src', [it.source_ids || it.source_id, it.locator].filter(function (x) { return !!x; }).join(' · '), 'why-srcline'));
  }

  /** panel(spec) → <div class="why-panel"> (a KB-rész a kérés után töltődik be). */
  function panel(spec) {
    spec = spec || {};
    var ids = kbIds(spec);
    var pl = spec.plain || {};
    var ev = spec.evidence || null;
    var kbHost = ids.length ? h('div', { 'class': 'why-kbhost', 'aria-busy': 'true' }, MA.ui.spinner()) : null;
    var el = h('div', { 'class': 'why-panel' },
      sec('why.what', spec.detail),
      sec('why.todo', spec.advice),
      sec('why.asks', pl.asks), sec('why.because', pl.because), sec('why.change', pl.change),
      sec('why.uncertain', pl.uncertain, 'why-uncertain'),
      ev && (ev.quote || ev.page || ev.locator) ? h('div', { 'class': 'why-sec' }, h('h4', { i18n: 'why.evidence' }),
        ev.quote ? h('blockquote', { 'class': 'why-quote' }, '„' + pick(ev.quote) + '”') : null,
        h('p', { 'class': 'muted' }, [ev.page ? t('why.page', { page: String(ev.page) }) : '', pick(ev.locator)].filter(function (x) { return !!x; }).join(' · '))) : null,
      sec('why.src', spec.source, 'why-srcline'),
      kbHost);
    if (kbHost) {
      item(ids[0]).then(function (it) {
        MA.proc.pend(kbHost, false);
        MA.dom.mount(kbHost, kbPart(spec, it), ids.length > 1 ? h('p', { 'class': 'why-more' }, h('span', { i18n: 'why.related' }), ' ', ids.slice(1).map(kbButton)) : null);
      }, function (err) {
        MA.proc.pend(kbHost, false);
        MA.dom.mount(kbHost, MA.ui.errorBox(err));
      });
    }
    return el;
  }

  function close(refocus) {
    if (!cur) { return; }
    var c = cur;
    cur = null;
    c.off.forEach(function (f) { f(); });
    if (c.el.parentNode) { c.el.parentNode.removeChild(c.el); }
    if (c.anchor) {
      c.anchor.setAttribute('aria-expanded', 'false');
      if (refocus && document.body.contains(c.anchor)) { c.anchor.focus(); }
    }
  }

  function place(pop, anchor) {
    var de = document.documentElement;
    var sx = window.pageXOffset, sy = window.pageYOffset;
    var r = anchor ? anchor.getBoundingClientRect() : { left: de.clientWidth / 2 - 180, bottom: 80 };
    var maxLeft = sx + de.clientWidth - pop.offsetWidth - 8;
    pop.style.left = String(MA.geom.max(sx + 8, MA.geom.min(r.left + sx, maxLeft))) + 'px';
    pop.style.top = String(r.bottom + sy + 6) + 'px';
  }

  /** open(spec, anchor?) → {el, close} — nem modális buborék; Esc, kattintás kívül vagy a fókusz elhagyása zárja. */
  function open(spec, anchor) {
    var again = cur && anchor && cur.anchor === anchor;
    close(false);
    if (again) { return null; }
    spec = spec || {};
    var ids = kbIds(spec);
    var tid = MA.dom.uid('why-t');
    var title = [spec.code || (ids[0] || ''), pick(spec.title)].filter(function (x) { return !!x; }).join(' — ');
    var pop = h('div', { 'class': 'why-pop', role: 'dialog', 'aria-modal': 'false', 'aria-labelledby': tid, id: MA.dom.uid('why') },
      h('div', { 'class': 'why-head' },
        h('h3', { id: tid, tabindex: '-1', 'class': 'why-title' }, h('span', { 'class': 'why-q', 'aria-hidden': 'true' }, '?'), ' ', title || t('why.title')),
        h('button', { type: 'button', 'class': 'btn-icon why-x', 'aria-label': t('common.close'), onclick: function () { close(true); } }, '×')),
      panel(spec),
      h('div', { 'class': 'why-foot' },
        ids.length ? h('button', { type: 'button', 'class': 'btn btn-sm btn-ghost why-open-kb', onclick: function () { close(false); openItem(ids[0]); } }, t('why.openKb', { id: ids[0] })) : null,
        h('span', { 'class': 'muted why-note', i18n: 'why.note' })));
    document.body.appendChild(pop);
    place(pop, anchor);
    var state = { el: pop, anchor: anchor || null, off: [] };
    function onKey(ev) {
      if (ev.key === 'Escape' && !document.querySelector('.modal-backdrop')) { ev.preventDefault(); ev.stopPropagation(); close(true); }
    }
    function onDown(ev) {
      if (!pop.contains(ev.target) && !(anchor && anchor.contains(ev.target))) { close(false); }
    }
    function onFocusOut(ev) {
      var to = ev.relatedTarget;
      if (to && !pop.contains(to) && !(anchor && anchor.contains(to))) { close(false); }
    }
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onDown, true);
    pop.addEventListener('focusout', onFocusOut);
    state.off.push(function () { document.removeEventListener('keydown', onKey, true); });
    state.off.push(function () { document.removeEventListener('mousedown', onDown, true); });
    state.off.push(MA.bus.on('route', function () { close(false); }));
    if (anchor) {
      anchor.setAttribute('aria-expanded', 'true');
      anchor.setAttribute('aria-controls', pop.id);
    }
    cur = state;
    MA.dom.$('#' + tid).focus();
    return { el: pop, close: function () { if (cur === state) { close(false); } } };
  }

  function button(spec, opts) {
    opts = opts || {};
    var b = h('button', { type: 'button', 'class': ['btn', 'btn-sm', 'btn-ghost', 'why-btn', opts.compact && 'is-compact'], id: opts.id || null,
      'aria-haspopup': 'dialog', 'aria-expanded': 'false',
      'aria-label': opts.compact ? t('why.aria', { what: spec.code || MA.proc.refs(spec.kb)[0] || '' }) : null,
      onclick: function () { open(spec, b); } },
    h('span', { 'class': 'why-q', 'aria-hidden': 'true' }, '?'), opts.compact ? null : ' ' + (opts.label || t('why.button')));
    return b;
  }

  function openItem(id) {
    var body = h('div', { 'class': 'kb-item', 'aria-busy': 'true' }, MA.ui.spinner());
    var m = MA.ui.modal({ title: t('why.itemTitle', { id: id }), body: body, size: 'lg', actions: [{ label: t('common.close'), kind: 'primary' }] });
    item(id).then(function (it) {
      MA.proc.pend(body, false);
      if (!it) { MA.dom.mount(body, MA.ui.emptyState('why.noKb')); return; }
      var dl = h('dl', { 'class': 'kb-fields' });
      FIELDS.forEach(function (f) {
        var v = it[f];
        if (v === undefined || v === null || v === '') { return; }
        dl.appendChild(h('dt', null, t('why.f.' + f)));
        dl.appendChild(h('dd', { 'class': f === 'text' || f === 'body' ? 'kb-long' : null }, f === 'strength' && STRENGTH[v] ? t('why.strength.' + v) : pick(v)));
      });
      MA.dom.mount(body,
        h('p', { 'class': 'kb-meta' }, MA.ui.badge('neutral', it.table || 'KB'), ' ',
          it.local_only ? MA.ui.badge('warning', pick(it.note) || t('kb.localOnly'), { title: t('kb.localOnlyTitle') }) : null),
        dl);
    }, function (err) {
      MA.proc.pend(body, false);
      MA.dom.mount(body, MA.ui.errorBox(err));
    });
    return m;
  }

  function kbButton(id) {
    return h('button', { type: 'button', 'class': 'kb-ref', dataset: { kb: id }, title: t('why.openKb', { id: id }), onclick: function () { openItem(id); } },
      h('span', { 'aria-hidden': 'true' }, 'ⓚ'), id);
  }

  MA.why = { button: button, open: open, panel: panel, item: item, openItem: openItem, kbButton: kbButton, strength: strengthBadge, close: function () { close(false); } };

  MA.selftest.register('why: a magyarázat szövegként épül; KB-hivatkozások bontása', function (tt) {
    var el = panel({ detail: '<img src=x onerror=alert(1)>', advice: 'a & b', plain: { asks: '<b>?</b>' } });
    tt.ok(!el.querySelector('img') && !el.querySelector('b'), 'nincs HTML-elem a szövegből');
    tt.ok(el.textContent.indexOf('<img src=x onerror=alert(1)>') >= 0 && el.textContent.indexOf('a & b') >= 0, 'szövegként jelenik meg');
    tt.eq(MA.proc.refs('V011 D-S07-004,P007;X001').join('|'), 'V011|D-S07-004|P007|X001', 'refs(szöveg)');
    tt.eq(MA.proc.refs(['P007', null, 'X005']).join('|'), 'P007|X005', 'refs(lista)');
    tt.eq(button({ code: 'P007' }, { compact: true }).getAttribute('aria-haspopup'), 'dialog', 'a gomb aria-haspopup=dialog');
  });
})();
