# -*- coding: utf-8 -*-
"""Metaheadhunter — meglévő metaanalízisek bányászata: a munkapad végpontjai (TERV_metaheadhunter.md 17. fejezet).

A felület varázslója (``screens/headhunter*.js``, a 2 PRISMA fül alképernyője) ezekből dolgozik. A route a
headhunter-csomagot NEM importálja (6.8: a ``ma_gui`` a motorhoz csak a ``metaelemzes.api``-n át fér hozzá):

- **olvasás**: a ``01_kereses/headhunter/`` JSON-fájljai a tárolón át (``store.load_json``: ETag = a fájl sha256-ja),
  a ``metaelemzes/headhunter/contracts/`` sémáival ellenőrizve (``schema_lite``; a sémahiba nem állítja meg a
  nézetet, ``problems``-ként jelenik meg — H001); a PRISMA-számokat a motor ellenőrzi (``api.prisma_check``);
- **írás és hosszú (hálózati) lépés**: mindig a headhunter PARANCSSORA alfolyamatként
  (``python -m metaelemzes.headhunter <parancs> <projekt> --json …``, ``jobs.run_subprocess``: shell nélkül, szűrt
  környezettel — a proxy- és kulcsváltozók átmennek, a PYTHONPATH nem), HÁTTÉRSZÁLBAN, lépésenkénti időkorláttal.
  A kiszolgáló szála soha nem vár hálózatra: a ``POST …/run`` azonnal 202-t ad, a felület a ``GET …/runs/<id>``-t
  kérdezi (a CLI ``runs/<run_id>/progress.jsonl``-jének vége is benne). Megszakítás: a CLI ``CANCEL`` fájlja
  (együttműködő), végső korlát az időkorlát (a folyamatfa leáll). Egyszerre egy író futás projektenként.

  Miért nem a meleg worker? A munkapad egyetlen meleg workere az elemzésekké (``JOB_ALLOWED_MODULES``: csak
  ``metaelemzes.api`` és a commit-burok); egy több perces hálózati lépés ott minden elemzést feltartana, az
  időkorlátos leállítás pedig a workert is újraindítaná. Az alfolyamat külön leállítható, és a CLI-vel bájtra
  azonos fájlokat ír (a terv 17. fejezete is így rögzíti).

Végpontok (a boríték ``data``-ja; ETag = a mögöttes fájl sha256-ja, a ``status``-nál az összes fájlé együtt):

- ``GET /api/headhunter/status[?cli=0]`` → állapot, források, lépések, ellenőrzőpontok (EP1–EP6), darabszámok,
  fájlok, sémahibák, a CLI ``status --json`` kivonata (H-kódok, nyitott EP-k, következő lépés), futások.
- ``GET /api/headhunter/sources[?lang=en]`` → a források táblázata a CLI-ből (hálózat nélkül; indulás előtt is).
- ``GET /api/headhunter/reviews[?status=]``, ``GET /api/headhunter/reviews/<review_id>``
- ``GET /api/headhunter/studies[?section=records|studies|proposals]``, ``GET /api/headhunter/proposals[?status=&kind=]``
  (a javaslat tételei a rekordok összefoglalójával, az egymás melletti összevetéshez)
- ``GET /api/headhunter/overlap`` (a ``cca_text`` a tárolt, motor által kerekített érték „%.1f” alakja — a motor
  saját CSV-jével és értelmezésével azonos), ``GET /api/headhunter/merged[?status=]``,
  ``GET /api/headhunter/prisma`` (+ ``api.prisma_check``), ``GET /api/headhunter/update``,
  ``GET /api/headhunter/decisions[?limit=]``
- ``POST /api/headhunter/run`` ← ``{step, options}`` → 202 ``{job}``; ``GET /api/headhunter/runs[/<id>][?wait=]``;
  ``POST /api/headhunter/runs/<id>/cancel`` (a GUI-feladat vagy egy CLI-futás azonosítója)
- ``POST /api/headhunter/decide`` ← ``{kind, target?, value?, level?, reason_code?, reason?, batch?, options?}``
  + ``If-Match`` (a cél fájl ETag-je) → 200 a CLI döntés-borítékával (vagy 202, ha még fut).

Szabályok: a döntés szereplője a munkamenet felhasználója (``user:<app.actor>``), soha nem a kérés törzséből; a
szabad szöveg (indoklás, PICO, lokátor) a PHI-őrön megy át (``phi_doc_guard``: TAJ-gyanú, születési dátum, e-mail →
403, érték nélkül); felhasználói érték csak validált kapcsolóként, ``--kapcsoló=érték`` alakban kerül az argv-be.
A CLI kimenete (és a stderr vége) a route-ban is redaktált (kulcsok, e-mail, ``api_key``/``mailto`` paraméterek —
H016), az abszolút projektút helyén ``<projekt>`` áll. Hibakódok (a ``details.hh_code`` a terv kódja):
``HH_NOT_INITIALIZED`` → 409 CONFLICT; ``HH_CHECKPOINT_PENDING`` → 409 GATE_BLOCKED; ``HH_RUN_ACTIVE`` → 409
CONFLICT; ``PRECONDITION_FAILED`` (If-Match) → 409 CONFLICT; ``HH_CLI_FAILED`` → 502 PLUGIN_FAILED (a stderr vége
redaktálva); ``HH_TIMEOUT`` → 504 TIMEOUT; érvénytelen döntés → 422 VALIDATION a CLI magyar üzenetével. Elérhetetlen
forrás (CLI 3-as kód) nem hiba: a feladat ``done``, ``partial: true``, ``hh_code: HH_SOURCE_UNAVAILABLE``.

Statisztikát a route nem számol: minden szám a headhunter fájljaiból / a CLI-ből / a motorból jön."""
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from metaelemzes import api

from .. import jobs, schema_lite, store
from ..router import ApiError, Result
from ._common import if_match, int_arg, log_activity_or_warn, num_arg, phi_doc_guard

# ---------------------------------------------------------------------------- utak és sémák
HH_REL = "01_kereses/headhunter"
STATE_REL = HH_REL + "/state.json"
STUDIES_REL = HH_REL + "/studies.json"
DECISIONS_REL = HH_REL + "/decisions.jsonl"
UPDATE_REL = HH_REL + "/update_search.json"
OVERLAP_REL = HH_REL + "/overlap.json"
MERGED_REL = HH_REL + "/merged.json"
PRISMA_REL = HH_REL + "/prisma_flow.json"
REVIEWS_REL = HH_REL + "/reviews"
RUNS_REL = HH_REL + "/runs"
FILES = (("state", STATE_REL, "state"), ("studies", STUDIES_REL, "studies"), ("update", UPDATE_REL, "update-search"),
         ("overlap", OVERLAP_REL, "overlap"), ("merged", MERGED_REL, "merged"), ("prisma", PRISMA_REL, None))
CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "metaelemzes" / "headhunter" / "contracts"
CLI_MODULE = "metaelemzes.headhunter"
CLI_PREFIX = None               # tesztekben felülírható: [python, szkript] (alapból: [sys.executable, "-m", CLI_MODULE])
ENGINE_ROOT = jobs.ROOT         # a motor gyökere (a ``-m`` innen találja a csomagot)

S_STATUS = "szk.ma.hh-status/v1"
S_REVIEWS = "szk.ma.hh-reviews/v1"
S_REVIEW = "szk.ma.hh-review/v1"
S_STUDIES = "szk.ma.hh-studies/v1"
S_PROPOSALS = "szk.ma.hh-proposals/v1"
S_OVERLAP = "szk.ma.hh-overlap/v1"
S_MERGED = "szk.ma.hh-merged/v1"
S_PRISMA = "szk.ma.hh-prisma/v1"
S_UPDATE = "szk.ma.hh-update/v1"
S_DECISIONS = "szk.ma.hh-decisions/v1"
S_JOB = "szk.ma.hh-job/v1"
S_JOBS = "szk.ma.hh-jobs/v1"
S_SOURCES = "szk.ma.hh-sources/v1"

STEP_ORDER = ("sources", "find_reviews", "select_reviews", "extract", "resolve", "dedupe", "overlap", "screen",
              "update_search", "merge", "prisma", "export")
CHECKPOINTS = ("EP1", "EP2", "EP3", "EP4", "EP5", "EP6")
SOURCE_KEYS = ("pubmed", "europepmc", "openalex", "scopus", "ctgov", "crossref")
API_SOURCES = frozenset(("pubmed", "europepmc", "openalex", "scopus", "ctgov", "crossref", "pmc"))
ID_KEYS = ("pmid", "pmcid", "doi", "nct", "eid", "openalex")

MAX_ITEMS = 5000                # rekordok, vizsgálatok, átfedési sorok (= security.MAX_ROWS)
MAX_REVIEWS = 1000
MAX_PROPOSALS = 2000
MAX_TEXT = 2000
MAX_REASON = 4000
MAX_JOB_DATA = 256 * 1024       # a CLI ``data``-ja ekkora JSON-ig kerül a feladat-pillanatképbe
KEEP_JOBS = 32
PROGRESS_TAIL = 20
DECIDE_WAIT = 25.0              # s; a döntés ennyit vár a CLI-re, utána 202 + lekérdezés
STATUS_CLI_TIMEOUT = 30.0
MAX_WAIT = 10.0

_RV = r"rv-[a-z0-9][a-z0-9-]{2,80}"
RV_RE = re.compile(r"^%s$" % _RV)
CAND_RE = re.compile(r"^(%s)#(c\d{4,5})$" % _RV)
PROP_RE = re.compile(r"^p-[a-z0-9][a-z0-9-]{2,80}$")
REC_RE = re.compile(r"^rec-[a-z0-9][a-z0-9-]{2,80}$")
STUDY_RE = re.compile(r"^st-\d{4,6}$")
RUN_RE = re.compile(r"^(\d{8}T\d{6}Z)-[0-9a-f]{6}$")
JOB_RE = re.compile(r"^hh-[0-9a-f]{12}$")
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OUTCOME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
FIELD_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
EV_RE = re.compile(r"^ev-[a-z0-9][a-z0-9#.-]{2,120}$")
REASON_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,16}$")
VALUE_RE = re.compile(r"^(accept|reject|include|exclude|not_retrieved|awaiting|no_identifier|keep_retracted|"
                      r"pmid:\d{1,9}|option:\d{1,2}|doi:10\.\d{3,9}/[^\s]{1,200}|pmcid:PMC\d{1,9}|nct:NCT\d{8}|"
                      r"eid:2-s2\.0-\d{5,20}|openalex:W\d{1,12})$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ACTOR_SAFE = re.compile(r"[^A-Za-z0-9_.@-]")

# kulcsok és kapcsolattartási adat: SOHA nem kerülhetnek a válaszba (N6, H016)
SECRET_ENV = ("MA_SCOPUS_APIKEY", "MA_SCOPUS_INSTTOKEN", "MA_OPENALEX_APIKEY", "MA_NCBI_APIKEY", "MA_CONTACT_EMAIL")
REDACTED = "«redacted»"
_SECRET_PARAM_RE = re.compile(r"(?i)\b(api_key|apikey|insttoken|mailto|email|tool)=([^&\s\"'<>]+)")
_SECRET_HEADER_RE = re.compile(r"(?i)\b(authorization|x-els-apikey|x-els-insttoken|cookie|set-cookie)\s*:\s*([^\r\n]+)")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}")

MSG_NOT_INIT = ("A Metaheadhunter még nincs elindítva ebben a projektben (nincs 01_kereses/headhunter/state.json). "
                "Kezdd a „Kérdés (PICO)” lépéssel (parancssorból: ma.py headhunter init <projekt> --question …).")
MSG_BUSY = ("Már fut egy Metaheadhunter-lépés ebben a projektben. Várd meg, vagy szakítsd meg (Megszakítás), "
            "aztán próbáld újra.")
