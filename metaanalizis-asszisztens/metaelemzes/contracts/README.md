# A motor adatszerződései (`szk.*`, JSON Schema 2020-12)

Ebben a mappában a metaanalízis-motor által **termelt vagy fogyasztott** szerződések sémái vannak (terv:
`TERV_validalo_grafikus_felulet.md`, 4. fejezet). A munkapad (`ma_gui/`), a pluginok (figure-forge, validator,
composer, presubmit) és az ágensek ugyanezekre a fájlokra hivatkoznak. A számok mindig a motorból jönnek; a
szerződés csak az alakjukat rögzíti.

## Elnevezés

| Mi | Alak | Példa |
|---|---|---|
| fájl | `<név>.v<N>.schema.json` | `ma.plot.v2.schema.json` |
| séma-azonosító (`$id`) | `urn:szk:contract:<név>:<N>` | `urn:szk:contract:ma.plot:2` |
| a dokumentum első mezője | `"schema": "szk.<név>/v<N>"` | `"schema": "szk.ma.plot/v2"` |

Az `$id` azért URN és nem URL, mert a marketplace CI slash-lintje az URL-re hamis találatot adna (4.0). A fájlok
közötti hivatkozás is URN-nel megy: `{"$ref": "urn:szk:contract:common:1#/$defs/sha256"}`.

## Szerződések

Az „irány” a motor szemszögéből értendő (`in`: a motor olvassa, `out`: a motor írja). A *forrás* oszlop azt
mutatja, hogy a séma a terv JSON-blokkjának szó szerinti átirata-e („terv”), vagy a terv példájából és prózájából
készült („próza”). Az utóbbiak `"$comment": "a tervből (próza) — pontosítandó"` jelölést viselnek: az első
termelő megvalósításakor a sémát a tényleges kimenethez kell igazítani (kötelező mezők, típusok).

| Szerződés | Fájl | Terv | Forrás | Irány | Termelő | Fogyasztó |
|---|---|---|---|---|---|---|
| `szk.common/v1` | `common.v1.schema.json` | 4.0 | terv | — | — | minden `szk.*`-séma (`$ref`) |
| `szk.capabilities/v1` | `capabilities.v1.schema.json` | 4.1 | terv | out | motor és pluginok (`--capabilities`) | munkapad (`caps.py`) |
| `szk.ma.validation/v1` | `ma.validation.v1.schema.json` | 4.3 | terv | out | motor (`api.validate_table`, `validate --json`) | munkapad, `ma-ellenorzo` |
| `szk.ma.validate-request/v1` | `ma.validate-request.v1.schema.json` | 4.3 | próza | in | munkapad (élő validálás) | motor (`validate --request-json`) |
| `szk.ma.analysis-spec/v1` | `ma.analysis-spec.v1.schema.json` | 4.4 | terv + generált | in | munkapad (`05_elemzes/specs/`), `spec.spec_from_argv` | motor (`analyze --spec`) |
| `szk.ma.run/v1` | `ma.run.v1.schema.json` | 4.5 | próza | out | motor (`run.json`, `analyze --json-summary`) | munkapad, `project audit` |
| `szk.ma.plot/v2` | `ma.plot.v2.schema.json` | 4.6 | terv | out | motor (`plot_data.json`) | munkapad, figure-forge (`meta`), motor-SVG |
| `szk.ma.convert-request/v1` | `ma.convert-request.v1.schema.json` | 4.7 | próza | in | munkapad (átváltó) | motor (`api.convert`) |
| `szk.ma.convert-result/v1` | `ma.convert-result.v1.schema.json` | 4.7 | próza | out | motor (`api.convert`) | munkapad, `provenance.conversion` |
| `szk.ma.provenance/v1` | `ma.provenance.v1.schema.json` | 4.8 | terv | in | munkapad (`03_adatok/<kimenet>.prov.json`) | motor (`project audit`: X010, X013, X022) |
| `szk.ma.compare-result/v1` | `ma.compare-result.v1.schema.json` | 4.9 | próza | out | motor (`api.compare`, E6) | munkapad (egyeztetés) |
| `szk.ma.consensus/v1` | `ma.consensus.v1.schema.json` | 4.9 | próza | in | munkapad (`kettos/<kimenet>.consensus.json`) | motor (konszenzus-CSV, E6) |
| `szk.ma.studies/v1` | `ma.studies.v1.schema.json` | 4.10 | próza | in | munkapad (`03_adatok/studies.json`) | motor (`prisma check --studies`, X014), composer |
| `szk.ma.project-audit/v1` | `ma.project-audit.v1.schema.json` | 4.15 | próza | out | motor (`project audit --json`, E8) | munkapad, `ma-ellenorzo`, FINAL audit-kapu |
| `szk.ma.activity/v1` | `ma.activity.v1.schema.json` | 4.16 | próza | in, out | munkapad és motor (`MA_ACTIVITY_LOG=1`) | munkapad, audit-csomag (`verify`) |
| `szk.ma.project/v1` | `ma.project.v1.schema.json` | 4.17 | próza | in, out | munkapad (`projekt.save_project_meta`) | motor (`projekt`, audit), munkapad |
| `szk.instrument/v1` | `instrument.v1.schema.json` | 4.11 | próza | out | motor (`metaelemzes/instruments/*.json`, `appraisal.instrument_get`); validator `--schema` | munkapad (értékelő űrlapok), validator-adapter |
| `szk.appraisal/v1` | `appraisal.v1.schema.json` | 4.11 | próza | in, out | munkapad és motor (`04_torzitas_kockazat/appraisals/*.json`, `appraisal.save`) | motor (`appraisal.check`, X003/X017), validator ≥ 1.1 |
| `szk.appraisal-result/v1` | `appraisal-result.v1.schema.json` | 4.11 | próza | out | motor (`appraisal.check`); validator `--verify`/`--rollup --json` | munkapad (teljesség, implikált ítélet, X017) |
| `szk.rob-summary/v1` | `rob-summary.v1.schema.json` | 3.5.10 | próza | out | motor (`appraisal.rob_summary`) | munkapad (forgalmi lámpa), figure-forge (`rob`) |
| `szk.ma.appraisal-agreement/v1` | `ma.appraisal-agreement.v1.schema.json` | 5.4 | próza | out | motor (`appraisal.agreement`) | munkapad (konszenzus-nézet) |
| `szk.ma.rob-sync-proposal/v1` | `ma.rob-sync-proposal.v1.schema.json` | 6.5 | próza | out | motor (`appraisal.rob_sync_proposal`) | munkapad (rob-oszlop szinkron), CLI `appraisal sync-rob` |
| `szk.ma.grade/v1` | `ma.grade.v1.schema.json` | 4.14 | próza | in, out | motor (`grade_help.advice` piszkozat; `projekt.save_grade_doc` / `record_grade_doc`; `06_kezirat/grade/`) | munkapad (GRADE-lap), projektnapló (`add_grade`), `project audit` (X007, X019) |
| `szk.ma.sof/v1` | `ma.sof.v1.schema.json` | 4.14 | próza | out | motor (`grade_help.sof`; `06_kezirat/sof/`) | munkapad (SoF-tábla, export), `project audit` (X008) |
| `szk.ff.flowchart/v1` | `ff.flowchart.v1.schema.json` | 4.12 | próza | out | motor (`prisma.flowchart`, `prisma check --emit-flowchart`) | figure-forge (`ff.py flowchart --spec`), munkapad (PRISMA) |

