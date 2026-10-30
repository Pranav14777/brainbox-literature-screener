"""Live PubMed search through NCBI E-utilities (ADR 0012).

The query is a fixed template built by code: the scope is fixed (ADR 0003), so
the same question always sends the same query, which keeps evaluation runs
reproducible. Only the top 10 by relevance are screened, so the query favours
precision at the top: metformin must be in the title or a MeSH major topic, and
reviews, meta-analyses and trial protocols are excluded by publication type.
Eligibility still screens every record as a safety net for untagged ones. There
is deliberately no Humans/Animals filter, because recent papers are often not
yet MeSH-indexed.

The API key goes only into request parameters, never into span inputs or error
messages (ADR 0014).
"""
from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import Callable

import httpx

from screener.config import Settings
from screener.errors import InvalidRequestError, ScreenerError, TransientError, classify_http, with_retries
from screener.models import ErrorEvent, IntakeForm, PubmedSearch, Record
from screener.tracing import add_metadata, traced

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PUBMED_URL = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

QUERY_TEMPLATE = (
    "(metformin[Title] OR metformin[MeSH Major Topic])"
    ' AND ("diabetes mellitus, type 2"[MeSH Terms] OR "type 2 diabetes"[Title/Abstract]'
    ' OR "type 2 diabetic"[Title/Abstract] OR T2DM[Title/Abstract])'
    ' AND ("cardiovascular diseases"[MeSH Terms] OR cardiovascular[Title/Abstract]'
    ' OR "myocardial infarction"[Title/Abstract] OR stroke[Title/Abstract] OR "heart failure"[Title/Abstract])'
    ' AND ("randomized controlled trial"[Publication Type] OR randomized[Title/Abstract]'
    ' OR randomised[Title/Abstract])'
    ' NOT (Review[Publication Type] OR "Systematic Review"[Publication Type]'
    ' OR "Meta-Analysis"[Publication Type] OR "Clinical Trial Protocol"[Publication Type])'
)


def build_query() -> str:
    return QUERY_TEMPLATE


def date_window(search_date: date, recency: bool, settings: Settings) -> tuple[str, str]:
    """Publication-date window: from 2015, or from 5 years before the search date when recency is requested."""
    if recency:
        try:
            start = search_date.replace(year=search_date.year - settings.recency_years)
        except ValueError:  # 29 February
            start = search_date.replace(year=search_date.year - settings.recency_years, day=28)
        start = max(start, date(settings.min_publication_year, 1, 1))
    else:
        start = date(settings.min_publication_year, 1, 1)
    return start.strftime("%Y/%m/%d"), search_date.strftime("%Y/%m/%d")


class PubMedClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._http = http or httpx.Client(timeout=settings.pubmed_timeout_s)
        self._sleep = sleep

    def _params(self, **extra) -> dict:
        params = {"db": "pubmed", "tool": self.settings.ncbi_tool, "email": self.settings.ncbi_email, **extra}
        if key := os.environ.get("NCBI_API_KEY"):
            params["api_key"] = key
        return params

    def _get(self, endpoint: str, params: dict) -> httpx.Response:
        try:
            resp = self._http.get(f"{EUTILS}/{endpoint}", params=params)
        except httpx.TimeoutException:
            raise TransientError(f"PubMed {endpoint}: timed out") from None
        except httpx.TransportError as e:
            raise TransientError(f"PubMed {endpoint}: connection error ({type(e).__name__})") from None
        if resp.status_code != 200:
            retry_after = resp.headers.get("Retry-After")
            raise classify_http(
                resp.status_code, resp.text[:500],
                float(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        return resp

    def esearch(self, query: str, min_date: str, max_date: str) -> tuple[int, list[str]]:
        resp = self._get("esearch.fcgi", self._params(
            term=query, retmax=self.settings.pubmed_max_records, sort="relevance",
            datetype="pdat", mindate=min_date, maxdate=max_date, retmode="json",
        ))
        try:
            body = resp.json()
        except ValueError:
            raise TransientError("PubMed esearch: response was not JSON") from None
        if "error" in body:  # NCBI reports some errors with HTTP 200
            raise _classify_body_error(str(body["error"]))
        result = body.get("esearchresult", {})
        if "ERROR" in result:
            raise InvalidRequestError("PubMed esearch: query rejected")
        pmids = list(dict.fromkeys(result.get("idlist", [])))  # dedupe, keep relevance order
        return int(result.get("count", 0)), pmids

    def efetch(self, pmids: list[str]) -> list[Record]:
        resp = self._get("efetch.fcgi", self._params(id=",".join(pmids), retmode="xml", rettype="abstract"))
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            raise TransientError("PubMed efetch: response was not valid XML") from None
        return [parse_article(a) for a in root.iter("PubmedArticle")]


def _classify_body_error(message: str) -> ScreenerError:
    if re.search(r"rate limit", message, re.IGNORECASE):
        return TransientError("PubMed: rate limited")
    return classify_http(400, message)


def _text(el: ET.Element | None) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def parse_article(article: ET.Element) -> Record:
    citation = article.find("MedlineCitation")
    pmid = _text(citation.find("PMID"))
    art = citation.find("Article")
    sections = []
    for part in art.findall("Abstract/AbstractText"):
        text = _text(part)
        label = part.get("Label")
        sections.append(f"{label}: {text}" if label and text else text)
    abstract = "\n".join(s for s in sections if s) or None
    return Record(
        record_id=pmid,
        source_type="pubmed",
        title=_text(art.find("ArticleTitle")),
        abstract=abstract,
        publication_year=_journal_year(art),
        publication_types=[_text(t) for t in art.findall("PublicationTypeList/PublicationType")],
        mesh_terms=[_text(d) for d in citation.findall("MeshHeadingList/MeshHeading/DescriptorName")],
        pmid=pmid,
        url=PUBMED_URL.format(pmid=pmid),
    )


def _journal_year(art: ET.Element) -> int | None:
    """Journal publication year (ADR 0012): PubDate/Year, else the first year in MedlineDate."""
    pub_date = art.find("Journal/JournalIssue/PubDate")
    if pub_date is None:
        return None
    year = _text(pub_date.find("Year"))
    if not year:
        m = re.search(r"\b(\d{4})\b", _text(pub_date.find("MedlineDate")))
        year = m.group(1) if m else ""
    return int(year) if year.isdigit() else None


@traced("retrieval.pubmed", run_type="retriever")
def search_pubmed(
    form: IntakeForm,
    search_date: date,
    *,
    settings: Settings,
    client: PubMedClient | None = None,
    on_error: Callable[[ErrorEvent], None] | None = None,
) -> tuple[list[Record], PubmedSearch]:
    """Search, then fetch abstracts. Zero matches is a normal result. Persistent failures raise."""
    client = client or PubMedClient(settings)
    query = build_query()
    min_date, max_date = date_window(search_date, form.recency, settings)
    total, pmids = with_retries(
        lambda: client.esearch(query, min_date, max_date),
        stage="retrieval.pubmed.esearch", settings=settings, on_error=on_error, sleep=client._sleep,
    )
    search = PubmedSearch(query=query, min_date=min_date, max_date=max_date, total_matches=total,
                          retrieved_pmids=pmids, status="ok" if pmids else "no_results")
    records: list[Record] = []
    if pmids:
        records = with_retries(
            lambda: client.efetch(pmids),
            stage="retrieval.pubmed.efetch", settings=settings, on_error=on_error, sleep=client._sleep,
        )
    add_metadata(query=query, min_date=min_date, max_date=max_date, total_matches=total, pmids=pmids,
                 without_abstract=[r.record_id for r in records if r.abstract is None])
    return records, search
