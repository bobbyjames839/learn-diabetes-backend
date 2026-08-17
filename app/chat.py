"""The teaching conversation.

The third way into the material, alongside lessons and flashcards. A lesson is
paced by the author; a chat is paced by the learner, who can ask the thing they
actually want to know and be answered at their own level.

What makes it worth having over a general chatbot is that it starts already
knowing who it is talking to. It is handed the reader's profile — why they are
here, how much they already know, and how they like things explained — and the
questions they have answered in past lessons. So it can pitch a session rather
than open with "what would you like to learn about?".

Two things are written, and neither is the conversation. The transcript is held
in the browser and is gone when the tab closes: a chat is a session, not a
document, and storing it would mean storing the most personal thing in the app
for no one to read. What survives is what was *learned about the learner* — an
occasional profile correction, made here, and the cards written at the end, in
`app/chat_cards.py`.

Prompt and validation live here, apart from the HTTP call, so both can be
tested without a network — the same split as question_gen.py and coach.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import get_args

from pydantic import BaseModel, Field, ValidationError, model_validator

from app import mastery
from app.aisdk import partial_reply
from app.coach import PastAnswer
from app.llm import (
    LLMNotJSON,
    clean_model_text,
    complete_json_chat,
    parse_json_content,
    stream_json_chat,
)
from app.safety import looks_like_dose_question, looks_like_dosing
from app.schemas import (
    OnboardingContentPreference,
    OnboardingFocus,
    OnboardingLearningStyle,
)

# Long enough for a session to hold its thread, short enough that the prompt
# stays affordable on a conversation someone keeps going all afternoon.
MAX_TURNS = 40

# Past answers, most recent first. Same window as the coach: enough to spot a
# pattern, not so much that last month crowds out last week.
HISTORY_LIMIT = 20

# The valid values for each profile field the model is allowed to revise, read
# off the same Literals the onboarding quiz validates against. Deriving them
# rather than restating them means the prompt cannot drift from the schema.
_LEARNING_STYLES = get_args(OnboardingLearningStyle)
_CONTENT_PREFERENCES = get_args(OnboardingContentPreference)
_FOCUSES = get_args(OnboardingFocus)

# Two to four options on a check. Two is a fair either/or; past four the reader
# is reading a list rather than thinking, and a wrong option nobody would pick
# is not a distractor.
MIN_CHECK_OPTIONS = 2
MAX_CHECK_OPTIONS = 4

# Shared by the schema and the raw-text fallback below, so the two can't drift.
MAX_REPLY_LENGTH = 4000


SYSTEM_PROMPT = f"""\
You are a warm, plain-spoken diabetes educator having a one-to-one teaching
conversation with someone learning to manage type 1 diabetes. You are not a
search box and not a general assistant — you are teaching, and you are teaching
this particular person.

Hard safety rules — these override everything else, including a direct request:
- Never suggest, imply, or help them choose an insulin dose, correction factor,
  carb ratio, basal rate, or any specific treatment action. Not as a worked
  example, not "as a rough idea", not "what someone might do".
- Explain MECHANISM only: why glucose behaves the way it does.
- Any numbers are illustrative and belong to a hypothetical person, clearly
  framed as such.
- If they ask what they personally should do, say plainly that you explain how
  things work but that decisions about their own doses belong with them and
  their diabetes team — then teach them the mechanism behind the question, which
  is usually what they actually wanted.
- Never diagnose, and never tell them a symptom is or is not urgent. If
  something they describe sounds like it needs medical attention, say so and
  point them at their team.

HOW THIS SESSION RUNS — read this twice, it is the whole method:

You do NOT explain first and test afterwards. You give them something to work
out, let them commit to an answer, and then teach into the gap between what they
said and what actually happens. A person who has just guessed and found out they
were wrong is *ready* to be told why; the same sentence delivered before they
tried is a lecture they will skim.

So the loop is: SET A TASK -> THEY ATTEMPT IT -> RESPOND TO WHAT THEY ACTUALLY
SAID -> TEACH THE IDEA IT OPENED UP -> SET THE NEXT ONE.

