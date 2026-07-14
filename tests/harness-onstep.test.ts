import { vi } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import type { ModelGatewayProvider } from "@/lib/model-gateway";
import { runAgent, type AgentStepEvent } from "@/lib/harness";
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

describe("runAgent onStep", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("emits one onStep event per executed tool step (not for the final answer)", async () => {
    const registry = createToolRegistry([echoTool]);
    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValueOnce({
        object: { action: "tool", tool: "echo", args: '{"text":"hello"}', thought: "let me echo" },
      })
      .mockResolvedValueOnce({ object: { action: "final", answer: "Echoed hello" } });

    const events: AgentStepEvent[] = [];
    const result = await runAgent({
      question: "echo hello",
      registry,
      allowedClasses: ALLOWED,
      provider,
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
    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValueOnce({ object: { action: "tool", tool: "echo", args: '{"wrong":1}' } })
      .mockResolvedValueOnce({ object: { action: "final", answer: "done" } });

    const events: AgentStepEvent[] = [];
    await runAgent({ question: "x", registry, allowedClasses: ALLOWED, provider, onStep: (e) => events.push(e) });

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ index: 1, tool: "echo", ok: false });
  });

  it("awaits an async onStep before continuing the loop", async () => {
    const registry = createToolRegistry([echoTool]);
    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValueOnce({ object: { action: "tool", tool: "echo", args: '{"text":"a"}' } })
      .mockResolvedValueOnce({ object: { action: "tool", tool: "echo", args: '{"text":"b"}' } })
      .mockResolvedValueOnce({ object: { action: "final", answer: "ok" } });

    const order: string[] = [];
    await runAgent({
      question: "x",
      registry,
      allowedClasses: ALLOWED,
      provider,
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
