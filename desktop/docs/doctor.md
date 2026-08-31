# Sourcecado Doctor and the migration registry

Doctor inspects Sourcecado's local state before a sprint build, an update, or a
recovery. It is diagnostic by default. It changes nothing unless you ask it to,
and even then it only makes changes whose correct outcome is not a judgement
call.

## Words used here

- **State directory** — the folder holding Sourcecado's local databases, logs,
  tokens, and connector state. Defaults to `~/.config/club/`. `CLUB_STATE_DIR`
  overrides it. Doctor prints it as `<state>` and never prints it in full.
- **Store** — one durable thing in the state directory. A SQLite database, a
  JSON document, an append-only JSONL log, or a directory of them.
- **Registry** — the single list in `coworker/migrations.py` that names every
  store, the version it is currently at, and the steps that bring an older copy
  forward.
- **Finding** — one problem Doctor found. It carries how many records it affects
  and whether Doctor can fix it.
- **Automatic repair** — a change with exactly one correct outcome. Doctor may
  apply it after a backup.
- **Review required** — a real problem that Doctor will not touch, because
  fixing it means guessing, or means deciding something about a person file or
  an external action.

## Running it

```
make doctor            # inspect and report. Changes nothing.
make doctor-repair     # back up, then apply only the automatic repairs.
```

Or directly:

```
cd desktop
.venv/bin/python -m coworker.doctor                     # check (the default)
.venv/bin/python -m coworker.doctor --json              # same report as JSON
.venv/bin/python -m coworker.doctor repair
.venv/bin/python -m coworker.doctor backups
.venv/bin/python -m coworker.doctor restore <backup-id>
.venv/bin/python -m coworker.doctor --state /path/to/state
```

Stop the backend before running `repair` or `restore`. Doctor does not
coordinate with a running process.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Healthy, or the repair succeeded. |
| 1 | Findings need attention. Nothing was changed by a `check`. |
| 2 | Doctor cannot trust what it read. No repair ran. |

## What Doctor starts

Nothing. It imports no provider, no connector, and no server. It opens no
socket. Every database is read through a SQLite read-only handle, so a check
cannot write even by accident. Two tests hold this line:
`test_doctor_never_imports_the_model_or_the_connectors` runs Doctor in a fresh
process and fails if the model or connector modules appear in `sys.modules`, and
`test_doctor_runs_with_networking_disabled` makes every socket call raise.

## The stores

| Store id | Path | Kind | Where its version lives |
| --- | --- | --- | --- |
| `conversation_db` | `club.db` | SQLite | `PRAGMA user_version` |
| `people_db` | `people.db` | SQLite | `PRAGMA user_version` |
| `drive_ingestion` | `drive_ingestion.db` | SQLite | `PRAGMA user_version` |
| `meeting_evidence` | `meeting_evidence.db` | SQLite | `PRAGMA user_version` |
| `conversation_transcripts` | `conversations/` | JSONL directory | `state_versions.json` |
| `presentation_events` | `events/` | JSONL directory | `state_versions.json` |
| `workspace_receipts` | `workspace_receipts.jsonl` | JSONL log | `state_versions.json` |
| `workspace_grants` | `workspace_grants.json` | JSON document | `version` key |
| `shell_tasks` | `shell_tasks.json` | JSON document | `version` key |
| `host_command_approvals` | `host_command_approvals.json` | JSON document | `version` key |
| `directory_requests` | `directory_requests.json` | JSON document | `version` key |
| `secrets` | `secrets.json` | Opaque file | `state_versions.json` |
| `mcp_config` | `mcp.json` | JSON document | `state_versions.json` |
| `memory_notes` | `memory/` | Directory | not versioned |
| `workspace_trash` | `workspace_trash/` | Directory | not versioned |
| `dotenv` | `.env` | Opaque file | not versioned |

One more SQLite database is checked but is **not** in the registry:

| Store id | Path | Kind | Where its version lives |
| --- | --- | --- | --- |
| `agent_runs_db` | `agent_runs.db` | SQLite | `PRAGMA user_version` |

