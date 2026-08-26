# Epic: Runtime Solidification — provider-agnostic agent loop, clean tool layer, two-layer memory

Date: 2026-07-14
Status: Approved (interview + /spec session, 2026-07-14)
Intent: `docs/intent/2026-07-14-runtime-solidification-sprint.md`
Tracking: local files only (no issue tracker). Each R slice gets its own
implementation plan doc under `docs/superpowers/plans/` before build.
Capacity: 2 weeks, 8–10 sessions (estimate ~10 — cut order at bottom applies).

## Context

Sourcecado's subsystems are solid (Model Gateway ledger discipline, Run Ledger,
permissioned pgvector memory, citation guarantee) but the agent wiring is
prose-driven: the model picks actions via `generate_object` with tool args
double-encoded as JSON strings, control flow lives in `MEMORY_INSTRUCTIONS`
prose, and the prompt is rebuilt from strings every step. Building roadmap
features B–G on this compounds the debt. This epic replaces the wiring with a
hand-rolled, provider-agnostic loop copying the patterns production stacks use
(claude-code `src/query.ts`, openclaw
`packages/agent-core/src/agent-loop.ts`), while keeping the moat (ledger,
permissions, citations, pgvector) intact.

## Decisions locked (do not relitigate)

- Hand-rolled loop over **raw provider SDKs** (`@anthropic-ai/sdk`, `openai`),
  NOT the Vercel AI SDK. Full AI SDK rip-out by end of sprint (R8).
- Provider-agnostic is a hard requirement: loop written only against the
  normalized contract; adapters behind the Model Gateway.
- Web search provider: **Tavily** (`TAVILY_API_KEY`). `web_fetch` = plain
  fetch + HTML→text.
- Server-side chat sessions: **in scope** (R6), minimal (resume latest + new
  chat; no management UI).
- Citations: **check-on-use, free format** — post-check runs on turns where
  `search_memory` was called; the 4-section format dies with
  `MEMORY_INSTRUCTIONS`.
- Apollo: build against a mocked client now; `APOLLO_API_KEY` provided later —
  live smoke deferred, not blocking.
- Postgres + pgvector memory engine untouched underneath.

## Current State (verified 2026-07-14)

| Where | What exists | Problem |
|---|---|---|
| `src/lib/harness.ts:18-35` | `agentDecisionSchema` — action via structured output; `args` is a JSON **string** by design | No native tool calling; double-encoded args |
| `src/lib/harness.ts:201-225` | `buildAgentSystemPrompt` inlines tool catalog + schemas as prose | Tools never passed as API `tools:` field |
| `src/lib/harness.ts:234-249` | `buildUserPrompt` re-renders history + observation transcript as one string per step | No message threading; unbounded rebuild each step |
| `src/lib/memory/answer-config.ts:9-32` | `MEMORY_INSTRUCTIONS`: "call search_memory first — on every turn" + rigid 4-section format | Control flow in prose; agent can't decide |
| `src/lib/model-gateway.ts:2-6,300-352` | Gateway built on Vercel AI SDK; `prompt`/`system` strings only | No streaming, no messages, no tools support |
| `src/app/chat/stream.ts` (~97), `ChatClient.tsx` | PR #10 open; hand-rolled SSE parser | 2 unresolved review defects (dead error guard; errored turns leak into history) |
| Tools | `search_memory`, `add_memory_note`, `echo` (Zod schema + execute) | Only memory tools; no external tools |
| Deps | `ai@6`, `@ai-sdk/{anthropic,deepseek,openai}`; no `@anthropic-ai/sdk`, no `openai`, no Tavily/Apollo keys | Raw SDKs + env vars must be added |

## Proposed Change — 10 slices, dependency-ordered

```
R0 PR#10 fixes+merge ──> R1 Provider adapters ──> R2 Loop ──> R4 Context+memory ──> R5 Streaming rewire ──> R9 E2E+provider-swap proof
                                                  └─> R3 Tool orchestrator ──> R7 External tools (Tavily/fetch/Apollo)
                                                                    R6 Chat sessions (after R5)
                                                                    R8 AI SDK rip-out completion (last, independent)
```

### R0 — Merge PR #10 (~0.5 sn)

Fix the dead `!res.ok && !res.body` guard in `src/app/chat/stream.ts` (error
responses must surface, not resolve as empty turns) and filter errored
exchanges out of follow-up history in `ChatClient.tsx`; merge PR #10. All new
work lands on a fresh branch off updated main.

