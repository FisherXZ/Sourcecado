# R1 — Provider Adapter Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add raw-SDK provider adapters (`@anthropic-ai/sdk`, `openai`) behind
the normalized `LlmMessage`/`LlmStreamEvent` contract, plus a new
`streamAgentTurn()` entry on the Model Gateway that traces every streaming
turn to the Run Ledger exactly like `callModel()` does. Nothing in this slice
executes a loop or a tool — it is pure plumbing: contract types, two
adapters, one gateway function. `callModel()`'s four existing kinds are
untouched.

**Depends on:** R0 (PR #10 merged — this branch forks from updated `main`).
R2 (the loop), R3 (tool orchestrator), R4+ all depend on this slice's output
(`src/lib/llm/types.ts`'s exports and `streamAgentTurn()`'s signature) but are
out of scope here — this plan does not touch `agent-loop.ts`, `harness.ts`,
or `tools/orchestrator.ts`.

## Context

Read `docs/superpowers/specs/2026-07-14-runtime-solidification-sprint-spec.md`
(R1 section) and `docs/superpowers/plans/2026-07-14-r-contracts-brief.md`
(§1–2, §7) before starting — this plan implements those sections verbatim and
does not repeat the rationale.

Today `src/lib/model-gateway.ts` is built entirely on the Vercel AI SDK
(`ai`, `@ai-sdk/anthropic`, `@ai-sdk/deepseek`, `@ai-sdk/openai`) via
`callModel({ kind: "generate_text" | "generate_object" | "embed" |
"embed_many" })` — string prompts only, no native tool-calling, no streaming.
This slice adds a second, independent path: raw-SDK adapters emitting a
normalized event stream, and a new `streamAgentTurn()` gateway entry that
records to the same `model_calls`/`run_steps` tables with a new
`call_kind = 'stream_turn'`. `callModel()` and its four kinds are not
modified in this slice (R8 migrates them later).

**Files this plan owns (new):**
- `src/lib/llm/types.ts`
- `src/lib/llm/anthropic.ts`
- `src/lib/llm/openai-compat.ts`
- `src/migrations/004_model_calls_stream_turn.sql` (see Judgment calls — numbering deviation, flagged)
- `tests/llm-anthropic.test.ts`, `tests/llm-openai-compat.test.ts`,
  `tests/model-gateway-stream.test.ts`, `tests/migrate-stream-turn.test.ts`

**Files this plan extends (additive only, no rewrite):**
- `package.json` — add `@anthropic-ai/sdk`, `openai`.
- `src/lib/model-gateway.ts` — add `StreamAgentTurnInput`, `LlmTurnOutcome`,
  `streamAgentTurn()`; widen `ModelCallKind` to include `"stream_turn"`;
  export `resolveProviderName`/`resolveModel` (currently private) and widen
  their parameter type so `streamAgentTurn` can reuse them unchanged.

**Read-only reference:** `src/lib/ledger.ts` (`startRunStep`, `finishRunStep`,
`failRunStep` — signatures used, not changed).

## Judgment calls

- **Migration file numbering.** The contracts brief's file-ownership table
  (§7) assigns `004_chat_sessions.sql` to R6. But `streamAgentTurn` requires
  `call_kind = 'stream_turn'` to pass the existing `CHECK` constraint on
  `model_calls.call_kind` (`src/migrations/001_run_ledger_model_gateway.sql:61`),
  which R1 must widen — the brief did not anticipate this because §2's
  `streamAgentTurn` pseudocode assumes the constraint already allows it. R1
  lands before R6 in the dependency graph, so this plan claims
  `004_model_calls_stream_turn.sql`; **R6's plan must use
  `005_chat_sessions.sql`, not `004_chat_sessions.sql` as written in the
  brief** — flag this back explicitly when R6's plan is authored (do not
  silently renumber R6's doc from here). Migrations apply in filename sort
  order (`src/lib/migrate.ts`), so the numeric prefix is load-bearing, not
  cosmetic.
- **Constraint-name assumption verified by a live test, not asserted blind.**
  Postgres auto-names an inline `CHECK` as `<table>_<column>_check`
  (`model_calls_call_kind_check`), so the migration does
  `DROP CONSTRAINT IF EXISTS model_calls_call_kind_check` then re-adds it
  widened. Task 3's test inserts a real row with `call_kind = 'stream_turn'`
  against a migrated DB — if the name assumption is wrong, that INSERT throws
  and the task fails until fixed, so the plan is self-correcting on this
  point rather than resting on the assumption.
- **`resolveAnthropicBaseUrl` duplicated, not imported.** `model-gateway.ts`
  will import `anthropicAdapter` from `llm/anthropic.ts` (to pick the default
  adapter in `streamAgentTurn`). If `llm/anthropic.ts` imported
  `resolveAnthropicBaseUrl` back from `model-gateway.ts`, that's a circular
  import. The ~5-line helper is duplicated verbatim in `llm/anthropic.ts`
  instead.
- **`openai-compat.ts` exports a factory, not two named adapters.** The
  contracts brief pins `LlmTurnRequest` with no `providerName` field, so a
  bare `LlmAdapter` function can't know at call time whether to hit
  `api.deepseek.com` or `api.openai.com`. This plan exports
  `createOpenAiCompatAdapter(providerName: "deepseek" | "openai"): LlmAdapter`
  — a closure factory `streamAgentTurn`'s `pickAdapter` calls once per
  request, keeping `LlmTurnRequest` exactly as pinned.
- **No `thinking_delta` from `openai-compat.ts`.** DeepSeek's
  `reasoning_content` streaming field isn't in the `openai` npm package's
  types, and the default provider model (`deepseek-chat`) doesn't emit it
  anyway (only `deepseek-reasoner` does). Mapping it would require an unsafe
  cast for a code path nothing exercises yet. Left out; only
  `anthropicAdapter` can ever emit `thinking_delta` (and only when a caller
  sets extended thinking — R1 doesn't request it, so it's inert for now,
  matching the contract's optionality).
- **Adapters never catch `AbortSignal`/errors themselves.** Both adapters let
  any thrown error (including the underlying SDK's own abort error) propagate
  out of the async generator unchanged. Classifying "aborted" vs
  "provider_error" and writing the failed ledger row is `streamAgentTurn`'s
  job alone (mirrors the brief's §2 ownership split and keeps the adapters
  down to "stream normalized events, nothing else").
- **`stream_options: {include_usage: true}` added to every openai-compat
  request.** Without it, OpenAI/DeepSeek chat-completion streams never
  include a `usage` object, so `LlmUsage` would always be nulls. This is
  required to satisfy the pinned `LlmUsage` contract, not an added feature.

## Tasks

### Task 1: Add raw SDK dependencies

**Build:** Add `@anthropic-ai/sdk` and `openai` to `package.json`
`dependencies` (alongside the existing `@ai-sdk/*`/`ai` entries — do not
remove those; R8 removes them later). Run `npm install`.

**Exact files:** `package.json`, `package-lock.json`.

**Acceptance criteria:**
- `package.json` lists `"@anthropic-ai/sdk": "^0.111.0"` and
  `"openai": "^6.47.0"` under `dependencies`.
- `node_modules/@anthropic-ai/sdk` and `node_modules/openai` exist.
- Existing test suite still passes unmodified (no version conflicts).

**Verify:**
```bash
cd /Users/fisher/Documents/GitHub2026/Sourcecado
npm install
npx vitest run 2>&1 | tail -20
```
Expected: install succeeds; full suite still green (baseline — nothing in
this task changes runtime behavior).

---

### Task 2: `src/lib/llm/types.ts` — the normalized contract

**Build:** Create the file with the exact shapes from the contracts brief §1
— copy verbatim, no additions, no renames:

```ts
import type postgres from "postgres";
export type Sql = postgres.Sql;

// Messages
export type LlmRole = "system" | "user" | "assistant" | "tool_result";
export interface LlmTextBlock { type: "text"; text: string }
export interface LlmToolUseBlock { type: "tool_use"; id: string; name: string; input: unknown }
export type LlmAssistantBlock = LlmTextBlock | LlmToolUseBlock;
export interface LlmToolResultBlock {
  toolUseId: string;
  toolName: string;
  content: string;
  isError: boolean;
}
export interface LlmSystemMessage { role: "system"; content: string }
export interface LlmUserMessage { role: "user"; content: string }
export interface LlmAssistantMessage { role: "assistant"; content: LlmAssistantBlock[] }
export interface LlmToolResultMessage { role: "tool_result"; content: LlmToolResultBlock[] }
export type LlmMessage =
  | LlmSystemMessage | LlmUserMessage | LlmAssistantMessage | LlmToolResultMessage;

// Streaming
export type StopReason = "end" | "tool_use" | "max_tokens" | "error" | "aborted";
export interface LlmUsage {
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
}
export type LlmStreamEvent =
  | { type: "text_delta"; delta: string }
  | { type: "thinking_delta"; delta: string }
  | { type: "tool_call_start"; id: string; name: string }
  | { type: "tool_call_delta"; id: string; delta: string }
  | { type: "tool_call_end"; id: string; name: string; input: unknown }
  | { type: "turn_end"; stopReason: StopReason; usage: LlmUsage };

// Adapter interface — implemented by anthropic.ts and openai-compat.ts
export interface LlmToolDefinition {
  name: string;
  description: string;
  inputSchema: unknown;
}
export interface LlmTurnRequest {
  model: string;
  messages: LlmMessage[];
  tools: LlmToolDefinition[];
  maxTokens?: number;
}
export type LlmAdapter = (request: LlmTurnRequest, signal?: AbortSignal) => AsyncGenerator<LlmStreamEvent>;
```

**Exact files:** Create `src/lib/llm/types.ts`.

**Acceptance criteria:**
- File exports exactly the names above (`LlmRole`, `LlmTextBlock`,
  `LlmToolUseBlock`, `LlmAssistantBlock`, `LlmToolResultBlock`,
  `LlmSystemMessage`, `LlmUserMessage`, `LlmAssistantMessage`,
  `LlmToolResultMessage`, `LlmMessage`, `StopReason`, `LlmUsage`,
  `LlmStreamEvent`, `LlmToolDefinition`, `LlmTurnRequest`, `LlmAdapter`, `Sql`)
  — no extra exports, no renamed fields.
- A throwaway construction of one value per type satisfies its interface with
  no `as any`/`@ts-expect-error` needed (proves the shapes are internally
  consistent — e.g. a `LlmAssistantMessage` built from both block variants).

**Verify:**
Create `tests/llm-types.test.ts` with a single smoke test that constructs one
value of each exported type and asserts basic shape, e.g.:
```ts
import type {
  LlmAssistantMessage, LlmMessage, LlmStreamEvent, LlmToolResultMessage,
} from "@/lib/llm/types";

describe("llm/types", () => {
  it("LlmMessage union covers all four roles with correct content shapes", () => {
    const messages: LlmMessage[] = [
      { role: "system", content: "sys" },
      { role: "user", content: "hi" },
      {
        role: "assistant",
        content: [
          { type: "text", text: "thinking out loud" },
          { type: "tool_use", id: "t1", name: "search_memory", input: { q: "x" } },
        ],
      },
      {
        role: "tool_result",
        content: [{ toolUseId: "t1", toolName: "search_memory", content: "[]", isError: false }],
      },
    ];
    expect(messages).toHaveLength(4);
    const assistant = messages[2] as LlmAssistantMessage;
    expect(assistant.content).toHaveLength(2);
    const toolResult = messages[3] as LlmToolResultMessage;
    expect(toolResult.content[0]?.isError).toBe(false);
  });

  it("LlmStreamEvent union covers all six event types", () => {
    const events: LlmStreamEvent[] = [
      { type: "text_delta", delta: "hi" },
      { type: "thinking_delta", delta: "hmm" },
      { type: "tool_call_start", id: "t1", name: "search_memory" },
      { type: "tool_call_delta", id: "t1", delta: '{"q":' },
      { type: "tool_call_end", id: "t1", name: "search_memory", input: { q: "x" } },
      { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 1, outputTokens: 2, totalTokens: 3 } },
    ];
    expect(events).toHaveLength(6);
  });
});
```
Run: `npx vitest run tests/llm-types.test.ts` — expect PASS (2 tests).

---

### Task 3: Widen `model_calls.call_kind` for `stream_turn`

**Build:** New migration:

`src/migrations/004_model_calls_stream_turn.sql`:
```sql
-- 004_model_calls_stream_turn.sql — R1: allow streamAgentTurn()'s ledger rows.
-- streamAgentTurn (src/lib/model-gateway.ts) records native tool-calling
-- streaming turns with call_kind='stream_turn'. The original CHECK
-- constraint (001_run_ledger_model_gateway.sql) only allows the four kinds
-- callModel() writes — widen it, additive, callModel()'s kinds untouched.

ALTER TABLE model_calls DROP CONSTRAINT IF EXISTS model_calls_call_kind_check;
ALTER TABLE model_calls ADD CONSTRAINT model_calls_call_kind_check
  CHECK (call_kind IN ('generate_text', 'generate_object', 'embed', 'embed_many', 'stream_turn'));
```

**Exact files:** Create `src/migrations/004_model_calls_stream_turn.sql`,
`tests/migrate-stream-turn.test.ts`.

**Acceptance criteria:**
- Running `runMigrations(db)` against a fresh schema (migrations `000`–`004`)
  succeeds with no error.
- `INSERT INTO model_calls (..., call_kind, status, ...) VALUES (..., 'stream_turn', 'running', ...)`
  succeeds after migration (previously would have thrown a check-constraint
  violation).
- Existing `call_kind` values (`generate_text`, `generate_object`, `embed`,
  `embed_many`) still insert successfully (constraint widened, not replaced).
- An invalid `call_kind` (e.g. `'bogus'`) still throws (constraint still
  enforces the allow-list, just a longer one).

**Verify:**
Create `tests/migrate-stream-turn.test.ts`:
```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("004_model_calls_stream_turn migration", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("allows call_kind='stream_turn'", async () => {
    const db = getDb();
    await db`
      INSERT INTO model_calls (task_name, prompt_version, prompt_hash, provider, model, call_kind, status)
      VALUES ('t', '1', 'h', 'anthropic', 'claude-sonnet-4-6', 'stream_turn', 'running')
    `;
    const rows = await db`SELECT call_kind FROM model_calls`;
    expect(rows).toHaveLength(1);
    expect(rows[0]?.call_kind).toBe("stream_turn");
  });

  it("still allows the four original call_kind values", async () => {
    const db = getDb();
    for (const kind of ["generate_text", "generate_object", "embed", "embed_many"]) {
      await db`
        INSERT INTO model_calls (task_name, prompt_version, prompt_hash, provider, model, call_kind, status)
        VALUES ('t', '1', 'h', 'anthropic', 'm', ${kind}, 'running')
      `;
    }
    const rows = await db`SELECT count(*) FROM model_calls`;
    expect(Number(rows[0]?.count)).toBe(4);
  });

  it("still rejects an invalid call_kind", async () => {
    const db = getDb();
    await expect(
      db`
        INSERT INTO model_calls (task_name, prompt_version, prompt_hash, provider, model, call_kind, status)
        VALUES ('t', '1', 'h', 'anthropic', 'm', 'bogus', 'running')
      `,
    ).rejects.toThrow();
  });
});
```
Run:
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/migrate-stream-turn.test.ts
```
Expected: PASS (3 tests). If the first test fails on a constraint-name
mismatch, inspect the actual constraint name via
`SELECT conname FROM pg_constraint WHERE conrelid = 'model_calls'::regclass;`
against a DB migrated only through `003_archived.sql`, and correct the `DROP
CONSTRAINT` name in the migration to match — don't guess a second time,
verify against the live schema.

---

### Task 4: `src/lib/llm/anthropic.ts` — Anthropic adapter

**Build:** Raw `@anthropic-ai/sdk` adapter implementing `LlmAdapter`. Message
conversion, tool conversion, and event mapping below are exact — the SDK's
TypeScript types were inspected directly (`node_modules/@anthropic-ai/sdk`)
to confirm every field name used here; do not substitute different field
names without re-checking the installed package.

**Message conversion** (`messages[0]` is always the system message per the
contract):
- `system` (`messages[0]`) → Anthropic's top-level `system: string` param.
- `user` → `{ role: "user", content: message.content }` (string content is a
  valid `Anthropic.MessageParam.content`).
- `assistant` → `{ role: "assistant", content: blocks }` where each
  `LlmTextBlock` → `{ type: "text", text }` and each `LlmToolUseBlock` →
  `{ type: "tool_use", id, name, input }`.
- `tool_result` → flattened into `{ role: "user", content: blocks }` where
  each `LlmToolResultBlock` → `{ type: "tool_result", tool_use_id: toolUseId, content, is_error: isError }`.

**Tool conversion:** `request.tools.map(t => ({ name: t.name, description: t.description, input_schema: t.inputSchema as Anthropic.Tool["input_schema"] }))`.

**Event mapping** (raw SSE event `type` → `LlmStreamEvent`), from
`client.messages.create({ ..., stream: true }, { signal })` (an
`AsyncIterable<Anthropic.RawMessageStreamEvent>`):

| Raw event | Condition | Emit |
|---|---|---|
| `content_block_start` | `content_block.type === "tool_use"` | `{type:"tool_call_start", id: content_block.id, name: content_block.name}`; reset a per-block JSON accumulator string to `""` and remember `id`/`name` as "current tool" |
| `content_block_start` | `content_block.type` is `"text"` or `"thinking"` | nothing (deltas carry the content) |
| `content_block_delta` | `delta.type === "text_delta"` | `{type:"text_delta", delta: delta.text}` |
| `content_block_delta` | `delta.type === "thinking_delta"` | `{type:"thinking_delta", delta: delta.thinking}` |
| `content_block_delta` | `delta.type === "input_json_delta"` | append `delta.partial_json` to the current tool's JSON accumulator; `{type:"tool_call_delta", id: currentToolId, delta: delta.partial_json}` |
| `content_block_stop` | a tool_use block is currently open | `{type:"tool_call_end", id: currentToolId, name: currentToolName, input: JSON.parse(accumulator) (or {} if accumulator is empty)}`; clear current-tool state |
| `content_block_stop` | no tool_use block open | nothing |
| `message_delta` | always | capture `event.usage.input_tokens`/`event.usage.output_tokens` (both present on `MessageDeltaUsage`) and `event.delta.stop_reason` for the final `turn_end` |
| `message_start`, `message_stop` | always | nothing (no event needed — usage comes from `message_delta`) |

Map `stop_reason` → `StopReason`: `"end_turn"` or `"stop_sequence"` → `"end"`;
`"tool_use"` → `"tool_use"`; `"max_tokens"` → `"max_tokens"`; anything else
(`"pause_turn"`, `"refusal"`, `null`) → `"error"` (R1 never requests server
tools, so `pause_turn` shouldn't occur; treat unexpected values as `"error"`
rather than silently mapping them to `"end"`).

After the `for await` loop over the raw stream completes, yield exactly one
final event: `{ type: "turn_end", stopReason, usage: { inputTokens, outputTokens, totalTokens: inputTokens ?? outputTokens !== null ? (inputTokens ?? 0) + (outputTokens ?? 0) : null } }`.

**Exact files:** Create `src/lib/llm/anthropic.ts`, `tests/llm-anthropic.test.ts`.

```ts
import Anthropic from "@anthropic-ai/sdk";
import type {
  LlmAdapter, LlmMessage, LlmStreamEvent, LlmToolDefinition, LlmTurnRequest,
  LlmUsage, StopReason,
} from "./types";

const DEFAULT_MAX_TOKENS = 8192;

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value?.trim()) {
    throw new Error(`${name} is required for the Anthropic LLM adapter.`);
  }
  return value;
}

// Mirrors model-gateway.ts's resolveAnthropicBaseUrl. Duplicated (not
// imported) to avoid a circular import — model-gateway.ts imports this
// module to pick the default adapter for streamAgentTurn.
function resolveAnthropicBaseUrl(raw?: string): string {
  const configured = raw?.trim();
  if (!configured) return "https://api.anthropic.com/v1";
  const trimmed = configured.replace(/\/+$/, "");
  return /\/v\d+$/.test(trimmed) ? trimmed : `${trimmed}/v1`;
}

function toAnthropicTools(tools: LlmToolDefinition[]): Anthropic.Tool[] {
  return tools.map((tool) => ({
    name: tool.name,
    description: tool.description,
    input_schema: tool.inputSchema as Anthropic.Tool["input_schema"],
  }));
}

function toAnthropicMessages(messages: LlmMessage[]): {
  system: string;
  wireMessages: Anthropic.MessageParam[];
} {
  const first = messages[0];
  if (!first || first.role !== "system") {
    throw new Error("anthropicAdapter: messages[0] must be the system message.");
  }
  const wireMessages: Anthropic.MessageParam[] = [];

  for (const message of messages.slice(1)) {
    if (message.role === "user") {
      wireMessages.push({ role: "user", content: message.content });
    } else if (message.role === "assistant") {
      wireMessages.push({
        role: "assistant",
        content: message.content.map((block): Anthropic.ContentBlockParam =>
          block.type === "text"
            ? { type: "text", text: block.text }
            : { type: "tool_use", id: block.id, name: block.name, input: block.input },
        ),
      });
    } else if (message.role === "tool_result") {
      wireMessages.push({
        role: "user",
        content: message.content.map((block) => ({
          type: "tool_result" as const,
          tool_use_id: block.toolUseId,
          content: block.content,
          is_error: block.isError,
        })),
      });
    }
  }

  return { system: first.content, wireMessages };
}

function mapStopReason(reason: string | null): StopReason {
  switch (reason) {
    case "end_turn":
    case "stop_sequence":
      return "end";
    case "tool_use":
      return "tool_use";
    case "max_tokens":
      return "max_tokens";
    default:
      return "error";
  }
}

export const anthropicAdapter: LlmAdapter = async function* anthropicAdapter(
  request: LlmTurnRequest,
  signal?: AbortSignal,
): AsyncGenerator<LlmStreamEvent> {
  const apiKey = requireEnv("ANTHROPIC_API_KEY");
  const client = new Anthropic({
    apiKey,
    baseURL: resolveAnthropicBaseUrl(process.env.ANTHROPIC_BASE_URL),
  });
  const { system, wireMessages } = toAnthropicMessages(request.messages);

  const stream = await client.messages.create(
    {
      model: request.model,
      max_tokens: request.maxTokens ?? DEFAULT_MAX_TOKENS,
      system,
      messages: wireMessages,
      tools: toAnthropicTools(request.tools),
      stream: true,
    },
    { signal },
  );

  let stopReason: StopReason = "error";
  let usage: LlmUsage = { inputTokens: null, outputTokens: null, totalTokens: null };
  let currentToolId: string | null = null;
  let currentToolName: string | null = null;
  let currentToolJson = "";

  for await (const event of stream) {
    if (event.type === "content_block_start") {
      if (event.content_block.type === "tool_use") {
        currentToolId = event.content_block.id;
        currentToolName = event.content_block.name;
        currentToolJson = "";
        yield { type: "tool_call_start", id: currentToolId, name: currentToolName };
      }
    } else if (event.type === "content_block_delta") {
      if (event.delta.type === "text_delta") {
        yield { type: "text_delta", delta: event.delta.text };
      } else if (event.delta.type === "thinking_delta") {
        yield { type: "thinking_delta", delta: event.delta.thinking };
      } else if (event.delta.type === "input_json_delta") {
        currentToolJson += event.delta.partial_json;
        if (currentToolId) {
          yield { type: "tool_call_delta", id: currentToolId, delta: event.delta.partial_json };
        }
      }
    } else if (event.type === "content_block_stop") {
      if (currentToolId && currentToolName) {
        yield {
          type: "tool_call_end",
          id: currentToolId,
          name: currentToolName,
          input: currentToolJson ? JSON.parse(currentToolJson) : {},
        };
        currentToolId = null;
        currentToolName = null;
        currentToolJson = "";
      }
    } else if (event.type === "message_delta") {
      stopReason = mapStopReason(event.delta.stop_reason);
      usage = {
        inputTokens: event.usage.input_tokens,
        outputTokens: event.usage.output_tokens,
        totalTokens:
          event.usage.input_tokens !== null || event.usage.output_tokens !== null
            ? (event.usage.input_tokens ?? 0) + (event.usage.output_tokens ?? 0)
            : null,
      };
    }
  }

  yield { type: "turn_end", stopReason, usage };
};
```

**Acceptance criteria:**
- A fake raw stream with one `text_delta` (`"Hello"`) then a normal
  `message_delta{stop_reason:"end_turn"}` yields
  `[text_delta("Hello"), turn_end{stopReason:"end"}]` with usage populated
  from the mocked `message_delta.usage`.
- A fake raw stream with a `tool_use` block (`content_block_start` →
  `input_json_delta` chunks `'{"q":' + '"x"}'` → `content_block_stop`) then
  `message_delta{stop_reason:"tool_use"}` yields, in order:
  `tool_call_start`, two `tool_call_delta`s, `tool_call_end` with
  `input: {q:"x"}`, `turn_end{stopReason:"tool_use"}`.
- `stop_reason: "max_tokens"` maps to `StopReason: "max_tokens"`.
- Missing `ANTHROPIC_API_KEY` throws synchronously before any network call
  (assert via `.next()` rejecting, or the generator throwing on first
  iteration) — never a silent no-op.
- `signal` is forwarded: the mocked `client.messages.create` is asserted to
  have been called with a second argument containing the same `signal`
  object passed to `anthropicAdapter`.
- A multi-turn transcript (`system` → `user` → `assistant` with a `tool_use`
  block → `tool_result`) plus a non-empty `tools:` array produces the exact
  wire `system`/`messages`/`tools` shape (`tool_use` block passed through
  verbatim, `tool_result` flattened to a `role: "user"` message with a
  `tool_result` content block, `input_schema` renamed from `inputSchema`) —
  asserted directly against the mocked `client.messages.create` call's first
  argument, not just the inbound event stream.

**Verify:**
Create `tests/llm-anthropic.test.ts` mocking `@anthropic-ai/sdk`:
```ts
import { vi } from "vitest";

const createMock = vi.fn();
vi.mock("@anthropic-ai/sdk", () => ({
  default: vi.fn().mockImplementation(() => ({
    messages: { create: createMock },
  })),
}));

async function* fakeStream(events: unknown[]) {
  for (const e of events) yield e;
}

describe("anthropicAdapter", () => {
  const savedKey = process.env.ANTHROPIC_API_KEY;
  beforeEach(() => {
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";
    createMock.mockReset();
  });
  afterAll(() => {
    if (savedKey === undefined) delete process.env.ANTHROPIC_API_KEY;
    else process.env.ANTHROPIC_API_KEY = savedKey;
  });

  it("normalizes a plain text turn", async () => {
    createMock.mockResolvedValue(
      fakeStream([
        { type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { input_tokens: 5, output_tokens: 3 } },
      ]),
    );
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const events = [];
    for await (const e of anthropicAdapter(
      { model: "claude-sonnet-4-6", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], tools: [] },
    )) {
      events.push(e);
    }
    expect(events).toEqual([
      { type: "turn_end", stopReason: "end", usage: { inputTokens: 5, outputTokens: 3, totalTokens: 8 } },
    ]);
  });

  it("normalizes a tool_use turn with accumulated JSON args", async () => {
    createMock.mockResolvedValue(
      fakeStream([
        { type: "content_block_start", content_block: { type: "tool_use", id: "t1", name: "search_memory" } },
        { type: "content_block_delta", delta: { type: "input_json_delta", partial_json: '{"q":' } },
        { type: "content_block_delta", delta: { type: "input_json_delta", partial_json: '"x"}' } },
        { type: "content_block_stop" },
        { type: "message_delta", delta: { stop_reason: "tool_use" }, usage: { input_tokens: 10, output_tokens: 4 } },
      ]),
    );
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const events = [];
    for await (const e of anthropicAdapter(
      { model: "claude-sonnet-4-6", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], tools: [] },
    )) {
      events.push(e);
    }
    expect(events).toEqual([
      { type: "tool_call_start", id: "t1", name: "search_memory" },
      { type: "tool_call_delta", id: "t1", delta: '{"q":' },
      { type: "tool_call_delta", id: "t1", delta: '"x"}' },
      { type: "tool_call_end", id: "t1", name: "search_memory", input: { q: "x" } },
      { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 10, outputTokens: 4, totalTokens: 14 } },
    ]);
  });

  it("maps max_tokens stop reason", async () => {
    createMock.mockResolvedValue(
      fakeStream([{ type: "message_delta", delta: { stop_reason: "max_tokens" }, usage: { input_tokens: 1, output_tokens: 1 } }]),
    );
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const events = [];
    for await (const e of anthropicAdapter(
      { model: "claude-sonnet-4-6", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], tools: [] },
    )) events.push(e);
    expect(events.at(-1)).toMatchObject({ type: "turn_end", stopReason: "max_tokens" });
  });

  it("throws synchronously when ANTHROPIC_API_KEY is missing", async () => {
    delete process.env.ANTHROPIC_API_KEY;
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const gen = anthropicAdapter(
      { model: "claude-sonnet-4-6", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], tools: [] },
    );
    await expect(gen.next()).rejects.toThrow(/ANTHROPIC_API_KEY/);
  });

  it("forwards the abort signal to the SDK call", async () => {
    createMock.mockResolvedValue(fakeStream([{ type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { input_tokens: 1, output_tokens: 1 } }]));
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const controller = new AbortController();
    const events = [];
    for await (const e of anthropicAdapter(
      { model: "claude-sonnet-4-6", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], tools: [] },
      controller.signal,
    )) events.push(e);
    expect(createMock).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ signal: controller.signal }));
  });

  it("converts a multi-turn transcript with tool_use/tool_result and non-empty tools into the wire request", async () => {
    createMock.mockResolvedValue(
      fakeStream([{ type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { input_tokens: 1, output_tokens: 1 } }]),
    );
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const events = [];
    for await (const e of anthropicAdapter({
      model: "claude-sonnet-4-6",
      messages: [
        { role: "system", content: "s" },
        { role: "user", content: "hi" },
        {
          role: "assistant",
          content: [
            { type: "text", text: "let me check" },
            { type: "tool_use", id: "t1", name: "search_memory", input: { q: "x" } },
          ],
        },
        {
          role: "tool_result",
          content: [{ toolUseId: "t1", toolName: "search_memory", content: "[]", isError: false }],
        },
      ],
      tools: [{ name: "search_memory", description: "search", inputSchema: { type: "object" } }],
    })) events.push(e);

    expect(createMock).toHaveBeenCalledWith(
      expect.objectContaining({
        system: "s",
        tools: [{ name: "search_memory", description: "search", input_schema: { type: "object" } }],
        messages: [
          { role: "user", content: "hi" },
          {
            role: "assistant",
            content: [
              { type: "text", text: "let me check" },
              { type: "tool_use", id: "t1", name: "search_memory", input: { q: "x" } },
            ],
          },
          {
            role: "user",
            content: [{ type: "tool_result", tool_use_id: "t1", content: "[]", is_error: false }],
          },
        ],
      }),
      expect.anything(),
    );
  });
});
```
Run: `npx vitest run tests/llm-anthropic.test.ts` — expect PASS (6 tests).

---

### Task 5: `src/lib/llm/openai-compat.ts` — DeepSeek + OpenAI adapter

**Build:** One `openai` npm client, parameterized per provider via a factory
(see Judgment calls). Confirmed field names from the installed `openai`
package's types — do not substitute different names without re-checking.

**Client construction per provider:**
- `"deepseek"`: `new OpenAI({ apiKey: requireEnv("DEEPSEEK_API_KEY"), baseURL: "https://api.deepseek.com" })`.
- `"openai"`: `new OpenAI({ apiKey: requireEnv("OPENAI_API_KEY") })` (SDK
  default `baseURL` is `https://api.openai.com/v1`).

