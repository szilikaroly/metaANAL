# MA-munkapad — validáló és grafikus felület a metaanalízis-motorhoz, a figure-forge és a validator (PROBAST+AI / TRIPOD+AI) pluginnal vagy önállóan

**Végleges terv** · Dátum: 2026-10-04 · Állapot: döntés-előkészítés (nem implementált; a pluginok repóját nem módosítottam)

Alapja: három független javaslat (*local-server-first*, *offline-first*, *plugin-ecosystem-first*) és két független
bírálat. Mindkét bíráló a **local-server-first** utat választotta (41/50 és 40/50 pont; a plugin-út 38 és 39, az
offline-út 35 és 35). Ez a dokumentum a nyertes architektúrára épül. Átveszi a másik két javaslat legjobb elemeit,
és javítja a bírálók által megnevezett hibákat (A. függelék). Minden kódra vonatkozó állítást a tervezés közben a
forráskód olvasásával vagy futtatással ellenőriztem (1.2 pont).

---

## 1. Összefoglaló és döntési javaslat

### 1.1 Mit kap a felhasználó

Ez egy magyar nyelvű, böngészőben futó munkafelület (angol publikációs exporttal), amely egy szisztematikus
áttekintést és metaanalízist (SR/MA) a PRISMA-folyamattól a GRADE-ig végigvisz. Windows-on és macOS-en is fut,
csak Python 3.9+ stdlib kell hozzá, CDN és telepítés nélkül. Négy munkaterülete van:

1. **Adatkinyerés és validálás.** Szerkeszthető tábla. Minden cellaváltozás után a motor V001–V025 szabályai a
   *teljes* táblán újrafutnak, ami ms-ok alatt megvan. Cellaszintű forrásjelölés: dokumentum, oldal,
   táblázat/ábra, idézet. Átváltó a motor `conversions` függvényeivel, „becsült” jelöléssel. Kettős kinyerés
   egyeztetése egyetértési mutatóval és az eltérések hatásával.
2. **Elemzés és interaktív ábrák.** Forest (alcsoportokkal, PI-vel), kontúr-javított funnel, Doi/LFK,
   leave-one-out, befolyás-diagnosztika, kumulatív és buborékábra. A vizsgálatra kattintva a kinyerési sorig és a
   PDF forrásoldaláig lehet lefúrni. Publikációs export a figure-forge-dzsal (600 dpi, címke-QC, szerkeszthetőségi
   audit, számhűség-igazolás); figure-forge nélkül a motor saját SVG-jével.
3. **Torzítás és bizonyosság.** RoB 2 / ROBINS-I / ROBINS-E / QUADAS-2 / NOS / QUIPS / JBI űrlapok,
   **PROBAST+AI** (kétmenetes: fejlesztés és értékelés) és **TRIPOD+AI** (52 altétel, D/E szűrés,
   hőtérkép) a validator plugin eszközdefinícióiból. GRADE kimenetenként SoF-táblával, AMSTAR 2 önellenőrzés.
4. **Folyamat és audit.** PRISMA 2020 élő számellenőrzéssel. Projektnapló: döntések, bírálói megállapítások,
   szakaszkapuk. Kereszt-artefaktum ellenőrzés (`project audit`). KB-kereső, hash-láncolt tevékenységnapló,
   reprodukálható audit-csomag.

### 1.2 Hogyan működik

- **Indítás:** `python ma.py gui --project <mappa>` (alias `munkapad`). Windows-on `ma-munkapad.cmd`, macOS-en
  `ma-munkapad.command`, Claude Code-ból a repó saját `metaanalizis` skillje. Egy stdlib `ThreadingHTTPServer`
  indul a 127.0.0.1-en, és egyetlen önálló HTML-felületet szolgál ki.
- **A számok egyetlen forrása a validált motor** (344 teszt és 3268 forrás-ellenőrzés, metafor 4.4
  referencia). A böngésző csak pixelre képez, a figure-forge csak rajzol. A motor `display_text`-je mindenhol
  bájtra ugyanaz, és ezt három ponton ellenőrizzük (6.7).
- **A pluginok opcionális adapterek.** Csak alfolyamatként hívjuk őket, verziózott `szk.*` JSON-szerződéssel
  és `--capabilities` kézfogással. Régi verzióhoz rögzített *bridge* adapter tartozik, a tervezés közben
  igazolt plugin-hibák elleni őrökkel. Plugin nélkül minden képernyő működik, a plugin csak gazdagít.
- **Önálló mód Python nélkül:** csak olvasható, kitakarható HTML-pillanatkép (már az MVP-ben), például
  társszerzőnek vagy zárolt kórházi gépre.
- **Adatvédelem alapértelmezésben:** adatosztályok (A–D) és `_privat/` mappa. Indításkor a munkapad
  felismeri, ha a vault automatikusan GitHubra tolná a projektet, vagy ha OneDrive/iCloud szinkronizálja.
  Kezelt `.gitignore`-blokkot ír, javasolja a Claude Code `deny`-szabályokat, futtat egy TAJ-szám-szűrőt, és a
  projektadat sosem kerül böngészőtárolóba.

### 1.3 Ráfordítás

Egy fejlesztő Claude Code-dal, tartalék nélkül; +15% tartalék javasolt.

| Fázis | Tartalom | Hét |
|---|---|---:|
| 0. fázis | igazolt plugin-hibák javítása (V2, V3, V4, F1, C3) és `--capabilities` minden pluginban | 1 |
| MVP | egy kimenet végig validálva, önálló módban | 6,5 |
| v1 | teljes SR/MA munkafolyamat pluginokkal, köztük PROBAST+AI / TRIPOD+AI | 7 |
| v2 | predikciós modellek poolingja, kézirat-ellenőrzés, Claude-inbox | 4,5 |
| **Összesen** | | **≈ 19** |

### 1.4 Döntési javaslat

**Ezt a tervet javaslom megépíteni, ebben a sorrendben:**

1. **0. fázis azonnal.** Az öt kis plugin-PR (validator GRADE-, verify- és AMSTAR 2-hiba; figure-forge lusta
   import; composer hordozható shebang) a felülettől függetlenül is javít ma ténylegesen hibás viselkedést.
2. **MVP a motor repójában** (`ma_gui/`). Ennek nem feltétele egyetlen plugin-PR sem.
3. **v1** a validator JSON-szerződésére (V1) és a figure-forge `meta` alparancsára (F2) épül.

**Amit tudatosan nem csinálunk:**

- statisztika vagy validációs szabály JS-ben;
- több futtatási mód és fájlbusz (az offline-javaslatból);
- a felület elhelyezése a marketplace-ben, a motortól külön (a plugin-javaslatból);
- LAN-elérés.

Hogy melyik javaslatból mi került át, azt a B. függelék, a bírálói kifogások kezelését az A. függelék
sorolja fel. A 11. fejezet hat eldöntendő kérdést tartalmaz, mindegyikhez ajánlott alapértelmezéssel. Ha azok
változatlanok maradnak, a terv így végrehajtható.

---

## 2. Architektúra

### 2.1 Áttekintő ábra

```
 ┌──────────────────── böngésző (Edge / Chrome / Firefox / Safari) — csak 127.0.0.1 ─────────────────────┐
 │ index.html: egyetlen önálló fájl (inline JS/CSS, CSP-nonce, CDN nélkül) · HU felület, EN export     │
 │ api.js (X-MA-Token) · store.js (állapot, undo) · grid.js · plots/*.js (CSAK pixel: geom.js) ·      │
 │ screens/* · i18n/{hu,en}.json · token: sessionStorage · projektadat: SOHA localStorage/IndexedDB     │
 └──────────────▲───────────────────────────────────────────────────────┬────────────────────────────────┘
                │ JSON (szk.* sémák) + ETag                               │ fetch /api/* · új lap: /f/… (aláírt)
 ┌──────────────┴───────────────────────────────────────────────────────▼────────────────────────────────┐
 │ ma_gui szerver (stdlib, egy folyamat; python ma.py gui)                                                │
 │  security ─ indítókód→token, Host/Origin/Sec-Fetch, CSP-nonce, aláírt fájl-URL, méretkorlát           │
 │  router + séma-ellenőrzés ─ /api/table /validate /convert /compare /analyze /runs /appraisals /grade  │
 │                              /prisma /studies /log /audit /kb /figures /export /capabilities /changes  │
 │  ┌──────────────┐ ┌──────────────────┐ ┌──────────────┐ ┌──────────────┐ ┌───────────────────────────┐ │
 │  │ store        │ │ jobs             │ │ caps         │ │ privacy      │ │ adapters/ (alfolyamat,    │ │
 │  │ CSV kerek-út,│ │ meleg worker-    │ │ feloldás,    │ │ adatosztály, │ │ argv-lista, időkorlát,    │ │
 │  │ ETag, oldal- │ │ folyamat (multi- │ │ kézfogás,    │ │ vault, felhő │ │ JSON be/ki)               │ │
 │  │ fájlok, atom.│ │ processing.Proc.)│ │ known_issues │ │ TAJ-szűrő,   │ │ validator figureforge     │ │
 │  │ írás         │ │ megszakítható    │ │ cache        │ │ .gitignore   │ │ composer presubmit        │ │
 │  └──────┬───────┘ └────────┬─────────┘ └──────────────┘ └──────────────┘ └────────────┬──────────────┘ │
 │  activity (hash-lánc, argv) · snapshot (kitakaró HTML) · audit (determinisztikus ZIP)  │                │
 │         │ in-process (validate, convert, compare,  │ worker (analyze explore/commit)   │ subprocess     │
 │  ┌──────▼──────────────────────────────────────────▼─────────────────────────┐        │                │
 │  │ metaelemzes.api — stabil homlokzat (E1); a CLI is erre áll át             │        │                │
 │  │ pipeline · validate · conversions · kettos · prisma · projekt · kb ·      │        │                │
 │  │ grade_help · audit (X-szabályok)                                          │        │                │
 │  └──────┬─────────────────────┬─────────────────────────┬────────────────────┘        │                │
 └─────────┼─────────────────────┼─────────────────────────┼─────────────────────────────┼────────────────┘
           ▼                     ▼                         ▼                             ▼
  projektmappa (CSV, JSON-   projekt.sqlite (decision,   tudasbazis.sqlite      pluginok (ha vannak, --capabilities):
  oldalfájlok, futások,      finding, checkpoint,        (csak olvas; FTS5;     validator  appraise.py / checklist.py
  activity.jsonl, _privat/)  grade, run)                 teljes szöveg helyben) figure-forge ff.py (saját interpreter)
           ▲    ▲                                                               composer  prisma export (csak olvas)
           │    └── Excel (CSV), composer CLI                                   presubmit pc.py check (v2)
           └─────── Claude Code-ágensek (ma-tervezo, ma-ellenorzo, ma-ertekelo): ugyanazok a CLI-k és fájlok;
                    a változásfigyelő (sha256/mtime + PRAGMA data_version) mutatja a munkájukat
```

### 2.2 Folyamatmodell és életciklus

**Indítás:** `python ma.py gui [--project DIR] [--port 0|8790] [--no-browser] [--lang hu|en]`. A
`.cmd`/`.command` indító a Pythont ebben a sorrendben keresi: `py -3`, `python`, `python3`, a
`.claude/.venv`. A Windows Store-os `python.exe`-csonkot kiszűri: az nem nulla kóddal lép ki, vagy a Store-t
nyitja.

Indításkor ez történik, sorrendben:

1. **Motor-önteszt.** A 192 forráseset 3268 ellenőrzése ≈ 0,2 s; az eredmény zöld vagy piros jelvény.
2. **KB.** `kb.ensure_built`.
3. **Képesség-felderítés** háttérszálon (5.1).
4. **Adatvédelmi ellenőrzés** (7.5).
5. **Indítókód.** Egyszer használható, 60 s-ig érvényes. A böngésző a
   `http://127.0.0.1:<port>/#launch=<kód>` címet nyitja meg.

Egy projektre egyszerre egy szerver futhat. A futásidejű mappa (2.4 vége) `server.json` fájlja tartja a
pid-et és a portot, token nélkül; a második indítás a meglévő példányra irányít, új indítókóddal.

**Kérések.** Stdlib `ThreadingHTTPServer`, egy-egy szál. A kérések két úton futnak:

- **In-process** (≤ 10 ms): validálás, átváltás, összevetés, KB, PRISMA-előnézet, naplóolvasás.
  - Mérés BCG-n: a `validate()` a 13 soros táblán 0,1–0,3 ms, 150 soron ≈ 1,2 ms.
- **Elemzés egy meleg worker-folyamatban:** `multiprocessing.Process` + `Pipe`, `spawn` kontextus, a motort
  előre importálva. Azért nem `ProcessPoolExecutor`, mert annak futó feladata nem szakítható meg.
  - Időkorlát 30 s; túllépésnél `terminate()`, majd új worker.
  - **Legutolsó nyer:** minden kérés `client_seq`-et visz. Ha új explore-kérés érkezik, a régi futást a szerver
    1 s-ig hagyja befejeződni, és az eredményét eldobja (`superseded`); ha tovább tartana, leállítja és a workert
    újraindítja.
  - Időigény: a teljes BCG-elemzés CLI-ből, interpreter-indulással együtt 0,15 s. A k = 150-es, alcsoportos és
    kumulatív elemzés ≈ 2,7 s, ezért automatikus újrafuttatás csak k ≤ 40-nél van.

**Változásfigyelés:** `GET /api/changes?since=<rev>` long-poll, 25 s-os várakozással.

- **Figyelt források** (1–2 s-onként): a projekt-CSV-k és oldalfájlok sha256/mtime-ja, a `projekt.sqlite`
  `PRAGMA data_version`-je, a composer-állapot mtime-ja.
- **Miért nem `EventSource`:** nem küld egyéni fejlécet, így a token URL-be kerülne.

**Leállás:** Ctrl-C, vagy 4 óra tétlenség után magától. A token nem kerül lemezre.

### 2.3 Kódelrendezés (minden új kód a motor repójában: `szilikaroly/anamnezis-asszisztens`)

```
metaanalizis-asszisztens/
  ma.py                          (+ `gui` alparancs, alias `munkapad`; + `project audit`; + `rules export`)
  metaelemzes/api.py             ÚJ — stabil, JSON-képes homlokzat; a CLI és a felület közös útja (E1)
  metaelemzes/spec.py            ÚJ — analysis-spec ↔ DEFAULTS ↔ argv leképezés, argparse-introspekcióval (E3)
  metaelemzes/kettos.py          ÚJ — kettős kinyerés összevetése, κ, eltérés-hatás (E6)
  metaelemzes/grade_help.py      ÚJ — GRADE-tanácsadó bemenetek, SoF abszolút hatás (E10)
  metaelemzes/audit.py           ÚJ — X-szabályok (`project audit`) (E8)
  metaelemzes/contracts/         ÚJ — szk.*.schema.json (a motor által termelt/fogyasztott szerződések)
  ma_gui/
    __main__.py server.py security.py router.py store.py jobs.py caps.py privacy.py activity.py
    snapshot.py audit_export.py schema_lite.py (stdlib séma-részhalmaz validátor)
    routes/   session table validate convert compare analyze runs documents appraisals grade prisma
              studies log audit kb figures export caps privacy
    adapters/ base.py validator.py figureforge.py composer.py presubmit.py
    web/src/  app.js api.js store.js grid.js geom.js plots/{forest,funnel,doi,series,influence,bubble,
              traffic,prisma}.js screens/*.js css/tokens.css i18n/{hu,en}.json selftest.js
    web/build_gui.py  → ma_gui/static/index.html (egy fájl, nonce-helyek; ≤ 450 KB; determinisztikus)
    static/index.html  a lefordított felület, verziókövetve (a felhasználónak nincs build-lépése)
    fallback_instruments.json  doménnevek és ítéletskálák validator nélküli módhoz (sodródás-őrrel)
  tests/gui/  api, biztonság, adatvédelem, lánc-visszajátszás, adapter (golden + stub-pluginok), UI
  ma-munkapad.cmd  ma-munkapad.command
.claude/skills/metaanalizis/SKILL.md   (+ bekezdés: a munkapad indítása; soha ne publikáld Artifactként)
```

A front-end **vanília JS (ES2019), keretrendszer nélkül**, ugyanabban a stílusban, mint az
Anamnézis-asszisztens: CSS-tokenek világos és sötét témával, `data-theme`, szótáras i18n. A `build_gui.py` a
meglévő `build_v16*.py` mintáját követi, de nem sztringpatch-láncként, hanem forrásfájlok összefűzéseként,
ellenőrző `assert`-ekkel.

### 2.4 A projektmappa fájljai és tulajdonosaik

Szabály: **minden fájlnak egy írója van.** Ahol mégis kettő írhat (a tábla a felületről és Excelből vagy egy
ágensből), ott ETag-alapú optimista zárolás véd: 409 és cellaszintű diff.

| Útvonal | Író | Formátum |
|---|---|---|
| `ma-projekt.json` | munkapad | `szk.ma.project/v1`: cím, PICO, review-típus, adatosztály, kimenetek, eszközök |
| `projekt.sqlite` | motor (`projekt.*`; a munkapad is ezeket a függvényeket hívja) | meglévő séma + `actor` oszlop (E7) |
| `02_szures/prisma_flow.json` | kézi mód: munkapad; composer-mód: másolat a composer exportjából | `szk.prisma-flow/v1` |
| `03_adatok/<kimenet>.csv` | munkapad / Excel / ágens | a motor CSV-je + `row_uid` oszlop |
| `03_adatok/<kimenet>.prov.json` | munkapad | `szk.ma.provenance/v1` oldalfájl (diffelhető, nem SQLite) |
| `03_adatok/studies.json` | munkapad | `szk.ma.studies/v1` (vizsgálat ↔ jelentés ↔ dokumentum) |
| `03_adatok/documents.json` | munkapad | dokumentum-jegyzék: id → relatív út, sha256, gyökér |
| `03_adatok/kettos/<kimenet>.{A,B}.csv`, `.consensus.json` | két kinyerő / munkapad | motor-CSV, `szk.ma.consensus/v1` |
| `04_torzitas_kockazat/appraisals/<study>.<tool>[.<cél>].<értékelő>.json` | munkapad | `szk.appraisal/v1` |
| `05_elemzes/specs/<név>.json` | munkapad | `szk.ma.analysis-spec/v1` |
| `05_elemzes/<kimenet>/<run_id>/` | motor (commit) | `results.json`, `plot_data.json` (v2), SVG-k, `report.md`, `run.json` |
| `06_kezirat/abrak/<stem>.*` | figure-forge vagy motor | SVG/PDF/TIFF/PNG/PPTX + `<stem>.result.json` |
| `06_kezirat/sof/<kimenet>.sof.json` | munkapad (motor-számokból) | `szk.ma.sof/v1` |
| `07_ellenorzes/activity.jsonl` | munkapad (csak hozzáfűz) | `szk.ma.activity/v1` (hash-lánc, cellaérték nélkül) |
| `07_ellenorzes/audit/<dátum>/` | munkapad | `szk.ma.audit-bundle/v1` |
| `_privat/` | ember | C/B osztály; **mindig `.gitignore`-ban**, Claude `deny` alatt |

