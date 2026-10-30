"""Block 6: content checks, Summarizer retries, Critic loop caps and rendering (no real LLM)."""
from screener.config import load_settings
from screener.drafting import NO_SYNTHESIS, produce_draft, run_round
from screener.fakes import FakeLLMTransport
from screener.llm import LLM
from screener.models import (
    CitedQuote,
    Claim,
    CriticIssue,
    CriticReport,
    Record,
    ScreeningDecision,
    StudyDetail,
    StudyDetails,
    SummaryDraft,
)
from screener.prompts import load_prompt
from screener.render import render_evidence_table, render_summary
from screener.rules import screen_on_metadata
from screener.validation import UNVERIFIED, check_draft, finalize
from test_agents import FORM
from test_llm import FakeTime
from test_rules import RECORDS

SETTINGS = load_settings()
INCLUDED = {k: RECORDS[k] for k in ("SYN001", "SYN002", "SYN008")}
NR = ("not reported", None)


def details(rid, **values):
    return StudyDetails(record_id=rid, details=[StudyDetail(name=n, value=v, quote=q) for n, (v, q) in values.items()])


DETAILS = [
    details("SYN001",
            population=("adults aged 40 to 75 years with type 2 diabetes", "adults aged 40 to 75 years with type 2 diabetes"),
            intervention=("metformin", "Participants were randomized to metformin"),
            comparator=("placebo", "or placebo (120 participants)"),
            participants=("240 adults", "enrolled 240 adults"),
            primary_outcome=("cardiovascular death, myocardial infarction and stroke",
                             "a composite of cardiovascular death, myocardial infarction and stroke"),
            follow_up=("18 months", "and stroke at 18 months"),
            intervention_events=("12 participants", "occurred in 12 participants receiving metformin"),
            comparator_events=("20 participants", "and 20 receiving placebo"),
            effect_estimate=NR),
    details("SYN002",
            population=("adults with type 2 diabetes", "320 adults with type 2 diabetes"),
            intervention=("metformin", "randomized equally to metformin or placebo"),
            comparator=("placebo", "randomized equally to metformin or placebo"),
            participants=("320 adults", "320 adults with type 2 diabetes"),
            primary_outcome=("cardiovascular death, myocardial infarction or stroke",
                             "cardiovascular death, myocardial infarction or stroke during 18 months"),
            follow_up=("18 months", "during 18 months"),
            intervention_events=("24 of 160", "Events occurred in 24 of 160 participants assigned metformin"),
            comparator_events=("16 of 160", "16 of 160 assigned placebo"),
            effect_estimate=NR),
    details("SYN008",
            population=("adults with type 2 diabetes", "160 adults with type 2 diabetes"),
            intervention=("metformin", "Six events occurred with metformin"),
            comparator=("placebo", "randomized placebo-controlled trial"),
            participants=("160 adults (80 per arm)", "enrolled 160 adults with type 2 diabetes, with 80 randomized to each arm"),
            primary_outcome=("myocardial infarction or stroke", "The primary endpoint was myocardial infarction or stroke"),
            follow_up=("12 months", "myocardial infarction or stroke at 12 months"),
            intervention_events=("6", "Six events occurred with metformin"),
            comparator_events=("7", "seven with placebo"),
            effect_estimate=NR),
]


def claim(cid, text, *quotes, kind="study"):
    return Claim(claim_id=cid, kind=kind, text=text, record_ids=list(dict.fromkeys(r for r, _ in quotes)),
                 quotes=[CitedQuote(record_id=r, quote=q) for r, q in quotes])


GOOD_CLAIMS = [
    claim("c1", "In a placebo-controlled trial, 12 people on metformin and 20 on placebo had a cardiovascular "
                "death, heart attack or stroke within 18 months.",
          ("SYN001", "The composite outcome occurred in 12 participants receiving metformin and 20 receiving placebo"),
          ("SYN001", "at 18 months")),
    claim("c2", "In another trial, 24 of 160 people on metformin and 16 of 160 on placebo had an event over 18 months.",
          ("SYN002", "Events occurred in 24 of 160 participants assigned metformin and 16 of 160 assigned placebo"),
          ("SYN002", "during 18 months")),
    claim("c3", "A further trial reported 6 events with metformin and 7 with placebo among 80 people per group "
                "at 12 months.",
          ("SYN008", "Six events occurred with metformin and seven with placebo"),
          ("SYN008", "with 80 randomized to each arm"), ("SYN008", "at 12 months")),
    claim("c4", "These trials point in different directions: fewer events with metformin in the first, more in "
                "the second and similar numbers in the third, and none reports confidence intervals or p values.",
          ("SYN001", "No confidence interval or p value is reported in this abstract"),
          ("SYN002", "The abstract provides no confidence interval or p value"),
          ("SYN008", "No confidence interval or p value is reported"), kind="comparison"),
]
STUDY_CLAIMS = GOOD_CLAIMS[:3]
DECISIONS = [ScreeningDecision(record_id=r, source_type="synthetic", decision="include") for r in INCLUDED] + [
    ScreeningDecision(record_id="SYN004", source_type="synthetic", decision="exclude",
                      failed_rules=["published before 2015"], metadata_only=True)]
