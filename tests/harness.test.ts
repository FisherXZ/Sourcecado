import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { getRunTrace } from "@/lib/ledger";
import { runMigrations } from "@/lib/migrate";
import { runAgent } from "@/lib/harness";
import { createToolRegistry } from "@/lib/tools/registry";
import { echoTool } from "@/lib/tools/echo";
import type { Tool } from "@/lib/tools/types";
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

const ALLOWED = new Set<Tool["permissionClass"]>(["read", "reason"]);

describe("runAgent", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("returns a failed result instead of throwing when the DB init fails (startRun throws)", async () => {
    const brokenDb = new Proxy(getDb(), {
      get(target, prop) {
        const original = Reflect.get(target, prop) as unknown;
        if (typeof original === "function") {
          return () => {
            throw new Error("DB connection refused");
          };
        }
        return original;
      },
    }) as typeof getDb extends () => infer R ? R : never;

    const registry = createToolRegistry([echoTool]);
    const result = await runAgent({ question: "any", registry, db: brokenDb });

    expect(result.status).toBe("failed");
    expect(result.runId).toBe(0);
    expect(result.steps).toBe(0);
  });

  it("runs a multi-step loop (tool then final) via native tool_use and traces it fully", async () => {
    const db = getDb();
    const registry = createToolRegistry([echoTool]);
    const adapter = sequentialAdapter([
      () => toolCallTurn("echo", { text: "hello" }),
      () => finalTurn("Echoed hello"),
    ]);

    const result = await runAgent({ question: "echo hello", registry, allowedClasses: ALLOWED, adapter });

    expect(result.status).toBe("succeeded");
    expect(result.answer).toBe("Echoed hello");

    const trace = await getRunTrace(db, result.runId);
    expect(trace?.status).toBe("succeeded");
    const agentStep = trace?.steps[0];
    expect(agentStep?.stepKind).toBe("agent");

    const modelSteps = agentStep?.children.filter((s) => s.stepKind === "model") ?? [];
    expect(modelSteps).toHaveLength(2);
    expect(modelSteps[0]?.modelCalls).toHaveLength(1);

    const toolStep = agentStep?.children.find((s) => s.stepKind === "tool" && s.name === "echo");
    expect(toolStep?.toolCalls[0]).toMatchObject({
      toolName: "echo",
      status: "succeeded",
      result: { echoed: "hello" },
    });
  });

  it("refuses and logs a tool whose class is not in the allowed set", async () => {
    const db = getDb();
    const adminTool: Tool = {
      name: "danger",
      description: "admin-only action",
      permissionClass: "admin",
      argsSchema: z.object({}),
      execute: async () => ({ ok: true }),
    };
    const registry = createToolRegistry([echoTool, adminTool]);
    const adapter = sequentialAdapter([
      () => toolCallTurn("danger", {}),
      () => finalTurn("could not use danger"),
    ]);

    const result = await runAgent({ question: "do danger", registry, allowedClasses: ALLOWED, adapter });

    expect(result.status).toBe("succeeded");
    const trace = await getRunTrace(db, result.runId);
    const toolStep = trace?.steps[0]?.children.find((s) => s.name === "danger");
    expect(toolStep?.toolCalls[0]).toMatchObject({
      toolName: "danger",
      status: "failed",
      errorType: "permission_denied",
    });
  });

  it("fails the run when maxSteps is exceeded", async () => {
    const db = getDb();
    const registry = createToolRegistry([echoTool]);
    const adapter = sequentialAdapter([() => toolCallTurn("echo", { text: "again" })]);

    const result = await runAgent({
      question: "loop forever",
      registry,
      allowedClasses: ALLOWED,
      maxSteps: 3,
      adapter,
    });

    expect(result.status).toBe("failed");
    expect(result.steps).toBe(3);
    const trace = await getRunTrace(db, result.runId);
    expect(trace?.status).toBe("failed");
    expect(trace?.errorType).toBe("max_steps_exceeded");
  });

  it("feeds a tool execution error back and lets the model recover", async () => {
    const boomTool: Tool = {
      name: "boom",
      description: "always throws",
      permissionClass: "read",
      argsSchema: z.object({}),
      execute: async () => {
        throw new Error("kaboom");
      },
    };
    const registry = createToolRegistry([boomTool]);
    const adapter = sequentialAdapter([() => toolCallTurn("boom", {}), () => finalTurn("recovered")]);

    const result = await runAgent({ question: "x", registry, allowedClasses: ALLOWED, adapter });

    expect(result.status).toBe("succeeded");
    const db = getDb();
    const trace = await getRunTrace(db, result.runId);
    const toolStep = trace?.steps[0]?.children.find((s) => s.name === "boom");
    expect(toolStep?.toolCalls[0]).toMatchObject({ status: "failed", errorType: "tool_error" });
  });

  it("feeds invalid tool args back and lets the model recover", async () => {
    const registry = createToolRegistry([echoTool]); // echo requires { text: string }
    const adapter = sequentialAdapter([() => toolCallTurn("echo", { wrong: 1 }), () => finalTurn("ok")]);

    const result = await runAgent({ question: "x", registry, allowedClasses: ALLOWED, adapter });

    expect(result.status).toBe("succeeded");
    const db = getDb();
    const trace = await getRunTrace(db, result.runId);
    const toolStep = trace?.steps[0]?.children.find((s) => s.name === "echo");
    expect(toolStep?.toolCalls[0]).toMatchObject({ status: "failed", errorType: "invalid_args" });
  });

  it("uses `instructions` as the system message when provided, else a default identity line", async () => {
    let capturedSystem: string | undefined;
    const capturingAdapter: LlmAdapter = async function* (request) {
      const first = request.messages[0];
      capturedSystem = first.role === "system" ? first.content : undefined;
      yield { type: "text_delta", delta: "ok" };
      yield { type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
    };
    const registry = createToolRegistry([echoTool]);

    await runAgent({ question: "x", registry, adapter: capturingAdapter, instructions: "CUSTOM_INSTRUCTIONS" });
    expect(capturedSystem).toBe("CUSTOM_INSTRUCTIONS");

    await runAgent({ question: "x", registry, adapter: capturingAdapter });
    expect(capturedSystem).toMatch(/sourcing agent/i);
  });

  it("threads priorMessages into messages[] immediately before the new user message", async () => {
    let capturedMessages: unknown[] | undefined;
    const capturingAdapter: LlmAdapter = async function* (request) {
      // Snapshot now: agent-loop.ts pushes the model's reply onto this same
      // array (by reference) once the turn ends, so a live reference would
      // observe post-turn mutations instead of what was actually sent.
      capturedMessages = [...request.messages];
      yield { type: "text_delta", delta: "ok" };
      yield { type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
    };
    const registry = createToolRegistry([echoTool]);
    const prior: LlmMessage[] = [
      { role: "user", content: "earlier question" },
      { role: "assistant", content: [{ type: "text", text: "earlier answer" }] },
    ];

    await runAgent({ question: "new question", registry, adapter: capturingAdapter, priorMessages: prior });

    expect(capturedMessages).toEqual([
      { role: "system", content: expect.stringMatching(/sourcing agent/i) },
      ...prior,
      { role: "user", content: "new question" },
    ]);
  });
});
