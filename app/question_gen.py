"""Turning a lesson into checkpoint questions.

A checkpoint is one multiple-choice question placed after a section of the
lesson. Its purpose is as much diagnostic as pedagogical: every wrong option
carries the misconception a reader who picks it is holding, so an answer tells
us *which* wrong model of glucose someone has, not merely that they missed one.

The prompt and the validation live here, apart from the HTTP call, so both can
be tested without a network.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.llm import complete_json
from app.sections import Section

# The same boundary the lesson authoring prompt carries. A question that asks
# "how much insulin would you take here?" would be a dosing suggestion wearing a
# quiz's clothes, so the rule is restated for the generator.
SYSTEM_PROMPT = """\
You write diagnostic comprehension checks for a type 1 diabetes education app.

You will be given one lesson, already split into numbered sections. Write exactly
one multiple-choice question per section, testing the idea that section teaches.

Hard safety rules — these override everything else:
- Never suggest, imply, or ask the reader to choose an insulin dose, correction
  factor, carb ratio, basal rate, or any specific treatment action.
- Questions test understanding of MECHANISM — why glucose behaves as it does.
- Any numbers are illustrative and belong to a hypothetical person.
- Never imply the reader should act on an answer without their diabetes team.

Question rules:
- Answerable from that section alone. Do not require outside knowledge.
- Test comprehension, not recall of a specific number or phrase.
- 3 or 4 options. Exactly one is correct.
- Every incorrect option must be a genuinely tempting misconception a real
  learner holds — never filler, never obviously absurd.
- For each incorrect option, state the misconception it reveals in a short
  lowercase phrase (e.g. "thinks the liver only releases glucose after meals").
- The explanation says why the right answer is right, in one or two sentences,
  addressed to the reader as "you".
- "concept" is a short kebab-case tag for the idea being tested
  (e.g. "liver-glucose-output"). Reuse a natural tag if two lessons test the
  same idea.

Return JSON of exactly this shape and nothing else:
{"questions": [{"section_index": 0, "concept": "kebab-case-tag",
  "prompt": "...", "explanation": "...",
  "options": [{"text": "...", "correct": true, "misconception": null},
              {"text": "...", "correct": false, "misconception": "..."}]}]}
"""


class GeneratedOption(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    correct: bool = False
    misconception: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _misconception_only_on_wrong_answers(self) -> GeneratedOption:
        if not self.correct and not self.misconception:
            raise ValueError("every incorrect option must name the misconception it reveals")
        if self.correct:
            self.misconception = None
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


def generate(
    title: str, summary: str, sections: list[Section], *, model: str | None = None
) -> list[GeneratedQuestion]:
    """Generate one checkpoint per section. Raises LLMError or ValueError."""
    payload = complete_json(
        SYSTEM_PROMPT, build_user_prompt(title, summary, sections), model=model
    )
    return parse_questions(payload, len(sections))
