"""Eligibility: verify extracted facts, then apply the screening rules in code (ADR 0004).

The LLM only extracts facts with quotes. verify_facts() turns any fact whose
quote or number fails the ADR 0010 checks into "unknown". screen() then decides
from verified facts and metadata alone:
- a definite exclusion beats needs_review
- every failed rule is listed, in a fixed order
- needs_review names each missing or conflicting detail
"""
from __future__ import annotations

from typing import TypeVar

from screener.matching import number_in_text, quote_in_source
from screener.models import (
    EvidenceItem,
    ExtractedFacts,
    QuotedDesign,
    QuotedMatch,
    QuotedNumber,
    QuotedSpecies,
    Record,
    RecordExtraction,
    ScreeningDecision,
)

_RANDOMISED_TYPES = {"randomized controlled trial"}
_NON_RANDOMISED_TYPES = {"observational study"}
_REVIEW_TYPES = {"meta-analysis", "systematic review", "review"}
_PROTOCOL_TYPES = {"clinical trial protocol"}
PROTOCOL_RULE = "trial protocol, no results"

UNCLEAR_FACTS = ExtractedFacts(
    total_sample_size=None,
    group_sizes=[],
    species=QuotedSpecies(value="unclear", quote=None),
    design=QuotedDesign(value="unclear", quote=None),
    population_match=QuotedMatch(value="unclear", quote=None),
    intervention_match=QuotedMatch(value="unclear", quote=None),
    outcome_match=QuotedMatch(value="unclear", quote=None),
)

Q = TypeVar("Q", QuotedSpecies, QuotedDesign, QuotedMatch)


def verify_facts(facts: ExtractedFacts, abstract: str) -> tuple[ExtractedFacts, list[str]]:
    """Return facts with every failed item made unknown, plus the list of failures."""
    failures: list[str] = []

    def number_ok(label: str, n: QuotedNumber) -> bool:
        if not quote_in_source(n.quote, abstract):
            failures.append(f"{label}: quote not found in abstract")
            return False
        if not number_in_text(n.value, n.quote):
            failures.append(f"{label}: {n.value} does not appear in its quote")
            return False
        return True

    def quoted_ok(label: str, item: Q) -> Q:
        if item.value == "unclear":
            return item
        if not item.quote:
            failures.append(f"{label}: '{item.value}' given without a quote")
        elif not quote_in_source(item.quote, abstract):
            failures.append(f"{label}: quote not found in abstract")
        else:
            return item
        return type(item)(value="unclear", quote=None)

    total = facts.total_sample_size
    if total is not None and not number_ok("total sample size", total):
        total = None
    groups = facts.group_sizes
    # One bad group size makes the sum meaningless, so all are dropped.
    if groups and not all(number_ok(f"group size {i + 1}", g) for i, g in enumerate(groups)):
        groups = []

    verified = ExtractedFacts(
        total_sample_size=total,
        group_sizes=groups,
        species=quoted_ok("species", facts.species),
        design=quoted_ok("design", facts.design),
        population_match=quoted_ok("population match", facts.population_match),
        intervention_match=quoted_ok("intervention match", facts.intervention_match),
        outcome_match=quoted_ok("outcome match", facts.outcome_match),
    )
    return verified, failures


def _design_from_metadata(publication_types: list[str]) -> str | None:
    types = {t.lower() for t in publication_types}
    rct = bool(types & _RANDOMISED_TYPES)
    observational = bool(types & _NON_RANDOMISED_TYPES)
    review = bool(types & _REVIEW_TYPES)
    if rct and (observational or review):
        return "conflicting"
    if review:
        return "review"
    if rct:
        return "randomised"
    if observational:
        return "non_randomised"
    return None  # e.g. "Journal Article" alone says nothing about design


def _species_from_metadata(mesh_terms: list[str]) -> str | None:
    terms = {t.lower() for t in mesh_terms}
    humans, animals = "humans" in terms, "animals" in terms
    if humans and not animals:
        return "human"
    if animals and not humans:
        return "animal"
    return None


def _is_protocol(record: Record) -> bool:
    return bool({t.lower() for t in record.publication_types} & _PROTOCOL_TYPES)


def screen_on_metadata(record: Record, *, min_year: int = 2015) -> ScreeningDecision | None:
    """A definite exclusion from metadata alone (protocol tag or year before min_year), or None.

    Used before the eligibility LLM call: an excluded record needs no extraction.
    Other rules are not checked, and the decision says so (ADR 0004).
    """
    failed: list[str] = []
    evidence: list[EvidenceItem] = []
    if _is_protocol(record):
        failed.append(PROTOCOL_RULE)
        evidence.append(EvidenceItem(fact="publication type: Clinical Trial Protocol", metadata_field="publication_types"))
    if record.publication_year is not None and record.publication_year < min_year:
        failed.append(f"published before {min_year}")
        evidence.append(EvidenceItem(fact=f"published {record.publication_year}", metadata_field="publication_year"))
    if not failed:
        return None
    return ScreeningDecision(
        record_id=record.record_id, source_type=record.source_type, decision="exclude",
        failed_rules=failed, evidence=evidence, metadata_only=True,
        notes=["other rules not checked: excluded on metadata before any LLM call"],
    )


