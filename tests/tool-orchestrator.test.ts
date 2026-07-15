import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import {
  executeTool,
  toLlmToolDefinition,
  TOOL_RESULT_MAX_CHARS,
} from "@/lib/tools/orchestrator";
import { createToolRegistry } from "@/lib/tools/registry";
import { echoTool } from "@/lib/tools/echo";
import { startRun, startRunStep } from "@/lib/ledger";
import type { PermissionClass, Tool } from "@/lib/tools/types";

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

async function seedAgentStep() {
  const db = getDb();
  const run = await startRun(db, { runType: "agent_chat", title: "t", input: {} });
  const step = await startRunStep(db, { runId: run.id, stepKind: "agent", name: "agent_loop", input: {} });
  return { db, runId: run.id, parentStepId: step.id };
}

const ALLOWED = new Set<PermissionClass>(["read", "reason"]);

afterAll(async () => {
  await closeDb();
});

describe("executeTool", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });

  it("executes a registered tool", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);

    const result = await executeTool({
      toolUseId: "call-1",
      name: "echo",
      input: { text: "hello" },
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
    });

    expect(result).toEqual({ content: 'Success: {"echoed":"hello"}', isError: false });

    const steps = await db`
      SELECT step_kind, name, status FROM run_steps
      WHERE run_id = ${runId} AND step_kind = 'tool'
    `;
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ step_kind: "tool", name: "echo", status: "succeeded" });

    const calls = await db`
      SELECT tool_name, status, result_json FROM tool_calls WHERE run_id = ${runId}
    `;
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ tool_name: "echo", status: "succeeded" });
    expect(calls[0].result_json).toEqual({ echoed: "hello" });
  });

  it("truncates an oversized success result", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const bigTool: Tool = {
      name: "big",
      description: "Returns an oversized payload.",
      permissionClass: "read",
      argsSchema: z.object({}),
      async execute() {
        return { blob: "x".repeat(TOOL_RESULT_MAX_CHARS + 5_000) };
      },
    };
    const registry = createToolRegistry([bigTool]);

    const result = await executeTool({
      toolUseId: "call-1",
      name: "big",
      input: {},
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
    });

    const json = JSON.stringify({ blob: "x".repeat(TOOL_RESULT_MAX_CHARS + 5_000) });
    const full = `Success: ${json}`;
    const overflow = full.length - TOOL_RESULT_MAX_CHARS;
    expect(result.isError).toBe(false);
    expect(result.content).toBe(
      `${full.slice(0, TOOL_RESULT_MAX_CHARS)}\n\n[truncated ${overflow} chars]`
    );
  });

  it("returns an is_error result for an unknown tool name", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);

    await expect(
      executeTool({
        toolUseId: "call-1",
        name: "nope",
        input: {},
        registry,
        allowed: ALLOWED,
        db,
        runId,
        parentStepId,
      })
    ).resolves.toEqual({ content: "Error (unknown_tool): Unknown tool: nope.", isError: true });

    const calls = await db`SELECT status, error_type FROM tool_calls WHERE run_id = ${runId}`;
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ status: "failed", error_type: "unknown_tool" });
  });

  it("returns an is_error result when the tool's permission class is not allowed", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const adminTool: Tool = {
      name: "danger",
      description: "Requires admin.",
      permissionClass: "admin",
      argsSchema: z.object({}),
      async execute() {
        return { ok: true };
      },
    };
    const registry = createToolRegistry([adminTool]);

    await expect(
      executeTool({
        toolUseId: "call-1",
        name: "danger",
        input: {},
        registry,
        allowed: ALLOWED,
        db,
        runId,
        parentStepId,
      })
    ).resolves.toMatchObject({ isError: true });

    const calls = await db`SELECT status, error_type FROM tool_calls WHERE run_id = ${runId}`;
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ status: "failed", error_type: "permission_denied" });
  });

  it("returns an is_error result when args fail schema validation", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const registry = createToolRegistry([echoTool]);

    const result = await executeTool({
      toolUseId: "call-1",
      name: "echo",
      input: { text: 42 },
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
    });

    expect(result.isError).toBe(true);
    expect(result.content).toContain("Error (invalid_args): Invalid arguments for echo:");

    const calls = await db`SELECT status, error_type FROM tool_calls WHERE run_id = ${runId}`;
    expect(calls[0]).toMatchObject({ status: "failed", error_type: "invalid_args" });
  });

  it("returns an is_error result when the tool's execute throws", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const brokenTool: Tool = {
      name: "broken",
      description: "Always throws.",
      permissionClass: "read",
      argsSchema: z.object({}),
      async execute() {
        throw new Error("kaboom");
      },
    };
    const registry = createToolRegistry([brokenTool]);

    await expect(
      executeTool({
        toolUseId: "call-1",
        name: "broken",
        input: {},
        registry,
        allowed: ALLOWED,
        db,
        runId,
        parentStepId,
      })
    ).resolves.toEqual({
      content: "Error (tool_error): Tool broken failed: kaboom",
      isError: true,
    });

    const calls = await db`SELECT status, error_type, error_message FROM tool_calls WHERE run_id = ${runId}`;
    expect(calls[0]).toMatchObject({ status: "failed", error_type: "tool_error" });
    expect(calls[0].error_message).toContain("kaboom");
  });

  it("truncates an oversized error message the same way as success", async () => {
    const { db, runId, parentStepId } = await seedAgentStep();
    const noisyTool: Tool = {
      name: "noisy",
      description: "Throws an oversized error.",
      permissionClass: "read",
      argsSchema: z.object({}),
      async execute() {
        throw new Error("e".repeat(TOOL_RESULT_MAX_CHARS + 3_000));
      },
    };
    const registry = createToolRegistry([noisyTool]);

    const result = await executeTool({
      toolUseId: "call-1",
      name: "noisy",
      input: {},
      registry,
      allowed: ALLOWED,
      db,
      runId,
      parentStepId,
    });

    const full = `Error (tool_error): Tool noisy failed: ${"e".repeat(TOOL_RESULT_MAX_CHARS + 3_000)}`;
    const overflow = full.length - TOOL_RESULT_MAX_CHARS;
    expect(result.isError).toBe(true);
    expect(result.content).toBe(
      `${full.slice(0, TOOL_RESULT_MAX_CHARS)}\n\n[truncated ${overflow} chars]`
    );
  });
});

