# R5 Streaming Rewire Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single end-of-run flush in `/api/agent/stream` with a typed re-emission of `LlmStreamEvent`s + tool lifecycle over the existing SSE channel, so the chat UI shows the model's own final-answer text streaming token-by-token (when nothing needs a citation check) and a live "which tool is running" indicator in the reasoning trace — while never relaxing the existing invariant that an invalid citation never reaches the client.

**Depends on:** R0, R1, R2, R3 (transitively — `agent-loop.ts` calls `executeTool()` from R3's orchestrator even though the dependency diagram draws R3 as a sibling feeding R7), R4. Assume all five are merged; this plan diffs against the *resulting* state of `src/lib/llm/types.ts`, `src/lib/agent-loop.ts`, `src/lib/harness.ts` (R2's thin wrapper), `src/lib/context.ts`, `src/lib/memory/citations.ts` (R4's `sinceStepId`) as specified in `docs/superpowers/plans/2026-07-14-r-contracts-brief.md`. If the merged code deviates from the brief in a way that breaks a task's exact snippet below, adapt the edit to the real symbol names/shapes 1:1 — the stated acceptance criteria (behavior) govern, not the literal diff text.

**Primary files:** `src/app/api/agent/stream/route.ts`, `src/lib/ui-message-stream.ts`, `src/app/chat/stream.ts`, `src/app/chat/ChatClient.tsx`, `src/app/chat/ReasoningTrace.tsx`, `src/app/chat/StepRow.tsx`. Two small **flagged deviations** into R2/other-owned files are required and called out explicitly below (Task 1) — see Judgment calls.

## Context

Today, `/api/agent/stream` calls `answerWithMemory` with only an `onStep` callback that fires once per **completed** tool step; the final answer is written as a single `writer.answer(fullText)` flush after `answerWithMemory` returns (post citation-check). This is what the spec calls "per-step flushes" — the reasoning trace updates live, but the answer text always appears in one shot. `runAgentLoop` (R2) natively streams `LlmStreamEvent`s (including `text_delta`) via its `onEvent` hook — this plan wires those events through to the SSE channel so the chat UI can render real token-by-token text for the branch of the acceptance criteria where the memory index already suffices (no `search_memory` call), while preserving the exact one-shot, citation-checked flush for the branch where it doesn't.

R2's contract states `RunAgentInput`/`RunAgentResult`/`ConversationTurn`/`AgentStepEvent`/`onStep` "survive byte-for-byte." To get raw `LlmStreamEvent`s out of the loop for streaming, this plan adds one new **additive, optional** field to `RunAgentInput` (harness.ts, R2-owned) and threads it through `AnswerWithMemoryInput`/`answerWithMemory` (`src/lib/memory/answer.ts`, unclaimed by any R-slice's file-ownership table). Both additions are opt-in — omitting them reproduces today's exact behavior — so this is not a rewrite of R2's contract, just a sibling hook next to the existing one. This deviation is explicitly flagged per the contracts brief's own escape hatch ("If a slice's plan needs to deviate, that deviation must be called out explicitly").

## Judgment calls