The Agent Run store has one schema version and no upgrade path yet, so there is
nothing for the registry to migrate. Doctor reads its version directly from
`coworker/agent_run_repository.SCHEMA_VERSION` and fails closed the same way: a
run store from a newer build is reported, blocks every repair, and is not read.
Because it is not registered, it does not appear in the `stores` table of the
report and its permissions are not covered by the drift check. Registering it
belongs in `coworker/migrations.py`.

The version lives with the data wherever the format can carry it. SQLite has
`PRAGMA user_version`, which needs no table of ours and commits inside the same
transaction as the migration. The workspace JSON documents already write a
top-level `version` key, so the registry reuses it. An append-only JSONL log and
an opaque secret file cannot carry a document header without changing their
write path, so their version is recorded in `state_versions.json` beside them.

`memory_notes` and `workspace_trash` are derived or disposable, and `.env` is
plain configuration. They are checked for permissions but never migrated.

## Version numbers

Version 0 means "written before this registry existed". Every store's 0 to 1
step adopts it: it completes the shape that store's constructor used to grow by
hand, then records version 1.

Supported prior version: 0. Each store's upgrade from it is covered by a
production-shaped fixture in `desktop/tests/state_fixtures.py`, built from the
DDL those stores actually shipped, populated with realistic rows — not a toy
table.

A store found at a version this build does not know **fails closed**. Doctor
reports it, refuses every repair in the whole run, and writes nothing at all.
The same rule applies to a store that cannot be read and to a store with no
registered path forward.

`coworker/store.py` and `coworker/people.py` still run their own
create-if-missing and column-existence blocks at construction. Those are
harmless and idempotent, but they are not release versioning. The registry is
the contract. Removing those blocks is a follow-up, not part of this change.

## What Doctor checks

- **Versions** — every store against the registry: behind, current, ahead, or
  unreadable.
- **Database integrity** — `PRAGMA integrity_check` on each SQLite store.
- **Corrupt rows** — every column that must hold JSON (`inbox.arguments`,
  `runs.artifacts`, `events.payload`, `person_attachments.fields_json`, and the
  rest) parsed and counted.
- **Torn JSONL** — an unterminated final line from a crash mid-append is a torn
  *tail*. A damaged line anywhere else is a torn *line*, and is not the same
  problem.
- **Record versions** — presentation events that are not at the current event
  version.
- **Schedule** — runs pointing at a job that is gone, jobs whose next run time
  will not parse, runs carrying a status outside the schedule contract, and runs
  in a final status with no finish time.
- **Orphaned links** — queued messages, approvals, retry chains, person session
  bindings, and ledger events pointing at something that no longer exists.
- **Agent Run owners and leases** — leases that have expired, owners the kernel
  confirms have exited, and owners whose liveness cannot be proven. See below.
- **Agent Run history** — checkpoints recording a transition the state machine
  forbids, a run that checkpointed after it finished, a hole in a sequence that
  is dense by construction, a run row that disagrees with its own last
  checkpoint, and a lease held in a state that holds no lease.
- **Agent Run links** — the approvals, sessions, person files, and parent runs a
  run names, and checkpoints naming a run the store does not hold.
- **Interrupted approvals** — approvals that were authorised but never reported
  an outcome.
- **Permission drift** — anything under the state directory that group or other
  can read or write.
- **Runtime dependencies** — the modules the backend cannot start without,
  resolved through the import finder so nothing is executed.

## What Doctor will and will not repair

Automatic, after a backup:

| Repair | Why it is safe |
| --- | --- |
| `permissions.tighten` | Restoring owner-only modes changes no data. |
| `jsonl.truncate_torn_tail` | The unterminated last line is already skipped on read. Dropping it has one correct outcome. |
| `migration.apply` | A registered, tested, idempotent upgrade step. |

Never automatic:

- Corrupt rows and torn lines inside a log. What they held cannot be
  reconstructed.
- Orphaned queue items. They hold text a person wrote.
- Orphaned approvals, person bindings, and ledger events. Person history is not
  deleted by Doctor.