MSG_IF_MATCH = ("A döntéshez kell az If-Match fejléc (a cél fájl ETag-je, ahogy a felület látta) — így nem írsz "
                "felül olyan változást, amit még nem láttál.")
MSG_STALE = ("A(z) %s közben megváltozott (például egy ágens vagy a parancssor írta). Frissítsd a nézetet, "
             "nézd át, majd dönts újra.")
MSG_CLI_FAILED = ("A Metaheadhunter parancssora váratlanul leállt vagy érthetetlen választ adott. A részletek "
                  "(redaktálva) a details.stderr_tail mezőben; próbáld a parancsot terminálban is: "
                  "ma.py headhunter status <projekt>.")
MSG_TIMEOUT = "A Metaheadhunter-lépés túllépte az időkorlátot (%d s), ezért leállítottuk. A már letöltött adat megmaradt."


# „Mit jelent?” — kezdőknek szóló magyarázatok lépésenként (a felület a status ``help`` mezőjéből mutatja; a hosszú
# szövegek így nem a felület 900 KB-os keretét terhelik). Forrásaik a TERV_metaheadhunter.md 22. fejezetében ellenőrzöttek.
HELP = {
    'intro': {
        "hu": "Ez az előző áttekintések és a hivatkozáskövetés („egyéb módszerek”) ága: kiegészíti, de nem helyettesíti a protokoll szerinti adatbázis-keresést. A bányászott halmaz örökli a forrás-áttekintések keresési hiányait; ezt a frissítő keresés és a saját szűrés csak részben pótolja. Az áttekintésekből átvett számok másodlagos adatok: elemzés előtt az eredeti közleménnyel ellenőrizni kell őket (EP6). Teljes szöveget a program nem ment; csak bibliográfiai adatot és legfeljebb 300 karakteres idézetet.",
        "en": "This is the 'other methods' branch (previous reviews and citation searching): it complements but does not replace the protocol's database search. The mined set inherits the source reviews' search gaps; the update search and your own screening only partly compensate. Numbers taken from reviews are secondary data and must be checked against the primary report before analysis (EP6). The tool never stores full texts — only bibliographic data and quotes of at most 300 characters."},
    'sources': {
        "hu": "PubMed, Europe PMC és ClinicalTrials.gov kulcs nélkül működik; az OpenAlex kulcs nélkül napi közös keretből dolgozik (ingyenes saját kulcs: MA_OPENALEX_APIKEY); a Scopus csak kulccsal (MA_SCOPUS_APIKEY, intézményen kívülről MA_SCOPUS_INSTTOKEN is). A kulcsot SOHA ne írd ide vagy fájlba: csak környezeti változóba (lásd TELEPITES.md), a felület csak azt mutatja, be van-e állítva. Ha egy forrás nem érhető el, a többivel folytatjuk, és a hiányt a keresési napló kimondja.",
        "en": "PubMed, Europe PMC and ClinicalTrials.gov work without a key; OpenAlex without a key uses a shared daily budget (free own key: MA_OPENALEX_APIKEY); Scopus only with a key (MA_SCOPUS_APIKEY, plus MA_SCOPUS_INSTTOKEN off campus). NEVER type a key here or into a file: only into an environment variable (see TELEPITES.md); the screen only shows whether it is set. If a source is unreachable we continue with the others and the search log states the gap."},
    'pico': {
        "hu": "A populáció (P) és a beavatkozás (I) kifejezéseiből épül az áttekintés-keresés (P ÉS I + SR/MA-szűrő). A kimenet-blokk szűkíthet, de bányászatnál inkább ne. A kizárási okok szótára (X1…) a szűréshez és a PRISMA kizárási okaihoz kell. A „saját áttekintés frissítése” mód akkor kell, ha a korábbi, saját közölt áttekintésedet frissíted.",
        "en": "The review search is built from the population (P) and intervention (I) terms (P AND I + SR/MA filter). An outcome block can narrow it, but avoid that when mining. The exclusion reason list (X1…) is used in screening and for the PRISMA exclusion reasons. Use 'update my own review' mode when you update your own previously published review."},
    'reviews': {
        "hu": "Bányászathoz MINDEN PICO-ba illő szisztematikus áttekintést érdemes kiválasztani, nem csak a „legjobbat”: az átfedést a duplumszűrés kezeli, a kihagyott áttekintés viszont vizsgálatokat veszíthet. A pontszám csak sorrend (relevancia, frissesség, méret, Cochrane, nyílt teljes szöveg), nem minőségítélet; az AMSTAR 2 jellegű jelzések (protokoll, kockázatértékelés, PRISMA) csak tájékoztatnak. A választásban Pollock 2019 döntési eszköze és Ballard & Montgomery 2017 négy feltétele segít; a minőség főleg a másodlagos adatok megbízhatóságát érinti.",
        "en": "When mining, select EVERY systematic review that fits your PICO, not only the 'best' one: overlap is handled by de-duplication, while an omitted review can lose studies. The score is only an ordering (relevance, recency, size, Cochrane, open full text), not a quality judgement; AMSTAR 2-like signals (protocol, risk of bias, PRISMA) are informative only. Pollock 2019's decision tool and Ballard & Montgomery 2017's four conditions help the choice; quality mainly affects the reliability of secondary data."},
    'extract': {
        "hu": "Csak a BEVONT vizsgálatokat keressük, nem a teljes irodalomjegyzéket. A legmegbízhatóbb a Cochrane „References to studies included in this review” szakasza (magas bizonyosság); utána a bevont vizsgálatok táblázata (közepes); az irodalomjegyzék + ágens-osztályozás alacsony bizonyosságú, emberi megerősítés kell. Minden tételnél ott a hely (táblázat, sor, szakasz) és a szó szerinti idézet — ha nincs bizonyíték, az hiba (H002). Ha a kinyert vizsgálatok száma eltér az áttekintés által közölttől, figyelmeztetünk (H006). A keresési dátum a frissítéshez kell; ha nincs közölve, a megjelenés előtti 12 hónap a becslés (H008), ezt neked kell jóváhagynod.",
        "en": "We look for INCLUDED studies only, not the whole reference list. Most reliable is the Cochrane section 'References to studies included in this review' (high confidence); next the table of included studies (medium); the reference list + agent classification is low confidence and needs human confirmation. Every item shows its location (table, row, section) and a verbatim quote — no evidence is an error (H002). If the number of extracted studies differs from what the review reports, we warn you (H006). The search date is needed for the update; if not reported, publication date minus 12 months is the estimate (H008), which you must approve."},
    'dedupe': {
        "hu": "Automatikusan csak az azonos azonosítójú (PMID, DOI, PMCID, EID) közlemények vonódnak össze — ez is visszavonható. Minden más (hasonló cím és szerző; közös regiszterszám; az áttekintés csoportosítása) csak javaslat. Kétség esetén tartsd meg külön (Bramer 2016; McKeown 2021): a hamis összevonás vizsgálatot veszíthet. A társközleményeket (követés, másodlagos elemzés) a vizsgálathoz kapcsoljuk — az elemzési egység a vizsgálat, nem a cikk. A ClinicalTrials.gov hivatkozástípusa önmagában nem elég a kapcsoláshoz; a közlemény saját regisztrációs nyilatkozata az erős jel.",
        "en": "Only reports with an identical identifier (PMID, DOI, PMCID, EID) are merged automatically — and this can be undone. Everything else (similar title and author; shared registration; the review's grouping) is only a proposal. When in doubt keep them separate (Bramer 2016; McKeown 2021): a false merge can lose a study. Companion reports (follow-up, secondary analysis) are linked to the study — the unit of analysis is the study, not the article. A ClinicalTrials.gov reference type alone is not enough for linking; the report's own registration statement is the strong signal."},
    'overlap': {
        "hu": "Corrected Covered Area (Pieper 2014): CCA = (N − r) / (r·c − r), ahol N a bejelölt cellák száma, r az egyedi vizsgálatok, c az áttekintések száma. Sávok (útmutató, nem merev szabály; Ying 2025): 0–5% enyhe, 6–10% mérsékelt, 11–15% magas, 15% fölött nagyon magas. Bányászatnál az átfedés nem torzítás (a duplumokat összevonjuk): azt mutatja, mennyire ugyanazt az irodalmat találták. Nagyon magas páronkénti átfedésnél (pl. ugyanazon szerzők frissítése) a régebbi áttekintés helyettesíthető; alacsony átfedés azonos PICO mellett eltérő kritériumokra vagy keresési hiányra utal (Hennessy & Johnson 2020). A számok a motor értékei.",
        "en": "Corrected Covered Area (Pieper 2014): CCA = (N − r) / (r·c − r), where N is the number of ticked cells, r the unique studies and c the reviews. Bands (guidance, not strict rules; Ying 2025): 0–5% slight, 6–10% moderate, 11–15% high, above 15% very high. When mining, overlap is not a bias (duplicates are merged): it shows how far the reviews found the same literature. With very high pairwise overlap (e.g. an update by the same authors) the older review can be replaced; low overlap with the same PICO suggests different criteria or search gaps (Hennessy & Johnson 2020). The numbers are the engine's values."},
    'screen': {
        "hu": "Az, hogy egy korábbi áttekintés bevonta, csak kontextus — a te kritériumaid eltérhetnek. Cím/absztrakt szinten kizárhatsz ok nélkül is; teljes szöveg szinten az ok kötelező (PRISMA 16a). „Nem szerezhető be”: a teljes szöveg nem érhető el (PRISMA F). Az áttekintésekből átvett számok másodlagosak: szűrésnél csak tájékoztatnak. Billentyűk a listán: ↑/↓ mozgás, I bevon, X kizár (okkal), T kizár cím/absztrakt alapján, N nem szerezhető be, W elbírálásra vár.",
        "en": "Being included by an earlier review is only context — your criteria may differ. At title/abstract level you may exclude without a reason; at full-text level a reason is required (PRISMA 16a). 'Not retrieved': the full text is unavailable (PRISMA F). Numbers taken from reviews are secondary: during screening they are informative only. Keys on the list: ↑/↓ move, I include, X exclude (with reason), T exclude on title/abstract, N not retrieved, W awaiting classification."},
    'update': {
        "hu": "Az ablak kezdete alapból a legfrissebb forrás-keresés dátuma mínusz 6 hónap átfedés (az indexelési késés miatt) — ez pragmatikus alapérték, nem irodalmi szabály, te döntöd el. Ha a legkorábbi és a legfrissebb keresési dátum 24 hónapnál távolabb esik, a „legkorábbi” horgony érzékenyebb. A lekérdezések SR-szűrő nélkül, a PICO-blokkokból készülnek; az előnézet a pontos lekérdezéseket mutatja futtatás nélkül. Mikor és hogyan frissíts: Garner 2016; egy áttekintés medián 5,5 év alatt avul el, de 23%-uk 2 éven belül (Shojania 2007).",
        "en": "By default the window starts at the latest source search date minus a 6-month overlap (for indexing delay) — a pragmatic default, not a literature rule; you decide. If the earliest and latest search dates are more than 24 months apart, the 'earliest' anchor is more sensitive. Queries are built from the PICO blocks without an SR filter; the preview shows the exact queries without running them. When and how to update: Garner 2016; a review goes out of date in a median of 5.5 years, but 23% within 2 years (Shojania 2007)."},
    'merge': {
        "hu": "Az egyesített lista minden vizsgálatnál mutatja, mely áttekintések vonták be, milyen címkével és milyen bizonyíték alapján. A PRISMA-ábra két ága: az egyéb módszerek (előző áttekintések + hivatkozáskövetés) és az adatbázis-ág (frissítő keresés); a motor ellenőrzi a számokat. A lezárás (EP5) csak akkor lehetséges, ha nincs nyitott döntés. Az áttekintések számai másodlagos adatok: SMD-metaanalízisek 37%-ában volt legalább egy vizsgálatnál 0,1-nél nagyobb eltérés (Gøtzsche 2007), 34 Cochrane-áttekintésből 20-ban volt kinyerési hiba (Jones 2005), a hibaarány akár 50% (Mathes 2017) — ezért az elsődleges közleménnyel ellenőrizd őket (EP6), mielőtt az adattáblába kerülnek.",
        "en": "The merged list shows for each study which reviews included it, under what label and on what evidence. The PRISMA flow has two branches: other methods (previous reviews + citation searching) and the database branch (update search); the engine checks the counts. Sign-off (EP5) is possible only when no decision is open. Numbers from reviews are secondary data: 37% of SMD meta-analyses had a difference above 0.1 in at least one study (Gøtzsche 2007), 20 of 34 Cochrane reviews had extraction errors (Jones 2005), error rates reach 50% (Mathes 2017) — so verify them against the primary report (EP6) before they enter the data table."},
}


