/* components/proc.js — közös segédek a folyamat-képernyőkhöz (3.5.1, 3.5.14–3.5.17).
 *
 * MA.proc:
 *   STAGES                      ['S00' … 'S14', 'FINAL'] (projekt.STAGES + FINAL)
 *   SEVERITIES, AGENTS          a motor projekt.SEVERITIES / KNOWN_AGENTS listái
 *   sevKind(sev) → badge-fajta  error|warning|info|blocker|major|minor → MA.ui.badge kind
 *   refs(v) → ['V011', …]       KB-hivatkozások szövegből („V011 D-S07-004”, „P007,D-S04-009”) vagy listából
 *   section(titleKey, id) → {el, body}; fill(sec, nodes); load(sec, ctx, path, query, renderFn)
 *   select(attrs, options[{value,label}], value) → <select>
 *   field(labelText, control, hint?) → <div class="pf-field"> (a <label for> a vezérlő id-jére mutat)
 *   pend(node, on)              aria-busy be/ki
 * A szerver üzeneteit és a motor szövegeit mindig szó szerint (textContent) jelenítjük meg.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;

  var STAGES = [];
  for (var i = 0; i < 15; i++) { STAGES.push('S' + (i < 10 ? '0' : '') + String(i)); }
  STAGES.push('FINAL');

  var KIND = { error: 'error', blocker: 'blocker', major: 'warning', warning: 'warning', minor: 'info', info: 'info' };

  function sevKind(sev) { return KIND[sev] || 'neutral'; }

  function refs(v) {
    if (Array.isArray(v)) { return v.filter(function (x) { return typeof x === 'string' && x; }); }
    if (typeof v !== 'string') { return []; }
    return v.split(/[\s,;|]+/).filter(function (x) { return !!x; });
  }

  function section(titleKey, id, extra) {
    var body = h('div', { 'class': 'section-body', 'aria-busy': 'true' }, MA.ui.spinner());
    var head = h('h2', { 'class': 'panel-title', id: id + '-h' }, h('span', { i18n: titleKey }), extra ? ' ' : null, extra || null);
    var el = h('section', { 'class': 'panel', id: id, 'aria-labelledby': id + '-h' }, head, body);
    return { el: el, body: body, head: head };
  }

  function fill(sec, nodes) {
    sec.body.removeAttribute('aria-busy');
    MA.dom.mount(sec.body, nodes);
  }

  function load(sec, ctx, path, query, renderFn) {
    return MA.api.get(path, { query: query, signal: ctx.signal, toast: false }).then(function (env) {
      if (ctx.alive()) { fill(sec, renderFn(env)); }
      return env;
    }, function (err) {
      if (ctx.alive() && err.code !== 'ABORTED') { fill(sec, MA.ui.errorBox(err)); }
      return null;
    });
  }

  function select(attrs, options, value) {
    return h('select', attrs, options.map(function (o) {
      return h('option', { value: o.value, selected: String(o.value) === String(value) }, o.label);
    }));
  }

  function field(label, control, hint) {
    var hid = hint ? MA.dom.uid('hint') : null;
    if (hid) { control.setAttribute('aria-describedby', hid); }
    return h('div', { 'class': 'pf-field' }, h('label', { htmlFor: control.id }, label), control,
      hint ? h('span', { 'class': 'pf-hint muted', id: hid }, hint) : null);
  }

  function pend(node, on) {
    if (on) { node.setAttribute('aria-busy', 'true'); } else { node.removeAttribute('aria-busy'); }
  }

  MA.proc = {
    STAGES: STAGES,
    SEVERITIES: ['blocker', 'major', 'minor', 'info'],
    AGENTS: ['planner', 'reviewer', 'evaluator', 'orchestrator', 'engine', 'user'],
    sevKind: sevKind,
    refs: refs,
    section: section,
    fill: fill,
    load: load,
    select: select,
    field: field,
    pend: pend
  };
})();
