# Durable Agent Runs: identity, leases, and checkpoints

Status: active-stack engineering reference. Covers the run store and its
ownership rules only. Approval fencing, restart resume, and the chat, queue,
schedule, and UI wiring are separate later work and are not implemented here.

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
and stamps it in `PRAGMA user_version`, which is the contract the migration
registry reads.

### States

| State | Meaning |
| --- | --- |
| `running` | A live process holds the lease and is working the run. |
| `waiting_approval` | Parked on an operator decision. Nobody owns it. |
| `waiting_input` | Parked on a question to the operator. Nobody owns it. |
| `waiting_external` | Parked on an external effect whose outcome is unknown. |
| `interrupted` | The owner died or ran out of lease. Needs classification. |
| `complete`, `partial`, `stopped`, `failed` | Terminal. The run never moves again. |

Waiting and terminal runs hold no lease. A lease means "a process is driving
this run right now", so parking or finishing releases it in the same
transaction as the state change.

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

## Extension points

The following are named seams, not implementations.

- **Approval fencing.** `acquire_lease` refuses waiting states on purpose. A
  resolution-gated acquire belongs beside it, so that only a resolved approval
  can return a lease to a parked run.
- **Restart and resume.** `reclaim_dead_owner_leases` classifies interrupted
  work and stops. Deciding what is safe to replay is separate, and a tool whose
  outcome is unknown must not be replayed by default.
- **Heartbeat.** `renew_lease` is the seam a long provider call renews through.
  Losing renewal must abandon the work, not continue it.
- **Wiring.** Nothing constructs an `AgentRunRepository` in the running
  application yet. The process that starts the sidecar registers one owner with
  `OwnerRegistry.register()` and calls `reclaim_dead_owner_leases()` once, at
  startup. The repository deliberately does not reconcile in its constructor,
  because opening a store is not the same event as starting a process.

## Known gaps

- The run store is a separate database from `club.db`, so a run checkpoint and a
  conversation or approval write are not one transaction. Slice 3 must make the
  run store the fence of record for external effects rather than relying on
  cross-store atomicity.
- Owner marker files are removed when their owner is proven dead and its work
  reclaimed, and when a process releases its own owner. A process killed with
  no runs to reclaim leaves a marker behind.
