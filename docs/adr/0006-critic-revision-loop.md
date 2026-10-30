# ADR 0006: Critic reviews the summary only; at most 1 revision per round

- Status: Accepted (amended 2026-10-29: revision limit reduced from 2 to 1; the Critic now receives the screening results and the code-check findings)
- Date: 2026-10-29

## Context

The brief asks the Critic to check unsupported claims, contradictions and missing context, and to request a bounded revision. Code already checks grounding mechanically (ADR 0005, ADR 0010).

## Decision

**1. What the Critic receives**

- **Evidence:** the abstracts of all included studies, and the screening results for every screened record.
- **Code-check findings:** removed claims, unverified details and a missing comparison. These are labelled as context only. A removed claim is unsupported and is never treated as evidence.
- **The draft:** the validated draft that passed the content checks.

**2. How the Critic judges**

- It judges the draft as it stands.
- It requests a revision only if what remains is misleading or leaves out context a reader needs, including when a removal leaves the summary misleading. It never requests one just because errors occurred.
- It checks for:
  - claims that don't fairly reflect their evidence (direction, strength, overclaiming, swapped arms)
  - contradictions
  - missing context, including findings that point in different directions without the draft saying so
- Code-check removals and warnings stay whatever the Critic decides.

**3. Output**

- The Critic's output is validated with Pydantic.
- A verdict of `revise` with an empty issues list is invalid.
- An issue that names a claim not in the draft is invalid.

**4. Only one action**

- The Critic can only send the draft back to the Summarizer, with its list of issues.

**5. Revision limit**

- At most **1** Summarizer revision per round.
- If the Critic still says `revise` after that, the draft goes to human review anyway. The unresolved issues are shown as warnings, and those claims stay in.

**6. Invalid Critic output**

- Invalid output is retried once.
- If it fails again, the draft goes to human review with the warning "Critic review unavailable".
- The content checks have already run, so the claims are still verified against their sources.

**7. Approving with warnings**

- Approving a draft that has warnings is logged as `approved_with_warnings`.

**8. Fresh budget per human round**

- Each human change request (ADR 0007) starts a new round, with a fresh revision budget of 1.

## Worst case (bounded)

| | Per round | Per run (3 rounds) |
|---|---|---|
| Summarizer passes (draft + 1 revision) | 2 | 6 |
| Summarizer calls (one retry per pass) | 4 | 12 |
| Critic reviews | 2 | 6 |
| Critic calls (one retry per review for invalid output) | 4 | 12 |

## Why

- **Code and the Critic catch different problems.** Code catches invented quotes and numbers. The Critic catches what code cannot: "proves benefit" when the evidence is mixed, outcomes that aren't alike being pooled, or numbers swapped between arms.
- **Code findings are context, not evidence.** The Critic can see that something was removed and judge whether what remains still reads fairly. It cannot bring the removed content back as evidence.
- **One revision keeps the cost down.** A second revision doubled the worst-case calls on a 500-requests-per-day quota. In every live run so far, the Critic approved the first draft. Whether one revision is enough when the Critic does object is tested by the weak-draft evaluation (ADR 0015, row 6).
- **Unresolved issues are disclosed, not hidden.** This is what the brief requires.
- **The Critic stays out of routing.** Its only possible action is a revision request (ADR 0002).

## Alternatives considered

- **2 revisions per round** (the original decision): more calls for little observed benefit.
- **No cap:** might never end, and could use up the free-tier quota.
- **A Critic that blocks delivery:** the human is the final authority. Warnings inform the human's decision rather than replacing it.
- **A single revision loop**, where code-check issues and Critic issues go to the Summarizer together. This was rejected; see the README.
