/* screens/results.js — 4 Elemzés · Eredmények: interaktív forest, lefúrás, KB-jelvények, további ábrák
 * (terv 3.5.7, 3.5.8).
 *
 * - Futás-választó: a kimenet commit-futásai (GET /api/runs?outcome=) AKTUÁLIS / ELAVULT (X001) jelvénnyel,
 *   valamint a legutóbbi explore-eredmény (memóriában, run_id nélkül).
 * - Összegző sáv: a motor szövegei (display_text, p_text, pi_text, i2_text, tau2_text, participants_text).
 * - ⓚ-jelvények: GET /api/kb/rules?field= (modell, PI, heterogenitás, torzítás) → KB-tétel modális ablak.
 * - Ábrák: Forest | Funnel | Doi | LOO | Befolyás | Kumulatív (fül + opcionális „mellette” ábra a kiemelés
 *   összevetéséhez); rétegek (PI, alcsoport, fix, becsült) — UI-preferencia (mag.pref.forest.layers).
 * - Lefúrás: kattintás / Enter egy vizsgálaton → kinyerési sor + eredet + PDF-oldal + „Ugrás a sorhoz”.
 * - Letöltés: a futás fájljai (motor-SVG, report.md, results.json …) aláírt URL-en (POST /api/fileurl).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var A = MA.analysis;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '—' : fb); };

  var VIEWS = ['forest', 'funnel', 'doi', 'loo', 'influence', 'cumulative'];
  var KB_FIELDS = [
    { field: 'model', key: 'results.kb.model' },
    { field: 'pi', key: 'results.kb.pi' },
    { field: 'heterogeneity', key: 'results.kb.heterogeneity' },
    { field: 'bias', key: 'results.kb.bias' }
  ];

  function available(plot, view) {
    if (!plot) { return false; }
    if (view === 'forest') { return true; }
    if (view === 'funnel') { return !!(plot.funnel && plot.funnel.points && plot.funnel.points.length); }
    if (view === 'doi') { return !!(plot.doi && plot.doi.points && plot.doi.points.length); }
    if (view === 'loo') { return Array.isArray(plot.loo) && plot.loo.length > 0; }
    if (view === 'influence') { return Array.isArray(plot.influence) && plot.influence.length > 0; }
    if (view === 'cumulative') { return !!(plot.cumulative && plot.cumulative.entries && plot.cumulative.entries.length); }
    return false;
  }

  function primaryOf(plot) {
    var s = plot.summaries || [];
    return s.filter(function (x) { return x.primary === true; })[0] || s.filter(function (x) { return x.kind === 'overall'; })[0] || null;
  }

  function render(root, ctx) {
    var outcome = A.pickOutcome(ctx.params.outcome);
    if (!outcome) {
      // nincs kimenet: a felvétele itt, a munkapadon (DOC-1) — utána a képernyő újraépül
      root.appendChild(A.noOutcomesPanel(function (id) { ctx.navigate('results', { outcome: id }); }));
      return null;
    }
    ctx.setTitle(pick(outcome.name, outcome.id));
    var busy = h('div', { 'class': 'panel', 'aria-busy': 'true' }, MA.ui.spinner('results.loading'));
    root.appendChild(busy);
    var explore = MA.store.get('analysis.explore.' + outcome.id);
    var exploreData = explore && explore.run && explore.run.data ? explore.run.data : null;
    // a feltárás (explore) eredménye a tábla akkori állapotát írja le: ha a tábla azóta változott, elavult (FID-5)
    var shaP = exploreData && exploreData.sha256
      ? A.currentDataSha(exploreData.path || outcome.data, ctx.signal) : Promise.resolve(null);
    return Promise.all([A.loadRuns(outcome.id, ctx.signal), shaP]).then(function (res) {
      if (!ctx.alive()) { return null; }
      var runs = res[0];
      var choices = runs.slice();
      if (explore && explore.run && explore.plot) {
        choices.unshift(Object.assign({}, explore.run, { run_id: null, _explore: true,
          _dataStale: !!(res[1] && exploreData.sha256 !== res[1]) }));
      }
      if (!choices.length) {
        MA.dom.mount(busy, MA.ui.emptyState('results.noRuns'), ' ',
          h('a', { 'class': 'btn btn-sm', href: ctx.href('analysis', { outcome: outcome.id }) }, t('results.toPlan')));
        busy.removeAttribute('aria-busy');
        return null;
      }
      var want = ctx.params.run;
      var sel = null;
      if (want === 'explore') { sel = choices.filter(function (r) { return r._explore; })[0] || null; } else if (want) { sel = choices.filter(function (r) { return r.run_id === want; })[0] || null; }
      if (!sel) {
        sel = runs.filter(function (r) { return r.stale !== true && r.spec && !r.spec.parent; })[0] || runs[0] || choices[0];
      }
      var plotP = sel._explore ? Promise.resolve(explore.plot) : A.loadPlot(sel.run_id, ctx.signal);
      return plotP.then(function (plot) {
        if (!ctx.alive()) { return; }
        root.removeChild(busy);
        build(root, ctx, outcome, choices, sel, plot);
      });
    });
  }

  function build(root, ctx, outcome, choices, run, plot) {
    var els = {};
    var params = Object.assign({}, ctx.params, { outcome: outcome.id });
    var view = VIEWS.indexOf(params.view) >= 0 ? params.view : MA.prefs.get('results.view', 'forest');
    if (VIEWS.indexOf(view) < 0 || !available(plot, view)) { view = 'forest'; }
    var side = MA.prefs.get('results.side', '');
    if (VIEWS.indexOf(side) < 0 || side === view || !available(plot, side)) { side = ''; }
    var layers = MA.plots.forest.layersPref();
    var mounted = {};

    function setParam(k, v) {
      params = Object.assign({}, params);
      if (v === null || v === undefined || v === '') { delete params[k]; } else { params[k] = v; }
      ctx.setParams(params);
    }

    // ------------------------------------------------ futás-választó
    var runSel = h('select', { id: 'results-run', onchange: function () {
      var v = runSel.value;
      ctx.navigate('results', { outcome: outcome.id, run: v === '' ? 'explore' : v, view: view });
    } }, choices.map(function (r) {
      return h('option', { value: r.run_id || '' }, A.runLabel(r));
    }));
    runSel.value = run.run_id || '';
    var sp = run.spec || {};
    var files = run.files && typeof run.files === 'object' ? Object.keys(run.files) : [];
    var head = h('section', { 'class': 'panel res-head', 'aria-label': t('results.head.aria') },
      h('div', { 'class': 'toolbar' },
        h('label', { 'for': 'results-run' }, t('results.run')), runSel,
        h('span', { id: 'results-run-badge' }, A.runBadge(run)),
        h('span', { 'class': 'muted res-run-meta' },
          run.run_id ? h('code', null, run.run_id) : null,
          sp.name ? ' · ' + sp.name : '',
          sp.parent ? ' (' + t('plan.parent', { name: sp.parent }) + ')' : '',
          run.data && run.data.sha256 ? [' · ' + t('results.data') + ' ', h('code', { title: run.data.sha256 }, A.shortSha(run.data.sha256))] : null),
        h('span', { 'class': 'hdr-spacer' }),
        h('a', { 'class': 'btn btn-sm btn-ghost', href: ctx.href('analysis', { outcome: outcome.id, spec: sp.name || null }) }, t('results.toPlan'))),
      run.stale === true ? h('p', { 'class': 'res-stale', role: 'note' }, MA.ui.badge('stale', 'X001'), ' ', t('results.staleNote')) : null,
      run.stale === null && run.run_id ? h('p', { 'class': 'muted', role: 'note' }, MA.ui.badge('neutral', t('analysis.run.unknown')), ' ', t('analysis.run.unknownTitle')) : null,
      run._dataStale ? h('p', { 'class': 'res-stale', id: 'results-explore-stale', role: 'note' }, MA.ui.badge('stale', t('plan.result.dataChanged')), ' ',
        t('results.exploreStale'), ' ', h('a', { href: ctx.href('analysis', { outcome: outcome.id }) }, t('results.toPlan'))) : null,
      run._explore ? h('p', { 'class': 'muted', role: 'note' }, t('results.exploreNote')) : null,
      h('div', { 'class': 'res-downloads', id: 'results-downloads' },
        h('span', { 'class': 'muted' }, t('results.downloads') + ' '),
        files.length ? files.map(function (k) {
          var f = run.files[k];
          var name = f && f.path ? String(f.path).split('/').pop() : k;
          return h('button', { type: 'button', 'class': 'btn btn-sm', 'data-file': k, title: f && f.path ? f.path : k,
            onclick: function () { A.download(f.path).catch(function () { /* toast */ }); } },
          (MA.i18n.has('results.file.' + k) ? t('results.file.' + k) : name), ' ↓');
        }) : h('span', { 'class': 'muted' }, t('results.noFiles'))));
    root.appendChild(head);

    // ------------------------------------------------ összegző sáv + ⓚ
    var prim = primaryOf(plot) || {};
    var het = plot.heterogeneity || {};
    var kbCtx = A.kbContext(run, plot);
    var bits = [
      h('span', { 'class': 'res-effect' }, h('strong', null, (plot.measure || '') + ' '), MA.ui.num(prim.display_text, { id: 'results-effect' }))
    ];
    if (prim.p_text) { bits.push(h('span', null, MA.ui.num(prim.p_text))); }
    if (prim.pi_text) { bits.push(h('span', null, 'PI ', MA.ui.num(prim.pi_text))); }
    if (het.i2_text) { bits.push(h('span', null, 'I² ', MA.ui.num(het.i2_text))); }
    if (het.tau2_text) { bits.push(h('span', null, 'τ² ', MA.ui.num(het.tau2_text))); }
    bits.push(h('span', null, 'k ', h('span', { 'class': 'num' }, typeof run.k === 'number' ? String(run.k) : '—')));
    if (run.participants_text) { bits.push(h('span', null, 'N ', MA.ui.num(run.participants_text))); }
    var line = [];
    bits.forEach(function (b, i) { if (i) { line.push(h('span', { 'class': 'res-sep', 'aria-hidden': 'true' }, ' · ')); } line.push(b); });
    var summary = h('section', { 'class': 'panel res-summary', id: 'results-summary', 'aria-label': t('results.summary.aria') },
      h('p', { 'class': 'res-line' }, line),
      h('p', { 'class': 'res-kb' }, KB_FIELDS.map(function (f, i) {
        var label = f.field === 'model' ? h('span', null, pick(prim.label, t(f.key))) : h('span', null, t(f.key));
        return [i ? h('span', { 'class': 'res-sep', 'aria-hidden': 'true' }, ' · ') : null, A.kbRow(label, f.field, ctx.signal, kbCtx)];
      })),
      het.text ? h('p', { 'class': 'muted res-het' }, pick(het.text, '')) : null,
      Array.isArray(plot.notes) && plot.notes.length ? h('ul', { 'class': 'res-notes' }, plot.notes.map(function (n) { return h('li', null, pick(n, '')); })) : null);
    root.appendChild(summary);

    // ------------------------------------------------ ábrák
    var tabsList = VIEWS.filter(function (v) { return available(plot, v); });
    var panelId = 'results-plot-panel';
    var tabs = MA.ui.tabs({ label: t('results.views'), panelId: panelId, active: view,
      tabs: tabsList.map(function (v) { return { id: v, label: t('results.view.' + v) }; }),
      onSelect: function (v) { selectView(v); } });
    tabs.id = 'results-tabs';
    var layerBoxes = h('span', { 'class': 'res-layers', id: 'results-layers', role: 'group', 'aria-label': t('results.layers') },
      h('span', { 'class': 'muted' }, t('results.layers') + ': '),
      MA.plots.forest.LAYERS.map(function (k) {
        var id = 'layer-' + k;
        return h('label', { 'class': 'res-layer', 'for': id },
          h('input', { type: 'checkbox', id: id, checked: !!layers[k], onchange: function (ev) {
            layers[k] = ev.target.checked;
            MA.plots.forest.saveLayers(layers);
            drawMain();
            if (side === 'forest') { drawSide(); }
          } }), ' ', t('results.layer.' + k));
      }));
    var sideSel = h('select', { id: 'results-side', onchange: function () {
      side = sideSel.value;
      MA.prefs.set('results.side', side || null);
      drawSide();
    } }, [h('option', { value: '' }, t('results.side.none'))].concat(tabsList.map(function (v) { return h('option', { value: v }, t('results.view.' + v)); })));
    sideSel.value = side;
    els.main = h('div', { 'class': 'res-plot', id: panelId, role: 'tabpanel', tabindex: '-1' });
    els.side = h('div', { 'class': 'res-plot res-side', id: 'results-side-plot', hidden: true });
    var plotsPanel = h('section', { 'class': 'panel res-plots', 'aria-label': t('results.views') },
      h('div', { 'class': 'toolbar res-toolbar' }, tabs, h('span', { 'class': 'hdr-spacer' }), layerBoxes,
        h('label', { 'for': 'results-side', 'class': 'res-side-label' }, t('results.side')), sideSel),
      h('p', { 'class': 'muted res-hint' }, t('results.hint')),
      h('div', { 'class': 'res-plot-grid', id: 'results-grid' }, els.main, els.side));
    root.appendChild(plotsPanel);
    els.drill = h('div', { 'class': 'res-drill', id: 'results-drill' });
    root.appendChild(els.drill);

    function onDrill(uid) { MA.plots.common.select(uid); }

    function draw(host, v) {
      var opts = { signal: ctx.signal, onDrill: onDrill };
      if (v === 'forest') { return MA.plots.forest.render(host, plot, Object.assign({ layers: layers }, opts)); }
      if (v === 'funnel') { return MA.plots.funnel.render(host, plot, opts); }
      if (v === 'doi') { return MA.plots.doi.render(host, plot, opts); }
      if (v === 'loo') { return MA.plots.series.render(host, plot, Object.assign({ kind: 'loo' }, opts)); }
      if (v === 'cumulative') { return MA.plots.series.render(host, plot, Object.assign({ kind: 'cumulative' }, opts)); }
      if (v === 'influence') { return MA.plots.influence.render(host, plot, opts); }
      return null;
    }

    function drawMain() {
      els.main.setAttribute('aria-labelledby', '');
      var btn = MA.dom.$('[role="tab"][data-tab="' + view + '"]', tabs);
      if (btn) { btn.id = btn.id || MA.dom.uid('res-tab'); els.main.setAttribute('aria-labelledby', btn.id); }
      layerBoxes.hidden = view !== 'forest';
      mounted.main = draw(els.main, view);
    }

    function drawSide() {
      if (!side || side === view) {
        els.side.hidden = true;
        MA.dom.clear(els.side);
        mounted.side = null;
        MA.dom.$('#results-grid').classList.remove('is-split');
        return;
      }
      els.side.hidden = false;
      MA.dom.$('#results-grid').classList.add('is-split');
      mounted.side = draw(els.side, side);
    }

    function selectView(v) {
      view = v;
      MA.prefs.set('results.view', v);
      setParam('view', v);
      if (side === v) { side = ''; sideSel.value = ''; MA.prefs.set('results.side', null); }
      drawMain();
      drawSide();
    }

    // ------------------------------------------------ lefúrás
    function showDrill(uid) {
      if (!uid) {
        MA.dom.clear(els.drill);
        setParam('row', null);
        return;
      }
      setParam('row', uid);
      A.drilldown(els.drill, { plot: plot, run: run, outcome: outcome, uid: uid, signal: ctx.signal,
        onClose: function () {
          MA.plots.common.select(null);
          var m = mounted.main;
          if (m && m.focus) { m.focus(uid); }
        } });
    }
    var offDrill = MA.bus.on('drilldown', function (ev) { if (ctx.alive()) { showDrill(ev && ev.row_uid); } });
    ctx.onCleanup(offDrill);
    ctx.onCleanup(function () { MA.store.set('analysis.highlight', null); });

    drawMain();
    drawSide();
    var initial = params.row && MA.plots.common.studyIndex(plot)[params.row] ? params.row : null;
    MA.store.set('analysis.selected', initial);
    if (initial) {
      showDrill(initial);
    }
  }

  MA.app.registerScreen({
    id: 'results',
    title_key: 'results.title',
    workspace: 'analysis',
    tab: 'analysis',
    order: 20,
    render: render
  });
})();
