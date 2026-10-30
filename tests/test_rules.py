"""Eligibility rules on the 8 synthetic records, using hand-written facts (no LLM).

The facts are what a correct extraction should return. The expected decisions
are the brief's answer to Q2 (ADR 0004 acceptance test).
"""
import pytest

from screener.config import load_settings
from screener.dataset import load_synthetic_records
from screener.models import (
    ExtractedFacts,
    QuotedDesign,
    QuotedMatch,
    QuotedNumber,
    QuotedSpecies,
    Record,
    RecordExtraction,
)
from screener.rules import screen, screen_on_metadata, verify_facts

RECORDS = {r.record_id: r for r in load_synthetic_records(load_settings().dataset_path)}


def facts(total=None, groups=(), species=("human", None), design=("randomised", None),
          pop=("yes", None), inter=("yes", None), out=("yes", None)) -> ExtractedFacts:
    return ExtractedFacts(
        total_sample_size=QuotedNumber(value=total[0], quote=total[1]) if total else None,
        group_sizes=[QuotedNumber(value=v, quote=q) for v, q in groups],
        species=QuotedSpecies(value=species[0], quote=species[1]),
        design=QuotedDesign(value=design[0], quote=design[1]),
        population_match=QuotedMatch(value=pop[0], quote=pop[1]),
        intervention_match=QuotedMatch(value=inter[0], quote=inter[1]),
        outcome_match=QuotedMatch(value=out[0], quote=out[1]),
    )


def run(record: Record, raw: ExtractedFacts):
    verified, failures = verify_facts(raw, record.abstract)
    return screen(record, RecordExtraction(record_id=record.record_id, facts=verified, check_failures=failures))


SYNTHETIC_FACTS = {
    "SYN001": facts(
        total=(240, "enrolled 240 adults aged 40 to 75 years with type 2 diabetes"),
        groups=[(120, "metformin (120 participants)"), (120, "placebo (120 participants)")],
        species=("human", "enrolled 240 adults"),
        design=("randomised", "This synthetic randomized, placebo-controlled trial"),
        pop=("yes", "adults aged 40 to 75 years with type 2 diabetes"),
        inter=("yes", "randomized to metformin (120 participants) or placebo"),
        out=("yes", "a composite of cardiovascular death, myocardial infarction and stroke"),
    ),
    "SYN002": facts(
        total=(320, "320 adults with type 2 diabetes were randomized equally to metformin or placebo"),
        groups=[(160, "24 of 160 participants assigned metformin"), (160, "16 of 160 assigned placebo")],
        species=("human", "320 adults with type 2 diabetes"),
        design=("randomised", "synthetic randomized, placebo-controlled trial"),
        pop=("yes", "320 adults with type 2 diabetes"),
        inter=("yes", "randomized equally to metformin or placebo"),
        out=("yes", "cardiovascular death, myocardial infarction or stroke"),
    ),
    "SYN003": facts(
        species=("human", "in adults with type 2 diabetes"),
        design=("randomised", "This synthetic double-blind randomized trial"),
        pop=("yes", "in adults with type 2 diabetes"),
        inter=("yes", "compared metformin with placebo"),
        out=("yes", "The primary endpoint was myocardial infarction or stroke"),
    ),
    "SYN004": facts(
        total=(180, "enrolled 180 adults with type 2 diabetes"),
        groups=[(90, "Ninety participants were randomized to each arm")] * 2,
        species=("human", "180 adults with type 2 diabetes"),
        design=("randomised", "This synthetic randomized trial"),
        pop=("yes", "180 adults with type 2 diabetes"),
        inter=("yes", "compared metformin with usual care"),
        out=("yes", "The primary endpoint was myocardial infarction over 18 months"),
    ),
    "SYN005": facts(
        total=(80, "randomly allocated 80 mice"),
        species=("animal", "randomly allocated 80 mice"),
        design=("randomised", "randomly allocated 80 mice with experimentally induced diabetes to metformin or vehicle"),
        pop=("no", "The study involved no human participants"),
        inter=("yes", "to metformin or vehicle"),
        out=("no", "does not report human cardiovascular events"),
    ),
    "SYN006": facts(
        total=(50, "enrolled exactly 50 adults with type 2 diabetes"),
        groups=[(25, "25 were randomized to metformin"), (25, "25 to placebo")],
        species=("human", "50 adults with type 2 diabetes"),
        design=("randomised", "This synthetic randomized controlled trial"),
        pop=("yes", "50 adults with type 2 diabetes"),
        inter=("yes", "25 were randomized to metformin"),
        out=("yes", "The primary endpoint was stroke during 18 months"),
    ),
    "SYN007": facts(
        total=(600, "included 600 adults with type 2 diabetes"),
        species=("human", "600 adults with type 2 diabetes"),
        design=("non_randomised", "participants were not randomized"),
        pop=("yes", "600 adults with type 2 diabetes"),
        inter=("yes", "compared metformin users with patients receiving usual care without metformin"),
        out=("yes", "The endpoint was myocardial infarction or stroke"),
    ),
    "SYN008": facts(
        total=(160, "enrolled 160 adults with type 2 diabetes"),
        groups=[(80, "with 80 randomized to each arm")] * 2,
        species=("human", "160 adults with type 2 diabetes"),
        design=("randomised", "This synthetic randomized placebo-controlled trial"),
        pop=("yes", "160 adults with type 2 diabetes"),
        inter=("yes", "Six events occurred with metformin and seven with placebo"),
        out=("yes", "The primary endpoint was myocardial infarction or stroke at 12 months"),
    ),
}

