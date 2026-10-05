# Metaheadhunter — meglévő metaanalízisek bányászata (terv és build-szerződés, v1)

**Állapot:** build-szerződés v1 (2026-10-05). A build-ágensek ezt követik; ami itt nincs rögzítve, arról a
build-ágens dönt, de a döntést a saját jelentésében megnevezi. **Kód csak új fájlokba kerül** (20. fejezet); a
meglévő fájlok bekötését (CLI, homlokzat, GUI-regiszter, skill, plugin-másolatok, dokumentáció) később az
integrátor végzi (21. fejezet).

**A felhasználó (orvos-kutató) kérése szó szerint:**

> „legyen metaheadhunter funkció ami kikeresi a témában ismert metaanaliziseket kicsomagolja belőlük az azokban
> felhasznált cikkeket és kikeresi majd őket a duplumokat validálja majd kizárja végül több metanalízisben
> cikkekből a plugin egy nagyot csinál valamint kiegészíti a frissebb irodalommal"
>
> „openalex és scopus bekütés is legyen lehetőség"

**Felületi név:** *Metaheadhunter — meglévő metaanalízisek bányászata* (EN: *Metaheadhunter — mining existing
meta-analyses*). Parancs: `python -m metaelemzes.headhunter …` (bekötés után: `ma.py headhunter …`).

**Mit csinál, egyszerűen (kezdőknek):**

1. Megkeresi a témádban már megjelent szisztematikus áttekintéseket és metaanalíziseket (PubMed, Europe PMC,
   opcionálisan OpenAlex és Scopus).
2. Te kiválasztod, melyiket „bányássza ki" (1. ellenőrzőpont).
3. Kiolvassa belőlük a **bevont** vizsgálatok listáját — nem a teljes irodalomjegyzéket —, és minden tételhez
   megmutatja, *honnan* vette (melyik táblázat, melyik sor, szó szerinti idézet).
4. Minden vizsgálatot azonosít (PMID, DOI, PMCID, regiszterszám) — azonosítót csak adatbázis-válaszból fogad el.
5. Megkeresi a duplumokat (ugyanaz a cikk több áttekintésben; ugyanannak a vizsgálatnak több közleménye), a
   bizonytalanokat eléd teszi döntésre.
6. A saját PICO-d szerint szűrsz (a program javasol és indokol, te döntesz, az okot rögzíti).
7. Egyetlen nagy, egyesített vizsgálatlistát készít, amelyben minden vizsgálatnál látszik, mely áttekintések
   vonták be és milyen számokat közöltek róla (ezek **másodlagos** adatok: elemzés előtt az eredeti cikkel
   ellenőrizni kell).
8. Frissítő keresést futtat a forrás-áttekintések utolsó keresési dátuma óta, és PRISMA 2020 folyamatábra-
   számokat ad, amelyeket a motor `prisma check`-je ellenőriz.

---

## Tartalom

0. Hatókör és fogalmak
1. Nem alku tárgyát képező elvek (N1–N10)
2. A folyamat és az emberi ellenőrzőpontok
3. Források: PubMed, Europe PMC, OpenAlex, Scopus, ClinicalTrials.gov (+ Crossref)
4. Adatmodell `szk.ma.headhunter/v1` és JSON-sémák
5. Forrás-áttekintések felkutatása (L1) és kiválasztása (EP1)
6. A bevont vizsgálatok kinyerése (L3) — stratégiák megbízhatósági sorrendben
7. Feloldás (L4): azonosítók és metaadatok
8. Duplikátumok és vizsgálat-kapcsolás (L5, EP3)
9. Átfedés (L6): hivatkozási mátrix és CCA
10. Jogosultsági szűrés a saját PICO szerint (L7, EP4)
11. Egyesítés (L9) és a másodlagos adatok
12. Frissítő keresés és hivatkozáskövetés (L8)
13. PRISMA 2020 leképezés (`szk.prisma-flow/v1`)
14. Parancssor (CLI)
15. Gépi ellenőrzések (H-kódok)
16. Ágens: `ma-metaheadhunter`
17. Grafikus felület (varázsló-képernyő)
18. Tudásbázis-seed (`rules_HH.json`, `knowledge_HH.json`, `sources_HH.json`)
19. Tesztterv
20. Fájl-tulajdon: ki mit épít
21. Az integrátor teendői (bekötés, dokumentáció)
22. Hivatkozások (ellenőrzött)
23. Nyitott kérdések és kockázatok

---

## 0. Hatókör és fogalmak

| Fogalom | Jelentés |
|---|---|
| **forrás-áttekintés** (source review) | egy közölt szisztematikus áttekintés vagy metaanalízis, amelyből a bevont vizsgálatokat kinyerjük |
| **bevont vizsgálat** (included study) | amit a forrás-áttekintés *bevont* (nem csak hivatkozott) — a kinyerés célja |
| **jelölt** (candidate) | egy áttekintésből kinyert, még meg nem erősített bevont-vizsgálat tétel (`reviews/<id>.json` → `candidates[]`) |
| **rekord / közlemény / jelentés** (record, report) | egy bibliográfiai egység (cikk, absztrakt, regiszter-eredmény); azonosítója `rec-…` |
| **vizsgálat** (study) | a kutatás maga; egy vizsgálatnak több közleménye lehet (társközlemények); azonosítója `st-…` |
| **társközlemény** (companion report) | ugyanannak a vizsgálatnak további közleménye (követés, másodlagos elemzés, protokoll, konferencia-absztrakt) |
| **bizonyíték-lokátor** (evidence) | áttekintés-azonosító + hely (táblázat/sor/fejezet/hivatkozás-ID/oldal) + szó szerinti rövid idézet (≤ 300 karakter) |
| **másodlagos adat** | szám, amelyet egy áttekintés közölt egy vizsgálatról (n, esemény, átlag, SD, hatásméret); nem az eredeti közleményből származik |
| **javaslat** vs **döntés** | a program/ágens *javasol* (proposal), ember *dönt* (decision, `decisions.jsonl`); a kettő soha nem keveredik |
| **ellenőrzőpont** (EP1–EP6) | ahol a folyamat emberi döntésre vár (2. fejezet) |

**Nem célok.** A Metaheadhunter nem végez metaanalízist (az a motor dolga), nem értékeli az áttekintéseket
AMSTAR 2 szerint (csak jelzéseket mutat; az értékelést ember végzi a `kb checklist AMSTAR2` alapján), nem tárol
teljes szöveget, és nem hoz végső döntést. **Nem helyettesíti a protokoll szerinti teljes szisztematikus
keresést** (D-S03-001): kiegészíti azt az „egyéb módszerek" ágon (előző áttekintések + hivatkozáskövetés) és egy
dátumkorlátos frissítő kereséssel. Ha a felhasználó *csak* erre épít, a riport (`report.md`) ezt a módszertani
korlátot kimondja: a bányászott halmaz örökli a forrás-áttekintések keresési hiányait; ezt a frissítő keresés és a
saját PICO szerinti szűrés csak részben ellensúlyozza.

**Módszertani keret.** Az előző áttekintések bevont vizsgálatainak átnézése bevett kiegészítő keresési módszer
(hivatkozáskövetés: Hirt 2023, TARCiS 2024; D-S03-008), az áttekintések áttekintésének (overview, umbrella review)
módszertana pedig kidolgozott (Cochrane Handbook V. fejezet; Aromataris 2015; PRIOR 2022). A frissítés módjára
Garner 2016 ad döntési keretet és ellenőrzőlistát. A forrás-áttekintések közti átfedést a CCA-val mérjük
(Pieper 2014). Az áttekintésekből átvett számok hibásak lehetnek (Gøtzsche 2007; Jones 2005; Mathes 2017) — ezért
másodlagosak. A teljes, ellenőrzött hivatkozáslista a 22. fejezetben.

---

## 1. Nem alku tárgyát képező elvek (N1–N10)

**N1 — Nincs kitalált vizsgálat, azonosító vagy szám.**
- Minden bevont-vizsgálat állítás (`candidates[].role_in_review = included…`) legalább egy bizonyítékra mutat
  (`evidence_ids`, `minItems: 1`), és a bizonyíték: `review_id` + `locator` + szó szerinti `quote` (≤ 300 karakter).
- Minden azonosító `idval` objektum (`value`, `source`, `via`, `at`): a `source` API-név (`pubmed`, `europepmc`,
  `openalex`, `scopus`, `ctgov`, `crossref`, `pmc`), vagy `review` (az áttekintés saját hivatkozásában szerepelt),
  vagy `user` (a felhasználó gépelte). A `review` és a `user` eredetű azonosítót egy API-hívásnak meg kell
  erősítenie (`confirmed_by`), különben a rekord nem „feloldott" (H003), és nem kerülhet a végső halmazba.
- Ágens (LLM) **soha** nem ad azonosítót. Az ágens-kimenet (`agent-classify import`) minden azonosítót eldob, a
  rekordot a program API-n keresztül oldja fel újra. Az ágens idézetét a program szó szerint visszakeresi a
  forrásszövegben; ha nincs meg, a tételt elutasítja (H004).
- Számot a program nem becsül, nem számol át és nem „javít" — csak idéz (lokátorral) és összevet.

**N2 — A másodlagos adat mindig jelölt.** Ami egy áttekintés táblázatából/ábrájából származik, az
`secondary_value` (`status: unverified`), és csak az elsődleges közleménnyel való emberi ellenőrzés után
(`secondary_verify` döntés) lehet `verified`. Az ellenőrizetlen másodlagos adat nem kerülhet a
`03_adatok/<kimenet>.csv`-be (D-S05-101, H010).

**N3 — Az ember dönt.** Emberi döntés kell: a forrás-áttekintések kiválasztásához (EP1), minden nem magas
bizonyosságú kinyert jelölthöz (EP2), minden nem azonosító-egyezésen alapuló duplikátum/kapcsolás-javaslathoz
(EP3), a jogosultsághoz (EP4), a végső bevonáshoz (EP5) és a másodlagos adatok ellenőrzéséhez (EP6). A program
automatikusan csak (a) az azonos azonosítójú rekordokat vonja össze (L1-certain, visszavonható döntéssel), és
(b) a Cochrane „References to studies included in this review" szakaszból kinyert tételeket jelöli `confirmed`-nek
(ezek is felülbírálhatók). Tömeges jóváhagyás csak kifejezett emberi művelettel, a szűrőfeltétel rögzítésével
(`decision.batch`) történhet.

**N4 — Szerzői jog.** Tárolható: bibliográfiai tények (cím, szerzők, folyóirat, év, kötet, oldal, azonosítók) és
rövid szó szerinti idézetek (≤ 300 karakter, bizonyítéknak). **Teljes szöveg soha** nem kerül a repóba, a
projektmappába vagy az audit-csomagba: a JATS/PDF szöveget a program memóriában dolgozza fel; opcionális
gyorsítótára a projekten kívül, az operációs rendszer felhasználói cache-mappájában van (`MA_HH_CACHE_DIR`,
alapból `~/.cache/metaelemzes/headhunter/fulltext`, Windows-on `%LOCALAPPDATA%\metaelemzes\headhunter\fulltext`;
lejárat 7 nap). Absztrakt csak a projekt `cache/` mappájában (szűréshez), exportba és teszt-fixture-be nem kerül.
Felhasználói PDF a felhasználó mappájában marad; csak a sha256-ja és az oldalszámos idézetek tárolódnak.

**N5 — Hálózat.** Csak Python stdlib `urllib` (a `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` környezeti változókat a
beépített `ProxyHandler` követi; a CA-t a rendszer/`SSL_CERT_FILE` adja). Forrásonkénti sebességkorlát, újrapróbálás
exponenciális várakozással és véletlen „jitterrel", a `Retry-After` tisztelete, időtúllépés (30 s), User-Agent
(`metaelemzes-headhunter/<verzió> (python-urllib; mailto:<MA_CONTACT_EMAIL>)`, e-mail nélkül a zárójeles rész
`(python-urllib)`). Elérhetetlen forrás esetén a lépés **részlegesen** fut tovább (a forrás állapota
`unreachable`/`rate_limited`, H014), és ezt a kimenet kimondja. `--offline`: csak a gyorsítótárból.

**N6 — Kulcsok.** Csak környezeti változóból (`MA_SCOPUS_APIKEY`, `MA_SCOPUS_INSTTOKEN`, `MA_OPENALEX_APIKEY`,
`MA_NCBI_APIKEY`; e-mail: `MA_CONTACT_EMAIL`). Parancssori kapcsoló kulcsra **nincs** (az argv a tevékenységnaplóba
kerülhet). Scopus: csak fejlécben (`X-ELS-APIKey`, `X-ELS-Insttoken`); OpenAlex: csak fejlécben
(`Authorization: Bearer <kulcs>` — az OpenAlex 429-es válasza maga nevezi meg ezt a módot). Egyetlen kivétel az
NCBI, amely a kulcsot csak `api_key` URL-paraméterként fogadja: ezt a paramétert minden naplózás, hibaüzenet,
gyorsítótár-kulcs és kazetta előtt redaktáljuk. Kulcs, kulcsrészlet, hossz vagy ujjlenyomat nem kerül
állapotfájlba, naplóba, hibaüzenetbe, kazettába; az állapotban csak `key_configured: true/false` áll (H016-őr).

**N7 — Reprodukálhatóság.** Minden keresés a PRISMA-S szerint naplózott (pontos lekérdezés, platform, dátum,
szűrők, találatszám, letöltött szám, teljesség); minden futás `runs/<run_id>/run.json`-t ír (argumentumok kulcsok
nélkül, verziók, forrás-állapotok, időtartam). Az azonosítók determinisztikusak, a kimenetek rendezettek, a lépések
idempotensek (ugyanarra a bemenetre bájtra ugyanazt írják, a `generated` időbélyeg kivételével). A
`studies.json` és a `merged.json` a rekordokból és a döntésnaplóból **újraépíthető** (`rebuild`).

**N8 — Kezdőbarát magyarázat.** Minden állapot, figyelmeztetés és javaslat `{hu, en}` szöveget kap: *mit jelent*
és *mit tegyél*. A CLI magyarul ír (`--lang en` angolul).

**N9 — Adatvédelem.** Betegadat nem érintett. A felhasználó e-mail-címe és kulcsai védettek (N6); a kazetta-
rögzítő a `mailto`/`email`/`tool` paramétert is redaktálja.

**N10 — Párhuzamos fejlesztés.** Csak új fájlok (20. fejezet). Meglévő motor-modult a csomag *importálhat*
(`metaelemzes.prisma`, `metaelemzes.projekt`, `metaelemzes.kb` — csak olvasó használat), de nem módosít.

---

## 2. A folyamat és az emberi ellenőrzőpontok

```
 L0 init + sources --check ─► L1 find-reviews ─► [EP1 áttekintések kiválasztása]
                                                         │
          ┌──────────────────────────────────────────────┘
          ▼
 L3 extract (teljes szöveg / irodalomjegyzék+ágens / saját PDF) ─► [EP2 bizonytalan jelöltek]
          │
          ▼
 L4 resolve (PMID/DOI/PMCID/NCT + metaadat) ─► L5 dedupe (közlemény ↔ vizsgálat) ─► [EP3 javaslatok]
          │
          ├─► L6 overlap (mátrix, CCA)                         (tájékoztató, nem kapu)
          ▼
 L7 screen (saját PICO: cím/absztrakt, teljes szöveg) ─► [EP4 jogosultság + okok]
          │
          ▼
 L8 update-search + cite-search (dátumkorlát) ─► új rekordok ─► L4 ─► L5 ─► L7 (EP3, EP4 újra)
          │
          ▼
 L9 merge + prisma + export ─► [EP5 végső bevonás]  ─►  S05 adatkinyerés ─► [EP6 másodlagos adatok ellenőrzése]
```

| Lépés | Mit csinál | Kimenet | Emberi pont |
|---|---|---|---|
| L0 `init`, `sources --check` | projekt-állapot, PICO és kritériumok, forrás-állapotok | `state.json` | PICO jóváhagyása (`criteria_set` döntés) |
| L1 `find-reviews` | SR/MA keresés a kiválasztott forrásokban, áttekintés-duplumok összevonása, rangsor | `reviews/*.json` (`status: candidate`), `state.searches[]` | — |
| EP1 `select-reviews` | ember választ (ok megadásával) | `review_select` döntések | **kötelező** |
| L3 `extract` | teljes szöveg elérhetősége, keresési dátum, közölt k, bevont-jelöltek bizonyítékkal | `reviews/*.json` → `candidates[]`, `evidence[]` | — |
| EP2 | a `medium`/`low` bizonyosságú, kétértelmű vagy darabszám-eltéréses jelöltek megerősítése | `candidate_confirm/reject` | **kötelező** a nem `high` tételekre |
| L4 `resolve` | azonosítók API-ból, metaadat, regiszter-kapcsolat, visszavonás/erratum | `studies.json` → `records[]` | kétértelmű feloldás → EP3 |
| L5 `dedupe` | L1 egyezések összevonása, L2–L4 javaslatok, vizsgálat-klaszterek | `studies.json` → `studies[]`, `proposals[]` | — |
| EP3 `decide` | duplikátum/kapcsolás javaslatok elfogadása/elutasítása | `duplicate_*`, `study_link/split` | **kötelező** minden nem-`certain` javaslatra |
| L6 `overlap` | hivatkozási mátrix, CCA összesen és páronként | `overlap.json`, `exports/overlap_matrix.csv` | — |
| L7 `screen` | gépi/ágens-javaslat a saját kritériumok szerint; döntésrögzítés | `screen` döntések | **EP4 kötelező** |
| L8 `update-search`, `cite-search` | dátumkorlátos keresés + előre/hátra hivatkozáskövetés; duplumszűrés az ismert halmazzal szemben | `update_search.json`, új `records[]` | ablak jóváhagyása (`update_window`), majd EP3/EP4 |
| L9 `merge`, `prisma`, `export` | egyesített halmaz proveniencával, PRISMA-számok, exportok | `merged.json`, `prisma_flow.json`, `exports/*` | **EP5** `signoff` |
| EP6 `verify-secondary` | másodlagos számok ellenőrzése az elsődleges közleménnyel (S05) | `secondary_verify` döntések | **kötelező** elemzés előtt |

