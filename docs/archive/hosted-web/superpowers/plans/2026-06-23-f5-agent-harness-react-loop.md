# F5 Agent Harness ReAct Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a permission-gated ReAct tool-use loop that runs a multi-step agent through the F3 Model Gateway and writes the full trace to the F4 Run Ledger, demoable via a thin API trigger and the existing run inspector.

**Architecture:** A `runAgent()` loop asks the model for a typed decision (`tool` or `final`) via the gateway's `generate_object`, dispatches tool calls through a permission-checked registry, and records every step/tool-call/model-call in the ledger. Tool errors, permission refusals, and bad args become observations fed back to the model (the model recovers); only a gateway error or the step cap fails the run.

**Tech Stack:** TypeScript, Next.js 15 (app-router API route), Zod v4, `postgres` (Sql), Vitest against live Postgres, the existing `src/lib/model-gateway.ts` and `src/lib/ledger.ts`.

## Global Constraints

- Tool selection is **structured output** via `callModel({ kind: "generate_object" })` — no native tool-calling, no text parsing.
- Permission model is an **allowed-set per run**, `allow | deny` only — no ranked hierarchy, no interactive `ask` tier.
- Default allowed classes: `new Set(["read", "reason"])`. Default `maxSteps`: `8`. `echo` tool class: `read`.
- Tool error / permission refusal / invalid args → recorded as a **failed tool call** AND returned as an observation; the loop continues. Only an unrecoverable `ModelGatewayError` or exceeding `maxSteps` fails the run.
- **No app-level retries** (transport retries already live in the `ai` SDK).
- Reuse `callModel`, the ledger write path, `getRunTrace`, and the `/runs/[id]` inspector unchanged.
- TDD: every behavior gets a failing test first. DB tests reset ledger tables + `runMigrations` in `beforeEach` and inject a mock `ModelGatewayProvider` (no real model API). Run tests with `DATABASE_URL` pointing at the local Postgres (`postgresql://sourcecado:sourcecado@localhost:5432/sourcecado`, container `sourcecado-db-1`).
- The six permission classes are exactly: `read`, `enrich`, `reason`, `draft`, `write_internal`, `admin`.
- Permission classes are `PermissionClass`; tools implement `Tool<TArgs, TResult>`; the registry is `ToolRegistry` from `createToolRegistry(tools?)`.

---

### Task 1: Tool types + registry

**Files:**
- Create: `src/lib/tools/types.ts`
- Create: `src/lib/tools/registry.ts`
- Test: `tests/tool-registry.test.ts`

**Interfaces:**
- Consumes: nothing (greenfield).
- Produces:
  - `type PermissionClass = "read"|"enrich"|"reason"|"draft"|"write_internal"|"admin"`
  - `interface ToolContext { db: Sql; runId: number; parentStepId: number }`
  - `interface Tool<TArgs=unknown,TResult=unknown> { name: string; description: string; permissionClass: PermissionClass; argsSchema: z.ZodType<TArgs>; execute(args: TArgs, ctx: ToolContext): Promise<TResult> }`
  - `interface ToolRegistry { register(tool: Tool): void; get(name: string): Tool | undefined; list(allowed: Set<PermissionClass>): Tool[] }`
  - `function createToolRegistry(tools?: Tool[]): ToolRegistry`

- [ ] **Step 1: Write `src/lib/tools/types.ts`**

```ts
import type postgres from "postgres";
import type { z } from "zod";

export type Sql = postgres.Sql;

export type PermissionClass =
  | "read"
  | "enrich"
  | "reason"
  | "draft"
  | "write_internal"
  | "admin";

export const PERMISSION_CLASSES: readonly PermissionClass[] = [
  "read",
  "enrich",
  "reason",
  "draft",
  "write_internal",
  "admin",
];

export interface ToolContext {
  db: Sql;
  runId: number;
  parentStepId: number;
}

export interface Tool<TArgs = unknown, TResult = unknown> {
  name: string;
  description: string;
  permissionClass: PermissionClass;
  argsSchema: z.ZodType<TArgs>;
  execute(args: TArgs, ctx: ToolContext): Promise<TResult>;
}
```

- [ ] **Step 2: Write the failing registry test**

Create `tests/tool-registry.test.ts`:

