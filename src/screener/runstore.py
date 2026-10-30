"""Save and load the run file after every step (ADR 0008)."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from screener.models import RunState

_write_lock = threading.Lock()


def run_path(runs_dir: Path, run_id: str) -> Path:
    return runs_dir / f"{run_id}.json"


def save_run(state: RunState, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = run_path(runs_dir, state.run_id)
    # The lock serialises writers in this process; the dump happens inside it so
    # a concurrent mutation can't interleave with serialisation.
    with _write_lock:
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        # Atomic replace: a crash never leaves a half-written run file, which matters
        # when the file records delivery status "sending" (ADR 0013).
        os.replace(tmp, path)
    return path


def load_run(runs_dir: Path, run_id: str) -> RunState:
    return RunState.model_validate_json(run_path(runs_dir, run_id).read_text(encoding="utf-8"))
