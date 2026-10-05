# MA-munkapad — böngészős felület (`ma_gui/web/`)

Vanília JS (ES2019), keretrendszer és külső könyvtár nélkül. A `build_gui.py` a `src/**` forrásait
**egyetlen önálló HTML-be** fűzi (inline `<style>`/`<script>`, CDN és betűkészlet nélkül). A terv
vonatkozó pontjai: 2.3, 3.3–3.5, 4, 6.1–6.3, 6.7–6.8, 7.1 (T4, T6), 7.6, 8.6.

```
web/
  build_gui.py             build + lint (python3 stdlib)
  src/index.template.html  sablon: {{CSS}} {{JS}} {{DATA}} {{BUILD_ID}} és a {{CSP_NONCE}} helyek
  src/css/tokens.css       CSS-tokenek (világos/sötét, Okabe–Ito, --plot-*)
  src/css/base.css         keret, gombok, toast, modális, jelvények, táblák, fókusz, nyomtatás
  src/css/*.css            képernyő-/ábra-stílusok (ábécérendben a base.css után)
  src/dom.js               MA.dom   — biztonságos DOM-építők (h, svg)
  src/geom.js              MA.geom  — pixel-leképezés; AZ EGYETLEN Math.*-felhasználó
  src/i18n.js              MA.i18n  — szótárak, nyelvváltás, a motor {hu,en} szövegeinek kiválasztása
  src/store.js             MA.store, MA.bus, MA.prefs — állapot, eseménybusz, UI-preferencia, undo/redo
  src/api.js               MA.api   — HTTP-kliens, boríték, hibák, munkamenet, long-poll
  src/dev/*.js             CSAK --dev: fixture-háttér (MA.dev.fixtures) és dinamikus fixture-ök
  src/ui.js                MA.ui    — toast, modális, megerősítés, jelvény, menü, fülsor, segédek
  src/selftest.js          MA.selftest — ?selftest=1 oldalon belüli állítások
  src/app.js               MA.app   — keret (3.5.0), hash-router, képernyő-regiszter, téma
  src/components/*.js      közös komponensek (pl. grid.js) — ábécérendben
  src/plots/*.js           SVG-ábrák (szk.ma.plot/v2 → pixel) — ábécérendben
  src/screens/*.js         képernyők (registerScreen) — ábécérendben
  src/boot.js              utolsó: MA.app.start()
  src/i18n/hu.json, en.json            alap-szótárak (lapos, pontozott kulcsok)
  src/i18n/hu/<név>.json, en/<név>.json képernyőnkénti töredékek (azonos kulcskészlettel!)
  fixtures/*.json          fejlesztői API-borítékok (csak a --dev buildbe kerülnek)
  dist/index.html          termék-build (az integrátor másolja a ma_gui/static/index.html-be)
  dist/index.dev.html      fejlesztői build (gitignore-olt)
```

## Build és tesztek

```
python3 ma_gui/web/build_gui.py           # dist/index.html  (termék, ≤ 450 KB, determinisztikus)
python3 ma_gui/web/build_gui.py --dev     # dist/index.dev.html (+ fixture-ök; megnyitás: ?fixtures=1)
python3 ma_gui/web/build_gui.py --check   # 1-es kód, ha a dist/index.html nem naprakész
python3 ma_gui/web/build_gui.py --list    # modulsorrend
python3 tests/gui/ui/test_build_gui.py    # build-, lint- és statikus tesztek (böngésző nélkül)
node tests/gui/ui/shell.spec.js           # Playwright/Chromium: keret, CSP, munkamenet, billentyűzet, téma, nyelv, tároló, önteszt
```

A dev-build megnyitása szerver nélkül is megy (`file://…/index.dev.html?fixtures=1`), de a CSP
csak HTTP-n él; a `shell.spec.js` saját teszt-szervere a nonce-ot behelyettesíti és a
`ma_gui/security.py` `HTML_CSP_TEMPLATE` fejlécét küldi. `?fixtures=1&selftest=1` → önteszt.

**CSP-nonce.** A sablon minden `<script>`/`<style>` tagja `nonce="{{CSP_NONCE}}"`-t visz; a szerver
válaszonként `html.replace("{{CSP_NONCE}}", nonce)`-t végez, és ugyanazt a nonce-ot teszi a
`security.base_headers(nonce, kind="html")` CSP-fejlécébe. A build ellenőrzi, hogy más `{{…}}` nem maradt.