Ami **nem** kerül a projektmappába: az ideiglenes és gyorsítótár-fájlok (bridge-módú Markdown, képesség-cache,
szervernapló). Ezek helye Windows-on `%LOCALAPPDATA%\ma-gui\<projekt-hash>\` (nem roaming, a OneDrive nem
szinkronizálja), macOS-en `~/Library/Caches/ma-gui/…`, Linuxon `$XDG_CACHE_HOME/ma-gui/…`.

### 2.5 Futtatási módok

| Mód | Indítás | Mit tud |
|---|---|---|
| **Élő** | `python ma.py gui --project <mappa>` | mindent, a képességek szerint |
| **Pillanatkép** | `python ma.py gui snapshot --project <mappa> --out x.html [--redact …]` | olvasás, szűrés, interaktív ábrák, nyomtatás; a gombok a pontos parancsot másolják; hálózat nélkül, Python nélkül nyitható |
| **Fej nélküli** | `ma.py project audit <mappa> --json`, `ma.py gui doctor --json`, `ma.py analyze --spec …` | a nem interaktív műveletek; az ágensek és a CI ugyanazt látják, mint a felület |

### 2.6 Explore, commit és gyermek-futások

- **Explore:** minden paraméter-változás ide fut. Csak memóriában létezik, nincs `run_id`-je, nem naplózódik.
- **Commit („Rögzítés”):** a commit-futás kap
  - `run_id`-t (`20261004T211200Z-a1f3c2`);
  - a spec mentését (`05_elemzes/specs/`);
  - a motor összes kimenetét;
  - egy `run.json`-t (`szk.ma.run/v1`, benne az egyenértékű argv: `ma.py analyze --spec … --out …`);
  - `projekt.log_run` naplósort;
  - activity-sort.

  **GRADE, SoF, ábra-export, audit és kézirat-ellenőrzés csak commit-futásra hivatkozhat**, így minden
  közölt szám reprodukálható.
- **Gyermek-futások:** egy kattintással készül érzékenységi spec `purpose: sensitivity` és `parent` mezővel:
  - becsült sorok nélkül (`exclude estimated=igen`);
  - magas RoB nélkül (`exclude rob=high`);
  - fix hatás;
  - DL τ²;
  - kiugrók nélkül.

  A KB D-S12-002/003/005/009 szabályainak ez a gyakorlati megvalósítása.
- **AKTUÁLIS vs. ELAVULT:** egy futás ELAVULT, ha az adat-sha256-ja eltér a tábla mostani hash-étől (X001).

---
## 3. Modulok és képernyők

### 3.1 Szerver-modulok

| Modul | Felelősség | Nem felelőssége |
|---|---|---|
| `security.py` | indítókód → munkamenet-token (`secrets`, `hmac.compare_digest`); Host/Origin/`Sec-Fetch-Site`; CSP-nonce; aláírt fájl-URL (HMAC-SHA256); válaszfejlécek; méretkorlátok | — |
| `router.py` + `schema_lite.py` | útvonal → kezelő; kérés- és válaszséma-ellenőrzés (stdlib részhalmaz: `type, required, enum, const, pattern, properties, additionalProperties, items, oneOf, $ref, minimum/maximum, maxLength`); egységes hiba-boríték | üzleti logika |
| `store.py` | CSV beolvasása a `tableio`-val (a kódolást, elválasztót és tizedesjelet megőrzi); visszaírás ugyanabban a formátumban, `tmp` + `os.replace`; ETag = sha256; oldalfájlok atomikus párírása (4.8); 409 + cellaszintű diff; Excel-zárolásnál érthető hiba („zárd be a fájlt az Excelben”) | számparszolás (azt a motor végzi) |
| `jobs.py` | meleg worker-folyamat, `client_seq`, időkorlát, superseded jelölés, explore/commit | statisztika |
| `caps.py` | plugin- és interpreter-feloldás, `--capabilities` kézfogás, legacy-szondázás, szerződés-hash összevetés, gyorsítótár | — |
| `privacy.py` | adatosztály, vault/OneDrive/iCloud felismerése, kezelt `.gitignore`-blokk, `deny`-javaslat, TAJ/PHI-szkenner, írás-tartás, „már felment?” vizsgálat | a vault konfigurációjának módosítása |
| `activity.py` | hash-láncolt `activity.jsonl` (argv, be/ki sha256, cellaérték nélkül); újrafuttató `rerun.cmd`/`rerun.sh` | — |
| `adapters/*` | egységes protokoll: `detect() → Capability`, `run(argv, stdin_json, timeout) → dict`; `shell=False`, rögzített szkript-út, szűrt környezet | ítélet, rajz, számítás |
| `snapshot.py`, `audit_export.py` | kitakaró, csak olvasható HTML (hash-CSP); determinisztikus ZIP | — |

A szerver Python-kódjában **nincs statisztika**. A selftest AST-ellenőrzéssel tiltja a `math`, `cmath`,
`statistics`, `random` és `decimal` importot a `ma_gui/` alatt (6.8).

### 3.2 Motor-homlokzat (`metaelemzes/api.py`, E1)

```python
engine_info() -> dict            # verzió, önteszt, mértékek + kötelező oszlopok, opció-metaadat (DEFAULTS,
                                 # OPTION_CHOICES, argparse help — generálva), V/P/X-szabálylista (rules export)
read_table(path) -> (header, rows_text, meta) ; write_table(path, header, rows_text, meta)  # tableio;
                                 # a beolvasott formátum (kódolás, elválasztó, tizedesjel) megőrzésével
validate_table(header, rows_text, measure, options) -> dict          # szk.ma.validation/v1
analyze(spec: dict, table=None, mode="explore", outdir=None) -> dict  # nézetmodell + szk.ma.run/v1
spec_from_argv(argv) / argv_from_spec(spec) / options_from_spec(spec) # spec.py; oda-vissza tesztelve
convert(request) -> dict          # szk.ma.convert-result/v1 (estimated jelző, módszer, KB-hivatkozás)
compare(a, b, key, tolerance) -> dict                                 # szk.ma.compare-result/v1
prisma_check(flow, studies=None, template="PRISMA2020") -> dict
project_audit(project_dir) -> dict                                    # szk.ma.project-audit/v1
grade_advice(run, rob_by_row, mid=None) -> dict ; sof(run, assumed_risks) -> dict
kb_search(q, scopes, limit) ; kb_show(id) ; kb_rules_for_field(field) ; kb_checklist(name)
project_* — a projekt.py vékony burka (log/finding/resolve/checkpoint/grade/list/status/export)
```

Minden függvény ugyanazt a dict-et adja, mint a megfelelő CLI-parancs `--json` kimenete; a motor tesztje ezt
bájtra ellenőrzi. A CLI fokozatosan erre a homlokzatra áll át, így a parancssor, az ágensek és a felület
ugyanazt az utat használják.

### 3.3 Front-end modulok

- `api.js` — `fetch`-burok: `X-MA-Token` fejléc, `Content-Type: application/json`, hiba-boríték → toast,
  `client_seq`.
- `store.js` — alkalmazásállapot (projekt, aktív kimenet, tábla-piszkozat, legutolsó validálás és elemzés,
  képességek).
  - Művelet-alapú undo/redo: a szerveren minden mentés naplózott, a visszavonás új művelet.
- `grid.js` — szerkeszthető tábla.
  - Billentyűzetes navigáció; TSV-beillesztés Excelből; oszloprögzítés.
  - Cella-dekoráció: hiba, figyelmeztetés, becsült, egyeztetett, külső szerkesztés.
  - Akadálymentesség: `role="grid"`, `aria-invalid`, `aria-describedby`.
  - Virtualizáció csak a v1-ben jön (az MVP-ben legfeljebb 1000 sor).
  - A cellák **szövegként** maradnak; a számokat a motor értelmezi.
- `geom.js` — az egyetlen modul, amely `Math.*`-t használhat. Lineáris és log10 pixel-leképezést végez a motor
  `axis.domain`-jére, és pixelre igazít.
- `plots/*.js` — SVG-rajzolók, amelyek a `szk.ma.plot/v2`-t képezik pixelre.
  - A tengelyosztás, a kontúr-poligonok, a buborék-sáv és minden szöveg a motorból jön.
  - Minden jelölő `data-uid`/`data-y`/`data-lo`/`data-hi` attribútumot kap (render-konzisztencia teszthez).
  - A színek Okabe–Ito palettán vannak; a szín sosem egyedüli jelölés.
- `screens/*` — a 3.5 képernyői.
- `i18n/*` — a felület magyar, váltható angolra. Az export-szövegek (Methods, SoF, ábrafeliratok) a motorból
  jönnek.
- `selftest.js` — `?selftest=1` esetén az oldalon belüli állítások lefuttatása (8.6).

### 3.4 HTTP API

Minden `/api/*` kérés `X-MA-Token`-t kér (a GET is). A törzs csak `application/json` lehet. A válasz boríték:
`{ok, schema, data, warnings, meta{engine, elapsed_ms, project_rev, request_id}}`.

| Metódus és út | Funkció | Motor / adapter |
|---|---|---|
| `POST /api/session` | indítókód → token (egyszer) | security |
| `GET /api/capabilities` · `POST …/refresh` | képesség-mátrix (5.2) | caps |
| `GET /api/engine` | verzió, önteszt, mértékek, opció- és szabály-metaadat | `api.engine_info` |
| `GET/POST /api/project` | megnyitás, `project init`, kimenetek, adatosztály | `projekt.init` |
| `GET /api/privacy` · `POST /api/privacy/apply` | adatvédelmi állapot; `.gitignore`-blokk, `deny`-szabály, pre-commit őr — **csak a felhasználó kattintására**, diff-előnézettel | privacy |
| `GET /api/table?dataset=` · `PUT /api/table` (If-Match) · `POST /api/table/import` | tábla, mentés, TSV/CSV import | store |
| `POST /api/validate` | piszkozat-validálás (nem ment) | `api.validate_table` |
| `GET/PUT /api/provenance?dataset=` | cellaszintű eredet | store |
| `GET/PUT /api/documents` · `POST /api/fileurl` · `GET /f/<doc>/<exp>/<sig>` | dokumentum-jegyzék; aláírt, 10 perces URL; PDF/kép kiszolgálása új lapon | store, security |
| `POST /api/convert` | átváltó | `api.convert` |
| `POST /api/compare` · `POST /api/reconcile` | kettős kinyerés | `api.compare` |
| `GET/PUT /api/specs/<név>` · `POST /api/analyze` · `GET /api/jobs/<id>` | spec, elemzés (explore/commit) | jobs + `api.analyze` |
| `GET /api/runs` · `GET /api/runs/<id>/{results,plot,report}` | commit-futások | store |
| `GET /api/instruments[/<tool>]` · `GET/PUT /api/appraisals/<unit>/<tool>` · `POST …/check` | RoB / PROBAST+AI / TRIPOD+AI / AMSTAR 2 | validator-adapter vagy tartalék |
| `GET/PUT /api/grade/<kimenet>` · `GET /api/sof/<kimenet>` | GRADE, SoF | `api.grade_advice`, `projekt.add_grade`, `api.sof` |
| `GET /api/prisma` · `PUT /api/prisma/manual` · `GET/PUT /api/studies` | PRISMA + vizsgálat↔jelentés | composer-adapter + `api.prisma_check` |
| `GET /api/log/<kind>` · `POST /api/log/{decision,finding,resolve,checkpoint}` | napló, kapuk | `projekt.*` |
| `GET /api/audit/project` | X-szabályok | `api.project_audit` |
| `GET /api/kb/search` · `GET /api/kb/item/<id>` · `GET /api/kb/rules?field=` | KB-kereső, mezőhöz kötött szabályok | `kb.*` |
| `POST /api/figures/export` · `POST /api/figures/audit` | ábra-export, -audit | figure-forge / motor-SVG |
| `POST /api/manuscript/check` (v2) | kézirat-ellenőrzés tényekkel | presubmit |
| `POST /api/export/audit` · `POST /api/export/snapshot` | audit-csomag, pillanatkép | audit_export, snapshot |
| `GET /api/changes?since=` | long-poll változásjelzés | store |
| `GET /` | a statikus felület — **adat és token nélkül** | — |

**Hibakódok:**

| Kód | HTTP | Mikor |
|---|---:|---|
| `BAD_REQUEST` | 400 | |
| `FORBIDDEN` | 403 | |
| `NOT_FOUND` | 404 | |
| `CONFLICT` | 409 | ETag-eltérés, cellaszintű diffel |
| `GATE_BLOCKED` | 409 | a blockerek listájával |
| `PAYLOAD_TOO_LARGE` | 413 | |
| `UNSUPPORTED_MEDIA` | 415 | |
| `VALIDATION` | 422 | a motor `ValueError`-ja |
| `LOCKED` | 423 | a fájlt más program (tipikusan az Excel) zárolja |
| `CAPABILITY_MISSING` | 424 | |
| `PLUGIN_FAILED` | 502 | nem 0 kilépési kód vagy parszolhatatlan kimenet; a stderr utolsó 2 KB-ja a részletekben |
| `TIMEOUT` | 504 | |
| `INTERNAL` | 500 | |

### 3.5 Képernyők (ASCII drótvázak)

A számok illusztratívak; a BCG-példából és kitalált projektértékekből valók. Közös elvek:

- minden szám mellett ott a forrása: futás-ID, fájl, plugin;
- a szín sosem egyedüli jelölés (RoB: `●◐○` szimbólum és szín együtt);
- billentyűzettel teljesen kezelhető;
- világos és sötét téma;
- nyomtatási CSS minden táblához.

#### 3.5.0 Keret és navigáció

```
┌ MA-munkapad · GLP-1 RA terhesség előtt ──────────────────────────────── motor 0.2.0 ✔ 3268/3268 ┐
│ [HU|EN] [◐ téma] [SzK ▾]  validator ● 1.1 json · figure-forge ◐ matplotlib hiányzik · composer ●  │
│ presubmit ○   Adatvédelem: B osztály · vault-gyökér alatt · VÉDVE (.gitignore + őr)   Külső: 2 ⟳ │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Áttekintés│1 Protokoll│2 PRISMA│3 Kinyerés│4 Elemzés│5 Torzítás│6 GRADE/SoF│7 Napló│8 Export │
│           │           │  ⚠ 1   │ ✖ 1 ⚠ 3  │ ⟳ elavult│  12/18  │  1/3 kész │ ⛔ 2  │         │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
 ●=ok ◐=telepítve, de nem használható ○=nincs · ⛔ nyitott blocker · ⟳ az adat változott a futás óta
```

A fülek jelvényei a szerverből jönnek: validálási összesítő, nyitott blockerek, X-szabályok, értékelés-
lefedettség. A „Külső” számláló azt mutatja, hány fájlt írt más (Excel, ágens) a legutóbbi frissítés óta.

#### 3.5.1 Áttekintés és „következő lépések”

```
 Szakaszok  S00 S01 S02 S03 S04 S05 S06 S07 S08 S09 S10 S11 S12 S13 S14 FINAL
            P   P   P   P  [F]  .   .   .   .   .   .   .   .   .   .    –     [ ] = nyitott blocker
 Nyitott blockerek (2)                      Következő lépések (motor: project audit)
 #12 S04 P007: H okainak összege ≠ H [nyit] X001 az elsődleges futás régebbi a táblánál [újrafuttat]
 #15 S05 SE/SD gyanú (Hart 1977)   [ugrás]  X005 3 becsült sor, nincs „becsült nélkül” futás [létrehoz]
                                            X010 7 elemzett cellának nincs forrásoldala      [szűr]
 Kimenet          k   Résztv.   Elsődleges hatás       I²    RoB magas  GRADE       Futás
 TBC-incidencia  13   357 347   RR 0.49 [0.33; 0.73]   92%   3          nagyon alac. AKTUÁLIS
 Mortalitás       6    80 211   (nincs commit-futás)   –     1 hiányzik –            –
```

#### 3.5.2 Projekt megnyitása és adatvédelmi ellenőrzés

```
┌ Projekt megnyitása ──────────────────────────────────────────────────────────────────────────────┐
│ Legutóbbi: ▸ reviews/glp1-terhesseg (S09, 2 blocker)   ▸ reviews/endo-dieta (S05)                 │
│ [Mappa megnyitása…]  [Új projekt: project init]                                                   │
│ Adatvédelmi ellenőrzés — reviews/glp1-terhesseg                                                   │
│  ✖ A mappa a vault gyökere alatt van (~/Documents/claude): munkamenet végén GitHubra kerül.       │
│    A _privat/ nincs a .gitignore-ban.     [Kezelt .gitignore-blokk beírása (diff ▾)] [Részletek]  │
│  ⚠ A ~/Documents mappát a OneDrive szinkronizálja (Known Folder Move).        [Mit jelent ez?]    │
│  ⚠ Nincs Claude deny-szabály a _privat/ mappára.   [.claude/settings.json javaslat (diff ▾)]      │
│  ✔ git-történet: érzékeny útvonal nem került fel (git log --all -- _privat/ üres)                 │
│  Adatosztály: ( ) A publikált aggregált  (•) B nem publikált aggregált  ( ) C betegszintű          │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

C osztály a vault-gyökér alatt csak úgy indul, ha a `.gitignore`-blokk és a pre-commit őr aktív, és nincs
követett érzékeny fájl; különben a megnyitás blokkolva van (7.5).

#### 3.5.3 Adatkinyerés és élő validáció

```
┌ 3 Kinyerés — kimenet [o1 Halálozás 12 hó ▾] mérték [RR ▾] 03_adatok/o1.csv ✔ 20:31 · sha 9f3a… ┐
│ [+ sor] [Beillesztés Excelből] [Átváltó…] [Kettős kinyerés ▸] [☐ csak problémás] [Forrás Alt+P] │
│ Validálás (motor, 0,3 ms): ✖ 1 hiba · ⚠ 3 figyelmeztetés · ℹ 2 · elemezhető k = 12/13            │
├────┬───────────────────┬──────┬──────┬──────┬──────┬──────┬────────┬───────────┬┬───────────────┤
│ #  │ study             │ e1   │ n1   │ e2   │ n2   │ rob  │ becsült│ forrás    ││ Megállapítások│
├────┼───────────────────┼──────┼──────┼──────┼──────┼──────┼────────┼───────────┤├───────────────┤
│ 1  │ Aronson 1948      │ 4    │ 123  │ 11   │ 139  │ low  │ nem    │ p.3 T1 ⧉  ││✖ V006 Smith   │
│ 2  │ Smith 2010     ✖  │▐140▌ │ 123  │ 11   │ 139  │ high │ nem    │ p.5 T2 ⧉  ││ e1 > n1 [Ugrás]│
│ 3  │ Kovács 2019    ⚠  │ 6 ◆  │ 306  │ 29   │ 303  │ some │ igen   │ p.7 F2 ⧉  ││ [Javítás]     │
│ 4  │ Tóth 2021         │ 3    │ 231  │ 11   │ 220  │ low  │ nem    │ p.4 T3 ⧉  ││ [Nem hiba —   │
│    │                   │      │      │      │      │      │        │           ││  indoklás]    │
└────┴───────────────────┴──────┴──────┴──────┴──────┴──────┴────────┴───────────┘│ [KB V006 ↗]   │
┌ Cella-eredet · Kovács 2019 · e1 ─────────────────────────────────────────────┐ │ℹ V018 becsült │
│ érték 6 (beírva „6”) · módszer: DIGITALIZÁLT (Figure 2) · becsült ◆           │ │ [Érzékenységi │
│ forrás: PMID 31234567 · 7. oldal · Figure 2 · „…at 12 months 6/306 died…”      │ │  futás ▸]     │
│ [PDF a 7. oldalon ↗] · kinyerte SzK 2026-10-04 20:31 · előzmény (2) ▾          │ └───────────────┘
└──────────────────────────────────────────────────────────────────────────────┘
```

Viselkedés:

- **Gépelés közben** a JS csak „számnak látszik-e” előjelzést ad, halványan. A végleges ítélet a motoré.
- **Validálás:** 250 ms tétlenség után `POST /api/validate` megy a teljes piszkozattal.
  - A V007/V011/V012/V017 táblaszintű szabályok miatt a részleges, kliensoldali validálás hibás volna.
  - A megállapítások `row` és `fields` mezői cellára képeződnek.
  - A blokkolt sorok szürkék, és nem számítanak bele a k-ba, pontosan úgy, ahogy a pipeline kizárja őket.
- **„Nem hiba — indoklás”:** a megállapítást naplózott döntéssé alakítja (`project log --agent user --kb V0xx`).
  A motor ettől nem némul el: a jelzés „indokolt” állapotba kerül.
- **Mentés:** Ctrl+S-re vagy fókuszvesztéskor, If-Match ETaggel. A CSV ugyanabban a formátumban íródik vissza,
  amelyben beolvastuk.

#### 3.5.4 Átváltó (modális)

```
┌ Átváltó — Kovács 2019 · m1, sd1 ──────────────────────────────────────────────────────────────┐
│ Típus [Medián + IQR (+ tartomány) → átlag, SD ▾]   Módszer (•) Luo/Wan  ( ) Hozo              │
│ n [40]  medián [12,5]  Q1 [10]  Q3 [15]  min [  ]  max [  ]                                    │
│ Eredmény (motor 0.2.0 · conversions.mean_from_median / sd_from_median): átlag 12,43 · SD 3,71  │
│ BECSÜLT ÉRTÉK → a sor estimated = igen; „becsült nélkül” gyermek-futás javasolt (V018, X005)   │
│ Feltevés: közel normális eloszlás. ⚠ A medián Q1-hez közelebb: ferde eloszlás (V013)            │
│ Forrás: Cochrane Handbook 6.5.2.5 · KB K-… [↗]   Oldal [7] Hely [Table 2] Idézet [median 12.5…] │
│ [Beírás a cellákba — egy tranzakció: érték + estimated + eredet + activity]   [Mégse]          │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Az algebrai átalakítás (például SE → SD) `calculated`, és **nem** becsült; a becslés (medián/IQR → átlag/SD)
`estimated`. Ezt a motor `convert-result.estimated` mezője dönti el, nem a felület.

#### 3.5.5 Kettős kinyerés és egyeztetés

```
┌ Kettős kinyerés — A: SzK (kettos/o1.A.csv) · B: KP (kettos/o1.B.csv) · kulcs: study_id + arm ────┐
│ Egyezés 296/312 cella (94,9%) · eltér 16 · csak A: 1 · csak B: 0 · κ(rob) = 0,71 [0,52; 0,90]   │
│ Oszloponként: sd1 ████░ 5   sd2 ███░░ 3   e1 █░░░░ 1   n1 ░░░░░ 0     (motor: kettos.compare)   │
├───────────────────┬──────┬──────────┬──────────┬───────────────────────┬──────────────┬─────────┤
│ kulcs             │ mező │ A        │ B        │ súgó (motor)          │ hatás (motor)│ döntés  │
├───────────────────┼──────┼──────────┼──────────┼───────────────────────┼──────────────┼─────────┤
│ NCT0456 · T       │ sd1  │ 0.42 p.5 │ 4.2 p.5  │ 10× — tizedes/egység? │ SMD 0.31→0.03│(A)(B)(…)│
│ NCT0789 · C       │ sd2  │ 1.9 p.6  │ 0.30 p.6 │ a ≈ b·√n: SE/SD csere?│ SMD 0.12→0.66│(A)(B)(…)│
│ Gál 2018 · C      │ n2   │ 1,204    │ 1204     │ csak formátum (V023)  │ —            │ auto =  │
├───────────────────┴──────┴──────────┴──────────┴───────────────────────┴──────────────┴─────────┤
│ Indoklás: [Table 2 lábjegyzete: SD; B tizedesjel-hiba        ]  [PDF mindkét oldalon ↗]           │
│ [☐ csak hatásos eltérések]  [Döntés → konszenzus-CSV]  Rögzítve 9/16  [Egyetértési táblázat (EN)] │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

A `hatás` oszlop megmutatja, mennyit mozdítana az eltérés az adott vizsgálat hatásméretén; így a fontos eltérések
kerülnek előre. Minden döntés naplózott (`agent=user`, S05). A konszenzus-CSV-be `origin: reconciled` eredettel
kerül az érték. Az egyetértési statisztika a Methodsba is exportálható.

#### 3.5.6 Elemzési terv, protokoll-eltérés, parancs-előnézet

```
┌ 4 Elemzési terv · o1_primary (elsődleges · előre rögzített: protokoll 9.2) ────── [Spec JSON] ──┐
│ Mérték [RR ▾] Modell [random ▾] τ² [REML ▾] CI [HKSJ ▾] PI [t(k−2) ▾] szint [0,95] ⚙ Konvenciók │
│ Alcsoport [allokáció ▾] ☐ közös τ²  Moderátor [szélesség] teszt [KNHA ▾] ☐ robusztus           │
│ Kumulatív [év ▾] ☐ kiugrók  MID [0,75]–[1,25]  Szűrők: kizár [            ]                     │
│ KB ehhez: D-S08-001 · D-S08-002 · D-S09-007 · D-S12-005 [megnyit]                                │
│ ┌ Protokoll-összevetés ─────────────────────────────────────────────────────────────────────┐  │
│ │ ⚠ τ²: protokoll DL, most REML → elsődlegesként rögzítve protokoll-eltérés (X016): döntés kell│  │
│ └────────────────────────────────────────────────────────────────────────────────────────────┘  │
│ Parancs-előnézet: python ma.py analyze --spec 05_elemzes/specs/o1_primary.json --out … [másol]  │
│   (egyenértékű: --data 03_adatok/o1.csv --measure RR --tau2 REML --ci hksj --subgroup alloc …)  │
│ [Futtatás (explore)] [☑ automatikus, ha k ≤ 40] [Rögzítés (commit)]                             │
│ Érzékenységi gyermekek: [+ becsült nélkül] [+ magas RoB nélkül] [+ fix hatás] [+ DL] [+ kiugrók]│
│ Futások: 10-04 21:12 o1_primary      k=13 RR 0.49 [0.33; 0.73] I² 92% adat 9f3a… AKTUÁLIS      │
│          10-04 21:15 └ o1_no_estim.  k=12 RR 0.47 [0.31; 0.72] I² 91% adat 9f3a… AKTUÁLIS      │
│          10-03 18:02 o1_primary      k=13 RR 0.50 [0.34; 0.74] I² 92% adat 4c0d… ELAVULT       │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Az űrlap mezői, választható értékei, alapértékei és súgószövegei a motor `engine_info()` opció-metaadatából
generálódnak. Ennek alapja az argparse-introspekció és a `DEFAULTS`/`OPTION_CHOICES`, így a JS-ben nincs második
opciólista.

#### 3.5.7 Eredmények — interaktív forest, lefúrás, KB-jelvények

```
┌ Eredmények · o1_primary · 20261004T211200Z-a1f3c2 · AKTUÁLIS ─────────────────────────────────────┐
│ RR 0,49 [0,33; 0,73] · p = 0,002 · PI 0,13–1,79 · I² 92% [87; 95] · τ² 0,313 · k 13 · N 357 347  │
│ modell: REML + HKSJ ⓚD-S08-001  PI ⓚD-S09-007  k<10 torzítás-teszt ⓚV016                         │
│ [Forest] Funnel  Doi  LOO  Befolyás  Kumulatív  Buborék  Alcsoportok  Torzítás  Érzékenység       │
│ ┌──────────────────────────────────────────────────────────────────────────────────────────────┐ │
│ │ Vizsgálat            Esem./N(I) Esem./N(K)   0.1     0.5   1    2        RR [95% CI]   Súly ● │ │
│ │ ▾ random (k = 7)                                                                             │ │
│ │   Aronson 1948          4/123     11/139       ├────■──┼──┤          0.41 [0.13; 1.26] 5.1% ● │ │
│ │   Ferguson & Simes      6/306     29/303    ├───■────┤   │          0.20 [0.09; 0.49] 6.4% ○ │ │
│ │ ▸ Hart & Sutherland◀ 62/13598  248/12867      ├■┤       │          0.24 [0.18; 0.31] 9.7% ● │ │
│ │   Subtotal (k = 7)  τ² 0.393; I² 95%           ◆◆◆◆◆    │          0.38 [0.20; 0.70]        │ │
│ │ Random-effects (REML, HKSJ)                       ◆◆◆◆  │          0.49 [0.33; 0.73]        │ │
│ │ Prediction interval                   ├──────────────────┼───┤      [0.13; 1.79]            │ │
│ │ Subgroup difference: Q = 1.86, df = 2, p = 0.39                                              │ │
│ └──────────────────────────────────────────────────────────────────────────────────────────────┘ │
│ ┌ Lefúrás: Hart & Sutherland 1977 · sor 4 · row_uid r2b91 ──────────────────────────────────────┐ │
│ │ e1 62/13598 · e2 248/12867 · eredet: közölt · p.6 Table 2 [PDF a 6. oldalon ↗] [Ugrás a sorhoz]│ │
│ │ LOO nélküle: 0.52 [0.33; 0.81], I² 90% · befolyás: rstudent −1.9, Cook 0.21 (nem befolyásos)  │ │
│ │ RoB 2: D1● D2◐ D3● D4● D5● → Némi aggály (implikált: ugyanaz) [Értékelés] · kettős kinyerés: ✔│ │
│ └──────────────────────────────────────────────────────────────────────────────────────────────┘ │
│ [Ábra exportálása…] [Methods-bekezdés (EN)] [report.md] [Rétegek: ☑PI ☑alcsoport ☐fix ☑becsült]  │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Kiemelés:** ha az egér egy sor fölött áll, a `row_uid` minden nyitott ábrán ki van emelve (funnel, Doi,
  LOO).
- **Billentyűzet:** ↑/↓ a vizsgálatok között, Enter a lefúrás.
- **Tooltip:** a motor `display_text`-jét mutatja.
- **KB-jelvény (ⓚ):** minden olyan eredménymező mellett megjelenik, amelyre egy KB-szabály `machine_check`-je
  hivatkozik. A listát a motor adja (`GET /api/kb/rules?field=`).
- **„PDF a 6. oldalon”:** aláírt, rövid életű URL-en új lapon nyílik (`#page=6`). Safari alatt az oldalszám
  szövegként is kiíródik.

#### 3.5.8 További ábrák

```
 Funnel (kontúr-javított) — x: log RR · y: SE (fordított) · Harbord p 0,23 · Peters p 0,20 · trim-fill k0 = 1
   0.0 ┤             ░▒▓│▓▒░            ▓ p<0,01  ▒ 0,01–0,05  ░ 0,05–0,10  (motor-poligonok)
   0.5 ┤         ░▒▓ ●  │●   ▓▒░        ● vizsgálat  ○ pótolt (trim-and-fill)  ┄ pszeudo-CI
   1.0 ┼────────────────┼──────────────
      −2       −1       0        1     Motor: bináris kimenetnél Harbord/Peters az elsődleges (Sterne 2011)
```

- **Doi:** x = hatás az elemzési skálán, y = |Z| fordított tengellyel; az LFK és a kategória a motor
  szövegével, U+2212 mínusszal.
- **LOO és kumulatív:** forest-szerű sorozat, minden sor a motor kész `display_text`-je.
- **Befolyás:** táblázat (rstudent, DFFITS, Cook, cov.ratio, hat, DFBETAS) és Cook-oszlopdiagram. A
  „befolyásos” és „kiugró” jelzés a motor metafor-kritériumaiból jön, a felület küszöböt nem számol.
- **Buborék:** x = moderátor, y = hatás, a buborék mérete a súly. Az illesztett egyenes és a CI-sáv a motor
  rácsán számolódik a koefficiensek kovarianciájából (E4c). Kategóriás moderátornál buborék helyett
  csoportonkénti pontdiagram.

#### 3.5.9 Ábra-export

```
┌ Ábra exportálása — Forest · futás 20261004T211200Z-a1f3c2 (AKTUÁLIS) ───────────────────────────┐
│ Megjelenítő: (•) figure-forge 0.3.0 (meta)  ( ) motor-SVG  ( ) böngészős PNG — ELŐNÉZET, QC NÉLKÜL│
│ Formátum ☑SVG ☑PDF ☑TIFF ☐PNG ☐PPTX  dpi [600 ▾] szélesség [double · 183 mm ▾] nyelv [EN ▾]       │
│ Oszlopok ☑esemény/N ☑súly ☑PI  sorrend [bemenet ▾]  paletta [okabe-ito ▾] ☑tipográfia (U+2212)    │
│ Eredmény: QC ✔ tiszta (64 címke) · szerkeszthető ✔ (0 görbésített szöveg, betűkészlet-lánc ✔)    │
│   glifák ✔ · figure-forge számhűség ✔ 31/31 · szerver-újraellenőrzés ✔ 31/31 · 4 tipográfiai csere│
│   06_kezirat/abrak/fig2_forest.{svg,pdf,tiff}  [Mappa] [Áttekintő overlay]                        │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Figure-forge nélkül a motor-SVG készül angol felirattal, elnevezett rétegekkel és U+2212 mínusszal (E5), mellé
stdlib-audit. Ha a figure-forge ≥ F1 elérhető, az `audit` is lefut, matplotlib nélkül is. A TIFF és a PPTX csak
figure-forge-dzsal érhető el; a felület kiírja, mi hiányzik, például: „matplotlib nem importálható a
`C:\…\python.exe` értelmezőben; állítsd be a FIGURE_FORGE_PYTHON-t”.

#### 3.5.10 Torzítási kockázat (RoB 2 / ROBINS-I / ROBINS-E / QUADAS-2 / NOS / QUIPS / JBI)

```
┌ 5 Torzítás — o1 · eszköz [RoB 2 — eredményenként ▾] · validator 1.1.0 (json) · 22 tétel ────────┐
│ Vizsgálat \ domén   D1 D2 D3 D4 D5  Össz.  kitöltve  értékelő  második  egyeztetve               │
│ Aronson 1948        ●  ●  ●  ●  ●   ●      22/22     SzK       KP       ✔                        │
│ Kovács 2019         ●  ○  ●  ◐  ●   ○      19/22 ⚠   SzK       —        —                        │
│ ● alacsony ◐ némi aggály ○ magas · = üres   [Forgalmi lámpa] [Súlyozott összesítő (motor-súlyok)] │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Kovács 2019 · halálozás 12 hó (ITT) · hatókör: assignment                                         │
│ Domén 2 — Eltérés a tervezett beavatkozástól                                                      │
│  2.1 Tudtak-e a résztvevők a besorolásukról?   [Y][PY][PN][N][NI] Y  (irányító kérdés)           │
│  2.6 Megfelelő elemzés a hatás becsléséhez?    [Y][PY][PN][N][NI] N  bizonyíték: p.8 [PDF ↗]     │
│  2.7 …                                         [—] ← hiányzik                                     │
│ Implikált (validator, KONZERVATÍV — nem a hivatalos RoB 2 folyamatábra): MAGAS — 'N' a 2.6-nál    │
│ Az Ön ítélete [Magas ▾]  Indoklás [……………]   (eltérésnél kötelező → döntés a naplóba, X017)       │
│ [Teljesség] [Rollup] [Mentés]   Hiányzó: 2.7, 4.5, 5.2   [Konszenzus-nézet A|B]                   │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Az eszközt a validator `--route` javasolja a projekt típusa és a sor `design` oszlopa alapján; több találatnál
mindet felsorolja, a validator saját magyarázatával.

- **Önálló mód:** csak doménszintű ítélettábla áll rendelkezésre. A doménnevek és az ítéletskála a
  `fallback_instruments.json`-ból jönnek. A fejléc kiírja: „jelző-kérdések a validator pluginnal érhetők el”.
- **A konszenzusos összítélet sorsa:** bekerül a kinyerési tábla `rob` oszlopába `calculated` eredettel. Így a
  V019 és a „magas RoB nélkül” futás automatikusan a helyes értéket látja; ezt az X003 őrzi.

#### 3.5.11 Predikciós modell — PROBAST+AI és TRIPOD+AI

```
┌ 5 Torzítás — predikciós modellek · PROBAST+AI (validator 1.1) ─── menetek ☑ fejlesztés ☑ értékelés ┐
│ SQ   kérdés                               │ fejlesztés: válasz  bizonyíték │ értékelés: válasz  biz. │
│ 1.1  Megfelelő adatforrások?              │ [PY]  p.4 „registry”           │ [Y]   p.9               │
│ 1.2  Megfelelő vizsgálati elrendezés?     │ [Y]                            │ [PN]  p.9 „…”           │
│ …    (kulcsok: development/1.1, evaluation/1.1 — menetenként külön számolva: 16 + 18 = 34)        │
│ Domén-ítélet          fejlesztés (minőség)   értékelés (torzítás)   Alkalmazhatóság (PICOTS)         │
│ 1 Résztvevők/adat     [Low]                  [High]                 [Low]                          │
│ 2 Prediktorok         [Low]                  [Unclear]              [Low]                          │
│ 3 Kimenet             [Low]                  [Low]                  [High]                         │
│ 4 Elemzés             [High]                 [High]                 —                              │
│ Összítélet: HOLISZTIKUS (a PROBAST+AI-nak nincs algoritmusa) → [Magas ▾] indoklás kötelező        │
│ Teljesség 34/34 (munkapad-számlálás; validator 1.0.0 bridge-módban a sajátja nem megbízható: H2)  │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ TRIPOD+AI jelentési teljesség (52 altétel, szűrő [D][E][D;E]) — hőtérkép: tétel × vizsgálat        │
│            1  2  3a 3b 3c 4  5a 5b 6a … 11 … 18a 18b … 27c                                        │
│ Lee 2023   ■  ■  ■  ▣  ■  ■  ■  □  ■ …  □ …  □   □  …  ■     ■ Present ▣ Partial □ Missing · N/A  │
│ Varga 2024 ■  ▣  ■  ■  □  ■  ▣  ■  ■ …  ■ …  □   □  …  ▣     [Hiánylista] [Saját kézirat ▸]      │
│ ⓘ A TRIPOD+AI a jelentés teljességét méri, nem a módszertan helyességét.                          │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Ha a projekt `review_type: prediction_model`:

- a RoB-menü PROBAST+AI-ra vált;
- a kinyerési sablon teljesítménymutatókat kér: c-statisztika CI-vel, O:E, kalibrációs meredekség;
- az X011 jelzi, ha egy vizsgálatnak nincs PROBAST+AI-értékelése.

A **„Saját kézirat”** nézet ugyanezt a TRIPOD+AI-ellenőrzést a felhasználó saját predikciós modelles
kéziratára futtatja, a presubmit-jelentés mellett (v2). A c-statisztika és az O:E poolingja a v2-ben jön (E13).

#### 3.5.12 GRADE kimenetenként és SoF

```
┌ 6 GRADE / SoF — o1 Halálozás 12 hó (kritikus) · futás 20261004T211200Z-a1f3c2 ───────────────────┐
│ Kiindulás (•) magas (RCT) ( ) alacsony (megfigyeléses)                                             │
│ Domén               Ítélet               Lépés  Motor-tanács (csak javaslat, grade_help)            │
│ Torzítási kockázat  [serious ▾]           −1    magas RoB-ú vizsgálatok súlya 41,2% (Smith, Kovács)│
│ Inkonzisztencia     [not serious ▾]        0    I² 92% [87; 95]; PI 0,13–1,79 átnyúlik az 1-en ⚠    │
│ Indirektség         [not serious ▾]        0    — (emberi ítélet)                                   │
│ Pontatlanság        [not serious ▾]        0    CI nem éri el a MID-et (0,75–1,25) ✔; OIS ✔          │
│ Publikációs torzítás[suspected ▾]          ?    FELOLDATLAN: döntsd el (0 / −1) és indokold  [0][−1]│
│                                                 (Harbord p 0,23 · Peters p 0,20 · LFK −4,10)       │
│ Felminősítés        ☐ nagy hatás ☐ dózis–válasz ☐ ellentétes zavaró                                 │
│ Bizonyosság: — (feloldatlan domén)   validator: rollup_unreliable (1.0.0, H3-őr)   motor: —        │
│ ⚠ Az inkonzisztencia-tanács leminősítést jelez; a „not serious” ítélethez indoklás kell.           │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Summary of Findings (EN)                                       alapkockázat [kontroll-pool ▾] [+]   │
│ Outcome       │ Participants (studies) │ RR [95% CI]       │ Baseline │ Absolute /1000     │ Cert. │
│ Mortality 12m │ 357 347 (13 RCTs)      │ 0.49 [0.33; 0.73] │ 9/1000   │ 5 fewer (6–2 fewer)│ ⊕⊕⊕◯ a│
│ [HTML másolása Wordbe] [Markdown] [CSV (Excel-biztos)]   lábjegyzetek a GRADE-indoklásokból       │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

A publikációs torzítás doménje:

| Ítélet | Lépés |
|---|---|
| `undetected` | 0 |
| `strongly suspected` | −1 (kézzel, indoklással −2) |
| `suspected` | **feloldatlan**: a rögzítés tiltott, amíg a felhasználó nem választ 0-t vagy −1-et indoklással (X019) |

A rögzítés a `projekt.add_grade` függvénnyel történik, előjeles lépés-szöveggel, így a meglévő
`grade_consistency` is ellenőriz.

A SoF minden cellájának forrása egy motormező:

- `totals.participants` és `k`;
- `back_transformed`;
- `sof.absolute` (alapkockázatonként).

A kontroll-pool alapkockázat az alapértelmezés; további, külső alapkockázat felvehető.

#### 3.5.13 AMSTAR 2 önellenőrzés

```
┌ AMSTAR 2 — a saját áttekintés (KB AMSTAR2, 16 tétel; validator rollup; konvenció: KB AMSTAR2-00) ┐
│ #   Tétel (K = kritikus)                     Javasolt bizonyíték (project audit)   Ítélet          │
│ 2 K Előre rögzített protokoll                protokoll.md · CRD42… ✔                [Igen ▾]        │
│ 4 K Átfogó keresés                           PRISMA-S: 4 adatbázis, regiszter 0      [Részben igen ▾]│
│ 7 K Kizárt vizsgálatok listája okokkal       prisma_flow: H okokkal ✔ (nincs P008)   [Igen ▾]        │
│ 9 K RoB megfelelő eszközzel                  appraisals: 13/13 RoB 2                 [Igen ▾]        │
│ 13 K RoB az értelmezésben                    GRADE RoB-domén kitöltve                [ ]  ← hiányzik │
│ Besorolás: MAGAS (kritikus hiba 0; nem kritikus gyengeség 1) — IDEIGLENES: 1 tétel hiányzik        │
│ Konvenció-érzékenység: ha a „részben igen” kritikus tételen gyengeség → MÉRSÉKELT  (KB AMSTAR2-00)  │
│ validator 1.1 ✔ egyezik · motor-konzisztencia (amstar2_consistency) ✔                              │
│ ⓘ Az AMSTAR 2 az áttekintés eredményeibe vetett bizalmat minősíti, nem a bizonyosságot (≠ GRADE).    │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

A projektkonvenciót a KB AMSTAR2-00 rögzíti: kritikus tételen a „részben igen” nem hiba, de jelezni kell, változna-e
a besorolás, ha hibának számítanánk. A felület **mindkét besorolást** megmutatja, ha eltérnek. A validator
PR-V4 ezt a konvenciót explicit kapcsolóvá teszi, és megszünteti a „PY” ≠ „Partial yes” eltérést.

#### 3.5.14 PRISMA 2020

```
┌ 2 PRISMA 2020 — forrás: composer (glp1 · 2026-10-04 20:12 · 1.4.1) [Frissítés] · ✖ 1 ⚠ 1 ℹ 1 ───┐
│ Azonosítva: adatbázisok (A1) 1234 · regiszterek (A2) 12   │ Szűrés előtt eltávolítva: D1 300 · D2 0 │
│ Szűrt rekordok (B) 946 ───────► kizárva (C) 860                                                   │
│ Keresett jelentések (E) 86 ───► nem elérhető (F) 4                                                │
│ Értékelt jelentések (G) 82 ───► kizárva okokkal (H) 57   ✖ P007: az okok összege 55 ≠ 57          │
│ Bevont vizsgálatok (I) 18 (studies.json) · jelentések (J) 25 (composer) ✔ · metaanalízisben 13 (o1)│
│ ⚠ X014: az o1 commit-futásában k = 13 ≤ I = 18 ✔ · X015: o1 included_meta 13 = k ✔                 │
│ ⚠ composer: 5D-kapu „függőben” rekord: 3 · retmax-figyelmeztetés (a composer status szövege)      │
│ [Vizsgálat↔jelentés térkép] [Folyamatábra (figure-forge)] [Kézi felülírás indoklással]            │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Composer-mód (csak olvasás).** Az adatfolyam:

1. a composer exportja egy ideiglenes mappába megy;
2. a munkapad onnan másolja a `02_szures/prisma_flow.json`-ba;
3. a sha256 és a composer-verzió az activity-naplóba kerül.

A szűrési döntések a composerben maradnak.

**Kézi mód.** A dobozok szerkeszthetők, és minden billentyűleütésre a motor `prisma check` előnézete fut. A
hibás doboz piros keretet kap: a P-megállapítások `fields` mezője dobozbetűre képeződik.

#### 3.5.15 Napló, kapuk, project audit, KB-kereső

```
┌ 7 Napló ─ [Döntések] [Megállapítások ⛔2] [Ellenőrzőpontok] [GRADE] [Futtatások] [X-szabályok 3] ─┐
│ Szűrő: szakasz [S09 ▾] súlyosság [blocker,major ▾] állapot [open ▾] ügynök [mind ▾]               │
│ #  ts           szak  súly     cím                              ügynök    állapot  KB             │
│ 3  10-03 14:02  S09   blocker  SE/SD csere gyanú (Smith 2010)   reviewer  open     V011 D-S07-…   │
│ 7  10-04 19:40  S09   blocker  Kettős zéró kezelése indokolatlan reviewer open     V008           │
│ [Megoldás: fixed/wontfix/invalid + kötelező indoklás]  [Új megállapítás]                            │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Ellenőrzőpont [S09 ▾] [PASS ▾] [Rögzítés] → ⛔ elutasítva: 2 nyitott blocker (#3, #7)               │
│ FINAL [kérés] → tiltva: blocker bármely szakaszban + X-szabály hibák: X001, X009 (project audit)   │
├ KB-kereső (Ctrl+K) ──────────────────────────────────────────────────────────────────────────────┤
│ [predikciós intervallum   ]  (•) szabály ( ) tudás ( ) teljes szöveg (helyi, nem exportálható)  │
│ D-S09-007 must  véletlen hatás, k ≥ 3 → PI közlése · D-S08-020 · V015 · inthout2016 „…” [megnyitás] │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

A felület semmit nem ír közvetlenül az SQLite-ba, mindent a `projekt.*` függvények végeznek. Ha a motor egy
ágenssel való verseny miatt mégis elutasít egy kérést, a hibaüzenete szó szerint jelenik meg.

#### 3.5.16 Képességek és adatvédelem

```
┌ Képességek ──────────────────────────────────────────────────────────────────── [Újraszondázás] ┐
│ komponens     verzió  állapot          szerződések                 megjegyzés / teendő           │
│ motor         0.2.0   ok (api)         ma.* v1/v2 ✔                önteszt 3268/3268             │
│ validator     1.0.0   legacy (bridge)  —                           őrök: H1 H2 H3 H4 · V1 kell JSON-hoz│
│ figure-forge  0.2.1   unusable         —                           matplotlib hiányzik → FIGURE_FORGE_PYTHON│
│ composer      1.4.1   ok (flow-json)   prisma-flow (séma nélkül)   shebang: explicit interpreterrel hívva│
│ presubmit     —       absent           —                           claude plugin install presubmit@szk-plugins│
│ Adatvédelem: osztály B · vault aktív, projekt a gyökér alatt (mélység 2) · .gitignore-blokk ✔       │
│   pre-commit őr: telepítve (opt-in) · OneDrive: Documents szinkronizált ⚠ · Claude deny: _privat/** ✔│
│   követett érzékeny fájl a git-indexben: nincs                                                    │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

#### 3.5.17 Export és pillanatkép

```
┌ 8 Export ───────────────────────────────────────────────────────────────────────────────────────┐
│ ☑ Döntési napló (MD + JSON)  ☑ Specek + commit-futások  ☑ Eredet + egyeztetések  ☑ Értékelések     │
│ ☑ PRISMA + studies.json  ☑ Ábrák + QC  ☑ activity.jsonl (lánc ép: 412 bejegyzés)  ☑ rerun.cmd/.sh   │
│ ☐ Adattáblák (B osztály: alapból ki)   ☒ _privat/ (C) — tiltva   ☒ PDF-ek (D) — csak doc-id + oldal│
│ Kitakarás ☑ értékelők → monogram ☑ provenance-idézetek ki ☑ abszolút utak ki                       │
│ [Audit-ZIP (determinisztikus)] → 07_ellenorzes/audit/2026-10-04/                                   │
│ [Pillanatkép társszerzőknek (egy HTML, hálózat nélkül)]  ⚠ Ne töltsd fel és ne publikáld.           │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---
## 4. Adatszerződések (JSON Schema 2020-12)

### 4.0 Közös konvenciók

- **Azonosítás.** Minden dokumentum első mezője a `"schema": "szk.<terület>.<név>/v<N>"`. A séma `$id`-je URN:
  `urn:szk:contract:<terület>.<név>:<N>`. Azért nem URL, mert a marketplace CI slash-lintje hamis találatot adna
  rá.
- **Számok.** IEEE-754 double. Hiányzó vagy nem értelmezett érték `null`, **soha** nem `NaN`, `Infinity` vagy
  szöveg.
- **Skálák.** Az `y`, `lo`, `hi`, `estimate` és `ci_*` mezők mindig az *elemzési* skálán vannak (például log RR).
  A `display` és a `display_text` a *megjelenítési* skálán van (például RR); a transzformációt a motor végzi.
- **Útvonalak.** A projektgyökérhez képest relatívak, `/` elválasztóval. Abszolút út csak a képesség-leírásban
  szerepel.
- **Nyelvfüggő szöveg.** `i18n`-objektum: `{"hu": "…", "en": "…"}`. A motor mindkettőt előállítja; a felület
  nem kerekít újra. Ennek oka, hogy a JS `toFixed` és a Python `%` a pontos „döntetlennél” eltérően kerekít
  (0,125 → 0,13 vs. 0,12).
- **Bővítés.** A fogyasztó az ismeretlen mezőt figyelmen kívül hagyja. Kötelező mező törlése vagy
  jelentésváltozása új főverziót jelent. A fogyasztó legalább az aktuális és az előző főverziót olvassa, de
  csak az aktuálisat termeli.
- **Elhelyezés.** A motor által termelt szerződések (`szk.ma.*`) a `metaelemzes/contracts/`, a pluginok
  szerződései a saját `contracts/` mappájukban élnek. Ugyanazon séma másolatai bájtra egyeznek; az ellenőrzést a
  4.20 írja le.

```json
{ "$id": "urn:szk:contract:common:1", "$defs": {
  "i18n":    {"type": "object", "required": ["hu","en"], "properties": {"hu": {"type": "string"}, "en": {"type": "string"}}},
  "num":     {"type": ["number","null"]},
  "relpath": {"type": "string", "pattern": "^(?!/)(?![A-Za-z]:)(?!.*\\.\\.)[^\\\\:]+$"},
  "sha256":  {"type": "string", "pattern": "^[0-9a-f]{64}$"},
  "severity":{"enum": ["error","warning","info"]},
  "kbid":    {"type": "string", "pattern": "^([VPX]\\d{3}|[A-Z][A-Z0-9_]*-[A-Z0-9-]+)$"},
  "row_uid": {"type": "string", "pattern": "^r[0-9a-z]{4,12}$"},
  "display": {"type": "object", "required": ["est","lo","hi"],
              "properties": {"est": {"$ref": "#/$defs/num"}, "lo": {"$ref": "#/$defs/num"}, "hi": {"$ref": "#/$defs/num"}}}
}}
```

### 4.1 `szk.capabilities/v1` — kézfogás (minden plugin és a motor: `--capabilities`)

```json
{ "$id": "urn:szk:contract:capabilities:1", "type": "object",
  "required": ["schema","plugin","version","ok","contracts","commands"],
  "properties": {
    "schema":   {"const": "szk.capabilities/v1"},
    "plugin":   {"type": "string", "examples": ["metaelemzes","validator","figure-forge","composer","presubmit"]},
    "version":  {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+"},
    "python":   {"type": "string"},
    "ok":       {"type": "boolean", "description": "a deklarált parancsok ebben az interpreterben futtathatók"},
    "contracts":{"type": "object", "additionalProperties": {"type": "object", "required": ["dir","sha256"],
                 "properties": {"dir": {"type": "array", "items": {"enum": ["in","out"]}},
                                "sha256": {"$ref": "urn:szk:contract:common:1#/$defs/sha256"}}},
                 "description": "a plugin contracts/ mappájában lévő séma bájt-hash-e — futásidejű sodródás-őr"},
    "commands": {"type": "array", "items": {"type": "object", "required": ["name","argv"],
                 "properties": {"name": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}},
                                "in": {"type": "array"}, "out": {"type": "array"},
                                "needs": {"type": "array", "items": {"type": "string"}}, "available": {"type": "boolean"}}}},
    "requires": {"type": "object", "properties": {"modules": {"type": "array"}, "missing": {"type": "array"}}},
    "known_issues": {"type": "array", "items": {"type": "object", "required": ["id","summary"],
                 "properties": {"id": {"type": "string"}, "summary": {"type": "string"}, "fixed_in": {"type": "string"}}}}
  } }
```

A szerver ebből képezi a felület állapotát pluginonként:

| Állapot | Jelentés |
|---|---|
| `absent` | nincs telepítve |
| `unusable` | megvan, de hiányzik egy függőség; ilyenkor `problems[]` mondja meg a teendőt |
| `legacy` | a `--capabilities` hiányzik; verzióhoz rögzített bridge-adapter fut |
| `ok` | a kézfogás sikerült; a `features[]` sorolja fel a funkciókat |

Ha egy szerződés `sha256`-ja eltér a munkapad saját másolatától, a funkció `legacy` módba esik vissza, és a
felület kiírja: „szerződés-eltérés”. Ez a futásidejű őr a két repó közötti sodródást is elkapja.

### 4.2 Boríték és hibák (munkapad-helyi)

```json
{ "ok": true, "schema": "szk.ma.validation/v1", "data": { }, "warnings": [],
  "meta": { "engine": "0.2.0", "elapsed_ms": 1, "project_rev": 41, "request_id": "q_7f3a" } }
{ "ok": false, "error": { "code": "GATE_BLOCKED", "http": 409,
  "message": "2 nyitott 'blocker' megállapítás van az S09 szakaszban; PASS nem adható.",
  "details": { "blockers": [ { "id": 3, "stage": "S09", "title": "SE/SD csere gyanú" } ] } } }
```

### 4.3 `szk.ma.validation/v1` — motor → felület (`api.validate_table`, `validate --json`; E2)

```json
{ "$id": "urn:szk:contract:ma.validation:1", "type": "object",
  "required": ["schema","engine_version","measure","summary","findings","k_analysable"],
  "properties": {
    "schema": {"const": "szk.ma.validation/v1"}, "engine_version": {"type": "string"}, "measure": {"type": "string"},
    "input_sha256": {"$ref": "urn:szk:contract:common:1#/$defs/sha256"},
    "column_map": {"type": "object", "additionalProperties": {"type": "string"}, "description": "kanonikus → eredeti fejléc"},
    "decimal_mark": {"enum": [",", ".", null]},
    "summary": {"type": "object", "required": ["error","warning","info"]},
    "k_analysable": {"type": "integer"},
    "findings": {"type": "array", "items": {"type": "object",
      "required": ["code","severity","title","study","row","fields","detail","advice","source","blocking"],
      "properties": {
        "code": {"type": "string", "pattern": "^V\\d{3}$"}, "severity": {"$ref": "urn:szk:contract:common:1#/$defs/severity"},
        "title": {"type": "string"}, "study": {"type": ["string","null"]},
        "row": {"type": ["integer","null"], "minimum": 0, "description": "0-alapú adatsor; null = táblaszintű (V001, V015)"},
        "rows": {"type": "array", "items": {"type": "integer"}, "description": "többsoros szabályok (V007, V017)"},
        "row_uid": {"type": ["string","null"]}, "line": {"type": ["integer","null"], "description": "1-alapú fájlsor"},
        "fields": {"type": "array", "items": {"type": "string"}, "description": "kanonikus oszlopnevek"},
        "detail": {"type": "string"}, "advice": {"type": "string"}, "source": {"type": "string"},
        "blocking": {"type": "boolean", "description": "a sor kimarad az elemzésből"},
        "kb_id": {"$ref": "urn:szk:contract:common:1#/$defs/kbid"},
        "acknowledged": {"type": ["integer","null"], "description": "„Nem hiba — indoklás” döntés-ID (projekt.sqlite)"}}}},
    "excluded": {"type": "array", "items": {"type": "object", "required": ["study","row","reason"]}}
  } }
```

**Bemenet** (`szk.ma.validate-request/v1`):

```json
{ "schema": "szk.ma.validate-request/v1", "measure": "RR", "options": {"cc": 0.5, "cc_to": "only0", "drop00": null},
  "table": {"header": ["row_uid","study","e1","n1","e2","n2","rob","estimated","forras_oldal"],
            "rows": [["r7f3a2","Aronson 1948","4","123","11","139","low","nem","3"]], "decimal_mark": null},
  "base_sha256": "9f3a…" }
```

A cellák **nyers szövegként** mennek (`"12,3"`, `"2,000"`, `"NR"`); a `tableio` értelmezi őket (V003, V021,
V023).

### 4.4 `szk.ma.analysis-spec/v1` — felület → motor (`analyze --spec`; E3)

```json
{ "$id": "urn:szk:contract:ma.analysis-spec:1", "type": "object",
  "required": ["schema","name","outcome","data","options"],
  "properties": {
    "schema": {"const": "szk.ma.analysis-spec/v1"},
    "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$"}, "outcome": {"type": "string"},
    "purpose": {"enum": ["primary","sensitivity","subgroup","metaregression","exploratory"]},
    "prespecified": {"type": "boolean"}, "protocol_ref": {"type": ["string","null"]},
    "parent": {"type": ["string","null"], "description": "gyermek-futásnál az elsődleges spec neve"},
    "data": {"type": "object", "required": ["path"], "properties": {
      "path": {"$ref": "urn:szk:contract:common:1#/$defs/relpath"},
      "sha256": {"type": "string", "description": "commitnál kötelező; eltérésnél a motor 409-et ad (elavult)"}}},
    "options": {"type": "object", "additionalProperties": false,
      "description": "PONTOSAN a pipeline.DEFAULTS kulcsai (generált séma): measure, model, tau2, ci, pi, level, smd_vtype, j_method, cc, cc_to, drop00, mh, peto, rd_var, subgroup, common_tau2, moderators, metareg_test, cumulative, title, left_label, right_label, label_col, bias_min_k, trimfill_estimator, md_vtype, glass_vtype, gen_smd_vtype, pft_backtransform, h_centre, trimfill_trim_model, egger_ci_dist, begg_method, begg_continuity, metareg_robust, outliers + ÚJ (E10): mid {low, high}, assumed_risks [], plot_locale"},
    "filters": {"type": "object", "properties": {
      "include": {"type": "array", "items": {"type": "string", "pattern": "^[^=]+=.*$"}},
      "exclude": {"type": "array", "items": {"type": "string", "pattern": "^[^=]+=.*$"}}}},
    "kb_refs": {"type": "array", "items": {"type": "string"}}
  } }
```

**Névegyezés.** A plugin-javaslat `ht_centre` és `robust` nevet használt, a motor `results.json.options` viszont
`h_centre` és `metareg_robust` nevet. A spec ezért **pontosan a `DEFAULTS` kulcsait** használja, így egy futás
`results.json.options`-éből a spec visszaállítható („Újrafuttatás ugyanígy”).

A CLI két kapcsolója eltérő `dest`-et kap, ezeket a leképező kifejezett táblája kezeli:

| `DEFAULTS` kulcs | CLI kapcsoló | argparse `dest` |
|---|---|---|
| `h_centre` | `--ht-centre` | `ht_centre` |
| `metareg_robust` | `--robust` | `robust` |

A teljes spec ↔ argv leképezést a `metaelemzes/spec.py` állítja elő a `build_parser()` introspekciójával. A
`cmd_analyze` ma kézzel épít opció-dictet; ez is ugyanezt a függvényt hívja majd (E3). A teszt minden kapcsolót
oda-vissza ellenőriz, és a spec meg a kapcsolók ugyanazt a `results.json`-t adják.

### 4.5 `szk.ma.run/v1` — futás-leíró (`run.json`; `analyze --json-summary`)

```json
{ "schema": "szk.ma.run/v1", "run_id": "20261004T211200Z-a1f3c2", "mode": "commit",
  "spec": {"path": "05_elemzes/specs/o1_primary.json", "sha256": "…", "name": "o1_primary", "parent": null},
  "equivalent_argv": ["ma.py","analyze","--spec","05_elemzes/specs/o1_primary.json","--out","05_elemzes/o1/20261004T211200Z-a1f3c2","--project","."],
  "engine_version": "0.2.0", "data": {"path": "03_adatok/o1.csv", "sha256": "9f3a…", "rows": 13},
  "files": {"results": {"path": "…/results.json", "sha256": "…"}, "plot": {"path": "…/plot_data.json", "sha256": "…"}},
  "k": 13, "primary": {"model": "random", "display_text": {"hu": "0,49 [0,33; 0,73]", "en": "0.49 [0.33; 0.73]"}},
  "validation_summary": {"error": 0, "warning": 0, "info": 0}, "client_seq": 57, "elapsed_ms": 84,
  "started": "2026-10-04T21:12:00Z", "finished": "2026-10-04T21:12:00Z" }
```

Explore-futásnál `run_id: null`, és nincs `files`.

### 4.6 `szk.ma.plot/v2` — a kulcsszerződés (`plot_data.json`; E4)

Ugyanez a dokumentum táplálja a felület interaktív ábráit, a motor saját SVG-jét **és** a figure-forge `meta`
parancsát. Így a képernyő, az export és a kézirat garantáltan ugyanazt a számot mutatja.

```json
{ "$id": "urn:szk:contract:ma.plot:2", "type": "object",
  "required": ["schema","meta","measure","scale","level","axis","studies","summaries"],
  "properties": {
    "schema": {"const": "szk.ma.plot/v2"},
    "meta": {"type": "object", "required": ["engine_version","data_sha256"], "properties": {
      "engine_version": {}, "run_id": {"type": ["string","null"]}, "data_sha256": {}, "spec_sha256": {}}},
    "measure": {"type": "string"},
    "scale": {"type": "object", "required": ["analysis","ratio","null_analysis"], "properties": {
      "analysis": {"enum": ["identity","log","logit","atanh","asin_sqrt","pft"]}, "ratio": {"type": "boolean"},
      "null_analysis": {"$ref": "urn:szk:contract:common:1#/$defs/num"}, "null_display": {"$ref": "urn:szk:contract:common:1#/$defs/num"}}},
    "level": {"type": "number"},
    "axis": {"type": "object", "required": ["domain","ticks","title"], "properties": {
      "domain": {"type": "array", "minItems": 2, "maxItems": 2},
      "ticks": {"type": "array", "items": {"type": "object", "required": ["at","text"],
                "properties": {"at": {"type": "number", "description": "elemzési skálán"}, "text": {"type": "string"}}}},
      "title": {"$ref": "urn:szk:contract:common:1#/$defs/i18n"}}},
    "labels": {"type": "object", "properties": {"left": {}, "right": {}, "title": {}}},
    "columns": {"type": "array", "items": {"type": "object", "required": ["id","title"]}},
    "studies": {"type": "array", "items": {"type": "object",
      "required": ["row_uid","row_index","label","y","lo","hi","weight_pct","display","display_text"],
      "properties": {
        "row_uid": {"$ref": "urn:szk:contract:common:1#/$defs/row_uid"}, "row_index": {"type": "integer"},
        "study_id": {"type": ["string","null"]}, "label": {"type": "string"}, "section": {"type": ["string","null"]},
        "y": {"type": "number"}, "lo": {"type": "number"}, "hi": {"type": "number"},
        "weight_pct": {"type": "number"}, "weight_fixed_pct": {"$ref": "urn:szk:contract:common:1#/$defs/num"},
        "cells": {"type": "object", "additionalProperties": {"type": "string"}, "description": "kész szövegek: 4/123, 12.4 (3.1)"},
        "display": {"$ref": "urn:szk:contract:common:1#/$defs/display"},
        "display_text": {"$ref": "urn:szk:contract:common:1#/$defs/i18n"},
        "clip": {"type": "object", "properties": {"left": {"type": "boolean"}, "right": {"type": "boolean"}}},
        "flags": {"type": "object", "properties": {"estimated": {"type": "boolean"},
                  "rob": {"enum": ["low","some","high","critical",null]}, "zero_cell_corrected": {"type": "boolean"},
                  "influential": {"type": "boolean"}, "outlier": {"type": "boolean"}}},
        "source": {"type": "object", "properties": {"doc": {}, "page": {}, "locator": {}}}}}},
    "sections": {"type": "array", "items": {"type": "object", "required": ["id","title","row_uids","summary"]}},
    "summaries": {"type": "array", "items": {"type": "object",
      "required": ["id","kind","label","estimate","ci_lower","ci_upper","display","display_text"],
      "properties": {"kind": {"enum": ["overall","subgroup","sensitivity"]}, "model": {"enum": ["random","fixed","ivhet","mh","peto"]},
        "label": {"$ref": "urn:szk:contract:common:1#/$defs/i18n"}, "pi_lower": {}, "pi_upper": {},
        "display_text": {"$ref": "urn:szk:contract:common:1#/$defs/i18n"}, "pi_text": {}, "het_text": {}}}},
    "subgroup_test": {"oneOf": [{"type": "null"}, {"type": "object", "required": ["Q","df","p","text"]}]},
    "heterogeneity": {"type": "object", "properties": {"Q": {}, "df": {}, "p": {}, "I2": {}, "tau2": {}, "text": {}}},
    "funnel": {"type": "object", "properties": {
      "points": {"type": "array", "items": {"type": "object", "required": ["row_uid","x","se"]}},
      "filled": {"type": "array"}, "center": {"type": "number"}, "se_max": {"type": "number"},
      "pseudo_ci": {"type": "array", "description": "[[x, se], …] töréspontok"},
      "contours": {"type": "array", "items": {"type": "object", "required": ["p","polygon"],
                   "description": "p = 0.10 / 0.05 / 0.01 sávok kész poligonként — a JS nem számol x = c ± z·se-t"}},
      "tests_text": {"$ref": "urn:szk:contract:common:1#/$defs/i18n"}}},
    "doi": {"type": "object", "properties": {"points": {}, "lfk": {}, "category": {}, "lfk_text": {}}},
    "loo": {"type": "array", "items": {"type": "object", "required": ["omitted_row_uid","estimate","ci_lower","ci_upper","display_text"]}},
    "influence": {"type": "array", "items": {"type": "object", "required": ["row_uid"], "properties": {
      "rstudent": {}, "dffits": {}, "cook_d": {}, "cov_ratio": {}, "hat": {}, "dfbetas": {},
      "influential": {"type": "boolean"}, "decimals": {"type": "integer"}}}},
    "cumulative": {"oneOf": [{"type": "null"}, {"type": "object", "required": ["key_label","entries"]}]},
    "bubble": {"oneOf": [{"type": "null"}, {"type": "object", "required": ["moderator","points","line","band"],
      "properties": {"band": {"description": "[[x, alsó, felső], …] — a motor számolja a koefficiens-kovarianciából"},
                     "coef_text": {"$ref": "urn:szk:contract:common:1#/$defs/i18n"}}}]},
    "notes": {"type": "array", "items": {"$ref": "urn:szk:contract:common:1#/$defs/i18n"}}
  } }
```

**Kompatibilitás.** A motor alapból v2-t ír; a `--plot-schema v1` kapcsolóval a mai kulcsokat is. A felület mindkét
változatot olvassa; v1-nél nincs lefúrás `row_uid`-ra, és nincs kontúr-poligon. A **`row_uid`** a CSV `row_uid`
oszlopából jön (`r` + 6 base32 karakter). Ha a CSV-ben nincs ilyen oszlop, determinisztikus hash készül:
`sha1(label|row_index)[:6]`.

### 4.7 Átváltás — `szk.ma.convert-request/v1` → `szk.ma.convert-result/v1`

```json
{ "schema": "szk.ma.convert-request/v1", "kind": "median_to_mean_sd",
  "inputs": {"n": "40", "median": "12,5", "q1": "10", "q3": "15"}, "method": "luo",
  "target": {"dataset": "03_adatok/o1.csv", "row_uid": "r7f3a2", "fields": {"mean": "m1", "sd": "sd1"}} }
{ "schema": "szk.ma.convert-result/v1", "kind": "median_to_mean_sd", "engine_version": "0.2.0",
  "outputs": {"mean": 12.43, "sd": 3.71}, "estimated": true,
  "method": {"id": "luo2018+wan2014", "citation": "Cochrane Handbook 6.5.2.5", "function": "conversions.mean_from_median / sd_from_median", "kb_refs": ["K-…"]},
  "assumptions": ["közel normális eloszlás"], "warnings": ["a medián Q1-hez közelebb: ferde eloszlás gyanúja (V013)"] }
```

A `kind` értékkészlete a motor `convert` alparancsaiból generálódik. Jelenleg ezek tartoznak bele:

| Csoport | `kind` értékek |
|---|---|
| eloszlás és variancia | `median_to_mean_sd`, `se_to_sd`, `ci_to_sd`, `ci_to_se`, `p_to_se`, `smd_variance` |
| csoportok és változás | `combine_groups`, `change_sd`, `corr_from_change`, `split_shared_control`, `paired_sums` |
| hatásméret-átváltás | `t_to_d`, `logor_to_d`, `d_to_logor`, `r_to_d`, `d_to_r` |
| v2 (E13) | `cstat`, `oe` |

Az `estimated` **mindig a motor döntése**: becslés `true`, algebrai átalakítás `false`.

### 4.8 `szk.ma.provenance/v1` — cellaszintű eredet (oldalfájl: `03_adatok/<kimenet>.prov.json`)

```json
{ "$id": "urn:szk:contract:ma.provenance:1", "type": "object", "required": ["schema","table","table_sha256","cells"],
  "properties": {
    "schema": {"const": "szk.ma.provenance/v1"}, "table": {"$ref": "urn:szk:contract:common:1#/$defs/relpath"},
    "table_sha256": {"description": "a CSV hash-e, amelyhez ez az oldalfájl tartozik (X022 őrzi)"},
    "cells": {"type": "array", "items": {"type": "object", "required": ["row_uid","field","method"],
      "properties": {
        "row_uid": {}, "field": {"type": "string", "description": "kanonikus oszlopnév"},
        "value_as_entered": {"type": ["string","null"]},
        "method": {"enum": ["reported","calculated","estimated","digitized","imputed","author_contact","reconciled","external_edit"]},
        "estimated": {"type": "boolean", "description": "true: estimated | digitized | imputed"},
        "source": {"type": "object", "properties": {"doc": {"description": "documents.json id"},
                   "page": {"type": ["integer","null"]}, "locator": {"examples": ["Table 2","Fig. 3B","Suppl. S4"]},
                   "quote": {"type": "string", "maxLength": 500}}},
        "conversion": {"type": ["object","null"], "description": "a convert-request + convert-result lényege, motorverzióval"},
        "reconciliation": {"type": ["object","null"], "description": "kulcs, A/B érték, választás, indoklás"},
        "extracted_by": {}, "extracted_at": {}, "verified_by": {"type": ["string","null"]}, "verified_at": {},
        "history": {"type": "array", "maxItems": 50, "description": "korábbi bejegyzések (érték + method + ki/mikor)"}}}}
  } }
```

- **Sorszintű `estimated`:** ha egy sor bármely hatásméret-releváns cellája `estimated: true`, a szerver a sor
  `estimated` oszlopát `igen`-re állítja. Így a V018 és az `--exclude estimated=igen` változatlanul működik; az
  egyezést az X013 őrzi. A `forras_oldal` oszlop a cellák oldalainak összefoglalója marad, visszafelé
  kompatibilisen.
- **Kétfájlos atomicitás:** a CSV és a `.prov.json` mindkét `tmp`-fájlja elkészül, majd `os.replace` következik,
  előbb a CSV-re. Ha közben összeomlás történik, az oldalfájl `table_sha256`-ja nem egyezik. Ezt a
  betöltés felismeri; az eredet `row_uid` + `field` kulcsú, így a nem érintett cellákra érvényes marad (X022).
- **Dokumentum-jegyzék** (`03_adatok/documents.json`): `{schema: "szk.ma.documents/v1", docs: [{id: "pmid:31234567" | "doi:…" | "file:<rel>", root: "project" | "composer_outdir", path, sha256, pages}]}`.
  Ez az aláírt fájl-URL-ek allowlistje is.

### 4.9 Kettős kinyerés — `szk.ma.compare-result/v1` és `szk.ma.consensus/v1` (E6)

```json
{ "schema": "szk.ma.compare-result/v1", "key": ["study_id","arm"],
  "summary": {"rows_a": 24, "rows_b": 24, "matched": 24, "cells_compared": 312, "agree": 296, "disagree": 16,
              "agreement_pct": 94.9, "by_field": {"sd1": {"compared": 24, "disagree": 5},
              "rob": {"compared": 24, "disagree": 4, "kappa": 0.71, "kappa_ci": [0.52, 0.90]}}},
  "disagreements": [ { "key": "NCT0456|T", "row_uid_a": "r11aa", "row_uid_b": "r22bb", "field": "sd1",
      "a": "0.42", "b": "4.2", "a_value": 0.42, "b_value": 4.2,
      "kind": "value", "hint": "10× eltérés: tizedesjel vagy mértékegység (V012 jellegű)",
      "impact": {"measure": "SMD", "yi_a": 0.31, "yi_b": 0.03, "text": "SMD 0.31 → 0.03"} } ],
  "only_a": ["NCT0123|T"], "only_b": [] }
```

- **`kind` értékei:** `value`, `missing_a`, `missing_b`, `format_only` (azonos szám más írásmóddal, a `tableio`
  szerint), `parse`, `category`.
- **Python számol:** a `hint` (a V011/V012 heurisztikáival), az `impact` és a κ is a motorból jön; a JS nem
  következtet.
- **Konszenzus:** a döntések a `kettos/<kimenet>.consensus.json`-ba kerülnek (`szk.ma.consensus/v1`, benne
  `{key, field, chosen: a|b|other, value, reason, actor, ts}`). A konszenzus-CSV cellái `reconciled` eredetet
  kapnak.

### 4.10 `szk.ma.studies/v1` — vizsgálat ↔ jelentés (`03_adatok/studies.json`)

```json
{ "schema": "szk.ma.studies/v1",
  "studies": [ { "study_id": "NCT0456", "label": "Smith 2010", "registration": "NCT0456",
                 "design": "rct_parallel", "outcomes": ["o1","o2"],
                 "reports": [ {"rec_id": "pmid:20123456", "role": "primary", "doc": "pmid:20123456"},
                              {"rec_id": "pmid:21987654", "role": "secondary"} ] } ] }
```

- **PRISMA-értékek:** I = `len(studies)`; J = a `reports` uniója, ami egyezzen a composer `included_reports`-jával.
- **Felhasználás:** a motor `prisma check --studies` (E9), a composer `--study-map` (C1), a RoB-egységkulcsok
  (`study_id`) és a validator `--route` bemenete (`design`).

### 4.11 Felület ↔ validator (PR-V1)

**`szk.instrument/v1`** — eszköz-leírás (`appraise.py --schema <tool> --json`, `checklist.py --schema probast|tripod --json`):

```json
{ "schema": "szk.instrument/v1", "key": "probast-ai", "name": "PROBAST+AI", "validator_version": "1.1.0",
  "reference_sha256": "…", "unit": "model",
  "passes": [ {"id": "development", "label": "Quality (development)"}, {"id": "evaluation", "label": "Risk of bias (evaluation)"} ],
  "answers": [ {"value": "yes", "label": "Y", "aliases": ["y"]}, {"value": "probably_yes", "label": "PY", "aliases": ["py"]},
               {"value": "probably_no", "label": "PN"}, {"value": "no", "label": "N"}, {"value": "no_information", "label": "NI"} ],
  "verdicts": ["low","high","unclear"],
  "domains": [ {"id": "1", "title": "Participants and data sources", "passes": ["development","evaluation"], "applicability": true} ],
  "items": [ {"id": "1.1", "domain": "1", "pass": "development", "key": "development/1.1",
              "text": "…", "tags": ["router"], "applies_to": null} ],
  "rollup": {"algorithm": "none", "note": "PROBAST+AI: holistic overall judgement"},
  "counts": {"parsed": 34, "published": 34, "per_pass": {"development": 16, "evaluation": 18}} }
```

- **`rollup.algorithm`:**

  | Érték | Eszközök |
  |---|---|
  | `published` | AMSTAR 2, GRADE |
  | `count` | NOS-csillagok |
  | `conservative` | RoB 2, ROBINS-I/E, QUADAS-2: a validator „amit a válaszok kikényszerítenek” logikája, **nem hivatalos** |
  | `none` | PROBAST+AI, TRIPOD+AI |

  A felület ezt szó szerint kiírja.
- **TRIPOD+AI:** a `status_vocab` értékei `present`, `partial`, `missing`, `not_applicable`; az
  `applies_to` értéke `D`, `E` vagy `D;E`.
- **AMSTAR 2:** a `partial_yes` és a `probably_yes` **külön kanonikus érték**; ez a H4 szerkezeti javítása.

**`szk.appraisal/v1`** — a munkapad tárolja; a validator ≥ 1.1 közvetlenül olvassa:

```json
{ "schema": "szk.appraisal/v1", "tool": "probast-ai", "scope": "both", "instrument_sha256": "…",
  "target": {"study_id": "LEE2023", "model": "XGB-PE", "outcome": null, "result": null, "index_test": null},
  "assessor": "SzK", "second_assessor": null, "status": "draft", "origin": "human",
  "answers": { "development/1.1": {"value": "probably_yes", "evidence": {"text": "registry", "doc": "pmid:…", "page": 4}},
               "evaluation/1.1":  {"value": "yes", "evidence": {"page": 9}} },
  "domain_judgements": [ {"domain": "1", "pass": "evaluation", "judgement": "high", "implied": null,
                          "rationale": "…", "override_reason": null, "decision_id": null} ],
  "applicability": [ {"domain": "1", "judgement": "low", "rationale": "…"} ],
  "overall": {"judgement": "high", "rationale": "…", "implied": null, "override_reason": null},
  "created": "…", "updated": "…" }
```

- A válaszkulcs `propertyNames` mintája `^((development|evaluation)/)?[0-9A-Za-z.\-]+$`. A menettel minősített
  kulcs **szerkezetileg kizárja** a H2 menetek közti számlálást.
- A `value` csak az eszköz `answers[].value`-jainak egyike lehet, szabad szöveg nem.
- `origin`:

  | Érték | Jelentés |
  |---|---|
  | `human` | ember töltötte ki |
  | `ai_draft` | Claude- vagy skill-vázlat (5.8); csak emberi jóváhagyás után válhat `complete` státuszúvá |

**`szk.appraisal-result/v1`** — `--verify` / `--rollup <f.json> --json` kimenete:

```json
{ "schema": "szk.appraisal-result/v1", "tool": "rob2", "validator_version": "1.1.0", "legacy": false,
  "complete": false, "expected": 22, "answered": 19,
  "missing": [{"item": "2.7", "pass": null}], "invalid": [],
  "domains": [ {"domain": "2", "implied": "high", "forced_by": ["2.6"], "unknown_at": [], "routers": ["2.1"]} ],
  "overall": {"implied": "high", "algorithm": "conservative", "lines": ["…"]},
  "amstar2": null, "grade": null, "nos": null,
  "conventions": {"amstar2.partial_yes_critical": "meets"},
  "guards": [ {"id": "H3", "message": "publication bias 'suspected' unresolved", "effect": "rollup_unreliable"} ],
  "notes": ["This is NOT the published RoB 2 flowchart."] }
```

- A `grade` blokkban `unresolved: ["publication_bias"]` szerepel, ha a 4.14 szerinti „suspected” feloldatlan.
- **Legacy (1.0.0, bridge) mód:**
  - **Írás.** A munkapad a validator saját sablonjával megegyező Markdown-táblát ír, PROBAST-nál menetenként
    külön `###` szakasszal. A bizonyíték-oszlopba soha nem kerül válaszszótárbeli szó (csak `[E3]`-hivatkozás),
    hogy a soron belüli keresés ne tévedjen.
  - **Olvasás.** A stdout-ot rögzített mintákkal olvassa.
  - **Teljesség.** Ebben a módban a munkapad **saját** JSON-jából számolja (H1/H2-őr).
  - **Jelölés.** Az eredmény `legacy: true`, a bekapcsolt `guards` listájával.

### 4.12 Felület ↔ figure-forge (PR-F2, F3, F5)

```json
{ "schema": "szk.figure-request/v1", "kind": "forest",
  "input": {"plot": "05_elemzes/o1/20261004T211200Z-a1f3c2/plot_data.json", "plot_sha256": "…"},
  "select": {"sections": ["random","alternate"], "columns": ["events","weight"], "show_pi": true, "order": "input"},
  "style": {"profile": "nature", "width": "double", "dpi": 600, "palette": "okabe-ito", "locale": "en", "typography": true},
  "out": {"dir": "06_kezirat/abrak", "stem": "fig2_forest", "formats": ["svg","pdf","tiff"]},
  "qc": {"max_iter": 8, "require_clean": true} }
{ "schema": "szk.figure-result/v1", "kind": "forest", "stem": "fig2_forest", "ff_version": "0.3.0",
  "formats": {"svg": "06_kezirat/abrak/fig2_forest.svg", "tiff": "…"}, "dpi": 600, "width_mm": 183, "height_mm": 121,
  "clean": true, "labels_checked": 64, "residual_violations": [],
  "typography": {"enabled": true, "substitutions": [{"from": "-", "to": "−", "n": 2}]},
  "glyphs": {"ok": true, "missing": [], "font": "Arial"},
  "editability": {"svg": {"editable": true, "outlined_text_groups": 0, "font_fallback_stack": true, "named_layers": 6}},
  "numbers": {"ok": true, "checked": 31, "mismatches": []},
  "source": {"plot_sha256": "…", "run_id": "20261004T211200Z-a1f3c2"} }
```

- **`kind` értékei:** `forest`, `funnel`, `doi`, `bubble`, `cumulative`, `loo`, `rob_traffic`, `rob_summary`,
  `flowchart`.
- **`numbers`:** minden kapott `display_text` a tipográfiai normalizálás után szó szerint szerepel-e az SVG-ben.
  A szerver ettől függetlenül maga is újraellenőrzi (6.7).
- **`szk.rob-summary/v1`:** a munkapad adja a figure-forge `rob` parancsának. Tartalma `{tool, outcome, domains[], studies[{study_id, label, weight_pct, judgements{}, overall}], scale[], weighted}`; a súlyokat a motor adja.
- **`szk.ff.flowchart/v1`:** a mai figure-forge flowchart-specifikáció formalizálása (`direction`,
  `nodes[{id,text,w,h,x,y,shape,color}]`, `edges[{from,to,label}]`, 0–100-as vászon). Változtatás nincs, csak a
  séma kerül be a `contracts/`-ba.

### 4.13 Felület ↔ composer — `szk.prisma-flow/v1` (a composer `export --format flow-json` + C1)

```json
{ "schema": "szk.prisma-flow/v1", "project": "glp1", "generated": "…", "composer_version": "1.5.0",
  "identified_databases": 1234, "identified_registers": 12, "identified_other": 0,
  "dedup_removed": 300, "automation_removed": 0, "removed_before_screening_n": 0,
  "screened": 946, "excluded_screening": 860, "sought_for_retrieval": 86, "not_retrieved": 4,
  "assessed_eligibility": 82, "excluded_eligibility": 57, "excluded_eligibility_reasons": {"wrong population": 30, "…": 25},
  "included": 25, "included_reports": 25, "included_studies": 18, "undecided": 0, "other_methods": null }
```

- **Kompatibilitás:** a C1 előtt a `schema` mező hiányzik; a fogyasztó ilyenkor a mezőkből ismeri fel a
  formátumot. Az `included` a bevont **jelentések** (J) száma; ezt a C1 kifejezetté teszi.
- **Leképezés:** a motor dobozbetűire a táblázat: A1/A2/D1–D3/B/C/E/F/G/H/J/I. Az I a `studies.json`-ból jön,
  ha a composernek nincs térképe.

### 4.14 GRADE és SoF — `szk.ma.grade/v1`, `szk.ma.sof/v1`

```json
{ "schema": "szk.ma.grade/v1", "outcome_id": "o1", "importance": "critical", "run_id": "20261004T211200Z-a1f3c2",
  "start": "high", "start_reason": "RCT",
  "domains": {
    "risk_of_bias":     {"rating": "serious",     "step": -1, "rationale": "…", "advisory": {"high_rob_weight_pct": 41.2}},
    "inconsistency":    {"rating": "not serious", "step": 0,  "rationale": "…", "advisory": {"I2": 92.1, "pi_crosses_null": true}},
    "indirectness":     {"rating": "not serious", "step": 0,  "rationale": "…"},
    "imprecision":      {"rating": "not serious", "step": 0,  "advisory": {"ci_crosses_null": false, "ci_crosses_mid": false, "ois_met": true}},
    "publication_bias": {"rating": "suspected",   "step": null, "status": "unresolved",
                         "advisory": {"k": 13, "tests_interpretable": true, "harbord_p": 0.23, "peters_p": 0.20, "lfk": -4.10}} },
  "upgrades": {"large_effect": false, "dose_response": false, "opposing_confounding": false},
  "certainty": null, "consistency_warning": null, "validator_rollup": null }
```

- **`rating`:** a négy fő doménnél `not serious`, `serious` vagy `very serious`; a publikációs torzításnál
  `undetected`, `suspected` vagy `strongly suspected`.
- **`step`:** 0, −1, −2 vagy `null`. A `suspected` értéknél `status: unresolved`, amíg a felhasználó 0-t vagy
  −1-et nem választ indoklással. A `certainty` addig `null`, és a rögzítés tiltott.
- **Tárolás:** a `projekt.add_grade` előjeles lépés-szöveggel kapja (például `"−1 serious: …"`), így a meglévő
  `grade_consistency` változatlanul ellenőriz.
- **SoF (`06_kezirat/sof/<kimenet>.sof.json`):** relatív hatás (`display_text`), résztvevők és k, design,
  alapkockázatonként abszolút hatás /1000 CI-vel (a motor `sof()` számolja; kontroll-pool az alap, külső
  felvehető), bizonyosság, lábjegyzetek. Minden cellánál ott a forrásmező neve.

### 4.15 `szk.ma.project-audit/v1` — X-szabályok (`ma.py project audit <mappa> --json`; E8)

```json
{ "schema": "szk.ma.project-audit/v1", "project": "glp1", "generated": "…",
  "summary": {"error": 2, "warning": 3, "info": 0},
  "findings": [ { "code": "X001", "severity": "error", "stage": "S08", "outcome": "o1",
                  "title": "Az elsődleges commit-futás régebbi, mint az adattábla",
                  "detail": "run 20261003T180200Z-4c0d…: data 4c0d… ≠ 03_adatok/o1.csv 9f3a…",
                  "artifacts": ["05_elemzes/o1/20261003T180200Z-4c0d1e/run.json", "03_adatok/o1.csv"],
                  "suggested_command": ["ma.py","analyze","--spec","05_elemzes/specs/o1_primary.json"],
                  "kb_refs": ["D-S08-…"] } ],
  "amstar2_hints": {"4": {"suggested": "partial_yes", "evidence": ["PRISMA-S: 4 adatbázis, regiszter 0"]}} }
```

### 4.16 `szk.ma.activity/v1` — hash-láncolt tevékenységnapló (`07_ellenorzes/activity.jsonl`, soronként)

```json
{ "schema": "szk.ma.activity/v1", "seq": 412, "ts": "2026-10-04T21:12:00Z", "actor": "user:SzK",
  "action": "analyze.commit", "argv": ["ma.py","analyze","--spec","05_elemzes/specs/o1_primary.json","--out","…"],
  "inputs": {"03_adatok/o1.csv": "9f3a…", "05_elemzes/specs/o1_primary.json": "…"},
  "outputs": {"05_elemzes/o1/20261004T211200Z-a1f3c2/results.json": "…"},
  "result": {"exit_code": 0, "summary": "k=13, RR 0.49 [0.33; 0.73]"}, "prev": "c81e…" }
```

- **Kizárások:** a sorokban **nincs cellaérték** (adatvédelem). Az explore-futások nem kerülnek bele.
- **Külső szerkesztés:** ha a változásfigyelő külső írást észlel, `actor: "external"` sor keletkezik, a fájl új
  hash-ével.
- **Kiterjesztés az ágensekre:** a motor CLI-je opcionálisan maga is hozzáfűz (E7). A
  `MA_ACTIVITY_LOG=1` környezeti változóval az ágensek CLI-hívásai is a láncba kerülnek.

### 4.17 `szk.ma.project/v1` és `szk.ma.audit-bundle/v1`

- **`ma-projekt.json`:** `{schema, title, question{P,I,C,O}, review_type: intervention | exposure | diagnostic | prognostic_factor | prediction_model, data_class: A | B | C, outcomes[{id, name(i18n), data, measure, critical, primary_spec, grade_start}], appraisal_tools[], composer{project, outdir}, doc_roots[], locale, conventions{amstar2_partial_yes_critical: meets | weakness, grade_suspected: unresolved}}`.
- **Audit-ZIP `manifest.json`:** `{schema, project, created, data_class, redactions[], versions{engine, selftest{checks, fail}, plugins{…}}, files[{path, sha256, bytes, role}], runs[{run_id, outcome_id, data_sha256, spec_sha256}], activity_head, rerun_scripts["rerun.cmd","rerun.sh"], kb_snapshot{ids[], db_sha256}, excluded[{pattern, reason}]}`.
  - A ZIP determinisztikus: rendezett bejegyzések, rögzített időbélyeg (1980-01-01), `ZIP_DEFLATED`. Ugyanaz a
    projektállapot ugyanazt a bájtsort adja.
  - A KB teljes szövege soha nem kerül bele, csak azonosítók és az adatbázis hash-e.

### 4.18 `szk.facts/v1` — motor → presubmit (v2; E10 + P1)

```json
{ "schema": "szk.facts/v1", "source": {"run_id": "20261004T211200Z-a1f3c2"},
  "facts": [ { "id": "o1.primary.estimate", "outcome": "o1", "kind": "estimate_ci",
               "value": {"est": 0.4894, "lo": 0.3301, "hi": 0.7257}, "decimals": 2,
               "patterns": ["RR 0.49 (95% CI 0.33–0.73)", "0.49 [0.33; 0.73]", "0,49 (95% CI: 0,33–0,73)"],
               "tolerance": 0.005 },
             { "id": "o1.I2", "kind": "percent", "value": {"value": 92.1}, "decimals": 0, "patterns": ["I² = 92%"] } ] }
```

### 4.19 Verzió-egyeztetés

| Fogyasztó ← termelő | Most (MVP) | PR után |
|---|---|---|
| munkapad ← motor validálás | `api.validate_table` (E2 a motorral együtt jön) | `szk.ma.validation/v1` |
| munkapad, figure-forge ← motor ábra | `plot_data` v1 (séma nélkül) olvasása is | `szk.ma.plot/v2` |
| motor ← munkapad elemzés | spec → argv (`spec.py`) | `analyze --spec` |
| munkapad ← validator | legacy bridge (1.0.0) + H1–H4 őrök | `szk.instrument/v1`, `szk.appraisal-result/v1` (V1) |
| figure-forge ← munkapad | csak `audit` (F1 után) és `flowchart` | `szk.figure-request/v1` (F2/F3/F5) |
| munkapad ← composer | `flow-json` séma nélkül | `szk.prisma-flow/v1` (C1) |
| presubmit ← motor | — | `szk.facts/v1` (P1) |

### 4.20 Sodródás-őrök

1. **Futásidőben:** a kézfogás szerződésenkénti `sha256`-ja (4.1).
2. **A marketplace CI-ben:** a `plugins/*/contracts/*.schema.json` azonos nevű fájljai bájtra egyeznek (M1).
3. **A motor repójának CI-jében:** opcionális job, amely kicheckoutolja a `szk-plugins`-t, és a
   `metaelemzes/contracts/` közös sémáit bájtra összeveti.
4. **Termelői oldalon:** minden termelő a saját selftestjében validálja a kimenetét a saját másolatával.

---
## 5. Plugin-integráció és önálló mód

### 5.0 A tervezés közben igazolt hibák (az adapterek őrei és a PR-ek ezekre épülnek)

| # | Hol | Megállapítás (kódolvasás vagy futtatás) | Kezelés |
|---|---|---|---|
| H1 | validator `checklist.py --verify`, TRIPOD+AI | üres sablonra „1/52 answered”: a 11. tétel címében („How missing data were handled”) a sorszintű keresés a `Missing` státuszszót találja meg | bridge-őr: a teljességet a munkapad számolja; PR-V3 |
| H2 | validator `checklist.py --verify`, PROBAST+AI | csak a fejlesztési táblát kitöltve „32/34 answered”: a két menet közös azonosítói (1.1–4.x) egymást teljesítik; bizonyíték-szövegben álló „no” is válasznak számít | menettel minősített kulcs + cella-alapú olvasás; PR-V3 |
| H3 | validator `appraise.py rollup_grade` | csak a „serious”/„very serious” előtag minősít le, így a publikációs torzítás „Suspected/Strongly suspected” értéke sosem | `rollup_unreliable` őr + motor `grade_consistency`; PR-V2 |
| H4 | validator `appraise.py _norm` + `rollup_amstar2` | a „py” normalizált alakja „probably yes” (nem gyengeség), a „Partial yes” kritikus tételen viszont nem kritikus gyengeség; ugyanaz a válasz kétféle besorolást adhat, és a KB AMSTAR2-00 projektkonvenciójával is ütközik | kanonikus `partial_yes` érték + explicit konvenció; PR-V4 |
| H5 | figure-forge `ff.py:28` | modulszintű `import ff_style` (→ matplotlib), ezért az `audit` matplotlib nélkül sem fut, pedig az `ff_editable`/`ff_typography` stdlib | állapot `unusable`, ok megnevezve; PR-F1 |
| H6 | figure-forge `forest` | nincs gyémánt, PI, alcsoport, szövegoszlop; nincs funnel/Doi; csak lapos rekordokat olvas | a meta-ábrák csak F2 után mennek figure-forge-dzsal |
| H7 | composer `scripts/{collect,lookup,prisma}` | shebang: `#!/Users/szili/anaconda3/bin/python3`; a `save_state` nem atomikus; a folyamatábra „Studies included” doboza a jelentések számát (J) mutatja | explicit interpreterrel hívjuk, csak olvasunk, újrapróbálunk; PR-C1–C4 |
| H8 | science-monitor `serve.py` | a `GET /` token nélkül adja a dashboardot az adattal és a tokennel együtt; az Origin-feltétel (`startswith("http://localhost:0")`) gyakorlatilag nem szűr; a port fix 8787 | a munkapad erősebb mintát használ (7.2); opcionális PR-S1 |
| H9 | motor | a V-megállapításokban nincs sor/oszlop; a `plot_data.json`-ban nincs `row_index`/séma, a feliratok magyarok; az SVG-ben nincsenek rétegek; a `doi.svg` kötőjel-mínuszt ír; a meta-regresszióhoz nincs kovariancia; opciónév-eltérés (`--ht-centre`→`h_centre`, `--robust`→`metareg_robust`) | E2–E5, E3 |
| H10 | Anamnézis-app | `localStorage('anam_cases')` betegeseteket tárol (a `file://` oldalak Chromiumban közös tároló-origón osztoznak) | ezt a mintát **nem** vesszük át (7.6) |
| H11 | vault | a `~/Documents/claude` alatt (mélység 2) minden projektet `git add -A` + push a munkamenet végén | 7.5 rétegek; PR-VA1 |

### 5.1 Felderítés, interpreter-választás, kézfogás

**Feloldási sorrend pluginonként.** A marketplace `bin/_resolve.sh` sorrendjét követi, hogy a terminál-indító és a
munkapad ugyanazt a kódot futtassa:

1. `SZK_<PLUGIN>_HOME` környezeti változó, illetve a `ma-projekt.json` `plugins.<név>` kulcsa;
2. git munkapéldány: `~/Documents/claude/szk-plugins/plugins/<p>`;
3. marketplace-klón: `~/.claude/plugins/marketplaces/szk-plugins/plugins/<p>`;
4. a legújabb telepített cache: `~/.claude/plugins/cache/szk-plugins/<p>/<verzió>`;
5. best effort: `~/.claude/plugins/installed_plugins.json`. Ez nem nyilvános formátum, ezért csak tartalék.

**Interpreter.**

- **Stdlib-pluginok** (validator, composer `prisma`, presubmit): a munkapad saját `sys.executable`-je. A composer
  szkriptjeit **explicit interpreterrel** hívjuk, a shebang megkerülésével (H7).
- **figure-forge:** keresési sorrend: `FIGURE_FORGE_PYTHON` → `SZK_PYTHON` → Anaconda/miniconda →
  `~/.claude/.venv` → `python3`/`python`/`py -3`.
  - Mindegyik jelöltet a ténylegesen szükséges importtal szondázzuk: `import matplotlib, numpy, pandas`; PPTX-hez
    `pptx`, TIFF-hez `PIL`.
  - A Windows Store-csonkot kiszűrjük.

**Kézfogás.** A `<szkript> --capabilities` 5 s-os időkorláttal fut (4.1).

- **Ha a kapcsoló hiányzik:** a `plugin.json` `version` mezője és egy `--help`-szonda alapján `legacy` állapot
  lesz, a verzióhoz rögzített bridge-adapterrel és a H-őrökkel. A legacy őröket ilyenkor a beépített táblából
  kapcsoljuk; újabb pluginnál a `known_issues` alapján.
- **Gyorsítótár:** a futásidejű mappában; a kulcs az útvonal, az mtime és az interpreter. A felület
  „Újraszondázás” gombja frissíti.

**Alfolyamat-higiénia.** Részletesen a 7.3-ban:

- `subprocess.run([...], shell=False)`;
- Windows-on `CREATE_NO_WINDOW`;
- `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8` (a cp1250 ellen);
- a `PYTHONPATH` és a `PYTHONHOME` nem öröklődik;
- `cwd` = a futásidejű tmp-mappa;
- időkorlát: validator 30 s, figure-forge 300 s, presubmit 120 s.

### 5.2 Képesség-mátrix (funkció × önálló × pluginnal)

| Funkció | Önálló (motor + KB) | Pluginnal |
|---|---|---|
| Tábla, élő V-validáció, átváltó, eredet, kettős kinyerés | teljes | — |
| Forest / funnel / Doi / LOO / befolyás / kumulatív / buborék, lefúrás | teljes (interaktív SVG) | — |
| Publikációs ábra | motor-SVG (EN felirat, rétegek, U+2212) + böngészős PNG „ELŐNÉZET — QC NÉLKÜL” vízjellel | figure-forge: SVG/PDF/TIFF/PNG/PPTX 600 dpi, címke-QC, szerkeszthetőség, tipográfia, `numbers` |
| Ábra-audit | stdlib SVG-ellenőrzés a motor-SVG-n | `ff.py audit` (F1 után matplotlib nélkül is) |
| RoB 2 / ROBINS-I/E / QUADAS-2 / NOS / QUIPS / JBI | doménszintű ítélettábla (`fallback_instruments.json`), forgalmi lámpa, `rob` oszlop karbantartása | jelző-kérdéses űrlapok, teljesség, implikált ítélet, NOS-csillagok |
| PROBAST+AI | doménszintű ítélet (4 domén × Low/High/Unclear, két menet) + alkalmazhatóság | 16 + 18 jelző-kérdés, menettel minősített teljesség |
| TRIPOD+AI | nem elérhető (a tétellista a validator referenciájából jön) | 52 altétel D/E szűréssel, hőtérkép, hiánylista, saját kézirat |
| GRADE + SoF | motor-tanácsadó (`grade_help`), `grade_consistency`, SoF a motorból | + validator `rollup_grade` (V2 után megbízható) |
| AMSTAR 2 | KB AMSTAR2 lista + `project audit` bizonyíték-javaslatok + kézi besorolás + motor `amstar2_consistency` | validator `rollup_amstar2` (V4 után konvencióval) |
| PRISMA | kézi űrlap + élő P001–P016 + X014/X015 | composer-állapot élő olvasása; figure-forge folyamatábra |
| Kézirat-számok | — | presubmit `--facts` (v2) |

### 5.3 figure-forge

**Ma (0.2.1):**

- **Funkciók:** `audit` a motor-SVG-ken (F1 után matplotlib nélkül is), `flowchart` a PRISMA-hoz (a motor E9
  specjével).
- **Korlát:** a meglévő `forest` alparancs nem rajzol helyes meta-forestet (H6). Ezért a meta-ábrák exportja F2
  előtt a motor-SVG.

**F2/F3/F5 után:**

- `ff.py meta --request <szk.figure-request/v1> --json` rajzol forestet, funnelt, Doit, buborékot, LOO-t és
  kumulatívot, mind a `szk.ma.plot/v2`-ből.
- `ff.py rob` a `szk.rob-summary/v1`-ből rajzol.
- A plugin **statisztikát nem számol, nem kerekít újra**: a címke-QC csak elrendez, a tipográfia csak ismert,
  naplózott cseréket végez (U+2212, en dash).
- A `numbers` blokk igazolja, hogy minden `display_text` szó szerint az SVG-ben van; a szerver ezt maga is
  újraellenőrzi (6.7).
- Ha az exportált ábra `plot_sha256`-ja nem az aktuális futásé, az ábra ELAVULT (X002).

### 5.4 validator — RoB-eszközök, PROBAST+AI, TRIPOD+AI, GRADE, AMSTAR 2

**Két mód, közös belső formátum** (`szk.appraisal/v1`):

- **`json`** (≥ 1.1, V1): `appraise.py --schema/--verify/--rollup … --json`, `checklist.py` ugyanígy.
- **`bridge`** (1.0.0):
  1. a munkapad a validator sablonjával egyező Markdown-táblát ír a futásidejű tmp-be;
  2. lefut a `--verify`, majd a `--rollup`;
  3. a stdout-ot rögzített mintákkal parszoljuk.
  - Golden tesztek rögzítik a pontos 1.0.0-s kimeneteket, köztük a H1–H4 reprodukcióját.
  - A bridge-adapter **a hibás plugin mellett is helyes eredményt ad**: a teljességet saját maga számolja, a GRADE
    publikációs torzítást feloldatlannak jelöli, az AMSTAR 2-nél pedig a `partial_yes`-t kanonikusan küldi
    („Partial yes” szöveggel, sosem „PY”-ként).

**Értékelési egység.**

| Eszköz | Egység |
|---|---|
| RoB 2 | eredményenként (`study_id:outcome:analízis`) |
| ROBINS-I | eredményenként, a protokollból előtöltött zavaró tényezőkkel |
| QUADAS-2 | indextesztenként |
| PROBAST+AI | modellenként, menetenként |
| TRIPOD+AI | vizsgálatonként (és a saját kéziratra) |
| AMSTAR 2 | a saját áttekintésre |

Az egységet a validator V5 metaadata adja; addig a munkapad beépített táblája.

**Ítélet ≠ számítás.** A validator csak az „implikált” ítéletet adja, az `algorithm` címkével együtt. Az ember
dönt, és ha eltér, indoklást ad; ez döntésként a naplóba kerül (`project log --agent user --stage S06`), és az
értékelés-JSON-ba a `decision_id` jut vissza (X017).

**Konszenzus.**

- Két értékelő külön JSON-t ment; a konszenzus-nézet tételenként mutatja az eltéréseket.
- A végső változat státusza `consensus`.
- Doménenkénti κ a motor `compare` függvényével (kategóriás mező).

**PROBAST+AI.**

- Két menet: fejlesztés 16 kérdés, értékelés 18.
- Doménenként Low/High/Unclear, plus alkalmazhatóság.
- **Holisztikus összítélet, algoritmus nélkül** — a felület ezt szó szerint kiírja.

**TRIPOD+AI.**

- 27 tétel, 52 altétel, D/E címkével; státusz Present/Partial/Missing/N/A.
- A hőtérkép és a hiánylista kétféle célra szolgál:
  - a bevont vizsgálatok jelentési teljessége (adatkinyerés-támogatás);
  - a saját kézirat ellenőrzése.
- A TRIPOD+AI **nem** a módszertan minőségét méri; a felület ezt kiírja.

**GRADE.** A „Suspected” feloldatlan marad, amíg a felhasználó nem dönt; a V2 után a validator is így jár el.

**AMSTAR 2.** A konvenció a KB AMSTAR2-00 szerint `meets`: a „részben igen” kritikus tételen nem hiba. Ha a
besorolás a `weakness` konvencióval más lenne, mindkettő látszik. A validator 1.0.0 (bridge) besorolása a
`weakness` konvenciónak felel meg, és a felület így is címkézi. A `meets` szerinti besorolást addig a motor
`amstar2_consistency` ellenőrzi; V4 után a validator mindkét konvenciót maga adja.

### 5.5 composer

- **A munkapad soha nem írja a composer állapotát.**
- **„Frissítés a composerből”:**
  1. `[py, composer/scripts/prisma, --outdir D, --project P, export, --format, flow-json, --out <tmp>]`;
  2. séma-ellenőrzés;
  3. másolás a `02_szures/prisma_flow.json`-ba;
  4. sha256 és composer-verzió az activity-naplóba.
- **Csonka JSON** (a `save_state` nem atomikus, H7): 3 próbálkozás 200 ms-onként, utána érthető hiba.
- **Helyfeloldás:** a C2 után `COMPOSER_OUTDIR` és `prisma where --project <slug>`; addig a `ma-projekt.json`
  `composer.outdir` mezője, vagy a felhasználó kiválasztja.
- **Kötelező megismétlés:** a composer `status` két figyelmeztetése (retmax-minta, 5D „függőben” rekordok) a
  PRISMA-képernyőn is megjelenik.
- **PDF-ek:** a composer letöltési mappája csak olvasható `doc_root`. A PDF-ek a `documents.json`-ba kerülnek
  (sha256 + út), és így nyithatók lefúráskor.

### 5.6 presubmit (v2)

- **Kézirat-ellenőrzés:** `pc.py check <kézirat> --json <ki>` a meglévő `findings[{category, severity, code, message, where, fix}]` formában.
- **Tény-összevetés (P1 után):** `--facts facts.json` is. A motor a commit-futásokból `szk.facts/v1`-et ír, és a
  presubmit `claims` ellenőrzése jelzi, ha a kéziratban más szám áll. Példa: „RR 0.48 (95% CI 0.33–0.73)” a
  motor 0.49 [0.33; 0.73] értéke helyett, pontos hellyel.
- **Kapcsolat a GUI-val:** az X018 jelzi, ha egy kéziratba jelölt ábra QC-ja nem tiszta.

### 5.7 science-monitor és vault

- **science-monitor:** opcionális, csak olvasható kapcsolat (v2, S2). Ha egy kézirat `root_path`-ja MA-projekt,
  a műszerfal kártyája mutatja a szakaszt, a blockereket és a kapukat a `project export --format json`
  alapján. A munkapad a science-monitort nem hívja.
- **vault:** a munkapad **soha nem módosítja** a vault konfigurációját. Felismeri a helyzetet, és megmutatja a
  teendőt (7.5). A VA1 után a támogatott `.vault-skip` / no-push jelölőt ajánlja.

### 5.8 Claude Code-ágensek és a `probast-tripod-ai` skill

- **Közös fájlok, közös szabályok.** Az ágensek ugyanazokat a CLI-ket és projektfájlokat használják. A
  változásfigyelő a felületen mutatja a munkájukat, például egy új megállapítást vagy egy új futást. A
  `ma-ellenorzo` ugyanazt a `project audit`-ot futtatja, amit a felület lát.
- **Skill-frissítés (E12).** A repó `metaanalizis` skillje egy bekezdést kap:
  - mikor ajánlja a munkapadot (emberi lépések: kinyerés, RoB-űrlap, GRADE, ábra-export);
  - hogyan indítja: `python ma.py gui`, háttérben;
  - és hogy a felületet és a pillanatképet **soha nem publikálja Artifactként, és nem tölti fel.**
- **Claude-inbox (v2).**
  - **Kérés.** A felületen „Claude-vázlat kérése” gomb van (például „PROBAST+AI-vázlat a Lee 2023-ra”). Ez a
    `07_ellenorzes/claude_inbox.jsonl`-be ír egy kérést: egység, eszköz, a dokumentum azonosítója, D-osztályú
    szabályok.
  - **Feldolgozás.** A felhasználó a Claude-munkamenetben a `metaanalizis` skill-lel dolgoztatja fel; a
    `ma-ertekelo` és a `probast-tripod-ai` skill útmutatása szerint.
  - **Eredmény.** `origin: "ai_draft"`, `status: "draft"` értékelés-JSON, amelyet **csak ember hagyhat jóvá.**
    A κ-ban és a konszenzusban az AI-vázlat nem számít második értékelőnek.
  - **Adatosztály.** C osztálynál az inbox ki van kapcsolva. Kérés csak publikált cikkre (A/D osztályú
    dokumentumra) indítható, mert a Claude-munkamenetben olvasott szöveg a modell-szolgáltatóhoz kerül.

### 5.9 Önálló mód és Python nélküli gép

- **Plugin nélkül** minden képernyő működik (5.2); a hiányzó képességet a felület megnevezi a teendővel együtt.
- **Python nélküli vagy zárolt gépen** (MVP): `python ma.py gui snapshot` készít egyetlen HTML-fájlt.
  - **Tartalom:** a commit-futások nézetmodelljei, a validálási összesítők, az értékelések, a GRADE/SoF, a
    PRISMA és a napló.
  - **Biztonság:** hash-alapú CSP, hálózati kérés nincs. Az interaktív ábrák működnek, a szerkesztés nem; a
    gombok a pontos parancsot másolják a vágólapra.
  - **Kitakarás** az adatosztály szerint: értékelők neve → monogram, provenance-idézetek ki, abszolút utak ki,
    C osztálynál adattábla soha.
- **Python nélküli *szerkesztéshez*** csak kifejezett igény esetén (11. fejezet, 2. kérdés) indul egy v2-es
  Pyodide-kísérlet, előre rögzített **go/no-go** feltételekkel:
  - a 344 teszt és a 3268 ellenőrzés a böngészőben is zöld;
  - a HTML ≤ 20 MB, a hideg indulás ≤ 5 s;
  - `file://` alatt Edge-ben és Safariban is működik.

  JS-ben újraírt validálás vagy statisztika (az offline-javaslat „tükre”) **nem** készül.

---

## 6. Validálási logika és számítási igazságforrás

### 6.1 Elvek

1. **A számok egyetlen forrása a motor.** Ide tartozik a hatásméret, variancia, súly, pooling, CI/PI, τ²,
   eloszlás-kvantilis, visszatranszformálás és a teszt; de a tengelyosztás, a kontúr-poligon, a buborék-sáv és
   minden megjelenített számszöveg (`display_text`) is.
2. **Minden szabálynak egy gazdája van, és a munkapad egyet sem birtokol:**

   | Szabályok | Gazda |
   |---|---|
   | V- és P-szabályok, X-szabályok, kapuk | motor |
   | értékelés-teljesség és rollup | validator |
   | ábra-QC | figure-forge |
   | kézirat-állítások | presubmit |

   A felület ugyanazt a kódot hívja, amit a fej nélküli ágensek. Az egyetlen saját ellenőrzés az adatvédelmi,
   ami nem tudományos szabály, hanem a fájlkezelés biztonsági feltétele.
3. **Nyers szöveg be, a motor parszol.** A tizedesjelet, az ezres tagolást és az NA-tokeneket a `tableio`
   értelmezi.
4. **Ítélet ≠ számítás.** RoB, GRADE és AMSTAR 2: az ember ítél; a gép az implikált ítéletet és a konzisztenciát
   adja, vizuálisan elkülönítve.

### 6.2 Térkép

| Ellenőrzés | Hol fut | Mikor | Hol látszik |
|---|---|---|---|
| Számparszolás, V003/V021/V023/V024 | motor `tableio` | gépelés után 250 ms, mentés | cella |
| V001–V025 adatvalidálás, blokkolt sorok | motor `validate` (teljes tábla, in-process) | ugyanaz | cella, lista, fül-jelvény |
| Hatásméret-kizárás (V014, V022) | motor `effect_sizes` / `check_effect_sizes` | mentés, elemzés | sor szürkítve + ok |
| Konverziós előfeltételek, `estimated` | motor `conversions` | átváltó | modális |
| Modell, heterogenitás, torzítás-tesztek, érzékenység, alcsoport, MR | motor `pipeline` (worker) | explore/commit | eredmények |
| Statisztikai figyelmeztetések (`bias.note`, `binary_note`, k < 10) | motor | futás után | eredménysáv |
| Kettős kinyerés: egyezés, tűrés, súgó, hatás, κ | motor `kettos` | összevetés | kettős nézet |
| P001–P016 | motor `prisma` | dobozváltozás, composer-frissítés | PRISMA-dobozok |
| X001–X022 kereszt-artefaktum | motor `audit` (`project audit`) | mentés, futás, értékelés után (fájl-hash szerint gyorsítótárazva); FINAL | Áttekintés, Napló, kapuk |
| Kapuk (PASS nyitott blockerrel tilos; FINAL; FINAL audit-kapu) | motor `projekt.checkpoint` | gombnyomás | Napló |
| GRADE-konzisztencia; AMSTAR 2-konzisztencia | motor `projekt.grade_consistency`; új `amstar2_consistency` (E10) | rögzítéskor | GRADE, AMSTAR |
| GRADE-tanácsadó bemenetek, SoF abszolút hatás | motor `grade_help` (OIS a `power` modulból) | GRADE-nézet | „csak javaslat” oszlop |
| Műszer-tételek, teljesség, implikált ítélet, AMSTAR 2 / NOS / GRADE rollup | validator | „Ellenőrzés”, mentés | űrlap |
| Ábra-QC, szerkeszthetőség, tipográfia, glifák, `numbers` | figure-forge; tartalék: stdlib-audit a motor-SVG-n | export | QC-panel |
| Ábra-számhűség újraellenőrzése | szerver (SVG `<text>` ↔ `display_text`, U+2212 normalizálva) | export után | QC-panel, X018 |
| Kézirat-állítások | presubmit | kérésre | kézirat-panel |
| Kérés-séma, méret, útvonal, ETag | szerver | minden kérés | hibaüzenet |
| „Számnak látszik?”, kötelező mező üres | böngésző — **csak UX-előjelzés** | gépelés | halvány jelzés |

### 6.3 Az élő validálás csővezetéke

1. A tábla minden változásra (és beillesztésre) piszkozatot képez: a `header`-t és a nyers cellaszövegeket.
2. 250 ms tétlenség után `POST /api/validate` megy, `client_seq`-kel. A régebbi válaszokat a felület eldobja.
3. A szerver az `api.validate_table`-t hívja in-process, ami ms alatt lefut.
4. A `row`, `rows` és `fields` mezőket a felület cellára képezi. A „Nem hiba — indoklás” állapotú
   megállapításokat `acknowledged` jelöli.
5. Mentéskor teljes validálás és hatásméret-ellenőrzés fut; a `project audit` gyorsítótára érvénytelenné válik.
6. Az elemzés-gomb hibánál sem tiltott, mert a motor a blokkoló sorokat kihagyja, és ezt a riportban rögzíti. A
   felület megmutatja, mely sorok maradnak ki, és megerősítést kér.

### 6.4 X-szabályok (motor `project audit`, KB-azonosítóval; E8)

Az X-szabályok a V-szabályokhoz hasonlóan a `kb build`-del a `decision_rule` táblába kerülnek, így a
`ma-ellenorzo` és a felület ugyanarra a szabályra hivatkozik.

| Kód | Súly | Feltétel | Kapcsolódó KB / javasolt lépés |
|---|---|---|---|
| X001 | error (S08-tól) | commit-futás `data_sha256` ≠ a tábla mostani hash-e (elavult elemzés) | újrafuttatás a speccel |
| X002 | warning | exportált ábra `plot_sha256` ≠ a legutóbbi futásé | újrarajzolás |
| X003 | error | a CSV `rob` értéke ≠ az értékelés végső összítélete | D-S13-003 · szinkron |
| X004 | warning → error S13-tól | elemzett vizsgálat értékelés nélkül a kimenet eszközével | D-S06-008 |
| X005 | warning | van becsült sor, de nincs „becsült nélkül” gyermek-futás | D-S12-003, D-S05-023 |
| X006 | warning | van magas RoB-ú sor, de nincs „magas RoB nélkül” gyermek-futás | D-S12-002 |
| X007 | error | a `grade` sor k / résztvevő / hatás-szöveg ≠ az elsődleges commit-futás | GRADE frissítése |
| X008 | error | SoF-cella ≠ a motor `display_text`-je | SoF újragenerálása |
| X009 | error (S08 PASS előtt) | kettős kinyerés lezáratlan eltéréssel | egyeztetés |
| X010 | warning | elemzett cellának nincs `source.page` eredete | forrásjelölés |
| X011 | error | `review_type = prediction_model`, és egy vizsgálatnak nincs PROBAST+AI-ja | — |
| X012 | warning | AMSTAR 2 hiányos FINAL előtt, vagy a besorolás nem egyeztethető a válaszokkal | — |
| X013 | error | eredet szerint becsült cella, de a sor `estimated` jelzője hamis (vagy fordítva) | szinkron |
| X014 | error | elemzett k > bevont vizsgálatok (I), vagy egyedi `study_id` > I | P010 · studies.json |
| X015 | warning | `included_meta` (kimenetenként) ≠ a commit-futás k-ja | PRISMA |
| X016 | warning | elsődleges spec ≠ előre rögzített spec, vagy `prespecified: false`, döntés nélkül (protokoll-eltérés) | D-S12-006 · `project log` |
| X017 | error | domén- vagy összítélet ≠ implikált, `override_reason` nélkül | D-S06-004 |
| X018 | warning | kéziratba jelölt ábra QC-ja nem tiszta, vagy a számhűség sérül | újrarajzolás |
| X019 | error (S13-tól) | GRADE publikációs torzítás `unresolved` | döntés |
| X020 | warning | composer `undecided > 0` FINAL kéréskor | composer |
| X021 | warning | teljes szöveg szintű kizárási okok ≠ a `02_szures` döntési napló | PRISMA |
| X022 | error | `.prov.json` `table_sha256` ≠ a CSV, és van nem egyeztethető cella | eredet-helyreállítás |

**FINAL audit-kapu:** `project checkpoint --stage FINAL --audit-gate` az `error` szintű X-találatoknál is
elutasít. A „kész” állapot így a fájlok összhangját is jelenti, nem csak azt, hogy nincs nyitott blocker. Az
MVP-ben az X001, X003, X005, X006, X010, X013, X014, X016 és X022 készül el, a többi a v1-ben.

### 6.5 Értékelés: teljesség, implikáció, felülbírálás, konszenzus

- **Teljesség:** minden hatókörbe eső tétel (PROBAST+AI-nál menetenként) kapott választ a kanonikus szótárból.
  A validator ≥ V3 ezt cellából olvassa; legacy módban a munkapad számol, és ezt kiírja.
- **Implikált ítélet:** a validatortól jön, `algorithm` címkével. A felület **soha** nem írja ki
  „hivatalos RoB 2 eredmény”-ként.
- **Felülbírálás:** kötelező indoklással; döntés a naplóba, `decision_id` vissza a JSON-ba (X017).
- **RoB → elemzés:** a konszenzusos összítélet a CSV `rob` oszlopába kerül, `calculated` eredettel (X003).

### 6.6 Kapuk

A kapulogika a motoré (`projekt.checkpoint`): PASS/PASS_WITH_FIXES tiltott nyitott blocker mellett, FINAL
bármely szakasz blockerénél, és az X-hibáknál az audit-kapuval. A felület előre lekérdezi a blokkolókat, és
letiltja a gombot, megnevezve az okot. Ha a motor egy ágenssel való verseny miatt mégis elutasít, a felület a
hibaüzenetet szó szerint mutatja.

### 6.7 Számhűség-lánc (egy szám, öt ellenőrzés)

```
 motor fmt_triple ──► plot/v2 display_text {hu,en} ──► felület DOM (szövegként, formázás nélkül)  [render-teszt]
        │                         │                 ──► motor-SVG <text>                         [E5 teszt]
        │                         └───────────────► figure-forge SVG ── numbers blokk           [F2]
        │                                                     └── szerver újraolvassa <text>      [5.3]
        └──► szk.facts/v1 ──► presubmit claims (kézirat)                                           [P1]
```

Ha bármely ponton eltérés van, az export **nem kap zöld jelvényt**, és major súlyú `finding` keletkezik.

### 6.8 Tilalmi lista és lint

- **JS:** `Math.*` csak a `web/src/geom.js`-ben lehet (`Math.log10`, `Math.round`, `Math.min/max`).
  - A build-szkript és a CI grep-pel tiltja a `Math.exp|Math.log(|Math.sqrt|Math.pow|toFixed|toPrecision`
    mintákat a `plots/` és `screens/` alatt.
  - Adatból `innerHTML` nem építhető (lint).
- **Python (`ma_gui/`):** AST-ellenőrzés tiltja a `math`, `cmath`, `statistics`, `random` és `decimal` importot, és
  a `metaelemzes` belső moduljainak közvetlen importját is; csak a `metaelemzes.api` hívható. Így a szerver nem
  tud a homlokzatot megkerülve számolni.
- **Szerződés:** a JS kizárólag `display_text`-et ír ki számként. Ha egy mező hiányzik, „—” jelenik meg, nem
  saját formázás.

---

## 7. Biztonság és adatvédelem

### 7.1 Fenyegetésmodell

| # | Fenyegetés | Védelem |
|---|---|---|
| T1 | Rosszindulatú weboldal ugyanabban a böngészőben (CSRF, „simple request” POST) | kötelező `Content-Type: application/json` + egyedi `X-MA-Token` fejléc (preflightot kényszerít); `OPTIONS` → 405; CORS-fejléc soha; ha van `Origin`, pontosan a saját origin; `Sec-Fetch-Site: cross-site` → 403 |
| T2 | DNS-rebinding | a `Host` csak `127.0.0.1:<port>` vagy `localhost:<port>` lehet, különben 403 |
| T3 | Clickjacking | `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'` |
| T4 | Más helyi felhasználó vagy folyamat | csak IPv4-loopback; **egyszer használható, 60 s-os indítókód** az URL-ben, amit a lap `POST /api/session`-nel munkamenet-tokenre cserél, majd `history.replaceState`-tel töröl. A folyamatlistában látható kód addigra felhasznált. A token `sessionStorage`-ban van (nem süti: a 127.0.0.1 sütijei nem port-izoláltak). A `GET /` **nem tartalmaz adatot és tokent** (a science-monitorral ellentétben, H8). Új lap a tokent `BroadcastChannel`-en kapja a meglévő laptól. |
| T5 | PDF/kép új lapon, ahol egyéni fejléc nem küldhető | **aláírt, rövid életű URL:** `/f/<doc>/<lejárat>/<hmac>`, HMAC-SHA256 a munkamenet-kulcsból származtatott kulccsal a `doc|lejárat|út` hármasra, 10 perc élettartam. Csak a `documents.json`-ban vagy a futás-artefaktumok közt szereplő fájl nyitható így; `Sec-Fetch-Site` értéke `none` vagy `same-origin` lehet. SVG-válasz `Content-Security-Policy: sandbox` fejléccel. |
| T6 | XSS a saját adatból (vizsgálatnév, idézet, KB-szöveg, SVG) | szigorú CSP nonce-szal: `default-src 'none'; script-src 'nonce-…'; style-src 'nonce-…'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`; DOM-építés `textContent`-tel; beolvasott SVG-ből a `<script>`, a `foreignObject`, az `on*` és a külső `href` eltávolítva; JSON-ban `__proto__`/`constructor` kulcs elutasítva |
| T7 | Útvonal-bejárás | `resolve()` + `is_relative_to(projekt | doc_roots)`; gyökéren kívülre mutató symlink, Windows-os fenntartott név (`CON`, `NUL`, `COM1`…) és ADS (`:`) tiltva; kiterjesztés-allowlist |
| T8 | Parancsinjektálás | argv-lista, `shell=False`; felhasználói érték csak validált (enum/regex) opcióértékként kerülhet be, kapcsoló-pozícióba soha; a fájlneveket a munkapad generálja (slug) |
| T9 | Ellátási lánc | nincs CDN, nincs külső JS/CSS/betűkészlet, nincs vendor-mappa a termékben; Playwright és axe-core csak tesztben |
| T10 | Szivárgás naplóból | a szervernapló csak útvonalat, kódot és időt ír; hibaüzenetben nincs cellaérték; az activity-naplóban sincs cellaérték; `Cache-Control: no-store` |
| T11 | Erőforrás-kimerítés | törzs ≤ 4 MB (tábla), ≤ 64 KB (egyéb); JSON-mélység ≤ 32; ≤ 5000 sor, ≤ 200 oszlop; elemzés 30 s; tétlenségi leállás 4 óra |

Minden válaszon ott vannak ezek a fejlécek:

- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Resource-Policy: same-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`

### 7.2 A loopback-szerver a science-monitor mintájához képest

**Átvett elvek:** loopback, futásonkénti token, egyedi fejléc, nincs CORS, `no-store`, nincs kéréstörzs a
konzolon.

**Bezárt rések (H8):**

- adatot és tokent csak tokennel ad ki;
- pontos Origin-ellenőrzés;
- CSP és keretezés-tiltás;
- `OPTIONS` → 405;
- foglalt alapportnál (8790, ütközés nélkül a science-monitor 8787-ével) a 8791–8799 sáv, majd OS-port.

A science-monitorra ugyanez a keményítés javasolt (PR-S1). Pluginok között nincs import, ezért a kód másolt
modulként, verziófejléccel kerülne át.

### 7.3 Alfolyamatok és fájlrendszer (Windows-higiénia)

- **Indítás:** `CREATE_NO_WINDOW`; `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`; a `PYTHONPATH`/`PYTHONHOME` nem
  öröklődik.
- **Korlátok:** kimeneti méretkorlát (stdout ≤ 8 MB), időkorlát, leállítás után a folyamatfa is megszűnik.
- **Írás:** csak a projektgyökér alá, atomikusan (`tmp` + `os.replace`).
- **Excel-zárolás:** Windows-on `PermissionError` → 423 + „Zárd be a fájlt az Excelben, majd [Újra]”.
- **Ideiglenes fájlok:** **a projektmappán kívül** (2.4). Így a vault nem pusholja, és a OneDrive sem
  szinkronizálja őket.
- **Kimenő hálózat:** nincs. A composer és a presubmit online módjait (`--online`, letöltések) a munkapad nem
  hívja.

### 7.4 Adatosztályok

| Osztály | Példa | Hely | vault-push | Claude olvashatja | Auditba / pillanatképbe |
|---|---|---|---|---|---|
| A — publikált aggregált | közleményből kinyert 2×2 cellák, átlag/SD | `03_adatok/` | engedett (privát repó) | igen | igen |
| B — nem publikált aggregált | szerzőtől kapott összesítők, titoktartással | `_privat/` (alapból) vagy `03_adatok/` kifejezett, naplózott hozzájárulással | csak hozzájárulással | ha a megállapodás engedi | alapból ki |
| C — betegszintű / azonosítható | saját kórházi kohorsz predikciós modell validálásához | **csak `_privat/`** | **tilos** | **tilos** (`deny`) | **soha** |
| D — jogvédett teljes szöveg | PDF-ek, KB `chunk` szöveg | composer outdir, `tudasbazis/forrasok/` | tilos | helyben igen | csak doc-id + oldal + sha256 |

- **A projekt osztálya** az A/B/C közül a legmagasabb; a `project init` kérdezi meg. A D fájltípus-osztály.
- **A motor csak aggregált adatot elemez.** Betegszintű fájl a munkapadon csak forrásként jelenhet meg,
  olvasásra.
- **PHI/TAJ-szkenner** (import és mentés, stdlib):
  - **Oszlopnév-minták:** `taj`, `név`/`name`, `születési`/`birth`/`dob`, `cím`/`address`, `telefon`, `email`,
    `mrn`, `patient_id`, `beteg_azonosító`.
  - **Értékminták:** 9 jegyű TAJ-szám CDV-ellenőrzőjeggyel (az első 8 jegy a páratlan helyeken 3-mal, a párosakon
    7-tel szorozva, összegük mod 10 = a 9. jegy), teljes születési dátum, e-mail.
  - **Találatnál:** a mentés csak a `_privat/` alá engedett. A hamis riasztás felülbírálható, naplózott
    döntéssel.
- **Írás-tartás:** B vagy C osztálynál, ha a cél nincs `.gitignore`-ban és a projekt a vault-gyökér alatt van,
  a szerver **megtagadja az írást**. Ilyenkor két lehetőséget kínál: [.gitignore-blokk beírása] vagy [projekt
  áthelyezése].

### 7.5 vault, OneDrive/iCloud, Claude-hozzáférés

**Tények (H11):** a vault Stop- és SessionEnd-hookja minden `~/Documents/claude` alatti projektben (mélység ≤ 2)
`git add -A` + commit + push-t futtat egy privát GitHub-repóba. A `.gitignore`-t tiszteli, kizárni csak
könyvtárnévvel lehet, és a pre-commit hook lefut. A felhasználó projektjei (a repó maga is,
`C:/Users/szili/Documents/claude/apps/…`) ezen a gyökéren belül vannak.

**Rétegzett védelem:**

| Réteg | Mit csinál a munkapad |
|---|---|
| L1 Felismerés | beolvassa a vault konfigurációját (`$VAULT_HOME` vagy `~/.claude/vault/config.json`: `root`, `max_depth`, `exclude`, `paused`), és eldönti, hogy a projekt követett-e; `git ls-files` + mintakeresés a már követett érzékeny fájlokra |
| L2 Kezelt `.gitignore`-blokk | jelölők közötti blokk: `_privat/`, `*_PHI*`, `*.phi.*`, `07_ellenorzes/audit/**/data/`, `*.snapshot.html`, `projekt.sqlite-wal`, `projekt.sqlite-shm`; a blokkon kívüli sorokhoz nem nyúl; csak kattintásra, diff-előnézettel |
| L3 Pre-commit őr (opt-in) | `.git/hooks/pre-commit` (stdlib): csak akkor utasít el, ha egy érzékeny fájl **mégis** az indexbe került (kényszerített add vagy korábban követett fájl); a meglévő hookot láncolja. Mivel az L2 a szokásos `git add -A`-t már megakadályozza, ez csak tartalék, és ritkán blokkolja a vault mentését. A felület ezt a következményt kimondja. |
| L4 Elhelyezés | C osztályú adat csak `_privat/`-ba kerülhet; a motor onnan is olvas |
| L5 Útmutatás, nem beavatkozás | a vault konfigurációját **soha** nem módosítja; a pontos teendőt mutatja (`vault pause`, a projekt felvétele az `exclude`-ba); a VA1 után a `.vault-skip` jelölőt ajánlja |
| L6 „Már felment?” | ha a `git log --all -- <érzékeny út>` nem üres: piros riasztás teendőkkel — történet-átírás (`git filter-repo`), force push, GitHub-támogatás a gyorsítótárazott nézetekhez, és betegszintű adatnál az adatvédelmi tisztviselő értesítése (a GDPR 33. cikke szerinti mérlegelés) |

C osztályú projekt a vault-gyökér alatt csak akkor nyílik meg, ha az L2 és az L3 aktív, és az L1 nem talált
követett érzékeny fájlt — vagy ha a vault szünetel, illetve a projekt ki van zárva.

**Felhőszinkron** (figyelmeztetés, nem tiltás):

- **Windows:** OneDrive Known Folder Move. Felismerés a `winreg` `User Shell Folders\Personal` értékéből és a
  `%OneDrive%` változóból.
- **macOS:** iCloud „Desktop & Documents”. Felismerés: létezik-e a
  `~/Library/Mobile Documents/com~apple~CloudDocs/Documents`.
- B/C osztálynál a felület javasolja a `_privat/` áthelyezését szinkronizálatlan helyre, és a `doc_roots`
  áthivatkozását.

**Claude-hozzáférés.** A Claude-munkamenetben olvasott fájl tartalma a modell-szolgáltatóhoz kerül. B/C
osztálynál a munkapad egy kattintásra, diff-előnézettel ezt javasolja/írja a projekt `.claude/settings.json`-jába:

```json
{ "permissions": { "deny": ["Read(./_privat/**)", "Read(**/*_PHI*)"] } }
```

**Indítókód a munkamenet kimenetében.** Ha a munkapadot Claude indítja, a kimenetben szerepelhet az indítókód.
Ez egyszer használható és 60 s-ig érvényes, ezért utólag értéktelen. Cellaérték sosem kerül a kimenetbe.

### 7.6 Böngésző, export, pillanatkép

- **Böngészőtároló (D8):** `localStorage`-ban **csak** UI-preferencia lehet (nyelv, téma, utolsó nézet,
  `mag.pref.*` előtaggal); `sessionStorage`-ban a token. Projektadat soha nem kerül böngészőtárolóba. Ok: az
  Anamnézis-app ezt a mintát betegesetekre használja (H10), és a `file://` oldalak tárolója Chromiumban közös. A
  szabályt teszt ellenőrzi.
