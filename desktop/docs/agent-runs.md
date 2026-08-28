# Durable Agent Runs: identity, leases, and checkpoints

Status: active-stack engineering reference. Covers the run store, its ownership
rules, the external-effect fence, and restart classification. The chat, queue,
schedule, server, and UI wiring are separate later work and are not implemented
here: nothing in this document is called by the running application yet.

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

## A trap for whoever wires this up

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

## Extension points

The following are named seams, not implementations.

- **Heartbeat.** `renew_lease` is the seam a long provider call renews through.
  Losing renewal must abandon the work, not continue it.
- **Wiring.** Nothing constructs an `AgentRunRepository` for run execution in
  the running application yet. The process that starts the sidecar registers one
  owner with `OwnerRegistry.register()` and calls `agent_run_resume.restart()`
  once, at startup. The repository deliberately does not reconcile in its
  constructor, because opening a store is not the same event as starting a
  process.
- **The two halves of at-most-once.** `store.decide_and_claim_inbox_execution`
  makes an approved send dispatch at most once. This store makes the outcome of
  that dispatch un-guessable after a crash. Joining them -- so that a quarantined
  effect and an `interrupted` inbox row are one thing an operator sees -- is
  wiring, and the run store is the fence of record when they disagree.
- **The review queue.** `list_quarantined_effects` is what a surface renders.
  Nothing renders it yet.
- **Receipts.** `agent_run_approval.external_effect_evidence` maps an unsettled
  effect onto the `Evidence` vocabulary `run_receipt` already reads. Wiring it
  into a receipt section is later work.

## Known gaps

- The run store is a separate database from `club.db`, so a run checkpoint and a
  conversation or approval write are not one transaction. Slice 3 must make the
  run store the fence of record for external effects rather than relying on
  cross-store atomicity.
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
