## User problem

Fisher (sourcing director) has no place in Sourcecado to record what he promised to do for
a person. Status alone can't express a commitment, its deadline, or who on the committee owns
it. This PR adds shared **Tasks** — visible both in a global Tasks view and on each person's
file — so "who do I follow up with, and what did I promise" is answerable at a glance.

Implements the assigned issue (shared tasks) plus two additions agreed during review: a
two-column Tasks view (all tasks / a chosen member's assigned tasks) and a sortable
owner/company on the Contacts table. Grounded in the engineering spec §§5.1, 5.2, 7.2, 8.3, 9.

## What changed

**Backend (`people.db`)**
- New `person_tasks` and `crm_members` tables and a `people.owner_member_id` column, added via
  a registered **v2 → v3 migration** (additive; existing people preserved).
- `PersonStore` methods: create/list/update tasks (idempotent create via `operation_id`;
  optimistic `version` checks raising `VersionConflict`), member add/list/archive, owner via
  the existing patch path. Tasks and `owner_name` surface in board/person reads.
- Routes: `GET /v1/tasks`, `GET/POST /v1/members`, `POST/PATCH /v1/people/{id}/tasks`,
  `PATCH /v1/people/{id}/owner`, and `DELETE /v1/people/{id}` (exposes the existing
  version-checked soft-delete).

**Frontend**
- A Contacts/Tasks in-page toggle. **Tasks view = two columns** (all tasks / assigned to a
  chosen member), each grouped Overdue/Today/Upcoming/No date, with a structured create/edit
  modal, a per-row **Edit** button, and an **Undo** toast after completing.
- Per-person task list + owner selector on the person file; a **Remove contact** action.
- Contacts table gains a sortable **Owner** column (and sortable company) and an **inline
  sequence dropdown**.
- Theme unchanged — reuses the existing Warm Operator tokens.

## Boundaries

- **Included:** durable shared tasks, committee-member labels, per-member assignment, contact
  owner + sortable columns, inline sequence editing, remove-contact, migration, tests.
- **Explicitly not included (later spec slices):** Gmail evidence→task analysis, attention
  ranking, editable descriptions, review proposals, a dedicated CRM undo ledger, and
  agent/chat tools for tasks.
- **Product invariants preserved:** single-user, local-first (SQLite `people.db`); exactly
  three sequence states; no auto-send; "company is context, not a deal." Committee
  "members/owner/assignee" are **lightweight local labels, not accounts or team tenancy**.

## Verification

### Automated tests
- Tests added/updated: `backend/tests/test_person_tasks.py` (shared-list IDs, restart
  persistence, idempotent create, version-conflict, migration preservation), delete-route
  tests in `test_people_api.py`, updated `test_migrations.py` (chain now ends at v3), and
  `frontend/tests/tasks.test.tsx` (two columns, member filter, complete, undo, edit, sort,
  inline sequence) plus task/member/owner API stubs in the person-file test mocks.
- Commands run: `cd backend && .venv/bin/python -m pytest -q` ; `cd frontend && npm test` ;
  `npm run build`.
- Results: backend **1963 passed, 3 skipped**; frontend **592 passed**; `tsc` and `vite build`
  clean.

### Live behavior demonstration
- Starting state: fresh local checkout of this branch, no Apollo/Gmail credentials.
- Action performed: seeded demo data, launched backend + GUI, exercised the Tasks view,
  member filter, create/edit/complete/undo, owner column + sorting, inline sequence, and
  remove-contact.
- Expected result: tasks appear identically in the global and per-person views; completing
  one updates both; a stale edit keeps the typed text; removing a contact drops it from
  Contacts; existing people survive the migration.
- Observed result: as expected (verified directly against the running backend; the full
  desktop/native walkthrough was not run).

#### How to run it + seed demo data
```bash
# 1. one-time: set an isolated dev state dir (keeps this out of your real data)
export CLUB_STATE_DIR="$HOME/.local/state/sourcecado-dev"

# 2. seed fictional contacts/members/tasks (idempotent; no credentials needed)
backend/.venv/bin/python backend/scripts/seed_demo.py

# 3. start the app (backend first, then GUI, in two terminals with the SAME export)
make backend        # terminal 1 — wait for :8765
make gui            # terminal 2
# open http://127.0.0.1:5180  → Contacts shows Maya/Nora/Leo; click the Tasks toggle
```
Your Contacts will be empty until you seed or create people — the app is local-first, so each
machine has its own database and nothing is shared.

## AI Accountability Note

### AI helped with
Planning the slice against the spec, implementing the backend entities/migration/routes and
the React UI, writing the tests, and running the test/build suites.

### I verified
<!-- Fill in what you personally ran/inspected before review. -->

### I am still uncertain about
- **Overdue timezone:** the UI buckets Overdue/Today against the *browser's* local date, not
  the stored `due_timezone` (spec §9.3). Fine on one machine; revisit for correctness.
- **Foreign keys:** references are validated in the service layer; `PRAGMA foreign_keys=ON`
  was not enabled globally (spec §7.2) — deliberate, flagged as a follow-up.
- **Scope deviation:** `owner` + member assignment are not in the spec (owner brushes the "no
  deal owners" non-goal); kept safe as local labels. Reviewer should confirm that's wanted.

## Safety and integrations

- [x] No credential, OAuth grant, token, private user data, or raw authorization material was committed or pasted into the PR.
- [x] External effects remain behind the correct permission and approval policy. (Sending/enrichment unchanged; tasks are local writes.)
- [x] Paid or credit-sensitive actions remain deliberate, approval-gated, and outside mandatory course work.
- [x] Source references and restricted data remain scoped correctly when applicable.

## Reviewer checklist

- [ ] The change matches the ticket and respects its non-goals.
- [ ] The normal and important failure paths are covered.
- [ ] Automated verification is appropriate and passes.
- [ ] The live behavior demonstration is credible when required.
- [ ] The diff is understandable and safe to merge.
- [ ] I am giving meaningful Peer Approval, not a rubber stamp.

---

## Review guide — session notes & what to watch out for

### What landed, by commit (oldest → newest)
1. `328cb25` — **backend:** `person_tasks` + `crm_members` tables, `people.owner_member_id`,
   a v2→v3 migration, `PersonStore` task/member methods, and the `/v1/tasks`, `/v1/members`,
   task create/update, and owner routes.
2. `fab21ae` — **frontend:** Tasks view (two columns), per-person task list, owner column +
   sortable company/owner, member management.
3. `ca6c6a1` — inline **sequence dropdown** on the Contacts table.
4. `67a880e` — per-row **Edit** button and an **Undo** toast after completing a task.
5. `74ceee9` — **Remove contact** (`DELETE /v1/people/{id}` over the existing soft-delete).
6. `3c18c2f` — pill-style the member filter to match the sequence dropdown.
7. `2da9e1c` — `backend/scripts/seed_demo.py` (idempotent reviewer seed data).

### Watch out for (please review these carefully)
- **Overdue uses the viewer's timezone, not the stored `due_timezone`.** Grouping
  (Overdue/Today/Upcoming) is computed client-side against the browser's local date. Correct on
  one machine; a known gap vs spec §9.3 for cross-timezone/DST.
- **Foreign keys validated in-service, not via `PRAGMA foreign_keys=ON`.** Deliberate (avoids
  flipping global enforcement on the shared connection); spec §7.2 wants the pragma — flagged
  as a follow-up with its own regression run.
- **Scope deviation from the spec:** committee **members / assignee / owner** and the sortable
  **owner** column are not in the spec; `owner` is adjacent to the "no deal owners" non-goal.
  Kept safe as **local labels only** (no accounts, no tenancy, no sync). Confirm this is wanted.
- **Tasks are not yet wired into the Contacts "Attention" column.** That column still reflects
  mail-derived follow-ups only; a person can have overdue tasks and show "—". Surfacing the
  nearest open task as a "Next action" is the natural next step (part of the deferred attention
  work).
- **No task delete/cancel in the UI.** Tasks can be completed/reopened; the `cancelled` state
  and any hard delete are not exposed yet.
- **`GET /v1/tasks` is unbounded** (no pagination) — fine at demo scale, revisit for volume.
- **Idempotency covers create only** (via `operation_id`); task updates rely on version checks.
- **Completion has a ~600ms client-side linger** before it persists (the strike-through
  animation); navigating away during it is harmless — the next load reflects server truth.
- **Removing a contact is a soft-delete;** its tasks are preserved in the DB but hidden from
  the global list (deleted people are filtered out of `list_tasks`).

### Operational gotchas for anyone running it
- **Single-user, local-first.** Each machine has its own `people.db`; nothing is shared. A
  fresh checkout shows **empty Contacts** until you seed or create people.
- **"No active contacts" almost always means the wrong state dir.** If `CLUB_STATE_DIR` isn't
  set, the app uses the empty default `~/.config/club`. Export the same absolute path in every
  terminal (and for the seed script), then relaunch.
- **"Couldn't load contacts" after restarting the backend = stale GUI token.** Vite bakes the
  backend token at GUI startup; if you restart the backend under a running GUI, restart the GUI
  too. Starting backend-before-GUI avoids it.
- **Migration is additive and was tested** on both fresh and legacy/populated databases;
  existing `test_migrations.py` version assertions were updated from 2 to 3.

### Deferred to later spec slices (not in this PR)
Gmail evidence→task proposals, deterministic attention ranking, editable short/long
descriptions, a dedicated CRM change/undo ledger, agent/chat tools for tasks, and the spec's
inline preview panel (we reused the existing person file + a modal instead).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
