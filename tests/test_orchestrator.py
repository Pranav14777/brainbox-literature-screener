"""End-to-end orchestration with every side effect faked (ADR 0002, ADR 0015)."""
import re

import pytest

from screener.config import load_settings
from screener.fakes import FakeGmailSender, FakeLLMTransport, fake_pubmed_http
from screener.llm import LLM
from screener.models import CriticReport, ExtractedFacts, IntakeForm, SummaryDraft
from screener.orchestrator import Services, run
from screener.retrieval.pubmed import PubMedClient
from screener.rules import UNCLEAR_FACTS
from screener.runstore import load_run
from test_agents import FORM, NOT_STATED
from test_drafting import APPROVE, GOOD
from test_llm import FakeTime
from test_retrieval import RecordingEmbedder
from test_rules import SYNTHETIC_FACTS

Q2 = "Which synthetic studies qualify under the screening rules?"


def facts(user):
    rid = re.search(r'record_id="(\w+)"', user).group(1)
    return SYNTHETIC_FACTS.get(rid, UNCLEAR_FACTS)


class Harness:
    def __init__(self, tmp_path, *, intake=NOT_STATED, answers=("a",), llm_failure=None, pubmed_mode="ok",
                 gmail_mode="ok", summaries=(GOOD,), reports=(APPROVE,), recipient="me@example.com"):
        self.settings = load_settings(runs_dir=tmp_path, recipient_email=recipient)
        ft = FakeTime()
        self.transport = FakeLLMTransport(
            {IntakeForm: [intake], ExtractedFacts: facts, SummaryDraft: list(summaries), CriticReport: list(reports)},
            failure=llm_failure)
        self.gmail = FakeGmailSender(gmail_mode)
        self.gmail_built = 0
        self.shown: list[str] = []
        answers = iter(answers)

        def gmail_factory():
            self.gmail_built += 1
            return self.gmail

        def ask(prompt):  # like input(): EOFError once the scripted answers run out
            try:
                return next(answers)
            except StopIteration:
                raise EOFError from None

        self.services = Services(
            llm=LLM(self.settings, transport=self.transport, sleep=ft.sleep, clock=ft.clock),
            pubmed=PubMedClient(self.settings, http=fake_pubmed_http(pubmed_mode), sleep=lambda s: None),
            gmail=gmail_factory, embedder=RecordingEmbedder(), ask=ask, show=self.shown.append,
        )

    def run(self, question=Q2):
        state = run(question, settings=self.settings, services=self.services, mock=True)
        assert load_run(self.settings.runs_dir, state.run_id) == state  # run file always saved
        return state

    def schemas(self):
        return [c["schema"] for c in self.transport.calls]


def test_normal_synthetic_run_approved_sends_once(tmp_path):
    h = Harness(tmp_path)
    state = h.run()
    assert state.stage == "done" and state.approval.decision == "approved"
    assert state.delivery.status == "sent" and len(h.gmail.sent) == 1
    assert {d.record_id: d.decision for d in state.decisions}["SYN004"] == "exclude"
    assert h.schemas().count("ExtractedFacts") == 7  # SYN004 excluded on metadata, no call
    assert state.llm_calls == len(h.transport.calls)
    assert state.email.fingerprint() == state.approval.fingerprint
    body = state.email.body
    assert "population: not stated; using the tool's scope" in body and f"Run ID: {state.run_id}" in body


def test_synthetic_records_are_shown_in_record_id_order(tmp_path):
    state = Harness(tmp_path, answers=("r",)).run()
    assert [d.record_id for d in state.decisions] == [f"SYN00{i}" for i in range(1, 9)]


def test_input_ending_at_review_sends_nothing(tmp_path):
    h = Harness(tmp_path, answers=())
    state = h.run()
    assert state.approval.decision == "rejected" and state.stage == "done"
    assert h.gmail_built == 0 and h.gmail.sent == []


