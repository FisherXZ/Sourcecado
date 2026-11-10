# Replace the local desktop app with a hosted web service

Status: Accepted — 2026-11-09

Supersedes [ADR 0002](0002-sourcecado-workspace-runtime.md) and
[ADR 0003](0003-macos-preview-artifact-packaging.md). Their code stays in the
tree until the migration slices remove it (S23 for the workspace runtime, S38
for packaging).

## Context

Sourcecado runs as a Tauri desktop app. A Python backend starts as a child
process on `127.0.0.1`. All data is in SQLite files and other files under
`~/.config/club`. There is one operator and no user model.

Two needs do not fit this shape:

1. **The agent must work while the laptop is closed.** Scheduled runs, the
   attention loop, and reply filing stop when the process stops.
2. **A director must not need an install.** The desktop app needs a macOS build,
   an update channel, and a repair tool.

`AGENTS.md` said: "Do not reintroduce the hosted app, Postgres, Next.js, or team
tenancy without a new product decision." This record is that decision.

## Decision

Sourcecado becomes a hosted web service.

| Part | Choice |
| --- | --- |
| Frontend | The existing React/Vite build, served as static files by Vercel |
| Backend | The existing FastAPI app, as one Railway service with one replica |
| Database | One Supabase Postgres database, raw SQL through `psycopg` |
| Login | Supabase Auth with Google sign-in and an email allowlist |
| Files | Supabase Storage |
| Users | Invite-only, a cap of 10, each user isolated |

The rules that go with this choice:

- **Port, not rewrite.** The agent loop, approvals, ledger, and connectors stay
  in Python. The archived Next.js app in `archive/hosted-web/` is not revived.
- **Each table has `user_id`.** The application filters by it, and Postgres
  row-level security is a second barrier. A two-user test is a CI gate.
- **No team workspaces.** Each user sees only their own data. Shared person
  files and roles are not part of this decision.
- **The desktop app stops at the `desktop-final` tag.** There is no second
  runtime and no flag that keeps SQLite.
- **Local machine tools are removed.** The workspace file tools, the shell
  tools, and the folder picker have no equivalent on a server.
- **Approval before send does not change.** There is no auto-send.

The full list of 27 decisions, each with the option that was rejected, is in the
[migration spec](../../superpowers/specs/2026-11-09-hosted-migration-spec.md).
The work is cut into slices in the
[slices document](../../superpowers/plans/2026-11-09-hosted-migration-slices.md).

## Consequences

- From the first database slice (S01), `main` is not a working desktop product.
  Desktop users and the course cohort use the `desktop-final` tag.
- The operator can read the stored mail data of each user. A data notice, a
  delete-account action, and operator-only production access are launch gates.
- The operator pays for model, Apollo, and search calls. A monthly spend cap for
  each user is a launch gate.
- Google shows an "unverified app" warning, and the Google project has a limit
  of 100 users for its lifetime. Growth past that limit needs Google's
  restricted-scope verification, which this decision does not include.
- One replica is a single point of failure. A deploy interrupts active runs, and
  the user retries.
- Doctor, local backups, the update channel, the diagnostic bundle, and macOS
  packaging are deleted during the migration.

## Open at the time of this record

- The domain name is not chosen.
- Five platform facts are not verified. The spec's risk table lists each one
  with its check.
