# Metaanalízis-asszisztens

Szisztematikus áttekintés és metaanalízis asszisztens Claude Code-hoz: egy **orkesztrátor** és három
**alágens** (tervező, ellenőrző, értékelő), egy **metafor-ral validált számítási motor** (csak Python
standard könyvtár, telepítés nélkül fut), egy **SQL-tudásbázis** a módszertani döntésekhez, és egy
**projektnapló**, amely minden döntést és ellenőrzési megállapítást visszakereshetően rögzít.

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

**Csak a motor** (parancssor, bármely mappából):

```bash
python metaanalizis-asszisztens/ma.py analyze --data adat.csv --measure SMD --out eredmeny/
python metaanalizis-asszisztens/ma.py validate --data adat.csv --measure OR
python metaanalizis-asszisztens/ma.py kb search "few studies random effects"
python metaanalizis-asszisztens/ma.py selftest
```

Windows-on `python` (a `.claude/.venv` is jó), Linuxon/macOS-en `python3`. Külső csomag nem kell; a PDF-ek
tudásbázisba töltéséhez opcionálisan `pip install pypdf`.

## A motor (`metaelemzes/`)

| Terület | Tartalom |
|---|---|
| Hatásméretek | MD, SMD (Hedges g; LS / LS2 = Borenstein / UB variancia), Cohen d, ROM, OR, RR, RD, arányok (PR, PLN, PLO, PAS, PFT), korreláció (COR, ZCOR), generikus (yi+vi/sei) |
| Modellek | közös (fix) hatás, véletlen hatás (DL, REML, ML, PM, HE, SJ), IVhet (Doi 2015), Mantel–Haenszel (OR, RR, RD — Sato-variancia), Peto |
| CI / PI | z, t, HKSJ, HKSJ ad hoc; predikciós intervallum t(k−2) / t(k−1) / z |
| Heterogenitás | Q, I², H², τ², τ; CI: Q-profile (metafor), Higgins–Thompson, Borenstein-féle τ²-CI |
| Moderátorok | alcsoport (külön vagy közös τ²; Q_between), vegyes hatású meta-regresszió (REML/ML/DL/FE, Wald vagy Knapp–Hartung; QM, QE, R²; kategóriás moderátor dummy-kódolással) |
| Torzítás | klasszikus Egger, Begg–Mazumdar (pontos/normális), trim-and-fill (L0/R0, metafor-algoritmus), kontúr-javított funnel, Rosenthal fail-safe N (csak tájékoztató) |
| Érzékenység | leave-one-out, befolyás-diagnosztika (rstudent, DFFITS, Cook, cov.ratio, hat, DFBETAS — metafor-kritériumok), kumulatív elemzés |
| Konverziók | medián/IQR/tartomány → átlag/SD (Luo 2018, Wan 2014, Hozo 2005), SE/CI/t/p → SD/SE, csoportok összevonása, változás-SD, közös kontroll felosztása, d↔lnOR↔r |
| Adatvalidálás | 20 szabály (V001–V020), pl. SD helyett SE gyanúja, mértékegység-eltérés, kettős nulla, közös kontroll, ferde eloszlás, kevés vizsgálat |
| Kimenet | `report.md` (magyar összefoglaló + angol Methods-bekezdés), `forest.svg`, `funnel.svg`, `plot_data.json` (külső ábrakészítőhöz), `results.json`, `effect_sizes.csv` |
| Bemenet | CSV/TSV; `;` vagy `,` elválasztó, tizedesvessző, UTF-8 / Windows-1250, magyar és metafor-féle oszlopnevek |

### Validálás
- `tests/reference/generate_metafor_reference.R` a metafor 4.4-gyel referencia-értékeket állít elő öt adatsorra
  (BCG, Normand 1999, Molloy 2014, Pritz 1997, Yusuf 1985); a `tests/test_metafor_reference.py` ezekhez mér
  (tolerancia jellemzően 1e-6 relatív).
- `tests/test_source_examples.py`: a feltöltött tankönyvek és cikkek kidolgozott számpéldái (Borenstein 2009, Khan 2020,
  JSLHR-tutorial 2022 …) — csak azok, amelyeket egy független ellenőrző újraszámolva reprodukálni tudott.
