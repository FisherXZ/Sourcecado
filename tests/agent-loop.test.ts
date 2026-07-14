import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { runAgentLoop } from "@/lib/agent-loop";
import { createToolRegistry } from "@/lib/tools/registry";
import { echoTool } from "@/lib/tools/echo";
import { startRun, startRunStep } from "@/lib/ledger";
import type { LlmAdapter, LlmMessage, LlmStreamEvent } from "@/lib/llm/types";
import type { Tool } from "@/lib/tools/types";

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

  it("executes a tool_use block, appends a tool_result message, and continues to a final answer", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);
    const messages: LlmMessage[] = [{ role: "system", content: "sys" }, { role: "user", content: "echo hello" }];

    const result = await runAgentLoop({
      messages,
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      adapter: sequentialAdapter([
        () => toolCallTurn("echo", { text: "hello" }),
        () => finalTurn("Echoed hello"),
      ]),
    });

    expect(result.status).toBe("succeeded");
    expect(result.finalText).toBe("Echoed hello");
    expect(result.steps).toBe(2);

    const toolResultMessage = result.messages.find((m) => m.role === "tool_result");
    expect(toolResultMessage).toBeDefined();
    if (toolResultMessage?.role === "tool_result") {
      expect(toolResultMessage.content[0]).toMatchObject({
        toolUseId: "call-1",
        toolName: "echo",
        isError: false,
      });
      expect(toolResultMessage.content[0].content).toBe('Success: {"echoed":"hello"}');
    }
  });

  it("denies a tool whose class is not in the allowed set, as an is_error tool_result — loop continues", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const adminTool: Tool = {
      name: "danger",
      description: "admin-only action",
      permissionClass: "admin",
      argsSchema: z.object({}),
      execute: async () => ({ ok: true }),
    };
    const registry = createToolRegistry([echoTool, adminTool]);

    const result = await runAgentLoop({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: "do danger" }],
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      adapter: sequentialAdapter([
        () => toolCallTurn("danger", {}),
        () => finalTurn("could not use danger"),
      ]),
    });

    expect(result.status).toBe("succeeded"); // a denial is not a run failure
    const toolResultMessage = result.messages.find((m) => m.role === "tool_result");
    if (toolResultMessage?.role === "tool_result") {
      expect(toolResultMessage.content[0]).toMatchObject({ isError: true });
      expect(toolResultMessage.content[0].content).toContain("Error (permission_denied)");
    }
  });

  it("returns invalid_args as an is_error tool_result without throwing", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]); // echo requires { text: string }

    const result = await runAgentLoop({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: "x" }],
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      adapter: sequentialAdapter([
        () => toolCallTurn("echo", { wrong: 1 }),
        () => finalTurn("ok"),
      ]),
    });

    expect(result.status).toBe("succeeded");
    const toolResultMessage = result.messages.find((m) => m.role === "tool_result");
    if (toolResultMessage?.role === "tool_result") {
      expect(toolResultMessage.content[0]).toMatchObject({ isError: true });
      expect(toolResultMessage.content[0].content).toContain("Error (invalid_args)");
    }
  });

  it("feeds a tool execution error back as an is_error tool_result and lets the model recover", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
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

    const result = await runAgentLoop({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: "x" }],
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      adapter: sequentialAdapter([() => toolCallTurn("boom", {}), () => finalTurn("recovered")]),
    });

    expect(result.status).toBe("succeeded");
    const toolResultMessage = result.messages.find((m) => m.role === "tool_result");
    if (toolResultMessage?.role === "tool_result") {
      expect(toolResultMessage.content[0]).toMatchObject({ isError: true });
      expect(toolResultMessage.content[0].content).toContain("Error (tool_error): Tool boom failed: kaboom");
    }
  });

  it("truncates an oversized tool result with a visible notice", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const hugeTool: Tool = {
      name: "huge",
      description: "returns an oversized payload",
      permissionClass: "read",
      argsSchema: z.object({}),
      execute: async () => ({ blob: "x".repeat(20_000) }),
    };
    const registry = createToolRegistry([hugeTool]);

    const result = await runAgentLoop({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: "x" }],
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      adapter: sequentialAdapter([() => toolCallTurn("huge", {}), () => finalTurn("ok")]),
    });

    const toolResultMessage = result.messages.find((m) => m.role === "tool_result");
    if (toolResultMessage?.role === "tool_result") {
      expect(toolResultMessage.content[0].isError).toBe(false);
      expect(toolResultMessage.content[0].content).toMatch(/\[truncated \d+ chars\]$/);
      expect(toolResultMessage.content[0].content.length).toBeLessThan(20_000);
    }
  });

  it("records a real ledger tool step/tool_call row for each tool_use block", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);

    await runAgentLoop({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: "echo hello" }],
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      adapter: sequentialAdapter([
        () => toolCallTurn("echo", { text: "hello" }),
        () => finalTurn("Echoed hello"),
      ]),
    });

    const { getRunTrace } = await import("@/lib/ledger");
    const trace = await getRunTrace(db, runId);
    const toolStep = trace?.steps
      .flatMap((s) => s.children)
      .find((s) => s.stepKind === "tool" && s.name === "echo");
    expect(toolStep?.toolCalls[0]).toMatchObject({
      toolName: "echo",
      status: "succeeded",
      result: { echoed: "hello" },
    });
  });

  it("fails the run when maxSteps is exceeded, reporting the last real stopReason", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);

    const result = await runAgentLoop({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: "loop forever" }],
      registry,
      allowed: ALLOWED,
      maxSteps: 3,
      db,
      runId,
      parentStepId,
      adapter: sequentialAdapter([() => toolCallTurn("echo", { text: "again" })]),
    });

    expect(result.status).toBe("failed");
    expect(result.stopReason).toBe("tool_use");
    expect(result.steps).toBe(3);
  });

  it("converts a streamAgentTurn throw into a synthetic assistant message and stops with stopReason 'error'", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);
    const throwingAdapter: LlmAdapter = async function* (): AsyncGenerator<LlmStreamEvent> {
      throw new Error("provider unreachable");
    };

    const result = await runAgentLoop({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: "x" }],
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      adapter: throwingAdapter,
    });

    expect(result.status).toBe("failed");
    expect(result.stopReason).toBe("error");
    const last = result.messages[result.messages.length - 1];
    expect(last.role).toBe("assistant");
    if (last.role === "assistant") {
      expect(last.content[0]).toMatchObject({ type: "text", text: "[model error: provider unreachable]" });
    }
  });

  it("stops immediately with a synthetic '[aborted]' message when the signal is already fired", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);
    const controller = new AbortController();
    controller.abort();

    const result = await runAgentLoop({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: "x" }],
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      signal: controller.signal,
      adapter: sequentialAdapter([() => finalTurn("should never run")]),
    });

    expect(result.status).toBe("failed");
    expect(result.stopReason).toBe("aborted");
    const last = result.messages[result.messages.length - 1];
    if (last.role === "assistant") {
      expect(last.content[0]).toMatchObject({ type: "text", text: "[aborted]" });
    }
  });

  it("fails the run when a turn ends with stopReason 'max_tokens' and no tool_use/text — a normal, non-throwing outcome", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);
    const maxTokensAdapter: LlmAdapter = async function* (): AsyncGenerator<LlmStreamEvent> {
      yield { type: "turn_end", stopReason: "max_tokens", usage: { inputTokens: 10, outputTokens: 5, totalTokens: 15 } };
    };

    const result = await runAgentLoop({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: "x" }],
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      adapter: maxTokensAdapter,
    });

    expect(result.status).toBe("failed");
    expect(result.stopReason).toBe("max_tokens");
    expect(result.steps).toBe(1);
  });

  it("reports stopReason 'aborted' (not 'error') when the adapter throws mid-stream while the signal is already aborted", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);
    const controller = new AbortController();
    const midStreamAbortAdapter: LlmAdapter = async function* (): AsyncGenerator<LlmStreamEvent> {
      controller.abort();
      throw new Error("aborted by user");
    };

    const result = await runAgentLoop({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: "x" }],
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
      signal: controller.signal,
      adapter: midStreamAbortAdapter,
    });

    expect(result.status).toBe("failed");
    expect(result.stopReason).toBe("aborted");
    const last = result.messages[result.messages.length - 1];
    expect(last.role).toBe("assistant");
    if (last.role === "assistant") {
      expect(last.content[0]).toMatchObject({ type: "text", text: "[aborted]" });
    }
  });
});
