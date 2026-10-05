# Metaanalízis-asszisztens

Szisztematikus áttekintés és metaanalízis asszisztens Claude Code-hoz: egy **orkesztrátor** és három
**alágens** (tervező, ellenőrző, értékelő), egy **metafor-ral validált számítási motor** (csak Python
standard könyvtár, telepítés nélkül fut), egy **SQL-tudásbázis** a módszertani döntésekhez, és egy
**projektnapló**, amely minden döntést és ellenőrzési megállapítást visszakereshetően rögzít, valamint egy helyi,
böngészős **munkapad** (MA-munkapad). A repó egyúttal **Claude Code-plugin-marketplace** is: a `metaanalizis` plugin
bármelyik mappában elérhetővé teszi az ágenseket és a skillt.

**Új felhasználó?** Kezdd a [TELEPITES.md](TELEPITES.md) útmutatóval (előfeltételek, letöltés, első futtatás,
plugin-telepítés, engedélyek, adatvédelem, hibaelhárítás).

```
                ┌──────────────────────────────────────────────┐
  felhasználó → │ metaanalizis-asszisztens  (orkesztrátor)      │
                │  .claude/agents/metaanalizis-asszisztens.md   │
                │  .claude/skills/metaanalizis/SKILL.md         │
                └──┬───────────────┬────────────────┬──────────┘
         kezdéskor │   menet közben│és a végén      │ a következtetések előtt
           ┌───────▼──────┐ ┌──────▼───────┐ ┌──────▼───────┐
           │ ma-tervezo   │ │ ma-ellenorzo │ │ ma-ertekelo  │
           │ protokoll,   │ │ checkpoint / │ │ GRADE, SoF,  │
           │ elemzési terv│ │ final; PASS/ │ │ AMSTAR 2,    │
           │ eszközök (S00)│ │ FAIL kapuk  │ │ MCID         │
           └───────┬──────┘ └──────┬───────┘ └──────┬───────┘
                   └────────┬──────┴────────────────┘
          python ma.py kb … │ python ma.py analyze/validate … │ python ma.py project …
          ┌─────────────────▼─────┐ ┌───────────────────────┐ ┌──────────────────────┐
          │ tudásbázis (SQLite,   │ │ metaelemzes motor      │ │ projektnapló (SQLite)│
          │ FTS5): szabályok,     │ │ (metafor-validált)     │ │ döntések, megállapí- │
          │ tudás, képletek, lis- │ │ → report.md, forest/   │ │ tások, kapuk, GRADE, │
          │ ták, eszközök, teljes │ │   funnel SVG, JSON     │ │ futtatások           │
          │ szöveg (helyben)      │ │                        │ │                      │
          └───────────────────────┘ └───────────────────────┘ └──────────────────────┘
```

## Gyors indulás

**Claude Code-ban** (a repó gyökeréből):

```bash
claude --agent metaanalizis-asszisztens          # az orkesztrátor a fő szál
# vagy egy normál munkamenetben:
/metaanalizis reviews/glp1-terhesseg "GLP-1 RA terhesség előtti expozíció és veleszületett rendellenességek"
```

**Claude Code-pluginként** (egyszer telepíted, utána bármely mappából elérhető; a repó privát, ezért előbb a
GitHub-meghívót kell elfogadni, és a gépen a git-azonosításnak működnie kell — lásd [TELEPITES.md](TELEPITES.md)):

```
/plugin marketplace add szilikaroly/anamnezis-asszisztens
/plugin install metaanalizis@anamnezis-asszisztens
```

Indítás pluginból: `claude --agent metaanalizis:metaanalizis-asszisztens`, vagy egy munkamenetben
`/metaanalizis:metaanalizis <projektmappa> "<kérdés>"`. A plugin-változatban az alágensek neve névtérrel szerepel
(`metaanalizis:ma-tervezo`, `metaanalizis:ma-ellenorzo`, `metaanalizis:ma-ertekelo`). A plugin nem adhat
engedélyeket: a javasolt engedélylistát a [TELEPITES.md](TELEPITES.md) 5. pontja adja, a saját beállításaidba.

**Csak a motor** (parancssor, bármely mappából):

```bash
python metaanalizis-asszisztens/ma.py analyze --data adat.csv --measure SMD --out eredmeny/
python metaanalizis-asszisztens/ma.py analyze --data adat.csv --measure OR --outliers --moderators év --robust
python metaanalizis-asszisztens/ma.py validate --data adat.csv --measure OR
python metaanalizis-asszisztens/ma.py validate --data adat.csv --measure OR --json    # szk.ma.validation/v1
python metaanalizis-asszisztens/ma.py analyze --spec 05_elemzes/specs/o1_primary.json --project .   # → 05_elemzes/<kimenet>/<run_id>/
python metaanalizis-asszisztens/ma.py power --k 18 --effect 0.7 --n1 15 --n2 15 --heterogeneity moderate
python metaanalizis-asszisztens/ma.py prisma check --composer prisma-flow.json     # kilépési kód 1, ha hibás
python metaanalizis-asszisztens/ma.py kb search "few studies random effects"
python metaanalizis-asszisztens/ma.py project audit <projektmappa> --json        # X-szabályok; 1, ha van error
python metaanalizis-asszisztens/ma.py rules export --json                        # a motor V/P/X-szabályai
# v1: értékelés, GRADE / SoF, kettős kinyerés, PRISMA-folyamatábra, E4c-ábrák
python metaanalizis-asszisztens/ma.py appraisal instruments                      # RoB 2, ROBINS-I/E, QUADAS-2, NOS, QUIPS, JBI, PROBAST+AI, TRIPOD+AI, AMSTAR 2, GRADE
python metaanalizis-asszisztens/ma.py appraisal check S1.rob2.o1.SzK.json        # teljesség, implikált ítélet (1, ha nem teljes)
python metaanalizis-asszisztens/ma.py appraisal agreement a.json b.json          # Cohen-féle κ CI-vel, eltérések
python metaanalizis-asszisztens/ma.py appraisal sync-rob <projekt> --outcome o1  # rob oszlop a végső összítéletekből (--apply)
python metaanalizis-asszisztens/ma.py grade advice --run <futás> --project <projekt>   # GRADE-tanács (piszkozat, „Miért?”)
python metaanalizis-asszisztens/ma.py grade sof --run <futás> --project <projekt> --format md
python metaanalizis-asszisztens/ma.py kettos compare --project <projekt> --outcome o1   # kettős kinyerés összevetése
python metaanalizis-asszisztens/ma.py prisma check --composer prisma-flow.json --studies 03_adatok/studies.json --emit-flowchart folyamatabra.json
python metaanalizis-asszisztens/ma.py figure --plot <futás> --kind bubble --lang en --out bubble_en.svg
python metaanalizis-asszisztens/ma.py --capabilities                             # szk.capabilities/v1
python metaanalizis-asszisztens/ma.py selftest
```

