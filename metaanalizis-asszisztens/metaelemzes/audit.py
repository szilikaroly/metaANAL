# -*- coding: utf-8 -*-
"""Projekt-audit: kereszt-artefaktum X-szabályok a projektmappán (szk.ma.project-audit/v1; terv 6.4, 4.15; E8).

Az X-szabályok a fájlok ÖSSZHANGJÁT ellenőrzik (adattábla ↔ eredet-oldalfájl ↔ spec ↔ commit-futás ↔ értékelés ↔
GRADE / SoF ↔ ábra ↔ PRISMA), nem az adat helyességét (az a V-szabályoké). A kódok — a V- és P-szabályokhoz
hasonlóan — tudásbázis-azonosítók: a RULES szótár formátuma a validate.RULES-é (súlyosság, cím, teendő, forrás), a
szakaszt a RULE_STAGES, a kapcsolódó KB-szabályokat a KB_REFS, az angol címet a TITLES_EN adja.

A projektmappa (terv 2.4; minden fájl opcionális — ami hiányzik, azt a szabály nem tudja ellenőrizni, és ezt a
kimenet `not_checked` listája indokkal sorolja fel, találat helyett; ha a projektben sehol sincs ilyen fájl, a tétel
projektszintű, outcome: null):

  ma-projekt.json                          kimenetek (id, data, measure, primary_spec), review_type, appraisal_tools,
                                           conventions
  projekt.sqlite                           ellenőrzőpontok (szakasz-kontextus), döntések (X016), grade-sorok (X007, X019)
  02_szures/prisma_flow.json | prisma_folyamat.md     I (X014), included_meta (X015), undecided (X020), kizárási
                                           okok (X021)
  02_szures/*.csv|*.tsv                    szűrési döntési napló (rec_id | pmid, decision, reason, phase; X021)
  03_adatok/<kimenet>.csv                  adattábla (row_uid, study_id, estimated, rob, forras_oldal …)
  03_adatok/<kimenet>.prov.json            szk.ma.provenance/v1 (X010, X013, X022)
  03_adatok/studies.json                   szk.ma.studies/v1 (I; címkék; design; kimenetenkénti vizsgálatok — X015)
  03_adatok/kettos/<kimenet>.{A,B}.csv     kettős kinyerés + <kimenet>.consensus.json (szk.ma.consensus/v1; X009)
  04_torzitas_kockazat/appraisals/*.json   szk.appraisal/v1 (X003, X004, X011, X012, X017)
  05_elemzes/specs/*.json                  szk.ma.analysis-spec/v1 (X005, X006, X016)
  05_elemzes/<kimenet>/<run_id>/run.json   szk.ma.run/v1 (+ results.json: a futás tényleges szűrői és számai,
                                           plot_data.json); a commit-futás 05_elemzes/<kimenet>/run.json-ban, vagy — a
                                           projektnapló futásai között — bárhol (pl. a --project melletti
                                           alapértelmezett <adatmappa>/eredmeny) is lehet
  06_kezirat/grade/<kimenet>.grade.json    szk.ma.grade/v1 (X007, X019)
  06_kezirat/sof/<kimenet>.sof.json        szk.ma.sof/v1 (X008)
  06_kezirat/abrak/<név>.result.json       szk.figure-result/v1 (source.plot_sha256, run_id; clean, numbers; X002, X018)

A futás kimenete: a specje kimenete (a spec-fájl vagy a spec neve alapján), ennek hiányában a 05_elemzes/<kimenet>/
<futás> mappa, majd az adatfájlja szerinti ismert kimenet, végül az adatfájl neve. A futás-csoport (X001, X005,
X006, X014: csak a csoport legutóbbi futása számít) a spec-fájl; spec-fájl nélkül a spec neve és az elemzés tartalma
(adatfájl, opciók, szűrők) — így két különböző CLI-elemzés ugyanazon a táblán nem takarja el egymást. Az elsődleges
commit-futás (X007, X008, X015, X004 szűrői) az elsődleges spec legutóbbi futása.

A párhuzamosan fejlődő v1-modulokat (metaelemzes.appraisal: implikált ítélet, AMSTAR 2-besorolás; metaelemzes.
grade_help: abszolút hatás; metaelemzes.kettos / api.compare: a kettős kinyerés összevetése) az audit lustán tölti
be; ha egy modul hiányzik, a ráépülő rész kimarad (not_checked, okkal), vagy — ahol a terv rögzíti a tárolt alakot
(pl. a dokumentumba mentett implikált ítélet) — a fájlból dolgozik.

Belépési pontok: project_audit(mappa, stage=None) → szk.ma.project-audit/v1 szótár; audit_gate_errors(mappa) →
a FINAL audit-kaput elutasító (error szintű) találatok; checkpoint_gate_errors(mappa, szakasz) → egy szakasz PASS-át
blokkoló találatok (FINAL: minden error; S08-tól az X009 — GATE_STAGES); require_gate(mappa) → ValueError, ha van
ilyen; format_text(jelentés) → a CLI szöveges kimenete; audit_schema() → a szerződés JSON Schemája.

Szakasz-kontextus: az X001 S08-tól, az X004 és az X019 S13-tól hiba, előtte figyelmeztetés (ESCALATION); az X012
hiányosság-része és az X020 az S14-től (és a FINAL kéréskor) számít. A szakasz a `stage` paraméter (a FINAL kapu
'FINAL'-lal hív), ennek hiányában a projektnapló ellenőrzőpontjaiból adódik (a legnagyobb rögzített szakasz; PASS /
PASS_WITH_FIXES után a következő); napló vagy ellenőrzőpont nélkül ismeretlen — ilyenkor a szigorúbb (RULES szerinti)
súlyosság érvényes, és a késői (S14-es) ellenőrzések is futnak.
"""
import collections
import copy
import datetime
import hashlib
import importlib
import json
import math
import os
import posixpath
import re
import sqlite3
import unicodedata
from pathlib import Path

from . import __version__
from . import tableio
from .effect_sizes import REQUIRED_COLUMNS, PAIRED_INPUTS

SCHEMA = "szk.ma.project-audit/v1"
SCHEMA_ID = "urn:szk:contract:ma.project-audit:1"

# kód: (súlyosság, rövid cím, magyarázat/teendő, forrás) — a validate.RULES formátuma (a `kb build` betölti)
RULES = {
    "X001": ("error", "Elavult commit-futás: az adattábla a futás óta megváltozott",
             "A futás a tábla egy korábbi állapotából készült, így az eredményei — és minden rájuk hivatkozó GRADE-, "
             "SoF- és kéziratszám — nem a mostani adatot tükrözik. Futtasd újra ugyanazzal a speccel (ha a spec "
             "rögzíti a data.sha256-ot, előbb frissítsd), és a hivatkozásokat állítsd át az új futásra. Az S08 "
             "(szintézis) szakasztól hiba, előtte figyelmeztetés.", "engine"),
    "X003": ("error", "A tábla rob értéke eltér az értékelés végső összítéletétől",
             "A kinyerési tábla rob oszlopa nem egyezik a lezárt (konszenzusos) torzításikockázat-értékelés "
             "összítéletével, így a V019, a „magas RoB nélkül” érzékenységi futás és a GRADE RoB-doménje rossz "
             "értéket lát. Írd át a rob cellát az értékelés összítéletére (vagy javítsd az értékelést), majd "
             "futtasd újra az érintett elemzéseket.", "Cochrane Handbook 7–8; RoB 2 / ROBINS-I"),
    "X005": ("warning", "Becsült adatú sor van, de nincs „becsült nélkül” érzékenységi futás",
             "Futtass gyermek-futást a becsült / imputált sorok kizárásával (--exclude estimated=igen; specben: "
             "purpose sensitivity, parent az elsődleges spec), és közöld, változik-e a következtetés.",
             "Cochrane Handbook 6.5.2.10, 10.14"),
    "X006": ("warning", "Magas RoB-ú sor van, de nincs „magas RoB nélkül” érzékenységi futás",
             "Futtass gyermek-futást a magas torzítási kockázatú sorok kizárásával (--exclude rob=high; specben: "
             "purpose sensitivity, parent az elsődleges spec), és közöld az eredményt. A szűrőnek a mostani tábla "
             "minden magas RoB-ú sorát ki kell zárnia.", "Cochrane Handbook 7–8, 10.14"),
    "X010": ("warning", "Elemzett cellának nincs forrásoldala",
             "Minden elemzett számhoz rögzíts forráshelyet — oldalt vagy táblázat/ábra-lokátort az eredet-oldalfájl "
             "(.prov.json) source mezőjében, oldalfájl nélkül a forras_oldal oszlopban —, hogy az érték a "
             "közleményben visszakereshető és ellenőrizhető legyen.", "Cochrane Handbook 5; PRISMA 2020 9. tétel"),
    "X013": ("error", "A sor becsült-jelölése ellentmond a cellák eredetének",
             "Az eredet-oldalfájl (.prov.json) szerint a sor egy hatásméret-releváns cellája becsült / digitalizált "
             "/ imputált, de a sor estimated oszlopa nem 'igen' — vagy fordítva: a sor 'igen', de minden "
             "dokumentált cellája közölt érték. Hangold össze őket: az estimated oszlop vezérli a V018-at és az "
             "--exclude estimated=igen érzékenységi futást.", "Cochrane Handbook 6.5.2"),
    "X014": ("error", "Több elemzett vizsgálat, mint bevont vizsgálat (I)",
             "Az elemzett vizsgálatok (a commit-futás k-ja, illetve a tábla egyedi study_id-i) a bevont vizsgálatok "
             "(PRISMA I; studies.json) részhalmazai. Több karú vizsgálatnál a k a sorok száma, ilyenkor az egyedi "
             "study_id számít. Javítsd a studies.json-t, a PRISMA-számokat vagy az adattáblát.",
             "PRISMA 2020 1. ábra"),
    "X016": ("warning", "Protokoll-eltérés döntés nélkül: az elsődleges elemzés nem az előre rögzített",
             "Az elsődleges spec nincs előre rögzítve (prespecified: false), vagy eltér az előre rögzített spectől "
             "(modell, τ²-becslő, szűrők, adatfájl). Ha az eltérés indokolt, naplózd döntésként (project log <mappa> "
             "--agent … --stage S12 --kb X016,D-S12-006 --decision 'Protokoll-eltérés (<kimenet>): …' --rationale …), "
             "és a kéziratban közöld; különben az előre rögzített elemzés maradjon az elsődleges.",
             "PRISMA 2020 24c; Cochrane Handbook 10.14"),
    "X022": ("error", "Az eredet-oldalfájl nem a mostani adattáblához tartozik",
             "A .prov.json table_sha256-ja nem a mostani CSV hash-e (megszakadt kétfájlos írás vagy külső "
             "szerkesztés), és van olyan eredet-bejegyzés, amely nem egyeztethető a táblával (hiányzó sor vagy "
             "oszlop, eltérő érték). Ellenőrizd az érintett cellákat a forrással, majd mentsd újra az eredetet "
             "(mentéskor a table_sha256 frissül).", "engine"),
    # ---- v1 (E8 teljes; terv 6.4)
    "X002": ("warning", "Elavult ábra: az exportált ábra nem a legutóbbi futás adataiból készült",
             "Az ábra (06_kezirat/abrak/<név>.result.json) olyan plot_data.json-ból készült, amely már nem a kimenet "
             "legutóbbi commit-futásáé, vagy a futás plot_data.json-ja azóta megváltozott. A kéziratban így más számok "
             "állhatnak, mint az elemzésben. Rajzold újra az ábrát a legutóbbi futásból (Ábra-export), és cseréld le a "
             "kéziratban.", "PRISMA 2020 20b; terv 5.3"),
    "X004": ("error", "Elemzett vizsgálat torzításikockázat-értékelés nélkül",
             "Minden elemzett vizsgálathoz kell egy lezárt (complete vagy konszenzusos) értékelés a kimenethez rögzített "
             "eszközzel (ma-projekt.json appraisal_tools; pl. randomizált vizsgálatnál RoB 2). Enélkül a „magas RoB "
             "nélkül” érzékenységi futás és a GRADE torzítási kockázat doménje nem megalapozott. Töltsd ki és zárd le "
             "az értékelést a Torzítási kockázat lapon (a vázlat és a jóvá nem hagyott AI-vázlat nem számít). Az S13 "
             "(bizonyosság) szakasztól hiba, előtte figyelmeztetés.", "Cochrane Handbook 7–8; RoB 2 / ROBINS-I"),
    "X007": ("error", "A GRADE-ítélet számai eltérnek az elsődleges commit-futástól",
             "A projektnapló GRADE-sorában (vagy a rögzített 06_kezirat/grade/<kimenet>.grade.json-ban) álló "
             "vizsgálatszám (k), résztvevőszám vagy hatás-szöveg nem az, amit a kimenet elsődleges elemzésének legutóbbi "
             "commit-futása ad: a GRADE egy régebbi eredményre épül. Nyisd meg a GRADE-lapot, frissítsd a futásra, nézd "
             "át a doménítéleteket, és rögzítsd újra.", "GRADE Handbook 5; Cochrane Handbook 14"),
    "X008": ("error", "A SoF-táblázat egy cellája eltér a motor eredményétől",
             "A 06_kezirat/sof/<kimenet>.sof.json egy cellája (k, résztvevők, a relatív hatás szövege vagy az abszolút "
             "hatás /1000) nem egyezik azzal, amit a motor a kimenet elsődleges commit-futásából számol: kézzel írt vagy "
             "régi futásból maradt szám került a táblázatba. Generáld újra a SoF-ot a GRADE-lapon (a számokat a motor "
             "sof() függvénye adja), ne szerkeszd kézzel.", "Cochrane Handbook 14.1; GRADE Handbook 5.2"),
    "X009": ("error", "Kettős kinyerés lezáratlan eltéréssel",
             "A két független kinyerő táblája (03_adatok/kettos/<kimenet>.A.csv és .B.csv) eltér, és nem minden "
             "eltérésre van érvényes egyeztetési döntés a <kimenet>.consensus.json-ban — vagy egy döntés óta valamelyik "
             "tábla megváltozott, így a döntés elavult. Amíg ez így van, a szintézis (S08) nem indulhat. Döntsd el az "
             "eltéréseket a Kettős kinyerés lapon (röviden indokolva), majd írd ki a konszenzus-táblát.",
             "Cochrane Handbook 5.5.2"),
    "X011": ("error", "Predikciósmodell-áttekintés: vizsgálat PROBAST+AI-értékelés nélkül",
             "A ma-projekt.json szerint az áttekintés predikciós modellekről szól (review_type: prediction_model), ezért "
             "minden bevont vizsgálat modelljének torzítási kockázatát és alkalmazhatóságát PROBAST+AI-jal kell "
             "értékelni — a RoB 2 vagy a ROBINS-I erre nem alkalmas. Töltsd ki és zárd le a PROBAST+AI-értékelést a "
             "felsorolt vizsgálatokra; az AI-vázlat csak emberi jóváhagyás után számít.",
             "PROBAST+AI (Moons et al. 2025); Cochrane Prognosis Methods"),
    "X012": ("warning", "AMSTAR 2 önellenőrzés hiányos, vagy a besorolás nem egyezik a válaszokkal",
             "A saját áttekintés AMSTAR 2 önellenőrzésében (04_torzitas_kockazat/appraisals/review.amstar2.*.json) nincs "
             "mind a 16 tétel megválaszolva, vagy a rögzített összbesorolás egyik konvencióval sem az, ami a válaszokból "
             "adódik (a projekt konvenciója a KB AMSTAR2-00 szerint: kritikus tételen a „részben igen” nem hiba). "
             "Válaszold meg a hiányzó tételeket, és a besorolást igazítsd a motor által számolthoz (vagy javítsd a "
             "válaszokat). A hiányosság az S14 (jelentés) szakasztól és a FINAL kéréskor számít.",
             "AMSTAR 2 (Shea et al. 2017); KB AMSTAR2-00"),
    "X015": ("warning", "A metaanalízisbe vont vizsgálatok száma eltér a commit-futásétól",
             "A kimenetenként közölt „metaanalízisben” szám (a studies.json vizsgálatainak outcomes listája, illetve a "
             "PRISMA-folyamat included_meta mezője) nem egyezik azzal, ahány vizsgálat a kimenet elsődleges commit-"
             "futásában ténylegesen szerepel. Javítsd a vizsgálat-térképet (melyik vizsgálat melyik kimenethez tartozik) "
             "vagy a PRISMA-számot, és a különbség okát a kéziratban közöld.", "PRISMA 2020 16b, 20b"),
    "X017": ("error", "Az implikálttól eltérő ítélet indoklás nélkül",
             "Egy domén- vagy összítélet eltér attól, amit a válaszok a motor szabálya szerint kikényszerítenek "
             "(implikált ítélet), de nincs megadva a felülbírálás oka (override_reason). Az eltérés nem tilos — az "
             "ítélet emberi döntés —, de indokolni kell, és döntésként naplózni. Az értékelőlapon írd be röviden, miért "
             "tér el az ítéleted (pl. „a hiányzó adat aránya elhanyagolható”), vagy igazítsd az ítéletet a válaszokhoz.",
             "RoB 2 útmutató (Sterne et al. 2019); Cochrane Handbook 8"),
    "X018": ("warning", "Kéziratba szánt ábra minőségellenőrzése nem tiszta",
             "A 06_kezirat/abrak/<név>.result.json szerint az ábra QC-je nem tiszta (felirat lóg ki a dobozából, vagy "
             "görbét, nyilat takar), egy karakter hiányzik a betűkészletből, vagy a számhűség-ellenőrzés eltérést talált "
             "(egy szám nem pontosan az, amit a motor számolt). Rajzold újra az ábrát (pl. szélesebb doboz, más "
             "elrendezés), és csak tiszta QC-vel tedd a kéziratba.", "terv 5.3, 6.7"),
    "X019": ("error", "A GRADE publikációs torzítás doménje feloldatlan („suspected”)",
             "A „gyanított” (suspected) publikációs torzítás addig feloldatlan, amíg ember nem választ 0-t (nem minősít "
             "le) vagy −1-et (leminősít) indoklással; az ilyen kimenet GRADE-je nem rögzíthető, és nem kerülhet a "
             "SoF-ba. Döntsd el a GRADE-lapon (a „Miért?” panel mutatja a tesztek értelmezhetőségét és a keresés "
             "teljességét), vagy válaszd a „nem észlelt” / „erősen gyanított” ítéletet. Az S13 (bizonyosság) szakasztól "
             "hiba, előtte figyelmeztetés.", "GRADE Handbook 5.2.5; 11. döntés, 4. pont"),
    "X020": ("warning", "A szűrésben még elbírálatlan rekord van",
             "A composer PRISMA-exportja (02_szures/prisma_flow.json) szerint vannak még döntésre váró (undecided) "
             "rekordok. A végleges áttekintés előtt minden teljes szöveget be kell vonni vagy okkal kizárni, különben a "
             "PRISMA-számok nem véglegesek. Zárd le a döntéseket a composerben és frissítsd az exportot, vagy a "
             "folyamatábrán jelöld őket „folyamatban lévő” vizsgálatként. Az S14-től és a FINAL kéréskor számít.",
             "PRISMA 2020 1. ábra; composer"),
    "X021": ("warning", "A teljes szöveg szintű kizárási okok eltérnek a szűrési döntési naplótól",
             "A PRISMA-folyamat okonkénti kizárási számai (02_szures/prisma_flow.json vagy prisma_folyamat.md) nem "
             "egyeznek a 02_szures mappába exportált döntési naplóval (a teljes szöveg szintű kizárások okonként "
             "összeszámolva). A folyamatábra számainak a naplóból kell jönniük: exportáld újra a naplót a szűrőeszközből "
             "(vagy frissítsd a composer-exportot), és javítsd az eltérő számot.", "PRISMA 2020 16a"),
}