**Message conversion** — OpenAI carries `system` as an ordinary message role
(no separate top-level param, unlike Anthropic):
- `system` → `{ role: "system", content }`.
- `user` → `{ role: "user", content }`.
- `assistant` → `{ role: "assistant", content: <joined text blocks, or null if none>, tool_calls: <tool_use blocks, or omitted if none> }` where each `LlmToolUseBlock` → `{ id, type: "function", function: { name, arguments: JSON.stringify(input) } }`.
- `tool_result` → **one message per block** (contract judgment call #1 in the
  brief): each `LlmToolResultBlock` → `{ role: "tool", tool_call_id: toolUseId, content }`.

**Tool conversion:** `request.tools.map(t => ({ type: "function", function: { name: t.name, description: t.description, parameters: t.inputSchema } }))`.

**Request:** `client.chat.completions.create({ model, messages, tools, max_tokens: request.maxTokens ?? DEFAULT_MAX_TOKENS, stream: true, stream_options: { include_usage: true } }, { signal })`.

**Event mapping**, iterating the returned `AsyncIterable<ChatCompletionChunk>`
— OpenAI streams don't have an explicit "tool call finished" event the way
Anthropic has `content_block_stop`; a tool call is only known-complete when
the chunk carrying `finish_reason` arrives. Track per-`index` accumulator
state (`id`, `name`, `argsJson`) in a `Map<number, {...}>`:

| Chunk field | Condition | Emit |
|---|---|---|
| `choices[0].delta.content` | non-null/non-empty | `{type:"text_delta", delta: content}` |
| `choices[0].delta.tool_calls[]` | entry has `.id` (first chunk for that `index`) | record `{id, name: entry.function?.name ?? ""}` in the per-index map; `{type:"tool_call_start", id, name}` |
| `choices[0].delta.tool_calls[]` | entry has `.function.arguments` | append to that index's `argsJson`; `{type:"tool_call_delta", id: <that index's id>, delta: entry.function.arguments}` |
| `choices[0].finish_reason` | non-null | for every entry in the per-index map (in index order): `{type:"tool_call_end", id, name, input: JSON.parse(argsJson) (or {} if empty)}`; then `{type:"turn_end", stopReason: <mapped>, usage}` |
| top-level `chunk.usage` | present (only the final chunk with `stream_options.include_usage`) | capture as the turn's usage |

Map `finish_reason` → `StopReason`: `"stop"` → `"end"`; `"tool_calls"` →
`"tool_use"`; `"length"` → `"max_tokens"`; anything else (`"content_filter"`,
`"function_call"`) → `"error"`.

**Exact files:** Create `src/lib/llm/openai-compat.ts`, `tests/llm-openai-compat.test.ts`.

```ts
import OpenAI from "openai";
import type {
  LlmAdapter, LlmMessage, LlmStreamEvent, LlmToolDefinition, LlmTurnRequest,
  LlmUsage, StopReason,
} from "./types";