Az `analyze -h` súgó végén minden mérték szükséges CSV-oszlopai szerepelnek.

Python 3.9 vagy újabb kell. Windows-on `python` vagy `py -3` (a `.claude/.venv` is jó), Linuxon/macOS-en `python3`.
Külső csomag nem kell; a PDF-ek tudásbázisba töltéséhez opcionálisan `pip install pypdf`.

**MA-munkapad** (helyi, böngészős felület ugyanahhoz a projektmappához és naplóhoz):

```bash
python metaanalizis-asszisztens/ma.py gui --project <projektmappa>     # magyar álnév: munkapad
```

További kapcsolók: `--port N`, `--no-browser`, `--lang hu|en`, `--idle-hours H`. Ha a `ma_gui/` hiányzik vagy nem
importálható, a parancs érthető hibával (2-es kóddal) áll le; a motor ettől függetlenül működik.

Két alparancs a munkapad futtatása nélkül:

```bash
# csak olvasható pillanatkép a társszerzőknek: EGY HTML-fájl, Python és hálózat nélkül nyitható
python metaanalizis-asszisztens/ma.py gui snapshot --project <mappa> [--out x.html] [--redact …] [--keep …]
# determinisztikus audit-csomag: 07_ellenorzes/audit/<dátum>/…zip (manifest.json, activity.jsonl, rerun.cmd/.sh)
python metaanalizis-asszisztens/ma.py gui audit-export --project <mappa>
```

A pillanatkép kitakarási alapértékei az adatosztályból jönnek: **A** — az adattáblák benne vannak; **B** — a táblák
kimaradnak (kérésre `--keep tables`), az értékelők neve monogram; **C** — táblák soha. A `_privat/` mappa, a PDF-ek és
a tudásbázis teljes szövege egyik osztályban sem kerül bele. Kilépési kód: 0 kész, 2 hibás kérés (pl. táblák C
osztályú projektből), 1 egyéb hiba. **A pillanatképet soha ne töltsd fel és ne publikáld — Claude Artifactként sem.**

## A motor (`metaelemzes/`)

