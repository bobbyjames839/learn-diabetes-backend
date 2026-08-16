"""Chat sessions — the third way into the material.

Two endpoints, matching the two moments a session can write:

- `POST /api/chat` runs one turn. It reads the profile and the reader's past
  answers, and may write the profile back when the conversation has revealed
  something about how they want to be taught. That write happens at the model's
  chosen moments, not on a schedule and not every turn.
- `POST /api/chat/end` closes the session: the model reads the transcript back,
  writes up to five cards for what the reader struggled with, and those cards
  are enqueued onto the deck, displacing its oldest.

The transcript is never stored. The client carries it and sends it up on each
call; when the tab closes it is gone. A session's residue is the profile
revision and the cards, which is the part worth keeping.

Unlike the coach, this cannot fail soft into silence — somebody asked a
question and is waiting for an answer — so a model failure here is a real error
the reader is told about. Ending a session is the opposite: the card write is a
background courtesy, and a failure there costs nothing worth reporting.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import aisdk, chat, chat_cards, chat_summary, flashcards, mastery
from app.auth import get_current_user
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.llm import LLMError, is_configured
from app.models import ChatCard, ChatSession, LessonProgress, Profile
from app.routers.coach import recorded_history
from app.schemas import (
    ChatCheckOptionOut,
    ChatCheckOut,
    ChatEndIn,
    ChatEndOut,
    ChatOut,
    ChatSessionOut,
    ChatStartIn,
    ChatStreamIn,
    ChatTurn,
    ProfileOut,
    SessionBriefIn,
)

router = APIRouter()
log = logging.getLogger(__name__)

UNAVAILABLE = "The tutor isn't available right now. Try again in a moment."


def _learner(db: Session, profile: Profile) -> chat.LearnerProfile:
    """The reader, as the conversation needs to see them."""
    completed = (
        db.scalar(
            select(func.count())
            .select_from(LessonProgress)
            .where(
                LessonProgress.user_id == profile.id,
                LessonProgress.completed.is_(True),
            )
        )
        or 0
    )
    return chat.LearnerProfile(
        name=profile.display_name or "",
        goal=profile.onboarding_goal,
        experience=profile.onboarding_experience,
        learning_style=profile.onboarding_learning_style,
        content_preference=profile.onboarding_content_preference,
        focus=profile.onboarding_focus,
        lessons_completed=completed,
        area_ratings=profile.area_ratings,
    )


def _transcript(messages: list[ChatTurn]) -> list[chat.Turn]:
    return [chat.Turn(role=m.role, content=m.content) for m in messages]


def _brief(brief: SessionBriefIn) -> chat.SessionBrief:
    """The subject the reader picked, validated by Pydantic on the way in.

    A closed set, so what reaches the prompt is always something the picker
    could have offered — the same guarantee the onboarding answers give, for
    the same reason.
    """
    return chat.SessionBrief(topic=brief.topic)


@router.post("/chat/start", response_model=ChatOut)
def start_chat(
    payload: ChatStartIn = ChatStartIn(),
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatOut:
    """Open a session — the tutor's first message, before the reader has said
    anything.

    The body is only the brief: the subject the reader picked and how far they
    want to go. Everything else the opening needs — who they are, what they have
    been answering — the server already has. Writes nothing; there is nothing to
    have learned yet.
    """
    if not is_configured():
        raise HTTPException(status_code=503, detail=UNAVAILABLE)

    try:
        opening = chat.open_session(
            _learner(db, profile),
            recorded_history(db, profile.id),
            brief=_brief(payload.brief),
            model=get_settings().chat_model,
        )
    except LLMError as exc:
        log.info("chat opening failed for %s: %s", profile.id, exc)
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from None

    if opening is None:
        raise HTTPException(status_code=503, detail=UNAVAILABLE)

    return ChatOut(reply=opening)


def _check_out(reply: chat.ChatReply) -> ChatCheckOut | None:
    if not reply.check:
        return None
    return ChatCheckOut(
        question=reply.check.question,
        options=[
            ChatCheckOptionOut(text=o.text, correct=o.correct, response=o.response)
            for o in reply.check.options
        ],
    )


def _write_profile_update(user_id, update: chat.ProfileUpdate) -> Profile | None:
    """Apply a revision the tutor made mid-conversation, on its own session.

    Its own, because this runs inside the streaming generator, by which point
    the request's injected session has already been closed — FastAPI tears
    dependencies down before the response body is consumed. The write is rare
    and small, so a short session of its own costs nothing and keeps the
    lifetime obvious.

    Validated to the same closed option sets the onboarding quiz uses, so what
    lands here is always a value the reader could have picked themselves.
    """
    fields = update.fields()
    with SessionLocal() as db:
        profile = db.get(Profile, user_id)
        if profile is None:
            return None
        for field, value in fields.items():
            setattr(profile, f"onboarding_{field}", value)
        db.commit()
        db.refresh(profile)
        log.info("chat revised profile for %s: %s", user_id, fields)
        return profile


@router.post("/chat")
def chat_turn(
    payload: ChatStreamIn,
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """One turn of a teaching conversation, streamed.

    The tutor is the only screen where the reader waits on the model with
    nothing to read, so this is the one endpoint that streams. It speaks the AI
    SDK's data stream protocol (`app/aisdk.py`) and the client is `useChat`,
    which is what the SDK is genuinely good at: the in-flight message, the
    abort, the error state, all handled.

    The prose goes out as it is written. Everything else the turn carries — the
    check, the wrap-up proposal, the profile revision — is a `data-*` part sent
    once the object has closed and `chat.parse_reply` has passed it, because
    none of them is meaningful half-arrived.

    Reads happen here, before the stream opens, while the request's database
    session is still alive. The one write on this path opens its own.
    """
    if not is_configured():
        raise HTTPException(status_code=503, detail=UNAVAILABLE)

    # Read eagerly: by the time the generator below runs, `db` is closed.
    # Only what's on record — a chat session has no in-flight lesson attempts to
    # be told about, so unlike the coach there is nothing client-side to merge in.
    learner = _learner(db, profile)
    history = recorded_history(db, profile.id)
    transcript = [chat.Turn(role=m.role, content=m.text()) for m in payload.messages if m.text()]
    brief = _brief(payload.brief)
    user_id = profile.id
    model = get_settings().chat_model

    if not transcript:
        raise HTTPException(status_code=422, detail="Nothing to reply to.")

    def stream():
        message_id = f"msg_{uuid4().hex}"
        yield aisdk.sse({"type": "start", "messageId": message_id})
        yield aisdk.sse({"type": "text-start", "id": message_id})

        reply: chat.ChatReply | None = None
        try:
            for kind, value in chat.respond_stream(
                learner, history, transcript, brief=brief, model=model
            ):
                if kind == "text":
                    yield aisdk.sse(
                        {"type": "text-delta", "id": message_id, "delta": value}
                    )
                else:
                    reply = value
        except LLMError as exc:
            # Mid-stream, so this cannot be a 503: the status line went out with
            # the first byte. The protocol carries the failure instead, and
            # `useChat` surfaces it the same way it would a rejected request.
            log.info("chat turn failed for %s: %s", user_id, exc)
            yield aisdk.sse({"type": "error", "errorText": UNAVAILABLE})
            yield aisdk.sse({"type": "text-end", "id": message_id})
            yield aisdk.sse({"type": "finish"})
            yield aisdk.done()
            return

        yield aisdk.sse({"type": "text-end", "id": message_id})

        if reply is None:
            yield aisdk.sse({"type": "error", "errorText": UNAVAILABLE})
        else:
            check = _check_out(reply)
            if check:
                yield aisdk.data_part("check", check.model_dump())
            if reply.wrap_up:
                yield aisdk.data_part("wrap-up", {"wrap_up": True})
            if reply.profile_update:
                updated = _write_profile_update(user_id, reply.profile_update)
                if updated is not None:
                    yield aisdk.data_part(
                        "profile",
                        ProfileOut.model_validate(updated, from_attributes=True).model_dump(
                            mode="json"
                        ),
                    )

        yield aisdk.sse({"type": "finish"})
        yield aisdk.done()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            **aisdk.STREAM_HEADER,
            # Streaming dies behind a proxy that buffers it.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _session_summary(payload: ChatEndIn, model: str) -> chat_summary.GeneratedSummary:
    """The recap this session gets, generated when possible.

    Falls back to a plain, deterministic one on no config, no transcript, or a
    model failure — a summary always exists, the same way a session always
    gets *some* cards outcome (even if it's zero) rather than an error.
    """
    if is_configured() and payload.messages:
        try:
            generated = chat_summary.generate(
                _transcript(payload.messages), topic=payload.brief.topic, model=model
            )
            if generated:
                return generated
        except LLMError as exc:
            log.info("no generated recap for chat session: %s", exc)
    return chat_summary.fallback(payload.brief.topic, payload.checks_correct, payload.checks_total)


def _rate_area(profile: Profile, recap: chat_summary.GeneratedSummary, payload: ChatEndIn) -> None:
    """Move an area rating by how this session's checks went.

    The checks are the only thing in a conversation that can be scored — they
    were answered by choosing, and the client tallied them on the way through.
    A session with none moves nothing, which is right: a chat where the reader
    was never asked anything is not evidence about what they know.

    The area comes from the recap rather than the brief, because the brief is
    `tutor_picks` by default and the recap is written by something that has
    actually read the conversation. It falls back to the brief's subject when
    the recap couldn't name one.
    """
    area = recap.area or mastery.TOPIC_AREAS.get(payload.brief.topic)
    if area is None or payload.checks_total == 0:
        return
    profile.area_ratings = mastery.apply_evidence(
        profile.area_ratings, {area: (payload.checks_correct, payload.checks_total)}
    )


@router.post("/chat/end", response_model=ChatEndOut)
def end_chat(
    payload: ChatEndIn,
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatEndOut:
    """Close a session: write its cards and its recap, then enqueue the cards
    onto the deck.

    All three writes land together. Zero cards is a normal outcome — most
    short conversations produce none — and so is a model failure generating
    either the cards or the recap, both of which are logged and degrade
    gracefully rather than costing the reader anything: the conversation was
    the teaching, and these are what's kept on top of it.
    """
    model = get_settings().chat_model
    cards_added = 0

    if is_configured() and payload.messages:
        try:
            generated = chat_cards.generate(_transcript(payload.messages), model=model)
        except LLMError as exc:
            log.info("no cards from chat session for %s: %s", profile.id, exc)
            generated = []

        if generated:
            rows = [
                ChatCard(
                    user_id=profile.id,
                    front=card.front,
                    back=card.back,
                    topic=card.topic,
                    model=model,
                )
                for card in generated
            ]
            db.add_all(rows)
            # Kept in `chat_cards` as well as the deck: the deck is a rolling
            # 20 and these will eventually age out of it, and unlike a weak
            # spot or a takeaway a card authored in a conversation cannot be
            # written again.
            #
            # Added to the deck rather than triggering a rebuild — they take
            # the oldest slots and everything else the reader has earned
            # stays where it is.
            flashcards.enqueue(db, profile.id, flashcards.chat_candidates(rows))
            cards_added = len(generated)

    recap = _session_summary(payload, model)
    _rate_area(profile, recap, payload)
    session_row = ChatSession(
        user_id=profile.id,
        topic=payload.brief.topic,
        headline=recap.headline,
        summary=recap.summary,
        checks_correct=payload.checks_correct,
        checks_total=payload.checks_total,
        cards_added=cards_added,
    )
    db.add(session_row)
    db.commit()
    db.refresh(session_row)

    return ChatEndOut(
        cards_added=cards_added,
        session=ChatSessionOut.model_validate(session_row, from_attributes=True),
    )


@router.get("/chat/sessions", response_model=list[ChatSessionOut])
def list_sessions(
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ChatSession]:
    """Past sessions, most recently ended first. A plain read — nothing here
    is the conversation itself, only the recap each one left behind."""
    return list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.user_id == profile.id)
            .order_by(ChatSession.created_at.desc())
            .limit(50)
        )
    )
