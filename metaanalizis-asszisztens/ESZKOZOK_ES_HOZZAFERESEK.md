# Eszközök és hozzáférések — mit engedélyezz most

Ez a lista azt mutatja meg, mire van szüksége a metaanalízis-asszisztensnek ahhoz, hogy egy szisztematikus
áttekintést a protokolltól a kéziratig végig tudjon vinni. A **Kötelező** oszlop azt jelzi, mi kell az alapműködéshez.
A projektenkénti, konkrét listát a `ma-tervezo` alágens állítja össze (`00_protokoll/eszkozok_hozzaferesek.md`),
a tudásbázis `tool` táblájából és a `PREFLIGHT` ellenőrzőlistából.

## 1. Claude-ban (claude.ai → Beállítások → Konnektorok)

| Konnektor | Mire kell | Kötelező? | Megjegyzés |
|---|---|---|---|
| **PubMed** | felderítő keresés, hivatkozások létezésének és adatainak ellenőrzése, absztrakt/teljes szöveg a szúrópróbához | **igen** | Az ellenőrző alágens enélkül nem tudja igazolni a hivatkozásokat (ilyenkor DOI-n keresztül, WebFetch-csel próbálkozik). |
| **Clinical Trials** (ClinicalTrials.gov) | regisztrált, nem közölt vizsgálatok (publikációs torzítás), protokoll–közlemény összevetés | ajánlott | |
| **Consensus** | gyors tájékozódás a kérdésről a tervezés elején | opcionális | Nem helyettesíti a szisztematikus keresést. |
| **Google Drive** | a teljes szövegű PDF-ek megosztása az ágensekkel | opcionális | Helyi mappa is megfelel (`tudasbazis/forrasok/` vagy a projektmappa). |

A Claude Code a claude.ai konnektorokat helyben `mcp__claude_ai_<Név>__…` néven látja. Az alágensek mindkét
névváltozatot engedélyezik, ezért ezzel nincs teendő.

## 2. Claude Code-engedélyek

**A repóban dolgozva** a `.claude/settings.json` előre engedélyezi azokat a csak olvasó vagy helyi műveleteket, amelyeket
az ágensek gyakran futtatnak:
- a motor futtatása: `python metaanalizis-asszisztens/ma.py …`;
- a tesztek;
- a tudományos API-k lekérése: NCBI E-utilities, Europe PMC, Crossref, OpenAlex, ClinicalTrials.gov, PROSPERO, Unpaywall, Semantic Scholar;
- a PubMed és a Clinical Trials MCP-eszközei.

Ha ezt nem szeretnéd, töröld a fájlt: ekkor minden ilyen műveletnél rákérdez.

**Pluginként telepítve** (`metaanalizis@anamnezis-asszisztens`) ez a fájl nem érvényes, és a plugin maga nem adhat
engedélyt (a plugin `settings.json`-jából a Claude Code csak az `agent` és a `subagentStatusLine` kulcsot olvassa). A
javasolt engedélylistát — a plugin telepítési útjára illeszkedő `Bash(python "…/ma.py" *)` szabályokkal és a
`_privat/` mappát tiltó `deny` sorokkal — a [TELEPITES.md](TELEPITES.md) 5. pontja adja; a saját
`~/.claude/settings.json` vagy a projekt `.claude/settings.json` fájljába másold.

## 3. Helyi szoftver

| Eszköz | Mire kell | Kötelező? |
|---|---|---|
| **Python ≥ 3.9** (a te `.claude/.venv`-ed is jó; Windows-on `py -3` is) | a motor, a tudásbázis, a projektnapló és a munkapad — külső csomag nélkül | **igen** |
| `pypdf` (`pip install pypdf`) vagy poppler `pdftotext` | PDF-ek betöltése a tudásbázisba (`kb ingest`) | ajánlott |
| R + `metafor` (+ `meta`) | független keresztellenőrzés; a motor által nem tudott modellek: többszintű (`rma.mv`), hálózati, dózis–hatás, bayesi | ajánlott |
| Zotero (ingyenes) | hivatkozáskezelés, RIS-export, duplikátumszűrés | ajánlott |
| Stata / MetaXL | a Khan-könyv példáinak reprodukálása (IVhet, Doi-plot); nem szükséges, a motor tudja | opcionális |

## 4. Intézményi előfizetések (a könyvtáradon keresztül)