| Terület | Tartalom |
|---|---|
| Hatásméretek | MD (variancia: `--md-vtype unequal` / `pooled` = metafor HO), SMD (Hedges g), Cohen d — variancia `--smd-vtype` LS / LS2 (Borenstein) / UB / METAN_COHEN / METAN_HEDGES (Stata metan, MetaXL), Glass Δ (`SMD_GLASS`, a kontroll SD-jével; `--glass-vtype`), párosított elrendezés: átlagos változás (`MC`) és standardizált változás (`SMCC`) — a változás SD-je `sd_diff`-ből, `sd1`+`sd2`+`r`-ből vagy Σ(d − d̄)²-ből —, ROM, OR, RR, RD, arányok (PR, PLN, PLO, PAS, PFT), korreláció (COR, ZCOR), generikus (yi + vi/sei; vagy yi + n1, n2 közölt SMD-ként: `--gen-smd-vtype`) |
| Modellek | közös (fix) hatás, véletlen hatás (DL, REML, ML, PM, HE, SJ), IVhet (Doi 2015; τ² alapból DL, más becslő figyelmeztetéssel), Mantel–Haenszel (OR, RR, RD — Sato- vagy Greenland–Robins-variancia), Peto. `--tau2` alapértéke a modellé (random → REML, ivhet → DL), és az alcsoport-, torzítás- és érzékenységi elemzések is ezt kapják |
| CI / PI | z, t, HKSJ, HKSJ ad hoc; predikciós intervallum t(k−2) / t(k−1) / z |
| Heterogenitás | Q, I², τ², τ; H = max(1, √(Q/df)) és módosított H² = (Q − df)/df (Stata admetan); CI: Q-profile (metafor), Higgins–Thompson (a középpont `--ht-centre truncated` = R meta, vagy `untruncated`), Borenstein-féle τ²-CI |
| Moderátorok | alcsoport (külön vagy közös τ² — `--common-tau2` véletlen hatású és IVhet modellnél; Q_between; az alcsoport súlyrészesedése a teljes modellben, MetaXL/RevMan 'Subtotal'; a Methods csak `--subgroup-prespecified` mellett írja, hogy előre tervezett), vegyes hatású meta-regresszió (τ²-becslő: a `--tau2`, ha ott értelmezett, vagy `--metareg-tau2` REML/ML/DL/PM/HE/SJ/FE — FE = közös hatású meta-regresszió; Wald vagy Knapp–Hartung; QM, QE, R²; kategóriás moderátor dummy-kódolással); `--robust`: HC1 szendvics SE-k t(k − p) próbákkal, robusztus F, súlyozott R² / root MSE, a meta-regresszió súlyaival 1/(v+τ²) (= metafor `robust(…, adjust = TRUE)`); a Stata `regress [aw=1/v], vce(robust)` eredményét `--metareg-tau2 FE` adja |
| Torzítás | Egger (a tengelymetszet CI-je t vagy z: `--egger-ci-dist`), bináris kimenetnél Harbord és Peters (log OR; az Egger ilyenkor csak tájékoztató, Sterne 2011); SMD-nél — `--measure GEN --gen-smd-vtype …` mellett is — a riport jelzi, hogy az Egger csak tájékoztató (Pustejovsky & Rodgers 2019), Begg–Mazumdar (`--begg-method auto/exact/normal`, `--begg-continuity`), trim-and-fill (L0/R0, metafor-algoritmus; `--trimfill-trim-model fixed` = meta::trimfill alapértelmezése), kontúr-javított funnel, Doi-plot + LFK-index (heurisztikus, érzékenységi jellegű), Rosenthal fail-safe N (csak tájékoztató) |
| Érzékenység | leave-one-out, befolyás-diagnosztika (rstudent, DFFITS, Cook, cov.ratio, hat, DFBETAS — metafor-kritériumok), kumulatív elemzés, kiugró-szűrés (`--outliers`: dmetar::find.outliers szabály + újraillesztés) |
| Visszatranszformálás | OR/RR/ROM exp, arányok, r; PFT: harmonikus átlag n (metafor/meta) vagy `--pft-backtransform variance` (MetaXL: m = 1/Var(t) minden összesített becslés saját SE-jéből) |
| Erőelemzés | `ma.py power`: prospektív erő (Hedges & Pigott 2001, dmetar::power.analysis konvenció; fix / alacsony / közepes / magas heterogenitás vagy τ² / I²; OR → d — OR mellett `--v` / `--tau2` nem adható; ln OR-skálájú vizsgálati varianciához `--measure GEN --effect <ln OR> --v …`), a szükséges k (`--target-power`) |
| PRISMA | `ma.py prisma check`: a folyamatábra dobozszámainak konzisztenciája (PRISMA 2020 és 2009; P001–P017; a negatívra adódó levezetett doboz, pl. C > B vagy H > G, hiba), bemenet JSON, a composer `prisma-flow.json`-ja, a projekt `prisma_folyamat.md` táblázata (ezres tagolással is; az egyéb módszerek ágának soraival), `--A1 … --I` és `--om-…` (egyéb ág). `--composer` és `--md` együtt: a composer számai érvényesek, minden eltérő doboz és kizárásiok-bontás (H, egyéb ág) P017-hiba. `--studies 03_adatok/studies.json` (szk.ma.studies/v1): a bevont vizsgálatok (I) és a hiányzó J a vizsgálat-térképből, eltérésnél P017; `--emit-flowchart OUT.json [--flowchart-lang en\|hu]`: a teljes PRISMA 2020 folyamatábra-specifikáció (szk.ff.flowchart/v1) a figure-forge-nak (`ff.py flowchart --spec OUT.json --width double`) |
| Értékelés (v1) | `ma.py appraisal …`: natív eszköz-definíciók (`metaelemzes/instruments/`, szk.instrument/v1; forrás: szk-plugins validator 1.0.0, eszközönként licenccel; magyar tételszöveg és kezdőknek szóló súgó): RoB 2, ROBINS-I, ROBINS-E, QUADAS-2, NOS, QUIPS, JBI, PROBAST+AI (két menet, 34 jelzőkérdés), TRIPOD+AI (52 altétel, D/E), AMSTAR 2, GRADE. Értékelés-fájlok: `04_torzitas_kockazat/appraisals/<egység>.<eszköz>[.<cél>].<értékelő>.json` (szk.appraisal/v1, atomi és ellenőrzött mentés). `check`: teljesség (PROBAST+AI-nál menetenként), implikált ítélet az algoritmus címkéjével (RoB 2 / ROBINS / QUADAS-2 / QUIPS: „konzervatív” — NEM a hivatalos folyamatábra; AMSTAR 2, GRADE: publikált algoritmus; NOS: csillagszám küszöb nélkül; PROBAST+AI, JBI, TRIPOD+AI: emberi ítélet), az implikálttól eltérő ítélet indoklással (X017); `agreement`: Cohen-féle κ aszimptotikus SE-vel és CI-vel; `consensus`; `rob-summary`: forgalmi lámpa és súlyarány kockázati szintenként (szk.rob-summary/v1); `sync-rob`: a kinyerési tábla `rob` oszlopa a végső összítéletekből (szk.ma.rob-sync-proposal/v1; `--apply`: formátumtartó írás, eredet: calculated, döntés a naplóba); `route`: eszköz-javaslat elrendezésből. Az AI-vázlat (`origin: ai_draft`) tételenként egyszerű nyelvű indoklást és idézetet kér, csak emberi jóváhagyással (`approve`) válik késszé, és soha nem értékelő (11. fejezet, 6. döntés) |
| GRADE, SoF (v1) | `ma.py grade advice`: szk.ma.grade/v1 piszkozat — doménenként a motor számai (magas RoB súlyaránya és a „magas RoB nélkül” gyermek-futás, I² CI-vel, PI vs. null/MID, CI vs. MID, OIS a `power` modulból, eseményszám, a publikációs torzítás tesztjeinek értelmezhetősége) és javaslat „Miért?” szöveggel (mit kérdez, miért ez, mi változtatná meg, mi bizonytalan); `grade save` / `show` / `record`: a GRADE-tár (`06_kezirat/grade/<kimenet>.grade.json`; a bizonyosság a lépésekből, üres, amíg nyitott domén vagy feloldatlan „gyanított” publikációs torzítás van — 4. döntés; a rögzítés a projektnaplóba előjeles lépés-szövegekkel); `grade sof`: Summary of Findings sor (szk.ma.sof/v1; abszolút hatás /1000 CI-vel a kontroll-poolból vagy külső alapkockázatból, ⊕ bizonyosság, GRADE-megfogalmazás; `--format md\|csv\|html`, `--save`); `grade amstar2`: AMSTAR 2 besorolás mindkét konvencióval |
| Kettős kinyerés (v1) | `ma.py kettos compare\|reconcile\|report\|status` (álnév: `kettős`): a két kinyerő táblájának (`03_adatok/kettos/<kimenet>.A.csv`, `.B.csv`) cellánkénti összevetése (szk.ma.compare-result/v1: formai eltérés, tűrés, valószínű ok — tizedesvessző, ×10, SE/SD-csere, felcserélt karok, mértékegység … —, hatás a vizsgálat és az összesített becslés értékére), egyetértés (κ CI-vel, ICC(A,1), Bland–Altman), indokolt döntések (szk.ma.consensus/v1) és konszenzus-CSV az A formátumában; feloldatlan eltérés mellett az S08 PASS nem adható (X009) |
| Konverziók | `ma.py convert <fajta>`: medián/IQR/tartomány → átlag/SD (`median`; Luo 2018, Wan 2014, Hozo 2005), SE/CI → SD (`se`, `ci`), CI → SE (`se-from-ci`; kis mintánál `--df`: t-kvantilis), p → SE (`se-from-p`), t → összevont SD (`sd-from-t`) vagy Cohen d (`d-from-t`; `--hedges`: Hedges g = J·d, a variancia g-ből), csoportok összevonása (`combine`), változás-SD (`change`) és a korreláció visszaszámolása (`corr-from-change`; az egymásnak ellentmondó SD-kből adódó |r| > 1 hiba), közös kontroll felosztása (`split-control`), d↔lnOR↔r (`logor-to-d`, `d-to-logor`, `r-to-d`, `d-to-r`), közölt SMD → variancia (`smd-var`), párosított összegek → átlagos változás és SD (`paired-sums`) |
| Adatvalidálás | 30 szabály (V001–V030; `validate --json`: szk.ma.validation/v1 sor-, oszlop- és fájlsor-lokátorokkal, sor-azonosítóval (`row_uid`) és kizárás-jelzővel; `validate --request-json`: nyers cellák a stdin-ről, ugyanazzal az értelmezéssel), pl. SD helyett SE gyanúja, mértékegység-eltérés, kettős nulla, közös kontroll, ferde eloszlás, kevés vizsgálat, nem számszerű érték opcionális oszlopban, a hatásméret-számításból kimaradt sor, kétértelmű számformátum, elcsúszott (kizárt) sor, nincs elemezhető vizsgálat (V026, k = 0: hiba, a `validate` is 1-gyel lép ki), fel nem ismert rob/estimated érték (V027), ismétlődő oszlop (V028, az első számít), hiányzó vizsgálat-címke (V029), numerikusan kezelhetetlenül kicsi variancia (V030, pl. vi = 1e-300: hiba, az `analyze` sem fut le), képletnek látszó szöveges cella (V025: `=`, `+`, `-`, `@`, tabulátor vagy CR az elején — táblázatkezelő-képlet vagy CSV-injekció gyanúja); a hibás sor kimarad (több karú vizsgálatnál csak az a sor, nem az azonos címkéjű társa); a teljes lista: `ma.py kb rules --stage S05 --agent engine` |
| Kimenet | `report.md` (magyar összefoglaló + angol Methods-bekezdés, amely minden ténylegesen használt opciót megnevez; értelmezési figyelmeztetések, pl. nyers arány CI-je [0, 1]-en kívül, PFT-becslés a megfigyelt tartományon kívül, az alapkockázattal össze nem egyeztethető RD, SMD-nél csak tájékoztató Egger), `forest.svg`, `funnel.svg`, `doi.svg`, kumulatív elemzésnél `cumulative.svg`, egyetlen folytonos moderátoros meta-regressziónál `bubble.svg` (a pontok területe ∝ súly, konfidencia- és predikciós sáv a koefficiens-kovarianciából — metafor `predict()`-tel egyezően; `ma.py figure --plot <futás> --kind cumulative\|bubble\|loo [--lang en] [--annotate]` újrarajzolja), `plot_data.json`, `results.json`, `effect_sizes.csv`, `run.json` (futás-leíró, szk.ma.run/v1; `--json-summary`: ugyanez a stdout-ra, minden más kiírás a stderr-re); `--no-plots` és újrafuttatás a mappában maradt, most nem készült ábrafájlokat törli. Az `analyze` a figyelmeztetéseket `FIGYELEM:` sorokban ki is írja. A `plot_data.json` alapból **szk.ma.plot/v2**: vizsgálatonkénti `row_uid` (a felület lefúrásához; szűrt futásnál is a teljes táblára vonatkozik), a motor által számolt tengelybeosztás, kontúr-poligonok, kész `display_text` szövegek (`hu`: az ábra és a riport szövege, `en`: U+2212 mínusszal); `--plot-schema v1` a korábbi kulcsokat írja. `--lang hu\|en` (álnév: `--plot-locale`) az SVG-feliratok nyelve, `--svg-annotate` elnevezett rétegeket és soronkénti `data-*` azonosítókat tesz az SVG-kbe; alapértelmezésben az SVG-k változatlanok |
| Bemenet | CSV/TSV; `;` vagy `,` elválasztó, tizedesvessző, UTF-8 / Windows-1250, magyar és metafor-féle oszlopnevek. Külön címkeoszlop nélkül a `Study ID` / `Trial` (study_id) oszlop is címke (közös azonosítónál ` (1)`, ` (2)` utótaggal és V007-figyelmeztetéssel; szűrőben `study=…` néven is hivatkozható); ismétlődő fejlécnél az első oszlop számít (V028); a `rob` oszlop a RoB 2 / ROBINS-I hivatalos alakjait is ismeri (`High risk of bias`, `Serious/Critical risk of bias`, `Some concerns`, `No information`, `Magas torzítási kockázat`; `--subgroup rob`-nál ugyanannak a kategóriának eltérő írásmódjai egy szintbe kerülnek). Az `es` / `analyze` nem írja felül a `--data` fájlt |

