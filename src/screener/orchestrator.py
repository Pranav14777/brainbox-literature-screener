"""The orchestrator: a fixed pipeline in plain Python (ADR 0002, ADR 0008).

    intake -> retrieval -> eligibility -> [summarizer -> content checks -> critic] -> human review -> send

LLMs supply validated values; this code chooses every next step. RunState is
saved to runs/<run_id>.json after every step. Any run that stops says why, and
nothing is sent. The root trace span records the user's question (ADR 0014).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from screener.agents.eligibility import screen_records
from screener.agents.intake import describe_interpretation, route_sources, run_intake
from screener.config import Settings
from screener.delivery import GmailSender, SendBlocked, send_approved
from screener.drafting import run_round
from screener.errors import ScreenerError, TransientError
from screener.llm import LLM
from screener.models import (
    ChangeRequest,
    EmailContent,
    PubmedSearch,
    Record,
    ReviewWarning,
    RunState,
    SummaryDraft,
    new_run_id,
)
from screener.render import (
    default_subject,
    email_limitations,
    pubmed_header,
    render_email_body,
    render_evidence_table,
    render_summary,
)
from screener.retrieval.pubmed import PubMedClient, build_query, date_window, search_pubmed
from screener.retrieval.synthetic import Embedder, retrieve_synthetic
from screener.review import approval_record, human_review, read_answer, valid_recipient
from screener.runstore import save_run
from screener.tracing import run_span


@dataclass
class Services:
    """Everything with a side effect. Tests and mocked scenarios swap in fakes (ADR 0015)."""

    llm: LLM
    pubmed: PubMedClient
    gmail: Callable[[], GmailSender]  # built only after approval, so a rejected run never loads credentials
    embedder: Embedder | None = None  # None loads the configured model
    ask: Callable[[str], str] = input
    show: Callable[[str], None] = print


class _Stop(Exception):
    pass


def run(question: str, *, settings: Settings, services: Services, scenario: str | None = None,
        mock: bool = False) -> RunState:
    state = RunState(run_id=new_run_id(), question=question, scenario=scenario, mock=mock)
    with run_span(question, run_id=state.run_id, scenario=scenario, mock=mock) as root:
        try:
            _pipeline(state, settings, services)
        except _Stop:
            pass
        except ScreenerError as e:  # quota, credentials, Gemini unavailable: stop, send nothing
            _stop(state, settings, services, f"{e.error_class}: {e}")
        finally:
            state.llm_calls = services.llm.requests_made
            save_run(state, settings.runs_dir)
            root.end(outputs={"stage": state.stage, "stop_reason": state.stop_reason,
                              "approval": state.approval.decision if state.approval else None,
                              "delivery": state.delivery.status, "llm_calls": state.llm_calls})
    return state


def _stop(state: RunState, settings: Settings, services: Services, reason: str) -> None:
    state.stage, state.stop_reason = "stopped", reason
    save_run(state, settings.runs_dir)
    services.show(f"Stopped: {reason}\nNothing was sent.")


def _pipeline(state: RunState, settings: Settings, services: Services) -> None:
    save = lambda s: save_run(s, settings.runs_dir)  # noqa: E731
    on_error = state.errors.append

    def stop(reason: str):
        _stop(state, settings, services, reason)
        raise _Stop

    if not valid_recipient(settings.recipient_email):
        stop("RECIPIENT_EMAIL in .env is missing or not a single valid email address.")

    # 1. Intake (ADR 0003). Ended input means no clarification.
    save(state)
    intake = run_intake(state.question, llm=services.llm, settings=settings,
                        ask=lambda prompt: read_answer(services.ask, prompt), on_error=on_error)
    state.intake, state.intake_scope_filled, state.clarification = intake.form, intake.scope_filled, intake.clarification
    state.intake_correction = intake.correction
    if intake.stop_reason:
        stop(intake.stop_reason)
    form = intake.form
    interpretation = describe_interpretation(form, intake.scope_filled, intake.correction)
    services.show(f"Interpreted question:\n{interpretation}")
    sources = route_sources(form)
    notices: list[ReviewWarning] = []
    if form.recency and "pubmed" not in sources:
        notices.append(ReviewWarning(kind="notice", message="'Recent' applies to the PubMed date window only; "
                                                            "it was not applied to the synthetic dataset."))

    # 2. Retrieval, sources kept separate (ADR 0011, ADR 0012)
    state.stage = "retrieval"
    save(state)
    records: list[Record] = []
    if "synthetic" in sources:
        found, state.synthetic_search = retrieve_synthetic(state.question, settings=settings, embedder=services.embedder)
        # All are screened, so show them in record-ID order; scores stay in the trace. PubMed keeps relevance order.
        records += sorted(found, key=lambda r: r.record_id)
    if "pubmed" in sources:
        try:
            found, state.pubmed_search = search_pubmed(form, state.search_date, settings=settings,
                                                       client=services.pubmed, on_error=on_error)
            records += found
        except TransientError as e:
            if sources == ["pubmed"]:
                stop(f"PubMed unavailable after retries ({e}); there is nothing to screen.")
            lo, hi = date_window(state.search_date, form.recency, settings)
            state.pubmed_search = PubmedSearch(query=build_query(), min_date=lo, max_date=hi, status="unavailable")
            notices.append(ReviewWarning(kind="source_unavailable", source="pubmed",
                                         message="PubMed unavailable after retries: synthetic results only."))
    state.records = records

    # 3. Eligibility (ADR 0004)
    state.stage = "eligibility"
    save(state)
    if records:
        state.extractions, state.decisions = screen_records(records, form, llm=services.llm, settings=settings,
                                                            on_error=on_error)
    by_id = {r.record_id: r for r in records}
    included = {d.record_id: by_id[d.record_id] for d in state.decisions if d.decision == "include"}
    services.show(f"Screened {len(records)} records: {len(included)} included.")

    # 4. Drafting rounds and human review (ADR 0005, ADR 0006, ADR 0007)
    instruction: str | None = None
    previous: SummaryDraft | None = None
    for round_number in range(1, settings.max_change_requests + 2):
        state.round, state.stage = round_number, "summarizer"
        save(state)
        summary, round_warnings = SummaryDraft(study_details=[], claims=[]), []
        if included:  # no Summarizer call without included studies
            result = run_round(round_number, included, form, decisions=state.decisions, llm=services.llm,
                               settings=settings, on_error=on_error, instruction=instruction, previous=previous)
            state.drafts += result.drafts
            summary, round_warnings = result.final.summary, result.warnings
        state.warnings = notices + round_warnings
        state.email = _compose(state, settings, summary, by_id, sources, interpretation)
        state.stage = "human_review"
        save(state)
        outcome = human_review(state.email, round_number=round_number,
                               changes_left=settings.max_change_requests - (round_number - 1),
                               notes=[w.message for w in state.warnings], ask=services.ask, show=services.show)
        state.email = outcome.email  # includes any recipient or subject edits
        if outcome.choice == "request_changes":
            state.change_requests.append(ChangeRequest(round=round_number, instruction=outcome.instruction))
            instruction, previous = outcome.instruction, summary
            continue
        state.approval = approval_record(outcome, state.warnings, round_number)
        save(state)
        break

    if state.approval.decision == "rejected":
        state.stage = "done"
        services.show("Rejected. Nothing was sent.")
        return

    # 5. Send exactly the approved version, at most once (ADR 0013)
    state.stage = "sending"
    save(state)
    try:
        sender = services.gmail()
        send_approved(state, sender=sender, save=save)
    except ScreenerError as e:  # credentials could not be loaded: Gmail was never called
        state.delivery = state.delivery.model_copy(update={"status": "failed", "error": str(e)})
    except SendBlocked as e:
        services.show(f"Send blocked: {e}")
    state.stage = "done"
    d = state.delivery
    services.show(f"Delivery: {d.status}" + (f" (Gmail message {d.gmail_message_id})" if d.gmail_message_id else "")
                  + (f" - {d.error}" if d.error else ""))
    if d.status == "unknown":
        services.show(f"The send result is uncertain. Check your inbox for run {state.run_id} before trying again.")


def _compose(state: RunState, settings: Settings, summary: SummaryDraft, by_id: dict[str, Record],
             sources: list, interpretation: str) -> EmailContent:
    pubmed_lines = (pubmed_header(state.pubmed_search, state.intake.recency, settings.recency_years)
                    if state.pubmed_search else None)
    body = render_email_body(
        question=state.question,
        interpretation=interpretation,
        summary_text=render_summary(summary, by_id, state.decisions, sources),
        limitations=email_limitations(state.warnings),
        evidence_text=render_evidence_table(summary, by_id, state.decisions, sources, pubmed_lines),
        run_id=state.run_id,
    )
    previous = state.email  # keep the reviewer's recipient and subject edits across rounds
    return EmailContent(
        recipient=previous.recipient if previous else settings.recipient_email,
        subject=previous.subject if previous else default_subject(state.question, str(state.search_date), state.run_id),
        body=body,
    )
