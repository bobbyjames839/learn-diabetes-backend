"""Turning a lesson into checkpoint questions.

A checkpoint is one multiple-choice question placed after a section of the
lesson. Its purpose is as much diagnostic as pedagogical: every wrong option
carries the misconception a reader who picks it is holding, so an answer tells
us *which* wrong model of glucose someone has, not merely that they missed one.

Two things follow from the reader never seeing the section while they answer.
A question that could be answered by scanning the text for a matching phrase is
worthless here, so checkpoints are transfer questions — a new situation, and a
prediction to make. And a wrong answer is a teaching moment rather than a score,
so every wrong option also carries `coaching`: what the teacher says back to
someone holding that particular misconception, before letting them try again.

The prompt and the validation live here, apart from the HTTP call, so both can
be tested without a network.
"""

from __future__ import annotations

import random

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.llm import complete_json
from app.sections import Section

# The same boundary the lesson authoring prompt carries. A question that asks
# "how much insulin would you take here?" would be a dosing suggestion wearing a
# quiz's clothes, so the rule is restated for the generator.
SYSTEM_PROMPT = """\
You are a patient diabetes educator writing the questions you ask a learner
partway through a lesson, to find out whether the idea actually landed.

You will be given one lesson, already split into numbered sections. Write exactly
one multiple-choice question per section, testing the idea that section teaches.

Hard safety rules — these override everything else:
- Never suggest, imply, or ask the reader to choose an insulin dose, correction
  factor, carb ratio, basal rate, or any specific treatment action.
- Questions test understanding of MECHANISM — why glucose behaves as it does.
- Any numbers are illustrative and belong to a hypothetical person.
- Never imply the reader should act on an answer without their diabetes team.

The reader CANNOT see the section while they answer it. This changes everything:
- A question answerable by scanning the text for a matching phrase is a failed
  question. Never quote the section's wording in the prompt or the right option.
- Instead, put the reader in a short concrete situation — a hypothetical person,
  a time of day, something they ate or did — and ask them to PREDICT or EXPLAIN
  what glucose does and why. Transfer, not recall.
- The situation must be NEW. If the section works through an example, change the
  food, the timing, or the circumstance, so remembering the outcome doesn't
  substitute for understanding the mechanism.
- Two or three sentences of setup at most, then the question.
- It must still be answerable from that section alone. No outside knowledge.

Options:
- 3 or 4 options. Exactly one is correct.
- Every incorrect option must be a genuinely tempting misconception a real
  learner holds — never filler, never obviously absurd.
- "misconception": the wrong model that option reveals, as a short lowercase
  phrase (e.g. "thinks the liver only releases glucose after meals").
- "coaching": what you say back to a reader who just picked that option. One or
  two sentences, addressed to them as "you". Correct THAT specific wrong model —
  name the thinking, don't just say they're wrong. They get another attempt right
  after reading it, so coaching must NOT reveal, name, or describe the correct
  option. Point at the reasoning, not the answer.
- "explanation": why the right answer is right, one or two sentences, addressed
  as "you". Shown only once the checkpoint is settled.
- "concept" is a short kebab-case tag for the idea being tested
  (e.g. "liver-glucose-output"). Reuse a natural tag if two lessons test the
  same idea.

Return JSON of exactly this shape and nothing else:
{"questions": [{"section_index": 0, "concept": "kebab-case-tag",
  "prompt": "...", "explanation": "...",
  "options": [{"text": "...", "correct": true, "misconception": null,
               "coaching": null},
              {"text": "...", "correct": false, "misconception": "...",
               "coaching": "..."}]}]}
"""


class GeneratedOption(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    correct: bool = False
    # What this wrong turn tells us about the reader — recorded, never shown.
    misconception: str | None = Field(default=None, max_length=300)
    # What the teacher says back to them — shown, never recorded.
    coaching: str | None = Field(default=None, max_length=600)

    @model_validator(mode="after")
    def _wrong_answers_carry_both_halves(self) -> GeneratedOption:
        if not self.correct and not self.misconception:
            raise ValueError("every incorrect option must name the misconception it reveals")
        if not self.correct and not self.coaching:
            raise ValueError("every incorrect option must carry the coaching for that misconception")
        if self.correct:
            self.misconception = None
            self.coaching = None
        return self


class GeneratedQuestion(BaseModel):
    section_index: int = Field(ge=0)
    concept: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=600)
    explanation: str = Field(min_length=1, max_length=800)
    options: list[GeneratedOption] = Field(min_length=3, max_length=4)

    @field_validator("concept")
    @classmethod
    def _kebab(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "-").replace("_", "-")

    @model_validator(mode="after")
    def _exactly_one_correct(self) -> GeneratedQuestion:
        correct = [o for o in self.options if o.correct]
        if len(correct) != 1:
            raise ValueError(f"expected exactly one correct option, got {len(correct)}")
        return self


class GeneratedSet(BaseModel):
    questions: list[GeneratedQuestion]


def build_user_prompt(title: str, summary: str, sections: list[Section]) -> str:
    """The lesson, laid out so section numbers are unambiguous."""
    parts = [f"LESSON: {title}", f"SUMMARY: {summary}", ""]
    for section in sections:
        parts.append(f"--- SECTION {section.index}: {section.heading or '(introduction)'} ---")
        parts.append(section.body)
        parts.append("")
    parts.append(
        f"Write exactly {len(sections)} questions, one per section, "
        f"with section_index values 0 to {len(sections) - 1}."
    )
    return "\n".join(parts)


def parse_questions(payload: dict, section_count: int) -> list[GeneratedQuestion]:
    """Validate a model response and keep the first question per section.

    Models occasionally return a question for a section that doesn't exist, or
    two for the same one. Both are recoverable — drop the strays rather than
    throw away an otherwise good set — but a response with nothing usable in it
    is a failure worth surfacing.
    """
    try:
        parsed = GeneratedSet.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"the model's questions did not validate: {exc.error_count()} problem(s)")

    seen: dict[int, GeneratedQuestion] = {}
    for question in parsed.questions:
        if question.section_index >= section_count:
            continue
        seen.setdefault(question.section_index, question)

    if not seen:
        raise ValueError("the model returned no questions matching this lesson's sections")
    return [seen[i] for i in sorted(seen)]


def shuffle_options(questions: list[GeneratedQuestion]) -> list[GeneratedQuestion]:
    """Randomise each question's option order in place.

    Models have a strong bias toward writing the correct option first — leaving
    that order as-is would turn every checkpoint into "pick option A". The
    shuffle happens once, here, before a question is ever stored, so it's the
    same order for every reader rather than reshuffled per view.
    """
    for question in questions:
        random.shuffle(question.options)
    return questions


def generate(
    title: str, summary: str, sections: list[Section], *, model: str | None = None
) -> list[GeneratedQuestion]:
    """Generate one checkpoint per section. Raises LLMError or ValueError."""
    payload = complete_json(
        SYSTEM_PROMPT, build_user_prompt(title, summary, sections), model=model
    )
    return shuffle_options(parse_questions(payload, len(sections)))