const DEFAULT_MAX_TOKENS = 8192;

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value?.trim()) {
    throw new Error(`${name} is required for the OpenAI-compatible LLM adapter.`);
  }
  return value;
}

function toOpenAiTools(tools: LlmToolDefinition[]): OpenAI.Chat.ChatCompletionTool[] {
  return tools.map((tool) => ({
    type: "function",
    function: { name: tool.name, description: tool.description, parameters: tool.inputSchema as Record<string, unknown> },
  }));
}

function toOpenAiMessages(messages: LlmMessage[]): OpenAI.Chat.ChatCompletionMessageParam[] {
  const wire: OpenAI.Chat.ChatCompletionMessageParam[] = [];
  for (const message of messages) {
    if (message.role === "system") {
      wire.push({ role: "system", content: message.content });
    } else if (message.role === "user") {
      wire.push({ role: "user", content: message.content });
    } else if (message.role === "assistant") {
      const text = message.content
        .filter((b): b is Extract<typeof b, { type: "text" }> => b.type === "text")
        .map((b) => b.text)
        .join("");
      const toolCalls = message.content
        .filter((b): b is Extract<typeof b, { type: "tool_use" }> => b.type === "tool_use")
        .map((b) => ({
          id: b.id,
          type: "function" as const,
          function: { name: b.name, arguments: JSON.stringify(b.input) },
        }));
      wire.push({
        role: "assistant",
        content: text || null,
        ...(toolCalls.length > 0 ? { tool_calls: toolCalls } : {}),
      });
    } else if (message.role === "tool_result") {
      for (const block of message.content) {
        wire.push({ role: "tool", tool_call_id: block.toolUseId, content: block.content });
      }
    }
  }
  return wire;
}

