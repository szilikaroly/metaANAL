# -*- coding: utf-8 -*-
"""ClinicalTrials.gov API v2 kliens — vizsgálat NCT-szám szerint, keresés (``query.term``, ``query.cond``,
``query.intr``, ``filter.advanced``, ``pageToken``-lapozás), verzió.

Kezdőknek: a ClinicalTrials.gov a klinikai vizsgálatok nyilvános regisztere; kulcs nem kell.

FIGYELEM (TERV 3.1, valós megfigyelés): a ``referencesModule.references[].type`` MEGBÍZHATATLAN. Az
NCT00953927 a saját fő eredményközlését (Tameris 2013, PMID 23391465) ``BACKGROUND``-ként sorolja, és egy
másik vizsgálat (NCT04975178) ugyanezt a PMID-et szintén ``BACKGROUND``-ként idézi. Ezért egy CT.gov-hivatkozás
önmagában NEM kapcsol közleményt vizsgálathoz: ``RESULT``/``DERIVED`` csak *megerősítő*, ``BACKGROUND``
*gyenge* jel (``link_strength``); az *erős* kapcsolat a közlemény saját regisztrációs nyilatkozata
(PubMed DataBank, absztrakt, Europe PMC annotáció).
"""

from __future__ import absolute_import

from .net import BaseClient, Paged, get_path, as_list, to_int, norm_nct, norm_pmid

SOURCE = "ctgov"
PLATFORM = "ClinicalTrials.gov (API v2)"
BASE = "https://clinicaltrials.gov/api/v2/"


def link_strength(ref_type):
    """A CT.gov hivatkozástípus jelereje: ``RESULT``/``DERIVED`` → ``confirming``; ``BACKGROUND`` (vagy
    ismeretlen) → ``weak`` (önmagában nem kapcsol, csak tipp)."""
    t = (ref_type or "").upper()
    return "confirming" if t in ("RESULT", "DERIVED") else "weak"


def references(study):
    """``[{"pmid", "type", "strength", "citation"}]`` egy vizsgálat hivatkozásaiból."""
    out = []
    for r in as_list(get_path(study, "protocolSection", "referencesModule", "references")):
        out.append({"pmid": norm_pmid(r.get("pmid")), "type": r.get("type"), "strength": link_strength(r.get("type")),
                    "citation": (r.get("citation") or "")[:300] or None})
    return out


def study_record(study):
    """Egy CT.gov-vizsgálat normalizált alakja (regiszter-adatok; nyilvános közhiteles rekord)."""
    ps = study.get("protocolSection") or {}
    ident = ps.get("identificationModule") or {}
    status = ps.get("statusModule") or {}
    design = ps.get("designModule") or {}
    arms = ps.get("armsInterventionsModule") or {}
    cond = ps.get("conditionsModule") or {}
    sponsor = ps.get("sponsorCollaboratorsModule") or {}
    secondary = []
    for s in as_list(ident.get("secondaryIdInfos")):
        if s.get("id"):
            secondary.append({"id": s.get("id"), "type": s.get("type"), "domain": s.get("domain")})
    return {
        "nct": norm_nct(ident.get("nctId")),
        "brief_title": ident.get("briefTitle"),
        "official_title": ident.get("officialTitle"),
        "acronym": ident.get("acronym"),
        "org_study_id": get_path(ident, "orgStudyIdInfo", "id"),
        "secondary_ids": secondary,
        "overall_status": status.get("overallStatus"),
        "start_date": get_path(status, "startDateStruct", "date"),
        "completion_date": get_path(status, "completionDateStruct", "date")
        or get_path(status, "primaryCompletionDateStruct", "date"),
        "first_post_date": get_path(status, "studyFirstPostDateStruct", "date"),
        "results_first_post_date": get_path(status, "resultsFirstPostDateStruct", "date"),
        "study_type": design.get("studyType"),
        "phases": as_list(design.get("phases")),
        "enrollment": to_int(get_path(design, "enrollmentInfo", "count")),
        "conditions": as_list(cond.get("conditions")),
        "interventions": [i.get("name") for i in as_list(arms.get("interventions")) if i.get("name")],
        "lead_sponsor": get_path(sponsor, "leadSponsor", "name"),
        "has_results": bool(study.get("hasResults")),
        "references": references(study),
    }


