# -*- coding: utf-8 -*-
"""PubMed (NCBI E-utilities) kliens — esearch, esummary, efetch, elink, ecitmatch, idconv, PMC efetch.

Kezdőknek: a PubMed az orvosi szakirodalom fő adatbázisa. Ez a modul az NCBI nyilvános programozói
felületét (E-utilities) hívja. Kulcs nem kell; ``MA_NCBI_APIKEY`` beállításával másodpercenként 10 kérés
mehet (különben 3). Az NCBI a kulcsot csak URL-paraméterként fogadja — ezt minden naplóból, hibából,
gyorsítótár-kulcsból és kazettából kivágjuk (net.redact_url). Az udvarias azonosítás: ``tool`` és
``email`` (``MA_CONTACT_EMAIL``).

Megfigyelt viselkedés (TERV 3.1): a ``systematic[sb]`` szűrő, a ``datetype=edat`` + ``mindate/maxdate``,
az ``ecitmatch``, az ``elink`` ``pubmed_pubmed_refs``/``pubmed_pubmed_citedin``, az EFetch ``DataBankList``
és ``ReferenceList`` működik. Az NCBI PMC ID-konverter a sandboxból blokkolt — ezért az ``idconv`` tartalék
úton (esearch ``[doi]``, esummary) is megy.
"""

from __future__ import absolute_import

import re

from . import net
from .net import (BaseClient, SourceUnavailable, HttpError, ParseError, get_env, get_path, iter_local, find_local,
                  xml_text, norm_pmid, norm_pmcid, norm_doi, norm_nct, as_list, to_int)

SOURCE = "pubmed"
PLATFORM = "PubMed (NCBI E-utilities API)"
BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
HOST = "eutils.ncbi.nlm.nih.gov"
IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
TOOL = "metaelemzes-headhunter"
PROBE_PMID = "8309034"  # Colditz 1994 (BCG) — stabil, ismert rekord

#: a PubMed-rekordban gyakori regiszter-azonosító minták (TERV 7. fejezet)
REGISTRY_PATTERNS = (
    ("nct", re.compile(r"\bNCT\d{8}\b")),
    ("isrctn", re.compile(r"\bISRCTN\d{8}\b")),
    ("actrn", re.compile(r"\bACTRN\d{14}\b")),
    ("chictr", re.compile(r"\bChiCTR[-A-Za-z0-9]+\b")),
    ("eudract", re.compile(r"\b\d{4}-\d{6}-\d{2}\b")),
    ("irct", re.compile(r"\bIRCT\d+N\d+\b")),
    ("ctri", re.compile(r"\bCTRI/\d{4}/\d{2,3}/\d{6}\b")),
    ("drks", re.compile(r"\bDRKS\d{8}\b")),
    ("pactr", re.compile(r"\bPACTR\d{15}\b")),
    ("kct", re.compile(r"\bKCT\d{7}\b")),
    ("umin", re.compile(r"\bUMIN\d{9}\b")),
    ("ntr", re.compile(r"\bNTR\d+\b")),
)

#: E-utilities: ennél hosszabb lekérdezés/ID-lista POST-tal megy
_POST_THRESHOLD = 1500


def _year_from(*values):
    for v in values:
        if not v:
            continue
        m = re.search(r"\b(1[89]\d{2}|20\d{2}|2100)\b", str(v))
        if m:
            return int(m.group(1))
    return None


