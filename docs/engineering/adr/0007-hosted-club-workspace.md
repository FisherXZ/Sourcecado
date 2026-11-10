# Sourcecado moves to a hosted, shared club workspace

Status: Accepted — 2026-11-09

This is the "new product decision" that [AGENTS.md](../../../AGENTS.md) asks for
before Postgres, hosting or tenancy come back. It supersedes the local-first
runtime in [ADR 0002](0002-sourcecado-workspace-runtime.md) and the macOS
packaging in [ADR 0003](0003-macos-preview-artifact-packaging.md).

## Context

Sourcecado runs as one FastAPI process and a Tauri shell on one officer's
machine. State lives in five SQLite stores (`club.db`, `people.db`,
`agent_runs.db`, `drive_ingestion.db`, `meeting_evidence.db`), JSONL
transcript and event logs, a memory notes directory, a workspace receipt log
and a mode-0600 `secrets.json`. A person file can only be picked up by another
officer if they sit at the same laptop. The club wants every officer working
from the same person files in a browser.

## Decision

1. **Hosting.** The React/Vite frontend deploys to Vercel as a plain web app.
   The FastAPI backend deploys to Railway as two services built from one
   image: `api` (HTTP) and `worker` (agent runs). The database is Supabase
   Postgres. The Tauri shell is retired. Next.js stays out.
2. **Tenancy.** One shared club workspace per club. Every table that holds
   club data carries `workspace_id`, and every query filters on it. Each row
   that records an action also carries the acting `user_id` (`created_by`,
   `actor_user_id`). Per-user state (Google credentials, approval identity,
   connection health) is keyed by `user_id` inside the workspace.
3. **Auth.** Supabase Auth issues sessions. The backend verifies the Supabase
   JWT on every request and resolves a `Scope(workspace_id, user_id)` from a
   `workspace_members` table. Requests with no membership get 403.
4. **One store interface.** All data access goes through repository
   interfaces that take a `Scope`. No module outside `coworker/db/` opens a
   connection or writes SQL against another store's tables.
5. **Row-level security.** Every new table has RLS enabled in the migration
   that creates it. The backend connects as a role without `BYPASSRLS` and
   sets `app.workspace_id` and `app.user_id` with `SET LOCAL` in each
   transaction. Policies compare against those settings, so a missing filter
   in code returns no rows instead of another club's rows.
6. **Collation.** SQLite compares text byte-wise. Postgres uses the database
   locale. Every text column is declared `COLLATE "C"` in its migration so
   ordering and equality match the desktop behavior without per-query
   `COLLATE` clauses.
7. **Sending.** The explicit-approval rule is unchanged. Whoever approves a
   send sends it from their own connected Gmail. An approval can't be used by
   a different user.
8. **Google OAuth.** v1 runs the Google Cloud app unverified in Testing mode
   with an allow-list of officer accounts (limit 100). Verification for
   restricted Gmail scopes is deferred to its own track.
9. **Secrets.** Google refresh tokens and Apollo keys move to an encrypted
   `credentials` table (AES-GCM, app-level). The key is a Railway environment
   variable and never reaches Postgres or logs.
10. **Agent runs.** The `worker` service claims queued runs from Postgres with
    `SELECT … FOR UPDATE SKIP LOCKED` and a lease. A redeploy of `api` never
    interrupts a run, and an expired lease lets another worker resume from the
    last checkpoint.
11. **Desktop-only features are removed.** Workspace shell and local file
    tools, PDF isolation that depends on local processes, the update channel,
    macOS packaging, stdio MCP servers and user-installed skill folders.
    Remote (HTTP/OAuth) MCP and built-in skills stay.
12. **Cutover.** A one-time import script loads a director's local stores into
    the club workspace under their user. The last desktop commit is tagged
    `desktop-final`.

## Alternatives considered

*Per-officer private data* keeps tenancy simple but defeats the handoff goal
of the person file. *A single club mailbox* would let any officer approve a
send that goes out under one shared identity, which weakens the approval
record. *Running agent runs inside `api`* is one service fewer, but every
deploy would cut off in-flight runs. *Supabase Vault* for secrets ties the key
to the database we are protecting it from. *Reviving `archive/hosted-web/`*
would bring back Next.js and a data model that predates the person file.

## Consequences

- Tests run against Postgres. Existing assertions stay as they are; only
  fixtures change. Green tests mean the port kept behavior.
- In Testing mode Google expires refresh tokens for external users after 7
  days, so officers reconnect Gmail weekly. If every officer is in one Google
  Workspace org, setting the app to Internal removes both the expiry and the
  verification need. Check this before the auth slice.
- `doctor.py` and `migrations.py` lose their file-system store checks and gain
  Postgres checks, including one that fails if any table lacks RLS.
- Local-state policy in the README and the "never reintroduce hosting" rule in
  AGENTS.md are replaced by this ADR.
- See the [spec](../../superpowers/specs/2026-11-09-hosted-migration.md) and
  the [slice plan](../../superpowers/plans/2026-11-09-hosted-migration-slices.md).
