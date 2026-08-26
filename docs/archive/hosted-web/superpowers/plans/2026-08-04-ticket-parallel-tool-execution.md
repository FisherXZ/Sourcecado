# Ticket — Tool calls in one turn execute strictly sequentially

Date: 2026-08-04
Found by: code read during the 2026-08-04 agent probe
Blocks (practically): J4 `research_contact` subagent
Est: 0.5

## Problem

`src/lib/agent-loop.ts` executes every tool the model requested in a turn one at
a time:

```ts
for (const block of toolUseBlocks) {
  await input.onEvent?.({ type: "tool_start", ... });
  const result = await executeTool({ ... });
  await input.onEvent?.({ type: "tool_end", ... });
  resultBlocks.push({ ... });
}
```

Providers routinely return several independent `tool_use` blocks in a single
turn. Researching five contacts costs five round trips serially when it could
cost roughly one.

Both reference implementations run independent calls concurrently — OpenWorker
marks read-only tools low-risk specifically so they become eligible for parallel
execution (`coworker/tools/subagent.py`).

## Why it matters more than it looks

J4 (`research_contact` subagent) is a fan-out design: N contacts researched
independently. On a sequential executor, N subagents run one after another and
the entire benefit — wall-clock, not context — disappears. Building J4 on this
executor produces a slow feature and a wrong impression of the pattern.

## Fix

Execute the turn's tool calls concurrently while preserving:

- **Result ordering.** `resultBlocks` must stay in the model's requested order.
  Providers reject a transcript where `tool_use` and `tool_result` pairing is
  scrambled. Collect concurrently, then reassemble in order.
- **Event ordering per tool.** `tool_start` must still precede `tool_end` for a
  given tool. Interleaving *between* tools is fine and expected; the stream
  consumer already handles a pending-tool state (R5).
- **Failure isolation.** One tool erroring must not reject the batch. The
  orchestrator contract is that denials and failures return an `is_error`
  result and nothing throws — preserve that per-call.

Open question for `/interview-me`: whether every permission class runs in
parallel, or only read-class tools, with `enrich`/`draft` kept serial. Parallel
`enrich` multiplies vendor spend within a single turn and interacts directly
with the pre-flight check in
`2026-08-04-ticket-enrich-spend-ceiling.md` — a pre-flight budget check that is
correct serially can be raced by concurrent calls.

## Done when

- Independent tool calls in one turn run concurrently, proven by a test that
  fails on the sequential implementation (e.g. total elapsed under the sum of
  individual latencies).
- Result order, per-tool event order, and per-call failure isolation all hold
  under test.
- The spend-ceiling interaction is resolved, not left implicit.