### Validálás
- `tests/reference/generate_metafor_reference.R` a metafor 4.4-gyel referencia-értékeket állít elő öt adatsorra
  (BCG, Normand 1999, Molloy 2014, Pritz 1997, Yusuf 1985); a `tests/test_metafor_reference.py` ezekhez mér
  (tolerancia jellemzően 1e-6 relatív).
- `tests/test_source_examples.py`: a feltöltött tankönyvek és cikkek kidolgozott számpéldái (Borenstein 2009, Khan 2020,
  JSLHR-tutorial 2022 …) — csak azok, amelyeket egy független ellenőrző újraszámolva reprodukálni tudott. Jelenleg
  196 aktív eset 3283 ellenőrzéssel (`python3 tests/source_cases.py`); a futtató tud származtatott mennyiséget
  (`expr`, pl. súlyarány), szöveges elvárást (pl. LFK-kategória), MetaXL-féle PFT-visszatranszformálást és a
  forrásszoftver egyszeres pontosságú tárolását (`input_storage`) is. 27 eset marad `known_gap`: 18-nál a forrás
  hibás vagy nem használható orákulum (elírás, kettős kerekítés, nem közölt bemenet), 7 nem implementált módszer (metaSEM
  megfigyelt információs SE / −2LL / Wilson-féle Q-felbontás: 4; dózis–hatás REMR: 3), 2-nél mindkettő.
