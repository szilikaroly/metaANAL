# Metaheadhunter — JSON-szerződések (`szk.ma.headhunter/v1`)

A Metaheadhunter (meglévő metaanalízisek bányászata) fájljainak sémái. A terv: `TERV_metaheadhunter.md`
4. fejezet. A sémák a motor `metaelemzes/contracts/README.md` konvencióját követik:

- `$id` = `urn:szk:contract:<név>:1`, a dokumentum `schema` mezője `szk.<név>/v1`;
- kanonikus formázás: `json.dumps(séma, indent=2, ensure_ascii=False) + "\n"`;
- csak a `ma_gui.schema_lite` által ismert kulcsszavak; nincs `/<szó>:<szó>` minta (marketplace slash-lint);
- **bővítés csak additív** (új, nem kötelező mező); kötelező mező törlése vagy jelentésváltozása új főverzió
  (`…v2.schema.json`). A fogyasztó az ismeretlen mezőt figyelmen kívül hagyja (a sémák `additionalProperties`-t
  csak ott tiltanak, ahol a kulcsok köre zárt).

Ezek a sémák **nincsenek** a `metaelemzes.contracts` regiszterében (a motor szerződés-tesztjei nem látják őket);
futásidőben a `metaelemzes.headhunter.state.validate(doc, név)` tölti be őket (`ma_gui.schema_lite`, ha nem
érhető el: minimális ellenőrzés a kötelező kulcsokra és a `schema`-konstansra).

| Fájl | Séma | Ki írja | Megjegyzés |
|---|---|---|---|
| `01_kereses/headhunter/state.json` | `ma.headhunter.state.v1` | `state.py` (minden lépés) | PICO, okszótár, források (csak igen/nem a kulcsokról), lépések, EP1–EP6, PRISMA-S keresési napló |
| `reviews/<review_id>.json` | `ma.headhunter.review.v1` | `finder.py`, `included.py`, CLI (`confirm`/`exclude`) | forrás-áttekintés + bevont-jelöltek + bizonyítékok (≤ 300 karakteres idézet) |
| `studies.json` | `ma.headhunter.studies.v1` | `resolve.py`, `dedup.py`, `update.py` | rekordok (közlemények), vizsgálat-klaszterek, javaslatok |
| `decisions.jsonl` (soronként) | `ma.headhunter.decision.v1` | `state.append_decision`, `dedup.append_decision` | csak hozzáfűzés, sha256-hash-lánc (H015); betegadat-őr (N9) |
| `update_search.json` | `ma.headhunter.update-search.v1` | `update.py` | frissítő keresés ablaka, lekérdezései, eredménye; hivatkozáskövetés |
| `overlap.json` | `ma.headhunter.overlap.v1` | `overlap.py` | hivatkozási mátrix, CCA |
| `merged.json` | `ma.headhunter.merged.v1` | `merge.py` | az egyesített vizsgálatkészlet proveniencával, másodlagos adatokkal |
| kazetták | `ma.headhunter.cassette.v1` | tesztrögzítő | offline tesztek (redaktálva) |
| `prisma_flow.json` | `szk.prisma-flow/v1` (motor) | `prisma_map.py` | kanonikus motor-dobozkulcsok + `hh` részletek; `ma.py prisma check --json …` |

Közös definíciók: `ma.headhunter.common.v1` (`ts`, `date`, `actor`, `idval`, `ids`, `bib`, `evidence`, `locator`,
`secondary_value`, `retrieval`, `source_cfg`, `step_status`, azonosító-minták).

## Additív bővítések (build, 2026-10-05)

A build során felvett, nem kötelező mezők (a régi dokumentumok érvényesek maradnak):

- **decision**: `field`, `review_id`, `outcome`, `arm`, `primary_locator`, `primary_value`, `secondary_value`
  (EP6 `secondary_verify`: melyik másodlagos értéket, hol ellenőrizte az elsődleges közleményben);
  `content_sha256`, `studies_included`, `reports_included` (EP5 `checkpoint`/`signoff`: a lezárt halmaz
  ujjlenyomata — ha a `merged.json` tartalma később eltér, a lezárás érvénytelen, H017).
- **update-search**: `generated`; `window.earliest_source_search`, `window.warnings`, `window.per_review[].basis`
  (`reported` | `fallback` | `fallback_pubdate` | `missing`); `queries[].complete`, `date_from`, `date_to`;
  `citation_search[].retrieved`, `since_year`, `iterations`, `seed_kind`, `run_at` (TARCiS);
  `results.unusable` (PRISMA D3), `known_from_update`, `by_source`, `databases_retrieved` (A1),
  `registers_retrieved` (A2); `citation_results`, `sources`, `warnings`, `cancelled`.
- **merged**: `content_sha256`, `dropped`, `summary` (EP-k nyitott tételei); `studies[].n_reviews`;
  `reports[].role_source`, `year`, `branch` (`other` | `database` | `null` — PRISMA-ág), `routes`, `resolution`,
  `screening_status`, `eligibility`; `provenance[].confidence`; `conflicts[].arm`.
- **state**: `exclusion_reasons[].domain` (a PICO-terület, amelyhez az ok tartozik).