```ts
import { z } from "zod";
import { createToolRegistry } from "@/lib/tools/registry";
import type { Tool } from "@/lib/tools/types";

function fakeTool(name: string, permissionClass: Tool["permissionClass"]): Tool {
  return {
    name,
    description: `${name} tool`,
    permissionClass,
    argsSchema: z.object({}),
    execute: async () => ({ ok: true }),
  };
}

describe("createToolRegistry", () => {
  it("registers and retrieves tools by name", () => {
    const registry = createToolRegistry([fakeTool("a", "read")]);
    expect(registry.get("a")?.name).toBe("a");
    expect(registry.get("missing")).toBeUndefined();
  });

  it("throws on duplicate tool name", () => {
    const registry = createToolRegistry([fakeTool("a", "read")]);
    expect(() => registry.register(fakeTool("a", "read"))).toThrow(/already registered/);
  });

  it("lists only tools whose class is in the allowed set", () => {
    const registry = createToolRegistry([
      fakeTool("r", "read"),
      fakeTool("d", "draft"),
      fakeTool("a", "admin"),
    ]);
    const names = registry
      .list(new Set(["read", "draft"]))
      .map((t) => t.name)
      .sort();
    expect(names).toEqual(["d", "r"]);
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/tool-registry.test.ts`
Expected: FAIL — cannot find module `@/lib/tools/registry`.

- [ ] **Step 4: Write `src/lib/tools/registry.ts`**

```ts
import type { PermissionClass, Tool } from "./types";

export interface ToolRegistry {
  register(tool: Tool): void;
  get(name: string): Tool | undefined;
  list(allowed: Set<PermissionClass>): Tool[];
}

export function createToolRegistry(tools: Tool[] = []): ToolRegistry {
  const byName = new Map<string, Tool>();

  const registry: ToolRegistry = {
    register(tool) {
      if (byName.has(tool.name)) {
        throw new Error(`Tool "${tool.name}" is already registered.`);
      }
      byName.set(tool.name, tool);
    },
    get(name) {
      return byName.get(name);
    },
    list(allowed) {
      return [...byName.values()].filter((tool) => allowed.has(tool.permissionClass));
    },
  };

  for (const tool of tools) {
    registry.register(tool);
  }
  return registry;
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/tool-registry.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/lib/tools/types.ts src/lib/tools/registry.ts tests/tool-registry.test.ts
git commit -m "feat(f5): tool contract + permission-aware registry"
```

---

### Task 2: echo reference tool

**Files:**
- Create: `src/lib/tools/echo.ts`
- Test: `tests/echo-tool.test.ts`

**Interfaces:**
- Consumes: `Tool` from `src/lib/tools/types.ts`.
- Produces:
  - `const echoArgsSchema = z.object({ text: z.string() })`
  - `type EchoArgs = { text: string }`
  - `const echoTool: Tool<EchoArgs, { echoed: string }>` with `name: "echo"`, `permissionClass: "read"`.

- [ ] **Step 1: Write the failing test**

Create `tests/echo-tool.test.ts`:

```ts
import { getDb } from "@/lib/db";
import { echoTool } from "@/lib/tools/echo";

describe("echoTool", () => {
  it("echoes the provided text", async () => {
    const result = await echoTool.execute(
      { text: "hello" },
      { db: getDb(), runId: 0, parentStepId: 0 },
    );
    expect(result).toEqual({ echoed: "hello" });
  });

  it("is a read-class tool named echo", () => {
    expect(echoTool.name).toBe("echo");
    expect(echoTool.permissionClass).toBe("read");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/echo-tool.test.ts`
Expected: FAIL — cannot find module `@/lib/tools/echo`.

- [ ] **Step 3: Write `src/lib/tools/echo.ts`**

```ts
import { z } from "zod";
import type { Tool } from "./types";

export const echoArgsSchema = z.object({ text: z.string() });
export type EchoArgs = z.infer<typeof echoArgsSchema>;

export const echoTool: Tool<EchoArgs, { echoed: string }> = {
  name: "echo",
  description: "Echo back the provided text. A reference tool for the harness.",
  permissionClass: "read",
  argsSchema: echoArgsSchema,
  async execute(args) {
    return { echoed: args.text };
  },
};
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/echo-tool.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lib/tools/echo.ts tests/echo-tool.test.ts
git commit -m "feat(f5): echo reference tool"
```

