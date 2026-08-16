-- Chat sessions — standalone migration for a database created before them.
-- Everything here is also in schema.sql. Safe to re-run.
--
-- A chat session itself is never stored: the transcript lives in the browser
-- for as long as the tab is open and is gone afterwards. A session writes
-- exactly two things, and this is one of them (the other is the profile, which
-- already has its columns).

-- 1. Cards written at the end of a chat session ------------------------------
-- Separate from `flashcards` because that table is a snapshot, deleted and
-- rebuilt wholesale on every lesson completion. Weak spots and takeaways
-- survive that rebuild because they can be rederived; a card authored once in
-- a conversation cannot, so it is kept here and read back by the deck build.
CREATE TABLE IF NOT EXISTS chat_cards (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID        NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    front      TEXT        NOT NULL,
    back       TEXT        NOT NULL,
    topic      VARCHAR(80) NOT NULL DEFAULT '',
    model      VARCHAR(120) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chat_cards_user_id    ON chat_cards (user_id);
CREATE INDEX IF NOT EXISTS ix_chat_cards_created_at ON chat_cards (created_at);

ALTER TABLE chat_cards ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "own chat cards" ON chat_cards;
CREATE POLICY "own chat cards" ON chat_cards
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- 2. Flashcards can now come from somewhere other than a lesson --------------
-- A `chat_gap` card belongs to no lesson, so the lesson columns go nullable /
-- defaulted. The other two kinds are still quoted from a lesson and still set
-- all of them.
ALTER TABLE flashcards ALTER COLUMN lesson_id    DROP NOT NULL;
ALTER TABLE flashcards ALTER COLUMN lesson_slug  SET DEFAULT '';
ALTER TABLE flashcards ALTER COLUMN lesson_title SET DEFAULT '';
ALTER TABLE flashcards ALTER COLUMN category     SET DEFAULT '';
