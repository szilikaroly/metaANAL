# JATS-fixture-ök a bevont vizsgálatok kinyeréséhez (Metaheadhunter, TERV 6. és 19.1)

Tesztek: `tests/test_headhunter_extract.py` (a `jats.py` elemző és az `included.py` kinyerő).

## Szintetikus fixture-ök (kézzel írt, kitalált vizsgálatok)

A fájlokban nincs valós cikkszöveg, és egyik vizsgálat sem létezik. Az azonosítók szándékosan ki nem osztott
tartományokból jönnek: PMID/PMCID `9999xxxx`, `NCT9999xxxx`, a DOI-k a Crossref `10.5555` tesztelőtagját
használják. Ezeket tilos a teszteken kívül használni.

| Fájl | Mit tesztel |
|---|---|
| `cochrane_nested_reflist.xml` | Cochrane, beágyazott `<ref-list>`: bevont, kizárt, besorolásra váró, folyamatban lévő, további és korábbi-változat szakaszok; elsődlegesnek jelölt (`*`) és társközlemény; vizsgálatonkénti jellemzők-táblák (a felirat egy xref a csoportra); a kizárás okai; esemény/összes adattábla → másodlagos adat |
| `bmj_table_author_year.xml` | xref nélküli „szerző év" táblázat: első-szerző-csapda (Alfa társszerző egy azonos évű másik cikkben), a/b utótag, kétértelmű első szerző + év (`ref_hint`), elírt név (tipp), xref-es sor, felső indexes szám (`Hotel 2014¹⁰`), lábjegyzet-jel és ezres elválasztó a létszámban |
| `springer_table_multirow.xml` | `<p>`-darabolt első cella, „2.1/2.2" sorok = egy vizsgálat, azonos szerző két évvel, névelő („van Nielsen"), ország az év előtt, `rowspan`-os címke, kétsoros címke („Papa," / „2016"), konzisztens „Out of eight RCTs … [1–8]" mondat |
| `forest_data_table.xml` | a3: események/összesen karonként, átlag/SD/N, „Mean (SD)", „n/N", „g" és külön „95% CI" oszlop, kockázati arány CI-vel; kétértelmű fejlécek (három kar, „Age (mean ± SD)") és bizonytalan cellák (NR, 10⁷) NEM rögzülnek |
| `no_structure.xml` | csak irodalomjegyzék (a bevont-tábla csak kép) → minden hivatkozás `unknown`/`low` jelölt; kétértelmű számos keresési dátum (03/04/2019 → csak az év) |
| `statement_only.xml` | a4: „We included five trials [3–7]" (xref-tartomány) → öt `medium` jelölt; a Bevezetés más áttekintésről szóló mondata és a Megbeszélés nem ad jelöltet; „date of the last search" |

## Valós, vágott fixture-ök (`real/`)

Nyílt hozzáférésű, CC-licencű áttekintések Europe PMC JATS-ából a `record_jats_fixtures.py` fejlesztői segéd
készítette (élő letöltés 2026-10-05-én, a teljes szöveg csak memóriában volt). Megmaradt: bibliográfiai adat
(hivatkozásonként az első 3 szerző), a táblázatok váza (fejléc, első oszlop, rövid számcellák; a többi cella `…`;
vizsgálat-sor nélküli táblából csak a fejléc és az első sor) és CSAK a kinyerés által használt, legfeljebb
300 karakteres mondatok (bevont-szám és keresési dátum). Ez nem a cikk szövege (N4). A licenc, a PMID és a DOI a
fájlok fejkommentjében áll.

A segéd minden fixture-nél ellenőrzi, hogy a vágott dokumentumon a kinyerés eredménye azonos a teljes szövegével;
az eredmény-összefoglaló a `real/expected.json` (regressziós teszt). A kinyerés szándékos javítása után:

```
python3 tests/reference/headhunter/jats/record_jats_fixtures.py          # élő letöltés, újravágás
python3 tests/reference/headhunter/jats/record_jats_fixtures.py --check  # csak összevetés
```

| PMCID | Áttekintés | Mit fed le |
|---|---|---|
| PMC6488980 | Kashangura 2019, Cochrane (PMID 31038197) | pontosan 6 bevont vizsgálat (Andrews 2017, Bunyasi 2017, Ndiaye 2015, Nemes 2018, Scriba 2011, Tameris 2013), kizárt/folyamatban lévő szakaszok |
| PMC4122754 | Roy 2014, BMJ (PMID 25097193) | Adetifa 2010 → ref26 (a ref24-ben csak társszerző) |
| PMC12070792 | Gholami 2025 (PMID 40355968) | Acharjee 2015 → CR24; Anderson 2007 két sora egy jelölt; Azadbakht 2007 és 2008 külön |
| PMC4364968 | Ebert 2015, PLoS One (PMID 25786025) | kétsoros címkék („Fleming," / „2012 [47]") |
| PMC6396088 | Rubinstein 2019, BMJ (PMID 30867144) | a tábla 23-at sorol fel a 47-ből; a konzisztens „47 trials [hivatkozások]" mondat adja a többit (`statement_only`) |
| PMC6405619 | Katsanos 2018, JAHA (PMID 30561254) | rövidítéses vizsgálatnevek több közleménnyel; „mm²" felső index nem hivatkozás; „TSA included 13 RCTs" részszám vs 28 |
| PMC4381278 | Machado 2015, BMJ (PMID 25828856) | karonkénti N és MD (95% CI) másodlagos adatként; a „Mean (SD or SE)" oszlop kétértelmű, nem rögzül |
| PMC4382075 | Cortese 2015, JAACAP (PMID 25721181) | lábjegyzet-betű a címkében („Egeland<sup>i</sup>"); 15 vizsgálat / 16 közlemény |