---

### Task 3: ReAct loop (`runAgent`)

**Files:**
- Create: `src/lib/harness.ts`
- Test: `tests/harness.test.ts`

**Interfaces:**
- Consumes:
  - `getDb()` from `src/lib/db.ts`.
  - From `src/lib/ledger.ts`: `startRun(db,{runType,title?,input?})`, `startRunStep(db,{runId,parentStepId?,stepKind,name,input?})`, `finishRunStep(db,{runStepId,output?})`, `failRunStep(db,{runStepId,errorType,errorMessage})`, `startToolCall(db,{runId,runStepId,toolName,arguments?,metadata?})`, `finishToolCall(db,{toolCallId,result?})`, `failToolCall(db,{toolCallId,errorType,errorMessage})`, `finishRun(db,{runId,output?})`, `failRun(db,{runId,errorType,errorMessage})`, `getRunTrace(db,runId)`.
  - From `src/lib/model-gateway.ts`: `callModel<T>(db,input)`, `ModelGatewayError`, `type ModelGatewayProvider`.
  - From `src/lib/tools/types.ts`: `PermissionClass`, `Sql`. From `src/lib/tools/registry.ts`: `ToolRegistry`.
- Produces:
  - `const agentDecisionSchema` (Zod discriminated union on `action`).
  - `type AgentDecision = { action:"tool"; tool:string; args:Record<string,unknown>; thought?:string } | { action:"final"; answer:string; thought?:string }`
  - `interface RunAgentInput { question:string; registry:ToolRegistry; allowedClasses?:Set<PermissionClass>; maxSteps?:number; provider?:ModelGatewayProvider; db?:Sql }`
  - `interface RunAgentResult { runId:number; status:"succeeded"|"failed"; answer?:string; steps:number }`
  - `function runAgent(input: RunAgentInput): Promise<RunAgentResult>`

- [ ] **Step 1: Write the failing happy-path test**

Create `tests/harness.test.ts`:

```ts
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
      .mockResolvedValueOnce({ object: { action: "tool", tool: "echo", args: { text: "hello" } } })
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
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/harness.test.ts`
Expected: FAIL — cannot find module `@/lib/harness`.

- [ ] **Step 3: Write `src/lib/harness.ts`**

