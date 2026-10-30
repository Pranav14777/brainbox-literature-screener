"""One-time Gmail OAuth setup (ADR 0013): creates token.json from credentials.json.

Opens a browser to sign in to your Gmail test account and grant the gmail.send
scope. Sends nothing. Re-run it when the token expires: OAuth apps in "Testing"
mode issue refresh tokens that last 7 days.

Usage: python scripts/gmail_auth.py
"""
from __future__ import annotations

import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from screener.config import PROJECT_ROOT
from screener.delivery import GMAIL_SCOPES


def main() -> None:
    credentials_file = PROJECT_ROOT / os.environ.get("GMAIL_CREDENTIALS_FILE", "credentials.json")
    token_file = PROJECT_ROOT / os.environ.get("GMAIL_TOKEN_FILE", "token.json")
    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), GMAIL_SCOPES)
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
        except (ValueError, RefreshError):
            creds = None  # expired or revoked: sign in again
    if not creds or not creds.valid:
        if not credentials_file.exists():
            raise SystemExit(f"{credentials_file.name} not found: download the OAuth client file first (see README).")
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), GMAIL_SCOPES)
        creds = flow.run_local_server(port=0)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    print(f"Gmail authorised: {token_file.name} saved (scope gmail.send). Nothing was sent.")


if __name__ == "__main__":
    main()