- **Excel-képletinjekció:** az Excelbe szánt exportokban (SoF-CSV, egyetértési tábla, audit-CSV-k) az `= + - @`,
  tabulátor vagy CR karakterrel kezdődő **szöveges** cella `'` előtagot kap. A számoszlop nem, mert a negatív
  szám is `-`-szal kezdődik. A kanonikus projekt-CSV-t a munkapad **nem** módosítja csendben: a motor új
  **V025** szabálya („képletnek látszó szöveges cella”, warning) jelzi.
- **Pillanatkép:**
  - **CSP:** `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-…'; style-src 'sha256-…'; img-src data: blob:; connect-src 'none'">`.
    A fájl internetes gépen megnyitva sem tud hálózatra szólni.
  - **Kitakarás:** a kapcsolók alapértéke az adatosztályból jön (C osztálynál adattábla soha). A manifeszt
    rögzíti a kitakarásokat.
- **Artifact-tilalom:** a skill és a parancs-dokumentáció kimondja, hogy a felületet és a pillanatképet soha nem
  szabad Artifactként publikálni vagy feltölteni.

### 7.7 Szerzői jog

A KB `chunk` táblája (8 forrás teljes szövege) csak helyben használható. A KB-kereső „helyi forrás — nem
exportálható” címkével mutatja. Pillanatképbe és audit-csomagba csak azonosító, oldal és az adatbázis sha256-ja
kerül. A PDF-ek nem kerülnek az auditba, csak a `doc`-azonosítójuk, sha256-juk és oldalszámuk.

