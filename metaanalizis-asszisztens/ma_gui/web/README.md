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
  dist/index.html          termék-build; a szerver közvetlenül ezt szolgálja ki (ma_gui/static/index.html csak tartalék)
  dist/index.dev.html      fejlesztői build (gitignore-olt)
  eslint.config.js         minimális ESLint (flat config): böngésző-globálisok, no-undef, no-unused-vars, eqeqeq
```

## Build és tesztek

```
python3 ma_gui/web/build_gui.py           # dist/index.html  (termék, ≤ 900 KB, determinisztikus) + dist/snapshot.html
python3 ma_gui/web/build_gui.py --dev     # dist/index.dev.html (+ fixture-ök; megnyitás: ?fixtures=1)
python3 ma_gui/web/build_gui.py --snapshot  # csak a dist/snapshot.html (a pillanatkép-sablon, lásd lent)
python3 ma_gui/web/build_gui.py --check   # 1-es kód, ha a dist/index.html vagy a dist/snapshot.html nem naprakész
python3 ma_gui/web/build_gui.py --list    # modulsorrend
python3 tests/gui/test_ui_static.py       # a termék-dist és a források statikus ellenőrzése (böngésző nélkül)
python3 tests/gui/ui/test_build_gui.py    # build-, lint- és szabálysértés-tesztek
python3 tests/gui/ui/test_minify.py       # a tömörítés veszteségmentes (espree-AST összevetés)
node /opt/node22/lib/node_modules/eslint/bin/eslint.js -c ma_gui/web/eslint.config.js ma_gui/web/src
node tests/gui/ui/run_all.js [--python]   # az összes *.spec.js egymás után (+ a Python-tesztek); 1-es kód hibánál
node tests/gui/ui/a11y.spec.js            # akadálymentesség: csak billentyűzettel minden MVP-képernyőn, kontraszt két témában
node tests/gui/ui/snapshot.spec.js        # pillanatkép: nulla hálózati kérés, nulla CSP-sértés, író gomb → parancs
```

**Termék-tömörítés (≤ 900 KB, terv 2.3; v1-ben emelve 600-ról, korábban 450 KB).** A termék-build a lint UTÁN veszteségmentesen tömörít
(`build_gui.py` vége): `minify_js` tokenszinten elhagyja a megjegyzéseket és a fölös szóközöket (a
sortörés ott marad, ahol az ASI számít), az idézőjeles azonosító-kulcsot idézőjel nélkül írja, és a
névtelen függvénykifejezést nyílfüggvényre cseréli, ahol a jelentés biztosan azonos (nincs `this` /
`arguments` / `new.target` a törzsben, és a modulban csak beépített konstruktor áll `new` után); nevet
nem cserél. `minify_css` a megjegyzéseket és a fölös szóközöket hagyja el. Az i18n mindkét buildben
`<script type="text/plain" id="ma-i18n" data-enc="lz1">`: a két szótár egy fában (`{"a":{"b":["hu","en"]}}`),
szöveges LZ77-tel (kicsomagolás: `i18n.js`, Python-pár: `build_gui.unpack_i18n`). A `--dev` build a JS-t
és a CSS-t olvasható alakban hagyja. A `tests/gui/ui/test_minify.py` minden termék-modulra ellenőrzi, hogy
az AST tömörítés előtt és után azonos (`minify_check.js`). Ha a build mégis túllépi a keretet, a hiba
üzenete megmondja; a keret emelése tervdöntés (2.3), nem a build dolga.

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

## Szerver-alakok (megerősítve és megvalósítva, 2026-10-05)

A `dist/index.html`-t a szerver közvetlenül szolgálja ki (`GET /`, a `{{CSP_NONCE}}` helyeket válaszonként cseréli);
a `ma_gui/static/index.html` csak tartalék oldal újraépítési útmutatással, ha a `dist/` hiányzik. CSP-sértés és
JS-kivétel nincs, az oldalon belüli önteszt (`?selftest=1`) zöld. A végponttól végpontig tartó elfogadási teszt
(`node tests/gui/ui/e2e.spec.js`) a valódi szerverrel fut; a v1 elfogadása (terv 9.3) `node tests/gui/ui/e2e_v1.spec.js`
(projektek és szerver: `tests/gui/ui/e2e_v1_server.py`, X-szabály-projekt: `e2e_v1_xrules.py`; részenként:
`E2E_V1_ONLY=appraisal,grade,dual,plots,figures,composer,hh,xrules,final,replay`; a valódi pluginok `MA_GUI_PLUGIN_DIRS`,
a figure-forge-hoz `MA_GUI_TEST_FF_PYTHON`; a Metaheadhunter kazettái `tests/reference/headhunter/cassettes/gui_e2e/`,
újrafelvétel élő hálózattal: `E2E_V1_HH_RECORD=1`).

- `POST /api/session` törzse `{"launch_code": "<kód>"}`, válasza `data.token` (api.js `SESSION_BODY_KEY`).
- `GET /api/project` `data`: `szk.ma.project/v1` + opcionális `badges: {fül: [{kind, text}]}`, `user.initials`.
- `GET /api/privacy` `data`: `privacy.status()` kimenete (opcionálisan `status`).
- `GET /api/capabilities` `data.components[]`: `{id, label, version, state: ok|unusable|legacy|absent, mode, problems[i18n], …}`.
- `GET /api/engine` `data`: `engine_version`, `selftest {ok, checks, passed}`, `measures[]`, `options{}` (JSON-séma
  típusnevek: `boolean`, `array`, `integer`, `number`, `enum`, `string`, `object`; `nullable`, `required`), `rules[]`.
- `GET /api/table` — a `column_map` (kanonikus név → eredeti fejléc, = `api.column_map`) is benne van.
- `POST /api/validate` → `szk.ma.validation/v1`; minden megállapítás `acknowledged` mezőt kap (az aktív „Nem hiba —
  indoklás” döntés azonosítója vagy null; illeszkedés: code + row_uid + fields + dataset).
- `POST /api/convert` → `szk.ma.convert-result/v1`; az `assumptions` / `warnings` `{hu, en}` objektumok, a cellába
  csak a `cell_text` kerül (a cél tábla tizedesjelével).
- `POST /api/analyze` `{mode, spec, table?, client_seq, lane?}` → a `jobs.py` feladat-pillanatképe; `result = {schema,
  run, plot, results, validation_summary, findings, excluded, filter_report, warnings, command}`, commitnál a
  `run.outcome_id`-vel. A commit 409 (`stale_data`: az adat sha256-ja eltér; `spec_differs`: a mentett spec más),
  403 (`_privat/` alatti adattábla), 400 (piszkozat-tábla). `GET /api/jobs/<id>` a feladat-pillanatkép.
- `GET/PUT /api/specs/<név>` (és `GET /api/specs` lista): a törzs és a `data` maga a spec, ETag/If-Match; azonos
  tartalmú ismételt PUT If-Match nélkül no-op, eltérő 409.
- `GET /api/runs?outcome=&primary=1` → `{runs: [szk.ma.run/v1 + outcome_id, stale, measure, spec.purpose, dir,
  data_current_sha256]}`; `primary=1` mellett a `primary.i2_text` / `primary.pi_text` (a motor szövegei) és a
  projektnapló legutóbbi GRADE-ítélete (`grade: {certainty, id}`). `GET /api/runs/<id>` → `downloads: {szerep:
  {path, url, expires_at}}` (a szerepek a `run.json` `files` kulcsai: `forest`, `funnel`, `doi`, `plot`, `results`,
  `effect_sizes`, `report`); `/results`, `/plot`, `/report` a futás fájljai.
- `GET /api/prisma` → `{mode, path, source, override{reason, decision_id}, flow, check, studies{path, studies?,
  reports?}, meta[{outcome_id, name, k, run_id, stale}], cross[X014/X015/X020/X021], composer_status[]}`;
  `PUT /api/prisma/manual` `{flow, dry_run?, override_reason?, client_seq?}`.
- `GET/PUT /api/studies` → `{schema, path, studies, summary{studies?, reports?}, problems}`.
- `GET /api/log/<kind>` `data.items[]`; a `POST /api/log/decision` `context`-je csak ezeket a kulcsokat engedi:
  `{kind, dataset, row_uid, code, fields, run_id, spec, outcome, changes[{key, before, after}]}`.
- `GATE_BLOCKED` (409) részletei `audit_errors`-t is tartalmazhatnak (a FINAL PASS alapból az audit-kapuval fut).
- `GET /api/audit/project` → `szk.ma.project-audit/v1`; `GET /api/kb/rules?field=` → `api.kb_rules_for_field`.
- `POST /api/export/audit|snapshot` → `{path, sha256, manifest, url}` (aláírt, 10 perces letöltés; a pillanatképhez
  `ack: true` kell).
- **Számok:** minden kiírt szám a motor kész szövege (`display_text`, `*_text`, `ticks[].text`); a 4.0 konvenció
  szerint a kijelzési szövegek tizedesponttal készülnek mindkét nyelven (a report.md-vel és a motor-SVG-vel azonos);
  tizedesvessző csak a táblacellába írt `cell_text`-ben van.

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

### Szerver-alakok (megerősítve; a fixture-ök ezt az alakot követik)

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

## KIEGÉSZÍTÉS (pillanatkép, 2026-10-05) — csak olvasható, kitakaró HTML-pillanatkép (terv 2.5, 3.5.17, 7.6)

**Mi ez.** `python ma.py gui snapshot --project <mappa> --out x.html [--redact …] [--keep …]` (belépési pont:
`ma_gui.snapshot.main`; a felületről: Export → Pillanatkép) egyetlen HTML-fájlt ad a társszerzőknek: a termék-felület
+ a projekt állapota beágyazott JSON-ként. Python, szerver és hálózat nélkül nyitható (`file://`); minden képernyő
ugyanúgy fut, mint élőben, csak semmi sem menthető.

