-- Ensure deleting a run removes raw model/tool payload rows instead of orphaning them.

ALTER TABLE model_calls DROP CONSTRAINT IF EXISTS model_calls_run_id_fkey;
ALTER TABLE model_calls
  ADD CONSTRAINT model_calls_run_id_fkey
  FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE;

ALTER TABLE model_calls DROP CONSTRAINT IF EXISTS model_calls_run_step_id_fkey;
ALTER TABLE model_calls
  ADD CONSTRAINT model_calls_run_step_id_fkey
  FOREIGN KEY (run_step_id) REFERENCES run_steps(id) ON DELETE CASCADE;

ALTER TABLE tool_calls DROP CONSTRAINT IF EXISTS tool_calls_run_id_fkey;
ALTER TABLE tool_calls
  ADD CONSTRAINT tool_calls_run_id_fkey
  FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE;

ALTER TABLE tool_calls DROP CONSTRAINT IF EXISTS tool_calls_run_step_id_fkey;
ALTER TABLE tool_calls
  ADD CONSTRAINT tool_calls_run_step_id_fkey
  FOREIGN KEY (run_step_id) REFERENCES run_steps(id) ON DELETE CASCADE;
