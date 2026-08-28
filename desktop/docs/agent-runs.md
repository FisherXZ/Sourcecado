# Durable Agent Runs: identity, leases, and checkpoints

Status: active-stack engineering reference. Covers the run store, its ownership
rules, the external-effect fence, restart classification, and -- since slice 5
-- the production paths that enter them. Chat, queued chat, scheduled routines,
approvals, and the operator review queue all run through the store now;
`coworker/agent_run_dispatch.py` is the seam and
`coworker/agent_run_reconcile.py` is the rule for reading two stores at once.

## What an Agent Run is

An Agent Run is one durable unit of assistant work with one identity. Chat
turns, queued chat items, and scheduled prompts all produce the same kind of
run, distinguished only by `trigger`. The run identity is what recovery,
approvals, ledger entries, and the board can all point at after a restart.

A run row carries its correlation to the rest of Sourcecado: the session it
belongs to, the person file it works, the approvals it raised, the sources it
read, the artifacts it produced, the usage it spent, and how it ended.

The run store is its own SQLite database, `agent_runs.db`, in the local state
directory. It declares `SCHEMA_VERSION` in `coworker/agent_run_repository.py`
and stamps it in `PRAGMA user_version`. Version 2 adds `agent_run_effects`.

### Versioning

`agent_runs.db` is registered in `coworker/migrations.py` as `agent_runs_db`,
on the same `PRAGMA user_version` channel as every other SQLite store, with two
steps: an adoption step for the store as it shipped before the registry knew it
existed, and `1 -> 2`, which adds the external-effect fence.

The division of labour matters and is the same for every store in the registry.
**The store owns its DDL. The registry owns its version.** `migrations.py` is
the only writer of `PRAGMA user_version` in the codebase, with one narrow
exception: `AgentRunRepository` stamps a database it creates from nothing,
because creating a database at the current version is not a migration. Changing
an existing database's version is, and only the registry does that.

The consequence is the point. Starting the application never silently upgrades
a store. An existing version 1 database gains the fence tables on open, because
`CREATE TABLE IF NOT EXISTS` runs either way and a store waiting to be migrated
should still be fenced rather than unprotected -- but it stays recorded as
version 1 until Doctor migrates it deliberately, with a backup taken first and
a rollback if the step fails. A user who rolls back to an older build after
merely running the app still finds a version that build can open.

`EFFECT_STATEMENTS` is a tuple of separate statements rather than one script for
one reason: `sqlite3.Connection.executescript` issues a COMMIT before it runs.
A migration step that used it would end the transaction `_apply_store` opened,
and the rollback would then silently have nothing to undo. The step executes the
statements one at a time, and SQLite rolls DDL back like anything else.

Two properties are asserted rather than argued.
`test_the_effect_schema_only_creates_objects_that_did_not_exist` fails the
moment the schema needs a column on an existing table, which is the case
`CREATE TABLE IF NOT EXISTS` would silently skip.
`test_the_fence_step_never_commits_the_transaction_it_runs_inside` fails if the
step ever stops being rollback-safe, and proves it is not vacuous by showing
`executescript` really does commit.

## Ownership

A lease names an owner and an expiry. Every durable write is a compare-and-swap
against `(run_id, version, lease_owner)` and increments `version`. The version
is the fencing token: a superseded owner, or a duplicate of a live owner holding
the same lease object, fails the swap and writes nothing. It never writes
second.

Two mechanisms take a lease back, and the difference between them is the whole
correctness argument.

**Expiry.** A lease past `lease_expires_at` may be reclaimed by anyone. This is
safe even when the old owner is alive, because that owner cannot renew an
expired lease and cannot commit against one: its next call fails the swap.

**Proven death.** Startup may also reclaim an unexpired lease, but only from an
owner it can prove is gone. Proof is a kernel fact, not a heuristic. Each owner
process holds an exclusive `flock` on its own marker file under
`agent_run_owners/` for the life of the process. The kernel releases that lock
when the process exits, by any means, and never before.

- The marker locks against a recovering process, the owner is alive.
- The marker is free, the owner is dead.
- Another host, a missing marker, or a platform without `flock` is unknown.

