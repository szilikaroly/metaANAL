/* screens/analysis_tests.js — az elemzés-képernyők és ábrák oldalon belüli öntesztje (terv 8.6, 1. réteg).
 * A nézetmodell az első kimenet első futásáé (fejlesztői módban a BCG-példa); ha nincs futás, a tesztek kihagyják.
 * Ellenőrzi: jelölő-pozíció ±0,5 px a motor tartományából, tengelyfeliratok = ticks[].text, kontúr-poligonok,
 * billentyűzetes bejárás és lefúrás, a címke mindig szövegként kerül a DOM-ba, parancs-idézőjelezés, és hogy
 * az űrlap minden mezője a motor opció-metaadatából jön. */
(function () {
  'use strict';
  var MA = window.MA;
  var A = MA.analysis;
  var P = MA.plots;

  function firstPlot() {
    var o = A.pickOutcome(null);
    if (!o) { return Promise.resolve(null); }
    return A.loadRuns(o.id).then(function (runs) {
      var r = runs.filter(function (x) { return x.run_id && !x.stale; })[0] || runs[0];
      return r ? A.loadPlot(r.run_id) : null;
    }).catch(function () { return null; });
  }

  function expectX(axis, px0, px1, v) { return px0 + (v - axis.domain[0]) / (axis.domain[1] - axis.domain[0]) * (px1 - px0); }

  function host() {
    var d = MA.dom.h('div', { 'class': 'sr-only', 'aria-hidden': 'true' });
    document.body.appendChild(d);
    return d;
  }

  MA.selftest.register('elemzés: forest — jelölő-pozíció, tengely, szakaszok, levágás', function (t) {
    return firstPlot().then(function (plot) {
      if (!plot) { t.ok(true, 'nincs futás — kihagyva'); return; }
      var el = MA.dom.h('div');
      var r = P.forest.render(el, plot, { layers: { pi: true, subgroups: true, fixed: false, estimated: true } });
      var L = r.layout;
      var groups = MA.dom.$$('g.fp-study', el);
      t.eq(groups.length, plot.studies.length, 'minden vizsgálat egy <g data-uid>');
      groups.forEach(function (g) {
        var y = Number(g.getAttribute('data-y'));
        var sq = g.querySelector('.fp-square');
        if (!sq) { return; }
        var cx = Number(sq.getAttribute('x')) + Number(sq.getAttribute('width')) / 2;
        t.near(cx, expectX(plot.axis, L.px0, L.px1, y), 0.5, 'négyzet-közép ±0,5 px: ' + g.getAttribute('data-uid'));
      });
      var labels = MA.dom.$$('.fp-axis .pl-tick-label', el).map(function (x) { return x.textContent; });
      var want = plot.axis.ticks.filter(function (tk) { return tk.at >= plot.axis.domain[0] && tk.at <= plot.axis.domain[1]; })
        .map(function (tk) { return MA.i18n.pick(tk.text, ''); });
      t.deepEq(labels, want, 'tengelyfeliratok = a motor ticks[].text');
      var eff = MA.dom.$$('g.fp-study .fp-effect', el).map(function (x) { return x.textContent; });
      t.ok(plot.studies.every(function (s) { return eff.indexOf(MA.i18n.pick(s.display_text)) >= 0; }), 'a display_text szó szerint');
      t.eq(MA.dom.$$('.fp-section', el).length, (plot.sections || []).length, 'alcsoport-szakaszok');
      var clipped = (plot.summaries || []).concat((plot.sections || []).map(function (s) { return s.summary; })).filter(function (s) {
        return s && (s.ci_lower < plot.axis.domain[0] || s.ci_upper > plot.axis.domain[1]);
      }).length + plot.studies.filter(function (s) { return s.lo < plot.axis.domain[0] || s.hi > plot.axis.domain[1]; }).length;
      t.ok(clipped === 0 || MA.dom.$$('.fp-arrow', el).length > 0, 'levágott CI → nyíl');
      t.ok(MA.dom.$$('.fp-diamond', el).length >= 1, 'gyémánt(ok)');
    });
  });

  MA.selftest.register('elemzés: funnel, Doi, LOO, befolyás — a motor poligonjai és pontjai', function (t) {
    return firstPlot().then(function (plot) {
      if (!plot) { t.ok(true, 'nincs futás — kihagyva'); return; }
      var el = MA.dom.h('div');
      if (plot.funnel) {
        var f = P.funnel.render(el, plot, {});
        t.eq(MA.dom.$$('.fn-contour', el).length, (plot.funnel.contours || []).length, 'kontúr-poligonok a motorból');
        t.eq(MA.dom.$$('.fn-pt', el).length, plot.funnel.points.length, 'funnel-pontok');
        t.eq(MA.dom.$$('path.fn-filled', el).length, (plot.funnel.filled || []).length, 'pótolt pont eltérő alakkal');
        var p0 = plot.funnel.points[0];
        var c0 = MA.dom.$('.fn-pt circle', el);
        t.near(Number(c0.getAttribute('cx')), f.layout.sx(p0.x), 0.5, 'funnel x ±0,5 px');
        t.near(Number(c0.getAttribute('cy')), f.layout.sy(p0.se), 0.5, 'funnel y ±0,5 px (SE fordítva)');
      }
      if (plot.doi) {
        P.doi.render(el, plot, {});
        t.eq(MA.dom.$$('.doi-pt', el).length, plot.doi.points.length, 'Doi-pontok');
        t.eq(MA.dom.$('.doi-lfk', el).textContent, MA.i18n.pick(plot.doi.lfk_text), 'LFK a motor szövegével');
      }
      if (plot.loo && plot.loo.length) {
        P.series.render(el, plot, { kind: 'loo' });
        t.eq(MA.dom.$$('.sr-row', el).length, plot.loo.length, 'LOO-sorok');
      }
      if (plot.influence && plot.influence.length) {
        P.influence.render(el, plot, {});
        t.eq(MA.dom.$$('.inf-table tbody tr', el).length, plot.influence.length, 'befolyás-táblázat');
        t.eq(MA.dom.$$('.inf-mark.is-flag', el).length, 3 * plot.influence.filter(function (e) { return e.influential; }).length, 'befolyásos jelzés alakkal');
      }
    });
  });

  MA.selftest.register('elemzés: billentyűzet (↓, End, Enter) és lefúrás; címke szövegként', function (t) {
    return firstPlot().then(function (plot) {
      if (!plot) { t.ok(true, 'nincs futás — kihagyva'); return; }
      var p2 = JSON.parse(JSON.stringify(plot));
      p2.studies[0].label = '<img src=x onerror=alert(1)>';
      var el = host();
      var drilled = [];
      var r = P.forest.render(el, p2, { onDrill: function (u) { drilled.push(u); } });
      t.eq(el.querySelector('img'), null, 'nem jött létre <img>');
      t.ok(el.textContent.indexOf('<img src=x') >= 0, 'a címke szövegként jelenik meg');
      var items = r.items;
      items[0].focus();
      items[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
      t.eq(document.activeElement, items[1], '↓ a következő vizsgálatra');
      items[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
      t.eq(document.activeElement, items[items.length - 1], 'End az utolsóra');
      items[items.length - 1].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      t.deepEq(drilled, [items[items.length - 1].getAttribute('data-uid')], 'Enter → lefúrás a helyes row_uid-ra');
      items[items.length - 1].blur();
      el.parentNode.removeChild(el);
      MA.store.set('analysis.highlight', null);
    });
  });

  MA.selftest.register('elemzés: parancs-előnézet idézőjelezése', function (t) {
    t.eq(A.cmd(['ma.py', 'analyze', '--subgroup', 'allokáció', '--title', 'a "b"']), 'python ma.py analyze --subgroup allokáció --title "a \\"b\\""', 'argv → parancs');
    t.eq(A.cmd([]), '', 'üres argv');
  });

  MA.selftest.register('elemzés: az űrlap mezői a motor opció-metaadatából', function (t) {
    var meta = A.engineOptions();
    var names = Object.keys(meta);
    if (!names.length || !A.pickOutcome(null)) { t.ok(true, 'nincs motor-metaadat — kihagyva'); return null; }
    var start = MA.app.current();
    MA.app.navigate('analysis');
    return MA.selftest.waitFor(function () { return !!document.getElementById('plan-form'); }, 5000).then(function (ok) {
      t.ok(ok, 'az Elemzési terv betöltött');
      var fields = MA.dom.$$('#plan-form [data-opt]').map(function (x) { return x.getAttribute('data-opt'); });
      t.deepEq(fields.slice().sort(), names.slice().sort(), 'mezők = engine.options kulcsai (CLI-s és csak-spec csoportban)');
      names.forEach(function (n) {
        var m = meta[n];
        if (m.type !== 'enum' || !Array.isArray(m.choices)) { return; }
        var opts = MA.dom.$$('#opt-' + n + ' option').map(function (o) { return o.value; }).filter(function (v) { return v !== ''; });
        t.deepEq(opts.slice(0, m.choices.length), m.choices.map(String), 'választható értékek: ' + n);
      });
      if (start) { MA.app.navigate(start.id, start.params); }
      return MA.selftest.waitFor(function () { var c = MA.app.current(); return !start || (c && c.id === start.id); });
    });
  });
})();
