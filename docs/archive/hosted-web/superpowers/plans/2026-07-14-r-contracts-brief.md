# Runtime Solidification — Shared Contracts Brief

Date: 2026-07-14
Status: binding for all R0–R9 slice plans
Source: `docs/superpowers/specs/2026-07-14-runtime-solidification-sprint-spec.md` ("Decisions locked" is non-negotiable), `findings.md`

Every R-slice implementation plan MUST conform to the shapes and file ownership
below. If a slice's plan needs to deviate, that deviation must be called out
explicitly in that plan's own doc and flagged back to this brief — don't
silently diverge.

---

## 1. Normalized LLM contract — `src/lib/llm/types.ts` (new)

```ts
import type postgres from "postgres";
export type Sql = postgres.Sql;

// Messages
export type LlmRole = "system" | "user" | "assistant" | "tool_result";
export interface LlmTextBlock { type: "text"; text: string }
export interface LlmToolUseBlock { type: "tool_use"; id: string; name: string; input: unknown } // input: already parsed, never a JSON string
export type LlmAssistantBlock = LlmTextBlock | LlmToolUseBlock;
export interface LlmToolResultBlock {
  toolUseId: string; // matches LlmToolUseBlock.id
  toolName: string;
  content: string; // already truncated/formatted by the R3 orchestrator
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
  | { type: "tool_call_delta"; id: string; delta: string } // partial JSON args, provider-native chunking
  | { type: "tool_call_end"; id: string; name: string; input: unknown } // input fully parsed here
  | { type: "turn_end"; stopReason: StopReason; usage: LlmUsage };

// Adapter interface — implemented by anthropic.ts and openai-compat.ts
export interface LlmToolDefinition {
  name: string;
  description: string;
  inputSchema: unknown; // JSON Schema, from z.toJSONSchema(tool.argsSchema)
}
export interface LlmTurnRequest {
  model: string;
  messages: LlmMessage[]; // first element is always the system message
  tools: LlmToolDefinition[];
  maxTokens?: number;
}
// Function, not a class — matches the ModelGatewayProvider idiom already in model-gateway.ts
export type LlmAdapter = (request: LlmTurnRequest, signal?: AbortSignal) => AsyncGenerator<LlmStreamEvent>;
```

**Judgment calls:**
- `tool_result` is its own `LlmMessage` role (not folded into `user` like the
  Anthropic wire format) so the loop/ledger never see provider-specific
  shapes. `anthropic.ts` flattens it into a `user` message with
  `tool_result` blocks; `openai-compat.ts` flattens each block into its own
  `role: "tool"` message. Adapter-internal only.