- Interrupted approvals. Whether the external action happened is unknown, and
  guessing either way is worse than reporting it.
- Schedule inconsistencies. Choosing a new run time or a new status is an
  operator's call.
- Anything at all, once a store has failed closed.

## Bounded and redacted output

Doctor reports store ids, versions, check names, row ids, and counts. It does
not report row contents, message bodies, tokens, authorization headers, or
private reasoning. Paths are printed relative to `<state>`; an absolute home
path never appears.

This holds in two layers, and both are tested.

**The first layer is structural.** A check reports what it counted, not what it
read: a corrupt row becomes `inbox.arguments rowid 4`, never the value in that
column. This is the layer that does the real work.

**The second layer is `redact()`,** a filter every summary and detail line passes
through on the way into a finding — so it covers the JSON output too, not just
the rendered text. It strips PEM private key blocks, `key: value` credential
assignments, absolute paths, and bare high-entropy tokens. It exists for the few
fields Doctor prints verbatim because the finding is *about* them: a session id,
an approval id, an unexpected run status.

Telling a secret from an identifier is done by character mix. Sourcecado's own
identifiers — `per_<32 hex>`, `run_<32 hex>`, a sha256, an ISO timestamp — use at
most two of lowercase, uppercase, and digits. Issued credentials mix all three,
or start with a known issuer prefix (`sk-`, `ya29.`, `ghp_`, `AKIA`, `AIza`, and
the rest). Anything under 24 characters is left alone. So `per_0f3c9a…` survives
intact and `sk-live-AbC123…` becomes `[redacted]`.

Known limit: a bare token that draws on fewer than three character classes and
carries no known prefix — a 40-character all-lowercase string, say — would be
truncated but not redacted if it landed in an identifier field. The structural
layer is what makes that unreachable in practice today; the filter is the
backstop, not the guarantee.

Every finding is capped: at most 8 detail lines, at most 200 characters per
line, at most 60 findings, at most 16000 characters in the whole report. A
finding that affects 200 files says so by its count, not by listing 200 paths.

### How this is proven

`test_every_store_kind_holds_a_planted_secret` asserts each of the six store
kinds is carrying a live-shaped canary — an API key, an OAuth access token, a
refresh token, a message body, a line of private reasoning — planted in
`club.db`, `people.db`, the transcripts, the event log, the receipt log, all
five JSON documents, `secrets.json`, `.env`, and a memory note. It fails if a
store ever stops carrying one, so the leak tests cannot quietly stop testing it.

`test_no_planted_secret_survives_into_doctor_output` then asserts Doctor
produced a finding *for each of those stores* before asserting no canary appears
in the report or its JSON. Without that first assertion the test would pass for
a store Doctor never mentioned, which proves nothing.

Five stores — `secrets.json`, `.env`, `mcp.json`, `workspace_grants.json`, the
memory notes, and the three remaining JSON documents — have no content check of
their own. Their only route into the report is a permissions finding, whose
detail list is capped at 8 lines. Eight carriers against a cap of 8 leaves no
headroom, so `test_no_planted_secret_survives_from_a_drift_only_store` proves
coverage in batches that fit under the cap and asserts every carrier was named
in some batch, rather than assuming they all fit at once.

`test_no_planted_secret_survives_a_repair_or_its_report` covers the repair path
and every backup manifest. It asserts the repair actually ran — the exact set of
applied repairs, a backup id, exactly one manifest, and named files inside the
backup — before it asserts anything about leaks. That guard exists because the
test originally planted an unreadable JSON document, which is a fail-closed
condition: `repair` correctly refused, wrote no backup, and the leak assertions
passed over a no-op. The blocking defect now belongs only to the diagnose test.

`test_redact_strips_credentials_without_mangling_identifiers` tests the filter
directly, including the negative cases: ten real identifiers that must come back
byte-for-byte unchanged. `test_a_secret_in_an_identifier_field_is_redacted_from_the_report`
drives a credential through a session id and a run status — the two fields
printed verbatim — and proves the backstop catches it.