### R1 — Provider adapter layer (~1.5 sn)

Add `@anthropic-ai/sdk` + `openai` packages. New `src/lib/llm/` with the
normalized contract:

- `LlmMessage`: system/user/assistant/tool_result; assistant content =
  text | tool_use blocks.
- `LlmStreamEvent`: `text_delta | thinking_delta | tool_call_start |
  tool_call_delta | tool_call_end | turn_end{stopReason, usage}`.
- `StopReason`: `'end' | 'tool_use' | 'max_tokens' | 'error' | 'aborted'`.

Two adapters: `anthropic.ts` (raw SDK, native tool_use blocks) and
`openai-compat.ts` (one client covers DeepSeek + OpenAI, tools as
function-calling). Gateway gets a new `streamAgentTurn()` entry recording to
`model_calls` exactly like `callModel` does.

### R2 — The loop (~1.5 sn)

`src/lib/agent-loop.ts`, ~200 lines: `while` over `messages[]`; stream turn →
if `tool_use` → execute via orchestrator → append `tool_result` messages →
continue; no tool_use → stop. Stop conditions: natural stop, `maxSteps`
(keep 8), `AbortSignal`. Model/tool errors become synthetic in-transcript
messages — the loop never throws mid-run. Ledger writes preserved 1:1
(`startRun`/`startRunStep`/tool + model call records). `harness.ts`'s
`runAgent` signature survives as a thin wrapper so `/api/agent` callers don't
churn.

### R3 — Tool orchestrator (~1 sn)

Keep the `Tool` shape (`src/lib/tools/types.ts` — name/description/
permissionClass/Zod schema/execute); add one `executeTool()` choke point:
validate → permission gate → execute → ledger log → truncate result to 16k
chars with visible `[truncated N chars]` notice. Denials/failures return
`tool_result` with `is_error: true` — never exceptions. Tool schemas exported
as JSON Schema into the API `tools:` param (Zod v4 `z.toJSONSchema`, already
used at `harness.ts:206`).

### R4 — Context assembly + memory contract (~1 sn)

`buildSystemPrompt(sections)` — identity, tool-use guidance, memory index,
per-run instructions. Memory index built at run start from a SQL query
(source titles/dates/kinds + last ~20 memory notes), rendered as capped
markdown (≤4k chars). `MEMORY_INSTRUCTIONS` deleted; replaced by: search when
the index isn't enough, cite inline (`sourceId#chunk-N`) whenever citing
memory, surface gaps honestly. Citation post-check becomes check-on-use: runs
only over turns where `search_memory` was called; cited ids must exist in
that turn's tool results.

### R5 — Streaming rewire (~1 sn)

`/api/agent/stream` re-emits `LlmStreamEvent`s + tool lifecycle over the
existing SSE channel; `ChatClient`/`ReasoningTrace`/`StepRow` consume the
typed union (tool events feed the trace; text deltas feed the bubble — true
token streaming replaces per-step flushes).

### R6 — Chat sessions (~1 sn)

Migration `004_chat_sessions.sql` (`chat_sessions`, `chat_messages` incl.
role, content, run_id link); stream route loads/saves; `/chat` resumes latest
session + "New chat" button. Session management UI (rename/delete/list beyond
latest) out of scope.

### R7 — External tools (~1.5 sn)

`web_search` (Tavily, `TAVILY_API_KEY`), `web_fetch` (plain fetch +
HTML→text, size-capped, http(s) only), `apollo_search_people` +
`apollo_enrich_contact` basics (`APOLLO_API_KEY`), all class `enrich`, all
through the R3 orchestrator, provider usage recorded in the ledger. Tests:
mocked-client unit tests per tool + one live smoke per key present; missing
key → clean in-transcript tool error, not a crash. Apollo live smoke deferred
until the key is provided.

### R8 — AI SDK rip-out completion (~1 sn)

Migrate `embed`/`embed_many` to the raw `openai` client, remaining
`generate_text`/`generate_object` callers (extractors, memory answer path) to
the new adapters (structured output via tool-forcing on Anthropic,
`response_format` on OpenAI-compat); remove `ai` + `@ai-sdk/*` from
package.json.

### R9 — E2E + provider-swap proof (~0.5 sn)

