/* screens/headhunter_reviews.js — Metaheadhunter 3–4. lépés: forrás-áttekintések (EP1) és a bevont vizsgálatok kinyerése (EP2).
 *
 * 3. Áttekintések: GET /api/headhunter/reviews → rangsorolt lista (a pontszám a CLI kész szövege: rank.score_text;
 *    csak sorrend, nem minőségítélet), jelzések (Cochrane, nyílt teljes szöveg, protokoll, visszavont); kiválasztás /
 *    kizárás okkal → POST /api/headhunter/decide {kind: 'decide', target: rv-…, value: include|exclude, reason} az
 *    áttekintés-fájl ETag-jével. Keresés: POST /api/headhunter/run {step: 'find_reviews'}.
 * 4. Kinyerés: GET /api/headhunter/reviews/<id> → jelöltek bizonyítékkal (szó szerinti idézet + lokátor), bizonyosság,
 *    keresési dátum és közölt k (bizonyítékkal), k-eltérés (H006); megerősítés / elvetés (indoklással), áttekintésenként
 *    tömeges megerősítés (a szűrő a döntésben rögzül). Futtatás: extract, resolve.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var B = MA.ui.badge;
  var F = MA.proc.field;
  var EMPTY = MA.ui.emptyState;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  /** Belső kód → magyar címke (UX-7); ismeretlen kódnál maga a kód. */
  function label(prefix, v) { return !v ? '—' : (MA.i18n.has(prefix + v) ? t(prefix + v) : String(v)); }
  var hh = MA.hh;
  var STATUS_KIND = { candidate: 'neutral', selected: 'ok', excluded: 'warning', superseded: 'stale' };
  var CAND_KIND = { proposed: 'pending', confirmed: 'ok', rejected: 'warning' };
  var EXCLUDE_PRESETS = ['hh.rv.preset.pico', 'hh.rv.preset.newer', 'hh.rv.preset.notSystematic', 'hh.rv.preset.retracted',
    'hh.rv.preset.noAccess', 'hh.rv.preset.other'];
  var filter = 'all';       // a lista szűrője (csak memóriában)
  var current = null;       // a kinyerés-lépés kiválasztott áttekintése

  function signals(r) {
    var s = r.signals || {};
    var out = [];
    if (r.is_cochrane) { out.push(B('info', 'Cochrane')); }
    if (s.open_fulltext || (r.fulltext && r.fulltext.available)) { out.push(B('ok', t('hh.rv.sig.oa'))); }
    if (s.meta_analysis) { out.push(B('neutral', t('hh.rv.sig.ma'))); }
    if (s.protocol_registered) { out.push(B('neutral', t('hh.rv.sig.protocol'))); }
    if (s.rob_assessed) { out.push(B('neutral', t('hh.rv.sig.rob'))); }
    if (s.prisma_mentioned) { out.push(B('neutral', 'PRISMA')); }
    if (s.retracted) { out.push(B('error', t('hh.rv.sig.retracted'))); }
    if (r.status === 'superseded') { out.push(B('stale', t('hh.rv.sig.superseded', { id: r.superseded_by || '—' }))); }
    return out;
  }

  function searchDate(sd) {
    if (!sd) { return h('span', { 'class': 'muted' }, '—'); }
    return h('span', null, sd.value || '—', sd.fallback ? [' ', B('warning', t('hh.rv.sdFallback'), { title: 'H008' })] : null);
  }

  function decideReview(api, r, include) {
    return api.reasonDialog({
      title: t(include ? 'hh.rv.includeTitle' : 'hh.rv.excludeTitle', { label: r.label || r.review_id }),
      intro: t(include ? 'hh.rv.includeIntro' : 'hh.rv.excludeIntro'),
      presets: include ? [] : EXCLUDE_PRESETS, needReason: !include, okLabel: t(include ? 'hh.rv.include' : 'hh.rv.exclude'),
      danger: !include
    }).then(function (res) {
      if (!res) { return null; }
      return api.decide({ kind: 'decide', target: r.review_id, value: include ? 'include' : 'exclude', reason: res.reason }, r.etag);
    });
  }

  function details(r) {
    var comps = (r.rank && r.rank.components_text) || {};
    return h('details', { 'class': 'hh-rv-details' }, h('summary', null, t('hh.rv.details')),
      h('dl', { 'class': 'hh-dl' },
        h('dt', null, t('hh.rv.ids')), h('dd', null, hh.api.idList(r.ids)),
        h('dt', null, t('hh.rv.components')), h('dd', null, Object.keys(comps).length ? Object.keys(comps).sort().map(function (k) {
          return h('span', { 'class': 'hh-comp' }, (MA.i18n.has('hh.rv.comp.' + k) ? t('hh.rv.comp.' + k) : k) + ': ' + comps[k]);
        }) : '—'),
        h('dt', null, t('hh.rv.searchDate')), h('dd', null, searchDate(r.search_date)),
        h('dt', null, t('hh.rv.kReported')), h('dd', null, r.k_reported && r.k_reported.value !== null ? String(r.k_reported.value) + ' (' + (r.k_reported.unit || '') + ')' : '—'),
        h('dt', null, t('hh.rv.fulltext')), h('dd', null, r.fulltext ? t('hh.rv.route.' + (r.fulltext.route || 'none')) + (r.fulltext.license ? ' · ' + r.fulltext.license : '') : '—'),
        h('dt', null, t('hh.rv.found')), h('dd', null, (r.found_by || []).join(', ') || '—')));
  }

  // ---------------------------------------------------------------- 3. lépés
  function stepReviews(host, api) {
    var sinceIn = h('input', { type: 'text', id: 'hh-find-since', 'class': 'input hh-narrow', inputmode: 'numeric', placeholder: 'ÉÉÉÉ', autocomplete: 'off' });
    var maxIn = h('input', { type: 'text', id: 'hh-find-max', 'class': 'input hh-narrow', inputmode: 'numeric', value: '200', autocomplete: 'off' });
    var findErr = h('span', { 'class': 'hh-err', role: 'alert' });
    function find() {
      var opts = {};
      var since = sinceIn.value.trim();
      var mx = maxIn.value.trim();
      if (since && !/^\d{4}(-\d{2}(-\d{2})?)?$/.test(since)) { findErr.textContent = t('hh.rv.sinceBad'); sinceIn.focus(); return; }
      if (mx && !/^\d{1,4}$/.test(mx)) { findErr.textContent = t('hh.rv.maxBad'); maxIn.focus(); return; }
      findErr.textContent = '';
      if (since) { opts.since = since; }
      if (mx) { opts.max = parseInt(mx, 10); }
      api.run('find_reviews', opts);
    }
    return api.get('/reviews').then(function (env) {
      var items = (env.data && env.data.items) || [];
      var counts = (env.data && env.data.counts) || {};
      var list = h('div', { 'class': 'hh-rv-list', id: 'hh-rv-list' });
      function paint() {
        var rows = items.filter(function (r) { return filter === 'all' || r.status === filter; });
        MA.dom.mount(list, rows.length ? h('ol', { 'class': 'hh-rv-items' }, rows.map(function (r, i) {
          var b = r.bib || {};
          return h('li', { 'class': ['hh-rv', 'is-' + (r.status || 'unknown')], dataset: { review: r.review_id } },
            h('div', { 'class': 'hh-rv-rank' }, h('span', { 'class': 'hh-rv-pos' }, '#' + String(i + 1)),
              h('span', { 'class': 'num', title: t('hh.rv.scoreTitle') }, (r.rank && r.rank.score_text) || '—')),
            h('div', { 'class': 'hh-rv-main' },
              h('div', { 'class': 'row' }, B(STATUS_KIND[r.status] || 'neutral', t('hh.rv.status.' + (r.status || 'candidate'))),
                h('strong', null, r.label || r.review_id), h('span', { 'class': 'muted' }, (b.journal || '') + (b.year ? ' ' + b.year : ''))),
              h('div', { 'class': 'hh-rv-title' }, b.title || '—'),
              h('div', { 'class': 'row hh-rv-sig' }, signals(r),
                h('span', { 'class': 'muted' }, t('hh.rv.candCount', { n: String(r.n_candidates || 0) }))),
              (r.problems || []).length ? h('p', { 'class': 'hh-err' }, B('error', 'H001'), ' ', r.problems.slice(0, 2).join('; ')) : null,
              details(r)),
            h('div', { 'class': 'hh-rv-actions' },
              r.status !== 'selected' ? h('button', { type: 'button', 'class': 'btn btn-sm btn-primary hh-rv-include', onclick: function () { decideReview(api, r, true); } }, t('hh.rv.include')) : null,
              r.status !== 'excluded' ? h('button', { type: 'button', 'class': 'btn btn-sm hh-rv-exclude', onclick: function () { decideReview(api, r, false); } }, t('hh.rv.exclude')) : null));
        })) : EMPTY(items.length ? 'hh.rv.emptyFilter' : 'hh.rv.empty'));
      }
      var segs = ['all', 'candidate', 'selected', 'excluded', 'superseded'].map(function (k) {
        return h('button', { type: 'button', 'class': 'seg-btn', 'aria-pressed': filter === k ? 'true' : 'false', dataset: { filter: k },
          onclick: function () {
            filter = k;
            MA.dom.$$('.seg-btn', segWrap).forEach(function (b) { b.setAttribute('aria-pressed', b.dataset.filter === k ? 'true' : 'false'); });
            paint();
          } }, t('hh.rv.filter.' + k) + (k === 'all' ? '' : ' (' + String(counts[k] || 0) + ')'));
      });
      var segWrap = h('div', { 'class': 'seg', role: 'group', 'aria-label': t('hh.rv.filterLabel') }, segs);
      var selected = counts.selected || 0;
      MA.dom.mount(host,
        h('form', { 'class': 'toolbar hh-find', onsubmit: function (ev) { ev.preventDefault(); find(); } },
          F(t('hh.rv.since'), sinceIn), F(t('hh.rv.max'), maxIn),
          h('button', { type: 'submit', 'class': 'btn' + (items.length ? '' : ' btn-primary'), id: 'hh-find' }, t(items.length ? 'hh.rv.findAgain' : 'hh.rv.find')),
          findErr),
        h('p', { 'class': 'hh-guide' }, B('info', 'EP1'), ' ', t('hh.rv.guide')),
        h('div', { 'class': 'toolbar' }, segWrap), list,
        h('div', { 'class': 'toolbar hh-next' },
          h('button', { type: 'button', 'class': 'btn btn-primary', id: 'hh-extract-all', disabled: !selected, onclick: function () { api.run('extract', { strategy: 'auto' }).then(function (job) { if (job && job.status === 'done') { api.go('extract', true); } }); } },
            t('hh.rv.extractAll', { n: String(selected) })),
          !selected ? h('span', { 'class': 'muted' }, t('hh.rv.selectFirst')) : null));
      paint();
    });
  }

  // ---------------------------------------------------------------- 4. lépés
  function candRow(api, rv, c, etag) {
    var role = c.role_in_review || 'unknown';
    var sec = c.secondary_data || [];
    function act(include) {
      var go = include ? Promise.resolve({ reason: null }) : api.reasonDialog({ title: t('hh.ex.rejectTitle', { label: c.study_label_in_review || c.cand_id }),
        intro: t('hh.ex.rejectIntro'), needReason: true, okLabel: t('hh.ex.reject'), danger: true });
      return go.then(function (res) {
        if (!res) { return null; }
        return api.decide({ kind: 'decide', target: rv + '#' + c.cand_id, value: include ? 'include' : 'exclude', reason: res.reason }, etag);
      });
    }
    return h('tr', { 'class': ['hh-cand', 'is-' + c.status], dataset: { cand: c.cand_id } },
      h('th', { scope: 'row' }, h('div', null, c.study_label_in_review || (c.cited_as || {}).first_author || c.cand_id),
        h('div', { 'class': 'muted hh-small' }, c.cand_id + (c.group_key ? ' · ' + t('hh.ex.group', { g: c.group_key }) : ''))),
      h('td', null, h('div', { 'class': 'hh-cited' }, (c.cited_as || {}).text || ''), hh.api.idList(c.ids)),
      h('td', null, (c.evidence || []).map(function (e) { return hh.api.quote(e); }),
        (c.evidence_missing || []).length ? h('p', { 'class': 'hh-err' }, B('error', 'H002'), ' ', t('hh.ex.evidenceMissing')) : null,
        !(c.evidence || []).length && !(c.evidence_missing || []).length ? h('p', { 'class': 'hh-err' }, B('error', 'H002'), ' ', t('hh.ex.noEvidence')) : null),
      h('td', null, B(role.indexOf('included') === 0 ? 'ok' : 'neutral', t('hh.ex.role.' + role)), ' ', hh.api.confBadge(c.confidence),
        sec.length ? h('div', null, B('estimated', t('hh.ex.secondary', { n: String(sec.length) }), { title: t('hh.ex.secondaryTitle') })) : null),
      h('td', null, B(CAND_KIND[c.status] || 'neutral', t('hh.ex.status.' + c.status))),
      h('td', { 'class': 'hh-actions' },
        c.status !== 'confirmed' ? h('button', { type: 'button', 'class': 'btn btn-sm btn-primary hh-cand-confirm', onclick: function () { act(true); } }, t('hh.ex.confirm')) : null,
        c.status !== 'rejected' ? h('button', { type: 'button', 'class': 'btn btn-sm hh-cand-reject', onclick: function () { act(false); } }, t('hh.ex.reject')) : null));
  }

  function reviewPanel(api, host, rid) {
    MA.dom.mount(host, MA.ui.spinner());
    return api.get('/reviews/' + encodeURIComponent(rid)).then(function (env) {
      var d = env.data || {};
      var s = d.summary || {};
      var etag = env.etag;
      var cands = d.candidates || [];
      // a tömeges megerősítés az ismeretlen szerepű (csak irodalomjegyzékből ismert) tételt nem érinti (W-UNKNOWN-ROLE)
      var proposed = cands.filter(function (c) { return c.status === 'proposed' && c.role_in_review !== 'unknown'; }).length;
      var k = s.k_reported && s.k_reported.value;
      var mismatch = typeof k === 'number' && k !== s.n_groups;
      MA.dom.mount(host,
        h('div', { 'class': 'hh-ex-head' },
          h('p', null, h('strong', null, s.label || rid), ' — ', (s.bib || {}).title || ''),
          h('dl', { 'class': 'hh-dl' },
            h('dt', null, t('hh.rv.searchDate')), h('dd', null, searchDate(s.search_date), d.search_date_evidence ? hh.api.quote(d.search_date_evidence) : null,
              s.search_date && s.search_date.fallback ? h('p', { 'class': 'muted' }, t('hh.ex.sdFallbackHint')) : null),
            h('dt', null, t('hh.rv.kReported')), h('dd', null, typeof k === 'number' ? String(k) : '—', ' · ', t('hh.ex.groups', { n: String(s.n_groups || 0) }),
              mismatch ? [' ', B('warning', t('hh.ex.kMismatch'), { title: 'H006' })] : null, d.k_evidence ? hh.api.quote(d.k_evidence) : null),
            h('dt', null, t('hh.rv.fulltext')), h('dd', null, s.fulltext ? t('hh.rv.route.' + (s.fulltext.route || 'none')) : '—',
              s.extraction ? [' · ', t('hh.ex.lastRun', { status: label('hh.ex.run.', s.extraction.status), strategy: label('hh.ex.strategy.', s.extraction.strategy) })] : null))),
        h('div', { 'class': 'toolbar' },
          h('button', { type: 'button', 'class': 'btn', id: 'hh-extract-one', onclick: function () { api.run('extract', { reviews: [rid], strategy: 'auto' }); } }, t('hh.ex.rerun')),
          h('button', { type: 'button', 'class': 'btn', id: 'hh-confirm-all', disabled: !proposed, onclick: function () {
            MA.ui.confirm({ title: t('hh.ex.batchTitle'), message: t('hh.ex.batchBody', { n: String(proposed), label: s.label || rid }), okLabel: t('hh.ex.batchOk') }).then(function (ok) {
              if (ok) { api.decide({ kind: 'batch', value: 'include', batch: { type: 'all_candidates', review: rid } }, etag); }
            });
          } }, t('hh.ex.batch', { n: String(proposed) }))),
        cands.length ? h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table hh-cand-table', id: 'hh-cand-table' },
          h('caption', { 'class': 'sr-only' }, t('hh.ex.caption', { label: s.label || rid })),
          h('thead', null, h('tr', null, ['study', 'cited', 'evidence', 'role', 'status', 'actions'].map(function (c) { return h('th', { scope: 'col' }, t('hh.ex.col.' + c)); }))),
          h('tbody', null, cands.map(function (c) { return candRow(api, rid, c, etag); })))) : EMPTY('hh.ex.noCandidates'),
        (d.excluded_by_review || []).length ? h('details', null, h('summary', null, t('hh.ex.excludedByReview', { n: String(d.excluded_by_review.length) })),
          h('ul', null, d.excluded_by_review.map(function (x) { return h('li', null, x.cited_as || '', x.reason_quote ? h('span', { 'class': 'muted' }, ' — „' + x.reason_quote + '”') : null); }))) : null);
    });
  }

  function stepExtract(host, api) {
    return api.get('/reviews', { status: 'selected' }).then(function (env) {
      var items = (env.data && env.data.items) || [];
      if (!items.length) {
        MA.dom.mount(host, EMPTY('hh.ex.noSelected'), h('button', { type: 'button', 'class': 'btn', onclick: function () { api.go('reviews', true); } }, t('hh.ex.goReviews')));
        return null;
      }
      if (!items.some(function (r) { return r.review_id === current; })) { current = items[0].review_id; }
      var panel = h('div', { 'class': 'hh-ex-panel', id: 'hh-ex-panel', role: 'tabpanel' });
      var tabs = MA.ui.tabs({ label: t('hh.ex.tabs'), panelId: 'hh-ex-panel', active: current,
        tabs: items.map(function (r) {
          var n = (r.candidates || {}).proposed || 0;
          return { id: r.review_id, label: (r.label || r.review_id) + (n ? ' (' + String(n) + ')' : '') };
        }),
        onSelect: function (id) { current = id; reviewPanel(api, panel, id); } });
      var total = items.reduce(function (a, r) { return a + ((r.candidates || {}).proposed || 0); }, 0);
      MA.dom.mount(host,
        h('p', { 'class': 'hh-guide' }, B(total ? 'warning' : 'ok', 'EP2'), ' ', t('hh.ex.guide')),
        tabs, panel,
        h('div', { 'class': 'toolbar hh-next' },
          h('button', { type: 'button', 'class': 'btn btn-primary', id: 'hh-resolve', onclick: function () { api.run('resolve', {}); } }, t('hh.ex.resolve')),
          h('span', { 'class': 'muted' }, t('hh.ex.resolveHint'))));
      return reviewPanel(api, panel, current);
    });
  }

  hh.steps.reviews = stepReviews;
  hh.steps.extract = stepExtract;
})();
