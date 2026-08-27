# Priority 0 Durable Agent Run Implementation Plan

Date: 2026-08-26
Status: Priority 0 in progress; Slices A and B complete
Architecture basis: `2026-08-26-evidence-reconciled-agent-os-roadmap.md`
Primary donor: OpenWorker prompt-driven `TurnEngine` lifecycle
Contract reference: OpenClaw Session / Agent Run / model-turn distinction

## Engineering judgment

The approved roadmap points in the right direction. Sourcecado already owns a
strong live-turn substrate: stable event identity, semantic tool receipts,
cooperative cancellation, durable approvals, restart-safe unknown-outcome
handling, transcript repair, and a shared low-level `run_turn` path for chat and
scheduling.

The dependency root is identity and authority. Today the UUID-like `run_id` in
chat events describes one live turn, while SQLite's integer `runs.id` describes
only a scheduled receipt. Neither is an authoritative, goal-sized Agent Run.
Suspension, restart recovery, compaction, usage, and terminal delivery should
not be added until one durable run record owns them.

Priority 0 therefore lands as narrow vertical slices around the existing loop.
It does not replace the provider, tool, approval, event, scheduler, or UI
subsystems wholesale.

## Locked decisions

1. `TurnIdentity.run_id` becomes the canonical Agent Run identity for new work.
2. A new `agent_runs` table owns execution state. The existing integer `runs`
   table remains a schedule-receipt projection during migration.
3. `agent_run_checkpoints` stores ordered semantic checkpoints. Raw token
   deltas, credentials, authorization headers, private source bodies, and
   hidden reasoning never enter checkpoints.
4. `waiting_approval` and `waiting_question` are suspended, nonterminal states.
5. A completed deliverable is immutable. A later revision starts a new run with
   `parent_run_id` linking it to the prior result.
6. Restart recovery never blindly replays an external write. Work is classified
   as not started, safe to retry, completed, or outcome unknown before resume.
7. Chat, queued chat, and scheduling invoke one Agent Run engine. Scheduling is
   a trigger and receipt surface, not a second runtime.
8. Skills remain optional tool-loaded context recorded in `skills_loaded`; they
   are not execution boundaries.

## Target flow

```text
prompt or saved prompt
        |
        v
start Agent Run -----> semantic checkpoint -----> model turn
        |                                        /          \
        |                                  final answer     tool call
        |                                                       |
        |                         +-----------------------------+
        |                         v
        |                 safe tool / approval / ask_user
        |                         |            |
        |                         |            +--> suspend durably
        |                         |                    |
        |                         +<------ answer or approval
        |                                      |
        +<------------- resume same run -------+
        |
        v
terminal delivery checkpoint -> complete / partial / stopped / failed
```

## Slice A — Canonical run and checkpoint spine

Status: complete and reviewed

Implementation commits: `56d4461..aae4be7`

Verification:

- Agent Run tests: 30 passed.
- Full Python suite: 373 passed, 1 skipped.
- GUI suite: 336 passed.
- Production TypeScript/Vite build: passed.
- Independent spec review: compliant.
- Independent code-quality review: ready; no Critical or Important findings.

Add the authoritative `agent_runs` and `agent_run_checkpoints` records, then
instrument the existing loop without changing its user-visible control flow.

Required behavior:

- Persist run identity, owning session, trigger, original goal, optional parent,
  current state, provider/model, aggregate skills, source and artifact refs,
  measurable usage, terminal result, checkpoint sequence, and timestamps.
- Record semantic checkpoints for start, user input, completed model response,
  completed tool, approval wait/resolution, interruption, and terminal result.
- Aggregate only normalized source/artifact references already safe for the UI.
- Mark an orphaned `running` record `interrupted` on store reopen while leaving
  waiting states intact for later resume support.
- Link each legacy scheduled receipt to its canonical `agent_run_id`.
- Keep all existing schedule status and API behavior compatible.

Acceptance tests:

- Chat event identity and canonical Agent Run identity match.
- Start is idempotent and terminal checkpointing is exactly once.
- Checkpoint order, metadata merging, and usage counters survive reopen.
- Running runs become interrupted on reopen; waiting runs remain waiting.
- Chat, queued chat, and schedule triggers create the same canonical row shape.
- Successful skill loads and tool provenance aggregate without storing deltas.

## Slice B — Durable ownership, leases, and restart resume

Status: complete

Implementation commits: `73d7cd3..6bd48a6`

Verification:

- Full Python suite: 616 passed, 1 skipped.
- GUI suite: 336 passed.
- Production TypeScript/Vite build: passed.
- Startup recovery resumes the same canonical identity and repairs linked
  schedule receipts.
- Explicit resume tests cover competing owners, completed-tool replay
  prevention, outcome-unknown review, and torn projection repair.
- Person-file tool events now carry their owning canonical `run_id`.

Make checkpoints authoritative for continuation rather than treating JSONL
repair as resume.