def summary_record(doc):
    """Egy esummary-dokumentum normalizált alakja (bibliográfiai tények; absztrakt nincs benne)."""
    ids = {}
    for a in doc.get("articleids", []) or []:
        t = a.get("idtype")
        if t and t not in ids:
            ids[t] = a.get("value")
    authors = [a.get("name") for a in (doc.get("authors") or []) if a.get("name") and a.get("authtype", "Author") == "Author"]
    doi = norm_doi(ids.get("doi"))
    if not doi:
        m = re.search(r"doi:\s*(\S+)", doc.get("elocationid") or "")
        doi = norm_doi(m.group(1)) if m else None
    return {
        "pmid": norm_pmid(doc.get("uid")),
        "pmcid": norm_pmcid(ids.get("pmc")),
        "doi": doi,
        "title": (doc.get("title") or "").strip() or None,
        "authors": authors[:50],
        "authors_truncated": len(authors) > 50,
        "first_author": authors[0] if authors else (doc.get("sortfirstauthor") or None),
        "last_author": doc.get("lastauthor") or None,
        "year": _year_from(doc.get("sortpubdate"), doc.get("pubdate"), doc.get("epubdate")),
        "pubdate": doc.get("pubdate") or None,
        "journal": doc.get("source") or None,
        "journal_full": doc.get("fulljournalname") or None,
        "volume": doc.get("volume") or None,
        "issue": doc.get("issue") or None,
        "pages": doc.get("pages") or None,
        "pub_types": list(doc.get("pubtype") or []),
        "language": (doc.get("lang") or [None])[0],
        "issn": doc.get("issn") or None,
        "essn": doc.get("essn") or None,
        "retracted": "Retracted Publication" in (doc.get("pubtype") or []),
    }


def _article_date_year(art):
    for path in ("Journal/JournalIssue/PubDate/Year", "ArticleDate/Year"):
        y = art.findtext(path)
        if y:
            return _year_from(y)
    return _year_from(art.findtext("Journal/JournalIssue/PubDate/MedlineDate"))


def parse_efetch(xml_text_value):
    """EFetch (``db=pubmed``, XML) feldolgozása rekord-dictekre.

    A visszaadott ``abstract`` mező CSAK memóriában használható (keresési dátum, szűrés) — projektfájlba,
    exportba, kazettába nem kerülhet (N4)."""
    root = net.parse_xml(xml_text_value, source=SOURCE)
    out = []
    for art_el in iter_local(root, "PubmedArticle"):
        mc = art_el.find("MedlineCitation")
        if mc is None:
            continue
        art = mc.find("Article")
        pmid = norm_pmid(mc.findtext("PMID"))
        rec = {"pmid": pmid, "title": None, "journal": None, "journal_full": None, "year": None, "volume": None,
               "issue": None, "pages": None, "authors": [], "first_author": None, "doi": None, "pmcid": None,
               "pub_types": [], "language": None, "databanks": [], "registry_ids": [], "comments_corrections": [],
               "retracted": False, "references": [], "abstract": "", "mesh": []}
        if art is not None:
            rec["title"] = xml_text(art.find("ArticleTitle")) or None
            rec["journal"] = art.findtext("Journal/ISOAbbreviation") or art.findtext("Journal/Title")
            rec["journal_full"] = art.findtext("Journal/Title")
            rec["year"] = _article_date_year(art)
            rec["volume"] = art.findtext("Journal/JournalIssue/Volume")
            rec["issue"] = art.findtext("Journal/JournalIssue/Issue")
            rec["pages"] = art.findtext("Pagination/MedlinePgn")
            names = []
            for au in art.findall("AuthorList/Author"):
                last = au.findtext("LastName")
                if last:
                    names.append(("%s %s" % (last, au.findtext("Initials") or "")).strip())
                elif au.findtext("CollectiveName"):
                    names.append(xml_text(au.find("CollectiveName")))
            rec["authors"] = names[:50]
            rec["authors_truncated"] = len(names) > 50
            rec["first_author"] = names[0] if names else None
            rec["pub_types"] = [xml_text(p) for p in art.findall("PublicationTypeList/PublicationType")]
            rec["language"] = art.findtext("Language")
            for e in art.findall("ELocationID"):
                if e.get("EIdType") == "doi" and not rec["doi"]:
                    rec["doi"] = norm_doi(xml_text(e))
            parts = []
            for at in art.findall("Abstract/AbstractText"):
                label = at.get("Label")
                txt = xml_text(at)
                parts.append(("%s: %s" % (label, txt)) if label else txt)
            rec["abstract"] = " ".join(p for p in parts if p)
            for db in art.findall("DataBankList/DataBank"):
                name = db.findtext("DataBankName")
                accs = [xml_text(a) for a in db.findall("AccessionNumberList/AccessionNumber") if xml_text(a)]
                rec["databanks"].append({"name": name, "accessions": accs})
                rec["registry_ids"].extend(accs)
        for cc in mc.findall("CommentsCorrectionsList/CommentsCorrections"):
            rec["comments_corrections"].append({"type": cc.get("RefType"), "pmid": norm_pmid(cc.findtext("PMID")),
                                                "source": xml_text(cc.find("RefSource")) or None})
        rec["mesh"] = [xml_text(d) for d in mc.findall("MeshHeadingList/MeshHeading/DescriptorName")]
        pd = art_el.find("PubmedData")
        if pd is not None:
            for aid in pd.findall("ArticleIdList/ArticleId"):
                t = aid.get("IdType")
                if t == "doi" and not rec["doi"]:
                    rec["doi"] = norm_doi(xml_text(aid))
                elif t == "pmc" and not rec["pmcid"]:
                    rec["pmcid"] = norm_pmcid(xml_text(aid))
            for ref in pd.findall("ReferenceList/Reference"):
                ids = {}
                for aid in ref.findall("ArticleIdList/ArticleId"):
                    t = aid.get("IdType")
                    v = xml_text(aid)
                    if t == "pubmed":
                        ids["pmid"] = norm_pmid(v)
                    elif t == "doi":
                        ids["doi"] = norm_doi(v)
                    elif t == "pmc":
                        ids["pmcid"] = norm_pmcid(v)
                ids = dict((k, v) for k, v in ids.items() if v)
                rec["references"].append({"citation": xml_text(ref.find("Citation"))[:500], "ids": ids})
        rec["retracted"] = ("Retracted Publication" in rec["pub_types"]
                            or any(c["type"] == "RetractionIn" for c in rec["comments_corrections"]))
        seen = set(rec["registry_ids"])
        for _name, rx in REGISTRY_PATTERNS:
            for m in rx.finditer(rec["abstract"] or ""):
                if m.group(0) not in seen:
                    seen.add(m.group(0))
                    rec.setdefault("registry_ids_from_abstract", []).append(m.group(0))
        out.append(rec)
    return out


