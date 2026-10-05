/* screens/grade.js — 6 GRADE / SoF kimenetenként (terv 3.5.12, 4.14, 6.5, 7.6; 11/4. és 11/6. döntés).
 *
 * HTTP API (ma_gui/routes/grade*.py):
 *   GET  /api/grade/<kimenet>            → szk.ma.grade-view/v1 + ETag; 424 CAPABILITY_MISSING: details.view (motor-tár nélkül)
 *   PUT  /api/grade/<kimenet>            ← {grade: szk.ma.grade/v1, record?, certainty?} + If-Match
 *                                          409 GATE_BLOCKED (X019 feloldatlan / X001 elavult / X007 régi futás), 422 VALIDATION
 *   GET  /api/grade/<kimenet>/advice?mid= → {run, mid, advice} — a motor grade_help-je (CSAK javaslat)
 *   GET  /api/sof/<kimenet>              → szk.ma.sof-view/v1 (preview: a motor sof()-ja) + ETag
 *   PUT  /api/sof/<kimenet>              ← {assumed_risks, footnotes, dry_run?} + If-Match
 *   POST /api/sof/<kimenet>/export       ← {format: csv|md, lang} → {path, url (aláírt), content?}
 * A domén-ítélet és az indoklás az emberé; a motor bizonyítékot, KB-szabályt és kezdőknek szóló magyarázatot ad
 * („Miért?”), a bizonyosságot és minden SoF-számot a motor számolja. A JS nem formáz és nem számol számot: a lépés
 * jelölése (0, −1 …) szótári címke, a többi szám a motor kész szövege.
 * Állapot csak memóriában (S); böngészőtárolóba nem kerül semmi.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '—' : fb); };

  var CERT_KEY = { 'high': 'grade.high', 'moderate': 'grade.moderate', 'low': 'grade.low', 'very low': 'grade.verylow' };
  var CERT_SYM = { 'high': 'grade.sym.high', 'moderate': 'grade.sym.moderate', 'low': 'grade.sym.low', 'very low': 'grade.sym.verylow' };
  var STEP_KEY = { '0': 'grade.step.zero', '-1': 'grade.step.m1', '-2': 'grade.step.m2', '1': 'grade.step.p1', '2': 'grade.step.p2' };
  // a motor SoF-sorának forrásmező-kulcsai az oszlopkulcsokhoz (grade_help.sof: sources.relative / absolute …)
  var SOF_SRC = { relative_text: 'relative', participants_text: 'participants', assumed_risk_text: 'absolute', absolute_text: 'absolute',
    certainty_cell: 'certainty', certainty_text: 'certainty' };
  var SOF_COLS = ['outcome', 'participants_text', 'relative_text', 'assumed_risk', 'absolute_text', 'certainty_text', 'footnotes'];
  var S = null;

  function clone(o) { return o === undefined || o === null ? o : JSON.parse(JSON.stringify(o)); }
  function key(s) { return String(s).replace(/ /g, '_'); }
  function ratingLabel(r) { return MA.i18n.has('grade.rating.' + key(r)) ? t('grade.rating.' + key(r)) : String(r); }
  function stepLabel(s) { return s === null || s === undefined ? t('grade.step.unresolved') : t(STEP_KEY[String(s)] || 'grade.step.zero'); }
  function certLabel(c) { return CERT_KEY[c] ? t(CERT_SYM[c]) + ' ' + t(CERT_KEY[c]) : '—'; }
  function enc(s) { return encodeURIComponent(s); }

  // ---------------------------------------------------------------- kimenet
  function outcomes() {
    var p = MA.store.get('project') || {};
    return Array.isArray(p.outcomes) ? p.outcomes : [];
  }
  function pickOutcome(id) {
    var list = outcomes();
    for (var i = 0; i < list.length; i++) { if (list[i].id === id) { return list[i]; } }
    return list.filter(function (o) { return !!o.primary_spec; })[0] || list[0] || null;
  }

  // ---------------------------------------------------------------- munkapéldány
  function stepsOf(dm, rating) {
    var list = ((S.view.vocab || {}).ratings || {})[dm] || [];
    for (var i = 0; i < list.length; i++) { if (list[i].rating === rating) { return list[i].steps; } }
    return null;
  }

  function workingCopy(view) {
    var v = view.vocab || {};
    var g = clone(view.grade) || {};
    var out = {
      schema: 'szk.ma.grade/v1',
      run_id: view.run ? view.run.run_id : (g.run_id || null),
      start: g.start || ((view.outcome || {}).grade_start === 'low' ? 'low' : 'high'),
      start_reason: g.start_reason || null,
      domains: {}, upgrades: {}, upgrade_details: {},
      mid_text: g.mid_text || null
    };
    (v.domains || []).forEach(function (dm) {
      var d = (g.domains || {})[dm] || {};
      out.domains[dm] = { rating: d.rating || null, step: d.step === undefined ? null : d.step, rationale: d.rationale || '' };
    });
    (v.upgrades || []).forEach(function (u) {
      var det = (g.upgrade_details || {})[u.id] || {};
      out.upgrades[u.id] = !!(g.upgrades || {})[u.id];
      out.upgrade_details[u.id] = { step: det.step || u.steps[0], rationale: det.rationale || '' };
    });
    return out;
  }

  function payload() {
    var d = S.doc;
    var out = { schema: d.schema, run_id: d.run_id, start: d.start, start_reason: (d.start_reason || '').trim() || null,
      domains: {}, upgrades: {}, upgrade_details: {}, mid_text: (d.mid_text || '').trim() || null };
    var draft = engineDraft();
    Object.keys(d.domains).forEach(function (dm) {
      var x = d.domains[dm];
      out.domains[dm] = { rating: x.rating || null, step: x.rating ? x.step : null, rationale: (x.rationale || '').trim() || null };
      var ed = draft && draft.domains ? draft.domains[dm] : null;
      if (ed && ed.suggestion) { out.domains[dm].suggestion = ed.suggestion; }
      if (ed && ed.advisory) { out.domains[dm].advisory = ed.advisory; }
    });
    if (draft && draft.run_summary) { out.run_summary = draft.run_summary; }
    Object.keys(d.upgrades).forEach(function (u) {
      out.upgrades[u] = !!d.upgrades[u];
      if (d.upgrades[u]) { out.upgrade_details[u] = { step: d.upgrade_details[u].step, rationale: (d.upgrade_details[u].rationale || '').trim() || null }; }
    });
    return out;
  }

  /** UX-előjelzés (a döntés a szerveré / motoré): hiányzó és feloldatlan domének. */
  function state() {
    var missing = [], unresolved = [];
    Object.keys(S.doc.domains).forEach(function (dm) {
      var x = S.doc.domains[dm];
      if (!x.rating) { missing.push(dm); return; }
      var steps = stepsOf(dm, x.rating) || [];
      if (x.step === null && steps.indexOf(null) >= 0) { unresolved.push(dm); }
    });
    return { missing: missing, unresolved: unresolved };
  }

  function touch() {
    S.dirty = true;
    refreshStatus();
  }

  // ---------------------------------------------------------------- tanács (motor)
  /** A motor tanácsa egységes alakban. A motor grade_help.advice-a szk.ma.grade/v1 PISZKOZATOT ad: doménenként
   * suggestion {rating, step, status, summary, why, flags[{code, level, text}], kb_refs}, a felminősítésekhez
   * advice.upgrades {large_effect: {candidate_step}|null, …, flags, kb_refs}. (A régebbi, lapos alakot is olvassuk.) */
  function adviceOf(dm, isUp) {
    var a = S.advice || {};
    if (isUp) {
      var eu = a.advice && a.advice.upgrades;
      if (eu && typeof eu === 'object') {
        var info = eu[dm];
        if (info && typeof info === 'object' && (info.summary || info.evidence || info.why)) {
          return { suggested: !!info.suggested, summary: info.summary || null, why: info.why && typeof info.why === 'object' ? info.why : null,
            evidence: evidenceOf(info), kb_refs: Array.isArray(info.kb_refs) ? info.kb_refs : (Array.isArray(eu.kb_refs) ? eu.kb_refs : []) };
        }
        var flags = (Array.isArray(eu.flags) ? eu.flags : []).filter(function (f) {
          return f && (f.code === dm || (dm !== 'large_effect' && f.code === 'upgrade_prompt'));
        });
        return { suggested: !!(info && info.candidate_step), evidence: flags.map(function (f) { return { text: f.text, level: f.level }; }),
          kb_refs: Array.isArray(eu.kb_refs) ? eu.kb_refs : [] };
      }
      return a.upgrades && typeof a.upgrades === 'object' ? a.upgrades[dm] || null : null;
    }
    var d = a.domains && typeof a.domains === 'object' ? a.domains[dm] : null;
    if (!d) { return null; }
    if (d.suggestion && typeof d.suggestion === 'object') {
      var sg = d.suggestion;
      var structured = sg.why && typeof sg.why === 'object' && (sg.why.asks || sg.why.because || sg.why.change);
      return { suggested_rating: sg.rating || null, status: sg.status || null, summary: sg.summary || null,
        why: structured ? sg.why : null, why_text: structured ? null : (sg.why || null),
        concern: typeof sg.concern === 'boolean' ? sg.concern : (typeof sg.step === 'number' && sg.step < 0),
        evidence: evidenceOf(sg), kb_refs: Array.isArray(sg.kb_refs) ? sg.kb_refs : [] };
    }
    return d;
  }

  /** A motor bizonyítéka ({label, text}) és jelzései ({code, level, text}) egy listában — szöveg, számformázás nélkül. */
  function evidenceOf(x) {
    var ev = Array.isArray(x.evidence) ? x.evidence.filter(function (e) { return e && typeof e === 'object'; }) : [];
    var fl = Array.isArray(x.flags) ? x.flags.filter(function (f) { return f && typeof f === 'object'; }).map(function (f) { return { text: f.text, level: f.level }; }) : [];
    return ev.concat(fl);
  }

  /** A motor piszkozatának a futáshoz kötött részei (run_summary, doménenkénti suggestion / advisory) — a mentett
   * ítélet így a motor saját adataival rögzíthető (k, résztvevők, hatás-szöveg; a tanácstól eltérés figyelmeztetése). */
  function engineDraft() {
    var a = S.advice;
    if (!a || a.schema !== 'szk.ma.grade/v1' || !S.doc || a.run_id !== S.doc.run_id) { return null; }
    return a;
  }

  /** A „Miért?” gomb specifikációja: ugyanaz az objektum marad a gombnál, a motor-tanács betöltése után frissül. */
  function whyRef(dm, isUp) {
    var k = (isUp ? 'u:' : 'd:') + dm;
    S.whySpecs = S.whySpecs || {};
    if (!S.whySpecs[k]) { S.whySpecs[k] = {}; }
    var spec = S.whySpecs[k];
    Object.keys(spec).forEach(function (f) { delete spec[f]; });
    return Object.assign(spec, whySpec(dm, isUp));
  }

  function whySpec(dm, isUp) {
    var a = adviceOf(dm, isUp) || {};
    var kb = Array.isArray(a.kb_refs) ? a.kb_refs : [];
    if (a.why_text) {
      // a motor kezdőknek szóló magyarázata (mit kérdez, miért ez a javaslat, mi változtatná meg)
      return { title: t(isUp ? 'grade.up.' + dm : 'grade.dom.' + dm), detail: a.why_text, plain: { asks: t('grade.help.' + dm + '.asks') }, kb: kb };
    }
    var w = a.why && typeof a.why === 'object' ? a.why : {};
    var base = 'grade.help.' + dm + '.';
    return {
      title: t(isUp ? 'grade.up.' + dm : 'grade.dom.' + dm),
      plain: {
        asks: w.asks || t(base + 'asks'),
        because: w.because || t(base + 'because'),
        change: w.change || t(base + 'change'),
        uncertain: w.uncertain || null
      },
      kb: kb
    };
  }

  function adviceCell(dm, isUp) {
    if (S.adviceErr) { return h('span', { 'class': 'muted' }, t('grade.advice.none')); }
    if (!S.advice) { return h('span', { 'class': 'muted' }, t('grade.advice.loading')); }
    var a = adviceOf(dm, isUp);
    if (!a) { return h('span', { 'class': 'muted' }, t('grade.advice.human')); }
    var sug = isUp ? null : a.suggested_rating;
    var ev = Array.isArray(a.evidence) ? a.evidence : [];
    var notes = Array.isArray(a.notes) ? a.notes : [];
    var status = !isUp && a.status && a.status !== 'suggested' && MA.i18n.has('grade.status.' + a.status) ? MA.ui.badge('neutral', t('grade.status.' + a.status)) : null;
    return h('div', { 'class': 'gr-adv' },
      sug || status ? h('p', { 'class': 'gr-adv-sug' }, sug ? MA.ui.badge(a.concern ? 'warning' : 'info', t('grade.advice.suggests', { rating: ratingLabel(sug) })) : null,
        sug && status ? ' ' : null, status) : null,
      isUp && a.suggested ? h('p', { 'class': 'gr-adv-sug' }, MA.ui.badge('info', t('grade.advice.upSuggested'))) : null,
      a.summary ? h('p', { 'class': 'gr-adv-sum' }, pick(a.summary)) : null,
      ev.length ? h('ul', { 'class': 'gr-adv-ev' }, ev.map(function (e) {
        var lbl = e && e.label ? pick(e.label, '') : '';
        var mark = e && (e.level === 'warning' || e.level === 'major') ? MA.ui.badge('warning', null, { srLabel: t('grade.advice.flag') }) : null;
        return h('li', null, mark, mark ? ' ' : null, lbl ? h('span', { 'class': 'muted' }, lbl + ': ') : null, h('span', { 'class': 'gr-ev-text' }, pick(e ? e.text : null)));
      })) : null,
      notes.length ? h('ul', { 'class': 'gr-adv-notes' }, notes.map(function (n) { return h('li', null, pick(n)); })) : null,
      Array.isArray(a.kb_refs) && a.kb_refs.length ? h('p', { 'class': 'gr-adv-kb' }, a.kb_refs.map(function (id) { return MA.why.kbButton(id); })) : null);
  }

  function renderAdvice() {
    Object.keys(S.doc.domains).forEach(function (dm) {
      var host = MA.dom.$('#gr-adv-' + dm);
      if (host) { MA.dom.mount(host, adviceCell(dm, false)); }
      concernNote(dm);
      whyRef(dm, false);
    });
    Object.keys(S.doc.upgrades).forEach(function (u) {
      var host = MA.dom.$('#gr-uadv-' + u);
      if (host) { MA.dom.mount(host, adviceCell(u, true)); }
      whyRef(u, true);
    });
    var st = MA.dom.$('#gr-start-adv');
    var s = S.advice && S.advice.start;
    var sv = typeof s === 'string' ? s : (s && s.suggested) || null;
    if (st) {
      MA.dom.mount(st, sv === 'high' || sv === 'low' ? t('grade.start.suggested', { start: t('grade.start.' + sv), reason: s && s.reason ? pick(s.reason, '') : t('grade.start.fromProject') }) : '');
    }
    var note = MA.dom.$('#gr-adv-note');
    var notes = S.advice && S.advice.advice && Array.isArray(S.advice.advice.notes) ? S.advice.advice.notes : [];
    if (note) {
      MA.dom.mount(note, S.adviceErr ? MA.ui.errorBox(S.adviceErr)
        : (notes.length ? h('ul', { 'class': 'gr-adv-notes muted', id: 'gr-adv-notes' }, notes.map(function (n) { return h('li', null, pick(n, '')); })) : null));
    }
  }

  function loadAdvice(ctx) {
    var q = {};
    if (S.doc.mid_text && S.doc.mid_text.trim()) { q.mid = S.doc.mid_text.trim(); }
    if (S.view.run && S.view.run.run_id) { q.run = S.view.run.run_id; }
    S.advice = null;
    S.adviceErr = null;
    renderAdvice();

    var mine = S;
    return MA.api.get('/api/grade/' + enc(S.oid) + '/advice', { query: q, signal: ctx.signal, toast: false }).then(function (env) {
      if (S !== mine) { return; }
      S.advice = (env.data || {}).advice || {};
      renderAdvice();
    }, function (err) {
      if (S !== mine || err.code === 'ABORTED') { return; }
      S.adviceErr = err;
      renderAdvice();
    });
  }

  /** Ha a motor-tanács leminősítést jelez, de az ember „nem súlyos”-t választott: az indoklás külön fontos. */
  function concernNote(dm) {
    var host = MA.dom.$('#gr-concern-' + dm);
    if (!host) { return; }
    var a = adviceOf(dm, false);
    var x = S.doc.domains[dm];
    var show = !!(a && a.concern && x.rating && x.step === 0);
    MA.dom.mount(host, show ? MA.ui.badge('warning', t('grade.concern')) : null);
  }

  // ---------------------------------------------------------------- domének
  function stepCell(dm) {
    var x = S.doc.domains[dm];
    var host = MA.dom.$('#gr-step-' + dm);
    if (!host) { return; }
    var steps = x.rating ? stepsOf(dm, x.rating) || [] : [];
    if (!x.rating) { MA.dom.mount(host, h('span', { 'class': 'muted' }, '—')); return; }
    if (steps.length === 1) {
      MA.dom.mount(host, h('span', { 'class': 'gr-step num', id: 'gr-stepv-' + dm }, stepLabel(steps[0])));
      return;
    }
    var name = 'gr-stepr-' + dm;
    var choices = steps.filter(function (s) { return s !== null; });
    var badge = h('span', { id: 'gr-stepb-' + dm });
    function mark() { MA.dom.mount(badge, x.step === null ? MA.ui.badge('warning', t('grade.step.unresolved'), { title: t('grade.x019.short') }) : null); }
    var group = h('div', { 'class': 'gr-steps', role: 'radiogroup', 'aria-label': t('grade.step.choose', { domain: t('grade.dom.' + dm) }) },
      choices.map(function (s, i) {
        var id = name + '-' + String(i);
        return h('label', { 'class': 'gr-step-opt', htmlFor: id },
          h('input', { type: 'radio', name: name, id: id, checked: x.step === s, onchange: function () { x.step = s; mark(); concernNote(dm); touch(); } }),
          ' ', stepLabel(s));
      }));
    mark();
    MA.dom.mount(host, group, badge);
  }

  function setRating(dm, rating) {
    var x = S.doc.domains[dm];
    x.rating = rating || null;
    var steps = rating ? stepsOf(dm, rating) || [] : [];
    x.step = steps.length ? (steps[0] === null ? null : steps[0]) : null;
    stepCell(dm);
    concernNote(dm);
    touch();
  }

  function rationaleField(id, value, labelText, onInput) {
    var err = h('span', { 'class': 'gr-err', id: id + '-err' });
    var ta = h('textarea', { id: id, rows: 2, 'class': 'gr-rat', 'aria-required': 'true', 'aria-label': labelText,
      oninput: function () { onInput(ta.value); if (ta.value.trim()) { setInvalid(ta, false); } touch(); } });
    ta.value = value || '';
    return h('div', { 'class': 'gr-ratwrap' }, ta, err);
  }

  function setInvalid(el, on) {
    var err = MA.dom.$('#' + el.id + '-err');
    if (on) {
      el.setAttribute('aria-invalid', 'true');
      el.setAttribute('aria-describedby', el.id + '-err');
      if (err) { err.textContent = t('grade.rationaleRequired'); }
    } else {
      el.removeAttribute('aria-invalid');
      el.removeAttribute('aria-describedby');
      if (err) { err.textContent = ''; }
    }
  }

  function domainRow(dm) {
    var x = S.doc.domains[dm];
    var ratings = (S.view.vocab.ratings || {})[dm] || [];
    var sel = h('select', { id: 'gr-rating-' + dm, 'aria-label': t('grade.col.rating') + ' — ' + t('grade.dom.' + dm),
      onchange: function () { setRating(dm, sel.value); } },
    [h('option', { value: '' }, t('grade.choose'))].concat(ratings.map(function (r) {
      return h('option', { value: r.rating, selected: r.rating === x.rating }, ratingLabel(r.rating));
    })));
    return h('tr', { dataset: { domain: dm } },
      h('th', { scope: 'row', 'class': 'gr-dom' }, h('span', null, t('grade.dom.' + dm)), ' ', MA.why.button(whyRef(dm, false), { compact: true, id: 'gr-why-' + dm })),
      h('td', null, sel, h('div', { id: 'gr-concern-' + dm, 'class': 'gr-concern' })),
      h('td', { id: 'gr-step-' + dm, 'class': 'gr-stepcell' }),
      h('td', null, rationaleField('gr-rat-' + dm, x.rationale, t('grade.col.rationale') + ' — ' + t('grade.dom.' + dm), function (v) { x.rationale = v; })),
      h('td', { id: 'gr-adv-' + dm, 'class': 'gr-advcell' }));
  }

  function upgradeRow(u) {
    var on = S.doc.upgrades[u.id];
    var det = S.doc.upgrade_details[u.id];
    var stepSel = h('select', { id: 'gr-ustep-' + u.id, 'aria-label': t('grade.col.step') + ' — ' + t('grade.up.' + u.id), disabled: !on,
      onchange: function () { det.step = u.steps[stepSel.selectedIndex]; touch(); } },
    u.steps.map(function (s) { return h('option', { value: String(s), selected: det.step === s }, stepLabel(s)); }));
    var rat = rationaleField('gr-urat-' + u.id, det.rationale, t('grade.col.rationale') + ' — ' + t('grade.up.' + u.id), function (v) { det.rationale = v; });
    var box = h('input', { type: 'checkbox', id: 'gr-up-' + u.id, checked: on, onchange: function () {
      S.doc.upgrades[u.id] = box.checked;
      stepSel.disabled = !box.checked;
      if (!box.checked) { setInvalid(MA.dom.$('#gr-urat-' + u.id), false); }
      touch();
    } });
    return h('tr', { dataset: { upgrade: u.id } },
      h('th', { scope: 'row', 'class': 'gr-dom' }, h('label', { htmlFor: 'gr-up-' + u.id }, box, ' ', t('grade.up.' + u.id)), ' ',
        MA.why.button(whyRef(u.id, true), { compact: true, id: 'gr-uwhy-' + u.id })),
      h('td', null, stepSel),
      h('td', null, rat),
      h('td', { id: 'gr-uadv-' + u.id, 'class': 'gr-advcell' }));
  }

  // ---------------------------------------------------------------- állapot, bizonyosság, kapu
  function refreshStatus() {
    var st = state();
    var banner = MA.dom.$('#gr-x019');
    if (banner) {
      banner.hidden = st.unresolved.length === 0;
    }
    var g = S.view.grade || null;
    var certHost = MA.dom.$('#gr-cert');
    if (certHost) {
      var parts = [];
      if (S.dirty) {
        parts.push(h('span', { 'class': 'muted' }, t('grade.cert.afterSave')));
      } else if (g && g.certainty) {
        parts.push(h('strong', { 'class': 'gr-cert-val', id: 'gr-cert-val' }, certLabel(g.certainty)), ' ', h('span', { 'class': 'muted' }, t('grade.cert.engine')));
      } else if (st.unresolved.length) {
        parts.push(h('span', { id: 'gr-cert-val' }, '—'), ' ', h('span', { 'class': 'muted' }, t('grade.cert.unresolved')));
      } else if (st.missing.length) {
        parts.push(h('span', { id: 'gr-cert-val' }, '—'), ' ', h('span', { 'class': 'muted' }, t('grade.cert.missing', { list: st.missing.map(function (d) { return t('grade.dom.' + d); }).join(', ') })));
      } else {
        parts.push(h('span', { id: 'gr-cert-val' }, '—'), ' ', h('span', { 'class': 'muted' }, t('grade.cert.noEngine')));
      }
      MA.dom.mount(certHost, parts);
    }
    var human = MA.dom.$('#gr-human-cert');
    if (human) { human.hidden = !needHumanCertainty(); }
    var why = [];
    if (!S.view.run) { why.push(t('grade.gate.noRun')); }
    if (S.view.run && S.view.run.stale === true) { why.push(t('grade.gate.stale')); }
    if (st.missing.length) { why.push(t('grade.gate.missing', { list: st.missing.map(function (d) { return t('grade.dom.' + d); }).join(', ') })); }
    if (st.unresolved.length) { why.push(t('grade.gate.unresolved')); }
    if (!S.view.engine || !S.view.engine.grade_put) { why.push(t('grade.gate.noEngine')); }
    var rec = MA.dom.$('#gr-record');
    if (rec) {
      rec.disabled = why.length > 0;
      MA.dom.mount(MA.dom.$('#gr-record-why'), why.length ? why.join(' · ') : '');
    }
    var save = MA.dom.$('#gr-save');
    if (save) { save.disabled = !S.view.engine || !S.view.engine.grade_put || !S.view.run; }
    var dirty = MA.dom.$('#gr-dirty');
    if (dirty) { dirty.textContent = S.dirty ? t('grade.unsaved') : ''; }
  }

  function needHumanCertainty() {
    var g = S.view.grade;
    var st = state();
    return !S.dirty && !!g && !g.certainty && !st.missing.length && !st.unresolved.length;
  }

  function validate() {
    var first = null;
    Object.keys(S.doc.domains).forEach(function (dm) {
      var x = S.doc.domains[dm];
      var el = MA.dom.$('#gr-rat-' + dm);
      var bad = !!x.rating && !(x.rationale || '').trim();
      setInvalid(el, bad);
      if (bad && !first) { first = el; }
    });
    Object.keys(S.doc.upgrades).forEach(function (u) {
      var el = MA.dom.$('#gr-urat-' + u);
      var bad = S.doc.upgrades[u] && !(S.doc.upgrade_details[u].rationale || '').trim();
      setInvalid(el, bad);
      if (bad && !first) { first = el; }
    });
    if (first) {
      first.focus();
      MA.dom.mount(MA.dom.$('#gr-msg'), MA.ui.badge('error', t('grade.fixErrors')));
      return false;
    }
    return true;
  }

  function showGate(err) {
    var box = MA.dom.$('#gr-msg');
    var d = err.details || {};
    MA.dom.mount(box, h('div', { 'class': 'error-box gr-gate', role: 'alert' },
      h('p', { 'class': 'error-box-msg' }, MA.ui.badge(err.code === 'GATE_BLOCKED' ? 'blocker' : 'error', d.code || err.code), ' ', err.message),
      d.code ? MA.why.kbButton(d.code) : null));
  }

  function save(ctx, record) {
    if (!validate()) { return Promise.resolve(); }
    MA.dom.mount(MA.dom.$('#gr-msg'), null);
    var body = { grade: payload(), record: !!record };
    var hc = MA.dom.$('#gr-human-cert-sel');
    if (record && needHumanCertainty() && hc && hc.value) { body.certainty = hc.value; }
    var btn = MA.dom.$(record ? '#gr-record' : '#gr-save');
    if (btn) { btn.disabled = true; }
    return MA.api.put('/api/grade/' + enc(S.oid), body, { ifMatch: S.etag, quiet: ['GATE_BLOCKED', 'VALIDATION'] }).then(function (env) {
      if (!ctx.alive()) { return; }
      var d = env.data || {};
      (env.warnings || []).forEach(function (w) { MA.ui.toast({ kind: 'warning', title: t('grade.engineWarning'), message: String(w) }); });
      MA.ui.toast({ kind: 'success', title: d.recorded ? t('grade.recorded', { id: String(d.recorded.id), cert: certLabel(d.recorded.certainty) }) : t('grade.saved') });
      S.view = d;
      S.etag = env.etag;
      S.dirty = false;
      ctx.rerender();
    }, function (err) {
      if (!ctx.alive()) { return; }
      if (btn) { btn.disabled = false; }
      if (err.code === 'GATE_BLOCKED' || err.code === 'VALIDATION') { showGate(err); }
      if (err.code === 'VALIDATION' && err.details && err.details.needs_certainty) {
        // a piszkozat elmentődött: friss ETag és nézet, majd az ember választja ki a szintet
        reload(ctx).then(function () {
          var hcs = MA.dom.$('#gr-human-cert');
          if (hcs) { hcs.hidden = false; }
          showGate(err);
          var sel = MA.dom.$('#gr-human-cert-sel');
          if (sel) { sel.focus(); }
        });
      }
      refreshStatus();
    });
  }

  function reload(ctx) {
    return MA.api.get('/api/grade/' + enc(S.oid), { signal: ctx.signal, toast: false }).then(function (env) {
      S.view = env.data;
      S.etag = env.etag;
      S.dirty = false;
      refreshStatus();
    }, function () { return null; });
  }

  // ---------------------------------------------------------------- SoF
  function sofCell(row, k) {
    if (k === 'assumed_risk') {
      var ar = row.assumed_risk;
      if (ar && typeof ar === 'object') {
        var lbl = pick(ar.label, '');
        var txt = pick(ar.text, '');
        return lbl && txt ? lbl + ': ' + txt : (txt || lbl || '—');
      }
      return pick(row.assumed_risk_text);
    }
    if (k === 'certainty_text' && (row.certainty_text === undefined || row.certainty_text === null)) {
      return row.certainty ? certLabel(row.certainty) : '—';
    }
    if (k === 'footnotes') {
      var f = Array.isArray(row.footnotes) ? row.footnotes : (Array.isArray(row.footnote_refs) ? row.footnote_refs : []);
      return f.length ? f.join(', ') : '';
    }
    return pick(row[k]);
  }

  function sofColumns(doc) {
    if (Array.isArray(doc.columns) && doc.columns.length) {
      return doc.columns.filter(function (c) { return c && typeof c.key === 'string'; }).map(function (c) { return { key: c.key, label: pick(c.label, c.key) }; });
    }
    return SOF_COLS.map(function (k) { return { key: k, label: t('sof.col.' + k) }; });
  }

  function sofTable(doc) {
    if (!doc || !Array.isArray(doc.rows)) { return MA.ui.emptyState('sof.noPreview'); }
    var cols = sofColumns(doc);
    var notes = Array.isArray(doc.footnotes) ? doc.footnotes : [];
    return h('div', { 'class': 'sof-preview' },
      h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table table-compact sof-table', id: 'sof-table' },
        h('caption', null, t('sof.caption'), ' ', h('span', { 'class': 'muted' }, t('sof.captionNote'))),
        h('thead', null, h('tr', null, cols.map(function (c) { return h('th', { scope: 'col' }, c.label); }))),
        h('tbody', null, doc.rows.filter(function (r) { return r && typeof r === 'object'; }).map(function (r) {
          var src = r.sources && typeof r.sources === 'object' ? r.sources : {};
          return h('tr', null, cols.map(function (c, i) {
            var s = src[c.key] || src[SOF_SRC[c.key] || ''] || '';
            var attrs = { 'class': i ? 'sof-cell' : null, dataset: { col: c.key, source: s || null }, title: s ? t('sof.source', { src: s }) : null };
            return i ? h('td', attrs, sofCell(r, c.key)) : h('th', Object.assign({ scope: 'row' }, attrs), sofCell(r, c.key));
          }));
        })))),
      notes.length ? h('ul', { 'class': 'sof-notes', id: 'sof-notes', 'aria-label': t('sof.footnotes') }, notes.map(function (n) {
        return h('li', null, n && n.id ? h('strong', null, String(n.id) + ' ') : null, pick(n && typeof n === 'object' && 'text' in n ? n.text : n));
      })) : null,
      doc.statement ? h('blockquote', { 'class': 'sof-statement', id: 'sof-statement' }, h('span', { 'class': 'muted' }, t('sof.statement') + ' '), pick(doc.statement)) : null);
  }

  function riskRow(r, i, rerenderRisks) {
    var src = h('select', { id: 'sof-src-' + i, 'aria-label': t('sof.risk.source') + ' — ' + String(i + 1),
      onchange: function () { r.source = src.value; if (r.source !== 'external') { r.per_1000 = null; } S.sofDirty = true; rerenderRisks(); } },
    ['control_pool', 'external'].map(function (v) { return h('option', { value: v, selected: r.source === v }, t('sof.risk.src.' + v)); }));
    var label = h('input', { type: 'text', id: 'sof-label-' + i, value: r.label || '', 'aria-label': t('sof.risk.label') + ' — ' + String(i + 1),
      oninput: function () { r.label = label.value; S.sofDirty = true; } });
    var per = r.source === 'external' ? h('input', { type: 'text', id: 'sof-per-' + i, value: r.per_1000 || '', inputmode: 'decimal',
      'aria-label': t('sof.risk.per1000') + ' — ' + String(i + 1), oninput: function () { r.per_1000 = per.value; S.sofDirty = true; } }) : null;
    return h('li', { 'class': 'sof-risk' },
      src, label, per, per ? h('span', { 'class': 'muted' }, t('sof.risk.perUnit')) : null,
      S.sofInputs.assumed_risks.length > 1 ? h('button', { type: 'button', 'class': 'btn-icon', 'aria-label': t('sof.risk.remove', { n: String(i + 1) }),
        onclick: function () { S.sofInputs.assumed_risks.splice(i, 1); S.sofDirty = true; rerenderRisks(); } }, '×') : null);
  }

  function sofBody(ctx, host) {
    var v = S.sof;
    var risksHost = h('ul', { 'class': 'sof-risks', id: 'sof-risks' });
    function rerenderRisks() { MA.dom.mount(risksHost, S.sofInputs.assumed_risks.map(function (r, i) { return riskRow(r, i, rerenderRisks); })); }
    rerenderRisks();
    var notesHost = h('ul', { 'class': 'sof-unotes', id: 'sof-unotes' });
    function rerenderNotes() {
      MA.dom.mount(notesHost, S.sofInputs.footnotes.map(function (n, i) {
        var ta = h('textarea', { id: 'sof-note-' + i, rows: 1, 'aria-label': t('sof.note.label', { n: String(i + 1) }), oninput: function () { n.text = ta.value; S.sofDirty = true; } });
        ta.value = n.text || '';
        return h('li', null, ta, h('button', { type: 'button', 'class': 'btn-icon', 'aria-label': t('sof.note.remove', { n: String(i + 1) }),
          onclick: function () { S.sofInputs.footnotes.splice(i, 1); S.sofDirty = true; rerenderNotes(); } }, '×'));
      }));
    }
    rerenderNotes();
    var preview = h('div', { id: 'sof-preview-host' }, sofTable(v.preview));
    var exportHost = h('div', { id: 'sof-export-result', 'class': 'sof-export-result', role: 'status' });
    var lang = h('select', { id: 'sof-lang', 'aria-label': t('sof.export.lang') }, ['en', 'hu'].map(function (l) { return h('option', { value: l }, t('sof.export.lang.' + l)); }));
    function body() {
      return { assumed_risks: S.sofInputs.assumed_risks.map(function (r) {
        return { label: (r.label || '').trim() || null, source: r.source, per_1000: r.source === 'external' ? ((r.per_1000 || '').trim() || null) : null };
      }), footnotes: S.sofInputs.footnotes.filter(function (n) { return (n.text || '').trim(); }).map(function (n) { return { text: n.text.trim() }; }) };
    }
    function put(dry) {
      var b = body();
      b.dry_run = !!dry;
      return MA.api.put('/api/sof/' + enc(S.oid), b, { ifMatch: dry ? null : S.sofEtag }).then(function (env) {
        if (!ctx.alive()) { return; }
        S.sof = env.data;
        if (!dry) { S.sofEtag = env.etag; S.sofDirty = false; MA.ui.toast({ kind: 'success', title: t('sof.saved') }); }
        (env.warnings || []).forEach(function (w) { MA.ui.toast({ kind: 'warning', title: t('sof.warning'), message: String(w) }); });
        MA.dom.mount(preview, sofTable(S.sof.preview));
        MA.dom.mount(MA.dom.$('#sof-saved'), savedLine());
      }, function () { /* toast */ });
    }
    function doExport(fmt) {
      return MA.api.post('/api/sof/' + enc(S.oid) + '/export', { format: fmt, lang: lang.value }).then(function (env) {
        if (!ctx.alive()) { return; }
        var d = env.data || {};
        var name = String(d.path || '').split('/').pop();
        MA.dom.mount(exportHost,
          h('p', null, MA.ui.badge('ok', t('sof.export.done', { path: d.path || '—' })), ' ',
            d.url ? h('a', { href: d.url, download: name, 'class': 'btn btn-sm', id: 'sof-dl' }, t('sof.export.download')) : null),
          d.content ? h('div', { 'class': 'sof-md' }, h('pre', { 'class': 'cmd', id: 'sof-md', tabindex: '0', 'aria-label': t('sof.export.md') }, d.content),
            MA.ui.copyButton(function () { return d.content; })) : null);
      }, function () { /* toast */ });
    }
    function savedLine() {
      var s = S.sof.saved;
      if (!s) { return h('span', { 'class': 'muted' }, t('sof.notSaved')); }
      return [h('span', null, t('sof.savedAt', { path: S.sof.path || '—' })),
        S.sof.saved_matches_run === false ? [' ', MA.ui.badge('stale', t('sof.oldRun'))] : null];
    }
    MA.dom.mount(host,
      h('p', { 'class': 'muted' }, t('sof.intro')),
      h('h3', { 'class': 'gr-h3' }, t('sof.risks')),
      h('p', { 'class': 'muted' }, t('sof.risks.help')),
      risksHost,
      h('div', { 'class': 'toolbar' },
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'sof-add-risk', disabled: S.sofInputs.assumed_risks.length >= 6, onclick: function () {
          S.sofInputs.assumed_risks.push({ label: '', source: 'external', per_1000: '' });
          S.sofDirty = true;
          rerenderRisks();
          var el = MA.dom.$('#sof-label-' + (S.sofInputs.assumed_risks.length - 1));
          if (el) { el.focus(); }
        } }, t('sof.risk.add'))),
      h('h3', { 'class': 'gr-h3' }, t('sof.userNotes')),
      notesHost,
      h('div', { 'class': 'toolbar' },
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'sof-add-note', onclick: function () {
          S.sofInputs.footnotes.push({ text: '' });
          S.sofDirty = true;
          rerenderNotes();
          var el = MA.dom.$('#sof-note-' + (S.sofInputs.footnotes.length - 1));
          if (el) { el.focus(); }
        } }, t('sof.note.add')),
        h('button', { type: 'button', 'class': 'btn', id: 'sof-preview', onclick: function () { put(true); } }, t('sof.preview')),
        h('button', { type: 'button', 'class': 'btn btn-primary', id: 'sof-save', onclick: function () { put(false); } }, t('sof.save')),
        h('span', { id: 'sof-saved', 'class': 'sof-saved' }, savedLine())),
      h('h3', { 'class': 'gr-h3' }, t('sof.previewTitle')),
      preview,
      h('h3', { 'class': 'gr-h3' }, t('sof.exportTitle')),
      h('p', { 'class': 'muted' }, t('sof.export.help')),
      h('div', { 'class': 'toolbar' },
        lang,
        h('button', { type: 'button', 'class': 'btn', id: 'sof-export-csv', onclick: function () { doExport('csv'); } }, t('sof.export.csv')),
        h('button', { type: 'button', 'class': 'btn', id: 'sof-export-md', onclick: function () { doExport('md'); } }, t('sof.export.mdBtn'))),
      exportHost);
  }

  function loadSof(ctx, host) {
    if (!S.view.run) { MA.dom.mount(host, MA.ui.emptyState('sof.noRun')); return Promise.resolve(); }
    MA.dom.mount(host, MA.ui.spinner());
    var mine = S;
    return MA.api.get('/api/sof/' + enc(S.oid), { signal: ctx.signal, toast: false }).then(function (env) {
      if (S !== mine || !ctx.alive()) { return; }
      S.sof = env.data || {};
      S.sofEtag = env.etag;
      var inp = S.sof.inputs || {};
      S.sofInputs = { assumed_risks: clone(Array.isArray(inp.assumed_risks) && inp.assumed_risks.length ? inp.assumed_risks : [{ label: null, source: 'control_pool', per_1000: null }]),
        footnotes: clone(Array.isArray(inp.footnotes) ? inp.footnotes : []) };
      sofBody(ctx, host);
    }, function (err) {
      if (S !== mine || err.code === 'ABORTED') { return; }
      MA.dom.mount(host, MA.ui.errorBox(err));
    });
  }

  // ---------------------------------------------------------------- képernyő
  function header(ctx, outcome) {
    var list = outcomes();
    var sel = h('select', { id: 'gr-outcome', 'aria-label': t('grade.outcome'), onchange: function () { MA.app.navigate('grade', { outcome: sel.value }); } },
      list.map(function (o) { return h('option', { value: o.id, selected: o.id === outcome.id }, o.id + ' — ' + pick(o.name, o.id)); }));
    var run = S.view.run;
    var badge = run && MA.analysis && MA.analysis.runBadge ? MA.analysis.runBadge(run) : null;
    return h('div', { 'class': 'gr-head' },
      h('div', { 'class': 'pf-field gr-outsel' }, h('label', { htmlFor: 'gr-outcome' }, t('grade.outcome')), sel),
      h('p', { 'class': 'gr-meta' },
        MA.ui.badge(outcome.critical ? 'warning' : 'neutral', t(outcome.critical ? 'grade.critical' : 'grade.important'), { symbol: outcome.critical ? '!' : '•' }), ' ',
        run ? [t('grade.run', { run: run.run_id }), ' ', badge, ' · ', t('grade.k', { k: run.k === null || run.k === undefined ? '—' : String(run.k) }), ' · ',
          h('span', { 'class': 'num', id: 'gr-effect' }, (run.measure ? run.measure + ' ' : '') + pick(run.display_text)), ' · ',
          t('grade.i2'), ' ', h('span', { 'class': 'num' }, pick(run.i2_text))]
          : h('span', { id: 'gr-norun' }, t('grade.noRun')), ' ',
        h('a', { href: MA.app.href('analysis', { outcome: outcome.id }), 'class': 'btn-link' }, t('grade.toAnalysis'))),
      S.view.run_matches === false ? h('p', { 'class': 'gr-warnline', id: 'gr-oldrun' }, MA.ui.badge('stale', 'X007'), ' ', t('grade.oldRun')) : null,
      run && run.stale === true ? h('p', { 'class': 'gr-warnline', id: 'gr-stale' }, MA.ui.badge('stale', 'X001'), ' ', t('grade.stale')) : null);
  }

  function gradeForm(ctx) {
    var v = S.view;
    var startName = 'gr-start';
    var startRadios = (v.vocab.starts || ['high', 'low']).map(function (s) {
      return h('label', { 'class': 'gr-step-opt', htmlFor: startName + '-' + s },
        h('input', { type: 'radio', name: startName, id: startName + '-' + s, checked: S.doc.start === s, onchange: function () { S.doc.start = s; touch(); } }),
        ' ', t('grade.start.' + s));
    });
    var reason = h('input', { type: 'text', id: 'gr-start-reason', value: S.doc.start_reason || '', 'aria-label': t('grade.start.reason'),
      oninput: function () { S.doc.start_reason = reason.value; touch(); } });
    var mid = h('input', { type: 'text', id: 'gr-mid', value: S.doc.mid_text || '', 'aria-describedby': 'gr-mid-help',
      oninput: function () { S.doc.mid_text = mid.value; touch(); } });
    var human = h('div', { id: 'gr-human-cert', 'class': 'pf-field', hidden: true },
      h('label', { htmlFor: 'gr-human-cert-sel' }, t('grade.cert.human')),
      h('select', { id: 'gr-human-cert-sel', 'aria-describedby': 'gr-human-cert-help' }, [h('option', { value: '' }, t('grade.choose'))].concat(
        (v.vocab.certainties || []).map(function (c) { return h('option', { value: c }, certLabel(c)); }))),
      h('span', { 'class': 'pf-hint muted', id: 'gr-human-cert-help' }, t('grade.cert.humanHelp')));
    var j = v.journal;
    return h('div', { 'class': 'gr-form' },
      h('div', { id: 'gr-x019', 'class': 'gr-x019', role: 'status', hidden: true },
        h('p', null, MA.ui.badge('blocker', 'X019'), ' ', h('strong', null, t('grade.x019.title')), ' ', t('grade.x019.body'), ' ', MA.why.kbButton('X019'))),
      h('fieldset', { 'class': 'gr-start' },
        h('legend', null, t('grade.start.legend')),
        h('div', { 'class': 'gr-steps', role: 'radiogroup', 'aria-label': t('grade.start.legend') }, startRadios),
        h('div', { 'class': 'pf-field' }, h('label', { htmlFor: 'gr-start-reason' }, t('grade.start.reason')), reason),
        h('p', { 'class': 'muted', id: 'gr-start-adv' })),
      h('div', { id: 'gr-adv-note' }),
      h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table gr-table', id: 'gr-domains' },
        h('caption', null, t('grade.domains.caption')),
        h('thead', null, h('tr', null, ['domain', 'rating', 'step', 'rationale', 'advice'].map(function (c) { return h('th', { scope: 'col' }, t('grade.col.' + c)); }))),
        h('tbody', null, v.vocab.domains.map(domainRow)))),
      h('div', { 'class': 'pf-field gr-midfield' },
        h('label', { htmlFor: 'gr-mid' }, t('grade.mid.label')), mid,
        h('button', { type: 'button', 'class': 'btn btn-sm', id: 'gr-mid-refresh', onclick: function () { loadAdvice(ctx); } }, t('grade.mid.refresh')),
        h('span', { 'class': 'pf-hint muted', id: 'gr-mid-help' }, t('grade.mid.help'))),
      h('div', { 'class': 'table-wrap' }, h('table', { 'class': 'table gr-table', id: 'gr-upgrades' },
        h('caption', null, t('grade.upgrades.caption')),
        h('thead', null, h('tr', null, ['upgrade', 'step', 'rationale', 'advice'].map(function (c) { return h('th', { scope: 'col' }, t('grade.col.' + c)); }))),
        h('tbody', null, v.vocab.upgrades.map(upgradeRow)))),
      h('section', { 'class': 'gr-certbox', 'aria-labelledby': 'gr-cert-h' },
        h('h3', { 'class': 'gr-h3', id: 'gr-cert-h' }, t('grade.cert.title')),
        h('p', { id: 'gr-cert', role: 'status' }),
        v.grade && v.grade.consistency_warning ? h('p', { 'class': 'gr-warnline', id: 'gr-consistency' }, MA.ui.badge('warning', t('grade.consistency')), ' ', pick(v.grade.consistency_warning)) : null,
        v.grade && Array.isArray(v.grade.override_warnings) && v.grade.override_warnings.length ? h('ul', { 'class': 'gr-warnlist', id: 'gr-override' },
          v.grade.override_warnings.map(function (w) { return h('li', null, MA.ui.badge('warning', null, { srLabel: t('grade.advice.flag') }), ' ', pick(w && w.text ? w.text : w)); })) : null,
        v.grade && v.grade.validator_rollup ? h('p', { 'class': 'muted', id: 'gr-validator' }, t('grade.validator'), ' ', pick(v.grade.validator_rollup.text || v.grade.validator_rollup.status || v.grade.validator_rollup)) : null,
        human,
        h('p', { 'class': 'muted', id: 'gr-journal' }, j ? t('grade.journal', { id: String(j.id), cert: certLabel(j.certainty), ts: MA.i18n.ts(j.ts) }) : t('grade.journalNone')),
        h('div', { 'class': 'toolbar' },
          h('button', { type: 'button', 'class': 'btn', id: 'gr-save', onclick: function () { save(ctx, false); } }, t('grade.save')),
          h('button', { type: 'button', 'class': 'btn btn-primary', id: 'gr-record', onclick: function () { save(ctx, true); } }, t('grade.record')),
          h('span', { 'class': 'muted', id: 'gr-dirty', role: 'status' })),
        h('p', { 'class': 'muted gr-recwhy', id: 'gr-record-why' }),
        h('div', { id: 'gr-msg' })));
  }

  function render(root, ctx) {
    var outcome = pickOutcome(ctx.params.outcome);
    if (!outcome) {
      MA.dom.mount(root, MA.analysis && MA.analysis.noOutcomesPanel ? MA.analysis.noOutcomesPanel(function (id) { MA.app.navigate('grade', { outcome: id }); })
        : MA.ui.emptyState('grade.noOutcomes'));
      return null;
    }
    if (ctx.params.outcome !== outcome.id) { ctx.setParams({ outcome: outcome.id }); }
    ctx.setTitle(pick(outcome.name, outcome.id));
    root.appendChild(MA.ui.spinner());
    return MA.api.get('/api/grade/' + enc(outcome.id), { signal: ctx.signal, toast: false }).then(function (env) {
      return { view: env.data, etag: env.etag, err: null };
    }, function (err) {
      if (err.code === 'ABORTED') { throw err; }
      if (err.code === 'CAPABILITY_MISSING' && err.details && err.details.view) { return { view: err.details.view, etag: null, err: err }; }
      throw err;
    }).then(function (res) {
      if (!ctx.alive()) { return; }
      S = { oid: outcome.id, view: res.view, etag: res.etag, engineErr: res.err, dirty: false, advice: null, adviceErr: null, sof: null, sofEtag: null, sofInputs: null, sofDirty: false };
      S.doc = workingCopy(S.view);
      var mine = S;
      ctx.onCleanup(function () { if (S === mine) { S = null; } });
      var sofHost = h('div', { id: 'sof-body', 'class': 'section-body' });
      var intro = h('p', { 'class': 'gr-intro' }, t('grade.intro'), ' ', MA.why.button({ title: t('grade.why.title'), plain: { asks: t('grade.why.asks'), because: t('grade.why.because'), change: t('grade.why.change') }, kb: ['D-S13-001'] }, { id: 'gr-why-main' }));
      if (!S.view.run) {
        // commit-futás nélkül nincs mire GRADE-et adni: csak a teendő (Elemzés → Rögzítés)
        MA.dom.mount(root, h('section', { 'class': 'panel', id: 'grade-panel', 'aria-labelledby': 'gr-h' },
          h('h2', { 'class': 'panel-title', id: 'gr-h' }, t('grade.title', { name: pick(outcome.name, outcome.id) })),
          header(ctx, outcome), intro));
        return;
      }
      MA.dom.mount(root,
        h('section', { 'class': 'panel', id: 'grade-panel', 'aria-labelledby': 'gr-h' },
          h('h2', { 'class': 'panel-title', id: 'gr-h' }, t('grade.title', { name: pick(outcome.name, outcome.id) })),
          header(ctx, outcome),
          intro,
          S.engineErr ? h('div', { 'class': 'gr-engine', id: 'gr-engine-missing' }, MA.ui.errorBox(S.engineErr)) : null,
          gradeForm(ctx)),
        h('section', { 'class': 'panel', id: 'sof-panel', 'aria-labelledby': 'sof-h' },
          h('h2', { 'class': 'panel-title', id: 'sof-h' }, t('sof.title'), ' ', MA.why.kbButton('D-S13-011'), ' ', MA.why.kbButton('D-S13-012')),
          sofHost));
      refreshStatus();
      Object.keys(S.doc.domains).forEach(stepCell);
      loadAdvice(ctx);
      loadSof(ctx, sofHost);
    }, function (err) {
      if (ctx.alive() && err.code !== 'ABORTED') { MA.dom.mount(root, MA.ui.errorBox(err)); }
    });
  }

  function onLeave(ctx, info) {
    if (!S || !(S.dirty || S.sofDirty) || info.reason !== 'navigate') { return true; }
    return MA.ui.confirm({ title: t('grade.leaveTitle'), message: t('grade.leaveBody'), okLabel: t('grade.leave'), danger: true }).then(function (ok) {
      if (ok && S) { S.dirty = false; S.sofDirty = false; }
      return ok;
    });
  }

  MA.app.registerScreen({ id: 'grade', title_key: 'tab.grade', workspace: 'appraisal', tab: 'grade', order: 10, render: render, onLeave: onLeave });

  MA.selftest.register('grade: a lépés-címkék szótári szövegek; a domén-szókincs a szerverről jön', function (tt) {
    tt.eq(key('very serious'), 'very_serious', 'a szótárkulcs');
    tt.ok(STEP_KEY['-1'] && STEP_KEY['0'] && STEP_KEY['2'], 'lépés-címkék');
    tt.eq(sofCell({ assumed_risk: { label: { hu: 'A', en: 'A' }, text: { hu: '9 / 1000', en: '9 / 1000' } } }, 'assumed_risk'), 'A: 9 / 1000', 'SoF-cella: a motor szövege');
  });
})();
