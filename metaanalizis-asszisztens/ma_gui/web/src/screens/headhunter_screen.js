/* screens/headhunter_screen.js — Metaheadhunter 7–8. lépés: szűrés a saját PICO szerint (EP4) és frissítő keresés.
 *
 * 7. Szűrés: GET /api/headhunter/merged → vizsgálatonként: eredet (mely áttekintések vonták be, milyen címkével, mely
 *    bizonyíték alapján), közlemények, másodlagos adatok (ellenőrizetlen!), ellentmondások. Döntés a vizsgálatra
 *    (POST /api/headhunter/decide {target: st-…, value, level, reason_code, reason}, a studies.json ETag-jével);
 *    teljes szöveg szintű kizárásnál az okkód kötelező (PRISMA 16a). Billentyűk a listán: ↑/↓ (j/k) mozgás,
 *    I bevon, X kizár (teljes szöveg, okkal), T kizár cím/absztrakt alapján, N nem szerezhető be, W elbírálásra vár.
 *    Az, hogy egy korábbi áttekintés bevonta, csak KONTEXTUS — a jogosultságot a saját PICO dönti el.
 * 8. Frissítés: ablak (horgony, átfedés, áttekintésenkénti keresési dátum bizonyítékkal), előnézet (dry-run: a pontos
 *    lekérdezések), futtatás (az ablak jóváhagyása emberi döntésként rögzül), eredmények (GET /api/headhunter/update).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var B = MA.ui.badge;
  var F = MA.proc.field;
  var SEL = MA.proc.select;
  var EMPTY = MA.ui.emptyState;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '' : fb); };
  var hh = MA.hh;
  var ST_KIND = { included: 'ok', excluded: 'warning', awaiting: 'pending', pending: 'neutral' };
  var screenFilter = null;  // null: alapértelmezés — „döntésre vár”, ha van ilyen; különben „mind” (UX-4)
  var selected = null;
  var local = {};           // study_id → a most rögzített döntés (amíg az egyesítés újra nem fut)
  var plan = null;          // az utolsó dry-run terve (csak memóriában)

  // ---------------------------------------------------------------- 7. lépés
  function provenance(st, meta) {
    return h('ul', { 'class': 'hh-prov' }, (st.provenance || []).map(function (p) {
      var m = meta[p.review_id] || {};
      return h('li', null, h('strong', null, m.label || p.review_id), ': ', p.label_in_review || '—',
        p.confidence ? [' ', hh.api.confBadge(p.confidence)] : null,
        h('span', { 'class': 'muted hh-small' }, ' (' + (p.evidence_ids || []).join(', ') + ')'));
    }));
  }

  function detail(st, meta) {
    var sec = st.secondary_data || [];
    return h('div', { 'class': 'hh-sc-detail', id: 'hh-sc-detail', 'aria-live': 'polite' },
      h('h3', null, st.label, ' ', B(ST_KIND[st.status] || 'neutral', t('hh.sc.status.' + st.status))),
      (st.found_by || []).length ? h('p', null, t('hh.sc.foundBy'), ' ', st.found_by.map(function (f) { return B('neutral', t('hh.sc.found.' + f)); })) : null,
      h('h4', { i18n: 'hh.sc.provTitle' }), provenance(st, meta),
      h('p', { 'class': 'muted hh-small' }, t('hh.sc.provNote')),
      h('h4', { i18n: 'hh.sc.reports' }),
      h('ul', { 'class': 'hh-reports' }, (st.reports || []).map(function (r) {
        var ss = r.screening_status || {};
        return h('li', null, B('neutral', t('hh.sc.role', { role: r.role || '—' })), ' ', r.citation || r.rec_id, ' ', hh.api.idList(r.ids),
          h('div', { 'class': 'muted hh-small' }, t('hh.sc.screening', { ta: ss.title_abstract || '—', ft: ss.full_text || '—' }),
            r.branch ? ' · ' + t('hh.sc.branch.' + r.branch) : ''));
      })),
      sec.length ? [h('h4', { i18n: 'hh.sc.secondary' }), h('p', null, B('estimated', t('hh.sc.secondaryBadge')), ' ', t('hh.sc.secondaryNote')),
        h('ul', null, sec.map(function (g) {
          return h('li', null, h('strong', null, ((meta[g.review_id] || {}).label || g.review_id) + ': '), (g.values || []).map(function (v) {
            return h('span', { 'class': 'hh-sv' }, v.field + (v.outcome ? ' (' + v.outcome + ')' : '') + ' = ' + (v.value === null || v.value === undefined ? '—' : String(v.value)), ' ',
              B(v.status === 'verified' ? 'ok' : 'estimated', t('hh.sv.' + (v.status || 'unverified'))), ' ');
          }));
        }))] : null,
      (st.conflicts || []).length ? h('p', null, B('warning', 'H018'), ' ', t('hh.sc.conflicts', { n: String(st.conflicts.length) }),
        h('ul', null, st.conflicts.map(function (c) {
          return h('li', null, c.field + ': ', (c.values || []).map(function (v) { return ((meta[v.review_id] || {}).label || v.review_id) + ' = ' + String(v.value); }).join(' ≠ '));
        }))) : null,
      (st.flags || []).length ? h('p', null, st.flags.map(function (f) { return B('warning', f); })) : null);
  }

  function stepScreen(host, api) {
    return api.get('/merged').then(function (env) {
      var d = env.data || {};
      var meta = d.review_meta || {};
      var reasons = d.exclusion_reasons || [];
      var studiesEtag = api.etag('studies');
      if (!d.exists) {
        MA.dom.mount(host, EMPTY('hh.sc.noMerged'),
          h('button', { type: 'button', 'class': 'btn btn-primary', id: 'hh-sc-merge', onclick: function () { api.run('merge', {}); } }, t('hh.sc.buildList')));
        return null;
      }
      var all = d.studies || [];
      var counts = d.status_counts || {};
      var filter = screenFilter || ((counts.pending || 0) ? 'pending' : 'all');
      var listEl = h('div', { 'class': 'hh-sc-list', role: 'listbox', id: 'hh-sc-list', tabindex: '0', 'aria-label': t('hh.sc.listLabel'),
        'aria-describedby': 'hh-sc-keys' });
      var detailHost = h('div', { 'class': 'hh-sc-detail-host' });
      var rows = [];

      function view() { return all.filter(function (s) { return filter === 'all' || s.status === filter; }); }

      function decideStudy(st, value, level, needCode) {
        var p = (value === 'include') ? Promise.resolve({ reason: null, reason_code: null }) : api.reasonDialog({
          title: t('hh.sc.decideTitle.' + (level === 'title_abstract' ? 'ta' : value), { label: st.label }),
          intro: t('hh.sc.decideIntro'), codes: reasons, needCode: needCode, needReason: false,
          okLabel: t('hh.sc.act.' + (level === 'title_abstract' ? 'excludeTa' : value)), danger: value === 'exclude' });
        return p.then(function (res) {
          if (!res) { return null; }
          var body = { kind: 'decide', target: st.study_id, value: value, reason: res.reason, reason_code: res.reason_code };
          if (level) { body.level = level; }
          return MA.api.post('/api/headhunter/decide', body, { ifMatch: studiesEtag }).then(function (r) {
            local[st.study_id] = value + (level === 'title_abstract' ? ' (TA)' : '');
            if (r.data && r.data.etag) { studiesEtag = r.data.etag; }
            MA.ui.toast({ kind: 'success', title: t('hh.decided'), message: pick(r.data && r.data.message) });
            paintList(true);
            move(1);
            return r;
          });
        });
      }

      function act(key) {
        var st = view().filter(function (x) { return x.study_id === selected; })[0];
        if (!st) { return false; }
        if (key === 'i') { decideStudy(st, 'include', 'both', false); } else if (key === 'x') { decideStudy(st, 'exclude', 'full_text', true); } else if (key === 't') { decideStudy(st, 'exclude', 'title_abstract', false); } else if (key === 'n') { decideStudy(st, 'not_retrieved', 'full_text', false); } else if (key === 'w') { decideStudy(st, 'awaiting', 'full_text', false); } else { return false; }
        return true;
      }

      function move(delta) {
        var v = view();
        if (!v.length) { return; }
        var i = v.map(function (x) { return x.study_id; }).indexOf(selected);
        i = i < 0 ? 0 : i + delta;
        if (i < 0) { i = 0; }
        if (i >= v.length) { i = v.length - 1; }
        select(v[i].study_id);
      }

      function select(id) {
        selected = id;
        rows.forEach(function (r) {
          var on = r.dataset.study === id;
          r.setAttribute('aria-selected', on ? 'true' : 'false');
          r.classList.toggle('is-selected', on);
          if (on) { listEl.setAttribute('aria-activedescendant', r.id); if (r.scrollIntoView) { r.scrollIntoView({ block: 'nearest' }); } }
        });
        var st = all.filter(function (x) { return x.study_id === id; })[0];
        MA.dom.mount(detailHost, st ? [detail(st, meta), actions(st)] : null);
      }

      function actions(st) {
        return h('div', { 'class': 'toolbar hh-sc-actions' },
          ['include', 'exclude', 'excludeTa', 'not_retrieved', 'awaiting'].map(function (k) {
            return h('button', { type: 'button', 'class': ['btn', 'btn-sm', k === 'include' && 'btn-primary'], dataset: { act: k }, onclick: function () {
              if (k === 'include') { decideStudy(st, 'include', 'both', false); } else if (k === 'exclude') { decideStudy(st, 'exclude', 'full_text', true); } else if (k === 'excludeTa') { decideStudy(st, 'exclude', 'title_abstract', false); } else { decideStudy(st, k, 'full_text', false); }
            } }, t('hh.sc.act.' + k));
          }));
      }

      function paintList(keepFocus) {
        var v = view();
        rows = v.map(function (st) {
          var mark = local[st.study_id];
          return h('div', { 'class': ['hh-sc-row', 'is-' + st.status], role: 'option', id: 'hh-sc-' + st.study_id, dataset: { study: st.study_id }, 'aria-selected': 'false',
            onclick: function () { select(st.study_id); listEl.focus(); } },
            h('span', { 'class': 'hh-sc-label' }, st.label),
            B(ST_KIND[st.status] || 'neutral', t('hh.sc.status.' + st.status)),
            h('span', { 'class': 'muted hh-small' }, t('hh.sc.nReviews', { n: String(st.n_reviews || (st.provenance || []).length) })),
            (st.conflicts || []).length ? B('warning', 'H018') : null,
            mark ? B('ok', t('hh.sc.recorded', { v: mark })) : null);
        });
        MA.dom.mount(listEl, rows.length ? rows : EMPTY('hh.sc.emptyFilter'));
        if (!v.some(function (x) { return x.study_id === selected; })) { selected = v.length ? v[0].study_id : null; }
        if (selected) { select(selected); } else { MA.dom.clear(detailHost); }
        if (keepFocus) { listEl.focus(); }
      }

      listEl.addEventListener('keydown', function (ev) {
        if (ev.altKey || ev.ctrlKey || ev.metaKey) { return; }
        var k = ev.key;
        if (k === 'ArrowDown' || k === 'j') { ev.preventDefault(); move(1); } else if (k === 'ArrowUp' || k === 'k') { ev.preventDefault(); move(-1); } else if (k === 'Home') { ev.preventDefault(); move(-100000); } else if (k === 'End') { ev.preventDefault(); move(100000); } else if (act(k.toLowerCase())) { ev.preventDefault(); }
      });

      var segWrap = h('div', { 'class': 'seg', role: 'group', 'aria-label': t('hh.sc.filterLabel') }, ['pending', 'included', 'excluded', 'awaiting', 'all'].map(function (k) {
        return h('button', { type: 'button', 'class': 'seg-btn', 'aria-pressed': filter === k ? 'true' : 'false', dataset: { filter: k }, onclick: function () {
          screenFilter = filter = k;
          MA.dom.$$('.seg-btn', segWrap).forEach(function (b) { b.setAttribute('aria-pressed', b.dataset.filter === k ? 'true' : 'false'); });
          paintList(false);
        } }, t('hh.sc.filter.' + k) + (k === 'all' ? '' : ' (' + String(counts[k] || 0) + ')'));
      }));
      MA.dom.mount(host,
        h('p', { 'class': 'hh-guide' }, B((counts.pending || 0) ? 'warning' : 'ok', 'EP4'), ' ', t('hh.sc.guide')),
        (counts.pending || 0) || !all.length ? null : h('p', { 'class': 'hh-guide', id: 'hh-sc-done' }, B('ok', t('hh.sc.allDecided')), ' ',
          t('hh.sc.nextHint'), ' ',
          h('button', { type: 'button', 'class': 'btn btn-sm', id: 'hh-sc-next', onclick: function () { api.go('update', true); } }, t('hh.sc.nextBtn'))),
        h('div', { 'class': 'toolbar' }, segWrap,
          h('button', { type: 'button', 'class': 'btn', id: 'hh-sc-refresh', onclick: function () { local = {}; api.run('merge', {}); } }, t('hh.sc.rebuild'))),
        h('p', { 'class': 'muted hh-small', id: 'hh-sc-keys' }, t('hh.sc.keys')),
        h('div', { 'class': 'hh-sc' }, listEl, detailHost));
      paintList(false);
      return null;
    });
  }

  // ---------------------------------------------------------------- 8. lépés: frissítés
  function windowView(w) {
    if (!w) { return null; }
    return h('div', { 'class': 'hh-window' },
      h('dl', { 'class': 'hh-dl' },
        h('dt', null, t('hh.up.window')), h('dd', { id: 'hh-up-window' }, (w.start_date || '—') + ' – ' + (w.end_date || '—')),
        h('dt', null, t('hh.up.anchorDt')), h('dd', null, t('hh.up.anchor.' + (w.anchor || 'latest')), ' · ', t('hh.up.overlapN', { n: w.overlap_months === undefined ? '—' : String(w.overlap_months) })),
        h('dt', null, t('hh.up.latest')), h('dd', null, w.latest_source_search || '—', w.earliest_source_search ? h('span', { 'class': 'muted' }, ' · ' + t('hh.up.earliest', { d: w.earliest_source_search })) : null),
        w.decision_id ? [h('dt', null, t('hh.up.approved')), h('dd', null, h('code', { 'class': 'cmd-inline' }, w.decision_id))] : null),
      (w.per_review || []).length ? h('table', { 'class': 'table table-compact hh-perreview' },
        h('thead', null, h('tr', null, ['review', 'date', 'basis', 'evidence'].map(function (k) { return h('th', { scope: 'col' }, t('hh.up.col.' + k)); }))),
        h('tbody', null, w.per_review.map(function (r) {
          return h('tr', null, h('td', null, r.review_id), h('td', null, r.search_date || '—'),
            h('td', null, r.fallback ? B('warning', t('hh.up.basis.fallback'), { title: 'H008' }) : t('hh.up.basis.' + (r.basis || 'reported'))),
            h('td', null, r.evidence_id ? h('code', { 'class': 'cmd-inline' }, r.evidence_id) : '—'));
        }))) : null,
      (w.warnings || []).map(function (x) { return h('p', null, B('warning', x.code), ' ', pick(x)); }));
  }

  function planView(p) {
    if (!p) { return null; }
    var qs = p.queries || {};
    return h('section', { 'class': 'hh-plan', id: 'hh-plan', 'aria-labelledby': 'hh-plan-h' },
      h('h3', { id: 'hh-plan-h', i18n: 'hh.up.planTitle' }), windowView(p.window),
      h('ul', { 'class': 'hh-queries' }, Object.keys(qs).sort().map(function (src) {
        return (qs[src] || []).map(function (q) {
          return h('li', null, B('neutral', src), ' ', h('code', { 'class': 'cmd-inline' }, q.query || q.term || q.search || ''),
            q.date_field ? h('span', { 'class': 'muted hh-small' }, ' · ' + q.date_field) : null);
        });
      })),
      (p.skipped || []).map(function (sk) { return h('p', null, B('warning', sk.source || ''), ' ', pick(sk.message) || sk.status || ''); }));
  }

  function resultsView(u) {
    var r = u.results || {};
    return h('section', { 'class': 'hh-up-results', id: 'hh-up-results', 'aria-labelledby': 'hh-upr-h' },
      h('h3', { id: 'hh-upr-h', i18n: 'hh.up.resultsTitle' }),
      windowView(u.window),
      h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact hh-up-queries' },
        h('thead', null, h('tr', null, ['source', 'query', 'status', 'total', 'retrieved'].map(function (k) { return h('th', { scope: 'col' }, t('hh.up.qcol.' + k)); }))),
        h('tbody', null, (u.queries || []).map(function (q) {
          return h('tr', null, h('td', null, q.source), h('td', null, h('code', { 'class': 'cmd-inline' }, q.query || ''), q.message ? h('div', { 'class': 'muted hh-small' }, pick(q.message)) : null),
            h('td', null, B(q.status === 'done' ? 'ok' : (q.status === 'planned' ? 'neutral' : 'warning'), t('hh.up.qstatus.' + q.status)),
              q.complete === false ? [' ', B('error', 'H012', { title: t('hh.up.incomplete') })] : null),
            h('td', { 'class': 'num' }, q.count_total === null || q.count_total === undefined ? '—' : String(q.count_total)),
            h('td', { 'class': 'num' }, q.count_retrieved === null || q.count_retrieved === undefined ? '—' : String(q.count_retrieved)));
        })))),
      h('p', null, t('hh.up.summary', { retrieved: String(r.retrieved_total || 0), dup: String(r.duplicates_within || 0),
        known: String(r.already_known || 0), fresh: String((r.new_records || []).length) })),
      (u.warnings || []).map(function (x) { return h('p', null, B('warning', x.code), ' ', pick(x)); }));
  }

  function stepUpdate(host, api) {
    return api.get('/update').then(function (env) {
      var u = env.data || {};
      var w = u.window || {};
      var anchor = SEL({ id: 'hh-up-anchor', 'class': 'input' }, ['latest', 'earliest', 'manual'].map(function (k) { return { value: k, label: t('hh.up.anchor.' + k) }; }), w.anchor || 'latest');
      var start = h('input', { type: 'text', id: 'hh-up-start', 'class': 'input hh-narrow', placeholder: 'ÉÉÉÉ-HH-NN', value: w.anchor === 'manual' ? (w.start_date || '') : '', autocomplete: 'off' });
      var end = h('input', { type: 'text', id: 'hh-up-end', 'class': 'input hh-narrow', placeholder: 'ÉÉÉÉ-HH-NN', value: w.end_date && w.anchor === 'manual' ? w.end_date : '', autocomplete: 'off' });
      var months = h('input', { type: 'text', id: 'hh-up-months', 'class': 'input hh-narrow', inputmode: 'numeric', value: w.overlap_months === undefined ? '6' : String(w.overlap_months), autocomplete: 'off' });
      var cap = h('input', { type: 'text', id: 'hh-up-cap', 'class': 'input hh-narrow', inputmode: 'numeric', value: '', placeholder: '5000', autocomplete: 'off' });
      var cite = SEL({ id: 'hh-up-cite', 'class': 'input' }, [{ value: '', label: t('hh.up.cite.none') }].concat(['forward', 'backward', 'both'].map(function (k) { return { value: k, label: t('hh.up.cite.' + k) }; })), '');
      var err = h('p', { 'class': 'hh-err', role: 'alert', hidden: true });
      var planHost = h('div', { id: 'hh-plan-host' }, planView(plan));
      function opts() {
        var o = { anchor: anchor.value };
        err.hidden = true;
        if (anchor.value === 'manual') {
          if (!/^\d{4}-\d{2}-\d{2}$/.test(start.value.trim())) { err.textContent = t('hh.up.startBad'); err.hidden = false; start.focus(); return null; }
          o.start = start.value.trim();
        }
        if (end.value.trim()) {
          if (!/^\d{4}-\d{2}-\d{2}$/.test(end.value.trim())) { err.textContent = t('hh.up.endBad'); err.hidden = false; end.focus(); return null; }
          o.end = end.value.trim();
        }
        if (months.value.trim()) {
          if (!/^\d{1,2}$/.test(months.value.trim())) { err.textContent = t('hh.up.monthsBad'); err.hidden = false; months.focus(); return null; }
          o.overlap_months = parseInt(months.value.trim(), 10);
        }
        if (cap.value.trim()) {
          if (!/^\d{1,6}$/.test(cap.value.trim()) || parseInt(cap.value.trim(), 10) < 1 || parseInt(cap.value.trim(), 10) > 100000) { err.textContent = t('hh.up.capBad'); err.hidden = false; cap.focus(); return null; }
          o.cap = parseInt(cap.value.trim(), 10);
        }
        if (cite.value) { o.cite = cite.value; }
        return o;
      }
      MA.dom.mount(host,
        h('form', { 'class': 'hh-up-form', onsubmit: function (ev) { ev.preventDefault(); } },
          h('div', { 'class': 'toolbar' }, F(t('hh.up.anchorLabel'), anchor), F(t('hh.up.startLabel'), start), F(t('hh.up.endLabel'), end, t('hh.up.endHint')),
            F(t('hh.up.monthsLabel'), months, t('hh.up.monthsHint')), F(t('hh.up.capLabel'), cap, t('hh.up.capHint')), F(t('hh.up.citeLabel'), cite)), err,
          h('div', { 'class': 'toolbar' },
            h('button', { type: 'button', 'class': 'btn', id: 'hh-up-preview', onclick: function () {
              var o = opts();
              if (!o) { return; }
              o.dry_run = true;
              delete o.cite;
              api.run('update_search', o, t('hh.up.previewing')).then(function (job) {
                if (job && job.status === 'done' && job.data && job.data.plan) { plan = job.data.plan; MA.dom.mount(planHost, planView(plan)); }
              });
            } }, t('hh.up.preview')),
            h('button', { type: 'button', 'class': 'btn btn-primary', id: 'hh-up-run', onclick: function () {
              var o = opts();
              if (!o) { return; }
              MA.ui.confirm({ title: t('hh.up.approveTitle'), message: t('hh.up.approveBody'), okLabel: t('hh.up.approveOk') }).then(function (ok) { if (ok) { api.run('update_search', o); } });
            } }, t('hh.up.run')))),
        planHost,
        u.exists ? resultsView(u) : EMPTY('hh.up.none'));
      return null;
    });
  }

  hh.steps.screen = stepScreen;
  hh.steps.update = stepUpdate;
})();