- System is carried as `messages[0]` (not a separate top-level param) so
  `messages[]` is the single source of truth end to end (matches R2's "while
  over messages[]"). Each adapter pulls it into whatever its provider expects.
- `LlmUsage` omits `raw` (unlike `NormalizedUsage` in `model-gateway.ts`) —
  adapters populate the three normalized numbers directly; `streamAgentTurn`
  (§2) wraps them with `raw` before writing `model_calls`.

---

## 2. Gateway streaming entry — `src/lib/model-gateway.ts` (extend, don't rewrite)

```ts
export interface StreamAgentTurnInput {
  taskName: string;
  promptVersion: string;
  providerName?: string; // "anthropic" | "deepseek" | "openai" — same resolution as callModel
  model?: string;
  messages: LlmMessage[];
  tools: LlmToolDefinition[];
  trace: ModelGatewayTrace; // { runId, parentStepId? } — required, streaming always traces
  maxTokens?: number;
  signal?: AbortSignal;
  adapter?: LlmAdapter; // test seam, mirrors callModel's `provider?: ModelGatewayProvider`
}

export interface LlmTurnOutcome {
  message: LlmAssistantMessage; // accumulated text + tool_use blocks for this turn
  stopReason: StopReason;
  usage: LlmUsage;
  modelCallId: number;
}

export async function* streamAgentTurn(
  db: Sql,
  input: StreamAgentTurnInput,
): AsyncGenerator<LlmStreamEvent, LlmTurnOutcome, void>
```

Ledger discipline mirrors `callModel` exactly: resolve `providerName`/`model`
via the same `resolveProviderName`/`resolveModel`; `INSERT INTO model_calls
(... call_kind='stream_turn', status='running')` before the first event is
yielded; pick the adapter (`input.adapter` test seam, else the real
`anthropic.ts`/`openai-compat.ts` module keyed off `providerName`, mirroring
`generationModel()`'s branch) and forward every yielded `LlmStreamEvent`
straight through, unbuffered, while accumulating `text_delta`/`tool_call_end`
into the final `LlmAssistantMessage`; on `turn_end` (or completion) `UPDATE
model_calls SET status='succeeded', usage_json=..., input/output/total_tokens
..., completed_at=now()` from `LlmUsage`; on a thrown error or fired
`AbortSignal`, `UPDATE ... status='failed', error_type=...` (`"aborted"` vs
`"provider_error"`, same `ModelGatewayError` wrapping `callModel` uses).
The generator `return`s an `LlmTurnOutcome` (typed generator return) so
`agent-loop.ts` gets the accumulated assistant message without re-deriving it
from raw events.

`callModel`'s existing kinds (`generate_text`/`generate_object`/`embed`/
`embed_many`) are untouched by R1 — R8 migrates their internals later.
`ModelCallKind` gains one new literal, `"stream_turn"`.

---

## 3. Loop module boundary — `src/lib/agent-loop.ts` (new)

```ts
export interface AgentLoopInput {
  messages: LlmMessage[]; // full transcript so far; messages[0] is the system message
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  maxSteps?: number; // default 8, same as today
  db: Sql;
  runId: number;
  parentStepId: number; // the "agent" run step this loop's model/tool steps nest under
  provider?: string; // "anthropic" | "deepseek" — passed through to streamAgentTurn
  adapter?: LlmAdapter; // test seam, passed through to streamAgentTurn
  signal?: AbortSignal;
  onEvent?: (event: AgentLoopEvent) => void | Promise<void>; // awaited between steps
}
export type AgentLoopEvent =
  | { type: "llm"; event: LlmStreamEvent }
  | { type: "tool_start"; id: string; name: string; input: unknown }
  | { type: "tool_end"; id: string; name: string; result: ToolExecutionResult };
export interface AgentLoopResult {
  status: "succeeded" | "failed";
  messages: LlmMessage[]; // full updated transcript — persist (R6) or thread into next turn
  finalText?: string; // set when stopReason === "end"
  stopReason: StopReason;
  steps: number;
}
export async function runAgentLoop(input: AgentLoopInput): Promise<AgentLoopResult>
```

**Loop body** (the ~200-line `while` the spec describes): `for (step = 1; step
<= maxSteps; step++)` calls `streamAgentTurn`, forwards every yielded event
through `onEvent({type:"llm", event})` (awaited, so a streaming consumer
flushes before the next turn), and collects the `LlmTurnOutcome`. Append its
assistant `message` to `messages[]`. If `stopReason !== "tool_use"`, stop:
`"end"` → `succeeded` with `finalText` = concatenated text blocks;
`"max_tokens"`/`"error"`/`"aborted"` → `failed` (synthetic handling below). If
`"tool_use"`: for each `tool_use` block call `executeTool()` (§4) —
`onEvent({type:"tool_start"})` before, `{type:"tool_end"}` after — append one
`LlmToolResultMessage` bundling this turn's blocks to `messages[]`, continue.
**Never throws** — a `streamAgentTurn` throw becomes a synthetic assistant
text block (`"[model error: <message>]"`) with `stopReason: "error"` fed back
into `messages[]` (spec: "model/tool errors become synthetic in-transcript
messages"); same for a fired `AbortSignal` (`stopReason: "aborted"`).
`maxSteps` exceeded → `status: "failed"`.

**Ledger write points** (nothing new beyond what `harness.ts` does today, just
relocated):
- `startRun`/`startRunStep(kind:"agent")` happen in the **caller** (thin
  `runAgent` wrapper), not in `runAgentLoop` — the loop only receives
  `runId`/`parentStepId` and nests child steps under them. Keeps the loop
  reusable for both `/api/agent` and multi-turn chat (R6).
- Each `streamAgentTurn` call does its own `model_calls` row + child `"model"`
  step (§2); each `executeTool()` call does its own `tool_calls` row + child
  `"tool"` step (§4) — the loop never duplicates that bookkeeping.
- `runAgentLoop` writes nothing to `runs`/`run_steps` directly; the
  `harness.ts` wrapper finishes/fails the top-level run + agent step from
  `AgentLoopResult`, mirroring today's tail-end `finishRun`/`failRun`.

**Thin wrapper — `src/lib/harness.ts` becomes:** unchanged signature
(`RunAgentInput`/`RunAgentResult`/`ConversationTurn`/`AgentStepEvent`/`onStep`
all survive byte-for-byte so `/api/agent` and `/api/agent/stream` callers
don't churn). Body: (1) `startRun`+`startRunStep(kind:"agent")` as today; (2)
build `messages[] = [systemMessage, ...historyAsMessages, userMessage]` via
`buildSystemPrompt` (§5) + a small `ConversationTurn`→`LlmMessage` mapper; (3)
call `runAgentLoop(...)`, translating `onEvent`→`onStep` (collapse each
`tool_start`/`tool_end` pair into one legacy `AgentStepEvent`); (4)
`finishRun`/`failRun` from the `AgentLoopResult`, return `RunAgentResult`.

`agentDecisionSchema`, `buildAgentSystemPrompt`, `buildUserPrompt`, and the
inline `executeToolCall`/`failTool` helpers are deleted from `harness.ts` —
their logic moves to `agent-loop.ts` (loop control), `context.ts` (system
prompt, §5), and `tools/orchestrator.ts` (tool execution, §4).

---

## 4. Tool orchestrator contract — `src/lib/tools/orchestrator.ts` (new)

```ts
export interface ToolExecutionResult {
  content: string; // final, already-truncated text for a tool_result block
  isError: boolean;
}

export interface ExecuteToolInput {
  toolUseId: string;
  name: string;
  input: unknown; // native object from LlmToolUseBlock.input — no JSON.parse anywhere in this path
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  db: Sql;
  runId: number;
  parentStepId: number;
}

export async function executeTool(opts: ExecuteToolInput): Promise<ToolExecutionResult>

// Tool → LlmToolDefinition, used by context assembly / agent-loop to build
// the API `tools:` param. Lives here (not registry.ts) since JSON-Schema
// conversion is a call-boundary concern, not a registry concern.
export function toLlmToolDefinition(tool: Tool): LlmToolDefinition
```

**Choke point order:** unknown tool → permission gate →
`argsSchema.safeParse(input)` → `tool.execute()` → ledger log → truncate.
Every branch does its own `startRunStep(kind:"tool")` + `startToolCall` at
entry, `finish*`/`fail*` at exit — same shape as today's `executeToolCall` in
`harness.ts`, relocated and no longer JSON-decoding args. **Truncation:**
`TOOL_RESULT_MAX_CHARS = 16_000` — if the serialized result or error message
exceeds it, truncate and append `` `\n\n[truncated ${originalLength -
16_000} chars]` `` (both success and error content). **Denials/failures never
throw** — `unknown_tool`, `permission_denied`, `invalid_args`, and
`tool_error` all return `{ content: "Error (<code>): <message>", isError:
true }`, matching today's `failTool` message format so
`describeObservation`-style UI summarizers keep working; `isError` is what
`agent-loop.ts` uses to set `LlmToolResultBlock.isError`.

---

## 5. Context assembly — `src/lib/context.ts` (new)

```ts
export interface SystemPromptSection {
  title: string;
  body: string;
}

export function buildSystemPrompt(sections: SystemPromptSection[]): string {
  // sections.map(s => `## ${s.title}\n${s.body}`).join("\n\n")
}

export async function buildMemoryIndexSection(
  db: Sql,
  actor?: MemoryActor,
): Promise<SystemPromptSection>
```

**Sections assembled by the memory chat path**, in order:
- **identity** — one paragraph: "You are a sourcing agent with access to team
  memory and tools. Decide when to search, when to answer directly, and when
  to record a finding."
- **tool-use guidance** (replaces the deleted `MEMORY_INSTRUCTIONS`
  4-section contract), free-format: *"Search memory when the index below
  isn't enough to answer confidently — you decide when that is. Whenever you
  cite memory, cite inline as `sourceId#chunk-N` (or `#row-N`); never invent
  a citation id. If memory doesn't cover something, say so plainly instead of
  guessing."* No fixed section headers, no "call search_memory every turn."
- **memory index** — built once per run by `buildMemoryIndexSection`: queries
  permission-filtered `source_records` (via `resolveAllowedSourceIds`,
  excluding archived) for title/updated_at/source_type, plus the last ~20
  rows where `source_type = 'note'` ordered by `updated_at DESC`. Rendered as
  a capped markdown bullet list, **hard cap 4,000 chars** — truncate the list
  (not mid-line), append `"...(N more sources not shown)"` on overflow. New
  capability (`notes.ts` only has `addMemoryNote` today) — add as
  `listMemoryIndexRows` in `src/lib/memory/sources.ts`.
- **per-run instructions** (optional) — unchanged pass-through slot, now an
  extra `SystemPromptSection` the caller appends (was
  `RunAgentInput.instructions`).

**Citation post-check becomes check-on-use** — `src/lib/memory/citations.ts`:
`verifyAnswerCitations(trace, answer)` already only collects citations from
`search_memory` tool-call results, so its allow-list source is already
check-on-use. What R4 changes: it must run **per turn**, not once over a
whole run — chat sessions (R6) are multi-turn, and a turn-2 citation must not
validate against turn 5's search results. Add `sinceStepId?: number` to
`verifyAnswerCitations`/`collectBundlesFromTrace`, filtering `trace.steps` to
`step.id > sinceStepId` before walking. Hook point: run the check only when
this turn's `run_steps` include a `search_memory` `tool_calls` row; otherwise
skip (nothing to validate against, nothing to strip).

---

## 6. Chat session persistence — `src/migrations/004_chat_sessions.sql` (new)

```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
  id            BIGSERIAL PRIMARY KEY,
  actor_type    TEXT NOT NULL,
  actor_id      TEXT NOT NULL,
  title         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id            BIGSERIAL PRIMARY KEY,
  session_id    BIGINT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role          TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool_result')),
  content_json  JSONB NOT NULL,
  run_id        BIGINT REFERENCES runs(id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_idx ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS chat_sessions_actor_idx ON chat_sessions(actor_type, actor_id, updated_at DESC);
```

- `content_json` stores exactly the `content` field of the corresponding
  `LlmMessage` variant (a `string` for `system`/`user`; an array of
  `LlmAssistantBlock`/`LlmToolResultBlock` for `assistant`/`tool_result`) — so
  round-tripping is a direct `{ role, content: row.content_json }`
  reassembly, no reshaping. `run_id` is nullable since `system`/`user` rows
  precede any run.
- New module `src/lib/chat/sessions.ts` (R6) owns: `getOrCreateLatestSession(db,
  actor)` (resume-latest-or-create-new, per the spec's minimal scope);
  `appendMessages(db, sessionId, messages: LlmMessage[], runId?)`; and
  `loadSessionMessages(db, sessionId): Promise<LlmMessage[]>` — `SELECT role,
  content_json FROM chat_messages WHERE session_id = $1 ORDER BY id`, then
  `rows.map(r => ({ role: r.role, content: r.content_json }) as LlmMessage)`.

---

## 7. File ownership per slice

One row per file; a file appears once. Where a later slice must touch a file
an earlier slice created, that's noted explicitly — never silently.

| File | Owning slice | Notes |
|---|---|---|
| `src/app/chat/stream.ts` | R0 (fix), R5 (rewrite) | R0: fix the dead `!res.ok && !res.body` guard only. R5: replace the SSE parser for the typed union. |
| `src/app/chat/ChatClient.tsx` | R0 (fix), R5 (rewrite) | R0: filter errored exchanges out of history only. R5: wire typed events to trace/bubble. |
| `src/lib/llm/types.ts`, `anthropic.ts`, `openai-compat.ts` | R1 | New. `openai-compat.ts` covers both `deepseek` and `openai` provider names via base-URL swap, like today's `generationModel()`. |
| `src/lib/model-gateway.ts` | R1 (add `streamAgentTurn`, additive only), R8 (migrate `callModel` internals to raw SDKs) | |
| `src/lib/agent-loop.ts` | R2 | New. |
| `src/lib/harness.ts` | R2 | Rewritten as thin wrapper; deletes `agentDecisionSchema`/`buildAgentSystemPrompt`/`buildUserPrompt`/inline tool-exec helpers. |
| `src/lib/tools/orchestrator.ts` | R3 | New. |
| `src/lib/tools/registry.ts`, `types.ts` | R3 (read-only reference) | Unchanged — `Tool`/`PermissionClass`/`ToolContext`/`ToolRegistry` shapes survive untouched. |
| `src/lib/context.ts` | R4 | New. |
| `src/lib/memory/answer-config.ts` | R4 | `MEMORY_INSTRUCTIONS` deleted; `memoryRegistry()` may move to `context.ts` or stay — R4's plan decides and states why. |
| `src/lib/memory/sources.ts` | R4 | Add `listMemoryIndexRows` (or equivalent) for the memory-index query. |
| `src/lib/memory/citations.ts` | R4 | `verifyAnswerCitations`/`collectBundlesFromTrace` gain `sinceStepId?` for check-on-use-per-turn. |
| `src/app/api/agent/stream/route.ts` | R5 (rewrite), R6 (add session load/save) | R6 depends on R5 landing first — R6's plan diffs against R5's final state, not today's. |
| `src/app/chat/ReasoningTrace.tsx`, `StepRow.tsx` | R5 | Consume the typed union. |
| `src/migrations/004_chat_sessions.sql`, `src/lib/chat/sessions.ts` | R6 | New. |
| `src/app/chat/page.tsx` | R6 | Resume-latest + "New chat" button only — no session management UI. |
| `src/lib/tools/web-search.ts`, `web-fetch.ts`, `apollo.ts` | R7 | New. All class `enrich`, all called only through `executeTool()` (R3). |
| `src/extractors/llm.ts`, `src/lib/memory/embed.ts` | R8 | Migrate off `generateObject`/`generateText`/`embed`/`embedMany` to raw-SDK adapters. |
| `package.json` | R8 | Remove `ai` + `@ai-sdk/*` (added in R1: `@anthropic-ai/sdk`, `openai`). |
| `.env.example` | R7 (`TAVILY_API_KEY`, `APOLLO_API_KEY`), R9 (provider docs) | |

**Dependency order** (unchanged from spec, restated for plan sequencing):
```
R0 → R1 → R2 → R4 → R5 → R9
          └R3 → R7
               R6 (after R5)
               R8 (last, independent)
```
Each slice's plan should assume the prior slice(s) in its dependency chain
are merged, and diff against that resulting state — not against the current
`main`.

---

## Open items each slice plan must still resolve (not pinned here)

- R2: exact synthetic-message copy for model-error/abort cases (mechanism only, here).
- R4: where `memoryRegistry()`/tool registry construction lives (`context.ts`,
  `answer-config.ts`, or new) — R4's plan picks one and states why.
- R5: `AgentLoopEvent` serialized 1:1 over SSE vs. mapped to a smaller
  UI-facing shape (today's `data-step`/`text-delta`/`data-meta`) — either OK.
- R6: session id transport (cookie vs. client-held id vs. URL param).

## Deferred to v2 (deliberate cuts, not silent gaps)

- **Wiring R7's `enrich`-class tools (`web_search`, `web_fetch`,
  `apollo_search_people`, `apollo_enrich_contact`) into the live
  `ToolRegistry`.** R7 builds and unit-tests all four; no R-slice in this
  sprint (R4, R6, R8, R9) registers them with `memoryRegistry()` or any other
  registry the running chat/agent path uses. This is a named v1→v2 cut, not
  an ownership gap: ticket "Wire enrich tools into live registry" — extend
  `memoryRegistry()` (or a superset registry) to include the four enrich
  tools behind the existing `enrich` permission-class gate, with a test
  proving each is reachable via `executeTool()`. (The analogous gap for
  `add_memory_note`, flagged independently in R9's plan, belongs to the same
  ticket.)