# ---------------------------------------------------------------------------- sémák (headhunter-szerződések)
_reg_lock = threading.Lock()
_registry = None
_valid_cache = {}                               # (név, etag) → hibák (beszúrási sorrendben)


def registry():
    """A headhunter-szerződések (``urn:szk:contract:ma.headhunter.*:1``) — csak OLVASSUK őket (JSON)."""
    global _registry
    with _reg_lock:
        if _registry is None:
            reg = {}
            if CONTRACTS_DIR.is_dir():
                schema_lite.load_schema_dir(CONTRACTS_DIR, reg)
            _registry = reg
        return _registry


def problems_of(doc, name, etag=None, limit=5):
    """A dokumentum sémahibái (H001; üres = érvényes; ismeretlen séma esetén üres)."""
    if not name:
        return []
    urn = "urn:szk:contract:ma.headhunter.%s:1" % name
    reg = registry()
    if urn not in reg:
        return []
    key = (name, etag)
    if etag is not None:
        with _reg_lock:
            if key in _valid_cache:
                return list(_valid_cache[key])
    errs = schema_lite.validate(doc, {"$ref": urn}, reg, limit=limit)
    if etag is not None:
        with _reg_lock:
            _valid_cache[key] = list(errs)
            while len(_valid_cache) > 512:
                del _valid_cache[next(iter(_valid_cache))]
    return errs


# ---------------------------------------------------------------------------- redaktálás
def _secret_values():
    out = []
    for name in SECRET_ENV:
        v = os.environ.get(name)
        if v and len(v.strip()) >= 4:
            out.append(v.strip())
    return sorted(set(out), key=len, reverse=True)


def redact(text, root=None, secret_values=None):
    """Kulcsok, e-mail, tiltott URL-paraméterek és fejlécek kitakarása; az abszolút projektút → ``<projekt>``."""
    if not isinstance(text, str) or not text:
        return text
    for v in (secret_values if secret_values is not None else _secret_values()):
        if v in text:
            text = text.replace(v, REDACTED)
    text = _SECRET_PARAM_RE.sub(lambda m: "%s=%s" % (m.group(1), REDACTED), text)
    text = _SECRET_HEADER_RE.sub(lambda m: "%s: %s" % (m.group(1), REDACTED), text)
    text = _BEARER_RE.sub("Bearer " + REDACTED, text)
    if root:
        text = text.replace(root, "<projekt>")
    return text


# UX-7: a CLI-tanácsok felületi megfogalmazása — a felületen a gombot / mezőt nevezzük meg, nem a parancsot (a CLI
# kimenete változatlan; a „Javasolt következő parancs” a haladóknak külön, lenyílóban marad).
_CLI = r"(?:ma\.py headhunter |python3? -m metaelemzes\.headhunter )?"
GUI_HINTS = (
    (re.compile(r"\(futtasd: " + _CLI + r"sources --check\)"), "(nyomd meg a „Források ellenőrzése” gombot)"),
    (re.compile(r"később: " + _CLI + r"sources --check\."), "később nyomd meg a „Források ellenőrzése” gombot."),
    (re.compile(r"\(bekapcsolás: " + _CLI + r"sources <projekt> --enable \S+ --actor user:<név>\)"),
     "(bekapcsolás: a „Bekapcsolva” oszlop kapcsolójával)"),
    (re.compile(r"Ellenőrizd a forrásokat \(" + _CLI + r"sources --check\)"),
     "Ellenőrizd a forrásokat (a Források lépés „Források ellenőrzése” gombjával)"),
    (re.compile(r"a felső korlát \(--max\)"), "a felső korlát (Áttekintések lépés, „Legfeljebb forrásonként” mező)"),
    (re.compile(r"emeld a korlátot \(--cap\)"),
     "emeld a korlátot (Frissítés lépés, „Legfeljebb találat forrásonként” mező)"),
    (re.compile(r"\(--cap / --max\)"), "(a „Legfeljebb forrásonként” / „Legfeljebb találat forrásonként” mezőben)"),
    (re.compile(r"\(run: " + _CLI + r"sources --check\)"), "(press the “Check sources” button)"),
    (re.compile(r"later run: " + _CLI + r"sources --check\."), "later press the “Check sources” button."),
    (re.compile(r"\(enable: " + _CLI + r"sources <project> --enable \S+ --actor user:<name>\)"),
     "(enable it with the switch in the “Enabled” column)"),
)


