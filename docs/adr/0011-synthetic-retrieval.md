# ADR 0011: Synthetic retrieval: PubMedBERT embeddings, in-memory Qdrant, every record screened

- Status: Accepted
- Date: 2026-10-29

## Context

The brief requires RAG over the 8 supplied synthetic abstracts. The dataset's usage note says:

- Embed the abstract text.
- Keep `record_id`, `title` and `publication_year` as metadata.
- Cite synthetic results by `record_id`.

The brief's expected answer to "which synthetic studies qualify?" needs a decision for every record. A record that retrieval silently leaves out would effectively be excluded without any stated rule.

## Decision

**Embeddings**

- **Primary model:** `NeuML/pubmedbert-base-embeddings`, loaded through sentence-transformers. It is a PubMedBERT model fine-tuned for sentence embeddings, with 768 dimensions.
- **Fallback model:** `all-MiniLM-L6-v2`, with 384 dimensions.
- **Matching sizes:** the collection's vector size is taken from whichever model actually loaded, so vectors from the two models are never mixed.
- **Same model for queries:** the query is embedded with the same model.
- **Traceability:** the model used is recorded in the trace.

**Store**

- Qdrant local mode, rebuilt in memory on each run.
- One entry per record.
- Only the abstract text is embedded, following the dataset's usage note exactly.
- Each entry also stores:
  - `record_id`
  - `title`
  - `publication_year`
  - `publication_types`
  - `source_type`
  - `pmid` (null)
  - the full abstract text, which the quote and number checks need

**Retrieval**

- **Depth:** how many records to retrieve is a config setting, set to the collection size. All 8 synthetic records are screened.
- **Role of embeddings:** at this size they rank records by relevance rather than filter them out.
- **Visibility:** similarity scores go into the trace, and the output says "8 of 8 synthetic records screened".
- **Exclusions:** no reranker. PubMed results are never stored in the vector store.

## Why

- **Retrieval depth.** Screening needs to find every relevant record (recall). A retrieval cut-off would act as an exclusion with no failed rule to report, which breaks "state the failed rule".
- **Embedding model.** A biomedical model handles clinical wording better than a general one, and the fallback keeps runs working if it fails to load.
- **Qdrant over FAISS.** Qdrant keeps each record's details (such as `record_id` and `source_type`) next to its vector, so no separate lookup table is needed.
- **Rebuilding every run.** Rebuilding in memory each run removes any chance of using an out-of-date saved index.
- **Keeping PubMed out of the store.** This keeps the two sources separate at storage level (ADR 0005).

## Alternatives considered

- **FAISS:** stores vectors only. It would need a separate table to hold each record's details.
- **MiniLM as the primary model:** smaller and faster, but weaker on biomedical text. It is kept as the fallback.
- **Gemini embeddings:** would use up the 500-requests-per-day quota (ADR 0009).
- **Top-N limit or score threshold now:** would drop records without a stated rule. This becomes the path forward only as the collection grows, and any unscreened records must be reported.

## Consequences

- Needs PyTorch (CPU build) and a one-time download of the model, which is a few hundred MB. Both happen during setup, not during the demo.
- Expected viva question: "why use RAG for 8 records?" Answer: the pipeline is built to scale. Depth is a config setting, and at this size the honest setting is "all".
