import { vi } from "vitest";
import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { getRunTrace } from "@/lib/ledger";
import { runMigrations } from "@/lib/migrate";
import type { ModelGatewayProvider } from "@/lib/model-gateway";
import { runAgent } from "@/lib/harness";
import { createToolRegistry } from "@/lib/tools/registry";
import { echoTool } from "@/lib/tools/echo";
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

const ALLOWED = new Set<Tool["permissionClass"]>(["read", "reason"]);

describe("runAgent", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("runs a multi-step loop (tool then final) and traces it fully", async () => {
    const db = getDb();
    const registry = createToolRegistry([echoTool]);
    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValueOnce({ object: { action: "tool", tool: "echo", args: '{"text":"hello"}' } })
      .mockResolvedValueOnce({ object: { action: "final", answer: "Echoed hello" } });

    const result = await runAgent({
      question: "echo hello",
      registry,
      allowedClasses: ALLOWED,
      provider,
    });

    expect(result.status).toBe("succeeded");
    expect(result.answer).toBe("Echoed hello");
    expect(provider).toHaveBeenCalledTimes(2);

    const trace = await getRunTrace(db, result.runId);
    expect(trace?.status).toBe("succeeded");
    const agentStep = trace?.steps[0];
    expect(agentStep?.stepKind).toBe("agent");

    // Each callModel(trace) creates a child "model" step holding the model call.
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
    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValueOnce({ object: { action: "tool", tool: "danger", args: "{}" } })
      .mockResolvedValueOnce({ object: { action: "final", answer: "could not use danger" } });

    const result = await runAgent({ question: "do danger", registry, allowedClasses: ALLOWED, provider });

    expect(result.status).toBe("succeeded"); // refusal is not a run failure
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
    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValue({ object: { action: "tool", tool: "echo", args: '{"text":"again"}' } });

    const result = await runAgent({
      question: "loop forever",
      registry,
      allowedClasses: ALLOWED,
      maxSteps: 3,
      provider,
    });

    expect(result.status).toBe("failed");
    expect(result.steps).toBe(3);
    expect(provider).toHaveBeenCalledTimes(3);
    const trace = await getRunTrace(db, result.runId);
    expect(trace?.status).toBe("failed");
    expect(trace?.errorType).toBe("max_steps_exceeded");
  });

  it("feeds a tool execution error back and lets the model recover", async () => {
    const db = getDb();
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
    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValueOnce({ object: { action: "tool", tool: "boom", args: "{}" } })
      .mockResolvedValueOnce({ object: { action: "final", answer: "recovered" } });

    const result = await runAgent({ question: "x", registry, allowedClasses: ALLOWED, provider });

    expect(result.status).toBe("succeeded");
    const trace = await getRunTrace(db, result.runId);
    const toolStep = trace?.steps[0]?.children.find((s) => s.name === "boom");
    expect(toolStep?.toolCalls[0]).toMatchObject({ status: "failed", errorType: "tool_error" });
  });

  it("feeds invalid tool args back and lets the model recover", async () => {
    const db = getDb();
    const registry = createToolRegistry([echoTool]); // echo requires { text: string }
    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValueOnce({ object: { action: "tool", tool: "echo", args: '{"wrong":1}' } })
      .mockResolvedValueOnce({ object: { action: "final", answer: "ok" } });

    const result = await runAgent({ question: "x", registry, allowedClasses: ALLOWED, provider });

    expect(result.status).toBe("succeeded");
    const trace = await getRunTrace(db, result.runId);
    const toolStep = trace?.steps[0]?.children.find((s) => s.name === "echo");
    expect(toolStep?.toolCalls[0]).toMatchObject({ status: "failed", errorType: "invalid_args" });
  });
});