def gui_wording(obj, keys=("message", "hu", "en", "advice", "detail")):
    """A JSON-fa megjelenő szövegeiben (``keys`` mezők, ill. {hu, en} értékek) a CLI-tanácsot a felületi
    megfelelőjére cseréli (``GUI_HINTS``). Azonosítót, parancsot (``next``), adatot nem érint."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, str) and k in keys:
                for rx, rep in GUI_HINTS:
                    v = rx.sub(rep, v)
                out[k] = v
            else:
                out[k] = gui_wording(v, keys)
        return out
    if isinstance(obj, (list, tuple)):
        return [gui_wording(v, keys) for v in obj]
    return obj


def redact_obj(obj, root=None, secret_values=None):
    """A redact() egy JSON-fa minden szövegére (a kulcsokra is)."""
    sv = _secret_values() if secret_values is None else secret_values
    if isinstance(obj, str):
        return redact(obj, root, sv)
    if isinstance(obj, dict):
        return {redact(str(k), root, sv): redact_obj(v, root, sv) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, root, sv) for v in obj]
    return obj


# ---------------------------------------------------------------------------- fájlok olvasása
def _load(app, rel, name=None, required=False):
    """(dokumentum | None, etag | None, problémák). Hibás JSON → (None, etag?, [hibaszöveg]) — a nézet nem bukik."""
    try:
        doc, etag = app.store.load_json(rel)
    except store.NotFound:
        doc, etag = None, None
    except store.StoreError as exc:
        if required:
            raise
        return None, None, [getattr(exc, "message", None) or str(exc)]
    if doc is None:
        return None, None, []
    if not isinstance(doc, dict):
        return None, etag, ["A(z) %s gyökere objektum legyen." % rel]
    return doc, etag, problems_of(doc, name, etag)


def _initialized(app):
    try:
        return app.store.path(STATE_REL).is_file()
    except store.StoreError:
        return False


def _require_state(app):
    if not _initialized(app):
        raise ApiError("CONFLICT", MSG_NOT_INIT, {"hh_code": "HH_NOT_INITIALIZED", "path": STATE_REL})


def _review_names(app):
    try:
        d = app.store.path(REVIEWS_REL)
    except store.StoreError:
        return []
    if not d.is_dir():
        return []
    out = []
    for name in sorted(os.listdir(str(d))):
        if name.endswith(".json") and RV_RE.match(name[:-5]) and (d / name).is_file():
            out.append(name[:-5])
    return out[:MAX_REVIEWS]


def _stat_sig(app, rel):
    try:
        st = app.store.path(rel).stat()
    except (OSError, store.StoreError):
        return "-"
    return "%d:%d" % (st.st_size, st.st_mtime_ns)


def _tag(app):
    """A headhunter-mappa összesített állapotjele (a status ETag-je és a CLI-gyorsítótár kulcsa)."""
    parts = []
    for _k, rel, _n in FILES:
        parts.append("%s=%s" % (rel, app.store.etag(rel) or "-"))
    parts.append("%s=%s" % (DECISIONS_REL, _stat_sig(app, DECISIONS_REL)))
    for rid in _review_names(app):
        rel = "%s/%s.json" % (REVIEWS_REL, rid)
        parts.append("%s=%s" % (rel, _stat_sig(app, rel)))
    return store.sha256_bytes("\n".join(parts).encode("utf-8"))


def _read_decisions(app, limit=None):
    """A decisions.jsonl sorai (a végéről legfeljebb ``limit``); hibás sor → kihagyva, a számuk visszajön."""
    try:
        path = app.store.path(DECISIONS_REL)
    except store.StoreError:
        return [], 0, 0
    if not path.is_file():
        return [], 0, 0
    with open(str(path), "rb") as fh:
        raw = fh.read()
    lines = [ln for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]
    bad = 0
    out = []
    for ln in (lines[-limit:] if limit else lines):
        try:
            d = json.loads(ln)
        except ValueError:
            bad += 1
            continue
        if isinstance(d, dict):
            out.append(d)
    return out, len(lines), bad


# ---------------------------------------------------------------------------- nézetmodellek (csak átrendezés)
def _ids_view(ids):
    out = {}
    for k in ID_KEYS:
        v = (ids or {}).get(k)
        if isinstance(v, dict) and v.get("value"):
            src = v.get("source")
            out[k] = {"value": v.get("value"), "source": src, "via": v.get("via"), "confirmed_by": v.get("confirmed_by"),
                      "api": bool(src in API_SOURCES or v.get("confirmed_by"))}
    reg = [x for x in (ids or {}).get("registry") or [] if isinstance(x, dict) and x.get("value")]
    if reg:
        out["registry"] = [{"value": x.get("value"), "source": x.get("source"), "via": x.get("via"),
                            "confirmed_by": x.get("confirmed_by"),
                            "api": bool(x.get("source") in API_SOURCES or x.get("confirmed_by"))} for x in reg[:20]]
    return out


def _bib_view(bib):
    b = bib if isinstance(bib, dict) else {}
    out = {k: b.get(k) for k in ("title", "first_author", "year", "journal", "volume", "issue", "pages", "language")}
    out["authors"] = list(b.get("authors") or [])[:8]
    out["authors_more"] = bool(b.get("authors_truncated")) or len(b.get("authors") or []) > 8
    out["pub_types"] = list(b.get("pub_types") or [])[:10]
    return out


def _record_view(rec):
    return {"rec_id": rec.get("rec_id"), "ids": _ids_view(rec.get("ids")), "bib": _bib_view(rec.get("bib")),
            "flags": {k: v for k, v in (rec.get("flags") or {}).items() if v},
            "related": list(rec.get("related") or [])[:20], "origins": list(rec.get("origins") or [])[:50],
            "resolution": rec.get("resolution") or {}, "status": rec.get("status"),
            "merged_into": rec.get("merged_into")}


def _label(bib):
    b = bib if isinstance(bib, dict) else {}
    fa = (b.get("first_author") or "").strip()
    surname = fa.split(" ")[0] if fa else ""
    yr = b.get("year")
    return ("%s %s" % (surname, yr if yr is not None else "")).strip() or None


def _review_summary(doc, etag, problems):
    cands = [c for c in doc.get("candidates") or [] if isinstance(c, dict)]
    by_status = _count(c.get("status") for c in cands)
    by_conf = _count(c.get("confidence") for c in cands if c.get("status") == "proposed")
    rank = doc.get("rank") or {}
    score = rank.get("score")
    n_included = sum(1 for c in cands if str(c.get("role_in_review") or "").startswith("included"))
    groups = {c.get("group_key") or c.get("cand_id") for c in cands
              if str(c.get("role_in_review") or "").startswith("included") and c.get("status") != "rejected"}
    return {
        "review_id": doc.get("review_id"), "status": doc.get("status"), "superseded_by": doc.get("superseded_by"),
        "ids": _ids_view(doc.get("ids")), "bib": _bib_view(doc.get("bib")), "label": _label(doc.get("bib")),
        "is_cochrane": bool(doc.get("is_cochrane")), "cochrane": doc.get("cochrane"),
        "found_by": list(doc.get("found_by") or []),
        "rank": {"score": score, "score_text": ("%.2f" % score) if isinstance(score, (int, float)) else None,
                 "components": {k: v for k, v in (rank.get("components") or {}).items()},
                 "components_text": {k: ("%.2f" % v) for k, v in (rank.get("components") or {}).items()
                                     if isinstance(v, (int, float)) and not isinstance(v, bool)}},
        "signals": doc.get("signals") or {}, "search_date": doc.get("search_date"),
        "k_reported": doc.get("k_reported"), "fulltext": doc.get("fulltext"),
        "n_candidates": len(cands), "n_included_claims": n_included, "n_groups": len(groups),
        "candidates": {"proposed": by_status.get("proposed", 0), "confirmed": by_status.get("confirmed", 0),
                       "rejected": by_status.get("rejected", 0)},
        "proposed_by_confidence": {k: by_conf.get(k, 0) for k in ("high", "medium", "low")},
        "extraction": (doc.get("extraction_runs") or [None])[-1],
        "path": "%s/%s.json" % (REVIEWS_REL, doc.get("review_id")), "etag": etag, "problems": problems,
    }


def _bump(d, key):
    d[key] = d.get(key, 0) + 1


def _count(items):
    """Előfordulások száma kulcsonként (darabszám, nem statisztika)."""
    out = {}
    for k in items:
        _bump(out, k)
    return out


def _cca_text(v):
    return ("%.1f" % v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ---------------------------------------------------------------------------- CLI-hívás
def _cli_prefix():
    return list(CLI_PREFIX) if CLI_PREFIX else [sys.executable, "-m", CLI_MODULE]


def _actor(app):
    """A munkamenet felhasználója ``user:<név>`` alakban (a CLI csak emberi szereplőt fogad el döntéshez)."""
    raw = str(getattr(app, "actor", "") or "user")
    if raw.startswith("user:"):
        raw = raw[5:]
    elif ":" in raw:
        raise ApiError("FORBIDDEN", "Emberi döntést csak felhasználó rögzíthet (user:<név>); a munkamenet szereplője "
                                    "nem felhasználó.")
    name = _ACTOR_SAFE.sub("_", raw.strip())[:64] or "user"
    return "user:" + name


def _base_argv(app, cmd, offline=False, sources=None, project=True, lang="hu"):
    argv = _cli_prefix() + [cmd] + ([str(app.project_root)] if project else []) + ["--json", "--lang", lang]
    if offline:
        argv.append("--offline")
    if sources:
        argv.append("--sources=" + ",".join(sources))
    return argv


def _text_arg(argv, flag, value):
    if value is not None:
        argv.append("%s=%s" % (flag, value))


def _safe_argv(argv):
    """Az activity-naplóba: a szabad szöveges kapcsolók értéke nélkül (T10), a projektút nélkül."""
    out = []
    # SEC-4: a verify_secondary --outcome / --arm értéke is szabad szöveg (a végpont PHI-szűri) → nem kerül naplóba
    free = ("--question", "--population", "--intervention", "--comparator", "--outcomes", "--study-designs",
            "--query", "--reason", "--primary-locator", "--primary-value", "--outcome", "--arm")
    for i, a in enumerate(argv):
        if i < len(_cli_prefix()):
            continue
        head = a.split("=", 1)[0]
        if "=" in a and head in free:
            out.append(head + "=«szöveg»")
        elif os.path.isabs(a):
            out.append("<projekt>")
        else:
            out.append(a)
    return ["python", "-m", CLI_MODULE] + out


# ---------------------------------------------------------------------------- feladatok (háttérszál + alfolyamat)
class _Job(object):
    def __init__(self, kind, step, argv, timeout, cli_step, owned=()):
        self.id = "hh-" + secrets.token_hex(6)
        self.kind = kind                    # run | decide
        self.step = step
        self.argv = list(argv)
        self.timeout = float(timeout)
        self.cli_step = cli_step            # a CLI runs/<run_id>/run.json 'step' mezője (vagy None)
        self.owned = tuple(owned)
        self.status = "queued"
        self.created = time.time()
        self.created_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.created))
        self.started = None
        self.finished = None
        self.exit_code = None
        self.envelope = None
        self.error = None
        self.stderr_tail = None
        self.cancel_requested = False
        self.run_id = None


class _Runner(object):
    """Projektenként egy író futás; a feladatok a memóriában (a szerver újraindulásával elvesznek — a fájlok nem)."""

    def __init__(self, app):
        self.app = app
        self.root = str(app.project_root)
        self.cond = threading.Condition()
        self.jobs = {}                      # job_id → _Job (beszúrási sorrendben)
        self.active = None
        self.status_cache = (None, None)     # (tag, CLI-boríték)

    def busy(self):
        with self.cond:
            job = self.jobs.get(self.active) if self.active else None
            return job if job is not None and job.status in ("queued", "running") else None

    def submit(self, kind, step, argv, timeout, cli_step=None, owned=()):
        with self.cond:
            cur = self.jobs.get(self.active) if self.active else None
            if cur is not None and cur.status in ("queued", "running"):
                raise ApiError("CONFLICT", MSG_BUSY, {"hh_code": "HH_RUN_ACTIVE", "job_id": cur.id, "step": cur.step})
            job = _Job(kind, step, argv, timeout, cli_step, owned)
            self.jobs[job.id] = job
            self.active = job.id
            while len(self.jobs) > KEEP_JOBS:
                oldest = next(iter(self.jobs))
                if oldest == job.id:
                    break
                self.jobs.pop(oldest)
        th = threading.Thread(target=self._execute, args=(job,), name="ma-gui-headhunter", daemon=True)
        th.start()
        return job

    def get(self, job_id):
        with self.cond:
            return self.jobs.get(job_id)

    def wait(self, job, timeout):
        deadline = time.monotonic() + max(0.0, timeout)
        with self.cond:
            while job.status in ("queued", "running"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.cond.wait(min(remaining, 0.5))
        return job

    def _execute(self, job):
        app = self.app
        watcher = getattr(app, "watcher", None) if app is not None else None
        tokens = []
        with self.cond:
            job.status = "running"
            job.started = time.time()
        try:
            if watcher is not None and hasattr(watcher, "own_prefix"):
                for prefix in (HH_REL + "/",) + job.owned:
                    tokens.append(watcher.own_prefix(prefix))
            res = jobs.run_subprocess(job.argv, timeout=job.timeout, cwd=str(ENGINE_ROOT))
        except (OSError, ValueError, TypeError) as exc:
            with self.cond:
                job.status = "error"
                job.error = {"code": "PLUGIN_FAILED", "hh_code": "HH_CLI_FAILED",
                             "message": MSG_CLI_FAILED, "detail": type(exc).__name__}
        else:
            self._interpret(job, res)
        finally:
            for tok in tokens:
                try:
                    watcher.release_prefix(tok)
                except Exception:                       # noqa: BLE001 — a figyelő hibája ne vigye el a feladatot
                    pass
            with self.cond:
                job.finished = time.time()
                if job.status in ("queued", "running"):
                    job.status = "error"
                if self.active == job.id:
                    self.active = None
                self.status_cache = (None, None)
                self.cond.notify_all()

    def _interpret(self, job, res):
        sv = _secret_values()
        stderr = redact(res.get("stderr", b"").decode("utf-8", "replace"), self.root, sv)
        tail = stderr[-2000:] if stderr else None
        env = None
        text = res.get("stdout", b"").decode("utf-8", "replace").strip()
        if text and not res.get("stdout_truncated"):
            try:
                env = json.loads(text)
            except ValueError:
                env = None
        with self.cond:
            job.exit_code = res.get("returncode")
            job.stderr_tail = tail
            if res.get("timed_out"):
                job.status = "timeout"
                job.error = {"code": "TIMEOUT", "hh_code": "HH_TIMEOUT", "message": MSG_TIMEOUT % int(job.timeout)}
            if isinstance(env, dict) and isinstance(env.get("ok"), bool):
                job.envelope = redact_obj(env, self.root, sv)
                code = env.get("exit_code")
                if isinstance(code, int) and not isinstance(code, bool):
                    job.exit_code = code
            if job.status == "timeout":
                return
            if job.envelope is None:
                job.status = "error"
                job.error = {"code": "PLUGIN_FAILED", "hh_code": "HH_CLI_FAILED", "message": MSG_CLI_FAILED,
                             "stderr_tail": tail}
                return
            data = job.envelope.get("data") if isinstance(job.envelope.get("data"), dict) else {}
            if job.cancel_requested and (data.get("cancelled") or not job.envelope.get("ok")):
                job.status = "cancelled"
            elif job.envelope.get("ok") and job.exit_code in (0, 3, 4):
                job.status = "done"                   # 3: részleges (forrás nem érhető el), 4: emberi döntésre vár
            else:
                job.status = "error"
                job.error = _cli_error(job.envelope)


_runners_lock = threading.Lock()
RUNNER_ATTR = "_headhunter_runner"


def runner(app):
    """Az App-hoz tartozó futtató (az App példányán tárolva: a szerverrel együtt keletkezik és szűnik meg)."""
    with _runners_lock:
        r = getattr(app, RUNNER_ATTR, None)
        if r is None:
            r = _Runner(app)
            setattr(app, RUNNER_ATTR, r)
        return r


def _cli_error(env):
    """A CLI hibaborítéka → {code (router-kód), hh_code, message, errors}."""
    errs = [e for e in (env or {}).get("errors") or [] if isinstance(e, dict)]
    codes = [str(e.get("code") or "") for e in errs]
    first = errs[0] if errs else {}
    msg = first.get("hu") or first.get("en") or "A Metaheadhunter hibát jelzett."
    if any("(H009)" in str(e.get("hu") or e.get("en") or "") for e in errs):
        codes.append("H009")                    # a CLI a lezárás tiltását szövegben jelzi (H009: nyitott EP)
    if "HH_NOT_INITIALIZED" in codes:
        return {"code": "CONFLICT", "hh_code": "HH_NOT_INITIALIZED", "message": msg, "errors": errs}
    if "LOCKED" in codes:
        return {"code": "LOCKED", "hh_code": "HH_LOCKED", "message": msg, "errors": errs}
    if "H009" in codes:
        return {"code": "GATE_BLOCKED", "hh_code": "HH_CHECKPOINT_PENDING", "message": msg, "errors": errs}
    if "INTERNAL" in codes:
        return {"code": "PLUGIN_FAILED", "hh_code": "HH_CLI_FAILED", "message": msg, "errors": errs}
    return {"code": "VALIDATION", "hh_code": codes[0] if codes else "HH_ERROR", "message": msg, "errors": errs}


def _run_dir_epoch(name):
    m = RUN_RE.match(name)
    if not m:
        return None
    try:
        return int(datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def _runs_listing(app, limit=8):
    """A CLI futásai (runs/<run_id>/run.json) — a legújabbak elöl; a GUI-n kívül indítottak is."""
    try:
        d = app.store.path(RUNS_REL)
    except store.StoreError:
        return []
    if not d.is_dir():
        return []
    names = sorted((n for n in os.listdir(str(d)) if RUN_RE.match(n)), reverse=True)[:limit]
    out = []
    for n in names:
        doc = None
        try:
            with open(str(d / n / "run.json"), encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            pass
        doc = doc if isinstance(doc, dict) else {}
        out.append({"run_id": n, "step": doc.get("step"), "status": doc.get("status"), "started": doc.get("started"),
                    "finished": doc.get("finished"), "exit_code": doc.get("exit_code"),
                    "cancel_requested": (d / n / "CANCEL").exists()})
    return out


def _discover_run(app, job):
    """A GUI-feladathoz tartozó CLI-futás (runs/<run_id>) — a feladat indulása után létrejött, azonos lépésű."""
    if job.run_id or not job.cli_step:
        return job.run_id
    for r in sorted(_runs_listing(app, limit=20), key=lambda x: x["run_id"]):
        ep = _run_dir_epoch(r["run_id"])
        if ep is None or ep < int(job.created) - 2 or r.get("step") != job.cli_step:
            continue
        job.run_id = r["run_id"]
        break
    return job.run_id


def _progress(app, run_id, limit=PROGRESS_TAIL):
    if not run_id:
        return []
    try:
        path = app.store.path("%s/%s/progress.jsonl" % (RUNS_REL, run_id))
    except store.StoreError:
        return []
    if not path.is_file():
        return []
    with open(str(path), "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 65536))
        raw = fh.read()
    out = []
    for ln in raw.decode("utf-8", "replace").splitlines()[-limit:]:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append({k: d.get(k) for k in ("ts", "step", "phase", "done", "total", "source", "message")})
    return redact_obj(out, str(app.project_root))


def _snapshot(app, job, with_data=True):
    env = job.envelope or {}
    now = job.finished or time.time()
    run_id = _discover_run(app, job)
    data = env.get("data") if with_data else None
    truncated = False
    if data is not None:
        try:
            if len(json.dumps(data, ensure_ascii=False)) > MAX_JOB_DATA:
                data, truncated = None, True
        except (TypeError, ValueError):
            data, truncated = None, True
    exit_code = job.exit_code
    partial = exit_code == 3
    return gui_wording({
        "job_id": job.id, "kind": job.kind, "step": job.step, "status": job.status, "created": job.created_iso,
        "elapsed_ms": int((now - job.created) * 1000), "timeout_s": int(job.timeout), "exit_code": exit_code,
        "ok": env.get("ok") if job.envelope is not None else None, "partial": partial,
        "needs_human": exit_code == 4, "hh_code": "HH_SOURCE_UNAVAILABLE" if partial else (job.error or {}).get("hh_code"),
        "message": env.get("message"), "warnings": list(env.get("warnings") or [])[:60],
        "errors": list(env.get("errors") or [])[:20], "pending": list(env.get("pending") or []),
        "next": env.get("next"), "data": data, "data_truncated": truncated, "error": job.error,
        "run_id": run_id, "progress": _progress(app, run_id) if run_id else [],
        "cancel_requested": job.cancel_requested,
        "cancellable": job.status in ("queued", "running"),
    })


# ---------------------------------------------------------------------------- lépések (POST /run)
_STR = {"type": ["string", "null"], "maxLength": MAX_TEXT}
_BOOL = {"type": ["boolean", "null"]}
RUN_SCHEMA = {
    "type": "object", "required": ["step"],
    "properties": {
        "step": {"enum": ["init", "sources_check", "find_reviews", "extract", "resolve", "dedupe", "overlap",
                          "update_search", "cite_search", "merge", "prisma", "export", "verify"]},
        "options": {"type": ["object", "null"], "properties": {
            "offline": _BOOL, "sources": {"type": ["array", "null"], "items": {"enum": list(SOURCE_KEYS)},
                                          "maxItems": 6},
            "question": _STR, "population": _STR, "intervention": _STR, "comparator": _STR, "outcomes": _STR,
            "study_designs": _STR, "mode": {"enum": ["harvest", "own_update", None]}, "force": _BOOL,
            "query": {"type": ["string", "null"], "maxLength": 4000},
            "since": {"type": ["string", "null"], "pattern": DATE_RE.pattern},
            "until": {"type": ["string", "null"], "pattern": DATE_RE.pattern},
            "max": {"type": ["integer", "null"], "minimum": 1, "maximum": 1000}, "fulltext_dates": _BOOL,
            "reviews": {"type": ["array", "null"], "items": {"type": "string", "pattern": RV_RE.pattern},
                        "maxItems": 200},
            "strategy": {"enum": ["auto", "jats", "reflist", None]}, "include_unknown": _BOOL,
            "level": {"enum": ["study", "report", None]}, "csv": _BOOL,
            "anchor": {"enum": ["latest", "earliest", "manual", None]},
            "start": {"type": ["string", "null"], "pattern": DAY_RE.pattern},
            "end": {"type": ["string", "null"], "pattern": DAY_RE.pattern},
            "overlap_months": {"type": ["integer", "null"], "minimum": 0, "maximum": 60},
            "cap": {"type": ["integer", "null"], "minimum": 1, "maximum": 100000}, "dry_run": _BOOL,
            "cite": {"enum": ["forward", "backward", "both", None]},
            "direction": {"enum": ["forward", "backward", "both", None]},
            "seeds": {"enum": ["reviews", "included", "both", None]},
            "outcome": {"type": ["string", "null"], "pattern": OUTCOME_RE.pattern},
            "to_project": _BOOL, "prisma": _BOOL, "for_analysis": _BOOL,
        }, "additionalProperties": False},
        "client_seq": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}
# lépés → (CLI-parancs, időkorlát s, a CLI runs/ lépésneve, kell-e állapot)
STEPS = {
    "init": ("init", 120, None, False),
    "sources_check": ("sources", 300, None, False),     # állapot nélkül is: a kulcsok ellenőrzése indulás előtt
    "find_reviews": ("find-reviews", 3600, "find_reviews", True),
    "extract": ("extract", 3600, "extract", True),
    "resolve": ("resolve", 3600, "resolve", True),
    "dedupe": ("dedupe", 900, None, True),
    "overlap": ("overlap", 600, None, True),
    "update_search": ("update-search", 3600, "update_search", True),
    "cite_search": ("cite-search", 3600, "update_search", True),
    "merge": ("merge", 900, None, True),
    "prisma": ("prisma", 300, None, True),
    "export": ("export", 900, None, True),
    "verify": ("verify", 900, None, True),
}


def _no_control(name, value):
    if value is not None and _CONTROL_RE.search(value.replace("\n", " ")):
        raise ApiError("BAD_REQUEST", "A(z) %s mező vezérlőkaraktert tartalmaz." % name)


def _opt_str(opts, name):
    v = opts.get(name)
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    _no_control(name, v)
    return v.replace("\n", " ")


def build_run_argv(app, step, opts):
    """(argv, cli_step, timeout, owned prefixes, szöveges mezők a PHI-őrhöz)."""
    cmd, timeout, cli_step, needs_state = STEPS[step]
    if needs_state:
        _require_state(app)
    project = needs_state or (step == "sources_check" and _initialized(app)) or step == "init"
    argv = _base_argv(app, cmd, offline=bool(opts.get("offline")), sources=opts.get("sources"), project=project)
    texts = {}
    owned = ()
    if step == "init":
        q = _opt_str(opts, "question")
        if not q:
            raise ApiError("BAD_REQUEST", "Hiányzó mező: options.question (a kutatási kérdés).")
        if _initialized(app) and not opts.get("force"):
            raise ApiError("CONFLICT", "A Metaheadhunter már el van indítva ebben a projektben. A PICO felülírásához "
                                       "kapcsold be a „felülírás” lehetőséget (force) — a döntésnapló megmarad.",
                           {"hh_code": "HH_ALREADY_INITIALIZED"})
        for name, flag in (("question", "--question"), ("population", "--population"),
                           ("intervention", "--intervention"), ("comparator", "--comparator"),
                           ("outcomes", "--outcomes"), ("study_designs", "--study-designs")):
            v = _opt_str(opts, name)
            if v is not None:
                texts[name] = v
                _text_arg(argv, flag, v)
        argv.append("--mode=" + (opts.get("mode") or "harvest"))
        argv.append("--actor=" + _actor(app))           # a PICO jóváhagyása (criteria_set döntés) a felhasználóé
        if opts.get("force"):
            argv.append("--force")
    elif step == "sources_check":
        argv.append("--check")
    elif step == "find_reviews":
        q = _opt_str(opts, "query")
        if q is not None:
            texts["query"] = q
            _text_arg(argv, "--query", q)
        _text_arg(argv, "--since", opts.get("since"))
        _text_arg(argv, "--until", opts.get("until"))
        if opts.get("max") is not None:
            argv.append("--max=%d" % opts["max"])
        if opts.get("fulltext_dates"):
            argv.append("--fulltext-dates")
    elif step == "extract":
        if opts.get("reviews"):
            argv.append("--review=" + ",".join(opts["reviews"]))
        argv.append("--strategy=" + (opts.get("strategy") or "auto"))
    elif step == "resolve":
        if opts.get("force"):
            argv.append("--force")
        if opts.get("include_unknown"):
            argv.append("--include-unknown")
    elif step == "overlap":
        argv.append("--level=" + (opts.get("level") or "study"))
        if opts.get("csv"):
            argv.append("--csv")
    elif step == "update_search":
        argv.append("--anchor=" + (opts.get("anchor") or "latest"))
        if opts.get("anchor") == "manual" and not opts.get("start"):
            raise ApiError("BAD_REQUEST", "Kézi horgonynál add meg az ablak kezdetét (options.start: ÉÉÉÉ-HH-NN).")
        _text_arg(argv, "--start", opts.get("start"))
        _text_arg(argv, "--end", opts.get("end"))
        if opts.get("overlap_months") is not None:
            argv.append("--overlap-months=%d" % opts["overlap_months"])
        if opts.get("cap") is not None:
            argv.append("--cap=%d" % opts["cap"])
        q = _opt_str(opts, "query")
        if q is not None:
            texts["query"] = q
            _text_arg(argv, "--query", q)
        if opts.get("dry_run"):
            argv.append("--dry-run")
            cli_step = None
        else:
            argv.append("--actor=" + _actor(app))       # az ablak jóváhagyása (update_window) a felhasználóé
            if opts.get("cite"):
                argv.append("--cite=" + opts["cite"])
                argv.append("--seeds=" + (opts.get("seeds") or "reviews"))
    elif step == "cite_search":
        argv.append("--direction=" + (opts.get("direction") or "forward"))
        argv.append("--seeds=" + (opts.get("seeds") or "reviews"))
    elif step == "export":
        if opts.get("outcome"):
            argv.append("--outcome=" + opts["outcome"])
        for name, flag in (("to_project", "--to-project"), ("prisma", "--prisma"), ("for_analysis", "--for-analysis")):
            if opts.get(name):
                argv.append(flag)
        if opts.get("to_project") or opts.get("prisma"):
            owned = ("02_szures/", "03_adatok/")
    return argv, cli_step, timeout, owned, texts


def post_run(req):
    app = req.app
    app.require_open()
    body = req.json_object()
    step = body["step"]
    opts = dict(body.get("options") or {})
    argv, cli_step, timeout, owned, texts = build_run_argv(app, step, opts)
    if texts:
        phi_doc_guard(app, texts, STATE_REL, "Metaheadhunter-beállítás (PICO / lekérdezés)")
    else:
        ok, why = app.can_write_meta(STATE_REL)
        if not ok:
            raise ApiError("FORBIDDEN", why, {"path": STATE_REL})
    warnings = []
    job = runner(app).submit("run", step, argv, timeout, cli_step, owned)
    log_activity_or_warn(app, "headhunter.run", warnings, argv=_safe_argv(argv),
                         details={"step": step, "job_id": job.id})
    return Result(_snapshot(app, job), S_JOB, warnings=warnings, status=202)


def get_runs(req):
    app = req.app
    app.require_open()
    r = runner(app)
    with r.cond:
        items = list(r.jobs.values())
    return Result({"jobs": [_snapshot(app, j, with_data=False) for j in reversed(items)],
                   "active": (r.busy().id if r.busy() else None), "cli_runs": _runs_listing(app)}, S_JOBS)


def get_run(req):
    app = req.app
    app.require_open()
    job_id = req.params["job_id"]
    if not JOB_RE.match(job_id):
        raise ApiError("BAD_REQUEST", "Érvénytelen feladat-azonosító.")
    r = runner(app)
    job = r.get(job_id)
    if job is None:
        raise ApiError("NOT_FOUND", "Nincs ilyen Metaheadhunter-feladat (a szerver újraindulása óta). Az eredmény a "
                                    "fájlokban van: frissítsd a nézetet.")
    wait = num_arg(req, "wait", 0.0, 0.0, MAX_WAIT)
    if wait:
        r.wait(job, wait)
    return Result(_snapshot(app, job), S_JOB)


def post_cancel(req):
    app = req.app
    app.require_open()
    ident = req.params["job_id"]
    r = runner(app)
    run_id = None
    job = None
    if JOB_RE.match(ident):
        job = r.get(ident)
        if job is None:
            raise ApiError("NOT_FOUND", "Nincs ilyen Metaheadhunter-feladat.")
        with r.cond:
            if job.status not in ("queued", "running"):
                return Result(_snapshot(app, job), S_JOB, warnings=["A feladat már befejeződött."])
            job.cancel_requested = True
        run_id = _discover_run(app, job)
    elif RUN_RE.match(ident):
        run_id = ident
    else:
        raise ApiError("BAD_REQUEST", "Érvénytelen azonosító (hh-… GUI-feladat vagy ÉÉÉÉHHNNTÓÓPPMMZ-xxxxxx CLI-futás).")
    warnings = []
    written = False
    if run_id:
        rel = "%s/%s/CANCEL" % (RUNS_REL, run_id)
        if not app.store.path("%s/%s" % (RUNS_REL, run_id)).is_dir():
            raise ApiError("NOT_FOUND", "Nincs ilyen futás: %s." % run_id)
        app.store.write_bytes(rel, b"")
        written = True
    else:
        warnings.append("Ez a lépés nem figyeli a megszakítást (rövid, helyi lépés): az időkorlátig befejeződik.")
    log_activity_or_warn(app, "headhunter.cancel", warnings, details={"job_id": job.id if job else None,
                                                                      "run_id": run_id})
    data = _snapshot(app, job) if job is not None else {"run_id": run_id, "status": "cancel_requested"}
    data["cancel_written"] = written
    return Result(data, S_JOB, warnings=warnings)


# ---------------------------------------------------------------------------- döntések (POST /decide)
_BATCH = {"type": ["object", "null"], "required": ["type"], "properties": {
    "type": {"enum": ["all_candidates", "all_proposals", "all_pending"]},
    "review": {"type": ["string", "null"], "pattern": RV_RE.pattern},
    "kind": {"enum": ["same_report", "same_study", "id_conflict", "split_study", "resolution", None]},
    "min_score": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    "certainty": {"enum": ["probable", "possible", None]},
    "min_reviews": {"type": ["integer", "null"], "minimum": 1, "maximum": 1000},
    "from_reviews_only": _BOOL}, "additionalProperties": False}
DECIDE_SCHEMA = {
    "type": "object", "required": ["kind"],
    "properties": {
        "kind": {"enum": ["decide", "batch", "signoff", "sources", "verify_secondary"]},
        "target": {"type": ["string", "null"], "maxLength": 200},
        "value": {"type": ["string", "null"], "maxLength": 240},
        "level": {"enum": ["title_abstract", "full_text", "both", None]},
        "reason_code": {"type": ["string", "null"], "pattern": REASON_CODE_RE.pattern},
        "reason": {"type": ["string", "null"], "maxLength": MAX_REASON},
        "batch": _BATCH,
        "options": {"type": ["object", "null"], "properties": {
            "enable": {"type": ["array", "null"], "items": {"enum": list(SOURCE_KEYS)}, "maxItems": 6},
            "disable": {"type": ["array", "null"], "items": {"enum": list(SOURCE_KEYS)}, "maxItems": 6},
            "study": {"type": ["string", "null"], "pattern": STUDY_RE.pattern},
            "field": {"type": ["string", "null"], "pattern": FIELD_RE.pattern},
            "status": {"enum": ["verified", "discrepant", "not_applicable", None]},
            "review": {"type": ["string", "null"], "pattern": RV_RE.pattern},
            "evidence": {"type": ["string", "null"], "pattern": EV_RE.pattern},
            "outcome": {"type": ["string", "null"], "maxLength": 200},
            "arm": {"type": ["string", "null"], "maxLength": 100},
            "primary_locator": {"type": ["string", "null"], "maxLength": 500},
            "primary_value": {"type": ["string", "null"], "maxLength": 100},
        }, "additionalProperties": False},
        "client_seq": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}


def _target_file(target):
    m = CAND_RE.match(target)
    if m:
        return "%s/%s.json" % (REVIEWS_REL, m.group(1))
    if RV_RE.match(target):
        return "%s/%s.json" % (REVIEWS_REL, target)
    if PROP_RE.match(target) or REC_RE.match(target) or STUDY_RE.match(target):
        return STUDIES_REL
    raise ApiError("BAD_REQUEST", "Ismeretlen célazonosító (rv-…, rv-…#c…, p-…, rec-…, st-…).")


def build_decide_argv(app, body):
    """(argv, cél fájl, PHI-ellenőrzendő szövegek, activity-részletek)."""
    kind = body["kind"]
    actor = _actor(app)
    reason = body.get("reason")
    if reason is not None:
        reason = reason.strip() or None
        _no_control("reason", reason)
        reason = reason.replace("\n", " ") if reason else None
    texts = {"reason": reason} if reason else {}
    details = {"kind": kind}
    if kind == "decide":
        target = (body.get("target") or "").strip()
        value = (body.get("value") or "").strip()
        if not target:
            raise ApiError("BAD_REQUEST", "Hiányzó mező: target (rv-…, rv-…#c…, p-…, rec-…, st-…).")
        if not VALUE_RE.match(value):
            raise ApiError("BAD_REQUEST", "A value: accept | reject | include | exclude | not_retrieved | awaiting | "
                                          "no_identifier | keep_retracted (feloldásnál: pmid:<szám>, doi:10.…, "
                                          "pmcid:PMC…, nct:NCT…, eid:2-s2.0-…, openalex:W… vagy option:<N>).")
        rel = _target_file(target)
        argv = _base_argv(app, "decide") + ["--target=" + target, "--value=" + value]
        details.update({"target_type": target.split("-", 1)[0] + ("#c" if "#" in target else ""), "value": value})
    elif kind == "batch":
        batch = body.get("batch") or None
        value = (body.get("value") or "").strip()
        if not batch:
            raise ApiError("BAD_REQUEST", "Hiányzó mező: batch (tömeges döntés szűrője).")
        if value not in ("include", "exclude", "accept", "reject"):
            raise ApiError("BAD_REQUEST", "Tömeges döntésnél a value: include/accept vagy exclude/reject.")
        include = value in ("include", "accept")
        argv = _base_argv(app, "confirm" if include else "exclude")
        btype = batch["type"]
        if btype == "all_candidates":
            if not batch.get("review"):
                raise ApiError("BAD_REQUEST", "A tömeges jelölt-döntéshez add meg az áttekintést (batch.review).")
            argv += ["--all-candidates", "--review=" + batch["review"]]
            rel = "%s/%s.json" % (REVIEWS_REL, batch["review"])
        elif btype == "all_proposals":
            argv.append("--all-proposals")
            if batch.get("kind"):
                argv.append("--kind=" + batch["kind"])
            if batch.get("min_score") is not None:
                argv.append("--min-score=%s" % repr(float(batch["min_score"])))
            if batch.get("certainty"):
                argv.append("--certainty=" + batch["certainty"])
            rel = STUDIES_REL
        else:
            argv.append("--all-pending")
            if batch.get("min_reviews"):
                argv.append("--min-reviews=%d" % batch["min_reviews"])
            if batch.get("from_reviews_only"):
                argv.append("--from-reviews-only")
            rel = STUDIES_REL
        if not include and not reason and not body.get("reason_code"):
            raise ApiError("BAD_REQUEST", "Tömeges kizárásnál/elutasításnál add meg az okát (reason vagy reason_code).")
        details.update({"batch": btype, "value": "include" if include else "exclude"})
    elif kind == "signoff":
        argv = _base_argv(app, "signoff")
        rel = MERGED_REL
    elif kind == "sources":
        opts = body.get("options") or {}
        en, dis = list(opts.get("enable") or []), list(opts.get("disable") or [])
        if not en and not dis:
            raise ApiError("BAD_REQUEST", "Add meg, melyik forrást kapcsolod be (options.enable) vagy ki (options.disable).")
        if set(en) & set(dis):
            raise ApiError("BAD_REQUEST", "Ugyanazt a forrást nem kapcsolhatod egyszerre be és ki.")
        argv = _base_argv(app, "sources")
        if en:
            argv.append("--enable=" + ",".join(en))
        if dis:
            argv.append("--disable=" + ",".join(dis))
        rel = STATE_REL
        details.update({"enable": en, "disable": dis})
    else:                                               # verify_secondary (EP6)
        opts = body.get("options") or {}
        for need in ("study", "field", "status"):
            if not opts.get(need):
                raise ApiError("BAD_REQUEST", "Hiányzó mező: options.%s." % need)
        loc = (opts.get("primary_locator") or "").strip() or None
        if opts["status"] in ("verified", "discrepant") and not loc:
            raise ApiError("BAD_REQUEST", "Add meg, hol ellenőrizted az elsődleges közleményben (options.primary_locator, "
                                          "pl. „rec-pmid-… 5. oldal, 2. táblázat”).")
        argv = _base_argv(app, "verify-secondary") + ["--study=" + opts["study"], "--field=" + opts["field"],
                                                      "--status=" + opts["status"]]
        for name, flag in (("review", "--review"), ("evidence", "--evidence"), ("outcome", "--outcome"),
                           ("arm", "--arm"), ("primary_value", "--primary-value")):
            v = (opts.get(name) or "").strip() or None
            if v is not None:
                _no_control(name, v)
                argv.append("%s=%s" % (flag, v))
                if name in ("outcome", "arm", "primary_value"):
                    texts[name] = v
        if loc:
            _no_control("primary_locator", loc)
            argv.append("--primary-locator=" + loc)
            texts["primary_locator"] = loc
        rel = MERGED_REL
        details.update({"study": opts["study"], "field": opts["field"], "status": opts["status"]})
    cmd = argv[len(_cli_prefix())]
    level = body.get("level")
    if level and cmd in ("decide", "confirm", "exclude"):
        if cmd == "exclude" and level == "both":
            level = "full_text"
        argv.append("--level=" + level)
    if body.get("reason_code") and cmd in ("decide", "exclude"):      # a confirm-nak nincs okkódja
        argv.append("--reason-code=" + body["reason_code"])
    if reason and kind != "sources":
        argv.append("--reason=" + reason)
    argv.append("--actor=" + actor)
    return argv, rel, texts, details


def post_decide(req):
    app = req.app
    app.require_open()
    _require_state(app)
    body = req.json_object()
    argv, rel, texts, details = build_decide_argv(app, body)
    if texts:
        phi_doc_guard(app, texts, DECISIONS_REL, "Metaheadhunter-döntés indoklása")
    else:
        ok, why = app.can_write_meta(DECISIONS_REL)
        if not ok:
            raise ApiError("FORBIDDEN", why, {"path": DECISIONS_REL})
    want = if_match(req)
    if want is None:
        raise ApiError("BAD_REQUEST", MSG_IF_MATCH, {"path": rel, "hh_code": "PRECONDITION_REQUIRED"})
    cur = app.store.etag(rel)
    if cur is None and rel != MERGED_REL:
        raise ApiError("NOT_FOUND", "A döntés célfájlja nem létezik: %s." % rel, {"path": rel})
    if str(want).strip('"') not in ("*", cur or ""):
        raise ApiError("CONFLICT", MSG_STALE % rel, {"path": rel, "etag": cur, "hh_code": "PRECONDITION_FAILED"})
    r = runner(app)
    job = r.submit("decide", body["kind"], argv, 600.0, None)
    warnings = []
    log_activity_or_warn(app, "headhunter.decide", warnings, argv=_safe_argv(argv), details=details)
    r.wait(job, DECIDE_WAIT)
    snap = _snapshot(app, job)
    if job.status in ("queued", "running"):
        return Result(snap, S_JOB, warnings=warnings, status=202)
    if job.status != "done":
        err = job.error or {"code": "PLUGIN_FAILED", "hh_code": "HH_CLI_FAILED", "message": MSG_CLI_FAILED}
        det = {k: v for k, v in err.items() if k not in ("code", "message")}
        det["job_id"] = job.id
        raise ApiError(err.get("code") or "PLUGIN_FAILED", err.get("message") or MSG_CLI_FAILED, det)
    snap["etag"] = app.store.etag(rel)
    snap["path"] = rel
    return Result(snap, S_JOB, warnings=warnings)


# ---------------------------------------------------------------------------- GET-nézetek
def _status_cli(app, tag):
    r = runner(app)
    with r.cond:
        cached_tag, cached = r.status_cache
    if cached_tag == tag and cached is not None:
        return cached
    try:
        res = jobs.run_subprocess(_base_argv(app, "status"), timeout=STATUS_CLI_TIMEOUT, cwd=str(ENGINE_ROOT))
    except (OSError, ValueError, TypeError) as exc:
        return {"available": False, "message": "A headhunter parancssora nem indítható (%s)." % type(exc).__name__}
    env = None
    try:
        env = json.loads(res["stdout"].decode("utf-8", "replace").strip() or "null")
    except ValueError:
        env = None
    if not isinstance(env, dict) or res.get("timed_out"):
        tail = redact(res.get("stderr", b"").decode("utf-8", "replace"), str(app.project_root))[-1000:]
        return {"available": False, "message": MSG_CLI_FAILED, "stderr_tail": tail or None}
    env = redact_obj(env, str(app.project_root))
    data = env.get("data") or {}
    out = {"available": True, "ok": env.get("ok"), "exit_code": env.get("exit_code"),
           "checkpoints": data.get("checkpoints") or {}, "findings": list(data.get("findings") or [])[:100],
           "pending": list(env.get("pending") or []), "next": env.get("next"),
           "warnings": list(env.get("warnings") or [])[:50], "errors": list(env.get("errors") or [])[:20],
           "final": data.get("final"), "merged": data.get("merged"), "update_window": data.get("update_window")}
    with r.cond:
        r.status_cache = (tag, out)
    return out


def get_sources(req):
    """A források táblázata a CLI-ből (``sources [<projekt>] --json``; hálózat NÉLKÜL): név, be/ki, állapot, kulcs
    beállítva igen/nem, jogosultság, visszaállás, üzenet, hitelesítés, szerep. Indulás előtt is (alapértékek)."""
    app = req.app
    app.require_open()
    lang = "en" if req.arg("lang") == "en" else "hu"
    initialized = _initialized(app)
    argv = _base_argv(app, "sources", project=initialized, lang=lang)
    try:
        res = jobs.run_subprocess(argv, timeout=STATUS_CLI_TIMEOUT, cwd=str(ENGINE_ROOT))
    except (OSError, ValueError, TypeError):
        raise ApiError("PLUGIN_FAILED", MSG_CLI_FAILED, {"hh_code": "HH_CLI_FAILED"}) from None
    try:
        env = json.loads(res["stdout"].decode("utf-8", "replace").strip() or "null")
    except ValueError:
        env = None
    if not isinstance(env, dict) or not isinstance(env.get("data"), dict):
        tail = redact(res.get("stderr", b"").decode("utf-8", "replace"), str(app.project_root))[-1000:]
        raise ApiError("PLUGIN_FAILED", MSG_CLI_FAILED, {"hh_code": "HH_CLI_FAILED", "stderr_tail": tail or None})
    rows = [{k: r.get(k) for k in ("source", "name", "enabled", "status", "automatic", "key_configured",
                                   "insttoken_configured", "entitlement", "reset_at", "checked_at", "message", "auth",
                                   "role", "unverified_live")}
            for r in env["data"].get("rows") or [] if isinstance(r, dict)]
    data = {"initialized": initialized, "lang": lang, "rows": rows,
            "warnings": list(env.get("warnings") or [])[:20],
            "state_etag": app.store.etag(STATE_REL) if initialized else None}
    return Result(gui_wording(redact_obj(data, str(app.project_root))), S_SOURCES)


def get_status(req):
    app = req.app
    app.require_open()
    tag = _tag(app)
    files = {}
    problems = []
    docs = {}
    for key, rel, name in FILES:
        doc, etag, probs = _load(app, rel, name)
        docs[key] = doc
        files[key] = {"path": rel, "exists": doc is not None or etag is not None, "etag": etag}
        if probs:
            problems.append({"path": rel, "errors": probs})
    decisions, n_dec, bad = _read_decisions(app, limit=5)
    files["decisions"] = {"path": DECISIONS_REL, "exists": n_dec > 0, "lines": n_dec, "bad_lines": bad}
    state = docs["state"]
    initialized = state is not None
    rv_counts = {}
    cand_counts = {}
    for rid in _review_names(app):
        doc, _etag, probs = _load(app, "%s/%s.json" % (REVIEWS_REL, rid), "review")
        if doc is None:
            if probs:
                problems.append({"path": "%s/%s.json" % (REVIEWS_REL, rid), "errors": probs})
            continue
        if probs:
            problems.append({"path": "%s/%s.json" % (REVIEWS_REL, rid), "errors": probs})
        _bump(rv_counts, doc.get("status") or "candidate")
        if doc.get("status") == "selected":
            for c in doc.get("candidates") or []:
                if isinstance(c, dict):
                    _bump(cand_counts, c.get("status") or "proposed")
    studies = docs["studies"] or {}
    recs = [r for r in studies.get("records") or [] if isinstance(r, dict)]
    props = [p for p in studies.get("proposals") or [] if isinstance(p, dict)]
    data = {
        "initialized": initialized, "path": HH_REL, "actor": _actor(app) if initialized else None,
        "state": None, "sources": [], "steps": [], "checkpoints": [],
        "counts": {"reviews": dict(rv_counts, total=sum(rv_counts.values())), "candidates": dict(cand_counts),
                   "records": sum(1 for r in recs if r.get("status") != "merged_into"),
                   "studies": len(studies.get("studies") or []),
                   "proposals_pending": sum(1 for p in props if p.get("status") == "pending"),
                   "proposals_auto": sum(1 for p in props if p.get("status") == "auto_applied"),
                   "decisions": n_dec},
        "files": files, "problems": problems,
        "decisions_recent": list(reversed(decisions)),
        "merged": {"final": bool((docs["merged"] or {}).get("final")), "counts": (docs["merged"] or {}).get("counts"),
                   "summary": (docs["merged"] or {}).get("summary")} if docs["merged"] else None,
        "overlap": {"cca_pct": docs["overlap"].get("cca_pct"), "cca_text": _cca_text(docs["overlap"].get("cca_pct")),
                    "band": docs["overlap"].get("band"), "level": docs["overlap"].get("level")}
        if docs["overlap"] else None,
        "jobs": None, "cli_runs": _runs_listing(app, limit=5), "cli": None, "help": HELP,
    }
    if initialized:
        data["state"] = {k: state.get(k) for k in ("mode", "created", "updated", "pico", "criteria",
                                                    "exclusion_reasons", "project_link", "tool_version")}
        data["state"]["settings"] = {k: v for k, v in (state.get("settings") or {}).items()
                                     if k in ("overlap_window_months", "max_reviews", "quote_max_chars",
                                              "title_similarity_probable", "title_similarity_possible",
                                              "contact_email_set", "reviewers")}
        srcs = state.get("sources") or {}
        for key in SOURCE_KEYS:
            cfg = srcs.get(key)
            if isinstance(cfg, dict):
                data["sources"].append(dict({k: cfg.get(k) for k in (
                    "enabled", "status", "checked_at", "message", "reset_at", "key_configured", "insttoken_configured",
                    "entitlement")}, key=key))
        steps = state.get("steps") or {}
        for s in STEP_ORDER:
            st = steps.get(s) if isinstance(steps.get(s), dict) else {}
            data["steps"].append({"id": s, "status": st.get("status") or "not_started", "updated": st.get("updated"),
                                  "message": st.get("message"), "run_id": st.get("run_id")})
        cps = {c.get("id"): c for c in state.get("checkpoints") or [] if isinstance(c, dict)}
        for ep in CHECKPOINTS:
            c = cps.get(ep) or {}
            data["checkpoints"].append({"id": ep, "status": c.get("status") or "pending",
                                        "open_items": c.get("open_items"), "decided_by": c.get("decided_by"),
                                        "at": c.get("at")})
        if req.arg("cli") != "0":
            data["cli"] = _status_cli(app, tag)
    r = runner(app)
    busy = r.busy()
    data["jobs"] = {"active": _snapshot(app, busy, with_data=False) if busy else None}
    warnings = []
    if problems:
        warnings.append("%d headhunter-fájl nem felel meg a sémájának (H001) — a részletek a problems listában."
                        % len(problems))
    return Result(gui_wording(redact_obj(data, str(app.project_root))), S_STATUS, warnings=warnings, etag=tag)


def get_reviews(req):
    app = req.app
    app.require_open()
    _require_state(app)
    want = req.arg("status")
    items, counts = [], {}
    for rid in _review_names(app):
        rel = "%s/%s.json" % (REVIEWS_REL, rid)
        doc, etag, probs = _load(app, rel, "review")
        if doc is None:
            items.append({"review_id": rid, "status": None, "path": rel, "etag": etag, "problems": probs or
                          ["A fájl nem olvasható."]})
            continue
        _bump(counts, doc.get("status"))
        if want and want != "all" and doc.get("status") != want:
            continue
        items.append(_review_summary(doc, etag, probs))
    items.sort(key=lambda x: (-(((x.get("rank") or {}).get("score")) or 0.0), x["review_id"]))
    return Result({"items": items, "counts": dict(counts), "path": REVIEWS_REL}, S_REVIEWS)


def get_review(req):
    app = req.app
    app.require_open()
    _require_state(app)
    rid = req.params["review_id"]
    if not RV_RE.match(rid):
        raise ApiError("BAD_REQUEST", "Érvénytelen áttekintés-azonosító (rv-…).")
    rel = "%s/%s.json" % (REVIEWS_REL, rid)
    doc, etag, probs = _load(app, rel, "review", required=True)
    if doc is None:
        raise ApiError("NOT_FOUND", "Nincs ilyen forrás-áttekintés: %s." % rid, {"path": rel})
    summary = _review_summary(doc, etag, probs)
    evidence = {e.get("evidence_id"): e for e in doc.get("evidence") or [] if isinstance(e, dict)}
    cands = []
    for c in doc.get("candidates") or []:
        if not isinstance(c, dict):
            continue
        cv = dict(c)
        cv["ids"] = _ids_view(c.get("ids"))
        cv["evidence"] = [evidence[e] for e in c.get("evidence_ids") or [] if e in evidence]
        cv["evidence_missing"] = [e for e in c.get("evidence_ids") or [] if e not in evidence]
        cands.append(cv)
    sd = doc.get("search_date") or {}
    kr = doc.get("k_reported") or {}
    data = {"summary": summary, "candidates": cands, "excluded_by_review": list(doc.get("excluded_by_review") or []),
            "search_date_evidence": evidence.get(sd.get("evidence_id")) if sd.get("evidence_id") else None,
            "k_evidence": evidence.get(kr.get("evidence_id")) if kr.get("evidence_id") else None,
            "signals_evidence": [evidence[e] for e in (doc.get("signals") or {}).get("evidence_ids") or []
                                 if e in evidence],
            "path": rel, "problems": probs}
    return Result(gui_wording(redact_obj(data, str(app.project_root))), S_REVIEW, etag=etag)


def _studies_doc(app):
    _require_state(app)
    doc, etag, probs = _load(app, STUDIES_REL, "studies", required=True)
    return doc, etag, probs


def get_studies(req):
    app = req.app
    app.require_open()
    doc, etag, probs = _studies_doc(app)
    section = req.arg("section")
    if section not in (None, "", "records", "studies", "proposals"):
        raise ApiError("BAD_REQUEST", "A section: records | studies | proposals.")
    if doc is None:
        return Result({"exists": False, "path": STUDIES_REL, "records": [], "studies": [], "proposals": [],
                       "problems": []}, S_STUDIES)
    recs = [r for r in doc.get("records") or [] if isinstance(r, dict)]
    data = {"exists": True, "path": STUDIES_REL, "generated": doc.get("generated"), "problems": probs,
            "counts": {"records": len(recs), "active": sum(1 for r in recs if r.get("status") != "merged_into"),
                       "studies": len(doc.get("studies") or []), "proposals": len(doc.get("proposals") or [])}}
    if section in (None, "", "records"):
        data["records"] = [_record_view(r) for r in recs[:MAX_ITEMS]]
        data["records_truncated"] = len(recs) > MAX_ITEMS
    if section in (None, "", "studies"):
        data["studies"] = list(doc.get("studies") or [])[:MAX_ITEMS]
    if section in (None, "", "proposals"):
        data["proposals"] = list(doc.get("proposals") or [])[:MAX_PROPOSALS]
    return Result(redact_obj(data, str(app.project_root)), S_STUDIES, etag=etag)


def get_proposals(req):
    app = req.app
    app.require_open()
    doc, etag, probs = _studies_doc(app)
    if doc is None:
        return Result({"exists": False, "path": STUDIES_REL, "items": [], "counts": {}, "auto_applied": []},
                      S_PROPOSALS)
    want_status = req.arg("status") or "pending"
    want_kind = req.arg("kind")
    recs = {r.get("rec_id"): r for r in doc.get("records") or [] if isinstance(r, dict)}
    studies = {s.get("study_id"): s for s in doc.get("studies") or [] if isinstance(s, dict)}
    props = [p for p in doc.get("proposals") or [] if isinstance(p, dict)]
    counts = _count("%s:%s" % (p.get("kind"), p.get("status")) for p in props)
    review_cache = {}

    def candidate(ref):
        m = CAND_RE.match(ref)
        if m.group(1) not in review_cache:
            rdoc, _e, _p = _load(app, "%s/%s.json" % (REVIEWS_REL, m.group(1)), "review")
            review_cache[m.group(1)] = {c.get("cand_id"): c for c in (rdoc or {}).get("candidates") or []
                                        if isinstance(c, dict)}
        c = review_cache[m.group(1)].get(m.group(2)) or {}
        return {"type": "candidate", "id": ref, "review_id": m.group(1), "label": c.get("study_label_in_review"),
                "cited_as": c.get("cited_as"), "ids": _ids_view(c.get("ids")), "confidence": c.get("confidence")}

    def expand(p):
        out = dict(p)
        if isinstance(p.get("options"), list):
            out["options"] = [dict(o, ids=_ids_view(o.get("ids")), bib=_bib_view(o.get("bib")),
                                   score_text=("%.2f" % o["score"]) if isinstance(o.get("score"), (int, float))
                                   and not isinstance(o.get("score"), bool) else None)
                              for o in p["options"] if isinstance(o, dict)]
        items = []
        for it in p.get("items") or []:
            if CAND_RE.match(str(it)):
                items.append(candidate(it))
            elif it in recs:
                items.append(dict(_record_view(recs[it]), type="record"))
            elif it in studies:
                s = studies[it]
                items.append({"type": "study", "study_id": it, "label": s.get("label"),
                              "reports": [dict(_record_view(recs[rp.get("rec_id")]), role=rp.get("role"))
                                          for rp in s.get("reports") or [] if rp.get("rec_id") in recs][:10],
                              "reviews": list(s.get("reviews") or [])})
            else:
                items.append({"type": "unknown", "id": it})
        out["records"] = items
        out["score_text"] = ("%.2f" % p["score"]) if isinstance(p.get("score"), (int, float)) else None
        feats = dict(p.get("features") or {})
        if isinstance(feats.get("title_sim"), (int, float)):
            feats["title_sim_text"] = "%.2f" % feats["title_sim"]
        out["features"] = feats
        return out

    sel = [p for p in props if (want_status == "all" or p.get("status") == want_status)
           and (not want_kind or p.get("kind") == want_kind)]
    auto = [expand(p) for p in props if p.get("status") == "auto_applied"][:200]
    data = {"exists": True, "path": STUDIES_REL, "items": [expand(p) for p in sel[:MAX_PROPOSALS]],
            "truncated": len(sel) > MAX_PROPOSALS, "counts": dict(counts), "auto_applied": auto,
            "problems": probs}
    return Result(redact_obj(data, str(app.project_root)), S_PROPOSALS, etag=etag)


def _review_labels(app):
    out = {}
    for rid in _review_names(app):
        doc, _e, _p = _load(app, "%s/%s.json" % (REVIEWS_REL, rid), "review")
        if doc is not None:
            out[rid] = {"label": _label(doc.get("bib")), "title": (doc.get("bib") or {}).get("title"),
                        "status": doc.get("status"), "is_cochrane": bool(doc.get("is_cochrane"))}
    return out


def get_overlap(req):
    app = req.app
    app.require_open()
    _require_state(app)
    doc, etag, probs = _load(app, OVERLAP_REL, "overlap", required=True)
    if doc is None:
        return Result({"exists": False, "path": OVERLAP_REL}, S_OVERLAP)
    rows = [r for r in doc.get("rows") or [] if isinstance(r, dict)]
    data = dict(doc)
    data.update({"exists": True, "path": OVERLAP_REL, "problems": probs, "cca_text": _cca_text(doc.get("cca_pct")),
                 "wcca_text": _cca_text(doc.get("wcca_pct")), "rows": rows[:MAX_ITEMS],
                 "rows_truncated": len(rows) > MAX_ITEMS, "n_rows": len(rows),
                 "pairs": [dict(p, cca_text=_cca_text(p.get("cca_pct"))) for p in doc.get("pairs") or []
                           if isinstance(p, dict)],
                 "review_meta": _review_labels(app)})
    return Result(redact_obj(data, str(app.project_root)), S_OVERLAP, etag=etag)


def get_merged(req):
    app = req.app
    app.require_open()
    _require_state(app)
    doc, etag, probs = _load(app, MERGED_REL, "merged", required=True)
    if doc is None:
        return Result({"exists": False, "path": MERGED_REL, "studies": []}, S_MERGED)
    want = req.arg("status")
    studies = [s for s in doc.get("studies") or [] if isinstance(s, dict) and (not want or want == "all"
                                                                                 or s.get("status") == want)]
    status_counts = _count(s.get("status") for s in doc.get("studies") or [] if isinstance(s, dict))
    data = {k: doc.get(k) for k in ("generated", "final", "counts", "summary", "exports", "prisma_flow", "dropped")}
    truncated = len(studies) > MAX_ITEMS
    # az azonosítók a felület alakjában (api: API-forrású vagy API-val megerősített — N1)
    studies = [dict(s, reports=[dict(rp, ids=_ids_view(rp.get("ids"))) for rp in s.get("reports") or []
                                if isinstance(rp, dict)]) for s in studies[:MAX_ITEMS]]
    data.update({"exists": True, "path": MERGED_REL, "problems": probs, "studies": studies, "truncated": truncated,
                 "status_counts": dict(status_counts),
                 "review_meta": _review_labels(app)})
    state, _e, _p = _load(app, STATE_REL, "state")
    data["exclusion_reasons"] = (state or {}).get("exclusion_reasons") or []
    return Result(gui_wording(redact_obj(data, str(app.project_root))), S_MERGED, etag=etag)


def get_prisma(req):
    app = req.app
    app.require_open()
    _require_state(app)
    doc, etag, probs = _load(app, PRISMA_REL, None, required=True)
    if doc is None:
        return Result({"exists": False, "path": PRISMA_REL}, S_PRISMA)
    flow = {k: v for k, v in doc.items() if k not in ("hh", "source")}
    warnings = []
    try:
        check = api.prisma_check(flow)
    except ValueError as exc:
        check = None
        warnings.append("A motor PRISMA-ellenőrzése nem futott le: %s" % exc)
    data = {"exists": True, "path": PRISMA_REL, "flow": flow, "hh": doc.get("hh") or {}, "source": doc.get("source"),
            "check": check, "problems": probs,
            "project_copy": {"path": "02_szures/prisma_flow.json", "exists": app.store.etag("02_szures/prisma_flow.json")
                             is not None}}
    return Result(gui_wording(redact_obj(data, str(app.project_root))), S_PRISMA, warnings=warnings, etag=etag)


def get_update(req):
    app = req.app
    app.require_open()
    _require_state(app)
    doc, etag, probs = _load(app, UPDATE_REL, "update-search", required=True)
    if doc is None:
        return Result({"exists": False, "path": UPDATE_REL}, S_UPDATE)
    data = dict(doc)
    data.update({"exists": True, "path": UPDATE_REL, "problems": probs})
    return Result(gui_wording(redact_obj(data, str(app.project_root))), S_UPDATE, etag=etag)


def get_decisions(req):
    app = req.app
    app.require_open()
    _require_state(app)
    limit = int_arg(req, "limit", 50, 1, 1000)
    items, n, bad = _read_decisions(app, limit=limit)
    return Result(redact_obj({"items": list(reversed(items)), "total": n, "bad_lines": bad, "path": DECISIONS_REL},
                             str(app.project_root)), S_DECISIONS)


# ---------------------------------------------------------------------------- regisztráció
JOB_RESPONSE = {"type": "object", "required": ["status"], "properties": {
    "status": {"enum": ["queued", "running", "done", "error", "timeout", "cancelled", "cancel_requested"]}}}


def register(router):
    router.add("GET", "/api/headhunter/status", get_status, schema=S_STATUS)
    router.add("GET", "/api/headhunter/sources", get_sources, schema=S_SOURCES)
    router.add("GET", "/api/headhunter/reviews", get_reviews, schema=S_REVIEWS)
    router.add("GET", "/api/headhunter/reviews/<review_id>", get_review, schema=S_REVIEW)
    router.add("GET", "/api/headhunter/studies", get_studies, schema=S_STUDIES)
    router.add("GET", "/api/headhunter/proposals", get_proposals, schema=S_PROPOSALS)
    router.add("GET", "/api/headhunter/overlap", get_overlap, schema=S_OVERLAP)
    router.add("GET", "/api/headhunter/merged", get_merged, schema=S_MERGED)
    router.add("GET", "/api/headhunter/prisma", get_prisma, schema=S_PRISMA)
    router.add("GET", "/api/headhunter/update", get_update, schema=S_UPDATE)
    router.add("GET", "/api/headhunter/decisions", get_decisions, schema=S_DECISIONS)
    router.add("POST", "/api/headhunter/run", post_run, schema=S_JOB, request_schema=RUN_SCHEMA,
               response_schema=JOB_RESPONSE)
    router.add("GET", "/api/headhunter/runs", get_runs, schema=S_JOBS)
    router.add("GET", "/api/headhunter/runs/<job_id>", get_run, schema=S_JOB, response_schema=JOB_RESPONSE)
    router.add("POST", "/api/headhunter/runs/<job_id>/cancel", post_cancel, schema=S_JOB, response_schema=JOB_RESPONSE)
    router.add("POST", "/api/headhunter/decide", post_decide, schema=S_JOB, request_schema=DECIDE_SCHEMA,
               response_schema=JOB_RESPONSE)
