-- 002_memory.sql — Feature A memory schema (source records, chunks, semantic facts, extraction cache, permissions).

CREATE TABLE IF NOT EXISTS source_records (
  id             BIGSERIAL PRIMARY KEY,
  source_id      TEXT NOT NULL UNIQUE,
  path           TEXT NOT NULL UNIQUE,
  title          TEXT,
  source_type    TEXT NOT NULL CHECK (source_type IN ('markdown', 'text', 'csv', 'email', 'note')),
  content_hash   TEXT NOT NULL,
  raw_text       TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_chunks (
  id               BIGSERIAL PRIMARY KEY,
  source_record_id BIGINT NOT NULL REFERENCES source_records(id) ON DELETE CASCADE,
  chunk_index      INTEGER NOT NULL,
  text             TEXT NOT NULL,
  chunk_hash       TEXT NOT NULL,
  embedding        vector(1536),
  citation         TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_record_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS semantic_facts (
  id               BIGSERIAL PRIMARY KEY,
  subject          TEXT NOT NULL,
  predicate        TEXT NOT NULL,
  object           TEXT NOT NULL,
  source_record_id BIGINT REFERENCES source_records(id) ON DELETE SET NULL,
  source_chunk_id  BIGINT REFERENCES memory_chunks(id) ON DELETE SET NULL,
  confidence       REAL NOT NULL,
  status           TEXT NOT NULL CHECK (status IN ('candidate', 'accepted', 'conflicted', 'stale')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS extraction_runs (
  id                     BIGSERIAL PRIMARY KEY,
  source_chunk_id        BIGINT REFERENCES memory_chunks(id) ON DELETE SET NULL,
  cache_key              TEXT NOT NULL UNIQUE,
  chunk_hash             TEXT NOT NULL,
  extractor_type         TEXT NOT NULL,
  extractor_version      TEXT NOT NULL,
  prompt_hash            TEXT NOT NULL,
  schema_version         TEXT NOT NULL,
  model_name             TEXT NOT NULL,
  raw_output             TEXT,
  parsed_candidates_json JSONB,
  status                 TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
  error                  TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_permissions (
  id             BIGSERIAL PRIMARY KEY,
  principal_type TEXT NOT NULL CHECK (principal_type IN ('user', 'oauth_client', 'test_client')),
  principal_id   TEXT NOT NULL,
  source_id      TEXT NOT NULL,
  access         TEXT NOT NULL CHECK (access IN ('read')),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (principal_type, principal_id, source_id)
);

CREATE INDEX IF NOT EXISTS source_records_source_id_idx ON source_records(source_id);

CREATE INDEX IF NOT EXISTS memory_chunks_source_record_idx ON memory_chunks(source_record_id);
CREATE INDEX IF NOT EXISTS memory_chunks_embedding_idx ON memory_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS semantic_facts_source_status_idx ON semantic_facts(source_record_id, status);
