-- 000_baseline.sql — F2 baseline migration.
-- Enables pgvector so later migrations can add vector columns. Idempotent.
CREATE EXTENSION IF NOT EXISTS vector;
