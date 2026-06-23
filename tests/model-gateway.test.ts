import { closeDb, getDb } from "@/lib/db";
import { startRun } from "@/lib/ledger";
import {
  callModel,
  ModelGatewayError,
  type ModelGatewayProvider,
} from "@/lib/model-gateway";
import { runMigrations } from "@/lib/migrate";
import { z } from "zod";

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("callModel()", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });

  afterEach(async () => {
    await closeDb();
  });

  it("records a generation call without trace context", async () => {
    const db = getDb();
    const provider = vi.fn<ModelGatewayProvider>().mockResolvedValue({
      text: "Draft the warm alumni note.",
      usage: { inputTokens: 5, outputTokens: 6, totalTokens: 11 },
      rawResponse: { id: "fake-response" },
    });

    const result = await callModel(db, {
      kind: "generate_text",
      taskName: "draft_outreach",
      promptVersion: "1",
      prompt: "Draft outreach.",
      providerName: "fake",
      model: "fake-model",
      provider,
    });

    expect(result).toMatchObject({
      kind: "generate_text",
      text: "Draft the warm alumni note.",
      runStepId: null,
    });
    expect(provider).toHaveBeenCalledWith(expect.objectContaining({ prompt: "Draft outreach." }));

    const rows = await db`SELECT * FROM model_calls`;
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      task_name: "draft_outreach",
      prompt_version: "1",
      provider: "fake",
      model: "fake-model",
      call_kind: "generate_text",
      status: "succeeded",
      input_tokens: 5,
      output_tokens: 6,
      total_tokens: 11,
      run_id: null,
      run_step_id: null,
    });
    expect(rows[0].prompt_hash).toMatch(/^[a-f0-9]{64}$/);
    expect(rows[0].request_json).toMatchObject({ prompt: "Draft outreach." });
    expect(rows[0].response_json).toMatchObject({ text: "Draft the warm alumni note." });
  });

  it("records a generation call with a linked trace step", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "Trace call" });
    const provider = vi.fn<ModelGatewayProvider>().mockResolvedValue({
      text: "Ranked contacts.",
      usage: { inputTokens: 7, outputTokens: 8, totalTokens: 15 },
    });

    const result = await callModel(db, {
      kind: "generate_text",
      taskName: "rank_sourcing_leads",
      promptVersion: "1",
      prompt: "Rank these contacts.",
      trace: { runId: run.id },
      providerName: "fake",
      model: "fake-model",
      provider,
    });

    expect(result.runStepId).toEqual(expect.any(Number));

    const steps = await db`SELECT * FROM run_steps WHERE run_id = ${run.id}`;
    const calls = await db`SELECT * FROM model_calls WHERE run_id = ${run.id}`;
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({
      step_kind: "model",
      name: "rank_sourcing_leads",
      status: "succeeded",
    });
    expect(calls[0]).toMatchObject({
      status: "succeeded",
    });
    expect(Number(calls[0].run_id)).toBe(run.id);
    expect(Number(calls[0].run_step_id)).toBe(result.runStepId);
  });

  it("validates structured object output with a Zod schema", async () => {
    const db = getDb();
    const schema = z.object({ candidates: z.array(z.string()) });
    const provider = vi.fn<ModelGatewayProvider>().mockResolvedValue({
      object: { candidates: ["Ada", "Grace"] },
      usage: { inputTokens: 4, outputTokens: 5, totalTokens: 9 },
    });

    const result = await callModel(db, {
      kind: "generate_object",
      taskName: "extract_memory_candidates",
      promptVersion: "1",
      prompt: "Extract candidates.",
      schema,
      schemaName: "candidate_list",
      providerName: "fake",
      model: "fake-model",
      provider,
    });

    expect(result.object).toEqual({ candidates: ["Ada", "Grace"] });
    const [row] = await db`SELECT * FROM model_calls`;
    expect(row.response_json).toMatchObject({ object: { candidates: ["Ada", "Grace"] } });
  });

  it("records embedding dimensions for embed and embedMany calls", async () => {
    const db = getDb();
    const embedProvider = vi.fn<ModelGatewayProvider>().mockResolvedValue({
      embedding: Array.from({ length: 1536 }, () => 0.1),
      usage: { tokens: 12 },
    });
    const manyProvider = vi.fn<ModelGatewayProvider>().mockResolvedValue({
      embeddings: [
        Array.from({ length: 1536 }, () => 0.1),
        Array.from({ length: 1536 }, () => 0.2),
      ],
      usage: { tokens: 24 },
    });

    await callModel(db, {
      kind: "embed",
      taskName: "embed_memory_chunk",
      promptVersion: "1",
      value: "Ada founded a lab.",
      providerName: "fake",
      model: "fake-embedding",
      provider: embedProvider,
    });
    await callModel(db, {
      kind: "embed_many",
      taskName: "embed_memory_chunks",
      promptVersion: "1",
      values: ["Ada founded a lab.", "Grace mentors students."],
      providerName: "fake",
      model: "fake-embedding",
      provider: manyProvider,
    });

    const rows = await db`SELECT call_kind, input_tokens, total_tokens, embedding_dimensions FROM model_calls ORDER BY id`;
    expect(rows).toEqual([
      expect.objectContaining({
        call_kind: "embed",
        input_tokens: 12,
        total_tokens: 12,
        embedding_dimensions: 1536,
      }),
      expect.objectContaining({
        call_kind: "embed_many",
        input_tokens: 24,
        total_tokens: 24,
        embedding_dimensions: 1536,
      }),
    ]);
  });

  it("records provider failures and throws ModelGatewayError", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "Failure run" });
    const provider = vi.fn<ModelGatewayProvider>().mockRejectedValue(new Error("Provider down"));

    await expect(
      callModel(db, {
        kind: "generate_text",
        taskName: "draft_outreach",
        promptVersion: "1",
        prompt: "Draft outreach.",
        trace: { runId: run.id },
        providerName: "fake",
        model: "fake-model",
        provider,
      }),
    ).rejects.toBeInstanceOf(ModelGatewayError);

    const [step] = await db`SELECT * FROM run_steps WHERE run_id = ${run.id}`;
    const [call] = await db`SELECT * FROM model_calls WHERE run_id = ${run.id}`;
    expect(step).toMatchObject({
      status: "failed",
      error_type: "provider_error",
      error_message: "Provider down",
    });
    expect(call).toMatchObject({
      status: "failed",
      error_type: "provider_error",
      error_message: "Provider down",
    });
  });

  it("suppresses raw payloads when capturePayloads is false", async () => {
    const db = getDb();
    const provider = vi.fn<ModelGatewayProvider>().mockResolvedValue({
      text: "Hidden payload response.",
      usage: { inputTokens: 1, outputTokens: 2, totalTokens: 3 },
    });

    const result = await callModel(db, {
      kind: "generate_text",
      taskName: "sensitive_task",
      promptVersion: "1",
      prompt: "Sensitive prompt.",
      providerName: "fake",
      model: "fake-model",
      capturePayloads: false,
      provider,
    });

    expect(result.text).toBe("Hidden payload response.");
    const [row] = await db`SELECT * FROM model_calls`;
    expect(row.request_json).toBeNull();
    expect(row.response_json).toBeNull();
  });
});
