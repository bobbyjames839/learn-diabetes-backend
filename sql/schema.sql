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
    id             UUID         PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
    email          VARCHAR(320),
    display_name   VARCHAR(120),
    diagnosed_year INTEGER,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

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

-- 7. Row level security -----------------------------------------------------
-- The API connects as `postgres`, which bypasses RLS, so these policies don't
-- affect the backend. They exist so that anything reaching the database with an
-- anon/authenticated key can only ever see its own rows.
ALTER TABLE profiles          ENABLE ROW LEVEL SECURITY;
ALTER TABLE lesson_progress   ENABLE ROW LEVEL SECURITY;
ALTER TABLE lessons           ENABLE ROW LEVEL SECURITY;
ALTER TABLE lesson_questions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE question_responses ENABLE ROW LEVEL SECURITY;

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