def test_rejection_sends_nothing_and_never_loads_gmail(tmp_path):
    h = Harness(tmp_path, answers=("r",))
    state = h.run()
    assert state.approval.decision == "rejected" and state.delivery.status == "not_sent"
    assert h.gmail_built == 0 and h.gmail.sent == []


def test_change_request_then_approve_keeps_recipient_edit(tmp_path):
    h = Harness(tmp_path, answers=("e", "new@example.com", "c", "Mention the follow-up periods.", "a"))
    state = h.run()
    assert [c.instruction for c in state.change_requests] == ["Mention the follow-up periods."]
    assert state.approval.round == 2 and state.email.recipient == "new@example.com"
    round2_summarizer = [c for c in h.transport.calls if c["schema"] == "SummaryDraft"][-1]
    assert "Mention the follow-up periods." in round2_summarizer["user"]
    assert len(h.gmail.sent) == 1


def test_out_of_scope_stops_before_retrieval(tmp_path):
    other = FORM.model_copy(update={"intervention": "other", "intervention_wording": "sitagliptin"})
    h = Harness(tmp_path, intake=other)
    state = h.run("Does sitagliptin reduce cardiovascular events?")
    assert state.stage == "stopped" and state.stop_reason.startswith("Out of scope")
    assert h.schemas() == ["IntakeForm"] and state.records == []


def test_missing_recipient_stops_before_any_llm_call(tmp_path):
    h = Harness(tmp_path, recipient="")
    state = h.run()
    assert state.stage == "stopped" and "RECIPIENT_EMAIL" in state.stop_reason and h.transport.calls == []


def test_pubmed_persistent_failure_with_both_sources_continues_with_warning(tmp_path):
    both = NOT_STATED.model_copy(update={"sources": "both"})
    h = Harness(tmp_path, intake=both, pubmed_mode="always")
    state = h.run("What do the synthetic abstracts and PubMed say?")
    assert state.pubmed_search.status == "unavailable"
    assert [w.kind for w in state.warnings][0] == "source_unavailable"
    body = state.email.body
    assert "PubMed was unavailable after retries" in body
    assert "LIMITATIONS\n- PubMed could not be searched (unavailable after retries)" in body
    assert "WARNINGS" not in body and "Query:" not in body
    assert any("Reviewer notes (not sent):\n- PubMed unavailable after retries" in s for s in h.shown)
    assert state.approval.decision == "approved_with_warnings" and len(h.gmail.sent) == 1


def test_pubmed_only_persistent_failure_stops(tmp_path):
    pubmed = FORM.model_copy(update={"sources": "pubmed"})
    h = Harness(tmp_path, intake=pubmed, pubmed_mode="always")
    state = h.run("Which PubMed trials investigate metformin and cardiovascular outcomes?")
    assert state.stage == "stopped" and "PubMed unavailable" in state.stop_reason and h.gmail.sent == []


def test_no_results_reaches_review_without_summarizer(tmp_path):
    pubmed = FORM.model_copy(update={"sources": "pubmed"})
    h = Harness(tmp_path, intake=pubmed, pubmed_mode="no_results", answers=("r",))
    state = h.run("Which PubMed trials investigate metformin and cardiovascular outcomes?")
    assert "No PubMed records matched the query in this date window." in state.email.body
    assert "Query:" not in state.email.body and state.pubmed_search.query  # the query stays in the run file
    assert "SummaryDraft" not in h.schemas() and state.approval.decision == "rejected"


@pytest.mark.parametrize("failure", ["quota", "always"])
def test_gemini_failure_stops_and_sends_nothing(tmp_path, failure):
    h = Harness(tmp_path, llm_failure=failure)
    state = h.run()
    assert state.stage == "stopped" and h.gmail_built == 0
    assert state.stop_reason.startswith("quota_exhausted" if failure == "quota" else "transient")


def test_uncertain_send_is_reported(tmp_path):
    h = Harness(tmp_path, gmail_mode="timeout")
    state = h.run()
    assert state.delivery.status == "unknown"
    assert any(f"Check your inbox for run {state.run_id}" in s for s in h.shown)