---

## 8. Tesztelési stratégia

### 8.1 Rétegek

| Réteg | Hol fut | Mit bizonyít | Függőség |
|---|---|---|---|
| Motor-kapu (344 teszt + 192 eset / 3268 ellenőrzés) | motor repó | a számok helyesek | nincs |
| Motor új tesztjei (E1–E13) | motor repó | homlokzat ≡ CLI `--json` bájtra; `api.analyze(spec)` ≡ `ma.py analyze --spec` (a `results.json` időbélyeg nélkül azonos); spec ↔ argv oda-vissza; `plot/v2` séma-érvényes az öt referencia-adatsoron (BCG, Normand, Molloy, Pritz, Yusuf); belső konzisztencia (`plot.studies[i].y == results.primary.yi[i]`, súly, összegzések, `display_text == fmt_triple`); minden V-szabály `row/fields` leképezése; X-szabályonként pozitív és negatív fixture; `kettos` (tűrés, formátum-egyenértékűség, κ referencia-példán); buborék-sáv a metafor `predict()` értékeivel (új referencia-sor) | nincs |
| Lánc-visszajátszás (8.2) | motor repó | a felület útja (saját CSV-író → HTTP → nézetmodell) egyetlen számot sem változtat | nincs |
| Szerződés, biztonság, adatvédelem | motor repó (`tests/gui`) | 8.3–8.5 | stdlib `unittest` + `http.client` |
| Adapterek: golden + stub-pluginok | motor repó | bridge-mód helyes a hibás plugin mellett; képesség-mátrix determinisztikusan | nincs |
| Plugin-oldali selftestek | szk-plugins CI | minden plugin a saját szerződés-másolatával validálja a kimenetét; drift-őr | nincs (matplotlib a CI-ben) |
| UI 1. réteg (telepítésmentes) | felhasználói gép is | rajzolók, lefúrás, i18n, billentyűzet | csak Edge/Chrome |
| UI 2. réteg | fejlesztő / CI | végponttól végpontig, vizuális regresszió, a11y | Playwright (csak teszt) |

