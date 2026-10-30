"""Eligibility agent (ADR 0004): the LLM extracts quoted facts; code verifies them and applies the rules.

Records are assessed concurrently (ADR 0009). The LLM's throttle spaces request
starts, so concurrency never exceeds the RPM limit.

Each record is handled independently (ADR 0013): invalid output or Gemini still
failing after retries makes only that record needs_review. The run stops only
when every record failed that way, or on quota exhaustion or bad credentials.
"""
from __future__ import annotations

from typing import Callable

from langsmith.utils import ContextThreadPoolExecutor

from screener.agents.intake import SCOPE
from screener.config import Settings
from screener.errors import InvalidOutputError, TransientError
from screener.llm import LLM
from screener.models import ErrorEvent, ExtractedFacts, IntakeForm, Record, RecordExtraction, ScreeningDecision
from screener.prompts import Prompt, data_block, load_prompt
from screener.rules import screen, screen_on_metadata, verify_facts
from screener.tracing import add_metadata, traced

GEMINI_UNAVAILABLE = "Gemini unavailable after retries"
INVALID_AFTER_RETRY = "LLM output invalid after retry"
METADATA_ONLY = "not extracted: excluded on metadata"


def _user_message(record: Record, form: IntakeForm) -> str:
    return "\n".join(
        [
            f"Scope: {SCOPE}.",
            f"User's wording (context only): population '{form.population_wording}', "
            f"intervention '{form.intervention_wording}', outcome '{form.outcome_wording}'.",
            "",
            data_block("abstract", record.abstract or "", record_id=record.record_id),
        ]
    )


def extract_facts(record: Record, form: IntakeForm, *, llm: LLM, prompt: Prompt, on_error=None) -> RecordExtraction:
    if record.abstract is None:
        return RecordExtraction(record_id=record.record_id, facts=None, check_failures=["no abstract"])
    for attempt in (1, 2):  # one retry on invalid output (ADR 0002)
        try:
            raw = llm.generate(
                agent="eligibility", prompt_version=prompt.version, system=prompt.text,
                user=_user_message(record, form), schema=ExtractedFacts, on_error=on_error,
            )
        except InvalidOutputError as e:
            if on_error:
                on_error(ErrorEvent(stage=f"eligibility[{record.record_id}]", error_class="invalid_output",
                                    message=str(e), attempt=attempt))
            continue
        except TransientError:  # already retried with backoff and recorded by the LLM layer
            return RecordExtraction(record_id=record.record_id, facts=None, check_failures=[GEMINI_UNAVAILABLE])
        facts, failures = verify_facts(raw, record.abstract)
        return RecordExtraction(record_id=record.record_id, facts=facts, check_failures=failures)
    return RecordExtraction(record_id=record.record_id, facts=None, check_failures=[INVALID_AFTER_RETRY])


@traced("eligibility")
def assess_record(record: Record, form: IntakeForm, *, llm: LLM, settings: Settings, prompt: Prompt,
                  on_error=None) -> dict:
    """One record: metadata check, then extract, verify, screen. The span shows the decision (ADR 0014)."""
    pre = screen_on_metadata(record, min_year=settings.min_publication_year)
    if pre is not None:  # definite exclusion from metadata alone: no LLM call
        add_metadata(record_id=record.record_id, decision=pre.decision, failed_rules=pre.failed_rules,
                     metadata_only=True)
        return {"extraction": RecordExtraction(record_id=record.record_id, facts=None,
                                               check_failures=[METADATA_ONLY]),
                "decision": pre}
    extraction = extract_facts(record, form, llm=llm, prompt=prompt, on_error=on_error)
    decision = screen(
        record, extraction,
        min_year=settings.min_publication_year, sample_size_must_exceed=settings.sample_size_must_exceed,
    )
    add_metadata(record_id=record.record_id, decision=decision.decision, failed_rules=decision.failed_rules,
                 missing_details=decision.missing_details, check_failures=extraction.check_failures)
    return {"extraction": extraction, "decision": decision}


def screen_records(
    records: list[Record],
    form: IntakeForm,
    *,
    llm: LLM,
    settings: Settings,
    on_error: Callable[[ErrorEvent], None] | None = None,
) -> tuple[list[RecordExtraction], list[ScreeningDecision]]:
    """Assess all records, in input order.

    Quota exhaustion and bad credentials propagate at once, cancelling records not yet started.
    If Gemini was unavailable for every record, raise TransientError so the run stops (ADR 0013).
    """
    prompt = load_prompt("eligibility", settings.prompts_dir)
    with ContextThreadPoolExecutor(max_workers=max(1, settings.eligibility_concurrency)) as pool:
        futures = [
            pool.submit(assess_record, r, form, llm=llm, settings=settings, prompt=prompt, on_error=on_error,
                        langsmith_extra={"name": f"eligibility[{r.record_id}]"})
            for r in records
        ]
        try:
            results = [f.result() for f in futures]
        except Exception:
            for f in futures:
                f.cancel()
            raise
    extractions = [r["extraction"] for r in results]
    decisions = [r["decision"] for r in results]
    attempted = [x for x, d in zip(extractions, decisions) if not d.metadata_only]
    if attempted and all(x.check_failures == [GEMINI_UNAVAILABLE] for x in attempted):
        raise TransientError("Gemini unavailable for every record after retries")
    return extractions, decisions