**Build-változat.** A `build_gui.py` a termék-build mellé `dist/snapshot.html`-t is ír (a `snapshot.py` ezt tölti ki):
- a `src/snapshot/*.js` modulok (az `api.js` után) és a `/*<snapshot>*/ … /*</snapshot>*/` blokkok **csak** ebbe kerülnek
  (a termék- és a dev-buildből kimaradnak; a build ellenőrzi);
- nonce helyett `<meta http-equiv="Content-Security-Policy" content="{{SNAPSHOT_CSP}}">` — a `snapshot.py` a kész tartalom
  sha256-hash-eivel tölti ki (`script-src 'sha256-…'; style-src 'sha256-…'; connect-src 'none'` …);
- `<script type="application/json" id="ma-snapshot">{{SNAPSHOT_DATA}}</script>` a futó szkript előtt;
- mérete legfeljebb `SNAPSHOT_MAX_BYTES` (900 KB, adat nélkül); a termék-build kerete 900 KB (`MAX_BYTES`), így a
  termék nem nőhet a pillanatkép-kliens (≈ 11 KB) helye fölé.

**`src/snapshot/provider.js`** — a hálózati réteg helyett (`MA.__snapSetTransport`, csak a pillanatkép-buildben) a
beágyazott borítékokból válaszol:
- GET: pontos kulcs (`'GET /api/runs?outcome=o1'`; a lekérdezés rendezett, RFC 3986 szerint kódolt — a Python
  `snapshot.query_string` párja), különben a lekérdezés nélküli kulcs + egyszerű szűrés a `data.items`-en (`status`,
  `severity`, `stage`/`stage_id`, `agent`; `limit`); `/api/kb/search` csak a beágyazott KB-tételekben keres; ami nincs
  benne: `NOT_FOUND` „Ez az adat nincs a pillanatképben.”;