EXPECTED = {
    "SYN001": ("include", None),
    "SYN002": ("include", None),
    "SYN008": ("include", None),
    "SYN003": ("needs_review", "total sample size"),
    "SYN004": ("exclude", "published before 2015"),
    "SYN005": ("exclude", "animal study"),
    "SYN006": ("exclude", "50 or fewer participants"),
    "SYN007": ("exclude", "non-randomised study"),
}


@pytest.mark.parametrize("record_id", sorted(EXPECTED))
def test_brief_expected_decisions(record_id):
    d = run(RECORDS[record_id], SYNTHETIC_FACTS[record_id])
    decision, reason = EXPECTED[record_id]
    assert d.decision == decision
    assert not any(n.startswith("check failed") for n in d.notes), d.notes
    if decision == "exclude":
        assert d.failed_rules[0] == reason
    if decision == "needs_review":
        assert d.missing_details == [reason]


def test_fabricated_quote_never_includes():
    raw = SYNTHETIC_FACTS["SYN001"].model_copy(
        update={"total_sample_size": QuotedNumber(value=240, quote="enrolled 999 adults"), "group_sizes": []}
    )
    d = run(RECORDS["SYN001"], raw)
    assert d.decision == "needs_review"
    assert "total sample size" in d.missing_details
    assert "check failed: total sample size: quote not found in abstract" in d.notes


def test_number_not_in_its_quote_is_unknown():
    raw = SYNTHETIC_FACTS["SYN001"].model_copy(
        update={"total_sample_size": QuotedNumber(value=240, quote="Participants were randomized to metformin"),
                "group_sizes": []}
    )
    assert run(RECORDS["SYN001"], raw).decision == "needs_review"


def test_definite_value_without_quote_is_unknown():
    raw = SYNTHETIC_FACTS["SYN001"].model_copy(update={"species": QuotedSpecies(value="human", quote=None)})
    d = run(RECORDS["SYN001"], raw)
    assert d.decision == "needs_review"
    assert "human participants" in d.missing_details


def custom(abstract, year=2020, types=("Randomized Controlled Trial",)):
    return Record(record_id="X1", source_type="pubmed", title="t", abstract=abstract,
                  publication_year=year, publication_types=list(types))


def test_total_vs_groups_conflict_across_threshold_needs_review():
    r = custom("A randomized trial enrolled 60 adults with type 2 diabetes. 25 received metformin and 25 received placebo.")
    raw = facts(total=(60, "enrolled 60 adults"), groups=[(25, "25 received metformin"), (25, "25 received placebo")],
                species=("human", "60 adults"), design=("randomised", "A randomized trial"),
                pop=("yes", "adults with type 2 diabetes"), inter=("yes", "25 received metformin"),
                out=("yes", "A randomized trial"))
    d = run(r, raw)
    assert d.decision == "needs_review"
    assert d.missing_details == ["sample size inconsistent (stated total 60, sum of group sizes 50)"]


