"""Thread-safety of the shared throttle, request counter, LLM cache and run-file writes."""
import threading
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from screener.config import load_settings
from screener.fakes import FakeLLMTransport
from screener.llm import LLM
from screener.models import RunState, new_run_id
from screener.runstore import load_run, save_run


class Ping(BaseModel):
    answer: int


def test_concurrent_calls_get_unique_start_slots_15_rpm_apart():
    slept, lock = [], threading.Lock()

    def sleep(s):
        with lock:
            slept.append(s)

    # A frozen clock means every caller arrives "at once": the throttle must still
    # hand out distinct start times 4s apart (60s / 15 RPM).
    llm = LLM(load_settings(), transport=FakeLLMTransport({Ping: ['{"answer": 1}']}), sleep=sleep, clock=lambda: 0.0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: llm.generate(agent="t", prompt_version="v", system="s", user="u",
                                                        schema=Ping), range(8)))
    assert results == [Ping(answer=1)] * 8
    assert llm.requests_made == 8
    assert sorted(slept) == [4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0]  # first request starts immediately


def test_concurrent_cache_use_leaves_valid_entries(tmp_path):
    settings = load_settings(use_llm_cache=True, llm_cache_dir=tmp_path)
    llm = LLM(settings, transport=FakeLLMTransport({Ping: ['{"answer": 2}']}), sleep=lambda s: None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: llm.generate(agent="t", prompt_version="v", system="s", user="u",
                                                        schema=Ping), range(16)))
    assert results == [Ping(answer=2)] * 16
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert not list(tmp_path.glob("*.tmp"))
    assert llm.generate(agent="t", prompt_version="v", system="s", user="u", schema=Ping) == Ping(answer=2)


def test_concurrent_run_file_writes_stay_valid(tmp_path):
    state = RunState(run_id=new_run_id(), question="q")

    def write(i):
        state.errors  # touch shared state while writing
        save_run(state.model_copy(update={"llm_calls": i}), tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))
    loaded = load_run(tmp_path, state.run_id)
    assert 0 <= loaded.llm_calls < 40
    assert not list(tmp_path.glob("*.tmp"))
