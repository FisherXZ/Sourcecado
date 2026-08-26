# Intent: Runtime Solidification Sprint

Date: 2026-07-14
Status: Confirmed (interview-me session)
Capacity: 2 weeks, 8–10 focused sessions

## Outcome

A solid, provider-agnostic agent runtime — the general agent stack made clean —
so every future product feature (B–G in the roadmap) builds on sound wiring.
Five components, each with a checkable pattern borrowed from the reference
stacks (`claude-code-main` `src/query.ts`, `openclaw` `packages/agent-core/src/agent-loop.ts`):

1. **Loop** — hand-rolled, ~200 lines. Native tool_use/tool_result message
   threading (no prose parsing, no JSON-string args). Explicit stop conditions
   (no-tool-use → stop, max turns, abort). Model/tool errors never crash the
   loop — they become synthetic in-transcript messages the model can react to.
   **Provider-agnostic is a hard requirement**: the loop is written only
   against a normalized message/event contract; provider adapters (Anthropic +
   at least one other) live behind the Model Gateway.
2. **Tool layer** — each tool = schema + description + dumb pure handler.
   One orchestrator choke point owns validation → permission gate → execution
   → ledger logging. Denials and tool failures return as in-transcript
   `tool_result` errors, never exceptions. Tool results get visible truncation
   caps. Tool list passed as the API `tools:` field — never inlined into
   prompt prose (kills the MEMORY_INSTRUCTIONS pattern). Tools:
   `search_memory`, `add_memory_note`/finding-write, Apollo search/enrich
   basics, web search, web fetch — each with basic tests.
3. **Context engineering** — system prompt assembled once per session from
   named sections; live per-turn context appended, not rebuilt from scratch;
   small capped memory index injected at run start, detail on demand. No
   compaction machinery — caps and truncation only.
4. **Memory (simple)** — two layers: injected index + agent-*decided*
   `search_memory` for cited deep lookups (Postgres/pgvector unchanged
   underneath) + mid-session finding writes retrievable later in the session
   and in the next. No dreaming/claims-wiki/hierarchy sophistication.
5. **Streaming** — loop emits a typed event union with text / thinking /
   tool-call as distinct streams; the A6.3 chat UI rewires onto it; PR #10's
   two review defects (dead `!res.ok && !res.body` guard in
   `src/app/chat/stream.ts`; errored exchanges leaking into follow-up history
   in `ChatClient.tsx`) fixed and merged.

## User

Fisher, at the keyboard. Engine sprint judged by direct use — not a
stakeholder demo.

## Why now

The 2026-06-26 handoff (`scratchpad/HANDOFF-agent-orchestration.md`) showed
the agent wiring is the bottleneck: prose-driven tool-calling, double-encoded
args, prompt rebuilt every step. Building B–G on that harness compounds the
debt.

## Success

In `/chat`: ask a sourcing question → agent answers from injected memory *or
decides on its own* to search memory / web / Apollo → findings written
mid-session are retrievable later and next session → everything streams live
and lands in the run ledger → same behavior when the model provider is
swapped in config. Tool basics covered by tests; PR #10 merged.

## Constraint

2 weeks, 8–10 sessions. Postgres + pgvector untouched. Pre-agreed cut order
if over budget: Apollo first, then memory-injection niceties (hierarchical
files → flat index v1).

## Out of scope

- Vercel AI SDK as loop substrate (decision: hand-rolled over raw provider
  SDKs, copying reference-repo structure).
- Memory-system sophistication: dreaming, claims/evidence wiki, compaction.
- Roadmap features B (sourcing state model), D (artifacts), E (routines),
  G (feedback loop / demo hardening).
- Apollo/web enrichment polish (full C1/C2 slices).
- Memory management UI work; deployment/hosting.

## Key decisions captured

- Hand-rolled loop over raw provider SDKs, **not** Vercel AI SDK — this is a
  learn-the-stack build, both reference repos hand-roll, and A6.3 already hit
  AI SDK version friction. Trade-off accepted: we own ~200 lines of loop +
  stream normalization.
- Provider-agnostic loop is non-negotiable; Model Gateway hosts the adapters.
- Two-layer memory (Claude-Code-style injection + cited retrieval tool), not
  forced search-every-turn and not md-files-replace-pgvector.
- External tools in scope: Apollo, web search, web fetch — basics tested, not
  polished.