### 8.2 A 3268 ellenőrzés újrahasznosítása — lánc-visszajátszás, őszinte lefedettséggel

A `tests/gui/test_chain_replay.py` a `source_cases` 192 aktív esetét a munkapad teljes láncán vezeti át:

1. **Bemenet.** Az eset `rows`-ából a munkapad **saját** táblaíró kódja készít CSV-t, szándékosan magyar
   konvencióval (`;`, tizedesvessző), hogy a parszolás is próbára kerüljön.
2. **Spec.** A `measure`, az `options` és a `call_args` alapján `szk.ma.analysis-spec/v1` készül, hívástípusonkénti
   leképezéssel:

   | Hívástípus | Leképezés |
   |---|---|
   | `meta_analysis` | modell / τ² / CI / PI / szint |
   | `subgroup` | `options.subgroup` |
   | `meta_regression` | `moderators`, `metareg_test` |
   | `egger`, `begg`, `trimfill`, `lfk`, `harbord`, `peters` | a torzítás-blokk |
   | `effect_sizes` | `results.effect_sizes` |
   | `prisma_flow` | `/api/prisma` |
   | `conversion` | `/api/convert` |

3. **Futtatás és olvasás.** `POST /api/analyze` (commit) egy `port=0`-n indított szerveren; az olvasás a felület
   nézetmodell-betöltőjével történik.
