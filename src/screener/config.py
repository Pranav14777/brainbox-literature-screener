"""Run settings.

Limits and tunables are code constants, each next to the ADR that fixes it.
Secrets are never stored on Settings: client builders read them from the
environment at the point of use, so Settings can safely appear in run files
and traces (ADR 0014).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
# Embedding models are downloaded during setup; runs load them from the local
# cache with no network call. Set HF_HUB_OFFLINE=0 to download again.
os.environ.setdefault("HF_HUB_OFFLINE", "1")


@dataclass(frozen=True)
class Settings:
    # LLM (ADR 0009)
    gemini_model: str = "gemini-3.5-flash-lite"
    thinking_level: str = "low"
    critic_thinking_level: str = "medium"  # raised 2026-10-30 after the Critic missed a real overclaim (ADR 0009)
    llm_requests_per_minute: int = 15
    eligibility_concurrency: int = 8  # requests in flight; the throttle still spaces starts
    use_llm_cache: bool = False  # development only; off for evaluation and demo

    # Timeouts in seconds (ADR 0013)
    llm_timeout_s: float = 90.0  # free-tier calls took up to 44s; 60s left too little margin
    pubmed_timeout_s: float = 15.0
    gmail_timeout_s: float = 30.0

    # Retries for transient errors (ADR 0013)
    max_attempts: int = 3
    backoff_base_s: float = 1.0
    retry_after_cap_s: float = 60.0

    # Loop caps (ADR 0006, ADR 0007)
    max_critic_revisions: int = 1
    max_change_requests: int = 2

    # Screening rules (ADR 0004) and search window (ADR 0003, ADR 0012)
    min_publication_year: int = 2015
    sample_size_must_exceed: int = 50
    recency_years: int = 5
    pubmed_max_records: int = 10

    # Synthetic retrieval (ADR 0011); None means the whole collection
    synthetic_retrieval_depth: int | None = None
    embedding_model: str = "NeuML/pubmedbert-base-embeddings"
    embedding_fallback_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Paths
    dataset_path: Path = PROJECT_ROOT / "synthetic_clinical_abstracts.json"
    runs_dir: Path = PROJECT_ROOT / "runs"
    llm_cache_dir: Path = PROJECT_ROOT / ".llm_cache"
    prompts_dir: Path = PROJECT_ROOT / "prompts"

    # Non-secret identifiers from the environment
    recipient_email: str = ""
    ncbi_tool: str = ""
    ncbi_email: str = ""


def load_settings(**overrides) -> Settings:
    """Build Settings from the environment, with keyword overrides for tests and eval scenarios."""
    values = {
        "gemini_model": os.environ.get("GEMINI_MODEL", Settings.gemini_model),
        "recipient_email": os.environ.get("RECIPIENT_EMAIL", ""),
        "ncbi_tool": os.environ.get("NCBI_TOOL", ""),
        "ncbi_email": os.environ.get("NCBI_EMAIL", ""),
    }
    values.update(overrides)
    return Settings(**values)
