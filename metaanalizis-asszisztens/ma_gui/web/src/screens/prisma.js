/* screens/prisma.js — 2 PRISMA 2020: kézi bevitel élő P-ellenőrzéssel, folyamatábra (terv 3.5.14, 4.13, 6.2).
 *
 * HTTP API (3.4; az alakok a szerverrel egyeztetendők):
 *   GET /api/prisma[?refresh=1]  → {mode: manual|composer, path, source{kind, composer_version?, generated?, updated?, actor?},
 *                                   override{reason, decision_id}|null, flow (szk.prisma-flow/v1),
 *                                   check (motor prisma_check: {template, ok, summary, findings[], derived{}}),
 *                                   studies{path, studies, reports}, meta[{outcome_id, name, k, run_id}],
 *                                   cross[X014/X015…], composer_status[]} + ETag
 *   PUT /api/prisma/manual        ← {dry_run: true, flow}            → ugyanez, mentés nélkül (élő előnézet, 250 ms)
 *                                 ← {flow, override_reason?} + If-Match → mentés (02_szures/prisma_flow.json) + új ETag
 * A dobozértékek nyers szövegként mennek (a motor parszol, P001); a felület dobozértéket NEM számol: a levezetett
 * értékeket a motor check.derived-je adja, a hibás dobozt a P-megállapítások fields mezője jelöli ki.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var s = MA.dom.svg;
  var P = MA.proc;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };

  // k: szk.prisma-flow/v1 kulcs · f: a motor kanonikus mezőneve (findings.fields) · alt: további bemeneti név
  var BOXES = [
    { k: 'identified_databases', L: 'A1' }, { k: 'identified_registers', L: 'A2' },
    { k: 'dedup_removed', f: 'duplicates_removed', L: 'D1' }, { k: 'automation_removed', L: 'D2' },
    { k: 'removed_before_screening_n', f: 'other_removed', L: 'D3' },
    { k: 'screened', L: 'B' }, { k: 'excluded_screening', L: 'C' },
    { k: 'sought_for_retrieval', f: 'sought', L: 'E' }, { k: 'not_retrieved', L: 'F' },
    { k: 'assessed_eligibility', f: 'assessed', L: 'G' }, { k: 'excluded_eligibility', L: 'H' },
    { k: 'included_reports', alt: 'included', L: 'J' }, { k: 'included_studies', L: 'I' }
  ];
  var GROUPS = [['id', ['A1', 'A2']], ['rm', ['D1', 'D2', 'D3']], ['scr', ['B', 'C']], ['ret', ['E', 'F']], ['elig', ['G', 'H']], ['inc', ['J', 'I']]];
  var RKEY = 'excluded_eligibility_reasons';
  var BY_L = {}, BY_FIELD = {};
  BOXES.forEach(function (b) { BY_L[b.L] = b; BY_FIELD[b.k] = b; BY_FIELD[b.f || b.k] = b; if (b.alt) { BY_FIELD[b.alt] = b; } });
  BY_FIELD[RKEY] = BY_L.H;

  var S = null;   // képernyő-állapot (csak memóriában): {data, etag, values{L: szöveg}, reasons[{reason, n}], dirty, override, check}

  function str(v) { return v === null || v === undefined ? '' : String(v); }

  function fromFlow(flow) {
    var vals = {};
    BOXES.forEach(function (b) {
      var v = flow[b.k];
      if (v === undefined || v === null) { v = flow[b.f || b.k]; }
      if ((v === undefined || v === null) && b.alt) { v = flow[b.alt]; }
      vals[b.L] = str(v);
    });
    var r = flow[RKEY] || flow.reasons || null;
    var rows = [];
    if (Array.isArray(r)) {
      r.forEach(function (x) { rows.push({ reason: str(x.reason || x.label), n: str(x.count === undefined ? x.n : x.count) }); });
    } else if (r && typeof r === 'object') {
      Object.keys(r).forEach(function (k) { rows.push({ reason: k, n: str(r[k]) }); });
    }
    return { values: vals, reasons: rows };
  }

  /** A küldendő flow (nyers szövegek; üres → null). Az okok {ok: nyers szám} alakban (a motor _reasons_dict-je). */
  function toFlow() {
    var out = { schema: 'szk.prisma-flow/v1' };
    BOXES.forEach(function (b) { var v = S.values[b.L].trim(); out[b.k] = v === '' ? null : v; });
    var rs = {};
    S.reasons.forEach(function (r) { if (r.reason.trim()) { rs[r.reason.trim()] = r.n.trim() === '' ? null : r.n.trim(); } });
    out[RKEY] = S.reasons.length ? rs : null;
    return out;
  }

  function dupReasons() {
    var seen = {}, dup = {};
    S.reasons.forEach(function (r, i) { var k = r.reason.trim().toLowerCase(); if (k) { if (seen[k] !== undefined) { dup[i] = true; dup[seen[k]] = true; } else { seen[k] = i; } } });
    return dup;
  }

  function findings() { return S.check && Array.isArray(S.check.findings) ? S.check.findings : []; }

  /** doboz-betű → a rá vonatkozó megállapítások indexei (a motor fields mezője alapján) */
  function boxFindings() {
    var map = {};
    findings().forEach(function (f, i) {
      (f.fields || []).forEach(function (fl) {
        var b = BY_FIELD[fl];
        if (b && (map[b.L] || []).indexOf(i) < 0) { (map[b.L] = map[b.L] || []).push(i); }
      });
    });
    return map;
  }

  function worst(idx) {
    var fs = findings();
    var sev = null;
    (idx || []).forEach(function (i) {
      var v = fs[i].severity;
      if (v === 'error' || (v === 'warning' && sev !== 'error') || (!sev && v === 'info')) { sev = v; }
    });
    return sev;
  }

  function derived(b) {
    var d = S.check && S.check.derived;
    var v = d ? d[b.f || b.k] : null;
    return v === undefined || v === null ? null : String(v);
  }

  // ---------------------------------------------------------------- folyamatábra (csak dobozok és nyilak)
  var SYM = { error: '✖ ', warning: '⚠ ', info: 'ℹ ' };

  function lineOf(b, map) {
    var raw = S.values[b.L].trim();
    var d = raw === '' ? derived(b) : null;
    return (SYM[worst(map[b.L])] || '') + t('prisma.box.' + b.L) + ' (' + b.L + '): ' + (raw !== '' ? raw : (d !== null ? t('prisma.derived', { n: d }) : '—'));
  }

  function box(x, y, w, lines, letters, map, title) {
    var sev = worst([].concat.apply([], letters.map(function (L) { return map[L] || []; })));
    var hgt = 16 + 17 * (lines.length + (title ? 1 : 0));
    var g = s('g', { 'class': ['pf-box', sev && 'is-' + sev], 'data-box': letters.join(' ') },
      s('rect', { x: x, y: y, width: w, height: hgt, rx: 4 }));
    var yy = y + 20;
    if (title) { g.appendChild(s('text', { x: x + 10, y: yy, 'class': 'pf-box-title' }, title)); yy += 17; }
    lines.forEach(function (ln) {
      g.appendChild(s('text', { x: x + 10, y: yy }, ln.length > 52 ? ln.slice(0, 51) + '…' : ln));
      yy += 17;
    });
    return { g: g, h: hgt };
  }

  function diagram() {
    var map = boxFindings();
    var mid = MA.dom.uid('pf-arrow');
    var W = 720, LX = 10, RX = 390, BW = 320, y = 10;
    var root = s('svg', { viewBox: '0 0 ' + W + ' 100', 'class': 'pf-svg', role: 'img', 'aria-labelledby': mid + '-t' },
      s('title', { id: mid + '-t' }, t('prisma.flowTitle')),
      s('defs', null, s('marker', { id: mid, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
        s('path', { d: 'M0,0 L10,5 L0,10 z', 'class': 'pf-arrowhead' }))));
    var desc = [];
    function arrow(x1, y1, x2, y2) { root.appendChild(s('line', { x1: x1, y1: y1, x2: x2, y2: y2, 'class': 'pf-arrow', 'marker-end': 'url(#' + mid + ')' })); }
    function lines(Ls) { return Ls.map(function (L) { var ln = lineOf(BY_L[L], map); desc.push(ln); return ln; }); }
    var rows = [
      [['A1', 'A2'], 'prisma.flow.identified', ['D1', 'D2', 'D3'], 'prisma.flow.removed'],
      [['B'], null, ['C'], null],
      [['E'], null, ['F'], null],
      [['G'], null, ['H'], null],
      [['I', 'J'], 'prisma.flow.included', null, null]
    ];
    var prevBottom = null;
    rows.forEach(function (r) {
      var stage = r === rows[0] ? 'prisma.flow.stage.id' : (r === rows[4] ? 'prisma.flow.stage.inc' : (r === rows[1] ? 'prisma.flow.stage.scr' : null));
      if (stage) { root.appendChild(s('text', { x: LX, y: y + 12, 'class': 'pf-stage' }, t(stage))); y += 20; }
      var main = box(LX, y, BW, lines(r[0]), r[0], map, r[1] ? t(r[1]) : null);
      root.appendChild(main.g);
      var hgt = main.h;
      if (r[2]) {
        var extra = [];
        if (r[2][0] === 'H') {
          S.reasons.forEach(function (x) { if (x.reason.trim()) { var ln = '· ' + x.reason.trim() + ': ' + (x.n.trim() || '—'); extra.push(ln); desc.push(ln); } });
        }
        var side = box(RX, y, BW, lines(r[2]).concat(extra), r[2], map, r[3] ? t(r[3]) : null);
        root.appendChild(side.g);
        arrow(LX + BW, y + main.h / 2, RX - 2, y + main.h / 2);
        hgt = MA.geom.max(hgt, side.h);
      }
      if (prevBottom !== null) { arrow(LX + BW / 2, prevBottom, LX + BW / 2, y - 2); }
      prevBottom = y + main.h;
      y += hgt + 28;
    });
    root.setAttribute('viewBox', '0 0 ' + W + ' ' + String(y));
    root.appendChild(s('desc', null, desc.join('; ')));
    return root;
  }

  // ---------------------------------------------------------------- megállapítások
  function findingList() {
    var fs = findings();
    var c = S.data || {};
    var cross = Array.isArray(c.cross) ? c.cross : [];
    var cs = Array.isArray(c.composer_status) ? c.composer_status : [];
    return [
      fs.length ? h('ul', { 'class': 'item-list pf-findings' }, fs.map(function (f, i) {
        var b = (f.fields || []).map(function (x) { return BY_FIELD[x]; }).filter(function (x) { return !!x; })[0];
        return h('li', { 'class': 'item', id: 'pf-f-' + i, dataset: { code: f.code } },
          MA.ui.badge(P.sevKind(f.severity), f.code),
          h('span', { 'class': 'item-title' }, pick(f.title)),
          f.detail ? h('span', { 'class': 'item-detail' }, pick(f.detail)) : null,
          h('span', { 'class': 'item-actions' },
            b && S.editable ? h('button', { type: 'button', 'class': 'btn btn-sm pf-goto', onclick: function () { var el = MA.dom.$('#pf-in-' + b.L); if (el) { el.focus(); } } },
              t('prisma.goto', { box: b.L })) : null,
            MA.why.button({ kb: f.code, code: f.code, title: f.title, detail: f.detail, advice: f.advice, source: f.source }, { compact: true })));
      })) : MA.ui.emptyState('prisma.noFindings'),
      cross.length ? h('div', { 'class': 'pf-cross' }, h('h3', { i18n: 'prisma.cross' }), h('ul', { 'class': 'item-list' }, cross.map(function (x) {
        return h('li', { 'class': 'item', dataset: { code: x.code } }, MA.ui.badge(P.sevKind(x.severity), x.code), h('span', { 'class': 'item-title' }, pick(x.title)),
          x.detail ? h('span', { 'class': 'item-detail muted' }, pick(x.detail)) : null);
      }))) : null,
      cs.length ? h('div', { 'class': 'pf-cross' }, h('h3', { i18n: 'prisma.composerStatus' }),
        h('ul', { 'class': 'item-list' }, cs.map(function (x) { return h('li', { 'class': 'item' }, MA.ui.badge('warning', null), ' ', pick(x)); }))) : null
    ];
  }

  // ---------------------------------------------------------------- űrlap
  function numInput(b) {
    var inp = h('input', { type: 'text', inputmode: 'numeric', autocomplete: 'off', id: 'pf-in-' + b.L, 'class': 'pf-num', value: S.values[b.L],
      readOnly: !S.editable, 'aria-readonly': S.editable ? null : 'true', dataset: { box: b.L },
      oninput: function () { S.values[b.L] = inp.value; changed(); } });
    return P.field(t('prisma.box.' + b.L) + ' (' + b.L + ')', inp);
  }

  function markDups() {
    var dup = dupReasons();
    S.reasons.forEach(function (r, i) { var el = MA.dom.$('#pf-r-' + i); if (el) { el.setAttribute('aria-invalid', dup[i] ? 'true' : 'false'); } });
    var note = MA.dom.$('#pf-dup');
    if (note) { note.hidden = !Object.keys(dup).length; }
    saveState();
  }

  function saveState() {
    if (!S.els.save) { return; }
    var dup = Object.keys(dupReasons()).length > 0;
    S.els.save.disabled = !S.dirty || dup;
    S.els.saveWhy.textContent = dup ? t('prisma.reason.dup') : (S.dirty ? t('prisma.unsaved') : t('prisma.saved'));
  }

  function reasonRows(host) {
    MA.dom.mount(host, S.reasons.map(function (r, i) {
      var name = h('input', { type: 'text', id: 'pf-r-' + i, 'class': 'pf-reason', value: r.reason, readOnly: !S.editable,
        'aria-label': t('prisma.reason.label', { n: String(i + 1) }), oninput: function () { r.reason = name.value; changed(); } });
      var num = h('input', { type: 'text', inputmode: 'numeric', id: 'pf-rn-' + i, 'class': 'pf-num', value: r.n, readOnly: !S.editable,
        'aria-label': t('prisma.reason.count', { n: String(i + 1) }), oninput: function () { r.n = num.value; changed(); } });
      return h('li', { 'class': 'pf-reason-row' }, name, num,
        S.editable ? h('button', { type: 'button', 'class': 'btn-icon', 'aria-label': t('prisma.reason.remove', { n: String(i + 1) }),
          onclick: function () { S.reasons.splice(i, 1); reasonRows(host); changed(); var f = MA.dom.$('#pf-add-reason'); if (f) { f.focus(); } } }, '×') : null);
    }), h('li', { 'class': 'pf-dup', id: 'pf-dup', role: 'note', hidden: true }, MA.ui.badge('warning', t('prisma.reason.dup'))));
    markDups();
  }

  function form(ctx) {
    var host = h('ul', { 'class': 'pf-reasons', id: 'pf-reasons' });
    reasonRows(host);
    S.els.reasons = host;
    S.els.save = h('button', { type: 'button', 'class': 'btn btn-primary', id: 'pf-save', onclick: function () { save(ctx); } }, t('prisma.save'));
    S.els.saveWhy = h('span', { 'class': 'muted', id: 'pf-save-why' });
    return h('section', { 'class': 'panel', id: 'pf-form', 'aria-labelledby': 'pf-form-h' },
      h('h2', { 'class': 'panel-title', id: 'pf-form-h', i18n: S.editable ? 'prisma.formManual' : 'prisma.formComposer' }),
      S.override ? h('p', { 'class': 'pf-override' }, MA.ui.badge('info', t('prisma.overrideOn')), ' ', S.override) : null,
      h('div', { 'class': 'pf-groups' }, GROUPS.map(function (g) {
        return h('fieldset', { 'class': ['pf-group', g[0] === 'elig' && 'is-wide'] }, h('legend', { i18n: 'prisma.group.' + g[0] }),
          g[1].map(function (L) { return numInput(BY_L[L]); }),
          g[0] === 'elig' ? h('div', { 'class': 'pf-rbox' }, h('h3', { 'class': 'pf-rh', i18n: 'prisma.reasons' }), host,
            S.editable ? h('button', { type: 'button', 'class': 'btn btn-sm', id: 'pf-add-reason', onclick: function () {
              S.reasons.push({ reason: '', n: '' });
              reasonRows(host);
              changed();
              var i = MA.dom.$('#pf-r-' + (S.reasons.length - 1));
              if (i) { i.focus(); }
            } }, t('prisma.reason.add')) : null) : null);
      })),
      S.editable ? h('div', { 'class': 'toolbar' }, S.els.save,
        h('button', { type: 'button', 'class': 'btn btn-ghost', id: 'pf-discard', onclick: function () { reload(ctx, false); } }, t('prisma.discard')),
        S.els.saveWhy) : null);
  }

  // ---------------------------------------------------------------- frissítés (attribútumok helyben — a fókusz nem vész el)
  function paint() {
    var map = boxFindings();
    BOXES.forEach(function (b) {
      var el = MA.dom.$('#pf-in-' + b.L);
      if (!el) { return; }
      var idx = map[b.L] || [];
      var sev = worst(idx);
      el.setAttribute('aria-invalid', sev === 'error' ? 'true' : 'false');
      el.classList.toggle('is-warning', sev === 'warning');
      if (idx.length) { el.setAttribute('aria-describedby', idx.map(function (i) { return 'pf-f-' + i; }).join(' ')); } else { el.removeAttribute('aria-describedby'); }
    });
    var sum = (S.check && S.check.summary) || {};
    MA.dom.mount(S.els.sum, ['error', 'warning', 'info'].map(function (k) {
      return typeof sum[k] === 'number' ? MA.ui.badge(k, String(sum[k]), { srLabel: t('ov.sev.' + k) }) : null;
    }));
    MA.dom.mount(S.els.flow, diagram());
    MA.dom.mount(S.els.list, findingList());
    saveState();
  }

  function changed() {
    S.dirty = true;
    markDups();
    MA.dom.mount(S.els.flow, diagram());
    S.els.status.textContent = t('prisma.checking');
    S.debounced();
  }

  function check() {
    var my = S;
    my.run(function (seq, sig) {
      return MA.api.put('/api/prisma/manual', { dry_run: true, flow: toFlow() }, { clientSeq: seq, signal: sig, toast: false });
    }).then(function (env) {
      if (!env || S !== my) { return; }
      S.check = (env.data || {}).check || null;
      S.els.status.textContent = t('prisma.checked');
      paint();
    }, function (err) {
      if (S !== my) { return; }
      S.els.status.textContent = t('prisma.checkFailed', { msg: err.message || err.code });
    });
  }

  function save(ctx) {
    var body = { flow: toFlow() };
    if (S.override) { body.override_reason = S.override; }
    S.els.save.disabled = true;
    return MA.api.put('/api/prisma/manual', body, { ifMatch: S.etag }).then(function (env) {
      if (!ctx.alive()) { return; }
      MA.ui.toast({ kind: 'success', title: t('prisma.savedToast') });
      take(env);
      keep();
      ctx.rerender();
    }, function () { if (ctx.alive()) { paint(); } });
  }

  function take(env) {
    var d = env.data || {};
    var f = fromFlow(d.flow || {});
    S.data = d;
    S.etag = env.etag;
    S.values = f.values;
    S.reasons = f.reasons;
    S.check = d.check || null;
    S.dirty = false;
    S.override = null;
    S.editable = d.mode !== 'composer';
  }

  function reload(ctx, refresh) {
    S.dirty = false;
    return MA.api.get('/api/prisma', { query: refresh ? { refresh: 1 } : null, signal: ctx.signal }).then(function (env) {
      if (!ctx.alive()) { return; }
      take(env);
      keep();
      ctx.rerender();
    });
  }

  function overrideDialog(ctx) {
    var ta = h('textarea', { id: 'pf-override-reason', rows: 3, 'class': 'input' });
    var err = h('p', { 'class': 'pf-err', role: 'alert', hidden: true, i18n: 'prisma.override.required' });
    MA.ui.modal({
      title: t('prisma.override.title'), size: 'md',
      body: [h('p', { i18n: 'prisma.override.body' }), P.field(t('prisma.override.reason'), ta), err],
      actions: [{ label: t('common.cancel'), kind: 'ghost' }, { label: t('prisma.override.ok'), kind: 'primary', onClick: function () {
        if (!ta.value.trim()) { err.hidden = false; ta.setAttribute('aria-invalid', 'true'); ta.focus(); return false; }
        S.override = ta.value.trim();
        S.editable = true;
        S.dirty = true;
        keep();
        ctx.rerender();
        return true;
      } }]
    });
  }

  /** a piszkozat megőrzése újrarajzoláshoz (nyelvváltás, ⟳) — csak memóriában */
  function keep() {
    MA.store.set('process.prisma', { data: S.data, etag: S.etag, values: S.values, reasons: S.reasons, dirty: S.dirty, override: S.override, check: S.check, editable: S.editable });
  }

  function render(root, ctx) {
    var kept = MA.store.get('process.prisma');
    MA.store.set('process.prisma', null);
    var p = kept ? Promise.resolve(null) : MA.api.get('/api/prisma', { signal: ctx.signal });
    root.appendChild(MA.ui.spinner());
    return p.then(function (env) {
      if (!ctx.alive()) { return; }
      S = { els: {}, run: MA.api.latest() };
      if (kept) { Object.assign(S, kept); } else { take(env); }
      S.debounced = MA.ui.debounce(check, 250);
      var mine = S;
      ctx.onCleanup(function () { mine.debounced.cancel(); if (S === mine) { if (S.dirty) { keep(); } S = null; } });
      var d = S.data;
      var src = d.source || {};
      var composer = d.mode === 'composer';
      ctx.setTitle(t(composer ? 'prisma.mode.composer' : 'prisma.mode.manual'));
      S.els.sum = h('span', { 'class': 'pf-sum', id: 'pf-summary' });
      S.els.status = h('span', { 'class': 'muted pf-status', role: 'status', 'aria-live': 'polite' });
      S.els.flow = h('div', { 'class': 'pf-flow', id: 'pf-flow' });
      S.els.list = h('div', { id: 'pf-list' });
      var st = d.studies || {};
      var meta = Array.isArray(d.meta) ? d.meta : [];
      MA.dom.mount(root,
        h('section', { 'class': 'panel pf-head', id: 'pf-head', 'aria-label': t('prisma.source') },
          h('p', { 'class': 'row' },
            MA.ui.badge(composer ? 'info' : 'neutral', t(composer ? 'prisma.mode.composer' : 'prisma.mode.manual')),
            h('span', { 'class': 'pf-src' }, composer
              ? t('prisma.src.composer', { project: src.project || '—', ts: MA.i18n.ts(src.generated), version: src.composer_version || '—' })
              : t('prisma.src.manual', { path: d.path || '02_szures/prisma_flow.json', ts: MA.i18n.ts(src.updated), actor: src.actor || '—' })),
            h('button', { type: 'button', 'class': 'btn btn-sm', id: 'pf-refresh', onclick: function () {
              if (S.dirty) { MA.ui.confirm({ title: t('prisma.unsavedTitle'), message: t('prisma.unsavedBody'), danger: true }).then(function (ok) { if (ok) { reload(ctx, true); } }); } else { reload(ctx, true); }
            } }, t('prisma.refresh')),
            S.els.sum, S.els.status),
          d.override && d.override.reason ? h('p', { 'class': 'pf-override' }, MA.ui.badge('info', t('prisma.overrideOn')), ' ', d.override.reason,
            d.override.decision_id ? h('span', { 'class': 'muted' }, ' (#' + String(d.override.decision_id) + ')') : null) : null,
          h('p', { 'class': 'pf-incl' },
            t('prisma.incl', { i: st.studies === undefined ? '—' : String(st.studies), j: st.reports === undefined ? '—' : String(st.reports), path: st.path || '03_adatok/studies.json' }),
            meta.map(function (m) { return h('span', { 'class': 'pf-meta' }, ' · ', t('prisma.inMeta', { k: typeof m.k === 'number' ? String(m.k) : '—', outcome: m.outcome_id || '' })); })),
          h('div', { 'class': 'toolbar' },
            h('a', { 'class': 'btn', href: MA.app.href('studies'), id: 'pf-studies' }, t('prisma.studiesMap')),
            composer && !S.override ? h('button', { type: 'button', 'class': 'btn', id: 'pf-override', onclick: function () { overrideDialog(ctx); } }, t('prisma.override.button')) : null)),
        h('div', { 'class': 'grid-2' },
          h('section', { 'class': 'panel', 'aria-labelledby': 'pf-flow-h' }, h('h2', { 'class': 'panel-title', id: 'pf-flow-h', i18n: 'prisma.flow' }), S.els.flow),
          h('section', { 'class': 'panel', 'aria-labelledby': 'pf-list-h' }, h('h2', { 'class': 'panel-title', id: 'pf-list-h', i18n: 'prisma.findings' }), S.els.list)),
        form(ctx));
      paint();
      if (S.dirty && S.editable) { S.debounced(); }
    });
  }

  function onLeave(ctx, info) {
    if (!S || !S.dirty || info.reason !== 'navigate') { return true; }
    return MA.ui.confirm({ title: t('prisma.unsavedTitle'), message: t('prisma.unsavedBody'), okLabel: t('prisma.leave'), danger: true }).then(function (ok) {
      if (ok) { MA.store.set('process.prisma', null); if (S) { S.dirty = false; } }
      return ok;
    });
  }

  MA.selftest.register('prisma: a motor mezőnevei (findings.fields) dobozra képeződnek', function (tt) {
    ['identified_databases', 'identified_registers', 'duplicates_removed', 'automation_removed', 'other_removed', 'screened',
      'excluded_screening', 'sought', 'not_retrieved', 'assessed', 'excluded_eligibility', 'included_reports', 'included_studies',
      'dedup_removed', 'sought_for_retrieval', 'assessed_eligibility', 'included'].forEach(function (f) { tt.ok(!!BY_FIELD[f], f); });
    tt.eq(BY_FIELD[RKEY].L, 'H', 'okok → H');
    tt.eq(BOXES.length, 13, '13 doboz (A1–J)');
  });

  MA.app.registerScreen({ id: 'prisma', title_key: 'tab.prisma', workspace: 'process', tab: 'prisma', order: 10, render: render, onLeave: onLeave });
})();