**Lépésállapotok** (`state.steps.<lépés>.status`): `not_started`, `running`, `done`, `needs_human`, `failed`,
`stale`, `skipped`. **Elavulás:** ha egy korábbi lépés kimenete változik (pl. EP1-ben új áttekintést választasz),
minden ráépülő lépés `stale` lesz (függőségek: select → extract → resolve → dedupe → {overlap, screen} → update →
merge → prisma → export). A `status` parancs és a felület ezt mutatja, és a következő javasolt parancsot is.

**Ellenőrzőpontok** (`state.checkpoints[]`): `EP1`–`EP6`, állapot `pending`/`done`/`not_applicable`, a nyitott
tételek száma. Az `export --for-analysis` és a `signoff` megtagadja a futást, amíg a megelőző EP-k nyitottak (H009).

---

## 3. Források

### 3.1 A források és szerepük

| Kulcs | Szolgáltatás | Végpontok (alap-URL) | Hitelesítés | Alap sebességkorlát | Itt (sandbox) |
|---|---|---|---|---|---|
| `pubmed` | NCBI E-utilities | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/` — `esearch.fcgi`, `esummary.fcgi` (JSON), `efetch.fcgi` (XML), `elink.fcgi`, `ecitmatch.cgi`, `einfo.fcgi` | nincs; `tool`/`email` paraméter; opcionális `MA_NCBI_APIKEY` (`api_key`, redaktálva) | 3 kérés/s (kulccsal 10) | működik |
| `europepmc` | Europe PMC REST | `https://www.ebi.ac.uk/europepmc/webservices/rest/` — `search` (`resultType=lite` vagy `core`, `cursorMark`), `{PMCID}/fullTextXML`, `{SRC}/{id}/references`, `{SRC}/{id}/citations`; annotációk: `https://www.ebi.ac.uk/europepmc/annotations_api/annotationsByArticleIds` | nincs | 5 kérés/s (udvarias) | működik |
| `openalex` | OpenAlex | `https://api.openalex.org/works` (szűrés, keresés, `cursor=*`, `per_page` ≤ 200, `select=`), `…/works/pmid:<id>`, `…/works/doi:<doi>`, `…/works/W…` | opcionális `MA_OPENALEX_APIKEY` → `Authorization: Bearer`; `mailto=<MA_CONTACT_EMAIL>` | 5 kérés/s + napi keret | egyedi lekérés működik; listás lekérdezés 429 (keret elfogyott) |
| `scopus` | Elsevier Scopus APIs | `https://api.elsevier.com/content/search/scopus`, `…/content/abstract/eid/{eid}` (`view=REF`, `startref`, `refcount`), `…/abstract/doi/{doi}`, `…/abstract/pubmed_id/{pmid}` | **csak ha** `MA_SCOPUS_APIKEY` van → `X-ELS-APIKey`; opcionális `MA_SCOPUS_INSTTOKEN` → `X-ELS-Insttoken`; `Accept: application/json` | 2 kérés/s (óvatos; a heti kvótát a válaszfejléc mutatja) | elérhető, kulcs nélkül 401 |
| `ctgov` | ClinicalTrials.gov API v2 | `https://clinicaltrials.gov/api/v2/studies` (`query.term`, `query.cond`, `query.intr`, `filter.advanced`, `fields`, `pageSize`, `pageToken`, `countTotal`), `…/studies/{NCT}`, `…/version` | nincs | 3 kérés/s | működik |
| `crossref` | Crossref REST (opcionális, automatikus) | `https://api.crossref.org/works/{doi}`, `…/works?query.bibliographic=` | nincs; `mailto` | 3 kérés/s | itt blokkolt → `unreachable`, kihagyva |

**Megfigyelt viselkedés, amelyre a kód épüljön (2026-10-05, ebből a környezetből kipróbálva):**
- Europe PMC: a `PMID:<szám>` mező **nem megbízható** (más cikket adott vissza); PMID-re mindig
  `EXT_ID:<pmid> AND SRC:MED`, DOI-ra `DOI:"<doi>"` keress. A `fullTextXML` csak nyílt hozzáférésű
  (`isOpenAccess: "Y"`) cikkre ad 200-at; nem nyíltra HTTP 500-at (nem 404-et!) — ez „nincs nyílt teljes szöveg",
  nem hálózati hiba. A `/MED/{pmid}/references` és `/citations` működik (pl. Colditz 1994: 1268 idéző közlemény).
  A szűrők `PUB_TYPE:"systematic-review"`, `PUB_TYPE:"meta-analysis"` (kis- és nagybetűtől függetlenül), `FIRST_PDATE:[a TO b]`, `CREATION_DATE:[a TO b]` működnek.
- PubMed: a `systematic[sb]` szűrő, a `datetype=edat` + `mindate/maxdate`, az `ecitmatch`
  (`jama|1994|271|698|colditz ga|ref1|` → 8309034), az `elink` `pubmed_pubmed_refs` és `pubmed_pubmed_citedin`
  linkjei, valamint az EFetch XML `DataBankList` (regiszterszám) és `ReferenceList` eleme mind működik. Az NCBI
  PMC ID-konverter (`www.ncbi.nlm.nih.gov/pmc/utils/idconv`) itt **blokkolt** — ne erre építs; DOI → PMID:
  `esearch term=<doi>[doi]` (működik).
- `efetch db=pmc` NIH-szerzői kéziratra is ad teljes szöveget (pl. PMC8555740), akkor is, ha a Europe PMC
  `fullTextXML` 500-at ad — ilyenkor a licenc nem nyílt: csak memóriában dolgozható fel, idézet ≤ 300 karakter.
- OpenAlex: kulcs nélkül az egyedi lekérés (`/works/pmid:24581293`) ingyenes (`x-ratelimit-cost-usd: 0`), a
  listás/szűrős lekérdezés kreditbe kerül (`x-ratelimit-cost-required-usd: 0.0001`), az IP-cím napi ingyenes kerete
  `x-ratelimit-limit-usd: 0.1`; kimerülve **429** jön `Retry-After: 49853` (másodperc, éjfél UTC-ig) fejléccel és
  JSON-üzenettel, amely szerint a kulcs ingyenes, és `Authorization: Bearer` fejlécben is küldhető.
- Scopus: kulcs nélkül `401` `x-els-status: AUTHENTICATION_ERROR - Invalid API Key` és
  `{"service-error":{"status":{"statusCode":"AUTHENTICATION_ERROR",…}}}`; érvénytelen kulccsal `401`
  `{"error-response":{"error-code":"APIKEY_INVALID",…}}`. Mindkét hibaalakot kezelni kell. A válasz `Set-Cookie`-t
  is küld — a kazettából törlendő.
- ClinicalTrials.gov: a `referencesModule.references[].type` **megbízhatatlan**. A `query.term=AREA[ReferencePMID]23391465`
  két vizsgálatot ad: az NCT00953927 a *saját* fő eredményközlését (Tameris 2013) `BACKGROUND`-ként sorolja, és
  egy másik vizsgálat (NCT04975178) ugyanezt a PMID-et szintén `BACKGROUND`-ként idézi. Ezért a CT.gov-hivatkozás
  önmagában nem kapcsol közleményt vizsgálathoz (7. és 8. fejezet). A közlemény saját regisztrációs nyilatkozata
  (PubMed `DataBankList`: a 23391465-nél NCT00953927; az absztraktban szereplő szám; Europe PMC annotáció) az erős jel.
  `filter.advanced=AREA[StudyFirstPostDate]RANGE[2023-01-01,MAX]` működik.

### 3.2 Forrás-állapot és ellenőrzés

Állapotok (`state.sources.<kulcs>.status`): `ok`, `not_configured` (Scopus kulcs nélkül), `unreachable` (hálózat/
DNS/proxy/TLS), `rate_limited` (429; `reset_at`), `unauthorized` (401), `forbidden` (403: nincs jogosultság, pl.
nem intézményi IP), `disabled` (a felhasználó kikapcsolta), `unknown` (még nem ellenőrzött).

`sources --check` olcsó próbakérést küld forrásonként: PubMed `einfo` (vagy `esearch term=8309034[pmid]`); Europe PMC
`search query=EXT_ID:8309034 AND SRC:MED`; OpenAlex `GET /works/pmid:8309034?select=id` (ingyenes); Scopus
`search?query=PMID(8309034)&count=1&field=dc:identifier,eid` és ha van EID, `abstract/eid/{eid}?view=REF&refcount=1`
(jogosultság: `entitlement = search_and_ref | search_only | none`); CT.gov `GET /api/v2/version`; Crossref
`GET /works/10.1136/bmj.n71`. Az eredmény a `state.json`-ba és a képernyőre kerül.

| Állapot | Magyar üzenet (mit jelent → mit tegyél) |
|---|---|
| `not_configured` (Scopus) | „A Scopus nincs beállítva: hiányzik az `MA_SCOPUS_APIKEY` környezeti változó. Kulcsot a dev.elsevier.com oldalon kérhetsz (intézményi hálózatról); lásd TELEPITES.md." |
| `unauthorized` | „A szolgáltatás elutasította a kulcsot (401). Ellenőrizd, hogy a kulcs helyes-e és nem járt-e le." |
| `forbidden` | „A kulcs érvényes, de ehhez az adathoz nincs jogosultságod (403). Scopusnál ez általában azt jelenti, hogy nem az intézményi hálózatról futtatod, vagy intézményi token (`MA_SCOPUS_INSTTOKEN`) kell." |
| `rate_limited` | „Elfogyott a lekérdezési keret; visszaáll: <idő>. Addig ezt a forrást kihagyjuk. OpenAlexnél ingyenes kulccsal (`MA_OPENALEX_APIKEY`) saját keretet kapsz." |
| `unreachable` | „A szolgáltatás nem érhető el innen (hálózat, proxy vagy tűzfal). A többi forrással folytatjuk; később `sources --check`." |

### 3.3 Beállítás

`state.json` → `sources.<kulcs>.enabled` (alap: `pubmed`, `europepmc`, `ctgov`, `openalex` bekapcsolva; `scopus`
csak kulccsal, egyébként `not_configured`; `crossref` automatikus). CLI: `sources set <projekt> --enable scopus
--disable openalex` (döntésként naplózva: `source_config`). Lépésenként felülírható: `--sources pubmed,europepmc`.

### 3.4 Melyik lépés melyik forrást használja

| Lépés | PubMed | Europe PMC | OpenAlex | Scopus | CT.gov | Crossref |
|---|---|---|---|---|---|---|
| L1 áttekintések felkutatása | `esearch` (`systematic[sb]`, `meta-analysis[pt]`) | `search` (`PUB_TYPE`) | `works?filter=type:review,…` (kredit!) | `TITLE-ABS-KEY(…) AND DOCTYPE(re)` | — | — |
| L3 teljes szöveg / irodalomjegyzék | `efetch db=pmc`, `ReferenceList`, `elink …_refs` | `fullTextXML`, `/references` | `referenced_works` (egyedi lekérés, ingyenes) | `abstract … view=REF` (jogosultság kell) | — | — |
| L4 feloldás | `ecitmatch`, `esearch [ti]/[au]/[dp]/[doi]`, `esummary`, `efetch` (DataBank, CommentsCorrections) | `search` (EXT_ID, DOI, TITLE+AUTH+PUB_YEAR), annotációk (NCT) | `/works/doi:`, `/works/pmid:` | `DOI()`, `PMID()`, `TITLE()` | `studies/{NCT}`, `AREA[ReferencePMID]` | `works/{doi}` |
| L8 frissítő keresés | `esearch` + `edat` ablak | `search` + `CREATION_DATE` | `from_publication_date` (kredit) | `PUBYEAR >` + betöltési dátum | `filter.advanced` `StudyFirstPostDate` | — |
| L8 hivatkozáskövetés | `elink …_citedin` (csak PMC-ből) | `/citations`, `/references` | `filter=cites:W…` (kredit) | `REFEID(2-s2.0-…)` | — | — |

### 3.5 Kulcskezelés (részletesen)

- **Beolvasás:** egyetlen modul (`metaelemzes/headhunter/secrets.py`) olvassa a környezeti változókat, és
  nyilvántartja a titkos értékeket (`SecretRegistry`). Minden kimenő szöveg (napló, hibaüzenet, progress-sor,
  `run.json`, kazetta, kivétel-szöveg) a `redact()`-on megy át, amely a nyilvántartott értékeket, a tiltott
  fejléceket (`Authorization`, `X-ELS-APIKey`, `X-ELS-Insttoken`, `Cookie`, `Set-Cookie`) és a tiltott
  URL-paramétereket (`api_key`, `apiKey`, `apikey`, `insttoken`, `mailto`, `email`) `«redacted»`-re cseréli.
- **Küldés:** Scopus és OpenAlex kulcs kizárólag fejlécben; NCBI `api_key` URL-ben (NCBI-előírás), redaktálva.
- **Hibaüzenet:** a HTTP-hiba szövegébe sosem kerül a teljes URL, csak a redaktált végpont.
- **Gyorsítótár-kulcs:** a redaktált URL-ből és a nem titkos paraméterekből képzett sha256 — a kulcs nem
  befolyásolja, így nem is szivárog.
- **Állapot:** `key_configured`, `insttoken_configured` (igen/nem), `entitlement`. Semmi más.
- **Teszt:** hamis kulcsokkal (`MA_SCOPUS_APIKEY=TESTKEY-SCOPUS-…`) futó teljes folyamat után minden írt fájlban,
  stdout/stderr-ben és kazettában bájtszintű keresés: a kulcs nem fordulhat elő (H016).
- **Scopus felhasználási feltételek:** csak a szűréshez szükséges bibliográfiai metaadatot tároljuk (cím, szerzők,
  forrás, év, kötet/oldal, DOI, PMID, EID, dokumentumtípus, idézettség); Scopus-absztraktot nem tárolunk, a csak
  Scopusban talált rekord absztraktját a felület élőben kérheti le megjelenítésre, de nem menti; az exportban a
  csak-Scopus rekordok forrása jelölt.

---

## 4. Adatmodell `szk.ma.headhunter/v1`

### 4.1 Mappaszerkezet (a projektmappán belül)

```
<projekt>/01_kereses/headhunter/
  state.json                    szk.ma.headhunter.state/v1        állapot, PICO, kritériumok, források, keresési napló
  reviews/<review_id>.json      szk.ma.headhunter.review/v1       forrás-áttekintés + bevont-jelöltek + bizonyítékok
  studies.json                  szk.ma.headhunter.studies/v1      rekordok, vizsgálat-klaszterek, javaslatok
  decisions.jsonl               szk.ma.headhunter.decision/v1     emberi/ágens döntések (csak hozzáfűzés, hash-lánc)
  update_search.json            szk.ma.headhunter.update-search/v1 frissítő keresés és hivatkozáskövetés
  overlap.json                  szk.ma.headhunter.overlap/v1      hivatkozási mátrix, CCA
  merged.json                   szk.ma.headhunter.merged/v1       az egyesített vizsgálatkészlet
  prisma_flow.json              szk.prisma-flow/v1 (+ "hh" kiegészítés)  → ma.py prisma check
  agent_classification/<review_id>.json   ágens-javaslatok (6.2; importálás előtt)
  exports/                      studies.ma.json (szk.ma.studies/v1), records.ris, screening.csv,
                                masodlagos_adatok.csv, overlap_matrix.csv, kereses_naplo_headhunter.md, report.md
  runs/<run_id>/                run.json, progress.jsonl, CANCEL (együttműködő megszakítás)
  cache/http/                   metaadat-válaszok gyorsítótára (absztrakt igen, teljes szöveg SOHA) — gitignore
  .lock                         írászár
```

### 4.2 Azonosítók

| Mi | Alak | Képzés |
|---|---|---|
| `review_id` | `rv-pmid-31038197`, `rv-doi-<sha1(doi)[:10]>`, `rv-eid-<eid számjegyei>`, `rv-oa-w123…` | az első elérhető: PMID → DOI → EID → OpenAlex W |
| `rec_id` | `rec-pmid-23391465`, `rec-doi-<sha1[:10]>`, `rec-eid-…`, `rec-nct-nct00953927`, `rec-x-<sha1(normalizált hivatkozás)[:10]>` | ugyanígy; a feloldatlan hivatkozás `rec-x-…`; ha később azonosítót kap, a régi rekord `merged_into` az újba |
| `study_id` | `st-0001` | sorszám (`counters.study_seq`), soha nem használjuk újra |
| `cand_id` | `c0001` | áttekintésen belül; globális hivatkozás: `<review_id>#c0001` |
| `evidence_id` | `ev-<review_id>-0001` | áttekintésenként sorszám |
| `decision_id` | `d-20261005T102000Z-0001` | UTC idő + `counters.decision_seq` |
| `proposal_id` | `p-l1-0001`, `p-l2-…`, `p-l3-…` | szabályszint + sorszám; determinisztikus sorrend |
| `search_id` | `s-pubmed-20261005T101200Z` | forrás + futási idő (+ `-2`, ha ütközik) |
| `run_id` | `20261005T101200Z-a1b2c3` | idő + 6 hexa |

Normalizálás: PMID csak számjegy; PMCID `PMC` + számjegy; DOI kisbetűs, `https://doi.org/`/`doi:` előtag nélkül,
záró írásjel nélkül; NCT `NCT\d{8}`; EID `2-s2.0-\d+`; OpenAlex `W\d+`.

### 4.3 Fájlok és sémák

A sémák: `metaelemzes/headhunter/contracts/*.v1.schema.json` (ez a terv része, már létrehozva). Konvenció a motor
`contracts/README.md`-jével azonos: `$id` = `urn:szk:contract:<név>:1`, a dokumentum `schema` mezője
`szk.<név>/v1`, kanonikus formázás (`json.dumps(indent=2, ensure_ascii=False) + "\n"`), csak a
`ma_gui.schema_lite` által ismert kulcsszavak, nincs `/<szó>:<szó>` minta. Közös definíciók:
`ma.headhunter.common.v1` (`ts`, `date`, `actor`, `idval`, `ids`, `bib`, `evidence`, `locator`,
`secondary_value`, `retrieval`, `source_cfg`, `step_status`, azonosító-minták).