4. **Értékelés.** Fordítótábla képezi az `expected` útvonalakat a `results.json`-ra (például
   `adjusted.estimate` → `bias.trimfill.adjusted.estimate`). A kiértékelés ugyanazzal a `check_expectation`-nel
   és tűréssel fut.
5. **Lefedettségi jelentés.** Esetenként jelzi, hogy az eset leképezhető-e.
   - **Már most ismert, biztosan nem leképezhető ellenőrzések: 106 (3,2%).**

     | Ok | Eset | Ellenőrzés |
     |---|---:|---:|
     | `tau2_fixed` — nincs rá pipeline-opció | 5 | 49 |
     | `trimfill` `side` paraméter | 2 | 42 |
     | `input_storage: float32` | 1 | 15 |

     Ezek a közvetlen motor-tesztben maradnak, és motor-PR-jelöltek.
   - **Cél:** a leképezhető ellenőrzések **100%-a** zöld. A leképezhetőség **mért szám**, nem ígéret; a felső
     korlát ma 96,8%.

A **megjelenítési réteg** visszajátszása (2. réteg) a `meta_analysis`-esetek futásaira három dolgot ellenőriz:

- **(a) Szöveg:** az összesítő sor DOM-szövege bájtra egyezik a `display_text`-tel.
- **(b) Geometria:** minden jelölő `data-y/lo/hi` értéke egyezik a `plot_data`-val, és az inverz pixel-leképezés
  a `lo`/`hi`-t fél pixel adategyenértékén belül adja vissza.
