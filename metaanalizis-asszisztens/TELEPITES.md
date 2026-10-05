# Telepítés és első lépések — Metaanalízis-asszisztens

Ez az útmutató annak a kollégának szól, aki most kapja meg az eszközt. Programozói tudás nem kell: a szürke
dobozokban lévő parancsokat másold be a terminálba (Windows: *PowerShell* vagy *Parancssor*; macOS: *Terminál*),
és nyomj Entert. A `<…>` jelek közötti részt a saját adatodra cseréld (a jelek nélkül).

Mit kapsz: egy Claude Code-ban futó **orkesztrátort** és négy **alágenst** (tervező, ellenőrző, értékelő, valamint
a **Metaheadhunter** — meglévő metaanalízisek bányászata), egy
metafor-ral validált **számítási motort** (`ma.py`), egy **tudásbázist** a módszertani döntésekhez, egy
**projektnaplót** szakaszkapukkal, és egy helyi, böngészős **munkapadot** (grafikus felület). Részletes leírás:
[README.md](README.md).

## Röviden — ellenőrzőlista

1. [ ] Python 3.9 vagy újabb ([1. pont](#1-előfeltételek))
2. [ ] Claude Code (és Claude-előfizetés)
3. [ ] A kód letöltése: `git clone` a meghívó elfogadása után, vagy ZIP ([2. pont](#2-a-kód-beszerzése))
4. [ ] Első futtatás: `python ma.py selftest`, `kb build`, `kb stats` ([3. pont](#3-első-futtatás))
5. [ ] (Ajánlott) telepítés Claude Code-pluginként ([4. pont](#4-claude-code-pluginként-ajánlott))
6. [ ] Engedélylista a saját beállításaidba ([5. pont](#5-javasolt-engedélylista))
7. [ ] Indítás ([6. pont](#6-indítás))
8. [ ] (Opcionális, a Metaheadhunterhez) API-kulcsok környezeti változóban, majd `python ma.py headhunter sources
   --check` ([7a. pont](#7a-metaheadhunter-források-és-api-kulcsok-opcionális))

## 1. Előfeltételek

| Mi | Kötelező? | Hogyan ellenőrzöd |
|---|---|---|
| **Python 3.9** vagy újabb | **igen** | `python --version` (macOS/Linux: `python3 --version`; Windows: `py -3 --version`) |
| **Claude Code** (és Claude Pro / Max / Team előfizetés vagy API-hozzáférés) | **igen** | `claude --version` |
| **Git** (Windows-on a *Git for Windows*; a Claude Code is ennek Bash-ét használja) | ajánlott (ZIP-pel kiváltható) | `git --version` |
| R + `metafor` csomag | nem — független keresztellenőrzéshez és a motor által nem tudott modellekhez | R-ben: `library(metafor)` |
| `pypdf` Python-csomag | nem — csak a saját PDF-jeid tudásbázisba töltéséhez | `python -m pip show pypdf` |

- **Python:** a [python.org](https://www.python.org/downloads/) telepítőjével. Windows-on a telepítő első
  képernyőjén pipáld be az **„Add python.exe to PATH”** négyzetet. Külső Python-csomag a motorhoz nem kell.
- **Claude Code:** a hivatalos telepítési útmutató szerint (code.claude.com → Docs → Quickstart). Az első
  `claude` indításkor be kell jelentkezned a Claude-fiókoddal.
- **R + metafor** (opcionális): telepítsd az R-t (cran.r-project.org), majd R-ben: `install.packages("metafor")`.
- **pypdf** (opcionális): `python -m pip install pypdf` (Windows: `py -3 -m pip install pypdf`).

## 2. A kód beszerzése

A repó **privát**. Először a tulajdonos (Szili Károly) meghív a GitHubon: a meghívót e-mailben vagy a
github.com → *Notifications* oldalon fogadd el (ehhez GitHub-fiók kell). Ezután két út közül választhatsz.

**A) git-tel (ajánlott — a frissítés egyetlen parancs):**

```bash
git clone https://github.com/szilikaroly/metaANAL.git
cd metaANAL
```

Ha a git jelszót kér: a GitHub a jelszót itt nem fogadja el. A legegyszerűbb a GitHub CLI (`gh`, cli.github.com):
`gh auth login`, majd `gh auth setup-git` — ezután a `git clone` és a plugin-telepítés is magától azonosít.
Frissítés később: a repó mappájában `git pull`.

**B) ZIP-ként:** a repó GitHub-oldalán *Code* → *Download ZIP*, majd csomagold ki (pl. a `Dokumentumok` mappába).
Frissítéshez újra le kell töltened.

A metaanalízis-eszköz a repó `metaanalizis-asszisztens/` mappájában van.
A kódot bárhová teheted; a **projektjeid** helyére a [9. pont](#9-adatvédelem) vonatkozik.

## 3. Első futtatás

```bash
cd metaANAL/metaanalizis-asszisztens
python ma.py selftest
python ma.py kb build
python ma.py kb stats
```

- `python ma.py selftest` lefuttatja a motor tesztjeit (kb. 1 perc). A végén `OK` (esetleg `OK (skipped=…)`)
  kell álljon. **Ha `FAILED`-et ír, ne elemezz vele**: küldd el a kimenetet a tulajdonosnak. A munkapad
  ([7. pont](#7-a-munkapad-grafikus-felület)) tesztjeit ez **nem** futtatja; azokat (a motoréval együtt) a
  `python tests/run_parallel.py --gui` ellenőrzi (néhány perc, a végén szintén `OK`).
- `python ma.py kb build` felépíti a tudásbázist (SQLite-fájl) a repóban lévő szabályokból és tudásegységekből.
  (Az első kereséskor magától is felépül.)
- `python ma.py kb stats` megmutatja, hány szabály, tudásegység és ellenőrzőlista-tétel van benne. A
  `fulltext_by_source` rész üres, amíg saját PDF-et nem töltesz be ([8. pont](#8-saját-pdf-ek-a-tudásbázisban)).

macOS-en és Linuxon `python` helyett `python3`, Windows-on szükség esetén `py -3` (lásd [10. pont](#10-hibaelhárítás)).

## 4. Claude Code-pluginként (ajánlott)

Pluginként az ágensek és a skill **bármelyik mappában** elérhetők, nem csak a repóban, és a frissítés is egyszerűbb.
Egy Claude Code-munkamenetben (indítsd: `claude`) írd be:

```
/plugin marketplace add szilikaroly/metaANAL
/plugin install metaanalizis@metaanal
```

Ugyanez a terminálból: `claude plugin marketplace add szilikaroly/metaANAL`, majd
`claude plugin install metaanalizis@metaanal`. Utána indítsd újra a Claude Code-ot (vagy a munkamenetben:
`/reload-plugins`). Ellenőrzés: `claude plugin details metaanalizis` — öt ágenst (az orkesztrátor + négy alágens: ma-tervezo,
ma-ellenorzo, ma-ertekelo, ma-metaheadhunter) és egy skillt kell mutatnia.

- **Privát repó:** a Claude Code a gépeden lévő git-azonosítást használja, kérdezni nem tud. Ha a hozzáadás
  hibát ad, futtasd a `gh auth login` és a `gh auth setup-git` parancsot (2. pont), majd próbáld újra. Ha nincs GitHub
  SSH-kulcsod és mégis SSH-val próbálkozik, állítsd be a `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` környezeti változót.
- **ZIP-ből:** a kicsomagolt mappát is felveheted marketplace-ként: `/plugin marketplace add <a kicsomagolt
  metaANAL mappa teljes útja>`, majd ugyanaz a `/plugin install …` parancs. **Ilyenkor a plugin helyben,
  a kicsomagolt mappából fut** (`claude plugin list`: „Read from: <kicsomagolt mappa>/metaanalizis-asszisztens”), nem a
  lenti gyorsítótárból: a parancsok a `<kicsomagolt mappa>/metaanalizis-asszisztens/ma.py`-t hívják, a tudásbázis a
  `<kicsomagolt mappa>/metaanalizis-asszisztens/tudasbazis/tudasbazis.sqlite` (egy új ZIP kicsomagolása felülírja — ha
  meg akarod tartani, állítsd be a `METAELEMZES_KB` változót, lásd lent), és az engedélylistába ennek a mappának a
  `ma.py`-ja kell (5. pont). A mappát ne töröld és ne nevezd át, amíg a plugin telepítve van.
- **Frissítés:** `/plugin marketplace update metaanal` (új változat akkor érkezik, ha a verziószám
  nőtt). Automatikus frissítés: `/plugin` → *Marketplaces* → *metaanal* → *Enable auto-update*.
- **Tudásbázis pluginként** (GitHub-marketplace; a ZIP-útnál lásd fent): a plugin saját mappája frissítéskor
  cserélődik, ezért a tudásbázis-adatbázis a plugin adatmappájában van: `~/.claude/plugins/data/metaanalizis-metaanal/tudasbazis.sqlite` (frissítéskor
  megmarad; eltávolításkor törlődik). Más helyet a `METAELEMZES_KB` környezeti változóval adhatsz meg (a teljes
  fájlútvonal, pl. `METAELEMZES_KB=D:/ma/tudasbazis.sqlite`).
- A plugin parancsai GitHub-marketplace-ből telepítve a telepítési mappából futnak
  (`~/.claude/plugins/cache/metaanal/metaanalizis/<verzió>/`), ZIP-ből a kicsomagolt mappából (fent);
  kézi futtatáshoz egyszerűbb a 2. pontban letöltött példány.

## 5. Javasolt engedélylista

A Claude Code minden parancs előtt engedélyt kér, amíg meg nem engeded. **Egy plugin nem adhat engedélyt** —
ezt neked kell beállítanod. A legegyszerűbb: az első kérdésnél válaszd a *„Yes, and don't ask again”* lehetőséget.
Vagy másold be az alábbiakat a saját beállításaidba: minden projektre `~/.claude/settings.json` (Windows:
`C:\Users\<név>\.claude\settings.json`), csak egy projektre a projekt `.claude/settings.json` fájlja. Ha a fájlban
már van `permissions` rész, a sorokat abba fűzd be.

```json
{
  "permissions": {
    "allow": [
      "Bash(python ma.py *)",
      "Bash(python3 ma.py *)",
      "Bash(py -3 ma.py *)",
      "Bash(python metaanalizis-asszisztens/ma.py *)",
      "Bash(python3 metaanalizis-asszisztens/ma.py *)",
      "Bash(python \"*/.claude/plugins/cache/metaanal/metaanalizis/*/ma.py\" *)",
      "Bash(python3 \"*/.claude/plugins/cache/metaanal/metaanalizis/*/ma.py\" *)",
      "WebFetch(domain:eutils.ncbi.nlm.nih.gov)",
      "WebFetch(domain:pubmed.ncbi.nlm.nih.gov)",
      "WebFetch(domain:www.ncbi.nlm.nih.gov)",
      "WebFetch(domain:www.ebi.ac.uk)",
      "WebFetch(domain:europepmc.org)",
      "WebFetch(domain:api.crossref.org)",
      "WebFetch(domain:doi.org)",
      "WebFetch(domain:api.openalex.org)",
      "WebFetch(domain:clinicaltrials.gov)",
      "WebFetch(domain:www.crd.york.ac.uk)",
      "WebFetch(domain:api.unpaywall.org)",
      "WebFetch(domain:api.semanticscholar.org)",
      "mcp__PubMed",
      "mcp__claude_ai_PubMed",
      "mcp__Clinical_Trials",
      "mcp__claude_ai_Clinical_Trials"
    ],
    "deny": [
      "Read(//**/_privat/**)",
      "Read(//**/*_PHI*)"
    ]
  }
}
```

- Az első öt `Bash` sor a motort engedi a repóból futtatva, a következő kettő a GitHub-marketplace-ből telepített
  pluginból (a `*` a saját mappádat és a plugin verzióját helyettesíti). Ha a Claude Code induláskor figyelmeztet a `*`
  helye miatt, írd be helyette a teljes utat, pl.
  `Bash(python "C:/Users/<név>/.claude/plugins/cache/metaanal/metaanalizis/*/ma.py" *)`.
- **ZIP-ből telepített pluginnál** a parancsok a kicsomagolt mappából futnak, ezért ezt a két sort is vedd fel (a
  saját kicsomagolt mappád teljes útjával): `Bash(python "<kicsomagolt mappa>/metaanalizis-asszisztens/ma.py" *)` és
  `Bash(python3 "<kicsomagolt mappa>/metaanalizis-asszisztens/ma.py" *)` — a gyorsítótáras sorok erre nem illeszkednek.
- A `WebFetch` sorok a tudományos adatbázisok (PubMed, Europe PMC, Crossref, OpenAlex, ClinicalTrials.gov, PROSPERO …)
  lekérdezését, az `mcp__` sorok a PubMed és a ClinicalTrials.gov konnektort engedik (claude.ai → Beállítások →
  Konnektorok; részletek: [ESZKOZOK_ES_HOZZAFERESEK.md](ESZKOZOK_ES_HOZZAFERESEK.md)).
- A `deny` sorok megtiltják, hogy Claude fájlolvasó eszközei a `_privat/` mappákba és a `_PHI` jelölésű fájlokba
  belenézzenek (9. pont).
- A repóban dolgozva ezek nagy része már be van állítva (a repó `.claude/settings.json` fájlja).

## 6. Indítás

**Pluginként** (bármely mappából, pl. a projektjeid mappájából):

```bash
claude --agent metaanalizis:metaanalizis-asszisztens
```

vagy egy szokásos `claude` munkamenetben: `/metaanalizis:metaanalizis <projektmappa> "<kutatási kérdés>"`.

**A repóból** (plugin nélkül; a repó gyökerében, azaz a `metaANAL` mappában):

```bash
claude --agent metaanalizis-asszisztens
```

vagy egy munkamenetben: `/metaanalizis <projektmappa> "<kutatási kérdés>"`.

Az első üzenetben írd le a kutatási kérdést, és ha van, a projektmappa nevét. Az orkesztrátor a tervező
alágenssel elkészítteti a protokollt, minden szakasz végén az ellenőrzővel átnézeti a munkát, a végén az értékelővel
GRADE-et és AMSTAR 2-t készíttet. Új projektmappát kézzel is létrehozhatsz — a `ma.py` parancsokat (itt és a 7.
pontban) a `metaanalizis-asszisztens` mappában futtasd (lásd [3. pont](#3-első-futtatás)); a repó gyökeréből
(`metaANAL`) írd elé a mappát: `python metaanalizis-asszisztens/ma.py …`.

```bash
python ma.py project init <projektmappa> --title "<cím>" --question "<kutatási kérdés>"
```

Az elemzés egy **kimenethez** kötődik (amit összesíteni akarsz, pl. „TBC-incidencia”): a nevéhez az adattábla és a
hatásméret tartozik. A friss projektben még nincs ilyen; a munkapadon az **Áttekintés** vagy az **Elemzés** oldal
„Kimenet felvétele” gombjával veheted fel, vagy parancssorból:

```bash
python ma.py project outcome <projektmappa> --id o1 --name "TBC-incidencia" --data 03_adatok/o1.csv --measure RR
```

(Ez létrehozza a projekt `ma-projekt.json` fájlját is; módosításhoz ugyanez `--replace`-szel.)

## 7. A munkapad (grafikus felület)

A `metaanalizis-asszisztens` mappában (a repó gyökeréből: `python metaanalizis-asszisztens/ma.py gui …`):

```bash
python ma.py gui --project <projektmappa>
```

Parancssor nélkül: Windowson kattints duplán a `metaanalizis-asszisztens` mappa **`ma-munkapad.cmd`** fájljára
(vagy húzd rá a projektmappát), macOS-en a **`ma-munkapad.command`** fájlra (az első megnyitásnál: jobb gomb →
*Megnyitás*). Megkérdezi a projektmappát (a mappát az ablakba húzhatod), és maga keres hozzá Pythont
(`py -3`, `python`, `python3`, `.claude/.venv`).

- A böngésző magától megnyílik a `http://127.0.0.1:8790/…` címen. A felület **csak a saját gépedről** érhető el;
  a megnyitáshoz egyszer használható, 60 másodpercig érvényes kód tartozik — ha lejárt, futtasd újra ugyanezt a
  parancsot.
- Ugyanazt a projektmappát és naplót használja, mint Claude: amit az egyikben rögzítesz, a másik is látja. Claude-ot
  is megkérheted, hogy indítsa el.
- Leállítás: a terminálban `Ctrl+C`, vagy magától leáll 4 óra tétlenség után. Hasznos kapcsolók: `--port <szám>`,
  `--no-browser` (csak kiírja a címet), `--lang en`.
- A felületet és az abból készült pillanatképet **ne töltsd fel sehova, és ne kérd Claude-ot, hogy publikálja**
  (pl. Artifactként): projektadatot tartalmaz.
- Ha az Elemzés oldal azt írja, hogy még nincs kimenet, a „Kimenet felvétele” gombbal add meg (név, adattábla,
  hatásméret) — lásd [6. pont](#6-indítás).

**A v1 újdonságai** (a munkapadon és parancssorból is; a számok mindkét úton a motoréi):

- **Kettős kinyerés:** a második kinyerő a saját gépén dolgozik, a táblát fájlként küldi (11. fejezet, 5. döntés); a
  két tábla a projekt `03_adatok/kettos/<kimenet>.A.csv` és `.B.csv` helyére kerül. Összevetés és egyeztetés: a munkapad
  Kettős kinyerés képernyője, vagy `python ma.py kettos compare --project <projektmappa> --outcome o1`. Amíg feloldatlan
  eltérés van, az S08 (szintézis) szakasz nem zárható.
- **Értékelés:** RoB 2, ROBINS-I, ROBINS-E, QUADAS-2, NOS, QUIPS, JBI, PROBAST+AI, TRIPOD+AI és AMSTAR 2 űrlap magyar
  súgóval (`python ma.py appraisal instruments`); két független értékelő egyezése (κ), konszenzus, forgalmi lámpa, a
  kinyerési tábla `rob` oszlopának szinkronja. Claude csak **AI-vázlatot** készíthet (publikált cikkre, tételenként
  idézettel és egyszerű nyelvű indoklással); azt neked kell jóváhagynod, és sosem számít második értékelőnek.
- **GRADE és SoF:** a motor doménenként javaslatot ad „Miért?” magyarázattal (`python ma.py grade advice --run <futás>
  --project <projektmappa>`), a döntés a tiéd; a SoF-táblát a motor számolja (`python ma.py grade sof …`). A publikációs
  torzítás „gyanított” ítéletét neked kell feloldanod (0 vagy −1, indoklással), addig a GRADE nem rögzíthető.
- **PRISMA 2020 folyamatábra** a figure-forge-hoz: `python ma.py prisma check --composer prisma-flow.json --studies
  03_adatok/studies.json --emit-flowchart folyamatabra.json`.
- **Ábrák:** kumulatív elemzésnél `cumulative.svg`, egyetlen folytonos moderátoros meta-regressziónál `bubble.svg`;
  angolul vagy rétegekkel: `python ma.py figure --plot <futásmappa> --kind bubble --lang en --out bubble_en.svg`.
  A munkapadon az **Ábra-export** képernyő (az Eredmények oldal „Ábra-export” gombja) ugyanezt adja
  magyar vagy angol felirattal; minden ábra számait a szerver visszaellenőrzi (zöld jelvény csak teljes egyezésnél).
- **Validator-keresztellenőrzés:** ha a `validator` plugin telepítve van, az értékelő űrlap alján a
  „Keresztellenőrzés a validator pluginnal” doboz összeveti a plugin eredményét a motoréval, és megmondja, hol nem
  összevethető (a plugin ismert hibái miatt) — tájékoztatás, az ítélet a tiéd.

**Pillanatkép és audit-csomag** (a munkapad indítása nélkül is; szintén a `metaanalizis-asszisztens` mappában):

```bash
python ma.py gui snapshot --project <projektmappa>        # csak olvasható HTML a társszerzőknek
python ma.py gui audit-export --project <projektmappa>    # ZIP a 07_ellenorzes/audit/<dátum>/ mappába
```

- A **pillanatkép** egyetlen HTML-fájl (alapból a `07_ellenorzes/pillanatkep/` mappában). Python és internet nélkül,
  dupla kattintással megnyílik, és semmilyen hálózati kérést nem indít; szerkeszteni nem lehet benne, az író gombok
  csak a megfelelő parancsot mutatják. Mit takar ki? **A** osztály: az adattáblák benne vannak; **B**: a táblák
  kimaradnak (ha mégis kellenek: `--keep tables`), az értékelők neve monogram lesz; **C**: táblák soha. A `_privat/`
  mappa, a PDF-ek és a tudásbázis teljes szövege sosem kerül bele. A pillanatképet sem szabad feltölteni vagy
  publikálni (Claude Artifactként sem).
- Az **audit-csomag** determinisztikus ZIP (ugyanabból a projektállapotból bájtra azonos): `manifest.json`
  (fájlok sha256-jával), a tevékenységnapló hash-lánca, a futások fájljai és `rerun.cmd`/`rerun.sh` az
  újrafuttatáshoz.

## 7a. Metaheadhunter: források és API-kulcsok (opcionális)

A Metaheadhunter (meglévő metaanalízisek bányászata: `python ma.py headhunter …`, a munkapadon PRISMA fül →
Metaheadhunter) a PubMed, az Europe PMC, az OpenAlex, a Scopus, a ClinicalTrials.gov és tartalékként a Crossref
nyilvános API-jait hívja. Kulcs nélkül is működik (Scopus nélkül); a kulcsok gyorsabbá és megbízhatóbbá teszik. Hol
kapod őket, és mire jók: [ESZKOZOK_ES_HOZZAFERESEK.md](ESZKOZOK_ES_HOZZAFERESEK.md) 6a. pont (Scopus Magyarországon
jellemzően az **EISZ** intézményi előfizetésén át; a kulcsot intézményi hálózatról kérd a dev.elsevier.com oldalon).

**A kulcs csak környezeti változóban lehet** — ne írd a csevegésbe, parancssorba, projektfájlba vagy a repóba. A program
a kulcsnak csak a meglétét mutatja, az értékét sehová nem írja ki.

| Változó | Mi ez |
|---|---|
| `MA_CONTACT_EMAIL` | a saját e-mail-címed (az NCBI és az OpenAlex udvariassági kérése) |
| `MA_OPENALEX_APIKEY` | OpenAlex API-kulcs |
| `MA_SCOPUS_APIKEY` | Elsevier / Scopus API-kulcs |
| `MA_SCOPUS_INSTTOKEN` | Scopus intézményi token (ha nem az intézményi hálózatról dolgozol) |
| `MA_NCBI_APIKEY` | NCBI E-utilities kulcs (opcionális; 3 helyett 10 kérés/s) |

**Beállítás tartósan** (utána nyiss új terminált, és a Claude Code-ot is indítsd újra):

- **Windows** (Parancssor vagy PowerShell; a felhasználói környezetbe ír):
  ```
  setx MA_CONTACT_EMAIL "nev@intezmeny.hu"
  setx MA_OPENALEX_APIKEY "<kulcs>"
  setx MA_SCOPUS_APIKEY "<kulcs>"
  ```
  (Vagy: *Gépház → Rendszer → Névjegy → Speciális rendszerbeállítások → Környezeti változók → Felhasználói változók*.)
- **macOS** (zsh): a `~/.zshrc` végére:
  ```bash
  export MA_CONTACT_EMAIL="nev@intezmeny.hu"
  export MA_OPENALEX_APIKEY="<kulcs>"
  export MA_SCOPUS_APIKEY="<kulcs>"
  ```
- **Linux** (bash): ugyanez a `~/.bashrc` végére.

**Ellenőrzés a saját gépeden** (a `metaanalizis-asszisztens` mappában):

```bash
python ma.py headhunter sources --check                     # minden forrás: rendben / nincs beállítva / nem érhető el
python ma.py headhunter sources --check --sources scopus     # csak a Scopus
```

Kilépési kód 0: minden bekapcsolt forrás elérhető; 3: legalább egy nem — az üzenet megmondja, miért. A Scopus-kapcsolatot
a fejlesztéskor élőben nem lehetett kipróbálni, ezért az első használat előtt ezzel igazold. (Fejlesztőknek: a valódi
Scopus-válaszok rögzítése `python3 tests/reference/headhunter/cassettes/record_cassettes.py --only scopus_live`.)

Az „Állapot” oszlop a parancssor magyar címkéit mutatja (zárójelben a `--json` / `--lang en` angol állapotkódja):

| Állapot a táblázatban | Teendő |
|---|---|
| Scopus: „nincs beállítva” (`not_configured`) | A `MA_SCOPUS_APIKEY` nincs a környezetben: `setx` / `export` után **új** terminál kell. |
| Scopus: „kulcs elutasítva” (`unauthorized`, HTTP 401) | A kulcs hibás, vagy nem intézményi IP-ről használod: intézményi hálózat / VPN, vagy `MA_SCOPUS_INSTTOKEN`. |
| Scopus: „nincs jogosultság” (`forbidden`, HTTP 403) | Az intézményed előfizetése nem fedi az adott API-t — kérdezd a könyvtárat (EISZ). |
| OpenAlex: „keret elfogyott (eddig: …)” (`rate_limited`, HTTP 429) | A kulcs nélküli napi keret elfogyott (IP-nként közös): állíts be `MA_OPENALEX_APIKEY`-t, vagy várj a zárójelben írt időpontig. |
| Egy forrás „nem elérhető” (`unreachable`) | Hálózat / tűzfal / proxy: a többi forrással a lépés részlegesen lefut (kilépési kód 3), és jelzi, mi hiányzik. |

Engedély: a `ma.py headhunter …` a hálózatot a Pythonon át éri el, ezért az 5. pont `Bash(… ma.py *)` sorai elegendők.

## 8. Saját PDF-ek a tudásbázisban

A tudásbázis a módszertani könyvek és cikkek **teljes szövegében** is tud keresni — ezeket a saját, jogszerűen
beszerzett példányaidból töltöd be. Ehhez `pypdf` kell (1. pont).

```bash
python ma.py kb ingest <a PDF-eket tartalmazó mappa>
python ma.py kb ingest <egy fájl.pdf> --source-id sajat2026 --citation "Szerző A. Cím. Kiadó; 2026."
python ma.py kb stats
```

- A mappa **bármelyik saját mappád** lehet (pl. `Dokumentumok/ma-forrasok`). A repóban a
  `metaanalizis-asszisztens/tudasbazis/forrasok/` is megfelel (ezt a git kihagyja). Pluginként **ne** a plugin mappájába
  tedd a fájlokat, mert az frissítéskor cserélődik.
- Az ismert forrásokat a fájlnévből felismeri; saját dokumentumhoz add meg a `--source-id` és a `--citation` értékét.
  Támogatott: PDF, DOCX, TXT, MD. Az újratöltés biztonságos (a változatlan fájl nem duplikálódik).
- **Szerzői jog:** a teljes szöveg csak a te gépeden, a helyi adatbázisban marad — nem kerül a repóba, és ne add
  tovább. Claude a kereséskor csak rövid találati részleteket lát.

## 9. Adatvédelem

- **Betegszintű adat csak anonimizáltan** kerülhet egy projektbe, és akkor is csak a projekt `_privat/` mappájába.
  Claude ezt nem olvassa (az 5. pont `deny` sorai ezt a Claude-eszközökre ki is kényszerítik). A betegszintű fájl
  nevében jelöld: `…_PHI.csv` (pl. `kohorsz_PHI.csv`).
- A metaanalízis maga csak **aggregált** (közleményekből kinyert) adatot használ — ez a szokásos eset.
- **vault plugin, OneDrive, iCloud:** ha a `vault` plugin telepítve van, a `~/Documents/claude` alatti projekteket
  automatikusan a GitHubra menti; a OneDrive és az iCloud a *Dokumentumok* mappát szinkronizálhatja. Érzékeny adatot
  tartalmazó projektet tegyél ezeken kívüli mappába, vagy a projekt `.gitignore`-jába vedd fel a `_privat/` mappát és a
  `*_PHI*` mintát. A munkapad figyelmeztet, ha ilyen helyen lévő projektet nyitsz meg.
- A munkapadot, a pillanatképet és az eredménymappákat ne töltsd fel nyilvános helyre.

## 10. Hibaelhárítás

| Jelenség | Teendő |
|---|---|
| `python` nem található, vagy a Microsoft Store nyílik meg (Windows) | A Windows „alkalmazás-végrehajtási alias” csonkja ez, nem valódi Python. Telepítsd a Pythont a python.org-ról („Add python.exe to PATH”), és kapcsold ki a csonkot: *Gépház → Alkalmazások → Speciális alkalmazásbeállítások → Alkalmazás-végrehajtási aliasok* → `python.exe` és `python3.exe` ki. Addig használd a `py -3` indítót: `py -3 ma.py selftest`. |
| macOS / Linux: `python: command not found` | Használd a `python3`-at: `python3 ma.py selftest`. |
| Több Python van, és rossz indul | `python --version` 3.9-nél kisebbet ír → Windows-on `py -3`, máshol `python3` (vagy a teljes út). |
| `SyntaxError` vagy furcsa hiba az első parancsnál | Valószínűleg Python 3.8 vagy régebbi fut: frissíts 3.9+-ra. |
| `selftest` → `FAILED` | Ne elemezz vele; küldd el a kimenetet a tulajdonosnak. |
| A munkapad portja foglalt | Alapból a 8790-es portot, foglaltság esetén a 8791–8799-et, végül egy szabadot használ. Megadhatod kézzel is: `python ma.py gui --project <mappa> --port 8800`. Ha ugyanerre a projektre már fut egy példány, az új parancs csak új belépőkódot kér tőle. |
| A böngésző nem nyílik meg | A terminálban kiírt `http://127.0.0.1:…` címet másold a böngészőbe (60 s-on belül), vagy indítsd `--no-browser`-rel. |
| `/plugin marketplace add` hiba (privát repó) | `gh auth login`, majd `gh auth setup-git`; ellenőrizd, hogy elfogadtad-e a GitHub-meghívót. |
| A plugin ágensei nem látszanak | `/reload-plugins` vagy a Claude Code újraindítása; `claude plugin list` mutatja, engedélyezve van-e. |
| `PDF-hez telepítsd a pypdf csomagot …` | `python -m pip install pypdf` (Windows: `py -3 -m pip install pypdf`). A beolvasott (szkennelt) PDF szövegét egyik sem nyeri ki. |
| A tudásbázis „nincs felépítve” | `python ma.py kb build`. |
