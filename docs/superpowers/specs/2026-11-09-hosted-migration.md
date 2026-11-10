# Hosted migration specification

Decision: [ADR 0007](../../engineering/adr/0007-hosted-club-workspace.md)
Plan: [slice plan](../plans/2026-11-09-hosted-migration-slices.md)
Baseline: tag `desktop-final` (`b17c091`)

The sourcing job in the
[spring spec](2026-08-25-sourcecado-sourcing-director-spring.md) is unchanged.
This spec changes where it runs and who can share it.

## Outcome

Officers of one club sign in at a web URL and work the same person files,
contacts board, chat sessions and run ledger. Each officer connects their own
Google account. Outreach still needs explicit review and approval, and goes
out from the approver's Gmail. The app runs on Vercel (frontend), Railway
(`api` and `worker`) and Supabase (Postgres and Auth).

## In scope

- Supabase Auth sign-in, club workspace, membership table, invite by email.
- Postgres behind one store interface for every store listed below.
- Mock run mode for development, CI and staging smoke tests.
- Encrypted per-user credentials and a hosted Google OAuth web flow.
- Separate worker service for agent runs.
- One-time import of a director's local data.
- Removal of desktop-only features and the Tauri shell.
- Staging environment on default domains.

## Out of scope

- Google OAuth verification and security assessment.
- More than one workspace per user in the UI. The schema allows it.
- Roles beyond `member` and `owner`.
- Custom domain and production cutover. Those follow a green staging smoke test.
- Auto-send of any kind.

## Store inventory

| Local store | Kind | Tables / contents | Slice |
|---|---|---|---|
| `club.db` | SQLite | sessions, memories, jobs, runs, settings, inbox, chat_queue, queue_commands, queue_sessions, recovery_commands | S03 |
| `conversations/` | JSONL dir | chat transcripts | S04 |
| `events/` | JSONL dir | presentation events | S04 |
| `people.db` (core) | SQLite | people, events, session_people, person_attachments, person_versions, reply_sync, person_descriptions, person_tasks, crm_changes, crm_operations | S05 |
| `people.db` (CRM/mail) | SQLite | gmail_messages, gmail_collection_state, crm_analysis_state, crm_analysis_attempts, crm_proposals, crm_interpretations, crm_review_dismissals, crm_task_evidence, crm_legacy_records | S06 |
| `agent_runs.db` | SQLite | agent_runs, agent_run_checkpoints | S07 |
| `drive_ingestion.db` | SQLite | drive_ingestion_jobs, _folders, _sources, _proposals | S08 |
| `meeting_evidence.db` | SQLite | meeting_evidence, meeting_candidates | S08 |
| `memory/` | directory | memory notes | S09 |
| `workspace_receipts.jsonl` | JSONL log | workspace receipts | S09 |
| `secrets.json` | JSON | provider keys, Google tokens | S10 |

Attachments and other blobs go to Supabase Storage under
`{workspace_id}/…`, with storage policies matching table RLS.

## Contracts

**Scope.** `Scope(workspace_id: UUID, user_id: UUID)` is built once per
request (or per worker claim) and passed to every repository call. A
repository never reads a global "current user".

**Store interface.** `coworker/db/` owns the connection pool (psycopg 3), the
transaction helper that runs `SET LOCAL app.workspace_id` / `app.user_id`, the
migration runner and the repository protocols. Each ported store keeps its
current public Python API. Its methods gain a `scope` argument, or the store
is constructed with one. Its SQL moves behind the protocol.

**Migrations.** Numbered plain-SQL files under `backend/migrations/`, applied
in order and recorded in `schema_migrations`. Every migration that creates a
table also:

- adds `workspace_id uuid not null references workspaces`,
- adds `created_by uuid references auth.users` where rows record an action,
- declares every `text` column `COLLATE "C"`,
- runs `ENABLE ROW LEVEL SECURITY` and creates the workspace policy,
- indexes `(workspace_id, …)` for each lookup the store makes.

Column types follow the SQLite meaning during the port. ISO timestamps stay
`text` so comparisons and test assertions don't change. Tightening types is a
later, separate change.

**Mock mode.** `SOURCECADO_MODE=mock` wires `FakeProvider` for the model and
fake Apollo, Gmail, Drive, Calendar and Granola connectors with deterministic
fixtures. Sends are recorded and never leave the process. Mock mode is
refused when `SOURCECADO_ENV=production`.

**Approval.** An approval records `approver_user_id`. Send uses that user's
Google credential and fails closed if it is missing or expired. It never
falls back to another officer's account.

**Worker.** `agent_runs` gains `lease_owner` and `lease_expires_at`. The worker
claims with `FOR UPDATE SKIP LOCKED`, renews the lease while it runs, and
resumes from the last checkpoint after a lease expires.

## Testing rules

- One Postgres per test session. CI uses a service container and locally it
  runs on Docker. Each test runs in a transaction that is rolled back, or in a
  fresh schema.
- Existing test assertions stay unchanged. A slice may change fixtures and
  imports, never expected values. If an assertion has to change, the slice
  stops and the change gets its own reviewed PR.
- Every ported store adds two tests: cross-workspace isolation (workspace B
  can't read workspace A through the repository) and RLS isolation (the same
  through a raw connection with only `app.workspace_id` set).
- `make test` runs fully in mock mode with no live keys.

## Acceptance

1. All backend and frontend suites pass on Postgres in mock mode.
2. No `sqlite3` import remains outside the import script.
3. A doctor check confirms RLS is on for every table in `public`.
4. Staging smoke test passes on the combined branch: sign in as two officers,
   keep a person, draft, approve and (mock) send, see the timeline from the
   second officer, run an agent run across an `api` redeploy, and import a
   sample desktop state.
