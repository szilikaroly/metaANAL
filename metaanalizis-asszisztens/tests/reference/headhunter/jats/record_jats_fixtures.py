# -*- coding: utf-8 -*-
"""Fejlesztői segéd: valós, nyílt hozzáférésű áttekintések JATS-ából VÁGOTT tesztfixture-ök (N4).

Mit csinál: az Europe PMC ``fullTextXML`` végpontjáról letölti az áttekintés teljes szövegét (csak memóriában),
a ``jats.trim_for_fixture`` segédjével kivágja belőle a bibliográfiai adatokat, a táblázatok vázát (fejléc, első
oszlop, rövid számcellák) és a kinyerés által használt ≤ 300 karakteres mondatokat, ellenőrzi, hogy a vágott
dokumentumon a kinyerés eredménye AZONOS a teljes szövegével, majd a vágott változatot ``real/<PMCID>.xml``-be,
az eredmény összefoglalóját ``real/expected.json``-ba írja. A teljes szöveg soha nem kerül lemezre.

Csak CC-licencű cikkekből készül fixture (a licenc a fájl fejkommentjében áll).

Használat (a ``metaanalizis-asszisztens`` mappából)::

    python3 tests/reference/headhunter/jats/record_jats_fixtures.py            # mind (élő letöltés)
    python3 tests/reference/headhunter/jats/record_jats_fixtures.py PMC6488980
    python3 tests/reference/headhunter/jats/record_jats_fixtures.py --from-dir /tmp/jats   # helyi teljes szövegek
    python3 tests/reference/headhunter/jats/record_jats_fixtures.py --check     # csak összevetés, nem ír

Hálózat: Python stdlib ``urllib`` (a ``HTTPS_PROXY`` beállítást követi), User-Agent a ``MA_CONTACT_EMAIL``
címmel, ha be van állítva; kérések között 1 s szünet.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from metaelemzes.headhunter import included, jats  # noqa: E402

AT = "2026-10-05T10:00:00Z"

#: PMCID → (leírás, drop_uncited_refs). A leírás a fejkommentbe kerül (mit fed le a fixture).
FIXTURES = {
    "PMC6488980": ("Kashangura 2019 Cochrane review (PMID 31038197): nested ref-list, 6 included studies, "
                   "excluded/ongoing sections, characteristics tables, adverse-event data table", True),
    "PMC4122754": ("Roy 2014 BMJ (PMID 25097193): author-year table without xrefs; Adetifa 2010 first-author "
                   "trap (co-author in another ref of the same year)", False),
    "PMC12070792": ("Gholami 2025 (PMID 40355968): p-split first cells, 2.1/2.2 rows of one study, "
                    "Acharjee 2015 -> CR24, Azadbakht 2007 vs 2008", True),
    "PMC4364968": ("Ebert 2015 PLoS One (PMID 25786025): two-line labels ('Fleming,' / '2012 [47]') with "
                   "rowspan'd attributes, 'December 4, 2013' search date", True),
    "PMC6396088": ("Rubinstein 2019 BMJ (PMID 30867144): adverse-event table lists 23 of 47 trials; the "
                   "consistent '47 trials [refs]' statement supplies the rest (statement_only)", False),
    "PMC6405619": ("Katsanos 2018 JAHA (PMID 30561254): trial-acronym rows citing several reports, "
                   "'mm2' superscript trap, 'TSA included 13 RCTs' sub-count vs 28 included", True),
    "PMC4381278": ("Machado 2015 BMJ (PMID 25828856): data tables with per-arm N and MD (95% CI) -> secondary "
                   "values; ambiguous 'Mean (SD or SE)' columns must stay uncaptured; 12 reports / 13 trials", False),
    "PMC4382075": ("Cortese 2015 JAACAP (PMID 25721181): footnote-letter superscripts in labels "
                   "('Egeland<sup>i</sup>'), '15 trials (reported in 16 papers)' vs 16 rows", True),
}

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/%s/fullTextXML"


def _user_agent():
    mail = (os.environ.get("MA_CONTACT_EMAIL") or "").strip()
    return "metaelemzes-headhunter/1.0.0 (python-urllib%s)" % ("; mailto:" + mail if mail else "")


def fetch(pmcid):
    req = urllib.request.Request(EPMC % pmcid, headers={"User-Agent": _user_agent(), "Accept": "application/xml"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec - fejlesztői segéd, rögzített host
        return resp.read()


def summary(res):
    """Az összevetéshez és a regressziós teszthez használt összefoglaló (szöveg nélkül)."""
    return included.summarize_result(res)


def build(pmcid, raw, drop_uncited):
    full = jats.parse(raw)
    lic = full.meta.get("license") or ""
    if not lic.startswith("cc "):
        raise SystemExit("%s: nem CC-licencű (%r) — nem készítünk belőle fixture-t." % (pmcid, lic or None))
    rid = "rv-pmid-%s" % (full.meta.get("ids", {}).get("pmid") or pmcid.lower())
    want = summary(included.extract_included(full, rid, at=AT))
    note = ("TRIMMED real JATS fixture (N4): %s; Europe PMC fullTextXML %s; PMID %s; DOI %s; license %s; "
            "retrieved %s. Kept: bibliographic data (first %d authors), table skeletons (header, first column, "
            "short numeric cells), and only the <=300-character sentences the extraction uses. "
            "Not the article text." % (FIXTURES[pmcid][0], pmcid, full.meta.get("ids", {}).get("pmid"),
                                       full.meta.get("ids", {}).get("doi"), lic,
                                       datetime.date.today().isoformat(), 3))
    trimmed = jats.trim_for_fixture(raw, keep_patterns=[included.fixture_sentence_filter], source_note=note,
                                    drop_uncited_refs=drop_uncited)
    got = summary(included.extract_included(jats.parse(trimmed), rid, at=AT))
    if got != want:
        raise SystemExit("%s: a vágott fixture kinyerése eltér a teljes szövegétől — nem írtuk ki." % pmcid)
    return trimmed, {"pmcid": pmcid, "review_id": rid, "license": lic, "title": full.meta.get("title"),
                     "covers": FIXTURES[pmcid][0], "expected": want}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pmcids", nargs="*", help="alapból mind (FIXTURES)")
    ap.add_argument("--from-dir", help="helyi teljes JATS-fájlok mappája (<PMCID>.xml) letöltés helyett")
    ap.add_argument("--check", action="store_true", help="csak összevetés, nem ír fájlt")
    a = ap.parse_args(argv)
    ids = a.pmcids or sorted(FIXTURES)
    out_dir = os.path.join(HERE, "real")
    exp_path = os.path.join(out_dir, "expected.json")
    expected = {}
    if os.path.exists(exp_path):
        with open(exp_path, encoding="utf-8") as fh:
            expected = json.load(fh)
    for i, pmcid in enumerate(ids):
        if pmcid not in FIXTURES:
            raise SystemExit("Ismeretlen PMCID (vedd fel a FIXTURES-be): %s" % pmcid)
        if a.from_dir:
            with open(os.path.join(a.from_dir, pmcid + ".xml"), "rb") as fh:
                raw = fh.read()
        else:
            if i:
                time.sleep(1.0)
            raw = fetch(pmcid)
        trimmed, meta = build(pmcid, raw, FIXTURES[pmcid][1])
        print("%s: %d kB -> %d kB, %d jelölt" % (pmcid, len(raw) // 1024, len(trimmed.encode("utf-8")) // 1024,
                                                 len(meta["expected"]["candidates"])))
        if a.check:
            continue
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, pmcid + ".xml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(trimmed)
        expected[pmcid] = meta
    if not a.check:
        with open(exp_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(expected, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