## Kemény szabályok (a build-lint `assert`-ként ellenőrzi — `build_gui.JS_RULES`)

- Minden JS-fájl IIFE `'use strict'`-tel: `(function () { 'use strict'; var MA = window.MA; … })();`
- **Nincs HTML-sztring a DOM-ban**: `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`,
  `DOMParser` tilos. DOM csak `MA.dom.h()` / `MA.dom.svg()` (textContent / createElementNS).
- **Nincs számformázás és statisztika**: `toFixed`, `toPrecision`, `Intl.NumberFormat`,
  `toLocaleString` tilos; `Math.*` csak a `geom.js`-ben (pixel-segédek: `MA.geom.min/max/round/abs…`).
  Minden megjelenített szám a motor `display_text` / `ticks[].text` / `*_text` mezője:
  `MA.ui.num(obj)` vagy `MA.i18n.pick(obj)`; hiányzó mező → „—”.
- **Tároló**: `localStorage` csak `MA.prefs`-en át (`mag.pref.*`, ≤ 256 karakter, UI-preferencia);
  a token a sessionStorage `mag.token` kulcsa (api.js). IndexedDB, Cache, süti tilos. Projektadat
  SOHA nem kerül böngészőtárolóba.
- **Hálózat** csak `MA.api`-n át (`fetch` csak az api.js-ben), csak saját `/api/*` útvonal.
  Külső URL (`http://`, `https://`) sehol (kivétel: az SVG-névtér a dom.js-ben).
- **CSP**: `style`-attribútum és inline eseménykezelő tilos (`h(..., {styles: {...}})` CSSOM-mal és
  `onclick: fn` addEventListenerrel megy); `eval`/`new Function`/sztring-`setTimeout` tilos.
- **ES2019**: `?.` és `??` tilos; nincs `import`/`export`.
- `console.log`, `debugger` nem kerülhet a buildbe.
- i18n: a `hu` és `en` kulcskészlet (és a `{param}`-ok) azonosak; kulcs nem ismétlődhet fájlok közt.

## Képernyő bekötése

1. `src/screens/<név>.js`:

```js
(function () {
  'use strict';
  var MA = window.MA;
  var h = MA.dom.h;

  MA.app.registerScreen({
    id: 'extraction',                 // útvonal: #/extraction?dataset=o1
    title_key: 'tab.extraction',      // i18n-kulcs (h1)
    workspace: 'extraction',          // extraction | analysis | appraisal | process
    tab: 'extraction',                // a fősáv fülje (MA.app.TABS); null = fül nélküli
    order: 10,                        // ugyanazon fül képernyői al-navigációt kapnak, order szerint
    render: function (root, ctx) {    // root: üres <div id="screen-root">; Promise-t is adhat
      root.appendChild(h('div', { 'class': 'panel' }, h('h2', { i18n: 'extraction.title' })));
      return MA.api.get('/api/table', { query: { dataset: ctx.params.dataset }, signal: ctx.signal })
        .then(function (env) { if (!ctx.alive()) { return; } /* … env.data, env.etag … */ });
    },
    onLeave: function (ctx, info) {   // info.reason: 'navigate' | 'refresh'; false → marad
      return MA.ui.confirm({ title: MA.i18n.t('extraction.unsaved'), message: '…' });
    }
  });
})();
```

2. Szótár: `src/i18n/hu/<név>.json` és `src/i18n/en/<név>.json` — azonos kulcsok, képernyő-előtaggal
   (`extraction.*`, `analysis.*`, `plots.*` …).
3. Stílus: `src/css/<név>.css` (csak tokenekkel: `var(--…)`; `url()` tilos).
4. Fixture: `fixtures/<név>.json` (formátum lent); dinamikus: `src/dev/<név>.js` →
   `MA.dev.fixtures.route('POST', '/api/validate', function (req) { return {envelope: …}; })`.
5. Önteszt: `MA.selftest.register('forest: jelölő-pozíció', function (t) { t.near(…); })`.
6. `python3 ma_gui/web/build_gui.py && python3 ma_gui/web/build_gui.py --dev`, majd a tesztek.

A fül helyőrzője (`placeholder`) automatikusan eltűnik, ha a fülre valódi képernyő regisztrál.
A meglévő alapfájlokat (dom/geom/i18n/store/api/ui/app/selftest, alap-szótárak, tokens/base.css)
képernyő-modul ne módosítsa; új igényt az alap gazdájánál kell jelezni.

