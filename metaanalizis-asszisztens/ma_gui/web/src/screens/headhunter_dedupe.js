/* screens/headhunter_dedupe.js — Metaheadhunter 5–6. lépés: duplumok és vizsgálat-kapcsolás (EP3), átfedés (CCA).
 *
 * 5. Duplumok: GET /api/headhunter/proposals[?status=&kind=] → javaslatonként a tételek EGYMÁS MELLETT (cím, első szerző,
 *    év, folyóirat, azonosítók, eredet), az eltérő mezők „≠” jellel és kiemeléssel (nem csak színnel); a jellemzők és a
 *    szabály magyarázata. Elfogad / elutasít (POST /api/headhunter/decide, a studies.json ETag-jével); feloldási
 *    javaslatnál a lehetőség kiválasztása (option:N). Szűrős tömeges elfogadás megerősítő párbeszéddel (a szűrő a
 *    döntésben rögzül). Az automatikusan (azonos azonosító alapján, L1) összevont párok listája visszavonható.
 * 6. Átfedés: GET /api/headhunter/overlap → összesített és páronkénti CCA a motor kész szövegével (cca_text) és sávjával,
 *    hőtérkép (SVG; a pixel-matematika csak MA.geom-ban) + táblázatos nézet; a hivatkozási mátrix (vizsgálat × áttekintés).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var B = MA.ui.badge;
  var F = MA.proc.field;
  var SEL = MA.proc.select;
  var EMPTY = MA.ui.emptyState;
  var s = MA.dom.svg;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '' : fb); };
  var hh = MA.hh;
  var CERT_KIND = { certain: 'ok', probable: 'info', possible: 'warning' };
  var BAND_KIND = { slight: 'ok', moderate: 'info', high: 'warning', very_high: 'error' };
  var MAX_MATRIX_ROWS = 150;
  var propFilter = { status: 'pending', kind: '' };
  var ovLevel = 'study';
  var matrixAsTable = false;

  // ---------------------------------------------------------------- 5. lépés
  function norm(v) { return v === null || v === undefined ? '' : String(v).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(); }

  function itemFields(it) {
    if (it.type === 'study') {
      var r0 = (it.reports || [])[0] || {};
      return { label: it.label, title: (r0.bib || {}).title, first_author: (r0.bib || {}).first_author, year: (r0.bib || {}).year,
        journal: (r0.bib || {}).journal, ids: r0.ids || {}, extra: t('hh.dd.studyReports', { n: String((it.reports || []).length) }),
        reviews: (it.reviews || []).join(', ') };
    }
    if (it.type === 'candidate') {
      var ca = it.cited_as || {};
      return { label: it.label || it.id, title: ca.title || ca.text, first_author: ca.first_author, year: ca.year, journal: ca.journal,
        ids: it.ids || {}, extra: t('hh.dd.candidate', { id: it.id }) };
    }
    if (it.type === 'record') {
      var b = it.bib || {};
      return { label: it.rec_id, title: b.title, first_author: b.first_author, year: b.year, journal: b.journal, ids: it.ids || {},
        extra: t('hh.dd.resolution.' + ((it.resolution || {}).status || 'not_attempted')),
        reviews: (it.origins || []).map(function (o) { return o.review_id || o.route; }).filter(function (x) { return !!x; }).join(', ') };
    }
    return { label: it.id, title: null, ids: {} };
  }

  var FIELDS = ['title', 'first_author', 'year', 'journal'];

  function sideBySide(p) {
    var items = (p.records || []).map(itemFields);
    function differs(f) {
      var vals = items.map(function (x) { return norm(x[f]); }).filter(function (v) { return v !== ''; });
      return vals.length > 1 && vals.some(function (v) { return v !== vals[0]; });
    }
    var idKeys = ['pmid', 'doi', 'pmcid', 'nct'];
    function idDiff(k) {
      var vals = items.map(function (x) { return x.ids[k] ? x.ids[k].value : ''; }).filter(function (v) { return v !== ''; });
      return vals.length > 1 && vals.some(function (v) { return v !== vals[0]; });
    }
    return h('table', { 'class': 'table hh-sbs' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('hh.dd.field')), items.map(function (x, i) { return h('th', { scope: 'col' }, String(i + 1) + '. ' + (x.label || '')); }))),
      h('tbody', null,
        FIELDS.map(function (f) {
          var dif = differs(f);
          return h('tr', { 'class': dif && 'hh-diff', dataset: { field: f } }, h('th', { scope: 'row' }, t('hh.dd.f.' + f), dif ? h('span', { 'class': 'hh-diff-mark', title: t('hh.dd.differs') }, ' ≠') : null),
            items.map(function (x) { return h('td', null, x[f] === null || x[f] === undefined ? '—' : String(x[f])); }));
        }),
        idKeys.map(function (k) {
          var dif = idDiff(k);
          return h('tr', { 'class': dif && 'hh-diff', dataset: { field: k } }, h('th', { scope: 'row' }, k.toUpperCase(), dif ? h('span', { 'class': 'hh-diff-mark', title: t('hh.dd.differs') }, ' ≠') : null),
            items.map(function (x) { var v = x.ids[k]; return h('td', null, v ? [v.value, ' ', v.api ? B('ok', t('hh.id.api'), { symbol: '✔' }) : B('warning', t('hh.id.unconfirmed'))] : '—'); }));
        }),
        h('tr', null, h('th', { scope: 'row' }, t('hh.dd.f.origin')), items.map(function (x) { return h('td', null, x.reviews || '—', x.extra ? h('div', { 'class': 'muted hh-small' }, x.extra) : null); }))));
  }

  function features(p) {
    var f = p.features || {};
    var out = [];
    if (f.title_sim_text) { out.push(t('hh.dd.feat.title', { v: f.title_sim_text })); }
    if (f.first_author_match !== null && f.first_author_match !== undefined) { out.push(t(f.first_author_match ? 'hh.dd.feat.authorYes' : 'hh.dd.feat.authorNo')); }
    if (f.year_diff !== null && f.year_diff !== undefined) { out.push(t('hh.dd.feat.year', { n: String(f.year_diff) })); }
    if (f.journal_match !== null && f.journal_match !== undefined) { out.push(t(f.journal_match ? 'hh.dd.feat.journalYes' : 'hh.dd.feat.journalNo')); }
    if ((f.shared_ids || []).length) { out.push(t('hh.dd.feat.sharedIds', { ids: f.shared_ids.join(', ') })); }
    if ((f.shared_registry || []).length) { out.push(t('hh.dd.feat.registry', { ids: f.shared_registry.join(', ') })); }
    if ((f.conflicting_ids || []).length) { out.push(t('hh.dd.feat.conflict', { ids: f.conflicting_ids.join(', ') })); }
    return h('ul', { 'class': 'hh-feats' }, out.map(function (x) { return h('li', null, x); }));
  }

  function options(api, p, etag) {
    return h('ol', { 'class': 'hh-options' }, (p.options || []).map(function (o, i) {
      var b = o.bib || {};
      var n = o.option || (i + 1);
      return h('li', null, h('span', null, (b.first_author || '') + ' ' + (b.year || ''), ' — ', b.title || '—'), ' ', hh.api.idList(o.ids),
        o.score_text ? h('span', { 'class': 'muted' }, ' · ' + t('hh.dd.score', { v: o.score_text })) : null, ' ',
        h('button', { type: 'button', 'class': 'btn btn-sm', onclick: function () {
          api.decide({ kind: 'decide', target: p.proposal_id, value: 'option:' + String(n) }, etag);
        } }, t('hh.dd.pickOption', { n: String(n) })));
    }));
  }

  function proposalCard(api, p, etag) {
    function act(accept) {
      var go = accept ? Promise.resolve({ reason: null }) : api.reasonDialog({ title: t('hh.dd.rejectTitle'), intro: t('hh.dd.rejectIntro'), okLabel: t('hh.dd.reject') });
      return go.then(function (res) {
        if (!res) { return null; }
        return api.decide({ kind: 'decide', target: p.proposal_id, value: accept ? 'accept' : 'reject', reason: res.reason }, etag);
      });
    }
    var pending = p.status === 'pending';
    return h('article', { 'class': ['hh-prop', 'is-' + p.status], dataset: { proposal: p.proposal_id }, 'aria-labelledby': 'hh-prop-' + p.proposal_id },
      h('div', { 'class': 'row' },
        h('h3', { 'class': 'hh-prop-title', id: 'hh-prop-' + p.proposal_id }, t('hh.dd.kind.' + p.kind)),
        p.certainty ? B(CERT_KIND[p.certainty] || 'neutral', t('hh.dd.cert.' + p.certainty)) : null,
        p.score_text ? h('span', { 'class': 'muted' }, t('hh.dd.score', { v: p.score_text })) : null,
        h('code', { 'class': 'cmd-inline' }, p.proposal_id),
        !pending ? B('neutral', t('hh.dd.status.' + p.status)) : null),
      p.explanation ? h('p', null, pick(p.explanation)) : null,
      h('p', { 'class': 'muted hh-small' }, t('hh.dd.rule', { rule: p.rule || '—' })),
      features(p),
      h('div', { 'class': 'table-wrap' }, sideBySide(p)),
      p.kind === 'resolution' && pending ? options(api, p, etag) : null,
      pending ? h('div', { 'class': 'toolbar' },
        p.kind !== 'resolution' ? h('button', { type: 'button', 'class': 'btn btn-primary hh-prop-accept', onclick: function () { act(true); } }, t(p.kind === 'same_study' ? 'hh.dd.acceptLink' : 'hh.dd.accept')) : null,
        h('button', { type: 'button', 'class': 'btn hh-prop-reject', onclick: function () { act(false); } }, t(p.kind === 'resolution' ? 'hh.dd.noneFits' : 'hh.dd.reject'))) : null);
  }

  function batchDialog(api, items, etag) {
    var kind = SEL({ id: 'hh-batch-kind', 'class': 'input' }, [{ value: '', label: t('hh.dd.batch.anyKind') }].concat(
      ['same_report', 'same_study', 'id_conflict'].map(function (k) { return { value: k, label: t('hh.dd.kind.' + k) }; })), '');
    var cert = SEL({ id: 'hh-batch-cert', 'class': 'input' }, [{ value: 'probable', label: t('hh.dd.cert.probable') },
      { value: 'possible', label: t('hh.dd.cert.possible') }], 'probable');
    var minScore = h('input', { type: 'text', id: 'hh-batch-min', 'class': 'input hh-narrow', inputmode: 'decimal', value: '0.97', autocomplete: 'off' });
    var note = h('p', { 'class': 'muted', role: 'status', 'aria-live': 'polite' });
    var err = h('p', { 'class': 'hh-err', role: 'alert', hidden: true });
    function matching() {
      var ms = parseFloat(minScore.value.replace(',', '.'));
      return items.filter(function (p) {
        return p.status === 'pending' && p.kind !== 'resolution' && (!kind.value || p.kind === kind.value) && p.certainty === cert.value &&
          (isNaN(ms) || (typeof p.score === 'number' && p.score >= ms));
      });
    }
    function upd() { note.textContent = t('hh.dd.batch.matching', { n: String(matching().length) }); }
    [kind, cert, minScore].forEach(function (el) { el.addEventListener('input', upd); el.addEventListener('change', upd); });
    upd();
    MA.ui.modal({
      title: t('hh.dd.batch.title'), size: 'md',
      body: [h('p', null, t('hh.dd.batch.intro')), F(t('hh.dd.batch.kind'), kind), F(t('hh.dd.batch.cert'), cert),
        F(t('hh.dd.batch.min'), minScore, t('hh.dd.batch.minHint')), note, err],
      actions: [{ label: t('common.cancel'), kind: 'ghost' }, { label: t('hh.dd.batch.ok'), kind: 'primary', onClick: function () {
        var ms = minScore.value.trim() ? parseFloat(minScore.value.replace(',', '.')) : null;
        if (ms !== null && (isNaN(ms) || ms < 0 || ms > 1)) { err.textContent = t('hh.dd.batch.minBad'); err.hidden = false; minScore.focus(); return false; }
        if (!matching().length) { err.textContent = t('hh.dd.batch.none'); err.hidden = false; return false; }
        var batch = { type: 'all_proposals', certainty: cert.value };
        if (kind.value) { batch.kind = kind.value; }
        if (ms !== null) { batch.min_score = ms; }
        api.decide({ kind: 'batch', value: 'accept', batch: batch, reason: t('hh.dd.batch.reason') }, etag);
        return true;
      } }]
    });
  }

  function stepDedupe(host, api) {
    var q = { status: propFilter.status };
    if (propFilter.kind) { q.kind = propFilter.kind; }
    return api.get('/proposals', q).then(function (env) {
      var d = env.data || {};
      var etag = env.etag;
      var items = d.items || [];
      var counts = d.counts || {};
      function cnt(kind) {
        return Object.keys(counts).filter(function (k) { return k.split(':')[1] === 'pending' && (!kind || k.split(':')[0] === kind); })
          .reduce(function (a, k) { return a + counts[k]; }, 0);
      }
      var kindSel = SEL({ id: 'hh-dd-kind', 'class': 'input', onchange: function () { propFilter.kind = kindSel.value; api.refresh(); } },
        [{ value: '', label: t('hh.dd.allKinds') }].concat(['same_report', 'same_study', 'id_conflict', 'resolution', 'split_study'].map(function (k) {
          return { value: k, label: t('hh.dd.kind.' + k) + ' (' + String(cnt(k)) + ')' };
        })), propFilter.kind);
      var statusSel = SEL({ id: 'hh-dd-status', 'class': 'input', onchange: function () { propFilter.status = statusSel.value; api.refresh(); } },
        ['pending', 'accepted', 'rejected', 'all'].map(function (k) { return { value: k, label: t('hh.dd.status.' + k) }; }), propFilter.status);
      MA.dom.mount(host,
        !d.exists ? h('p', null, EMPTY('hh.dd.noStudies')) : null,
        h('div', { 'class': 'toolbar' },
          h('button', { type: 'button', 'class': 'btn' + (d.exists ? '' : ' btn-primary'), id: 'hh-dedupe', onclick: function () { api.run('dedupe', {}); } }, t('hh.dd.run')),
          F(t('hh.dd.kindLabel'), kindSel), F(t('hh.dd.statusLabel'), statusSel),
          h('button', { type: 'button', 'class': 'btn', id: 'hh-dd-batch', disabled: !cnt(''), onclick: function () { batchDialog(api, items, etag); } }, t('hh.dd.batch.button'))),
        h('p', { 'class': 'hh-guide' }, B(cnt('') ? 'warning' : 'ok', 'EP3'), ' ', t('hh.dd.guide')),
        items.length ? h('div', { 'class': 'hh-props', id: 'hh-props' }, items.map(function (p) { return proposalCard(api, p, etag); }))
          : EMPTY(d.exists ? 'hh.dd.none' : 'hh.dd.noStudies'),
        (d.auto_applied || []).length ? h('details', { 'class': 'hh-auto', id: 'hh-auto' },
          h('summary', null, t('hh.dd.auto', { n: String(d.auto_applied.length) })),
          h('p', { 'class': 'muted' }, t('hh.dd.autoHint')),
          h('ul', { 'class': 'item-list' }, d.auto_applied.map(function (p) {
            return h('li', { 'class': 'item' }, h('code', { 'class': 'cmd-inline' }, p.proposal_id), ' ', (p.items || []).join(' = '), ' ',
              h('button', { type: 'button', 'class': 'btn btn-sm', onclick: function () {
                api.reasonDialog({ title: t('hh.dd.undoTitle'), intro: t('hh.dd.undoIntro'), needReason: true, okLabel: t('hh.dd.undo'), danger: true }).then(function (res) {
                  if (res) { api.decide({ kind: 'decide', target: p.proposal_id, value: 'reject', reason: res.reason }, etag); }
                });
              } }, t('hh.dd.undo')));
          }))) : null);
    });
  }

  // ---------------------------------------------------------------- 6. lépés: átfedés
  function short(v, n) { v = String(v || ''); return v.length > n ? v.slice(0, n - 1) + '…' : v; }

  function bandBadge(b) { return b ? B(BAND_KIND[b] || 'neutral', t('hh.ov.band.' + b)) : B('neutral', t('hh.ov.band.none')); }

  function pairHeatmap(d) {
    var revs = d.reviews || [];
    var meta = d.review_meta || {};
    var n = revs.length;
    if (n < 2) { return EMPTY('hh.ov.fewReviews'); }
    var pairs = {};
    (d.pairs || []).forEach(function (p) { pairs[p.a + '|' + p.b] = p; pairs[p.b + '|' + p.a] = p; });
    var label = function (rid) { return short((meta[rid] && meta[rid].label) || rid, 14); };
    var LW = 120, TOP = 26, CELL = 88;
    var W = LW + CELL * n + 4, Hh = TOP + CELL * n + 4;
    var bx = MA.geom.band(n, [LW, LW + CELL * n], 0.06);
    var by = MA.geom.band(n, [TOP, TOP + CELL * n], 0.06);
    var root = s('svg', { viewBox: '0 0 ' + MA.geom.px(W) + ' ' + MA.geom.px(Hh), width: MA.geom.px(W), height: MA.geom.px(Hh), 'class': 'hh-heat', role: 'img', 'aria-labelledby': 'hh-heat-t' },
      s('title', { id: 'hh-heat-t' }, t('hh.ov.heatTitle')));
    revs.forEach(function (rid, i) {
      root.appendChild(s('text', { x: MA.geom.px(bx.center(i)), y: '16', 'class': 'hh-heat-col', 'text-anchor': 'middle' }, label(rid)));
      root.appendChild(s('text', { x: MA.geom.px(LW - 6), y: MA.geom.px(by.center(i) + 4), 'class': 'hh-heat-row', 'text-anchor': 'end' }, label(rid)));
      revs.forEach(function (rid2, j) {
        var x = bx.at(j), y = by.at(i), w = bx.bandwidth, hgt = by.bandwidth;
        var g;
        if (i === j) {
          g = s('g', { 'class': 'hh-heat-cell is-diag' }, s('rect', { x: MA.geom.px(x), y: MA.geom.px(y), width: MA.geom.px(w), height: MA.geom.px(hgt), rx: '3' }),
            s('text', { x: MA.geom.px(x + w / 2), y: MA.geom.px(y + hgt / 2 + 4), 'text-anchor': 'middle' }, '—'));
        } else {
          var p = pairs[rid + '|' + rid2];
          var band = p ? p.band : null;
          var txt = p && p.cca_text ? p.cca_text + '%' : '—';
          g = s('g', { 'class': ['hh-heat-cell', 'band-' + (band || 'none')], 'data-a': rid, 'data-b': rid2, 'data-cca': p ? (p.cca_text || '') : '' },
            s('title', null, t('hh.ov.cellTitle', { a: label(rid), b: label(rid2), cca: txt, band: band ? t('hh.ov.band.' + band) : '—', shared: p ? String(p.shared) : '—' })),
            s('rect', { x: MA.geom.px(x), y: MA.geom.px(y), width: MA.geom.px(w), height: MA.geom.px(hgt), rx: '3' }),
            s('text', { x: MA.geom.px(x + w / 2), y: MA.geom.px(y + hgt / 2 + 4), 'text-anchor': 'middle' }, txt));
        }
        root.appendChild(g);
      });
    });
    return root;
  }

  function matrix(d) {
    var revs = d.reviews || [];
    var meta = d.review_meta || {};
    var rows = (d.rows || []).slice(0, MAX_MATRIX_ROWS);
    var label = function (rid) { return short((meta[rid] && meta[rid].label) || rid, 14); };
    if (!rows.length || !revs.length) { return EMPTY('hh.ov.noRows'); }
    if (matrixAsTable) {
      return h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact hh-matrix-table', id: 'hh-matrix-table' },
        h('caption', { 'class': 'sr-only' }, t('hh.ov.matrixTitle')),
        h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('hh.ov.study')), revs.map(function (r) { return h('th', { scope: 'col' }, label(r)); }))),
        h('tbody', null, rows.map(function (r) {
          return h('tr', null, h('th', { scope: 'row' }, r.label || r.key), (r['in'] || []).map(function (v, j) {
            var idx = r.index_review === revs[j];
            return h('td', null, v ? (idx ? t('hh.ov.inIndex') : t('hh.ov.in')) : '');
          }));
        }))));
    }
    var LW = 190, TOP = 24, CW = 90, RH = 18;
    var W = LW + CW * revs.length + 4, Hh = TOP + RH * rows.length + 4;
    var bx = MA.geom.band(revs.length, [LW, LW + CW * revs.length], 0.1);
    var by = MA.geom.band(rows.length, [TOP, TOP + RH * rows.length], 0.12);
    var root = s('svg', { viewBox: '0 0 ' + MA.geom.px(W) + ' ' + MA.geom.px(Hh), width: MA.geom.px(W), height: MA.geom.px(Hh), 'class': 'hh-matrix', role: 'img', 'aria-labelledby': 'hh-mx-t' },
      s('title', { id: 'hh-mx-t' }, t('hh.ov.matrixTitle')));
    revs.forEach(function (rid, j) { root.appendChild(s('text', { x: MA.geom.px(bx.center(j)), y: '15', 'text-anchor': 'middle', 'class': 'hh-heat-col' }, label(rid))); });
    rows.forEach(function (r, i) {
      var lab = r.label || r.key;
      root.appendChild(s('text', { x: MA.geom.px(LW - 6), y: MA.geom.px(by.center(i) + 4), 'text-anchor': 'end', 'class': 'hh-heat-row' }, short(lab, 28)));
      (r['in'] || []).forEach(function (v, j) {
        var idx = r.index_review === revs[j];
        root.appendChild(s('g', { 'class': ['hh-mx-cell', v ? 'is-in' : 'is-out', idx && 'is-index'] },
          s('title', null, lab + ' · ' + label(revs[j]) + ': ' + t(v ? (idx ? 'hh.ov.inIndex' : 'hh.ov.in') : 'hh.ov.out')),
          s('rect', { x: MA.geom.px(bx.at(j)), y: MA.geom.px(by.at(i)), width: MA.geom.px(bx.bandwidth), height: MA.geom.px(by.bandwidth), rx: '2' })));
      });
    });
    return root;
  }

  function stepOverlap(host, api) {
    return api.get('/overlap').then(function (env) {
      var d = env.data || {};
      var levelSel = SEL({ id: 'hh-ov-level', 'class': 'input', onchange: function () { ovLevel = levelSel.value; } },
        [{ value: 'study', label: t('hh.ov.level.study') }, { value: 'report', label: t('hh.ov.level.report') }], d.level || ovLevel);
      var mxHost = h('div', { 'class': 'hh-mx-host', id: 'hh-mx-host' });
      function paintMx() { MA.dom.mount(mxHost, matrix(d)); }
      var toggle = h('button', { type: 'button', 'class': 'btn btn-sm', id: 'hh-mx-toggle', 'aria-pressed': matrixAsTable ? 'true' : 'false', onclick: function () {
        matrixAsTable = !matrixAsTable;
        toggle.setAttribute('aria-pressed', matrixAsTable ? 'true' : 'false');
        paintMx();
      } }, t('hh.ov.asTable'));
      MA.dom.mount(host,
        h('div', { 'class': 'toolbar' }, F(t('hh.ov.levelLabel'), levelSel),
          h('button', { type: 'button', 'class': 'btn' + (d.exists ? '' : ' btn-primary'), id: 'hh-overlap', onclick: function () { api.run('overlap', { level: levelSel.value, csv: true }); } }, t('hh.ov.run'))),
        !d.exists ? EMPTY('hh.ov.none') : [
          h('div', { 'class': 'hh-tiles' },
            h('div', { 'class': 'hh-tile', id: 'hh-cca' }, h('div', { 'class': 'hh-tile-k' }, t('hh.ov.cca', { level: t('hh.ov.level.' + (d.level || 'study')) })),
              h('div', { 'class': 'hh-tile-v num' }, d.cca_text ? d.cca_text + '%' : '—'), bandBadge(d.band)),
            h('div', { 'class': 'hh-tile' }, h('div', { 'class': 'hh-tile-k' }, 'N · r · c'), h('div', { 'class': 'hh-tile-v num' }, String(d.N) + ' · ' + String(d.r) + ' · ' + String(d.c)),
              h('div', { 'class': 'muted hh-small' }, t('hh.ov.nrc'))),
            d.wcca_text ? h('div', { 'class': 'hh-tile' }, h('div', { 'class': 'hh-tile-k' }, 'wCCA'), h('div', { 'class': 'hh-tile-v num' }, d.wcca_text + '%')) : null),
          d.notes ? h('p', { 'class': 'hh-notes' }, pick(d.notes)) : null,
          h('p', { 'class': 'muted' }, t('hh.ov.bands')),
          h('h3', { i18n: 'hh.ov.pairsTitle' }),
          h('div', { 'class': 'hh-heat-wrap' }, pairHeatmap(d)),
          (d.pairs || []).length ? h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact hh-pairs', id: 'hh-pairs' },
            h('thead', null, h('tr', null, ['a', 'b', 'shared', 'na', 'nb', 'cca', 'band'].map(function (k) { return h('th', { scope: 'col', 'class': /^(shared|na|nb|cca)$/.test(k) ? 'num' : null }, t('hh.ov.col.' + k)); }))),
            h('tbody', null, d.pairs.map(function (p) {
              var m = d.review_meta || {};
              return h('tr', null, h('td', null, (m[p.a] || {}).label || p.a), h('td', null, (m[p.b] || {}).label || p.b), h('td', { 'class': 'num' }, String(p.shared)),
                h('td', { 'class': 'num' }, p.n_a === undefined ? '—' : String(p.n_a)), h('td', { 'class': 'num' }, p.n_b === undefined ? '—' : String(p.n_b)),
                h('td', { 'class': 'num' }, p.cca_text ? p.cca_text + '%' : '—'), h('td', null, bandBadge(p.band)));
            })))) : null,
          h('div', { 'class': 'row' }, h('h3', { i18n: 'hh.ov.matrixTitle' }), toggle,
            d.n_rows > MAX_MATRIX_ROWS ? h('span', { 'class': 'muted' }, t('hh.ov.capped', { n: String(MAX_MATRIX_ROWS), total: String(d.n_rows) })) : null),
          mxHost]);
      if (d.exists) { paintMx(); }
    });
  }

  hh.steps.dedupe = stepDedupe;
  hh.steps.overlap = stepOverlap;
})();