function mapFinishReason(reason: string | null): StopReason {
  switch (reason) {
    case "stop":
      return "end";
    case "tool_calls":
      return "tool_use";
    case "length":
      return "max_tokens";
    default:
      return "error";
  }
}

export function createOpenAiCompatAdapter(providerName: "deepseek" | "openai"): LlmAdapter {
  return async function* openAiCompatAdapter(
    request: LlmTurnRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<LlmStreamEvent> {
    const client =
      providerName === "deepseek"
        ? new OpenAI({ apiKey: requireEnv("DEEPSEEK_API_KEY"), baseURL: "https://api.deepseek.com" })
        : new OpenAI({ apiKey: requireEnv("OPENAI_API_KEY") });

    const stream = await client.chat.completions.create(
      {
        model: request.model,
        max_tokens: request.maxTokens ?? DEFAULT_MAX_TOKENS,
        messages: toOpenAiMessages(request.messages),
        tools: toOpenAiTools(request.tools),
        stream: true,
        stream_options: { include_usage: true },
      },
      { signal },
    );

    const toolCalls = new Map<number, { id: string; name: string; argsJson: string }>();
    let usage: LlmUsage = { inputTokens: null, outputTokens: null, totalTokens: null };
    let stopReason: StopReason | null = null;

    for await (const chunk of stream) {
      if (chunk.usage) {
        usage = {
          inputTokens: chunk.usage.prompt_tokens ?? null,
          outputTokens: chunk.usage.completion_tokens ?? null,
          totalTokens: chunk.usage.total_tokens ?? null,
        };
      }
      const choice = chunk.choices[0];
      if (!choice) continue;

      if (choice.delta.content) {
        yield { type: "text_delta", delta: choice.delta.content };
      }
      for (const entry of choice.delta.tool_calls ?? []) {
        if (entry.id) {
          const name = entry.function?.name ?? "";
          toolCalls.set(entry.index, { id: entry.id, name, argsJson: "" });
          yield { type: "tool_call_start", id: entry.id, name };
        }
        const args = entry.function?.arguments;
        if (args) {
          const state = toolCalls.get(entry.index);
          if (state) {
            state.argsJson += args;
            yield { type: "tool_call_delta", id: state.id, delta: args };
          }
        }
      }
      if (choice.finish_reason) {
        for (const state of toolCalls.values()) {
          yield {
            type: "tool_call_end",
            id: state.id,
            name: state.name,
            input: state.argsJson ? JSON.parse(state.argsJson) : {},
          };
        }
        stopReason = mapFinishReason(choice.finish_reason);
      }
    }

    yield { type: "turn_end", stopReason: stopReason ?? "error", usage };
  };
}
```

**Acceptance criteria:**
- `createOpenAiCompatAdapter("deepseek")` constructs the client with
  `baseURL: "https://api.deepseek.com"`; `createOpenAiCompatAdapter("openai")`
  constructs it with no `baseURL` override.
- A fake chunk stream with `delta.content` chunks then a final chunk
  (`finish_reason: "stop"`, `usage`) yields `text_delta`s then
  `turn_end{stopReason:"end"}` with the mapped usage.
- A fake chunk stream with `delta.tool_calls[{index:0, id, function:{name}}]`
  then argument chunks then a final chunk (`finish_reason: "tool_calls"`)
  yields `tool_call_start`, `tool_call_delta`(s), `tool_call_end` with parsed
  `input`, then `turn_end{stopReason:"tool_use"}`.
- Two concurrent tool calls (different `index`) accumulate independently and
  both get a `tool_call_end` in index order.
- `finish_reason: "length"` maps to `StopReason: "max_tokens"`.
- Missing `DEEPSEEK_API_KEY` throws synchronously on first iteration; missing
  `OPENAI_API_KEY` (for the `"openai"` factory) throws synchronously on first
  iteration — both cases get their own test, not just the `DEEPSEEK_API_KEY`
  one.
- A multi-turn transcript (`system` → `user` → `assistant` with a `tool_use`
  block → `tool_result`) plus a non-empty `tools:` array produces the exact
  wire `messages`/`tools` shape (assistant `content`/`tool_calls`, one `role:
  "tool"` message per `tool_result` block, `type: "function"` tool defs) —
  asserted directly against the mocked `create()` call's first argument, not
  just the inbound event stream.

**Verify:**
Create `tests/llm-openai-compat.test.ts` mocking `openai`:
```ts
import { vi } from "vitest";

const createMock = vi.fn();
const ctorMock = vi.fn();
vi.mock("openai", () => ({
  default: vi.fn().mockImplementation((opts) => {
    ctorMock(opts);
    return { chat: { completions: { create: createMock } } };
  }),
}));

async function* fakeStream(chunks: unknown[]) {
  for (const c of chunks) yield c;
}

describe("createOpenAiCompatAdapter", () => {
  const savedDeepseek = process.env.DEEPSEEK_API_KEY;
  const savedOpenai = process.env.OPENAI_API_KEY;
  beforeEach(() => {
    process.env.DEEPSEEK_API_KEY = "ds-test";
    process.env.OPENAI_API_KEY = "oa-test";
    createMock.mockReset();
    ctorMock.mockReset();
  });
  afterAll(() => {
    if (savedDeepseek === undefined) delete process.env.DEEPSEEK_API_KEY; else process.env.DEEPSEEK_API_KEY = savedDeepseek;
    if (savedOpenai === undefined) delete process.env.OPENAI_API_KEY; else process.env.OPENAI_API_KEY = savedOpenai;
  });

  it("constructs the deepseek client with the deepseek base URL", async () => {
    createMock.mockResolvedValue(fakeStream([{ choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }]));
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    for await (const _ of adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] })) { /* drain */ }
    expect(ctorMock).toHaveBeenCalledWith(expect.objectContaining({ baseURL: "https://api.deepseek.com" }));
  });

  it("constructs the openai client with no base URL override", async () => {
    createMock.mockResolvedValue(fakeStream([{ choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }]));
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("openai");
    for await (const _ of adapter({ model: "gpt-5", messages: [{ role: "system", content: "s" }], tools: [] })) { /* drain */ }
    expect(ctorMock).toHaveBeenCalledWith(expect.not.objectContaining({ baseURL: expect.anything() }));
  });

  it("normalizes a plain text turn", async () => {
    createMock.mockResolvedValue(
      fakeStream([
        { choices: [{ delta: { content: "Hel" }, finish_reason: null }] },
        { choices: [{ delta: { content: "lo" }, finish_reason: "stop" }], usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 } },
      ]),
    );
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    const events = [];
    for await (const e of adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] })) events.push(e);
    expect(events).toEqual([
      { type: "text_delta", delta: "Hel" },
      { type: "text_delta", delta: "lo" },
      { type: "turn_end", stopReason: "end", usage: { inputTokens: 5, outputTokens: 2, totalTokens: 7 } },
    ]);
  });

  it("normalizes a single tool call across argument chunks", async () => {
    createMock.mockResolvedValue(
      fakeStream([
        { choices: [{ delta: { tool_calls: [{ index: 0, id: "call_1", function: { name: "search_memory", arguments: "" } }] }, finish_reason: null }] },
        { choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '{"q":' } }] }, finish_reason: null }] },
        { choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '"x"}' } }] }, finish_reason: "tool_calls" }], usage: { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 } },
      ]),
    );
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    const events = [];
    for await (const e of adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] })) events.push(e);
    expect(events).toEqual([
      { type: "tool_call_start", id: "call_1", name: "search_memory" },
      { type: "tool_call_delta", id: "call_1", delta: '{"q":' },
      { type: "tool_call_delta", id: "call_1", delta: '"x"}' },
      { type: "tool_call_end", id: "call_1", name: "search_memory", input: { q: "x" } },
      { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 10, outputTokens: 4, totalTokens: 14 } },
    ]);
  });

  it("maps length finish_reason to max_tokens", async () => {
    createMock.mockResolvedValue(fakeStream([{ choices: [{ delta: {}, finish_reason: "length" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }]));
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    const events = [];
    for await (const e of adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] })) events.push(e);
    expect(events.at(-1)).toMatchObject({ type: "turn_end", stopReason: "max_tokens" });
  });

  it("throws synchronously when DEEPSEEK_API_KEY is missing", async () => {
    delete process.env.DEEPSEEK_API_KEY;
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    const gen = adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] });
    await expect(gen.next()).rejects.toThrow(/DEEPSEEK_API_KEY/);
  });

  it("throws synchronously when OPENAI_API_KEY is missing", async () => {
    delete process.env.OPENAI_API_KEY;
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("openai");
    const gen = adapter({ model: "gpt-5", messages: [{ role: "system", content: "s" }], tools: [] });
    await expect(gen.next()).rejects.toThrow(/OPENAI_API_KEY/);
  });

  it("converts a multi-turn transcript with tool_use/tool_result and non-empty tools into the wire request", async () => {
    createMock.mockResolvedValue(
      fakeStream([{ choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }]),
    );
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    for await (const _ of adapter({
      model: "deepseek-chat",
      messages: [
        { role: "system", content: "s" },
        { role: "user", content: "hi" },
        {
          role: "assistant",
          content: [
            { type: "text", text: "let me check" },
            { type: "tool_use", id: "t1", name: "search_memory", input: { q: "x" } },
          ],
        },
        {
          role: "tool_result",
          content: [{ toolUseId: "t1", toolName: "search_memory", content: "[]", isError: false }],
        },
      ],
      tools: [{ name: "search_memory", description: "search", inputSchema: { type: "object" } }],
    })) { /* drain */ }

    expect(createMock).toHaveBeenCalledWith(
      expect.objectContaining({
        messages: [
          { role: "system", content: "s" },
          { role: "user", content: "hi" },
          {
            role: "assistant",
            content: "let me check",
            tool_calls: [{ id: "t1", type: "function", function: { name: "search_memory", arguments: '{"q":"x"}' } }],
          },
          { role: "tool", tool_call_id: "t1", content: "[]" },
        ],
        tools: [{ type: "function", function: { name: "search_memory", description: "search", parameters: { type: "object" } } }],
      }),
      expect.anything(),
    );
  });
});
```
Run: `npx vitest run tests/llm-openai-compat.test.ts` — expect PASS (8 tests).

---

### Task 6: `model-gateway.ts` — `streamAgentTurn()`

**Build:** Extend `src/lib/model-gateway.ts` additively:

1. Widen `ModelCallKind`: `"generate_text" | "generate_object" | "embed" | "embed_many" | "stream_turn"`.
2. Export the two currently-private helpers and widen their parameter type
   so `streamAgentTurn` can reuse them (both functions only inspect
   `.kind`/`.providerName`/`.model`, so the widened type is a safe supertype
   of `CallModelInput` — no behavior change for existing callers):
   ```ts
   interface ProviderResolutionInput {
     kind: string;
     providerName?: string;
     model?: string;
   }
   export function resolveProviderName(input: ProviderResolutionInput): string { /* body unchanged */ }
   export function resolveModel(input: ProviderResolutionInput): string {
     if (input.model?.trim()) {
       return input.model;
     }
     if (input.kind === "embed" || input.kind === "embed_many") {
       return process.env.SOURCECADO_EMBEDDING_MODEL || "text-embedding-3-small";
     }
     if (process.env.SOURCECADO_GENERATION_MODEL?.trim()) {
       return process.env.SOURCECADO_GENERATION_MODEL;
     }
     const providerName = resolveProviderName(input);
     if (providerName === "anthropic") return "claude-sonnet-4-6";
     if (providerName === "openai") {
       throw new ModelGatewayError(
         "config_error",
         'streamAgentTurn requires an explicit model when providerName is "openai" (no default model is assumed).',
       );
     }
     return "deepseek-chat";
   }
   ```
   (Change their existing `input: CallModelInput` parameter type to
   `ProviderResolutionInput` — `CallModelInput` structurally satisfies it, so
   every existing call site keeps compiling with no other edits. **This one
   line of behavior does change** — the previous fallback ternary only
   distinguished `"anthropic"` from "everything else" and silently returned
   `"deepseek-chat"` for `providerName: "openai"` with no explicit model. That
   was always latent (both for `callModel` and the new `streamAgentTurn`); it
   becomes reachable the moment `"openai"` is a real third streaming provider,
   so it's fixed here rather than inherited silently. `callModel`'s existing
   call sites are unaffected: none of them pass `providerName: "openai"` for a
   generation kind today.)
3. Import `anthropicAdapter` from `./llm/anthropic` and
   `createOpenAiCompatAdapter` from `./llm/openai-compat`; import
   `LlmAdapter`, `LlmAssistantBlock`, `LlmAssistantMessage`, `LlmMessage`,
   `LlmStreamEvent`, `LlmToolDefinition`, `LlmUsage`, `StopReason` from
   `./llm/types`.
4. Add the new exported interfaces and function, exactly matching the
   contracts brief §2 signature:
   ```ts
   export interface ModelGatewayTrace { runId: number; parentStepId?: number | null }
   // (already exists — no change)

   export interface StreamAgentTurnInput {
     taskName: string;
     promptVersion: string;
     providerName?: string;
     model?: string;
     messages: LlmMessage[];
     tools: LlmToolDefinition[];
     trace: ModelGatewayTrace;
     maxTokens?: number;
     signal?: AbortSignal;
     adapter?: LlmAdapter;
   }

   export interface LlmTurnOutcome {
     message: LlmAssistantMessage;
     stopReason: StopReason;
     usage: LlmUsage;
     modelCallId: number;
   }

   function pickAdapter(providerName: string): LlmAdapter {
     if (providerName === "anthropic") return anthropicAdapter;
     if (providerName === "deepseek") return createOpenAiCompatAdapter("deepseek");
     if (providerName === "openai") return createOpenAiCompatAdapter("openai");
     throw new ModelGatewayError(
       "config_error",
       `Unsupported streaming provider: ${providerName}. Set SOURCECADO_GENERATION_PROVIDER to "anthropic" or "deepseek".`,
     );
   }

   export async function* streamAgentTurn(
     db: Sql,
     input: StreamAgentTurnInput,
   ): AsyncGenerator<LlmStreamEvent, LlmTurnOutcome, void> {
     const providerName = resolveProviderName({ kind: "stream_turn", providerName: input.providerName, model: input.model });
     const model = resolveModel({ kind: "stream_turn", providerName: input.providerName, model: input.model });
     const adapter = input.adapter ?? pickAdapter(providerName);

     const requestPayload = {
       messages: input.messages,
       toolNames: input.tools.map((t) => t.name),
       maxTokens: input.maxTokens ?? null,
     };
     const promptHash = createHash("sha256").update(JSON.stringify(requestPayload)).digest("hex");

     const runStep = await startRunStep(db, {
       runId: input.trace.runId,
       parentStepId: input.trace.parentStepId ?? null,
       stepKind: "model",
       name: input.taskName,
       input: requestPayload,
     });

     const blocks: LlmAssistantBlock[] = [];
     let pendingText = "";
     let usage: LlmUsage = { inputTokens: null, outputTokens: null, totalTokens: null };
     let stopReason: StopReason = "error";
     // Declared outside try so the catch handler can still fail the ledger
     // row if the INSERT itself is what throws (null means "never inserted").
     let modelCallId: number | null = null;

     const flushPendingText = () => {
       if (pendingText) {
         blocks.push({ type: "text", text: pendingText });
         pendingText = "";
       }
     };

     try {
       const [modelCallRow] = await db`
         INSERT INTO model_calls (
           run_id, run_step_id, task_name, prompt_version, prompt_hash,
           provider, model, call_kind, status, request_json
         )
         VALUES (
           ${input.trace.runId}, ${runStep.id}, ${input.taskName}, ${input.promptVersion}, ${promptHash},
           ${providerName}, ${model}, 'stream_turn', 'running', ${toJson(db, requestPayload)}
         )
         RETURNING id
       `;
       modelCallId = Number(modelCallRow.id);

       const events = adapter({ model, messages: input.messages, tools: input.tools, maxTokens: input.maxTokens }, input.signal);
       for await (const event of events) {
         if (event.type === "text_delta") {
           pendingText += event.delta;
         } else if (event.type === "tool_call_start") {
           flushPendingText();
         } else if (event.type === "tool_call_end") {
           blocks.push({ type: "tool_use", id: event.id, name: event.name, input: event.input });
         } else if (event.type === "turn_end") {
           flushPendingText();
           stopReason = event.stopReason;
           usage = event.usage;
         }
         yield event;
       }

       const message: LlmAssistantMessage = { role: "assistant", content: blocks };
       const responsePayload = { message, stopReason };

       await db`
         UPDATE model_calls
         SET status = 'succeeded',
             response_json = ${toJson(db, responsePayload)},
             usage_json = ${toJson(db, usage)},
             input_tokens = ${usage.inputTokens},
             output_tokens = ${usage.outputTokens},
             total_tokens = ${usage.totalTokens},
             completed_at = now(),
             updated_at = now()
         WHERE id = ${modelCallId}
       `;
       await finishRunStep(db, { runStepId: runStep.id, output: responsePayload });

       return { message, stopReason, usage, modelCallId };
     } catch (error) {
       const aborted = input.signal?.aborted === true || (error instanceof Error && error.name === "AbortError");
       const code = aborted ? "aborted" : "provider_error";
       const message = error instanceof Error ? error.message : String(error);

       if (modelCallId !== null) {
         await db`
           UPDATE model_calls
           SET status = 'failed', error_type = ${code}, error_message = ${message}, completed_at = now(), updated_at = now()
           WHERE id = ${modelCallId}
         `;
       }
       await failRunStep(db, { runStepId: runStep.id, errorType: code, errorMessage: message });

       throw new ModelGatewayError(code, message, { cause: error });
     }
   }
   ```
   `toJson` is the existing private helper already defined in
   `model-gateway.ts` — reuse it, don't redefine it.

**Exact files:** Modify `src/lib/model-gateway.ts`; create
`tests/model-gateway-stream.test.ts`.

**Acceptance criteria:**
- A fake `adapter` yielding `[text_delta("Echoed hi"), turn_end{stopReason:"end", usage:{...}}]`:
  `streamAgentTurn`'s return value has `message: {role:"assistant", content:[{type:"text", text:"Echoed hi"}]}`,
  `stopReason: "end"`; the `model_calls` row is `status:'succeeded'`,
  `call_kind:'stream_turn'`, with matching `input_tokens`/`output_tokens`;
  the parent `run_steps` row (`stepKind:'model'`) is `status:'succeeded'`.
- A fake `adapter` yielding a `tool_call_start`/`tool_call_end` pair before
  `turn_end{stopReason:"tool_use"}`: the returned `message.content` has a
  `{type:"tool_use", id, name, input}` block; no `text` block when no
  `text_delta` was yielded.
- Every event yielded by the fake adapter is also yielded by
  `streamAgentTurn` itself, unmodified and in the same order (pass-through
  verified by collecting `for await` output from `streamAgentTurn` and
  comparing to the fake adapter's event list).
- A fake `adapter` that throws (non-abort) mid-stream: `streamAgentTurn`
  throws a `ModelGatewayError` with `code: "provider_error"`; the
  `model_calls` row is `status:'failed'`, `error_type:'provider_error'`; the
  `run_steps` row is `status:'failed'`.
- A fake `adapter` that throws while `input.signal.aborted` is `true`:
  `streamAgentTurn` throws `ModelGatewayError` with `code: "aborted"`; the
  `model_calls` row is `status:'failed'`, `error_type:'aborted'`.
- `input.adapter` (test seam) is used verbatim when provided — `pickAdapter`
  is never called (assert by passing a provider name `pickAdapter` would
  reject, e.g. `"bogus"`, alongside an explicit `adapter`, and confirming no
  throw).
- `pickAdapter` throws `ModelGatewayError{code:"config_error"}` for an
  unrecognized `providerName` when no `adapter` override is given.
- The `INSERT INTO model_calls` sits inside the `try` block (not before it),
  closing over the same `runStep.id` the `catch` handler uses, so a failure
  during that INSERT still reaches `failRunStep` instead of leaving the
  `run_steps` row stuck in `status='running'` forever.
- `streamAgentTurn` called with `providerName: "openai"` and no explicit
  `model` (and no `SOURCECADO_GENERATION_MODEL`) throws
  `ModelGatewayError{code:"config_error"}` instead of silently resolving to
  `model: "deepseek-chat"` against OpenAI's API.

**Verify:**
Create `tests/model-gateway-stream.test.ts` following the existing
`tests/model-gateway.test.ts` pattern (`resetLedgerTables` + `runMigrations`
in `beforeEach`, real Postgres, fake `LlmAdapter` functions as the test
seam — no real SDK calls):
```ts
import { closeDb, getDb } from "@/lib/db";
import { startRun } from "@/lib/ledger";
import { runMigrations } from "@/lib/migrate";
import { ModelGatewayError, streamAgentTurn } from "@/lib/model-gateway";
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