GOOD = SummaryDraft(study_details=DETAILS, claims=GOOD_CLAIMS)
APPROVE = CriticReport(verdict="approve", issues=[])
REVISE = CriticReport(verdict="revise", issues=[
    CriticIssue(claim_id="c4", kind="missing_context", explanation="Say the outcomes differ between trials.")])


def make_llm(responses):
    ft = FakeTime()
    transport = FakeLLMTransport(responses)
    return LLM(SETTINGS, transport=transport, sleep=ft.sleep, clock=ft.clock), transport


# --- Content checks (ADR 0005, ADR 0010) ---

def test_correct_q1_draft_passes_all_checks():
    assert check_draft(GOOD, INCLUDED).ok


def failures_for(c):
    return check_draft(SummaryDraft(study_details=DETAILS, claims=[c]), INCLUDED).claim_failures.get(c.claim_id)


def test_pooled_total_is_rejected():
    c = claim("p", "Across these trials 42 events occurred with metformin.",
              ("SYN001", "occurred in 12 participants receiving metformin"), ("SYN002", "24 of 160"))
    assert failures_for(c) == ["numbers not in its quotes: 42"]


def test_counting_studies_in_words_is_rejected():
    c = claim("w", "The three trials disagree.", ("SYN001", "receiving placebo"))
    assert failures_for(c) == ["numbers not in its quotes: 3"]


def test_fake_quote_uncited_and_non_included_records_are_rejected():
    assert failures_for(claim("f", "Metformin helped.", ("SYN001", "metformin clearly prevented heart attacks"))) == [
        "quote not found in SYN001"]
    excluded = claim("x", "A small trial found one stroke per group.", ("SYN006", "One stroke was reported in each group"))
    assert "cites records that are not included: SYN006" in failures_for(excluded)


def test_mixing_synthetic_and_pubmed_is_rejected():
    pubmed = Record(record_id="999", source_type="pubmed", title="t", abstract="Metformin reduced events.",
                    publication_year=2022, pmid="999")
    c = claim("m", "Both sources agree.", ("SYN001", "receiving placebo"), ("999", "Metformin reduced events."))
    check = check_draft(SummaryDraft(study_details=DETAILS, claims=[c]), {**INCLUDED, "999": pubmed})
    assert "mixes synthetic and PubMed records" in check.claim_failures["m"]


def test_record_ids_in_claim_text_are_not_numbers():
    c = claim("i", "SYN001 reported 12 events with metformin.", ("SYN001", "occurred in 12 participants receiving metformin"))
    assert failures_for(c) is None


def test_detail_failures_and_finalize():
    bad = SummaryDraft(study_details=[
        details("SYN001", participants=("250 adults", "enrolled 240 adults"))] + DETAILS[1:], claims=GOOD_CLAIMS[:1] + [
        claim("p", "Overall 42 events occurred.", ("SYN001", "receiving placebo"))])
    check = check_draft(bad, INCLUDED)
    assert check.detail_failures[("SYN001", "participants")] == ["numbers not in its quote: 250"]
    assert check.detail_failures[("SYN001", "follow_up")] == ["missing"]
    final, removed, warnings = finalize(bad, check, INCLUDED)
    assert [c.claim_id for c in final.claims] == ["c1"] and [r.claim.claim_id for r in removed] == ["p"]
    syn001 = {d.name: d.value for d in final.study_details[0].details}
    assert syn001["participants"] == UNVERIFIED and syn001["follow_up"] == UNVERIFIED
    assert all(w.kind == "removed_unsupported" and w.message.startswith("removed: unsupported") for w in warnings[:-1])
    assert warnings[-1].message == "no cross-study comparison for the synthetic studies"


# --- Comparison claim (ADR 0005, improvement after testing) ---

