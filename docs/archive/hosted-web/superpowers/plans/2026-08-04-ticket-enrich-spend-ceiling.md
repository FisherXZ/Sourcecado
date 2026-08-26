# Ticket — Per-conversation enrich spend ceiling

Date: 2026-08-04
Split out of: R11 (`docs/superpowers/plans/2026-08-02-completion-roadmap.md`)
Blocks: J4 (`research_contact` subagent), E1 (routines)
Est: 1

## Why this is its own ticket

R11 originally bundled a spend ceiling with two `web_fetch` security fixes. The
security fixes are real today and shipped as the narrowed R11. The ceiling was
deferred here because its premises were not yet true:

- **Apollo is not live.** `APOLLO_API_KEY` never arrived; PR #18 deferred the
  live smoke. The credits the ceiling protects cannot currently be spent, so any
  cost weights would be guesses against unbilled pricing.
- **The gate already exists.** `allowed: Set<PermissionClass>` with `enrich` as
  its own class, enforced at `src/lib/tools/orchestrator.ts:62`. "Chat runs allow
  the enrich class freely" is a one-line policy choice at the call site, not a
  missing subsystem.
- **J3 is already scheduled** and ports `ask.py` + `grant_entries()` — the
  approval half of this problem.

## Why it is deferred, not dropped

Neither reference implementation has a vendor-spend budget. Claude Code gates
`WebFetchTool` on `domain:${hostname}` permission rules; OpenWorker uses
`grant_entries()` standing grants (write-only, fail-closed, shown on a consent
card). Both *ask*; neither *counts*. They can do that because they are
single-user local tools with a human at the keyboard and zero per-call vendor
cost. Two of those three are false for Sourcecado.

Gates and ceilings answer different questions:

| | Question it answers | Fails when |
|---|---|---|
| Permission gate (J3) | Was this authorized? | Nobody is at the keyboard |
| Spend ceiling (this) | Even authorized, how much? | Nothing — it is the backstop |

The ceiling's real consumers are **J4** (N parallel researchers multiply spend
N-fold) and **E1** (scheduled routines run unattended). A director who has
clicked "always allow" once has no gate left; the ceiling is what remains.

## Design decisions already settled

Settled with Fisher on 2026-08-03; do not re-litigate these during
implementation.

1. **Counts external vendor spend only.** Model tokens are explicitly not a
   concern. This means the budget lives at the tool-call layer, not the LLM turn
   layer — `model_calls.total_tokens` is irrelevant to it.

2. **Per-tool weights, not a flat counter.** `apollo_*` spends prepaid credits
   and `web_search` spends Tavily quota; `web_fetch` costs only bandwidth. A flat
   counter lets a page-reading agent exhaust an allowance meant for Apollo, which
   ends with the ceiling raised until it protects nothing.

3. **Scoped to the whole conversation, not one run.** `harness.runAgent` calls
   `startRun` per invocation (`src/lib/harness.ts:92`), and the chat route calls
   it once per user message — so `runId` is a single turn. A per-run budget
   resets every message and does not address the stated fear ("one conversation
   can burn the balance"). Key on the chat session and derive the total by
   summing weights over that session's `tool_calls` rows: stateless, and it
   survives R6 session resume, which an in-memory tracker does not.

4. **Soft-deny; do not kill the run.** The goal is capping spend, not capping the
   run. Add a fourth `failTool` branch after the permission gate at
   `orchestrator.ts:70`. The agent receives
   `Error (budget_exhausted): ...`, stops calling Apollo, and answers from what it
   already gathered. This matches OpenWorker (`coworker/web/tool.py:77-81` returns
   `{"error": ...}` on quota failure rather than raising) and Sourcecado's own
   orchestrator contract ("Denials and failures return an is_error result;
   nothing throws").

5. **Pre-flight, not post-hoc.** Subtract and compare *before* the call goes out.
   Checking afterwards records the overspend instead of preventing it. The
   weights make this possible since each tool's cost is known in advance.

## Notes for implementation

- The `tool_calls` ledger row opens *before* the permission gate
  (`orchestrator.ts:44-57`), so a denial is ledger-visible with **no migration**.
- Reference `claude-code-main/src/query/tokenBudget.ts` only for shape (a tracker
  threaded through the loop, a decision object carrying a reason). Its *policy* is
  the opposite of what is wanted here: it is an auto-continuation mechanism that
  keeps an agent going until 90% of budget, designed to spend a budget
  productively, not to prevent spend.
- Revisit the weights once Apollo is live and real per-call pricing is known.

## Done when

- A conversation exceeding its ceiling has its next enrich call denied, with the
  reason recorded in `tool_calls`, while the agent still produces an answer.
- Weights are per-tool and `web_fetch` does not draw down the Apollo allowance.
- The ceiling survives a session resume (proven by test, not by inspection).