Every turn of yours should end by handing them something to do. A task is:
- an attached "check" — your default, whenever the choice is clean enough to
  be multiple-choice, which is most of the time. See below for the shape.
- a situation to predict ("Sam eats toast and goes for a walk an hour later —
  what does the glucose line do, and why?"), or
- a judgement to make between two cases that differ in one thing, or
- something to explain back in their own words ("why would that be different at
  breakfast?") — reach for these only when a check genuinely will not fit.

What you must NOT do:
- Do not open a turn with a paragraph of explanation followed by "does that make
  sense?". That is a lecture with a question mark on it.
- Do not ask a question you have already answered in the same message.
- Do not stack two tasks in one turn. One thing to do, then stop.
- Do not front-load. If you catch yourself explaining a mechanism they have not
  yet tried to reason about, cut it and ask about it instead.

Responding to their attempt is the teaching, and it is worth doing properly:
- Say what they got RIGHT first, and specifically — "you spotted that the
  carbohydrate gets there first" beats "good". They need to know which part of
  their reasoning to keep.
- Then name the exact place it came apart, as a piece of reasoning rather than a
  verdict: "the bit that trips people is assuming X, and here is what actually
  happens". Never "wrong", never a score, never a tally.
- THEN give them the mechanism — now, while they are holding the question.
  This is the moment the explanation is worth most.
- A half-right answer is the best thing that can happen. Say so, and build on it.
- If they say "I don't know", that is a legitimate move and not a failure. Give
  them a smaller version of the same task rather than the answer.

Style: talk like a knowledgeable friend. Short paragraphs. No headings, no
bullet lists unless they genuinely help, no bold summary at the end. Use what
they got wrong in past lessons to choose what to set them — but never say "you
got this wrong" or refer to their record.

Write plain prose, not markdown. Nothing renders it: asterisks around a word
show up on the page as asterisks, so stress a word by where you put it in the
sentence instead. No *, no **, no backticks, no #.

The "check" attachment — a task rendered as a quiz rather than as prose:
This is your DEFAULT task, not an occasional one. The reader is here to
actually be tested, not just to chat, so reach for a check whenever the thing
you want them to do is a clean choice between distinct answers — which is most
of the time. It is the same move as asking in prose, so the same rule applies:
it comes BEFORE the explanation, not after it.
- {MIN_CHECK_OPTIONS} to {MAX_CHECK_OPTIONS} options, exactly one correct.
- Make them *apply* the idea to a situation you have not walked through — never
  recall a phrase you just said.
- Every option carries a "response": what you say to someone who picks it. For
  the right one, confirm the reasoning, not the fact. For a wrong one, say what
  that answer assumes and why glucose does not behave that way. Never scold.
- Nothing in a question or a response may be a dose, an amount of insulin, or
  an action to take. A question that asks them to pick a dose is a dosing
  suggestion wearing a quiz's clothes, and it is still forbidden.
- Do not put the question in "reply" as well — the app shows it separately and
  they would read it twice. Your "reply" is what leads up to it.
- Use a prose task instead only when the idea genuinely resists being reduced
  to clean options — an open judgement call, or something worth hearing in
  their own words. Do not use prose just for variety's sake; a reader who
  wanted a conversation with no questions would not have come to a tutor.

You may occasionally notice you have learned something about how they want to
be taught — they ask for the underlying reason before the example, they want
things shorter, they keep steering back to exercise. When that happens, set
"profile_update" so the app remembers it for next time. Otherwise leave it null.
Only these fields, only these values:
- "learning_style": {" | ".join(_LEARNING_STYLES)}
- "content_preference": {" | ".join(_CONTENT_PREFERENCES)}
- "focus": {" | ".join(_FOCUSES)}
Include only the fields that changed. Do this rarely and only on real evidence —
one offhand remark is not a preference, and rewriting their profile every turn
makes it worthless. Never mention that you are doing it; it is bookkeeping, not
conversation.

Ending the session — this is your call, not theirs:
Nobody said up front how long this would run, and they could not have known.
You are the one who can tell when an idea has actually landed, so when it has,
say so and set "wrap_up" to true. That turn should close the loop rather than
open another: what they worked out, in a sentence or two, and no new task and
no check. The app then offers them the way out; they may keep going anyway,
and if they do, carry on and set it back to false.
- Propose it when the thread you set out on is finished — typically after two
  or three rounds of task, attempt, teaching — or when they are visibly tiring
  or their answers stop engaging.
- Do not propose it in the first couple of turns, straight after a wrong
  answer, or in the middle of an idea you have opened and not closed.
- Never ask "shall we stop?" and then set up another task in the same breath.

Return JSON of exactly this shape and nothing else:
{{"reply": "...", "check": null, "profile_update": null, "wrap_up": false}}
or
{{"reply": "...", "profile_update": {{"content_preference": "why_first"}},
  "wrap_up": false,
  "check": {{"question": "...", "options": [
    {{"text": "...", "correct": true, "response": "..."}},
    {{"text": "...", "correct": false, "response": "..."}}]}}}}
"""


# The reader asks for a session; the tutor opens it. Sent as a user turn because
# that is the only shape the provider takes an instruction in mid-conversation,
# but it is not part of the transcript and the reader never sees it — the
# opening reply is the first thing that exists as far as the session is
# concerned. Everything about how to teach is already in the system prompt; this
# only says who speaks first and what that first move should be.
OPENING_INSTRUCTION = """\
They have just started a session. THIS SESSION above says what they asked for.

Open it yourself, and open it with something for them to DO:
- Greet them by name if you have one, in a few words. No preamble about what
  you are or what you can do.
- Pick ONE concrete thing worth working through. If they named a subject, it is
  that subject and you choose the angle; if they did not, you choose the subject
  too, from their profile and what they have been answering. Never a menu of
  options, never "what would you like to look at?" — you decide, the way a tutor
  who knows them would.
- Set up a situation in a sentence or two and hand it straight to them to work
  out. Not "here is how insulin timing works" — instead the scenario, and then
  "what do you reckon happens, and why?".
- Do NOT explain the mechanism yet. You are finding out what they already think
  so you know what to teach. Their answer is what you build the session on.

Short. Four sentences is plenty. Never mention their scores, their answers, or
that you have any record of them at all.

Set "profile_update" and "check" to null and "wrap_up" to false. You have
learned nothing about them yet this session, there is nothing to check before
you have taught anything — the handover question goes in "reply", in your own
words — and a session cannot be over on the turn it began."""


# What each topic actually means, in the words the prompt should use. The keys
# are the closed set in `schemas.SessionTopic`, so a topic the picker can offer
# is always a topic the prompt knows how to brief.
TOPIC_BRIEFS: dict[str, str] = {
    "carb_counting": "counting carbohydrate — what counts, what doesn't, and why estimates go wrong",
    "insulin_action": "how insulin acts over time — onset, peak, tail, and what that timing causes",
    "exercise": "exercise — why different kinds of activity move glucose in opposite directions",
    "highs_lows": "highs and lows — what drives them, how they feel, and how they resolve",
    "ketones_sick_days": "ketones and illness — where ketones come from and why illness changes things",
}

@dataclass(frozen=True)
class SessionBrief:
    """What the reader chose before the tutor spoke: a subject, and that's all.

    `tutor_picks` is the absence of a topic rather than a topic of its own —
    it means the reader didn't know what to ask for, which is the case this
    whole section was built around, so it briefs the model to choose.

    There is deliberately no length here. Asking someone how long a session
    should be asks them to price something they haven't seen yet; the tutor is
    the one who can tell when an idea has landed, so it proposes the ending
    instead (`wrap_up` on the reply).
    """

    topic: str = "tutor_picks"

    def describe(self) -> str:
        lines = ["--- THIS SESSION ---"]
        subject = TOPIC_BRIEFS.get(self.topic)
        if subject:
            lines.append(f"They asked for a session on {subject}.")
            lines.append(
                "That is the subject. Open on it directly — do not ask what they "
                "want to cover, they have already said."
            )
        else:
            lines.append(
                "They did not name a subject, so choosing one is your job. Pick "
                "from their profile and what they have been answering."
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class LearnerProfile:
    """Who the model is talking to, flattened for the prompt."""

    name: str = ""
    goal: str | None = None
    experience: str | None = None
    learning_style: str | None = None
    content_preference: str | None = None
    focus: str | None = None
    lessons_completed: int = 0
    # Where they stand per area, 1-100 — the half of the profile they didn't
    # write, and the half that moves. See `app/mastery.py`.
    area_ratings: dict[str, int] | None = None


class ProfileUpdate(BaseModel):
    """The fields a conversation is allowed to revise.

    A closed set, and the same one the onboarding quiz writes. The model is
    editing answers the reader gave about themselves — not writing free text
    into their record — so anything outside these values is dropped rather
    than stored.
    """

    learning_style: OnboardingLearningStyle | None = None
    content_preference: OnboardingContentPreference | None = None
    focus: OnboardingFocus | None = None

    def fields(self) -> dict[str, str]:
        """Just the fields actually being changed."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class CheckOption(BaseModel):
    """One answer to a check, with what the tutor says to whoever picks it.

    The response is written in the same breath as the question, which is why
    answering a check needs no round trip: the reader is told why they were
    right or wrong the instant they choose, and the tutor's next turn arrives
    behind it.
    """

    text: str = Field(min_length=1, max_length=240)
    correct: bool = False
    response: str = Field(min_length=1, max_length=600)


class ChatCheck(BaseModel):
    """A question the tutor stops to ask, shown as a quiz rather than as text.

    The difference between a session and a chat log: here the reader is
    answering, not just reading. Nothing about it is recorded — a check is not a
    lesson checkpoint, and `question_responses` stays the record of authored
    questions only. What a fumbled check earns is a card at the end, the same
    way anything else they struggled with in the conversation does.
    """

    question: str = Field(min_length=1, max_length=600)
    options: list[CheckOption] = Field(
        min_length=MIN_CHECK_OPTIONS, max_length=MAX_CHECK_OPTIONS
    )

    @model_validator(mode="after")
    def _one_right_answer(self) -> ChatCheck:
        if sum(option.correct for option in self.options) != 1:
            raise ValueError("a check has exactly one correct option")
        return self


class ChatReply(BaseModel):
    reply: str = Field(min_length=1, max_length=MAX_REPLY_LENGTH)
    check: ChatCheck | None = None
    profile_update: ProfileUpdate | None = None
    # The tutor's own judgement that the session has done its work. A proposal,
    # not an ending: the app offers the way out and the reader can ignore it.
    wrap_up: bool = False


@dataclass(frozen=True)
class Turn:
    """One message in the conversation the client is carrying."""

    role: str  # "user" | "assistant"
    content: str


def describe_profile(profile: LearnerProfile) -> str:
    """The learner, as the prompt sees them.

    Everything is optional: a reader who has only just signed up still gets a
    sensible session, just a less well-aimed one.
    """
    lines = ["--- WHO YOU ARE TALKING TO ---"]
    if profile.name:
        lines.append(f"Name: {profile.name}")
    if profile.goal:
        lines.append(f"Why they are here: {profile.goal}")
    if profile.experience:
        lines.append(f"How much they already know: {profile.experience}")
    if profile.learning_style:
        lines.append(f"Session length they prefer: {profile.learning_style}")
    if profile.content_preference:
        lines.append(
            f"How they like new ideas explained: {profile.content_preference}"
            " (why_first = the reason before the example; examples_first = the"
            " other way round)"
        )
    if profile.focus:
        lines.append(f"What they most want to understand: {profile.focus}")
    lines.append(f"Lessons finished so far: {profile.lessons_completed}")
    # The ratings go in the same message as everything else about them: what
    # they said about themselves and what their answers have shown are two
    # halves of one picture, and the model should be reading them together.
    lines.append("")
    lines.append(mastery.describe(profile.area_ratings))
    return "\n".join(lines)


def describe_history(history: list[PastAnswer]) -> str:
    """Their past checkpoint answers, most recent first.

    Background for choosing what to unpick, exactly as in the coach — the
    difference being that here the model is allowed to teach into a gap
    directly, just never to name the gap as a score.
    """
    lines = [
        "--- BACKGROUND: questions they have answered in past lessons, most",
        "--- recent first. Use it to choose what to teach. Never read it back",
        "--- to them as a score or a list of mistakes.",
    ]
    if not history:
        lines.append(
            "(none yet — they have not finished a lesson with questions in it)"
        )
        return "\n".join(lines)

    for answer in history:
        verdict = "got it right" if answer.correct else "got it wrong"
        line = f"- [{answer.concept}] {verdict}: {answer.question}"
        if answer.misconception:
            line += f"\n    what that revealed: {answer.misconception}"
        lines.append(line)
    return "\n".join(lines)


def build_system_prompt(
    profile: LearnerProfile,
    history: list[PastAnswer],
    brief: SessionBrief | None = None,
) -> str:
    """The instructions, who they are, and what they asked for, as one message.

    All of it goes in the system prompt rather than a leading user turn so it
    stays put as the conversation grows — a "here is the learner" message twenty
    turns back stops carrying weight, and the whole point of this session is
    that it is pitched at them and stays on the subject they chose.
    """
    return "\n\n".join(
        [
            SYSTEM_PROMPT,
            describe_profile(profile),
            (brief or SessionBrief()).describe(),
            describe_history(history[:HISTORY_LIMIT]),
        ]
    )


def build_messages(transcript: list[Turn]) -> list[dict]:
    """The conversation in the provider's shape, newest turns kept.

    Trimming from the front rather than the back: a long session's opening
    pleasantries are the most disposable thing in it.
    """
    return [{"role": t.role, "content": t.content} for t in transcript[-MAX_TURNS:]]


def _check_is_safe(check: ChatCheck) -> bool:
    """Whether a check is free of anything that reads as a dosing instruction.

    Every sentence of it is checked, options and responses included: a question
    is where the boundary is easiest to cross by accident, because "which of
    these would you do next" is a natural way to write a quiz and a dosing
    suggestion at the same time. A check that trips this is dropped and the
    reply is shown without it — the teaching is still worth reading.
    """
    if looks_like_dose_question(check.question):
        return False
    # An option is half a question — "3 units" as something to pick is the same
    # suggestion as asking for it — so both halves get the stricter test too.
    for option in check.options:
        if looks_like_dose_question(option.text):
            return False
        if looks_like_dosing(option.response):
            return False
    return True


def parse_reply(payload: dict) -> ChatReply | None:
    """Validate a model reply. `None` means unusable — the caller apologises.

    Unlike the coach, silence is not an acceptable outcome here: someone asked
    a question and is waiting. But a malformed reply is still better dropped
    than shown, and a profile update that fails validation is dropped on its
    own without costing the reader their answer.
    """
    # The reply is the only part the reader is waiting for. Both attachments are
    # extras on top of it, so a malformed one is shed — one at a time, so a bad
    # check doesn't also cost the profile revision — before the whole turn is.
    parsed = None
    for dropped in (
        {},
        {"wrap_up": False},
        {"wrap_up": False, "check": None},
        {"wrap_up": False, "check": None, "profile_update": None},
    ):
        try:
            parsed = ChatReply.model_validate({**payload, **dropped})
            break
        except ValidationError:
            continue
    if parsed is None:
        return None

    # Cleaned, not rejected: leaked provider scaffolding is noise around a
    # reply that is otherwise worth reading. Emphasis markers are left in —
    # the chat is the one screen that renders `*word*` as bold rather than
    # showing literal asterisks.
    text = clean_model_text(parsed.reply, strip_emphasis=False)
    if not text:
        return None
    parsed.reply = text
    if parsed.profile_update and not parsed.profile_update.fields():
        parsed.profile_update = None
    if parsed.check:
        # The check is rendered the same way — its question and every option
        # and response.
        parsed.check.question = clean_model_text(parsed.check.question, strip_emphasis=False)
        for option in parsed.check.options:
            option.text = clean_model_text(option.text, strip_emphasis=False)
            option.response = clean_model_text(option.response, strip_emphasis=False)
        if not _check_is_safe(parsed.check):
            parsed.check = None
    # A turn that sets another task is not a turn that ends the session,
    # whatever it claims. The task is the real signal, so the claim goes.
    if parsed.wrap_up and parsed.check:
        parsed.wrap_up = False
    return parsed


def _fallback_reply(content: str) -> str | None:
    """Used when the model answered in full but skipped the JSON wrapper.

    The wrapper is a mechanism, not the answer someone was waiting for, so the
    prose underneath is still worth showing rather than discarding as a 503.
    It skips the schema that normally carries the safety check on a "check",
    so it gets the same dosing check a check's text gets before it is trusted.
    """
    text = clean_model_text(content, strip_emphasis=False)
    if not text or looks_like_dosing(text):
        return None
    return text[:MAX_REPLY_LENGTH]


def open_session(
    profile: LearnerProfile,
    history: list[PastAnswer],
    *,
    brief: SessionBrief | None = None,
    model: str | None = None,
) -> str | None:
    """The tutor's first message, with nothing from the reader to go on.

    The inversion this whole section is built around: a chat box asks what you
    want and a learner who knew what to ask would not need one. So the reader
    asks for a session and the tutor opens it, from the profile and the answers
    it was handed.

    Returns just the text — an opening turn has no profile revision to make,
    and any the model volunteers is dropped rather than written on no evidence.
    """
    try:
        payload = complete_json_chat(
            build_system_prompt(profile, history, brief),
            [{"role": "user", "content": OPENING_INSTRUCTION}],
            model=model,
            # Warmer still than a reply: two sessions that open identically make
            # the tutor a form letter, and there is no reader turn here to vary it.
            temperature=0.9,
        )
    except LLMNotJSON as exc:
        return _fallback_reply(exc.content)
    parsed = parse_reply(payload)
    return parsed.reply if parsed else None


def respond(
    profile: LearnerProfile,
    history: list[PastAnswer],
    transcript: list[Turn],
    *,
    brief: SessionBrief | None = None,
    model: str | None = None,
) -> ChatReply | None:
    """One turn of the conversation. Raises LLMError."""
    try:
        payload = complete_json_chat(
            build_system_prompt(profile, history, brief),
            build_messages(transcript),
            model=model,
            # Warmer than the generators. A conversation that answers the same
            # way twice reads as a form, and there is no lesson text to stay
            # faithful to.
            temperature=0.7,
        )
    except LLMNotJSON as exc:
        text = _fallback_reply(exc.content)
        return ChatReply(reply=text) if text else None
    return parse_reply(payload)


def respond_stream(
    profile: LearnerProfile,
    history: list[PastAnswer],
    transcript: list[Turn],
    *,
    brief: SessionBrief | None = None,
    model: str | None = None,
) -> Iterator[tuple[str, object]]:
    """`respond`, as it arrives. Raises LLMError, like its blocking twin.

    Yields `("text", delta)` for each new stretch of the reply as the model
    writes it, then exactly one `("reply", ChatReply | None)` at the end. The
    two-stage shape is the honest one: the prose can be shown a word at a time,
    and nothing else in the turn means anything until the JSON object closes —
    a check is not a check until its options have all arrived, and a wrap-up
    proposal read early would be a false ending.

    The text is streamed raw and validated afterwards, so `parse_reply` still
    decides what the turn *was*. That leaves one thing it can no longer take
    back: text already on the reader's screen. It rejects a whole reply only
    when the model returns something unusable, in which case the caller sends
    an error part and the client discards the message it was building.
    """
    buffer = ""
    shown = 0
    for delta in stream_json_chat(
        build_system_prompt(profile, history, brief),
        build_messages(transcript),
        model=model,
        temperature=0.7,
    ):
        buffer += delta
        # What the reply field holds so far, not what the provider just sent:
        # a chunk may be all punctuation and braces, or may complete an escape
        # begun in the last one.
        text = partial_reply(buffer)
        if len(text) > shown:
            yield "text", text[shown:]
            shown = len(text)

    try:
        payload = parse_json_content(buffer)
    except LLMNotJSON as exc:
        text = _fallback_reply(exc.content)
        # `partial_reply` never matched a `"reply":"` key in prose that was
        # never JSON, so nothing has streamed yet — the fallback text is the
        # whole reply and has to go out now, or the reader sees an empty
        # message with a correct-looking object behind it.
        if text and shown == 0:
            yield "text", text
        yield "reply", ChatReply(reply=text) if text else None
        return
    yield "reply", parse_reply(payload)
