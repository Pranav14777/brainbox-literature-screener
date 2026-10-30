from pydantic import BaseModel

from screener.tracing import REDACTED, redact


def test_secret_keys_redacted_but_token_counts_kept():
    out = redact({"api_key": "x", "refresh_token": "y", "input_tokens": 3, "nested": {"client_secret": "z"}})
    assert out == {"api_key": REDACTED, "refresh_token": REDACTED, "input_tokens": 3,
                   "nested": {"client_secret": REDACTED}}


def test_secret_values_from_environment_redacted_anywhere(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFAKESECRET123")
    assert redact({"url": "https://x?key=AIzaFAKESECRET123"}) == {"url": f"https://x?key={REDACTED}"}


def test_non_data_objects_become_type_names():
    class Client:
        api_key = "secret"

    class M(BaseModel):
        a: int

    assert redact({"llm": Client(), "m": M(a=1)}) == {"llm": "<Client>", "m": {"a": 1}}
