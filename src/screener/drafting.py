"""One drafting round: Summarizer -> content checks -> Critic, with bounded revisions (ADR 0005, ADR 0006).

A round is the first draft, or one human change request, plus up to
max_critic_revisions (1) Critic-requested revision. Each Summarizer pass gets
one retry, for invalid output or failed content checks. Routing here is code:
the Critic only returns a verdict, and this loop decides what runs next
(ADR 0002). Code-check removals and warnings stay whatever the Critic decides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from screener.agents.critic import critique
from screener.agents.summarizer import summarize
from screener.config import Settings
from screener.llm import LLM
from screener.models import (
    CriticReport,
    Draft,
    ErrorEvent,
    IntakeForm,
    Record,
    RemovedClaim,
    ReviewWarning,
    ScreeningDecision,
    SummaryDraft,
)
from screener.prompts import Prompt, load_prompt
from screener.tracing import add_metadata, traced
from screener.validation import check_draft, finalize

NO_SYNTHESIS = "No synthesis could be produced; see evidence table."


@dataclass
class RoundResult:
    drafts: list[Draft] = field(default_factory=list)
    warnings: list[ReviewWarning] = field(default_factory=list)  # warnings for the final draft

    @property
    def final(self) -> Draft:
        return self.drafts[-1]


@traced("summarizer")
def produce_draft(
    included: dict[str, Record], form: IntakeForm, *, llm: LLM, prompt: Prompt, on_error=None,
    previous: SummaryDraft | None = None, critic_issues=None, instruction: str | None = None,
) -> tuple[SummaryDraft, list[RemovedClaim], list[ReviewWarning]]:
    """Summarize, check, then finalize what still fails.

    Each pass makes at most 2 Summarizer calls (ADR 0006): the one retry goes to
    whichever failure happens first, invalid output or failed content checks.
    """
    common = dict(llm=llm, prompt=prompt, on_error=on_error, critic_issues=critic_issues,
                  instruction=instruction, attempts=1)
    draft = summarize(included, form, previous=previous, **common)
    retried = draft is None
    if retried:
        draft = summarize(included, form, previous=previous, **common)
    draft = draft or SummaryDraft(study_details=[], claims=[])
    check = check_draft(draft, included)
    if not check.ok and not retried:
        retry = summarize(included, form, previous=draft, problems=check.describe(), **common)
        if retry is not None:
            retry_check = check_draft(retry, included)
            failures = lambda c: len(c.claim_failures) + len(c.detail_failures)  # noqa: E731
            if failures(retry_check) <= failures(check):
                draft, check = retry, retry_check
    final, removed, warnings = finalize(draft, check, included)
    add_metadata(claims_kept=len(final.claims), claims_removed=len(removed),
                 details_unverified=len(check.detail_failures))
    return final, removed, warnings


@traced("critic")
def review_draft(draft: SummaryDraft, included: dict[str, Record], *, decisions: list[ScreeningDecision],
                 code_findings: list[ReviewWarning], llm: LLM, prompt: Prompt, thinking_level: str | None = None,
                 on_error=None) -> CriticReport | None:
    if not draft.claims:  # nothing to judge; the human sees "no synthesis" instead
        return CriticReport(verdict="approve", issues=[])
    report = critique(draft, included, decisions=decisions, code_findings=code_findings,
                      llm=llm, prompt=prompt, thinking_level=thinking_level, on_error=on_error)
    add_metadata(verdict=report.verdict if report else "unavailable",
                 issues=len(report.issues) if report else None)
    return report


def run_round(
    round_number: int,
    included: dict[str, Record],
    form: IntakeForm,
    *,
    decisions: list[ScreeningDecision],
    llm: LLM,
    settings: Settings,
    on_error: Callable[[ErrorEvent], None] | None = None,
    instruction: str | None = None,
    previous: SummaryDraft | None = None,
) -> RoundResult:
    summarizer_prompt = load_prompt("summarizer", settings.prompts_dir)
    critic_prompt = load_prompt("critic", settings.prompts_dir)
    result = RoundResult()
    issues = None
    for pass_number in range(1, settings.max_critic_revisions + 2):
        summary, removed, warnings = produce_draft(
            included, form, llm=llm, prompt=summarizer_prompt, on_error=on_error, previous=previous,
            critic_issues=issues, instruction=instruction,
            langsmith_extra={"name": f"summarizer[{round_number},{pass_number}]"},
        )
        report = review_draft(summary, included, decisions=decisions, code_findings=warnings, llm=llm,
                              prompt=critic_prompt, thinking_level=settings.critic_thinking_level, on_error=on_error,
                              langsmith_extra={"name": f"critic[{round_number},{pass_number}]"})
        result.drafts.append(Draft(round=round_number, pass_number=pass_number, summary=summary,
                                   removed_claims=removed, critic=report, critic_available=report is not None))
        result.warnings = list(warnings)
        if not summary.claims:
            result.warnings.append(ReviewWarning(kind="no_synthesis", message=NO_SYNTHESIS))
        if report is None:
            result.warnings.append(ReviewWarning(kind="critic_unavailable", message="Critic review unavailable"))
            break
        if report.verdict == "approve":
            break
        if pass_number == settings.max_critic_revisions + 1:  # revision limit reached
            result.warnings += [
                ReviewWarning(kind="critic_issue",
                              message=f"unresolved critic issue ({i.kind}, claim {i.claim_id or 'whole draft'}): "
                                      f"{i.explanation}")
                for i in report.issues
            ]
            break
        issues, previous = report.issues, summary
    return result
