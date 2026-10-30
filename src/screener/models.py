"""Shared data models. Names follow docs/glossary.md.

Two kinds of model live here:
- LLM output schemas (IntakeForm, ExtractedFacts, SummaryDraft, CriticReport).
  They are passed to Gemini as response schemas, so they avoid dict fields.
- RunState and its parts, saved to the run file after every step (ADR 0008).
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SourceType = Literal["synthetic", "pubmed"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_run_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


# --- Records (ADR 0011, ADR 0012) ---

class Record(BaseModel):
    """One study abstract and its metadata."""

    record_id: str  # SYN001 for synthetic records, the PMID for PubMed records
    source_type: SourceType
    title: str
    abstract: str | None  # None when PubMed has no abstract, which means needs_review
    publication_year: int | None
    publication_types: list[str] = Field(default_factory=list)
    mesh_terms: list[str] = Field(default_factory=list)
    pmid: str | None = None
    url: str | None = None
    retrieval_score: float | None = None


# --- Intake form (ADR 0003): LLM output ---

class IntakeForm(BaseModel):
    # not_stated: the question doesn't mention it; code fills it from the scope.
    # unclear: the question mentions it vaguely; triggers one clarifying question.
    intervention: Literal["metformin", "other", "unclear", "not_stated"]
    outcome: Literal["cardiovascular", "other", "unclear", "not_stated"]
    population: Literal["adults_t2d", "other", "unclear", "not_stated"]
    sources: Literal["pubmed", "synthetic", "both", "unspecified"]
    recency: bool
    intervention_wording: str
    outcome_wording: str
    population_wording: str


# --- Extracted facts (ADR 0004): LLM output, one per record ---

class QuotedNumber(BaseModel):
    value: int
    quote: str


class QuotedSpecies(BaseModel):
    value: Literal["human", "animal", "unclear"]
    quote: str | None


class QuotedDesign(BaseModel):
    value: Literal["randomised", "non_randomised", "unclear"]
    quote: str | None


class QuotedMatch(BaseModel):
    value: Literal["yes", "no", "unclear"]
    quote: str | None


class ExtractedFacts(BaseModel):
    total_sample_size: QuotedNumber | None  # None when the abstract does not state a total
    group_sizes: list[QuotedNumber]
    species: QuotedSpecies
    design: QuotedDesign
    population_match: QuotedMatch
    intervention_match: QuotedMatch
    outcome_match: QuotedMatch


class RecordExtraction(BaseModel):
    """Extracted facts for one record plus the code checks that failed (ADR 0010)."""

    record_id: str
    facts: ExtractedFacts | None  # None when the LLM output was still invalid after one retry
    check_failures: list[str] = Field(default_factory=list)


# --- Screening decision (ADR 0004) ---

Decision = Literal["include", "exclude", "needs_review"]


class EvidenceItem(BaseModel):
    """Support for one fact: a verified quote, or a metadata field."""

    fact: str
    quote: str | None = None
    metadata_field: str | None = None


class ScreeningDecision(BaseModel):
    record_id: str
    source_type: SourceType
    decision: Decision
    failed_rules: list[str] = Field(default_factory=list)
    missing_details: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    metadata_only: bool = False  # excluded on metadata before any LLM call; other rules not checked


# --- Summary draft (ADR 0005): LLM output ---

StudyDetailName = Literal[
    "population",
    "intervention",
    "comparator",
    "participants",
    "primary_outcome",
    "follow_up",
    "intervention_events",
    "comparator_events",
    "effect_estimate",
]


class StudyDetail(BaseModel):
    name: StudyDetailName
    value: str  # the literal "not reported" when the abstract does not state it
    quote: str | None


class StudyDetails(BaseModel):
    record_id: str
    details: list[StudyDetail]


class CitedQuote(BaseModel):
    record_id: str
    quote: str


class Claim(BaseModel):
    claim_id: str
    kind: Literal["study", "comparison"]  # comparison: direction of findings across a source's included studies
    text: str
    record_ids: list[str]
    quotes: list[CitedQuote]


class SummaryDraft(BaseModel):
    study_details: list[StudyDetails]
    claims: list[Claim]


# --- Critic (ADR 0006): LLM output ---

class CriticIssue(BaseModel):
    claim_id: str | None
    kind: Literal["unfair_claim", "contradiction", "missing_context"]
    explanation: str


class CriticReport(BaseModel):
    verdict: Literal["approve", "revise"]
    issues: list[CriticIssue]

    @model_validator(mode="after")
    def revise_needs_issues(self) -> CriticReport:
        if self.verdict == "revise" and not self.issues:
            raise ValueError("verdict 'revise' requires at least one issue")
        return self


# --- Drafts and warnings in RunState ---

class RemovedClaim(BaseModel):
    """A claim that still failed the content checks after one retry (ADR 0005)."""

    claim: Claim
    reasons: list[str]


class Draft(BaseModel):
    round: int
    pass_number: int  # 1 is the round's first draft; 2 and 3 are Critic revisions
    summary: SummaryDraft  # only claims and details that passed the content checks
    removed_claims: list[RemovedClaim] = Field(default_factory=list)
    critic: CriticReport | None = None
    critic_available: bool = True


class ReviewWarning(BaseModel):
    kind: Literal[
        "critic_issue",
        "critic_unavailable",
        "removed_unsupported",
        "missing_comparison",
        "source_unavailable",
        "no_synthesis",
        "notice",
    ]
    message: str
    source: SourceType | None = None  # for source-specific warnings, used by the email's Limitations section


# --- Searches (ADR 0011, ADR 0012) ---

class SyntheticSearch(BaseModel):
    embedding_model: str
    collection_size: int
    screened: int


class PubmedSearch(BaseModel):
    query: str
    min_date: str  # YYYY/MM/DD
    max_date: str
    total_matches: int | None = None
    retrieved_pmids: list[str] = Field(default_factory=list)
    status: Literal["ok", "no_results", "unavailable"] = "ok"


# --- Email, approval and delivery (ADR 0007, ADR 0013) ---

class EmailContent(BaseModel):
    recipient: str
    subject: str
    body: str

    def fingerprint(self) -> str:
        """SHA-256 of the exact recipient, subject and body."""
        payload = json.dumps([self.recipient, self.subject, self.body], ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ChangeRequest(BaseModel):
    round: int
    instruction: str
    at: datetime = Field(default_factory=utc_now)


class ApprovalRecord(BaseModel):
    decision: Literal["approved", "approved_with_warnings", "rejected"]
    fingerprint: str
    round: int
    at: datetime = Field(default_factory=utc_now)


DeliveryStatus = Literal["not_sent", "sending", "sent", "failed", "unknown"]


class Delivery(BaseModel):
    status: DeliveryStatus = "not_sent"
    fingerprint: str | None = None
    gmail_message_id: str | None = None
    error: str | None = None
    updated_at: datetime | None = None


# --- Errors (ADR 0013) ---

ErrorClass = Literal[
    "transient",
    "invalid_request",
    "bad_credentials",
    "quota_exhausted",
    "empty_result",
    "invalid_output",
]


class ErrorEvent(BaseModel):
    stage: str
    error_class: ErrorClass
    message: str
    attempt: int
    at: datetime = Field(default_factory=utc_now)


# --- RunState (ADR 0008) ---

Stage = Literal[
    "intake",
    "retrieval",
    "eligibility",
    "summarizer",
    "critic",
    "human_review",
    "sending",
    "done",
    "stopped",
]


class RunState(BaseModel):
    run_id: str
    question: str
    created_at: datetime = Field(default_factory=utc_now)
    search_date: date = Field(default_factory=date.today)
    scenario: str | None = None
    mock: bool = False

    stage: Stage = "intake"
    stop_reason: str | None = None

    intake: IntakeForm | None = None
    intake_scope_filled: list[str] = Field(default_factory=list)
    intake_correction: str | None = None  # misspelled drug word the user confirmed meant metformin
    clarification: str | None = None

    synthetic_search: SyntheticSearch | None = None
    pubmed_search: PubmedSearch | None = None
    records: list[Record] = Field(default_factory=list)
    extractions: list[RecordExtraction] = Field(default_factory=list)
    decisions: list[ScreeningDecision] = Field(default_factory=list)

    round: int = 1
    drafts: list[Draft] = Field(default_factory=list)
    change_requests: list[ChangeRequest] = Field(default_factory=list)
    warnings: list[ReviewWarning] = Field(default_factory=list)

    email: EmailContent | None = None
    approval: ApprovalRecord | None = None
    delivery: Delivery = Field(default_factory=Delivery)

    errors: list[ErrorEvent] = Field(default_factory=list)
    llm_calls: int = 0
