import hashlib
from datetime import date

import httpx
import pytest

from screener.config import load_settings
from screener.dataset import load_synthetic_records
from screener.errors import BadCredentialsError, TransientError
from screener.fakes import fake_pubmed_http
from screener.models import IntakeForm
from screener.retrieval.pubmed import PubMedClient, build_query, date_window, search_pubmed
from screener.retrieval.synthetic import COLLECTION, SyntheticIndex, load_embedder, retrieve_synthetic

SETTINGS = load_settings()
RECORDS = load_synthetic_records(SETTINGS.dataset_path)
QUESTION = "Which synthetic studies qualify under the screening rules?"


class RecordingEmbedder:
    """Records every text it is asked to embed; vectors are a deterministic hash."""

    def __init__(self, name="fake-embedder", dim=8):
        self.name, self.dim = name, dim
        self.calls: list[list[str]] = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[: self.dim]] for t in texts]


# --- Synthetic retrieval (ADR 0011) ---

def test_only_the_abstract_is_embedded_and_metadata_is_payload():
    embedder = RecordingEmbedder()
    index = SyntheticIndex(RECORDS, embedder)

    # The text sent to the embedding model is exactly each abstract, nothing else.
    assert embedder.calls[0] == [r.abstract for r in RECORDS]
    for text, r in zip(embedder.calls[0], RECORDS):
        assert r.title not in text and r.record_id not in text

    # Metadata lives in the Qdrant payload.
    points, _ = index.client.scroll(COLLECTION, limit=100, with_payload=True, with_vectors=False)
    payloads = {p.payload["record_id"]: p.payload for p in points}
    for r in RECORDS:
        assert payloads[r.record_id] == {
            "record_id": r.record_id, "title": r.title, "publication_year": r.publication_year,
            "publication_types": r.publication_types, "source_type": "synthetic", "pmid": None,
            "abstract": r.abstract,
        }


def test_question_is_embedded_with_the_same_model_and_all_records_are_returned():
    embedder = RecordingEmbedder()
    records, search = retrieve_synthetic(QUESTION, settings=SETTINGS, embedder=embedder)
    assert embedder.calls[-1] == [QUESTION]
    assert sorted(r.record_id for r in records) == sorted(r.record_id for r in RECORDS)
    scores = [r.retrieval_score for r in records]
    assert scores == sorted(scores, reverse=True)
    assert (search.collection_size, search.screened, search.embedding_model) == (8, 8, "fake-embedder")


def test_retrieval_depth_is_configurable():
    records, search = retrieve_synthetic(
        QUESTION, settings=load_settings(synthetic_retrieval_depth=3), embedder=RecordingEmbedder())
    assert len(records) == 3 and (search.collection_size, search.screened) == (8, 3)


def test_fallback_model_sets_the_vector_size():
    def loader(name):
        if name == SETTINGS.embedding_model:
            raise OSError("model files missing")
        return RecordingEmbedder(name=name, dim=4)

    embedder = load_embedder(SETTINGS, loader=loader)
    assert embedder.name == SETTINGS.embedding_fallback_model
    index = SyntheticIndex(RECORDS, embedder)
    assert index.client.get_collection(COLLECTION).config.params.vectors.size == 4


# --- PubMed (ADR 0012) ---

FORM = IntakeForm(intervention="metformin", outcome="cardiovascular", population="adults_t2d", sources="pubmed",
                  recency=False, intervention_wording="", outcome_wording="", population_wording="")
SEARCH_DATE = date(2026, 10, 29)


def client(mode="ok", requests=None):
    return PubMedClient(SETTINGS, http=fake_pubmed_http(mode, requests), sleep=lambda s: None)


def test_date_window():
    assert date_window(SEARCH_DATE, False, SETTINGS) == ("2015/01/01", "2026/10/29")
    assert date_window(SEARCH_DATE, True, SETTINGS) == ("2021/10/29", "2026/10/29")
    assert date_window(date(2028, 2, 29), True, SETTINGS) == ("2023/02/28", "2028/02/29")


def test_search_sends_template_and_parses_records():
    sent: list[httpx.Request] = []
    records, search = search_pubmed(FORM, SEARCH_DATE, settings=SETTINGS, client=client(requests=sent))

    esearch = sent[0].url.params
    assert esearch["term"] == build_query()
    assert (esearch["retmax"], esearch["sort"], esearch["datetype"]) == ("10", "relevance", "pdat")
    assert (esearch["mindate"], esearch["maxdate"]) == ("2015/01/01", "2026/10/29")
    assert "Humans" not in esearch["term"] and "Animals" not in esearch["term"]
    assert esearch["term"].startswith("(metformin[Title] OR metformin[MeSH Major Topic])")
    assert esearch["term"].endswith(
        ' NOT (Review[Publication Type] OR "Systematic Review"[Publication Type]'
        ' OR "Meta-Analysis"[Publication Type] OR "Clinical Trial Protocol"[Publication Type])')
    assert sent[1].url.params["id"] == "111,222"  # duplicates removed, order kept

    assert search.retrieved_pmids == ["111", "222"] and search.status == "ok" and search.total_matches == 42
    first, second = records
    assert first.abstract == (
        "METHODS: We randomized 400 adults with type 2 diabetes to metformin or placebo.\n"
        "RESULTS: Events occurred in 20 and 30 participants (HR 0.66, 95% CI 0.38-1.15)."
    )
    assert (first.publication_year, first.url, first.source_type) == (
        2021, "https://pubmed.ncbi.nlm.nih.gov/111/", "pubmed")
    assert "Randomized Controlled Trial" in first.publication_types and "Humans" in first.mesh_terms
    assert second.abstract is None and second.publication_year == 2016


def test_zero_matches_is_a_normal_result_without_efetch():
    sent = []
    records, search = search_pubmed(FORM, SEARCH_DATE, settings=SETTINGS, client=client("no_results", sent))
    assert records == [] and search.status == "no_results" and len(sent) == 1


def test_temporary_failure_is_retried():
    events = []
    records, _ = search_pubmed(FORM, SEARCH_DATE, settings=SETTINGS, client=client("fail_once"),
                               on_error=events.append)
    assert len(records) == 2
    assert [(e.stage, e.error_class) for e in events] == [("retrieval.pubmed.esearch", "transient")]


def test_persistent_failure_raises_after_three_attempts():
    events = []
    with pytest.raises(TransientError):
        search_pubmed(FORM, SEARCH_DATE, settings=SETTINGS, client=client("always"), on_error=events.append)
    assert [e.attempt for e in events] == [1, 2, 3]


def test_bad_api_key_is_a_credentials_error_and_message_hides_the_key(monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "ncbi-secret-123")
    http = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error": "API key invalid"})))
    with pytest.raises(BadCredentialsError) as exc:
        search_pubmed(FORM, SEARCH_DATE, settings=SETTINGS,
                      client=PubMedClient(SETTINGS, http=http, sleep=lambda s: None))
    assert "ncbi-secret-123" not in str(exc.value)


def test_rate_limit_retry_after_header_is_used():
    slept, calls = [], {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="Too Many Requests")
        return httpx.Response(200, json={"esearchresult": {"count": "0", "idlist": []}})

    pc = PubMedClient(SETTINGS, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=slept.append)
    search_pubmed(FORM, SEARCH_DATE, settings=SETTINGS, client=pc)
    assert slept == [2.0]