def test_total_vs_groups_same_side_uses_total_and_notes_it():
    r = custom("A randomized trial enrolled 240 adults with type 2 diabetes. 100 received metformin and 100 received placebo.")
    raw = facts(total=(240, "enrolled 240 adults"), groups=[(100, "100 received metformin"), (100, "100 received placebo")],
                species=("human", "240 adults"), design=("randomised", "A randomized trial"),
                pop=("yes", "adults with type 2 diabetes"), inter=("yes", "100 received metformin"),
                out=("yes", "A randomized trial"))
    d = run(r, raw)
    assert d.decision == "include"
    assert any("differs from sum of group sizes 200" in n for n in d.notes)


def test_metadata_text_design_conflict_needs_review_unless_excluded():
    abstract = "A randomized trial enrolled 240 adults with type 2 diabetes."
    raw = facts(total=(240, "enrolled 240 adults"), species=("human", "240 adults"),
                design=("randomised", "A randomized trial"), pop=("yes", "240 adults"),
                inter=("yes", "A randomized trial"), out=("yes", "A randomized trial"))
    d = run(custom(abstract, types=("Observational Study",)), raw)
    assert d.decision == "needs_review"
    assert d.missing_details == ["study design: metadata and abstract disagree"]
    d_old = run(custom(abstract, year=2012, types=("Observational Study",)), raw)
    assert d_old.decision == "exclude"
    assert d_old.failed_rules == ["published before 2015"]


REVIEW_ABSTRACT = "A meta-analysis of randomized trials pooling 12,000 adults with type 2 diabetes."


def review_facts(design):
    return facts(total=(12000, "pooling 12,000 adults"), species=("human", "12,000 adults"), design=design,
                 pop=("yes", "adults with type 2 diabetes"), inter=("yes", "A meta-analysis"),
                 out=("yes", "A meta-analysis"))


def test_review_tag_excludes_when_text_does_not_say_randomised():
    d = run(custom(REVIEW_ABSTRACT, types=("Meta-Analysis", "Journal Article")),
            review_facts(("non_randomised", "A meta-analysis")))
    assert d.decision == "exclude"
    assert d.failed_rules == ["not a randomised trial (review or meta-analysis)"]


def test_review_tag_against_randomised_text_needs_review():
    d = run(custom(REVIEW_ABSTRACT, types=("Systematic Review",)),
            review_facts(("randomised", "randomized trials")))
    assert d.decision == "needs_review"
    assert d.missing_details == ["study design: metadata and abstract disagree"]


def test_contradictory_publication_types_need_review():
    d = run(custom(REVIEW_ABSTRACT, types=("Randomized Controlled Trial", "Review")),
            review_facts(("randomised", "randomized trials")))
    assert d.decision == "needs_review"
    assert d.missing_details == ["study design: publication types disagree"]


def test_protocol_tag_is_excluded():
    d = run(custom(REVIEW_ABSTRACT, types=("Clinical Trial Protocol",)), review_facts(("randomised", "randomized trials")))
    assert d.decision == "exclude" and d.failed_rules == ["trial protocol, no results"]


def test_metadata_only_exclusion_before_llm():
    d = screen_on_metadata(RECORDS["SYN004"])
    assert d.decision == "exclude" and d.metadata_only
    assert d.failed_rules == ["published before 2015"]
    assert d.notes == ["other rules not checked: excluded on metadata before any LLM call"]
    protocol = screen_on_metadata(custom("x", year=2012, types=("Clinical Trial Protocol",)))
    assert protocol.failed_rules == ["trial protocol, no results", "published before 2015"]
    assert all(screen_on_metadata(RECORDS[r]) is None for r in RECORDS if r != "SYN004")


def test_invalid_extraction_is_needs_review_but_metadata_exclusion_wins():
    r = RECORDS["SYN001"]
    d = screen(r, RecordExtraction(record_id="SYN001", facts=None))
    assert d.decision == "needs_review"
    assert d.missing_details[0] == "extracted facts (LLM output invalid after retry)"
    assert screen(RECORDS["SYN004"], RecordExtraction(record_id="SYN004", facts=None)).decision == "exclude"


def test_missing_abstract_is_needs_review():
    r = custom(None)
    d = screen(r, None)
    assert d.decision == "needs_review"
    assert d.missing_details[0] == "abstract"