- író kérés: párbeszédablak a **pontos paranccsal** (Windows: `py -3 ma.py …`, macOS/Linux: `python3 ma.py …`; a
  platformé rögtön a vágólapra kerül), a kérés `READ_ONLY` hibával zárul (toast nélkül). A parancs-sablonokat a
  `snapshot.command_templates()` adja (`"{mező|alap}"`, lista = opcionális csoport); a teszt a motor argparse-ával
  ellenőrzi őket;
- olvasó POST (élő validálás, explore, átváltó, fájl-URL): beágyazott eredmény vagy csendes `READ_ONLY`;
- munkamenet, token, long-poll nincs; `MA.api.openFile` → tájékoztató toast (PDF/fájl nincs benne);
- `html[data-mode=snapshot]`, sáv a fejléc alatt (`#snapshot-banner`: projektállapot ideje, állapot-azonosító,
  adatosztály, „Mi van benne?” → `MA.snapshot.showInfo()`: kitakarások, kizárt minták, futások, validálás-összegzés).

**Kitakarás** (alapérték az adatosztályból, `snapshot.policy_defaults`): C — adattábla soha (kérésre sem: 403 / 2-es
kilépési kód); B — adattábla alapból ki, értékelők monogrammal; mindig: `_privat/`, PDF (csak doc-id + oldal + sha256), a
KB teljes szövege soha; az eredet-idézetek és az abszolút utak alapból ki. Adattábla nélkül az ábra-adat nyers
cellaoszlopai (`studies[].cells`), az eredet bevitt értékei és a validálás soronkénti üzenetei is kimaradnak. A
beágyazott `manifest` rögzíti mindezt. Ugyanaz a projektállapot ugyanazt a bájtsort adja.

