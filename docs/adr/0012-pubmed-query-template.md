# ADR 0012: PubMed query built by code from a fixed template

- Status: Accepted
- Date: 2026-10-29

## Context

Live search uses NCBI's free E-utilities API in two steps:

- **ESearch** takes a query and returns PMIDs.
- **EFetch** takes PMIDs and returns XML with the title, abstract, publication types, MeSH terms and dates.

The question is whether an LLM writes the query or code builds it. ADR 0003 fixes the scope to metformin, cardiovascular outcomes and adults with type 2 diabetes, so the query's subject never changes.

## Decision

**1. Query.** The LLM only classifies the question into the intake form (ADR 0003). Python builds the query from a fixed template:

- **Metformin:** `metformin[Title] OR metformin[MeSH Major Topic]`, so metformin must be the subject of the paper, not background treatment.
- **Type 2 diabetes, cardiovascular outcomes and randomised design:** MeSH terms plus title/abstract keywords, and a publication-type or title/abstract match for randomisation.
- **Excluded publication types:** `NOT (Review[Publication Type] OR "Systematic Review"[Publication Type] OR "Meta-Analysis"[Publication Type] OR "Clinical Trial Protocol"[Publication Type])`, so these don't take top-10 slots from real trials.
- **No Humans/Animals filter**, because recent papers are often not tagged yet. Eligibility catches animal studies instead.
- **Precision at the top over total recall.** Only 10 records are screened, so the query aims to put real metformin trials in those slots. Eligibility still screens every record as a safety net for untagged reviews, protocols and animal studies (ADR 0004).
- **Amended 2026-10-29.** The original broad clause, `"metformin"[MeSH Terms] OR metformin[Title/Abstract]`, put only 1 real metformin trial in each top 10. The other slots went to trials of other drugs where metformin was only background treatment, plus reviews and a trial protocol (see build-notes).

**2. Search and fetch.**

- ESearch is sorted by relevance and limited to 10 records. The limit is a config setting.
- EFetch then gets the abstracts and metadata.

**3. Date window.**

- By default, the window starts at 2015.
- When recency is requested, it starts 5 years before the search date.

**4. Year rule.**

- The year rule uses the journal publication year. The README states this.
- If no year can be parsed, the year is treated as unknown, and the eligibility rules in ADR 0004 apply.

**5. Handling results.**

- Duplicate PMIDs are removed.
- Zero matches is a normal result, not an error.
- A record without an abstract is marked `needs_review`.

**6. NCBI identification.** The `tool` name and contact email are set in config.

**7. Output.** The output states:

- the exact query string, in the run file and the trace only; amended 2026-10-30 to remove it from the email
- the search date
- the date window
- "screened X of N total matches"

**8. Traces.** Traces record the query, total matches, retrieved PMIDs, decisions, errors, retries and timing.

**9. Tests.** ESearch and EFetch are tested, including simulated failures.

## Why

- The scope is fixed, so a query built by code gives the same results every run and can be tested.
- An LLM-written query at temperature 1.0 would change between runs, so evaluation results could not be reproduced.
- This is a clear case of "deterministic because there is nothing left to judge".

## Alternatives considered

- **LLM writes the query and code validates it:** not reproducible, and adds nothing within a fixed scope.
- **Template plus synonyms suggested by an LLM:** the same objection.
- **Filtering out animal studies in the query:** would miss untagged recent papers.

The README names LLM-written queries as the way to support general questions in the future.

## Consequences

- **Trade-offs of the precise query** (also stated in the README):
  - A new metformin trial can be missed if metformin is not in its title and PubMed has not yet indexed metformin as a major topic.
  - A trial wrongly tagged as a review, meta-analysis or protocol is dropped by the query.

- Each run makes 2 NCBI requests, which is well within the 3 requests/second limit for use without an API key. An NCBI API key is optional.
- With 10 records, a PubMed run adds up to 10 eligibility extraction calls, or 20 with retries (ADR 0009).
