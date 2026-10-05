/* screens/headhunter_merge.js — Metaheadhunter 9. lépés: egyesítés, PRISMA 2020 (egyéb módszerek ága is), lezárás (EP5),
 * másodlagos adatok ellenőrzése (EP6) és exportok.
 *
 * GET /api/headhunter/merged (egy sor vizsgálatonként proveniencával), GET /api/headhunter/prisma (a headhunter
 * prisma_flow.json-ja kanonikus dobozkulcsokkal + a motor ellenőrzése: api.prisma_check). A dobozértékek a fájl
 * számai — a felület nem számol. Lezárás: POST /api/headhunter/decide {kind: 'signoff'} a merged.json ETag-jével (a CLI
 * megtagadja, amíg EP1–EP4 nyitott: 409 GATE_BLOCKED). EP6: {kind: 'verify_secondary', options: {study, field, status,
 * review, evidence, outcome, primary_locator, primary_value?}}. Export: POST /api/headhunter/run {step: 'export'}.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var B = MA.ui.badge;
  var F = MA.proc.field;
  var EMPTY = MA.ui.emptyState;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '' : fb); };
  var hh = MA.hh;
  var ST_KIND = { included: 'ok', excluded: 'warning', awaiting: 'pending', pending: 'neutral' };
  var DB_BOXES = [['identified_databases', 'A1'], ['identified_registers', 'A2'], ['duplicates_removed', 'D1'], ['automation_removed', 'D2'],
    ['other_removed', 'D3'], ['screened', 'B'], ['excluded_screening', 'C'], ['sought', 'E'], ['not_retrieved', 'F'], ['assessed', 'G'],
    ['excluded_eligibility', 'H']];
  var OM_BOXES = [['other_methods_identified', 'O1'], ['other_methods_sought', 'O2'], ['other_methods_not_retrieved', 'O3'],
    ['other_methods_assessed', 'O4'], ['other_methods_excluded', 'O5']];
  var INC_BOXES = [['included_reports', 'J'], ['included_studies', 'I'], ['awaiting', '…']];

  function box(flow, key, letter) {
    var v = flow[key];
    return h('div', { 'class': 'hh-pbox', dataset: { box: key } }, h('span', { 'class': 'hh-pbox-k' }, letter + ' · ' + t('hh.pr.box.' + key)),
      h('span', { 'class': 'hh-pbox-v num' }, v === null || v === undefined ? '—' : String(v)));
  }

  function reasons(obj) {
    var keys = Object.keys(obj || {});
    return keys.length ? h('ul', { 'class': 'hh-preasons' }, keys.map(function (k) { return h('li', null, k + ': ' + String(obj[k])); })) : null;
  }

  function prismaView(p) {
    if (!p.exists) { return EMPTY('hh.pr.none'); }
    var f = p.flow || {};
    var c = p.check || {};
    var sum = c.summary || {};
    var hhx = p.hh || {};
    return h('div', { 'class': 'hh-prisma', id: 'hh-prisma' },
      h('div', { 'class': 'row' }, B(c.ok === false ? 'error' : ((sum.warning || 0) ? 'warning' : 'ok'),
        t('hh.pr.check', { e: String(sum.error || 0), w: String(sum.warning || 0) })),
        h('span', { 'class': 'muted' }, t('hh.pr.engine'))),
      h('div', { 'class': 'hh-pcols' },
        h('section', { 'class': 'hh-pcol', 'aria-labelledby': 'hh-pdb-h' }, h('h4', { id: 'hh-pdb-h', i18n: 'hh.pr.db' }),
          DB_BOXES.map(function (b) { return box(f, b[0], b[1]); }), reasons(f.excluded_eligibility_reasons)),
        h('section', { 'class': 'hh-pcol', 'aria-labelledby': 'hh-pom-h' }, h('h4', { id: 'hh-pom-h', i18n: 'hh.pr.om' }),
          OM_BOXES.map(function (b) { return box(f, b[0], b[1]); }), reasons(f.other_methods_excluded_reasons),
          h('p', { 'class': 'muted hh-small' }, t('hh.pr.footnote', { total: hhx.citations_total === undefined ? '—' : String(hhx.citations_total),
            title: hhx.other_methods_title_excluded === undefined ? '—' : String(hhx.other_methods_title_excluded),
            known: hhx.already_known === undefined ? '—' : String(hhx.already_known) })))),
      h('section', { 'class': 'hh-pinc', 'aria-labelledby': 'hh-pinc-h' }, h('h4', { id: 'hh-pinc-h', i18n: 'hh.pr.included' }),
        INC_BOXES.map(function (b) { return box(f, b[0], b[1]); })),
      (c.findings || []).length ? h('ul', { 'class': 'item-list', id: 'hh-pr-findings' }, c.findings.map(function (x) {
        return h('li', { 'class': 'item', dataset: { code: x.code } }, B(MA.proc.sevKind(x.severity), x.code), h('span', { 'class': 'item-title' }, pick(x.title)),
          x.detail ? h('span', { 'class': 'item-detail' }, pick(x.detail)) : null,
          MA.why.button({ kb: x.code, code: x.code, title: x.title, detail: x.detail, advice: x.advice }, { compact: true }));
      })) : null,
      h('p', { 'class': 'muted hh-small' }, t('hh.pr.copy', { path: (p.project_copy || {}).path || '02_szures/prisma_flow.json' }),
        (p.project_copy || {}).exists ? [' ', B('info', t('hh.pr.copyExists'))] : null));
  }

  function secondaryList(api, d, etag) {
    var meta = d.review_meta || {};
    var items = [];
    (d.studies || []).forEach(function (st) {
      if (st.status !== 'included') { return; }
      (st.secondary_data || []).forEach(function (g) {
        (g.values || []).forEach(function (v) { if (v.status === 'unverified') { items.push({ st: st, g: g, v: v }); } });
      });
    });
    if (!items.length) { return EMPTY('hh.ev6.none'); }
    return h('table', { 'class': 'table table-compact hh-ev6', id: 'hh-ev6' },
      h('thead', null, h('tr', null, ['study', 'review', 'field', 'value', 'evidence', 'actions'].map(function (k) { return h('th', { scope: 'col' }, t('hh.ev6.col.' + k)); }))),
      h('tbody', null, items.map(function (it) {
        function verify(status) {
          var loc = h('input', { type: 'text', id: 'hh-ev6-loc', 'class': 'input', autocomplete: 'off', placeholder: t('hh.ev6.locPh') });
          var pv = h('input', { type: 'text', id: 'hh-ev6-pv', 'class': 'input hh-narrow', autocomplete: 'off' });
          var err = h('p', { 'class': 'hh-err', role: 'alert', hidden: true });
          MA.ui.modal({ title: t('hh.ev6.title.' + status, { study: it.st.label, field: it.v.field }), size: 'md',
            body: [h('p', null, t('hh.ev6.intro')), F(t('hh.ev6.loc'), loc), status === 'discrepant' ? F(t('hh.ev6.pv'), pv) : null, err],
            actions: [{ label: t('common.cancel'), kind: 'ghost' }, { label: t('hh.ev6.ok.' + status), kind: 'primary', onClick: function () {
              if (!loc.value.trim()) { err.textContent = t('hh.ev6.locMissing'); err.hidden = false; loc.focus(); return false; }
              var o = { study: it.st.study_id, field: it.v.field, status: status, review: it.g.review_id, evidence: it.v.evidence_id, primary_locator: loc.value.trim() };
              if (it.v.outcome) { o.outcome = it.v.outcome; }
              if (status === 'discrepant' && pv.value.trim()) { o.primary_value = pv.value.trim(); }
              api.decide({ kind: 'verify_secondary', options: o }, etag);
              return true;
            } }] });
        }
        return h('tr', null, h('td', null, it.st.label), h('td', null, (meta[it.g.review_id] || {}).label || it.g.review_id),
          h('td', null, it.v.field + (it.v.outcome ? ' (' + it.v.outcome + ')' : '')),
          h('td', { 'class': 'num' }, it.v.value === null || it.v.value === undefined ? '—' : String(it.v.value), ' ', B('estimated', t('hh.sv.unverified'))),
          h('td', null, h('code', { 'class': 'cmd-inline' }, it.v.evidence_id || '—')),
          h('td', { 'class': 'hh-actions' }, h('button', { type: 'button', 'class': 'btn btn-sm', onclick: function () { verify('verified'); } }, t('hh.ev6.verify')),
            h('button', { type: 'button', 'class': 'btn btn-sm', onclick: function () { verify('discrepant'); } }, t('hh.ev6.discrepant'))));
      })));
  }

  function exportForm(api) {
    var outcome = h('input', { type: 'text', id: 'hh-exp-outcome', 'class': 'input hh-narrow', autocomplete: 'off', placeholder: 'o1' });
    var toProject = h('input', { type: 'checkbox', id: 'hh-exp-project' });
    var prisma = h('input', { type: 'checkbox', id: 'hh-exp-prisma' });
    var err = h('p', { 'class': 'hh-err', role: 'alert', hidden: true });
    return h('form', { 'class': 'hh-export', id: 'hh-export', onsubmit: function (ev) {
      ev.preventDefault();
      var o = {};
      var oc = outcome.value.trim();
      if (oc && !/^[A-Za-z0-9_.-]{1,64}$/.test(oc)) { err.textContent = t('hh.exp.outcomeBad'); err.hidden = false; outcome.focus(); return; }
      err.hidden = true;
      if (oc) { o.outcome = oc; }
      if (toProject.checked) { o.to_project = true; }
      if (prisma.checked) { o.prisma = true; }
      api.run('export', o);
    } },
    h('div', { 'class': 'toolbar' }, F(t('hh.exp.outcome'), outcome, t('hh.exp.outcomeHint')),
      h('label', { 'class': 'row' }, toProject, ' ', t('hh.exp.toProject')), h('label', { 'class': 'row' }, prisma, ' ', t('hh.exp.prisma'))),
    err,
    h('div', { 'class': 'toolbar' }, h('button', { type: 'submit', 'class': 'btn', id: 'hh-exp-run' }, t('hh.exp.run')),
      h('span', { 'class': 'muted' }, t('hh.exp.hint'))));
  }

  function stepMerge(host, api) {
    return Promise.all([api.get('/merged'), api.get('/prisma')]).then(function (envs) {
      var m = envs[0].data || {};
      var mEtag = envs[0].etag;
      var p = envs[1].data || {};
      var counts = m.counts || {};
      var summ = m.summary || {};
      var openBefore = ['EP1', 'EP2', 'EP3', 'EP4'].filter(function (ep) { return (api.epOpen(ep) || 0) > 0; });
      var meta = m.review_meta || {};
      MA.dom.mount(host,
        h('div', { 'class': 'toolbar' },
          h('button', { type: 'button', 'class': 'btn' + (m.exists ? '' : ' btn-primary'), id: 'hh-merge', onclick: function () { api.run('merge', {}); } }, t('hh.mg.run')),
          h('button', { type: 'button', 'class': 'btn', id: 'hh-prisma-run', onclick: function () { api.run('prisma', {}); } }, t('hh.mg.prisma')),
          h('button', { type: 'button', 'class': 'btn', id: 'hh-verify', onclick: function () { api.run('verify', {}); } }, t('hh.mg.verify'))),
        !m.exists ? EMPTY('hh.mg.none') : [
          h('div', { 'class': 'hh-tiles', id: 'hh-mg-tiles' }, ['reviews_selected', 'citations_total', 'studies_total', 'studies_included', 'reports_included',
            'studies_from_update', 'pending_decisions', 'secondary_unverified'].map(function (k) {
            return h('div', { 'class': 'hh-tile', dataset: { k: k } }, h('div', { 'class': 'hh-tile-k' }, t('hh.mg.count.' + k)),
              h('div', { 'class': 'hh-tile-v num' }, counts[k] === undefined ? '—' : String(counts[k])));
          })),
          m.final ? h('p', null, B('ok', t('hh.mg.final')), ' ', summ.signoff_decision ? h('code', { 'class': 'cmd-inline' }, summ.signoff_decision) : null) : null,
          h('section', { 'class': 'hh-signoff', 'aria-labelledby': 'hh-so-h' }, h('h3', { id: 'hh-so-h', i18n: 'hh.mg.signoffTitle' }),
            openBefore.length ? h('p', null, B('warning', 'H009'), ' ', t('hh.mg.signoffBlocked', { eps: openBefore.join(', ') })) : h('p', { 'class': 'muted' }, t('hh.mg.signoffReady')),
            h('button', { type: 'button', 'class': 'btn btn-primary', id: 'hh-signoff', disabled: !!m.final || openBefore.length > 0, onclick: function () {
              api.reasonDialog({ title: t('hh.mg.signoffTitle'), intro: t('hh.mg.signoffIntro', { studies: String(counts.studies_included || 0), reports: String(counts.reports_included || 0) }),
                okLabel: t('hh.mg.signoff') }).then(function (res) { if (res) { api.decide({ kind: 'signoff', reason: res.reason }, mEtag); } });
            } }, t('hh.mg.signoff'))),
          h('h3', { i18n: 'hh.mg.studiesTitle' }),
          h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact hh-mg-studies', id: 'hh-mg-studies' },
            h('thead', null, h('tr', null, ['study', 'status', 'reviews', 'found', 'reports', 'flags'].map(function (k) { return h('th', { scope: 'col' }, t('hh.mg.col.' + k)); }))),
            h('tbody', null, (m.studies || []).map(function (st) {
              return h('tr', { dataset: { study: st.study_id } }, h('th', { scope: 'row' }, st.label, h('div', { 'class': 'muted hh-small' }, st.study_id)),
                h('td', null, B(ST_KIND[st.status] || 'neutral', t('hh.sc.status.' + st.status))),
                h('td', null, (st.provenance || []).map(function (pv) { return (meta[pv.review_id] || {}).label || pv.review_id; }).join(', ') || '—'),
                h('td', null, (st.found_by || []).map(function (f) { return t('hh.sc.found.' + f); }).join(', ')),
                h('td', { 'class': 'num' }, String((st.reports || []).length)),
                h('td', null, (st.conflicts || []).length ? B('warning', 'H018') : null, (st.flags || []).map(function (f) { return B('warning', f); })));
            })))),
          h('h3', { i18n: 'hh.ev6.heading' }), h('p', null, B('estimated', 'EP6'), ' ', t('hh.ev6.lead')), secondaryList(api, m, mEtag)],
        h('h3', { i18n: 'hh.pr.title' }), prismaView(p),
        h('h3', { i18n: 'hh.exp.title' }), exportForm(api),
        h('div', { 'class': 'toolbar hh-links' },
          h('a', { 'class': 'btn', href: MA.app.href('prisma'), id: 'hh-open-prisma' }, t('hh.mg.openPrisma')),
          h('a', { 'class': 'btn', href: MA.app.href('studies'), id: 'hh-open-studies' }, t('hh.mg.openStudies')),
          h('a', { 'class': 'btn', href: MA.app.href('extraction'), id: 'hh-open-extraction' }, t('hh.mg.openExtraction'))));
      return null;
    });
  }

  hh.steps.merge = stepMerge;
})();