**Export-végpontok** (`ma_gui/routes/export.py`): `POST /api/export/audit` és `POST /api/export/snapshot` (`ack: true`
kötelező) → `{kind, path, name, sha256, bytes, …, url, expires_at}`; az `url` aláírt, 10 perces letöltési cím
(`/f/x/…`, mindig csatolmányként, sandbox CSP-vel). A felület az eredmény alatt „Letöltés” gombot mutat.

## KIEGÉSZÍTÉS (értékelés-ágens, v1, 2026-10-05) — RoB-család, konszenzus, forgalmi lámpa, PROBAST+AI, TRIPOD+AI, AMSTAR 2

Fájlok: `src/components/appraisal_{form,panel}.js`, `src/plots/appraisal_traffic.js`,
`src/screens/appraisal{,_consensus,_summary,_probast,_tripod,_amstar2}.js`, `src/css/appraisal.css`,
`src/i18n/{hu,en}/appraisal.json`, `src/dev/appraisal_backend.js` (csak dev; SZIMULÁLT ellenőrzés — nem a motor),
`fixtures/appraisal_*.json` (generálja: `python3 tests/gui/ui/gen_appraisal_fixtures.py [--check]`), szerver:
`ma_gui/routes/appraisal{,_common,_rob}.py`; tesztek: `tests/gui/test_v1_appraisal{,_drift}.py`, `node tests/gui/ui/appraisal.spec.js`.

Képernyők: `appraisal` (5 Torzítás: RoB 2 / ROBINS-I/E / QUADAS-2 / NOS / QUIPS / JBI; `?tool=&target=&unit=&rater=`, álnevek:
`?outcome=` → cél, `?study=` → egység), `appraisal-consensus`, `appraisal-summary` (forgalmi lámpa + rob-oszlop szinkron),
`appraisal-probast`, `appraisal-tripod` (`?mode=studies|manuscript&filter=both|D|E`) és `appraisal-amstar2` (a 6 GRADE/SoF fül alatt).

| Modul | API |
|---|---|
| `MA.appr` | `instrument(tool)`, `instruments()` (gyorsítótárral), `form(o) → {el, update(check), focusKey(key), refreshWhy()}`, `verdictBadge(inst, v, {implied})` (●◐○⊗ + szöveg; implikált = szaggatott keret), `algLabel(inst, alg)` / `algText(alg)` (a motor rollup.label-je, ennek hiányában általános címke — az implikált ítélet SOHA nem „hivatalos eredmény”), `itemsFor(inst, scope, pass)`, `scopes/scopeLabel`, `ans/setAns/judg/setJudg`, `rater()/raterField()` (`mag.pref.appraisal.rater` — csak monogram), `aiBanner`, `completeness(check, pass?)`, `missingList`, `download`, `pickJson`, `isRobFamily`, `domainShort` |
| `MA.apprPanel` | `open(host, {tool, unit, target, rater, myRater, inst, passes?, applicability?, judgements?, overall?, extra?(view, check, doc), onSaved?, onRater?, consensusHref?}) → {dirty(), reload(), save(status?), view(), doc(), check()}`: betöltés, élő `POST …/check` (400 ms, legutolsó nyer), mentés If-Match-csel (422-nél a szerver üzenete + fókusz a hiányzó X017-indoklásra, 409-nél újratöltés), AI-vázlat jóváhagyása; `importBody/importFile/inbox/exportAll/exchangeBar/sourceLine` (5. döntés: fájlcsere) |
| `MA.plots.robTraffic` | `matrix(summary, inst)`, `weighted(summary, inst)` — a motor `szk.rob-summary/v1`-éből; a súly és a százalék a motor kész szövege, a szélesség csak `MA.geom.linear` pixel-leképezés |

Szerver-alakok: `GET /api/instruments` → `{instruments[], engine{available, missing[]}, validator{state, version, mode}}`;
`GET /api/instruments/<tool>` → `szk.instrument/v1`; `GET /api/appraisals?tool=&unit=&target=&answers=1` → `{items[{unit, tool,
target, rater, path, etag, human, origin, status, domain_judgements[], overall, check{complete, completeness_text, per_pass,
domains[{domain, implied, algorithm}], overall{implied}, overrides_missing}, answers?}], studies[], outcomes[], engine}`;
`GET|PUT /api/appraisals/<unit>/<tool>?rater=&target=` → `{doc, check, raters[], human_raters[], path, exists, study}` + ETag;
`POST …/check` `{doc}` → `{check, problems}`; `POST …/approve` `{approver, note?, confirm}` + If-Match;
`GET /api/appraisals/consensus/<unit>/<tool>?target=&a=&b=` → `{ready, a{rater, doc, check}, b, agreement (motor), consensus{exists,
doc, etag}, excluded[AI-vázlatok]}`; `GET /api/appraisals/rob-summary?tool=&outcome=`; `POST /api/appraisals/rob-sync`
`{tool, outcome|dataset, dry_run}` (+ If-Match a tábla ETag-jével); `GET /api/appraisals/inbox`; `POST /api/appraisals/import`
`{doc | path, replace?}`; `POST /api/appraisals/export` `{unit, tool, target?, rater} | {all: true, rater?, tool?}`.
A motor hiányzó értékelő függvényénél 424 `CAPABILITY_MISSING` (`details.engine_functions`).

