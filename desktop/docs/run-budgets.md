# Run budgets

Issue #83. What one Sourcecado run may spend before it stops, how each number
is measured, and what a stopped run is allowed to claim about itself.

This replaces the fixed eight-step loop ceiling in `turn.py`.

## Words used here

- **Run** — one call to `run_turn`. One director message, the model turns it
  takes, and the tool calls those turns make.
- **Model turn** — one pass of the agent loop: one model request plus the tool
  calls it asked for. A provider retry or failover is not a model turn; it
  repeats a request the loop already counted.
- **Budget** — an absolute ceiling on one measurement for one run.
- **Loop detector** — the check that stops a run for repeating itself, before
  any budget runs out.
- **No progress** — a tool call whose exact arguments have already produced
  this exact result in this run.
- **Continuation** — a new run on the same conversation, started by the
  director. Not a resumption: a new run, with a new budget and the same gates.

## Where the code is

| File | What it does |
| --- | --- |
| `coworker/run_budget.py` | All of the policy. `RunBudgetPolicy`, `RunBudgetMeter`, the loop detector, the payload projections. |
| `coworker/turn.py` | Creates one meter per run, feeds it, and stops on its decision. |
| `coworker/telemetry/` | The typed values the meter reads: `UsageEvent`, `CostEstimate`. |
| `surfaces/gui/src/chat/protocol.ts` | Parses the operator payload. Drops anything not on the allowlist. |
| `surfaces/gui/src/chat/RunBudget.tsx` | The live strip, the warning, the stop card, the Continue action. |

## The defaults

| Budget | Default | How it is measured | Why this number |
| --- | --- | --- | --- |
| `model_turns` | 40 | counted in the loop | Five times the ceiling it replaces. A real sourcing run reads the board, checks several sources, enriches, and drafts. Eight was below ordinary work; forty is above it. |
| `tool_calls` | 120 | counted in the loop | A model turn may request several tools at once. Roughly three per turn. |
| `elapsed_seconds` | 900 | `time.monotonic` | Fifteen minutes. Long enough for forty turns of connector work, short enough that an unattended run cannot spend an afternoon. |
| `input_tokens` | 2,000,000 | provider-reported usage | About 50,000 tokens of prompt per turn across the turn budget, which is what a compacted sourcing conversation actually costs. |
| `output_tokens` | 200,000 | provider-reported usage | Ten percent of the input allowance. A run that writes more than this is not drafting outreach. |
| `estimated_cost_usd` | 2.00 | provider-reported cost estimate | The spend ceiling for one unattended run. |

Two of the six are not measurements and the payload says so.
`MEASUREMENT_SOURCES` names the origin of each number, and the operator
surface repeats it.

- **Cost is an estimate.** It comes from the provider stream's
  `estimated_cost_usd`, which `provider.py` derives from the Sourcecado-owned
  pricing table. A model with no entry in that table reports no cost at all,
  contributes nothing to the meter, and is counted in `unpriced_requests`. A
  run showing `$0.02` with four unpriced requests is not a run that cost two
  cents, and the stop card says that in words.
- **Elapsed time is wall clock.** It includes time spent waiting for a
  director to answer an approval.

Tokens and cost bind at different places on purpose. On `deepseek-v4-pro` the
token ceilings are worth about $1.04, so the two land close together. On a
model priced like `kimi-k3` the $2.00 ceiling is reached long before the token
ceilings. The token ceilings exist for the case the cost ceiling cannot cover:
a model with no price, where the cost meter reads zero forever.

Every default is a field on `RunBudgetPolicy`. `run_turn` takes
`run_budget_policy` and falls back to the defaults, so nothing outside this
module has to know the numbers.

## The order: loop detector first

`RunBudgetMeter.check` asks two questions and the order is the point.

1. Is the run repeating itself?
2. Has any budget run out?

A run stuck on one tool and a run that read a hundred large pages both stop.
They are different problems and they lead to different operator actions, so
they must not produce the same message. Checking the loop detector first means
a stuck run is reported as stuck even when it has also exhausted a budget.

`RunBudgetPolicy.__post_init__` rejects any policy where
`loop_repeat_limit >= tool_calls`. Without that, a policy could be configured
where the tool-call ceiling is reached before the detector can fire, and the
ordering would hold only by luck.

### What counts as no progress

