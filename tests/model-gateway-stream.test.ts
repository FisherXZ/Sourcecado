import { closeDb, getDb } from "@/lib/db";
import { startRun } from "@/lib/ledger";
import { runMigrations } from "@/lib/migrate";
import { ModelGatewayError, streamAgentTurn } from "@/lib/model-gateway";
import type { LlmAdapter, LlmStreamEvent } from "@/lib/llm/types";

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

function fakeAdapter(events: LlmStreamEvent[]): LlmAdapter {
  return async function* () {
    for (const e of events) yield e;
  };
}

async function collect(
  gen: AsyncGenerator<LlmStreamEvent, unknown, void>,
): Promise<{ events: LlmStreamEvent[]; result: unknown }> {
  const events: LlmStreamEvent[] = [];
  let next = await gen.next();
  while (!next.done) {
    events.push(next.value);
    next = await gen.next();
  }
  return { events, result: next.value };
}

describe("streamAgentTurn", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("records a succeeded text turn and returns the accumulated message", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const adapter = fakeAdapter([
      { type: "text_delta", delta: "Echoed hi" },
      { type: "turn_end", stopReason: "end", usage: { inputTokens: 5, outputTokens: 3, totalTokens: 8 } },
    ]);

    const { events, result } = await collect(
      streamAgentTurn(db, {
        taskName: "chat_turn",
        promptVersion: "1",
        messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
        tools: [],
        trace: { runId: run.id },
        adapter,
      }),
    );

    expect(events).toHaveLength(2);
    expect(result).toMatchObject({
      stopReason: "end",
      message: { role: "assistant", content: [{ type: "text", text: "Echoed hi" }] },
    });

    const rows = await db`SELECT * FROM model_calls WHERE run_id = ${run.id}`;
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      call_kind: "stream_turn",
      status: "succeeded",
      input_tokens: 5,
      output_tokens: 3,
      total_tokens: 8,
    });

    const steps = await db`SELECT * FROM run_steps WHERE run_id = ${run.id}`;
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ step_kind: "model", status: "succeeded" });
  });

  it("accumulates a tool_use turn with no text block", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const adapter = fakeAdapter([
      { type: "tool_call_start", id: "t1", name: "search_memory" },
      { type: "tool_call_delta", id: "t1", delta: '{"q":"x"}' },
      { type: "tool_call_end", id: "t1", name: "search_memory", input: { q: "x" } },
      { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } },
    ]);

    const { result } = await collect(
      streamAgentTurn(db, {
        taskName: "chat_turn",
        promptVersion: "1",
        messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
        tools: [],
        trace: { runId: run.id },
        adapter,
      }),
    );

    expect(result).toMatchObject({
      stopReason: "tool_use",
      message: { content: [{ type: "tool_use", id: "t1", name: "search_memory", input: { q: "x" } }] },
    });
  });

  it("marks the ledger failed with provider_error on a non-abort throw", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const adapter: LlmAdapter = async function* () {
      throw new Error("boom");
    };

    const gen = streamAgentTurn(db, {
      taskName: "chat_turn",
      promptVersion: "1",
      messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
      tools: [],
      trace: { runId: run.id },
      adapter,
    });

    await expect(collect(gen)).rejects.toThrow(ModelGatewayError);
    const rows = await db`SELECT * FROM model_calls WHERE run_id = ${run.id}`;
    expect(rows[0]).toMatchObject({ status: "failed", error_type: "provider_error" });
    const steps = await db`SELECT * FROM run_steps WHERE run_id = ${run.id}`;
    expect(steps[0]).toMatchObject({ status: "failed", error_type: "provider_error" });
  });

  it("marks the ledger failed with aborted when the signal fired", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const controller = new AbortController();
    controller.abort();
    const adapter: LlmAdapter = async function* () {
      throw new Error("aborted mid-stream");
    };

    const gen = streamAgentTurn(db, {
      taskName: "chat_turn",
      promptVersion: "1",
      messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
      tools: [],
      trace: { runId: run.id },
      adapter,
      signal: controller.signal,
    });

    await expect(collect(gen)).rejects.toThrow(ModelGatewayError);
    const rows = await db`SELECT * FROM model_calls WHERE run_id = ${run.id}`;
    expect(rows[0]).toMatchObject({ status: "failed", error_type: "aborted" });
  });

  it("marks the ledger abandoned when the consumer stops draining mid-stream", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const adapter = fakeAdapter([
      { type: "text_delta", delta: "partial" },
      { type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } },
    ]);

    const gen = streamAgentTurn(db, {
      taskName: "chat_turn",
      promptVersion: "1",
      messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
      tools: [],
      trace: { runId: run.id },
      adapter,
    });

    const first = await gen.next();
    expect(first.done).toBe(false);
    // Simulate an SSE client disconnect: the route's stream is cancelled and
    // the generator is returned without being drained.
    await gen.return(undefined as never);

    const rows = await db`SELECT * FROM model_calls WHERE run_id = ${run.id}`;
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ status: "failed", error_type: "abandoned" });
    const steps = await db`SELECT * FROM run_steps WHERE run_id = ${run.id}`;
    expect(steps[0]).toMatchObject({ status: "failed", error_type: "abandoned" });
  });

  it("uses the adapter test seam verbatim, bypassing pickAdapter", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const adapter = fakeAdapter([{ type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } }]);

    const { result } = await collect(
      streamAgentTurn(db, {
        taskName: "chat_turn",
        promptVersion: "1",
        providerName: "bogus-provider",
        messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
        tools: [],
        trace: { runId: run.id },
        adapter,
      }),
    );
    expect(result).toMatchObject({ stopReason: "end" });
  });

  it("pickAdapter throws config_error for an unrecognized provider with no adapter override", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const gen = streamAgentTurn(db, {
      taskName: "chat_turn",
      promptVersion: "1",
      providerName: "bogus-provider",
      messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
      tools: [],
      trace: { runId: run.id },
    });
    await expect(gen.next()).rejects.toThrow(/Unsupported streaming provider/);
  });

  it("throws config_error for providerName openai with no explicit model", async () => {
    // The guard only fires when no env fallback model exists — clear it for
    // this test (developer .env.local files typically set it).
    const savedModel = process.env.SOURCECADO_GENERATION_MODEL;
    delete process.env.SOURCECADO_GENERATION_MODEL;
    try {
      const db = getDb();
      const run = await startRun(db, { runType: "chat", title: "t" });
      const adapter = fakeAdapter([{ type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } }]);

      const gen = streamAgentTurn(db, {
        taskName: "chat_turn",
        promptVersion: "1",
        providerName: "openai",
        messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
        tools: [],
        trace: { runId: run.id },
        adapter,
      });
      await expect(gen.next()).rejects.toThrow(/requires an explicit model/);
    } finally {
      if (savedModel === undefined) delete process.env.SOURCECADO_GENERATION_MODEL;
      else process.env.SOURCECADO_GENERATION_MODEL = savedModel;
    }
  });
});
