## User problem

The director needs to save several promises for one person and edit them from
Contacts or that person's file, including before outreach and without Gmail or AI.

## Summary

```text
Contacts → Tasks ──┐
                  ├─ authenticated task service ─ people.db transaction
Person file ──────┘                                task + change + receipt
```

Both views use the same saved task IDs and shared editor. The global Tasks view
covers all live saved people, as approved by the director; the Contacts people
table retains Open / In conversation / Done.

## What changed

Added optional calendar dates with a saved director timezone, independent task
versions, durable operation IDs and conflict responses. Exact retries replay the
original receipt; changed retries conflict. The editor retains drafts through
errors and stale edits and protects navigation. People schema 3 upgrades through
registered backup/rollback, preserves existing records, and rejects orphan or
deleted-person task writes. Task audit events remain in Timeline while leaving
relationship claims and reviewed handoff freshness intact.

## Boundaries

- Included: human task creation/editing, both views, timezone preference and recovery.
- Deferred: completion, assistant writes/suggestions, attention logic and project-management features.
- Preserved: local single-director state, Chat home, person files, private sources,
  active sequence states and approval requirements for sending/enrichment.

## Evidence

- **Before:** Tasks had no durable API/UI; initial HTTP tests returned 404. Audit
  events displaced relationship evidence, and task saves marked handoffs stale
  in the regression tests added during review.
  **After:** 134 relevant tests passed in a clean hash-verified environment,
  including restart, retry, atomic rollback, invalid inputs, concurrency and
  migration restore. The handoff regressions pass.
- The final frontend build passes. The complete frontend suite reports 589 pass
  and one existing CRLF-sensitive assertion in unchanged files.
- The complete backend suite reports 1,729 pass, 257 fail, 3 skip and 2 errors on
  Windows. Unchanged main reports 1,695 pass, 258 fail, 3 skip and 2 errors;
  no final candidate failure is new. The sourcing CLI fails with the same Windows
  child-process PermissionError on both branches. These are not green full gates.

## Verification

Commands, deselections, baseline comparison and limitations are recorded in
[tasks/verification.md](verification.md). Tests added exercise the real
authenticated FastAPI app and disk state, plus the shared UI and full App shell.
Existing assertion updates follow the new schema target and distinguish the
additional Contacts link and Tasks loading status without removing checks.

### Live behavior demonstration

With isolated sample state, no Gmail and `provider=None`, created one dated task
globally and one undated task in Maya's file. Edited from both views and verified
shared contents; Maya remained outside the active Contacts table. Stopped and
restarted the backend during an edit: the exact retry saved retained text without
a duplicate. A real second-tab edit produced a stale conflict showing current
text alongside the draft; explicit reconciliation saved it. Keyboard navigation
and the 390-pixel layout were checked.

## AI Accountability Note

### AI helped with

The implementation plan, persistence/API/UI changes, tests, browser checks,
documentation and independent code review. Review caught and drove fixes for
handoff freshness, native date validation and undeclared timezone data.

### I verified

The coding agent ran the checks and live demonstrations recorded above, inspected
the baseline failures and re-reviewed the fixes. Human peer approval is still required.

### I am still uncertain about

Full supported-host CI and the packaged macOS/Tauri close/quit/reopen smoke test
must run before merge/release. The frozen build collects timezone data, but its
native bundle was not built on this Windows host.

## Merge Danger

**Door:** One-way schema compatibility. Code rollback alone does not roll back
the schema; preserve tasks written since the migration backup before restoring it.

**Blast Radius:** CRM. Includes people database startup/migration, person-file
brief projection and task navigation guards. Existing data preservation and
failure rollback have dedicated tests.

## Safety and integrations

- [x] Credentials, runtime state and private operator data remain outside the repository.
- [x] Sending and credit-spending enrichment retain approval requirements.
- [x] Task writes call no external connector or model and do not activate outreach.
- [x] Restricted sources are preserved and absent from the task picker.

## Reviewer checklist

- [ ] Verify the ticket scope and approved all-saved-people adjustment.
- [ ] Review the save transaction, migrations and normal/failure tests.
- [ ] Run supported-host CI and native packaging/smoke checks.
- [ ] Give human Peer Approval before merge.
