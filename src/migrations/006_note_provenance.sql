-- 006_note_provenance.sql — provenance stamp for memory notes (poisoned-note traceability).
-- A note written by a chat run (whose text may be prompt-injected) needs the writing
-- run and actor recorded on its source_records row, so a bad note can be traced back to
-- the run that produced it and archived. Run link is SET NULL on run delete (mirrors
-- semantic_facts): losing the run pointer must not delete the note itself.

ALTER TABLE source_records ADD COLUMN IF NOT EXISTS created_by_run_id BIGINT REFERENCES runs(id) ON DELETE SET NULL;
ALTER TABLE source_records ADD COLUMN IF NOT EXISTS created_by_actor_type TEXT;
ALTER TABLE source_records ADD COLUMN IF NOT EXISTS created_by_actor_id TEXT;

CREATE INDEX IF NOT EXISTS source_records_created_by_run_idx ON source_records(created_by_run_id);
