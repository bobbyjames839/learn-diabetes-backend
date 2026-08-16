-- Learn Diabetes — Postgres schema (Supabase).
-- Generated from app/models.py. Run this in the Supabase SQL editor.
-- Safe to re-run.
--
-- All ids are native `uuid`, because profiles.id is a foreign key onto
-- auth.users.id, which Supabase defines as uuid. A varchar column cannot
-- reference it.

-- 1. Drop the old AI-era tables -------------------------------------------
-- These held throwaway test data from the first build. Skip this block if you
-- want to keep them.
DROP TABLE IF EXISTS messages          CASCADE;
DROP TABLE IF EXISTS sessions          CASCADE;
DROP TABLE IF EXISTS attempts          CASCADE;
DROP TABLE IF EXISTS learner_concepts  CASCADE;
DROP TABLE IF EXISTS concepts          CASCADE;
DROP TABLE IF EXISTS learners          CASCADE;

-- 2. Profiles ---------------------------------------------------------------
-- One row per Supabase auth user. id mirrors auth.users.id, so deleting the
-- auth user removes the profile and everything hanging off it.
CREATE TABLE IF NOT EXISTS profiles (
    id                            UUID         PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
    email                         VARCHAR(320),
    display_name                  VARCHAR(120),
    diagnosed_year                INTEGER,
    created_at                    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- One-time onboarding quiz, asked the first time a reader reaches Home.
    -- All five are set together (see POST /api/me/onboarding); NULL until then.
    onboarding_goal               VARCHAR(40),
    onboarding_experience         VARCHAR(40),
    onboarding_learning_style     VARCHAR(40),
    onboarding_content_preference VARCHAR(40),
    onboarding_focus              VARCHAR(40),
    onboarding_completed_at       TIMESTAMPTZ,
    -- Where they stand per area of diabetes management, 1-100 (app/mastery.py).
    -- Not self-reported: lesson and chat sessions move these, and every prompt
    -- that gets the profile gets them. '{}' means nothing learned yet, which
    -- reads as the default 50 everywhere.
    area_ratings                  JSONB        NOT NULL DEFAULT '{}'::jsonb
);

-- `CREATE TABLE IF NOT EXISTS` above is a no-op against an already-created
-- table, so the onboarding columns are added explicitly for anyone re-running
-- this against a database from before they existed.
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS onboarding_goal               VARCHAR(40);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS onboarding_experience         VARCHAR(40);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS onboarding_learning_style     VARCHAR(40);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS onboarding_content_preference VARCHAR(40);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS onboarding_focus              VARCHAR(40);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS onboarding_completed_at       TIMESTAMPTZ;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS area_ratings                  JSONB NOT NULL DEFAULT '{}'::jsonb;