def test_missing_comparison_fails_the_check_with_a_precise_message():
    check = check_draft(SummaryDraft(study_details=DETAILS, claims=STUDY_CLAIMS), INCLUDED)
    assert not check.ok and check.missing_comparisons == {"synthetic": ["SYN001", "SYN002", "SYN008"]}
    assert check.describe()[0].startswith(
        "missing comparison for the synthetic studies: add one claim of kind 'comparison' citing SYN001, SYN002, SYN008")


def test_comparison_must_cite_every_included_study_and_not_pool():
    partial = claim("k", "Fewer events with metformin in the first trial and more in the second.",
                    ("SYN001", "receiving placebo"), ("SYN002", "assigned placebo"), kind="comparison")
    assert failures_for(partial) == ["a comparison must cite every included study from its source; missing SYN008"]
    pooled = claim("k", "Across these trials 42 events occurred with metformin.", ("SYN001", "receiving placebo"),
                   ("SYN002", "assigned placebo"), ("SYN008", "seven with placebo"), kind="comparison")
    assert failures_for(pooled) == ["numbers not in its quotes: 42"]


def test_single_included_study_needs_no_comparison():
    one = {"SYN001": INCLUDED["SYN001"]}
    assert check_draft(SummaryDraft(study_details=DETAILS[:1], claims=STUDY_CLAIMS[:1]), one).ok
    lone = claim("k", "Fewer events with metformin.", ("SYN001", "receiving placebo"), kind="comparison")
    assert check_draft(SummaryDraft(study_details=DETAILS[:1], claims=[lone]), one).claim_failures == {
        "k": ["a comparison needs two or more included studies from its source"]}


def test_missing_comparison_is_retried_then_warned_never_written_by_code():
    no_comparison = SummaryDraft(study_details=DETAILS, claims=STUDY_CLAIMS)
    (final, _, warnings), transport = produce([no_comparison, GOOD])
    assert "missing comparison for the synthetic studies" in transport.calls[1]["user"]
    assert final.claims == GOOD_CLAIMS and warnings == []

    (final, _, warnings), _ = produce([no_comparison, no_comparison])
    assert final.claims == STUDY_CLAIMS  # code adds no claim of its own
    assert [w.message for w in warnings] == ["no cross-study comparison for the synthetic studies"]


# --- Summarizer retry (one per pass) ---

def produce(responses):
    llm, transport = make_llm({SummaryDraft: responses})
    out = produce_draft(INCLUDED, FORM, llm=llm, prompt=load_prompt("summarizer", SETTINGS.prompts_dir))
    return out, transport


def test_failed_checks_retried_once_and_fixed():
    bad = GOOD.model_copy(update={"claims": GOOD_CLAIMS + [claim("p", "42 events overall.", ("SYN001", "receiving placebo"))]})
    (final, removed, warnings), transport = produce([bad, GOOD])
    assert len(transport.calls) == 2 and "claim p: numbers not in its quotes: 42" in transport.calls[1]["user"]
    assert final.claims == GOOD_CLAIMS and removed == [] and warnings == []


def test_still_failing_after_retry_is_removed_with_warning():
    bad = GOOD.model_copy(update={"claims": GOOD_CLAIMS + [claim("p", "42 events overall.", ("SYN001", "receiving placebo"))]})
    (final, removed, warnings), transport = produce([bad, bad])
    assert len(transport.calls) == 2
    assert [r.claim.claim_id for r in removed] == ["p"] and len(warnings) == 1


def test_invalid_output_uses_the_one_retry():
    (final, _, _), transport = produce(["not json", GOOD])
    assert len(transport.calls) == 2 and final.claims == GOOD_CLAIMS


def test_invalid_twice_gives_no_synthesis():
    (final, _, warnings), transport = produce(["not json"])
    assert len(transport.calls) == 2 and final.claims == []
    assert all(d.value == UNVERIFIED for sd in final.study_details for d in sd.details)


# --- Critic loop (ADR 0006) ---

def round_with(summaries, reports):
    llm, transport = make_llm({SummaryDraft: summaries, CriticReport: reports})
    return run_round(1, INCLUDED, FORM, decisions=DECISIONS, llm=llm, settings=SETTINGS), transport


def test_critic_approves_first_draft():
    result, transport = round_with([GOOD], [APPROVE])
    assert len(result.drafts) == 1 and result.warnings == []
    assert [c["schema"] for c in transport.calls] == ["SummaryDraft", "CriticReport"]
    # Only the Critic runs at medium thinking (ADR 0009).
    assert [c["thinking_level"] for c in transport.calls] == ["low", "medium"]


