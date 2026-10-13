# Durable person tasks: execution and verification

Executed 2026-10-05/06 on Windows, Python 3.14 and the installed Node/Vite runtime.
Branch: `codex/durable-person-tasks`. Scope and decisions: [plan](plan.md).

## Implemented

- Shared Contacts → Tasks and person-file list/editor for all live saved people.
- Multiple dated/undated tasks; saved timezone, independent task versions and bounded reads.
- Transactional entity, append-only history, timeline event and durable save receipt.
- Exact retry replay, changed-intent/stale-edit conflicts, server-owned director attribution.
- Draft-preserving errors, exact uncertain retry, explicit conflict reconciliation and navigation protection.
- Registered people schema 3, startup backup, failure rollback/restore, live-parent triggers and Doctor JSON checks.
- Existing person records, handoffs and private sources preserved; task audit excluded from relationship claims and handoff invalidation. Task saves leave sequence membership unchanged.

## Automated results

| Check | Result |
| --- | --- |
| Clean external environment: `uv venv`, then `uv pip install --require-hashes -r requirements.lock` | Pass; offline `tzdata` installed from the declared lock |
| Clean-environment CRM, migration, person/API, brief/API regressions | **134 passed**, 14 deselected Windows workspace fixtures; command below |
| Final complete backend: `.venv/Scripts/python.exe -m pytest -q --tb=short` | **1,729 passed, 257 failed, 3 skipped, 2 errors**; known baseline failures, no new failed test IDs |
| Unchanged `main` exported to an external snapshot, same complete backend command | **1,695 passed, 258 failed, 3 skipped, 2 errors**; every final candidate failure is also in this baseline |
| Final frontend: `npm --prefix frontend test` | **589 passed, 1 failed** across 64 files; existing CRLF assertion described below |
| Final frontend: `npm --prefix frontend run build` | Pass: TypeScript and Vite production bundle |
| `python -m coworker.evals --suite sourcing --artifacts <external-directory>` | Fails with `PermissionError: eval child failed`; same command on unchanged main fails the same way |
| `git diff --check` | Pass |
| Independent read-only review and follow-up review | Three required findings fixed and regression-tested; no required findings remain |

The scoped clean-environment command (run from `backend/`) was:

```text
<clean-venv>/Scripts/python.exe -m pytest -q
tests/test_crm_tasks.py tests/test_crm_task_migrations.py
tests/test_people.py tests/test_people_api.py tests/test_brief.py
tests/test_living_brief.py tests/test_living_brief_api.py tests/test_migrations.py
-k 'not every_versioned_store and not unknown_future_sqlite and not unknown_future_json
and not missing_migration_step and not unreadable_json and not timestamped_backup
and not backups_can and not restore_backs and not secret_bearing and not backup_manifest
and not json_migration and not manifest_is_written and not sqlite_backups and not backup_files'
--tb=short
```

This scoped command is evidence for the feature, **not a full-suite pass**. The
complete suites were run without deselection. New migration fixtures independently
verify fresh/version 0/1/2 startup, row preservation, injected migration failure,
exact rollback, backup restoration with safety backup, and future-schema rejection.
New real HTTP tests cover two tasks, restart, shared IDs, concurrent identical
requests, changed retries, stale edits, date/timezone boundaries, title/details
limits, actor spoofing, missing/deleted/cross-person writes, FK-disabled raw
connections, transaction rollback and independent person reverts. UI tests cover
shared views, uncertain saves, conflicts, read failures, invalid date validation,
destination restoration and browser-history draft protection.

## Live demonstration

Started the actual FastAPI app with `provider=None`, no connected Gmail, a random
local token and isolated external sample state. Ran the actual frontend through
its development proxy. No normal operator data or credentials were used.

1. Saved Maya and Nora without active sequences. Contacts stayed empty.
2. Created “Send project examples” from the global list, dated October 9, 2026.
3. Created “Prepare intro notes” without a date from Maya's person file; edited
   its title and details there. Both global and person lists showed both tasks.
4. Attempted navigation with unsaved details. The editor kept its text, focused
   the leave prompt, and returned to the input after keyboard “Keep editing.”
5. Stopped the backend during an edit. The failed save retained the text and
   offered “Retry saved request.” Restarted against the same state and retried:
   the edit saved, both original tasks remained, and no duplicate appeared.
6. Opened a second app tab and changed the same task. The first tab's stale save
   showed the current saved version alongside its unsaved details. Explicitly
   adopted the latest version and saved; the person file showed the retained text.
7. Checked at 390 × 844: stacked fields remained usable, the page width stayed
   390 pixels, and the task table scrolled inside its pane. Checked actual date
   entry and Save with keyboard controls. Reset the viewport override afterwards.
8. Revisited the person file: no outreach, no active sequence, person version 1,
   two tasks. Task events remained in Timeline without appearing as learned facts
   or generated handoff content. No runtime console errors were captured in the
   final inspection.

## Known limits and next verification

- The complete backend suite is not green on this Windows host. Baseline failures
  include Unix directory-open/fsync behavior, permission/mode and path assumptions,
  process liveness, long temporary paths and eval child-process failures. The
  comparison found no new failing tests; one existing timing-sensitive budget
  test changed from failure to pass. These platform failures were not suppressed
  or repaired as part of the task ticket.
- `productionSpine.test.ts` expects literal LF text for the unchanged `App.tsx`;
  this checkout uses CRLF. Both files are unchanged from main. This assertion
  remains failing; the check was not weakened.
- Vite retains existing warnings about a marketing hero asset unresolved at
  build time and a JavaScript chunk above 500 kB.
- A packaged macOS/Tauri build and native close/quit/reopen smoke test require a
  supported Mac/CI host. The frozen build now explicitly collects timezone data,
  but its resulting native bundle was not built here. Run normal CI and native
  packaging gates before merge/release.
- Browser draft recovery was verified for failures, conflicts and protected
  navigation. This ticket does not add crash-persistent unsaved drafts; committed
  tasks and operation receipts are durable.

Logs, eval artifacts, sample state, dependency environments and credentials were
kept outside the repository. The implementation includes no sends, enrichment,
completion actions, assistant task writes or attention logic.