Unknown never authorizes a reclaim. An unknown owner's run is not stranded: its
lease still expires, and expiry reclaim is already fenced.

This matters because "the application is starting, so every previous owner must
be dead" is false. A second sidecar can be launched beside a working one. Under
that assumption the new process steals live leases and two owners commit state
for one run. `reclaim_dead_owner_leases` refuses to make that assumption.

A run is created already owned by its creator, so `running` with no lease is not
a normal state. It can only follow a crash between creating a run and owning it,
and startup reclaim classifies it as interrupted.

## Checkpoints

A checkpoint records semantic progress: which step, which tool, which approval,
how much was spent, how it ended. It never records content.

The payload is an allowlist, not a denylist. `agent_runs.CHECKPOINT_PAYLOAD_FIELDS`
names every field a checkpoint may carry, and anything else is dropped before
persistence. Message bodies, model reasoning, tool arguments, and tool output
have no field to land in, so they cannot arrive by accident. Adding a field is a
deliberate, reviewable change.

Allowed string fields are additionally passed through `redact_secrets`, so a
credential quoted inside an error summary or an id is masked. Reference URLs
keep scheme, host, and path only, because query strings carry access tokens.
The originating goal is stored as a SHA-256 fingerprint, never as text; the
operator's words stay in the transcript.

Each checkpoint kind declares the states it may be appended from and the states
it may leave behind. `coworker/agent_run_state.py` holds that one table, and
state reachability is derived from it rather than restated, so the store, later
resume paths, and Doctor cannot drift apart.

## External effects

Some effects reach a real person and cost real money. `gmail_send` is the case
the fence exists for. Between dispatching one and recording what happened there
is a window, and a process that dies inside it leaves a fact nobody holds: the
mail may have gone out, or it may not have. That is not `failed` and it is not
`succeeded`, and calling it either is the mistake with no undo.

So the store records the dispatch **before** the call and the outcome **after**
it. The ordering is the whole recovery argument. A process that dies before the
dispatch commit made no call. A process that dies after it may or may not have,
and an effect with a dispatch and no outcome is `ambiguous`: quarantined until a
person settles it.

`coworker/agent_run_approval.py` holds the vocabulary, and it is two disjoint
halves.

| Half | Values | Who may write it | From |
| --- | --- | --- | --- |
| Machine | `succeeded`, `failed` | only the process that dispatched | `dispatched` |
| Operator | `resolved_succeeded`, `resolved_failed`, `abandoned` | a named person | `ambiguous` |

No value belongs to both, so "the machine concluded this" and "a person
concluded this" can never be confused when the record is read back. The
database enforces the separation itself. `EFFECT_SCHEMA` carries a `CHECK` that
refuses an operator status without a `resolved_by`, and triggers that abort an
update crossing out of `ambiguous` into a machine outcome, an update that
changes a settled outcome, an insert that opens anywhere but `dispatched`, and
any delete at all. A retry, a resume, a second owner, and a raw SQL edit all hit
the same wall.

The effect row is content-free for the same reason a checkpoint is. It carries
the tool name, the approval id, the call id, and a SHA-256 fingerprint of the
arguments. The recipient, subject, and body stay in the transcript and the
approval record; what the run store needs is only whether the call it is about
to make is the call it already dispatched.

An effect record is never deleted. `prune_checkpoints` drops step detail for old
runs and touches `agent_run_effects` never, because the record that something
left the machine is not step detail.

### Which tools are fenced

`coworker/permissions.py` already owns this decision and the fence does not
restate it. `RETRY_SAFE` is the list of tools that can re-run without producing
a second external effect, and `agent_run_approval.replay_class` reads it:
anything outside that list is `consequential`. `gmail_send` is deliberately
absent from `RETRY_SAFE`, which is what stops a provider retry replaying a send,
and it is the same absence that makes the run store fence it. A tool the
permission module has never heard of is consequential, so the fence fails
closed rather than guessing.

### The approval door

`acquire_lease` refuses waiting states on purpose: a parked run belongs to a
person. `resume_from_approval` is the one way back, and it is narrow. It needs
the id of an approval the run actually raised, and it grants the lease and moves
the run to `running` in a single transaction, so the run never rests parked and
leased at once -- a pair Doctor reports as a record contradicting itself.

