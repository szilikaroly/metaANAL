/* screens/project.js — Projekt megnyitása és adatvédelmi ellenőrzés (terv 3.5.2, 7.4, 7.5, 11/3).
 *
 * HTTP API (3.4; a kérés-alakok egyeztetendő feltevések):
 *   GET  /api/project?include=recent   → szk.ma.project/v1 + path, recent: [{path, title, stage, blockers}]
 *   POST /api/project                  ← {action: 'open', path} | {action: 'init', path, title, data_class} | {action: 'data_class', data_class}
 *   GET  /api/privacy[?data_class=X]   → ma_gui/privacy.py status() (X: „mi lenne, ha” előnézet; nem ír)
 *   POST /api/privacy/apply            ← {action: gitignore|deny|precommit, dry_run: true} → terv (diff, base_sha256)
 *                                      ← {action, base_sha256, confirm: true} → eredmény — CSAK a felhasználó kattintására,
 *                                         diff-előnézet és kifejezett jóváhagyás után (7.5 L2/L3).
 * Az ellenőrzés tételei a szerver logikai mezőiből jönnek; a javaslatok szövege a szerveré, szó szerint.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };

  var CLASSES = ['A', 'B', 'C'];

  function sym(kind) { return MA.ui.badge(kind === 'bad' ? 'error' : (kind === 'warn' ? 'warning' : (kind === 'ok' ? 'ok' : 'info')), null); }

  function overall(p) {
    if (p.status === 'protected' || p.status === 'at_risk' || p.status === 'blocked') { return p.status; }
    if ((p.open_blocked && p.open_blocked.blocked) || (p.write_hold && p.write_hold.active)) { return 'blocked'; }
    var tracked = p.vault && p.vault.tracked;
    if ((tracked && p.gitignore_block_present === false) || (p.tracked_sensitive_files && p.tracked_sensitive_files.length) ||
        (p.history && p.history.in_history)) { return 'at_risk'; }
    return 'protected';
  }

  /** Az ellenőrzés tételei [{kind: bad|warn|ok|info, text, id}] — megjelenítési döntés a szerver mezőiből. */
  function checks(p) {
    var out = [];
    var sensitive = p.data_class === 'B' || p.data_class === 'C';
    var v = p.vault || {};
    if (v.tracked) {
      out.push({ id: 'vault', kind: p.gitignore_block_present ? 'warn' : 'bad', text: v.reason || t('project.chk.vaultTracked') });
    } else {
      out.push({ id: 'vault', kind: 'ok', text: v.reason || t('project.chk.vaultNone') });
    }
    out.push({ id: 'gitignore', kind: p.gitignore_block_present ? 'ok' : (v.tracked || sensitive ? 'bad' : 'info'),
      text: t(p.gitignore_block_present ? 'project.chk.gitignoreOk' : 'project.chk.gitignoreMissing') });
    var cloud = p.cloud_sync;
    if (cloud && (cloud.kind || cloud.length)) {
      out.push({ id: 'cloud', kind: 'warn', text: t('project.chk.cloud', { svc: cloud.kind === 'icloud' ? 'iCloud' : 'OneDrive', path: cloud.path || '—' }) });
    } else {
      out.push({ id: 'cloud', kind: 'ok', text: t('project.chk.cloudNone') });
    }
    out.push({ id: 'deny', kind: p.deny_rule_present ? 'ok' : (sensitive ? 'warn' : 'info'), text: t(p.deny_rule_present ? 'project.chk.denyOk' : 'project.chk.denyMissing') });
    if (p.git && p.git.repo) {
      out.push({ id: 'precommit', kind: p.precommit_guard_installed ? 'ok' : (p.data_class === 'C' && v.tracked ? 'bad' : 'info'),
        text: t(p.precommit_guard_installed ? 'project.chk.guardOk' : 'project.chk.guardMissing') });
    }
    var tracked = p.tracked_sensitive_files || [];
    if (tracked.length) { out.push({ id: 'tracked', kind: 'bad', text: t('project.chk.tracked', { n: String(tracked.length), first: tracked[0] }) }); }
    var hist = p.history || {};
    if (hist.in_history) {
      out.push({ id: 'history', kind: 'bad', text: t('project.chk.history', { n: String((hist.paths || []).length) }) });
    } else if (hist.checked) {
      out.push({ id: 'history', kind: 'ok', text: t('project.chk.historyOk') });
    } else {
      out.push({ id: 'history', kind: 'info', text: hist.reason || t('project.chk.historyUnchecked') });
    }
    if (p.write_hold && p.write_hold.active) { out.push({ id: 'hold', kind: 'bad', text: p.write_hold.reason || t('project.chk.hold') }); }
    if (p.open_blocked && p.open_blocked.blocked) {
      out.push({ id: 'blocked', kind: 'bad', text: t('project.chk.openBlocked', { reasons: (p.open_blocked.reasons || []).join('; ') }) });
    }
    return out;
  }

  function actionLabel(a) { return MA.i18n.has('project.action.' + a.id) ? t('project.action.' + a.id) : (a.label || a.id); }

  function diffView(text) {
    return h('pre', { 'class': 'pj-diff', id: 'pj-diff', tabindex: '0', 'aria-label': t('project.diff.label') }, String(text || '').split('\n').map(function (line) {
      var cls = /^\+(?!\+\+)/.test(line) ? 'is-add' : (/^-(?!--)/.test(line) ? 'is-del' : (/^@@/.test(line) ? 'is-hunk' : null));
      return h('span', { 'class': ['pj-dl', cls] }, line + '\n');
    }));
  }

  // ---------------------------------------------------------------- műveletek (diff-előnézet + kifejezett jóváhagyás)
  function applyAction(a, ctx) {
    return MA.api.post('/api/privacy/apply', { action: a.id, dry_run: true }, { signal: ctx.signal }).then(function (env) {
      var plan = env.data || {};
      var target = plan.path || plan.hook_path || '—';
      var agree = h('input', { type: 'checkbox', id: 'pj-agree' });
      var err = h('p', { 'class': 'ex-cf-err', role: 'alert', hidden: true }, t('project.apply.needAgree'));
      var unchanged = plan.changed === false && !plan.script;
      var m = MA.ui.modal({
        title: t('project.apply.title', { label: actionLabel(a) }),
        size: 'lg',
        body: [
          h('p', null, t('project.apply.file'), ' ', h('code', null, target), plan.exists === false ? ' ' + t('project.apply.newFile') : ''),
          unchanged ? h('p', { 'class': 'muted' }, t('project.apply.noChange')) : null,
          plan.diff ? diffView(plan.diff) : null,
          plan.script ? h('details', null, h('summary', null, t('project.apply.script')), h('pre', { 'class': 'pj-diff' }, plan.script)) : null,
          plan.will_chain ? h('p', null, MA.ui.badge('info', t('project.apply.chain', { path: plan.chained_path || '—' }))) : null,
          plan.note ? h('p', { 'class': 'pj-note' }, MA.ui.badge('warning', t('project.apply.consequence')), ' ', plan.note) : null,
          plan.missing_rules && plan.missing_rules.length ? h('p', { 'class': 'muted' }, t('project.apply.rules', { rules: plan.missing_rules.join(', ') })) : null,
          h('p', { 'class': 'muted' }, t('project.apply.never')),
          h('label', { 'class': 'ex-check pj-agree' }, agree, ' ', t('project.apply.agree', { path: target })),
          err
        ],
        actions: [
          { label: t('common.cancel'), kind: 'ghost' },
          { label: t('project.apply.ok'), kind: 'primary', onClick: function (close) {
            if (!agree.checked) { err.hidden = false; agree.focus(); return false; }
            MA.api.post('/api/privacy/apply', { action: a.id, base_sha256: plan.base_sha256 === undefined ? null : plan.base_sha256, confirm: true })
              .then(function (res) {
                close('applied');
                var r = res.data || {};
                MA.ui.toast({ kind: 'success', title: t('project.apply.done', { label: actionLabel(a) }), message: r.path || r.hook_path || '' });
                return reloadPrivacy().then(function () { if (ctx.alive()) { ctx.rerender(); } });
              }, function () { return null; });
            return false;
          } }
        ]
      });
      m.el.id = 'pj-apply';
    }, function () { return null; });
  }

  function reloadPrivacy() {
    return MA.api.get('/api/privacy', { toast: false }).then(function (env) { MA.store.set('privacy', env.data); }, function () { return null; });
  }

  function afterProjectChange(data, ctx) {
    if (data) { MA.store.set('project', data); }
    MA.store.set('extraction.draft', null);
    MA.app.setTabBadges('extraction', null);
    return MA.app.loadBase().then(function () { if (ctx.alive()) { ctx.rerender(); } });
  }

  function openProject(path, ctx) {
    return MA.api.post('/api/project', { action: 'open', path: path }).then(function (env) {
      MA.ui.toast({ kind: 'success', title: t('project.open.done', { path: (env.data && env.data.path) || path }) });
      return afterProjectChange(env.data, ctx);
    }, function () { return null; });
  }

  // ---------------------------------------------------------------- szakaszok
  function openPanel(project, recent, ctx) {
    var path = h('input', { type: 'text', id: 'pj-open-path', 'class': 'pj-path', placeholder: 'reviews/…', autocomplete: 'off' });
    var err = h('p', { 'class': 'ex-cf-err', role: 'alert', hidden: true }, t('project.open.needPath'));
    var fresh = project && project.initialized === false;
    var title = h('input', { type: 'text', id: 'pj-init-title', autocomplete: 'off', value: fresh && project.title ? MA.i18n.pick(project.title, '') : '' });
    var ipath = h('input', { type: 'text', id: 'pj-init-path', 'class': 'pj-path', placeholder: 'reviews/…', autocomplete: 'off', value: (project && project.path) || '' });
    var ierr = h('p', { 'class': 'ex-cf-err', role: 'alert', hidden: true }, t('project.init.need'));
    var icls = classRadios('pj-init-class', 'A', null);
    return h('section', { 'class': 'panel', id: 'pj-open', 'aria-labelledby': 'pj-open-h' },
      h('h2', { 'class': 'panel-title', id: 'pj-open-h' }, t('project.open.title')),
      project && project.path ? h('p', null, t('project.current'), ' ', h('strong', null, MA.i18n.pick(project.title, '')), ' · ', h('code', null, project.path),
        project.data_class ? [' · ', MA.ui.badge('neutral', t('project.class.short', { cls: project.data_class }))] : null) : null,
      fresh ? h('p', { 'class': 'ex-alert is-warning', id: 'pj-uninit' }, MA.ui.badge('warning', t('project.uninitBadge')), ' ', t('project.uninit')) : null,
      recent.length ? h('div', { 'class': 'pj-recent' }, h('span', { 'class': 'muted' }, t('project.recent') + ' '),
        h('ul', { 'class': 'pj-recent-list' }, recent.map(function (r) {
          var bits = [r.stage || null, typeof r.blockers === 'number' && r.blockers ? t('project.recent.blockers', { n: String(r.blockers) }) : null].filter(function (x) { return !!x; });
          return h('li', null, h('button', { type: 'button', 'class': 'btn-link pj-recent-btn', dataset: { path: r.path }, onclick: function () { openProject(r.path, ctx); } },
            '▸ ' + r.path + (r.title ? ' — ' + MA.i18n.pick(r.title, '') : '') + (bits.length ? ' (' + bits.join(', ') + ')' : '')));
        }))) : null,
      h('form', { 'class': 'row pj-open-form', onsubmit: function (ev) {
        ev.preventDefault();
        var v = path.value.trim();
        if (!v) { err.hidden = false; path.setAttribute('aria-invalid', 'true'); path.focus(); return; }
        err.hidden = true;
        path.removeAttribute('aria-invalid');
        openProject(v, ctx);
      } }, h('label', { htmlFor: 'pj-open-path' }, t('project.open.path')), path,
      h('button', { type: 'submit', 'class': 'btn btn-primary btn-sm', id: 'pj-open-btn' }, t('project.open.button')), err),
      h('p', { 'class': 'muted pj-note' }, t('project.open.note')),
      h('details', { 'class': 'pj-init', id: 'pj-init', open: fresh }, h('summary', null, t('project.init.title')),
        h('form', { 'class': 'stack', onsubmit: function (ev) {
          ev.preventDefault();
          var p = ipath.value.trim(), ti = title.value.trim();
          var cls = icls.value();
          if (!p || !ti) { ierr.hidden = false; return; }
          ierr.hidden = true;
          MA.ui.confirm({ title: t('project.init.confirmTitle'), message: t('project.init.confirmBody', { path: p, cls: cls }), okLabel: t('project.init.button') }).then(function (ok) {
            if (!ok) { return null; }
            return MA.api.post('/api/project', { action: 'init', path: p, title: ti, data_class: cls }).then(function (env) {
              MA.ui.toast({ kind: 'success', title: t('project.init.done', { path: p }) });
              return afterProjectChange(env.data, ctx);
            }, function () { return null; });
          });
        } },
        h('div', { 'class': 'ex-prov-grid' },
          h('div', { 'class': 'ex-fld' }, h('label', { htmlFor: 'pj-init-path' }, t('project.init.path')), ipath),
          h('div', { 'class': 'ex-fld' }, h('label', { htmlFor: 'pj-init-title' }, t('project.init.name')), title)),
        icls.el,
        h('p', { 'class': 'muted' }, t('project.init.cmd')),
        h('div', { 'class': 'row' }, h('button', { type: 'submit', 'class': 'btn btn-sm', id: 'pj-init-btn' }, t('project.init.button'))), ierr)));
  }

  /** Adatosztály-választó (A/B/C) magyarázattal; role=radiogroup, natív rádiógombokkal (nyilakkal léptethető). */
  function classRadios(name, current, onChange) {
    var inputs = {};
    var el = h('fieldset', { 'class': 'pj-classes', id: name },
      h('legend', null, t('project.class.legend')),
      CLASSES.map(function (c) {
        var id = name + '-' + c;
        inputs[c] = h('input', { type: 'radio', name: name, id: id, value: c, checked: c === current, 'aria-describedby': id + '-d',
          onchange: function () { if (onChange) { onChange(c); } } });
        return h('div', { 'class': ['pj-class', c === current && 'is-current'] },
          h('label', { htmlFor: id, 'class': 'pj-class-label' }, inputs[c], ' ', h('strong', null, t('project.class.' + c))),
          h('p', { 'class': 'pj-class-desc muted', id: id + '-d' }, t('project.class.' + c + '.desc')));
      }));
    return { el: el, value: function () { var x = el.querySelector('input:checked'); return x ? x.value : null; } };
  }

  function privacyList(p, ctx, opts) {
    var status = overall(p);
    var kind = { protected: 'ok', at_risk: 'warning', blocked: 'error' }[status];
    var acts = Array.isArray(p.actions) ? p.actions : [];
    return [
      h('p', { 'class': 'pj-status' }, MA.ui.badge(kind, t('privacy.status.' + status)), ' ',
        t('project.privacy.classLine', { cls: p.data_class || '?', label: p.data_class_label || '' })),
      h('ul', { 'class': 'pj-checks', id: opts.listId }, checks(p).map(function (c) {
        return h('li', { 'class': ['pj-check', 'is-' + c.kind], dataset: { check: c.id } }, sym(c.kind),
          h('span', { 'class': 'sr-only' }, t('project.chk.kind.' + c.kind) + ': '), h('span', null, c.text));
      })),
      p.recommendations && p.recommendations.length ? h('div', { 'class': 'pj-recs' },
        h('h3', { 'class': 'ex-sub' }, t('project.privacy.recs')),
        h('ul', null, p.recommendations.map(function (r) { return h('li', null, MA.i18n.pick(r, '')); }))) : null,
      opts.actions && acts.length ? h('div', { 'class': 'row pj-actions' }, acts.map(function (a) {
        return h('button', { type: 'button', 'class': 'btn btn-sm', dataset: { action: a.id }, id: 'pj-act-' + a.id, onclick: function () { applyAction(a, ctx); } },
          actionLabel(a) + ' ', h('span', { 'aria-hidden': 'true' }, '(diff ▾)'));
      })) : null
    ];
  }

  function classPanel(project, privacy, ctx) {
    var current = (privacy && privacy.data_class) || (project && project.data_class) || 'A';
    var preview = h('div', { 'class': 'pj-preview', id: 'pj-class-preview', 'aria-live': 'polite' });
    var chosen = current;
    var applyBtn = h('button', { type: 'button', 'class': 'btn btn-sm btn-primary', id: 'pj-class-apply', disabled: true, onclick: function () {
      var c = chosen;
      MA.ui.confirm({ title: t('project.class.confirmTitle', { cls: c }), message: t('project.class.confirmBody', { from: current, to: c }), okLabel: t('project.class.apply') })
        .then(function (ok) {
          if (!ok) { return null; }
          // a megerősítő párbeszéd után: az osztály csökkentéséhez a szerver kifejezett confirm-et kér
          return MA.api.post('/api/project', { action: 'data_class', data_class: c, confirm: true }).then(function (env) {
            MA.ui.toast({ kind: 'success', title: t('project.class.done', { cls: c }) });
            return afterProjectChange(env.data, ctx);
          }, function () { return null; });
        });
    } }, t('project.class.apply'));
    var radios = classRadios('pj-class', current, function (c) {
      chosen = c;
      applyBtn.disabled = c === current;
      if (c === current) { MA.dom.clear(preview); return; }
      MA.dom.mount(preview, MA.ui.spinner());
      MA.api.get('/api/privacy', { query: { data_class: c }, signal: ctx.signal, toast: false }).then(function (env) {
        if (!ctx.alive() || chosen !== c) { return; }
        MA.dom.mount(preview, h('h3', { 'class': 'ex-sub' }, t('project.class.previewTitle', { cls: c })),
          privacyList(env.data || {}, ctx, { listId: 'pj-preview-checks', actions: false }));
      }, function (err) { if (ctx.alive()) { MA.dom.mount(preview, MA.ui.errorBox(err)); } });
    });
    return h('section', { 'class': 'panel', id: 'pj-class-panel', 'aria-labelledby': 'pj-class-h' },
      h('h2', { 'class': 'panel-title', id: 'pj-class-h' }, t('project.class.title')),
      radios.el,
      h('p', { 'class': 'muted' }, t('project.class.note')),
      h('div', { 'class': 'row' }, applyBtn), preview);
  }

  function render(root, ctx) {
    var project = MA.store.get('project') || {};
    ctx.setTitle(project.path || '');
    var openBox = h('div', null, MA.ui.spinner());
    var privBox = h('section', { 'class': 'panel', id: 'pj-privacy', 'aria-labelledby': 'pj-privacy-h' },
      h('h2', { 'class': 'panel-title', id: 'pj-privacy-h' }, t('project.privacy.title', { path: project.path || '—' })), MA.ui.spinner());
    var classBox = h('div');
    MA.dom.mount(root, openBox, h('div', { 'class': 'grid-2' }, privBox, classBox));
    var pRecent = MA.api.get('/api/project', { query: { include: 'recent' }, signal: ctx.signal, toast: false }).then(function (env) {
      return env.data && Array.isArray(env.data.recent) ? env.data.recent : [];
    }, function (e) { if (e.code === 'ABORTED') { throw e; } return []; }).then(function (recent) {
      if (ctx.alive()) { MA.dom.mount(openBox, openPanel(project, recent, ctx)); }
    });
    var pPriv = MA.api.get('/api/privacy', { signal: ctx.signal, toast: false }).then(function (env) {
      if (!ctx.alive()) { return; }
      MA.store.set('privacy', env.data);
      var p = env.data || {};
      MA.dom.mount(privBox, h('h2', { 'class': 'panel-title', id: 'pj-privacy-h' }, t('project.privacy.title', { path: project.path || '—' })),
        privacyList(p, ctx, { listId: 'pj-checks', actions: true }));
      MA.dom.mount(classBox, classPanel(project, p, ctx));
    }, function (err) {
      if (ctx.alive() && err.code !== 'ABORTED') { MA.dom.mount(privBox, MA.ui.errorBox(err)); }
    });
    return Promise.all([pRecent, pPriv]);
  }

  MA.app.registerScreen({
    id: 'project',
    title_key: 'screen.project.title',
    workspace: 'process',
    tab: null,
    order: 10,
    render: render
  });

  MA.selftest.register('projekt: adatvédelmi tételek a szerver mezőiből', function (tt) {
    var c = checks({ data_class: 'B', vault: { tracked: true, reason: 'vault' }, gitignore_block_present: false, cloud_sync: { kind: 'onedrive', path: '~/Documents' },
      deny_rule_present: false, git: { repo: true }, precommit_guard_installed: false, tracked_sensitive_files: [], history: { checked: true, in_history: false },
      write_hold: { active: true, reason: 'tartás' }, open_blocked: { blocked: false } });
    var by = {};
    c.forEach(function (x) { by[x.id] = x.kind; });
    tt.eq(by.vault, 'bad', 'követett vault + nincs blokk → ✖');
    tt.eq(by.gitignore, 'bad', 'hiányzó .gitignore-blokk → ✖');
    tt.eq(by.cloud, 'warn', 'felhőszinkron → ⚠');
    tt.eq(by.hold, 'bad', 'írás-tartás → ✖');
    tt.eq(overall({ write_hold: { active: true } }), 'blocked', 'írás-tartás → BLOKKOLVA');
  });
})();
