# Glossary

Terms as used in this project's code, docs and viva. Updated as decisions settle.

## Questions and sources

**Run.** One end-to-end execution for one research question. It has a `run_id`.

**RunState.** The Pydantic model holding all of a run's state. It is saved to a JSON **run file** in `runs/` after every step and contains no secrets. See ADR 0008.

**Research question.** The user's free-text question, given at the CLI.

**Intake form.** The structured reading of the research question. The LLM fills it in and code validates it. It has these fields:

- `intervention`
- `outcome`
- `population`
- `sources`
- `recency`

Each field has fixed choices. See ADR 0003.

**Not stated / unclear / other.** The values an intake element can take besides its in-scope value:

- `not_stated`: the question doesn't mention the element. Code fills it in from the tool's scope and shows this.
- `unclear`: the question mentions the element vaguely. It triggers one clarifying question.
- `other`: the element is outside the scope. The question is rejected.

**Spelling correction.** A close misspelling of "metformin" (difflib ratio ≥ 0.85, for example "metaformin") that the user confirmed with "y". It is recorded in `RunState.intake_correction` and in the printed interpretation. See ADR 0003 point 4.

**Scope-filled.** An intake element that code filled in from the tool's scope because it was `not_stated`. Recorded in `RunState.intake_scope_filled`.

**Recent.** Published in the last 5 years before the search date. It applies to the PubMed date window only.

**Scope.** Metformin and cardiovascular outcomes in adults with type 2 diabetes. A question outside the scope is rejected before any search runs.

**Source type.** Where a record came from: `synthetic` (the supplied dataset) or `pubmed` (live search). It is kept on every record from retrieval through to the email.

**Record.** One study abstract and its metadata. A synthetic record is identified by `record_id` (for example SYN001). A PubMed record is identified by PMID and URL.

## Screening

**Screening rules.** The fixed rules from the brief:

- human randomised trial
- published 2015 or later
- more than 50 participants in total
- population, treatment and outcome match the question

**Extracted fact.** A detail the LLM reads from an abstract, together with an evidence quote. Examples: total sample size, group sizes, species, design, and population/intervention/outcome match.

**Evidence quote.** A verbatim span of source text that supports a fact or claim.

**Quote check.** A code check that an evidence quote appears exactly in the record's abstract.

**Number check.** A code check that every number in a fact or claim appears in its source. Matching rules:

- Numbers are matched whole.
- Number words are converted to digits.
- Decimals are kept as one number.
- Ranges are split into two numbers.

See ADR 0010.

**Unknown.** The value a fact gets when it fails the quote check or the number check. An unknown fact can lead to `needs_review`, never to `include`.

**Screening decision.** One of three values:

- `include`
- `exclude`, with every failed rule listed
- `needs_review`, naming the missing or unclear detail

A definite exclusion beats `needs_review`.

**Failed rule.** A screening rule that a record definitely does not meet.

**Metadata-only exclusion.** A record excluded on metadata alone, before any LLM call. The two cases are a publication year before 2015 and a "Clinical Trial Protocol" tag. Shown as "excluded: <rule> (other rules not checked)". See ADR 0004 point 7a.

**Missing detail.** A required detail the abstract does not state clearly.

## Summary and review

**Claim.** One statement in the synthesis. It cites one or more `record_id`s, all with the same `source_type`, and carries a list of evidence quotes. Each cited record has at least one quote of its own.

**Removed claim.** A claim that still fails the content checks after one retry. It is shown as "removed: unsupported" and is never part of the content that can be approved.

**Study details.** Structured details of an included study.

- `not reported`: the abstract does not state this detail.
- `not reported (unverified)`: the detail failed its quote or number check after one retry. It comes with a "removed: unsupported" warning. See ADR 0005.

**Evidence table.** A table built by code from screening decisions and study details. Synthetic and PubMed records appear in separate sections.

The review screen and the email show a compact version:
- included studies, with their key evidence fields and quotes
- needs-review records, one line each
- excluded records, one line each, with the failed rule and its supporting quote

The full per-record evidence is kept in the run file. See ADR 0007.

**Synthesis.** A plain-language summary built by code from validated claims. It covers included studies only.

**Draft.** One version of the Summarizer's structured output, together with its validation results.

**Critic issue.** A problem the Critic finds in a draft: an unfair claim, a contradiction or missing context.

**Pass.** One Summarizer output, either the first draft or a revision.

**Comparison claim.** A claim of kind `comparison` that compares the direction of findings across all included studies from one source. It is required when a source has two or more included studies, and is enforced by code. If it's still missing after the retry, the warning "no cross-study comparison" is shown. See ADR 0005 point 8.

**Revision.** The Summarizer re-runs on the Critic's issues. At most 1 per round.

**Round.** One cycle that ends in human review: the first draft plus its Critic revisions, or one change request plus its Critic revisions. A run has at most 3 rounds.

**Warning.** Something unresolved that is shown at human review. Three kinds:

- a Critic issue still open after the revision limit
- "no cross-study comparison"
- "Critic review unavailable"
- "removed: unsupported" (a claim removed after failing the checks)

**Reviewer notes (not sent).** All warnings for the current draft. They are shown on the review screen after the email and outside it, and kept in the run file. They are never part of the email or its fingerprint. See ADR 0007.

**Limitations section.** A short section in the email, built by code from fixed sentences for three cases only: a source was unavailable, no synthesis could be produced, or there is no cross-study comparison. It is omitted when none of the three applies.

**Change request.** A human instruction to change the summary. It re-enters the Summarizer → content checks → Critic path. At most 2 per run. After the second, only approve or reject are offered.

**Approval record.** A logged decision by the human: `approved`, `approved_with_warnings` or `rejected`. Sending requires an approval record that matches exactly the content being sent.

**Content fingerprint.** A SHA-256 hash of the exact recipient, subject and body, stored in the approval record. The send function refuses to send if the hash does not match the content.

**Delivery status.** The state of the email delivery:

- `not_sent`
- `sending`, written before Gmail is called
- `sent`, together with the Gmail message ID
- `failed`, after a known rejection (400, 401, 403 or 429); nothing was sent
- `unknown`, after any other outcome, which blocks a resend until the human has checked the inbox

See ADR 0013.

## Errors and monitoring

**Temporary error.** A timeout, connection error, 5xx response or per-minute 429. Retried up to 3 times with increasing waits between attempts. See ADR 0013.

**Daily quota exhausted.** The Gemini daily request limit has been reached. The run stops; it is not retried.

**Span.** One timed step in a trace: a run, an agent, a tool call or a retry attempt. See ADR 0014.

**Scenario.** A named evaluation case, used to tag its traces, for example `eval:pubmed-503-persistent`.
