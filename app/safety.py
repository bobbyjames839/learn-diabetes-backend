"""The dosing backstop, shared by everything a model writes for the reader.

The prompt is the main defence — every system prompt in the app restates the
boundary. This is what happens when a model crosses it anyway, and it lives on
its own because several callers need it: the cards a chat session writes
(`app/chat_cards.py`), its recap (`app/chat_summary.py`), and the check
questions and fallback replies a tutor turn produces (`app/chat.py`).

Anything tripping this is dropped silently. One fewer card, or a turn with no
question attached to it, is meant to be a non-event; a flashcard telling
someone how much insulin to take is not. In practice the original, single
`looks_like_dosing` shape turned out too eager to use that way: live-tested
against the real model, ordinary factual sentences like "how long after you
inject insulin does it start to have an effect" or "you take insulin because
your pancreas cannot make its own" tripped it — same `you ... verb ...
insulin` shape as an instruction — and a conversation about T1D produces
sentences like that constantly. Checks were being silently dropped far more
often than the model was actually failing to write one.

`looks_like_dosing_instruction` is what every caller actually filters model
output with now. It keeps the imperative shape ("increase your basal insulin
overnight") eager, since that phrasing is rare outside an actual instruction —
but the declarative shape ("you ... take ... insulin") is only trusted when
there is a number next to the amount, which is what tells "take 3 units"
apart from a mechanism sentence that only mentions insulin.
"""

from __future__ import annotations

import re

# Deliberately narrow, and matching within one sentence only. Broad matching
# here would eat legitimate mechanism text, which is a real cost: an
# explanation of why rapid-acting insulin takes fifteen minutes to work is the
# whole point of the app, and it is made of the same words as a dosing
# instruction.
#
# "bolus" is deliberately left out of _VERB even though it can be used as one
# ("you should bolus 3 units") — it is also in _AMOUNT, and "bolus insulin" is
# ordinary mechanism vocabulary in this app (basal vs. bolus is a core topic).
# With "bolus" in both lists, a sentence as plain as "carbohydrate you ate,
# which needs bolus insulin to match it" reads as "you ... bolus ... insulin"
# and trips as a dosing instruction. The other imperative verbs below still
# catch a real "you should bolus/give/take 3 units" instruction.
_VERB = (
    r"take|takes|inject|give|dose|correct|correction|"
    r"increase|decrease|reduce|adjust|lower|raise"
)
_AMOUNT = r"unit|units|dose|doses|basal|bolus|ratio|insulin"

# "you would take about 3 units to correct that" — the declarative order,
# verb before the amount, with "you"/"your" out front. The wide 60-character
# gaps on both sides are exactly why this is the one that over-triggers: they
# let it span an entire mechanism sentence ("you take insulin because your
# pancreas cannot make its own") as readily as an actual instruction.
_DECLARATIVE = re.compile(
    rf"\b(?:you|your)\b[^.?!]{{0,60}}\b(?:{_VERB})\b[^.?!]{{0,60}}\b(?:{_AMOUNT})\b",
    re.IGNORECASE,
)

# "increase your basal insulin overnight" — the imperative, verb first, no
# "you" in front of it, and a much tighter gap on both sides. Rare outside an
# actual instruction, so this stays eager rather than needing a number.
_IMPERATIVE = re.compile(
    rf"\b(?:{_VERB})\b[^.?!]{{0,20}}\byour\b[^.?!]{{0,30}}\b(?:{_AMOUNT})\b",
    re.IGNORECASE,
)

_DOSING = (_DECLARATIVE, _IMPERATIVE)


def looks_like_dosing(text: str) -> bool:
    """Whether a sentence reads as an instruction to change insulin.

    The eager, no-number match — kept for `test_chat_cards.py`'s
    `TestDosingScreen`, which pins this exact behaviour. Everything that
    actually filters model output uses `looks_like_dosing_instruction`
    below instead, since this stayed too eager for that once tested against
    real replies.
    """
    return any(pattern.search(text) for pattern in _DOSING)


# The interrogative order a dosing *question* comes in — "how much insulin
# would you take" — which the shapes above miss because they expect the
# amount after the verb, not after "how much/many". Deliberately narrower
# than `looks_like_dosing`: it requires the literal phrase "how much" or "how
# many", which a genuine mechanism question essentially never uses ("how long
# does insulin take to start working", not "how much insulin..."). If a check
# frames a dosing suggestion as multiple-choice, the options themselves (each
# a specific amount, e.g. "2 units") are what actually catch it — see
# `looks_like_dosing_instruction`.
_DOSE_QUESTION = re.compile(
    rf"\bhow (?:much|many)\b[^.?!]{{0,60}}\b(?:{_AMOUNT})\b|"
    rf"\bhow (?:much|many)\b[^.?!]{{0,40}}\b(?:{_VERB})\b[^.?!]{{0,40}}\byou(?:r)?\b",
    re.IGNORECASE,
)


def asks_for_dose_amount(text: str) -> bool:
    """Whether a question directly asks the reader to name an amount of
    insulin — "how much would you take for that?" — a dosing suggestion
    wearing a quiz's question mark.
    """
    return bool(_DOSE_QUESTION.search(text))


# The declarative shape, but only trusted when a number sits next to the
# amount word too — "3 units", "a couple more units" — which is what actually
# separates a dosing instruction from a mechanism sentence that only
# *mentions* insulin.
_DECLARATIVE_WITH_NUMBER = re.compile(
    rf"\b(?:you|your)\b[^.?!]{{0,60}}\b(?:{_VERB})\b[^.?!]{{0,60}}\d[^.?!]{{0,20}}\b(?:{_AMOUNT})\b|"
    rf"\b(?:you|your)\b[^.?!]{{0,60}}\d[^.?!]{{0,20}}\b(?:{_AMOUNT})\b[^.?!]{{0,60}}\b(?:{_VERB})\b",
    re.IGNORECASE,
)


def looks_like_dosing_instruction(text: str) -> bool:
    """Whether a piece of text names an actual dosing instruction.

    What every caller in the app actually filters model output with. The
    imperative shape ("increase your basal insulin") stays eager; the
    declarative shape ("you ... take ... insulin") additionally needs a
    number next to the amount, since without one it matches ordinary
    mechanism sentences just as readily as an instruction.
    """
    return bool(_IMPERATIVE.search(text)) or bool(_DECLARATIVE_WITH_NUMBER.search(text))
