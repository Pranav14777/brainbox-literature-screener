"""Block 7: send gate, delivery status and the CLI review loop. Gmail is always faked here."""
import email as email_lib

import pytest

from screener.delivery import SendBlocked, build_mime, send_approved
from screener.fakes import FakeGmailSender
from screener.models import EmailContent, ReviewWarning, RunState
from screener.render import default_subject, email_limitations, footer, render_email_body
from screener.review import approval_record, human_review, valid_recipient

RUN_ID = "20261029-120000-abcd"


def make_email():
    body = render_email_body(question="Q?", interpretation="intervention: metformin", summary_text="- a claim [SYN001]",
                             limitations=[], evidence_text="EVIDENCE TABLE", run_id=RUN_ID)
    return EmailContent(recipient="me@example.com", subject=default_subject("Q?", "2026-10-29", RUN_ID), body=body)


def scripted(*answers):
    it = iter(answers)
    shown: list[str] = []
    return (lambda prompt: next(it)), shown.append, shown


def approved_state(email=None, decision="approve"):
    email = email or make_email()
    state = RunState(run_id=RUN_ID, question="Q?", email=email)
    ask, show, _ = scripted("a" if decision == "approve" else "r")
    outcome = human_review(email, round_number=1, changes_left=2, ask=ask, show=show)
    state.approval = approval_record(outcome, [], 1)
    return state


class Saver:
    def __init__(self):
        self.statuses: list[str] = []

    def __call__(self, state):
        self.statuses.append(state.delivery.status)


# --- Email content ---

def test_mime_is_plain_text_with_run_id_footer():
    msg = email_lib.message_from_bytes(build_mime(make_email()))
    assert msg["To"] == "me@example.com" and f"[run {RUN_ID}]" in msg["Subject"]
    assert msg.get_content_type() == "text/plain" and not msg.is_multipart()
    assert footer(RUN_ID) in msg.get_payload(decode=True).decode()


@pytest.mark.parametrize("address,ok", [
    ("me@example.com", True), ("a@b.co", True), ("not-an-email", False),
    ("a@b.com, c@d.com", False), ("a@b.com;c@d.com", False), ("<a@b.com>", False), ("", False)])
def test_recipient_validation(address, ok):
    assert valid_recipient(address) is ok


# --- Send gate ---

def test_rejection_sends_nothing():
    state = approved_state(decision="reject")
    sender = FakeGmailSender()
    with pytest.raises(SendBlocked, match="no approval"):
        send_approved(state, sender=sender, save=Saver())
    assert sender.sent == [] and state.delivery.status == "not_sent"
    assert state.approval.decision == "rejected"


def test_approval_sends_exactly_once_and_saves_sending_first():
    state, sender, saver = approved_state(), FakeGmailSender(), Saver()
    delivery = send_approved(state, sender=sender, save=saver)
    assert delivery.status == "sent" and delivery.gmail_message_id == "fake-message-1"
    assert saver.statuses == ["sending", "sent"]
    with pytest.raises(SendBlocked, match="already sent"):
        send_approved(state, sender=sender, save=saver)
    assert len(sender.sent) == 1


def test_changed_email_after_approval_is_blocked():
    state = approved_state()
    state.email = state.email.model_copy(update={"recipient": "someone@else.com"})
    sender = FakeGmailSender()
    with pytest.raises(SendBlocked, match="needs approval again"):
        send_approved(state, sender=sender, save=Saver())
    assert sender.sent == []


def test_timeout_is_unknown_and_blocks_resend():
    state, saver = approved_state(), Saver()
    assert send_approved(state, sender=FakeGmailSender("timeout"), save=saver).status == "unknown"
    assert saver.statuses == ["sending", "unknown"]
    retry = FakeGmailSender()
    with pytest.raises(SendBlocked, match=f"check your inbox for run {RUN_ID}"):
        send_approved(state, sender=retry, save=saver)
    assert retry.sent == []


def test_server_error_is_unknown():
    assert send_approved(approved_state(), sender=FakeGmailSender("server_error"), save=Saver()).status == "unknown"


def test_known_rejection_is_failed_and_manual_retry_allowed():
    state = approved_state()
    assert send_approved(state, sender=FakeGmailSender("rejected"), save=Saver()).status == "failed"
    assert send_approved(state, sender=FakeGmailSender(), save=Saver()).status == "sent"


def test_crash_during_send_leaves_sending_which_blocks():
    state = approved_state()
    state.delivery = state.delivery.model_copy(update={"status": "sending"})
    with pytest.raises(SendBlocked, match="'sending'"):
        send_approved(state, sender=FakeGmailSender(), save=Saver())


# --- Review loop ---