- `python ma.py selftest` a motor tesztjeit futtatja (a `tests/` felső szintjét); a munkapad (`ma_gui/`) Python-tesztjeit
  nem. Mindkettő: `python3 tests/run_parallel.py --gui`; a böngészős (Playwright) tesztek:
  `node tests/gui/ui/run_all.js --python`; a forráspéldák: `python3 tests/source_cases.py`.

### Ismert korlátok (tervezett bővítések)
Nem támogatott még: függő hatásméretek többszintű modellje (metafor `rma.mv`, klaszter-robusztus varianciabecslés),
hálózati metaanalízis, dózis–hatás metaanalízis (REMR / GLST, restricted cubic spline, `dosresmeta`), diagnosztikus
pontosság bivariáns modellje, bayesi modellek, IPD-metaanalízis. Ezekhez a riport kimondja, hogy validált R-csomag kell
(metafor, meta, netmeta, dosresmeta, mada).

Az értékelő eszközöknél (v1): a RoB 2 / ROBINS / QUADAS-2 / QUIPS implikált ítélete „konzervatív” (amit a válaszok
kikényszerítenek) — a hivatalos folyamatábrát nem helyettesíti; a RoB 2 klaszter-randomizált és keresztezett változata,
valamint a „protokollhoz ragaszkodás” hatásának 2. doménje még nincs meg; a licencadatok emlékezetből, ellenőrzésre
jelölve szerepelnek (kivétel: TRIPOD+AI, CC BY 4.0). A buborék-ábra csak egyetlen folytonos moderátornál készül.

Kisebb, ismert hiányok (a `known_gap` forrás-esetek is ezeket dokumentálják):
- ML/REML modellnél nincs megfigyelt információs (Hessian-alapú) SE, nincs a τ² SE-je / Wald-CI-je és a −2LL (metaSEM),
  és nincs Wilson-féle (SPSS METAREG) Q-felbontás véletlen hatású súlyokkal;
- a robusztus meta-regresszió HC1 (kis mintás CR2 / clubSandwich-korrekció nincs);
- nincs SMCR (nyers kiinduló SD-vel standardizált változás), párosított mérték t- vagy p-értékből, ROM 'HO' variancia,
  metafor 'AV' SMD-variancia;
- k = 2 esetén nincs H/I² CI (a meta Q > k-nál ad); a kiugró-szűrés egylépéses (nem iterált); az erőelemzés csak az
  összesített hatás z-tesztjére vonatkozik (a heterogenitás-tesztre nem).

## Validáló és grafikus felület (MA-munkapad)

