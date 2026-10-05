/* screens/headhunter.js — Metaheadhunter: meglévő metaanalízisek bányászata (TERV_metaheadhunter.md 17. fejezet).
 *
 * Varázsló a 2 PRISMA fül alképernyőjeként: #/headhunter?step=<lépés>. Kilenc lépés, mindegyik kártya: állapotjelvény,
 * egymondatos magyarázat, fő gomb, nyitott emberi tételek száma (EP1–EP6), „Mit jelent?” lenyíló. Ez a fájl a keret
 * (lépéssor, feladatpanel, közös segédek: MA.hh) és az 1–2. lépés (Források, Kérdés); a többi lépés a
 * headhunter_*.js fájlokban regisztrál (MA.hh.steps[id] = function (host, api) {…}).
 *
 * HTTP API (ma_gui/routes/headhunter.py): GET /api/headhunter/{status,sources,reviews[/<id>],studies,proposals,overlap,
 * merged,prisma,update,decisions}; POST /api/headhunter/run {step, options} → 202 feladat; GET /api/headhunter/runs/<id>
 * (?wait=); POST …/runs/<id>/cancel; POST /api/headhunter/decide {kind, target, value, …} + If-Match (a cél fájl
 * ETag-je). A hálózati lépések a szerveren háttérben futnak (CLI-alfolyamat); a felület csak lekérdez.
 * Számot a felület nem számol: a CCA, a pontszám, a PRISMA-dobozok a motor/CLI kész értékei (*_text).
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var B = MA.ui.badge;
  var F = MA.proc.field;
  var SEL = MA.proc.select;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '' : fb); };
  var BASE = '/api/headhunter';

  var STEPS = [
    { id: 'sources', eps: [], steps: ['sources'] },
    { id: 'pico', eps: [], steps: [] },
    { id: 'reviews', eps: ['EP1'], steps: ['find_reviews', 'select_reviews'] },
    { id: 'extract', eps: ['EP2'], steps: ['extract', 'resolve'] },
    { id: 'dedupe', eps: ['EP3'], steps: ['dedupe'] },
    { id: 'overlap', eps: [], steps: ['overlap'] },
    { id: 'screen', eps: ['EP4'], steps: ['screen'] },
    { id: 'update', eps: [], steps: ['update_search'] },
    { id: 'merge', eps: ['EP5', 'EP6'], steps: ['merge', 'prisma', 'export'] }
  ];
  var RUN_STEP = { sources_check: 'sources', init: 'pico', find_reviews: 'reviews', extract: 'extract', resolve: 'extract',
    dedupe: 'dedupe', overlap: 'overlap', update_search: 'update', cite_search: 'update', merge: 'merge', prisma: 'merge',
    export: 'merge', verify: 'merge' };
  var STATE_KIND = { done: 'ok', needs_human: 'warning', stale: 'stale', running: 'progress', failed: 'error',
    partial: 'progress', not_started: 'neutral', locked: 'neutral' };
  var SRC_KIND = { ok: 'ok', not_configured: 'neutral', unreachable: 'warning', rate_limited: 'warning',
    unauthorized: 'error', forbidden: 'error', disabled: 'neutral', unknown: 'pending' };
  var CONF_KIND = { high: 'ok', medium: 'info', low: 'warning' };
  var ID_LABEL = { pmid: 'PMID', pmcid: 'PMCID', doi: 'DOI', nct: 'NCT', eid: 'EID', openalex: 'OpenAlex' };

  var S = null;           // képernyő-állapot (csak memóriában): {ctx, status, etag, step, els, job}

  // ---------------------------------------------------------------- kis segédek
  function num(v) { return v === null || v === undefined || v === '' ? '—' : String(v); }
  function byId(list, id) { return (list || []).filter(function (x) { return x && x.id === id; })[0] || null; }
  function badge(kind, key, args, opts) { return B(kind, t(key, args), opts); }

  /** „Mit jelent?” lenyíló — a kezdőknek szóló magyarázat a szerver status.help[id] {hu, en} szövege. */
  function explain(id) {
    var txt = pick(((S && S.status && S.status.help) || {})[id]);
    return txt ? h('details', { 'class': 'hh-what' }, h('summary', { i18n: 'hh.what' }), h('p', { 'class': 'hh-what-body' }, txt)) : null;
  }

  function idList(ids) {
    var out = [];
    Object.keys(ID_LABEL).forEach(function (k) {
      var v = ids && ids[k];
      if (!v || !v.value) { return; }
      out.push(h('span', { 'class': ['hh-id', !v.api && 'is-unconfirmed'], title: t('hh.id.source', { source: v.source || '—', via: v.via || '—' }) },
        h('span', { 'class': 'hh-id-k' }, ID_LABEL[k]), ' ', h('span', { 'class': 'hh-id-v' }, v.value), ' ',
        v.api ? B('ok', t('hh.id.api'), { symbol: '✔' }) : B('warning', t('hh.id.unconfirmed'))));
    });
    ((ids && ids.registry) || []).forEach(function (v) {
      out.push(h('span', { 'class': 'hh-id' }, h('span', { 'class': 'hh-id-k' }, t('hh.id.registry')), ' ', h('span', { 'class': 'hh-id-v' }, v.value)));
    });
    return out.length ? h('span', { 'class': 'hh-ids' }, out) : h('span', { 'class': 'muted' }, t('hh.id.none'));
  }

  function locator(loc) {
    if (!loc) { return ''; }
    var parts = [];
    if (loc.container) { parts.push(loc.container); }
    if (loc.section) { parts.push(t('hh.loc.section', { s: loc.section })); }
    if (loc.label) { parts.push(loc.label); }
    if (loc.row !== null && loc.row !== undefined) { parts.push(t('hh.loc.row', { n: String(loc.row) })); }
    if (loc.column) { parts.push(t('hh.loc.column', { c: loc.column })); }
    if (loc.ref_id) { parts.push(loc.ref_id); }
    if (loc.page) { parts.push(t('hh.loc.page', { p: loc.page })); }
    return parts.join(' · ');
  }

  function quote(ev) {
    if (!ev) { return null; }
    return h('figure', { 'class': 'hh-quote' },
      h('blockquote', null, ev.quote || ''),
      h('figcaption', { 'class': 'muted' }, locator(ev.locator),
        ev.confidence ? [' · ', confBadge(ev.confidence)] : null));
  }

  function confBadge(c) { return B(CONF_KIND[c] || 'neutral', t('hh.conf.' + (c || 'unknown'))); }

  function stateBadge(state) { return B(STATE_KIND[state] || 'neutral', t('hh.state.' + state)); }

  function epOpen(ep) {
    var d = S && S.status;
    if (!d) { return null; }
    var cli = d.cli;
    if (cli && cli.available && cli.checkpoints && typeof cli.checkpoints[ep] === 'number') { return cli.checkpoints[ep]; }
    var c = byId(d.checkpoints, ep);
    return c && typeof c.open_items === 'number' ? c.open_items : null;
  }

  function stepState(def) {
    var d = S.status;
    var job = S.job;
    if (job && (job.status === 'running' || job.status === 'queued') && RUN_STEP[job.step] === def.id) { return 'running'; }
    if (!d.initialized) { return def.id === 'pico' || def.id === 'sources' ? 'not_started' : 'locked'; }
    if (def.id === 'pico') { return 'done'; }
    var st = def.steps.map(function (s) { var x = byId(d.steps, s); return x ? x.status : 'not_started'; });
    if (st.indexOf('running') >= 0) { return 'running'; }
    if (st.indexOf('failed') >= 0) { return 'failed'; }
    var open = 0;
    def.eps.forEach(function (ep) { var n = epOpen(ep); if (n) { open += n; } });
    if (open > 0 || st.indexOf('needs_human') >= 0) { return 'needs_human'; }
    if (st.indexOf('stale') >= 0) { return 'stale'; }
    if (st.length && st.every(function (x) { return x === 'done' || x === 'skipped'; })) { return 'done'; }
    if (st.some(function (x) { return x === 'done'; })) { return 'partial'; }
    return 'not_started';
  }

  function openItems(def) {
    var n = 0, any = false;
    def.eps.forEach(function (ep) { var v = epOpen(ep); if (typeof v === 'number') { any = true; n += v; } });
    return any ? n : null;
  }

  function recommended() {
    var d = S.status;
    if (!d.initialized) { return 'pico'; }
    for (var i = 0; i < STEPS.length; i++) {
      var st = stepState(STEPS[i]);
      if (st === 'needs_human' || st === 'running' || st === 'failed') { return STEPS[i].id; }
    }
    for (var j = 0; j < STEPS.length; j++) {
      if (stepState(STEPS[j]) !== 'done') { return STEPS[j].id; }
    }
    return 'merge';
  }

  // ---------------------------------------------------------------- hálózat
  function get(path, query) {
    return MA.api.get(BASE + path, { query: query || null, signal: S.ctx.signal });
  }

  function loadStatus() {
    return MA.api.get(BASE + '/status', { signal: S.ctx.signal }).then(function (env) {
      S.status = env.data || {};
      S.etag = env.etag;
      if (!S.job && S.status.jobs && S.status.jobs.active) { S.job = S.status.jobs.active; follow(S.job.job_id); }
      return env;
    });
  }

  /** Frissítés: állapot + a lépéssor + az aktuális lépés kártyája (a fókusz a kártyán marad, ha ott volt). */
  function refresh() {
    if (!S) { return Promise.resolve(null); }
    var my = S;
    return loadStatus().then(function () {
      if (S !== my || !my.ctx.alive()) { return; }
      paintHead();
      paintStepper();
      paintCard();
    });
  }

  /** run(step, options) → a végállapotú feladat-pillanatkép (Promise). A szerver 202-t ad; a felület lekérdez. */
  function run(step, options, label) {
    var my = S;
    return MA.api.post(BASE + '/run', { step: step, options: options || {} }).then(function (env) {
      if (S !== my) { return null; }
      S.job = env.data;
      S.jobLabel = label || t('hh.run.' + step);
      paintJob();
      paintStepper();
      return follow(env.data.job_id);
    });
  }

  function follow(jobId) {
    var my = S;
    if (my.following === jobId) { return my.followP; }
    my.following = jobId;
    my.followP = new Promise(function (resolve) {
      function tick() {
        if (S !== my || !my.ctx.alive()) { resolve(null); return; }
        MA.api.get(BASE + '/runs/' + encodeURIComponent(jobId), { query: { wait: 2 }, toast: false, quiet: ['NOT_FOUND'] }).then(function (env) {
          if (S !== my) { resolve(null); return; }
          my.job = env.data;
          paintJob();
          if (env.data.status === 'queued' || env.data.status === 'running') { setTimeout(tick, 400); return; }
          my.following = null;
          finished(env.data);
          resolve(env.data);
        }, function (err) {
          if (S !== my) { resolve(null); return; }
          my.following = null;
          if (err.code === 'NOT_FOUND') { my.job = null; paintJob(); resolve(null); return; }
          setTimeout(tick, 2000);
        });
      }
      tick();
    });
    return my.followP;
  }

  function finished(job) {
    if (job.status === 'done') {
      MA.ui.toast({ kind: job.partial ? 'warning' : 'success', title: t('hh.job.doneToast', { step: t('hh.run.' + job.step) }),
        message: pick(job.message) || (job.partial ? t('hh.job.partial') : '') });
    } else if (job.status === 'error' || job.status === 'timeout') {
      MA.ui.toast({ kind: 'error', title: t('hh.job.failedToast', { step: t('hh.run.' + job.step) }),
        message: (job.error && job.error.message) || '' });
    }
    refresh();
  }

  function cancel() {
    if (!S.job) { return; }
    MA.api.post(BASE + '/runs/' + encodeURIComponent(S.job.job_id) + '/cancel', {}).then(function (env) {
      if (S) { S.job = env.data; paintJob(); }
      MA.ui.toast({ kind: 'info', title: t('hh.job.cancelAsked') });
    }, function () { return null; });
  }

  /** decide(body, etag) → a CLI döntés-borítéka (Promise; hibánál az api.js toastol és a Promise elutasul). */
  function decide(body, etag) {
    var my = S;
    return MA.api.post(BASE + '/decide', body, { ifMatch: etag || null }).then(function (env) {
      var d = env.data || {};
      var p = Promise.resolve(d);
      if (d.status === 'queued' || d.status === 'running') {     // 202: a CLI még fut — lekérdezzük
        S.job = d;
        paintJob();
        p = follow(d.job_id);
      }
      return p.then(function (done) {
        if (S !== my) { return done; }
        var msg = done && pick(done.message);
        MA.ui.toast({ kind: 'success', title: t('hh.decided'), message: msg || '' });
        return refresh().then(function () { return done; });
      });
    });
  }

  // ---------------------------------------------------------------- párbeszédablakok
  /** reasonDialog({title, intro?, codes?: [{code, label}], needCode?, needReason?, presets?: [kulcs], okLabel?})
   *  → Promise<{reason, reason_code} | null>. Az indoklás a projekt döntésnaplójába kerül: betegadat nem írható bele. */
  function reasonDialog(o) {
    return new Promise(function (resolve) {
      var done = false;
      var ta = h('textarea', { id: 'hh-reason', rows: 3, 'class': 'input', maxLength: 4000 });
      var sel = null;
      if (o.codes && o.codes.length) {
        sel = h('select', { id: 'hh-reason-code', 'class': 'input' },
          h('option', { value: '' }, t(o.needCode ? 'hh.reason.pickCode' : 'hh.reason.noCode')),
          o.codes.map(function (c) { return h('option', { value: c.code }, c.code + ' — ' + pick(c.label)); }));
      }
      var presets = (o.presets || []).map(function (k) {
        return h('button', { type: 'button', 'class': 'btn btn-sm hh-preset', onclick: function () { ta.value = t(k); ta.focus(); } }, t(k));
      });
      var err = h('p', { 'class': 'hh-err', role: 'alert', hidden: true });
      MA.ui.modal({
        title: o.title, size: 'md',
        body: [o.intro ? h('p', null, o.intro) : null,
          sel ? F(t('hh.reason.code'), sel) : null,
          presets.length ? h('div', { 'class': 'row hh-presets', role: 'group', 'aria-label': t('hh.reason.presets') }, presets) : null,
          F(t(o.needReason ? 'hh.reason.required' : 'hh.reason.optional'), ta, t('hh.reason.noPhi')), err],
        actions: [{ label: t('common.cancel'), kind: 'ghost' },
          { label: o.okLabel || t('common.ok'), kind: o.danger ? 'danger' : 'primary', onClick: function () {
            var code = sel ? sel.value : '';
            var reason = ta.value.trim();
            if (o.needCode && !code) { err.textContent = t('hh.reason.codeMissing'); err.hidden = false; sel.focus(); return false; }
            if (o.needReason && !reason) { err.textContent = t('hh.reason.textMissing'); err.hidden = false; ta.setAttribute('aria-invalid', 'true'); ta.focus(); return false; }
            done = true;
            resolve({ reason: reason || null, reason_code: code || null });
            return true;
          } }],
        onClose: function () { if (!done) { resolve(null); } }
      });
    });
  }

  // ---------------------------------------------------------------- feladatpanel
  function jobSummary(job) {
    var nodes = [];
    var msg = pick(job.message);
    if (msg) { nodes.push(h('p', null, msg)); }
    if (job.partial) { nodes.push(h('p', null, badge('warning', 'hh.job.partialBadge'), ' ', t('hh.job.partial'))); }
    if (job.error) {
      nodes.push(h('p', { 'class': 'hh-err' }, B('error', job.error.hh_code || job.error.code), ' ', job.error.message || ''));
      if (job.error.stderr_tail) { nodes.push(h('pre', { 'class': 'cmd hh-tail' }, job.error.stderr_tail)); }
    }
    (job.errors || []).forEach(function (e) { nodes.push(h('p', { 'class': 'hh-err' }, B('error', e.code), ' ', pick(e))); });
    if ((job.warnings || []).length) {
      nodes.push(h('ul', { 'class': 'item-list hh-warnings' }, job.warnings.slice(0, 12).map(function (w) {
        return h('li', { 'class': 'item' }, B('warning', w.code), ' ', h('span', null, pick(w)));
      })));
    }
    if ((job.pending || []).length) {
      nodes.push(h('p', null, t('hh.job.pending'), ' ', job.pending.map(function (p) {
        return B('warning', p.checkpoint + ': ' + String(p.n));
      })));
    }
    if (job.next) { nodes.push(h('p', { 'class': 'muted' }, t('hh.job.next'), ' ', h('code', { 'class': 'cmd-inline' }, job.next))); }
    return nodes;
  }

  function paintJob() {
    var host = S.els.job;
    var job = S.job;
    if (!job) { MA.dom.clear(host); host.hidden = true; return; }
    host.hidden = false;
    var runningNow = job.status === 'queued' || job.status === 'running';
    var last = (job.progress || [])[job.progress && job.progress.length ? job.progress.length - 1 : 0];
    var prog = null;
    if (runningNow && last && typeof last.done === 'number' && typeof last.total === 'number' && last.total > 0) {
      prog = h('progress', { max: String(last.total), value: String(last.done), 'aria-label': t('hh.job.progress') });
    }
    MA.dom.mount(host,
      h('div', { 'class': 'row hh-job-head' },
        B(runningNow ? 'progress' : (job.status === 'done' ? (job.partial ? 'warning' : 'ok') : (job.status === 'cancelled' ? 'neutral' : 'error')),
          t('hh.job.status.' + job.status)),
        h('strong', null, t('hh.run.' + job.step)),
        runningNow ? MA.ui.spinner('hh.job.running') : null,
        runningNow && job.cancellable ? h('button', { type: 'button', 'class': 'btn btn-sm', id: 'hh-cancel', disabled: !!job.cancel_requested, onclick: cancel },
          t(job.cancel_requested ? 'hh.job.cancelling' : 'hh.job.cancel')) : null,
        !runningNow ? h('button', { type: 'button', 'class': 'btn btn-sm btn-ghost', id: 'hh-job-close', onclick: function () { S.job = null; paintJob(); } }, t('common.close')) : null),
      last ? h('p', { 'class': 'hh-job-progress' }, prog, ' ', last.source ? h('span', { 'class': 'muted' }, last.source + ' · ') : null,
        pick(last.message) || last.phase || '', typeof last.done === 'number' ? ' (' + String(last.done) + (typeof last.total === 'number' ? ' / ' + String(last.total) : '') + ')' : '') : null,
      !runningNow ? jobSummary(job) : null);
  }

  // ---------------------------------------------------------------- fej, lépéssor, kártya
  function paintHead() {
    var d = S.status;
    var st = d.state || {};
    var probs = d.problems || [];
    var cli = d.cli || {};
    MA.dom.mount(S.els.head,
      h('p', { 'class': 'hh-question' }, d.initialized ? [h('strong', null, t('hh.head.question')), ' ', (st.pico || {}).question || '—',
        ' ', B('neutral', t('hh.mode.' + (st.mode || 'harvest')))] : t('hh.head.notInit')),
      h('div', { 'class': 'row' },
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'hh-refresh', onclick: function () { refresh(); } }, t('hh.head.refresh')),
        d.counts ? h('span', { 'class': 'muted' }, t('hh.head.counts', { reviews: num((d.counts.reviews || {}).selected || 0),
          records: num(d.counts.records), studies: num(d.counts.studies), decisions: num(d.counts.decisions) })) : null,
        cli.available === false ? B('warning', t('hh.head.cliMissing'), { title: cli.message || '' }) : null),
      probs.length ? h('div', { 'class': 'error-box', role: 'note' }, B('error', 'H001'), ' ', t('hh.head.problems', { n: String(probs.length) }),
        h('ul', null, probs.slice(0, 5).map(function (p) { return h('li', null, h('code', null, p.path), ': ', (p.errors || []).slice(0, 2).join('; ')); }))) : null,
      (cli.findings || []).length ? h('ul', { 'class': 'item-list hh-findings', 'aria-label': t('hh.head.findings') }, cli.findings.slice(0, 8).map(function (f) {
        return h('li', { 'class': 'item' }, B(f.severity === 'error' ? 'error' : 'warning', f.code), ' ', h('span', null, pick(f.hu ? { hu: f.hu, en: f.en } : f.title)),
          MA.why.button({ kb: f.code, code: f.code, title: f.title || f.hu, detail: f.detail, advice: f.advice }, { compact: true }));
      })) : null);
  }

  function stepperKeys(ev) {
    var links = MA.dom.$$('.hh-step-link', S.els.stepper);
    var i = links.indexOf(document.activeElement);
    if (i < 0) { return; }
    var j = null;
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') { j = (i + 1) % links.length; } else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') { j = (i - 1 + links.length) % links.length; } else if (ev.key === 'Home') { j = 0; } else if (ev.key === 'End') { j = links.length - 1; }
    if (j !== null) { ev.preventDefault(); links[j].focus(); }
  }

  function paintStepper() {
    MA.dom.mount(S.els.stepper, STEPS.map(function (def, i) {
      var st = stepState(def);
      var open = openItems(def);
      var on = def.id === S.step;
      return h('li', { 'class': ['hh-step', 'is-' + st, on && 'is-current'], dataset: { step: def.id } },
        h('a', { 'class': 'hh-step-link', href: MA.app.href('headhunter', { step: def.id }), 'aria-current': on ? 'step' : null,
          onclick: function (ev) { ev.preventDefault(); go(def.id, true); } },
          h('span', { 'class': 'hh-step-num', 'aria-hidden': 'true' }, String(i + 1)),
          h('span', { 'class': 'hh-step-label' }, t('hh.step.' + def.id + '.title')),
          h('span', { 'class': 'hh-step-badges' }, stateBadge(st),
            open ? B('warning', t('hh.step.open', { n: String(open) }), { srLabel: t('hh.step.openSr') }) : null)));
    }));
  }

  function card(def) {
    var st = stepState(def);
    var body = h('div', { 'class': 'hh-card-body' });
    var el = h('section', { 'class': 'panel hh-card', id: 'hh-card', 'aria-labelledby': 'hh-card-h', dataset: { step: def.id }, tabindex: '-1' },
      h('div', { 'class': 'row hh-card-head' },
        h('h2', { 'class': 'panel-title', id: 'hh-card-h' }, t('hh.step.' + def.id + '.title')),
        stateBadge(st),
        def.eps.map(function (ep) {
          var n = epOpen(ep);
          return typeof n === 'number' ? B(n ? 'warning' : 'ok', ep + ': ' + String(n), { title: t('hh.ep.' + ep) }) : null;
        })),
      h('p', { 'class': 'hh-lead' }, t('hh.step.' + def.id + '.lead')),
      explain(def.id),
      body);
    return { el: el, body: body, state: st };
  }

  function paintCard(focus) {
    var def = STEPS.filter(function (d) { return d.id === S.step; })[0] || STEPS[0];
    var c = card(def);
    MA.dom.mount(S.els.card, c.el);
    var fn = hh.steps[def.id];
    var my = S;
    var p = null;
    if (!S.status.initialized && def.id !== 'pico' && def.id !== 'sources') {
      c.body.appendChild(h('p', null, badge('neutral', 'hh.locked'), ' ', t('hh.lockedBody')));
      c.body.appendChild(h('button', { type: 'button', 'class': 'btn btn-primary', onclick: function () { go('pico', true); } }, t('hh.goPico')));
    } else if (fn) {
      c.body.appendChild(MA.ui.spinner());
      try {
        p = Promise.resolve(fn(c.body, api, c));
      } catch (e) {
        p = Promise.reject(e);
      }
      p.then(function () {
        var sp = MA.dom.$('.spinner', c.body);
        if (sp && sp.parentNode === c.body) { sp.remove(); }
      }, function (err) {
        if (S !== my || !my.ctx.alive() || (err && err.code === 'ABORTED')) { return; }
        MA.dom.mount(c.body, MA.ui.errorBox(err || {}));
      });
    }
    if (focus) { c.el.focus(); }
  }

  function go(id, focus) {
    if (!S) { return; }
    S.step = id;
    S.ctx.setParams({ step: id });
    paintStepper();
    paintCard(focus);
  }

  // ---------------------------------------------------------------- 1. lépés: források
  function stepSources(host) {
    var d = S.status;
    return get('/sources', { lang: MA.i18n.lang() }).then(function (env) {
      var data = env.data || {};
      var rows = data.rows || [];
      var etag = data.state_etag;
      MA.dom.mount(host,
        h('div', { 'class': 'toolbar' },
          h('button', { type: 'button', 'class': 'btn btn-primary', id: 'hh-src-check', onclick: function () { run('sources_check', {}); } }, t('hh.src.check')),
          h('span', { 'class': 'muted' }, t('hh.src.keysHint'))),
        h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table hh-src-table', id: 'hh-src-table' },
          h('caption', { 'class': 'sr-only' }, t('hh.src.caption')),
          h('thead', null, h('tr', null, ['name', 'enabled', 'status', 'key', 'reset', 'message'].map(function (k) { return h('th', { scope: 'col' }, t('hh.src.col.' + k)); }))),
          h('tbody', null, rows.map(function (r) {
            var cb = h('input', { type: 'checkbox', checked: !!r.enabled, disabled: !d.initialized || r.automatic, id: 'hh-src-' + r.source,
              'aria-label': t('hh.src.toggle', { name: r.name || r.source }),
              onchange: function () {
                var on = cb.checked;
                decide({ kind: 'sources', options: on ? { enable: [r.source] } : { disable: [r.source] } }, etag).catch(function () { cb.checked = !on; });
              } });
            return h('tr', { dataset: { source: r.source } },
              h('th', { scope: 'row' }, r.name || r.source, r.unverified_live ? [' ', B('info', t('hh.src.unverified'))] : null,
                h('div', { 'class': 'muted hh-small' }, r.role || '')),
              h('td', null, cb, r.automatic ? h('span', { 'class': 'muted hh-small' }, ' ' + t('hh.src.auto')) : null),
              h('td', null, B(SRC_KIND[r.status] || 'neutral', t('hh.src.status.' + (r.status || 'unknown')))),
              h('td', null, r.key_configured === true ? t('hh.yes') : (r.key_configured === false ? t('hh.no') : '—'),
                r.insttoken_configured ? h('div', { 'class': 'muted hh-small' }, t('hh.src.insttoken')) : null,
                r.auth ? h('div', { 'class': 'muted hh-small' }, r.auth) : null),
              h('td', null, r.reset_at ? MA.i18n.ts(r.reset_at) : '—'),
              h('td', null, r.message || ''));
          })))),
        !d.initialized ? h('p', { 'class': 'muted' }, t('hh.src.beforeInit')) : null);
    });
  }

  // ---------------------------------------------------------------- 2. lépés: kérdés (PICO)
  function picoForm(st, force) {
    var pico = (st && st.pico) || {};
    var f = {};
    function inp(name, value, area) {
      f[name] = area ? h('textarea', { id: 'hh-pico-' + name, rows: 2, 'class': 'input', value: value || '' })
        : h('input', { type: 'text', id: 'hh-pico-' + name, 'class': 'input', value: value || '', autocomplete: 'off' });
      return F(t('hh.pico.' + name), f[name], MA.i18n.has('hh.pico.' + name + 'Hint') ? t('hh.pico.' + name + 'Hint') : null);
    }
    function terms(c) {
      var b = (pico.query_blocks || []).filter(function (x) { return x.concept === c; })[0];
      return b ? (b.terms || []).join('; ') : '';
    }
    var mode = SEL({ id: 'hh-pico-mode', 'class': 'input' }, [{ value: 'harvest', label: t('hh.mode.harvest') },
      { value: 'own_update', label: t('hh.mode.own_update') }], (st && st.mode) || 'harvest');
    var err = h('p', { 'class': 'hh-err', role: 'alert', hidden: true });
    return h('form', { 'class': 'hh-pico-form', id: 'hh-pico-form', onsubmit: function (ev) {
      ev.preventDefault();
      var q = f.question.value.trim();
      if (!q) { err.textContent = t('hh.pico.questionMissing'); err.hidden = false; f.question.setAttribute('aria-invalid', 'true'); f.question.focus(); return; }
      var opts = { question: q, mode: mode.value };
      ['population', 'intervention', 'comparator', 'outcomes', 'study_designs'].forEach(function (k) { var v = f[k].value.trim(); if (v) { opts[k] = v; } });
      var go2 = function () { run('init', opts); };
      if (force) {
        opts.force = true;
        MA.ui.confirm({ title: t('hh.pico.forceTitle'), message: t('hh.pico.forceBody'), danger: true }).then(function (ok) { if (ok) { go2(); } });
      } else { go2(); }
    } },
    inp('question', pico.question, true), inp('population', pico.population || terms('P')), inp('intervention', pico.intervention || terms('I')),
    inp('comparator', pico.comparator || terms('C')), inp('outcomes', (pico.outcomes || []).join('; ')), inp('study_designs', (pico.study_designs || []).join('; ')),
    F(t('hh.pico.mode'), mode), err,
    h('div', { 'class': 'toolbar' }, h('button', { type: 'submit', 'class': 'btn btn-primary', id: 'hh-pico-submit' }, t(force ? 'hh.pico.reinit' : 'hh.pico.start')),
      h('span', { 'class': 'muted' }, t('hh.pico.approveNote'))));
  }

  function stepPico(host) {
    var d = S.status;
    if (!d.initialized) { MA.dom.mount(host, picoForm(null, false)); return null; }
    var st = d.state || {};
    var pico = st.pico || {};
    MA.dom.mount(host,
      h('dl', { 'class': 'hh-dl' },
        h('dt', null, t('hh.pico.question')), h('dd', null, pico.question || '—'),
        (pico.query_blocks || []).map(function (b) {
          return [h('dt', null, t('hh.pico.block', { c: b.concept })), h('dd', null, (b.terms || []).join(' OR ') || '—', (b.mesh || []).length ? h('span', { 'class': 'muted' }, ' · MeSH: ' + b.mesh.join(', ')) : null)];
        })),
      (st.criteria || []).length ? h('div', null, h('h3', { i18n: 'hh.pico.criteria' }), h('ul', null, st.criteria.map(function (c) {
        return h('li', null, B(c.type === 'exclude' ? 'warning' : 'ok', c.type), ' ', c.domain ? '[' + c.domain + '] ' : '', c.text);
      }))) : null,
      h('h3', { i18n: 'hh.pico.reasons' }),
      h('table', { 'class': 'table table-compact hh-reasons' }, h('tbody', null, (st.exclusion_reasons || []).map(function (r) {
        return h('tr', null, h('th', { scope: 'row' }, r.code), h('td', null, pick(r.label)), h('td', { 'class': 'muted' }, r.domain || ''));
      }))),
      h('details', { 'class': 'hh-reinit' }, h('summary', { i18n: 'hh.pico.edit' }), picoForm(st, true)));
    return null;
  }

  // ---------------------------------------------------------------- keret
  var api = {
    get: get, run: run, decide: decide, refresh: refresh, go: go, reasonDialog: reasonDialog,
    status: function () { return S.status; }, etag: function (key) { var f = ((S.status || {}).files || {})[key]; return f ? f.etag : null; },
    epOpen: epOpen, idList: idList, quote: quote, locator: locator, confBadge: confBadge, explain: explain, num: num, byId: byId,
    busy: function () { return !!(S && S.job && (S.job.status === 'running' || S.job.status === 'queued')); },
    lastJob: function () { return S ? S.job : null; }
  };

  var hh = MA.hh = { steps: { sources: stepSources, pico: stepPico }, STEPS: STEPS, api: api, CONF_KIND: CONF_KIND, SRC_KIND: SRC_KIND };

  function render(root, ctx) {
    root.appendChild(MA.ui.spinner());
    S = { ctx: ctx, els: {}, status: null, step: null, job: null };
    var mine = S;
    ctx.onCleanup(function () { if (S === mine) { S = null; } });
    var offChanges = MA.bus.on('changes', function (ev) {
      var paths = ((ev && (ev.changes || ev.changed)) || []).map(function (c) { return typeof c === 'string' ? c : (c && c.path) || ''; });
      if (S === mine && !api.busy() && paths.some(function (p) { return p.indexOf('01_kereses/headhunter/') === 0; })) { refresh(); }
    });
    ctx.onCleanup(offChanges);
    return loadStatus().then(function () {
      if (!ctx.alive() || S !== mine) { return; }
      var want = ctx.params.step;
      S.step = STEPS.some(function (d) { return d.id === want; }) ? want : recommended();
      S.els.head = h('div', { 'class': 'hh-head', id: 'hh-head' });
      S.els.job = h('section', { 'class': 'panel hh-job', id: 'hh-job', role: 'status', 'aria-live': 'polite', hidden: true });
      S.els.stepper = h('ol', { 'class': 'hh-steps', id: 'hh-steps', onkeydown: stepperKeys });
      S.els.card = h('div', { 'class': 'hh-card-host', id: 'hh-card-host' });
      MA.dom.mount(root,
        h('section', { 'class': 'panel hh-intro', 'aria-labelledby': 'hh-intro-h' },
          h('h2', { 'class': 'panel-title', id: 'hh-intro-h', i18n: 'hh.intro.title' }),
          h('p', { i18n: 'hh.intro.lead' }), S.els.head, explain('intro')),
        S.els.job,
        h('nav', { 'class': 'hh-nav', 'aria-label': t('hh.steps.label') }, S.els.stepper),
        S.els.card);
      paintHead();
      paintJob();
      paintStepper();
      paintCard(false);
      ctx.setTitle(t('hh.step.' + S.step + '.title'));
    });
  }

  MA.selftest.register('headhunter: kilenc lépés, mindnek van renderelője', function (tt) {
    tt.eq(STEPS.length, 9, '9 varázsló-lépés');
    STEPS.forEach(function (d) { tt.ok(typeof hh.steps[d.id] === 'function', 'renderelő: ' + d.id); });
  });

  MA.app.registerScreen({ id: 'headhunter', title_key: 'hh.nav.title', workspace: 'process', tab: 'prisma', order: 30, render: render });
})();
