"""Critic agent (ADR 0006): judges a draft that has already passed the content checks.

The Critic receives:
- the abstracts of all included studies and the screening results, to compare the draft against
- what the code checks found (removed claims, unverified details, a missing comparison),
  labelled as context only: removed claims are unsupported and never evidence
Code-check removals and warnings stay whatever the Critic decides.
"""
from __future__ import annotations

from screener.errors import InvalidOutputError
from screener.llm import LLM
from screener.models import CriticReport, ErrorEvent, Record, ReviewWarning, ScreeningDecision, SummaryDraft
from screener.prompts import Prompt, data_block


def _screening_lines(decisions: list[ScreeningDecision]) -> list[str]:
    lines = []
    for d in decisions:
        reason = "; ".join(d.failed_rules or d.missing_details)
        lines.append(f"{d.record_id} ({d.source_type}): {d.decision}" + (f" ({reason})" if reason else ""))
    return lines


def critic_message(draft: SummaryDraft, included: dict[str, Record], decisions: list[ScreeningDecision],
                   code_findings: list[ReviewWarning]) -> str:
    findings = [w.message for w in code_findings
                if w.kind in ("removed_unsupported", "missing_comparison")] or ["none"]
    return "\n".join(
        ["Included studies:"]
        + [data_block("abstract", r.abstract or "", record_id=rid, source_type=r.source_type)
           for rid, r in included.items()]
        + ["", "Screening results:", data_block("screening_results", "\n".join(_screening_lines(decisions))),
           "", "Code-check findings (context only; removed claims are unsupported and never evidence):",
           data_block("code_check_findings", "\n".join(findings)),
           "", "Draft to review:", data_block("draft", draft.model_dump_json(indent=1))]
    )


def critique(
    draft: SummaryDraft,
    included: dict[str, Record],
    *,
    decisions: list[ScreeningDecision],
    code_findings: list[ReviewWarning],
    llm: LLM,
    prompt: Prompt,
    thinking_level: str | None = None,
    on_error=None,
    stage: str = "critic",
) -> CriticReport | None:
    """The Critic's report, or None if its output was invalid twice ("Critic review unavailable")."""
    user = critic_message(draft, included, decisions, code_findings)
    claim_ids = {c.claim_id for c in draft.claims}
    for attempt in (1, 2):
        try:
            report = llm.generate(agent="critic", prompt_version=prompt.version, system=prompt.text,
                                  user=user, schema=CriticReport, thinking_level=thinking_level, on_error=on_error)
            if unknown := [i.claim_id for i in report.issues if i.claim_id and i.claim_id not in claim_ids]:
                raise InvalidOutputError(f"critic: issues name claims not in the draft: {', '.join(unknown)}")
            return report
        except InvalidOutputError as e:
            if on_error:
                on_error(ErrorEvent(stage=stage, error_class="invalid_output", message=str(e), attempt=attempt))
    return None
