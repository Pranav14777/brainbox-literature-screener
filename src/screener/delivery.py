"""Send gate and Gmail delivery (ADR 0007, ADR 0013).

send_approved() is the only path to Gmail. It refuses unless:
- an approval (approved or approved_with_warnings) is recorded, and
- the email's fingerprint equals the approved fingerprint, and
- delivery status is not_sent, or failed for this same fingerprint (a manual retry).

Status "sending" is saved before Gmail is called. Only known rejections
(400, 401, 403, 429) count as failed, meaning nothing was sent. Any other error,
timeout or dropped connection is "unknown", which blocks resending until the
human has checked the inbox for the run ID. There are no automatic retries.
"""
from __future__ import annotations

import base64
import os
from email.message import EmailMessage
from typing import Callable, Protocol

from langsmith import trace

from screener.config import PROJECT_ROOT, Settings
from screener.errors import BadCredentialsError
from screener.models import Delivery, EmailContent, RunState, utc_now

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
KNOWN_REJECTIONS = {400, 401, 403, 429}


class GmailRejected(Exception):
    """Gmail definitely did not send: a known rejection, or credentials failed before the request."""


class SendBlocked(Exception):
    """The send gate refused; Gmail was not called."""


class GmailSender(Protocol):
    def send(self, raw_message: bytes) -> str: ...


def build_mime(email: EmailContent) -> bytes:
    msg = EmailMessage()
    msg["To"] = email.recipient
    msg["Subject"] = email.subject
    msg.set_content(email.body)  # plain text only (ADR 0013)
    return msg.as_bytes()


class GmailApiSender:
    """Real sender. Credentials are loaded here and never passed to traced functions (ADR 0014)."""

    def __init__(self, settings: Settings):
        import google_auth_httplib2
        import httplib2
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token_file = PROJECT_ROOT / os.environ.get("GMAIL_TOKEN_FILE", "token.json")
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), GMAIL_SCOPES)
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
        except (OSError, ValueError, RefreshError) as e:
            raise BadCredentialsError(f"Gmail credentials unavailable ({type(e).__name__}); re-run OAuth") from None
        http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=settings.gmail_timeout_s))
        self._service = build("gmail", "v1", http=http, cache_discovery=False)

    def send(self, raw_message: bytes) -> str:
        from google.auth.exceptions import RefreshError
        from googleapiclient.errors import HttpError

        body = {"raw": base64.urlsafe_b64encode(raw_message).decode("ascii")}
        try:
            resp = self._service.users().messages().send(userId="me", body=body).execute(num_retries=0)
        except HttpError as e:
            if e.resp.status in KNOWN_REJECTIONS:
                raise GmailRejected(f"Gmail rejected the message (HTTP {e.resp.status})") from None
            raise  # anything else may or may not have been sent: unknown
        except RefreshError:
            raise GmailRejected("Gmail credentials rejected before sending; re-run OAuth") from None
        return resp["id"]


def _set(state: RunState, save: Callable[[RunState], None], **fields) -> None:
    state.delivery = state.delivery.model_copy(update={**fields, "updated_at": utc_now()})
    save(state)


def check_gate(state: RunState) -> str:
    """Return the fingerprint to send, or raise SendBlocked with the reason."""
    if state.approval is None or state.approval.decision not in ("approved", "approved_with_warnings"):
        raise SendBlocked("no approval recorded for this email")
    if state.email is None:
        raise SendBlocked("no email to send")
    fingerprint = state.email.fingerprint()
    if fingerprint != state.approval.fingerprint:
        raise SendBlocked("the email differs from the approved version; it needs approval again")
    status = state.delivery.status
    if status == "sent":
        raise SendBlocked(f"already sent (Gmail message {state.delivery.gmail_message_id})")
    if status in ("sending", "unknown"):
        raise SendBlocked(f"delivery status is '{status}': check your inbox for run {state.run_id} "
                          "before sending again")
    if status == "failed" and state.delivery.fingerprint not in (None, fingerprint):
        raise SendBlocked("a previous failed send was for a different version")
    return fingerprint


def send_approved(state: RunState, *, sender: GmailSender, save: Callable[[RunState], None]) -> Delivery:
    """Send the approved email at most once. Gmail is called only after 'sending' is saved."""
    with trace(name="send", run_type="tool", inputs={"run_id": state.run_id}) as span:
        try:
            fingerprint = check_gate(state)
        except SendBlocked as e:
            span.end(outputs={"blocked": str(e), "status": state.delivery.status})
            raise
        _set(state, save, status="sending", fingerprint=fingerprint, error=None)
        try:
            message_id = sender.send(build_mime(state.email))
        except GmailRejected as e:
            _set(state, save, status="failed", error=str(e))
        except Exception as e:  # timeout, dropped connection, 5xx: may or may not have been sent
            _set(state, save, status="unknown", error=f"{type(e).__name__}: outcome uncertain")
        else:
            _set(state, save, status="sent", gmail_message_id=message_id)
        span.end(outputs={"status": state.delivery.status, "gmail_message_id": state.delivery.gmail_message_id,
                          "fingerprint": fingerprint, "error": state.delivery.error})
    return state.delivery