def screen(
    record: Record,
    extraction: RecordExtraction | None,
    *,
    min_year: int = 2015,
    sample_size_must_exceed: int = 50,
) -> ScreeningDecision:
    """Apply the screening rules to one record. Facts must already be verified."""
    failed: list[str] = []
    missing: list[str] = []
    evidence: list[EvidenceItem] = []
    notes: list[str] = []

    if record.abstract is None:
        missing.append("abstract")
        facts = UNCLEAR_FACTS
    elif extraction is None or extraction.facts is None:
        reason = "; ".join(extraction.check_failures) if extraction else ""
        missing.append(f"extracted facts ({reason or 'LLM output invalid after retry'})")
        facts = UNCLEAR_FACTS
    else:
        facts = extraction.facts
        notes.extend(f"check failed: {f}" for f in extraction.check_failures)

    # Species: PubMed MeSH tags win when present (ADR 0004 point 4).
    species = _species_from_metadata(record.mesh_terms)
    if species is not None:
        evidence.append(EvidenceItem(fact=f"species: {species}", metadata_field="mesh_terms"))
        if facts.species.value not in ("unclear", species):
            notes.append(f"abstract text suggests '{facts.species.value}'; MeSH tags used")
    else:
        species = facts.species.value
        if species != "unclear":
            evidence.append(EvidenceItem(fact=f"species: {species}", quote=facts.species.quote))
    if species == "animal":
        failed.append("animal study")
    elif species != "human":
        missing.append("human participants")

    # Design: metadata and text must not clearly disagree (ADR 0004 point 6).
    meta_design = _design_from_metadata(record.publication_types)
    text_design = facts.design.value
    if meta_design == "conflicting":
        missing.append("study design: publication types disagree")
        notes.append(f"publication types: {', '.join(record.publication_types)}")
    elif meta_design == "review":
        evidence.append(EvidenceItem(fact="design: review or meta-analysis", metadata_field="publication_types"))
        if text_design == "randomised":
            missing.append("study design: metadata and abstract disagree")
            notes.append(
                f"publication type says review or meta-analysis; abstract says 'randomised' "
                f"(quote: \"{facts.design.quote}\")"
            )
        else:
            failed.append("not a randomised trial (review or meta-analysis)")
    elif meta_design and text_design != "unclear" and meta_design != text_design:
        missing.append("study design: metadata and abstract disagree")
        notes.append(
            f"publication type says '{meta_design}'; abstract says '{text_design}' "
            f"(quote: \"{facts.design.quote}\")"
        )
    else:
        design = meta_design or text_design
        if meta_design:
            evidence.append(EvidenceItem(fact=f"design: {design}", metadata_field="publication_types"))
        elif design != "unclear":
            evidence.append(EvidenceItem(fact=f"design: {design}", quote=facts.design.quote))
        if design == "non_randomised":
            failed.append("non-randomised study")
        elif design == "unclear":
            missing.append("randomisation")

    # Protocol: a safety net for records the query did not filter (ADR 0004 point 6).
    if _is_protocol(record):
        failed.append(PROTOCOL_RULE)
        evidence.append(EvidenceItem(fact="publication type: Clinical Trial Protocol", metadata_field="publication_types"))

    # Year: metadata only, no LLM.
    if record.publication_year is None:
        missing.append("publication year")
    else:
        evidence.append(
            EvidenceItem(fact=f"published {record.publication_year}", metadata_field="publication_year")
        )
        if record.publication_year < min_year:
            failed.append(f"published before {min_year}")

    # Sample size: a stated total wins; groups are summed only without one (ADR 0004 point 5).
    total = facts.total_sample_size
    groups = facts.group_sizes if len(facts.group_sizes) >= 2 else []
    group_sum = sum(g.value for g in groups) if groups else None
    size: int | None = None
    if total is not None and group_sum is not None and total.value != group_sum:
        if (total.value > sample_size_must_exceed) != (group_sum > sample_size_must_exceed):
            missing.append(
                f"sample size inconsistent (stated total {total.value}, sum of group sizes {group_sum})"
            )
        else:
            size = total.value
            notes.append(f"stated total {total.value} differs from sum of group sizes {group_sum}; stated total used")
            evidence.append(EvidenceItem(fact=f"total sample size: {size}", quote=total.quote))
    elif total is not None:
        size = total.value
        evidence.append(EvidenceItem(fact=f"total sample size: {size}", quote=total.quote))
    elif group_sum is not None:
        size = group_sum
        evidence.extend(
            EvidenceItem(fact=f"group size: {g.value}", quote=g.quote) for g in groups
        )
        notes.append(f"total sample size {size} is the sum of group sizes")
    else:
        missing.append("total sample size")
    if size is not None and size <= sample_size_must_exceed:
        failed.append(f"{sample_size_must_exceed} or fewer participants")

    # Population, intervention and outcome match the question.
    for label, match in (
        ("population", facts.population_match),
        ("intervention", facts.intervention_match),
        ("outcome", facts.outcome_match),
    ):
        if match.value == "no":
            failed.append(f"out of scope: {label} does not match")
            evidence.append(EvidenceItem(fact=f"{label} does not match", quote=match.quote))
        elif match.value == "unclear":
            missing.append(f"{label} match")
        else:
            evidence.append(EvidenceItem(fact=f"{label} matches", quote=match.quote))

    if failed:
        decision = "exclude"
    elif missing:
        decision = "needs_review"
    else:
        decision = "include"
    return ScreeningDecision(
        record_id=record.record_id,
        source_type=record.source_type,
        decision=decision,
        failed_rules=failed,
        missing_details=missing if not failed else [],
        evidence=evidence,
        notes=notes,
    )