- **(c) Tengely:** a tengelyfeliratok a motor `axis.ticks` szövegei.

A motor-SVG `data-*` attribútumai (E5) ugyanígy ellenőrizhetők, így a két rajzoló egymással is össze van vetve.

### 8.3 Szerződés-, golden- és stub-tesztek

- **Sémák:** minden sémához legalább 3 pozitív és 3 negatív példa. A `schema_lite` validátort a hivatalos
  példákon is próbára tesszük.
- **Legacy golden:** a validator 1.0.0 rögzített stdout-jai eszközönként, és a belőlük elvárt
  `szk.appraisal-result/v1`. Bennük van a H1 (üres TRIPOD → 1/52), a H2 (csak a fejlesztési menet kitöltve →
  32/34), a H3 („Strongly suspected” → HIGH) és a H4 (PY vs „Partial yes”) reprodukciója; az őröknek kötelezően
  meg kell jelenniük.
- **Stub-pluginok:** kis szkriptek, amelyek megadott verziójú `--capabilities`-t és rögzített kimeneteket adnak.
  Ezekkel a 16 képesség-kombináció (absent / unusable / legacy / ok × 4 plugin) determinisztikusan előáll. Az
  E2E ellenőrzi a fejléc-jelvényeket és a funkció-letiltásokat.
- **AMSTAR 2-paritás:** az `amstar2_consistency` és a validator `rollup_amstar2` (V4 után) ugyanazt a besorolást
  adja.
  - **Minta:** a 7 kritikus tétel összes igen / részben igen / nem kombinációja × 1000 véletlen kitöltés a nem
    kritikusakra, hiányzó válaszokkal együtt.
  - **Konvenciók:** mindkét konvencióban.
- **figure-forge:**
  - matplotlib nélküli környezetben `unusable` állapot, érthető `problems` mezővel; F1 után az `audit` fut;
  - F2 után a `numbers.ok` igaz, és a szerver újraellenőrzése is zöld a BCG, a Normand, a Molloy és a Pritz
    példán.
- **composer:**
  - a `the-collector.prisma/v1` állapot és a `flow-json` mintái (a plugin selftestjéből);
  - csonka JSON → újrapróbálás → érthető hiba.

### 8.4 Biztonsági tesztek (stdlib `http.client`)

- **Hitelesítés:**
  - token nélkül vagy hibás tokennel minden `/api/*` → 403 (konstans idejű összevetés);
  - az indítókód második használata → 403;
  - a lejárt vagy hamisított `/f/…` aláírás → 403.
- **Fejlécek:**
  - idegen `Host` (`evil.com`, `127.0.0.1.nip.io`) → 403;
  - idegen `Origin` → 403; `Sec-Fetch-Site: cross-site` → 403;
  - `OPTIONS` → 405, CORS-fejléc nincs.
- **Törzs:** `text/plain` → 415; túl nagy törzs → 413; JSON-mélység > 32 → 400.
- **Útvonal:** `../../`, gyökéren kívüli symlink, `CON`/ADS, tiltott kiterjesztés → 403/404.
- **Kiszolgált tartalom:**
  - a `GET /` nem tartalmaz tokent és projektadatot;
  - a CSP és a többi fejléc minden válaszon jelen van, a nonce egyezik;
  - SVG-válaszon `sandbox`.
- **Injektálás:** a `"; rm -rf ~; "`, a `$(…)`, a backtick és a `--exclude` vizsgálatnévként szó szerint jelenik
  meg az argv-ben, és sosem értelmeződik opcióként.
- **Hálózat:** a szerver csak a 127.0.0.1-en hallgat (socket-ellenőrzés).

### 8.5 Adatvédelmi és konkurencia-tesztek

- **vault:** ideiglenes `VAULT_HOME` hamis konfigurációval; a gyökér alatti, kívüli, kizárt és szüneteltetett
  projekt felismerése.
- **`.gitignore`-blokk:** idempotens beszúrás; a kézi sorok érintetlenek maradnak.
- **Pre-commit őr:** egy ideiglenes git-repóban csak a kényszerítetten indexelt érzékeny fájlt utasítja el, és a
  meglévő hookot láncolja.
- **C osztály:** a vault-gyökér alatt védelem nélkül nem nyílik meg.
- **TAJ-szkenner:** érvényes CDV-jű számot jelez, érvénytelent nem.
- **Naplók:** az activity- és a szervernapló nem tartalmaz cellaértéket (mintakeresés a tesztadat egyedi
  értékeire).
- **Böngészőtároló (D8):** egy munkamenet után a `localStorage` csak `mag.pref.*` kulcsot tartalmaz.
- **Pillanatkép:** `file://`-ból megnyitva nulla hálózati kérés és nulla CSP-sértés.
- **Konkurencia:**
  - párhuzamos CLI-írás (`ma.py project finding …`) listázás közben;
  - kívülről módosított CSV → 409 és diff;
  - Windows-on zárolt fájl → 423 és érthető hiba;
  - félbeszakított kétfájlos írás → X022.

### 8.6 UI-tesztek

- **1. réteg — telepítésmentes önteszt:** az `index.html?selftest=1` az oldalon belül ≈ 150 állítást futtat le a
  rögzített BCG-nézetmodellen.
  - **Mit ellenőriz:** jelölő-pozíció ±0,5 px; tengelyfeliratok = `ticks.text`; a lefúrás a helyes `row_uid`-ra
    ugrik; billentyűzetes navigáció; az i18n-kulcsok teljesek; a tiltott API-k nem szerepelnek.
  - **Futtatás:** `msedge|chrome --headless=new --dump-dom "http://127.0.0.1:<port>/?selftest=1#launch=<kód>"`; a
    Python-futtató a `<pre id="selftest">` tartalmát parszolja. Windows-on az Edge mindig elérhető.
- **2. réteg — Playwright** (csak fejlesztő/CI), végponttól végpontig, a 3.5 összes képernyőjén:

  | Folyamat | Elvárt eredmény |
  |---|---|
  | cella → V006 → javítás | a jelzés eltűnik |
  | TSV „2,000” | V023 |
  | átváltó | becsült érték, V018, X005, gyermek-futás |
  | kettős kinyerés | konszenzus, X009 eltűnik |
  | commit → export | a számhűség zöld |
  | RoB legacy-validatorral | H-őrök; felülbírálás indoklás nélkül nem menthető |
  | PROBAST+AI | 34 slot |
  | GRADE „suspected” | rögzítés tiltva |
  | PASS blockerrel | elutasítás szó szerinti üzenettel |
  | PRISMA kézi | P007 |
  | audit-ZIP kétszer | azonos sha256 |

  Vizuális regresszió világos/sötét témában, 1280 és 1920 px-en, küszöbös pixel-diffel. Akadálymentesség:
  axe-core, nincs „serious/critical” találat.
- **Böngésző-mátrix:**
  - Chromium (Playwright);
  - Firefox és WebKit füstteszt — a Safari a PDF `#page=N`-t eltérően kezelheti, ezért az oldalszám szövegként
    is kiíródik;
  - kiadásonként kézi lista Windows/Edge és macOS/Safari alatt: indítás, tábla, elemzés, PDF, export,
    OneDrive-figyelmeztetés.

### 8.7 CI

- **Motor-repó (GitHub Actions):** mátrix ubuntu / windows / macos × Python 3.9 / 3.12. Lépések:
  1. motor-kapu;
  2. új motortesztek;
  3. lánc-visszajátszás;
  4. biztonság és adatvédelem;
  5. adapterek;
  6. UI 1. réteg;
  7. UI 2. réteg (ubuntu, eleinte nem blokkoló);
  8. opcionális `szk-plugins` checkout: szerződés-drift és valódi plugin-integráció.
- **szk-plugins CI:** a meglévő lint és selftest, plus az M1 contract-drift lépés (Python, nem `sha256sum`, mert
  a macOS-futón az utóbbi alapból hiányzik).

---
## 9. Ütemterv (MVP → v1 → v2, becsült ráfordítás)

Egység: egy fejlesztő heti munkája, Claude Code-dal párban. A becslés tartalmazza a teszteket és a
dokumentációt, tartalékot nem; +15% javasolt. A plugin-PR-ek kicsik, visszafelé kompatibilisek, és egyenként is
értéket adnak.

### 9.1 0. fázis — gyors javítások és kézfogás (1 hét, a felülettől függetlenül)

| Tétel | Hol | Hét |
|---|---|---:|
| V2 GRADE publikációs torzítás (H3), V3 cella- és menet-alapú verify (H1, H2), V4 AMSTAR 2 szókincs és konvenció (H4) | validator | 0,4 |
| F1 lusta importok: `audit` matplotlib nélkül (H5) | figure-forge | 0,15 |
| C3 hordozható shebang (H7) | composer | 0,05 |
| `--capabilities` (szerződés-hash + `known_issues`) a validatorban, a figure-forge-ban, a composerben és a presubmitben; `szk.common/v1` + `szk.capabilities/v1` séma; M1 drift-őr | pluginok + marketplace | 0,4 |

**Kilépési feltétel:**

- a H1–H5 és a H7 regressziós tesztje zöld a pluginok selftestjében;
- a kézfogás minden pluginnál működik;
- a régi verziót a munkapad `legacy`-ként ismeri fel (stub-teszt).

### 9.2 MVP — „egy kimenet végig validálva, önálló módban” (6,5 hét)

| Tétel | Hét |
|---|---:|
| Szerver-váz: biztonság (indítókód → token, Host/Origin/Sec-Fetch, CSP-nonce, aláírt fájl-URL), router + `schema_lite`, `ma.py gui`, `.cmd`/`.command` indító, egypéldányos zár, futásidejű mappa | 1,0 |
| Motor: E1 (homlokzat), E2 (lokátorok, V025), E3 (spec + `--spec` + `run.json`), E4a (plot/v2 mag: `row_uid`, `display_text` i18n, `axis.ticks`, `sections`), E5 (EN feliratok, rétegek, U+2212, `data-*`), E7 (`project … --json`, `actor`) | 1,25 |
| Kinyerés: tábla (≤ 1000 sor, virtualizálás nélkül), TSV-beillesztés, élő validálás, „Nem hiba — indoklás”, átváltó + becsült + `.prov.json` + `documents.json`, ETag/409, Excel-zár üzenet | 1,5 |
| Elemzés: explore/commit, meleg worker (megszakítható), forest (alcsoport, PI), funnel (E4b kontúr-poligonok), Doi, LOO, befolyás, lefúrás + PDF-oldal aláírt URL-en, gyermek-futások, protokoll-eltérés sáv + parancs-előnézet, KB-jelvények | 1,25 |
| Napló, kapuk, KB-kereső; PRISMA kézi + P-ellenőrzés + `studies.json`; E8 alap (X001, X003, X005, X006, X010, X013, X014, X016, X022); `activity.jsonl` hash-lánc | 0,75 |
| Adatvédelem (osztályok, vault/OneDrive/iCloud felismerés, `.gitignore`-blokk, `deny`-javaslat, TAJ-szkenner, írás-tartás), **kitakaró pillanatkép**, audit-export (alap) | 0,5 |
| Tesztek összefésülése: lánc-visszajátszás lefedettségi jelentéssel, biztonsági és adatvédelmi tesztek, UI 1. réteg; kézi próba Windows + macOS | 0,25 |

**Elfogadás:**

- **Teljes kör plugin nélkül** a BCG- és a Normand-példán: adatbevitel → validálás → átváltás → commit →
  forest/funnel/Doi/LOO → lefúrás a PDF-oldalig → napló/kapu → audit-export → pillanatkép.
- **Lánc-visszajátszás:** a leképezhető ellenőrzések 100%-a zöld; a lefedettségi jelentés kész.
- **Tesztek:** a biztonsági és az adatvédelmi tesztek zöldek, a pillanatkép nulla hálózati kérést indít.
- **Platformok:** kézi próba Windows/Edge (OneDrive-os Documents-szel) és macOS/Safari alatt.

### 9.3 v1 — „teljes SR/MA munkafolyamat pluginokkal” (7 hét)

| Tétel | Hol | Hét |
|---|---|---:|
| V1 JSON I/O (`szk.instrument/v1`, `szk.appraisal/v1`, `szk.appraisal-result/v1`), V5 egység-metaadat; validator-adapter (bridge → json) | validator + munkapad | 1,0 |
| RoB 2 / ROBINS-I / ROBINS-E / QUADAS-2 / NOS / QUIPS / JBI űrlapok, konszenzus-nézet, forgalmi lámpa, `rob` oszlop szinkronja | munkapad | 1,25 |
| **PROBAST+AI** (két menet, alkalmazhatóság, holisztikus összítélet) és **TRIPOD+AI** (52 altétel, D/E, hőtérkép, hiánylista, saját kézirat) | munkapad | 1,0 |
| GRADE + SoF (E10: `grade_help`, `sof`, MID, alapkockázatok, `amstar2_consistency`), AMSTAR 2 nézet mindkét konvencióval | motor + munkapad | 1,0 |
| Kettős kinyerés (E6: összevetés, súgó, hatás, κ) + egyeztetés + konszenzus-CSV + egyetértési tábla | motor + munkapad | 0,75 |
| Kumulatív + buborék (E4c: kovariancia, sáv, metafor `predict()` referencia), táblavirtualizálás | motor + munkapad | 0,5 |
| figure-forge F2 (`meta`) + F3 (`rob`) + F5 (`--json`) + export-panel + szerver-újraellenőrzés | figure-forge + munkapad | 1,0 |
| composer C1/C2/C4 + élő PRISMA + E9 (`--studies`, PRISMA 2020 folyamatábra-spec); E8 teljes (X002–X022) + FINAL audit-kapu | composer + motor + munkapad | 0,5 |

**Elfogadás:**

- **Értékelés:** az értékelési munkafolyamatok (RoB 2, ROBINS-I/E, QUADAS-2, NOS, PROBAST+AI, TRIPOD+AI, AMSTAR 2,
  GRADE) végigvihetők a validator 1.1-gyel (json) és 1.0.0-val (bridge, őrökkel).
- **PROBAST+AI:** 34 slot, menetenként helyes teljesség.
- **Ábrák:** QC-tiszták, a számhűség 100% (figure-forge `numbers` és szerver-újraellenőrzés).
- **E2E:** zöld.
- **Lánc-visszajátszás:** változatlanul 100% a leképezhetőkre.

### 9.4 v2 — „predikciós modellek, kézirat, Claude-inbox, csiszolás” (4,5 hét)

| Tétel | Hol | Hét |
|---|---|---:|
| E13: c-statisztika → logit(c) + SE, O:E → ln(O:E) + SE (Debray 2017), `GEN` visszatranszformálás; kinyerési sablon; metamisc `valmeta` referencia | motor + munkapad | 1,25 |
| Kézirat-számegyezés: `szk.facts/v1` + P1 + kézirat-panel (TRIPOD+AI saját kéziratra a presubmit-jelentés mellett) | motor + presubmit + munkapad | 0,75 |
| Claude-inbox: AI-vázlat (PROBAST+AI / TRIPOD+AI / RoB) `origin: ai_draft`, emberi jóváhagyás (11. fejezet, 6. kérdés szerint) | munkapad + skill | 0,5 |
| Teljes angol felület, akadálymentesség, Playwright-lefedettség, vizuális regresszió, `zipapp` csomagolás, F4 stílusprofilok | munkapad + figure-forge | 1,0 |
| VA1/VA2 (vault), S1/S2 (science-monitor, opcionális), csiszolás Windows/macOS-en | vault + science-monitor + munkapad | 0,5 |
| Tartalék a Windows-specifikus hibákra | — | 0,5 |
| *(opcionális, csak ha a 2. kérdés válasza „B”)* Pyodide-kísérlet go/no-go feltételekkel | munkapad | *+0,5* |

**Elfogadás:**

- **Predikciós modell:** egy predikciós modelles áttekintés végigvihető a PROBAST+AI-tól a c-statisztika
  poolingjáig, referenciával validálva.
- **Kézirat:** a kézirat-ellenőrzés a motor számaival veti össze a szöveget.
- **Platformok:** kézi füstteszt Windows/Edge és macOS/Safari alatt.

### 9.5 Összesítés, függőségek, kockázatok

| Fázis | Hét | Halmozott |
|---|---:|---:|
| 0. fázis | 1 | 1 |
| MVP | 6,5 | 7,5 |
| v1 | 7 | 14,5 |
| v2 | 4,5 | **19** (+15% tartalék ≈ 22) |

**Függőségek:**

- **MVP:** csak a motor-PR-ekre épül (E1, E2, E3, E4a/b, E5, E7, E8-alap). Ezek a felhasználó saját repójában
  vannak, ezért gyorsan beolvaszthatók.
- **v1 kritikus útja:** validator V1 → értékelési képernyők; figure-forge F2 → publikációs export.
- **Párhuzamosíthatóság:** a plugin-PR-ek a munkapad fejlesztése mellett is haladhatnak.

| Kockázat | Val. / hatás | Kezelés |
|---|---|---|
| Számítás csúszik a JS-be („csak egy kis back-transform”) | közepes / magas | tilalmi lista, CI-grep, AST-lint, render-teszt, lánc-visszajátszás |
| Szerződés-sodródás két repó és öt plugin között | közepes / magas | kézfogás-hash, drift-őrök, kétverziós olvasás, legacy-adapterek |
| Klinikai adat felhőbe kerül (vault, OneDrive) vagy Claude-ba olvasódik | közepes / nagyon magas | adatosztályok, írás-tartás, L1–L6, `deny`, pillanatkép-kitakarás |
| A felület „hivatalosnak” mutatja az implikált RoB- vagy GRADE-ítéletet | közepes / magas | `algorithm` címke szó szerint, kötelező emberi ítélet, X017, X019 |
| A figure-forge függőségei Windows-on (rossz interpreter, Store-csonk) | magas / alacsony | interpreter-szonda, `unusable` + teendő, motor-SVG tartalék |
| Egyidejű írás (felület, Excel, ágens) | magas / közepes | ETag/409 + diff, egy-író elv, `data_version` figyelés, 423 Excel-zárnál |
| Vanília JS bonyolódása egy fejlesztővel | közepes / közepes | képernyőnkénti modulok, nincs számítás a felületen, erős E2E |
| Hatókör-kúszás (NMA, IPD, dózis–hatás) | közepes / közepes | a motor dokumentált korlátai a felületen is látszanak; ezekhez R-csomag ajánlott |

---

## 10. Szükséges módosítások — a pluginokban és a motorban

A marketplace CI-lint miatt a PR-szövegek és a plugin-fájlok **más plugin perjeles parancsára nem hivatkoznak**,
csak névvel és szkripttel (például „a figure-forge plugin `ff.py audit` alparancsa”). Méret: S ≤ 1 nap, M 2–3
nap, L ≥ 5 nap. Minden PR visszafelé kompatibilis; a kivételek a hibajavítások (V2, V3, V4, C4), ezeket jelöljük.

