import pytest
from pydantic import ValidationError

from screener.config import load_settings
from screener.models import (
    CriticReport,
    EmailContent,
    ExtractedFacts,
    QuotedDesign,
    QuotedMatch,
    QuotedNumber,
    QuotedSpecies,
    Record,
    RecordExtraction,
    RunState,
    new_run_id,
)
from screener.runstore import load_run, save_run


def test_run_state_round_trips_through_run_file(tmp_path):
    state = RunState(run_id=new_run_id(), question="metformin and cardiovascular outcomes?")
    state.records.append(
        Record(
            record_id="SYN001",
            source_type="synthetic",
            title="t",
            abstract="enrolled 240 adults",
            publication_year=2019,
        )
    )
    match = QuotedMatch(value="yes", quote="adults")
    state.extractions.append(
        RecordExtraction(
            record_id="SYN001",
            facts=ExtractedFacts(
                total_sample_size=QuotedNumber(value=240, quote="enrolled 240 adults"),
                group_sizes=[],
                species=QuotedSpecies(value="human", quote="adults"),
                design=QuotedDesign(value="unclear", quote=None),
                population_match=match,
                intervention_match=match,
                outcome_match=match,
            ),
        )
    )
    save_run(state, tmp_path)
    assert load_run(tmp_path, state.run_id) == state
    assert not list(tmp_path.glob("*.tmp"))


def test_critic_revise_without_issues_is_invalid():
    with pytest.raises(ValidationError):
        CriticReport(verdict="revise", issues=[])


def test_fingerprint_changes_with_any_field():
    a = EmailContent(recipient="a@example.com", subject="s", body="b")
    assert a.fingerprint() == EmailContent(recipient="a@example.com", subject="s", body="b").fingerprint()
    assert a.fingerprint() != a.model_copy(update={"recipient": "c@example.com"}).fingerprint()
    assert a.fingerprint() != a.model_copy(update={"subject": "s2"}).fingerprint()


def test_settings_hold_no_secrets():
    fields = vars(load_settings())
    assert not any("key" in name.lower() or "token" in name.lower() for name in fields)