Required behavior:

- Add compare-and-swap versioning and a bounded execution lease so WebSocket,
  HTTP approval, scheduler, and startup recovery cannot resume the same run.
- Checkpoint before model request, after semantic model result, before tool
  execution, and after tool result.
- Persist the continuation cursor, visible partial output, pending interaction,
  completed tool receipts, and remaining work/delivery budget.
- On restart, resume safe incomplete work under the same `run_id`.
- Never replay a consequential tool whose outcome is unknown; surface a review
  state and require an explicit recovery choice.
- Carry the canonical `run_id` onto person-file ledger events.

Acceptance tests:

- App reconstruction resumes the same run without repeating a completed tool.
- Two competing resume actors produce one owner and one continuation.
- A crash during an approved external action becomes outcome-unknown and does
  not execute again automatically.
- Torn transcript/event tails recover from the latest committed checkpoint.

## Slice C — Human suspension inside the same run

Generalize the existing durable approval substrate into run interactions while
preserving the different semantics of questions and permissions.

Required behavior:

- Add engine-controlled `ask_user` with a bounded question and answer contract.
- Persist `waiting_question` or `waiting_approval` without terminalizing the
  Agent Run.
- Route an answer or approval to the owning run and resume from its checkpoint.
- Resume scheduled runs after interaction rather than executing only the
  approved tool and closing the workflow.
- Render and restore question state in Chat; Inbox remains the durable place to
  recover a missed interaction.

Acceptance tests:

- `ask_user` survives app reconstruction and the answer continues the same run.
- A scheduled approval continues through final model delivery under one run.
- Repeated answers/decisions are idempotent and never duplicate external work.

## Slice D — Bounded context, usage, and terminal delivery reserve

Bound the model view without deleting canonical history, and guarantee a useful
delivery phase when research consumes the work budget.

Required behavior:

- Extend provider chunks/results with finish reason and available token usage.
- Build an outbound context view that always preserves the original goal,
  current checkpoint, pending work, relevant source/artifact refs, and recent
  complete model/tool groups.
- Compact only the outbound view; canonical messages and checkpoints remain.
- Separate work/tool budget from terminal-delivery budget.
- Disable tools during the reserved delivery pass.
- If delivery cannot finish, return the best useful partial result with explicit
  gaps instead of starting another research round.

Acceptance tests:

- Large canonical history produces a bounded, valid model request without
  dangling tool calls.
- Canonical history remains byte-for-byte available after compaction.
- Repeated tool choices exhaust only the work budget, then force one
  tools-disabled delivery pass.
- Usage totals are monotonic across suspension and resume.

## Slice E — Product acceptance

- Re-run the outreach campaign from one prompt through the director-review
  package without a second “converge now” message.
- Re-run the company pitch package through its review deliverable under one run.
- Exercise at least one saved prompt through scheduling and an interaction wait.
- Restart during research, approval wait, and delivery; verify the same run ID,
  no duplicate external action, preserved partial output, and a terminal result.
- Export the run record and semantic checkpoints and verify they contain no
  credentials, hidden reasoning, or token-delta rows.

## Not in Priority 0

- Knowledge Workspace indexing or global Drive replacement.
- Per-run writable artifact workspace, shell, or office-file skills.
- Connector descriptor migration and provider validators.
- Semantic trace delta coalescing beyond the checkpoints required for resume.
- Multi-agent orchestration, team tenancy, or automatic sending.

## Principal risks

- Scheduler receipt IDs and Agent Run IDs must never be silently conflated.
- SQLite checkpoints and JSONL messages/events are not one atomic write today;
  Slice B must name which record is authoritative after each crash boundary.
- Approval recovery must retain the existing “outcome unknown” safety rule.
- The current Anthropic adapter does not implement the same tool loop as the
  OpenAI-compatible adapter; provider parity must be explicit before it can be
  claimed.
- `turn.py` and `server.py` are already large. New state transitions should live
  behind a focused Agent Run service rather than growing ad hoc branches.

Resolved Slice A follow-ups:

- Slice B extracted an `AgentRunRepository` that shares the existing SQLite
  connection and lock.
- The resume migration rollback test now fails after prior durable state exists
  and proves the transaction and migration marker roll back together.

## Session checklist

- [x] Read the approved roadmap, mature-system blueprint, domain language,
  golden-run evidence, and active desktop code.
- [x] Verify the clean linked worktree and green baseline test suite.
- [x] Lock the identity and migration strategy.
- [x] Land Slice A with focused and full verification.
- [x] Review Slice A for roadmap compliance and code quality.
- [x] Begin Slice B only after the canonical run record is proven.
- [x] Land Slice B with leases, atomic continuation checkpoints, explicit and
  startup resume, schedule projection repair, and person-ledger identity.
- [x] Verify Slice B with the full Python and GUI suites and a production build.
- [ ] Begin Slice C human-question suspension as the next Priority 0 branch.
