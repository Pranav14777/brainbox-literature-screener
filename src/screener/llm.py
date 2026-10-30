"""Gemini client wrapper (ADR 0009, ADR 0013, ADR 0014).

LLM.generate() is the only way agents call the model. It:
- throttles requests to stay under the free-tier RPM
- retries transient errors with backoff; the SDK's own retries are switched off
- validates the response against the agent's Pydantic schema
- records model, thinking level, prompt version, tokens, cache hits and errors
- uses the disk cache only when settings.use_llm_cache is on (development)

The network call sits behind a Transport, so fakes can replace it in tests and
evaluation scenarios while retries, throttling and validation still run for real.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from screener.config import Settings
from screener.errors import (
    InvalidOutputError,
    ScreenerError,
    TransientError,
    classify_http,
    parse_retry_delay,
    with_retries,
)
from screener.models import ErrorEvent
from screener.tracing import add_metadata, traced

S = TypeVar("S", bound=BaseModel)


@dataclass
class RawResponse:
    text: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0


class Transport(Protocol):
    def __call__(
        self, *, model: str, system: str, user: str, schema: type[BaseModel], thinking_level: str
    ) -> RawResponse: ...


def classify_genai_error(e: genai_errors.APIError) -> ScreenerError:
    text = f"{e.message or ''} {e.details}"
    return classify_http(e.code or 0, text, parse_retry_delay(text))


def gemini_transport(settings: Settings) -> Transport:
    """The real transport. The API key is read here and never leaves this closure."""
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(
            timeout=int(settings.llm_timeout_s * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),  # retries are ours alone
        ),
    )

    def call(*, model, system, user, schema, thinking_level) -> RawResponse:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    thinking_config=types.ThinkingConfig(thinking_level=thinking_level.upper()),
                ),
            )
        except genai_errors.APIError as e:
            raise classify_genai_error(e) from None
        except httpx.TimeoutException:
            raise TransientError("Gemini request timed out") from None
        except httpx.TransportError as e:
            raise TransientError(f"Gemini connection error: {type(e).__name__}") from None
        u = resp.usage_metadata
        return RawResponse(
            text=resp.text,
            input_tokens=(u.prompt_token_count or 0) if u else 0,
            output_tokens=(u.candidates_token_count or 0) if u else 0,
            thinking_tokens=(u.thoughts_token_count or 0) if u else 0,
        )

    return call


class LLM:
    def __init__(
        self,
        settings: Settings,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self._transport = transport or gemini_transport(settings)
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._lock = threading.Lock()  # throttle slot and request counter
        self._cache_lock = threading.Lock()
        self.requests_made = 0  # quota consumed, including failed attempts

    def generate(
        self,
        *,
        agent: str,
        prompt_version: str,
        system: str,
        user: str,
        schema: type[S],
        thinking_level: str | None = None,
        on_error: Callable[[ErrorEvent], None] | None = None,
    ) -> S:
        """Return the parsed response, or raise InvalidOutputError / a non-transient ScreenerError."""
        level = thinking_level or self.settings.thinking_level
        model = self.settings.gemini_model
        cache_path = None
        if self.settings.use_llm_cache:
            key = hashlib.sha256(
                json.dumps(
                    [model, level, system, user, schema.__name__, schema.model_json_schema()], sort_keys=True
                ).encode("utf-8")
            ).hexdigest()
            cache_path = self.settings.llm_cache_dir / f"{key}.json"
            cached = self._cache_read(cache_path)
            if cached is not None:
                try:
                    parsed = schema.model_validate_json(cached)
                    add_metadata(cache_hit=True)
                    return parsed
                except ValidationError:
                    pass  # a stale or damaged entry is a miss

        text = self._call_model(
            agent=agent, model=model, thinking_level=level, prompt_version=prompt_version,
            system=system, user=user, schema=schema, on_error=on_error,
        )["text"]
        try:
            parsed = schema.model_validate_json(text or "")
        except ValidationError as e:
            raise InvalidOutputError(f"{agent}: output failed its schema ({e.error_count()} errors)") from None

        if cache_path is not None:  # only valid outputs are cached; errors never are
            self._cache_write(cache_path, text)
        return parsed

    def _cache_read(self, path) -> str | None:
        with self._cache_lock:
            try:
                return json.loads(path.read_text(encoding="utf-8"))["text"]
            except (OSError, ValueError, KeyError):
                return None

    def _cache_write(self, path, text: str) -> None:
        # Write-then-replace under a lock, so concurrent calls never see a half-written entry.
        # The cache is optional: a failed write is ignored.
        with self._cache_lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"text": text}), encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                pass

    @traced("gemini", run_type="llm")
    def _call_model(self, *, agent, model, thinking_level, prompt_version, system, user, schema, on_error) -> dict:
        """The LLM span. LangSmith reads token counts from usage_metadata in its outputs."""
        add_metadata(
            ls_provider="google_genai", ls_model_name=model, agent=agent,
            thinking_level=thinking_level, prompt_version=prompt_version, cache_hit=False,
        )
        try:
            raw = with_retries(
                lambda: self._request(model=model, system=system, user=user, schema=schema,
                                      thinking_level=thinking_level),
                stage=f"llm.{agent}", settings=self.settings, on_error=on_error, sleep=self._sleep,
            )
        except ScreenerError as e:
            add_metadata(error_class=e.error_class)
            raise
        return {
            "text": raw.text,
            "usage_metadata": {
                "input_tokens": raw.input_tokens,
                "output_tokens": raw.output_tokens + raw.thinking_tokens,
                "total_tokens": raw.input_tokens + raw.output_tokens + raw.thinking_tokens,
                "output_token_details": {"reasoning": raw.thinking_tokens},
            },
        }

    def _request(self, **kwargs) -> RawResponse:
        # Reserve a start slot under the lock, then wait outside it, so concurrent
        # callers queue up at 60/RPM-second spacing without blocking each other's calls.
        interval = 60.0 / self.settings.llm_requests_per_minute
        with self._lock:
            now = self._clock()
            start = now if self._last_request is None else max(now, self._last_request + interval)
            self._last_request = start
            self.requests_made += 1
        if start > now:
            self._sleep(start - now)
        return self._transport(**kwargs)