function fakeAdapter(events: LlmStreamEvent[]): LlmAdapter {
  return async function* () {
    for (const e of events) yield e;
  };
}

async function collect(
  gen: AsyncGenerator<LlmStreamEvent, unknown, void>,
): Promise<{ events: LlmStreamEvent[]; result: unknown }> {
  const events: LlmStreamEvent[] = [];
  let next = await gen.next();
  while (!next.done) {
    events.push(next.value);
    next = await gen.next();
  }
  return { events, result: next.value };
}

describe("streamAgentTurn", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("records a succeeded text turn and returns the accumulated message", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const adapter = fakeAdapter([
      { type: "text_delta", delta: "Echoed hi" },
      { type: "turn_end", stopReason: "end", usage: { inputTokens: 5, outputTokens: 3, totalTokens: 8 } },
    ]);

    const { events, result } = await collect(
      streamAgentTurn(db, {
        taskName: "chat_turn",
        promptVersion: "1",
        messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
        tools: [],
        trace: { runId: run.id },
        adapter,
      }),
    );

    expect(events).toHaveLength(2);
    expect(result).toMatchObject({
      stopReason: "end",
      message: { role: "assistant", content: [{ type: "text", text: "Echoed hi" }] },
    });

    const rows = await db`SELECT * FROM model_calls WHERE run_id = ${run.id}`;
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      call_kind: "stream_turn",
      status: "succeeded",
      input_tokens: 5,
      output_tokens: 3,
      total_tokens: 8,
    });

    const steps = await db`SELECT * FROM run_steps WHERE run_id = ${run.id}`;
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ step_kind: "model", status: "succeeded" });
  });

  it("accumulates a tool_use turn with no text block", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const adapter = fakeAdapter([
      { type: "tool_call_start", id: "t1", name: "search_memory" },
      { type: "tool_call_delta", id: "t1", delta: '{"q":"x"}' },
      { type: "tool_call_end", id: "t1", name: "search_memory", input: { q: "x" } },
      { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } },
    ]);

    const { result } = await collect(
      streamAgentTurn(db, {
        taskName: "chat_turn",
        promptVersion: "1",
        messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
        tools: [],
        trace: { runId: run.id },
        adapter,
      }),
    );

    expect(result).toMatchObject({
      stopReason: "tool_use",
      message: { content: [{ type: "tool_use", id: "t1", name: "search_memory", input: { q: "x" } }] },
    });
  });

  it("marks the ledger failed with provider_error on a non-abort throw", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const adapter: LlmAdapter = async function* () {
      throw new Error("boom");
    };

    const gen = streamAgentTurn(db, {
      taskName: "chat_turn",
      promptVersion: "1",
      messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
      tools: [],
      trace: { runId: run.id },
      adapter,
    });

    await expect(collect(gen)).rejects.toThrow(ModelGatewayError);
    const rows = await db`SELECT * FROM model_calls WHERE run_id = ${run.id}`;
    expect(rows[0]).toMatchObject({ status: "failed", error_type: "provider_error" });
    const steps = await db`SELECT * FROM run_steps WHERE run_id = ${run.id}`;
    expect(steps[0]).toMatchObject({ status: "failed", error_type: "provider_error" });
  });

  it("marks the ledger failed with aborted when the signal fired", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const controller = new AbortController();
    controller.abort();
    const adapter: LlmAdapter = async function* () {
      throw new Error("aborted mid-stream");
    };

    const gen = streamAgentTurn(db, {
      taskName: "chat_turn",
      promptVersion: "1",
      messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
      tools: [],
      trace: { runId: run.id },
      adapter,
      signal: controller.signal,
    });

    await expect(collect(gen)).rejects.toThrow(ModelGatewayError);
    const rows = await db`SELECT * FROM model_calls WHERE run_id = ${run.id}`;
    expect(rows[0]).toMatchObject({ status: "failed", error_type: "aborted" });
  });

  it("uses the adapter test seam verbatim, bypassing pickAdapter", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const adapter = fakeAdapter([{ type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } }]);

    const { result } = await collect(
      streamAgentTurn(db, {
        taskName: "chat_turn",
        promptVersion: "1",
        providerName: "bogus-provider",
        messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
        tools: [],
        trace: { runId: run.id },
        adapter,
      }),
    );
    expect(result).toMatchObject({ stopReason: "end" });
  });

  it("pickAdapter throws config_error for an unrecognized provider with no adapter override", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const gen = streamAgentTurn(db, {
      taskName: "chat_turn",
      promptVersion: "1",
      providerName: "bogus-provider",
      messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
      tools: [],
      trace: { runId: run.id },
    });
    await expect(gen.next()).rejects.toThrow(/Unsupported streaming provider/);
  });

  it("throws config_error for providerName openai with no explicit model", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "t" });
    const adapter = fakeAdapter([{ type: "turn_end", stopReason: "end", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } }]);

    const gen = streamAgentTurn(db, {
      taskName: "chat_turn",
      promptVersion: "1",
      providerName: "openai",
      messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }],
      tools: [],
      trace: { runId: run.id },
      adapter,
    });
    await expect(gen.next()).rejects.toThrow(/requires an explicit model/);
  });
});
```
Run:
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/model-gateway-stream.test.ts
```
Expected: PASS (7 tests).