class Client(BaseClient):
    """PubMed E-utilities kliens (a 20.5 szerződés szerinti metódusokkal)."""

    SOURCE = SOURCE
    PLATFORM = PLATFORM

    def __init__(self, http=None, cfg=None, env=None):
        BaseClient.__init__(self, http, cfg, env)
        if get_env(net.ENV_NCBI_APIKEY, self.env):
            self.http.set_rate(HOST, 10.0)

    def key_configured(self):
        return get_env(net.ENV_NCBI_APIKEY, self.env) is not None

    def _common(self):
        params = [("tool", TOOL)]
        mail = get_env(net.ENV_CONTACT_EMAIL, self.env)
        if mail:
            params.append(("email", mail))
        key = get_env(net.ENV_NCBI_APIKEY, self.env)
        if key:
            params.append(("api_key", key))
        return params

    def _call(self, endpoint, params, accept="json", cache=True, source=SOURCE, allow_status=(), force_post=False):
        url = BASE + endpoint
        params = list(params) + self._common()
        size = sum(len(str(k)) + len(str(v)) + 2 for k, v in params)
        if force_post or size > _POST_THRESHOLD:
            return self.http.post(source, url, data=params, accept=accept, cache=cache, allow_status=allow_status)
        return self.http.get(source, url, params=params, accept=accept, cache=cache, allow_status=allow_status)

    # -- esearch -----------------------------------------------------------------------------

    def esearch(self, term, datetype=None, mindate=None, maxdate=None, retmax=100, retstart=0, sort=None,
                db="pubmed"):
        """Keresés. Visszaad: ``{count, ids, querytranslation, retstart, retmax, warnings}``.

        Dátumkorlát: ``datetype="edat"`` (bekerülés dátuma) vagy ``"pdat"``; ``mindate``/``maxdate``
        ``YYYY``, ``YYYY/MM`` vagy ``YYYY/MM/DD`` (``-`` elválasztó is elfogadott)."""
        params = [("db", db), ("term", term), ("retmode", "json"), ("retmax", int(retmax)),
                  ("retstart", int(retstart))]
        if sort:
            params.append(("sort", sort))
        if datetype:
            params.append(("datetype", datetype))
            if mindate:
                params.append(("mindate", str(mindate).replace("-", "/")))
            params.append(("maxdate", str(maxdate or "3000").replace("-", "/")))
        resp = self._call("esearch.fcgi", params)
        data = resp.json_dict()
        res = net.as_dict(data.get("esearchresult"))
        if res.get("ERROR"):
            raise HttpError(SOURCE, resp.status, endpoint=resp.url, body_excerpt=str(res.get("ERROR")))
        warnings = []
        for key in ("errorlist", "warninglist"):
            for k, v in (res.get(key) or {}).items():
                for item in as_list(v):
                    if item:
                        warnings.append("%s.%s: %s" % (key, k, item))
        return {"count": to_int(res.get("count"), 0), "ids": [str(i) for i in res.get("idlist", [])],
                "querytranslation": res.get("querytranslation"), "retstart": to_int(res.get("retstart"), 0),
                "retmax": to_int(res.get("retmax"), 0), "warnings": warnings, "retrieval": resp.retrieval}

    def esearch_all(self, term, cap=5000, page_size=500, **kw):
        """Az összes találat azonosítója lapozva, legfeljebb ``cap`` darab.

        Visszaad: ``{count, ids, querytranslation, retrieved, complete, error}``; ``complete=False``, ha a
        korlát, a PubMed 10 000-es lapozási határa vagy egy hiba megállította (H012)."""
        ids = []
        first = None
        error = None
        start = 0
        while True:
            want = min(page_size, cap - len(ids))
            if want <= 0:
                break
            try:
                res = self.esearch(term, retmax=want, retstart=start, **kw)
            except SourceUnavailable as exc:
                if first is None:
                    raise
                error = exc
                break
            if first is None:
                first = res
            ids.extend(res["ids"])
            start += len(res["ids"])
            if not res["ids"] or start >= res["count"] or start >= 9999:
                break
        count = first["count"] if first else 0
        complete = error is None and len(ids) >= count
        return {"count": count, "ids": ids, "querytranslation": first["querytranslation"] if first else None,
                "retrieved": len(ids), "complete": complete, "error": error.to_dict() if error else None,
                "warnings": first["warnings"] if first else []}

    def pmid_for_doi(self, doi):
        """DOI → PMID (``"<doi>"[doi]``); csak egyértelmű (egyetlen) találatnál, különben ``None``."""
        d = norm_doi(doi)
        if not d:
            return None
        res = self.esearch('"%s"[doi]' % d, retmax=2)
        return res["ids"][0] if res["count"] == 1 and res["ids"] else None

    # -- esummary / efetch -------------------------------------------------------------------

    def esummary(self, pmids):
        """Metaadat PMID-listára (a bemenet sorrendjében; nem létező PMID kimarad). Normalizált dictek
        (``summary_record``) + ``raw`` (az eredeti esummary-dokumentum, absztrakt nincs benne)."""
        ids = [p for p in (norm_pmid(x) for x in as_list(pmids)) if p]
        out = []
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            resp = self._call("esummary.fcgi", [("db", "pubmed"), ("id", ",".join(chunk)), ("retmode", "json")])
            result = net.as_dict(resp.json_dict().get("result"))
            for pmid in chunk:
                doc = result.get(pmid)
                if not isinstance(doc, dict) or doc.get("error"):
                    continue
                rec = summary_record(doc)
                rec["raw"] = doc
                rec["retrieval"] = resp.retrieval
                out.append(rec)
        return out

    def efetch_xml(self, pmids, db="pubmed"):
        """EFetch XML szövegként (memóriában; az absztraktot tartalmazza — fájlba ne írd)."""
        ids = [p for p in (norm_pmid(x) for x in as_list(pmids)) if p]
        if not ids:
            return ""
        resp = self._call("efetch.fcgi", [("db", db), ("id", ",".join(ids)), ("retmode", "xml")], accept="xml",
                          force_post=len(ids) > 200)
        return resp.text

    def efetch_records(self, pmids):
        """EFetch feldolgozva (``parse_efetch``): DataBank, CommentsCorrections, ReferenceList, absztrakt."""
        ids = [p for p in (norm_pmid(x) for x in as_list(pmids)) if p]
        out = []
        for i in range(0, len(ids), 200):
            text = self.efetch_xml(ids[i:i + 200])
            if text:
                out.extend(parse_efetch(text))
        return out

    # -- elink / ecitmatch -------------------------------------------------------------------

    def elink(self, pmid, linkname, dbfrom="pubmed", db=None):
        """Kapcsolt azonosítók (pl. ``pubmed_pubmed_refs``, ``pubmed_pubmed_citedin``, ``pubmed_pmc``)."""
        p = norm_pmid(pmid)
        if not p:
            return []
        if not db:
            parts = linkname.split("_")
            db = parts[1] if len(parts) > 2 else "pubmed"
        resp = self._call("elink.fcgi", [("dbfrom", dbfrom), ("db", db), ("id", p), ("linkname", linkname),
                                         ("retmode", "json")])
        data = resp.json_dict()
        out = []
        for ls in as_list(data.get("linksets")):
            for lsdb in as_list(net.as_dict(ls).get("linksetdbs")):
                lsdb = net.as_dict(lsdb)
                if lsdb.get("linkname") == linkname:
                    out.extend(str(x) for x in lsdb.get("links", []) or [])
        return out

    def ecitmatch_detail(self, citations):
        """Hivatkozás → PMID (``ecitmatch``). Bemenet: ``(journal, year, volume, first_page, author, key)``
        sorok. Visszaad: ``{key: {"pmid": str|None, "status": "found"|"not_found"|"ambiguous", "raw": str}}``."""
        rows = []
        keys = []
        for c in citations:
            journal, year, volume, page, author, key = (list(c) + [None] * 6)[:6]
            key = str(key if key is not None else len(keys))
            clean = [re.sub(r"[|\r\n]", " ", str(x or "")).strip() for x in (journal, year, volume, page, author)]
            rows.append("|".join(clean + [key]) + "|")
            keys.append(key)
        out = {}
        # kis kötegek: az ecitmatch.cgi GET-tel megy (a POST-ot az NCBI nem dokumentálja ehhez a végponthoz)
        for i in range(0, len(rows), 20):
            chunk = rows[i:i + 20]
            resp = self._call("ecitmatch.cgi", [("db", "pubmed"), ("retmode", "xml"), ("bdata", "\r".join(chunk))],
                              accept="text")
            for line in resp.text.splitlines():
                parts = line.strip().split("|")
                if len(parts) < 7:
                    continue
                key = parts[5]
                val = parts[6].strip()
                if re.match(r"^\d+$", val):
                    out[key] = {"pmid": val, "status": "found", "raw": val}
                elif val.upper().startswith("AMBIGUOUS"):
                    out[key] = {"pmid": None, "status": "ambiguous", "raw": val}
                else:
                    out[key] = {"pmid": None, "status": "not_found", "raw": val}
        for k in keys:
            out.setdefault(k, {"pmid": None, "status": "not_found", "raw": ""})
        return out

    def ecitmatch(self, citations):
        """``{key: pmid|None}`` — csak egyértelmű találat ad PMID-et (utána esummary-címellenőrzés kell, 7. fej.)."""
        return dict((k, v["pmid"]) for k, v in self.ecitmatch_detail(citations).items())

    # -- PMC / idconv ------------------------------------------------------------------------

    def efetch_pmc(self, pmcid):
        """PMC teljes szöveg (JATS) ``efetch db=pmc``-vel — CSAK memóriában (N4); a projekt-gyorsítótárba nem
        kerül (legfeljebb a projekten kívüli ``MA_HH_CACHE_DIR`` alá). ``None``, ha nincs."""
        p = norm_pmcid(pmcid)
        if not p:
            return None
        resp = self._call("efetch.fcgi", [("db", "pmc"), ("id", p[3:]), ("retmode", "xml")], accept="xml",
                          cache="fulltext", source="pmc", allow_status=(400, 500))
        if resp.status != 200:
            return None
        text = resp.text
        if "<article" not in text or re.search(r"<error\b", text[:2000]):
            return None
        return text

    def idconv(self, ids):
        """Azonosító-konverzió (PMID/PMCID/DOI → mindhárom). Elsődlegesen az NCBI PMC ID Converter; ha az nem
        érhető el (pl. blokkolt), tartalék: esearch ``[doi]``/PMCID és esummary.

        Visszaad: ``{bemenet: {"pmid", "pmcid", "doi", "via"}}`` (ismeretlennél a mezők ``None``)."""
        items = [str(x).strip() for x in as_list(ids) if str(x).strip()]
        out = {}
        if not items:
            return out
        try:
            for i in range(0, len(items), 200):
                chunk = items[i:i + 200]
                params = [("ids", ",".join(chunk)), ("format", "json"), ("tool", TOOL)]
                mail = get_env(net.ENV_CONTACT_EMAIL, self.env)
                if mail:
                    params.append(("email", mail))
                resp = self.http.get("pmc", IDCONV_URL, params=params, bucket="pmc:idconv")
                if resp.status != 200:
                    raise SourceUnavailable("pmc", "unreachable", http_status=resp.status, endpoint=resp.url)
                for rec in as_list(resp.json_dict().get("records")):
                    rec = net.as_dict(rec)
                    req = str(rec.get("requested-id") or "")
                    match = next((x for x in chunk if x.lower() == req.lower()), req)
                    if rec.get("status") == "error":
                        out[match] = {"pmid": None, "pmcid": None, "doi": None, "via": "pmc.idconv"}
                    else:
                        out[match] = {"pmid": norm_pmid(rec.get("pmid")), "pmcid": norm_pmcid(rec.get("pmcid")),
                                      "doi": norm_doi(rec.get("doi")), "via": "pmc.idconv"}
            return out
        except (SourceUnavailable, HttpError, ParseError) + net.SHAPE_ERRORS as exc:
            # a konverter ebben a munkamenetben ne terhelődjön újra (pl. proxy blokkolja) — tartalék út
            if isinstance(exc, SourceUnavailable) and exc.status == "unreachable":
                self.http._block("pmc:idconv", exc, until=self.http.now() + 3600.0)
        # tartalék: esearch + esummary (a sandboxban ez működik)
        for item in items:
            pmid = norm_pmid(item) if re.match(r"^\d+$", item) else None
            via = "pubmed.esummary"
            if not pmid and norm_doi(item):
                pmid = self.pmid_for_doi(item)
                via = "pubmed.esearch"
            elif not pmid and norm_pmcid(item) and item.upper().startswith("PMC"):
                res = self.esearch(norm_pmcid(item), retmax=2)
                pmid = res["ids"][0] if res["count"] == 1 and res["ids"] else None
                via = "pubmed.esearch"
            if not pmid:
                out[item] = {"pmid": None, "pmcid": None, "doi": None, "via": via}
                continue
            recs = self.esummary([pmid])
            if not recs:
                out[item] = {"pmid": None, "pmcid": None, "doi": None, "via": via}
                continue
            r = recs[0]
            # ellenőrzés: a talált rekord valóban a kért azonosítót hordozza (nincs találgatás)
            if (norm_doi(item) and r["doi"] and r["doi"] != norm_doi(item)) or (
                    item.upper().startswith("PMC") and r["pmcid"] != norm_pmcid(item)):
                out[item] = {"pmid": None, "pmcid": None, "doi": None, "via": via}
                continue
            out[item] = {"pmid": r["pmid"], "pmcid": r["pmcid"], "doi": r["doi"], "via": via}
        return out

    # -- állapot -----------------------------------------------------------------------------

    def _probe(self):
        res = self.esearch("%s[pmid]" % PROBE_PMID, retmax=1)
        if res["count"] >= 1 and PROBE_PMID in res["ids"]:
            return self._result("ok", http_status=200)
        return self._result("unreachable", detail_text={
            "hu": "A próbakeresés váratlan eredményt adott.", "en": "The probe search returned an unexpected result."})