### 10.1 validator (`szilikaroly/szk-plugins`, `plugins/validator`)

| PR | Tartalom | Méret | Fázis |
|---|---|---|---|
| **V1 — JSON I/O** | `appraise.py --schema <tool> --json` → `szk.instrument/v1`, kanonikus enumerált válaszértékekkel és álnevekkel, polaritással (`router`/`reverse`), kritikus tételekkel. `--verify/--rollup <f.json> --json` (`szk.appraisal/v1` be, `szk.appraisal-result/v1` ki); `--list/--route … --json`. Ugyanez a `checklist.py`-ban: PROBAST+AI `development/…`/`evaluation/…` kulcsokkal, TRIPOD+AI D/E címkékkel és státusz-szótárral. A Markdown-út és a kilépési kódok változatlanok. | M | v1 |
| **V2 — GRADE publikációs torzítás** *(hibajavítás, H3)* | `strongly suspected` → −1 (kézzel −2 indoklással); `suspected` → `unresolved`: a rollup nem ad csendben 0-t, hanem döntést kér; a kimenet kimondja. Regressziós teszt a mai hibára. | S | 0. fázis |
| **V3 — verify helyessége** *(hibajavítás, H1, H2)* | a `checklist.py --verify` a státuszt csak a státusz-*cellából* olvassa (mint az `appraise.read_answers`); PROBAST+AI-nál a `### … (development)` / `(evaluation)` szakaszok szerint, menetenként számol. Teszt: üres TRIPOD → 0/52; csak fejlesztési kitöltés → 16/34; bizonyíték-szövegben álló „no” nem válasz. | S | 0. fázis |
| **V4 — AMSTAR 2 szókincs és konvenció** *(hibajavítás, H4)* | eszközönkénti álnév-tábla a globális `_norm` helyett (AMSTAR 2-nél „PY” = `partial_yes`); `--amstar2-partial-yes-critical meets\|weakness`, az alapérték a KB AMSTAR2-00 projektkonvenciója (`meets`); a kimenet kiírja a futott konvenciót, és ha eltér, a másik besorolást is | S | 0. fázis |
| **V5 — egység-metaadat** | `unit: result\|study\|outcome\|model\|index_test` minden eszköz referenciafejlécében | S | v1 |
| **V6 — `--capabilities`** | `szk.capabilities/v1` szerződés-hash-ekkel és `known_issues`-zal | S | 0. fázis |
| *(V7, opcionális)* | a hivatalos RoB 2 doménalgoritmusok (2019-es útmutató folyamatábrái) `algorithm: "published"` jelöléssel, az útmutató példáiból készült tesztekkel | L | később |

### 10.2 figure-forge (`plugins/figure-forge`)

| PR | Tartalom | Méret | Fázis |
|---|---|---|---|
| **F1 — lusta importok** *(H5)* | az `ff_style`/`ff_lib` (matplotlib) importja az alparancsokba kerül; az `audit`, `advise` és `--capabilities` stdlib-only; `audit --json`. Teszt: `audit` matplotlib nélküli interpreterrel. | S | 0. fázis |
| **F2 — `meta` alparancs** | `ff.py meta --request <szk.figure-request/v1> --json`: forest (súlyozott négyzet, gyémánt, PI-sáv, alcsoport-fejlécek és -összesítők, szövegoszlopok a `cells`-ből, nyíl a vágott CI-nél, log-tengely a motor osztásaival), kontúr-funnel (a motor poligonjai), Doi, buborék (a motor sávja), LOO, kumulatív. **Nem számol, nem kerekít**; a címke-QC, a tipográfia, a glif- és a szerkeszthetőségi audit változatlan; új `numbers` blokk. Teszt: az öt referencia-adatsor v2-fájljaira QC tiszta, `numbers.ok`. | L | v1 |
| **F3 — `rob` alparancs** | traffic-light mátrix és súlyozott összesítő sáv a `szk.rob-summary/v1`-ből | M | v1 |
| **F4 — stílusprofilok** | folyóirat-szélesség, betűkészlet, alapméret JSON-ból; a „nature” marad az alap | M | v2 |
| **F5 — gépi eredmény + kézfogás** | `--json` → `szk.figure-result/v1` stdout-ra; `.qc.json` `schema` mezővel; kilépési kódok: 0 tiszta, 3 maradék QC-sértés; `--capabilities` (pptx ↔ python-pptx, tiff ↔ Pillow) | S | 0. fázis (kézfogás) / v1 |

### 10.3 composer (`plugins/composer`)

| PR | Tartalom | Méret | Fázis |
|---|---|---|---|
| **C1 — séma és vizsgálat↔jelentés** | `export --format flow-json` → `"schema": "szk.prisma-flow/v1"`, `included_reports`, `included_studies` (`--study-map studies.json` alapján, különben `null`), `automation_removed`, `other_methods`; `prisma status --json`; `--capabilities` | M | 0. fázis (kézfogás) / v1 |
| **C2 — atomikus állapot és helyfeloldás** | `save_state`: `tmp` + `os.replace` (ma `path.write_text`); `COMPOSER_OUTDIR`; `prisma where --project <slug>` | S | v1 |
| **C3 — hordozható shebang** *(H7)* | `#!/usr/bin/env python3` a `#!/Users/szili/anaconda3/bin/python3` helyett a `collect`, `lookup`, `prisma` (és minden más) szkriptben | S | 0. fázis |
| **C4 — folyamatábra-címkék** *(hibajavítás, H7)* | záró doboz: „Studies included in review (n = I)” és „Reports of included studies (n = J)”; térkép nélkül csak a jelentés-sor, megjegyzéssel; regiszter-ág és egyéb módszerek ága; a spec validál a `szk.ff.flowchart/v1`-re | S–M | v1 |

### 10.4 presubmit (`plugins/presubmit`)

| PR | Tartalom | Méret | Fázis |
|---|---|---|---|
| **P1 — tény-összevetés** | `pc.py check … --facts facts.json`: új `claims` ellenőrzés `fact-mismatch` kóddal. Ha a kéziratban egy tény mintájához illeszkedő kontextusban (mérték neve + CI) eltérő szám áll, hibát ad a pontos hellyel és a javítással, a meglévő `{category, severity, code, message, where, fix}` formában. | M | v2 |
| **P2 — kézfogás + stdout JSON** | `--capabilities`; `--json -` | S | 0. fázis |

### 10.5 vault, science-monitor, marketplace

| PR | Tartalom | Méret | Fázis |
|---|---|---|---|
| **VA1 — adatvédelmi kapu** (vault) | `.vault-skip` (kihagyás) és `.vault/no-push` (helyben commit, push nem; állapot „PUSH VISSZATARTVA (adatvédelem)”); `sensitive_globs` (a `git add -A` után az illeszkedő fájlok kikerülnek az indexből, név, méret és sha256 a `.vault/held.txt`-be — nem csendes veszteség); opcionális TAJ-szűrő; `vault.py check <út> --json` | M | v2 (az MVP addig az L1–L6 rétegekkel véd) |
| **VA2 — dokumentáció** (vault) | a README figyelmeztet a klinikai adatra és a jelölőkre | S | v2 |
| **S1 — szerver-keményítés** (science-monitor, opcionális, H8) | adat és token csak tokennel; pontos Origin; `Sec-Fetch-Site`; CSP; `OPTIONS` 405; efemer port foglaltságnál | S | bármikor |
| **S2 — MA-projekt kártya** (science-monitor, opcionális) | csak olvasható kártya a `project export --format json` alapján | S | v2 |
| **M1 — szerződés-drift őr** (marketplace CI) | a `plugins/*/contracts/*.schema.json` azonos nevű fájljainak bájt-egyezése (Python-szkript) | S | 0. fázis |

### 10.6 A motor szükséges módosításai (ebben a repóban: `metaanalizis-asszisztens/`)

| PR | Tartalom | Kompatibilitás | Fázis |
|---|---|---|---|
| **E1** `metaelemzes/api.py` | 3.2 szerinti homlokzat; `engine_info()` generált opció- és szabály-metaadattal (`rules export --json`); `ma.py --capabilities` | új modul; a CLI fokozatosan erre áll | MVP |
| **E2** lokátorok + V025 | `_finding` kap `row/rows/line/fields/row_uid/blocking/kb_id` mezőt; `validate --request-json` (stdin); V025 képletnek látszó szöveg; README: 24→25 szabály | csak új kulcsok | MVP |
| **E3** spec | `metaelemzes/spec.py` (argparse-introspekció, `h_centre`/`metareg_robust` leképezés); `analyze --spec`, `--json-summary`, `run.json`; a `cmd_analyze` az opció-dictet ugyanezzel a függvénnyel építi | a kapcsolók változatlanok | MVP |
| **E4** `szk.ma.plot/v2` | a) `row_uid`, `row_index`, `display_text` i18n, `axis.ticks`, `sections.row_uids`, `subgroup_test`, `flags`, `source`; b) `funnel.contours` poligonok, `pseudo_ci`, `loo`, `influence`; c) `cumulative`, `bubble` (a meta-regresszió kovarianciája + sáv, metafor-referencia) | `--plot-schema v1` megmarad | MVP (a, b) / v1 (c) |
| **E5** SVG | `lang hu\|en` a `plots.*`-ban és a `make_plots`-ban (ma a „Vizsgálat”, „Súly”, „Alcsoport összesen”, „Heterogenitás:” kódba égetett); `<g id="layer-…">`, soronként `<g id="study-<row_uid>" data-row data-y data-lo data-hi>`; U+2212 mínusz | alapértelmezés `hu` → a mai kimenet byte-azonos | MVP |
| **E6** `kettos.py` + `compare` | kettős kinyerés: kulcs, tűrés, `format_only` (`tableio`), súgók, `impact`, κ CI-vel | új | v1 |
| **E7** napló JSON + `actor` | `project list\|show\|status --json`, `export --format json`; `actor` oszlop (`_ADDED_COLUMNS`); opcionális activity-hozzáfűzés (`MA_ACTIVITY_LOG`) | a meglévő Markdown-export változatlan | MVP |
| **E8** `audit.py` + `project audit` | X001–X022 (6.4), `--json`; KB-betöltés; `checkpoint --stage FINAL --audit-gate` | új | MVP (alap) / v1 (teljes) |
| **E9** PRISMA | `prisma check --studies studies.json` (I), `--emit-flowchart` (teljes PRISMA 2020 spec `szk.ff.flowchart/v1`-ben), X014/X015/X020/X021 | új kapcsolók | v1 |
| **E10** `grade_help.py` + SoF + AMSTAR 2 | RoB-súlyarány, PI/CI vs. null, CI vs. MID, OIS (a `power` modulból), a tesztek értelmezhetősége; `sof(assumed_risks)` a `totals.absolute_per_1000` általánosításaként; `amstar2_consistency` (a `grade_consistency` mintájára, a KB AMSTAR2-00 konvenciójával); `facts.json` | új, csak javaslat és konzisztencia | v1 (facts: v2) |
| **E11** `ma.py gui` + `ma_gui/` | maga a munkapad, indítók, tesztek | új | MVP |
| **E12** skill- és ágensleírás | a `metaanalizis` skill és a `ma-ellenorzo`/`ma-ertekelo` leírása: mikor ajánlja a munkapadot; a `project audit` az ellenőrzés része; Artifact-tilalom; inbox-feldolgozás (v2) | dokumentáció | MVP / v2 |
| **E13** predikciós modell MA | `convert cstat`, `convert oe`, `--gen-scale identity\|log\|logit`; metamisc-referencia (új generátor-szkript); amíg nincs referencia, `known_gap` | új | v2 |

### 10.7 Összesítő

| Komponens | PR-ek | Méret | Fázis |
|---|---|---|---|
| motor | E1–E13 | ≈ 4,5 hét (az ütemtervben benne van) | MVP / v1 / v2 |
| validator | V1–V6 (+V7) | ≈ 1,2 hét | 0 / v1 |
| figure-forge | F1–F5 | ≈ 1,7 hét | 0 / v1 / v2 |
| composer | C1–C4 | ≈ 0,6 hét | 0 / v1 |
| presubmit | P1–P2 | ≈ 0,5 hét | 0 / v2 |
| vault | VA1–VA2 | ≈ 0,5 hét | v2 |
| science-monitor | S1–S2 (opcionális) | ≤ 2 nap | bármikor / v2 |
| marketplace | M1 | ≤ 1 nap | 0 |

**Új plugin nem kell.** A munkapad a motor repójában él. A Claude-integráció a repó saját `metaanalizis`
skilljén keresztül megy; hogy a motor és a munkapad később pluginná váljon-e, azt az 1. kérdés dönti el.

---

## 11. Nyitott kérdések a felhasználónak

Hat döntés, mindegyiknél ajánlott alapértelmezéssel. Ha egyikre sem érkezik válasz, a terv az ajánlott
értékekkel hajtható végre.

**1. Hol éljen a munkapad kódja?**

- **A) A motor repójában** (`metaanalizis-asszisztens/ma_gui/`, indítás: `python ma.py gui`). A Claude-integráció
  a repó `metaanalizis` skilljén megy. — **AJÁNLOTT** (mindkét bíráló ezt preferálta: egy repó, közös CI, a motor
  és a felület együtt tesztelhető).
- B) A motor és a munkapad együtt új szk-plugins pluginként (`meta-engine`). Egylépéses telepítés, de előbb el
  kell dönteni, hogy a KB seed-JSON-jai közzétehetők-e; a teljes szöveg semmiképp.
- C) Külön `ma-studio` plugin a marketplace-ben, a motor maradna a saját repójában. A bírálók szerint ez a
  leggyengébb: két repó közötti feloldás és verziókötés kell hozzá.

**2. Kell-e munka Python nélküli (zárolt kórházi) gépen?**

- **A) Csak olvasható, kitakaró HTML-pillanatkép** (már az MVP-ben). Szerkesztés csak Pythonos gépen. — **AJÁNLOTT**
- B) Mint az A, és a v2-ben Pyodide-kísérlet a böngészőben futó motorral, előre rögzített go/no-go feltételekkel:
  344 + 3268 zöld, ≤ 20 MB, ≤ 5 s indulás, Edge/Safari `file://`. Ráfordítás +0,5 hét a kísérletre; siker esetén
  további ≈ 2 hét.
- C) Validálás JS-ben újraírva. **Nem ajánlott:** a validált motor második implementációja lenne.

**3. Kerülhet-e betegszintű (C osztályú) adat egy munkapad-projektbe?** Például saját kohorsz egy predikciós
modell külső validálásához.

- A) Soha. A munkapad csak A/B osztályt fogad el, C-t el sem indít.
- **B) Igen, de csak `_privat/`-ban.** C osztályú projekt a vault-gyökér alatt csak `.gitignore`-blokkal és
  pre-commit őrrel nyílik meg; erősen javasolt a vaulton és a OneDrive-on kívüli hely. Claude `deny`-szabály
  kötelező, a Claude-inbox ki van kapcsolva. — **AJÁNLOTT** (a PROBAST+AI/TRIPOD+AI munkáknál ez valószínű igény)
- C) Igen, és a vault alatt is maradhat a védőrétegekkel, külön figyelmeztetés nélkül.

**4. GRADE, publikációs torzítás: mi legyen a „Suspected” (gyanított) ítélettel?**

- **A) Feloldatlan.** A rögzítés addig tiltott, amíg a felhasználó nem választ 0-t vagy −1-et indoklással; a
  „Strongly suspected” −1. — **AJÁNLOTT** (módszertanilag a legóvatosabb; a validator PR-V2 így készül)
- B) Mindig −1.
- C) 0; csak a „Strongly suspected” minősít le.

**5. Hogyan dolgozik a második kinyerő vagy értékelő** (kettős kinyerés, kettős RoB/PROBAST)?

- **A) A saját gépén, saját munkapad-példánnyal; fájlcserével.** Az A/B CSV és az értékelés-JSON a
  `kettos/` és az `appraisals/` mappába kerül; az összevetés és az egyeztetés az első gépen történik. — **AJÁNLOTT**
- B) Ugyanazon a gépen, felváltva, külön monogrammal.
- C) Hálózati elérés a szerverhez (LAN). **Nem ajánlott:** bontja a loopback-biztonsági modellt.

**6. Készíthet-e Claude vázlatot az értékelésekhez** (PROBAST+AI / TRIPOD+AI / RoB), a `probast-tripod-ai`
skill és a `ma-ertekelo` útmutatása szerint?

- **A) Igen, de csak „AI-vázlat” státusszal.** Emberi jóváhagyás kötelező; nem számít második értékelőnek
  (κ-ban és konszenzusban sem); C osztálynál tiltva. Csak publikált cikkre kérhető, mert a cikk szövege a
  Claude-munkamenetben a modell-szolgáltatóhoz kerül. — **AJÁNLOTT**
- B) Nem; csak kézi kitöltés.
- C) Igen, és második értékelőként is számíthat. **Nem ajánlott** a jelenlegi módszertani ajánlások mellett.

*Szándékosan nem kérdezzük, mert a meglévő források eldöntik:*

- az AMSTAR 2 „részben igen” kezelését a KB AMSTAR2-00 projektkonvenciója rögzíti (`meets`), és mindkét
  besorolás látszik;
- a SoF alapkockázata a kontroll-pool, külső kockázat felvehető;
- a nyelv HU + EN (a hat nyelv az Anamnézis-app sajátja; itt nem indokolt).

---

## A. függelék — a bírálók által jelzett hiányosságok és a kezelésük

| Kifogás (javaslat) | Kezelés ebben a tervben | Hol |
|---|---|---|
| A PDF új lapon nem tud `X-MA-Token` fejlécet küldeni, és a CSP blokkolja a `blob:` kereteket (server) | aláírt, 10 perces `/f/<doc>/<exp>/<hmac>` URL, felső szintű navigáció, `Sec-Fetch-Site` ellenőrzés; új lap tokenje `BroadcastChannel`-en | 7.1 T5 |
| Nincs ökoszisztéma-szintű képesség-kézfogás; nem nyilvános `installed_plugins.json`-ra támaszkodik (server) | `szk.capabilities/v1` szerződés-hash-ekkel és `known_issues`-zal; a `_resolve.sh` sorrendje; az `installed_plugins.json` csak tartalék | 4.1, 5.1 |
| Vékony kereszt-ellenőrzések, fej nélkül nem elérhetők (server) | 22 X-szabály a motor `project audit` parancsában, KB-azonosítóval, FINAL audit-kapuval | 6.4, E8 |
| Nincs hash-lánc és újrafuttatható argv (server) | `activity.jsonl` hash-lánccal, argv-vel, be/ki sha256-tal; `rerun.cmd`/`.sh`; determinisztikus ZIP | 4.16–4.17 |
| A pillanatkép csak v2-ben (server) | kitakaró pillanatkép az MVP-ben | 5.9, 9.2 |
| A GRADE „suspected” nyitva marad (server) | feloldatlan állapot, X019; validator V2 | 3.5.12, 4.14 |
| A 6 hetes MVP optimista (server) | 0. fázis leválasztva; az MVP 6,5 hét; a táblavirtualizálás v1-be került; +15% tartalék javasolt | 9 |
| A `ProcessPoolExecutor` nem szakítható meg (server) | meleg `multiprocessing.Process` worker, `terminate()` + újraindítás | 2.2 |
| A ≥ 95% HTTP-paritás állítás, nem elemzés (server) | mért leképezhetőség; 106 ellenőrzés (3,2%) már most azonosítva mint nem leképezhető; cél a leképezhetők 100%-a | 8.2 |
| Az AMSTAR 2 motor-fallback a validator logikájának második implementációja; a PY-hiba kimaradt (server) | nincs második rollup: `amstar2_consistency` konzisztencia-ellenőrzés (mint a `grade_consistency`) paritásteszttel; validator V4 | 5.4, 6.2, 10.1 |
| Funnel-kontúr JS-ben (`x = c ± z·se`) (server) | a motor kész poligonokat ad (`funnel.contours`) | 4.6 |
| Eredet és értékelés SQLite-táblákban — bináris, nem diffelhető (server) | JSON-oldalfájlok (`.prov.json`, `appraisals/*.json`, `consensus.json`, `specs/*.json`); az SQLite csak a meglévő naplótáblákat tartja | 2.4 |
| A felület a marketplace-ben, a motor más repóban (plugin) | a munkapad a motor repójában; a pluginok csak adapterek | 2.3, 11/1 |
| 2 hetes szerződés-fázis a felhasználói érték előtt (plugin) | a 0. fázis 1 hét, és maga is értéket ad (hibajavítások); a szerződések az MVP-vel együtt jönnek | 9.1 |
| A spec `ht_centre`/`robust` vs. a motor `h_centre`/`metareg_robust` (plugin) | a spec a `DEFAULTS` kulcsait használja; generált, oda-vissza tesztelt argv-leképezés | 4.4, E3 |
| A cella-alapú olvasás nem javítja a PROBAST menetek közti számlálást (plugin) | menettel minősített kulcsok a szerződésben + szakaszonkénti verify (V3) | 4.11, 10.1 |
| Token az URL-fragmentben, a böngésző argv-jében látható (plugin) | egyszer használható, 60 s-os indítókód → munkamenet-token | 7.1 T4 |
| Nincs OneDrive/iCloud-felismerés és Claude `deny` (plugin) | megvan | 7.5 |
| A pre-commit őr minden vault-commitot blokkol (plugin) | a `.gitignore`-blokk az elsődleges; az őr csak a kényszerített vagy követett fájlra lép; VA1 `sensitive_globs` | 7.5 L2–L3 |
| Minden elemzés CLI-alfolyamat → lassú Windows-on (plugin) | az explore és a commit is a meleg worker-folyamatban fut (nincs új interpreter-indulás); a commit egyenértékű argv-t rögzít, a CLI-vel való bájt-paritást teszt igazolja | 2.2, 2.6, 8.1 |
| JS-ben újraimplementált parszer, szabályok, rollupok (offline) | a JS nem számol, nem validál; a teljes tábla a motorban validálódik ms alatt | 6 |
| Három mód és három transzport; fájlbusz a OneDrive-on (offline) | egy élő mód + pillanatkép; nincs fájlbusz; ideiglenes fájlok a projekten kívül, `%LOCALAPPDATA%`-ban | 2.4–2.5 |
| A File System Access API csak Chromiumban van, az engedély nem tartós (offline) | nem használjuk | — |
| Ideiglenes JS-konverziók kéziratba kerülhetnek (offline) | minden konverzió a motorban fut, `estimated` jelzővel | 3.5.4, 4.7 |
| A GRADE „suspected” → −1 túllő (offline) | feloldatlan, emberi döntéssel | 4.14 |
| A figure-forge `audit` matplotlibet igényel — kimaradt (offline) | `unusable` állapot és F1 | 5.0 H5 |
| A validált `RULES` runtime-JSON-ba költöztetése érinti a tesztelt kódot (offline) | nem költöztetjük; csak csak-olvasó `rules export --json` | E1 |

---

## B. függelék — mit honnan vettünk át

| Forrás | Átvett elemek |
|---|---|
| local-server-first (alap) | egy folyamat a motor repójában; in-process motor-homlokzat; explore/commit szétválasztás (`run_id` + adat- és spec-sha256); egyszer használható indítókód → munkamenet-token; teljes táblás élő validáció; ábra-számhűség-ellenőrzés; adatosztályok A–D; OneDrive/iCloud és vault felismerése; Claude `deny`-szabályok; ideiglenes fájlok a projektmappán kívül; telepítésmentes önteszt (`--dump-dom`); a front-end tilalmi listája CI-greppel; `grade_help` tanácsadó |
| plugin-ecosystem-first | `szk.capabilities/v1` kézfogás `known_issues`-zal; verziózott `szk.*` szerződések sodródás-őrrel; X-szabályok a motor `project audit` parancsában és FINAL audit-kapu; hash-láncolt tevékenységnapló pontos argv-vel és újrafuttató szkripttel; aláírt rövid életű fájl-URL-ek; `_privat/`, kezelt `.gitignore`-blokk, opt-in pre-commit őr és „már felment?” ellenőrzés; GRADE „Suspected” = feloldatlan; kettős kinyerés `impact` oszlop és κ; gyermek-érzékenységi futások AKTUÁLIS/ELAVULT jelöléssel; KB-jelvények az eredménymezők mellett; `szk.ma.plot/v2` kontúr-poligonokkal és kétnyelvű `display_text`-tel; figure-forge `numbers` blokk; lánc-visszajátszás magyar formátumú CSV-n; stub-pluginok a képesség-mátrix teszteléséhez; Windows-higiénia (`CREATE_NO_WINDOW`, `PYTHONUTF8`, Store-csonk szűrése, fenntartott nevek és ADS tiltása) |
| offline-first | projektadat soha nem kerül böngészőtárolóba (D8); Excel-képletinjekció elleni védelem; generált opció- és szabály-metaadat (argparse/`DEFAULTS` introspekció); protokoll-eltérés sáv és parancs-előnézet; enumerált kanonikus válaszértékek és menettel minősített kulcsok a validator-szerződésben; AMSTAR 2 PY ≠ „Partial yes” javítás (PR-V4); composer hordozható shebang (PR-C3); `studies.json` vizsgálat↔jelentés nyilvántartás a PRISMA I/J-hez; `data-*` render-konzisztencia és `plot_data` ≡ `results` belső konzisztencia-tesztek; „Nem hiba — indoklás” gomb naplózott döntéssel; TAJ-ellenőrzőjegyes azonosító-szűrő |