---

### Task 7: Full verification + cleanup pass

**Build:** No new behavior. One cleanup-only pass over this slice's own
additions: confirm no orphaned imports, no leftover `console.log`, and that
`llm/anthropic.ts`/`llm/openai-compat.ts` don't accidentally import from
`model-gateway.ts` (the circularity this plan deliberately avoids — see
Judgment calls).

**Exact files:** None new; review only.

**Acceptance criteria:**
- `npx tsc --noEmit -p tsconfig.json` (or the project's existing typecheck
  path) reports no new errors.
- `npm run lint` — no new warnings/errors in `src/lib/llm/*` or
  `src/lib/model-gateway.ts`.
- Full suite green, including every pre-existing test file (333+ tests from
  the spec's baseline, plus this slice's new tests).
- `grep -rn "from \"\.\./model-gateway\"\|from \"@/lib/model-gateway\"" src/lib/llm/` returns nothing (confirms no circular import was introduced).
- `ai`/`@ai-sdk/*` are still present in `package.json` (R1 is additive only —
  R8 removes them later).

**Verify:**
```bash
cd /Users/fisher/Documents/GitHub2026/Sourcecado
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run
npm run lint
npx tsc --noEmit -p tsconfig.json
grep -rn "model-gateway" src/lib/llm/ || echo "no circular import"
```
Expected: all green; grep prints "no circular import".