class Client(BaseClient):
    """ClinicalTrials.gov v2 kliens (a 20.5 szerződés szerinti ``study`` és ``studies`` metódusokkal)."""

    SOURCE = SOURCE
    PLATFORM = PLATFORM

    def version(self):
        return self.http.get(SOURCE, BASE + "version", accept="json", cache=False).json()

    def study(self, nct, fields=None):
        """Egy vizsgálat (nyers v2-alak) vagy ``None`` (nincs ilyen / érvénytelen NCT)."""
        n = norm_nct(nct)
        if not n:
            return None
        params = [("format", "json")]
        if fields:
            params.append(("fields", fields if isinstance(fields, str) else ",".join(fields)))
        resp = self.http.get(SOURCE, BASE + "studies/" + n, params=params, accept="json", allow_status=(400,))
        if resp.status != 200:
            return None
        return resp.json()

    def studies(self, term=None, cond=None, intr=None, advanced=None, fields=None, page_size=100, max_results=None,
                count_total=True, sort=None):
        """Keresés ``Paged`` iterátorként (nyers v2-vizsgálatok; ``pageToken``-lapozás, ``totalCount``).

        Pl. frissítő keresés: ``advanced="AREA[StudyFirstPostDate]RANGE[2023-01-01,MAX]"``; közlemény →
        vizsgálatok: ``term="AREA[ReferencePMID]23391465"`` (lásd ``studies_citing_pmid``)."""
        page_size = max(1, min(int(page_size), 1000))

        def fetch(state):
            params = [("format", "json"), ("pageSize", page_size)]
            if term:
                params.append(("query.term", term))
            if cond:
                params.append(("query.cond", cond))
            if intr:
                params.append(("query.intr", intr))
            if advanced:
                params.append(("filter.advanced", advanced))
            if fields:
                params.append(("fields", fields if isinstance(fields, str) else ",".join(fields)))
            if sort:
                params.append(("sort", sort))
            if count_total and state is None:
                params.append(("countTotal", "true"))
            if state:
                params.append(("pageToken", state))
            data = self.http.get(SOURCE, BASE + "studies", params=params, accept="json").json()
            items = as_list(data.get("studies"))
            return items, to_int(data.get("totalCount")), data.get("nextPageToken") or None

        q = "; ".join("%s=%s" % (k, v) for k, v in (("query.term", term), ("query.cond", cond), ("query.intr", intr),
                                                      ("filter.advanced", advanced)) if v)
        pager = Paged(SOURCE, fetch, max_results=max_results, query=q)
        return pager

    def studies_citing_pmid(self, pmid):
        """Vizsgálatok, amelyek a PMID-et hivatkozásként felsorolják — a hivatkozás típusával és jelerejével.
        ``[{"nct", "type", "strength"}]``. Gyenge jel; önmagában nem kapcsol (lásd a modul leírását)."""
        p = norm_pmid(pmid)
        if not p:
            return []
        out = []
        pager = self.studies(term="AREA[ReferencePMID]%s" % p,
                             fields="protocolSection.identificationModule.nctId,protocolSection.referencesModule",
                             page_size=100, max_results=500)
        for st in pager:
            nct = norm_nct(get_path(st, "protocolSection", "identificationModule", "nctId"))
            for r in references(st):
                if r["pmid"] == p:
                    out.append({"nct": nct, "type": r["type"], "strength": r["strength"]})
        return out

    def _probe(self):
        v = self.version()
        if v.get("apiVersion"):
            return self._result("ok", http_status=200, details={"api_version": v.get("apiVersion"),
                                                                "data_timestamp": v.get("dataTimestamp")})
        return self._result("unreachable", detail_text={"hu": "Váratlan verzióválasz.",
                                                        "en": "Unexpected version response."})
