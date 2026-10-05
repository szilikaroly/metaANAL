/* app.js — keret, hash-router, navigáció (terv 3.5.0), képernyő-regiszter, téma, nyelv.
 *
 * Képernyő bekötése (screens/<név>.js):
 *   MA.app.registerScreen({
 *     id: 'extraction', title_key: 'screen.extraction.title', workspace: 'extraction',
 *     tab: 'extraction', order: 10,
 *     render: function (root, ctx) { … },          // root: üres <div>; Promise-t is adhat
 *     onLeave: function (ctx) { return true; }      // false / Promise<false> → a navigáció elmarad
 *   });
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  // ---------------------------------------------------------------- munkaterületek és fülek (3.5.0, 1.1)
  var WORKSPACES = [
    { id: 'extraction', label_key: 'ws.extraction', home: 'extraction' },
    { id: 'analysis', label_key: 'ws.analysis', home: 'analysis' },
    { id: 'appraisal', label_key: 'ws.appraisal', home: 'appraisal' },
    { id: 'process', label_key: 'ws.process', home: 'overview' }
  ];
  var TABS = [
    { id: 'overview', num: '', label_key: 'tab.overview', workspace: 'process', ref: '3.5.1' },
    { id: 'protocol', num: '1', label_key: 'tab.protocol', workspace: 'process', ref: '3.5.6' },
    { id: 'prisma', num: '2', label_key: 'tab.prisma', workspace: 'process', ref: '3.5.14' },
    { id: 'extraction', num: '3', label_key: 'tab.extraction', workspace: 'extraction', ref: '3.5.3–3.5.5' },
    { id: 'analysis', num: '4', label_key: 'tab.analysis', workspace: 'analysis', ref: '3.5.6–3.5.9' },
    { id: 'appraisal', num: '5', label_key: 'tab.appraisal', workspace: 'appraisal', ref: '3.5.10–3.5.11' },
    { id: 'grade', num: '6', label_key: 'tab.grade', workspace: 'appraisal', ref: '3.5.12–3.5.13' },
    { id: 'log', num: '7', label_key: 'tab.log', workspace: 'process', ref: '3.5.15' },
    { id: 'export', num: '8', label_key: 'tab.export', workspace: 'process', ref: '3.5.17' }
  ];
  // fül nélküli, de útvonallal elérhető helyőrzők (a fejlécből nyílnak); képernyő-modul felülírhatja
  var EXTRA_PLACEHOLDERS = [
    { id: 'project', title_key: 'screen.project.title', workspace: 'process', ref: '3.5.2' },
    { id: 'capabilities', title_key: 'screen.capabilities.title', workspace: 'process', ref: '3.5.16' }
  ];
  var DEFAULT_SCREEN = 'overview';
  var ID_RE = /^[a-z][a-z0-9-]{0,47}$/;

  var screens = {};           // id → screen-leíró
  var started = false;
  var current = null;         // {screen, ctx, ctrl, hash, cleanups}
  var els = {};               // keret-elemek
  var routeCount = 0;

  function tabById(id) {
    for (var i = 0; i < TABS.length; i++) { if (TABS[i].id === id) { return TABS[i]; } }
    return null;
  }
  function wsById(id) {
    for (var i = 0; i < WORKSPACES.length; i++) { if (WORKSPACES[i].id === id) { return WORKSPACES[i]; } }
    return null;
  }

  // ---------------------------------------------------------------- regiszter
  /**
   * registerScreen(def) — def: {id, title_key, workspace, tab?, order?, render(root, ctx), onLeave?(ctx),
   *   onLangChange?(ctx), hidden?}. tab alapértéke: az id, ha az egy fül azonosítója; különben null
   *   (útvonallal elérhető, a fősávban nem jelenik meg). Ugyanazon fül több képernyője al-navigációt kap.
   */
  function registerScreen(def) {
    if (!def || !ID_RE.test(def.id || '')) { throw new Error('registerScreen: érvénytelen id'); }
    if (screens[def.id] && !screens[def.id].placeholder) { throw new Error('registerScreen: már regisztrált: ' + def.id); }
    if (!wsById(def.workspace)) { throw new Error('registerScreen: ismeretlen munkaterület: ' + def.workspace); }
    if (typeof def.render !== 'function') { throw new Error('registerScreen: render(root, ctx) kötelező'); }
    if (typeof def.title_key !== 'string') { throw new Error('registerScreen: title_key kötelező'); }
    var tab = def.tab === undefined ? (tabById(def.id) ? def.id : null) : def.tab;
    if (tab !== null && !tabById(tab)) { throw new Error('registerScreen: ismeretlen fül: ' + tab); }
    screens[def.id] = {
      id: def.id,
      title_key: def.title_key,
      workspace: def.workspace,
      tab: tab,
      order: typeof def.order === 'number' ? def.order : 100,
      render: def.render,
      onLeave: def.onLeave || null,
      onLangChange: def.onLangChange || null,
      hidden: !!def.hidden,
      placeholder: !!def.placeholder,
      ref: def.ref || null
    };
    if (started) { renderNav(); }
    return screens[def.id];
  }

  function screensOfTab(tabId) {
    return Object.keys(screens).map(function (k) { return screens[k]; })
      .filter(function (s) { return s.tab === tabId && !s.hidden; })
      .sort(function (a, b) { return a.order - b.order || (a.id < b.id ? -1 : 1); });
  }

  function placeholderRender(ref) {
    return function (root) {
      root.appendChild(h('div', { 'class': 'panel placeholder' },
        h('p', { i18n: 'placeholder.body' }),
        h('p', { 'class': 'muted', i18n: 'placeholder.ref', i18nArgs: { ref: ref } })));
    };
  }

  function registerPlaceholders() {
    TABS.forEach(function (tb) {
      if (!screens[tb.id]) {
        screens[tb.id] = { id: tb.id, title_key: tb.label_key, workspace: tb.workspace, tab: tb.id, order: 1000,
          render: placeholderRender(tb.ref), onLeave: null, onLangChange: null, hidden: false, placeholder: true, ref: tb.ref };
      }
    });
    EXTRA_PLACEHOLDERS.forEach(function (p) {
      if (!screens[p.id]) {
        screens[p.id] = { id: p.id, title_key: p.title_key, workspace: p.workspace, tab: null, order: 1000,
          render: placeholderRender(p.ref), onLeave: null, onLangChange: null, hidden: false, placeholder: true, ref: p.ref };
      }
    });
  }

  /** A fül alapképernyője: ha van valódi (nem helyőrző) képernyő a fülön, a legkisebb order-ű. */
  function defaultScreenOfTab(tabId) {
    var list = screensOfTab(tabId);
    var real = list.filter(function (s) { return !s.placeholder; });
    return (real[0] || list[0] || screens[tabId] || null);
  }

  // ---------------------------------------------------------------- útvonal
  function parseHash(hash) {
    var hs = hash === undefined ? window.location.hash : hash;
    if (!hs || hs.indexOf('#/') !== 0) { return { id: null, params: {} }; }
    var rest = hs.slice(2);
    var q = rest.indexOf('?');
    var id = q < 0 ? rest : rest.slice(0, q);
    var params = {};
    if (q >= 0) { new URLSearchParams(rest.slice(q + 1)).forEach(function (v, k) { params[k] = v; }); }
    try { id = decodeURIComponent(id); } catch (e) { id = ''; }
    return { id: id, params: params };
  }

  /** href(id, params?) → '#/id?k=v' (linkekhez). */
  function href(id, params) {
    var s = '#/' + encodeURIComponent(id);
    if (params) {
      var parts = Object.keys(params).filter(function (k) { return params[k] !== undefined && params[k] !== null; })
        .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(String(params[k])); });
      if (parts.length) { s += '?' + parts.join('&'); }
    }
    return s;
  }

  /** navigate(id, params?, {replace?}) — új előzmény-bejegyzéssel (vagy cserével). */
  function navigate(id, params, opts) {
    var target = href(id, params);
    if (opts && opts.replace) {
      window.history.replaceState(null, '', target);
      route();
    } else if (window.location.hash === target) {
      route(true);
    } else {
      window.location.hash = target;
    }
  }

  function resolve(parsed) {
    var id = parsed.id;
    if (id && screens[id]) {
      var s = screens[id];
      // fül-azonosító helyőrzővel, de van valódi képernyő a fülön → az
      if (s.placeholder && s.tab) { return defaultScreenOfTab(s.tab); }
      return s;
    }
    if (id && tabById(id)) { return defaultScreenOfTab(id); }
    return null;
  }

  function leaveCurrent() {
    if (!current) { return; }
    try { current.ctrl.abort(); } catch (e) { /* — */ }
    current.cleanups.forEach(function (fn) { try { fn(); } catch (e) { window.console.error(e); } });
    current = null;
  }

  function route(force) {
    if (!started) { return; }
    var parsed = parseHash();
    var screen = resolve(parsed);
    if (!screen) {
      var last = MA.prefs.get('lastView');
      var fallback = last && screens[last] ? last : DEFAULT_SCREEN;
      window.history.replaceState(null, '', href(fallback));
      parsed = parseHash();
      screen = resolve(parsed);
    }
    var hash = window.location.hash;
    if (!force && current && current.hash === hash) { return; }
    var leaving = current && current.screen.onLeave && current.screen !== screen
      ? Promise.resolve().then(function () { return current.screen.onLeave(current.ctx, { reason: 'navigate' }); })
      : Promise.resolve(true);
    leaving.then(function (ok) {
      if (ok === false && current) {
        window.history.replaceState(null, '', current.hash);
        return;
      }
      show(screen, parsed.params, hash);
    }, function (e) { window.console.error(e); });
  }

  function makeCtx(screen, params, root, ctrl, state) {
    return {
      id: screen.id,
      params: params,
      root: root,
      signal: ctrl.signal,
      alive: function () { return !ctrl.signal.aborted; },
      onCleanup: function (fn) { state.cleanups.push(fn); },
      setTitle: function (suffix) {
        els.titleSuffix.textContent = suffix ? ' · ' + suffix : '';
      },
      setParams: function (p) {
        var hs = href(screen.id, p);
        state.hash = hs;
        state.ctx.params = p;
        window.history.replaceState(null, '', hs);
      },
      navigate: navigate,
      href: href,
      rerender: function () { route(true); },
      store: MA.store,
      api: MA.api,
      i18n: MA.i18n,
      dom: MA.dom,
      ui: MA.ui,
      geom: MA.geom
    };
  }

  function show(screen, params, hash) {
    var prevId = current ? current.screen.id : null;
    leaveCurrent();
    routeCount += 1;
    var ctrl = new AbortController();
    var state = { screen: screen, ctrl: ctrl, hash: hash, cleanups: [], ctx: null };
    var root = h('div', { 'class': 'screen-root', id: 'screen-root', dataset: { screen: screen.id } });
    state.ctx = makeCtx(screen, params, root, ctrl, state);
    current = state;
    MA.store.set('ui.route', { id: screen.id, tab: screen.tab, params: params });
    MA.prefs.set('lastView', screen.id);
    renderChrome();
    MA.dom.mount(els.screenHost, root);
    els.main.setAttribute('aria-busy', 'true');
    var finish = function () { if (current === state) { els.main.removeAttribute('aria-busy'); } };
    try {
      Promise.resolve(screen.render(root, state.ctx)).then(finish, function (e) {
        finish();
        // elhagyott képernyő megszakított kérése nem hiba
        if (ctrl.signal.aborted || (e && e.code === 'ABORTED')) { return; }
        renderError(root, e);
      });
    } catch (e) {
      finish();
      renderError(root, e);
    }
    // képernyőváltáskor a fókusz az új címre kerül (képernyőolvasó); újrarajzoláskor (nyelv, ⟳) marad
    if (routeCount > 1 && prevId !== screen.id) { els.title.focus({ preventScroll: false }); }
    MA.bus.emit('route', { id: screen.id, tab: screen.tab, params: params });
  }

  function renderError(root, e) {
    if (!(e && e.isApiError)) { window.console.error(e); }   // programhiba; az API-hibát a toast már jelezte
    // a képernyő „Betöltés …” jelzője ne maradjon a hibadoboz mellett
    Array.prototype.slice.call(root.children).forEach(function (c) { if (c.classList.contains('spinner')) { root.removeChild(c); } });
    root.appendChild(h('div', { 'class': 'panel' }, MA.ui.errorBox(e && e.isApiError ? e : { code: 'INTERNAL', message: e && e.message ? e.message : String(e) })));
  }

  // ---------------------------------------------------------------- téma
  var mql = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function resolvedTheme(pref) {
    if (pref === 'light' || pref === 'dark') { return pref; }
    return mql && mql.matches ? 'dark' : 'light';
  }

  /** setTheme('light' | 'dark' | 'auto') — data-theme a <html>-en; preferencia: mag.pref.theme. */
  function setTheme(pref) {
    var p = pref === 'light' || pref === 'dark' ? pref : 'auto';
    MA.prefs.set('theme', p === 'auto' ? null : p);
    applyTheme();
  }

  function applyTheme() {
    var pref = MA.prefs.get('theme', 'auto');
    var th = resolvedTheme(pref);
    document.documentElement.setAttribute('data-theme', th);
    MA.store.set('ui.theme', { pref: pref, theme: th });
    if (els.themeBtn) {
      els.themeBtn.setAttribute('aria-pressed', th === 'dark' ? 'true' : 'false');
      els.themeBtn.lastChild.textContent = t(th === 'dark' ? 'theme.dark' : 'theme.light');
    }
    MA.bus.emit('theme', th);
  }

  function toggleTheme() {
    setTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
  }

  // ---------------------------------------------------------------- keret (fejléc, navigáció)
  function buildShell() {
    var app = document.getElementById('app');
    MA.dom.clear(app);
    els.skip = h('button', { type: 'button', 'class': 'skip-link', i18n: 'app.skip', onclick: function () { els.main.focus(); } });
    els.brandProject = h('span', { 'class': 'brand-project' });
    els.engine = h('div', { 'class': 'engine-status', 'aria-live': 'polite' });
    els.langHu = h('button', { type: 'button', 'class': 'seg-btn', lang: 'hu', dataset: { lang: 'hu' }, onclick: function () { MA.i18n.setLang('hu'); } }, 'HU');
    els.langEn = h('button', { type: 'button', 'class': 'seg-btn', lang: 'en', dataset: { lang: 'en' }, onclick: function () { MA.i18n.setLang('en'); } }, 'EN');
    els.langGroup = h('div', { 'class': 'seg', role: 'group', id: 'lang-switch', i18nAttrs: { 'aria-label': 'lang.label' } }, els.langHu, els.langEn);
    els.themeBtn = h('button', { type: 'button', 'class': 'btn btn-ghost theme-btn', id: 'theme-toggle', 'aria-pressed': 'false', onclick: toggleTheme,
      i18nAttrs: { title: 'theme.toggle' } }, h('span', { 'aria-hidden': 'true' }, '◐ '), h('span', null, ''));
    els.userMenuHost = h('div', { 'class': 'user-menu-host' });
    els.caps = h('ul', { 'class': 'caps-list', i18nAttrs: { 'aria-label': 'caps.label' } });
    els.privacy = h('a', { 'class': 'privacy-summary', href: '#/capabilities' });
    els.extBtn = h('button', { type: 'button', 'class': 'btn btn-ghost ext-btn', id: 'external-changes', onclick: function () { refresh(); } });
    els.conn = h('span', { 'class': 'conn-status', 'aria-live': 'polite' });
    els.header = h('header', { 'class': 'app-header', role: 'banner' },
      h('div', { 'class': 'hdr-row hdr-row1' },
        h('div', { 'class': 'brand' }, h('span', { 'class': 'brand-name', i18n: 'app.name' }), els.brandProject),
        els.engine),
      h('div', { 'class': 'hdr-row hdr-row2' },
        els.langGroup, els.themeBtn, els.userMenuHost, els.caps,
        h('span', { 'class': 'hdr-spacer' }), els.privacy, els.conn, els.extBtn));
    els.navList = h('ul', { 'class': 'nav-tabs' });
    els.navLinks = null;
    els.wsLinks = {};
    els.wsBar = h('ul', { 'class': 'ws-bar', i18nAttrs: { 'aria-label': 'ws.label' } }, WORKSPACES.map(function (w) {
      els.wsLinks[w.id] = h('a', { 'class': 'ws-link', id: 'ws-' + w.id, href: href(w.home), dataset: { workspace: w.id }, onkeydown: navKeydown },
        h('span', { 'class': 'ws-swatch', 'aria-hidden': 'true' }), h('span', { 'class': 'ws-name' }));
      return h('li', null, els.wsLinks[w.id]);
    }));
    els.nav = h('nav', { 'class': 'app-nav', id: 'main-nav', i18nAttrs: { 'aria-label': 'nav.label' } }, els.wsBar, els.navList);
    els.subnav = h('nav', { 'class': 'app-subnav', hidden: true, i18nAttrs: { 'aria-label': 'nav.sublabel' } });
    els.title = h('h1', { 'class': 'screen-title', id: 'screen-title', tabindex: '-1' });
    els.titleSuffix = h('span', { 'class': 'screen-title-suffix' });
    els.wsLabel = h('span', { 'class': 'ws-label' });
    els.screenHost = h('div', { 'class': 'screen-host' });
    els.main = h('main', { 'class': 'app-main', id: 'main', tabindex: '-1', 'aria-labelledby': 'screen-title' },
      h('div', { 'class': 'screen-head' }, els.wsLabel, h('div', { 'class': 'screen-title-row' }, els.title)),
      els.screenHost);
    els.footer = h('footer', { 'class': 'app-footer' }, h('span', { i18n: 'legend.symbols' }), ' · ',
      h('button', { type: 'button', 'class': 'btn-link', i18n: 'keys.open', onclick: showKeyHelp }));
    MA.dom.mount(app, els.skip, els.header, els.nav, els.subnav, els.main, els.footer);
    renderUserMenu();
  }

  function renderUserMenu() {
    var initials = MA.store.get('project.user.initials') || MA.store.get('session.user') || null;
    MA.dom.mount(els.userMenuHost, MA.ui.menu({
      label: (initials ? String(initials) : t('menu.label')) + ' ▾',
      buttonAttrs: { id: 'user-menu-button' },
      items: [
        { label: t('menu.project'), href: href('project') },
        { label: t('menu.capabilities'), href: href('capabilities') },
        { label: t('menu.themeAuto'), onSelect: function () { setTheme('auto'); } },
        { label: t('menu.keys'), onSelect: showKeyHelp },
        { label: t('menu.selftest'), onSelect: function () { MA.selftest.run({ show: true }); } }
      ]
    }));
  }

  function badgeNodes(list) {
    return (list || []).map(function (b) { return MA.ui.badge(b.kind || 'neutral', b.text, { title: b.title || null }); });
  }

  function tabBadges(tabId) {
    var screenSet = MA.store.get('badges.screen.' + tabId);
    if (Array.isArray(screenSet)) { return screenSet; }
    var server = MA.store.get('project.badges');
    return server && Array.isArray(server[tabId]) ? server[tabId] : [];
  }

  function buildNav() {
    els.navLinks = {};
    MA.dom.mount(els.navList, TABS.map(function (tb) {
      var a = h('a', { 'class': 'nav-tab', href: href(tb.id), id: 'nav-' + tb.id, dataset: { tab: tb.id, workspace: tb.workspace }, onkeydown: navKeydown },
        h('span', { 'class': 'nav-tab-label' }, tb.num ? h('span', { 'class': 'nav-num' }, tb.num + ' ') : null, h('span', { 'class': 'nav-text' })),
        h('span', { 'class': 'nav-badges' }));
      els.navLinks[tb.id] = a;
      return h('li', { 'class': 'nav-item' }, a);
    }));
  }

  /** A fősáv helyben frissül (a linkek megmaradnak → a billentyűzetfókusz nem vész el). */
  function renderNav() {
    if (!els.navList) { return; }
    if (!els.navLinks) { buildNav(); }
    var activeTab = current ? current.screen.tab : null;
    var activeWs = current ? current.screen.workspace : null;
    WORKSPACES.forEach(function (w) {
      var a = els.wsLinks[w.id];
      a.lastChild.textContent = t(w.label_key);
      a.classList.toggle('is-active', w.id === activeWs);
      if (w.id === activeWs) { a.setAttribute('aria-current', 'true'); } else { a.removeAttribute('aria-current'); }
    });
    TABS.forEach(function (tb) {
      var a = els.navLinks[tb.id];
      var isActive = tb.id === activeTab;
      a.classList.toggle('is-active', isActive);
      a.classList.toggle('in-workspace', tb.workspace === activeWs);
      if (isActive) { a.setAttribute('aria-current', 'page'); } else { a.removeAttribute('aria-current'); }
      a.title = t('ws.prefix') + ' ' + t(wsById(tb.workspace).label_key) + ' · Alt+' + (tb.num || '0');
      a.querySelector('.nav-text').textContent = t(tb.label_key);
      MA.dom.mount(a.querySelector('.nav-badges'), badgeNodes(tabBadges(tb.id)));
    });
    // al-navigáció: a fül több képernyője
    var subs = activeTab ? screensOfTab(activeTab).filter(function (s) { return !s.placeholder; }) : [];
    if (subs.length > 1) {
      els.subnav.hidden = false;
      MA.dom.mount(els.subnav, h('ul', { 'class': 'subnav-list' }, subs.map(function (s) {
        var on = current && current.screen.id === s.id;
        return h('li', null, h('a', { 'class': ['subnav-link', on && 'is-active'], href: href(s.id), 'aria-current': on ? 'page' : null, onkeydown: navKeydown }, t(s.title_key)));
      })));
    } else {
      els.subnav.hidden = true;
      MA.dom.clear(els.subnav);
    }
  }

  /** ←/→/Home/End a fősáv és az al-navigáció linkjei között (a Tab is működik). */
  function navKeydown(ev) {
    var list = MA.dom.$$('a', ev.currentTarget.closest('ul'));
    var i = list.indexOf(ev.currentTarget);
    var j = null;
    if (ev.key === 'ArrowRight') { j = (i + 1) % list.length; } else if (ev.key === 'ArrowLeft') { j = (i - 1 + list.length) % list.length; } else if (ev.key === 'Home') { j = 0; } else if (ev.key === 'End') { j = list.length - 1; } else if (ev.key === ' ') { ev.preventDefault(); ev.currentTarget.click(); return; }
    if (j !== null) { ev.preventDefault(); list[j].focus(); }
  }

  function renderHeader() {
    if (!els.header) { return; }
    var lang = MA.i18n.lang();
    els.langHu.setAttribute('aria-pressed', lang === 'hu' ? 'true' : 'false');
    els.langEn.setAttribute('aria-pressed', lang === 'en' ? 'true' : 'false');
    var project = MA.store.get('project');
    els.brandProject.textContent = project && project.title ? ' · ' + MA.i18n.pick(project.title, '') : '';
    document.title = t('app.name') + (project && project.title ? ' · ' + MA.i18n.pick(project.title, '') : '');
    renderEngine();
    renderCaps();
    renderPrivacy();
    renderExternal();
    renderConn();
  }

  function renderEngine() {
    var eng = MA.store.get('engine');
    if (!eng) { MA.dom.mount(els.engine, MA.ui.badge('pending', t('engine.loading'))); return; }
    var st = eng.selftest || {};
    var state = st.ok === true ? 'ok' : (st.ok === false ? 'error' : 'pending');
    var label = { ok: 'engine.selftestOk', error: 'engine.selftestFail', pending: 'engine.selftestUnknown' }[state];
    var count = st.passed !== undefined && st.checks !== undefined ? String(st.passed) + '/' + String(st.checks) : '';
    MA.dom.mount(els.engine,
      h('span', { 'class': 'engine-version' }, t('engine.label', { version: eng.engine_version || eng.version || '—' })),
      ' ',
      MA.ui.badge(state, count || t(label), { title: t(label), srLabel: t('engine.selftest') }));
  }

  var CAP_STATE_KIND = { ok: 'cap_ok', unusable: 'cap_unusable', legacy: 'cap_legacy', absent: 'cap_absent' };

  function renderCaps() {
    var caps = MA.store.get('caps');
    var comps = caps && Array.isArray(caps.components) ? caps.components : [];
    MA.dom.mount(els.caps, comps.filter(function (c) { return c.id !== 'metaelemzes' && c.id !== 'engine'; }).map(function (c) {
      var state = CAP_STATE_KIND[c.state] ? c.state : 'absent';
      var note = '';
      if (state === 'ok') { note = (c.version || '') + (c.mode ? ' ' + c.mode : ''); } else if (state === 'legacy') { note = (c.version || '') + ' ' + t('caps.state.legacy'); } else if (state === 'unusable') { note = c.problems && c.problems.length ? MA.i18n.pick(c.problems[0], '') : t('caps.state.unusable'); }
      return h('li', { 'class': ['cap', 'is-' + state], dataset: { plugin: c.id } },
        h('a', { href: href('capabilities'), title: t('caps.state.' + state) },
          h('span', { 'class': 'cap-name' }, c.label ? MA.i18n.pick(c.label, c.id) : c.id), ' ',
          h('span', { 'class': 'cap-sym', 'aria-hidden': 'true' }, MA.ui.symbol(CAP_STATE_KIND[state])),
          h('span', { 'class': 'sr-only' }, t('caps.state.' + state)),
          note ? h('span', { 'class': 'cap-note' }, ' ' + note) : null));
    }));
  }

  /**
   * Az adatvédelmi összesítő állapota: a szerver 'status' mezője, ha van; különben a
   * privacy.status() logikai mezőiből (open_blocked, write_hold, vault.tracked, gitignore_block_present,
   * tracked_sensitive_files, history.in_history) — ez megjelenítési döntés, nem számítás.
   */
  function privacyStatus(p) {
    if (p.status === 'protected' || p.status === 'at_risk' || p.status === 'blocked') { return p.status; }
    if ((p.open_blocked && p.open_blocked.blocked) || (p.write_hold && p.write_hold.active)) { return 'blocked'; }
    var tracked = p.vault && p.vault.tracked;
    if ((tracked && p.gitignore_block_present === false) || (p.tracked_sensitive_files && p.tracked_sensitive_files.length) ||
        (p.history && p.history.in_history)) { return 'at_risk'; }
    if (p.vault || p.gitignore_block_present !== undefined) { return 'protected'; }
    return 'unknown';
  }

  function renderPrivacy() {
    var p = MA.store.get('privacy');
    if (!p) { MA.dom.mount(els.privacy, t('privacy.loading')); return; }
    var status = privacyStatus(p);
    var kind = { protected: 'ok', at_risk: 'warning', blocked: 'error', unknown: 'neutral' }[status];
    var parts = [t('privacy.class', { cls: p.data_class || '?' })];
    if (p.vault && p.vault.under_root) { parts.push(t(p.vault.tracked ? 'privacy.underVault' : 'privacy.underVaultIdle')); }
    var cloud = Array.isArray(p.cloud_sync) ? p.cloud_sync.length > 0 : !!p.cloud_sync;
    if (cloud) { parts.push(t('privacy.cloudSync')); }
    MA.dom.mount(els.privacy,
      h('span', { 'class': 'privacy-label' }, t('privacy.label') + ' '),
      h('span', null, parts.join(' · ') + ' '),
      MA.ui.badge(kind, t('privacy.status.' + status)));
  }

  function renderExternal() {
    var ch = MA.store.get('changes');
    var n = ch && ch.external_count ? ch.external_count : 0;
    els.extBtn.textContent = '';
    els.extBtn.appendChild(h('span', null, t('changes.external', { n: String(n) }) + ' '));
    els.extBtn.appendChild(h('span', { 'aria-hidden': 'true' }, '⟳'));
    els.extBtn.setAttribute('aria-label', t('changes.externalAria', { n: String(n) }));
    els.extBtn.classList.toggle('has-changes', n > 0);
  }

  function renderConn() {
    var c = MA.store.get('connection');
    MA.dom.mount(els.conn, c === 'retrying' ? MA.ui.badge('warning', t('conn.retrying')) : null);
  }

  function renderChrome() {
    renderNav();
    if (!current) { return; }
    var s = current.screen;
    els.title.textContent = t(s.title_key);
    els.title.appendChild(els.titleSuffix);
    els.titleSuffix.textContent = '';
    var ws = wsById(s.workspace);
    els.wsLabel.textContent = t('ws.prefix') + ' ' + t(ws.label_key);
    els.wsLabel.dataset.workspace = ws.id;
    els.main.dataset.workspace = ws.id;
  }

  // ---------------------------------------------------------------- munkamenet-állapot paneljei
  function sessionPanel(state) {
    var keyBase = 'session.' + state;
    var errInfo = MA.store.get('session') || {};
    // egy mondat és egy parancs (Windowson a py -3 változat); a szerver 'failed' üzenete ugyanezt mondaná — nem
    // ismételjük (UX-17)
    var win = MA.shell && MA.shell.isWindows();
    MA.dom.mount(els.screenHost, h('div', { 'class': 'panel session-panel', role: 'alert', id: 'session-panel', dataset: { state: state } },
      h('h2', { i18n: keyBase + '.title' }),
      h('p', { i18n: keyBase + '.body' }),
      errInfo.message && state !== 'failed' ? h('p', { 'class': 'muted' }, errInfo.message) : null,
      h('pre', { 'class': 'cmd', id: 'session-cmd' }, win ? 'py -3 ma.py gui --project <mappa>' : 'python ma.py gui --project <mappa>'),
      h('p', { 'class': 'muted', i18n: win ? 'session.cmdHintWin' : 'session.cmdHint' })));
    els.title.textContent = t('session.heading');
  }

  // ---------------------------------------------------------------- adatbetöltés
  var BASE = [
    { key: 'engine', path: '/api/engine' },
    { key: 'caps', path: '/api/capabilities' },
    { key: 'privacy', path: '/api/privacy' },
    { key: 'project', path: '/api/project' }
  ];

  /** loadBase() → Promise — a keret adatai (motor, képességek, adatvédelem, projekt) a store-ba. */
  function loadBase() {
    var failed = [];
    return Promise.all(BASE.map(function (b) {
      return MA.api.get(b.path, { toast: false }).then(function (env) {
        MA.store.set(b.key, env.data);
        return env;
      }, function (err) {
        failed.push(b.key + ': ' + err.code);
        if (err.code === 'FORBIDDEN') { return null; }
        MA.store.set(b.key + 'Error', { code: err.code, message: err.message });
        return null;
      });
    })).then(function () {
      if (failed.length && MA.store.get('session.state') === 'ok') {
        MA.ui.toast({ kind: 'warning', title: t('app.baseLoadFailed'), details: failed });
      }
    });
  }

  /**
   * refresh() — „⟳”: keretadatok újratöltése, a külső-számláló nullázása, majd a képernyő újrarajzolása
   * (előtte a képernyő onLeave(ctx, {reason: 'refresh'}) őre fut: false → nincs újrarajzolás).
   */
  function refresh() {
    MA.api.changes.ack();
    return loadBase().then(function () {
      MA.bus.emit('refresh', {});
      var cur = current;
      var guard = cur && cur.screen.onLeave
        ? Promise.resolve().then(function () { return cur.screen.onLeave(cur.ctx, { reason: 'refresh' }); })
        : Promise.resolve(true);
      return guard.then(function (ok) { if (ok !== false && current === cur) { route(true); } });
    });
  }

  /** setTabBadges(tabId, [{kind, text, title?}] | null) — képernyő-oldali fül-jelvények (null → a szerveré). */
  function setTabBadges(tabId, list) {
    if (!tabById(tabId)) { throw new Error('setTabBadges: ismeretlen fül'); }
    MA.store.set('badges.screen.' + tabId, list);
  }

  // ---------------------------------------------------------------- billentyűzet
  function showKeyHelp() {
    var rows = [
      ['Alt+0 … Alt+8', t('keys.tabs')],
      ['← / →', t('keys.navArrows')],
      ['Tab / Shift+Tab', t('keys.tab')],
      ['Esc', t('keys.esc')],
      ['?', t('keys.help')]
    ];
    MA.ui.modal({
      title: t('keys.title'),
      body: h('table', { 'class': 'table table-compact' },
        h('tbody', null, rows.map(function (r) { return h('tr', null, h('th', { scope: 'row' }, h('kbd', null, r[0])), h('td', null, r[1])); }))),
      actions: [{ label: t('common.close'), kind: 'primary' }]
    });
  }

  function onGlobalKey(ev) {
    if (ev.defaultPrevented) { return; }
    if (document.querySelector('.modal-backdrop')) { return; }
    if (ev.altKey && !ev.ctrlKey && !ev.metaKey && /^Digit[0-8]$/.test(ev.code || '')) {
      if (MA.dom.isTyping(ev.target)) { return; }
      var n = Number(ev.code.slice(5));
      var tb = TABS[n];
      if (tb) { ev.preventDefault(); navigate(tb.id); }
      return;
    }
    if (ev.key === '?' && !ev.ctrlKey && !ev.metaKey && !ev.altKey && !MA.dom.isTyping(ev.target)) {
      ev.preventDefault();
      showKeyHelp();
    }
  }

  // ---------------------------------------------------------------- indítás
  function onLang(lang) {
    MA.bus.emit('lang', lang);
    renderUserMenu();
    renderHeader();
    renderChrome();
    MA.i18n.apply(document.body);
    if (current) {
      if (current.screen.onLangChange) { current.screen.onLangChange(current.ctx); } else { route(true); }
    }
    applyTheme();
  }

  /**
   * start() — a keret felépítése, munkamenet, keretadatok, változásfigyelés, első útvonal, selftest.
   * Visszaad: Promise<'ok' | 'none' | 'failed'>.
   */
  function start() {
    if (started) { return Promise.resolve('ok'); }
    var lang = MA.prefs.get('lang', null);
    if (lang && MA.i18n.LANGS.indexOf(lang) >= 0) { MA.i18n.setLang(lang, { persist: false }); }
    document.documentElement.setAttribute('lang', MA.i18n.lang());
    registerPlaceholders();
    buildShell();
    applyTheme();
    if (mql && mql.addEventListener) { mql.addEventListener('change', function () { if (MA.prefs.get('theme', 'auto') === 'auto') { applyTheme(); } }); }
    MA.i18n.onChange(onLang);
    ['engine', 'caps', 'privacy', 'project', 'changes', 'connection'].forEach(function (k) {
      MA.store.subscribe(k, function () { renderHeader(); if (k === 'project') { renderNav(); renderUserMenu(); } });
    });
    MA.store.subscribe('badges', renderNav);
    // a szerver nem tudja követni a kliens rev-jét (pl. régi rev) → teljes újratöltés
    MA.bus.on('changes', function (c) {
      if (c && c.reset) {
        MA.ui.toast({ kind: 'info', title: t('changes.reset'), actions: [{ label: t('changes.refresh'), onClick: refresh }] });
      }
    });
    MA.store.subscribe('session', function (s) {
      if (s && (s.state === 'lost' || s.state === 'none' || s.state === 'failed')) { leaveCurrent(); sessionPanel(s.state); }
    });
    document.addEventListener('keydown', onGlobalKey);
    renderHeader();
    MA.dom.mount(els.screenHost, MA.ui.spinner('session.connecting'));
    return MA.api.session.init().then(function (state) {
      if (state !== 'ok') { sessionPanel(state); return state; }
      started = true;
      window.addEventListener('hashchange', function () { route(); });
      return loadBase().then(function () {
        var rev = MA.api.lastMeta().project_rev;
        MA.api.changes.start(typeof rev === 'number' ? rev : undefined);
        route(true);
        MA.store.set('ui.ready', true);
        document.documentElement.setAttribute('data-ready', '1');
        if (new URLSearchParams(window.location.search).get('selftest') === '1') {
          setTimeout(function () { MA.selftest.run({ show: true }); }, 0);
        }
        return 'ok';
      });
    });
  }

  MA.app = {
    WORKSPACES: WORKSPACES,
    TABS: TABS,
    registerScreen: registerScreen,
    screens: function () { return Object.keys(screens).map(function (k) { return screens[k]; }); },
    navigate: navigate,
    href: href,
    parseHash: parseHash,
    current: function () { return current ? { id: current.screen.id, tab: current.screen.tab, params: current.ctx.params } : null; },
    /** isPlaceholder(id) — a képernyő még csak helyőrző (az MVP-ben nincs mögötte funkció; oda nem linkelünk). */
    isPlaceholder: function (id) { return !screens[id] || !!screens[id].placeholder; },
    refresh: refresh,
    loadBase: loadBase,
    setTabBadges: setTabBadges,
    setTheme: setTheme,
    toggleTheme: toggleTheme,
    start: start
  };
})();