A `szk.ma.project-audit/v1` megállapításai a v1-től opcionális `studies` mezőt is vihetnek (X004, X011, X017:
az érintett vizsgálatok azonosítói; additív).

Ugyanez géppel olvashatóan: `python3 -m metaelemzes.contracts --json` (a `metaelemzes.contracts.CONTRACTS`
táblából, a fájlok sha256-jával).

**Még nincs séma** (más fázis vagy más tulajdonos):
`szk.ma.audit-bundle/v1` és `szk.ma.documents/v1` (4.17, 4.8 — a munkapad írja), `szk.facts/v1` (4.18, v2),
`szk.ma.journal-export/v1` (`project export --format json`, E7). A pluginok szerződései (`szk.figure-request/v1`,
`szk.figure-result/v1`, `szk.prisma-flow/v1`) a saját `contracts/` mappájukban élnek. Az értékelő szerződések
(`szk.instrument/v1`, `szk.appraisal/v1`, `szk.appraisal-result/v1`) a v1-től a motorban is élnek, mert a
motornak natív eszköz-definíciói vannak (`metaelemzes/instruments/`, „forrás: szk-plugins validator 1.0.0”);
a validator ≥ 1.1 ugyanezeket a fájlokat bájtra azonos másolatként viheti (4.20).

### Generált rész

Az `ma.analysis-spec.v1` `options` objektumának `required` és `properties` része **generált**: pontosan a
`pipeline.DEFAULTS` kulcsai, a típusok és választható értékek a `cli.build_parser()` introspekciójából
(`spec.analysis_spec_schema()`). Ha a motor opciói változnak:

```
python3 -m metaelemzes.contracts check   # eltérésnél kilépési kód 1
python3 -m metaelemzes.contracts sync    # a generált rész újraírása; a séma többi része változatlan
```

## Kompatibilitási szabályok (4.0, 4.19)

- **Számok:** IEEE-754 double; hiányzó vagy nem értelmezhető érték `null`, **soha** nem `NaN`, `Infinity` vagy
  szöveg.