# kód → a szabály szakasza (a KB decision_rule.stage_id-je és a találat 'stage' mezője)
RULE_STAGES = {"X001": "S08", "X003": "S06", "X005": "S12", "X006": "S12", "X010": "S05", "X013": "S05",
               "X014": "S04", "X016": "S12", "X022": "S05",
               "X002": "S14", "X004": "S06", "X007": "S13", "X008": "S13", "X009": "S05", "X011": "S06",
               "X012": "S14", "X015": "S04", "X017": "S06", "X018": "S14", "X019": "S13", "X020": "S04",
               "X021": "S04"}

# kód → kapcsolódó tudásbázis-szabályok (a találat 'kb_refs' mezője)
KB_REFS = {"X001": (), "X003": ("D-S06-008", "D-S13-003"), "X005": ("D-S12-003", "D-S05-023"),
           "X006": ("D-S12-002",), "X010": ("D-S05-001", "D-S05-002"), "X013": ("D-S05-023",),
           "X014": ("P010",), "X016": ("D-S12-006", "D-S02-017"), "X022": ("D-S05-001",),
           "X002": ("D-S14-008", "D-S14-012"), "X004": ("D-S06-008", "D-S06-016"), "X007": ("D-S13-026", "D-S13-002"),
           "X008": ("D-S13-011", "D-S13-012"), "X009": ("D-S05-001",), "X011": ("D-S13-023", "D-S02-011"),
           "X012": ("D-S13-021", "D-S04-014"), "X015": ("D-S04-015", "P010"), "X017": ("D-S06-004", "D-S06-005"),
           "X018": ("D-S14-008", "D-S14-012"), "X019": ("D-S13-008",), "X020": ("D-S04-008", "P013"),
           "X021": ("D-S04-008", "D-S04-013", "P007")}

# kód → a szakasz, amelytől a RULES szerinti súlyosság érvényes; előtte 'warning' (6.4: „error (S08-tól)”,
# „warning → error S13-tól”)
ESCALATION = {"X001": "S08", "X004": "S13", "X019": "S13"}

# kód → a szakasz, amelynek PASS-át a szabály error-találata már a FINAL előtt blokkolja (6.4: „error (S08 PASS
# előtt)”); a FINAL audit-kapu minden error-találatra elutasít
GATE_STAGES = {"X009": "S08"}

# az S14-től (és a FINAL kéréskor, illetve ismeretlen szakasznál) futó ellenőrzések: X012 hiányosság, X020
LATE_STAGE = "S14"

# a szabályok angol címe (api.rules_export, KB; a magyar a RULES-ban)
TITLES_EN = {
    "X001": "Stale commit run: the data table changed after the run",
    "X002": "Stale figure: the exported figure was not drawn from the latest run",
    "X003": "Table rob value differs from the final appraisal judgement",
    "X004": "Analysed study without a risk-of-bias appraisal",
    "X005": "Estimated rows present but no sensitivity run without them",
    "X006": "High-RoB rows present but no sensitivity run without them",
    "X007": "GRADE numbers differ from the primary commit run",
    "X008": "Summary-of-findings cell differs from the engine result",
    "X009": "Double data extraction has unresolved disagreements",
    "X010": "Analysed cell has no source page",
    "X011": "Prediction-model review: study without a PROBAST+AI appraisal",
    "X012": "AMSTAR 2 self-assessment incomplete or rating inconsistent with the answers",
    "X013": "Row estimated flag contradicts cell provenance",
    "X014": "More analysed studies than included studies (I)",
    "X015": "Number of studies in the meta-analysis differs from the commit run",
    "X016": "Protocol deviation without decision: primary analysis is not the prespecified one",
    "X017": "Judgement differs from the implied one without a reason",
    "X018": "Manuscript figure did not pass quality control",
    "X019": "GRADE publication bias domain unresolved ('suspected')",
    "X020": "Screening still has undecided records",
    "X021": "Full-text exclusion reasons differ from the screening decision log",
    "X022": "Provenance sidecar does not belong to the current data table",
}

DATA_DIR = "03_adatok"
ANALYSIS_DIR = "05_elemzes"
SPEC_DIR = ANALYSIS_DIR + "/specs"
APPRAISAL_DIR = "04_torzitas_kockazat/appraisals"
STUDIES_FILE = DATA_DIR + "/studies.json"
PRISMA_JSON = "02_szures/prisma_flow.json"
PRISMA_MD = "02_szures/prisma_folyamat.md"
META_FILE = "ma-projekt.json"
JOURNAL_FILE = "projekt.sqlite"
SCREENING_DIR = "02_szures"
KETTOS = "kettos"
GRADE_DIR = "06_kezirat/grade"
SOF_DIR = "06_kezirat/sof"
FIGURES_DIR = "06_kezirat/abrak"
GRADE_SCHEMA = "szk.ma.grade/v1"
SOF_SCHEMA = "szk.ma.sof/v1"
CONSENSUS_SCHEMA = "szk.ma.consensus/v1"
FINAL = "FINAL"

SEVERITIES = ("error", "warning", "info")
_SEV_LABEL = {"error": "HIBA", "warning": "FIGYELEM", "info": "MEGJEGYZÉS"}
_ESTIMATED_METHODS = ("estimated", "digitized", "imputed")
_FINAL_STATUSES = ("complete", "final", "consensus")
# nem torzításikockázat-ítéletet adó eszközök (az összítéletük 'high' / 'low' mást jelent: bizalom, bizonyosság)
_NON_ROB_TOOLS = ("amstar", "grade", "tripod", "prisma", "nos", "newcastle")
_CONSENSUS = ("consensus", "konszenzus")
_DEVIATION_REFS = ("X016", "D-S12-006", "D-S02-017")
_DEVIATION_WORDS = re.compile(r"protokoll-?\s?elt[ée]r[ée]s|protocol deviation", re.I)
# az elemzés tartalmát nem érintő opciók (X016 összevetés)
_COSMETIC = ("title", "left_label", "right_label", "label_col", "plot_schema", "plot_locale", "svg_annotate")
# a forrásHELY oszlopa (a „forras” / „source” nem: D-S05-002 szerint a forrás TÍPUSA, pl. forras=absztrakt)
_PAGE_COLUMNS = ("forrasoldal", "forrashely", "oldal", "oldalszam", "sourcepage", "page", "pages", "locator")
_MAX_LIST = 500


# ------------------------------------------------------------------ segédek
def _now_utc(now=None):
    t = now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    if isinstance(t, str):
        return t
    if t.tzinfo is not None:
        t = t.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stage_index(stage):
    if stage is None:
        return None
    if stage == FINAL:
        return 99
    m = re.fullmatch(r"S(\d{2})", str(stage))
    return int(m.group(1)) if m else None


def _norm_stage(stage):
    """'S8', 's08', 'FINAL', 'S07-S09' (→ a legnagyobb) → 'S08' | 'FINAL'; None → None; érvénytelen → ValueError."""
    if stage is None or not str(stage).strip():
        return None
    from .projekt import parse_stage
    st = parse_stage(stage)
    return st[-1] if st else None


def severity_for(code, stage=None):
    """A szabály súlyossága a megadott szakasz-kontextusban (ismeretlen szakasz: a RULES szerinti)."""
    sev = RULES[code][0]
    start = ESCALATION.get(code)
    idx = _stage_index(stage)
    if start is not None and idx is not None and idx < _stage_index(start):
        return "warning"
    return sev


def _clean(obj):
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _short(h):
    return (h[:12] + "…") if isinstance(h, str) and len(h) > 12 else (h or "–")


