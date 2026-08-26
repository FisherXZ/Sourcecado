import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { runAgent, type AgentStepEvent } from "@/lib/harness";
import { createToolRegistry } from "@/lib/tools/registry";
import { echoTool } from "@/lib/tools/echo";
import type { Tool } from "@/lib/tools/types";
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

function sequentialAdapter(turns: (() => AsyncGenerator<LlmStreamEvent>)[]): LlmAdapter {
  let call = 0;
  return async function* (_request, _signal) {
    const turn = turns[Math.min(call, turns.length - 1)];
    call += 1;
    for await (const event of turn()) yield event;
  };
}

const ALLOWED = new Set<Tool["permissionClass"]>(["read", "reason"]);

describe("runAgent onStep", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("emits one onStep event per executed tool step (not for the final answer), carrying accumulated thought text", async () => {
    const registry = createToolRegistry([echoTool]);
    const adapter = sequentialAdapter([
      async function* () {
        yield { type: "text_delta", delta: "let me echo" };
        yield { type: "tool_call_start", id: "call-1", name: "echo" };
        yield { type: "tool_call_end", id: "call-1", name: "echo", input: { text: "hello" } };
        yield { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
      },
      async function* () {
        yield { type: "text_delta", delta: "Echoed hello" };
        yield { type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
      },
    ]);

    const events: AgentStepEvent[] = [];
    const result = await runAgent({
      question: "echo hello",
      registry,
      allowedClasses: ALLOWED,
      adapter,
      onStep: (e) => {
        events.push(e);
      },
    });

    expect(result.status).toBe("succeeded");
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ index: 1, tool: "echo", ok: true, thought: "let me echo" });
    expect(events[0].observation).toContain("hello");
  });

  it("marks a failed tool step with ok:false", async () => {
    const registry = createToolRegistry([echoTool]); // echo requires { text }
    const adapter = sequentialAdapter([
      async function* () {
        yield { type: "tool_call_start", id: "call-1", name: "echo" };
        yield { type: "tool_call_end", id: "call-1", name: "echo", input: { wrong: 1 } };
        yield { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
      },
      async function* () {
        yield { type: "text_delta", delta: "done" };
        yield { type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
      },
    ]);

    const events: AgentStepEvent[] = [];
    await runAgent({ question: "x", registry, allowedClasses: ALLOWED, adapter, onStep: (e) => events.push(e) });

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ index: 1, tool: "echo", ok: false });
  });

  it("awaits an async onStep before continuing the loop", async () => {
    const registry = createToolRegistry([echoTool]);
    const adapter = sequentialAdapter([
      async function* () {
        yield { type: "tool_call_start", id: "call-1", name: "echo" };
        yield { type: "tool_call_end", id: "call-1", name: "echo", input: { text: "a" } };
        yield { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
      },
      async function* () {
        yield { type: "tool_call_start", id: "call-2", name: "echo" };
        yield { type: "tool_call_end", id: "call-2", name: "echo", input: { text: "b" } };
        yield { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
      },
      async function* () {
        yield { type: "text_delta", delta: "ok" };
        yield { type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
      },
    ]);

    const order: string[] = [];
    await runAgent({
      question: "x",
      registry,
      allowedClasses: ALLOWED,
      adapter,
      onStep: async (e) => {
        order.push(`start-${e.index}`);
        await Promise.resolve();
        order.push(`end-${e.index}`);
      },
    });

    // Each onStep fully resolves before the next step's onStep starts.
    expect(order).toEqual(["start-1", "end-1", "start-2", "end-2"]);
  });
});