Every one of these was checked by mutation, because a test that cannot fail is
not evidence. Each of the following breaks the suite: making `redact()` an
identity function; dropping only its bare-token rule; making a check emit row
content; making `repair` a no-op; making the permissions finding stop naming
files; and removing the canary from any single store.

## Backups

Any repair takes a backup first. If the backup cannot be written, nothing is
repaired.

Backups live at `<state>/backups/doctor-<UTC timestamp>Z/`, mode 0700. Each
holds a copy of every store the repair touches plus a `manifest.json` recording,
per store: its id, kind, relative path, size, SHA-256, file mode, and the
version it was at. SQLite files are copied through SQLite's online backup API,
so a database that was open still copies consistently.

**Secret-bearing stores (`secrets.json`, `.env`, `mcp.json`) are recorded but
never copied.** Doctor never changes their contents, so a hash and a mode are
enough to verify them and to put their permissions back. A second copy of a
credential sitting on disk is a cost with no matching benefit. The manifest
marks these with `"content_backed_up": false`.

### Inspecting a backup

```
cd desktop
.venv/bin/python -m coworker.doctor backups
```

That lists every backup newest first with its id, timestamp, reason, and the
stores it holds. The files themselves are plain: read `manifest.json` with any
JSON tool, and open a copied `club.db` with `sqlite3` directly.

### Restoring a backup

```
cd desktop
.venv/bin/python -m coworker.doctor restore doctor-20260827T164500123456Z
```

Restore takes its own safety backup of the current state before overwriting
anything, and prints that safety backup's id. So a restore is itself
reversible. Only stores with `"content_backed_up": true` are put back.

Stop the backend first. Restore replaces files under the state directory and
does not coordinate with a running process.

## If a migration fails

A step that raises rolls its own store back from the backup taken at the start
of the run, and the run stops there. That store keeps its old version, so a
rerun sees it as still pending and tries again. Stores that already migrated in
that run keep their new version and a rerun treats them as current. Migrations
are idempotent: running Doctor twice does the work once.

## Agent Runs

An **Agent Run** is one durable unit of assistant work with one identity, stored
in `agent_runs.db`. A **lease** names the one process allowed to write to a run
and the moment that right runs out. An **owner** is that process.
`desktop/docs/agent-runs.md` is the design record.

### How Doctor decides an owner is dead

Only when the kernel says so. Nothing else counts.

Every owner process holds an exclusive `flock` on its own marker file under
`agent_run_owners/` for as long as it runs. The kernel releases that lock when
the process exits, by any means, and never a moment sooner. Doctor asks
`OwnerRegistry.liveness_of` — the same function the run store's own startup
recovery asks — and gets one of three answers:

| Answer | What Doctor reports |
| --- | --- |
| The marker locks against Doctor | `agent_run.owned_by_live_process`, severity info |
| The marker is free | `agent_run.dead_owner`, severity warn |
| Anything else | `agent_run.unknown_owner`, severity info |

"Anything else" means the lease belongs to another host, the marker file is
gone, the marker directory is gone, or the platform has no file locking. All of
those are **unknown**, and unknown is reported as unknown.

This distinction is the whole point. Reporting an unknown owner as dead would
invite an operator to take a run away from a process that is still working it,
which is exactly what the `flock` design exists to prevent. An unknown owner
does not need rescuing: its lease still expires, and reclaiming an expired lease
is fenced by version, so a superseded owner writes nothing rather than writing
second.

An **expired lease is a separate finding** and is decided before liveness is
consulted at all. Once a lease has run out, whether its owner is alive changes
nothing — that owner cannot renew and cannot commit. So a live process holding
an expired lease is reported as `agent_run.expired_lease` and never as a dead
owner.

`test_a_live_run_owner_is_never_reported_as_stale` is the test that holds this
line. It starts a real second process that takes a real lease and holds its
marker, then asserts Doctor reports it alive, reports no dead owner, no expired
lease, and no stale run, and calls the install healthy. Two more tests cover the
unknown cases: a deleted marker file and a lease recorded against another host.

