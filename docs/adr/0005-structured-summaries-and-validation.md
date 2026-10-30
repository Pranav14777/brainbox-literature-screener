# ADR 0005: Summarizer returns structured claims; two-layer validation; sources never mixed

- Status: Accepted
- Date: 2026-10-29

## Context

The summary must be cited and must not invent missing information. Synthetic and real findings must stay clearly separate. A single block of prose from an LLM cannot be checked automatically.

## Decision

**1. The Summarizer returns structured output, not prose.**

- **Study details** for each included study. Each detail has its own supporting quote, or comes from trusted metadata. A detail the abstract does not state is the literal value `not reported`. A detail that fails its check is handled as described in point 5.
- **Claims.** Each claim cites one or more `record_id`s and carries a list of verbatim quotes. Every cited record needs at least one quote of its own. Each claim has a `kind`: `study` or `comparison` (point 8).
- **Study claims report only metformin-versus-comparator results.** If an included abstract reports none, its study claim says so instead of reporting numbers for other treatments. This applies to trials where metformin is background therapy, such as GRADE; see ADR 0004 and summarizer-v6.

**2. Code builds the evidence table and the summary text** from that structured output.

**3. Layer 1 (format).** Pydantic checks the shape of the output.

**4. Layer 2 (content).** Code checks that:

- each cited `record_id` is an included study
- each quote is an exact substring of its record's abstract
- every number in a claim appears in at least one of that claim's quotes (ADR 0010)
- each study detail is validated separately against its own quote or metadata
- missing details say `not reported`

**5. Claims that fail Layer 2.**

- A failing claim is retried once.
- If it still fails, it is **removed** and shown as a "removed: unsupported" warning.
- Removed claims are never part of the content that can be approved.
- If no claims pass, the summary reads "No synthesis could be produced; see evidence table". The evidence table is still shown, because code builds it from the eligibility results.
- **A study detail that fails its check** is retried once. If it still fails:
  - its value becomes `not reported (unverified)`
  - a "removed: unsupported" warning is shown on the review screen as a reviewer note (not sent) and kept in the run file (ADR 0007)

  This is different from `not reported`, which means the abstract does not state the detail at all.

**6. Live PubMed records follow the same rules.** Citations use PMID and URL. Quotes and numbers are checked against the fetched abstract text.

**7. Sources are never mixed.**

- A claim may cite several records only if they all share the same `source_type`.
- Real and synthetic findings appear in separate sections, in both the summary and the evidence table.

**8. A cross-study comparison is required, and enforced by code.** Added 2026-10-29 as the improvement after testing.

- **When it's needed:** a source has two or more included studies.
- **What it is:** exactly one claim of kind `comparison`, citing every included study from that source, with a quote from each. It says whether events were fewer, more or similar with metformin in each study, and says plainly when findings point in different directions.
- **Checks:** the usual number rules apply, so no pooled or calculated numbers. A comparison that doesn't cite every included study from its source fails. A comparison for a source with only one included study also fails.
- **If it's missing:** the content check fails. The pass's one retry tells the Summarizer exactly what's missing.
- **If it's still missing after the retry:** the warning "no cross-study comparison" is shown to the reviewer as a reviewer note. The email's Limitations section gets the fixed sentence "The summary does not compare the direction of findings across the included … studies" (ADR 0007). Code never writes the claim itself.
- **Display:** the comparison is shown last in its source section.
- **Why it's enforced in code:** a prompt instruction alone (summarizer-v2) was ignored in two live runs on the brief's Q1, and the Critic approved both drafts (see build-notes).

## Why

- Structured claims let code check the evidence behind each claim one by one. A free-text summary could only be checked by another LLM.
- Code catches invented or misquoted evidence every time. The Critic (ADR 0006) is left with the questions that need judgement.
- **Failing checks and Critic issues are treated differently on purpose.**
  - A failed content check proves a claim is unsupported, so the claim is removed.
  - A Critic issue is a judgement about a claim that is supported, so the claim stays and carries a warning.
- Allowing a claim to cite several records makes statements like "findings are mixed across SYN001, SYN002 and SYN008" possible. Each part of such a claim is still checked against its own quote.

## Alternatives considered

- **Free-text summary checked only by the Critic.** Relying on a second LLM to catch the first one's inventions is a matter of chance, not a guarantee.
- **One quote per claim.** Fails when one claim needs several sentences, for example SYN008's group size and its event counts.
