-- Learn Diabetes — checkpoint questions (additive migration).
--
-- Run this in the Supabase SQL editor. It only adds; it drops nothing, so it is
-- safe to apply to a database that already has lessons and progress in it.
-- These same tables are also in schema.sql, which stays the canonical whole.

-- 1. The questions themselves ----------------------------------------------
-- One checkpoint per section of a lesson. Generated once and kept, so every
-- reader sees the same question and the responses stay comparable.
CREATE TABLE IF NOT EXISTS lesson_questions (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id     UUID        NOT NULL REFERENCES lessons (id) ON DELETE CASCADE,
    section_index INTEGER     NOT NULL,
    concept       VARCHAR(80) NOT NULL DEFAULT '',
    prompt        TEXT        NOT NULL,
    -- [{"text": ..., "correct": ..., "misconception": ...}]
    options       JSON        NOT NULL DEFAULT '[]'::json,
    explanation   TEXT        NOT NULL DEFAULT '',
    model         VARCHAR(120) NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Doubles as the concurrency guard: two readers opening a lesson whose
    -- questions don't exist yet cannot both insert a set.
    CONSTRAINT uq_lesson_section_question UNIQUE (lesson_id, section_index)
);

CREATE INDEX IF NOT EXISTS ix_lesson_questions_lesson_id ON lesson_questions (lesson_id);
CREATE INDEX IF NOT EXISTS ix_lesson_questions_concept   ON lesson_questions (concept);

-- 2. What each reader answered ---------------------------------------------
-- Every attempt is a row. A reader who answers wrong and then right has told us
-- something that overwriting a single row would erase.
CREATE TABLE IF NOT EXISTS question_responses (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL REFERENCES profiles        (id) ON DELETE CASCADE,
    question_id   UUID        NOT NULL REFERENCES lesson_questions (id) ON DELETE CASCADE,
    chosen_index  INTEGER     NOT NULL,
    correct       BOOLEAN     NOT NULL DEFAULT FALSE,
    -- Copied from the chosen option at answer time, so the observation survives
    -- the question being regenerated with different wording.
    misconception TEXT,
    attempt       INTEGER     NOT NULL DEFAULT 1,
    answered_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_question_responses_user_id     ON question_responses (user_id);
CREATE INDEX IF NOT EXISTS ix_question_responses_question_id ON question_responses (question_id);

-- 3. Row level security -----------------------------------------------------
-- The API connects as `postgres` and bypasses these. They matter for anything
-- arriving with an anon/authenticated key.
ALTER TABLE lesson_questions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE question_responses ENABLE ROW LEVEL SECURITY;

-- Deliberately no SELECT policy on lesson_questions: the options column carries
-- the correct answer, so it is backend-only. The API strips it before sending.

DROP POLICY IF EXISTS "own responses" ON question_responses;
CREATE POLICY "own responses" ON question_responses
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
