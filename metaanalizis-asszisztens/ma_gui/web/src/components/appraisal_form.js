/* components/appraisal_form.js — közös értékelő-űrlap (terv 3.5.10–3.5.13, 4.11, 6.5; 11. fejezet 4–6. döntés).
 *
 * Az eszköz-definíció (szk.instrument/v1), az értékelés (szk.appraisal/v1) és a motor ellenőrzése
 * (szk.appraisal-result/v1) alapján épít űrlapot: doménenként összecsukható szakaszok jelző-kérdésekkel, válasz-
 * választóval (natív rádiógombok: nyilakkal bejárható), bizonyítékkal (idézet, oldal, hely), „Miért?” magyarázattal
 * (a tétel útmutatója + az AI-vázlat egyszerű nyelvű indoklása), doménítélettel, a motor implikált ítéletével
 * (AZ ALGORITMUS CÍMKÉJÉVEL — soha nem „hivatalos eredményként”) és felülbírálásnál kötelező indoklással (X017).
 * A felület nem számol: a teljesség, az implikált ítélet és minden szám a motoré (check); itt csak címkék
 * összevetése és megjelenítés történik.
 *
 * MA.appr:
 *   instrument(tool) → Promise<inst> · instruments() → Promise<data> (GET /api/instruments, gyorsítótárral)
 *   family(inst), toolName(inst), answers(inst), verdicts(inst), level(inst, v), verdictBadge(inst, v, {implied})
 *   algText(alg) → szöveg · scopes(inst) · itemsFor(inst, scope, pass) · rater() / setRater(v) / raterField(onChange)
 *   ans(doc, key) · setAns(doc, key, patch) · judg(doc, coll, domain, pass) · setJudg(doc, coll, domain, pass, patch)
 *   form(o) → {el, update(check), focusKey(key)} — o: {inst, doc, check, scope, passes?, judgements?, applicability?,
 *            overall?, editable?, filter?(item), onChange(kind)}
 *   aiBanner(doc, onApprove) · completeness(check) · missingList(check, onGo) · download(name, obj) · pickJson(cb)
 *   apiPath(unit, tool, suffix?) · query(target, rater)
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;
  var t = function (k, a) { return MA.i18n.t(k, a); };
  var pick = function (v, fb) { return MA.i18n.pick(v, fb === undefined ? '' : fb); };

  var FAMILY = { rob2: 'rob', 'robins-i': 'rob', 'robins-e': 'rob', quadas2: 'rob', nos: 'rob', quips: 'rob', jbi: 'rob',
    'probast-ai': 'prediction', 'tripod-ai': 'reporting', amstar2: 'review', robis: 'review', grade: 'certainty' };
  var LEVELS = { low: 'low', some_concerns: 'some', moderate: 'some', unclear: 'some', high: 'high', serious: 'high',
    critical: 'high', very_high: 'high', no_information: 'ni' };
  var SYM = { low: '●', some: '◐', high: '○', critical: '⊗', ni: '?', none: '=' };
  // ezek a családok a saját képernyőjükön; a többi (rob, quality …) a RoB-család képernyőjén
  var OWN_SCREEN = { prediction: 1, reporting: 1, review: 1, certainty: 1 };
  var ALGS = { published: 1, conservative: 1, count: 1, none: 1, 'validator-compatible': 1 };
  var RATER_RE = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;

  var instCache = {};
  var listCache = null;

  // ---------------------------------------------------------------- eszközök
  function instrument(tool) {
    if (!instCache[tool]) {
      instCache[tool] = MA.api.get('/api/instruments/' + encodeURIComponent(tool), { toast: false }).then(function (env) {
        return env.data;
      }, function (err) { delete instCache[tool]; throw err; });
    }
    return instCache[tool];
  }

  function instruments() {
    if (!listCache) {
      listCache = MA.api.get('/api/instruments', { toast: false }).then(function (env) { return env.data; },
        function (err) { listCache = null; throw err; });
    }
    return listCache;
  }

  function family(inst) { return (inst && (inst.family || FAMILY[inst.key])) || 'rob'; }
  function toolName(inst) { return inst ? (pick(inst.label) || pick(inst.name_i18n) || pick(inst.name) || inst.key) : ''; }
  function isRobFamily(inst) { return !OWN_SCREEN[family(inst)]; }
  function domainShort(d) { return d.short ? String(d.short) : 'D' + String(d.id); }
  function scopeLabel(inst, s) {
    var hit = ((inst && inst.scopes) || []).filter(function (x) { return x && typeof x === 'object' && x.id === s; })[0];
    if (hit && hit.label) { return pick(hit.label); }
    return MA.i18n.has('appraisal.scopes.' + s) ? t('appraisal.scopes.' + s) : String(s);
  }
  /** az algoritmus címkéje: a motor saját szövege (rollup.label), ennek hiányában a felület általános címkéje */
  function algLabel(inst, alg) {
    var r = inst && inst.rollup;
    if (r && r.algorithm === alg && r.label) { return pick(r.label); }
    return algText(alg);
  }

  function answers(inst) {
    var list = (inst && inst.answers) || [];
    if (!list.length && inst && inst.status_vocab) { list = inst.status_vocab; }
    return list.map(function (a) {
      if (typeof a === 'string') { return { value: a, label: a, text: a }; }
      return { value: a.value, label: a.label || a.value, text: pick(a.text) || a.label || a.value };
    });
  }

  function verdicts(inst) {
    return ((inst && inst.verdicts) || []).map(function (v) {
      if (typeof v === 'string') { return { value: v, label: v, level: LEVELS[v] || 'ni' }; }
      return { value: v.value, label: pick(v.label) || v.value, level: v.level || LEVELS[v.value] || 'ni' };
    });
  }

  function level(inst, v) {
    if (v === null || v === undefined || v === '') { return null; }
    var hit = verdicts(inst).filter(function (x) { return x.value === v; })[0];
    return hit ? hit.level : (LEVELS[v] || 'ni');
  }

  function verdictLabel(inst, v) {
    var hit = verdicts(inst).filter(function (x) { return x.value === v; })[0];
    return hit ? hit.label : String(v);
  }

  /** ítélet-jelvény: szimbólum ÉS szöveg (a szín sosem egyedüli jelölés); null → „=” (üres) */
  function verdictBadge(inst, v, opts) {
    opts = opts || {};
    var lv = level(inst, v);
    var label = lv ? verdictLabel(inst, v) : t('appraisal.empty');
    return h('span', { 'class': ['ap-v', 'ap-v-' + (lv || 'none'), opts.implied && 'is-implied'], dataset: { v: v || '' },
      title: opts.title || null },
    h('span', { 'class': 'ap-v-sym', 'aria-hidden': 'true' }, SYM[lv || 'none']), ' ',
    h('span', { 'class': 'ap-v-text' }, label));
  }

  function algText(alg) { return ALGS[alg] ? t('appraisal.alg.' + alg) : t('appraisal.alg.unknown', { alg: String(alg || '—') }); }

  function scopes(inst) {
    var sc = inst && inst.scopes;
    if (Array.isArray(sc) && sc.length) {
      return sc.map(function (s) { return typeof s === 'string' ? s : s.id; }).filter(function (s) { return !!s && s !== 'all'; });
    }
    if (inst && inst.passes && inst.passes.length) {
      return ['both'].concat(inst.passes.map(function (p) { return p.id; }));
    }
    return [];
  }

  function itemsFor(inst, scope, pass) {
    var hasPasses = !!(inst.passes && inst.passes.length);
    return (inst.items || []).filter(function (it) {
      if (hasPasses) {
        if (pass && it.pass !== pass) { return false; }
        return !(scope === 'development' || scope === 'evaluation') || it.pass === scope;
      }
      if (it.applies_to) {
        if (scope === 'development') { return String(it.applies_to).indexOf('D') >= 0; }
        if (scope === 'evaluation') { return String(it.applies_to).indexOf('E') >= 0; }
        return true;
      }
      var sc = Array.isArray(it.scope) ? it.scope : ['all'];
      return !scope || sc.indexOf('all') >= 0 || sc.indexOf(scope) >= 0;
    });
  }

  function keyOf(it) { return it.key || (it.pass ? it.pass + '/' + it.id : it.id); }

  // ---------------------------------------------------------------- értékelő (csak UI-preferencia: monogram)
  function rater() { var v = MA.prefs.get('appraisal.rater', ''); return RATER_RE.test(v || '') ? v : ''; }
  function setRater(v) { MA.prefs.set('appraisal.rater', RATER_RE.test(v || '') ? v : null); }

  function raterField(onChange) {
    var id = MA.dom.uid('ap-rater');
    var hint = MA.dom.uid('ap-rater-h');
    var inp = h('input', { id: id, type: 'text', 'class': 'ap-rater-input', value: rater(), maxlength: '32', size: '6',
      autocomplete: 'off', spellcheck: 'false', 'aria-describedby': hint, placeholder: 'SzK' });
    function commit() {
      var v = inp.value.trim();
      var ok = v === '' || RATER_RE.test(v);
      inp.setAttribute('aria-invalid', ok ? 'false' : 'true');
      if (!ok) { return; }
      if (v !== rater()) { setRater(v); if (onChange) { onChange(v); } }
    }
    inp.addEventListener('change', commit);
    inp.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); commit(); } });
    return h('span', { 'class': 'ap-rater' }, h('label', { htmlFor: id, i18n: 'appraisal.rater' }), ' ', inp,
      h('span', { id: hint, 'class': 'sr-only', i18n: 'appraisal.raterHint' }));
  }

  // ---------------------------------------------------------------- dokumentum-segédek
  function ans(doc, key) { var a = (doc.answers || {})[key]; return a && typeof a === 'object' ? a : null; }

  function emptyEv(ev) { return !ev || (!ev.text && (ev.page === null || ev.page === undefined) && !ev.locator); }

  function setAns(doc, key, patch) {
    doc.answers = doc.answers || {};
    var cur = Object.assign({}, ans(doc, key) || {});
    Object.keys(patch).forEach(function (k) { cur[k] = patch[k]; });
    if (cur.evidence && emptyEv(cur.evidence)) { delete cur.evidence; }
    var noValue = cur.value === null || cur.value === undefined || cur.value === '';
    if (noValue && !cur.evidence && !cur.comment && !cur.rationale) { delete doc.answers[key]; return; }
    if (noValue) { cur.value = null; }
    doc.answers[key] = cur;
  }

  function samePass(a, b) { return (a || null) === (b || null); }

  function judg(doc, coll, domain, pass) {
    return (doc[coll] || []).filter(function (d) { return d && String(d.domain) === String(domain) && samePass(d.pass, pass); })[0] || null;
  }

  function setJudg(doc, coll, domain, pass, patch) {
    doc[coll] = (doc[coll] || []).slice();
    var cur = judg(doc, coll, domain, pass);
    var next = Object.assign({ domain: String(domain), pass: pass || null, judgement: null, rationale: null, override_reason: null,
      decision_id: null }, cur || {}, patch);
    if (coll === 'applicability') { delete next.pass; delete next.override_reason; delete next.decision_id; }
    var i = doc[coll].indexOf(cur);
    if (i >= 0) { doc[coll][i] = next; } else { doc[coll].push(next); }
    return next;
  }

  function apiPath(unit, tool, suffix) {
    return '/api/appraisals/' + encodeURIComponent(unit) + '/' + encodeURIComponent(tool) + (suffix ? '/' + suffix : '');
  }

  function query(target, r) {
    var q = {};
    if (target) { q.target = target; }
    if (r) { q.rater = r; }
    return q;
  }

  // ---------------------------------------------------------------- AI-vázlat (6. döntés)
  function aiBadge() {
    return MA.ui.badge('estimated', t('appraisal.ai.badge'), { symbol: '✦', title: t('appraisal.ai.title') });
  }

  function aiBanner(doc, onApprove) {
    if (!doc || doc.origin !== 'ai_draft') { return null; }
    var approved = !!doc.approved_by;
    return h('div', { 'class': ['ap-ai-banner', approved && 'is-approved'], role: 'note', id: 'ap-ai-banner' },
      aiBadge(), ' ',
      h('span', null, approved ? t('appraisal.ai.approved', { who: doc.approved_by, at: MA.i18n.ts(doc.approved_at || '') })
        : t('appraisal.ai.pending')),
      h('p', { 'class': 'muted ap-ai-note', i18n: 'appraisal.ai.note' }),
      !approved && onApprove ? h('button', { type: 'button', 'class': 'btn btn-sm btn-primary', id: 'ap-approve', onclick: onApprove },
        t('appraisal.ai.approve')) : null);
  }

  // ---------------------------------------------------------------- teljesség
  function completeness(check, pass) {
    if (!check) { return '—'; }
    if (pass && check.per_pass && check.per_pass[pass]) {
      var pp = check.per_pass[pass];
      return pp.text || (String(pp.answered) + '/' + String(pp.expected));
    }
    if (check.completeness_text) { return pick(check.completeness_text, '—'); }
    if (typeof check.answered === 'number' && typeof check.expected === 'number') {
      return String(check.answered) + '/' + String(check.expected);
    }
    return '—';
  }

  function missingList(check, onGo) {
    var miss = (check && check.missing) || [];
    if (!miss.length) { return h('span', { 'class': 'ap-missing is-none', i18n: 'appraisal.noMissing' }); }
    return h('span', { 'class': 'ap-missing', id: 'ap-missing' }, h('span', { i18n: 'appraisal.missing' }), ' ',
      miss.slice(0, 12).map(function (m) {
        var key = m.key || (m.pass ? m.pass + '/' + m.item : m.item);
        var label = m.pass ? m.item + ' (' + t('appraisal.pass.' + m.pass) + ')' : String(m.item);
        return h('button', { type: 'button', 'class': 'btn-link ap-missing-go', dataset: { key: key }, onclick: function () { onGo(key); } }, label);
      }), miss.length > 12 ? h('span', { 'class': 'muted' }, ' ' + t('appraisal.moreMissing', { n: String(miss.length - 12) })) : null);
  }

  // ---------------------------------------------------------------- fájlcsere (5. döntés)
  function download(name, obj) {
    var blob = new Blob([JSON.stringify(obj, null, 2) + '\n'], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = h('a', { href: url, download: name, 'class': 'sr-only' }, name);
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); if (a.parentNode) { a.parentNode.removeChild(a); } }, 1000);
  }

  function pickJson(cb) {
    var inp = h('input', { type: 'file', accept: '.json,application/json', 'class': 'sr-only' });
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0];
      if (inp.parentNode) { inp.parentNode.removeChild(inp); }
      if (!f) { return; }
      var rd = new FileReader();
      rd.onload = function () {
        var obj = null;
        try { obj = JSON.parse(String(rd.result || '')); } catch (e) { obj = null; }
        if (!obj || typeof obj !== 'object') {
          MA.ui.toast({ kind: 'error', title: t('appraisal.import.badFile'), message: f.name });
          return;
        }
        cb(obj, f.name);
      };
      rd.readAsText(f);
    });
    document.body.appendChild(inp);
    inp.click();
  }

  // ---------------------------------------------------------------- űrlap
  function tagBadges(it) {
    var out = [];
    (it.tags || []).forEach(function (tg) {
      if (tg === 'router' && it.polarity !== 'router') { out.push(MA.ui.badge('info', t('appraisal.tag.router'), { symbol: '↪', title: t('appraisal.tag.routerTitle') })); }
      if (tg === 'reverse' && it.polarity !== 'reverse') { out.push(MA.ui.badge('warning', t('appraisal.tag.reverse'), { symbol: '⇄', title: t('appraisal.tag.reverseTitle') })); }
    });
    if (it.polarity === 'router') { out.push(MA.ui.badge('info', t('appraisal.tag.router'), { symbol: '↪', title: t('appraisal.tag.routerTitle') })); }
    if (it.polarity === 'reverse') { out.push(MA.ui.badge('warning', t('appraisal.tag.reverse'), { symbol: '⇄', title: t('appraisal.tag.reverseTitle') })); }
    if (it.critical) { out.push(MA.ui.badge('blocker', t('appraisal.tag.critical'), { symbol: 'K', title: t('appraisal.tag.criticalTitle') })); }
    if (it.applies_to) { out.push(MA.ui.badge('neutral', String(it.applies_to), { symbol: null, title: t('appraisal.tag.appliesTitle') })); }
    if (it.stars) { out.push(MA.ui.badge('neutral', t('appraisal.tag.stars', { n: String(it.stars) }), { symbol: '★' })); }
    return out;
  }

  function whySpec(it, a) {
    var ev = a && a.evidence ? { quote: a.evidence.text, page: a.evidence.page, locator: a.evidence.locator } : null;
    var rat = a && a.rationale;
    return { title: it.id + ' — ' + pick(it.text), detail: it.help || it.guidance || null,
      plain: rat && typeof rat === 'object' ? rat : (rat ? { because: rat } : null), evidence: ev };
  }

  function form(o) {
    var inst = o.inst, doc = o.doc;
    var editable = o.editable !== false;
    var passes = o.passes && o.passes.length ? o.passes : [null];
    var refs = { rows: {}, judg: {}, overall: null, whys: {} };
    var check = o.check || null;
    var root = h('div', { 'class': 'ap-form', role: 'group', 'aria-label': toolName(inst) });

    function changed(kind) { if (o.onChange) { o.onChange(kind); } }

    function slot(it, passLabel) {
      var key = keyOf(it);
      var a = ans(doc, key) || {};
      var name = MA.dom.uid('ap-r');
      var legendId = MA.dom.uid('ap-lg');
      var allowed = Array.isArray(it.answers) && it.answers.length ? it.answers : null;
      var radios = answers(inst).filter(function (opt) { return !allowed || allowed.indexOf(opt.value) >= 0; }).map(function (opt) {
        var id = MA.dom.uid('ap-o');
        return h('span', { 'class': 'ap-opt' },
          h('input', { type: 'radio', id: id, name: name, value: opt.value, checked: a.value === opt.value, disabled: !editable,
            dataset: { key: key }, onchange: function () { setAns(doc, key, { value: opt.value }); markAnswered(key); changed('answer'); } }),
          h('label', { htmlFor: id, title: opt.text }, opt.label, h('span', { 'class': 'sr-only' }, ' — ' + opt.text)));
      });
      var ev = a.evidence || {};
      var qId = MA.dom.uid('ap-q'), pId = MA.dom.uid('ap-p'), lId = MA.dom.uid('ap-l');
      var page = h('input', { id: pId, type: 'text', inputmode: 'numeric', 'class': 'ap-page', size: '4', maxlength: '6', disabled: !editable,
        value: ev.page === null || ev.page === undefined ? '' : String(ev.page) });
      function evPatch() {
        var pv = page.value.trim();
        var okPage = pv === '' || /^[0-9]{1,6}$/.test(pv);
        page.setAttribute('aria-invalid', okPage ? 'false' : 'true');
        var cur = (ans(doc, key) || {}).evidence || {};
        setAns(doc, key, { evidence: { text: quote.value.trim() || null, page: okPage && pv !== '' ? parseInt(pv, 10) : (okPage ? null : cur.page || null),
          locator: loc.value.trim() || null, doc: cur.doc || null } });
        evSum.classList.toggle('has-ev', !emptyEv((ans(doc, key) || {}).evidence));
        changed('evidence');
      }
      var quote = h('textarea', { id: qId, rows: '2', maxlength: '1000', 'class': 'ap-quote', disabled: !editable, value: ev.text || '' });
      var loc = h('input', { id: lId, type: 'text', maxlength: '200', size: '14', 'class': 'ap-loc', disabled: !editable, value: ev.locator || '' });
      [quote, page, loc].forEach(function (el) { el.addEventListener('change', evPatch); });
      var evSum = h('summary', { 'class': ['ap-ev-sum', !emptyEv(ev) && 'has-ev'] }, h('span', { i18n: 'appraisal.evidence' }));
      var clear = editable ? h('button', { type: 'button', 'class': 'btn-icon ap-clear', 'aria-label': t('appraisal.clearAnswer', { id: it.id }),
        title: t('appraisal.clearAnswer', { id: it.id }), onclick: function () {
          setAns(doc, key, { value: null });
          MA.dom.$$('input[name="' + name + '"]', root).forEach(function (r) { r.checked = false; });
          changed('answer');
        } }, '×') : null;
      return h('fieldset', { 'class': 'ap-slot', dataset: { key: key } },
        h('legend', { id: legendId, 'class': passLabel ? 'ap-slot-label' : 'sr-only' }, passLabel || it.id),
        h('span', { 'class': 'ap-opts', role: 'radiogroup', 'aria-labelledby': legendId }, radios), clear,
        a.rationale ? h('span', { 'class': 'ap-ai-mark', title: t('appraisal.ai.itemTitle') }, aiBadge()) : null,
        h('details', { 'class': 'ap-ev' }, evSum,
          h('div', { 'class': 'ap-ev-body' },
            h('label', { htmlFor: qId, i18n: 'appraisal.quote' }), quote,
            h('span', { 'class': 'row' }, h('label', { htmlFor: pId, i18n: 'appraisal.page' }), page,
              h('label', { htmlFor: lId, i18n: 'appraisal.locator' }), loc))));
    }

    function markAnswered(key) {
      var r = refs.rows[key];
      if (r) { r.classList.remove('is-missing'); }
    }

    function itemRow(group) {
      var it0 = group.items.filter(function (x) { return !!x; })[0];
      var row = h('div', { 'class': 'ap-item', dataset: { id: it0.id } });
      var whyHost = h('span', { 'class': 'ap-why' });
      function refreshWhy() {
        var a = null;
        group.items.forEach(function (x) { if (x && !a) { var aa = ans(doc, keyOf(x)); if (aa && (aa.rationale || aa.evidence)) { a = aa; } } });
        MA.dom.mount(whyHost, MA.why.button(whySpec(it0, a), { compact: false, label: t('appraisal.why') }));
      }
      refreshWhy();
      refs.whys[it0.id] = refreshWhy;
      row.appendChild(h('div', { 'class': 'ap-item-head' },
        h('span', { 'class': 'ap-item-id' }, it0.id), ' ',
        h('span', { 'class': 'ap-item-text' }, pick(it0.text)), ' ', tagBadges(it0), whyHost));
      row.appendChild(h('div', { 'class': ['ap-slots', group.items.length > 1 && 'is-multi'] }, group.items.map(function (it, i) {
        if (!it) { return h('div', { 'class': 'ap-slot is-na' }, h('span', { 'class': 'muted' }, t('appraisal.notInPass'))); }
        var lbl = group.items.length > 1 ? t('appraisal.pass.' + passes[i]) : null;
        var s = slot(it, lbl);
        refs.rows[keyOf(it)] = s;
        return s;
      })));
      return row;
    }

    function judgBlock(domain, pass, coll) {
      var cur = judg(doc, coll, domain.id, pass) || {};
      var key = coll + '|' + domain.id + '|' + (pass || '');
      var selId = MA.dom.uid('ap-j'), ratId = MA.dom.uid('ap-jr'), ovId = MA.dom.uid('ap-jo'), msgId = MA.dom.uid('ap-jm');
      var sel = h('select', { id: selId, 'class': 'ap-judg-select', disabled: !editable, dataset: { domain: domain.id, pass: pass || '', coll: coll } },
        h('option', { value: '' }, t('appraisal.noJudgement')),
        verdicts(inst).map(function (v) { return h('option', { value: v.value, selected: cur.judgement === v.value }, v.label); }));
      var rat = h('textarea', { id: ratId, rows: '1', maxlength: '4000', 'class': 'ap-rationale', disabled: !editable, value: cur.rationale || '' });
      var ov = h('textarea', { id: ovId, rows: '2', maxlength: '4000', 'class': 'ap-override-text', disabled: !editable, value: cur.override_reason || '',
        'aria-describedby': msgId });
      var ovMsg = h('p', { id: msgId, 'class': 'ap-override-msg' });
      var ovBox = h('div', { 'class': 'ap-override', hidden: true }, h('label', { htmlFor: ovId, i18n: 'appraisal.override' }), ov, ovMsg);
      var implied = coll === 'domain_judgements' ? h('div', { 'class': 'ap-implied', 'aria-live': 'polite' }) : null;
      var ref = { sel: sel, ov: ov, ovBox: ovBox, ovMsg: ovMsg, implied: implied, impliedValue: null, alg: null, reasonMissing: false };
      refs.judg[key] = ref;
      sel.addEventListener('change', function () {
        setJudg(doc, coll, domain.id, pass, { judgement: sel.value || null });
        syncOverride(ref);
        changed('judgement');
      });
      rat.addEventListener('change', function () { setJudg(doc, coll, domain.id, pass, { rationale: rat.value.trim() || null }); changed('rationale'); });
      ov.addEventListener('input', function () { syncOverride(ref); });
      ov.addEventListener('change', function () { setJudg(doc, coll, domain.id, pass, { override_reason: ov.value.trim() || null }); syncOverride(ref); changed('override'); });
      var title = coll === 'applicability' ? t('appraisal.applicability') : (pass ? t('appraisal.judgementPass', { pass: t('appraisal.pass.' + pass) }) : t('appraisal.judgement'));
      return h('div', { 'class': ['ap-judg', 'ap-judg-' + coll], dataset: { domain: domain.id, pass: pass || '', coll: coll } },
        implied,
        h('div', { 'class': 'ap-judg-row' }, h('label', { htmlFor: selId }, title), sel,
          cur.decision_id ? MA.ui.badge('info', t('appraisal.decision', { id: String(cur.decision_id) }), { symbol: '✎' }) : null),
        h('div', { 'class': 'ap-judg-row' }, h('label', { htmlFor: ratId, i18n: 'appraisal.rationale' }), rat),
        coll === 'domain_judgements' ? ovBox : null);
    }

    function syncOverride(ref) {
      var v = ref.sel.value || null;
      var differs = !!(v && ref.impliedValue && v !== ref.impliedValue && ref.alg !== 'none');
      var text = ref.ov.value.trim();
      ref.ovBox.hidden = !(differs || text);
      ref.ov.required = differs;
      ref.ov.setAttribute('aria-required', differs ? 'true' : 'false');
      ref.ov.setAttribute('aria-invalid', differs && !text ? 'true' : 'false');
      ref.ovBox.classList.toggle('is-required', differs);
      MA.dom.mount(ref.ovMsg, differs ? (text ? t('appraisal.overrideOk') : t('appraisal.overrideNeeded', {
        implied: verdictLabel(inst, ref.impliedValue), mine: verdictLabel(inst, v) })) : '');
    }

    // doménszakaszok
    var scope = o.scope;
    (inst.domains || []).forEach(function (d) {
      var groups = [];
      var byId = {};
      passes.forEach(function (p, pi) {
        itemsFor(inst, scope, p).filter(function (it) { return String(it.domain) === String(d.id) && (!o.filter || o.filter(it)); })
          .forEach(function (it) {
            if (!byId[it.id]) { byId[it.id] = { items: passes.map(function () { return null; }) }; groups.push(byId[it.id]); }
            byId[it.id].items[pi] = it;
          });
      });
      if (!groups.length) { return; }
      var sumImplied = h('span', { 'class': 'ap-dom-implied' });
      var sec = h('details', { 'class': 'ap-domain', open: true, dataset: { domain: d.id }, id: MA.dom.uid('ap-dom') },
        h('summary', { 'class': 'ap-dom-sum' }, h('span', { 'class': 'ap-dom-title' }, /^[0-9]+$/.test(String(d.id)) ? t('appraisal.domain', { id: String(d.id) }) + ' — ' + pick(d.title) : pick(d.title) || String(d.id)), ' ', sumImplied),
        h('div', { 'class': 'ap-dom-items' }, groups.map(itemRow)));
      refs['dom|' + d.id] = sumImplied;
      if (o.judgements !== false) {
        var jp = o.passes && o.passes.length ? o.passes.filter(function (p) { return !d.passes || d.passes.indexOf(p) >= 0; }) : [null];
        sec.appendChild(h('div', { 'class': 'ap-dom-judg' }, jp.map(function (p) { return judgBlock(d, p, 'domain_judgements'); }),
          o.applicability && d.applicability ? judgBlock(d, null, 'applicability') : null));
      }
      root.appendChild(sec);
    });

    // összítélet
    if (o.overall !== false && verdicts(inst).length) {
      var ov0 = doc.overall || {};
      var oSel = MA.dom.uid('ap-os'), oRat = MA.dom.uid('ap-or'), oOv = MA.dom.uid('ap-oo'), oMsg = MA.dom.uid('ap-om');
      var sel = h('select', { id: oSel, 'class': 'ap-overall-select', disabled: !editable },
        h('option', { value: '' }, t('appraisal.noJudgement')),
        verdicts(inst).map(function (v) { return h('option', { value: v.value, selected: ov0.judgement === v.value }, v.label); }));
      var rat = h('textarea', { id: oRat, rows: '2', maxlength: '4000', 'class': 'ap-rationale', disabled: !editable, value: ov0.rationale || '' });
      var ovt = h('textarea', { id: oOv, rows: '2', maxlength: '4000', 'class': 'ap-override-text', disabled: !editable, value: ov0.override_reason || '', 'aria-describedby': oMsg });
      var ovMsg = h('p', { id: oMsg, 'class': 'ap-override-msg' });
      var box = h('div', { 'class': 'ap-override', hidden: true }, h('label', { htmlFor: oOv, i18n: 'appraisal.override' }), ovt, ovMsg);
      var implied = h('div', { 'class': 'ap-implied', 'aria-live': 'polite' });
      var oref = { sel: sel, ov: ovt, ovBox: box, ovMsg: ovMsg, implied: implied, impliedValue: null, alg: null, rat: rat };
      refs.overall = oref;
      function patchOverall(p) { doc.overall = Object.assign({ judgement: null, rationale: null, override_reason: null, decision_id: null }, doc.overall || {}, p); }
      sel.addEventListener('change', function () { patchOverall({ judgement: sel.value || null }); syncOverride(oref); syncHolistic(); changed('overall'); });
      rat.addEventListener('change', function () { patchOverall({ rationale: rat.value.trim() || null }); syncHolistic(); changed('overall'); });
      rat.addEventListener('input', syncHolistic);
      ovt.addEventListener('input', function () { syncOverride(oref); });
      ovt.addEventListener('change', function () { patchOverall({ override_reason: ovt.value.trim() || null }); syncOverride(oref); changed('override'); });
      root.appendChild(h('section', { 'class': 'ap-overall', id: 'ap-overall' },
        h('h3', { i18n: 'appraisal.overall' }), implied,
        h('div', { 'class': 'ap-judg-row' }, h('label', { htmlFor: oSel, i18n: 'appraisal.yourJudgement' }), sel,
          ov0.decision_id ? MA.ui.badge('info', t('appraisal.decision', { id: String(ov0.decision_id) }), { symbol: '✎' }) : null),
        h('div', { 'class': 'ap-judg-row' }, h('label', { htmlFor: oRat, i18n: 'appraisal.rationale' }), rat), box));
    }

    function syncHolistic() {
      var r = refs.overall;
      if (!r) { return; }
      var need = r.alg === 'none' && !!r.sel.value;
      r.rat.setAttribute('aria-required', need ? 'true' : 'false');
      r.rat.setAttribute('aria-invalid', need && !r.rat.value.trim() ? 'true' : 'false');
    }

    function impliedNode(v, alg, text, forced, isOverall, extra) {
      var algNode = h('span', { 'class': 'ap-alg', dataset: { alg: alg || '' } }, algLabel(inst, alg));
      if (alg === 'none') {
        // nincs algoritmus: doménszinten csak a motor jelzései (nem ítélet), az összítéletnél a holisztikus címke
        if (!isOverall) { return text ? [h('span', { 'class': 'ap-implied-label', i18n: 'appraisal.flags' }), ' ', h('span', { 'class': 'ap-implied-why' }, pick(text))] : null; }
        return [h('span', { 'class': 'ap-implied-label', i18n: 'appraisal.holistic' }), ' ', algNode];
      }
      if (alg === 'count') {
        if (!isOverall) { return null; }
        return [h('span', { 'class': 'ap-implied-label', i18n: 'appraisal.stars' }), ' ', h('span', { 'class': 'num ap-implied-why' }, pick(extra) || '—'), ' (', algNode, ')'];
      }
      return [h('span', { 'class': 'ap-implied-label', i18n: 'appraisal.implied' }), ' (', algNode, '): ',
        v ? verdictBadge(inst, v, { implied: true }) : h('span', { 'class': 'muted', i18n: 'appraisal.impliedNone' }),
        text ? h('span', { 'class': 'ap-implied-why' }, ' — ' + pick(text)) : null,
        forced && forced.length ? h('span', { 'class': 'ap-forced muted' }, ' ' + t('appraisal.forcedBy', { items: forced.join(', ') })) : null];
    }

    function update(c) {
      check = c || null;
      var miss = {};
      ((c && c.missing) || []).forEach(function (m) { miss[m.key || (m.pass ? m.pass + '/' + m.item : m.item)] = true; });
      Object.keys(refs.rows).forEach(function (k) { refs.rows[k].classList.toggle('is-missing', !!miss[k]); refs.rows[k].dataset.missing = miss[k] ? '1' : ''; });
      var byDom = {};
      ((c && c.domains) || []).forEach(function (d) { byDom[String(d.domain) + '|' + (d.pass || '')] = d; });
      var alg0 = c && c.overall ? c.overall.algorithm : (inst.rollup && inst.rollup.algorithm);
      Object.keys(refs.judg).forEach(function (k) {
        var r = refs.judg[k];
        var parts = k.split('|');
        if (parts[0] !== 'domain_judgements') { return; }
        var d = byDom[parts[1] + '|' + parts[2]] || null;
        r.impliedValue = d ? d.implied || null : null;
        r.alg = d ? d.algorithm || alg0 : alg0;
        if (r.implied) { MA.dom.mount(r.implied, impliedNode(r.impliedValue, r.alg, d && d.text, d && d.forced_by, false)); }
        var sum = refs['dom|' + parts[1]];
        if (sum && !parts[2]) { MA.dom.mount(sum, r.impliedValue ? verdictBadge(inst, r.impliedValue, { implied: true, title: t('appraisal.impliedShort') }) : null); }
        syncOverride(r);
      });
      ((c && c.overrides) || []).forEach(function (ov) {
        var r = ov.domain === 'overall' ? refs.overall : refs.judg['domain_judgements|' + ov.domain + '|' + (ov.pass || '')];
        if (r && ov.reason_missing) { r.ov.setAttribute('aria-invalid', r.ov.value.trim() ? 'false' : 'true'); }
      });
      if (refs.overall) {
        var co = (c && c.overall) || {};
        refs.overall.impliedValue = co.implied || null;
        refs.overall.alg = co.algorithm || alg0;
        MA.dom.mount(refs.overall.implied, impliedNode(refs.overall.impliedValue, refs.overall.alg, null, null, true, c && c.nos ? c.nos.text : null),
          co.provisional ? [' ', MA.ui.badge('warning', t('appraisal.provisional'))] : null);
        syncOverride(refs.overall);
        syncHolistic();
      }
    }

    function focusKey(key) {
      var r = refs.rows[key];
      if (!r) { return false; }
      var det = r.closest('details');
      if (det) { det.open = true; }
      var first = r.querySelector('input:checked') || r.querySelector('input[type="radio"]');
      if (first) { first.focus(); }
      if (r.scrollIntoView) { r.scrollIntoView({ block: 'center' }); }
      return true;
    }

    update(check);
    return { el: root, update: update, focusKey: focusKey, refreshWhy: function () { Object.keys(refs.whys).forEach(function (k) { refs.whys[k](); }); } };
  }

  MA.appr = {
    instrument: instrument, instruments: instruments, family: family, toolName: toolName, answers: answers, verdicts: verdicts,
    level: level, verdictLabel: verdictLabel, isRobFamily: isRobFamily, domainShort: domainShort, scopeLabel: scopeLabel, algLabel: algLabel, verdictBadge: verdictBadge, algText: algText, scopes: scopes, itemsFor: itemsFor,
    keyOf: keyOf, rater: rater, setRater: setRater, raterField: raterField, ans: ans, setAns: setAns, judg: judg, setJudg: setJudg,
    apiPath: apiPath, query: query, aiBadge: aiBadge, aiBanner: aiBanner, completeness: completeness, missingList: missingList,
    download: download, pickJson: pickJson, form: form, SYM: SYM, RATER_RE: RATER_RE,
    reset: function () { instCache = {}; listCache = null; }
  };

  MA.selftest.register('értékelő-űrlap: címkék, szimbólumok, hatókör-szűrés (nem számol)', function (tt) {
    var inst = { key: 'rob2', answers: [{ value: 'yes', label: 'Y' }, { value: 'no', label: 'N' }],
      verdicts: [{ value: 'low', label: 'Low', level: 'low' }, { value: 'high', label: 'High', level: 'high' }],
      domains: [{ id: '1', title: 'D1' }], items: [{ id: '1.1', domain: '1', key: '1.1', scope: ['all'], text: '<b>x</b>' },
        { id: '2.9', domain: '1', key: '2.9', scope: ['adherence'], text: 'y' }], rollup: { algorithm: 'conservative' } };
    tt.eq(itemsFor(inst, 'assignment').length, 1, 'a hatókörön kívüli tétel kimarad');
    tt.eq(level(inst, 'high'), 'high', 'szint a motor leírásából');
    var doc = { answers: {} };
    setAns(doc, '1.1', { value: 'yes' });
    setAns(doc, '1.1', { value: null });
    tt.ok(!doc.answers['1.1'], 'üres válasz törlődik');
    var f = form({ inst: inst, doc: { answers: {}, domain_judgements: [] }, check: { domains: [{ domain: '1', implied: 'high', algorithm: 'conservative' }] }, scope: 'assignment' });
    tt.ok(!f.el.querySelector('b') && f.el.textContent.indexOf('<b>x</b>') >= 0, 'a tételszöveg szövegként');
    tt.ok(f.el.textContent.indexOf(algText('conservative')) >= 0, 'az implikált ítélet az algoritmus címkéjével');
  });
})();
