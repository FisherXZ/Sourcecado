# Implementation plan: durable person tasks

Date: 2026-10-05. Parent: [CRM #199](https://github.com/FisherXZ/Sourcecado/issues/199).
Scope: human creation and editing of tasks, globally inside Contacts and in each person file. Executed on `codex/durable-person-tasks`.

## Product contract

- One person has many tasks. Title is required; details and due date are optional.
- **Approved adjustment to linked spec §5.1:** Contacts → Tasks includes every live saved person, including kept people without outreach. The Contacts people table retains Open / In conversation / Done. Task writes never start outreach or change sequence state.
- The pinned [engineering spec](https://github.com/FisherXZ/Sourcecado/blob/93bf013aa01fc7792eb16212bbc1d29b87d7c1bc/docs/superpowers/specs/2026-09-28-contacts-attention-engineering-spec.md) supplies storage, authority, migration and conflict requirements. Its completion, cancellation, undo UI, AI, descriptions, attention engine, previews and mail collection belong to later tickets.
- Gmail and model availability are irrelevant to manual task operations. State and credentials remain outside the checkout. Existing handoffs and restricted sources retain their behavior.

## Architecture decisions

1. Put tasks, append-only CRM changes and durable operation receipts in `people.db`. Reuse PersonStore's lock and connection with explicit transactions. Do not use a second task database or person snapshot replacement.
2. Register schema 2 → 3, execute statements transactionally, and use the existing migration backup/rollback machinery before modifying an existing database. New databases start at the current schema. Unknown newer schemas fail closed. Enable foreign keys on runtime and migration write connections; schema triggers additionally reject missing and soft-deleted people.
3. Task versions are independent of person versions. Updates compare the expected task version inside the write transaction; stale writes return 409 and the current task. Tasks remain independent of broad person revert.
4. One shared service performs title/details/date validation. HTTP adapters derive the director actor; request payloads cannot choose actor, origin, sources or state. Bounds: title 200, details 8,000 characters; ISO calendar dates only. No NLP date parsing.
5. Each save intent has a client-generated operation ID retained across retries. Hash the validated request including person/task identity and expected version. In one BEGIN IMMEDIATE transaction, check the live parent and prior operation, write task + change + readable timeline event + response receipt, then commit. Exact replay returns the original response even if the task subsequently changed; changed arguments conflict. A failure rolls back every write.
6. Persist the director's IANA timezone through the existing settings store, initial default `America/Los_Angeles` matching current scheduling. The backend stamps a task's date timezone from this preference. Store dates as YYYY-MM-DD, never midnight UTC. Existing task timezone stays fixed unless its date is changed; timezone preference changes do not rewrite deadlines. Do not change existing calendar/scheduler semantics.
7. Add bounded, paginated task reads (global and person-filtered) and a minimal paginated live-person picker without private sources. Both UI surfaces use identical task IDs and a shared list/editor. Refresh task data after committed saves and when revisiting a view. Preserve loaded data on read failure.
8. Store editor drafts separately from query responses. Retain the exact attempted payload/operation ID after an uncertain network failure; resolve it before allowing a different save intent. Show current saved text alongside unsaved text on conflict; only an explicit reconciliation action adopts the current version. In-app navigation protects unsaved drafts; explicit Cancel discards. Warn on window close with unsaved work. No optimistic committed rows.

## UI design contract

- Contacts retains its current default table; add Contacts / Tasks controls within the same destination. Tasks explains that it includes all saved people.
- Primary action: Add task. Compact rows show person, title, optional due date and Edit. Details expand/read in the editor. No completion checkbox until that behavior is implemented.
- Reuse Warm Operator tokens, restrained borders, dense typography and keyboard-accessible controls. Reference Take: compact table, initials/person links, date beside obligation. Leave: deal stages/values, owners, project manager controls, completion actions outside this ticket.
- Explicit Save / Cancel, visible saving/error/success states, inline field validation. Task and person selection remain accessible on narrow windows. Loading, empty and failed reads remain distinct; a failed refresh preserves rows.

## Ordered execution

See [todo.md](todo.md) for acceptance checklists and status.

1. Foundation: failing real HTTP/repository tests for persistence, retries, conflicts and missing parents; migration upgrade/rollback fixtures. Implement schema, transaction boundary, service, API and timezone preference.
2. Person-file slice: shared API types, task list/editor and date handling. Verify create/edit plus unsaved-text failure behavior in component tests.
3. Contacts slice: Tasks view and all-live-person selector, paging and consistent refresh. Verify both surfaces against shared saved fixtures and real local HTTP behavior.
4. Hardening and delivery: focused then full suites, frontend build, sourcing regression, browser create/edit/revisit and narrow layout, backend restart, migration restore, independent code review where available, documentation and verification record.

## Verification and failure matrix

| Behavior | Evidence |
| --- | --- |
| Two tasks (dated/undated), shared identity, edits and restart | Real authenticated FastAPI + disk database tests; browser journey |
| Commit happened but response was lost | Restart app/service, replay same operation, one task/change/receipt |
| Same ID with different changes / two stale editors | 409; saved data intact; component tests preserve drafts and show current text |
| Invalid/long inputs, actor spoof, cross-person task | 422/404; no entity/history/receipt created |
| Missing/deleted parent on every write boundary | API, service/repository and database constraint tests, foreign-key checks |
| Transaction failure after entity write | Inject history/receipt failure; assert all task/event/history writes rolled back |
| Upgrade fresh/version 0/1/2 and failure restore | Registered migration fixtures, preservation of people/attachments/handoffs/timelines; backup/restore assertions |
| Timezone boundaries and DST | ISO date round trips and saved timezone changes without day shift |
| Offline Gmail/model; no outreach effects | Provider absent, connector methods forbidden; people versions/sequence unchanged |
| UI error, keyboard, draft protection, paging | Vitest plus browser observations |

Commands: backend `.venv/Scripts/python.exe -m pytest` (Windows equivalent of CONTRIBUTING's venv command); `npm --prefix frontend test`; `npm --prefix frontend run build`; sourcing eval through its documented Python entry point. Use isolated temporary external state for real app checks. Browser bundle build does not certify a macOS native build; record unavailable checks honestly.

## Risks and mitigations

- Existing global person refresh resets some existing form state: task updates use task-specific refresh and keep drafts independently; avoid broad person refresh after task writes.
- SQLite foreign keys alone cannot reject soft deletion: validate under the same lock/transaction and install live-parent triggers. Preserve historical rows after deletion.
- Lost responses are different from definitive validation/conflict failures: retry the exact operation before permitting modified arguments, so a second intent cannot duplicate an uncertain create.
- Migration backup on Windows uses existing Unix-oriented permission helpers: establish runtime support and report platform limitations instead of weakening backup or skipping verification.
- Multi-window freshness is verified on focus/revisit; this ticket does not introduce a global synchronization framework.

## Verification record

Implemented and reviewed. See [verification.md](verification.md) for the complete
commands, baseline comparison, live create/edit/restart/conflict demonstration
and remaining platform limits. The clean feature regression set passes 134 tests;
the frontend build passes. Full suites retain existing Windows failures, and the
native macOS packaging gate remains to be run on a supported host.
