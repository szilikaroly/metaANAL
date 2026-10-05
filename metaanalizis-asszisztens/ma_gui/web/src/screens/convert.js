/* screens/convert.js — Átváltó modális (terv 3.5.4, 4.7, 4.8). Nem önálló képernyő: MA.convertDialog.open(opts).
 *
 * POST /api/convert ← szk.ma.convert-request/v1 {kind, inputs (nyers szövegek), method?, target {dataset, row_uid,
 *   fields: {kimenet: kanonikus oszlop}, decimal_mark}} → szk.ma.convert-result/v1 {outputs, outputs_text {kimenet: {hu,en}},
 *   cell_text? {kimenet: szöveg a tábla tizedesjelével}, estimated, method {id, citation, function, kb_refs}, assumptions, warnings}.
 * A „becsült” (estimated) mindig a motor döntése; a felület nem számol és nem kerekít: a cellába a motor szövege kerül
 * (cell_text, különben outputs_text a tábla tizedesjele szerint: ',' → hu, '.' → en).
 * opts: {dataset, rowUid, study, columns: [{id, label, field}], activeField, values {mező: szöveg}, decimalMark,
 *        documents: [{id, title?}], source {doc, page, locator}, onApply({rowUid, request, result, writes: [{col, text}], source}) → Promise}
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  var KINDS = [
    { id: 'median_to_mean_sd', inputs: ['n', 'median', 'q1', 'q3', 'min', 'max'], methods: ['luo', 'hozo'], outputs: ['mean', 'sd'] },
    { id: 'se_to_sd', inputs: ['se', 'n'], outputs: ['sd'] },
    { id: 'ci_to_sd', inputs: ['lower', 'upper', 'n', 'level'], outputs: ['sd'] },
    { id: 'combine_groups', inputs: ['n1', 'm1', 'sd1', 'n2', 'm2', 'sd2'], outputs: ['n', 'mean', 'sd'] },
    { id: 'change_sd', inputs: ['sd_baseline', 'sd_final', 'corr'], outputs: ['sd'] }
  ];
  var TARGET = { mean: 'm', sd: 'sd', n: 'n' };   // kimenet → kanonikus oszlop-előtag (+ kar: 1 | 2)
  var LIVE_MS = 400;

  function kindById(id) { for (var i = 0; i < KINDS.length; i++) { if (KINDS[i].id === id) { return KINDS[i]; } } return KINDS[0]; }

  /** A cellába írandó szöveg: csak a motor cell_text-je (a cél tábla tizedesjelével, 4.7). Az outputs_text
   * kijelzési szöveg (4.0: tizedespont mindkét nyelven), cellába nem kerül; nincs cell_text → null (nincs beírás). */
  function cellText(result, out) {
    if (result && result.cell_text && typeof result.cell_text[out] === 'string') { return result.cell_text[out]; }
    return null;
  }

  function open(opts) {
    var arm = /2$/.test(opts.activeField || '') ? '2' : '1';
    var kindSel, methodBox, inputsBox, targetsBox, resultBox, errBox, applyErr;
    var inputs = {};
    var targets = {};
    var state = { result: null, request: null, sig: null };
    var run = MA.api.latest();
    var cols = opts.columns || [];
    var byField = {};
    cols.forEach(function (c) { if (!byField[c.field]) { byField[c.field] = c; } });

    function kind() { return kindById(kindSel.value); }

    function buildRequest() {
      var k = kind();
      var ins = {};
      k.inputs.forEach(function (n) { var v = inputs[n] ? inputs[n].value.trim() : ''; if (v !== '') { ins[n] = v; } });
      var fields = {};
      k.outputs.forEach(function (o) { var c = targets[o] && targets[o].value; if (c) { var col = cols.filter(function (x) { return x.id === c; })[0]; fields[o] = col ? col.field : c; } });
      var req = { schema: 'szk.ma.convert-request/v1', kind: k.id, inputs: ins,
        target: { dataset: opts.dataset, row_uid: opts.rowUid, fields: fields, decimal_mark: opts.decimalMark || null } };
      if (k.methods) {
        var m = methodBox.querySelector('input:checked');
        req.method = m ? m.value : k.methods[0];
      }
      return req;
    }

    function buildForm() {
      var k = kind();
      MA.dom.mount(methodBox, k.methods ? h('fieldset', { 'class': 'cv-methods' },
        h('legend', null, t('convert.method')),
        k.methods.map(function (m, i) {
          return h('label', { 'class': 'ex-check' }, h('input', { type: 'radio', name: 'cv-method', value: m, checked: i === 0, onchange: live }), ' ', t('convert.method.' + m));
        })) : null);
      inputs = {};
      MA.dom.mount(inputsBox, k.inputs.map(function (n) {
        var id = 'cv-in-' + n;
        var pre = '';
        if (n === 'n' && opts.values) { pre = opts.values['n' + arm] || opts.values.n || ''; }
        if (n === 'level') { pre = ''; }
        inputs[n] = h('input', { type: 'text', id: id, inputmode: 'decimal', 'class': 'cv-in', value: pre, autocomplete: 'off', oninput: live });
        return h('div', { 'class': 'ex-fld' }, h('label', { htmlFor: id }, t('convert.in.' + n)), inputs[n]);
      }));
      targets = {};
      MA.dom.mount(targetsBox, h('legend', null, t('convert.targets')), k.outputs.map(function (o) {
        var id = 'cv-target-' + o;
        var want = byField[TARGET[o] + arm] || null;
        targets[o] = h('select', { id: id, onchange: function () { state.sig = null; } },
          h('option', { value: '' }, t('convert.noTarget')),
          cols.map(function (c) { return h('option', { value: c.id, selected: !!want && want.id === c.id }, c.label + (c.field !== c.label ? ' (' + c.field + ')' : '')); }));
        return h('div', { 'class': 'ex-fld' }, h('label', { htmlFor: id }, t('convert.out.' + o)), targets[o]);
      }));
      state.result = null;
      state.sig = null;
      MA.dom.clear(resultBox);
      errBox.hidden = true;
    }

    function showResult(r) {
      var k = kind();
      var fn = r.method && r.method['function'] ? r.method['function'] : '';
      var nodes = [
        h('p', { 'class': 'cv-res-line' },
          h('span', { 'class': 'muted' }, t('convert.result', { engine: r.engine_version || '—', fn: fn || '—' }) + ' '),
          k.outputs.map(function (o, i) {
            return [i ? ' · ' : '', t('convert.out.' + o) + ' ', h('strong', { 'class': 'num', dataset: { out: o } }, MA.ui.numText(r.outputs_text ? r.outputs_text[o] : null))];
          })),
        r.estimated
          ? h('p', { 'class': 'cv-est' }, MA.ui.badge('estimated', t('convert.estimated')), ' ', t('convert.estimatedNote'))
          : h('p', { 'class': 'cv-calc' }, MA.ui.badge('info', t('convert.calculated')), ' ', t('convert.calculatedNote'))
      ];
      (r.assumptions || []).forEach(function (a) { nodes.push(h('p', { 'class': 'cv-assume' }, t('convert.assumption') + ' ' + MA.i18n.pick(a, ''))); });
      (r.warnings || []).forEach(function (w) { nodes.push(h('p', { 'class': 'cv-warn' }, MA.ui.badge('warning', t('convert.warning')), ' ', MA.i18n.pick(w, ''))); });
      var m = r.method || {};
      if (m.citation || (m.kb_refs && m.kb_refs.length)) {
        nodes.push(h('p', { 'class': 'cv-cite' }, t('convert.source') + ' ', m.citation ? MA.i18n.pick(m.citation, '') : '',
          (m.kb_refs || []).map(function (id) { return [' · ', h('a', { href: MA.app.href('log', { kb: id }) }, 'KB ' + id + ' ↗')]; })));
      }
      MA.dom.mount(resultBox, nodes);
    }

    function compute() {
      var req = buildRequest();
      var sig = JSON.stringify(req);
      if (!Object.keys(req.inputs).length) { MA.dom.clear(resultBox); errBox.hidden = true; state.result = null; return Promise.resolve(null); }
      resultBox.setAttribute('aria-busy', 'true');
      return run(function (seq, signal) {
        return MA.api.post('/api/convert', req, { clientSeq: seq, signal: signal, toast: false });
      }).then(function (env) {
        if (!env) { return null; }
        resultBox.removeAttribute('aria-busy');
        errBox.hidden = true;
        state.result = env.data || {};
        state.request = req;
        state.sig = sig;
        showResult(state.result);
        return state.result;
      }, function (err) {
        resultBox.removeAttribute('aria-busy');
        state.result = null;
        state.sig = null;
        MA.dom.clear(resultBox);
        errBox.hidden = false;
        MA.dom.mount(errBox, MA.ui.errorBox(err));
        return null;
      });
    }
    var live = MA.ui.debounce(function () { compute(); }, LIVE_MS);

    var docSel = h('select', { id: 'cv-doc' }, h('option', { value: '' }, t('extraction.prov.noDoc')),
      (opts.documents || []).map(function (d) { return h('option', { value: d.id, selected: opts.source && opts.source.doc === d.id }, d.id + (d.title ? ' — ' + MA.i18n.pick(d.title, '') : '')); }));
    var page = h('input', { type: 'text', id: 'cv-page', inputmode: 'numeric', 'class': 'ex-in-short', value: opts.source && opts.source.page ? String(opts.source.page) : '' });
    var loc = h('input', { type: 'text', id: 'cv-locator', value: opts.source && opts.source.locator ? opts.source.locator : '' });
    var quote = h('input', { type: 'text', id: 'cv-quote', maxlength: '500', value: '' });
    function fld(id, key, ctl) { return h('div', { 'class': 'ex-fld' }, h('label', { htmlFor: id }, t(key)), ctl); }

    kindSel = h('select', { id: 'cv-kind', onchange: function () { buildForm(); } }, KINDS.map(function (k) { return h('option', { value: k.id }, t('convert.kind.' + k.id)); }));
    if (/^(m|sd)[12]$/.test(opts.activeField || '')) { kindSel.value = 'median_to_mean_sd'; }
    methodBox = h('div', { 'class': 'cv-method-box' });
    inputsBox = h('div', { 'class': 'ex-prov-grid cv-inputs', id: 'cv-inputs' });
    targetsBox = h('fieldset', { 'class': 'ex-prov-grid cv-targets', id: 'cv-targets' });
    resultBox = h('div', { 'class': 'cv-result', id: 'cv-result', 'aria-live': 'polite' });
    errBox = h('div', { 'class': 'cv-error', id: 'cv-error', hidden: true });
    applyErr = h('p', { 'class': 'ex-cf-err', role: 'alert', hidden: true });

    var form = h('form', { id: 'cv-form', onsubmit: function (ev) { ev.preventDefault(); live.cancel(); compute(); } },
      h('div', { 'class': 'row' }, h('label', { htmlFor: 'cv-kind' }, t('convert.kind')), kindSel, methodBox),
      inputsBox, targetsBox,
      h('div', { 'class': 'row' }, h('button', { type: 'submit', 'class': 'btn btn-sm', id: 'cv-compute' }, t('convert.compute'))));
    var body = [
      h('p', { 'class': 'muted' }, t('convert.intro')),
      form, resultBox, errBox,
      h('fieldset', { 'class': 'cv-prov' }, h('legend', null, t('convert.provLegend')),
        h('div', { 'class': 'ex-prov-grid' }, fld('cv-doc', 'extraction.prov.f.doc', docSel), fld('cv-page', 'extraction.prov.f.page', page),
          fld('cv-locator', 'extraction.prov.f.locator', loc), fld('cv-quote', 'extraction.prov.f.quote', quote))),
      applyErr
    ];

    function apply(close) {
      var pg = page.value.trim();
      function fail(key) { applyErr.textContent = t(key); applyErr.hidden = false; return false; }
      if (pg && !/^\d{1,6}$/.test(pg)) { return fail('extraction.prov.badPage'); }
      var req = buildRequest();
      if (!state.result || state.sig !== JSON.stringify(req)) {
        live.cancel();
        compute().then(function (r) { if (r) { apply(close); } });
        return false;
      }
      var r = state.result;
      var writes = [];
      var missing = false;
      kind().outputs.forEach(function (o) {
        var c = targets[o] && targets[o].value;
        if (!c) { return; }
        var text = cellText(r, o, opts.decimalMark);
        if (text === null) { missing = true; return; }
        writes.push({ col: c, text: text });
      });
      if (missing) { return fail('convert.noCellText'); }
      if (!writes.length) { return fail('convert.noTargets'); }
      applyErr.hidden = true;
      Promise.resolve(opts.onApply({
        rowUid: opts.rowUid, request: req, result: r, writes: writes,
        source: { doc: docSel.value || null, page: pg ? Number(pg) : null, locator: loc.value.trim() || null, quote: quote.value.trim() || null }
      })).then(function () { close('applied'); }, function () { return null; });
      return false;
    }

    var outs = KINDS[0].outputs.map(function (o) { return byField[TARGET[o] + arm] ? byField[TARGET[o] + arm].label : null; }).filter(function (x) { return !!x; });
    var m = MA.ui.modal({
      title: t('convert.title', { study: opts.study || '—', fields: outs.join(', ') || opts.activeField || '—' }),
      size: 'lg',
      body: body,
      actions: [
        { label: t('common.cancel'), kind: 'ghost' },
        { label: t('convert.apply'), kind: 'primary', onClick: apply }
      ],
      onClose: function () { live.cancel(); }
    });
    m.el.id = 'cv-modal';
    buildForm();
    return m;
  }

  MA.convertDialog = { open: open, KINDS: KINDS, cellText: cellText };

  MA.selftest.register('átváltó: cellaszöveg csak a motortól', function (tt) {
    var r = { outputs: { mean: 12.854875 }, outputs_text: { mean: { hu: '12.85', en: '12.85' } } };
    tt.eq(cellText(r, 'mean'), null, 'a kijelzési szöveg (outputs_text) nem kerül cellába');
    tt.eq(cellText({ outputs: { sd: 3.1 } }, 'sd'), null, 'nyers szám nem kerül cellába (nincs JS-formázás)');
    tt.eq(cellText({ cell_text: { sd: '3,029' }, outputs_text: { sd: { hu: '3.029', en: '3.029' } } }, 'sd'), '3,029', 'a cellába a motor cell_text-je');
  });
})();
