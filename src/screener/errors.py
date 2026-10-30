"""Error classes and the retry policy (ADR 0013).

Only transient errors are retried. Invalid requests, bad credentials and an
exhausted daily quota fail at once with a clear message. Empty results are not
errors at all. Invalid LLM output has its own one-retry rule in the agents
(ADR 0002) and is never retried here.
"""
from __future__ import annotations

import random
import re
import time
from typing import Callable, TypeVar

from langsmith import trace

from screener.config import Settings
from screener.models import ErrorClass, ErrorEvent


class ScreenerError(Exception):
    error_class: ErrorClass = "invalid_request"


class TransientError(ScreenerError):
    error_class = "transient"

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class InvalidRequestError(ScreenerError):
    error_class = "invalid_request"


class BadCredentialsError(ScreenerError):
    error_class = "bad_credentials"


class QuotaExhaustedError(ScreenerError):
    error_class = "quota_exhausted"


class InvalidOutputError(ScreenerError):
    error_class = "invalid_output"


_DAILY_QUOTA = re.compile(r"per\s*day|perday|daily", re.IGNORECASE)
_INVALID_KEY = re.compile(r"api[_ ]key[_ ]invalid|api key not valid", re.IGNORECASE)
_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


def parse_retry_delay(text: str) -> float | None:
    m = _RETRY_DELAY.search(text)
    return float(m.group(1)) if m else None


def classify_http(status: int, message: str = "", retry_after: float | None = None) -> ScreenerError:
    """Map an HTTP status (and error text) to one of the ADR 0013 error classes."""
    summary = f"HTTP {status}"
    if status == 429:
        if _DAILY_QUOTA.search(message):
            return QuotaExhaustedError(f"{summary}: daily quota exhausted")
        return TransientError(f"{summary}: rate limited", retry_after)
    if status >= 500:
        return TransientError(f"{summary}: server error", retry_after)
    if status in (401, 403) or (status == 400 and _INVALID_KEY.search(message)):
        return BadCredentialsError(f"{summary}: credentials rejected")
    return InvalidRequestError(f"{summary}: request rejected")


T = TypeVar("T")


def backoff_delay(error: TransientError, attempt: int, settings: Settings) -> float:
    if error.retry_after is not None:
        return min(error.retry_after, settings.retry_after_cap_s)
    base = settings.backoff_base_s * 2 ** (attempt - 1)  # 1s, 2s, 4s
    return base + random.uniform(0, settings.backoff_base_s)


def with_retries(
    fn: Callable[[], T],
    *,
    stage: str,
    settings: Settings,
    on_error: Callable[[ErrorEvent], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run fn, retrying transient errors with backoff. Each attempt is its own span (ADR 0014)."""
    for attempt in range(1, settings.max_attempts + 1):
        try:
            with trace(name=f"{stage}.attempt", run_type="tool", inputs={"attempt": attempt}):
                return fn()
        except ScreenerError as e:
            if on_error:
                on_error(ErrorEvent(stage=stage, error_class=e.error_class, message=str(e), attempt=attempt))
            if not isinstance(e, TransientError) or attempt == settings.max_attempts:
                raise
            sleep(backoff_delay(e, attempt, settings))
    raise AssertionError("unreachable")
