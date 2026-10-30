# ADR 0015: Evaluation scenarios, failure mocking and build notes

- Status: Accepted
- Date: 2026-10-29

## Context

The brief asks for an evaluation table with the columns **scenario, expected behaviour, actual result, lesson learned**. It must test:

- a normal run
- an ambiguous question and a weak draft
- no results
- temporary and persistent API failure
- approval and rejection

Rejection must send nothing, and approval must deliver exactly once. Failures are mocked. Live search and Gmail delivery must be demonstrated for real.

## Decision

### Scenarios

| # | Scenario | Expected behaviour | Mode |
|---|---|---|---|
| 1 | Normal synthetic run (brief Q1/Q2) | SYN001, SYN002 and SYN008 included. SYN003 needs review (sample size missing). SYN004–SYN007 excluded, each with its failed rule. Summary gives raw counts, does not pool and does not claim benefit. | Live LLM |
| 2 | Normal live PubMed run (brief Q3) | Real titles, dates, PMIDs and URLs. The output states the query, search date, date window and "screened X of N". If no record qualifies, the output explains why. | Live PubMed |
| 3 | Ambiguous question | One clarifying question. The run stops if the question is still unclear. | Live LLM |
| 4 | Out-of-scope question (another drug) | Rejected with a scope message before any search runs. | Live LLM |
| 5 | Rewording (for example "the supplied abstracts") | Routed to the same source as the plain wording (ADR 0003). | Live LLM |
| 6 | Weak draft | An overclaiming draft is given to the Critic. The Critic returns `revise` with issues. | Live LLM, crafted draft |
| 7 | No results | Zero matches is reported as a normal result, with no invented papers. | Fake PubMed returning 0 hits |
| 8 | Temporary PubMed failure | Fails once, then succeeds. The retry is visible as its own span, and the run completes. | Fake: fail once |
| 9 | Persistent PubMed failure | A question asking for both sources continues on synthetic records only, with a "PubMed unavailable" warning. A question asking for PubMed only stops, and nothing is sent. | Fake: always fail |
| 10 | Gemini failure | After retries, the run stops with a clear message and nothing is sent. Daily-quota exhaustion stops at once, with no retries. | Fake LLM client |
| 11 | Approval (sends once) | Status `sent`, with a Gmail message ID. Triggering the send again for the same run is blocked. | **Real Gmail** |
| 12 | Rejection (sends nothing) | Status `not_sent`. No send span appears in the trace. | Real run, rejected |
| 13 | Uncertain Gmail send | A timeout leaves status `unknown`. Resending is blocked, and the user is told to check the inbox for the run ID. | Fake Gmail: timeout |
| 14 | Prompt-injection abstract | The record is screened by the normal rules. Its instructions have no effect: decisions, recipient and actions are unchanged. | Test-only fixture dataset |
| 15 | Eligibility consistency and accuracy | Run 3 times with the cache off. Report agreement across runs, and accuracy against the brief's expected answers. 24 decisions are indicative only. | Live LLM ×3 |
| 16 | Secrets absent from traces | Searching the exported traces for the API key and refresh-token strings finds zero matches (ADR 0014). | Trace export |

The scenario list is a minimum. Rows 5 and 16 carry over commitments from ADR 0003 and ADR 0014.

**As run (2026-10-30):** the final scenario list, results and LangSmith tags are in `docs/evaluation.md`, produced by `scripts/run_evaluation.py`. Changes from the list above:
- added: the typo check, the Gemini daily quota as its own row, and the real Gmail send as row 13b
- the prompt-injection row is split into a mocked worst case (16a, the model obeys) and one live eligibility call with real Gemini (16b)
- the persistent PubMed failure is split into PubMed-only and both-sources rows
- the rewording row is covered by the brief's Q1 and Q2 (scope-filled), which run in the live and mocked rows

### Weak-draft fixture

**The fixture is a real draft.** Decided 2026-10-30. It comes from run 20261030-041459-7c08 (recent PubMed question), saved as `evidence/row06-weak-draft-20261030-041459-7c08.json`. The draft passed every content check, but:
- for GRADE, it reported hazard ratios for drugs other than metformin
- for SMARTEST, it reported a blinded pooled event rate as if it were a comparison

The Critic (critic-v3, `low` thinking) approved it. `scripts/run_evaluation.py --only weak-draft` gives the Critic exactly this draft again, with the same abstracts, screening results and code findings. The expected verdict is `revise`.

The original plan, kept as a description of what a valid fixture must look like:

- The deliberately overclaiming draft must **pass the content checks**: real quotes, real numbers and only included records.
- Its weakness is in the judgement, for example "metformin proves cardiovascular benefit", or treating SYN001's composite outcome as the same as SYN008's.
- The Critic only sees drafts that have passed those checks (ADR 0006). A draft with invented numbers would be removed by code before the Critic ever sees it, so the test would not measure the Critic at all.

### Failure mocking

- Fake clients (LLM, PubMed, Gmail) are passed into the orchestrator in place of the real ones.
- Each fake can be set to **fail once, then succeed** (temporary failure) or **always fail** (persistent failure). They can also time out or return empty results.
- Production code never checks whether it is running under a test.
- Mocked runs are tagged `mock=true` in traces (ADR 0014).

### Live services

- Live PubMed and Gmail are never mocked in the demo runs.
- Row 2 and row 11 are the real demonstrations the brief requires.

### Loop mechanics

- The revise → Summarizer routing and the caps (1 Critic revision per round, 2 change requests) are also covered by unit tests with a fake Critic.
- Row 6 tests the real Critic's judgement. The unit tests check that the routing around the Critic is correct.

### Evidence for each row

- Each row links to its trace (a screenshot or exported trace) and its run file in `evidence/`.

### Build notes

- During the build, `docs/build-notes.md` gets one line for every real bug or surprise.
- These notes feed the "lesson learned" column, the failure investigated in the walkthrough, and the README's "one improvement after testing".

## Why

- Mocks make failures repeatable and quick to trigger. Real services prove that the integration actually works. Each is used where it gives the strongest evidence.
- **The fixed query template (ADR 0012) cannot return zero results live**, so "no results" has to be produced by a fake.
- A one-line log written during the build gives honest material for the lessons-learned column, instead of lessons reconstructed afterwards.

## Consequences

- Row 6 depends on one judgement by the LLM at temperature 1.0. If the Critic misses the overclaim, that is recorded as an actual result and a lesson, not hidden.
- Row 14 needs the dataset path to be a config setting, so that a fixture file (the 8 records plus the injection record) can be loaded without changing the supplied dataset.
