-- 001_run_ledger_model_gateway.sql — F3/F4 Run Ledger + Model Gateway.
-- Sourcecado-owned trace/span records for agent runs, steps, model calls, and tool calls.

CREATE TABLE IF NOT EXISTS runs (
  id            BIGSERIAL PRIMARY KEY,
  run_type      TEXT NOT NULL,
  title         TEXT,
  status        TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled')),
  input_json    JSONB,
  output_json   JSONB,
  metadata_json JSONB,
  error_type    TEXT,
  error_message TEXT,
  error_json    JSONB,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS run_steps (
  id              BIGSERIAL PRIMARY KEY,
  run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  parent_step_id  BIGINT REFERENCES run_steps(id) ON DELETE CASCADE,
  step_kind       TEXT NOT NULL CHECK (
    step_kind IN (
      'agent',
      'model',
      'embedding',
      'tool',
      'retrieval',
      'rerank',
      'artifact',
      'evaluation',
      'system'
    )
  ),
  name            TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled', 'skipped')),
  input_json      JSONB,
  output_json     JSONB,
  metadata_json   JSONB,
  error_type      TEXT,
  error_message   TEXT,
  error_json      JSONB,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_calls (
  id                   BIGSERIAL PRIMARY KEY,
  run_id               BIGINT REFERENCES runs(id) ON DELETE CASCADE,
  run_step_id          BIGINT REFERENCES run_steps(id) ON DELETE CASCADE,
  task_name            TEXT NOT NULL,
  prompt_version       TEXT NOT NULL,
  prompt_hash          TEXT NOT NULL,
  provider             TEXT NOT NULL,
  model                TEXT NOT NULL,
  call_kind            TEXT NOT NULL CHECK (call_kind IN ('generate_text', 'generate_object', 'embed', 'embed_many')),
  status               TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled')),
  request_json         JSONB,
  response_json        JSONB,
  usage_json           JSONB,
  input_tokens         INTEGER,
  output_tokens        INTEGER,
  total_tokens         INTEGER,
  embedding_dimensions INTEGER,
  error_type           TEXT,
  error_message        TEXT,
  error_json           JSONB,
  started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at         TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id             BIGSERIAL PRIMARY KEY,
  run_id         BIGINT REFERENCES runs(id) ON DELETE CASCADE,
  run_step_id    BIGINT REFERENCES run_steps(id) ON DELETE CASCADE,
  tool_name      TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled')),
  arguments_json JSONB,
  result_json    JSONB,
  metadata_json  JSONB,
  error_type     TEXT,
  error_message  TEXT,
  error_json     JSONB,
  started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at   TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS runs_status_idx ON runs(status);
CREATE INDEX IF NOT EXISTS runs_started_at_idx ON runs(started_at);

CREATE INDEX IF NOT EXISTS run_steps_run_id_idx ON run_steps(run_id);
CREATE INDEX IF NOT EXISTS run_steps_parent_step_id_idx ON run_steps(parent_step_id);
CREATE INDEX IF NOT EXISTS run_steps_status_idx ON run_steps(status);
CREATE INDEX IF NOT EXISTS run_steps_started_at_idx ON run_steps(started_at);

CREATE INDEX IF NOT EXISTS model_calls_run_id_idx ON model_calls(run_id);
CREATE INDEX IF NOT EXISTS model_calls_run_step_id_idx ON model_calls(run_step_id);
CREATE INDEX IF NOT EXISTS model_calls_status_idx ON model_calls(status);
CREATE INDEX IF NOT EXISTS model_calls_task_name_idx ON model_calls(task_name);
CREATE INDEX IF NOT EXISTS model_calls_started_at_idx ON model_calls(started_at);

CREATE INDEX IF NOT EXISTS tool_calls_run_id_idx ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS tool_calls_run_step_id_idx ON tool_calls(run_step_id);
CREATE INDEX IF NOT EXISTS tool_calls_status_idx ON tool_calls(status);
CREATE INDEX IF NOT EXISTS tool_calls_tool_name_idx ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS tool_calls_started_at_idx ON tool_calls(started_at);
