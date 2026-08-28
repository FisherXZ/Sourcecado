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

Stop the sidecar before running `repair` or `restore`. Doctor does not
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
- **Interrupted approvals** — approvals that were authorised but never reported
  an outcome.
- **Permission drift** — anything under the state directory that group or other
  can read or write.
- **Runtime dependencies** — the modules the sidecar cannot start without,
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

Every detail line passes through a redaction filter that strips private keys and
`key: value` credential assignments, and every finding is capped: at most 8
detail lines, at most 200 characters per line, at most 60 findings, and at most
16000 characters in the whole report. A finding that affects 200 files says so
by its count, not by listing 200 paths.

`test_no_planted_secret_survives_into_doctor_output` plants a live-shaped API
key, an OAuth access token, a refresh token, a message body, and a line of
private reasoning across `club.db`, `people.db`, the transcripts, the receipt
log, `secrets.json`, and `.env`, then asserts none of them appear in the
rendered report or its JSON. `test_no_secret_survives_a_repair_backup_manifest`
does the same for the backup manifest.

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

Stop the sidecar first. Restore replaces files under the state directory and
does not coordinate with a running process.

## If a migration fails

A step that raises rolls its own store back from the backup taken at the start
of the run, and the run stops there. That store keeps its old version, so a
rerun sees it as still pending and tries again. Stores that already migrated in
that run keep their new version and a rerun treats them as current. Migrations
are idempotent: running Doctor twice does the work once.

## Not covered yet

Doctor does not detect a stale run owner, a dead lease, or an impossible
checkpoint transition. All three need the canonical Agent Run identity that
issue #63 introduces; nothing on disk today can tell an abandoned owner from a
live one. The place they belong is `_check_run_ownership` in
`coworker/doctor.py`, which deliberately reports nothing rather than guessing.
