/* screens/log.js — 7 Napló, kapuk, project audit, KB-kereső (terv 3.5.15, 6.4, 6.6, 7.7).
 *
 * HTTP API (3.4; ma_gui/routes/log.py, kb.py):
 *   GET  /api/log/<finding|decision|checkpoint|grade|run>?status=&severity=&stage=  → {kind, items[]}
 *   GET  /api/log/activity?limit=     → {items[], total, verify{ok, first_bad_seq, message}, head}
 *   POST /api/log/finding  {agent:'user', severity, title, detail?, stage?, evidence?, kb_refs?}
 *   POST /api/log/decision {agent:'user', decision, rationale, stage?, kb_refs?, alternatives?}
 *   POST /api/log/resolve  {id, status: fixed|wontfix|invalid|open, resolution}
 *   POST /api/log/checkpoint {agent:'user', stage: S00–S14|FINAL, verdict, summary?} → 409 GATE_BLOCKED {blockers[]}
 *   GET  /api/audit/project           → szk.ma.project-audit/v1 (X-szabályok, javasolt parancs)
 *   GET  /api/kb/search?q=&scope=     → {results{rule|knowledge|chunk: […]}, note}; GET /api/kb/item/<id> (MA.why.openItem)
 * A kapu a motoré (projekt.checkpoint): a felület előre lekérdezi a blokkolókat és megnevezi az okot; ha a motor
 * mégis elutasít (verseny egy ágenssel), az üzenete szó szerint jelenik meg. Az SQLite-ba a felület nem ír.
 * Útvonal-paraméterek: tab, finding (kiemelt tétel), stage, kb (KB-tétel megnyitása), q (KB-keresés), focus=kb.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var P = MA.proc;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };

  var TABS = ['finding', 'decision', 'checkpoint', 'grade', 'run', 'audit', 'activity'];
  var VERDICTS = ['PASS', 'PASS_WITH_FIXES', 'FAIL'];
  var SCOPES = ['rule', 'knowledge', 'chunk'];
  var L = null;          // képernyő-állapot (csak memóriában)
  var filters = { finding: { status: 'open', severity: '', stage: '', agent: '' }, decision: { status: '', stage: '', agent: '' } };

  function short(v, n) { v = v === null || v === undefined ? '' : String(v); return v.length > n ? v.slice(0, n) + '…' : v; }
  function opts(list, allKey, prefix) {
    var o = list.map(function (v) { return { value: v, label: prefix ? t(prefix + v) : v }; });
    return allKey ? [{ value: '', label: t(allKey) }].concat(o) : o;
  }
  function stageOpts(allKey) { return opts(P.STAGES, allKey); }
  function kbCell(refsText) {
    var ids = P.refs(refsText);
    return ids.length ? h('span', { 'class': 'kb-cell' }, ids.map(function (id) { return MA.why.kbButton(id); })) : '—';
  }
  function table(id, cols, rows) {
    return h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: id },
      h('thead', null, h('tr', null, cols.map(function (c) { return h('th', { scope: 'col', i18n: 'log.col.' + c }); }))),
      h('tbody', null, rows.length ? rows : h('tr', null, h('td', { colspan: cols.length }, MA.ui.emptyState('log.empty'))))));
  }

  // ---------------------------------------------------------------- listák
  function filterBar(kind, reload) {
    var f = filters[kind];
    function sel(key, options) {
      var el = P.select({ id: 'log-f-' + key, onchange: function () { f[key] = el.value; reload(); } }, options, f[key]);
      return P.field(t('log.filter.' + key), el);
    }
    return h('div', { 'class': 'toolbar log-filters', role: 'group', 'aria-label': t('log.filters') },
      sel('stage', stageOpts('log.all')),
      kind === 'finding' ? sel('severity', opts(P.SEVERITIES, 'log.all', 'log.sev.')) : null,
      sel('status', kind === 'finding' ? opts(['open', 'resolved', 'fixed', 'wontfix', 'invalid'], 'log.all', 'log.status.')
        : opts(['active', 'superseded', 'reverted'], 'log.all', 'log.status.')),
      sel('agent', opts(P.AGENTS, 'log.all', 'log.agent.')));
  }

  function query(kind) {
    var f = filters[kind] || {};
    return { status: f.status || null, severity: f.severity || null, stage: f.stage || null };
  }

  function byAgent(kind, items) {
    var a = filters[kind] && filters[kind].agent;
    return a ? items.filter(function (x) { return x.agent === a; }) : items;
  }

  function findingRows(items) {
    return items.map(function (f) {
      var sel = String(L.params.finding || '') === String(f.id);
      var ids = P.refs(f.kb_refs);
      return h('tr', { 'class': sel ? 'is-selected' : null, dataset: { id: f.id }, tabindex: sel ? '-1' : null, id: 'log-row-' + f.id },
        h('td', { 'class': 'num' }, '#' + String(f.id)),
        h('td', null, MA.i18n.ts(f.ts)),
        h('td', null, f.stage_id || '—'),
        h('td', null, MA.ui.badge(P.sevKind(f.severity), t('log.sev.' + f.severity))),
        h('td', null, h('span', { 'class': 'log-title' }, f.title || ''), f.detail ? h('span', { 'class': 'log-detail muted' }, f.detail) : null,
          f.resolution ? h('span', { 'class': 'log-detail' }, t('log.resolution', { text: f.resolution })) : null),
        h('td', null, t('log.agent.' + f.agent)),
        h('td', null, MA.ui.badge(f.status === 'open' ? 'warning' : 'ok', t('log.status.' + f.status))),
        h('td', null, kbCell(f.kb_refs), ids.length ? MA.why.button({ kb: ids, title: f.title, detail: f.detail }, { compact: true }) : null),
        h('td', null, h('button', { type: 'button', 'class': 'btn btn-sm log-resolve', onclick: function (ev) { resolveDialog(f, ev.currentTarget); } },
          t(f.status === 'open' ? 'log.resolve' : 'log.reopen'))));
    });
  }

  function decisionRows(items) {
    return items.map(function (d) {
      return h('tr', { dataset: { id: d.id } },
        h('td', { 'class': 'num' }, '#' + String(d.id)), h('td', null, MA.i18n.ts(d.ts)), h('td', null, d.stage_id || '—'),
        h('td', null, t('log.agent.' + d.agent)),
        h('td', null, h('span', { 'class': 'log-title' }, d.decision || ''), d.alternatives ? h('span', { 'class': 'log-detail muted' }, t('log.alternatives', { text: d.alternatives })) : null),
        h('td', null, d.rationale || '—'), h('td', null, kbCell(d.kb_refs)),
        h('td', null, MA.ui.badge(d.status === 'active' ? 'ok' : 'neutral', t('log.status.' + (d.status || 'active')))));
    });
  }

  function checkpointRows(items) {
    return items.map(function (c) {
      return h('tr', { dataset: { id: c.id, stage: c.stage_id } },
        h('td', { 'class': 'num' }, '#' + String(c.id)), h('td', null, MA.i18n.ts(c.ts)), h('td', null, c.stage_id),
        h('td', null, MA.ui.badge(c.verdict === 'FAIL' ? 'error' : 'ok', t('overview.verdict.' + c.verdict))),
        h('td', null, t('log.agent.' + c.agent)), h('td', null, c.summary || '—'));
    });
  }

  var CERT = { high: 'grade.high', moderate: 'grade.moderate', low: 'grade.low', 'very low': 'grade.verylow' };
  function gradeRows(items) {
    return items.map(function (g) {
      return h('tr', { dataset: { outcome: g.outcome } },
        h('td', null, g.outcome), h('td', null, CERT[g.certainty] ? t(CERT[g.certainty]) : String(g.certainty || '—')),
        h('td', { 'class': 'num' }, g.k === null || g.k === undefined ? '—' : String(g.k)),
        h('td', { 'class': 'num' }, g.participants === null || g.participants === undefined ? '—' : String(g.participants)),
        h('td', null, g.effect || '—'),
        h('td', null, ['risk_of_bias', 'inconsistency', 'indirectness', 'imprecision', 'publication_bias'].map(function (k) {
          return g[k] ? h('span', { 'class': 'log-detail' }, t('log.grade.' + k) + ': ' + g[k]) : null;
        })),
        h('td', null, MA.i18n.ts(g.ts)));
    });
  }

  function runRows(items) {
    return items.map(function (r) {
      return h('tr', { dataset: { id: r.id } },
        h('td', { 'class': 'num' }, '#' + String(r.id)), h('td', null, MA.i18n.ts(r.ts)), h('td', null, h('code', { 'class': 'cmd-inline' }, short(r.command, 120))),
        h('td', null, h('code', null, short(r.data_sha256, 12))), h('td', null, r.outdir || '—'), h('td', null, r.summary || '—'));
    });
  }

  function auditList(env) {
    var d = (env && env.data) || {};
    var items = Array.isArray(d.findings) ? d.findings : [];
    if (!items.length) { return MA.ui.emptyState('overview.noNextSteps'); }
    return h('ul', { 'class': 'item-list', id: 'log-audit' }, items.map(function (f) {
      var cmd = Array.isArray(f.suggested_command) ? 'python ' + f.suggested_command.join(' ') : null;
      return h('li', { 'class': 'item', dataset: { code: f.code } },
        MA.ui.badge(P.sevKind(f.severity), f.code),
        h('span', { 'class': 'item-stage' }, [f.stage, f.outcome].filter(function (x) { return !!x; }).join(' · ')),
        h('span', { 'class': 'item-title' }, pick(f.title)),
        f.detail ? h('span', { 'class': 'item-detail muted' }, pick(f.detail)) : null,
        Array.isArray(f.artifacts) && f.artifacts.length ? h('span', { 'class': 'item-detail' }, t('log.artifacts', { list: f.artifacts.join(', ') })) : null,
        cmd ? h('code', { 'class': 'cmd-inline' }, cmd) : null,
        h('span', { 'class': 'item-actions' }, cmd ? MA.ui.copyButton(function () { return cmd; }) : null,
          kbCell(f.kb_refs), MA.why.button({ kb: [f.code].concat(P.refs(f.kb_refs)), code: f.code, title: f.title, detail: f.detail }, { compact: true })));
    }));
  }

  function activityView(env) {
    var d = env.data || {};
    var v = d.verify || {};
    var items = Array.isArray(d.items) ? d.items.slice().reverse() : [];
    return [
      h('p', { id: 'log-chain', role: 'status' }, v.ok === false
        ? MA.ui.badge('error', t('log.chain.broken', { seq: String(v.first_bad_seq === null || v.first_bad_seq === undefined ? '?' : v.first_bad_seq) }))
        : MA.ui.badge('ok', t('log.chain.ok', { n: typeof d.total === 'number' ? String(d.total) : '—' })),
      v.message ? ' ' + v.message : null),
      table('log-activity', ['seq', 'ts', 'actor', 'action', 'argv', 'result'], items.map(function (a) {
        return h('tr', null, h('td', { 'class': 'num' }, String(a.seq)), h('td', null, MA.i18n.ts(a.ts)), h('td', null, a.actor || '—'), h('td', null, a.action || '—'),
          h('td', null, Array.isArray(a.argv) ? h('code', { 'class': 'cmd-inline' }, short(a.argv.join(' '), 120)) : '—'),
          h('td', null, a.result ? short(a.result.summary || (a.result.exit_code === undefined ? '' : 'exit ' + String(a.result.exit_code)), 120) : '—'));
      }))];
  }

  // ---------------------------------------------------------------- új tétel űrlapok
  function formToggle(id, labelKey, build) {
    var host = h('div', { 'class': 'log-form-host', id: id + '-host', hidden: true });
    var btn = h('button', { type: 'button', 'class': 'btn', id: id + '-toggle', 'aria-expanded': 'false', 'aria-controls': id + '-host', onclick: function () {
      var open = host.hidden;
      host.hidden = !open;
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) { MA.dom.mount(host, build(function () { host.hidden = true; btn.setAttribute('aria-expanded', 'false'); btn.focus(); })); var f = MA.dom.focusables(host)[0]; if (f) { f.focus(); } }
    } }, t(labelKey));
    return [btn, host];
  }

  function required(el, err) {
    var bad = !el.value.trim();
    el.setAttribute('aria-invalid', bad ? 'true' : 'false');
    if (bad) { err.hidden = false; el.focus(); }
    return !bad;
  }

  function postWrite(path, body, okKey, err, done) {
    return MA.api.post(path, body, { toast: false }).then(function (env) {
      var d = env.data || {};
      var w = Array.isArray(d.warnings) ? d.warnings : [];
      MA.ui.toast({ kind: w.length ? 'warning' : 'success', title: t(okKey, { id: String(d.id) }), details: w });
      done();
      loadTab(L.tab);
      refreshCounts();
    }, function (e) {
      err.hidden = false;
      err.textContent = t('err.' + e.code) + (e.message ? ' — ' + e.message : '');
    });
  }

  function findingForm(close) {
    var sev = P.select({ id: 'nf-sev' }, opts(P.SEVERITIES, null, 'log.sev.'), 'major');
    var stage = P.select({ id: 'nf-stage' }, stageOpts('log.none'), filters.finding.stage || '');
    var title = h('input', { type: 'text', id: 'nf-title' });
    var detail = h('textarea', { id: 'nf-detail', rows: 2 });
    var evid = h('input', { type: 'text', id: 'nf-evidence' });
    var kb = h('input', { type: 'text', id: 'nf-kb', placeholder: 'V011, D-S07-004' });
    var err = h('p', { 'class': 'pf-err', role: 'alert', hidden: true, i18n: 'log.required' });
    return h('form', { 'class': 'log-form', id: 'nf-form', onsubmit: function (ev) {
      ev.preventDefault();
      if (!required(title, err)) { return; }
      postWrite('/api/log/finding', { agent: 'user', severity: sev.value, title: title.value.trim(), detail: detail.value.trim() || null,
        stage: stage.value || null, evidence: evid.value.trim() || null, kb_refs: P.refs(kb.value) }, 'log.findingSaved', err, close);
    } },
    h('div', { 'class': 'pf-row' }, P.field(t('log.f.severity'), sev), P.field(t('log.f.stage'), stage), P.field(t('log.f.kb'), kb)),
    P.field(t('log.f.title'), title), P.field(t('log.f.detail'), detail), P.field(t('log.f.evidence'), evid), err,
    h('div', { 'class': 'toolbar' }, h('button', { type: 'submit', 'class': 'btn btn-primary', id: 'nf-submit' }, t('log.save')),
      h('button', { type: 'button', 'class': 'btn btn-ghost', onclick: close }, t('common.cancel'))));
  }

  function decisionForm(close) {
    var dec = h('textarea', { id: 'nd-decision', rows: 2 });
    var rat = h('textarea', { id: 'nd-rationale', rows: 2 });
    var stage = P.select({ id: 'nd-stage' }, stageOpts('log.none'), filters.decision.stage || '');
    var kb = h('input', { type: 'text', id: 'nd-kb', placeholder: 'D-S12-006' });
    var alt = h('input', { type: 'text', id: 'nd-alt' });
    var err = h('p', { 'class': 'pf-err', role: 'alert', hidden: true, i18n: 'log.required' });
    return h('form', { 'class': 'log-form', id: 'nd-form', onsubmit: function (ev) {
      ev.preventDefault();
      if (!required(dec, err) || !required(rat, err)) { return; }
      postWrite('/api/log/decision', { agent: 'user', decision: dec.value.trim(), rationale: rat.value.trim(), stage: stage.value || null,
        kb_refs: P.refs(kb.value), alternatives: alt.value.trim() || null }, 'log.decisionSaved', err, close);
    } },
    P.field(t('log.f.decision'), dec), P.field(t('log.f.rationale'), rat),
    h('div', { 'class': 'pf-row' }, P.field(t('log.f.stage'), stage), P.field(t('log.f.kb'), kb), P.field(t('log.f.alternatives'), alt)), err,
    h('div', { 'class': 'toolbar' }, h('button', { type: 'submit', 'class': 'btn btn-primary', id: 'nd-submit' }, t('log.save')),
      h('button', { type: 'button', 'class': 'btn btn-ghost', onclick: close }, t('common.cancel'))));
  }

  function resolveDialog(f) {
    var reopen = f.status !== 'open';
    var name = MA.dom.uid('rs');
    var choices = reopen ? ['open'] : ['fixed', 'wontfix', 'invalid'];
    var radios = choices.map(function (c, i) {
      var id = name + '-' + c;
      return h('label', { 'class': 'log-radio', htmlFor: id }, h('input', { type: 'radio', name: name, id: id, value: c, checked: i === 0 }), ' ', t('log.status.' + c));
    });
    var ta = h('textarea', { id: 'rs-text', rows: 3 });
    var err = h('p', { 'class': 'pf-err', role: 'alert', hidden: true, i18n: 'log.reasonRequired' });
    MA.ui.modal({
      title: t(reopen ? 'log.reopenTitle' : 'log.resolveTitle', { id: String(f.id) }), size: 'md',
      body: [h('p', { 'class': 'log-title' }, f.severity === 'blocker' ? MA.ui.badge('blocker', t('log.sev.blocker')) : null, ' ', f.title || ''),
        reopen ? null : h('fieldset', { 'class': 'log-radios' }, h('legend', { i18n: 'log.f.status' }), radios),
        P.field(t('log.f.resolution'), ta), err],
      actions: [{ label: t('common.cancel'), kind: 'ghost' }, { label: t(reopen ? 'log.reopen' : 'log.resolve'), kind: 'primary', onClick: function (close) {
        err.textContent = t('log.reasonRequired');
        if (!required(ta, err)) { return false; }
        var picked = MA.dom.$('input[name="' + name + '"]:checked');
        MA.api.post('/api/log/resolve', { id: f.id, status: picked ? picked.value : 'open', resolution: ta.value.trim() }, { toast: false }).then(function () {
          close('ok');
          MA.ui.toast({ kind: 'success', title: t('log.resolved', { id: String(f.id) }) });
          loadTab(L.tab);
          refreshCounts();
        }, function (e) {
          err.hidden = false;
          err.textContent = t('err.' + e.code) + (e.message ? ' — ' + e.message : '');
        });
        return false;
      } }]
    });
  }

  // ---------------------------------------------------------------- fülek
  function loadTab(kind) {
    if (!L) { return null; }
    var my = L;
    var host = L.els.panel;
    MA.dom.mount(host, MA.ui.spinner());
    P.pend(host, true);
    var top = [];
    if (kind === 'finding' || kind === 'decision') {
      top.push(filterBar(kind, function () { loadTab(kind); }));
      top.push(h('div', { 'class': 'toolbar' }, formToggle(kind === 'finding' ? 'nf' : 'nd', kind === 'finding' ? 'log.newFinding' : 'log.newDecision',
        kind === 'finding' ? findingForm : decisionForm)));
    }
    var path = kind === 'audit' ? '/api/audit/project' : '/api/log/' + kind;
    var q = kind === 'finding' || kind === 'decision' ? query(kind) : (kind === 'activity' ? { limit: 200 } : null);
    return MA.api.get(path, { query: q, signal: L.ctx.signal, toast: false }).then(function (env) {
      if (L !== my || L.tab !== kind) { return; }
      if (kind === 'audit') { L.audit = env; }
      var items = byAgent(kind, MA.api.list(env, kind + 's'));
      var body;
      if (kind === 'finding') { body = table('log-findings', ['id', 'ts', 'stage', 'severity', 'title', 'agent', 'status', 'kb', 'action'], findingRows(items)); }
      if (kind === 'decision') { body = table('log-decisions', ['id', 'ts', 'stage', 'agent', 'decision', 'rationale', 'kb', 'status'], decisionRows(items)); }
      if (kind === 'checkpoint') { body = table('log-checkpoints', ['id', 'ts', 'stage', 'verdict', 'agent', 'summary'], checkpointRows(items)); }
      if (kind === 'grade') { body = table('log-grades', ['outcome', 'certainty', 'k', 'participants', 'effect', 'domains', 'ts'], gradeRows(items)); }
      if (kind === 'run') { body = table('log-runs', ['id', 'ts', 'command', 'data', 'outdir', 'summary'], runRows(items)); }
      if (kind === 'audit') { body = auditList(env); }
      if (kind === 'activity') { body = activityView(env); }
      P.pend(host, false);
      MA.dom.mount(host, top, body);
      var row = L.params.finding ? MA.dom.$('#log-row-' + L.params.finding) : null;
      if (row && kind === 'finding' && !L.focused) { L.focused = true; row.focus(); }
    }, function (err) {
      if (L !== my) { return; }
      P.pend(host, false);
      MA.dom.mount(host, top, MA.ui.errorBox(err));
    });
  }

  function tabLabel(k) {
    var n = L.counts[k];
    return t('log.tab.' + k) + (n ? (k === 'finding' ? ' ⛔ ' : ' ') + String(n) : '');
  }

  function refreshCounts() {
    var my = L;
    var sig = L.ctx.signal;
    return Promise.all([
      MA.api.get('/api/log/finding', { query: { status: 'open', severity: 'blocker' }, signal: sig, toast: false }).then(function (env) { return MA.api.list(env, 'items'); }, function () { return null; }),
      MA.api.get('/api/audit/project', { signal: sig, toast: false }).then(function (env) { my.audit = env; return env; }, function () { return null; })
    ]).then(function (r) {
      if (L !== my) { return; }
      L.blockers = r[0] || [];
      L.counts.finding = r[0] ? r[0].length : 0;
      L.counts.audit = r[1] && r[1].data && Array.isArray(r[1].data.findings) ? r[1].data.findings.length : 0;
      MA.dom.$$('[role="tab"]', L.els.tabs).forEach(function (b) { b.textContent = tabLabel(b.dataset.tab); });
      gateCheck();
    });
  }

  // ---------------------------------------------------------------- kapu (ellenőrzőpont)
  function gateCheck() {
    if (!L || !L.els.gStage) { return; }
    var my = L;
    var stage = L.els.gStage.value, verdict = L.els.gVerdict.value;
    var btn = L.els.gSubmit;
    MA.dom.clear(L.els.gPre);
    btn.disabled = false;
    btn.removeAttribute('aria-describedby');
    if (verdict === 'FAIL') { return; }
    var q = { status: 'open', severity: 'blocker' };
    if (stage !== 'FINAL') { q.stage = stage; }
    MA.api.get('/api/log/finding', { query: q, signal: L.ctx.signal, toast: false }).then(function (env) {
      if (L !== my || L.els.gStage.value !== stage || L.els.gVerdict.value !== verdict) { return; }
      var bl = MA.api.list(env, 'items');
      var xerr = stage === 'FINAL' && L.audit && L.audit.data && Array.isArray(L.audit.data.findings)
        ? L.audit.data.findings.filter(function (f) { return f.severity === 'error'; }) : [];
      if (!bl.length && !xerr.length) {
        MA.dom.mount(L.els.gPre, MA.ui.badge('ok', t('log.gate.clear', { stage: stage })));
        return;
      }
      btn.disabled = true;
      btn.setAttribute('aria-describedby', 'log-gate-pre');
      MA.dom.mount(L.els.gPre,
        bl.length ? h('p', null, MA.ui.badge('blocker', t('log.gate.blockers', { n: String(bl.length), stage: stage })), ' ',
          bl.map(function (b) { return h('a', { 'class': 'log-blk', href: MA.app.href('log', { tab: 'finding', finding: b.id }) }, '#' + String(b.id) + ' ' + (b.stage_id || '') + ' ' + (b.title || '')); })) : null,
        xerr.length ? h('p', null, MA.ui.badge('error', t('log.gate.audit', { list: xerr.map(function (f) { return f.code; }).join(', ') }))) : null);
    }, function () { /* a kapu a motoré — az előzetes lekérdezés hibája nem tilt */ });
  }

  function gateSubmit() {
    var my = L;
    var body = { agent: 'user', stage: L.els.gStage.value, verdict: L.els.gVerdict.value, summary: L.els.gSummary.value.trim() || null };
    MA.dom.clear(L.els.gResult);
    L.els.gSubmit.disabled = true;
    return MA.api.post('/api/log/checkpoint', body, { quiet: ['GATE_BLOCKED'] }).then(function (env) {
      if (L !== my) { return; }
      var d = env.data || {};
      MA.dom.mount(L.els.gResult, h('p', { role: 'status' }, MA.ui.badge('ok', t('log.gate.saved', { id: String(d.id), stage: body.stage, verdict: body.verdict })),
        (d.warnings || []).map(function (w) { return h('span', { 'class': 'log-detail' }, w); })));
      L.els.gSummary.value = '';
      if (L.tab === 'checkpoint') { loadTab('checkpoint'); }
      gateCheck();
    }, function (e) {
      if (L !== my) { return; }
      L.els.gSubmit.disabled = false;
      if (e.code !== 'GATE_BLOCKED') { return; }
      var bl = e.details && Array.isArray(e.details.blockers) ? e.details.blockers : [];
      MA.dom.mount(L.els.gResult, h('div', { 'class': 'error-box log-gate-blocked', role: 'alert', id: 'log-gate-blocked' },
        MA.ui.badge('blocker', t('err.GATE_BLOCKED')),
        h('p', { 'class': 'log-msg' }, e.message || ''),
        bl.length ? h('ul', { 'class': 'item-list' }, bl.map(function (b) {
          return h('li', { 'class': 'item' }, MA.ui.badge('blocker', '#' + String(b.id)), h('span', { 'class': 'item-stage' }, b.stage || '—'),
            h('span', { 'class': 'item-title' }, b.title || ''), h('a', { 'class': 'btn btn-sm', href: MA.app.href('log', { tab: 'finding', finding: b.id }) }, t('overview.jump')));
        })) : null));
      refreshCounts();
    });
  }

  function gateSection() {
    var stage0 = L.params.stage && P.STAGES.indexOf(L.params.stage) >= 0 ? L.params.stage : 'S00';
    L.els.gStage = P.select({ id: 'log-gate-stage', onchange: gateCheck }, stageOpts(null), stage0);
    L.els.gVerdict = P.select({ id: 'log-gate-verdict', onchange: gateCheck }, opts(VERDICTS, null, 'overview.verdict.'), 'PASS');
    L.els.gSummary = h('input', { type: 'text', id: 'log-gate-summary' });
    L.els.gSubmit = h('button', { type: 'submit', 'class': 'btn btn-primary', id: 'log-gate-submit' }, t('log.gate.submit'));
    L.els.gPre = h('div', { id: 'log-gate-pre', 'class': 'log-gate-pre', 'aria-live': 'polite' });
    L.els.gResult = h('div', { id: 'log-gate-result' });
    return h('section', { 'class': 'panel', id: 'log-gate', 'aria-labelledby': 'log-gate-h' },
      h('h2', { 'class': 'panel-title', id: 'log-gate-h', i18n: 'log.gate.title' }),
      h('form', { 'class': 'pf-row', onsubmit: function (ev) { ev.preventDefault(); gateSubmit(); } },
        P.field(t('log.f.stage'), L.els.gStage), P.field(t('log.f.verdict'), L.els.gVerdict), P.field(t('log.f.summary'), L.els.gSummary),
        h('div', { 'class': 'pf-field' }, L.els.gSubmit)),
      L.els.gPre, L.els.gResult, h('p', { 'class': 'muted pf-note', i18n: 'log.gate.note' }));
  }

  // ---------------------------------------------------------------- KB-kereső
  function marked(text) {
    var parts = String(text || '').split(/(\[[^\]]*\])/);
    return parts.map(function (p) { return /^\[[^\]]*\]$/.test(p) ? h('mark', null, p.slice(1, -1)) : p; });
  }

  function kbResults(env) {
    var d = env.data || {};
    var res = d.results || {};
    var out = [];
    SCOPES.forEach(function (sc) {
      (Array.isArray(res[sc]) ? res[sc] : []).forEach(function (r) {
        var id = sc === 'chunk' ? (r.ref || r.id) : r.id;
        out.push(h('li', { 'class': 'item kb-hit', dataset: { scope: sc, id: id } },
          MA.ui.badge('neutral', String(id)),
          sc === 'rule' ? MA.why.strength(r.strength) : null,
          sc === 'knowledge' && r.kind ? MA.ui.badge('info', r.kind) : null,
          sc === 'chunk' ? MA.ui.badge('warning', pick(d.note) || t('kb.localOnly'), { title: t('kb.localOnlyTitle') }) : null,
          h('span', { 'class': 'item-title' }, sc === 'rule' ? r.condition : (sc === 'knowledge' ? r.title : r.source_id + (r.locator ? ' · ' + r.locator : ''))),
          h('span', { 'class': 'item-detail' }, sc === 'rule' ? short(r.recommendation, 240) : marked(r.snippet)),
          h('span', { 'class': 'item-actions' }, h('button', { type: 'button', 'class': 'btn btn-sm kb-open', onclick: function () { MA.why.openItem(String(id)); } }, t('kb.open')))));
      });
    });
    return out.length ? [h('p', { 'class': 'muted', role: 'status' }, t('kb.hits', { n: String(out.length), q: d.query || '' })), h('ul', { 'class': 'item-list', id: 'kb-results' }, out)]
      : h('p', { 'class': 'empty-state', role: 'status' }, t('kb.none', { q: d.query || '' }));
  }

  function kbSection() {
    var run = MA.api.latest();
    var input = h('input', { type: 'search', id: 'kb-q', autocomplete: 'off', value: L.params.q || L.params.kb || '', placeholder: t('kb.placeholder'),
      'aria-keyshortcuts': 'Control+K', oninput: function () { L.kbDeb(); } });
    var host = h('div', { id: 'kb-host', 'aria-live': 'polite' });
    var scope = L.params.kb && /^K-/.test(L.params.kb) ? 'knowledge' : (L.params.scope && SCOPES.indexOf(L.params.scope) >= 0 ? L.params.scope : 'rule');
    var name = MA.dom.uid('kbs');
    var radios = SCOPES.map(function (sc) {
      var id = 'kb-scope-' + sc;
      return h('label', { 'class': 'log-radio', htmlFor: id }, h('input', { type: 'radio', name: name, id: id, value: sc, checked: sc === scope,
        onchange: function () { search(); } }), ' ', t('kb.scope.' + sc));
    });
    function search() {
      var q = input.value.trim();
      var sc = (MA.dom.$('input[name="' + name + '"]:checked') || {}).value || 'rule';
      if (!q) { MA.dom.clear(host); return; }
      P.pend(host, true);
      run(function (seq, sig) { return MA.api.get('/api/kb/search', { query: { q: q, scope: sc, limit: 12 }, signal: sig, clientSeq: seq, toast: false }); })
        .then(function (env) { if (env && L) { P.pend(host, false); MA.dom.mount(host, kbResults(env)); } },
          function (e) { if (L) { P.pend(host, false); MA.dom.mount(host, MA.ui.errorBox(e)); } });
    }
    L.kbDeb = MA.ui.debounce(search, 250);
    L.kbSearch = search;
    return h('section', { 'class': 'panel', id: 'log-kb', 'aria-labelledby': 'log-kb-h' },
      h('h2', { 'class': 'panel-title', id: 'log-kb-h', i18n: 'kb.title' }),
      h('form', { role: 'search', 'class': 'pf-row', onsubmit: function (ev) { ev.preventDefault(); L.kbDeb.cancel(); search(); } },
        P.field(t('kb.query'), input),
        h('fieldset', { 'class': 'log-radios' }, h('legend', { i18n: 'kb.scope' }), radios)),
      h('p', { 'class': 'muted pf-note', i18n: 'kb.copyright' }),
      host);
  }

  // ---------------------------------------------------------------- képernyő
  function selectTab(k) {
    L.tab = k;
    L.ctx.setParams(Object.assign({}, L.params, { tab: k, finding: k === 'finding' ? L.params.finding : undefined }));
    L.els.panel.setAttribute('aria-labelledby', 'log-tab-' + k);
    loadTab(k);
  }

  function render(root, ctx) {
    var params = ctx.params || {};
    var tab = TABS.indexOf(params.tab) >= 0 ? params.tab : 'finding';
    if (params.stage && P.STAGES.indexOf(params.stage) >= 0 && tab === 'finding') { filters.finding.stage = params.stage; }
    if (params.finding) { filters.finding.status = ''; }
    L = { ctx: ctx, params: Object.assign({}, params), tab: tab, els: {}, counts: {}, audit: null, blockers: [] };
    var my = L;
    ctx.onCleanup(function () { if (L === my) { if (L.kbDeb) { L.kbDeb.cancel(); } L = null; } });
    L.els.panel = h('div', { 'class': 'log-panel', role: 'tabpanel', id: 'log-panel', tabindex: '0', 'aria-labelledby': 'log-tab-' + tab });
    L.els.tabs = MA.ui.tabs({ label: t('log.tabs'), tabs: TABS.map(function (k) { return { id: k, label: tabLabel(k) }; }), active: tab, panelId: 'log-panel', onSelect: selectTab });
    MA.dom.$$('[role="tab"]', L.els.tabs).forEach(function (b) { b.id = 'log-tab-' + b.dataset.tab; });
    MA.dom.mount(root,
      h('section', { 'class': 'panel', id: 'log-main', 'aria-label': t('tab.log') }, L.els.tabs, L.els.panel),
      gateSection(), kbSection());
    var p = Promise.all([loadTab(tab), refreshCounts()]);
    if (params.q || params.kb) { L.kbSearch(); }
    // a keret a render után a címre teszi a fókuszt — ezért a következő körben
    setTimeout(function () {
      if (L !== my) { return; }
      if (params.kb) { MA.why.openItem(params.kb); } else if (params.focus === 'kb') { MA.dom.$('#kb-q').focus(); }
    }, 0);
    return p;
  }

  // Ctrl+K (⌘K): KB-kereső bárhonnan
  document.addEventListener('keydown', function (ev) {
    if (!(ev.ctrlKey || ev.metaKey) || ev.altKey || ev.shiftKey || String(ev.key).toLowerCase() !== 'k') { return; }
    if (document.querySelector('.modal-backdrop') || !MA.store.get('ui.ready')) { return; }
    ev.preventDefault();
    var q = MA.dom.$('#kb-q');
    if (q && L) { q.focus(); q.select(); } else { MA.app.navigate('log', { tab: 'finding', focus: 'kb' }); }
  });

  MA.app.registerScreen({ id: 'log', title_key: 'tab.log', workspace: 'process', tab: 'log', order: 10, render: render });
})();
