/* dev/extraction_backend.js — CSAK a fejlesztői buildben (build_gui.py --dev, ?fixtures=1).
 *
 * Állapottartó fixture-háttér a 3 Kinyerés, az átváltó és a projekt/adatvédelem képernyő útvonalaihoz:
 *   GET/PUT /api/table (ETag, If-Match → 409 cellaszintű háromutas diff, 423 Excel-zár), POST /api/validate,
 *   GET/PUT /api/provenance, GET/PUT /api/documents, POST /api/fileurl, POST /api/log/decision, POST /api/convert,
 *   GET/POST /api/project, GET /api/privacy (+ ?data_class= előnézet), POST /api/privacy/apply.
 * A kiinduló adatok a web/fixtures/*.json fájlok (a valódi motor kimenetéből generálva).
 *
 * FIGYELEM: a POST /api/validate itt egy MINIMÁLIS ÁLMOTOR (V002/V003/V004/V005/V006/V009/V015/V016/V018/V019/V023),
 * kizárólag a felület interakcióinak teszteléséhez; a termékben a validálás a motoré (6.1). A többi kódot
 * (pl. V011, V013) a fixture-ből veszi át, amíg az érintett cellák változatlanok.
 *
 * Tesztelői segédek: MA.dev.extraction.{state(), externalEdit(ds, uid, oszlopnév, érték), lock(ds, bool),
 *   slowSave(ms), privacy({gitignore, deny, guard}), decisions()}.
 */
