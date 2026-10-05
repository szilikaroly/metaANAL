/* screens/plan.js — 4 Elemzés · Elemzési terv, protokoll-eltérés, parancs-előnézet (terv 3.5.6, 2.6, 4.4, 4.5).
 *
 * - Az űrlap mezői, választható értékei, alapértékei és súgói a motor opció-metaadatából (GET /api/engine
 *   → data.options: {név: {default, type, choices, cli, help}}) generálódnak; a JS-ben nincs opciólista.
 * - A spec (szk.ma.analysis-spec/v1) GET/PUT /api/specs/<név> (ETag/If-Match); a szerkesztett piszkozat
 *   csak memóriában él (MA.store 'analysis.plan.*').
 * - Protokoll-eltérés sáv: a piszkozat ↔ a mentett (előre rögzített) elsődleges spec különbségei (X016).
 * - Parancs-előnézet: a futás-leíró equivalent_argv-ja (és expanded_argv-ja) szó szerint.
 * - Explore: automatikusan csak k ≤ 40 esetén (debounce, client_seq, „legutolsó nyer”); „Rögzítés”:
 *   PUT spec → POST /api/analyze {mode: 'commit'} → GET /api/jobs/<id>.
 * - Érzékenységi gyermekek egy kattintással (purpose: sensitivity, parent): becsült nélkül, magas RoB
 *   nélkül, fix hatás, DL τ², kiugrók nélkül — csak ha a motor metaadata az opciót/értéket ismeri.
 * A felület nem számol: minden számszöveg a motor display_text / *_text mezője.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var A = MA.analysis;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '—' : fb); };

  var AUTO_MAX_K = 40;
  var DEBOUNCE_MS = 350;
  var NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
  var FILTER_RE = /^[^=]+=.*$/;
  var NUM_RE = /^\s*-?\d+(?:[.,]\d+)?\s*$/;
  var SPEC_SCHEMA = 'szk.ma.analysis-spec/v1';

  // Gyermek-futások (2.6; D-S12-002/003/005/009). Ha a motor ad 'sensitivity_presets'-et, az nyer.
  var CHILD_PRESETS = [
    { id: 'no_estim', suffix: '_no_estim', label_key: 'plan.child.noEstim', kb: 'D-S12-003', exclude: 'estimated=igen' },
    { id: 'no_rob_high', suffix: '_no_rob_high', label_key: 'plan.child.noRobHigh', kb: 'D-S12-002', exclude: 'rob=high' },
    { id: 'fixed', suffix: '_fixed', label_key: 'plan.child.fixed', kb: 'D-S12-005', options: { model: 'fixed' } },
    { id: 'dl', suffix: '_dl', label_key: 'plan.child.dl', kb: 'D-S12-005', options: { tau2: 'DL' } },
    { id: 'no_outliers', suffix: '_no_outliers', label_key: 'plan.child.noOutliers', kb: 'D-S12-009', options: { outliers: true } }
  ];

  function clone(o) { return o === undefined ? undefined : JSON.parse(JSON.stringify(o)); }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  // ---------------------------------------------------------------- spec-segédek
  function newSpec(outcome, name, meta) {
    var opts = {};
    Object.keys(meta).forEach(function (k) { opts[k] = meta[k]['default'] === undefined ? null : clone(meta[k]['default']); });
    if (outcome.measure && Object.prototype.hasOwnProperty.call(opts, 'measure')) { opts.measure = outcome.measure; }
    return { schema: SPEC_SCHEMA, name: name, outcome: outcome.id, purpose: 'primary', prespecified: false, protocol_ref: null, parent: null,
      data: { path: outcome.data || ('03_adatok/' + outcome.id + '.csv') }, options: opts, filters: { include: [], exclude: [] }, kb_refs: [] };
  }

  /** A hiányzó (a motor által ismert) opciókulcsokat alapértékkel tölti — a spec kulcsai = DEFAULTS. */
  function normalize(spec, meta) {
    var s = clone(spec);
    s.options = s.options || {};
    Object.keys(meta).forEach(function (k) {
      if (!Object.prototype.hasOwnProperty.call(s.options, k)) { s.options[k] = meta[k]['default'] === undefined ? null : clone(meta[k]['default']); }
    });
    s.filters = s.filters || {};
    s.filters.include = Array.isArray(s.filters.include) ? s.filters.include : [];
    s.filters.exclude = Array.isArray(s.filters.exclude) ? s.filters.exclude : [];
    if (s.data) { delete s.data.sha256; }
    return s;
  }

  function optLabel(name, meta) {
    return MA.i18n.has('plan.opt.' + name) ? t('plan.opt.' + name) : (meta && meta.cli ? meta.cli : name);
  }

  function valueText(v) {
    if (v === null || v === undefined) { return t('plan.value.default'); }
    if (v === true) { return t('plan.value.yes'); }
    if (v === false) { return t('plan.value.no'); }
    if (Array.isArray(v)) { return v.length ? v.join(', ') : t('plan.value.empty'); }
    if (typeof v === 'object') { return JSON.stringify(v); }
    return String(v);
  }

  /** A két spec eltérései: [{key, label, before, after}] (opciók, szűrők, cél, előre rögzítés). */
  function specDiffs(saved, draft, meta) {
    if (!saved) { return []; }
    var out = [];
    var keys = Object.keys(Object.assign({}, saved.options || {}, draft.options || {}));
    keys.forEach(function (k) {
      var a = (saved.options || {})[k];
      var b = (draft.options || {})[k];
      if (!same(a === undefined ? null : a, b === undefined ? null : b)) {
        out.push({ key: k, label: optLabel(k, meta[k]), before: valueText(a), after: valueText(b) });
      }
    });
    ['include', 'exclude'].forEach(function (f) {
      var a = (saved.filters || {})[f] || [];
      var b = (draft.filters || {})[f] || [];
      if (!same(a, b)) { out.push({ key: 'filters.' + f, label: t('plan.filters.' + f), before: valueText(a), after: valueText(b) }); }
    });
    return out;
  }

  function childName(parent, suffix) {
    var base = parent.slice(0, 64 - suffix.length);
    return base + suffix;
  }

  function presetsFor(meta) {
    var eng = MA.store.get('engine') || {};
    var list = Array.isArray(eng.sensitivity_presets) && eng.sensitivity_presets.length ? eng.sensitivity_presets : CHILD_PRESETS;
    return list.map(function (p) {
      var okay = true;
      Object.keys(p.options || {}).forEach(function (k) {
        var m = meta[k];
        var v = p.options[k];
        if (!m) { okay = false; return; }
        if (m.type === 'enum' && Array.isArray(m.choices) && m.choices.indexOf(v) < 0) { okay = false; }
        if (m.type === 'bool' && typeof v !== 'boolean') { okay = false; }
      });
      return Object.assign({}, p, { available: okay });
    });
  }

  function parseNum(text) {
    var s = String(text);
    if (s.trim() === '') { return { ok: true, value: null }; }
    if (!NUM_RE.test(s)) { return { ok: false }; }
    return { ok: true, value: Number(s.trim().replace(',', '.')) };
  }

  function splitList(text) {
    return String(text).split(/[,;\n]/).map(function (x) { return x.trim(); }).filter(function (x) { return x.length > 0; });
  }

  // ---------------------------------------------------------------- képernyő
  function render(root, ctx) {
    var outcome = A.pickOutcome(ctx.params.outcome);
    if (!outcome) {
      root.appendChild(h('div', { 'class': 'panel' }, MA.ui.emptyState('analysis.noOutcomes')));
      return null;
    }
    var meta = A.engineOptions();
    var specName = ctx.params.spec && NAME_RE.test(ctx.params.spec) ? ctx.params.spec : (outcome.primary_spec || outcome.id + '_primary');
    ctx.setTitle(pick(outcome.name, outcome.id) + ' · ' + specName);
    var stKey = 'analysis.plan.' + outcome.id + '.' + specName;
    var st = MA.store.get(stKey);
    if (!st) { st = { draft: null, saved: null, etag: null, lastRun: null, invalid: {}, query: '' }; MA.store.set(stKey, st); }
    var loading = h('div', { 'class': 'panel', 'aria-busy': 'true' }, MA.ui.spinner('plan.loading'));
    root.appendChild(loading);
    if (!Object.keys(meta).length) {
      MA.dom.mount(loading, MA.ui.emptyState('plan.noEngineMeta'));
      loading.removeAttribute('aria-busy');
      return null;
    }

    var specP = MA.api.get('/api/specs/' + encodeURIComponent(specName), { signal: ctx.signal, quiet: ['NOT_FOUND'] }).then(function (env) {
      return { spec: env.data, etag: env.etag };
    }, function (err) {
      if (err.code === 'NOT_FOUND') { return { spec: null, etag: null }; }
      throw err;
    });
    var runsP = A.loadRuns(outcome.id, ctx.signal).catch(function (err) {
      if (err.code === 'ABORTED') { throw err; }
      return [];
    });
    return Promise.all([specP, runsP]).then(function (res) {
      if (!ctx.alive()) { return; }
      var server = res[0];
      st.runs = res[1];
      st.saved = server.spec ? normalize(server.spec, meta) : null;
      st.etag = server.etag;
      if (!st.draft) { st.draft = st.saved ? clone(st.saved) : newSpec(outcome, specName, meta); }
      root.removeChild(loading);
      build(root, ctx, outcome, specName, st, meta);
    });
  }

  function build(root, ctx, outcome, specName, st, meta) {
    var els = {};
    var runLatest = MA.api.latest();
    var autoPref = function () { return MA.prefs.get('analysis.autoExplore', '1') !== '0'; };

    function currentSpec() { return clone(st.draft); }
    function hasInvalid() { return Object.keys(st.invalid).length > 0; }
    function specRuns() {
      return (st.runs || []).filter(function (r) { return r.spec && r.spec.name === specName && r.run_id; });
    }
    function knownK() {
      if (st.lastRun && st.lastRun.run && typeof st.lastRun.run.k === 'number') { return st.lastRun.run.k; }
      var r = specRuns()[0];
      return r && typeof r.k === 'number' ? r.k : null;
    }
    function autoAllowed() {
      var k = knownK();
      return autoPref() && (k === null || k <= AUTO_MAX_K);
    }
    function fresh() { return !!(st.lastRun && st.lastRun.specJson === JSON.stringify(st.draft)); }

    // ------------------------------------------------ fejléc
    var outcomeSel = h('select', { id: 'plan-outcome', onchange: function () { ctx.navigate('analysis', { outcome: outcomeSel.value }); } },
      A.outcomes().map(function (o) { return h('option', { value: o.id }, o.id + ' — ' + pick(o.name, o.id)); }));
    outcomeSel.value = outcome.id;
    var primaryName = outcome.primary_spec || outcome.id + '_primary';
    var names = [primaryName];
    (st.runs || []).forEach(function (r) {
      var n = r.spec && r.spec.name;
      if (n && names.indexOf(n) < 0 && (n === primaryName || r.spec.parent === primaryName)) { names.push(n); }
    });
    if (names.indexOf(specName) < 0) { names.push(specName); }
    var specSel = h('select', { id: 'plan-spec', onchange: function () { ctx.navigate('analysis', { outcome: outcome.id, spec: specSel.value }); } },
      names.map(function (n) { return h('option', { value: n }, n === primaryName ? n : '└ ' + n); }));
    specSel.value = specName;
    els.specMeta = h('span', { 'class': 'plan-spec-meta' });
    var head = h('section', { 'class': 'panel plan-head', 'aria-label': t('plan.head.aria') },
      h('div', { 'class': 'toolbar' },
        h('label', { 'for': 'plan-outcome' }, t('plan.outcome')), outcomeSel,
        h('label', { 'for': 'plan-spec' }, t('plan.spec')), specSel,
        els.specMeta,
        h('span', { 'class': 'hdr-spacer' }),
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'plan-json', onclick: showJson }, t('plan.json')),
        h('button', { type: 'button', 'class': 'btn btn-sm btn-ghost', id: 'plan-reset', onclick: resetDraft }, t('plan.reset'))),
      h('p', { 'class': 'plan-kb' }, h('span', { 'class': 'muted' }, t('plan.kb') + ' '),
        h('span', { 'class': 'kb-list', id: 'plan-kb-refs' }, (st.draft.kb_refs || []).map(function (id) { return A.kbButton(id); })),
        ' ', A.kbRow(h('span', { 'class': 'muted' }, '·'), 'spec', ctx.signal)));
    root.appendChild(head);

    // ------------------------------------------------ űrlap
    els.form = h('form', { 'class': 'plan-form', id: 'plan-form', novalidate: true, onsubmit: function (ev) { ev.preventDefault(); } });
    els.search = h('input', { type: 'search', id: 'plan-search', value: st.query || '', 'aria-controls': 'plan-form',
      i18nAttrs: { placeholder: 'plan.searchPlaceholder' },
      oninput: function () { st.query = els.search.value; applySearch(); } });
    var formPanel = h('section', { 'class': 'panel plan-options', 'aria-labelledby': 'plan-options-h' },
      h('div', { 'class': 'panel-head-row' },
        h('h2', { 'class': 'panel-title', id: 'plan-options-h' }, t('plan.options')),
        h('label', { 'class': 'plan-search-label', 'for': 'plan-search' }, t('plan.search')), els.search),
      h('p', { 'class': 'muted plan-meta-src' }, t('plan.metaSource', { version: (MA.store.get('engine') || {}).engine_version || '—' })),
      els.form);
    root.appendChild(formPanel);
    buildForm();

    // ------------------------------------------------ eltérés-sáv, parancs, műveletek
    els.banner = h('section', { 'class': 'panel plan-banner', id: 'plan-banner', role: 'status', 'aria-live': 'polite' });
    root.appendChild(els.banner);
    els.cmd = h('section', { 'class': 'panel plan-cmd', id: 'plan-cmd', 'aria-labelledby': 'plan-cmd-h' });
    root.appendChild(els.cmd);
    els.autoBox = h('input', { type: 'checkbox', id: 'plan-auto', checked: autoPref(),
      onchange: function () { MA.prefs.set('analysis.autoExplore', els.autoBox.checked ? '1' : '0'); renderActions(); if (els.autoBox.checked && autoAllowed()) { exploreDebounced(); } } });
    els.exploreBtn = h('button', { type: 'button', 'class': 'btn', id: 'plan-explore', onclick: function () { exploreDebounced.cancel(); explore(); } }, t('plan.explore'));
    els.commitBtn = h('button', { type: 'button', 'class': 'btn btn-primary', id: 'plan-commit', onclick: commit }, t('plan.commit'));
    els.status = h('span', { 'class': 'plan-status', id: 'plan-status', role: 'status', 'aria-live': 'polite' });
    els.kHint = h('span', { 'class': 'muted plan-khint', id: 'plan-khint' });
    root.appendChild(h('section', { 'class': 'panel plan-actions', 'aria-label': t('plan.actions') },
      h('div', { 'class': 'toolbar' }, els.exploreBtn,
        h('label', { 'class': 'plan-auto', 'for': 'plan-auto' }, els.autoBox, ' ', t('plan.auto', { k: String(AUTO_MAX_K) })),
        els.commitBtn, els.status),
      els.kHint));
    els.children = h('section', { 'class': 'panel plan-children', id: 'plan-children', 'aria-labelledby': 'plan-children-h' });
    root.appendChild(els.children);
    els.result = h('section', { 'class': 'panel plan-result', id: 'plan-result', 'aria-labelledby': 'plan-result-h' });
    root.appendChild(els.result);
    els.runs = h('section', { 'class': 'panel plan-runs', id: 'plan-runs', 'aria-labelledby': 'plan-runs-h' });
    root.appendChild(els.runs);

    var exploreDebounced = MA.ui.debounce(function () { explore(); }, DEBOUNCE_MS);
    ctx.onCleanup(function () { exploreDebounced.cancel(); });

    renderAll();
    if (autoAllowed() && !fresh() && !hasInvalid()) { explore(); }

    // ================================================ részek
    function renderAll() {
      renderSpecMeta();
      renderMarks();
      renderBanner();
      renderCmd();
      renderActions();
      renderChildren();
      renderResult();
      renderRuns();
    }

    function renderSpecMeta() {
      var d = st.draft;
      var purpose = MA.i18n.has('plan.purpose.' + d.purpose) ? t('plan.purpose.' + d.purpose) : String(d.purpose || '—');
      MA.dom.mount(els.specMeta,
        MA.ui.badge(d.purpose === 'primary' ? 'info' : 'neutral', purpose),
        ' ',
        d.prespecified ? MA.ui.badge('ok', t('plan.prespecified', { ref: d.protocol_ref || '—' })) : MA.ui.badge('warning', t('plan.notPrespecified')),
        d.parent ? [' ', h('a', { href: ctx.href('analysis', { outcome: outcome.id, spec: d.parent }), 'class': 'plan-parent' }, t('plan.parent', { name: d.parent }))] : null,
        st.saved ? null : [' ', MA.ui.badge('pending', t('plan.unsaved'))]);
    }

    function fieldFor(name) {
      var m = meta[name] || {};
      var id = 'opt-' + name;
      var helpId = id + '-help';
      var errId = id + '-err';
      var v = st.draft.options[name];
      var help = m.help ? pick(m.help, '') : '';
      var control;
      var describedBy = [help ? helpId : null, errId].filter(Boolean).join(' ');
      function changed(val) { onOption(name, val); }
      function invalid(msgKey) {
        if (msgKey) { st.invalid[name] = msgKey; } else { delete st.invalid[name]; }
        control.setAttribute('aria-invalid', msgKey ? 'true' : 'false');
        errEl.textContent = msgKey ? t(msgKey) : '';
        renderActions();
      }
      var errEl = h('span', { 'class': 'opt-err', id: errId, role: 'alert' });
      if (m.type === 'enum' && Array.isArray(m.choices)) {
        var opts = [];
        if (m['default'] === null || m['default'] === undefined) { opts.push(h('option', { value: '' }, t('plan.value.defaultOption'))); }
        m.choices.forEach(function (c) {
          opts.push(h('option', { value: String(c) }, name === 'measure' ? String(c) + ' — ' + A.measureLabel(c) : String(c)));
        });
        if (v !== null && v !== undefined && m.choices.indexOf(v) < 0) { opts.push(h('option', { value: String(v) }, String(v) + ' ?')); }
        control = h('select', { id: id, 'data-opt': name, 'aria-describedby': describedBy }, opts);
        control.value = v === null || v === undefined ? '' : String(v);
        control.addEventListener('change', function () {
          var raw = control.value;
          var val = raw === '' ? null : (m.choices.filter(function (c) { return String(c) === raw; })[0]);
          changed(val === undefined ? raw : val);
        });
      } else if (m.type === 'bool') {
        control = h('input', { type: 'checkbox', id: id, 'data-opt': name, checked: v === true, 'aria-describedby': describedBy });
        control.addEventListener('change', function () { changed(control.checked); });
      } else if (m.type === 'number') {
        control = h('input', { type: 'text', id: id, 'data-opt': name, inputmode: 'decimal', autocomplete: 'off', spellcheck: 'false',
          value: v === null || v === undefined ? '' : String(v), 'aria-describedby': describedBy });
        control.addEventListener('input', function () {
          var r = parseNum(control.value);
          if (!r.ok) { invalid('plan.err.number'); return; }
          invalid(null);
          changed(r.value);
        });
      } else if (m.type === 'list') {
        control = h('input', { type: 'text', id: id, 'data-opt': name, autocomplete: 'off', value: Array.isArray(v) ? v.join(', ') : '',
          'aria-describedby': describedBy });
        control.addEventListener('input', function () { changed(splitList(control.value)); });
      } else if (m.type === 'object') {
        control = h('textarea', { id: id, 'data-opt': name, rows: '2', spellcheck: 'false', value: v === null || v === undefined ? '' : JSON.stringify(v),
          'aria-describedby': describedBy });
        control.addEventListener('input', function () {
          var raw = control.value.trim();
          if (!raw) { invalid(null); changed(null); return; }
          var parsed;
          try { parsed = JSON.parse(raw); } catch (e) { invalid('plan.err.json'); return; }
          if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) { invalid('plan.err.json'); return; }
          invalid(null);
          changed(parsed);
        });
      } else {
        control = h('input', { type: 'text', id: id, 'data-opt': name, autocomplete: 'off', value: v === null || v === undefined ? '' : String(v),
          'aria-describedby': describedBy });
        control.addEventListener('input', function () { changed(control.value === '' ? null : control.value); });
      }
      if (st.invalid[name]) { control.setAttribute('aria-invalid', 'true'); errEl.textContent = t(st.invalid[name]); }
      var label = h('label', { 'for': id, 'class': 'opt-label' }, optLabel(name, m),
        h('span', { 'class': 'opt-changed', 'aria-hidden': 'true' }, ' •'),
        h('span', { 'class': 'sr-only opt-changed-sr' }, ' ' + t('plan.changedSr')));
      var cli = m.cli ? h('code', { 'class': 'opt-cli' }, m.cli) : h('span', { 'class': 'opt-cli muted' }, t('plan.specOnly'));
      var wrap = h('div', { 'class': ['opt-field', 'opt-' + (m.type || 'string')], 'data-field': name, 'data-search': (name + ' ' + optLabel(name, m) + ' ' + (m.cli || '') + ' ' + help).toLowerCase() },
        m.type === 'bool' ? h('div', { 'class': 'opt-row' }, control, ' ', label) : [label, control],
        h('div', { 'class': 'opt-sub' }, cli, help ? h('small', { 'class': 'opt-help', id: helpId, title: help }, help) : null),
        errEl);
      return wrap;
    }

    function filterField(kind) {
      var id = 'flt-' + kind;
      var errId = id + '-err';
      var errEl = h('span', { 'class': 'opt-err', id: errId, role: 'alert' });
      var input = h('input', { type: 'text', id: id, 'data-filter': kind, autocomplete: 'off', value: (st.draft.filters[kind] || []).join(', '),
        'aria-describedby': id + '-help ' + errId });
      input.addEventListener('input', function () {
        var list = splitList(input.value);
        var bad = list.filter(function (x) { return !FILTER_RE.test(x); });
        var key = 'filters.' + kind;
        if (bad.length) {
          st.invalid[key] = 'plan.err.filter';
          input.setAttribute('aria-invalid', 'true');
          errEl.textContent = t('plan.err.filter');
          renderActions();
          return;
        }
        delete st.invalid[key];
        input.setAttribute('aria-invalid', 'false');
        errEl.textContent = '';
        st.draft.filters[kind] = list;
        afterChange();
      });
      return h('div', { 'class': 'opt-field opt-filter', 'data-field': 'filters.' + kind, 'data-search': ('filter szűrő ' + kind).toLowerCase() },
        h('label', { 'for': id, 'class': 'opt-label' }, t('plan.filters.' + kind),
          h('span', { 'class': 'opt-changed', 'aria-hidden': 'true' }, ' •'),
          h('span', { 'class': 'sr-only opt-changed-sr' }, ' ' + t('plan.changedSr'))),
        input,
        h('div', { 'class': 'opt-sub' }, h('code', { 'class': 'opt-cli' }, kind === 'include' ? '--include' : '--exclude'),
          h('small', { 'class': 'opt-help', id: id + '-help' }, t('plan.filters.help'))),
        errEl);
    }

    function buildForm() {
      var groups = {};
      var order = [];
      Object.keys(meta).forEach(function (name) {
        var m = meta[name] || {};
        var g = m.group ? String(m.group) : (m.cli ? 'cli' : 'spec');
        if (!groups[g]) { groups[g] = []; order.push(g); }
        groups[g].push(name);
      });
      var nodes = order.map(function (g) {
        var title = MA.i18n.has('plan.group.' + g) ? t('plan.group.' + g) : g;
        var fields = groups[g].map(fieldFor);
        if (g === 'spec' || (meta[groups[g][0]] && meta[groups[g][0]].advanced)) {
          return h('details', { 'class': 'opt-group is-advanced', 'data-group': g },
            h('summary', null, title + ' (' + String(fields.length) + ')'),
            h('div', { 'class': 'opt-grid' }, fields));
        }
        return h('fieldset', { 'class': 'opt-group', 'data-group': g }, h('legend', null, title), h('div', { 'class': 'opt-grid' }, fields));
      });
      nodes.push(h('fieldset', { 'class': 'opt-group', 'data-group': 'filters' }, h('legend', null, t('plan.group.filters')),
        h('div', { 'class': 'opt-grid' }, filterField('include'), filterField('exclude'))));
      MA.dom.mount(els.form, nodes);
      applySearch();
    }

    function applySearch() {
      var q = (st.query || '').trim().toLowerCase();
      MA.dom.$$('.opt-field', els.form).forEach(function (f) {
        f.hidden = !!q && (f.getAttribute('data-search') || '').indexOf(q) < 0;
      });
      MA.dom.$$('details.opt-group', els.form).forEach(function (d) { if (q) { d.open = true; } });
    }

    function onOption(name, value) {
      st.draft.options[name] = value;
      afterChange();
    }

    function afterChange() {
      renderMarks();
      renderBanner();
      renderCmd();
      renderActions();
      renderResultFreshness();
      if (autoAllowed() && !hasInvalid()) { exploreDebounced(); }
    }

    function renderMarks() {
      var saved = st.saved;
      MA.dom.$$('.opt-field', els.form).forEach(function (f) {
        var key = f.getAttribute('data-field');
        var isChanged = false;
        if (saved) {
          if (key.indexOf('filters.') === 0) {
            var k2 = key.slice(8);
            isChanged = !same((saved.filters || {})[k2] || [], st.draft.filters[k2] || []);
          } else {
            var a = saved.options[key];
            var b = st.draft.options[key];
            isChanged = !same(a === undefined ? null : a, b === undefined ? null : b);
          }
        }
        f.classList.toggle('is-changed', isChanged);
      });
    }

    function renderBanner() {
      var saved = st.saved;
      var d = st.draft;
      var diffs = specDiffs(saved, d, meta);
      var kids = [];
      if (!saved) {
        els.banner.className = 'panel plan-banner is-info';
        kids.push(h('p', null, MA.ui.badge('info', t('plan.banner.newTitle')), ' ', t('plan.banner.new')));
      } else if (!diffs.length) {
        els.banner.className = 'panel plan-banner is-ok';
        kids.push(h('p', null, saved.prespecified
          ? [MA.ui.badge('ok', t('plan.banner.matchTitle')), ' ', t('plan.banner.match', { ref: saved.protocol_ref || '—' })]
          : [MA.ui.badge('warning', 'X016'), ' ', t('plan.banner.notPrespecified')]));
      } else {
        var protocol = saved.prespecified && saved.purpose === 'primary';
        els.banner.className = 'panel plan-banner ' + (protocol ? 'is-warning' : 'is-info');
        kids.push(h('p', { 'class': 'plan-banner-title' },
          MA.ui.badge(protocol ? 'warning' : 'info', protocol ? t('plan.banner.deviationTitle') : t('plan.banner.changedTitle')), ' ',
          protocol ? t('plan.banner.deviation', { ref: saved.protocol_ref || '—' }) : t('plan.banner.changed')));
        kids.push(h('ul', { 'class': 'plan-diff', id: 'plan-diff' }, diffs.map(function (x) {
          return h('li', { 'data-key': x.key },
            h('strong', null, x.label), ': ', t('plan.banner.diffLine', { before: x.before, after: x.after }));
        })));
        if (protocol) { kids.push(h('p', { 'class': 'muted' }, t('plan.banner.decisionNeeded'), ' ', A.kbButton('D-S12-006'))); }
      }
      MA.dom.mount(els.banner, h('h2', { 'class': 'sr-only' }, t('plan.banner.aria')), kids);
    }

    function renderCmd() {
      var src = null;
      var note = null;
      if (st.lastRun && st.lastRun.run) {
        src = st.lastRun.run;
        note = fresh() ? t('plan.cmd.fromExplore') : t('plan.cmd.stale');
      } else {
        var r = specRuns()[0];
        if (r) { src = r; note = t('plan.cmd.fromCommit', { run: r.run_id }); }
      }
      var main = src ? A.cmd(src.equivalent_argv) : '';
      var exp = src && src.expanded_argv ? A.cmd(src.expanded_argv) : '';
      MA.dom.mount(els.cmd,
        h('h2', { 'class': 'panel-title', id: 'plan-cmd-h' }, t('plan.cmd.title')),
        main
          ? [h('div', { 'class': 'cmd-row' }, h('code', { 'class': 'cmd-inline', id: 'plan-cmd-main' }, main), MA.ui.copyButton(function () { return main; })),
            exp ? h('div', { 'class': 'cmd-row' }, h('span', { 'class': 'muted' }, t('plan.cmd.equivalent') + ' '),
              h('code', { 'class': 'cmd-inline', id: 'plan-cmd-expanded' }, exp), MA.ui.copyButton(function () { return exp; })) : null,
            h('p', { 'class': ['muted', 'plan-cmd-note', src && !fresh() && st.lastRun ? 'is-stale' : ''] }, note)]
          : MA.ui.emptyState('plan.cmd.none'));
    }

    function renderActions() {
      var k = knownK();
      var invalid = hasInvalid();
      els.exploreBtn.disabled = invalid;
      els.commitBtn.disabled = invalid || !!st.committing;
      els.autoBox.disabled = k !== null && k > AUTO_MAX_K;
      var hint = '';
      if (invalid) { hint = t('plan.hint.invalid'); } else if (k !== null && k > AUTO_MAX_K) { hint = t('plan.hint.bigK', { k: String(k), max: String(AUTO_MAX_K) }); } else if (k !== null) { hint = t('plan.hint.k', { k: String(k) }); }
      els.kHint.textContent = hint;
      els.kHint.classList.toggle('is-warning', invalid || (k !== null && k > AUTO_MAX_K));
    }

    function setStatus(kind, text) {
      MA.dom.mount(els.status, kind ? MA.ui.badge(kind, text) : null);
    }

    // ------------------------------------------------ explore
    function explore() {
      if (hasInvalid()) { return Promise.resolve(null); }
      var spec = currentSpec();
      var specJson = JSON.stringify(st.draft);
      setStatus('progress', t('plan.status.exploring'));
      els.result.setAttribute('aria-busy', 'true');
      return runLatest(function (seq, sig) {
        return A.analyze({ mode: 'explore', spec: spec }, { clientSeq: seq, signal: A.linkSignals(sig, ctx.signal) });
      }).then(function (job) {
        if (!job || !ctx.alive()) { return null; }
        els.result.removeAttribute('aria-busy');
        if (job.status === 'superseded') { setStatus('neutral', t('plan.status.superseded')); return null; }
        var res = job.result || {};
        st.lastRun = { run: res.run || null, plot: res.plot || null, specJson: specJson, validation: res.validation_summary || null,
          excluded: Array.isArray(res.excluded) ? res.excluded : [], seq: job.client_seq };
        st.exploreError = null;
        MA.store.set('analysis.explore.' + outcome.id, { run: res.run || null, plot: res.plot || null, spec: spec, specName: specName });
        setStatus('ok', t('plan.status.explored', { seq: String(job.client_seq) }));
        renderCmd();
        renderActions();
        renderResult();
        return job;
      }, function (err) {
        if (!ctx.alive() || err.code === 'ABORTED') { return null; }
        els.result.removeAttribute('aria-busy');
        st.exploreError = { code: err.code, message: err.message };
        setStatus('error', t('plan.status.failed'));
        renderResult();
        return null;
      });
    }

    function renderResultFreshness() {
      var badge = MA.dom.$('#plan-result-fresh');
      if (badge) { MA.dom.mount(badge, st.lastRun && !fresh() ? MA.ui.badge('stale', t('plan.result.outdated')) : null); }
    }

    function renderResult() {
      var lr = st.lastRun;
      var kids = [h('h2', { 'class': 'panel-title', id: 'plan-result-h' }, t('plan.result.title'), ' ', h('span', { id: 'plan-result-fresh' }))];
      if (st.exploreError) {
        kids.push(MA.ui.errorBox(st.exploreError));
      }
      if (!lr || !lr.run) {
        kids.push(MA.ui.emptyState('plan.result.none'));
        MA.dom.mount(els.result, kids);
        renderResultFreshness();
        return;
      }
      var plot = lr.plot;
      var prim = plot ? ((plot.summaries || []).filter(function (s) { return s.primary === true; })[0] || (plot.summaries || [])[0]) : null;
      var het = plot ? plot.heterogeneity : null;
      var vs = lr.validation || {};
      kids.push(h('p', { 'class': 'plan-summary', id: 'plan-summary' },
        MA.ui.badge('info', t('analysis.run.explore')), ' ',
        h('span', { 'class': 'muted' }, 'k = '), h('span', { 'class': 'num' }, typeof lr.run.k === 'number' ? String(lr.run.k) : '—'), ' · ',
        h('span', { 'class': 'muted' }, (plot && plot.measure) || st.draft.options.measure || ''), ' ',
        MA.ui.num(prim ? prim.display_text : (lr.run.primary || {}).display_text),
        prim && prim.p_text ? [' · ', MA.ui.num(prim.p_text)] : null,
        het && het.i2_text ? [' · I² ', MA.ui.num(het.i2_text)] : null,
        prim && prim.pi_text ? [' · PI ', MA.ui.num(prim.pi_text)] : null));
      kids.push(h('p', { 'class': 'plan-validation' }, t('plan.result.validation'), ' ',
        ['error', 'warning', 'info'].map(function (k) {
          return [MA.ui.badge(k, typeof vs[k] === 'number' ? String(vs[k]) : '—', { srLabel: t('plan.result.count.' + k) }), ' '];
        })));
      if (lr.excluded.length) {
        kids.push(h('div', { 'class': 'plan-excluded' }, h('p', null, MA.ui.badge('warning', t('plan.result.excluded', { n: String(lr.excluded.length) }))),
          h('ul', { 'class': 'item-list' }, lr.excluded.map(function (x) {
            return h('li', { 'class': 'item' }, h('span', { 'class': 'item-title' }, String(x.study || '—')), h('span', { 'class': 'muted' }, ' ' + pick(x.reason, '')));
          }))));
      }
      var previewHost = h('div', { 'class': 'plan-preview', id: 'plan-preview' });
      var details = h('details', { 'class': 'plan-preview-box', open: MA.prefs.get('plan.preview', '1') !== '0',
        on: { toggle: function () { MA.prefs.set('plan.preview', details.open ? '1' : '0'); } } },
      h('summary', null, t('plan.result.preview')), previewHost);
      kids.push(details);
      kids.push(h('p', null, h('a', { 'class': 'btn btn-sm', id: 'plan-open-results', href: ctx.href('results', { outcome: outcome.id, run: 'explore' }) }, t('plan.result.open'))));
      MA.dom.mount(els.result, kids);
      renderResultFreshness();
      if (plot) {
        MA.plots.forest.render(previewHost, plot, {
          signal: ctx.signal,
          layers: MA.plots.forest.layersPref(),
          onDrill: function (uid) { ctx.navigate('results', { outcome: outcome.id, run: 'explore', row: uid }); }
        });
      }
    }

    // ------------------------------------------------ futások
    function renderRuns() {
      var runs = st.runs || [];
      var kids = [h('h2', { 'class': 'panel-title', id: 'plan-runs-h' }, t('plan.runs.title'))];
      if (!runs.length) {
        kids.push(MA.ui.emptyState('plan.runs.none'));
        MA.dom.mount(els.runs, kids);
        return;
      }
      kids.push(h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: 'plan-runs-table' },
        h('thead', null, h('tr', null, ['plan.runs.col.time', 'plan.runs.col.spec', 'plan.runs.col.k', 'plan.runs.col.effect', 'plan.runs.col.i2',
          'plan.runs.col.data', 'plan.runs.col.state'].map(function (k) {
          return h('th', { scope: 'col', 'class': k === 'plan.runs.col.k' || k === 'plan.runs.col.i2' ? 'num' : null }, t(k));
        }))),
        h('tbody', null, runs.map(function (r) {
          var sp = r.spec || {};
          return h('tr', { 'data-run': r.run_id, 'class': { 'is-stale': !!r.stale, 'is-selected': sp.name === specName } },
            h('th', { scope: 'row' }, h('a', { href: ctx.href('results', { outcome: outcome.id, run: r.run_id }) }, MA.i18n.ts(r.started || r.finished))),
            h('td', null, sp.parent ? '└ ' + (sp.name || '—') : (sp.name || '—')),
            h('td', { 'class': 'num' }, typeof r.k === 'number' ? String(r.k) : '—'),
            h('td', null, h('span', { 'class': 'muted' }, (r.measure || '') + ' '), MA.ui.num(r.primary ? r.primary.display_text : null)),
            h('td', { 'class': 'num' }, MA.ui.numText(r.primary ? r.primary.i2_text : null)),
            h('td', null, h('code', { title: r.data ? r.data.sha256 : '' }, A.shortSha(r.data ? r.data.sha256 : null))),
            h('td', null, A.runBadge(r)));
        })))));
      MA.dom.mount(els.runs, kids);
    }

    function reloadRuns() {
      return A.loadRuns(outcome.id, ctx.signal).then(function (runs) {
        if (!ctx.alive()) { return; }
        st.runs = runs;
        renderRuns();
        renderChildren();
        renderCmd();
        renderActions();
      }, function () { /* a hibát a toast jelezte */ });
    }

    // ------------------------------------------------ gyermek-futások
    function renderChildren() {
      var d = st.saved || st.draft;
      var kids = [h('h2', { 'class': 'panel-title', id: 'plan-children-h' }, t('plan.children.title'))];
      if (d.parent || (st.saved && st.saved.purpose !== 'primary')) {
        kids.push(h('p', { 'class': 'muted' }, t('plan.children.isChild')));
        MA.dom.mount(els.children, kids);
        return;
      }
      if (!st.saved) {
        kids.push(h('p', { 'class': 'muted' }, t('plan.children.saveFirst')));
      }
      var existing = {};
      (st.runs || []).forEach(function (r) { if (r.spec && r.spec.parent === specName) { existing[r.spec.name] = (existing[r.spec.name] || 0) + 1; } });
      kids.push(h('div', { 'class': 'toolbar plan-child-buttons' }, presetsFor(meta).map(function (p) {
        var name = childName(specName, p.suffix || ('_' + p.id));
        var busy = st.childBusy && st.childBusy[p.id];
        var label = p.label ? pick(p.label, p.id) : (MA.i18n.has(p.label_key || '') ? t(p.label_key) : p.id);
        return h('span', { 'class': 'plan-child', 'data-child': p.id },
          h('button', { type: 'button', 'class': 'btn btn-sm', id: 'child-' + p.id, disabled: !p.available || !st.saved || !!busy,
            title: p.available ? t('plan.children.creates', { name: name }) : t('plan.children.unavailable'),
            onclick: function () { makeChild(p, name); } }, '+ ' + label),
          busy ? MA.ui.spinner('plan.children.running') : null,
          existing[name] ? MA.ui.badge('ok', t('plan.children.has', { n: String(existing[name]) })) : null,
          p.kb ? A.kbButton(p.kb) : null);
      })));
      if (st.saved && !same(st.saved, st.draft)) { kids.push(h('p', { 'class': 'muted' }, t('plan.children.fromSaved'))); }
      MA.dom.mount(els.children, kids);
    }

    function makeChild(p, name) {
      var parent = clone(st.saved);
      if (!parent) { return; }
      var child = clone(parent);
      child.name = name;
      child.purpose = 'sensitivity';
      child.parent = parent.name;
      child.prespecified = !!parent.prespecified;
      child.protocol_ref = parent.protocol_ref || null;
      Object.keys(p.options || {}).forEach(function (k) { child.options[k] = clone(p.options[k]); });
      var ex = (p.filters && p.filters.exclude) || (p.exclude ? [p.exclude] : []);
      ex.forEach(function (e) { if (child.filters.exclude.indexOf(e) < 0) { child.filters.exclude.push(e); } });
      child.kb_refs = p.kb ? [p.kb] : [];
      st.childBusy = st.childBusy || {};
      st.childBusy[p.id] = true;
      renderChildren();
      MA.api.put('/api/specs/' + encodeURIComponent(name), child).then(function () {
        return A.analyze({ mode: 'commit', spec: child });
      }).then(function (job) {
        var run = job.result && job.result.run;
        if (run) { if (!run.outcome_id) { run.outcome_id = outcome.id; } A.addRun(run); }
        MA.ui.toast({ kind: 'success', title: t('plan.children.done', { name: name }), message: run ? run.run_id : '',
          actions: run ? [{ label: t('plan.result.openRun'), onClick: function () { ctx.navigate('results', { outcome: outcome.id, run: run.run_id }); } }] : [] });
      }).catch(function () { /* a hibát a toast jelezte */ }).then(function () {
        st.childBusy[p.id] = false;
        if (ctx.alive()) { reloadRuns(); }
      });
    }

    // ------------------------------------------------ rögzítés (commit)
    function decisionModal(diffs, excluded, needDecision) {
      return new Promise(function (resolve) {
        var answered = false;
        var reasonId = MA.dom.uid('reason');
        var reason = h('textarea', { id: reasonId, rows: '3', 'class': 'plan-reason' });
        var body = [
          needDecision ? h('p', null, t('plan.commitModal.deviation', { ref: (st.saved && st.saved.protocol_ref) || '—' })) : null,
          needDecision ? h('ul', { 'class': 'plan-diff' }, diffs.map(function (x) { return h('li', null, h('strong', null, x.label), ': ', t('plan.banner.diffLine', { before: x.before, after: x.after })); })) : null,
          excluded.length ? h('p', null, t('plan.commitModal.excluded', { n: String(excluded.length) })) : null,
          excluded.length ? h('ul', null, excluded.map(function (x) { return h('li', null, String(x.study || '—') + ' — ' + pick(x.reason, '')); })) : null,
          needDecision ? [h('label', { 'for': reasonId }, t('plan.commitModal.reason')), reason, h('p', { 'class': 'muted' }, t('plan.commitModal.reasonHelp'))] : null
        ];
        var actions = [{ label: t('common.cancel'), kind: 'ghost', onClick: function () { answered = true; resolve(null); } }];
        if (needDecision) {
          actions.push({ label: t('plan.commitModal.later'), onClick: function () { answered = true; resolve({ ok: true, reason: null }); } });
          actions.push({ label: t('plan.commitModal.withReason'), kind: 'primary', onClick: function () {
            var txt = reason.value.trim();
            if (!txt) { reason.setAttribute('aria-invalid', 'true'); reason.focus(); return false; }
            answered = true;
            resolve({ ok: true, reason: txt });
            return undefined;
          } });
        } else {
          actions.push({ label: t('plan.commit'), kind: 'primary', onClick: function () { answered = true; resolve({ ok: true, reason: null }); } });
        }
        MA.ui.modal({ title: t('plan.commitModal.title'), body: body, actions: actions, size: 'md',
          onClose: function () { if (!answered) { resolve(null); } } });
      });
    }

    function saveSpec(spec) {
      return MA.api.put('/api/specs/' + encodeURIComponent(spec.name), spec, { ifMatch: st.etag || null, quiet: ['CONFLICT'] }).then(function (env) {
        st.saved = normalize(env.data || spec, meta);
        st.etag = env.etag || st.etag;
        return env;
      }, function (err) {
        if (err.code === 'CONFLICT') {
          return MA.api.get('/api/specs/' + encodeURIComponent(spec.name), { toast: false }).then(function (env) {
            st.saved = normalize(env.data, meta);
            st.etag = env.etag;
            if (ctx.alive()) { renderAll(); }
            MA.ui.toast({ kind: 'warning', title: t('plan.conflict.title'), message: t('plan.conflict.body'), timeout: 0 });
            throw err;
          });
        }
        throw err;
      });
    }

    function commit() {
      if (hasInvalid() || st.committing) { return; }
      var spec = currentSpec();
      var diffs = specDiffs(st.saved, spec, meta);
      var needDecision = !!(st.saved && st.saved.prespecified && st.saved.purpose === 'primary' && diffs.length);
      var excluded = fresh() ? st.lastRun.excluded : [];
      var pre = needDecision || excluded.length ? decisionModal(diffs, excluded, needDecision) : Promise.resolve({ ok: true, reason: null });
      pre.then(function (ans) {
        if (!ans) { return null; }
        st.committing = true;
        renderActions();
        setStatus('progress', t('plan.status.committing'));
        var chain = Promise.resolve();
        if (ans.reason) {
          chain = chain.then(function () {
            return MA.api.post('/api/log/decision', {
              agent: 'user', stage: 'S12', decision: t('plan.decision.title', { spec: spec.name }), rationale: ans.reason,
              kb_refs: ['D-S12-006'],
              context: { spec: spec.name, outcome: outcome.id, changes: diffs.map(function (x) { return { key: x.key, before: x.before, after: x.after }; }) }
            });
          });
        }
        return chain.then(function () { return saveSpec(spec); }).then(function () {
          var body = { mode: 'commit', spec: clone(spec) };
          if (fresh() && st.lastRun.run && st.lastRun.run.data && st.lastRun.run.data.sha256) {
            body.spec.data = Object.assign({}, body.spec.data, { sha256: st.lastRun.run.data.sha256 });
          }
          return A.analyze(body, { onProgress: function (j) { if (ctx.alive()) { setStatus('progress', t('plan.status.job', { status: j.status || '…' })); } } });
        }).then(function (job) {
          var run = job.result && job.result.run;
          if (run) { if (!run.outcome_id) { run.outcome_id = outcome.id; } A.addRun(run); }
          MA.ui.toast({ kind: 'success', title: t('plan.status.committed'), message: run ? run.run_id : '',
            actions: run ? [{ label: t('plan.result.openRun'), onClick: function () { ctx.navigate('results', { outcome: outcome.id, run: run.run_id }); } }] : [] });
          if (ctx.alive()) { setStatus('ok', t('plan.status.committed')); }
          return run;
        });
      }).catch(function () {
        if (ctx.alive()) { setStatus('error', t('plan.status.commitFailed')); }
      }).then(function () {
        st.committing = false;
        if (ctx.alive()) { renderAll(); reloadRuns(); }
      });
    }

    // ------------------------------------------------ JSON, visszaállítás
    function showJson() {
      var txt = JSON.stringify(currentSpec(), null, 2);
      MA.ui.modal({ title: t('plan.jsonTitle', { name: specName }), size: 'lg',
        body: [h('pre', { 'class': 'cmd plan-json-pre', id: 'plan-json-pre', tabindex: '0' }, txt), MA.ui.copyButton(function () { return txt; })],
        actions: [{ label: t('common.close'), kind: 'primary' }] });
    }

    function resetDraft() {
      if (!st.saved) { return; }
      st.draft = clone(st.saved);
      st.invalid = {};
      buildForm();
      afterChange();
    }
  }

  MA.app.registerScreen({
    id: 'analysis',
    title_key: 'plan.title',
    workspace: 'analysis',
    tab: 'analysis',
    order: 10,
    render: render
  });
})();