| Fájl | Séma | Író | Olvasó |
|---|---|---|---|
| `state.json` | `ma.headhunter.state.v1` | CLI minden lépése | CLI, GUI, ágens |
| `reviews/<id>.json` | `ma.headhunter.review.v1` | `find-reviews`, `extract`, `agent-classify import`, `decide` | `resolve`, `dedupe`, `overlap`, GUI |
| `studies.json` | `ma.headhunter.studies.v1` | `resolve`, `dedupe`, `update-search`, `rebuild` | `screen`, `merge`, GUI |
| `decisions.jsonl` (soronként) | `ma.headhunter.decision.v1` | `decide`, `select-reviews`, `screen`, `signoff`, … | minden lépés (eseményforrás) |
| `update_search.json` | `ma.headhunter.update-search.v1` | `update-search`, `cite-search` | `merge`, `prisma` |
| `overlap.json` | `ma.headhunter.overlap.v1` | `overlap` | GUI, riport |
| `merged.json` | `ma.headhunter.merged.v1` | `merge` | `prisma`, `export`, GUI, ágens |
| kazetták | `ma.headhunter.cassette.v1` | tesztrögzítő | tesztek |

**Bővítési szabály:** új mező additív (a fogyasztó az ismeretlent figyelmen kívül hagyja); kötelező mező törlése
vagy jelentésváltozása új főverzió (`…v2.schema.json`). A build-ágens a sémát csak additívan bővítheti, és a
változást ebben a tervben is rögzíti.

### 4.4 Példák

`reviews/rv-pmid-31038197.json` (rövidítve; a Kashangura 2019 Cochrane-áttekintés valódi szerkezete alapján):

```json
{ "schema": "szk.ma.headhunter.review/v1", "model": "szk.ma.headhunter/v1",
  "review_id": "rv-pmid-31038197", "status": "selected",
  "ids": { "pmid":  {"value": "31038197",   "source": "pubmed",    "via": "pubmed.esearch",   "at": "2026-10-05T10:12:00Z"},
           "pmcid": {"value": "PMC6488980", "source": "europepmc", "via": "europepmc.search", "at": "2026-10-05T10:12:01Z"} },
  "bib": {"title": "MVA85A vaccine to enhance BCG for preventing tuberculosis.", "first_author": "Kashangura R",
          "year": 2019, "journal": "Cochrane Database Syst Rev"},
  "is_cochrane": true, "cochrane": {"cd_number": "CD012915", "version": null},
  "found_by": ["s-pubmed-20261005T101200Z"],
  "fulltext": {"available": true, "route": "europepmc_oa", "license": "cc by-nc", "checked_at": "2026-10-05T10:13:00Z"},
  "candidates": [ {
      "cand_id": "c0001",
      "cited_as": {"text": "Tameris MD, Hatherill M, Landry BS, et al. Safety and efficacy of MVA85A … Lancet 2013;381:1021-8.",
                   "first_author": "Tameris MD", "year": 2013},
      "study_label_in_review": "Tameris 2013", "group_key": "CD012915-bbs2-0006",
      "ids": {"pmid": {"value": "23391465", "source": "review", "via": "jats.pub-id", "at": "2026-10-05T10:13:02Z",
                       "confirmed_by": "pubmed.esummary"}},
      "rec_id": "rec-pmid-23391465", "role_in_review": "included",
      "evidence_ids": ["ev-rv-pmid-31038197-0001"], "confidence": "high", "status": "confirmed",
      "secondary_data": [], "decision_ids": [] } ],
  "evidence": [ {
      "evidence_id": "ev-rv-pmid-31038197-0001", "review_id": "rv-pmid-31038197",
      "kind": "reference_section", "strategy": "jats_cochrane_included",
      "locator": {"container": "PMC6488980", "element_id": "CD012915-bbs2-0006",
                  "section": "References to studies included in this review"},
      "quote": "Tameris 2013 {published data only}", "extracted_by": "tool:headhunter",
      "at": "2026-10-05T10:13:02Z", "confidence": "high" } ] }
```

Egy döntés-sor (`decisions.jsonl`):

```json
{"schema": "szk.ma.headhunter.decision/v1", "decision_id": "d-20261005T102000Z-0001", "ts": "2026-10-05T10:20:00Z",
 "actor": "user:SzK", "kind": "duplicate_accept", "target": {"type": "proposal", "id": "p-l2-0003"}, "value": "accept",
 "level": null, "reason_code": null, "reason": "Ugyanaz a közlemény: a cím, az első szerző és az év egyezik.",
 "evidence_ids": [], "proposed_by": "tool:headhunter", "kb_refs": ["D-S04-101"],
 "prev_sha256": "0000000000000000000000000000000000000000000000000000000000000000", "sha256": "…64 hexa…"}
```

### 4.5 Invariánsok (gépileg ellenőrizve, 15. fejezet)

1. Minden fájl átmegy a sémáján (H001).
2. Minden `included*` szerepű jelöltnek van létező bizonyítéka, és a bizonyíték `quote`-ja ≤ 300 karakter (H002).
3. Minden végső halmazba kerülő rekord legalább egy azonosítója API-forrású vagy API-val megerősített (H003, H005).
4. Egy rekord legfeljebb egy vizsgálathoz tartozik, és minden aktív rekord tartozik vizsgálathoz a `dedupe` után (H020).
5. A `decisions.jsonl` hash-lánca ép (H015): `sha256` = a sor kanonikus JSON-jának (az `sha256` mező nélkül,
   `sort_keys=True`, `ensure_ascii=False`, elválasztók `(",", ":")`) sha256-ja; `prev_sha256` = az előző sor
   `sha256`-ja (az elsőnél 64 nulla).
6. A `merged.json` `final: true` csak EP5 után; ekkor nincs `pending` javaslat és nyitott EP (H009).
7. Teljes szöveg nincs a headhunter-mappában (H019: tilos JATS `<body>`/`<sec>` töredék, és bármely szövegmező
   > 2000 karakter a `reason` kivételével).

### 4.6 Írás és párhuzamosság

Atomikus írás (ideiglenes fájl ugyanabban a mappában + `os.replace`); írászár: `.lock` (`O_CREAT|O_EXCL`, PID +
időbélyeg, 10 perc után elavultnak tekinthető, a CLI ezt kiírja). A `decisions.jsonl` csak hozzáfűzéssel bővül, zár
alatt. A felület ETag-je a fájl bájtjainak sha256-ja; módosítás csak `If-Match`-csel (17. fejezet).

**Eseményforrás:** a duplikátum- és kapcsolás-döntések a `decisions.jsonl`-ban élnek; a `studies.json` klaszterei
determinisztikusan újraszámolhatók a rekordokból + döntésekből (`rebuild`). Visszavonás: új döntés `supersedes`-zel.

---

## 5. Forrás-áttekintések felkutatása (L1) és kiválasztása (EP1)

### 5.1 Lekérdezés a PICO-ból

A `state.pico.query_blocks` fogalomblokkokat tartalmaz (P, I, [C], [O], [S]; szabadszavas tagok és MeSH). A
lekérdezést a program építi, a felhasználó (vagy a `ma-tervezo`) szerkesztheti (`--query-file`). Alapesetben **P ÉS
I** blokk (a kimenet-blokk szűkíthet, de a bányászatnál inkább ne). Az SR/MA-szűrő forrásonként:

| Forrás | Szűrő |
|---|---|
| PubMed | `(<P>) AND (<I>) AND (systematic[sb] OR meta-analysis[pt] OR "meta-analysis"[ti] OR "systematic review"[ti])` |
| Europe PMC | `(<P>) AND (<I>) AND (PUB_TYPE:"systematic-review" OR PUB_TYPE:"meta-analysis" OR TITLE:"meta-analysis" OR TITLE:"systematic review")` |
| OpenAlex | `search=<P és I kulcsszavai>&filter=type:review` (+ `from_publication_date`); a `type:review` narratív áttekintést is ad → a rangsor lejjebb sorolja, ha a cím nem SR/MA |
| Scopus | `TITLE-ABS-KEY(<P>) AND TITLE-ABS-KEY(<I>) AND (TITLE-ABS-KEY("meta-analysis") OR TITLE-ABS-KEY("systematic review")) AND DOCTYPE(re)` (+ `AND PUBYEAR > <év>`) |

Minden futás `state.searches[]` sor (`purpose: review_discovery`), pontos lekérdezéssel. Felső korlát:
`--max` (alap 200 áttekintés-jelölt forrásonként).

### 5.2 Áttekintés-duplumok és Cochrane-változatok

Azonos azonosító (PMID/DOI/PMCID/EID/OpenAlex) → egy áttekintés, a `found_by` egyesítve. Cochrane-áttekintés:
folyóirat `Cochrane Database Syst Rev` vagy DOI-előtag `10.1002/14651858`; a CD-szám a DOI-ból (`CD\d{6}`), a
változat a `.pubN` utótagból. Több változatnál a legfrissebb `candidate`, a régebbiek `superseded` (`superseded_by`);
a PubMed `CommentsCorrections` (`UpdateOf`/`UpdateIn`) és a Cochrane „References to other published versions of this
review" szakasz is erre utal. A Cochrane-áttekintés előnyt kap a rangsorban (5.3), mert a bevont vizsgálatok listája
szabványos szakaszban, vizsgálatonként csoportosítva szerepel (6.1 a1).

### 5.3 Rangsor (csak sorrend, nem minőségítélet)

`score = Σ súly × komponens` (minden komponens 0–1, a súlyok a `settings`-ben; a felület komponensenként
megmutatja): relevancia (a P és I kifejezések a címben), frissesség (megjelenés éve, ha ismert a keresési dátum),
méret (közölt k), metaanalízis-e, Cochrane-e, elérhető-e nyílt teljes szöveg (kinyerhetőség), és AMSTAR 2-höz
kapcsolódó **jelzések** (regisztrált protokoll — `CRD\d{11}` vagy PROSPERO/OSF említés; legalább két megnevezett
adatbázis; torzításikockázat-értékelés említése; PRISMA említése). Visszavont áttekintés (`Retracted Publication`
publikációtípus vagy `RetractionIn`) piros jelölést és „kizárás" javaslatot kap. A jelzések bizonyítékkal
(`signals.evidence_ids`) tárolódnak, ha szövegből származnak.

### 5.4 EP1 — kiválasztás

Ember dönt (`select-reviews --include … --exclude … --reason …`; okszótár: „más PICO", „újabb változata van",
„nem szisztematikus", „visszavont", „nem hozzáférhető", „egyéb"). Útmutatás a felületen és az ágensnek:

- Bányászathoz **minden** PICO-ba illő SR/MA-t érdemes kiválasztani, nem csak a „legjobbat": az átfedést a
  duplumszűrés kezeli, a kihagyott áttekintés viszont vizsgálatokat veszíthet.
- Az áttekintések közti választás és a minőség kérdésében segít Pollock 2019 döntési eszköze és Ballard &
  Montgomery 2017 négy feltétele (nem lényegesen átfedő vizsgálatok, illeszkedő terjedelem, jó módszertani minőség,
  naprakészség) — utóbbiak az overview-k eredményszintézisére vonatkoznak; a bányászatnál a minőség főleg a
  **másodlagos adatok** megbízhatóságát érinti (N2).
- Ha a felhasználó overview-t (áttekintések áttekintését) is közöl, az AMSTAR 2 értékelést a meglévő
  ellenőrzőlistával végezze, és a PRIOR (2022) jelentési útmutatót kövesse.

---

## 6. A bevont vizsgálatok kinyerése (L3)

### 6.0 Beszerzés és alapadatok

1. **Nyílt teljes szöveg?** Europe PMC `search` (`resultType=core`, `EXT_ID:<pmid> AND SRC:MED`): `isOpenAccess`,
   `inEPMC`, `fullTextIdList`, `license`. Ha `isOpenAccess = Y` → `GET {PMCID}/fullTextXML`. Ha nem, de van PMCID →
   `efetch db=pmc id=<szám>` (szerzői kézirat is lehet; licenc nélkül csak memóriában, rövid idézettel). Egyébként
   → (b) irodalomjegyzék-utak vagy (c) saját PDF. A `fulltext` mező rögzíti az utat és a licencet.
2. **Keresési dátum** (`search_date`): mintaillesztés a Methods/Abstract szövegben (EN): „searched … (from
   inception) (up) to/until/through <dátum>", „last search(ed) … (on|in) <dátum>", „date of (the last) search",
   „searches were (conducted|run|updated) (on|in|until) <dátum>"; Cochrane: „Date of search". A találat idézetként
   bizonyíték, pontosság `day|month|year`. Ha nincs: **tartalék = megjelenés dátuma − 12 hónap** (`fallback: true`,
   H008) — a korábbi dátum szélesebb frissítési ablakot ad, tehát érzékenyebb; emberi jóváhagyás kell (12.1).
3. **Közölt k** (`k_reported`): „(\d+|szám szóval) (randomi[sz]ed )?(controlled )?(trials|studies|RCTs) (were|was)
   included", „included (\d+) (studies|trials)"; bizonyítékkal. Ha több szám van (pl. „24 trials in 26 reports"),
   mindkettő rögzül (`unit`).

### 6.1 (a) Strukturált teljes szöveg (JATS) — legmegbízhatóbb

A hivatkozás-azonosítókat a JATS-ben **két** helyen kell keresni: `<pub-id pub-id-type="pmid|doi|pmcid">` és a
Europe PMC dúsításaként `<ext-link ext-link-type="pmid|doi|pmcid" xlink:href="…">` (a PMC4122754 és a PMC12070792
így kódolja). Az így kapott azonosító `source: review`, `via: jats.pub-id` / `jats.ext-link`, és L4-ben API-val
megerősítendő.

**a1 — Cochrane-szakaszok (bizonyosság: `high`).** A `<ref-list>` címe „References to studies included in this
review" → a beágyazott al-`ref-list`-ek címe a vizsgálat Cochrane-azonosítója (pl. „Tameris 2013 {published data
only}"), az al-lista `ref`-jei a vizsgálat közleményei (→ `group_key` = az al-lista `id`-ja, pl.
`CD012915-bbs2-0006`). „References to studies excluded from this review" → `excluded_by_review[]`; „…awaiting
classification" → `awaiting`; „…ongoing studies" → `ongoing`; „Additional references" és „References to other
published versions of this review" → nem jelölt. Idézet: az al-lista címe (+ legfeljebb 200 karakter a
hivatkozásból). A „Characteristics of included studies [ordered by study ID]" szakasz táblái
(vizsgálatonként egy tábla, a címkéjük a vizsgálat-ID) megerősítő bizonyítékok.

**a2 — A bevont vizsgálatok táblázata (`medium`, xref-fel `high`).** Táblázat-felismerés (felirat/címke/első oszlop
fejléce, kis-nagybetű és ékezet nélkül): „characteristics of (the )?included (studies|trials)", „(studies|trials)
included", „study characteristics", „included studies"; első oszlop: `study`, `author`, `author (year)`, `first
author`, `reference`, `trial`, `study id`, `code`. Soronként:
- ha a sorban `<xref ref-type="bibr" rid="…">` van → a hivatkozás közvetlenül megvan (`high`);
- különben a cella szövegéből szerző + év (`Adetifa 2010`, `Acharjee, S. 2015`; a `<p>` darabok szóközzel
  összefűzve; sorszám-előtag eldobva) → illesztés az irodalomjegyzékre **csak első szerző + év** szerint
  (egyedi találat → `medium`; több → `low` és EP2; nincs → `low`, feloldás L4-ben címből, ha a táblázat ad címet).
  *Csapda (valós):* a PMC4122754 „Adetifa 2010" sora: az „Adetifa IM" a ref26 első szerzője, de a ref24-ben
  társszerző — ezért kizárólag első szerzőre illesztünk;
- ugyanannak a vizsgálatnak több sora („2.1", „2.2", `rowspan`, ismétlődő szerző + év + ország) **egy** jelölt, a
  lokátor a sorok listája (PMC12070792: „Anderson, J. W. 2007" két sora).
Idézet: az első cella szövege (+ ha belefér, a második cella eleje).

**a3 — Elemzési/forest-táblák (`medium`; másodlagos adat forrása).** Cochrane „Comparison N / Analysis N.M" és a
hasonló adattáblák soraiból: a vizsgálat neve megerősíti a bevonást; az egyértelmű fejlécű numerikus cellák
(`Events`, `Total`, `Mean`, `SD`, `N`) `secondary_value`-ként rögzülnek lokátorral, `status: unverified`.
Kétértelmű fejlécnél **nem** rögzítünk számot (nem találgatunk).

**a4 — Szöveges állítás (`medium`, csak megerősítésre).** „We included N studies [12–24]" típusú mondat xref-
tartománnyal: a darabszámot és a hivatkozás-tartományt megerősítésként használjuk; önmagában jelöltet csak akkor
ad, ha nincs a1/a2.

### 6.2 (b) Irodalomjegyzék + ágens-osztályozás (`low`)

Ha nincs strukturált bevont-lista, a hivatkozások forrásai (prioritás szerint): a JATS `ref-list`; Europe PMC
`/MED/{pmid}/references`; PubMed EFetch `ReferenceList` (`ArticleIdList`-tel); `elink pubmed_pubmed_refs`;
OpenAlex `referenced_works` (egyedi lekérés, ingyenes); Scopus `view=REF` (ha jogosult). Ezek **jelölt-hivatkozások**
(`role_in_review: unknown`, `low`) — nem bevont-vizsgálat állítások.

**Ágens-protokoll:** a `ma-metaheadhunter` ágens a `show-text <projekt> --review <id> --part methods,results,tables`
paranccsal kiírt szöveget olvassa (a szöveg nem mentődik), és `agent_classification/<review_id>.json`-t ír:

```json
{ "review_id": "rv-pmid-…", "agent": "agent:ma-metaheadhunter", "created": "…Z",
  "items": [ { "ref_key": "ref12", "role": "included",
               "quote": "Twelve RCTs met the inclusion criteria (12-23)", "locator": {"section": "Results", "page": null} } ] }