```ts
import { z } from "zod";
import { getDb } from "./db";
import {
  failRun,
  failRunStep,
  failToolCall,
  finishRun,
  finishRunStep,
  finishToolCall,
  startRun,
  startRunStep,
  startToolCall,
} from "./ledger";
import { callModel, ModelGatewayError, type ModelGatewayProvider } from "./model-gateway";
import type { ToolRegistry } from "./tools/registry";
import type { PermissionClass, Sql } from "./tools/types";

export const agentDecisionSchema = z.discriminatedUnion("action", [
  z.object({
    action: z.literal("tool"),
    tool: z.string(),
    args: z.record(z.unknown()),
    thought: z.string().optional(),
  }),
  z.object({
    action: z.literal("final"),
    answer: z.string(),
    thought: z.string().optional(),
  }),
]);
export type AgentDecision = z.infer<typeof agentDecisionSchema>;

export interface RunAgentInput {
  question: string;
  registry: ToolRegistry;
  allowedClasses?: Set<PermissionClass>;
  maxSteps?: number;
  provider?: ModelGatewayProvider;
  db?: Sql;
}

export interface RunAgentResult {
  runId: number;
  status: "succeeded" | "failed";
  answer?: string;
  steps: number;
}

const DEFAULT_ALLOWED: PermissionClass[] = ["read", "reason"];
const DEFAULT_MAX_STEPS = 8;

export async function runAgent(input: RunAgentInput): Promise<RunAgentResult> {
  const db = input.db ?? getDb();
  const allowed = input.allowedClasses ?? new Set(DEFAULT_ALLOWED);
  const maxSteps = input.maxSteps ?? DEFAULT_MAX_STEPS;

  const run = await startRun(db, {
    runType: "agent_chat",
    title: input.question.slice(0, 80),
    input: { question: input.question },
  });
  const agentStep = await startRunStep(db, {
    runId: run.id,
    stepKind: "agent",
    name: "react_loop",
    input: { question: input.question },
  });

  const transcript: string[] = [];
  let step = 0;

  try {
    for (step = 1; step <= maxSteps; step++) {
      const decision = await decide(db, {
        runId: run.id,
        parentStepId: agentStep.id,
        question: input.question,
        registry: input.registry,
        allowed,
        transcript,
        provider: input.provider,
      });

      if (decision.action === "final") {
        await finishRunStep(db, {
          runStepId: agentStep.id,
          output: { answer: decision.answer, steps: step },
        });
        await finishRun(db, {
          runId: run.id,
          output: { answer: decision.answer, steps: step },
        });
        return { runId: run.id, status: "succeeded", answer: decision.answer, steps: step };
      }

      const observation = await executeToolCall(db, {
        decision,
        registry: input.registry,
        allowed,
        runId: run.id,
        parentStepId: agentStep.id,
      });
      transcript.push(`Step ${step}: called ${decision.tool} -> ${observation}`);
    }

    const message = `Agent did not finish within ${maxSteps} steps.`;
    await failRunStep(db, {
      runStepId: agentStep.id,
      errorType: "max_steps_exceeded",
      errorMessage: message,
    });
    await failRun(db, {
      runId: run.id,
      errorType: "max_steps_exceeded",
      errorMessage: message,
    });
    return { runId: run.id, status: "failed", steps: maxSteps };
  } catch (error) {
    const code = error instanceof ModelGatewayError ? error.code : "harness_error";
    const message = error instanceof Error ? error.message : String(error);
    await failRunStep(db, { runStepId: agentStep.id, errorType: code, errorMessage: message });
    await failRun(db, { runId: run.id, errorType: code, errorMessage: message });
    return { runId: run.id, status: "failed", steps: step };
  }
}

interface DecideOptions {
  runId: number;
  parentStepId: number;
  question: string;
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  transcript: string[];
  provider?: ModelGatewayProvider;
}

async function decide(db: Sql, opts: DecideOptions): Promise<AgentDecision> {
  const tools = opts.registry.list(opts.allowed);
  const result = await callModel<AgentDecision>(db, {
    kind: "generate_object",
    taskName: "agent_react_decide",
    promptVersion: "1",
    system: buildSystemPrompt(tools),
    prompt: buildUserPrompt(opts.question, opts.transcript),
    schema: agentDecisionSchema,
    schemaName: "agent_decision",
    trace: { runId: opts.runId, parentStepId: opts.parentStepId },
    provider: opts.provider,
  });
  return result.object;
}

function buildSystemPrompt(tools: { name: string; description: string; permissionClass: string }[]): string {
  const catalog = tools
    .map((t) => `- ${t.name} (${t.permissionClass}): ${t.description}`)
    .join("\n");
  return [
    "You are a sourcing agent. Decide the next action.",
    "Either call one tool, or give a final answer.",
    "Respond with a decision object: {action:'tool', tool, args} or {action:'final', answer}.",
    "Available tools:",
    catalog || "(none)",
  ].join("\n");
}

function buildUserPrompt(question: string, transcript: string[]): string {
  const history = transcript.length ? `\n\nObservations so far:\n${transcript.join("\n")}` : "";
  return `Question: ${question}${history}`;
}

interface ExecuteToolOptions {
  decision: Extract<AgentDecision, { action: "tool" }>;
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  runId: number;
  parentStepId: number;
}

async function executeToolCall(db: Sql, opts: ExecuteToolOptions): Promise<string> {
  const { decision, registry, allowed, runId, parentStepId } = opts;
  const toolName = decision.tool;
  const tool = registry.get(toolName);

  const toolStep = await startRunStep(db, {
    runId,
    parentStepId,
    stepKind: "tool",
    name: toolName,
    input: { args: decision.args },
  });
  const toolCall = await startToolCall(db, {
    runId,
    runStepId: toolStep.id,
    toolName,
    arguments: decision.args,
    metadata: { permissionClass: tool?.permissionClass ?? null },
  });

  if (!tool) {
    return failTool(db, toolStep.id, toolCall.id, "unknown_tool", `Unknown tool: ${toolName}.`);
  }
  if (!allowed.has(tool.permissionClass)) {
    return failTool(
      db,
      toolStep.id,
      toolCall.id,
      "permission_denied",
      `Tool ${toolName} (class ${tool.permissionClass}) is not permitted for this run.`,
    );
  }
  const parsed = tool.argsSchema.safeParse(decision.args);
  if (!parsed.success) {
    return failTool(
      db,
      toolStep.id,
      toolCall.id,
      "invalid_args",
      `Invalid arguments for ${toolName}: ${parsed.error.message}`,
    );
  }

  try {
    const result = await tool.execute(parsed.data, { db, runId, parentStepId: toolStep.id });
    await finishToolCall(db, { toolCallId: toolCall.id, result });
    await finishRunStep(db, { runStepId: toolStep.id, output: result });
    return `Success: ${JSON.stringify(result)}`;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return failTool(db, toolStep.id, toolCall.id, "tool_error", `Tool ${toolName} failed: ${message}`);
  }
}

async function failTool(
  db: Sql,
  runStepId: number,
  toolCallId: number,
  errorType: string,
  message: string,
): Promise<string> {
  await failToolCall(db, { toolCallId, errorType, errorMessage: message });
  await failRunStep(db, { runStepId, errorType, errorMessage: message });
  return `Error (${errorType}): ${message}`;
}
```

