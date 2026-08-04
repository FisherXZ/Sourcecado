-- 007_users_auth.sql — H1 per-director Google login.
-- Replaces the single shared DEFAULT_ACTOR sentinel with real users.
--
-- google_sub is Google's stable subject claim, not the email: emails can be
-- reassigned within a Workspace domain, `sub` cannot. It is the join key on
-- every sign-in.
--
-- Sessions stay in a signed JWT cookie (no adapter, no session table); this
-- table exists so `users.id` can be the durable actor_id that runs, chat
-- sessions, and source_permissions all point at.

CREATE TABLE IF NOT EXISTS users (
  id            BIGSERIAL PRIMARY KEY,
  google_sub    TEXT NOT NULL UNIQUE,
  email         TEXT NOT NULL,
  name          TEXT,
  image_url     TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS users_email_idx ON users(email);

-- Run attribution. Nullable because `runs` predates this migration and CLI/
-- test runs have no signed-in user — a NULL here means "unattributed", which
-- is honest, where backfilling a fake owner would not be.
-- Shape mirrors chat_sessions(actor_type, actor_id) rather than a users(id)
-- FK: the ledger already records non-user actors, and H2 tenancy will hang a
-- team_id off this table independently.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS actor_type TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS actor_id   TEXT;

CREATE INDEX IF NOT EXISTS runs_actor_idx ON runs(actor_type, actor_id, started_at DESC);
