# Durable tasks execution checklist

## 1. Durable save foundation (no dependencies)
- [x] Create/edit/list API persists multiple tasks and uses server-derived actor.
- [x] Exact retries replay receipts; changed requests and stale versions conflict; partial failures roll back.
- [x] Fresh/legacy upgrades, backup/restore and live-parent constraints pass.
Files: people.py, migrations.py, new crm_repository.py / crm_service.py / crm_api.py, backend/tests/test_crm_tasks.py and migration tests.
Verification: focused backend tests and existing people/migration checks.

## 2. Person file tasks (depends on 1)
- [x] Shared list/editor creates and edits dated/undated tasks with saved timezone.
- [x] Errors/conflicts retain text, uncertain saves reuse the same intent, navigation protects drafts.
- [x] Existing person-file sections remain working.
Files: frontend/src/crm/*, api.ts, PersonFile.tsx, frontend/tests/task*.tsx.
Verification: focused component tests and frontend build.

## 3. Contacts Tasks view (depends on 2)
- [x] Tasks control and person picker cover all live saved people.
- [x] Both lists show committed identity and edits; task creation never activates Contacts membership.
- [x] Paging, loading, empty, refresh-error and narrow-window behavior work.
Files: Board.tsx, shared task UI/styles, frontend tests.
Verification: shared UI journey, real API and browser journey.

## 4. Delivery verification (depends on 1–3)
- [x] Relevant suites/eval/build executed; feature checks pass and full-suite platform failures classified against main.
- [x] Browser create/edit/revisit, date/no-date, keyboard and narrow layout checked; restart verified.
- [x] Documentation updated, diff reviewed, verification evidence/limits recorded in plan.
- [x] Incremental changes committed on feature branch; no unsolicited merge/deploy.

Delivery gates still requiring a supported host: full CI and the packaged native
macOS smoke test. See [verification.md](verification.md).
