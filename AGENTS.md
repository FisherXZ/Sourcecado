# Sourcecado agent context

## Product direction

Sourcecado is an assistant for a Codeology sourcing director. It does the complete job: name a target, find people, prepare tailored outreach, deliberately enrich when needed, approve and send, keep conversations moving, and leave a person file another officer can pick up.

Sourcecado is moving from a local desktop app to a hosted web service: React/Vite on Vercel, the FastAPI backend on Railway, and Supabase for Postgres, login, and files. [ADR 0007](docs/engineering/adr/0007-hosted-web-service.md) records the decision. The work is in progress, so parts of the code are still local-only. The [slices document](docs/superpowers/plans/2026-11-09-hosted-migration-slices.md) shows which slices are done. The desktop app stops at the `desktop-final` tag.

Chat is home. Contacts is the assistant's operating picture for saved people and their sequences. The person file is the durable domain object.

The active runtime is `backend/` (Python) and `frontend/` (React; the Tauri shell stays until slice S38 removes it). The previous hosted Next.js/Postgres application is preserved in `archive/hosted-web/`. It is not the current implementation and is not the base for the hosted service.

## Current guardrails

- Build the hosted Sourcecado service: Python/FastAPI backend plus React/Vite UI. Follow the migration spec and work one slice at a time.
- Do not add desktop-only features: Tauri, packaging, the update channel, doctor, or tools that read the user's disk or run a shell.
- Each user sees only their own data. Every new table has `user_id`, and every new query filters by it.
- Optimize for one sourcing director per account while keeping person files intelligible to a later officer.
- A sequence is a person being worked through Open, In conversation, and Done. A company is context, not a deal.
- A kept person remains durable before outreach. The Contacts people table shows every live saved person: Open, In conversation, Done, or Not started (kept without a sequence). Its Tasks view includes the same people. Saving a task leaves sequence membership unchanged.
- Apollo search may return candidates without an email. Enrichment is manual and credit-aware.
- Sending is allowed only after explicit review and approval in Sourcecado. Never add auto-send.
- Keep Gmail, Drive, Calendar, Granola, Apollo, and web work attached to the relevant person and run ledger.
- Store secrets and runtime state outside the repository. Never log tokens, API keys, or raw authorization headers.
- Preserve tool calls, artifacts, source references, permission decisions, rationale summaries, and failures in the run ledger.
- Do not revive the archived Next.js app, and do not add team workspaces, shared person files, roles, open signup, or billing without a new product decision.
- Do not make OpenClaw, Hermes, or MCP the core product dependency. Borrow useful patterns behind Sourcecado-owned boundaries.

## Repository map

- `backend/coworker/` — backend, connectors, agent loop, policy, and persistence
- `frontend/` — active React/Vite UI; `src-tauri/` is the desktop shell, kept until slice S38
- `backend/tests/` and `frontend/tests/` — active verification suites
- `docs/` — current and historical product/engineering records
- `docs/course/` — Sourcecado Course language and plan. Product language in `docs/domain.md` takes precedence over course terminology.
- `archive/hosted-web/` — read-only historical implementation

## Documentation precedence

1. `docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md` — current product source of truth for the sourcing job. Where it says local, desktop, or one machine, the hosted migration spec takes precedence.
2. `docs/superpowers/specs/2026-11-09-hosted-migration-spec.md` and ADR 0007 — source of truth for where and how the service runs
3. `README.md`, this file, `docs/domain.md`, `docs/design.md`, and `CONTRIBUTING.md` — current operating guidance
4. Current dated implementation plans and ADRs listed in `docs/README.md`
5. Older dated specs and plans — historical context only

If documents conflict, follow the highest item in this list and update the stale living document in the same change.

## Read for the task

- For setup, testing or a first contribution, read [CONTRIBUTING.md](CONTRIBUTING.md).
- For product concepts, read [docs/domain.md](docs/domain.md).
- Before visual changes, read [docs/design.md](docs/design.md). Preserve the Warm Operator direction unless the user approves a change.
- Before changing approvals, persistence or recovery, read the relevant [engineering guide](docs/engineering/README.md).
- Before migration work, read the [slices document](docs/superpowers/plans/2026-11-09-hosted-migration-slices.md) and the slice's tasks in the [migration plan](docs/superpowers/plans/2026-11-09-hosted-migration-plan.md).
- For active work and deliberate deferrals, read [docs/BACKLOG.md](docs/BACKLOG.md).
