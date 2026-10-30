# ADR 0010: Number and quote matching rules

- Status: Accepted
- Date: 2026-10-29

## Context

ADR 0004 and ADR 0005 require every number in an extracted fact or claim to be backed by source text. A simple substring check fails on the dataset in four ways:

- **It rejects a correct claim.** SYN008 writes "Six" and "seven", so a claim saying "6/80" fails.
- **It passes for the wrong reason.** "6" is found inside "160".
- **It rejects a valid detail.** The publication year comes from metadata, not from the abstract.
- **It is also needed for real abstracts.** These report decimals and ranges, such as "HR 0.85 (95% CI 0.74–0.97)".

## Decision

**1. Quotes belong to individual facts**

- Each extracted study detail has its own supporting quote, or comes from trusted metadata such as the year.
- Code validates each fact separately before building the evidence table.
- A claim carries a list of quotes. Every number in the claim must appear in at least one of those quotes.
- Example: SYN008's group size comes from one sentence and its event counts from another.

**2. How numbers are found**

- Numbers are matched as whole numbers, never as part of a longer number.
- Number words are converted to digits ("Six" becomes 6, "Ninety" becomes 90). Thousands commas are removed. This applies to both the source text and the claim.
- A decimal is one number: 0.85, not 0 and 85.
- A range is split into its two ends: "0.74–0.97" becomes 0.74 and 0.97. A dash between two numbers is read as a range, not a minus sign.
- "Type 1 diabetes" and "type 2 diabetes" are disease names, not numbers, so the number check skips them.
- A raised dot between two digits is a decimal point. Lancet-style abstracts such as UKPDS 91 write "p=0·010". This applies to both the abstract and the claim, and also to quote matching, so "0·010" and "0.010" are the same. Added 2026-10-30, after "0·010" was read as the two numbers 0 and 10 (see build-notes).

**3. Where numbers may come from**

- Study details may use numbers from metadata, such as the publication year.
- An LLM never writes a calculated number. The only calculated number in the output is the sum of group sizes, computed by code and labelled "sum of group sizes".
- A percentage or ratio stated in the abstract, such as "20%" or "HR 0.85", may be used if it is in the claim's quote. Only new calculations are banned.

**4. Failure handling**

- A fact that fails the check becomes unknown (ADR 0004).
- A claim that fails is removed (ADR 0005).

## Why

- Matching whole numbers after conversion fixes all four failures above, and stays deterministic and easy to explain.
- Banning calculated numbers in LLM text also blocks pooling across studies. A pooled total such as "32 events overall" cannot appear in any quote, so it is rejected.

## Known limitation

- The check confirms that a number exists in the quote, not which arm it belongs to. Numbers swapped between arms (12 vs 20) pass the check. Catching that is the Critic's job (ADR 0006).
