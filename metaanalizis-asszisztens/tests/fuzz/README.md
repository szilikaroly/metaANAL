# Differenciális fuzz-tesztelés: motor vs. R/metafor

A `tests/fuzz/` mappa egy újrafuttatható, CPU-párhuzamos összevető környezet: véletlen (de
seedelt, reprodukálható) adatsorokon ugyanazokat a számításokat lefuttatja a Python-motorral
(`metaelemzes`) és az R `metafor` csomaggal, majd mezőnként összeveti az eredményeket.

A fájlnevek szándékosan nem `test_`-tel kezdődnek, és nincs `__init__.py`: a
`python3 -m unittest discover -s tests` nem futtatja ezeket (a fuzz percekig tart, és R kell hozzá).

## Fájlok

| fájl | szerep |
|---|---|
| `gen.py` | seedelt adatsor-generátor (k = 2..40; vi 1e-4..10, extrém arányok; azonos yi; 2×2 táblák nulla és kettős nulla cellákkal; arányok 0/n és n/n; ±1 közeli korrelációk; folytonos átlag/SD/n; párosított adatok; 1–3 moderátor; alcsoportok; évszám a kumulatív elemzéshez) |
| `run_metafor.R` | az orákulum: escalc, rma (FE/DL/REML/ML/PM/HE/SJ × z/t/knha/adhoc), predict (PI: z, t(k−1), Riley t(k−2)), confint (τ², I², H²), regtest (lm), ranktest, trimfill (L0, R0), leave1out, influence, rma.mh (OR/RR/RD), rma.peto, moderátoros rma, alcsoportok, cumul; minden hívás `tryCatch`-ben, a hibák és figyelmeztetések rögzítve |
| `run_fuzz.py` | vezérlő: generálás → darabolás → párhuzamos munkafolyamatok (R + motor + összevetés) → osztályozás → `summary.json` + `summary.md` |
| `exact.py` | pontos (racionális, `fractions.Fraction`) referenciaértékek a rosszul kondicionált eltérések eldöntéséhez (súlyozott LS, Q_E, tr(P), DL τ² moderátorokkal) |

## Előfeltételek

- Python 3 (csak standard könyvtár)
- R ≥ 4.x, `metafor` (4.4-gyel ellenőrizve) és `jsonlite` csomag, `Rscript` a PATH-on

Ha az `Rscript`, a `metafor` vagy a `jsonlite` hiányzik, a `run_fuzz.py` egy `SKIP: ...` üzenettel,
0-s kilépési kóddal kilép.

## Futtatás

```bash
# a metaanalizis-asszisztens mappából
python3 tests/fuzz/run_fuzz.py --n 2000 --jobs 4 --seed 1 --out /tmp/fuzz_out
python3 tests/fuzz/run_fuzz.py --n 300 --only rma,confint          # csak egyes csoportok
python3 tests/fuzz/run_fuzz.py --n 500 --types bin,prop             # csak bizonyos adattípusok
```

Csoportok (`--only`): `escalc rma confint regtest ranktest trimfill leave1out influence mods
subgroup cumul mh peto harbord peters rma_es`.

Az `--out` alapértelmezése egy új ideiglenes mappa a repón kívül. Kimenet:

- `summary.md` – olvasható összefoglaló: ellenőrzések csoportonként, a meg nem magyarázott (FAIL)
  és az ismert (KNOWN) eltérés-osztályok táblázata példákkal, a tolerancia- és az ismert-eltérés-tábla;
- `summary.json` – ugyanez gépi formában (osztályonként legfeljebb 200 adatsor-azonosítóval);
- `records.json` – minden egyes eltérés (csak `--verbose`, `--ids` vagy kis `--datasets` futásnál).

Kilépési kód: 0 = nincs meg nem magyarázott eltérés (vagy SKIP), 1 = van FAIL osztály,
2 = a környezet hibája (R-összeomlás, a motor nem importálható).

## Eltérések kivizsgálása

Minden adatsornak saját azonosítója van (`<seed>-<index>`, pl. `20261004-00042`), és az azonosítóból
pontosan újragenerálható:

```bash
# egy-két adatsor újrafuttatása, részletes listával
python3 tests/fuzz/run_fuzz.py --ids 20261004-00042 --only trimfill

# kézzel írt vagy minimalizált adatsorok (JSON lista; mezők: id, type, yi, vi, ... vagy rows)
python3 tests/fuzz/run_fuzz.py --datasets sajat.json

# az első 5 FAIL osztály egy-egy példájának automatikus minimalizálása
# (vizsgálatok elhagyása, amíg ugyanaz a függvény/mező eltérése megmarad)
python3 tests/fuzz/run_fuzz.py --n 1000 --minimize 5
```