## API-referencia

### `MA.dom` (dom.js)

| Függvény | Leírás |
|---|---|
| `h(tag, attrs?, ...children) → HTMLElement` | `attrs`: `class`/`className` (sztring, tömb vagy `{osztály: bool}`), `id`, `text`, `i18n` (+ `i18nArgs`), `i18nAttrs: {'aria-label': kulcs}`, `role`, `aria-*`, `data-*`, `dataset{}`, `styles{}` (CSSOM), `on{ev: fn}`, `onclick: fn` (csak függvény), `ref(el)`, `value`, `checked`, `disabled`, `hidden`, `href` (csak `#…`, `/…`, `blob:`, `data:image/`), `tabindex` … Gyerek: sztring/szám → szöveg; Node; tömb; `null`/`false` kihagyva. Tiltott: `innerHTML`, `style`, `srcdoc`, `script`/`style`/`iframe` elem. |
| `svg(tag, attrs?, ...children) → SVGElement` | createElementNS; attribútum sztringként; `<text>`-be csak szöveg; `foreignObject`/`script` tilos. |
| `text(s)`, `frag(...c)`, `clear(el)`, `mount(el, ...c)` | szövegcsomópont, fragmens, ürítés, ürítés + beillesztés |
| `$(sel, root?)`, `$$(sel, root?) → []` | querySelector(All) |
| `on(el, ev, fn, opts?) → off()` | eseménykezelő leiratkozóval |
| `uid(prefix?) → 'prefix-N'` | egyedi id (`aria-describedby`, `aria-labelledby`) |
| `focusables(root) → []`, `isTyping(target?) → bool` | fókuszálható elemek; gépelés-e (gyorsbillentyűkhöz) |
| `SVG_NS`, `safeUrl(v)` | |

### `MA.geom` (geom.js — az egyetlen `Math.*`)

| Függvény | Leírás |
|---|---|
| `linear(domain, range, {clamp}?) → scale` | `scale(v)` px; `scale.invert(px)`, `.domain`, `.range`, `.inDomain(v)`, `.kind` |
| `log10(domain, range, {clamp}?) → scale` | megjelenítési skálán (> 0) adott tartomány log10-leképezése |
| `band(n, range, padding?) → {step, bandwidth, at(i), center(i)}` | sorok (forest) |
| `snap(px, strokeWidth=1)` | éles vonal (páratlan vastagság → .5) |
| `px(v) → '12.3'` | SVG-attribútum 0,1 px-re (CSAK pixel, nem eredményszám) |
| `ticks(axis.ticks, scale) → [{px, text, at}]` | a motor tickjei pixelre; a szöveg a motoré |
| `points(polygon, sx, sy)`, `pathD(polyline, sx, sy)` | motor-poligon → `points` / `d` |
| `areaRadius(weight, maxWeight, rMax, rMin?)` | terület-arányos jelölősugár |
| `clipInterval(lo, hi, scale) → {x1, x2, clipLeft, clipRight}` | CI levágása a tartományra |
| `isNum, clamp, min, max, abs, round, floor, ceil` | pixel-segédek |

### `MA.i18n` (i18n.js)

`t(key, args?)` · `has(key, lang?)` · `pick(i18nObj|string|null, fallback='—')` (a motor `{hu,en}`
szövege) · `lang()` · `setLang('hu'|'en')` (mag.pref.lang, `<html lang>`, újrarajzolás) ·
`onChange(fn) → off` · `apply(root)` (data-i18n újrafordítás helyben) · `ts(iso) → 'ÉÉÉÉ-HH-NN ÓÓ:PP'`
(szeleteléssel) · `keys(lang?)` · `missing()` · `LANGS`.

### `MA.store`, `MA.bus`, `MA.prefs` (store.js)

- `store.get(path)`, `set(path, value)`, `update(path, fn)`, `subscribe(path, fn(value, changedPath)) → off`,
  `reset(path?)`. Az út pontozott; az ős- és leszármazott-utak feliratkozói is értesülnek.
- Keret-kulcsok (csak olvasd): `session` `{state: pending|ok|none|failed|lost}`, `engine`
  (GET /api/engine `data`), `caps` (/api/capabilities), `privacy` (/api/privacy), `project`
  (/api/project), `meta` `{engine, project_rev}`, `changes` `{rev, external_count, items[]}`,
  `connection` (`ok`|`retrying`), `ui.route` `{id, tab, params}`, `ui.theme`, `ui.ready`,
  `badges.screen.<fül>`. Saját állapot a képernyő névterében (`extraction.*`, `analysis.*` …) — csak memóriában.
