"""Intake agent (ADR 0003): the LLM fills the intake form; code validates it and decides what happens.

Element values and what code does with them:
- other       -> rejected with a scope message
- unclear     -> one clarifying question
- not_stated  -> filled in from the tool's scope and shown in the printed interpretation,
                 unless all three are not_stated and the question doesn't refer to the
                 synthetic dataset, which is too vague and gets a clarifying question

Before the LLM call, a close misspelling of "metformin" (difflib ratio >= 0.85)
gets a yes/no confirmation; the intervention is the only field with a literal check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable

from screener.config import Settings
from screener.errors import InvalidOutputError
from screener.llm import LLM
from screener.models import ErrorEvent, IntakeForm, SourceType
from screener.prompts import Prompt, data_block, load_prompt
from screener.tracing import traced

SCOPE = "metformin and cardiovascular outcomes in adults with type 2 diabetes"
SCOPE_VALUES = {
    "intervention": ("metformin", "metformin"),
    "outcome": ("cardiovascular", "cardiovascular outcomes"),
    "population": ("adults_t2d", "adults with type 2 diabetes"),
}
MAX_QUESTION_CHARS = 1000
_ELEMENTS = ("intervention", "outcome", "population")

# Spelling check for the intervention (ADR 0003). 0.85 accepts typos such as
# "metaformin" (0.947) and "metfromin" (0.889) and rejects other drug names such
# as "metoprolol" (0.526) and "dapagliflozin" (0.364).
DRUG = "metformin"
TYPO_THRESHOLD = 0.85


@dataclass
class IntakeOutcome:
    form: IntakeForm | None = None
    scope_filled: list[str] = field(default_factory=list)
    clarification: str | None = None
    correction: str | None = None  # e.g. "metaformin" when the user confirmed it meant metformin
    stop_reason: str | None = None


def find_typo(text: str) -> str | None:
    """A word close to "metformin" when the exact word is absent, else None."""
    words = re.findall(r"[a-z]+", text.lower())
    if DRUG in words:
        return None
    return next((w for w in words if SequenceMatcher(None, w, DRUG).ratio() >= TYPO_THRESHOLD), None)


def _confirm_typo(text: str, ask) -> tuple[str, str | None, str | None]:
    """Ask once about a close misspelling.

    Returns (text to classify, corrected word if confirmed, declined word if not).
    Text without a close misspelling is returned unchanged.
    """
    typo = find_typo(text)
    if typo is None:
        return text, None, None
    answer = (ask(f'Did you mean "{DRUG}"? [y/n]\n> ') if ask else None) or ""
    if answer.strip().lower() in ("y", "yes"):
        return re.sub(rf"\b{typo}\b", DRUG, text, flags=re.IGNORECASE), typo, None
    return text, None, typo


def cross_check(form: IntakeForm, text: str) -> IntakeForm:
    """A wrong accept is the risky error, so "metformin" must really appear in the question."""
    if form.intervention == "metformin" and "metformin" not in text.lower():
        return form.model_copy(update={"intervention": "unclear"})
    return form


def route_sources(form: IntakeForm) -> list[SourceType]:
    """ADR 0003 source routing. An explicit source wins over recency."""
    if form.sources == "pubmed":
        return ["pubmed"]
    if form.sources == "synthetic":
        return ["synthetic"]
    if form.sources == "both":
        return ["synthetic", "pubmed"]
    return ["pubmed"] if form.recency else ["synthetic", "pubmed"]


def _with_value(form: IntakeForm, value: str) -> list[str]:
    return [e for e in _ELEMENTS if getattr(form, e) == value]


def _problem(form: IntakeForm) -> tuple[str, list[str]] | None:
    """("other", fields) means reject; ("unclear", fields) means ask; None means accept."""
    if other := _with_value(form, "other"):
        return "other", other
    if unclear := _with_value(form, "unclear"):
        return "unclear", unclear
    if len(_with_value(form, "not_stated")) == len(_ELEMENTS) and form.sources not in ("synthetic", "both"):
        return "unclear", list(_ELEMENTS)
    return None


def apply_scope(form: IntakeForm) -> tuple[IntakeForm, list[str]]:
    """Fill not_stated elements from the tool's scope. Returns the filled form and which elements were filled."""
    filled = _with_value(form, "not_stated")
    return form.model_copy(update={e: SCOPE_VALUES[e][0] for e in filled}), filled


