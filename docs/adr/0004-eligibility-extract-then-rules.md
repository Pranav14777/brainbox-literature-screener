# ADR 0004: Eligibility: LLM extracts quoted facts, code verifies them and applies the rules

- Status: Accepted
- Date: 2026-10-29

## Context

The rules must be applied consistently, and each decision must be explained using source text. The synthetic dataset contains deliberate edge cases:

- SYN006 has exactly 50 participants, and the rule is "more than 50".
- SYN005 is a randomised study in mice.
- SYN003 does not state its sample size.

An LLM deciding eligibility directly can get these wrong (an off-by-one at 50, guessing a missing value, treating "randomly allocated" as evidence of a human RCT) and can give different answers on different runs. Pure code (regex and keywords) is fragile on prose and cannot judge scope.

## Decision

**1. The LLM extracts facts only. Each fact has its own verbatim quote.**
- Total sample size, or `null` if not stated.
- Each group size, when group sizes are reported.
- Species.
- Study design.
- Whether population, intervention and outcome match the question.

**2. The LLM never does arithmetic.** Code adds up group sizes.

**3. Code verifies each fact, using the rules in ADR 0010.**
- (a) The quote appears in the source abstract.
- (b) Every number in the fact appears in its own quote.
- If either check fails, the fact becomes unknown.

**4. Metadata is used directly, with no LLM involved:**
- Publication year.
- Publication type.
- PubMed's Humans/Animals tags, when present.

**5. Which sample size counts.** Participants randomised or enrolled.
- A stated total wins.
- If no total is stated, code adds up the group sizes.
- If a stated total and the sum of groups disagree, it matters only when the decision changes, meaning one number is above 50 and the other is 50 or below. In that case the record is `needs_review` ("sample size inconsistent").
- If both numbers are on the same side of 50, the stated total is used and the discrepancy is noted in the evidence table.

**6. Metadata vs text on study design.**
- A publication type of "Randomized Controlled Trial" counts as evidence of randomisation.
- "Observational Study" counts as evidence against randomisation.
- A generic type such as "Journal Article" gives no information, so the abstract text decides.
- If metadata and text clearly disagree on design, the record is `needs_review`, and both sources are shown.
- The publication types "Meta-Analysis", "Systematic Review" and "Review" count as evidence that the record is not a randomised trial. This is based on the publication type tag, not on keywords in the text.
  - The eligibility prompt tells the LLM that a review's design is non-randomised.
  - If the LLM still reports "randomised" for a record with one of these tags, the conflict makes it `needs_review`.
  - Otherwise the record is excluded as "not a randomised trial (review or meta-analysis)".
- If the publication types contradict each other (for example, both "Randomized Controlled Trial" and "Review"), the record is `needs_review`.
- The publication type "Clinical Trial Protocol" excludes the record as "trial protocol, no results". The PubMed query already filters these out (ADR 0012); this rule is a safety net.

**7. A deterministic rule engine decides.**
- A definite exclusion beats `needs_review`, including the conflicts in points 5 and 6.
- Every failed rule is listed, except for metadata-only exclusions (point 7a).
- `needs_review` names the missing or conflicting detail.

**7a. Metadata check before the LLM call.**
- If metadata alone gives a definite exclusion, the eligibility LLM call is skipped. The two cases are a publication year before 2015, and a "Clinical Trial Protocol" tag.
- The decision is recorded as "excluded: <rule> (other rules not checked)", and it is marked `metadata_only`.
- SYN004 (2014) is excluded this way, without an LLM call. This saves quota, and a definite exclusion cannot be changed by any other rule.

**8. Invalid LLM output for a study**, after one retry, makes that study `needs_review`. It is never `include`. The same applies if Gemini is still failing for that study after its retries (ADR 0013). Each record is handled independently, so one record's failure affects no other record.

**9. Live PubMed records go through the same steps.** A record with no abstract is `needs_review`.

**10. Metformin as background therapy.** Decided 2026-10-30.
- A trial where metformin is background therapy in every arm, such as GRADE, can pass the intervention match. Eligibility is not changed for this case: metformin as background is acceptable for this question.
- The Summarizer handles it instead. It reports only results comparing metformin with its comparator. If the abstract has none, the claim says so rather than reporting numbers for other treatments (ADR 0005).

## Expected results on the synthetic dataset (acceptance test from the brief)

| Record | Decision | Reason |
|---|---|---|
| SYN001 | include | |
| SYN002 | include | |
| SYN008 | include | |
| SYN003 | needs_review | missing: total sample size |
| SYN004 | exclude | published 2014 (from metadata, no LLM) |
| SYN005 | exclude | animal study |
| SYN006 | exclude | 50 participants is not more than 50 (checked in code) |
| SYN007 | exclude | non-randomised (observational; metadata and text agree) |

## Why

- The model handles what code cannot: reading prose.
- Code handles what models do badly: thresholds, arithmetic and applying rules the same way every time.
- The quote checks stop the model from inventing evidence. A made-up quote or number cannot pass, and failing safe means "unknown", never "include".
- Conflicts go to `needs_review` only when they could change the outcome. This avoids pointless reviews without hiding the discrepancy.

## Alternatives considered

- **LLM decides:** inconsistent, and fails on exactly the edge cases in the dataset.
- **Pure code:** a regex for "N participants" in SYN001 finds 120, which is one group, not the total of 240. Code also cannot judge "clearly out of scope".