- `store.createHistory({apply(op, {kind}), limit=500, onChange?}) → {push(op), undo(), redo(), canUndo(),
  canRedo(), peek(), size(), clear()}`. Művelet-alapú: a `push` egy már végrehajtott műveletet rögzít; az
  `undo` az inverzet ÚJ műveletként alkalmazza (a szerveren új, naplózott mentés). Műveletek:
  `{type:'cell', dataset, row_uid, field, before, after}`, `{type:'rows-insert'|'rows-delete', dataset, index, rows}`,
  `{type:'batch', label?, ops:[…]}`, `{type:'custom', do, undo}`. `store.invertOp(op)`.
- `bus.on(event, fn) → off`, `bus.emit(event, payload)`. Események: `route` `{id, tab, params}`,
  `lang`, `theme`, `session`, `changes` `{rev, changes[], reset}`, `refresh`, `toast`. Ajánlott
  konvenció az ábrák közti kiemeléshez: `highlight` `{row_uid|null, source}`, lefúráshoz `drilldown` `{row_uid}`.
- `prefs.get(name, fallback?)`, `prefs.set(name, value|null)` → `localStorage['mag.pref.'+name]`
  (kulcs `^[a-z][a-zA-Z0-9_.-]{0,63}$`, érték ≤ 256 karakter; pl. `lastView`, `lang`, `theme`, `forest.layers`).

### `MA.api` (api.js)

- `request(method, path, opts?) → Promise<env>`; `get(path, opts?)`, `post(path, body, opts?)`,
  `put(path, body, opts?)`. `path` csak `/api/…`. `opts`: `query{}`, `body`, `signal`, `ifMatch`,
  `headers{}`, `toast` (alap `true`), `quiet: [kódok]`, `seq: true` (→ `client_seq` a törzsbe),
  `clientSeq: n`, `timeoutMs` (alap 35 000).
  Siker: a boríték `{ok:true, schema, data, warnings, meta}` + `env.etag` (ETag vagy null) + `env.client_seq`.
  Hiba: `ApiError {code, http, message, details, envelope}`; toast magyar címmel (`err.<KÓD>`) és a szerver
  üzenetével szó szerint (`GATE_BLOCKED` → a blockerek listája). Kódok: a 3.4 táblája + `METHOD_NOT_ALLOWED`,
  kliensoldali `NETWORK`, `BAD_RESPONSE`, `TIMEOUT`, `ABORTED` (néma). Minden kérés: `X-MA-Token`,
  `Accept: application/json`, POST/PUT-nál `Content-Type: application/json` (üres törzs → `{}`),
  `X-MA-Client-Seq`.
- `latest() → run(fn(seq, signal))` — „legutolsó nyer”: a felülírt hívás `null`-lal old fel, az előző
  kérés megszakad. Élő validáláshoz: `MA.ui.debounce(fn, 250)` + `latest()` + `{clientSeq: seq}`.
- `list(env, key?) → []` (data, data[key] vagy data.items) · `nextSeq()` · `lastMeta()` ·
  `openFile({doc, page?, path?})` (POST /api/fileurl → `{url: '/f/…'}`, új lapon `#page=N`).
- `session.init()`, `session.token()`, `session.clear()`, `session.TOKEN_KEY` (a keret hívja).
- `changes.start(rev?)`, `stop()`, `ack()`, `running()`, `rev()` — long-poll `GET /api/changes?since=`;
  a válasz `{rev, changed:[út], external:[út], reset}` (ma_gui/store.py ChangeWatcher) vagy
  `{rev, changes:[{path, actor}]}`; hibánál 1 → 30 s visszalépés.

### `MA.ui` (ui.js)