- `python ma.py selftest` lefuttat mindent.

### Ismert korlátok (tervezett bővítések)
Nem támogatott még: függő hatásméretek többszintű modellje (metafor `rma.mv`, robusztus varianciabecslés),
hálózati metaanalízis, dózis–hatás metaanalízis, diagnosztikus pontosság bivariáns modellje, bayesi modellek,
IPD-metaanalízis, Doi-plot/LFK-index. Ezekhez a riport kimondja, hogy validált R-csomag kell (metafor, meta, netmeta, dosresmeta, mada).

## Tudásbázis (`tudasbazis/`)

| Tábla | Mi van benne |
|---|---|
| `source` | a források bibliográfiai adatai |
| `stage` | a munkafolyamat szakaszai (S00–S14) |
| `knowledge` | saját szavainkkal összefoglalt tudásegységek (fogalom, útmutatás, küszöb, buktató, konvenció), oldal-/fejezet-hivatkozással |
| `formula` | képletek, a motor megfelelő függvényére hivatkozva |
| `decision_rule` | döntési szabályok (HA … → AKKOR …, erősség: must/should/consider/avoid) + a motor V-szabályai |
| `checklist_item` | PRISMA 2020, PREFLIGHT, REVIEWER, EVALUATOR, AMSTAR 2, GRADE |
| `tool` | adatbázisok, felfedező eszközök, szoftverek — hozzáférési móddal és Claude-integrációval |
| `worked_example` | a források kidolgozott számpéldái, ellenőrzési státusszal |
| `chunk` (+ FTS5) | a forrásdokumentumok **teljes szövege** oldalanként / bekezdésenként — **csak helyben** |

A seed JSON-ok (`tudasbazis/seed/`) verziókövetettek; a `tudasbazis.sqlite` ezekből épül (`ma.py kb build`, vagy
automatikusan az első lekérdezéskor). A könyvek és cikkek teljes szövege szerzői jogvédett, ezért **nem kerül a
repóba**: a saját PDF/DOCX példányaidból helyben töltöd be:

```bash
# tedd a fájlokat a tudasbazis/forrasok/ mappába (gitignore-olt), majd:
python metaanalizis-asszisztens/ma.py kb ingest metaanalizis-asszisztens/tudasbazis/forrasok
python metaanalizis-asszisztens/ma.py kb stats
```

A fájlneveket a `sources.json` `file_hint` mezője alapján ismeri fel (pl. `bookChapterSample`, `978-981-15-5032-4`),
így a teljes szöveg a megfelelő forráshoz kapcsolódik.

### Bővítés új adatokkal
1. **Új dokumentum teljes szövege:** `kb ingest <fájl> --source-id sajat2026 --citation "…"`.
2. **Új szabály / tudásegység:** új JSON-fájl a `tudasbazis/seed/` mappába (`rules_<téma>.json`, `knowledge_<forrás>.json`)
   a meglévők mezőivel; a következő lekérdezés automatikusan újraépíti az adatbázist.
3. **Új kidolgozott példa → teszt:** `examples_<forrás>.json`; ha `usable_as_test_oracle: true`, a
   `test_source_examples.py` automatikusan ellenőrzi a motorral.

## Projektnapló (`ma.py project …`)

`project init <mappa>` létrehozza a munkamappát (`00_protokoll` … `07_ellenorzes`, sablonokkal) és a `projekt.sqlite`
naplót: döntések (`log`, KB-hivatkozással), ellenőrzési megállapítások (`finding`, `resolve`), szakaszkapuk
(`checkpoint` — nyitott *blocker* mellett PASS nem adható), GRADE-ítéletek (`grade`), futtatások adat-hash-sel
(`analyze --project`), és `export` → Markdown döntési napló a kiegészítő anyaghoz.

## Források
Lásd `tudasbazis/seed/sources.json` és `ma.py kb sql "SELECT source_id, citation FROM source"`.

## Fontos
Döntéstámogató eszköz: a be-/kizárás, az adatkinyerés kettős ellenőrzése, a torzítási kockázat és a GRADE végső
ítélete a szerzőké. A motor eredményei a metaforral egyeznek a tesztelt esetekben, de minden új elemzést az
ellenőrző ágens és a szerzők is ellenőriznek.