(function () {
  'use strict';
  var MA = window.MA;
  var FX = MA.dev && MA.dev.fixtures;
  if (!FX) { return; }

  var S = null;
  var DATASETS = ['03_adatok/o1.csv', '03_adatok/o2.csv', '03_adatok/o3.csv'];

  function clone(o) { return o === undefined ? undefined : JSON.parse(JSON.stringify(o)); }
  function same(want, got) {
    return Object.keys(want || {}).every(function (k) { return got && String(got[k]) === String(want[k]); });
  }
  function seed(file, method, path, cond) {
    var hit = null;
    FX.routes.forEach(function (r) {
      if (hit || r._file !== file || (r.method || 'GET') !== method || r.path !== path) { return; }
      if (cond && cond.query && !same(cond.query, r.query)) { return; }
      if (cond && cond.body && !(same(cond.body, r.body) && Object.keys(r.body || {}).length === Object.keys(cond.body).length)) { return; }
      if (!cond && (r.query || r.body)) { return; }
      hit = r;
    });
    return hit ? clone(hit) : null;
  }

  function init() {
    if (S) { return S; }
    S = { rev: 41, n: 0, tables: {}, seedTables: {}, prov: {}, seedValid: {}, docs: null, decisions: [], locks: { '03_adatok/o2.csv': true } };
    DATASETS.forEach(function (ds) {
      var t = seed('table.json', 'GET', '/api/table', { query: { dataset: ds } });
      if (t) {
        S.tables[ds] = { data: t.envelope.data, versions: {} };
        S.tables[ds].versions[t.envelope.data.etag] = snap(t.envelope.data);
        S.seedTables[ds] = clone(t.envelope.data);
      }
      var p = seed('provenance.json', 'GET', '/api/provenance', { query: { dataset: ds } });
      S.prov[ds] = { doc: p ? p.envelope.data.provenance : { schema: 'szk.ma.provenance/v1', table: ds, table_sha256: null, cells: [] },
        etag: p ? p.envelope.data.etag : null };
      var v = seed('validate.json', 'POST', '/api/validate', { body: { dataset: ds } });
      S.seedValid[ds] = v ? v.envelope.data : null;
    });
    var d = seed('documents.json', 'GET', '/api/documents');
    S.docs = { doc: d ? d.envelope.data : { schema: 'szk.ma.documents/v1', docs: [] }, etag: 'docs-1' };
    var pr = seed('project_actions.json', 'GET', '/api/project', { query: { include: 'recent' } });
    S.project = pr ? pr.envelope.data : null;
    S.recent = S.project && S.project.recent ? S.project.recent : [];
    if (S.project) { delete S.project.recent; }
    var pv = seed('privacy.json', 'GET', '/api/privacy');
    S.privacyBase = pv ? pv.envelope.data : null;
    S.privacyState = S.privacyBase ? { gitignore: !!S.privacyBase.gitignore_block_present, deny: !!S.privacyBase.deny_rule_present, guard: !!S.privacyBase.precommit_guard_installed } : null;
    return S;
  }

  function ok(schema, data, etag) {
    S.n += 1;
    return { status: 200, etag: etag ? '"' + etag + '"' : undefined,
      envelope: { ok: true, schema: schema, data: data, warnings: [], meta: { engine: '0.2.0', elapsed_ms: 1, project_rev: S.rev, request_id: 'q_dx' + S.n } } };
  }
  function fail(code, http, message, details) {
    var e = { code: code, http: http, message: message };
    if (details) { e.details = details; }
    return { status: http, envelope: { ok: false, error: e } };
  }
  function ifMatch(req) { var v = req.headers['If-Match']; return v ? String(v).replace(/^W\//, '').replace(/"/g, '') : null; }
  function bump(prefix) { S.rev += 1; S.n += 1; return prefix + '-' + String(S.rev) + '-' + String(S.n); }

  // ---------------------------------------------------------------- táblák + háromutas cella-diff (ma_gui/store.py cell_diff)
  function snap(d) { return { header: d.header.slice(), rows: d.rows.map(function (r) { return { row_uid: r.row_uid, cells: r.cells.slice() }; }) }; }
  function dedupe(names) { var seen = {}; return names.map(function (n) { seen[n] = (seen[n] || 0) + 1; return seen[n] === 1 ? n : n + '#' + seen[n]; }); }
  function asMap(v) {
    var out = { cols: [], order: [], m: {} };
    if (!v) { return out; }
    out.cols = dedupe(v.header);
    v.rows.forEach(function (r) {
      out.order.push(r.row_uid);
      var o = {};
      out.cols.forEach(function (c, i) { o[c] = i < r.cells.length ? r.cells[i] : ''; });
      out.m[r.row_uid] = o;
    });
    return out;
  }
  function union() {
    var seen = {}, out = [];
    Array.prototype.forEach.call(arguments, function (list) { list.forEach(function (x) { if (!seen[x]) { seen[x] = 1; out.push(x); } }); });
    return out;
  }
  function get(map, uid, col) { var r = map.m[uid]; return r && Object.prototype.hasOwnProperty.call(r, col) ? r[col] : null; }
  function cellDiff(base, theirs, mine, known) {
    var b = asMap(base), t = asMap(theirs), m = asMap(mine), out = [];
    union(t.order, m.order, b.order).forEach(function (uid) {
      union(t.cols, m.cols, b.cols).forEach(function (col) {
        var bv = get(b, uid, col), tv = get(t, uid, col), mv = get(m, uid, col), conflict;
        if (known) {
          if (tv === bv) { return; }
          conflict = mv !== bv && mv !== tv;
        } else {
          if (tv === mv) { return; }
          conflict = true;
        }
        out.push({ row_uid: uid, column: col, base: known ? bv : null, theirs: tv, mine: mv, conflict: conflict });
      });
    });
    return out;
  }

  function submission(body) {
    var header = (body.header || []).slice();
    var ui = header.indexOf('row_uid');
    if (ui >= 0) { header.splice(ui, 1); }
    return { header: header, rows: (body.rows || []).map(function (r, i) {
      var cells = Array.isArray(r) ? r.slice() : (r.cells || []).slice();
      if (ui >= 0 && Array.isArray(r)) { cells.splice(ui, 1); }
      return { row_uid: r.row_uid || ('rnew' + String(i)), cells: cells.map(function (c) { return c === null || c === undefined ? '' : String(c); }) };
    }) };
  }

  FX.route('GET', '/api/table', function (req) {
    init();
    var t = S.tables[req.query.dataset];
    if (!t) { return fail('NOT_FOUND', 404, 'Nincs ilyen adattábla: ' + req.query.dataset, { dataset: req.query.dataset }); }
    return ok('szk.ma.table/v1', clone(t.data), t.data.etag);
  });

  // PHI/TAJ-szkenner és írás-tartás (ma_gui/privacy.py scan_table / can_write — egyszerűsített fejlesztői változat)
  var PHI_COLS = /^(taj|taj[_ -]?sz[aá]m|n[eé]v|name|sz[uü]let[eé]si.*|birth.*|dob|c[ií]m|address|telefon|phone|e-?mail|mrn|patient_id|beteg_azonos[ií]t[oó])$/i;
  var PHI_LABELS = { column: 'oszlopnév', email: 'e-mail-cím', taj_cdv: '9 jegyű TAJ-szám érvényes CDV-ellenőrzőjeggyel' };
  function tajOk(s) {
    if (!/^\d{9}$/.test(s)) { return false; }
    var sum = 0;
    for (var i = 0; i < 8; i++) { sum += Number(s.charAt(i)) * (i % 2 === 0 ? 3 : 7); }
    return sum % 10 === Number(s.charAt(8));
  }
  function scanPhi(header, rows) {
    var out = [];
    header.forEach(function (name, ci) {
      if (PHI_COLS.test(String(name).trim())) { out.push({ kind: 'column', column: name, column_index: ci, row_index: null, pattern: 'name' }); }
    });
    rows.forEach(function (r, ri) {
      r.cells.forEach(function (c, ci) {
        var v = String(c).trim();
        if (/^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i.test(v)) { out.push({ kind: 'value', column: header[ci], column_index: ci, row_index: ri, pattern: 'email' }); }
        if (tajOk(v.replace(/[\s-]/g, ''))) { out.push({ kind: 'value', column: header[ci], column_index: ci, row_index: ri, pattern: 'taj_cdv' }); }
      });
    });
    return out;
  }
  function describePhi(list) {
    var n = {};
    list.forEach(function (f) { var k = f.kind === 'column' ? 'column' : f.pattern; n[k] = (n[k] || 0) + 1; });
    return 'PHI-gyanú (' + Object.keys(n).map(function (k) { return (PHI_LABELS[k] || k) + ' ×' + String(n[k]); }).join('; ') +
      '). A mentés csak a _privat/ alá engedett; hamis riasztásnál indokolt, naplózott felülbírálással menthető.';
  }
  function canWrite(rel, phi, consent) {
    var dc = (S.project && S.project.data_class) || 'A';
    var priv = /^_privat\/./.test(rel);
    if (dc === 'C' && !priv) { return 'C osztályú (betegszintű) adat csak a _privat/ mappába írható.'; }
    if (phi && !priv) { return 'PHI-gyanús tartalom: a mentés csak a _privat/ alá engedett; hamis riasztásnál indokolt, naplózott felülbírálással menthető.'; }
    if (dc === 'A' || !(S.privacyBase.vault && S.privacyBase.vault.tracked)) { return null; }
    if (dc === 'B' && consent) { return null; }
    if (priv && S.privacyState.gitignore) { return null; }
    return 'Írás-tartás: ' + dc + ' osztály, a projektet a vault követi (munkamenet végén GitHubra kerül), és a cél nincs a .gitignore-ban. Lehetőségek: a kezelt .gitignore-blokk beírása (és mentés a _privat/ alá), vagy a projekt áthelyezése a vault gyökerén kívülre.';
  }

  FX.route('PUT', '/api/table', function (req) {
    init();
    var body = req.body || {};
    var ds = body.dataset;
    var t = S.tables[ds];
    if (!t) { return fail('NOT_FOUND', 404, 'Nincs ilyen adattábla: ' + ds, { dataset: ds }); }
    var mine = submission(body);
    var phi = scanPhi(mine.header, mine.rows);
    var flagged = phi.length > 0 && !body.phi_override;
    var targets = [ds].concat(body.provenance ? [ds.replace(/\.[^.\/]+$/, '') + '.prov.json'] : []);
    for (var ti = 0; ti < targets.length; ti++) {
      var why = canWrite(targets[ti], flagged, !!body.consent);
      if (why) { return fail('FORBIDDEN', 403, why, flagged ? { phi: phi, summary: describePhi(phi) } : { path: targets[ti] }); }
    }
    var im = ifMatch(req);
    if (im && im !== t.data.etag) {
      var base = t.versions[im] || null;
      var diff = cellDiff(base, snap(t.data), mine, !!base);
      var nConf = diff.filter(function (x) { return x.conflict; }).length;
      return fail('CONFLICT', 409, 'A(z) ' + ds + ' időközben megváltozott (például Excelben vagy egy ágens írta át): ' + String(diff.length) +
        ' eltérő cella, ebből ' + String(nConf) + ' ütközik a te módosításoddal. Töltsd be újra, vagy fésüld össze a változásokat.',
      { dataset: ds, etag: t.data.etag, diff: diff, base_known: !!base, truncated: false });
    }
    if (S.locks[ds]) { return fail('LOCKED', 423, 'Zárd be a fájlt az Excelben, majd próbáld újra.', { path: ds }); }
    var etag = bump('fx-' + ds.slice(-6, -4));
    var slow = S.putDelay || 0;
    t.data = Object.assign({}, t.data, { etag: etag, header: mine.header,
      rows: mine.rows.map(function (r, i) { return { row_uid: r.row_uid, cells: r.cells, line: i + 2 }; }) });
    t.versions[etag] = snap(t.data);
    if (body.provenance) {
      var doc = clone(body.provenance);
      doc.table = ds;
      doc.table_sha256 = etag;
      S.prov[ds] = { doc: doc, etag: bump('prov-' + ds.slice(-6, -4)) };
    }
    var res = ok('szk.ma.table/v1', clone(t.data), etag);
    res.delay_ms = slow;
    return res;
  });

  // ---------------------------------------------------------------- álmotor a validáláshoz (lásd a fejkommentet)
  var RULES = {
    V002: ['error', 'Hiányzó kötelező érték', 'A vizsgálat kimarad az elemzésből; pótold a közleményből, a szerzőtől, vagy dokumentált konverzióval (medián/IQR, SE, CI → SD).', 'Cochrane Handbook 6.5.2'],
    V003: ['error', 'Nem szám érték', 'A hatásmérethez szükséges cella nem értelmezhető számként; a vizsgálat kimarad az elemzésből, amíg nem javítod.', 'engine'],
    V004: ['error', 'Érvénytelen mintanagyság', 'Nem egész n, vagy túl kicsi: folytonos adatnál n < 2 (karonként), bináris és arány-adatnál n < 1, korrelációnál n < 4. A vizsgálat kimarad az elemzésből.', 'engine'],
    V005: ['error', 'Nem pozitív SD', 'Az SD <= 0; valószínű adatkinyerési hiba.', 'engine'],
    V006: ['error', 'Érvénytelen eseményszám', 'Az eseményszám negatív vagy nagyobb, mint n.', 'engine'],
    V009: ['info', 'Nulla cella', 'Folytonossági korrekció kerül alkalmazásra a vizsgálat hatásméreténél (mértéke: --cc, alapértelmezés 0,5; lásd a részleteket).', 'Cochrane Handbook 10.4.4.1'],
    V015: ['warning', 'Kevés vizsgálat', 'k < 5: a τ² becslése megbízhatatlan; HKSJ-CI és predikciós intervallum ajánlott, a véletlen hatású eredményt óvatosan értelmezd.', 'Cochrane Handbook 10.10.4'],
    V016: ['info', 'Publikációs torzítás tesztje alulerőzött', 'k < 10: funnel-aszimmetria tesztek (Egger, Begg) nem ajánlottak.', 'Sterne et al. 2011 BMJ'],
    V018: ['info', 'Becsült (imputált) értékek', 'Becsült/imputált adatot tartalmazó vizsgálatok: érzékenységi elemzés javasolt nélkülük.', 'Cochrane Handbook 6.5.2.10'],
    V019: ['info', 'Magas torzítási kockázatú vizsgálatok', 'Érzékenységi elemzés javasolt a magas RoB-ú vizsgálatok kizárásával.', 'Cochrane Handbook 7–8'],
    V023: ['warning', 'Kétértelmű számformátum', "A számot ezres tagolásként olvastuk (pl. '2,000' → 2000), mert tizedesjelként értelmetlen lenne vagy ellentmond a fájl tizedesjelének. Ellenőrizd a forrással.", 'engine']
  };
  var KEEP = { V011: 1, V013: 1, V007: 1, V017: 1 };
  var SYN = { study: ['study', 'vizsgálat', 'szerző', 'author', 'label'], e1: ['e1', 'esemény1', 'events1'], n1: ['n1'], e2: ['e2', 'esemény2', 'events2'],
    n2: ['n2'], m1: ['m1', 'mean1'], sd1: ['sd1'], m2: ['m2', 'mean2'], sd2: ['sd2'], rob: ['rob'], estimated: ['estimated', 'becsült'], year: ['év', 'year'] };
  var REQ = { RR: ['e1', 'n1', 'e2', 'n2'], OR: ['e1', 'n1', 'e2', 'n2'], RD: ['e1', 'n1', 'e2', 'n2'],
    MD: ['m1', 'sd1', 'n1', 'm2', 'sd2', 'n2'], SMD: ['m1', 'sd1', 'n1', 'm2', 'sd2', 'n2'], ROM: ['m1', 'sd1', 'n1', 'm2', 'sd2', 'n2'] };

  function finding(code, f) {
    var r = RULES[code];
    return Object.assign({ code: code, severity: r[0], title: r[1], advice: r[2], source: r[3], kb_id: code, detail: '', fields: [], blocking: false,
      study: null, row: null, row_uid: null, line: null, acknowledged: null }, f);
  }

  function fakeValidate(body) {
    var tb = body.table || { header: [], rows: [] };
    var header = tb.header.map(function (x) { return String(x).trim(); });
    var canon = {}, column_map = {};
    header.forEach(function (name, i) {
      if (name === 'row_uid') { return; }
      var low = name.toLowerCase(), hit = null;
      Object.keys(SYN).forEach(function (c) { if (!hit && SYN[c].indexOf(low) >= 0) { hit = c; } });
      var key = hit || name;
      if (canon[key] === undefined) { canon[key] = i; column_map[key] = name; }
    });
    var ui = header.indexOf('row_uid');
    var measure = body.measure || 'RR';
    var req = REQ[measure] || REQ.RR;
    var ds = body.dataset;
    var seedRows = {};
    if (ds && S.seedTables[ds]) { S.seedTables[ds].rows.forEach(function (r) { seedRows[r.row_uid] = r; }); }
    var out = [], blocked = {};
    tb.rows.forEach(function (row, i) {
      function cell(f) { return canon[f] === undefined ? '' : String(row[canon[f]] === undefined || row[canon[f]] === null ? '' : row[canon[f]]).trim(); }
      var uid = ui >= 0 ? row[ui] : null;
      var label = cell('study') || '#' + String(i + 1);
      var loc = { study: label, row: i, row_uid: uid, line: i + 2 };
      var val = {}, missing = [];
      req.forEach(function (f) {
        var s = cell(f);
        if (s === '' || /^(na|nr|n\.a\.|-|\.)$/i.test(s)) { missing.push(f); return; }
        var count = /^[en]\d?$/.test(f);
        if (/^[-+]?\d+$/.test(s)) { val[f] = Number(s); } else if (count && /^\d{1,3}(,\d{3})+$/.test(s)) {
          val[f] = Number(s.replace(/,/g, ''));
          out.push(finding('V023', Object.assign({ fields: [f], detail: String(i + 2) + '. sor, ' + header[canon[f]] + ' oszlop: ezres tagolásként értelmezve: ' + s + ' → ' + String(val[f]) }, loc)));
        } else if (/^[-+]?\d+[.,]\d+$/.test(s)) { val[f] = Number(s.replace(',', '.')); } else {
          out.push(finding('V003', Object.assign({ fields: [f], blocking: true, detail: f + ": '" + s + "'" }, loc)));
          blocked[i] = 'V003';
        }
      });
      if (missing.length) {
        out.push(finding('V002', Object.assign({ fields: missing, blocking: true, detail: 'hiányzik: ' + missing.join(', ') }, loc)));
        blocked[i] = 'V002';
      }
      ['1', '2'].forEach(function (arm) {
        var e = val['e' + arm], n = val['n' + arm];
        if (n !== undefined && (n < 1 || n !== Number(String(n).split('.')[0]))) {
          out.push(finding('V004', Object.assign({ fields: ['n' + arm], blocking: true, detail: 'n' + arm + ' = ' + String(n) }, loc)));
          blocked[i] = 'V004';
        }
        if (e !== undefined && n !== undefined && (e < 0 || e > n)) {
          out.push(finding('V006', Object.assign({ fields: ['e' + arm, 'n' + arm], blocking: true, detail: 'e' + arm + ' = ' + String(e) + ', n' + arm + ' = ' + String(n) }, loc)));
          blocked[i] = 'V006';
        }
        var sd = val['sd' + arm];
        if (sd !== undefined && sd <= 0) {
          out.push(finding('V005', Object.assign({ fields: ['sd' + arm], blocking: true, detail: 'sd' + arm + ' = ' + String(sd) }, loc)));
          blocked[i] = 'V005';
        }
      });
      if (!blocked[i] && (val.e1 === 0 || val.e2 === 0)) { out.push(finding('V009', Object.assign({ detail: '+0.5 minden cellához' }, loc))); }
      if (/^(igen|yes|true|1)$/i.test(cell('estimated'))) { out.push(finding('V018', Object.assign({ fields: ['estimated'] }, loc))); }
      if (/^(high|magas|serious|critical)/i.test(cell('rob'))) { out.push(finding('V019', Object.assign({ fields: ['rob'] }, loc))); }
      // a fixture további (heurisztikus) megállapításai, amíg a cellák változatlanok
      var sv = ds ? S.seedValid[ds] : null;
      if (sv && uid && seedRows[uid]) {
        sv.findings.forEach(function (f) {
          if (!KEEP[f.code] || f.row_uid !== uid) { return; }
          var unchanged = (f.fields || []).every(function (fld) {
            var si = S.seedTables[ds].header.indexOf(sv.column_map[fld]);
            return si >= 0 && canon[fld] !== undefined && seedRows[uid].cells[si] === String(row[canon[fld]] || '');
          });
          if (unchanged) { out.push(Object.assign(clone(f), loc)); }
        });
      }
    });
    var k = tb.rows.length - Object.keys(blocked).length;
    if (k < 5) { out.push(finding('V015', { detail: 'k = ' + String(k) + ' elemezhető vizsgálat (' + String(tb.rows.length) + ' sorból)' })); }
    if (k < 10) { out.push(finding('V016', { detail: 'k = ' + String(k) })); }
    out.forEach(function (f) {
      S.decisions.forEach(function (d) {
        if (d.code === f.code && d.row_uid === f.row_uid && d.dataset === ds) { f.acknowledged = d.id; }
      });
    });
    var summary = { error: 0, warning: 0, info: 0 };
    out.forEach(function (f) { summary[f.severity] += 1; });
    return {
      schema: 'szk.ma.validation/v1', engine_version: '0.2.0', measure: measure, input_sha256: null, column_map: column_map,
      decimal_mark: tb.decimal_mark || null, summary: summary, k_analysable: k, findings: out,
      excluded: Object.keys(blocked).map(function (i) { return { study: String(tb.rows[i][canon.study] || ''), row: Number(i), reason: blocked[i] }; })
    };
  }

  FX.route('POST', '/api/validate', function (req) {
    init();
    var b = req.body || {};
    if (!b.table || !Array.isArray(b.table.header) || !Array.isArray(b.table.rows)) { return fail('BAD_REQUEST', 400, 'A kérés table {header, rows} mezője hiányzik.'); }
    var r = ok('szk.ma.validation/v1', fakeValidate(b));
    r.delay_ms = 20;
    return r;
  });

  // ---------------------------------------------------------------- eredet, dokumentumok, fájl-URL, döntés
  FX.route('GET', '/api/provenance', function (req) {
    init();
    var ds = req.query.dataset;
    var p = S.prov[ds];
    if (!p) { p = S.prov[ds] = { doc: { schema: 'szk.ma.provenance/v1', table: ds, table_sha256: null, cells: [] }, etag: null }; }
    return ok('szk.ma.provenance/v1', provView(ds), p.etag);
  });

  function provView(ds) {
    var p = S.prov[ds], t = S.tables[ds];
    var stated = p.doc.table_sha256;
    return { provenance: clone(p.doc), etag: p.etag, state: { table_etag: t ? t.data.etag : null, in_sync: stated === null || !t ? null : stated === t.data.etag } };
  }

  FX.route('PUT', '/api/provenance', function (req) {
    init();
    var ds = req.query.dataset;
    var p = S.prov[ds];
    var im = ifMatch(req);
    if (p && p.etag && im && im !== p.etag) {
      return fail('CONFLICT', 409, 'A(z) ' + ds.replace(/\.csv$/, '.prov.json') + ' időközben megváltozott; töltsd be újra.', { path: ds, etag: p.etag });
    }
    var doc = clone(req.body || {});
    doc.table = ds;
    var etag = bump('prov-' + ds.slice(-6, -4));
    S.prov[ds] = { doc: doc, etag: etag };
    return ok('szk.ma.provenance/v1', provView(ds), etag);
  });

  FX.route('GET', '/api/documents', function () { init(); return ok('szk.ma.documents/v1', clone(S.docs.doc), S.docs.etag); });
  FX.route('PUT', '/api/documents', function (req) {
    init();
    var im = ifMatch(req);
    if (im && im !== S.docs.etag) { return fail('CONFLICT', 409, 'A documents.json időközben megváltozott; töltsd be újra.', { etag: S.docs.etag }); }
    var doc = clone(req.body || {});
    if (!doc || !Array.isArray(doc.docs)) { return fail('VALIDATION', 422, 'A(z) 03_adatok/documents.json nem felel meg a(z) szk.ma.documents/v1 szerződésnek: docs: lista kell'); }
    S.docs = { doc: doc, etag: bump('docs') };
    return ok('szk.ma.documents/v1', clone(doc), S.docs.etag);
  });

  FX.route('POST', '/api/fileurl', function (req) {
    init();
    var b = req.body || {};
    var id = b.doc === undefined ? null : b.doc;
    var path = b.path === undefined ? null : b.path;
    if ((id === null) === (path === null)) { return fail('BAD_REQUEST', 400, 'Pontosan egy mező adható meg: doc (jegyzékbeli dokumentum) vagy path (futás-artefaktum).'); }
    if (path !== null) {
      // futás-artefaktum (ma_gui/routes/documents.py: _run:<út>, csak 05_elemzes/ vagy 06_kezirat/ alatt)
      if (!/^(05_elemzes|06_kezirat)\//.test(path) || /(^|\/)\.\.(\/|$)/.test(path)) { return fail('NOT_FOUND', 404, 'Nincs ilyen futás-artefaktum.', { path: path }); }
      return ok('szk.ma.fileurl/v1', { url: '/f/' + encodeURIComponent('_run:' + path) + '/1791200000/dev' + String(S.n), expires_at: 1791200000, ttl: 600 });
    }
    var known = S.docs.doc.docs.some(function (d) { return d.id === id; });
    if (!known) { return fail('NOT_FOUND', 404, 'Nincs ilyen dokumentum a jegyzékben.', { doc: id }); }
    return ok('szk.ma.fileurl/v1', { url: '/f/' + encodeURIComponent(id) + '/1791200000/dev' + String(S.n), expires_at: 1791200000 });
  });

  FX.route('POST', '/api/log/decision', function (req) {
    init();
    var b = req.body || {};
    if (!b.decision || !b.rationale) { return fail('BAD_REQUEST', 400, 'A döntés és az indoklás kötelező.'); }
    var c = b.context || {};
    var id = 101 + S.decisions.length;
    S.decisions.push({ id: id, code: c.code || (b.kb_refs || [])[0], row_uid: c.row_uid || null, dataset: c.dataset || null, body: clone(b) });
    S.rev += 1;
    return ok('szk.ma.log-write/v1', { kind: 'decision', id: id, warnings: [] });
  });

  // ---------------------------------------------------------------- átváltó (a motor-számolta fixture-eredmények)
  var CONV_REQ = { se_to_sd: ['se', 'n'], ci_to_sd: ['lower', 'upper', 'n'], combine_groups: ['n1', 'm1', 'sd1', 'n2', 'm2', 'sd2'], change_sd: ['sd_baseline', 'sd_final', 'corr'] };
  FX.route('POST', '/api/convert', function (req) {
    init();
    var b = req.body || {};
    var ins = b.inputs || {};
    var bad = Object.keys(ins).filter(function (k) { return !/^[-+]?\d+([.,]\d+)?$/.test(String(ins[k]).trim()); });
    if (bad.length) { return fail('VALIDATION', 422, 'nem értelmezhető számként: ' + bad.join(', ')); }
    var need = CONV_REQ[b.kind];
    if (b.kind === 'median_to_mean_sd') {
      if (!ins.median) { return fail('VALIDATION', 422, 'a medián kötelező'); }
      if (b.method === 'hozo' && !(ins.min && ins.max)) { return fail('VALIDATION', 422, 'Hozo-képlethez min és max kell'); }
      if (!ins.n && b.method !== 'hozo') { return fail('VALIDATION', 422, 'n >= 2 szükséges, kapott: None'); }
      if (!((ins.q1 && ins.q3) || (ins.min && ins.max))) { return fail('VALIDATION', 422, 'min+max vagy Q1+Q3 kell'); }
    } else if (need) {
      var miss = need.filter(function (k) { return !ins[k]; });
      if (miss.length) { return fail('VALIDATION', 422, 'hiányzó bemenet: ' + miss.join(', ')); }
    } else {
      return fail('BAD_REQUEST', 400, 'Ismeretlen átváltás: ' + String(b.kind));
    }
    var r = (b.method && seed('convert.json', 'POST', '/api/convert', { body: { kind: b.kind, method: b.method } })) ||
      seed('convert.json', 'POST', '/api/convert', { body: { kind: b.kind } });
    if (!r) { return fail('NOT_FOUND', 404, 'Nincs eredmény ehhez az átváltáshoz: ' + b.kind); }
    return { status: 200, envelope: r.envelope, delay_ms: 10 };
  });

  // ---------------------------------------------------------------- projekt és adatvédelem (ma_gui/privacy.py status() logikája szerint)
  var LABELS = { A: 'publikált aggregált', B: 'nem publikált aggregált', C: 'betegszintű / azonosítható' };
  var NOTE = "A pre-commit őr csak tartalék: akkor állítja meg a commitot (a vault mentését is), ha érzékeny fájl mégis az indexbe került — kényszerített 'git add -f' vagy korábban követett fájl miatt. A szokásos 'git add -A'-t a kezelt .gitignore-blokk már kiszűri.";

  function privacyStatus(dc) {
    var st = S.privacyState;
    var d = clone(S.privacyBase);
    var sensitive = dc === 'B' || dc === 'C';
    var tracked = d.vault && d.vault.tracked;
    d.data_class = dc;
    d.data_class_label = LABELS[dc];
    d.gitignore_block_present = st.gitignore;
    d.deny_rule_present = st.deny;
    d.precommit_guard_installed = st.guard;
    d.write_hold = sensitive && tracked && !st.gitignore
      ? { active: true, reason: dc + ' osztály: a projektet a vault követi, és a _privat/ nincs a .gitignore-ban — adat nem írható, amíg a kezelt .gitignore-blokk nincs beírva (vagy a projekt nincs áthelyezve).' }
      : { active: false, reason: null };
    var reasons = [];
    if (dc === 'C' && tracked) {
      if (!st.gitignore) { reasons.push('hiányzik a kezelt .gitignore-blokk (L2)'); }
      if (!st.guard) { reasons.push('nincs telepítve a pre-commit őr (L3)'); }
    }
    var recs = [], acts = [];
    if (tracked) {
      recs.push(d.vault.reason);
      if (!st.gitignore) { recs.push('Írd be a kezelt .gitignore-blokkot: a _privat/, a *_PHI* és a többi érzékeny minta kimarad a vault mentéséből.'); }
      if (sensitive) { recs.push("Ha a projektet egészen ki akarod hagyni a vault mentéséből: 'vault pause', vagy vedd fel a mappa nevét (bcg-oltas) a vault exclude listájába (~/.claude/vault/config.json). A munkapad a vault konfigurációját nem módosítja."); }
      if (dc === 'C' && !st.guard) { recs.push('C osztálynál a vault gyökere alatt a pre-commit őr is kell. ' + NOTE); }
    }
    if (!st.gitignore && (tracked || sensitive)) { acts.push({ id: 'gitignore', label: 'Kezelt .gitignore-blokk beírása (diff-előnézettel)' }); }
    if (d.cloud_sync) {
      recs.push(sensitive ? 'A projekt mappáját a(z) OneDrive szinkronizálja (~/Documents): helyezd a _privat/ mappát szinkronizálatlan helyre, és hivatkozd át a doc_roots-ban.'
        : 'A projekt mappáját a(z) OneDrive szinkronizálja (~/Documents); A osztálynál ez csak figyelmeztetés.');
    }
    if (sensitive && !st.deny) {
      recs.push('Javasolt Claude deny-szabály a .claude/settings.json-ban (Read(./_privat/**), Read(**/*_PHI*)): a Claude-munkamenetben olvasott fájl tartalma a modell-szolgáltatóhoz kerül.');
      acts.push({ id: 'deny', label: '.claude/settings.json deny-javaslat (diff-előnézettel)' });
    }
    if (!st.guard && (dc === 'C' || (sensitive && tracked))) { acts.push({ id: 'precommit', label: 'Pre-commit őr telepítése (opt-in)' }); }
    if (reasons.length) { recs.push('C osztály: a vault gyökere alatt a projekt csak védelemmel nyitható meg — ' + reasons.join('; ') + '. (Vagy szüneteltesd a vaultot, illetve zárd ki a projektet.)'); }
    d.open_blocked = { blocked: reasons.length > 0, reasons: reasons };
    d.recommendations = recs;
    d.actions = acts;
    return d;
  }

  FX.route('GET', '/api/privacy', function (req) {
    init();
    var dc = req.query.data_class || (S.project && S.project.data_class) || S.privacyBase.data_class;
    if (['A', 'B', 'C'].indexOf(dc) < 0) { return fail('VALIDATION', 422, 'Ismeretlen adatosztály: a projekt osztálya A, B vagy C lehet.'); }
    var out = privacyStatus(dc);
    out.preview = !!req.query.data_class;
    out.project_data_class = (S.project && S.project.data_class) || 'A';
    return ok('szk.ma.privacy/v1', out);
  });

  FX.route('POST', '/api/privacy/apply', function (req) {
    init();
    var b = req.body || {};
    var key = { gitignore: 'gitignore', deny: 'deny', precommit: 'guard' }[b.action];
    if (!key) { return fail('BAD_REQUEST', 400, 'Ismeretlen művelet: ' + String(b.action)); }
    if (b.dry_run === true) {
      var plan = seed('privacy_apply.json', 'POST', '/api/privacy/apply', { body: { action: b.action, dry_run: true } });
      return ok('szk.ma.privacy-plan/v1', plan.envelope.data);
    }
    if (b.confirm !== true) { return fail('BAD_REQUEST', 400, 'Hiányzik a kifejezett jóváhagyás (confirm: true) vagy a dry_run.'); }
    var res = seed('privacy_apply.json', 'POST', '/api/privacy/apply', { body: { action: b.action, confirm: true } });
    S.privacyState[key] = true;
    S.rev += 1;
    var data = res.envelope.data;
    data.status = privacyStatus((S.project && S.project.data_class) || 'A');
    return ok('szk.ma.privacy-plan/v1', data);
  });

  FX.route('GET', '/api/project', function (req) {
    init();
    var d = clone(S.project);
    if (req.query.include === 'recent') { d.recent = clone(S.recent); }
    // a táblák ETag-je élő (mint a szerver list_tables-e): a rácsban vagy „külső” szerkesztés után változik;
    // a MA.dev.mapTableSha horog (dev/analysis_fixtures.js) a saját fixture-világához igazíthatja
    if (Array.isArray(d.tables)) {
      d.tables = d.tables.map(function (tb) {
        var cur = S.tables[tb.dataset] ? S.tables[tb.dataset].data.etag : tb.etag;
        if (MA.dev.mapTableSha) { cur = MA.dev.mapTableSha(tb.dataset, cur); }
        return Object.assign({}, tb, { etag: cur });
      });
    }
    return ok('szk.ma.project/v1', d);
  });

  FX.route('POST', '/api/project', function (req) {
    init();
    var b = req.body || {};
    var same = !b.path || b.path === S.project.path || b.path === S.project.name;
    var other = fail('BAD_REQUEST', 400, 'Ez a munkapad a(z) „' + S.project.name + '” projekthez indult; másik projektmappához indíts új példányt: python ma.py gui --project <mappa> (Windowson: py -3 ma.py gui --project <mappa>).',
      { project: S.project.path });
    if (b.action === 'open') {
      if (!same) { return other; }
    } else if (b.action === 'init') {
      if (!same) { return other; }
      if (!b.title) { return fail('BAD_REQUEST', 400, 'Hiányzó mező: title.'); }
      S.project = { schema: 'szk.ma.project/v1', title: b.title, question: { P: '', I: '', C: '', O: '' }, review_type: 'intervention', data_class: b.data_class || 'A',
        locale: 'hu', outcomes: [], appraisal_tools: [], composer: null, doc_roots: [], path: S.project.path, name: S.project.name, initialized: true,
        has_project_json: true, open_blockers: 0, tables: [], rev: S.rev, data_class_source: 'ma-projekt.json', user: { initials: 'SzK' } };
    } else if (b.action === 'data_class') {
      if (['A', 'B', 'C'].indexOf(b.data_class) < 0) { return fail('VALIDATION', 422, 'Ismeretlen adatosztály: a projekt osztálya A, B vagy C lehet.'); }
      S.project.data_class = b.data_class;
    } else {
      return fail('BAD_REQUEST', 400, 'Ismeretlen projekt-művelet: ' + String(b.action));
    }
    S.rev += 1;
    return ok('szk.ma.project/v1', clone(S.project));
  });

  // ---------------------------------------------------------------- tesztelői segédek
  MA.dev.extraction = {
    state: function () { return init(); },
    externalEdit: function (ds, uid, column, value, opts) {
      init();
      var t = S.tables[ds];
      var ci = t.data.header.indexOf(column);
      t.data.rows.forEach(function (r) { if (r.row_uid === uid) { r.cells[ci] = String(value); } });
      t.data.etag = bump('fx-ext');
      t.versions[t.data.etag] = snap(t.data);
      if (!opts || opts.notify !== false) {
        MA.bus.emit('changes', { rev: S.rev, changes: [{ path: ds, external: true, actor: 'external' }], reset: false });
      }
      return t.data.etag;
    },
    lock: function (ds, on) { init(); S.locks[ds] = on !== false; },
    slowSave: function (ms) { init(); S.putDelay = ms || 0; },
    privacy: function (patch) { init(); Object.assign(S.privacyState, patch || {}); return clone(S.privacyState); },
    decisions: function () { init(); return clone(S.decisions); }
  };
})();