- [ ] **Step 4: Run the happy-path test to verify it passes**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/harness.test.ts`
Expected: PASS (1 test).

- [ ] **Step 5: Add the permission-refusal, max-steps, tool-error, and invalid-args tests**

Append these inside the `describe("runAgent", ...)` block in `tests/harness.test.ts`:

```ts
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
      .mockResolvedValueOnce({ object: { action: "tool", tool: "danger", args: {} } })
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
      .mockResolvedValue({ object: { action: "tool", tool: "echo", args: { text: "again" } } });

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
      .mockResolvedValueOnce({ object: { action: "tool", tool: "boom", args: {} } })
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
      .mockResolvedValueOnce({ object: { action: "tool", tool: "echo", args: { wrong: 1 } } })
      .mockResolvedValueOnce({ object: { action: "final", answer: "ok" } });

    const result = await runAgent({ question: "x", registry, allowedClasses: ALLOWED, provider });

    expect(result.status).toBe("succeeded");
    const trace = await getRunTrace(db, result.runId);
    const toolStep = trace?.steps[0]?.children.find((s) => s.name === "echo");
    expect(toolStep?.toolCalls[0]).toMatchObject({ status: "failed", errorType: "invalid_args" });
  });
```

- [ ] **Step 6: Run the full harness test file to verify all pass**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/harness.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add src/lib/harness.ts tests/harness.test.ts
git commit -m "feat(f5): ReAct loop with permission enforcement and ledger tracing"
```

---

### Task 4: Thin API trigger

**Files:**
- Create: `src/app/api/agent/route.ts`
- Reference (read for style): `src/app/api/health/route.ts`
- Test: `tests/agent-route.test.ts`

**Interfaces:**
- Consumes: `runAgent` from `src/lib/harness.ts`, `createToolRegistry` from `src/lib/tools/registry.ts`, `echoTool` from `src/lib/tools/echo.ts`.
- Produces: a `POST` handler that accepts `{ question: string }` and returns `RunAgentResult` JSON (`200` on `succeeded`, `500` on `failed`, `400` on missing question).

- [ ] **Step 1: Write the failing route test**

Create `tests/agent-route.test.ts`:

```ts
import { vi } from "vitest";

// vi.mock is hoisted; declare the mock via vi.hoisted so the factory can see it.
const { runAgentMock } = vi.hoisted(() => ({ runAgentMock: vi.fn() }));
vi.mock("@/lib/harness", () => ({ runAgent: runAgentMock }));

import { POST } from "@/app/api/agent/route";

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/agent", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("POST /api/agent", () => {
  beforeEach(() => {
    runAgentMock.mockReset();
  });

  it("returns 400 when question is missing", async () => {
    const res = await POST(postRequest({}));
    expect(res.status).toBe(400);
  });

  it("runs the agent and returns the run id on success", async () => {
    runAgentMock.mockResolvedValue({ runId: 7, status: "succeeded", answer: "hi", steps: 2 });
    const res = await POST(postRequest({ question: "echo hi" }));
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({ runId: 7, status: "succeeded" });
    expect(runAgentMock).toHaveBeenCalledTimes(1);
  });

  it("returns 500 when the run fails", async () => {
    runAgentMock.mockResolvedValue({ runId: 9, status: "failed", steps: 8 });
    const res = await POST(postRequest({ question: "loop" }));
    expect(res.status).toBe(500);
    await expect(res.json()).resolves.toMatchObject({ runId: 9, status: "failed" });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/agent-route.test.ts`