Every completed call contributes one pair: a fingerprint of `(tool name,
arguments)` and a fingerprint of the result. A pair already seen in this run is
a stale call. `loop_repeat_limit` consecutive stale calls, three by default,
trip the detector. Anything new resets the streak to zero.

A refusal is an outcome. A model that asks for a denied tool ten times is
looping exactly as much as one re-reading the same page, so gate refusals and
tool failures feed the detector alongside successes.

The fingerprints never leave `run_budget.py`. Only the count of repeats is
reported.

## What a stopped run says

`turn_end` carries `state: "stopped"` and a `run_budget` payload. The store
maps that state to a `partial` run status, the same as the ceiling it replaces,
so nothing downstream reads a budget stop as a success.

The payload's job is to make a stopping point impossible to read as a
conclusion:

- `state: "exhausted"` and `stopped_by`, a budget name or `"loop"`.
- `completed`, a receipt for every tool call that actually ran, with its
  outcome. Failed calls are receipts too.
- `remaining.requested_tools`, the calls the model asked for in the final batch
  that were never run.
- `remaining.final_answer: false`, whenever the run ended without the model
  closing it.
- `consumed`, `limits`, `measurement`, `unpriced_requests`,
  `unmeasured_requests`.
- `continue_available: true`.

The UI writes its own sentences from those numbers. `runBudgetStatus` in
`protocol.ts` rebuilds the payload field by field, so a backend that starts
sending prose cannot get it rendered as Sourcecado's account of the run. The
stop card leads with "This run did not finish."

## The warning

`tool_started` carries a smaller `run_budget` payload with the run's current
spend. Once a budget passes `warn_at`, 80 percent by default, the next of
those events carries a warning naming that budget. Each budget warns once.

The thread renders it as one quiet line under the activity list, not a banner.
A director who is watching sees the run approaching a stop; a director who is
away sees it in the transcript afterwards.

## Continuation

Continue is not a resumption. It is an ordinary new turn on the same
conversation, sent by the director from the stop card, and everything follows
from that.

**What it preserves,** because a new turn on the same session already has it:

- the bound person file, which is bound to the session;
- the conversation, which is the target;
- every completed tool result, which is in the transcript on disk;
- the compaction state, which `SessionCompactor.restore` reads back from the
  store.

**What it does not do:** re-run anything. The completed results reach the model
as transcript, not as fresh calls.

**What it never becomes: permission.** Nothing in `run_budget.py` touches a
permission decision. The gate is `permissions.decide`, asked per tool call
inside the loop, and it reads the tool name and its arguments — never how much
budget is left, never whether this run is a continuation. A `gmail_send` that
needed an exact approval before the stop needs the same exact approval after
Continue. The meter is constructed fresh in `run_turn`, the approval claim is
scoped to one run id, and a parked approval's scope is `once`.

The one thing that does span runs is a workspace shell grant the director
issued deliberately, through the workspace grant system. That grant applied
before the stop and applies after it, unchanged. Continuation neither creates
it nor widens it.

Declining costs nothing. The director who does not click Continue keeps every
completed result, every receipt, and the partial text, because all of it was
persisted as it happened.

## Tests

| File | What it covers |
| --- | --- |
| `tests/test_run_budget.py` | Ordinary work past eight steps; each of the six budgets; two at once; loop detection; the warning; the partial result; continuation, no replay, restart, compaction, the bound person; cancellation; retry; the meter on its own. Criterion 8 is parametrized over the real `ASK` set. |
| `tests/test_run_budget_mutations.py` | Each guard broken on purpose: a budget that never trips, a loop detector that never fires, budgets checked before the loop detector, a detector that ignores refusals, a continuation that grants authority, a partial result that claims completion, receipts that omit failed calls. |
| `surfaces/gui/tests/runBudget.test.tsx` | The allowlist projection, the live strip and warning, the stop card and its receipts, Continue sending the director's message, and the same after a restore. |

## Known gaps

- The warning rides on `tool_started`. A run whose every tool is
  approval-gated emits `permission_required` instead and gets no live warning
  before its stop, though the terminal payload still carries the full record.
  Adding a new event type would need `events.py`, which is outside this
  change.
- Budgets are per run. Continuing four times spends four budgets. There is no
  per-day or per-session ceiling.
- `estimated_cost_usd` cannot bind on a model with no pricing entry. The token
  ceilings are the backstop, and `unpriced_requests` is how the operator finds
  out.
