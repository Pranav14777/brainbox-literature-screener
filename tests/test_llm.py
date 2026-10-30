import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel

from screener.config import load_settings
from screener.errors import (
    BadCredentialsError,
    InvalidOutputError,
    InvalidRequestError,
    QuotaExhaustedError,
    TransientError,
)
from screener.fakes import FakeLLMTransport
from screener.llm import LLM, RawResponse, classify_genai_error


class Ping(BaseModel):
    answer: int


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now


def make(transport, **overrides):
    ft = FakeTime()
    return LLM(load_settings(**overrides), transport=transport, sleep=ft.sleep, clock=ft.clock), ft


def gen(llm, on_error=None):
    return llm.generate(agent="test", prompt_version="v1", system="s", user="u", schema=Ping, on_error=on_error)


def test_valid_response_is_parsed():
    llm, ft = make(FakeLLMTransport({Ping: ['{"answer": 5}']}))
    assert gen(llm) == Ping(answer=5)
    assert llm.requests_made == 1
    assert ft.slept == []


@pytest.mark.parametrize("text", ['{"answer": "five"}', "not json", ""])
def test_invalid_output_raises(text):
    llm, _ = make(FakeLLMTransport({Ping: [text]}))
    with pytest.raises(InvalidOutputError):
        gen(llm)


def test_temporary_failure_retries_once_then_succeeds():
    events = []
    llm, ft = make(FakeLLMTransport({Ping: ['{"answer": 5}']}, failure="fail_once"))
    assert gen(llm, on_error=events.append) == Ping(answer=5)
    assert [(e.error_class, e.attempt) for e in events] == [("transient", 1)]
    assert 1.0 <= ft.slept[0] < 2.0  # first backoff: 1s plus jitter
    assert sum(ft.slept) == pytest.approx(4.0)  # throttle tops up to the 15 RPM spacing
    assert llm.requests_made == 2


def test_persistent_failure_stops_after_three_attempts():
    events = []
    llm, _ = make(FakeLLMTransport({Ping: ['{"answer": 5}']}, failure="always"))
    with pytest.raises(TransientError):
        gen(llm, on_error=events.append)
    assert [e.attempt for e in events] == [1, 2, 3]
    assert llm.requests_made == 3


def test_daily_quota_stops_without_retry():
    llm, ft = make(FakeLLMTransport({Ping: ['{"answer": 5}']}, failure="quota"))
    with pytest.raises(QuotaExhaustedError):
        gen(llm)
    assert llm.requests_made == 1
    assert ft.slept == []


def test_retry_after_is_honoured_and_capped():
    calls = []

    def transport(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise TransientError("rate limited", retry_after=120)
        return RawResponse(text='{"answer": 1}')

    llm, ft = make(transport)
    gen(llm)
    assert ft.slept[0] == 60.0


def test_throttle_spaces_requests():
    llm, ft = make(FakeLLMTransport({Ping: ['{"answer": 5}']}))
    gen(llm)
    gen(llm)
    assert ft.slept == [4.0]


def test_cache_serves_repeat_calls_and_never_stores_invalid_output(tmp_path):
    llm, _ = make(FakeLLMTransport({Ping: ['{"answer": 5}']}), use_llm_cache=True, llm_cache_dir=tmp_path)
    gen(llm)
    gen(llm)
    assert llm.requests_made == 1

    bad_dir = tmp_path / "bad"
    llm, _ = make(FakeLLMTransport({Ping: ["oops"]}), use_llm_cache=True, llm_cache_dir=bad_dir)
    with pytest.raises(InvalidOutputError):
        gen(llm)
    assert not bad_dir.exists()


def api_error(cls, code, message, details=None):
    body = {"error": {"code": code, "message": message, "status": "X"}}
    if details:
        body["error"]["details"] = details
    return cls(code, body)


def test_classify_gemini_errors():
    daily = api_error(genai_errors.ClientError, 429,
                      "Quota exceeded for quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    assert isinstance(classify_genai_error(daily), QuotaExhaustedError)

    minute = classify_genai_error(api_error(
        genai_errors.ClientError, 429,
        "Quota exceeded for quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
        [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"}],
    ))
    assert isinstance(minute, TransientError) and minute.retry_after == 34.0

    assert isinstance(classify_genai_error(api_error(genai_errors.ServerError, 503, "overloaded")), TransientError)
    assert isinstance(
        classify_genai_error(api_error(genai_errors.ClientError, 400, "API key not valid. Please pass a valid API key.")),
        BadCredentialsError,
    )
    assert isinstance(classify_genai_error(api_error(genai_errors.ClientError, 403, "denied")), BadCredentialsError)
    assert isinstance(classify_genai_error(api_error(genai_errors.ClientError, 400, "bad schema")), InvalidRequestError)