| Adatbázis | Miért | Kötelező? |
|---|---|---|
| **Embase** | gyógyszeres és európai vizsgálatok — a Cochrane-ajánlás szerint MEDLINE mellett kötelező forrás | **igen** (orvosi SR) |
| **Cochrane Library / CENTRAL** | kontrollált vizsgálatok regisztere | **igen** (beavatkozásos SR) |
| Web of Science Core Collection vagy Scopus | hivatkozáskövetés, multidiszciplináris lefedettség | ajánlott |
| PsycINFO, CINAHL | pszichológiai, ápolástudományi kérdésekhez | témától függ |
| Teljes szöveg (EZproxy / LibKey) | adatkinyeréshez | **igen** |

Magyarországon a Scopus és a Web of Science jellemzően az EISZ-en keresztül érhető el; az Embase és a CENTRAL
hozzáférését ellenőrizd az intézményednél.

## 5. Fiókok és regisztrációk

| Szolgáltatás | Mire kell | Kötelező? |
|---|---|---|
| **PROSPERO** (ingyenes fiók) vagy OSF | protokoll-regisztráció a szűrés megkezdése előtt | **igen** |
| **Rayyan** (ingyenes fiók) vagy **Covidence** (előfizetés) | kettős, vak szűrés és egyeztetés | **igen** — valamelyik |
| ResearchRabbit, Litmaps, Connected Papers, Elicit, Scite, SciSpace | hivatkozás-hálózat feltérképezése, kiegészítő felderítés | opcionális |

A ResearchRabbit és a hasonló felfedező eszközök csak kiegészítésre valók (hivatkozáskövetés, hiányzó vizsgálatok
keresése). A szisztematikus keresést nem helyettesítik, mert nem reprodukálhatók és nem dokumentálhatók PRISMA-S szerint.

## 6. API-kulcsok (opcionális, gyorsabb és megbízhatóbb lekérdezéshez)

| Kulcs | Hol | Haszon |
|---|---|---|
| NCBI E-utilities API key | ncbi.nlm.nih.gov → fiók → API Key Management | 3 helyett 10 lekérés/s; a composer plugin `~/.config/ncbi/env` fájlból olvassa |
| Scopus / Elsevier API key | dev.elsevier.com (intézményi IP-ről) | Scopus-keresés és találatszámok programból |
| Semantic Scholar API key | semanticscholar.org/product/api | hivatkozás-hálózat programból |
| OpenAlex, Crossref, Unpaywall | nem kell kulcs — elég az e-mail-cím a kérésben („polite pool”) | DOI-ellenőrzés, nyílt hozzáférésű teljes szöveg |

### 6a. A Metaheadhunter forrásai és környezeti változói

A Metaheadhunter (meglévő metaanalízisek bányászata; `ma.py headhunter …`, `ma-metaheadhunter` ágens) a kulcsokat
**kizárólag környezeti változóból** olvassa: a csevegésbe, parancssorba vagy projektfájlba soha ne írd be őket. A
program a kulcsnak csak a meglétét mutatja (igen/nem), az értékét sem a kimenetbe, sem a naplóba, sem a
gyorsítótárba nem írja.

| Forrás | Környezeti változó | Hol kapod | Kötelező? |
|---|---|---|---|
| PubMed (NCBI E-utilities), Europe PMC, ClinicalTrials.gov | `MA_CONTACT_EMAIL` (a saját e-mail-címed; az NCBI és az OpenAlex „polite pool” ezt kéri) | — | **ajánlott** (kulcs nélkül is működik) |
| PubMed gyorsabban | `MA_NCBI_APIKEY` | ncbi.nlm.nih.gov → fiók → API Key Management | opcionális |
| OpenAlex | `MA_OPENALEX_APIKEY` | openalex.org → ingyenes API-kulcs (a lista-lekérdezések napi kerete IP-nként közös; kulccsal saját keret) | ajánlott |
| Scopus (Elsevier) | `MA_SCOPUS_APIKEY` + intézményi hálózaton kívül `MA_SCOPUS_INSTTOKEN` | dev.elsevier.com → API Key (intézményi IP-ről kérd); az Insttoken-t az intézményi könyvtár / az Elsevier adja | opcionális (ha van intézményi Scopus) |
| Crossref | — | — | automatikus tartalék |

**Scopus Magyarországon:** az intézményi Scopus-hozzáférés jellemzően az **EISZ** (Elektronikus Információszolgáltatás
Nemzeti Program) előfizetésén át érhető el. A kulcsot a dev.elsevier.com oldalon az intézményi hálózatról (vagy VPN-ről)
kérd; ha otthonról dolgozol, az intézményi könyvtár adhat Insttoken-t (`MA_SCOPUS_INSTTOKEN`). Ha nincs Scopus, a
Metaheadhunter nélküle is fut (PubMed, Europe PMC, OpenAlex, ClinicalTrials.gov).

**Ellenőrzés a saját gépeden** (a beállítás után, új terminálban):

