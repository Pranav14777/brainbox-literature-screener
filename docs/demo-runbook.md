# Demo runbook

Four steps in order, about 10 minutes plus Gemini wait time.

## Before you start

- Open a PowerShell terminal in `D:\brainbox-literature-project` and activate the environment:
  ```powershell
  .venv\Scripts\Activate.ps1
  ```
  The prompt must show `(.venv)`.
- Open LangSmith (project from `LANGSMITH_PROJECT`) and your Gmail inbox in the browser.
- **Quota:** a Q2 run uses about 10 Gemini requests, and a Q3 run about 13–15. Keep at least 50 of the 500 daily requests spare.
- **Do not use `--dev-cache`.** The demo makes live calls (ADR 0009).
- **Timing:** each live run takes 1–3 minutes, because free-tier Gemini responses are slow (12–44 s per call). Explain this while it runs.
- **Run-to-run variation:** the model runs at temperature 1.0, so wording, and sometimes decisions, vary between runs. The tool's design shows this honestly rather than hiding it.

---

## 1. Synthetic screening (brief Q2), rejected

```powershell
screener "Which synthetic studies qualify under the screening rules?" --scenario demo:q2
```

**Point out on screen**
- **Interpretation:** all three elements say "not stated; using the tool's scope". The LLM fills a form; code validates it and decides what happens next (ADR 0002, ADR 0003).
- **"Screened 8 records":** every synthetic record is screened. Embeddings rank them but never filter them out (ADR 0011).
- **Evidence table:**
  - **SYN006** is excluded at exactly 50. Code compares 50 > 50; the LLM only quoted the number (ADR 0004).
  - **SYN005** is an animal study, with its supporting quote.
  - **SYN004** is "published before 2015 (other rules not checked)". It was excluded on metadata before any LLM call.
  - **SYN007** is non-randomised, decided from the publication type.
  - **SYN003** needs review: the sample size is missing. The tool doesn't guess.
- **Summary:** event counts per study, the missing confidence intervals and p values stated, and the comparison claim shown last ("findings point in different directions"). Citations are `[SYN00x]`.
- **Reviewer notes (not sent):** these appear outside the email and are never sent.

**Action:** type `x` once to show the one-line "Please choose a, r, e, s or c" message, then type `r`. The output reads "Rejected. Nothing was sent."

**Then:** open the printed run file (`runs\<run_id>.json`) to show the full per-record evidence that the email leaves out.

**LangSmith (tag `demo:q2`):**
- The root span `screener_run` has the question as its input.
- The `eligibility[SYN00x]` spans run in parallel. SYN004 has no `gemini` child.
- Then the `summarizer[1,1]` and `critic[1,1]` spans.
- Token counts are on each `gemini` span.

---

## 2. Live PubMed (brief Q3), rejected

```powershell
screener "Which recent PubMed trials investigate metformin and cardiovascular outcomes in adults with type 2 diabetes?" --scenario demo:q3
```

**Point out on screen**
- **Header:** the search date, the date window with the definition of "recent" (published in the 5 years before the search date), and "Screened 10 of N".
- **Records:** real titles, years, PMIDs and URLs.
- **Exclusions:** each one has its rule and quote: a mouse study, n = 29 or n = 46, an observational design, outcomes that aren't cardiovascular.
- **The query:** it's in the run file, not the email. Say why it was narrowed (ADR 0012): the broad version filled the top 10 with trials of other drugs.
- **Be honest about the known limitation:** if a GRADE claim lists other drugs' hazard ratios, the Critic did not catch it. Human review is the final safeguard (README limitations, evaluation row 6).

**Action:** type `r`.

**LangSmith (tag `demo:q3`):** the `retrieval.pubmed` span shows the query, date window, total matches and PMIDs, with an `.attempt` child span for each request.

---

## 3. Synthetic findings (brief Q1), approved and sent once

```powershell
screener "What do the eligible synthetic trials report about metformin and cardiovascular outcomes?" --scenario demo:q1-send
```

**Point out on screen**
- **Population:** "not stated; using the tool's scope".
- **Numbers:** they match the brief: SYN001 12 vs 20, SYN002 24 of 160 vs 16 of 160, SYN008 6 vs 7. No confidence intervals or p values are reported, and the findings are mixed. Nothing is pooled and nothing claims a proven benefit.

**Action (optional):**
1. Type `s` and change the subject. The email is shown again, because any change needs approval again (ADR 0007).
2. Type `a`. The output reads `Delivery: sent (Gmail message ...)`.

**Then:**
- Open the inbox. The email is exactly the one shown, with no reviewer notes and the run ID in the footer.
- Show that it sends exactly once:
  ```powershell
  screener --resend <run_id>
  ```
  The output reads `Send blocked: already sent (Gmail message ...)`.
- Open the run file and show the approval record (decision, fingerprint) and the delivery record (`sent`, message ID).

**LangSmith (tag `demo:q1-send`):**
- The `human_review` stage is recorded in the run file.
- The `send` span outputs the status, message ID and fingerprint.

---

## 4. Mocked persistent failure

```powershell
python scripts\run_evaluation.py --only pubmed-persistent-failure-both
```

**Point out**
- PubMed fails 3 times with backoff, and the run continues with the synthetic records only.
- The email's Limitations section says PubMed could not be searched, and the reviewer note says so as well.
- Nothing is sent.
- Optional: run `--only pubmed-persistent-failure-only`. When PubMed was the only source, the run stops.

**LangSmith (tag `eval:pubmed-persistent-failure-both`):**
- Three `retrieval.pubmed.esearch.attempt` spans, each with an error.
- The run is tagged `mock=true`.

---

## If asked

- **One alternative:** LangGraph (ADR 0008), or a single revision loop for code-check and Critic issues (README).
- **One failure investigated:** the Lancet raised-dot decimals in UKPDS 91 ("p=0·010" was read as 0 and 10). See build-notes and ADR 0010.
- **Other honest findings:**
  - Critic row 6 still approves the real weak draft.
  - The prompt-injection test: real Gemini ignored the injection and excluded SYN009 (row 16b). The simulated worst case is PARTIAL: a model that obeyed could make the injected record pass the quote check (row 16a).
  - Eligibility varies between runs (evaluation row "eligibility-consistency").
- **Spelling check (optional):** `screener "Which synthetic papers talk about metaformin effects on cardiovascular issues?"`, then answer `y`.
- **Tests:** `python -m pytest -q` (all fakes, no network).

## Fallbacks

- **Gemini slow or quota exhausted:** show the saved evaluation evidence. That's `evidence/eval-normal-synthetic-review.txt`, `evidence/eval-normal-pubmed-review.txt` and `docs/evaluation.md`, plus the traces tagged `eval:*`.
- **Empty Q3 summary** ("No synthesis could be produced"): explain it honestly. Number words or missing quotes removed the claims; the output stays truthful even when it is empty (README limitations).