def _fold(s):
    """Kis/nagybetű-, ékezet- és szóközfüggetlen összevetési kulcs."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", s).strip()


def _colkey(s):
    return re.sub(r"[^a-z0-9]", "", _fold(s))


def _mentions(text, name):
    return bool(name) and re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(name), text or "", re.I) is not None


def _relpath_ok(p):
    return isinstance(p, str) and bool(p) and re.fullmatch(r"(?!/)(?![A-Za-z]:)(?!.*\.\.)[^\\:]+", p) is not None


def _plural_list(items, limit=8):
    items = list(items)
    s = ", ".join(items[:limit])
    return s + (" … (+%d)" % (len(items) - limit) if len(items) > limit else "")


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _count(v):
    """Doboz-érték → nemnegatív egész vagy None (a PRISMA-fájlok számai; a hibás értéket a P001 jelzi)."""
    if isinstance(v, str) and re.fullmatch(r"\s*\d+\s*", v):
        return int(v)
    if isinstance(v, float) and v.is_integer() and v >= 0:
        return int(v)
    return v if _int(v) is not None and v >= 0 else None


class _Ctx(object):
    """Egy audit-futás állapota: a projektgyökér, a beolvasott fájlok hash-e, a nem ellenőrizhető tételek."""

    def __init__(self, root, stage):
        self.root = os.path.abspath(root)
        self.stage = stage
        self.inputs = {}
        self.not_checked = []
        self.findings = []
        self._json = {}
        self._tables = {}

    # utak: a projektgyökérhez relatív, '/'-elválasztós alak
    def abs(self, rel):
        if os.path.isabs(rel):
            return rel
        return os.path.join(self.root, *rel.split("/"))

    def rel(self, path):
        ap = os.path.abspath(path)
        try:
            r = os.path.relpath(ap, self.root)
        except ValueError:
            return ap
        if r == os.pardir or r.startswith(os.pardir + os.sep) or os.path.isabs(r):
            return ap
        return r.replace(os.sep, "/")

    def isfile(self, rel):
        return os.path.isfile(self.abs(rel))

    def read_bytes(self, rel):
        with open(self.abs(rel), "rb") as fh:
            raw = fh.read()
        self.inputs[self.rel(self.abs(rel))] = hashlib.sha256(raw).hexdigest()
        return raw

    def sha(self, rel):
        key = self.rel(self.abs(rel))
        if key not in self.inputs:
            try:
                self.read_bytes(rel)
            except OSError:
                return None
        return self.inputs.get(key)

    def load_json(self, rel, code=None, outcome=None, what=None):
        """JSON-fájl → objektum; hiányzó fájl → None (csendben); hibás → None + not_checked-tétel."""
        key = self.rel(self.abs(rel))
        if key in self._json:
            return self._json[key]
        obj = None
        try:
            raw = self.read_bytes(rel)
            obj = json.loads(raw.decode("utf-8-sig"), parse_constant=lambda name: None)
        except FileNotFoundError:
            obj = None
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self.skip(code, outcome, "%s nem olvasható%s: %s" % (key, (" (%s)" % what) if what else "",
                                                                   _exc_text(exc)))
            obj = None
        self._json[key] = obj
        return obj

    def table(self, rel, outcome=None, codes=()):
        """Adattábla → (sorok, meta, row_uid-ok) vagy None (hiányzó / olvashatatlan fájl: not_checked)."""
        key = self.rel(self.abs(rel))
        if key in self._tables:
            return self._tables[key]
        res = None
        if not self.isfile(rel):
            for c in codes:
                self.skip(c, outcome, "az adattábla nem található: %s" % key)
        else:
            try:
                raw = self.read_bytes(rel)
                rows, meta = tableio.read_table_bytes(raw, key)
                res = (rows, meta, tableio.row_uids(rows, meta))
            except Exception as exc:     # a hibás tábla az auditot nem állíthatja le
                for c in codes:
                    self.skip(c, outcome, "az adattábla nem olvasható (%s): %s" % (key, _exc_text(exc)))
        self._tables[key] = res
        return res

    def raw_cells(self, rel, meta):
        """A tábla nyers cellaszövegei a beolvasott sorok sorrendjében: [{kanonikus oszlop: szöveg}] (X022: a
        bevitt érték először szövegként vetendő össze); olvashatatlan fájlnál None."""
        try:
            with open(self.abs(rel), "rb") as fh:
                _, rows_text, _ = tableio.read_raw(raw=fh.read())
        except (OSError, ValueError):
            return None
        mapping = meta.get("mapping") or {}
        index = {}
        for p, col in enumerate(meta.get("columns") or []):
            index.setdefault(mapping.get(col, col), p)
        out = []
        for pos in meta.get("row_positions") or range(len(rows_text)):
            cells = rows_text[pos] if 0 <= pos < len(rows_text) else []
            out.append({f: (cells[p] if p < len(cells) else "") for f, p in index.items()})
        return out

    def skip(self, code, outcome, reason):
        item = {"code": code, "outcome": outcome, "reason": reason}
        if item not in self.not_checked:
            self.not_checked.append(item)

    def add(self, code, outcome, detail, artifacts=(), suggested_command=None, **extra):
        f = collections.OrderedDict()
        f["code"] = code
        f["severity"] = severity_for(code, self.stage)
        f["stage"] = RULE_STAGES[code]
        f["outcome"] = outcome
        f["title"] = RULES[code][1]
        f["detail"] = detail
        f["advice"] = RULES[code][2]
        f["source"] = RULES[code][3]
        f["artifacts"] = list(dict.fromkeys(a for a in artifacts if _relpath_ok(a)))   # 4.0: projekt-relatív utak
        f["suggested_command"] = list(suggested_command) if suggested_command else None
        f["kb_refs"] = list(KB_REFS.get(code, ()))
        for k, v in extra.items():
            if v is not None:
                f[k] = v[:_MAX_LIST] if isinstance(v, list) else v
        self.findings.append(f)


def _exc_text(exc):
    return "%s: %s" % (type(exc).__name__, exc)


# ------------------------------------------------------------------ a projekt beolvasása
class _Outcome(object):
    def __init__(self, oid):
        self.id = oid
        self.data = None
        self.measure = None
        self.primary_spec = None
        self.names = []          # a ma-projekt.json name {hu, en} értékei (a GRADE-napló outcome-mezőjéhez)
        self.raw = {}            # a ma-projekt.json kimenet-bejegyzése
        self.runs = []
        self.specs = []


def _load_meta(ctx):
    if not ctx.isfile(META_FILE):
        return None
    from . import projekt
    try:
        meta = projekt.load_project_meta(ctx.root)
        ctx.sha(META_FILE)
        return meta
    except (ValueError, OSError) as exc:
        ctx.skip(None, None, "%s érvénytelen, a kimenetek a többi fájlból adódnak: %s" % (META_FILE, exc))
        raw = ctx.load_json(META_FILE)
        return raw if isinstance(raw, dict) else None


def _load_specs(ctx):
    out = []
    d = ctx.abs(SPEC_DIR)
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.lower().endswith(".json"):
            continue
        rel = SPEC_DIR + "/" + name
        doc = ctx.load_json(rel, what="elemzési spec")
        if not isinstance(doc, dict):
            continue
        data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
        filters = doc.get("filters") if isinstance(doc.get("filters"), dict) else {}
        out.append({"rel": rel, "doc": doc, "name": doc.get("name") if isinstance(doc.get("name"), str) else None,
                    "outcome": doc.get("outcome") if isinstance(doc.get("outcome"), str) else None,
                    "purpose": doc.get("purpose"), "prespecified": doc.get("prespecified"),
                    "parent": doc.get("parent"), "data_path": data.get("path"),
                    "filters": (_str_list(filters.get("exclude")), _str_list(filters.get("include")))})
    return out


def _str_list(v):
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _load_runs(ctx):
    """A projekt commit-futásai (spec.project_run_files: 05_elemzes/<kimenet>/<futás>/run.json, 05_elemzes/
    <kimenet>/run.json és a projektnapló futásainak mappái), időrendben (indulás, majd a run.json módosítási ideje)."""
    from . import spec as S
    out = []
    for path, layout, folder, logged in S.project_run_files(ctx.root):
        rel = ctx.rel(path)
        doc = ctx.load_json(rel, what="futás-leíró")
        if not isinstance(doc, dict):
            continue
        if doc.get("mode", "commit") != "commit":
            continue
        spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
        data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
        if os.path.isabs(rel) and logged and not any(
                (not f or _same_path(ctx, data.get("path"), f)) and (not h or h == data.get("sha256"))
                for f, h in logged):
            continue        # a projekten kívüli mappában már nem a projekt futása van (más adat)
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            mtime = 0
        d = os.path.dirname(rel) if os.path.isabs(rel) else (rel.rsplit("/", 1)[0] if "/" in rel else ".")
        out.append({"rel": rel, "dir": d, "layout": layout, "folder": folder, "outcome": None, "doc": doc,
                    "run_id": doc.get("run_id") if isinstance(doc.get("run_id"), str) else os.path.basename(d),
                    "spec_name": spec.get("name") if isinstance(spec.get("name"), str) else None,
                    "spec_path": spec.get("path") if isinstance(spec.get("path"), str) else None,
                    "spec_sha": spec.get("sha256") if isinstance(spec.get("sha256"), str) else None,
                    "data_path": data.get("path") if isinstance(data.get("path"), str) else None,
                    "data_sha": data.get("sha256") if isinstance(data.get("sha256"), str) else None,
                    "k": _int(doc.get("k")),
                    "sort": (str(doc.get("started") or ""), mtime, str(doc.get("run_id") or ""), rel)})
    out.sort(key=lambda r: r["sort"])
    return out


def _run_results(ctx, run):
    """A futás results.json-ja: (exclude, include, measure) — a futás TÉNYLEGES szűrői."""
    if "results" not in run:
        res = ctx.load_json(run["dir"] + "/results.json", what="futás-eredmény")
        excl, incl, measure, content = None, None, None, None
        if isinstance(res, dict):
            inp = res.get("input") if isinstance(res.get("input"), dict) else {}
            if isinstance(inp.get("filters"), dict):
                excl, incl = _str_list(inp["filters"].get("exclude")), _str_list(inp["filters"].get("include"))
            opts = res.get("options") if isinstance(res.get("options"), dict) else {}
            measure = opts.get("measure") if isinstance(opts.get("measure"), str) else None
            if opts:
                key = {k: v for k, v in opts.items() if k not in _COSMETIC and k != "filter_report"}
                content = json.dumps([run["data_path"], key, sorted(excl or []), sorted(incl or [])],
                                     sort_keys=True, ensure_ascii=False, default=str)
        run["results"] = (excl, incl, measure)
        run["content"] = content
    return run["results"]


def _run_group(ctx, run):
    """A futás csoportja (a csoport legutóbbi futása számít): spec-fájlos futásnál a spec (neve vagy útja); spec-fájl
    nélkül a spec neve + az elemzés tartalma (adatfájl, opciók, szűrők a results.json-ból; ennek hiányában a spec
    hash-e, végül a futásmappa) — a CLI minden --data futása ugyanazt a (táblanévből képzett) spec-nevet kapja."""
    if run["spec_path"]:
        return ("spec", run["spec_name"] or run["spec_path"])
    _run_results(ctx, run)
    if run.get("content"):
        return ("run", run["spec_name"], run["content"])
    if run["spec_sha"]:
        return ("run", run["spec_name"], run["spec_sha"])
    return ("dir", run["dir"])


def _run_filters(ctx, run, specs_by_path):
    """A futás szűrői: results.json input.filters, ennek hiányában a futás specje."""
    excl, incl, _ = _run_results(ctx, run)
    if excl is not None or incl is not None:
        return excl or [], incl or []
    sp = specs_by_path.get(run["spec_path"]) if run["spec_path"] else None
    if sp is not None:
        return sp["filters"]
    return None


def _latest_runs(ctx, runs):
    """Futás-csoportonként (_run_group) a legutolsó commit-futás."""
    last = collections.OrderedDict()
    for r in runs:
        last[_run_group(ctx, r)] = r
    return list(last.values())


def _outcomes(ctx, meta, specs, runs):
    outs = collections.OrderedDict()

    def get(oid):
        if oid not in outs:
            outs[oid] = _Outcome(oid)
        return outs[oid]
    if isinstance(meta, dict) and isinstance(meta.get("outcomes"), list):
        for o in meta["outcomes"]:
            if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not o["id"]:
                continue
            oc = get(o["id"])
            oc.raw = o
            nm = o.get("name")
            oc.names = [v for v in (nm.values() if isinstance(nm, dict) else [nm]) if isinstance(v, str) and v.strip()]
            oc.data = o.get("data") if _relpath_ok(o.get("data")) else None
            oc.measure = o.get("measure") if isinstance(o.get("measure"), str) else None
            oc.primary_spec = o.get("primary_spec") if _relpath_ok(o.get("primary_spec")) else None
    for sp in specs:
        if sp["outcome"]:
            get(sp["outcome"]).specs.append(sp)
    _assign_run_outcomes(ctx, runs, specs, outs)
    for r in runs:
        get(r["outcome"]).runs.append(r)
    for oc in outs.values():
        _resolve_outcome(ctx, oc)
    d = ctx.abs(DATA_DIR)
    if os.path.isdir(d):
        covered = {os.path.normcase(os.path.normpath(ctx.abs(_prov_rel(oc.data)))) for oc in outs.values()}
        for name in sorted(os.listdir(d)):
            if name.endswith(".prov.json") and os.path.normcase(os.path.join(d, name)) not in covered:
                oid = name[:-len(".prov.json")]
                if oid not in outs:
                    _resolve_outcome(ctx, get(oid))
    return list(outs.values())


def _data_key(ctx, path):
    return os.path.normcase(os.path.normpath(ctx.abs(path))) if path else None


def _assign_run_outcomes(ctx, runs, specs, outs):
    """A futások kimenete (r['outcome']): 1. a futás specje (spec-fájl útja, ennek hiányában egyértelmű név) szerint;
    2. a 05_elemzes/<kimenet>/<futás> mappa szerint (ha a mappanév egy ismert kimenet slugja, az a kimenet);
    3. az adatfájl szerint, ha az pontosan egy ismert kimeneté; 4. a 05_elemzes/<kimenet>/run.json mappája, ha
    ismert kimenet; 5. az adatfájl neve; 6. a mappa."""
    from . import spec as S
    by_rel = {sp["rel"]: sp for sp in specs}
    names = collections.Counter(sp["name"] for sp in specs if sp["name"])
    by_name = {sp["name"]: sp for sp in specs if sp["name"] and names[sp["name"]] == 1}
    known = list(outs) + [sp["outcome"] for sp in specs if sp["outcome"] and sp["outcome"] not in outs]
    slug_of = collections.defaultdict(set)
    for oid in known:
        slug_of[S.outcome_dir(oid)].add(oid)
    data_of = collections.defaultdict(set)
    for oid, oc in outs.items():
        if oc.data:
            data_of[_data_key(ctx, oc.data)].add(oid)
    for sp in specs:
        if sp["outcome"] and _relpath_ok(sp["data_path"]):
            data_of[_data_key(ctx, sp["data_path"])].add(sp["outcome"])
    rest = []
    for r in runs:
        sp = by_rel.get(r["spec_path"]) if r["spec_path"] else None
        if sp is None and r["spec_name"] in by_name:
            cand = by_name[r["spec_name"]]
            # a CLI-futás spec-neve a tábla nevéből képzett: csak egyező adatfájl esetén ugyanaz a spec
            if r["spec_path"] or not cand["data_path"] or _data_key(ctx, cand["data_path"]) == _data_key(
                    ctx, r["data_path"]):
                sp = cand
        if sp is not None and sp["outcome"]:
            r["outcome"] = sp["outcome"]
        elif r["layout"] == "nested":
            hit = slug_of.get(r["folder"]) or set()
            r["outcome"] = (r["folder"] if r["folder"] in hit or len(hit) != 1 else next(iter(hit)))
        else:
            rest.append(r)
            continue
        if r["data_path"]:
            data_of[_data_key(ctx, r["data_path"])].add(r["outcome"])
    for r in rest:
        hit = data_of.get(_data_key(ctx, r["data_path"])) if r["data_path"] else None
        if hit and len(hit) == 1:
            r["outcome"] = next(iter(hit))
        elif r["layout"] == "flat" and r["folder"] in known:
            r["outcome"] = r["folder"]
        elif r["data_path"]:
            r["outcome"] = os.path.splitext(os.path.basename(r["data_path"]))[0] or r["folder"] or r["dir"]
        else:
            r["outcome"] = r["folder"] or os.path.basename(r["dir"])


def _prov_rel(data):
    """03_adatok/<kimenet>.csv → 03_adatok/<kimenet>.prov.json"""
    return (data[:-4] if data.lower().endswith(".csv") else data) + ".prov.json"


def _resolve_outcome(ctx, oc):
    """A kimenet adatfájlja és hatásmértéke, ha a ma-projekt.json nem adja meg: elsődleges spec, többi spec,
    legutóbbi futás, végül 03_adatok/<kimenet>.csv."""
    specs = _primary_candidates(oc) + oc.specs
    if oc.data is None:
        oc.data = next((sp["data_path"] for sp in specs if _relpath_ok(sp["data_path"])), None)
    if oc.data is None:
        oc.data = next((ctx.rel(ctx.abs(r["data_path"])) for r in reversed(oc.runs)
                        if r["data_path"] and ctx.isfile(r["data_path"])), None)
    if oc.data is None:
        oc.data = "%s/%s.csv" % (DATA_DIR, oc.id)
    if oc.measure is None:
        opts = [sp["doc"].get("options") for sp in specs]
        oc.measure = next((o["measure"] for o in opts if isinstance(o, dict) and isinstance(o.get("measure"), str)),
                          None)
    if oc.measure is None:
        oc.measure = next((m for m in (_run_results(ctx, r)[2] for r in reversed(oc.runs)) if m), None)


def _primary_candidates(oc):
    if oc.primary_spec:
        return [sp for sp in oc.specs if sp["rel"] == oc.primary_spec]
    return [sp for sp in oc.specs if sp["purpose"] == "primary"]


def _same_path(ctx, a, b):
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(ctx.abs(a))) == os.path.normcase(os.path.normpath(ctx.abs(b)))


def _journal(ctx):
    """(checkpointok, aktív döntések, GRADE-sorok) a projektnaplóból, csak olvasva; napló nélkül (None, None, None)."""
    p = ctx.abs(JOURNAL_FILE)
    if not os.path.isfile(p):
        return None, None, None
    ctx.sha(JOURNAL_FILE)
    try:
        con = sqlite3.connect(Path(p).as_uri() + "?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error as exc:
        ctx.skip(None, None, "%s nem nyitható meg: %s" % (JOURNAL_FILE, _exc_text(exc)))
        return None, None, None
    try:
        con.row_factory = sqlite3.Row
        cps = [dict(r) for r in con.execute("SELECT stage_id, verdict FROM checkpoint ORDER BY id")]
        decs = [dict(r) for r in con.execute(
            "SELECT id, stage_id, decision, rationale, alternatives, kb_refs FROM decision "
            "WHERE status = 'active' ORDER BY id")]
        try:
            grades = [dict(r) for r in con.execute(
                "SELECT id, outcome, k, participants, effect, risk_of_bias, publication_bias, certainty FROM grade "
                "ORDER BY id")]
        except sqlite3.Error:
            grades = None           # nagyon régi napló grade-tábla nélkül
        return cps, decs, grades
    except sqlite3.Error as exc:
        ctx.skip(None, None, "%s nem olvasható: %s" % (JOURNAL_FILE, _exc_text(exc)))
        return None, None, None
    finally:
        con.close()


def _journal_stage(checkpoints):
    """A projekt elért szakasza az ellenőrzőpontokból: a legnagyobb rögzített szakasz, PASS / PASS_WITH_FIXES
    után a következő (legfeljebb S14); FINAL-ellenőrzőpont után FINAL; ellenőrzőpont nélkül None."""
    best = None
    for cp in checkpoints or ():
        sid = str(cp.get("stage_id") or "").strip().upper()
        if sid == FINAL:
            return FINAL
        m = re.fullmatch(r"S(\d{2})", sid)
        if not m:
            continue
        i = int(m.group(1))
        if cp.get("verdict") in ("PASS", "PASS_WITH_FIXES"):
            i = min(i + 1, 14)
        best = i if best is None else max(best, i)
    return None if best is None else "S%02d" % best


# ------------------------------------------------------------------ közös tábla-segédek
def _row_label(row, i):
    lab = row.get("study")
    return str(lab) if lab not in (None, "") else "#%d" % (i + 1)


def _columns(meta):
    mapping = meta.get("mapping") or {}
    return [mapping.get(c, c) for c in meta.get("columns") or []]


def _relevant_fields(measure, meta):
    """A hatásméret-releváns kanonikus oszlopok (a tábla sorrendjében); ismeretlen mértéknél minden számoszlop."""
    m = (measure or "").upper()
    if m in REQUIRED_COLUMNS:
        f = set(REQUIRED_COLUMNS[m])
        if m == "GEN":
            f |= {"vi", "sei"}
        if m in ("MC", "SMCC"):
            f |= {c for alts in PAIRED_INPUTS.values() for alt in alts for c in alt}
    else:
        f = set(tableio.NUMERIC) - {"year"}
    return [c for c in dict.fromkeys(_columns(meta)) if c in f]


def _present(v):
    return v is not None and not (isinstance(v, str) and not v.strip())


def _prov(ctx, oc, codes):
    """A kimenet eredet-oldalfájlja → (rel, dokumentum, {(row_uid, field): cella}) vagy None."""
    rel = _prov_rel(oc.data)
    if not ctx.isfile(rel):
        for c in codes:
            ctx.skip(c, oc.id, "nincs eredet-oldalfájl (%s)" % rel)
        return None
    doc = ctx.load_json(rel, codes[0] if codes else None, oc.id, "eredet-oldalfájl")
    if not isinstance(doc, dict) or not isinstance(doc.get("cells"), list):
        if doc is not None:
            for c in codes:
                ctx.skip(c, oc.id, "%s nem szk.ma.provenance/v1 (hiányzó cells lista)" % rel)
        return None
    index = {}
    for cell in doc["cells"]:
        if isinstance(cell, dict) and isinstance(cell.get("row_uid"), str) and isinstance(cell.get("field"), str):
            index[(cell["row_uid"], cell["field"])] = cell
    return rel, doc, index


def _cell_estimated(cell):
    return cell.get("estimated") is True or cell.get("method") in _ESTIMATED_METHODS


def _cell_located(cell):
    src = cell.get("source") if isinstance(cell.get("source"), dict) else {}
    page = src.get("page")
    if _int(page) is not None and page >= 1:
        return True
    if isinstance(page, str) and page.strip():
        return True
    if isinstance(src.get("locator"), str) and src["locator"].strip():
        return True
    # számított cella: a forrása az átváltás bemenete (convert-request a conversion mezőben)
    return cell.get("method") == "calculated" and isinstance(cell.get("conversion"), dict) and bool(cell["conversion"])


# ------------------------------------------------------------------ X001
def _x001(ctx, oc):
    if not oc.runs:
        ctx.skip("X001", oc.id, "nincs commit-futás (%s/%s/*/run.json, és a projektnapló futásai között sincs)" % (
            ANALYSIS_DIR, oc.id))
        return
    primary = {sp["name"] for sp in _primary_candidates(oc)}
    for run in _latest_runs(ctx, oc.runs):
        if not run["data_path"] or not run["data_sha"]:
            ctx.skip("X001", oc.id, "%s: hiányzik a data.path vagy a data.sha256" % run["rel"])
            continue
        data_rel = ctx.rel(ctx.abs(run["data_path"]))
        now = ctx.sha(run["data_path"]) if ctx.isfile(run["data_path"]) else None
        if now == run["data_sha"].lower():
            continue
        if run["spec_name"]:
            which = "%s spec (%s) legutóbbi commit-futásának (%s)" % (
                "Az elsődleges" if run["spec_name"] in primary else "A(z)", run["spec_name"], run["run_id"])
        else:
            which = "A(z) %s commit-futás" % run["run_id"]
        if now is None:
            detail = "%s adatfájlja már nem létezik: %s" % (which, data_rel)
        else:
            detail = "%s adat-hash-e %s, a %s mostani hash-e %s" % (which, _short(run["data_sha"]), data_rel,
                                                                     _short(now))
        ctx.add("X001", oc.id, detail, [run["rel"], data_rel], _rerun_command(run), run_id=run["run_id"])


def _rerun_command(run):
    if run["spec_path"]:
        return ["ma.py", "analyze", "--spec", run["spec_path"], "--project", "."]
    argv = run["doc"].get("equivalent_argv")
    if isinstance(argv, list) and all(isinstance(x, str) for x in argv) and argv:
        out, skip = [], False
        for x in argv:
            if skip:
                skip = False
                continue
            if x == "--out":
                skip = True
                continue
            if x.startswith("--out="):
                continue
            out.append(x)
        return out
    return None


# ------------------------------------------------------------------ X005 / X006
_CHILD = {"X005": ("estimated", "estimated=igen", "becsult_nelkul", "becsült"),
          "X006": ("rob", "rob=high", "magas_rob_nelkul", "magas RoB-ú")}


def _flagged_rows(code, rows):
    if code == "X005":
        return [i for i, r in enumerate(rows) if tableio.yes_no(r.get("estimated")) == "yes"]
    return [i for i, r in enumerate(rows) if tableio.rob_category(r.get("rob")) == "high"]


def _removes_all(filters, column, rows, meta, flagged):
    """A szűrők (exclude, include) a mostani táblán minden jelölt sort kizárnak, marad sor, és van köztük az
    oszlopra (estimated / rob) vonatkozó szűrő."""
    excl, incl = filters
    refs = False
    for cond in list(excl) + list(incl):
        col = str(cond).partition("=")[0]
        try:
            if tableio.resolve_column(col, meta, rows) == column:
                refs = True
        except ValueError:
            return False
    if not refs:
        return False
    try:
        kept = tableio.apply_filters(rows, excl or None, incl or None, meta=meta)
    except ValueError:
        return False
    kept_ids = {id(r) for r in kept}
    return bool(kept) and not any(id(rows[i]) in kept_ids for i in flagged)


def _x005_x006(ctx, oc, code, tab, specs_by_path):
    column, filt, suffix, what = _CHILD[code]
    rows, meta, uids = tab
    flagged = _flagged_rows(code, rows)
    if not flagged:
        return
    runs = [r for r in _latest_runs(ctx, oc.runs)
            if r["data_path"] is None or _same_path(ctx, r["data_path"], oc.data)]
    for r in runs:
        f = _run_filters(ctx, r, specs_by_path)
        if f is not None and _removes_all(f, column, rows, meta, flagged):
            return
    pending = [sp["name"] or sp["rel"] for sp in oc.specs
               if _removes_all(sp["filters"], column, rows, meta, flagged)]
    labels = [_row_label(rows[i], i) for i in flagged]
    detail = "%d %s sor (%s), de egyik commit-futás szűrője sem zárja ki mindet" % (
        len(flagged), what, _plural_list(labels))
    if pending:
        detail += "; a(z) %s spec megvan, de nincs (érvényes) commit-futása" % _plural_list(pending)
    ctx.add(code, oc.id, detail, [oc.data] + [sp["rel"] for sp in oc.specs if (sp["name"] or sp["rel"]) in pending],
            _child_command(ctx, oc, filt, suffix), row_uids=[uids[i] for i in flagged])


def _parent_spec(ctx, oc, specs_by_path):
    prim = _primary_candidates(oc)
    if len(prim) == 1:
        return prim[0]["doc"]
    for r in reversed(oc.runs):
        sp = specs_by_path.get(r["spec_path"]) if r["spec_path"] else None
        if sp is not None and sp["purpose"] in (None, "primary"):
            return sp["doc"]
    return None


def _child_command(ctx, oc, filt, suffix):
    """Javasolt parancs a gyermek-futáshoz: az elsődleges spec + a kizáró szűrő (spec.argv_from_spec)."""
    specs_by_path = {sp["rel"]: sp for sp in oc.specs}
    parent = _parent_spec(ctx, oc, specs_by_path)
    if parent is not None and isinstance(parent.get("name"), str):
        child = copy.deepcopy(parent)
        name = re.sub(r"[^a-z0-9_-]", "_", ("%s_%s" % (parent["name"], suffix)).lower())[:64]
        child.update({"name": name, "purpose": "sensitivity", "parent": parent["name"]})
        child.pop("prespecified", None)
        f = child.get("filters") if isinstance(child.get("filters"), dict) else {}
        child["filters"] = {"exclude": _str_list(f.get("exclude")) + [filt], "include": _str_list(f.get("include"))}
        try:
            from . import spec as S
            return ["ma.py"] + S.argv_from_spec(child, project_root=ctx.root,
                                                out_dir=ctx.abs("%s/%s/%s" % (ANALYSIS_DIR, oc.id, name)),
                                                log_to_project=True)
        except Exception:    # érvénytelen spec vagy betöltési hiba: tartalék parancs
            pass
    if oc.measure and _relpath_ok(oc.data):
        return ["ma.py", "analyze", "--data", oc.data, "--measure", oc.measure.upper(), "--exclude", filt,
                "--out", "%s/%s/%s" % (ANALYSIS_DIR, oc.id, suffix), "--project", "."]
    return None


# ------------------------------------------------------------------ X010 / X013
def _x010(ctx, oc, tab, prov):
    rows, meta, uids = tab
    fields = _relevant_fields(oc.measure, meta)
    if prov is not None:
        rel, _, index = prov
        missing = []
        for i, (r, u) in enumerate(zip(rows, uids)):
            for f in fields:
                if _present(r.get(f)):
                    cell = index.get((u, f))
                    if cell is None or not _cell_located(cell):
                        missing.append((i, u, f))
        if missing:
            nrows = len({m[0] for m in missing})
            by_row = collections.OrderedDict()
            for i, u, f in missing:
                by_row.setdefault(i, []).append(f)
            ctx.add("X010", oc.id, "%d elemzett cellának nincs forrásoldala (%d sor): %s" % (
                len(missing), nrows, _plural_list("%s (%s)" % (_row_label(rows[i], i), ", ".join(fs))
                                                  for i, fs in by_row.items())),
                [oc.data, rel], None, cells=[{"row_uid": u, "field": f} for _, u, f in missing])
        return
    mapping = meta.get("mapping") or {}
    page_col = next((mapping.get(c, c) for c in meta.get("columns") or [] if _colkey(c) in _PAGE_COLUMNS), None)
    if page_col is None:
        ctx.skip("X010", oc.id, "nincs eredet-oldalfájl és forras_oldal oszlop sem (%s)" % oc.data)
        return
    bad = [i for i, r in enumerate(rows) if any(_present(r.get(f)) for f in fields) and not _present(r.get(page_col))]
    if bad:
        ctx.add("X010", oc.id, "%d sorban üres a '%s' oszlop (eredet-oldalfájl nincs): %s" % (
            len(bad), page_col, _plural_list(_row_label(rows[i], i) for i in bad)), [oc.data], None,
            row_uids=[uids[i] for i in bad])


def _x013(ctx, oc, tab, prov):
    if prov is None:
        return
    rows, meta, uids = tab
    rel, _, index = prov
    fields = _relevant_fields(oc.measure, meta)
    has_col = "estimated" in set(_columns(meta))
    wrong_no, wrong_yes = [], []
    for i, (r, u) in enumerate(zip(rows, uids)):
        present = [f for f in fields if _present(r.get(f))]
        cells = {f: index.get((u, f)) for f in present}
        est = [f for f, c in cells.items() if c is not None and _cell_estimated(c)]
        flag = tableio.yes_no(r.get("estimated")) == "yes"
        if est and not flag:
            wrong_no.append((i, est))
        elif flag and present and all(c is not None for c in cells.values()) and not est:
            wrong_yes.append(i)
    if not wrong_no and not wrong_yes:
        return
    parts = []
    if wrong_no:
        parts.append("eredet szerint becsült, de a sor estimated jelzője %s: %s" % (
            "nem 'igen'" if has_col else "hiányzik (nincs estimated oszlop)",
            _plural_list("%s (%s)" % (_row_label(rows[i], i), ", ".join(fs)) for i, fs in wrong_no)))
    if wrong_yes:
        parts.append("a sor estimated = 'igen', de minden dokumentált cellája közölt érték: %s" %
                     _plural_list(_row_label(rows[i], i) for i in wrong_yes))
    ctx.add("X013", oc.id, "; ".join(parts), [oc.data, rel], None,
            row_uids=[uids[i] for i, _ in wrong_no] + [uids[i] for i in wrong_yes])


# ------------------------------------------------------------------ X022
def _values_match(row_value, entered, field, raw=None, meta=None):
    """A bevitt érték (value_as_entered) egyezik-e a tábla cellájával: azonos nyers szöveg, vagy — számoszlopban a
    tábla saját tizedesjelével és tagolójával értelmezve ('1.234' egy tizedesvesszős táblában 1234) — azonos szám."""
    if entered is None:
        return True
    if raw is not None and isinstance(entered, str) and entered.strip() == raw.strip():
        return True
    num = None
    if isinstance(entered, str) and field in tableio.NUMERIC and meta is not None:
        try:
            num = tableio._parse_numeric_cell(entered, field, meta.get("decimal_mark"), meta.get("delimiter") or ",")[0]
        except ValueError:
            return False
        na = num is None
    else:
        try:
            na = tableio.parse_number(entered) is None
        except ValueError:
            na = False
    if row_value is None or (isinstance(row_value, str) and not row_value.strip()):
        return na or not str(entered).strip()
    if na:
        return False
    if num is not None and isinstance(row_value, (int, float)):
        return abs(float(row_value) - float(num)) <= 1e-9 * max(1.0, abs(float(row_value)), abs(float(num)))
    return tableio.values_equal(row_value, entered, field)


def _x022(ctx, oc, tab, prov):
    if prov is None:
        return
    rel, doc, index = prov
    want = doc.get("table_sha256")
    if not isinstance(want, str) or not want:
        ctx.skip("X022", oc.id, "%s: hiányzik a table_sha256" % rel)
        return
    now = ctx.sha(oc.data)
    if now == want.lower():
        return
    if tab is None:
        ctx.add("X022", oc.id, "a %s eredet-oldalfájl táblája (%s) %s; egyik cella sem egyeztethető" % (
            rel, oc.data, "nem olvasható" if now else "nem található"), [rel, oc.data], None)
        return
    rows, meta, uids = tab
    cols = set(_columns(meta))
    by_uid = dict(zip(uids, rows))
    raws = ctx.raw_cells(oc.data, meta)
    raw_by_uid = dict(zip(uids, raws)) if raws is not None and len(raws) == len(uids) else {}
    bad = []
    for (u, f), cell in index.items():
        if u not in by_uid:
            bad.append((u, f, "nincs ilyen sor"))
        elif f not in cols:
            bad.append((u, f, "nincs ilyen oszlop"))
        elif not _values_match(by_uid[u].get(f), cell.get("value_as_entered"), f,
                               (raw_by_uid.get(u) or {}).get(f), meta):
            bad.append((u, f, "eltérő érték (eredet: %r)" % (cell.get("value_as_entered"),)))
    if not bad:
        return
    label = {u: _row_label(r, i) for i, (u, r) in enumerate(zip(uids, rows))}
    ctx.add("X022", oc.id, "a %s table_sha256-ja %s, a %s mostani hash-e %s; %d nem egyeztethető cella: %s" % (
        rel, _short(want), oc.data, _short(now), len(bad),
        _plural_list("%s/%s — %s" % (label.get(u, u), f, why) for u, f, why in bad)),
        [rel, oc.data], None, cells=[{"row_uid": u, "field": f} for u, f, _ in bad])


# ------------------------------------------------------------------ X003
def _appraisals(ctx):
    """Lezárt értékelések: [{'rel', 'study', 'tool', 'outcome', 'category', 'raw', 'consensus', 'updated'}]."""
    d = ctx.abs(APPRAISAL_DIR)
    if not os.path.isdir(d):
        ctx.skip("X003", None, "nincs értékelés-mappa (%s)" % APPRAISAL_DIR)
        return None
    out = []
    for name in sorted(os.listdir(d)):
        if not name.lower().endswith(".json"):
            continue
        rel = APPRAISAL_DIR + "/" + name
        doc = ctx.load_json(rel, "X003", None, "értékelés")
        if not isinstance(doc, dict) or doc.get("schema") not in (None, "szk.appraisal/v1"):
            continue
        status = str(doc.get("status") or "").strip().lower()
        if status not in _FINAL_STATUSES:
            continue
        target = doc.get("target") if isinstance(doc.get("target"), dict) else {}
        overall = doc.get("overall") if isinstance(doc.get("overall"), dict) else {}
        stem = name[:-5].split(".")
        tool = doc.get("tool") if isinstance(doc.get("tool"), str) else (stem[1] if len(stem) > 1 else "")
        if _colkey(tool).startswith(_NON_ROB_TOOLS):
            continue
        study = target.get("study_id") if isinstance(target.get("study_id"), str) else stem[0]
        raw = overall.get("judgement")
        if not isinstance(raw, str) or not raw.strip():
            continue
        cat = tableio.rob_category(raw)
        if not cat:
            ctx.skip("X003", None, "%s: az összítélet (%r) nem RoB-kategória" % (rel, raw))
            continue
        assessor = _fold(doc.get("assessor"))
        out.append({"rel": rel, "study": study, "tool": tool,
                    "outcome": target.get("outcome") if isinstance(target.get("outcome"), str) else None,
                    "category": cat, "raw": raw,
                    "consensus": status == "consensus" or assessor in _CONSENSUS or "consensus" in stem[2:],
                    "updated": str(doc.get("updated") or "")})
    if not out:
        ctx.skip("X003", None, "nincs lezárt (status: complete) értékelés (%s)" % APPRAISAL_DIR)
    return out


def _final_judgements(ctx, appraisals, oid):
    """vizsgálat → (kategória, nyers ítélet, [fájlok]) a kimenetre vonatkozó lezárt értékelésekből."""
    groups = collections.OrderedDict()
    for a in appraisals:
        if a["outcome"] is not None and a["outcome"] != oid:
            continue
        groups.setdefault(_fold(a["study"]), []).append(a)
    out = {}
    for key, items in groups.items():
        cons = [a for a in items if a["consensus"]]
        if cons:
            pick = sorted(cons, key=lambda a: a["updated"])[-1:]
        elif len({a["category"] for a in items}) == 1:
            pick = items
        else:
            ctx.skip("X003", oid, "%s: a lezárt értékelések összítélete eltér, konszenzus nincs (%s)" % (
                items[0]["study"], ", ".join(a["rel"] for a in items)))
            continue
        out[key] = (pick[0]["category"], pick[0]["raw"], [a["rel"] for a in items])
    return out


def _study_keys(row, label_to_id):
    keys = []
    for v in (row.get("study_id"), row.get("study")):
        if _present(v):
            keys.append(_fold(v))
    if _present(row.get("study")) and _fold(row.get("study")) in label_to_id:
        keys.append(label_to_id[_fold(row.get("study"))])
    return keys


def _x003(ctx, oc, tab, appraisals, label_to_id):
    rows, meta, uids = tab
    finals = _final_judgements(ctx, appraisals, oc.id)
    if not finals:
        return
    if "rob" not in set(_columns(meta)):
        ctx.skip("X003", oc.id, "az adattáblában nincs rob oszlop (%s)" % oc.data)
        return
    bad, files = [], []
    for i, (r, u) in enumerate(zip(rows, uids)):
        hit = next((finals[k] for k in _study_keys(r, label_to_id) if k in finals), None)
        if hit is None:
            continue
        cat, raw, rels = hit
        if tableio.rob_category(r.get("rob")) != cat:
            cell = r.get("rob")
            bad.append((i, u, "%s: rob = %s, az értékelés összítélete: '%s'" % (
                _row_label(r, i), ("'%s'" % cell) if _present(cell) else "üres", raw)))
            files += rels
    if bad:
        ctx.add("X003", oc.id, "; ".join(b[2] for b in bad[:20]) + (" … (+%d)" % (len(bad) - 20) if len(bad) > 20
                                                                     else ""),
                [oc.data] + files, None, row_uids=[b[1] for b in bad])


# ------------------------------------------------------------------ X014
def _included_studies(ctx):
    """(I, forrás) — studies.json, ennek hiányában a PRISMA-fájlok; nincs adat: (None, None)."""
    st = ctx.load_json(STUDIES_FILE, "X014", None, "vizsgálat-jegyzék")
    if isinstance(st, dict) and isinstance(st.get("studies"), list):
        return len(st["studies"]), STUDIES_FILE
    from . import prisma
    flow = ctx.load_json(PRISMA_JSON, "X014", None, "PRISMA-folyamat")
    if isinstance(flow, dict):
        i = _count(prisma.normalize(flow)[0].get("included_studies"))
        if i is not None:
            return i, PRISMA_JSON
    if ctx.isfile(PRISMA_MD):
        try:
            text = ctx.read_bytes(PRISMA_MD).decode("utf-8-sig")
            i = _count(prisma.parse_markdown_table(text).get("included_studies"))
        except (OSError, UnicodeDecodeError, ValueError):
            i = None
        if i is not None:
            return i, PRISMA_MD
    return None, None


def _x014(ctx, oc, tab, included):
    n_i, src = included
    if n_i is None:
        ctx.skip("X014", oc.id, "a bevont vizsgálatok száma (I) ismeretlen (%s, %s vagy %s)" % (
            STUDIES_FILE, PRISMA_JSON, PRISMA_MD))
        return
    problems, artifacts = [], [src]
    n_sid = None
    if tab is not None and "study_id" in set(_columns(tab[1])):
        n_sid = len({_fold(r.get("study_id")) for r in tab[0] if _present(r.get("study_id"))})
        if n_sid > n_i:
            problems.append("az adattábla egyedi study_id-jainak száma %d > I = %d" % (n_sid, n_i))
            artifacts.append(oc.data)
    latest = [r for r in _latest_runs(ctx, oc.runs) if r["k"] is not None]
    if latest:
        top = max(latest, key=lambda r: r["k"])
        if top["k"] > n_i and (n_sid is None or n_sid > n_i):
            problems.append("a(z) %s commit-futás k = %d > I = %d" % (top["run_id"], top["k"], n_i))
            artifacts.append(top["rel"])
    elif n_sid is None:
        ctx.skip("X014", oc.id, "nincs commit-futás és study_id oszlop sem")
    if problems:
        ctx.add("X014", oc.id, "%s (I forrása: %s)" % ("; ".join(problems), src), artifacts, None)


# ------------------------------------------------------------------ X016
def _analysis_content(sp):
    """A spec elemzési tartalma összevetéshez: (adatfájl, opciók a DEFAULTS-szal kiegészítve, szűrők)."""
    doc = sp["doc"]
    opts = doc.get("options") if isinstance(doc.get("options"), dict) else {}
    try:
        from .pipeline import DEFAULTS
        full = dict(DEFAULTS)
    except Exception:
        full = {}
    full.update(opts)
    if isinstance(full.get("measure"), str):
        full["measure"] = full["measure"].upper()
    for k in _COSMETIC:
        full.pop(k, None)
    excl, incl = sp["filters"]
    return sp["data_path"], full, (sorted(excl), sorted(incl))


def _content_diff(a, b):
    """Az eltérések szövege: 'tau2: DL → REML'."""
    (pa, oa, fa), (pb, ob, fb) = _analysis_content(a), _analysis_content(b)
    out = []
    if pa != pb:
        out.append("adat: %s → %s" % (pa, pb))
    for k in list(oa) + [k for k in ob if k not in oa]:
        va, vb = oa.get(k), ob.get(k)
        if va != vb:
            out.append("%s: %s → %s" % (k, "alapérték" if va is None else json.dumps(va, ensure_ascii=False),
                                        "alapérték" if vb is None else json.dumps(vb, ensure_ascii=False)))
    for name, x, y in (("kizárás", fa[0], fb[0]), ("szűkítés", fa[1], fb[1])):
        if x != y:
            out.append("%s: %s → %s" % (name, ", ".join(x) or "–", ", ".join(y) or "–"))
    return out


def _deviation_decided(decisions, oc, primary, single):
    for d in decisions or ():
        refs = {x.strip() for x in re.split(r"[,;|\s]+", d.get("kb_refs") or "") if x.strip()}
        text = " ".join(str(d.get(k) or "") for k in ("decision", "rationale", "alternatives"))
        if not (refs & set(_DEVIATION_REFS) or _DEVIATION_WORDS.search(text)):
            continue
        if single or _mentions(text, oc.id) or _mentions(text, primary["name"]):
            return d
    return None


def _x016(ctx, oc, decisions, single):
    prim = _primary_candidates(oc)
    if oc.primary_spec and not prim:
        ctx.skip("X016", oc.id, "a ma-projekt.json primary_spec fájlja nem található (%s)" % oc.primary_spec)
        return
    if len(prim) > 1:
        ran = [sp for r in reversed(oc.runs) for sp in prim if r["spec_name"] == sp["name"]]
        if not ran:
            ctx.skip("X016", oc.id, "több elsődleges spec (%s), és egyiknek sincs commit-futása" % ", ".join(
                sp["rel"] for sp in prim))
            return
        prim = ran[:1]
    if not prim:
        if oc.specs:
            ctx.skip("X016", oc.id, "nincs elsődleges spec (purpose: primary vagy ma-projekt.json primary_spec)")
        return
    p = prim[0]
    prespec = [sp for sp in oc.specs if sp["prespecified"] is True and sp["purpose"] in (None, "primary")
               and sp is not p]
    reasons = []
    if p["prespecified"] is not True:
        same = [q for q in prespec if not _content_diff(q, p)]
        differing = [q for q in prespec if _content_diff(q, p)]
        if same:
            return
        if differing:
            q = differing[0]
            reasons.append("az elsődleges %s eltér az előre rögzített %s spectől: %s" % (
                p["rel"], q["rel"], "; ".join(_content_diff(q, p))))
        elif p["prespecified"] is False:
            reasons.append("az elsődleges %s nincs előre rögzítve (prespecified: false)" % p["rel"])
        else:
            ctx.skip("X016", oc.id, "nincs előre rögzített spec (prespecified: true), és az elsődleges %s sem jelöli "
                     "(prespecified hiányzik)" % p["rel"])
            return
    if not reasons:
        return
    if decisions is None:
        reasons.append("projektnapló (%s) nélkül döntés nem ellenőrizhető" % JOURNAL_FILE)
    elif _deviation_decided(decisions, oc, p, single) is not None:
        return
    else:
        reasons.append("a naplóban nincs rá döntés (kb: %s)" % ", ".join(_DEVIATION_REFS[:2]))
    ctx.add("X016", oc.id, "; ".join(reasons), [p["rel"]] + [q["rel"] for q in prespec], None, spec=p["name"])


# ------------------------------------------------------------------ v1: közös segédek
def _module(name):
    """A párhuzamosan fejlődő v1-modulok (appraisal, grade_help, kettos, api) lusta betöltése; a hiányzó vagy
    betöltéskor hibás modul → None (a ráépülő ellenőrzés kimarad, a not_checked okkal jelzi)."""
    try:
        return importlib.import_module("%s.%s" % (__package__, name))
    except Exception:       # noqa: BLE001 — ImportError és a félkész modul bármely betöltési hibája
        return None


def _late(stage):
    """Fut-e a késői (S14-es / FINAL) ellenőrzés: ismeretlen szakasznál igen (a szigorúbb eset)."""
    idx = _stage_index(stage)
    return idx is None or idx >= _stage_index(LATE_STAGE)


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _whole(v):
    """Egész értékű szám → int (a JSON 357347.0 is); más → None."""
    if _isnum(v) and float(v).is_integer():
        return int(v)
    return None


def _close(a, b, rel=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= rel * max(1.0, abs(float(a)), abs(float(b)))


def _nonempty(v):
    return isinstance(v, str) and bool(v.strip())


_FINAL_APPRAISAL = ("complete", "final", "consensus")
_REVIEW_TOOLS = ("amstar2", "grade", "tripod-ai", "tripod")       # nem vizsgálatonkénti RoB-eszközök (X004)
_NON_PLOT_KINDS = ("flowchart", "rob_traffic", "rob_summary")       # nem plot_data.json-ból készülő ábrák (X002)
_FIG_FORMATS = (".svg", ".pdf", ".png", ".tif", ".tiff", ".pptx", ".eps")


def _all_appraisals(ctx):
    """Minden értékelés, bármely státusszal: [{rel, doc, tool, unit, outcome, status, final, ai, updated}]; mappa
    nélkül None. Lezárt (final): complete / consensus, az AI-vázlat csak emberi jóváhagyással (approved_by; 6. döntés)."""
    d = ctx.abs(APPRAISAL_DIR)
    if not os.path.isdir(d):
        return None
    out = []
    for name in sorted(os.listdir(d)):
        if not name.lower().endswith(".json") or name.startswith("."):
            continue
        rel = APPRAISAL_DIR + "/" + name
        doc = ctx.load_json(rel, None, None, "értékelés")
        if not isinstance(doc, dict) or doc.get("schema") not in (None, "szk.appraisal/v1"):
            continue
        target = doc.get("target") if isinstance(doc.get("target"), dict) else {}
        stem = name[:-5].split(".")
        tool = doc.get("tool") if isinstance(doc.get("tool"), str) else (stem[1] if len(stem) > 1 else "")
        unit = next((target[k] for k in ("unit", "study_id") if _nonempty(target.get(k))), stem[0])
        status = str(doc.get("status") or "").strip().lower()
        ai = doc.get("origin") == "ai_draft"
        out.append({"rel": rel, "doc": doc, "tool": tool.strip().lower(), "unit": unit,
                    "outcome": target.get("outcome") if _nonempty(target.get("outcome")) else None,
                    "status": status, "ai": ai,
                    "final": status in _FINAL_APPRAISAL and (not ai or _nonempty(doc.get("approved_by"))),
                    "updated": str(doc.get("updated") or "")})
    return out


def _run_numbers(ctx, run):
    """A futás közölt számai: {k, participants, display_text {hu, en} | None, estimate_ci [3] (megjelenítési skála) |
    None, measure} — run.json + results.json."""
    if "numbers" not in run:
        doc = run["doc"]
        res = ctx.load_json(run["dir"] + "/results.json", what="futás-eredmény")
        res = res if isinstance(res, dict) else {}
        totals = res.get("totals") if isinstance(res.get("totals"), dict) else {}
        bt = res.get("back_transformed") if isinstance(res.get("back_transformed"), dict) else {}
        est = bt.get("estimate_ci")
        est = [x if _isnum(x) else None for x in est] if isinstance(est, list) and len(est) == 3 else None
        prim = doc.get("primary") if isinstance(doc.get("primary"), dict) else {}
        disp = prim.get("display_text") if isinstance(prim.get("display_text"), dict) else None
        part = _whole(totals.get("participants"))
        if part is None and isinstance(doc.get("participants_text"), dict):
            m = re.fullmatch(r"\s*(\d+)\s*", str(doc["participants_text"].get("en") or ""))
            part = int(m.group(1)) if m else None
        opts = res.get("options") if isinstance(res.get("options"), dict) else {}
        measure = doc.get("measure") if isinstance(doc.get("measure"), str) else opts.get("measure")
        k = run["k"]
        if k is None and isinstance(res.get("primary"), dict):
            k = _int(res["primary"].get("k"))
        run["numbers"] = {"k": k, "participants": part, "display_text": disp, "estimate_ci": est,
                          "measure": measure.upper() if isinstance(measure, str) else None}
    return run["numbers"]


def _primary_run(ctx, oc, specs_by_path):
    """A kimenet elsődleges commit-futása: az elsődleges spec (ma-projekt.json primary_spec vagy purpose: primary)
    legutóbbi futása; ennek hiányában a futás-leíró szerint 'primary' célú legutóbbi futás; végül, ha a kimenetnek
    egyetlen futás-csoportja van, annak legutóbbi futása. Nem egyértelmű → None."""
    prim = _primary_candidates(oc)
    rels = {sp["rel"] for sp in prim}
    names = {sp["name"] for sp in prim if sp["name"]}
    hits = [r for r in oc.runs if (r["spec_path"] and r["spec_path"] in rels) or (r["spec_name"] in names)]
    if hits:
        return hits[-1]

    def purpose(r):
        sp = r["doc"].get("spec") if isinstance(r["doc"].get("spec"), dict) else {}
        p = sp.get("purpose")
        if p is None and r["spec_path"] in specs_by_path:
            p = specs_by_path[r["spec_path"]]["purpose"]
        return p
    hits = [r for r in oc.runs if purpose(r) == "primary"]
    if hits:
        return hits[-1]
    latest = _latest_runs(ctx, oc.runs)
    return latest[0] if len(latest) == 1 else None


def _analysed_rows(ctx, tab, run, specs_by_path, data):
    """Az elemzett sorok indexei: a tábla (data) sorai a futás tényleges szűrőivel; futás nélkül, vagy ha a futás más
    adatfájlból készült, az összes sor."""
    rows, meta, _uids = tab
    idx = list(range(len(rows)))
    if run is None or (run["data_path"] and not _same_path(ctx, run["data_path"], data)):
        return idx
    f = _run_filters(ctx, run, specs_by_path)
    if f is not None and (f[0] or f[1]):
        try:
            kept = {id(r) for r in tableio.apply_filters(rows, f[0] or None, f[1] or None, meta=meta)}
        except ValueError:
            return idx
        idx = [i for i in idx if id(rows[i]) in kept]
    return idx


def _study_groups(rows, idx, label_to_id):
    """Sorok → vizsgálatok: OrderedDict(első kulcs → {keys, rows, label}) (többkarú vizsgálat: egy csoport)."""
    groups = collections.OrderedDict()
    for i in idx:
        keys = _study_keys(rows[i], label_to_id)
        if not keys:
            continue
        g = groups.get(keys[0])
        if g is None:
            sid = rows[i].get("study_id")
            g = groups[keys[0]] = {"keys": set(), "rows": [], "label": str(sid) if _present(sid) else _row_label(
                rows[i], i)}
        g["keys"].update(keys)
        g["rows"].append(i)
    return groups


# ------------------------------------------------------------------ X004
def _outcome_tools(meta, oc):
    """A kimenet vizsgálatonkénti értékelő eszközei: a kimenet saját appraisal_tool(s) mezője, különben a
    ma-projekt.json appraisal_tools-ából a nem áttekintés-szintűek (predikciósmodell-áttekintésnél a PROBAST+AI-t az
    X011 ellenőrzi)."""
    raw = oc.raw if isinstance(oc.raw, dict) else {}
    if _nonempty(raw.get("appraisal_tool")):
        return [raw["appraisal_tool"].strip().lower()]
    own = [t.strip().lower() for t in raw.get("appraisal_tools") or () if _nonempty(t)] \
        if isinstance(raw.get("appraisal_tools"), list) else []
    if own:
        return list(dict.fromkeys(own))
    meta = meta if isinstance(meta, dict) else {}
    tools = meta.get("appraisal_tools") if isinstance(meta.get("appraisal_tools"), list) else []
    pm = meta.get("review_type") == "prediction_model"
    return list(dict.fromkeys(t.strip().lower() for t in tools if _nonempty(t) and t.strip().lower() not in
                              _REVIEW_TOOLS and not (pm and t.strip().lower() == "probast-ai")))


def _expected_tools(tools, keys, design_of, AP):
    """Több eszköznél a vizsgálat elrendezése (studies.json design) szerinti eszköz, ha az a kimenet eszközei közt
    van (appraisal.instrument_route); különben bármelyik."""
    if len(tools) <= 1 or AP is None or not callable(getattr(AP, "instrument_route", None)):
        return tools
    design = next((design_of[k] for k in keys if k in design_of), None)
    if not design:
        return tools
    try:
        routed = [h.get("tool") for h in AP.instrument_route(design) if isinstance(h, dict)]
    except Exception:       # noqa: BLE001 — a javaslat csak szűkítés; hibánál bármelyik eszköz elfogadható
        return tools
    hit = next((t for t in routed if t in tools), None)
    return [hit] if hit else tools


def _x004(ctx, oc, tab, prun, specs_by_path, entries, tools, label_to_id, design_of, coverage):
    rows, _meta, uids = tab
    have, drafts = collections.defaultdict(set), collections.defaultdict(set)
    for e in entries:
        if e["tool"] not in tools or (e["outcome"] is not None and e["outcome"] != oc.id):
            continue
        (have if e["final"] else drafts)[_fold(e["unit"])].add(e["tool"])
    AP = _module("appraisal")
    groups = _study_groups(rows, _analysed_rows(ctx, tab, prun, specs_by_path, oc.data), label_to_id)
    missing = []
    for g in groups.values():
        want = _expected_tools(tools, g["keys"], design_of, AP)
        if any(t in have.get(k, ()) for k in g["keys"] for t in want):
            continue
        g["draft"] = any(t in drafts.get(k, ()) for k in g["keys"] for t in want)
        g["want"] = want
        missing.append(g)
    coverage[oc.id] = (len(groups) - len(missing), len(groups), tools)
    if not missing:
        return
    drafted = [g["label"] for g in missing if g["draft"]]
    detail = "%d/%d elemzett vizsgálatnak nincs lezárt értékelése a kimenet eszközével (%s): %s" % (
        len(missing), len(groups), ", ".join(tools), _plural_list(g["label"] for g in missing))
    if drafted:
        detail += "; csak vázlat (vagy jóvá nem hagyott AI-vázlat) van: %s" % _plural_list(drafted)
    if prun is not None:
        detail += " (az elemzett sorok a(z) %s futás szűrőivel)" % prun["run_id"]
    ctx.add("X004", oc.id, detail, [oc.data, APPRAISAL_DIR], None, studies=[g["label"] for g in missing],
            row_uids=[uids[i] for g in missing for i in g["rows"]])


# ------------------------------------------------------------------ X007 / X008
_NUM_RE = re.compile(r"[-−]?\d+(?:[.,]\d+)?")


def _numbers_in(text):
    return [float(x.replace("−", "-").replace(",", ".")) for x in _NUM_RE.findall(str(text or ""))]


def _effect_matches(text, display):
    """A hatás-szöveg tartalmazza-e a futás display_text-jének számait (sorrendben; tizedesvessző, U+2212 is jó).
    Szám nélküli szöveg (pl. „csökkenti”) nem vethető össze → egyezőnek számít."""
    if not _nonempty(text) or not _nonempty(display):
        return True
    if display in text:
        return True
    want, have = _numbers_in(display), _numbers_in(text)
    if not want or not have:
        return True
    pos = 0
    for w in want:
        while pos < len(have) and not _close(have[pos], w, 1e-12):
            pos += 1
        if pos >= len(have):
            return False
        pos += 1
    return True


def _number_mismatches(k, participants, effect, nums):
    """A GRADE / SoF rögzített számai (k, résztvevők, hatás-szöveg) vs. a futás → ['k: 12 ≠ 13', …]."""
    out = []
    kk = _whole(k) if not isinstance(k, str) else (int(k) if re.fullmatch(r"\s*\d+\s*", k) else None)
    if kk is not None and nums["k"] is not None and kk != nums["k"]:
        out.append("k: %d ≠ %d" % (kk, nums["k"]))
    pp = _whole(participants) if not isinstance(participants, str) else (
        int(participants) if re.fullmatch(r"\s*\d+\s*", participants) else None)
    if pp is not None and nums["participants"] is not None and pp != nums["participants"]:
        out.append("résztvevők: %d ≠ %d" % (pp, nums["participants"]))
    disp = nums["display_text"] or {}
    if isinstance(effect, dict):
        for lang in ("hu", "en"):
            if _nonempty(effect.get(lang)) and not _effect_matches(effect[lang], disp.get(lang)):
                out.append("hatás-szöveg (%s): %r — a futásé: %r" % (lang, effect[lang], disp.get(lang)))
    elif _nonempty(effect) and disp:
        if not any(_effect_matches(effect, disp.get(lang)) for lang in ("hu", "en") if disp.get(lang)):
            out.append("hatás-szöveg: %r — a futásé: %r" % (effect, disp.get("hu") or disp.get("en")))
    return out


def _grade_docs(ctx):
    """06_kezirat/grade/*.grade.json → [{rel, doc, outcome, status}]; mappa nélkül None."""
    d = ctx.abs(GRADE_DIR)
    if not os.path.isdir(d):
        return None
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".grade.json") or fn.startswith("."):
            continue
        rel = GRADE_DIR + "/" + fn
        doc = ctx.load_json(rel, None, None, "GRADE-dokumentum")
        if not isinstance(doc, dict) or doc.get("schema") not in (None, GRADE_SCHEMA):
            continue
        oid = doc.get("outcome_id") if _nonempty(doc.get("outcome_id")) else fn[:-len(".grade.json")]
        out.append({"rel": rel, "doc": doc, "outcome": oid, "status": doc.get("status")})
    return out


def _grade_rows_for(grade_rows, oc):
    keys = {_fold(oc.id)} | {_fold(n) for n in oc.names}
    return [g for g in grade_rows or () if _fold(g.get("outcome")) in keys]


def _x007(ctx, oc, prun, rows_for, docs_for, runs_by_id):
    probs, artifacts = [], []
    if not rows_for and not docs_for:
        ctx.skip("X007", oc.id, "nincs GRADE-ítélet ehhez a kimenethez (projektnapló grade-sor vagy %s/%s.grade.json)"
                 % (GRADE_DIR, oc.id))
        return
    if not rows_for and not any(e["doc"].get("status") == "recorded" for e in docs_for):
        ctx.skip("X007", oc.id, "a GRADE-ítélet még piszkozat (%s); a rögzített ítéletet vetjük össze a futással" %
                 ", ".join(e["rel"] for e in docs_for))
        return
    if prun is None:
        ctx.skip("X007", oc.id, "nincs (egyértelmű) elsődleges commit-futás, amellyel a GRADE számai összevethetők")
        return
    nums = _run_numbers(ctx, prun)
    if rows_for:
        g = rows_for[-1]
        for p in _number_mismatches(g.get("k"), g.get("participants"), g.get("effect"), nums):
            probs.append("napló grade #%s: %s" % (g.get("id"), p))
        artifacts.append(JOURNAL_FILE)
    for e in docs_for:
        doc = e["doc"]
        if doc.get("status") != "recorded":
            continue                    # a piszkozatot a GRADE-lap frissíti; az X007 a rögzített ítéletet nézi
        rs = doc.get("run_summary") if isinstance(doc.get("run_summary"), dict) else None
        if rs is None and doc.get("run_id") in runs_by_id:
            r = runs_by_id[doc["run_id"]]
            n2 = _run_numbers(ctx, r)
            rs = {"k": n2["k"], "participants": n2["participants"], "display_text": n2["display_text"]}
        if rs is None:
            ctx.skip("X007", oc.id, "%s: nincs run_summary, és a hivatkozott futás (%s) nem található" % (
                e["rel"], doc.get("run_id") or "–"))
            continue
        found = _number_mismatches(rs.get("k"), rs.get("participants"), rs.get("display_text"), nums)
        if found:
            ref = " (a GRADE a(z) %s futásra hivatkozik)" % doc["run_id"] if doc.get("run_id") and doc.get(
                "run_id") != prun["run_id"] else ""
            probs += ["%s: %s%s" % (e["rel"], p, ref) for p in found]
            artifacts.append(e["rel"])
    if probs:
        ctx.add("X007", oc.id, "a GRADE számai eltérnek a(z) %s elsődleges commit-futástól: %s" % (
            prun["run_id"], "; ".join(probs[:12]) + (" … (+%d)" % (len(probs) - 12) if len(probs) > 12 else "")),
            artifacts + [prun["rel"]], None, run_id=prun["run_id"])


def _absolute(measure, rel, acr):
    """Abszolút hatás /1000 (GRADE-10a; D-S13-012): a grade_help.absolute_effect, ennek hiányában ugyanaz a képlet
    (RR: ACR·RR; OR: ACR·OR / (1 − ACR + ACR·OR); RD: ACR + RD; a [0; 1]-en kívüli kockázat None)."""
    GH = _module("grade_help")
    if GH is not None and callable(getattr(GH, "absolute_effect", None)):
        return GH.absolute_effect(measure, rel, acr)
    if measure not in ("RR", "OR", "RD") or not _isnum(acr) or not 0 < acr < 1:
        raise ValueError("nem számolható")
    risks, diffs = [], []
    for x in rel:
        if not _isnum(x) or (measure in ("RR", "OR") and x < 0):
            risks.append(None)
            diffs.append(None)
            continue
        r = acr * x if measure == "RR" else (acr * x / (1 - acr + acr * x) if measure == "OR" else acr + x)
        if not 0.0 <= r <= 1.0:
            risks.append(None)
            diffs.append(1000.0 * x if measure == "RD" else None)
            continue
        risks.append(1000.0 * r)
        diffs.append(1000.0 * (x if measure == "RD" else r - acr))
    return {"risk_per_1000": risks, "difference_per_1000": diffs}


def _lists_close(a, b):
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
        return False
    return all(_close(x, y, 1e-6) for x, y in zip(a, b))


def _x008(ctx, oc, prun, runs_by_id):
    rel = "%s/%s.sof.json" % (SOF_DIR, oc.id)
    if not ctx.isfile(rel):
        ctx.skip("X008", oc.id, "nincs SoF-táblázat ehhez a kimenethez (%s)" % rel)
        return
    doc = ctx.load_json(rel, "X008", oc.id, "SoF")
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        if doc is not None:
            ctx.skip("X008", oc.id, "%s nem szk.ma.sof/v1 (hiányzó rows lista)" % rel)
        return
    target = prun or runs_by_id.get(doc.get("run_id"))
    if target is None:
        ctx.skip("X008", oc.id, "%s: nincs elsődleges commit-futás, és a hivatkozott futás (%s) nem található" % (
            rel, doc.get("run_id") or "–"))
        return
    nums = _run_numbers(ctx, target)
    bt = nums["estimate_ci"]
    probs = []
    for i, row in enumerate(doc["rows"]):
        if not isinstance(row, dict) or row.get("outcome_id") not in (None, oc.id):
            continue
        where = "%d. sor" % (i + 1)
        for p in _number_mismatches(row.get("k"), row.get("participants"), None, nums):
            probs.append("%s: %s" % (where, p))
        eff = row.get("relative") if isinstance(row.get("relative"), dict) else (
            row.get("effect") if isinstance(row.get("effect"), dict) else None)
        if eff is not None:
            dt, want = eff.get("display_text"), nums["display_text"]
            if isinstance(dt, dict) and isinstance(want, dict):
                for lang in ("hu", "en"):
                    if dt.get(lang) is not None and want.get(lang) is not None and dt[lang] != want[lang]:
                        probs.append("%s: hatás (%s) %r ≠ a motoré %r" % (where, lang, dt[lang], want[lang]))
            if bt is not None and not _lists_close([eff.get("estimate"), eff.get("ci_lower"), eff.get("ci_upper")],
                                                   bt):
                probs.append("%s: a hatás számai (becslés, CI) ≠ a futás back_transformed.estimate_ci-je" % where)
        for a in row.get("absolute") or []:
            if not isinstance(a, dict) or not _isnum(a.get("assumed_risk_per_1000")) or bt is None:
                continue
            label = a.get("label")
            label = (label.get("hu") or label.get("en")) if isinstance(label, dict) else (label or "?")
            try:
                again = _absolute(nums["measure"], bt, a["assumed_risk_per_1000"] / 1000.0)
            except (ValueError, TypeError, KeyError):
                continue            # nem bináris mérték vagy érvénytelen alapkockázat: a sof() sem számol ilyet
            if not (_lists_close(a.get("difference_per_1000"), again.get("difference_per_1000")) and
                    _lists_close(a.get("risk_per_1000"), again.get("risk_per_1000"))):
                probs.append("%s: abszolút hatás (%s) ≠ az újraszámolt" % (where, label))
    if probs:
        ref = (" (a SoF a(z) %s futásra hivatkozik)" % doc["run_id"]) if doc.get("run_id") and doc.get(
            "run_id") != target["run_id"] else ""
        ctx.add("X008", oc.id, "a SoF %d cellája eltér a(z) %s commit-futás motor-számaitól%s: %s" % (
            len(probs), target["run_id"], ref, "; ".join(probs[:12]) + (
                " … (+%d)" % (len(probs) - 12) if len(probs) > 12 else "")),
            [rel, target["rel"]], None, run_id=target["run_id"])


# ------------------------------------------------------------------ X009
_COMPARE_NAMES = ("compare", "kettos_compare", "compare_tables", "dual_compare")


def _compare_fn():
    """A kettős kinyerés összevetése: metaelemzes.kettos.compare, ennek hiányában az api homlokzat (a munkapad
    ugyanezeket a neveket keresi; E6)."""
    for modname in ("kettos", "api"):
        mod = _module(modname)
        for n in _COMPARE_NAMES:
            fn = getattr(mod, n, None) if mod is not None else None
            if callable(fn) and not isinstance(fn, type):
                return fn
    return None


def _raw_table(ctx, rel):
    raw = ctx.read_bytes(rel)
    header, rows_text, fmt = tableio.read_raw(raw=raw)
    uids = None
    try:
        rows, meta = tableio.read_table_bytes(raw, rel)
        u = tableio.row_uids(rows, meta)
        uids = list(u) if len(u) == len(rows_text) else None
    except Exception:       # noqa: BLE001 — az összevetés a nyers cellákkal is megy
        uids = None
    return {"header": list(header), "rows": [list(r) for r in rows_text], "row_uids": uids, "dataset": rel,
            "decimal_mark": fmt.get("decimal_mark")}


def _call_compare(fn, ctx, rel_a, rel_b, key, measure):
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    pos = [p for p in params.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if pos and "path" in pos[0].name:
        a, b = ctx.abs(rel_a), ctx.abs(rel_b)
        ctx.sha(rel_a)
        ctx.sha(rel_b)
    else:
        a, b = _raw_table(ctx, rel_a), _raw_table(ctx, rel_b)
    var_kw = any(p.kind == p.VAR_KEYWORD for p in params.values())
    kw = {k: v for k, v in (("key", key), ("measure", measure)) if v is not None and (k in params or var_kw)}
    return fn(a, b, **kw)


def _x009(ctx, oc, kettos_dirs):
    base = posixpath.dirname(oc.data) if oc.data else DATA_DIR
    kdir = (base + "/" if base else "") + KETTOS
    if not os.path.isdir(ctx.abs(kdir)):
        kettos_dirs.append(kdir)
        return
    rel_a, rel_b = "%s/%s.A.csv" % (kdir, oc.id), "%s/%s.B.csv" % (kdir, oc.id)
    have_a, have_b = ctx.isfile(rel_a), ctx.isfile(rel_b)
    if not have_a and not have_b:
        ctx.skip("X009", oc.id, "ehhez a kimenethez nincs kettős kinyerés (%s, %s)" % (rel_a, rel_b))
        return
    if not (have_a and have_b):
        ctx.skip("X009", oc.id, "csak az egyik kinyerő táblája van meg (%s); az összevetéshez mindkettő kell" % (
            rel_a if have_a else rel_b))
        return
    cons_rel = "%s/%s.consensus.json" % (kdir, oc.id)
    command = ["ma.py", "kettos", "compare", "--project", ".", "--outcome", oc.id]
    KT = _module("kettos")
    status_fn = getattr(KT, "outcome_status", None) if KT is not None else None
    if callable(status_fn):
        # az E6 saját állapota (összevetés + döntések + elavulás): ugyanaz, amit a Kettős kinyerés lap mutat
        for rel in (rel_a, rel_b, cons_rel):
            if ctx.isfile(rel):
                ctx.sha(rel)
        try:
            st = status_fn(ctx.root, oc.id, oc.data)
        except Exception as exc:    # noqa: BLE001 — a motor hibája nem állíthatja le az auditot
            ctx.skip("X009", oc.id, "a két tábla nem vethető össze: %s" % _exc_text(exc))
            return
        if not isinstance(st, dict) or st.get("error") or st.get("unresolved") is None:
            ctx.skip("X009", oc.id, "a két tábla nem vethető össze: %s" % (
                (st or {}).get("error") if isinstance(st, dict) else "ismeretlen állapot"))
            return
        if st["unresolved"]:
            detail = "%d feloldatlan eltérés a két kinyerő táblája között (%s döntést igénylő tételből)" % (
                st["unresolved"], st.get("total") if st.get("total") is not None else "?")
            if st.get("stale"):
                detail += "; ebből %d döntés elavult, mert a tábla a döntés óta megváltozott" % st["stale"]
            ctx.add("X009", oc.id, detail, [rel_a, rel_b] + ([cons_rel] if ctx.isfile(cons_rel) else []), command)
        return
    fn = _compare_fn()
    if fn is None:
        ctx.skip("X009", oc.id, "a kettős kinyerés összevetése (metaelemzes.kettos.compare / api.compare, E6) ebben "
                                "a motorváltozatban nem érhető el")
        return
    cons = ctx.load_json(cons_rel, "X009", oc.id, "konszenzus") if ctx.isfile(cons_rel) else None
    cons = cons if isinstance(cons, dict) else {}
    key = cons.get("key") if isinstance(cons.get("key"), list) and cons["key"] else None
    try:
        res = _call_compare(fn, ctx, rel_a, rel_b, key, oc.measure)
    except Exception as exc:        # noqa: BLE001 — a motor hibája nem állíthatja le az auditot
        ctx.skip("X009", oc.id, "a két tábla nem vethető össze: %s" % _exc_text(exc))
        return
    if not isinstance(res, dict) or not isinstance(res.get("disagreements"), list):
        ctx.skip("X009", oc.id, "az összevetés eredménye nem szk.ma.compare-result/v1")
        return
    decisions = {}
    for d in cons.get("decisions") or []:
        if isinstance(d, dict) and isinstance(d.get("key"), str) and isinstance(d.get("field"), str):
            decisions[(d["key"], d["field"])] = d
    unresolved, stale, cells, fields = 0, 0, [], collections.Counter()
    for d in res["disagreements"]:
        if not isinstance(d, dict) or d.get("kind") == "format_only":
            continue                    # csak írásmódban tér el: a konszenzus az A szövegét veszi át
        dec = decisions.get((d.get("key"), d.get("field")))
        is_stale = dec is not None and (("a" in dec and dec.get("a") != d.get("a")) or
                                        ("b" in dec and dec.get("b") != d.get("b")))
        if dec is not None and not is_stale:
            continue
        unresolved += 1
        stale += 1 if is_stale else 0
        fields[str(d.get("field"))] += 1
        uid = d.get("row_uid_a") or d.get("row_uid_b")
        if isinstance(uid, str) and isinstance(d.get("field"), str):
            cells.append({"row_uid": uid, "field": d["field"]})
    rows_only = 0
    for side in ("only_a", "only_b"):
        for k in res.get(side) or []:
            if (k, "*") not in decisions:
                unresolved += 1
                rows_only += 1
    if not unresolved:
        return
    parts = ["%d feloldatlan eltérés" % unresolved]
    if fields:
        parts.append("mezőnként: %s" % ", ".join("%s %d" % kv for kv in sorted(fields.items())))
    if rows_only:
        parts.append("%d sor csak az egyik táblában van, döntés nélkül" % rows_only)
    if stale:
        parts.append("ebből %d döntés elavult (a tábla a döntés óta megváltozott)" % stale)
    ctx.add("X009", oc.id, "; ".join(parts), [rel_a, rel_b] + ([cons_rel] if ctx.isfile(cons_rel) else []), command,
            cells=cells or None)


# ------------------------------------------------------------------ X011
def _x011(ctx, meta, entries, smap, study_ids_from_tables):
    if not isinstance(meta, dict) or meta.get("review_type") != "prediction_model":
        return
    if smap is not None:
        ids = list(smap["study_ids"])
        src = STUDIES_FILE
    else:
        ids, src = list(study_ids_from_tables), "az adattáblák study_id-i"
    if not ids:
        ctx.skip("X011", None, "a bevont vizsgálatok listája ismeretlen (%s, illetve study_id oszlop)" % STUDIES_FILE)
        return
    have = {_fold(e["unit"]) for e in entries or () if e["tool"] == "probast-ai" and e["final"]}
    drafts = {_fold(e["unit"]) for e in entries or () if e["tool"] == "probast-ai" and not e["final"]}
    missing = [s for s in ids if _fold(s) not in have]
    if not missing:
        return
    detail = "%d/%d bevont vizsgálatnak nincs lezárt PROBAST+AI-értékelése (a vizsgálatok forrása: %s): %s" % (
        len(missing), len(ids), src, _plural_list(missing))
    only_draft = [s for s in missing if _fold(s) in drafts]
    if only_draft:
        detail += "; csak vázlat (vagy jóvá nem hagyott AI-vázlat) van: %s" % _plural_list(only_draft)
    ctx.add("X011", None, detail, [APPRAISAL_DIR] + ([STUDIES_FILE] if smap is not None else []), None,
            studies=missing)


# ------------------------------------------------------------------ X012
_AMSTAR_RATINGS = {"high": "high", "magas": "high", "moderate": "moderate", "mersekelt": "moderate", "low": "low",
                   "alacsony": "low", "critically low": "critically_low", "critically_low": "critically_low",
                   "kritikusan alacsony": "critically_low"}


def _rating_key(v):
    return _AMSTAR_RATINGS.get(re.sub(r"[_\-]+", " ", _fold(v)).strip()) if _nonempty(v) else None


def _amstar2_ratings(answers, convention):
    """(a projekt konvenciója szerinti, a másik konvenció szerinti) besorolás a motor algoritmusával
    (appraisal.amstar2_rating, ennek hiányában grade_help.amstar2_consistency); egyik sem érhető el → None."""
    AP = _module("appraisal")
    if AP is not None and callable(getattr(AP, "amstar2_rating", None)):
        try:
            blk = AP.amstar2_rating(answers, convention)
            alt = blk.get("alternative") if isinstance(blk.get("alternative"), dict) else {}
            return blk.get("rating"), alt.get("rating", blk.get("rating"))
        except Exception:       # noqa: BLE001
            pass
    GH = _module("grade_help")
    if GH is not None and callable(getattr(GH, "amstar2_consistency", None)):
        try:
            out = GH.amstar2_consistency(answers, convention=convention)
            other = "weakness" if convention == "meets" else "meets"
            alt = (out.get("by_convention") or {}).get(other)
            alt = alt.get("rating") if isinstance(alt, dict) else alt
            return _rating_key(out.get("rating")), _rating_key(alt) or _rating_key(out.get("rating"))
        except Exception:       # noqa: BLE001
            pass
    return None


def _x012(ctx, meta, entries, stage):
    meta = meta if isinstance(meta, dict) else {}
    docs = [e for e in entries or () if e["tool"] == "amstar2"]
    declared = "amstar2" in [str(t).lower() for t in meta.get("appraisal_tools") or [] if isinstance(t, str)]
    if not docs and not declared:
        ctx.skip("X012", None, "nincs AMSTAR 2 önellenőrzés (%s/review.amstar2.*.json), és a ma-projekt.json "
                               "appraisal_tools sem kéri" % APPRAISAL_DIR)
        return
    late = _late(stage)
    if not docs:
        if late:
            ctx.add("X012", None, "a ma-projekt.json az AMSTAR 2-t kéri (appraisal_tools), de nincs önellenőrzés "
                                  "(%s/review.amstar2.<monogram>.json)" % APPRAISAL_DIR, [META_FILE], None)
        else:
            ctx.skip("X012", None, "még nincs AMSTAR 2 önellenőrzés; az S14-től és a FINAL kéréskor számít")
        return
    rank = {"consensus": 2}
    pick = sorted(docs, key=lambda e: (rank.get(e["status"], 1 if e["final"] else 0), e["updated"], e["rel"]))[-1]
    doc = pick["doc"]
    answers = doc.get("answers") if isinstance(doc.get("answers"), dict) else {}
    vals = {}
    for i in range(1, 17):
        a = answers.get(str(i))
        v = a.get("value") if isinstance(a, dict) else a
        if _nonempty(v):
            vals[str(i)] = v
    missing = [str(i) for i in range(1, 17) if str(i) not in vals]
    problems = []
    if missing:
        if late:
            problems.append("hiányos: %d/16 tétel megválaszolva (hiányzik: %s)" % (16 - len(missing),
                                                                                   ", ".join(missing)))
        else:
            ctx.skip("X012", None, "az AMSTAR 2 még hiányos (%d/16); a hiányosság az S14-től és a FINAL kéréskor "
                                   "számít" % (16 - len(missing)))
    ov = doc.get("overall") if isinstance(doc.get("overall"), dict) else {}
    claimed = _rating_key(ov.get("judgement"))
    if claimed and not missing:
        conv = (meta.get("conventions") or {}).get("amstar2_partial_yes_critical") if isinstance(
            meta.get("conventions"), dict) else None
        conv = conv if conv in ("meets", "weakness") else "meets"
        got = _amstar2_ratings(vals, conv)
        if got is None:
            ctx.skip("X012", None, "a besorolás nem számolható újra: az AMSTAR 2-algoritmus (metaelemzes.appraisal / "
                                   "grade_help) nem érhető el")
        elif claimed not in got:
            problems.append("a rögzített besorolás (%s) nem egyezik a válaszokból adódóval: %s (konvenció: %s)%s" % (
                ov.get("judgement"), got[0], conv,
                ("; a másik konvencióval: %s" % got[1]) if got[1] != got[0] else ""))
    if problems:
        ctx.add("X012", None, "%s: %s" % (pick["rel"], "; ".join(problems)), [pick["rel"]], None)


# ------------------------------------------------------------------ X015
def _run_study_count(ctx, run, oc, tab, specs_by_path, label_to_id):
    """A futásban ténylegesen szereplő vizsgálatok száma: a plot_data.json vizsgálatai (egyedi study_id / címke), ennek
    hiányában a tábla futás-szűrt sorainak egyedi study_id-i, végül a futás k-ja. → (n, forrás) vagy (None, None)."""
    plot = ctx.load_json(run["dir"] + "/plot_data.json", what="ábra-adat")
    if isinstance(plot, dict) and isinstance(plot.get("studies"), list) and plot["studies"]:
        keys = set()
        for st in plot["studies"]:
            if isinstance(st, dict):
                k = st.get("study_id") if _present(st.get("study_id")) else st.get("label")
                if _present(k):
                    keys.add(label_to_id.get(_fold(k), _fold(k)))
        if keys:
            return len(keys), "plot_data.json"
    if tab is not None and "study_id" in set(_columns(tab[1])) and (
            not run["data_path"] or _same_path(ctx, run["data_path"], oc.data)):
        rows = tab[0]
        keys = {_fold(rows[i]["study_id"]) for i in _analysed_rows(ctx, tab, run, specs_by_path, oc.data)
                if _present(rows[i].get("study_id"))}
        if keys:
            return len(keys), "az adattábla study_id-i"
    if run["k"] is not None:
        return run["k"], "k"
    return None, None


def _x015(ctx, oc, prun, tab, specs_by_path, label_to_id, smap, flow_meta, n_outcomes):
    expected = []
    if smap is not None and smap.get("per_outcome") is not None:
        expected.append((STUDIES_FILE, smap["per_outcome"].get(oc.id, 0)))
    if flow_meta is not None:
        rel, im = flow_meta
        if isinstance(im, dict):
            v = _count(im.get(oc.id))
            if v is not None:
                expected.append((rel, v))
        elif _count(im) is not None:
            if n_outcomes == 1:
                expected.append((rel, _count(im)))
            else:
                ctx.skip("X015", oc.id, "a %s included_meta-ja egyetlen szám, de %d kimenet van — kimenetenként a "
                                        "studies.json outcomes listája adja" % (rel, n_outcomes))
    if not expected:
        return False
    if prun is None:
        ctx.skip("X015", oc.id, "nincs (egyértelmű) elsődleges commit-futás")
        return True
    n_run, how = _run_study_count(ctx, prun, oc, tab, specs_by_path, label_to_id)
    if n_run is None:
        ctx.skip("X015", oc.id, "a(z) %s futás vizsgálatszáma ismeretlen" % prun["run_id"])
        return True
    bad = [(rel, n) for rel, n in expected if n != n_run]
    if bad:
        ctx.add("X015", oc.id, "a(z) %s elsődleges commit-futásban %d vizsgálat szerepel (%s), de %s" % (
            prun["run_id"], n_run, how, "; ".join("a %s szerint %d" % (rel, n) for rel, n in bad)),
            [rel for rel, _ in bad] + [prun["rel"]], None, run_id=prun["run_id"])
    return True


# ------------------------------------------------------------------ X017
def _same_judgement(a, b):
    ca, cb = tableio.rob_category(a), tableio.rob_category(b)
    if ca and cb:
        return ca == cb
    return re.sub(r"[_\-\s]+", " ", _fold(a)) == re.sub(r"[_\-\s]+", " ", _fold(b))


def _stored_overrides(doc):
    """A dokumentumba mentett implikált ítéletből (domain_judgements[].implied, overall.implied) — ha a motor
    implikált-ítélet számítása (appraisal.check) nem érhető el."""
    out = []
    items = [(dj, str(dj.get("domain")), dj.get("pass")) for dj in doc.get("domain_judgements") or ()
             if isinstance(dj, dict)]
    if isinstance(doc.get("overall"), dict):
        items.append((doc["overall"], "overall", None))
    for d, dom, ps in items:
        j, imp = d.get("judgement"), d.get("implied")
        if _nonempty(j) and _nonempty(imp) and not _same_judgement(j, imp):
            out.append({"domain": dom, "pass": ps, "judgement": j, "implied": imp,
                        "reason_missing": not _nonempty(d.get("override_reason"))})
    return out


def _x017(ctx, entries, conventions):
    finals = [e for e in entries or () if e["final"]]
    if not finals:
        ctx.skip("X017", None, "nincs lezárt értékelés (%s)" % APPRAISAL_DIR)
        return
    AP = _module("appraisal")
    check = getattr(AP, "check", None) if AP is not None else None
    for e in finals:
        overrides = None
        if callable(check):
            try:
                res = check(e["doc"], conventions=conventions or None)
            except Exception:   # noqa: BLE001 — ismeretlen eszköz vagy hibás dokumentum: a tárolt implikált ítélet
                res = None
            if isinstance(res, dict) and isinstance(res.get("overrides"), list):
                overrides = res["overrides"]
        if overrides is None:
            overrides = _stored_overrides(e["doc"])
        bad = [o for o in overrides if isinstance(o, dict) and o.get("reason_missing")]
        if not bad:
            continue
        what = ["%s: %s ≠ implikált %s" % ("összítélet" if o.get("domain") == "overall" else "D%s%s" % (
            o.get("domain"), ("/" + o["pass"]) if o.get("pass") else ""), o.get("judgement"), o.get("implied"))
                for o in bad]
        ctx.add("X017", e["outcome"], "%s (%s, %s): %s — felülbírálási indoklás (override_reason) nélkül" % (
            e["rel"], e["tool"], e["unit"], "; ".join(what)), [e["rel"]], None, studies=[str(e["unit"])])


# ------------------------------------------------------------------ X019
_PB_SIGNED = re.compile(r"^\s*(?:[+\-−–]\s*[0-3]|0)(?![0-9.,])")
_PB_SUSPECTED = re.compile(r"^\s*(?:suspected|gyan[ií]tott|gyan[ií]that[óo])", re.I)


def _pb_unresolved_doc(doc):
    pb = (doc.get("domains") or {}).get("publication_bias") if isinstance(doc.get("domains"), dict) else None
    if not isinstance(pb, dict):
        return False
    return pb.get("status") == "unresolved" or (pb.get("rating") == "suspected" and pb.get("step") is None)


def _x019(ctx, grade_docs, grade_rows):
    if not grade_docs and not grade_rows:
        ctx.skip("X019", None, "nincs GRADE-ítélet (%s/*.grade.json vagy projektnapló grade-sor)" % GRADE_DIR)
        return
    seen = set()
    for e in grade_docs or ():
        seen.add(_fold(e["outcome"]))
        if _pb_unresolved_doc(e["doc"]):
            ctx.add("X019", e["outcome"], "%s: a publikációs torzítás „gyanított” (suspected), de nincs 0 / −1 döntés "
                                          "indoklással (futás: %s)" % (e["rel"], e["doc"].get("run_id") or "–"),
                    [e["rel"]], None)
    latest = collections.OrderedDict()
    for g in grade_rows or ():
        latest[_fold(g.get("outcome"))] = g
    for key, g in latest.items():
        if key in seen:
            continue
        text = g.get("publication_bias")
        if _nonempty(text) and not _PB_SIGNED.match(text) and _PB_SUSPECTED.match(text):
            ctx.add("X019", g.get("outcome"), "projektnapló grade #%s: a publikációs torzítás („%s”) előjeles lépés "
                                              "nélkül feloldatlan" % (g.get("id"), text.strip()[:80]),
                    [JOURNAL_FILE], None)


# ------------------------------------------------------------------ X020 / X021 (PRISMA)
def _flow_doc(ctx):
    """(út, flow-szótár) — a composer-export (prisma_flow.json), ennek hiányában a prisma_folyamat.md; nincs: None."""
    from . import prisma
    flow = ctx.load_json(PRISMA_JSON, None, None, "PRISMA-folyamat")
    if isinstance(flow, dict):
        return PRISMA_JSON, flow
    if ctx.isfile(PRISMA_MD):
        try:
            return PRISMA_MD, prisma.parse_markdown_table(ctx.read_bytes(PRISMA_MD).decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, ValueError):
            return None
    return None


def _x020(ctx, stage):
    from . import prisma
    flow = ctx.load_json(PRISMA_JSON, "X020", None, "PRISMA-folyamat")
    if not isinstance(flow, dict):
        ctx.skip("X020", None, "nincs composer PRISMA-export (%s)" % PRISMA_JSON)
        return
    n = prisma.undecided_count(flow)
    if n is None:
        ctx.skip("X020", None, "a %s nem közli az elbírálásra váró rekordok számát (undecided)" % PRISMA_JSON)
    elif n > 0:
        if _late(stage):
            ctx.add("X020", None, "a composer-export szerint %d rekord még elbírálásra vár (undecided)" % n,
                    [PRISMA_JSON], None)
        else:
            ctx.skip("X020", None, "%d rekord még elbírálásra vár; az S14-től és a FINAL kéréskor számít" % n)


def _decision_logs(ctx):
    """A 02_szures mappa döntési naplói (CSV/TSV, a composer döntés-CSV-jének oszlopaival) → ([utak], összesítés) —
    a fájlok név szerinti sorrendben, rekordazonosítónként a későbbi döntés érvényes."""
    from . import prisma
    d = ctx.abs(SCREENING_DIR)
    if not os.path.isdir(d):
        return [], None
    rels, rows = [], []
    for name in sorted(os.listdir(d)):
        if name.startswith(".") or not name.lower().endswith((".csv", ".tsv")):
            continue
        rel = SCREENING_DIR + "/" + name
        try:
            header, rows_text, _fmt = tableio.read_raw(raw=ctx.read_bytes(rel))
        except Exception:       # noqa: BLE001 — nem döntési napló (pl. bináris vagy hibás CSV): kimarad
            continue
        canon = prisma.decision_log_rows(header, rows_text)
        if canon is None:
            continue
        rels.append(rel)
        rows.extend(canon)
    if not rels:
        return [], None
    return rels, prisma.decision_log_reasons(prisma.DECISION_LOG_HEADER, rows)


def _x021(ctx, flow_src):
    from . import prisma
    if flow_src is None:
        ctx.skip("X021", None, "nincs PRISMA-folyamat (%s vagy %s)" % (PRISMA_JSON, PRISMA_MD))
        return
    rel, flow = flow_src
    logs, agg = _decision_logs(ctx)
    if agg is None:
        ctx.skip("X021", None, "nincs szűrési döntési napló a %s mappában (CSV: rec_id | pmid, decision, reason, "
                               "phase — a szűrőeszköz vagy a composer exportja)" % SCREENING_DIR)
        return
    vals, reasons, _seen = prisma.normalize(flow)
    flow_r = prisma.reason_counts(reasons.get("excluded_eligibility_reasons")) if reasons.get(
        "excluded_eligibility_reasons") else None
    log_r = agg["reasons"]
    problems = []
    if flow_r:
        for k in list(flow_r) + [k for k in log_r if k not in flow_r]:
            fn = flow_r.get(k, [None, 0])[1]
            ln = log_r.get(k, [None, 0])[1]
            if fn != ln:
                label = (flow_r.get(k) or log_r.get(k))[0]
                problems.append("„%s”: folyamat %d, napló %d" % (label, fn, ln))
    else:
        try:
            h = prisma._as_count(vals.get("excluded_eligibility"))
        except ValueError:
            h = None
        if h is None:
            ctx.skip("X021", None, "a %s nem közli a teljes szöveg szintű kizárásokat (H) okonként sem" % rel)
            return
        if h != agg["rows"]:
            problems.append("H = %d, a naplóban %d teljes szöveg szintű kizárás" % (h, agg["rows"]))
    if problems:
        ctx.add("X021", None, "a %s kizárási okai eltérnek a döntési naplótól (%s): %s" % (
            rel, ", ".join(logs), "; ".join(problems[:15]) + (" … (+%d)" % (len(problems) - 15) if len(problems) > 15
                                                               else "")), [rel] + logs, None)


# ------------------------------------------------------------------ X002 / X018 (ábrák)
def _figures(ctx):
    """(06_kezirat/abrak/*.result.json → [{rel, stem, doc}], .result.json nélküli ábra-tövek) vagy None."""
    d = ctx.abs(FIGURES_DIR)
    if not os.path.isdir(d):
        return None
    names = [n for n in sorted(os.listdir(d)) if not n.startswith(".")]
    results = [n for n in names if n.endswith(".result.json")]
    stems = {n.split(".")[0] for n in results}
    orphans = sorted({n.split(".")[0] for n in names if n.lower().endswith(_FIG_FORMATS)} - stems)
    out = []
    for n in results:
        rel = FIGURES_DIR + "/" + n
        doc = ctx.load_json(rel, None, None, "ábra-eredmény")
        if isinstance(doc, dict):
            out.append({"rel": rel, "stem": n[:-len(".result.json")], "doc": doc})
    return out, orphans


def _plot_sha(ctx, run):
    rel = run["dir"] + "/plot_data.json"
    if ctx.isfile(rel):
        return ctx.sha(rel)
    files = run["doc"].get("files") if isinstance(run["doc"].get("files"), dict) else {}
    plot = files.get("plot") if isinstance(files.get("plot"), dict) else {}
    sha = plot.get("sha256")
    return sha.lower() if isinstance(sha, str) else None


def _x002(ctx, figs, runs):
    by_id = {r["run_id"]: r for r in runs}
    last = {}
    for r in runs:
        last[_run_group(ctx, r)] = r
    for f in figs:
        src = f["doc"].get("source") if isinstance(f["doc"].get("source"), dict) else {}
        psha = src.get("plot_sha256").lower() if _nonempty(src.get("plot_sha256")) else None
        rid = src.get("run_id") if _nonempty(src.get("run_id")) else None
        if psha is None:
            if f["doc"].get("kind") not in _NON_PLOT_KINDS:
                ctx.skip("X002", None, "%s: nincs source.plot_sha256" % f["rel"])
            continue
        if not runs:
            ctx.skip("X002", None, "%s: nincs commit-futás, amellyel összevethető" % f["rel"])
            continue
        run = by_id.get(rid) if rid else None
        if run is None:
            run = next((r for r in reversed(runs) if _plot_sha(ctx, r) == psha), None)
        if run is None:
            ctx.add("X002", None, "a(z) %s ábra plot_sha256-ja (%s) egyik commit-futás plot_data.json-jához sem "
                                  "tartozik%s" % (f["stem"], _short(psha), (" (a hivatkozott %s futás nem található)"
                                                                            % rid) if rid else ""),
                    [f["rel"]], None)
            continue
        latest = last.get(_run_group(ctx, run), run)
        cur = _plot_sha(ctx, latest)
        if cur is None or cur == psha:
            if cur is None:
                ctx.skip("X002", run["outcome"], "%s: a(z) %s futásnak nincs plot_data.json-ja" % (
                    f["rel"], latest["run_id"]))
            continue
        if latest is run:
            detail = "a(z) %s ábra a(z) %s futás plot_data.json-jából készült (%s), de az azóta megváltozott (%s)" % (
                f["stem"], run["run_id"], _short(psha), _short(cur))
        else:
            detail = "a(z) %s ábra a(z) %s futásból készült; ugyanarra az elemzésre azóta újabb commit-futás van " \
                     "(%s)" % (f["stem"], run["run_id"], latest["run_id"])
        ctx.add("X002", run["outcome"], detail, [f["rel"], latest["dir"] + "/plot_data.json"], None,
                run_id=latest["run_id"])


def _x018(ctx, figs, runs):
    by_id = {r["run_id"]: r for r in runs}
    for f in figs:
        doc = f["doc"]
        probs = []
        if doc.get("clean") is False:
            probs.append("a QC nem tiszta")
        rv = doc.get("residual_violations")
        if isinstance(rv, list) and rv:
            probs.append("%d maradék címke-ütközés" % len(rv))
        gl = doc.get("glyphs") if isinstance(doc.get("glyphs"), dict) else {}
        if gl.get("ok") is False:
            miss = gl.get("missing") if isinstance(gl.get("missing"), list) else []
            probs.append("hiányzó karakter a betűkészletben%s" % ((": " + " ".join(str(x) for x in miss[:10]))
                                                                 if miss else ""))
        nb = doc.get("numbers") if isinstance(doc.get("numbers"), dict) else {}
        mism = nb.get("mismatches") if isinstance(nb.get("mismatches"), list) else []
        if nb.get("ok") is False or mism:
            probs.append("számhűség: %d eltérés%s" % (len(mism), (" (%s ellenőrzött szám)" % nb["checked"])
                                                      if _int(nb.get("checked")) is not None else ""))
        for k in ("server_check", "recheck"):
            sc = doc.get(k) if isinstance(doc.get(k), dict) else {}
            if sc.get("ok") is False:
                probs.append("a szerver újraellenőrzése eltérést talált")
        if not probs:
            continue
        src = doc.get("source") if isinstance(doc.get("source"), dict) else {}
        run = by_id.get(src.get("run_id")) if src.get("run_id") else None
        ctx.add("X018", run["outcome"] if run else None, "a(z) %s ábra (%s): %s" % (
            f["stem"], doc.get("kind") or "?", "; ".join(probs)), [f["rel"]], None)


# ------------------------------------------------------------------ AMSTAR 2-javaslatok (4.15 amstar2_hints)
def _amstar2_hints(ctx, flow_src, coverage, grade_docs, grade_rows, outcomes):
    """Javaslatok az AMSTAR 2 önellenőrzéshez a projekt fájljaiból (csak tájékoztató; az ítélet emberi): 4 (átfogó
    keresés), 7 (kizárt vizsgálatok okokkal), 9 (RoB megfelelő eszközzel), 13 (RoB az értelmezésben), 15 (publikációs
    torzítás vizsgálata)."""
    from . import prisma
    hints = collections.OrderedDict()
    if flow_src is not None:
        rel, flow = flow_src
        vals, reasons, _ = prisma.normalize(flow)
        try:
            a1, a2, h = (prisma._as_count(vals.get(k)) for k in ("identified_databases", "identified_registers",
                                                                 "excluded_eligibility"))
        except ValueError:
            a1 = a2 = h = None
        dbs = [x for x in flow.get("databases") or [] if isinstance(x, dict)] if isinstance(
            flow.get("databases"), list) else []
        if a1:
            ev = "%s: adatbázisokból %d rekord (A1)%s, regiszterekből %s (A2)" % (
                rel, a1, (", %d adatbázis" % len(dbs)) if dbs else "", a2 if a2 is not None else "?")
            hints["4"] = {"suggested": "no" if len(dbs) == 1 else "partial_yes", "evidence": [ev]}
        rs = reasons.get("excluded_eligibility_reasons")
        if h:
            rc = prisma.reason_counts(rs) if rs else {}
            no_reason = rc.get(prisma.NO_REASON, [None, 0])[1] if rc else 0
            if rc and sum(v[1] for v in rc.values()) == h and not no_reason:
                hints["7"] = {"suggested": "partial_yes", "evidence": [
                    "%s: H = %d, %d okkal (a kizárt vizsgálatok listája a kiegészítő anyagban adja az „igen”-t)" % (
                        rel, h, len(rc))]}
            else:
                hints["7"] = {"suggested": "no", "evidence": ["%s: H = %d, okonkénti bontás nélkül vagy ok nélküli "
                                                              "kizárással (P008)" % (rel, h)]}
    if coverage and all(tot for _c, tot, _t in coverage.values()):
        if all(c == tot for c, tot, _t in coverage.values()):
            hints["9"] = {"suggested": "yes", "evidence": ["értékelések: %s" % "; ".join(
                "%s %d/%d (%s)" % (oid, c, tot, ", ".join(t)) for oid, (c, tot, t) in coverage.items())]}
        elif all(c == 0 for c, _tot, _t in coverage.values()):
            hints["9"] = {"suggested": "no", "evidence": ["egyik elemzett vizsgálatnak sincs lezárt értékelése"]}
    if outcomes:
        decided_rob, decided_pb = [], []
        for oc in outcomes:
            gd = next((e["doc"] for e in reversed(grade_docs or []) if e["outcome"] == oc.id), None)
            rows = _grade_rows_for(grade_rows, oc)
            doms = gd.get("domains") if isinstance(gd, dict) and isinstance(gd.get("domains"), dict) else {}
            rob = (doms.get("risk_of_bias") or {}).get("rating") if isinstance(doms.get("risk_of_bias"), dict) else None
            if rob or (rows and _nonempty(rows[-1].get("risk_of_bias"))):
                decided_rob.append(oc.id)
            if (isinstance(gd, dict) and not _pb_unresolved_doc(gd) and isinstance(doms.get("publication_bias"), dict)
                    and doms["publication_bias"].get("step") is not None) or (
                        rows and _nonempty(rows[-1].get("publication_bias"))
                        and not _PB_SUSPECTED.match(rows[-1]["publication_bias"])):
                decided_pb.append(oc.id)
        ids = [oc.id for oc in outcomes]
        if decided_rob == ids:
            hints["13"] = {"suggested": "yes", "evidence": ["GRADE torzítási kockázat doménje kitöltve: %s" %
                                                            ", ".join(ids)]}
        if decided_pb == ids:
            hints["15"] = {"suggested": "yes", "evidence": ["GRADE publikációs torzítás eldöntve: %s" %
                                                            ", ".join(ids)]}
    return hints


# ------------------------------------------------------------------ fő belépési pont
def project_audit(project_dir, stage=None, now=None):
    """A projektmappa X-szabályai → szk.ma.project-audit/v1 szótár.

    stage: a szakasz-kontextus ('S08', 'FINAL' …; None: a projektnapló ellenőrzőpontjaiból). now: az
    időbélyeg (datetime vagy kész szöveg; teszthez). A hiányzó vagy hibás opcionális fájl nem hiba: az érintett
    szabály kimarad, az ok a 'not_checked' listába kerül."""
    if not os.path.isdir(project_dir):
        raise FileNotFoundError("Nincs ilyen projektmappa: %s" % project_dir)
    ctx = _Ctx(project_dir, None)
    checkpoints, decisions, grade_rows = _journal(ctx)
    ctx.stage = _norm_stage(stage) if stage is not None else _journal_stage(checkpoints)
    meta = _load_meta(ctx)
    specs = _load_specs(ctx)
    runs = _load_runs(ctx)
    outcomes = _outcomes(ctx, meta, specs, runs)
    specs_by_path = {sp["rel"]: sp for sp in specs}
    if not outcomes:
        ctx.skip(None, None, "a projektben nincs kimenet (%s, %s, %s/<kimenet>/*/run.json, a projektnapló "
                 "commit-futásai vagy %s/*.prov.json)" % (META_FILE, SPEC_DIR, ANALYSIS_DIR, DATA_DIR))
    appraisals = _appraisals(ctx) if outcomes else None
    included = _included_studies(ctx) if outcomes else (None, None)
    st = ctx.load_json(STUDIES_FILE)
    label_to_id, design_of, smap = {}, {}, None
    if isinstance(st, dict) and isinstance(st.get("studies"), list):
        for s in st["studies"]:
            if isinstance(s, dict) and _present(s.get("label")) and _present(s.get("study_id")):
                label_to_id[_fold(s["label"])] = _fold(s["study_id"])
            if isinstance(s, dict) and _nonempty(s.get("design")) and _present(s.get("study_id")):
                design_of[_fold(s["study_id"])] = s["design"]
        from . import prisma
        try:
            smap = prisma.studies_counts(st)
        except prisma.PrismaError:
            smap = None
    # v1-bemenetek: minden értékelés, GRADE-dokumentumok, PRISMA-folyamat
    entries = _all_appraisals(ctx)
    grade_docs = _grade_docs(ctx)
    runs_by_id = {r["run_id"]: r for r in runs}
    flow_src = _flow_doc(ctx)
    flow_meta = None
    if flow_src is not None:
        im = flow_src[1].get("included_meta")
        if not isinstance(im, dict):
            from . import prisma
            im = prisma.normalize(flow_src[1])[0].get("included_meta")
        flow_meta = (flow_src[0], im) if im is not None else None
    conventions = meta.get("conventions") if isinstance(meta, dict) and isinstance(meta.get("conventions"),
                                                                                   dict) else None
    grade_any = bool(grade_docs) or bool(grade_rows)
    sof_any = os.path.isdir(ctx.abs(SOF_DIR))
    coverage, no_kettos, x015_any, tools_any = collections.OrderedDict(), [], False, False
    table_ids = set()
    single = len(outcomes) == 1
    for oc in outcomes:
        _x001(ctx, oc)
        tools = _outcome_tools(meta, oc)
        tools_any = tools_any or bool(tools)
        tab = ctx.table(oc.data, oc.id, ("X003", "X005", "X006", "X010", "X013") + (("X004",) if tools else ()))
        prov = _prov(ctx, oc, ("X013", "X022") if tab is not None else ())
        prun = _primary_run(ctx, oc, specs_by_path)
        if tab is not None:
            if appraisals:
                _x003(ctx, oc, tab, appraisals, label_to_id)
            _x005_x006(ctx, oc, "X005", tab, specs_by_path)
            _x005_x006(ctx, oc, "X006", tab, specs_by_path)
            _x010(ctx, oc, tab, prov)
            _x013(ctx, oc, tab, prov)
            if tools:
                _x004(ctx, oc, tab, prun, specs_by_path, entries or [], tools, label_to_id, design_of, coverage)
            if "study_id" in set(_columns(tab[1])):
                table_ids.update(str(r["study_id"]) for r in tab[0] if _present(r.get("study_id")))
        _x014(ctx, oc, tab, included)
        _x016(ctx, oc, decisions, single)
        _x022(ctx, oc, tab, prov)
        if grade_any:
            _x007(ctx, oc, prun, _grade_rows_for(grade_rows, oc),
                  [e for e in grade_docs or () if e["outcome"] == oc.id], runs_by_id)
        if sof_any:
            _x008(ctx, oc, prun, runs_by_id)
        _x009(ctx, oc, no_kettos)
        x015_any = _x015(ctx, oc, prun, tab, specs_by_path, label_to_id, smap, flow_meta, len(outcomes)) or x015_any
    if outcomes:
        if not tools_any:
            ctx.skip("X004", None, "a kimenetek értékelő eszköze nincs megadva (ma-projekt.json appraisal_tools, pl. "
                                   "[\"rob2\"]); így nem dönthető el, melyik eszközzel kell értékelni")
        if not grade_any:
            ctx.skip("X007", None, "nincs GRADE-ítélet (projektnapló grade-sor vagy %s/*.grade.json)" % GRADE_DIR)
        if not sof_any:
            ctx.skip("X008", None, "nincs SoF-táblázat (%s/*.sof.json)" % SOF_DIR)
        if len(no_kettos) == len(outcomes):
            ctx.skip("X009", None, "nincs kettős kinyerés (%s)" % ", ".join(sorted(set(no_kettos))))
        else:
            for kdir in sorted(set(no_kettos)):
                ctx.skip("X009", None, "nincs kettős kinyerés ebben a mappában: %s" % kdir)
        if not x015_any:
            ctx.skip("X015", None, "a metaanalízisbe vont vizsgálatok kimenetenkénti száma nincs megadva (studies.json "
                                   "outcomes lista vagy a PRISMA included_meta)")
    _x011(ctx, meta, entries, smap, sorted(table_ids))
    _x012(ctx, meta, entries, ctx.stage)
    _x017(ctx, entries, conventions)
    _x019(ctx, grade_docs, grade_rows)
    _x020(ctx, ctx.stage)
    _x021(ctx, flow_src)
    figs = _figures(ctx)
    if figs is None:
        for code in ("X002", "X018"):
            ctx.skip(code, None, "nincs exportált ábra (%s)" % FIGURES_DIR)
    else:
        figs, orphans = figs
        for code in ("X002", "X018"):
            if orphans:
                ctx.skip(code, None, "%d ábrához nincs <név>.result.json (szk.figure-result/v1), így nem ellenőrizhető: "
                                     "%s" % (len(orphans), _plural_list(orphans)))
            elif not figs:
                ctx.skip(code, None, "nincs exportált ábra (%s/*.result.json)" % FIGURES_DIR)
        _x002(ctx, figs, runs)
        _x018(ctx, figs, runs)
    order = {o.id: i for i, o in enumerate(outcomes)}
    findings = sorted(ctx.findings, key=lambda f: (SEVERITIES.index(f["severity"]), f["code"],
                                                   order.get(f["outcome"], -1)))
    summary = collections.OrderedDict((s, sum(1 for f in findings if f["severity"] == s)) for s in SEVERITIES)
    rep = collections.OrderedDict()
    rep["schema"] = SCHEMA
    rep["engine_version"] = __version__
    rep["project"] = os.path.basename(os.path.normpath(os.path.abspath(project_dir)))
    rep["title"] = meta.get("title") if isinstance(meta, dict) and isinstance(meta.get("title"), str) else None
    rep["generated"] = _now_utc(now)
    rep["stage"] = ctx.stage
    rep["summary"] = summary
    rep["findings"] = findings
    rep["not_checked"] = ctx.not_checked
    rep["rules_checked"] = sorted(RULES)
    rep["outcomes"] = [o.id for o in outcomes]
    rep["inputs"] = collections.OrderedDict(sorted((k, v) for k, v in ctx.inputs.items() if _relpath_ok(k)))
    rep["amstar2_hints"] = _amstar2_hints(ctx, flow_src, coverage, grade_docs, grade_rows, outcomes)
    return _clean(rep)


