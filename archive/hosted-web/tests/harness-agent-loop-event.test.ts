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

const ALLOWED = new Set<Tool["permissionClass"]>(["read", "reason"]);

// A fake adapter: turn 1 emits a text delta then calls echo; turn 2 answers.
const fakeAdapter: LlmAdapter = async function* (request): AsyncGenerator<LlmStreamEvent> {
  const isFirstTurn = request.messages.filter((m) => m.role === "assistant").length === 0;
  if (isFirstTurn) {
    yield { type: "text_delta", delta: "checking..." };
    yield { type: "tool_call_start", id: "call-1", name: "echo" };
    yield { type: "tool_call_end", id: "call-1", name: "echo", input: { text: "hi" } };
    yield { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
    return;
  }
  yield { type: "text_delta", delta: "done" };
  yield { type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
};

describe("runAgent onAgentLoopEvent", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("forwards llm and tool_start/tool_end events without disturbing onStep", async () => {
    const registry = createToolRegistry([echoTool]);
    const loopEvents: string[] = [];
    const stepEvents: AgentStepEvent[] = [];

    const result = await runAgent({
      question: "echo hi",
      registry,
      allowedClasses: ALLOWED,
      adapter: fakeAdapter,
      onStep: (e) => {
        stepEvents.push(e);
      },
      onAgentLoopEvent: (e) => {
        loopEvents.push(e.type);
      },
    });

    expect(result.status).toBe("succeeded");
    expect(loopEvents).toContain("llm");
    expect(loopEvents).toContain("tool_start");
    expect(loopEvents).toContain("tool_end");
    // onStep is unaffected — still one event, still only fired for the completed tool step.
    expect(stepEvents).toHaveLength(1);
    expect(stepEvents[0]).toMatchObject({ tool: "echo", ok: true });
  });

  it("omitting onAgentLoopEvent reproduces today's behavior (no throw, same result)", async () => {
    const registry = createToolRegistry([echoTool]);
    const result = await runAgent({
      question: "echo hi",
      registry,
      allowedClasses: ALLOWED,
      adapter: fakeAdapter,
    });
    expect(result.status).toBe("succeeded");
  });

  it("fires onAgentLoopEvent even when onStep is omitted (no silent no-op)", async () => {
    const registry = createToolRegistry([echoTool]);
    const loopEvents: string[] = [];

    const result = await runAgent({
      question: "echo hi",
      registry,
      allowedClasses: ALLOWED,
      adapter: fakeAdapter,
      onAgentLoopEvent: (e) => {
        loopEvents.push(e.type);
      },
    });

    expect(result.status).toBe("succeeded");
    expect(loopEvents).toContain("llm");
    expect(loopEvents).toContain("tool_start");
    expect(loopEvents).toContain("tool_end");
  });
});