## KIEGÉSZÍTÉS (GRADE-ágens, v1, 2026-10-05) — GRADE / SoF (3.5.12) és Protokoll („1 Protokoll”)

Fájlok: `src/screens/grade.js` (`grade` képernyő a 6-os fülön), `src/screens/grade_protocol.js` (`protocol` képernyő az
1-es fülön), `src/css/grade.css`, `src/i18n/{hu,en}/grade.json` (`grade.*`, `sof.*`, `protocol.*`), `src/dev/grade_backend.js`
(csak dev; `MA.dev.grade.{state(), engineMissing(on), bumpEtag('grade'|'protocol', kimenet?)}`), `fixtures/grade.json`
(generálja: `python3 tests/gui/ui/gen_grade_fixtures.py` — a tanács és a SoF a VALÓDI motor `grade_help` kimenete; `--check`
sodródás-őr: `tests/gui/ui/test_grade_fixtures.py`). Tesztek: `node tests/gui/ui/grade.spec.js` (dev-fixture + valódi szerver,
`tests/gui/ui/grade_server.py [--stub]`), `python3 -m unittest tests/gui/test_v1_grade_routes.py`.

Szerver-alakok (`ma_gui/routes/grade*.py`; a motor-függvényeket a `grade_engine.py` keresi név szerint, hiányuknál 424):
- `GET /api/grade/<kimenet>[?run=]` → `szk.ma.grade-view/v1` + ETag (tartalom-ETag): `{outcome, run (rövid futás-leírás,
  stale), grade (szk.ma.grade/v1 | null), journal, unresolved[], missing[], run_matches, conventions, vocab{domains, ratings,
  upgrades, certainties, starts}, engine{…}}`; 424-nél a `details.view` ugyanez `grade: null`-lal.
- `PUT /api/grade/<kimenet>` ← `{grade, record?, certainty?}` + If-Match; 422 (indoklás nélküli ítélet / felminősítés, „gyanított”
  −2-vel), 409 `GATE_BLOCKED` (`details.code`: X019 feloldatlan publikációs torzítás, X007 nem a legutóbbi elsődleges futás,
  X001 elavult futás; `missing`), 409 `CONFLICT`. A válasz a nézet + `recorded{id, certainty, warnings}`.
- `GET /api/grade/<kimenet>/advice[?run=&mid=]` → `{run, mid, advice}` — az `advice` a motor `grade_help.advice` piszkozata
  (doménenként `suggestion{rating, step, status, concern, summary, evidence[], flags[], why{asks, because, change,
  uncertain}, kb_refs}`; `advice.upgrades.<szempont>`, `advice.notes`). A felület a mentéskor a futáshoz tartozó
  `run_summary`-t és a doménenkénti `suggestion`/`advisory`-t is visszaküldi (a motor rögzítése és figyelmeztetései ebből dolgoznak).
- `GET/PUT /api/sof/<kimenet>` (`{assumed_risks[{label, source: control_pool|external, per_1000 (nyers szöveg)}], footnotes[],
  dry_run?}`) → `szk.ma.sof-view/v1` (`preview`: a motor `sof()`-ja; oszlopok a `columns`-ból, cellánként a motor kész
  szövege, a forrásmező a `sources`-ból); `POST /api/sof/<kimenet>/export` `{format: csv|md, lang}` → `{path, url (aláírt),
  content?}` (Excel-biztos CSV: `'` előtag, BOM, CRLF; hu `;`, en `,`).
- AMSTAR 2 önellenőrzés: EGY végpont, az értékelésé (`GET/PUT /api/appraisals/review/amstar2?rater=`,
  `review.amstar2.<értékelő>.json`); a motor besorolása mindkét konvencióval a `check.amstar2`-ben (`rating`,
  `alternative`, `differs`); eltérő összítélethez `override_reason` (X017). A korábbi párhuzamos `GET/PUT /api/amstar2`
  a v1 integrációban megszűnt (egy fájl-elrendezés, egy út).
