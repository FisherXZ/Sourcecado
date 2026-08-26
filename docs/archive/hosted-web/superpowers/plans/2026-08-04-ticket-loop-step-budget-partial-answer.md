# Ticket — Exhausting the step budget returns nothing

Date: 2026-08-04
Found by: code read during the 2026-08-04 agent probe
Est: 0.5

## Problem

`src/lib/agent-loop.ts:158` ends a step-exhausted run like this:

```ts
return { status: "failed", messages, stopReason: lastStopReason, steps: maxSteps };
```

No `finalText`. An agent that ran 8 steps of real research — searched memory,
queried Apollo, fetched pages — hands back **nothing at all**. Not a summary, not
partial findings, not a statement of what it was doing when it ran out.

Every other exit path in the loop produces something. This one silently discards
the whole run.

## Second problem: the ceiling is low — and is the wrong instrument

```ts
const DEFAULT_MAX_STEPS = 8;
```

One step is one LLM turn. The chain a real sourcing task needs — memory search,
Apollo search, web search, web fetch, Apollo enrich, then write — spends 5-6
turns before the first word of the deliverable. Eight leaves almost no room for
a retry, a refinement, or a second contact.

**There are two constants, not one.** `agent-loop.ts:16` and `harness.ts:71`.
Harness always passes its value down, so `harness.ts:71` is the effective ceiling
for every chat run and `agent-loop.ts:16` only binds direct `runAgentLoop`
callers (tests today). Changing one and not the other is a live foot-gun.

## Evidence

The 2026-08-04 probe used 2 of 8 steps, so the ceiling was not what broke that
run. This is a latent defect, not the observed one — but it will bite the moment
`2026-08-04-ticket-agent-tool-chaining.md` lands and the agent actually starts
chaining. Fixing the chain without fixing this trades a template for a blank.

### What the reference implementations do

Checked 2026-08-04 against `Good-Examples/claude-code-main` and
`GitHub2026/openclaw`. **Neither imposes a default step ceiling on its main
agent loop.**

Claude Code:

- `maxTurns?: number` is optional with no default (`QueryEngine.ts:146`). The
  check is `if (maxTurns && nextTurnCount > maxTurns)` (`query.ts:1705`) —
  undefined means the `while (true)` loop runs until the model stops calling
  tools.
- `--max-turns` is `.hideHelp()`'d and its own description says *"only works with
  `--print`"* (`main.tsx:976`). It is a headless-scripting affordance, not the
  interactive agent's governor.
- Registered on the next line is the actual cost control: `--max-budget-usd`,
  checked at `QueryEngine.ts:972` (`getTotalCost() >= maxBudgetUsd` →
  `error_max_budget_usd`). `attachments.ts:3846` injects
  `{ used, total, remaining }` into the model's context each turn, so the agent
  sees its runway shrink and wraps up itself rather than being cut off blind.
- Subagents take an optional `maxTurns` from agent frontmatter
  (`loadAgentsDir.ts:89`) — opt-in, still no default.

openclaw imposes no step ceiling on its own loop. Its `maxTurns` occurrences are
xAI's internal search config (`types.tools.ts:648`), an agent-to-agent ping-pong
deadlock guard that also hands the model an explicit stop token
(`sessions-send-helpers.ts:101-103`), and relaying `cli_max_turns` out of the
Claude Code subprocess it drives.

Sourcecado's unconditional `DEFAULT_MAX_STEPS = 8` on every chat run is out of
line with both.

## Decisions

Settled 2026-08-04 with Fisher, after the reference read above.

1. **Keep a ceiling, but demote it to a runaway backstop: 50.** Not "remove
   entirely" and not a tuned product number like 20. A tuned number is just a
   smaller version of the same wrong control — it still ends legitimate runs, and
   any value picked today is a guess. 50 sits far above any real sourcing chain,
   so it stops being the thing that ends real work, while still capping a
   pathological tool-call cycle burning model tokens. Set **both** constants.