def audit_gate_errors(project_dir):
    """A FINAL audit-kaput elutasító találatok (minden error szintű X-találat FINAL szakasz-kontextusban)."""
    return [f for f in project_audit(project_dir, stage=FINAL)["findings"] if f["severity"] == "error"]


def checkpoint_gate_errors(project_dir, stage):
    """Egy szakasz PASS-át blokkoló X-találatok: FINAL-nál minden error szintű (audit_gate_errors); egyébként azok az
    error-találatok, amelyek szabálya (GATE_STAGES) erre vagy egy korábbi szakaszra kapuz — pl. az X009 (lezáratlan
    kettős kinyerés) az S08 (szintézis) PASS-át. Ismeretlen / érvénytelen szakasz → ValueError (projekt.parse_stage)."""
    st = _norm_stage(stage)
    if st is None:
        raise ValueError("A kapuhoz szakaszkód kell (S00–S14 vagy FINAL).")
    if st == FINAL:
        return audit_gate_errors(project_dir)
    idx = _stage_index(st)
    gated = [c for c, g in GATE_STAGES.items() if _stage_index(g) <= idx]
    if not gated:
        return []
    return [f for f in project_audit(project_dir, stage=st)["findings"]
            if f["severity"] == "error" and f["code"] in gated]


def _data_tables(project_dir):
    d = os.path.join(project_dir, DATA_DIR)
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d) if n.lower().endswith((".csv", ".tsv", ".txt"))
                  and os.path.isfile(os.path.join(d, n)))