- `GET/PUT /api/protocol` (`{title?, question{P,I,C,O}?, review_type?, registration{registry, id}|null}` + If-Match) — a
  ma-projekt.json protokoll-mezői; a kimenetek a `POST /api/project {action: 'outcome', replace?}`, az adatosztály a
  `{action: 'data_class'}`, az előre rögzített jelölés a `PUT /api/specs/<név>` útján megy.

## KIEGÉSZÍTÉS (kinyerés-ágens, v1, 2026-10-05) — kettős kinyerés (3.5.5), rács-virtualizáció, kumulatív és buborékábra

Fájlok: `src/screens/extraction_dual.js` (`dual` képernyő a 3 Kinyerés fül alatt, `#/dual?outcome=&view=items|tables&item=`),
`src/components/extraction_vtable.js` (`MA.vtable`), `src/plots/{cumulative,bubble}.js`, `src/css/extraction_dual.css`,
`src/i18n/{hu,en}/extraction_{dual,plots}.json`, `src/dev/extraction_dual_backend.js` (csak dev; `MA.dev.dual.{state(),
engineMissing(bool), bumpEtag(kimenet), reset()}` — a döntések hozzárendelését szimulálja, az összevetés a fixture-é),
`fixtures/extraction_{dual,plots}.json` (generálja: `python3 tests/gui/ui/gen_extraction_fixtures.py [--check]` — a munkapad
VALÓDI szerverével; az összevetés a homlokzat `compare`-je, ennek hiányában a motor `metaelemzes/kettos.py` modulja, végső
tartalékként a teszt-csonk). Szerver: `ma_gui/routes/extraction_dual{,_common}.py`. Tesztek: `tests/gui/test_v1_extraction_dual.py`,
`tests/gui/ui/test_extraction_dual_fixtures.py`, `node tests/gui/ui/extraction_dual.spec.js` (dev-fixture + valódi szerver a
termék-builddel: `tests/gui/ui/extraction_dual_server.py [--stub|--no-engine]`).

| Modul | API |
|---|---|
| `MA.vtable` | `create({label, columns: [{id, label, header?, cls?, title?}], rows, key(row), cell(row, col, i) → Node \| szöveg \| {node\|text, cls?, title?}, rowClass?, rowLabel?, onSelect?, onActivate?, onKey?(ev, row, i) → true, virtualMin? (1000), emptyKey?}) → {el, table, rows(), setRows(list, keep?), select(key, {focus?, silent?}), selected(), selectedIndex(), refresh(), focus(), active(), virtual(), renderedCount(), row(i)}` — csak olvasható WAI-ARIA grid (roving tabindex a cellákon, aria-rowcount/-rowindex a teljes listára, aria-selected), 1000 sor fölött ablakos kirajzolással |
| `MA.grid` (bővítés) | 1000 sor (`VIRTUAL_MIN`) fölött ablakos kirajzolás: a modell teljes, a DOM-ban csak a görgetési ablak (+15 sor ráhagyás) és két térkitöltő sor; a billentyűzet a célsort előbb az ablakba görgeti; a kigörgetett aktív cella helyett a rács (`table`) kap fókuszt, a következő billentyű visszagörget; a dekorációk a kirajzolt sorokra kerülnek. `MAX_ROWS` = 5000 (= `security.MAX_ROWS`). Új: `virtual()`, `renderedCount()`, `opts.virtualMin`. Az API többi része változatlan. |
| `MA.plots.cumulative` | `render(host, plot, {onDrill, signal})` — a `plot.cumulative` (a motor tengelye, becslései, `display_text`/`i2_text`/`tau2_text`/`key_text`, `k`) kumulatív forestként; a végső sor gyémánt, a referencia-vonal a végső becslés; sorok `.cu-row.sr-row[data-uid][data-y][data-lo][data-hi][data-k]` |
| `MA.plots.bubble` | `render(host, plot, opts)`, `layout(plot)`, `available(plot)`, `bandPolygon(bubble)` — a `plot.bubble` (lent) buborék-/pontdiagramként; a sáv a motor `band` rácsából (felső él előre, alsó vissza) vagy kész `band_polygon`-ból, az egyenes a motor `line` pontjaiból; buborék-terület ∝ `weight_pct` |

A `screens/results.js` (4 Elemzés · Eredmények) a „Kumulatív” fület a `MA.plots.cumulative`-vel rajzolja (tartalék: `MA.plots.series`),
és új „Buborék” fület kap, ha a futás plot-dokumentumában van `bubble` blokk.