- **Skálák:** az `y`, `lo`, `hi`, `estimate` és `ci_*` mezők az *elemzési* skálán vannak (pl. log RR); a
  `display` és a `display_text` a *megjelenítési* skálán (pl. RR). A transzformációt a motor végzi.
- **Útvonalak:** a projektgyökérhez képest relatívak, `/` elválasztóval (`common#/$defs/relpath`). Abszolút út
  csak a képesség-leírásban szerepel.
- **Nyelvfüggő szöveg:** `{"hu": "…", "en": "…"}` (`common#/$defs/i18n`). A motor mindkettőt előállítja; a felület
  nem kerekít újra (a JS `toFixed` és a Python `%` a döntetlennél eltérően kerekít).
- **Számok a szövegben:** a magyar szöveg is tizedespontot használ (`0.49 [0.33; 0.73]`), mint a `report.md` és az
  SVG; a két nyelv csak a mínuszjelben tér el (hu: `-`, en: U+2212). A táblacellába írandó számot az átváltó
  `cell_text`-je adja a cél tábla tizedesjelével. Az átváltás feltevései és figyelmeztetései (`assumptions`,
  `warnings`) is `{hu, en}` objektumok.
- **A plot/v2 kiegészítései (additív, a v2-n belül):** a felület által olvasott kész szövegek és tengelyek —
  `studies[].weight_text`, `summaries[].p_text/pi_label/primary`, `heterogeneity.*_text`, a funnel/Doi/LOO/kumulatív/
  befolyás tengelyei (`$defs/axis`), `influence[].*_text`, `influence_axes/_text/_note`, `axis.ticks[].text_i18n`.
- **Bővítés:** a fogyasztó az ismeretlen mezőt figyelmen kívül hagyja, ezért a sémák a gyökérben nem tiltják a
  további mezőket (kivétel: az elemzési spec `options`-e, amely pontosan a `DEFAULTS` kulcsait fogadja el).
  Kötelező mező törlése vagy jelentésváltozása **új főverzió** (új fájl: `<név>.v<N+1>.schema.json`, új `$id`).
- **Verziók:** a fogyasztó legalább az aktuális és az előző főverziót olvassa, de csak az aktuálisat termeli.
  Példa: a motor alapból `szk.ma.plot/v2`-t ír, a `--plot-schema v1` a régi kulcsokat; a felület mindkettőt olvassa.
- **Verzió-egyeztetés (4.19):** amíg egy termelő PR-je nincs kész, a fogyasztó az átmeneti utat használja
  (pl. munkapad ← motor validálás: `api.validate_table`; munkapad ← composer: `flow-json` séma nélkül; munkapad ←
  validator: legacy bridge + H1–H4 őrök). A PR után a fenti szerződés az egyetlen út.

## Sodródás-őrök (4.20)

1. **Futásidőben:** a `szk.capabilities/v1` kézfogás szerződésenként közli a sémafájl **bájt-hash**-ét
   (`metaelemzes.contracts.sha256(név, verzió)`); eltérésnél a munkapad az adott funkciót legacy módba teszi és
   „szerződés-eltérés”-t ír ki.
2. **Bájt-azonos másolatok:** ugyanannak a sémának minden másolata (pluginok `contracts/` mappája) bájtra egyezik.
   Ezért a fájlok formázása kanonikus — `json.dumps(séma, indent=2, ensure_ascii=False) + "\n"`, UTF-8, BOM és CR
   nélkül (`metaelemzes.contracts.canonical_text`). Kézi szerkesztés után is ebben a formában mentsd.
3. **A marketplace CI slash-lintje** a `*.json` fájlokban a `/<szó>:<szó>` alakot kifogásolja; a sémák szövege
   ezt kerüli.
4. **Termelői oldalon** minden termelő a saját kimenetét a saját másolatával validálja.

A `tests/test_mvp_contracts.py` őrzi: a fájlnév, az `$id` és a `schema`-konstans egyezését; a kanonikus formát;
minden `$ref` feloldhatóságát; hogy csak a `ma_gui/schema_lite.py` által is ismert kulcsszavak szerepelnek; hogy a
„terv” forrású sémák szó szerint egyeznek a terv JSON-blokkjaival; a terv példadokumentumait
(`tests/reference/contract_examples/*.terv.json`; a „…” hash-helykitöltők nullákkal 64 jegyűre egészítve);
szerződésenként a pozitív és negatív példákat; és hogy a motor meglévő termelőinek kimenete megfelel a sémáknak.

## Használat Pythonból

```python
from metaelemzes import contracts
schema = contracts.load("ma.plot", 2)        # vagy "szk.ma.plot/v2", "urn:szk:contract:ma.plot:2"
digest = contracts.sha256("ma.plot", 2)      # a capabilities contracts[].sha256 értéke
reg = contracts.registry()                   # {$id: séma} a fájlok közti $ref-ekhez
```