def coverage_warning(rep, project_dir):
    """Figyelmeztetés, ha az audit egyetlen kimenetet sem talált, pedig a 03_adatok-ban van adattábla: ilyenkor a
    kapu átengedése nem jelenti, hogy bármi ellenőrződött (pl. a commit-futások a projekten kívül vannak)."""
    if rep.get("outcomes"):
        return None
    tables = _data_tables(project_dir)
    if not tables:
        return None
    return ("A projekt-audit egyetlen kimenetet sem talált (nincs ma-projekt.json, spec, commit-futás vagy eredet-"
            "oldalfájl), pedig a %s mappában van adattábla (%s): az X-szabályok (pl. X001 elavult futás) semmit sem "
            "ellenőriztek. Futtasd az elemzést --project-tel (commit-futás), és ellenőrizd: ma.py project audit "
            "<mappa>." % (DATA_DIR, _plural_list(tables, 5)))


def gate_message(errors):
    """A FINAL audit-kapu elutasításának szövege (üres lista: None)."""
    if not errors:
        return None
    groups = collections.OrderedDict()
    for f in errors:
        groups.setdefault((f["code"], f.get("outcome")), []).append(f)
    return ("%d hiba szintű X-szabály találat van a projekt-auditban, ezért a FINAL ellenőrzőpont nem adható: %s "
            "(részletek: ma.py project audit <mappa>)" % (len(errors), "; ".join(
                "%s%s%s: %s" % (code, (" [%s]" % oid) if oid else "", (" (%d×)" % len(fs)) if len(fs) > 1 else "",
                                fs[0]["title"]) for (code, oid), fs in groups.items())))