A [`TERV_validalo_grafikus_felulet.md`](TERV_validalo_grafikus_felulet.md) a böngészős munkafelület terve; az MVP
(`ma_gui/`) helyi, csak 127.0.0.1-en figyelő stdlib szerver: `python ma.py gui --project <mappa>` (parancssor nélkül:
`ma-munkapad.cmd` Windowson, `ma-munkapad.command` macOS-en). Csak `project init` után az elemzéshez előbb egy
kimenetet kell felvenni: a felület „Kimenet felvétele” gombjával vagy `python ma.py project outcome <mappa> --id o1
--name "…" --data 03_adatok/o1.csv --measure RR`. A számok egyetlen forrása a motor. A `figure-forge` (ábra-export,
audit) és a `validator` (RoB, PROBAST+AI, TRIPOD+AI, GRADE, AMSTAR 2) a v1-ben csatlakozik majd opcionális
adapterként, verziózott `szk.*` JSON-szerződéssel; az MVP csak felderíti őket (Képességek oldal), és nélkülük működik.
A v1 motor-oldala már kész (2026-10-05): a kettős kinyerés egyeztetése, a natív értékelő eszközök (RoB 2, ROBINS-I/E,
QUADAS-2, NOS, QUIPS, JBI, PROBAST+AI, TRIPOD+AI, AMSTAR 2; konszenzus, forgalmi lámpa, `rob`-szinkron), a GRADE / SoF,
a kumulatív és a buborék-ábra, valamint a PRISMA 2020 folyamatábra-specifikáció a `metaelemzes/api.py` homlokzatán és a
CLI-n (`appraisal`, `grade`, `kettos`, `figure`, `prisma check --studies --emit-flowchart`) érhető el; a felület ezeket
név szerint köti, plugin nélkül is.
A projektfájlok összhangját a `python ma.py project audit <mappa> --json` X-szabályai ellenőrzik (a felület és a
`ma-ellenorzo` ugyanazt látja); a FINAL ellenőrzőpont `--audit-gate`-tel az error szintű X-találat mellett sem
zárható. A felületet és a pillanatképét soha ne publikáld Artifactként, és ne töltsd fel. A terv 11. fejezete
rögzíti a felhasználó döntéseit.

## Claude Code-plugin (`.claude-plugin/`, `agents/`, `skills/`)

A repó gyökerében lévő `.claude-plugin/marketplace.json` a marketplace (`anamnezis-asszisztens`), a plugin maga ez a
mappa (`metaanalizis-asszisztens/.claude-plugin/plugin.json`, név: `metaanalizis`, verzió: a `metaelemzes.__version__`).
A plugin `agents/` és `skills/` mappája **generált**: egyetlen forrása a repó `.claude/agents/` és
`.claude/skills/` változata. Szerkesztés után futtasd:

```bash
python metaanalizis-asszisztens/tools/build_plugin.py           # újragenerálás (manifesztekkel együtt)
python metaanalizis-asszisztens/tools/build_plugin.py --check   # 1-es kilépési kód, ha elavult (a tesztek is futtatják)
```

A generátor az utakat a plugin telepítési helyére írja át (`"${CLAUDE_PLUGIN_ROOT}/ma.py"`), az alágens- és
skillneveket névtérbe teszi (`metaanalizis:ma-ellenorzo`, `skills: metaanalizis:metaanalizis`), és kihagyja a
plugin-ágensnél figyelmen kívül hagyott frontmatter-mezőket. Plugin-szintű `settings.json` szándékosan nincs (a plugin
engedélyt nem adhat). Pluginként a tudásbázis-adatbázis a plugin adatmappájában (`${CLAUDE_PLUGIN_DATA}`, ennek
hiányában a plugin-gyorsítótár elrendezéséből: `<plugins>/data/metaanalizis-anamnezis-asszisztens/`) van, mert a
telepítési mappa frissítéskor cserélődik; a `METAELEMZES_KB` környezeti változó mindkét változatban felülírja.
Új kiadás: a `metaelemzes/__init__.py` verziójának növelése, majd újragenerálás — a plugin-felhasználók csak
verzióváltáskor kapják meg a változást.

## Tudásbázis (`tudasbazis/`)

| Tábla | Mi van benne |
|---|---|
| `source` | a források bibliográfiai adatai |
| `stage` | a munkafolyamat szakaszai (S00–S14) |
| `knowledge` | saját szavainkkal összefoglalt tudásegységek (fogalom, útmutatás, küszöb, buktató, konvenció), oldal-/fejezet-hivatkozással |
| `formula` | képletek, a motor megfelelő függvényére hivatkozva |
| `decision_rule` | döntési szabályok (HA … → AKKOR …, erősség: must/should/consider/avoid) + a motor V- (adatvalidálás, S05), P- (PRISMA, S04) és X-szabályai (projekt-audit, szabályonként saját szakasszal) — `ma.py rules export --json` ugyanezeket adja metaadattal |
| `checklist_item` | ellenőrzőlisták (`kb checklist <név>`): PRISMA 2020 (`PRISMA2020`), PRISMA-P (`PRISMA_P`, protokoll), PRISMA-S (`PRISMA_S`, keresés), `PREFLIGHT`, `REVIEWER`, `EVALUATOR`, AMSTAR 2 (`AMSTAR2`), `GRADE` |
| `tool` | adatbázisok, felfedező eszközök, szoftverek — hozzáférési móddal és Claude-integrációval |
| `worked_example` | a források kidolgozott számpéldái, ellenőrzési státusszal |
| `chunk` (+ FTS5) | a forrásdokumentumok **teljes szövege** oldalanként / bekezdésenként — **csak helyben** |

A seed JSON-ok (`tudasbazis/seed/`) verziókövetettek; a `tudasbazis.sqlite` ezekből épül (`ma.py kb build`, vagy
automatikusan az első lekérdezéskor). A könyvek és cikkek teljes szövege szerzői jogvédett, ezért **nem kerül a
repóba**: a saját PDF/DOCX példányaidból helyben töltöd be, bármelyik saját mappádból (a repóban erre a gitignore-olt
`tudasbazis/forrasok/` is megfelel; pluginként ne a plugin mappáját használd, mert az frissítéskor cserélődik):

```bash
python metaanalizis-asszisztens/ma.py kb ingest <a PDF-eket tartalmazó mappa>
python metaanalizis-asszisztens/ma.py kb stats
```

