from decimal import Decimal as D

from screener.matching import number_in_text, numbers_in, quote_in_source, unsupported_numbers

SYN008 = (
    "This synthetic randomized placebo-controlled trial enrolled 160 adults with type 2 diabetes, "
    "with 80 randomized to each arm. The primary endpoint was myocardial infarction or stroke at "
    "12 months. Six events occurred with metformin and seven with placebo. No confidence interval "
    "or p value is reported."
)


def test_number_words_become_digits():
    assert numbers_in("Six events occurred with metformin and seven with placebo") == [D(6), D(7)]
    assert numbers_in("Ninety participants") == [D(90)]
    assert numbers_in("twenty-four and twenty four and seventeen") == [D(24), D(24), D(17)]


def test_whole_number_match_not_substring():
    assert not number_in_text(6, "enrolled 160 adults")
    assert number_in_text(160, "enrolled 160 adults")


def test_decimals_and_ranges():
    assert numbers_in("HR 0.85 (95% CI 0.74–0.97)") == [D("0.85"), D(95), D("0.74"), D("0.97")]
    assert numbers_in("CI 0.74-0.97") == [D("0.74"), D("0.97")]
    assert number_in_text(0.85, "HR 0.850")


def test_commas_removed():
    assert numbers_in("1,200 participants") == [D(1200)]


def test_digits_glued_to_letters_are_not_numbers():
    assert numbers_in("SYN001 reports 12/120 versus 20/120") == [D(12), D(120), D(20), D(120)]
    assert numbers_in("HbA1c") == []


def test_diabetes_type_is_not_a_number():
    assert numbers_in("adults with type 2 diabetes") == []
    assert numbers_in("Type-1 diabetes and type 2  diabetes") == []
    assert numbers_in("type 2 groups") == [D(2)]


# Real sentence from the UKPDS 91 abstract (PMID 38772405), with Lancet-style raised-dot decimals.
UKPDS91 = (
    "Early intensive glycaemic control with metformin therapy, compared with conventional glycaemic control, "
    "showed overall relative risk reductions of 20% (95% CI 5-32; p=0·010) for death from any cause and "
    "31% (12-46; p=0·003) for myocardial infarction."
)


def test_raised_dot_decimals_ukpds91():
    assert numbers_in(UKPDS91) == [D(20), D(95), D(5), D(32), D("0.010"), D(31), D(12), D(46), D("0.003")]
    claim = ("Metformin showed relative risk reductions of 20% for death from any cause (p=0.010) "
             "and 31% for myocardial infarction (p=0.003).")
    assert unsupported_numbers(claim, [UKPDS91]) == []
    assert unsupported_numbers(claim.replace("0.010", "0·010"), [UKPDS91]) == []
    assert quote_in_source("20% (95% CI 5-32; p=0.010)", UKPDS91)  # quote typed with a full stop still matches


def test_quote_check():
    assert quote_in_source("enrolled 160  adults\nwith type 2", SYN008)
    assert not quote_in_source("enrolled 999 adults", SYN008)
    assert not quote_in_source("", SYN008)
    assert not quote_in_source(None, SYN008)


def test_brief_expected_counts_pass_and_pooled_total_fails():
    quote = "Six events occurred with metformin and seven with placebo"
    assert unsupported_numbers("6 of 80 versus 7 of 80", [quote, "with 80 randomized to each arm"]) == []
    assert unsupported_numbers("13 events in total", [SYN008]) == [D(13)]
