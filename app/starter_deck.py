"""The 20 cards every reader starts with.

Written by hand, once, here — not derived from lesson takeaways. The derived
version was the wrong shape twice over: a lesson's key takeaway is a *statement*
with no question to recall it from, and every card cut from the same lesson
carried the same front, so the starting deck read as five cards printed four
times each.

These are real question → answer pairs. Twenty distinct fronts, twenty distinct
backs, spread across all twelve lessons and all four categories, ordered so the
first pass through the deck moves outward from the basics rather than sitting in
one corner of the curriculum.

Each card names the lesson it belongs to, so the deck can link it up and the
reader can go and read the long version. `category` and `topic` are carried here
as well rather than only looked up, so a card still renders correctly if the
curriculum is reordered or a slug is renamed out from under it.

They are seeded with `kind="takeaway"` — the deck's filler tier — because that
is exactly what they are. They fill twenty slots on day one and give them up,
oldest first, to the checkpoints the reader actually misses and the cards their
tutor sessions write. A reader who works through the app will not have many of
these left, which is the point.

Educational only, like everything else that generates or carries text here: these
explain why glucose behaves the way it does and never suggest a dose, a ratio, or
a treatment action.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StarterCard:
    front: str
    back: str
    lesson_slug: str
    category: str
    # Shown in the card footer. Normally overwritten with the lesson's real
    # title at seed time; used as-is if that lesson isn't in the database.
    topic: str


STARTER_DECK: tuple[StarterCard, ...] = (
    StarterCard(
        front="Glucose arrives in your blood from two places. What are they?",
        back=(
            "Food, and your own liver — which stores glucose and releases it steadily "
            "between meals and overnight, so the supply never stops just because you have."
        ),
        lesson_slug="what-blood-glucose-is",
        category="basics",
        topic="What blood glucose is and what moves it",
    ),
    StarterCard(
        front=(
            "Almost everything that affects glucose does so in one of two ways. "
            "Which two?"
        ),
        back=(
            "By changing how fast glucose enters the blood, or how fast it leaves. "
            "Inflow and outflow — food, insulin, exercise, illness and stress all act "
            "on one side or the other, and that is the whole system."
        ),
        lesson_slug="what-blood-glucose-is",
        category="basics",
        topic="What blood glucose is and what moves it",
    ),
    StarterCard(
        front="What does time in range tell you that an average glucose cannot?",
        back=(
            "How much of the day was actually spent in range. A steady 8 mmol/L and a "
            "day swinging between 3 and 15 can average out to the same number — the "
            "average hides which one you had."
        ),
        lesson_slug="target-ranges-and-time-in-range",
        category="basics",
        topic="Target ranges and time in range",
    ),
    StarterCard(
        front="Why is time spent below range treated as more serious than time above?",
        back=(
            "Because the risks are not symmetrical. A low is dangerous within minutes; "
            "the harm from highs builds up over years. The same percentage of the day "
            "means something very different on each side."
        ),
        lesson_slug="target-ranges-and-time-in-range",
        category="basics",
        topic="Target ranges and time in range",
    ),
    StarterCard(
        front=(
            "A CGM reads 7.0 with a steep upward arrow. Later it reads 7.0 again, with "
            "a steep downward arrow. Why are these not the same situation?"
        ),
        back=(
            "The number says where you are; the arrow says where you are going. Same "
            "reading, opposite directions — in twenty minutes those two 7.0s will be "
            "nowhere near each other."
        ),
        lesson_slug="reading-cgm-trend-arrows",
        category="basics",
        topic="Reading CGM trend arrows",
    ),
    StarterCard(
        front="A CGM does not measure blood. What does it measure, and what does that cause?",
        back=(
            "The fluid between cells just under the skin. Glucose reaches it a few "
            "minutes after it reaches the blood, so the reading lags — and the gap is "
            "widest exactly when glucose is moving fastest and you most want to trust it."
        ),
        lesson_slug="reading-cgm-trend-arrows",
        category="basics",
        topic="Reading CGM trend arrows",
    ),
    StarterCard(
        front="Fibre is counted as carbohydrate on a label. Why is it treated differently?",
        back=(
            "Because it is not absorbed as glucose. It passes through largely intact, "
            "so it does not raise blood glucose the way the rest of the carbohydrate "
            "on that label does."
        ),
        lesson_slug="counting-carbohydrate",
        category="food",
        topic="Counting carbohydrate",
    ),
    StarterCard(
        front="Why can the same portion of pasta be counted two very different ways?",
        back=(
            "Because cooked and raw weights are not the same thing — pasta and rice "
            "absorb a lot of water. A number that does not say which one it means can "
            "be out by a factor of two or three."
        ),
        lesson_slug="counting-carbohydrate",
        category="food",
        topic="Counting carbohydrate",
    ),
    StarterCard(
        front=(
            "Two meals contain identical carbohydrate but produce completely different "
            "glucose curves. What is the difference between how much and how fast?"
        ),
        back=(
            "The amount sets how far glucose eventually rises. The speed sets how "
            "sharply it gets there. Same total, different shape — one a gentle hill, "
            "the other a spike and a fall."
        ),
        lesson_slug="fast-and-slow-carbohydrate",
        category="food",
        topic="Fast and slow carbohydrate",
    ),
    StarterCard(
        front="Why does juice act faster than the same carbohydrate eaten as whole fruit?",
        back=(
            "Liquids leave the stomach quickly and need almost no breaking down. Whole "
            "fruit has fibre and cell structure to get through first, so the same sugar "
            "arrives spread out over a much longer window."
        ),
        lesson_slug="fast-and-slow-carbohydrate",
        category="food",
        topic="Fast and slow carbohydrate",
    ),
    StarterCard(
        front=(
            "A high-fat meal seems to do almost nothing at first, then glucose climbs "
            "hours later. What happened?"
        ),
        back=(
            "Fat slows how quickly the stomach empties, so the carbohydrate arrives "
            "late and spread out rather than not at all. The rise was delayed, not "
            "avoided — which is why it turns up long after the meal looked finished."
        ),
        lesson_slug="why-fat-and-protein-delay-the-rise",
        category="food",
        topic="Why fat and protein delay the rise",
    ),
    StarterCard(
        front="What actually goes wrong in type 1 diabetes?",
        back=(
            "The immune system's T cells destroy the beta cells in the pancreas that "
            "make insulin. It is an autoimmune disease — not something caused by diet "
            "or by anything the person did."
        ),
        lesson_slug="why-the-immune-system-attacks-beta-cells",
        category="basics",
        topic="Why the immune system attacks beta cells",
    ),
    StarterCard(
        front="If beta cells are lost gradually, why do symptoms appear so suddenly?",
        back=(
            "The process runs silently for months or years while the surviving cells "
            "cover for the losses. Symptoms only start once too few are left to keep "
            "up — so the onset feels abrupt even though the cause was not."
        ),
        lesson_slug="why-the-immune-system-attacks-beta-cells",
        category="basics",
        topic="Why the immune system attacks beta cells",
    ),
    StarterCard(
        front="What does insulin actually do when it reaches a cell?",
        back=(
            "It binds a receptor on the cell's surface, and that signal brings glucose "
            "transporters to the membrane to let glucose in. Insulin is the key at the "
            "door — it does not burn glucose or destroy it."
        ),
        lesson_slug="what-insulin-does-at-the-cell",
        category="insulin",
        topic="What insulin actually does at the cell",
    ),
    StarterCard(
        front="Insulin does something to the liver as well as to muscle and fat. What?",
        back=(
            "It tells the liver to stop releasing its stored glucose. Without insulin "
            "the liver keeps pouring glucose into a bloodstream that already has too "
            "much — which is why missing insulin raises glucose even with no food at all."
        ),
        lesson_slug="what-insulin-does-at-the-cell",
        category="insulin",
        topic="What insulin actually does at the cell",
    ),
    StarterCard(
        front="Which hormones push glucose up, and why does the body have several?",
        back=(
            "Glucagon, adrenaline, cortisol and growth hormone. Falling too low is an "
            "immediate danger, so the body keeps more than one independent way to raise "
            "glucose — there is no equivalent redundancy for bringing it down."
        ),
        lesson_slug="the-counter-regulatory-system",
        category="basics",
        topic="The hormones that push glucose up",
    ),
    StarterCard(
        front="Why can stress or illness raise glucose when no food is involved?",
        back=(
            "Adrenaline and cortisol tell the liver to release stored glucose and make "
            "cells temporarily less responsive to insulin. The glucose is coming from "
            "inside you, not from a meal."
        ),
        lesson_slug="the-counter-regulatory-system",
        category="basics",
        topic="The hormones that push glucose up",
    ),
    StarterCard(
        front="How can ketones be high at the same time as blood glucose is high?",
        back=(
            "Because the shortage is insulin, not glucose. Without insulin the glucose "
            "cannot get into cells, so the liver burns fat for fuel instead and makes "
            "ketones — glucose piles up outside the cells while the cells starve."
        ),
        lesson_slug="where-ketones-come-from",
        category="troubleshooting",
        topic="Where ketones come from",
    ),
    StarterCard(
        front="What is HbA1c actually measuring?",
        back=(
            "The share of your haemoglobin that has glucose stuck to it. That happens "
            "slowly and does not reverse, so it reflects average glucose across the "
            "lifespan of red blood cells — roughly the last two to three months."
        ),
        lesson_slug="what-hba1c-actually-measures",
        category="basics",
        topic="What HbA1c actually measures",
    ),
    StarterCard(
        front="What does a CGM sensor physically detect under the skin?",
        back=(
            "A tiny electrical current, produced by an enzyme on the sensor reacting "
            "with glucose in the surrounding fluid. The current is proportional to the "
            "glucose, and the transmitter converts it into the number you see."
        ),
        lesson_slug="how-cgm-sensors-work",
        category="basics",
        topic="How CGM sensors work",
    ),
)