A run parked in `waiting_external` is not opened by that door at all. Its effect
is quarantined, and an approval decision says nothing about whether a send
already went out.

## Restart

`coworker/agent_run_resume.py` decides and never runs. Restart asks two
questions in order.

Which leases are free? `reclaim_dead_owner_leases` answers that from expiry and
from proven death, and it never takes a lease from a live owner. Whatever is
still held after it runs is genuinely held.

Of the work that is now free, which is safe to continue? `classify_resume`
reaches one of five verdicts from the run row, its checkpoints, and its effects.

| Verdict | When |
| --- | --- |
| `nothing` | Terminal, parked on a person, or still owned by another process. |
| `quarantine` | An effect was dispatched and never reported back. |
| `review` | Already quarantined, or a consequential tool with no effect record. |
| `deliver` | A terminal result is on record and its delivery is not. |
| `resume` | Safe incomplete work, under the same run identity. |

Effects outrank the run's own state. What the run was doing matters less than
whether something already left the machine.

`deliver` exists so that a crash after the model produced the final answer does
not buy that answer twice. The run row already carries the shape of the result
-- status, message id, text length -- written in the same transaction as the
checkpoint that recorded it, and the text itself is in the transcript. Asking
the model again would spend tokens to produce a different answer.

`restart()` performs exactly one kind of write: it quarantines. That is the only
decision that cannot wait, because an effect nobody knows the outcome of must
stop being mistakable for work in progress before anything else looks at the
run. Resuming and delivering are left to the caller.

## The trap, and where the wiring avoids it

A consequential tool **must** open an effect record before it is called.
`dispatch_effect` commits before the call and `record_effect_outcome` commits
after it, and that ordering is the entire recovery argument: a process that dies
before the dispatch commit made no call, and one that dies after it may or may
not have.

Call a consequential tool without `dispatch_effect` and a crash mid-call leaves
a `tool_pending` checkpoint with no effect record. `classify_resume` then returns
`REVIEW` -- it will not resume that run and there is no effect row for a person
to settle, so the run needs manual attention. That is the safe direction and it
is deliberate, but it is a trap: the failure shows up only after a crash, on the
one code path nobody exercises by hand.

`agent_run_approval.replay_class` names which tools this applies to. It reads
`permissions.RETRY_SAFE`, and everything outside that list is consequential,
including a tool the permission module has never heard of.

Slice 5 answers the trap with one function rather than discipline.
`agent_run_dispatch.guarded_call` is the only way either production path
reaches a tool, and the ordering is control flow inside it: `dispatch` returns
only after its transaction committed, and the call cannot run before `dispatch`
returns. `turn.py` and `server.py` each call it once. Neither calls `execute`
around it.

Three consequences follow, and each is a test.

- Every tool outside `RETRY_SAFE` is fenced, not a list of the ones anyone
  thought of. `agent_run_dispatch.needs_fence` is `replay_class` and nothing
  else.
- A call that *raises* is quarantined rather than reported. `record_effect_outcome`
  has no value for "nothing at all", so the guard does not invent one.
- **A layer below the fence can make that unreachable, and one did.** The guard
  only ever sees what the tool lets past. `_execute_bound_send` and
  `tools.execute` caught every Gmail exception and returned `ok=False`, so a
  send that timed out after the request left the machine was recorded as
  `failed` and the operator was offered a clean second send. The connector is
  the only layer that can tell the two apart, so `gmail.GmailOutcomeUnknown` is
  raised there when no HTTP status was ever observed, and both call sites
  re-raise it. A status means Gmail answered, and an answer is a fact; no
  status means the request left and nothing came back. `verify_send_authority`
  reads before the send, so an unknown from that step stays a plain
  `SendAuthorityError` and never reaches the queue.
- A caller that supplied a run store and could not open a run refuses to make
  the call. A caller that supplied no run store was never fenced, which is the
  legacy and test path. The two are different values, not the same `None`.

## Extension points

The following are named seams, not implementations.

- **Heartbeat.** `renew_lease` is the seam a long provider call renews through.
  Losing renewal must abandon the work, not continue it.