```bash
python metaanalizis-asszisztens/ma.py headhunter sources --check              # minden forrás: rendben / nincs beállítva / nem elérhető / keret elfogyott / kulcs elutasítva / nincs jogosultság
python metaanalizis-asszisztens/ma.py headhunter sources --check --sources scopus
```

Kilépési kód 0: minden bekapcsolt forrás elérhető; 3: legalább egy nem érhető el (az üzenet megmondja, miért —
hiányzó kulcs, jogosultság, kvóta, hálózat). A Scopus-kliens a fejlesztői környezetből élőben nem volt igazolható, ezért
az első Scopus-használat előtt ezzel a paranccsal igazold a saját gépeden. A beállítás lépései operációs rendszerenként:
[TELEPITES.md](TELEPITES.md) 7a. pont.

## 7. A szk-plugins pluginjai (ha telepítve vannak)

| Plugin | Előfeltétel | Szerep |
|---|---|---|
| `composer` | `biopython`, `requests`; NCBI-hitelesítés `~/.config/ncbi/env` | PRISMA 2020 számok, PRISMA-S napló, PROSPERO-rekord, 5D bibliográfiai validálás |
| `validator` | csak standard könyvtár | RoB-eszközválasztás, GRADE, AMSTAR 2, PROBAST+AI / TRIPOD+AI (a motor v1-ben ezeket natívan is tudja: `ma.py appraisal`, `ma.py grade`; forrás: validator 1.0.0) |
| `figure-forge` | `matplotlib`, `numpy`, `pandas`, `lxml`, `python-pptx`, `Pillow` | a motor SVG-ábráinak auditja; PRISMA-folyamatábra a motor specifikációjából (`ma.py prisma check … --emit-flowchart`) |
| `presubmit` | `python-docx` (PDF-hez PyMuPDF vagy `pdftotext`) | kézirat-ellenőrzés beadás előtt |

**Ismert hiba a validatorban (1.0.0):** a GRADE-összesítés a publikációs torzítás doménjén a „suspected” és a „strongly
suspected” választ nem minősíti le. Az értékelő alágens ezt a domént kézzel ellenőrzi; a motor GRADE-tára (`ma.py grade
save|record`, a munkapad GRADE-lapja) ezt kikényszeríti: a „gyanított” feloldatlan, amíg indoklással 0-t vagy −1-et nem
választasz, az „erősen gyanított” −1 (11. fejezet, 4. döntés). További, a motor natív definícióinál javított
validator-eltérések (QUADAS-2 és ROBINS-I/E polaritás, ROBINS-I 1.1, a megválaszolatlan domén „LOW”-ja, a QUIPS
„Partly”, a NOS részleges csillaga): `ma.py appraisal schema <eszköz> --json` → `validator_differences`.

## 8. Adatvédelem

- Betegszintű adat csak **anonimizáltan**, a projekt `_privat/` mappájában lehet; ezt Claude nem olvassa (javasolt `deny`
  szabályok: [TELEPITES.md](TELEPITES.md) 5. pont). A metaanalízis maga aggregált adatot használ.
- Betegszintű adat nem kerülhet a repóba; a gyökér `.gitignore` a `*_[Pp][Hh][Ii]`, `*_[Pp][Hh][Ii][._-]*`,
  `[Pp][Hh][Ii]_*`, `*.[Pp][Hh][Ii].*` és `*beteg_adat*` mintákat kizárja — a betegszintű fájl (vagy mappa) nevében tehát a PHI (kis- vagy nagybetűvel) önálló, elválasztott
  tagként szerepeljen: a név végén `_PHI`, utána `.`, `_` vagy `-` (pl. `betegek_PHI.csv`, `kohorsz_PHI_v2.csv`), az
  elején `PHI_`, vagy `.phi.` között; illetve a név tartalmazza a `beteg_adat` részt. A „phi” betűsor más szó részeként
  (pl. `morphine-…`, `delphi-…`, `neutrophil-…`, `dengue_philippines`, `smith_phillips_2020.pdf`) nem zár ki semmit.
- A **vault** plugin a `~/Documents/claude` alatti projekteket automatikusan feltölti a GitHubra. Érzékeny kinyerési adatot
  ne tárolj ott, vagy tedd `.gitignore`-ba.
- A forrásdokumentumok teljes szövege (`tudasbazis/forrasok/` vagy bármely saját mappa, `tudasbazis/*.sqlite`, pluginként a
  plugin adatmappája) szerzői jogvédett, ezért nem kerül a repóba, és nem adható tovább.
- A munkapadot és a pillanatképét soha ne publikáld Artifactként, és ne töltsd fel.
