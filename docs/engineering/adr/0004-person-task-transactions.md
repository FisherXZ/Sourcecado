# Person tasks share the person database and transaction authority

Status: Accepted — 2026-10-05

## Context

A director needs to keep several promises for one saved person, with optional
dates. Contacts and the person file must show the same saved tasks. A retry after
a lost response must recover the committed result, and a stale edit must preserve
both the saved version and the director's draft. Gmail and model availability
must not affect this work.

## Decision

Store `person_tasks`, append-only `crm_changes` and `crm_operations` in
`people.db`, using PersonStore's lock and one explicit transaction for each save.
Task versions are independent of person versions. Each operation fingerprints
the validated intent and stores its committed response beside the audit change.
Exact retries replay that response; changed arguments and stale versions conflict.
Live-parent checks and database triggers prevent orphan or deleted-person writes.

The director approved Tasks across **all live saved people**, including kept
people before outreach. The Contacts people table retains active sequences.
Saving a task leaves sequence membership unchanged.

## Alternatives considered

A separate task database would split parent validation, entity writes and audit
receipts across transaction boundaries. Tasks embedded in person snapshots would
couple independent edits and broad person reverts. Both make retry and recovery
harder to reason about than a shared database with independently versioned rows.

## Consequences

Schema 2 → 3 uses the registered backup/rollback path before startup changes
existing data. Person reverts preserve tasks and their history. Task audit events
remain in the timeline but do not become relationship evidence or mark reviewed
handoffs stale. Completion, assistant writes and attention projections remain
later work. See [the task contract](../person-tasks.md) and
[execution evidence](../../../tasks/verification.md).