1. **New `onAgentLoopEvent` hook on `RunAgentInput`/`AnswerWithMemoryInput`** (deviates from strict R5 file ownership into R2's `harness.ts` and touches the unclaimed `answer.ts`). Leanest way to get raw `LlmStreamEvent`s to the SSE route without duplicating ledger/message-assembly logic that R2/R4 already own. Additive only — existing `onStep`/`AgentStepEvent` shape and behavior are untouched.
2. **Live-stream vs. buffer-and-flush is decided by two independent flags, not "is this the final turn."** We cannot know a turn is the terminal one until its `turn_end` arrives, and by then its tokens are already gone if we didn't forward them live. Instead: (a) `searchCalledSoFar` — true once any `tool_start` named `search_memory` has fired this run (closes the live gate, since only text produced after a search could plausibly cite it) — and (b) `streamedLive` — true once at least one `text_delta` was actually forwarded live. At the end: if `streamedLive && !searchCalledSoFar`, everything (including the true final answer) already streamed verbatim → just close the text part. Otherwise, flush `result.answer` (the authoritative, citation-checked string) as one append-or-fresh write. This means a caller that never wires `onAgentLoopEvent` (e.g. an old test, or a future caller that only wants JSON) reproduces today's exact one-shot-flush behavor with zero code change — verified explicitly in Task 3's tests.
3. **Accepted edge case, not engineered around:** if a turn emits a few tokens of narration before deciding to call `search_memory` in that same turn, those tokens will already have streamed live (gate was still open) and will visibly precede the final flushed answer in the same bubble — no retraction/clearing is implemented. This is judged rare (native tool-use models emit little-to-no preamble before a tool call) and low-impact (worst case: a short line of narration ahead of the real answer, never an unchecked citation, since the citation-bearing text is exactly what still goes through the buffered/flushed path). **Explicit call on the resulting double-surfacing:** the same narration text is *also* captured by R2's `thoughtBuffer` mechanism in `harness.ts` and reappears verbatim as that step's `AgentStepEvent.thought`, which `StepRow.tsx` renders visibly in the reasoning trace. This plan accepts that duplication rather than suppressing it — consistent with the "accept, don't engineer around" posture above, and cheaper than teaching `onAgentLoopEvent`'s consumer to distinguish "already streamed live" text from `thoughtBuffer`'s independent accumulation. This is documented behavior, not a silent gap — verified by a dedicated test in Task 3.
4. **Tool lifecycle in the trace is a single "pending tool name" slot, not per-id reconciliation.** The ReAct loop dispatches one tool call at a time (sequential steps), so there is at most one in-flight tool at any moment — no need to correlate `tool_start`'s string `id` against `AgentStepEvent.index` (a different id space). `AssistantTurn` gains one optional `pendingTool?: string` field, set on `tool_start`, cleared on the next settled `data-step` or on `data-meta`.
5. **`thinking_delta` and `tool_call_delta` (partial-JSON) events are consumed nowhere in R5.** Not required by the spec's acceptance criteria; wiring them into the reasoning trace is a plausible future enhancement, not this slice's job. `onAgentLoopEvent`'s consumer in `route.ts` only branches on `tool_start` and `llm.text_delta`.
6. **`ui-message-stream.ts` keeps using the AI SDK's `createUIMessageStream`/`createUIMessageStreamResponse`.** R8 (AI SDK rip-out) is the last slice and explicitly independent; `ui-message-stream.ts` is already the one file (alongside `model-gateway.ts`) that `tests/model-boundary.test.ts` allows to import `"ai"`. R5 only adds new writer methods inside that same boundary file — no new AI SDK surface anywhere else.

**Open questions for the orchestrator** (see `openQuestions` in the returned structured output).

## Global Constraints

- No `JSON.parse` of model-produced strings anywhere in this plan's code — R5 only forwards already-typed `LlmStreamEvent`/`AgentLoopEvent` values.
- Do not touch `src/lib/agent-loop.ts`, `src/lib/context.ts`, `src/lib/memory/citations.ts`, or `src/lib/memory/answer-config.ts` — read-only references only.
- `src/lib/harness.ts` and `src/lib/memory/answer.ts` get **additive-only** edits (Task 1): one new optional field each, one new line inside an existing closure. No renames, no removed fields, no changed existing behavior.
- `src/lib/ui-message-stream.ts`: retire the old one-shot `answer()` method (its only caller is `route.ts`, rewritten in Task 3) in favor of `answerDelta` / `answerFlush` / `answerEnd` / `toolPending`. Keep `step()` and `meta()` unchanged.
- TDD: every behavior gets a failing test first, per this repo's convention. DB-backed tests reset ledger tables + `runMigrations` in `beforeEach`; route/client tests mock `@/lib/harness`'s `runAgent` (existing pattern in `tests/agent-stream-route.test.ts`) rather than hit Postgres.
- Run tests with `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"`.

---

### Task 1: Additive raw-event passthrough (`harness.ts` + `answer.ts`)

**Files:**
- Modify: `src/lib/harness.ts` (R2-owned — flagged deviation, additive only)
- Modify: `src/lib/memory/answer.ts` (unclaimed by any R-slice — R5 claims this one addition)
- Test: `tests/harness-agent-loop-event.test.ts` (new)
- Test: `tests/memory-answer-agent-loop-event.test.ts` (new)

**What to build:**

In `src/lib/harness.ts`, add one field to `RunAgentInput`, alongside the existing `onStep`:

```ts
import type { AgentLoopEvent } from "./agent-loop";
// ...
export interface RunAgentInput {
  // ...existing fields unchanged...
  onStep?: (event: AgentStepEvent) => void | Promise<void>;
  // Raw agent-loop events (llm text/thinking deltas, tool_start, tool_end),
  // forwarded 1:1 before the existing tool_end→onStep collapse. Optional and
  // additive — omitting it reproduces today's behavior exactly. Consumed by
  // the streaming route (R5) for true token streaming; the JSON /api/agent
  // route and existing tests never set it.
  onAgentLoopEvent?: (event: AgentLoopEvent) => void | Promise<void>;
}
```

Then, restructure the wrapper's `onEvent` construction passed to `runAgentLoop(...)`. R2's real closure is built **conditionally** on `onStep` alone — `const onEvent = input.onStep ? async (event) => {...} : undefined` — and only reacts to `event.type === "llm"` (buffering delta text into a `thoughtBuffer`) and `event.type === "tool_end"` (flushing that buffer into `onStep`'s `AgentStepEvent.thought`); it never inspects `tool_start` at all, so there is no "tool_start/tool_end collapse" to insert into. Build `onEvent` whenever *either* `onStep` **or** `onAgentLoopEvent` is set, and forward the raw event to `onAgentLoopEvent` unconditionally as the **first statement**, before falling into the existing (possibly-absent) `onStep` logic:

```ts
const onEvent = (input.onStep || input.onAgentLoopEvent)
  ? async (event: AgentLoopEvent) => {
      await input.onAgentLoopEvent?.(event);
      if (!input.onStep) return;
      // ...existing thoughtBuffer accumulation + tool_end → onStep flush, unchanged...
    }
  : undefined;
```

In `src/lib/memory/answer.ts`, add the same passthrough field to `AnswerWithMemoryInput` and forward it to `runAgent`:

```ts
import type { AgentLoopEvent } from "@/lib/agent-loop";
// ...
export interface AnswerWithMemoryInput {
  question: string;
  history?: ConversationTurn[];
  onStep?: (event: AgentStepEvent) => void | Promise<void>;
  onAgentLoopEvent?: (event: AgentLoopEvent) => void | Promise<void>;
}

export async function answerWithMemory(db: Sql, input: AnswerWithMemoryInput): Promise<MemoryAnswer> {
  const registry = memoryRegistry();
  const result = await runAgent({
    question: input.question,
    history: input.history,
    registry,
    allowedClasses: new Set(["read"]),
    db,
    onStep: input.onStep,
    onAgentLoopEvent: input.onAgentLoopEvent,
    // ...whatever instructions/context wiring R4 already put here, unchanged...
  });
  // ...rest of the function unchanged...
}
```

- [x] **Step 0: Pre-flight — confirm R2/R1 have actually landed**

Before writing any test, verify the prerequisites this task's edits assume are real on this branch, not just planned:

```bash
test -f src/lib/agent-loop.ts && echo "agent-loop.ts: FOUND" || echo "agent-loop.ts: MISSING"
test -f src/lib/llm/types.ts && echo "llm/types.ts: FOUND" || echo "llm/types.ts: MISSING"
grep -n "onEvent\|RunAgentInput\|onStep" src/lib/harness.ts | head -20
```

- If either `src/lib/agent-loop.ts` or `src/lib/llm/types.ts` is `MISSING`, **stop** — R1/R2 haven't landed on this branch yet. Do not hand-adapt or stub these modules to make Task 1 "work" in isolation; report back that this plan's prerequisites aren't merged rather than proceeding on a guess.
- If both exist, read the actual `RunAgentInput` shape and `onEvent` construction in `harness.ts`. If it matches what Task 1 assumes (conditional on `input.onStep`, reacting only to `llm`/`tool_end`), proceed as written below. If it has drifted further, adapt the edit 1:1 to the real symbol names/branches — the acceptance criteria (onAgentLoopEvent fires for every event type; onStep behavior unchanged) govern, not the literal snippet text — and note the drift in the commit message.

- [x] **Step 1: Write the failing harness test**

Create `tests/harness-agent-loop-event.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { runAgent, type AgentStepEvent } from "@/lib/harness";
import { createToolRegistry } from "@/lib/tools/registry";
import { echoTool } from "@/lib/tools/echo";
import type { Tool } from "@/lib/tools/types";
import type { LlmAdapter } from "@/lib/llm/types";

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
const fakeAdapter: LlmAdapter = async function* (request) {
  const isFirstTurn = request.messages.filter((m) => m.role === "assistant").length === 0;
  if (isFirstTurn) {
    yield { type: "text_delta", delta: "checking..." };
    yield { type: "tool_call_start", id: "call-1", name: "echo" };
    yield { type: "tool_call_end", id: "call-1", name: "echo", input: { text: "hi" } };
    yield { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
    return { stopReason: "tool_use" };
  }
  yield { type: "text_delta", delta: "done" };
  yield { type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
  return { stopReason: "end" };
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
      onStep: (e) => stepEvents.push(e),
      onAgentLoopEvent: (e) => loopEvents.push(e.type),
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
      onAgentLoopEvent: (e) => loopEvents.push(e.type),
    });

    expect(result.status).toBe("succeeded");
    expect(loopEvents).toContain("llm");
    expect(loopEvents).toContain("tool_start");
    expect(loopEvents).toContain("tool_end");
  });
});
```

- [x] **Step 2: Run the test to verify it fails**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/harness-agent-loop-event.test.ts`
Expected: FAIL — `onAgentLoopEvent` is not a recognized field / `loopEvents` stays empty (adapt the exact failure to whatever R2's real fake-adapter test seam looks like; the acceptance criterion is "fails before the edit, passes after").

- [x] **Step 3: Apply the `harness.ts` edit above**

- [x] **Step 4: Run the test to verify it passes**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/harness-agent-loop-event.test.ts`
Expected: PASS (3 tests).

- [x] **Step 5: Write the failing `answer.ts` passthrough test**

Create `tests/memory-answer-agent-loop-event.test.ts`:

```ts
import { vi } from "vitest";

const { runAgentMock } = vi.hoisted(() => ({ runAgentMock: vi.fn() }));
vi.mock("@/lib/harness", () => ({ runAgent: runAgentMock }));
vi.mock("@/lib/ledger", () => ({ getRunTrace: vi.fn().mockResolvedValue(null) }));

import { answerWithMemory } from "@/lib/memory/answer";

describe("answerWithMemory onAgentLoopEvent passthrough", () => {
  beforeEach(() => runAgentMock.mockReset());

  it("forwards onAgentLoopEvent to runAgent unchanged", async () => {
    runAgentMock.mockImplementation(async (input: { onAgentLoopEvent?: (e: unknown) => void }) => {
      input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "hi" } });
      return { runId: 1, status: "succeeded", answer: "hi", steps: 1 };
    });

    const events: unknown[] = [];
    await answerWithMemory({} as never, {
      question: "q",
      onAgentLoopEvent: (e) => events.push(e),
    });

    expect(events).toHaveLength(1);
    expect(runAgentMock).toHaveBeenCalledWith(
      expect.objectContaining({ onAgentLoopEvent: expect.any(Function) })
    );
  });

  it("works with onAgentLoopEvent omitted (backward compatible)", async () => {
    runAgentMock.mockResolvedValue({ runId: 1, status: "succeeded", answer: "hi", steps: 1 });
    const result = await answerWithMemory({} as never, { question: "q" });
    expect(result.answer).toBe("hi");
  });
});
```

- [x] **Step 6: Run the test to verify it fails, then apply the `answer.ts` edit, then verify it passes**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/memory-answer-agent-loop-event.test.ts`
Expected: FAIL, then PASS (2 tests) after the edit.

- [x] **Step 7: Run the full existing harness + answer suites to confirm no regression**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/harness.test.ts tests/harness-onstep.test.ts tests/harness-multiturn.test.ts tests/memory-answer.test.ts`
Expected: PASS — all still green (adjust filenames to whatever R2/R4 actually left in place; the point is zero regressions in existing harness/answer coverage).

- [x] **Step 8: Commit**

```bash
git add src/lib/harness.ts src/lib/memory/answer.ts tests/harness-agent-loop-event.test.ts tests/memory-answer-agent-loop-event.test.ts
git commit -m "feat(r5): additive onAgentLoopEvent passthrough for streaming"
```

---

### Task 2: Extend the SSE writer (`ui-message-stream.ts`)

**Files:**
- Modify: `src/lib/ui-message-stream.ts`
- Test: `tests/ui-message-stream.test.ts` (new)

**What to build:** Replace the one-shot `answer()` method with three primitives — `answerDelta` (lazily starts the "answer" text part, writes one delta), `answerFlush` (lazily starts if needed, writes one delta of the given text, then closes — safe to call whether or not deltas already streamed), `answerEnd` (closes the part only if it was started; no-op otherwise) — plus a `toolPending` method for the live tool-name indicator.

- [x] **Step 1: Write the failing test**

Create `tests/ui-message-stream.test.ts`:

```ts
import { vi } from "vitest";

const writes: unknown[] = [];
const { createUIMessageStreamMock, createUIMessageStreamResponseMock } = vi.hoisted(() => ({
  createUIMessageStreamMock: vi.fn(),
  createUIMessageStreamResponseMock: vi.fn(() => new Response(null)),
}));
vi.mock("ai", () => ({
  createUIMessageStream: createUIMessageStreamMock,
  createUIMessageStreamResponse: createUIMessageStreamResponseMock,
}));

import { streamAgentResponse, type AgentStreamWriter } from "@/lib/ui-message-stream";

async function capture(run: (writer: AgentStreamWriter) => Promise<void>): Promise<unknown[]> {
  writes.length = 0;
  createUIMessageStreamMock.mockImplementation(({ execute }: { execute: (opts: { writer: { write: (c: unknown) => void } }) => Promise<void> }) => {
    const writer = { write: (chunk: unknown) => writes.push(chunk) };
    return execute({ writer });
  });
  streamAgentResponse(run);
  await new Promise((r) => setTimeout(r, 0));
  return writes;
}

describe("streamAgentResponse writer", () => {
  it("answerDelta streams multiple deltas under one text-start/text-end pair", async () => {
    const out = await capture(async (writer) => {
      writer.answerDelta("Hel");
      writer.answerDelta("lo");
      writer.answerEnd();
    });
    expect(out).toContainEqual({ type: "text-start", id: "answer" });
    expect(out).toContainEqual({ type: "text-delta", id: "answer", delta: "Hel" });
    expect(out).toContainEqual({ type: "text-delta", id: "answer", delta: "lo" });
    expect(out).toContainEqual({ type: "text-end", id: "answer" });
    expect(out.filter((c) => (c as { type: string }).type === "text-start")).toHaveLength(1);
  });

  it("answerEnd is a no-op if nothing was ever started", async () => {
    const out = await capture(async (writer) => {
      writer.answerEnd();
    });
    expect(out.some((c) => (c as { type: string }).type === "text-start")).toBe(false);
    expect(out.some((c) => (c as { type: string }).type === "text-end")).toBe(false);
  });

  it("answerFlush starts fresh when nothing streamed yet (today's one-shot behavior)", async () => {
    const out = await capture(async (writer) => {
      writer.answerFlush("full answer");
    });
    expect(out).toContainEqual({ type: "text-start", id: "answer" });
    expect(out).toContainEqual({ type: "text-delta", id: "answer", delta: "full answer" });
    expect(out).toContainEqual({ type: "text-end", id: "answer" });
  });

  it("answerFlush appends after live deltas instead of restarting the part", async () => {
    const out = await capture(async (writer) => {
      writer.answerDelta("checking... ");
      writer.answerFlush("final answer");
    });
    expect(out.filter((c) => (c as { type: string }).type === "text-start")).toHaveLength(1);
    expect(out).toContainEqual({ type: "text-delta", id: "answer", delta: "checking... " });
    expect(out).toContainEqual({ type: "text-delta", id: "answer", delta: "final answer" });
    expect(out.filter((c) => (c as { type: string }).type === "text-end")).toHaveLength(1);
  });

  it("toolPending writes a data-tool-pending part with the tool name", async () => {
    const out = await capture(async (writer) => {
      writer.toolPending("search_memory");
    });
    expect(out).toContainEqual({ type: "data-tool-pending", id: "tool-pending", data: { tool: "search_memory" } });
  });

  it("step and meta are unchanged", async () => {
    const out = await capture(async (writer) => {
      writer.step("step-1", { ok: true });
      writer.meta({ runId: 1 });
    });
    expect(out).toContainEqual({ type: "data-step", id: "step-1", data: { ok: true } });
    expect(out).toContainEqual({ type: "data-meta", id: "meta", data: { runId: 1 } });
  });
});
```

- [x] **Step 2: Run the test to verify it fails**

Run: `npx vitest run tests/ui-message-stream.test.ts`
Expected: FAIL — `writer.answerDelta`/`answerFlush`/`answerEnd`/`toolPending` don't exist yet.

- [x] **Step 3: Rewrite `src/lib/ui-message-stream.ts`**

```ts
import { createUIMessageStream, createUIMessageStreamResponse } from "ai";

// Boundary module for the AI SDK UI-message-stream transport. All `from "ai"`
// usage for streaming lives here (model-boundary.test.ts allows this file and
// model-gateway.ts) so AI SDK surface stays contained and auditable. Routes call
// streamAgentResponse and never touch the SDK directly.

export interface AgentStreamWriter {
  // A reasoning step (reconciled by id). data is the rendered ChatStepPart.
  step: (id: string, data: unknown) => void;
  // A tool has just been dispatched and is running; the reasoning trace shows
  // its name in the live pending row until the matching data-step settles.
  toolPending: (tool: string) => void;
  // One incremental chunk of the assistant's own generated text. Lazily opens
  // the "answer" text part on first use.
  answerDelta: (delta: string) => void;
  // Closes the "answer" text part with no further content — used when every
  // token was already streamed live via answerDelta and needs no correction.
  answerEnd: () => void;
  // Appends `text` as one more delta (starting the part fresh if nothing has
  // streamed yet) and closes it. Safe to call whether or not answerDelta ran
  // first — this is the authoritative, citation-checked flush.
  answerFlush: (text: string) => void;
  // Run metadata (runId, status, steps, invalidCitations).
  meta: (data: unknown) => void;
}

export function streamAgentResponse(run: (writer: AgentStreamWriter) => Promise<void>): Response {
  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      writer.write({ type: "start" });
      let answerStarted = false;
      const ensureStarted = () => {
        if (!answerStarted) {
          writer.write({ type: "text-start", id: "answer" });
          answerStarted = true;
        }
      };
      await run({
        step: (id, data) => writer.write({ type: "data-step", id, data }),
        toolPending: (tool) => writer.write({ type: "data-tool-pending", id: "tool-pending", data: { tool } }),
        answerDelta: (delta) => {
          ensureStarted();
          writer.write({ type: "text-delta", id: "answer", delta });
        },
        answerEnd: () => {
          if (answerStarted) {
            writer.write({ type: "text-end", id: "answer" });
            answerStarted = false;
          }
        },
        answerFlush: (text) => {
          ensureStarted();
          writer.write({ type: "text-delta", id: "answer", delta: text });
          writer.write({ type: "text-end", id: "answer" });
          answerStarted = false;
        },
        meta: (data) => writer.write({ type: "data-meta", id: "meta", data }),
      });
    },
    onError: (error) => (error instanceof Error ? error.message : String(error)),
  });
  return createUIMessageStreamResponse({ stream });
}
```

- [x] **Step 4: Run the test to verify it passes**

Run: `npx vitest run tests/ui-message-stream.test.ts`
Expected: PASS (6 tests).

- [x] **Step 5: Commit**

```bash
git add src/lib/ui-message-stream.ts tests/ui-message-stream.test.ts
git commit -m "feat(r5): incremental answer streaming + tool-pending part in the SSE writer"
```

---

### Task 3: Rewire `/api/agent/stream/route.ts`

**Files:**
- Modify: `src/app/api/agent/stream/route.ts`
- Modify: `tests/agent-stream-route.test.ts` (existing file — extend, don't replace its current two tests)

**What to build:** Consume `onStep` (unchanged, still feeds `writer.step`) and the new `onAgentLoopEvent` to decide, per event, whether to live-stream text or gate it behind the end-of-run citation-checked flush, per Judgment call #2.

- [x] **Step 1: Write the failing tests, appended to `tests/agent-stream-route.test.ts`**

Add these `it` blocks inside the existing `describe("POST /api/agent/stream", ...)`:

```ts
  it("streams the answer live token-by-token when search_memory was never called", async () => {
    runAgentMock.mockImplementation(
      async (input: {
        onStep?: (e: unknown) => unknown;
        onAgentLoopEvent?: (e: unknown) => unknown;
      }) => {
        await input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "Acme is " } });
        await input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "a Series B co." } });
        return { runId: 7, status: "succeeded", answer: "Acme is a Series B co.", steps: 0 };
      }
    );

    const res = await POST(postRequest({ question: "what is acme" }));
    const body = await readAll(res);

    // Two separate text-delta writes prove live streaming, not one final flush.
    const deltaCount = (body.match(/"type":"text-delta"/g) ?? []).length;
    expect(deltaCount).toBe(2);
    expect(body).toContain("Acme is ");
    expect(body).toContain("a Series B co.");
    expect(body).toContain("data-meta");
  });

  it("buffers text once search_memory is called and flushes the checked answer once at the end", async () => {
    runAgentMock.mockImplementation(
      async (input: {
        onStep?: (e: unknown) => unknown;
        onAgentLoopEvent?: (e: unknown) => unknown;
      }) => {
        await input.onAgentLoopEvent?.({ type: "tool_start", id: "call-1", name: "search_memory", input: {} });
        // Any text after the tool_start must NOT be forwarded live.
        await input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "should not stream" } });
        await input.onStep?.({
          index: 1,
          tool: "search_memory",
          observation: 'Success: {"acceptedFacts":[],"gapFacts":[],"chunks":[]}',
          ok: true,
        });
        return {
          runId: 8,
          status: "succeeded",
          answer: "Acme Robotics is a Series B company [acme-md#chunk-1].",
          steps: 1,
        };
      }
    );

    const res = await POST(postRequest({ question: "tell me about acme" }));
    const body = await readAll(res);

    expect(body).not.toContain("should not stream");
    // Exactly one text-delta write carries the final, checked answer.
    const deltaLines = body.split("\n").filter((l) => l.includes('"type":"text-delta"'));
    expect(deltaLines).toHaveLength(1);
    expect(body).toContain("Acme Robotics is a Series B company");
    expect(body).toContain("data-tool-pending");
    expect(body).toContain("search_memory");
  });

  it("still streams the answer in one shot when the caller never emits onAgentLoopEvent (backward compatible)", async () => {
    runAgentMock.mockImplementation(async (input: { onStep?: (e: unknown) => unknown }) => {
      await input.onStep?.({
        index: 1,
        tool: "search_memory",
        observation: 'Success: {"acceptedFacts":[1],"gapFacts":[],"chunks":[1]}',
        ok: true,
      });
      return { runId: 9, status: "succeeded", answer: "Answer with no live events.", steps: 1 };
    });

    const res = await POST(postRequest({ question: "tell me about acme" }));
    const body = await readAll(res);
    expect(body).toContain("Answer with no live events.");
  });

  it("accepts that pre-tool narration streams live AND reappears in the step's thought field (Judgment call #3 — documented, not suppressed)", async () => {
    runAgentMock.mockImplementation(
      async (input: {
        onStep?: (e: unknown) => unknown;
        onAgentLoopEvent?: (e: unknown) => unknown;
      }) => {
        await input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "checking memory..." } });
        await input.onAgentLoopEvent?.({ type: "tool_start", id: "call-1", name: "search_memory", input: {} });
        await input.onStep?.({
          index: 1,
          tool: "search_memory",
          observation: 'Success: {"acceptedFacts":[],"gapFacts":[],"chunks":[]}',
          ok: true,
          thought: "checking memory...",
        });
        return {
          runId: 10,
          status: "succeeded",
          answer: "Acme is a Series B company.",
          steps: 1,
        };
      }
    );

    const res = await POST(postRequest({ question: "tell me about acme" }));
    const body = await readAll(res);

    // The narration streamed live before the tool_start gate closed...
    expect(body).toContain("checking memory...");
    // ...and also reappears verbatim as the step's thought line — accepted duplication, not suppressed.
    expect(body).toContain('"thought":"checking memory..."');
  });
```

- [x] **Step 2: Run the test to verify the new tests fail**

Run: `npx vitest run tests/agent-stream-route.test.ts`
Expected: FAIL on the four new tests (route doesn't consume `onAgentLoopEvent` yet, `writer.answer` doesn't exist post-Task-2). The two pre-existing tests should still be passing at this point against the *old* route.ts (sanity check before editing).

- [x] **Step 3: Rewrite `src/app/api/agent/stream/route.ts`**

```ts
import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { answerWithMemory, summarizeStep } from "@/lib/memory/answer";
import { streamAgentResponse } from "@/lib/ui-message-stream";
import type { ConversationTurn } from "@/lib/harness";

// Streaming sibling of /api/agent: same memory agent run, but tool steps and
// the model's own text stream to the client live. Text streams token-by-token
// only while no search_memory call has happened this run (nothing to check
// yet); once one fires, subsequent text is held back and the authoritative,
// citation-checked answer is flushed once at the end — so no invalid citation
// ever streams. See docs/superpowers/plans/2026-07-14-r5-streaming-rewire-plan.md
// Judgment call #2 for the exact gating rules.
export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const question = (body as { question?: unknown } | null)?.question;
  if (typeof question !== "string" || !question.trim()) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }
  const history = parseHistory((body as { history?: unknown } | null)?.history);
  const db = getDb();

  return streamAgentResponse(async (writer) => {
    let searchCalledSoFar = false;
    let streamedLive = false;

    const result = await answerWithMemory(db, {
      question,
      history,
      onStep: (event) => {
        writer.step(`step-${event.index}`, summarizeStep(event));
        if (event.tool === "search_memory") searchCalledSoFar = true;
      },
      onAgentLoopEvent: (event) => {
        if (event.type === "tool_start") {
          writer.toolPending(event.name);
          if (event.name === "search_memory") searchCalledSoFar = true;
        } else if (event.type === "llm" && event.event.type === "text_delta" && !searchCalledSoFar) {
          writer.answerDelta(event.event.delta);
          streamedLive = true;
        }
      },
    });

    if (streamedLive && !searchCalledSoFar) {
      writer.answerEnd();
    } else if (result.answer) {
      writer.answerFlush(result.answer);
    }

    writer.meta({
      runId: result.runId,
      status: result.status,
      steps: result.steps,
      invalidCitations: result.invalidCitations,
    });
  });
}

// Accept only well-formed {role, content} turns; ignore anything malformed so a
// bad client payload degrades to a single-turn run rather than a 500.
function parseHistory(raw: unknown): ConversationTurn[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const turns = raw.filter(
    (turn): turn is ConversationTurn =>
      typeof turn === "object" &&
      turn !== null &&
      ((turn as ConversationTurn).role === "user" || (turn as ConversationTurn).role === "assistant") &&
      typeof (turn as ConversationTurn).content === "string"
  );
  return turns.length ? turns : undefined;
}
```

- [x] **Step 4: Run the full route test file to verify all pass**

Run: `npx vitest run tests/agent-stream-route.test.ts`
Expected: PASS (all 6 tests: the original 2 plus the 4 new ones).

- [x] **Step 5: Commit**

```bash
git add src/app/api/agent/stream/route.ts tests/agent-stream-route.test.ts
git commit -m "feat(r5): stream typed LlmStreamEvents + tool lifecycle over SSE"
```

---

### Task 4: Client SSE parser — `data-tool-pending` support

**Files:**
- Modify: `src/app/chat/stream.ts`
- Modify: `tests/chat-stream.test.ts` (existing file — extend)

**What to build:** `text-delta` already concatenates correctly into `turn.answer` with zero changes needed (verified by the existing "concatenates text-delta chunks" test — confirm it still passes unmodified). Add handling for the new `data-tool-pending` chunk type and a `pendingTool` field on `AssistantTurn`, cleared whenever a step settles or the run completes.

- [x] **Step 1: Write the failing tests, appended to `tests/chat-stream.test.ts`**

Add inside `describe("applyChunk", ...)`:

```ts
  it("sets pendingTool from a data-tool-pending chunk", () => {
    const turn = applyChunk(empty, { type: "data-tool-pending", data: { tool: "search_memory" } });
    expect(turn.pendingTool).toBe("search_memory");
  });

  it("clears pendingTool once the matching step settles", () => {
    let turn = applyChunk(empty, { type: "data-tool-pending", data: { tool: "search_memory" } });
    turn = applyChunk(turn, {
      type: "data-step",
      data: { index: 1, tool: "search_memory", ok: true, detail: "2 facts, 1 chunk" },
    });
    expect(turn.pendingTool).toBeUndefined();
  });

  it("clears pendingTool once the run's meta lands", () => {
    let turn = applyChunk(empty, { type: "data-tool-pending", data: { tool: "search_memory" } });
    turn = applyChunk(turn, {
      type: "data-meta",
      data: { runId: 1, status: "succeeded", steps: 1, invalidCitations: [] },
    });
    expect(turn.pendingTool).toBeUndefined();
  });
```

- [x] **Step 2: Run the test to verify the new tests fail**

Run: `npx vitest run tests/chat-stream.test.ts`
Expected: FAIL on the three new tests; the pre-existing tests in this file still pass (confirms `text-delta` needs no change).

- [x] **Step 3: Edit `src/app/chat/stream.ts`**

Add `pendingTool?: string` to `AssistantTurn` and a case to `applyChunk`:

```ts
export interface AssistantTurn {
  steps: ChatStep[];
  answer: string;
  meta?: ChatMeta;
  // Name of the tool currently dispatched but not yet settled into a ChatStep,
  // shown in the reasoning trace's live pending row. Cleared once the matching
  // data-step or the run's data-meta lands.
  pendingTool?: string;
}
```

```ts
export function applyChunk(turn: AssistantTurn, chunk: UiChunk): AssistantTurn {
  switch (chunk.type) {
    case "data-step": {
      const step = chunk.data as ChatStep;
      const exists = turn.steps.some((s) => s.index === step.index);
      const steps = exists
        ? turn.steps.map((s) => (s.index === step.index ? step : s))
        : [...turn.steps, step];
      return { ...turn, steps, pendingTool: undefined };
    }
    case "data-tool-pending":
      return { ...turn, pendingTool: (chunk.data as { tool: string }).tool };
    case "text-delta":
      return { ...turn, answer: turn.answer + (chunk.delta ?? "") };
    case "data-meta":
      return { ...turn, meta: chunk.data as ChatMeta, pendingTool: undefined };
    default:
      return turn;
  }
}
```

- [x] **Step 4: Run the test to verify it passes**

Run: `npx vitest run tests/chat-stream.test.ts`
Expected: PASS (all tests in the file, old and new).

- [x] **Step 5: Commit**

```bash
git add src/app/chat/stream.ts tests/chat-stream.test.ts
git commit -m "feat(r5): client-side pendingTool tracking for the live tool indicator"
```

---

### Task 5: Reasoning trace — live tool-name label

**Files:**
- Modify: `src/app/chat/ReasoningTrace.tsx`
- Modify: `src/app/chat/ChatClient.tsx`
- Modify: `tests/components/chat-pieces.test.tsx` (existing file — extend)

**What to build:** `ReasoningTrace` gains an optional `pendingTool?: string` prop; when set, the live `PendingRow` shows "Running `{tool}`…" instead of the generic "Searching memory"/"Composing answer" copy. `ChatClient` passes `e.turn.pendingTool` through.

- [x] **Step 1: Write the failing test, appended to `describe("ReasoningTrace", ...)` in `tests/components/chat-pieces.test.tsx`**

```ts
  it("shows the pending tool name in the live row when given", () => {
    render(
      <ReasoningTrace steps={[]} running={true} open={true} onToggle={() => {}} pendingTool="search_memory" />
    );
    expect(screen.getByText(/Running search_memory/)).toBeInTheDocument();
  });

  it("falls back to the generic label when no pending tool is given", () => {
    render(<ReasoningTrace steps={[]} running={true} open={true} onToggle={() => {}} />);
    expect(screen.getByText(/Searching memory/)).toBeInTheDocument();
  });
```

- [x] **Step 2: Run the test to verify it fails**

Run: `npx vitest run tests/components/chat-pieces.test.tsx`
Expected: FAIL on the new `pendingTool` test (prop doesn't exist yet); the fallback test may already pass coincidentally — that's fine, both must pass after the edit.

- [x] **Step 3: Edit `src/app/chat/ReasoningTrace.tsx`**

```tsx
import { StepRow, PendingRow } from "./StepRow";
import type { ChatStep } from "./stream";

// The collapsible reasoning trace. While the agent runs it auto-expands and a live
// PendingRow sits at the tail; once the answer lands the container collapses it to
// a one-line "Reasoning · N steps". Collapse animates via grid-template-rows
// (animation-safe) in globals.css.
export function ReasoningTrace({
  steps,
  running,
  open,
  onToggle,
  pendingTool,
}: {
  steps: ChatStep[];
  running: boolean;
  open: boolean;
  onToggle: () => void;
  pendingTool?: string;
}) {
  const count = steps.length;
  const pendingLabel = pendingTool
    ? `Running ${pendingTool}`
    : count === 0
      ? "Searching memory"
      : "Composing answer";

  return (
    <div className="reasoning-trace">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex items-center gap-1.5 text-[12px] font-medium text-muted transition-colors hover:text-text focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-accent-tint rounded-[4px]"
      >
        <span className={`chevron ${open ? "chevron--open" : ""}`} aria-hidden>
          ▸
        </span>
        <span>Reasoning</span>
        {count > 0 ? (
          <span className="font-mono text-[11px] text-muted">
            · {count} step{count === 1 ? "" : "s"}
          </span>
        ) : null}
      </button>

      <div className="reasoning-body" data-open={open}>
        <ul className="reasoning-list mt-1.5 flex flex-col gap-1">
          {steps.map((s) => (
            <StepRow key={s.index} step={s} />
          ))}
          {running ? <PendingRow label={pendingLabel} /> : null}
        </ul>
      </div>
    </div>
  );
}
```

(`StepRow.tsx` needs no change — it only renders settled steps.)

- [x] **Step 4: Edit `src/app/chat/ChatClient.tsx`** — pass `pendingTool` through

Find the `<ReasoningTrace ... />` call and add one prop:

```tsx
                  {(e.turn.steps.length > 0 || !e.done) && (
                    <ReasoningTrace
                      steps={e.turn.steps}
                      running={!e.done && !e.errored}
                      open={e.open}
                      onToggle={() => patch(e.id, (x) => ({ ...x, open: !x.open }))}
                      pendingTool={e.turn.pendingTool}
                    />
                  )}
```

- [x] **Step 5: Run the test to verify it passes**

Run: `npx vitest run tests/components/chat-pieces.test.tsx tests/components/ChatClient.test.tsx`
Expected: PASS (all tests in both files).

- [x] **Step 6: Commit**

```bash
git add src/app/chat/ReasoningTrace.tsx src/app/chat/ChatClient.tsx tests/components/chat-pieces.test.tsx
git commit -m "feat(r5): live tool-name label in the reasoning trace's pending row"
```

---

### Task 6: Full verification pass

**Files:** none (verification only)

- [x] **Step 1: Run the full test suite**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run`
Expected: PASS — every prior suite plus the 5 new/extended files from this plan (`harness-agent-loop-event.test.ts`, `memory-answer-agent-loop-event.test.ts`, `ui-message-stream.test.ts`, `agent-stream-route.test.ts`, `chat-stream.test.ts`, `chat-pieces.test.tsx`, `ChatClient.test.tsx`) all green.

- [x] **Step 2: Lint**

Run: `npm run lint`
Expected: `✔ No ESLint warnings or errors`.

- [x] **Step 3: Build**

Run: `npm run build`
Expected: build succeeds; `/api/agent/stream` and `/chat` still listed.

- [x] **Step 4: Model boundary check**

Run: `npx vitest run tests/model-boundary.test.ts`
Expected: PASS — no new file outside `ui-message-stream.ts`/`model-gateway.ts` imports `"ai"` or `@ai-sdk/*`.

- [x] **Step 5: Manual smoke (recommended)**

Run in one terminal: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npm run dev`
Open `http://localhost:3000/chat`, ask a question the memory index already covers (no search needed) and confirm the answer visibly types in over multiple renders rather than appearing all at once; ask a question that requires `search_memory` and confirm the reasoning trace's pending row shows "Running search_memory" before settling, and the answer still appears (in one piece) with a working "View trace" link.

- [x] **Step 6: Commit (if step 4/5 required any fix-up)**

```bash
git add -A
git commit -m "chore(r5): verification pass for streaming rewire"
```

---

## Tests Summary

| File | New/Extended | Covers |
|---|---|---|
| `tests/harness-agent-loop-event.test.ts` | new | `onAgentLoopEvent` fires for llm/tool_start/tool_end; `onStep` unaffected; omitting the field doesn't break `runAgent`; fires even when `onStep` is omitted (no silent no-op) |
| `tests/memory-answer-agent-loop-event.test.ts` | new | `answerWithMemory` forwards `onAgentLoopEvent` to `runAgent`; backward compatible when omitted |
| `tests/ui-message-stream.test.ts` | new | `answerDelta`/`answerFlush`/`answerEnd`/`toolPending` write the right SSE parts, including the append-after-live-deltas case and the never-started no-op case |
| `tests/agent-stream-route.test.ts` | extended | live streaming when no search; buffered-then-flushed when search fires; backward-compatible one-shot flush when the caller never emits `onAgentLoopEvent`; accepted pre-tool-narration double-surfacing (Judgment call #3) |
| `tests/chat-stream.test.ts` | extended | `data-tool-pending` sets/clears `pendingTool`; existing `text-delta` concatenation unchanged |
| `tests/components/chat-pieces.test.tsx` | extended | `ReasoningTrace` shows the live tool name when given, generic copy otherwise |
| `tests/components/ChatClient.test.tsx` | run only (no new tests expected) | confirms `pendingTool` passthrough doesn't break existing streaming/error/history behavior |

Total new test count: ~21 across 4 new + 3 extended files — within the spec's "Integration | Stream route... +3-4" plus incidental unit coverage for the writer/harness passthrough.

---

## Self-Review

**Spec coverage:**
- "`/api/agent/stream` re-emits `LlmStreamEvent`s + tool lifecycle over the existing SSE channel" → Task 3 (route consumes `onAgentLoopEvent`, forwards `text_delta`/`tool_start`).
- "tool events feed the trace" → Task 3 (`writer.toolPending`) + Task 4/5 (`pendingTool` state + live label).
- "text deltas feed the bubble — true token streaming replaces per-step flushes" → Task 2 (`answerDelta`) + Task 3 (live-stream branch), demoable in the no-search AC1 branch; the search-required branch keeps today's exact one-shot, citation-safe flush (Judgment call #2).
- Citation safety invariant ("no invalid citation ever streams") preserved: the only text streamed live is text produced before any `search_memory` call this run; anything after is buffered and only reaches the client via the existing citation-checked `result.answer`.

**Placeholder scan:** No TBD/TODO; every code and test step contains full content.

**Type consistency:** `AgentLoopEvent`/`LlmStreamEvent` shapes used in Tasks 1 and 3 match the contracts brief exactly (`{type:"llm",event}`, `{type:"tool_start",id,name,input}`, `{type:"text_delta",delta}`). `AgentStreamWriter`'s new methods are used identically across Tasks 2 and 3. `AssistantTurn.pendingTool` is threaded consistently through Tasks 4 and 5.

**Deviations flagged:** Task 1's edits to `src/lib/harness.ts` (R2-owned) and `src/lib/memory/answer.ts` (unclaimed) are called out in Judgment call #1 and in the plan header, per the contracts brief's explicit escape hatch for flagged deviations.

---

## Eng Review (2026-07-14)

**Method:** Read this plan against the current repo state on `feat/a-chat-streaming` (not against the R-slice plan docs alone) — `src/lib/harness.ts`, `src/lib/memory/answer.ts`, `src/app/api/agent/stream/route.ts`, `src/lib/ui-message-stream.ts`, `src/app/chat/stream.ts`, `src/app/chat/ReasoningTrace.tsx`, `src/app/chat/StepRow.tsx`, `tests/agent-stream-route.test.ts`, `tests/harness.test.ts`, `tests/model-boundary.test.ts`, `src/lib/memory/answer-config.ts`, `src/lib/memory/citations.ts` — cross-referenced against `2026-07-14-r-contracts-brief.md` and `2026-07-14-r2-agent-loop-plan.md` (since R5 diffs against R2's *planned* output, and R2 hasn't landed either).

### Verdict: approve (revised)

The decomposition (6 tasks, additive-only deviations into R2/unclaimed files, TDD-first, backward-compat tests at every layer) is sound and appropriately scoped — 8 files touched, 0 new classes, matches "boring by default." Tasks 4 and 5 (client-side `pendingTool` plumbing) are verified feasible against the actual current `stream.ts`/`ReasoningTrace.tsx`/`ChatClient.tsx` files and need no changes. The problems are concentrated in Task 1's integration with R2's real (planned) `harness.ts`, and one unaddressed UX duplication that falls out of that integration.

### Must-fix

1. **[RESOLVED]** **Task 1's snippet mischaracterizes R2's actual `onEvent` closure — the passthrough will silently no-op in an untested path.** R5 says: "inside the wrapper's existing `onEvent` closure passed to `runAgentLoop(...)` (the one that currently collapses `tool_start`/`tool_end` pairs into a legacy `AgentStepEvent`... add the passthrough as the first statement." But R2's own plan (`2026-07-14-r2-agent-loop-plan.md:1070-1089`) builds `onEvent` **conditionally**: `const onEvent = input.onStep ? async (event) => {...} : undefined;` — and that closure reacts only to `event.type === "llm"` (buffered into a `thoughtBuffer`) and `event.type === "tool_end"` (fires `onStep` with the buffered thought). It never inspects `tool_start` at all — there is no "tool_start/tool_end collapse" to insert into. If an implementer follows the snippet literally, `onAgentLoopEvent` is wired into a closure that is `undefined` whenever `input.onStep` is omitted — meaning a caller that sets `onAgentLoopEvent` but not `onStep` gets silent no-streaming with no error. R5's only real caller (`route.ts`) always sets both, so this won't bite in practice today, but Task 1's own tests (Step 1) only cover "both set" and "neither set" — never "`onAgentLoopEvent` alone" — so the gap ships with zero test coverage. **Fix:** restructure the conditional to build `onEvent` whenever `input.onStep || input.onAgentLoopEvent` is set, forward the raw event to `onAgentLoopEvent` unconditionally as the first statement (regardless of event type), then fall into the existing tool_end/thought-buffer logic. Add a test case for `onAgentLoopEvent` set with `onStep` omitted.

**Resolution:** Task 1's snippet now builds `onEvent` off `input.onStep || input.onAgentLoopEvent`, forwards the raw event unconditionally as the first statement, and returns early into the (possibly-absent) `onStep` logic only when `onStep` is set. A third test, "fires onAgentLoopEvent even when onStep is omitted (no silent no-op)," covers the previously-untested path; Step 4's expected count is bumped to 3 tests.

2. **[RESOLVED]** **Narration-before-tool-call duplicates into the UI, unaddressed by Judgment call #3.** R2's `thoughtBuffer` mechanism accumulates every `text_delta`/`thinking_delta` since the last flush and attaches it as `AgentStepEvent.thought` on the *next* completed tool step's `onStep` call — and `StepRow.tsx:13` renders `step.thought` visibly under that step's row. Judgment call #3 already accepts that pre-tool-call narration streams live into the answer bubble with "no retraction" — but the identical narration text will **also** reappear a second time, verbatim, as the `.thought` line under the `search_memory` step in the reasoning trace, via a completely separate (pre-existing, R2-owned) code path that Judgment call #3 doesn't mention. This is a concrete, visible double-surfacing of the same sentence in two places in the same turn, and none of Task 2/3's tests catch it (they test the answer-bubble half only). **Fix:** make an explicit call — either accept and document the duplication (consistent with Judgment call #3's existing "accepted edge case" framing), or have the `onAgentLoopEvent` handler suppress forwarded-live text from re-entering `thoughtBuffer` — and add a test asserting the chosen behavior.

**Resolution:** Accept and document (per the review's own recommendation) — Judgment call #3 now explicitly names the duplication into `StepRow.tsx`'s thought line and states the decision not to suppress it. Task 3 gains a test, "accepts that pre-tool narration streams live AND reappears in the step's thought field," asserting both surfaces carry the same narration text.

3. **[RESOLVED]** **Add a pre-flight verification step before Task 1 begins.** None of this plan's assumed prerequisite files exist yet in this repo: `src/lib/agent-loop.ts`, `src/lib/llm/types.ts`, and `src/lib/tools/orchestrator.ts` are all `MISSING` (confirmed by direct file check), and the current `src/lib/harness.ts`/`tests/harness.test.ts` still use the pre-R2 `generate_object`/`provider` seam, not `runAgentLoop`/`adapter`. The plan states this dependency in prose ("Assume all five are merged") but has no explicit gate — an agent picking up this plan today will fail immediately at `import type { AgentLoopEvent } from "./agent-loop"` with a nonexistent module, and won't have a structured way to detect "R2 hasn't landed yet" vs. "I made a mistake." **Fix:** add a Step 0 to Task 1 that greps/reads for `src/lib/agent-loop.ts` and the shape of `RunAgentInput` in `harness.ts`, and hard-stops with a clear message if R2 hasn't landed or its shape has drifted from what this plan assumes, rather than proceeding to hand-adapt silently.

**Resolution:** Task 1 now opens with "Step 0: Pre-flight — confirm R2/R1 have actually landed," which checks for `src/lib/agent-loop.ts` and `src/lib/llm/types.ts` and greps the real `onEvent`/`RunAgentInput` shape in `harness.ts`, with an explicit stop-and-report instruction if either file is missing.

### Should-fix / notes

4. **`searchCalledSoFar` gates on the literal tool name `"search_memory"`, not "any tool call happened."** Safe today because `memoryRegistry()` (`src/lib/memory/answer-config.ts`) registers exactly one tool, so in practice "any tool call" and "search_memory called" are the same event — but that equivalence is implicit and undocumented. The contracts brief itself flags wiring R7's enrich tools into the live registry as a near-term v2 item; the day a second read/citation-bearing tool joins the same `allowedClasses` set, this hardcoded name check will let live text stream past a citation-relevant call it should have gated on. Recommend a one-line comment calling out the coupling in Task 3's `route.ts`, or gate on "any `tool_start`" instead of a specific tool name (behavior-identical today, safer against registry growth).

5. **Task 1 Step 7's regression command is already a known-stale placeholder, not a checkable step.** `tests/harness.test.ts` today asserts against the pre-R2 `provider`/`generate_object` mock seam (confirmed by reading the file), and R2's own plan states `tests/harness-onstep.test.ts` is fully rewritten by R2. The plan's hedge ("adjust filenames to whatever R2/R4 actually left in place") is honest about this uncertainty, but it means Task 1 has no concrete, executable verify command today — this is expected given the sequencing, but worth tightening to a real command once R2 has actually landed rather than executing this step against a guess.

6. **The core streaming UX claim ("answer visibly types in over multiple renders") has no automated coverage beyond SSE payload shape.** Task 3's tests assert `text-delta` write counts, and Task 6 Step 5 is a manual smoke test. There's no integration/browser-level test asserting the live-typing behavior end-to-end. Reasonable to defer to R9 (e2e provider swap) if that slice owns it — confirm it does, or add one here.

### Verified as sound (no action needed)

- Tasks 4 and 5's edits to `src/app/chat/stream.ts`, `ReasoningTrace.tsx`, and `ChatClient.tsx` match the actual current file contents exactly (read and diffed against source) — these are feasible today, independent of R0–R4 landing.
- `tests/agent-stream-route.test.ts`'s existing `postRequest`/`readAll` helpers (read from source) are reused correctly by Task 3's new test cases.
- `tests/model-boundary.test.ts`'s allow-list (read from source) does confirm `ui-message-stream.ts` and `model-gateway.ts` are the only two files permitted to import `"ai"` — Judgment call #6's claim is accurate.
- The `R2 → R3` transitive-dependency callout in the plan header (runAgentLoop calls R3's `executeTool()` despite the dependency diagram drawing R3 as a sibling) is correct and appropriately flagged already — no note needed.
- Citation-safety invariant logic (gate on `searchCalledSoFar`, flush only the post-check `result.answer`) is correct in design, contingent on must-fix #1 being resolved so the gate actually wires up in all cases.

### Unresolved decisions

- Must-fix #2 (thought/answer duplication) required a product call: accept the duplication as a documented edge case, or suppress one surface. **Resolved:** accept and document (matches Judgment call #3's existing posture, cheapest fix, real-world impact is low since native tool-use models rarely narrate before their first tool call) — reflected in the revised Judgment call #3 and Task 3's new test.

NO UNRESOLVED DECISIONS remain; all three must-fix items are resolved in this revision.