Expected: FAIL — cannot find module `@/app/api/agent/route`.

- [ ] **Step 3: Write `src/app/api/agent/route.ts`**

```ts
import { NextResponse } from "next/server";
import { runAgent } from "@/lib/harness";
import { echoTool } from "@/lib/tools/echo";
import { createToolRegistry } from "@/lib/tools/registry";

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const question = (body as { question?: unknown } | null)?.question;
  if (typeof question !== "string" || !question.trim()) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }

  const registry = createToolRegistry([echoTool]);
  const result = await runAgent({ question, registry });
  return NextResponse.json(result, { status: result.status === "succeeded" ? 200 : 500 });
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/agent-route.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/app/api/agent/route.ts tests/agent-route.test.ts
git commit -m "feat(f5): POST /api/agent trigger for the harness"
```

---

### Task 5: Full verification + plan checkbox update

**Files:**
- Modify: `docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md` (check off F5 tasks/criteria)

- [ ] **Step 1: Run the full test suite**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run`
Expected: PASS — all prior suites plus the 4 new F5 files (tool-registry, echo-tool, harness, agent-route) green.

- [ ] **Step 2: Lint**

Run: `npm run lint`
Expected: `✔ No ESLint warnings or errors`.

- [ ] **Step 3: Build**

Run: `npm run build`
Expected: build succeeds; route list includes `ƒ /api/agent` alongside the existing routes.

- [ ] **Step 4: Manual end-to-end smoke (optional but recommended)**

Run in one terminal: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npm run dev`
Then: `curl -s -X POST localhost:3000/api/agent -H 'content-type: application/json' -d '{"question":"echo hello"}'`
Expected: JSON with a `runId`. Open `http://localhost:3000/runs/<runId>` and confirm the trace shows the agent step with model + echo tool children. (Note: without a real `DEEPSEEK_API_KEY`, the live model call fails and the run is recorded as `failed` — still inspectable. The mocked tests are the source of truth for behavior.)

- [ ] **Step 5: Check off F5 in the task breakdown**

In `docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md`, mark the F5 acceptance criteria and tasks F5.1/F5.2/F5.3 as `[x]`.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md
git commit -m "docs(f5): mark F5 agent harness complete"
```

---

## Self-Review

**Spec coverage:**
- Tool contract + registry (spec §Components) → Task 1.
- echo tool (spec §echo) → Task 2.
- ReAct loop, decision schema, executeToolCall permission/validation/ledger table (spec §The loop, §executeToolCall) → Task 3.
- Thin trigger (spec §Trigger) → Task 4.
- Existing inspector reused unchanged (spec §UI scope) → no task needed; verified in Task 5 step 4.
- Acceptance criteria #1 (multi-step loop via gateway + tool) → Task 3 happy-path test. #2 (refused + logged) → Task 3 refusal test. #3 (full trace in inspector) → Task 3 trace asserts + Task 5 smoke.
- Loop guardrails (final/cap/error-feedback/no-retries) → Task 3 tests (happy, max-steps, tool-error, invalid-args, refusal).

**Placeholder scan:** No TBD/TODO; every code and test step contains full content.

**Type consistency:** `runAgent`/`RunAgentInput`/`RunAgentResult`, `Tool`/`ToolRegistry`/`createToolRegistry`, `agentDecisionSchema`/`AgentDecision`, `echoTool`, and the ledger/gateway signatures are used identically across Tasks 1–4. The gateway creates a child `model` step per `callModel`, so Task 3's trace assertions look for model calls on `model`-kind children of the agent step (not on the agent step itself).