2. **Why not remove it entirely.** Claude Code can run unbounded partly because
   it auto-compacts (`autoCompactTracking` threaded through `query.ts` state).
   Sourcecado has no compaction — `runAgentLoop` appends to `messages[]` forever.
   An unbounded loop here does not run until it is done; it runs until the
   provider rejects the transcript on context length, which surfaces as
   `stopReason: "error"` — a hard failure, not a graceful stop. Revisit removing
   the backstop only once compaction exists.

3. **The spend ceiling does not subsume this.** Worth stating because it is the
   natural assumption: `2026-08-04-ticket-enrich-spend-ceiling.md` decision 4 is
   *soft-deny, do not kill the run* — a budget-exhausted agent gets
   `Error (budget_exhausted)` and answers from what it has, so the run still ends
   `succeeded` through the normal path. It never truncates. That makes the step
   backstop (and abort) the **only** ways a run ends mid-chain, so the truncated
   path built here is not shared machinery with the budget work.

4. **Signal truncation with a distinct status,** threaded
   `AgentLoopResult` → `RunAgentResult` → `MemoryAnswer` → the stream's `meta`:

   ```ts
   status: "succeeded" | "truncated" | "failed"
   ```

   The ticket's requirement is that a caller can tell "ran out of room, here is
   what I have" from "the model errored"; a boolean flag riding alongside
   `status: "failed"` leaves `/api/agent` returning 500 and the ledger recording
   a failure for a run that produced usable output.

5. **Partial text is the last assistant text, verbatim,** with a synthetic
   fallback line when that turn was pure `tool_use` and carries no text. Rejected
   the alternative (one extra tools-disabled synthesis turn) because at a 50-step
   backstop the run is already pathological — spending another LLM turn to
   prettify a runaway is the wrong trade. Reconsider if the backstop is ever
   lowered back into everyday range.

## Notes for implementation

- **The citation scrub is the trap.** `src/lib/memory/answer.ts:75` gates on
  `result.status === "succeeded" && answer !== undefined`. Populating `finalText`
  on the truncated path without widening this condition ships partial text to the
  user **unscrubbed**, invented citations included. Widen the gate; do not just
  change the loop's return.
- `/api/agent` (`route.ts:20`) maps `status !== "succeeded"` to **HTTP 500**.
  `truncated` must return 200 — a truncated answer is a result, not a server
  error.
- `/api/agent/stream` (`route.ts:91-101`) falls to `writer.answerEnd()` when
  `result.answer` is undefined. In `ChatClient.tsx`, `errored` is only set on a
  *transport* failure (line 95), so today a step-exhausted run renders as a
  completed exchange with an **empty answer bubble** and a small grey "failed" in
  the meta footer. That is the "silently dropped" this ticket names.
- `harness.ts:165-168` routes every non-`succeeded` result through
  `describeLoopFailure` → `failRunStep` + `failRun`. A truncated run should
  `finishRun` with its output plus a truncation marker; it produced work.
- The stream route's `withCheckedAnswer` (`route.ts:120`) no-ops when `answer` is
  undefined — once truncated runs carry text, it starts applying to them, which is
  correct but currently untested.
- Existing tests encode the old behavior and must be rewritten, not deleted:
  `tests/agent-loop.test.ts:267` asserts `status === "failed"` on exhaustion;
  `tests/harness.test.ts:129-146` asserts the ledger gets
  `errorType: "max_steps_exceeded"`.
- Steal from Claude Code later, not now: injecting remaining-runway into the
  model's context (`attachments.ts:3846`) is the mechanism that makes a ceiling
  steer rather than guillotine. It belongs with the spend ceiling, where the
  agent can act on it.

## Done when

- A run that exhausts its steps returns usable text plus a `truncated` status
  distinguishable from a hard failure, proven by test.
- Both `DEFAULT_MAX_STEPS` constants (`agent-loop.ts:16`, `harness.ts:71`) are 50,
  documented in-file as a runaway backstop rather than a product limit.
- Truncated partial text passes through the citation scrub, proven by a test that
  would fail if the `answer.ts:75` gate were left as-is.
- `/api/agent` returns 200 with the partial answer for a truncated run.
- The truncated answer renders in chat as text the user can read, with a visible
  "ran out of steps" signal — not an empty bubble.