A fájlneveket a `sources.json` `file_hint` mezője alapján ismeri fel (pl. `bookChapterSample`, `978-981-15-5032-4`),
így a teljes szöveg a megfelelő forráshoz kapcsolódik.

Az újratöltés idempotens: a változatlan fájl szövege (és a `<forrás>#<sorszám>` hivatkozás) megmarad; `--source-id`-vel
betöltött többfájlos forrásban csak a módosult fájl szövege cserélődik; azonos nevű, de más mappából származó fájl
saját forrást kap (FIGYELEM-mel). Teljes szöveges találatra a `kb search` [forrás#sorszám] azonosítójával hivatkozz
(`--kb`).

### Bővítés új adatokkal
1. **Új dokumentum teljes szövege:** `kb ingest <fájl> --source-id sajat2026 --citation "…"`.
2. **Új szabály / tudásegység:** új JSON-fájl a `tudasbazis/seed/` mappába (`rules_<téma>.json`, `knowledge_<forrás>.json`)
   a meglévők mezőivel; a következő lekérdezés automatikusan újraépíti az adatbázist.
3. **Új kidolgozott példa → teszt:** `examples_<forrás>.json`; ha `usable_as_test_oracle: true`, a
   `test_source_examples.py` automatikusan ellenőrzi a motorral.

## Projektnapló (`ma.py project …`)

`project init <mappa>` létrehozza a munkamappát (`00_protokoll` … `07_ellenorzes`, sablonokkal) és a `projekt.sqlite`
naplót: döntések (`log`, KB-hivatkozással), ellenőrzési megállapítások (`finding`, `resolve` — blocker csak fixed vagy
indokolt invalid státusszal zárható, `--status open` újranyit), szakaszkapuk
(`checkpoint` — nyitott *blocker* mellett PASS nem adható), GRADE-ítéletek (`grade`), futtatások adat-hash-sel
(`analyze --project`), és `export` → Markdown döntési napló a kiegészítő anyaghoz (`--format json`:
szk.ma.journal-export/v1, `07_ellenorzes/dontesi_naplo.json`).

- `status`, `list`, `show`: JSON-kimenet (`--json` mellett a `status` a figyelmeztetéseket csak a JSON-ban adja, a
  stderr-re nem).
- `--actor` (log, finding, resolve, checkpoint, grade, `analyze --project`; alap: `MA_ACTOR`): ki rögzítette a
  tételt (pl. `user:SzK`, `agent:ma-ellenorzo`); a megállapítás lezárójáé a `resolved_actor`.
- `project audit <mappa> [--json] [--stage S08]`: kereszt-artefaktum X-szabályok, X001–X022 (X001 elavult commit-futás,
  X002 elavult exportált ábra, X003 RoB-szinkron, X004 értékelés nélküli elemzett vizsgálat, X005/X006 hiányzó „becsült /
  magas RoB nélkül” futás, X007 a GRADE számai ≠ a futás, X008 SoF-cella ≠ a motor, X009 feloldatlan kettős kinyerés,
  X010 forrásoldal, X011 PROBAST+AI nélküli vizsgálat predikciós modelles áttekintésben, X012 AMSTAR 2, X013
  becsült-jelölés, X014 k ≤ I, X015 `included_meta` ≠ k, X016 protokoll-eltérés döntés nélkül, X017 indokolatlan
  felülbírálás, X018 ábra-QC, X019 feloldatlan „gyanított” publikációs torzítás, X020 elbírálatlan rekord, X021 kizárási
  okok ≠ döntési napló, X022 elavult eredet-oldalfájl); szk.ma.project-audit/v1, kilépési kód 1, ha van error szintű
  találat. Szakaszkapuk: S08 (és később) PASS / PASS_WITH_FIXES feloldatlan kettős kinyerés (X009) mellett nem adható;
  `checkpoint --stage FINAL --audit-gate`: bármely error szintű X-találat mellett sem.
- GRADE-tár (v1): `ma.py grade save|show|record` (`06_kezirat/grade/<kimenet>.grade.json`, szk.ma.grade/v1); a
  `project grade --publication-bias suspected` (feloldás nélkül) hibát ad (4. döntés): írd így: „0 gyanított (feloldva):
  <indok>” vagy „−1 gyanított: <jelek>”, illetve „−1 erősen gyanított: …”.
- Tevékenységnapló: `MA_ACTIVITY_LOG=1` mellett a projektbe író parancsok (init, log, finding, resolve, checkpoint,
  grade, export, `analyze --project`) a `07_ellenorzes/activity.jsonl` hash-láncába is bekerülnek (szk.ma.activity/v1;
  újrafuttatható, projekt-relatív argv, be- és kimeneti fájl-hash-ek, cellaérték nélkül). `project activity <mappa>
  [--json]` ellenőrzi a láncot (1-es kód, ha sérült).

## Elemzési spec és futás-leíró (`analyze --spec`, `run.json`)

Az elemzési spec (szk.ma.analysis-spec/v1, a projektben `05_elemzes/specs/<név>.json`) egy elemzés teljes,
gépi leírása: adatfájl (projekt-relatív út, commitnál rögzített sha256), szűrők és az opciók **pontosan a
`pipeline.DEFAULTS` kulcsaival** (a `results.json` `options`-éből visszaállítható). A spec és a kapcsolók oda-vissza
leképezését a `metaelemzes/spec.py` az argparse-ból generálja; `analyze --spec F` bájtra ugyanazt adja, mint az
egyenértékű kapcsolók. A spec mellett csak a futás-vezérlő kapcsolók adhatók meg (`--out`, `--project`, `--date`,
`--no-plots`, `--json-summary`, `--run-id`, `--actor`); ha a spec rögzített adat-hash-e eltér a fájlétól, a futás
elavultként hibával áll le. Minden `analyze` `run.json`-t ír: `--project` (vagy `--run-id`) mellett commit-futás
`run_id`-vel (`20261004T211200Z-a1f3c2`: UTC idő + az adat-hash eleje; ha a projektben már foglalt — pl. két commit
ugyanabban a másodpercben —, az időbélyeg másodpercenként előrelép), különben explore (`run_id: null`, `files` nélkül).
A `--spec … --project` futás `--out` nélkül a terv szerinti helyre ír: `05_elemzes/<kimenet>/<run_id>/` (a `--data`
futás alapértelmezése változatlanul `<adatmappa>/eredmeny/`). A `project audit` a commit-futásokat a
`05_elemzes/<kimenet>/<futás>/`, a `05_elemzes/<kimenet>/` mappákban és a projektnapló futásai között keresi; a
kézi `--out` ezért legyen `05_elemzes/<kimenet>/<futás>` (pl. `05_elemzes/o1/primary`, `05_elemzes/o1/magas_rob_nelkul`).

## Homlokzat és adatszerződések (`metaelemzes/api.py`, `metaelemzes/contracts/`)

A `metaelemzes/api.py` a parancssor, az ágensek és a munkapad közös belépési pontja; minden függvénye ugyanazt adja,
mint a megfelelő CLI-parancs JSON-kimenete (a teszt bájtra összeveti):

| Függvény | Megfelelője |
|---|---|
| `engine_info()` | verzió, gyors önteszt (a forrás-esetek, ha a `tests/` megvan), mértékek kötelező oszlopokkal, az argparse-ból generált opció-metaadat, szabálylista |
| `capabilities()`, `rules_export()` | `ma.py --capabilities`, `ma.py rules export --json` |
| `read_table` / `write_table` | formátumtartó nyers tábla (kódolás, tagoló, sorvég, idézés, BOM) |
| `validate_table`, `validate_file`, `validate_request` | szk.ma.validation/v1 nyers cellákból, `validate --json`, `validate --request-json` |
| `analyze(spec, table=None, mode="explore"\|"commit", outdir, project_root)` | nézetmodell: eredmények + szk.ma.plot/v2 + szk.ma.run/v1; explore semmit nem ír, commit a CLI-vel azonos kimeneteket, `run.json`-t és naplósort ír |
| `convert(request)` | szk.ma.convert-request/v1 → szk.ma.convert-result/v1 (`estimated`: a motor döntése; módszer, hivatkozás, KB-azonosítók) |
| `prisma_check(flow, studies=None)`, `project_audit(dir)` | `prisma check [--studies S] --out-format json`, `project audit --json` |
| `prisma_flowchart(flow, studies=None, lang="en", out=None)`, `checkpoint_gate_errors(dir, stage)` | `prisma check --emit-flowchart` fájltartalma (szk.ff.flowchart/v1); a szakaszkapu találatai |
| `instruments_list`, `instrument_get`, `instrument_route`, `appraisal_check`, `appraisal_problems`, `appraisal_list`, `appraisal_save`, `appraisal_approve_ai_draft`, `appraisal_consensus`, `appraisal_build_consensus`, `project_rob_summary`, `project_rob_sync`, `project_rob_sync_apply` | `appraisal instruments\|schema\|route\|check\|validate\|list\|save\|approve\|agreement\|consensus\|rob-summary\|sync-rob [--apply] --json`; továbbá `rob_summary`, `rob_sync_proposal`, `apply_rob_sync`, `tripod_check`, `amstar2_rating`, `appraisal_set_judgement`, `appraisal_agreement_pooled`, `appraisal_completeness`, `appraisal_implied` |
| `grade_advice`, `grade_get`, `grade_put`, `grade_record`, `grade_list`, `sof`, `sof_save`, `sof_load`, `sof_markdown`, `sof_csv`, `sof_html`, `amstar2_consistency` | `grade advice\|show\|save\|record\|sof\|amstar2 --json` |
| `compare`, `reconcile`, `consensus_table`, `agreement_report`, `kettos_status`, `kettos_project_compare`, `kettos_project_reconcile`, `kettos_project_report` | `kettos compare\|reconcile\|report\|status --json` |
| `render_figure(plot, kind, lang, annotate)` | `figure --plot … --kind cumulative\|bubble\|loo --json` |
| `kb_search`, `kb_show`, `kb_rules`, `kb_rules_for_field`, `kb_checklist` | `kb search\|show\|rules\|checklist --json`; a mezőre hivatkozó szabályok (a felület ⓚ-jelvényei) |
| `project_*` | a projektnapló vékony burka (init, log, finding, resolve, checkpoint, grade, status, list, show, export, activity) |

A motor által termelt és fogadott JSON-szerződések (szk.*, JSON Schema 2020-12) a `metaelemzes/contracts/`
mappában vannak (lásd a [README](metaelemzes/contracts/README.md)-jét); `ma.py contracts [list] [--json] | check |
sync`. A `--capabilities` kézfogás minden szerződés fájl-sha256-ját közli (futásidejű sodródás-őr).

## Források
Lásd `tudasbazis/seed/sources.json` és `ma.py kb sql "SELECT source_id, citation FROM source"`.

## Fontos
Döntéstámogató eszköz: a be-/kizárás, az adatkinyerés kettős ellenőrzése, a torzítási kockázat és a GRADE végső
ítélete a szerzőké. A motor eredményei a metaforral egyeznek a tesztelt esetekben, de minden új elemzést az
ellenőrző ágens és a szerzők is ellenőriznek.
