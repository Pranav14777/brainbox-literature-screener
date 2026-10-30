# ADR 0013: Error classes, retries, exactly-once delivery and prompt-injection defences

- Status: Accepted
- Date: 2026-10-29

## Context

The brief requires several things:

- timeouts, and bounded retries with backoff for temporary failures
- telling temporary failures apart from invalid requests, bad credentials and empty results
- no duplicate emails, and no blind resend when the send result is uncertain
- retained delivery status
- treating retrieved text as evidence, not instructions

The Gmail API cannot make a repeated send safe on its own. The `gmail.send` scope cannot read the Sent folder, so the code cannot check whether a send went through.

## Decision

### Error classes

| Class | Examples | Response |
|---|---|---|
| Temporary | timeout, connection error, HTTP 5xx, per-minute HTTP 429 | Retry up to 3 attempts with exponential backoff and jitter (1s, 2s, 4s). Honour `Retry-After`, capped at 60s. |
| Invalid request | HTTP 400, malformed input | No retry. Fail with a clear message. |
| Bad credentials | 401/403, Gmail `invalid_grant`, missing key | No retry. Message tells the user to re-run OAuth or fix the key. Never print the token. |
| Daily quota exhausted (Gemini) | 429 that identifies the daily quota | No retry. The run stops with "daily quota exhausted". |
| Empty results | zero matches | Not an error. Reported in the output. |
| Invalid LLM output (schema) | Pydantic failure | One retry, then the safe default (ADR 0002). Counted separately from temporary retries. |

### Timeouts

| Service | Timeout |
|---|---|
| PubMed | about 15 s |
| Gemini | 90 s (raised from 60 s: normal free-tier calls took up to 44 s, which left too little margin and would trigger needless retries) |
| Gmail | 30 s |

### When a dependency is still failing after retries

- **Gemini during eligibility:** each record is handled independently. A record whose call is still failing after its retries becomes `needs_review` ("Gemini unavailable after retries"), and the other records continue. If every record fails this way, the run stops with a clear message, and nothing is sent.
- **Gemini at any other step, daily quota exhausted, or bad credentials:** the run stops with a clear message, and nothing is sent.
- **The only source requested:** the run stops, and nothing is sent.
- **One of two sources requested:** the run continues with the other source. A prominent "PubMed unavailable" warning is shown at human review.

### Exactly-once delivery

**Fingerprint check**
- The approval record stores a SHA-256 fingerprint of the exact recipient, subject and body.
- The send function recomputes the fingerprint and refuses to send if it does not match.

**Delivery status**
- Delivery status is one of: `not_sent`, `sending`, `sent` (with the Gmail message ID), `failed`, `unknown`.
- `sending` is written to the run file **before** Gmail is called.

**Interpreting the result**
- Only known rejection codes (400, 401, 403, 429) count as `failed`, meaning nothing was sent. After fixing the cause, the human can trigger a manual retry of the same approved fingerprint.
- Any other response, a timeout or a dropped connection counts as `unknown`.
- A run file found still marked `sending` (a crash during the send) also counts as `unknown`.

**Retries**
- There are no automatic retries of the send.
- `unknown` blocks resending until the human has checked their inbox for the run ID.

**Run ID placement**
- The run ID appears in the default subject.
- The run ID **also** appears in a footer that code generates in the body. The human can edit the subject, so the subject alone is not reliable. The summary text cannot be edited, so the footer cannot be removed (ADR 0007).

**Recipient validation**
- Code checks that the recipient is a single, correctly formatted email address before review and after any edit.

### Prompt injection (retrieved text is evidence, not instructions)

- Each abstract goes into prompts inside a clearly marked data block, with the instruction that its content is data, never instructions.
- Structural defences:
  - No LLM agent has any tools.
  - Code makes the eligibility decisions.
  - Code controls the recipient.
  - The send function sits behind code and human approval.
- The email is sent as plain text, so links or HTML from an abstract are never rendered.
- A test-only abstract containing an injection attempt, kept separate from the dataset, is part of the evaluation.

## Why

- Retrying only temporary failures avoids hammering an API over a request that will never succeed. Distinct messages tell the user what to fix.
- A daily quota resets only the next day, so retrying it would waste time.
- Because the API cannot make a repeated send safe and the scope cannot read Sent, `unknown` must be a final state that a human resolves. Treating only known rejections as `failed` errs toward "possibly sent", which blocks duplicates.

## Alternatives considered

- **Adding `gmail.readonly` to check Sent automatically:** rejected. The brief specifies `gmail.send`, and a wider scope than needed is a security cost.
- **Automatic retry of sends after 5xx or timeout:** rejected. It risks a duplicate email.

## Limitations (stated in the README)

- The data block and structural defences reduce prompt-injection risk, but cannot guarantee that the LLM never misreads malicious text. This is why decisions, recipients and sending are controlled by code and human approval, not by the LLM.
- The quote check proves that text exists in the abstract, not that it is true.
- Refresh tokens for OAuth apps in "Testing" mode expire after 7 days. The current token covers the demo, but it must be recreated for later use.
