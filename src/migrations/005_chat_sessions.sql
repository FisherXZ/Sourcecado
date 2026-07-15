-- 005_chat_sessions.sql — R6 server-side chat session persistence.
-- content_json stores exactly the `content` field of the corresponding
-- LlmMessage variant (a string for system/user; an array of
-- LlmAssistantBlock/LlmToolResultBlock for assistant/tool_result), so
-- round-tripping is a direct { role, content: row.content_json } reassembly.
-- run_id is nullable since system/user rows precede any run.

CREATE TABLE IF NOT EXISTS chat_sessions (
  id            BIGSERIAL PRIMARY KEY,
  actor_type    TEXT NOT NULL,
  actor_id      TEXT NOT NULL,
  title         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id            BIGSERIAL PRIMARY KEY,
  session_id    BIGINT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role          TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool_result')),
  content_json  JSONB NOT NULL,
  run_id        BIGINT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_idx ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS chat_sessions_actor_idx ON chat_sessions(actor_type, actor_id, updated_at DESC);
