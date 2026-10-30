# ADR 0003: LLM fills a fixed-choice intake form, code validates it; scope locked to the brief

- Status: Accepted
- Date: 2026-10-29

## Context

A free-text question has to become two decisions: which sources to search, and which population, intervention and outcome to screen against. The brief fixes the demonstration scope: metformin and cardiovascular outcomes in adults with type 2 diabetes. The evaluation must include an ambiguous question.

## Decision

1. **Empty input** is rejected in code before any LLM call.

2. **The LLM fills a form with fixed choices:**

   | Field | Allowed values |
   |---|---|
   | `intervention` | `metformin`, `other`, `unclear`, `not_stated` |
   | `outcome` | `cardiovascular`, `other`, `unclear`, `not_stated` |
   | `population` | `adults_t2d`, `other`, `unclear`, `not_stated` |
   | `sources` | `pubmed`, `synthetic`, `both`, `unspecified` |
   | `recency` | `true`, `false` |

   The form also keeps the user's original wording of the population, intervention and outcome, for display.

3. **Code validates the form and acts on it before any search:**
   - Any `other` rejects the question with a message explaining the scope.
   - Any `unclear` (the element is mentioned, but vaguely, such as "does *it* work?") triggers one clarifying question. If the answer is still unclear, the run stops.
   - `not_stated` (the question doesn't mention the element) is filled in by code from the tool's scope. The printed interpretation shows this, for example "population: not stated; using the tool's scope (adults with type 2 diabetes)".
   - **Exception:** if all three elements are `not_stated` and the question doesn't refer to the synthetic dataset (`sources` is neither `synthetic` nor `both`), the question is too vague. It gets one clarifying question.
   - Amended 2026-10-29: the brief's own Q1 (no population stated) and Q2 (no element stated) triggered clarifying questions when "not stated" was treated as "unclear". The scope is fixed, so a missing element is not ambiguous.

4. **Cross-check against misclassification.** If the LLM says the intervention is `metformin`, code checks that "metformin" appears in the original question. If it does not, the field is treated as `unclear`.

   **Spelling check, added 2026-10-30.** This runs in code only, before the LLM call, and applies only to the intervention, the one field with a literal code check. Outcome and population are still classified by the LLM alone.
   - **When it asks:** no word in the question is exactly "metformin", but one is a close spelling match (difflib ratio ≥ 0.85).
     - Accepted, for example: "metaformin" (0.947), "metfromin" (0.889).
     - Rejected, for example: "formin" (0.80), "metabolic" (0.556), "metoprolol" (0.526), "empagliflozin" and "dapagliflozin" (0.364).
   - **The question:** `Did you mean "metformin"? [y/n]`
   - **Yes:** the word is replaced with "metformin" in the text the LLM classifies. The correction is recorded in the run file (`intake_correction`) and in the printed interpretation.
   - **No, or no answer:** the scope message, with no LLM call.
   - **The same check** runs on the answer to a clarifying question.
   - **Words that are not close** behave as before.
   - **Why:** "metaformin" used to trigger a clarifying question and then stop, even when the user repeated the same spelling. The confirmation keeps a human in the loop for the correction, so a wrong accept still cannot happen silently.

5. **Source routing:**

   | Question mentions | Sources searched |
   |---|---|
   | PubMed, or "recent" | Live PubMed only |
   | "synthetic", or "supplied abstracts" | Synthetic dataset only |
   | Both | Both |
   | Neither | Both, in two labelled sections |

   An explicit source wins over recency. "Recent synthetic trials" searches only the synthetic dataset, and the output says that recency was not applied to it.

6. **Recency.** The LLM only sets true or false. Code defines "recent" as published in the last 5 years before the search date. It applies only to the PubMed date window, on top of the 2015+ rule.

7. **Visible interpretation.** The interpreted form is printed at the start of each run and shown again at human review.

8. **Source type is kept.** Every record keeps its `source_type` from retrieval through to the email.

9. **Screening rules apply everywhere.** The fixed rules (human, randomised, 2015 or later, more than 50 participants) apply to every study. The form's population, intervention and outcome are used only for the "population, treatment and outcome match" rule.

## Why

- Questions are free text, so an LLM is the right tool for reading them. The LLM still makes the classification, and code decides what happens because of it.
- With fixed choices, code compares values instead of keeping a synonym list.
- **A wrong reject is safe**, because the user just rephrases. **A wrong accept is risky**, so it is cross-checked in code and shown to the human before anything is sent.
- Rejecting out-of-scope questions before searching saves quota, and keeps the system within the scope the rules were written for.
- Because code owns the date range, "recent" means the same thing on every run and can be stated in the output, as the brief's Q3 requires.

## Alternatives considered

- **Keyword rules:** miss rewordings.
- **Free-text fields with a synonym list:** more code, and more ways to fail.
- **No intake:** cannot handle the ambiguous-question scenario or route to a single source.
- **General scope:** the rules are defined only for the brief's scope.

## Consequences

- Brand names (such as Glucophage, ratio 0.105) still fail the "metformin" cross-check and lead to a clarifying question. This is acceptable, because a wrong reject is safe. Close misspellings get a yes/no confirmation instead (point 4).
- If the LLM wrongly reports an element as `not_stated`, code fills it from the scope, so the run uses the tool's scope instead of what was asked. The printed interpretation and human review make this visible, and evaluation row 4 tests an out-of-scope drug question.
- The evaluation table includes rewordings and an out-of-scope drug question.
