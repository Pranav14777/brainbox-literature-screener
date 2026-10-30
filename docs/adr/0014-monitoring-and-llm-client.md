# ADR 0014: Monitoring with LangSmith (or Opik), secret protection, and the LLM client

- Status: Accepted
- Date: 2026-10-29

## Context

The brief asks for traces of both normal and failing runs in LangSmith or Opik. The traces must show:

- agent spans and tool calls
- duration, errors and retries
- approval and delivery status
- tokens, if available

No secrets may be logged.

## Decision

### Tool

- Use LangSmith with the `@traceable` decorator, unless Opik proves faster to set up.
- Make the choice within 15 minutes during setup, and record it in the README.

### Spans

- **Parent span:** one per run.
  - Its input is the user's question, so each trace can be checked against what was asked.
  - Tags: `run_id`, the scenario name (for example `eval:pubmed-503-persistent`), and `mock=true|false`.
- **Child spans:**

| Span | Records |
|---|---|
| `intake` | the form; whether a clarifying question was asked |
| `retrieval.synthetic` | embedding model, number screened, similarity scores |
| `retrieval.pubmed` | query, date window, total matches, PMIDs; **each attempt as its own child span**, so retries are visible |
| `eligibility[record_id]` | decision, failed rules, missing detail, quote/number check failures |
| `summarizer[round, pass]` | claims kept and removed |
| `critic[round, pass]` | verdict and number of issues |
| `human_review[round]` | decision, warnings, content fingerprint |
| `send` | delivery status, Gmail message ID, fingerprint |

- **Every LLM call records:**
  - model and thinking level
  - prompt version
  - input, output and thinking tokens
  - latency
  - cache hit
  - error type
- **Quota, not cost.** Track quota used rather than cost, because the free tier costs nothing.
- **Latency.** Report automated latency separately from time spent waiting for the human.

### Keeping secrets out

1. **By design**
   - Credentials and Gmail service objects are never passed as arguments to traced functions. They are built inside untraced helpers.
   - Secrets live in `.env`. The repo ships a `.env.example`.
   - `.gitignore` covers `.env`, `token.json`, `credentials.json` and `runs/`.
2. **Redaction hook (safety net).** Any input or output key matching `token|secret|key|auth|password` is removed before a trace is sent.
3. **Proof.** After the evaluation runs, export the traces and search them for the API key and refresh-token strings. Zero matches is an evaluation-table row.

### Run files for submission

- `runs/` stays out of git.
- A few clean example run files are copied to `evidence/` for submission.

### LLM client

- Use the official `google-genai` SDK.
- Pydantic models serve as the response schema.
- Token counts come from usage metadata.
- Only our own retry policy (ADR 0013) handles retries. The SDK's built-in retries are disabled, or counted, so attempts don't multiply.

## Why

- **Two complementary records.** Traces show the process (which step ran, how long it took, whether it failed). Run files show the data (ADR 0008).
- **Retries as separate spans.** Giving each retry its own span makes the brief's "retries" requirement visible without extra effort.
- **Three layers of secret protection.** Design prevents leaks, redaction catches mistakes, and the export check proves both worked.

## Alternatives considered

- **Opik self-hosted:** needs Docker Desktop on Windows, which costs too much setup time.
- **LangChain wrappers for tracing:** unnecessary, because the plain Python orchestrator (ADR 0008) can use `@traceable` directly.