```

Az `agent-classify import` ellenőrzi: (1) az idézet szóközre normalizálva **szó szerint** megtalálható a forrásszövegben
(különben elutasítva, H004); (2) azonosítót nem vesz át; (3) minden tétel `confidence: low`, `status: proposed` —
EP2-ben emberi megerősítés kell (tömegesen is, a felület egy-egy áttekintésre).

### 6.3 (c) A felhasználó PDF-je

`extract <projekt> --review <id> --pdf <út>`: szöveg a `metaelemzes.kb` stdlib PDF-kinyerőjével (csak olvasó
import), oldalszámmal, memóriában; az a2/a4 szabályok szövegre alkalmazva + szükség esetén ágens-osztályozás. A
bizonyíték lokátora `page` + idézet; a PDF-ből csak `user_pdf_sha256` tárolódik. A PDF nem kerül a projektbe.

### 6.4 Teljességi ellenőrzés és EP2

A kinyert különálló vizsgálat-csoportok száma vs `k_reported` → eltérésnél H006 (figyelmeztetés + EP2 tétel).
EP2-be kerül: minden `medium`/`low` jelölt, minden kétértelmű illesztés, a H006-os áttekintések, és az
`unknown` szerepű tételek. Döntés: `candidate_confirm` / `candidate_reject` (indoklással).

---

## 7. Feloldás (L4): azonosítók és metaadatok

**Sorrend jelöltenként** (az első elfogadott eredménynél megáll):

1. Az áttekintés saját azonosítója (`review` forrás) → megerősítés: PMID → `esummary`; DOI → `esearch <doi>[doi]`
   vagy Europe PMC `DOI:"…"`; PMCID → Europe PMC. Elfogadás: a visszakapott cím hasonlósága a hivatkozás szövegében
   lévő címhez ≥ 0,80, vagy (ha a hivatkozásban nincs cím) első szerző + év egyezik.
2. `ecitmatch` (`folyóirat|év|kötet|első oldal|szerző|kulcs|`) — csak egyetlen PMID-találat, utána `esummary`-
   címellenőrzés.
3. `esearch`: `"<cím>"[ti]` (pontos kifejezés); ha 0 → `<címszavak>[ti] AND <vezetéknév>[au] AND <év>[dp]`;
   elfogadás: egyetlen találat és cím-hasonlóság ≥ 0,90.
4. Europe PMC: `TITLE:"…" AND AUTH:"…" AND PUB_YEAR:…` (MEDLINE-on kívüli, preprint).
5. OpenAlex: `/works?filter=title.search:…,publication_year:…` (kredit!) — csak ha engedélyezett.
6. Scopus: `TITLE("…") AND AUTHLASTNAME(…) AND PUBYEAR = …` — csak ha beállított.
7. Crossref `query.bibliographic` — csak ha elérhető.

Több jó találat vagy küszöb alatti hasonlóság → `resolution` javaslat (`proposals[]`, `kind: resolution`), EP3.
Sikertelen → `rec-x-…` rekord `resolution.status: unresolved` (a felhasználó beírhat azonosítót → `user` forrás →
API-megerősítés). **Ágens és felhasználó által beírt azonosító is csak API-megerősítéssel számít feloldottnak.**

**Metaadat:** `esummary` (cím, szerzők, folyóirat, dátum, kötet, szám, oldal, `articleids` → DOI/PMCID,
`pubtype`); `efetch` XML: `DataBankList` (regiszterszám), `CommentsCorrections` (`RetractionIn`, `ErratumIn`,
`UpdateIn`, `CommentIn`), `PublicationType` („Retracted Publication", „Published Erratum", „Clinical Trial
Protocol"); Europe PMC `core`: `pubTypeList`, `isOpenAccess`, `license`; annotációk (`type=Accession Numbers`,
`subType=NCT`; a PMID 23391465 absztraktjából így jön az NCT00953927).

**Regiszter-kapcsolat** (`ids.nct`, `ids.registry[]`): források: PubMed `DataBankList`; absztrakt-minták (`NCT\d{8}`,
`ISRCTN\d{8}`, `ACTRN\d{14}`, `ChiCTR[-A-Za-z0-9]+`, `\d{4}-\d{6}-\d{2}` (EudraCT), `IRCT\d+N\d+`,
`CTRI/\d{4}/\d{2,3}/\d{6}`, `DRKS\d{8}`, `PACTR\d{15}`, `KCT\d{7}`, `UMIN\d{9}`, `NTR\d+`, `NL\d{4,}`); Europe PMC
annotációk; CT.gov `referencesModule`. **Erősség:** *erős* = a közlemény maga nevezi meg a regisztrációt
(`DataBankList`, absztrakt-szöveg, Europe PMC annotáció); *megerősítő* = CT.gov `RESULT`/`DERIVED` hivatkozás;
*gyenge* = csak CT.gov `BACKGROUND` hivatkozás (ez a típus a gyakorlatban a saját eredményközlést is jelölheti, és
idegen vizsgálat is így idézhet — 3.1) → önmagában nem kapcsol, csak tipp.

---

## 8. Duplikátumok és vizsgálat-kapcsolás (L5, EP3)

### 8.1 Szintek

| Szint | Mit jelent | Szabály | Bizonyosság | Teendő |
|---|---|---|---|---|
| **L1** azonos közlemény | ugyanaz a cikk több áttekintésből/forrásból | közös PMID, DOI, PMCID, EID vagy azonos rekordra mutató OpenAlex W | `certain` | automatikus összevonás (`auto_applied`), visszavonható |
| L1-ütközés | ellentmondó azonosítók | pl. azonos DOI két különböző PMID-del | — | `id_conflict` javaslat, H007, ember |
| **L2** valószínűleg azonos közlemény | nincs közös azonosító | `probable`: cím-hasonlóság ≥ 0,90 ÉS első szerző egyezik ÉS évkülönbség ≤ 1; `possible`: cím ≥ 0,80 ÉS (első szerző VAGY év ≤ 1) | `probable`/`possible` | javaslat, EP3 |
| **L3** azonos vizsgálat, más közlemény | társközlemény | közös *erős* (vagy erős + megerősítő) regiszter-kapcsolat (7. fejezet: DataBank, absztrakt, Europe PMC annotáció; CT.gov `RESULT`/`DERIVED`); a csak `BACKGROUND` alapú egyezés legfeljebb L4; vagy az áttekintés csoportosítása (Cochrane al-lista ≥ 2 közleménnyel; „2.1/2.2" sorok) | `probable` | `same_study` javaslat, EP3 (áttekintésenként tömegesen jóváhagyható) |
| **L4** lehetséges azonos vizsgálat | regiszterszám nélkül | azonos első/utolsó szerző + átfedő toborzási időszak + azonos beavatkozás + hasonló N (±10%), vagy két áttekintés eltérően csoportosít | `possible` | csak tipp-lista, EP3 |

**Hasonlóság:** cím normalizálása: NFKD, ékezet le; átírás `ø→o, æ→ae, œ→oe, ß→ss, đ→d, ł→l, ı→i`; kisbetű;
nem alfanumerikus → szóköz; szóközök összevonása; tiltólista (`a, an, the, of, in, on, and, for, with, to, versus,
vs`). `title_sim = max(difflib.SequenceMatcher.ratio, token-Jaccard)`. Első szerző: a normalizált vezetéknév
(kötőjel/szóköz nélkül) egyezése. Folyóirat: ISSN vagy normalizált rövidítés. `score` (0–1, csak rendezéshez és
megjelenítéshez) = `title_sim` + 0,03 (ha a folyóirat egyezik) − 0,05 × évkülönbség, a [0, 1] tartományra vágva.
Kétség esetén **megtartás** (Bramer 2016; McKeown 2021: a hamis összevonás bevont vizsgálatot veszíthet).

### 8.2 Különleges rekordok

- **Visszavont közlemény** (publikációtípus vagy `RetractionIn`): jelölés + kizárási javaslat („visszavont
  közlemény"), H013, ha a bevont halmazban marad.
- **Erratum**: nem önálló közlemény; `related: erratum_in` a szülőhöz.
- **Protokoll-cikk** → szerep `protocol`; **konferencia-absztrakt** → `abstract`; **regiszter-rekord** (CT.gov) →
  `registry_result` (a PRISMA-ban regiszterből azonosított rekord; jelentésként csak akkor számít, ha bevonod).
- **Rejtett kettős közlés** (von Elm 2004 mintázatai: azonos minta azonos/más kimenettel, növekvő/csökkenő minta):
  csak L4-tipp; a figyelem indoka Tramèr 1997 (a duplikált adat beemelése túlbecsléshez vezetett).

### 8.3 Vizsgálat-címke és szerepek

Címke: az elsődleges közlemény „Elsőszerző Év"-e (ütközésnél `a`, `b` utótag). Szerepek: `primary` (fő eredmény),
`secondary`, `companion`, `protocol`, `abstract`, `registry_result`, `erratum`, `unknown`; forrásuk:
`review_grouping`, `registry_link`, `human`, `rule`. Az elemzési egység a vizsgálat (D-S04-002, D-S05-020).

### 8.4 Emberi megerősítés

| Bizonyosság | Teendő |
|---|---|
| `certain` (L1) | automatikus, a felület listázza, egy kattintással visszavonható |
| `probable` | EP3; a felület egymás mellett mutatja a két rekordot (cím, szerzők, folyóirat, év, azonosítók, eredet) és a jellemzőket; tömeges jóváhagyás szűrővel (pl. „score ≥ 0,97 és azonos folyóirat"), a szűrő a döntésben (`batch`) |
| `possible` | EP3; egyenként |
| `id_conflict` | EP3; egyenként, a forrás-API-k válaszával |

---

## 9. Átfedés (L6): hivatkozási mátrix és CCA

**Mátrix:** sorok = egyedi vizsgálatok (vagy közlemények), oszlopok = kiválasztott forrás-áttekintések; cella =
bevonta-e. Az első előfordulás az „index" (a mátrixban `index_review`).

**Corrected Covered Area** (Pieper 2014; a képletet Hennessy & Johnson 2020 és Ying 2025 is így közli):

```
CCA = (N − r) / (r·c − r)            (százalékban: × 100)
N = a bejelölt cellák száma (duplikációval együtt), r = sorok (egyedi vizsgálatok/közlemények), c = oszlopok (áttekintések)
```

- `c < 2` vagy `r = 0` → `cca_pct: null` (nem értelmezhető).
- **Páronként** (`pairs[]`, `c = 2`): `CCA = (N − r) / r`.
- **Két szinten** számolunk: `report` (Pieper eredeti, közlemény-szintű) és `study` (társközlemények összevonva); a
  kimenet a szintet kiírja. Ha a felhasználó kimenetenként is kéri (`--scope <kimenet>`), csak az adott kimenetet
  jelentő vizsgálatokkal.
- **Sávok** (útmutató, nem merev szabály; Ying 2025 1. keretes táblája, amely Pieper 2014-re hivatkozik):
  0–5%: enyhe (`slight`); 6–10%: mérsékelt (`moderate`); 11–15%: magas (`high`); > 15%: nagyon magas
  (`very_high`). Megvalósítás: egy tizedesre kerekítve ≤ 5,0 → slight; ≤ 10,0 → moderate; ≤ 15,0 → high; egyébként
  very_high.
- **Kidolgozott példa (teszt):** 3 áttekintés, r = 10 egyedi vizsgálat, N = 16 bejelölés → CCA = (16 − 10) / (30 −
  10) = 30% → nagyon magas.
- **wCCA** (Ying 2025: a vizsgálatok súlya a mintaelemszám négyzetgyöke) csak tájékoztató és csak ellenőrzött
  (`verified`) mintaelemszámokkal; egyébként `null`.

**Értelmezés (kezdőknek, a felületen és a riportban):** bányászatnál az átfedés nem torzítás (a duplumokat
összevonjuk), hanem azt mutatja, mennyire ugyanazt az irodalmat találták a korábbi áttekintések. Hennessy & Johnson
2020 öt lépését követjük: összesített és páronkénti CCA; a nagyon magas páronkénti átfedésnél (pl. ugyanazon
szerzők frissítése) a régebbi áttekintés helyettesíthető; alacsony átfedés azonos PICO mellett eltérő
kritériumokra vagy keresési hiányra utal → a frissítő és a saját keresés jelentősége nő.

---

## 10. Jogosultsági szűrés a saját PICO szerint (L7, EP4)

- **Egységek:** rekord (cím/absztrakt szint) és jelentés (teljes szöveg szint), a PRISMA 2020 szerint.
- **Mit szűrünk:** minden egyedi rekordot (az áttekintésekből és a frissítő keresésből). Az, hogy egy korábbi
  áttekintés bevonta, **kontextus**, nem jogosultság — a te PICO-d eltérhet.
- **Javaslatok:** (1) gépi, a kritériumok `machine_hint`-jei alapján (publikációtípus, év, nyelv, ember/állat —
  pl. „Review", „Editorial" kizárási javaslat); (2) ágens-javaslat idézettel az absztraktból (élőben lekérve, a
  projekt `cache/`-ében tárolva; idézet ≤ 300). Mindkettő `proposed_by`-jal jelölt javaslat, a döntést ember hozza.
- **Okszótár:** `state.exclusion_reasons` (`X1…`, a kritériumokhoz kötve; PRISMA 16a). Teljes szöveg szinten az ok
  kötelező.
- **Kettős szűrés:** `settings.reviewers`; bírálónként külön döntés; ütközés-lista; feloldó döntés (`supersedes`);
  a riport egyezési százalékot és Cohen-kappát közöl (stdlib).
- **Teljes szöveg beszerzése:** állapot jelentésenként (`nyílt`, `felhasználó adta`, `nem elérhető` → PRISMA F).
- **Csere eszközökkel:** `exports/records.ris` (Rayyan/Covidence), visszatöltés `screen import <csv>`
  (`rec_id;level;decision;reason_code;actor`; az `actor` kötelező, `user:` előtaggal).

---

## 11. Egyesítés (L9) és a másodlagos adatok

- **Egy rekord vizsgálatonként** a `merged.json`-ban: közlemények szereppel, regiszterszámok, **proveniencia**
  (mely áttekintések vonták be, milyen címkével, mely bizonyítékok alapján), `found_by` (`previous_reviews`,
  `citation_search`, `update_search`, `manual`), jogosultsági döntések, végső döntés.
- **Másodlagos adatok:** áttekintésenként külön (`secondary_data[]`), soha nem átlagoljuk. Ha két áttekintés ugyanarra
  a mezőre eltérő értéket közöl (pontos eltérés, vagy `settings.secondary_tolerance_rel` felett) → `conflicts[]`,
  H018. Miért kell ellenőrizni: SMD-metaanalízisek 37%-ában legalább egy vizsgálatnál ≥ 0,1 eltérés volt
  (Gøtzsche 2007); 34 Cochrane-áttekintésből 20-ban volt kinyerési hiba (Jones 2005); a kinyerési hibák aránya
  akár 50% (Mathes 2017).
- **EP5 (`signoff`):** a végső bevonás emberi lezárása; feltétel: nincs `pending` javaslat, nincs nyitott EP1–EP4,
  a PRISMA-ellenőrzés hibátlan (H009, H011). Ezután `merged.final = true`, és tükör-döntés kerül a projektnaplóba
  (`metaelemzes.projekt.log_decision`, `agent="planner"` amíg a `headhunter` ágensnév nincs bekötve, `actor` = a
  döntő ember, `kb_refs` = a vonatkozó HH-szabályok).
- **Exportok:**
  - `exports/studies.ma.json` (`szk.ma.studies/v1`: `study_id`, `label`, `registration`, `reports[{rec_id, role}]`);
    `export --to-project` a `03_adatok/studies.json`-ba csak akkor ír, ha az nem létezik; különben eltérés-
    összefoglalót ad, felülírás csak `--force`-szal és naplózott döntéssel.
  - `exports/masodlagos_adatok.csv`: a `tudasbazis/sablonok/adatkinyero_sablon.csv` oszlopai (`study;study_id;year;
    subgroup;design;rob;estimated;m1;sd1;n1;m2;sd2;n2;e1;e2;egyseg;meroeszkoz;idopont;elemzesi_populacio;forras_oldal;
    megjegyzes`) + `adat_forras` (mindig `masodlagos`), `forras_attekintes`, `forras_lokator`, `ellenorizve`
    (`nem`/`igen`). Ez **ellenőrzési munkalista**, nem elemzési tábla; a `03_adatok/<kimenet>.csv`-be a program soha
    nem ír másodlagos számot.
  - `exports/records.ris`, `exports/screening.csv`, `exports/overlap_matrix.csv`,
    `exports/kereses_naplo_headhunter.md` (a `kereses_naplo.md` sablon oszlopaival, PRISMA-S), `exports/report.md`
    (magyar összefoglaló: módszer, források, számok, korlátok, hivatkozások).
- **EP6 (`verify-secondary`):** S05-ben a felhasználó az elsődleges közleményt megnyitva tételenként `verified` vagy
  `discrepant` (az elsődleges lokátorral). `export --for-analysis` csak `verified` értéket ad ki (H010).

---

## 12. Frissítő keresés és hivatkozáskövetés (L8)

### 12.1 Ablak

- Kiválasztott áttekintésenként a keresési dátum (6.0, bizonyítékkal vagy tartalékkal).
- **Horgony** (`anchor`): alapból `latest` (a legfrissebb forrás-keresés); a program mellé írja a legkorábbit is, és
  figyelmeztet, ha a kettő > 24 hónapra esik egymástól (az idősebb áttekintés PICO-ja által lefedett időszak és a
  többieké eltér) — ilyenkor az `earliest` horgony érzékenyebb. `manual` is választható.
- **Átfedési ablak:** alapból 6 hónap visszafelé a horgonytól (`overlap_window_months`), az indexelési késés miatt.
  Ez **pragmatikus alapérték, nem irodalmi szabály** — a felhasználó dönti el (`update_window` döntés, EP).
- Indoklás a felületen: Garner 2016 (mikor és hogyan frissíts — döntési keret és ellenőrzőlista); Shojania 2007
  (100 áttekintésnél a frissítési jelzésig eltelt idő mediánja 5,5 év, de 23%-nál 2 éven belül jelentkezett);
  D-S03-018 (beadás előtt 12 hónapon belül újrafuttatás).

### 12.2 Lekérdezések

PICO-blokkokból, **SR-szűrő nélkül**; vizsgálattípus-szűrő csak akkor, ha a PICO korlátozza és a felhasználó (vagy a
`ma-tervezo`) megadja. Ha a forrás-áttekintések keresési stratégiája elérhető, a felhasználó beillesztheti
(`--query-file`). Dátummező forrásonként:

| Forrás | Dátumkorlát |
|---|---|
| PubMed | `datetype=edat&mindate=<kezdet>&maxdate=<vég>` (a bekerülés dátuma a késve indexelteket is elkapja); a futás a `[dp]`-szerinti számot is naplózza |
| Europe PMC | `CREATION_DATE:[<kezdet> TO <vég>]` |
| OpenAlex | `filter=from_publication_date:<kezdet>,to_publication_date:<vég>` (a létrehozás-dátum szűrő nem használt) |
| Scopus | `PUBYEAR > <kezdet éve − 1>` és — ha az élő ellenőrzés igazolja — `ORIG-LOAD-DATE AFT <ééééhhnn>` (unverified-live) |
| CT.gov | `filter.advanced=AREA[StudyFirstPostDate]RANGE[<kezdet>,MAX]` (+ a `ResultsFirstPostDate` szerinti futás) |

Forrásonként felső korlát (`--cap`, alap 5000); ha elérjük, vagy a lapozás megszakad (kvóta), a keresés
`complete: false` → H012 (a PRISMA-szám nem véglegesíthető, szűkíts vagy emeld a korlátot).

### 12.3 Hivatkozáskövetés (egyéb módszerek ága)

- **Előre** (idéző közlemények) a kiválasztott áttekintésekből és a bevont vizsgálatokból (mag-készlet; alap korlát
  200 mag): Europe PMC `/citations`, OpenAlex `cites:` (kredit), Scopus `REFEID(…)`, PubMed `…_citedin` (csak
  PMC-ben idéző cikkek).
- **Hátra** (irodalomjegyzék) az *új* bevont vizsgálatokból.
- Egy iteráció az alap; a TARCiS (Hirt 2024) szerint naplózzuk: irány, magok, eszköz/index, dátum, iterációk,
  találatszám. A hivatkozáskövetés hasznát Hirt 2023 áttekintése támasztja alá.
- Az idéző-közlemény halmaz nagy lehet; dátumszűrés: csak a horgony utáni közlemények (alap), kapcsolható.

### 12.4 Duplumszűrés az ismert halmazzal szemben

Minden új rekord L1 szerint összevetve az ismert rekordokkal: egyezés → `already_known` (PRISMA D1, a lábjegyzetben
külön számmal), L2 → javaslat (EP3). A megmaradt új rekordok EP4-en mennek át.

---

## 13. PRISMA 2020 leképezés (`szk.prisma-flow/v1`)

A `prisma` lépés a `01_kereses/headhunter/prisma_flow.json`-t írja: `"schema": "szk.prisma-flow/v1"`, `"source":
{"kind": "headhunter", …}`, lapos dobozkulcsok **a motor kanonikus neveivel** (`metaelemzes.prisma.COUNT_FIELDS`,
`REASON_FIELDS`), + egy `hh` objektum a részletekkel (a motor figyelmen kívül hagyja). **Miért nem a composer-nevek
(`dedup_removed`, `sought_for_retrieval`, …)?** Ha a fájlban `dedup_removed` áll, a motor (`ma.py prisma check
--json` és `api.prisma_check`) a `prisma.from_composer`-en át olvassa, amely az `other_methods_excluded_reasons`-t
eldobja → hamis P008-figyelmeztetés (kipróbálva 2026-10-05: ugyanaz a számsor composer-nevekkel 1 figyelmeztetés,
kanonikus nevekkel 0 hiba, 0 figyelmeztetés). A javítást az integrátor végzi (21. fejezet 16. pont); addig a
kanonikus nevek a kötelezők.

**Számolási sorrend:** előbb az egyéb-módszerek ág (áttekintésekből bányászott rekordok), utána az adatbázis-ág
(frissítő keresés), amelyet az előbbivel szemben duplumszűrünk.

| Doboz (PRISMA 2020) | Kulcs | Számítás |
|---|---|---|
| Adatbázisokból azonosított rekordok (A1) | `identified_databases` | Σ forrásonként a frissítő keresésben **letöltött** rekordok (PubMed, Europe PMC, OpenAlex, Scopus); bontás: `hh.databases` |
| Regiszterekből (A2) | `identified_registers` | CT.gov rekordok |
| Duplikátumok (D1) | `duplicates_removed` | a frissítésen belüli duplikátumok + a már ismert (egyéb-ág) rekordok; lábjegyzet: `hh.already_known` |
| Automatizált eszközzel kizárt (D2) | `automation_removed` | 0 (gépi javaslat nem döntés) |
| Egyéb okból eltávolított (D3) | `other_removed` | pl. visszavont-értesítés, ha ember így döntött |
| Szűrt (B) / kizárt (C) | `screened` / `excluded_screening` | cím/absztrakt döntések |
| Teljes szövegre keresett (E) / nem elérhető (F) | `sought` / `not_retrieved` | |
| Értékelt (G) / kizárt okokkal (H) | `assessed` / `excluded_eligibility` + `excluded_eligibility_reasons` (`{ok: n}`) | |
| Egyéb módszerekkel azonosított | `other_methods_identified` | az áttekintésekből + hivatkozáskövetésből származó **egyedi** közlemény-rekordok (L1 után); lábjegyzet: N (összes hivatkozás duplikációval), áttekintésenkénti számok |
| Egyéb ág: keresett / nem elérhető / értékelt / kizárt | `other_methods_sought` / `_not_retrieved` / `_assessed` / `_excluded` (+ `_excluded_reasons`) | `sought` = azonosított − a cím alapján kizártak (ez utóbbi a PRISMA-ábrán nem doboz → `hh.other_methods_title_excluded` lábjegyzet) |
| Bevont jelentések (J) | `included_reports` | (G − H) + (egyéb ág értékelt − kizárt) |
| Bevont vizsgálatok (I) | `included_studies` | vizsgálat-klaszterek, amelyeknek ≥ 1 bevont jelentése van (I ≤ J) |

- A program a kiírás után meghívja a `metaelemzes.prisma.check_flow`-t (és a CLI a `ma.py prisma check --json`
  egyenértékét): bármely `error` P-kód → H011.
- Ellenőrzött minta (a teszt része): A1 = 420, A2 = 16, D1 = 130, B = 306, C = 280, E = 26, F = 2, G = 24, H = 18
  (két ok), egyéb ág: azonosított 60, keresett 52, nem elérhető 3, értékelt 49, kizárt 9 (két ok), J = (24 − 18) +
  (49 − 9) = 46, I = 38 → `ma.py prisma check --json` „RENDBEN — 0 hiba, 0 figyelmeztetés".
- `export --to-project --prisma` a `02_szures/prisma_flow.json`-ba csak akkor másol, ha az nem létezik (a `source`
  objektummal), különben eltérés-összefoglalót ad. **Figyelem:** a munkapad mai PRISMA-képernyője (`routes/prisma.py`
  `FLOW_KEYS`) az egyéb-módszerek ág dobozait még nem szerkeszti; ezt az integrátor pótolja (21. fejezet 16. pont),
  addig a headhunter-képernyő mutatja a teljes ábrát.
- **`own_update` mód** (a felhasználó *saját* korábbi áttekintését frissíti): a korábbi változat bevont vizsgálatai
  és jelentései `previous_studies`/`previous_reports`, az összesítés `total_studies`/`total_reports` (P015).
- **Jelentés:** a `report.md` és a `kereses_naplo_headhunter.md` a PRISMA-S tételeit (adatbázisok platformmal,
  regiszterek, hivatkozáskövetés, dátumok, pontos stratégiák) és a TARCiS hivatkozáskövetési adatait adja; a
  rekordkövetés kérdéseiben Rethlefsen & Page 2022 ad további útmutatást. Overview-ként közölt munkánál PRIOR.

---

## 14. Parancssor (CLI)

`python -m metaelemzes.headhunter <parancs> <projekt> [kapcsolók]` (bekötés után `ma.py headhunter …`). Közös
kapcsolók: `--json` (boríték: `{"ok", "data", "warnings": [{code, hu, en}], "errors": [...], "pending":
[{checkpoint, n}], "next": "javasolt következő parancs"}`), `--lang hu|en`, `--offline`, `--sources a,b`,
`--actor user:<név>` (döntéseknél kötelező), `--quiet`. Üzenetek magyarul, kezdőbarátan.

| Parancs | Feladat |
|---|---|
| `sources [--check] [<projekt>]` | forrás-állapotok táblázata; `--check` próbakérésekkel (3.2) |
| `sources set <projekt> --enable … --disable …` | forrásválasztás (döntés) |
| `init <projekt> --question … [--pico pico.json] [--mode harvest\|own_update]` | állapotfájl, PICO, kritériumok, okszótár |
| `find-reviews <projekt> [--query-file f] [--since ÉÉÉÉ] [--max N]` | L1 |
| `reviews <projekt>` | áttekintés-jelöltek rangsorral és jelzésekkel |
| `select-reviews <projekt> --include id,… --exclude id,… --reason … --actor …` | EP1 |
| `extract <projekt> [--review id] [--strategy auto\|jats\|reflist\|pdf] [--pdf út]` | L3 |
| `show-text <projekt> --review id --part methods,results,tables` | szöveg az ágensnek (nem ment) |
| `agent-classify import <projekt> --review id --file f.json` | ágens-javaslatok ellenőrzött beolvasása |
| `resolve <projekt>` | L4 |
| `dedupe <projekt>` | L5 |
| `proposals <projekt> [--kind …] [--status pending]` | javaslatok listája |
| `decide <projekt> --target <id> --value accept\|reject\|include\|exclude\|… [--reason-code X3] [--reason …] [--batch-filter …] --actor …` | bármely EP-döntés |
| `overlap <projekt> [--level study\|report] [--scope kimenet] [--csv]` | L6 |
| `screen <projekt> list\|import <csv>\|propose` | L7 |
| `update-search <projekt> [--anchor latest\|earliest\|manual --start ÉÉÉÉ-HH-NN] [--overlap-months 6] [--cap N] [--dry-run]` | L8 (a `--dry-run` a lekérdezéseket és dátumokat mutatja futtatás nélkül) |
| `cite-search <projekt> [--direction forward\|backward\|both] [--seeds reviews\|included]` | hivatkozáskövetés |
| `merge <projekt>` | L9 |
| `prisma <projekt> [--check]` | `prisma_flow.json` + motor-ellenőrzés |
| `signoff <projekt> --actor …` | EP5 |
| `verify-secondary <projekt> --study st-… --field … --status verified\|discrepant --primary-locator … --actor …` | EP6 |
| `export <projekt> [--to-project] [--prisma] [--for-analysis] [--force]` | exportok (`--to-project`: `03_adatok/studies.json`, `--prisma`: `02_szures/prisma_flow.json`, csak ha nem létezik) |
| `status <projekt>` | lépések, EP-k, H-kódok, következő parancs |
| `verify <projekt>` | minden H-ellenőrzés (sémák, bizonyítékok, hash-lánc, szivárgás) |
| `rebuild <projekt>` | `studies.json`/`merged.json` újraépítése a döntésekből |
| `report <projekt>` | `exports/report.md` |

**Kilépési kódok:** 0 rendben; 1 `error` szintű H-kód; 2 használati hiba; 3 forrás nem érhető el, a lépés részleges;
4 emberi döntésre vár (nyitott EP). Az ágens ezek alapján lép tovább.

**Futások és megszakítás:** minden hosszabb lépés `runs/<run_id>/progress.jsonl`-be ír soronként
(`{"ts","step","phase","done","total","source","message":{hu,en}}`) és a kérések között figyeli a
`runs/<run_id>/CANCEL` fájlt (együttműködő megszakítás: a már letöltött adatot menti, a lépés `failed`/részleges).

---

## 15. Gépi ellenőrzések (H-kódok)

`metaelemzes/headhunter/checks.py` → `RULES = {"H001": (súlyosság, cím, teendő, forrás), …}` és `RULE_STAGES`, a
`validate.RULES`/`prisma.RULES` formátumában (bekötés után a KB a motor-szabályok közé veheti, 21. fejezet).

| Kód | Súlyosság | Cím | Szakasz | KB |
|---|---|---|---|---|
| H001 | error | Sémahiba egy headhunter-fájlban | S03 | — |
| H002 | error | Bevont-vizsgálat állítás bizonyíték nélkül (vagy a bizonyíték nem létezik) | S03 | D-S03-102 |
| H003 | warning (merge/exportnál error) | API-val meg nem erősített azonosító | S04 | D-S03-103 |
| H004 | error | Az ágens idézete nem található a forrásszövegben | S03 | D-S03-102 |
| H005 | error | Érvénytelen vagy nem létező azonosító (formailag hibás, vagy az API szerint nincs ilyen) | S04 | D-S03-103 |
| H006 | warning | A kinyert vizsgálatszám eltér az áttekintés által közölttől | S03 | D-S03-102 |
| H007 | error | Azonosító-ütközés (pl. azonos DOI két PMID-del) | S04 | D-S04-101 |
| H008 | warning | A forrás-áttekintés keresési dátuma nem közölt, becsült | S03 | D-S03-104 |
| H009 | error | Nyitott emberi ellenőrzőpont a lezáró lépésnél | S04 | D-S04-104 |
| H010 | error (`--for-analysis`), egyébként warning | Ellenőrizetlen másodlagos adat | S05 | D-S05-101 |
| H011 | error | A PRISMA-számok nem mennek át a motor ellenőrzésén | S14 | D-S14-101 |
| H012 | error | Hiányos keresés (lapozás megszakadt / felső korlát) | S03 | D-S03-105 |
| H013 | error | Visszavont közlemény a bevont halmazban | S04 | D-S04-103 |
| H014 | warning | Forrás nem érhető el, a lépés részleges | S03 | D-S03-106 |
| H015 | error | A döntésnapló hash-lánca sérült | S04 | — |
| H016 | error | Titok-szivárgás gyanúja (kulcs vagy e-mail egy kimeneti fájlban) | S00 | D-S00-101 |
| H017 | warning | Elavult kimenet (egy korábbi lépés változott) | S03 | — |
| H018 | warning | Áttekintések közti adat-ellentmondás | S05 | D-S05-102 |
| H019 | error | Teljes szöveg (vagy annak gyanúja) a headhunter-mappában | S03 | D-S03-107 |
| H020 | error | Közlemény több vizsgálatban, vagy vizsgálat nélküli aktív közlemény | S04 | D-S04-102 |

Minden H-találat kimenete: `{code, severity, stage, title, detail, artifacts[], suggested_command[], kb_refs[],
explain{hu,en}}` — a projekt-audit X-találatainak alakjával összhangban.

---

## 16. Ágens: `ma-metaheadhunter`

Fájl: `.claude/agents/ma-metaheadhunter.md` (a plugin-másolatot az integrátor készíti). Frontmatter:

```yaml
---
name: ma-metaheadhunter
description: "Metaheadhunter alágens: meglévő szisztematikus áttekintések és metaanalízisek bányászata — felkutatás, a bevont vizsgálatok kinyerése bizonyítékkal, azonosítás, duplumszűrés, átfedés (CCA), a saját PICO szerinti szűrés előkészítése, egyesítés, frissítő keresés és PRISMA-számok. Használd, ha a felhasználó a témában meglévő metaanalízisekből akar vizsgálatlistát építeni vagy frissíteni (S01 duplikáció-ellenőrzés után, S03–S04)."
tools: Read, Grep, Glob, Write, Bash, WebFetch, mcp__PubMed__search_articles, mcp__PubMed__get_article_metadata, mcp__PubMed__lookup_article_by_citation, mcp__PubMed__convert_article_ids, mcp__Clinical_Trials__get_trial_details, mcp__claude_ai_PubMed, mcp__claude_ai_Clinical_Trials
model: inherit
color: orange
---
```

**Feladatai:**
1. Indulás: `sources --check`; a hiányzó/hibás forrásokat kezdőbarátan elmagyarázza (TELEPITES.md hivatkozással).
2. PICO-blokkok és kritériumok: a `ma-tervezo` protokolljából (`00_protokoll/protokoll.md`) átveszi, a felhasználóval
   jóváhagyatja (`init`, `criteria_set` döntés).
3. Futtatja a lépéseket, a kilépési kód szerint halad; 4-es kódnál megáll, és **összefoglalja, mit kell eldönteni**
   (EP), a felhasználó helyett nem dönt.
4. (b) stratégiánál a `show-text` kimenetéből osztályoz, `agent_classification/<id>.json`-t ír **szó szerinti
   idézetekkel, azonosítók nélkül**, majd `agent-classify import`.
5. Szűrési javaslatot ad idézettel (`decide … --value include|exclude` **nem** az ő nevében: a javaslat
   `proposed_by: agent:ma-metaheadhunter`, a döntést a felhasználó hagyja jóvá — a felületen vagy a CLI-ben a saját
   `--actor user:…` értékével).
6. A kész halmazt a `ma-ellenorzo`-nak adja át ellenőrzésre (`verify`, `prisma --check`; H-kódok), a másodlagos
   adatok ellenőrzését (EP6) az S05 adatkinyeréshez köti.

**Tilos:** azonosítót kitalálni vagy emlékezetből beírni (MCP-konnektorral talált azonosítót is csak a
`resolve`-on át, API-proveniencával lehet rögzíteni); teljes szöveget fájlba menteni; emberi döntést a felhasználó
nevében rögzíteni; másodlagos számot elemzési táblába írni.

**Napló és KB:** `kb rules --agent planner --stage S03` és `--stage S04` (a HH-szabályok `planner`/`reviewer`/`all`
szerepűek), `kb search "corrected covered area"`, `kb show D-S03-101`. Projekt-napló: `project log <mappa> --agent
planner --stage S03 --decision "Metaheadhunter: …" --kb D-S03-101,… --strict` (az `headhunter` ágensnév bekötéséig).

---

## 17. Grafikus felület (varázsló-képernyő)

**Képernyő:** `ma_gui/web/src/screens/headhunter.js` (szükség esetén `headhunter_*.js` részekre bontva), a
`process` munkaterület `prisma` fülén alképernyőként: `MA.app.registerScreen({id: 'headhunter', title_key:
'screen.headhunter.title', workspace: 'process', tab: 'prisma', order: 20, render, onLeave})` — az `app.js`
módosítása nélkül megjelenik a fül alképernyői között. CSS: `ma_gui/web/src/css/headhunter.css`. i18n:
`ma_gui/web/src/i18n/hu/headhunter.json` és `en/headhunter.json` (azonos kulcskészlet, `hh.` előtag). Fejlesztői
fixture-ök: `ma_gui/web/fixtures/headhunter_*.json`.

**Varázsló-lépések** (mindegyik kártya: állapotjelvény, egy mondatos magyarázat, fő gomb, nyitott emberi tételek
száma, „Mit jelent?" lenyíló):

1. **Források** — táblázat (állapot, kulcs beállítva igen/nem, keret visszaállása), „Ellenőrzés" gomb, ki/be kapcsolók.
2. **Kérdés (PICO)** — blokkok és kritériumok, okszótár; jóváhagyás.
3. **Áttekintések** — rangsorolt lista (komponensek, jelzések, Cochrane-jelvény, nyílt-szöveg ikon); kijelölés,
   ok-modális (EP1).
4. **Kinyerés** — áttekintésenként jelöltek: idézet, lokátor, bizonyosság-jelvény (`high`/`medium`/`low`),
   k-eltérés figyelmeztetés; megerősítés/elvetés, áttekintésenként tömeges megerősítés (EP2).
5. **Duplumok** — javaslat-sor: két rekord egymás mellett, kiemelt eltérések, jellemzők; elfogad/elutasít, szűrős
   tömeges jóváhagyás megerősítő párbeszéddel (EP3).
6. **Átfedés** — hőtérkép (SVG; a pixel-matematika csak `MA.geom`-ban), összesített és páronkénti CCA a sávval; a
   számok kizárólag az `overlap.json`-ból.
7. **Szűrés** — rekordonként cím/absztrakt, eredet (mely áttekintések vonták be), gépi/ágens-javaslat idézettel;
   bevon/kizár okkóddal, billentyűparancsokkal (EP4).
8. **Frissítés** — ablak (horgony, átfedés, per-áttekintés dátumok bizonyítékkal), lekérdezések előnézete
   (`--dry-run`), futtatás, eredmények.
9. **Egyesítés és PRISMA** — vizsgálatlista proveniencával és ellentmondásokkal; PRISMA-dobozok a motor
   ellenőrzésével; lezárás (EP5); exportok.

**Végpontok** (`ma_gui/routes/headhunter.py`, a `prisma.py` mintájára; `register(router)`):

| Végpont | Leírás |
|---|---|
| `GET /api/headhunter/status` | `state.json` összegzés + H-kódok + következő lépés (ETag) |
| `GET /api/headhunter/reviews[?status=]`, `GET /api/headhunter/reviews/<id>` | áttekintések |
| `GET /api/headhunter/studies`, `GET /api/headhunter/proposals[?status=pending]` | rekordok, klaszterek, javaslatok |
| `GET /api/headhunter/overlap`, `GET /api/headhunter/merged`, `GET /api/headhunter/prisma` | kimenetek |
| `POST /api/headhunter/run` ← `{step, options}` | lépés indítása → `{run_id}` (202) |
| `GET /api/headhunter/runs/<run_id>` | `progress.jsonl` vége + állapot |
| `POST /api/headhunter/runs/<run_id>/cancel` | `CANCEL` fájl |
| `POST /api/headhunter/decide` ← `{kind, target, value, level?, reason_code?, reason?, batch?}` | döntés (If-Match a cél fájl ETag-jére) |

**Megvalósítási szabályok:**
- A route a motorhoz **nem** importál (a 6.8-as lint csak `metaelemzes.api`-t enged): olvasáshoz a JSON-fájlokat
  a `store`-on át olvassa, és a `metaelemzes/headhunter/contracts/` sémáival (`schema_lite`) ellenőrzi; minden
  írás és hosszú lépés **alfolyamat**: `jobs.run_subprocess([sys.executable, "-m", "metaelemzes.headhunter", <parancs>,
  <projekt>, "--json", …], cwd=<motor gyökere>)` háttérszálban (a `clean_env` a környezetet — proxy, kulcsok —
  átadja, csak a `PYTHONPATH`-ot nem). Felhasználói érték csak validált opcióként kerül az argv-be.
- A döntés `actor`-a a munkamenet felhasználója (`user:<név>`), nem a kérés törzséből jön.
- Szabad szöveg (indoklás) a meglévő PHI-őrön megy át, mint a többi végponton.
- Hibakódok: `HH_NOT_INITIALIZED` (409), `HH_CHECKPOINT_PENDING` (409), `HH_SOURCE_UNAVAILABLE` (503, részleges
  eredménnyel), `HH_CLI_FAILED` (502, a stderr vége redaktálva), `PRECONDITION_FAILED` (412), `BAD_REQUEST` (400).
- Statisztikát a felület nem számol (CCA, számok a fájlokból); akadálymentesség: csak billentyűzettel is
  használható, kontraszt mindkét témában (a meglévő `a11y.spec.js` mércéje).

---

## 18. Tudásbázis-seed

Új fájlok a `tudasbazis/seed/`-ben; a KB-építés ezeket automatikusan felveszi (`kb.SEED_TABLES` mintái:
`sources*.json`, `knowledge*.json`, `rules*.json`). Követelmények: JSON-tömb; a táblák oszlopai pontosan
(`source`: `source_id, citation, short, year, doi, kind, license_note, file_hint, notes`; `knowledge`: `k_id,
stage_id, kind, title, body, source_id, locator, tags`; `decision_rule`: `rule_id, stage_id, applies_to, condition,
recommendation, rationale, strength, machine_check, source_ids, locator`); **egyetlen azonosító sem ütközhet**
(a build duplikált ID-re `KBError`-t dob); a meglévő forrásokat (`prisma2020`, `prisma_s`, `cochrane_handbook`,
`amstar2`, `morvaridzadeh2020`, `viechtbauer2010`, `engine`, `kharbach2026_tools`) **újrahasznosítjuk**, nem
másoljuk. A szabály-azonosítók a meglévő `D-Sxx-nnn` alakot követik, a **101–149 tartományt a Metaheadhunter
foglalja** (a v1 munkaágak a 0xx tartományban dolgoznak). A tudásegységek `K-HH-nnn`. A `condition`/`recommendation`
magyar, a `body` a meglévő egységekhez hasonlóan angol vagy magyar; a meglévő seed-lintek (pl. „megvalósított
képesség tagadása" tilalom) érvényesek. Ellenőrzés: `python3 -c "from metaelemzes import kb; print(kb.build('/tmp/hh_kb.sqlite'))"`
és a teljes tesztkészlet KB-t érintő tesztjei.

### 18.1 `sources_HH.json`

| `source_id` | Hivatkozás (rövid) | `kind` | DOI | Megjegyzés (`notes`) |
|---|---|---|---|---|
| `pieper2014_cca` | Pieper D et al. J Clin Epidemiol 2014;67(4):368-75 | article | 10.1016/j.jclinepi.2013.11.007 | PMID 24581293; ellenőrizve PubMed |
| `hennessy2020_cca` | Hennessy EA, Johnson BT. Res Synth Methods 2020;11(1):134-145 | article | 10.1002/jrsm.1390 | PMID 31823513 |
| `ying2025_wcca` | Ying X et al. Res Synth Methods 2025;16(4):701-708 | article | 10.1017/rsm.2025.19 | PMID 41626914; CCA-sávok (1. keretes tábla) |
| `gotzsche2007_extraction` | Gøtzsche PC et al. JAMA 2007;298(4):430-7 | article | 10.1001/jama.298.4.430 | PMID 17652297 |
| `jones2005_extraction` | Jones AP et al. J Clin Epidemiol 2005;58(7):741-2 | article | 10.1016/j.jclinepi.2004.11.024 | PMID 15939227 |
| `mathes2017_extraction` | Mathes T, Klaßen P, Pieper D. BMC Med Res Methodol 2017;17:152 | article | 10.1186/s12874-017-0431-4 | PMID 29179685 |
| `garner2016_update` | Garner P et al. BMJ 2016;354:i3507 | guide | 10.1136/bmj.i3507 | PMID 27443385 |
| `shojania2007_outdated` | Shojania KG et al. Ann Intern Med 2007;147(4):224-33 | article | 10.7326/0003-4819-147-4-200708210-00179 | PMID 17638714 |
| `aromataris2015_umbrella` | Aromataris E et al. Int J Evid Based Healthc 2015;13(3):132-40 | guide | 10.1097/XEB.0000000000000055 | PMID 26360830 |
| `prior2022` | Gates M et al. BMJ 2022;378:e070849 (PRIOR) | standard | 10.1136/bmj-2022-070849 | PMID 35944924 |
| `ballard2017_overviews` | Ballard M, Montgomery P. Res Synth Methods 2017;8(1):92-108 | article | 10.1002/jrsm.1229 | PMID 28074553 |
| `pollock2019_overview_tool` | Pollock M et al. Syst Rev 2019;8:29 | article | 10.1186/s13643-018-0768-8 | PMID 30670086 |
| `hirt2023_citation_tracking` | Hirt J et al. Res Synth Methods 2023;14(3):563-579 | article | 10.1002/jrsm.1635 | PMID 37042216 |
| `tarcis2024` | Hirt J et al. BMJ 2024;385:e078384 (TARCiS) | standard | 10.1136/bmj-2023-078384 | PMID 38724089 |
| `rethlefsen2022_tracking` | Rethlefsen ML, Page MJ. J Med Libr Assoc 2022;110(2):253-257 | article | 10.5195/jmla.2022.1449 | PMID 35440907 |
| `bramer2016_dedup` | Bramer WM et al. J Med Libr Assoc 2016;104(3):240-3 | article | 10.3163/1536-5050.104.3.014 | PMID 27366130 |
| `mckeown2021_dedup` | McKeown S, Mir ZM. Syst Rev 2021;10:38 | article | 10.1186/s13643-021-01583-y | PMID 33485394 |
| `hair2023_asysd` | Hair K et al. BMC Biol 2023;21:189 (ASySD) | article | 10.1186/s12915-023-01686-z | PMID 37674179 |
| `tramer1997_duplicate` | Tramèr MR et al. BMJ 1997;315:635-40 | article | 10.1136/bmj.315.7109.635 | PMID 9310564 |
| `vonelm2004_duplicate` | von Elm E et al. JAMA 2004;291(8):974-80 | article | 10.1001/jama.291.8.974 | PMID 14982913 |
| `colditz1994_bcg` | Colditz GA et al. JAMA 1994;271(9):698-702 | exemplar | — (PubMedben nincs DOI) | PMID 8309034; a metafor `dat.bcg` adatkészletének forrása |

`license_note`: „csak hivatkozás (teljes szöveg nincs a tudásbázisban)"; `file_hint`: null. A Cochrane Handbook V.
fejezete (Pollock M, Fernandes RM, Becker LA, Pieper D, Hartling L: *Overviews of Reviews*) a meglévő
`cochrane_handbook` forrásra mutat, `locator: "V. fejezet"` (nem PubMed-indexelt; ellenőrizve a cochrane.org
fejezetoldalán).

### 18.2 `rules_HH.json` (vázlat — a build-ágens fogalmazza ki teljes mondatokkal)

| `rule_id` | Szakasz | `applies_to` | Erősség | Feltétel → ajánlás (röviden) | `machine_check` | Források |
|---|---|---|---|---|---|---|
| D-S00-101 | S00 | all | must | API-kulcsot (Scopus/OpenAlex/NCBI) használsz → csak környezeti változóban; parancssorban, fájlban, naplóban soha; `sources --check` | H016 | engine |
| D-S01-101 | S01 | planner | should | A duplikáció-ellenőrzés (D-S01-002) meglévő SR/MA-kat talált → mérlegeld a Metaheadhuntert: a bevont vizsgálataik bányászata + frissítés; a saját PICO-dat ne add fel | — | garner2016_update, cochrane_handbook |
| D-S03-101 | S03 | planner | must | Előző áttekintésekből bányászol → „egyéb módszerek" ágként naplózd (PRISMA-S: hivatkozáskövetés), a forrás-áttekintések listájával; ez nem helyettesíti az adatbázis-keresést | H012 | prisma_s, tarcis2024, hirt2023_citation_tracking |
| D-S03-102 | S03 | all | must | Bevont vizsgálatot áttekintésből veszel át → bizonyíték kötelező (áttekintés + hely + szó szerinti idézet); az irodalomjegyzékben szereplés nem bevonás | H002, H004, H006 | engine, cochrane_handbook |
| D-S03-103 | S03 | all | must | Azonosítót rögzítesz → csak API-válaszból vagy API-val megerősítve; MI-/emlékezetből származó azonosító tilos | H003, H005 | engine, kharbach2026_tools |
| D-S03-104 | S03 | planner | must | Frissítő keresést tervezel → az ablak a forrás-áttekintések bizonyítékkal rögzített keresési dátumából + átfedési ablakból; nem közölt dátumnál korábbi becslés és emberi jóváhagyás | H008 | garner2016_update, shojania2007_outdated |
| D-S03-105 | S03 | reviewer | must | Egy keresés lapozása megszakadt vagy elérte a korlátot → a PRISMA-szám nem végleges; szűkíts vagy futtasd újra | H012 | prisma_s |
| D-S03-106 | S03 | all | should | Egy forrás nem érhető el (kvóta, jogosultság, hálózat) → a hiányt a keresési naplóban és a módszertanban nevezd meg; pótold, amikor elérhető | H014 | prisma_s |
| D-S03-107 | S03 | all | must | Teljes szöveget dolgozol fel → csak memóriában; a projektben csak bibliográfiai adat és ≤ 300 karakteres idézet | H019 | engine |
| D-S04-101 | S04 | all | must | Áttekintésekből származó rekordokat duplumszűrsz → csak azonos azonosító vonható össze automatikusan; a többi javaslat emberi döntés, kétség esetén megtartás | H007 | bramer2016_dedup, mckeown2021_dedup, hair2023_asysd |
| D-S04-102 | S04 | reviewer | must | Közleményeket vizsgálathoz kapcsolsz → regiszterszám (a közlemény saját regisztrációs nyilatkozata; a CT.gov hivatkozástípusa önmagában nem elég) vagy az áttekintés csoportosítása alapján javaslat, emberi megerősítéssel; rejtett kettős közlésre figyelj | H020 | vonelm2004_duplicate, tramer1997_duplicate |
| D-S04-103 | S04 | reviewer | must | Visszavont közlemény a halmazban → jelöld, a kizárást indokold, a PRISMA-ban okként szerepeljen | H013 | engine |
| D-S04-104 | S04 | all | must | A jogosultságot a SAJÁT PICO-d szerint döntsd el; az, hogy egy áttekintés bevonta, csak kontextus; teljes szöveg szinten okot rögzíts | H009 | prisma2020, cochrane_handbook |
| D-S05-101 | S05 | all | must | Áttekintésből vett számot használnál → másodlagos adat: az elsődleges közleménnyel ellenőrizd elemzés előtt; ellenőrizetlenül nem kerülhet az adattáblába | H010 | gotzsche2007_extraction, jones2005_extraction, mathes2017_extraction |
| D-S05-102 | S05 | reviewer | should | Két áttekintés eltérő számot közöl ugyanarról → az eltérést dokumentáld, az elsődleges forrás dönt | H018 | gotzsche2007_extraction |
| D-S13-101 | S13 | evaluator | should | Overview-t vagy bányászott halmazt értékelsz → közöld az átfedést (CCA összesen és páronként, sávval), és értelmezd a Hennessy–Johnson lépések szerint | — | pieper2014_cca, hennessy2020_cca, ying2025_wcca |
| D-S14-101 | S14 | reviewer | must | A bányászott halmaz PRISMA-számait jelented → egyéb-módszerek ág + adatbázis-ág, `ma.py prisma check --json 01_kereses/headhunter/prisma_flow.json` hibátlan | H011 | prisma2020, rethlefsen2022_tracking |
| D-S14-102 | S14 | all | should | Az áttekintések áttekintését (overview) közlöd → PRIOR; hivatkozáskövetésnél TARCiS-adatok | — | prior2022, tarcis2024 |

### 18.3 `knowledge_HH.json` (vázlat)

| `k_id` | Szakasz | `kind` | Cím | Forrás | Lokátor |
|---|---|---|---|---|---|
| K-HH-001 | S03 | definition | Corrected Covered Area (CCA) — definíció és képlet | pieper2014_cca | absztrakt (Study design) |
| K-HH-002 | S13 | threshold | CCA-sávok (enyhe/mérsékelt/magas/nagyon magas), útmutató jelleggel | ying2025_wcca | Box 1 |
| K-HH-003 | S13 | guidance | Az átfedés öt vizsgálati lépése (Hennessy & Johnson) | hennessy2020_cca | Guidance szakasz |
| K-HH-004 | S13 | concept | wCCA: mintaelemszám-súlyozott átfedés | ying2025_wcca | 4–5. szakasz |
| K-HH-005 | S05 | pitfall | Kinyerési hibák SMD-metaanalízisekben (37%) | gotzsche2007_extraction | Results |
| K-HH-006 | S05 | pitfall | Kinyerési hibák Cochrane-áttekintésekben (20/34) | jones2005_extraction | Results |
| K-HH-007 | S05 | pitfall | Kinyerési hibák gyakorisága módszertani áttekintésben (akár 50%) | mathes2017_extraction | Results |
| K-HH-008 | S04 | pitfall | Rejtett kettős közlés hatása (23%-os túlbecslés az ondanszetron-példában) | tramer1997_duplicate | Results |
| K-HH-009 | S04 | concept | A kettős közlés hat mintázata; a szerzőség megbízhatatlan kritérium | vonelm2004_duplicate | Data synthesis |
| K-HH-010 | S04 | tool | Duplumszűrő módszerek pontossága (Ovid, Covidence, Rayyan) | mckeown2021_dedup | Results |
| K-HH-011 | S04 | tool | ASySD: automatikus duplumszűrő (érzékenység 0,95–0,99) | hair2023_asysd | Results |
| K-HH-012 | S03 | guidance | A hivatkozáskövetés haszna és terminológiája | hirt2023_citation_tracking | Results |
| K-HH-013 | S14 | guidance | TARCiS: a hivatkozáskövetés jelentése | tarcis2024 | — |
| K-HH-014 | S03 | guidance | Mikor és hogyan frissíts (döntési keret, ellenőrzőlista) | garner2016_update | — |
| K-HH-015 | S03 | concept | Mennyi idő alatt avul el egy áttekintés (medián 5,5 év; 23% 2 éven belül) | shojania2007_outdated | Results |
| K-HH-016 | S01 | concept | Umbrella review (JBI) — áttekintések szintézise | aromataris2015_umbrella | — |
| K-HH-017 | S01 | guidance | Overview-k feltételei (4 feltétel) és döntési eszköz az áttekintések bevonásához | ballard2017_overviews | Results |
| K-HH-018 | S01 | guidance | Cochrane Overviews of Reviews (Handbook V. fejezet) | cochrane_handbook | V. fejezet |
| K-HH-019 | S03 | tool | Forrás-sajátosságok (Europe PMC `EXT_ID`, nyílt teljes szöveg; OpenAlex napi keret és kulcs; Scopus kulcs/insttoken/jogosultság; CT.gov hivatkozástípusok) | engine | TERV_metaheadhunter.md 3.1 |
| K-HH-020 | S03 | convention | Bizonyíték-lokátor és másodlagos-adat jelölés a Metaheadhunterben | engine | TERV_metaheadhunter.md 1., 4. |

A számszerű állítások (37%, 20/34, 50%, 23%, 5,5 év, 0,95–0,99) a 22. fejezetben ellenőrzött absztraktokból
származnak; a seed-író ezeket nem változtathatja, és nem adhat hozzá ellenőrizetlen állítást.

---

## 19. Tesztterv

**Futtatás:**

```
cd metaanalizis-asszisztens
python3 -m unittest discover -s tests -p 'test_headhunter*'
python3 -m unittest discover -s tests/gui -p 'test_headhunter*'
python3 ma_gui/web/build_gui.py --dev && node tests/gui/ui/headhunter.spec.js
MA_LIVE_TESTS=1 python3 -m unittest tests.test_headhunter_live      # élő, opt-in
```

### 19.1 Kazetták (offline)

- Hely: `tests/reference/headhunter/cassettes/<forrás>/<név>.json` (`szk.ma.headhunter.cassette/v1`). A lejátszó a
  kérést a redaktált URL + metódus (+ törzs) alapján illeszti; ismeretlen kérésnél a teszt **hibát** ad (nem megy ki
  a hálózatra).
- **Rögzítő** (`python -m metaelemzes.headhunter cassette record …`, csak fejlesztői): eldobja az `Authorization`,
  `X-ELS-*`, `Cookie`, `Set-Cookie`, `report-to`, `nel` fejléceket; redaktálja az `api_key`, `apiKey`, `insttoken`,
  `mailto`, `email`, `tool` paramétereket; az EFetch XML `AbstractText`-jét és minden JATS `<body>`/`<abstract>`
  tartalmát kivágja (`redactions[]`); írás előtt bájtszinten ellenőrzi, hogy a nyilvántartott titkok nincsenek
  benne (különben nem ír).
- **Teljes szövegű fixture:** szintetikus JATS (`tests/reference/headhunter/jats/`), a valós szerkezetek mintájára:
  `cochrane_nested_reflist.xml` (PMC6488980 mintája: „References to studies included in this review" → al-listák
  pub-id-kkel), `bmj_table_author_year.xml` (PMC4122754 mintája: „Adetifa 2010" sor, `ext-link` azonosítók,
  társszerző-csapda), `springer_table_multirow.xml` (PMC12070792 mintája: `<p>`-darabolt első cella, „2.1/2.2"
  sorok), `no_structure.xml` (csak irodalomjegyzék). Valós cikkszöveg nem kerül a repóba.
- **Rögzíthető itt (sandbox):** PubMed (esearch/esummary JSON, ecitmatch, elink, efetch absztrakt nélkül), Europe
  PMC (search lite/core, references, citations, annotációk, `fullTextXML` 500 nem-OA cikkre), CT.gov (studies,
  version, AREA-szűrők), OpenAlex egyedi lekérés és a **valós 429** (keret elfogyott), Scopus **valós 401** (két
  hibaalak, kulcs nélkül).
- **Kézzel írt, `unverified_live: true`:** Scopus Search sikeres válasz (cursor-lapozás), Abstract Retrieval
  `view=REF`, 403 (`AUTHORIZATION_ERROR`), 429 (`QUOTA_EXCEEDED`, `X-RateLimit-Reset`), OpenAlex listás válasz kulccsal.
  A felhasználó a saját gépén igazolja: `python -m metaelemzes.headhunter sources --check` (és
  `MA_LIVE_TESTS=1 … test_headhunter_live.TestScopusLive`), majd a kazettát újrarögzíti.

### 19.2 Egységtesztek (fájlonként)

| Tesztfájl | Mit ellenőriz |
|---|---|
| `test_headhunter_secrets.py` | kulcs csak fejlécben; `redact()` minden útvonalon; hamis kulcsos teljes futás után semmilyen kimenetben nincs kulcs/e-mail (H016) |
| `test_headhunter_net.py` | sebességkorlát, újrapróbálás, `Retry-After` (rövid: vár; hosszú: `rate_limited` + `reset_at`), időtúllépés, proxy-környezet tisztelete (`ProxyHandler`), offline mód, gyorsítótár |
| `test_headhunter_sources.py` | forrásonkénti kliens kazettákkal; Europe PMC `EXT_ID`; `fullTextXML` 500 → `no_fulltext`; CT.gov hivatkozástípus; OpenAlex egyedi vs listás költség; Scopus 401/403/429 → állapot és magyar üzenet |
| `test_headhunter_jats.py` | a1/a2/a3/a4 a szintetikus fixture-ökön: helyes jelöltek, lokátorok, idézetek ≤ 300, első-szerző-csapda, többsoros vizsgálat egy jelölt |
| `test_headhunter_extract.py` | keresési dátum és k-minták; H006, H008; ágens-import: szó szerinti idézet-ellenőrzés (H004), azonosító eldobása |
| `test_headhunter_resolve.py` | feloldási sorrend, elfogadási küszöbök, `review`/`user` forrás megerősítése, regiszter-kapcsolat, visszavonás |
| `test_headhunter_dedupe.py` | L1–L4 szabályok, normalizálás (ø, ß stb.), küszöbök, `auto_applied` csak L1, eseményforrású újraépítés |
| `test_headhunter_overlap.py` | CCA: kidolgozott példa (30%), páronként, `c < 2` → null, sávhatárok (5,0/10,0/15,0), report vs study szint |
| `test_headhunter_prisma.py` | leképezés; a kimenet átmegy a `metaelemzes.prisma.check_flow`-n; szándékosan hibás számoknál H011; `own_update` |
| `test_headhunter_model.py` | sémavalidálás minden kimenetre; atomikus írás; zár; hash-lánc (H015); determinisztikus ID-k és rendezés |
| `test_headhunter_checks.py` | minden H-kód pozitív és negatív esete; H019 (teljes szöveg gyanú) |
| `test_headhunter_cli.py` | parancsok, `--json` boríték, kilépési kódok (0/1/2/3/4), magyar üzenetek, végponttól végpontig offline: kis szintetikus projekt (3 áttekintés → kinyerés → feloldás → duplumok → döntések → átfedés → frissítés → egyesítés → PRISMA → export) |
| `test_headhunter_kb.py` | a HH-seedek: ID-formátum és -tartomány, nincs ütközés, a hivatkozott forrás létezik, `kb.build` ideiglenes adatbázisba sikeres, a `machine_check` H-kódjai léteznek a `checks.RULES`-ban |
| `test_headhunter_contracts.py` | a sémák kanonikus formája, `$id`/fájlnév/`schema`-konstans egyezés, csak `schema_lite`-kulcsszavak, slash-lint, `$ref`-ek feloldhatók, pozitív/negatív példák |
| `tests/gui/test_headhunter_routes.py` | végpontok fixture-projekttel; ETag/If-Match; `actor` a munkamenetből; a CLI-alfolyamat hívása (valódi CLI offline módban); hibakódok; a route nem importál motor-modult |
| `tests/gui/ui/headhunter.spec.js` | varázsló a dev-buildben fixture-ökkel: lépések, EP-jelvények, döntés-modális, hőtérkép számai a fixture-ből, billentyűzetes használat |

### 19.3 Élő füstpróba (opt-in, `MA_LIVE_TESTS=1`)

**BCG-vakcina és tuberkulózis.**
- Felkutatás (PubMed + Europe PMC) a találatok között: Colditz 1994 (PMID 8309034), Mangtani 2014 (PMID 24336911),
  Roy 2014 (PMID 25097193, PMC4122754), Abubakar 2013 (PMID 24021245), Martinez 2022 (PMID 35961354).
- Kinyerés a Kashangura 2019 Cochrane-áttekintésből (PMID 31038197, PMC6488980, nyílt): a „References to studies
  included in this review" pontosan 6 vizsgálatot ad: Andrews 2017, Bunyasi 2017, Ndiaye 2015, Nemes 2018, Scriba
  2011, Tameris 2013 (`high`).
- Feloldás: Tameris 2013 → PMID 23391465 → regiszter NCT00953927 (PubMed DataBank és Europe PMC annotáció; *erős*).
  Regressziós állítás: a CT.gov ezt a hivatkozást `BACKGROUND`-ként sorolja, ez nem gyengítheti a kapcsolatot, és
  az NCT04975178 (amely szintén `BACKGROUND`-ként idézi) **nem** kerülhet ugyanabba a vizsgálatba.
- `dat.bcg` (metafor, Colditz 1994 forrásból, 13 vizsgálat — a címkék a `tests/reference/metafor_reference.json`
  `bcg.rows[].study` mezőjében): kézi bemenetként (13 hivatkozás szerző + év + folyóirat) a feloldás **csak**
  ellenőrzött egyezést fogadhat el; a régi (1948–1953) tételeknél a „feloldatlan" elfogadható eredmény, a
  kitalált PMID nem. Kötelező állítás: minden feloldott PMID `esummary` első szerzőjének vezetékneve egyezik a
  címkéével.

**Szója/izoflavon és gyulladásos markerek.**
- Felkutatás a találatok között: Asbaghi 2020 (PMID 32979840), Rezazadegan 2021 (PMID 32814603), Khodarahmi 2019
  (PMID 30314925), Bajerska 2022 (PMID 34642749), Gholami 2025 (PMID 40355968, PMC12070792, nyílt).
- Kinyerés a Gholami 2025 1. táblázatából: „Acharjee 2015" → CR24; az „Anderson 2007" két sora egy jelölt;
  „Azadbakht 2007" és „Azadbakht 2008" külön jelölt, az első szerzős illesztéssel.
- Átfedés a kiválasztott szója-áttekintések között (CCA számolható, sávval); frissítő ablak a legfrissebb keresési
  dátumból.
- **Megjegyzés:** a tudásbázis `morvaridzadeh2020` forrása (Morvaridzadeh 2020, PMID 33233189) a szója és az
  **oxidatív stressz** paramétereiről szól, nem a gyulladásos markerekről. Ha a felhasználó helyi PDF-je
  megvan (`tudasbazis/forrasok/` `1-s2.0-S0963996920306037-main`), a (c) stratégia élő/helyi tesztje ezen fut
  (a PDF szövege a repóba nem kerül); ha nincs, a teszt kihagyva.

### 19.4 Elfogadási feltételek

1. Az offline teljes folyamat (19.2 `test_headhunter_cli`) zöld, minden kimenet sémahelyes, a `prisma_flow.json`
   átmegy a motor ellenőrzésén.
2. Egyetlen kimenetben sincs kulcs, e-mail-cím, teljes szöveg vagy absztrakt (fixture-ben).
3. Minden bevont-vizsgálat állításnak van bizonyítéka; minden végső azonosító API-proveniencájú.
4. Elérhetetlen forrás (pl. Crossref itt) mellett a lépés lefut, a kimenet kimondja a hiányt (H014), kilépési kód 3.
5. A GUI-tesztek és a meglévő tesztkészletek zöldek (a HH-fájlok nem törnek meglévő tesztet).

---

## 20. Fájl-tulajdon: ki mit épít

Négy build-ágens, párhuzamosan; a határfelületeket a 20.5 rögzíti. Mindegyik **csak a saját fájljait** hozza
létre/módosítja. Közös szabályok: **Python 3.9+, csak standard könyvtár**; magyar docstring és üzenetek (a
kódazonosítók angolul); a meglévő tesztkészletek zöldek maradnak. A `metaelemzes/headhunter/contracts/*.schema.json`
sémák már léteznek (ennek a tervnek a részei); additív bővítésük a HH-B feladata, a terv 4. fejezetének frissítésével.
**Sémavalidálás futásidőben:** a `model.validate(doc, név)` a `ma_gui.schema_lite`-ot lustán importálja (stdlib-only,
ugyanabban a pluginban érkezik); ha nem importálható, a kötelező kulcsok és a `schema`-konstans minimális
ellenőrzésére esik vissza, és ezt `info` szinten jelzi. A tesztek mindig a teljes `schema_lite`-validálást futtatják.

### 20.1 HH-A — hálózat, források, titkok, kazetták

```
metaelemzes/headhunter/__init__.py            (verzió: __version__ = "1.0.0", rövid docstring)
metaelemzes/headhunter/secrets.py             env-olvasás, SecretRegistry, redact()
metaelemzes/headhunter/net.py                 HttpClient: urllib, proxy, forrásonkénti korlát, újrapróbálás, Retry-After,
                                              gyorsítótár (cache/http), offline mód, retrieval-rekord
metaelemzes/headhunter/cassette.py            rögzítő (redaktálással) és lejátszó
metaelemzes/headhunter/sources/__init__.py    regiszter + állapot-ellenőrzés (sources --check)
metaelemzes/headhunter/sources/pubmed.py
metaelemzes/headhunter/sources/europepmc.py
metaelemzes/headhunter/sources/openalex.py
metaelemzes/headhunter/sources/scopus.py
metaelemzes/headhunter/sources/ctgov.py
metaelemzes/headhunter/sources/crossref.py
tests/test_headhunter_secrets.py
tests/test_headhunter_net.py
tests/test_headhunter_sources.py
tests/reference/headhunter/cassettes/**
```

### 20.2 HH-B — folyamat-logika

```
metaelemzes/headhunter/model.py               fájl-IO, sémák (contracts/), ID-k, atomikus írás, zár, döntésnapló + hash-lánc
metaelemzes/headhunter/normalize.py           ID- és szövegnormalizálás, hasonlóság
metaelemzes/headhunter/discover.py            L1 + rangsor + Cochrane-változatok
metaelemzes/headhunter/jats.py                JATS-elemzők (a1–a4)
metaelemzes/headhunter/extract.py             L3 (stratégiák, keresési dátum, k, ágens-import, PDF)
metaelemzes/headhunter/resolve.py             L4
metaelemzes/headhunter/dedupe.py              L5 (szabályok, javaslatok, klaszterek, rebuild)
metaelemzes/headhunter/overlap.py             L6
metaelemzes/headhunter/screen.py              L7
metaelemzes/headhunter/update.py              L8 (ablak, lekérdezések, hivatkozáskövetés)
metaelemzes/headhunter/merge.py               L9
metaelemzes/headhunter/prisma_map.py          13. fejezet + motor-ellenőrzés
metaelemzes/headhunter/export.py              exportok
metaelemzes/headhunter/checks.py              H-kódok (RULES, RULE_STAGES) és verify
metaelemzes/headhunter/report.py              report.md
metaelemzes/headhunter/facade.py              nyilvános API (20.5) — a későbbi api.py-bekötés célpontja
tests/test_headhunter_jats.py, _extract.py, _resolve.py, _dedupe.py, _overlap.py, _prisma.py, _model.py, _checks.py
tests/reference/headhunter/jats/**, tests/reference/headhunter/projects/**
```

### 20.3 HH-C — CLI, ágens, tudásbázis, szerződés-dokumentáció

```
metaelemzes/headhunter/__main__.py            python -m metaelemzes.headhunter
metaelemzes/headhunter/cli.py                 argparse, --json boríték, kilépési kódok, progress, CANCEL
metaelemzes/headhunter/messages.py            magyar/angol üzenetek (H-kódok, állapotok, lépések)
metaelemzes/headhunter/contracts/README.md    a sémák leírása (a *.schema.json már létezik — additív bővítés csak a terv frissítésével)
metaelemzes/headhunter/INTEGRACIO.md          a 21. fejezet bekötési lépései másolható kódrészletekkel és a doksi-szövegekkel
.claude/agents/ma-metaheadhunter.md
tudasbazis/seed/sources_HH.json
tudasbazis/seed/rules_HH.json
tudasbazis/seed/knowledge_HH.json
tests/test_headhunter_cli.py
tests/test_headhunter_kb.py
tests/test_headhunter_contracts.py
tests/test_headhunter_live.py                 (MA_LIVE_TESTS=1 nélkül skip)
```

### 20.4 HH-D — grafikus felület

```
ma_gui/routes/headhunter.py
ma_gui/web/src/screens/headhunter.js          (+ headhunter_review.js, headhunter_dedupe.js, headhunter_overlap.js … szükség szerint)
ma_gui/web/src/css/headhunter.css
ma_gui/web/src/i18n/hu/headhunter.json
ma_gui/web/src/i18n/en/headhunter.json
ma_gui/web/fixtures/headhunter_*.json
tests/gui/test_headhunter_routes.py
tests/gui/ui/headhunter.spec.js
tests/gui/ui/headhunter_fixtures/**           (ha kell)
```

### 20.5 Határfelületek (a párhuzamos munkához rögzítve)

```python
# net.py (HH-A)
class Response:            # status:int, headers:dict, text:str, json():object, from_cache:bool, retrieval:dict
class SourceUnavailable(Exception):   # .source, .status ('unreachable'|'rate_limited'|'unauthorized'|'forbidden'|'not_configured'), .reset_at, .explain{hu,en}
class HttpClient:
    def __init__(self, cache_dir=None, offline=False, player=None, recorder=None, clock=None, sleep=None): ...
    def get(self, source, url, params=None, headers=None, accept="json", cache=True, ttl_days=30) -> Response: ...

# sources/<x>.py (HH-A) — mind: Client(http, cfg); check() -> dict(status, message, key_configured, entitlement, reset_at)
pubmed.Client:    esearch(term, datetype=None, mindate=None, maxdate=None, retmax=..., retstart=0) -> {count, ids, querytranslation}
                  esummary(pmids) -> [dict]; efetch_xml(pmids) -> str (memóriában); elink(pmid, linkname) -> [pmid]
                  ecitmatch([(journal, year, volume, first_page, author, key)]) -> {key: pmid|None}; efetch_pmc(pmcid) -> str|None
europepmc.Client: search(query, result_type="core", page_size=100, cursor="*") -> iter[dict]; fulltext_xml(pmcid) -> str|None
                  references(src, ext_id) -> iter[dict]; citations(src, ext_id) -> iter[dict]; accession_numbers(src, ext_id) -> [str]
openalex.Client:  work(id_or_pmid_or_doi, select=...) -> dict|None; works(filter=..., search=None, select=..., per_page=200) -> iter[dict]
scopus.Client:    search(query, fields=..., count=25, view="STANDARD") -> iter[dict]; references(eid, startref=1, refcount=40) -> iter[dict]
ctgov.Client:     study(nct) -> dict|None; studies(term=None, cond=None, intr=None, advanced=None, fields=..., page_size=100) -> iter[dict]
crossref.Client:  work(doi) -> dict|None

# facade.py (HH-B) — a CLI és később az api.py ezt hívja; minden függvény JSON-képes dict-et ad vissza
init(project_dir, question, pico=None, mode="harvest", actor=None)
sources_status(project_dir=None, check=False)
run_step(project_dir, step, **options)          # step ∈ {find_reviews, extract, resolve, dedupe, overlap, screen_propose,
                                                 #          update_search, cite_search, merge, prisma, export, report}
decide(project_dir, kind, target, value, actor, level=None, reason_code=None, reason=None, evidence_ids=(), batch=None,
       proposed_by=None, supersedes=None)
status(project_dir)  verify(project_dir)  rebuild(project_dir)  show_text(project_dir, review_id, parts)
```

A HH-B a HH-A-t kazettás lejátszással teszteli; amíg a HH-A nincs kész, a `sources` interfészt egy egyszerű
helyettesítővel (fake) használja. A HH-C a `facade`-ot hívja; a HH-D az alfolyamatot (CLI) és a fájlokat — tehát
a HH-D csak a CLI-szerződésre (14. fejezet) és a sémákra (4. fejezet) épít.

---

## 21. Az integrátor teendői (bekötés, dokumentáció)

Ezeket **nem** a build-ágensek végzik (a fájlok más munkaágakhoz tartoznak). A `metaelemzes/headhunter/
INTEGRACIO.md` (HH-C) minden ponthoz másolható kódrészletet ad.

1. **`metaelemzes/cli.py`:** új alparancs `headhunter`: az argumentumok továbbadása
   `metaelemzes.headhunter.cli.main(argv)`-nak (a kilépési kód is); a modul docstring parancslistájába egy sor:
   `headhunter … Metaheadhunter — meglévő metaanalízisek bányászata`.
2. **`metaelemzes/api.py`:** lusta import (`from .headhunter import facade as _hh`), és homlokzat-függvények:
   `headhunter_status`, `headhunter_sources`, `headhunter_run_step`, `headhunter_decide`, `headhunter_verify`
   (a facade 1:1 burkolói); az `engine_info()`/képesség-leírás `features` listájába `"headhunter"`.
3. **`ma_gui/routes/__init__.py`:** `from . import headhunter` és a `MODULES` tuple-be `headhunter`.
4. **`metaelemzes/projekt.py`:** `KNOWN_AGENTS`-be `"headhunter"`; **`metaelemzes/cli.py`** `kb rules --agent`
   választékába `"headhunter"` (a HH-szabályok addig `planner`/`reviewer`/`evaluator`/`all` szerepűek, így ez
   opcionális).
5. **`metaelemzes/kb.py`:** `ENGINE_RULESETS`-be `("headhunter.checks", "S03")`, `ENGINE_RULE_KINDS`-ba
   `"headhunter.checks": "Metaheadhunter"`, `ENGINE_RULE_WHY`-ba a (`headhunter.checks`, error/warning) magyar
   indoklás; **`ma_gui/snapshot.py`** `_KB_ID_RE`: `[VPX]\d{3}` → `[VPXH]\d{3}`; **`metaelemzes/contracts/
   common.v1.schema.json`** `kbid` mintája: `^([VPXH]\d{3}|…)$` (lazítás, additív).
6. **Skill:** `.claude/skills/metaanalizis/SKILL.md` és a plugin-másolat (`metaanalizis-asszisztens/skills/
   metaanalizis/SKILL.md`): az alágensek közé `ma-metaheadhunter` (plugin-névvel `metaanalizis:ma-metaheadhunter`);
   mikor hívd: S01 duplikáció-ellenőrzés után, ha van meglévő SR/MA, és S03–S04-ben; a lépések és EP-k rövid
   leírása; a kilépési kódok jelentése.
7. **Ágens-másolat:** `.claude/agents/ma-metaheadhunter.md` → `metaanalizis-asszisztens/agents/ma-metaheadhunter.md`
   (plugin-útvonalakkal: `python ${CLAUDE_PLUGIN_ROOT}/ma.py headhunter …`).
8. **`.claude/settings.json`** és a TELEPITES.md 5. pontja (engedélylista): `Bash(python3 -m metaelemzes.headhunter:*)`,
   `Bash(python -m metaelemzes.headhunter:*)`, `WebFetch(domain:api.elsevier.com)`.
9. **`ESZKOZOK_ES_HOZZAFERESEK.md`**, 6. pont (API-kulcsok) táblázatának frissítése és új alpont:

   | Változó | Mire | Hol szerzed be | Kötelező? |
   |---|---|---|---|
   | `MA_CONTACT_EMAIL` | az API-k „udvarias" azonosítása (User-Agent, NCBI `email`, OpenAlex/Crossref `mailto`) | a saját e-mail-címed | ajánlott |
   | `MA_OPENALEX_APIKEY` | saját OpenAlex-keret (kulcs nélkül a napi közös keret gyorsan elfogyhat) | ingyenes kulcs az OpenAlex oldalán (a kulcsigénylés útmutatója: help.openalex.org, „API authentication") | ajánlott |
   | `MA_SCOPUS_APIKEY` | Scopus-keresés és -hivatkozások | dev.elsevier.com → „I want an API key" (Elsevier-fiókkal; Magyarországon az intézményi Scopus-hozzáférés jellemzően az EISZ-en át — a könyvtáradnál ellenőrizd) | opcionális |
   | `MA_SCOPUS_INSTTOKEN` | intézményi jogosultság hálózaton kívülről (pl. otthonról) | az intézményi könyvtár / Elsevier-kapcsolattartó kéri az Elseviertől | opcionális |
   | `MA_NCBI_APIKEY` | PubMed 10 kérés/s (3 helyett) | ncbi.nlm.nih.gov → fiók → API Key Management | opcionális |
   | `MA_HH_CACHE_DIR` | a teljes szöveg ideiglenes gyorsítótára (a projekten kívül) | — | opcionális |

   Kiemelve: a kulcsot **soha** ne írd fájlba a projektben, parancssorba vagy csevegésbe; csak környezeti változóba.
   Ellenőrzés: `python -m metaelemzes.headhunter sources --check`.
10. **`TELEPITES.md`:** új alfejezet „Metaheadhunter: források és kulcsok" — környezeti változó beállítása
    Windows (`setx MA_SCOPUS_APIKEY "…"`, új terminál), macOS/Linux (`~/.zshrc`/`~/.bashrc`:
    `export MA_SCOPUS_APIKEY="…"`), proxy (`HTTPS_PROXY`); próba: `sources --check`; a Scopus élő igazolása
    (`MA_LIVE_TESTS=1 python3 -m unittest tests.test_headhunter_live`); hibaelhárítás (401/403/429, OpenAlex keret).
11. **`README.md`:** rövid szakasz (mit csinál, parancsok, hol vannak a fájlok, elvek N1–N4).
12. **`.gitignore`:** `**/01_kereses/headhunter/cache/` és `**/01_kereses/headhunter/runs/`.
13. **`tudasbazis/seed/tools.json`** (meglévő seed): az `openalex` sor megjegyzése (napi keret, ingyenes kulcs,
    `MA_OPENALEX_APIKEY`), a `scopus` sor (`MA_SCOPUS_APIKEY`/`MA_SCOPUS_INSTTOKEN`, `sources --check`).
14. **Audit-csomag** (`ma_gui/audit_export.py`): a `01_kereses/headhunter/cache/` és `runs/*/CANCEL` kizárása; a
    `decisions.jsonl` és a `prisma_flow.json` bevétele.
15. **Képesség-kézfogás:** `capabilities` komponensként `headhunter` (állapot: `ok`, ha a csomag importálható; a
    források állapota a `sources_status`-ból).
16. **PRISMA egyéb-módszerek ág a motorban és a munkapadon:** (a) `metaelemzes/prisma.py` `from_composer`: a
    `normalize` által visszaadott összes `REASON_FIELDS` okot tartsa meg (ma csak az `excluded_eligibility_reasons`-t),
    így composer-nevekkel sem jön hamis P008; (b) `ma_gui/routes/prisma.py` `COUNT_TO_FLOW`/`FLOW_KEYS` és a PRISMA-
    képernyő: az `other_methods_*` dobozok (+ `other_methods_excluded_reasons`) megjelenítése és szerkesztése.

---

## 22. Hivatkozások (ellenőrzött)

Minden tétel PubMed E-utilities-szel (`esearch` + `esummary`, 2026-10-05) ellenőrizve; a számszerű állításokat az
absztraktból (`efetch`), a CCA-sávokat és a képletet a nyílt teljes szövegből (Ying 2025, PMC12527530; Hennessy
2020, PMC8555740) olvastuk ki.

| # | Hivatkozás | PMID | DOI |
|---|---|---|---|
| 1 | Pieper D, Antoine SL, Mathes T, Neugebauer EA, Eikermann M. Systematic review finds overlapping reviews were not mentioned in every other overview. J Clin Epidemiol. 2014;67(4):368-75. | 24581293 | 10.1016/j.jclinepi.2013.11.007 |
| 2 | Hennessy EA, Johnson BT. Examining overlap of included studies in meta-reviews: guidance for using the corrected covered area index. Res Synth Methods. 2020;11(1):134-145. | 31823513 | 10.1002/jrsm.1390 |
| 3 | Ying X, Bougioukas KI, Pieper D, Mayo-Wilson E. Weighted corrected covered area (wCCA): a measure of informational overlap among reviews. Res Synth Methods. 2025;16(4):701-708. | 41626914 | 10.1017/rsm.2025.19 |
| 4 | Gøtzsche PC, Hróbjartsson A, Maric K, Tendal B. Data extraction errors in meta-analyses that use standardized mean differences. JAMA. 2007;298(4):430-7. | 17652297 | 10.1001/jama.298.4.430 |
| 5 | Jones AP, Remmington T, Williamson PR, Ashby D, Smyth RL. High prevalence but low impact of data extraction and reporting errors were found in Cochrane systematic reviews. J Clin Epidemiol. 2005;58(7):741-2. | 15939227 | 10.1016/j.jclinepi.2004.11.024 |
| 6 | Mathes T, Klaßen P, Pieper D. Frequency of data extraction errors and methods to increase data extraction quality: a methodological review. BMC Med Res Methodol. 2017;17(1):152. | 29179685 | 10.1186/s12874-017-0431-4 |
| 7 | Page MJ, McKenzie JE, Bossuyt PM, et al. The PRISMA 2020 statement: an updated guideline for reporting systematic reviews. BMJ. 2021;372:n71. | 33782057 | 10.1136/bmj.n71 |
| 8 | Rethlefsen ML, Kirtley S, Waffenschmidt S, et al. PRISMA-S: an extension to the PRISMA Statement for Reporting Literature Searches in Systematic Reviews. Syst Rev. 2021;10(1):39. | 33499930 | 10.1186/s13643-020-01542-z |
| 9 | Rethlefsen ML, Page MJ. PRISMA 2020 and PRISMA-S: common questions on tracking records and the flow diagram. J Med Libr Assoc. 2022;110(2):253-257. | 35440907 | 10.5195/jmla.2022.1449 |
| 10 | Garner P, Hopewell S, Chandler J, et al. When and how to update systematic reviews: consensus and checklist. BMJ. 2016;354:i3507. | 27443385 | 10.1136/bmj.i3507 |
| 11 | Shojania KG, Sampson M, Ansari MT, et al. How quickly do systematic reviews go out of date? A survival analysis. Ann Intern Med. 2007;147(4):224-33. | 17638714 | 10.7326/0003-4819-147-4-200708210-00179 |
| 12 | Aromataris E, Fernandez R, Godfrey CM, Holly C, Khalil H, Tungpunkom P. Summarizing systematic reviews: methodological development, conduct and reporting of an umbrella review approach. Int J Evid Based Healthc. 2015;13(3):132-40. | 26360830 | 10.1097/XEB.0000000000000055 |
| 13 | Pollock M, Fernandes RM, Becker LA, Pieper D, Hartling L. Chapter V: Overviews of Reviews. In: Higgins JPT et al. (eds). Cochrane Handbook for Systematic Reviews of Interventions, v6.5. Cochrane; 2024. (KB: `cochrane_handbook`; nem PubMed-indexelt, a cochrane.org fejezetoldalán ellenőrizve) | — | — |
| 14 | Gates M, Gates A, Pieper D, et al. Reporting guideline for overviews of reviews of healthcare interventions: development of the PRIOR statement. BMJ. 2022;378:e070849. | 35944924 | 10.1136/bmj-2022-070849 |
| 15 | Ballard M, Montgomery P. Risk of bias in overviews of reviews: a scoping review of methodological guidance and four-item checklist. Res Synth Methods. 2017;8(1):92-108. | 28074553 | 10.1002/jrsm.1229 |
| 16 | Pollock M, Fernandes RM, Newton AS, Scott SD, Hartling L. A decision tool to help researchers make decisions about including systematic reviews in overviews of reviews of healthcare interventions. Syst Rev. 2019;8(1):29. | 30670086 | 10.1186/s13643-018-0768-8 |
| 17 | Hirt J, Nordhausen T, Appenzeller-Herzog C, Ewald H. Citation tracking for systematic literature searching: a scoping review. Res Synth Methods. 2023;14(3):563-579. | 37042216 | 10.1002/jrsm.1635 |
| 18 | Hirt J, Nordhausen T, Fuerst T, Ewald H, Appenzeller-Herzog C; TARCiS study group. Guidance on terminology, application, and reporting of citation searching: the TARCiS statement. BMJ. 2024;385:e078384. | 38724089 | 10.1136/bmj-2023-078384 |
| 19 | Bramer WM, Giustini D, de Jonge GB, Holland L, Bekhuis T. De-duplication of database search results for systematic reviews in EndNote. J Med Libr Assoc. 2016;104(3):240-3. | 27366130 | 10.3163/1536-5050.104.3.014 |
| 20 | McKeown S, Mir ZM. Considerations for conducting systematic reviews: evaluating the performance of different methods for de-duplicating references. Syst Rev. 2021;10(1):38. | 33485394 | 10.1186/s13643-021-01583-y |
| 21 | Hair K, Bahor Z, Macleod M, Liao J, Sena ES. The Automated Systematic Search Deduplicator (ASySD): a rapid, open-source, interoperable tool to remove duplicate citations in biomedical systematic reviews. BMC Biol. 2023;21(1):189. | 37674179 | 10.1186/s12915-023-01686-z |
| 22 | Tramèr MR, Reynolds DJ, Moore RA, McQuay HJ. Impact of covert duplicate publication on meta-analysis: a case study. BMJ. 1997;315(7109):635-40. | 9310564 | 10.1136/bmj.315.7109.635 |
| 23 | von Elm E, Poglia G, Walder B, Tramèr MR. Different patterns of duplicate publication: an analysis of articles used in systematic reviews. JAMA. 2004;291(8):974-80. | 14982913 | 10.1001/jama.291.8.974 |
| 24 | Colditz GA, Brewer TF, Berkey CS, et al. Efficacy of BCG vaccine in the prevention of tuberculosis. Meta-analysis of the published literature. JAMA. 1994;271(9):698-702. | 8309034 | — |
| 25 | Morvaridzadeh M, Nachvak SM, Agah S, et al. Effect of soy products and isoflavones on oxidative stress parameters: a systematic review and meta-analysis of randomized controlled trials. Food Res Int. 2020;137:109578. (KB: `morvaridzadeh2020`) | 33233189 | 10.1016/j.foodres.2020.109578 |

**Teszthorgonyként használt (ellenőrzött) közlemények:** Kashangura 2019 (PMID 31038197, PMC6488980); Roy 2014
(PMID 25097193, PMC4122754); Mangtani 2014 (PMID 24336911); Abubakar 2013 (PMID 24021245); Martinez 2022 (PMID
35961354); Tameris 2013 (PMID 23391465, NCT00953927); Gholami 2025 (PMID 40355968, PMC12070792); Asbaghi 2020 (PMID
32979840); Rezazadegan 2021 (PMID 32814603); Khodarahmi 2019 (PMID 30314925); Bajerska 2022 (PMID 34642749).

**Műszaki dokumentáció (nem irodalmi hivatkozás; a build-ágens ezekből dolgozik, a Scopus-oldalak innen nem
érhetők el):** NCBI E-utilities súgó (ncbi.nlm.nih.gov/books/NBK25499); Europe PMC REST API (europepmc.org/
RestfulWebService); OpenAlex API (docs.openalex.org; hitelesítés: help.openalex.org); Elsevier Scopus Search API és
Abstract Retrieval API (dev.elsevier.com/documentation/ScopusSearchAPI.wadl, …/AbstractRetrievalAPI.wadl; keresési
szintaxis: dev.elsevier.com/sc_search_tips.html); ClinicalTrials.gov API v2 (clinicaltrials.gov/data-api/api).

---

## 23. Nyitott kérdések és kockázatok

1. **Scopus élőben nem igazolt innen.** A kliens a dokumentáció alapján készül; a pontos mezőnevek (pl. a `view=REF`
   válasz hivatkozás-mezői, a `ORIG-LOAD-DATE` szintaxis) a felhasználó gépén igazolandók (`sources --check`,
   élő teszt), utána a kazetták újrarögzítendők. Addig a Scopus-eredmények mellett „élőben nem igazolt" jelzés áll.
2. **OpenAlex-keret.** Kulcs nélkül a listás lekérdezések napi közös kerete gyorsan elfogyhat (itt is elfogyott);
   a kód ilyenkor kihagyja a forrást. Ajánlás: ingyenes kulcs.
3. **Táblázat-felismerés.** A kiadói JATS-ek változatosak; a nem felismert szerkezet a (b) ágens-útra esik
   (alacsonyabb bizonyosság, több emberi munka). A felismerők bővítése fixture-alapon történjen.
4. **Keresési dátum** gyakran pontatlan vagy hiányzik; a tartalék szándékosan óvatos (−12 hónap), és emberi
   jóváhagyást kér.
5. **Átfedési ablak** (6 hónap) pragmatikus alapérték — nem irodalmi szabály; a felhasználó dönt.
6. **A bányászott halmaz korlátja:** a forrás-áttekintések keresési hiányait örökli; a riport ezt kimondja, és a
   protokoll szerinti teljes keresést (D-S03-001) nem helyettesíti.
7. **Másodlagos adatok:** az EP6 jelentős kézi munka; a felület munkalistával és lokátorokkal segít, de a számot
   ember ellenőrzi.
