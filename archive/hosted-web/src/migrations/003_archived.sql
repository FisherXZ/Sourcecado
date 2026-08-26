-- 003_archived.sql — soft-archive for source records (A7.2 "correct/retire a source").
-- Archiving is reversible and never deletes: a hard DELETE would orphan facts via
-- semantic_facts.source_record_id ON DELETE SET NULL (accepted-but-citationless
-- facts would keep surfacing). Retrieval excludes archived sources centrally in
-- resolveAllowedSourceIds; management views pass includeArchived to still see them.

ALTER TABLE source_records ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS source_records_archived_idx ON source_records(archived_at);