## Additív bővítések (integráció és élő próba, 2026-10-05)

A sémák nem változtak (a mezők a meglévő objektumokba additívan kerülnek; a sémák nem tiltják őket):

- **review**: `flags` (`retracted`, `erratum`, `narrative_suspect`, `updates`, `unverified_live`) és `proposal`
  (`{action: exclude|check, reason{hu,en}}`) — a felkutatás gépi jelzései a fájlban is megmaradnak;
  `fulltext.note` (`pmc_no_body`: a PMC csak címlapot adott); `extraction_runs[].reflist_tried`
  (`[{source, n, note}]`, `note: list_quota` = OpenAlex listás keret helyett egyedi lekérések) és `reflist_source`;
  `candidates[].role_origin` (`{extracted: unknown, set_by: d-…}` — az EP2-ben megerősített irodalomjegyzék-
  hivatkozás bevont vizsgálattá válik).
- **decision**: `id_confirm` célja lehet `record` is, `value: no_identifier` — emberi nyilatkozat, hogy a közleménynek
  nincs API-ban elérhető azonosítója (indoklás kötelező; csak feloldatlan rekordra).
- **merged**: `studies[].flags` új értéke `no_identifier_acknowledged` (az `unresolved_ids` helyett, ha a fenti
  nyilatkozat megvan); a lezárás (EP5) `unresolved_ids` mellett nem engedett (H003).
- **state**: alap `criteria` (`E1`: nem elsődleges közlemény — publikációtípus-javaslat; `E2`: emberi vizsgálat), ha
  az `init` nem kapott saját kritériumot; az `X6`/`X1` ok ezekhez kötve.
- **studies**: új javaslat-szabály `L4-title-series` (`same_study`, `possible`): (majdnem) azonos cím, legalább
  2 év különbség — követéses jelentés-sorozat tippje.
- **resolution notes**: `accepted_author_form_differs`, `accepted_year_differs` (a cím szó szerint egyezik, a
  szerzőalak/év eltérése tűrt — azonosító-ellenőrzésnél).

## Additív bővítések (független felülvizsgálat, 2026-10-05)

A sémák nem változtak (minden új érték a meglévő mezőkben, additívan):

- **studies**: új javaslat-szabály `L1-bib-mismatch` (`kind: id_conflict`, `pending`) — közös megbízható azonosító,
  de a cím-hasonlóság < 0,50 és az első szerző/év sem egyezik: nincs automatikus összevonás, ember dönt. A
  `resolution.status` `unresolved`/`ambiguous` rekord azonosítói nem L1-kulcsok (`dedup.trusted_ids` üres), és nem
  kerülnek a vizsgálat `registry_ids`-ébe.
- **decision**: `final_inclusion` + `value: keep_retracted` (cél: `study`, indoklás kötelező) — visszavont
  közleményű vizsgálat tudatos megtartása (H013); `id_confirm` célja lehet `candidate` (`rv-…#c…`) a CLI-ből is
  (`decide --target rv-…#c… --value pmid:|doi:|pmcid:|nct:|eid:|openalex:…`).
- **merged**: `studies[].flags` új értéke `retracted_retention_documented`; a `content_sha256` ujjlenyomatban a
  másodlagos adat EP6-mezői (`status` → egységesen `unverified`, `verified_decision`, `primary_locator`,
  `primary_value`) nem számítanak — az EP6 a lezárás (EP5) után jön, és nem érvényteleníti azt.
- **resolution notes**: `human_choice_bib_mismatch` (a felhasználó által megadott azonosító közleménye nem egyezik a
  hivatkozással — a döntés érvényes, figyelmeztetéssel).
- **figyelmeztetés-kódok** (CLI/`resolve`): `id_title_mismatch`, `resolution_year_differs`.
- **prisma_flow.json**: elavult vagy hiányzó `update_search.json` mellett a program NEM igazítja ki az azonosított
  számot (korábban: A1 megemelése / A1 = B) — a motor P002-t jelez (H011), és a lezárás nem lehetséges;
  `hh.no_database_branch` csak akkor igaz, ha nincs adatbázis-ági rekord.

## Elvek, amelyeket a sémák kikényszerítenek

- **N1 — nincs kitalált azonosító**: minden azonosító `idval` (`value`, `source`, `via`, `at`); a `review`/`user`
  forrású csak `confirmed_by` mellett számít megbízhatónak.
- **N1 — bizonyíték**: minden jelöltnek legalább egy `evidence_ids` eleme van; a bizonyíték `quote` mezője
  1–300 karakter.
- **N2 — másodlagos adat**: `secondary_value.status` ∈ `unverified` | `verified` | `discrepant` |
  `not_applicable`; a `verified`-hez `verified_decision` (emberi EP6-döntés) tartozik.
- **N3 — ember dönt**: a `decision.actor` `user:` / `agent:` / `tool:` előtagú; az emberi döntés-fajtákat a
  program csak `user:` szereplőtől fogadja el.
- **N6 — kulcsok**: a `source_cfg` csak `key_configured` / `insttoken_configured` igen/nem értéket tárol.