def require_gate(project_dir, warnings=None):
    """'checkpoint --stage FINAL --audit-gate': ValueError, ha van error szintű X-találat. warnings (lista): ide kerül
    a coverage_warning, ha az audit semmit sem tudott ellenőrizni."""
    rep = project_audit(project_dir, stage=FINAL)
    msg = gate_message([f for f in rep["findings"] if f["severity"] == "error"])
    if msg:
        raise ValueError(msg)
    w = coverage_warning(rep, project_dir)
    if w and warnings is not None:
        warnings.append(w)


# ------------------------------------------------------------------ megjelenítés, metaadat, séma
def format_text(rep):
    """A jelentés szöveges alakja (ma.py project audit <mappa>)."""
    s = rep["summary"]
    lines = ["Projekt-audit (%s; szakasz: %s): %d hiba, %d figyelmeztetés, %d megjegyzés" % (
        rep["project"], rep.get("stage") or "ismeretlen", s["error"], s["warning"], s["info"])]
    for f in rep["findings"]:
        lines.append("[%s] %-10s %s — %s: %s" % (f["code"], _SEV_LABEL[f["severity"]], f.get("outcome") or "(projekt)",
                                                 f["title"], f["detail"]))
        if f.get("suggested_command"):
            lines.append("    javasolt parancs: %s" % " ".join(_quote(x) for x in f["suggested_command"]))
        if f.get("kb_refs"):
            lines.append("    KB: %s" % ", ".join(f["kb_refs"]))
    if rep.get("not_checked"):
        lines.append("Nem ellenőrizhető:")
        for n in rep["not_checked"]:
            lines.append("  - %s%s: %s" % (n.get("code") or "audit", (" [%s]" % n["outcome"]) if n.get("outcome")
                                           else "", n["reason"]))
    if not rep["findings"] and not rep.get("outcomes"):
        lines.append("Nincs X-szabály találat, de kimenet sem: a szabályok (%s) semmit sem ellenőriztek." %
                     ", ".join(rep["rules_checked"]))
    elif not rep["findings"]:
        lines.append("Nincs X-szabály találat (ellenőrzött szabályok: %s)." % ", ".join(rep["rules_checked"]))
    return "\n".join(lines)