`badge(kind, text, {symbol, title, srLabel}?)` — kind: `error warning info ok stale blocker estimated
progress pending neutral` (+ `cap_ok cap_unusable cap_legacy cap_absent` szimbólumok) · `num(i18nObj, attrs?)`
· `numText(i18nObj)` · `toast({kind, title, message?, details?, timeout?, code?, actions?: [{label, onClick}]}) →
{el, close}` · `modal({title, body, actions?: [{label, kind?, value?, onClick?(close) → false = nyitva}], onClose?,
size?: sm|md|lg}) → {el, body, close}` (role=dialog, fókuszcsapda, Esc, inert háttér) ·
`confirm({title, message, okLabel?, cancelLabel?, danger?}) → Promise<bool>` · `menu({label, items:
[{label, onSelect?|href?}], buttonAttrs?})` · `tabs({label, tabs:[{id,label}], active, onSelect(id), panelId?})`
(role=tablist, nyilak, `.select(id)`) · `debounce(fn, ms)` (`.cancel()`, `.flush()`) ·
`copyButton(getText, label?)` · `emptyState(key, args?)` · `spinner(key?)` · `errorBox(err)` · `symbol(kind)`.

### `MA.app` (app.js)

`registerScreen(def)` (fent) · `navigate(id, params?, {replace}?)` · `href(id, params?) → '#/id?…'` ·
`parseHash(hash?)` · `current() → {id, tab, params}` · `refresh()` (keretadatok + ⟳, onLeave-őrrel) ·
`loadBase()` · `setTabBadges(tabId, [{kind, text, title?}] | null)` · `setTheme('light'|'dark'|'auto')` ·
`toggleTheme()` · `screens()` · `TABS` · `WORKSPACES` · `start()`.

A `render(root, ctx)` `ctx`-je: `id`, `params`, `root`, `signal` (elhagyáskor megszakad), `alive()`,
`onCleanup(fn)`, `setTitle(utótag)`, `setParams(params)` (replaceState, újrarajzolás nélkül),
`navigate`, `href`, `rerender()`, valamint `store`, `api`, `i18n`, `dom`, `ui`, `geom`.
Nyelvváltáskor a képernyő újrarajzolódik (`onLeave` nélkül), kivéve ha `onLangChange(ctx)`-et ad.

Fülek (3.5.0) → munkaterület: `overview`, `protocol` (1), `prisma` (2), `log` (7), `export` (8) →
`process`; `extraction` (3) → `extraction`; `analysis` (4) → `analysis`; `appraisal` (5), `grade` (6) →
`appraisal`. Fül nélküli helyőrzők: `project` (3.5.2), `capabilities` (3.5.16) — felülírhatók.
Billentyűzet: Alt+0…8 fül, ←/→/Home/End a navigációban, `?` súgó, Esc modális.

### `MA.selftest` (selftest.js)

`register(name, fn(t))` — `t.ok(c, msg)`, `t.eq(a, b, msg)`, `t.deepEq`, `t.near(a, b, tol, msg)`,
`t.throws(fn, msg)`; Promise-t is adhat · `waitFor(pred, ms?) → Promise<bool>` · `run({show}) →
{pass, fail, results}` · `result()`. Kimenet: `<pre id="selftest">` (`SELFTEST pass=N fail=M`) és
`<html data-selftest="pass|fail">`.

### `MA.dev.fixtures` (csak --dev)

`active`, `TOKEN`, `calls[]` (`{method, path, query, body, headers}`), `routes`, `route(method,
pattern, handler(req) → {status?, envelope, etag?, delay_ms?} | Promise)`, `reset()`.
Fixture-fájl: `{"description": "…", "routes": [{"method": "GET", "path": "/api/runs/*/plot",
"query": {…}?, "body": {…}?, "status": 200, "etag": "…"?, "delay_ms": 0?, "envelope": {…}}]}` vagy
`"sequence": [{status, envelope, delay_ms}, …]` (sorban, az utolsó ismétlődik). A legspecifikusabb
(több query/body-feltétel) útvonal nyer. A build ellenőrzi a boríték alakját (4.2).

## Feltevések a szerver felé (egyeztetendő)

- `POST /api/session` törzse `{"launch_code": "<kód>"}`, válasza `data.token` (api.js `SESSION_BODY_KEY`).
- `GET /api/project` `data`: `szk.ma.project/v1` + opcionális `badges: {fül: [{kind, text}]}`, `user.initials`.
- `GET /api/privacy` `data`: `privacy.status()` kimenete (opcionálisan `status`).
- `GET /api/capabilities` `data.components[]`: `{id, label, version, state: ok|unusable|legacy|absent, mode, problems[i18n], …}`.
- `GET /api/engine` `data`: `engine_version`, `selftest {ok, checks, passed}`, `measures[]`, `options{}`, `rules[]`.
- `GET /api/log/<kind>` `data.items[]`; `GET /api/runs?primary=1` `data.runs[]` (`szk.ma.run/v1` + `outcome_id`,
  `stale`, `participants_text`, `rob_high`, `grade.certainty`, `primary.i2_text`).