def test_edit_recipient_then_approve_approves_the_edited_version():
    ask, show, shown = scripted("e", "bad address", "e", "new@example.com", "a")
    outcome = human_review(make_email(), round_number=1, changes_left=2, ask=ask, show=show)
    assert outcome.choice == "approve" and outcome.email.recipient == "new@example.com"
    assert "Not a single valid email address; recipient unchanged." in shown
    assert sum("HUMAN REVIEW" in s for s in shown) == 2  # shown again only after the email changed


def test_invalid_choice_prints_one_line_without_reprinting_the_screen():
    ask, show, shown = scripted("x", "zz", "a")
    outcome = human_review(make_email(), round_number=1, changes_left=2, ask=ask, show=show)
    assert outcome.choice == "approve"
    assert sum("HUMAN REVIEW" in s for s in shown) == 1
    assert shown.count("Please choose a, r, e, s or c") == 2


def test_edit_subject_keeps_footer_in_body():
    ask, show, _ = scripted("s", "My subject", "a")
    outcome = human_review(make_email(), round_number=1, changes_left=2, ask=ask, show=show)
    assert outcome.email.subject == "My subject" and footer(RUN_ID) in outcome.email.body


def test_request_changes_returns_instruction_and_is_hidden_when_none_left():
    ask, show, _ = scripted("c", "", "c", "Mention that outcomes differ.")
    outcome = human_review(make_email(), round_number=1, changes_left=1, ask=ask, show=show)
    assert outcome.choice == "request_changes" and outcome.instruction == "Mention that outcomes differ."

    ask, show, shown = scripted("c", "r")
    outcome = human_review(make_email(), round_number=3, changes_left=0, ask=ask, show=show)
    assert outcome.choice == "reject" and "Please choose a, r, e or s" in shown


def test_piped_byte_order_mark_is_ignored():
    outcome = human_review(make_email(), round_number=1, changes_left=2, ask=lambda p: "﻿a", show=lambda s: None)
    assert outcome.choice == "approve"


def test_end_of_input_at_review_is_no_approval():
    def eof(prompt):
        raise EOFError

    shown = []
    outcome = human_review(make_email(), round_number=1, changes_left=2, ask=eof, show=shown.append)
    assert outcome.choice == "reject" and outcome.no_input
    assert "No input received: treated as no approval. Nothing will be sent." in shown
    assert approval_record(outcome, [], 1).decision == "rejected"


def test_reviewer_notes_are_shown_outside_the_email_and_not_fingerprinted():
    email = make_email()
    notes = ['removed: unsupported claim "x" (numbers not in its quotes: 42)']
    shown = []
    outcome = human_review(email, round_number=1, changes_left=2, notes=notes, ask=lambda p: "a", show=shown.append)
    screen = shown[0]
    notes_block = screen.split("Reviewer notes (not sent):")[1]
    assert notes[0] in notes_block and notes[0] not in outcome.email.body
    assert screen.index(outcome.email.body) < screen.index("Reviewer notes (not sent):")
    assert outcome.email.fingerprint() == email.fingerprint()  # notes never change what is approved or sent


def test_email_limitations_are_fixed_sentences_for_three_cases_only():
    warnings = [
        ReviewWarning(kind="source_unavailable", source="pubmed", message="PubMed unavailable after retries"),
        ReviewWarning(kind="no_synthesis", message="No synthesis could be produced; see evidence table."),
        ReviewWarning(kind="missing_comparison", source="synthetic", message="no cross-study comparison ..."),
        ReviewWarning(kind="removed_unsupported", message='removed: unsupported claim "secret detail"'),
        ReviewWarning(kind="critic_issue", message="unresolved critic issue"),
        ReviewWarning(kind="critic_unavailable", message="Critic review unavailable"),
    ]
    assert email_limitations(warnings) == [
        "PubMed could not be searched (unavailable after retries), so its results are not included.",
        "No plain-language summary could be produced for the included studies; the evidence table lists them.",
        "The summary does not compare the direction of findings across the included synthetic dataset studies.",
    ]
    assert email_limitations(warnings[3:]) == []
    body = render_email_body(question="Q?", interpretation="i", summary_text="s", limitations=[],
                             evidence_text="E", run_id=RUN_ID)
    assert "LIMITATIONS" not in body and "WARNINGS" not in body


def test_approval_with_warnings_is_recorded():
    outcome = human_review(make_email(), round_number=1, changes_left=2, ask=lambda p: "a", show=lambda s: None)
    warn = [ReviewWarning(kind="critic_unavailable", message="Critic review unavailable")]
    assert approval_record(outcome, warn, 1).decision == "approved_with_warnings"
    notice = [ReviewWarning(kind="notice", message="recency not applied")]
    assert approval_record(outcome, notice, 1).decision == "approved"