Doctor also never touches the marker directory. It will not create one, and it
will not read markers through a directory whose permissions have been loosened,
because `OwnerRegistry`'s constructor would restore the mode and a check may not
change state. A loosened directory is reported as
`agent_run.owner_markers_permissive` and its owners stay unknown. Losing a proof
of death is the safe direction to lose one in.

### Which transitions are impossible

Doctor does not carry its own copy of the rules. `coworker/agent_run_state.py`
holds one table naming, for each checkpoint kind, the states it may be appended
from and the states it may leave behind. The run store validates every write
against that table, and Doctor walks the stored checkpoints back through the
same `validate_transition`. A check cannot drift away from the rule it checks.

What is reported:

| Finding | What it means |
| --- | --- |
| `agent_run.checkpoint_after_terminal` | A run checkpointed after it finished. A terminal run releases its lease and never moves again. |
| `agent_run.impossible_transition` | An edge the table forbids, or a run that opens with something other than `run_started`. |
| `agent_run.checkpoint_gap` | The sequence has a hole. See below. |
| `agent_run.state_mismatch` | The run row and its own last checkpoint disagree, though both are written in one transaction. |
| `agent_run.lease_on_unleasable_state` | A lease is held on a waiting or terminal run. Parking or finishing releases the lease in the same transaction as the state change. |
| `agent_run.unsupported_record` | A state, trigger, or checkpoint kind this build does not know. |

`agent_run.unsupported_record` fails closed and stops there. A record Doctor
cannot read is not a record Doctor may call wrong, so no transition verdict is
reached for that run. "This build cannot express it" and "this is broken" are
different reports and one must not decay into the other.

### Orphaned is not pruned

Retention drops step detail for long-finished runs on purpose
(`RunLedger.prune_checkpoints`, 30 days). Pruned is not orphaned, and Doctor
tells them apart three ways.

**By shape.** A checkpoint sequence is dense from 1 to the run's
`checkpoint_sequence`, and retention only ever removes a leading run of it. So a
dense tail that reaches the expected sequence is pruned evidence, and anything
else is damage. Doctor does not reimplement that rule: it uses
`run_evidence.analyze_record`, the same function the run receipt uses, and reads
its `pruned_through` and `damaged` fields. A fully pruned run reports nothing.

**By direction.** Pruning deletes checkpoint rows and never touches the run row.
It can leave a run with no steps; it can never leave a step with no run. So
`agent_run.orphaned_checkpoint` is always damage.

**By store.** Approvals are never deleted by anything in Sourcecado, so an
approval id a run names that the inbox does not hold is genuinely dangling and
not aged out.

A fourth rule keeps the check honest in the other direction: **no answer is not
an orphan.** If `club.db` or `people.db` is missing or unreadable, Doctor cannot
tell whether a session, approval, or person file still exists, so it reports
nothing for those links rather than calling them broken.

### What Doctor will not do to a run

Nothing. Every Agent Run finding is report-only. There is no repair, no proposed
repair, and no write of any kind to `agent_runs.db`.

That is deliberate. The two repairs an "obvious" fix would reach for — releasing
a stale lease and marking an abandoned run interrupted — are exactly the two the
run store already does correctly at startup, under a lease, fenced by version,
in one transaction with the checkpoint that records it. Doctor holds no lease
and cannot acquire one. A Doctor that wrote the same rows would be a second
writer racing an owner it may not be able to prove is gone, which is the failure
the whole design exists to prevent. Starting the backend is the repair.

### Not covered yet

- **Queue items are not linked to runs.** `chat_queue` carries no run id and a
  run row carries no queue item id, so there is no link to check. The only queue
  link a run has is the session it belongs to, which is checked.
- **`inbox.run_id` is not checked against the run store.** That column holds
  turn-loop identities (`run_<hex>`), which are a different namespace from Agent
  Run identities (`run-<hex>`), and nothing writes the latter into it yet.
  Checking it today would report every approval on every install as orphaned.
- **`agent_runs.db` is not in the migration registry**, so it is absent from the
  report's store table and from the permission drift check.
- **The scan is unbounded in memory.** Every run row and every checkpoint is
  read at once. Output is bounded; the read is not.