Scripted chat e2e (question → agent-decided search → cited answer → finding
write → retrievable in new session); run the same flow twice with
`SOURCECADO_GENERATION_PROVIDER=anthropic` and `=deepseek`; document env in
`.env.example`.

## Acceptance Criteria

1. `/chat`: agent answers a memory question **without** calling
   `search_memory` when the injected index suffices, and **does** call it
   (with valid citations, post-check green) when depth is needed — both
   observed live.
2. Tool args arrive as native structured tool calls — zero `JSON.parse` of
   model-produced arg strings anywhere in the loop path.
3. A permission-denied tool call appears in the transcript as an `is_error`
   tool_result and the run continues; nothing throws.
4. Swapping `SOURCECADO_GENERATION_PROVIDER` between `anthropic` and
   `deepseek` changes zero loop/tool/UI code paths — same e2e passes on both.
5. A finding written mid-session via `add_memory_note` is retrieved in a
   *new* session (index or search).
6. Tavily search and web fetch succeed live with key present and degrade to
   in-transcript errors without; Apollo passes mocked tests (live smoke
   deferred until key provided); usage rows land in the ledger.
7. Chat survives a page reload (session restored from Postgres).
8. Full test suite green; new tests cover adapters (event normalization),
   loop (stop conditions, error-as-message), orchestrator (denial,
   truncation), each external tool (mocked).
9. `ai`/`@ai-sdk/*` absent from `package.json` (R8 done) — or explicitly
   ticketed if cut.
10. PR #10 merged; existing 333 tests still green after every slice.

## Testing Plan

| Layer | What | Count |
|---|---|---|
| Unit | Adapter event normalization (anthropic + openai-compat), stop reasons | +8–12 |
| Unit | Loop: natural stop, maxSteps, abort, model-error-as-message, tool-error-as-message | +5–8 |
| Unit | Orchestrator: validation fail, permission denial, truncation notice, ledger rows | +4–6 |
| Unit | Tools: Tavily/web_fetch/Apollo mocked clients; missing-key path | +6–9 |
| Unit | Context: system prompt sections, memory index cap, check-on-use citation gate | +4–6 |
| Integration | Stream route: typed events over SSE; session save/load | +3–4 |
| E2E | Scripted chat loop, run twice (anthropic, deepseek) | +1 |

## Rollback

Each slice is its own PR off main; `runAgent`'s wrapper keeps the old
signature so any slice reverts cleanly. The old prose harness stays in git
history; R8 (dep removal) is the only hard-to-partially-revert step, which is
why it's last.

## Files Reference (primary)

| File | Change |
|---|---|
| `src/app/chat/stream.ts:97`, `src/app/chat/ChatClient.tsx` | R0 PR #10 fixes |
| `src/lib/llm/` (new: `types.ts`, `anthropic.ts`, `openai-compat.ts`) | R1 contract + adapters |
| `src/lib/model-gateway.ts` | R1 `streamAgentTurn()`; R8 raw-SDK migration of remaining kinds |
| `src/lib/agent-loop.ts` (new), `src/lib/harness.ts` (becomes wrapper) | R2 |
| `src/lib/tools/orchestrator.ts` (new), `src/lib/tools/registry.ts` | R3 |
| `src/lib/context.ts` (new), `src/lib/memory/answer-config.ts` (delete MEMORY_INSTRUCTIONS), `src/lib/memory/citations.ts` | R4 |
| `src/app/api/agent/stream/route.ts`, `src/app/chat/*` | R5 |
| `src/migrations/004_chat_sessions.sql` (new), `src/app/api/agent/*` | R6 |
| `src/lib/tools/web-search.ts`, `web-fetch.ts`, `apollo.ts` (new) | R7 |
| `src/extractors/llm.ts`, `src/lib/memory/embed.ts`, `package.json` | R8 |
| `.env.example` | R7/R9: `TAVILY_API_KEY`, `APOLLO_API_KEY`, provider docs |

## Out of Scope

Roadmap B/D/E/G; compaction/memory sophistication; session management UI;
Apollo/web enrichment polish (full C1/C2 slices); deployment; Vercel AI SDK
as loop substrate.

## Cut order if over budget

1. Apollo (R7 ships search/fetch only) →
2. R8 rip-out completion (ticket it; the loop path is already raw-SDK) →
3. R6 sessions.