**Szerver-alakok** (`ma_gui/routes/extraction_dual.py`; a motor-függvényeket az `extraction_dual_common.ENGINE` keresi név
szerint — `compare` | `kettos_compare` …, `consensus_table`, `agreement_report` —, hiányuknál 424 `CAPABILITY_MISSING`):
- `GET /api/kettos` → `szk.ma.dual-list/v1`: `{outcomes[{id, name, measure, data, dir, a|b|csv: {path, exists, etag, bytes}, consensus{path,
  exists, etag, decisions, raters, csv}}], inbox[{path, name, bytes, side_guess, outcome_guess}], engine{available, functions, missing},
  default_dir}`. A fájlok helye a kimenet adattáblája melletti `kettos/` mappa (`_privat/o1.csv` → `_privat/kettos/`).
- `POST /api/compare` `{outcome, key?, tolerance?, tables?}` → `szk.ma.dual-view/v1` + ETag (a konszenzus-fájlé): `{outcome, dir, key,
  key_candidates, columns[{field, a, b}], files, raters, compare (szk.ma.compare-result/v1, változatlanul), pairs, pairs_source,
  items[{id, level: cell|row, key, field ('*' = egész sor), kind, a, b, row_uid_a, row_uid_b, hint, hint_code, kb, impact, auto,
  needs_decision, decision{chosen, value, reason, actor, ts}|null, stale}], progress{total, decided, unresolved, auto, stale, orphans},
  gate{code: 'X009', blocked, message}, agreement_text{hu, en}|null, consensus{exists, etag, csv, decisions}, engine, a?, b? (nyers
  cellák: {header, rows[{row_uid, cells}], n_rows, etag, format})}`.
- `POST /api/reconcile` `{outcome, decisions[{key, field, chosen: a|b|other, value?, reason}], clear?[{key, field}], write?: null|'kettos'|
  'outcome', outcome_if_match?, actor?, dry_run?}` + If-Match → ugyanez a nézet + `written{csv{path, sha256, prov, rows, reconciled,
  builder: engine|server, preview?}, changed, cleared, decision_id}`. Indoklás nélkül / ismeretlen tétel / formátum-eltérés /
  sorszintű „other” → 422; feloldatlan eltérésnél a CSV-írás 409 `GATE_BLOCKED` (`details.code` = X009); a kimenet táblájába írás
  `outcome_if_match` nélkül 409. Atomikus (konszenzus-JSON + CSV + eredet-oldalfájl `reconciled` módszerrel; A és B közben nem
  változhatott). Activity: utak, hash-ek, darabszámok és mezőnevek — cellaérték, kulcs és indoklás nélkül.
- `POST /api/kettos/import` `{outcome, side: A|B, rater?, content_b64 | path (kettos/beerkezett/…), filename?, replace?}` →
  `szk.ma.dual-import/v1` `{state: new|same|replaced, path, etag, rows, columns, header, format, rater, source}` (bájthű mentés,
  PHI-szkenner értékek nélkül, meglévő eltérő fájl csak `replace`-szel). `POST /api/kettos/export` `{outcome, side: A|B|consensus,
  template?}` → `{filename, content_b64, sha256, bytes, rows, media_type}` (a sablon: fejléc + kulcsoszlopok, row_uid nélkül).

**`plot.bubble` (E4c — a motor már írja; a felület által olvasott mezők):** `{moderator{name, label, type: continuous|categorical},
x_axis, y_axis ($defs/axis; y az elemzési skálán, refs[] a referencia-vonalakhoz), points[{row_uid, label, x, y, weight_pct, weight_text?,
x_text{hu,en}, display_text, flags?, group?}], line[[x, ŷ]…], band[[x, alsó, felső]…] (vagy kész band_polygon[[x, y]…]),
pi_band?[[x, alsó, felső]…], coef_text{hu,en}, line_label?, band_label?, pi_band_label?, note?, groups?[{id, label, x, estimate,
ci_lower, ci_upper, display_text, k}]}` (kategóriás moderátornál `line`/`band` üres, `groups` a csoport-összesítők — ezt a motor még
nem írja; a fixture a motor alcsoport-összesítőiből építi). A `plot.cumulative`-ből a `note` és az `order.text` is megjelenik.

## KIEGÉSZÍTÉS (adapter-ágens, v1, 2026-10-05) — plugin-adapterek, Ábra-export (3.5.9), Composer-forrás (3.5.14), Képességek (3.5.16)

