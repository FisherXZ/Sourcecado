# Durable person tasks

Manual tasks attach to live saved people. Contacts → Tasks (`#/board/tasks`) and
the person file use the same task IDs, list endpoint and editor. The global picker
includes kept people without sequences. Task operations require the existing
local API token and do not invoke connectors, enrichment, sending or the model.

The [transaction decision](adr/0004-person-task-transactions.md) explains the
storage boundary. Runtime data remains in the external Sourcecado state root.

## HTTP contract

| Route | Input / behavior |
| --- | --- |
| `GET /v1/crm/tasks` | Optional `person_id`; `limit` 1–100 (default 50), `offset` ≥0; tasks plus `next_offset` |
| `GET /v1/crm/people` | Minimal live identity picker; optional `query` ≤200 characters, same pagination |
| `POST /v1/people/{person_id}/tasks` | `operation_id`, required `title`, optional `details` and `due_date`; returns task + receipt, 201 |
| `PATCH /v1/people/{person_id}/tasks/{task_id}` | `operation_id`, positive `expected_version`, one or more editable fields; returns task + receipt, 200 |
| `GET /v1/crm/preferences` | Saved `timezone` |
| `PUT /v1/crm/preferences` | Only `timezone`: valid IANA identifier |

Title is 1–200 characters, trimmed; details are at most 8,000 characters. Both
reject NUL. Dates are valid `YYYY-MM-DD` calendar dates or null. Requests cannot
set author, origin, state, sources or task ownership. Unknown/deleted people and
cross-person task IDs return 404; invalid inputs return 422. Conflicts return
409 with an error and the current task when an edit version is stale.

An operation ID is a stable 1–128 character identifier. The service hashes the
validated fields, person/task IDs and expected version. In one transaction, it
checks the live parent and replay, writes the task, audit change, timeline event
and response receipt. Exact retries return the original response, even after a
later task edit; reusing the ID with different changes conflicts. The UI freezes
an uncertain save's intent until retry or explicit discard. Definitive errors
and conflicts keep editable text; adopting a newer version requires a separate
director action, followed by Save.

## Dates and recovery

The existing settings store holds `director_timezone`, initially
`America/Los_Angeles`. Settings → Task dates edits it. The backend stamps the
saved timezone when a task is created or its date changes; a title-only edit and
a preference change preserve the existing date timezone. Calendar dates never
round-trip through midnight UTC. The frontend formats their calendar day in UTC
solely for display, without converting the stored date.

Python's [ZoneInfo documentation](https://docs.python.org/3/library/zoneinfo.html#data-sources)
recommends declared `tzdata` for systems without IANA data. The universal,
hash-verified requirements lock includes it, and the frozen-backend build collects
its data. This is an offline dependency, independent of live connectors.

People schema 3 adds the three task tables, indexes and live-parent/history
triggers. Runtime and migration people connections enable foreign keys. Existing
state upgrades through registered migrations with a backup before any constructor
mutation; failed upgrades roll back and retain the backup for Doctor restore.
Future schemas fail closed. Doctor validates task/history/receipt JSON, including
nullable before snapshots. Historical tasks remain stored after soft deletion
but are hidden from live lists and reject further edits.

See [verification](../../tasks/verification.md) for real-app, restart, failure,
migration and browser evidence and the platform checks still needed.
