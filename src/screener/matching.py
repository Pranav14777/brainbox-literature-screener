"""Quote and number checks (ADR 0010).

Both checks are deterministic and run on the LLM's output before any decision
uses it. A fact or claim that fails becomes unknown or is removed; it never
reaches the rule engine or the email as if it were verified.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_DIGITS = [w for w, v in _UNITS.items() if 1 <= v <= 9]

# "twenty-four" / "twenty four" first, then single words. \b stops "seven"
# matching inside "seventeen".
_NUMBER_WORD = re.compile(
    rf"\b(?:({'|'.join(_TENS)})(?:[\s-]+({'|'.join(_DIGITS)}))?|({'|'.join(_UNITS)}))\b",
    re.IGNORECASE,
)

# A whole number: thousands-comma form or plain, optionally with a decimal part.
# The lookbehind rejects digits glued to letters or other digits (SYN001, HbA1c),
# so "6" is never found inside "160". Signs are ignored on both sides of a check.
_NUMBER = re.compile(r"(?<![A-Za-z\d.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")

# "type 1/2 diabetes" is a disease name, not a number (ADR 0010).
_DIABETES_TYPE = re.compile(r"\b(type)[\s-]*[12](?=[\s-]*diabetes\b)", re.IGNORECASE)

# Lancet-style decimals use a raised dot (0·010). Between two digits it is a decimal point (ADR 0010).
_RAISED_DOT = re.compile(r"(?<=\d)[·∙⋅](?=\d)")


def normalise_text(text: str) -> str:
    """NFKC, raised-dot decimals as full stops, and collapsed whitespace.

    Applied to both sides of every quote and number check, so typographic variants
    (line breaks, non-breaking spaces, 0·010 vs 0.010) never fail a check.
    """
    text = _RAISED_DOT.sub(".", unicodedata.normalize("NFKC", text))
    return " ".join(text.split())


def quote_in_source(quote: str | None, source: str | None) -> bool:
    if not quote or not source:
        return False
    q = normalise_text(quote)
    return bool(q) and q in normalise_text(source)


def _words_to_digits(text: str) -> str:
    def replace(m: re.Match) -> str:
        if m.group(3):
            return str(_UNITS[m.group(3).lower()])
        value = _TENS[m.group(1).lower()]
        if m.group(2):
            value += _UNITS[m.group(2).lower()]
        return str(value)

    return _NUMBER_WORD.sub(replace, text)


def numbers_in(text: str) -> list[Decimal]:
    """All numbers in text, in order: words converted, commas removed, decimals kept, ranges split."""
    text = _DIABETES_TYPE.sub(r"\1", normalise_text(text))
    text = _words_to_digits(text)
    return [Decimal(m.group().replace(",", "")) for m in _NUMBER.finditer(text)]


def number_in_text(value: int | float | Decimal, text: str) -> bool:
    return Decimal(str(value)) in set(numbers_in(text))


def unsupported_numbers(text: str, sources: list[str]) -> list[Decimal]:
    """Numbers in text that appear in none of the sources."""
    allowed: set[Decimal] = set()
    for source in sources:
        allowed.update(numbers_in(source))
    return [n for n in numbers_in(text) if n not in allowed]
