# ADR 0009: Gemini 3.5 Flash-Lite for every agent, in development and in the demo

- Status: Accepted
- Date: 2026-10-29

## Context

Only free tiers may be used. One synthetic-only run makes roughly 11 to 15 LLM calls. A PubMed run adds about one call per fetched record. Development needs many runs per day.

Free-tier limits, as checked in AI Studio on 2026-10-29:

| Option | Limits | Verdict |
|---|---|---|
| Gemini 3.8 Flash | 5 RPM, 20 RPD | Too few requests for development |
| Gemini 3.5 Flash-Lite | 15 RPM, 500 RPD | Chosen |
| Qwen via Groq | 200K tokens/day | Rejected: only about 5 full runs at medium reasoning |

## Decision

**Model**
- Every agent uses Gemini 3.5 Flash-Lite, in development and in the demo, so the demo runs exactly what was tested.

**Settings**
- The model name and thinking level live in config.
- The thinking level defaults to `low` for every agent. If the weak-draft test (ADR 0015, row 6) shows the Critic missing the planted overclaim, raise the Critic alone to `medium` and log the change in build-notes.
- **Applied 2026-10-30:** the Critic now runs at `medium` (`critic_thinking_level`). All other agents stay at `low`. The trigger was a real draft rather than a planted one: run 20261030-041459-7c08 reported other drugs' results for GRADE and a blinded pooled rate for SMARTEST, and the Critic approved it. That draft is now the row 6 fixture.
- Temperature stays at the default of 1.0, as Google strongly recommends for Gemini 3 models.

**Quota protection**
- A disk cache, used in development only:
  - keyed by a hash of the prompt, model and settings
  - errors are never cached
  - switched off for the evaluation and the demo
- A limit of 10 PubMed records per run (ADR 0012).
- Requests are throttled to stay under 15 RPM, with backoff on per-minute HTTP 429. When the daily quota is used up, the run stops instead of retrying (ADR 0013).
- Eligibility calls run concurrently, up to 8 at a time. The throttle spaces when requests start, not how many are in flight, so concurrency never exceeds 15 RPM. This was decided because free-tier responses took 12–44s per call (see build-notes).
- The throttle, the request counter, the LLM cache and run-file writes are thread-safe, and a test covers each.
- Thinking level stays `low`. `minimal` was no faster, and thinking tokens were 0 at both levels.

## Why

Benchmarks for this model on Vals AI (https://vals-ai.com/models/google_gemini-3.5-flash-lite):

| Benchmark | Score |
|---|---|
| MedScribe | 70.89% (ranked 93 of 109) |
| MedCode | 43.49% |
| GPQA Diamond | 83.84% |
| MMLU-Pro | 85.84% |

Its medical scores are moderate, and no benchmark measures clinical screening. The model is therefore evaluated directly on the 8 synthetic abstracts, measuring:

- eligibility accuracy
- extraction correctness
- citation validity

The design makes up for a weaker model:

- Code makes the decisions (ADR 0004).
- Extracted facts are checked against quotes (ADR 0010).
- Outputs are checked against schemas.
- Mistakes fall back to `needs_review`, never to a wrong `include`.

## Consequences

- Worst-case calls per run, for the synthetic source:
  - intake: about 2, including a clarification
  - eligibility: up to 14 (7 records, each with one retry; SYN004 is excluded on metadata, with no call)
  - Summarizer: up to 12 (ADR 0006)
  - Critic: up to 12 (ADR 0006)
  - Total: about 40 calls, so roughly 12 worst-case runs per day fit within 500 RPD.
  - A PubMed run adds up to 20 eligibility calls (10 records, each with one retry).
  - Typical runs are much smaller.
- Temperature 1.0 means extraction can differ between runs. The rules in code are deterministic, but the facts they receive may not be. The evaluation therefore runs eligibility on the 8 synthetic records 3 times with the cache off, and measures two things:
  - **Consistency:** how often decisions agree across the 3 runs.
  - **Accuracy:** agreement with the brief's expected answers.
    - Include: SYN001, SYN002, SYN008.
    - Exclude: SYN004, SYN005, SYN006, SYN007.
    - Needs review: SYN003.
- The README and the evaluation table state that 24 decisions is an indicative check, not a statistically strong one.
- README notes:
  - On the free tier, Google may use inputs. This is acceptable because all inputs are synthetic or public.
  - The Summarizer and Critic share one model and may share blind spots. The code checks reduce this risk.
