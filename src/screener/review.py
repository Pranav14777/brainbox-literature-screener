"""Human review in the CLI (ADR 0007).

The reviewer sees the exact email (recipient, subject, body with summary,
warnings, compact evidence table and citations) once, and again only after an
edit changes it. An invalid choice prints a one-line hint. The reviewer chooses:
- approve: records an approval of this exact email's fingerprint
- reject: records a rejection; nothing is sent
- request changes: an instruction for the Summarizer (only while change requests remain)
- edit recipient or subject: applied directly, then the updated email is shown again for approval
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal

from screener.models import ApprovalRecord, EmailContent, ReviewWarning

_EMAIL = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+$")
# Kinds that make an approval "approved_with_warnings" (ADR 0006, ADR 0007).
_SERIOUS = {"critic_issue", "critic_unavailable", "removed_unsupported", "missing_comparison", "source_unavailable"}


def valid_recipient(address: str) -> bool:
    """A single, correctly formatted email address (ADR 0013)."""
    return bool(_EMAIL.match(address.strip()))


def read_answer(ask: Callable[[str], str], prompt: str) -> str | None:
    """One answer from the human, or None if input ended (EOF, Ctrl+C).

    Strips the byte-order mark that Windows PowerShell adds to piped input.
    """
    try:
        return ask(prompt).replace("﻿", "").strip()
    except (EOFError, KeyboardInterrupt):
        return None


@dataclass
class ReviewOutcome:
    choice: Literal["approve", "reject", "request_changes"]
    email: EmailContent  # the exact version shown when the choice was made
    instruction: str | None = None
    no_input: bool = False  # input ended before a choice: treated as no approval


NOTES_HEADING = "Reviewer notes (not sent)"


def review_screen(email: EmailContent, *, round_number: int, changes_left: int, notes: list[str]) -> str:
    """The exact email, then the reviewer notes in a separate block outside the email body (ADR 0007)."""
    bar = "=" * 72
    return "\n".join([
        bar, f"HUMAN REVIEW (round {round_number}; change requests left: {changes_left})",
        "EMAIL TO BE SENT", bar,
        f"To:      {email.recipient}", f"Subject: {email.subject}", "-" * 72, email.body, bar,
        f"{NOTES_HEADING}:", *([f"- {n}" for n in notes] or ["- none"]), bar,
    ])


def human_review(
    email: EmailContent,
    *,
    round_number: int,
    changes_left: int,
    notes: list[str] | None = None,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> ReviewOutcome:
    notes = notes or []
    options = {"a": "approve and send", "r": "reject (send nothing)", "e": "edit recipient", "s": "edit subject"}
    if changes_left > 0:
        options["c"] = "request changes to the summary"
    keys = list(options)
    menu = "  ".join(f"[{k}] {v}" for k, v in options.items())
    invalid = f"Please choose {', '.join(keys[:-1])} or {keys[-1]}"

    def screen() -> str:
        return review_screen(email, round_number=round_number, changes_left=changes_left, notes=notes)

    show(screen())
    prompt = f"{menu}\n> "
    while True:
        choice = read_answer(ask, prompt)
        if choice is None:
            show("No input received: treated as no approval. Nothing will be sent.")
            return ReviewOutcome("reject", email, no_input=True)
        choice = choice.lower()
        if choice not in options:
            show(invalid)  # the screen is not reprinted
            prompt = "> "
            continue
        prompt = f"{menu}\n> "
        if choice == "a":
            return ReviewOutcome("approve", email)
        if choice == "r":
            return ReviewOutcome("reject", email)
        if choice == "c":
            instruction = read_answer(ask, "Describe the change to the summary:\n> ")
            if instruction:
                return ReviewOutcome("request_changes", email, instruction)
            show("No instruction given; nothing changed.")
        elif choice == "e":
            new = read_answer(ask, "New recipient email address:\n> ") or ""
            if valid_recipient(new):
                email = email.model_copy(update={"recipient": new})
                show("Recipient updated. Review the email again before approving.")
                show(screen())
            else:
                show("Not a single valid email address; recipient unchanged.")
        elif choice == "s":
            new = read_answer(ask, "New subject:\n> ")
            if new:
                email = email.model_copy(update={"subject": new})
                show("Subject updated. Review the email again before approving.")
                show(screen())


def approval_record(outcome: ReviewOutcome, warnings: list[ReviewWarning], round_number: int) -> ApprovalRecord:
    if outcome.choice == "reject":
        decision = "rejected"
    elif any(w.kind in _SERIOUS for w in warnings):
        decision = "approved_with_warnings"
    else:
        decision = "approved"
    return ApprovalRecord(decision=decision, fingerprint=outcome.email.fingerprint(), round=round_number)