## Tests

New test files (all under `tests/`, matching the repo's existing flat
layout):

| File | Covers |
|---|---|
| `tests/llm-types.test.ts` | Contract shapes compile and round-trip through TS's structural typing |
| `tests/migrate-stream-turn.test.ts` | `call_kind='stream_turn'` allowed; existing four kinds still allowed; invalid kind still rejected |
| `tests/llm-anthropic.test.ts` | Event normalization (text, tool_use w/ JSON accumulation, max_tokens), missing-key error, signal forwarding, outbound message/tool conversion (assistant tool_use + tool_result + non-empty tools asserted against the mocked `create()` call) |
| `tests/llm-openai-compat.test.ts` | Provider-specific client construction (deepseek base URL vs openai default), event normalization (text, tool_calls w/ per-index accumulation, length→max_tokens), missing-key error (both `DEEPSEEK_API_KEY` and `OPENAI_API_KEY`), outbound message/tool conversion asserted against the mocked `create()` call |
| `tests/model-gateway-stream.test.ts` | Ledger discipline (running→succeeded/failed rows, run_steps nesting), message accumulation (text-only, tool_use-only), provider_error vs aborted classification, adapter test seam, `pickAdapter` config_error, `resolveModel` config_error for `providerName: "openai"` with no explicit model |

No changes to any existing test file — R1 is additive only, so
`tests/model-gateway.test.ts` and `tests/model-gateway-anthropic.test.ts`
(both exercising `callModel()`) are untouched and must stay green.

## Decision footer

- **What changed:** New `src/lib/llm/` module (contract + two raw-SDK
  adapters), one new migration widening `model_calls.call_kind`, and an
  additive `streamAgentTurn()` export on `model-gateway.ts`. `callModel()`
  and its four kinds are byte-for-byte unchanged.
- **How it's verified:** Task-level `npx vitest run <file>` commands above,
  plus Task 7's full-suite + lint + typecheck + circular-import grep.
- **Remaining gap:** Nothing in this slice calls `streamAgentTurn()` from a
  real loop yet — that's R2. Apollo/web-search tools, chat sessions, and the
  AI-SDK rip-out are untouched (out of scope per the spec's slice
  boundaries).
- **Decision:** ship now — unblocks R2 (loop) and R3 (tool orchestrator),
  both of which depend on `src/lib/llm/types.ts`.
- **Next step:** hand off to R2's plan author with this slice merged; flag
  the `004_model_calls_stream_turn.sql` numbering deviation to whoever writes
  R6's plan so it claims `005_chat_sessions.sql` instead of the brief's
  `004_chat_sessions.sql`.

## Eng Review (2026-07-14)

**Method:** Read the full plan, the contracts brief (`2026-07-14-r-contracts-brief.md`),
and the real code it touches or extends (`src/lib/model-gateway.ts`,
`src/lib/ledger.ts`, `src/lib/migrate.ts`, `src/migrations/001_run_ledger_model_gateway.sql`,
`tests/model-gateway.test.ts`, `tests/model-gateway-anthropic.test.ts`,
`package.json`, `vitest.config.ts`). Confirmed `@anthropic-ai/sdk` and `openai`
are not yet installed in this repo, then pulled the exact pinned versions
(`0.111.0` / `6.47.0`, both real, current npm releases) via `npm pack` and
diffed the plan's raw-event field names against the installed `.d.ts` files
line by line (`RawMessageStreamEvent`, `MessageDeltaUsage`, `RawContentBlockDelta`,
`ChatCompletionChunk`, `ChatCompletionChunk.Choice.Delta.ToolCall`). Every
field name, event-type literal, and stop-reason mapping the plan asserts
against both SDKs checked out exactly as written — this is unusually
well-grounded technical spec work, not guessed-at API shape.

### VERDICT: approve (revised)

Architecture is sound and matches the contracts brief closely (types.ts is a
verbatim copy, `StreamAgentTurnInput`/`LlmTurnOutcome` match §2 exactly,
constraint-name assumption is verified live by Task 3's own test, not asserted
blind). The four must-fix items below have all been applied directly to the
plan doc — none required re-architecting.

### Must-fix

1. **RESOLVED.** Task 5's Verify block now includes
   `it("throws synchronously when OPENAI_API_KEY is missing", ...)` using
   `createOpenAiCompatAdapter("openai")`, alongside the outbound-conversion
   test added for item 2. The Run line's expected count is corrected (now 8
   `it()` blocks, recounted against the actual code block).

2. **RESOLVED.** Task 4 (`tests/llm-anthropic.test.ts`) and Task 5
   (`tests/llm-openai-compat.test.ts`) each gained a
   "converts a multi-turn transcript with tool_use/tool_result and non-empty
   tools into the wire request" test: a `system` → `user` → `assistant` (with
   a `tool_use` block) → `tool_result` transcript plus a non-empty `tools:`
   array, asserting the mocked `create()` call's first argument matches the
   expected wire shape exactly (Anthropic's `system`/`tools`/`messages` with
   `input_schema`/`tool_result`/`is_error`; OpenAI's `tool_calls`/`role:"tool"`
   messages/`type:"function"` tools). `toAnthropicMessages`'s and
   `toOpenAiMessages`'s assistant/tool_result branches and both tool-conversion
   functions now have coverage. Test counts and acceptance criteria updated to
   match (Task 4: 6 tests; Task 5: 8 tests).

3. **RESOLVED.** Task 6's code moves the `INSERT INTO model_calls` inside the
   `try` block; `modelCallId` is now declared `let modelCallId: number | null
   = null` above the `try` and assigned inside it, so the `catch` handler can
   still reach `runStep.id` (unchanged — it was always outside `try`, matching
   `callModel`'s own pre-existing gap for `startRunStep`, which this plan does
   not additionally scope in) while guarding the `model_calls` `UPDATE` on
   `modelCallId !== null` — a failure during the INSERT itself now still
   drives `failRunStep`, closing the orphaned-`run_steps`-row gap. Acceptance
   criteria updated with an explicit bullet.

4. **RESOLVED.** `resolveModel()`'s body (in the Task 6 Build section) now
   branches explicitly on `providerName === "anthropic"` /
   `providerName === "openai"` / else, and throws
   `ModelGatewayError("config_error", ...)` when `providerName` resolves to
   `"openai"` with no explicit `model` and no `SOURCECADO_GENERATION_MODEL` —
   instead of silently falling through to `"deepseek-chat"`. Confirmed no
   existing `callModel()` call site passes `providerName: "openai"` for a
   generation kind today, so this is safe for the shared helper. A new test
   ("throws config_error for providerName openai with no explicit model") was
   added to `tests/model-gateway-stream.test.ts`, bringing that file's count
   to 7.

### Notes (should-fix, non-blocking)

- **Migration-numbering coordination is only recorded as prose in this plan.**
  The `004_model_calls_stream_turn.sql` vs. R6's brief-assigned
  `004_chat_sessions.sql` collision is called out correctly, but the fix lives
  entirely inside this plan's "Judgment calls" section. Recommend also
  editing the contracts brief itself (§6/§7) now, while this plan is fresh, so
  whoever writes R6's plan can't miss it by only reading their own doc.
- **`thinking_delta` is implemented but never exercised by a test** (Task 4).
  Low risk since the feature is inert until a caller requests extended
  thinking, but a one-line test (`content_block_delta` with
  `delta.type: "thinking_delta"` → `{type:"thinking_delta", delta}`) costs
  almost nothing and matches the stated test-coverage bar.
- **File-count smell check:** this plan touches/creates 12 files (3 new
  `src/lib/llm/*` modules, 1 migration, 1 extended file, 5 new test files,
  `package.json`/`package-lock.json`), above the skill's 8-file complexity
  trigger. Reviewed explicitly: the count is dominated by test files
  mandated by the contracts brief's own file-ownership table (§7) and the
  "well-tested, not too few" preference, not scope creep — the plan already
  correctly excludes `agent-loop.ts`/`harness.ts`/`tools/orchestrator.ts`.
  No reduction recommended.
- All SQL/constraint-name claims, Anthropic/OpenAI SDK field names, event-type
  literals, and stop/finish-reason mappings were verified against the actual
  installed package versions and the actual `001_run_ledger_model_gateway.sql`
  — no discrepancies found beyond the four items above.

NO UNRESOLVED DECISIONS