def test_critic_revise_is_capped_at_one_revision():
    result, transport = round_with([GOOD], [REVISE])
    assert [d.pass_number for d in result.drafts] == [1, 2]
    assert [c["schema"] for c in transport.calls] == ["SummaryDraft", "CriticReport", "SummaryDraft", "CriticReport"]
    assert "Critic issues to address" in transport.calls[2]["user"]  # revision sees the issues
    assert [w.kind for w in result.warnings] == ["critic_issue"]


def test_critic_gets_abstracts_screening_results_and_labelled_code_findings():
    removed = GOOD.model_copy(update={"claims": GOOD_CLAIMS + [claim("p", "42 events overall.", ("SYN001", "receiving placebo"))]})
    result, transport = round_with([removed, removed], [APPROVE])
    critic_user = next(c["user"] for c in transport.calls if c["schema"] == "CriticReport")
    assert all(f'record_id="{r}"' in critic_user for r in INCLUDED)
    assert "<screening_results>" in critic_user and "SYN004 (synthetic): exclude (published before 2015)" in critic_user
    assert "removed claims are unsupported and never evidence" in critic_user
    assert 'removed: unsupported claim "42 events overall."' in critic_user
    assert '"42 events overall."' not in critic_user.split("Draft to review:")[1]  # not in the draft itself
    # Code-check warnings stay even though the Critic approved.
    assert [w.kind for w in result.warnings] == ["removed_unsupported"]


def test_critic_revise_then_approve():
    result, _ = round_with([GOOD], [REVISE, APPROVE])
    assert len(result.drafts) == 2 and result.warnings == []


def test_critic_invalid_twice_goes_to_human_with_warning():
    result, _ = round_with([GOOD], ["oops"])
    assert len(result.drafts) == 1 and not result.final.critic_available
    assert [w.message for w in result.warnings] == ["Critic review unavailable"]


def test_critic_issue_naming_unknown_claim_is_invalid():
    unknown = CriticReport(verdict="revise", issues=[CriticIssue(claim_id="c99", kind="unfair_claim", explanation="x")])
    result, _ = round_with([GOOD], [unknown])
    assert [w.kind for w in result.warnings] == ["critic_unavailable"]


def test_no_claims_skips_critic_and_says_no_synthesis():
    result, transport = round_with(["not json"], [APPROVE])
    assert "CriticReport" not in [c["schema"] for c in transport.calls]
    messages = [w.message for w in result.warnings]
    assert messages[-2:] == ["no cross-study comparison for the synthetic studies", NO_SYNTHESIS]
    # One warning per record when all its details are missing, not one per detail.
    assert messages[:-2] == [
        f"removed: unsupported study details for {r} (missing from Summarizer output; shown as {UNVERIFIED})"
        for r in INCLUDED]


# --- Rendering ---

def test_render_q1_summary_and_evidence_table():
    from test_rules import SYNTHETIC_FACTS, run

    decisions = [screen_on_metadata(RECORDS[r]) or run(RECORDS[r], SYNTHETIC_FACTS[r]) for r in RECORDS]
    comparison_first = GOOD.model_copy(update={"claims": [GOOD_CLAIMS[3], *STUDY_CLAIMS]})
    summary = render_summary(comparison_first, RECORDS, decisions, ["synthetic"])
    assert summary.splitlines()[-1].startswith("- These trials point in different directions")  # comparison last
    assert summary.startswith("SYNTHETIC DATASET (fictional test records, not medical evidence)")
    assert "12 people on metformin and 20 on placebo" in summary and "[SYN001]" in summary
    assert "[SYN001] [SYN002] [SYN008]" in summary
    table = render_evidence_table(GOOD, RECORDS, decisions, ["synthetic"])
    assert "Included (3)" in table and "Needs review (1)" in table and "Excluded (4)" in table
    assert 'intervention events: 6 ["Six events occurred with metformin"]' in table
    assert "effect estimate: not reported" in table
    lines = table.splitlines()
    assert ("- [SYN003] Metformin and cardiovascular events in a randomized adult diabetes trial (2021): "
            "missing or unclear: total sample size") in lines
    assert ("- [SYN004] Earlier randomized metformin cardiovascular trial (2014): published before 2015 "
            "(other rules not checked) | published 2014 (metadata)") in lines
    syn005 = next(line for line in lines if line.startswith("- [SYN005]"))
    assert "animal study" in syn005 and '| "randomly allocated 80 mice"' in syn005
    # Excluded and needs-review records are one line each: no study details for them.
    assert sum(line.startswith("  participants:") for line in lines) == 3
