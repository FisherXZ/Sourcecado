# Hosted migration slice plan

Spec: [hosted migration](../specs/2026-11-09-hosted-migration.md)
Decision: [ADR 0007](../../engineering/adr/0007-hosted-club-workspace.md)
Baseline: tag `desktop-final` on `b17c091`

Each slice is one PR on a branch named `hosted/sNN-<name>`, cut from `main`
after the slices it depends on have merged. Slices merge in number order. The
column "Wave" says which slices can be built at the same time. Build is done
by Claude agents in separate worktrees, at most three at once, each opening
its own PR.

## Rules for every slice

- Read the spec's Contracts and Testing rules first.
- Every query takes a `Scope` and filters on `workspace_id`.
- Text columns are `COLLATE "C"` in the migration, never in queries.
- RLS on, with the workspace policy, in the migration that creates the table.
- Existing assertions stay unchanged. Fixtures and imports may change.
- Add the cross-workspace and RLS isolation tests for each ported store.
- `make test` passes in mock mode with no live keys.
- Expect conflicts in `migrations.py`, `doctor.py`, `server.py` and the
  migration numbering. Rebase on `main` and take the next free number.
- The PR description fills in the repository's pull-request template.

## Slices

| # | Branch | Wave | Depends on | What it does |
|---|---|---|---|---|
| S00 | `hosted/h00-decision-docs` | 0 | — | ADR 0007, this plan, the spec. Update AGENTS.md and docs/README.md. Tag `desktop-final`. |
| S01 | `hosted/s01-db-foundation` | 1 | S00 | `coworker/db/`: pool, `Scope`, scoped transaction helper, SQL migration runner, `workspaces` and `workspace_members` tables with RLS, Postgres test fixture, CI service container. No store moves yet. |
| S02 | `hosted/s02-mock-mode` | 1 | S00 | `SOURCECADO_MODE=mock`: FakeProvider plus fake Apollo, Gmail, Drive, Calendar, Granola connectors with fixtures. `make dev-mock`. Refuse in production. |
| S03 | `hosted/s03-conversation-store` | 2 | S01, S02 | Port `club.db` tables in `store.py` to Postgres behind the interface. |
| S04 | `hosted/s04-transcripts-events` | 2 | S01, S02 | Port the JSONL transcripts and presentation events to append-only tables. |
| S05 | `hosted/s05-people-core` | 2 | S01, S02 | Port core `people.db` tables (`people.py`, `crm_tasks.py`). |
| S06 | `hosted/s06-people-crm-mail` | 3 | S05 | Port CRM analysis and Gmail evidence tables (`crm_*_schema.py`, `crm_mail_repository.py`). |
| S07 | `hosted/s07-agent-runs` | 3 | S03 | Port `agent_runs.db` (`agent_run_repository.py`). Add lease columns. |
| S08 | `hosted/s08-drive-meeting` | 3 | S05 | Port `drive_ingestion.db` and `meeting_evidence.db`. |
| S09 | `hosted/s09-notes-receipts-blobs` | 4 | S03, S05 | Memory notes and workspace receipts to tables. Attachments to Supabase Storage. |
| S10 | `hosted/s10-credentials` | 4 | S01 | Encrypted per-user `credentials` table replacing `secrets.py`. Key from env. Doctor check that the key is set. |
| S11 | `hosted/s11-auth-workspace` | 4 | S01 | Supabase Auth on the frontend, JWT check and `Scope` resolution in FastAPI, invite flow, `approver_user_id` on approvals, CORS. |
| S12 | `hosted/s12-google-web-oauth` | 5 | S10, S11 | Hosted Google OAuth redirect flow per user. Send uses the approver's credential and fails closed. Testing-mode allow-list documented. |
| S13 | `hosted/s13-worker` | 5 | S07 | `python -m coworker.worker`: claim with SKIP LOCKED, lease renewal, resume from checkpoint. `api` only enqueues. |
| S14 | `hosted/s14-remove-desktop` | 5 | S03–S09 | Delete workspace shell/file tools, local PDF isolation, update channel, packaging, `src-tauri`, stdio MCP, user skill folders. Drop SQLite branches from `migrations.py` and `doctor.py`. Add the RLS-everywhere doctor check. |
| S15 | `hosted/s15-desktop-import` | 6 | S03–S10 | `python -m coworker.import_desktop <state_dir> --workspace … --user …`. Idempotent and dry-run first. The only module allowed to import `sqlite3`. |
| S16 | `hosted/s16-hosting-prep` | 6 | S11, S13 | Dockerfile, `railway.json` with `api` and `worker`, `vercel.json`, env schema in `.env.example`, `/healthz`, frontend API base URL. |
| S17 | `hosted/s17-staging` | 7 | S16 | Check Vercel, Railway and Supabase CLI access. Create staging projects on default domains. Apply migrations. Set Google redirect URIs. |
| S18 | `hosted/s18-smoke` | 8 | all | Browser smoke test on staging from the combined `main`, per spec Acceptance 4. Fix-forward PRs as needed. |

## Waves at a glance

```
W0  S00
W1  S01  S02
W2  S03  S04  S05
W3  S06  S07  S08
W4  S09  S10  S11
W5  S12  S13  S14
W6  S15  S16
W7  S17
W8  S18
```

## Before S11

Find out whether every officer's Google account is in one Google Workspace
org. If so, make the OAuth app Internal and skip the 7-day token expiry. If
not, S12 has to tell officers when to reconnect.

## Before S17

Confirm CLI logins for `vercel`, `railway` and `supabase`. Staging uses
`*.vercel.app` and `*.up.railway.app`. Choose a custom domain before
production, which is outside this plan.
