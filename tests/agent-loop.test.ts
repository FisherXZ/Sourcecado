import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { runAgentLoop } from "@/lib/agent-loop";
import { createToolRegistry } from "@/lib/tools/registry";
import { echoTool } from "@/lib/tools/echo";
import { startRun, startRunStep } from "@/lib/ledger";
import type { LlmAdapter, LlmMessage, LlmStreamEvent } from "@/lib/llm/types";

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

// A stateful LlmAdapter that yields one canned turn per call, holding on the
// last turn if called more times than turns provided.
function sequentialAdapter(turns: (() => AsyncGenerator<LlmStreamEvent>)[]): LlmAdapter {
  let call = 0;
  return async function* (_request, _signal) {
    const turn = turns[Math.min(call, turns.length - 1)];
    call += 1;
    for await (const event of turn()) yield event;
  };
}

async function* toolCallTurn(toolName: string, args: unknown): AsyncGenerator<LlmStreamEvent> {
  yield { type: "tool_call_start", id: "call-1", name: toolName };
  yield { type: "tool_call_end", id: "call-1", name: toolName, input: args };
  yield { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 10, outputTokens: 5, totalTokens: 15 } };
}

async function* finalTurn(answer: string): AsyncGenerator<LlmStreamEvent> {
  yield { type: "text_delta", delta: answer };
  yield { type: "turn_end", stopReason: "end", usage: { inputTokens: 5, outputTokens: 3, totalTokens: 8 } };
}

async function seedAgentStep() {
  const db = getDb();
  const run = await startRun(db, { runType: "agent_chat", title: "t", input: {} });
  const step = await startRunStep(db, { runId: run.id, stepKind: "agent", name: "agent_loop", input: {} });
  return { db, runId: run.id, parentStepId: step.id };
}

const ALLOWED = new Set<"read" | "reason" | "enrich" | "draft" | "write_internal" | "admin">(["read", "reason"]);

describe("runAgentLoop", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("stops naturally when the model returns text with no tool_use, returning finalText", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);
    const messages: LlmMessage[] = [
      { role: "system", content: "sys" },
      { role: "user", content: "hi" },
    ];

    const result = await runAgentLoop({
      messages,
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      adapter: sequentialAdapter([() => finalTurn("Hello there.")]),
    });

    expect(result.status).toBe("succeeded");
    expect(result.stopReason).toBe("end");
    expect(result.finalText).toBe("Hello there.");
    expect(result.steps).toBe(1);
    // messages[] grew by exactly one assistant message.
    expect(result.messages).toHaveLength(3);
    expect(result.messages[2].role).toBe("assistant");
  });
});