def describe_interpretation(form: IntakeForm, scope_filled: list[str], correction: str | None = None) -> str:
    """The interpreted form, printed at the start of a run and shown at human review (ADR 0003)."""
    lines = []
    for e in _ELEMENTS:
        if e in scope_filled:
            lines.append(f"{e}: not stated; using the tool's scope ({SCOPE_VALUES[e][1]})")
        else:
            lines.append(f"{e}: {getattr(form, e)} ('{getattr(form, e + '_wording')}')")
        if e == "intervention" and correction:
            lines[-1] += f"; spelling corrected from '{correction}', confirmed by the user"
    lines.append(f"sources: {', '.join(route_sources(form))}")
    lines.append(f"recent only: {'yes' if form.recency else 'no'}")
    return "\n".join(lines)


def _scope_message(form: IntakeForm, fields: list[str]) -> str:
    parts = [f"{f} ('{getattr(form, f + '_wording') or 'not stated'}')" for f in fields]
    return f"Out of scope: this tool covers {SCOPE}. Outside that scope: {', '.join(parts)}."


def clarifying_question(fields: list[str]) -> str:
    return (
        f"Your question is unclear about the {', '.join(fields)}. This tool covers {SCOPE}. "
        "Please restate your question."
    )


def _classify(text: str, llm: LLM, prompt: Prompt, on_error) -> IntakeForm | None:
    for attempt in (1, 2):  # one retry on invalid output (ADR 0002)
        try:
            form = llm.generate(
                agent="intake", prompt_version=prompt.version, system=prompt.text,
                user=data_block("question", text), schema=IntakeForm, on_error=on_error,
            )
            return cross_check(form, text)
        except InvalidOutputError as e:
            if on_error:
                on_error(ErrorEvent(stage="intake", error_class="invalid_output", message=str(e), attempt=attempt))
    return None


def _accept(form: IntakeForm, clarification: str | None = None, correction: str | None = None) -> IntakeOutcome:
    filled_form, filled = apply_scope(form)
    return IntakeOutcome(form=filled_form, scope_filled=filled, clarification=clarification, correction=correction)


def _declined(word: str, clarification: str | None = None) -> IntakeOutcome:
    return IntakeOutcome(clarification=clarification,
                         stop_reason=f"Out of scope: this tool covers {SCOPE}. Outside that scope: intervention ('{word}').")


@traced("intake")
def run_intake(
    question: str,
    *,
    llm: LLM,
    settings: Settings,
    ask: Callable[[str], str | None] | None = None,
    on_error: Callable[[ErrorEvent], None] | None = None,
) -> IntakeOutcome:
    """Interpret the question. Stops with a reason instead of guessing."""
    text = question.strip()
    if not text:
        return IntakeOutcome(stop_reason="Empty question: please enter a research question.")
    if len(text) > MAX_QUESTION_CHARS:
        return IntakeOutcome(stop_reason=f"Question too long: keep it under {MAX_QUESTION_CHARS} characters.")

    # A close misspelling of the drug is confirmed in code before the LLM sees the text.
    text, correction, declined = _confirm_typo(text, ask)
    if declined:
        return _declined(declined)

    prompt = load_prompt("intake", settings.prompts_dir)
    form = _classify(text, llm, prompt, on_error)
    if form is None:
        return IntakeOutcome(stop_reason="Could not interpret the question: the model output was invalid twice.")
    problem = _problem(form)
    if problem is None:
        return _accept(form, correction=correction)
    kind, fields = problem
    if kind == "other":
        return IntakeOutcome(form=form, stop_reason=_scope_message(form, fields))

    # One clarifying question, then stop if still unclear.
    answer = ask(clarifying_question(fields) + "\n> ") if ask else None
    if not answer or not answer.strip():
        return IntakeOutcome(form=form, stop_reason=f"Question unclear about the {', '.join(fields)}; no clarification given.")
    answer, answer_correction, declined = _confirm_typo(answer.strip(), ask)
    if declined:
        return _declined(declined, clarification=answer)
    correction = correction or answer_correction
    form = _classify(f"{text}\nClarification: {answer}", llm, prompt, on_error)
    if form is None:
        return IntakeOutcome(clarification=answer, stop_reason="Could not interpret the clarified question: the model output was invalid twice.")
    problem = _problem(form)
    if problem is None:
        return _accept(form, clarification=answer, correction=correction)
    kind, fields = problem
    if kind == "other":
        return IntakeOutcome(form=form, clarification=answer, stop_reason=_scope_message(form, fields))
    return IntakeOutcome(
        form=form, clarification=answer,
        stop_reason=f"Still unclear about the {', '.join(fields)} after one clarifying question.",
    )
