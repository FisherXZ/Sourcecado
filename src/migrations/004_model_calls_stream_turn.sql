-- 004_model_calls_stream_turn.sql — R1: allow streamAgentTurn()'s ledger rows.
-- streamAgentTurn (src/lib/model-gateway.ts) records native tool-calling
-- streaming turns with call_kind='stream_turn'. The original CHECK
-- constraint (001_run_ledger_model_gateway.sql) only allows the four kinds
-- callModel() writes — widen it, additive, callModel()'s kinds untouched.

ALTER TABLE model_calls DROP CONSTRAINT IF EXISTS model_calls_call_kind_check;
ALTER TABLE model_calls ADD CONSTRAINT model_calls_call_kind_check
  CHECK (call_kind IN ('generate_text', 'generate_object', 'embed', 'embed_many', 'stream_turn'));
