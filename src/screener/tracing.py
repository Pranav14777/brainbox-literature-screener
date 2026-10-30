"""LangSmith tracing helpers (ADR 0014).

Every traced function runs its inputs and outputs through redact():
- keys that name a secret (api_key, refresh_token, ...) are replaced
- any secret value loaded from the environment is replaced wherever it appears
- objects that are not plain data (SDK clients, services) become their type name,
  so a client's internals can never be serialised into a trace
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from langsmith import get_current_run_tree, trace, traceable
from pydantic import BaseModel

# Suffix match, so "refresh_token" is redacted but token counts like "input_tokens" are kept.
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_?key|key|secret|password|token|auth|authorization|credentials?)$", re.IGNORECASE
)
_SECRET_ENV_VARS = ("GEMINI_API_KEY", "NCBI_API_KEY", "LANGSMITH_API_KEY")
REDACTED = "[redacted]"


def _secret_values() -> list[str]:
    return [v for name in _SECRET_ENV_VARS if (v := os.environ.get(name)) and len(v) >= 8]


def redact(value: Any) -> Any:
    secrets = _secret_values()

    def walk(v: Any) -> Any:
        if isinstance(v, BaseModel):
            v = v.model_dump(mode="json")
        if isinstance(v, dict):
            return {k: REDACTED if _SECRET_KEY.search(str(k)) else walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple, set)):
            return [walk(x) for x in v]
        if isinstance(v, str):
            for s in secrets:
                v = v.replace(s, REDACTED)
            return v
        if v is None or isinstance(v, (bool, int, float)):
            return v
        if isinstance(v, Enum):
            return v.value
        if isinstance(v, (date, datetime, Decimal, Path)):
            return str(v)
        return f"<{type(v).__name__}>"

    return walk(value)


def _redact_inputs(inputs: dict) -> dict:
    return redact(inputs)


def _redact_outputs(outputs: Any) -> dict:
    cleaned = redact(outputs)
    return cleaned if isinstance(cleaned, dict) else {"output": cleaned}


def traced(name: str, run_type: str = "chain"):
    """traceable() with redaction always applied."""
    return traceable(
        name=name, run_type=run_type, process_inputs=_redact_inputs, process_outputs=_redact_outputs
    )


def run_span(question: str, *, run_id: str, scenario: str | None = None, mock: bool = False) -> trace:
    """Root span for one run. Its input is the user's question (ADR 0014).

    Use as a context manager; call .end(outputs=...) on the result before exit.
    """
    return trace(
        name="screener_run",
        run_type="chain",
        inputs=redact({"question": question}),
        tags=[f"run:{run_id}", scenario or "normal", f"mock={str(mock).lower()}"],
        metadata=redact({"run_id": run_id, "scenario": scenario, "mock": mock}),
    )


def add_metadata(**values: Any) -> None:
    """Attach metadata to the current span, if tracing is active."""
    run = get_current_run_tree()
    if run is not None:
        run.metadata.update(redact(values))
