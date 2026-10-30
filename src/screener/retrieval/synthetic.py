"""Synthetic retrieval (ADR 0011).

- Only the abstract text is embedded, following the dataset's usage note.
  record_id, title, publication_year, publication_types, source_type, pmid and
  the full abstract are kept as Qdrant payload, never embedded.
- The collection is rebuilt in memory on every run; its vector size comes from
  whichever embedding model actually loaded, so vectors never mix.
- Retrieval depth defaults to the collection size: every record is screened and
  embeddings only rank them. A cut-off would exclude records with no stated rule.
"""
from __future__ import annotations

from typing import Callable, Protocol

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from screener.config import Settings
from screener.dataset import load_synthetic_records
from screener.models import Record, SyntheticSearch
from screener.tracing import add_metadata, traced

COLLECTION = "synthetic_abstracts"
PAYLOAD_FIELDS = ("record_id", "title", "publication_year", "publication_types", "source_type", "pmid", "abstract")


class Embedder(Protocol):
    name: str
    dim: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class SentenceEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # heavy import, only when used

        self.name = model_name
        self._model = SentenceTransformer(model_name, device="cpu")
        self.dim = self._model.get_embedding_dimension()

    def encode(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()


def load_embedder(settings: Settings, loader: Callable[[str], Embedder] = SentenceEmbedder) -> Embedder:
    """PubMedBERT first; MiniLM if it fails to load. The model used is recorded in the trace."""
    try:
        return loader(settings.embedding_model)
    except Exception as e:  # any load failure (missing cache, corrupt files) falls back
        add_metadata(embedding_fallback_reason=f"{type(e).__name__}: {e}")
        return loader(settings.embedding_fallback_model)


class SyntheticIndex:
    def __init__(self, records: list[Record], embedder: Embedder):
        self.embedder = embedder
        self.size = len(records)
        self.client = QdrantClient(location=":memory:")
        self.client.create_collection(
            COLLECTION, vectors_config=VectorParams(size=embedder.dim, distance=Distance.COSINE)
        )
        vectors = embedder.encode([r.abstract or "" for r in records])  # abstract only
        self.client.upsert(
            COLLECTION,
            points=[
                PointStruct(id=i, vector=v, payload=r.model_dump(include=set(PAYLOAD_FIELDS)))
                for i, (r, v) in enumerate(zip(records, vectors))
            ],
        )

    def search(self, question: str, depth: int | None = None) -> list[Record]:
        limit = min(depth or self.size, self.size)
        hits = self.client.query_points(
            COLLECTION, query=self.embedder.encode([question])[0], limit=limit, with_payload=True
        ).points
        return [Record(**h.payload, retrieval_score=h.score) for h in hits]


@traced("retrieval.synthetic", run_type="retriever")
def retrieve_synthetic(
    question: str, *, settings: Settings, embedder: Embedder | None = None
) -> tuple[list[Record], SyntheticSearch]:
    embedder = embedder or load_embedder(settings)
    index = SyntheticIndex(load_synthetic_records(settings.dataset_path), embedder)
    records = index.search(question, settings.synthetic_retrieval_depth)
    add_metadata(
        embedding_model=embedder.name, embedding_dim=embedder.dim,
        scores={r.record_id: round(r.retrieval_score or 0.0, 4) for r in records},
    )
    return records, SyntheticSearch(embedding_model=embedder.name, collection_size=index.size, screened=len(records))
