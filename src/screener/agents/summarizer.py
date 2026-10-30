"""Summarizer agent (ADR 0005): structured, quoted claims and study details for included records only."""
from __future__ import annotations

from screener.errors import InvalidOutputError
from screener.llm import LLM
from screener.models import CriticIssue, ErrorEvent, IntakeForm, Record, SummaryDraft
from screener.prompts import Prompt, data_block


def _user_message(
    included: dict[str, Record],
    form: IntakeForm,
    *,
    previous: SummaryDraft | None,
    problems: list[str] | None,
    critic_issues: list[CriticIssue] | None,
    instruction: str | None,
) -> str:
    parts = [
        "Question scope: metformin and cardiovascular outcomes in adults with type 2 diabetes.",
        f"User's wording (context only): '{form.intervention_wording}', '{form.outcome_wording}', "
        f"'{form.population_wording}'.",
        "",
        "Included studies:",
    ]
    for r in included.values():
        parts.append(data_block("abstract", r.abstract or "", record_id=r.record_id, source_type=r.source_type))
    if previous is not None:
        parts += ["", "Previous draft:", data_block("draft", previous.model_dump_json(indent=1))]
    if problems:
        parts += ["", "Code checks failed for these items; fix them:", *[f"- {p}" for p in problems]]
    if critic_issues:
        parts += ["", "Critic issues to address:",
                  *[f"- {i.kind} (claim {i.claim_id or 'whole draft'}): {i.explanation}" for i in critic_issues]]
    if instruction:
        parts += ["", "Reviewer instruction (follow it unless it breaks the rules):",
                  data_block("instruction", instruction)]
    return "\n".join(parts)


def summarize(
    included: dict[str, Record],
    form: IntakeForm,
    *,
    llm: LLM,
    prompt: Prompt,
    on_error=None,
    stage: str = "summarizer",
    previous: SummaryDraft | None = None,
    problems: list[str] | None = None,
    critic_issues: list[CriticIssue] | None = None,
    instruction: str | None = None,
    attempts: int = 2,
) -> SummaryDraft | None:
    """One Summarizer output, or None if every attempt returned invalid output (ADR 0002)."""
    user = _user_message(included, form, previous=previous, problems=problems,
                         critic_issues=critic_issues, instruction=instruction)
    for attempt in range(1, attempts + 1):
        try:
            return llm.generate(agent="summarizer", prompt_version=prompt.version, system=prompt.text,
                                user=user, schema=SummaryDraft, on_error=on_error)
        except InvalidOutputError as e:
            if on_error:
                on_error(ErrorEvent(stage=stage, error_class="invalid_output", message=str(e), attempt=attempt))
    return None