-- 3. Authored lesson content ------------------------------------------------
CREATE TABLE IF NOT EXISTS lessons (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    slug              VARCHAR(80)  NOT NULL,
    title             VARCHAR(160) NOT NULL,
    summary           TEXT         NOT NULL DEFAULT '',
    category          VARCHAR(60)  NOT NULL DEFAULT 'general',
    difficulty        INTEGER      NOT NULL DEFAULT 1,
    estimated_minutes INTEGER      NOT NULL DEFAULT 5,
    body              TEXT         NOT NULL DEFAULT '',   -- markdown
    key_takeaways     JSON         NOT NULL DEFAULT '[]'::json,
    order_index       INTEGER      NOT NULL DEFAULT 0,
    published         BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_lessons_slug        ON lessons (slug);
CREATE INDEX        IF NOT EXISTS ix_lessons_category    ON lessons (category);
CREATE INDEX        IF NOT EXISTS ix_lessons_order_index ON lessons (order_index);

-- 4. Per-user lesson progress ----------------------------------------------
CREATE TABLE IF NOT EXISTS lesson_progress (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    lesson_id      UUID        NOT NULL REFERENCES lessons  (id) ON DELETE CASCADE,
    completed      BOOLEAN     NOT NULL DEFAULT FALSE,
    last_viewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at   TIMESTAMPTZ,
    CONSTRAINT uq_user_lesson UNIQUE (user_id, lesson_id)
);

CREATE INDEX IF NOT EXISTS ix_lesson_progress_user_id   ON lesson_progress (user_id);
CREATE INDEX IF NOT EXISTS ix_lesson_progress_lesson_id ON lesson_progress (lesson_id);

-- 5. Checkpoint questions ---------------------------------------------------
-- One question per section of a lesson, generated once and kept. Also in
-- sql/002_questions.sql as a standalone migration for an existing database.
CREATE TABLE IF NOT EXISTS lesson_questions (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id     UUID        NOT NULL REFERENCES lessons (id) ON DELETE CASCADE,
    section_index INTEGER     NOT NULL,
    concept       VARCHAR(80) NOT NULL DEFAULT '',
    prompt        TEXT        NOT NULL,
    options       JSON        NOT NULL DEFAULT '[]'::json,
    explanation   TEXT        NOT NULL DEFAULT '',
    model         VARCHAR(120) NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_lesson_section_question UNIQUE (lesson_id, section_index)
);

CREATE INDEX IF NOT EXISTS ix_lesson_questions_lesson_id ON lesson_questions (lesson_id);
CREATE INDEX IF NOT EXISTS ix_lesson_questions_concept   ON lesson_questions (concept);

-- 6. Reader answers ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS question_responses (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL REFERENCES profiles        (id) ON DELETE CASCADE,
    question_id   UUID        NOT NULL REFERENCES lesson_questions (id) ON DELETE CASCADE,
    chosen_index  INTEGER     NOT NULL,
    correct       BOOLEAN     NOT NULL DEFAULT FALSE,
    misconception TEXT,
    attempt       INTEGER     NOT NULL DEFAULT 1,
    answered_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_question_responses_user_id     ON question_responses (user_id);
CREATE INDEX IF NOT EXISTS ix_question_responses_question_id ON question_responses (question_id);

-- 7. Cards written at the end of a chat session ------------------------------
-- Kept apart from `flashcards` below, which is a rolling 20 that these will
-- eventually age out of. Weak spots and takeaways can always be written again
-- from question_responses and lessons; a card authored once in a conversation
-- cannot, so it lives here permanently. The conversation itself is never stored.
CREATE TABLE IF NOT EXISTS chat_cards (
    id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID         NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    front      TEXT         NOT NULL,
    back       TEXT         NOT NULL,
    topic      VARCHAR(80)  NOT NULL DEFAULT '',
    model      VARCHAR(120) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chat_cards_user_id    ON chat_cards (user_id);
CREATE INDEX IF NOT EXISTS ix_chat_cards_created_at ON chat_cards (created_at);

-- 7b. What a chat session leaves behind, in place of the conversation --------
-- The transcript itself is never stored. This is a generated recap instead:
-- a headline, a couple of sentences on how it went, and the check tally the
-- client computed while the session was live (a check is answered client-side
-- and never recorded anywhere else, so this is the only place that count
-- survives). Written once, at session end, alongside that session's cards.
CREATE TABLE IF NOT EXISTS chat_sessions (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID         NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    topic          VARCHAR(40)  NOT NULL DEFAULT 'tutor_picks',
    headline       VARCHAR(160) NOT NULL DEFAULT '',
    summary        TEXT         NOT NULL DEFAULT '',
    checks_correct INTEGER      NOT NULL DEFAULT 0,
    checks_total   INTEGER      NOT NULL DEFAULT 0,
    cards_added    INTEGER      NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chat_sessions_user_id    ON chat_sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_chat_sessions_created_at ON chat_sessions (created_at);

-- 8. Flashcard deck ----------------------------------------------------------
-- A per-user queue of 20 slots, seeded with a spread of the curriculum's key
-- takeaways the moment a profile is created and only ever added to after that.
-- Finishing a lesson enqueues the checkpoints missed in it plus that lesson's
-- takeaways; ending a chat session enqueues its cards. Each new card overwrites
-- the oldest slot in place, so `position` is a stable slot number and
-- `updated_at` is when that slot last changed hands — which is what the deck
-- orders by and what "oldest" means when something has to give.
--
-- Nothing here is ever recomputed: a card earned in an old session survives
-- until 20 newer ones have pushed it out.
--
-- The lesson columns are nullable/defaulted because a `chat_gap` card belongs
-- to no lesson. The other two kinds are quoted from one and always set them.
CREATE TABLE IF NOT EXISTS flashcards (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID         NOT NULL REFERENCES profiles         (id) ON DELETE CASCADE,
    position     INTEGER      NOT NULL,
    kind         VARCHAR(20)  NOT NULL,
    question_id  UUID         REFERENCES lesson_questions (id) ON DELETE CASCADE,
    lesson_id    UUID         REFERENCES lessons (id) ON DELETE CASCADE,
    lesson_slug  VARCHAR(80)  NOT NULL DEFAULT '',
    lesson_title VARCHAR(160) NOT NULL DEFAULT '',
    category     VARCHAR(60)  NOT NULL DEFAULT '',
    front        TEXT         NOT NULL,
    back         TEXT         NOT NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_flashcard_position UNIQUE (user_id, position)
);

CREATE INDEX IF NOT EXISTS ix_flashcards_user_id ON flashcards (user_id);

-- Explicit for anyone re-running this against a database from before chat
-- sessions existed, where these columns were NOT NULL. See sql/003_chat.sql.
ALTER TABLE flashcards ALTER COLUMN lesson_id    DROP NOT NULL;
ALTER TABLE flashcards ALTER COLUMN lesson_slug  SET DEFAULT '';
ALTER TABLE flashcards ALTER COLUMN lesson_title SET DEFAULT '';
ALTER TABLE flashcards ALTER COLUMN category     SET DEFAULT '';

-- 9. Row level security -----------------------------------------------------
-- The API connects as `postgres`, which bypasses RLS, so these policies don't
-- affect the backend. They exist so that anything reaching the database with an
-- anon/authenticated key can only ever see its own rows.
ALTER TABLE profiles          ENABLE ROW LEVEL SECURITY;
ALTER TABLE lesson_progress   ENABLE ROW LEVEL SECURITY;
ALTER TABLE lessons           ENABLE ROW LEVEL SECURITY;
ALTER TABLE lesson_questions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE question_responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE flashcards         ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_cards         ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_sessions      ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "own profile" ON profiles;
CREATE POLICY "own profile" ON profiles
    FOR ALL USING (auth.uid() = id) WITH CHECK (auth.uid() = id);

DROP POLICY IF EXISTS "own progress" ON lesson_progress;
CREATE POLICY "own progress" ON lesson_progress
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- Lessons are shared content: any signed-in user may read, nobody may write.
DROP POLICY IF EXISTS "read published lessons" ON lessons;
CREATE POLICY "read published lessons" ON lessons
    FOR SELECT TO authenticated USING (published);

-- Deliberately no SELECT policy on lesson_questions: its options column carries
-- the correct answer, so it is backend-only. The API strips it before sending.

DROP POLICY IF EXISTS "own responses" ON question_responses;
CREATE POLICY "own responses" ON question_responses
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "own flashcards" ON flashcards;
CREATE POLICY "own flashcards" ON flashcards
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "own chat cards" ON chat_cards;
CREATE POLICY "own chat cards" ON chat_cards
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "own chat sessions" ON chat_sessions;
CREATE POLICY "own chat sessions" ON chat_sessions
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
