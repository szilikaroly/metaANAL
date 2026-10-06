/* screens/grade_protocol.js — 1 Protokoll (terv 3.5.0, 4.17, 6.4 X016; AMSTAR 2 1–2. tétel): a kutatási kérdés
 * (PICO), az áttekintés típusa, a regisztráció, az adatosztály, a kimenetek és az előre rögzített elemzések.
 *
 * HTTP API:
 *   GET  /api/protocol             → szk.ma.protocol/v1 + ETag (ma-projekt.json)   (ma_gui/routes/grade_protocol.py)
 *   PUT  /api/protocol             ← {title?, question?{P,I,C,O}, review_type?, registration?} + If-Match
 *   POST /api/project              ← {action: 'outcome', outcome{id, name, data, measure, critical}, replace?}
 *                                  ← {action: 'data_class', data_class, confirm?, reason?}   (meglévő végpont)
 *   GET  /api/specs/<név> + PUT    ← az előre rögzített jelölés (prespecified, protocol_ref) If-Match-csel (meglévő)
 * A ma-projekt.json-t mindig a szerver írja (a motor validate_project_meta-ja ellenőriz); a felület csak űrlap.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '—' : fb); };

  var PICO = ['P', 'I', 'C', 'O'];
  var REGISTRIES = ['PROSPERO', 'OSF', 'INPLASY', 'other'];
  var CLASSES = ['A', 'B', 'C'];
  var RANK = { A: 0, B: 1, C: 2 };
  var S = null;

  function str(v) { return v === null || v === undefined ? '' : String(v); }

  function touch() {
    S.dirty = true;
    var b = MA.dom.$('#pr-save');
    if (b) { b.disabled = false; }
    var st = MA.dom.$('#pr-state');
    if (st) { st.textContent = t('protocol.unsaved'); }
  }

  function field(id, labelText, control, help) {
    var hid = help ? id + '-help' : null;
    if (hid) { control.setAttribute('aria-describedby', hid); }
    return h('div', { 'class': 'pf-field' }, h('label', { htmlFor: id }, labelText), control,
      help ? h('span', { 'class': 'pf-hint muted', id: hid }, help) : null);
  }

  // ---------------------------------------------------------------- kérdés, típus, regisztráció
  function questionSection() {
    var d = S.data;
    var title = h('input', { type: 'text', id: 'pr-title', value: str(d.title), oninput: function () { S.form.title = title.value; touch(); } });
    var rows = PICO.map(function (k) {
      var ta = h('textarea', { id: 'pr-q-' + k, rows: 2, oninput: function () { S.form.question[k] = ta.value; touch(); } });
      ta.value = str(d.question[k]);
      return field('pr-q-' + k, t('protocol.pico.' + k), ta, t('protocol.pico.' + k + '.help'));
    });
    var types = h('select', { id: 'pr-type', onchange: function () { S.form.review_type = types.value; MA.dom.mount(MA.dom.$('#pr-type-help'), t('protocol.type.' + types.value + '.help')); touch(); } },
      (d.review_types || []).map(function (rt) { return h('option', { value: rt, selected: rt === d.review_type }, t('protocol.type.' + rt)); }));
    var reg = d.registration || {};
    var known = REGISTRIES.indexOf(reg.registry) >= 0 ? reg.registry : (reg.registry ? 'other' : '');
    var regSel = h('select', { id: 'pr-reg-registry', onchange: function () {
      S.form.registry = regSel.value;
      other.hidden = regSel.value !== 'other';
      touch();
    } }, [h('option', { value: '' }, t('protocol.reg.none'))].concat(REGISTRIES.map(function (r) { return h('option', { value: r, selected: r === known }, t('protocol.reg.' + r)); })));
    var other = h('input', { type: 'text', id: 'pr-reg-other', value: known === 'other' ? str(reg.registry) : '', hidden: known !== 'other',
      'aria-label': t('protocol.reg.otherName'), oninput: function () { S.form.registryOther = other.value; touch(); } });
    var regId = h('input', { type: 'text', id: 'pr-reg-id', value: str(reg.id), autocomplete: 'off', oninput: function () { S.form.regId = regId.value; touch(); } });
    return h('section', { 'class': 'panel', id: 'pr-question', 'aria-labelledby': 'pr-q-h' },
      h('h2', { 'class': 'panel-title', id: 'pr-q-h' }, t('protocol.question.title'), ' ',
        MA.why.button({ title: t('protocol.question.title'), plain: { asks: t('protocol.why.asks'), because: t('protocol.why.because'), change: t('protocol.why.change') }, kb: ['AMSTAR2-01'] }, { id: 'pr-why-pico' })),
      h('p', { 'class': 'muted' }, t('protocol.question.intro')),
      field('pr-title', t('protocol.titleLabel'), title),
      h('div', { 'class': 'pr-pico' }, rows),
      d.question_text ? h('p', { 'class': 'muted', id: 'pr-qtext' }, t('protocol.question.legacy', { text: d.question_text })) : null,
      h('h3', { 'class': 'gr-h3' }, t('protocol.type.title')),
      h('div', { 'class': 'pf-field' }, h('label', { htmlFor: 'pr-type' }, t('protocol.type.label')), types,
        h('span', { 'class': 'pf-hint muted', id: 'pr-type-help' }, t('protocol.type.' + d.review_type + '.help'))),
      h('h3', { 'class': 'gr-h3' }, t('protocol.reg.title'), ' ', MA.why.kbButton('AMSTAR2-02')),
      h('p', { 'class': 'muted' }, t('protocol.reg.intro')),
      h('div', { 'class': 'pr-reg' },
        field('pr-reg-registry', t('protocol.reg.registry'), regSel), other,
        field('pr-reg-id', t('protocol.reg.id'), regId, t('protocol.reg.idHelp'))),
      h('div', { 'class': 'toolbar' },
        h('button', { type: 'button', 'class': 'btn btn-primary', id: 'pr-save', disabled: !S.dirty, onclick: function () { saveProtocol(); } }, t('protocol.save')),
        h('span', { 'class': 'muted', id: 'pr-state', role: 'status' }, S.dirty ? t('protocol.unsaved') : '')));
  }

  function protocolBody() {
    var f = S.form;
    var body = { title: f.title, question: {}, review_type: f.review_type };
    PICO.forEach(function (k) { body.question[k] = f.question[k] || null; });
    var registry = f.registry === 'other' ? (f.registryOther || '').trim() : f.registry;
    body.registration = registry || (f.regId || '').trim() ? { registry: registry || null, id: (f.regId || '').trim() || null } : null;
    return body;
  }

  function saveProtocol() {
    var b = MA.dom.$('#pr-save');
    if (b) { b.disabled = true; }
    return MA.api.put('/api/protocol', protocolBody(), { ifMatch: S.etag }).then(function (env) {
      if (!S) { return; }
      MA.ui.toast({ kind: 'success', title: (env.data.changed || []).length ? t('protocol.saved') : t('protocol.nochange') });
      take(env);
      rerender();
      MA.app.loadBase();
    }, function () { if (b) { b.disabled = false; } });
  }

  // ---------------------------------------------------------------- adatosztály
  function classSection() {
    var d = S.data;
    var name = 'pr-class';
    return h('section', { 'class': 'panel', id: 'pr-class-panel', 'aria-labelledby': 'pr-c-h' },
      h('h2', { 'class': 'panel-title', id: 'pr-c-h' }, t('project.class.title')),
      h('p', { 'class': 'muted' }, t('protocol.class.intro'), ' ', h('a', { href: MA.app.href('project'), 'class': 'btn-link', id: 'pr-class-privacy' }, t('protocol.class.privacy'))),
      h('fieldset', { 'class': 'pr-classes' },
        h('legend', null, t('project.class.legend')),
        CLASSES.map(function (c) {
          return h('div', { 'class': 'pr-classopt' },
            h('input', { type: 'radio', name: name, id: name + '-' + c, value: c, checked: d.data_class === c, 'aria-describedby': name + '-' + c + '-d',
              onchange: function () { changeClass(c); } }),
            ' ', h('label', { htmlFor: name + '-' + c }, t('project.class.' + c)),
            h('p', { 'class': 'pf-hint muted', id: name + '-' + c + '-d' }, t('project.class.' + c + '.desc')));
        })),
      d.data_class_source !== 'ma-projekt.json' ? h('p', { 'class': 'gr-warnline' }, MA.ui.badge('warning', t('protocol.class.implicit'))) : null);
  }

  function changeClass(c) {
    var from = S.data.data_class;
    if (c === from) { return; }
    var down = RANK[c] < RANK[from];
    var reason = h('textarea', { id: 'pr-class-reason', rows: 2 });
    MA.ui.modal({
      title: t('project.class.confirmTitle', { cls: c }), size: 'md',
      body: [h('p', null, t('project.class.confirmBody', { from: from, to: c })),
        down ? h('p', { 'class': 'gr-warnline' }, MA.ui.badge('warning', t('protocol.class.downgrade'))) : null,
        down ? field('pr-class-reason', t('protocol.class.reason'), reason) : null],
      actions: [{ label: t('common.cancel'), kind: 'ghost', onClick: function () { rerender(); } },
        { label: t('project.class.apply'), kind: down ? 'danger' : 'primary', onClick: function () {
          MA.api.post('/api/project', { action: 'data_class', data_class: c, confirm: true, reason: down ? (reason.value.trim() || null) : null }).then(function (env) {
            MA.store.set('project', env.data);
            MA.ui.toast({ kind: 'success', title: t('project.class.done', { cls: c }) });
            MA.app.loadBase();
            if (S) { load(S.ctx, true); }
          }, function () { rerender(); });
        } }],
      onClose: function () { if (S && S.data.data_class !== c) { var r = MA.dom.$('#pr-class-' + S.data.data_class); if (r) { r.checked = true; } } }
    });
  }

  // ---------------------------------------------------------------- kimenetek
  function outcomeDialog(o) {
    var editing = !!o;
    var project = MA.store.get('project') || {};
    var eng = MA.store.get('engine') || {};
    var taken = {};
    S.data.outcomes.forEach(function (x) { taken[x.id] = true; });
    var n = S.data.outcomes.length + 1;
    while (taken['o' + String(n)]) { n += 1; }
    var name = h('input', { type: 'text', id: 'pr-oc-name', value: o ? pick(o.name, '') : '', 'aria-required': 'true' });
    var tables = (Array.isArray(project.tables) ? project.tables : []).map(function (x) { return x.dataset; }).filter(Boolean);
    if (o && o.data && tables.indexOf(o.data) < 0) { tables.unshift(o.data); }
    var data = tables.length
      ? h('select', { id: 'pr-oc-data' }, tables.map(function (ds) { return h('option', { value: ds, selected: o && o.data === ds }, ds); }))
      : h('input', { type: 'text', id: 'pr-oc-data', value: o && o.data ? o.data : '03_adatok/adatkinyeres.csv' });
    var measure = h('select', { id: 'pr-oc-measure' }, [h('option', { value: '' }, t('outcome.chooseMeasure'))].concat(
      (eng.measures || []).map(function (m) { return h('option', { value: m.id, selected: o && o.measure === m.id }, m.id + ' — ' + pick(m.label, m.id)); })));
    var crit = h('input', { type: 'checkbox', id: 'pr-oc-critical', checked: !!(o && o.critical) });
    var oid = h('input', { type: 'text', id: 'pr-oc-id', value: o ? o.id : 'o' + String(n), disabled: editing });
    var err = h('p', { 'class': 'opt-err', id: 'pr-oc-err', role: 'alert' });
    MA.ui.modal({
      title: editing ? t('protocol.outcome.edit', { id: o.id }) : t('outcome.title'), size: 'md',
      body: [h('p', null, t('outcome.body')),
        field('pr-oc-name', t('outcome.name'), name, t('outcome.nameHelp')),
        field('pr-oc-data', t('outcome.data'), data, t('outcome.dataHelp')),
        field('pr-oc-measure', t('outcome.measure'), measure, t('outcome.measureHelp')),
        h('div', { 'class': 'pf-field' }, h('label', { htmlFor: 'pr-oc-critical' }, crit, ' ', t('protocol.outcome.critical')),
          h('span', { 'class': 'pf-hint muted' }, t('protocol.outcome.criticalHelp'))),
        field('pr-oc-id', t('outcome.id'), oid, t('outcome.idHelp')),
        err],
      actions: [{ label: t('common.cancel'), kind: 'ghost' }, { label: t('outcome.save'), kind: 'primary', onClick: function () {
        var nm = name.value.trim();
        if (!nm || !measure.value || !oid.value.trim() || !data.value.trim()) {
          err.textContent = t('outcome.required');
          (!nm ? name : (!measure.value ? measure : (!data.value.trim() ? data : oid))).focus();
          return false;
        }
        var body = { action: 'outcome', outcome: { id: oid.value.trim(), name: nm, data: data.value.trim(), measure: measure.value, critical: crit.checked } };
        if (editing) { body.replace = true; }
        MA.api.post('/api/project', body).then(function (env) {
          MA.store.set('project', env.data || project);
          MA.ui.toast({ kind: 'success', title: t('outcome.saved', { name: nm }) });
          if (S) { load(S.ctx, true); }
        }, function () { /* toast */ });
        return undefined;
      } }]
    });
  }

  function outcomesSection() {
    var list = S.data.outcomes;
    return h('section', { 'class': 'panel', id: 'pr-outcomes', 'aria-labelledby': 'pr-o-h' },
      h('h2', { 'class': 'panel-title', id: 'pr-o-h' }, t('protocol.outcomes.title')),
      h('p', { 'class': 'muted' }, t('protocol.outcomes.intro')),
      list.length ? h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: 'pr-outcome-table' },
        h('caption', { 'class': 'sr-only' }, t('protocol.outcomes.title')),
        h('thead', null, h('tr', null, ['id', 'name', 'data', 'measure', 'critical', 'actions'].map(function (c) { return h('th', { scope: 'col' }, t('protocol.oc.' + c)); }))),
        h('tbody', null, list.map(function (o) {
          return h('tr', { dataset: { outcome: o.id } },
            h('th', { scope: 'row' }, o.id), h('td', null, pick(o.name, '—')), h('td', null, h('code', null, o.data || '—')),
            h('td', null, o.measure || '—'),
            h('td', null, o.critical ? MA.ui.badge('warning', t('grade.critical'), { symbol: '!' }) : h('span', { 'class': 'muted' }, t('grade.important'))),
            h('td', null, h('button', { type: 'button', 'class': 'btn btn-sm', id: 'pr-oc-edit-' + o.id, 'aria-label': t('protocol.outcome.edit', { id: o.id }),
              onclick: function () { outcomeDialog(o); } }, t('protocol.outcome.editBtn')),
            ' ', h('a', { href: MA.app.href('grade', { outcome: o.id }), 'class': 'btn-link' }, t('protocol.outcome.toGrade'))));
        })))) : MA.ui.emptyState('protocol.outcomes.empty'),
      h('div', { 'class': 'toolbar' }, h('button', { type: 'button', 'class': 'btn', id: 'pr-oc-add', onclick: function () { outcomeDialog(null); } }, t('outcome.add'))));
  }

  // ---------------------------------------------------------------- előre rögzített elemzések
  function saveSpec(sp, pre, ref, status) {
    if (pre && !ref) {
      status.textContent = t('protocol.spec.needRef');
      return Promise.resolve();
    }
    status.textContent = t('common.loading');
    return MA.api.get('/api/specs/' + encodeURIComponent(sp.name)).then(function (env) {
      var doc = env.data || {};
      doc.prespecified = pre;
      doc.protocol_ref = ref || null;
      return MA.api.put('/api/specs/' + encodeURIComponent(sp.name), doc, { ifMatch: env.etag });
    }).then(function () {
      status.textContent = t('protocol.spec.saved');
      MA.ui.toast({ kind: 'success', title: t('protocol.spec.savedToast', { name: sp.name }) });
      if (S) { load(S.ctx, true); }
    }, function () { status.textContent = ''; });
  }

  function specsSection() {
    var list = S.data.specs || [];
    return h('section', { 'class': 'panel', id: 'pr-specs', 'aria-labelledby': 'pr-s-h' },
      h('h2', { 'class': 'panel-title', id: 'pr-s-h' }, t('protocol.specs.title'), ' ', MA.why.kbButton('X016'), ' ', MA.why.kbButton('D-S12-006')),
      h('p', { 'class': 'muted' }, t('protocol.specs.intro')),
      list.length ? h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact', id: 'pr-spec-table' },
        h('caption', { 'class': 'sr-only' }, t('protocol.specs.title')),
        h('thead', null, h('tr', null, ['name', 'outcome', 'purpose', 'prespecified', 'ref', 'actions'].map(function (c) { return h('th', { scope: 'col' }, t('protocol.sp.' + c)); }))),
        h('tbody', null, list.map(function (sp, i) {
          var pre = h('input', { type: 'checkbox', id: 'pr-sp-pre-' + i, checked: sp.prespecified === true, 'aria-label': t('protocol.sp.prespecified') + ' — ' + sp.name });
          var ref = h('input', { type: 'text', id: 'pr-sp-ref-' + i, value: str(sp.protocol_ref), 'aria-label': t('protocol.sp.ref') + ' — ' + sp.name });
          var status = h('span', { 'class': 'muted', id: 'pr-sp-st-' + i, role: 'status' });
          return h('tr', { dataset: { spec: sp.name } },
            h('th', { scope: 'row' }, h('code', null, sp.name)), h('td', null, sp.outcome || '—'),
            h('td', null, sp.purpose ? t('protocol.purpose.' + sp.purpose) : '—', sp.parent ? h('span', { 'class': 'muted' }, ' └ ' + sp.parent) : null),
            h('td', null, pre), h('td', null, ref),
            h('td', null, h('button', { type: 'button', 'class': 'btn btn-sm', id: 'pr-sp-save-' + i, 'aria-label': t('protocol.spec.save') + ' — ' + sp.name,
              onclick: function () { saveSpec(sp, pre.checked, ref.value.trim(), status); } }, t('protocol.spec.save')), ' ', status));
        })))) : MA.ui.emptyState('protocol.specs.empty'),
      h('p', { 'class': 'muted' }, h('a', { href: MA.app.href('analysis'), 'class': 'btn-link' }, t('protocol.specs.toPlan'))));
  }

  // ---------------------------------------------------------------- képernyő
  function take(env) {
    var d = env.data || {};
    S.data = d;
    S.etag = env.etag;
    S.dirty = false;
    var reg = d.registration || {};
    var known = REGISTRIES.indexOf(reg.registry) >= 0 ? reg.registry : (reg.registry ? 'other' : '');
    S.form = { title: str(d.title), question: Object.assign({}, d.question || {}), review_type: d.review_type,
      registry: known, registryOther: known === 'other' ? str(reg.registry) : '', regId: str(reg.id) };
  }

  /** Újrarajzolás a memóriában lévő állapottal (a rerender a képernyőt újraépíti; az S megmarad). */
  function rerender() {
    if (!S) { return; }
    S.repaint = true;
    S.ctx.rerender();
  }

  function load(ctx, keepForm) {
    return MA.api.get('/api/protocol', { signal: ctx.signal }).then(function (env) {
      if (!ctx.alive() || !S) { return; }
      var dirty = keepForm && S.dirty ? { form: S.form, dirty: true } : null;
      take(env);
      if (dirty) { S.form = dirty.form; S.dirty = true; }
      rerender();
    });
  }

  function render(root, ctx) {
    if (S && S.repaint && S.data) {
      S.repaint = false;
      S.ctx = ctx;
      paint(root);
      return null;
    }
    root.appendChild(MA.ui.spinner());
    return MA.api.get('/api/protocol', { signal: ctx.signal }).then(function (env) {
      if (!ctx.alive()) { return; }
      S = { ctx: ctx };
      take(env);
      paint(root);
    });
  }

  function paint(root) {
    var mine = S;
    S.ctx.onCleanup(function () { if (S === mine && !S.repaint) { S = null; } });
    MA.dom.mount(root,
      h('p', { 'class': 'gr-intro' }, t('protocol.intro')),
      S.data.has_project_json ? null : h('p', { 'class': 'gr-warnline', id: 'pr-nojson' }, MA.ui.badge('info', t('protocol.noJson'))),
      questionSection(), classSection(), outcomesSection(), specsSection());
    if (S.dirty) {
      // a rerender (pl. kimenet-mentés) után az űrlap a félkész értékekkel marad
      MA.dom.$('#pr-title').value = S.form.title;
      PICO.forEach(function (k) { MA.dom.$('#pr-q-' + k).value = str(S.form.question[k]); });
      MA.dom.$('#pr-type').value = S.form.review_type;
      MA.dom.$('#pr-reg-registry').value = S.form.registry;
      MA.dom.$('#pr-reg-id').value = S.form.regId;
    }
  }

  function onLeave(ctx, info) {
    if (!S || !S.dirty || info.reason !== 'navigate') { return true; }
    return MA.ui.confirm({ title: t('protocol.leaveTitle'), message: t('protocol.leaveBody'), okLabel: t('grade.leave'), danger: true }).then(function (ok) {
      if (ok && S) { S.dirty = false; }
      return ok;
    });
  }

  MA.app.registerScreen({ id: 'protocol', title_key: 'tab.protocol', workspace: 'process', tab: 'protocol', order: 10, render: render, onLeave: onLeave });
})();
