-- 007_outreach_history.sql — B1.4: relationship timeline, pulled forward from B3.
-- A row records one past interaction with a Contact. occurred_at and summary are
-- required (a history entry without either isn't useful on the profile card);
-- channel and citation are optional, since a manually recalled conversation may
-- have neither a clear channel label nor a source document behind it.

CREATE TABLE IF NOT EXISTS outreach_history (
  id           BIGSERIAL PRIMARY KEY,
  contact_id   BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  occurred_at  TIMESTAMPTZ NOT NULL,
  channel      TEXT,
  summary      TEXT NOT NULL,
  citation     TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS outreach_history_contact_idx ON outreach_history(contact_id, occurred_at DESC);