`python3 tests/fuzz/gen.py --n 5 --seed 1` a generált adatsorokat JSON-ként kiírja.

## Osztályozás, tolerancia, ismert eltérések

- Egy eltérés-osztály kulcsa: (függvény, mező, feltétel). A feltétel címkék listája, pl. `k=2`,
  `identical_yi`, `equal_vi`, `vratio>=1e7`, `zero_cell`, `double_zero`, `engine_NA` / `metafor_NA`
  (az egyik oldal NA), `eng_global_search` (a motor profil-likelihood rácskeresése a Fisher-scoringnál
  nagyobb likelihoodot talált), `eng_ll_higher` / `mf_ll_higher` (melyik program τ²-je ad nagyobb
  (RE)ML likelihoodot), `mf_loose_thr` (a metafor csak lazább küszöbbel konvergált).
- A hibák is osztályok: `!engine_error` (a motor kivételt dob, a metafor számol) és `!metafor_error`
  (fordítva). Ha a metafor NA-t ad a fő becslésre, a motor kivétele egyezésnek számít.
- Tolerancia: alapértelmezésben 1e-6 relatív; az abszolút alsó korlátokat (pl. τ²-nél
  1e-8·medián(vi)) és a lazább eseteket (p-értékek 1e-5, befolyás-diagnosztikák 1e-5, REML/ML/PM
  illesztések mezői 1e-5) a `TOLERANCES` tábla indokolja a `run_fuzz.py`-ban.
- Döntőbírák: (RE)ML-eltérésnél mindkét τ²-nél kiszámoljuk a (korlátozott) log-likelihoodot
  (`eng_ll_higher` / `mf_ll_higher` / `ll_equal` címke); moderátoros zárt képleteknél az `exact.py`
  pontos értéke dönt (`exact=engine` / `exact=metafor` / `exact=neither:<közelebbi>`).
- A dokumentált konvenció-eltéréseket (pl. a motor Higgins–Thompson-féle I²-t jelent a leave-one-out
  és a kumulatív elemzésben; RD-nél nincs folytonossági korrekció; t(k−2) PI k = 2-nél nincs) a
  `KNOWN_DIFFERENCES` tábla sorolja fel forráshivatkozással; ezek nem számítanak hibának, de a
  `summary.md` külön táblában mutatja őket. Új ismert eltérést csak forrással (a motor kódjának
  megjegyzése vagy szakirodalom) vegyünk fel. Fajtájuk: `convention` (a motor dokumentált
  konvenciója), `oracle` (a metafor a pontatlan / nem konvergáló fél), `noise` (mindkét eredmény
  kerekítési zajtól függ).
- A `SUSPECTED_DEFECTS` tábla a kivizsgált, a motor hibájának tulajdonított osztályokat csoportosítja
  (`D1_...`, `D2_...`); ezek **továbbra is FAIL-nak számítanak**, amíg a motort nem javítják — a
  `summary.md` elején osztály- és adatsorszámmal szerepelnek. A még nem vizsgált osztály `untriaged`.

## Az orákulum beállításai

A metafor iterációs küszöbei abszolútak, ezért a `run_metafor.R` a medián mintavételi varianciával
skálázza őket (`threshold = 1e-12·s`, nem-konvergencia esetén lazább küszöb, majd lépésfelezés;
`uniroot`-tolerancia `1e-12·s`, `tau2.max = 1e8`). A `leave1out()`, `influence()`, `cumul()` és
`trimfill()` belső újraillesztései a modell `control` listáját használják, ezért ha ezek nem
konvergálnak (NA sorok vagy hiba), a script lépésfelezéses illesztéssel megismétli őket. A `trimfill()` a kitöltött adatokat a metafor
4.4-ben a modell `control` listája nélkül illeszti újra (alapértelmezett küszöb 1e-5), ezért ott a
script ugyanazt a kitöltött adatsort szoros küszöbbel újraillesztve adja vissza.

## Kapcsolódó: párhuzamos unittest-futtató

`python3 tests/run_parallel.py [--jobs N] [--gui]` – a `tests/` modulokat külön folyamatokban,
párhuzamosan futtatja; az eredménye (siker/hiba) ugyanaz, mint a
`python3 -m unittest discover -s tests` futásé (a `--gui` a `tests/gui/` modulokat is hozzáveszi).
