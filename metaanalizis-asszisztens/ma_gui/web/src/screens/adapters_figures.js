/* screens/adapters_figures.js — 4 Elemzés › Ábra-export (terv 3.5.9, 4.12, 5.2–5.3, 6.7; 2.6: csak commit-futásból).
 *
 * HTTP API (ma_gui/routes/adapters_figures.py):
 *   GET  /api/figures[?run=]   → szk.ma.figure-options/v1 {run{run_id, outcome_id, stale, kinds[{kind, available, source,
 *                                reason?}]}, runs[], renderers[{id, available, version, remedy}], formats[{id, available,
 *                                via[], reason?}], dpis[], widths[], langs[], audit{figure_forge, stdlib}, existing[]}
 *   POST /api/figures/export  ← {run_id, kind, formats[], renderer, lang, dpi, width, stem, overwrite?}
 *                              → szk.ma.figure-export/v1 {renderer{used, svg_source, ff_version}, lang{used}, formats{fmt:
 *                                {path, sha256, via}}, skipped[{format, reason}], qc{badge, numbers, ff_numbers, clean,
 *                                editable, stdlib, figure_forge, glyphs, …}}; 409 CONFLICT details.needs_overwrite
 *   POST /api/figures/audit   ← {path} → {figure_forge, stdlib, numbers}
 * Minden szám a motor kész szövege vagy darabszám a szervertől; a felület nem formáz és nem számol. A zöld jelvény
 * a szerver döntése (qc.badge). A böngészős PNG csak előnézet („ELŐNÉZET — QC NÉLKÜL” vízjellel).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };
  var FIG_DIR = '06_kezirat/abrak';
  var STEM_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
  var WIDTH_KEY = { single: 'single', '1.5': 'w15', double: 'double' };
  var LAST = null;      // a legutóbbi eredmény (csak memóriában): {run, data, warnings, audit}

  function caps() { return MA.adaptersCaps; }

  function kindLabel(k) { return MA.i18n.has('adp.fig.kind.' + k) ? t('adp.fig.kind.' + k) : String(k); }

  function radio(name, value, checked, disabled, label, id, onchange) {
    var inp = h('input', { type: 'radio', name: name, value: value, id: id, checked: !!checked, disabled: !!disabled,
      onchange: onchange });
    return h('label', { 'class': ['adp-choice', disabled && 'is-disabled'], htmlFor: id }, inp, ' ', label);
  }

  // ---------------------------------------------------------------- QC-nézet
  function line(kind, text, extra, key) {
    return h('li', { 'class': 'item', dataset: { k: key || '' } }, MA.ui.badge(kind, null), ' ', h('span', { 'class': 'item-title' }, text), extra || null);
  }

  function editabilityLines(src, out, keyPrefix) {
    if (!src || src.error) { return; }
    if (typeof src.editable === 'boolean') {
      out.push(line(src.editable ? 'ok' : 'error', src.editable
        ? t('adp.qc.editable', { n: String(src.text_elements === undefined || src.text_elements === null ? '—' : src.text_elements), outlined: String(src.outlined_text_groups || 0) })
        : t('adp.qc.notEditable', { outlined: String(src.outlined_text_groups || 0) }), null, keyPrefix + 'editable'));
    }
    if (typeof src.font_fallback_stack === 'boolean') {
      out.push(line(src.font_fallback_stack ? 'ok' : 'warning', t(src.font_fallback_stack ? 'adp.qc.fontStack' : 'adp.qc.noFontStack'), null, keyPrefix + 'fonts'));
    }
    if (typeof src.named_layers === 'number' && src.named_layers > 0) {
      out.push(line('info', t('adp.qc.layers', { n: String(src.named_layers) }), null, keyPrefix + 'layers'));
    }
    var ty = src.typography || {};
    if (typeof ty.suggestions_n === 'number' && ty.suggestions_n > 0) {
      out.push(line('info', t('adp.qc.typo', { n: String(ty.suggestions_n) }),
        h('ul', { 'class': 'adp-typo' }, (ty.suggestions || []).slice(0, 8).map(function (s) {
          return h('li', null, h('code', null, String(s.from)), ' → ', h('code', null, String(s.to)));
        })), keyPrefix + 'typo'));
    }
    if (src.glyphs && typeof src.glyphs.ok === 'boolean') {
      out.push(line(src.glyphs.ok ? 'ok' : 'warning', src.glyphs.ok ? t('adp.qc.glyphsOk') : t('adp.qc.glyphsMissing', { list: (src.glyphs.missing || []).join(' ') }), null, keyPrefix + 'glyphs'));
    }
  }

  function numbersLines(numbers, out) {
    if (!numbers || typeof numbers.checked !== 'number' || numbers.checked === 0) {
      out.push(line('neutral', t('adp.qc.numbersNone'), null, 'numbers'));
      return;
    }
    var mm = Array.isArray(numbers.mismatches) ? numbers.mismatches : [];
    out.push(line(numbers.ok ? 'ok' : 'error', t('adp.qc.numbers', { matched: String(numbers.matched), checked: String(numbers.checked) }),
      mm.length ? h('ul', { 'class': 'adp-mismatch' }, mm.slice(0, 20).map(function (m) {
        return h('li', null, t('adp.qc.mismatch', { expected: String(m.expected), field: String(m.field) }));
      })) : null, 'numbers'));
  }

  function qcView(view) {
    // view: {badge, renderer?, svg_source?, version?, lang?, numbers, ff_numbers, clean, labels_checked, residual, figure_forge, stdlib, glyphs}
    var out = [];
    numbersLines(view.numbers, out);
    if (view.ff_numbers && typeof view.ff_numbers.checked === 'number') {
      out.push(line(view.ff_numbers.ok ? 'ok' : 'error', t('adp.qc.numbersFF', { checked: String(view.ff_numbers.checked) }), null, 'ff-numbers'));
    }
    if (typeof view.clean === 'boolean') {
      out.push(line(view.clean ? 'ok' : 'warning', view.clean ? t('adp.qc.clean', { n: String(view.labels_checked === null || view.labels_checked === undefined ? '—' : view.labels_checked) })
        : t('adp.qc.residual', { n: String((view.residual || []).length) }), null, 'clean'));
    }
    if (view.glyphs && typeof view.glyphs.ok === 'boolean') {
      out.push(line(view.glyphs.ok ? 'ok' : 'warning', view.glyphs.ok ? t('adp.qc.glyphsOk') : t('adp.qc.glyphsMissing', { list: (view.glyphs.missing || []).join(' ') }), null, 'glyphs'));
    }
    var ff = view.figure_forge;
    var blocks = [h('ul', { 'class': 'item-list', id: 'fig-qc-list' }, out)];
    if (ff && !ff.error) {
      var fl = [];
      editabilityLines(ff, fl, 'ff-');
      blocks.push(h('div', { 'class': 'adp-qc-sub', id: 'fig-qc-ff' }, h('h4', null, t('adp.qc.ff', { mode: caps() ? caps().modeText(ff.mode) : String(ff.mode || '') })), h('ul', { 'class': 'item-list' }, fl)));
    } else if (ff && ff.error) {
      blocks.push(h('p', { 'class': 'adp-qc-down', id: 'fig-qc-ffdown' }, ff.error.guard ? MA.ui.badge('warning', ff.error.guard) : MA.ui.badge('warning', null), ' ',
        t('adp.qc.ffDown', { msg: pick(ff.error.message_i18n) || String(ff.error.message || ff.error.code || '') })));
    }
    if (view.stdlib) {
      var sl = [];
      if (view.stdlib.error) { sl.push(line('warning', String(view.stdlib.error), null, 'std-error')); }
      editabilityLines(view.stdlib, sl, 'std-');
      if (!ff || ff.error) { sl.push(line('neutral', t('adp.qc.glyphsUnknown'), null, 'std-glyphs')); }
      blocks.push(h('div', { 'class': 'adp-qc-sub', id: 'fig-qc-std' }, h('h4', { i18n: 'adp.qc.stdlib' }), h('ul', { 'class': 'item-list' }, sl)));
    }
    return blocks;
  }

  function badgeHead(ok) {
    return h('p', { 'class': ['adp-qc-badge', ok ? 'is-ok' : 'is-issues'], id: 'fig-qc-badge', dataset: { badge: ok ? 'ok' : 'issues' } },
      MA.ui.badge(ok ? 'ok' : 'warning', t(ok ? 'adp.qc.ok' : 'adp.qc.issues')), ' ',
      MA.why ? MA.why.button({ title: t('adp.qc.why.title'), detail: t('adp.qc.why.body') }, { id: 'fig-qc-why' }) : null);
  }

  function resultPanel(host) {
    MA.dom.clear(host);
    if (!LAST) { host.hidden = true; return; }
    host.hidden = false;
    var d = LAST.data;
    var nodes = [h('h2', { 'class': 'panel-title', i18n: 'adp.qc.title' })];
    if (LAST.audit) {
      var editable = null;
      [d.figure_forge, d.stdlib].forEach(function (s) { if (editable === null && s && !s.error && typeof s.editable === 'boolean') { editable = s.editable; } });
      var ok = !!(d.numbers && d.numbers.ok === true && editable !== false);
      nodes.push(badgeHead(ok), h('p', { 'class': 'muted' }, h('code', null, String(d.path || ''))),
        qcView({ numbers: d.numbers, figure_forge: d.figure_forge, stdlib: d.stdlib }));
    } else {
      var qc = d.qc || {};
      var r = d.renderer || {};
      nodes.push(badgeHead(qc.badge === 'ok'),
        h('p', { 'class': 'muted', id: 'fig-qc-renderer' }, t('adp.qc.renderer', {
          r: (r.used === 'figure-forge' ? t('adp.qc.r.ff', { version: r.ff_version || '' }) : t('adp.qc.r.engine')) +
            (r.svg_source && MA.i18n.has('adp.qc.src.' + (r.svg_source === 'figure-forge' ? 'ff' : r.svg_source)) ? ' (' + t('adp.qc.src.' + (r.svg_source === 'figure-forge' ? 'ff' : r.svg_source)) + ')' : ''),
          lang: d.lang && d.lang.used ? t('adp.fig.lang.' + d.lang.used) : '—' })),
        qcView({ numbers: qc.numbers, ff_numbers: qc.ff_numbers, clean: qc.clean, labels_checked: qc.labels_checked,
          residual: qc.residual_violations, figure_forge: qc.figure_forge, stdlib: qc.stdlib, glyphs: qc.glyphs }));
      var files = Object.keys(d.formats || {});
      nodes.push(h('h3', { i18n: 'adp.qc.files' }), h('ul', { 'class': 'item-list', id: 'fig-files' }, files.map(function (f) {
        var w = d.formats[f];
        return h('li', { 'class': 'item', dataset: { format: f } }, MA.ui.badge('ok', f.toUpperCase()), ' ', h('code', { 'class': 'item-title' }, String(w.path)),
          f === 'pptx' ? null : h('button', { type: 'button', 'class': 'btn btn-sm', onclick: function () { MA.api.openFile({ path: w.path }); } }, t('adp.qc.open')));
      })));
      if ((d.skipped || []).length) {
        nodes.push(h('ul', { 'class': 'item-list', id: 'fig-skipped' }, d.skipped.map(function (s) {
          return h('li', { 'class': 'item', dataset: { format: s.format } }, MA.ui.badge('neutral', null), ' ', t('adp.qc.skipped', { format: String(s.format).toUpperCase() }),
            h('span', { 'class': 'item-detail muted' }, pick(s.reason)));
        })));
      }
      nodes.push(h('p', { 'class': 'muted' }, t('adp.qc.folder', { dir: d.dir || FIG_DIR })));
    }
    if ((LAST.warnings || []).length) {
      nodes.push(h('ul', { 'class': 'item-list', id: 'fig-warnings' }, LAST.warnings.map(function (w) { return h('li', { 'class': 'item' }, MA.ui.badge('warning', null), ' ', String(w)); })));
    }
    MA.dom.mount(host, nodes);
  }

  // ---------------------------------------------------------------- előnézet (böngészős PNG, QC nélkül)
  function preview(path, host) {
    MA.dom.mount(host, MA.ui.spinner());
    MA.api.post('/api/fileurl', { path: path }, { toast: false }).then(function (env) {
      var url = env.data && env.data.url;
      var img = document.createElement('img');
      img.onload = function () {
        var w = img.naturalWidth || 800;
        var hh = img.naturalHeight || 600;
        var canvas = h('canvas', { width: String(w * 2), height: String(hh * 2) });
        var g = canvas.getContext('2d');
        g.fillStyle = '#ffffff';
        g.fillRect(0, 0, w * 2, hh * 2);
        g.drawImage(img, 0, 0, w * 2, hh * 2);
        g.fillStyle = 'rgba(200, 0, 0, 0.35)';
        g.font = 'bold 36px sans-serif';
        g.fillText(t('adp.fig.previewWatermark'), 24, 60);
        g.fillText(t('adp.fig.previewWatermark'), 24, hh * 2 - 24);
        canvas.toBlob(function (blob) {
          if (!blob) { MA.dom.mount(host, h('p', { 'class': 'muted' }, t('adp.fig.previewFailed'))); return; }
          var href = URL.createObjectURL(blob);
          MA.dom.mount(host, h('a', { 'class': 'btn btn-sm', href: href, download: 'preview_QC_NELKUL.png', id: 'fig-preview-link' }, t('adp.fig.previewReady')));
        }, 'image/png');
      };
      img.onerror = function () { MA.dom.mount(host, h('p', { 'class': 'muted', id: 'fig-preview-failed' }, t('adp.fig.previewFailed'))); };
      img.src = url;
    }, function () { MA.dom.mount(host, h('p', { 'class': 'muted', id: 'fig-preview-failed' }, t('adp.fig.previewFailed'))); });
  }

  // ---------------------------------------------------------------- űrlap
  function render(root, ctx) {
    var rid = ctx.params.run || null;
    root.appendChild(MA.ui.spinner());
    return MA.api.get('/api/figures', { query: rid ? { run: rid } : null, signal: ctx.signal }).then(function (env) {
      if (!ctx.alive()) { return; }
      var d = env.data || {};
      var runs = Array.isArray(d.runs) ? d.runs : [];
      if (!rid && runs.length) {
        var best = runs.filter(function (r) { return !r.stale && r.purpose === 'primary'; })[0] || runs[0];
        ctx.setParams({ run: best.run_id, kind: ctx.params.kind || null });
        ctx.rerender();
        return;
      }
      if (LAST && LAST.run !== rid) { LAST = null; }
      build(root, ctx, d, runs);
    });
  }

  function build(root, ctx, d, runs) {
    var run = d.run || null;
    if (!runs.length || !run) {
      MA.dom.mount(root, h('section', { 'class': 'panel', id: 'fig-empty' }, h('h2', { 'class': 'panel-title', i18n: 'adp.fig.title' }),
        h('p', { 'class': 'empty-state', i18n: 'adp.fig.noRuns' })));
      return;
    }
    var renderers = {};
    (d.renderers || []).forEach(function (r) { renderers[r.id] = r; });
    var ff = renderers['figure-forge'] || { available: false };
    var engine = renderers.engine || { available: true };
    var kinds = run.kinds || [];
    var firstKind = (kinds.filter(function (k) { return k.available; })[0] || {}).kind || 'forest';
    var S = { kind: ctx.params.kind && kinds.some(function (k) { return k.kind === ctx.params.kind && k.available; }) ? ctx.params.kind : firstKind,
      renderer: ff.available ? 'figure-forge' : 'engine', formats: { svg: true }, dpi: 600, width: 'double', lang: 'en', stem: null, stemTouched: false };
    var fmtById = {};
    (d.formats || []).forEach(function (f) { fmtById[f.id] = f; });

    function fmtAvailable(f) {
      var x = fmtById[f];
      if (!x) { return false; }
      if (f === 'svg') { return true; }
      if (S.renderer === 'figure-forge') { return (x.via || []).indexOf('figure-forge') >= 0; }
      return (x.via || []).indexOf('converter') >= 0;
    }
    function defaultStem() { return ('fig_' + S.kind + '_' + (run.outcome_id || 'x')).replace(/[^A-Za-z0-9_-]+/g, '_'); }

    var resultHost = h('section', { 'class': 'panel', id: 'fig-result', 'aria-live': 'polite', hidden: true });
    var fmtHost = h('div', { 'class': 'adp-fmts', id: 'fig-formats' });
    var stemInput = h('input', { type: 'text', id: 'fig-stem', 'class': 'input', autocomplete: 'off', value: defaultStem(),
      oninput: function () { S.stemTouched = true; checkStem(); } });
    var stemErr = h('span', { 'class': 'pf-err', role: 'alert', id: 'fig-stem-err', hidden: true }, t('adp.fig.stemBad'));
    var exportBtn = h('button', { type: 'button', 'class': 'btn btn-primary', id: 'fig-export', onclick: function () { doExport(false); } }, t('adp.fig.export'));
    var status = h('span', { 'class': 'muted', role: 'status', id: 'fig-status' });

    function checkStem() {
      var ok = STEM_RE.test(stemInput.value.trim());
      stemInput.setAttribute('aria-invalid', ok ? 'false' : 'true');
      stemErr.hidden = ok;
      exportBtn.disabled = !ok;
      return ok;
    }

    function paintFormats() {
      var shown = {};          // ugyanaz a (hosszú) teendő-szöveg csak egyszer — a többi formátumnál rövid utalás (UX-8)
      MA.dom.mount(fmtHost, ['svg', 'pdf', 'png', 'tiff', 'pptx'].filter(function (f) { return fmtById[f]; }).map(function (f) {
        var avail = fmtAvailable(f);
        if (!avail) { S.formats[f] = false; }
        var id = 'fig-fmt-' + f;
        var x = fmtById[f];
        var reason = !avail ? (x.reason ? pick(x.reason) : t('adp.fig.unavailable')) : (f === 'svg' ? t('adp.fig.svgAlways') : null);
        if (!avail && reason && shown[reason]) { reason = t('adp.fig.sameReason', { fmt: shown[reason] }); } else if (!avail && reason) { shown[reason] = f.toUpperCase(); }
        var cb = h('input', { type: 'checkbox', id: id, value: f, checked: f === 'svg' || !!S.formats[f], disabled: f === 'svg' || !avail,
          onchange: function () { S.formats[f] = cb.checked; } });
        return h('div', { 'class': ['adp-fmt', !avail && 'is-disabled'], dataset: { format: f } },
          h('label', { htmlFor: id }, cb, ' ', f.toUpperCase()), reason ? h('span', { 'class': 'pf-hint muted' }, reason) : null);
      }));
    }

    function doExport(overwrite) {
      if (!checkStem()) { stemInput.focus(); return; }
      var formats = ['svg'].concat(['pdf', 'png', 'tiff', 'pptx'].filter(function (f) { return S.formats[f] && fmtAvailable(f); }));
      var body = { run_id: run.run_id, kind: S.kind, formats: formats, renderer: S.renderer, lang: S.lang, dpi: S.dpi,
        width: S.width, stem: stemInput.value.trim(), overwrite: !!overwrite };
      exportBtn.disabled = true;
      status.textContent = t('adp.fig.exporting');
      MA.api.post('/api/figures/export', body, { signal: ctx.signal, quiet: ['CONFLICT'] }).then(function (env) {
        if (!ctx.alive()) { return; }
        exportBtn.disabled = false;
        status.textContent = '';
        LAST = { run: run.run_id, data: env.data || {}, warnings: env.warnings || [], audit: false };
        resultPanel(resultHost);
        MA.ui.toast({ kind: 'success', title: t('adp.fig.exported') });
        refreshExisting();
        var b = MA.dom.$('#fig-qc-badge');
        if (b) { b.setAttribute('tabindex', '-1'); b.focus(); }
      }, function (err) {
        if (!ctx.alive()) { return; }
        exportBtn.disabled = false;
        status.textContent = '';
        if (err && err.code === 'CONFLICT' && err.details && err.details.needs_overwrite) {
          MA.ui.confirm({ title: t('adp.fig.overwriteTitle'), message: t('adp.fig.overwriteBody', { list: (err.details.exists || []).join(', ') }),
            okLabel: t('adp.fig.overwrite'), danger: true }).then(function (ok) { if (ok) { doExport(true); } });
        }
      });
    }

    var existingHost = h('div', { id: 'fig-existing-body' });
    function paintExisting(list) {
      if (!list.length) { MA.dom.mount(existingHost, MA.ui.emptyState('adp.fig.ex.none')); return; }
      MA.dom.mount(existingHost, h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: 'fig-existing-table' },
        h('thead', null, h('tr', null, ['stem', 'kind', 'run', 'formats', 'qc'].map(function (k) { return h('th', { scope: 'col', i18n: 'adp.fig.ex.col.' + k }); }), h('th', { scope: 'col' }, ''))),
        h('tbody', null, list.map(function (e) {
          return h('tr', { dataset: { stem: e.stem } },
            h('th', { scope: 'row' }, h('code', null, String(e.stem || ''))),
            h('td', null, kindLabel(e.kind)),
            h('td', null, h('code', null, String(e.run_id || '—'))),
            h('td', null, (e.formats || []).join(', ')),
            h('td', null, MA.ui.badge(e.badge === 'ok' ? 'ok' : 'warning', t(e.badge === 'ok' ? 'adp.qc.ok' : 'adp.qc.issues')),
              e.stale ? h('span', null, ' ', MA.ui.badge('stale', t('adp.fig.ex.staleBadge'))) : null),
            h('td', null, h('button', { type: 'button', 'class': 'btn btn-sm', onclick: function () { audit(FIG_DIR + '/' + e.stem + '.svg'); } }, t('adp.fig.ex.audit'))));
        })))));
    }
    function refreshExisting() {
      MA.api.get('/api/figures', { query: { run: run.run_id }, signal: ctx.signal, toast: false }).then(function (env) {
        if (ctx.alive()) { paintExisting(((env.data || {}).existing) || []); }
      }, function () { /* a lista frissítése nem kritikus */ });
    }
    function audit(path) {
      MA.api.post('/api/figures/audit', { path: path }, { signal: ctx.signal }).then(function (env) {
        if (!ctx.alive()) { return; }
        LAST = { run: run.run_id, data: env.data || {}, warnings: env.warnings || [], audit: true };
        resultPanel(resultHost);
      });
    }

    // a választók
    var runSel = MA.proc.select({ id: 'fig-run', onchange: function () { LAST = null; ctx.navigate('figures', { run: runSel.value }); } },
      runs.map(function (r) {
        return { value: r.run_id, label: t('adp.fig.runOption', { run: r.run_id, outcome: r.outcome_id || '—', purpose: r.purpose || '—' }) +
          ' · ' + t(r.stale ? 'adp.fig.stale' : 'adp.fig.current') };
      }), run.run_id);
    var kindBox = h('fieldset', { 'class': 'adp-fieldset', id: 'fig-kinds' }, h('legend', { i18n: 'adp.fig.kind' }),
      kinds.map(function (k) {
        var r = radio('fig-kind', k.kind, k.kind === S.kind, !k.available, kindLabel(k.kind), 'fig-kind-' + k.kind, function () {
          S.kind = k.kind;
          ctx.setParams({ run: run.run_id, kind: k.kind });
          if (!S.stemTouched) { stemInput.value = defaultStem(); checkStem(); }
        });
        return h('div', { 'class': 'adp-choice-row' }, r, !k.available && k.reason ? h('span', { 'class': 'pf-hint muted' }, pick(k.reason)) : null);
      }));
    var rendBox = h('fieldset', { 'class': 'adp-fieldset', id: 'fig-renderers' }, h('legend', { i18n: 'adp.fig.renderer' }),
      h('div', { 'class': 'adp-choice-row' },
        radio('fig-renderer', 'figure-forge', S.renderer === 'figure-forge', !ff.available, t('adp.fig.r.ff', { version: ff.version || '' }), 'fig-r-ff',
          function () { S.renderer = 'figure-forge'; paintFormats(); }),
        !ff.available && ff.remedy ? h('span', { 'class': 'pf-hint muted', id: 'fig-r-ff-why' }, pick(ff.remedy)) : null),
      h('div', { 'class': 'adp-choice-row' },
        radio('fig-renderer', 'engine', S.renderer === 'engine', false, t('adp.fig.r.engine'), 'fig-r-engine', function () { S.renderer = 'engine'; paintFormats(); }),
        h('span', { 'class': 'pf-hint muted' }, engine.remedy ? pick(engine.remedy) : t('adp.fig.r.engineNote'))));
    var dpiSel = MA.proc.select({ id: 'fig-dpi', onchange: function () { S.dpi = Number(dpiSel.value); } },
      (d.dpis || [600]).map(function (x) { return { value: x, label: String(x) }; }), S.dpi);
    var widthSel = MA.proc.select({ id: 'fig-width', onchange: function () { S.width = widthSel.value; } },
      (d.widths || ['double']).map(function (x) { return { value: x, label: t('adp.fig.width.' + (WIDTH_KEY[x] || 'double')) }; }), S.width);
    var langSel = MA.proc.select({ id: 'fig-lang', onchange: function () { S.lang = langSel.value; } },
      (d.langs || ['en', 'hu']).map(function (x) { return { value: x, label: t('adp.fig.lang.' + x) }; }), S.lang);
    var previewHost = h('div', { id: 'fig-preview-out', 'aria-live': 'polite' });

    MA.dom.mount(root,
      h('section', { 'class': 'panel', id: 'fig-form', 'aria-labelledby': 'fig-form-h' },
        h('h2', { 'class': 'panel-title', id: 'fig-form-h' }, h('span', { i18n: 'adp.fig.title' }), ' ',
          MA.why ? MA.why.button({ title: t('adp.qc.why.title'), detail: t('adp.qc.why.body') }, { id: 'fig-why' }) : null),
        h('p', { 'class': 'muted', i18n: 'adp.fig.intro' }),
        MA.proc.field(t('adp.fig.run'), runSel),
        run.stale ? h('p', { 'class': 'adp-stale', id: 'fig-stale' }, MA.ui.badge('stale', t('adp.fig.stale')), ' ', t('adp.fig.staleNote')) : null,
        h('div', { 'class': 'adp-grid' }, kindBox, rendBox,
          h('fieldset', { 'class': 'adp-fieldset' }, h('legend', { i18n: 'adp.fig.formats' }), fmtHost)),
        h('div', { 'class': 'adp-row' }, MA.proc.field(t('adp.fig.dpi'), dpiSel), MA.proc.field(t('adp.fig.width'), widthSel),
          MA.proc.field(t('adp.fig.lang'), langSel), h('div', { 'class': 'pf-field' }, h('label', { htmlFor: 'fig-stem' }, t('adp.fig.stem')), stemInput, stemErr)),
        h('div', { 'class': 'toolbar' }, exportBtn, status)),
      resultHost,
      h('section', { 'class': 'panel', id: 'fig-preview', 'aria-labelledby': 'fig-preview-h' },
        h('h2', { 'class': 'panel-title', id: 'fig-preview-h', i18n: 'adp.fig.preview' }),
        h('p', { 'class': 'muted', i18n: 'adp.fig.previewNote' }),
        h('div', { 'class': 'toolbar' }, h('button', { type: 'button', 'class': 'btn btn-sm', id: 'fig-preview-btn', onclick: function () {
          var p = LAST && !LAST.audit && LAST.data.formats && LAST.data.formats.svg ? LAST.data.formats.svg.path : (LAST && LAST.audit ? LAST.data.path : null);
          if (!p) { MA.dom.mount(previewHost, h('p', { 'class': 'muted' }, t('adp.fig.previewNeedsSvg'))); return; }
          preview(p, previewHost);
        } }, t('adp.fig.r.preview')), previewHost)),
      h('section', { 'class': 'panel', id: 'fig-existing', 'aria-labelledby': 'fig-existing-h' },
        h('h2', { 'class': 'panel-title', id: 'fig-existing-h', i18n: 'adp.fig.existing' }), existingHost));
    paintFormats();
    paintExisting(d.existing || []);
    resultPanel(resultHost);
    checkStem();
  }

  MA.app.registerScreen({ id: 'figures', title_key: 'adp.nav.figures', workspace: 'analysis', tab: 'analysis', order: 30, render: render });
})();