def _quote(s):
    return s if re.fullmatch(r"[\w./=:,@+-]+", s or "") else "'%s'" % str(s).replace("'", "'\\''")


def to_json(rep):
    """Szigorú JSON (NaN/Infinity nélkül), UTF-8 szövegként."""
    return json.dumps(_clean(rep), ensure_ascii=False, indent=1, allow_nan=False)


def rules_table():
    """A szabályok metaadata (rules export, KB): [{code, severity, stage, title, advice, source, kb_refs,
    escalation}]."""
    return [{"code": c, "severity": RULES[c][0], "stage": RULE_STAGES[c], "title": RULES[c][1],
             "advice": RULES[c][2], "source": RULES[c][3], "kb_refs": list(KB_REFS.get(c, ())),
             "escalation": ({"from": ESCALATION[c], "before": "warning"} if c in ESCALATION else None)}
            for c in sorted(RULES)]


def audit_schema():
    """A szk.ma.project-audit/v1 JSON Schema (2020-12) — a metaelemzes/contracts/ma.project-audit.v1.schema.json
    (egy igazságforrás; a teszt a kimenetet ehhez méri)."""
    from . import contracts
    return contracts.load("ma.project-audit", 1)


def cli_main(project_dir, as_json=False, stage=None, out=None):
    """'ma.py project audit <mappa> [--json] [--stage S]' → kilépési kód (1, ha van error szintű találat)."""
    import sys
    out = out or sys.stdout
    rep = project_audit(project_dir, stage=stage)
    out.write((to_json(rep) if as_json else format_text(rep)) + "\n")
    return 1 if rep["summary"]["error"] else 0