- **Wiring.** `create_app` registers one owner with `OwnerRegistry.register()`
  and calls `agent_run_resume.restart()` once, because that is the function
  that starts the sidecar process. The repository still does not reconcile in
  its constructor, because opening a store is not the same event as starting a
  process, and `create_app` opens it a line earlier than it becomes an owner.
- **The two halves of at-most-once.** `store.decide_and_claim_inbox_execution`
  makes an approved send dispatch at most once: only a `pending` row is
  claimable, so a claim closed as `interrupted` is never re-executed. This
  store makes the outcome of that dispatch un-guessable after a crash. Neither
  re-implements the other -- nothing in `agent_run_reconcile` grants
  permission to run -- and `agent_run_reconcile.reconciled_status` joins them:
  **the run store is the fence of record when they disagree**, because its
  writes bracket the external call and the inbox's do not. The inbox knows a
  claimant vanished. Only the run store knows a call was dispatched.
- **The review queue.** `list_quarantined_effects` is what a surface renders,
  and `GET /v1/agent-run-effects/quarantine` renders it, joined to the approval
  that authorized each effect. `POST` to the same path with one of the three
  operator decisions settles one. The route deliberately does not live under
  `/v1/agent-runs`, where `run_ledger_api` owns `{run_id}` and would swallow a
  literal segment; and `run_ledger_api` stays read-only, as its docstring says.
- **Receipts.** `agent_run_approval.external_effect_evidence` maps an unsettled
  effect onto the `Evidence` vocabulary, and the review queue reads it, so a
  queue row and a receipt use the same word for the same fact. A run receipt
  reaches the same conclusion from checkpoints alone: `quarantine_effect`
  writes `tool_outcome_unknown`, which `run_receipt._tools` already reports as
  `Evidence.AMBIGUOUS`. `record_effect_outcome` now carries `tool_call_id` on
  its checkpoint for the same reason `quarantine_effect` always did -- without
  it a fenced call read as one call that never finished plus one outcome
  belonging to nothing.

## Known gaps

- The run store is a separate database from `club.db`, so a run checkpoint and a
  conversation or approval write are not one transaction. The run store is the
  fence of record for external effects instead of relying on cross-store
  atomicity; `agent_run_reconcile` is where that preference is expressed.
- An approval decided from the operator surface is executed under a run of the
  server's own, not the turn's. That avoids a race for one lease between a
  live turn and the HTTP executor, at the cost of a second run row per such
  send. The effect still names the approval, which is the key the review queue
  joins on.
- A turn cancelled while an approval is outstanding resumes its run with
  `deny`, because `resume_from_approval` takes allow or deny and a cancel is
  neither. The word "cancelled" survives in the inbox and the transcript; the
  run store records the nearest true thing, which is that the call was not
  authorized.
- A quarantined effect is joined to its approval by scanning `approval_id`,
  which carries no index. Adding one is a schema change and therefore a
  migration. The table holds one row per consequential call and is read when a
  person opens the queue, so the scan is not on any hot path.
- Owner marker files are removed when their owner is proven dead and its work
  reclaimed, and when a process releases its own owner. A process killed with
  no runs to reclaim leaves a marker behind.
- A process whose lease expired, whose effect was quarantined by someone else,
  and which then wakes up genuinely knowing the send succeeded cannot write what
  it knows. `record_effect_outcome` raises instead. That is deliberate -- a
  person may already have acted on the quarantine -- but the observation is
  lost unless the caller logs it. There is no way to attach a late, non-binding
  observation to a quarantined effect.
- A quarantined run comes to rest `interrupted`. Settling its effect makes the
  run eligible to resume, but nothing decides whether resuming a turn whose send
  may already have gone out is what the operator wants. That is a person's next
  decision and no code makes it.
- **`restart` classifies and only quarantines.** `plan_restart` returns `RESUME`
  and `DELIVER` verdicts, and `create_app` stores the outcome on
  `app.state.run_restart` without acting on either. So a safe incomplete run is
  not continued at startup, a final answer that was generated and recorded but
  never delivered is not delivered, and a missing person projection is not
  repaired. Those are three of issue #63's acceptance criteria and they are
  tracked separately; this file describes what runs, not what is planned.
