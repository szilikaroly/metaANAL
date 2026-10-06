# metaANAL — Metaanalízis-asszisztens

Szisztematikus áttekintés és metaanalízis asszisztens Claude Code-hoz, orvos-kutatóknak (magyar felület, angol
publikációs kimenetek).

- **Ágensek:** orkesztrátor (`metaanalizis-asszisztens`) és alágensek — tervező, ellenőrző, értékelő,
  metaheadhunter (meglévő metaanalízisek bányászata, duplumszűrés, egyesítés, frissítő keresés).
- **Számítási motor:** csak Python standard könyvtár, R metafor-ral validált (`metaanalizis-asszisztens/ma.py`).
- **Tudásbázis:** SQLite + FTS5 a módszertani döntésekhez (szabályok, ellenőrzőlisták, eszközök).
- **Projektnapló** szakaszkapukkal, hash-láncolt tevékenységnaplóval.
- **MA-munkapad:** helyi, böngészős grafikus felület (`python ma.py gui --project <mappa>`): kinyerés élő
  validálással, interaktív ábrák, RoB 2 / ROBINS-I / ROBINS-E / QUADAS-2 / NOS / QUIPS / JBI / PROBAST+AI /
  TRIPOD+AI / AMSTAR 2, GRADE és Summary of Findings, kettős kinyerés, PRISMA 2020, audit-csomag, pillanatkép.

## Telepítés

Lépésről lépésre, kezdőknek: [metaanalizis-asszisztens/TELEPITES.md](metaanalizis-asszisztens/TELEPITES.md).
Részletes leírás: [metaanalizis-asszisztens/README.md](metaanalizis-asszisztens/README.md).

Claude Code-pluginként (ez a repó a marketplace):

```
/plugin marketplace add szilikaroly/metaANAL
/plugin install metaanalizis@metaanal
```

## Adatvédelem és szerzői jog

- Betegszintű adat csak anonimizáltan, a projekt `_privat/` mappájában lehet; ez soha nem kerül a repóba.
- A módszertani könyvek és cikkek teljes szövege nem része a repónak: a saját példányaidból helyben töltöd be
  (`ma.py kb ingest`). A felépített tudásbázis-adatbázis (`*.sqlite`) szintén csak helyben él.

## Licenc

A kód MIT-licenc alatt áll (lásd [LICENSE](LICENSE)). A tudásbázis módszertani tartalma saját szavas
összefoglalás oldal- és fejezethivatkozással; a hivatkozott könyvek, cikkek és értékelőeszközök a szerzőik
jogvédett művei, és nem részei a repónak.

## Eredet

A kód az `szilikaroly/anamnezis-asszisztens` repóban indult; ide a teljes előzményével került át.
