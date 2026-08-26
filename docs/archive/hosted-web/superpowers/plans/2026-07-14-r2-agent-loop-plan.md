# R2 — Hand-rolled Agent Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `generate_object`-based ReAct decision loop in `src/lib/harness.ts` with a native tool-calling loop (`src/lib/agent-loop.ts`) that streams turns through `streamAgentTurn`, threads native `tool_use`/`tool_result` blocks through `messages[]`, and never throws mid-run — while `runAgent`'s public signature survives so `/api/agent` and `/api/agent/stream` callers don't churn.

**Depends on:** R1 (provider adapter layer) — **must already be merged**. This plan assumes `src/lib/llm/types.ts` exports `LlmMessage`, `LlmStreamEvent`, `LlmAdapter`, `LlmToolDefinition`, `LlmToolResultBlock`, `StopReason` exactly as specified in `docs/superpowers/plans/2026-07-14-r-contracts-brief.md` §1, and `src/lib/model-gateway.ts` exports `streamAgentTurn(db, input): AsyncGenerator<LlmStreamEvent, LlmTurnOutcome, void>` and `LlmTurnOutcome` exactly as specified in §2. If either is missing or shaped differently, stop and flag it — do not re-derive or stub them.

**Downstream (not this plan's job, but shapes this plan must not block):** R3 lifts the tool-execution helper this plan adds to `agent-loop.ts` into `src/lib/tools/orchestrator.ts` (see Judgment call #1). R4 replaces the default system-message fallback by passing a fully-assembled prompt through the pre-existing `instructions` field (see Judgment call #2) — R4 does not need to touch `harness.ts` again.

**Architecture:** `runAgentLoop()` is a `for` loop (max `maxSteps`, default 8) over a growing `messages[]` array. Each iteration calls `streamAgentTurn`, drains its async generator manually (to capture the typed generator `return` value — a plain `for await...of` would silently drop it), appends the resulting assistant message to `messages[]`, and either stops (`stopReason !== "tool_use"`) or executes every `tool_use` block in that turn and appends one bundled `tool_result` message. A `streamAgentTurn` throw (model error) or a pre-fired `AbortSignal` never propagates out of `runAgentLoop` — both become a synthetic assistant text block and a `"failed"` result. `harness.ts`'s `runAgent()` becomes a thin wrapper: build the initial `messages[]`, call `runAgentLoop`, translate its `onEvent` stream into the legacy `onStep` callback, and do the existing `startRun`/`finishRun`/`failRun` bookkeeping — byte-for-byte the same outer try/catch safety net that exists today.

**Tech Stack:** TypeScript, Zod v4 (`z.toJSONSchema`), `postgres` (`Sql`), Vitest against live Postgres (same conventions as the existing harness test suite).

## Global Constraints

- **Zero `JSON.parse` of model-produced arg strings.** `LlmToolUseBlock.input` arrives already-parsed; nothing in `agent-loop.ts` or `harness.ts` decodes a JSON string for tool args. (Acceptance criterion #2.)
- **The loop never throws mid-run.** Every documented error path (model error, tool error, abort, maxSteps) resolves `runAgentLoop`'s promise with `status: "failed"`; it does not reject except for a genuinely unexpected bug (e.g. a ledger write failing outside the documented tool-error path), which `harness.ts`'s outer try/catch still absorbs exactly as it does today.
- **Ledger discipline unchanged.** `runAgentLoop` writes nothing to `runs`/`run_steps` directly — `streamAgentTurn` (R1) owns each turn's `model` child step + `model_calls` row; this plan's tool-execution helper owns each tool's `tool` child step + `tool_calls` row, using the existing `src/lib/ledger.ts` functions unchanged. **`src/lib/ledger.ts` needs no code changes for this slice** — every function this plan calls (`startRunStep`, `finishRunStep`, `failRunStep`, `startToolCall`, `finishToolCall`, `failToolCall`, `startRun`, `finishRun`, `failRun`) already exists with the right signature.
- **Tool result truncation:** `TOOL_RESULT_MAX_CHARS = 16_000`. Content (success or error) over that length is truncated with an appended `` `\n\n[truncated ${overflow} chars]` `` notice.
- **Tool result string formats are back-compat-load-bearing.** `src/lib/memory/answer.ts::describeObservation` (untouched by this slice) strips a literal `"Success: "` prefix and JSON.parses the remainder for `search_memory` results, and strips `/^Error \([^)]*\):\s*/` for failures. The tool-execution helper must keep emitting `` `Success: ${JSON.stringify(result)}` `` and `` `Error (${errorType}): ${message}` `` exactly as today's `harness.ts` does.
- **Default permission set / step cap unchanged:** `new Set(["read", "reason"])`, `maxSteps = 8`.
- **No app-level retries** — transport retries, if any, live in the R1 adapters.
- TDD: every behavior gets a failing test first. DB tests reset ledger tables + `runMigrations` in `beforeEach` (same `resetLedgerTables` pattern already duplicated across `tests/harness*.test.ts`) and inject a fake `LlmAdapter` (no real model API). Run tests with `DATABASE_URL` pointing at the local Postgres (`postgresql://sourcecado:sourcecado@localhost:5432/sourcecado`, container `sourcecado-db-1`).

---

## Judgment calls

The contracts brief and spec leave two things unresolved that this plan must decide to be buildable in isolation (R2 lands before R3/R4 per the dependency graph, but its narrative already assumes both exist). Leanest calls, made explicit here per the brief's own rule ("don't silently diverge"):

1. **Where `executeTool`/`toLlmToolDefinition` live during R2.** The contracts brief's loop-body narrative (§3) already calls `executeTool()` and needs `LlmToolDefinition`s, but both are §4 shapes owned by `src/lib/tools/orchestrator.ts` — an **R3** file that doesn't exist yet, and R3 depends on R2 landing first (dependency graph: `R2 → R3`). Resolution: this plan implements the choke-point logic (validate → permission gate → execute → ledger log → truncate) and the JSON-Schema mapper as **private, non-exported functions inside `agent-loop.ts`** (`executeToolUseBlock`, `toLlmToolDefinition`, `truncate`), using the exact field names and error-code strings from brief §4 (`ToolExecutionResult`, `unknown_tool`/`permission_denied`/`invalid_args`/`tool_error`) so R3's job is a mechanical cut-and-paste into `orchestrator.ts` plus an import-path fix in `agent-loop.ts` — no behavior change. `ToolExecutionResult` is exported from `agent-loop.ts` for now (consumed by `AgentLoopEvent`'s `tool_end` variant); R3 will re-export it from `orchestrator.ts` instead and delete the local copy.
2. **How the system message is built before `context.ts` exists.** Brief §3 says the thin wrapper builds `messages[0]` "via `buildSystemPrompt` (§5)" — but `context.ts` is an **R4** file (`R2 → R4`), so it can't exist yet either. Resolution: `harness.ts` builds the system message as `input.instructions ?? DEFAULT_IDENTITY` (a one-line generic fallback) — the tool catalog no longer needs to be prose (tools travel via the native `tools:` param now, not the system prompt). R4 will pass its fully-assembled sectioned prompt through the **already-existing** `instructions` field — no further change to `harness.ts` is needed when R4 lands.
3. **Synthetic abort-message copy.** Brief §3 gives the model-error copy (`"[model error: <message>]"`) but not the abort one. Call: `"[aborted]"` (no message — an `AbortSignal` firing rarely carries a useful reason string across environments).
4. **`AgentStepEvent.thought` under native tool calling.** The old `thought` came from a structured-output field that no longer exists. Call: the wrapper accumulates `text_delta`/`thinking_delta` content since the last flush and attaches it as `thought` to the *next* `tool_end`'s collapsed `AgentStepEvent`, then clears the buffer. If one turn issues multiple `tool_use` blocks, only the first gets the accumulated thought; later ones in the same turn get `undefined`. Acceptable — matches today's one-tool-per-turn common case and degrades gracefully for the rare multi-tool turn.
5. **`AgentLoopResult.stopReason` when `maxSteps` is exhausted.** `StopReason` has no `"max_steps"` literal. Call: report the last turn's real `stopReason` (always `"tool_use"`, since `maxSteps` is only reached while the model keeps calling tools) — `harness.ts` maps this to `errorType: "max_steps_exceeded"` for the ledger, same as today.
6. **`RunAgentResult` gains an additive `messages: LlmMessage[]` field**, sourced from `AgentLoopResult.messages` and passed through unchanged. Brief §3 calls `RunAgentResult` "byte-for-byte" unchanged, but R6 (chat-session persistence) needs the full produced transcript to persist, and the only place it exists today is `AgentLoopResult` inside this thin wrapper — nothing past `harness.ts` sees it otherwise. Purely additive (existing `runId`/`status`/`answer`/`steps` consumers at `/api/agent` and `/api/agent/stream` are unaffected), so this doesn't break the "byte-for-byte" intent for any existing caller — same category of deviation as R5's additive `onAgentLoopEvent`, flagged here per the brief's own escape hatch.

---

### Task 1: `agent-loop.ts` — types + natural-stop path

**Files:**
- Create: `src/lib/agent-loop.ts`
- Test: `tests/agent-loop.test.ts`

**Interfaces (produced):**
```ts
export interface ToolExecutionResult { content: string; isError: boolean }
export interface AgentLoopInput {
  messages: LlmMessage[];
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  maxSteps?: number;
  db: Sql;
  runId: number;
  parentStepId: number;
  provider?: string;
  adapter?: LlmAdapter;
  signal?: AbortSignal;
  onEvent?: (event: AgentLoopEvent) => void | Promise<void>;
}
export type AgentLoopEvent =
  | { type: "llm"; event: LlmStreamEvent }
  | { type: "tool_start"; id: string; name: string; input: unknown }
  | { type: "tool_end"; id: string; name: string; result: ToolExecutionResult };
export interface AgentLoopResult {
  status: "succeeded" | "failed";
  messages: LlmMessage[];
  finalText?: string;
  stopReason: StopReason;
  steps: number;
}
export async function runAgentLoop(input: AgentLoopInput): Promise<AgentLoopResult>
```

- [x] **Step 1: Write the failing natural-stop test**

Create `tests/agent-loop.test.ts`:

```ts
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
```

- [x] **Step 2: Run the test to verify it fails**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/agent-loop.test.ts`
Expected: FAIL — cannot find module `@/lib/agent-loop`.

- [x] **Step 3: Write `src/lib/agent-loop.ts`**

```ts
import { z } from "zod";
import {
  failRunStep,
  failToolCall,
  finishRunStep,
  finishToolCall,
  startRunStep,
  startToolCall,
} from "./ledger";
import { streamAgentTurn, type LlmTurnOutcome } from "./model-gateway";
import type {
  LlmAdapter,
  LlmMessage,
  LlmStreamEvent,
  LlmToolDefinition,
  LlmToolResultBlock,
  StopReason,
} from "./llm/types";
import type { ToolRegistry } from "./tools/registry";
import type { PermissionClass, Sql, Tool } from "./tools/types";

const DEFAULT_MAX_STEPS = 8;
const TOOL_RESULT_MAX_CHARS = 16_000;

export interface ToolExecutionResult {
  content: string;
  isError: boolean;
}

export interface AgentLoopInput {
  messages: LlmMessage[];
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  maxSteps?: number;
  db: Sql;
  runId: number;
  parentStepId: number;
  provider?: string;
  adapter?: LlmAdapter;
  signal?: AbortSignal;
  onEvent?: (event: AgentLoopEvent) => void | Promise<void>;
}

export type AgentLoopEvent =
  | { type: "llm"; event: LlmStreamEvent }
  | { type: "tool_start"; id: string; name: string; input: unknown }
  | { type: "tool_end"; id: string; name: string; result: ToolExecutionResult };

export interface AgentLoopResult {
  status: "succeeded" | "failed";
  messages: LlmMessage[];
  finalText?: string;
  stopReason: StopReason;
  steps: number;
}

export async function runAgentLoop(input: AgentLoopInput): Promise<AgentLoopResult> {
  const maxSteps = input.maxSteps ?? DEFAULT_MAX_STEPS;
  const messages = [...input.messages];
  const tools = input.registry.list(input.allowed).map(toLlmToolDefinition);
  let lastStopReason: StopReason = "tool_use";

  for (let step = 1; step <= maxSteps; step++) {
    if (input.signal?.aborted) {
      messages.push(syntheticAssistantMessage("[aborted]"));
      return { status: "failed", messages, stopReason: "aborted", steps: step };
    }

    let outcome: LlmTurnOutcome;
    try {
      const gen = streamAgentTurn(input.db, {
        taskName: "agent_loop_turn",
        promptVersion: "1",
        providerName: input.provider,
        messages,
        tools,
        trace: { runId: input.runId, parentStepId: input.parentStepId },
        adapter: input.adapter,
        signal: input.signal,
      });
      outcome = await drain(gen, input.onEvent);
    } catch (error) {
      const aborted = input.signal?.aborted === true;
      const message = error instanceof Error ? error.message : String(error);
      messages.push(syntheticAssistantMessage(aborted ? "[aborted]" : `[model error: ${message}]`));
      return { status: "failed", messages, stopReason: aborted ? "aborted" : "error", steps: step };
    }

    messages.push(outcome.message);
    lastStopReason = outcome.stopReason;

    if (outcome.stopReason === "end") {
      const finalText = outcome.message.content
        .filter((block): block is Extract<(typeof outcome.message.content)[number], { type: "text" }> => block.type === "text")
        .map((block) => block.text)
        .join("");
      return { status: "succeeded", messages, finalText, stopReason: "end", steps: step };
    }

    if (outcome.stopReason !== "tool_use") {
      // "max_tokens" or "error" surfaced as a normal turn outcome (not a throw).
      return { status: "failed", messages, stopReason: outcome.stopReason, steps: step };
    }

    const toolUseBlocks = outcome.message.content.filter(
      (block): block is Extract<(typeof outcome.message.content)[number], { type: "tool_use" }> =>
        block.type === "tool_use"
    );
    const resultBlocks: LlmToolResultBlock[] = [];
    for (const block of toolUseBlocks) {
      await input.onEvent?.({ type: "tool_start", id: block.id, name: block.name, input: block.input });
      const result = await executeToolUseBlock({
        name: block.name,
        input: block.input,
        registry: input.registry,
        allowed: input.allowed,
        db: input.db,
        runId: input.runId,
        parentStepId: input.parentStepId,
      });
      await input.onEvent?.({ type: "tool_end", id: block.id, name: block.name, result });
      resultBlocks.push({
        toolUseId: block.id,
        toolName: block.name,
        content: result.content,
        isError: result.isError,
      });
    }
    messages.push({ role: "tool_result", content: resultBlocks });
  }

  return { status: "failed", messages, stopReason: lastStopReason, steps: maxSteps };
}

async function drain(
  gen: AsyncGenerator<LlmStreamEvent, LlmTurnOutcome, void>,
  onEvent?: (event: AgentLoopEvent) => void | Promise<void>
): Promise<LlmTurnOutcome> {
  let cur = await gen.next();
  while (!cur.done) {
    await onEvent?.({ type: "llm", event: cur.value });
    cur = await gen.next();
  }
  return cur.value;
}

function syntheticAssistantMessage(text: string): LlmMessage {
  return { role: "assistant", content: [{ type: "text", text }] };
}

function toLlmToolDefinition(tool: Tool): LlmToolDefinition {
  let inputSchema: unknown = {};
  try {
    inputSchema = z.toJSONSchema(tool.argsSchema);
  } catch {
    inputSchema = {};
  }
  return { name: tool.name, description: tool.description, inputSchema };
}

// --- Internal tool execution -------------------------------------------------
// Temporary home (Judgment call #1): R3 lifts this verbatim into
// src/lib/tools/orchestrator.ts as `executeTool`/`toLlmToolDefinition`, then
// updates the imports above instead of the local definitions.

interface ExecuteToolUseBlockInput {
  name: string;
  input: unknown;
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  db: Sql;
  runId: number;
  parentStepId: number;
}

async function executeToolUseBlock(opts: ExecuteToolUseBlockInput): Promise<ToolExecutionResult> {
  const { name, input, registry, allowed, db, runId, parentStepId } = opts;
  const tool = registry.get(name);

  const toolStep = await startRunStep(db, {
    runId,
    parentStepId,
    stepKind: "tool",
    name,
    input: { args: input },
  });
  const toolCall = await startToolCall(db, {
    runId,
    runStepId: toolStep.id,
    toolName: name,
    arguments: input,
    metadata: { permissionClass: tool?.permissionClass ?? null },
  });

  const fail = async (errorType: string, message: string): Promise<ToolExecutionResult> => {
    await failToolCall(db, { toolCallId: toolCall.id, errorType, errorMessage: message });
    await failRunStep(db, { runStepId: toolStep.id, errorType, errorMessage: message });
    return truncate(`Error (${errorType}): ${message}`, true);
  };

  if (!tool) {
    return fail("unknown_tool", `Unknown tool: ${name}.`);
  }
  if (!allowed.has(tool.permissionClass)) {
    return fail(
      "permission_denied",
      `Tool ${name} (class ${tool.permissionClass}) is not permitted for this run.`
    );
  }
  const parsed = tool.argsSchema.safeParse(input);
  if (!parsed.success) {
    return fail("invalid_args", `Invalid arguments for ${name}: ${parsed.error.message}`);
  }

  try {
    const result = await tool.execute(parsed.data, { db, runId, parentStepId: toolStep.id });
    await finishToolCall(db, { toolCallId: toolCall.id, result });
    await finishRunStep(db, { runStepId: toolStep.id, output: result });
    return truncate(`Success: ${JSON.stringify(result)}`, false);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return fail("tool_error", `Tool ${name} failed: ${message}`);
  }
}

function truncate(content: string, isError: boolean): ToolExecutionResult {
  if (content.length <= TOOL_RESULT_MAX_CHARS) {
    return { content, isError };
  }
  const overflow = content.length - TOOL_RESULT_MAX_CHARS;
  return {
    content: `${content.slice(0, TOOL_RESULT_MAX_CHARS)}\n\n[truncated ${overflow} chars]`,
    isError,
  };
}
```

- [x] **Step 4: Run the test to verify it passes**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/agent-loop.test.ts`
Expected: PASS (1 test).

- [x] **Step 5: Commit**

```bash
git add src/lib/agent-loop.ts tests/agent-loop.test.ts
git commit -m "feat(r2): agent-loop natural-stop path over streamAgentTurn"
```

---

### Task 2: `agent-loop.ts` — tool round-trip, permission gate, validation, truncation

**Files:**
- Modify: `src/lib/agent-loop.ts` (no changes expected — this task is pure test coverage of Task 1's implementation; if any test fails, fix the implementation, don't weaken the test)
- Modify: `tests/agent-loop.test.ts`

- [x] **Step 1: Add the failing tests**

Append inside `describe("runAgentLoop", ...)` in `tests/agent-loop.test.ts`:

```ts
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
```

Also add the two missing imports at the top of `tests/agent-loop.test.ts`:

```ts
import { z } from "zod";
import type { Tool } from "@/lib/tools/types";
```

- [x] **Step 2: Run the tests to verify each new one passes against the Task 1 implementation**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/agent-loop.test.ts`
Expected: PASS (7 tests). The Task 1 implementation already contains this logic — if anything fails, fix `agent-loop.ts`, not the test.

- [x] **Step 3: Commit**

```bash
git add src/lib/agent-loop.ts tests/agent-loop.test.ts
git commit -m "test(r2): agent-loop tool round-trip, permission/validation/truncation coverage"
```

---

### Task 3: `agent-loop.ts` — maxSteps, abort, model-error synthetic messages

**Files:**
- Modify: `tests/agent-loop.test.ts`

- [x] **Step 1: Add the failing tests**

Append inside the same `describe` block:

```ts
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
```

- [x] **Step 2: Run to verify pass**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/agent-loop.test.ts`
Expected: PASS (12 tests total).

- [x] **Step 3: Commit**

```bash
git add tests/agent-loop.test.ts
git commit -m "test(r2): agent-loop maxSteps/abort/model-error stop conditions"
```

---

### Task 4: `harness.ts` rewritten as a thin wrapper

**Files:**
- Rewrite: `src/lib/harness.ts`
- Rewrite: `tests/harness.test.ts`

**Interfaces (produced, replacing the deleted `agentDecisionSchema`/`buildAgentSystemPrompt`/`buildUserPrompt`/inline tool-exec helpers):**
```ts
export interface ConversationTurn { role: "user" | "assistant"; content: string }
export interface AgentStepEvent { index: number; tool: string; thought?: string; observation: string; ok: boolean }
export interface RunAgentInput {
  question: string; registry: ToolRegistry; allowedClasses?: Set<PermissionClass>; maxSteps?: number;
  db?: Sql; instructions?: string; history?: ConversationTurn[];
  onStep?: (event: AgentStepEvent) => void | Promise<void>;
  providerName?: string; adapter?: LlmAdapter; signal?: AbortSignal;
}
export interface RunAgentResult { runId: number; status: "succeeded" | "failed"; answer?: string; steps: number; messages: LlmMessage[] }
export function conversationTurnsToMessages(history?: ConversationTurn[]): LlmMessage[]
export async function runAgent(input: RunAgentInput): Promise<RunAgentResult>
```

- [x] **Step 1: Rewrite `tests/harness.test.ts`**

```ts
import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { getRunTrace } from "@/lib/ledger";
import { runMigrations } from "@/lib/migrate";
import { runAgent } from "@/lib/harness";
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
});
```

- [x] **Step 2: Run to verify it fails**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/harness.test.ts`
Expected: FAIL — `runAgent` still expects the old `provider`/`generate_object` shape (compile or assertion failures).

- [x] **Step 3: Rewrite `src/lib/harness.ts`**

```ts
import { getDb } from "./db";
import { failRun, failRunStep, finishRun, finishRunStep, startRun, startRunStep } from "./ledger";
import { ModelGatewayError } from "./model-gateway";
import { runAgentLoop, type AgentLoopEvent } from "./agent-loop";
import type { LlmAdapter, LlmMessage } from "./llm/types";
import type { ToolRegistry } from "./tools/registry";
import type { PermissionClass, Sql } from "./tools/types";

export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

// Emitted after each executed tool step (never for the final answer). Carries the
// model's rationale, the tool, and the observation so a streaming UI can render the
// agent's live reasoning trace.
export interface AgentStepEvent {
  index: number;
  tool: string;
  thought?: string;
  observation: string;
  ok: boolean;
}

export interface RunAgentInput {
  question: string;
  registry: ToolRegistry;
  allowedClasses?: Set<PermissionClass>;
  maxSteps?: number;
  db?: Sql;
  instructions?: string;
  // Prior conversation turns for multi-turn chat. Threaded into messages[] ahead
  // of the current question, capped server-side in conversationTurnsToMessages.
  history?: ConversationTurn[];
  // Invoked after each executed tool step. Awaited so a streaming consumer can
  // flush the step to the client before the next turn runs.
  onStep?: (event: AgentStepEvent) => void | Promise<void>;
  providerName?: string;
  // Test seam: injected LlmAdapter, forwarded to streamAgentTurn. Mirrors the old
  // `provider` seam's purpose for the new native tool-calling loop.
  adapter?: LlmAdapter;
  signal?: AbortSignal;
}

export interface RunAgentResult {
  runId: number;
  status: "succeeded" | "failed";
  answer?: string;
  steps: number;
  // Additive: the full transcript produced by this run (AgentLoopResult.messages),
  // for R6's chat-session persistence. Not consumed by /api/agent or /api/agent/stream.
  messages: LlmMessage[];
}

const DEFAULT_ALLOWED: PermissionClass[] = ["read", "reason"];
const DEFAULT_MAX_STEPS = 8;
// Fallback system message when no `instructions` is supplied. R4's context
// assembly passes its full sectioned prompt through `instructions` instead of
// this repo needing another call site change.
const DEFAULT_IDENTITY = "You are a sourcing agent. Use the available tools to answer accurately.";
const MAX_HISTORY_TURNS = 12;
const MAX_TURN_CHARS = 4000;

export function conversationTurnsToMessages(history: ConversationTurn[] = []): LlmMessage[] {
  return history.slice(-MAX_HISTORY_TURNS).map((turn) =>
    turn.role === "user"
      ? { role: "user", content: turn.content.slice(0, MAX_TURN_CHARS) }
      : { role: "assistant", content: [{ type: "text", text: turn.content.slice(0, MAX_TURN_CHARS) }] }
  );
}

export async function runAgent(input: RunAgentInput): Promise<RunAgentResult> {
  const db = input.db ?? getDb();
  const allowed = input.allowedClasses ?? new Set(DEFAULT_ALLOWED);
  const maxSteps = input.maxSteps ?? DEFAULT_MAX_STEPS;

  let run: Awaited<ReturnType<typeof startRun>> | null = null;
  let agentStep: Awaited<ReturnType<typeof startRunStep>> | null = null;

  try {
    run = await startRun(db, {
      runType: "agent_chat",
      title: input.question.slice(0, 80),
      input: { question: input.question },
    });
    agentStep = await startRunStep(db, {
      runId: run.id,
      stepKind: "agent",
      name: "agent_loop",
      input: { question: input.question },
    });

    const messages: LlmMessage[] = [
      { role: "system", content: input.instructions ?? DEFAULT_IDENTITY },
      ...conversationTurnsToMessages(input.history),
      { role: "user", content: input.question },
    ];

    let stepCounter = 0;
    let thoughtBuffer = "";
    const onEvent = input.onStep
      ? async (event: AgentLoopEvent): Promise<void> => {
          if (event.type === "llm" && (event.event.type === "text_delta" || event.event.type === "thinking_delta")) {
            thoughtBuffer += event.event.delta;
            return;
          }
          if (event.type === "tool_end") {
            stepCounter += 1;
            const thought = thoughtBuffer.trim() || undefined;
            thoughtBuffer = "";
            await input.onStep?.({
              index: stepCounter,
              tool: event.name,
              thought,
              observation: event.result.content,
              ok: !event.result.isError,
            });
          }
        }
      : undefined;

    const result = await runAgentLoop({
      messages,
      registry: input.registry,
      allowed,
      maxSteps,
      db,
      runId: run.id,
      parentStepId: agentStep.id,
      provider: input.providerName,
      adapter: input.adapter,
      signal: input.signal,
      onEvent,
    });

    if (result.status === "succeeded") {
      await finishRunStep(db, {
        runStepId: agentStep.id,
        output: { answer: result.finalText, steps: result.steps },
      });
      await finishRun(db, { runId: run.id, output: { answer: result.finalText, steps: result.steps } });
      return { runId: run.id, status: "succeeded", answer: result.finalText, steps: result.steps, messages: result.messages };
    }

    const { errorType, errorMessage } = describeLoopFailure(result.stopReason, maxSteps);
    await failRunStep(db, { runStepId: agentStep.id, errorType, errorMessage });
    await failRun(db, { runId: run.id, errorType, errorMessage });
    return { runId: run.id, status: "failed", steps: result.steps, messages: result.messages };
  } catch (error) {
    const code = error instanceof ModelGatewayError ? error.code : "harness_error";
    const message = error instanceof Error ? error.message : String(error);
    if (agentStep) {
      await failRunStep(db, { runStepId: agentStep.id, errorType: code, errorMessage: message });
    }
    if (run) {
      await failRun(db, { runId: run.id, errorType: code, errorMessage: message });
    }
    // Loop never ran (or threw before returning AgentLoopResult) — nothing produced.
    return { runId: run?.id ?? 0, status: "failed", steps: 0, messages: [] };
  }
}

function describeLoopFailure(
  stopReason: string,
  maxSteps: number
): { errorType: string; errorMessage: string } {
  if (stopReason === "aborted") {
    return { errorType: "aborted", errorMessage: "Agent run was aborted." };
  }
  if (stopReason === "tool_use") {
    return { errorType: "max_steps_exceeded", errorMessage: `Agent did not finish within ${maxSteps} steps.` };
  }
  if (stopReason === "max_tokens") {
    return { errorType: "max_tokens_exceeded", errorMessage: "Agent loop stopped: model hit its max token limit." };
  }
  return { errorType: "model_error", errorMessage: "Agent loop stopped due to a model error." };
}
```

- [x] **Step 4: Run to verify pass**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/harness.test.ts`
Expected: PASS (7 tests).

- [x] **Step 5: Commit**

```bash
git add src/lib/harness.ts tests/harness.test.ts
git commit -m "feat(r2): harness.ts becomes a thin wrapper over runAgentLoop"
```

---

### Task 5: `onStep` translation coverage

**Files:**
- Rewrite: `tests/harness-onstep.test.ts`

- [x] **Step 1: Rewrite the failing/updated test file**

```ts
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
```

- [x] **Step 2: Run to verify pass**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/harness-onstep.test.ts`
Expected: PASS (3 tests).

- [x] **Step 3: Commit**

```bash
git add tests/harness-onstep.test.ts
git commit -m "test(r2): onStep translation over native tool-calling events"
```

---

### Task 6: Retire stale tests for deleted functions

**Files:**
- Delete: `tests/agent-prompt.test.ts` (tested `buildAgentSystemPrompt`, deleted in Task 4 — the tool catalog no longer needs to be prose since tools travel via the native `tools:` param; coverage for the system-message fallback is now in `tests/harness.test.ts`'s "uses `instructions`..." test)
- Modify: `tests/harness-multiturn.test.ts` (replace the `buildUserPrompt` describe block only — the `MEMORY_INSTRUCTIONS` describe block is untouched; that constant is R4's territory, not R2's)
- Modify: `tests/memory-answer.test.ts` (convert the 3 integration tests in the "search_memory agentic flow" describe block from the `provider`/`generate_object` seam to the `adapter` seam — these call `runAgent` directly and break under R2's contract change even though `answer.ts`/`answer-config.ts` themselves are untouched)

- [x] **Step 1: Delete `tests/agent-prompt.test.ts`**

```bash
git rm tests/agent-prompt.test.ts
```

- [x] **Step 2: Rewrite `tests/harness-multiturn.test.ts`**

```ts
import { describe, it, expect } from "vitest";
import { conversationTurnsToMessages } from "@/lib/harness";
import { MEMORY_INSTRUCTIONS } from "@/lib/memory/answer-config";

describe("conversationTurnsToMessages — multi-turn history", () => {
  it("maps user/assistant turns to LlmMessage in order", () => {
    const messages = conversationTurnsToMessages([
      { role: "user", content: "Who is Acme?" },
      { role: "assistant", content: "Acme is a fintech startup." },
    ]);
    expect(messages).toEqual([
      { role: "user", content: "Who is Acme?" },
      { role: "assistant", content: [{ type: "text", text: "Acme is a fintech startup." }] },
    ]);
  });

  it("returns an empty array with no history (back-compat: nothing to thread)", () => {
    expect(conversationTurnsToMessages([])).toEqual([]);
    expect(conversationTurnsToMessages()).toEqual([]);
  });

  it("caps history to the most recent turns server-side, dropping the oldest", () => {
    const many = Array.from({ length: 50 }, (_, i) => ({
      role: "user" as const,
      content: `HISTMARK_${i}`,
    }));
    const messages = conversationTurnsToMessages(many);
    const userContents = messages.filter((m) => m.role === "user").map((m) => (m.role === "user" ? m.content : ""));
    expect(userContents).toContain("HISTMARK_49");
    expect(userContents).not.toContain("HISTMARK_0");
  });

  it("caps a single very long turn so a message cannot grow unbounded", () => {
    const huge = "x".repeat(20000);
    const messages = conversationTurnsToMessages([{ role: "user", content: huge }]);
    const message = messages[0];
    expect(message.role).toBe("user");
    if (message.role === "user") {
      expect(message.content.length).toBeLessThan(huge.length);
    }
  });
});

describe("MEMORY_INSTRUCTIONS — multi-turn citation safety (N3)", () => {
  it("requires calling search_memory every turn so prior-turn citations are not scrubbed", () => {
    expect(MEMORY_INSTRUCTIONS).toMatch(/every turn|each turn|follow-up/i);
  });
});
```

- [x] **Step 3: Run to verify pass**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/harness-multiturn.test.ts`
Expected: PASS (5 tests).

- [x] **Step 4: Convert the 3 integration tests in `tests/memory-answer.test.ts`**

Replace the import block (top of file) — remove `import type { ModelGatewayProvider } from "@/lib/model-gateway";` and add:

```ts
import type { LlmAdapter, LlmStreamEvent } from "@/lib/llm/types";
```

Immediately above `describe("search_memory agentic flow (mock provider + postgres)", ...)` (around line 217), add these shared helpers:

```ts
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
```

In each of the 3 `it(...)` blocks in that describe (`"tool ran and real citation from bundle validates (no invalid)"`, `"invented citation is flagged in post-check"`, `"refuse-on-empty: ..."`):
- Replace the `const provider = vi.fn<ModelGatewayProvider>().mockResolvedValueOnce({ object: { action: "tool", tool: "search_memory", args: '{"query":"..."}' } }).mockResolvedValueOnce({ object: { action: "final", answer: "..." } });` block with:
  ```ts
  const adapter = sequentialAdapter([
    () => toolCallTurn("search_memory", { query: "who responded" }),
    () => finalTurn("<the same answer string already in that test>"),
  ]);
  ```
- Replace the `provider,` line inside the `runAgent({...})` call with `adapter,`.
- Delete the `expect(provider).toHaveBeenCalledTimes(2);` line in the first test (that call-count check is redundant with the existing `modelSteps`/trace-based assertions already in the test; not something the new fake tracks).

Everything else in the file (the `describe("collectAllowedCitations", ...)` and `describe("checkCitations", ...)` blocks above line 213) is untouched.

- [x] **Step 5: Run to verify pass**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/memory-answer.test.ts`
Expected: PASS (all tests in the file, including the 3 converted integration tests).

- [x] **Step 6: Commit**

```bash
git add -A tests/agent-prompt.test.ts tests/harness-multiturn.test.ts tests/memory-answer.test.ts
git commit -m "test(r2): retire tests for the deleted generate_object decision seam"
```

---

### Task 7: Full verification + cleanup pass

**Files:** none (verification only, plus any cleanup found)

- [x] **Step 1: Run the full test suite**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npm test`
Expected: PASS — every existing suite plus `agent-loop.test.ts` green; no file still imports `agentDecisionSchema`/`buildAgentSystemPrompt`/`buildUserPrompt`/`ModelGatewayProvider` from `@/lib/harness` or `@/lib/model-gateway` in a way that fails.

- [x] **Step 2: Grep for orphaned references**

Run: `grep -rn "agentDecisionSchema\|buildAgentSystemPrompt\|buildUserPrompt" src tests`
Expected: no output (all three names fully removed).

- [x] **Step 3: Lint**

Run: `npm run lint`
Expected: `✔ No ESLint warnings or errors`.

- [x] **Step 4: Build**

Run: `npm run build`
Expected: build succeeds; `/api/agent` and `/api/agent/stream` routes still list, since `answerWithMemory`/`runAgent`'s consumer-facing shape (`RunAgentInput.question/registry/allowedClasses/maxSteps/db/instructions/history/onStep`, `RunAgentResult`) is unchanged.

- [x] **Step 5: Self-review pass on this slice's own additions**

Confirm:
- `src/lib/ledger.ts` has zero diff (per Global Constraints, no changes were needed).
- `src/lib/agent-loop.ts` exports only `runAgentLoop`, `AgentLoopInput`, `AgentLoopEvent`, `AgentLoopResult`, `ToolExecutionResult` — no accidental export of `executeToolUseBlock`/`toLlmToolDefinition`/`truncate`/`drain` (these stay private; R3 will lift/export them from `orchestrator.ts`).
- `src/lib/harness.ts` no longer imports anything from `zod` (the `agentDecisionSchema` and JSON-schema-in-prompt logic are gone).
- No leftover `JSON.parse`/`args?.trim()` decode logic anywhere in `src/lib/harness.ts` or `src/lib/agent-loop.ts`.

- [x] **Step 6: Commit (only if Step 5 found something to fix)**

```bash
git add -A
git commit -m "chore(r2): cleanup pass on agent-loop/harness rewrite"
```

---

## Tests

| File | New/Modified | Coverage |
|---|---|---|
| `tests/agent-loop.test.ts` | New | Natural stop; tool round-trip; permission denial; invalid args; tool execution error; truncation; ledger row shape; maxSteps exceeded; model-error-as-synthetic-message; pre-fired-abort-as-synthetic-message; non-throwing `max_tokens`/`error` turn_end as failed; mid-stream abort (throw while signal already aborted) as `"aborted"` not `"error"` (12 tests) |
| `tests/harness.test.ts` | Rewritten | DB-init failure passthrough; multi-step trace via native tool_use; permission refusal; maxSteps failure; tool-error recovery; invalid-args recovery; instructions-vs-default system message (7 tests) |
| `tests/harness-onstep.test.ts` | Rewritten | onStep emitted once per tool step with accumulated thought; ok:false on tool failure; async onStep awaited before the next step (3 tests) |
| `tests/harness-multiturn.test.ts` | Modified | `conversationTurnsToMessages` ordering/back-compat/cap-by-turns/cap-by-chars (4 tests); `MEMORY_INSTRUCTIONS` block untouched (1 test) |
| `tests/memory-answer.test.ts` | Modified (3 of its tests) | Existing citation-checking coverage untouched; 3 `runAgent`-via-`memoryRegistry()` integration tests ported to the `adapter` seam |
| `tests/agent-prompt.test.ts` | Deleted | Superseded — tested a deleted function; its one remaining concern (system message fallback) is now in `tests/harness.test.ts` |

Net new/changed test count for this slice: **+12 new** (`agent-loop.test.ts`), ~17 rewritten in place, 1 file deleted. Full suite must stay green start to finish (acceptance criterion #10).

---

## Self-Review

**Spec coverage:**
- "while over messages[]... stream turn → tool_use → orchestrator → append tool_result → continue; no tool_use → stop" (spec §R2) → Task 1–3, `runAgentLoop`.
- "Stop conditions: natural stop, maxSteps (keep 8), AbortSignal" → Task 1 (natural), Task 3 (maxSteps, abort).
- "Model/tool errors become synthetic in-transcript messages — the loop never throws mid-run" → Task 3 (model error), Task 2 (tool error as `is_error` tool_result, which is the "synthetic in-transcript" form for tool failures specifically).
- "Ledger writes preserved 1:1" → Task 2's ledger-row test + Global Constraints note that `ledger.ts` needed zero changes.
- "harness.ts's runAgent signature survives as a thin wrapper" → Task 4, with the one explicit, documented seam substitution (`provider` → `adapter`/`providerName`) under Judgment call context.
- Acceptance criterion #2 (zero `JSON.parse` of model-produced arg strings) → Global Constraints + verified by Task 7 Step 2's grep and the fact that `LlmToolUseBlock.input` is consumed as-is throughout.
- Acceptance criterion #3 (permission-denied tool call appears as `is_error` tool_result, run continues) → Task 2.

**Placeholder scan:** no TBD/TODO; every code and test step has complete content.

**Type consistency:** `AgentLoopInput`/`AgentLoopEvent`/`AgentLoopResult`/`ToolExecutionResult` match `docs/superpowers/plans/2026-07-14-r-contracts-brief.md` §3 field-for-field except the two explicitly-flagged judgment calls (temporary local ownership of `executeTool`/`toLlmToolDefinition`; default system message). `RunAgentInput`/`RunAgentResult`/`ConversationTurn`/`AgentStepEvent` keep their pre-R2 field names and semantics except `provider?: ModelGatewayProvider` → `adapter?: LlmAdapter` + `providerName?: string` (the only test/production seam that could not survive byte-for-byte, since its whole shape — `generate_object`-style provider function — no longer exists).

---

## Eng Review (2026-07-14)

**Method:** Read this plan end-to-end, the shared contracts brief
(`2026-07-14-r-contracts-brief.md`), and the live repo state it diffs
against: `src/lib/harness.ts` (344 lines, current ReAct implementation),
`src/lib/ledger.ts` (all 15 exported functions the plan calls), `src/lib/tools/{registry,types,echo}.ts`,
`src/lib/memory/{answer,answer-config}.ts`, `src/app/api/agent/{route,stream/route}.ts`,
and all five test files the plan diffs (`tests/harness.test.ts`,
`tests/harness-onstep.test.ts`, `tests/harness-multiturn.test.ts`,
`tests/memory-answer.test.ts`, `tests/agent-prompt.test.ts`), plus the R1/R3/R4
sibling plans for cross-plan consistency on the two judgment calls. Confirmed
`vitest.config.ts` has `globals: true` (the test code's bare `describe`/`it`
usage is valid) and zod is `^4.4.3` (`z.toJSONSchema` exists and is already
used in current `harness.ts:206`).

**Verdict: approve (revised)** — the plan is unusually well-grounded (every
line number, current-file quote, and test diff I checked against the live
tree matched exactly — this is unusual and a good sign), and the two
concrete, cheap test-coverage gaps flagged below have since been closed in
Task 3 (two additional tests appended; test counts and the Tests table
updated to 12).

### Must-fix — RESOLVED

1. **RESOLVED.** No test for `outcome.stopReason` returned as `"max_tokens"`/`"error"` from
   a normal (non-throwing) `turn_end` event.** Task 1's `runAgentLoop` body has
   an explicit branch for this (agent-loop.ts, the `if (outcome.stopReason !==
   "tool_use")` block with the comment `"max_tokens" or "error" surfaced as a
   normal turn outcome (not a throw)`), and `StopReason` in the contracts
   brief legitimately includes both as `turn_end` values a provider can emit
   without ever throwing. But every "error" test in Tasks 1–3 goes through the
   *throw* path (`throwingAdapter` that `throw`s) — none construct a canned
   turn that `yield`s `{ type: "turn_end", stopReason: "max_tokens", ... }` or
   `stopReason: "error"` with no tool_use/text. That branch of the loop is
   currently unverified. Fix: add one test to Task 3 with a turn generator
   that yields only `turn_end` with `stopReason: "max_tokens"` (no
   `tool_call_end`, no `text_delta`) and assert `result.status === "failed"`,
   `result.stopReason === "max_tokens"` — a handful of lines, same shape as
   the existing `finalTurn`/`toolCallTurn` helpers. Closed: see the
   "fails the run when a turn ends with stopReason 'max_tokens' ..." test
   appended to Task 3.
2. **RESOLVED.** No test for mid-stream abort (the `aborted === true` branch inside the
   `catch` in `runAgentLoop`).** Task 3 tests two abort-adjacent cases: signal
   already fired *before* the loop starts (`step 1` bails before calling the
   adapter at all), and a plain throw with no signal at all (goes to the
   `[model error: ...]` branch). Neither exercises the actual branch that
   matters for a real cancel-mid-run UX: `streamAgentTurn` throws **while**
   `input.signal.aborted` is already `true` (a user cancels partway through a
   turn; R1's adapters propagate the underlying abort as a throw, per R1's
   own plan — "Adapters never catch AbortSignal/errors themselves ... any
   thrown error ... propagate[s]"). The `const aborted = input.signal?.aborted
   === true` check inside the `catch` block of `runAgentLoop` is therefore
   currently dead code as far as this test suite proves. Fix: add a test with
   a real `AbortController`, an adapter that calls `controller.abort()` then
   throws (simulating the SDK's abort error arriving mid-stream), asserting
   `result.stopReason === "aborted"` and the synthetic `"[aborted]"` message —
   not just `"[model error: ...]"`. Closed: see the
   "reports stopReason 'aborted' (not 'error') when the adapter throws
   mid-stream ..." test appended to Task 3.

Both were small, additive test-only changes to Task 3 (no implementation code
needed to change — the existing Task 1 code already had correct branches for
both; the gap was purely in coverage). The plan's "## Tests" table and net-new
test count are updated accordingly (10 → 12).

### Notes (non-blocking)

- **Sequential tool execution within a multi-tool-use turn.** The `for (const
  block of toolUseBlocks)` loop in `runAgentLoop` awaits each tool
  sequentially rather than running them concurrently. Correct and simpler,
  but if a provider returns several parallel `tool_use` blocks in one turn
  (Anthropic does this routinely for independent lookups), this adds
  wall-clock latency vs. `Promise.all`. Judgment call #4 already flags the
  single-tool-per-turn case as the common path and accepts the degraded
  multi-tool case for `thought` attribution — the same "acceptable for v1,
  revisit if it matters" framing applies here. Not worth the added
  complexity (partial-failure ordering, ledger-write interleaving) for this
  slice; flagging only so it's a known, named tradeoff rather than an
  unnoticed one.
- **Abort maps to `failRun`/`failRunStep` with `errorType: "aborted"`, not the
  `RunStatus: "cancelled"` value that already exists in `ledger.ts`'s type.**
  `ledger.ts` has no `cancelRun`/`cancelRunStep` function — only
  `start`/`finish`/`fail` — so `"cancelled"` is presently an unreachable
  literal anywhere in the codebase. Using `failRun` for an aborted run is the
  only option available under the plan's own hard constraint ("`ledger.ts`
  needs no code changes for this slice"), and abort support is a genuinely
  new capability this plan adds (today's `harness.ts` has no `signal` at
  all) — so this isn't a regression, just a judgment call that isn't
  currently listed among the plan's numbered Judgment calls 1–6. Worth a
  one-line addition there for the record, not a behavior change.
- **R1 dependency confirmed not yet merged.** `src/lib/llm/types.ts` does not
  exist and `model-gateway.ts` (534 lines) has no `streamAgentTurn` export in
  the current tree — exactly what the plan's own opening paragraph says to
  expect ("This plan assumes ... If either is missing or shaped differently,
  stop and flag it"). Not a defect in this plan; confirming the precondition
  is honestly stated and currently unmet, so this plan is not yet
  executable as-is until R1 lands. Cross-checked R1's plan
  (`2026-07-14-r1-provider-adapters-plan.md`) and confirmed `streamAgentTurn`'s
  throw-on-error/abort behavior there matches exactly what R2's `catch` block
  assumes.
- **Judgment calls #1 and #2 are consistent with their downstream plans.**
  Checked R3's plan (`2026-07-14-r3-tool-orchestrator-plan.md`) — it correctly
  expects to lift `executeToolUseBlock`/`toLlmToolDefinition` out of
  `agent-loop.ts` verbatim, matching judgment call #1's "R3's job is a
  mechanical cut-and-paste" claim. Checked R4's plan
  (`2026-07-14-r4-context-memory-plan.md`) — it correctly expects to pass its
  assembled prompt through `RunAgentInput.instructions`, matching judgment
  call #2. Also verified against the current `answerWithMemory`/
  `MEMORY_INSTRUCTIONS` call site (`src/lib/memory/answer.ts`,
  `answer-config.ts`): `MEMORY_INSTRUCTIONS` already opens with "You are a
  memory-grounded sourcing assistant," so judgment call #2's behavior change
  (system message becomes `instructions` verbatim, dropping the old
  "You are a sourcing agent... Available tools:" prose wrapper) doesn't
  silently strip identity framing for the one real caller that passes
  `instructions` today.
- **Step-0 file-count smell (informational only, not re-litigated per the
  scope-gate rule):** the slice touches ~8 files (1 new source file, 1
  rewritten source file, 1 new test file, 4 rewritten/modified test files, 1
  deleted test file) — at the plan's own "8 files or 2+ new
  classes/services" complexity-check threshold. Given every test file
  touched is touched *because* it directly exercises the deleted
  `provider`/`generate_object` seam (verified against each file's current
  content — `tests/memory-answer.test.ts:254-354`,
  `tests/harness-multiturn.test.ts:1-3`, `tests/agent-prompt.test.ts` — all
  import/construct the seam being removed), this reads as the actual minimum
  footprint for the rewrite, not scope creep. Noting for the record rather
  than reopening Step 0.

### Verified-accurate plan claims (spot-checked, no issues found)

- `ledger.ts` truly needs zero changes: all 9 functions the plan calls
  (`startRun`, `finishRun`, `failRun`, `startRunStep`, `finishRunStep`,
  `failRunStep`, `startToolCall`, `finishToolCall`, `failToolCall`) exist with
  the exact signatures the plan's code assumes.
- `ToolRegistry.list(allowed)`/`.get(name)`, `Tool`/`PermissionClass`/
  `ToolContext` shapes in `src/lib/tools/{registry,types}.ts` match the plan's
  usage exactly, including the `echoTool` reference fixture.
- The Task 6 test-diff instructions (delete `tests/agent-prompt.test.ts`;
  replace the `buildUserPrompt` describe block in
  `tests/harness-multiturn.test.ts` while leaving the `MEMORY_INSTRUCTIONS`
  block untouched; convert exactly 3 `it(...)` blocks in
  `tests/memory-answer.test.ts` starting at line 217) all match the live file
  contents line-for-line, including the "around line 217" pointer.
- `/api/agent/route.ts` and `/api/agent/stream/route.ts` only import
  `ConversationTurn` from `@/lib/harness` and call `answerWithMemory` (not
  `runAgent` directly) — so the additive `messages: LlmMessage[]` field on
  `RunAgentResult` (judgment call #6) is genuinely non-breaking for both
  existing route consumers, as claimed.

**mustFix (RESOLVED):**
1. RESOLVED — added a Task 3 test exercising `outcome.stopReason ===
   "max_tokens"` returned from a normal `turn_end` event (not a throw),
   asserting `status: "failed"` with that `stopReason`.
2. RESOLVED — added a Task 3 test exercising a mid-stream abort (a real
   `AbortController`, adapter calls `controller.abort()` then throws),
   asserting `stopReason: "aborted"` and the synthetic `"[aborted]"` message,
   distinct from `"[model error: ...]"`.

NO UNRESOLVED DECISIONS