describe("toLlmToolDefinition", () => {
  it("toLlmToolDefinition produces a JSON Schema reflecting the tool's argsSchema", () => {
    const def = toLlmToolDefinition(echoTool);

    expect(def.name).toBe("echo");
    expect(def.description).toBe(echoTool.description);
    const schema = def.inputSchema as { properties?: Record<string, { type?: string }> };
    expect(schema.properties?.text?.type).toBe("string");
  });

  it("toLlmToolDefinition falls back to an empty schema when z.toJSONSchema throws", () => {
    // z.toJSONSchema cannot represent bigint and throws on it — a real,
    // deterministic trigger for the catch branch. (The plan's
    // vi.spyOn(z, "toJSONSchema") approach is impossible here: ESM module
    // namespaces are not configurable under vitest.)
    const unrepresentableTool: Tool = {
      name: "unrepresentable",
      description: "argsSchema cannot be converted to JSON Schema.",
      permissionClass: "read",
      argsSchema: z.bigint() as unknown as Tool["argsSchema"],
      async execute() {
        return {};
      },
    };
    expect(() => z.toJSONSchema(unrepresentableTool.argsSchema)).toThrow();

    const def = toLlmToolDefinition(unrepresentableTool);
    expect(def).toEqual({
      name: "unrepresentable",
      description: unrepresentableTool.description,
      inputSchema: {},
    });
  });
});
