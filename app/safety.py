"""The dosing backstop, shared by everything a model writes for the reader.

The prompt is the main defence — every system prompt in the app restates the
boundary. This is what happens when a model crosses it anyway, and it lives on
its own because two callers need it: the cards a chat session writes
(`app/chat_cards.py`) and the check questions a tutor turn asks (`app/chat.py`).

Anything tripping this is dropped silently. One fewer card, or a turn with no
question attached to it, is a non-event; a flashcard telling someone how much
insulin to take is not.
"""

from __future__ import annotations

import re

# Deliberately narrow, and matching within one sentence only. These catch the
# shape of a dosing instruction in the two word orders it comes in: "you would
# take 3 units" and "increase your basal". Broad matching here would eat
# legitimate mechanism text, which is a real cost: an explanation of why
# rapid-acting insulin takes fifteen minutes to work is the whole point of the
# app, and it is made of the same words as a dosing instruction.
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

_DOSING = (
    # "you would take about 3 units to correct that"
    re.compile(
        rf"\b(?:you|your)\b[^.?!]{{0,60}}\b(?:{_VERB})\b[^.?!]{{0,60}}\b(?:{_AMOUNT})\b",
        re.IGNORECASE,
    ),
    # "increase your basal insulin overnight" — the imperative, where the verb
    # comes first and there is no "you" in front of it at all.
    re.compile(
        rf"\b(?:{_VERB})\b[^.?!]{{0,20}}\byour\b[^.?!]{{0,30}}\b(?:{_AMOUNT})\b",
        re.IGNORECASE,
    ),
)


# The interrogative order, which the two above miss because they expect the
# amount after the verb: "how much insulin would you take". Only questions are
# run through this — a quiz is where the boundary is easiest to cross by
# accident, since "how much would you..." is a natural way to write a question
# and a dosing suggestion at the same time. It costs the occasional honest
# question ("how much insulin does a working pancreas release?"), which is a
# cheap loss: one fewer check, and the tutor still explained the mechanism.
_DOSE_QUESTION = re.compile(
    rf"\bhow (?:much|many)\b[^.?!]{{0,60}}\b(?:{_AMOUNT})\b|"
    rf"\bhow (?:much|many)\b[^.?!]{{0,40}}\b(?:{_VERB})\b[^.?!]{{0,40}}\byou(?:r)?\b",
    re.IGNORECASE,
)


def looks_like_dosing(text: str) -> bool:
    """Whether a sentence reads as an instruction to change insulin."""
    return any(pattern.search(text) for pattern in _DOSING)


def looks_like_dose_question(text: str) -> bool:
    """Whether a question asks the reader to pick an amount of insulin.

    A dosing suggestion wearing a quiz's clothes. Separate from
    `looks_like_dosing` because it is deliberately more trigger-happy, and only
    questions can afford that.
    """
    return looks_like_dosing(text) or bool(_DOSE_QUESTION.search(text))