- A HTML-ben a `{{CSP_NONCE}}` helyeket válaszonként a szerver cseréli.

## KIEGÉSZÍTÉS (folyamat-ágens, 2026-10-05) — Folyamat és audit képernyők, „Miért?” komponens

Fájlok: `src/components/{proc,why}.js`, `src/screens/{overview,prisma,studies,log,capabilities,export}.js`,
`src/css/process.css`, `src/i18n/{hu,en}/process.json`, `src/dev/process_backend.js` (csak dev),
`fixtures/{prisma,studies,kb_process,export,capabilities,log_*}.json` (generálja: `python3 tests/gui/ui/gen_process_fixtures.py`,
a valódi motorból/KB-ból), tesztek: `node tests/gui/ui/process.spec.js`, `python3 tests/gui/ui/test_process_fixtures.py`.

### `MA.why` — „Miért?” magyarázó (11. fejezet 6. döntés; más képernyők is használják)

| Függvény | Leírás |
|---|---|
| `button(spec, {label?, compact?, id?}) → <button>` | „? Miért?” gomb (`aria-haspopup=dialog`, `aria-expanded`); kattintásra/Enterre buborék |
| `open(spec, anchor?) → {el, close}` | nem modális buborék (`role=dialog`, `aria-modal=false`); Esc / kattintás kívül / fókusz elhagyása zárja, a fókusz visszakerül |
| `panel(spec) → <div>` | ugyanez beágyazva (a KB-rész aszinkron töltődik) |
| `item(id) → Promise<item\|null>` | `GET /api/kb/item/<id>` gyorsítótárral (404 → `null`) |
| `openItem(id)`, `kbButton(id)` | a KB-tétel minden mezője modálisban („helyi forrás — nem exportálható” a szövegrésznél); „ⓚ ID” gomb |

`spec = {kb?: id | [id…] | "V011 D-S07-004", code?, title?, detail?, advice?, source?, plain?: {asks, because, change,
uncertain}, evidence?: {quote, page, locator}}` — a motor megállapításának mezői (`title/detail/advice/source`) és a
KB-tétel (`condition/recommendation/rationale/source_ids/locator`) egyszerű nyelvű szakaszokba kerülnek; az AI-vázlat
indoklása (`plain`) és bizonyítéka (`evidence`) is megjeleníthető. Példa: `MA.why.button({kb: f.code, code: f.code,
title: f.title, detail: f.detail, advice: f.advice}, {compact: true})`.

`MA.proc`: `STAGES` (S00–S14, FINAL), `SEVERITIES`, `AGENTS`, `sevKind`, `refs`, `section/fill/load`, `select`, `field`, `pend`.

### Feltevések a szerver felé (egyeztetendő; a fixture-ök ezt az alakot követik)

- `GET /api/prisma[?refresh=1]` → `{mode: manual|composer, path, source{…}, override, flow (szk.prisma-flow/v1), check (motor
  prisma_check: template, ok, summary, findings[], derived{}), studies{path, studies, reports}, meta[{outcome_id, k, run_id}],
  cross[X014/X015], composer_status[]}` + ETag. `PUT /api/prisma/manual` `{dry_run: true, flow}` → előnézet (mentés nélkül,
  250 ms-os élő ellenőrzés); `{flow, override_reason?}` + If-Match → mentés. A dobozértékek **nyers szövegként** mennek
  (üres → null), az okok `{ok: nyers szám}` alakban — a motor parszol (P001); a felület dobozértéket nem számol.
- `GET/PUT /api/studies` → `szk.ma.studies/v1` + `summary{studies, reports}` (I és J a szervertől) + `problems[]`, If-Match, 409/422.
- `GET /api/log/activity?limit=` (ma_gui/routes/log.py), `GET /api/audit/project` (`szk.ma.project-audit/v1`).
- `POST /api/export/audit|snapshot` `{include{decisions, specs_runs, provenance, appraisals, prisma, figures, activity, rerun,
  data_tables?}, redact{assessors, quotes, abs_paths}}` → `{path, sha256, bytes, manifest{files[], redactions[], excluded[], activity_head}}`.
- `GET /api/capabilities` a `ma_gui.caps.Caps.report()` valódi alakja (components[] + matrix + problems).
