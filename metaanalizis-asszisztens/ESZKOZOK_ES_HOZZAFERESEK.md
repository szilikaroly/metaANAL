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

## 2. Claude Code-engedélyek (ebben a repóban)

A `.claude/settings.json` előre engedélyezi azokat a csak olvasó vagy helyi műveleteket, amelyeket az ágensek gyakran
futtatnak:
- a motor futtatása: `python metaanalizis-asszisztens/ma.py …`;
- a tesztek;
- a tudományos API-k lekérése: NCBI E-utilities, Europe PMC, Crossref, OpenAlex, ClinicalTrials.gov, PROSPERO, Unpaywall, Semantic Scholar;
- a PubMed és a Clinical Trials MCP-eszközei.

Ha ezt nem szeretnéd, töröld a fájlt: ekkor minden ilyen műveletnél rákérdez.

## 3. Helyi szoftver

| Eszköz | Mire kell | Kötelező? |
|---|---|---|
| **Python ≥ 3.8** (a te `.claude/.venv`-ed is jó) | a motor, a tudásbázis és a projektnapló — külső csomag nélkül | **igen** |
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

## 7. A szk-plugins pluginjai (ha telepítve vannak)

| Plugin | Előfeltétel | Szerep |
|---|---|---|
| `composer` | `biopython`, `requests`; NCBI-hitelesítés `~/.config/ncbi/env` | PRISMA 2020 számok, PRISMA-S napló, PROSPERO-rekord, 5D bibliográfiai validálás |
| `validator` | csak standard könyvtár | RoB-eszközválasztás, GRADE, AMSTAR 2, PROBAST+AI / TRIPOD+AI |
| `figure-forge` | `matplotlib`, `numpy`, `pandas`, `lxml`, `python-pptx`, `Pillow` | a motor SVG-ábráinak auditja; PRISMA-folyamatábra |
| `presubmit` | `python-docx` (PDF-hez PyMuPDF vagy `pdftotext`) | kézirat-ellenőrzés beadás előtt |

**Ismert hiba a validatorban:** a GRADE-összesítés a publikációs torzítás doménjén a „suspected” és a „strongly
suspected” választ nem minősíti le. Az értékelő alágens ezt a domént kézzel ellenőrzi.

## 8. Adatvédelem

- Betegszintű adat nem kerülhet a repóba; a gyökér `.gitignore` a `*_[Pp][Hh][Ii]`, `*_[Pp][Hh][Ii][._-]*`,
  `[Pp][Hh][Ii]_*`, `*.[Pp][Hh][Ii].*` és `*beteg_adat*` mintákat kizárja — a betegszintű fájl (vagy mappa) nevében tehát a PHI (kis- vagy nagybetűvel) önálló, elválasztott
  tagként szerepeljen: a név végén `_PHI`, utána `.`, `_` vagy `-` (pl. `betegek_PHI.csv`, `kohorsz_PHI_v2.csv`), az
  elején `PHI_`, vagy `.phi.` között; illetve a név tartalmazza a `beteg_adat` részt. A „phi” betűsor más szó részeként
  (pl. `morphine-…`, `delphi-…`, `neutrophil-…`, `dengue_philippines`, `smith_phillips_2020.pdf`) nem zár ki semmit.
- A **vault** plugin a `~/Documents/claude` alatti projekteket automatikusan feltölti a GitHubra. Érzékeny kinyerési adatot
  ne tárolj ott, vagy tedd `.gitignore`-ba.
- A forrásdokumentumok teljes szövege (`tudasbazis/forrasok/`, `tudasbazis/*.sqlite`) szerzői jogvédett, ezért nem kerül a repóba.
