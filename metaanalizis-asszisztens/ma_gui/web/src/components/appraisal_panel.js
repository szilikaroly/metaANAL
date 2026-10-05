/* components/appraisal_panel.js — egy értékelés betöltése, szerkesztése, élő ellenőrzése és mentése (3.4, 3.5.10–3.5.13).
 *
 * MA.apprPanel.open(host, o) → {ctl}: GET /api/appraisals/<unit>/<tool>?target=&rater= → MA.appr.form; minden változás
 * után 400 ms-mal POST …/check (a motor teljesség- és implikált-ítélete; „legutolsó nyer”); Mentés: PUT If-Match-csel
 * (új fájlnál nélküle), 422-nél a szerver üzenete szó szerint + fókusz a hiányzó felülbírálás-indoklásra (X017), 409-nél
 * újratöltés-gomb; AI-vázlatnál „Jóváhagyás” (6. döntés: approver + megerősítés, If-Match); Export (fájlcsere, 5. döntés).
 *   o: {tool, unit, target, rater, myRater, inst, label?, passes?: true (PROBAST+AI menetei), applicability?, judgements?,
 *       overall?, extra?(view) → Node (pl. TRIPOD-hiánylista), onSaved?(view), onRater?(rater), consensusHref?}
 *   ctl: {dirty(), reload(), save(status?), view()}
 * A lábban (ha a komponens be van töltve) a validator keresztellenőrző doboza: MA.adaptersValidator.box (csak vélemény).
 * A számok (teljesség, implikált ítélet) a motor válaszából jönnek; itt nincs számolás.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var A = function () { return MA.appr; };
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v) { return MA.i18n.pick(v, ''); };

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  function open(host, o) {
    var st = { view: null, doc: null, etag: null, check: null, dirty: false, form: null };
    var latest = MA.api.latest();
    var msg = h('div', { 'class': 'ap-msg', id: 'ap-form-msg', role: 'alert' });
    var status = h('span', { 'class': 'ap-status', id: 'ap-status', 'aria-live': 'polite' });
    var body = h('div', { 'class': 'ap-panel-body', 'aria-busy': 'true' }, MA.ui.spinner());
    MA.dom.mount(host, body);

    function editable() {
      if (!st.view) { return false; }
      var r = o.rater;
      return r === o.myRater || r === 'ai';
    }

    function passesOf(doc) {
      if (!o.passes) { return null; }
      var sc = doc.scope || 'both';
      return sc === 'both' ? ['development', 'evaluation'] : [sc];
    }

    function q() { return A().query(o.target, o.rater); }

    function setStatus() {
      var c = st.check;
      MA.dom.mount(status,
        h('span', { 'class': 'ap-comp' }, h('span', { i18n: 'appraisal.filled' }), ' ', h('strong', { 'class': 'num', id: 'ap-comp-text' }, A().completeness(c))),
        o.passes && c && c.per_pass ? Object.keys(c.per_pass).map(function (p) {
          return h('span', { 'class': 'ap-comp-pass', dataset: { pass: p } }, ' · ' + t('appraisal.pass.' + p) + ': ', h('span', { 'class': 'num' }, A().completeness(c, p)));
        }) : null,
        c && c.complete ? MA.ui.badge('ok', t('appraisal.complete')) : MA.ui.badge('warning', t('appraisal.incomplete')),
        st.dirty ? MA.ui.badge('pending', t('appraisal.unsaved')) : null);
      var go = function (key) { if (st.form) { st.form.focusKey(key); } };
      MA.dom.mount(missHost, A().missingList(c, go, o.inst), A().invalidList(c, go, o.inst));
      if (extraHost && o.extra) { MA.dom.mount(extraHost, o.extra(st.view, st.check, st.doc)); }
    }

    var missHost = h('div', { 'class': 'ap-missing-host' });
    var extraHost = o.extra ? h('div', { 'class': 'ap-extra-host' }) : null;

    var runCheck = MA.ui.debounce(function () {
      latest(function (seq, signal) {
        return MA.api.post(A().apiPath(o.unit, o.tool, 'check'), { doc: st.doc }, { query: q(), signal: signal, toast: false, clientSeq: seq });
      }).then(function (env) {
        if (!env || !st.form) { return; }
        st.check = env.data.check;
        st.form.update(st.check);
        setStatus();
      }, function (err) { if (err.code !== 'ABORTED') { MA.dom.mount(msg, MA.ui.errorBox(err)); } });
    }, 400);

    function onChange(kind) {
      st.dirty = true;
      MA.dom.clear(msg);
      if (kind === 'answer' || kind === 'evidence') { if (st.form) { st.form.refreshWhy(); } }
      setStatus();
      runCheck();
    }

    function load() {
      MA.proc.pend(body, true);
      return MA.api.get(A().apiPath(o.unit, o.tool), { query: q(), toast: false }).then(function (env) {
        st.view = env.data;
        st.etag = env.etag || null;
        st.doc = clone(env.data.doc);
        st.check = env.data.check;
        st.dirty = false;
        draw();
        return st.view;
      }, function (err) {
        MA.proc.pend(body, false);
        MA.dom.mount(body, MA.ui.errorBox(err));
        return null;
      });
    }

    function scopeBar() {
      var sc = A().scopes(o.inst);
      if (!sc.length) { return null; }
      var cur = st.doc.scope || sc[0];
      if (o.passes) {
        // PROBAST+AI: a két menet kapcsolói (legalább egy kell)
        var dev = cur === 'both' || cur === 'development', ev = cur === 'both' || cur === 'evaluation';
        var cb = function (id, on, other) {
          var cid = 'ap-pass-' + id;
          return h('span', { 'class': 'ap-passbox' }, h('input', { type: 'checkbox', id: cid, checked: on, disabled: !editable(), onchange: function (e) {
            var a = id === 'development' ? e.target.checked : other(), b = id === 'evaluation' ? e.target.checked : other();
            if (!a && !b) { e.target.checked = true; return; }
            st.doc.scope = a && b ? 'both' : (a ? 'development' : 'evaluation');
            redrawForm();
            onChange('scope');
          } }), h('label', { htmlFor: cid }, t('appraisal.pass.' + id)));
        };
        return h('div', { 'class': 'ap-scope', role: 'group', 'aria-label': t('appraisal.passes') }, h('span', { i18n: 'appraisal.passes' }), ' ',
          cb('development', dev, function () { return MA.dom.$('#ap-pass-evaluation').checked; }),
          cb('evaluation', ev, function () { return MA.dom.$('#ap-pass-development').checked; }));
      }
      return h('div', { 'class': 'ap-scope', role: 'group', 'aria-label': t('appraisal.scope') }, h('span', { i18n: 'appraisal.scope' }), ' ',
        h('span', { 'class': 'seg' }, sc.map(function (s) {
          return h('button', { type: 'button', 'class': 'seg-btn', 'aria-pressed': s === cur ? 'true' : 'false', disabled: !editable(), dataset: { scope: s },
            onclick: function () { st.doc.scope = s; redrawForm(); onChange('scope'); } }, A().scopeLabel(o.inst, s));
        })));
    }

    var formHost = h('div', { 'class': 'ap-form-host' });
    var scopeHost = h('div', { 'class': 'ap-scope-host' });

    function redrawForm() {
      st.form = A().form({ inst: o.inst, doc: st.doc, check: st.check, scope: st.doc.scope, passes: passesOf(st.doc), editable: editable(),
        judgements: o.judgements !== false, applicability: !!o.applicability, overall: o.overall !== false, onChange: onChange });
      MA.dom.mount(formHost, st.form.el);
      MA.dom.mount(scopeHost, scopeBar());
    }

    function raterChips() {
      var v = st.view;
      if (!v.raters.length) { return null; }
      return h('div', { 'class': 'ap-raters' }, h('span', { i18n: 'appraisal.raters' }), ' ', v.raters.map(function (r) {
        var cur = r.rater === o.rater;
        return h('button', { type: 'button', 'class': ['ap-chip', cur && 'is-current', !r.human && 'is-nonhuman'], 'aria-current': cur ? 'true' : null,
          dataset: { rater: r.rater }, onclick: function () { if (!cur && o.onRater) { o.onRater(r.rater); } } },
        r.origin === 'ai_draft' ? A().aiBadge() : null, ' ', r.rater === 'consensus' ? t('appraisal.consensusDoc') : r.rater, ' ',
        MA.ui.badge(r.status === 'draft' ? 'pending' : 'ok', t('appraisal.status.' + (r.status || 'draft'))));
      }), o.consensusHref && v.human_raters.length >= 2 ? h('a', { href: o.consensusHref, 'class': 'btn btn-sm', id: 'ap-to-consensus' }, t('appraisal.toConsensus')) : null);
    }

    function draw() {
      MA.proc.pend(body, false);
      var v = st.view;
      var ro = !editable();
      redrawForm();
      MA.dom.mount(body,
        h('div', { 'class': 'ap-form-head' },
          h('h3', { 'class': 'ap-form-title', id: 'ap-form-title' }, (o.label || v.unit) + (v.target ? ' · ' + v.target : '') + ' · ' + A().toolName(o.inst)),
          h('p', { 'class': 'muted ap-path' }, v.path, v.exists ? null : [' — ', h('span', { i18n: 'appraisal.newFile' })]),
          raterChips(),
          o.inst && o.inst.intro ? h('details', { 'class': 'ap-intro', id: 'ap-intro' }, h('summary', { i18n: 'appraisal.intro' }), h('p', null, pick(o.inst.intro))) : null,
          A().aiBanner(st.doc, editable() ? approve : null),
          ro ? h('p', { 'class': 'ap-ro', role: 'note' }, t(o.rater === 'consensus' ? 'appraisal.roConsensus' : 'appraisal.readOnly', { rater: o.rater })) : null,
          scopeHost),
        formHost,
        h('div', { 'class': 'ap-foot', id: 'ap-foot' },
          h('div', { 'class': 'row' }, status),
          missHost, extraHost, msg,
          // a validator plugin keresztellenőrzése (csak vélemény; semmit nem ír vissza) — egyszer csatolva, így az élő
          // ellenőrzés nem törli az eredményét
          MA.adaptersValidator ? h('details', { 'class': 'ap-val-wrap', id: 'ap-val' }, h('summary', { i18n: 'adp.val.title' }),
            MA.adaptersValidator.box(function () { return st.doc; }, { id: 'ap-val-run', engine: function (doc) {
              // a motor ellenőrzése a validatornak küldött dokumentum pillanatképére (FID-4)
              return MA.api.post(A().apiPath(o.unit, o.tool, 'check'), { doc: doc }, { query: q(), toast: false }).then(function (env) { return env.data.check; });
            } })) : null,
          h('div', { 'class': 'toolbar' },
            h('button', { type: 'button', 'class': 'btn', id: 'ap-check', onclick: function () { runCheck(); runCheck.flush(); } }, t('appraisal.check')),
            ro ? null : h('button', { type: 'button', 'class': 'btn btn-primary', id: 'ap-save', onclick: function () { save(); } }, t('appraisal.save')),
            ro ? null : h('button', { type: 'button', 'class': 'btn', id: 'ap-save-complete', onclick: function () { save('complete'); } }, t('appraisal.saveComplete')),
            v.exists ? h('button', { type: 'button', 'class': 'btn btn-ghost', id: 'ap-export', onclick: exportOne }, t('appraisal.export.one')) : null)));
      setStatus();
    }

    function showError(err) {
      var nodes = [MA.ui.errorBox(err)];
      if (err.code === 'CONFLICT') {
        nodes.push(h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ap-reload', onclick: function () { load(); } }, t('appraisal.reload')));
      }
      MA.dom.mount(msg, nodes);
      var ov = (err.details && err.details.overrides) || [];
      if (ov.length) {
        var bad = MA.dom.$('.ap-override-text[aria-invalid="true"]', formHost);
        if (bad) { var d = bad.closest('details'); if (d) { d.open = true; } bad.focus(); }
      }
    }

    function save(statusOverride) {
      var doc = clone(st.doc);
      if (statusOverride) { doc.status = statusOverride; }
      MA.dom.clear(msg);
      return MA.api.put(A().apiPath(o.unit, o.tool), { doc: doc }, { query: q(), ifMatch: st.etag || undefined, toast: false }).then(function (env) {
        st.view = env.data;
        st.etag = env.etag || null;
        st.doc = clone(env.data.doc);
        st.check = env.data.check;
        st.dirty = false;
        draw();
        MA.ui.toast({ kind: 'success', title: t('appraisal.saved'), message: st.view.path, details: env.warnings && env.warnings.length ? env.warnings : null });
        if (o.onSaved) { o.onSaved(st.view); }
        return st.view;
      }, function (err) { showError(err); return null; });
    }

    function approve() {
      var aid = MA.dom.uid('ap-apr'), cid = MA.dom.uid('ap-apc'), nid = MA.dom.uid('ap-apn');
      var who = h('input', { id: aid, type: 'text', maxlength: '32', size: '8', value: o.myRater || '' });
      var conf = h('input', { id: cid, type: 'checkbox' });
      var note = h('textarea', { id: nid, rows: '2', maxlength: '4000' });
      var err = h('div', { role: 'alert', 'class': 'ap-msg' });
      MA.ui.modal({ title: t('appraisal.ai.approveTitle'), size: 'md',
        body: h('div', { 'class': 'stack', id: 'ap-approve-dialog' },
          h('p', { i18n: 'appraisal.ai.approveBody' }),
          h('p', { 'class': 'muted', i18n: 'appraisal.ai.note' }),
          h('div', { 'class': 'row' }, h('label', { htmlFor: aid, i18n: 'appraisal.ai.approver' }), who),
          h('div', { 'class': 'row' }, conf, h('label', { htmlFor: cid, i18n: 'appraisal.ai.confirm' })),
          h('label', { htmlFor: nid, i18n: 'appraisal.ai.noteLabel' }), note, err),
        actions: [{ label: t('common.cancel'), kind: 'ghost' },
          { label: t('appraisal.ai.approve'), kind: 'primary', onClick: function (close) {
            var a = who.value.trim();
            if (!A().RATER_RE.test(a) || /^(ai|consensus)$/i.test(a)) { MA.dom.mount(err, t('appraisal.ai.badApprover')); who.focus(); return false; }
            if (!conf.checked) { MA.dom.mount(err, t('appraisal.ai.needConfirm')); conf.focus(); return false; }
            MA.api.post(A().apiPath(o.unit, o.tool, 'approve'), { approver: a, note: note.value.trim() || null, confirm: true },
              { query: q(), ifMatch: st.etag || undefined, toast: false }).then(function (env) {
              close(true);
              st.view = env.data;
              st.etag = env.etag || null;
              st.doc = clone(env.data.doc);
              st.check = env.data.check;
              st.dirty = false;
              draw();
              MA.ui.toast({ kind: 'success', title: t('appraisal.ai.approvedToast', { who: a }), details: env.warnings && env.warnings.length ? env.warnings : null });
              if (o.onSaved) { o.onSaved(st.view); }
            }, function (e) { MA.dom.mount(err, MA.ui.errorBox(e)); });
            return false;
          } }] });
    }

    function exportOne() {
      MA.api.post('/api/appraisals/export', { unit: o.unit, tool: o.tool, target: o.target || null, rater: o.rater }, { toast: true }).then(function (env) {
        A().download(env.data.filename, env.data.doc);
        MA.ui.toast({ kind: 'success', title: t('appraisal.export.done', { name: env.data.filename }), message: t('appraisal.export.hint') });
      }, function () { /* toast */ });
    }

    load();
    return {
      dirty: function () { return st.dirty; },
      reload: load,
      save: save,
      view: function () { return st.view; },
      doc: function () { return st.doc; },
      check: function () { return st.check; }
    };
  }

  /** Import (5. döntés): fájlból vagy a beérkezett mappából; ütközésnél megerősítéssel felülírható. → Promise<adat|null> */
  function importBody(body) {
    return MA.api.post('/api/appraisals/import', body, { toast: false }).then(function (env) {
      MA.ui.toast({ kind: 'success', title: t('appraisal.import.done', { n: String(env.data.imported.length) }),
        details: env.data.imported.map(function (x) { return x.path + ' — ' + t('appraisal.import.state.' + x.state); }).concat(env.warnings || []) });
      return env.data;
    }, function (err) {
      if (err.code === 'CONFLICT' && err.details && err.details.conflicts) {
        return MA.ui.confirm({ title: t('appraisal.import.conflictTitle'), danger: true, okLabel: t('appraisal.import.replace'),
          message: err.message + ' ' + err.details.conflicts.map(function (c) { return c.path; }).join(', ') }).then(function (yes) {
          return yes ? importBody(Object.assign({}, body, { replace: true })) : null;
        });
      }
      if (err.code === 'PAYLOAD_TOO_LARGE') {
        MA.ui.toast({ kind: 'error', title: t('appraisal.import.tooLarge'), message: t('appraisal.import.useInbox') });
        return null;
      }
      MA.ui.toast({ kind: 'error', title: t('appraisal.import.failed'), message: err.message, code: err.code });
      return null;
    });
  }

  function importFile(onDone) {
    A().pickJson(function (obj) { importBody({ doc: obj }).then(function (d) { if (d && onDone) { onDone(d); } }); });
  }

  function inbox(onDone) {
    var body = h('div', { 'class': 'stack', id: 'ap-inbox', 'aria-busy': 'true' }, MA.ui.spinner());
    var m = MA.ui.modal({ title: t('appraisal.inbox.title'), size: 'lg', body: body, actions: [{ label: t('common.close'), kind: 'primary' }] });
    MA.api.get('/api/appraisals/inbox', { toast: false }).then(function (env) {
      MA.proc.pend(body, false);
      var files = env.data.files || [];
      MA.dom.mount(body, h('p', { 'class': 'muted' }, t('appraisal.inbox.help', { dir: env.data.dir })),
        files.length ? h('ul', { 'class': 'item-list' }, files.map(function (f) {
          return h('li', { 'class': 'item', dataset: { path: f.path } },
            h('span', { 'class': 'item-title' }, f.name), ' ',
            MA.ui.badge(f.kind === 'unknown' ? 'error' : 'neutral', t('appraisal.inbox.kind.' + (f.kind || 'unknown'), { n: String(f.items || 0) })),
            f.error ? h('span', { 'class': 'item-detail' }, f.error) : h('span', { 'class': 'item-detail muted' },
              (f.preview || []).map(function (p) { return [p.tool, p.unit, p.assessor].filter(function (x) { return !!x; }).join(' · '); }).join('; ')),
            f.kind !== 'unknown' ? h('button', { type: 'button', 'class': 'btn btn-sm ap-inbox-import', onclick: function () {
              importBody({ path: f.path }).then(function (d) { if (d) { m.close(); if (onDone) { onDone(d); } } });
            } }, t('appraisal.inbox.import')) : null);
        })) : MA.ui.emptyState('appraisal.inbox.empty'));
    }, function (err) { MA.proc.pend(body, false); MA.dom.mount(body, MA.ui.errorBox(err)); });
  }

  function exportAll(rater, tool) {
    MA.api.post('/api/appraisals/export', { all: true, rater: rater || null, tool: tool || null }, { toast: true }).then(function (env) {
      A().download(env.data.filename, env.data.bundle);
      MA.ui.toast({ kind: 'success', title: t('appraisal.export.done', { name: env.data.filename }), message: t('appraisal.export.hint') });
    }, function () { /* toast */ });
  }

  /** a fejléc fájlcsere-gombjai */
  function exchangeBar(getRater, tool, onDone) {
    return h('span', { 'class': 'ap-exchange' },
      h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ap-import', onclick: function () { importFile(onDone); } }, t('appraisal.import.file')),
      h('button', { type: 'button', 'class': 'btn btn-sm', id: 'ap-inbox-btn', onclick: function () { inbox(onDone); } }, t('appraisal.inbox.button')),
      h('button', { type: 'button', 'class': 'btn btn-sm btn-ghost', id: 'ap-export-all', onclick: function () { exportAll(getRater(), tool); } }, t('appraisal.export.all')));
  }

  /** egységes fejléc-sor az eszköz forrásáról (motor / validator) */
  function sourceLine(list, inst) {
    var v = list && list.validator;
    var alg = inst && inst.rollup ? inst.rollup.algorithm : null;
    return h('p', { 'class': 'muted ap-source', id: 'ap-source' },
      t('appraisal.source.engine', { name: A().toolName(inst), n: String((inst && inst.items || []).length) }),
      alg ? ' · ' + t('appraisal.source.alg', { alg: A().algText(alg) }) : '',
      v && v.state ? ' · ' + t('appraisal.source.validator', { state: v.state, version: v.version || '—', mode: v.mode || '—' }) : '',
      inst && inst.source && inst.source.from ? ' · ' + pick(inst.source.from) : '');
  }

  MA.apprPanel = { open: open, importBody: importBody, importFile: importFile, inbox: inbox, exportAll: exportAll, exchangeBar: exchangeBar, sourceLine: sourceLine };
})();
