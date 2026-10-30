"""Scripted fakes for tests and mocked evaluation scenarios (ADR 0015).

Fakes replace only the network call. Retries, throttling, validation and
routing still run for real, so a mocked scenario exercises the production path.
"""
from __future__ import annotations

import json
import threading
from typing import Callable, Literal

import httpx
from pydantic import BaseModel

from screener.errors import QuotaExhaustedError, TransientError
from screener.llm import RawResponse

FailureMode = Literal["fail_once", "always", "quota"] | None


class FakeLLMTransport:
    """Stand-in for the Gemini transport.

    responses maps a schema class to the outputs to return in order; the last
    one repeats. An output is a JSON string or a Pydantic instance. Instead of a
    list, a callable(user_message) can pick the output, which keeps concurrent
    calls (eligibility) deterministic.
    failure: "fail_once" raises one transient error then behaves normally,
    "always" raises a transient error on every call, "quota" raises daily-quota
    exhaustion.
    """

    def __init__(
        self,
        responses: dict[type, list[str | BaseModel] | Callable[[str], str | BaseModel]] | None = None,
        failure: FailureMode = None,
    ):
        self.responses = {k: v if callable(v) else list(v) for k, v in (responses or {}).items()}
        self.failure = failure
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, *, model, system, user, schema, thinking_level) -> RawResponse:
        with self._lock:
            self.calls.append(
                {"model": model, "system": system, "user": user, "schema": schema.__name__,
                 "thinking_level": thinking_level}
            )
            first_call = len(self.calls) == 1
        if self.failure == "always" or (self.failure == "fail_once" and first_call):
            raise TransientError("fake: HTTP 503: server error")
        if self.failure == "quota":
            raise QuotaExhaustedError("fake: HTTP 429: daily quota exhausted")
        source = self.responses.get(schema)
        if not source:
            raise AssertionError(f"no fake response scripted for {schema.__name__}")
        if callable(source):
            out = source(user)
        else:
            with self._lock:
                out = source.pop(0) if len(source) > 1 else source[0]
        text = out.model_dump_json() if isinstance(out, BaseModel) else out
        return RawResponse(text=text, input_tokens=10, output_tokens=5)


# --- Embeddings (mocked runs need no model download) ---

class FakeEmbedder:
    """Deterministic hash-based vectors. Retrieval still screens every record (ADR 0011)."""

    name = "fake-embedder"
    dim = 8

    def encode(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[: self.dim]] for t in texts]


# --- Gmail (ADR 0015 rows 11-13) ---

GmailMode = Literal["ok", "timeout", "rejected", "server_error"]


class FakeGmailSender:
    """Records every message it is asked to send.

    "timeout" raises TimeoutError (outcome unknown), "rejected" raises a known
    rejection (nothing sent), "server_error" raises a generic error (unknown).
    """

    def __init__(self, mode: GmailMode = "ok"):
        self.mode = mode
        self.sent: list[bytes] = []

    def send(self, raw_message: bytes) -> str:
        from screener.delivery import GmailRejected

        self.sent.append(raw_message)
        if self.mode == "timeout":
            raise TimeoutError("fake: Gmail timed out")
        if self.mode == "rejected":
            raise GmailRejected("fake: Gmail rejected the message (HTTP 400)")
        if self.mode == "server_error":
            raise RuntimeError("fake: HTTP 503")
        return f"fake-message-{len(self.sent)}"


# --- PubMed (ADR 0015 rows 7-9) ---

FAKE_EFETCH_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle><MedlineCitation><PMID>111</PMID><Article>
    <Journal><JournalIssue><PubDate><Year>2021</Year><Month>Mar</Month></PubDate></JournalIssue></Journal>
    <ArticleTitle>Metformin and cardiovascular events: a randomized trial</ArticleTitle>
    <Abstract>
      <AbstractText Label="METHODS">We randomized 400 adults with type 2 diabetes to metformin or placebo.</AbstractText>
      <AbstractText Label="RESULTS">Events occurred in 20 and 30 participants (HR 0.66, 95% CI 0.38-1.15).</AbstractText>
    </Abstract>
    <PublicationTypeList><PublicationType>Journal Article</PublicationType>
      <PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
  </Article>
  <MeshHeadingList><MeshHeading><DescriptorName>Humans</DescriptorName></MeshHeading>
    <MeshHeading><DescriptorName>Metformin</DescriptorName></MeshHeading></MeshHeadingList>
  </MedlineCitation></PubmedArticle>
  <PubmedArticle><MedlineCitation><PMID>222</PMID><Article>
    <Journal><JournalIssue><PubDate><MedlineDate>2016 Dec-2017 Jan</MedlineDate></PubDate></JournalIssue></Journal>
    <ArticleTitle>A trial without an abstract</ArticleTitle>
    <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
  </Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""

PubMedMode = Literal["ok", "no_results", "fail_once", "always"]


def fake_pubmed_http(mode: PubMedMode = "ok", requests: list[httpx.Request] | None = None) -> httpx.Client:
    """An httpx client whose transport answers like NCBI E-utilities.

    "fail_once" returns one HTTP 503 then answers normally; "always" returns 503 every time.
    Pass a list as `requests` to record what was sent.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if requests is not None:
            requests.append(request)
        if mode == "always" or (mode == "fail_once" and calls["n"] == 1):
            return httpx.Response(503, text="Service Unavailable")
        if request.url.path.endswith("esearch.fcgi"):
            ids = [] if mode == "no_results" else ["111", "222", "111"]  # duplicate on purpose
            body = {"esearchresult": {"count": str(len(set(ids)) * 21), "idlist": ids}}
            return httpx.Response(200, text=json.dumps(body))
        return httpx.Response(200, content=FAKE_EFETCH_XML.encode("utf-8"))

    return httpx.Client(transport=httpx.MockTransport(handler))
