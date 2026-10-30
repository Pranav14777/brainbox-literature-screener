"""Layer 2 content checks for Summarizer output (ADR 0005, ADR 0010).

Layer 1 (format) is the Pydantic schema. Layer 2 checks each claim and each
study detail against the abstracts of included records, and checks that every
source with two or more included studies has one comparison claim. Failures are
retried once by the caller; whatever still fails is removed (claims) or marked
"not reported (unverified)" (details), and becomes a visible warning. A missing
comparison becomes the warning "no cross-study comparison"; code never writes it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import get_args

from screener.matching import quote_in_source, unsupported_numbers
from screener.models import (
    Claim,
    Record,
    RemovedClaim,
    ReviewWarning,
    StudyDetail,
    StudyDetailName,
    StudyDetails,
    SummaryDraft,
)

REQUIRED_DETAILS: tuple[str, ...] = get_args(StudyDetailName)
NOT_REPORTED = "not reported"
UNVERIFIED = "not reported (unverified)"


@dataclass
class DraftCheck:
    claim_failures: dict[str, list[str]] = field(default_factory=dict)  # claim_id -> reasons
    detail_failures: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (record_id, name) -> reasons
    missing_comparisons: dict[str, list[str]] = field(default_factory=dict)  # source_type -> included record_ids

    @property
    def ok(self) -> bool:
        return not self.claim_failures and not self.detail_failures and not self.missing_comparisons

    def describe(self) -> list[str]:
        lines = [f"claim {cid}: {'; '.join(r)}" for cid, r in self.claim_failures.items()]
        lines += [f"{rid} {name}: {'; '.join(r)}" for (rid, name), r in self.detail_failures.items()]
        lines += [
            f"missing comparison for the {source} studies: add one claim of kind 'comparison' citing "
            f"{', '.join(rids)}, with a quote from each, saying whether events were fewer, more or similar "
            "with metformin in each study, without pooled or calculated numbers"
            for source, rids in self.missing_comparisons.items()
        ]
        return lines


def included_by_source(included: dict[str, Record]) -> dict[str, list[str]]:
    by_source: dict[str, list[str]] = {}
    for rid, record in included.items():
        by_source.setdefault(record.source_type, []).append(rid)
    return by_source


def check_detail(detail: StudyDetail, record: Record) -> list[str]:
    if detail.value.strip().lower() == NOT_REPORTED:
        return []
    if not detail.quote:
        return ["no quote"]
    if not quote_in_source(detail.quote, record.abstract):
        return ["quote not found in abstract"]
    if bad := unsupported_numbers(detail.value, [detail.quote]):
        return [f"numbers not in its quote: {', '.join(str(n) for n in bad)}"]
    return []


def check_claim(claim: Claim, included: dict[str, Record]) -> list[str]:
    reasons: list[str] = []
    if not claim.record_ids:
        return ["cites no record"]
    if unknown := [r for r in claim.record_ids if r not in included]:
        reasons.append(f"cites records that are not included: {', '.join(unknown)}")
    if len({included[r].source_type for r in claim.record_ids if r in included}) > 1:
        reasons.append("mixes synthetic and PubMed records")
    for rid in claim.record_ids:
        if not any(q.record_id == rid for q in claim.quotes):
            reasons.append(f"no quote from {rid}")
    for q in claim.quotes:
        if q.record_id not in claim.record_ids:
            reasons.append(f"quote from uncited record {q.record_id}")
        elif q.record_id in included and not quote_in_source(q.quote, included[q.record_id].abstract):
            reasons.append(f"quote not found in {q.record_id}")
    text = claim.text
    for rid in claim.record_ids:  # record IDs and PMIDs are citations, not numbers
        text = text.replace(rid, " ")
    if bad := unsupported_numbers(text, [q.quote for q in claim.quotes]):
        reasons.append(f"numbers not in its quotes: {', '.join(str(n) for n in bad)}")
    if claim.kind == "comparison" and claim.record_ids[0] in included:
        expected = included_by_source(included)[included[claim.record_ids[0]].source_type]
        if len(expected) < 2:
            reasons.append("a comparison needs two or more included studies from its source")
        elif missing := [r for r in expected if r not in claim.record_ids]:
            reasons.append(f"a comparison must cite every included study from its source; missing {', '.join(missing)}")
    return reasons


def sources_lacking_comparison(claims: list[Claim], included: dict[str, Record]) -> dict[str, list[str]]:
    """Sources with two or more included studies and no comparison claim among `claims`."""
    covered = {included[c.record_ids[0]].source_type
               for c in claims if c.kind == "comparison" and c.record_ids and c.record_ids[0] in included}
    return {s: rids for s, rids in included_by_source(included).items() if len(rids) >= 2 and s not in covered}


def check_draft(draft: SummaryDraft, included: dict[str, Record]) -> DraftCheck:
    check = DraftCheck()
    for claim in draft.claims:
        if reasons := check_claim(claim, included):
            check.claim_failures[claim.claim_id] = reasons
    # A comparison that exists but fails is a claim failure, not also "missing".
    check.missing_comparisons = sources_lacking_comparison(draft.claims, included)
    given = {sd.record_id: sd for sd in draft.study_details}
    for rid, record in included.items():
        details = {d.name: d for d in given[rid].details} if rid in given else {}
        for name in REQUIRED_DETAILS:
            if name not in details:
                check.detail_failures[(rid, name)] = ["missing"]
            elif reasons := check_detail(details[name], record):
                check.detail_failures[(rid, name)] = reasons
    return check


def finalize(
    draft: SummaryDraft, check: DraftCheck, included: dict[str, Record]
) -> tuple[SummaryDraft, list[RemovedClaim], list[ReviewWarning]]:
    """Remove failed claims and mark failed details unverified. Only checked content remains approvable."""
    removed = [RemovedClaim(claim=c, reasons=check.claim_failures[c.claim_id])
               for c in draft.claims if c.claim_id in check.claim_failures]
    claims = [c for c in draft.claims if c.claim_id not in check.claim_failures]
    given = {sd.record_id: {d.name: d for d in sd.details} for sd in draft.study_details}
    study_details = []
    for rid in included:
        details = []
        for name in REQUIRED_DETAILS:
            if (rid, name) in check.detail_failures:
                details.append(StudyDetail(name=name, value=UNVERIFIED, quote=None))
            else:
                d = given[rid][name]
                details.append(StudyDetail(name=name, value=NOT_REPORTED, quote=None)
                               if d.value.strip().lower() == NOT_REPORTED else d)
        study_details.append(StudyDetails(record_id=rid, details=details))
    warnings = [ReviewWarning(kind="removed_unsupported",
                              message=f"removed: unsupported claim \"{r.claim.text}\" ({'; '.join(r.reasons)})")
                for r in removed]
    for rid in included:
        failed = {name: r for (fr, name), r in check.detail_failures.items() if fr == rid}
        if len(failed) == len(REQUIRED_DETAILS) and all(r == ["missing"] for r in failed.values()):
            # Collapse to one warning when the Summarizer gave no details for the record at all.
            warnings.append(ReviewWarning(
                kind="removed_unsupported",
                message=f"removed: unsupported study details for {rid} "
                        f"(missing from Summarizer output; shown as {UNVERIFIED})"))
            continue
        warnings += [ReviewWarning(kind="removed_unsupported",
                                   message=f"removed: unsupported detail {rid} {name} ({'; '.join(reasons)})")
                     for name, reasons in failed.items()]
    for source in sources_lacking_comparison(claims, included):  # after removals
        warnings.append(ReviewWarning(kind="missing_comparison", source=source,
                                      message=f"no cross-study comparison for the {source} studies"))
    return SummaryDraft(study_details=study_details, claims=claims), removed, warnings
