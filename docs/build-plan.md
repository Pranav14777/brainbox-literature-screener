# Build plan

The plan has a 1-hour prep phase, then about 3 hours of build, then evaluation and the README.

Rule for every block: test it before starting the next one. The core logic (number matching, eligibility rules, the send gate) is tested offline, without any LLM, before the LLM is wired in.

## Prep (about 1 hour)

| # | Task | Who |
|---|---|---|
| 1 | `git init`. Add `runs/`, `.llm_cache/` and Python caches to `.gitignore` (it already has `credentials.json`, `token.json`, `.env`, `.venv`). `evidence/` stays tracked (ADR 0014). | Claude |
| 2 | Venv with Python 3.12 (created by the user). Install CPU-only PyTorch **first** to avoid the large CUDA download. Then install `google-genai`, `pydantic`, `qdrant-client`, `sentence-transformers`, `httpx`, `google-api-python-client`, `google-auth-oauthlib`, `langsmith`, `python-dotenv`, `pytest`. | Claude, with your approval |
| 3 | Download both embedding models in advance (PubMedBERT and MiniLM). | Claude |
| 4 | Fill in `.env` from `.env.example`: `GEMINI_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `NCBI_EMAIL`, `RECIPIENT_EMAIL`. | You (Claude never reads `.env`) |
| 5 | Smoke checks: one Gemini call that returns structured output, one PubMed ESearch, a Gmail token refresh **without sending**, and one LangSmith trace appearing. Give Opik 15 minutes only if LangSmith is awkward. | Both |

## Build (about 3 hours, in this order)

| # | Block | ADRs | Done when |
|---|---|---|---|
| 1 | `models.py` (RunState and all schemas), `config.py`, saving the run file | 0008 | RunState saves and reloads |
| 2 | `matching.py` (quote and number checks) and `rules.py` (rule engine) | 0004, 0010 | Unit tests pass on the 8 synthetic records using hand-written facts, without an LLM. SYN006 edge case, "Six", "160", decimals and ranges all covered. |
| 3 | `errors.py` (error classes, retry with backoff) and `llm.py` (google-genai, Pydantic schema, throttle, dev cache, token counts, tracing) | 0009, 0013, 0014 | One traced structured call. Retries tested with mocks. |
| 4 | Intake agent and Eligibility agent, plus prompts in `prompts/` | 0003, 0004 | Live run on the 8 synthetic records matches the brief's expected answers |
| 5 | `retrieval/synthetic.py` (Qdrant + embeddings) and `retrieval/pubmed.py` (query template, ESearch, EFetch) | 0011, 0012 | "8 of 8 screened". A live PubMed query returns parsed records. Simulated failures tested. |
| 6 | Summarizer, content checks, Critic loop, `render.py` (evidence table, summary text, email body) | 0005, 0006 | Q1 answer has the right counts and nothing pooled. A bad claim gets removed. Loop caps hold. |
| 7 | `review.py` (CLI human review) and `delivery.py` (fingerprint, delivery status, Gmail send) | 0007, 0013 | Reject sends nothing. Approve sends once. `unknown` blocks a resend. Tested with a mocked Gmail. |
| 8 | `orchestrator.py` and `cli.py` wiring everything together | 0002 | Full end-to-end run, plus **one real approved send to yourself** |

## After the build (about 2 hours, still required)

- **Evaluation runs**: the 16 scenarios in ADR 0015.
  - Failure scenarios use fake LLM, PubMed and Gmail clients, built alongside blocks 3, 5 and 7.
  - Live PubMed and the real Gmail send are never mocked.
- **Build notes**: add one line to `docs/build-notes.md` for every real bug or surprise, logged as it happens.
- **Evaluation table** with columns: scenario, expected behaviour, actual result, lesson learned.
- **Trace screenshots** and exports, plus clean example run files copied to `evidence/`.
- **README**, covering: setup, architecture, rules, design rationale (from the ADRs), limitations, one improvement made after testing, and disclosure of AI assistance.

## Planned layout

```
src/screener/
  config.py  models.py  errors.py  llm.py  tracing.py
  matching.py  rules.py  render.py  review.py  delivery.py
  orchestrator.py  cli.py
  agents/      intake.py  eligibility.py  summarizer.py  critic.py
  retrieval/   synthetic.py  pubmed.py
prompts/       intake.md  eligibility.md  summarizer.md  critic.md
tests/         test_matching.py  test_rules.py  test_errors.py
               test_pubmed.py  test_delivery.py  test_orchestrator.py
docs/          adr/  glossary.md  build-plan.md
runs/          (git-ignored)
evidence/      (clean example run files for submission)
```