Fájlok: `src/components/adapters_{caps,validator}.js`, `src/screens/adapters_{figures,composer}.js`, `src/css/adapters.css`,
`src/i18n/{hu,en}/adapters.json` (`adp.*`), `src/dev/adapters_backend.js` (csak dev; `MA.dev.adapters.{scenario('legacy'|'h5'|'ok'),
manualNumbers(bool), configure(), state()}`), `fixtures/adapters_{caps,figures,composer,validator}.json` (generálja:
`python3 tests/gui/ui/gen_adapters_fixtures.py [--check]` — a VALÓDI szerver válaszai a BCG valódi commit-futásán, stub-pluginokkal);
szerver: `ma_gui/adapters/{validator,figureforge,composer,svgaudit}.py`, `ma_gui/routes/adapters{,_figures,_composer}.py`;
tesztek: `tests/gui/test_v1_adapters.py` (stub-pluginok minden állapotban, `tests/gui/_adapters_stubs.py`),
`tests/gui/test_v1_adapters_real.py` (a valódi szk-plugins: `MA_GUI_PLUGIN_DIRS=…/plugins`, figure-forge-hoz `MA_GUI_TEST_FF_PYTHON`
vagy `MA_GUI_TEST_FF_INSTALL=1`; hiányzó függőségnél kimarad), `tests/gui/ui/test_adapters_fixtures.py`, `node tests/gui/ui/adapters.spec.js`.
A validator 1.0.0 rögzített kimenetei (H1–H4 reprodukció): `tests/gui/adapters_golden/validator-1.0.0/`. A v1 elfogadási teszt
két további 1.0.0-hibát talált: **H12** (eltérő polaritás-címke: QUADAS-2 1.2/1.3, ROBINS-E 2.3/5.2/6.2, ROBINS-I 6.3 → az érintett
domének és az összítélet `reliable: false`, `unreliable_domains`) és **H13** (régi tételszámozás: ROBINS-I 4.3–4.6/5.2/5.3, QUIPS →
ezek a válaszok nem mennek át, `comparable: false`); mindkettő módtól független utófeldolgozás (`ValidatorAdapter._numbering_polarity`).

Képernyők: `figures` (4 Elemzés › Ábra-export; `?run=&kind=`), `prisma-composer` (2 PRISMA › Composer-forrás). A Képességek képernyő
(`screens/capabilities.js`) egy sorral illeszti be az `MA.adaptersCaps.panel(ctx)`-et.

| Modul | API |
|---|---|
| `MA.adaptersCaps` | `panel(ctx) → <section>` (GET /api/adapters táblája: funkció, plugin, állapot ●◑◐○ + szöveg, mód, tartalék, teendő, H-őrök; átalakítók), `stateCell(state, extra?)`, `modeText(mode)` |
| `MA.adaptersValidator` | `box(getDoc, {id?, engine?}) → <section>` („Ellenőrzés a validatorral” → POST /api/validator/check; CSAK a válaszértékek mennek; `engine()` → a motor ellenőrzése: „Összevetés a motorral” — teljesség, implikált ítélet, AMSTAR 2, GRADE, NOS soronként egyezik / eltér az okkal, `.adp-val-cmp li[data-k][data-agree]`), `render(result, engine?)`, `compare(result, engine)`, `minimal(doc)` — az értékelő panel `extra` horgába illeszthető |

Szerver-alakok:
- `GET /api/adapters` → `szk.ma.adapters/v1` `{plugins{validator, figure-forge, composer}, features[{id, plugin, label, state:
  ok|legacy|unusable|absent, mode: json|bridge|null, remedy{hu,en}|null, guards[{id, text}], standalone, fallback, fallback_used?}],
  converters[{name, ok, formats}], converter_remedy, composer_location}`.
- `POST /api/validator/check` `{doc}` → `szk.appraisal-result/v1` + `mode`, `legacy`, `validator_reported{answered, expected, trusted,
  agrees}`, `completeness_source: workbench`, `guards[{id, message, effect}]`; 424 a teendővel.
- `GET /api/figures[?run=]` → `szk.ma.figure-options/v1`; `POST /api/figures/export` → `szk.ma.figure-export/v1` (`qc.badge` a szerver
  döntése: zöld csak hiánytalan számhűségnél); 409 `details.needs_overwrite`; `POST /api/figures/audit` `{path}` | `{run_id, kind}`.
- `GET /api/prisma/composer` → `szk.ma.prisma-composer/v1` + ETag (a PRISMA-fájlé); `PUT /api/prisma/composer/config` `{outdir, project}`;
  `POST /api/prisma/composer/refresh` `{dry_run?, confirm_replace_manual?}` + If-Match → `{flow, check, status_warnings, written, …}`;
  409 `details.needs_confirm` kézi számoknál. A tárolt fájl `source.kind: composer`, `source.status[]` (a composer figyelmeztetései).
