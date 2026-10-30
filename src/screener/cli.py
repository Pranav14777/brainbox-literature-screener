"""Command-line entry point.

    python -m screener "Which synthetic studies qualify under the screening rules?"
    python -m screener --resend <run_id>    # manual retry after a failed (not unknown) send
"""
from __future__ import annotations

import argparse
import sys

from langsmith import Client

from screener.config import load_settings
from screener.delivery import GmailApiSender, SendBlocked, send_approved
from screener.errors import ScreenerError
from screener.llm import LLM
from screener.orchestrator import Services, run
from screener.retrieval.pubmed import PubMedClient
from screener.runstore import load_run, run_path, save_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="screener", description="Clinical literature screener")
    parser.add_argument("question", nargs="?", help="research question (asked interactively if omitted)")
    parser.add_argument("--scenario", help="evaluation scenario tag for traces")
    parser.add_argument("--dev-cache", action="store_true", help="use the development LLM cache (never for eval or demo)")
    parser.add_argument("--resend", metavar="RUN_ID", help="retry sending an approved run whose delivery failed")
    args = parser.parse_args(argv)
    # Windows consoles and pipes may not encode every character in PubMed titles; never crash on output.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    settings = load_settings(use_llm_cache=args.dev_cache)

    try:
        if args.resend:
            return _resend(args.resend, settings)
        question = args.question or input("Research question: ")
        services = Services(llm=LLM(settings), pubmed=PubMedClient(settings), gmail=lambda: GmailApiSender(settings))
        state = run(question, settings=settings, services=services, scenario=args.scenario)
        print(f"Run file: {run_path(settings.runs_dir, state.run_id)}")
        return 0 if state.stage == "done" else 1
    finally:
        Client().flush()


def _resend(run_id: str, settings) -> int:
    state = load_run(settings.runs_dir, run_id)
    try:
        delivery = send_approved(state, sender=GmailApiSender(settings), save=lambda s: save_run(s, settings.runs_dir))
    except (SendBlocked, ScreenerError) as e:
        print(f"Send blocked: {e}")
        return 1
    print(f"Delivery: {delivery.status}" + (f" (Gmail message {delivery.gmail_message_id})" if delivery.gmail_message_id else ""))
    return 0 if delivery.status == "sent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
